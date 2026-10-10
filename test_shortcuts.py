"""第一批体验增强的 App 级测试：快捷键派发、窗口几何持久化、设置页体检触发。

窗口几何与设置页体检都需要真实 Tk 窗口，所以这里把 settings.load/save 和
macOS menubar 都替换掉，既隔离测试结果，也避免污染用户真实的 settings.json。
"""

import time
from unittest.mock import Mock

from app import gui, settings as settings_mod


class _Kind:
    """MediaPlatformPage.kind_var 的最小替身。"""

    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _Page:
    def __init__(self, kind=None):
        self.video_panel = Mock()
        self.image_panel = Mock()
        self.url_entry = None
        if kind is not None:
            self.kind_var = _Kind(kind)


class _FakeApp:
    def __init__(self, page):
        self.pages = {"x": page}
        self.current_page = "x"
        self.shown = []

    def _current_page(self):
        return self.pages.get(self.current_page)

    def show_page(self, key):
        self.shown.append(key)


def test_active_panel_follows_kind_switch():
    page = _Page(kind="video")
    assert gui._active_panel(page) is page.video_panel
    page.kind_var = _Kind("image")
    assert gui._active_panel(page) is page.image_panel
    # 没有 kind_var 的页面（纯视频页）只暴露 video_panel
    plain = _Page()
    assert gui._active_panel(plain) is plain.video_panel
    assert gui._active_panel(None) is None


def test_shortcut_probe_uses_active_panel():
    page = _Page(kind="image")
    app = _FakeApp(page)
    assert gui.App._shortcut_probe(app) == "break"
    page.image_panel.on_probe.assert_called_once_with()
    page.video_panel.on_probe.assert_not_called()


def test_shortcut_cancel_only_when_busy():
    page = _Page(kind="video")
    app = _FakeApp(page)
    # 空闲：不接管 Esc
    page.video_panel._busy = False
    page.video_panel._downloading = False
    page.video_panel._paused = False
    assert gui.App._shortcut_cancel(app) is None
    page.video_panel.on_cancel.assert_not_called()

    # 下载中：接管并取消
    page.video_panel._downloading = True
    assert gui.App._shortcut_cancel(app) == "break"
    page.video_panel.on_cancel.assert_called_once_with()


def test_shortcut_focus_url_selects_entry():
    page = _Page(kind="image")
    entry = Mock()
    page.image_panel.url_entry = Mock(entry=entry)
    app = _FakeApp(page)

    assert gui.App._shortcut_focus_url(app) == "break"
    entry.focus_set.assert_called_once_with()
    entry.selection_range.assert_called_once_with(0, "end")


def test_shortcut_settings_opens_settings_page():
    app = _FakeApp(_Page(kind="image"))
    assert gui.App._shortcut_settings(app) == "break"
    assert app.shown == ["settings"]


def test_app_restores_and_saves_window_geometry():
    saved = {}
    original_load = settings_mod.load
    original_save = settings_mod.save
    original_create = gui.menubar.create

    def fake_load():
        data = dict(settings_mod.DEFAULTS)
        data["language"] = "zh_CN"
        data["browser_cookies"] = ""      # 体检会立刻返回「未启用」，不碰钥匙串
        data["window_geometry"] = "880x760+64+48"
        return data

    settings_mod.load = fake_load
    settings_mod.save = lambda data: saved.update(data)
    gui.menubar.create = lambda *a, **kw: None

    app = None
    try:
        app = gui.App()
        app.update_idletasks()
        app.update()
        # 宽度小于 minsize(920) 会被抬起，位置保留
        assert app.geometry() == "920x760+64+48"

        app._save_window_geometry()
        assert saved.get("window_geometry") == "920x760+64+48"
    finally:
        settings_mod.load = original_load
        settings_mod.save = original_save
        gui.menubar.create = original_create
        if app is not None:
            app.destroy()


def test_settings_page_probes_cookies_only_when_shown():
    """体检不能在启动时发生（会平白弹钥匙串授权），只在打开设置页时触发。"""
    saved = {}
    original_load = settings_mod.load
    original_save = settings_mod.save
    original_create = gui.menubar.create

    def fake_load():
        data = dict(settings_mod.DEFAULTS)
        data["language"] = "zh_CN"
        data["browser_cookies"] = ""
        data["window_geometry"] = "1020x745"
        return data

    settings_mod.load = fake_load
    settings_mod.save = lambda data: saved.update(data)
    gui.menubar.create = lambda *a, **kw: None

    app = None
    try:
        app = gui.App()
        app.update_idletasks()
        app.update()
        page = app.pages["settings"]
        # 启动阶段（未显示设置页）不应发起体检
        assert page._cookie_probe_token == 0

        app.show_page("settings")
        for _ in range(60):
            app.update_idletasks()
            app.update()
            if page._cookie_probe_token:
                break
            time.sleep(0.02)
        assert page._cookie_probe_token >= 1
    finally:
        settings_mod.load = original_load
        settings_mod.save = original_save
        gui.menubar.create = original_create
        if app is not None:
            app.destroy()


if __name__ == "__main__":
    test_active_panel_follows_kind_switch()
    test_shortcut_probe_uses_active_panel()
    test_shortcut_cancel_only_when_busy()
    test_shortcut_focus_url_selects_entry()
    test_shortcut_settings_opens_settings_page()
    test_app_restores_and_saves_window_geometry()
    test_settings_page_probes_cookies_only_when_shown()
    print("PASS: 快捷键派发、窗口几何持久化与设置页体检触发")
