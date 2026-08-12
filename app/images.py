"""图片引擎：用 gallery-dl 解析 X / Instagram 帖子里的图片列表，
再用 requests 自行下载（便于实现单张/多张选择、进度与取消）。

- X 图片走 gallery-dl 的 twitter 提取器，默认返回 `name=orig` 原图链接。
- Instagram 走 instagram 提取器；匿名访问常被平台重定向到登录页，
  此时抛出带中文提示的 ImageError，界面可降级展示。
"""

import os
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import requests

from gallery_dl import config as gdl_config
from gallery_dl import job as gdl_job

DEFAULT_SAVE_DIR = os.path.expanduser("~/Downloads")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 进度回调签名: callback(percent: float, text: str)
ImageProgress = Callable[[float, str], None]

_browser_cookies = ""  # 空 = 不读取浏览器 Cookie


def set_browser_cookies(browser: str):
    """设置 gallery-dl 从哪个浏览器读取 Cookie（空字符串为禁用）。"""
    global _browser_cookies
    _browser_cookies = (browser or "").strip().lower()


class ImageError(Exception):
    """对外暴露的图片解析/下载错误，message 为中文文案。"""


class ImageCancelled(Exception):
    """用户取消了图片下载。"""


@dataclass
class ImageItem:
    """帖子中的一张图片。"""

    url: str                      # 最高清原图直链
    thumb_url: str                # 缩略图直链（供界面预览）
    index: int                    # 帖内序号（1 开始）
    width: Optional[int] = None
    height: Optional[int] = None
    extension: str = "jpg"
    filename: str = ""            # 不含扩展名的建议文件名

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
            return ImageError(
                "Instagram 要求登录才能访问该帖子（平台已封锁匿名访问）。"
                "可在「设置」中选择浏览器 Cookie 后重试")
        return ImageError(
            "该内容需要登录才能访问，可在「设置」中选择浏览器 Cookie 后重试")
    if "404" in msg or "notfound" in name.lower() or "not found" in low:
        return ImageError("帖子不存在或已被删除")
    if any(k in low for k in ("timed out", "timeout", "connection", "getaddrinfo",
                              "network", "unreachable")):
        return ImageError("网络请求失败，请检查网络连接后重试")
    if "nomatch" in name.lower() or "unsupported" in low:
        return ImageError("不支持的链接，请输入 X 或 Instagram 帖子链接")
    return ImageError(f"解析失败：{name}: {msg[:160]}")


def probe_images(url: str) -> ImagePost:
    """解析帖子中的全部图片（不下载），返回图片列表（含原图与缩略图链接）。"""
    url = (url or "").strip()
    if not url:
        raise ImageError("请输入帖子链接")

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
            if ext in ("mp4", "m4v", "webm", "mov", "gif"):
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
        raise ImageError("该帖子中没有解析到图片" +
                         ("（Instagram 匿名访问受限时也会出现此提示）" if is_ig else ""))

    title = re.sub(r"\s+", " ", title).strip()
    return ImagePost(url=url, title=title[:120], author=author, items=items)


def fetch_thumbnail(item: ImageItem, max_bytes: int = 8 * 1024 * 1024,
                    retries: int = 2) -> bytes:
    """下载单张缩略图字节（在工作线程调用，界面层再转 PhotoImage）。"""
    last_exc = None
    for _ in range(retries + 1):
        try:
            r = requests.get(item.thumb_url, headers={"User-Agent": UA}, timeout=10)
            r.raise_for_status()
            if len(r.content) > max_bytes:
                raise ImageError("缩略图过大")
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
        raise ImageError("请先勾选要下载的图片")
    os.makedirs(save_dir, exist_ok=True)
    total = len(items)
    paths: List[str] = []
    session = requests.Session()
    session.headers["User-Agent"] = UA

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
                                progress(percent, f"正在下载第 {i + 1}/{total} 张")
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
                f"第 {i + 1} 张图片下载失败：网络错误"
                f"（{last_exc.__class__.__name__}）") from last_exc
        paths.append(path)
        if progress:
            progress((i + 1) / total * 100, f"已完成 {i + 1}/{total} 张")

    return paths
