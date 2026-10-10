"""浏览器 Cookie / cookies.txt 的共享状态、可用性体检与失败识别。

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

``probe()`` 则把这件事提前：在设置页真实加载一次 Cookie，直接告诉用户
当前配置到底能不能用，而不是等解析失败后才发现。
"""

import os
from dataclasses import dataclass
from typing import Iterator, Optional

# 空 = 不读取浏览器 Cookie；可选 chrome/safari/firefox/edge 等 yt-dlp 支持的值
_browser = ""
# 空 = 不读取 cookies.txt
_cookie_file = ""
# 按平台覆盖：{"instagram": "chrome"}；值 "none" 表示该平台不使用 Cookie。
# 未出现的平台沿用上面的全局设置。
_platform_overrides = {}

# 平台覆盖里表示「该平台不使用 Cookie」的哨兵值
PLATFORM_NONE = "none"

# yt-dlp / gallery-dl 在 Cookie 数据库或 cookies.txt 读取失败时的消息特征。
_COOKIE_LOAD_ERROR_MARKERS = (
    "cookies database",        # could not find chrome cookies database in ...
    "cookie database",         # unable to open cookie database / could not copy ...
    "cookiefile",              # yt-dlp 的 --cookies 选项
    "cookies.txt",
    "failed to load cookies",  # yt-dlp CookieLoadError 的通用包装消息
)

# macOS 13+ 的「完全磁盘访问权限」设置页深链。已在本机 macOS 27 实测：
# 打开后系统设置会直接定位到「完全磁盘访问权限」，窗口标题即为该名称。
FDA_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
    "?Privacy_AllFiles"
)

PROBE_OFF = "off"                # 未配置任何 Cookie 来源
PROBE_OK = "ok"                  # 可用
PROBE_UNREADABLE = "unreadable"  # 配置了但读不到（权限/文件/格式问题）


@dataclass(frozen=True)
class ProbeResult:
    """一次 Cookie 可用性体检的结果。"""

    status: str
    cookie_count: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == PROBE_OK

    @property
    def unreadable(self) -> bool:
        return self.status == PROBE_UNREADABLE


def set_browser(browser: str) -> None:
    """设置从哪个浏览器读取 Cookie（空字符串为禁用）。"""
    global _browser
    _browser = (browser or "").strip().lower()


def set_cookie_file(path: str) -> None:
    """设置手动导出的 cookies.txt 路径（空字符串为禁用）。"""
    global _cookie_file
    _cookie_file = (path or "").strip()


def set_platform_overrides(mapping) -> None:
    """设置按平台的 Cookie 覆盖。

    值是浏览器名，或 ``PLATFORM_NONE`` 表示该平台不使用 Cookie；未列出的
    平台沿用全局设置。配置文件可能被手动编辑，因此这里做类型清洗。
    """
    global _platform_overrides
    cleaned = {}
    if isinstance(mapping, dict):
        for platform, value in mapping.items():
            if not isinstance(platform, str) or not isinstance(value, str):
                continue
            platform = platform.strip().lower()
            value = value.strip().lower()
            if not platform or not value:
                continue
            cleaned[platform] = value
    _platform_overrides = cleaned


def platform_overrides() -> dict:
    return dict(_platform_overrides)


def platform_override(platform: Optional[str]) -> Optional[str]:
    """返回该平台的覆盖值；没有覆盖时返回 None（表示跟随全局）。"""
    if not platform:
        return None
    return _platform_overrides.get(platform.strip().lower())


def browser(platform: Optional[str] = None) -> str:
    """该平台实际生效的浏览器名（覆盖优先于全局）。"""
    override = platform_override(platform)
    if override is not None:
        return "" if override == PLATFORM_NONE else override
    return _browser


def cookie_file() -> str:
    return _cookie_file


def cookie_file_exists() -> bool:
    """cookies.txt 已配置且文件确实存在。"""
    return bool(_cookie_file) and os.path.isfile(_cookie_file)


def cookie_file_missing() -> bool:
    """配置了 cookies.txt 但文件已不存在（提示用户重新选择）。"""
    return bool(_cookie_file) and not os.path.isfile(_cookie_file)


def enabled(platform: Optional[str] = None) -> bool:
    override = platform_override(platform)
    if override is not None:
        return override != PLATFORM_NONE
    return bool(_browser) or cookie_file_exists()


def ytdlp_options(platform: Optional[str] = None) -> dict:
    """返回 yt-dlp 的 Cookie 参数。

    优先级：该平台的覆盖 > 全局 cookies.txt > 全局浏览器。
    平台覆盖是更具体的意图，所以即使存在全局 cookies.txt 也以它为准；
    cookies.txt 优先于浏览器则是因为它不依赖 TCC 与钥匙串。
    """
    override = platform_override(platform)
    if override == PLATFORM_NONE:
        return {}
    if override:
        return {"cookiesfrombrowser": (override,)}

    if cookie_file_exists():
        return {"cookiefile": _cookie_file}
    if _browser:
        return {"cookiesfrombrowser": (_browser,)}
    return {}


def gallery_dl_cookies(platform: Optional[str] = None):
    """gallery-dl ``extractor.cookies`` 的值：cookie 文件路径或浏览器列表。"""
    override = platform_override(platform)
    if override == PLATFORM_NONE:
        return None
    if override:
        return [override]

    if cookie_file_exists():
        return _cookie_file
    if _browser:
        return [_browser]
    return None


def _iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """沿 ``__cause__`` / ``__context__`` 遍历异常链（带环保护）。"""
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _has_marker(message: str) -> bool:
    low = (message or "").lower()
    return any(marker in low for marker in _COOKIE_LOAD_ERROR_MARKERS)


def is_load_error(exc) -> bool:
    """判断异常（或错误消息字符串）是否为「Cookie 读取失败」。

    需要注意 yt-dlp 的包装层：``YoutubeDL.cookiejar`` 是 cached_property，
    ``load_cookies()`` 会把底层异常包成 ``CookieLoadError('failed to load
    cookies')``，再由 ``report_error()`` 以**原始消息**抛成 ``DownloadError``。
    不同版本抛出的层级不一样，所以既要匹配消息特征，也要顺着异常链回溯，
    避免包装层把特征信息挡掉。
    """
    if exc is None:
        return False
    if isinstance(exc, str):
        return _has_marker(exc)
    return any(_has_marker(str(item)) for item in _iter_exception_chain(exc))


def _load_error_detail(exc: BaseException) -> str:
    """从异常链里挑出最有信息量的一条消息。"""
    for item in _iter_exception_chain(exc):
        text = str(item).strip()
        if text and _has_marker(text):
            return text[:300]
    return str(exc).strip()[:300]


class _SilentLogger:
    """yt-dlp 的 logger 参数槽位。

    ``quiet`` 只影响下载进度输出，``to_stderr()`` 实际上只认 ``params['logger']``，
    没有 logger 时无论 quiet 与否都会把 "ERROR: ..." 写到 stderr。体检是后台
    主动探测，不该往终端喷错误，所以这里接住所有日志。
    """

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def probe(platform: Optional[str] = None) -> ProbeResult:
    """真实加载一次 Cookie，判断当前配置是否可用。

    刻意复用与解析完全相同的加载路径（``YoutubeDL.cookiejar``），因此结论就是
    「真去解析会不会失败」，而不会因为另写一套路径判断而与实际行为脱节。

    代价是可能触发一次钥匙串访问，也可能耗时到秒级，所以只应在设置页按需调用
    （放进后台线程），不要挂在应用启动路径上。
    """
    options = ytdlp_options(platform)
    if not options:
        return ProbeResult(PROBE_OFF)

    try:
        import yt_dlp
    except Exception as exc:  # pragma: no cover - 依赖缺失时的兜底
        return ProbeResult(PROBE_UNREADABLE, detail=str(exc)[:300])

    try:
        ydl = yt_dlp.YoutubeDL({
            **options,
            "quiet": True,
            "no_warnings": True,
            "logger": _SilentLogger(),
        })
        cookies = list(ydl.cookiejar)  # 触发真实加载
    except Exception as exc:
        return ProbeResult(PROBE_UNREADABLE, detail=_load_error_detail(exc))
    return ProbeResult(PROBE_OK, cookie_count=len(cookies))
