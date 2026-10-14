"""Liquid Glass 自绘控件库（主题感知版）。

所有控件在绘制时从 theme.C() 读取当前调色板；普通 tk.Frame/tk.Label 使用
TFrame/TLabel 包装并记录配色角色。切换主题后调用 apply_theme_tree(root)
即可让整棵控件树换肤重绘，无需重建界面。
"""

import os
import sys
import tkinter as tk

from . import i18n, theme


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


# 需要键盘激活的控件（按钮/复选/下拉/侧边栏/色板）都绑这三个键。
# Canvas 上的 <space> 不会当成字符输入，所以可以安全地当「确认键」用。
ACTIVATION_KEYS = ("<Return>", "<KP_Enter>", "<space>")


def bind_activation(widget, handler):
    """把 Return / 小键盘回车 / 空格绑到激活处理函数上。"""
    for sequence in ACTIVATION_KEYS:
        widget.bind(sequence, handler)


def draw_focus_ring(widget, w, h, radius, color):
    """键盘焦点环。

    颜色刻意用控件自身的**前景色**：前景色与填充色本来就是对比色，所以主色按钮
    （白字蓝底）和次级按钮（深字浅底）都能看清，不必再引入主题角色。
    """
    if w < 8 or h < 8:
        return
    draw_round_rect(widget, 2, 2, w - 3, h - 3, radius, fill="", outline=color, width=2)


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

    def sync_body(self):
        """按当前画布尺寸同步内部 body 的高度（幂等）。

        只在 <Configure> 里更新 body 是不够的：set_height 用的高度可能与画布当前
        高度相同（缓存命中），那样不会产生 Configure，body 就停在旧高度把内容裁掉
        （设置页在放大字号后实测 63 vs 需要 91）。调用方用这个入口确定性地同步。
        """
        self._redraw()

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
        self._focused = False
        # takefocus 让 Tab 能走到这里；禁用时在 configure_state 里关掉
        self.configure(takefocus=1)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        bind_activation(self, self._on_key)

    def _colors(self):
        c = theme.C()
        if self._primary:
            if self._state == "disabled":
                return c.ACCENT_DISABLED, "#FFFFFF"
            # 用 ACCENT_FILL 而不是 ACCENT：白字压在亮蓝上对比度只有 3.65
            return (c.ACCENT_FILL_HOVER if self._hover else c.ACCENT_FILL), "#FFFFFF"
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
        if self._focused:
            draw_focus_ring(self, w, h, (h - 2) / 2, fg)

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

    def _on_focus_in(self, e=None):
        self._focused = True
        self._draw()

    def _on_focus_out(self, e=None):
        self._focused = False
        self._draw()

    def _on_key(self, e=None):
        if self._state == "normal" and self._command:
            self._command()
        return "break"

    def set_text(self, text):
        self._text = text
        self._draw()

    def configure_state(self, state):
        self._state = state
        # 禁用控件不该被 Tab 选中，否则键盘用户会停在点不动的按钮上
        self.configure(takefocus=1 if state == "normal" else 0)
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
        self._focused = False
        self.configure(takefocus=1)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        for sequence in ("<Left>", "<Up>"):
            self.bind(sequence, lambda e: self._step(-1))
        for sequence in ("<Right>", "<Down>"):
            self.bind(sequence, lambda e: self._step(1))

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
        if self._focused:
            draw_focus_ring(self, w, h, (h - 2) / 2, c.ACCENT)

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

    def _on_focus_in(self, e=None):
        self._focused = True
        self._draw()

    def _on_focus_out(self, e=None):
        self._focused = False
        self._draw()

    def _step(self, delta):
        """左右/上下方向键在分段之间移动，等同于点击该段。"""
        values = [value for _label, value in self._segments]
        if not values:
            return "break"
        try:
            index = values.index(self._var.get())
        except ValueError:
            index = 0
        target = values[min(len(values) - 1, max(0, index + delta))]
        if target != self._var.get():
            self._var.set(target)
            self._draw()
            if self._command:
                self._command()
        return "break"

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
        self._focused = False
        self.configure(takefocus=1)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._popup)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        bind_activation(self, self._on_key)
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
        if self._focused:
            draw_focus_ring(self, w, h, (h - 2) / 2, c.ACCENT)

    def set_values(self, values):
        self._values = list(values)
        self._draw()

    def set_enabled(self, enabled):
        self._enabled = enabled
        if not enabled:
            self._focused = False
        self.configure(takefocus=1 if enabled else 0)
        self._draw()

    def _on_focus_in(self, e=None):
        self._focused = True
        self._draw()

    def _on_focus_out(self, e=None):
        self._focused = False
        self._draw()

    def _on_key(self, e=None):
        """键盘打开下拉：tk.Menu 自己支持方向键与回车选择、Esc 关闭。"""
        self._popup(None)
        return "break"

    def _popup(self, e=None):
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
            draw_round_rect(self, 1, 1, 1 + fill_w, h - 1, r, fill=c.PROGRESS_FILL,
                                outline="")
        elif fill_w > 2:
            self.create_oval(1, 1, 1 + max(fill_w, h - 2), h - 1, fill=c.PROGRESS_FILL,
                             outline="")

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
        self._focused = False
        self.configure(takefocus=1)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self.configure(cursor=hand_cursor()))
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        bind_activation(self, self._on_key)

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
        if self._focused:
            draw_focus_ring(self, w, h, 6, c.ACCENT)

    def set_checked(self, value):
        """程序化勾选/取消。

        直接改 StringVar/BooleanVar 不会触发重绘（_draw 只挂在点击与
        <Configure> 上），所以「全选」这类批量操作必须走这里。
        """
        self._var.set(bool(value))
        self._draw()

    def _toggle(self, e=None):
        self._var.set(not self._var.get())
        self._draw()
        if self._command:
            self._command()

    def _on_focus_in(self, e=None):
        self._focused = True
        self._draw()

    def _on_focus_out(self, e=None):
        self._focused = False
        self._draw()

    def _on_key(self, e=None):
        self._toggle()
        return "break"

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
        self._focused = False
        self.configure(takefocus=1)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", lambda e: self._command and self._command())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        bind_activation(self, self._on_key)

    def _on_focus_in(self, e=None):
        self._focused = True
        self._draw()

    def _on_focus_out(self, e=None):
        self._focused = False
        self._draw()

    def _on_key(self, e=None):
        if self._command:
            self._command()
        return "break"

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

    ``on_change`` 是**无参**回调，选中态变化时调用（鼠标点击、``set_all``、
    空格/回车勾选都走它）。
    """

    CELL_W = 132
    CELL_H = 128
    THUMB_W = 116
    THUMB_H = 84
    OUTER_PAD = 10
    GAP_X = 10
    GAP_Y = 16
    VIEWPORT_RADIUS = 14
    CARD_RADIUS = 14
    THUMB_RADIUS = 10

    def __init__(self, master, on_change=None, height=150, bg_role="CARD"):
        self._bg_role = bg_role
        super().__init__(master, bg=getattr(theme.C(), bg_role))
        self._on_change = on_change
        self._items = []
        self._scrollbar_visible = False
        self._pointer_over_canvas = False
        # 缩略图是画布上的图形而不是子控件，所以键盘导航靠自己维护一个游标
        self._cursor = None
        self._focused = False
        self.canvas = tk.Canvas(self, bg=getattr(theme.C(), bg_role),
                                highlightthickness=0, bd=0, height=height,
                                takefocus=1)
        self.scrollbar = tk.Scrollbar(self, orient="vertical",
                                      command=self.canvas.yview, takefocus=0)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw())
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", self._on_wheel)
        self.canvas.bind("<Button-5>", self._on_wheel)
        self.canvas.bind("<Enter>", self._on_pointer_enter)
        self.canvas.bind("<Leave>", self._on_pointer_leave)
        self.canvas.bind("<Unmap>", self._on_pointer_leave)
        self.canvas.bind("<FocusIn>", self._on_focus_in)
        self.canvas.bind("<FocusOut>", self._on_focus_out)
        self.canvas.bind("<Left>", lambda e: self._move_cursor(-1))
        self.canvas.bind("<Right>", lambda e: self._move_cursor(1))
        self.canvas.bind("<Up>", lambda e: self._move_cursor_row(-1))
        self.canvas.bind("<Down>", lambda e: self._move_cursor_row(1))
        self.canvas.bind("<Home>", lambda e: self._move_cursor_to(0))
        self.canvas.bind("<End>", lambda e: self._move_cursor_to(len(self._items) - 1))
        bind_activation(self.canvas, self._toggle_cursor_item)

    # -- 数据接口 --

    def set_items(self, items):
        self._items = items
        self._cursor = 0 if items else None
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
        return self._cols_for_width(self.canvas.winfo_width())

    def _cols_for_width(self, width):
        return max(1, (width - 2 * self.OUTER_PAD + self.GAP_X)
                   // (self.CELL_W + self.GAP_X))

    def _content_height(self, cols):
        rows = (len(self._items) + cols - 1) // cols
        return (2 * self.OUTER_PAD + rows * self.CELL_H
                + max(0, rows - 1) * self.GAP_Y)

    def _set_scrollbar_visible(self, visible):
        if self._scrollbar_visible == visible:
            return False
        self._scrollbar_visible = visible
        if visible:
            self.scrollbar.pack(side="right", fill="y", padx=(4, 0))
        else:
            self.scrollbar.pack_forget()
        return True

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        if w < 8 or not self._items:
            self._set_scrollbar_visible(False)
            c.configure(scrollregion=(0, 0, 0, 0))
            return
        pal = theme.C()
        h = c.winfo_height()
        if h >= 8:
            draw_round_rect(c, 1, 1, w - 3, h - 3, self.VIEWPORT_RADIUS,
                            fill=pal.CARD, outline=pal.FIELD_BORDER, width=1,
                            tags="viewport")
            c.tag_lower("viewport")
        cols = self._cols()
        total_h = self._content_height(cols)
        if self._set_scrollbar_visible(total_h > c.winfo_height()):
            self.after_idle(self._draw)
            return
        for i, it in enumerate(self._items):
            row, col = divmod(i, cols)
            x = self.OUTER_PAD + col * (self.CELL_W + self.GAP_X)
            y = self.OUTER_PAD + row * (self.CELL_H + self.GAP_Y)
            sel = it.get("selected")
            # 选中时整格高亮描边
            draw_round_rect(c, x - 2, y - 4, x + self.CELL_W + 2, y + self.CELL_H - 6,
                            self.CARD_RADIUS,
                            fill=pal.FIELD_BG if sel else "",
                            outline=pal.ACCENT if sel else pal.FIELD_BORDER,
                            width=2 if sel else 1)
            tx = x + (self.CELL_W - self.THUMB_W) / 2
            draw_round_rect(c, tx, y, tx + self.THUMB_W, y + self.THUMB_H,
                            self.THUMB_RADIUS,
                            fill=pal.THUMB_BG, outline="")
            photo = it.get("photo")
            if photo is not None:
                c.create_image(x + self.CELL_W / 2, y + self.THUMB_H / 2,
                               image=photo, anchor="center")
            else:
                c.create_text(x + self.CELL_W / 2, y + self.THUMB_H / 2,
                              text=i18n.tr("loading"), fill=pal.TEXT_SUB,
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
        # 键盘游标：只在获得焦点时画，用主题文字色（与选中的强调色描边区分开）
        index = self.cursor_index() if self._focused else None
        if index is not None:
            draw_round_rect(c, *self._cursor_ring_bounds(index, cols),
                            self.CARD_RADIUS + 2, fill="", outline=pal.TEXT, width=2,
                            tags="cursor")
            c.create_text(x + self.CELL_W / 2, y + self.THUMB_H + 12,
                          text=it.get("label", ""), fill=pal.TEXT, font=it.get("font_small"))
            sub = it.get("sub")
            if sub:
                c.create_text(x + self.CELL_W / 2, y + self.THUMB_H + 28,
                              text=sub, fill=pal.TEXT_SUB, font=it.get("font_small"))
        c.configure(scrollregion=(0, 0, w, total_h))

    def _cursor_ring_bounds(self, index, cols):
        row, col = divmod(index, cols)
        x = self.OUTER_PAD + col * (self.CELL_W + self.GAP_X)
        y = self.OUTER_PAD + row * (self.CELL_H + self.GAP_Y)
        return x - 4, y - 6, x + self.CELL_W + 4, y + self.CELL_H - 4

    def _on_click(self, e):
        if not self._items:
            return
        cx = self.canvas.canvasx(e.x)
        cy = self.canvas.canvasy(e.y)
        cols = self._cols()
        col = int((cx - self.OUTER_PAD) // (self.CELL_W + self.GAP_X))
        row = int((cy - self.OUTER_PAD) // (self.CELL_H + self.GAP_Y))
        if col < 0 or col >= cols:
            return
        cell_x = self.OUTER_PAD + col * (self.CELL_W + self.GAP_X)
        cell_y = self.OUTER_PAD + row * (self.CELL_H + self.GAP_Y)
        if not (cell_x <= cx <= cell_x + self.CELL_W
                and cell_y <= cy <= cell_y + self.CELL_H):
            return
        idx = row * cols + col
        if 0 <= idx < len(self._items):
            # 点过之后游标落在这里，接着按方向键/空格就顺理成章
            self._cursor = idx
            self._items[idx]["selected"] = not self._items[idx].get("selected")
            self._draw()
            if self._on_change:
                self._on_change()

    # -- 键盘导航 --

    def cursor_index(self):
        """键盘游标所在的下标；没有可选内容时为 None。"""
        if not self._items or self._cursor is None:
            return None
        return max(0, min(len(self._items) - 1, self._cursor))

    def _on_focus_in(self, _event=None):
        self._focused = True
        if self._cursor is None and self._items:
            self._cursor = 0
        self._draw()

    def _on_focus_out(self, _event=None):
        self._focused = False
        self._draw()

    def _move_cursor(self, delta):
        if not self._items:
            return "break"
        start = self.cursor_index()
        start = 0 if start is None else start
        self._move_cursor_to(start + delta)
        return "break"

    def _move_cursor_row(self, delta):
        """上下移动一整行；行内列数会随窗口宽度变化，所以实时算。"""
        if not self._items:
            return "break"
        cols = self._cols()
        start = self.cursor_index()
        start = 0 if start is None else start
        self._move_cursor_to(start + delta * cols)
        return "break"

    def _move_cursor_to(self, index):
        if not self._items:
            return "break"
        self._cursor = max(0, min(len(self._items) - 1, index))
        self._draw()
        self._ensure_cursor_visible()
        return "break"

    def _toggle_cursor_item(self, _event=None):
        """空格/回车：勾选或取消游标所在的那张图。"""
        index = self.cursor_index()
        if index is None:
            return "break"
        self._items[index]["selected"] = not self._items[index].get("selected")
        self._draw()
        if self._on_change:
            self._on_change()
        return "break"

    def _ensure_cursor_visible(self):
        """把游标滚进可视区域，否则键盘选了却看不见。"""
        index = self.cursor_index()
        if index is None:
            return
        canvas = self.canvas
        cols = self._cols()
        total = self._content_height(cols)
        height = canvas.winfo_height()
        if total <= 0 or height <= 0:
            return
        row = index // cols
        cell_y = self.OUTER_PAD + row * (self.CELL_H + self.GAP_Y)
        top = canvas.canvasy(0)
        if cell_y < top:
            canvas.yview_moveto(max(0.0, (cell_y - self.OUTER_PAD) / total))
        elif cell_y + self.CELL_H > top + height:
            canvas.yview_moveto(max(0.0, min(
                1.0, (cell_y + self.CELL_H + self.OUTER_PAD - height) / total)))

    def _on_pointer_enter(self, _event):
        self._pointer_over_canvas = True

    def _on_pointer_leave(self, _event):
        self._pointer_over_canvas = False

    def accepts_native_scroll(self):
        return self._pointer_over_canvas and self._scrollbar_visible

    def scroll_by_delta(self, delta):
        try:
            delta = float(delta)
        except (TypeError, ValueError):
            return
        if not delta:
            return
        if sys.platform == "darwin":
            units = -int(delta)
            if not units:
                units = -1 if delta > 0 else 1
            units = max(-10, min(10, units))
        else:
            units = -1 if delta > 0 else 1
        self.canvas.yview_scroll(units, "units")

    def scroll_with_wheel(self, event):
        button = getattr(event, "num", None)
        if button in (4, 5):
            self.canvas.yview_scroll(-1 if button == 4 else 1, "units")
            return
        self.scroll_by_delta(getattr(event, "delta", 0))

    def _on_wheel(self, event):
        self.scroll_with_wheel(event)
        return "break"

    def retheme(self):
        bg = getattr(theme.C(), self._bg_role)
        self.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self._draw()


# ---------- 侧边栏 ----------


class SidebarItem(tk.Canvas):
    """侧边栏条目：圆角高亮 + 品牌色平台 Logo + 文字。icon 取值
    youtube / x / instagram / douyin / bilibili / weibo / queue / history / settings。

    未知取值不会报错，只是不画图标；新增入口时记得在这里补一个分支。
    """

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
        self._focused = False
        self.configure(takefocus=1)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", lambda e: self._command and self._command())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        bind_activation(self, self._on_key)

    def _on_focus_in(self, e=None):
        self._focused = True
        self._draw()

    def _on_focus_out(self, e=None):
        self._focused = False
        self._draw()

    def _on_key(self, e=None):
        if self._command:
            self._command()
        return "break"

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
            # YouTube 官方红色播放标志。
            draw_round_rect(self, cx - 9, cy - 7, cx + 9, cy + 7, 5,
                            fill="#FF0000", outline="")
            self.create_polygon(cx - 2.5, cy - 3.5, cx - 2.5, cy + 3.5,
                                cx + 4, cy, fill="#FFFFFF", outline="")
        elif self._icon == "x":
            # X 官方黑/白色交叉标志。
            logo = "#FFFFFF" if theme.is_dark() else "#111111"
            self.create_line(cx - 7, cy - 7, cx + 7, cy + 7,
                             fill=logo, width=2.6, capstyle="round")
            self.create_line(cx + 7, cy - 7, cx - 7, cy + 7,
                             fill=logo, width=2.6, capstyle="round")
        elif self._icon == "instagram":
            # Instagram 官方相机标志，使用品牌洋红色底和白色细节。
            draw_round_rect(self, cx - 8, cy - 8, cx + 8, cy + 8, 5,
                            fill="#E4405F", outline="")
            self.create_oval(cx - 4, cy - 4, cx + 4, cy + 4,
                             fill="", outline="#FFFFFF", width=1.8)
            self.create_oval(cx + 3.2, cy - 5.2, cx + 5.4, cy - 3.0,
                             fill="#FFFFFF", outline="")
        elif self._icon == "douyin":
            # 抖音/TikTok 官方音符的青、粉错位效果。
            for offset, color in ((-1.5, "#25F4EE"), (1.5, "#FE2C55")):
                self.create_oval(cx - 8 + offset, cy + 2,
                                 cx - 2 + offset, cy + 8,
                                 fill=color, outline="")
                self.create_line(cx - 2.6 + offset, cy + 6,
                                 cx - 2.6 + offset, cy - 8,
                                 fill=color, width=2.2, capstyle="round")
                self.create_line(cx - 2.6 + offset, cy - 8,
                                 cx + 7 + offset, cy - 5,
                                 fill=color, width=2.2, capstyle="round")
            self.create_oval(cx - 8, cy + 2, cx - 2, cy + 8,
                             fill=fg, outline="")
            self.create_line(cx - 2.6, cy + 6, cx - 2.6, cy - 8,
                             fill=fg, width=2.2, capstyle="round")
            self.create_line(cx - 2.6, cy - 8, cx + 7, cy - 5,
                             fill=fg, width=2.2, capstyle="round")
        elif self._icon == "bilibili":
            # 哔哩哔哩官方蓝色电视机头标志。
            blue = "#00AEEC"
            draw_round_rect(self, cx - 9, cy - 4, cx + 9, cy + 8, 4,
                            fill=blue, outline="")
            self.create_line(cx - 6, cy - 9, cx - 2.5, cy - 4,
                             fill=blue, width=2, capstyle="round")
            self.create_line(cx + 6, cy - 9, cx + 2.5, cy - 4,
                             fill=blue, width=2, capstyle="round")
            self.create_oval(cx - 5.5, cy + 0.5, cx - 2.5, cy + 3.5,
                             fill="#FFFFFF", outline="")
            self.create_oval(cx + 2.5, cy + 0.5, cx + 5.5, cy + 3.5,
                             fill="#FFFFFF", outline="")
        elif self._icon == "weibo":
            # 微博官方红色眼睛/气泡标志。
            red = "#E6162D"
            self.create_oval(cx - 8, cy - 6, cx + 8, cy + 6,
                             fill=red, outline="")
            self.create_polygon(cx + 4, cy + 4, cx + 8, cy + 8,
                                cx + 1, cy + 5, fill=red, outline="")
            self.create_oval(cx - 4.5, cy - 2.5, cx + 4.5, cy + 2.5,
                             fill="#FFFFFF", outline="")
            self.create_oval(cx - 1.5, cy - 2.5, cx + 2.5, cy + 2.5,
                             fill=red, outline="")
        elif self._icon == "generic":
            # 通用面板：一颗地球（表示「任何站点」）
            self.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, outline=fg, width=2)
            self.create_line(cx - 9, cy, cx + 9, cy, fill=fg, width=1)
            self.create_oval(cx - 4, cy - 9, cx + 4, cy + 9, outline=fg, width=1)
        elif self._icon == "queue":
            # 列表：三条横线，每行左侧一个小方点
            for offset in (-5, 0, 5):
                self.create_oval(cx - 9, cy + offset - 1.6, cx - 5.8, cy + offset + 1.6,
                                 fill=fg, outline="")
                self.create_line(cx - 2.5, cy + offset, cx + 8, cy + offset, fill=fg,
                                 width=2, capstyle="round")
        elif self._icon == "history":
            # 时钟：外圈 + 时针分针，表示「历史」
            import math
            self.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, fill="", outline=fg, width=2)
            for dx, dy in ((0, -4), (4, 2)):
                self.create_line(cx, cy, cx + dx, cy + dy, fill=fg, width=2, capstyle="round")
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


def _pack_wrapped_hint(master, app, text, bg_role="BG", pady=(0, 8)):
    """按控件宽度自动换行的说明文字（TLabel 本身不会自动折行）。"""
    label = TLabel(master, text=text, font=app.f_small, bg_role=bg_role,
                   fg_role="TEXT_SUB", anchor="w", justify="left", wraplength=1)

    def update_wraplength(event):
        wraplength = max(1, event.width - 2)
        if int(label.cget("wraplength")) != wraplength:
            label.configure(wraplength=wraplength)

    label.bind("<Configure>", update_wraplength)
    label.pack(fill="x", pady=pady)
    return label


def _check_pill_width(font, text, extra=44):
    """CheckPill 的文字是 Canvas 自绘的，宽度不够会被直接裁掉。

    英文（以及其他语言）文案常比中文长一半以上，所以按实际字体测量，
    而不是写死像素——写死过一次 340，英文直接被截成
    "Skip images that were already downloade"。
    """
    try:
        measured = font.measure(text)
    except Exception:
        return 300
    return max(120, measured + extra)  # extra = 复选框 18px + 左右内边距


def _segmented_width(font, labels, margin=18):
    """SegmentedControl 把总宽平分给每段，必须保证最长的一段也放得下。

    写死宽度在英文下会截断（"Video (with audio)" 曾显示成 "/ideo (with audio)"）。
    """
    labels = list(labels)
    try:
        widest = max(font.measure(label) for label in labels)
    except Exception:
        return 260
    return int(widest + margin) * len(labels) + 6
