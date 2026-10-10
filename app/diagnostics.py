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

from . import browser_cookies

# 最近一次在界面上展示过的错误，供报告引用
_last_error = ""


def record_error(text: str) -> None:
    """记录界面最近一次展示的错误（面板出错时调用）。"""
    global _last_error
    _last_error = (text or "").strip()[:1000]


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
        return f"不可用（{exc.__class__.__name__}）"
    version = getattr(getattr(module, "version", None), "__version__", None)
    if version:
        return str(version)
    return str(getattr(module, "__version__", "未知"))


def _ffmpeg_line() -> str:
    try:
        from .ffmpeg_util import ffmpeg_location_for_ytdlp
    except Exception:
        return "不可用"
    path = ffmpeg_location_for_ytdlp()
    if not path:
        return "未找到"
    exists = "存在" if os.path.exists(path) else "缺失"
    return f"{_sanitize(path)}（{exists}）"


def _cookie_lines(probe) -> list:
    browser = browser_cookies.browser()
    source = "未启用"
    if browser_cookies.cookie_file_exists():
        source = "cookies.txt"
    elif browser_cookies.cookie_file_missing():
        source = "cookies.txt（已配置但文件不存在）"
    elif browser:
        source = f"浏览器 {browser}"

    if probe is None:
        status = "未检测"
    elif probe.status == browser_cookies.PROBE_OK:
        status = f"可用（读取到 {probe.cookie_count} 条）"
    elif probe.status == browser_cookies.PROBE_OFF:
        status = "未启用"
    else:
        status = "不可用"

    lines = [f"- Cookie 来源: {source}", f"- Cookie 状态: {status}"]
    if probe is not None and probe.unreadable and probe.detail:
        lines.append(f"- Cookie 错误: {_sanitize(probe.detail)}")
    return lines


def _save_dir_line(settings: dict) -> str:
    configured = settings.get("save_dirs")
    count = len(configured) if isinstance(configured, dict) else 0
    if count:
        return f"已按下载方式自定义 {count} 项（路径略）"
    return "使用默认目录（~/Downloads）"


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
        "## 多平台下载器 诊断信息",
        f"- 应用版本: {__version__}",
        f"- 系统: {platform.platform()}",
        f"- Python: {sys.version.split()[0]}",
        f"- yt-dlp: {_dependency_version('yt_dlp')}",
        f"- gallery-dl: {_dependency_version('gallery_dl')}",
        f"- ffmpeg: {_ffmpeg_line()}",
        f"- 保存目录: {_save_dir_line(settings)}",
    ]
    lines.extend(_cookie_lines(probe))

    error = last_error()
    lines.append(f"- 最近一次错误: {_sanitize(error) if error else '无'}")
    return "\n".join(lines)
