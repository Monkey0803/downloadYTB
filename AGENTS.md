# Repository Guidelines

## Project Structure

- `main.py` is the application entry point.
- `app/` contains the Tkinter UI (`gui.py`, `widgets.py`), download engines (`core.py`, `images.py`), settings, themes, localization, and macOS menubar integration.
- `assets/` stores icons and logo variants.
- `test_*.py` contains focused unit, GUI, packaging, and live-network checks.
- `build/` contains the macOS and Windows PyInstaller scripts.

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

Run `test_i18n.py`, `test_save_dir_sync.py`, and relevant unit tests for local changes. The offline suite is `test_i18n.py`, `test_save_dir_sync.py`, `test_weibo.py`, `test_browser_cookies.py`, `test_audio_options.py`, `test_windowing.py`, `test_diagnostics.py`, and `test_shortcuts.py`; all of them run without network access, and the last three need no Tk except `test_shortcuts.py`, which creates a real window and therefore patches `settings.load`/`settings.save` so it cannot touch the user's own `settings.json`. GUI scripts such as `test_smoke_ui.py` require Tk; `test_core.py`, `test_images.py`, and several GUI flows use live services and may require valid URLs, login cookies, and network access. Use a dedicated test download directory and never include private URLs or cookies in fixtures.

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

Keep one logical change per commit. Pull requests should describe the problem, implementation, verification commands, unverified platform/network cases, and include screenshots for visible UI changes. Before submitting, inspect `git diff --cached --stat` and `git diff --cached --check`.

## Security and Configuration

Browser-cookie selections and paths are stored locally. Treat them as sensitive: do not commit settings files, credentials, cookies, or personal media. Respect target-platform terms, copyright, privacy requirements, and rate limits.
