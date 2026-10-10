"""yt-dlp 封装：视频信息解析、清晰度列表、视频/音频下载与进度回调。

支持 YouTube / X（Twitter）/ Instagram / 抖音（Douyin）/ 哔哩哔哩（Bilibili）/ 微博（Weibo）
链接，均由 yt-dlp 自动识别。抖音通常仅单一清晰度档位，B 站为 DASH 分离的
视频流 + 音频流，由 ffmpeg 自动合并为单文件（音视频同轨）。
"""

import glob
import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import yt_dlp

from . import i18n
from . import browser_cookies
from . import platforms
from .ffmpeg_util import ffmpeg_location_for_ytdlp

DEFAULT_SAVE_DIR = os.path.expanduser("~/Downloads")

# 仅音频模式可选的目标格式；flac 为无损，比特率对它无意义。
AUDIO_FORMATS = ("mp3", "m4a", "opus", "flac")
LOSSLESS_AUDIO_FORMATS = ("flac",)
DEFAULT_AUDIO_FORMAT = "mp3"

# 字幕默认语言：留空时用这几项，避免 "all" 拉下上百种自动字幕。
DEFAULT_SUBTITLE_LANGS = ("zh-Hans", "zh-Hant", "en")

# 播放列表：解析阶段只做「扁平 + 限量」抽取。实测一个 5300 条的频道，
# 不限量的扁平抽取要 56 秒、非扁平超过 200 秒仍不返回；限量 100 只要 1.6 秒。
PLAYLIST_PREVIEW_LIMIT = 100
PLAYLIST_LIMIT_ALL = 0           # 0 = 整个播放列表
# 界面提供的条数选项（1 表示只下载当前视频，保持历史行为）
PLAYLIST_LIMITS = (1, 10, 25, 50, PLAYLIST_LIMIT_ALL)


def set_browser_cookies(browser: str):
    """设置 yt-dlp 从哪个浏览器读取 Cookie（空字符串为禁用）。"""
    browser_cookies.set_browser(browser)


def set_cookie_file(path: str):
    """设置手动导出的 cookies.txt（空字符串为禁用）。"""
    browser_cookies.set_cookie_file(path)


# 进度回调签名: callback(percent: float|None, speed_text: str, eta_text: str, status: str)
ProgressCallback = Callable[[Optional[float], str, str, str], None]


def format_size(num_bytes: Optional[int]) -> str:
    """把字节数格式化为 "245 MB" / "1.2 GB" 这样的短文本。"""
    if not num_bytes or num_bytes <= 0:
        return ""
    gb = num_bytes / 1024 / 1024 / 1024
    if gb >= 1:
        return f"{gb:.1f} GB"
    mb = num_bytes / 1024 / 1024
    if mb >= 10:
        return f"{mb:.0f} MB"
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{num_bytes / 1024:.0f} KB"


@dataclass
class QualityOption:
    """一个可选清晰度档位。

    label 是展示给用户的标准档位名（如 "1080p"，来自 YouTube 的 format_note），
    max_height 是该档位下格式的实际像素高度，用于构造 yt-dlp 的格式过滤条件。
    两者可能不同：宽银幕影片的 1080p 档实际高度可能是 804（1920x804）。
    size_bytes 是该档位视频流 + 最佳音频流合并后的估算总大小（可能为 None）。
    """

    label: str
    max_height: int
    size_bytes: Optional[int] = None

    @property
    def display(self) -> str:
        """下拉框展示文案，如 "1080p ・ 约 245 MB"。"""
        size = format_size(self.size_bytes)
        return f"{self.label} ・ 约 {size}" if size else self.label


@dataclass
class AudioOption:
    """一个可下载的 MP3 比特率档位。"""

    bitrate_kbps: int
    size_bytes: Optional[int] = None
    format_id: Optional[str] = None

    @property
    def display(self) -> str:
        size = format_size(self.size_bytes)
        label = f"{self.bitrate_kbps} kbps"
        return f"{label} ・ 约 {size}" if size else label


@dataclass
class VideoInfo:
    """probe() 的解析结果。"""

    url: str
    title: str
    duration: Optional[float]
    uploader: str
    qualities: List[QualityOption] = field(default_factory=list)  # 可用清晰度（降序）
    audio_options: List[AudioOption] = field(default_factory=list)  # 可用音频比特率（降序）
    warning: str = ""  # 非致命的降级提示（如浏览器 Cookie 读取失败后改用匿名解析）
    thumbnail: str = ""  # 视频封面直链，用于下载前的预览
    # 播放列表元信息；非播放列表时 playlist_count 为 0
    playlist_title: str = ""
    playlist_count: int = 0
    playlist_truncated: bool = False   # 条目数被 PLAYLIST_PREVIEW_LIMIT 截断
    playlist_entries: List[str] = field(default_factory=list)  # 探测到的条目标题
    # （最多 PLAYLIST_PREVIEW_LIMIT 个，供批量选择界面使用）

    @property
    def heights(self) -> List[int]:
        """各档位的标准高度数值（降序），如 [1080, 720, 480]。"""
        out = []
        for q in self.qualities:
            m = re.match(r"(\d+)", q.label)
            if m:
                out.append(int(m.group(1)))
        return out

    @property
    def duration_text(self) -> str:
        if not self.duration:
            return "未知时长"
        total = int(self.duration)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


class DownloadError(Exception):
    """对外暴露的下载/解析错误，message 为适合界面展示的中文文案。"""


class DownloadCancelled(Exception):
    """用户主动取消了下载。"""


class DownloadPaused(Exception):
    """用户暂停了下载：保留 .part 等临时文件，可凭 partial_files 在
    之后取消时清理；重新调用下载即可断点续传（yt-dlp 默认 continuedl）。"""

    def __init__(self, partial_files: Optional[set] = None):
        super().__init__("下载已暂停")
        self.partial_files: set = set(partial_files or ())


_js_runtimes_cache = None


def _js_runtimes() -> dict:
    """定位可用的 JS 运行时（deno / node），返回 yt-dlp `js_runtimes` 配置。

    背景：YouTube 的 web/tv 客户端需要 JS 运行时解 n/sig 挑战；启用浏览器
    Cookie 后 yt-dlp 会跳过不支持 Cookie 的客户端（如 android_vr），若此时
    没有可用 JS 运行时，所有音视频格式都会被丢弃，只剩 storyboard 预览图，
    任何格式选择器（含 best）都会报 "Requested format is not available"。

    打包成 .app 后 GUI 进程的 PATH 很短（不含 /opt/homebrew/bin 等），
    因此除 shutil.which 外还显式搜索常见安装路径。
    """
    global _js_runtimes_cache
    if _js_runtimes_cache is not None:
        return _js_runtimes_cache
    search = {
        "deno": ("/opt/homebrew/bin/deno", "/usr/local/bin/deno",
                 os.path.expanduser("~/.deno/bin/deno")),
        "node": ("/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node"),
    }
    runtimes = {}
    for name, fallbacks in search.items():
        path = shutil.which(name) or next(
            (p for p in fallbacks if os.path.isfile(p)), None)
        if path:
            runtimes[name] = {"path": path}
    # 一个都没找到时保留 yt-dlp 默认（deno），它会在缺失时自行降级
    _js_runtimes_cache = runtimes or {"deno": {}}
    return _js_runtimes_cache


def _base_opts(with_cookies: bool = True, platform: Optional[str] = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        # 不再强制 player_client=android：该客户端如今只返回单个低清格式
        # （如 360p），导致清晰度列表缺少 1080p 等档位。新版 yt-dlp 默认的
        # 客户端组合（tv/web 等）能拿到全部档位且下载不会 403。
        "js_runtimes": _js_runtimes(),
    }
    ffmpeg = ffmpeg_location_for_ytdlp()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    if with_cookies:
        opts.update(browser_cookies.ytdlp_options(platform))
    return opts


def subtitle_options(langs=None) -> dict:
    """构造 yt-dlp 的字幕下载参数。

    同时开启人工字幕与自动字幕：绝大多数 YouTube 视频只有自动字幕。

    语言列表只在明确给出时使用，并且**显式过滤掉 yt-dlp 的 "all"**：配合
    writeautomaticsub，它会把上百种自动字幕全部拉下来，几乎不可能是本意。
    过滤后为空就回落到默认语言。
    """
    if isinstance(langs, str):
        langs = [part.strip() for part in langs.split(",")]
    cleaned = []
    for lang in (langs or []):
        text = str(lang).strip()
        if text and text.lower() != "all":
            cleaned.append(text)
    if not cleaned:
        cleaned = list(DEFAULT_SUBTITLE_LANGS)
    return {
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": cleaned,
        "subtitlesformat": "srt/best",
    }


def _wrap_error(exc: Exception) -> DownloadError:
    msg = str(exc)
    # Cookie 读取失败要单独识别：yt-dlp 因为 os.walk 静默吞掉权限错误，
    # 会把搜索根目录当成数据库位置，报出无助于排查的消息（见
    # app/browser_cookies.py 的说明）。这里换成可操作的指引。
    if browser_cookies.is_load_error(exc):
        err = DownloadError(i18n.tr("error_browser_cookie_unavailable"))
        err.cookie_unavailable = True
        return err
    if "Requested format is not available" in msg or "Only images are available" in msg:
        err = DownloadError(i18n.tr("error_format_unavailable"))
        err.format_unavailable = True
        return err
    if "empty media response" in msg or "not granting access" in msg:
        return DownloadError(i18n.tr("error_instagram_login_video"))
    low = msg.lower()
    # —— 哔哩哔哩大会员 / 付费内容 ——
    if any(k in msg for k in ("大会员", "充电专属", "付费", "premium")) \
            or "vip" in low or "paid" in low:
        return DownloadError(i18n.tr("error_bilibili_premium"))
    # —— 抖音 / B 站风控（频繁访问被拦截，需登录或稍后重试） ——
    if any(k in msg for k in ("风控", "验证", "captcha", "rate limit",
                              "rate-limit", "too many", "frequently",
                              "blocked", "412")):
        return DownloadError(i18n.tr("error_rate_limit"))
    if "login_required" in msg or "Login required" in msg or "log in" in low \
            or "需要登录" in msg or "account_cookies" in msg:
        return DownloadError(i18n.tr("error_login_required"))
    if any(k in msg for k in ("geo", "region", "country", "地区", "区域",
                              "not available in your")):
        return DownloadError(i18n.tr("error_geo"))
    if "Unsupported URL" in msg:
        return DownloadError(i18n.tr("error_unsupported_video_url"))
    if "is not a valid URL" in msg:
        return DownloadError(i18n.tr("error_invalid_url"))
    # —— HTTP 403：多为下载 URL 过期/签名失效，常因 yt-dlp 版本落后于
    # YouTube 反爬更新。提示升级或重试，通常升级 yt-dlp 即可解决。 ——
    if "403" in msg or "forbidden" in low:
        return DownloadError(i18n.tr("error_forbidden"))
    if "unable to download" in low or any(
            k in msg for k in ("Network", "timed out", "getaddrinfo", "Connection")):
        return DownloadError(i18n.tr("error_network"))
    if "Private video" in msg or "members-only" in msg:
        return DownloadError(i18n.tr("error_private_video"))
    if any(k in msg for k in ("Video unavailable", "no longer available",
                              "not found", "404", "已失效", "稿件不可见",
                              "不存在", "已删除")):
        return DownloadError(i18n.tr("error_video_missing"))
    if "age" in low and "confirm" in low:
        return DownloadError(i18n.tr("error_age_restricted"))
    return DownloadError(i18n.tr("error_operation", message=msg[:200]))


def _has_usable_formats(info: dict) -> bool:
    """是否存在真正可下载的音/视频格式（排除 storyboard 预览图）。"""
    for f in info.get("formats") or []:
        if f.get("ext") == "mhtml":
            continue
        if f.get("vcodec") not in (None, "none") or f.get("acodec") not in (None, "none"):
            return True
    return False


def probe(url: str) -> VideoInfo:
    """解析视频信息（不下载），返回标题、时长与可用清晰度列表。

    启用浏览器 Cookie 且没有可用 JS 运行时时，YouTube 可能只返回
    storyboard 预览图；此时自动降级为匿名解析重试一次（匿名可用
    android_vr 等无需 JS 的客户端）。
    """
    url = (url or "").strip()
    if not url:
        raise DownloadError(i18n.tr("enter_video_link"))

    platform = platforms.detect(url)

    def extract(opts, target=None):
        # 解析阶段不做格式选择，避免无格式时直接抛
        # "Requested format is not available"
        opts["ignore_no_formats_error"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target or url, download=False)
        return info

    def probe_opts(with_cookies=True):
        opts = _base_opts(with_cookies=with_cookies, platform=platform)
        # 播放列表只做「扁平 + 限量」抽取（实测 5300 条的频道不限量要 56 秒，
        # 非扁平超过 200 秒仍不返回；限量 100 只要 1.6 秒）。
        # 关键是 noplaylist 保持 True：这样 "watch?v=X&list=Y" 仍旧按单个
        # 视频处理，历史行为不变，只有真正没有视频 ID 的播放列表链接才会
        # 走到下面的播放列表分支。
        opts.update({
            "extract_flat": "in_playlist",
            "playlistend": PLAYLIST_PREVIEW_LIMIT,
        })
        return opts

    def resolve(raw):
        """把（可能是播放列表的）解析结果规整为 (视频 info, 播放列表元信息)。

        扁平条目没有 formats，所以再完整解析第一个条目，界面的清晰度下拉才有
        内容；解析不出来就退回扁平条目，宁可没有档位表也不要整体失败。
        """
        empty = ("", 0, False, [], None)
        if raw is None or raw.get("_type") != "playlist":
            return raw, empty
        entries = [e for e in (raw.get("entries") or []) if e]
        if not entries:
            raise DownloadError(i18n.tr("error_no_video"))
        meta = (
            raw.get("title") or "",
            len(entries),
            len(entries) >= PLAYLIST_PREVIEW_LIMIT,
            [(e.get("title") or "").strip() for e in entries],
            entries[0],
        )
        first_url = entries[0].get("url") or entries[0].get("webpage_url") or ""
        resolved = None
        if first_url:
            # 带上 Cookie 解析一次；打包 App 未获完全磁盘访问权限读不到浏览器
            # Cookie 时，这里会失败——必须像单视频那样匿名重试，否则播放列表
            # 会没有档位表（界面上清晰度下拉是空的）。
            attempts = ((True, False) if browser_cookies.enabled(platform) else (True,))
            for with_cookies in attempts:
                try:
                    resolved = extract(
                        _base_opts(with_cookies=with_cookies, platform=platform),
                        target=first_url)
                    break
                except Exception:
                    resolved = None
        return (resolved or entries[0]), meta

    # cookie_error 记录「浏览器 Cookie 读取失败」；此时不再直接报错，
    # 而是先尝试匿名解析：不需要登录的内容仍能正常使用。
    cookie_error = None
    try:
        info = extract(probe_opts())
    except DownloadError:
        raise
    except Exception as exc:  # yt_dlp 抛出多种异常类型，统一包装
        wrapped = _wrap_error(exc)
        if getattr(wrapped, "cookie_unavailable", False):
            cookie_error = wrapped
            info = None
        elif browser_cookies.enabled() and getattr(wrapped, "format_unavailable", False):
            info = None
        else:
            raise wrapped from exc

    info, playlist = resolve(info)

    if info is None or not _has_usable_formats(info):
        if browser_cookies.enabled(platform):
            try:
                retry = extract(probe_opts(with_cookies=False))
            except DownloadError:
                retry = None
            except Exception:
                retry = None
            if retry is not None:
                retry_info, retry_playlist = resolve(retry)
                if _has_usable_formats(retry_info) or retry_playlist[1]:
                    info, playlist = retry_info, retry_playlist
        if info is None or not _has_usable_formats(info):
            if playlist[1]:
                # 播放列表：第一个条目拿不到档位表也照常放行，下载按最佳画质，
                # 不能因为一个条目解析不出格式就让整个播放列表失败。
                info = info or playlist[4] or {}
            elif cookie_error is not None:
                # Cookie 读取失败且匿名也拿不到格式时才报错，此时 Cookie 就是
                # 最可能的原因，直接给出可操作的指引。
                raise cookie_error
            else:
                raise _wrap_error(Exception("Requested format is not available"))

    # 按标准档位（format_note 中的 1080p/720p…）归并清晰度。
    # 直接用 height 会得到 804/536 这类非标准数值（宽银幕视频的实际高度），
    # 因此优先解析 format_note 得到标准档位名，同时记录该档位的实际最大高度
    # 供下载时构造 height<=N 的过滤条件。
    formats = info.get("formats") or []

    # 最佳音频的估算大小（下载时与视频流合并，需计入总大小）
    audio_only = [
        f for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    best_audio = max(
        audio_only, key=lambda f: f.get("abr") or f.get("tbr") or 0, default=None
    )
    audio_size = (
        (best_audio.get("filesize") or best_audio.get("filesize_approx"))
        if best_audio else None
    )

    # 音频模式会转为对应比特率的 MP3。将站点提供的音频码率归一到
    # 常见 MP3 档位，再用时长估算转码后的容量。单流站点则从含音轨的
    # 视频格式中提取音频。
    audio_candidates = audio_only or [
        f for f in formats if f.get("acodec") not in (None, "none")
    ]
    standard_bitrates = (32, 48, 64, 96, 128, 160, 192, 256, 320)
    audio_by_bitrate = {}
    for fmt in audio_candidates:
        raw_bitrate = fmt.get("abr") or fmt.get("tbr")
        if not raw_bitrate:
            continue
        bitrate = min(standard_bitrates, key=lambda value: abs(value - raw_bitrate))
        current = audio_by_bitrate.get(bitrate)
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        # 同档优先使用纯音频流，其次使用更接近目标档位的流。
        rank = (
            fmt.get("vcodec") in (None, "none"),
            -abs(float(raw_bitrate) - bitrate),
        )
        if current is None or rank > current[0]:
            audio_by_bitrate[bitrate] = (rank, fmt, size)

    audio_options = []
    for bitrate, (_, fmt, source_size) in sorted(audio_by_bitrate.items(), reverse=True):
        estimated_size = None
        if info.get("duration"):
            estimated_size = int(float(info["duration"]) * bitrate * 1000 / 8)
        elif source_size:
            estimated_size = int(source_size)
        audio_options.append(AudioOption(
            bitrate_kbps=bitrate,
            size_bytes=estimated_size,
            format_id=str(fmt.get("format_id")) if fmt.get("format_id") is not None else None,
        ))

    tiers = {}  # 标准档位数值 -> {"h": 实际最大高度, "fmt": 该档位最佳视频格式}
    for f in formats:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        note = f.get("format_note") or ""
        m = re.search(r"(\d{3,4})p", note)
        tier = int(m.group(1)) if m else int(h)
        entry = tiers.setdefault(tier, {"h": 0, "fmt": None})
        entry["h"] = max(entry["h"], int(h))
        # 模拟 bestvideo 的选择倾向：优先纯视频流，其次更高分辨率/码率
        key = (
            f.get("acodec") in (None, "none"),
            int(h),
            f.get("tbr") or 0,
        )
        if entry["fmt"] is None or key > entry["fmt"][0]:
            entry["fmt"] = (key, f)

    qualities = []
    for tier, entry in sorted(tiers.items(), reverse=True):
        key, fmt = entry["fmt"]
        vsize = fmt.get("filesize") or fmt.get("filesize_approx")
        size = None
        if vsize:
            size = int(vsize)
            # 纯视频流需要再叠加合并进来的最佳音频大小
            if fmt.get("acodec") in (None, "none") and audio_size:
                size += int(audio_size)
        qualities.append(
            QualityOption(label=f"{tier}p", max_height=entry["h"], size_bytes=size)
        )

    return VideoInfo(
        url=url,
        title=info.get("title") or "未命名视频",
        duration=info.get("duration"),
        uploader=info.get("uploader") or info.get("channel") or "",
        qualities=qualities,
        audio_options=audio_options,
        thumbnail=info.get("thumbnail") or "",
        playlist_title=playlist[0],
        playlist_count=playlist[1],
        playlist_truncated=playlist[2],
        playlist_entries=playlist[3],
        # 解析成功但浏览器 Cookie 被跳过：如实告知，避免用户以为用的是登录态。
        warning=i18n.tr("cookie_fallback_warning") if cookie_error is not None else "",
    )


def _playlist_position(d: dict, playlist_total: Optional[int] = None):
    """从进度回调里取播放列表位置。

    ``playlist_count`` 在 yt-dlp 里经常是 None（实测 YouTube 频道页就不给），
    所以总数优先取 info 里的，取不到就用调用方已知的总数（限量值或解析结果）。
    两者都没有时返回 (None, None)，界面就只能退化成「第 i 个」。
    """
    info = d.get("info_dict") or {}
    index = info.get("playlist_index") or info.get("playlist_autonumber")
    count = info.get("playlist_count") or playlist_total
    title = info.get("title") or ""
    if not index or not count:
        return index, None, title
    return int(index), int(count), title


def _make_progress_hook(
    callback: Optional[ProgressCallback],
    cancel_event: Optional[threading.Event] = None,
    seen_files: Optional[set] = None,
    pause_event: Optional[threading.Event] = None,
    entry_callback: Optional[Callable[[int, int, str], None]] = None,
    playlist_total: Optional[int] = None,
):
    # bestvideo+bestaudio 会为同一个条目产生两段进度（视频流下完再下音频流），
    # 第二段从 0 开始，直接换算会让整批进度条倒退。用单调不减钳制住。
    highest = {"value": 0.0}
    last_entry = {"value": None}

    def hook(d: dict):
        # 记录 yt-dlp 正在写入的文件，供取消后清理 .part 等残留
        if seen_files is not None:
            for key in ("filename", "tmpfilename"):
                fn = d.get(key)
                if fn:
                    seen_files.add(fn)
        if cancel_event is not None and cancel_event.is_set():
            raise yt_dlp.utils.DownloadCancelled("用户取消下载")
        if pause_event is not None and pause_event.is_set():
            # 与取消一样借 DownloadCancelled 中断 yt-dlp，
            # 但 _run_download 中按 pause_event 区分处理，保留临时文件
            raise yt_dlp.utils.DownloadCancelled("用户暂停下载")
        index, count, entry_title = _playlist_position(d, playlist_total)
        if entry_callback is not None and index:
            # 进度钩子每秒触发几十次；只在「换条目」时通知界面，避免刷爆状态栏
            current = (index, entry_title)
            if current != last_entry["value"]:
                last_entry["value"] = current
                entry_callback(index, count or 0, entry_title)
        if callback is None:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            percent = (downloaded / total * 100) if total else None
            if percent is not None and count and index:
                # 播放列表：进度条表示整批的进度，而不是当前这一个文件。
                # 上限取 min：总数估小（例如 probe 只看到前 100 条）时不要把
                # 进度条推过 100%。
                overall = min(100.0, ((index - 1) + percent / 100.0) / count * 100)
                percent = max(highest["value"], overall)
                highest["value"] = percent

            speed = d.get("speed")
            speed_text = f"{speed / 1024 / 1024:.2f} MB/s" if speed else "--"

            eta = d.get("eta")
            if eta is not None:
                m, s = divmod(int(eta), 60)
                eta_text = f"{m}分{s:02d}秒" if m else f"{s}秒"
            else:
                eta_text = "--"
            callback(percent, speed_text, eta_text, "downloading")
        elif status == "finished":
            # 播放列表里每个条目结束后都会到 finished，此时不能把整条进度
            # 拉满（否则第一个视频下完进度条就显示 100%）
            done = min(100.0, index / count * 100) if (count and index) else 100.0
            if count and index:
                done = max(highest["value"], done)
                highest["value"] = done
            callback(done, "--", "--", "merging")

    return hook


def cleanup_partial_files(seen_files: Optional[set]):
    """删除取消下载后残留的临时文件（.part / .ytdl / 分段碎片等）。"""
    for fn in sorted(seen_files or ()):
        candidates = {fn, fn + ".part", fn + ".ytdl"}
        candidates.update(glob.glob(glob.escape(fn) + ".part*"))
        candidates.update(glob.glob(glob.escape(fn) + "-Frag*"))
        for path in candidates:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass


def _run_download(
    url: str,
    opts: dict,
    cancel_event: Optional[threading.Event] = None,
    seen_files: Optional[set] = None,
    pause_event: Optional[threading.Event] = None,
    collected: Optional[list] = None,
    info_out: Optional[list] = None,
) -> str:
    """执行下载并返回最终产物文件路径。

    collected 传入列表时，所有后处理完成的文件都会被追加进去（播放列表会有
    多个产物），返回值仍是最后一个产物路径。
    """
    final_path = {"value": None}

    def on_postprocess(d):
        # 后处理（合并/转码）结束后记录最终文件名
        if d.get("status") == "finished":
            info = d.get("info_dict") or {}
            path = info.get("filepath") or info.get("_filename")
            if path:
                final_path["value"] = path
                if collected is not None and path not in collected:
                    collected.append(path)

    opts = dict(opts)
    opts.setdefault("postprocessor_hooks", []).append(on_postprocess)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadCancelled as exc:
        # 取消优先于暂停：即使两个事件都置位也按取消处理（清理临时文件）
        if cancel_event is not None and cancel_event.is_set():
            cleanup_partial_files(seen_files)
            raise DownloadCancelled() from exc
        if pause_event is not None and pause_event.is_set():
            raise DownloadPaused(seen_files) from exc
        cleanup_partial_files(seen_files)
        raise DownloadCancelled() from exc
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            cleanup_partial_files(seen_files)
            raise DownloadCancelled() from exc
        if pause_event is not None and pause_event.is_set():
            raise DownloadPaused(seen_files) from exc
        raise _wrap_error(exc) from exc

    if info_out is not None:
        # 必须在把播放列表拆成第一个条目之前交出去
        info_out.append(info)

    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        info = entries[0] if entries else {}

    path = (
        final_path["value"]
        or (info.get("requested_downloads") or [{}])[0].get("filepath")
        or info.get("filepath")
        or info.get("_filename")
    )
    if not path or not os.path.isfile(path):
        raise DownloadError(i18n.tr("error_output_missing"))
    return path


def _run_download_with_cookie_fallback(url, opts, **kw) -> str:
    """执行下载；启用 Cookie 时若因「无可用格式」失败，匿名重试一次。

    与 probe 的降级逻辑对应：带 Cookie 的 web/tv 客户端在缺少 JS 运行时的
    环境（如打包后的 .app）会丢弃全部音视频格式，匿名重试可走 android_vr。
    """
    try:
        return _run_download(url, opts, **kw)
    except DownloadError as exc:
        recoverable = (
            getattr(exc, "cookie_unavailable", False)
            or (browser_cookies.enabled(platforms.detect(url))
                and getattr(exc, "format_unavailable", False))
        )
        if not recoverable:
            raise
        retry_opts = dict(opts)
        retry_opts.pop("cookiesfrombrowser", None)
        retry_opts.pop("cookiefile", None)
        return _run_download(url, retry_opts, **kw)


def download_video(
    url: str,
    height: Optional[int] = None,
    save_dir: str = DEFAULT_SAVE_DIR,
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
    subtitles: bool = False,
    subtitle_langs=None,
) -> str:
    """下载视频（指定最大清晰度），由 ffmpeg 合并为单个 mp4，返回文件路径。

    cancel_event 置位后会在下一次进度回调时中断下载，抛出 DownloadCancelled
    并清理 .part 等临时文件。
    pause_event 置位则中断但保留临时文件，抛出 DownloadPaused（携带
    partial_files）；用相同参数重新调用本函数即可从 .part 断点续传。
    """
    os.makedirs(save_dir, exist_ok=True)
    if height:
        # 兜底链：限高的「视频+音频」→ 限高的单文件 → 不限高的「视频+音频」
        # → 任意单文件。即使档位信息过期/不准确也不会直接报错。
        fmt = (
            f"bestvideo[height<={height}]+bestaudio"
            f"/best[height<={height}]"
            f"/bestvideo+bestaudio/best"
        )
    else:
        fmt = "bestvideo+bestaudio/best"

    seen_files: set = set()
    opts = _base_opts(platform=platforms.detect(url))
    if subtitles:
        opts.update(subtitle_options(subtitle_langs))
    opts.update(
        {
            "format": fmt,
            "outtmpl": os.path.join(save_dir, "%(title).120B [%(id)s].%(ext)s"),
            "merge_output_format": "mp4",
            "progress_hooks": [
                _make_progress_hook(progress, cancel_event, seen_files, pause_event)
            ],
        }
    )
    return _run_download_with_cookie_fallback(
        url, opts, cancel_event=cancel_event,
        seen_files=seen_files, pause_event=pause_event)


def _paths_from_info(info) -> List[str]:
    """从 yt-dlp 返回的 info（可能是播放列表）里按顺序取出产物路径。

    不能只依赖后处理钩子：站点给出单文件格式时不会发生合并，钩子就不会触发，
    那时只能从 info 的 filepath / requested_downloads 里取。
    """
    out: List[str] = []

    def add(entry):
        if not isinstance(entry, dict):
            return
        for candidate in (
            (entry.get("requested_downloads") or [{}])[0].get("filepath"),
            entry.get("filepath"),
            entry.get("_filename"),
        ):
            if candidate and os.path.isfile(candidate) and candidate not in out:
                out.append(candidate)
                return

    if isinstance(info, dict) and info.get("_type") == "playlist":
        for entry in (info.get("entries") or []):
            add(entry)
    add(info)
    return out


def _clean_playlist_items(items) -> List[int]:
    """整理界面传来的条目下标：去重、排序、只保留正整数。

    界面的勾选顺序不该影响结果，所以统一排序；非法值一律丢弃而不是报错——
    这只是个筛选条件，不该让整个下载失败。
    """
    if not items:
        return []
    cleaned = set()
    for raw in items:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            cleaned.add(value)
    return sorted(cleaned)


def _playlist_index_width(size_hint) -> int:
    """序号补零宽度：按已知条目数决定，最少 2 位。

    文件名按字典序排序时，不补零会出现 1, 10, 100, 2 这种顺序。总数未知时
    退到 2 位（yt-dlp 对超出的位数不会截断，只是不再对齐）。
    """
    try:
        total = int(size_hint or 0)
    except (TypeError, ValueError):
        total = 0
    return max(2, len(str(total))) if total > 0 else 2


def _playlist_outtmpl(folder: str, size_hint=None) -> str:
    """播放列表条目的输出模板：``序号 - 标题 [id].ext``。

    ``%(playlist_index|0)0Nd`` 的 ``|0`` 兜底是为了极少数不提供序号的抽取器：
    不加兜底 yt-dlp 会填成 "NA"，加了我们最多看到一个 "0"。
    """
    width = _playlist_index_width(size_hint)
    return os.path.join(folder, f"%(playlist_index|0)0{width}d - %(title).120B [%(id)s].%(ext)s")


def _safe_dirname(name: str) -> str:
    """把播放列表标题变成安全的目录名。"""
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip()).strip(" .")
    return cleaned[:120] or "playlist"


def download_playlist(
    url: str,
    height: Optional[int] = None,
    save_dir: str = DEFAULT_SAVE_DIR,
    playlist_limit: int = PLAYLIST_LIMIT_ALL,
    playlist_title: str = "",
    progress: Optional[ProgressCallback] = None,
    entry_progress=None,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
    subtitles: bool = False,
    subtitle_langs=None,
    playlist_total: Optional[int] = None,
    playlist_size_hint: Optional[int] = None,
    playlist_items=None,
) -> List[str]:
    """下载播放列表，返回所有产物路径（按列表顺序）。

    playlist_items 给定时只下载这些条目（1 基下标，如 [1, 3, 5]），此时忽略
    playlist_limit；文件名里的序号仍用条目在播放列表中的原始位置。

    playlist_size_hint 只用于决定文件名的序号补零宽度（总数未知时也没关系）。

    playlist_limit 为 0 表示整个播放列表，否则只下载前 N 个（playlistend）。
    整批进度由 progress 回调给出（percent 是整批的百分比），单个条目的位置
    通过 entry_progress(index, count, title) 回调告知界面；count 可能为 0，
    因为 yt-dlp 常常不提供总数（此时需要调用方用 playlist_total 传入）。

    产物落在 save_dir/<播放列表标题>/ 下，避免不同条目的同名文件互相覆盖。

    取消/暂停语义与 download_video 一致；续传时重复调用本函数即可，yt-dlp 会
    跳过已下载的条目。
    """
    os.makedirs(save_dir, exist_ok=True)
    if height:
        fmt = (
            f"bestvideo[height<={height}]+bestaudio"
            f"/best[height<={height}]"
            f"/bestvideo+bestaudio/best"
        )
    else:
        fmt = "bestvideo+bestaudio/best"

    seen_files: set = set()
    collected: List[str] = []
    info_out: List[dict] = []
    opts = _base_opts(platform=platforms.detect(url))
    opts.update({
        "noplaylist": False,
        "format": fmt,
        "outtmpl": _playlist_outtmpl(
            os.path.join(save_dir, _safe_dirname(playlist_title)),
            playlist_size_hint if playlist_size_hint is not None else playlist_total),
        "merge_output_format": "mp4",
        "progress_hooks": [
            _make_progress_hook(progress, cancel_event, seen_files, pause_event,
                                entry_progress, playlist_total)
        ],
    })
    selected = _clean_playlist_items(playlist_items)
    limit = int(playlist_limit or 0)
    if selected:
        # 指定条目时用 playlist_items（yt-dlp 接受 "1,3,5-7" 这种写法）
        opts["playlist_items"] = ",".join(str(i) for i in selected)
    elif limit > 0:
        opts["playlistend"] = limit
    if subtitles:
        opts.update(subtitle_options(subtitle_langs))

    _run_download_with_cookie_fallback(
        url, opts, cancel_event=cancel_event, seen_files=seen_files,
        pause_event=pause_event, collected=collected, info_out=info_out)

    paths = _paths_from_info(info_out[0] if info_out else {})
    for path in collected:
        if path not in paths and os.path.isfile(path):
            paths.append(path)
    if not paths:
        raise DownloadError(i18n.tr("error_output_missing"))
    return paths


def _audio_postprocessor(audio_format: str, bitrate_kbps: int) -> dict:
    """构造 FFmpegExtractAudio 后处理器。

    flac 是无损容器，传 preferredquality 没有意义（ffmpeg 会忽略甚至报错），
    因此只在有损格式上设置比特率。
    """
    codec = (audio_format or DEFAULT_AUDIO_FORMAT).strip().lower()
    if codec not in AUDIO_FORMATS:
        codec = DEFAULT_AUDIO_FORMAT
    config = {"key": "FFmpegExtractAudio", "preferredcodec": codec}
    if codec not in LOSSLESS_AUDIO_FORMATS:
        config["preferredquality"] = str(bitrate_kbps)
    return config


def download_audio(
    url: str,
    save_dir: str = DEFAULT_SAVE_DIR,
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
    bitrate_kbps: int = 192,
    format_id: Optional[str] = None,
    audio_format: str = DEFAULT_AUDIO_FORMAT,
) -> str:
    """仅下载音频并按 ``audio_format`` 转码，返回文件路径。"""
    os.makedirs(save_dir, exist_ok=True)
    seen_files: set = set()
    opts = _base_opts(platform=platforms.detect(url))
    opts.update(
        {
            "format": f"{format_id}/bestaudio/best" if format_id else "bestaudio/best",
            # 文件名加 [audio] 后缀，避免中间文件与同名视频文件冲突
            # （否则提取 mp3 后会把同名 .mp4 当作源文件删除）
            "outtmpl": os.path.join(save_dir, "%(title).120B [%(id)s] [audio].%(ext)s"),
            "progress_hooks": [
                _make_progress_hook(progress, cancel_event, seen_files, pause_event)
            ],
            "postprocessors": [
                _audio_postprocessor(audio_format, bitrate_kbps)
            ],
        }
    )
    return _run_download_with_cookie_fallback(
        url, opts, cancel_event=cancel_event,
        seen_files=seen_files, pause_event=pause_event)
