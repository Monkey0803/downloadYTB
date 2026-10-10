"""Linux 任务栏进度：KDE JobView 与 Unity LauncherEntry，尽力而为。

必须如实说明这个平台的现实情况：

- **没有跨桌面的统一 API**。GNOME Shell 本身不消费任何「进度」信号；Unity /
  带 Ubuntu Dock 扩展的 Ubuntu 走 ``com.canonical.Unity.LauncherEntry``；KDE
  走 ``org.kde.JobViewServer``。本模块两个协议都实现，并按桌面环境自动选择。
  在原生 GNOME 上调用不会报错，但**界面上不会显示进度**。
- 通过 ``gdbus`` 命令行发送信号：仓库依赖里没有 dbus-python / PyGObject，而为
  一个进度条引入 D-Bus 客户端实现不划算。调用被**节流**（整数百分比变化且
  至少间隔 ``MIN_INTERVAL`` 秒），避免每秒拉起几十个进程。
- KDE 的 JobView 只能设置百分比（``setPercent``），不提供取消/暂停回调
  （capabilities 传 0，即不显示可点的取消按钮）。

同样如实说明：本文件在 macOS 上开发，**没有在真实 Linux 上运行验证过**。
两个后端的可测部分（协议选择、命令构造、输出解析、节流、失败处理）由单测覆盖，
真正的 D-Bus 往返只能靠代码审查。
"""

import os
import re
import shutil
import subprocess
import sys
import time

FALLBACK_DESKTOP_ID = "videodownloader"

# Unity LauncherEntry
UNITY_DEST = "com.canonical.Unity"
UNITY_PATH = "/com/canonical/Unity/LauncherEntry"
UNITY_IFACE = "com.canonical.Unity.LauncherEntry"

# KDE JobView
KDE_SERVICE = "org.kde.JobViewServer"
KDE_PATH = "/JobViewServer"
KDE_REQUEST_IFACE = "org.kde.JobViewServer"
KDE_JOB_IFACE = "org.kde.JobViewV2"
KDE_APP_NAME = "VideoDownloader"
# capabilities：0 = 不提供取消/暂停（我们无法处理 KDE 发来的取消请求）
KDE_CAPABILITIES = 0
# JobViewV2 的金额类型：0=Bytes 1=Files 2=Directories
KDE_AMOUNT_FILES = 1

DBUS_SERVICE = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"

# 进度条不需要高频刷新；同时避免频繁拉起子进程
MIN_INTERVAL = 0.5
# gdbus 调用不应拖住下载线程
TIMEOUT = 3

# gdbus 用 GVariant 文本格式打印返回值。object path 到底带不带引号，本机
# 没有会话总线、也没有 dbus-daemon，**无法实测**；所以解析写成宽容的两种都吃。
_OBJECT_PATH_RE = re.compile(r"objectpath\s+['\"]?(/[^\s'\",)\]]+)")
_FALLBACK_PATH_RE = re.compile(r"['\"]?(/org/kde/[A-Za-z0-9_/.\-]+)")
_NAME_OWNER_RE = re.compile(r"['\"]([^'\"]+)['\"]")


def _parse_view_path(output: str):
    """从 requestView 的输出里取出对象路径；两种可能格式都支持。"""
    match = _OBJECT_PATH_RE.search(output or "")
    if match:
        return match.group(1)
    fallback = _FALLBACK_PATH_RE.search(output or "")
    return fallback.group(1) if fallback else None


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


def _desktop_env():
    return (os.environ.get("XDG_CURRENT_DESKTOP", "")
            + ":" + os.environ.get("XDG_SESSION_DESKTOP", "")).lower()


def is_kde():
    env = _desktop_env()
    return "kde" in env or "plasma" in env


def enabled():
    """只要有 gdbus 就算「有能力」，具体协议由桌面环境决定。"""
    return sys.platform.startswith("linux") and _gdbus_path() is not None


def _quote(text):
    """把字符串包成 GVariant 字面量。"""
    return "'" + str(text).replace("'", "\\'") + "'"


def _uint(value):
    """gdbus 需要显式类型，否则 int 会被当成 int32 而与 uint32 不匹配。"""
    return f"<uint32 {int(value)}>"


def _int(value):
    return f"<int32 {int(value)}>"


class _Gdbus:
    """gdbus 调用的薄封装：构造命令、执行、取回 stdout。"""

    def __init__(self, runner=None, path=None):
        self._runner = runner
        self._path = path

    def available(self):
        return bool(self._resolve_path())

    def _resolve_path(self):
        if self._path is None:
            self._path = _gdbus_path() or ""
        return self._path

    def call(self, dest, object_path, method, *args):
        path = self._resolve_path()
        if not path:
            raise OSError("找不到 gdbus")
        runner = self._runner or _load_runner()
        proc = runner([path, "call", "--session", "--dest", dest,
                       "--object-path", object_path, "--method", method,
                       *[str(a) for a in args]],
                      capture_output=True, text=True, timeout=TIMEOUT)
        return getattr(proc, "stdout", "") or ""


class _Throttle:
    """整数百分比 + 最小间隔的双重节流。"""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._last = None
        self._at = 0.0

    def should_send(self, percent):
        whole = int(percent)
        now = self._clock()
        if whole == self._last and (now - self._at) < MIN_INTERVAL:
            return False
        self._last = whole
        self._at = now
        return True

    def reset(self):
        self._last = None
        self._at = self._clock()


class LauncherEntryBackend:
    """Unity / Ubuntu Dock 的 LauncherEntry 协议。"""

    def __init__(self, runner=None, path=None, clock=time.monotonic):
        self._gdbus = _Gdbus(runner, path)
        self._throttle = _Throttle(clock)
        self._visible = False

    def _update(self, properties):
        self._gdbus.call(UNITY_DEST, UNITY_PATH,
                         f"{UNITY_IFACE}.Update", app_uri(), properties)

    def set_progress(self, percent):
        clip = max(0.0, min(100.0, float(percent)))
        if not self._throttle.should_send(clip):
            return
        self._update("{'progress': <%.4f>, 'progress-visible': <true>}"
                     % (clip / 100.0))
        self._visible = True

    def set_done(self, attention=True):
        self._throttle.reset()
        urgent = "true" if attention else "false"
        self._update("{'progress-visible': <false>, 'urgent': <%s>}" % urgent)
        self._visible = False

    def clear(self):
        self._throttle.reset()
        if not self._visible:
            return
        self._update("{'progress-visible': <false>}")
        self._visible = False


class KdeJobViewBackend:
    """KDE Plasma 的 JobViewServer / JobViewV2 协议。

    流程：``GetNameOwner`` 拿到唯一连接名 → ``requestView`` 申请一个任务视图并
    得到对象路径 → 之后所有调用都打到「唯一名 + 该路径」上 → 结束时
    ``terminate``。
    """

    def __init__(self, runner=None, path=None, clock=time.monotonic):
        self._gdbus = _Gdbus(runner, path)
        self._throttle = _Throttle(clock)
        self._owner = None
        self._view = None

    # ---- 协议细节 ----
    def _name_owner(self):
        if self._owner is None:
            # 参数是字符串，必须包成 GVariant 字面量，否则 gdbus 会把它当作
            # 字面量去解析并直接报错（真实踩过）。
            out = self._gdbus.call(DBUS_SERVICE, DBUS_PATH,
                                   f"{DBUS_SERVICE}.GetNameOwner",
                                   _quote(KDE_SERVICE))
            match = _NAME_OWNER_RE.search(out)
            self._owner = match.group(1) if match else ""
        return self._owner

    def _request_view(self):
        out = self._gdbus.call(KDE_SERVICE, KDE_PATH,
                               f"{KDE_REQUEST_IFACE}.requestView",
                               _quote(KDE_APP_NAME), _quote(desktop_id()),
                               _int(KDE_CAPABILITIES))
        path = _parse_view_path(out)
        if not path:
            raise OSError(f"requestView 未返回对象路径: {out[:80]}")
        self._view = path
        return self._view

    def _view_call(self, method, *args):
        """调用 JobViewV2 上的方法；视图失效时丢弃缓存以便下次重建。"""
        self._name_owner()
        if not self._owner:
            raise OSError("JobViewServer 不在总线上")
        if self._view is None:
            self._request_view()
        self._gdbus.call(self._owner, self._view, f"{KDE_JOB_IFACE}.{method}", *args)

    # ---- 对外接口 ----
    def set_progress(self, percent):
        clip = max(0.0, min(100.0, float(percent)))
        if not self._throttle.should_send(clip):
            return
        self._view_call("setInfoMessage",
                        _quote(f"{KDE_APP_NAME}  {clip:.0f}%"))
        # KDE 只认「总量 + 已处理」，这里用 0..100 作为百分比刻度
        self._view_call("setTotalAmount", _uint(KDE_AMOUNT_FILES), _uint(100))
        self._view_call("setPercent", _uint(clip))

    def set_done(self, attention=True):
        self._throttle.reset()
        if self._view is None and self._owner is None:
            return          # 从未启动过任务视图，不必创建再销毁
        try:
            self._view_call("setPercent", _uint(100))
        finally:
            self.terminate()

    def clear(self):
        self._throttle.reset()
        self.terminate()

    def terminate(self):
        try:
            if self._view is not None:
                self._view_call("terminate")
        except Exception:
            pass
        finally:
            self._view = None
            self._owner = None


class Backend:
    """按桌面环境选择协议；两个协议都不可用时静默空操作。"""

    def __init__(self, runner=None, path=None, clock=time.monotonic, force=None):
        choice = force or ("kde" if is_kde() else "unity")
        impl = KdeJobViewBackend if choice == "kde" else LauncherEntryBackend
        self._impl = impl(runner=runner, path=path, clock=clock)
        self.choice = choice

    def set_progress(self, percent):
        self._impl.set_progress(percent)

    def set_done(self, attention=True):
        self._impl.set_done(attention=attention)

    def clear(self):
        self._impl.clear()
