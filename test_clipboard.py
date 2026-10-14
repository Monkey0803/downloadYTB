"""剪贴板处理方式（#1）的界面行为测试。

旧行为：每次窗口获得焦点，只要剪贴板里有链接就**切页 + 覆盖输入框 + 直接联网
解析**，用户无法拒绝也无法关闭。现在默认改成「先提示」，只有显式选「自动填入」
才保留旧行为。

需要真实 Tk 窗口；settings 与 menubar 都替换掉，不会碰用户自己的 settings.json。
"""

import unittest

from app import gui, i18n, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

YOUTUBE_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
WEIBO_URL = "https://weibo.com/2569360232/Rjo6Fqvcw"


_APP = None


def shared_app():
    """整个文件共用一个 App。

    本机实测：第一次 Tk 初始化要 2 秒起（环境繁忙时到 20 秒），而且**销毁根窗口后
    再新建会让 macOS 上的 Tk 直接段错误**。所以这里只建一次、不销毁。
    """
    global _APP
    if _APP is None:
        settings_mod.load = lambda: _fake_load()
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


def _fake_load(**overrides):
    data = dict(settings_mod.DEFAULTS)
    data["language"] = "zh_CN"
    data["browser_cookies"] = ""
    data["window_geometry"] = "1020x745"
    data.update(overrides)
    return data


class ClipboardBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        # 每个用例都从干净状态开始：清掉建议条、去重记录与设置值
        self.app.settings["clipboard_action"] = "suggest"
        self.app._hide_clipboard_suggestion()
        self.app._last_clipboard = None
        self.app.show_page("youtube")
        self.app.pages["youtube"].video_panel.url_var.set("")
        self.probes = []
        # App 调的是页面的 handle_url（MediaPlatformPage 再转发给面板）
        for name, page in self.app.pages.items():
            page.handle_url = (lambda url, _n=name: self.probes.append((_n, url)))

    def tearDown(self):
        # handle_url 是实例属性，删掉即恢复类方法
        for page in self.app.pages.values():
            if "handle_url" in page.__dict__:
                del page.__dict__["handle_url"]

    def _feed_clipboard(self, text):
        """喂一段剪贴板文本并触发检测（不依赖真实系统剪贴板）。"""
        self.app.clipboard_get = lambda: text
        self.app._check_clipboard()

    def _set_action(self, value):
        self.app.settings["clipboard_action"] = value

    # ---------- 三档行为 ----------

    def test_default_action_is_suggest(self):
        self.app.settings.pop("clipboard_action", None)
        self.assertEqual(self.app.clipboard_action(), "suggest")

    def test_invalid_action_falls_back_to_suggest(self):
        for value in ("", None, "AUTO!", "自动"):
            self._set_action(value)
            self.assertEqual(self.app.clipboard_action(), "suggest")

    def test_suggest_only_shows_a_bar(self):
        """默认行为：不切页、不覆盖输入、不联网解析。"""
        self.app.pages["youtube"].video_panel.url_var.set("我在输入中")
        self._set_action("suggest")
        self._feed_clipboard(WEIBO_URL)

        self.assertTrue(self.app.clip_bar.winfo_manager(), "建议条应出现")
        self.assertIn(WEIBO_URL[:30], self.app.clip_label.cget("text"))
        self.assertEqual(self.app.current_page, "youtube", "不应切换页面")
        self.assertEqual(self.app.pages["youtube"].video_panel.url_var.get(), "我在输入中")
        self.assertEqual(self.probes, [], "不应自动解析")

    def test_auto_keeps_old_behaviour(self):
        self._set_action("auto")
        self._feed_clipboard(WEIBO_URL)
        self.assertEqual(self.app.current_page, "weibo")
        self.assertEqual(self.probes, [("weibo", WEIBO_URL)])
        self.assertFalse(self.app.clip_bar.winfo_manager(), "自动模式下不显示建议条")

    def test_off_does_nothing_at_all(self):
        self._set_action("off")
        self._feed_clipboard(WEIBO_URL)
        self.assertFalse(self.app.clip_bar.winfo_manager())
        self.assertEqual(self.app.current_page, "youtube")
        self.assertEqual(self.probes, [])

    def test_unsupported_text_is_ignored(self):
        for value in ("just some text", "", "https://example.com/x"):
            self.app._last_clipboard = None
            self._feed_clipboard(value)
            self.assertFalse(self.app.clip_bar.winfo_manager())

    def test_same_text_twice_is_not_re_suggested(self):
        self._set_action("suggest")
        self._feed_clipboard(YOUTUBE_URL)
        self.app._hide_clipboard_suggestion()
        self.app._check_clipboard()          # 同样的剪贴板内容
        self.assertFalse(self.app.clip_bar.winfo_manager())

    # ---------- 不支持的平台要说明白 ----------

    WECHAT = "https://weixin.qq.com/sph/AhMkUsjhOF"
    TIKTOK = "https://www.tiktok.com/@a/video/1"

    def _status_text(self):
        """状态文字写在**当前面板**上（_set_status_anywhere 的落点）。"""
        return self.app.pages["youtube"].video_panel._status_full[0]

    def test_unsupported_platform_is_named(self):
        """以前这里完全静默，用户粘了链接却毫无反应。"""
        self._feed_clipboard(self.WECHAT)
        text = self._status_text()
        self.assertIn(i18n.tr("platform_wechat_channels"), text)
        self.assertIn(i18n.tr("unsupported_platform",
                              platform=i18n.tr("platform_wechat_channels")), text)
        self.assertNotIn(i18n.tr("clipboard_no_link"), text,
                         "明明有链接，不能说「没有可识别的链接」")

    def test_engine_supported_platform_gets_the_hint(self):
        """小红书/TikTok 的提取器 yt-dlp 自带，只是界面没做，提示要如实。"""
        self._feed_clipboard(self.TIKTOK)
        self.assertIn(i18n.tr("unsupported_platform_engine_hint"),
                      self._status_text())

    def test_engine_unsupported_platform_has_no_hint(self):
        self._feed_clipboard(self.WECHAT)
        self.assertNotIn(i18n.tr("unsupported_platform_engine_hint"),
                         self._status_text())

    def test_unknown_site_is_not_reported_as_missing_link(self):
        self._feed_clipboard("https://example.com/x")
        self.assertIn(i18n.tr("unsupported_link_other"), self._status_text())

    def test_really_missing_link_still_says_so(self):
        self.app._last_clipboard = None
        self.app.clipboard_get = lambda: "just some text"
        self.app._check_clipboard(force=True)
        self.assertIn(i18n.tr("clipboard_no_link"), self._status_text())

    def test_unsupported_is_reported_once_per_text(self):
        calls = []
        panel = self.app.pages["youtube"].video_panel
        original = panel._set_status
        panel._set_status = lambda text, **kw: calls.append(text)
        try:
            self._feed_clipboard(self.WECHAT)
            self._feed_clipboard(self.WECHAT)      # 同一段剪贴板内容
        finally:
            panel._set_status = original
        self.assertEqual(len(calls), 1, "同一段文本不该反复刷屏")

    def test_menubar_paste_names_the_platform(self):
        self.app._last_clipboard = None
        self.app.clipboard_get = lambda: self.WECHAT
        self.app._check_clipboard(force=True)
        text = self._status_text()
        self.assertIn(i18n.tr("platform_wechat_channels"), text)
        self.assertNotIn(i18n.tr("clipboard_no_link"), text)

    def test_accepting_an_unsupported_suggestion_explains(self):
        """点一下建议条却什么都不发生是最糟的反馈。"""
        self.app._clipboard_suggestion = self.WECHAT
        self.app._accept_clipboard_suggestion()
        self.assertIn(i18n.tr("platform_wechat_channels"), self._status_text())
        self.assertEqual(self.probes, [], "不该把不支持的链接塞给任何面板")

    # ---------- 接受 / 忽略 ----------

    def test_accept_fills_and_probes(self):
        self._set_action("suggest")
        self._feed_clipboard(YOUTUBE_URL)
        self.app._accept_clipboard_suggestion()
        self.assertEqual(self.app.current_page, "youtube")
        self.assertEqual(self.probes, [("youtube", YOUTUBE_URL)])
        self.assertFalse(self.app.clip_bar.winfo_manager())

    def test_dismiss_keeps_input_and_page(self):
        self._set_action("suggest")
        self._feed_clipboard(WEIBO_URL)
        self.app._dismiss_clipboard_suggestion()
        self.assertFalse(self.app.clip_bar.winfo_manager())
        self.assertEqual(self.app.current_page, "youtube")
        self.assertEqual(self.probes, [])

    def test_accept_without_suggestion_is_safe(self):
        self.app._hide_clipboard_suggestion()
        self.app._accept_clipboard_suggestion()      # 不应抛错
        self.assertEqual(self.probes, [])


class ClipboardSettingUiTests(unittest.TestCase):
    """设置页那一行的取值/持久化。

    整个类共享一个 App：本机实测**第一次** Tk 初始化要 2 秒起（环境繁忙时更久），
    每个用例建一次 App 会让这个文件从几秒涨到近三分钟，而且反复销毁根窗口时
    残留的后台体检线程会让 Tk 段错误。所有页面在 App 构造时就建好了，因此这里
    直接访问页面对象，不调 show_page（也顺带避免触发设置页的 Cookie 体检）。
    """

    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()
        cls.page = cls.app.pages["settings"]

    def setUp(self):
        self.saved = {}
        settings_mod.save = lambda data: self.saved.update(data)
        self.page.clipboard_var.set(self.page._clipboard_action_label("suggest"))

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def test_label_round_trip(self):
        for value in ("suggest", "auto", "off"):
            label = self.page._clipboard_action_label(value)
            self.assertEqual(self.page._clipboard_action_value(label), value)

    def test_unknown_label_falls_back_to_suggest(self):
        self.assertEqual(self.page._clipboard_action_value("不存在"), "suggest")

    def test_change_is_persisted(self):
        self.page.clipboard_var.set(self.page._clipboard_action_label("off"))
        self.app.update_idletasks()
        self.assertEqual(self.saved.get("clipboard_action"), "off")
        self.assertEqual(self.app.settings.get("clipboard_action"), "off")

    def test_leaving_suggest_hides_existing_bar(self):
        self.page.clipboard_var.set(self.page._clipboard_action_label("auto"))
        self.app.update_idletasks()
        self.assertFalse(self.app.clip_bar.winfo_manager())

    def test_options_are_localized(self):
        for value in ("suggest", "auto", "off"):
            label = self.page._clipboard_action_label(value)
            self.assertNotEqual(label, value, "选项文案应来自 i18n")
            self.assertTrue(label)


if __name__ == "__main__":
    unittest.main()
