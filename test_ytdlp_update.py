"""yt-dlp 本机更新的测试。

核心不变量（都对应真实约束，见 `app/ytdlp_update.py` 的模块说明）：

1. ``activate()`` **不能导入** yt_dlp——一旦导入就进了 ``sys.modules``，再注入
   ``sys.path`` 完全无效，用户装的新版会被静默忽略。这里有一条子进程测试专门盯它。
2. 安装是原子的：任何失败都不留下半成品，结论回落到「用内置副本」。
3. 读不出内置版本号时（冻结包常见）用户明确装过的副本优先。

所有用例都把数据目录指到临时目录，不碰用户真实的 yt-dlp 安装。
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from app import ytdlp_update as u

REPO = os.path.dirname(os.path.abspath(__file__))


# ---------- 造一个 wheel / 假 HTTP ----------


def make_wheel(path, version, extra=(), package="yt_dlp"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{package}/__init__.py", "# fake\n")
        zf.writestr(f"{package}/version.py", f'__version__ = "{version}"\n')
        for name in extra:
            zf.writestr(name, "x")
    return path


class _FakeResponse:
    def __init__(self, content, headers=None):
        self.content = content
        self.headers = headers or {"Content-Length": str(len(content))}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        step = max(1, chunk_size)
        for start in range(0, len(self.content), step):
            yield self.content[start:start + step]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeSession:
    """按 URL 返回预置内容；记录请求以便断言。"""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(url)
        value = self.responses.get(url)
        if value is None:
            raise RuntimeError(f"unexpected url: {url}")
        if isinstance(value, Exception):
            raise value
        return value


class _TempRootCase(unittest.TestCase):
    """把 yt-dlp 数据目录指到临时目录。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ytdlp_test_")
        self._original_data_dir = u.data_dir
        u.data_dir = lambda: self.root

    def tearDown(self):
        u.data_dir = self._original_data_dir
        shutil.rmtree(self.root, ignore_errors=True)

    def install_fake(self, version):
        """在数据目录里放一份「已安装」的副本。"""
        target = os.path.join(u.managed_root(), version, "yt_dlp")
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "__init__.py"), "w", encoding="utf-8") as fh:
            fh.write("# fake\n")
        with open(os.path.join(target, "version.py"), "w", encoding="utf-8") as fh:
            fh.write(f'__version__ = "{version}"\n')
        return os.path.dirname(target)


class VersionTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(u.parse_version("2026.08.19"), (2026, 8, 19))
        self.assertEqual(u.parse_version("2026.08.19.1"), (2026, 8, 19, 1))
        self.assertEqual(u.parse_version(" 2026.8.9 "), (2026, 8, 9))

    def test_parse_version_rejects_garbage(self):
        for value in ("", None, "nightly", "2026.08.beta", "v2026.08.19"):
            self.assertEqual(u.parse_version(value), (), repr(value))

    def test_is_newer(self):
        self.assertTrue(u.is_newer("2026.10.01", "2026.08.19"))
        self.assertTrue(u.is_newer("2026.08.19.1", "2026.08.19"))
        self.assertFalse(u.is_newer("2026.08.19", "2026.10.01"))
        self.assertFalse(u.is_newer("2026.08.19", "2026.08.19"))
        self.assertFalse(u.is_newer("nightly", "2026.08.19"))

    def test_unknown_bundled_version_prefers_the_user_copy(self):
        """冻结包里读不出内置版本号时，用户明确装过的副本优先。"""
        self.assertTrue(u.is_newer("2026.08.19", ""))
        self.assertTrue(u.should_use_managed("2026.08.19", ""))

    def test_should_use_managed(self):
        self.assertFalse(u.should_use_managed("", "2026.08.19"))
        self.assertFalse(u.should_use_managed("nightly", "2026.08.19"))
        self.assertFalse(u.should_use_managed("2026.08.19", "2026.08.19"))
        self.assertTrue(u.should_use_managed("2026.09.01", "2026.08.19"))

    def test_read_version_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "version.py"), "w", encoding="utf-8") as fh:
                fh.write("__version__ = '2026.01.02'\n# other\n")
            self.assertEqual(u.read_version_file(tmp), "2026.01.02")
            self.assertEqual(u.read_version_file(os.path.join(tmp, "nope")), "")
            with open(os.path.join(tmp, "version.py"), "w", encoding="utf-8") as fh:
                fh.write("# no version here\n")
            self.assertEqual(u.read_version_file(tmp), "")

    def test_plain_version_file_is_used_when_version_py_is_unreadable(self):
        """冻结包场景：只能靠构建时写入的版本文件。"""
        from unittest.mock import patch as _patch

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, u.BUNDLED_VERSION_FILENAME), "w",
                      encoding="utf-8") as fh:
                fh.write("2030.01.02\n")
            with _patch.object(u, "bundled_dir", lambda: ""), \
                 _patch.object(u, "bundle_root", lambda: tmp):
                self.assertEqual(u.bundled_version(), "2030.01.02")

    def test_garbage_version_file_is_ignored(self):
        from unittest.mock import patch as _patch

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, u.BUNDLED_VERSION_FILENAME), "w",
                      encoding="utf-8") as fh:
                fh.write("nightly\n")
            with _patch.object(u, "bundled_dir", lambda: ""), \
                 _patch.object(u, "bundle_root", lambda: tmp), \
                 _patch("importlib.metadata.version",
                        side_effect=Exception("no metadata")):
                self.assertEqual(u.bundled_version(), "")

    def test_bundled_version_is_readable(self):
        """内置 yt-dlp 的版本号要能读到（读不到就永远优先用户副本，这是可接受的，
        但仍应确认取值路径可用）。"""
        self.assertTrue(u.parse_version(u.bundled_version())
                        or u.bundled_version() == "",
                        f"内置版本号异常：{u.bundled_version()!r}")


class ActivateTests(_TempRootCase):
    def setUp(self):
        super().setUp()
        self._saved_path = list(sys.path)

    def tearDown(self):
        sys.path[:] = self._saved_path
        super().tearDown()

    def test_no_managed_copy_keeps_the_bundled_one(self):
        self.assertEqual(u.activate(), "bundled")
        self.assertIsNone(u.best_managed())
        self.assertEqual(u.active_source(), "bundled")

    def test_newer_managed_copy_is_injected(self):
        root = self.install_fake("2099.01.01")
        self.assertEqual(u.activate(), "managed")
        self.assertIn(root, sys.path)
        self.assertEqual(sys.path[0], root, "必须优先于内置副本")

    def test_older_managed_copy_is_ignored(self):
        root = self.install_fake("2000.01.01")
        self.assertEqual(u.activate(), "bundled")
        self.assertNotIn(root, sys.path)

    def test_same_version_is_ignored(self):
        root = self.install_fake(u.bundled_version() or "2026.08.19")
        expected = "bundled" if u.parse_version(u.bundled_version()) else "managed"
        self.assertEqual(u.activate(), expected)
        self.assertEqual(root in sys.path, expected == "managed")

    def test_activate_is_idempotent(self):
        root = self.install_fake("2099.01.01")
        u.activate()
        u.activate()
        self.assertEqual(sys.path.count(root), 1)

    def test_installed_versions_filters_and_sorts(self):
        self.install_fake("2026.01.01")
        self.install_fake("2026.05.05")
        # 结构不完整的目录、非版本目录都要忽略
        os.makedirs(os.path.join(u.managed_root(), "not-a-version", "yt_dlp"))
        broken = os.path.join(u.managed_root(), "2026.03.03", "yt_dlp")
        os.makedirs(broken)          # 只有目录、没有 __init__.py
        self.assertEqual(u.installed_versions(), ["2026.05.05", "2026.01.01"])
        self.assertEqual(u.best_managed()[0], "2026.05.05")

    def test_activate_never_imports_yt_dlp(self):
        """第一条约束：activate() 不能导入 yt_dlp，否则注入 sys.path 毫无意义。"""
        self.install_fake("2099.01.01")
        # 子进程必须也指向同一个临时数据目录：否则它看不到托管副本，activate()
        # 会直接早返回，这条断言就变成空跑（第一版正是这样，反向验证才发现）
        script = (
            "import sys;"
            "from app import ytdlp_update as u;"
            f"u.data_dir = lambda: {self.root!r};"
            "assert u.best_managed() is not None, '子进程没看到托管副本，测试会空跑';"
            "u.activate();"
            "print('yt_dlp' in sys.modules)"
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=REPO,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False",
                         "activate() 竟然导入了 yt_dlp，注入会失效")


class InstallTests(_TempRootCase):
    def _wheel(self, version, **kw):
        path = os.path.join(self.root, f"yt_dlp-{version}-py3-none-any.whl")
        return make_wheel(path, version, **kw)

    def test_install_extracts_only_the_package(self):
        wheel = self._wheel("2026.09.09", extra=["yt_dlp-2026.9.9.dist-info/METADATA"])
        target = u.install_wheel(wheel, "2026.09.09")
        self.assertEqual(target, os.path.join(u.managed_root(), "2026.09.09"))
        self.assertTrue(os.path.isfile(os.path.join(target, "yt_dlp", "__init__.py")))
        self.assertFalse(os.path.exists(os.path.join(target, "yt_dlp-2026.9.9.dist-info")))
        self.assertEqual(u.read_version_file(os.path.join(target, "yt_dlp")), "2026.09.09")

    def test_install_is_idempotent(self):
        wheel = self._wheel("2026.09.09")
        first = u.install_wheel(wheel, "2026.09.09")
        second = u.install_wheel(wheel, "2026.09.09")
        self.assertEqual(first, second)
        self.assertEqual(u.installed_versions(), ["2026.09.09"])

    def test_rejects_wheel_without_the_package(self):
        path = os.path.join(self.root, "empty.whl")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("README.txt", "hi")
        with self.assertRaises(u.YtdlpUpdateError):
            u.install_wheel(path, "2026.09.09")
        self.assertEqual(u.installed_versions(), [])
        self.assertEqual(os.listdir(u.managed_root()), [], "失败不该留下半成品")

    def test_rejects_broken_zip(self):
        path = os.path.join(self.root, "broken.whl")
        with open(path, "wb") as fh:
            fh.write(b"not a zip at all")
        with self.assertRaises(u.YtdlpUpdateError):
            u.install_wheel(path, "2026.09.09")
        self.assertEqual(os.listdir(u.managed_root()), [])

    def test_rejects_version_mismatch(self):
        wheel = self._wheel("2026.09.09")
        with self.assertRaises(u.YtdlpUpdateError):
            u.install_wheel(wheel, "2026.10.10")
        self.assertEqual(u.installed_versions(), [])

    def test_rejects_bad_version_string(self):
        wheel = self._wheel("2026.09.09")
        with self.assertRaises(u.YtdlpUpdateError):
            u.install_wheel(wheel, "nightly")
        self.assertFalse(os.path.exists(os.path.join(u.managed_root(), "nightly")))

    def test_rejects_path_traversal(self):
        path = os.path.join(self.root, "evil.whl")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("yt_dlp/__init__.py", "")
            zf.writestr("yt_dlp/version.py", '__version__ = "2026.09.09"\n')
            zf.writestr("yt_dlp/../../../evil.py", "boom")
        with self.assertRaises(u.YtdlpUpdateError):
            u.install_wheel(path, "2026.09.09")
        self.assertFalse(os.path.exists(os.path.join(self.root, "evil.py")))
        self.assertFalse(os.path.exists(
            os.path.join(os.path.dirname(self.root), "evil.py")))
        self.assertEqual(os.listdir(u.managed_root()), [])

    def test_bundled_version_is_stable_after_activation(self):
        """回归：注入本机副本后「内置版本」不能变成「已安装版本」。

        否则 active_source() 会与 activate() 的结论矛盾，诊断里显示的版本也会错。
        """
        before = u.bundled_version()
        self.install_fake("2099.01.01")
        self.assertEqual(u.activate(), "managed")
        self.assertEqual(u.bundled_version(), before,
                         "注入后内置版本被托管副本顶替了")
        self.assertEqual(u.active_source(), "managed")

    def test_install_then_activate_prefers_the_new_copy(self):
        self._wheel("2099.01.01")
        saved = list(sys.path)
        try:
            u.install_wheel(os.path.join(self.root, "yt_dlp-2099.01.01-py3-none-any.whl"),
                            "2099.01.01")
            self.assertEqual(u.activate(), "managed")
            self.assertEqual(u.active_source(), "managed")
        finally:
            sys.path[:] = saved


class NetworkTests(_TempRootCase):
    def _pypi_payload(self, version, url):
        return (b'{"info": {"version": "%s"}, "urls": [{"filename": "yt_dlp-%s.tar.gz",'
                b' "url": "https://example.com/src.tar.gz"}, {"filename":'
                b' "yt_dlp-%s-py3-none-any.whl", "url": "%s"}]}'
                % (version.encode(), version.encode(), version.encode(), url.encode()))

    def test_check_latest_prefers_the_wheel(self):
        payload = self._pypi_payload("2026.10.01", "https://example.com/a.whl")
        session = _FakeSession({u.PYPI_JSON: _FakeResponse(payload)})
        version, url = u.check_latest(session=session)
        self.assertEqual(version, "2026.10.01")
        self.assertEqual(url, "https://example.com/a.whl")
        self.assertEqual(session.requests, [u.PYPI_JSON])

    def test_check_latest_handles_network_errors(self):
        session = _FakeSession({u.PYPI_JSON: RuntimeError("dns boom")})
        with self.assertRaises(u.YtdlpUpdateError) as ctx:
            u.check_latest(session=session)
        self.assertIn("dns boom", str(ctx.exception))

    def test_check_latest_handles_garbage(self):
        for payload in (b"not json", b'{"info": {}}', b'{"info": {"version": "x"}}'):
            session = _FakeSession({u.PYPI_JSON: _FakeResponse(payload)})
            with self.assertRaises(u.YtdlpUpdateError):
                u.check_latest(session=session)

    def test_download_and_install(self):
        wheel_path = os.path.join(self.root, "src.whl")
        make_wheel(wheel_path, "2026.11.11")
        with open(wheel_path, "rb") as fh:
            blob = fh.read()
        session = _FakeSession({"https://example.com/a.whl": _FakeResponse(blob)})
        seen = []
        target = u.download_and_install(
            "2026.11.11", "https://example.com/a.whl", session=session,
            progress=lambda done, total: seen.append((done, total)))
        self.assertTrue(os.path.isfile(os.path.join(target, "yt_dlp", "__init__.py")))
        self.assertEqual(seen[-1], (len(blob), len(blob)))
        # 临时文件要清干净
        leftovers = [n for n in os.listdir(tempfile.gettempdir())
                     if n.startswith("yt-dlp-") and n.endswith(".whl")]
        self.assertEqual(leftovers, [])

    def test_download_failure_is_reported_and_cleaned(self):
        class _Boom(_FakeResponse):
            def iter_content(self, chunk_size=65536):
                raise RuntimeError("connection reset")

        session = _FakeSession({"https://example.com/a.whl": _Boom(b"x")})
        with self.assertRaises(u.YtdlpUpdateError) as ctx:
            u.download_and_install("2026.11.11", "https://example.com/a.whl",
                                   session=session)
        self.assertIn("connection reset", str(ctx.exception))
        self.assertEqual(u.installed_versions(), [])


class BrokenCopyTests(_TempRootCase):
    """本机副本坏掉时的兜底：不能因此让 App 起不来。"""

    def setUp(self):
        super().setUp()
        self._saved_path = list(sys.path)
        os.environ.pop(u.DISABLE_ENV, None)

    def tearDown(self):
        sys.path[:] = self._saved_path
        os.environ.pop(u.DISABLE_ENV, None)
        super().tearDown()

    def test_env_guard_skips_the_managed_copy(self):
        """重启兜底：设了环境变量这次就完全不用本机副本。"""
        root = self.install_fake("2099.01.01")
        self.assertEqual(u.activate(), "managed")
        sys.path[:] = self._saved_path
        os.environ[u.DISABLE_ENV] = "1"
        self.assertEqual(u.activate(), "bundled")
        self.assertNotIn(root, sys.path)

    def test_discard_moves_the_copy_aside_keeping_evidence(self):
        self.install_fake("2099.01.01")
        self.assertTrue(u.discard("2099.01.01", reason="ImportError: boom"))
        self.assertEqual(u.installed_versions(), [], "坏副本不该再被认作可用")
        broken = os.path.join(u.managed_root(), u.BROKEN_PREFIX + "2099.01.01")
        self.assertTrue(os.path.isdir(broken))
        with open(os.path.join(broken, "broken.txt"), encoding="utf-8") as fh:
            self.assertIn("ImportError", fh.read())

    def test_discard_is_safe_for_unknown_input(self):
        for version in ("nightly", "", "2000.01.01"):
            self.assertFalse(u.discard(version))

    def test_discard_replaces_an_older_broken_marker(self):
        self.install_fake("2099.01.01")
        self.assertTrue(u.discard("2099.01.01", "first"))
        self.install_fake("2099.01.01")
        self.assertTrue(u.discard("2099.01.01", "second"))
        with open(os.path.join(u.managed_root(), u.BROKEN_PREFIX + "2099.01.01",
                               "broken.txt"), encoding="utf-8") as fh:
            self.assertIn("second", fh.read())

    def test_disable_current_only_reports_when_it_was_in_use(self):
        self.assertFalse(u.disable_current("boom"), "没有本机副本时不该谎报处理过")
        self.install_fake("2099.01.01")
        self.assertTrue(u.disable_current("ImportError: cannot import CHANNEL"))
        self.assertEqual(u.active_source(), "bundled")
        self.assertEqual(u.activate(), "bundled")


if __name__ == "__main__":
    unittest.main()
