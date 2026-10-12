"""播放列表支持（#11）的回归测试。

覆盖三件事：解析阶段能不能正确识别播放列表并快速返回、下载阶段的参数是否正确、
以及整批进度是否可信（单调、条目通知不刷屏）。

真实网络验证另有一份（本文件全部离线）：实测某 5300 条频道链接，改造前
probe 超过 200 秒仍不返回，改造后 3.0 秒返回并正确给出「至少 100 个视频」。
"""

import os
import tempfile
import unittest
from unittest import mock

from app import core


def _info(**kw):
    base = {
        "title": "Video A",
        "duration": 60,
        "formats": [
            {"format_id": "v", "vcodec": "avc1", "acodec": "none",
             "height": 720, "format_note": "720p", "filesize": 1_000_000},
        ],
    }
    base.update(kw)
    return base


class _FakeYDL:
    """按 URL 返回预设结果，用来验证 probe 的播放列表分支。"""

    responses = {}

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def extract_info(self, url, download=False):
        result = self.responses.get(url)
        if isinstance(result, Exception):
            raise result
        return result


PLAYLIST_URL = "https://www.youtube.com/@someone/videos"
FIRST_URL = "https://www.youtube.com/watch?v=aaa"


class ProbePlaylistTests(unittest.TestCase):
    def setUp(self):
        # responses 是类属性（共享可变状态），逐用例清空避免相互污染
        _FakeYDL.responses = {}

    def test_preview_limit_is_passed_to_ytdlp(self):
        """「加载更多」靠放大 playlistend 实现，必须真的传下去。"""
        seen = {}

        class RecordingYDL(_FakeYDL):
            def __init__(self, opts):
                super().__init__(opts)
                seen.update(opts)

        flat = {"_type": "playlist", "title": "p",
                "entries": [{"id": "aaa", "title": "First", "url": FIRST_URL}]}
        _FakeYDL.responses = {PLAYLIST_URL: flat, FIRST_URL: _info()}
        with mock.patch.object(core.yt_dlp, "YoutubeDL", RecordingYDL):
            core.probe(PLAYLIST_URL, preview_limit=300)
        self.assertEqual(seen.get("playlistend"), 300)

    def test_truncated_flag_follows_the_requested_limit(self):
        entries = [{"id": str(i), "title": f"t{i}", "url": f"https://x/{i}"}
                   for i in range(5)]
        _FakeYDL.responses = {
            PLAYLIST_URL: {"_type": "playlist", "title": "p", "entries": entries},
            FIRST_URL: _info(),
        }
        with mock.patch.object(core.yt_dlp, "YoutubeDL", _FakeYDL):
            # 正好取到上限：无法确定是否还有更多，按截断处理
            self.assertTrue(core.probe(PLAYLIST_URL, preview_limit=5).playlist_truncated)
        with mock.patch.object(core.yt_dlp, "YoutubeDL", _FakeYDL):
            self.assertFalse(core.probe(PLAYLIST_URL, preview_limit=50).playlist_truncated)

    def test_load_step_is_sane(self):
        self.assertGreaterEqual(core.PLAYLIST_LOAD_STEP, 10)
        self.assertLessEqual(core.PLAYLIST_LOAD_STEP, 500)

    def test_playlist_is_detected_with_meta_and_first_entry_formats(self):
        flat = {
            "_type": "playlist",
            "title": "My Playlist",
            "entries": [{"id": "aaa", "title": "First", "url": FIRST_URL},
                        {"id": "bbb", "title": "Second", "url": "https://x/bbb"}],
        }
        _FakeYDL.responses = {PLAYLIST_URL: flat, FIRST_URL: _info()}
        with mock.patch.object(core.yt_dlp, "YoutubeDL", _FakeYDL):
            info = core.probe(PLAYLIST_URL)

        self.assertEqual(info.playlist_title, "My Playlist")
        self.assertEqual(info.playlist_count, 2)
        self.assertFalse(info.playlist_truncated)
        self.assertEqual(info.playlist_entries, ["First", "Second"])
        # 扁平条目没有 formats，必须再解析首个条目，界面才有档位可选
        self.assertTrue(info.qualities)

    def test_truncation_is_reported_honestly(self):
        """解析被限量截断时不能把「100」说成总数。"""
        entries = [{"id": str(i), "title": f"v{i}", "url": f"https://x/{i}"}
                   for i in range(core.PLAYLIST_PREVIEW_LIMIT)]
        flat = {"_type": "playlist", "title": "Huge", "entries": entries}
        _FakeYDL.responses = {PLAYLIST_URL: flat,
                              "https://x/0": _info()}
        with mock.patch.object(core.yt_dlp, "YoutubeDL", _FakeYDL):
            info = core.probe(PLAYLIST_URL)

        self.assertTrue(info.playlist_truncated)
        self.assertEqual(info.playlist_count, core.PLAYLIST_PREVIEW_LIMIT)

    def test_probe_asks_for_flat_and_limited_extraction(self):
        """这是修复「播放列表 probe 卡死」的关键：必须扁平且限量。"""
        captured = {}

        class Recorder(_FakeYDL):
            def __init__(self, opts):
                captured.update(opts)
                super().__init__(opts)

        flat = {"_type": "playlist", "title": "P",
                "entries": [{"id": "aaa", "title": "F", "url": FIRST_URL}]}
        Recorder.responses = {PLAYLIST_URL: flat, FIRST_URL: _info()}
        with mock.patch.object(core.yt_dlp, "YoutubeDL", Recorder):
            core.probe(PLAYLIST_URL)

        self.assertEqual(captured.get("extract_flat"), "in_playlist")
        self.assertEqual(captured.get("playlistend"), core.PLAYLIST_PREVIEW_LIMIT)
        # noplaylist 必须保持 True：这样 watch?v=X&list=Y 仍按单个视频处理
        self.assertTrue(captured.get("noplaylist"))

    def test_single_video_still_has_no_playlist_meta(self):
        _FakeYDL.responses = {"https://www.youtube.com/watch?v=zzz": _info()}
        with mock.patch.object(core.yt_dlp, "YoutubeDL", _FakeYDL):
            info = core.probe("https://www.youtube.com/watch?v=zzz")
        self.assertEqual(info.playlist_count, 0)
        self.assertFalse(info.playlist_truncated)
        self.assertEqual(info.playlist_entries, [])

    def test_first_entry_falls_back_to_anonymous_when_cookies_fail(self):
        """打包 App 读不到浏览器 Cookie 时，首个条目必须匿名重试。

        否则播放列表会没有档位表（界面清晰度下拉空白）——这是打包态实测才
        暴露的问题：源码环境有权限读 Cookie，看不到这个分支。
        """
        flat = {"_type": "playlist", "title": "P",
                "entries": [{"id": "aaa", "title": "First", "url": FIRST_URL}]}

        class FlakyYDL(_FakeYDL):
            def extract_info(self, url, download=False):
                has_cookies = ("cookiesfrombrowser" in self.opts
                               or "cookiefile" in self.opts)
                if url == FIRST_URL and has_cookies:
                    raise RuntimeError('could not find chrome cookies database')
                return _FakeYDL.responses.get(url)

        # 注意：查表走的是基类属性，必须设在基类上（设在子类上会读到上个用例的残留）
        _FakeYDL.responses = {PLAYLIST_URL: flat, FIRST_URL: _info()}
        with mock.patch.object(core.yt_dlp, "YoutubeDL", FlakyYDL), \
                mock.patch.object(core.browser_cookies, "enabled", lambda *a: True), \
                mock.patch.object(core.browser_cookies, "ytdlp_options",
                                  lambda *a: {"cookiesfrombrowser": ("chrome",)}):
            info = core.probe(PLAYLIST_URL)

        self.assertEqual(info.playlist_count, 1)
        self.assertTrue(info.qualities, "匿名重试后仍应拿到档位表")

    def test_empty_playlist_raises(self):
        _FakeYDL.responses = {PLAYLIST_URL: {"_type": "playlist", "entries": []}}
        with mock.patch.object(core.yt_dlp, "YoutubeDL", _FakeYDL):
            with self.assertRaises(core.DownloadError):
                core.probe(PLAYLIST_URL)


class PlaylistPositionTests(unittest.TestCase):
    def test_uses_playlist_total_when_yt_dlp_gives_none(self):
        """实测 YouTube 频道页的 playlist_count 就是 None，必须靠调用方给总数。"""
        d = {"info_dict": {"playlist_index": 2, "playlist_count": None, "title": "B"}}
        self.assertEqual(core._playlist_position(d, 5), (2, 5, "B"))
        self.assertEqual(core._playlist_position(d), (2, None, "B"))

    def test_prefers_yt_dlp_count_when_present(self):
        d = {"info_dict": {"playlist_index": 2, "playlist_count": 9, "title": "B"}}
        self.assertEqual(core._playlist_position(d, 5), (2, 9, "B"))

    def test_falls_back_to_autonumber(self):
        d = {"info_dict": {"playlist_autonumber": 3, "title": "C"}}
        self.assertEqual(core._playlist_position(d, 4), (3, 4, "C"))

    def test_single_video_has_no_position(self):
        d = {"info_dict": {"title": "C"}}
        self.assertEqual(core._playlist_position(d, 4), (None, None, "C"))


class ProgressHookTests(unittest.TestCase):
    def _hook(self, total=None, callback=None, entry=None):
        return core._make_progress_hook(callback, None, None, None, entry, total)

    def test_overall_percent_covers_whole_playlist(self):
        seen = []
        hook = self._hook(total=4, callback=lambda p, s, e, st: seen.append(p))
        hook({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 50,
              "info_dict": {"playlist_index": 1, "title": "A"}})
        hook({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 50,
              "info_dict": {"playlist_index": 3, "title": "C"}})
        # 第 1/4 个下载一半 -> 12.5%；第 3/4 个下载一半 -> 62.5%
        self.assertAlmostEqual(seen[0], 12.5, places=3)
        self.assertAlmostEqual(seen[1], 62.5, places=3)

    def test_progress_never_goes_backwards(self):
        """bestvideo+bestaudio 会让同一个条目产生两段进度，第二段从 0 开始。"""
        seen = []
        hook = self._hook(total=2, callback=lambda p, s, e, st: seen.append(p))
        hook({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 100,
              "info_dict": {"playlist_index": 1, "title": "A"}})   # 第1个视频流下完
        hook({"status": "finished", "info_dict": {"playlist_index": 1, "title": "A"}})
        hook({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 1,
              "info_dict": {"playlist_index": 1, "title": "A"}})   # 同一个的音频流开始
        self.assertEqual(seen, sorted(seen), f"进度出现倒退: {seen}")

    def test_finished_does_not_max_out_the_bar_early(self):
        seen = []
        hook = self._hook(total=5, callback=lambda p, s, e, st: seen.append(p))
        hook({"status": "finished", "info_dict": {"playlist_index": 1, "title": "A"}})
        self.assertAlmostEqual(seen[-1], 20.0, places=3)

    def test_single_video_still_reaches_full(self):
        seen = []
        hook = self._hook(callback=lambda p, s, e, st: seen.append(p))
        hook({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 50,
              "info_dict": {"title": "A"}})
        hook({"status": "finished", "info_dict": {"title": "A"}})
        self.assertEqual(seen, [50.0, 100.0])

    def test_entry_callback_fires_once_per_entry(self):
        """进度钩子每秒触发几十次，条目回调不能每次都通知界面。"""
        seen = []
        hook = self._hook(total=2, entry=lambda i, n, t: seen.append((i, n, t)))
        for _ in range(5):
            hook({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 10,
                  "info_dict": {"playlist_index": 1, "title": "A"}})
        for _ in range(3):
            hook({"status": "downloading", "total_bytes": 100, "downloaded_bytes": 10,
                  "info_dict": {"playlist_index": 2, "title": "B"}})
        self.assertEqual(seen, [(1, 2, "A"), (2, 2, "B")])

    def test_entry_callback_without_total(self):
        seen = []
        hook = self._hook(entry=lambda i, n, t: seen.append((i, n, t)))
        hook({"status": "downloading", "total_bytes": 1, "downloaded_bytes": 0,
              "info_dict": {"playlist_index": 3, "title": "C"}})
        self.assertEqual(seen, [(3, 0, "C")])


class PlaylistItemSelectionTests(unittest.TestCase):
    """批量选择（#2）：core 侧的下标清洗与参数构造。"""

    def test_clean_items_dedupes_sorts_and_drops_invalid(self):
        self.assertEqual(core._clean_playlist_items([3, 1, 1, "2", "x", 0, -5, None]),
                         [1, 2, 3])
        self.assertEqual(core._clean_playlist_items([]), [])
        self.assertEqual(core._clean_playlist_items(None), [])

    def _capture(self, **kwargs):
        captured = {}

        def fake_run(url, opts, **extra):
            captured.update(opts)
            return ""

        with tempfile.TemporaryDirectory() as out, mock.patch.object(
            core, "_run_download_with_cookie_fallback", fake_run
        ), mock.patch.object(core, "_paths_from_info", lambda info: ["/tmp/x.mp4"]):
            core.download_playlist(PLAYLIST_URL, save_dir=out,
                                   playlist_title="L", **kwargs)
        return captured

    def test_selection_sets_playlist_items(self):
        captured = self._capture(playlist_items=[5, 2, 2])
        self.assertEqual(captured["playlist_items"], "2,5")

    def test_selection_overrides_limit(self):
        captured = self._capture(playlist_items=[7], playlist_limit=50)
        self.assertEqual(captured["playlist_items"], "7")
        self.assertNotIn("playlistend", captured)

    def test_no_selection_keeps_limit_behaviour(self):
        captured = self._capture(playlist_items=[], playlist_limit=10)
        self.assertNotIn("playlist_items", captured)
        self.assertEqual(captured["playlistend"], 10)


class PlaylistNamingTests(unittest.TestCase):
    """文件名按「序号 - 标题」命名（#3）。"""

    def test_index_width_follows_size_hint(self):
        self.assertEqual(core._playlist_index_width(0), 2)
        self.assertEqual(core._playlist_index_width(None), 2)
        self.assertEqual(core._playlist_index_width(5), 2)
        self.assertEqual(core._playlist_index_width(25), 2)
        self.assertEqual(core._playlist_index_width(100), 3)
        self.assertEqual(core._playlist_index_width(5309), 4)
        self.assertEqual(core._playlist_index_width("bad"), 2)

    def test_template_renders_index_title_and_id(self):
        import yt_dlp
        tmpl = core._playlist_outtmpl("/tmp/x", 100)
        ydl = yt_dlp.YoutubeDL({"outtmpl": tmpl, "quiet": True})
        name = ydl.prepare_filename(
            {"title": "TED Talk", "id": "abc", "ext": "mp4", "playlist_index": 7})
        self.assertEqual(name.split("/")[-1], "007 - TED Talk [abc].mp4")

    def test_missing_index_does_not_become_na(self):
        """不加 |0 兜底时 yt-dlp 会填 "NA"，文件名会很难看。"""
        import yt_dlp
        tmpl = core._playlist_outtmpl("/tmp/x", 25)
        ydl = yt_dlp.YoutubeDL({"outtmpl": tmpl, "quiet": True})
        name = ydl.prepare_filename({"title": "T", "id": "i", "ext": "mp4"})
        self.assertTrue(name.split("/")[-1].startswith("0 - "), name)

    def test_outtmpl_used_by_download_playlist(self):
        captured = {}

        def fake_run(url, opts, **extra):
            captured.update(opts)
            return ""

        with tempfile.TemporaryDirectory() as out, mock.patch.object(
            core, "_run_download_with_cookie_fallback", fake_run
        ), mock.patch.object(core, "_paths_from_info", lambda info: ["/tmp/x.mp4"]):
            core.download_playlist(PLAYLIST_URL, save_dir=out, playlist_title="L",
                                   playlist_size_hint=42)
        self.assertIn("%(playlist_index", captured["outtmpl"])
        self.assertIn(" - %(title)", captured["outtmpl"])
        self.assertIn("02d", captured["outtmpl"])      # 42 -> 两位


class PathsAndNamingTests(unittest.TestCase):
    def test_playlist_paths_follow_entry_order(self):
        with tempfile.TemporaryDirectory() as out:
            paths = []
            for name in ("a.mp4", "b.mp4"):
                path = os.path.join(out, name)
                open(path, "w").close()
                paths.append(path)
            info = {"_type": "playlist",
                    "entries": [{"requested_downloads": [{"filepath": paths[0]}]},
                                {"filepath": paths[1]}]}
            self.assertEqual(core._paths_from_info(info), paths)
            # 非播放列表：单体 info
            self.assertEqual(core._paths_from_info({"filepath": paths[1]}), [paths[1]])

    def test_missing_files_are_ignored(self):
        info = {"_type": "playlist",
                "entries": [{"filepath": "/nonexistent/x.mp4"}]}
        self.assertEqual(core._paths_from_info(info), [])

    def test_safe_dirname(self):
        self.assertEqual(core._safe_dirname("TED - Videos"), "TED - Videos")
        self.assertEqual(core._safe_dirname('a/b:c*d?e"f<g>h|i'), "a_b_c_d_e_f_g_h_i")
        self.assertEqual(core._safe_dirname("   "), "playlist")
        self.assertEqual(core._safe_dirname(""), "playlist")
        self.assertEqual(len(core._safe_dirname("x" * 300)), 120)


class DownloadPlaylistOptionTests(unittest.TestCase):
    def _capture(self, **kwargs):
        captured = {}

        def fake_run(url, opts, **extra):
            captured.update(opts)
            captured["_extra"] = extra
            return ""

        with tempfile.TemporaryDirectory() as out, mock.patch.object(
            core, "_run_download_with_cookie_fallback", fake_run
        ), mock.patch.object(core, "_paths_from_info", lambda info: ["/tmp/x.mp4"]):
            paths = core.download_playlist(
                PLAYLIST_URL, save_dir=out, playlist_title="My List", **kwargs)
        return captured, paths

    def test_limit_sets_playlistend(self):
        captured, _ = self._capture(playlist_limit=5)
        self.assertFalse(captured["noplaylist"])
        self.assertEqual(captured["playlistend"], 5)

    def test_limit_zero_means_whole_playlist(self):
        captured, _ = self._capture(playlist_limit=0)
        self.assertFalse(captured["noplaylist"])
        self.assertNotIn("playlistend", captured)

    def test_output_goes_into_a_playlist_folder(self):
        captured, _ = self._capture(playlist_limit=2)
        self.assertIn("My List", captured["outtmpl"])

    def test_collects_paths_from_info_and_hook(self):
        captured, paths = self._capture(playlist_limit=2)
        # info_out / collected 都要传给底层，否则播放列表拿不到产物清单
        self.assertIn("info_out", captured["_extra"])
        self.assertIn("collected", captured["_extra"])
        self.assertEqual(paths, ["/tmp/x.mp4"])

    def test_no_output_raises(self):
        with tempfile.TemporaryDirectory() as out, mock.patch.object(
            core, "_run_download_with_cookie_fallback", lambda *a, **kw: ""
        ), mock.patch.object(core, "_paths_from_info", lambda info: []):
            with self.assertRaises(core.DownloadError):
                core.download_playlist(PLAYLIST_URL, save_dir=out)


if __name__ == "__main__":
    unittest.main()
