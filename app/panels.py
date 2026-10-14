"""下载面板：视频（yt-dlp）与图片（gallery-dl + requests）。

从 gui.py 拆出来（ROADMAP 第 13 项），这是最大的一块——单个文件里原本挤着 53 + 31
个方法。面板只负责「界面状态 + 回调 + 线程编排」，真正的下载逻辑在 core.py /
images.py；两者之间通过参数与回调连接，这里没有改变任何行为。

原有引用方式保持不变：gui.py 仍然 import 这两个类，``gui.VideoPanel`` 这些名字
以及按类属性 patch 的测试都照旧生效。
"""

import io
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog

from . import applog, core, diagnostics, i18n, images, settings
from .dialogs import EntryPicker, TextDetailDialog
from .fspath import open_in_file_manager, open_path
from .taskbar import _taskbar_clear, _taskbar_done, _taskbar_progress
from .urlutil import (download_dir_key, extract_supported_url, is_weibo_video_url,
                       parse_links)
from .widgets import (
    CheckPill, GlassCard, ImagePicker, PillButton, PillSelect, RoundEntry,
    RoundProgress, SegmentedControl, TFrame, TLabel, _check_pill_width,
    _segmented_width, ellipsize, ellipsize_filename, hand_cursor,
)


def enqueue_from_entry(app, panel, kind) -> None:
    """把面板输入框里的链接加入队列（视频页与图片页共用）。

    输入框里有多个链接时（整段粘贴）全部排队，并如实报告新增与跳过数。
    """
    urls = parse_links(panel.url_entry.get().strip())
    if not urls:
        panel._set_status(i18n.tr("queue_need_link"), error=True)
        return
    if len(urls) == 1:
        app.enqueue_url(urls[0], kind=kind)
        panel._set_status(i18n.tr("queue_added", url=urls[0]), ok=True)
        return
    added, skipped = app.enqueue_urls(urls, kind=kind)
    if not added:
        panel._set_status(i18n.tr("queue_all_duplicated", skipped=skipped),
                          error=True)
        return
    panel._set_status(i18n.tr("queue_batch_added", n=added, skipped=skipped),
                      ok=True)

# ====================================================================
# 视频下载面板（yt-dlp）：YouTube / X / Instagram / 微博 共用
# ====================================================================


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
        applog.log_event("probe_failed", panel=self.platform, error=msg)
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
            "embed": self.app.embed_settings(),
            "extras": self.app.extras_settings(),
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
                                               audio_format=p["audio_format"],
                                               embed=p["embed"],
                                               extras=p["extras"])
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
                        subtitles=p["subtitles"], subtitle_langs=p["subtitle_langs"],
                        embed=p["embed"],
                                               extras=p["extras"])
                else:
                    path = core.download_video(p["url"], height=p["height"],
                                               save_dir=p["save_dir"],
                                               progress=on_progress,
                                               cancel_event=cancel_event,
                                               pause_event=pause_event,
                                               subtitles=p["subtitles"],
                                               subtitle_langs=p["subtitle_langs"],
                                               embed=p["embed"],
                                               extras=p["extras"])
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
        applog.log_event("download_failed", panel=self.platform, error=msg)
        self._reset_after_download()
        self.progress.set(0)
        _taskbar_clear(self.app)
        self._set_status(msg, error=True)

    def _download_done(self, result):
        self._reset_after_download()
        paths = [p for p in (result if isinstance(result, (list, tuple)) else [result]) if p]
        applog.log_event("download_done", panel=self.platform, files=paths)
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
        enqueue_from_entry(self.app, self, "video")

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
        self._thumb_workers = []  # 仅供诊断：最近一批缩略图 worker（守护线程）
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
        self.queue_btn = PillButton(row, i18n.tr("queue_add"),
                                    command=self.on_enqueue, font=f.f_btn, width=96,
                                    bg_role="CARD")
        self.queue_btn.pack(side="left", padx=(8, 0))

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

    def on_enqueue(self):
        """把当前链接加入队列，标记为图片任务（执行时仍用当时的设置）。"""
        enqueue_from_entry(self.app, self, "image")

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
        applog.log_event("probe_failed", panel=self.platform, error=msg)
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

    # 缩略图并发上限：一个帖子可能有几十张图，原先「一张一个线程」会瞬间开几十个连接，
    # 容易触发平台限流或耗尽 socket（微博动图帖实测 18 张）。
    THUMB_WORKERS = 4

    def _load_thumbnails(self, post):
        """后台**限量**并发拉取缩略图，主线程转 PhotoImage 填入网格。

        固定若干个守护线程从队列取任务，而不是一张图一个线程：线程数有上限、随
        App 退出立即消失（ThreadPoolExecutor 的线程是非守护的，退出时会 join，
        一次卡住的请求能拖慢关闭）。换帖子时代数（epoch）变化，worker 取到下一次
        任务前就会退出，排队中的任务自然作废。
        """
        epoch = self._thumb_epoch
        pending = queue.Queue()
        for index, item in enumerate(post.items):
            pending.put((index, item))
        workers = min(self.THUMB_WORKERS, pending.qsize())
        self._thumb_workers = []
        for _ in range(max(0, workers)):
            thread = threading.Thread(target=self._thumb_worker, args=(pending, epoch),
                                      daemon=True, name=f"thumb-{epoch}")
            thread.start()
            self._thumb_workers.append(thread)

    def _thumb_worker(self, pending, epoch):
        while epoch == self._thumb_epoch:
            try:
                index, item = pending.get_nowait()
            except queue.Empty:
                return
            try:
                raw = images.fetch_thumbnail(item)
            except Exception:
                continue          # 单张失败不影响其它图，也不写状态栏
            if epoch != self._thumb_epoch:
                return
            self.after(0, self._set_thumb, epoch, index, raw)

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
        applog.log_event("download_failed", panel=self.platform, error=msg)
        self._reset_after_download()
        self.progress.set(0)
        _taskbar_clear(self.app)
        self._set_status(msg, error=True)

    def _download_done(self, result):
        # 跳过（已存在）的文件也算「能找到的产物」，否则全部跳过时
        #「打开所在文件夹」会没有目标。
        self._last_paths = list(result.paths) + list(result.skipped)
        applog.log_event("images_done", panel=self.platform,
                         files=self._last_paths,
                         skipped=len(result.skipped))
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
