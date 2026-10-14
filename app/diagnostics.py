"""诊断信息导出：一键复制环境与最近一次错误，便于反馈问题。

设计取向：**可公开**。报告会被贴到 GitHub issue，因此刻意不包含个人数据：

- 只报告保存目录是否已按类型自定义（条数），不写具体路径；
- 只报告 Cookie 来源类型与可用性，不写浏览器 profile 路径或 cookies.txt 路径；
- 完全不含 Cookie 内容、账号或链接；
- 其余路径（如 ffmpeg）统一把家目录替换成 ``~``。

字段名固定用中文：本项目的 issue 与提交说明都是中文，报告是给维护者看的，
没必要为一段诊断文本再维护五套文案；界面按钮的文案仍然走 i18n。
"""

import os
import platform
import sys

from . import browser_cookies, i18n

# 最近一次在界面上展示过的错误，供报告引用
_last_error = ""


def record_error(text: str) -> None:
    """记录界面最近一次展示的错误（面板出错时调用）。

    除了留在内存里供「复制诊断信息」引用，同时写进日志文件——用户重启后还能取到。
    """
    global _last_error
    _last_error = (text or "").strip()[:1000]
    try:
        from . import applog

        applog.log_event("ui_error", message=_last_error)
    except Exception:
        pass


def last_error() -> str:
    return _last_error


def _sanitize(text: str) -> str:
    """把家目录替换成 ``~``，避免报告里泄露用户名。"""
    home = os.path.expanduser("~")
    if home and home in text:
        text = text.replace(home, "~")
    return text


def _dependency_version(module_name: str) -> str:
    try:
        module = __import__(module_name)
    except Exception as exc:
        return i18n.tr("diag_unavailable_exc", name=exc.__class__.__name__)
    version = getattr(getattr(module, "version", None), "__version__", None)
    if version:
        return str(version)
    return str(getattr(module, "__version__", i18n.tr("diag_unknown")))


def _log_line() -> str:
    """日志文件位置（家目录替换成 ~）；只报路径，不带内容。"""
    try:
        from . import applog

        return _sanitize(applog.log_path())
    except Exception:
        return i18n.tr("diag_unknown")


def _proxy_line() -> str:
    """代理设置（**密码打码**）；只报是否启用与地址。"""
    try:
        from . import netproxy

        return netproxy.describe()
    except Exception:
        return i18n.tr("diag_unknown")


def _ytdlp_line() -> str:
    """yt-dlp 版本 + 它到底来自哪里（App 内置还是本机更新）。

    排查「同一版本号行为不同」时，来源比版本号本身更关键。
    """
    try:
        from . import ytdlp_update

        source = ytdlp_update.active_source()
    except Exception:
        source = "bundled"
    label = i18n.tr("ytdlp_source_managed" if source == "managed"
                    else "ytdlp_source_bundled")
    return f"{_dependency_version('yt_dlp')} ({label})"


def _ffprobe_line() -> str:
    """ffprobe 是否可用（第 15 项：没有它「按章节切分」与「SponsorBlock 剪切」会失败）。

    报告里必须能看到这一项：用户反馈「剪切总是失败」时，看一眼就知道是不是这个原因，
    不必反复追问环境。
    """
    try:
        from .ffmpeg_util import has_ffprobe
    except Exception:
        return i18n.tr("diag_unavailable")
    if has_ffprobe():
        return i18n.tr("diag_exists")
    return i18n.tr("diag_ffprobe_needed")


def _ffmpeg_line() -> str:
    try:
        from .ffmpeg_util import ffmpeg_location_for_ytdlp
    except Exception:
        return i18n.tr("diag_unavailable")
    path = ffmpeg_location_for_ytdlp()
    if not path:
        return i18n.tr("diag_not_found")
    state = i18n.tr("diag_exists" if os.path.exists(path) else "diag_missing")
    return i18n.tr("diag_path_state", path=_sanitize(path), state=state)


def _cookie_lines(probe) -> list:
    browser = browser_cookies.browser()
    source = i18n.tr("diag_source_off")
    if browser_cookies.cookie_file_exists():
        source = "cookies.txt"
    elif browser_cookies.cookie_file_missing():
        source = i18n.tr("diag_source_file_missing")
    elif browser:
        source = i18n.tr("diag_source_browser", browser=browser)

    if probe is None:
        status = i18n.tr("diag_status_not_checked")
    elif probe.status == browser_cookies.PROBE_OK:
        status = i18n.tr("diag_status_ok", count=probe.cookie_count)
    elif probe.status == browser_cookies.PROBE_OFF:
        status = i18n.tr("diag_source_off")
    else:
        status = i18n.tr("diag_unavailable")

    lines = [i18n.tr("diag_cookie_source", source=source),
             i18n.tr("diag_cookie_status", status=status)]
    if probe is not None and probe.unreadable and probe.detail:
        lines.append(i18n.tr("diag_cookie_error", error=_sanitize(probe.detail)))
    return lines


def _save_dir_line(settings: dict) -> str:
    configured = settings.get("save_dirs")
    count = len(configured) if isinstance(configured, dict) else 0
    if count:
        return i18n.tr("diag_save_dirs_custom", count=count)
    return i18n.tr("diag_save_dirs_default")


def build_report(settings: dict = None, probe=None) -> str:
    """生成诊断报告文本。

    probe 传入已完成的 ``browser_cookies.ProbeResult`` 可避免重复检测；
    为 None 时现场检测一次（可能耗时到秒级，请在后台线程调用）。
    """
    settings = settings or {}
    if probe is None:
        probe = browser_cookies.probe()

    from . import __version__

    lines = [
        i18n.tr("diag_header"),
        i18n.tr("diag_app_version", value=__version__),
        i18n.tr("diag_os", value=platform.platform()),
        i18n.tr("diag_python", value=sys.version.split()[0]),
        i18n.tr("diag_ytdlp", value=_ytdlp_line()),
        i18n.tr("diag_gallerydl", value=_dependency_version("gallery_dl")),
        i18n.tr("diag_ffmpeg", value=_ffmpeg_line()),
        i18n.tr("diag_ffprobe", value=_ffprobe_line()),
        i18n.tr("diag_save_dir", value=_save_dir_line(settings)),
        i18n.tr("diag_log_path", value=_log_line()),
        i18n.tr("diag_proxy", value=_proxy_line()),
    ]
    lines.extend(_cookie_lines(probe))

    error = last_error()
    lines.append(i18n.tr("diag_last_error",
                         error=_sanitize(error) if error else i18n.tr("diag_none")))
    return "\n".join(lines)
