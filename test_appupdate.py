"""版本显示与新版本检查的测试（ROADMAP 第 7 项）。

第 7 项的由来：界面任何地方都不显示版本，用户既不知道自己用的是哪版，也不知道有没有
新版——而这类工具恰恰需要跟着站点变化升级。这个文件锁住四件事：

1. **版本比较正确**：``1.11`` 与 ``1.10.0`` 这种长度不同的写法不能误判。
2. **检查从不抛异常**：网络失败、限流、返回内容异常都折算成 ``error`` 状态，界面照常可用。
3. **自动检查有节流**：最多每 24 小时一次，关掉开关就一次都不查。
4. **三条界面出口都跟着更新**：设置页状态、状态栏提示、菜单栏项。
"""

import unittest
from unittest.mock import patch

from app import appupdate, gui, i18n, settings as settings_mod, settingspage

_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create
_APP = None


def _shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, raise_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._payload


class _FakeSession:
    """记录请求参数的最小会话替身。"""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.proxies = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


class VersionParsingTests(unittest.TestCase):
    def test_parses_common_forms(self):
        self.assertEqual(appupdate.parse_version("1.10.0"), (1, 10, 0))
        self.assertEqual(appupdate.parse_version("v1.11.0"), (1, 11, 0))
        self.assertEqual(appupdate.parse_version(" 1.10 "), (1, 10))
        self.assertEqual(appupdate.parse_version("1.10.0-beta.1"), (1, 10, 0))
        self.assertEqual(appupdate.parse_version("垃圾"), ())

    def test_is_newer(self):
        self.assertTrue(appupdate.is_newer("1.11.0", "1.10.0"))
        self.assertTrue(appupdate.is_newer("1.11", "1.10.0"), "长度不同也要正确")
        self.assertTrue(appupdate.is_newer("2.0", "1.99.99"))
        self.assertFalse(appupdate.is_newer("1.10.0", "1.10.0"))
        self.assertFalse(appupdate.is_newer("1.9.9", "1.10.0"))
        self.assertFalse(appupdate.is_newer("1.10", "1.10.0"))

    def test_garbage_never_counts_as_newer(self):
        for bad in ("", "abc", None, "v"):
            self.assertFalse(appupdate.is_newer(bad, "1.10.0"), bad)

    def test_current_version_matches_the_package(self):
        from app import __version__

        self.assertEqual(appupdate.current_version(), __version__)


class LatestReleaseTests(unittest.TestCase):
    def test_parses_a_release(self):
        session = _FakeSession(_FakeResponse(200, {
            "tag_name": "v1.11.0",
            "html_url": "https://github.com/x/y/releases/tag/v1.11.0",
            "body": "notes"}))
        release = appupdate.latest_release(session=session)
        self.assertEqual(release["version"], "1.11.0")
        self.assertIn("v1.11.0", release["url"])
        self.assertEqual(release["notes"], "notes")
        # 只访问公开接口，并带上 User-Agent
        url, kwargs = session.calls[0]
        self.assertEqual(url, appupdate.RELEASES_API)
        self.assertIn("User-Agent", kwargs.get("headers", {}))

    def test_status_codes_map_to_messages(self):
        cases = ((404, appupdate.i18n.tr("update_error_no_release")),
                 (403, appupdate.i18n.tr("update_error_rate_limit")),
                 (429, appupdate.i18n.tr("update_error_rate_limit")),
                 (500, appupdate.i18n.tr("update_error_http", code=500)))
        for status, expected in cases:
            with self.subTest(status=status):
                session = _FakeSession(_FakeResponse(status, {}))
                with self.assertRaises(appupdate.AppUpdateError) as ctx:
                    appupdate.latest_release(session=session)
                self.assertEqual(str(ctx.exception), expected)

    def test_broken_payload_is_reported(self):
        for response in (_FakeResponse(200, {}, raise_json=True),
                         _FakeResponse(200, {"tag_name": ""}),
                         _FakeResponse(200, None)):
            with self.subTest(response=response):
                session = _FakeSession(response)
                with self.assertRaises(appupdate.AppUpdateError):
                    appupdate.latest_release(session=session)

    def test_network_error_is_wrapped(self):
        import requests

        session = _FakeSession(error=requests.RequestException("boom"))
        with self.assertRaises(appupdate.AppUpdateError) as ctx:
            appupdate.latest_release(session=session)
        self.assertIn("boom", str(ctx.exception))

    def test_own_session_gets_the_proxy(self):
        """检查更新也要走用户配置的代理，否则受限网络下这个功能本身就是坏的。"""
        created = []

        class _Session:
            def __init__(self):
                self.proxies = {}
                created.append(self)

            def get(self, url, **kwargs):
                return _FakeResponse(200, {"tag_name": "v1.10.0"})

        from app import netproxy

        netproxy.set_current("http://proxy:9")
        try:
            with patch("requests.Session", _Session):
                appupdate.latest_release()
        finally:
            netproxy.set_current("")
        self.assertTrue(created)
        self.assertEqual(created[0].proxies.get("https"), "http://proxy:9")


class CheckTests(unittest.TestCase):
    def _session(self, tag):
        return _FakeSession(_FakeResponse(200, {"tag_name": tag,
                                                "html_url": "https://e/rel"}))

    def test_newer_tag_reports_update(self):
        result = appupdate.check(current="1.10.0", session=self._session("v1.11.0"))
        self.assertEqual(result["status"], "update")
        self.assertEqual(result["version"], "1.11.0")
        self.assertEqual(result["url"], "https://e/rel")

    def test_same_or_older_tag_reports_current(self):
        for tag in ("v1.10.0", "v1.9.0", "1.10"):
            with self.subTest(tag=tag):
                result = appupdate.check(current="1.10.0", session=self._session(tag))
                self.assertEqual(result["status"], "current", tag)

    def test_never_raises_on_failure(self):
        import requests

        for session in (_FakeSession(error=requests.RequestException("boom")),
                        _FakeSession(_FakeResponse(500, {})),
                        _FakeSession(_FakeResponse(200, raise_json=True))):
            with self.subTest(session=session):
                result = appupdate.check(current="1.10.0", session=session)
                self.assertEqual(result["status"], "error")
                self.assertTrue(result["error"])

    def test_defaults_to_the_packaged_version(self):
        with patch.object(appupdate, "current_version", lambda: "1.9.0"):
            result = appupdate.check(session=self._session("v1.10.0"))
        self.assertEqual(result["status"], "update")
        self.assertEqual(result["version"], "1.10.0")


class AutoCheckThrottleTests(unittest.TestCase):
    def test_respects_the_switch(self):
        self.assertFalse(appupdate.should_auto_check({"check_updates": False}))
        # 缺省是「开」：没写过这个键的用户（老设置文件）也该收到新版本提示
        self.assertTrue(appupdate.should_auto_check({}))

    def test_once_per_day(self):
        now = 1_000_000.0
        self.assertTrue(appupdate.should_auto_check(
            {"check_updates": True, "last_update_check": 0}, now=now))
        self.assertFalse(appupdate.should_auto_check(
            {"check_updates": True, "last_update_check": now - 60}, now=now))
        self.assertTrue(appupdate.should_auto_check(
            {"check_updates": True, "last_update_check": now - 25 * 3600}, now=now))

    def test_dirty_timestamp_is_tolerated(self):
        self.assertTrue(appupdate.should_auto_check(
            {"check_updates": True, "last_update_check": "垃圾"}, now=1_000_000.0))

    def test_mark_checked(self):
        data = {}
        appupdate.mark_checked(data, now=123.0)
        self.assertEqual(data["last_update_check"], 123.0)

    def test_format_checked_at(self):
        # 0 = 从未检查，必须返回空串而不是 1970 年（真实踩到过）
        self.assertEqual(appupdate.format_checked_at(0), "")
        self.assertEqual(appupdate.format_checked_at(-5), "")
        self.assertEqual(appupdate.format_checked_at("垃圾"), "")
        self.assertEqual(appupdate.format_checked_at(None), "")
        self.assertTrue(appupdate.format_checked_at(1_700_000_000))


class _FakeMenubar:
    """只记录新版本提示，避免真的去碰 AppKit。"""

    def __init__(self):
        self.hints = []

    def update_update_hint(self, version):
        self.hints.append(version)


class _SyncThread:
    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target:
            self._target()


class VersionCardTests(unittest.TestCase):
    """设置页「版本与更新」卡片。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.saved = []
        settings_mod.save = lambda data: self.saved.append(dict(data))
        self.fake_menubar = _FakeMenubar()
        self.app.menubar = self.fake_menubar
        self.app.settings["update_available"] = ""
        self.app.settings["last_update_check"] = 0
        self.app.settings["check_updates"] = True
        self.app._update_notified = ""
        self.app._release_url = ""
        self.app.show_page("settings")
        self.app.update_idletasks()
        self.page = self.app.pages["settings"]

    def tearDown(self):
        self.app.settings["update_available"] = ""
        self.app.settings["last_update_check"] = 0
        self.app.menubar = None
        settings_mod.save = _ORIGINAL_SAVE

    def _run_check(self, result):
        with patch.object(gui.appupdate, "check", lambda: result):
            with patch.object(gui.threading, "Thread", _SyncThread):
                self.page._check_updates()
            self.app.update_idletasks()
            self.app.update()

    def test_card_shows_the_packaged_version(self):
        self.page._refresh_version_status()
        self.assertIn(appupdate.current_version(),
                      self.page.version_label.cget("text"))
        self.assertIn(i18n.tr("update_never_checked"),
                      self.page.version_status.cget("text"))
        self.assertEqual(self.page.release_btn._state, "disabled")

    def test_update_result_updates_everywhere(self):
        result = {"status": "update", "version": "9.9.9",
                  "url": "https://e/rel", "error": ""}
        self._run_check(result)
        self.assertEqual(self.app.settings["update_available"], "9.9.9")
        self.assertGreater(self.app.settings["last_update_check"], 0)
        self.assertEqual(self.app._release_url, "https://e/rel")
        self.assertEqual(self.app._update_notified, "9.9.9")
        self.assertIn("9.9.9", self.page.version_status.cget("text"))
        self.assertEqual(self.page.release_btn._state, "normal")
        # 菜单栏项也要跟着变（不用打开设置就能看见）
        self.assertEqual(self.fake_menubar.hints, ["9.9.9"])

    def test_current_result_says_latest(self):
        self._run_check({"status": "current", "version": "1.10.0",
                         "url": "https://e/rel", "error": ""})
        self.assertEqual(self.app.settings["update_available"], "")
        self.assertIn(i18n.tr("update_is_latest"),
                      self.page.version_status.cget("text"))

    def test_failure_is_shown_and_the_button_recovers(self):
        self._run_check({"status": "error", "version": "", "url": "",
                         "error": "连不上"})
        self.assertIn("连不上", self.page.version_status.cget("text"))
        self.assertEqual(self.page.update_check_btn._state, "normal")
        self.assertEqual(self.app.settings["update_available"], "")

    def test_stale_result_is_ignored(self):
        self.page._update_token = 5
        self.page._apply_update_check(1, {"status": "update", "version": "9.9.9",
                                          "url": "", "error": ""}, True)
        self.assertEqual(self.app.settings["update_available"], "")

    def test_auto_check_respects_the_throttle(self):
        calls = []
        with patch.object(gui.appupdate, "check",
                          lambda: calls.append(1) or {"status": "current",
                                                      "version": "", "url": "",
                                                      "error": ""}):
            with patch.object(gui.threading, "Thread", _SyncThread):
                self.app.settings["last_update_check"] = 0
                self.page._auto_check_updates()
                self.assertEqual(len(calls), 1, "超过 24 小时应当检查")
                self.app.update_idletasks()
                self.app.update()

                import time as _time

                self.app.settings["last_update_check"] = _time.time()
                self.page._auto_check_updates()
                self.assertEqual(len(calls), 1, "24 小时内不该再检查")

    def test_auto_check_toggle_is_wired_and_persists(self):
        calls = []
        # CheckPill 是画布控件，command 只在点击时触发；先确认接线正确
        self.assertIs(self.page.update_auto_check._var, self.page.auto_update_var)
        self.assertEqual(self.page.update_auto_check._command,
                         self.page._on_auto_check_toggle)
        self.page.auto_update_var.set(False)
        self.page._on_auto_check_toggle()
        self.app.update_idletasks()
        self.assertFalse(self.app.settings["check_updates"])
        self.assertEqual(self.saved[-1]["check_updates"], False)
        with patch.object(gui.appupdate, "check", lambda: calls.append(1)):
            with patch.object(gui.threading, "Thread", _SyncThread):
                self.app.settings["last_update_check"] = 0
                self.page._auto_check_updates()
        self.assertEqual(calls, [])
        self.page.auto_update_var.set(True)
        self.app.update_idletasks()

    def test_open_release_page_uses_the_stored_url(self):
        self.app._release_url = "https://e/rel"
        opened = []
        # _open_release_page 是设置页的方法：打桩要落在 settingspage 上
        with patch.object(settingspage, "open_path", opened.append):
            self.page._open_release_page()
        self.assertEqual(opened, ["https://e/rel"])

    def test_open_release_page_falls_back_to_the_releases_page(self):
        self.app._release_url = ""
        opened = []
        # _open_release_page 是设置页的方法：打桩要落在 settingspage 上
        with patch.object(settingspage, "open_path", opened.append):
            self.page._open_release_page()
        self.assertEqual(opened, [appupdate.RELEASES_PAGE])


if __name__ == "__main__":
    unittest.main()
