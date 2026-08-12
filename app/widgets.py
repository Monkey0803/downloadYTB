"""Liquid Glass 自绘控件库（主题感知版）。

所有控件在绘制时从 theme.C() 读取当前调色板；普通 tk.Frame/tk.Label 使用
TFrame/TLabel 包装并记录配色角色。切换主题后调用 apply_theme_tree(root)
即可让整棵控件树换肤重绘，无需重建界面。
"""

import os
import sys
import tkinter as tk

from . import theme


def _round_rect_points(x1, y1, x2, y2, r):
    """圆角矩形的平滑多边形顶点。"""
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def draw_round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return canvas.create_polygon(_round_rect_points(x1, y1, x2, y2, r), smooth=True, **kw)


def ellipsize(text: str, font, max_px: int) -> str:
    """文本超宽时尾部截断加省略号。"""
    if max_px <= 0 or font.measure(text) <= max_px:
        return text
    while text and font.measure(text + "…") > max_px:
        text = text[:-1]
    return text + "…"


def ellipsize_filename(name: str, font, max_px: int) -> str:
    """文件名超宽时保留扩展名、截断主干（如「AIロイド - ワール….mp4」）。"""
    if max_px <= 0 or font.measure(name) <= max_px:
        return name
    stem, ext = os.path.splitext(name)
    while stem and font.measure(stem + "…" + ext) > max_px:
        stem = stem[:-1]
    return stem + "…" + ext


def hand_cursor():
    return "pointinghand" if sys.platform == "darwin" else "hand2"


def apply_theme_tree(widget):
    """递归遍历控件树，让每个控件按当前调色板换肤。"""
    if hasattr(widget, "retheme"):
        widget.retheme()
    for child in widget.winfo_children():
        apply_theme_tree(child)


# ---------- 基础容器 / 文本 ----------


class TFrame(tk.Frame):
    """记录配色角色的 Frame，支持 retheme。"""

    def __init__(self, master, bg_role="BG", **kw):
        self._bg_role = bg_role
        super().__init__(master, bg=getattr(theme.C(), bg_role), **kw)

    def retheme(self):
        self.configure(bg=getattr(theme.C(), self._bg_role))


class TLabel(tk.Label):
    """记录配色角色的 Label，支持 retheme 与动态前景角色。"""

    def __init__(self, master, bg_role="BG", fg_role="TEXT", **kw):
        self._bg_role = bg_role
        self._fg_role = fg_role
        c = theme.C()
        super().__init__(master, bg=getattr(c, bg_role), fg=getattr(c, fg_role), **kw)

    def set_fg_role(self, role):
        self._fg_role = role
        self.configure(fg=getattr(theme.C(), role))

    def retheme(self):
        c = theme.C()
        self.configure(bg=getattr(c, self._bg_role), fg=getattr(c, self._fg_role))


# ---------- 玻璃卡片 ----------


class GlassCard(TFrame):
    """圆角玻璃卡片：Canvas 画底 + 内嵌内容 Frame。"""

    RADIUS = 18

    def __init__(self, master, bg_role="BG", **kw):
        super().__init__(master, bg_role=bg_role, **kw)
        self._canvas = tk.Canvas(self, bg=getattr(theme.C(), bg_role),
                                 highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)
        self.body = TFrame(self._canvas, bg_role="CARD")
        self._win = None
        self._canvas.bind("<Configure>", self._redraw)

    def _redraw(self, event=None):
        c = self._canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 4 or h < 4:
            return
        pal = theme.C()
        c.delete("bg")
        r = self.RADIUS
        # 投影 → 高光描边 → 卡片表面，三层叠出柔和玻璃质感
        draw_round_rect(c, 3, 5, w - 1, h - 1, r, fill=pal.CARD_SHADOW, outline="", tags="bg")
        draw_round_rect(c, 1, 1, w - 3, h - 4, r, fill=pal.CARD_BORDER, outline="", tags="bg")
        draw_round_rect(c, 2, 2, w - 4, h - 5, r - 1, fill=pal.CARD, outline="", tags="bg")
        c.tag_lower("bg")
        if self._win is None:
            self._win = c.create_window(0, 0, anchor="nw", window=self.body)
        pad = 16
        c.coords(self._win, pad, pad - 2)
        c.itemconfigure(self._win, width=w - pad * 2 - 4, height=h - pad * 2 - 1)

    def set_height(self, h):
        self._canvas.configure(height=h)

    def retheme(self):
        super().retheme()
        self._canvas.configure(bg=getattr(theme.C(), self._bg_role))
        self._redraw()


# ---------- 按钮 / 选择控件 ----------


class PillButton(tk.Canvas):
    """胶囊形按钮，支持主色 / 次级两种样式与悬停、禁用状态。"""

    def __init__(self, master, text, command=None, primary=False, font=None,
                 width=92, height=34, bg_role="BG"):
        self._bg_role = bg_role
        super().__init__(master, width=width, height=height,
                         bg=getattr(theme.C(), bg_role), highlightthickness=0, bd=0)
        self._text = text
        self._command = command
        self._primary = primary
        self._font = font
        self._state = "normal"
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _colors(self):
        c = theme.C()
        if self._primary:
            if self._state == "disabled":
                return c.ACCENT_DISABLED, "#FFFFFF"
            return (c.ACCENT_HOVER if self._hover else c.ACCENT), "#FFFFFF"
        if self._state == "disabled":
            return c.PILL_BG, c.PILL_TEXT_DISABLED
        return (c.PILL_HOVER if self._hover else c.PILL_BG), c.TEXT

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        fill, fg = self._colors()
        draw_round_rect(self, 1, 1, w - 1, h - 1, (h - 2) / 2, fill=fill, outline="")
        self.create_text(w / 2, h / 2 - 1, text=self._text, fill=fg, font=self._font)

    def _on_enter(self, e):
        self._hover = True
        self._draw()
        if self._state == "normal":
            self.configure(cursor=hand_cursor())

    def _on_leave(self, e):
        self._hover = False
        self._draw()

    def _on_click(self, e):
        if self._state == "normal" and self._command:
            self._command()

    def set_text(self, text):
        self._text = text
        self._draw()

    def configure_state(self, state):
        self._state = state
        self._draw()

    def retheme(self):
        self.configure(bg=getattr(theme.C(), self._bg_role))
        self._draw()


class SegmentedControl(tk.Canvas):
    """分段选择控件（模拟 macOS segmented control）。"""

    def __init__(self, master, options, variable, command=None, font=None,
                 width=240, height=34, bg_role="CARD"):
        self._bg_role = bg_role
        super().__init__(master, width=width, height=height,
                         bg=getattr(theme.C(), bg_role), highlightthickness=0, bd=0)
        self._segments = options  # [(label, value), ...]
        self._var = variable
        self._command = command
        self._font = font
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._on_click)

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        c = theme.C()
        draw_round_rect(self, 1, 1, w - 1, h - 1, (h - 2) / 2, fill=c.FIELD_BG, outline="")
        n = len(self._segments)
        seg_w = (w - 6) / n
        cur = self._var.get()
        for i, (label, value) in enumerate(self._segments):
            x1 = 3 + i * seg_w
            if value == cur:
                draw_round_rect(self, x1 + 1, 3, x1 + seg_w - 1, h - 3,
                                (h - 6) / 2, fill=c.SEG_ACTIVE, outline="")
            self.create_text(x1 + seg_w / 2, h / 2 - 1, text=label,
                             fill=c.TEXT if value == cur else c.TEXT_SUB, font=self._font)

    def _on_click(self, e):
        w = self.winfo_width()
        n = len(self._segments)
        idx = min(n - 1, max(0, int((e.x - 3) / ((w - 6) / n))))
        value = self._segments[idx][1]
        if value != self._var.get():
            self._var.set(value)
            self._draw()
            if self._command:
                self._command()

    def refresh(self):
        self._draw()

    def retheme(self):
        self.configure(bg=getattr(theme.C(), self._bg_role))
        self._draw()


class PillSelect(tk.Canvas):
    """胶囊形下拉选择器，点击弹出 tk.Menu。"""

    def __init__(self, master, variable, values=None, font=None,
                 width=132, height=34, bg_role="CARD"):
        self._bg_role = bg_role
        super().__init__(master, width=width, height=height,
                         bg=getattr(theme.C(), bg_role), highlightthickness=0, bd=0)
        self._var = variable
        self._values = values or []
        self._font = font
        self._enabled = True
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._popup)
        self._var.trace_add("write", lambda *a: self._draw())

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        c = theme.C()
        fill = c.FIELD_BG if self._enabled else c.PILL_BG
        fg = c.TEXT if self._enabled else c.PILL_TEXT_DISABLED
        draw_round_rect(self, 1, 1, w - 1, h - 1, (h - 2) / 2,
                        fill=fill, outline=c.FIELD_BORDER)
        self.create_text(14, h / 2 - 1, text=self._var.get(), fill=fg,
                         font=self._font, anchor="w")
        # 下拉箭头
        ax = w - 18
        ay = h / 2
        self.create_line(ax - 4, ay - 2, ax, ay + 3, ax + 4, ay - 2,
                         fill=fg, width=1.6, capstyle="round", joinstyle="round")

    def set_values(self, values):
        self._values = list(values)
        self._draw()

    def set_enabled(self, enabled):
        self._enabled = enabled
        self._draw()

    def _popup(self, e):
        if not self._enabled or not self._values:
            return
        menu = tk.Menu(self, tearoff=0)
        for v in self._values:
            menu.add_command(label=v, command=lambda v=v: self._var.set(v))
        menu.tk_popup(self.winfo_rootx() + 4, self.winfo_rooty() + self.winfo_height())

    def retheme(self):
        self.configure(bg=getattr(theme.C(), self._bg_role))
        self._draw()


class RoundEntry(tk.Canvas):
    """圆角输入框：Canvas 画底，内嵌无边框 tk.Entry。"""

    def __init__(self, master, textvariable=None, font=None, height=36, bg_role="CARD"):
        self._bg_role = bg_role
        super().__init__(master, height=height, bg=getattr(theme.C(), bg_role),
                         highlightthickness=0, bd=0)
        c = theme.C()
        self.entry = tk.Entry(
            self, textvariable=textvariable, bd=0, relief="flat",
            bg=c.FIELD_BG, fg=c.TEXT, insertbackground=c.TEXT,
            highlightthickness=0, font=font,
        )
        self._win = None
        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", lambda e: self.entry.focus_set())

    def _redraw(self, e=None):
        self.delete("bg")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        c = theme.C()
        draw_round_rect(self, 1, 1, w - 1, h - 1, (h - 2) / 2,
                        fill=c.FIELD_BG, outline=c.FIELD_BORDER, tags="bg")
        self.tag_lower("bg")
        if self._win is None:
            self._win = self.create_window(0, 0, anchor="nw", window=self.entry)
        self.coords(self._win, 15, (h - 20) / 2)
        self.itemconfigure(self._win, width=w - 30, height=20)

    def get(self):
        return self.entry.get()

    def set(self, text):
        self.entry.delete(0, "end")
        self.entry.insert(0, text)

    def retheme(self):
        c = theme.C()
        self.configure(bg=getattr(c, self._bg_role))
        self.entry.configure(bg=c.FIELD_BG, fg=c.TEXT, insertbackground=c.TEXT)
        self._redraw()


class RoundProgress(tk.Canvas):
    """圆角进度条。"""

    def __init__(self, master, height=10, bg_role="BG"):
        self._bg_role = bg_role
        super().__init__(master, height=height, bg=getattr(theme.C(), bg_role),
                         highlightthickness=0, bd=0)
        self._value = 0.0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, value):
        self._value = max(0.0, min(100.0, value))
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        c = theme.C()
        r = (h - 2) / 2
        draw_round_rect(self, 1, 1, w - 1, h - 1, r, fill=c.TRACK, outline="")
        fill_w = (w - 2) * self._value / 100.0
        if fill_w >= h:
            draw_round_rect(self, 1, 1, 1 + fill_w, h - 1, r, fill=c.ACCENT, outline="")
        elif fill_w > 2:
            self.create_oval(1, 1, 1 + max(fill_w, h - 2), h - 1, fill=c.ACCENT, outline="")

    def retheme(self):
        self.configure(bg=getattr(theme.C(), self._bg_role))
        self._draw()


class CheckPill(tk.Canvas):
    """带文字的圆角复选框（如「全选」）。"""

    BOX = 18

    def __init__(self, master, text, variable, command=None, font=None,
                 width=86, height=28, bg_role="CARD"):
        self._bg_role = bg_role
        super().__init__(master, width=width, height=height,
                         bg=getattr(theme.C(), bg_role), highlightthickness=0, bd=0)
        self._text = text
        self._var = variable
        self._command = command
        self._font = font
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self.configure(cursor=hand_cursor()))

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        c = theme.C()
        b = self.BOX
        y0 = (h - b) / 2
        checked = bool(self._var.get())
        if checked:
            draw_round_rect(self, 1, y0, 1 + b, y0 + b, 5, fill=c.ACCENT, outline="")
            self.create_line(1 + b * 0.26, y0 + b * 0.52, 1 + b * 0.44, y0 + b * 0.72,
                             1 + b * 0.76, y0 + b * 0.3,
                             fill="#FFFFFF", width=2, capstyle="round", joinstyle="round")
        else:
            draw_round_rect(self, 1, y0, 1 + b, y0 + b, 5,
                            fill=c.FIELD_BG, outline=c.CHECK_BORDER)
        self.create_text(b + 9, h / 2, text=self._text, fill=c.TEXT,
                         font=self._font, anchor="w")

    def _toggle(self, e):
        self._var.set(not self._var.get())
        self._draw()
        if self._command:
            self._command()

    def refresh(self):
        self._draw()

    def retheme(self):
        self.configure(bg=getattr(theme.C(), self._bg_role))
        self._draw()


class LogoSwatch(tk.Canvas):
    """应用图标候选卡片：缩略图 + 名称，选中时高亮描边与对勾角标。"""

    def __init__(self, master, text, photo, command=None, font=None,
                 width=88, height=110, bg_role="CARD"):
        self._bg_role = bg_role
        super().__init__(master, width=width, height=height,
                         bg=getattr(theme.C(), bg_role), highlightthickness=0, bd=0)
        self._text = text
        self._photo = photo  # 同时作为引用保活
        self._command = command
        self._font = font
        self._selected = False
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", lambda e: self._command and self._command())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._draw()

    def _on_enter(self, e):
        self._hover = True
        self.configure(cursor=hand_cursor())
        self._draw()

    def _on_leave(self, e):
        self._hover = False
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        c = theme.C()
        sel = self._selected
        draw_round_rect(self, 2, 2, w - 2, h - 2, 14,
                        fill=c.FIELD_BG if (sel or self._hover) else "",
                        outline=c.ACCENT if sel else c.FIELD_BORDER,
                        width=2 if sel else 1)
        if self._photo is not None:
            self.create_image(w / 2, 12 + self._photo.height() / 2,
                              image=self._photo, anchor="center")
        self.create_text(w / 2, h - 16, text=self._text,
                         fill=c.TEXT if sel else c.TEXT_SUB, font=self._font)
        if sel:
            bx, by = w - 26, 8
            self.create_oval(bx, by, bx + 18, by + 18, fill=c.ACCENT,
                             outline="#FFFFFF", width=1.5)
            self.create_line(bx + 5, by + 9.5, bx + 8, by + 13, bx + 13.5, by + 5.5,
                             fill="#FFFFFF", width=2, capstyle="round", joinstyle="round")

    def retheme(self):
        self.configure(bg=getattr(theme.C(), self._bg_role))
        self._draw()


# ---------- 图片选择网格 ----------


class ImagePicker(tk.Frame):
    """可滚动的图片缩略图网格，每张图带复选框，点击切换选中。

    items 元素为 dict: {"label": str, "sub": str, "selected": bool, "photo": PhotoImage|None}
    """

    CELL_W = 132
    CELL_H = 128
    THUMB_W = 116
    THUMB_H = 84
    PAD = 10

    def __init__(self, master, on_change=None, height=150, bg_role="CARD"):
        self._bg_role = bg_role
        super().__init__(master, bg=getattr(theme.C(), bg_role))
        self._on_change = on_change
        self._items = []
        self.canvas = tk.Canvas(self, bg=getattr(theme.C(), bg_role),
                                highlightthickness=0, bd=0, height=height)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw())
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

    # -- 数据接口 --

    def set_items(self, items):
        self._items = items
        self.canvas.yview_moveto(0)
        self._draw()

    def set_thumb(self, index, photo):
        if 0 <= index < len(self._items):
            self._items[index]["photo"] = photo
            self._draw()

    def selected_indices(self):
        return [i for i, it in enumerate(self._items) if it.get("selected")]

    def set_all(self, selected: bool):
        for it in self._items:
            it["selected"] = selected
        self._draw()
        if self._on_change:
            self._on_change()

    # -- 绘制 --

    def _cols(self):
        w = self.canvas.winfo_width()
        return max(1, (w - self.PAD) // (self.CELL_W + self.PAD))

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        if w < 8 or not self._items:
            c.configure(scrollregion=(0, 0, 0, 0))
            return
        pal = theme.C()
        cols = self._cols()
        for i, it in enumerate(self._items):
            row, col = divmod(i, cols)
            x = self.PAD + col * (self.CELL_W + self.PAD)
            y = self.PAD + row * (self.CELL_H + self.PAD)
            sel = it.get("selected")
            # 选中时整格高亮描边
            draw_round_rect(c, x - 4, y - 4, x + self.CELL_W + 4, y + self.CELL_H - 6, 12,
                            fill=pal.FIELD_BG if sel else "",
                            outline=pal.ACCENT if sel else pal.FIELD_BORDER,
                            width=2 if sel else 1)
            tx = x + (self.CELL_W - self.THUMB_W) / 2
            draw_round_rect(c, tx, y, tx + self.THUMB_W, y + self.THUMB_H, 8,
                            fill=pal.THUMB_BG, outline="")
            photo = it.get("photo")
            if photo is not None:
                c.create_image(x + self.CELL_W / 2, y + self.THUMB_H / 2,
                               image=photo, anchor="center")
            else:
                c.create_text(x + self.CELL_W / 2, y + self.THUMB_H / 2,
                              text="加载中…", fill=pal.TEXT_SUB,
                              font=it.get("font_small"))
            # 右上角复选标记
            bx = x + self.CELL_W - 24
            by = y + 6
            if sel:
                c.create_oval(bx, by, bx + 18, by + 18, fill=pal.ACCENT, outline="#FFFFFF", width=1.5)
                c.create_line(bx + 5, by + 9.5, bx + 8, by + 13, bx + 13.5, by + 5.5,
                              fill="#FFFFFF", width=2, capstyle="round", joinstyle="round")
            else:
                c.create_oval(bx, by, bx + 18, by + 18, fill=pal.CARD,
                              outline=pal.CHECK_BORDER, width=1.5)
            c.create_text(x + self.CELL_W / 2, y + self.THUMB_H + 12,
                          text=it.get("label", ""), fill=pal.TEXT, font=it.get("font_small"))
            sub = it.get("sub")
            if sub:
                c.create_text(x + self.CELL_W / 2, y + self.THUMB_H + 28,
                              text=sub, fill=pal.TEXT_SUB, font=it.get("font_small"))
        rows = (len(self._items) + cols - 1) // cols
        total_h = self.PAD + rows * (self.CELL_H + self.PAD)
        c.configure(scrollregion=(0, 0, w, total_h))

    def _on_click(self, e):
        if not self._items:
            return
        cx = self.canvas.canvasx(e.x)
        cy = self.canvas.canvasy(e.y)
        cols = self._cols()
        col = int((cx - self.PAD) // (self.CELL_W + self.PAD))
        row = int((cy - self.PAD) // (self.CELL_H + self.PAD))
        if col < 0 or col >= cols:
            return
        idx = row * cols + col
        if 0 <= idx < len(self._items):
            self._items[idx]["selected"] = not self._items[idx].get("selected")
            self._draw()
            if self._on_change:
                self._on_change()

    def _on_wheel(self, e):
        delta = e.delta
        if sys.platform == "darwin":
            self.canvas.yview_scroll(-delta, "units")
        else:
            self.canvas.yview_scroll(-delta // 120, "units")

    def retheme(self):
        bg = getattr(theme.C(), self._bg_role)
        self.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self._draw()


# ---------- 侧边栏 ----------


class SidebarItem(tk.Canvas):
    """侧边栏条目：圆角高亮 + 简笔图标 + 文字。icon 取值
    youtube / x / instagram / douyin / bilibili / settings。"""

    def __init__(self, master, text, icon, command=None, font=None,
                 width=164, height=40):
        super().__init__(master, width=width, height=height,
                         bg=theme.C().SIDEBAR_BG, highlightthickness=0, bd=0)
        self._text = text
        self._icon = icon
        self._command = command
        self._font = font
        self._active = False
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", lambda e: self._command and self._command())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def set_active(self, active):
        self._active = active
        self._draw()

    def _on_enter(self, e):
        self._hover = True
        self.configure(cursor=hand_cursor())
        self._draw()

    def _on_leave(self, e):
        self._hover = False
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 4 or h < 4:
            return
        c = theme.C()
        if self._active:
            draw_round_rect(self, 2, 2, w - 2, h - 2, 11, fill=c.SIDEBAR_ACTIVE, outline="")
        elif self._hover:
            draw_round_rect(self, 2, 2, w - 2, h - 2, 11, fill=c.SIDEBAR_HOVER, outline="")
        fg = c.SIDEBAR_TEXT_ACTIVE if self._active else c.SIDEBAR_TEXT
        self._draw_icon(16, h / 2, fg)
        self.create_text(40, h / 2 - 1, text=self._text, fill=fg,
                         font=self._font, anchor="w")

    def _draw_icon(self, cx, cy, fg):
        c = theme.C()
        if self._icon == "youtube":
            # 圆角矩形 + 播放三角
            draw_round_rect(self, cx - 9, cy - 7, cx + 9, cy + 7, 5, fill=fg, outline="")
            bg = c.SIDEBAR_ACTIVE if self._active else c.SIDEBAR_BG
            self.create_polygon(cx - 2.5, cy - 3.5, cx - 2.5, cy + 3.5, cx + 4, cy,
                                fill=bg, outline="")
        elif self._icon == "x":
            self.create_line(cx - 7, cy - 7, cx + 7, cy + 7, fill=fg, width=2.4, capstyle="round")
            self.create_line(cx + 7, cy - 7, cx - 7, cy + 7, fill=fg, width=2.4, capstyle="round")
        elif self._icon == "instagram":
            draw_round_rect(self, cx - 8, cy - 8, cx + 8, cy + 8, 6, fill="", outline=fg, width=2)
            self.create_oval(cx - 3.5, cy - 3.5, cx + 3.5, cy + 3.5, fill="", outline=fg, width=2)
            self.create_oval(cx + 3.6, cy - 5.8, cx + 5.8, cy - 3.6, fill=fg, outline="")
        elif self._icon == "douyin":
            # 抖音风格：八分音符（符头 + 符干 + 双旗），单色线条
            self.create_oval(cx - 8, cy + 2, cx - 2, cy + 8, fill=fg, outline="")
            self.create_line(cx - 2.6, cy + 6, cx - 2.6, cy - 8, fill=fg,
                             width=2.2, capstyle="round")
            self.create_line(cx - 2.6, cy - 8, cx + 7, cy - 5, fill=fg,
                             width=2.2, capstyle="round")
            self.create_line(cx - 2.6, cy - 3.5, cx + 7, cy - 0.5, fill=fg,
                             width=2.2, capstyle="round")
        elif self._icon == "bilibili":
            # 哔哩哔哩风格：电视机头（双天线 + 机身 + 双眼），单色线条
            draw_round_rect(self, cx - 9, cy - 4, cx + 9, cy + 8, 4,
                            fill="", outline=fg, width=2)
            self.create_line(cx - 6, cy - 9, cx - 2.5, cy - 4, fill=fg,
                             width=2, capstyle="round")
            self.create_line(cx + 6, cy - 9, cx + 2.5, cy - 4, fill=fg,
                             width=2, capstyle="round")
            self.create_oval(cx - 5.5, cy + 0.5, cx - 2.5, cy + 3.5, fill=fg, outline="")
            self.create_oval(cx + 2.5, cy + 0.5, cx + 5.5, cy + 3.5, fill=fg, outline="")
        elif self._icon == "settings":
            # 齿轮：外圈 + 内孔 + 齿
            import math
            self.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill="", outline=fg, width=2)
            self.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill=fg, outline="")
            for k in range(8):
                ang = k * math.pi / 4
                x1 = cx + 6 * math.cos(ang)
                y1 = cy + 6 * math.sin(ang)
                x2 = cx + 9 * math.cos(ang)
                y2 = cy + 9 * math.sin(ang)
                self.create_line(x1, y1, x2, y2, fill=fg, width=2, capstyle="round")

    def retheme(self):
        self.configure(bg=theme.C().SIDEBAR_BG)
        self._draw()
