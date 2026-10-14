"""链接 → 平台标识的共享映射。

放在独立模块是为了让下载引擎（core / images）也能按平台取 Cookie 设置，
而不必从 GUI 反向依赖。GUI 里的 ``detect_platform`` 只是这里的别名。
"""

from typing import Optional

# 与侧边栏页面 key、保存目录 key 一致
PLATFORMS = ("youtube", "x", "instagram", "douyin", "bilibili", "weibo")


# 常见、但本应用没有面板的站点。**只用于给出准确说明**：用户粘进来时告诉他
# 「这个平台不支持」而不是沉默或谎称「没有链接」。
#
# 每条是 (域名后缀, 路径前缀, 平台名 i18n 键, 下载引擎是否支持)：
# 引擎支持与否会影响提示措辞——小红书/TikTok 的提取器 yt-dlp 自带（实测过
# extractor 列表），只是界面还没做面板；视频号/公众号/快手则引擎也没有。
UNSUPPORTED_SITES = (
    ("channels.weixin.qq.com", "", "platform_wechat_channels", False),
    ("weixin.qq.com", "/sph", "platform_wechat_channels", False),
    ("mp.weixin.qq.com", "", "platform_wechat_article", False),
    ("xiaohongshu.com", "", "platform_xiaohongshu", True),
    ("xhslink.com", "", "platform_xiaohongshu", True),
    ("kuaishou.com", "", "platform_kuaishou", False),
    ("tiktok.com", "", "platform_tiktok", True),
)


def unsupported_site(url: str):
    """已知但没有面板的站点 → ``(平台名 i18n 键, 引擎是否支持)``；否则 None。

    域名按**边界**匹配：``notweixin.qq.com`` 不算微信（子串匹配会误判）。
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    path = (parts.path or "").lower()
    if not host:
        return None
    for suffix, prefix, key, engine_ok in UNSUPPORTED_SITES:
        if host == suffix or host.endswith("." + suffix):
            if prefix and not path.startswith(prefix):
                continue
            return key, engine_ok
    return None


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
