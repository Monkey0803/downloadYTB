"""视频面板的状态机测试：音频格式（#9）、字幕开关（#10）、视频封面（#12）。

需要真实 Tk 窗口（要断言控件的可见性与 pack 顺序），因此把 settings 与
macOS menubar 都替换掉，既隔离结果，也不会污染用户自己的 settings.json。
"""

import io
import time
import unittest
from unittest.mock import Mock

from app import core, gui, i18n, panels, settings as settings_mod

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

    def test_download_params_carry_the_chapter_switches(self):
        """第 15 项：章节切分与 SponsorBlock 也要由面板交给引擎。

        离线测试只能验到「参数带上了」——真正的切分/剪切产物需要带章节且有赞助片段的
        视频才能验证，那一步本仓库没做。
        """
        self.panel._probe_done(self._playlist_info())
        # 链接要与解析结果一致，否则 on_download 会走「重新解析」分支而不组装参数
        self.panel.url_var.set(self.panel._info.url)
        self.app.settings["split_chapters"] = True
        self.app.settings["sponsorblock"] = "remove"
        self.app.settings["sponsorblock_categories"] = "sponsor,intro"
        try:
            captured = {}
            original = gui.VideoPanel._start_download
            gui.VideoPanel._start_download = lambda self, resume: captured.update(
                self._dl_params)
            try:
                self.panel.on_download()
            finally:
                gui.VideoPanel._start_download = original
        finally:
            self.app.settings["split_chapters"] = False
            self.app.settings["sponsorblock"] = "off"
            self.app.settings["sponsorblock_categories"] = ""
        self.assertEqual(captured["extras"], {"split_chapters": True,
                                              "sponsorblock": "remove",
                                              "categories": "sponsor,intro"})

    def test_download_params_carry_the_embed_switches(self):
        """第 10 项：面板要把「嵌入到文件」三个开关交给引擎。"""
        self.panel._probe_done(self._playlist_info())
        # 链接要与解析结果一致，否则 on_download 会走「重新解析」分支而不组装参数
        self.panel.url_var.set(self.panel._info.url)
        self.app.settings["embed_metadata"] = True
        self.app.settings["embed_thumbnail"] = False
        try:
            captured = {}
            original = gui.VideoPanel._start_download
            gui.VideoPanel._start_download = lambda self, resume: captured.update(
                self._dl_params)
            try:
                self.panel.on_download()
            finally:
                gui.VideoPanel._start_download = original
        finally:
            self.app.settings["embed_metadata"] = False
        self.assertEqual(captured["embed"],
                         {"subtitles": False, "thumbnail": False, "metadata": True})

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

    def _picker(self, selected=None, truncated=False, on_request_more=None):
        picker = gui.EntryPicker(self.app, self.app, self.titles, selected,
                                 truncated=truncated,
                                 on_request_more=on_request_more)
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

    # ---------- 增量加载（#7）----------

    def test_load_more_button_hidden_without_more_entries(self):
        self.assertFalse(self._picker().more_btn.winfo_manager())

    def test_load_more_button_hidden_without_callback(self):
        """只标了「还有更多」但没人能去取，就不要显示按钮。"""
        self.assertFalse(self._picker(truncated=True).more_btn.winfo_manager())

    def test_load_more_button_visible_when_truncated(self):
        picker = self._picker(truncated=True, on_request_more=lambda limit: None)
        self.assertTrue(picker.more_btn.winfo_manager())
        self.assertEqual(picker.more_btn._state, "normal")

    def test_load_more_asks_for_the_next_step(self):
        asked = []
        picker = self._picker(truncated=True, on_request_more=asked.append)
        picker._on_load_more()
        self.assertEqual(asked, [len(self.titles) + core.PLAYLIST_LOAD_STEP])

    def test_loading_disables_button_and_explains(self):
        picker = self._picker(truncated=True, on_request_more=lambda limit: None)
        picker._on_load_more()
        self.assertEqual(picker.more_btn._state, "disabled")
        self.assertEqual(picker.hint_label.cget("text"), i18n.tr("pick_loading_more"))
        # 加载中重复点击不会再发一次请求
        asked = []
        picker._on_request_more = asked.append
        picker._on_load_more()
        self.assertEqual(asked, [])

    def test_set_items_replaces_list_and_keeps_selection(self):
        picker = self._picker([2, 4], truncated=True,
                              on_request_more=lambda limit: None)
        picker.set_items(["一", "二", "三", "四", "五", "六"], truncated=False)
        self.assertEqual(len(picker._pills), 6)
        self.assertEqual(picker._collect(), [2, 4])
        self.assertFalse(picker.more_btn.winfo_manager(), "已加载完就不该再显示按钮")

    def test_set_items_can_extend_and_keep_new_checks(self):
        picker = self._picker([1], truncated=True, on_request_more=lambda limit: None)
        picker.set_items(["一", "二", "三"], truncated=True)
        picker._pills[2].set_checked(True)
        self.assertEqual(picker._collect(), [1, 3])
        self.assertEqual(picker.more_btn._state, "normal")

    def test_hint_count_and_wording_follow_the_loaded_state(self):
        """还有更多时提示「可以继续取」，全部加载完后改成「已列出全部」。"""
        picker = self._picker(truncated=True, on_request_more=lambda limit: None)
        picker.set_items(["一"] * 7)
        self.assertEqual(picker.hint_label.cget("text"),
                         i18n.tr("pick_entries_hint_more", n=7))
        picker.set_items(["一"] * 7, truncated=False)
        self.assertEqual(picker.hint_label.cget("text"),
                         i18n.tr("pick_entries_hint", n=7))
        self.assertNotEqual(i18n.tr("pick_entries_hint", n=7),
                            i18n.tr("pick_entries_hint_more", n=7))

    def test_load_error_reenables_and_reports(self):
        picker = self._picker(truncated=True, on_request_more=lambda limit: None)
        picker._on_load_more()
        picker.show_load_error("网络断了")
        self.assertEqual(picker.more_btn._state, "normal")
        self.assertIn("网络断了", picker.hint_label.cget("text"))

    def test_confirm_after_extending_reports_new_indices(self):
        picker = self._picker([1], truncated=True, on_request_more=lambda limit: None)
        picker.set_items([f"第{i}集" for i in range(1, 121)], truncated=False)
        picker._pills[119].set_checked(True)
        picker._confirm()
        self.assertEqual(picker.result, [1, 120])

    def test_cancel_then_destroy_is_safe(self):
        picker = self._picker()
        picker._cancel()
        picker.destroy()          # 第二次销毁不应抛错


class PlaylistLoadMoreTests(unittest.TestCase):
    """面板把「加载更多」接到后台探测（#7）。

    选择器自己不发请求，只回调面板；面板在后台线程里放大 playlistend 再回填，
    所以这里断言的是「探测上限被放大」和「回填后列表真的变长、勾选没丢」。
    """

    PLAYLIST = "https://www.youtube.com/@someone/videos"

    @classmethod
    def setUpClass(cls):
        settings_mod.load = lambda: _fake_load()
        settings_mod.save = lambda data: None
        gui.menubar.create = lambda *a, **kw: None
        cls.app = gui.App()
        cls.panel = cls.app.pages["youtube"].video_panel

    @classmethod
    def tearDownClass(cls):
        settings_mod.load = _ORIGINAL_LOAD
        settings_mod.save = _ORIGINAL_SAVE
        gui.menubar.create = _ORIGINAL_MENUBAR
        cls.app.destroy()

    def setUp(self):
        self._probe = core.probe
        self.panel.url_entry.set(self.PLAYLIST)
        self.panel._playlist_items = []
        self.panel._playlist_entries = []
        self.panel._playlist_truncated = False

    def tearDown(self):
        core.probe = self._probe

    def _wait(self, condition, timeout=3.0):
        """等后台线程把回传回调挂出来（只等线程，不等 Tk 事件）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if condition():
                return True
            time.sleep(0.01)
        return False

    def _capture_after(self):
        """接管 app.after：记下回调，由测试在主线程手动执行。

        为什么必须这样：测试没有真的跑 mainloop()，而 Tk 在主线程不处于 main loop
        时会拒绝跨线程的 after 调用（实测抛 RuntimeError: main thread is not in
        main loop）。生产环境有 mainloop，这个改法只是让测试能确定性地驱动回传。
        """
        scheduled = []
        self._after = self.app.after
        self.app.after = lambda ms, fn=None, *a: (scheduled.append(fn), "id")[1]
        self.addCleanup(lambda: setattr(self.app, "after", self._after))
        return scheduled

    def test_open_picker_passes_loaded_state_and_loader(self):
        captured = {}

        class FakePicker:
            def __init__(self, master, app, titles, selected=None, **kw):
                captured["titles"] = list(titles)
                captured["selected"] = selected
                captured.update(kw)

            def show(self):
                return None

        # _open_entry_picker 是面板方法，代码住在 app/panels.py：打桩落在 panels 上，
        # 否则补丁拦不住，会弹出真实模态框把测试卡住（本文件曾因此挂起）
        original = panels.EntryPicker
        panels.EntryPicker = FakePicker
        try:
            self.panel._info = Mock(playlist_count=250, playlist_truncated=True,
                                    warning="")
            self.panel._playlist_entries = ["第一集", "第二集"]
            self.panel._playlist_truncated = True
            self.panel._playlist_items = [2]
            self.panel._open_entry_picker()
        finally:
            panels.EntryPicker = original
        self.assertEqual(captured["titles"], ["第一集", "第二集"])
        self.assertEqual(captured["selected"], [2])
        self.assertTrue(captured["truncated"])
        self.assertTrue(callable(captured["on_request_more"]))

    def test_load_more_probes_with_the_requested_limit(self):
        asked = []
        picker = Mock()

        def fake_probe(url, preview_limit=0):
            asked.append((url, preview_limit))
            return Mock(playlist_entries=[f"第{i}集" for i in range(250)],
                        playlist_truncated=False)

        core.probe = fake_probe
        scheduled = self._capture_after()
        self.panel._load_more_entries(200, picker)
        self.assertTrue(self._wait(lambda: scheduled), "后台没有调度回传")
        self.assertEqual(asked, [(self.PLAYLIST, 200)])
        scheduled[0]()                       # 在主线程执行回传
        picker.set_items.assert_called_with([f"第{i}集" for i in range(250)], False)
        self.assertEqual(len(self.panel._playlist_entries), 250)
        self.assertFalse(self.panel._playlist_truncated)

    def test_load_more_failure_is_reported_to_the_picker(self):
        picker = Mock()

        def boom(url, preview_limit=0):
            raise core.DownloadError("网络断了")

        core.probe = boom
        scheduled = self._capture_after()
        self.panel._load_more_entries(200, picker)
        self.assertTrue(self._wait(lambda: scheduled), "失败路径没有调度回传")
        scheduled[0]()
        self.assertIn("网络断了", str(picker.show_load_error.call_args[0][0]))
        picker.set_items.assert_not_called()

    def test_load_more_without_url_reports_immediately(self):
        picker = Mock()
        self.panel.url_entry.set("")
        self.panel._load_more_entries(200, picker)
        picker.show_load_error.assert_called_once()
        picker.set_items.assert_not_called()

    def test_closed_picker_is_not_touched_when_results_come_back(self):
        """探测期间用户关掉弹窗：回填必须安全跳过，而不是对已销毁控件下手。"""
        picker = gui.EntryPicker(self.app, self.app, ["一", "二"])
        picker.destroy()
        scheduled = self._capture_after()
        core.probe = lambda url, preview_limit=0: Mock(
            playlist_entries=["一", "二", "三"], playlist_truncated=False)
        self.panel._load_more_entries(200, picker)
        self.assertTrue(self._wait(lambda: scheduled))
        scheduled[0]()                       # 不应抛 TclError
        self.assertEqual(self.panel._playlist_entries, [], "弹窗关了就不必回填")

    def test_selection_beyond_the_old_cap_is_still_valid(self):
        """第 250 条要能真的被选中并影响命名补零宽度。"""
        self.panel._info = Mock(playlist_truncated=True, playlist_count=100, warning="")
        self.panel._playlist_items = [1, 250]
        self.assertEqual(self.panel._playlist_size_hint(), 250)
        self.assertEqual(self.panel._playlist_total(), 2)

if __name__ == "__main__":
    unittest.main()
