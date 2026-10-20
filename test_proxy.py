"""网络代理的测试（ROADMAP 第 6 项）。

三条网络路径读代理的方式不同：`requests` 会读 HTTP(S)_PROXY 环境变量，而 **yt-dlp 与
gallery-dl 都不读**，必须显式传参。这个文件锁住四件事：

1. **校验**：只接受能识别的代理 URL，写错不能把网络整体打断。
2. **优先级**：用户设置 → 环境变量 → 直连。
3. **注入**：三条路径都真的拿到了代理（不是只改了设置页）。
4. **不泄露密码**：代理 URL 常带 ``user:pass@``，诊断与日志里必须打码。
"""

import os
import unittest
from unittest.mock import patch

from app import core, diagnostics, gui, images, netproxy, settings as settings_mod
from app import ytdlp_update

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

_ENV_KEYS = netproxy.ENV_KEYS
_APP = None


def _env_names_are_case_insensitive() -> bool:
    """Windows 的环境变量名大小写不敏感，POSIX 上不是。"""
    probe = "VD_PROXY_CASE_PROBE"
    os.environ[probe] = "1"
    try:
        return probe.lower() in os.environ
    finally:
        os.environ.pop(probe, None)
        os.environ.pop(probe.lower(), None)


_ENV_CASE_INSENSITIVE = _env_names_are_case_insensitive()


def _shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


class _CleanProxyCase(unittest.TestCase):
    """清掉环境变量与当前值，避免本机代理设置影响用例。"""

    def setUp(self):
        self._saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        for key in _ENV_KEYS:
            os.environ.pop(key, None)
        self._saved_current = netproxy.current()
        netproxy.set_current("")

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        netproxy.set_current(self._saved_current)


class ValidateTests(unittest.TestCase):
    def test_accepts_http_and_https(self):
        for value in ("http://127.0.0.1:7890", "https://proxy.example.com:8080",
                      "http://proxy.example.com", "http://user:pass@host:1080",
                      "  http://127.0.0.1:7890  "):
            normalized, error = netproxy.validate(value)
            self.assertIsNone(error, value)
            self.assertEqual(normalized, value.strip())

    def test_empty_means_direct(self):
        for value in ("", "   ", None):
            self.assertEqual(netproxy.validate(value), ("", None))

    def test_rejects_missing_scheme(self):
        _, error = netproxy.validate("127.0.0.1:7890")
        self.assertIn("http://", error)

    def test_rejects_socks_with_a_hint(self):
        """暂不支持 socks：要明确告诉用户而不是静默直连。"""
        for value in ("socks5://127.0.0.1:1080", "socks4://host:1080"):
            _, error = netproxy.validate(value)
            self.assertTrue(error)

    def test_rejects_spaces_and_bad_hosts(self):
        self.assertTrue(netproxy.validate("http://a b:1")[1])
        self.assertTrue(netproxy.validate("http://")[1])
        self.assertTrue(netproxy.validate("http://host:notaport")[1])
        self.assertTrue(netproxy.validate("http://host:70000")[1])

    def test_bad_value_never_becomes_current(self):
        netproxy.set_current("http://ok:1")
        netproxy.set_current("这不是地址")
        self.assertEqual(netproxy.current(), "")

    def test_redacts_only_the_password(self):
        self.assertEqual(netproxy.redact("http://user:pass@host:1080"),
                         "http://user:***@host:1080")
        self.assertEqual(netproxy.redact("http://host:1080"), "http://host:1080")
        self.assertEqual(netproxy.redact(""), "")
        self.assertNotIn("pass", netproxy.redact("http://user:pass@host"))


class PriorityTests(_CleanProxyCase):
    def test_setting_wins_over_environment(self):
        os.environ["HTTPS_PROXY"] = "http://env:1"
        value = netproxy.set_from_settings({"proxy": "http://setting:2"})
        self.assertEqual(value, "http://setting:2")
        self.assertEqual(netproxy.current(), "http://setting:2")

    def test_empty_setting_uses_the_environment(self):
        os.environ["HTTPS_PROXY"] = "http://env:1"
        self.assertEqual(netproxy.set_from_settings({"proxy": ""}), "http://env:1")

    def test_invalid_setting_falls_back_to_the_environment(self):
        """写错时也别把网络整体打断：界面上报错，但仍能用系统环境变量的代理。"""
        os.environ["HTTPS_PROXY"] = "http://env:1"
        self.assertEqual(netproxy.set_from_settings({"proxy": "乱写"}), "http://env:1")

    def test_environment_order_and_invalid_values(self):
        os.environ["ALL_PROXY"] = "http://all:3"
        self.assertEqual(netproxy.env_proxy(), "http://all:3")
        os.environ["HTTP_PROXY"] = "http://http:2"
        self.assertEqual(netproxy.env_proxy(), "http://http:2")
        # 大小写不敏感的平台（Windows）只能用一个名字：HTTPS_PROXY 与
        # https_proxy 是同一个变量，同时设只会互相覆盖。换一个真正生效的
        # 名字，断言的仍然是「HTTPS 优先于 HTTP」这条优先级。
        name = "HTTPS_PROXY" if _ENV_CASE_INSENSITIVE else "https_proxy"
        os.environ[name] = "http://lower:1"
        self.assertEqual(netproxy.env_proxy(), "http://lower:1", "HTTPS 优先于 HTTP")
        os.environ["HTTPS_PROXY"] = "垃圾值"
        # 大小写不敏感时这一步覆盖的就是上面那个变量，于是回落到 HTTP_PROXY
        expected = "http://http:2" if _ENV_CASE_INSENSITIVE else "http://lower:1"
        self.assertEqual(netproxy.env_proxy(), expected, "非法环境变量应跳过")

    def test_no_proxy_anywhere_means_direct(self):
        self.assertEqual(netproxy.set_from_settings({}), "")
        self.assertFalse(netproxy.is_enabled())


class InjectionTests(_CleanProxyCase):
    """代理必须真的注入三条路径，而不只是存进设置。"""

    def test_requests_session_gets_the_proxy(self):
        netproxy.set_current("http://proxy:9")

        class _Session:
            def __init__(self):
                self.proxies = {}

        session = _Session()
        netproxy.apply_to_session(session)
        self.assertEqual(session.proxies,
                         {"http": "http://proxy:9", "https": "http://proxy:9"})

    def test_session_is_left_alone_without_a_proxy(self):
        class _Session:
            def __init__(self):
                self.proxies = {}

        session = _Session()
        netproxy.apply_to_session(session)
        self.assertEqual(session.proxies, {}, "没代理时不要动会话，保持默认行为")

    def test_gallery_dl_config_gets_the_proxy(self):
        netproxy.set_current("http://proxy:9")
        seen = []

        class _Config:
            def set(self, section, key, value):
                seen.append((section, key, value))

        netproxy.apply_to_gallery_dl(_Config())
        self.assertIn((("downloader",), "proxy", "http://proxy:9"), seen)
        self.assertIn((("extractor",), "proxy", "http://proxy:9"), seen)

    def test_ytdlp_options(self):
        self.assertEqual(netproxy.ytdlp_options(), {})
        netproxy.set_current("http://proxy:9")
        self.assertEqual(netproxy.ytdlp_options(), {"proxy": "http://proxy:9"})

    def test_core_opts_carry_the_proxy(self):
        netproxy.set_current("http://proxy:9")
        # 用 .get() 断言：缺键时给出清晰的失败信息，而不是 KeyError
        self.assertEqual(core._base_opts().get("proxy"), "http://proxy:9")
        netproxy.set_current("")
        self.assertNotIn("proxy", core._base_opts())

    def test_core_opts_use_the_environment_proxy(self):
        """yt-dlp 不读环境变量，所以这一步必须由我们补上。"""
        os.environ["HTTPS_PROXY"] = "http://env:1"
        netproxy.set_from_settings({})
        self.assertEqual(core._base_opts().get("proxy"), "http://env:1")

    def test_image_requests_pass_proxies(self):
        netproxy.set_current("http://proxy:9")
        captured = {}

        class _Response:
            content = b"data"

            def raise_for_status(self):
                return None

        def fake_get(url, **kwargs):
            captured.update(kwargs)
            return _Response()

        with patch.object(images.requests, "get", fake_get):
            images.fetch_image_bytes("https://e/img.jpg", retries=0)
        self.assertEqual(captured.get("proxies"),
                         {"http": "http://proxy:9", "https": "http://proxy:9"})

    def test_updater_session_carries_the_proxy(self):
        netproxy.set_current("http://proxy:9")
        created = []

        class _Session:
            def __init__(self):
                self.proxies = {}
                created.append(self)

        with patch.object(ytdlp_update, "_session", ytdlp_update._session):
            with patch("requests.Session", _Session):
                session = ytdlp_update._session()
        self.assertTrue(created, "应当创建了会话")
        self.assertEqual(session.proxies.get("https"), "http://proxy:9")


class DiagnosticsTests(_CleanProxyCase):
    def test_report_shows_the_proxy_without_the_password(self):
        netproxy.set_current("http://user:secret@proxy:9")
        report = diagnostics.build_report({"save_dir": "/tmp"})
        self.assertIn("http://user:***@proxy:9", report)
        self.assertNotIn("secret", report)

    def test_report_says_direct_when_unset(self):
        netproxy.set_current("")
        report = diagnostics.build_report({"save_dir": "/tmp"})
        self.assertIn(netproxy.i18n.tr("proxy_direct"), report)


class _SyncThread:
    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target:
            self._target()


class ProxyCardTests(_CleanProxyCase):
    """设置页「网络代理」卡片：校验、保存与实际生效值。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        super().setUp()
        settings_mod.save = lambda data: None
        self.saved = []
        settings_mod.save = lambda data: self.saved.append(dict(data))
        self.app.settings["proxy"] = ""
        netproxy.set_current("")
        self.app.show_page("settings")
        self.app.update_idletasks()
        self.page = self.app.pages["settings"]
        self.page.proxy_var.set("")
        self.app.update_idletasks()

    def tearDown(self):
        self.app.settings["proxy"] = ""
        netproxy.set_current("")
        settings_mod.save = _ORIGINAL_SAVE
        super().tearDown()

    def test_card_exists(self):
        self.assertTrue(hasattr(self.page, "proxy_test_btn"))
        self.assertEqual(self.page.proxy_test_btn._text, netproxy.i18n.tr("proxy_test"))

    def test_valid_value_is_saved_and_applied(self):
        self.page.proxy_var.set("http://127.0.0.1:7890")
        self.app.update_idletasks()
        self.assertEqual(self.app.settings["proxy"], "http://127.0.0.1:7890")
        self.assertEqual(netproxy.current(), "http://127.0.0.1:7890")
        self.assertEqual(self.saved[-1]["proxy"], "http://127.0.0.1:7890")
        self.assertIn("127.0.0.1:7890", self.page.proxy_status.cget("text"))

    def test_invalid_value_shows_an_error_and_keeps_the_old_one(self):
        self.page.proxy_var.set("http://ok:1")
        self.app.update_idletasks()
        self.page.proxy_var.set("不是地址")
        self.app.update_idletasks()
        self.assertEqual(self.page.proxy_status._fg_role, "ERROR")
        self.assertEqual(self.app.settings["proxy"], "http://ok:1",
                         "非法值不该覆盖已保存的代理")
        self.assertEqual(netproxy.current(), "http://ok:1")

    def test_clearing_disables_the_proxy(self):
        self.page.proxy_var.set("http://ok:1")
        self.app.update_idletasks()
        self.page.proxy_var.set("")
        self.app.update_idletasks()
        self.assertEqual(self.app.settings["proxy"], "")
        self.assertFalse(netproxy.is_enabled())

    def test_test_button_reports_success(self):
        with patch.object(gui.ytdlp_update, "check_latest",
                          lambda: ("2026.11.11", "https://e/a.whl")):
            with patch.object(gui.threading, "Thread", _SyncThread):
                self.page._test_proxy()
            self.app.update_idletasks()
            self.app.update()
        text = self.page.proxy_status.cget("text")
        self.assertIn("2026.11.11", text)
        self.assertEqual(self.page.proxy_test_btn._state, "normal")

    def test_test_button_reports_failure_and_recovers(self):
        def boom():
            raise ytdlp_update.YtdlpUpdateError("连接失败：超时")

        with patch.object(gui.ytdlp_update, "check_latest", boom):
            with patch.object(gui.threading, "Thread", _SyncThread):
                self.page._test_proxy()
            self.app.update_idletasks()
            self.app.update()
        self.assertIn(netproxy.i18n.tr("proxy_failed", message="连接失败：超时"),
                      self.page.proxy_status.cget("text"))
        self.assertEqual(self.page.proxy_test_btn._state, "normal")


if __name__ == "__main__":
    unittest.main()
