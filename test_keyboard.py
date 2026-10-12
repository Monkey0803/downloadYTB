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


class ImagePickerKeyboardTests(unittest.TestCase):
    """缩略图网格的键盘导航。

    格子是画在画布上的图形、没有子控件，所以 Tab 到画布后要靠自己维护一个游标：
    方向键移动、Home/End 跳转、空格/回车勾选、移动时自动滚进可视区域。这是键盘
    可达性里最后补上的一块（此前只能鼠标点）。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.host = tk.Toplevel(self.app)
        self.host.geometry("420x320")
        self.changes = []
        # on_change 是**无参**回调（与面板用法一致），传 list.append 会被 Tk 吞成
        # TypeError，看起来像「回调没被调用」
        self.picker = widgets.ImagePicker(self.host, on_change=self._record_change,
                                          height=140)
        self.picker.pack(fill="both", expand=True, padx=10, pady=10)
        self.items = [{"label": f"图{i}", "sub": "", "selected": False, "photo": None,
                       "font_small": self.app.f_small} for i in range(9)]
        self.picker.set_items(self.items)
        self.host.update_idletasks()
        for _ in range(4):
            self.host.update()

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        self.host.destroy()

    def _record_change(self):
        self.changes.append(1)

    def _press(self, key):
        """按键前必须先让画布持有焦点：否则事件会发给别的控件（Tk 的坑）。"""
        self.picker.canvas.focus_force()
        self.host.update_idletasks()
        self.picker.canvas.event_generate(key)
        self.host.update_idletasks()

    def test_canvas_is_tab_reachable(self):
        self.assertEqual(str(self.picker.canvas.cget("takefocus")), "1")

    def test_cursor_starts_at_the_first_item(self):
        self.assertEqual(self.picker.cursor_index(), 0)

    def test_right_and_left_move_the_cursor(self):
        self._press("<Right>")
        self.assertEqual(self.picker.cursor_index(), 1)
        self._press("<Right>")
        self.assertEqual(self.picker.cursor_index(), 2)
        self._press("<Left>")
        self.assertEqual(self.picker.cursor_index(), 1)

    def test_down_and_up_move_a_whole_row(self):
        cols = self.picker._cols()
        self.assertGreaterEqual(cols, 1, "画布宽度不足，测试环境异常")
        self.picker._move_cursor_to(cols - 1)
        self._press("<Down>")
        self.assertEqual(self.picker.cursor_index(), cols * 2 - 1)
        self._press("<Up>")
        self.assertEqual(self.picker.cursor_index(), cols - 1)

    def test_cursor_is_clamped_at_both_ends(self):
        self._press("<Left>")
        self.assertEqual(self.picker.cursor_index(), 0)
        self.picker._move_cursor_to(len(self.items) - 1)
        self._press("<Right>")
        self.assertEqual(self.picker.cursor_index(), len(self.items) - 1)

    def test_home_and_end_jump(self):
        self._press("<End>")
        self.assertEqual(self.picker.cursor_index(), len(self.items) - 1)
        self._press("<Home>")
        self.assertEqual(self.picker.cursor_index(), 0)

    def test_space_toggles_the_cursor_item(self):
        self._press("<Right>")
        self._press("<space>")
        self.assertTrue(self.items[1]["selected"])
        self.assertEqual(len(self.changes), 1)
        self._press("<space>")
        self.assertFalse(self.items[1]["selected"])

    def test_return_toggles_the_cursor_item(self):
        self._press("<Return>")
        self.assertTrue(self.items[0]["selected"])

    def test_moving_the_cursor_does_not_change_selection(self):
        self._press("<space>")
        self._press("<Right>")
        self._press("<Right>")
        self.assertEqual(self.picker.selected_indices(), [0])

    def test_cursor_is_drawn_only_while_focused(self):
        self.host.update()
        self.picker.canvas.event_generate("<FocusOut>")
        self.host.update_idletasks()
        self.assertEqual(self.picker.canvas.find_withtag("cursor"), ())
        self.picker.canvas.focus_force()
        self.picker.canvas.event_generate("<FocusIn>")
        self.host.update_idletasks()
        self.assertTrue(self.picker.canvas.find_withtag("cursor"),
                        "获得焦点后应当画出游标环")

    def test_click_moves_the_cursor(self):
        cols = self.picker._cols()
        index = min(len(self.items) - 1, cols)     # 第二行第一格
        row, col = divmod(index, cols)
        x = self.picker.OUTER_PAD + col * (self.picker.CELL_W + self.picker.GAP_X) + 8
        y = self.picker.OUTER_PAD + row * (self.picker.CELL_H + self.picker.GAP_Y) + 8
        self.picker.canvas.event_generate("<Button-1>", x=x, y=y)
        self.host.update_idletasks()
        self.assertEqual(self.picker.cursor_index(), index)
        self._press("<Right>")
        self.assertEqual(self.picker.cursor_index(), index + 1)

    def test_cursor_scrolls_into_view(self):
        for _ in range(len(self.items)):
            self._press("<Down>")
        self.assertGreater(self.picker.canvas.yview()[0], 0.0,
                           "游标移到底部后应当自动滚动")

    def test_empty_picker_is_safe(self):
        self.picker.set_items([])
        self.host.update_idletasks()
        self.assertIsNone(self.picker.cursor_index())
        for key in ("<Right>", "<Left>", "<Down>", "<Up>", "<Home>", "<End>",
                    "<space>", "<Return>"):
            self._press(key)        # 不应抛异常
        self.assertEqual(self.changes, [])

    def test_set_items_resets_the_cursor(self):
        self.picker._move_cursor_to(5)
        self.picker.set_items(self.items)
        self.assertEqual(self.picker.cursor_index(), 0)


if __name__ == "__main__":
    unittest.main()
