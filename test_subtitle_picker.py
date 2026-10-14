"""字幕语言选择器（#4）的测试。

原来设置页只能手写 ``zh-Hans,zh-Hant,en`` 这类 yt-dlp 语言代码；现在可以勾选
常用语言，输入框保留给列表之外的冷门代码。

整个文件共用一个 App：第一次 Tk 初始化要数秒，且销毁根窗口后再新建会让 macOS
上的 Tk 段错误（真实踩过）。
"""

import unittest

from app import gui, i18n, settings as settings_mod, settingspage

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

_APP = None


def shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


class LanguagePickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self._pickers = []

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        for picker in self._pickers:
            try:
                if picker.winfo_exists():
                    picker.destroy()
            except Exception:
                pass

    def _picker(self, current=""):
        picker = gui.SubtitleLanguagePicker(self.app, self.app, current)
        self._pickers.append(picker)
        return picker

    # ---------- 预选与取值 ----------

    def test_known_codes_are_pre_checked(self):
        picker = self._picker("en,ja")
        self.assertEqual(sorted(picker._collect()), ["en", "ja"])

    def test_unknown_codes_are_preserved(self):
        """列表之外的冷门代码不能被丢掉。"""
        picker = self._picker("en,xx-Custom")
        self.assertEqual(picker.extra, ["xx-Custom"])
        self.assertIn("xx-Custom", picker._collect())
        self.assertIn("en", picker._collect())

    def test_empty_value_checks_nothing(self):
        picker = self._picker("")
        self.assertEqual(picker._collect(), [])

    def test_select_all_and_none(self):
        picker = self._picker("")
        picker._select_all(True)
        self.assertEqual(len(picker._collect()), len(gui.SUBTITLE_LANGUAGES))
        picker._select_all(False)
        self.assertEqual(picker._collect(), [])

    def test_collect_order_follows_the_list(self):
        picker = self._picker("ja,en,zh-Hans")   # 输入顺序与列表顺序不同
        codes = picker._collect()
        self.assertEqual(sorted(codes), ["en", "ja", "zh-Hans"])
        self.assertLess(codes.index("zh-Hans"), codes.index("en"))
        self.assertLess(codes.index("en"), codes.index("ja"))

    def test_confirm_joins_with_comma_and_cancel_returns_none(self):
        picker = self._picker("zh-Hans")
        picker._confirm()
        self.assertEqual(picker.result, "zh-Hans")

        picker = self._picker("en,xx")
        picker._cancel()
        self.assertIsNone(picker.result)

    def test_codes_are_yt_dlp_style(self):
        for code, name in gui.SUBTITLE_LANGUAGES:
            self.assertTrue(code and name)
            self.assertNotIn(",", code, "单个代码内不能有逗号")

    def test_title_is_localized(self):
        picker = self._picker()
        self.assertEqual(picker.title(), i18n.tr("subtitle_pick_title"))
        self.assertNotEqual(picker.title(), "subtitle_pick_title")


class SubtitleSettingWiringTests(unittest.TestCase):
    """设置页那行的接线（用假弹窗避免模态阻塞）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()
        cls.page = cls.app.pages["settings"]

    def setUp(self):
        self.saved = {}
        settings_mod.save = lambda data: self.saved.update(data)

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def test_typing_codes_is_persisted(self):
        self.page.subtitle_langs_var.set("en,ja")
        self.app.update_idletasks()
        self.assertEqual(self.saved.get("subtitle_langs"), "en,ja")

    def test_picker_result_is_written_back(self):
        # _open_subtitle_picker 是设置页的方法，代码住在 app/settingspage.py
        original = settingspage.SubtitleLanguagePicker

        class FakePicker:
            def __init__(self, *a, **kw):
                pass

            def show(self):
                return "zh-Hans,en"

        settingspage.SubtitleLanguagePicker = FakePicker
        try:
            self.page._open_subtitle_picker()
        finally:
            settingspage.SubtitleLanguagePicker = original
        self.assertEqual(self.page.subtitle_langs_var.get(), "zh-Hans,en")
        self.assertEqual(self.saved.get("subtitle_langs"), "zh-Hans,en")

    def test_cancelling_picker_changes_nothing(self):
        original = settingspage.SubtitleLanguagePicker

        class Cancelling:
            def __init__(self, *a, **kw):
                pass

            def show(self):
                return None

        self.page.subtitle_langs_var.set("en")
        self.app.update_idletasks()
        settingspage.SubtitleLanguagePicker = Cancelling
        try:
            self.page._open_subtitle_picker()
        finally:
            settingspage.SubtitleLanguagePicker = original
        self.assertEqual(self.page.subtitle_langs_var.get(), "en")

    def test_choose_button_exists(self):
        self.assertTrue(self.page.subtitle_langs_entry.winfo_exists())


if __name__ == "__main__":
    unittest.main()
