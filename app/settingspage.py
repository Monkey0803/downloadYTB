"""设置页：外观、下载、嵌入、Cookie、代理、更新、诊断等全部设置卡片。

从 gui.py 拆出来（ROADMAP 第 13 项）——这是拆分前最大的一块：单个类 65 个方法、
1200 余行。它只做「读设置、写设置、把变化交给 App 应用」，没有下载逻辑。

一个反复出现的约定：会改变产物的开关（字幕嵌入、封面、元数据）默认关闭；取值列表
（语言、字号、图标、音质）都来自各自的权威模块，设置页只负责展示与回写。

原有引用方式保持不变：gui.py 仍然 import 这个类，``gui.SettingsPage`` 与既有测试
照旧生效。
"""

import io
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from . import (about, applog, appupdate, browser_cookies, core, diagnostics,
               ffmpeg_util, i18n, images, menubar, netproxy, platforms, postaction,
               settings, settings_io, taskbar, theme, ytdlp_update)
from .dialogs import SubtitleLanguagePicker

# 限速与分片并发的候选值：界面显示的是标签（随语言变），存的是这里的数值。
# 用选择器而不是文本框：省掉解析，也不可能存进非法值。
RATELIMIT_CHOICES = (0, 256, 512, 1024, 2048)
FRAGMENT_CHOICES = (1, 2, 4, 8)


def chapters_hint_text():
    """章节卡片的说明；没有 ffprobe 时补一句警告。

    切分与剪切要靠 yt-dlp 的 _fixup_chapters 读时长，而那只内置 ffmpeg 的形态下没有
    ffprobe。真实下载验证就是这么失败的——与其等下载到一半报错，不如提前说清楚。
    """
    text = i18n.tr("chapters_hint")
    if not ffmpeg_util.has_ffprobe():
        text += "　" + i18n.tr("chapters_need_ffprobe")
    return text
from .fspath import LOGO_OPTIONS, logo_icon_path, open_in_file_manager, open_path
from .pages import PageHeader
from .urlutil import PLATFORM_LABEL_KEYS
from .widgets import (
    CheckPill, GlassCard, LogoSwatch, PillButton, PillSelect, RoundEntry,
    SegmentedControl, TFrame, TLabel, _check_pill_width, _pack_wrapped_hint,
    _segmented_width,
)


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
        # Dock / Finder / .app 只在 macOS 上存在：其他平台换一套文案。
        # 两个 key 都写成字面量，test_i18n 的静态扫描才能继续覆盖它们。
        logo_hint = (i18n.tr("logo_hint") if sys.platform == "darwin"
                     else i18n.tr("logo_hint_other"))
        TLabel(bl, text=logo_hint,
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

        # —— 章节、赞助片段与限速 ——
        card_ch = GlassCard(self._scroll_body)
        card_ch.set_height(268)
        card_ch.pack(fill="x", pady=(0, 12))
        bch = card_ch.body
        TLabel(bch, text=i18n.tr("chapters_card"), font=f.f_section,
               bg_role="CARD", fg_role="TEXT_SUB").pack(anchor="w")

        self.split_chapters_var = tk.BooleanVar(
            value=bool(app.settings.get("split_chapters", False)))
        self.split_chapters_pill = CheckPill(
            bch, i18n.tr("split_chapters"), self.split_chapters_var,
            font=f.f_body, bg_role="CARD",
            command=self._on_split_chapters_change,
            width=_check_pill_width(f.f_body, i18n.tr("split_chapters")))
        self.split_chapters_pill.pack(anchor="w", pady=(10, 0))

        sb_row = TFrame(bch, bg_role="CARD")
        sb_row.pack(fill="x", pady=(10, 0))
        TLabel(sb_row, text=i18n.tr("sponsorblock"), font=f.f_body,
               bg_role="CARD", fg_role="TEXT_SUB").pack(side="left", padx=(0, 8))
        self.sponsorblock_var = tk.StringVar(
            value=self._sponsorblock_label(app.settings.get("sponsorblock", "off")))
        self.sponsorblock_var.trace_add(
            "write", lambda *a: self._on_sponsorblock_change())
        self.sponsorblock_select = PillSelect(
            sb_row, self.sponsorblock_var,
            values=[self._sponsorblock_label(code)
                    for code in core.SPONSORBLOCK_MODES],
            font=f.f_btn, width=150)
        self.sponsorblock_select.pack(side="left")

        cats_row = TFrame(bch, bg_role="CARD")
        cats_row.pack(fill="x", pady=(10, 0))
        TLabel(cats_row, text=i18n.tr("sponsorblock_categories"), font=f.f_body,
               bg_role="CARD", fg_role="TEXT_SUB").pack(side="left", padx=(0, 8))
        self.sponsorblock_cats_var = tk.StringVar(
            value=str(app.settings.get("sponsorblock_categories", "") or ""))
        self.sponsorblock_cats_entry = RoundEntry(
            cats_row, textvariable=self.sponsorblock_cats_var, font=f.f_body)
        self.sponsorblock_cats_entry.pack(side="left", fill="x", expand=True)
        self.sponsorblock_apply_btn = PillButton(
            cats_row, i18n.tr("sponsorblock_apply"),
            command=self._apply_sponsorblock_categories, font=f.f_btn,
            width=72, bg_role="CARD")
        self.sponsorblock_apply_btn.pack(side="left", padx=(8, 0))
        self.sponsorblock_status = TLabel(cats_row, text="", font=f.f_small,
                                          bg_role="CARD", fg_role="TEXT_SUB")
        self.sponsorblock_status.pack(side="left", padx=(10, 0))

        limit_row = TFrame(bch, bg_role="CARD")
        limit_row.pack(fill="x", pady=(10, 0))
        TLabel(limit_row, text=i18n.tr("ratelimit"), font=f.f_body,
               bg_role="CARD", fg_role="TEXT_SUB").pack(side="left", padx=(0, 8))
        self.ratelimit_var = tk.StringVar(
            value=self._ratelimit_label(app.settings.get("ratelimit_kb", 0)))
        self.ratelimit_var.trace_add("write", lambda *a: self._on_download_limits_change())
        self.ratelimit_select = PillSelect(
            limit_row, self.ratelimit_var,
            values=[self._ratelimit_label(v) for v in RATELIMIT_CHOICES],
            font=f.f_btn, width=110)
        self.ratelimit_select.pack(side="left")
        TLabel(limit_row, text=i18n.tr("fragment_workers"), font=f.f_body,
               bg_role="CARD", fg_role="TEXT_SUB").pack(side="left", padx=(16, 8))
        self.fragments_var = tk.StringVar(value=str(app.settings.get("fragment_workers", 1)))
        self.fragments_var.trace_add("write", lambda *a: self._on_download_limits_change())
        self.fragments_select = PillSelect(
            limit_row, self.fragments_var,
            values=[str(v) for v in FRAGMENT_CHOICES], font=f.f_btn, width=80)
        self.fragments_select.pack(side="left")

        _pack_wrapped_hint(bch, app, chapters_hint_text(), bg_role="CARD",
                           pady=(8, 0))
        self._track_card("chapters", card_ch, bch)
        self._apply_download_limits(save=False)

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
            # 只有 macOS 才有「完全磁盘访问权限」这一层：其他平台连说明都不该出现。
            # 之前是先无条件写文案、再由 _set_fda_row 收起整行——但那个分支只在
            # 「按钮已经显示过」时才清空文案，于是 Windows 上第一次读取失败会让
            # macOS 的设置路径留在界面上。
            if sys.platform == "darwin":
                self.cookie_fda_hint.configure(text=i18n.tr("cookie_fda_hint"))
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

    # —— 章节 / SponsorBlock / 限速 ——

    def _sponsorblock_label(self, value):
        return i18n.tr({
            "mark": "sponsorblock_mark",
            "remove": "sponsorblock_remove",
        }.get(str(value or "").strip().lower(), "sponsorblock_off"))

    def _on_split_chapters_change(self):
        app = self.app
        app.settings["split_chapters"] = bool(self.split_chapters_var.get())
        settings.save(app.settings)

    def _on_sponsorblock_change(self):
        label = self.sponsorblock_var.get()
        code = next((c for c in core.SPONSORBLOCK_MODES
                     if self._sponsorblock_label(c) == label), "off")
        self.app.settings["sponsorblock"] = code
        settings.save(self.app.settings)
        applog.log_event("sponsorblock_mode", mode=code)

    def _apply_sponsorblock_categories(self):
        """保存分类并如实回报哪些被忽略（写错分类不该静默生效或静默失效）。"""
        app = self.app
        raw = self.sponsorblock_cats_entry.get().strip()
        app.settings["sponsorblock_categories"] = raw
        settings.save(app.settings)
        mode = app.settings.get("sponsorblock", "off")
        kept = core.sponsorblock_categories(raw, mode=mode)
        if not raw:
            text = i18n.tr("sponsorblock_categories_default", n=len(kept))
        else:
            text = i18n.tr("sponsorblock_categories_kept", kept=", ".join(kept))
        self.sponsorblock_status.configure(text=ellipsize(text, 60))
        applog.log_event("sponsorblock_categories", kept=len(kept), mode=mode)

    def _ratelimit_label(self, value):
        try:
            kb = int(value)
        except (TypeError, ValueError):
            kb = 0
        if kb <= 0:
            return i18n.tr("ratelimit_unlimited")
        if kb >= 1024:
            return f"{kb / 1024:g} MB/s"
        return f"{kb} KB/s"

    def _selected_index(self, var, choices):
        """按「标签在候选里的位置」取值：文案随语言变，位置不会变。"""
        try:
            return choices.index(var.get())
        except ValueError:
            return 0

    def _on_download_limits_change(self):
        self._apply_download_limits(save=True)

    def _apply_download_limits(self, save=True):
        """把限速与分片并发交给引擎（模块级状态，与 Cookie / 代理同一套做法）。"""
        app = self.app
        kb = RATELIMIT_CHOICES[min(self._selected_index(self.ratelimit_var,
                                                        [self._ratelimit_label(v)
                                                         for v in RATELIMIT_CHOICES]),
                                   len(RATELIMIT_CHOICES) - 1)]
        workers = FRAGMENT_CHOICES[min(self._selected_index(self.fragments_var,
                                                            [str(v) for v in FRAGMENT_CHOICES]),
                                       len(FRAGMENT_CHOICES) - 1)]
        app.settings["ratelimit_kb"] = kb
        app.settings["fragment_workers"] = workers
        if save:
            settings.save(app.settings)
        core.set_download_limits(kb, workers)

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




# ====================================================================
# 主窗口
# ====================================================================
