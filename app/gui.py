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
from .settingspage import SettingsPage
from .pages import (HistoryPage, MediaPlatformPage, PageHeader, QueuePage,
                    VideoPlatformPage, _active_panel, _format_history_time,
                    _panel_url_entry, _refresh_queue_page_safe)
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
