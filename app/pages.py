"""平台页面：页头、视频页、媒体页、队列页、历史页，以及页面级辅助函数。

从 gui.py 拆出来（ROADMAP 第 13 项）。页面负责「组合面板 + 侧栏入口 + 队列/历史
列表的刷新」，真正的下载逻辑在 core.py / images.py，面板在 panels.py；这里没有
改变任何行为。

原有引用方式保持不变：gui.py 仍然 import 这些类与辅助，``gui.QueuePage``、
``gui._active_panel`` 这类名字与既有测试都照旧生效。
"""

import os
import time
import tkinter as tk
from tkinter import messagebox

from . import i18n, theme
from .fspath import open_in_file_manager, open_path
from .panels import ImagePanel, VideoPanel
from .urlutil import is_weibo_video_url, normalize_kind, parse_links, platform_label
from .widgets import (
    CheckPill, GlassCard, PillButton, SegmentedControl, TFrame, TLabel,
    _check_pill_width, _pack_wrapped_hint, _segmented_width, ellipsize,
)


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
