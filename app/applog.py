"""轻量轮转日志。

**为什么需要**：出错时 `diagnostics.last_error()` 只在内存里——换个操作、重启一下就没了，
用户报问题时常说「刚才失败了」，而没有可取证的东西。

**三条约束**（写法都以此为准）：

1. **绝不抛异常**：磁盘满、目录只读、文件被占用，都不能让下载失败。任何写日志的路径都
   ``try/except`` 吞掉。
2. **不记敏感值**：键名含 cookie / token / secret / password 等一律写成 ``<已隐藏>``；
   URL 只保留路径、丢掉查询串（令牌常藏在 query 里，而帖子 ID 在路径上，够定位问题）。
3. **有界**：超过 ``MAX_BYTES`` 就轮转，最多保留 ``KEEP_FILES`` 份历史。

日志只写在本机数据目录，不会自动上传；诊断报告里只带**路径**，不带内容。
"""

import os
import sys
import threading
import time

from . import i18n

# 测试或便携模式可以用环境变量把日志改到别处，避免污染真实日志
ENV_LOG_DIR = "VD_LOG_DIR"

MAX_BYTES = 1_000_000
KEEP_FILES = 3
REDACT_HINTS = ("cookie", "token", "secret", "password", "passwd", "session",
                "authorization", "auth")
REDACTED_KEY = "log_redacted"
_lock = threading.Lock()


def _is_test_run() -> bool:
    """主模块名以 ``test_`` 开头时视为测试运行。

    真实踩到过：跑一遍测试套件会往用户的真实日志里写十几行，里面甚至有
    ``panel=<Mock name='mock.platform' …>`` 这种噪声。测试运行改写到 ``logs/test/``
    子目录——既不污染用户日志，开发者仍然看得到测试期的日志。
    """
    main = sys.modules.get("__main__")
    name = os.path.basename(getattr(main, "__file__", "") or "")
    return name.startswith("test_")


def log_dir() -> str:
    override = os.environ.get(ENV_LOG_DIR)
    if override:
        return override
    from . import settings

    base = os.path.join(settings._settings_dir(), "logs")
    return os.path.join(base, "test") if _is_test_run() else base


def log_path() -> str:
    return os.path.join(log_dir(), "videodownloader.log")


def _one_line(text) -> str:
    """字段值里的换行会破坏「一条事件一行」的格式，统一压平。"""
    return " ".join(str(text).split())


def _scrub_url(value: str) -> str:
    """URL 丢掉查询串：令牌常藏在 query 里。"""
    head, sep, _tail = value.partition("?")
    return head + (i18n.tr("log_query_omitted") if sep else "")


def _format_value(key: str, value) -> str:
    lowered = key.lower()
    if any(hint in lowered for hint in REDACT_HINTS):
        return i18n.tr(REDACTED_KEY)
    if isinstance(value, str):
        text = _one_line(value)
        if text.lower().startswith(("http://", "https://")):
            return _scrub_url(text)
        return text
    if isinstance(value, (list, tuple)):
        return ",".join(_one_line(item) for item in value)
    return _one_line(value)


def format_line(name: str, fields: dict) -> str:
    """把事件格式化成一行（供测试与诊断复用）。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    parts = [f"{stamp}  {_one_line(name)}"]
    for key in sorted(fields):
        parts.append(f"{key}={_format_value(key, fields[key])}")
    return "  ".join(parts)


def _rotate(path: str) -> None:
    """超过上限就轮转：``app.log`` → ``app.log.1`` → ``app.log.2`` …"""
    try:
        if os.path.getsize(path) < MAX_BYTES:
            return
    except OSError:
        return
    for index in range(KEEP_FILES, 0, -1):
        older = f"{path}.{index}"
        newer = path if index == 1 else f"{path}.{index - 1}"
        try:
            if os.path.exists(newer):
                os.replace(newer, older)
        except OSError:
            pass


def log_event(name: str, **fields) -> None:
    """写一条事件。任何失败都静默——日志不能影响下载。"""
    try:
        directory = log_dir()
        os.makedirs(directory, exist_ok=True)
        path = log_path()
        line = format_line(name, fields)
        with _lock:
            _rotate(path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        pass


def log_exception(name: str, exc: BaseException, **fields) -> None:
    """记录异常类型与消息（不写 traceback，避免日志体积失控）。"""
    log_event(name, error=f"{exc.__class__.__name__}: {exc}", **fields)


def tail(lines: int = 200) -> str:
    """读日志末尾若干行（供界面/诊断使用）；读不到就返回空串。"""
    try:
        with open(log_path(), encoding="utf-8", errors="replace") as handle:
            content = handle.readlines()
    except OSError:
        return ""
    return "".join(content[-max(1, int(lines)):])
