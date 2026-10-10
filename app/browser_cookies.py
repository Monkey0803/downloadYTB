"""浏览器 Cookie / cookies.txt 的共享状态与失败识别。

core.py（视频）与 images.py（图片）都从这里读取 Cookie 配置，界面只需在
设置变更时调用一次。

为什么需要单独识别「Cookie 读取失败」：
macOS 上浏览器的数据目录受 TCC 保护（例如 Chrome 的
``~/Library/Application Support/Google/Chrome`` 带 ``com.apple.macl``
扩展属性）。未获得「完全磁盘访问权限」的进程读取该目录会被拒绝，而
yt-dlp 内部用 ``os.walk()`` 递归查找 Cookie 数据库，``os.walk`` 在默认
``onerror=None`` 时会**静默吞掉权限错误并返回空**，于是抛出把搜索根目录
当成数据库位置的误导性消息::

    could not find chrome cookies database in "/.../Google/Chrome"

用户看到这条消息无从判断该怎么办。这里统一识别这类失败，让上层可以
降级为匿名解析，并给出可操作的提示。
"""

import os

# 空 = 不读取浏览器 Cookie；可选 chrome/safari/firefox/edge 等 yt-dlp 支持的值
_browser = ""
# 空 = 不读取 cookies.txt
_cookie_file = ""

# yt-dlp / gallery-dl 在 Cookie 数据库或 cookies.txt 读取失败时的消息特征。
_COOKIE_LOAD_ERROR_MARKERS = (
    "cookies database",       # could not find chrome cookies database in ...
    "cookie database",        # unable to open cookie database / could not copy ...
    "cookiefile",             # yt-dlp 的 --cookies 选项
    "cookies.txt",
)


def set_browser(browser: str) -> None:
    """设置从哪个浏览器读取 Cookie（空字符串为禁用）。"""
    global _browser
    _browser = (browser or "").strip().lower()


def set_cookie_file(path: str) -> None:
    """设置手动导出的 cookies.txt 路径（空字符串为禁用）。"""
    global _cookie_file
    _cookie_file = (path or "").strip()


def browser() -> str:
    return _browser


def cookie_file() -> str:
    return _cookie_file


def cookie_file_exists() -> bool:
    """cookies.txt 已配置且文件确实存在。"""
    return bool(_cookie_file) and os.path.isfile(_cookie_file)


def cookie_file_missing() -> bool:
    """配置了 cookies.txt 但文件已不存在（提示用户重新选择）。"""
    return bool(_cookie_file) and not os.path.isfile(_cookie_file)


def enabled() -> bool:
    return bool(_browser) or cookie_file_exists()


def ytdlp_options() -> dict:
    """返回 yt-dlp 的 Cookie 参数；cookies.txt 优先于浏览器读取。

    cookies.txt 不依赖 TCC 与钥匙串，是打包 App 下最稳妥的方式。
    """
    if cookie_file_exists():
        return {"cookiefile": _cookie_file}
    if _browser:
        return {"cookiesfrombrowser": (_browser,)}
    return {}


def gallery_dl_cookies():
    """gallery-dl ``extractor.cookies`` 的值：cookie 文件路径或浏览器列表。"""
    if cookie_file_exists():
        return _cookie_file
    if _browser:
        return [_browser]
    return None


def is_load_error(exc) -> bool:
    """判断异常（或错误消息字符串）是否为「Cookie 读取失败」。"""
    if exc is None:
        return False
    message = exc if isinstance(exc, str) else str(exc)
    message = message.lower()
    return any(marker in message for marker in _COOKIE_LOAD_ERROR_MARKERS)
