"""设置页分组（④）的结构测试。

动机：设置页当时是 10 张卡片一路滚到底，没有分组，找一项设置要整页扫。
分组标题是纯布局改动，最容易出的错是「插错位置」或「改的过程中弄丢卡片」，
所以这里按出现顺序把「分组标题 + 卡片」的序列整体断言下来。

整个文件共用一个 App：第一次 Tk 初始化要数秒，且销毁根窗口后再新建会让
macOS 上的 Tk 段错误（真实踩过）。
"""

import unittest

from app import gui, i18n, settings as settings_mod

_ORIGINAL_LOAD = settings_mod.load
_ORIGINAL_SAVE = settings_mod.save
_ORIGINAL_MENUBAR = gui.menubar.create

# 期望的结构：分组标题 -> 该组内的卡片标题（i18n 键）
EXPECTED = (
    ("settings_section_general", ["theme", "language", "app_logo"]),
    ("settings_section_save", ["default_save"]),
    ("settings_section_cookies", ["browser_cookie", "platform_cookies"]),
    ("settings_section_download", ["download_feedback", "subtitles", "embed_card",
                                  "after_download_card"]),
    ("settings_section_advanced", ["shortcuts", "ytdlp_card", "proxy_card",
                                   "diagnostics", "version_card",
                                   "settings_io_card"]),
)

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


class SettingsGroupingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()
        cls.page = cls.app.pages["settings"]

    def setUp(self):
        settings_mod.save = lambda data: None

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def _structure(self):
        """按出现顺序返回 ('section'|'card', 文本) 列表。"""
        result = []
        for child in self.page._scroll_body.winfo_children():
            if hasattr(child, "body"):          # GlassCard
                titles = [w.cget("text") for w in child.body.winfo_children()
                          if isinstance(w, gui.TLabel)]
                result.append(("card", titles[0] if titles else ""))
                continue
            try:
                text = child.cget("text")
            except Exception:
                continue                        # PageHeader 之类的容器
            if text:
                result.append(("section", text))
        return result

    def test_sections_appear_in_order_with_their_cards(self):
        structure = self._structure()
        expected = []
        for key, cards in EXPECTED:
            expected.append(("section", i18n.tr(key)))
            expected.extend(("card", i18n.tr(card)) for card in cards)
        self.assertEqual(structure, expected)

    def test_every_card_is_accounted_for(self):
        """分组是纯布局改动，卡片数量不该变。"""
        cards = [text for kind, text in self._structure() if kind == "card"]
        self.assertEqual(len(cards), 16)
        self.assertEqual(len(set(cards)), 16, "不该出现重复卡片标题")

    def test_section_titles_are_localized(self):
        titles = [text for kind, text in self._structure() if kind == "section"]
        for text in titles:
            self.assertNotIn("settings_section", text)
            self.assertTrue(text.strip())

    def test_section_title_does_not_repeat_the_card_title_right_below(self):
        """分组标题与紧随其后的卡片标题重复会显得很业余。"""
        structure = self._structure()
        for index, (kind, text) in enumerate(structure[:-1]):
            if kind != "section":
                continue
            nxt_kind, nxt_text = structure[index + 1]
            if nxt_kind == "card":
                self.assertNotEqual(text, nxt_text, f"{text} 既是分组名又是卡片名")

    def test_sections_are_on_screen_in_every_language(self):
        """五种语言下的分组标题都要有实际文案，不能退化成键名。"""
        original = i18n.language()
        try:
            for code in ("zh_CN", "zh_TW", "en", "ko", "ja"):
                i18n.set_language(code)
                for key, _cards in EXPECTED:
                    text = i18n.tr(key)
                    self.assertNotEqual(text, key, f"{code} 缺少 {key}")
        finally:
            i18n.set_language(original)


class _SyncThread:
    """把后台线程变成同步执行，让状态机可以在没有 mainloop 的测试里跑完。

    注意只在调用那一小段窗口内替换，避免影响 App 其它后台任务（如 Cookie 体检）。
    """

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target:
            self._target()


class YtdlpCardTests(unittest.TestCase):
    """「yt-dlp 更新」卡片的状态机：检查 → 发现新版本 → 更新 → 重启后生效。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _shared_app()
        cls.page = cls.app.pages["settings"]

    def setUp(self):
        settings_mod.save = lambda data: None
        self.page._ytdlp_token += 1          # 让之前的残留状态失效
        self.page._ytdlp_pending = None
        self.page.ytdlp_btn.set_text(i18n.tr("ytdlp_check"))
        self.page.ytdlp_btn.configure_state("normal")
        self.page.ytdlp_status_label.configure(text="")
        self.calls = []

    def tearDown(self):
        settings_mod.save = _ORIGINAL_SAVE

    def _pump(self):
        for _ in range(4):
            self.app.update_idletasks()
            self.app.update()

    def test_shows_the_active_version_and_source(self):
        from unittest.mock import patch as _patch

        with _patch.object(gui.ytdlp_update, "active_version",
                           lambda: ("2026.08.19", "bundled")):
            self.page._ytdlp_refresh()
        text = self.page.ytdlp_version_label.cget("text")
        self.assertIn("2026.08.19", text)
        self.assertIn(i18n.tr("ytdlp_source_bundled"), text)
        with _patch.object(gui.ytdlp_update, "active_version",
                           lambda: ("2099.01.01", "managed")):
            self.page._ytdlp_refresh()
        text = self.page.ytdlp_version_label.cget("text")
        self.assertIn("2099.01.01", text)
        self.assertIn(i18n.tr("ytdlp_source_managed"), text)

    def test_check_reports_up_to_date(self):
        from unittest.mock import patch as _patch

        with _patch.object(gui.ytdlp_update, "active_version",
                           lambda: ("2026.08.19", "bundled")), \
             _patch.object(gui.ytdlp_update, "check_latest",
                           lambda: ("2026.08.19", "https://e/a.whl")):
            with _patch_thread():
                self.page._on_ytdlp_button()
            self._pump()
        self.assertIn(i18n.tr("ytdlp_is_latest", version="2026.08.19"),
                      self.page.ytdlp_status_label.cget("text"))
        self.assertEqual(self.page.ytdlp_btn._text, i18n.tr("ytdlp_check"))
        self.assertIsNone(self.page._ytdlp_pending)

    def test_check_offers_a_newer_version(self):
        from unittest.mock import patch as _patch

        with _patch.object(gui.ytdlp_update, "active_version",
                           lambda: ("2026.08.19", "bundled")), \
             _patch.object(gui.ytdlp_update, "check_latest",
                           lambda: ("2099.01.01", "https://e/a.whl")):
            with _patch_thread():
                self.page._on_ytdlp_button()
            self._pump()
        self.assertEqual(self.page._ytdlp_pending, ("2099.01.01", "https://e/a.whl"))
        self.assertEqual(self.page.ytdlp_btn._text,
                         i18n.tr("ytdlp_update_to", version="2099.01.01"))
        self.assertIn(i18n.tr("ytdlp_found", version="2099.01.01"),
                      self.page.ytdlp_status_label.cget("text"))

    def test_second_click_installs_the_offered_version(self):
        from unittest.mock import patch as _patch

        installed = []
        active = {"value": ("2026.08.19", "bundled")}
        with _patch.object(gui.ytdlp_update, "active_version", lambda: active["value"]), \
             _patch.object(gui.ytdlp_update, "check_latest",
                           lambda: ("2099.01.01", "https://e/a.whl")), \
             _patch.object(gui.ytdlp_update, "download_and_install",
                           lambda version, url: installed.append((version, url))):
            with _patch_thread():
                self.page._on_ytdlp_button()          # 第一次点：检查
            self._pump()
            active["value"] = ("2099.01.01", "managed")   # 更新成功后生效的就是新装的
            with _patch_thread():
                self.page._on_ytdlp_button()          # 第二次点：更新
            self._pump()
        self.assertEqual(installed, [("2099.01.01", "https://e/a.whl")])
        self.assertIn(i18n.tr("ytdlp_updated", version="2099.01.01"),
                      self.page.ytdlp_status_label.cget("text"))
        self.assertEqual(self.page.ytdlp_btn._text, i18n.tr("ytdlp_check"))
        self.assertIsNone(self.page._ytdlp_pending)

    def test_check_failure_is_reported_and_button_recovers(self):
        def boom():
            raise gui.ytdlp_update.YtdlpUpdateError("无法连接 PyPI：dns")

        from unittest.mock import patch as _patch

        with _patch.object(gui.ytdlp_update, "active_version",
                           lambda: ("2026.08.19", "bundled")), \
             _patch.object(gui.ytdlp_update, "check_latest", boom):
            with _patch_thread():
                self.page._on_ytdlp_button()
            self._pump()
        self.assertIn("无法连接 PyPI", self.page.ytdlp_status_label.cget("text"))
        self.assertEqual(self.page.ytdlp_btn._state, "normal")
        self.assertIsNone(self.page._ytdlp_pending)

    def test_update_failure_is_reported_and_button_recovers(self):
        def boom(version, url):
            raise gui.ytdlp_update.YtdlpUpdateError("下载失败：reset")

        self.page._ytdlp_pending = ("2099.01.01", "https://e/a.whl")
        from unittest.mock import patch as _patch

        with _patch.object(gui.ytdlp_update, "active_version",
                           lambda: ("2026.08.19", "bundled")), \
             _patch.object(gui.ytdlp_update, "download_and_install", boom):
            with _patch_thread():
                self.page._on_ytdlp_button()
            self._pump()
        text = self.page.ytdlp_status_label.cget("text")
        self.assertIn(i18n.tr("ytdlp_failed", message="下载失败：reset"), text)
        self.assertEqual(self.page.ytdlp_btn._text, i18n.tr("ytdlp_check"))
        self.assertIsNone(self.page._ytdlp_pending)

    def test_stale_result_is_ignored(self):
        """期间的旧结果不能覆盖新状态（token 机制）。"""
        self.page.ytdlp_status_label.configure(text="新状态")
        self.page._ytdlp_token += 5
        self.page._apply_ytdlp_check(self.page._ytdlp_token - 1, "2099.01.01",
                                     "https://e/a.whl", None)
        self.assertEqual(self.page.ytdlp_status_label.cget("text"), "新状态")
        self.assertIsNone(self.page._ytdlp_pending)


def _patch_thread():
    from unittest.mock import patch as _patch

    return _patch.object(gui.threading, "Thread", _SyncThread)


if __name__ == "__main__":
    unittest.main()
