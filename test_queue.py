"""下载队列（#11）的测试。

设计语义（刻意如此，测试也把它锁住）：队列**只保存 URL 与平台**，不冻结清晰度、
字幕、保存位置——执行时使用当时的设置。这样队列不必理解每个面板的选项状态，也就
不会把核心下载流程卷进来；代价是「排队时的选项」不会被记住，这一点写在页面提示里。

数据层用假 App 直接调方法（不需要 Tk）；派发、面板与页面用例需要真实窗口，按约定
整个文件共用一个 App 且不销毁。
"""

import time
import unittest
from unittest.mock import Mock

from app import gui, i18n, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

YOUTUBE = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
WEIBO = "https://weibo.com/2569360232/Rjo6Fqvcw"

_APP = None


class _FakeApp:
    QUEUE_LIMIT = gui.App.QUEUE_LIMIT
    # 数据层方法全部借用真实实现，测试只提供 settings 与刷新钩子；
    # 抄一遍逻辑就会在实现改动时失去意义（历史页测试的教训）
    queue_items = gui.App.queue_items
    set_queue_items = gui.App.set_queue_items
    enqueue_url = gui.App.enqueue_url
    dequeue_item = gui.App.dequeue_item
    clear_queue = gui.App.clear_queue
    queue_autostart_enabled = gui.App.queue_autostart_enabled
    set_queue_autostart = gui.App.set_queue_autostart

    def __init__(self, settings=None):
        self.settings = settings if settings is not None else {}
        self.refreshed = 0

    def _refresh_queue_page(self):
        self.refreshed += 1


class QueueDataTests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        settings_mod.save = lambda data: self.saved.append(dict(data))

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def _enqueue(self, app, url, platform=""):
        return gui.App.enqueue_url(app, url, platform)

    def test_enqueue_stores_url_and_detected_platform(self):
        app = _FakeApp()
        self.assertTrue(self._enqueue(app, YOUTUBE + "&list=PL"))  # 检测应忽略参数
        items = gui.App.queue_items(app)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], YOUTUBE + "&list=PL")
        self.assertEqual(items[0]["platform"], "youtube")

    def test_explicit_platform_wins(self):
        app = _FakeApp()
        self._enqueue(app, WEIBO, "weibo")
        self.assertEqual(gui.App.queue_items(app)[0]["platform"], "weibo")

    def test_items_keep_only_url_and_platform(self):
        """不做「选项快照」：存进去的只有这两个键（语义写进文档与提示）。"""
        app = _FakeApp()
        self._enqueue(app, YOUTUBE)
        self.assertEqual(set(gui.App.queue_items(app)[0]), {"url", "platform"})

    def test_empty_url_is_rejected(self):
        app = _FakeApp()
        for value in ("", "   ", None):
            self.assertFalse(self._enqueue(app, value))
        self.assertEqual(gui.App.queue_items(app), [])
        self.assertEqual(self.saved, [])

    def test_order_is_preserved(self):
        app = _FakeApp()
        for index in range(3):
            self._enqueue(app, f"https://www.youtube.com/watch?v=v{index}")
        urls = [item["url"] for item in gui.App.queue_items(app)]
        self.assertTrue(urls[0].endswith("v0"))
        self.assertTrue(urls[-1].endswith("v2"))

    def test_dequeue_removes_the_first(self):
        app = _FakeApp()
        self._enqueue(app, WEIBO)
        item = gui.App.dequeue_item(app)
        self.assertEqual(item["url"], WEIBO)
        self.assertEqual(gui.App.queue_items(app), [])

    def test_dequeue_on_empty_returns_none(self):
        self.assertIsNone(gui.App.dequeue_item(_FakeApp()))

    def test_limit_is_enforced(self):
        app = _FakeApp()
        for index in range(app.QUEUE_LIMIT + 5):
            self._enqueue(app, f"https://www.youtube.com/watch?v=v{index}")
        items = gui.App.queue_items(app)
        self.assertEqual(len(items), app.QUEUE_LIMIT)
        self.assertTrue(items[-1]["url"].endswith(f"v{app.QUEUE_LIMIT + 4}"))

    def test_dirty_entries_are_filtered(self):
        app = _FakeApp({"download_queue": [{"url": YOUTUBE}, "junk", {}, None]})
        self.assertEqual(len(gui.App.queue_items(app)), 1)

    def test_non_list_queue_is_ignored(self):
        for value in ("junk", 3, None):
            self.assertEqual(gui.App.queue_items(_FakeApp({"download_queue": value})), [])

    def test_clear_empties_and_saves(self):
        app = _FakeApp({"download_queue": [{"url": YOUTUBE}]})
        gui.App.clear_queue(app)
        self.assertEqual(app.settings["download_queue"], [])
        self.assertEqual(len(self.saved), 1)

    def test_autostart_defaults_to_on_and_persists(self):
        app = _FakeApp()
        self.assertTrue(gui.App.queue_autostart_enabled(app))
        gui.App.set_queue_autostart(app, False)
        self.assertFalse(gui.App.queue_autostart_enabled(app))
        self.assertFalse(self.saved[-1]["queue_autostart"])


def _shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


class QueueDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["download_queue"] = []
        self.app.settings["queue_autostart"] = True
        self.panel = self.app.pages["youtube"].video_panel
        self.calls = []
        self._on_download = self.panel.on_download
        self.panel.on_download = lambda: self.calls.append(self.panel.url_entry.get())

    def tearDown(self):
        self.panel.on_download = self._on_download
        settings_mod.save = _ORIGINAL_SAVE

    def test_advance_takes_the_first_item_and_starts_it(self):
        self.app.enqueue_url(YOUTUBE)
        self.app.enqueue_url(WEIBO)
        self.assertTrue(self.app.advance_queue())
        self.assertEqual(self.calls, [YOUTUBE])
        self.assertEqual(self.panel.url_entry.get(), YOUTUBE)
        self.assertEqual(self.app.current_page, "youtube")
        remaining = [item["url"] for item in self.app.queue_items()]
        self.assertEqual(remaining, [WEIBO], "取走的那条要出队")

    def test_advance_switches_to_the_right_platform(self):
        self.app.enqueue_url(WEIBO)
        lived = []
        weibo_panel = self.app.pages["weibo"].video_panel
        original = weibo_panel.on_download
        weibo_panel.on_download = lambda: lived.append(weibo_panel.url_entry.get())
        try:
            self.assertTrue(self.app.advance_queue())
        finally:
            weibo_panel.on_download = original
        self.assertEqual(lived, [WEIBO])
        self.assertEqual(self.app.current_page, "weibo")

    def test_advance_respects_the_autostart_switch(self):
        self.app.enqueue_url(YOUTUBE)
        self.app.settings["queue_autostart"] = False
        self.assertFalse(self.app.advance_queue())
        self.assertEqual(self.calls, [], "关掉自动继续后不该自己跑")
        self.assertEqual(len(self.app.queue_items()), 1, "任务要留在队列里")
        self.assertTrue(self.app.advance_queue(force=True), "手动按钮应当能跑")
        self.assertEqual(self.calls, [YOUTUBE])

    def test_advance_waits_while_any_panel_is_busy(self):
        self.app.enqueue_url(YOUTUBE)
        other = self.app.pages["weibo"].video_panel
        other._busy = True
        try:
            self.assertFalse(self.app.advance_queue())
        finally:
            other._busy = False
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.app.queue_items()), 1)

    def test_advance_on_empty_queue_does_nothing(self):
        self.assertFalse(self.app.advance_queue())
        self.assertEqual(self.calls, [])

    def test_item_with_unknown_platform_is_dropped(self):
        self.app.set_queue_items([{"url": "https://example.com/x", "platform": "nope"}])
        self.assertFalse(self.app.advance_queue())
        self.assertEqual(self.app.queue_items(), [], "无法执行的条目不该一直堵着队列")

    def test_media_page_kind_is_forced_to_video(self):
        """X/微博这类页面有视频/图片切换，队列里排的都是视频链接。"""
        self.app.enqueue_url(WEIBO)
        page = self.app.pages["weibo"]
        page.kind_var.set("image")
        lived = []
        panel = page.video_panel
        original = panel.on_download
        panel.on_download = lambda: lived.append(page.kind_var.get())
        try:
            self.app.advance_queue()
        finally:
            panel.on_download = original
        self.assertEqual(lived, ["video"])

    def test_finishing_a_download_schedules_an_advance(self):
        scheduled = []
        original = self.app.after
        self.app.after = lambda ms, fn=None, *a: (scheduled.append(fn), "id")[1]
        try:
            self.panel._reset_after_download()
        finally:
            self.app.after = original
        self.assertTrue(scheduled, "结束下载后应当安排推进队列")
        self.assertIn(self.app.advance_queue, scheduled)


class QueuePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["download_queue"] = []
        self.panel = self.app.pages["youtube"].video_panel
        self.statuses = []
        self._set_status = self.panel._set_status
        self.panel._set_status = lambda text, error=False, **kw: self.statuses.append(
            (text, error))

    def tearDown(self):
        self.panel._set_status = self._set_status
        settings_mod.save = _ORIGINAL_SAVE

    def test_only_video_panels_offer_the_queue_button(self):
        self.assertTrue(hasattr(self.panel, "queue_btn"))
        for key in ("weibo", "x", "instagram"):
            image_panel = self.app.pages[key].image_panel
            self.assertFalse(hasattr(image_panel, "queue_btn"),
                             f"{key} 的图片面板不该有队列按钮")

    def test_enqueue_adds_the_typed_url(self):
        self.panel.url_entry.set(YOUTUBE)
        self.panel.on_enqueue()
        self.assertEqual([item["url"] for item in self.app.queue_items()], [YOUTUBE])
        self.assertTrue(self.statuses and not self.statuses[0][1], "应给出成功提示")

    def test_enqueue_without_a_url_explains(self):
        self.panel.url_entry.set("")
        self.panel.on_enqueue()
        self.assertEqual(self.app.queue_items(), [])
        self.assertEqual(self.statuses[0][0], i18n.tr("queue_need_link"))
        self.assertTrue(self.statuses[0][1], "应作为错误提示")


class QueuePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()
        cls.page = cls.app.pages["queue"]

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["download_queue"] = []
        self.app.settings["queue_autostart"] = True
        self.page.refresh()
        self.asked = []

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def _rows(self):
        return self.page.rows.winfo_children()

    def test_empty_state_disables_the_actions(self):
        self.assertEqual(len(self._rows()), 1)
        self.assertEqual(self.page.start_btn._state, "disabled")
        self.assertEqual(self.page.clear_btn._state, "disabled")

    def test_rows_follow_the_queue(self):
        self.app.enqueue_url(YOUTUBE)
        self.app.enqueue_url(WEIBO)
        self.page.refresh()
        self.assertEqual(len(self._rows()), 2)
        self.assertEqual(self.page.start_btn._state, "normal")

    def test_row_content_is_not_clipped(self):
        """GlassCard 会把内部高度写死，行内容更高就会被裁（历史页踩过一次）。"""
        self.app.enqueue_url(YOUTUBE)
        self.page.refresh()
        self.app.update_idletasks()
        card = self._rows()[0]
        self.assertGreaterEqual(card.body.winfo_height(), card.body.winfo_reqheight())

    def test_remove_deletes_only_that_row(self):
        self.app.enqueue_url(YOUTUBE)
        self.app.enqueue_url(WEIBO)
        self.page.refresh()
        self.page._on_remove(0)
        self.assertEqual([item["url"] for item in self.app.queue_items()], [WEIBO])

    def test_remove_with_a_stale_index_is_safe(self):
        self.page._on_remove(5)          # 不应抛错
        self.assertEqual(self.app.queue_items(), [])

    def test_clear_asks_and_can_be_cancelled(self):
        self.app.enqueue_url(YOUTUBE)
        gui.messagebox.askyesno = lambda *a, **kw: False
        self.page._on_clear()
        self.assertEqual(len(self.app.queue_items()), 1)
        gui.messagebox.askyesno = lambda *a, **kw: True
        self.page._on_clear()
        self.assertEqual(self.app.queue_items(), [])

    def test_autostart_toggle_persists(self):
        self.page.autostart_var.set(False)
        self.page._on_autostart_change()
        self.assertFalse(self.app.settings["queue_autostart"])
        self.assertTrue(self.app.queue_autostart_enabled() is False)

    def test_start_button_forces_the_next_item(self):
        self.app.settings["queue_autostart"] = False
        self.app.enqueue_url(YOUTUBE)
        self.page.refresh()
        calls = []
        panel = self.app.pages["youtube"].video_panel
        original = panel.on_download
        panel.on_download = lambda: calls.append(1)
        try:
            self.page._on_start_next()
        finally:
            panel.on_download = original
        self.assertEqual(calls, [1], "手动按钮要忽略自动继续开关")

    def test_refresh_is_idempotent(self):
        self.app.enqueue_url(YOUTUBE)
        for _ in range(3):
            self.page.refresh()
        self.assertEqual(len(self._rows()), 1)

    def test_page_is_in_the_sidebar(self):
        self.assertIn("queue", self.app.nav_items)
        self.assertIn("queue", self.app.pages)


if __name__ == "__main__":
    unittest.main()
