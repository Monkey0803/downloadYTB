"""图片引擎：用 gallery-dl 解析 X / Instagram 帖子里的图片列表，
再用 requests 自行下载（便于实现单张/多张选择、进度与取消）。

- X 图片走 gallery-dl 的 twitter 提取器，默认返回 `name=orig` 原图链接。
- Instagram 走 instagram 提取器；匿名访问常被平台重定向到登录页，
  此时抛出带中文提示的 ImageError，界面可降级展示。
- 微博图片通过 yt-dlp 建立访客会话后读取微博状态 API；图片、GIF，以及混合帖
  中的 MP4/MOV 共用同一套缩略图、多选、批量下载流程。
"""

import json
import os
import random
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from urllib.parse import urlparse
import requests
import yt_dlp

from . import i18n
from . import browser_cookies
from . import platforms
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


def set_browser_cookies(browser: str):
    """设置 gallery-dl / yt-dlp 从哪个浏览器读取 Cookie（空字符串为禁用）。"""
    browser_cookies.set_browser(browser)


def set_cookie_file(path: str):
    """设置手动导出的 cookies.txt（空字符串为禁用）。"""
    browser_cookies.set_cookie_file(path)


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
class DownloadResult:
    """一批图片的下载结果。

    分开记录「本次下载」与「已存在被跳过」，界面才能如实汇报，
    也让「全部已存在」这种情况不会退化成「完成 0 张」。
    """

    paths: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def saved_any(self) -> bool:
        return bool(self.paths)


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
    warning: str = ""  # 非致命的降级提示（如浏览器 Cookie 读取失败后改用匿名解析）
    # 平台侧帖子 ID（取不到时为空）。「每个帖子单独建子文件夹」用它命名目录，
    # 所以只依赖链接内容、同一帖子每次解析结果一致。
    post_id: str = ""

    @property
    def has_video(self) -> bool:
        return any(item.media_type == "video" for item in self.items)


def _twitter_thumb(url: str) -> str:
    """把 pbs.twimg.com 的 orig 原图链接降为 small 档作缩略图。"""
    if "pbs.twimg.com" in url and "name=" in url:
        return re.sub(r"name=\w+", "name=small", url)
    return url


def _probe_error(name: str, msg: str, url: str) -> ImageError:
    # Cookie 读取失败优先识别：yt-dlp 的原始消息只提到搜索目录，用户无从
    # 判断该怎么处理（见 app/browser_cookies.py 的说明）。
    if browser_cookies.is_load_error(msg):
        return ImageError(i18n.tr("error_browser_cookie_unavailable"))
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
_WEIBO_DESKTOP_STATUS_RE = re.compile(
    r"weibo\.com/(?:u/)?\d+/(?P<short>[0-9A-Za-z]+)",
    re.IGNORECASE,
)
# status/detail 后面既可能是数字 mid、也可能是 base62 短 ID（移动端两种都在用）
_WEIBO_ANY_STATUS_RE = re.compile(
    r"weibo\.(?:cn|com)/(?:status|detail)/(?P<id>[0-9A-Za-z]+)",
    re.IGNORECASE,
)
_WEIBO_BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_WEIBO_REFERER = "https://weibo.com/"


def _weibo_base62_to_mid(value: str) -> Optional[str]:
    """将网页版微博短 ID 转换为状态接口使用的数字 MID。"""
    if not value or not re.fullmatch(r"[0-9A-Za-z]+", value):
        return None

    decoded_parts = []
    for end in range(len(value), 0, -4):
        part = value[max(0, end - 4):end]
        number = 0
        for char in part:
            number = number * 62 + _WEIBO_BASE62.index(char)
        decoded_parts.append(str(number))

    decoded_parts.reverse()
    return decoded_parts[0] + "".join(
        part.zfill(7) for part in decoded_parts[1:])


def _is_weibo_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return (host == "weibo.com" or host.endswith(".weibo.com")
            or host == "weibo.cn" or host.endswith(".weibo.cn"))


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

    match = _WEIBO_DESKTOP_STATUS_RE.search(url or "")
    if match:
        return _weibo_base62_to_mid(match.group("short"))
    return None


def _weibo_ytdlp_options(with_cookies: bool = True,
                         platform: Optional[str] = "weibo") -> dict:
    # 图片微博经常会被 yt-dlp 识别为“没有视频格式”；保留解析结果中的
    # 状态 ID，后续仍可从微博状态 API 读取图片/GIF。
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignore_no_formats_error": True,
    }
    if with_cookies:
        options.update(browser_cookies.ytdlp_options(platform))
    return options


def _weibo_session(ydl: Optional[yt_dlp.YoutubeDL]) -> requests.Session:
    """把 yt-dlp 生成的微博访客/浏览器 Cookie 转给 requests。

    ydl 为 None（连 yt-dlp 实例都没能建起来）时返回一个干净的会话，
    微博访客 Cookie 会在 _weibo_status_data 里按需补齐。
    """
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": _WEIBO_REFERER})
    if ydl is None:
        return session
    for cookie in ydl.cookiejar:
        session.cookies.set(cookie.name, cookie.value,
                            domain=cookie.domain, path=cookie.path)
    return session


def _weibo_load_jsonp(text: str) -> dict:
    """解析微博访客接口返回的 JSONP。"""
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start < 0 or end < start:
        raise ValueError(i18n.tr("weibo_invalid_response"))
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError(i18n.tr("weibo_invalid_response"))
    return data


def _weibo_update_visitor_session(session: requests.Session):
    """建立微博访客 Cookie，供状态接口在未登录时使用。"""
    user_agent = session.headers.get("User-Agent", _WEIBO_VIDEO_UA)
    match = re.search(r"Chrome/(\d+)", user_agent)
    chrome_version = match.group(1) if match else "90"
    fingerprint = json.dumps({
        "os": "1",
        "browser": f"Chrome{chrome_version},0,0,0",
        "fonts": "undefined",
        "screenInfo": "1920*1080*24",
        "plugins": "",
    }, separators=(",", ":"))

    response = session.post(
        "https://passport.weibo.com/visitor/genvisitor",
        data={"cb": "gen_callback", "fp": fingerprint},
        headers={"Referer": _WEIBO_REFERER}, timeout=(8, 20))
    response.raise_for_status()
    visitor_data = _weibo_load_jsonp(response.text).get("data")
    if not isinstance(visitor_data, dict) or not visitor_data.get("tid"):
        raise ValueError(i18n.tr("weibo_cookie_failed"))

    response = session.get(
        "https://passport.weibo.com/visitor/visitor",
        params={
            "a": "incarnate",
            "t": visitor_data["tid"],
            "w": 3 if visitor_data.get("new_tid") else 2,
            "c": f"{visitor_data.get('confidence', 100):03d}",
            "gc": "",
            "cb": "cross_domain",
            "from": "weibo",
            "_rand": random.random(),
        },
        headers={"Referer": _WEIBO_REFERER}, timeout=(8, 20))
    response.raise_for_status()


def _weibo_status_data(session: requests.Session, status_id: str) -> dict:
    """读取微博状态；若未携带有效访客 Cookie 则自动补齐后重试。"""
    response = session.get(
        "https://weibo.com/ajax/statuses/show",
        params={"id": status_id}, timeout=(8, 20))
    response_url = getattr(response, "url", "") or ""
    if "passport.weibo.com" in response_url:
        close = getattr(response, "close", None)
        if callable(close):
            close()
        _weibo_update_visitor_session(session)
        response = session.get(
            "https://weibo.com/ajax/statuses/show",
            params={"id": status_id}, timeout=(8, 20))
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(i18n.tr("weibo_invalid_response"))
    return data


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


def _weibo_has_media(data: dict) -> bool:
    """帖子自身是否携带图片/GIF 或视频。"""
    if not isinstance(data, dict):
        return False
    if data.get("pic_ids"):
        return True
    pic_infos = data.get("pic_infos")
    if isinstance(pic_infos, dict) and pic_infos:
        return True
    return bool(_weibo_video_infos(data))


def _weibo_media_source(data: dict) -> Optional[dict]:
    """返回真正承载媒体的帖子数据。

    微博的「转发」帖自身只有一句转发语（text_raw 常为「转发微博」），
    图片/视频挂在 retweeted_status 上。转发链不会正常嵌套多层，这里仍加
    深度上限，避免异常数据导致死循环。
    """
    current = data
    for _ in range(4):
        if not isinstance(current, dict):
            return None
        if _weibo_has_media(current):
            return current
        nested = current.get("retweeted_status")
        if not isinstance(nested, dict):
            return None
        current = nested
    return None


def _weibo_image_type(session: requests.Session, thumb_url: str) -> str:
    """用小缩略图的 magic bytes 判断微博图片是否为 GIF。"""
    if thumb_url.split("?", 1)[0].lower().endswith(".gif"):
        return "gif"
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
    """解析微博状态中的图片和 GIF。

    微博图片/GIF 实际走状态接口，浏览器 Cookie 只是可选增强。yt-dlp 的
    YoutubeDL 在构造时就会加载 Cookie 数据库，因此构造本身也必须纳入
    容错：Cookie 读取失败（打包 App 未获「完全磁盘访问权限」时的常见
    情况）时降级为匿名重试，而不是让整个解析失败。
    """
    ydl = None
    info = None
    extract_error = None
    cookie_fallback = ""
    attempts = (True, False) if browser_cookies.enabled("weibo") else (False,)
    for index, with_cookies in enumerate(attempts):
        try:
            ydl = yt_dlp.YoutubeDL(_weibo_ytdlp_options(with_cookies))
            info = ydl.extract_info(url, download=False)
        except Exception as exc:
            extract_error = exc
            # 还有匿名重试机会，且这次失败确实源于 Cookie 读取。
            if with_cookies and browser_cookies.is_load_error(exc) \
                    and index + 1 < len(attempts):
                cookie_fallback = i18n.tr("cookie_fallback_warning")
                continue
            # 图片微博可能没有视频格式，yt-dlp 解析失败并不代表状态 API
            # 不可访问；直链含数字 ID 时仍可继续使用已建立的访客会话。
            break
        else:
            extract_error = None
            break

    status_id = _weibo_status_id(url, info)
    if not status_id:
        if extract_error:
            raise _probe_error(extract_error.__class__.__name__, str(extract_error), url)
        raise ImageError(i18n.tr("error_image_url"))

    session = _weibo_session(ydl)
    try:
        data = _weibo_status_data(session, status_id)
    except (requests.RequestException, ValueError) as exc:
        raise _probe_error(exc.__class__.__name__, str(exc), url) from exc

    if not isinstance(data, dict) or data.get("ok") is False:
        raise ImageError(i18n.tr("error_images_login"))

    # 转发帖自身没有媒体：图片/视频以及正文归属都在原帖（retweeted_status）。
    source = _weibo_media_source(data)
    if source is None:
        if isinstance(data.get("retweeted_status"), dict):
            # 转发了原微博，但原帖已删除/无媒体可下载
            raise ImageError(i18n.tr("error_repost_source_missing"))
        raise ImageError(i18n.tr("error_no_images", suffix=""))

    pic_ids = source.get("pic_ids") or []
    pic_infos = source.get("pic_infos") or {}

    title = re.sub(r"\s+", " ", source.get("text_raw") or "").strip()
    user = source.get("user") or {}
    author = user.get("screen_name") or user.get("nick") or "" \
        if isinstance(user, dict) else ""
    # 文件名用用户实际粘贴的那条微博 ID，便于和链接对应。
    mblog_id = str(data.get("mblogid") or status_id)
    items = []
    video_records = []
    seen_video_urls = set()
    thumb_by_index = {}
    for index, pic_id in enumerate(pic_ids, start=1):
        meta = pic_infos.get(pic_id) if isinstance(pic_infos, dict) else {}
        meta = meta if isinstance(meta, dict) else {}
        original = (_weibo_meta_url(meta, "original")
                    or _weibo_meta_url(meta, "largest")
                    or _weibo_media_url(str(pic_id), "large"))
        thumb = (_weibo_meta_url(meta, "thumbnail")
                 or _weibo_meta_url(meta, "bmiddle")
                 or _weibo_media_url(str(pic_id), "mw690"))
        declared_type = str(meta.get("type") or "").lower()
        ext = "gif" if declared_type == "gif" else _weibo_image_type(session, thumb)
        filename = f"{mblog_id}_{index}"
        thumb_by_index[index] = thumb
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
            video_records.append((video_url, index, thumb))

    mixed_video_infos = _weibo_video_infos(source)
    page_info = source.get("page_info") or {}
    media_info = page_info.get("media_info") if isinstance(page_info, dict) else {}
    big_pic_info = media_info.get("big_pic_info") if isinstance(media_info, dict) else {}
    page_thumb = (
        _weibo_meta_url(page_info, "page_pic")
        or _weibo_meta_url(big_pic_info, "pic_middle")
        or _weibo_meta_url(big_pic_info, "pic_big")
        or _weibo_meta_url(big_pic_info, "pic_small")
        or (info.get("thumbnail") if isinstance(info, dict) else "")
        or ""
    )
    for media_info in mixed_video_infos:
        video_url = _weibo_video_url(media_info)
        if not video_url or video_url in seen_video_urls:
            continue
        seen_video_urls.add(video_url)
        # 单图+单视频是最常见的 Live Photo 形态；多媒体帖则使用独立视频名。
        pair_index = 1 if len(pic_ids) == 1 and len(mixed_video_infos) == 1 else None
        video_thumb = thumb_by_index.get(pair_index, "") if pair_index else page_thumb
        video_records.append((video_url, pair_index, video_thumb))

    for video_index, (video_url, pair_index, video_thumb) in enumerate(
            video_records, start=1):
        stem = (f"{mblog_id}_{pair_index}" if pair_index
                else f"{mblog_id}_video_{video_index}")
        items.append(ImageItem(
            url=video_url,
            thumb_url=video_thumb or page_thumb,
            index=len(items) + 1,
            extension=_weibo_video_extension(video_url),
            filename=stem,
            media_type="video",
        ))

    if not items:
        raise ImageError(i18n.tr("error_no_images", suffix=""))

    return ImagePost(url=url, title=title[:120], author=author, items=items,
                     warning=cookie_fallback, post_id=mblog_id)


# 帖子 ID 的识别规则：只依赖链接本身（结果稳定，图片流程与视频流程能算出同一个目录）
_POST_ID_PATTERNS = (
    re.compile(r"(?:twitter|x)\.com/[^/]+/status/(?P<id>\d+)"),
    re.compile(r"instagram\.com/(?:p|reel|reels|tv)/(?P<id>[A-Za-z0-9_-]+)"),
)


def post_id_from_url(url: str) -> str:
    """从帖子链接取出平台侧 ID；取不到（例如 YouTube 单条视频）返回空串。

    微博优先用 PC 链接里那段 base62（如 ``Rmr4XxLMD``）：它既和图片文件名里的
    ``mblogid`` 一致、也便于人认。移动端链接只有数字 mid，就退而用数字——
    两种链接指向同一帖子时会得到不同目录，取舍见 AGENTS。
    """
    text = (url or "").strip()
    if not text:
        return ""
    if _is_weibo_url(text):
        desktop = _WEIBO_DESKTOP_STATUS_RE.search(text)
        if desktop:
            return desktop.group("short")
        any_status = _WEIBO_ANY_STATUS_RE.search(text)
        if any_status:
            return any_status.group("id")
        return _weibo_status_id(text, None) or ""
    for pattern in _POST_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group("id")
    return ""


def probe_images(url: str) -> ImagePost:
    """解析帖子中的全部图片（不下载），返回图片列表（含原图与缩略图链接）。"""
    url = (url or "").strip()
    if not url:
        raise ImageError(i18n.tr("enter_post_link"))
    if _is_weibo_url(url):
        return _probe_weibo_images(url)

    # 每次解析前重置 gallery-dl 全局配置，避免残留
    gdl_config.clear()
    gdl_config.set(("extractor", "twitter"), "videos", False)
    gdl_config.set(("extractor", "instagram"), "videos", False)
    # X 图片默认就是 orig；显式声明保证拿到最高清原图
    gdl_config.set(("extractor", "twitter"), "size", "orig")
    gdl_cookies = browser_cookies.gallery_dl_cookies(platforms.detect(url))
    if gdl_cookies is not None:
        # gallery-dl 在 Cookie 读取失败时只记录警告并继续，不会中断解析。
        gdl_config.set(("extractor",), "cookies", gdl_cookies)

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
    return ImagePost(url=url, title=title[:120], author=author, items=items,
                     post_id=post_id_from_url(url))


def fetch_image_bytes(url: str, max_bytes: int = 8 * 1024 * 1024,
                      retries: int = 2) -> bytes:
    """下载任意图片字节（缩略图 / 视频封面），在工作线程调用。

    界面层再转成 PhotoImage；带重试以容忍网络抖动。
    """
    last_exc = None
    for _ in range(retries + 1):
        try:
            r = requests.get(url, headers=_media_headers(url), timeout=10)
            r.raise_for_status()
            if len(r.content) > max_bytes:
                raise ImageError(i18n.tr("error_thumbnail_large"))
            return r.content
        except requests.RequestException as exc:
            last_exc = exc
    raise last_exc


def fetch_thumbnail(item: ImageItem, max_bytes: int = 8 * 1024 * 1024,
                    retries: int = 2) -> bytes:
    """下载单张缩略图字节（在工作线程调用，界面层再转 PhotoImage）。"""
    return fetch_image_bytes(item.thumb_url, max_bytes=max_bytes, retries=retries)


def _primary_path(save_dir: str, stem: str, ext: str) -> str:
    """该图片「本来应该」保存到的路径（不做重名规避）。

    用于判断是否已经下载过：微博/图集的文件名由帖子 ID + 序号决定，
    同一帖子重复解析会得到相同的文件名，因此可以据此识别重复。
    """
    stem = re.sub(r'[\\/:*?"<>|]+', "_", stem)[:120] or "image"
    return os.path.join(save_dir, f"{stem}.{ext}")


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
    skip_existing: bool = True,
) -> DownloadResult:
    """逐张下载图片，返回本次下载与跳过的路径。

    skip_existing 为真时，目标文件已存在就跳过（与视频走 yt-dlp 默认
    「已下载则跳过」的行为一致），避免同一帖子反复下载出一堆 " (1)" 副本；
    为假时沿用旧的防覆盖策略，重名自动加序号。

    cancel_event 置位后在下一个数据块边界中断，删除当前未完成文件，
    已完成的文件保留，抛出 ImageCancelled。
    """
    if not items:
        raise ImageError(i18n.tr("error_select_images"))
    os.makedirs(save_dir, exist_ok=True)
    total = len(items)
    result = DownloadResult()
    session = requests.Session()

    for i, item in enumerate(items):
        if cancel_event is not None and cancel_event.is_set():
            raise ImageCancelled()

        primary = _primary_path(save_dir, item.filename, item.extension)
        if skip_existing and os.path.isfile(primary):
            result.skipped.append(primary)
            if progress:
                progress((i + 1) / total * 100, i18n.tr(
                    "image_skipped_progress", index=i + 1, total=total))
            continue
        path = primary if not os.path.exists(primary) else _unique_path(
            save_dir, item.filename, item.extension)
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
        result.paths.append(path)
        if progress:
            progress((i + 1) / total * 100, i18n.tr(
                "image_done_progress", index=i + 1, total=total))

    return result
