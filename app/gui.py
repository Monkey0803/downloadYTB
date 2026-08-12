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
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

from . import core, images, menubar, settings, theme
from .widgets import (
    CheckPill, GlassCard, ImagePicker, LogoSwatch, PillButton, PillSelect,
    RoundEntry, RoundProgress, SegmentedControl, SidebarItem, TFrame, TLabel,
    apply_theme_tree, ellipsize, ellipsize_filename,
)

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
LOGO_OPTIONS = [("classic", "经典蓝"), ("neon", "霓虹"),
                ("sunset", "日落"), ("mint", "薄荷")]


def logo_icon_path(key: str, size: int = 1024):
    """返回某款 logo 处理后图标的绝对路径；缺失时返回 None。"""
    name = f"{key}_icon.png" if size >= 1024 else f"{key}_512.png"
    return asset_path(os.path.join("logos", name))


def logo_menubar_path(key: str):
    """返回某款 logo 的 menubar 彩色小图路径；缺失时返回 None。"""
    return asset_path(os.path.join("logos", f"{key}_menubar.png"))


def detect_platform(url: str):
    """根据链接判断平台页 key。"""
    low = (url or "").lower()
    if "youtube.com" in low or "youtu.be" in low:
        return "youtube"
    if "x.com" in low or "twitter.com" in low:
        return "x"
    if "instagram.com" in low:
        return "instagram"
    if "douyin.com" in low:
        return "douyin"
    if "bilibili.com" in low or "b23.tv" in low:
        return "bilibili"
    return None


def download_dir_key(platform: str, kind: str) -> str:
    """返回一个下载方式的保存目录键，例如 ``youtube_audio``。"""
    platform_keys = {
        "YouTube": "youtube",
        "X": "x",
        "Instagram": "instagram",
        "抖音": "douyin",
        "哔哩哔哩": "bilibili",
    }
    platform_key = platform_keys.get(platform, (platform or "unknown").lower())
    return f"{platform_key}_{kind}"


# ====================================================================
# 视频下载面板（yt-dlp）：YouTube / X / Instagram 共用
# ====================================================================


class VideoPanel(TFrame):
    """单平台的视频下载面板：解析 → 清晰度 → 下载（暂停/继续/取消）。"""

    def __init__(self, master, app, platform="YouTube", show_audio=True):
        super().__init__(master, bg_role="BG")
        self.app = app
        self.platform = platform
        self._show_audio = show_audio

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

    # ---------- 界面 ----------

    def _build(self):
        f = self.app

        # —— 卡片 1：链接 ——
        card1 = GlassCard(self)
        card1.set_height(130)
        card1.pack(fill="x", pady=(0, 12))
        b1 = card1.body
        TLabel(b1, text="视频链接", font=f.f_section, bg_role="CARD",
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
        self.paste_btn = PillButton(row, "粘贴", command=self.on_paste,
                                    font=f.f_btn, width=68, bg_role="CARD")
        self.paste_btn.pack(side="left", padx=(10, 0))
        self.probe_btn = PillButton(row, "解析", command=self.on_probe,
                                    font=f.f_btn, width=68, bg_role="CARD")
        self.probe_btn.pack(side="left", padx=(8, 0))

        self.title_label = TLabel(b1, text="", anchor="w", justify="left",
                                  font=f.f_small, bg_role="CARD", fg_role="TEXT_SUB")
        self.title_label.pack(anchor="w", fill="x", pady=(8, 0))

        # —— 卡片 2：下载选项 ——
        card2 = GlassCard(self)
        card2.set_height(114)
        card2.pack(fill="x", pady=(0, 12))
        b2 = card2.body
        TLabel(b2, text="下载选项", font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(anchor="w")

        opt_row = TFrame(b2, bg_role="CARD")
        opt_row.pack(fill="x", pady=(10, 0))
        self.mode_var = tk.StringVar(value="video")
        if self._show_audio:
            self.mode_seg = SegmentedControl(
                opt_row, [("视频（含音轨）", "video"), ("仅音频 (MP3)", "audio")],
                self.mode_var, command=self._on_mode_change, font=f.f_btn, width=250,
            )
            self.mode_seg.pack(side="left")
            TLabel(opt_row, text="清晰度", font=f.f_body, bg_role="CARD",
                   fg_role="TEXT_SUB").pack(side="left", padx=(22, 8))
        else:
            TLabel(opt_row, text="清晰度", font=f.f_body, bg_role="CARD",
                   fg_role="TEXT_SUB").pack(side="left", padx=(0, 8))
        self.quality_var = tk.StringVar(value="请先解析链接")
        self.quality_select = PillSelect(opt_row, self.quality_var,
                                         font=f.f_btn, width=196)
        self.quality_select.pack(side="left")

        # —— 卡片 3：保存位置 ——
        card3 = GlassCard(self)
        card3.set_height(102)
        card3.pack(fill="x", pady=(0, 14))
        b3 = card3.body
        TLabel(b3, text="保存位置", font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(anchor="w")

        dir_row = TFrame(b3, bg_role="CARD")
        dir_row.pack(fill="x", pady=(8, 0))
        self.dir_var = tk.StringVar(value=self.app.default_save_dir(self._save_dir_key()))
        self.dir_entry = RoundEntry(dir_row, textvariable=self.dir_var, font=f.f_body)
        self.dir_entry.pack(side="left", fill="x", expand=True)
        PillButton(dir_row, "选择…", command=self.on_choose_dir,
                   font=f.f_btn, width=74, bg_role="CARD").pack(side="left", padx=(10, 0))

        # —— 主操作按钮区 ——
        btn_row = TFrame(self, bg_role="BG")
        btn_row.pack(fill="x", pady=(0, 12))
        # 「开始下载」始终可点：未解析时点击会自动先解析再下载
        self.download_btn = PillButton(btn_row, "开始下载", command=self._on_download_btn,
                                       primary=True, font=f.f_btn_big, height=46)
        self.download_btn.pack(side="left", fill="x", expand=True)
        self.cancel_btn = PillButton(btn_row, "取消", command=self.on_cancel,
                                     font=f.f_btn, width=88, height=46)
        # 取消按钮仅在下载中/暂停时显示

        # —— 进度与状态 ——
        self.progress = RoundProgress(self)
        self.progress.pack(fill="x")

        status_row = TFrame(self, bg_role="BG")
        status_row.pack(fill="x", pady=(10, 0))
        self.open_btn = PillButton(status_row, "打开所在文件夹", command=self.on_open_folder,
                                   font=f.f_btn, width=130)
        self.open_btn.pack(side="right", padx=(12, 0))
        self.open_btn.configure_state("disabled")

        self._status_full = ("请输入链接并点击「解析」", None)
        self.status_label = TLabel(status_row, text=self._status_full[0], anchor="w",
                                   font=f.f_sub, bg_role="BG", fg_role="TEXT_SUB")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.status_label.bind("<Configure>", lambda e: self._refresh_status_text())

    # ---------- 状态栏 ----------

    def _set_status(self, text, error=False, ok=False, filename=None):
        self._status_full = (text, filename)
        self.status_label.set_fg_role("ERROR" if error else ("OK" if ok else "TEXT_SUB"))
        self._refresh_status_text()

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
        if shown != self.status_label.cget("text"):
            self.status_label.configure(text=shown)

    # ---------- 事件 ----------

    def _on_mode_change(self):
        audio = self.mode_var.get() == "audio"
        self.quality_select.set_enabled(not audio)
        # 视频与仅音频是两个独立的下载方式，各自恢复上次选择的目录。
        saved_dir = self.app.default_save_dir(self._save_dir_key())
        if self.dir_var.get() != saved_dir:
            self.dir_var.set(saved_dir)

    def _save_dir_key(self):
        kind = "audio" if self.mode_var.get() == "audio" else "video"
        return download_dir_key(self.platform, kind)

    def on_choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get() or core.DEFAULT_SAVE_DIR)
        if path:
            self.app.apply_download_dir(self._save_dir_key(), path)

    def on_paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self._set_status("剪贴板为空", error=True)
            return
        url = extract_supported_url(text) or text.strip()
        if not url:
            self._set_status("剪贴板为空", error=True)
            return
        self.url_entry.set(url)
        if extract_supported_url(url) and not self._busy:
            self.on_probe()

    def handle_url(self, url):
        """外部（剪贴板检测）填入链接并解析。"""
        if self._busy:
            return
        self.url_entry.set(url)
        self._set_status("已从剪贴板填入链接，正在解析…")
        self.on_probe()

    def on_probe(self):
        if self._busy:
            return
        url = self.url_entry.get().strip()
        if not url:
            self._set_status("请输入视频链接", error=True)
            return

        self._busy = True
        self.probe_btn.configure_state("disabled")
        self.download_btn.configure_state("disabled")
        self._set_status("正在解析链接…")

        def work():
            try:
                info = core.probe(url)
            except core.DownloadError as exc:
                self.after(0, self._probe_failed, str(exc))
            except Exception as exc:
                self.after(0, self._probe_failed, f"解析失败：{exc}")
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
        self.probe_btn.configure_state("normal")
        self.download_btn.configure_state("normal")

        suffix = (f"  ·  {info.uploader}" if info.uploader else "") + f"  ·  {info.duration_text}"
        max_px = max(200, self.title_label.winfo_width() - 8)
        f = self.app.f_small
        title = info.title
        while title and f.measure(title + "…" + suffix) > max_px:
            title = title[:-1]
        if title != info.title:
            title += "…"
        self.title_label.set_fg_role("TEXT")
        self.title_label.configure(text=f"{title}{suffix}")

        values = [q.display for q in info.qualities] if info.qualities else ["最佳画质"]
        self.quality_select.set_values(values)
        self.quality_var.set(values[0])
        self._on_mode_change()
        self._set_status("解析成功，请选择选项后点击「开始下载」", ok=True)

    def _height_for_label(self, label):
        if not self._info:
            return None
        for q in self._info.qualities:
            if label in (q.display, q.label):
                return q.max_height
        return None

    def _on_url_edited(self):
        """链接被修改后，旧的解析结果立即失效（清晰度档位归零）。"""
        if self._downloading or self._paused or self._info is None:
            return
        url = self.url_entry.get().strip()
        if url != self._info.url:
            self._info = None
            self.quality_select.set_values([])
            self.quality_var.set("请先解析链接")
            self.title_label.configure(text="")
            self._set_status("链接已变更，点击「解析」或直接「开始下载」（会自动解析）")

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
            self._set_status("正在暂停下载…")

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
            self._set_status("正在取消下载…")

    def on_download(self):
        if self._busy:
            return
        url = self.url_entry.get().strip()
        if not url:
            self._set_status("请输入视频链接", error=True)
            return
        # 未解析、或链接已变更（解析结果与输入不一致）时：先自动解析，
        # 成功后用解析出的最高档位继续下载，避免用旧视频的档位下错视频
        if self._info is None or self._info.url != url:
            self._probe_then_download(url)
            return
        # 先持久化当前输入；清空输入时也能正确回退到全局默认目录。
        self.app.apply_download_dir(self._save_dir_key(), self.dir_var.get().strip())
        self._dl_params = {
            "url": self._info.url,
            "save_dir": self.app.default_save_dir(self._save_dir_key()),
            "audio_only": self.mode_var.get() == "audio",
            "height": (None if self.mode_var.get() == "audio"
                       else self._height_for_label(self.quality_var.get())),
        }
        self._last_file = None
        self._last_percent = 0.0
        self.open_btn.configure_state("disabled")
        self.progress.set(0)
        self._start_download(resume=False)

    def _probe_then_download(self, url):
        """「开始下载」时尚未解析：自动解析，成功后立即接着下载。"""
        self._busy = True
        self._info = None
        self.probe_btn.configure_state("disabled")
        self.download_btn.configure_state("disabled")
        self._set_status("正在解析链接，完成后自动开始下载…")

        def work():
            try:
                info = core.probe(url)
            except core.DownloadError as exc:
                self.after(0, self._probe_failed, str(exc))
            except Exception as exc:
                self.after(0, self._probe_failed, f"解析失败：{exc}")
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
        self.download_btn.set_text("暂停下载")
        self.cancel_btn.pack(side="left", padx=(10, 0))
        self.probe_btn.configure_state("disabled")
        self._set_status("正在继续下载…" if resume else "正在开始下载…")

        def on_progress(percent, speed_text, eta_text, status):
            def update():
                if cancel_event.is_set() or pause_event.is_set():
                    return
                if status == "merging":
                    self.progress.set(100)
                    self._set_status("下载完成，正在合并/转码…")
                else:
                    if percent is not None:
                        self._last_percent = percent
                        self.progress.set(percent)
                        self._set_status(
                            f"下载中 {percent:.1f}%  ·  速度 {speed_text}  ·  剩余 {eta_text}")
                    else:
                        self._set_status(f"下载中…  ·  速度 {speed_text}")
            self.after(0, update)

        def work():
            try:
                if p["audio_only"]:
                    path = core.download_audio(p["url"], save_dir=p["save_dir"],
                                               progress=on_progress,
                                               cancel_event=cancel_event,
                                               pause_event=pause_event)
                else:
                    path = core.download_video(p["url"], height=p["height"],
                                               save_dir=p["save_dir"],
                                               progress=on_progress,
                                               cancel_event=cancel_event,
                                               pause_event=pause_event)
            except core.DownloadCancelled:
                self.after(0, self._download_cancelled)
            except core.DownloadPaused as exc:
                self.after(0, self._download_paused, exc.partial_files)
            except core.DownloadError as exc:
                self.after(0, self._download_failed, str(exc))
            except Exception as exc:
                self.after(0, self._download_failed, f"下载失败：{exc}")
            else:
                self.after(0, self._download_done, path)

        threading.Thread(target=work, daemon=True).start()

    def _download_paused(self, partial_files):
        if self._cancel_event is not None and self._cancel_event.is_set():
            core.cleanup_partial_files(partial_files)
            self._download_cancelled()
            return
        self._downloading = False
        self._paused = True
        self._partial_files = partial_files
        self.download_btn.set_text("继续下载")
        self._set_status(f"已暂停，已下载 {self._last_percent:.1f}%")

    def _reset_after_download(self):
        self._busy = False
        self._downloading = False
        self._paused = False
        self._cancel_event = None
        self._pause_event = None
        self._partial_files = None
        self.download_btn.set_text("开始下载")
        self.download_btn.configure_state("normal")
        self.cancel_btn.pack_forget()
        self.probe_btn.configure_state("normal")

    def _download_cancelled(self):
        self._reset_after_download()
        self.progress.set(0)
        self._set_status("已取消下载，临时文件已清理")

    def _download_failed(self, msg):
        self._reset_after_download()
        self.progress.set(0)
        self._set_status(msg, error=True)

    def _download_done(self, path):
        self._reset_after_download()
        self._last_file = path
        self.open_btn.configure_state("normal")
        self.progress.set(0)
        self._set_status("下载完成：", ok=True, filename=os.path.basename(path))

    def on_open_folder(self):
        open_in_file_manager(self._last_file)


# ====================================================================
# 图片下载面板（gallery-dl 解析 + requests 下载）：X / Instagram 共用
# ====================================================================


class ImagePanel(TFrame):
    """图片下载面板：解析帖子 → 缩略图多选（全选）→ 下载（可取消）。"""

    def __init__(self, master, app, platform="X"):
        super().__init__(master, bg_role="BG")
        self.app = app
        self.platform = platform

        self._post = None
        self._busy = False
        self._downloading = False
        self._cancel_event = None
        self._last_paths = []
        self._thumb_epoch = 0  # 解析代数，旧的缩略图线程结果直接丢弃

        self._build()

    def _build(self):
        f = self.app

        # —— 卡片 1：链接 ——
        card1 = GlassCard(self)
        card1.set_height(96)
        card1.pack(fill="x", pady=(0, 12))
        b1 = card1.body
        row = TFrame(b1, bg_role="CARD")
        row.pack(fill="x", pady=(4, 0))
        TLabel(row, text="帖子链接", font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(side="left", padx=(0, 10))
        self.url_entry = RoundEntry(row, font=f.f_body)
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.entry.bind("<Return>", lambda e: self.on_probe())
        self.paste_btn = PillButton(row, "粘贴", command=self.on_paste,
                                    font=f.f_btn, width=68, bg_role="CARD")
        self.paste_btn.pack(side="left", padx=(10, 0))
        self.probe_btn = PillButton(row, "解析", command=self.on_probe,
                                    font=f.f_btn, width=68, bg_role="CARD")
        self.probe_btn.pack(side="left", padx=(8, 0))

        # —— 卡片 2：图片选择 ——
        card2 = GlassCard(self)
        card2.pack(fill="both", expand=True, pady=(0, 12))
        b2 = card2.body
        head = TFrame(b2, bg_role="CARD")
        head.pack(fill="x")
        TLabel(head, text="图片选择", font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(side="left")
        self.count_label = TLabel(head, text="", font=f.f_small, bg_role="CARD",
                                  fg_role="TEXT_SUB")
        self.count_label.pack(side="right")
        self.all_var = tk.BooleanVar(value=True)
        self.all_check = CheckPill(head, "全选", self.all_var,
                                   command=self._on_all_toggle, font=f.f_btn, width=70)
        self.all_check.pack(side="right", padx=(0, 12))

        self.post_label = TLabel(b2, text="解析后在此选择要下载的图片", anchor="w",
                                 font=f.f_small, bg_role="CARD", fg_role="TEXT_SUB")
        self.post_label.pack(anchor="w", fill="x", pady=(6, 4))

        self.picker = ImagePicker(b2, on_change=self._on_selection_change, height=150)
        self.picker.pack(fill="both", expand=True)

        # —— 卡片 3：保存位置 ——
        card3 = GlassCard(self)
        card3.set_height(96)
        card3.pack(fill="x", pady=(0, 14))
        b3 = card3.body
        dir_row = TFrame(b3, bg_role="CARD")
        dir_row.pack(fill="x", pady=(4, 0))
        TLabel(dir_row, text="保存位置", font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(side="left", padx=(0, 10))
        self.dir_var = tk.StringVar(value=self.app.default_save_dir(self._save_dir_key()))
        self.dir_entry = RoundEntry(dir_row, textvariable=self.dir_var, font=f.f_body)
        self.dir_entry.pack(side="left", fill="x", expand=True)
        PillButton(dir_row, "选择…", command=self.on_choose_dir,
                   font=f.f_btn, width=74, bg_role="CARD").pack(side="left", padx=(10, 0))

        # —— 操作按钮 + 进度 ——
        btn_row = TFrame(self, bg_role="BG")
        btn_row.pack(fill="x", pady=(0, 12))
        self.download_btn = PillButton(btn_row, "下载选中图片", command=self.on_download,
                                       primary=True, font=f.f_btn_big, height=46)
        self.download_btn.pack(side="left", fill="x", expand=True)
        self.download_btn.configure_state("disabled")
        self.cancel_btn = PillButton(btn_row, "取消", command=self.on_cancel,
                                     font=f.f_btn, width=88, height=46)

        self.progress = RoundProgress(self)
        self.progress.pack(fill="x")

        status_row = TFrame(self, bg_role="BG")
        status_row.pack(fill="x", pady=(10, 0))
        self.open_btn = PillButton(status_row, "打开所在文件夹", command=self.on_open_folder,
                                   font=f.f_btn, width=130)
        self.open_btn.pack(side="right", padx=(12, 0))
        self.open_btn.configure_state("disabled")
        self._status_full = "请输入帖子链接并点击「解析」"
        self.status_label = TLabel(status_row, text=self._status_full, anchor="w",
                                   font=f.f_sub, bg_role="BG", fg_role="TEXT_SUB")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.status_label.bind("<Configure>", lambda e: self._refresh_status_text())

    # ---------- 状态栏 ----------

    def _set_status(self, text, error=False, ok=False):
        self._status_full = text
        self.status_label.set_fg_role("ERROR" if error else ("OK" if ok else "TEXT_SUB"))
        self._refresh_status_text()

    def _refresh_status_text(self):
        max_px = self.status_label.winfo_width() - 4
        shown = (self._status_full if max_px <= 8
                 else ellipsize(self._status_full, self.app.f_sub, max_px))
        if shown != self.status_label.cget("text"):
            self.status_label.configure(text=shown)

    # ---------- 事件 ----------

    def on_choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get() or images.DEFAULT_SAVE_DIR)
        if path:
            self.app.apply_download_dir(self._save_dir_key(), path)

    def _save_dir_key(self):
        return download_dir_key(self.platform, "image")

    def on_paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self._set_status("剪贴板为空", error=True)
            return
        url = extract_supported_url(text) or text.strip()
        if not url:
            self._set_status("剪贴板为空", error=True)
            return
        self.url_entry.set(url)
        if extract_supported_url(url) and not self._busy:
            self.on_probe()

    def handle_url(self, url):
        if self._busy:
            return
        self.url_entry.set(url)
        self._set_status("已从剪贴板填入链接，正在解析…")
        self.on_probe()

    def _on_all_toggle(self):
        self.picker.set_all(bool(self.all_var.get()))

    def _on_selection_change(self):
        n = len(self.picker.selected_indices())
        total = len(self._post.items) if self._post else 0
        self.count_label.configure(text=f"已选 {n}/{total} 张")
        self.all_var.set(n == total and total > 0)
        self.all_check.refresh()
        if not self._downloading:
            self.download_btn.configure_state("normal" if n else "disabled")

    def on_probe(self):
        if self._busy:
            return
        url = self.url_entry.get().strip()
        if not url:
            self._set_status("请输入帖子链接", error=True)
            return
        self._busy = True
        self._thumb_epoch += 1
        self.probe_btn.configure_state("disabled")
        self.download_btn.configure_state("disabled")
        self._set_status("正在解析帖子中的图片…")

        def work():
            try:
                post = images.probe_images(url)
            except images.ImageError as exc:
                self.after(0, self._probe_failed, str(exc))
            except Exception as exc:
                self.after(0, self._probe_failed, f"解析失败：{exc}")
            else:
                self.after(0, self._probe_done, post)

        threading.Thread(target=work, daemon=True).start()

    def _probe_failed(self, msg):
        self._busy = False
        self.probe_btn.configure_state("normal")
        self._set_status(msg, error=True)

    def _probe_done(self, post):
        self._busy = False
        self._post = post
        self.probe_btn.configure_state("normal")

        desc = f"@{post.author}" if post.author else ""
        if post.title:
            desc = f"{desc}  ·  {post.title}" if desc else post.title
        self.post_label.set_fg_role("TEXT")
        self.post_label.configure(text=desc or "（无文字内容）")

        items = []
        for it in post.items:
            sub = it.size_text + (f" · {it.extension.upper()}" if it.extension else "")
            items.append({
                "label": f"图 {it.index}",
                "sub": sub.strip(" ·"),
                "selected": True,
                "photo": None,
                "font_small": self.app.f_small,
            })
        self.picker.set_items(items)
        self.all_var.set(True)
        self._on_selection_change()
        self._set_status(f"解析成功，共 {len(post.items)} 张图片（原图画质）", ok=True)
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
            self._set_status("请先勾选要下载的图片", error=True)
            return
        self._busy = True
        self._downloading = True
        self._cancel_event = threading.Event()
        cancel_event = self._cancel_event
        # 也记录手动输入的路径，而不只记录目录选择器选出的路径。
        self.app.apply_download_dir(self._save_dir_key(), self.dir_var.get().strip())
        save_dir = self.app.default_save_dir(self._save_dir_key())
        self._last_paths = []
        self.open_btn.configure_state("disabled")
        self.download_btn.configure_state("disabled")
        self.cancel_btn.pack(side="left", padx=(10, 0))
        self.probe_btn.configure_state("disabled")
        self.progress.set(0)
        self._set_status(f"开始下载 {len(sel)} 张图片…")

        def on_progress(percent, text):
            def update():
                if cancel_event.is_set():
                    return
                self.progress.set(percent)
                self._set_status(text)
            self.after(0, update)

        def work():
            try:
                paths = images.download_images(sel, save_dir=save_dir,
                                               progress=on_progress,
                                               cancel_event=cancel_event)
            except images.ImageCancelled:
                self.after(0, self._download_cancelled)
            except images.ImageError as exc:
                self.after(0, self._download_failed, str(exc))
            except Exception as exc:
                self.after(0, self._download_failed, f"下载失败：{exc}")
            else:
                self.after(0, self._download_done, paths)

        threading.Thread(target=work, daemon=True).start()

    def on_cancel(self):
        if self._downloading and self._cancel_event is not None \
                and not self._cancel_event.is_set():
            self._cancel_event.set()
            self._set_status("正在取消下载…")

    def _reset_after_download(self):
        self._busy = False
        self._downloading = False
        self._cancel_event = None
        self.cancel_btn.pack_forget()
        self.probe_btn.configure_state("normal")
        self._on_selection_change()

    def _download_cancelled(self):
        done = len(self._last_paths)
        self._reset_after_download()
        self.progress.set(0)
        self._set_status("已取消图片下载" + (f"（已完成 {done} 张保留）" if done else ""))

    def _download_failed(self, msg):
        self._reset_after_download()
        self.progress.set(0)
        self._set_status(msg, error=True)

    def _download_done(self, paths):
        self._last_paths = paths
        self._reset_after_download()
        self.progress.set(0)
        self.open_btn.configure_state("normal")
        self._set_status(f"已下载 {len(paths)} 张图片到 {os.path.basename(os.path.dirname(paths[0]))}/",
                         ok=True)

    def on_open_folder(self):
        if self._last_paths:
            open_in_file_manager(self._last_paths[0])


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
        if hint:
            TLabel(self, text=hint, font=app.f_small, bg_role="BG",
                   fg_role="TEXT_SUB", anchor="w", justify="left").pack(
                fill="x", pady=(0, 8))
        self.video_panel = VideoPanel(self, app, platform=platform, show_audio=True)
        self.video_panel.pack(fill="both", expand=True)

    def handle_url(self, url):
        self.video_panel.handle_url(url)


class MediaPlatformPage(TFrame):
    """X / Instagram 页：顶部「视频 / 图片」分段切换两个面板。"""

    def __init__(self, master, app, title, subtitle, platform, hint=""):
        super().__init__(master, bg_role="BG")
        head_row = TFrame(self, bg_role="BG")
        head_row.pack(fill="x", pady=(0, 14))
        PageHeader(head_row, app, title, subtitle).pack(side="left")
        self.kind_var = tk.StringVar(value="video")
        self.kind_seg = SegmentedControl(
            head_row, [("视频", "video"), ("图片", "image")], self.kind_var,
            command=self._on_kind_change, font=app.f_btn, width=176, bg_role="BG")
        self.kind_seg.pack(side="right", pady=(6, 0))

        if hint:
            TLabel(self, text=hint, font=app.f_small, bg_role="BG",
                   fg_role="TEXT_SUB", anchor="w", justify="left").pack(
                fill="x", pady=(0, 8))

        self.video_panel = VideoPanel(self, app, platform=platform, show_audio=False)
        self.image_panel = ImagePanel(self, app, platform=platform)
        self.video_panel.pack(fill="both", expand=True)

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
            self.kind_seg.refresh()
            self._on_kind_change()

    def handle_url(self, url):
        low = url.lower()
        if "/photo/" in low:
            self.show_kind("image")
        elif "instagram.com/p/" in low:
            self.show_kind("image")
        elif "/reel" in low or "/tv/" in low:
            self.show_kind("video")
        panel = self.video_panel if self.kind_var.get() == "video" else self.image_panel
        panel.handle_url(url)


class SettingsPage(TFrame):
    def __init__(self, master, app):
        super().__init__(master, bg_role="BG")
        self.app = app
        f = app
        PageHeader(self, app, "设置", "外观与下载偏好，修改后自动保存").pack(
            fill="x", pady=(0, 14))

        # —— 外观 ——
        card1 = GlassCard(self)
        card1.set_height(112)
        card1.pack(fill="x", pady=(0, 12))
        b1 = card1.body
        TLabel(b1, text="外观主题", font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(anchor="w")
        row1 = TFrame(b1, bg_role="CARD")
        row1.pack(fill="x", pady=(10, 0))
        self.theme_var = tk.StringVar(value=theme.mode())
        self.theme_seg = SegmentedControl(
            row1, theme.MODES, self.theme_var,
            command=self._on_theme_change, font=f.f_btn, width=300)
        self.theme_seg.pack(side="left")
        self.theme_hint = TLabel(row1, text="", font=f.f_small, bg_role="CARD",
                                 fg_role="TEXT_SUB")
        self.theme_hint.pack(side="left", padx=(16, 0))
        self._update_theme_hint()

        # —— 应用图标 ——
        card_logo = GlassCard(self)
        card_logo.set_height(216)
        card_logo.pack(fill="x", pady=(0, 12))
        bl = card_logo.body
        TLabel(bl, text="应用图标", font=f.f_section, bg_role="CARD",
               fg_role="TEXT_SUB").pack(anchor="w")
        logo_row = TFrame(bl, bg_role="CARD")
        logo_row.pack(anchor="w", pady=(10, 0))
        self._logo_swatches = {}
        current = app.current_logo()
        for key, label in LOGO_OPTIONS:
            sw = LogoSwatch(logo_row, label, self._load_logo_thumb(key),
                            command=lambda k=key: self._on_logo_select(k),
                            font=f.f_small)
            sw.pack(side="left", padx=(0, 12))
            sw.set_selected(key == current)
            self._logo_swatches[key] = sw
        TLabel(bl, text="Dock 与窗口图标立即生效，Finder 中 .app 的图标保持默认。",
               font=f.f_small, bg_role="CARD", fg_role="TEXT_SUB",
               anchor="w", justify="left").pack(anchor="w", fill="x", pady=(10, 0))

        # —— 默认保存位置 ——
        card2 = GlassCard(self)
        card2.set_height(104)
        card2.pack(fill="x", pady=(0, 12))
        b2 = card2.body
        TLabel(b2, text="默认保存位置（各下载方式未单独设置时使用）", font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        row2 = TFrame(b2, bg_role="CARD")
        row2.pack(fill="x", pady=(8, 0))
        self.dir_var = tk.StringVar(value=app.default_save_dir())
        self.dir_var.trace_add("write", lambda *a: self._on_dir_change())
        RoundEntry(row2, textvariable=self.dir_var, font=f.f_body).pack(
            side="left", fill="x", expand=True)
        PillButton(row2, "选择…", command=self._choose_dir,
                   font=f.f_btn, width=74, bg_role="CARD").pack(side="left", padx=(10, 0))

        # —— 登录 Cookie ——
        card3 = GlassCard(self)
        card3.set_height(150)
        card3.pack(fill="x", pady=(0, 12))
        b3 = card3.body
        TLabel(b3, text="浏览器 Cookie（用于需要登录的内容）", font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")
        row3 = TFrame(b3, bg_role="CARD")
        row3.pack(fill="x", pady=(10, 0))
        self._cookie_options = [("不使用", ""), ("Chrome", "chrome"),
                                ("Safari", "safari"), ("Firefox", "firefox"),
                                ("Edge", "edge")]
        cur = app.settings.get("browser_cookies", "")
        cur_label = next((l for l, v in self._cookie_options if v == cur), "不使用")
        self.cookie_var = tk.StringVar(value=cur_label)
        self.cookie_var.trace_add("write", lambda *a: self._on_cookie_change())
        self.cookie_select = PillSelect(row3, self.cookie_var,
                                        values=[l for l, _ in self._cookie_options],
                                        font=f.f_btn, width=150)
        self.cookie_select.pack(side="left")
        TLabel(b3, text="Instagram 已封锁匿名访问：选择已登录 Instagram 的浏览器后，"
                        "解析与下载会携带其 Cookie。\n不需要时请保持「不使用」。",
               font=f.f_small, bg_role="CARD", fg_role="TEXT_SUB",
               anchor="w", justify="left").pack(anchor="w", fill="x", pady=(10, 0))

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
            cur = "深色" if theme.is_dark() else "浅色"
            self.theme_hint.configure(text=f"当前系统外观：{cur}")
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


# ====================================================================
# 主窗口
# ====================================================================


def open_in_file_manager(path):
    if not path:
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = settings.load()
        theme.set_mode(self.settings.get("theme", "system"))
        core.set_browser_cookies(self.settings.get("browser_cookies", ""))
        images.set_browser_cookies(self.settings.get("browser_cookies", ""))

        self.title("多平台下载器 — YouTube / X / Instagram / 抖音 / 哔哩哔哩")
        self.geometry("1020x745")
        self.minsize(920, 700)
        self.configure(bg=theme.C().BG)
        self.apply_app_logo()  # 按持久化设置恢复窗口 + Dock 图标

        self._last_clipboard = None
        self._setup_fonts()
        self._build_layout()
        self.show_page("youtube")

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
        self.bind("<FocusIn>", self._on_focus_in)

    def default_save_dir(self, key=None):
        """返回指定下载方式的目录，未单独设置时回退到全局默认目录。"""
        if key:
            save_dirs = self.settings.get("save_dirs", {})
            if isinstance(save_dirs, dict):
                path = save_dirs.get(key, "")
                if isinstance(path, str) and path.strip():
                    return path.strip()
        return self.settings.get("save_dir") or core.DEFAULT_SAVE_DIR

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

    def _setup_fonts(self):
        base = tkfont.nametofont("TkDefaultFont")
        family = base.actual("family")
        self.f_title = tkfont.Font(family=family, size=22, weight="bold")
        self.f_sub = tkfont.Font(family=family, size=12)
        self.f_section = tkfont.Font(family=family, size=12, weight="bold")
        self.f_body = tkfont.Font(family=family, size=13)
        self.f_body_bold = tkfont.Font(family=family, size=13, weight="bold")
        self.f_btn = tkfont.Font(family=family, size=13)
        self.f_btn_big = tkfont.Font(family=family, size=15, weight="bold")
        self.f_small = tkfont.Font(family=family, size=11)
        self.f_side = tkfont.Font(family=family, size=13)
        self.f_side_title = tkfont.Font(family=family, size=16, weight="bold")

    # ---------- 布局 ----------

    def _build_layout(self):
        self.sidebar = TFrame(self, bg_role="SIDEBAR_BG", width=188)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        TLabel(self.sidebar, text="下载中心", font=self.f_side_title,
               bg_role="SIDEBAR_BG", fg_role="SIDEBAR_TEXT_ACTIVE").pack(
            anchor="w", padx=18, pady=(22, 2))
        TLabel(self.sidebar, text="多平台音视频 / 图片", font=self.f_small,
               bg_role="SIDEBAR_BG", fg_role="SIDEBAR_TEXT").pack(
            anchor="w", padx=18, pady=(0, 16))

        self.content = TFrame(self, bg_role="BG")
        self.content.pack(side="left", fill="both", expand=True)

        self.page_holder = TFrame(self.content, bg_role="BG")
        self.page_holder.pack(fill="both", expand=True, padx=26, pady=(20, 16))

        self.pages = {
            "youtube": VideoPlatformPage(
                self.page_holder, self, "YouTube",
                "下载视频（可选清晰度）或仅音频 MP3", platform="YouTube"),
            "x": MediaPlatformPage(
                self.page_holder, self, "X (Twitter)", "下载推文中的视频或原图画质图片",
                platform="X"),
            "instagram": MediaPlatformPage(
                self.page_holder, self, "Instagram", "下载帖子中的视频或图片",
                platform="Instagram",
                hint="提示：Instagram 已封锁匿名访问，公开帖子通常也需要登录。"
                     "可在「设置」中选择已登录的浏览器 Cookie。"),
            "douyin": VideoPlatformPage(
                self.page_holder, self, "抖音 Douyin",
                "下载抖音视频（音视频同轨）或仅音频 MP3", platform="抖音",
                hint="提示：抖音视频通常为单一清晰度档位。若被风控/需要登录，"
                     "可在「设置」中选择已登录抖音的浏览器 Cookie。"),
            "bilibili": VideoPlatformPage(
                self.page_holder, self, "哔哩哔哩 Bilibili",
                "下载 B 站视频（DASH 流自动合并）或仅音频 MP3", platform="哔哩哔哩",
                hint="提示：1080P+/4K 等高清晰度需要登录大会员，匿名最高 1080P。"
                     "可在「设置」中选择已登录的浏览器 Cookie。"),
            "settings": SettingsPage(self.page_holder, self),
        }

        self.nav_items = {}
        nav_defs = [("youtube", "YouTube", "youtube"), ("x", "X (Twitter)", "x"),
                    ("instagram", "Instagram", "instagram"),
                    ("douyin", "抖音", "douyin"),
                    ("bilibili", "哔哩哔哩", "bilibili")]
        for key, label, icon in nav_defs:
            item = SidebarItem(self.sidebar, label, icon,
                               command=lambda k=key: self.show_page(k),
                               font=self.f_side)
            item.pack(padx=12, pady=2)
            self.nav_items[key] = item

        # 设置入口固定在侧边栏底部
        settings_item = SidebarItem(self.sidebar, "设置", "settings",
                                    command=lambda: self.show_page("settings"),
                                    font=self.f_side)
        settings_item.pack(side="bottom", padx=12, pady=14)
        self.nav_items["settings"] = settings_item

        self.current_page = None

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
                    return f"下载中 {p._last_percent:.0f}%"
                if p._paused:
                    return f"已暂停 {p._last_percent:.0f}%"
            elif isinstance(p, ImagePanel) and p._downloading:
                return "下载图片中"
        return "空闲"

    def _poll_menubar(self):
        """轮询 menubar 菜单点击产生的动作（show / quit）。"""
        req = self.menubar.take_pending()
        # 先续期轮询：若后续 quit 销毁了窗口，未触发的定时器随解释器终止
        self.after(150, self._poll_menubar)
        if req == "show":
            self.show_main_window()
        elif req == "quit":
            self.quit_from_menubar()

    def _on_close_window(self):
        """关闭窗口（红色按钮）→ 隐藏窗口，应用保留在 menubar 运行。"""
        self.withdraw()

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
                    "退出", "有下载正在进行，退出将取消下载并清理临时文件。\n"
                    "确定退出吗？", parent=self):
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
        if self.menubar is not None:
            self.menubar.remove()
        self.destroy()

    # ---------- 剪贴板 ----------

    def _on_focus_in(self, event):
        if event.widget is self:
            self._check_clipboard()

    def _check_clipboard(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        if text == self._last_clipboard:
            return
        self._last_clipboard = text
        url = extract_supported_url(text)
        if not url:
            return
        platform = detect_platform(url)
        if platform not in self.pages:
            return
        self.show_page(platform)
        self.pages[platform].handle_url(url)


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
