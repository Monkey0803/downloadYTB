"""安装包相关的离线测试（ROADMAP 第 14 项）。

Linux 侧是**真跑**：在临时目录里造一个假的包目录（路径故意带空格），用临时 HOME
执行生成的 install-desktop.sh，然后检查启动项与图标真的落到该落的地方。

Windows 侧只能在 CI 上编译，所以这里做的是「脚本与产物路径一致」的检查，加一条
ASCII 护栏——Inno 各版本对 .iss 的编码处理不一致，非 ASCII 一旦被按 ANSI 读入就是乱码，
而这条链路本地无法验证。这些断言挡不住 Inno 语法错误，只能挡住我们这边的漂移。
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"

import sys

sys.path.insert(0, str(BUILD))
import desktop_entry  # noqa: E402


def _fake_bundle(base: str, name: str = "My Apps") -> str:
    """造一个带空格的假包目录，里面有可执行文件、图标与生成的两个文件。"""
    target = os.path.join(base, name, "VideoDownloader")
    os.makedirs(target)
    exe = os.path.join(target, desktop_entry.EXECUTABLE)
    Path(exe).write_bytes(b"\x7fELF fake")
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR)
    icon = os.path.join(base, "icon.png")
    Path(icon).write_bytes(b"\x89PNG fake")
    desktop_entry.write_bundle_files(target, "9.9.9", icon)
    return target


def _run_installer(target: str, home: str, extra_env=None):
    env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["sh", os.path.join(target, "install-desktop.sh")],
                          env=env, capture_output=True, text=True)


class DesktopEntryTests(unittest.TestCase):
    def test_desktop_file_has_the_required_keys(self):
        text = desktop_entry.desktop_text("/tmp/app/VideoDownloader", "/tmp/icon.png",
                                          "1.2.3")
        for key in ("[Desktop Entry]", "Type=Application", "Name=VideoDownloader",
                    "Terminal=false", "Categories=AudioVideo;Player;Utility;",
                    "StartupWMClass=VideoDownloader", "StartupNotify=true"):
            self.assertIn(key, text, key)
        self.assertIn('Exec="/tmp/app/VideoDownloader"', text, "Exec 必须加引号")
        self.assertIn("Icon=/tmp/icon.png", text)
        self.assertIn("X-VideoDownloader-Version=1.2.3", text)
        self.assertTrue(text.endswith("\n"))

    def test_version_key_is_optional(self):
        self.assertNotIn("X-VideoDownloader-Version", desktop_entry.desktop_text("/a", "/b"))

    def test_desktop_id_matches_the_taskbar_module(self):
        """启动项文件名必须与 taskbar_linux 报给 Unity 的 id 一致。

        名字对不上时不会报错——任务栏上的进度条会变成一个独立图标，很难察觉。
        """
        from app import taskbar_linux

        self.assertEqual(desktop_entry.APP_ID, taskbar_linux.FALLBACK_DESKTOP_ID)

    def test_install_script_is_posix_and_mentions_uninstall(self):
        text = desktop_entry.install_script_text("1.2.3")
        self.assertTrue(text.startswith("#!/bin/sh"))
        self.assertIn("set -eu", text)
        self.assertIn(desktop_entry.APP_ID, text)
        self.assertIn("卸载", text, "脚本要告诉用户怎么卸载")

    def test_missing_icon_falls_back_to_the_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "VideoDownloader")
            os.makedirs(target)
            written = desktop_entry.write_bundle_files(target, "1.0", "")
            self.assertEqual(written["icon"], "", "没有图标来源就不该生成图标文件")
            desktop = Path(written["desktop"]).read_text(encoding="utf-8")
            self.assertIn(f"Icon={os.path.join(target, desktop_entry.EXECUTABLE)}",
                          desktop)


class LinuxInstallRunTests(unittest.TestCase):
    """真跑安装脚本（在临时 HOME 里），不是一个字符一个字符地比字符串。"""

    def test_install_puts_the_entry_and_icon_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = _fake_bundle(tmp)
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            result = _run_installer(target, home)
            self.assertEqual(result.returncode, 0, result.stderr)

            entry = Path(home, ".local/share/applications",
                         f"{desktop_entry.APP_ID}.desktop")
            self.assertTrue(entry.is_file(), "启动项没装到 ~/.local/share/applications")
            mode = entry.stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR,
                            "启动项必须是可执行的，否则桌面环境不给「允许启动」")

            text = entry.read_text(encoding="utf-8")
            exe = os.path.join(target, desktop_entry.EXECUTABLE)
            self.assertIn(f'Exec="{exe}"', text,
                          "Exec 要指向解压目录的绝对路径（并带引号，路径里有空格）")

            icon = Path(home, ".local/share/icons/hicolor",
                        f"{desktop_entry.ICON_SIZE}x{desktop_entry.ICON_SIZE}",
                        "apps", desktop_entry.ICON_FILE)
            self.assertTrue(icon.is_file(), "图标没复制过去")
            self.assertIn(f"Icon={icon}", text)

    def test_running_it_twice_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = _fake_bundle(tmp)
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            self.assertEqual(_run_installer(target, home).returncode, 0)
            self.assertEqual(_run_installer(target, home).returncode, 0)

    def test_respects_xdg_data_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = _fake_bundle(tmp)
            home = os.path.join(tmp, "home")
            xdg = os.path.join(tmp, "xdg")
            os.makedirs(home)
            result = _run_installer(target, home, {"XDG_DATA_HOME": xdg})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(Path(xdg, "applications",
                                 f"{desktop_entry.APP_ID}.desktop").is_file())
            self.assertFalse(Path(home, ".local/share/applications").exists(),
                             "设了 XDG_DATA_HOME 就不该再写 ~/.local/share")

    def test_refuses_to_install_from_a_broken_bundle(self):
        """包里没有可执行文件时要说清楚原因，而不是装一个指向空处的启动项。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = _fake_bundle(tmp)
            os.unlink(os.path.join(target, desktop_entry.EXECUTABLE))
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            result = _run_installer(target, home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("找不到可执行文件", result.stderr)
            self.assertFalse(Path(home, ".local/share/applications",
                                  f"{desktop_entry.APP_ID}.desktop").exists())

    def test_desktop_cache_tools_are_optional_and_never_fatal(self):
        """桌面缓存刷新失败（或工具不存在）不能影响安装结果。

        不去假设本机有没有这两个工具——那是平台相关的。这里造两个「一定失败」的假工具
        放进 PATH，既验证分支真的走到了，也验证失败被吞掉。
        """
        with tempfile.TemporaryDirectory() as tmp:
            target = _fake_bundle(tmp)
            home = os.path.join(tmp, "home")
            os.makedirs(home)
            bindir = os.path.join(tmp, "bin")
            os.makedirs(bindir)
            marker = os.path.join(tmp, "called.txt")
            for tool in ("update-desktop-database", "gtk-update-icon-cache"):
                path = os.path.join(bindir, tool)
                Path(path).write_text(f'#!/bin/sh\necho {tool} >> "{marker}"\nexit 1\n',
                                      encoding="utf-8")
                os.chmod(path, 0o755)

            result = _run_installer(target, home,
                                    {"PATH": bindir + ":/usr/bin:/bin"})
            self.assertEqual(result.returncode, 0, "缓存工具失败不该让安装失败")
            called = Path(marker).read_text(encoding="utf-8") if os.path.exists(marker) else ""
            self.assertIn("update-desktop-database", called, "分支没走到，用例失去意义")
            self.assertTrue(Path(home, ".local/share/applications",
                                 f"{desktop_entry.APP_ID}.desktop").is_file(),
                            "缓存失败也要把启动项装好")


class BuildWiringTests(unittest.TestCase):
    def test_linux_build_generates_the_desktop_entry(self):
        script = (BUILD / "build_linux.sh").read_text(encoding="utf-8")
        self.assertIn("build/desktop_entry.py", script)
        self.assertIn("--dir \"dist/VideoDownloader\"", script)
        self.assertIn("--icon", script, "要带图标，否则菜单里是默认图标")

    def test_release_notes_tell_linux_users_about_the_installer(self):
        notes = (BUILD / "release_notes.py").read_text(encoding="utf-8")
        self.assertIn("install-desktop.sh", notes)
        self.assertIn("-setup.exe", notes, "Windows 用户也要被告知有安装包")


class WindowsInstallerScriptTests(unittest.TestCase):
    """Inno Setup 脚本的一致性检查（真正的编译只能在 Windows CI 上做）。"""

    @classmethod
    def setUpClass(cls):
        cls.text = (BUILD / "windows_installer.iss").read_text(encoding="utf-8")

    def test_is_pure_ascii(self):
        raw = (BUILD / "windows_installer.iss").read_bytes()
        raw.decode("ascii")  # 非 ASCII 会抛 UnicodeDecodeError
        self.assertNotIn("\ufeff", self.text, "不要 BOM")

    def test_appid_uses_the_escaped_brace_and_a_fixed_guid(self):
        self.assertIn("AppId={{7C4B0E52", self.text, "Inno 里字面量 { 要写成 {{")
        self.assertEqual(self.text.count("AppId="), 1)

    def test_sources_the_pyinstaller_output_directory(self):
        self.assertIn('#define SourceDir "..\\dist\\VideoDownloader"', self.text)
        self.assertIn("recursesubdirs", self.text,
                      "onedir 产物有 _internal 子目录，漏了会装出跑不起来的目录")
        # 与 build_windows.bat 的 --name 对齐
        bat = (BUILD / "build_windows.bat").read_text(encoding="utf-8", errors="replace")
        self.assertIn('--name "VideoDownloader"', bat)
        self.assertIn('#define AppExeName "VideoDownloader.exe"', self.text)

    def test_version_comes_from_the_command_line(self):
        self.assertIn("#ifndef AppVersion", self.text, "要给个缺省值，脚本才能单独打开")
        self.assertIn("/DAppVersion=", (BUILD / "windows_installer.iss").read_text(
            encoding="utf-8"))
        self.assertIn("OutputBaseFilename=VideoDownloader-{#AppVersion}-windows-x64-setup",
                      self.text)
        self.assertIn("UninstallDisplayIcon={app}\\{#AppExeName}", self.text)

    def test_ci_builds_and_publishes_the_installer(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("windows_installer.iss", workflow)
        self.assertIn('"/DAppVersion=$version"', workflow)
        self.assertIn("ISCC.exe", workflow)
        self.assertIn("windows-x64-setup.exe", workflow,
                      "安装包要作为资产上传")
        self.assertIn("release-assets/*", workflow,
                      "发布步骤要上传全部资产，而不是只上传 zip")


if __name__ == "__main__":
    unittest.main()
