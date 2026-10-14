"""通用面板的测试（目标第 2 项）。

以前只有六个平台有面板，粘 TikTok / 小红书 进来只能说一句「引擎支持、界面还没做」。
现在多了一个「其他站点」页：不针对任何站点，任何 yt-dlp 支持的链接都在这里下载。

两件事必须钉住：
- 通用键 ``generic`` 刻意**不在** ``platforms.PLATFORMS`` 里（那一组要与
  ``PLATFORM_LABEL_KEYS`` 逐项对应），因此身份测试要显式放行、标签要单独处理；
- 剪贴板与队列都要能路由到这一页——「引擎支持的站点」不再走那条「暂不支持」的提示。
"""

import unittest

from app import gui, i18n, platforms, settings as settings_mod

_ORIGINAL_SAVE = settings_mod.save
_APP = None


def _shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


TIKTOK = "https://www.tiktok.com/@someone/video/1234567890"
XHS = "https://www.xiaohongshu.com/explore/abcdef123456"
CHANNELS = "https://weixin.qq.com/sph/AhMkUsjhOF"
KUAISHOU = "https://www.kuaishou.com/short-video/abcdef"
YOUTUBE = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


class GenericKeyTests(unittest.TestCase):
    def test_generic_is_not_a_platform(self):
        """通用键不能混进 PLATFORMS：Cookie 覆盖行等映射靠它与标签逐项对齐。"""
        self.assertEqual(platforms.GENERIC_KEY, "generic")
        self.assertNotIn(platforms.GENERIC_KEY, platforms.PLATFORMS)
        self.assertTrue(platforms.GENERIC_KEY.isascii())

    def test_label_is_translated_not_raw(self):
        self.assertEqual(gui.platform_label(platforms.GENERIC_KEY),
                         i18n.tr("generic_label"))
        self.assertNotEqual(gui.platform_label(platforms.GENERIC_KEY), "generic")

    def test_engine_supported_sites_are_recognised(self):
        self.assertEqual(platforms.generic_site_label_key(TIKTOK), "platform_tiktok")
        self.assertEqual(platforms.generic_site_label_key(XHS), "platform_xiaohongshu")

    def test_sites_the_engine_cannot_do_are_not_routed(self):
        """视频号 / 快手连 yt-dlp 都没有提取器，不该被送进通用页。"""
        self.assertIsNone(platforms.generic_site_label_key(CHANNELS))
        self.assertIsNone(platforms.generic_site_label_key(KUAISHOU))

    def test_normal_platform_link_is_not_generic(self):
        self.assertIsNone(platforms.generic_site_label_key(YOUTUBE))
        self.assertIsNone(platforms.generic_site_label_key("https://example.com/nothing"))


class GenericPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def test_page_and_panel_exist(self):
        page = self.app.pages.get(platforms.GENERIC_KEY)
        self.assertIsNotNone(page, "通用页没建起来")
        self.assertEqual(page.video_panel.platform, platforms.GENERIC_KEY)
        self.assertIn(platforms.GENERIC_KEY, self.app.nav_items, "侧栏没有入口")

    def test_target_page_routes_three_kinds_of_links(self):
        self.assertEqual(self.app._target_page(YOUTUBE), "youtube")
        self.assertEqual(self.app._target_page(TIKTOK), platforms.GENERIC_KEY)
        self.assertEqual(self.app._target_page(XHS), platforms.GENERIC_KEY)
        self.assertIsNone(self.app._target_page(CHANNELS), "引擎也不支持的站点不该有去处")

    def test_probe_does_not_apply_the_strict_platform_regex(self):
        """通用页必须收得下任意链接：校验只交给引擎，不靠 URL_RE。"""
        page = self.app.pages[platforms.GENERIC_KEY]
        self.app.show_page(platforms.GENERIC_KEY)
        page.video_panel.url_entry.set(TIKTOK)
        self.assertEqual(page.video_panel.url_entry.get(), TIKTOK)


class ClipboardRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["clipboard_action"] = "suggest"
        self.app._last_clipboard = None
        self.app._hide_clipboard_suggestion()
        # 解析是后台线程：测试不等它结束，所以要手动把面板从 _busy 里放出来，
        # 否则 handle_url 会直接返回，下一个用例看到的是上一条链接
        for page in self.app.pages.values():
            panel = getattr(page, "video_panel", None)
            if panel is not None:
                panel._busy = False

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        self.app._hide_clipboard_suggestion()

    def _paste(self, text, force=False):
        self.app.clipboard_get = lambda: text
        self.app._check_clipboard(force=force)

    def test_engine_supported_link_is_offered_not_rejected(self):
        self._paste(TIKTOK)
        self.assertEqual(self.app._clipboard_suggestion, TIKTOK,
                         "TikTok 链接应当被提示，而不是报「暂不支持」")

    def test_accepting_the_suggestion_opens_the_generic_page(self):
        self._paste(TIKTOK)
        self.app._accept_clipboard_suggestion()
        self.assertEqual(self.app.current_page, platforms.GENERIC_KEY)
        self.assertEqual(self.app.pages[platforms.GENERIC_KEY].video_panel.url_entry.get(),
                         TIKTOK)

    def test_menubar_paste_goes_straight_to_the_generic_page(self):
        self._paste(XHS, force=True)
        self.assertEqual(self.app.current_page, platforms.GENERIC_KEY)
        self.assertEqual(self.app.pages[platforms.GENERIC_KEY].video_panel.url_entry.get(),
                         XHS)

    def test_engine_unsupported_link_still_reports_clearly(self):
        self._paste(CHANNELS)
        self.assertFalse(self.app._clipboard_suggestion,
                         "引擎也不支持的站点不该给建议条")
        # 状态文案落在当前可见面板上；先切页再粘一次，
        # 并清掉去重记录（同一段文本第二次默认不再提示，这是第 7 轮之前就有的行为）
        self.app.show_page("youtube")
        self.app._last_clipboard = None
        self._paste(CHANNELS)
        self.assertIn(i18n.tr("unsupported_platform",
                              platform=i18n.tr("platform_wechat_channels")),
                      self.app.pages["youtube"].video_panel._status_full)

    def test_message_for_engine_supported_sites_is_actionable(self):
        """措辞从「界面上还没做」改成「可以在其他站点页下载」。"""
        message = self.app._unsupported_message(TIKTOK)
        self.assertIn("其他站点", message, "要告诉用户去哪儿下载")
        self.assertNotIn("暂不支持", message,
                         "引擎支持的站点不该同时说「不支持」和「可以去这儿下」")
        self.assertNotIn("还没做", message)


class GenericQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.set_queue_items([])

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        self.app.set_queue_items([])

    def test_enqueue_keeps_the_panels_platform(self):
        """队列只存 URL/平台/类型；平台必须是页面键，否则回到队列时找不到去处。"""
        page = self.app.pages[platforms.GENERIC_KEY]
        page.video_panel.url_entry.set(TIKTOK)
        page.video_panel.on_enqueue()
        items = self.app.queue_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["platform"], platforms.GENERIC_KEY)
        self.assertIn(items[0]["platform"], self.app.pages,
                      "队列里的平台键必须能映射到页面")

    def test_batch_enqueue_also_keeps_it(self):
        page = self.app.pages[platforms.GENERIC_KEY]
        page.video_panel.url_entry.set(f"{TIKTOK}\n{XHS}")
        added, _skipped = self.app.enqueue_urls(
            gui.parse_links(page.video_panel.url_entry.get()),
            platform=platforms.GENERIC_KEY)
        self.assertEqual(added, 2)
        for item in self.app.queue_items():
            self.assertEqual(item["platform"], platforms.GENERIC_KEY)


if __name__ == "__main__":
    unittest.main()
