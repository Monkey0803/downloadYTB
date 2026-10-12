# Repository Guidelines

## Project Structure

- `main.py` is the application entry point.
- `app/` contains the Tkinter UI (`gui.py`, `widgets.py`), download engines (`core.py`, `images.py`), settings, themes, localization, and macOS menubar integration.
- `assets/` stores icons and logo variants.
- `test_*.py` contains focused unit, GUI, packaging, and live-network checks.
- `build/` contains the macOS and Windows PyInstaller scripts, plus
  `release_notes.py`, which turns commit messages into the GitHub Release body.

Keep network and media-engine logic in `app/core.py` or `app/images.py`; keep panels responsible for UI state, callbacks, and threading. Perform slow work in background threads and update Tk widgets through `after(...)` on the main thread.

## Development Commands

Create an environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run locally with `.venv/bin/python main.py`. Run fast checks with:

```bash
PYTHONPYCACHEPREFIX=/tmp/downloadytb-pycache .venv/bin/python -m py_compile app/*.py main.py
git diff --check
```

Build platform-specific bundles from the repository root with `bash build/build_macos.sh` or `build\\build_windows.bat`.

## Task Completion Routine

After completing a repository task on macOS, rebuild and open the app for a final smoke check:

```bash
bash build/build_macos.sh && open dist/VideoDownloader.app
```

Confirm that the rebuilt app launches and that the changed behavior is visible before handing off the task. If the build or launch cannot run because of missing platform dependencies, report that limitation explicitly.

## Coding and Naming Style

Use Python 3 style, four-space indentation, descriptive `snake_case` functions and variables, `PascalCase` classes, and concise comments for platform or threading constraints. Reuse shared widgets and theme roles instead of duplicating UI drawing code. Do not commit personal settings, generated downloads, screenshots, `.venv`, or build output.

## Testing Guidelines

Run `test_i18n.py`, `test_save_dir_sync.py`, and relevant unit tests for local changes. The offline suite is `test_i18n.py`, `test_save_dir_sync.py`, `test_weibo.py`, `test_browser_cookies.py`, `test_audio_options.py`, `test_windowing.py`, `test_diagnostics.py`, `test_shortcuts.py`, `test_platforms.py`, `test_dock.py`, `test_video_options.py`, `test_image_skip.py`, `test_video_panel.py`, `test_playlist.py`, `test_taskbar.py`, `test_taskbar_kde.py`, `test_clipboard.py`, `test_status_detail.py`, `test_subtitle_picker.py`,
`test_release_notes.py`, and `test_history.py`; they all run without network access and only the Tk-based ones (`test_shortcuts.py`, `test_video_panel.py`, `test_clipboard.py`, `test_status_detail.py`, `test_subtitle_picker.py`, `test_history.py`) need a display; they create a real window and therefore patch `settings.load`/`settings.save` so they cannot touch the user's own `settings.json`. **Each Tk-based test file must create exactly one `App` for the whole file and never destroy it**: the first Tk initialisation costs seconds, and destroying a Tk root and creating another one in the same process segfaults on macOS (both observed here). Reusing one App also took `test_clipboard.py` from 171s to 3s. Cards built with `GlassCard` need a height that fits their content: it pins the inner body to `height - padding`, so oversized content is silently clipped (a history row at 96px clipped its whole button row). `test_history.py` asserts body height against `winfo_reqheight()` to catch that class of bug. `test_dock.py` uses a fake AppKit module and asserts against the real `NSApp` semantics without opening a window. GUI scripts such as `test_smoke_ui.py` require Tk; `test_core.py`, `test_images.py`, and several GUI flows use live services and may require valid URLs, login cookies, and network access. Use a dedicated test download directory and never include private URLs or cookies in fixtures.

## Commits and Pull Requests

A global `commit-msg` hook (`~/.config/git/hooks/commit-msg`, enabled through
`core.hooksPath`) validates every commit message and rejects the commit outright when it
does not match. It applies to this repository too, so commit messages must follow the
format it enforces:

- **Subject**: `<type>(<scope>): <description>`, where `<type>` is one of
  `feat|fix|refactor|chore|docs|test|perf|build|ci|revert` and `<scope>` matches
  `[a-z0-9._-]+`. Use the scope `repo` here: the companion `prepare-commit-msg` hook
  derives it for this repository, because none of its `unity`/`ios`/`android` path
  patterns ever match (`refactor(repo):` for code changes, `docs(repo):` for
  Markdown-only ones). A subject without a scope is rejected.
- **The subject must contain Chinese.** English-only subjects are rejected, so the
  English-only examples in older revisions of this file no longer apply.
- **Line 2 must be empty**, followed by the body.
- **Body**: at least two lines; every non-empty line must start with `- `, the body must
  contain Chinese, and it must be at least 20 characters long.
- Merge commits keep Git's generated message and are exempt.

A message that passes the hook:

~~~text
fix(repo): 修复浏览器 Cookie 不可读时微博图片解析整体失败

- 新增 browser_cookies 模块统一持有 Cookie 配置并识别读取失败
- 打包 App 未获完全磁盘访问权限时降级为匿名解析而非直接报错
- Cookie 读取失败输出可操作的权限指引，替代 yt-dlp 的原始消息
~~~

Always pass an explicit message with `-m`/`-F`. Running a bare `git commit` lets the
companion `prepare-commit-msg` hook synthesize a generic `refactor(repo):` /
`docs(repo):` skeleton, which is compliant but says nothing useful about the change.

If a commit changes user-visible default behaviour, start a body line with
`行为变更` (or `BREAKING`). Release notes generated by `build/release_notes.py` lift those
commits into a separate highlighted section at the top.

Keep one logical change per commit. Pull requests should describe the problem, implementation, verification commands, unverified platform/network cases, and include screenshots for visible UI changes. Before submitting, inspect `git diff --cached --stat` and `git diff --cached --check`.

## Release Notes

GitHub's `--generate-notes` only lists pull requests and new contributors, and this
repository commits directly with no PRs, so it produced a one-line changelog for
v1.1.0–v1.7.0. The publish job instead runs `build/release_notes.py`, which reads commit
messages (already enforced by the commit-msg hook), groups them by type, and expands each
commit body's `- ` bullets into sub-items.

Two constraints keep it working:

- The `publish-release` job must check out with `fetch-depth: 0` **and** `fetch-tags: true`.
  With the default depth of 1 there is no previous tag, so the notes silently come out
  empty — verified by running the generator in a `--depth 1` clone, which found 0 items.
- Failures fall back to a minimal body so a notes bug never blocks publishing assets.

Preview the notes for any range before tagging:

~~~bash
python3 build/release_notes.py v1.8.0 --repo Monkey0803/downloadYTB
~~~

## Security and Configuration

Browser-cookie selections and paths are stored locally. Treat them as sensitive: do not commit settings files, credentials, cookies, or personal media. Respect target-platform terms, copyright, privacy requirements, and rate limits.
