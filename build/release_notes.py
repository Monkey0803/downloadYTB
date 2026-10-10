#!/usr/bin/env python3
"""从 git 提交生成 Release 说明。

为什么不用 ``gh release create --generate-notes``：GitHub 的自动说明只汇总
**Pull Request** 与新贡献者，而本仓库是直接提交、没有 PR，因此生成出来只有一行
changelog 链接，用户看不到本版改了什么（v1.1.0–v1.7.0 都是这样）。

本脚本改为读提交信息（提交规范已由全局 commit-msg 钩子强制），按类型分组，
并把每个提交正文里的 ``- `` 要点作为子条目展开，于是「说明有内容」这件事变成
提交时顺手写的事实，而不是发版时再补的负担。

用法::

    python3 build/release_notes.py [tag] [--prev TAG] [--repo OWNER/REPO]

- ``tag`` 省略时取当前 HEAD 所在的最新标签
- ``--prev`` 省略时自动取比 ``tag`` 早的最近一个 ``v*`` 标签
- 输出 Markdown 到 stdout；工作流把它写进 release body

设计上把纯逻辑（解析 + 排版）与 IO（读 git）分开，这样离线单测可以直接喂
假的提交文本，不依赖仓库历史。
"""

import argparse
import os
import re
import subprocess
import sys

# 提交规范里的类型 -> (中文标题, 英文标题)
SECTIONS = (
    ("feat", "新增", "Features"),
    ("fix", "修复", "Fixes"),
    ("perf", "性能", "Performance"),
    ("refactor", "重构", "Refactoring"),
    ("docs", "文档", "Documentation"),
    ("test", "测试", "Tests"),
    ("build", "构建与 CI", "Build & CI"),
    ("ci", "构建与 CI", "Build & CI"),
    ("revert", "回退", "Reverts"),
    ("chore", "其他", "Chores"),
)
FALLBACK_SECTION = ("其他改动", "Other changes")

# 提交正文出现这些词时，视为「用户可见的行为变更」，在说明顶部单独提示
BREAKING_MARKERS = ("行为变更", "breaking change", "breaking-change", "BREAKING")

# 这些 scope 对读者没有意义，不在说明里显示（本仓库的提交 scope 恒为 repo）
HIDDEN_SCOPES = ("repo",)
# 版本号提交对使用者没有信息量，默认从说明里剔除
DEFAULT_SKIP_TEXTS = ("提升版本号",)

_SUBJECT_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?!?:\s*(?P<text>.+)$")

FIELD = "\x1f"
RECORD = "\x1e"


class Commit:
    """一个提交：主题行 + 正文要点。"""

    def __init__(self, subject: str, body: str = ""):
        self.subject = subject.strip()
        self.body = body or ""
        match = _SUBJECT_RE.match(self.subject)
        self.type = match.group("type") if match else ""
        self.scope = (match.group("scope") or "") if match else ""
        self.text = match.group("text").strip() if match else self.subject

    @property
    def bullets(self):
        """正文里 ``- `` 开头的要点（提交规范要求正文都是要点）。"""
        out = []
        for line in self.body.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                out.append(stripped[2:].strip())
        return out

    @property
    def is_breaking(self):
        haystack = (self.subject + "\n" + self.body).lower()
        return any(marker.lower() in haystack for marker in BREAKING_MARKERS)

    def __repr__(self):  # pragma: no cover - 调试用
        return f"<Commit {self.type}: {self.text[:30]!r}>"


def parse_commits(log_text: str):
    """解析 ``git log --format=%s%x1f%b%x1e`` 的输出。"""
    commits = []
    for record in (log_text or "").split(RECORD):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split(FIELD)
        subject = parts[0]
        body = parts[1] if len(parts) > 1 else ""
        commits.append(Commit(subject, body))
    return commits


def read_git_log(previous, current):
    """读取 ``previous..current`` 的提交（不含 merge）。"""
    fmt = f"%s{FIELD}%b{RECORD}"
    args = ["git", "log", f"--format={fmt}", "--no-merges"]
    args.append(f"{previous}..{current}" if previous else current)
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git log 失败：{proc.stderr.strip()}")
    return proc.stdout


def latest_tag(pattern="v*"):
    """当前 HEAD 可达的最新标签。"""
    proc = subprocess.run(["git", "describe", "--tags", "--abbrev=0", "--match", pattern],
                          capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _pick_previous(tags, current):
    """在「按版本号倒序」的标签列表里选出 current 的下一个（纯函数，便于测试）。

    按版本号而不是提交时间：本仓库重排过历史、移动过标签，按时间排序会选错。
    current 不在列表里时（新标签尚未出现在列表中）退回到列表里最新的那个。
    """
    for index, tag in enumerate(tags):
        if tag == current:
            return tags[index + 1] if index + 1 < len(tags) else ""
    for tag in tags:
        if tag != current:
            return tag
    return ""


def previous_tag(current, pattern="v*"):
    """比 current 早的最近一个标签（按版本号排序，不按提交时间）。"""
    proc = subprocess.run(["git", "tag", "--sort=-v:refname", "--list", pattern],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return ""
    tags = [t.strip() for t in proc.stdout.splitlines() if t.strip()]
    return _pick_previous(tags, current)


def compare_url(repo, previous, current):
    base = f"https://github.com/{repo}" if repo else ""
    if base and previous:
        return f"{base}/compare/{previous}...{current}"
    if base:
        return f"{base}/releases/tag/{current}"
    return ""


def build_notes(commits, tag, previous="", repo="", skip_texts=DEFAULT_SKIP_TEXTS):
    """把提交列表排版成 Release 说明（Markdown）。

    skip_texts 用于剔除「提升版本号到 x.y.z」这类对使用者没有信息量的提交。
    """
    if skip_texts:
        commits = [c for c in commits
                   if not any(text in c.text for text in skip_texts)]
    lines = []
    breaking = [c for c in commits if c.is_breaking]
    if breaking:
        lines.append("## ⚠ 行为变更 / Behaviour change")
        lines.append("")
        for commit in breaking:
            lines.append(f"- {commit.text}")
            for bullet in commit.bullets:
                lines.append(f"  - {bullet}")
        lines.append("")

    grouped = {}
    for commit in commits:
        if commit.is_breaking:
            continue
        grouped.setdefault(commit.type, []).append(commit)

    for key, zh, en in SECTIONS:
        items = grouped.get(key)
        if not items:
            continue
        lines.append(f"## {zh} / {en}")
        lines.append("")
        for commit in items:
            scope = (f"**{commit.scope}**: "
                     if commit.scope and commit.scope not in HIDDEN_SCOPES else "")
            lines.append(f"- {scope}{commit.text}")
            for bullet in commit.bullets:
                lines.append(f"  - {bullet}")
        lines.append("")

    unknown = [c for key, items in grouped.items() if key not in {k for k, _z, _e in SECTIONS}
               for c in items]
    unknown += grouped.get("", [])
    if unknown:
        lines.append(f"## {FALLBACK_SECTION[0]} / {FALLBACK_SECTION[1]}")
        lines.append("")
        for commit in unknown:
            lines.append(f"- {commit.text}")
        lines.append("")

    if not commits:
        lines.append("本版没有解析到提交（可能是首次发布或标签区间为空）。")
        lines.append("")

    url = compare_url(repo, previous, tag)
    if url:
        if previous:
            lines.append(f"**Full Changelog**: {url}")
        else:
            lines.append(f"**Release**: {url}")
    return "\n".join(lines).strip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="从 git 提交生成 Release 说明")
    parser.add_argument("tag", nargs="?", default="", help="要生成说明的标签")
    parser.add_argument("--prev", default="", help="上一个标签（默认自动推断）")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                        help="OWNER/REPO，用于生成 changelog 链接")
    parser.add_argument("--output", default="", help="写入文件（默认打印到 stdout）")
    parser.add_argument("--keep-version-bumps", action="store_true",
                        help="保留「提升版本号」提交（默认剔除）")
    args = parser.parse_args(argv)

    tag = args.tag or latest_tag()
    if not tag:
        print("找不到标签：请在 tag 上运行，或用参数指定", file=sys.stderr)
        return 1
    previous = args.prev or previous_tag(tag)
    if not previous and not args.prev:
        # 没有标签时说明只能退化成一堆提交，通常意味着浅克隆：Actions 里必须
        # fetch-depth: 0 + fetch-tags: true，否则 Release 说明会静默变空
        print("警告：本地找不到其它 v* 标签，无法确定上一个版本（浅克隆？"
              "请用 fetch-depth: 0 与 fetch-tags: true）", file=sys.stderr)

    if previous:
        check = subprocess.run(["git", "rev-parse", "--verify", f"{previous}^{{commit}}"],
                               capture_output=True, text=True)
        if check.returncode != 0:
            print(f"警告：找不到上一个标签 {previous}（浅克隆？），改为列出全部提交",
                  file=sys.stderr)
            previous = ""

    commits = parse_commits(read_git_log(previous, tag))
    notes = build_notes(commits, tag, previous, args.repo,
                        skip_texts=() if args.keep_version_bumps else DEFAULT_SKIP_TEXTS)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(notes)
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
