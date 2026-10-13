"""本 App 自身的版本显示与新版本检查。

第 7 项的由来：界面任何地方都不显示版本，用户既不知道自己用的是哪版，也不知道有没有
新版——而这类工具恰恰需要跟着站点变化升级（打包的 yt-dlp 会过期，站点改版会整批失败）。

三条约束：

1. **绝不因为检查更新而打断使用**：`check()` 从不抛异常，任何失败都折算成 ``error`` 状态，
   界面照常可用。
2. **不静默骚扰**：自动检查最多每 24 小时一次，并且可以在设置里关掉（默认开）。
3. **只访问公开接口**：GitHub Releases API，不带任何用户数据（无 cookie、无标识），
   并且和下载一样走用户配置的代理。
"""

import re
import time

from . import i18n

RELEASES_API = "https://api.github.com/repos/Monkey0803/downloadYTB/releases/latest"
RELEASES_PAGE = "https://github.com/Monkey0803/downloadYTB/releases/latest"
CHECK_INTERVAL = 24 * 3600
USER_AGENT = "VideoDownloader-UpdateCheck"


class AppUpdateError(Exception):
    """检查新版本失败（网络、限流、返回内容异常等）。"""


def current_version() -> str:
    from . import __version__

    return __version__


def parse_version(text):
    """把 ``v1.10.0`` / ``1.10`` / ``1.10.0-beta.1`` 解析成可比较的数字元组。

    只看数字段：本仓库只发正式版，预发布后缀不参与比较。
    """
    value = str(text or "").strip().lstrip("vV")
    numbers = []
    for part in re.split(r"[.\-_+ ]+", value):
        match = re.match(r"^(\d+)", part)
        if not match:
            break          # 遇到非数字段就停（1.10.0-beta.1 只取 1.10.0）
        numbers.append(int(match.group(1)))
    return tuple(numbers)


def is_newer(candidate, base) -> bool:
    """``candidate`` 是否比 ``base`` 新。

    先把两边的位数补齐再比，否则 ``1.11`` 与 ``1.10.0`` 这类长度不同的写法会误判。
    """
    left = parse_version(candidate)
    right = parse_version(base)
    if not left or not right:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def latest_release(session=None) -> dict:
    """查询 GitHub 上最新一个 Release，返回 ``{"version", "url", "notes"}``。"""
    import requests

    from . import netproxy

    own_session = session is None
    if own_session:
        session = requests.Session()
        netproxy.apply_to_session(session)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    try:
        response = session.get(RELEASES_API, headers=headers, timeout=(8, 20))
    except requests.RequestException as exc:
        raise AppUpdateError(i18n.tr("update_error_network", message=exc)) from exc
    status = getattr(response, "status_code", 0)
    if status == 404:
        raise AppUpdateError(i18n.tr("update_error_no_release"))
    if status in (403, 429):
        raise AppUpdateError(i18n.tr("update_error_rate_limit"))
    if status != 200:
        raise AppUpdateError(i18n.tr("update_error_http", code=status))
    try:
        data = response.json()
    except ValueError as exc:
        raise AppUpdateError(i18n.tr("update_error_parse")) from exc
    tag = str((data or {}).get("tag_name") or "").strip()
    if not tag:
        raise AppUpdateError(i18n.tr("update_error_parse"))
    return {
        "version": tag.lstrip("vV"),
        "url": str((data or {}).get("html_url") or RELEASES_PAGE),
        "notes": str((data or {}).get("body") or "")[:400],
    }


def check(current=None, session=None) -> dict:
    """检查更新，**从不抛异常**。

    返回 ``{"status", "version", "url", "error"}``，status 为
    ``update`` / ``current`` / ``error``。
    """
    base = str(current or current_version())
    try:
        release = latest_release(session=session)
    except Exception as exc:                      # 包括预期之外的异常
        return {"status": "error", "version": "", "url": "", "error": str(exc)}
    if is_newer(release["version"], base):
        return {"status": "update", "version": release["version"],
                "url": release["url"], "error": ""}
    return {"status": "current", "version": base, "url": release["url"], "error": ""}


def should_auto_check(settings, now=None) -> bool:
    """是否该做自动检查：开关打开且距上次检查超过 24 小时。"""
    if not (settings or {}).get("check_updates", True):
        return False
    try:
        last = float((settings or {}).get("last_update_check") or 0)
    except (TypeError, ValueError):
        last = 0
    return (now if now is not None else time.time()) - last >= CHECK_INTERVAL


def mark_checked(settings, now=None):
    """记录本次检查时间（调用方负责保存）。"""
    if settings is not None:
        settings["last_update_check"] = now if now is not None else time.time()
    return settings


def format_checked_at(timestamp) -> str:
    """把检查时间格式化成 ``2026-10-13 18:30``。

    0 与脏数据都返回空串：0 在本模块里表示「从未检查」，直接格式化会显示
    「1970-01-01」——界面第一次跑起来就是这么显示的（用例抓到的）。
    """
    try:
        value = float(timestamp)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(value))
    except (OSError, OverflowError, ValueError):
        return ""
