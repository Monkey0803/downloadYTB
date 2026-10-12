"""设置页分组（④）的结构测试。

动机：设置页当时是 10 张卡片一路滚到底，没有分组，找一项设置要整页扫。
分组标题是纯布局改动，最容易出的错是「插错位置」或「改的过程中弄丢卡片」，
所以这里按出现顺序把「分组标题 + 卡片」的序列整体断言下来。

整个文件共用一个 App：第一次 Tk 初始化要数秒，且销毁根窗口后再新建会让
macOS 上的 Tk 段错误（真实踩过）。
"""

import unittest

from app import gui, i18n, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

# 期望的结构：分组标题 -> 该组内的卡片标题（i18n 键）
EXPECTED = (
    ("settings_section_general", ["theme", "language", "app_logo"]),
    ("settings_section_save", ["default_save"]),
    ("settings_section_cookies", ["browser_cookie", "platform_cookies"]),
    ("settings_section_download", ["download_feedback", "subtitles"]),
    ("settings_section_advanced", ["shortcuts", "diagnostics"]),
)

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


class SettingsGroupingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()
        cls.page = cls.app.pages["settings"]

    def setUp(self):
        settings_mod.save = lambda data: None

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def _structure(self):
        """按出现顺序返回 ('section'|'card', 文本) 列表。"""
        result = []
        for child in self.page._scroll_body.winfo_children():
            if hasattr(child, "body"):          # GlassCard
                titles = [w.cget("text") for w in child.body.winfo_children()
                          if isinstance(w, gui.TLabel)]
                result.append(("card", titles[0] if titles else ""))
                continue
            try:
                text = child.cget("text")
            except Exception:
                continue                        # PageHeader 之类的容器
            if text:
                result.append(("section", text))
        return result

    def test_sections_appear_in_order_with_their_cards(self):
        structure = self._structure()
        expected = []
        for key, cards in EXPECTED:
            expected.append(("section", i18n.tr(key)))
            expected.extend(("card", i18n.tr(card)) for card in cards)
        self.assertEqual(structure, expected)

    def test_every_card_is_accounted_for(self):
        """分组是纯布局改动，卡片数量不该变。"""
        cards = [text for kind, text in self._structure() if kind == "card"]
        self.assertEqual(len(cards), 10)
        self.assertEqual(len(set(cards)), 10, "不该出现重复卡片标题")

    def test_section_titles_are_localized(self):
        titles = [text for kind, text in self._structure() if kind == "section"]
        for text in titles:
            self.assertNotIn("settings_section", text)
            self.assertTrue(text.strip())

    def test_section_title_does_not_repeat_the_card_title_right_below(self):
        """分组标题与紧随其后的卡片标题重复会显得很业余。"""
        structure = self._structure()
        for index, (kind, text) in enumerate(structure[:-1]):
            if kind != "section":
                continue
            nxt_kind, nxt_text = structure[index + 1]
            if nxt_kind == "card":
                self.assertNotEqual(text, nxt_text, f"{text} 既是分组名又是卡片名")

    def test_sections_are_on_screen_in_every_language(self):
        """五种语言下的分组标题都要有实际文案，不能退化成键名。"""
        original = i18n.language()
        try:
            for code in ("zh_CN", "zh_TW", "en", "ko", "ja"):
                i18n.set_language(code)
                for key, _cards in EXPECTED:
                    text = i18n.tr(key)
                    self.assertNotEqual(text, key, f"{code} 缺少 {key}")
        finally:
            i18n.set_language(original)


if __name__ == "__main__":
    unittest.main()
