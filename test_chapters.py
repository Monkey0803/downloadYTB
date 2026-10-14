"""章节切分 / SponsorBlock / 限速的测试（ROADMAP 第 15 项）。

这一项和第 10 项踩的是同一个坑：**yt-dlp 的选项键分两半**。
- ``ratelimit`` / ``concurrent_fragment_downloads`` 是真正的 API 参数（下载器直接读 params）；
- ``split_chapters`` / ``sponsorblock_mark`` / ``sponsorblock_remove`` **只出现在 options.py**，
  只有命令行层会翻译，走 Python API 传它们什么都不会发生。

所以这里既验「后处理器列表对不对、顺序对不对」，也留一条回归护栏钉住「不许再用那些
无效键」。顺序也不是抄来的：CLI 明确写了 ModifyChapters 必须在 FFmpegMetadataPP 之前。
"""

import os
import tempfile
import types
import unittest
from unittest import mock

from app import core, ffmpeg_util, gui, i18n, settings as settings_mod, settingspage

URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


class CategoryTests(unittest.TestCase):
    def test_empty_means_the_builtin_segments(self):
        self.assertEqual(core.sponsorblock_categories(None),
                         list(core.SPONSORBLOCK_SEGMENT_CATEGORIES))
        self.assertEqual(core.sponsorblock_categories("   "),
                         list(core.SPONSORBLOCK_SEGMENT_CATEGORIES))

    def test_parses_a_comma_list_and_drops_unknown(self):
        self.assertEqual(core.sponsorblock_categories("sponsor, intro , 不认识"),
                         ["sponsor", "intro"])
        self.assertEqual(core.sponsorblock_categories(["Sponsor", "OUTRO"]),
                         ["sponsor", "outro"])

    def test_duplicates_collapse_in_order(self):
        self.assertEqual(core.sponsorblock_categories("outro,sponsor,outro"),
                         ["outro", "sponsor"])

    def test_poi_categories_are_only_allowed_for_marking(self):
        """poi_highlight / chapter 是时间点，没有可剪的区间。"""
        self.assertNotIn("poi_highlight",
                         core.sponsorblock_categories("poi_highlight", mode="remove"))
        self.assertEqual(core.sponsorblock_categories("poi_highlight", mode="mark"),
                         ["poi_highlight"])

    def test_all_builtin_categories_exist_in_yt_dlp(self):
        """分类名必须与 yt-dlp 一致，否则会被我们悄悄丢掉。"""
        from yt_dlp.postprocessor import SponsorBlockPP

        known = set(SponsorBlockPP.CATEGORIES)
        for name in (core.SPONSORBLOCK_SEGMENT_CATEGORIES
                     + core.SPONSORBLOCK_POI_CATEGORIES):
            self.assertIn(name, known, f"yt-dlp 不认识分类 {name}")

    def test_unknown_only_input_falls_back_to_defaults(self):
        self.assertEqual(core.sponsorblock_categories("nothing_here"),
                         list(core.SPONSORBLOCK_SEGMENT_CATEGORIES))


class PostprocessorOrderTests(unittest.TestCase):
    def _keys(self, **kwargs):
        return [post["key"] for post in core.media_postprocessors(**kwargs)]

    def test_all_off_produces_nothing(self):
        self.assertEqual(core.media_postprocessors(), [])

    def test_mark_adds_sponsorblock_and_chapters_metadata(self):
        keys = self._keys(extras={"sponsorblock": "mark"})
        self.assertEqual(keys, ["SponsorBlock", "FFmpegMetadata"])
        posts = core.media_postprocessors(extras={"sponsorblock": "mark"})
        self.assertTrue(posts[1]["add_chapters"], "标记模式必须让元数据写章节")
        self.assertFalse(posts[1]["add_metadata"], "没勾元数据就不该顺手写元数据")

    def test_remove_cuts_segments(self):
        posts = core.media_postprocessors(extras={"sponsorblock": "remove"})
        self.assertEqual([p["key"] for p in posts], ["SponsorBlock", "ModifyChapters"])
        self.assertEqual(posts[1]["remove_sponsor_segments"],
                         list(core.SPONSORBLOCK_SEGMENT_CATEGORIES))

    def test_cutting_runs_before_metadata(self):
        """CLI 的原话：ModifyChapters must run before FFmpegMetadataPP。

        反过来的话，元数据先写进带广告的那一版，剪切之后章节信息就对不上了。
        """
        keys = self._keys(extras={"sponsorblock": "remove"}, embed={"metadata": True})
        self.assertLess(keys.index("ModifyChapters"), keys.index("FFmpegMetadata"))

    def test_split_chapters_runs_last(self):
        keys = self._keys(extras={"split_chapters": True, "sponsorblock": "remove"},
                          embed={"metadata": True, "thumbnail": True})
        self.assertEqual(keys[-1], "FFmpegSplitChapters")
        self.assertLess(keys.index("FFmpegMetadata"), keys.index("FFmpegSplitChapters"))

    def test_unknown_mode_is_treated_as_off(self):
        self.assertEqual(core.media_postprocessors(extras={"sponsorblock": "erase"}), [])

    def test_sponsorblock_query_waits_for_filtering(self):
        posts = core.media_postprocessors(extras={"sponsorblock": "mark"})
        self.assertEqual(posts[0]["when"], "after_filter")

    def test_embed_only_behaviour_is_unchanged(self):
        keys = self._keys(embed={"subtitles": True, "thumbnail": True, "metadata": True},
                          subtitles=True)
        self.assertEqual(keys, ["FFmpegThumbnailsConvertor", "FFmpegEmbedSubtitle",
                                "FFmpegMetadata", "EmbedThumbnail"])


class DownloadLimitsTests(unittest.TestCase):
    def setUp(self):
        self._saved = core.download_limits()
        core.set_download_limits(0, 1)

    def tearDown(self):
        core.set_download_limits(
            (self._saved["ratelimit"] or 0) // 1024, self._saved["fragments"] or 1)

    def test_disabled_by_default(self):
        self.assertIsNone(core.download_limits()["ratelimit"])
        self.assertIsNone(core.download_limits()["fragments"])
        opts = core._base_opts()
        self.assertNotIn("ratelimit", opts)
        self.assertNotIn("concurrent_fragment_downloads", opts)

    def test_kilobytes_become_bytes(self):
        core.set_download_limits(500, 4)
        opts = core._base_opts()
        self.assertEqual(opts["ratelimit"], 512000, "yt-dlp 的单位是字节/秒")
        self.assertEqual(opts["concurrent_fragment_downloads"], 4)

    def test_values_are_clamped(self):
        core.set_download_limits(10 ** 9, 999)
        limits = core.download_limits()
        self.assertEqual(limits["ratelimit"], core.RATELIMIT_MAX_KB * 1024)
        self.assertEqual(limits["fragments"], core.MAX_FRAGMENT_WORKERS)

    def test_garbage_input_is_ignored(self):
        core.set_download_limits("快一点", None)
        self.assertIsNone(core.download_limits()["ratelimit"])
        self.assertIsNone(core.download_limits()["fragments"])

    def test_one_thread_means_yt_dlp_default(self):
        core.set_download_limits(0, 1)
        self.assertIsNone(core.download_limits()["fragments"])


class NoCliOnlyKeysTests(unittest.TestCase):
    """回归护栏：这三种键只有命令行层认，走 API 传了等于没传。"""

    CLI_ONLY = ("split_chapters", "sponsorblock_mark", "sponsorblock_remove",
                "sponsorblock", "sponsorblock_categories", "ratelimit_kb",
                "fragment_workers", "concurrent_fragments")

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
                pass
        return captured

    def test_download_video_passes_the_postprocessors(self):
        opts = self._capture(core.download_video,
                             extras={"sponsorblock": "remove", "split_chapters": True})
        keys = [p["key"] for p in opts["postprocessors"]]
        self.assertEqual(keys, ["SponsorBlock", "ModifyChapters", "FFmpegSplitChapters"])

    def test_download_video_never_passes_cli_only_keys(self):
        opts = self._capture(core.download_video,
                             extras={"sponsorblock": "mark", "split_chapters": True})
        for key in self.CLI_ONLY:
            self.assertNotIn(key, opts, f"{key} 只有 CLI 认，传进 API 没有作用")

    def test_download_playlist_also_gets_the_extras(self):
        opts = self._capture(core.download_playlist,
                             extras={"split_chapters": True})
        self.assertEqual([p["key"] for p in opts["postprocessors"]],
                         ["FFmpegSplitChapters"])
        for key in self.CLI_ONLY:
            self.assertNotIn(key, opts)

    def test_audio_never_gets_chapter_postprocessors(self):
        """章节属于视频容器；音频路径不带 extras，也不该出现章节相关后处理器。

        注意别写成「传 extras 进去然后期望不报错」——download_audio 不接这个参数，
        异常会被 _capture 吞掉，那种写法是假绿。
        """
        opts = self._capture(core.download_audio)
        keys = [p["key"] for p in opts.get("postprocessors", [])]
        for name in ("FFmpegSplitChapters", "SponsorBlock", "ModifyChapters"):
            self.assertNotIn(name, keys, name)
        self.assertIn("FFmpegExtractAudio", keys, "音频提取本身还在")

    def test_audio_signature_has_no_chapter_extras(self):
        import inspect

        params = inspect.signature(core.download_audio).parameters
        self.assertNotIn("extras", params,
                         "音频路径刻意不接章节设置，避免出现「以为会切分」的误会")

    def test_embeddding_still_switches_warnings_off(self):
        opts = self._capture(core.download_video,
                             extras={"sponsorblock": "mark"})
        self.assertFalse(opts["no_warnings"], "有后处理器时必须让警告能进日志")
        self.assertTrue(hasattr(opts["logger"], "debug"))


class ExtrasSettingsTests(unittest.TestCase):
    """设置 → 引擎参数的映射（用假 self，不需要 Tk）。"""

    def _fake_app(self, **settings):
        base = {"split_chapters": False, "sponsorblock": "off",
                "sponsorblock_categories": ""}
        base.update(settings)
        return types.SimpleNamespace(settings=base)

    def test_maps_settings_to_engine_arguments(self):
        app = self._fake_app(split_chapters=True, sponsorblock="remove",
                             sponsorblock_categories="sponsor")
        self.assertEqual(gui.App.extras_settings(app),
                         {"split_chapters": True, "sponsorblock": "remove",
                          "categories": "sponsor"})

    def test_missing_keys_default_to_off(self):
        self.assertEqual(gui.App.extras_settings(types.SimpleNamespace(settings={})),
                         {"split_chapters": False, "sponsorblock": "off",
                          "categories": ""})

    def test_ratelimit_labels(self):
        self.assertEqual(settingspage.SettingsPage._ratelimit_label(None, 0),
                         i18n.tr("ratelimit_unlimited"))
        self.assertIn("512", settingspage.SettingsPage._ratelimit_label(None, 512))
        self.assertIn("MB", settingspage.SettingsPage._ratelimit_label(None, 2048))
        self.assertEqual(settingspage.SettingsPage._ratelimit_label(None, "垃圾"),
                         i18n.tr("ratelimit_unlimited"))

    def test_index_is_taken_by_position_not_text(self):
        """标签随语言变，所以按位置取值，避免换语言后设置漂移。"""
        labels = [settingspage.SettingsPage._ratelimit_label(None, v)
                  for v in settingspage.RATELIMIT_CHOICES]
        # 未绑定方法：显式传 self=None（该方法只用参数与 i18n）
        pick = settingspage.SettingsPage._selected_index
        self.assertEqual(pick(None, types.SimpleNamespace(get=lambda: labels[2]),
                              labels), 2)
        self.assertEqual(pick(None, types.SimpleNamespace(get=lambda: "不认识"),
                              labels), 0, "对不上时回到第一项（不限）")

    def test_choices_are_sane(self):
        self.assertEqual(settingspage.RATELIMIT_CHOICES[0], 0, "第一项必须是不限")
        self.assertEqual(settingspage.FRAGMENT_CHOICES[0], 1, "第一项必须是 yt-dlp 默认")
        self.assertLessEqual(max(settingspage.RATELIMIT_CHOICES) * 1024,
                             core.RATELIMIT_MAX_KB * 1024)
        self.assertLessEqual(max(settingspage.FRAGMENT_CHOICES),
                             core.MAX_FRAGMENT_WORKERS)

    def test_defaults_exist_and_are_conservative(self):
        for key, expected in (("split_chapters", False), ("sponsorblock", "off"),
                              ("sponsorblock_categories", ""), ("ratelimit_kb", 0),
                              ("fragment_workers", 1)):
            self.assertIn(key, settings_mod.DEFAULTS)
            self.assertEqual(settings_mod.DEFAULTS[key], expected, key)

    def test_sponsorblock_modes_match_the_engine(self):
        self.assertEqual(tuple(core.SPONSORBLOCK_MODES), ("off", "mark", "remove"))


class FfprobeRequirementTests(unittest.TestCase):
    """真实下载验证的结论：切分/剪切需要 ffprobe，界面必须提前说清楚。"""

    def test_helper_detects_a_real_ffprobe(self):
        import os
        import tempfile

        from app import ffmpeg_util

        with tempfile.TemporaryDirectory() as tmp:
            fake_ffmpeg = os.path.join(tmp, "ffmpeg")
            open(fake_ffmpeg, "w").close()
            os.chmod(fake_ffmpeg, 0o755)
            fake_ffprobe = os.path.join(tmp, "ffprobe")
            open(fake_ffprobe, "w").close()
            os.chmod(fake_ffprobe, 0o755)
            with mock.patch.object(ffmpeg_util, "get_ffmpeg_path",
                                   lambda: fake_ffmpeg):
                self.assertTrue(ffmpeg_util.has_ffprobe())
            os.unlink(fake_ffprobe)
            with mock.patch.object(ffmpeg_util, "get_ffmpeg_path",
                                   lambda: fake_ffmpeg):
                self.assertEqual(ffmpeg_util.has_ffprobe(),
                                 __import__("shutil").which("ffprobe") is not None)

    def test_hint_warns_when_ffprobe_is_missing(self):
        from app import ffmpeg_util

        with mock.patch.object(ffmpeg_util, "has_ffprobe", lambda: False):
            hinted = settingspage.chapters_hint_text()
            self.assertIn(i18n.tr("chapters_need_ffprobe"), hinted)
        with mock.patch.object(ffmpeg_util, "has_ffprobe", lambda: True):
            self.assertNotIn(i18n.tr("chapters_need_ffprobe"),
                             settingspage.chapters_hint_text())

    def test_split_outtmpl_needs_a_chapter_key(self):
        """只给字符串模板时每一章会写到同一个文件（真实下载验证发现的）。"""
        template = "/tmp/out/%(title)s [%(id)s].%(ext)s"
        self.assertEqual(core.with_chapter_template(template, None), template,
                         "不切分时必须原样返回，播放列表的分目录模板靠这一点保持不变")
        split = core.with_chapter_template(template, {"split_chapters": True})
        self.assertIsInstance(split, dict)
        self.assertEqual(split["default"], template)
        self.assertIn("section_title", split["chapter"])
        self.assertIn("section_number", split["chapter"])
        self.assertTrue(split["chapter"].endswith(".%(ext)s"))
        self.assertNotEqual(split["chapter"], split["default"])


if __name__ == "__main__":
    unittest.main()
