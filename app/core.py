"""yt-dlp 封装：视频信息解析、清晰度列表、视频/音频下载与进度回调。

支持 YouTube / X（Twitter）/ Instagram / 抖音（Douyin）/ 哔哩哔哩（Bilibili）
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
from .ffmpeg_util import ffmpeg_location_for_ytdlp

DEFAULT_SAVE_DIR = os.path.expanduser("~/Downloads")

_browser_cookies = ""  # 空 = 不读取浏览器 Cookie


def set_browser_cookies(browser: str):
    """设置 yt-dlp 从哪个浏览器读取 Cookie（空字符串为禁用）。"""
    global _browser_cookies
    _browser_cookies = (browser or "").strip().lower()

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
class VideoInfo:
    """probe() 的解析结果。"""

    url: str
    title: str
    duration: Optional[float]
    uploader: str
    qualities: List[QualityOption] = field(default_factory=list)  # 可用清晰度（降序）

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


def _base_opts(with_cookies: bool = True) -> dict:
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
    if with_cookies and _browser_cookies:
        opts["cookiesfrombrowser"] = (_browser_cookies,)
    return opts


def _wrap_error(exc: Exception) -> DownloadError:
    msg = str(exc)
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

    def extract(opts):
        # 解析阶段不做格式选择，避免无格式时直接抛
        # "Requested format is not available"
        opts["ignore_no_formats_error"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        # 播放列表/多视频时取第一个条目
        if info.get("_type") == "playlist":
            entries = info.get("entries") or []
            if not entries:
                raise DownloadError(i18n.tr("error_no_video"))
            info = entries[0]
        return info

    try:
        info = extract(_base_opts())
    except DownloadError:
        raise
    except Exception as exc:  # yt_dlp 抛出多种异常类型，统一包装
        wrapped = _wrap_error(exc)
        if not (_browser_cookies and getattr(wrapped, "format_unavailable", False)):
            raise wrapped from exc
        info = None

    if info is None or not _has_usable_formats(info):
        if _browser_cookies:
            try:
                retry = extract(_base_opts(with_cookies=False))
            except DownloadError:
                retry = None
            except Exception:
                retry = None
            if retry is not None and _has_usable_formats(retry):
                info = retry
        if info is None or not _has_usable_formats(info):
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
    )


def _make_progress_hook(
    callback: Optional[ProgressCallback],
    cancel_event: Optional[threading.Event] = None,
    seen_files: Optional[set] = None,
    pause_event: Optional[threading.Event] = None,
):
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
        if callback is None:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            percent = (downloaded / total * 100) if total else None

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
            callback(100.0, "--", "--", "merging")

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
) -> str:
    """执行下载并返回最终产物文件路径。"""
    final_path = {"value": None}

    def on_postprocess(d):
        # 后处理（合并/转码）结束后记录最终文件名
        if d.get("status") == "finished":
            info = d.get("info_dict") or {}
            path = info.get("filepath") or info.get("_filename")
            if path:
                final_path["value"] = path

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
        if not (_browser_cookies and getattr(exc, "format_unavailable", False)):
            raise
        retry_opts = dict(opts)
        retry_opts.pop("cookiesfrombrowser", None)
        return _run_download(url, retry_opts, **kw)


def download_video(
    url: str,
    height: Optional[int] = None,
    save_dir: str = DEFAULT_SAVE_DIR,
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
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
    opts = _base_opts()
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


def download_audio(
    url: str,
    save_dir: str = DEFAULT_SAVE_DIR,
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    pause_event: Optional[threading.Event] = None,
) -> str:
    """仅下载音频并转为 mp3，返回文件路径。"""
    os.makedirs(save_dir, exist_ok=True)
    seen_files: set = set()
    opts = _base_opts()
    opts.update(
        {
            "format": "bestaudio/best",
            # 文件名加 [audio] 后缀，避免中间文件与同名视频文件冲突
            # （否则提取 mp3 后会把同名 .mp4 当作源文件删除）
            "outtmpl": os.path.join(save_dir, "%(title).120B [%(id)s] [audio].%(ext)s"),
            "progress_hooks": [
                _make_progress_hook(progress, cancel_event, seen_files, pause_event)
            ],
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
    )
    return _run_download_with_cookie_fallback(
        url, opts, cancel_event=cancel_event,
        seen_files=seen_files, pause_event=pause_event)
