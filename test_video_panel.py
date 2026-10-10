"""视频面板的状态机测试：音频格式（#9）、字幕开关（#10）、视频封面（#12）。

需要真实 Tk 窗口（要断言控件的可见性与 pack 顺序），因此把 settings 与
macOS menubar 都替换掉，既隔离结果，也不会污染用户自己的 settings.json。
"""

import io
import unittest

from app import core, gui, i18n, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create


def _fake_load(**overrides):
    data = dict(settings_mod.DEFAULTS)
    data["language"] = "zh_CN"
    data["browser_cookies"] = ""
    data["window_geometry"] = "1020x745"
    data.update(overrides)
    return data


def _png_bytes(width=320, height=180, color="red"):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


class VideoPanelStateTests(unittest.TestCase):
    """用一个真实 App 驱动面板，断言控件可见性与可用性。"""

    def setUp(self):
        self.saved = {}
        settings_mod.load = lambda: _fake_load()
        settings_mod.save = lambda data: self.saved.update(data)
        gui.menubar.create = lambda *a, **kw: None
        self.app = gui.App()
        self.app.show_page("youtube")
        self.panel = self.app.pages["youtube"].video_panel
        self.app.update_idletasks()

    def tearDown(self):
        settings_mod.load = _ORIGINAL_LOAD
        settings_mod.save = _ORIGINAL_SAVE
        gui.menubar.create = _ORIGINAL_MENUBAR
        self.app.destroy()

    def _set_mode(self, mode):
        self.panel.mode_var.set(mode)
        self.panel._on_mode_change()   # 程序化切换不会走 SegmentedControl 回调
        self.app.update_idletasks()

    def test_video_mode_shows_subtitles_not_format(self):
        self._set_mode("video")
        self.assertTrue(self.panel.subtitle_check.winfo_manager())
        self.assertFalse(self.panel.format_select.winfo_manager())
        self.assertTrue(self.panel.quality_select.winfo_manager())

    def test_audio_mode_shows_format_not_subtitles(self):
        self._set_mode("audio")
        self.assertTrue(self.panel.format_select.winfo_manager())
        self.assertFalse(self.panel.subtitle_check.winfo_manager())
        self.assertEqual(self.panel.quality_label.cget("text"), "比特率")

    def test_lossless_hides_bitrate_and_mp3_restores_it(self):
        self._set_mode("audio")
        self.panel.format_var.set("FLAC")
        self.app.update_idletasks()
        self.assertFalse(self.panel.quality_select.winfo_manager())
        self.assertFalse(self.panel.quality_label.winfo_manager())

        # 切回有损格式必须把比特率控件恢复出来（否则只能重启应用）
        self.panel.format_var.set("MP3")
        self.app.update_idletasks()
        self.assertTrue(self.panel.quality_select.winfo_manager())
        self.assertTrue(self.panel.quality_label.winfo_manager())
        self.assertEqual(self.panel.quality_label.cget("text"), "比特率")

    def test_format_choice_is_persisted(self):
        self._set_mode("audio")
        self.panel.format_var.set("OPUS")
        self.app.update_idletasks()
        self.assertEqual(self.saved.get("audio_format"), "opus")
        self.assertEqual(self.panel._audio_format(), "opus")

    def test_subtitle_toggle_is_persisted(self):
        self.panel.subtitle_var.set(True)
        self.app.update_idletasks()
        self.assertEqual(self.saved.get("subtitles_enabled"), True)

    def test_non_youtube_platform_hides_subtitles(self):
        """字幕只对 YouTube 承诺，其它平台不应出现该开关。"""
        other = self.app.pages["x"].video_panel
        other.mode_var.set("video")
        other._on_mode_change()
        self.app.update_idletasks()
        self.assertFalse(other.subtitle_check.winfo_manager())
        self.assertFalse(other.opt_row2.winfo_manager())


class VideoThumbnailTests(unittest.TestCase):
    def setUp(self):
        settings_mod.load = lambda: _fake_load()
        settings_mod.save = lambda data: None
        gui.menubar.create = lambda *a, **kw: None
        self.app = gui.App()
        self.app.show_page("youtube")
        self.panel = self.app.pages["youtube"].video_panel
        self.app.update_idletasks()

    def tearDown(self):
        settings_mod.load = _ORIGINAL_LOAD
        settings_mod.save = _ORIGINAL_SAVE
        gui.menubar.create = _ORIGINAL_MENUBAR
        self.app.destroy()

    def test_image_is_shown_and_reference_kept(self):
        self.panel._set_thumb_image(self.panel._thumb_epoch, _png_bytes())
        self.app.update_idletasks()
        # PhotoImage 必须持引用，否则被 GC 回收后界面显示空白
        self.assertIsNotNone(self.panel._thumb_photo)
        self.assertNotEqual(str(self.panel.thumb_label.cget("image")), "")

    def test_stale_epoch_is_ignored(self):
        """解析了另一条链接后，旧封面的返回结果不能落到新视频上。"""
        self.panel._thumb_epoch += 1
        self.panel._set_thumb_image(self.panel._thumb_epoch - 1, _png_bytes())
        self.app.update_idletasks()
        self.assertIsNone(self.panel._thumb_photo)
        self.assertEqual(str(self.panel.thumb_label.cget("image")), "")

    def test_clear_removes_image(self):
        self.panel._set_thumb_image(self.panel._thumb_epoch, _png_bytes())
        self.app.update_idletasks()
        self.panel._clear_video_thumbnail()
        self.app.update_idletasks()
        self.assertIsNone(self.panel._thumb_photo)
        self.assertEqual(str(self.panel.thumb_label.cget("image")), "")

    def test_broken_bytes_do_not_raise(self):
        self.panel._set_thumb_image(self.panel._thumb_epoch, b"not an image")
        self.app.update_idletasks()   # 不应抛错，也不应设置图片
        self.assertIsNone(self.panel._thumb_photo)

    def test_missing_thumbnail_url_clears_without_network(self):
        info = core.VideoInfo(url="u", title="t", duration=1, uploader="",
                              qualities=[], audio_options=[], thumbnail="")
        self.panel._load_video_thumbnail(info)
        self.app.update_idletasks()
        self.assertIsNone(self.panel._thumb_photo)



class PlaylistUiTests(unittest.TestCase):
    """播放列表界面（#11）：范围选择器的取值/可见性与标题行文案。"""

    def setUp(self):
        self.saved = {}
        settings_mod.load = lambda: _fake_load()
        settings_mod.save = lambda data: self.saved.update(data)
        gui.menubar.create = lambda *a, **kw: None
        self.app = gui.App()
        self.app.show_page("youtube")
        self.panel = self.app.pages["youtube"].video_panel
        self.app.update_idletasks()

    def tearDown(self):
        settings_mod.load = _ORIGINAL_LOAD
        settings_mod.save = _ORIGINAL_SAVE
        gui.menubar.create = _ORIGINAL_MENUBAR
        self.app.destroy()

    def _playlist_info(self, count=100, truncated=True, title="My List"):
        return core.VideoInfo(
            url="u", title="First video", duration=60, uploader="Someone",
            qualities=[], audio_options=[], thumbnail="",
            playlist_title=title, playlist_count=count,
            playlist_truncated=truncated, playlist_entries=["First", "Second"])

    def test_limit_label_round_trip(self):
        for value in core.PLAYLIST_LIMITS:
            label = self.panel._playlist_limit_label(value)
            self.assertEqual(self.panel._playlist_limit_value(label), value,
                             f"{value} <-> {label!r} 不能往返")

    def test_range_hidden_for_single_video(self):
        self.panel._probe_done(core.VideoInfo(url="u", title="单视频", duration=10,
                                              uploader="", qualities=[],
                                              audio_options=[], thumbnail=""))
        self.app.update_idletasks()
        self.assertFalse(self.panel.range_select.winfo_manager())
        self.assertFalse(self.panel.range_label.winfo_manager())

    def test_range_visible_for_playlist(self):
        self.panel._probe_done(self._playlist_info())
        self.app.update_idletasks()
        self.assertTrue(self.panel.range_select.winfo_manager())
        self.assertTrue(self.panel.range_label.winfo_manager())

    def test_title_line_announces_playlist(self):
        info = self._playlist_info()
        text, suffix = self.panel._title_text(info)
        self.assertIn("My List", text)
        self.assertIn("至少 100", text)     # 被限量截断时不能谎报总数
        self.assertEqual(suffix, "")

    def test_title_line_exact_count_for_small_playlist(self):
        info = self._playlist_info(count=3, truncated=False)
        text, _ = self.panel._title_text(info)
        self.assertIn("My List", text)
        self.assertIn("3 个视频", text)
        self.assertNotIn("至少", text)

    def test_single_video_title_keeps_uploader_and_duration(self):
        info = core.VideoInfo(url="u", title="标题", duration=125, uploader="作者",
                              qualities=[], audio_options=[], thumbnail="")
        text, suffix = self.panel._title_text(info)
        self.assertEqual(text, "标题")
        self.assertIn("作者", suffix)

    def test_total_uses_limit_when_limited(self):
        self.panel._probe_done(self._playlist_info(count=100, truncated=True))
        self.panel.playlist_limit_var.set(self.panel._playlist_limit_label(10))
        self.app.update_idletasks()
        self.assertEqual(self.panel._playlist_total(), 10)

    def test_total_is_zero_when_unknown(self):
        """「全部」且解析被截断时总数未知，只能退化成「第 i 个」。"""
        self.panel._probe_done(self._playlist_info(count=100, truncated=True))
        self.panel.playlist_limit_var.set(self.panel._playlist_limit_label(0))
        self.app.update_idletasks()
        self.assertEqual(self.panel._playlist_total(), 0)

    def test_total_uses_count_when_known(self):
        self.panel._probe_done(self._playlist_info(count=3, truncated=False))
        self.panel.playlist_limit_var.set(self.panel._playlist_limit_label(0))
        self.app.update_idletasks()
        self.assertEqual(self.panel._playlist_total(), 3)

    def test_title_is_recomputed_on_resize(self):
        """解析可能早于布局完成（那时宽度还很小），窗口缩放也会改变可用宽度。

        所以标题必须挂 <Configure> 重算；否则截断结果会一直停留在
        「播放列表：TED - Videos · …」，把条目数整段丢掉。
        """
        self.panel._probe_done(self._playlist_info())
        self.app.update_idletasks()
        # 把文字改坏，再触发一次尺寸变化事件，模拟「宽度变了需要重算」
        self.panel.title_label.configure(text="坏掉的文字")
        self.panel.title_label.event_generate("<Configure>")
        self.app.update_idletasks()
        self.assertIn("至少 100", self.panel.title_label.cget("text"))

    def test_refresh_title_without_info_is_safe(self):
        self.panel._info = None
        self.panel._refresh_title()      # 不应抛错

    def test_pick_button_visibility(self):
        self.panel._probe_done(self._playlist_info())
        self.app.update_idletasks()
        self.assertTrue(self.panel.pick_btn.winfo_manager())
        self.panel._probe_done(core.VideoInfo(url="u", title="单视频", duration=10,
                                              uploader="", qualities=[],
                                              audio_options=[], thumbnail=""))
        self.app.update_idletasks()
        self.assertFalse(self.panel.pick_btn.winfo_manager())

    def test_pick_button_text_reflects_selection(self):
        self.panel._probe_done(self._playlist_info())
        self.assertEqual(self.panel.pick_btn._text, i18n.tr("pick_entries"))
        self.panel._playlist_items = [2, 5, 9]
        self.panel._refresh_option_visibility()
        self.app.update_idletasks()
        self.assertEqual(self.panel.pick_btn._text, i18n.tr("pick_entries_selected", n=3))

    def test_range_is_disabled_while_a_selection_exists(self):
        self.panel._probe_done(self._playlist_info())
        self.panel._playlist_items = [4]
        self.panel._refresh_option_visibility()
        self.app.update_idletasks()
        self.assertFalse(self.panel.range_select._enabled)
        self.panel._playlist_items = []
        self.panel._refresh_option_visibility()
        self.app.update_idletasks()
        self.assertTrue(self.panel.range_select._enabled)

    def test_total_follows_selection(self):
        self.panel._probe_done(self._playlist_info())
        self.panel.playlist_limit_var.set(self.panel._playlist_limit_label(50))
        self.panel._playlist_items = [1, 2, 3]
        self.assertEqual(self.panel._playlist_total(), 3)

    def test_size_hint_follows_selection(self):
        """补零宽度要覆盖最大勾选下标，而不是「下载范围」的值。"""
        self.panel._probe_done(self._playlist_info())
        self.panel.playlist_limit_var.set(self.panel._playlist_limit_label(1))
        self.panel._playlist_items = [7, 100]
        self.assertEqual(self.panel._playlist_size_hint(), 100)
        self.panel._playlist_items = []
        self.assertEqual(self.panel._playlist_size_hint(), 1)      # 跟随「仅当前视频」
        self.panel.playlist_limit_var.set(self.panel._playlist_limit_label(25))
        self.assertEqual(self.panel._playlist_size_hint(), 25)

    def test_selection_is_cleared_on_new_probe(self):
        self.panel._probe_done(self._playlist_info())
        self.panel._playlist_items = [1, 2]
        self.panel._probe_done(self._playlist_info())
        self.assertEqual(self.panel._playlist_items, [])

    def test_download_params_carry_selection(self):
        self.panel._probe_done(self._playlist_info())
        self.panel.playlist_limit_var.set(self.panel._playlist_limit_label(25))
        self.panel._playlist_items = [3, 9]
        self.panel.url_var.set(self.panel._info.url)
        captured = {}
        original = gui.VideoPanel._start_download
        gui.VideoPanel._start_download = lambda self, resume: captured.update(self._dl_params)
        try:
            self.panel.on_download()
        finally:
            gui.VideoPanel._start_download = original
        self.assertEqual(captured["playlist_items"], [3, 9])
        self.assertEqual(captured["playlist_total"], 2)
        self.assertTrue(captured["is_playlist"])
        # 有勾选时补零宽度按最大勾选下标（9）而不是「下载范围」的 25
        self.assertEqual(captured["playlist_size_hint"], 9)

    def test_range_choice_is_persisted(self):
        self.panel._probe_done(self._playlist_info())
        self.panel.playlist_limit_var.set(self.panel._playlist_limit_label(25))
        self.app.update_idletasks()
        self.assertEqual(self.saved.get("playlist_limit"), 25)
        self.assertEqual(self.panel._playlist_limit(), 25)


class EntryPickerTests(unittest.TestCase):
    """条目批量选择弹窗（#2）。刻意不调用 show()，否则模态会挂住测试。

    整个类共享一个 Tk 根窗口：每个用例都建一次 App 太慢，而且反复销毁根窗口
    时残留的 Toplevel 会让 Tk 直接段错误（真实踩过）。
    """

    @classmethod
    def setUpClass(cls):
        settings_mod.load = lambda: _fake_load()
        settings_mod.save = lambda data: None
        gui.menubar.create = lambda *a, **kw: None
        cls.app = gui.App()
        cls.titles = ["第一集", "第二集", "第三集", "第四集"]

    @classmethod
    def tearDownClass(cls):
        settings_mod.load = _ORIGINAL_LOAD
        settings_mod.save = _ORIGINAL_SAVE
        gui.menubar.create = _ORIGINAL_MENUBAR
        cls.app.destroy()

    def setUp(self):
        self._pickers = []

    def tearDown(self):
        # 用例内不手动销毁，统一在这里收尾，避免重复 destroy 导致崩溃
        for picker in self._pickers:
            try:
                if picker.winfo_exists():
                    picker.destroy()
            except Exception:
                pass

    def _picker(self, selected=None):
        picker = gui.EntryPicker(self.app, self.app, self.titles, selected)
        self._pickers.append(picker)
        return picker

    def test_preselection_round_trip(self):
        self.assertEqual(self._picker([2, 4])._collect(), [2, 4])

    def test_no_preselection_means_nothing_checked(self):
        self.assertEqual(self._picker()._collect(), [])

    def test_select_all_and_none(self):
        picker = self._picker()
        picker._select_all(True)
        self.assertEqual(picker._collect(), [1, 2, 3, 4])
        picker._select_all(False)
        self.assertEqual(picker._collect(), [])

    def test_confirm_sets_result(self):
        picker = self._picker([1])
        picker._confirm()
        self.assertEqual(picker.result, [1])

    def test_cancel_returns_none(self):
        picker = self._picker([1, 2])
        picker._cancel()
        self.assertIsNone(picker.result)

    def test_row_count_matches_probed_entries(self):
        self.assertEqual(len(self._picker()._pills), len(self.titles))

    def test_labels_carry_zero_padded_index(self):
        picker = self._picker()
        self.assertTrue(picker._pills[0]._text.startswith("01 · "), picker._pills[0]._text)

    def test_cancel_then_destroy_is_safe(self):
        picker = self._picker()
        picker._cancel()
        picker.destroy()          # 第二次销毁不应抛错

if __name__ == "__main__":
    unittest.main()
