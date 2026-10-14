"""字幕/封面/元数据嵌入的测试（ROADMAP 第 10 项）。

第 10 项的由来：字幕只存成 `.srt` 旁挂文件、封面不保存、元数据不写，交给播放器
自己识别；而 mp3/m4a 这类音频嵌上封面与标题才真正好用。

这个文件锁住四件事：

1. **默认一个都不开**：这三个开关会改动产出的文件，不能悄悄变。
2. **开关到 yt-dlp 参数的映射**正确（尤其封面要同时 write+embed）。
3. **嵌入字幕会顺带打开字幕下载**：否则勾了嵌入却因为没勾「下载字幕」而毫无效果。
4. 未识别的键不能把下载整体弄崩（调用方传的是设置字典）。
"""

import tempfile
import unittest
from unittest import mock

from app import core, gui, settings as settings_mod

_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create
_APP = None
URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def _shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


class EmbedPostprocessorTests(unittest.TestCase):
    """嵌入必须靠**后处理器**实现，不是靠选项键。"""

    def _keys(self, **kwargs):
        return [post["key"] for post in core.embed_postprocessors(**kwargs)]

    def test_all_off_gives_nothing(self):
        self.assertEqual(core.embed_postprocessors(), [])

    def test_thumbnail_converts_before_embedding(self):
        """webp 封面嵌不进 mp3：必须先转成 jpg，实测就是这么静默失败的。"""
        keys = self._keys(thumbnail=True)
        self.assertEqual(keys, ["FFmpegThumbnailsConvertor", "EmbedThumbnail"])
        posts = core.embed_postprocessors(thumbnail=True)
        self.assertEqual(posts[0]["format"], "jpg")
        self.assertTrue(posts[1]["already_have_thumbnail"], "要保住那张封面文件")

    def test_metadata(self):
        keys = self._keys(metadata=True)
        self.assertEqual(keys, ["FFmpegMetadata"])

    def test_subtitles(self):
        keys = self._keys(subtitles=True)
        self.assertEqual(keys, ["FFmpegEmbedSubtitle"])

    def test_subtitle_file_is_deleted_unless_asked_for(self):
        """只勾「嵌入字幕」时产物应当干净；勾了「下载字幕」才保留 .srt。"""
        posts = core.embed_postprocessors(subtitles=True)
        self.assertFalse(posts[0]["already_have_subtitle"])
        kept = core.embed_postprocessors(subtitles=True, keep_subtitles=True)
        self.assertTrue(kept[0]["already_have_subtitle"])

    def test_order_matches_yt_dlp(self):
        """元数据要排在字幕之后、封面之前（照 CLI 的顺序，语义有依赖）。"""
        keys = self._keys(subtitles=True, metadata=True, thumbnail=True)
        self.assertEqual(keys, ["FFmpegThumbnailsConvertor", "FFmpegEmbedSubtitle",
                                "FFmpegMetadata", "EmbedThumbnail"])

    def test_unknown_keys_are_ignored(self):
        self.assertEqual(core.embed_postprocessors(quality="best"), [])


class _CaptureMixin:
    """借用真实实现里的 opts 组装，只把「跑下载」这一步替换掉。"""

    def _capture(self, func, **kwargs):
        captured = {}

        def fake_run(url, opts, **extra):
            captured.update(opts)
            return ["/tmp/out.mp4"] if func is core.download_playlist else "/tmp/out.mp4"

        with tempfile.TemporaryDirectory() as output_dir, mock.patch.object(
                core, "_run_download_with_cookie_fallback", fake_run):
            try:
                func(URL, save_dir=output_dir, **kwargs)
            except Exception:
                pass          # 返回值的形状不是本文件的关注点，opts 已经拿到
        return captured

    def _video_opts(self, **kwargs):
        return self._capture(core.download_video, **kwargs)

    def _audio_opts(self, **kwargs):
        return self._capture(core.download_audio, **kwargs)

    def _playlist_opts(self, **kwargs):
        return self._capture(core.download_playlist, **kwargs)


class EmbedEngineTests(_CaptureMixin, unittest.TestCase):
    def test_video_defaults_change_nothing(self):
        """默认路径必须和以前完全一样。"""
        opts = self._video_opts()
        self.assertNotIn("postprocessors", opts)
        self.assertNotIn("writethumbnail", opts)
        self.assertTrue(opts["no_warnings"], "没开嵌入时不必放警告出来")

    def test_useless_flag_keys_are_not_used(self):
        """回归护栏：addmetadata/embedsubtitles/embedthumbnail 这些键
        **只有 yt-dlp 的 CLI 层**会翻译成后处理器，走 Python API 传它们等于什么都没做
        （第一版就是这么写的，真机跑完才发现封面和元数据根本没进去）。"""
        opts = self._video_opts(embed={"subtitles": True, "thumbnail": True,
                                       "metadata": True})
        for key in ("addmetadata", "embedsubtitles", "embedthumbnail"):
            self.assertNotIn(key, opts, key)
        keys = [post["key"] for post in opts["postprocessors"]]
        self.assertIn("FFmpegMetadata", keys)
        self.assertIn("EmbedThumbnail", keys)
        self.assertIn("FFmpegEmbedSubtitle", keys)

    def test_embedding_installs_a_logger_and_lets_warnings_through(self):
        """后处理器会静默跳过；no_warnings 还开着的话 logger 根本收不到消息。"""
        opts = self._video_opts(embed={"metadata": True})
        self.assertFalse(opts["no_warnings"])
        logger = opts["logger"]
        self.assertTrue(callable(getattr(logger, "warning", None)))
        logger.warning("测试警告")          # 不应抛错

    def test_thumbnail_asks_for_the_file_too(self):
        opts = self._video_opts(embed={"thumbnail": True})
        self.assertTrue(opts["writethumbnail"],
                        "writethumbnail 是 Python API 认的键，用来写出封面")

    def test_embedding_subtitles_also_downloads_them(self):
        opts = self._video_opts(embed={"subtitles": True})
        self.assertTrue(opts["writesubtitles"])
        self.assertTrue(opts["writeautomaticsub"])
        self.assertEqual(opts["subtitleslangs"], list(core.DEFAULT_SUBTITLE_LANGS))

    def test_plain_subtitles_do_not_embed(self):
        opts = self._video_opts(subtitles=True, subtitle_langs="en")
        self.assertTrue(opts["writesubtitles"])
        self.assertNotIn("postprocessors", opts, "老的下载字幕行为不能被改成嵌入")
        self.assertTrue(opts["no_warnings"])

    def test_subtitle_languages_are_respected_when_embedding(self):
        opts = self._video_opts(embed={"subtitles": True}, subtitle_langs="ja,en")
        self.assertEqual(opts["subtitleslangs"], ["ja", "en"])

    def test_audio_embeds_after_extraction(self):
        """顺序：提取音频 → 写元数据 → 嵌封面。提取前嵌会被丢掉。"""
        opts = self._audio_opts(embed={"thumbnail": True, "metadata": True})
        keys = [post["key"] for post in opts["postprocessors"]]
        self.assertEqual(keys[0], "FFmpegExtractAudio")
        self.assertLess(keys.index("FFmpegMetadata"), keys.index("EmbedThumbnail"))
        self.assertFalse(opts["no_warnings"])

    def test_audio_defaults_change_nothing(self):
        opts = self._audio_opts()
        keys = [post["key"] for post in opts["postprocessors"]]
        self.assertEqual(keys, ["FFmpegExtractAudio"])

    def test_playlist_can_embed(self):
        opts = self._playlist_opts(embed={"metadata": True})
        self.assertIn("FFmpegMetadata",
                      [post["key"] for post in opts["postprocessors"]])

    def test_unknown_embed_keys_do_not_break_the_download(self):
        opts = self._video_opts(embed={"metadata": True, "something": True})
        self.assertIn("FFmpegMetadata",
                      [post["key"] for post in opts["postprocessors"]])


class EmbedSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.events = []
        self._log_event = gui.applog.log_event
        gui.applog.log_event = lambda name, **fields: self.events.append(
            (name, fields))
        for key in ("embed_subtitles", "embed_thumbnail", "embed_metadata"):
            self.app.settings[key] = False
        self.app.show_page("settings")
        self.app.update_idletasks()
        self.page = self.app.pages["settings"]

    def tearDown(self):
        gui.applog.log_event = self._log_event
        for key in ("embed_subtitles", "embed_thumbnail", "embed_metadata"):
            self.app.settings[key] = False
        settings_mod.save = _ORIGINAL_SAVE

    def test_defaults_are_all_off(self):
        self.assertEqual(self.app.embed_settings(),
                         {"subtitles": False, "thumbnail": False,
                          "metadata": False})

    def test_helper_reads_the_settings(self):
        self.app.settings["embed_thumbnail"] = True
        self.assertEqual(self.app.embed_settings()["thumbnail"], True)
        self.assertEqual(self.app.embed_settings()["subtitles"], False)

    def test_card_has_three_switches_with_the_stored_values(self):
        self.assertEqual(set(self.page.embed_vars), {"embed_subtitles",
                                                     "embed_thumbnail",
                                                     "embed_metadata"})
        for variable in self.page.embed_vars.values():
            self.assertFalse(bool(variable.get()))

    def test_flipping_a_switch_persists_and_logs(self):
        saved = []
        settings_mod.save = lambda data: saved.append(dict(data))
        self.page.embed_vars["embed_metadata"].set(True)
        self.app.update_idletasks()
        self.assertTrue(self.app.settings["embed_metadata"])
        self.assertTrue(saved[-1]["embed_metadata"])
        self.assertIn("embed_setting", [name for name, _ in self.events])
        self.assertEqual(self.app.embed_settings()["metadata"], True)

    def test_flipping_back_also_persists(self):
        self.page.embed_vars["embed_subtitles"].set(True)
        self.app.update_idletasks()
        self.page.embed_vars["embed_subtitles"].set(False)
        self.app.update_idletasks()
        self.assertFalse(self.app.settings["embed_subtitles"])


if __name__ == "__main__":
    unittest.main()
