"""macOS Dock 角标进度与完成提示（非 macOS 为空操作）。

为什么用「角标 + 弹跳」而不是系统通知横幅：
- Dock 角标与 ``requestUserAttention`` 不需要任何 TCC 授权，打包成 adhoc
  签名的 .app 也能正常工作；
- 真正的通知横幅需要 UserNotifications 框架（当前依赖里没有对应 pyobjc
  子包）并要求用户授权，对未签名应用经常静默失败。

因此这里只做两件确定可靠的事：下载中把进度写进 Dock 角标，完成时把角标
换成 ✓ 并在应用不在前台时弹跳一次图标。所有调用都吞掉异常：Dock 属于
锦上添花，绝不能因为它让下载流程报错。
"""

import sys

_MAX_PERCENT = 100
_DONE_BADGE = "✓"


def enabled_platform() -> bool:
    return sys.platform == "darwin"


def _nsapp():
    """取当前 NSApplication（非 macOS 或未初始化时返回 None）。"""
    if not enabled_platform():
        return None
    try:
        from AppKit import NSApp
    except Exception:
        return None
    return NSApp()


def _tile(app):
    try:
        return app.dockTile()
    except Exception:
        return None


def _set_badge(text: str) -> None:
    app = _nsapp()
    if app is None:
        return
    tile = _tile(app)
    if tile is None:
        return
    try:
        tile.setBadgeLabel_(text)
        tile.display()
    except Exception:
        pass


def set_progress(percent) -> None:
    """把下载进度写成 Dock 角标，如 ``42%``。"""
    if not enabled_platform():
        return
    try:
        value = int(round(float(percent)))
    except (TypeError, ValueError):
        return
    value = max(0, min(_MAX_PERCENT, value))
    _set_badge(f"{value}%")


def set_done(attention: bool = True) -> None:
    """完成：角标显示 ✓；应用不在前台时弹跳一次 Dock 图标。"""
    if not enabled_platform():
        return
    _set_badge(_DONE_BADGE)
    if not attention:
        return
    app = _nsapp()
    if app is None:
        return
    try:
        from AppKit import NSInformationalRequest
        if not app.isActive():
            app.requestUserAttention_(NSInformationalRequest)
    except Exception:
        pass


def clear() -> None:
    """清除角标（取消、失败，或窗口重新获得焦点时调用）。"""
    if not enabled_platform():
        return
    _set_badge("")
