"""网络代理设置。

**为什么需要**：在这类工具最常见的受限网络环境里，没有代理几乎等于完全不可用。麻烦在于
三条网络路径读代理的方式各不相同——`requests` 会读标准的 ``HTTP(S)_PROXY`` 环境变量，而
**yt-dlp 与 gallery-dl 都不读**，必须显式传参。所以这里统一持有「当前代理」，再分别注入。

优先级：用户设置 → 环境变量（``HTTPS_PROXY`` / ``http_proxy`` / ``ALL_PROXY`` …）→ 直连。

两条约束：

1. **只接受能识别的代理 URL**（必须带 ``http://`` 或 ``https://`` 与主机名）。写错一律当作
   没设置并给出原因——绝不让一个坏字符串把整个网络打断，用户还以为「这软件坏了」。
   socks 暂不支持：`requests` 走 socks 需要额外的 PySocks 依赖，不值得为它引入新依赖，
   代理工具的 HTTP 端口就够用（提示里写清了）。
2. **不泄露密码**：代理 URL 常带 ``user:pass@``，写日志或诊断时必须打码。
"""

import urllib.parse

from . import i18n

SCHEMES = ("http", "https")
ENV_KEYS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
            "ALL_PROXY", "all_proxy")

_current = ""


def validate(text):
    """校验并归一化代理 URL，返回 ``(值, 错误信息或 None)``。空串表示直连。"""
    value = str(text or "").strip()
    if not value:
        return "", None
    if any(ch.isspace() for ch in value):
        return "", i18n.tr("proxy_invalid_format")
    parsed = urllib.parse.urlsplit(value)
    scheme = (parsed.scheme or "").lower()
    if scheme not in SCHEMES:
        return "", i18n.tr("proxy_invalid_scheme",
                           schemes=" / ".join(f"{s}://" for s in SCHEMES))
    if not parsed.hostname:
        return "", i18n.tr("proxy_invalid_host")
    try:
        port = parsed.port
    except ValueError:
        return "", i18n.tr("proxy_invalid_port")
    if port is not None and not 0 < port < 65536:
        return "", i18n.tr("proxy_invalid_port")
    return value, None


def env_proxy() -> str:
    """标准环境变量里的代理；没有则空串。"""
    import os

    for key in ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            normalized, error = validate(value)
            if not error:
                return normalized
    return ""


def set_current(url) -> str:
    """设置当前生效的代理（空串 = 直连）。"""
    global _current
    normalized, error = validate(url)
    _current = "" if error else normalized
    return _current


def set_from_settings(settings) -> str:
    """按设置初始化；设置为空**或写错**时退回环境变量。

    写错时也回退，是为了不把网络整体打断：界面上会显示校验错误，用户知道要去改，
    但在改好之前，系统里已有的环境变量代理仍然能让他正常下载。
    """
    configured = (settings or {}).get("proxy", "")
    if str(configured or "").strip():
        value, error = validate(configured)
        if not error:
            return set_current(value)
    return set_current(env_proxy())


def current() -> str:
    """当前生效的代理 URL（空串 = 直连）。"""
    return _current


def is_enabled() -> bool:
    return bool(_current)


def session_proxies() -> dict:
    """给 ``requests`` 用的代理映射；没有代理时返回空字典（等价于直连）。"""
    if not _current:
        return {}
    return {"http": _current, "https": _current}


def apply_to_session(session) -> None:
    """把代理装到 requests 会话上（没有代理时不动它，保持环境变量的默认行为）。"""
    if session is None or not _current:
        return
    try:
        session.proxies.update(session_proxies())
    except Exception:
        pass


def apply_to_gallery_dl(config) -> None:
    """把代理写进 gallery-dl 的配置（下载与解析两侧都要）。"""
    if config is None or not _current:
        return
    for section in (("downloader",), ("extractor",)):
        try:
            config.set(section, "proxy", _current)
        except Exception:
            pass


def ytdlp_options() -> dict:
    """给 yt-dlp 的参数字典（没有代理时是空字典）。"""
    return {"proxy": _current} if _current else {}


def redact(url) -> str:
    """打码版本：保留主机与用户名，密码换成 ``***``。"""
    text = str(url or "")
    if not text:
        return ""
    parsed = urllib.parse.urlsplit(text)
    if parsed.password is None:
        return text
    host = parsed.hostname or ""
    user = urllib.parse.quote(parsed.username or "")
    netloc = f"{user}:***@{host}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def describe() -> str:
    """给诊断/日志用的一行说明。"""
    if not _current:
        return i18n.tr("proxy_direct")
    return redact(_current)
