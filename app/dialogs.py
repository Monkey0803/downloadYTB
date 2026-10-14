"""弹窗与选择器：首次说明、长文本详情、字幕语言、音质选择。

从 gui.py 拆出来（ROADMAP 第 13 项）。这些弹窗只依赖 tkinter、主题与文案，不碰
下载状态；界面模块继续 import 这些类，所以 `gui.EntryPicker` 这类引用与既有测试
都不用改。
"""

import tkinter as tk

from . import core, i18n, theme
from .widgets import (CheckPill, PillButton, TFrame, TLabel, _check_pill_width,
                      _pack_wrapped_hint, ellipsize)


class WelcomeDialog(tk.Toplevel):
    """首次使用的说明（#9）。

    新用户面对一个空输入框，并不知道「解析 → 选清晰度 → 开始下载」这条流程，
    也不知道图片类平台常需要登录态。做成一次性弹窗而不是常驻卡片：面板的卡片
    高度是写死的，常驻卡片会把「开始下载」挤出可视区域。
    """

    WIDTH = 560

    def __init__(self, master, app, on_dismiss=None):
        super().__init__(master)
        self.app = app
        self._on_dismiss = on_dismiss
        self._fired_dismiss = False
        self.title(i18n.tr("welcome_title"))
        self.configure(bg=theme.C().BG)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)

        outer = TFrame(self, bg_role="BG")
        outer.pack(fill="both", expand=True, padx=20, pady=18)
        TLabel(outer, text=i18n.tr("welcome_title"), font=app.f_section,
               bg_role="BG").pack(anchor="w")
        steps = TFrame(outer, bg_role="BG")
        steps.pack(fill="x", pady=(10, 6))
        for key in ("welcome_step_1", "welcome_step_2", "welcome_step_3"):
            TLabel(steps, text=i18n.tr(key), font=app.f_body, bg_role="BG",
                   anchor="w", justify="left", wraplength=self.WIDTH - 60).pack(
                anchor="w", fill="x", pady=2)
        _pack_wrapped_hint(outer, app, i18n.tr("welcome_tip"), bg_role="BG",
                           pady=(4, 12))

        buttons = TFrame(outer, bg_role="BG")
        buttons.pack(fill="x")
        PillButton(buttons, i18n.tr("settings"), command=lambda: self._finish(True),
                   font=app.f_btn, width=110, bg_role="BG").pack(side="left")
        PillButton(buttons, i18n.tr("welcome_go"), command=self._finish, primary=True,
                   font=app.f_btn, width=110, bg_role="BG").pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._finish)
        self._center_on(master)

    def _center_on(self, master):
        try:
            self.update_idletasks()
            x = master.winfo_rootx() + max(20, (master.winfo_width() - self.WIDTH) // 2)
            y = master.winfo_rooty() + 90
            self.geometry(f"{self.WIDTH}+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _finish(self, open_settings=False):
        """无论「开始使用」「打开设置」还是直接关窗口，都算看过了。"""
        if not self._fired_dismiss:
            self._fired_dismiss = True
            if callable(self._on_dismiss):
                self._on_dismiss()
        if open_settings:
            self.app.show_page("settings")
        self.destroy()

    def show(self):
        self.grab_set()
        self.wait_window(self)


class TextDetailDialog(tk.Toplevel):
    """完整显示被状态栏截断的文本（#3）。

    状态栏是单行的，长错误（例如 yt-dlp 的原始消息）会被省略号截断，完整内容
    此前只能通过「复制诊断信息」拿到——普通用户不知道要去那里找。
    """

    WIDTH = 620

    def __init__(self, master, app, title, body):
        super().__init__(master)
        self._body = body
        self.title(title)
        self.configure(bg=theme.C().BG)
        self.transient(master.winfo_toplevel())

        outer = TFrame(self, bg_role="BG")
        outer.pack(fill="both", expand=True, padx=16, pady=14)
        TLabel(outer, text=title, font=app.f_section, bg_role="BG").pack(anchor="w")

        holder = TFrame(outer, bg_role="BG")
        holder.pack(fill="both", expand=True, pady=(8, 10))
        # wraplength 让长文本换行显示，而不是再截断一次
        TLabel(holder, text=body, font=app.f_sub, bg_role="BG", fg_role="TEXT",
               justify="left", anchor="w", wraplength=self.WIDTH - 70).pack(
            anchor="w", fill="x")

        self.hint_label = TLabel(outer, text="", font=app.f_small, bg_role="BG",
                                 fg_role="TEXT_SUB")
        self.hint_label.pack(anchor="w")

        buttons = TFrame(outer, bg_role="BG")
        buttons.pack(fill="x", pady=(8, 0))
        PillButton(buttons, i18n.tr("copy"), command=self._copy, font=app.f_btn,
                   width=90, bg_role="BG").pack(side="left")
        PillButton(buttons, i18n.tr("close"), command=self._close, primary=True,
                   font=app.f_btn, width=90, bg_role="BG").pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._close)
        self._center_on(master)

    def _center_on(self, master):
        try:
            self.update_idletasks()
            x = master.winfo_rootx() + max(20, (master.winfo_width() - self.WIDTH) // 2)
            y = master.winfo_rooty() + 80
            self.geometry(f"{self.WIDTH}x+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _copy(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self._body)
        except tk.TclError:
            return
        self.hint_label.configure(text=i18n.tr("copied"))

    def _close(self):
        self.destroy()

    def show(self):
        self.grab_set()
        self.wait_window(self)


# 常用字幕语言：代码 + 母语名（母语名按惯例不翻译，也省掉一整套 i18n 键）
SUBTITLE_LANGUAGES = (
    ("zh-Hans", "中文（简体）"), ("zh-Hant", "中文（繁體）"), ("en", "English"),
    ("ja", "日本語"), ("ko", "한국어"), ("es", "Español"), ("fr", "Français"),
    ("de", "Deutsch"), ("ru", "Русский"), ("pt", "Português"), ("it", "Italiano"),
    ("ar", "العربية"), ("hi", "हिन्दी"), ("th", "ไทย"), ("vi", "Tiếng Việt"),
    ("id", "Bahasa Indonesia"),
)


class SubtitleLanguagePicker(tk.Toplevel):
    """常用字幕语言勾选（#4）。

    原来只能手写 ``zh-Hans,zh-Hant,en`` 这种 yt-dlp 语言代码，普通用户不知道
    该写什么。输入框仍然保留，用于列表之外的冷门语言。
    """

    WIDTH = 520
    HEIGHT = 470

    def __init__(self, master, app, current=""):
        super().__init__(master)
        self.app = app
        self.result = None
        self._pills = []
        known = {code for code, _name in SUBTITLE_LANGUAGES}
        existing = [part.strip() for part in str(current or "").split(",") if part.strip()]
        chosen = {code for code in existing if code in known}
        self.extra = [code for code in existing if code not in known]

        self.title(i18n.tr("subtitle_pick_title"))
        self.configure(bg=theme.C().BG)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)

        outer = TFrame(self, bg_role="BG")
        outer.pack(fill="both", expand=True, padx=14, pady=12)
        TLabel(outer, text=i18n.tr("subtitle_pick_title"), font=app.f_section,
               bg_role="BG").pack(anchor="w")
        _pack_wrapped_hint(outer, app, i18n.tr("subtitle_pick_hint"),
                           bg_role="BG", pady=(6, 8))

        holder = TFrame(outer, bg_role="BG")
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, bg=theme.C().BG, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        body = TFrame(canvas, bg_role="BG")
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

        for code, name in SUBTITLE_LANGUAGES:
            label = f"{name} · {code}"
            pill = CheckPill(body, label, tk.BooleanVar(value=code in chosen),
                             font=app.f_btn, bg_role="BG",
                             width=_check_pill_width(app.f_btn, label))
            pill.pack(anchor="w", pady=2)
            self._pills.append((code, pill))

        buttons = TFrame(outer, bg_role="BG")
        buttons.pack(fill="x", pady=(10, 0))
        PillButton(buttons, i18n.tr("select_all"), command=lambda: self._select_all(True),
                   font=app.f_btn, width=96, bg_role="BG").pack(side="left")
        PillButton(buttons, i18n.tr("pick_none"), command=lambda: self._select_all(False),
                   font=app.f_btn, width=96, bg_role="BG").pack(side="left", padx=(8, 0))
        PillButton(buttons, i18n.tr("confirm"), command=self._confirm, primary=True,
                   font=app.f_btn, width=96, bg_role="BG").pack(side="right")
        PillButton(buttons, i18n.tr("cancel"), command=self._cancel,
                   font=app.f_btn, width=96, bg_role="BG").pack(side="right", padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._center_on(master)

    def _center_on(self, master):
        try:
            self.update_idletasks()
            x = master.winfo_rootx() + max(20, (master.winfo_width() - self.WIDTH) // 2)
            y = master.winfo_rooty() + 70
            self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _select_all(self, value):
        for _code, pill in self._pills:
            pill.set_checked(value)

    def _collect(self):
        """按列表顺序返回勾选的代码，并补上列表外的自定义代码。"""
        codes = [code for code, pill in self._pills if bool(pill._var.get())]
        return codes + self.extra

    def _confirm(self):
        self.result = ",".join(self._collect())
        self._close()

    def _cancel(self):
        self.result = None
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self):
        self.grab_set()
        self.wait_window(self)
        return self.result


class EntryPicker(tk.Toplevel):
    """播放列表条目批量选择弹窗（#2）。

    探针一次只取 PLAYLIST_PREVIEW_LIMIT 条（限量扁平抽取才够快），列表不够用时
    由「加载更多」按 core.PLAYLIST_LOAD_STEP 逐次放大上限。弹窗自己不发请求：
    它只调用外部传入的 on_request_more，由调用方在后台线程探测后再回调
    set_items——这样弹窗仍然可以在单测里直接驱动，不会被模态 wait_window 挂住。

    刻意把「构建」「取值」「确认」拆开，show() 之外都能在单测里直接调用。
    """

    WIDTH = 620
    HEIGHT = 540
    ROW_PADY = 2

    def __init__(self, master, app, titles, selected=None, truncated=False,
                 on_request_more=None):
        super().__init__(master)
        self.app = app
        self._titles = list(titles)
        self._pills = []
        self.result = None
        self._truncated = bool(truncated)
        self._on_request_more = on_request_more
        self._loading = False
        chosen = {int(i) for i in (selected or []) if str(i).strip().isdigit()}

        self.title(i18n.tr("pick_entries_title"))
        self.configure(bg=theme.C().BG)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)

        outer = TFrame(self, bg_role="BG")
        outer.pack(fill="both", expand=True, padx=14, pady=12)
        TLabel(outer, text=i18n.tr("pick_entries_title"), font=app.f_section,
               bg_role="BG").pack(anchor="w")
        self.hint_label = TLabel(
            outer, text="", font=app.f_small, bg_role="BG", fg_role="TEXT_SUB",
            anchor="w", justify="left", wraplength=self.WIDTH - 30)
        self.hint_label.pack(anchor="w", fill="x", pady=(6, 8))
        self._refresh_hint()

        holder = TFrame(outer, bg_role="BG")
        holder.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(holder, bg=theme.C().BG, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(holder, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=bar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self._body = TFrame(self._canvas, bg_role="BG")
        window = self._canvas.create_window((0, 0), window=self._body, anchor="nw")
        self._body.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>", lambda e: self._canvas.itemconfigure(window, width=e.width))
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind(sequence, self._on_wheel, add="+")
            self._body.bind(sequence, self._on_wheel, add="+")
        self._build_pills(chosen)

        buttons = TFrame(outer, bg_role="BG")
        buttons.pack(fill="x", pady=(10, 0))
        PillButton(buttons, i18n.tr("select_all"), command=lambda: self._select_all(True),
                   font=app.f_btn, width=96, bg_role="BG").pack(side="left")
        PillButton(buttons, i18n.tr("pick_none"), command=lambda: self._select_all(False),
                   font=app.f_btn, width=96, bg_role="BG").pack(side="left", padx=(8, 0))
        self.more_btn = PillButton(buttons, i18n.tr("pick_load_more"),
                                   command=self._on_load_more, font=app.f_btn,
                                   width=104, bg_role="BG")
        PillButton(buttons, i18n.tr("confirm"), command=self._confirm, primary=True,
                   font=app.f_btn, width=96, bg_role="BG").pack(side="right")
        PillButton(buttons, i18n.tr("cancel"), command=self._cancel,
                   font=app.f_btn, width=96, bg_role="BG").pack(side="right", padx=(0, 8))

        self._sync_more_button()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._center_on(master)

    # ---------- 内部 ----------

    def _build_pills(self, chosen):
        """按 self._titles 重建勾选列表，chosen 是需要保持选中态的 1 基下标。"""
        for pill in self._pills:
            pill.destroy()
        self._pills = []
        width = core._playlist_index_width(len(self._titles))
        for index, text in enumerate(self._titles, start=1):
            # 序号 + 标题；标题按可用宽度截断（ellipsize 需要字体与像素宽度）
            label = f"{index:0{width}d} · " + ellipsize(text, self.app.f_btn,
                                                        self.WIDTH - 150)
            pill = CheckPill(self._body, label, tk.BooleanVar(value=index in chosen),
                             font=self.app.f_btn, bg_role="BG",
                             width=_check_pill_width(self.app.f_btn, label))
            pill.pack(anchor="w", pady=self.ROW_PADY)
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                pill.bind(sequence, self._on_wheel, add="+")
            self._pills.append(pill)

    def _refresh_hint(self):
        # 「还有更多」和「已全部列出」要分开说，否则「前 120 条」听起来像硬上限
        key = "pick_entries_hint_more" if self._truncated else "pick_entries_hint"
        self.hint_label.configure(text=i18n.tr(key, n=len(self._titles)))

    def _sync_more_button(self):
        """还有更多条目才显示「加载更多」；加载中保持可见但不可点。"""
        if self._truncated and self._on_request_more is not None:
            if not self.more_btn.winfo_manager():
                self.more_btn.pack(side="left", padx=(8, 0))
        elif self.more_btn.winfo_manager():
            self.more_btn.pack_forget()
        self.more_btn.configure_state("disabled" if self._loading else "normal")

    def set_items(self, titles, truncated=None):
        """「加载更多」完成后替换列表，并保留用户已经勾选的条目。"""
        chosen = set(self._collect())
        self._titles = list(titles)
        if truncated is not None:
            self._truncated = bool(truncated)
        self._loading = False
        self._build_pills(chosen)
        self._refresh_hint()
        self._sync_more_button()

    def show_load_error(self, text):
        """探测失败：解除加载态并在提示区说明原因，勾选不变。"""
        self._loading = False
        self._sync_more_button()
        self.hint_label.configure(text=str(text))

    def _on_load_more(self):
        if self._loading or self._on_request_more is None:
            return
        self._loading = True
        self._sync_more_button()
        self.hint_label.configure(text=i18n.tr("pick_loading_more"))
        self._on_request_more(len(self._titles) + core.PLAYLIST_LOAD_STEP)

    def _center_on(self, master):
        try:
            self.update_idletasks()
            x = master.winfo_rootx() + max(0, (master.winfo_width() - self.WIDTH) // 2)
            y = master.winfo_rooty() + 60
            self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _on_wheel(self, event):
        delta = getattr(event, "delta", 0) or 0
        self._canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        return "break"

    def _select_all(self, value):
        for pill in self._pills:
            pill.set_checked(value)

    def _collect(self):
        """当前勾选的条目下标（1 基，升序）。"""
        return [index for index, pill in enumerate(self._pills, start=1)
                if bool(pill._var.get())]

    def _confirm(self):
        self.result = self._collect()
        self._close()

    def _cancel(self):
        self.result = None
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self):
        """模态显示，返回选中的条目下标；取消返回 None。"""
        self.grab_set()
        self.wait_window(self)
        return self.result
