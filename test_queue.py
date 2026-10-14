"""下载队列（#11）的测试。

设计语义（刻意如此，测试也把它锁住）：队列**只保存 URL 与平台**，不冻结清晰度、
字幕、保存位置——执行时使用当时的设置。这样队列不必理解每个面板的选项状态，也就
不会把核心下载流程卷进来；代价是「排队时的选项」不会被记住，这一点写在页面提示里。

数据层用假 App 直接调方法（不需要 Tk）；派发、面板与页面用例需要真实窗口，按约定
整个文件共用一个 App 且不销毁。
"""

import time
import tkinter as tk
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

    def test_items_keep_only_url_platform_and_kind(self):
        """不做「选项快照」：只存定位信息（url/platform）与类型（kind）。

        第 9 项加了图片入队，所以多了 kind 一个键；清晰度/字幕/保存位置这些
        执行时该用**当时**设置的选项，一个都不能进队列。
        """
        app = _FakeApp()
        self._enqueue(app, YOUTUBE)
        item = gui.App.queue_items(app)[0]
        self.assertEqual(set(item), {"url", "platform", "kind"})
        for forbidden in ("height", "audio", "subtitle", "save_dir", "quality"):
            self.assertNotIn(forbidden, item)

    def test_kind_is_normalized(self):
        self.assertEqual(gui.normalize_kind("image"), "image")
        self.assertEqual(gui.normalize_kind("IMAGE"), "image")
        for value in ("video", "", None, "垃圾", 7):
            self.assertEqual(gui.normalize_kind(value), "video", value)

    def test_image_enqueue_stores_the_kind(self):
        app = _FakeApp()
        gui.App.enqueue_url(app, WEIBO, kind="image")
        self.assertEqual(gui.App.queue_items(app)[0]["kind"], "image")

    def test_kind_defaults_to_video(self):
        app = _FakeApp()
        self._enqueue(app, YOUTUBE)
        self.assertEqual(gui.App.queue_items(app)[0]["kind"], "video")

    def test_queue_key_includes_the_kind(self):
        """同一个链接既排视频又排图片是两件事，去重键必须带类型。"""
        self.assertNotEqual(gui.queue_key({"url": "u", "kind": "video"}),
                            gui.queue_key({"url": "u", "kind": "image"}))
        self.assertEqual(gui.queue_key({"url": "u"}), ("u", "video"),
                         "老条目没有 kind，按视频算")

    def test_same_url_can_be_queued_as_video_and_image(self):
        app = _FakeApp()
        self._enqueue(app, WEIBO)
        gui.App.enqueue_url(app, WEIBO, kind="image")
        self.assertEqual(len(gui.App.queue_items(app)), 2)

    def test_batch_enqueue_dedups_per_kind(self):
        app = _FakeApp()
        added, skipped = gui.App.enqueue_urls(app, [WEIBO, WEIBO, YOUTUBE],
                                             kind="image")
        self.assertEqual((added, skipped), (2, 1), "本批重复要跳过")
        self.assertEqual({item["kind"] for item in gui.App.queue_items(app)},
                         {"image"})
        self.assertEqual(gui.App.enqueue_urls(app, [WEIBO], kind="image"), (0, 1))
        self.assertEqual(gui.App.enqueue_urls(app, [WEIBO], kind="video"), (1, 0),
                         "不同类别不算重复")

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

    def test_image_job_switches_to_the_image_panel(self):
        """第 9 项：图片任务要切到图片面板再执行。"""
        self.app.enqueue_url(WEIBO, kind="image")
        page = self.app.pages["weibo"]
        page.kind_var.set("video")            # 故意先停在视频面板
        lived = []
        image_panel = page.image_panel
        original = image_panel.on_download
        image_panel.on_download = lambda: lived.append(
            (page.kind_var.get(), image_panel.url_entry.get()))
        try:
            self.assertTrue(self.app.advance_queue())
        finally:
            image_panel.on_download = original
        self.assertEqual(lived, [("image", WEIBO)], "图片任务必须走图片面板")
        self.assertEqual(page.kind_var.get(), "image")

    def test_legacy_item_without_kind_runs_as_video(self):
        """图片入队之前存的条目没有 kind，必须仍按视频执行（老设置文件不能失效）。"""
        self.app.settings["download_queue"] = [{"url": WEIBO, "platform": "weibo"}]
        page = self.app.pages["weibo"]
        page.kind_var.set("image")
        lived = []
        video_panel = page.video_panel
        original = video_panel.on_download
        video_panel.on_download = lambda: lived.append(page.kind_var.get())
        try:
            self.assertTrue(self.app.advance_queue())
        finally:
            video_panel.on_download = original
        self.assertEqual(lived, ["video"])

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

    def test_both_panels_offer_the_queue_button(self):
        """第 9 项翻了这个断言：图片面板以前明确「不该有」队列按钮。"""
        self.assertTrue(hasattr(self.panel, "queue_btn"))
        for key in ("weibo", "x", "instagram"):
            page = self.app.pages[key]
            self.assertTrue(hasattr(page.video_panel, "queue_btn"), key)
            self.assertTrue(hasattr(page.image_panel, "queue_btn"), key)

    def test_each_panel_enqueues_with_its_own_kind(self):
        page = self.app.pages["weibo"]
        kinds = []
        original = gui.enqueue_from_entry
        gui.enqueue_from_entry = lambda app, panel, kind: kinds.append(kind)
        try:
            page.image_panel.on_enqueue()
            page.video_panel.on_enqueue()
        finally:
            gui.enqueue_from_entry = original
        self.assertEqual(kinds, ["image", "video"])

    def test_image_panel_enqueue_adds_an_image_job(self):
        panel = self.app.pages["weibo"].image_panel
        statuses = []
        original = panel._set_status
        panel._set_status = lambda text, error=False, **kw: statuses.append(
            (text, error))
        try:
            panel.url_entry.set(WEIBO)
            panel.on_enqueue()
        finally:
            panel._set_status = original
        items = self.app.queue_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "image")
        self.assertTrue(statuses and statuses[0][1] is False, "应当报成功")

    def test_image_panel_batch_enqueue_reports_counts(self):
        panel = self.app.pages["weibo"].image_panel
        statuses = []
        original = panel._set_status
        panel._set_status = lambda text, error=False, **kw: statuses.append(
            (text, error))
        try:
            panel.url_entry.set(f"看这个{WEIBO}很好 https://x.com/a/status/1 "
                                f"再来一次{WEIBO}")
            panel.on_enqueue()
        finally:
            panel._set_status = original
        items = self.app.queue_items()
        self.assertEqual(len(items), 2, "两个不同链接入队，重复的那个跳过")
        self.assertEqual({item["kind"] for item in items}, {"image"})
        self.assertFalse(statuses[0][1])
        self.assertIn("1", statuses[0][0], "跳过数要如实报告")

    def test_image_panel_without_a_link_reports_it(self):
        panel = self.app.pages["weibo"].image_panel
        statuses = []
        original = panel._set_status
        panel._set_status = lambda text, error=False, **kw: statuses.append(
            (text, error))
        try:
            panel.url_entry.set("")
            panel.on_enqueue()
        finally:
            panel._set_status = original
        self.assertEqual(self.app.queue_items(), [])
        self.assertTrue(statuses and statuses[0][1] is True)

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

    def _row_texts(self, card):
        texts = []
        for child in card.body.winfo_children():
            try:
                texts.append(str(child.cget("text")))
            except Exception:
                pass
        return texts

    def test_image_row_is_labelled(self):
        """图片任务在队列里要标出来，否则和视频任务长得一模一样。"""
        self.app.enqueue_url(WEIBO, kind="image")
        self.app.enqueue_url(YOUTUBE)
        self.page.refresh()
        self.app.update_idletasks()
        rows = self._rows()
        self.assertIn(i18n.tr("image_segment"), " ".join(self._row_texts(rows[0])))
        self.assertNotIn(i18n.tr("image_segment"),
                         " ".join(self._row_texts(rows[1])))

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


class ParseLinksTests(unittest.TestCase):
    """从一段文本里挑链接（批量粘贴的解析层）。"""

    def test_extracts_from_mixed_text(self):
        cases = {
            "看这个https://weibo.com/2549621477/Rmr4XxLMD很好":
                ["https://weibo.com/2549621477/Rmr4XxLMD"],
            "https://a.com/1\nhttps://b.com/2?x=1#f\n\n   https://c.com/3   ":
                ["https://a.com/1", "https://b.com/2?x=1#f", "https://c.com/3"],
            "（https://x.com/someone/status/123），https://www.instagram.com/p/ABC/。":
                ["https://x.com/someone/status/123", "https://www.instagram.com/p/ABC/"],
        }
        for text, expected in cases.items():
            self.assertEqual(gui.parse_links(text), expected, text)

    def test_keeps_every_occurrence_in_order(self):
        """解析不去重：否则入队时「跳过 N 个重复」会永远报 0。"""
        text = "https://a.com/1 https://b.com/2 https://a.com/1"
        self.assertEqual(gui.parse_links(text),
                         ["https://a.com/1", "https://b.com/2", "https://a.com/1"])

    def test_ignores_text_without_links(self):
        for text in ("没有链接的一段话", "", "   ", None, 12345,
                     "ftp://old.example.com/file", "www.example.com/no-scheme"):
            self.assertEqual(gui.parse_links(text), [], repr(text))

    def test_trims_chinese_and_ascii_trailing_punctuation(self):
        self.assertEqual(gui.parse_links("https://a.com/1，"),
                         ["https://a.com/1"])
        self.assertEqual(gui.parse_links("https://a.com/1)."),
                         ["https://a.com/1"])


class EnqueueManyTests(unittest.TestCase):
    """批量入队的去重与容量。"""

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app = _shared_app()
        self.app.settings["download_queue"] = []

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        self.app.settings["download_queue"] = []

    def test_adds_all_and_reports_counts(self):
        added, skipped = self.app.enqueue_urls(
            ["https://www.youtube.com/watch?v=a", WEIBO])
        self.assertEqual((added, skipped), (2, 0))
        self.assertEqual([item["url"] for item in self.app.queue_items()],
                         ["https://www.youtube.com/watch?v=a", WEIBO])
        self.assertEqual([item["platform"] for item in self.app.queue_items()],
                         ["youtube", "weibo"])

    def test_skips_links_already_queued(self):
        self.app.enqueue_url(WEIBO)
        added, skipped = self.app.enqueue_urls([WEIBO, YOUTUBE])
        self.assertEqual((added, skipped), (1, 1))
        self.assertEqual(len(self.app.queue_items()), 2)

    def test_skips_duplicates_inside_the_batch(self):
        added, skipped = self.app.enqueue_urls([WEIBO, WEIBO, WEIBO])
        self.assertEqual((added, skipped), (1, 2))
        self.assertEqual(len(self.app.queue_items()), 1)

    def test_respects_the_queue_limit_without_overreporting(self):
        limit = self.app.QUEUE_LIMIT
        self.app.enqueue_urls([f"https://a.com/{i}" for i in range(limit - 1)])
        added, skipped = self.app.enqueue_urls(
            ["https://a.com/x", "https://a.com/y", "https://a.com/z", ""])
        self.assertEqual(added, 1, "只剩一个位置，不能虚报")
        self.assertEqual(skipped, 3)
        self.assertEqual(len(self.app.queue_items()), limit)

    def test_empty_input_is_a_noop(self):
        self.assertEqual(self.app.enqueue_urls([]), (0, 0))
        self.assertEqual(self.app.enqueue_urls(None), (0, 0))
        # 空字符串算「跳过」：界面上它确实是没排上的一项
        self.assertEqual(self.app.enqueue_urls(["", "   "]), (0, 2))


class BatchPasteUiTests(unittest.TestCase):
    """队列页「批量粘贴」按钮与视频面板的多链接入队。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.app.settings["download_queue"] = []
        self.app.show_page("queue")
        self.app.update_idletasks()
        self.page = self.app.pages["queue"]
        self.page.refresh()
        self.panel = self.app.pages["youtube"].video_panel
        self.statuses = []
        self._set_status = self.panel._set_status
        self.panel._set_status = lambda text, error=False, **kw: self.statuses.append(
            (text, error))

    def tearDown(self):
        self.panel._set_status = self._set_status
        settings_mod.save = _ORIGINAL_SAVE
        self.app.settings["download_queue"] = []

    def _paste(self, text):
        self.page.app.clipboard_get = lambda: text
        self.page._on_paste_many()

    def test_button_exists(self):
        self.assertEqual(self.page.batch_btn._text, i18n.tr("queue_paste_many"))

    def test_pastes_every_link_from_the_clipboard(self):
        self._paste(f"好片 https://www.youtube.com/watch?v=a 和\n{WEIBO}，还有 "
                    "https://www.youtube.com/watch?v=a")
        self.assertEqual([item["url"] for item in self.app.queue_items()],
                         ["https://www.youtube.com/watch?v=a", WEIBO])
        self.assertIn(i18n.tr("queue_batch_added", n=2, skipped=1),
                      self.page.status_label.cget("text"))

    def test_reports_when_the_clipboard_has_no_link(self):
        self._paste("只是普通文字")
        self.assertEqual(self.app.queue_items(), [])
        self.assertEqual(self.page.status_label.cget("text"),
                         i18n.tr("queue_no_links"))

    def test_reports_when_everything_is_a_duplicate(self):
        self.app.enqueue_url(WEIBO)
        self._paste(WEIBO)
        self.assertEqual(len(self.app.queue_items()), 1)
        self.assertIn("1", self.page.status_label.cget("text"))

    def test_empty_clipboard_does_not_raise(self):
        def boom():
            raise tk.TclError("clipboard is empty")

        self.page.app.clipboard_get = boom
        self.page._on_paste_many()
        self.assertEqual(self.page.status_label.cget("text"),
                         i18n.tr("queue_no_links"))

    def test_refresh_clears_the_one_off_status(self):
        self._paste("只 是 文 字")
        self.page.refresh()
        self.assertEqual(self.page.status_label.cget("text"), "")

    def test_video_panel_queues_several_pasted_links(self):
        self.panel.url_entry.set(f"{YOUTUBE}\n{WEIBO}")
        self.panel.on_enqueue()
        self.assertEqual(len(self.app.queue_items()), 2)
        self.assertIn(i18n.tr("queue_batch_added", n=2, skipped=0),
                      self.statuses[-1][0])
        self.assertFalse(self.statuses[-1][1])

    def test_video_panel_single_link_keeps_the_old_message(self):
        self.panel.url_entry.set(YOUTUBE)
        self.panel.on_enqueue()
        self.assertEqual(self.statuses[-1][0], i18n.tr("queue_added", url=YOUTUBE))

    def test_video_panel_without_links_still_hints(self):
        self.panel.url_entry.set("没有链接")
        self.panel.on_enqueue()
        self.assertEqual(self.app.queue_items(), [])
        self.assertEqual(self.statuses[-1][0], i18n.tr("queue_need_link"))


if __name__ == "__main__":
    unittest.main()
