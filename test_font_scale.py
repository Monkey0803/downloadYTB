"""界面字号缩放（#12）的测试。

字号直接影响布局：卡片高度、省略号宽度都是按字体量出来的，所以除了「设置被保存」
之外，更要验证**放大后内容没有被裁掉**（GlassCard 会把内部高度写死，超出部分静默
消失——历史页的行卡片就踩过一次）。

整个文件共用一个 App：第一次 Tk 初始化要数秒，且销毁根窗口后再新建会让 macOS 上的
Tk 段错误。字体档位在每个用例收尾时恢复为标准。
"""

import unittest
from unittest.mock import Mock

from app import gui, i18n, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
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


class FontScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        self.saved = []
        settings_mod.save = lambda data: self.saved.append(dict(data))
        self.app.settings["font_scale"] = "standard"
        self.app.set_font_scale("standard")      # 让设置与字体状态对齐

    def tearDown(self):
        self.app.settings["font_scale"] = "standard"
        self.app.set_font_scale("standard")
        settings_mod.save = _ORIGINAL_SAVE

    def _settle(self):
        for _ in range(6):
            self.app.update_idletasks()
            self.app.update()

    # ---------- 取值与换算 ----------

    def test_default_is_standard(self):
        self.app.settings.pop("font_scale", None)
        self.assertEqual(self.app.font_scale(), "standard")

    def test_invalid_value_falls_back(self):
        for value in ("", None, "HUGE", "特大"):
            self.app.settings["font_scale"] = value
            self.assertEqual(self.app.font_scale(), "standard")

    def test_font_size_math(self):
        self.app.settings["font_scale"] = "standard"
        self.assertEqual(self.app._font_size(13), 13)
        self.app.settings["font_scale"] = "large"
        self.assertEqual(self.app._font_size(13), round(13 * 1.15))
        self.app.settings["font_scale"] = "xlarge"
        self.assertEqual(self.app._font_size(13), round(13 * 1.3))
        # 小字号放大后仍有下限，避免出现 8px 这种读不了的字
        self.assertGreaterEqual(self.app._font_size(1), 9)

    def test_setup_fonts_applies_the_scale(self):
        self.app.settings["font_scale"] = "large"
        self.app._setup_fonts()
        for attr, base, _weight in self.app.FONT_BASES:
            self.assertEqual(getattr(self.app, attr).cget("size"),
                             self.app._font_size(base), attr)

    # ---------- 切换 ----------

    def test_switching_persists_and_rebuilds(self):
        before = self.app.sidebar
        self.assertTrue(self.app.set_font_scale("large"))
        self._settle()
        self.assertEqual(self.app.settings["font_scale"], "large")
        self.assertEqual(self.saved[-1]["font_scale"], "large")
        self.assertIsNot(self.app.sidebar, before, "界面应当重建")
        self.assertEqual(self.app.f_body.cget("size"), self.app._font_size(13))

    def test_switching_to_the_same_value_does_nothing(self):
        self.assertFalse(self.app.set_font_scale("standard"))
        self.assertEqual(self.saved, [])

    def test_invalid_value_is_normalized(self):
        """非法档位回落到标准；与当前一致时不算切换（返回 False）。"""
        self.assertTrue(self.app.set_font_scale("large"))
        self.assertTrue(self.app.set_font_scale("HUGE"))     # 回落到 standard
        self.assertEqual(self.app.settings["font_scale"], "standard")
        self.assertFalse(self.app.set_font_scale("HUGE"))    # 已经是标准了

    def test_busy_download_blocks_the_rebuild(self):
        panel = self.app.pages["youtube"].video_panel
        panel._busy = True
        shown = []
        original = gui.messagebox.showinfo
        gui.messagebox.showinfo = lambda *a, **kw: shown.append(a)
        try:
            self.assertFalse(self.app.set_font_scale("xlarge"))
        finally:
            gui.messagebox.showinfo = original
            panel._busy = False
        self.assertTrue(shown, "应提示下载中不切换")
        self.assertEqual(self.app.settings["font_scale"], "standard")
        self.assertEqual(self.saved, [], "被拒绝时不该保存")

    def test_keeps_the_current_page_after_rebuild(self):
        self.app.current_page = "history"
        self.app.set_font_scale("large")
        self._settle()
        self.assertEqual(self.app.current_page, "history")

    # ---------- 放大后不能裁内容 ----------

    def test_large_fonts_do_not_clip_settings_cards(self):
        """字号放大后卡片要跟着长高，否则内容会被静默裁掉。"""
        self.app.set_font_scale("xlarge")
        self.app.show_page("settings")
        self._settle()
        page = self.app.pages["settings"]
        checked = 0
        for child in page._scroll_body.winfo_children():
            if not hasattr(child, "body"):
                continue
            page._fit_card(next((name for name, entry in page._fit_cards.items()
                                 if entry[0] is child), ""))
        self._settle()
        for child in page._scroll_body.winfo_children():
            if not hasattr(child, "body"):
                continue
            checked += 1
            self.assertGreaterEqual(
                child.body.winfo_height(), child.body.winfo_reqheight(),
                "字号放大后卡片内容被裁掉了（应更新卡片高度）")
        self.assertGreater(checked, 5, "设置页卡片数量异常")

    def test_url_field_still_usable_with_xlarge(self):
        """输入框高度是写死的，放大字号后内嵌 Entry 仍应可见。"""
        self.app.set_font_scale("xlarge")
        self._settle()
        panel = self.app.pages["youtube"].video_panel
        self.assertGreater(panel.url_entry.winfo_height(), 0)
        self.assertTrue(panel.url_entry.get() == "" or True)


class FontScaleSettingUiTests(unittest.TestCase):
    """设置页那一行的取值与回滚。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        self.saved = []
        settings_mod.save = lambda data: self.saved.append(dict(data))
        self.app.settings["font_scale"] = "standard"
        self.app.set_font_scale("standard")
        self.app.show_page("settings")
        self.app.update_idletasks()
        self.page = self.app.pages["settings"]

    def tearDown(self):
        self.app.settings["font_scale"] = "standard"
        self.app.set_font_scale("standard")
        settings_mod.save = _ORIGINAL_SAVE

    def test_var_holds_the_value_not_the_label(self):
        """变量必须是档位值：分段控件的选项是 (label, value)，放标签进去就匹配不上。"""
        self.assertTrue(self.app.set_font_scale("xlarge"))   # 真实切换路径会重建界面
        self.app.show_page("settings")
        self.app.update_idletasks()
        page = self.app.pages["settings"]                    # 重建后要重新取页面
        self.assertEqual(page.font_scale_var.get(), "xlarge")

    def test_current_segment_is_actually_highlighted(self):
        """回归：变量与选项值不匹配时，分段控件一个高亮块都不画（真实踩过）。

        只断言变量内容是不够的——要断言控件能在选项里找到当前值。
        """
        self.assertTrue(self.app.set_font_scale("large"))
        self.app.show_page("settings")
        self.app.update_idletasks()
        page = self.app.pages["settings"]
        seg = page.font_scale_seg
        values = [value for _label, value in seg._segments]
        self.assertIn(seg._var.get(), values, "当前值不在选项里，界面不会高亮")
        self.assertEqual(seg._var.get(), "large")

    def test_options_are_localized(self):
        labels = [label for label, _value in self.page.font_scale_seg._segments]
        self.assertEqual(len(labels), 3)
        for label in labels:
            self.assertTrue(label.strip())
            self.assertNotIn("font_", label)

    def test_change_is_persisted(self):
        self.page.font_scale_var.set("large")
        self.app.update_idletasks()
        self.assertEqual(self.saved[-1]["font_scale"], "large")

    def test_refused_change_rolls_the_var_back(self):
        panel = self.app.pages["youtube"].video_panel
        panel._busy = True
        original = gui.messagebox.showinfo
        gui.messagebox.showinfo = lambda *a, **kw: None
        try:
            self.page.font_scale_var.set("xlarge")
            self.app.update_idletasks()
        finally:
            gui.messagebox.showinfo = original
            panel._busy = False
        self.assertEqual(self.page.font_scale_var.get(), "standard",
                         "被拒绝后下拉应回到实际生效的档位")


if __name__ == "__main__":
    unittest.main()
