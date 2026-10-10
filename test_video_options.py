"""视频下载选项的无网络回归测试：字幕（#10）与封面字段（#12 的数据来源）。

字幕的关键风险是语言列表：yt-dlp 的 "all" 会把上百种自动字幕全拉下来，
所以「空/非法输入必须回落到默认语言」这类行为要有测试守住。
"""

import tempfile
import unittest
from unittest import mock

from app import core


class SubtitleOptionTests(unittest.TestCase):
    def test_default_languages_when_not_specified(self):
        for value in (None, "", "   ", [], ["", "  "]):
            with self.subTest(value=value):
                opts = core.subtitle_options(value)
                self.assertEqual(opts["subtitleslangs"],
                                 list(core.DEFAULT_SUBTITLE_LANGS))

    def test_never_uses_all(self):
        """无论如何都不应把 "all" 交给 yt-dlp。"""
        for value in (None, "", "all", "ALL"):
            with self.subTest(value=value):
                langs = core.subtitle_options(value)["subtitleslangs"]
                self.assertNotIn("all", [str(item).lower() for item in langs])

    def test_comma_separated_languages_are_parsed(self):
        opts = core.subtitle_options("en, ja ,zh-Hans")
        self.assertEqual(opts["subtitleslangs"], ["en", "ja", "zh-Hans"])

    def test_enables_manual_and_automatic_subtitles(self):
        opts = core.subtitle_options("en")
        self.assertTrue(opts["writesubtitles"])
        # 绝大多数 YouTube 视频只有自动字幕，必须一并开启
        self.assertTrue(opts["writeautomaticsub"])
        self.assertEqual(opts["subtitlesformat"], "srt/best")


class VideoDownloadOptionTests(unittest.TestCase):
    def _capture_opts(self, **kwargs):
        captured = {}

        def fake_run(url, opts, **extra):
            captured.update(opts)
            return "/tmp/video.mp4"

        with tempfile.TemporaryDirectory() as output_dir, mock.patch.object(
            core, "_run_download_with_cookie_fallback", fake_run
        ):
            core.download_video("https://www.youtube.com/watch?v=x",
                                save_dir=output_dir, **kwargs)
        return captured

    def test_subtitles_off_by_default(self):
        opts = self._capture_opts()
        self.assertNotIn("writesubtitles", opts)
        self.assertNotIn("subtitleslangs", opts)

    def test_subtitles_on_adds_language_options(self):
        opts = self._capture_opts(subtitles=True, subtitle_langs="en,zh-Hans")
        self.assertTrue(opts["writesubtitles"])
        self.assertEqual(opts["subtitleslangs"], ["en", "zh-Hans"])
        # 字幕不能影响视频格式选择
        self.assertIn("format", opts)

    def test_subtitles_on_without_languages_uses_defaults(self):
        opts = self._capture_opts(subtitles=True)
        self.assertEqual(opts["subtitleslangs"], list(core.DEFAULT_SUBTITLE_LANGS))


class ThumbnailFieldTests(unittest.TestCase):
    def test_probe_exposes_thumbnail_for_preview(self):
        info = {
            "title": "Thumb test",
            "thumbnail": "https://example.com/cover.jpg",
            "formats": [
                {"format_id": "v", "vcodec": "avc1", "acodec": "none",
                 "height": 720, "format_note": "720p", "filesize": 1_000_000},
            ],
        }

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def extract_info(self, url, download=False):
                return info

        with mock.patch.object(core.yt_dlp, "YoutubeDL", FakeYDL):
            result = core.probe("https://example.com/video")
        self.assertEqual(result.thumbnail, "https://example.com/cover.jpg")

    def test_missing_thumbnail_is_empty_not_none(self):
        info = {"title": "No thumb", "formats": [
            {"format_id": "v", "vcodec": "avc1", "acodec": "none", "height": 360,
             "format_note": "360p", "filesize": 100},
        ]}

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def extract_info(self, url, download=False):
                return info

        with mock.patch.object(core.yt_dlp, "YoutubeDL", FakeYDL):
            result = core.probe("https://example.com/video")
        # 界面按真值判断是否载入封面，None 会让 `if url` 之外的分支难写
        self.assertEqual(result.thumbnail, "")


if __name__ == "__main__":
    unittest.main()
