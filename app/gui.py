"""tkinter 图形界面 — Liquid Glass 风格，sidebar 多平台布局。

左侧侧边栏切换平台（YouTube / X / Instagram / 设置），右侧为对应平台的
下载面板。视频面板（yt-dlp）与图片面板（gallery-dl + requests）为共用组件。
所有耗时操作（解析 / 下载）都在后台线程执行，通过 `after` 回到主线程更新界面。

主题：集中式调色板（app/theme.py），支持 浅色 / 深色 / 跟随系统，
切换后调用 widgets.apply_theme_tree 重绘整棵控件树。

剪贴板：启动时与窗口重新获得焦点时，若剪贴板是受支持的链接且与当前输入
不同，自动切换到对应平台页、填入 URL 并触发解析。
"""

import io
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

from . import (browser_cookies, core, diagnostics, i18n, images, menubar,
               ytdlp_update,
               platforms, settings, taskbar, theme, windowing)
from .widgets import (
    CheckPill, GlassCard, ImagePicker, LogoSwatch, PillButton, PillSelect,
    RoundEntry, RoundProgress, SegmentedControl, SidebarItem, TFrame, TLabel,
    apply_theme_tree, ellipsize, ellipsize_filename, hand_cursor,
)

_NATIVE_SCROLL_AVAILABLE = False
if sys.platform == "darwin":
    try:
        from AppKit import NSEvent, NSEventMaskScrollWheel
        _NATIVE_SCROLL_AVAILABLE = True
    except Exception:
        pass

URL_RE = re.compile(
    r"https?://"
    r"(?:"
    r"(?:www\.|m\.|music\.)?youtube\.com/(?:watch\?\S*v=|shorts/|live/)[\w-]+\S*"
    r"|youtu\.be/[\w-]+\S*"
    r"|(?:www\.|m\.)?(?:x|twitter)\.com/\w+/status/\d+\S*"
    r"|(?:www\.)?instagram\.com/(?:p|reel|reels|tv)/[\w-]+\S*"
    r"|(?:www\.|m\.)?douyin\.com/(?:video/|share/video/|note/)\d+\S*"
    r"|(?:www\.)?iesdouyin\.com/share/video/\d+\S*"
    r"|v\.douyin\.com/[\w-]+\S*"
    r"|(?:www\.|m\.)?bilibili\.com/video/[\w-]+\S*"
    r"|b23\.tv/[\w-]+\S*"
    r"|(?:www\.)?weibo\.com/(?:\d+/[A-Za-z0-9]+|0/\d+|tv/show/[\w:]+)\S*"
    r"|video\.weibo\.com/show\?\S*"
    r"|m\.weibo\.cn/(?:status|detail)/\d+\S*"
    r")",
    re.IGNORECASE,
)


def extract_supported_url(text: str):
    """从文本中提取首个受支持的链接，没有则返回 None。"""
    if not text:
        return None
    m = URL_RE.search(text.strip())
    return m.group(0) if m else None


def asset_path(name: str):
    """返回 assets 资源的绝对路径；兼容 PyInstaller 打包（sys._MEIPASS）。"""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "assets", name)
    return path if os.path.isfile(path) else None


# 「更换 App Logo」候选：key 对应 assets/logos/{key}_icon.png（1024，Dock 用）、
# assets/logos/{key}_512.png（512，窗口图标 + 设置页预览用）
# 与 assets/logos/{key}_menubar.png（36px@2x，macOS menubar 状态栏图标用）
LOGO_OPTIONS = [("classic", "classic_blue"), ("neon", "neon"),
                ("sunset", "sunset"), ("mint", "mint")]

# 按平台 Cookie 覆盖的界面行顺序，与 platforms.PLATFORMS 一一对应
PLATFORM_LABEL_KEYS = ("youtube_nav", "x_nav", "instagram_nav",
                       "douyin_nav", "bilibili_nav", "weibo_nav")


def logo_icon_path(key: str, size: int = 1024):
    """返回某款 logo 处理后图标的绝对路径；缺失时返回 None。"""
    name = f"{key}_icon.png" if size >= 1024 else f"{key}_512.png"
    return asset_path(os.path.join("logos", name))


def logo_menubar_path(key: str):
    """返回某款 logo 的 menubar 彩色小图路径；缺失时返回 None。"""
    return asset_path(os.path.join("logos", f"{key}_menubar.png"))


def detect_platform(url: str):
    """根据链接判断平台页 key（映射表在 app/platforms.py，引擎也会用到）。"""
    return platforms.detect(url)


def is_weibo_video_url(url: str):
    """判断微博专用视频地址；普通微博状态统一按媒体帖子解析。"""
    low = (url or "").lower()
    return "/tv/show/" in low or "video.weibo.com/show" in low


def _sanitize_filename(text: str) -> str:
    """把标题里的路径分隔符、控制字符等清掉，避免生成非法目录名。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def post_folder_name(post_id: str, title: str = "") -> str:
    """「每个帖子单独建子文件夹」用的目录名：``<帖子 ID>_<标题摘要>``。

    只用帖子 ID 不好认，所以附上标题摘要；取不到标题时退回纯 ID，取不到 ID 时返回
    空串（表示不分组）。标题若被作者修改，会落到新目录——取舍见 AGENTS。
    """
    pid = _sanitize_filename(post_id)
    if not pid:
        return ""
    summary = _sanitize_filename(title)[:40].strip(" ._-")
    return f"{pid}_{summary}" if summary else pid


def platform_label(platform: str) -> str:
    """把 ASCII 平台键翻成界面上的平台名；未知值原样返回。

    面板的身份标识统一用 ``platforms.PLATFORMS`` 里的 ASCII 键（与引擎、Cookie
    覆盖、保存目录键一致），显示时才翻成当前语言的标签。
    """
    key = str(platform or "").strip().lower()
    labels = dict(zip(platforms.PLATFORMS, PLATFORM_LABEL_KEYS))
    label_key = labels.get(key)
    return i18n.tr(label_key) if label_key else str(platform or "")


def download_dir_key(platform: str, kind: str) -> str:
    """返回一个下载方式的保存目录键，例如 ``youtube_audio``。

    ``platform`` 是 ASCII 平台键。历史版本传过显示名（"YouTube"/"微博"），
    所以这里额外做一次小写归一：ASCII 名字（含旧调用点）结果不变，而中文名会
    落到 unknown——调用点已全部改成 ASCII 键，不存在这种输入。
    """
    platform_key = str(platform or "unknown").strip().lower()
    if not platform_key.isascii():
        platform_key = "unknown"
    return f"{platform_key}_{kind}"


# ====================================================================
# 视频下载面板（yt-dlp）：YouTube / X / Instagram / 微博 共用
# ====================================================================


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


class VideoPanel(TFrame):
    """单平台的视频下载面板：解析 → 清晰度 → 下载（暂停/继续/取消）。"""

    # 视频封面在标题行左侧预留的固定尺寸（16:9）
    THUMB_W = 140
    THUMB_H = 79

    def __init__(self, master, app, platform="youtube", show_audio=True,
                 on_redirect=None):
        super().__init__(master, bg_role="BG")
        self.app = app
        self.platform = platform
        self._show_audio = show_audio
        self._on_redirect = on_redirect

        self._info = None
        self._busy = False
        self._downloading = False
        self._paused = False
        self._cancel_event = None
        self._pause_event = None
        self._partial_files = None
        self._dl_params = None
        self._last_percent = 0.0
        self._last_file = None

        self._build()

    def _url_prompt_key(self):
        return "post_link" if self.platform == "weibo" else "video_link"

    # ---------- 界面 ----------

    def _build(self):
        f = self.app

        # —— 卡片 1：链接 ——
        card1 = GlassCard(self)
        card1.set_height(196)
        card1.pack(fill="x", pady=(0, 12))
        b1 = card1.body
        TLabel(b1, text=i18n.tr(self._url_prompt_key()), font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(anchor="w")

        row = TFrame(b1, bg_role="CARD")
        row.pack(fill="x", pady=(8, 0))
        # 链接内容变化（输入/粘贴/程序填入）时使旧解析结果失效，
        # 避免用 A 视频的档位下载 B 视频
        self.url_var = tk.StringVar()
        self.url_var.trace_add("write", lambda *a: self._on_url_edited())
        self.url_entry = RoundEntry(row, textvariable=self.url_var, font=f.f_body)
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.entry.bind("<Return>", lambda e: self.on_probe())
        self.paste_btn = PillButton(row, i18n.tr("paste"), command=self.on_paste,
                                    font=f.f_btn, width=68, bg_role="CARD")
        self.paste_btn.pack(side="left", padx=(10, 0))
        self.probe_btn = PillButton(row, i18n.tr("probe"), command=self.on_probe,
                                    font=f.f_btn, width=68, bg_role="CARD")
        self.probe_btn.pack(side="left", padx=(8, 0))

        # 标题行：左侧预留固定尺寸的封面位（解析成功后在后台载入），右侧标题
        title_row = TFrame(b1, bg_role="CARD")
        title_row.pack(fill="x", pady=(8, 0))
        self.thumb_holder = TFrame(title_row, bg_role="CARD",
                                   width=self.THUMB_W, height=self.THUMB_H)
        self.thumb_holder.pack(side="left", padx=(0, 10))
        self.thumb_holder.pack_propagate(False)  # 固定像素尺寸，不被内容撑开
        self.thumb_label = TLabel(self.thumb_holder, text="", bg_role="CARD")
        self.thumb_label.pack(fill="both", expand=True)
        self._thumb_epoch = 0
        self._thumb_photo = None  # 必须持引用，否则 PhotoImage 会被回收
        self.title_label = TLabel(title_row, text="", anchor="w", justify="left",
                                  font=f.f_small, bg_role="CARD", fg_role="TEXT_SUB")
        self.title_label.pack(side="left", fill="x", expand=True)
        # 宽度由父容器决定，改文字不会反向改变自身的 Configure，不会死循环
        self.title_label.bind("<Configure>", lambda e: self._refresh_title())

        # —— 卡片 2：下载选项 ——
        card2 = GlassCard(self)
        card2.set_height(152)
        card2.pack(fill="x", pady=(0, 12))
        b2 = card2.body
        TLabel(b2, text=i18n.tr("download_options"), font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(anchor="w")

        opt_row = TFrame(b2, bg_role="CARD")
        opt_row.pack(fill="x", pady=(10, 0))
        self.mode_var = tk.StringVar(value="video")
        if self._show_audio:
            mode_options = [(i18n.tr("video_with_audio"), "video"),
                            (i18n.tr("audio_only"), "audio")]
            self.mode_seg = SegmentedControl(
                opt_row, mode_options, self.mode_var, command=self._on_mode_change,
                font=f.f_btn,
                width=_segmented_width(f.f_btn, [text for text, _ in mode_options]))
            self.mode_seg.pack(side="left")
            self.quality_label = TLabel(
                opt_row, text=i18n.tr("quality"), font=f.f_body, bg_role="CARD",
                fg_role="TEXT_SUB")
            self.quality_label.pack(side="left", padx=(22, 8))
        else:
            self.quality_label = TLabel(
                opt_row, text=i18n.tr("quality"), font=f.f_body, bg_role="CARD",
                fg_role="TEXT_SUB")
            self.quality_label.pack(side="left", padx=(0, 8))
        self.quality_var = tk.StringVar(value=i18n.tr("probe_first"))
        self.quality_select = PillSelect(opt_row, self.quality_var,
                                         font=f.f_btn, width=196)
        self.quality_select.pack(side="left")

        # 第二行：仅音频时显示目标格式，YouTube 视频时显示字幕开关
        self.opt_row2 = TFrame(b2, bg_role="CARD")
        self.format_label = TLabel(self.opt_row2, text=i18n.tr("audio_format"),
                                   font=f.f_body, bg_role="CARD", fg_role="TEXT_SUB")
        self.format_var = tk.StringVar(value=self.app.settings.get(
            "audio_format") or core.DEFAULT_AUDIO_FORMAT)
        self.format_var.trace_add("write", lambda *a: self._on_audio_format_change())
        self.format_select = PillSelect(
            self.opt_row2, self.format_var,
            values=[fmt.upper() for fmt in core.AUDIO_FORMATS],
            font=f.f_btn, width=110)
        self.subtitle_var = tk.BooleanVar(
            value=bool(self.app.settings.get("subtitles_enabled", False)))
        self.subtitle_var.trace_add("write", lambda *a: self._on_subtitle_toggle())
        self.subtitle_check = CheckPill(
            self.opt_row2, i18n.tr("download_subtitles"), self.subtitle_var,
            font=f.f_btn, width=_check_pill_width(f.f_btn, i18n.tr("download_subtitles")),
            bg_role="CARD")
        # 播放列表下载范围（只有解析到播放列表时才出现）
        self.range_label = TLabel(self.opt_row2, text=i18n.tr("playlist_range"),
                                  font=f.f_body, bg_role="CARD", fg_role="TEXT_SUB")
        self.playlist_limit_var = tk.StringVar(
            value=self._playlist_limit_label(self.app.settings.get("playlist_limit", 1)))
        self.playlist_limit_var.trace_add(
            "write", lambda *a: self._on_playlist_range_change())
        self.range_select = PillSelect(
            self.opt_row2, self.playlist_limit_var,
            values=[self._playlist_limit_label(v) for v in core.PLAYLIST_LIMITS],
            font=f.f_btn, width=150)
        self._playlist_items = []      # 批量选择的下标；非空时优先于「下载范围」
        # 已加载的条目标题与「是否还有更多」：选择器可以逐次放大探测上限
        self._playlist_entries = []
        self._playlist_truncated = False
        self.pick_btn = PillButton(self.opt_row2, i18n.tr("pick_entries"),
                                   command=self._open_entry_picker, font=f.f_btn,
                                   width=120, bg_role="CARD")
        self._refresh_option_visibility()

        # —— 卡片 3：保存位置 ——
        card3 = GlassCard(self)
        card3.set_height(102)
        card3.pack(fill="x", pady=(0, 14))
        b3 = card3.body
        TLabel(b3, text=i18n.tr("save_location"), font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(anchor="w")

        dir_row = TFrame(b3, bg_role="CARD")
        dir_row.pack(fill="x", pady=(8, 0))
        self.dir_var = tk.StringVar(value=self.app.default_save_dir(self._save_dir_key()))
        self.dir_entry = RoundEntry(dir_row, textvariable=self.dir_var, font=f.f_body)
        self.dir_entry.pack(side="left", fill="x", expand=True)
        PillButton(dir_row, i18n.tr("choose"), command=self.on_choose_dir,
                   font=f.f_btn, width=74, bg_role="CARD").pack(side="left", padx=(10, 0))

        # —— 主操作按钮区 ——
        btn_row = TFrame(self, bg_role="BG")
        btn_row.pack(fill="x", pady=(0, 12))
        # 「开始下载」始终可点：未解析时点击会自动先解析再下载
        self.download_btn = PillButton(btn_row, i18n.tr("start_download"), command=self._on_download_btn,
                                       primary=True, font=f.f_btn_big, height=46)
        self.download_btn.pack(side="left", fill="x", expand=True)
        self.queue_btn = PillButton(btn_row, i18n.tr("queue_add"),
                                    command=self.on_enqueue, font=f.f_btn, width=104,
                                    height=46)
        self.queue_btn.pack(side="right", padx=(10, 0))
        self.cancel_btn = PillButton(btn_row, i18n.tr("cancel"), command=self.on_cancel,
                                     font=f.f_btn, width=88, height=46)
        # 取消按钮仅在下载中/暂停时显示

        # —— 进度与状态 ——
        self.progress = RoundProgress(self)
        self.progress.pack(fill="x")

        status_row = TFrame(self, bg_role="BG")
        status_row.pack(fill="x", pady=(10, 0))
        self.open_btn = PillButton(status_row, i18n.tr("open_folder"), command=self.on_open_folder,
                                   font=f.f_btn, width=130)
        self.open_btn.pack(side="right", padx=(12, 0))
        self.open_btn.configure_state("disabled")
        self.open_file_btn = PillButton(status_row, i18n.tr("open_file"),
                                        command=self.on_open_file, font=f.f_btn, width=104)
        self.open_file_btn.pack(side="right")
        self.open_file_btn.configure_state("disabled")

        self._status_full = (i18n.tr("enter_link_probe"), None)
        self.status_label = TLabel(status_row, text=self._status_full[0], anchor="w",
                                   font=f.f_sub, bg_role="BG", fg_role="TEXT_SUB")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.status_label.bind("<Configure>", lambda e: self._refresh_status_text())
        # 状态栏可点开看完整内容（长错误会被截断）
        self.status_label.bind("<Button-1>", lambda e: self._show_status_detail())

    # ---------- 状态栏 ----------

    def _set_status(self, text, error=False, ok=False, filename=None):
        self._status_full = (text, filename)
        if error:
            diagnostics.record_error(text)  # 供「复制诊断信息」引用
        self.status_label.set_fg_role("ERROR" if error else ("OK" if ok else "TEXT_SUB"))
        self._refresh_status_text()

    def _status_detail_body(self):
        """详情弹窗的正文：状态文字 + 文件名（若有）。"""
        text, filename = self._status_full
        return text + (f"\n\n{filename}" if filename else "")

    def _show_status_detail(self):
        TextDetailDialog(self, self.app, i18n.tr("status_detail"),
                         self._status_detail_body()).show()

    def _refresh_status_text(self):
        text, filename = self._status_full
        f = self.app.f_sub
        max_px = self.status_label.winfo_width() - 4
        if max_px <= 8:
            self.status_label.configure(text=text + (filename or ""))
            return
        if filename:
            avail = max_px - f.measure(text)
            shown = text + ellipsize_filename(filename, f, avail)
        else:
            shown = ellipsize(text, f, max_px)
        # 截断时给出可点击的提示（手型光标），否则用户不知道能点
        self.status_label.configure(cursor=hand_cursor() if shown != text else "")
        if shown != self.status_label.cget("text"):
            self.status_label.configure(text=shown)

    # ---------- 事件 ----------

    def _on_mode_change(self):
        audio = self.mode_var.get() == "audio"
        if self._info is not None:
            options = self._info.audio_options if audio else self._info.qualities
            values = [option.display for option in options]
            if not values:
                values = [i18n.tr("probe_first")]
            self.quality_select.set_values(values)
            self.quality_var.set(values[0])
        # 视频与仅音频是两个独立的下载方式，各自恢复上次选择的目录。
        saved_dir = self.app.default_save_dir(self._save_dir_key())
        if self.dir_var.get() != saved_dir:
            self.dir_var.set(saved_dir)
        self._refresh_option_visibility()

    def _audio_format(self) -> str:
        value = (self.format_var.get() or "").strip().lower()
        return value if value in core.AUDIO_FORMATS else core.DEFAULT_AUDIO_FORMAT

    def _refresh_option_visibility(self):
        """按「视频/仅音频」「是否 YouTube」「是否无损格式」决定控件可见与可用。

        无损（flac）没有比特率概念：此时禁用下拉框并把标签改成「无损」，
        避免用户以为选了个实际不生效的比特率。
        """
        audio = self.mode_var.get() == "audio"
        lossless = self._audio_format() in core.LOSSLESS_AUDIO_FORMATS
        has_audio = bool(self._info and self._info.audio_options)
        has_qualities = bool(self._info and self._info.qualities)

        for widget in (self.format_label, self.format_select):
            if audio and not widget.winfo_manager():
                widget.pack(side="left", padx=(22, 8) if widget is self.format_label else 0)
            elif not audio:
                widget.pack_forget()

        show_subs = (not audio) and self.platform == "youtube"
        if show_subs and not self.subtitle_check.winfo_manager():
            self.subtitle_check.pack(side="left", padx=(22, 0))
        elif not show_subs:
            self.subtitle_check.pack_forget()

        show_range = bool(self._info and self._info.playlist_count)
        for widget in (self.range_label, self.range_select):
            if show_range and not widget.winfo_manager():
                widget.pack(side="left", padx=(22, 8) if widget is self.range_label else 0)
            elif not show_range:
                widget.pack_forget()

        # 条目选择只对播放列表有意义；有勾选时把范围下拉置灰以示其被忽略
        if show_range:
            self._update_pick_button()
            if not self.pick_btn.winfo_manager():
                self.pick_btn.pack(side="left", padx=(16, 0))
            self.range_select.set_enabled(not self._playlist_items)
        else:
            self.pick_btn.pack_forget()

        # 第二行没有内容时不占位，避免非 YouTube 视频页多出一条空白
        if audio or show_subs or show_range:
            if not self.opt_row2.winfo_manager():
                self.opt_row2.pack(fill="x", pady=(10, 0))
        else:
            self.opt_row2.pack_forget()

        if audio and lossless:
            # 无损格式没有比特率概念：与其灰着显示一个无效档位，
            # 不如直接隐藏，只留「格式」下拉，避免误导。
            self.quality_label.pack_forget()
            self.quality_select.pack_forget()
        else:
            # 重新显示时按原顺序追加到模式切换之后
            for widget in (self.quality_label, self.quality_select):
                if not widget.winfo_manager():
                    widget.pack(side="left",
                                padx=(22, 8) if widget is self.quality_label else 0)
            if audio:
                self.quality_label.configure(text=i18n.tr("bitrate"))
                self.quality_select.set_enabled(has_audio)
            else:
                self.quality_label.configure(text=i18n.tr("quality"))
                self.quality_select.set_enabled(has_qualities)

    def _playlist_limit_label(self, limit):
        value = int(limit or 0)
        if value <= 0:
            return i18n.tr("playlist_range_all")
        if value == 1:
            return i18n.tr("playlist_range_current")
        return i18n.tr("playlist_range_n", n=value)

    def _playlist_limit_value(self, label):
        for value in core.PLAYLIST_LIMITS:
            if self._playlist_limit_label(value) == label:
                return value
        return 1

    def _playlist_limit(self):
        return self._playlist_limit_value(self.playlist_limit_var.get())

    def _on_playlist_range_change(self):
        self.app.settings["playlist_limit"] = self._playlist_limit()
        settings.save(self.app.settings)

    def _on_audio_format_change(self):
        self.app.settings["audio_format"] = self._audio_format()
        settings.save(self.app.settings)
        self._refresh_option_visibility()

    def _on_subtitle_toggle(self):
        self.app.settings["subtitles_enabled"] = bool(self.subtitle_var.get())
        settings.save(self.app.settings)

    # ---------- 视频封面 ----------

    def _load_video_thumbnail(self, info):
        """后台拉取视频封面；用代数防止旧结果落到新视频上。"""
        self._thumb_epoch += 1
        epoch = self._thumb_epoch
        url = getattr(info, "thumbnail", "") or ""
        if not url:
            self._set_thumb_image(epoch, None)
            return

        def work():
            try:
                raw = images.fetch_image_bytes(url)
            except Exception:
                raw = None
            self.after(0, self._set_thumb_image, epoch, raw)

        threading.Thread(target=work, daemon=True).start()

    def _set_thumb_image(self, epoch, raw):
        if epoch != self._thumb_epoch:
            return
        if not raw:
            self._thumb_photo = None
            self.thumb_label.configure(image="")
            return
        try:
            from PIL import Image, ImageTk

            im = Image.open(io.BytesIO(raw))
            im.thumbnail((self.THUMB_W, self.THUMB_H))
            photo = ImageTk.PhotoImage(im)
        except Exception:
            return
        self._thumb_photo = photo  # 持引用，避免被 GC 回收后显示空白
        self.thumb_label.configure(image=photo)

    def _clear_video_thumbnail(self):
        self._thumb_epoch += 1
        self._thumb_photo = None
        self.thumb_label.configure(image="")

    def _save_dir_key(self):
        if self.platform == "weibo":
            return download_dir_key(self.platform, "media")
        kind = "audio" if self.mode_var.get() == "audio" else "video"
        return download_dir_key(self.platform, kind)

    def _download_dir(self):
        """本次下载实际用的目录：开启分组时套帖子目录 + 媒体类型子目录。"""
        kind = "audio" if self.mode_var.get() == "audio" else "video"
        return self.app._post_download_dir(
            self.app.default_save_dir(self._save_dir_key()),
            getattr(self._info, "url", ""), getattr(self._info, "title", ""),
            kind=kind)

    def on_choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get() or core.DEFAULT_SAVE_DIR)
        if path:
            self.app.apply_download_dir(self._save_dir_key(), path)

    def on_paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self._set_status(i18n.tr("clipboard_empty"), error=True)
            return
        url = extract_supported_url(text) or text.strip()
        if not url:
            self._set_status(i18n.tr("clipboard_empty"), error=True)
            return
        self.url_entry.set(url)
        if extract_supported_url(url) and not self._busy:
            self.on_probe()

    def handle_url(self, url):
        """外部（剪贴板检测）填入链接并解析。"""
        if self._busy:
            return
        self.url_entry.set(url)
        self._set_status(i18n.tr("clipboard_probe"))
        self.on_probe()

    def on_probe(self):
        if self._busy:
            return
        url = self.url_entry.get().strip()
        if not url:
            self._set_status(i18n.tr(self._url_prompt_key()), error=True)
            return
        if (self.platform == "weibo" and not is_weibo_video_url(url)
                and self._on_redirect is not None):
            self._on_redirect("image", url)
            return

        self._busy = True
        self.probe_btn.configure_state("disabled")
        self.download_btn.configure_state("disabled")
        self._set_status(i18n.tr("probing"))

        def work():
            try:
                info = core.probe(url)
            except core.DownloadError as exc:
                self.after(0, self._probe_failed, str(exc))
            except Exception as exc:
                self.after(0, self._probe_failed, i18n.tr("probe_failed", message=exc))
            else:
                self.after(0, self._probe_done, info)

        threading.Thread(target=work, daemon=True).start()

    def _probe_failed(self, msg):
        self._busy = False
        self.probe_btn.configure_state("normal")
        self.download_btn.configure_state("normal")
        self._set_status(msg, error=True)

    def _probe_done(self, info):
        self._busy = False
        self._info = info
        self._playlist_entries = list(info.playlist_entries)
        self._playlist_truncated = bool(info.playlist_truncated)
        self.probe_btn.configure_state("normal")
        self.download_btn.configure_state("normal")

        self._refresh_title()

        values = ([q.display for q in info.qualities] if info.qualities
                  else [i18n.tr("best_quality")])
        self.quality_select.set_values(values)
        self.quality_var.set(values[0])
        self._playlist_items = []      # 换了链接/重新解析，之前的勾选作废
        self._playlist_entries = []
        self._playlist_truncated = False
        self._on_mode_change()
        self._load_video_thumbnail(info)
        # 浏览器 Cookie 读取失败但匿名解析成功时给出提示。
        self._set_status(info.warning or i18n.tr("probe_success_video"),
                         ok=not info.warning)

    def _refresh_title(self):
        """按当前可用宽度重算标题的截断。

        解析可能发生在布局完成之前（此时 winfo_width() 还很小），窗口缩放也会
        改变可用宽度，所以不能只在解析成功时算一次——实测会截成
        「播放列表：TED - Videos · …」，把条目数整个丢掉。
        """
        if self._info is None:
            return
        text, suffix = self._title_text(self._info)
        max_px = max(200, self.title_label.winfo_width() - 8)
        font = self.app.f_small
        title = text
        while title and font.measure(title + "…" + suffix) > max_px:
            title = title[:-1]
        if title != text:
            title += "…"
        self.title_label.set_fg_role("TEXT")
        self.title_label.configure(text=f"{title}{suffix}")

    def _title_text(self, info):
        """标题行文字与后缀。

        播放列表时改为说明「这是一个播放列表、共多少条」：旧版本遇到播放列表
        链接会静默只下载第一条，用户根本不知道这是个列表。
        """
        if not info.playlist_count:
            suffix = (f"  ·  {info.uploader}" if info.uploader else "") \
                + f"  ·  {info.duration_text}"
            return info.title, suffix
        count_text = i18n.tr(
            "playlist_count_at_least" if info.playlist_truncated
            else "playlist_count_exact", n=info.playlist_count)
        return i18n.tr("playlist_summary",
                       title=info.playlist_title or info.title,
                       count=count_text), ""

    def _height_for_label(self, label):
        if not self._info:
            return None
        for q in self._info.qualities:
            if label in (q.display, q.label):
                return q.max_height
        return None

    def _audio_option_for_label(self, label):
        if not self._info:
            return None
        for option in self._info.audio_options:
            if label == option.display:
                return option
        return None

    def _on_url_edited(self):
        """链接被修改后，旧的解析结果立即失效（清晰度档位归零）。"""
        if self._downloading or self._paused or self._info is None:
            return
        url = self.url_entry.get().strip()
        if url != self._info.url:
            self._info = None
            self.quality_select.set_values([])
            self.quality_var.set(i18n.tr("probe_first"))
            self.title_label.configure(text="")
            self._clear_video_thumbnail()
            self._playlist_items = []
            self._set_status(i18n.tr("url_changed"))

    def _on_download_btn(self):
        if self._paused:
            self.on_resume()
        elif self._downloading:
            self.on_pause()
        else:
            self.on_download()

    def on_pause(self):
        if not self._downloading or self._pause_event is None:
            return
        if not self._pause_event.is_set() and not self._cancel_event.is_set():
            self._pause_event.set()
            self._set_status(i18n.tr("pausing"))

    def on_resume(self):
        if not self._paused or not self._dl_params:
            return
        self._partial_files = None
        # 重新调用下载即可：yt-dlp 默认 continuedl=True，会从 .part 断点续传
        self._start_download(resume=True)

    def on_cancel(self):
        if self._paused:
            files = self._partial_files
            self._partial_files = None
            core.cleanup_partial_files(files)
            self._download_cancelled()
            return
        if not self._downloading or self._cancel_event is None:
            return
        if not self._cancel_event.is_set():
            self._cancel_event.set()
            self._set_status(i18n.tr("cancelling"))

    def on_download(self):
        if self._busy:
            return
        url = self.url_entry.get().strip()
        if not url:
            self._set_status(i18n.tr(self._url_prompt_key()), error=True)
            return
        if (self.platform == "weibo" and not is_weibo_video_url(url)
                and self._on_redirect is not None):
            self._on_redirect("image", url)
            return
        # 未解析、或链接已变更（解析结果与输入不一致）时：先自动解析，
        # 成功后用解析出的最高档位继续下载，避免用旧视频的档位下错视频
        if self._info is None or self._info.url != url:
            self._probe_then_download(url)
            return
        # 先持久化当前输入；清空输入时也能正确回退到全局默认目录。
        self.app.apply_download_dir(self._save_dir_key(), self.dir_var.get().strip())
        audio_option = self._audio_option_for_label(self.quality_var.get())
        self._dl_params = {
            "url": self._info.url,
            "save_dir": self._download_dir(),
            "audio_only": self.mode_var.get() == "audio",
            "height": (None if self.mode_var.get() == "audio"
                       else self._height_for_label(self.quality_var.get())),
            "audio_bitrate": audio_option.bitrate_kbps if audio_option else 192,
            "audio_format_id": audio_option.format_id if audio_option else None,
            "audio_format": self._audio_format(),
            "subtitles": (self.mode_var.get() != "audio"
                          and self.platform == "youtube"
                          and bool(self.subtitle_var.get())),
            "subtitle_langs": self.app.settings.get("subtitle_langs", ""),
            # 只有解析到播放列表时才可能扩大下载范围；否则固定为「仅当前视频」
            "is_playlist": bool(self._info.playlist_count),
            "playlist_limit": (self._playlist_limit()
                               if self._info.playlist_count else 1),
            "playlist_title": self._info.playlist_title if self._info.playlist_count else "",
            "playlist_total": self._playlist_total(),
            "playlist_size_hint": self._playlist_size_hint(),
            "playlist_items": list(self._playlist_items),
        }
        self._last_file = None
        self._last_percent = 0.0
        self.open_btn.configure_state("disabled")
        self.open_file_btn.configure_state("disabled")
        self.progress.set(0)
        self._start_download(resume=False)

    def _load_more_entries(self, limit, picker):
        """选择器请求更多条目：后台重新扁平探测，成功后回填列表。

        只放大「探测上限」，不动下载逻辑——指定条目下载时用的是 playlist_items，
        不带 playlistend，所以选到第 250 条也能真的下到。
        """
        url = (self.url_entry.get() or "").strip()
        if not url:
            picker.show_load_error(i18n.tr("enter_video_link"))
            return

        def alive():
            # 探测期间用户可能已经把弹窗关掉；此时任何控件操作都会 TclError
            try:
                return bool(picker.winfo_exists())
            except tk.TclError:
                return False

        def worker():
            try:
                info = core.probe(url, preview_limit=limit)
            except Exception as exc:                      # noqa: BLE001 - 原样告知用户
                message = str(exc) or exc.__class__.__name__
                self.app.after(0, lambda: alive() and picker.show_load_error(
                    i18n.tr("pick_load_failed", error=message)))
                return
            titles = list(info.playlist_entries)
            truncated = bool(info.playlist_truncated)

            def apply():
                if not alive():
                    return
                self._playlist_entries = titles
                self._playlist_truncated = truncated
                picker.set_items(titles, truncated)

            self.app.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def _playlist_total(self):
        """整批进度需要的总数：批量选择时是选择数，限量时是限量值。"""
        if not self._info or not self._info.playlist_count:
            return 0
        if self._playlist_items:
            return len(self._playlist_items)
        limit = self._playlist_limit()
        if limit > 0:
            return limit
        # 「全部」但解析被限量截断时总数未知，交给界面退化成「第 i 个」
        return 0 if self._info.playlist_truncated else self._info.playlist_count

    def _update_pick_button(self):
        count = len(self._playlist_items)
        self.pick_btn.set_text(i18n.tr("pick_entries_selected", n=count)
                               if count else i18n.tr("pick_entries"))

    def _open_entry_picker(self):
        """打开条目选择弹窗；确定后记录勾选（空选择表示回到「下载范围」）。"""
        if not self._info or not self._info.playlist_count:
            return
        picker = EntryPicker(
            self, self.app, self._playlist_entries, self._playlist_items,
            truncated=self._playlist_truncated,
            on_request_more=lambda limit: self._load_more_entries(limit, picker))
        chosen = picker.show()
        if chosen is None:
            return
        self._playlist_items = chosen
        self._refresh_option_visibility()
        if chosen:
            self._set_status(i18n.tr("pick_entries_selected", n=len(chosen)))
        else:
            self._set_status(i18n.tr("probe_success_video")
                             if not self._info.warning else self._info.warning,
                             ok=not self._info.warning)

    def _playlist_size_hint(self):
        """仅用于决定文件名序号补零宽度。

        - 勾选了条目：用最大下标（可能勾到第 100 条，宽度得够）；
        - 否则限量时用限量值，全量时用解析到的条目数。
        """
        if not self._info or not self._info.playlist_count:
            return 0
        if self._playlist_items:
            return max(self._playlist_items)
        limit = self._playlist_limit()
        return limit if limit > 0 else self._info.playlist_count

    def _probe_then_download(self, url):
        """「开始下载」时尚未解析：自动解析，成功后立即接着下载。"""
        self._busy = True
        self._info = None
        self.probe_btn.configure_state("disabled")
        self.download_btn.configure_state("disabled")
        self._set_status(i18n.tr("auto_probing"))

        def work():
            try:
                info = core.probe(url)
            except core.DownloadError as exc:
                self.after(0, self._probe_failed, str(exc))
            except Exception as exc:
                self.after(0, self._probe_failed, i18n.tr("probe_failed", message=exc))
            else:
                self.after(0, self._auto_probe_done, info)

        threading.Thread(target=work, daemon=True).start()

    def _auto_probe_done(self, info):
        self._probe_done(info)  # 刷新标题/档位下拉框并解除 busy
        self.on_download()      # 此时解析结果与输入一致，直接开始下载

    def _start_download(self, resume):
        p = self._dl_params
        self._busy = True
        self._downloading = True
        self._paused = False
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        cancel_event = self._cancel_event
        pause_event = self._pause_event
        self.download_btn.set_text(i18n.tr("pause_download"))
        self.cancel_btn.pack(side="left", padx=(10, 0))
        self.probe_btn.configure_state("disabled")
        self._set_status(i18n.tr("resuming" if resume else "starting"))

        # 播放列表当前条目位置；由 entry_progress 更新，供进度文案复用
        self._playlist_pos = None

        def position_prefix():
            if not self._playlist_pos:
                return ""
            index, total, _title = self._playlist_pos
            key = "playlist_prefix" if total else "playlist_prefix_unknown"
            return i18n.tr(key, index=index, total=total) + "  ·  "

        def on_entry(index, total, title):
            self._playlist_pos = (index, total, title)

            def update():
                if cancel_event.is_set() or pause_event.is_set():
                    return
                if total:
                    self._set_status(i18n.tr("playlist_progress", index=index,
                                             total=total, title=title))
                else:
                    self._set_status(i18n.tr("playlist_progress_unknown", index=index,
                                             title=title))
            self.after(0, update)

        def on_progress(percent, speed_text, eta_text, status):
            def update():
                if cancel_event.is_set() or pause_event.is_set():
                    return
                if status == "merging":
                    # 播放列表里每个条目都会合并，进度条保持整批百分比
                    if self._playlist_pos is None:
                        self.progress.set(100)
                    self._set_status(position_prefix() + i18n.tr("merging"))
                else:
                    if percent is not None:
                        self._last_percent = percent
                        self.progress.set(percent)
                        _taskbar_progress(self.app, percent)
                        self._set_status(position_prefix() + i18n.tr(
                            "downloading_percent", percent=percent,
                            speed=speed_text, eta=eta_text))
                    else:
                        self._set_status(position_prefix()
                                         + i18n.tr("downloading", speed=speed_text))
            self.after(0, update)

        def work():
            path = None
            paths = None
            try:
                if p["audio_only"]:
                    path = core.download_audio(p["url"], save_dir=p["save_dir"],
                                               progress=on_progress,
                                               cancel_event=cancel_event,
                                               pause_event=pause_event,
                                               bitrate_kbps=p["audio_bitrate"],
                                               format_id=p["audio_format_id"],
                                               audio_format=p["audio_format"])
                elif p["is_playlist"] and (p["playlist_items"]
                                           or p["playlist_limit"] != 1):
                    paths = core.download_playlist(
                        p["url"], height=p["height"], save_dir=p["save_dir"],
                        playlist_limit=p["playlist_limit"],
                        playlist_title=p["playlist_title"],
                        playlist_total=p["playlist_total"],
                        playlist_size_hint=p["playlist_size_hint"],
                        playlist_items=p["playlist_items"],
                        progress=on_progress, entry_progress=on_entry,
                        cancel_event=cancel_event, pause_event=pause_event,
                        subtitles=p["subtitles"], subtitle_langs=p["subtitle_langs"])
                else:
                    path = core.download_video(p["url"], height=p["height"],
                                               save_dir=p["save_dir"],
                                               progress=on_progress,
                                               cancel_event=cancel_event,
                                               pause_event=pause_event,
                                               subtitles=p["subtitles"],
                                               subtitle_langs=p["subtitle_langs"])
            except core.DownloadCancelled:
                self.after(0, self._download_cancelled)
            except core.DownloadPaused as exc:
                self.after(0, self._download_paused, exc.partial_files)
            except core.DownloadError as exc:
                self.after(0, self._download_failed, str(exc))
            except Exception as exc:
                self.after(0, self._download_failed, i18n.tr("download_failed", message=exc))
            else:
                # 播放列表返回多条路径，单视频/仅音频返回一条
                self.after(0, self._download_done, paths if paths else path)

        threading.Thread(target=work, daemon=True).start()

    def _download_paused(self, partial_files):
        if self._cancel_event is not None and self._cancel_event.is_set():
            core.cleanup_partial_files(partial_files)
            self._download_cancelled()
            return
        self._downloading = False
        self._paused = True
        self._partial_files = partial_files
        self.download_btn.set_text(i18n.tr("resume_download"))
        self._set_status(i18n.tr("paused_at", percent=self._last_percent))

    def _reset_after_download(self):
        self._busy = False
        self._downloading = False
        self._paused = False
        self._cancel_event = None
        self._pause_event = None
        self._partial_files = None
        self.download_btn.set_text(i18n.tr("start_download"))
        self.download_btn.configure_state("normal")
        self.cancel_btn.pack_forget()
        self.probe_btn.configure_state("normal")
        self.app.notify_download_finished()

    def _download_cancelled(self):
        self._reset_after_download()
        self.progress.set(0)
        _taskbar_clear(self.app)
        self._set_status(i18n.tr("cancelled_cleaned"))

    def _download_failed(self, msg):
        self._reset_after_download()
        self.progress.set(0)
        _taskbar_clear(self.app)
        self._set_status(msg, error=True)

    def _download_done(self, result):
        self._reset_after_download()
        paths = [p for p in (result if isinstance(result, (list, tuple)) else [result]) if p]
        self._last_file = paths[0] if paths else None
        self.open_btn.configure_state("normal")
        if paths:
            self.open_file_btn.configure_state("normal")
            self.app.record_download(paths, title=getattr(self._info, "title", ""),
                                     platform=self.platform)
        self.progress.set(0)
        _taskbar_done(self.app)
        if len(paths) > 1:
            folder = os.path.basename(os.path.dirname(paths[0]))
            self._set_status(i18n.tr("playlist_complete", n=len(paths), folder=folder),
                             ok=True)
        else:
            self._set_status(i18n.tr("download_complete"), ok=True,
                             filename=os.path.basename(self._last_file or ""))

    def on_open_folder(self):
        open_in_file_manager(self._last_file)

    def on_enqueue(self):
        """把当前链接加入队列（选项不冻结，执行时用当时的设置）。"""
        url = self.url_entry.get().strip()
        if not url:
            self._set_status(i18n.tr("queue_need_link"), error=True)
            return
        self.app.enqueue_url(url)
        self._set_status(i18n.tr("queue_added", url=url), ok=True)

    def on_open_file(self):
        path = self._last_file
        if not path:
            return
        if not os.path.exists(path):
            self._set_status(i18n.tr("file_missing"), error=True)
            return
        open_path(path)


# ====================================================================
# 图片下载面板（gallery-dl 解析 + requests 下载）：X / Instagram / 微博 共用
# ====================================================================


class ImagePanel(TFrame):
    """图片下载面板：解析帖子 → 缩略图多选（全选）→ 下载（可取消）。"""

    RESOURCE_CARD_MIN_HEIGHT = 180
    RESOURCE_CARD_MAX_HEIGHT = 322
    RESOURCE_CARD_RESERVED_HEIGHT = 342
    PICKER_CHROME_HEIGHT = 96
    PICKER_MIN_HEIGHT = 80

    def __init__(self, master, app, platform="x", on_redirect=None):
        super().__init__(master, bg_role="BG")
        self.app = app
        self.platform = platform
        self._on_redirect = on_redirect

        self._post = None
        self._busy = False
        self._downloading = False
        self._cancel_event = None
        self._last_paths = []
        self._thumb_epoch = 0  # 解析代数，旧的缩略图线程结果直接丢弃
        self._resource_card_height = None

        self._build()
        self.bind("<Configure>", self._on_panel_resize, add="+")

    def _build(self):
        f = self.app

        # —— 卡片 1：链接 ——
        self.link_card = GlassCard(self)
        self.link_card.set_height(96)
        self.link_card.pack(fill="x", pady=(0, 12))
        b1 = self.link_card.body
        row = TFrame(b1, bg_role="CARD")
        row.pack(fill="x", pady=(4, 0))
        TLabel(row, text=i18n.tr("post_link"), font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(side="left", padx=(0, 10))
        self.url_entry = RoundEntry(row, font=f.f_body)
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.entry.bind("<Return>", lambda e: self.on_probe())
        self.paste_btn = PillButton(row, i18n.tr("paste"), command=self.on_paste,
                                    font=f.f_btn, width=68, bg_role="CARD")
        self.paste_btn.pack(side="left", padx=(10, 0))
        self.probe_btn = PillButton(row, i18n.tr("probe"), command=self.on_probe,
                                    font=f.f_btn, width=68, bg_role="CARD")
        self.probe_btn.pack(side="left", padx=(8, 0))

        # —— 卡片 2：图片选择 ——
        self.selection_card = GlassCard(self)
        self.selection_card.pack(fill="both", expand=True, pady=(0, 12))
        b2 = self.selection_card.body
        head = TFrame(b2, bg_role="CARD")
        head.pack(fill="x")
        self.selection_label = TLabel(
            head, text=i18n.tr("image_selection"), font=f.f_section,
            bg_role="CARD", fg_role="TEXT_SUB")
        self.selection_label.pack(side="left")
        self.count_label = TLabel(head, text="", font=f.f_small, bg_role="CARD",
                                  fg_role="TEXT_SUB")
        self.count_label.pack(side="right")
        self.all_var = tk.BooleanVar(value=False)
        self.all_check = CheckPill(
            head, i18n.tr("select_all"), self.all_var,
            command=self._on_all_toggle, font=f.f_btn,
            width=_check_pill_width(f.f_btn, i18n.tr("select_all")))
        self.all_check.pack(side="right", padx=(0, 12))

        self.post_label = TLabel(b2, text=i18n.tr("select_images_hint"), anchor="w",
                                 font=f.f_small, bg_role="CARD", fg_role="TEXT_SUB")
        self.post_label.pack(anchor="w", fill="x", pady=(6, 4))

        self.picker = ImagePicker(b2, on_change=self._on_selection_change, height=150)
        self.picker.pack(fill="both", expand=True)

        # —— 卡片 3：保存位置 ——
        self.save_card = GlassCard(self)
        self.save_card.set_height(96)
        self.save_card.pack(fill="x", pady=(0, 14))
        b3 = self.save_card.body
        dir_row = TFrame(b3, bg_role="CARD")
        dir_row.pack(fill="x", pady=(4, 0))
        TLabel(dir_row, text=i18n.tr("save_location"), font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(side="left", padx=(0, 10))
        self.dir_var = tk.StringVar(value=self.app.default_save_dir(self._save_dir_key()))
        self.dir_entry = RoundEntry(dir_row, textvariable=self.dir_var, font=f.f_body)
        self.dir_entry.pack(side="left", fill="x", expand=True)
        PillButton(dir_row, i18n.tr("choose"), command=self.on_choose_dir,
                   font=f.f_btn, width=74, bg_role="CARD").pack(side="left", padx=(10, 0))

        # —— 操作按钮 + 进度 ——
        btn_row = TFrame(self, bg_role="BG")
        btn_row.pack(fill="x", pady=(0, 12))
        self.download_btn = PillButton(btn_row, i18n.tr("download_selected_images"), command=self.on_download,
                                       primary=True, font=f.f_btn_big, height=46)
        self.download_btn.pack(side="left", fill="x", expand=True)
        self.download_btn.configure_state("disabled")
        self.cancel_btn = PillButton(btn_row, i18n.tr("cancel"), command=self.on_cancel,
                                     font=f.f_btn, width=88, height=46)

        self.progress = RoundProgress(self)
        self.progress.pack(fill="x")

        status_row = TFrame(self, bg_role="BG")
        status_row.pack(fill="x", pady=(10, 0))
        self.open_btn = PillButton(status_row, i18n.tr("open_folder"), command=self.on_open_folder,
                                   font=f.f_btn, width=130)
        self.open_btn.pack(side="right", padx=(12, 0))
        self.open_btn.configure_state("disabled")
        self.open_file_btn = PillButton(status_row, i18n.tr("open_file"),
                                        command=self.on_open_file, font=f.f_btn, width=104)
        self.open_file_btn.pack(side="right")
        self.open_file_btn.configure_state("disabled")
        self._status_full = i18n.tr("enter_link_probe")
        self.status_label = TLabel(status_row, text=self._status_full, anchor="w",
                                   font=f.f_sub, bg_role="BG", fg_role="TEXT_SUB")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.status_label.bind("<Configure>", lambda e: self._refresh_status_text())
        # 状态栏可点开看完整内容（长错误会被截断）
        self.status_label.bind("<Button-1>", lambda e: self._show_status_detail())

    def _on_panel_resize(self, _event):
        available = self.winfo_height() - self.RESOURCE_CARD_RESERVED_HEIGHT
        card_height = max(self.RESOURCE_CARD_MIN_HEIGHT, min(
            self.RESOURCE_CARD_MAX_HEIGHT, available))
        if card_height == self._resource_card_height:
            return
        self._resource_card_height = card_height
        self.selection_card.set_height(card_height)
        self.picker.canvas.configure(height=max(
            self.PICKER_MIN_HEIGHT, card_height - self.PICKER_CHROME_HEIGHT))

    # ---------- 状态栏 ----------

    def _set_status(self, text, error=False, ok=False):
        self._status_full = text
        if error:
            diagnostics.record_error(text)  # 供「复制诊断信息」引用
        self.status_label.set_fg_role("ERROR" if error else ("OK" if ok else "TEXT_SUB"))
        self._refresh_status_text()

    def _status_detail_body(self):
        return str(self._status_full or "")

    def _show_status_detail(self):
        TextDetailDialog(self, self.app, i18n.tr("status_detail"),
                         self._status_detail_body()).show()

    def _refresh_status_text(self):
        max_px = self.status_label.winfo_width() - 4
        shown = (self._status_full if max_px <= 8
                 else ellipsize(self._status_full, self.app.f_sub, max_px))
        self.status_label.configure(
            cursor=hand_cursor() if shown != self._status_full else "")
        if shown != self.status_label.cget("text"):
            self.status_label.configure(text=shown)

    # ---------- 事件 ----------

    def on_choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get() or images.DEFAULT_SAVE_DIR)
        if path:
            self.app.apply_download_dir(self._save_dir_key(), path)

    def _save_dir_key(self):
        if self.platform == "weibo":
            return download_dir_key(self.platform, "media")
        return download_dir_key(self.platform, "image")

    def _download_dir(self):
        """本次下载实际用的目录：开启「每个帖子单独建子文件夹」时再套一层。"""
        return self.app._post_download_dir(
            self.app.default_save_dir(self._save_dir_key()),
            getattr(self._post, "url", ""), getattr(self._post, "title", ""),
            fallback_id=getattr(self._post, "post_id", ""))

    def _group_by_type(self):
        """是否让引擎按媒体类型分目录（仅在确实用了帖子目录时）。"""
        if not self.app.group_by_post_enabled():
            return False
        base = self.app.default_save_dir(self._save_dir_key())
        return self._download_dir() != base

    def on_paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self._set_status(i18n.tr("clipboard_empty"), error=True)
            return
        url = extract_supported_url(text) or text.strip()
        if not url:
            self._set_status(i18n.tr("clipboard_empty"), error=True)
            return
        self.url_entry.set(url)
        if extract_supported_url(url) and not self._busy:
            self.on_probe()

    def handle_url(self, url):
        if self._busy:
            return
        self.url_entry.set(url)
        self._set_status(i18n.tr("clipboard_probe"))
        self.on_probe()

    def _on_all_toggle(self):
        self.picker.set_all(bool(self.all_var.get()))

    def _on_selection_change(self):
        n = len(self.picker.selected_indices())
        total = len(self._post.items) if self._post else 0
        self.count_label.configure(text=i18n.tr("selected_count", n=n, total=total))
        self.all_var.set(n == total and total > 0)
        self.all_check.refresh()
        if not self._downloading:
            self.download_btn.configure_state("normal" if n else "disabled")

    def on_probe(self):
        if self._busy:
            return
        url = self.url_entry.get().strip()
        if not url:
            self._set_status(i18n.tr("enter_post_link"), error=True)
            return
        if (self.platform == "weibo" and is_weibo_video_url(url)
                and self._on_redirect is not None):
            self._on_redirect("video", url)
            return
        self._busy = True
        self._thumb_epoch += 1
        self.probe_btn.configure_state("disabled")
        self.download_btn.configure_state("disabled")
        self._set_status(i18n.tr("probing_images"))

        def work():
            try:
                post = images.probe_images(url)
            except images.ImageError as exc:
                self.after(0, self._probe_failed, str(exc))
            except Exception as exc:
                self.after(0, self._probe_failed, i18n.tr("images_probe_failed", message=exc))
            else:
                self.after(0, self._probe_done, post)

        threading.Thread(target=work, daemon=True).start()

    def _clear_results(self):
        """清空上一次的解析结果。

        解析失败时如果保留旧帖子的媒体列表，用户会看到「旧内容 + 新报错」，
        容易误勾选并下载到上一个帖子的媒体。
        """
        self._post = None
        self._thumb_epoch += 1  # 丢弃上一次解析遗留的缩略图线程结果
        self.picker.set_items([])
        self.all_var.set(False)
        self.post_label.configure(text=i18n.tr("select_images_hint"))
        self.selection_label.configure(text=i18n.tr("image_selection"))
        self.download_btn.set_text(i18n.tr("download_selected_images"))
        self._on_selection_change()

    def _probe_failed(self, msg):
        self._busy = False
        self.probe_btn.configure_state("normal")
        self._clear_results()
        self._set_status(msg, error=True)

    def _probe_done(self, post):
        self._busy = False
        self._post = post
        self.probe_btn.configure_state("normal")

        desc = f"@{post.author}" if post.author else ""
        if post.title:
            desc = f"{desc}  ·  {post.title}" if desc else post.title
        self.post_label.set_fg_role("TEXT")
        self.post_label.configure(text=desc or i18n.tr("no_text"))
        mixed = post.has_video
        self.selection_label.configure(text=i18n.tr(
            "media_selection" if mixed else "image_selection"))
        self.post_label.configure(text=desc or i18n.tr(
            "select_media_hint" if mixed else "select_images_hint"))
        self.download_btn.set_text(i18n.tr(
            "download_selected_media" if mixed else "download_selected_images"))

        items = []
        for it in post.items:
            sub = it.size_text + (f" · {it.extension.upper()}" if it.extension else "")
            items.append({
                "label": i18n.tr(
                    "video_label" if it.media_type == "video" else "image_label",
                    index=it.index),
                "sub": sub.strip(" ·"),
                "selected": False,
                "photo": None,
                "font_small": self.app.f_small,
            })
        self.picker.set_items(items)
        self.all_var.set(False)
        self._on_selection_change()
        # 解析成功但浏览器 Cookie 被跳过时如实告知，避免用户误以为用的是登录态。
        if post.warning:
            self._set_status(post.warning)
        else:
            self._set_status(i18n.tr(
                "probe_success_media" if mixed else "probe_success_images",
                n=len(post.items)), ok=True)
        self._load_thumbnails(post)

    def _load_thumbnails(self, post):
        """后台并行拉取缩略图（每张独立线程 + 重试），主线程转 PhotoImage 填入网格。"""
        epoch = self._thumb_epoch

        def fetch_one(i, item):
            if epoch != self._thumb_epoch:
                return
            try:
                raw = images.fetch_thumbnail(item)
            except Exception:
                return
            self.after(0, self._set_thumb, epoch, i, raw)

        for i, item in enumerate(post.items):
            threading.Thread(target=fetch_one, args=(i, item), daemon=True).start()

    def _set_thumb(self, epoch, index, raw):
        if epoch != self._thumb_epoch:
            return
        try:
            from PIL import Image, ImageTk

            im = Image.open(io.BytesIO(raw))
            im.thumbnail((ImagePicker.THUMB_W, ImagePicker.THUMB_H))
            photo = ImageTk.PhotoImage(im)
        except Exception:
            return
        self.picker.set_thumb(index, photo)

    def on_download(self):
        if self._busy or self._downloading or not self._post:
            return
        sel = [self._post.items[i] for i in self.picker.selected_indices()]
        if not sel:
            self._set_status(i18n.tr("select_images_error"), error=True)
            return
        self._busy = True
        self._downloading = True
        self._cancel_event = threading.Event()
        cancel_event = self._cancel_event
        # 也记录手动输入的路径，而不只记录目录选择器选出的路径。
        self.app.apply_download_dir(self._save_dir_key(), self.dir_var.get().strip())
        save_dir = self._download_dir()
        group_by_type = self._group_by_type()
        self._last_paths = []
        self.open_btn.configure_state("disabled")
        self.open_file_btn.configure_state("disabled")
        self.download_btn.configure_state("disabled")
        self.cancel_btn.pack(side="left", padx=(10, 0))
        self.probe_btn.configure_state("disabled")
        self.progress.set(0)
        self._set_status(i18n.tr(
            "start_media_download" if self._post.has_video else "start_image_download",
            n=len(sel)))

        def on_progress(percent, text):
            def update():
                if cancel_event.is_set():
                    return
                self.progress.set(percent)
                _taskbar_progress(self.app, percent)
                self._set_status(text)
            self.after(0, update)

        def work():
            try:
                result = images.download_images(
                    sel, save_dir=save_dir, progress=on_progress,
                    cancel_event=cancel_event,
                    skip_existing=bool(self.app.settings.get(
                        "skip_existing_images", True)),
                    group_by_type=group_by_type)
            except images.ImageCancelled:
                self.after(0, self._download_cancelled)
            except images.ImageError as exc:
                self.after(0, self._download_failed, str(exc))
            except Exception as exc:
                self.after(0, self._download_failed, i18n.tr("images_download_failed", message=exc))
            else:
                self.after(0, self._download_done, result)

        threading.Thread(target=work, daemon=True).start()

    def on_cancel(self):
        if self._downloading and self._cancel_event is not None \
                and not self._cancel_event.is_set():
            self._cancel_event.set()
            self._set_status(i18n.tr("cancelling"))

    def _reset_after_download(self):
        self._busy = False
        self._downloading = False
        self._cancel_event = None
        self.cancel_btn.pack_forget()
        self.probe_btn.configure_state("normal")
        self._on_selection_change()
        self.app.notify_download_finished()

    def _download_cancelled(self):
        done = len(self._last_paths)
        self._reset_after_download()
        self.progress.set(0)
        _taskbar_clear(self.app)
        suffix = i18n.tr("completed_images_kept", done=done) if done else ""
        self._set_status(i18n.tr("cancelled_images") + suffix)

    def _download_failed(self, msg):
        self._reset_after_download()
        self.progress.set(0)
        _taskbar_clear(self.app)
        self._set_status(msg, error=True)

    def _download_done(self, result):
        # 跳过（已存在）的文件也算「能找到的产物」，否则全部跳过时
        #「打开所在文件夹」会没有目标。
        self._last_paths = list(result.paths) + list(result.skipped)
        self._reset_after_download()
        self.progress.set(0)
        self.open_btn.configure_state("normal")
        if self._last_paths:
            self.open_file_btn.configure_state("normal")
            # 已存在的文件也记进历史：它们同样是「能找到的产物」
            self.app.record_download(self._last_paths,
                                     title=getattr(self._post, "title", ""),
                                     platform=self.platform)
        _taskbar_done(self.app)
        if not result.paths:
            # 全部已存在：报「完成 0 张」会让人以为失败了
            self._set_status(
                i18n.tr("images_all_skipped", n=len(result.skipped)), ok=True)
            return
        folder = os.path.basename(os.path.dirname(result.paths[0]))
        if result.skipped:
            self._set_status(i18n.tr("images_complete_skipped", n=len(result.paths),
                                     skipped=len(result.skipped), folder=folder),
                             ok=True)
        else:
            self._set_status(i18n.tr("images_complete", n=len(result.paths),
                                     folder=folder), ok=True)

    def on_open_folder(self):
        if self._last_paths:
            open_in_file_manager(self._last_paths[0])

    def on_open_file(self):
        if not self._last_paths:
            return
        path = self._last_paths[0]
        if not os.path.exists(path):
            self._set_status(i18n.tr("file_missing"), error=True)
            return
        open_path(path)


# ====================================================================
# 平台页面
# ====================================================================


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


class PageHeader(TFrame):
    def __init__(self, master, app, title, subtitle):
        super().__init__(master, bg_role="BG")
        TLabel(self, text=title, font=app.f_title, bg_role="BG",
               fg_role="TEXT").pack(anchor="w")
        TLabel(self, text=subtitle, font=app.f_sub, bg_role="BG",
               fg_role="TEXT_SUB").pack(anchor="w", pady=(2, 0))


class VideoPlatformPage(TFrame):
    """纯视频平台页（YouTube / 抖音 / 哔哩哔哩）：完整视频面板（清晰度 /
    仅音频 / 暂停续传 / 取消）。"""

    def __init__(self, master, app, title, subtitle, platform, hint=""):
        super().__init__(master, bg_role="BG")
        PageHeader(self, app, title, subtitle).pack(fill="x", pady=(0, 14))
        self.hint_label = None
        if hint:
            self.hint_label = _pack_wrapped_hint(self, app, hint)
        self.video_panel = VideoPanel(self, app, platform=platform, show_audio=True)
        self.video_panel.pack(fill="both", expand=True)

    def handle_url(self, url):
        self.video_panel.handle_url(url)


class MediaPlatformPage(TFrame):
    """支持视频和图片的平台页，可按需显示手动类型切换。"""

    def __init__(self, master, app, title, subtitle, platform, hint="",
                 show_kind_switch=True):
        super().__init__(master, bg_role="BG")
        self.platform = platform
        self._show_kind_switch = show_kind_switch
        head_row = TFrame(self, bg_role="BG")
        head_row.pack(fill="x", pady=(0, 14))
        PageHeader(head_row, app, title, subtitle).pack(side="left")
        self.kind_var = tk.StringVar(value="video" if show_kind_switch else "image")
        self.kind_seg = None
        if show_kind_switch:
            kind_options = [(i18n.tr("video_segment"), "video"),
                            (i18n.tr("image_segment"), "image")]
            self.kind_seg = SegmentedControl(
                head_row, kind_options, self.kind_var,
                command=self._on_kind_change, font=app.f_btn, bg_role="BG",
                width=_segmented_width(app.f_btn, [t for t, _ in kind_options]))
            self.kind_seg.pack(side="right", pady=(6, 0))

        self.hint_label = None
        if hint:
            self.hint_label = _pack_wrapped_hint(self, app, hint)

        redirect = self._redirect_panel if platform == "weibo" else None
        self.video_panel = VideoPanel(self, app, platform=platform, show_audio=False,
                                      on_redirect=redirect)
        self.image_panel = ImagePanel(self, app, platform=platform,
                                      on_redirect=redirect)
        initial_panel = self.video_panel if self.kind_var.get() == "video" else self.image_panel
        initial_panel.pack(fill="both", expand=True)

    def _redirect_panel(self, kind, url):
        """微博手动输入时按链接类型切换内部面板，用户无需选择类型。"""
        self.show_kind(kind)
        panel = self.video_panel if kind == "video" else self.image_panel
        panel.handle_url(url)

    def _on_kind_change(self):
        if self.kind_var.get() == "video":
            self.image_panel.pack_forget()
            self.video_panel.pack(fill="both", expand=True)
        else:
            self.video_panel.pack_forget()
            self.image_panel.pack(fill="both", expand=True)

    def show_kind(self, kind):
        if kind != self.kind_var.get():
            self.kind_var.set(kind)
            if self.kind_seg is not None:
                self.kind_seg.refresh()
            self._on_kind_change()

    def handle_url(self, url):
        low = url.lower()
        if self.platform == "weibo":
            # 普通微博状态默认进入媒体列表；微博专用视频 URL 进入视频面板。
            if not is_weibo_video_url(low):
                self.show_kind("image")
            else:
                self.show_kind("video")
        elif "/photo/" in low:
            self.show_kind("image")
        elif "instagram.com/p/" in low:
            self.show_kind("image")
        elif "/reel" in low or "/tv/" in low:
            self.show_kind("video")
        panel = self.video_panel if self.kind_var.get() == "video" else self.image_panel
        panel.handle_url(url)


class QueuePage(TFrame):
    """下载队列：列出待下载的链接，可移除、清空、立即开始下一个。

    队列只保存 **URL 与平台**，不冻结清晰度/字幕/保存位置——执行时用的是当时的
    设置。这样队列不必理解每个面板的选项状态，也就不会把核心下载流程卷进来；
    语义写在页面提示里，避免用户误以为选项会被记住。
    """

    MAX_ROWS = 50

    def __init__(self, master, app):
        super().__init__(master, bg_role="BG")
        self.app = app
        canvas = tk.Canvas(self, bg=theme.C().BG, highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._body = TFrame(canvas, bg_role="BG")
        window = canvas.create_window((0, 0), window=self._body, anchor="nw")
        self._body.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

        header = TFrame(self._body, bg_role="BG")
        header.pack(fill="x", pady=(0, 14))
        PageHeader(header, app, i18n.tr("queue"),
                   i18n.tr("queue_subtitle")).pack(side="left", fill="x", expand=True)
        self.start_btn = PillButton(header, i18n.tr("queue_start"),
                                    command=self._on_start_next, primary=True,
                                    font=app.f_btn, width=118, bg_role="BG")
        self.start_btn.pack(side="right")
        self.clear_btn = PillButton(header, i18n.tr("queue_clear"),
                                    command=self._on_clear, font=app.f_btn, width=110,
                                    bg_role="BG")
        self.clear_btn.pack(side="right", padx=(0, 8))

        toggle_row = TFrame(self._body, bg_role="BG")
        toggle_row.pack(fill="x", pady=(0, 10))
        self.autostart_var = tk.BooleanVar(value=app.queue_autostart_enabled())
        check = i18n.tr("queue_autostart")
        CheckPill(toggle_row, check, self.autostart_var,
                  command=self._on_autostart_change, font=app.f_btn, bg_role="BG",
                  width=_check_pill_width(app.f_btn, check)).pack(side="left")

        self.rows = TFrame(self._body, bg_role="BG")
        self.rows.pack(fill="both", expand=True)
        self.refresh()

    def on_shown(self):
        self.refresh()

    def refresh(self):
        for child in self.rows.winfo_children():
            child.destroy()
        items = self.app.queue_items()[:self.MAX_ROWS]
        has_items = bool(items)
        self.start_btn.configure_state("normal" if has_items else "disabled")
        self.clear_btn.configure_state("normal" if has_items else "disabled")
        self.autostart_var.set(self.app.queue_autostart_enabled())
        if not items:
            card = GlassCard(self.rows)
            card.set_height(112)
            card.pack(fill="x")
            TLabel(card.body, text=i18n.tr("queue_empty"), font=self.app.f_section,
                   bg_role="CARD").pack(anchor="w")
            _pack_wrapped_hint(card.body, self.app, i18n.tr("queue_empty_hint"),
                               bg_role="CARD", pady=(6, 0))
            return
        for index, item in enumerate(items):
            self._build_row(index, item)

    def _build_row(self, index, item):
        card = GlassCard(self.rows)
        # 实测内容需要 95px，而 126 的卡片只给出 93px，会裁掉一点（同历史页的坑）
        card.set_height(132)
        card.pack(fill="x", pady=(0, 10))
        TLabel(card.body, text=ellipsize(str(item.get("url") or ""), self.app.f_body,
                                        520),
               font=self.app.f_body, bg_role="CARD", anchor="w").pack(
            anchor="w", fill="x")
        meta = [i18n.tr("queue") + f" {index + 1}",
                platform_label(item.get("platform"))]
        TLabel(card.body, text="　·　".join(bit for bit in meta if bit),
               font=self.app.f_small, bg_role="CARD", fg_role="TEXT_SUB",
               anchor="w").pack(anchor="w", fill="x", pady=(2, 8))
        PillButton(card.body, i18n.tr("queue_remove"),
                   command=lambda i=index: self._on_remove(i), font=self.app.f_btn,
                   width=96, bg_role="CARD").pack(anchor="w")

    def _on_remove(self, index):
        items = self.app.queue_items()
        if 0 <= index < len(items):
            items.pop(index)
            self.app.set_queue_items(items)

    def _on_start_next(self):
        self.app.advance_queue(force=True)

    def _on_autostart_change(self):
        self.app.set_queue_autostart(bool(self.autostart_var.get()))

    def _on_clear(self):
        if not self.app.queue_items():
            return
        if not messagebox.askyesno(i18n.tr("queue"),
                                   i18n.tr("queue_clear_confirm"), parent=self.app):
            return
        self.app.clear_queue()


class HistoryPage(TFrame):
    """下载历史：列出最近完成的文件，可直接打开或定位到所在文件夹。"""

    MAX_ROWS = 50

    def __init__(self, master, app):
        super().__init__(master, bg_role="BG")
        self.app = app
        canvas = tk.Canvas(self, bg=theme.C().BG, highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._body = TFrame(canvas, bg_role="BG")
        window = canvas.create_window((0, 0), window=self._body, anchor="nw")
        self._body.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))

        header = TFrame(self._body, bg_role="BG")
        header.pack(fill="x", pady=(0, 14))
        PageHeader(header, app, i18n.tr("history"),
                   i18n.tr("history_subtitle")).pack(side="left", fill="x", expand=True)
        self.clear_btn = PillButton(header, i18n.tr("history_clear"),
                                    command=self._on_clear, font=app.f_btn, width=110,
                                    bg_role="BG")
        self.clear_btn.pack(side="right")
        self.clear_btn.configure_state("disabled")

        self.rows = TFrame(self._body, bg_role="BG")
        self.rows.pack(fill="both", expand=True)
        self.refresh()

    def on_shown(self):
        # 每次进入都刷新：下载可能发生在别的页面上
        self.refresh()

    def refresh(self):
        for child in self.rows.winfo_children():
            child.destroy()
        entries = self.app.download_history()[:self.MAX_ROWS]
        self.clear_btn.configure_state("normal" if entries else "disabled")
        if not entries:
            self._build_empty()
            return
        for entry in entries:
            self._build_row(entry)

    def _build_empty(self):
        card = GlassCard(self.rows)
        card.set_height(112)
        card.pack(fill="x")
        TLabel(card.body, text=i18n.tr("history_empty"), font=self.app.f_section,
               bg_role="CARD").pack(anchor="w")
        _pack_wrapped_hint(card.body, self.app, i18n.tr("history_empty_hint"),
                           bg_role="CARD", pady=(6, 0))

    def _build_row(self, entry):
        path = str(entry.get("path") or "")
        card = GlassCard(self.rows)
        # GlassCard 会把内部 body 的高度写死成「卡片高度 - 内边距」，内容超出
        # 就被裁掉（实测 96 时 body 只有 63px，而内容需要 95px，按钮整排看不见），
        # 所以这里按「标题 + 元信息 + 按钮行」实测高度留足。
        card.set_height(132)
        card.pack(fill="x", pady=(0, 10))
        TLabel(card.body, text=os.path.basename(path) or path, font=self.app.f_body,
               bg_role="CARD", anchor="w").pack(anchor="w", fill="x")
        meta = [str(bit) for bit in (platform_label(entry.get("platform")),
                                     _format_history_time(entry.get("time")),
                                     os.path.dirname(path)) if bit]
        TLabel(card.body, text="　·　".join(meta), font=self.app.f_small,
               bg_role="CARD", fg_role="TEXT_SUB", anchor="w").pack(
            anchor="w", fill="x", pady=(2, 8))
        row = TFrame(card.body, bg_role="CARD")
        row.pack(anchor="w")
        PillButton(row, i18n.tr("open_file"), command=lambda p=path: self._open_file(p),
                   font=self.app.f_btn, width=104, bg_role="CARD").pack(side="left")
        PillButton(row, i18n.tr("open_folder"),
                   command=lambda p=path: open_in_file_manager(p),
                   font=self.app.f_btn, width=130, bg_role="CARD").pack(
            side="left", padx=(8, 0))

    def _open_file(self, path):
        if not os.path.exists(path):
            messagebox.showinfo(i18n.tr("history"), i18n.tr("file_missing"),
                                parent=self.app)
            return
        open_path(path)

    def _on_clear(self):
        if not self.app.download_history():
            return
        if not messagebox.askyesno(i18n.tr("history"),
                                   i18n.tr("history_clear_confirm"), parent=self.app):
            return
        self.app.clear_history()
        self.refresh()


class SettingsPage(TFrame):
    def __init__(self, master, app):
        super().__init__(master, bg_role="BG")
        self.app = app
        f = app
        self._scroll_canvas = tk.Canvas(
            self, bg=theme.C().BG, highlightthickness=0, bd=0)
        # 登记「按内容自适应高度」的卡片；必须在建第一张卡片之前就绪，
        # 否则先 _track_card 后初始化会 AttributeError（真实踩过）
        self._fit_cards = {}
        self._card_heights = {}
        self._scrollbar = tk.Scrollbar(self, orient="vertical",
                                       command=self._scroll_canvas.yview)
        self._scroll_canvas.configure(yscrollcommand=self._scrollbar.set)
        self._scroll_canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar.pack(side="right", fill="y")
        self._scroll_body = TFrame(self._scroll_canvas, bg_role="BG")
        self._scroll_window = self._scroll_canvas.create_window(
            (0, 0), window=self._scroll_body, anchor="nw")
        self._scroll_body.bind(
            "<Configure>",
            lambda e: self._scroll_canvas.configure(
                scrollregion=self._scroll_canvas.bbox("all")))
        self._scroll_canvas.bind(
            "<Configure>",
            lambda e: self._scroll_canvas.itemconfigure(
                self._scroll_window, width=e.width))

        PageHeader(self._scroll_body, app, i18n.tr("settings"), i18n.tr("settings_subtitle")).pack(
            fill="x", pady=(0, 14))

        self._section("settings_section_general")

        # —— 外观 ——
        card1 = GlassCard(self._scroll_body)
        card1.set_height(112)
        card1.pack(fill="x", pady=(0, 12))
        b1 = card1.body
        # 加了字号行之后固定高度会裁切，交给按内容自适应（见 _track_card 说明）
        self._track_card("appearance", card1, b1)
        TLabel(b1, text=i18n.tr("theme"), font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(anchor="w")
        row1 = TFrame(b1, bg_role="CARD")
        row1.pack(fill="x", pady=(10, 0))
        self.theme_var = tk.StringVar(value=theme.mode())
        theme_options = [(i18n.tr("system_theme"), "system"),
                         (i18n.tr("light_theme"), "light"),
                         (i18n.tr("dark_theme"), "dark")]
        self.theme_seg = SegmentedControl(
            row1, theme_options, self.theme_var, command=self._on_theme_change,
            font=f.f_btn, width=_segmented_width(f.f_btn, [t for t, _ in theme_options]))
        self.theme_seg.pack(side="left")
        self.theme_hint = TLabel(row1, text="", font=f.f_small, bg_role="CARD",
                                 fg_role="TEXT_SUB")
        self.theme_hint.pack(side="left", padx=(16, 0))
        self._update_theme_hint()

        font_row = TFrame(b1, bg_role="CARD")
        font_row.pack(fill="x", pady=(10, 0))
        TLabel(font_row, text=i18n.tr("font_size"), font=f.f_body, bg_role="CARD",
               fg_role="TEXT_SUB").pack(side="left", padx=(0, 8))
        # 变量存的是档位值（standard/large/xlarge）而不是显示标签：分段控件的
        # 选项是 (label, value)，变量必须是 value，否则一个分段都匹配不上、
        # 界面永远不会高亮当前档位（真实踩过）
        self.font_scale_var = tk.StringVar(value=app.font_scale())
        font_options = [(self._font_scale_label(v), v) for v in ("standard", "large",
                                                                "xlarge")]
        # 用变量 trace 而不是控件 command：程序化改动（含测试与回滚）同样会生效，
        # 与剪贴板那一行保持一致
        self.font_scale_var.trace_add("write", lambda *a: self._on_font_scale_change())
        self.font_scale_seg = SegmentedControl(
            font_row, font_options, self.font_scale_var, font=f.f_btn,
            width=_segmented_width(f.f_btn, [t for t, _ in font_options]),
            bg_role="CARD")
        self.font_scale_seg.pack(side="left")
        _pack_wrapped_hint(b1, app, i18n.tr("font_size_hint"), bg_role="CARD",
                           pady=(6, 0))

        # —— 界面语言 ——
        card_language = GlassCard(self._scroll_body)
        card_language.set_height(104)
        card_language.pack(fill="x", pady=(0, 12))
        language_body = card_language.body
        self._track_card("language", card_language, language_body)
        TLabel(language_body, text=i18n.tr("language"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        language_row = TFrame(language_body, bg_role="CARD")
        language_row.pack(fill="x", pady=(8, 0))
        current_language = app.settings.get("language", "zh_CN")
        current_language = current_language if current_language in dict(i18n.LANGUAGES) else "zh_CN"
        current_language_name = dict(i18n.LANGUAGES)[current_language]
        self.language_var = tk.StringVar(value=current_language_name)
        self.language_select = PillSelect(
            language_row, self.language_var,
            values=[name for name, _ in i18n.language_options()],
            font=f.f_btn, width=170)
        self.language_select.pack(side="left")
        self.language_var.trace_add("write", lambda *a: self._on_language_change())
        TLabel(language_row, text=i18n.tr("language_hint"), font=f.f_small,
               bg_role="CARD", fg_role="TEXT_SUB").pack(side="left", padx=(16, 0))

        # —— 应用图标 ——
        card_logo = GlassCard(self._scroll_body)
        card_logo.set_height(216)
        card_logo.pack(fill="x", pady=(0, 12))
        bl = card_logo.body
        self._track_card("logo", card_logo, bl)
        TLabel(bl, text=i18n.tr("app_logo"), font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(anchor="w")
        logo_row = TFrame(bl, bg_role="CARD")
        logo_row.pack(anchor="w", pady=(10, 0))
        self._logo_swatches = {}
        current = app.current_logo()
        for key, label_key in LOGO_OPTIONS:
            sw = LogoSwatch(logo_row, i18n.tr(label_key), self._load_logo_thumb(key),
                            command=lambda k=key: self._on_logo_select(k),
                            font=f.f_small)
            sw.pack(side="left", padx=(0, 12))
            sw.set_selected(key == current)
            self._logo_swatches[key] = sw
        TLabel(bl, text=i18n.tr("logo_hint"),
               font=f.f_small, bg_role="CARD", fg_role="TEXT_SUB",
               anchor="w", justify="left").pack(anchor="w", fill="x", pady=(10, 0))

        self._section("settings_section_save")

        # —— 默认保存位置 ——
        card2 = GlassCard(self._scroll_body)
        card2.set_height(104)
        card2.pack(fill="x", pady=(0, 12))
        b2 = card2.body
        self._track_card("save", card2, b2)
        TLabel(b2, text=i18n.tr("default_save"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        row2 = TFrame(b2, bg_role="CARD")
        row2.pack(fill="x", pady=(8, 0))
        self.dir_var = tk.StringVar(value=app.default_save_dir())
        self.dir_var.trace_add("write", lambda *a: self._on_dir_change())
        RoundEntry(row2, textvariable=self.dir_var, font=f.f_body).pack(
            side="left", fill="x", expand=True)
        PillButton(row2, i18n.tr("choose"), command=self._choose_dir,
                   font=f.f_btn, width=74, bg_role="CARD").pack(side="left", padx=(10, 0))

        self._section("settings_section_cookies")

        # —— 登录 Cookie ——
        card3 = GlassCard(self._scroll_body)
        card3.set_height(310)
        card3.pack(fill="x", pady=(0, 12))
        b3 = card3.body
        TLabel(b3, text=i18n.tr("browser_cookie"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        row3 = TFrame(b3, bg_role="CARD")
        row3.pack(fill="x", pady=(10, 0))
        self._cookie_options = [(i18n.tr("no_cookie"), ""), ("Chrome", "chrome"),
                                ("Safari", "safari"), ("Firefox", "firefox"),
                                ("Edge", "edge")]
        cur = app.settings.get("browser_cookies", "")
        cur_label = next((l for l, v in self._cookie_options if v == cur), i18n.tr("no_cookie"))
        self.cookie_var = tk.StringVar(value=cur_label)
        self.cookie_var.trace_add("write", lambda *a: self._on_cookie_change())
        self.cookie_select = PillSelect(row3, self.cookie_var,
                                        values=[l for l, _ in self._cookie_options],
                                        font=f.f_btn, width=150)
        self.cookie_select.pack(side="left")
        _pack_wrapped_hint(b3, app, i18n.tr("cookie_hint"),
                           bg_role="CARD", pady=(10, 0))

        # —— cookies.txt：不读浏览器数据库，无需「完全磁盘访问权限」——
        TLabel(b3, text=i18n.tr("cookie_file"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w", pady=(16, 0))
        row_file = TFrame(b3, bg_role="CARD")
        row_file.pack(fill="x", pady=(8, 0))
        self.cookie_file_var = tk.StringVar(
            value=app.settings.get("cookie_file", "") or i18n.tr("cookie_file_none"))
        self.cookie_file_entry = RoundEntry(row_file, textvariable=self.cookie_file_var,
                                            font=f.f_body)
        self.cookie_file_entry.pack(side="left", fill="x", expand=True)
        # 仅作展示，禁止手动编辑（避免用户输入不存在的路径）
        self.cookie_file_entry.entry.configure(
            state="readonly", readonlybackground=theme.C().FIELD_BG)
        PillButton(row_file, i18n.tr("choose_cookie_file"),
                   command=self._choose_cookie_file,
                   font=f.f_btn, width=110, bg_role="CARD").pack(side="left", padx=(10, 0))
        PillButton(row_file, i18n.tr("clear_cookie_file"),
                   command=self._clear_cookie_file,
                   font=f.f_btn, width=64, bg_role="CARD").pack(side="left", padx=(8, 0))
        cookie_file_hint = i18n.tr("cookie_file_hint")
        if browser_cookies.cookie_file_missing():
            cookie_file_hint = f"{i18n.tr('cookie_file_missing')}\n{cookie_file_hint}"
        self._cookie_file_hint = _pack_wrapped_hint(
            b3, app, cookie_file_hint, bg_role="CARD", pady=(10, 0))

        # —— Cookie 可用性体检结果（打开设置页时后台检测一次）——
        self._cookie_probe = None
        self._cookie_probe_token = 0
        self.cookie_status_label = _pack_wrapped_hint(
            b3, app, "", bg_role="CARD", pady=(10, 0))
        self.cookie_fda_hint = _pack_wrapped_hint(
            b3, app, "", bg_role="CARD", pady=(6, 0))
        self.cookie_fda_btn = PillButton(
            b3, i18n.tr("open_system_settings"), command=self._open_fda_settings,
            font=f.f_btn, width=132, bg_role="CARD")
        self._fda_button_packed = False

        # 说明文字的换行行数随语言/窗口宽度变化，卡片高度按内容实测，
        # 避免英文、日文文案被卡片下沿裁切（固定高度已不够用）。
        self._track_card("cookie", card3, b3)

        # —— 按平台覆盖 Cookie ——
        # 动机：Instagram 需要登录态，而 YouTube / 微博 等不需要；全局开关
        # 一旦打开就会把所有平台都带上 Cookie（早期版本的微博失败即由此而来）。
        card_plat = GlassCard(self._scroll_body)
        card_plat.set_height(340)
        card_plat.pack(fill="x", pady=(0, 12))
        bp = card_plat.body
        TLabel(bp, text=i18n.tr("platform_cookies"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        # 选项：跟随全局 / 不使用 / 具体浏览器
        self._platform_cookie_options = [
            (i18n.tr("cookie_follow_global"), ""),
            (i18n.tr("no_cookie"), browser_cookies.PLATFORM_NONE),
            ("Chrome", "chrome"), ("Safari", "safari"),
            ("Firefox", "firefox"), ("Edge", "edge"),
        ]
        stored = app.settings.get("platform_cookies")
        stored = stored if isinstance(stored, dict) else {}
        self._platform_vars = {}
        for platform, label_key in zip(platforms.PLATFORMS, PLATFORM_LABEL_KEYS):
            row = TFrame(bp, bg_role="CARD")
            row.pack(fill="x", pady=(8, 0))
            TLabel(row, text=i18n.tr(label_key), font=f.f_body, bg_role="CARD",
                   width=14, anchor="w").pack(side="left")
            current = stored.get(platform, "")
            label = next((l for l, v in self._platform_cookie_options
                          if v == current), self._platform_cookie_options[0][0])
            var = tk.StringVar(value=label)
            var.trace_add("write", lambda *a, p=platform: self._on_platform_cookie_change(p))
            self._platform_vars[platform] = var
            PillSelect(row, var, values=[l for l, _ in self._platform_cookie_options],
                       font=f.f_btn, width=150).pack(side="left", padx=(8, 0))
        _pack_wrapped_hint(bp, app, i18n.tr("platform_cookies_hint"),
                           bg_role="CARD", pady=(12, 0))
        self._track_card("platform", card_plat, bp)

        self._section("settings_section_download")

        # —— 下载行为：跳过重复图片 + Dock 提示 ——
        card_dock = GlassCard(self._scroll_body)
        card_dock.set_height(190)
        card_dock.pack(fill="x", pady=(0, 12))
        bdk = card_dock.body
        TLabel(bdk, text=i18n.tr("download_feedback"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")

        clip_row = TFrame(bdk, bg_role="CARD")
        clip_row.pack(fill="x", pady=(10, 0))
        TLabel(clip_row, text=i18n.tr("clipboard_setting"), font=f.f_body,
               bg_role="CARD", fg_role="TEXT_SUB").pack(side="left", padx=(0, 8))
        self.clipboard_var = tk.StringVar(
            value=self._clipboard_action_label(app.settings.get("clipboard_action")))
        self.clipboard_var.trace_add("write", lambda *a: self._on_clipboard_action_change())
        PillSelect(clip_row, self.clipboard_var,
                   values=[self._clipboard_action_label(v)
                           for v in ("suggest", "auto", "off")],
                   font=f.f_btn, width=170).pack(side="left")
        _pack_wrapped_hint(bdk, app, i18n.tr("clipboard_hint"),
                           bg_role="CARD", pady=(6, 0))

        self.skip_var = tk.BooleanVar(
            value=bool(app.settings.get("skip_existing_images", True)))
        self.skip_var.trace_add("write", lambda *a: self._on_skip_existing_change())
        CheckPill(bdk, i18n.tr("skip_existing_images"), self.skip_var,
                  font=f.f_btn, bg_role="CARD",
                  width=_check_pill_width(f.f_btn, i18n.tr("skip_existing_images"))
                  ).pack(anchor="w", pady=(10, 0))
        _pack_wrapped_hint(bdk, app, i18n.tr("skip_existing_hint"),
                           bg_role="CARD", pady=(6, 0))

        self.group_post_var = tk.BooleanVar(
            value=bool(app.settings.get("group_by_post", False)))
        self.group_post_var.trace_add("write", lambda *a: self._on_group_by_post_change())
        CheckPill(bdk, i18n.tr("group_by_post"), self.group_post_var,
                  font=f.f_btn, bg_role="CARD",
                  width=_check_pill_width(f.f_btn, i18n.tr("group_by_post"))
                  ).pack(anchor="w", pady=(10, 0))
        _pack_wrapped_hint(bdk, app, i18n.tr("group_by_post_hint"),
                           bg_role="CARD", pady=(6, 0))

        self.dock_var = tk.BooleanVar(value=bool(app.settings.get("dock_status", True)))
        self.dock_var.trace_add("write", lambda *a: self._on_dock_status_change())
        CheckPill(bdk, i18n.tr("dock_status"), self.dock_var,
                  font=f.f_btn, bg_role="CARD",
                  width=_check_pill_width(f.f_btn, i18n.tr("dock_status"))
                  ).pack(anchor="w", pady=(10, 0))
        _pack_wrapped_hint(bdk, app, i18n.tr("dock_status_hint"),
                           bg_role="CARD", pady=(6, 0))
        self._track_card("dock", card_dock, bdk)

        # —— 字幕（仅视频下载时可用）——
        card_sub = GlassCard(self._scroll_body)
        card_sub.set_height(140)
        card_sub.pack(fill="x", pady=(0, 12))
        bs = card_sub.body
        TLabel(bs, text=i18n.tr("subtitles"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        sub_row = TFrame(bs, bg_role="CARD")
        sub_row.pack(fill="x", pady=(8, 0))
        TLabel(sub_row, text=i18n.tr("subtitle_langs"), font=f.f_body,
               bg_role="CARD", fg_role="TEXT_SUB").pack(side="left", padx=(0, 8))
        self.subtitle_langs_var = tk.StringVar(
            value=app.settings.get("subtitle_langs") or ",".join(core.DEFAULT_SUBTITLE_LANGS))
        self.subtitle_langs_var.trace_add(
            "write", lambda *a: self._on_subtitle_langs_change())
        self.subtitle_langs_entry = RoundEntry(
            sub_row, textvariable=self.subtitle_langs_var, font=f.f_body)
        self.subtitle_langs_entry.pack(side="left", fill="x", expand=True)
        PillButton(sub_row, i18n.tr("choose"), command=self._open_subtitle_picker,
                   font=f.f_btn, width=74, bg_role="CARD").pack(side="left", padx=(10, 0))
        _pack_wrapped_hint(bs, app, i18n.tr("subtitle_langs_hint"),
                           bg_role="CARD", pady=(8, 0))
        self._track_card("subtitles", card_sub, bs)

        self._section("settings_section_advanced")

        # —— 快捷键 ——
        card_keys = GlassCard(self._scroll_body)
        card_keys.set_height(96)
        card_keys.pack(fill="x", pady=(0, 12))
        bk = card_keys.body
        TLabel(bk, text=i18n.tr("shortcuts"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        _pack_wrapped_hint(bk, app, i18n.tr("shortcuts_hint"),
                           bg_role="CARD", pady=(8, 0))
        self._track_card("shortcuts", card_keys, bk)

        # —— yt-dlp 更新：打包的 yt-dlp 会随站点改版失效，这里装最新版到本机 ——
        card_ytdlp = GlassCard(self._scroll_body)
        card_ytdlp.set_height(200)
        card_ytdlp.pack(fill="x", pady=(0, 12))
        by = card_ytdlp.body
        TLabel(by, text=i18n.tr("ytdlp_card"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        self._ytdlp_token = 0
        self._ytdlp_pending = None       # (版本, wheel 直链)，点按钮时决定「检查」还是「更新」
        self.ytdlp_version_label = TLabel(by, text="", font=f.f_body, bg_role="CARD")
        self.ytdlp_version_label.pack(anchor="w", pady=(8, 0))
        self.ytdlp_status_label = TLabel(by, text="", font=f.f_small, bg_role="CARD",
                                        fg_role="TEXT_SUB")
        self.ytdlp_status_label.pack(anchor="w", pady=(4, 0))
        ytdlp_row = TFrame(by, bg_role="CARD")
        ytdlp_row.pack(fill="x", pady=(8, 0))
        self.ytdlp_btn = PillButton(ytdlp_row, i18n.tr("ytdlp_check"),
                                    command=self._on_ytdlp_button, font=f.f_btn, width=140)
        self.ytdlp_btn.pack(side="left")
        _pack_wrapped_hint(by, app, i18n.tr("ytdlp_hint"), bg_role="CARD", pady=(10, 0))
        self._track_card("ytdlp", card_ytdlp, by)
        self._ytdlp_refresh()

        # —— 诊断与反馈 ——
        card_diag = GlassCard(self._scroll_body)
        card_diag.set_height(150)
        card_diag.pack(fill="x", pady=(0, 12))
        bd = card_diag.body
        TLabel(bd, text=i18n.tr("diagnostics"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        diag_row = TFrame(bd, bg_role="CARD")
        diag_row.pack(anchor="w", pady=(10, 0))
        self.diag_btn = PillButton(diag_row, i18n.tr("copy_diagnostics"),
                                   command=self._copy_diagnostics,
                                   font=f.f_btn, width=140, bg_role="CARD")
        self.diag_btn.pack(side="left")
        self.diag_status = TLabel(diag_row, text="", font=f.f_small,
                                  bg_role="CARD", fg_role="TEXT_SUB")
        self.diag_status.pack(side="left", padx=(10, 0))
        _pack_wrapped_hint(bd, app, i18n.tr("diagnostics_hint"),
                           bg_role="CARD", pady=(10, 0))
        self._track_card("diagnostics", card_diag, bd)

        self._bind_scroll_events()

    def on_shown(self):
        """设置页变为可见时触发一次 Cookie 体检。

        刻意不放在 __init__：所有页面在启动时就构建好了，若在构造期体检，
        每次启动都会为「浏览器 Cookie」配置访问一次钥匙串并弹授权框，
        即使用户这次根本没打算下载。
        """
        self.after_idle(self._refresh_cookie_status)

    def _section(self, title_key):
        """分组标题：把十来张卡片切成可扫读的几段。

        不改动卡片的创建顺序，只在既有分组注释的位置插入标题，避免为了「好看」
        而搬动大段布局代码。
        """
        TLabel(self._scroll_body, text=i18n.tr(title_key), font=self.app.f_section,
               bg_role="BG", fg_role="TEXT").pack(anchor="w", pady=(10, 8))

    def _track_card(self, name, card, body):
        """登记一张按内容自适应高度的卡片。

        说明文案的换行行数随语言与窗口宽度变化，固定高度会裁切（英文/日文
        尤其明显），所以这里实测内容高度再回写卡片高度。
        """
        self._fit_cards[name] = (card, body)
        body.bind("<Configure>", lambda _e, n=name: self._fit_card(n), add="+")
        self.after_idle(lambda n=name: self._fit_card(n))

    def _fit_card(self, name):
        entry = self._fit_cards.get(name)
        if entry is None:
            return
        card, body = entry
        try:
            body.update_idletasks()
            needed = body.winfo_reqheight() + 34  # 卡片上下各 16px 内边距 + 描边
        except tk.TclError:
            # 切换语言会重建整个设置页，延迟回调可能落在已销毁的控件上。
            return
        if needed != self._card_heights.get(name):
            self._card_heights[name] = needed
            card.set_height(needed)
        # 高度没变时也要同步一次：set_height 设的值若与画布当前高度相同就不会有
        # <Configure>，body 会停在旧高度把内容裁掉（sync_body 是幂等的）。
        card.sync_body()

    def _fit_cookie_card(self):
        self._fit_card("cookie")

    # ---------- Cookie 体检 / 诊断 ----------

    def _refresh_cookie_status(self):
        """在后台真实加载一次 Cookie，把结论显示在设置页。"""
        if not self.winfo_exists():
            return
        self._cookie_probe_token += 1
        token = self._cookie_probe_token
        self.cookie_status_label.set_fg_role("TEXT_SUB")
        self.cookie_status_label.configure(text=i18n.tr("cookie_checking"))
        self._set_fda_row(False)
        self._fit_cookie_card()

        def work():
            try:
                result = browser_cookies.probe()
            except Exception as exc:  # 体检本身不应让界面崩掉
                result = browser_cookies.ProbeResult(
                    browser_cookies.PROBE_UNREADABLE, detail=str(exc)[:200])
            self.after(0, lambda: self._apply_cookie_status(token, result))

        threading.Thread(target=work, daemon=True).start()

    def _apply_cookie_status(self, token, result):
        # 期间又发起了新的体检，丢弃这次的结果
        if token != getattr(self, "_cookie_probe_token", 0) or not self.winfo_exists():
            return
        self._cookie_probe = result

        if result.status == browser_cookies.PROBE_OK:
            self.cookie_status_label.set_fg_role("OK")
            self.cookie_status_label.configure(
                text=i18n.tr("cookie_ok", n=result.cookie_count))
            self._set_fda_row(False)
        elif result.status == browser_cookies.PROBE_OFF:
            self.cookie_status_label.set_fg_role("TEXT_SUB")
            self.cookie_status_label.configure(text="")
            self._set_fda_row(False)
        else:
            self.cookie_status_label.set_fg_role("ERROR")
            self.cookie_status_label.configure(
                text=i18n.tr("cookie_unreadable_status"))
            self.cookie_fda_hint.configure(text=i18n.tr("cookie_fda_hint"))
            # 只有 macOS 才有「完全磁盘访问权限」这一层，其他平台不加按钮
            self._set_fda_row(sys.platform == "darwin")
        self._fit_cookie_card()

    def _set_fda_row(self, visible):
        """显示/隐藏「打开系统设置」按钮与对应说明。"""
        if visible and not self._fda_button_packed:
            self.cookie_fda_btn.pack(anchor="w", pady=(8, 0))
            self._fda_button_packed = True
        elif not visible and self._fda_button_packed:
            self.cookie_fda_btn.pack_forget()
            self._fda_button_packed = False
            self.cookie_fda_hint.configure(text="")

    def _open_fda_settings(self):
        """跳转到 macOS「完全磁盘访问权限」设置页。"""
        if sys.platform != "darwin":
            return
        try:
            subprocess.Popen(["open", browser_cookies.FDA_SETTINGS_URL])
        except OSError:
            pass

    def _copy_diagnostics(self):
        """把诊断报告复制到剪贴板（生成过程可能较慢，放后台线程）。"""
        self.diag_btn.configure_state("disabled")
        self.diag_status.set_fg_role("TEXT_SUB")
        self.diag_status.configure(text=i18n.tr("cookie_checking"))

        def work():
            try:
                report = diagnostics.build_report(self.app.settings,
                                                  probe=self._cookie_probe)
            except Exception as exc:
                self.after(0, lambda: self._diagnostics_done(
                    None, i18n.tr("diagnostics_failed", message=str(exc)[:120])))
                return
            self.after(0, lambda: self._diagnostics_done(report, None))

        threading.Thread(target=work, daemon=True).start()

    def _diagnostics_done(self, report, error):
        if not self.winfo_exists():
            return
        self.diag_btn.configure_state("normal")
        if error:
            self.diag_status.set_fg_role("ERROR")
            self.diag_status.configure(text=error)
            return
        self.clipboard_clear()
        self.clipboard_append(report)
        self.diag_status.set_fg_role("OK")
        self.diag_status.configure(text=i18n.tr("diagnostics_copied"))
        self._fit_card("diagnostics")

    def _walk_widgets(self, widget=None):
        """遍历设置页控件，确保鼠标停在任意控件上都能滚动。"""
        widget = self if widget is None else widget
        yield widget
        for child in widget.winfo_children():
            yield from self._walk_widgets(child)

    def _bind_scroll_events(self):
        """把滚轮事件绑定到设置页的每个子控件，而非只依赖 bind_all。"""
        for widget in self._walk_widgets():
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(sequence, self._on_scroll_event, add="+")

    def _on_scroll_event(self, event):
        self.scroll_with_wheel(event)
        return "break"

    def scroll_by_delta(self, delta):
        """按滚动量移动 Canvas；兼容鼠标整格和触控板小数值。"""
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
        self._scroll_canvas.yview_scroll(units, "units")

    def scroll_with_wheel(self, event):
        """处理设置页的鼠标滚轮与 macOS 触控板双指滚动。"""
        button = getattr(event, "num", None)
        if button in (4, 5):
            self._scroll_canvas.yview_scroll(-1 if button == 4 else 1, "units")
            return
        self.scroll_by_delta(getattr(event, "delta", 0))

    def _load_logo_thumb(self, key, size=64):
        """加载某款 logo 的 64px 预览缩略图；缺失时返回 None。"""
        try:
            from PIL import Image, ImageTk

            path = logo_icon_path(key, 512)
            if not path:
                return None
            return ImageTk.PhotoImage(
                Image.open(path).resize((size, size), Image.LANCZOS))
        except Exception:
            return None

    def _on_logo_select(self, key):
        if key == self.app.current_logo():
            return
        self.app.set_app_logo(key)
        for k, sw in self._logo_swatches.items():
            sw.set_selected(k == key)

    def _update_theme_hint(self):
        if theme.mode() == "system":
            cur = i18n.tr("dark_theme" if theme.is_dark() else "light_theme")
            self.theme_hint.configure(text=i18n.tr("system_appearance", cur=cur))
        else:
            self.theme_hint.configure(text="")

    def _on_theme_change(self):
        self.app.set_theme(self.theme_var.get())
        self._update_theme_hint()

    def _choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get() or core.DEFAULT_SAVE_DIR)
        if path:
            self.app.apply_save_dir(path)

    def _on_dir_change(self):
        self.app.apply_save_dir(self.dir_var.get().strip())

    def _on_cookie_change(self):
        label = self.cookie_var.get()
        value = next((v for l, v in self._cookie_options if l == label), "")
        self.app.settings["browser_cookies"] = value
        settings.save(self.app.settings)
        core.set_browser_cookies(value)
        images.set_browser_cookies(value)
        self._refresh_cookie_status()  # 设置一变立刻重新体检

    def _apply_cookie_file(self, path):
        """写入 cookies.txt 设置并同步给两个引擎。"""
        self.app.settings["cookie_file"] = path
        settings.save(self.app.settings)
        core.set_cookie_file(path)
        images.set_cookie_file(path)
        self.cookie_file_var.set(path or i18n.tr("cookie_file_none"))
        self._refresh_cookie_status()

    def _on_platform_cookie_change(self, platform):
        """按平台 Cookie 覆盖变更：只保存非默认项，保持 settings.json 干净。"""
        var = self._platform_vars.get(platform)
        if var is None:
            return
        label = var.get()
        value = next((v for l, v in self._platform_cookie_options if l == label), "")
        mapping = self.app.settings.get("platform_cookies")
        mapping = dict(mapping) if isinstance(mapping, dict) else {}
        if value:
            mapping[platform] = value
        else:
            mapping.pop(platform, None)
        self.app.settings["platform_cookies"] = mapping
        settings.save(self.app.settings)
        browser_cookies.set_platform_overrides(mapping)

    def _font_scale_label(self, value):
        return i18n.tr({"large": "font_large",
                        "xlarge": "font_xlarge"}.get(value, "font_standard"))

    def _on_font_scale_change(self):
        value = self.font_scale_var.get()
        if not self.app.set_font_scale(value):
            # 被拒绝（下载中）或与当前一致：把选择回滚到实际生效的档位
            self.font_scale_var.set(self.app.font_scale())

    def _clipboard_action_label(self, value):
        return i18n.tr({
            "auto": "clipboard_auto",
            "off": "clipboard_off",
        }.get(str(value or "").strip().lower(), "clipboard_suggest"))

    def _clipboard_action_value(self, label):
        for value in ("suggest", "auto", "off"):
            if self._clipboard_action_label(value) == label:
                return value
        return "suggest"

    def _on_clipboard_action_change(self):
        value = self._clipboard_action_value(self.clipboard_var.get())
        self.app.settings["clipboard_action"] = value
        settings.save(self.app.settings)
        if value != "suggest":
            # 关掉/改成自动后，残留的建议条要收起来
            self.app._hide_clipboard_suggestion()

    # ---------- yt-dlp 更新 ----------

    def _ytdlp_refresh(self):
        version, source = ytdlp_update.active_version()
        label = i18n.tr("ytdlp_source_managed" if source == "managed"
                        else "ytdlp_source_bundled")
        self.ytdlp_version_label.configure(text=i18n.tr(
            "ytdlp_current", version=version or i18n.tr("diag_unknown"), source=label))

    def _on_ytdlp_button(self):
        if self._ytdlp_pending:
            self._start_ytdlp_update(*self._ytdlp_pending)
        else:
            self._start_ytdlp_check()

    def _start_ytdlp_check(self):
        if not self.winfo_exists():
            return
        self._ytdlp_token += 1
        token = self._ytdlp_token
        self._ytdlp_pending = None
        self.ytdlp_btn.configure_state("disabled")
        self.ytdlp_status_label.configure(text=i18n.tr("ytdlp_checking"))

        def work():
            try:
                version, url = ytdlp_update.check_latest()
                error = None
            except Exception as exc:      # 网络问题不该让界面崩掉
                version = url = None
                error = str(exc)
            self.after(0, lambda: self._apply_ytdlp_check(token, version, url, error))

        threading.Thread(target=work, daemon=True).start()

    def _apply_ytdlp_check(self, token, version, url, error):
        if token != self._ytdlp_token or not self.winfo_exists():
            return
        self.ytdlp_btn.configure_state("normal")
        if error:
            self.ytdlp_status_label.configure(text=error)
            return
        current, _source = ytdlp_update.active_version()
        if not ytdlp_update.is_newer(version, current):
            self.ytdlp_btn.set_text(i18n.tr("ytdlp_check"))
            self.ytdlp_status_label.configure(
                text=i18n.tr("ytdlp_is_latest", version=version))
            return
        self._ytdlp_pending = (version, url)
        self.ytdlp_btn.set_text(i18n.tr("ytdlp_update_to", version=version))
        self.ytdlp_status_label.configure(text=i18n.tr("ytdlp_found", version=version))

    def _start_ytdlp_update(self, version, url):
        if not self.winfo_exists():
            return
        self._ytdlp_token += 1
        token = self._ytdlp_token
        self.ytdlp_btn.configure_state("disabled")
        self.ytdlp_status_label.configure(text=i18n.tr("ytdlp_updating"))

        def work():
            try:
                ytdlp_update.download_and_install(version, url)
                error = None
            except Exception as exc:
                error = str(exc)
            self.after(0, lambda: self._apply_ytdlp_update(token, version, error))

        threading.Thread(target=work, daemon=True).start()

    def _apply_ytdlp_update(self, token, version, error):
        if token != self._ytdlp_token or not self.winfo_exists():
            return
        self.ytdlp_btn.configure_state("normal")
        self._ytdlp_pending = None
        self.ytdlp_btn.set_text(i18n.tr("ytdlp_check"))
        if error:
            self.ytdlp_status_label.configure(text=i18n.tr("ytdlp_failed", message=error))
            return
        self.ytdlp_status_label.configure(text=i18n.tr("ytdlp_updated", version=version))
        self._ytdlp_refresh()

    def _on_group_by_post_change(self):
        self.app.settings["group_by_post"] = bool(self.group_post_var.get())
        settings.save(self.app.settings)

    def _on_skip_existing_change(self):
        self.app.settings["skip_existing_images"] = bool(self.skip_var.get())
        settings.save(self.app.settings)

    def _open_subtitle_picker(self):
        """勾选常用语言；列表外的自定义代码会原样保留。"""
        picker = SubtitleLanguagePicker(self, self.app, self.subtitle_langs_var.get())
        chosen = picker.show()
        if chosen is None:
            return
        self.subtitle_langs_var.set(chosen)
        self._fit_card("subtitles")

    def _on_subtitle_langs_change(self):
        """字幕语言：留空时回落到默认语言，避免 yt-dlp 的 "all" 拉上百种自动字幕。"""
        value = (self.subtitle_langs_var.get() or "").strip()
        self.app.settings["subtitle_langs"] = value
        settings.save(self.app.settings)

    def _on_dock_status_change(self):
        self.app.settings["dock_status"] = bool(self.dock_var.get())
        settings.save(self.app.settings)
        if not self.dock_var.get():
            taskbar.clear(window=self.app)  # 关掉后立刻清掉残留角标

    def _choose_cookie_file(self):
        path = filedialog.askopenfilename(
            initialdir=os.path.dirname(self.app.settings.get("cookie_file", "")) or None,
            title=i18n.tr("cookie_file"),
            filetypes=[("cookies.txt", "*.txt"), ("All files", "*.*")])
        if path:
            self._apply_cookie_file(path)

    def _clear_cookie_file(self):
        self._apply_cookie_file("")

    def _on_language_change(self):
        name = self.language_var.get()
        code = next((code for label, code in i18n.language_options()
                     if label == name), i18n.language())
        self.app.set_language(code)


# ====================================================================
# 主窗口
# ====================================================================


def _taskbar_progress(app, percent):
    """下载进度写入 Dock 角标 / Windows 任务栏进度（用户开关打开时）。"""
    if app.dock_status_enabled():
        taskbar.set_progress(percent, window=app)


def _taskbar_done(app):
    """完成提示：角标 ✓ / 弹跳，或在任务栏闪烁提示。"""
    if app.dock_status_enabled():
        taskbar.set_done(window=app)


def _taskbar_clear(app):
    """清除 Dock 角标 / 任务栏进度。"""
    if app.dock_status_enabled():
        taskbar.clear(window=app)


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


def _active_panel(page):
    """返回页面当前可见的面板（视频或图片）。

    MediaPlatformPage 用 kind_var 在视频/图片面板间切换，快捷键与状态记录
    都要作用在当前真正可见的那个面板上。
    """
    if page is None:
        return None
    kind = getattr(page, "kind_var", None)
    if kind is not None:
        return page.video_panel if kind.get() == "video" else page.image_panel
    return getattr(page, "video_panel", None)


def _panel_url_entry(panel):
    """取面板里链接输入框的内层 tk.Entry（RoundEntry 是 Canvas 包装）。"""
    round_entry = getattr(panel, "url_entry", None)
    return getattr(round_entry, "entry", None) if round_entry is not None else None


def open_in_file_manager(path):
    if not path:
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def _refresh_queue_page_safe(app):
    """刷新队列页（页面可能还没建好或已销毁）。"""
    page = getattr(app, "pages", {}).get("queue")
    refresh = getattr(page, "refresh", None)
    if callable(refresh):
        try:
            refresh()
        except tk.TclError:
            pass


def open_path(path):
    """用系统默认程序打开文件本身（而不是在文件管理器里定位它）。"""
    if not path:
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - 只打开用户自己刚下载的文件
    else:
        subprocess.Popen(["xdg-open", path])


def _format_history_time(timestamp):
    """把时间戳格式化成 ``2026-10-10 18:30``；脏数据返回空串。"""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(timestamp)))
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


class App(tk.Tk):
    DEFAULT_GEOMETRY = "1020x745"
    MIN_SIZE = (920, 700)

    def __init__(self):
        super().__init__()
        self.settings = settings.load()
        i18n.set_language(self.settings.get("language", "zh_CN"))
        theme.set_mode(self.settings.get("theme", "system"))
        core.set_browser_cookies(self.settings.get("browser_cookies", ""))
        images.set_browser_cookies(self.settings.get("browser_cookies", ""))
        core.set_cookie_file(self.settings.get("cookie_file", ""))
        images.set_cookie_file(self.settings.get("cookie_file", ""))
        browser_cookies.set_platform_overrides(
            self.settings.get("platform_cookies", {}))

        self.title(i18n.tr("app_title"))
        self.minsize(*self.MIN_SIZE)
        # 恢复上次的窗口尺寸/位置；位置够不着（例如拔掉副屏）时只恢复尺寸。
        windowing.restore(self, self.settings.get("window_geometry", ""),
                          self.DEFAULT_GEOMETRY, *self.MIN_SIZE)
        self.configure(bg=theme.C().BG)
        self.apply_app_logo()  # 按持久化设置恢复窗口 + Dock 图标

        self._last_clipboard = None
        self._setup_fonts()
        self._build_layout()
        self.show_page("youtube")
        self._native_scroll_monitor = None
        self._native_scroll_pending = 0.0
        self._native_scroll_target = None
        self._native_scroll_window_title = i18n.tr("app_title")
        # Tk 的滚轮事件不会自动传给 Canvas 的父容器；绑定到
        # 顶层窗口，才能在设置页任意子控件上使用滚轮/触控板。
        self.bind_all("<MouseWheel>", self._on_mouse_wheel, add="+")
        self.bind_all("<Button-4>", self._on_mouse_wheel, add="+")
        self.bind_all("<Button-5>", self._on_mouse_wheel, add="+")
        self._bind_shortcuts()
        self._install_native_scroll_monitor()

        # macOS：menubar 常驻图标；启用后关闭窗口仅隐藏，应用保留在状态栏
        self.menubar = menubar.create(
            self,
            icon_path=asset_path("menubar_template.png"),
            fallback_icon_path=asset_path("app_icon.png"),
        )
        if self.menubar is not None:
            self._apply_menubar_icon()  # 按持久化设置换成所选 logo 的彩色小图
            self.protocol("WM_DELETE_WINDOW", self._on_close_window)
            # Cmd+Q / Dock 退出走同一确认流程；点击 Dock 图标恢复窗口
            try:
                self.createcommand("::tk::mac::Quit", self.quit_from_menubar)
                self.createcommand("::tk::mac::ReopenApplication",
                                   self.show_main_window)
            except tk.TclError:
                pass
            # menubar 菜单点击不能直接调用 Tk（见 app/menubar.py docstring），
            # 由这里的轮询取走 pending 动作并执行
            self.after(150, self._poll_menubar)

        self.after(300, self._check_clipboard)
        # 首次安装才弹说明；延迟一点让主窗口先画出来
        self.after(self.ONBOARDING_DELAY_MS, self._maybe_show_welcome)
        self.bind("<FocusIn>", self._on_focus_in)

    # ---------- 下载队列 ----------

    QUEUE_LIMIT = 50

    def queue_items(self):
        """待下载的链接，按加入顺序。脏数据一律跳过，避免整页打不开。"""
        entries = self.settings.get("download_queue")
        if not isinstance(entries, list):
            return []
        return [e for e in entries if isinstance(e, dict) and e.get("url")]

    def set_queue_items(self, items):
        self.settings["download_queue"] = list(items)[-self.QUEUE_LIMIT:]
        settings.save(self.settings)
        self._refresh_queue_page()

    def enqueue_url(self, url, platform=""):
        """把链接加入队列；只存 URL 与平台，不冻结选项（见 QueuePage 说明）。"""
        url = (url or "").strip()
        if not url:
            return False
        items = self.queue_items()
        items.append({"url": url, "platform": platform or detect_platform(url)})
        self.set_queue_items(items)
        return True

    def dequeue_item(self, index=0):
        items = self.queue_items()
        if not items or not 0 <= index < len(items):
            return None
        item = items.pop(index)
        self.set_queue_items(items)
        return item

    def clear_queue(self):
        self.set_queue_items([])

    def group_by_post_enabled(self):
        """是否把同一个帖子的文件放进以帖子 ID 命名的子文件夹。"""
        return bool(self.settings.get("group_by_post", False))

    def _post_download_dir(self, base, url, title="", fallback_id="", kind=""):
        """按设置决定本次下载的实际目录。

        开启后：``base/<帖子 ID>_<标题摘要>/``，再按媒体类型分（``image``/``gif``/
        ``video``/``audio``）。帖子 ID 以**链接**为准：同一帖子的图片流程与视频流程
        才会算出同一个目录；链接里没有 ID 时才用探测结果兜底。
        """
        if not self.group_by_post_enabled():
            return base
        post_id = images.post_id_from_url(url) or fallback_id
        folder = post_folder_name(post_id, title)
        if not folder:
            return base
        path = os.path.join(base, folder)
        return os.path.join(path, kind) if kind else path

    def queue_autostart_enabled(self):
        return bool(self.settings.get("queue_autostart", True))

    def set_queue_autostart(self, enabled):
        self.settings["queue_autostart"] = bool(enabled)
        settings.save(self.settings)

    def notify_download_finished(self):
        """面板结束一次下载：稍后推进队列。

        必须延后：在面板自己的复位流程里直接再发起下载会重入，状态会互相踩。
        """
        self.after(200, self.advance_queue)

    def advance_queue(self, force=False):
        """执行队列里的下一个任务，返回是否真的取了任务。

        force=True 用于「开始下一个」按钮：忽略自动继续开关。任意面板忙碌时都
        不取任务，免得两个下载互相抢进度条。
        """
        if not force and not self.queue_autostart_enabled():
            return False
        if any(getattr(panel, "_busy", False) for panel in self._all_panels()):
            return False
        item = self.dequeue_item(0)
        if item is None:
            return False
        url = str(item.get("url") or "")
        page = self.pages.get(item.get("platform") or detect_platform(url))
        if page is None:
            return False
        if getattr(page, "kind_var", None) is not None:
            page.kind_var.set("video")       # 队列里排的都是视频链接
        panel = _active_panel(page)
        if panel is None or not hasattr(panel, "on_download"):
            return False
        self.show_page(self._page_key(page))
        panel.url_entry.set(url)
        panel.on_download()
        _refresh_queue_page_safe(self)
        return True

    def _page_key(self, page):
        for key, candidate in self.pages.items():
            if candidate is page:
                return key
        return self.current_page

    def _refresh_queue_page(self):
        _refresh_queue_page_safe(self)

    # ---------- 下载历史 ----------

    HISTORY_LIMIT = 50

    def download_history(self):
        """下载历史，最新的在前。

        容错处理：settings 里可能残留非列表或被手工改坏的条目，一律跳过，
        否则历史页会因为一条脏数据整页打不开。
        """
        entries = self.settings.get("download_history")
        if not isinstance(entries, list):
            return []
        return [e for e in entries if isinstance(e, dict) and e.get("path")]

    def record_download(self, paths, title="", platform="", timestamp=None):
        """记录一次完成的下载。

        同一个路径只保留最新一条（重复下载不会把列表撑满），整体截断到
        HISTORY_LIMIT 条。批量下载（播放列表 / 图集）会产生多条记录，这是
        有意的：每条都能单独「打开文件」。
        """
        if isinstance(paths, (str, bytes)):
            paths = [paths]
        fresh = [str(p) for p in (paths or ()) if p]
        if not fresh:
            return
        when = time.time() if timestamp is None else float(timestamp)
        replaced = set(fresh)
        kept = [e for e in self.download_history() if str(e.get("path")) not in replaced]
        added = [{"path": path, "title": title or "", "platform": platform or "",
                  "time": when} for path in fresh]
        self.settings["download_history"] = (added + kept)[:self.HISTORY_LIMIT]
        settings.save(self.settings)

    def clear_history(self):
        self.settings["download_history"] = []
        settings.save(self.settings)

    # ---------- 首次使用说明 ----------

    ONBOARDING_DELAY_MS = 600

    def needs_onboarding(self):
        """只有「全新安装」（还没有设置文件）且没看过说明时才需要引导。"""
        return bool(self.settings.get("_first_run")) and not bool(
            self.settings.get("onboarding_done"))

    def _maybe_show_welcome(self):
        if not self.needs_onboarding():
            return
        WelcomeDialog(self, self, on_dismiss=self._finish_onboarding).show()

    def _finish_onboarding(self):
        self.settings["onboarding_done"] = True
        settings.save(self.settings)

    def dock_status_enabled(self):
        """是否把进度/完成状态写到 Dock 角标（用户可关闭，且仅 macOS 有效）。"""
        return (bool(self.settings.get("dock_status", True))
                and taskbar.enabled_platform())

    def default_save_dir(self, key=None):
        """返回指定下载方式的目录，未单独设置时回退到全局默认目录。"""
        if key:
            save_dirs = self.settings.get("save_dirs", {})
            if isinstance(save_dirs, dict):
                path = save_dirs.get(key, "")
                if isinstance(path, str) and path.strip():
                    return path.strip()
        return self.settings.get("save_dir") or core.DEFAULT_SAVE_DIR

    def _on_mouse_wheel(self, event):
        if self.current_page != "settings":
            return
        page = self.pages.get("settings")
        if page is not None:
            page.scroll_with_wheel(event)
            return "break"

    def _install_native_scroll_monitor(self):
        """监听 macOS 原生滚轮，覆盖 Tk 未转发鼠标滚轮的情况。"""
        if not _NATIVE_SCROLL_AVAILABLE:
            return
        try:
            self._native_scroll_monitor = (
                NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                    NSEventMaskScrollWheel, self._on_native_scroll))
            self.after(20, self._poll_native_scroll)
        except Exception:
            self._native_scroll_monitor = None

    def _native_scroll_target_for_current_page(self):
        if self.current_page == "settings":
            return self.pages.get("settings")
        page = self.pages.get(self.current_page)
        image_panel = getattr(page, "image_panel", None)
        picker = getattr(image_panel, "picker", None)
        if picker is not None and picker.accepts_native_scroll():
            return picker
        return None

    def _on_native_scroll(self, event):
        """AppKit 回调只记录数据，不在回调中调用 Tk。"""
        try:
            window = event.window()
            if window is None or window.title() != self._native_scroll_window_title:
                return event
            if event.locationInWindow().x < 188:
                return event
            target = self._native_scroll_target_for_current_page()
            if target is None:
                return event
            delta = float(event.scrollingDeltaY())
            if not delta:
                return event
            self._native_scroll_target = target
            self._native_scroll_pending += delta
            return None
        except Exception:
            return event

    def _poll_native_scroll(self):
        """在 Tk 主线程消费 AppKit 回调积累的滚动量。"""
        delta = self._native_scroll_pending
        self._native_scroll_pending = 0.0
        target = self._native_scroll_target
        self._native_scroll_target = None
        if delta and target is not None and target.winfo_exists():
            target.scroll_by_delta(delta)
        if self.winfo_exists():
            self.after(20, self._poll_native_scroll)

    def apply_download_dir(self, key, path):
        """只保存并更新一个下载方式的目录，不影响其它方式。"""
        new = (path or "").strip()
        save_dirs = self.settings.get("save_dirs", {})
        if not isinstance(save_dirs, dict):
            save_dirs = {}
        else:
            save_dirs = dict(save_dirs)

        if new:
            save_dirs[key] = new
        else:
            # 清空专属目录后恢复使用全局默认目录。
            save_dirs.pop(key, None)
        self.settings["save_dirs"] = save_dirs
        settings.save(self.settings)

        if hasattr(self, "pages"):
            display = self.default_save_dir(key)
            for panel in self._all_panels():
                panel_key = getattr(panel, "_save_dir_key", None)
                if callable(panel_key) and panel_key() == key:
                    var = getattr(panel, "dir_var", None)
                    if var is not None and var.get() != display:
                        var.set(display)

    def apply_save_dir(self, path):
        """设置全局默认目录，并更新尚未设置专属目录的面板。

        - 在 settings.json 中持久化（空字符串表示「跟随默认 ~/Downloads」）。
        - 已有专属目录的 VideoPanel / ImagePanel 不会被覆盖。
        - 设置页输入框同步为原始值（保留空字符串语义）。
        正在进行的下载不受影响：core 在启动下载时已快照 save_dir 参数。
        早期返回避免 trace 回调引发递归。
        """
        new = (path or "").strip()
        if new == self.settings.get("save_dir", ""):
            return
        self.settings["save_dir"] = new
        settings.save(self.settings)
        display = new or core.DEFAULT_SAVE_DIR
        if hasattr(self, "pages"):
            for panel in self._all_panels():
                panel_key = getattr(panel, "_save_dir_key", None)
                has_specific_dir = False
                if callable(panel_key):
                    save_dirs = self.settings.get("save_dirs", {})
                    specific_dir = (save_dirs.get(panel_key(), "")
                                     if isinstance(save_dirs, dict) else "")
                    has_specific_dir = isinstance(specific_dir, str) \
                        and bool(specific_dir.strip())
                var = getattr(panel, "dir_var", None)
                if not has_specific_dir and var is not None and var.get() != display:
                    var.set(display)
            settings_page = self.pages.get("settings")
            if settings_page is not None:
                sv = getattr(settings_page, "dir_var", None)
                if sv is not None and sv.get() != new:
                    sv.set(new)

    # ---------- 应用图标（窗口 + macOS Dock，可在设置页切换） ----------

    def current_logo(self):
        key = self.settings.get("app_logo", "classic")
        return key if any(k == key for k, _ in LOGO_OPTIONS) else "classic"

    def apply_app_logo(self):
        """按当前设置应用窗口图标、macOS Dock 图标与 menubar 状态栏图标。

        图标缺失或 Pillow 不可用时静默跳过；.app 在 Finder 中的静态
        .icns 不受影响（无法运行时更换，保持默认经典蓝）。
        """
        key = self.current_logo()
        win_path = logo_icon_path(key, 512) or asset_path("app_icon.png")
        try:
            from PIL import Image, ImageTk

            if win_path:
                self._icon_image = ImageTk.PhotoImage(Image.open(win_path))
                self.iconphoto(True, self._icon_image)
        except Exception:
            pass
        menubar.set_dock_icon(logo_icon_path(key, 1024) or win_path)
        self._apply_menubar_icon()

    def _apply_menubar_icon(self):
        """把 menubar 状态栏图标换成当前 logo 的彩色小图。

        menubar 未创建（非 macOS / 初始化早期）或小图缺失时静默跳过，
        此时保留默认单色模板图。
        """
        mb = getattr(self, "menubar", None)
        if mb is None:
            return
        path = logo_menubar_path(self.current_logo())
        if path:
            mb.update_icon(path)

    def set_app_logo(self, key):
        """设置页切换 logo：持久化并立即应用。"""
        self.settings["app_logo"] = key
        settings.save(self.settings)
        self.apply_app_logo()

    # ---------- 字体 ----------

    # 字号档位 -> 缩放系数。只做整数像素取整，避免半像素字发虚。
    FONT_SCALES = {"standard": 1.0, "large": 1.15, "xlarge": 1.3}
    # 各字体的基准字号
    FONT_BASES = (("f_title", 22, "bold"), ("f_sub", 12, ""), ("f_section", 12, "bold"),
                  ("f_body", 13, ""), ("f_body_bold", 13, "bold"), ("f_btn", 13, ""),
                  ("f_btn_big", 15, "bold"), ("f_small", 11, ""), ("f_side", 13, ""),
                  ("f_side_title", 16, "bold"))

    def font_scale(self):
        """当前字号档位，非法值回退到标准。"""
        value = str(self.settings.get("font_scale", "standard") or "").strip().lower()
        return value if value in self.FONT_SCALES else "standard"

    def _font_size(self, base):
        return max(9, round(base * self.FONT_SCALES[self.font_scale()]))

    def _setup_fonts(self):
        base = tkfont.nametofont("TkDefaultFont")
        family = base.actual("family")
        for attr, size, weight in self.FONT_BASES:
            setattr(self, attr, tkfont.Font(
                family=family, size=self._font_size(size), weight=weight or "normal"))

    def set_font_scale(self, value):
        """切换界面字号并重建界面；下载进行中不打断任务。

        重建是必要的：卡片高度、省略号宽度都是按字体量出来的，只改字体对象
        而不重建，布局会留着旧尺寸（GlassCard 还会把内容裁掉）。
        """
        value = value if value in self.FONT_SCALES else "standard"
        if value == self.font_scale():
            return False
        if any(getattr(panel, "_busy", False) for panel in self._all_panels()):
            messagebox.showinfo(i18n.tr("font_size"), i18n.tr("language_busy"),
                                parent=self)
            return False
        current_page = self.current_page or "youtube"
        self.settings["font_scale"] = value
        settings.save(self.settings)
        for child in (getattr(self, "sidebar", None), getattr(self, "content", None)):
            if child is not None:
                child.destroy()
        self._setup_fonts()
        self._build_layout()
        self.show_page(current_page)
        return True

    # ---------- 布局 ----------

    def _build_layout(self):
        self.sidebar = TFrame(self, bg_role="SIDEBAR_BG", width=188)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        TLabel(self.sidebar, text=i18n.tr("download_center"), font=self.f_side_title,
               bg_role="SIDEBAR_BG", fg_role="SIDEBAR_TEXT_ACTIVE").pack(
            anchor="w", padx=18, pady=(22, 2))
        TLabel(self.sidebar, text=i18n.tr("media_subtitle"), font=self.f_small,
               bg_role="SIDEBAR_BG", fg_role="SIDEBAR_TEXT").pack(
            anchor="w", padx=18, pady=(0, 16))

        self.content = TFrame(self, bg_role="BG")
        self.content.pack(side="left", fill="both", expand=True)

        # 剪贴板建议条：默认隐藏，检测到链接时才插到页面区域上方。
        # 之所以放在全局而不是各页面里：检测到的链接可能属于任何平台，而且
        # 切换页面本身就是要避免的打扰。
        self._clipboard_suggestion = ""
        self.clip_bar = TFrame(self.content, bg_role="BG")
        self.clip_label = TLabel(self.clip_bar, text="", anchor="w",
                                 font=self.f_small, bg_role="BG", fg_role="TEXT")
        self.clip_label.pack(side="left", fill="x", expand=True)
        PillButton(self.clip_bar, i18n.tr("clipboard_fill"),
                   command=self._accept_clipboard_suggestion, primary=True,
                   font=self.f_btn, width=124, bg_role="BG").pack(side="right")
        PillButton(self.clip_bar, i18n.tr("clipboard_ignore"),
                   command=self._dismiss_clipboard_suggestion,
                   font=self.f_btn, width=88, bg_role="BG").pack(side="right", padx=(0, 8))

        self.page_holder = TFrame(self.content, bg_role="BG")
        self.page_holder.pack(fill="both", expand=True, padx=26, pady=(20, 16))

        self.pages = {
            "youtube": VideoPlatformPage(
                self.page_holder, self, i18n.tr("youtube_title"),
                i18n.tr("youtube_subtitle"), platform="youtube"),
            "x": MediaPlatformPage(
                self.page_holder, self, i18n.tr("x_title"), i18n.tr("x_subtitle"),
                platform="x"),
            "instagram": MediaPlatformPage(
                self.page_holder, self, i18n.tr("instagram_title"), i18n.tr("instagram_subtitle"),
                platform="instagram",
                hint=i18n.tr("instagram_hint")),
            "douyin": VideoPlatformPage(
                self.page_holder, self, i18n.tr("douyin_title"),
                i18n.tr("douyin_subtitle"), platform="douyin",
                hint=i18n.tr("douyin_hint")),
            "bilibili": VideoPlatformPage(
                self.page_holder, self, i18n.tr("bilibili_title"),
                i18n.tr("bilibili_subtitle"), platform="bilibili",
                hint=i18n.tr("bilibili_hint")),
            "weibo": MediaPlatformPage(
                self.page_holder, self, i18n.tr("weibo_title"),
                i18n.tr("weibo_subtitle"), platform="weibo",
                hint=i18n.tr("weibo_hint"), show_kind_switch=False),
            "queue": QueuePage(self.page_holder, self),
            "history": HistoryPage(self.page_holder, self),
            "settings": SettingsPage(self.page_holder, self),
        }

        self.nav_items = {}
        nav_defs = [("youtube", i18n.tr("youtube_nav"), "youtube"),
                    ("x", i18n.tr("x_nav"), "x"),
                    ("instagram", i18n.tr("instagram_nav"), "instagram"),
                    ("douyin", i18n.tr("douyin_nav"), "douyin"),
                    ("bilibili", i18n.tr("bilibili_nav"), "bilibili"),
                    ("weibo", i18n.tr("weibo_nav"), "weibo")]
        for key, label, icon in nav_defs:
            item = SidebarItem(self.sidebar, label, icon,
                               command=lambda k=key: self.show_page(k),
                               font=self.f_side)
            item.pack(padx=12, pady=2)
            self.nav_items[key] = item

        # 设置入口固定在侧边栏底部；历史排在它上面
        settings_item = SidebarItem(self.sidebar, i18n.tr("settings"), "settings",
                                    command=lambda: self.show_page("settings"),
                                    font=self.f_side)
        settings_item.pack(side="bottom", padx=12, pady=14)
        self.nav_items["settings"] = settings_item

        history_item = SidebarItem(self.sidebar, i18n.tr("history"), "history",
                                   command=lambda: self.show_page("history"),
                                   font=self.f_side)
        history_item.pack(side="bottom", padx=12, pady=2)
        self.nav_items["history"] = history_item

        queue_item = SidebarItem(self.sidebar, i18n.tr("queue"), "queue",
                                 command=lambda: self.show_page("queue"),
                                 font=self.f_side)
        queue_item.pack(side="bottom", padx=12, pady=2)
        self.nav_items["queue"] = queue_item

        self.current_page = None

    def set_language(self, code):
        """保存语言并重建静态界面；下载进行中时不破坏当前任务。"""
        if code == i18n.language():
            return
        if any(getattr(panel, "_busy", False)
               for panel in self._all_panels()):
            messagebox.showinfo(i18n.tr("language"), i18n.tr("language_busy"), parent=self)
            return
        current_page = self.current_page or "youtube"
        i18n.set_language(code)
        self.settings["language"] = i18n.language()
        settings.save(self.settings)
        self.title(i18n.tr("app_title"))
        self._native_scroll_window_title = i18n.tr("app_title")
        if self.menubar is not None:
            self.menubar.update_language()
        for child in (getattr(self, "sidebar", None), getattr(self, "content", None)):
            if child is not None:
                child.destroy()
        self._build_layout()
        self.show_page(current_page)

    def show_page(self, key):
        if key == self.current_page:
            return
        for k, page in self.pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for k, item in self.nav_items.items():
            item.set_active(k == key)
        self.current_page = key
        # 页面首次可见时才做昂贵/有副作用的准备（例如设置页的 Cookie 体检，
        # 会访问钥匙串，放在启动路径上会平白弹出授权框）。
        shown = self.pages.get(key)
        on_shown = getattr(shown, "on_shown", None)
        if callable(on_shown):
            on_shown()

    # ---------- 主题 ----------

    def set_theme(self, mode):
        self.settings["theme"] = mode
        settings.save(self.settings)
        theme.set_mode(mode)
        self.configure(bg=theme.C().BG)
        apply_theme_tree(self)

    # ---------- menubar（macOS 状态栏图标） ----------

    def _all_panels(self):
        """收集所有平台页中的视频/图片面板。"""
        out = []
        for page in self.pages.values():
            for attr in ("video_panel", "image_panel"):
                panel = getattr(page, attr, None)
                if panel is not None:
                    out.append(panel)
        return out

    def _active_download_panels(self):
        """正在下载或已暂停（持有断点临时文件）的面板。"""
        return [p for p in self._all_panels()
                if getattr(p, "_downloading", False) or getattr(p, "_paused", False)]

    def menubar_status_text(self):
        """menubar 菜单的只读状态文案；只读纯 Python 状态，可在菜单跟踪期调用。"""
        for p in self._all_panels():
            if isinstance(p, VideoPanel):
                if p._downloading:
                    return i18n.tr("downloading_status", percent=p._last_percent)
                if p._paused:
                    return i18n.tr("paused_status", percent=p._last_percent)
            elif isinstance(p, ImagePanel) and p._downloading:
                return i18n.tr("downloading_images_status")
        return i18n.tr("idle")

    def _poll_menubar(self):
        """轮询 menubar 菜单点击产生的动作（show / quit）。"""
        req = self.menubar.take_pending()
        # 先续期轮询：若后续 quit 销毁了窗口，未触发的定时器随解释器终止
        self.after(150, self._poll_menubar)
        if req == "show":
            self.show_main_window()
        elif req == "quit":
            self.quit_from_menubar()
        elif req == "clipboard":
            self.paste_from_menubar()
        elif req == "last_folder":
            self.open_last_download_folder()
        elif req == "history":
            self.show_main_window()
            self.show_page("history")
        elif req == "settings":
            self.show_main_window()
            self.show_page("settings")

    def _on_close_window(self):
        """关闭窗口（红色按钮）→ 隐藏窗口，应用保留在 menubar 运行。"""
        self.withdraw()

    def paste_from_menubar(self):
        """menubar「粘贴剪贴板链接」：显式动作，忽略设置与去重记录。"""
        self.show_main_window()
        self._last_clipboard = None
        self._check_clipboard(force=True)

    def open_last_download_folder(self):
        """menubar「打开上次下载文件夹」：定位到最近一条下载记录。"""
        entries = self.download_history()
        if entries:
            open_in_file_manager(str(entries[0].get("path") or ""))

    def show_main_window(self):
        """menubar「显示主窗口」：恢复窗口并带到前台。"""
        self.deiconify()
        self.lift()
        self.focus_force()

    def quit_from_menubar(self):
        """menubar「退出」：有下载进行中时先确认并取消，再真正退出。"""
        active = self._active_download_panels()
        if active:
            self.show_main_window()
            if not messagebox.askyesno(
                    i18n.tr("quit"), i18n.tr("quit_confirm"), parent=self):
                return
            for p in active:
                p.on_cancel()
            self._wait_downloads_then_destroy(tries=25)
        else:
            self._destroy_app()

    def _wait_downloads_then_destroy(self, tries):
        """等待取消生效（最多 5 秒），避免残留进程/临时文件后退出。"""
        if tries <= 0 or not self._active_download_panels():
            self._destroy_app()
            return
        self.after(200, lambda: self._wait_downloads_then_destroy(tries - 1))

    def _destroy_app(self):
        self._save_window_geometry()
        if self._native_scroll_monitor is not None:
            try:
                NSEvent.removeMonitor_(self._native_scroll_monitor)
            except Exception:
                pass
        if self.menubar is not None:
            self.menubar.remove()
        self.destroy()

    # ---------- 窗口几何与快捷键 ----------

    def _save_window_geometry(self):
        """记录窗口尺寸/位置，下次启动恢复。"""
        try:
            # 隐藏（macOS 上关窗只是 withdraw）或最大化时不要覆盖用户的手动尺寸
            if self.state() != "normal":
                return
            geometry = self.geometry()
        except tk.TclError:
            return
        if not geometry or geometry == self.settings.get("window_geometry"):
            return
        self.settings["window_geometry"] = geometry
        settings.save(self.settings)

    def _bind_shortcuts(self):
        """全局快捷键。

        ⌘V 不在这里绑定：Tk 的 Entry 已由系统虚拟事件 <<Paste>> 处理，
        再绑一次会破坏输入框与其它控件的正常粘贴。
        """
        bindings = (
            ("<Command-Return>", self._shortcut_probe),
            ("<Control-Return>", self._shortcut_probe),
            ("<Command-d>", self._shortcut_download),
            ("<Control-d>", self._shortcut_download),
            ("<Command-p>", self._shortcut_pause),
            ("<Control-p>", self._shortcut_pause),
            ("<Command-l>", self._shortcut_focus_url),
            ("<Control-l>", self._shortcut_focus_url),
            ("<Command-comma>", self._shortcut_settings),
            ("<Escape>", self._shortcut_cancel),
        )
        for sequence, handler in bindings:
            self.bind_all(sequence, handler, add="+")

    def _current_page(self):
        return self.pages.get(self.current_page)

    def _shortcut_probe(self, event=None):
        """⌘↩：对当前页面的链接立刻解析。"""
        panel = _active_panel(self._current_page())
        probe = getattr(panel, "on_probe", None)
        if callable(probe):
            probe()
            return "break"
        return None

    def _shortcut_download(self, event=None):
        """⌘D：开始下载（等同点「开始下载」）。

        正在下载时 on_download 自己会早退，所以这里不必再判状态；
        空闲且未解析时它会先自动解析再下载，与按钮行为一致。
        """
        panel = _active_panel(self._current_page())
        if panel is None:
            return None
        start = getattr(panel, "on_download", None)
        if callable(start):
            start()
            return "break"
        return None

    def _shortcut_pause(self, event=None):
        """⌘P：暂停 / 继续。

        图片批量下载没有暂停能力（界面本来就没有该按钮），此时什么都不做；
        空闲时也返回 None，避免抢掉其他控件对 ⌘P 的处理。
        """
        panel = _active_panel(self._current_page())
        if panel is None:
            return None
        if bool(getattr(panel, "_paused", False)):
            handler = getattr(panel, "on_resume", None)
        elif bool(getattr(panel, "_downloading", False)):
            handler = getattr(panel, "on_pause", None)
        else:
            return None
        if callable(handler):
            handler()
            return "break"
        return None

    def _shortcut_focus_url(self, event=None):
        """⌘L：把焦点放到链接输入框并全选，便于直接覆盖粘贴。"""
        panel = _active_panel(self._current_page())
        entry = _panel_url_entry(panel)
        if entry is None:
            return None
        entry.focus_set()
        try:
            entry.selection_range(0, "end")
        except tk.TclError:
            pass
        return "break"

    def _shortcut_settings(self, event=None):
        """⌘,：打开设置页（macOS 习惯用法）。"""
        self.show_page("settings")
        return "break"

    def _shortcut_cancel(self, event=None):
        """Esc：取消进行中的解析或下载（仅在确实忙碌时接管）。"""
        panel = _active_panel(self._current_page())
        if panel is None:
            return None
        busy = any(getattr(panel, flag, False)
                   for flag in ("_busy", "_downloading", "_paused"))
        if not busy:
            return None
        cancel = getattr(panel, "on_cancel", None)
        if callable(cancel):
            cancel()
            return "break"
        return None

    # ---------- 剪贴板 ----------

    def _on_focus_in(self, event):
        if event.widget is self:
            # 回到应用就该清掉 Dock 上的进度/完成角标，避免长期残留
            _taskbar_clear(self)
            self._check_clipboard()

    # ---------- 剪贴板 ----------

    def clipboard_action(self) -> str:
        """剪贴板处理方式：suggest（默认）/ auto / off。

        默认改成「先提示」：旧行为会在每次窗口获得焦点时切页、覆盖输入框并直接
        联网解析，用户无法拒绝也无法关闭。
        """
        action = str(self.settings.get("clipboard_action", "suggest") or "").strip().lower()
        return action if action in ("suggest", "auto", "off") else "suggest"

    def _show_clipboard_suggestion(self, url):
        self._clipboard_suggestion = url
        self.clip_label.configure(
            text=i18n.tr("clipboard_found", url=ellipsize(url, self.f_small, 460)))
        if not self.clip_bar.winfo_manager():
            # before=self.page_holder：page_holder 先被 pack 过，不指定就会排到它下面
            self.clip_bar.pack(side="top", fill="x", padx=26, pady=(14, 0),
                               before=self.page_holder)

    def _hide_clipboard_suggestion(self):
        self._clipboard_suggestion = ""
        self.clip_bar.pack_forget()

    def _accept_clipboard_suggestion(self):
        url = self._clipboard_suggestion
        self._hide_clipboard_suggestion()
        if not url:
            return
        platform = detect_platform(url)
        if platform in self.pages:
            self.show_page(platform)
            self.pages[platform].handle_url(url)

    def _dismiss_clipboard_suggestion(self):
        self._hide_clipboard_suggestion()

    def _check_clipboard(self, force=False):
        """检测剪贴板里的链接。

        force=True 用于 menubar 的「粘贴剪贴板链接」：这是用户的显式动作，所以
        跳过「不处理」设置与「同一段文本不重复提示」的去重记录，直接填入并解析。
        """
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        if not force and text == self._last_clipboard:
            return
        self._last_clipboard = text
        action = self.clipboard_action()
        if action == "off" and not force:
            return
        url = extract_supported_url(text)
        if not url:
            if force:
                self._set_status_anywhere(i18n.tr("clipboard_no_link"), error=True)
            return
        platform = detect_platform(url)
        if platform not in self.pages:
            if force:
                self._set_status_anywhere(i18n.tr("clipboard_no_link"), error=True)
            return
        if force or action == "auto":
            # 保留旧行为，但改成需要用户显式选择
            self.show_page(platform)
            self.pages[platform].handle_url(url)
            return
        self._show_clipboard_suggestion(url)

    def _set_status_anywhere(self, text, error=False):
        """把状态写到当前可见面板（menubar 动作发生在哪个页面并不确定）。"""
        panel = _active_panel(self._current_page())
        setter = getattr(panel, "_set_status", None)
        if callable(setter):
            setter(text, error=error)


def run():
    app = App()
    # 验证钩子：环境变量指向的脚本在 App 创建后执行（命名空间含 app），
    # 用于打包态自动化验证（截图/切换设置）。日常使用不受影响。
    script = os.environ.get("VD_TEST_SCRIPT")
    if script and os.path.isfile(script):
        try:
            with open(script, encoding="utf-8") as fh:
                exec(compile(fh.read(), script, "exec"), {"app": app})
        except Exception:
            pass
    app.mainloop()
