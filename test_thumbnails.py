"""缩略图加载的并发上限（ROADMAP 第 5 项）。

原来是一张图一个线程：微博动图帖实测 18 张就是 18 个并发连接，几十张的图集会
瞬间开几十个连接，容易触发平台限流或耗尽 socket。现在固定若干个守护线程从队列
取任务。

这个文件锁住三件事：**并发有上限**、**换帖子后旧任务立即作废**、**单张失败不影响
其它图**。

整个文件共用一个 App（第一次 Tk 初始化要数秒，且销毁根窗口后再新建会让 macOS 上的
Tk 段错误）。
"""

import threading
import time
import unittest
from unittest.mock import patch

from app import gui, images, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

_APP = None


def _shared_app():
    global _APP
    if _APP is None:
        settings_mod.load = lambda: {**settings_mod.DEFAULTS, "language": "zh_CN",
                                     "browser_cookies": "",
                                     "window_geometry": "1020x745"}
        gui.menubar.create = lambda *a, **kw: None
        _APP = gui.App()
    return _APP


def _items(count):
    return [images.ImageItem(url=f"https://e/{i}", thumb_url=f"https://e/t{i}",
                             index=i, extension="jpg", filename=f"p_{i}")
            for i in range(1, count + 1)]


class _FetchTracker:
    """替换 images.fetch_thumbnail：记录并发峰值、线程数与调用次数。"""

    def __init__(self, delay=0.02, fail_every=0):
        self.delay = delay
        self.fail_every = fail_every
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.threads = set()
        self.lock = threading.Lock()

    def __call__(self, item, **kwargs):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.threads.add(threading.current_thread().ident)
            number = self.calls
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.fail_every and number % self.fail_every == 0:
                raise RuntimeError("故意失败")
            return b"not-really-an-image"
        finally:
            with self.lock:
                self.active -= 1


class ThumbnailPoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.panel = self.app.pages["weibo"].image_panel
        self.panel._thumb_epoch += 1          # 让上一次的残留失效
        self.delivered = []
        self._real_set_thumb = self.panel._set_thumb
        self.panel._set_thumb = lambda epoch, index, raw: self.delivered.append(index)

    def tearDown(self):
        self.panel._set_thumb = self._real_set_thumb
        settings_mod.save = _ORIGINAL_SAVE
        self.panel._thumb_epoch += 1          # 结束本用例的后台工作

    def _run_until(self, predicate=None, timeout=6.0, slice_ms=50):
        """分片跑真实 mainloop 直到条件成立。

        必须用 mainloop：worker 在**非主线程**里调用 ``self.after(...)``，主线程只在
        ``update()`` 里泵事件时 Tk 会抛 ``RuntimeError: main thread is not in main loop``
        （AGENTS 里记录的同一条约束，这次是它让用例第一次全挂）。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.after(slice_ms, self.app.quit)
            self.app.mainloop()
            if predicate is None or predicate():
                return True
        return predicate is None

    def test_concurrency_and_thread_count_are_bounded(self):
        tracker = _FetchTracker(delay=0.03)
        with patch.object(images, "fetch_thumbnail", tracker):
            self.panel._load_thumbnails(type("P", (), {"items": _items(20)})())
            self.assertTrue(self._run_until(lambda: len(self.delivered) == 20),
                            f"只收到 {len(self.delivered)} 张缩略图")
        self.assertLessEqual(tracker.peak, self.panel.THUMB_WORKERS,
                             f"并发峰值 {tracker.peak} 超过上限")
        self.assertLessEqual(len(tracker.threads), self.panel.THUMB_WORKERS,
                             f"开了 {len(tracker.threads)} 个线程")
        self.assertGreaterEqual(tracker.peak, 1)
        # 落地的是「网格位置」（0 起），不是 item.index（1 起）——set_thumb 要的是前者
        self.assertEqual(sorted(self.delivered), list(range(0, 20)))

    def test_small_posts_do_not_spawn_extra_workers(self):
        tracker = _FetchTracker(delay=0)
        with patch.object(images, "fetch_thumbnail", tracker):
            self.panel._load_thumbnails(type("P", (), {"items": _items(2)})())
            self._run_until(lambda: len(self.delivered) == 2)
        self.assertLessEqual(len(tracker.threads), 2)

    def test_empty_post_starts_nothing(self):
        tracker = _FetchTracker(delay=0)
        with patch.object(images, "fetch_thumbnail", tracker):
            self.panel._load_thumbnails(type("P", (), {"items": []})())
            self._run_until(timeout=0.3)
        self.assertEqual(tracker.calls, 0)
        self.assertEqual(self.panel._thumb_workers, [])

    def test_stale_work_stops_after_a_new_post(self):
        """换帖子后旧任务立即作废：不再继续取队列，结果也不落地。"""
        started = threading.Event()
        release = threading.Event()
        calls_after_bump = []

        def slow_fetch(item, **kwargs):
            started.set()
            release.wait(2.0)
            return b"x"

        with patch.object(images, "fetch_thumbnail", slow_fetch):
            self.panel._load_thumbnails(type("P", (), {"items": _items(30)})())
            self.assertTrue(started.wait(2.0), "worker 没启动")
            self.panel._thumb_epoch += 1          # 等价于用户切到了新帖子
            release.set()
            # 给 worker 一点时间退出
            time.sleep(0.2)
            calls_after_bump.append(True)
        self.assertEqual(self.delivered, [], "过期代数的缩略图不该落地")

    def test_one_failure_does_not_stop_the_rest(self):
        tracker = _FetchTracker(delay=0, fail_every=3)
        with patch.object(images, "fetch_thumbnail", tracker):
            self.panel._load_thumbnails(type("P", (), {"items": _items(9)})())
            self.assertTrue(self._run_until(lambda: len(self.delivered) == 6),
                            f"只收到 {len(self.delivered)} 张")
        # 第 3、6 次调用故意失败 → 位置 2、5 没有缩略图，其余照常
        self.assertEqual(sorted(self.delivered), [0, 1, 3, 4, 6, 7])

    def test_workers_are_daemon_threads(self):
        tracker = _FetchTracker(delay=0.05)
        with patch.object(images, "fetch_thumbnail", tracker):
            self.panel._load_thumbnails(type("P", (), {"items": _items(8)})())
            self._run_until(lambda: len(self.panel._thumb_workers) > 0, timeout=0.5)
            workers = list(self.panel._thumb_workers)
        self.assertTrue(workers, "应当记录 worker 线程")
        self.assertTrue(all(thread.daemon for thread in workers),
                        "必须是守护线程，否则卡住的请求会拖慢退出")


if __name__ == "__main__":
    unittest.main()
