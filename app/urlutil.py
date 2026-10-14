"""链接与平台标签工具：从文本挑链接、判断平台、生成目录名与界面标签。

从 gui.py 拆出来（ROADMAP 第 13 项）。这些函数不碰 Tk，是纯字符串逻辑，界面只负责
调用。测试里大量按 ``gui.parse_links`` 这样引用——gui.py 仍然 import 这些名字，
所以调用点与既有测试都不用改。
"""

import re

from . import i18n, platforms


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
    r"|(?:www\.)?weibo\.com/(?:\d+/[A-Za-z0-9]+|0/\d+|tv/show/[\w:]+)\S*"
    r"|video\.weibo\.com/show\?\S*"
    r"|m\.weibo\.cn/(?:status|detail)/\d+\S*"
    r")",
    re.IGNORECASE,
)


def extract_supported_url(text: str):
    """从文本中提取首个受支持的链接，没有则返回 None。"""
    if not text:
        return None
    m = URL_RE.search(text.strip())
    return m.group(0) if m else None


# 按平台 Cookie 覆盖的界面行顺序，与 platforms.PLATFORMS 一一对应
PLATFORM_LABEL_KEYS = ("youtube_nav", "x_nav", "instagram_nav",
                       "douyin_nav", "bilibili_nav", "weibo_nav")


def detect_platform(url: str):
    """根据链接判断平台页 key（映射表在 app/platforms.py，引擎也会用到）。"""
    return platforms.detect(url)


def is_weibo_video_url(url: str):
    """判断微博专用视频地址；普通微博状态统一按媒体帖子解析。"""
    low = (url or "").lower()
    return "/tv/show/" in low or "video.weibo.com/show" in low


def _sanitize_filename(text: str) -> str:
    """把标题里的路径分隔符、控制字符等清掉，避免生成非法目录名。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


# 只认 URL 安全字符：这样贴在中英文混排文本里的链接能挑出来，而周围的中文不会被
# 当成链接的一部分（实测聊天记录里最常见的就是「看这个https://…很好」）
_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")


def normalize_kind(value) -> str:
    """下载类型归一化：只有 ``image`` 是图片，其余（含缺省）都是视频。

    队列条目里没写 kind 的是老数据（图片入队之前存的），一律当视频，不能丢。
    """
    return "image" if str(value or "").strip().lower() == "image" else "video"


def queue_key(item) -> tuple:
    """队列去重键：同一个链接可以既排视频又排图片，所以键里必须带类型。"""
    item = item or {}
    return (str(item.get("url") or ""), normalize_kind(item.get("kind")))


def parse_links(text) -> list:
    """从任意文本里挑出链接，保持出现顺序（**不去重**）。

    用于「批量粘贴」：用户从聊天记录或文档里复制一大段，里面可能混着说明文字、
    重复链接和中文标点。去重与计数交给 ``enqueue_urls``——放在这里的话，「跳过 N 个
    重复」就永远报 0，因为重复项在解析阶段就被吃掉了（实测踩到）。
    """
    found = []
    for raw in _URL_RE.findall(str(text or "")):
        url = raw.rstrip(".,;:!?)]}'\"、，。；：！？）】》」’“")
        if url:
            found.append(url)
    return found


def post_folder_name(post_id: str, title: str = "") -> str:
    """「每个帖子单独建子文件夹」用的目录名：``<帖子 ID>_<标题摘要>``。

    只用帖子 ID 不好认，所以附上标题摘要；取不到标题时退回纯 ID，取不到 ID 时返回
    空串（表示不分组）。标题若被作者修改，会落到新目录——取舍见 AGENTS。
    """
    pid = _sanitize_filename(post_id)
    if not pid:
        return ""
    summary = _sanitize_filename(title)[:40].strip(" ._-")
    return f"{pid}_{summary}" if summary else pid


def platform_label(platform: str) -> str:
    """把 ASCII 平台键翻成界面上的平台名；未知值原样返回。

    面板的身份标识统一用 ``platforms.PLATFORMS`` 里的 ASCII 键（与引擎、Cookie
    覆盖、保存目录键一致），显示时才翻成当前语言的标签。
    """
    key = str(platform or "").strip().lower()
    labels = dict(zip(platforms.PLATFORMS, PLATFORM_LABEL_KEYS))
    label_key = labels.get(key)
    return i18n.tr(label_key) if label_key else str(platform or "")


def download_dir_key(platform: str, kind: str) -> str:
    """返回一个下载方式的保存目录键，例如 ``youtube_audio``。

    ``platform`` 是 ASCII 平台键。历史版本传过显示名（"YouTube"/"微博"），
    所以这里额外做一次小写归一：ASCII 名字（含旧调用点）结果不变，而中文名会
    落到 unknown——调用点已全部改成 ASCII 键，不存在这种输入。
    """
    platform_key = str(platform or "unknown").strip().lower()
    if not platform_key.isascii():
        platform_key = "unknown"
    return f"{platform_key}_{kind}"
