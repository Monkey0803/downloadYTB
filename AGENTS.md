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
`test_release_notes.py`, `test_history.py`, `test_settings_layout.py`, `test_keyboard.py`, `test_onboarding.py`, `test_menubar.py`, `test_contrast.py`, `test_font_scale.py`, `test_queue.py`, and `test_platform_identity.py`; they all run without network access and only the Tk-based ones (`test_shortcuts.py`, `test_video_panel.py`, `test_clipboard.py`, `test_status_detail.py`, `test_subtitle_picker.py`, `test_history.py`, `test_settings_layout.py`, `test_keyboard.py`, `test_onboarding.py`) need a display; they create a real window and therefore patch `settings.load`/`settings.save` so they cannot touch the user's own `settings.json`. **Each Tk-based test file must create exactly one `App` for the whole file and never destroy it**: the first Tk initialisation costs seconds, and destroying a Tk root and creating another one in the same process segfaults on macOS (both observed here). Reusing one App also took `test_clipboard.py` from 171s to 3s. Cards built with `GlassCard` need a height that fits their content: it pins the inner body to `height - padding`, so oversized content is silently clipped (a history row at 96px clipped its whole button row). `test_history.py` asserts body height against `winfo_reqheight()` to catch that class of bug. `test_dock.py` uses a fake AppKit module and asserts against the real `NSApp` semantics without opening a window. Background work follows one pattern: the worker thread does the slow call and
hops back with `self.app.after(0, callback)`. That hop **requires a running
`mainloop()`** — Tk raises `RuntimeError: main thread is not in main loop` when a
non-main thread calls `after` while the main thread is only pumping `update()`, so a test
that drives the UI without `mainloop()` must take over `app.after` (record the callback,
then invoke it on the main thread) to verify the result path. `test_video_panel.py`'s
`PlaylistLoadMoreTests` shows the pattern. Any callback that touches a dialog must also
check `winfo_exists()` first: the user can close the picker while a probe is in flight,
and a stale callback then raises `TclError: bad window path name`.

GUI scripts such as `test_smoke_ui.py` require Tk; `test_core.py`, `test_images.py`, and several GUI flows use live services and may require valid URLs, login cookies, and network access. Use a dedicated test download directory and never include private URLs or cookies in fixtures.

## First-Run Onboarding

`settings.load()` adds an in-memory `_first_run` flag (true only when the settings file does
not exist yet). It is deliberately **not** in `DEFAULTS`, so `save()` never writes it and
faked settings in tests never carry it — that keeps the modal welcome dialog from appearing
during a test run, where `wait_window()` would hang the suite instead of failing it.
`App.needs_onboarding()` requires `_first_run` and an unset `onboarding_done`; dismissing the
dialog (either button or the window close) persists `onboarding_done` exactly once.

## Platform Identity

A panel's identity (`self.platform`) is the **ASCII key** from `platforms.PLATFORMS`
(`youtube`, `x`, `instagram`, `douyin`, `bilibili`, `weibo`) — the same key the engines,
per-platform cookie overrides and save-directory keys use. Display names ("YouTube", "微博")
are no longer used as identifiers: when the two systems coexisted, anything that looked a key
up in an ASCII-keyed table (for example `browser_cookies.platform_override`) would silently
miss, and the hardcoded-text check needed a whitelist for `gui.py`.

Use `gui.platform_label(key)` when a platform has to be shown to the user (history and queue
rows do); it maps the key through `PLATFORM_LABEL_KEYS`, so the name follows the UI language.
`gui.download_dir_key()` still lowercases ASCII names (older call sites passed "YouTube") and
maps anything non-ASCII to `unknown` — the keys it returns are persisted in users'
`settings.json`, so they must not change.

`test_platform_identity.py` guards all of it, and because the whitelist is gone the
hardcoded-text check in `test_i18n.py` now flags a display name reintroduced as an identifier.

## Download Queue

The queue stores **only a URL and a platform key** — deliberately not a snapshot of the
quality/subtitle/save-location options. Executing a job therefore reuses the panel's normal
flow: switch page, fill the URL, call `on_download()` (which probes first) and let the
current settings apply. That keeps the queue out of the core download state machine; the
trade-off is stated in the page hint so users are not surprised.

`advance_queue()` refuses to take a job while **any** panel reports `_busy`, so two downloads
never fight over one progress bar. Panels call `App.notify_download_finished()` from
`_reset_after_download()`, which schedules the next advance with `after(...)` — advancing
synchronously inside a panel's own reset would re-enter the download flow and corrupt its
state.

## Colours And Text Size

`test_contrast.py` computes WCAG contrast for every text-on-background pair in both
palettes and fails below 4.5:1 (normal text) or 3.0:1 (secondary text and non-text
graphics). Disabled pairs are exempt per WCAG 1.4.3 but must be listed explicitly, and the
test complains if an exempted pair starts passing.

Two roles exist because one accent cannot serve both jobs: `ACCENT` is for non-text accents
(checkmarks, focus rings) while `ACCENT_FILL` fills buttons whose label is white — white on
`ACCENT` is only 3.65:1. `PROGRESS_FILL` is per-theme for the same reason (a progress bar
must reach 3:1 against its own track, which needs a darker blue in light mode and a lighter
one in dark mode).

Text size is a stored scale (`font_scale`: standard/large/xlarge) applied in `_setup_fonts`.
Changing it **rebuilds the interface**, because card heights and ellipsis widths are measured
from the fonts. `GlassCard.sync_body()` exists for that: `set_height()` alone is not enough,
since a height that happens to equal the canvas's current height produces no `<Configure>`
event and leaves the inner body at its old size — silently clipping content (measured 63px
body against 91px of content on the settings page).

If a colour or font change seems not to apply, clear `app/__pycache__` first. The cause is
narrower than "stale cache": CPython validates a `.pyc` by source **mtime and size**, and
every hex colour edit keeps the file size identical — so a value changed and restored within
the same second can reuse bytecode compiled from the old value (reproduced twice here: the
palette kept reporting `#3A4150` while the source said `#78829A`). Same-size, same-second
edits are exactly what colour flipping produces, so suspect the cache before the code.

## Menubar Tests And AppKit Ordering

`test_menubar.py` exercises the real `NSStatusBar`/`NSMenu` code (the only place pyobjc is
used), because that module's past bug was index-based menu updates. Two things to know:

- **Tk must be initialised before any pyobjc AppKit object is created.** If a test creates
  an `NSStatusItem` first and a Tk root afterwards, Tk's init aborts the process with
  `-[NSApplication macOSVersion]: unrecognized selector` (SIGABRT, reproduced here). The real
  app has the right order — Tk's mainloop runs first, the menubar is created later — so the
  test file creates its shared `App` in `setUpClass` before building any status item.
- Menu items are indexed by **action name** (`controller._items[action]`), never by position.
  A positional `itemAtIndex_(2)` update silently translates the wrong rows once an item is
  added; `test_menubar.py` asserts a full language switch instead.

## Keyboard Support

Custom Canvas widgets (`PillButton`, `CheckPill`, `SegmentedControl`, `PillSelect`,
`SidebarItem`, `LogoSwatch`) are mouse-only unless they opt in. Each of them therefore
sets `takefocus=1` (disabled states set it back to 0 so Tab never lands on something that
cannot be clicked), binds `widgets.ACTIVATION_KEYS` (Return / keypad Enter / Space) through
`widgets.bind_activation`, and draws `widgets.draw_focus_ring` while focused. The ring uses
the widget's own foreground colour, which is guaranteed to contrast with its fill in both
themes. Add the same four things to any new interactive widget; `test_keyboard.py` checks
all of them, including that Tab from the URL field reaches a Canvas widget.

`ImagePicker` is the exception that needs different plumbing: its thumbnails are canvas
items, not child widgets, so there is nothing for Tab to land on. It keeps its own cursor
(`cursor_index()`), draws a ring in the theme's text colour while focused (the `cursor` tag,
visually distinct from the accent outline of a selected cell), binds arrow/Home/End/activation
keys, and scrolls the cursor into view. Its `on_change` is a **no-arg** callback like the
panels pass — `list.append` would raise a `TypeError` that Tk swallows, which looks exactly
like "the callback was never called".

Tk detail that bites when testing this: `widget.event_generate("<Return>")` only fires the
binding when that widget **holds focus** — otherwise the key goes to the focused widget and
nothing happens. Tests must call `focus_force()` first (the test window is usually not
frontmost, so `focus_set()` is not enough).

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
