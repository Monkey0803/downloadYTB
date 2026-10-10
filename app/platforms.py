"""链接 → 平台标识的共享映射。

放在独立模块是为了让下载引擎（core / images）也能按平台取 Cookie 设置，
而不必从 GUI 反向依赖。GUI 里的 ``detect_platform`` 只是这里的别名。
"""

from typing import Optional

# 与侧边栏页面 key、保存目录 key 一致
PLATFORMS = ("youtube", "x", "instagram", "douyin", "bilibili", "weibo")


def detect(url: str) -> Optional[str]:
    """根据链接判断平台标识；无法识别时返回 None。"""
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
    if "weibo.com" in low or "weibo.cn" in low:
        return "weibo"
    return None
