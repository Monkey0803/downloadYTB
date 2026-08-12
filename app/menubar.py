"""macOS menubar（屏幕顶部状态栏）常驻图标 — pyobjc NSStatusBar 实现。

仅 macOS 启用：其它平台 create() 直接返回 None，不引入任何依赖。

与 tkinter 事件循环的共存原理：macOS 上 Tk 本身就是一个 Cocoa 应用，
Tk 的 mainloop 会驱动 NSApplication 的事件分发，因此在主线程创建
NSStatusItem 后无需额外 runloop。

⚠️ 关键约束：菜单点击回调由 AppKit 经 pyobjc trampoline 派发，此时
Tk 的 mainloop 正阻塞在 Tcl_DoOneEvent 内（_tkinter 已把线程状态存入
模块级 tcl_tstate 并释放 GIL）。若在该回调里调用任何 _tkinter 方法
（包括 `app.after`），嵌套的 ENTER_TCL/LEAVE_TCL 会把 tcl_tstate 置空，
mainloop 随后服务定时器事件时 PyEval_RestoreThread(NULL) 直接
Fatal Error（SIGABRT，已在打包后的 .app 实测复现）。
因此回调中只设置纯 Python 的 pending 标志，由 GUI 侧的常规
`after` 轮询（take_pending）取走并执行真正的 Tk 操作。

菜单结构：
    当前状态（只读，菜单弹出时通过 menuNeedsUpdate: 刷新）
    ─────────
    显示主窗口
    ─────────
    退出
"""

import sys

from . import i18n

_AVAILABLE = False
if sys.platform == "darwin":
    try:
        from AppKit import (
            NSApp, NSApplication, NSImage, NSMenu, NSMenuItem, NSObject,
            NSStatusBar, NSVariableStatusItemLength,
        )
        _AVAILABLE = True
    except Exception:  # pyobjc 未安装等情况：静默降级为无 menubar
        _AVAILABLE = False


if _AVAILABLE:

    class _StatusBarDelegate(NSObject):
        """NSStatusItem 菜单的 target 与 NSMenuDelegate。"""

        def initWithController_(self, controller):
            self = self.init()
            if self is None:
                return None
            self.controller = controller
            return self

        # —— 菜单动作：只设置 pending 标志，绝不调用 _tkinter（见模块 docstring）——

        def showWindow_(self, sender):
            NSApp.activateIgnoringOtherApps_(True)
            self.controller.pending = "show"

        def quitApp_(self, sender):
            NSApp.activateIgnoringOtherApps_(True)
            self.controller.pending = "quit"

        # —— NSMenuDelegate：菜单即将弹出时刷新状态文案 ——

        def menuNeedsUpdate_(self, menu):
            self.controller.refresh_status()

    class MenuBarController:
        """持有 NSStatusItem 与菜单；负责状态文案刷新与资源引用保活。"""

        def __init__(self, app, icon_path, fallback_icon_path=None):
            self.app = app
            self.pending = None  # "show" / "quit"，由 GUI 侧 after 轮询取走
            self._bar = NSStatusBar.systemStatusBar()
            self._item = self._bar.statusItemWithLength_(
                NSVariableStatusItemLength)
            self._delegate = _StatusBarDelegate.alloc().initWithController_(self)

            self._set_icon(icon_path, fallback_icon_path)
            self._build_menu()

        def _set_icon(self, icon_path, fallback_icon_path):
            img = None
            template = True
            if icon_path:
                img = NSImage.alloc().initWithContentsOfFile_(icon_path)
            if img is None and fallback_icon_path:
                img = NSImage.alloc().initWithContentsOfFile_(fallback_icon_path)
                template = False  # 彩色 logo 不能当模板图
            if img is not None:
                img.setSize_((18, 18))
                img.setTemplate_(template)
                self._item.button().setImage_(img)
            else:
                self._item.button().setTitle_("⬇")

        def update_icon(self, path, template=False):
            """运行时替换状态栏图标（设置页切换 logo 时调用）。

            默认按彩色图处理（setTemplate_(False)，深浅菜单栏下原样
            显示）；加载失败时保留当前图标。
            """
            if not path:
                return
            try:
                img = NSImage.alloc().initWithContentsOfFile_(path)
                if img is None:
                    return
                img.setSize_((18, 18))
                img.setTemplate_(bool(template))
                self._item.button().setImage_(img)
                self._item.button().setTitle_("")
            except Exception:
                pass

        def _build_menu(self):
            menu = NSMenu.alloc().init()
            menu.setDelegate_(self._delegate)

            self._status_mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                i18n.tr("idle"), None, "")
            self._status_mi.setEnabled_(False)
            menu.addItem_(self._status_mi)
            menu.addItem_(NSMenuItem.separatorItem())

            show_mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                i18n.tr("show_window"), b"showWindow:", "")
            show_mi.setTarget_(self._delegate)
            menu.addItem_(show_mi)
            menu.addItem_(NSMenuItem.separatorItem())

            quit_mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                i18n.tr("quit"), b"quitApp:", "")
            quit_mi.setTarget_(self._delegate)
            menu.addItem_(quit_mi)

            self._item.setMenu_(menu)

        def take_pending(self):
            """取走并清空菜单点击产生的待处理动作（GUI 侧轮询调用）。"""
            req, self.pending = self.pending, None
            return req

        def update_language(self):
            """刷新菜单项文案；只在 Tk 主线程调用。"""
            self._status_mi.setTitle_(i18n.tr("idle"))
            menu = self._item.menu()
            if menu is not None and menu.numberOfItems() >= 4:
                menu.itemAtIndex_(2).setTitle_(i18n.tr("show_window"))
                menu.itemAtIndex_(4).setTitle_(i18n.tr("quit"))

        def refresh_status(self):
            """菜单弹出时刷新只读状态项（只读取纯 Python 状态，不碰 Tk）。"""
            try:
                text = self.app.menubar_status_text()
            except Exception:
                text = i18n.tr("idle")
            self._status_mi.setTitle_(text)

        def remove(self):
            """从状态栏移除图标（真正退出前调用）。"""
            try:
                self._bar.removeStatusItem_(self._item)
            except Exception:
                pass


def set_dock_icon(image_path):
    """运行时替换 macOS Dock 图标（setApplicationIconImage_）。

    仅影响运行中的 Dock 图标；.app 包在 Finder 中的静态 .icns 不变。
    非 macOS / pyobjc 不可用 / 文件缺失时静默忽略。
    """
    if not _AVAILABLE or not image_path:
        return
    try:
        img = NSImage.alloc().initWithContentsOfFile_(image_path)
        if img is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(img)
    except Exception:
        pass


def create(app, icon_path=None, fallback_icon_path=None):
    """创建 menubar 图标；非 macOS 或 pyobjc 不可用时返回 None。"""
    if not _AVAILABLE:
        return None
    try:
        return MenuBarController(app, icon_path, fallback_icon_path)
    except Exception:
        return None
