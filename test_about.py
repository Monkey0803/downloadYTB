"""关于对话框的测试（ROADMAP 第 11 项）。

第 11 项的由来：诊断报告早就有了，但入口只有一个「复制诊断信息」按钮；用户想知道自己
用的是哪一版、依赖是否正常、日志在哪，界面里没有任何地方能看。

这个文件锁住四件事：

1. **展示的就是诊断报告本身**：不另抄一份依赖列表（抄一份迟早会漂）。
2. **只保留一个实例**：反复打开不会堆出一串窗口。
3. **异步收集**：报告要探浏览器 Cookie（可能秒级），不能卡住界面；失败也不能崩。
4. **两个入口都能到**：设置页的「关于」按钮 / 菜单栏的版本项。
"""

import unittest
from unittest import mock

from app import about, appupdate, diagnostics, gui, i18n, settings as settings_mod

_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create
_APP = None


def _shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


class _SyncThread:
    """把后台线程变成同步执行，测试里就能确定性地等结果。"""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target:
            self._target()


class AboutDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["language"] = "zh_CN"
        self.app._about_dialog = None
        self.app._release_url = ""

    def tearDown(self):
        dialog = getattr(self.app, "_about_dialog", None)
        if dialog is not None:
            try:
                dialog.destroy()
            except Exception:
                pass
        self.app._about_dialog = None
        settings_mod.save = _ORIGINAL_SAVE

    def _open(self, probe=None):
        with mock.patch.object(about.threading, "Thread", _SyncThread):
            dialog = about.show(self.app, probe=probe)
        self.app.update_idletasks()
        self.app.update()
        return dialog

    def test_shows_the_report(self):
        dialog = self._open()
        self.assertEqual(dialog.title(), i18n.tr("about_title"))
        # 报告正文里有应用版本与日志路径（展示的就是诊断报告本身）
        from app import __version__

        self.assertIn(__version__, dialog.body_label.cget("text"))
        self.assertIn("videodownloader.log", dialog.body_label.cget("text"))
        self.assertEqual(dialog.report, dialog.body_label.cget("text"))
        dialog.destroy()

    def test_reuses_a_single_instance(self):
        first = self._open()
        second = self._open()
        self.assertIs(first, second, "不该堆出第二个窗口")
        first.destroy()
        third = self._open()
        self.assertIsNot(third, first, "关掉之后可以再开一个")
        third.destroy()

    def test_copy_puts_the_report_on_the_clipboard(self):
        dialog = self._open()
        copied = []
        dialog.clipboard_clear = lambda: copied.append("clear")
        dialog.clipboard_append = lambda text: copied.append(text)
        dialog._copy()
        self.assertIn("clear", copied)
        self.assertIn(dialog.report, copied)
        self.assertEqual(dialog.hint_label.cget("text"), i18n.tr("copied"))
        dialog.destroy()

    def test_copy_without_a_report_does_nothing(self):
        dialog = self._open()
        dialog.report = ""
        copied = []
        dialog.clipboard_clear = lambda: copied.append("clear")
        dialog._copy()
        self.assertEqual(copied, [])
        dialog.destroy()

    def test_buttons_open_the_log_and_the_release_page(self):
        dialog = self._open()
        opened = []
        with mock.patch.object(about, "_open_with_system", opened.append):
            dialog._open_log()
            dialog._open_release()
            self.app._release_url = "https://e/tag"
            dialog._open_release()
        self.assertIn("videodownloader.log", opened[0])
        self.assertEqual(opened[1], appupdate.RELEASES_PAGE, "没新版时是发布页首页")
        self.assertEqual(opened[2], "https://e/tag")
        dialog.destroy()

    def test_report_failure_is_shown_not_raised(self):
        with mock.patch.object(diagnostics, "build_report",
                               side_effect=OSError("钥匙串不可读")):
            dialog = self._open()
        self.assertIn("钥匙串不可读", dialog.body_label.cget("text"))
        dialog.destroy()

    def test_late_callback_after_close_is_ignored(self):
        dialog = self._open()
        before = dialog.report
        self.assertTrue(before, "先要有正常填充的报告")
        dialog.destroy()
        dialog._fill("迟到的报告", "")          # 不应抛 TclError
        self.assertEqual(dialog.report, before, "关窗后的迟到结果不能覆盖")


class AboutEntryPointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["update_available"] = ""
        self.app._release_url = ""

    def tearDown(self):
        self.app.settings["update_available"] = ""
        settings_mod.save = _ORIGINAL_SAVE

    def test_settings_card_has_an_about_button(self):
        page = self.app.pages["settings"]
        self.assertTrue(hasattr(page, "about_btn"))
        self.assertEqual(page.about_btn._text, i18n.tr("about_button"))

    def test_about_button_opens_the_dialog_with_the_cached_probe(self):
        page = self.app.pages["settings"]
        page._cookie_probe = "cached-probe"
        calls = []
        with mock.patch.object(gui.about, "show",
                               lambda app, probe=None: calls.append(probe)):
            page._open_about()
        self.assertEqual(calls, ["cached-probe"], "复用设置页已经探好的结果")

    def test_menubar_version_action_opens_about_without_an_update(self):
        shown = []
        opened = []
        with mock.patch.object(gui.about, "show",
                               lambda app, probe=None: shown.append(app)), \
             mock.patch.object(gui, "open_path", opened.append):
            self.app._handle_menubar_action("version")
        self.assertEqual(len(shown), 1)
        self.assertEqual(opened, [])

    def test_menubar_version_action_opens_the_release_page_with_an_update(self):
        self.app.settings["update_available"] = "9.9.9"
        self.app._release_url = "https://e/tag"
        shown = []
        opened = []
        with mock.patch.object(gui.about, "show",
                               lambda app, probe=None: shown.append(app)), \
             mock.patch.object(gui, "open_path", opened.append):
            self.app._handle_menubar_action("version")
        self.assertEqual(shown, [])
        self.assertEqual(opened, ["https://e/tag"])


if __name__ == "__main__":
    unittest.main()
