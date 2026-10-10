"""图片重复下载检测（#7）的回归测试。

核心诉求：重复下载同一帖子时不再产出一堆 " (1)" 副本，而是跳过已存在的
文件——与视频走 yt-dlp「已下载则跳过」的行为对齐。用假 requests 层避免联网。
"""

import os
import tempfile
import unittest
from unittest.mock import Mock

from app import gui, i18n, images


class _FakeResponse:
    def __init__(self, content):
        self.content = content
        self.headers = {"Content-Length": str(len(content))}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeSession:
    def __init__(self):
        self.headers = {}
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        return _FakeResponse(b"payload:" + url.encode())


class _FakeRequests:
    """替换 images.requests，提供 Session 与 RequestException。"""

    class RequestException(Exception):
        pass

    def __init__(self):
        self.session = _FakeSession()

    def Session(self):
        return self.session


class _FakeRequestsContext:
    def __enter__(self):
        self.fake = _FakeRequests()
        self.original = images.requests
        images.requests = self.fake
        return self.fake

    def __exit__(self, *exc_info):
        images.requests = self.original
        return False


def _items(*names):
    return [images.ImageItem(url=f"https://example.com/{name}.jpg", thumb_url="",
                             index=i, extension="jpg", filename=name)
            for i, name in enumerate(names, start=1)]


class SkipExistingTests(unittest.TestCase):
    def test_first_download_saves_all(self):
        with tempfile.TemporaryDirectory() as out, _FakeRequestsContext():
            result = images.download_images(_items("a", "b"), save_dir=out)
            self.assertEqual(len(result.paths), 2)
            self.assertEqual(result.skipped, [])
            self.assertEqual(sorted(os.listdir(out)), ["a.jpg", "b.jpg"])

    def test_second_download_skips_and_creates_no_duplicates(self):
        with tempfile.TemporaryDirectory() as out, _FakeRequestsContext() as fake:
            images.download_images(_items("a", "b"), save_dir=out)
            before = sorted(os.listdir(out))

            result = images.download_images(_items("a", "b"), save_dir=out)

            self.assertEqual(result.paths, [])
            self.assertEqual(len(result.skipped), 2)
            self.assertEqual(sorted(os.listdir(out)), before)   # 没有 " (1)" 副本
            # 第二次不应再发请求
            self.assertEqual(len(fake.session.requests), 2)

    def test_mixed_new_and_existing(self):
        with tempfile.TemporaryDirectory() as out, _FakeRequestsContext():
            images.download_images(_items("a"), save_dir=out)
            result = images.download_images(_items("a", "b"), save_dir=out)
            self.assertEqual([os.path.basename(p) for p in result.paths], ["b.jpg"])
            self.assertEqual([os.path.basename(p) for p in result.skipped], ["a.jpg"])
            self.assertEqual(sorted(os.listdir(out)), ["a.jpg", "b.jpg"])

    def test_disable_skip_keeps_numbered_copies(self):
        """关掉开关后回到旧行为：重名另存为带序号的副本，不覆盖原文件。"""
        with tempfile.TemporaryDirectory() as out, _FakeRequestsContext():
            images.download_images(_items("a"), save_dir=out)
            result = images.download_images(_items("a"), save_dir=out,
                                            skip_existing=False)
            self.assertEqual(len(result.paths), 1)
            self.assertEqual(result.skipped, [])
            self.assertEqual(sorted(os.listdir(out)), ["a (1).jpg", "a.jpg"])

    def test_primary_path_is_sanitized(self):
        path = images._primary_path("/tmp", 'bad/name:with*chars', "jpg")
        self.assertEqual(path, os.path.join("/tmp", "bad_name_with_chars.jpg"))


class _NoDockApp:
    """只提供面板需要的接口。

    dock_status_enabled() 明确返回 False：Mock 的同名属性会自动生成且恒为真，
    那样 _dock_done 会真的去碰 Dock，测试就不再是无副作用的了。
    """

    def dock_status_enabled(self):
        return False


class DownloadDoneReportingTests(unittest.TestCase):
    """界面汇报：必须能区分「新下载」「部分跳过」「全部已存在」。"""

    def _panel(self):
        panel = Mock()
        panel.app = _NoDockApp()
        return panel

    def test_all_new_reports_completed(self):
        panel = self._panel()
        gui.ImagePanel._download_done(
            panel, images.DownloadResult(paths=["/tmp/dir/a.jpg"], skipped=[]))
        args, kwargs = panel._set_status.call_args
        self.assertEqual(args[0], i18n.tr("images_complete", n=1, folder="dir"))
        self.assertTrue(kwargs.get("ok"))

    def test_partial_skip_is_reported(self):
        panel = self._panel()
        gui.ImagePanel._download_done(
            panel, images.DownloadResult(paths=["/tmp/dir/b.jpg"],
                                         skipped=["/tmp/dir/a.jpg"]))
        text = panel._set_status.call_args[0][0]
        self.assertEqual(text, i18n.tr("images_complete_skipped", n=1, skipped=1,
                                       folder="dir"))

    def test_all_skipped_does_not_report_zero_downloaded(self):
        panel = self._panel()
        gui.ImagePanel._download_done(
            panel, images.DownloadResult(paths=[], skipped=["/tmp/dir/a.jpg",
                                                           "/tmp/dir/b.jpg"]))
        text = panel._set_status.call_args[0][0]
        self.assertEqual(text, i18n.tr("images_all_skipped", n=2))
        # 跳过的文件也要留给「打开所在文件夹」，否则按钮没有目标
        self.assertEqual(panel._last_paths, ["/tmp/dir/a.jpg", "/tmp/dir/b.jpg"])

    def test_skip_reporting_is_localized_not_hardcoded(self):
        for key in ("images_all_skipped", "images_complete_skipped",
                    "image_skipped_progress", "skip_existing_images"):
            self.assertNotEqual(i18n.tr(key), key)


if __name__ == "__main__":
    unittest.main()
