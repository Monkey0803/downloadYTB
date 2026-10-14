"""下载历史（①）的测试：数据层 + 页面 + 面板接线。

数据层用例用「假 App」直接调 `gui.App` 上的方法，因此不需要 Tk、跑得很快；
页面与面板用例需要真实窗口，按仓库约定**整个文件只建一个 App 且不销毁**
（第一次 Tk 初始化要数秒，销毁后再建根窗口会让 macOS 上的 Tk 段错误）。
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

from app import gui, panels, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

_APP = None


class _FakeApp:
    """只带 settings 与 LIMIT 的替身，用来测试纯数据层方法。

    download_history 直接借用真实实现，避免测试里再抄一遍同样的读取逻辑
    （抄一遍就会在实现改动时失去意义）。
    """

    HISTORY_LIMIT = gui.App.HISTORY_LIMIT
    download_history = gui.App.download_history

    def __init__(self, settings=None):
        self.settings = settings if settings is not None else {}


class HistoryDataTests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        settings_mod.save = lambda data: self.saved.append(dict(data))

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def _record(self, app, paths, **kw):
        gui.App.record_download(app, paths, **kw)

    def test_newest_first(self):
        app = _FakeApp()
        self._record(app, ["/tmp/a.mp4"], timestamp=100)
        self._record(app, ["/tmp/b.mp4"], timestamp=200)
        paths = [e["path"] for e in gui.App.download_history(app)]
        self.assertEqual(paths, ["/tmp/b.mp4", "/tmp/a.mp4"])

    def test_same_path_is_not_duplicated(self):
        app = _FakeApp()
        self._record(app, ["/tmp/a.mp4"], timestamp=100)
        self._record(app, ["/tmp/a.mp4"], timestamp=300)
        entries = gui.App.download_history(app)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["time"], 300)

    def test_limit_is_enforced(self):
        app = _FakeApp()
        for index in range(app.HISTORY_LIMIT + 10):
            self._record(app, [f"/tmp/f{index}.mp4"], timestamp=index)
        entries = gui.App.download_history(app)
        self.assertEqual(len(entries), app.HISTORY_LIMIT)
        # 保留的是最新的那批
        self.assertEqual(entries[0]["path"], f"/tmp/f{app.HISTORY_LIMIT + 9}.mp4")

    def test_playlist_batch_is_truncated_not_rejected(self):
        app = _FakeApp()
        batch = [f"/tmp/pl{i:03d}.mp4" for i in range(80)]
        self._record(app, batch, title="整个列表", platform="youtube", timestamp=1)
        entries = gui.App.download_history(app)
        self.assertEqual(len(entries), app.HISTORY_LIMIT)
        self.assertEqual(entries[0]["title"], "整个列表")
        self.assertEqual(entries[0]["platform"], "youtube",
                         "平台标识现在是 ASCII 键（与引擎/Cookie 覆盖一致）")

    def test_single_string_path_is_accepted(self):
        app = _FakeApp()
        self._record(app, "/tmp/one.mp4", timestamp=1)
        self.assertEqual(len(gui.App.download_history(app)), 1)

    def test_empty_paths_change_nothing(self):
        app = _FakeApp()
        for value in (None, [], ["", None]):
            self._record(app, value, timestamp=1)
        self.assertEqual(gui.App.download_history(app), [])
        self.assertEqual(self.saved, [], "空输入不应触发保存")

    def test_save_is_called_once_per_record(self):
        app = _FakeApp()
        self._record(app, ["/tmp/a.mp4"], timestamp=1)
        self.assertEqual(len(self.saved), 1)

    def test_dirty_settings_entries_are_filtered(self):
        app = _FakeApp({"download_history": [
            {"path": "/tmp/ok.mp4"},
            "not-a-dict",
            {"title": "没有路径"},
            None,
        ]})
        entries = gui.App.download_history(app)
        self.assertEqual([e["path"] for e in entries], ["/tmp/ok.mp4"])

    def test_non_list_history_is_ignored(self):
        for value in ("junk", 42, None):
            app = _FakeApp({"download_history": value})
            self.assertEqual(gui.App.download_history(app), [])

    def test_clear_history_empties_and_saves(self):
        app = _FakeApp({"download_history": [{"path": "/tmp/a.mp4"}]})
        gui.App.clear_history(app)
        self.assertEqual(app.settings["download_history"], [])
        self.assertEqual(len(self.saved), 1)


class HistoryPersistenceTests(unittest.TestCase):
    """跨重启保留：真实读写 settings 文件，确认历史真的留下来了。

    这里必须显式换回**真实**的 settings.load：同一个文件里的 GUI 用例会调用
    _shared_app()，而它把 settings.load 换成了内存里的假实现且不还原。不换回来
    的话本类会「读到默认值」，看起来像持久化失效，实际是测试自己没读文件。
    """

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self._path = settings_mod.SETTINGS_PATH
        self._save = settings_mod.save
        self._load = settings_mod.load
        settings_mod.SETTINGS_PATH = os.path.join(self.tempdir, "settings.json")
        settings_mod.load = _ORIGINAL_LOAD
        settings_mod.save = _ORIGINAL_SAVE

    def tearDown(self):
        settings_mod.SETTINGS_PATH = self._path
        settings_mod.save = self._save
        settings_mod.load = self._load
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_history_survives_a_real_reload(self):
        app = _FakeApp(settings_mod.load())
        gui.App.record_download(app, ["/tmp/保留的文件.mp4"], title="保留标题",
                                platform="x", timestamp=1234)
        reloaded = settings_mod.load()
        entries = reloaded["download_history"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], "/tmp/保留的文件.mp4")
        self.assertEqual(entries[0]["title"], "保留标题")
        self.assertEqual(entries[0]["platform"], "x")
        self.assertEqual(entries[0]["time"], 1234)

    def test_cleared_history_stays_cleared(self):
        app = _FakeApp(settings_mod.load())
        gui.App.record_download(app, ["/tmp/a.mp4"], timestamp=1)
        gui.App.clear_history(app)
        self.assertEqual(settings_mod.load()["download_history"], [])

    def test_other_settings_are_not_wiped(self):
        app = _FakeApp(settings_mod.load())
        app.settings["language"] = "en"
        gui.App.record_download(app, ["/tmp/a.mp4"], timestamp=1)
        self.assertEqual(settings_mod.load()["language"], "en")


class OpenPathTests(unittest.TestCase):
    """打开文件的分平台命令（不真的打开任何东西）。"""

    def setUp(self):
        self.calls = []
        self._popen = subprocess.Popen
        self._platform = sys.platform
        self._startfile = getattr(os, "startfile", None)
        subprocess.Popen = lambda args, *a, **kw: self.calls.append(args)

    def tearDown(self):
        subprocess.Popen = self._popen
        sys.platform = self._platform
        if self._startfile is None:
            if hasattr(os, "startfile"):
                del os.startfile
        else:
            os.startfile = self._startfile

    def test_darwin_uses_open(self):
        sys.platform = "darwin"
        gui.open_path("/tmp/a.mp4")
        self.assertEqual(self.calls, [["open", "/tmp/a.mp4"]])

    def test_linux_uses_xdg_open(self):
        sys.platform = "linux"
        gui.open_path("/tmp/a.mp4")
        self.assertEqual(self.calls, [["xdg-open", "/tmp/a.mp4"]])

    def test_windows_uses_startfile(self):
        sys.platform = "win32"
        opened = []
        os.startfile = lambda path: opened.append(path)
        gui.open_path("/tmp/a.mp4")
        self.assertEqual(opened, ["/tmp/a.mp4"])
        self.assertEqual(self.calls, [], "Windows 上不该调用 open/xdg-open")

    def test_empty_path_does_nothing(self):
        for value in (None, ""):
            gui.open_path(value)
        self.assertEqual(self.calls, [])

    def test_reveal_in_folder_still_targets_the_folder(self):
        """「打开所在文件夹」与「打开文件」必须走不同命令。"""
        sys.platform = "darwin"
        gui.open_in_file_manager("/tmp/a.mp4")
        self.assertEqual(self.calls, [["open", "-R", "/tmp/a.mp4"]])

    def test_time_formatting_tolerates_junk(self):
        self.assertEqual(gui._format_history_time("junk"), "")
        self.assertEqual(gui._format_history_time(None), "")
        self.assertTrue(gui._format_history_time(time.time()))


def _shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


class HistoryPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()
        cls.page = cls.app.pages["history"]

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["download_history"] = []
        self.saved_info = []
        self.opened = []
        self._showinfo = gui.messagebox.showinfo
        self._askyesno = gui.messagebox.askyesno
        self._open_path = gui.open_path
        self._reveal = gui.open_in_file_manager
        gui.messagebox.showinfo = lambda *a, **kw: self.saved_info.append(a)
        gui.open_path = lambda path: self.opened.append(path)
        gui.open_in_file_manager = lambda path: self.opened.append(("reveal", path))

    def tearDown(self):
        gui.messagebox.showinfo = self._showinfo
        gui.messagebox.askyesno = self._askyesno
        gui.open_path = self._open_path
        gui.open_in_file_manager = self._reveal
        settings_mod.save = _ORIGINAL_SAVE

    def _rows(self):
        return self.page.rows.winfo_children()

    def test_empty_state_is_shown_and_clear_disabled(self):
        self.page.refresh()
        self.assertEqual(len(self._rows()), 1, "空状态应有一张卡片")
        self.assertEqual(self.page.clear_btn._state, "disabled")

    def test_rows_appear_after_recording(self):
        gui.App.record_download(self.app, ["/tmp/视频一.mp4"], title="视频一",
                                platform="youtube", timestamp=time.time())
        self.page.refresh()
        self.assertEqual(len(self._rows()), 1)
        self.assertEqual(self.page.clear_btn._state, "normal")

    def test_open_file_on_existing_path(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
            path = handle.name
        try:
            self.page._open_file(path)
            self.assertEqual(self.opened, [path])
            self.assertEqual(self.saved_info, [])
        finally:
            os.unlink(path)

    def test_open_file_on_missing_path_warns_instead(self):
        self.page._open_file("/tmp/绝对不存在的文件-9f8a7b.mp4")
        self.assertEqual(self.opened, [], "文件不存在时不该调用系统打开")
        self.assertEqual(len(self.saved_info), 1, "应给出提示")

    def test_clear_asks_for_confirmation(self):
        gui.App.record_download(self.app, ["/tmp/a.mp4"], timestamp=time.time())
        asked = []
        gui.messagebox.askyesno = lambda *a, **kw: (asked.append(a), True)[1]
        self.page._on_clear()
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.app.download_history(), [])
        self.assertEqual(self.page.clear_btn._state, "disabled")

    def test_cancelling_clear_keeps_history(self):
        gui.App.record_download(self.app, ["/tmp/a.mp4"], timestamp=time.time())
        gui.messagebox.askyesno = lambda *a, **kw: False
        self.page._on_clear()
        self.assertEqual(len(self.app.download_history()), 1)

    def test_clear_on_empty_history_does_not_ask(self):
        asked = []
        gui.messagebox.askyesno = lambda *a, **kw: asked.append(1) or True
        self.page._on_clear()
        self.assertEqual(asked, [])

    def test_row_card_is_tall_enough_for_its_content(self):
        """GlassCard 把内部 body 的高度写死成「卡片高度 - 内边距」，内容更高就被裁。

        真实踩过：行卡片设成 96 时 body 只有 63px，而内容需要 95px，于是
        「打开文件 / 打开所在文件夹」整排被裁掉——按钮在、但看不见，纯逻辑用例
        和「行数对不对」的用例都发现不了，所以这里直接比高度。
        """
        gui.App.record_download(self.app, ["/tmp/一个比较长的文件名 [id].mp4"],
                                timestamp=time.time())
        self.page.refresh()
        self.app.update_idletasks()
        card = self.page.rows.winfo_children()[0]
        self.assertGreaterEqual(
            card.body.winfo_height(), card.body.winfo_reqheight(),
            "行内容高于卡片，按钮会被裁掉看不见")

    def test_empty_card_is_tall_enough(self):
        self.page.refresh()
        self.app.update_idletasks()
        card = self.page.rows.winfo_children()[0]
        self.assertGreaterEqual(card.body.winfo_height(), card.body.winfo_reqheight())

    def test_refresh_is_idempotent(self):
        gui.App.record_download(self.app, ["/tmp/a.mp4"], timestamp=time.time())
        for _ in range(3):
            self.page.refresh()
        self.assertEqual(len(self._rows()), 1, "重复刷新不应叠加行")

    def test_on_shown_refreshes(self):
        gui.App.record_download(self.app, ["/tmp/b.mp4"], timestamp=time.time())
        self.page.on_shown()
        self.assertEqual(len(self._rows()), 1)


class PanelHistoryWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["download_history"] = []
        self.video = self.app.pages["youtube"].video_panel
        self.image = self.app.pages["weibo"].image_panel
        self.opened = []
        # 面板代码现在住在 app/panels.py，调用的是该模块里导入的 open_path，
        # 所以面板相关的打桩要落在 panels 上（gui.open_path 只影响主窗口/页面代码）
        self._open_path = panels.open_path
        panels.open_path = lambda path: self.opened.append(path)

    def tearDown(self):
        panels.open_path = self._open_path
        settings_mod.save = _ORIGINAL_SAVE

    def test_video_panel_records_history_on_completion(self):
        self.video._info = Mock(title="示例视频")
        self.video._download_done(["/tmp/示例.mp4"])
        entries = self.app.download_history()
        self.assertEqual([e["path"] for e in entries], ["/tmp/示例.mp4"])
        self.assertEqual(entries[0]["title"], "示例视频")
        self.assertEqual(entries[0]["platform"], "youtube",
                         "平台标识现在是 ASCII 键（与引擎/Cookie 覆盖一致）")

    def test_video_panel_records_every_playlist_file(self):
        self.video._info = Mock(title="整个列表")
        self.video._download_done(["/tmp/001.mp4", "/tmp/002.mp4"])
        self.assertEqual(len(self.app.download_history()), 2)

    def test_video_open_file_button_state_follows_completion(self):
        self.video._info = Mock(title="x")
        self.video._download_done(["/tmp/示例.mp4"])
        self.assertEqual(self.video.open_file_btn._state, "normal")

    def test_video_open_file_warns_when_missing(self):
        warnings = []
        self.video._set_status = lambda *a, **kw: warnings.append((a, kw))
        self.video._last_file = "/tmp/绝不存在-a1b2.mp4"
        self.video.on_open_file()
        self.assertEqual(self.opened, [])
        self.assertEqual(len(warnings), 1)

    def test_video_open_file_opens_existing(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
            path = handle.name
        try:
            self.video._last_file = path
            self.video.on_open_file()
            self.assertEqual(self.opened, [path])
        finally:
            os.unlink(path)

    def test_video_open_file_without_target_is_safe(self):
        self.video._last_file = None
        self.video.on_open_file()
        self.assertEqual(self.opened, [])

    def test_image_panel_records_history(self):
        self.image._post = Mock(title="示例帖子", items=[])
        self.image._download_done(Mock(paths=["/tmp/1.jpg"], skipped=["/tmp/2.jpg"]))
        paths = [e["path"] for e in self.app.download_history()]
        self.assertEqual(sorted(paths), ["/tmp/1.jpg", "/tmp/2.jpg"],
                         "已存在的图片也应记入历史（它们同样能找到）")

    def test_history_page_lists_what_the_panel_recorded(self):
        """端到端：面板记录 → 历史页显示。"""
        settings_mod.save = lambda data: None
        self.video._info = Mock(title="端到端")
        self.video._download_done(["/tmp/端到端.mp4"])
        page = self.app.pages["history"]
        page.refresh()
        self.assertEqual(len(page.rows.winfo_children()), 1)


if __name__ == "__main__":
    unittest.main()
