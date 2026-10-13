"""「每个帖子单独建子文件夹」的测试。

需求来自微博动图帖：一个帖子可能同时有 GIF 和 MP4，用户希望这一帖的文件单独放一处。
设计取舍：
- **默认关闭**，开启后既有目录结构会变，所以默认行为必须逐字节不变（本文件有守卫）。
- 目录名 = ``<帖子 ID>_<标题摘要>``，只用链接与标题推导，同一帖子每次结果一致
  （图片流程与视频流程要算到同一个目录；重复检测也依赖它稳定）。
- 只有能识别出帖子 ID 的链接才分组：YouTube 单条视频、B 站等不受影响。
"""

import os
import unittest

from app import core, gui, images, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

WEIBO = "https://weibo.com/2549621477/Rmr4XxLMD"
WEIBO_TITLE = "一组动图警示你: 行人提高安全意识，不要蹲坐在车辆盲区"

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


class PostIdTests(unittest.TestCase):
    def test_weibo_forms(self):
        for url in ("https://weibo.com/2549621477/Rmr4XxLMD",
                    "https://m.weibo.cn/status/Rmr4XxLMD",
                    "https://weibo.com/detail/Rmr4XxLMD",
                    "https://m.weibo.cn/detail/Rmr4XxLMD"):
            self.assertEqual(images.post_id_from_url(url), "Rmr4XxLMD", url)

    def test_x_and_instagram(self):
        self.assertEqual(
            images.post_id_from_url("https://x.com/someone/status/1234567890"),
            "1234567890")
        self.assertEqual(
            images.post_id_from_url("https://twitter.com/someone/status/42"), "42")
        self.assertEqual(
            images.post_id_from_url("https://www.instagram.com/p/ABC_def-1/"),
            "ABC_def-1")
        self.assertEqual(
            images.post_id_from_url("https://www.instagram.com/reel/XYZ123/"), "XYZ123")

    def test_platforms_without_a_post_id(self):
        for url in ("https://www.youtube.com/watch?v=aqz-KE-bpKQ",
                    "https://www.bilibili.com/video/BV1xx411c7mD",
                    "https://www.youtube.com/playlist?list=PL123",
                    "", None, "随便一段文字"):
            self.assertEqual(images.post_id_from_url(url), "", repr(url))


class PostFolderNameTests(unittest.TestCase):
    def test_id_and_title(self):
        """标题会先做安全清洗（冒号等会被替换），所以按清洗后的结果比对。"""
        cleaned = gui._sanitize_filename(WEIBO_TITLE)[:40]
        self.assertEqual(gui.post_folder_name("Rmr4XxLMD", WEIBO_TITLE),
                         "Rmr4XxLMD_" + cleaned)

    def test_id_only(self):
        self.assertEqual(gui.post_folder_name("Rmr4XxLMD"), "Rmr4XxLMD")
        self.assertEqual(gui.post_folder_name("Rmr4XxLMD", "   "), "Rmr4XxLMD")

    def test_without_an_id_there_is_no_folder(self):
        for value in ("", None, "  ", "///", "::"):
            self.assertEqual(gui.post_folder_name(value, "标题"), "")

    def test_unsafe_characters_never_reach_the_path(self):
        name = gui.post_folder_name("ABC", 'a/b\\c:d*e?f"g<h>i|j\nk\tl')
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        for char in ':*?"<>|\n\t':
            self.assertNotIn(char, name)

    def test_long_titles_are_truncated(self):
        name = gui.post_folder_name("ABC", "长" * 200)
        self.assertLessEqual(len(name), 4 + 40)

    def test_title_of_only_punctuation_falls_back_to_the_id(self):
        self.assertEqual(gui.post_folder_name("ABC", " ... __ "), "ABC")

    def test_same_post_always_gets_the_same_folder(self):
        first = gui.post_folder_name("Rmr4XxLMD", WEIBO_TITLE)
        second = gui.post_folder_name("Rmr4XxLMD", WEIBO_TITLE)
        self.assertEqual(first, second,
                         "目录名必须稳定，否则重复检测会失效、文件会重复下载")


class PostDownloadDirTests(unittest.TestCase):
    """App._post_download_dir：开关语义。"""

    def setUp(self):
        self.app = _shared_app()
        settings_mod.save = lambda data: None
        self.base = "/tmp/下载测试"

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        self.app.settings["group_by_post"] = False

    def test_default_is_off(self):
        self.app.settings.pop("group_by_post", None)
        self.assertFalse(self.app.group_by_post_enabled())

    def test_off_keeps_the_base_directory(self):
        """默认关闭时路径必须逐字节不变（既有用户目录不受影响）。"""
        self.app.settings["group_by_post"] = False
        self.assertEqual(self.app._post_download_dir(self.base, WEIBO, WEIBO_TITLE),
                         self.base)

    def test_on_appends_the_post_folder(self):
        self.app.settings["group_by_post"] = True
        expected = os.path.join(self.base, gui.post_folder_name("Rmr4XxLMD", WEIBO_TITLE))
        self.assertEqual(self.app._post_download_dir(self.base, WEIBO, WEIBO_TITLE),
                         expected)

    def test_on_but_no_post_id_stays_flat(self):
        self.app.settings["group_by_post"] = True
        youtube = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
        self.assertEqual(self.app._post_download_dir(self.base, youtube, "标题"),
                         self.base, "单条视频不该被塞进子文件夹")

    def test_on_with_a_weird_title_still_yields_a_usable_path(self):
        self.app.settings["group_by_post"] = True
        path = self.app._post_download_dir(self.base, WEIBO, 'a/b:c*d?e"f')
        self.assertTrue(path.startswith(self.base + os.sep))
        self.assertEqual(len(path.split(os.sep)), len(self.base.split(os.sep)) + 1)


class PanelDownloadDirTests(unittest.TestCase):
    """两个面板实际算出来的目录（真实面板、真实 post/info 对象）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["group_by_post"] = False

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        self.app.settings["group_by_post"] = False

    def test_image_panel_groups_a_weibo_post(self):
        panel = self.app.pages["weibo"].image_panel
        panel._post = images.ImagePost(url=WEIBO, title=WEIBO_TITLE, author="",
                                      post_id="Rmr4XxLMD")
        base = panel._save_dir_key()
        off = panel._download_dir()
        self.app.settings["group_by_post"] = True
        on = panel._download_dir()
        self.assertEqual(os.path.dirname(on), self.app.default_save_dir(base))
        self.assertEqual(os.path.basename(on), gui.post_folder_name("Rmr4XxLMD",
                                                                     WEIBO_TITLE))
        self.assertNotEqual(off, on)

    def test_image_panel_falls_back_to_the_link_for_the_id(self):
        """post_id 缺失（老路径）时也要能从链接推出 ID。"""
        panel = self.app.pages["weibo"].image_panel
        panel._post = images.ImagePost(url=WEIBO, title="标题", author="")
        self.app.settings["group_by_post"] = True
        self.assertTrue(os.path.basename(panel._download_dir()).startswith("Rmr4XxLMD"))

    def test_weibo_video_panel_groups_the_same_way(self):
        """同一帖子的图片与视频流程要落到同一个目录。"""
        image_panel = self.app.pages["weibo"].image_panel
        video_panel = self.app.pages["weibo"].video_panel
        image_panel._post = images.ImagePost(url=WEIBO, title=WEIBO_TITLE, author="",
                                             post_id="Rmr4XxLMD")
        video_panel._info = core.VideoInfo(url=WEIBO, title=WEIBO_TITLE, duration=10.0,
                                          uploader="")
        self.app.settings["group_by_post"] = True
        self.assertEqual(video_panel._download_dir(), image_panel._download_dir())

    def test_youtube_panel_is_unaffected_even_when_on(self):
        panel = self.app.pages["youtube"].video_panel
        panel._info = core.VideoInfo(url="https://www.youtube.com/watch?v=aqz-KE-bpKQ",
                                     title="某视频", duration=10.0, uploader="")
        self.app.settings["group_by_post"] = True
        self.assertEqual(panel._download_dir(),
                         self.app.default_save_dir(panel._save_dir_key()))

    def test_off_keeps_both_panels_on_the_base_dir(self):
        image_panel = self.app.pages["weibo"].image_panel
        image_panel._post = images.ImagePost(url=WEIBO, title=WEIBO_TITLE, author="",
                                             post_id="Rmr4XxLMD")
        panel = self.app.pages["youtube"].video_panel
        panel._info = core.VideoInfo(url="https://www.youtube.com/watch?v=x", title="t",
                                     duration=1.0, uploader="")
        self.assertEqual(image_panel._download_dir(),
                         self.app.default_save_dir(image_panel._save_dir_key()))
        self.assertEqual(panel._download_dir(),
                         self.app.default_save_dir(panel._save_dir_key()))


class PostFolderSettingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        self.saved = []
        settings_mod.save = lambda data: self.saved.append(dict(data))
        self.app.settings["group_by_post"] = False
        self.app.show_page("settings")
        self.app.update_idletasks()
        self.page = self.app.pages["settings"]

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        self.app.settings["group_by_post"] = False

    def test_checkbox_agrees_with_the_stored_value(self):
        """设置页的勾选状态要和实际生效值一致（页面是单例，不会重建）。"""
        self.assertEqual(bool(self.page.group_post_var.get()),
                         self.app.group_by_post_enabled())
        self.page.group_post_var.set(True)
        self.app.update_idletasks()
        self.assertTrue(self.app.group_by_post_enabled())

    def test_toggling_persists(self):
        self.page.group_post_var.set(True)
        self.app.update_idletasks()
        self.assertTrue(self.app.settings["group_by_post"])
        self.assertTrue(self.saved[-1]["group_by_post"])


if __name__ == "__main__":
    unittest.main()
