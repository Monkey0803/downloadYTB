"""Dock / 任务栏进度提示的测试：macOS 后端与界面接线。

单测用假 AppKit 覆盖「应用不在前台时弹跳」这类真实环境不好构造的分支；
另外在这里回读一次真实 AppKit 的行为（见 __main__ 尾部的说明），确保假件
与 PyObjC 的真实语义一致——PyObjC 的 NSApp 是**可调用的**代理，返回当前
NSApplication 或 None，写成 NSApp.dockTile() 是错的。
"""

import sys
import types
from unittest.mock import Mock

from app import dock, gui, images, settings as settings_mod, taskbar

INFORMATIONAL = 10


class _FakeDockTile:
    def __init__(self):
        self.badge = None
        self.displays = 0

    def setBadgeLabel_(self, value):
        self.badge = value

    def display(self):
        self.displays += 1


class _FakeApp:
    def __init__(self, active=False):
        self.tile = _FakeDockTile()
        self._active = active
        self.attention = []

    def dockTile(self):
        return self.tile

    def isActive(self):
        return self._active

    def requestUserAttention_(self, kind):
        self.attention.append(kind)


def _install_fake_appkit(active=False, app=None):
    """把假 AppKit 注入 sys.modules，返回 (假 NSApplication, 还原函数)。"""
    fake_app = app if app is not None else _FakeApp(active)
    module = types.ModuleType("AppKit")
    module.NSApp = lambda: fake_app          # PyObjC 的 NSApp 就是可调用的
    module.NSInformationalRequest = INFORMATIONAL
    original = sys.modules.get("AppKit")
    sys.modules["AppKit"] = module

    def restore():
        if original is None:
            sys.modules.pop("AppKit", None)
        else:
            sys.modules["AppKit"] = original
    return fake_app, restore


def test_progress_badge_formatting_and_clamping():
    fake_app, restore = _install_fake_appkit()
    try:
        dock.set_progress(42.4)
        assert fake_app.tile.badge == "42%"
        assert fake_app.tile.displays >= 1

        dock.set_progress(150)
        assert fake_app.tile.badge == "100%"
        dock.set_progress(-5)
        assert fake_app.tile.badge == "0%"
        dock.set_progress(0)
        assert fake_app.tile.badge == "0%"

        # 非法输入不改角标，也不抛错
        before = fake_app.tile.badge
        dock.set_progress("not-a-number")
        dock.set_progress(None)
        assert fake_app.tile.badge == before
    finally:
        restore()


def test_done_bounces_only_when_not_active():
    fake_app, restore = _install_fake_appkit(active=False)
    try:
        dock.set_done()
        assert fake_app.tile.badge == "✓"
        assert fake_app.attention == [INFORMATIONAL]
    finally:
        restore()

    fake_app, restore = _install_fake_appkit(active=True)
    try:
        dock.set_done()
        assert fake_app.tile.badge == "✓"
        assert fake_app.attention == []      # 已在前台就不打扰
    finally:
        restore()


def test_clear_resets_badge():
    fake_app, restore = _install_fake_appkit()
    try:
        dock.set_progress(7)
        dock.clear()
        assert fake_app.tile.badge == ""
    finally:
        restore()


def test_non_darwin_is_a_noop():
    fake_app, restore = _install_fake_appkit()
    original_platform = sys.platform
    try:
        taskbar.sys.platform = "linux"
        assert dock.enabled_platform() is False
        dock.set_progress(50)
        dock.set_done()
        dock.clear()
        assert fake_app.tile.badge is None      # 一次都没碰过
        assert fake_app.attention == []
    finally:
        taskbar.sys.platform = original_platform
        restore()


def test_missing_appkit_does_not_raise():
    original = sys.modules.get("AppKit")
    sys.modules["AppKit"] = None                # from AppKit import ... 会失败
    try:
        dock.set_progress(10)                   # 不应抛错
        dock.set_done()
        dock.clear()
    finally:
        if original is None:
            sys.modules.pop("AppKit", None)
        else:
            sys.modules["AppKit"] = original


def test_app_gate_respects_setting_and_platform():
    original_platform = sys.platform
    try:
        on = _fake_app_with_gate(True)
        off = _fake_app_with_gate(False)
        if taskbar.enabled_platform():
            assert on.dock_status_enabled() is True
        # 设置里关掉 → 即使 macOS 也不写角标
        assert off.dock_status_enabled() is False
        # 非 macOS → 即使设置为开也一律关闭
        taskbar.sys.platform = "linux"
        assert on.dock_status_enabled() is False
        assert off.dock_status_enabled() is False
    finally:
        taskbar.sys.platform = original_platform


def _fake_app_with_gate(dock_on=True):
    """带真实开关逻辑的假 App（Mock 的自动属性会恒为真，必须显式绑定）。"""
    app = Mock()
    app.settings = {"dock_status": dock_on}
    app.dock_status_enabled = lambda: (
        bool(app.settings.get("dock_status", True)) and dock.enabled_platform())
    return app


def test_panels_call_dock_on_progress_and_completion():
    """界面接线：进度写角标，完成提示，取消/失败清除。"""
    calls = []
    original_progress = gui.taskbar.set_progress
    original_done = gui.taskbar.set_done
    original_clear = gui.taskbar.clear
    # 界面层会带 window=app（Windows 后端需要窗口句柄）
    gui.taskbar.set_progress = lambda p, **kw: calls.append(("progress", p))
    gui.taskbar.set_done = lambda **kw: calls.append(("done", None))
    gui.taskbar.clear = lambda **kw: calls.append(("clear", None))
    try:
        app = _fake_app_with_gate(True)

        gui._taskbar_progress(app, 33)
        gui._taskbar_done(app)
        gui._taskbar_clear(app)
        assert calls == [("progress", 33), ("done", None), ("clear", None)]

        # 用户在设置里关闭后，一律不再触碰 Dock
        calls.clear()
        app = _fake_app_with_gate(False)
        gui._taskbar_progress(app, 33)
        gui._taskbar_done(app)
        gui._taskbar_clear(app)
        assert calls == []

        # 图片面板完成路径确实会触发完成提示
        calls.clear()
        panel = Mock()
        panel.app = _fake_app_with_gate(True)
        # #7 之后 _download_done 接收 DownloadResult 而不是路径列表
        gui.ImagePanel._download_done(
            panel, images.DownloadResult(paths=["/tmp/dir/a.jpg"]))
        assert calls == [("done", None)]

        # 取消与失败路径会清除角标
        calls.clear()
        gui.ImagePanel._download_cancelled(panel)
        gui.ImagePanel._download_failed(panel, "boom")
        assert calls == [("clear", None), ("clear", None)]
    finally:
        gui.taskbar.set_progress = original_progress
        gui.taskbar.set_done = original_done
        gui.taskbar.clear = original_clear


def test_dock_toggle_handler_persists_and_clears():
    saved = {}
    original_save = settings_mod.save
    cleared = []
    original_clear = dock.clear
    settings_mod.save = lambda data: saved.update(data)
    dock.clear = lambda: cleared.append(True)
    try:
        page = Mock()
        page.app.settings = {}
        page.dock_var = Mock(get=lambda: False)
        gui.SettingsPage._on_dock_status_change(page)
        assert page.app.settings["dock_status"] is False
        assert saved["dock_status"] is False
        assert cleared == [True]        # 关闭时立刻清掉残留角标

        page.dock_var = Mock(get=lambda: True)
        gui.SettingsPage._on_dock_status_change(page)
        assert page.app.settings["dock_status"] is True
        assert cleared == [True]        # 打开时不清
    finally:
        settings_mod.save = original_save
        dock.clear = original_clear


def test_real_appkit_nsapp_semantics():
    """守住假件的关键假设，避免把 dock.py 写成 NSApp.dockTile()。

    PyObjC 的 ``AppKit.NSApp`` 是**可调用**的代理：调用后返回当前
    NSApplication，尚未创建时返回 None。仓库其他代码用的是
    ``NSApp.windows()``（属性访问），两者不可混用。
    """
    if not dock.enabled_platform():
        return
    import AppKit
    # 前面的用例若把假件泄漏进 sys.modules，这里会立刻暴露
    assert getattr(AppKit, "__file__", None), "AppKit 应为真实扩展模块"
    assert callable(AppKit.NSApp)
    value = AppKit.NSApp()
    assert value is None or hasattr(value, "dockTile")


if __name__ == "__main__":
    test_progress_badge_formatting_and_clamping()
    test_done_bounces_only_when_not_active()
    test_clear_resets_badge()
    test_non_darwin_is_a_noop()
    test_missing_appkit_does_not_raise()
    test_app_gate_respects_setting_and_platform()
    test_panels_call_dock_on_progress_and_completion()
    test_dock_toggle_handler_persists_and_clears()
    test_real_appkit_nsapp_semantics()
    print("PASS: Dock 进度角标、完成弹跳与界面接线")
