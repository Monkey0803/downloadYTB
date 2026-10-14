"""跨平台「任务栏 / Dock 进度」的统一入口。

三个后端各自独立，且都保证**失败不影响下载**：

- macOS：``dock``（Dock 角标 + 图标弹跳），已在本机打包 App 内实测。
- Windows：``taskbar_win``（ITaskbarList3 进度条 + FlashWindowEx）。
- Linux：``taskbar_linux``（Unity LauncherEntry，尽力而为）。

Windows / Linux 后端在 macOS 上无法真机验证，详见各模块头部说明。
"""

import sys

from . import dock

_BACKEND_KINDS = {"darwin": "dock", "win32": "win", "cygwin": "win", "linux": "linux"}

_backend = None
_backend_key = None


def _kind():
    if sys.platform.startswith("win"):
        return "win"
    return _BACKEND_KINDS.get(sys.platform)


class _DockBackend:
    """把 dock 的模块级函数适配成后端对象，让上层只有一条调用路径。"""

    def set_progress(self, percent):
        dock.set_progress(percent)

    def set_done(self, attention=True):
        dock.set_done(attention=attention)

    def clear(self):
        dock.clear()


def _load_backend(kind, window):
    if kind == "dock":
        return _DockBackend()
    if kind == "win":
        from . import taskbar_win

        return taskbar_win.Backend(window=window)
    if kind == "linux":
        from . import taskbar_linux

        return taskbar_linux.Backend()
    return None


def enabled_platform() -> bool:
    """当前平台是否具备可用的任务栏/Dock 进度能力。"""
    kind = _kind()
    if kind == "dock":
        return dock.enabled_platform()
    if kind == "win":
        return True
    if kind == "linux":
        from . import taskbar_linux

        return taskbar_linux.enabled()
    return False


def _backend_for(window=None):
    global _backend, _backend_key
    kind = _kind()
    if kind is None:
        return None
    # Windows 需要窗口句柄，换窗口（理论上不会）时重建
    key = (kind, id(window) if kind == "win" else None)
    if _backend is None or _backend_key != key:
        _backend = _load_backend(kind, window)
        _backend_key = key
    return _backend


def _call(method, *args, window=None):
    """所有调用都吞掉异常：进度显示失败绝不能让下载失败。"""
    try:
        backend = _backend_for(window)
        if backend is None:
            return
        getattr(backend, method)(*args)
    except Exception:
        pass


def set_progress(percent, window=None):
    _call("set_progress", percent, window=window)


def set_done(attention=True, window=None):
    _call("set_done", attention, window=window)


def clear(window=None):
    _call("clear", window=window)


def reset_backend():
    """丢弃缓存的后端（测试用；也便于平台切换后重新探测）。"""
    global _backend, _backend_key
    _backend = None
    _backend_key = None


# 按用户开关决定是否写任务栏的应用层薄包装：面板与主窗口共用，避免到处重复判断
# dock_status_enabled()。测试里仍以 gui._taskbar_progress 打桩（gui 会重新导出）。
def _taskbar_progress(app, percent):
    """下载进度写入 Dock 角标 / Windows 任务栏进度（用户开关打开时）。"""
    if app.dock_status_enabled():
        set_progress(percent, window=app)


def _taskbar_done(app):
    """完成提示：角标 ✓ / 弹跳，或在任务栏闪烁提示。"""
    if app.dock_status_enabled():
        set_done(window=app)


def _taskbar_clear(app):
    """清除 Dock 角标 / 任务栏进度。"""
    if app.dock_status_enabled():
        clear(window=app)
