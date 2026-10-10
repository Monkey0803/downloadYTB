"""Release 说明生成器（build/release_notes.py）的离线测试。

背景：`gh release create --generate-notes` 只汇总 PR 与新贡献者，本仓库是直接
提交、没有 PR，所以 v1.1.0–v1.7.0 的 Release 页面只有一行 changelog 链接。
生成器改为读提交信息，这些用例锁住它的解析与排版行为。
"""

import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
