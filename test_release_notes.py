"""Release 说明生成器（build/release_notes.py）的离线测试。

背景：`gh release create --generate-notes` 只汇总 PR 与新贡献者，本仓库是直接
提交、没有 PR，所以 v1.1.0–v1.7.0 的 Release 页面只有一行 changelog 链接。
生成器改为读提交信息，这些用例锁住它的解析与排版行为。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "build"))

import release_notes as rn  # noqa: E402


def log(*records):
    """把 (subject, body) 拼成 git log 的机器可读输出。"""
    out = ""
    for subject, body in records:
        out += f"{subject}{rn.FIELD}{body}{rn.RECORD}"
    return out


def run_script(*args, cwd=None):
    """用子进程跑发布说明脚本，两端都钉成 UTF-8。

    输出编码原本跟着环境走：子进程没设 PYTHONIOENCODING 时，Windows 中文环境
    print 出来的是 GBK；父进程 text=True 按本机编码解码，一旦开发者的 shell 带了
    PYTHONIOENCODING=utf-8（写中文日志的常用设置），解码失败还会把 stdout 变成
    None，断言于是报 ``argument of type 'NoneType' is not iterable``，看起来像
    脚本坏了。两边都显式指定编码后，这条用例不再看环境脸色。
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(ROOT / "build" / "release_notes.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=cwd or ROOT, timeout=120)


class ParseTests(unittest.TestCase):
    def test_split_fields_and_records(self):
        commits = rn.parse_commits(log(("feat(repo): 加了功能", "- 要点一\n- 要点二"),
                                       ("fix(repo): 修了问题", "- 只有一个要点")))
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0].text, "加了功能")
        self.assertEqual(commits[0].bullets, ["要点一", "要点二"])
        self.assertEqual(commits[1].bullets, ["只有一个要点"])

    def test_empty_body_is_allowed(self):
        commit = rn.parse_commits(log(("chore(repo): 杂活", "")))[0]
        self.assertEqual(commit.bullets, [])
        self.assertEqual(commit.text, "杂活")

    def test_empty_input(self):
        self.assertEqual(rn.parse_commits(""), [])
        self.assertEqual(rn.parse_commits(None), [])

    def test_subject_without_conventional_prefix(self):
        commit = rn.parse_commits(log(("随手改的东西", "- 说明")))[0]
        self.assertEqual(commit.type, "")
        self.assertEqual(commit.text, "随手改的东西")

    def test_scope_parsed_and_optional(self):
        with_scope = rn.parse_commits(log(("feat(ui): 有 scope", "")))[0]
        self.assertEqual(with_scope.scope, "ui")
        without = rn.parse_commits(log(("feat: 没 scope", "")))[0]
        self.assertEqual(without.scope, "")
        self.assertEqual(without.type, "feat")

    def test_non_bullet_body_lines_are_ignored(self):
        commit = rn.parse_commits(log(("feat(repo): 标题", "这是说明段落\n- 才是要点")))[0]
        self.assertEqual(commit.bullets, ["才是要点"])


class BreakingTests(unittest.TestCase):
    def test_chinese_marker_in_body(self):
        commit = rn.parse_commits(
            log(("fix(repo): 改了默认行为", "- 行为变更：默认值改了")))[0]
        self.assertTrue(commit.is_breaking)

    def test_english_markers(self):
        for body in ("- BREAKING: behaviour changed", "- breaking change: x",
                     "- Breaking Change: y"):
            commit = rn.parse_commits(log(("fix(repo): 改", body)))[0]
            self.assertTrue(commit.is_breaking, body)

    def test_marker_mentioned_mid_bullet_is_not_breaking(self):
        """本仓库自己的提交里就有「提交正文含『行为变更』的会被提到顶部」这种叙述。

        全文匹配会把它也提到顶部——真实发生过一次，所以规则是「以标记开头」。
        """
        commit = rn.parse_commits(log(
            ("ci(repo): 发布说明改为从提交信息生成",
             "- 提交正文含「行为变更」的会被提到说明顶部单独提示")))[0]
        self.assertFalse(commit.is_breaking)

    def test_marker_at_bullet_start_is_breaking(self):
        commit = rn.parse_commits(log(
            ("fix(repo): 改了默认值", "- 行为变更：默认值从 A 改成 B")))[0]
        self.assertTrue(commit.is_breaking)

    def test_marker_in_the_subject_alone_is_not_breaking(self):
        """主题行里出现这个词不算：本仓库会把它当话题词（工具自身的改动就踩过）。"""
        commit = rn.parse_commits(log(("feat(repo): 行为变更：新的默认行为", "- 细节")))[0]
        self.assertFalse(commit.is_breaking)

    def test_normal_commit_is_not_breaking(self):
        commit = rn.parse_commits(log(("fix(repo): 常规修复", "- 只是修了个错")))[0]
        self.assertFalse(commit.is_breaking)


class PickPreviousTests(unittest.TestCase):
    """上一个标签的选择：必须按版本号而不是提交时间。"""

    def test_returns_tag_before_current(self):
        tags = ["v1.8.0", "v1.7.0", "v1.6.0"]
        self.assertEqual(rn._pick_previous(tags, "v1.8.0"), "v1.7.0")
        self.assertEqual(rn._pick_previous(tags, "v1.6.0"), "")

    def test_falls_back_to_largest_when_current_unknown(self):
        # 新打的标签还没出现在 git tag 列表里（例如在 tag 上运行但克隆较浅）
        self.assertEqual(rn._pick_previous(["v1.7.0", "v1.6.0"], "v1.8.0"), "v1.7.0")

    def test_empty_list(self):
        self.assertEqual(rn._pick_previous([], "v1.8.0"), "")


class BuildNotesTests(unittest.TestCase):
    def _notes(self, *records, **kwargs):
        kwargs.setdefault("tag", "v9.9.9")
        kwargs.setdefault("previous", "v9.9.8")
        kwargs.setdefault("repo", "owner/repo")
        return rn.build_notes(rn.parse_commits(log(*records)), **kwargs)

    def test_sections_are_grouped_with_bilingual_titles(self):
        notes = self._notes(("feat(repo): 新功能", "- 细节"),
                            ("fix(repo): 修问题", "- 细节"))
        self.assertIn("## 新增 / Features", notes)
        self.assertIn("## 修复 / Fixes", notes)
        self.assertLess(notes.index("新增 / Features"), notes.index("修复 / Fixes"))
        self.assertIn("- 新功能", notes)
        self.assertIn("  - 细节", notes)

    def test_repo_scope_is_hidden_but_real_scopes_shown(self):
        notes = self._notes(("feat(repo): 收起 scope", ""), ("feat(ui): 显示 scope", ""))
        self.assertIn("- 收起 scope", notes)
        self.assertNotIn("**repo**", notes)
        self.assertIn("- **ui**: 显示 scope", notes)

    def test_breaking_section_is_on_top_and_not_duplicated(self):
        notes = self._notes(("feat(repo): 常规新增", "- 细节"),
                            ("fix(repo): 剪贴板默认改了", "- 行为变更：默认从自动改为提示"))
        self.assertIn("## ⚠ 行为变更 / Behaviour change", notes)
        self.assertLess(notes.index("行为变更"), notes.index("新增 / Features"))
        # 只在顶部出现一次
        self.assertEqual(notes.count("- 剪贴板默认改了"), 1)
        self.assertNotIn("## 修复 / Fixes", notes)

    def test_version_bump_is_skipped_by_default(self):
        notes = self._notes(("fix(repo): 真修复", "- 细节"),
                            ("chore(repo): 提升版本号到 9.9.9", "- 改版本号"))
        self.assertNotIn("提升版本号", notes)

    def test_version_bump_can_be_kept(self):
        notes = self._notes(("chore(repo): 提升版本号到 9.9.9", "- 改版本号"),
                            skip_texts=())
        self.assertIn("提升版本号", notes)

    def test_unknown_type_goes_to_fallback_section(self):
        notes = self._notes(("wip(repo): 试验中的东西", ""))
        self.assertIn("## 其他改动 / Other changes", notes)
        self.assertIn("试验中的东西", notes)

    def test_changelog_link_uses_previous_tag(self):
        notes = self._notes(("feat(repo): x", ""))
        self.assertIn(
            "**Full Changelog**: https://github.com/owner/repo/compare/v9.9.8...v9.9.9",
            notes)

    def test_first_release_links_the_tag_instead(self):
        notes = self._notes(("feat(repo): x", ""), previous="")
        self.assertIn("**Release**: https://github.com/owner/repo/releases/tag/v9.9.9", notes)
        self.assertNotIn("Full Changelog", notes)

    def test_no_repo_means_no_link(self):
        notes = self._notes(("feat(repo): x", ""), repo="")
        self.assertNotIn("github.com", notes)

    def test_empty_range_still_returns_valid_markdown(self):
        notes = rn.build_notes([], "v9.9.9", "v9.9.8", "owner/repo")
        self.assertIn("没有解析到提交", notes)
        self.assertTrue(notes.endswith("\n"))


class RealRepositoryTests(unittest.TestCase):
    """用本仓库真实的标签区间跑一遍（标签缺失时跳过，保持离线套件稳定）。"""

    def _tags(self):
        proc = subprocess.run(["git", "tag", "--list", "v*"], cwd=ROOT,
                              capture_output=True, text=True)
        return [t.strip() for t in proc.stdout.splitlines() if t.strip()]

    def test_v180_notes_contain_the_ux_work(self):
        tags = self._tags()
        if "v1.7.0" not in tags or "v1.8.0" not in tags:
            self.skipTest("标签不存在，跳过真实区间检查")
        notes = rn.build_notes(
            rn.parse_commits(rn.read_git_log("v1.7.0", "v1.8.0")),
            "v1.8.0", "v1.7.0", "Monkey0803/downloadYTB")
        self.assertIn("剪贴板链接改为默认", notes)
        self.assertIn("字幕语言支持勾选常用语言", notes)
        self.assertIn("compare/v1.7.0...v1.8.0", notes)
        self.assertNotIn("提升版本号", notes)
        # 每个提交正文的要点都要展开，否则说明就只有一行标题
        self.assertGreater(notes.count("\n  - "), 10)


class InstallSectionTests(unittest.TestCase):
    """安装说明必须出现在每份说明里（免签名应用的首启门槛）。"""

    def _notes(self, *records):
        commits = rn.parse_commits(log(*records)) if records else []
        return rn.build_notes(commits, "v9.9.9", "v9.9.8", "o/r")

    def test_present_even_with_commits(self):
        notes = self._notes(("feat(repo): 新功能", "- 细节"))
        self.assertIn("## 安装 / Installation", notes)

    def test_present_even_without_any_commit(self):
        """标签区间为空时也必须附上指引——那正是用户最需要它的时候。"""
        self.assertIn("## 安装 / Installation", self._notes())

    def test_covers_all_three_platform_hints(self):
        notes = self._notes(("fix(repo): 修个问题", "- 细节"))
        self.assertIn("xattr -dr com.apple.quarantine", notes)
        self.assertIn("Run anyway", notes)
        self.assertIn("chmod +x VideoDownloader", notes)
        for asset in ("macos-arm64", "windows-x64", "linux-x64"):
            self.assertIn(asset, notes, asset)

    def test_is_bilingual_and_says_it_is_not_corruption(self):
        notes = self._notes(("fix(repo): 修个问题", "- 细节"))
        self.assertIn("不是文件损坏", notes)
        self.assertIn("not** a corrupted download", notes)

    def test_sits_after_the_changelog(self):
        notes = self._notes(("feat(repo): 新功能", "- 细节"))
        self.assertLess(notes.index("Full Changelog"), notes.index("## 安装"))

    def test_install_only_prints_just_the_section(self):
        result = run_script("--install-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## 安装 / Installation", result.stdout)
        self.assertNotIn("Full Changelog", result.stdout)
        self.assertNotIn("## 新增", result.stdout)

    def test_install_only_works_without_a_git_repository(self):
        """CI 的兜底路径：说明生成失败时用它救场，不能依赖仓库或标签。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = run_script("--install-only", cwd=tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("xattr -dr com.apple.quarantine", result.stdout)

    def test_install_only_writes_the_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "release-notes.md")
            result = run_script("--install-only", "--output", target)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(target, encoding="utf-8") as handle:
                self.assertIn("## 安装 / Installation", handle.read())

    def test_workflow_falls_back_to_the_install_section(self):
        """CI 的回退链里必须保留安装说明，否则一次生成故障就把它弄丢了。"""
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8")
        self.assertIn("--install-only --output release-notes.md", workflow)


if __name__ == "__main__":
    unittest.main()
