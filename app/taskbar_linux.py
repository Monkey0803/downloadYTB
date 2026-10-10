"""Linux 任务栏进度：Unity LauncherEntry（D-Bus），尽力而为。

必须如实说明这个平台的现实情况：

- **没有跨桌面的统一 API**。GNOME Shell 本身不消费任何「进度」信号，KDE 用的是
  另一套 ``org.kde.JobViewServer``。本模块只实现 Unity 的 LauncherEntry 协议，
  它适用于 Unity 桌面以及带 Ubuntu Dock 扩展的 GNOME（Ubuntu 默认）。
  在原生 GNOME 上调用不会报错，但**界面上不会显示进度**。
- 通过 ``gdbus`` 命令行发送信号：仓库依赖里没有 dbus-python / PyGObject，而为
  一个进度条引入 D-Bus 客户端实现不划算。调用被**节流**（整数百分比变化且
  至少间隔 ``MIN_INTERVAL`` 秒），避免每秒拉起几十个进程。
- KDE 的 JobView 未实现。

同样如实说明：本文件在 macOS 上开发，**没有在真实 Linux 上运行验证过**。
"""

import os
import shutil
import subprocess
import sys
import time

DBUS_DEST = "com.canonical.Unity"
DBUS_PATH = "/com/canonical/Unity/LauncherEntry"
DBUS_IFACE = "com.canonical.Unity.LauncherEntry"

# 进度条不需要高频刷新；同时避免频繁拉起子进程
MIN_INTERVAL = 0.5
# gdbus 调用不应拖住下载线程
TIMEOUT = 3

FALLBACK_DESKTOP_ID = "videodownloader"


def desktop_id():
    """GIO 从 .desktop 启动时会给出真实文件名；否则退回常量。

    LauncherEntry 靠 application://<id>.desktop 与图标关联，
    用错 id 就只是「发了信号但没人认领」。
    """
    launched = os.environ.get("GIO_LAUNCHED_DESKTOP_FILE", "")
    if launched:
        base = os.path.basename(launched)
        if base.endswith(".desktop"):
            base = base[: -len(".desktop")]
        if base:
            return base
    return FALLBACK_DESKTOP_ID


def app_uri():
    return f"application://{desktop_id()}.desktop"


def _gdbus_path():
    return shutil.which("gdbus")


def _load_runner():
    return subprocess.run


def enabled():
    return sys.platform.startswith("linux") and _gdbus_path() is not None


class Backend:
    """被 taskbar.py 调用的后端；任何失败都不应影响下载。"""

    def __init__(self, runner=None, clock=time.monotonic, path=None):
        self._runner = runner
        self._clock = clock
        self._path = path
        self._last_percent = None
        self._last_at = 0.0
        self._visible = False

    def _call(self, properties):
        if self._path is None:
            self._path = _gdbus_path() or ""
        if not self._path:
            return False
        runner = self._runner or _load_runner()
        runner([self._path, "call", "--session", "--dest", DBUS_DEST,
                "--object-path", DBUS_PATH, "--method",
                f"{DBUS_IFACE}.Update", app_uri(), properties],
               capture_output=True, timeout=TIMEOUT)
        return True

    def set_progress(self, percent):
        clip = max(0.0, min(100.0, float(percent)))
        whole = int(clip)
        now = self._clock()
        if whole == self._last_percent and (now - self._last_at) < MIN_INTERVAL:
            return
        self._last_percent = whole
        self._last_at = now
        self._call("{'progress': <%.4f>, 'progress-visible': <true>}"
                   % (clip / 100.0))
        self._visible = True

    def set_done(self, attention=True):
        self._last_percent = None
        self._last_at = self._clock()
        # 完成时不画满进度条，只把「需要关注」标记打开
        urgent = "true" if attention else "false"
        self._call("{'progress-visible': <false>, 'urgent': <%s>}" % urgent)
        self._visible = False

    def clear(self):
        self._last_percent = None
        self._last_at = self._clock()
        if not self._visible:
            return
        self._call("{'progress-visible': <false>}")
        self._visible = False
