"""下载完成后动作的测试（ROADMAP 第 8 项）。

第 8 项的由来：批量/播放列表/队列下载往往是「挂上就走」，下完之后还要自己回来打开
文件夹，或者让机器空转一整夜。这个文件锁住四件事：

1. **只在真正空闲时触发一次**：播放列表/队列还在跑时不动作，否则每个条目都会弹窗口。
2. **设置里存的是 ASCII 代号**，不是界面文案（文案会随语言变）。
3. **睡眠而不是关机**：关机不可逆，误触发会丢数据。
4. **任何失败都不影响应用**：打开文件夹失败、平台不支持睡眠，都只记日志。
"""

import unittest
from unittest.mock import patch

from app import gui, i18n, postaction, settings as settings_mod

_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create
_APP = None

_PATH = "/tmp/after_download_test/pic_001.jpg"


def _shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


class PostActionModuleTests(unittest.TestCase):
    def test_normalize_accepts_only_known_codes(self):
        for code in postaction.OPTIONS:
            self.assertEqual(postaction.normalize(code), code)
        self.assertEqual(postaction.normalize(" QUIT "), "quit")
        for bad in ("", None, "关机", "openfolder", 7):
            self.assertEqual(postaction.normalize(bad), "none", bad)

    def test_disruptive_actions(self):
        self.assertTrue(postaction.is_disruptive("quit"))
        self.assertTrue(postaction.is_disruptive("sleep"))
        for code in ("none", "open_folder", "open_file", "垃圾"):
            self.assertFalse(postaction.is_disruptive(code), code)

    def test_sleep_command_per_platform(self):
        self.assertEqual(postaction.sleep_command("darwin")[:1], ["pmset"])
        self.assertIn("SetSuspendState", " ".join(postaction.sleep_command("win32")))
        self.assertEqual(postaction.sleep_command("linux"), ["systemctl", "suspend"])
        self.assertEqual(postaction.sleep_command("linux2"), ["systemctl", "suspend"])
        self.assertIsNone(postaction.sleep_command("freebsd"))

    def test_run_sleep_uses_the_platform_command(self):
        started = []
        with patch.object(postaction.subprocess, "Popen",
                          lambda cmd, **kw: started.append(cmd)):
            with patch.object(postaction.sys, "platform", "darwin"):
                self.assertTrue(postaction.run_sleep())
        self.assertEqual(started, [["pmset", "sleepnow"]])

    def test_run_sleep_on_unsupported_platform_is_silent(self):
        started = []
        with patch.object(postaction.subprocess, "Popen",
                          lambda cmd, **kw: started.append(cmd)):
            with patch.object(postaction.sys, "platform", "freebsd"):
                self.assertFalse(postaction.run_sleep())
        self.assertEqual(started, [])

    def test_labels_round_trip(self):
        i18n.set_language("zh_CN")
        for code in postaction.OPTIONS:
            label = postaction.label(code)
            self.assertTrue(label)
            self.assertEqual(postaction.value_for_label(label), code, code)
        self.assertEqual(postaction.value_for_label("不存在的文案"), "none")
        self.assertEqual(postaction.label("垃圾"), postaction.label("none"))


class _AfterDownloadCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.events = []
        self.opened = []
        self.slept = []
        self.quit_calls = []
        self._patches = [
            patch.object(gui.applog, "log_event",
                         lambda name, **fields: self.events.append((name, fields))),
            patch.object(gui, "open_path", lambda path: self.opened.append(path)),
            patch.object(gui.postaction, "run_sleep",
                         lambda: (self.slept.append(1), True)[1]),
            # 参数不能叫 self：那会指向 App 而不是本用例，往 App 上挂个空列表后
            # 断言永远看到空（第一版就是这么写的，用例失败而不是通过）
            patch.object(gui.App, "quit_from_menubar",
                         lambda app_self: self.quit_calls.append(1)),
        ]
        for item in self._patches:
            item.start()
        self.app.settings["after_download"] = "none"
        self.app.settings["download_queue"] = []
        self.app._after_action_path = ""
        self.app.settings["download_history"] = [
            {"path": _PATH, "title": "t", "platform": "weibo", "time": 1.0}]
        for panel in self.app._all_panels():
            panel._busy = False

    def tearDown(self):
        for item in self._patches:
            item.stop()
        self.app.settings["after_download"] = "none"
        self.app.settings["download_queue"] = []
        self.app.settings["download_history"] = []
        self.app._after_action_path = ""
        for panel in self.app._all_panels():
            panel._busy = False
        settings_mod.save = _ORIGINAL_SAVE

    def _names(self):
        return [name for name, _fields in self.events]

    def _run(self):
        self.app._maybe_run_after_download()


class IdleTests(_AfterDownloadCase):
    def test_idle_when_nothing_busy_and_queue_empty(self):
        self.assertTrue(self.app.is_idle())

    def test_not_idle_while_a_panel_downloads(self):
        panel = self.app._all_panels()[0]
        panel._busy = True
        try:
            self.assertFalse(self.app.is_idle())
        finally:
            panel._busy = False

    def test_not_idle_while_the_queue_has_jobs(self):
        self.app.settings["download_queue"] = [{"url": "https://e/1",
                                                "platform": "youtube"}]
        self.assertFalse(self.app.is_idle())
        self.app.settings["download_queue"] = []
        self.assertTrue(self.app.is_idle())


class AfterDownloadActionTests(_AfterDownloadCase):
    def test_none_does_nothing(self):
        self._run()
        self.assertEqual(self.opened, [])
        self.assertEqual(self.slept, [])
        self.assertEqual(self.quit_calls, [])
        self.assertEqual(self._names(), [])

    def test_open_folder_opens_the_directory(self):
        self.app.settings["after_download"] = "open_folder"
        self._run()
        self.assertEqual(self.opened, ["/tmp/after_download_test"])
        self.assertIn("after_download", self._names())

    def test_open_file_opens_the_file(self):
        self.app.settings["after_download"] = "open_file"
        self._run()
        self.assertEqual(self.opened, [_PATH])

    def test_quit_leaves_the_app(self):
        self.app.settings["after_download"] = "quit"
        self._run()
        self.assertEqual(self.quit_calls, [1])
        self.assertEqual(self.opened, [])

    def test_sleep_is_used_instead_of_shutdown(self):
        self.app.settings["after_download"] = "sleep"
        self._run()
        self.assertEqual(self.slept, [1])
        self.assertIn("after_download", self._names())

    def test_unsupported_sleep_is_skipped_silently(self):
        self.app.settings["after_download"] = "sleep"
        with patch.object(gui.postaction, "run_sleep", lambda: False):
            self._run()
        self.assertNotIn("after_download", self._names())

    def test_garbage_setting_is_treated_as_none(self):
        self.app.settings["after_download"] = "关机"
        self._run()
        self.assertEqual(self.opened, [])
        self.assertEqual(self.quit_calls, [])

    def test_each_path_triggers_only_once(self):
        self.app.settings["after_download"] = "open_folder"
        self._run()
        self._run()
        self.assertEqual(self.opened, ["/tmp/after_download_test"], "不该重复开窗口")

    def test_a_new_download_triggers_again(self):
        self.app.settings["after_download"] = "open_file"
        self._run()
        self.app.settings["download_history"] = [
            {"path": "/tmp/after_download_test/pic_002.jpg", "title": "t",
             "platform": "weibo", "time": 2.0}]
        self._run()
        self.assertEqual(len(self.opened), 2)

    def test_no_history_does_nothing(self):
        self.app.settings["download_history"] = []
        self.app.settings["after_download"] = "open_folder"
        self._run()
        self.assertEqual(self.opened, [])

    def test_no_action_while_a_panel_is_busy(self):
        panel = self.app._all_panels()[0]
        panel._busy = True
        try:
            self.app.settings["after_download"] = "open_folder"
            self._run()
        finally:
            panel._busy = False
        self.assertEqual(self.opened, [], "播放列表还没下完就不该开窗口")

    def test_no_action_while_the_queue_has_jobs(self):
        self.app.settings["after_download"] = "quit"
        self.app.settings["download_queue"] = [{"url": "https://e/1",
                                                "platform": "youtube"}]
        self._run()
        self.assertEqual(self.quit_calls, [])

    def test_failure_is_logged_and_swallowed(self):
        def boom(path):
            raise OSError("no such folder")

        self.app.settings["after_download"] = "open_folder"
        with patch.object(gui, "open_path", boom):
            self._run()                       # 不应抛异常
        failed = [fields for name, fields in self.events
                  if name == "after_download_failed"]
        self.assertTrue(failed, "失败必须留痕")
        self.assertIn("no such folder", failed[0].get("error", ""))
        self.assertNotIn("after_download", self._names())


class AfterDownloadSettingTests(_AfterDownloadCase):
    def setUp(self):
        super().setUp()
        self.app.show_page("settings")
        self.app.update_idletasks()
        self.page = self.app.pages["settings"]

    def test_select_lists_every_option(self):
        values = list(self.page.after_download_select._values)
        self.assertEqual(values,
                         [postaction.label(code) for code in postaction.OPTIONS])
        self.assertEqual(len(values), 5)

    def test_change_stores_the_ascii_code(self):
        self.saved = []
        settings_mod.save = lambda data: self.saved.append(dict(data))
        self.page.after_download_var.set(postaction.label("quit"))
        self.app.update_idletasks()
        self.assertEqual(self.app.settings["after_download"], "quit")
        self.assertEqual(self.saved[-1]["after_download"], "quit",
                         "设置里要存 ASCII 代号，不能存会随语言变的文案")

    def test_unknown_label_falls_back_to_none(self):
        self.page.after_download_var.set("乱写的文案")
        self.app.update_idletasks()
        self.assertEqual(self.app.settings["after_download"], "none")


if __name__ == "__main__":
    unittest.main()
