"""音频解析档位与下载参数的无网络回归测试。"""

import tempfile
import unittest
from unittest import mock

from app import core


class AudioOptionsTests(unittest.TestCase):
    def test_probe_exposes_audio_bitrate_and_estimated_size(self):
        info = {
            "title": "Audio test",
            "duration": 60,
            "formats": [
                {"format_id": "low", "vcodec": "none", "acodec": "opus", "abr": 49.5},
                {"format_id": "high", "vcodec": "none", "acodec": "opus", "abr": 129.1},
                {"format_id": "video", "vcodec": "avc1", "acodec": "none",
                 "height": 720, "format_note": "720p", "filesize": 1_000_000},
            ],
        }

        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def extract_info(self, url, download=False):
                return info

        with mock.patch.object(core.yt_dlp, "YoutubeDL", FakeYDL):
            result = core.probe("https://example.com/video")

        self.assertEqual([option.bitrate_kbps for option in result.audio_options], [128, 48])
        self.assertEqual(result.audio_options[0].format_id, "high")
        self.assertEqual(result.audio_options[0].size_bytes, 960_000)
        self.assertEqual(result.audio_options[0].display, "128 kbps ・ 约 938 KB")

    def test_download_audio_uses_selected_bitrate_and_format(self):
        captured = {}

        def fake_run(url, opts, **kwargs):
            captured.update(opts)
            return "audio.mp3"

        with tempfile.TemporaryDirectory() as output_dir, mock.patch.object(
            core, "_run_download_with_cookie_fallback", fake_run
        ):
            core.download_audio(
                "https://example.com/video", save_dir=output_dir,
                bitrate_kbps=128, format_id="high",
            )

        self.assertEqual(captured["format"], "high/bestaudio/best")
        self.assertEqual(captured["postprocessors"][0]["preferredquality"], "128")


if __name__ == "__main__":
    unittest.main()
