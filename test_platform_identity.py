"""平台身份标识的统一性（技术债清理后的守卫）。

面板曾用**显示名**（"YouTube"/"微博"/"哔哩哔哩"）当内部标识，而引擎、Cookie 覆盖、
保存目录键都用 ASCII 键（`platforms.PLATFORMS`）。两个体系并存时：
- 「禁止硬编码中文」的检查不得不为 gui.py 开白名单；
- 任何一处把显示名传给按 ASCII 键查表的代码（如 platform_override）都会静默不匹配。

现在身份统一为 ASCII 键，显示时才翻成当前语言的标签。这个文件把几件关键不变量锁住：
面板身份必须是 ASCII 键、保存目录键不能变（它是用户设置里的既有键）、以及页面
显示的是本地化标签而不是裸键。
"""

import unittest

from app import gui, i18n, platforms, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

# 用户 settings.json 里既有的保存目录键就长这样，改名会让用户的自定义目录失效
HISTORICAL_DIR_KEYS = {
    ("youtube", "video"): "youtube_video",
    ("youtube", "audio"): "youtube_audio",
    ("x", "video"): "x_video",
    ("x", "image"): "x_image",
    ("instagram", "video"): "instagram_video",
    ("douyin", "media"): "douyin_media",
    ("bilibili", "video"): "bilibili_video",
    ("weibo", "media"): "weibo_media",
    ("weibo", "image"): "weibo_image",
}

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


class PlatformIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def test_every_panel_uses_an_ascii_platform_key(self):
        for key, page in self.app.pages.items():
            for panel in (getattr(page, "video_panel", None),
                          getattr(page, "image_panel", None)):
                if panel is None:
                    continue
                # 通用面板用 platforms.GENERIC_KEY：它刻意不在 PLATFORMS 里
                #（那一组是「有专属面板」的平台，要与 PLATFORM_LABEL_KEYS 逐项对应）
                allowed = platforms.PLATFORMS + (platforms.GENERIC_KEY,)
                self.assertIn(panel.platform, allowed,
                              f"{key} 的面板身份 {panel.platform!r} 不是 ASCII 平台键")
                self.assertTrue(panel.platform.isascii())

    def test_panel_identity_matches_its_page_key(self):
        """页面键与面板身份应当一致，避免两套名字各说各话。"""
        for key, page in self.app.pages.items():
            panel = getattr(page, "video_panel", None)
            if panel is None:
                continue
            self.assertEqual(panel.platform, key)

    def test_label_keys_are_aligned_with_platforms(self):
        self.assertEqual(len(gui.PLATFORM_LABEL_KEYS), len(platforms.PLATFORMS))
        for key, label_key in zip(platforms.PLATFORMS, gui.PLATFORM_LABEL_KEYS):
            self.assertNotEqual(i18n.tr(label_key), label_key,
                                f"{key} 的标签键 {label_key} 没有翻译")

    def test_platform_label_is_localized(self):
        original = i18n.language()
        try:
            i18n.set_language("zh_CN")
            chinese = gui.platform_label("youtube")
            i18n.set_language("en")
            english = gui.platform_label("youtube")
        finally:
            i18n.set_language(original)
        self.assertTrue(chinese and english)
        self.assertNotEqual(chinese, "youtube")
        self.assertNotEqual(english, "youtube")

    def test_platform_label_passes_unknown_values_through(self):
        for value in ("", None, "没有这个平台", "unknown"):
            self.assertEqual(gui.platform_label(value), value or "")

    def test_platform_label_accepts_uppercase_keys(self):
        self.assertEqual(gui.platform_label("YOUTUBE"), gui.platform_label("youtube"))


class DownloadDirKeyTests(unittest.TestCase):
    """保存目录键是用户设置里的既有数据，绝不能改。"""

    def test_historical_keys_are_preserved(self):
        for (platform, kind), expected in HISTORICAL_DIR_KEYS.items():
            self.assertEqual(gui.download_dir_key(platform, kind), expected)

    def test_legacy_display_names_lowercase_to_the_same_keys(self):
        """旧调用点传过 ASCII 显示名（"YouTube"），结果必须一致。"""
        self.assertEqual(gui.download_dir_key("YouTube", "audio"), "youtube_audio")
        self.assertEqual(gui.download_dir_key("X", "image"), "x_image")
        self.assertEqual(gui.download_dir_key("Instagram", "video"), "instagram_video")

    def test_non_ascii_names_do_not_leak_into_filenames(self):
        """中文名不会再出现（调用点已改），真出现时也不能让它变成目录键。"""
        self.assertEqual(gui.download_dir_key("微博", "media"), "unknown_media")

    def test_empty_platform_is_handled(self):
        for value in ("", None):
            self.assertEqual(gui.download_dir_key(value, "video"), "unknown_video")

    def test_panel_save_dir_keys_are_unchanged(self):
        """端到端：面板算出来的键要落在历史键集合里。"""
        video = self.app_panel("weibo", "video_panel")
        image = self.app_panel("weibo", "image_panel")
        self.assertEqual(video._save_dir_key(), "weibo_media")
        self.assertEqual(image._save_dir_key(), "weibo_media")
        self.assertEqual(self.app_panel("youtube", "video_panel")._save_dir_key(),
                         "youtube_video")

    def app_panel(self, key, attr):
        return getattr(_shared_app().pages[key], attr)


class StoredPlatformDisplayTests(unittest.TestCase):
    """历史/队列页要把存下来的 ASCII 键翻成标签，而不是显示裸键。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["download_history"] = []
        self.app.settings["download_queue"] = []

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def _all_texts(self, widget):
        found = []
        for child in widget.winfo_children():
            try:
                if "text" in child.keys():
                    found.append(str(child.cget("text")))
            except Exception:
                pass
            found.extend(self._all_texts(child))
        return found

    def test_history_row_shows_a_localized_label(self):
        self.app.record_download(["/tmp/a.mp4"], title="t", platform="weibo")
        page = self.app.pages["history"]
        page.refresh()
        self.app.update_idletasks()
        joined = "\n".join(self._all_texts(page.rows))
        self.assertIn(gui.platform_label("weibo"), joined)
        self.assertNotIn("weibo", joined.replace(gui.platform_label("weibo"), ""))

    def test_queue_row_shows_a_localized_label(self):
        self.app.enqueue_url("https://www.youtube.com/watch?v=aqz-KE-bpKQ")
        page = self.app.pages["queue"]
        page.refresh()
        self.app.update_idletasks()
        joined = "\n".join(self._all_texts(page.rows))
        self.assertIn(gui.platform_label("youtube"), joined)


if __name__ == "__main__":
    unittest.main()
