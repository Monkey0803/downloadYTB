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
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

from . import (about, applog, appupdate, browser_cookies, core, diagnostics, i18n, images,
               menubar, netproxy, postaction, settings_io,
               ytdlp_update,
               platforms, settings, taskbar, theme, windowing)
from .dialogs import (SUBTITLE_LANGUAGES, EntryPicker, SubtitleLanguagePicker,
                      TextDetailDialog, WelcomeDialog)
from .panels import ImagePanel, VideoPanel, enqueue_from_entry
from .fspath import (LOGO_OPTIONS, asset_path, logo_icon_path, logo_menubar_path,
                     open_in_file_manager, open_path)
from .taskbar import _taskbar_clear, _taskbar_done, _taskbar_progress
from .urlutil import (PLATFORM_LABEL_KEYS, URL_RE, _URL_RE, _sanitize_filename,
                      detect_platform, download_dir_key, extract_supported_url,
                      is_weibo_video_url, normalize_kind, parse_links,
                      platform_label, post_folder_name, queue_key)
from .widgets import (
    CheckPill, GlassCard, ImagePicker, LogoSwatch, PillButton, PillSelect,
    RoundEntry, RoundProgress, SegmentedControl, SidebarItem, TFrame, TLabel,
    apply_theme_tree, ellipsize, ellipsize_filename, hand_cursor,
    _check_pill_width, _pack_wrapped_hint, _segmented_width,
)

_NATIVE_SCROLL_AVAILABLE = False
if sys.platform == "darwin":
    try:
        from AppKit import NSEvent, NSEventMaskScrollWheel
        _NATIVE_SCROLL_AVAILABLE = True
    except Exception:
        pass


# ====================================================================
# 平台页面
# ====================================================================


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
        self.batch_btn = PillButton(toggle_row, i18n.tr("queue_paste_many"),
                                    command=self._on_paste_many, font=app.f_btn,
                                    width=110, bg_role="BG")
        self.batch_btn.pack(side="right")
        self.status_label = TLabel(toggle_row, text="", font=app.f_small, bg_role="BG",
                                   fg_role="TEXT_SUB")
        self.status_label.pack(side="right", padx=(0, 10))
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
        self.status_label.configure(text="")
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
        if normalize_kind(item.get("kind")) == "image":
            # 只有图片任务才标出来：视频是老行为，不必给每行都加噪音
            meta.append(i18n.tr("image_segment"))
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

    def _on_paste_many(self):
        """把剪贴板里的多个链接一次性排队（忽略其中的说明文字与重复链接）。"""
        try:
            text = self.app.clipboard_get()
        except tk.TclError:
            text = ""
        urls = parse_links(text)
        if not urls:
            self._set_queue_status(i18n.tr("queue_no_links"), ok=False)
            return
        added, skipped = self.app.enqueue_urls(urls)
        if not added:
            self._set_queue_status(
                i18n.tr("queue_all_duplicated", skipped=skipped), ok=False)
            return
        self._set_queue_status(
            i18n.tr("queue_batch_added", n=added, skipped=skipped), ok=True)

    def _set_queue_status(self, text, ok=True):
        if not self.winfo_exists():
            return
        self.status_label.set_fg_role("TEXT_SUB" if ok else "ERROR")
        self.status_label.configure(text=text)

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

        # —— 嵌入到文件 ——
        card_embed = GlassCard(self._scroll_body)
        card_embed.set_height(200)
        card_embed.pack(fill="x", pady=(0, 12))
        be = card_embed.body
        TLabel(be, text=i18n.tr("embed_card"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        self.embed_vars = {}
        for index, (key, text_key) in enumerate((
                ("embed_subtitles", "embed_subtitles"),
                ("embed_thumbnail", "embed_thumbnail"),
                ("embed_metadata", "embed_metadata"))):
            variable = tk.BooleanVar(value=bool(app.settings.get(key, False)))
            variable.trace_add(
                "write", lambda *a, k=key, v=variable: self._on_embed_change(k, v))
            self.embed_vars[key] = variable
            CheckPill(be, i18n.tr(text_key), variable, font=f.f_btn,
                      bg_role="CARD",
                      width=_check_pill_width(f.f_btn, i18n.tr(text_key))
                      ).pack(anchor="w", pady=(10 if index == 0 else 8, 0))
        _pack_wrapped_hint(be, app, i18n.tr("embed_hint"), bg_role="CARD",
                           pady=(8, 0))
        self._track_card("embed", card_embed, be)

        # —— 完成后动作 ——
        card_after = GlassCard(self._scroll_body)
        card_after.set_height(170)
        card_after.pack(fill="x", pady=(0, 12))
        ba = card_after.body
        TLabel(ba, text=i18n.tr("after_download_card"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        after_row = TFrame(ba, bg_role="CARD")
        after_row.pack(fill="x", pady=(10, 0))
        TLabel(after_row, text=i18n.tr("after_download_setting"), font=f.f_body,
               bg_role="CARD", fg_role="TEXT_SUB").pack(side="left", padx=(0, 8))
        self.after_download_var = tk.StringVar(
            value=postaction.label(app.settings.get("after_download")))
        self.after_download_var.trace_add(
            "write", lambda *a: self._on_after_download_change())
        self.after_download_select = PillSelect(
            after_row, self.after_download_var,
            values=[postaction.label(code) for code in postaction.OPTIONS],
            font=f.f_btn, width=190)
        self.after_download_select.pack(side="left")
        _pack_wrapped_hint(ba, app, i18n.tr("after_download_hint"),
                           bg_role="CARD", pady=(8, 0))
        self._track_card("after_download", card_after, ba)

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

        # —— 网络代理 ——
        card_proxy = GlassCard(self._scroll_body)
        card_proxy.set_height(200)
        card_proxy.pack(fill="x", pady=(0, 12))
        bp = card_proxy.body
        TLabel(bp, text=i18n.tr("proxy_card"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        proxy_row = TFrame(bp, bg_role="CARD")
        proxy_row.pack(fill="x", pady=(10, 0))
        self.proxy_var = tk.StringVar(value=str(app.settings.get("proxy", "")))
        self.proxy_var.trace_add("write", lambda *a: self._on_proxy_change())
        RoundEntry(proxy_row, textvariable=self.proxy_var,
                   font=f.f_body).pack(side="left", fill="x", expand=True)
        self.proxy_test_btn = PillButton(proxy_row, i18n.tr("proxy_test"),
                                         command=self._test_proxy, font=f.f_btn,
                                         width=110, bg_role="CARD")
        self.proxy_test_btn.pack(side="left", padx=(10, 0))
        self.proxy_status = TLabel(bp, text="", font=f.f_small, bg_role="CARD",
                                   fg_role="TEXT_SUB")
        self.proxy_status.pack(anchor="w", pady=(6, 0))
        _pack_wrapped_hint(bp, app, i18n.tr("proxy_hint"), bg_role="CARD",
                           pady=(8, 0))
        self._track_card("proxy", card_proxy, bp)
        self._refresh_proxy_status()

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
        self.log_btn = PillButton(diag_row, i18n.tr("open_log"),
                                  command=self._open_log, font=f.f_btn, width=110,
                                  bg_role="CARD")
        self.log_btn.pack(side="left", padx=(8, 0))
        self.about_btn = PillButton(diag_row, i18n.tr("about_button"),
                                    command=self._open_about, font=f.f_btn,
                                    width=90, bg_role="CARD")
        self.about_btn.pack(side="left", padx=(8, 0))
        self.diag_status = TLabel(diag_row, text="", font=f.f_small,
                                  bg_role="CARD", fg_role="TEXT_SUB")
        self.diag_status.pack(side="left", padx=(10, 0))
        _pack_wrapped_hint(bd, app, i18n.tr("diagnostics_hint"),
                           bg_role="CARD", pady=(10, 0))
        self._track_card("diagnostics", card_diag, bd)

        # —— 版本与更新 ——
        card_ver = GlassCard(self._scroll_body)
        card_ver.set_height(210)
        card_ver.pack(fill="x", pady=(0, 12))
        bv = card_ver.body
        TLabel(bv, text=i18n.tr("version_card"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        ver_row = TFrame(bv, bg_role="CARD")
        ver_row.pack(fill="x", pady=(10, 0))
        self.version_label = TLabel(ver_row, text="", font=f.f_body,
                                    bg_role="CARD", fg_role="TEXT")
        self.version_label.pack(side="left")
        self.update_check_btn = PillButton(ver_row, i18n.tr("check_updates"),
                                           command=self._check_updates, font=f.f_btn,
                                           width=96, bg_role="CARD")
        self.update_check_btn.pack(side="left", padx=(12, 0))
        self.release_btn = PillButton(ver_row, i18n.tr("update_open_page"),
                                      command=self._open_release_page, font=f.f_btn,
                                      width=110, bg_role="CARD")
        self.release_btn.pack(side="left", padx=(8, 0))
        self.version_status = TLabel(bv, text="", font=f.f_small, bg_role="CARD",
                                     fg_role="TEXT_SUB")
        self.version_status.pack(anchor="w", pady=(8, 0))
        self.auto_update_var = tk.BooleanVar(
            value=bool(app.settings.get("check_updates", True)))
        self.update_auto_check = CheckPill(
            bv, i18n.tr("update_auto"), self.auto_update_var,
            command=self._on_auto_check_toggle, width=140, bg_role="CARD")
        self.update_auto_check.pack(anchor="w", pady=(8, 0))
        _pack_wrapped_hint(bv, app, i18n.tr("update_hint"), bg_role="CARD",
                           pady=(8, 0))
        self._track_card("version", card_ver, bv)

        # —— 设置导入导出 ——
        card_io = GlassCard(self._scroll_body)
        card_io.set_height(160)
        card_io.pack(fill="x", pady=(0, 12))
        bio = card_io.body
        TLabel(bio, text=i18n.tr("settings_io_card"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        io_row = TFrame(bio, bg_role="CARD")
        io_row.pack(anchor="w", pady=(10, 0))
        self.export_btn = PillButton(io_row, i18n.tr("settings_export"),
                                     command=self._export_settings, font=f.f_btn,
                                     width=110, bg_role="CARD")
        self.export_btn.pack(side="left")
        self.import_btn = PillButton(io_row, i18n.tr("settings_import"),
                                     command=self._import_settings, font=f.f_btn,
                                     width=110, bg_role="CARD")
        self.import_btn.pack(side="left", padx=(8, 0))
        _pack_wrapped_hint(bio, app, i18n.tr("settings_io_hint"), bg_role="CARD",
                           pady=(10, 0))
        self._track_card("settings_io", card_io, bio)
        self._refresh_version_status()

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
        applog.log_event("cookie_probe", status=result.status,
                         count=getattr(result, "cookie_count", 0))

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

    # ---------- 网络代理 ----------

    def _refresh_proxy_status(self):
        """显示当前**实际生效**的代理（设置优先，其次环境变量）。"""
        self.proxy_status.set_fg_role("TEXT_SUB")
        self.proxy_status.configure(
            text=i18n.tr("proxy_current", value=netproxy.describe()))

    def _on_proxy_change(self):
        """边输入边校验：非法值不落盘，也不影响当前生效的代理。"""
        value, error = netproxy.validate(self.proxy_var.get())
        if error:
            self.proxy_status.set_fg_role("ERROR")
            self.proxy_status.configure(text=error)
            return
        self.app.settings["proxy"] = value
        settings.save(self.app.settings)
        netproxy.set_current(value)
        applog.log_event("proxy_changed", proxy=netproxy.describe())
        self._refresh_proxy_status()

    def _test_proxy(self):
        """用一次轻量请求验证代理是否真的可用（走 yt-dlp 更新器的那条路径）。"""
        if not self.winfo_exists():
            return
        self._proxy_test_token = getattr(self, "_proxy_test_token", 0) + 1
        token = self._proxy_test_token
        self.proxy_test_btn.configure_state("disabled")
        self.proxy_status.set_fg_role("TEXT_SUB")
        self.proxy_status.configure(text=i18n.tr("proxy_testing"))

        def work():
            try:
                version, _url = ytdlp_update.check_latest()
                error = None
            except Exception as exc:
                version = None
                error = str(exc)
            self.after(0, lambda: self._apply_proxy_test(token, version, error))

        threading.Thread(target=work, daemon=True).start()

    def _apply_proxy_test(self, token, version, error):
        if token != getattr(self, "_proxy_test_token", 0) or not self.winfo_exists():
            return
        self.proxy_test_btn.configure_state("normal")
        if error:
            self.proxy_status.set_fg_role("ERROR")
            self.proxy_status.configure(text=i18n.tr("proxy_failed", message=error))
            applog.log_event("proxy_test_failed", error=error)
            return
        self.proxy_status.set_fg_role("OK")
        self.proxy_status.configure(text=i18n.tr("proxy_ok", version=version))
        applog.log_event("proxy_test_ok", version=version)

    # ---------- 版本与更新 ----------

    def _refresh_version_status(self):
        """显示当前版本与已知的新版本状态。"""
        self.version_label.configure(
            text=i18n.tr("version_current", version=appupdate.current_version()))
        found = str(self.app.settings.get("update_available") or "")
        if found:
            self.version_status.set_fg_role("OK")
            self.version_status.configure(
                text=i18n.tr("update_available", version=found))
            self.release_btn.configure_state("normal")
            return
        self.release_btn.configure_state("disabled")
        self.version_status.set_fg_role("TEXT_SUB")
        checked = appupdate.format_checked_at(
            self.app.settings.get("last_update_check"))
        self.version_status.configure(
            text=i18n.tr("update_last_checked", time=checked) if checked
            else i18n.tr("update_never_checked"))

    def _on_auto_check_toggle(self):
        enabled = bool(self.auto_update_var.get())
        self.app.settings["check_updates"] = enabled
        settings.save(self.app.settings)
        applog.log_event("update_auto_toggle", enabled=enabled)

    def _check_updates(self):
        """后台查询 GitHub 公开 Releases 接口（手动触发）。"""
        if not self.winfo_exists():
            return
        self._update_token = getattr(self, "_update_token", 0) + 1
        token = self._update_token
        self.update_check_btn.configure_state("disabled")
        self.version_status.set_fg_role("TEXT_SUB")
        self.version_status.configure(text=i18n.tr("checking_updates"))
        self._start_update_worker(token, announce=True)

    def _auto_check_updates(self):
        """启动时最多每 24 小时自动检查一次（可在设置里关闭）。"""
        if not appupdate.should_auto_check(self.app.settings):
            return
        self._start_update_worker(getattr(self, "_update_token", 0), announce=False)

    def _start_update_worker(self, token, announce):
        def work():
            result = appupdate.check()
            self.after(0, lambda: self._apply_update_check(token, result, announce))

        threading.Thread(target=work, daemon=True).start()

    def _apply_update_check(self, token, result, announce=True):
        # 自动检查不改动设置页当前显示的文案，避免用户正在看时被改掉
        if announce:
            if token != getattr(self, "_update_token", 0):
                return
            if not self.winfo_exists():
                return
            self.update_check_btn.configure_state("normal")
        status = result.get("status")
        found = result.get("version") if status == "update" else ""
        self.app.settings["update_available"] = found or ""
        appupdate.mark_checked(self.app.settings)
        settings.save(self.app.settings)
        applog.log_event("update_check", status=status,
                         version=result.get("version") or "",
                         error=result.get("error") or "")
        if status == "update":
            self._announce_update(result.get("version"), result.get("url"))
        if not announce or not self.winfo_exists():
            return
        if status == "error":
            self.version_status.set_fg_role("ERROR")
            self.version_status.configure(
                text=i18n.tr("update_check_failed", message=result.get("error") or ""))
            return
        if status == "current":
            self.version_status.set_fg_role("OK")
            self.version_status.configure(text=i18n.tr("update_is_latest"))
            return
        self._refresh_version_status()

    def _announce_update(self, version, url):
        """发现新版本：菜单栏加一项、状态栏提一次（不弹模态框打断用户）。"""
        self.app._release_url = url or appupdate.RELEASES_PAGE
        try:
            if self.app.menubar is not None:
                self.app.menubar.update_update_hint(version)
        except Exception:
            pass
        if getattr(self.app, "_update_notified", "") != version:
            self.app._update_notified = version
            # App 级入口（当前页面若不是面板则静默跳过，见 _set_status_anywhere）
            self.app._set_status_anywhere(
                i18n.tr("update_notice_status", version=version))

    def _open_release_page(self):
        url = getattr(self.app, "_release_url", "") or appupdate.RELEASES_PAGE
        applog.log_event("open_release_page", url=url)
        open_path(url)

    def _on_embed_change(self, key, variable):
        """三个「嵌入到文件」开关：改一个存一个（默认全关，开才会改动产物）。"""
        enabled = bool(variable.get())
        if bool(self.app.settings.get(key, False)) == enabled:
            return
        self.app.settings[key] = enabled
        settings.save(self.app.settings)
        applog.log_event("embed_setting", key=key, enabled=enabled)

    def _on_after_download_change(self):
        value = postaction.value_for_label(self.after_download_var.get())
        self.app.settings["after_download"] = value
        settings.save(self.app.settings)
        applog.log_event("after_download_setting", action=value)

    def _export_settings(self):
        """把用户偏好导出成 JSON 文件。"""
        path = filedialog.asksaveasfilename(
            title=i18n.tr("settings_export"), parent=self.app,
            defaultextension=".json",
            initialfile="videodownloader-settings.json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            settings_io.write_file(path, settings_io.exportable(self.app.settings))
        except OSError as exc:
            applog.log_event("settings_export_failed", error=str(exc)[:200])
            messagebox.showerror(
                i18n.tr("settings_io_card"),
                i18n.tr("settings_export_failed", message=str(exc)[:200]),
                parent=self.app)
            return
        applog.log_event("settings_exported", path=path)
        messagebox.showinfo(i18n.tr("settings_io_card"),
                            i18n.tr("settings_export_done", path=path),
                            parent=self.app)

    def _import_settings(self):
        """从 JSON 文件导入设置，并立刻应用到当前进程。"""
        path = filedialog.askopenfilename(
            title=i18n.tr("settings_import"), parent=self.app,
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            data = settings_io.read_file(path)
            merged, summary = settings_io.import_settings(data, self.app.settings)
        except settings_io.SettingsImportError as exc:
            applog.log_event("settings_import_failed", error=str(exc)[:200])
            messagebox.showerror(
                i18n.tr("settings_io_card"),
                i18n.tr("settings_import_failed", message=str(exc)[:200]),
                parent=self.app)
            return
        if not merged:
            messagebox.showinfo(i18n.tr("settings_io_card"),
                                i18n.tr("settings_import_nothing"),
                                parent=self.app)
            return
        self.app.settings.update(merged)
        settings.save(self.app.settings)
        applog.log_event("settings_imported", applied=summary["applied"],
                         ignored=summary["ignored"])
        messagebox.showinfo(
            i18n.tr("settings_io_card"),
            i18n.tr("settings_import_done", applied=summary["applied"],
                    ignored=summary["ignored"]),
            parent=self.app)
        self._apply_imported_settings()

    def _apply_imported_settings(self):
        """把导入的设置应用到当前进程。

        顺序要紧：``set_font_scale`` 会**销毁并重建**整个布局（本页控件随之作废），
        所以放在最后，之后不再碰任何控件。
        """
        try:
            self.app.set_theme(self.app.settings.get("theme", "system"))
            self.app.set_language(self.app.settings.get("language", i18n.language()))
            self.app.set_font_scale(self.app.settings.get("font_scale", "standard"))
        except tk.TclError:
            pass

    def _open_about(self):
        """集中入口：版本、依赖、保存位置、日志路径都在这里看。"""
        about.show(self.app, probe=getattr(self, "_cookie_probe", None))

    def _open_log(self):
        """在文件管理器里定位日志文件；还没有日志就先写一行把它建出来。"""
        path = applog.log_path()
        try:
            if not os.path.isfile(path):
                applog.log_event("log_created")
            if os.path.isfile(path):
                open_in_file_manager(path)
                self._log_status(i18n.tr("log_opened", name=os.path.basename(path)),
                                 ok=True)
            else:
                self._log_status(i18n.tr("log_unavailable"), ok=False)
        except Exception as exc:      # 打开失败也不该让界面崩掉
            self._log_status(i18n.tr("log_unavailable"), ok=False)
            applog.log_exception("log_open_failed", exc)

    def _log_status(self, text, ok=True):
        if not self.winfo_exists():
            return
        self.diag_status.set_fg_role("TEXT_SUB" if ok else "ERROR")
        self.diag_status.configure(text=text)

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
        applog.log_event("ytdlp_update_available", version=version)
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
        applog.log_event("ytdlp_installed", version=version)
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


def _refresh_queue_page_safe(app):
    """刷新队列页（页面可能还没建好或已销毁）。"""
    page = getattr(app, "pages", {}).get("queue")
    refresh = getattr(page, "refresh", None)
    if callable(refresh):
        try:
            refresh()
        except tk.TclError:
            pass


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
        # 代理要在任何网络动作之前就位（空设置时回退到环境变量）
        netproxy.set_from_settings(self.settings)

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
        # 版本检查放在最后：不跟启动期的 Cookie 体检、剪贴板检查抢资源
        self.after(self.UPDATE_CHECK_DELAY_MS, self._settings_page_auto_check)
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

    def enqueue_url(self, url, platform="", kind="video"):
        """把链接加入队列；只存 URL、平台与类型，不冻结选项（见 QueuePage 说明）。"""
        url = (url or "").strip()
        if not url:
            return False
        items = self.queue_items()
        items.append({"url": url, "platform": platform or detect_platform(url),
                      "kind": normalize_kind(kind)})
        self.set_queue_items(items)
        return True

    def enqueue_urls(self, urls, kind="video"):
        """批量入队，返回 (新增数, 跳过数)。

        跳过 = 队列里已有（**同类型**）或 本批重复。用户从聊天记录里复制时经常重复
        粘贴，静默去重比塞一堆重复项友好；数量会如实反馈给界面。
        """
        kind = normalize_kind(kind)
        existing = {queue_key(item) for item in self.queue_items()}
        items = list(self.queue_items())
        room = max(0, self.QUEUE_LIMIT - len(items))
        added = 0
        skipped = 0
        for url in urls or []:
            url = (url or "").strip()
            key = (url, kind)
            # 队列满了也算「跳过」，否则返回的 added 会虚报（set_queue_items 会截断）
            if not url or key in existing or added >= room:
                skipped += 1
                continue
            items.append({"url": url, "platform": detect_platform(url), "kind": kind})
            existing.add(key)
            added += 1
        if added:
            self.set_queue_items(items)
        return added, skipped

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
        # 排在队列推进之后：队列里还有任务就不该触发「完成后动作」
        self.after(400, self._maybe_run_after_download)

    def embed_settings(self):
        """「嵌入到文件」的三个开关，交给引擎翻译成 yt-dlp 参数。"""
        return {
            "subtitles": bool(self.settings.get("embed_subtitles", False)),
            "thumbnail": bool(self.settings.get("embed_thumbnail", False)),
            "metadata": bool(self.settings.get("embed_metadata", False)),
        }

    def is_idle(self):
        """没有任何面板在下载、队列里也没有待办。"""
        if any(getattr(panel, "_busy", False) for panel in self._all_panels()):
            return False
        return not self.queue_items()

    def _maybe_run_after_download(self):
        """下载结束后的后续动作（第 8 项）。

        只在**真正空闲**时触发一次：播放列表或队列还在跑的时候不动作，否则会为每个
        条目各开一次文件夹窗口。任何失败只记日志，绝不影响应用本体。
        """
        code = postaction.normalize(self.settings.get("after_download"))
        if code == "none" or not self.is_idle():
            return
        entry = next(iter(self.download_history()), None)
        path = str((entry or {}).get("path") or "")
        if not path or path == getattr(self, "_after_action_path", ""):
            return                 # 没有新产出，或这一次已经触发过
        self._after_action_path = path
        target = os.path.dirname(path) if code == "open_folder" else path
        try:
            if code in ("open_folder", "open_file"):
                open_path(target)
            elif code == "quit":
                self.quit_from_menubar()
                return             # 已经进入退出流程，不再记「完成」
            elif code == "sleep":
                if not postaction.run_sleep():
                    return         # 平台不支持，静默跳过
        except Exception as exc:
            applog.log_event("after_download_failed", action=code, error=str(exc))
            return
        applog.log_event("after_download", action=code, path=path)

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
        kind = normalize_kind(item.get("kind"))
        if getattr(page, "kind_var", None) is not None:
            page.kind_var.set(kind)          # 图片任务要切到图片面板再执行
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
    UPDATE_CHECK_DELAY_MS = 2500

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

    def _settings_page_auto_check(self):
        """启动后延迟触发一次版本自动检查（那块界面属于设置页）。"""
        page = self.pages.get("settings")
        if page is not None:
            page._auto_check_updates()

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
        self._handle_menubar_action(req)

    def _handle_menubar_action(self, req):
        """执行一个菜单动作（拆出来是为了能直接驱动，尤其是版本项的双行为）。"""
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
        elif req == "version":
            # 菜单项标题就说明了它会做什么：有新版打开发布页，否则打开「关于」
            if str(self.settings.get("update_available") or ""):
                open_path(getattr(self, "_release_url", "") or appupdate.RELEASES_PAGE)
            else:
                about.show(self, probe=getattr(self.pages.get("settings"),
                                               "_cookie_probe", None))

    def _on_close_window(self):
        """关闭窗口（红色按钮）→ 隐藏窗口，应用保留在 menubar 运行。"""
        applog.log_event("window_hidden")
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

    def report_callback_exception(self, exc_type, exc_value, exc_tb):
        """回调里未捕获的异常：写进日志，再留给 Tk 默认处理（打印到 stderr）。

        「App 突然没反应」这类问题只能靠这里留下的记录定位。
        """
        where = ""
        if exc_tb is not None and getattr(exc_tb, "tb_frame", None) is not None:
            where = getattr(exc_tb.tb_frame.f_code, "co_name", "")
        applog.log_exception("unhandled_exception", exc_value, where=where)
        super().report_callback_exception(exc_type, exc_value, exc_tb)

    def _destroy_app(self):
        applog.log_event("app_exit")
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
            return
        # 点了一下却什么都没发生是最糟的反馈，说明白为什么不支持
        self._set_status_anywhere(self._unsupported_message(url), error=True)

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
            # URL_RE 是按平台严格匹配的，不支持的链接在这里就是 None。先判断
            # 「到底有没有链接」：有链接但不支持，要说清是哪个平台；真的没有链接，
            # 才说「没有可识别的链接」——两者对用户是完全不同的信息。
            found = parse_links(text)
            if found and (force or action != "off"):
                self._set_status_anywhere(self._unsupported_message(found[0]),
                                          error=True)
                return
            if force and not found:
                self._set_status_anywhere(i18n.tr("clipboard_no_link"), error=True)
            return
        platform = detect_platform(url)
        if platform not in self.pages:
            # 已知但不支持的平台要**主动说一句**（每个剪贴板文本只说一次，
            # 靠上面 _last_clipboard 的去重，不会反复刷屏）
            self._set_status_anywhere(self._unsupported_message(url), error=True)
            return
        if force or action == "auto":
            # 保留旧行为，但改成需要用户显式选择
            self.show_page(platform)
            self.pages[platform].handle_url(url)
            return
        self._show_clipboard_suggestion(url)

    def _unsupported_message(self, url):
        """链接进不了任何面板时，给一句**准确**的说明。

        以前是静默返回（或不区分情况地说「剪贴板里没有可识别的链接」）：用户
        明确粘了一个链接进来，得到的却是「没有链接」，只会以为软件坏了。
        """
        info = platforms.unsupported_site(url)
        if info is None:
            return i18n.tr("unsupported_link_other")
        key, engine_ok = info
        message = i18n.tr("unsupported_platform", platform=i18n.tr(key))
        if engine_ok:
            message += "　" + i18n.tr("unsupported_platform_engine_hint")
        return message

    def _set_status_anywhere(self, text, error=False):
        """把状态写到当前可见面板（menubar 动作发生在哪个页面并不确定）。"""
        panel = _active_panel(self._current_page())
        setter = getattr(panel, "_set_status", None)
        if callable(setter):
            setter(text, error=error)


def run():
    app = App()
    from . import __version__

    applog.log_event("app_start", version=__version__, platform=sys.platform,
                     frozen=bool(getattr(sys, "frozen", False)),
                     ytdlp=ytdlp_update.active_version()[0],
                     ytdlp_source=ytdlp_update.active_source(),
                     save_dir=app.settings.get("save_dir", ""))
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
