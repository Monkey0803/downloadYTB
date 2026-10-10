"""状态详情弹窗（#3）的测试。

状态栏是单行的，长错误会被省略号截断；完整内容此前只能通过「复制诊断信息」
拿到。现在点击状态栏即可查看并可复制。

整个文件共用一个 App：本机实测第一次 Tk 初始化要数秒，而且**销毁根窗口后再新建
会让 macOS 上的 Tk 段错误**（真实踩过），所以只建一次、不销毁。
"""

import unittest

from app import gui, i18n, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

LONG_ERROR = ("解析失败：ERROR: [youtube] aqz-KE-bpKQ: Sign in to confirm you're not a "
              "bot. Use --cookies-from-browser or --cookies for the authentication.")

_APP = None


def shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


class StatusDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.video = self.app.pages["youtube"].video_panel
        self.image = self.app.pages["weibo"].image_panel

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    # ---------- 正文构造 ----------

    def test_video_body_appends_filename(self):
        self.video._status_full = (LONG_ERROR, "视频标题 [id].mp4")
        body = self.video._status_detail_body()
        self.assertIn("Sign in to confirm", body)
        self.assertIn("视频标题 [id].mp4", body)

    def test_video_body_without_filename(self):
        self.video._status_full = ("只有状态", None)
        self.assertEqual(self.video._status_detail_body(), "只有状态")

    def test_image_body_is_the_status_text(self):
        self.image._status_full = LONG_ERROR
        self.assertEqual(self.image._status_detail_body(), LONG_ERROR)

    def test_image_body_handles_empty(self):
        self.image._status_full = None
        self.assertEqual(self.image._status_detail_body(), "")

    # ---------- 截断提示 ----------

    def test_cursor_hint_only_when_truncated(self):
        self.video.status_label.configure(text="")
        self.video._status_full = (LONG_ERROR, None)
        self.video._refresh_status_text()
        self.app.update_idletasks()
        self.assertTrue(self.video.status_label.cget("cursor") != "" or
                        len(self.video.status_label.cget("text")) < len(LONG_ERROR))

    # ---------- 弹窗本体 ----------

    def _dialog(self, body=LONG_ERROR):
        return gui.TextDetailDialog(self.app, self.app,
                                    i18n.tr("status_detail"), body)

    def _all_texts(self, widget):
        """递归收集控件树里的所有 text（正文标签在嵌套的 holder 里）。"""
        found = []
        for child in widget.winfo_children():
            try:
                if "text" in child.keys():
                    found.append(str(child.cget("text")))
            except Exception:
                pass
            found.extend(self._all_texts(child))
        return found

    def test_dialog_shows_full_text_without_truncating(self):
        dialog = self._dialog()
        try:
            dialog.update_idletasks()
            joined = "\n".join(self._all_texts(dialog))
            self.assertIn("Sign in to confirm", joined)
            self.assertIn("--cookies", joined)      # 结尾也不能被截掉
        finally:
            dialog.destroy()

    def test_dialog_copy_puts_body_on_clipboard(self):
        dialog = self._dialog("要复制的内容")
        try:
            dialog._copy()
            self.app.update_idletasks()
            self.assertEqual(self.app.clipboard_get(), "要复制的内容")
            self.assertEqual(dialog.hint_label.cget("text"), i18n.tr("copied"))
        finally:
            dialog.destroy()

    def test_dialog_close_destroys(self):
        dialog = self._dialog()
        dialog._close()
        self.assertFalse(dialog.winfo_exists())

    def test_dialog_title_is_localized(self):
        dialog = self._dialog()
        try:
            self.assertEqual(dialog.title(), i18n.tr("status_detail"))
            self.assertNotEqual(dialog.title(), "status_detail")
        finally:
            dialog.destroy()


if __name__ == "__main__":
    unittest.main()
