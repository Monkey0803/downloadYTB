"""菜单栏快捷操作（#10）的测试。

分两部分：
1. `MenuBarControllerTests` 用**真实 AppKit** 建一个 NSStatusItem，检查菜单项、
   动作派发、状态刷新与语言切换。这样能真正验证 pyobjc 调用（旧实现用「第 N 项」
   下标更新文案，加一项就会错位）。测试会在菜单栏短暂出现一个图标，收尾时移除。
2. `MenubarActionDispatchTests` 用 Tk 验证 GUI 侧取走 pending 后真的执行了对应动作。

**顺序很重要**：两个类都必须先建 Tk（即先让 Tk 建出 NSApplication），再创建
NSStatusItem。反过来先让 pyobjc 建 AppKit、后初始化 Tk，Tk 启动会直接崩：
`-[NSApplication macOSVersion]: unrecognized selector`（实测 SIGABRT）。
真实应用也是这个顺序——Tk 的 mainloop 先跑起来，菜单栏才创建。
"""

import unittest

from app import appupdate, gui, i18n, menubar, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

_APP = None


class _FakeApp:
    """只提供 menubar 需要读的纯 Python 状态。"""

    def __init__(self, status="空闲", history=None):
        self._status = status
        self._history = history or []

    def menubar_status_text(self):
        return self._status

    def download_history(self):
        return list(self._history)


class _BrokenApp:
    def menubar_status_text(self):
        raise RuntimeError("boom")

    def download_history(self):
        raise RuntimeError("boom")


class MenuBarControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 先建 Tk 应用，保证 NSApplication 由 Tk 建好（见模块 docstring）
        _shared_app()

    @unittest.skipUnless(menubar._AVAILABLE, "需要 macOS 与 pyobjc")
    def setUp(self):
        self.app = _FakeApp()
        self.controller = menubar.MenuBarController(self.app, None, None)
        self.delegate = self.controller._delegate
        self._language = i18n.language()

    def tearDown(self):
        i18n.set_language(self._language)
        self.controller.remove()

    def _titles(self):
        menu = self.controller._item.menu()
        return [str(menu.itemAtIndex_(i).title()) for i in range(menu.numberOfItems())]

    def test_menu_contains_the_quick_actions(self):
        titles = self._titles()
        for key in ("menubar_paste", "menubar_last_folder", "history", "settings",
                    "show_window", "quit"):
            self.assertIn(i18n.tr(key), titles, f"菜单缺少 {key}")

    def test_status_item_is_read_only(self):
        self.assertFalse(bool(self.controller._status_mi.isEnabled()))

    def test_actions_set_the_expected_pending_value(self):
        cases = (("clipboardLink_", "clipboard"), ("openLastFolder_", "last_folder"),
                 ("showHistory_", "history"), ("showSettings_", "settings"),
                 ("showWindow_", "show"), ("quitApp_", "quit"),
                 ("openRelease_", "version"))
        for method, expected in cases:
            self.controller.pending = None
            getattr(self.delegate, method)(None)
            self.assertEqual(self.controller.pending, expected, method)

    def test_take_pending_clears(self):
        self.delegate.showHistory_(None)
        self.assertEqual(self.controller.take_pending(), "history")
        self.assertIsNone(self.controller.take_pending())

    def test_refresh_status_shows_app_text(self):
        self.app._status = "下载中 42%"
        self.controller.refresh_status()
        self.assertEqual(str(self.controller._status_mi.title()), "下载中 42%")

    def test_refresh_status_survives_a_broken_app(self):
        broken = menubar.MenuBarController(_BrokenApp(), None, None)

        def boom():
            raise RuntimeError("boom")

        try:
            broken.refresh_status()          # 不应抛错
        finally:
            broken.remove()

    def test_last_folder_enabled_only_with_history(self):
        self.app._history = []
        self.controller.refresh_status()
        item = self.controller._items["last_folder"][0]
        self.assertFalse(bool(item.isEnabled()), "没有历史时应置灰")

        self.app._history = [{"path": "/tmp/a.mp4"}]
        self.controller.refresh_status()
        self.assertTrue(bool(item.isEnabled()))

    def test_language_switch_updates_every_item(self):
        i18n.set_language("zh_CN")
        self.controller.update_language()
        chinese = self._titles()
        i18n.set_language("en")
        self.controller.update_language()
        english = self._titles()
        self.assertNotEqual(chinese, english)
        self.assertIn(i18n.tr("menubar_paste"), english)
        self.assertNotIn(i18n.tr("menubar_paste"), chinese)
        # 分隔符与只读状态项之外，每一项都要跟着切
        for key in ("menubar_last_folder", "history", "settings", "show_window", "quit"):
            self.assertIn(i18n.tr(key), english)

    def test_items_are_indexed_by_action_not_position(self):
        """加一项不该影响其它项的更新（旧实现按下标取，正是这个问题）。"""
        self.assertIn("clipboard", self.controller._items)
        self.assertIn("quit", self.controller._items)
        item, key = self.controller._items["settings"]
        self.assertTrue(key)
        self.assertEqual(str(item.title()), i18n.tr(key))

    def test_version_item_shows_the_version_and_is_read_only(self):
        """平时只是个只读版本号，用户不用打开设置就知道自己用的是哪版。"""
        item = self.controller._items["version"][0]
        self.assertIn(appupdate.current_version(), str(item.title()))
        self.assertFalse(bool(item.isEnabled()))

    def test_update_hint_turns_the_version_item_clickable(self):
        self.controller.update_update_hint("9.9.9")
        item = self.controller._items["version"][0]
        self.assertIn("9.9.9", str(item.title()))
        self.assertTrue(bool(item.isEnabled()), "发现新版本后应当可点")
        self.controller.update_update_hint("")
        self.assertIn(appupdate.current_version(), str(item.title()))
        self.assertFalse(bool(item.isEnabled()), "清空后回到只读")

    def test_version_item_follows_the_language(self):
        i18n.set_language("zh_CN")
        self.controller.update_language()
        chinese = str(self.controller._items["version"][0].title())
        i18n.set_language("en")
        self.controller.update_language()
        english = str(self.controller._items["version"][0].title())
        self.assertNotEqual(chinese, english)
        self.assertIn(appupdate.current_version(), english)
        i18n.set_language("zh_CN")
        self.controller.update_language()

    def test_remove_is_safe_to_call_twice(self):
        self.controller.remove()
        self.controller.remove()          # 不应抛错


def _shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


class _FakeMenubar:
    def __init__(self, pending):
        self.pending = pending

    def take_pending(self):
        req, self.pending = self.pending, None
        return req


class MenubarActionDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.scheduled = []
        self._after = self.app.after
        self.app.after = lambda ms, fn=None, *a: (self.scheduled.append(fn), "id")[1]
        self._menubar = self.app.menubar
        self.shown = []
        self._show_main = self.app.show_main_window
        self.app.show_main_window = lambda: self.shown.append("main")

    def tearDown(self):
        self.app.after = self._after
        self.app.menubar = self._menubar
        self.app.show_main_window = self._show_main
        settings_mod.save = _ORIGINAL_SAVE

    def _dispatch(self, pending):
        self.app.menubar = _FakeMenubar(pending)
        self.app._poll_menubar()

    def test_show_and_quit_still_work(self):
        self._dispatch("show")
        self.assertEqual(self.shown, ["main"])

    def test_history_action_switches_page(self):
        self.app.current_page = "youtube"
        self._dispatch("history")
        self.assertEqual(self.app.current_page, "history")
        self.assertEqual(self.shown, ["main"], "应先恢复窗口再切页")

    def test_settings_action_switches_page(self):
        self.app.current_page = "youtube"
        self._dispatch("settings")
        self.assertEqual(self.app.current_page, "settings")

    def test_unknown_pending_is_ignored(self):
        self.app.current_page = "youtube"
        self._dispatch("没有这个动作")
        self.assertEqual(self.app.current_page, "youtube")
        self.assertEqual(self.shown, [])

    def test_poll_reschedules_itself(self):
        self._dispatch(None)
        self.assertTrue(self.scheduled, "轮询必须续期，否则菜单动作会失效")

    def test_paste_from_menubar_forces_probe(self):
        """显式动作要跳过「不处理」设置与去重记录。"""
        self.app.settings["clipboard_action"] = "off"
        self.app._last_clipboard = "同一段文本"
        self.app.clipboard_get = lambda: "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
        calls = []
        self.app.pages["youtube"].handle_url = lambda url: calls.append(url)
        try:
            self.app.paste_from_menubar()
        finally:
            del self.app.pages["youtube"].__dict__["handle_url"]
        self.assertEqual(calls, ["https://www.youtube.com/watch?v=aqz-KE-bpKQ"])
        self.assertEqual(self.shown, ["main"])

    def test_paste_without_a_link_reports_it(self):
        self.app.clipboard_get = lambda: "这段文字里没有链接"
        statuses = []
        panel = self.app.pages["youtube"].video_panel
        self.app.current_page = "youtube"
        original = panel._set_status
        panel._set_status = lambda text, error=False: statuses.append((text, error))
        try:
            self.app.paste_from_menubar()
        finally:
            panel._set_status = original
        self.assertTrue(statuses, "应给出提示")
        self.assertIn(i18n.tr("clipboard_no_link"), statuses[0][0])

    def test_open_last_download_folder_uses_newest_entry(self):
        opened = []
        original = gui.open_in_file_manager
        gui.open_in_file_manager = lambda path: opened.append(path)
        try:
            self.app.settings["download_history"] = [
                {"path": "/tmp/最新.mp4"}, {"path": "/tmp/旧.mp4"}]
            self.app.open_last_download_folder()
        finally:
            gui.open_in_file_manager = original
        self.assertEqual(opened, ["/tmp/最新.mp4"])

    def test_open_last_download_folder_without_history_is_safe(self):
        opened = []
        original = gui.open_in_file_manager
        gui.open_in_file_manager = lambda path: opened.append(path)
        try:
            self.app.settings["download_history"] = []
            self.app.open_last_download_folder()
        finally:
            gui.open_in_file_manager = original
        self.assertEqual(opened, [])


if __name__ == "__main__":
    unittest.main()
