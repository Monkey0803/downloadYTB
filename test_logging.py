"""持久化日志的测试（ROADMAP 第 2 项）。

三条约束各有用例盯着：
1. **绝不抛异常**——日志失败不能影响下载（只读/不可写目录都要静默）。
2. **不记敏感值**——键名含 cookie/token 的字段打码，URL 丢掉查询串。
3. **有界**——超过上限轮转，最多保留固定份数。

另外验证「一条事件一行」的格式不被字段里的换行破坏（日志要能被 grep/脚本解析）。
所有用例都把日志目录指到临时目录，绝不碰用户真实日志。
"""

import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import applog, diagnostics, i18n


class _LogCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="applog_test_")
        self._original_env = os.environ.get(applog.ENV_LOG_DIR)
        os.environ[applog.ENV_LOG_DIR] = os.path.join(self.root, "logs")
        self._original_max = applog.MAX_BYTES

    def tearDown(self):
        applog.MAX_BYTES = self._original_max
        if self._original_env is None:
            os.environ.pop(applog.ENV_LOG_DIR, None)
        else:
            os.environ[applog.ENV_LOG_DIR] = self._original_env
        shutil.rmtree(self.root, ignore_errors=True)

    def _lines(self):
        with open(applog.log_path(), encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if line]


class WriteTests(_LogCase):
    def test_writes_one_line_per_event_and_creates_the_directory(self):
        applog.log_event("download_start", url="https://e/a", quality="1080p")
        self.assertTrue(os.path.isdir(applog.log_dir()), "应当自动建目录")
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertIn("download_start", line)
        self.assertIn("quality=1080p", line)
        self.assertIn("url=https://e/a", line)
        # 时间戳前缀，便于按时间排查
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

    def test_fields_are_sorted_for_grepping(self):
        applog.log_event("e", zebra=1, alpha=2, mid=3)
        line = self._lines()[0]
        self.assertLess(line.index("alpha="), line.index("mid="))
        self.assertLess(line.index("mid="), line.index("zebra="))

    def test_newlines_in_fields_do_not_break_the_format(self):
        applog.log_event("e", error="第一行\n第二行\t带缩进")
        lines = self._lines()
        self.assertEqual(len(lines), 1, "字段里的换行不能把一条事件拆成多行")
        self.assertIn("第一行 第二行 带缩进", lines[0])

    def test_appends_across_calls(self):
        for index in range(3):
            applog.log_event("tick", index=index)
        self.assertEqual(len(self._lines()), 3)

    def test_tail_reads_the_end(self):
        for index in range(5):
            applog.log_event("tick", index=index)
        tail = applog.tail(2).strip().splitlines()
        self.assertEqual(len(tail), 2)
        self.assertIn("index=4", tail[-1])

    def test_tail_is_empty_when_there_is_no_log(self):
        self.assertEqual(applog.tail(), "")

    def test_test_runs_write_to_a_separate_file(self):
        """本文件就是 ``test_`` 开头的主模块，所以不带覆盖时应当落到 logs/test/。"""
        os.environ.pop(applog.ENV_LOG_DIR, None)
        path = applog.log_path()
        self.assertTrue(path.endswith(os.path.join("logs", "test",
                                                  "videodownloader.log")), path)


class RedactionTests(_LogCase):
    def test_sensitive_keys_are_redacted(self):
        applog.log_event("cookie_probe", cookies="chrome", cookie_file="/Users/x/c.txt",
                         token="abc", password="hunter2", session="s3cr3t", status="ok")
        line = self._lines()[0]
        for leaked in ("chrome", "/Users/x/c.txt", "abc", "hunter2", "s3cr3t"):
            self.assertNotIn(leaked, line, f"{leaked!r} 不该出现在日志里")
        self.assertIn("status=ok", line, "非敏感字段要照常记录")
        self.assertEqual(line.count(applog.i18n.tr(applog.REDACTED_KEY)), 5)

    def test_urls_lose_their_query_string(self):
        applog.log_event("probe_failed",
                         url="https://weibo.com/2549621477/Rmr4XxLMD?token=secret&x=1")
        line = self._lines()[0]
        self.assertIn("Rmr4XxLMD", line, "路径里的帖子 ID 要保留，够定位问题")
        self.assertNotIn("secret", line)
        self.assertNotIn("token=", line)

    def test_lists_are_joined(self):
        applog.log_event("download_done", files=["/a/1.mp4", "/a/2.mp4"])
        self.assertIn("files=/a/1.mp4,/a/2.mp4", self._lines()[0])


class SafetyTests(_LogCase):
    def test_never_raises_when_the_directory_cannot_be_created(self):
        blocker = os.path.join(self.root, "blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("我是文件，不是目录")
        os.environ[applog.ENV_LOG_DIR] = os.path.join(blocker, "logs")
        applog.log_event("app_start", version="1.10.0")   # 不应抛异常
        applog.log_exception("boom", ValueError("x"))

    def test_log_exception_records_type_and_message(self):
        applog.log_exception("download_failed", ValueError("格式不可用"), panel="youtube")
        line = self._lines()[0]
        self.assertIn("error=ValueError: 格式不可用", line)
        self.assertIn("panel=youtube", line)

    def test_concurrent_writes_keep_every_line_intact(self):
        threads = 8
        per_thread = 40

        def worker(number):
            for index in range(per_thread):
                applog.log_event("concurrent", worker=number, index=index)

        workers = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
        for item in workers:
            item.start()
        for item in workers:
            item.join()
        lines = self._lines()
        self.assertEqual(len(lines), threads * per_thread, "并发写不能丢行")
        for line in lines:
            self.assertTrue(line.endswith("index=" + line.rsplit("index=", 1)[1]))
            self.assertIn("worker=", line)


class RotationTests(_LogCase):
    def test_rotates_and_keeps_a_bounded_number_of_files(self):
        applog.MAX_BYTES = 400          # 每行约 60 字节，写十几行就会触发轮转
        for index in range(40):
            applog.log_event("tick", index=index, pad="x" * 40)
        files = sorted(name for name in os.listdir(applog.log_dir())
                       if name.startswith("videodownloader.log"))
        self.assertIn("videodownloader.log", files)
        self.assertIn("videodownloader.log.1", files)
        self.assertLessEqual(len(files), applog.KEEP_FILES + 1,
                             f"轮转后不该无限增长：{files}")
        self.assertTrue(self._lines(), "当前日志里应当有内容")
        for name in files:
            size = os.path.getsize(os.path.join(applog.log_dir(), name))
            self.assertLessEqual(size, applog.MAX_BYTES * 2,
                                 f"{name} 体积异常（轮转没生效？）")


class DiagnosticsIntegrationTests(_LogCase):
    def test_record_error_also_lands_in_the_log(self):
        diagnostics.record_error("下载失败：网络错误")
        content = applog.tail()
        self.assertIn("ui_error", content)
        self.assertIn("下载失败：网络错误", content)
        self.assertEqual(diagnostics.last_error(), "下载失败：网络错误")

    def test_report_contains_the_log_path_but_not_its_content(self):
        applog.log_event("download_failed", error="SECRET-CONTENT-42")
        report = diagnostics.build_report({}, probe=diagnostics.browser_cookies.ProbeResult(
            diagnostics.browser_cookies.PROBE_OK, cookie_count=1))
        log_line = next(line for line in report.splitlines()
                        if i18n.tr("diag_log_path", value="X").split("X")[0].strip()
                        and line.startswith("- ") and "videodownloader" in line)
        self.assertTrue(log_line.startswith("- "), "诊断行要跟其它行同样格式")
        self.assertIn("videodownloader.log", log_line)
        self.assertIn(applog.log_dir().split(os.sep)[-1], report)
        self.assertNotIn("SECRET-CONTENT-42", report, "报告里只该有路径，不该有日志内容")
        home = os.path.expanduser("~")
        self.assertNotIn(home + os.sep, report, "家目录要替换成 ~")
        # 报告里出现的应是净化后的路径（测试目录不在 HOME 下时替换是无操作，同样成立）
        self.assertIn(applog.log_path().replace(home, "~"), report)


if __name__ == "__main__":
    unittest.main()
