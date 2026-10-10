"""视频面板的状态机测试：音频格式（#9）、字幕开关（#10）、视频封面（#12）。

需要真实 Tk 窗口（要断言控件的可见性与 pack 顺序），因此把 settings 与
macOS menubar 都替换掉，既隔离结果，也不会污染用户自己的 settings.json。
"""

import io
import unittest

from app import core, gui, settings as settings_mod

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


if __name__ == "__main__":
    unittest.main()
