"""设置导入导出的测试（ROADMAP 第 12 项）。

第 12 项的由来：换机器或重装后要把十几项设置手抄一遍。这个文件锁住四件事：

1. **导出不含敏感/机器相关项**：Cookie 来源与 cookies.txt 路径、代理地址（可能带密码）、
   下载历史与队列、窗口位置。
2. **导入不是照单全收**：未知键、类型不符、取值越界的键一律忽略，并如实计入摘要。
3. **导入后要应用到当前进程**：语言、主题、字号三项要真的切过去。
4. **_ENUMS 的取值清单不能与真实来源漂开**（否则某天改了选项，导入就会静默放行非法值）。
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from app import core, gui, i18n, postaction, settings as settings_mod, settings_io

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


class ExportTests(unittest.TestCase):
    def test_export_excludes_sensitive_and_machine_keys(self):
        data = settings_io.exportable({**settings_mod.DEFAULTS,
                                       "proxy": "http://u:p@host:1",
                                       "cookie_file": "/tmp/cookies.txt",
                                       "browser_cookies": "chrome",
                                       "platform_cookies": {"x": "chrome"},
                                       "download_history": [{"path": "/tmp/a"}],
                                       "download_queue": [{"url": "https://e/1"}],
                                       "window_geometry": "1020x745+1+1",
                                       "last_update_check": 123,
                                       "update_available": "9.9.9"})
        for key in settings_io.EXCLUDED:
            self.assertNotIn(key, data, key)
        self.assertEqual(data["_format"], settings_io.FORMAT_TAG)

    def test_export_keeps_user_preferences(self):
        data = settings_io.exportable({**settings_mod.DEFAULTS,
                                       "theme": "dark", "language": "en",
                                       "font_scale": "large", "after_download": "sleep",
                                       "embed_thumbnail": True})
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["language"], "en")
        self.assertEqual(data["font_scale"], "large")
        self.assertEqual(data["after_download"], "sleep")
        self.assertTrue(data["embed_thumbnail"])

    def test_export_is_sorted_for_readable_diffs(self):
        data = settings_io.exportable(dict(settings_mod.DEFAULTS))
        # 元信息（_format 等）故意排在最前，其余键按名字排序，方便人看与 diff
        keys = [k for k in data if not k.startswith("_")]
        self.assertEqual(keys, sorted(keys))
        self.assertTrue([k for k in data if k.startswith("_")])

    def test_export_drops_values_with_the_wrong_type(self):
        data = settings_io.exportable({**settings_mod.DEFAULTS, "theme": 5})
        self.assertNotIn("theme", data)


class ImportTests(unittest.TestCase):
    def test_applies_valid_preferences(self):
        merged, summary = settings_io.import_settings(
            {"theme": "light", "font_scale": "xlarge", "embed_metadata": True})
        self.assertEqual(merged, {"theme": "light", "font_scale": "xlarge",
                                  "embed_metadata": True})
        self.assertEqual(summary["applied"], 3)
        self.assertEqual(summary["ignored"], 0)

    def test_unknown_keys_are_ignored(self):
        merged, summary = settings_io.import_settings(
            {"theme": "dark", "definitely_not_a_key": 1})
        self.assertEqual(merged, {"theme": "dark"})
        self.assertEqual(summary["ignored"], 1)

    def test_excluded_keys_are_never_imported(self):
        merged, summary = settings_io.import_settings({
            "theme": "dark", "proxy": "http://u:p@host:1",
            "cookie_file": "/etc/passwd", "download_history": [{"path": "/x"}]})
        self.assertEqual(merged, {"theme": "dark"})
        for key in ("proxy", "cookie_file", "download_history"):
            self.assertNotIn(key, merged, key)
        self.assertEqual(summary["ignored"], 3)

    def test_metadata_keys_are_not_counted_as_ignored(self):
        merged, summary = settings_io.import_settings(
            {"_format": "videodownloader-settings", "_app_version": "1.11.0"})
        self.assertEqual(merged, {})
        self.assertEqual(summary["ignored"], 0)

    def test_wrong_types_are_rejected(self):
        merged, summary = settings_io.import_settings({
            "theme": "dark",           # 正常
            "font_scale": 5,           # str 默认值收到 int
            "embed_thumbnail": "yes",  # bool 默认值收到 str
            "playlist_limit": True,    # int 默认值收到 bool（bool 是 int 子类）
            "save_dirs": ["/tmp"],     # dict 默认值收到 list
            "log_retention_days": 3,   # 不存在的键
        })
        self.assertEqual(merged, {"theme": "dark"})
        self.assertEqual(summary["rejected"],
                         ["embed_thumbnail", "font_scale", "playlist_limit",
                          "save_dirs"])
        self.assertEqual(summary["applied"], 1)

    def test_invalid_enum_values_are_rejected(self):
        merged, summary = settings_io.import_settings({
            "language": "klingon", "theme": "neon", "font_scale": "huge",
            "clipboard_action": "always", "app_logo": "sparkles",
            "audio_format": "wav", "after_download": "shutdown"})
        self.assertEqual(merged, {})
        self.assertEqual(len(summary["rejected"]), 7)

    def test_every_valid_enum_value_is_accepted(self):
        for key, allowed in settings_io._ENUMS.items():
            for value in allowed:
                merged, summary = settings_io.import_settings({key: value})
                self.assertEqual(merged, {key: value}, f"{key}={value}")
                self.assertEqual(summary["rejected"], [], f"{key}={value}")

    def test_negative_playlist_limit_is_rejected(self):
        merged, _ = settings_io.import_settings({"playlist_limit": -3})
        self.assertEqual(merged, {})
        merged, _ = settings_io.import_settings({"playlist_limit": 0})
        self.assertEqual(merged, {"playlist_limit": 0})

    def test_save_dirs_must_be_a_string_map(self):
        merged, _ = settings_io.import_settings(
            {"save_dirs": {"weibo": "/tmp/w"}})
        self.assertEqual(merged, {"save_dirs": {"weibo": "/tmp/w"}})
        merged, summary = settings_io.import_settings(
            {"save_dirs": {"weibo": 5}})
        self.assertEqual(merged, {})
        self.assertIn("save_dirs", summary["rejected"])

    def test_non_dict_raises(self):
        for value in ([], "text", 5, None):
            with self.subTest(value=value):
                with self.assertRaises(settings_io.SettingsImportError):
                    settings_io.import_settings(value)


class FileIoTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "settings.json")
            data = settings_io.exportable({**settings_mod.DEFAULTS, "theme": "dark"})
            settings_io.write_file(path, data)
            self.assertEqual(settings_io.read_file(path), data)
            self.assertFalse(os.path.exists(path + ".tmp"), "不要留临时文件")

    def test_broken_files_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            with self.assertRaises(settings_io.SettingsImportError):
                settings_io.read_file(bad)
            not_dict = os.path.join(tmp, "list.json")
            with open(not_dict, "w", encoding="utf-8") as handle:
                json.dump([1, 2, 3], handle)
            with self.assertRaises(settings_io.SettingsImportError):
                settings_io.read_file(not_dict)
            with self.assertRaises(settings_io.SettingsImportError):
                settings_io.read_file(os.path.join(tmp, "missing.json"))


class EnumDriftTests(unittest.TestCase):
    """取值清单必须与真实来源一致（否则改了选项，导入会静默放行非法值）。"""

    def test_matches_the_real_option_sources(self):
        self.assertEqual(set(settings_io._ENUMS["language"]),
                         {code for _label, code in i18n.language_options()})
        self.assertEqual(set(settings_io._ENUMS["font_scale"]),
                         set(gui.App.FONT_SCALES))
        self.assertEqual(set(settings_io._ENUMS["app_logo"]),
                         {key for key, _label in gui.LOGO_OPTIONS})
        self.assertEqual(set(settings_io._ENUMS["audio_format"]),
                         set(core.AUDIO_FORMATS))
        self.assertEqual(set(settings_io._ENUMS["after_download"]),
                         set(postaction.OPTIONS))
        self.assertEqual(set(settings_io._ENUMS["clipboard_action"]),
                         {"suggest", "auto", "off"})
        self.assertEqual(set(settings_io._ENUMS["theme"]),
                         {"system", "light", "dark"})

    def test_every_enum_key_exists_in_defaults(self):
        for key in settings_io._ENUMS:
            self.assertIn(key, settings_mod.DEFAULTS, key)

    def test_excluded_keys_exist_in_defaults(self):
        for key in settings_io.EXCLUDED:
            self.assertIn(key, settings_mod.DEFAULTS, key)


class SettingsIoUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self.saved = []
        settings_mod.save = lambda data: self.saved.append(dict(data))
        self.app.settings = dict(settings_mod.DEFAULTS)
        self.app.settings["language"] = "zh_CN"
        self.app.show_page("settings")
        self.app.update_idletasks()
        self.page = self.app.pages["settings"]
        self.infos, self.errors = [], []
        self._patches = [
            mock.patch.object(gui.messagebox, "showinfo",
                              lambda *a, **kw: self.infos.append(a)),
            mock.patch.object(gui.messagebox, "showerror",
                              lambda *a, **kw: self.errors.append(a)),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()
        settings_mod.save = _ORIGINAL_SAVE

    def test_export_writes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.json")
            with mock.patch.object(gui.filedialog, "asksaveasfilename",
                                   lambda **kw: path):
                self.page._export_settings()
            self.assertTrue(os.path.exists(path))
            data = json.load(open(path, encoding="utf-8"))
            self.assertEqual(data["_format"], settings_io.FORMAT_TAG)
            self.assertNotIn("proxy", data)
            self.assertEqual(len(self.infos), 1, "应当告诉用户导出到哪了")
            self.assertEqual(self.errors, [])

    def test_export_cancelled_does_nothing(self):
        with mock.patch.object(gui.filedialog, "asksaveasfilename",
                               lambda **kw: ""):
            self.page._export_settings()
        self.assertEqual(self.infos, [])
        self.assertEqual(self.errors, [])

    def test_export_failure_is_reported(self):
        with mock.patch.object(gui.filedialog, "asksaveasfilename",
                               lambda **kw: "/tmp/whatever.json"), \
             mock.patch.object(settings_io, "write_file",
                               side_effect=OSError("磁盘只读")):
            self.page._export_settings()
        self.assertEqual(self.infos, [])
        self.assertIn("磁盘只读", str(self.errors[0]))

    def test_import_applies_and_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in.json")
            settings_io.write_file(path, {"theme": "dark", "language": "en",
                                          "font_scale": "large"})
            applied = []
            with mock.patch.object(gui.filedialog, "askopenfilename",
                                   lambda **kw: path), \
                 mock.patch.object(gui.App, "set_theme",
                                   lambda self, v: applied.append(("theme", v))), \
                 mock.patch.object(gui.App, "set_language",
                                   lambda self, v: applied.append(("language", v))), \
                 mock.patch.object(gui.App, "set_font_scale",
                                   lambda self, v: applied.append(("font", v))):
                self.page._import_settings()
        self.assertEqual(self.app.settings["theme"], "dark")
        self.assertEqual(self.app.settings["language"], "en")
        self.assertEqual(self.app.settings["font_scale"], "large")
        self.assertEqual(self.saved[-1]["theme"], "dark", "要落盘")
        self.assertEqual([name for name, _v in applied],
                         ["theme", "language", "font"],
                         "字号会重建界面，必须放最后")
        self.assertEqual(len(self.infos), 1)

    def test_import_cancelled_does_nothing(self):
        with mock.patch.object(gui.filedialog, "askopenfilename",
                               lambda **kw: ""):
            self.page._import_settings()
        self.assertEqual(self.saved, [])
        self.assertEqual(self.infos, [])

    def test_import_of_a_broken_file_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{broken")
            with mock.patch.object(gui.filedialog, "askopenfilename",
                                   lambda **kw: path):
                self.page._import_settings()
        self.assertEqual(self.infos, [], "失败不该用信息框")
        self.assertEqual(len(self.errors), 1, "坏文件必须报错")
        self.assertIn(i18n.tr("settings_import_failed").split("：")[0],
                      str(self.errors[0]))
        self.assertEqual(self.saved, [], "坏文件不能改动任何设置")

    def test_import_of_a_file_with_nothing_usable_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.json")
            settings_io.write_file(path, {"_format": settings_io.FORMAT_TAG,
                                          "proxy": "http://u:p@host:1"})
            applied = []
            with mock.patch.object(gui.filedialog, "askopenfilename",
                                   lambda **kw: path), \
                 mock.patch.object(gui.App, "set_language",
                                   lambda self, v: applied.append(v)):
                self.page._import_settings()
        self.assertEqual(applied, [], "没有可导入项时不该去应用")
        self.assertIn(i18n.tr("settings_import_nothing"), str(self.infos[0]))


if __name__ == "__main__":
    unittest.main()
