"""首次使用说明（#9）的测试。

设计要点：只有**真正全新安装**（连设置文件都还没有）才弹说明，靠 settings.load
在内存里放一个不落盘的 `_first_run` 标记。这样既符合「首次使用」语义，也保证任何
用假 settings 驱动的测试都不会突然冒出模态窗口挡住事件循环（模态会 wait_window，
一旦冒出来就是测试挂死，而不是失败）。

整个文件共用一个 App；弹窗只构造不 show()，否则模态会把测试挂住。
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock

from app import gui, i18n, settings as settings_mod

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


class FirstRunFlagTests(unittest.TestCase):
    """settings.load 的 _first_run 标记（不需要 Tk）。"""

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self._path = settings_mod.SETTINGS_PATH
        self._load = settings_mod.load
        self._save = settings_mod.save
        settings_mod.SETTINGS_PATH = os.path.join(self.tempdir, "settings.json")
        settings_mod.load = _ORIGINAL_LOAD
        settings_mod.save = _ORIGINAL_SAVE

    def tearDown(self):
        settings_mod.SETTINGS_PATH = self._path
        settings_mod.load = self._load
        settings_mod.save = self._save
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_missing_file_means_first_run(self):
        self.assertTrue(settings_mod.load()["_first_run"])

    def test_existing_file_is_not_first_run(self):
        settings_mod.save(dict(settings_mod.DEFAULTS))
        self.assertFalse(settings_mod.load()["_first_run"])

    def test_flag_is_never_written_to_disk(self):
        data = settings_mod.load()
        settings_mod.save(data)
        with open(settings_mod.SETTINGS_PATH, encoding="utf-8") as handle:
            self.assertNotIn("_first_run", handle.read())

    def test_corrupt_file_counts_as_seen(self):
        """文件存在但内容坏了：不该再当成首次安装去弹说明。"""
        with open(settings_mod.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.assertFalse(settings_mod.load()["_first_run"])


class OnboardingGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        self.saved = []
        settings_mod.save = lambda data: self.saved.append(dict(data))
        self.app.settings.pop("_first_run", None)
        self.app.settings["onboarding_done"] = False

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def test_faked_settings_never_trigger_the_dialog(self):
        """测试里假造的 settings 没有 _first_run，因此永远不会弹模态。"""
        self.assertFalse(self.app.needs_onboarding())

    def test_first_run_without_flag_needs_onboarding(self):
        self.app.settings["_first_run"] = True
        self.assertTrue(self.app.needs_onboarding())

    def test_already_seen_does_not_need_onboarding(self):
        self.app.settings["_first_run"] = True
        self.app.settings["onboarding_done"] = True
        self.assertFalse(self.app.needs_onboarding())

    def test_maybe_show_welcome_is_a_noop_when_not_needed(self):
        shown = []
        original = gui.WelcomeDialog
        gui.WelcomeDialog = lambda *a, **kw: shown.append(kw)
        try:
            self.app._maybe_show_welcome()
        finally:
            gui.WelcomeDialog = original
        self.assertEqual(shown, [], "不该弹说明")

    def test_finish_onboarding_persists(self):
        self.app._finish_onboarding()
        self.assertTrue(self.app.settings["onboarding_done"])
        self.assertEqual(len(self.saved), 1)
        self.assertTrue(self.saved[0]["onboarding_done"])


class WelcomeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()

    def setUp(self):
        settings_mod.save = lambda data: None
        self._dialogs = []
        self.dismissed = []

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE
        for dialog in self._dialogs:
            try:
                if dialog.winfo_exists():
                    dialog.destroy()
            except Exception:
                pass

    def _dialog(self, on_dismiss=None):
        dialog = gui.WelcomeDialog(self.app, self.app, on_dismiss=on_dismiss)
        self._dialogs.append(dialog)
        return dialog

    def _texts(self, widget):
        found = []
        for child in widget.winfo_children():
            try:
                if "text" in child.keys():
                    found.append(str(child.cget("text")))
            except Exception:
                pass
            found.extend(self._texts(child))
        return found

    def test_dialog_shows_the_three_steps_and_the_tip(self):
        dialog = self._dialog()
        dialog.update_idletasks()
        joined = "\n".join(self._texts(dialog))
        for key in ("welcome_title", "welcome_step_1", "welcome_step_2",
                    "welcome_step_3", "welcome_tip"):
            self.assertIn(i18n.tr(key), joined, f"缺少 {key}")
            self.assertNotIn(key, joined, f"{key} 没有翻译")

    def test_title_is_localized(self):
        dialog = self._dialog()
        self.assertEqual(dialog.title(), i18n.tr("welcome_title"))

    def test_finish_dismisses_once(self):
        dialog = self._dialog(on_dismiss=lambda: self.dismissed.append(1))
        dialog._finish()
        dialog._finish()          # 例如按钮和关窗口事件都触发
        self.assertEqual(len(self.dismissed), 1, "重复收尾不该重复保存")
        self.assertFalse(dialog.winfo_exists())

    def test_finish_can_open_settings(self):
        switched = []
        self.app.show_page = lambda key: switched.append(key)
        try:
            dialog = self._dialog()
            dialog._finish(open_settings=True)
        finally:
            self.app.show_page = type(self.app).show_page.__get__(self.app)
        self.assertEqual(switched, ["settings"])

    def test_dismiss_callback_is_optional(self):
        dialog = self._dialog()
        dialog._finish()          # 不应抛错
        self.assertFalse(dialog.winfo_exists())

    def test_steps_are_distinct_across_languages(self):
        for code in ("zh_CN", "zh_TW", "en", "ko", "ja"):
            original = i18n.language()
            i18n.set_language(code)
            try:
                texts = [i18n.tr(key) for key in
                         ("welcome_step_1", "welcome_step_2", "welcome_step_3")]
                self.assertEqual(len(set(texts)), 3, f"{code} 的步骤文案重复了")
            finally:
                i18n.set_language(original)


if __name__ == "__main__":
    unittest.main()
