"""任务栏 / Dock 进度：调度层与 Windows、Linux 后端。

**必须如实说明的边界**：Windows 与 Linux 后端是在 macOS 上开发的，无法在这里
真机运行。下面用假件覆盖的是「纯逻辑」部分——百分比换算、状态迁移、HWND 上溯、
节流、失败静默。真正调用 COM / D-Bus 的那几行只能靠代码审查，单测覆盖不到，
所以不能据此声称「已在 Windows / Linux 上验证通过」。
"""

import os
import sys
import unittest
from unittest.mock import Mock

from app import dock, taskbar, taskbar_linux, taskbar_win


# ---------------------------------------------------------------- Windows
class _FakeWinApi:
    def __init__(self, foreground=True, hwnd=1000):
        self.calls = []
        self._foreground = foreground
        self._hwnd = hwnd
        self.flashed = []

    def set_state(self, hwnd, state):
        self.calls.append(("state", hwnd, state))

    def set_value(self, hwnd, completed, total):
        self.calls.append(("value", hwnd, completed, total))

    def is_foreground(self, hwnd):
        return self._foreground

    def flash(self, hwnd):
        self.flashed.append(hwnd)

    def top_level_hwnd(self, hwnd):
        return self._hwnd


class _FakeWindow:
    def __init__(self, winfo_id=55):
        self._id = winfo_id

    def winfo_id(self):
        return self._id


class WindowsBackendTests(unittest.TestCase):
    def _backend(self, **kw):
        api = _FakeWinApi(**kw)
        return taskbar_win.Backend(api=api, window=_FakeWindow()), api

    def test_progress_is_converted_to_fraction(self):
        backend, api = self._backend()
        backend.set_progress(42.5)
        self.assertEqual(api.calls, [("state", 1000, taskbar_win.TBPF_NORMAL),
                                     ("value", 1000, 425, 1000)])

    def test_progress_is_clamped(self):
        backend, api = self._backend()
        backend.set_progress(150)
        self.assertEqual(api.calls[-1], ("value", 1000, 1000, 1000))
        backend.set_progress(-20)
        self.assertEqual(api.calls[-1], ("state", 1000, taskbar_win.TBPF_NOPROGRESS))

    def test_zero_progress_does_not_paint_an_empty_bar(self):
        backend, api = self._backend()
        backend.set_progress(0)
        self.assertEqual(api.calls, [("state", 1000, taskbar_win.TBPF_NOPROGRESS)])

    def test_same_permille_is_not_repeated(self):
        """进度钩子每秒调用几十次，同一千分位不必反复打扰任务栏。"""
        backend, api = self._backend()
        backend.set_progress(42.50)
        before = len(api.calls)
        backend.set_progress(42.54)   # 仍落在同一千分位
        self.assertEqual(len(api.calls), before)
        backend.set_progress(43.0)
        self.assertGreater(len(api.calls), before)

    def test_done_clears_progress_and_flashes_when_background(self):
        backend, api = self._backend(foreground=False)
        backend.set_progress(10)
        backend.set_done()
        self.assertEqual(api.calls[-1], ("state", 1000, taskbar_win.TBPF_NOPROGRESS))
        self.assertEqual(api.flashed, [1000])

    def test_done_does_not_flash_when_foreground(self):
        backend, api = self._backend(foreground=True)
        backend.set_done()
        self.assertEqual(api.flashed, [])

    def test_clear_resets_state(self):
        backend, api = self._backend()
        backend.set_progress(30)
        backend.clear()
        self.assertEqual(api.calls[-1], ("state", 1000, taskbar_win.TBPF_NOPROGRESS))

    def test_missing_window_id_is_swallowed_by_dispatcher(self):
        """拿不到窗口句柄时不能把下载搞崩。"""
        original = sys.platform
        try:
            taskbar.sys.platform = "win32"
            taskbar.reset_backend()
            taskbar._load_backend = lambda kind, window: taskbar_win.Backend(
                api=_FakeWinApi(), window=object())   # object() 没有 winfo_id
            with self.assertRaises(OSError):
                taskbar_win.Backend(api=_FakeWinApi(), window=object()).set_progress(50)
            # 调度层必须吞掉同一个错误
            taskbar.set_progress(50, window=object())
            taskbar.set_done(window=object())
            taskbar.clear(window=object())
        finally:
            taskbar.sys.platform = original
            taskbar.reset_backend()


class WinHwndWalkTests(unittest.TestCase):
    """_WinApi.top_level_hwnd 的上溯逻辑是纯计算，可以用假 user32 验证。"""

    class _Api(taskbar_win._WinApi):
        def __init__(self, styles, parents):   # 不碰真实 COM
            import ctypes
            self._ctypes = ctypes
            self._styles = styles
            self._parents = parents
            self._user32 = self

        def GetWindowLongW(self, hwnd, index):
            return self._styles.get(hwnd, 0)

        def GetParent(self, hwnd):
            return self._parents.get(hwnd, 0)

    def test_walks_up_while_window_is_a_child(self):
        api = self._Api(styles={10: taskbar_win.WS_CHILD,
                                20: taskbar_win.WS_CHILD,
                                30: 0},
                        parents={10: 20, 20: 30})
        self.assertEqual(api.top_level_hwnd(10), 30)

    def test_returns_self_when_not_a_child(self):
        api = self._Api(styles={10: 0}, parents={})
        self.assertEqual(api.top_level_hwnd(10), 10)

    def test_cycle_does_not_loop_forever(self):
        api = self._Api(styles={10: taskbar_win.WS_CHILD, 20: taskbar_win.WS_CHILD},
                        parents={10: 20, 20: 10})
        self.assertIn(api.top_level_hwnd(10), (10, 20))


# ---------------------------------------------------------------- Linux
class _FakeRunner:
    def __init__(self, fail=False):
        self.calls = []
        self._fail = fail

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if self._fail:
            raise OSError("gdbus 不可用")
        return Mock(stdout="", stderr="")


class LinuxBackendTests(unittest.TestCase):
    def _backend(self, clock=None, fail=False):
        runner = _FakeRunner(fail=fail)
        now = {"t": 100.0}
        backend = taskbar_linux.Backend(
            runner=runner, path="/usr/bin/gdbus",
            clock=(clock or (lambda: now["t"])))
        return backend, runner, now

    def test_progress_sends_launcher_entry_update(self):
        backend, runner, _ = self._backend()
        backend.set_progress(42.5)
        args = runner.calls[0]
        self.assertIn("com.canonical.Unity.LauncherEntry.Update", args)
        self.assertTrue(any("'progress': <0.4250>" in a for a in args))
        self.assertTrue(any("'progress-visible': <true>" in a for a in args))

    def test_repeats_are_throttled(self):
        backend, runner, now = self._backend()
        backend.set_progress(42.5)
        backend.set_progress(42.9)          # 同一整数百分比、时间未过
        self.assertEqual(len(runner.calls), 1)
        now["t"] += taskbar_linux.MIN_INTERVAL + 0.1
        backend.set_progress(42.9)
        self.assertEqual(len(runner.calls), 2)

    def test_done_marks_urgent(self):
        backend, runner, _ = self._backend()
        backend.set_progress(10)
        backend.set_done(attention=True)
        args = runner.calls[-1]
        self.assertTrue(any("'progress-visible': <false>" in a for a in args))
        self.assertTrue(any("'urgent': <true>" in a for a in args))

    def test_clear_is_skipped_when_never_shown(self):
        backend, runner, _ = self._backend()
        backend.clear()
        self.assertEqual(runner.calls, [])

    def test_runner_failure_is_swallowed_by_dispatcher(self):
        """gdbus 调用失败（例如桌面没有 D-Bus）时下载不能受影响。"""
        original = sys.platform
        failed = taskbar_linux.Backend(runner=_FakeRunner(fail=True),
                                       path="/usr/bin/gdbus")
        try:
            taskbar.sys.platform = "linux"
            taskbar.reset_backend()
            taskbar._load_backend = lambda kind, window: failed
            with self.assertRaises(OSError):
                failed.set_progress(11)      # 直接调用确实会抛
            taskbar.set_progress(10)         # 调度层必须吞掉
            taskbar.set_done()
            taskbar.clear()
        finally:
            taskbar.sys.platform = original
            taskbar.reset_backend()

    def test_desktop_id_prefers_gio_env(self):
        original = os.environ.get("GIO_LAUNCHED_DESKTOP_FILE")
        try:
            os.environ["GIO_LAUNCHED_DESKTOP_FILE"] = "/usr/share/applications/foo.desktop"
            self.assertEqual(taskbar_linux.desktop_id(), "foo")
            self.assertEqual(taskbar_linux.app_uri(), "application://foo.desktop")
            os.environ.pop("GIO_LAUNCHED_DESKTOP_FILE")
            self.assertEqual(taskbar_linux.desktop_id(), taskbar_linux.FALLBACK_DESKTOP_ID)
        finally:
            if original is None:
                os.environ.pop("GIO_LAUNCHED_DESKTOP_FILE", None)
            else:
                os.environ["GIO_LAUNCHED_DESKTOP_FILE"] = original


# ---------------------------------------------------------------- 调度层
class DispatcherTests(unittest.TestCase):
    def setUp(self):
        taskbar.reset_backend()

    def tearDown(self):
        taskbar.reset_backend()

    def test_kind_mapping(self):
        original = sys.platform
        try:
            taskbar.sys.platform = "darwin"
            self.assertEqual(taskbar._kind(), "dock")
            taskbar.sys.platform = "win32"
            self.assertEqual(taskbar._kind(), "win")
            taskbar.sys.platform = "linux"
            self.assertEqual(taskbar._kind(), "linux")
            taskbar.sys.platform = "freebsd"
            self.assertIsNone(taskbar._kind())
        finally:
            taskbar.sys.platform = original

    def test_unknown_platform_is_disabled_and_noop(self):
        original = sys.platform
        try:
            taskbar.sys.platform = "freebsd"
            self.assertFalse(taskbar.enabled_platform())
            taskbar.set_progress(50)
            taskbar.set_done()
            taskbar.clear()
        finally:
            taskbar.sys.platform = original

    def test_backend_errors_never_escape(self):
        class Boom:
            def set_progress(self, *a, **kw):
                raise RuntimeError("boom")

            def set_done(self, *a, **kw):
                raise RuntimeError("boom")

            def clear(self, *a, **kw):
                raise RuntimeError("boom")

        taskbar._backend = Boom()
        taskbar._backend_key = (taskbar._kind(), None)
        taskbar.set_progress(1)     # 全部不应抛错
        taskbar.set_done()
        taskbar.clear()

    def test_window_change_rebuilds_backend(self):
        original = sys.platform
        try:
            taskbar.sys.platform = "win32"
            taskbar.reset_backend()
            w1, w2 = _FakeWindow(1), _FakeWindow(2)
            api = _FakeWinApi()
            taskbar._load_backend = lambda kind, window: taskbar_win.Backend(
                api=api, window=window)
            first = taskbar._backend_for(w1)
            self.assertIs(taskbar._backend_for(w1), first)
            self.assertIsNot(taskbar._backend_for(w2), first)
        finally:
            taskbar.sys.platform = original

    def test_dock_backend_is_used_on_macos(self):
        calls = []
        original = dock.set_progress, dock.set_done, dock.clear
        dock.set_progress = lambda p: calls.append(("p", p))
        dock.set_done = lambda attention=True: calls.append(("d", attention))
        dock.clear = lambda: calls.append(("c", None))
        try:
            if sys.platform == "darwin":
                taskbar.reset_backend()
                taskbar.set_progress(12)
                taskbar.set_done()
                taskbar.clear()
                self.assertEqual(calls, [("p", 12), ("d", True), ("c", None)])
        finally:
            dock.set_progress, dock.set_done, dock.clear = original


if __name__ == "__main__":
    unittest.main()
