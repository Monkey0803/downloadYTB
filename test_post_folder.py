"""「每个帖子单独建子文件夹」的测试。

需求来自微博动图帖：一个帖子可能同时有 GIF 和 MP4，用户希望这一帖的文件单独放一处。
设计取舍：
- **默认关闭**，开启后既有目录结构会变，所以默认行为必须逐字节不变（本文件有守卫）。
- 目录名 = ``<帖子 ID>_<标题摘要>``，只用链接与标题推导，同一帖子每次结果一致
  （图片流程与视频流程要算到同一个目录；重复检测也依赖它稳定）。
- 只有能识别出帖子 ID 的链接才分组：YouTube 单条视频、B 站等不受影响。
"""

import os
import shutil
import tempfile
import unittest

from app import core, gui, i18n, images, settings as settings_mod

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
        """同一帖子的图片与视频流程要落在同一个帖子目录下。

        视频面板自己多带一层媒体类型目录（video），图片流程的类型目录由引擎按条目
        算——两者的「帖子目录」必须一致，且视频文件要落在同一个 video 桶里。
        """
        image_panel = self.app.pages["weibo"].image_panel
        video_panel = self.app.pages["weibo"].video_panel
        image_panel._post = images.ImagePost(url=WEIBO, title=WEIBO_TITLE, author="",
                                             post_id="Rmr4XxLMD")
        video_panel._info = core.VideoInfo(url=WEIBO, title=WEIBO_TITLE, duration=10.0,
                                          uploader="")
        self.app.settings["group_by_post"] = True
        post_dir = image_panel._download_dir()
        self.assertEqual(video_panel._download_dir(), os.path.join(post_dir, "video"))
        # 图片流程里那条 MP4（动图的视频版本）也会进 video 桶，两者同处一地
        self.assertEqual(images.media_bucket(_item(1, "mp4", "video")),
                         os.path.basename(video_panel._download_dir()))

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


class _FakeResponse:
    def __init__(self, content):
        self.content = content
        self.headers = {"Content-Length": str(len(content))}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeRequests:
    """替换 images.requests，避免联网。"""

    class RequestException(Exception):
        pass

    def __init__(self):
        self.requests = []

    def Session(self):
        outer = self

        class _Session:
            headers = {}

            def get(self, url, **kwargs):
                outer.requests.append(url)
                return _FakeResponse(b"payload:" + url.encode())

        return _Session()

    def __enter__(self):
        self.original = images.requests
        images.requests = self
        return self

    def __exit__(self, *exc_info):
        images.requests = self.original
        return False


def _item(index, extension, media_type="image"):
    return images.ImageItem(url=f"https://example.com/{index}", thumb_url="",
                            index=index, extension=extension,
                            filename=f"Rmr4XxLMD_{index}", media_type=media_type)


class MediaBucketTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(images.media_bucket(_item(1, "jpg")), "image")
        self.assertEqual(images.media_bucket(_item(2, "png")), "image")
        self.assertEqual(images.media_bucket(_item(3, "gif")), "gif")
        self.assertEqual(images.media_bucket(_item(4, "mp4", "video")), "video")
        self.assertEqual(images.media_bucket(_item(5, "m4s", "video")), "video")

    def test_gif_extension_is_case_insensitive(self):
        self.assertEqual(images.media_bucket(_item(1, "GIF")), "gif")

    def test_bucket_names_do_not_follow_the_ui_language(self):
        """目录名必须与语言无关：否则切语言后路径和下载历史都会漂移。"""
        original = i18n.language()
        try:
            i18n.set_language("zh_CN")
            chinese = [images.media_bucket(_item(i, ext, mt)) for i, (ext, mt) in
                       enumerate([("jpg", "image"), ("gif", "image"), ("mp4", "video")], 1)]
            i18n.set_language("en")
            english = [images.media_bucket(_item(i, ext, mt)) for i, (ext, mt) in
                       enumerate([("jpg", "image"), ("gif", "image"), ("mp4", "video")], 1)]
        finally:
            i18n.set_language(original)
        self.assertEqual(chinese, english)
        for bucket in chinese:
            self.assertTrue(bucket.isascii())


class TypeFolderDownloadTests(unittest.TestCase):
    """引擎确实把条目写进各自的类型子目录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="post_folder_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _items(self):
        return [_item(1, "jpg"), _item(2, "gif"), _item(3, "mp4", "video")]

    def test_grouped_layout(self):
        with _FakeRequests():
            result = images.download_images(self._items(), save_dir=self.tmp,
                                            skip_existing=False, group_by_type=True)
        dirs = sorted(os.path.relpath(os.path.dirname(path), self.tmp)
                      for path in result.paths)
        self.assertEqual(dirs, ["gif", "image", "video"])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "gif")))

    def test_flat_by_default(self):
        """不开分组时一个子目录都不该出现（默认行为逐字节不变）。"""
        with _FakeRequests():
            result = images.download_images(self._items(), save_dir=self.tmp,
                                            skip_existing=False)
        for path in result.paths:
            self.assertEqual(os.path.dirname(path), self.tmp)
        self.assertEqual(sorted(os.listdir(self.tmp)),
                         ["Rmr4XxLMD_1.jpg", "Rmr4XxLMD_2.gif", "Rmr4XxLMD_3.mp4"])

    def test_skip_existing_works_inside_each_type_folder(self):
        """重复检测要在各自类型目录里生效，否则重复下载会复活。"""
        with _FakeRequests() as fake:
            first = images.download_images(self._items(), save_dir=self.tmp,
                                          skip_existing=True, group_by_type=True)
            second = images.download_images(self._items(), save_dir=self.tmp,
                                           skip_existing=True, group_by_type=True)
            second_run_requests = len(fake.requests) - 3
        self.assertEqual(len(first.paths), 3)
        self.assertEqual(second.paths, [])
        self.assertEqual(sorted(second.skipped), sorted(first.paths))
        self.assertEqual(second_run_requests, 0, "已存在的文件不该再发请求")


class TypeFolderPanelTests(unittest.TestCase):
    """面板：目录与分类型开关的配合。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["group_by_post"] = False

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        self.app.settings["group_by_post"] = False

    def _weibo_post(self):
        return images.ImagePost(url=WEIBO, title=WEIBO_TITLE, author="",
                                post_id="Rmr4XxLMD")

    def test_video_panel_puts_video_in_the_video_folder(self):
        panel = self.app.pages["weibo"].video_panel
        panel._info = core.VideoInfo(url=WEIBO, title=WEIBO_TITLE, duration=9.0,
                                     uploader="")
        self.app.settings["group_by_post"] = True
        self.assertEqual(os.path.basename(panel._download_dir()), "video")

    def test_video_panel_audio_mode_uses_the_audio_folder(self):
        panel = self.app.pages["youtube"].video_panel
        panel._info = core.VideoInfo(url="https://x.com/a/status/123", title="t",
                                     duration=9.0, uploader="")
        panel.mode_var.set("audio")
        self.app.settings["group_by_post"] = True
        try:
            self.assertEqual(os.path.basename(panel._download_dir()), "audio")
        finally:
            panel.mode_var.set("video")

    def test_image_panel_groups_types_only_when_a_post_folder_is_used(self):
        panel = self.app.pages["weibo"].image_panel
        panel._post = self._weibo_post()
        self.assertFalse(panel._group_by_type(), "默认关闭时不该分类型")
        self.app.settings["group_by_post"] = True
        self.assertTrue(panel._group_by_type())
        # 认不出帖子 ID 的链接：即使开关打开也保持平铺
        panel._post = images.ImagePost(
            url="https://www.youtube.com/watch?v=aqz-KE-bpKQ", title="t", author="")
        self.assertFalse(panel._group_by_type())

    def test_post_dir_without_kind_has_no_type_folder(self):
        """App 层的 kind 是可选的：不传就不分类型（图片面板交给引擎按条目分）。"""
        self.app.settings["group_by_post"] = True
        base = "/tmp/下载测试"
        path = self.app._post_download_dir(base, WEIBO, WEIBO_TITLE)
        self.assertEqual(os.path.dirname(path), base)
        with_kind = self.app._post_download_dir(base, WEIBO, WEIBO_TITLE, kind="video")
        self.assertEqual(with_kind, os.path.join(path, "video"))


if __name__ == "__main__":
    unittest.main()
