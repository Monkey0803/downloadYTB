"""KDE JobView 后端与协议选择的测试（#1）。

同样如实说明：本机没有会话总线、也没有 dbus-daemon，**无法真的跑一次 gdbus**。
这里用假 runner 覆盖的是：协议选择、命令参数构造、返回值解析、节流、视图复用与
终止、失败处理。真正的 D-Bus 往返没有被验证过。
"""

import os
import sys
import unittest
from unittest.mock import Mock

from app import taskbar_linux as L


class _Runner:
    """记录每次 gdbus 调用；按需给出返回值或抛错。"""

    def __init__(self, outputs=None, fail_on=None):
        self.calls = []
        self._outputs = outputs or {}
        self._fail_on = fail_on or set()

    def __call__(self, args, **kwargs):
        self.calls.append([str(a) for a in args])
        joined = " ".join(str(a) for a in args)
        for needle in self._fail_on:
            if needle in joined:
                raise OSError(f"gdbus 失败于 {needle}")
        for needle, out in self._outputs.items():
            if needle in joined:
                return Mock(stdout=out, stderr="")
        return Mock(stdout="", stderr="")

    def methods(self):
        """返回按调用顺序的 (dest, object-path, method, 参数...) 列表。"""
        out = []
        for call in self.calls:
            dest = call[call.index("--dest") + 1]
            path = call[call.index("--object-path") + 1]
            method = call[call.index("--method") + 1]
            args = call[call.index("--method") + 2:]
            out.append((dest, path, method, args))
        return out


GET_OWNER = "GetNameOwner"


def _kde_runner(**kw):
    outputs = {GET_OWNER: "(':1.42',)",
               "requestView": "(objectpath '/org/kde/JobView/Job_7',)"}
    outputs.update(kw.pop("outputs", {}))
    return _Runner(outputs=outputs, **kw)


class KdeDetectionTests(unittest.TestCase):
    def _with_env(self, value, **extra):
        original = {k: os.environ.get(k) for k in
                    ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP")}
        try:
            os.environ["XDG_CURRENT_DESKTOP"] = value
            for k, v in extra.items():
                os.environ[k] = v
            return L.is_kde()
        finally:
            for k, v in original.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_kde_and_plasma_are_detected(self):
        self.assertTrue(self._with_env("KDE"))
        self.assertTrue(self._with_env("plasma"))
        self.assertTrue(self._with_env("X-Cinnamon", XDG_SESSION_DESKTOP="plasma"))

    def test_other_desktops_are_not(self):
        for value in ("GNOME", "ubuntu:GNOME", "XFCE", ""):
            with self.subTest(value=value):
                self.assertFalse(self._with_env(value))

    def test_backend_follows_desktop_but_can_be_forced(self):
        original = os.environ.get("XDG_CURRENT_DESKTOP")
        try:
            os.environ["XDG_CURRENT_DESKTOP"] = "KDE"
            self.assertEqual(L.Backend(force=None).choice, "kde")
            os.environ["XDG_CURRENT_DESKTOP"] = "GNOME"
            self.assertEqual(L.Backend(force=None).choice, "unity")
            self.assertEqual(L.Backend(force="kde").choice, "kde")
            self.assertEqual(L.Backend(force="unity").choice, "unity")
        finally:
            if original is None:
                os.environ.pop("XDG_CURRENT_DESKTOP", None)
            else:
                os.environ["XDG_CURRENT_DESKTOP"] = original


class ParseTests(unittest.TestCase):
    def test_object_path_parsing_is_permissive(self):
        cases = [
            "(objectpath /org/kde/JobView/Job_1,)",
            "(objectpath '/org/kde/JobView/Job_1',)",
            '(objectpath "/org/kde/JobView/Job_1")',
            "('/org/kde/JobView/Job_1',)",       # 不带 objectpath 前缀
        ]
        for out in cases:
            with self.subTest(out=out):
                self.assertEqual(L._parse_view_path(out), "/org/kde/JobView/Job_1")

    def test_unparsable_output_returns_none(self):
        for out in ("", "()", "garbage"):
            with self.subTest(out=out):
                self.assertIsNone(L._parse_view_path(out))


class KdeJobViewTests(unittest.TestCase):
    def _backend(self, runner=None, clock=None):
        now = {"t": 100.0}
        runner = runner or _kde_runner()
        backend = L.KdeJobViewBackend(
            runner=runner, path="/usr/bin/gdbus",
            clock=clock or (lambda: now["t"]))
        return backend, runner, now

    def test_first_progress_requests_a_view_then_reports(self):
        backend, runner, _ = self._backend()
        backend.set_progress(42.5)
        methods = [m[2] for m in runner.methods()]
        self.assertEqual(methods, [
            "org.freedesktop.DBus.GetNameOwner",
            "org.kde.JobViewServer.requestView",
            "org.kde.JobViewV2.setInfoMessage",
            "org.kde.JobViewV2.setTotalAmount",
            "org.kde.JobViewV2.setPercent",
        ])
        # 进度调用必须打到「唯一名 + 返回的对象路径」上
        last = runner.methods()[-1]
        self.assertEqual(last[0], ":1.42")
        self.assertEqual(last[1], "/org/kde/JobView/Job_7")
        self.assertIn("<uint32 42>", last[3])

    def test_view_is_reused_across_updates(self):
        backend, runner, now = self._backend()
        backend.set_progress(10)
        count = len(runner.methods())
        now["t"] += L.MIN_INTERVAL + 0.1
        backend.set_progress(60)
        after = runner.methods()[count:]
        self.assertNotIn("org.kde.JobViewServer.requestView", [m[2] for m in after])
        self.assertNotIn("org.freedesktop.DBus.GetNameOwner", [m[2] for m in after])
        self.assertIn("org.kde.JobViewV2.setPercent", [m[2] for m in after])

    def test_throttled_updates_are_skipped(self):
        backend, runner, _ = self._backend()
        backend.set_progress(42.1)
        count = len(runner.methods())
        backend.set_progress(42.4)      # 同一整数百分比、时间未过
        self.assertEqual(len(runner.methods()), count)

    def test_done_sets_full_then_terminates(self):
        backend, runner, _ = self._backend()
        backend.set_progress(50)
        backend.set_done()
        methods = [m[2] for m in runner.methods()]
        self.assertIn("org.kde.JobViewV2.setPercent", methods)
        self.assertEqual(methods[-1], "org.kde.JobViewV2.terminate")
        self.assertIn("<uint32 100>", runner.methods()[-2][3])

    def test_state_is_reset_after_done(self):
        backend, runner, _ = self._backend()
        backend.set_progress(50)
        backend.set_done()
        self.assertIsNone(backend._view)
        self.assertIsNone(backend._owner)
        backend.set_progress(5)         # 需要重新申请视图
        self.assertIsNotNone(backend._view)

    def test_clear_before_any_progress_does_nothing(self):
        backend, runner, _ = self._backend()
        backend.clear()
        self.assertEqual(runner.methods(), [])

    def test_clear_terminates_existing_view(self):
        backend, runner, _ = self._backend()
        backend.set_progress(20)
        backend.clear()
        self.assertEqual(runner.methods()[-1][2], "org.kde.JobViewV2.terminate")

    def test_missing_owner_raises_and_can_retry(self):
        runner = _Runner(outputs={"requestView": "(objectpath /x,)"})
        backend, _, _ = self._backend(runner=runner)
        with self.assertRaises(OSError):
            backend.set_progress(10)
        # 失败后不应把缓存留成半成品，下一次仍能重新尝试
        self.assertIsNone(backend._view)

    def test_unparsable_request_view_raises(self):
        runner = _Runner(outputs={GET_OWNER: "(':1.9',)", "requestView": "()"})
        backend, _, _ = self._backend(runner=runner)
        with self.assertRaises(OSError):
            backend.set_progress(10)

    def test_terminate_swallows_failures(self):
        runner = _kde_runner(fail_on={"terminate"})
        backend, _, _ = self._backend(runner=runner)
        backend.set_progress(10)
        backend.clear()                 # 不应抛错
        self.assertIsNone(backend._view)

    def test_string_arguments_are_quoted(self):
        """gdbus 把参数当 GVariant 解析，字符串必须带引号（真实踩过）。"""
        backend, runner, _ = self._backend()
        backend.set_progress(10)
        owner_call = runner.methods()[0]
        self.assertEqual(owner_call[3], ["'org.kde.JobViewServer'"])
        request = runner.methods()[1]
        self.assertEqual(request[3][0], "'VideoDownloader'")
        self.assertEqual(request[3][-1], "<int32 0>")   # capabilities

    def test_dispatcher_swallows_kde_failures(self):
        from app import taskbar
        original = sys.platform
        failed = L.KdeJobViewBackend(runner=_Runner(fail_on={GET_OWNER}),
                                     path="/usr/bin/gdbus")
        try:
            taskbar.sys.platform = "linux"
            taskbar.reset_backend()
            taskbar._load_backend = lambda kind, window: failed
            with self.assertRaises(OSError):
                failed.set_progress(10)
            taskbar.set_progress(10)     # 调度层必须吞掉
            taskbar.set_done()
            taskbar.clear()
        finally:
            taskbar.sys.platform = original
            taskbar.reset_backend()


if __name__ == "__main__":
    unittest.main()
