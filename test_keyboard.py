"""键盘可达性（#8）的测试。

自绘控件（PillButton / CheckPill / SegmentedControl / PillSelect / SidebarItem /
LogoSwatch）原本只绑鼠标事件：Tab 走不到、键盘按不动、也看不出焦点在哪。这里锁住
三件事：**能 Tab 到**（takefocus）、**能按键激活**、**焦点可见**（画出焦点环）。

整个文件共用一个 App：第一次 Tk 初始化要数秒，且销毁根窗口后再新建会让 macOS 上的
Tk 段错误（真实踩过）。控件挂在一个 Toplevel 里，用完销毁 Toplevel 是安全的。
"""

import unittest
from unittest.mock import patch

import tkinter as tk

from app import gui, i18n, settings as settings_mod, widgets

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


class KeyboardSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.host = tk.Toplevel(self.app)
        self.host.geometry("360x300")
        self.pressed = []
        self.host.update_idletasks()

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        self.host.destroy()

    def _settle(self):
        for _ in range(5):
            self.host.update_idletasks()
            self.host.update()

    def _press(self, widget, sequence):
        """给控件焦点再发按键。

        实测：`event_generate("<Return>")` 只有在控件**持有焦点**时才会触发绑定
        （否则 Tk 把按键投给当前焦点控件，等于没按）。测试窗口通常不在前台，
        所以用 focus_force 而不是 focus_set。
        """
        widget.focus_force()
        widget.event_generate(sequence)
        self._settle()

    def _button(self, **kw):
        btn = widgets.PillButton(self.host, "确定",
                                 command=lambda: self.pressed.append("click"), **kw)
        btn.pack(fill="x", padx=10, pady=4)
        self._settle()
        return btn

    # ---------- 能 Tab 到 ----------

    def test_widgets_are_focusable(self):
        check = widgets.CheckPill(self.host, "选项", tk.BooleanVar(value=False),
                                  bg_role="BG")
        check.pack(anchor="w")
        seg = widgets.SegmentedControl(self.host, [("甲", "a"), ("乙", "b")],
                                       tk.StringVar(value="a"), bg_role="BG")
        seg.pack(anchor="w")
        select = widgets.PillSelect(self.host, tk.StringVar(value="甲"),
                                    values=["甲", "乙"], bg_role="BG")
        select.pack(anchor="w")
        side = widgets.SidebarItem(self.host, "YouTube", "youtube",
                                   command=lambda: None)
        side.pack(anchor="w")
        self._settle()
        for name, widget in (("PillButton", self._button()), ("CheckPill", check),
                             ("SegmentedControl", seg), ("PillSelect", select),
                             ("SidebarItem", side)):
            self.assertEqual(str(widget.cget("takefocus")), "1", f"{name} 无法 Tab 到")

    def test_disabled_button_is_not_focusable(self):
        btn = self._button()
        btn.configure_state("disabled")
        self.assertEqual(str(btn.cget("takefocus")), "0",
                         "禁用按钮不该被 Tab 选中")
        btn.configure_state("normal")
        self.assertEqual(str(btn.cget("takefocus")), "1")

    def test_disabled_select_is_not_focusable(self):
        select = widgets.PillSelect(self.host, tk.StringVar(value="甲"),
                                    values=["甲"], bg_role="BG")
        select.pack()
        self._settle()
        select.set_enabled(False)
        self.assertEqual(str(select.cget("takefocus")), "0")

    # ---------- 能按键激活 ----------

    def test_activation_keys_are_bound(self):
        for widget, name in ((self._button(), "PillButton"),):
            for sequence in widgets.ACTIVATION_KEYS:
                self.assertTrue(widget.bind(sequence), f"{name} 缺少 {sequence}")

    def test_enter_and_space_click_the_button(self):
        btn = self._button()
        for sequence in ("<Return>", "<space>"):
            self.pressed.clear()
            self._press(btn, sequence)
            self.assertEqual(self.pressed, ["click"], f"{sequence} 没有触发命令")

    def test_disabled_button_ignores_keys(self):
        btn = self._button()
        btn.configure_state("disabled")
        self._press(btn, "<Return>")
        self.assertEqual(self.pressed, [])

    def test_space_toggles_a_check_pill(self):
        var = tk.BooleanVar(value=False)
        pill = widgets.CheckPill(self.host, "选项", var, bg_role="BG")
        pill.pack(anchor="w")
        self._settle()
        self._press(pill, "<space>")
        self.assertTrue(var.get())
        self._press(pill, "<space>")
        self.assertFalse(var.get())

    def test_arrow_keys_move_the_segmented_control(self):
        var = tk.StringVar(value="a")
        changes = []
        seg = widgets.SegmentedControl(self.host, [("甲", "a"), ("乙", "b"), ("丙", "c")],
                                       var, command=lambda: changes.append(var.get()),
                                       bg_role="BG")
        seg.pack(anchor="w")
        self._settle()
        self._press(seg, "<Right>")
        self.assertEqual(var.get(), "b")
        self._press(seg, "<Down>")
        self.assertEqual(var.get(), "c")
        self._press(seg, "<Left>")
        self.assertEqual(var.get(), "b")
        self.assertEqual(changes, ["b", "c", "b"])

    def test_segmented_control_clamps_at_the_ends(self):
        var = tk.StringVar(value="a")
        seg = widgets.SegmentedControl(self.host, [("甲", "a"), ("乙", "b")], var,
                                       bg_role="BG")
        seg.pack(anchor="w")
        self._settle()
        self._press(seg, "<Left>")
        self.assertEqual(var.get(), "a", "已在最左不该回绕或越界")

    def test_unknown_value_does_not_crash_arrow_keys(self):
        var = tk.StringVar(value="不存在")
        seg = widgets.SegmentedControl(self.host, [("甲", "a"), ("乙", "b")], var,
                                       bg_role="BG")
        seg.pack(anchor="w")
        self._settle()
        self._press(seg, "<Right>")
        self.assertIn(var.get(), ("a", "b"))

    def test_enter_opens_the_select_popup(self):
        var = tk.StringVar(value="甲")
        select = widgets.PillSelect(self.host, var, values=["甲", "乙"], bg_role="BG")
        select.pack(anchor="w")
        self._settle()
        with patch.object(widgets.tk, "Menu") as menu_cls:
            menu_cls.return_value.tk_popup = lambda *a, **kw: self.pressed.append("popup")
            self._press(select, "<Return>")
        self.assertEqual(self.pressed, ["popup"])

    def test_sidebar_item_activates_with_keys(self):
        side = widgets.SidebarItem(self.host, "YouTube", "youtube",
                                   command=lambda: self.pressed.append("nav"))
        side.pack(anchor="w")
        self._settle()
        self._press(side, "<Return>")
        self.assertEqual(self.pressed, ["nav"])

    def test_logo_swatch_activates_with_keys(self):
        photo = tk.PhotoImage(width=4, height=4)
        swatch = widgets.LogoSwatch(self.host, "经典蓝", photo,
                                    command=lambda: self.pressed.append("logo"),
                                    bg_role="BG")
        swatch.pack(anchor="w")
        self._settle()
        self._press(swatch, "<space>")
        self.assertEqual(self.pressed, ["logo"])

    # ---------- 焦点可见 ----------

    def test_focus_draws_a_visible_ring(self):
        btn = self._button()
        before = len(btn.find_all())
        self._press(btn, "<FocusIn>")
        self.assertTrue(btn._focused)
        self.assertEqual(len(btn.find_all()), before + 1, "焦点环没有画出来")
        self._press(btn, "<FocusOut>")
        self.assertFalse(btn._focused)
        self.assertEqual(len(btn.find_all()), before, "失焦后焦点环没有擦掉")

    def test_check_pill_draws_focus_ring(self):
        pill = widgets.CheckPill(self.host, "选项", tk.BooleanVar(value=False),
                                 bg_role="BG")
        pill.pack(anchor="w")
        self._settle()
        before = len(pill.find_all())
        self._press(pill, "<FocusIn>")
        self.assertEqual(len(pill.find_all()), before + 1)

    def test_segmented_control_draws_focus_ring(self):
        seg = widgets.SegmentedControl(self.host, [("甲", "a"), ("乙", "b")],
                                       tk.StringVar(value="a"), bg_role="BG")
        seg.pack(anchor="w")
        self._settle()
        before = len(seg.find_all())
        self._press(seg, "<FocusIn>")
        self.assertEqual(len(seg.find_all()), before + 1)

    def test_tab_reaches_a_button_from_the_url_entry(self):
        """端到端：从链接输入框按 Tab 应该能走到按钮上（原来走不到）。"""
        panel = self.app.pages["youtube"].video_panel
        entry = panel.url_entry.entry
        entry.focus_set()
        self._settle()
        reached = None
        current = str(entry)
        seen = set()
        for _ in range(40):
            # tk_focusNext 返回 Tcl_Obj，必须转成 str 才能比较/入集合
            current = str(self.app.tk.call("tk_focusNext", current))
            if not current or current in seen:
                break
            seen.add(current)
            widget = self.app.nametowidget(current)
            # 排除本用例自己建的 host 里的控件，要证明的是应用自己的界面
            if (widget.winfo_class() == "Canvas"
                    and not str(widget).startswith(str(self.host))):
                reached = widget
                break
        self.assertIsNotNone(reached,
                             f"从链接输入框按 Tab 走不到应用自己的自绘控件：{sorted(seen)[:8]}")
        self.assertEqual(str(reached.cget("takefocus")), "1")


if __name__ == "__main__":
    unittest.main()
