"""图片引擎：用 gallery-dl 解析 X / Instagram 帖子里的图片列表，
再用 requests 自行下载（便于实现单张/多张选择、进度与取消）。

- X 图片走 gallery-dl 的 twitter 提取器，默认返回 `name=orig` 原图链接。
- Instagram 走 instagram 提取器；匿名访问常被平台重定向到登录页，
  此时抛出带中文提示的 ImageError，界面可降级展示。
- 微博图片通过 yt-dlp 建立访客会话后读取微博状态 API；图片、GIF，以及混合帖
  中的 MP4/MOV 共用同一套缩略图、多选、批量下载流程。
"""

import os
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional
import requests
import yt_dlp

from . import i18n
from gallery_dl import config as gdl_config
from gallery_dl import job as gdl_job

DEFAULT_SAVE_DIR = os.path.expanduser("~/Downloads")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_WEIBO_VIDEO_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/146.0.0.0 Safari/537.36")

# 进度回调签名: callback(percent: float, text: str)
ImageProgress = Callable[[float, str], None]

_browser_cookies = ""  # 空 = 不读取浏览器 Cookie


def set_browser_cookies(browser: str):
    """设置 gallery-dl 从哪个浏览器读取 Cookie（空字符串为禁用）。"""
    global _browser_cookies
    _browser_cookies = (browser or "").strip().lower()


def _media_headers(url: str) -> dict:
    low = (url or "").lower()
    headers = {"User-Agent": _WEIBO_VIDEO_UA if "weibocdn.com" in low else UA}
    if "sinaimg.cn" in low or "weibocdn.com" in low:
        headers["Referer"] = "https://weibo.com/"
    return headers


class ImageError(Exception):
    """对外暴露的图片解析/下载错误，message 为中文文案。"""


class ImageCancelled(Exception):
    """用户取消了图片下载。"""


@dataclass
class ImageItem:
    """帖子中的一个可下载媒体项。"""

    url: str                      # 最高清原图直链
    thumb_url: str                # 缩略图直链（供界面预览）
    index: int                    # 帖内序号（1 开始）
    width: Optional[int] = None
    height: Optional[int] = None
    extension: str = "jpg"
    filename: str = ""            # 不含扩展名的建议文件名
    media_type: str = "image"      # image / video；微博混合帖可能同时包含两者

    @property
    def size_text(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return ""


@dataclass
class ImagePost:
    """probe_images() 的解析结果。"""

    url: str
    title: str
    author: str
    items: List[ImageItem] = field(default_factory=list)

    @property
    def has_video(self) -> bool:
        return any(item.media_type == "video" for item in self.items)


def _twitter_thumb(url: str) -> str:
    """把 pbs.twimg.com 的 orig 原图链接降为 small 档作缩略图。"""
    if "pbs.twimg.com" in url and "name=" in url:
        return re.sub(r"name=\w+", "name=small", url)
    return url


def _probe_error(name: str, msg: str, url: str) -> ImageError:
    low = (msg or "").lower()
    is_ig = "instagram" in url.lower()
    if "login" in low or "authrequired" in name.lower() or "authenticated" in low \
            or "authorization" in low:
        if is_ig:
            return ImageError(i18n.tr("error_images_login_instagram"))
        return ImageError(i18n.tr("error_images_login"))
    if "404" in msg or "notfound" in name.lower() or "not found" in low:
        return ImageError(i18n.tr("error_post_missing"))
    if any(k in low for k in ("timed out", "timeout", "connection", "getaddrinfo",
                              "network", "unreachable")):
        return ImageError(i18n.tr("error_network"))
    if "nomatch" in name.lower() or "unsupported" in low:
        return ImageError(i18n.tr("error_image_url"))
    return ImageError(i18n.tr("error_image_probe", name=name, message=msg[:160]))


_WEIBO_STATUS_RE = re.compile(
    r"(?:m\.weibo\.cn/(?:status|detail)/(?P<mobile>\d+)"
    r"|weibo\.com/0/(?P<zero>\d+))",
    re.IGNORECASE,
)


def _weibo_status_id(url: str, info: Optional[dict]) -> Optional[str]:
    """从直链或 yt-dlp 结果中取得微博状态 ID。"""
    match = _WEIBO_STATUS_RE.search(url or "")
    if match:
        return match.group("mobile") or match.group("zero")
    if isinstance(info, dict):
        value = info.get("id")
        if value:
            return str(value)
        entries = info.get("entries") or []
        for entry in entries:
            value = entry.get("id") if isinstance(entry, dict) else None
            if value:
                return str(value)
    return None


def _weibo_ytdlp_options() -> dict:
    # 图片微博经常会被 yt-dlp 识别为“没有视频格式”；保留解析结果中的
    # 状态 ID，后续仍可从微博状态 API 读取图片/GIF。
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignore_no_formats_error": True,
    }
    if _browser_cookies:
        options["cookiesfrombrowser"] = (_browser_cookies,)
    return options


def _weibo_session(ydl: yt_dlp.YoutubeDL) -> requests.Session:
    """把 yt-dlp 生成的微博访客/浏览器 Cookie 转给 requests。"""
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://weibo.com/"})
    for cookie in ydl.cookiejar:
        session.cookies.set(cookie.name, cookie.value,
                            domain=cookie.domain, path=cookie.path)
    return session


def _weibo_media_url(pic_id: str, size: str = "large") -> str:
    # 微博图片 ID 不携带扩展名；CDN 会根据图片实际内容返回 JPEG/GIF。
    return f"https://wx1.sinaimg.cn/{size}/{pic_id}.jpg"


def _weibo_meta_url(meta: dict, key: str) -> Optional[str]:
    value = meta.get(key)
    if isinstance(value, dict):
        value = value.get("url")
    return value if isinstance(value, str) and value else None


_WEIBO_VIDEO_KEYS = (
    "mp4_720p_mp4", "hevc_mp4_720p", "mp4_hd_url", "stream_url_hd",
    "mp4_sd_url", "stream_url", "url",
)


def _weibo_video_url(value) -> Optional[str]:
    """从微博状态的多种视频字段中选出一个可直接下载的 MP4。"""
    if not isinstance(value, dict):
        return None

    for key in ("video", "livephoto", "live_photo", "video_info"):
        nested = value.get(key)
        if isinstance(nested, str) and nested.startswith(("http://", "https://")):
            if ".m3u8" not in nested.lower():
                return nested
        nested_url = _weibo_video_url(nested)
        if nested_url:
            return nested_url

    for key in _WEIBO_VIDEO_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, dict):
            candidate = candidate.get("url")
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            # m3u8 是播放清单，不是 download_images 可直接保存的媒体文件。
            if ".m3u8" not in candidate.lower():
                return candidate

    playback = value.get("playback_list")
    if isinstance(playback, list):
        candidates = []
        for entry in playback:
            if not isinstance(entry, dict):
                continue
            play_info = entry.get("play_info") or {}
            candidate = play_info.get("url")
            if not isinstance(candidate, str) or ".m3u8" in candidate.lower():
                continue
            candidates.append((play_info.get("height") or 0, candidate))
        if candidates:
            return max(candidates, key=lambda pair: pair[0])[1]
    return None


def _weibo_video_extension(url: str) -> str:
    path = url.split("?", 1)[0].lower()
    for ext in ("mp4", "mov", "m4v", "webm"):
        if path.endswith("." + ext):
            return ext
    return "mp4"


def _weibo_video_infos(data: dict) -> List[dict]:
    """返回状态中除图片外的混合媒体视频描述。"""
    infos = []
    mix_info = data.get("mix_media_info")
    mix_items = mix_info.get("items") if isinstance(mix_info, dict) else None
    if isinstance(mix_items, list):
        for item in mix_items:
            if not isinstance(item, dict) or item.get("type") == "pic":
                continue
            payload = item.get("data") if isinstance(item.get("data"), dict) else item
            media_info = payload.get("media_info") if isinstance(payload, dict) else None
            infos.append(media_info if isinstance(media_info, dict) else payload)

    page_info = data.get("page_info")
    if isinstance(page_info, dict):
        media_info = page_info.get("media_info")
        if isinstance(media_info, dict) and page_info.get("object_type") == "video":
            infos.append(media_info)
    return infos


def _weibo_image_type(session: requests.Session, thumb_url: str) -> str:
    """用小缩略图的 magic bytes 判断微博图片是否为 GIF。"""
    try:
        response = session.get(thumb_url, stream=True, timeout=(5, 15))
        response.raise_for_status()
        head = response.raw.read(12)
        content_type = (response.headers.get("Content-Type") or "").lower()
        response.close()
        if head.startswith(b"GIF8") or "image/gif" in content_type:
            return "gif"
    except requests.RequestException:
        # 缩略图仅用于识别/预览，识别失败时仍保留可下载的原图入口。
        pass
    return "jpg"


def _probe_weibo_images(url: str) -> ImagePost:
    """解析微博状态中的图片和 GIF。"""
    ydl = yt_dlp.YoutubeDL(_weibo_ytdlp_options())
    info = None
    extract_error = None
    try:
        info = ydl.extract_info(url, download=False)
    except Exception as exc:
        # 图片微博可能没有视频格式，yt-dlp 解析失败并不代表状态 API
        # 不可访问；直链含数字 ID 时仍可继续使用已建立的访客会话。
        extract_error = exc

    status_id = _weibo_status_id(url, info)
    if not status_id:
        if extract_error:
            raise _probe_error(extract_error.__class__.__name__, str(extract_error), url)
        raise ImageError(i18n.tr("error_image_url"))

    session = _weibo_session(ydl)
    try:
        response = session.get(
            "https://weibo.com/ajax/statuses/show",
            params={"id": status_id}, timeout=(8, 20))
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise _probe_error(exc.__class__.__name__, str(exc), url) from exc

    if not isinstance(data, dict) or data.get("ok") is False:
        raise ImageError(i18n.tr("error_images_login"))

    pic_ids = data.get("pic_ids") or []
    pic_infos = data.get("pic_infos") or {}
    if not pic_ids:
        raise ImageError(i18n.tr("error_no_images", suffix=""))

    title = re.sub(r"\s+", " ", data.get("text_raw") or "").strip()
    user = data.get("user") or {}
    author = user.get("screen_name") or user.get("nick") or "" \
        if isinstance(user, dict) else ""
    mblog_id = str(data.get("mblogid") or status_id)
    items = []
    video_records = []
    seen_video_urls = set()
    for index, pic_id in enumerate(pic_ids, start=1):
        meta = pic_infos.get(pic_id) if isinstance(pic_infos, dict) else {}
        meta = meta if isinstance(meta, dict) else {}
        original = (_weibo_meta_url(meta, "original")
                    or _weibo_meta_url(meta, "largest")
                    or _weibo_media_url(str(pic_id), "large"))
        thumb = (_weibo_meta_url(meta, "thumbnail")
                 or _weibo_meta_url(meta, "bmiddle")
                 or _weibo_media_url(str(pic_id), "mw690"))
        ext = _weibo_image_type(session, thumb)
        filename = f"{mblog_id}_{index}"
        items.append(ImageItem(
            url=original,
            thumb_url=thumb,
            index=index,
            width=meta.get("width"),
            height=meta.get("height"),
            extension=ext,
            filename=filename,
        ))

        # Live Photo 的短视频有时直接挂在 pic_infos 的图片描述下。
        video_url = _weibo_video_url(meta)
        if video_url and video_url not in seen_video_urls:
            seen_video_urls.add(video_url)
            video_records.append((video_url, index))

    mixed_video_infos = _weibo_video_infos(data)
    for media_info in mixed_video_infos:
        video_url = _weibo_video_url(media_info)
        if not video_url or video_url in seen_video_urls:
            continue
        seen_video_urls.add(video_url)
        # 单图+单视频是最常见的 Live Photo 形态；多媒体帖则使用独立视频名。
        pair_index = 1 if len(pic_ids) == 1 and len(mixed_video_infos) == 1 else None
        video_records.append((video_url, pair_index))

    for video_index, (video_url, pair_index) in enumerate(video_records, start=1):
        stem = (f"{mblog_id}_{pair_index}" if pair_index
                else f"{mblog_id}_video_{video_index}")
        page_info = data.get("page_info") or {}
        items.append(ImageItem(
            url=video_url,
            thumb_url=_weibo_meta_url(page_info, "page_pic") or "",
            index=len(items) + 1,
            extension=_weibo_video_extension(video_url),
            filename=stem,
            media_type="video",
        ))

    return ImagePost(url=url, title=title[:120], author=author, items=items)


def probe_images(url: str) -> ImagePost:
    """解析帖子中的全部图片（不下载），返回图片列表（含原图与缩略图链接）。"""
    url = (url or "").strip()
    if not url:
        raise ImageError(i18n.tr("enter_post_link"))
    if "weibo.com" in url.lower() or "weibo.cn" in url.lower():
        return _probe_weibo_images(url)

    # 每次解析前重置 gallery-dl 全局配置，避免残留
    gdl_config.clear()
    gdl_config.set(("extractor", "twitter"), "videos", False)
    gdl_config.set(("extractor", "instagram"), "videos", False)
    # X 图片默认就是 orig；显式声明保证拿到最高清原图
    gdl_config.set(("extractor", "twitter"), "size", "orig")
    if _browser_cookies:
        gdl_config.set(("extractor",), "cookies", [_browser_cookies])

    try:
        j = gdl_job.DataJob(url, file=None)
        j.run()
    except Exception as exc:
        raise _probe_error(exc.__class__.__name__, str(exc), url) from exc

    if j.exception is not None:
        exc = j.exception
        raise _probe_error(exc.__class__.__name__, str(exc), url) from exc

    title = ""
    author = ""
    items: List[ImageItem] = []
    for msg in j.data:
        if not msg:
            continue
        if msg[0] == -1:
            info = msg[1] if len(msg) > 1 and isinstance(msg[1], dict) else {}
            raise _probe_error(info.get("error", ""), info.get("message", ""), url)
        if msg[0] == 2:  # Message.Directory：帖子元数据
            kw = msg[1]
            title = title or kw.get("content") or kw.get("description") or ""
            user = kw.get("user") or kw.get("author") or {}
            if isinstance(user, dict):
                author = author or user.get("nick") or user.get("name") or ""
            author = author or kw.get("username") or kw.get("fullname") or ""
        elif msg[0] == 3:  # Message.Url：一个媒体文件
            media_url, kw = msg[1], msg[2]
            ext = (kw.get("extension") or "jpg").lower()
            if ext in ("mp4", "m4v", "webm", "mov"):
                continue  # 只要图片
            idx = len(items) + 1
            stem = kw.get("filename") or f"image_{idx}"
            tweet_id = kw.get("tweet_id") or kw.get("shortcode") or kw.get("post_id")
            if tweet_id:
                stem = f"{tweet_id}_{kw.get('num') or idx}_{stem}"
            items.append(ImageItem(
                url=media_url,
                thumb_url=_twitter_thumb(media_url),
                index=idx,
                width=kw.get("width"),
                height=kw.get("height"),
                extension=ext,
                filename=str(stem),
            ))

    if not items:
        is_ig = "instagram" in url.lower()
        suffix = i18n.tr("instagram_limited_suffix") if is_ig else ""
        raise ImageError(i18n.tr("error_no_images", suffix=suffix))

    title = re.sub(r"\s+", " ", title).strip()
    return ImagePost(url=url, title=title[:120], author=author, items=items)


def fetch_thumbnail(item: ImageItem, max_bytes: int = 8 * 1024 * 1024,
                    retries: int = 2) -> bytes:
    """下载单张缩略图字节（在工作线程调用，界面层再转 PhotoImage）。"""
    last_exc = None
    for _ in range(retries + 1):
        try:
            r = requests.get(item.thumb_url, headers=_media_headers(item.thumb_url), timeout=10)
            r.raise_for_status()
            if len(r.content) > max_bytes:
                raise ImageError(i18n.tr("error_thumbnail_large"))
            return r.content
        except requests.RequestException as exc:
            last_exc = exc
    raise last_exc


def _unique_path(save_dir: str, stem: str, ext: str) -> str:
    stem = re.sub(r'[\\/:*?"<>|]+', "_", stem)[:120] or "image"
    path = os.path.join(save_dir, f"{stem}.{ext}")
    n = 1
    while os.path.exists(path):
        path = os.path.join(save_dir, f"{stem} ({n}).{ext}")
        n += 1
    return path


def download_images(
    items: List[ImageItem],
    save_dir: str = DEFAULT_SAVE_DIR,
    progress: Optional[ImageProgress] = None,
    cancel_event: Optional[threading.Event] = None,
) -> List[str]:
    """逐张下载图片，返回保存路径列表。

    cancel_event 置位后在下一个数据块边界中断，删除当前未完成文件，
    已完成的文件保留，抛出 ImageCancelled。
    """
    if not items:
        raise ImageError(i18n.tr("error_select_images"))
    os.makedirs(save_dir, exist_ok=True)
    total = len(items)
    paths: List[str] = []
    session = requests.Session()

    for i, item in enumerate(items):
        if cancel_event is not None and cancel_event.is_set():
            raise ImageCancelled()
        path = _unique_path(save_dir, item.filename, item.extension)
        part = path + ".part"

        def _cleanup_part():
            try:
                if os.path.isfile(part):
                    os.remove(part)
            except OSError:
                pass

        last_exc = None
        for attempt in range(2):  # 网络抖动时重试一次
            if cancel_event is not None and cancel_event.is_set():
                raise ImageCancelled()
            try:
                # 连接超时设短一些，保证「取消」能尽快生效
                session.headers.update(_media_headers(item.url))
                with session.get(item.url, stream=True, timeout=(5, 30)) as r:
                    r.raise_for_status()
                    content_len = int(r.headers.get("Content-Length") or 0)
                    done_bytes = 0
                    with open(part, "wb") as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            if cancel_event is not None and cancel_event.is_set():
                                raise ImageCancelled()
                            f.write(chunk)
                            done_bytes += len(chunk)
                            if progress and content_len:
                                frac = min(1.0, done_bytes / content_len)
                                percent = (i + frac) / total * 100
                                progress(percent, i18n.tr(
                                    "image_progress", index=i + 1, total=total))
                os.replace(part, path)
                last_exc = None
                break
            except ImageCancelled:
                _cleanup_part()
                raise
            except requests.RequestException as exc:
                _cleanup_part()
                last_exc = exc
        if last_exc is not None:
            raise ImageError(
                i18n.tr("error_image_download", index=i + 1,
                        error=last_exc.__class__.__name__)) from last_exc
        paths.append(path)
        if progress:
            progress((i + 1) / total * 100, i18n.tr(
                "image_done_progress", index=i + 1, total=total))

    return paths
