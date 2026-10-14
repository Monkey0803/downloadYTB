# Repository Guidelines

## Project Structure

- `main.py` is the application entry point.
- `app/` contains the Tkinter UI (`gui.py`, `widgets.py`), download engines (`core.py`, `images.py`), settings, themes, localization, and macOS menubar integration. Pure helpers that used to live in `gui.py` now sit in `urlutil.py` (links, platform labels, folder names), `fspath.py` (asset paths, opening files), `dialogs.py` (the four pop-ups) and `settings_io.py` (settings import/export); the download panels live in `panels.py`, the platform/queue/history pages in `pages.py`, the settings page in `settingspage.py`, and taskbar-on/off wrappers in `taskbar.py`. After the split `gui.py` holds only `App` and `run()`. `gui.py` re-imports those names so `gui.parse_links`-style call sites keep working, and it is now roughly half its former size.
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
`test_release_notes.py`, `test_installers.py`, `test_chapters.py`, `test_history.py`, `test_settings_layout.py`, `test_keyboard.py`, `test_onboarding.py`, `test_menubar.py`, `test_contrast.py`, `test_font_scale.py`, `test_queue.py`, `test_platform_identity.py`, `test_post_folder.py`, `test_ytdlp_update.py`, `test_logging.py`, `test_thumbnails.py`, `test_proxy.py`, `test_appupdate.py`, `test_after_download.py`, `test_embed.py`, `test_about.py`, and `test_settings_io.py`; they all run without network access and only the Tk-based ones (`test_shortcuts.py`, `test_video_panel.py`, `test_clipboard.py`, `test_status_detail.py`, `test_subtitle_picker.py`, `test_history.py`, `test_settings_layout.py`, `test_keyboard.py`, `test_onboarding.py`) need a display; they create a real window and therefore patch `settings.load`/`settings.save` so they cannot touch the user's own `settings.json`. **Each Tk-based test file must create exactly one `App` for the whole file and never destroy it**: the first Tk initialisation costs seconds, and destroying a Tk root and creating another one in the same process segfaults on macOS (both observed here). Reusing one App also took `test_clipboard.py` from 171s to 3s. Cards built with `GlassCard` need a height that fits their content: it pins the inner body to `height - padding`, so oversized content is silently clipped (a history row at 96px clipped its whole button row). `test_history.py` asserts body height against `winfo_reqheight()` to catch that class of bug. `test_dock.py` uses a fake AppKit module and asserts against the real `NSApp` semantics without opening a window. Background work follows one pattern: the worker thread does the slow call and
hops back with `self.app.after(0, callback)`. That hop **requires a running
`mainloop()`** — Tk raises `RuntimeError: main thread is not in main loop` when a
non-main thread calls `after` while the main thread is only pumping `update()`, so a test
that drives the UI without `mainloop()` must take over `app.after` (record the callback,
then invoke it on the main thread) to verify the result path. `test_video_panel.py`'s
`PlaylistLoadMoreTests` shows the pattern. Any callback that touches a dialog must also
check `winfo_exists()` first: the user can close the picker while a probe is in flight,
and a stale callback then raises `TclError: bad window path name`.

GUI scripts such as `test_smoke_ui.py` require Tk; `test_core.py`, `test_images.py`, `test_pause.py` (pause/resume against a real video), and several GUI flows use live services and may require valid URLs, login cookies, and network access. Use a dedicated test download directory and never include private URLs or cookies in fixtures.

## Where The Code Lives (And Where Tests Must Stub It)

`gui.py` was split (ROADMAP item 13) by moving whole units out and re-importing their names:
`urlutil.py`, `fspath.py`, `dialogs.py`, `panels.py`, `pages.py`, `settingspage.py`, plus helpers into `widgets.py`/`taskbar.py`.
A re-imported name is the **same object**, so `gui.parse_links` or `gui.EntryPicker` still resolve —
but a test that *stubs* a module-level name must stub it in the module that **calls** it:

- `gui.open_path` / `gui.open_in_file_manager` patches still work for the main window and the
  menubar; the **pages** are stubbed on `pages.open_path` (`HistoryPageTests`) and the **panels** on
  `panels.open_path` (`PanelHistoryWiringTests`) — three modules, three stub targets, one shared
  helper. `test_history.py` shows two of them side by side.
- The **settings page** is the third target: its subtitle-language picker and release-page button
  are stubbed on `settingspage.SubtitleLanguagePicker` / `settingspage.open_path`
  (`test_subtitle_picker.py`, `test_appupdate.py`).
- `gui.messagebox` is different: it *is* the `tkinter.messagebox` module object, so
  `gui.messagebox.showinfo = …` mutates the module every importer sees and keeps working.
- The same rule hit `gui.EntryPicker`: `_open_entry_picker()` is a **panel** method, so stubbing
  `gui.EntryPicker` no longer intercepted the call, a **real modal dialog** opened, and
  `test_video_panel.py` hung instead of failing. Stub `panels.EntryPicker`.
- Modules may not import `gui` (that would be circular), which is why panel-needed helpers moved to
  `widgets.py`/`taskbar.py` instead of being fetched back from `gui.py`.

When moving code again, grep the tests for `patch.object(gui, "<name>")` and `gui.<name> =` and
check each hit against the module that now calls that name. A quick AST scan (names loaded but not
defined in a module) catches the "forgot to import it" half of this problem immediately.

## First-Run Onboarding

`settings.load()` adds an in-memory `_first_run` flag (true only when the settings file does
not exist yet). It is deliberately **not** in `DEFAULTS`, so `save()` never writes it and
faked settings in tests never carry it — that keeps the modal welcome dialog from appearing
during a test run, where `wait_window()` would hang the suite instead of failing it.
`App.needs_onboarding()` requires `_first_run` and an unset `onboarding_done`; dismissing the
dialog (either button or the window close) persists `onboarding_done` exactly once.

## Local Log

`app/applog.py` writes one line per event to `<data dir>/logs/videodownloader.log`, rotating
at 1 MB and keeping three older files. Three rules: it must **never raise** (a full disk or a
read-only directory cannot be allowed to break a download), it must **not record secrets**
(keys containing cookie/token/secret/password are redacted, and URLs keep only the path
because tokens hide in query strings), and it must stay **bounded** (rotation).

Wired at: app start/exit, window hide, unhandled Tk callback exceptions
(`report_callback_exception`), probe/download failures and completions, cookie probe results,
yt-dlp update results, and `diagnostics.record_error` (the in-memory "last error" is now
persisted too).

Runs where the main module is named `test_*.py` are redirected to `logs/test/`: without that,
one suite run wrote sixteen lines into the user's real log, including
`panel=<Mock name='mock.platform' …>` noise. The diagnostics report carries the log **path**
only, never its content.

## Updating yt-dlp

The packaged yt-dlp is frozen at build time, so a site change breaks every download until a
new app release ships. `app/ytdlp_update.py` installs the official wheel from PyPI into the
user data directory and injects it into `sys.path` at startup.

The settings page exposes it as the *yt-dlp update* card in the Advanced section: one button
that checks PyPI and then turns into "update to X", with a token guarding against a stale
result overwriting newer state.

Three constraints, all learned the hard way:

- `activate()` must run **before anything imports `yt_dlp`** (that is why `main.py` calls it
  before `from app.gui import run`) and must **not import `yt_dlp` itself**: once the module
  is in `sys.modules`, injecting `sys.path` has no effect and the user's update is silently
  ignored. `test_ytdlp_update.py` checks this in a subprocess — and the check installs a
  managed copy there too, because otherwise `activate()` returns early and the assertion
  passes vacuously (it did, until a negative test caught it).
- `bundled_dir()` must skip `managed_root()`: after injection, `find_spec("yt_dlp")` resolves
  to the managed copy, which would make "bundled version" report the *installed* version and
  put `active_source()` in contradiction with `activate()`.
- Installation is atomic (extract into a staging dir inside the same parent, verify the
  package and inner version, then `os.replace`) and zip-slip guarded; any failure leaves the
  app running on the bundled copy.
- A **broken** managed copy must not stop the app from starting. `main.py` catches an
  `ImportError` from the first import, marks that copy as `.broken-<version>` (kept aside with
  the reason, never deleted) and re-execs itself with `VD_NO_MANAGED_YTDLP=1` so the retry uses
  the bundled engine. This was not hypothetical: a managed copy whose `version.py` lacked
  `CHANNEL` made the frozen app die at startup, before any UI existed.
- The bundled version is written into the package by the build scripts
  (`build/work/yt_dlp_bundled_version.txt`, read via `bundle_root()`): in a frozen app
  `find_spec("yt_dlp")` reports paths that do not exist on disk and `loader.get_data` is a
  plain file read, so `yt_dlp/version.py` is unreadable at runtime.

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

Links from platforms the app has no panel for must be **named, not silently dropped**
(`platforms.unsupported_site()` + `gui._unsupported_message()`): copying a WeChat Channels link
used to produce no reaction at all, and the menu-bar paste path even claimed "no link in the
clipboard". Two details: the message distinguishes *the engine also cannot do it* (WeChat
Channels / WeChat articles / Kuaishou) from *yt-dlp supports it but the UI does not yet*
(Xiaohongshu / TikTok — verified against `yt-dlp --list-extractors`), and host matching is
boundary-safe so `notweixin.qq.com` is not treated as WeChat. Note that `extract_supported_url()`
uses the strict per-platform `URL_RE`, so an unsupported link returns `None` there — the clipboard
path falls back to `parse_links()` to tell "a link we cannot handle" apart from "no link at all".

## Download Queue

The queue stores **only a URL, a platform key and the kind** (`video`/`image`) — deliberately
not a snapshot of the quality/subtitle/save-location options. Executing a job therefore reuses
the panel's normal flow: switch page (and `kind_var`, so an image job lands on the image panel),
fill the URL, call `on_download()` (which probes first) and let the current settings apply. That
keeps the queue out of the core download state machine; the trade-off is stated in the page hint
so users are not surprised.

`kind` is what made image queueing (ROADMAP item 9) possible, and it has two consequences worth
remembering:

- Deduplication is keyed by `(url, kind)` (`gui.queue_key()`): the same link queued as a video and
  as an image is two legitimate jobs. Items **without** a `kind` are pre-item-9 data and must keep
  running as video (`normalize_kind()` treats anything but `image` as video).
- Both panels share one entry point, `gui.enqueue_from_entry(app, panel, kind)`, so the batch-paste
  counting/reporting behaviour cannot drift between the video and image pages.

Batch pasting is split deliberately: `gui.parse_links()` **only extracts** links (order
preserved, duplicates kept), while `App.enqueue_urls()` owns deduplication and counting. Doing
the dedup in the parser made the UI always report "skipped 0 duplicates", because the repeats
were already gone by the time the count was taken. The URL pattern accepts only URL-safe ASCII,
so a link glued to Chinese prose ("看这个https://…很好") comes out clean.

`advance_queue()` refuses to take a job while **any** panel reports `_busy`, so two downloads
never fight over one progress bar. Panels call `App.notify_download_finished()` from
`_reset_after_download()`, which schedules the next advance with `after(...)` — advancing
synchronously inside a panel's own reset would re-enter the download flow and corrupt its
state.

## Settings Import / Export

`app/settings_io.py` writes/reads a JSON file of **user preferences** (ROADMAP item 12). Two rules
carry the design:

- **Exports exclude sensitive and machine-bound keys** (`EXCLUDED`): the cookie source and
  `cookies.txt` path (they point at files on *this* machine), the proxy URL (it may embed a
  password), the download history and queue, the window geometry and the last update check. The
  same list is enforced on import, so a hand-edited file cannot smuggle them back in.
- **Import is not a wholesale copy**: a key must exist in `DEFAULTS`, match the default's **type**
  (`bool` is checked before `int`, since `bool` subclasses `int`) and pass the value range check
  (`_ENUMS`, plus `playlist_limit >= 0` and a string map for `save_dirs`). Everything else is
  counted as "ignored" and reported back to the user.

`_ENUMS` duplicates option lists that live elsewhere, so `test_settings_io.py` cross-checks every
entry against its real source (`i18n.language_options()`, `App.FONT_SCALES`, `LOGO_OPTIONS`,
`core.AUDIO_FORMATS`, `postaction.OPTIONS`) — otherwise adding an option would silently make
imports reject it.

After a successful import the page calls `App.set_theme/set_language/set_font_scale`; **the font
scale must come last**, because it destroys and rebuilds the whole layout (every page widget it
was called from is gone afterwards). Imports therefore finish touching widgets before applying.

## About Dialog

`app/about.py` is the one place to see the version, dependencies, save location and log path. It
shows **`diagnostics.build_report()` verbatim** rather than a second hand-written dependency list:
the report is the single source of truth for diagnostics, and a parallel list would drift.

Four details:

- The report probes browser cookies (seconds, maybe a keychain prompt), so it is collected in a
  background thread and filled in with `after(0, ...)`; the settings page passes its **cached**
  probe, so opening About does not trigger a second probe.
- The dialog is a **singleton per App** (`app._about_dialog`): pressing the button twice lifts the
  existing window instead of stacking copies. `_fill()` checks `winfo_exists()` first, because the
  report can arrive after the user closed the window.
- Entry points: the settings card's About button, and the **menu-bar version item** — which has two
  behaviours keyed on its own label (no update → About; update found → the releases page).
- **Sizing**: a `Toplevel` must be given a minimum width and its **position only**
  (`geometry("+x+y")`) — the first version set `geometry("660+x+y")`, which pinned the size, so long
  paths (the ffmpeg path, a cookie error) ran past the right edge and the bottom button row was cut
  off. No unit test can see that; only a screenshot did. `_fill()` calls `update_idletasks()` so the
  window grows once the report arrives, and the check is `winfo_height() >= winfo_reqheight()`.
- `App._handle_menubar_action()` was extracted out of `_poll_menubar()` so a test can drive a menu
  action directly instead of faking the poll loop; the two-behaviour version item is exactly the
  kind of logic that needs it.

## Embedding Subtitles / Cover / Metadata

Three settings (`embed_subtitles`, `embed_thumbnail`, `embed_metadata`, all off) map to
`core.embed_postprocessors()`. The trap worth remembering:

- **`addmetadata` / `embedsubtitles` / `embedthumbnail` are CLI-only option keys.** Only
  `yt_dlp/__init__.py` (the command-line layer) turns them into postprocessors; passing them
  through the Python API does *nothing* at all — the cover is still written as a loose file and
  the metadata/subtitles never enter the container. The first version of this feature did exactly
  that and looked correct in unit tests; only a real download showed the file had no embedded
  cover. `embed_postprocessors()` builds the list explicitly, in the CLI's order.
- **Order matters**: audio is extracted first, then metadata is written, then the cover is
  embedded (`FFmpegMetadata` before audio extraction would write into the intermediate container
  that then gets replaced). `FFmpegThumbnailsConvertor` runs first because a **webp** cover cannot
  be embedded into mp3 — yt-dlp fails that silently, which is also why `writethumbnail` (a key the
  Python API *does* honour) is set separately.
- Because postprocessors skip silently, the embedding path turns `no_warnings` **off** and installs
  `_PostprocessLogger`, which forwards yt-dlp warnings/errors into the app log. Without that,
  yt-dlp does not even call the logger, and "I turned embedding on and nothing happened" is
  undiagnosable.
- `mutagen` is required for mp3/m4a cover embedding and is **not** a yt-dlp dependency, so it is in
  `requirements.txt` and each build script passes `--hidden-import mutagen` (yt-dlp imports it
  lazily, so PyInstaller would otherwise drop it).

## Chapters, SponsorBlock And Rate Limit

`core.media_postprocessors()` (ROADMAP item 15) assembles the full postprocessor list for video and
playlist downloads. Same trap as embedding, same conclusion:

- **`split_chapters` and `sponsorblock_mark` / `sponsorblock_remove` are CLI-only keys.** They appear
  in `yt_dlp/options.py` and are translated in `yt_dlp/__init__.py`; the Python API ignores them. The
  CLI's own generator shows what to build instead: a `SponsorBlock` step, a `ModifyChapters` step and
  an `FFmpegSplitChapters` step. `test_chapters.py` has a guard asserting none of those keys ever
  reach the options.
- **Order is a constraint, not a preference**: `ModifyChapters` must run *before* `FFmpegMetadataPP`
  (the CLI says so in a comment) — otherwise the metadata is written into the version that still has
  the sponsor segments and the chapters no longer match the cut file. Splitting runs last.
- `ratelimit` and `concurrent_fragment_downloads` **are** real API parameters (the downloaders read
  them from `params`). Two details: `ratelimit` is in **bytes per second** (the UI stores KB/s), and
  `concurrent_fragment_downloads` only affects native HLS/DASH — 1 means "let yt-dlp decide", so 1 is
  never passed. Both are clamped (`RATELIMIT_MAX_KB`, `MAX_FRAGMENT_WORKERS`).
- **SponsorBlock categories are split into segments and POIs.** `poi_highlight` / `chapter` are points
  in time, not ranges: asking to *cut* them is meaningless, so they are only allowed in mark mode. The
  list is cross-checked against yt-dlp's `SponsorBlockPP.CATEGORIES` so a renamed category is not
  silently dropped.
- Limits are applied through `core.set_download_limits()`, the same module-level pattern as cookies
  and the proxy, so the panels do not have to thread them through every call. The **audio path takes
  no chapter settings on purpose** (`download_audio` has no `extras` parameter) — chapters belong to
  a video container, and pretending otherwise would be worse than not offering it.
- In the UI these are selectors, not text fields (`RATELIMIT_CHOICES`, `FRAGMENT_CHOICES`), so no
  invalid value can be stored; the selected value is taken **by position**, because the labels follow
  the UI language while the stored number must not.

## After-download Action

`app/postaction.py` runs one action when a download finishes: nothing, open the folder, open the
file, quit, or sleep the computer. `App._maybe_run_after_download()` is scheduled from
`notify_download_finished()` **after** the queue advance, and it only fires when `is_idle()` — no
panel busy and an empty queue. That rule is the whole point: a 25-item playlist would otherwise pop
25 Finder windows, so the action is tied to the app reaching idle, not to each finished file.

Two decisions worth keeping:

- The setting stores the **ASCII code** (`open_folder`), never the displayed label — the label
  follows the UI language, and values persisted in `settings.json` must not shift.
- **Sleep instead of shutdown**: a shutdown cannot be undone, sleep can, and sleep covers the real
  use case (queue a playlist overnight). `SLEEP_COMMANDS` holds the per-platform command; an
  unsupported platform returns `None` and the action is skipped silently.

Failures only reach the log (`after_download_failed`) — an after-download action must never break
the app, so `open_path`/sleep problems are swallowed after being recorded.

## Version And Update Check

`app/appupdate.py` holds the version shown in the UI and the new-release check against GitHub's
public Releases API. Three rules:

- `check()` **never raises**: a failed request, a 404 (no releases yet), a rate limit or an
  unparseable body all come back as `{"status": "error", ...}` so the UI stays usable. The
  request goes through the configured proxy — a restricted network is exactly where this matters.
- Comparison pads the tuple before comparing, because tag names and `__version__` do not always
  have the same number of fields (`1.11` vs `1.10.0`). `last_update_check == 0` means "never":
  formatting it directly produced 「上次检查：1970-01-01」 (a test caught it).
- Automatic checks are throttled to once per 24 h and can be switched off; `should_auto_check()`
  treats a missing key as enabled, so existing settings files still get told about new versions.

The version surfaces in three places: the settings card (版本与更新), the menu-bar item, and a
status-bar line. Two traps, both caught by tests: the menu-bar item must be refreshed right after
the menu is built (`add()` only fills in the i18n template, so it read 「版本 {version}」 until the
language was switched), and announcements must go through `App._set_status_anywhere(text)` —
that helper only takes `error=`, and a page calling `self.app._set_status(...)` raises
AttributeError, because only the panels own status bars.

## Network Proxy

`app/netproxy.py` holds the effective proxy and injects it into the three network paths, which
read it differently: `requests` honours the `HTTP(S)_PROXY` environment variables, while
**yt-dlp and gallery-dl do not** — they need it passed explicitly (`core._base_opts()` and
`gallery_dl` config respectively). Priority is: user setting → environment → direct.

Two rules:

- **A malformed value must never break networking.** `netproxy.validate()` accepts only
  `http://` / `https://` URLs with a host and a sane port; anything else is rejected, the UI
  shows why, and the effective proxy falls back to the environment variables so downloads keep
  working until the typo is fixed. SOCKS is deliberately unsupported (it needs the extra PySocks
  dependency); the hint tells users to use their proxy tool's HTTP port.
- **Never print the password.** Proxy URLs often carry `user:pass@`; `redact()` masks it and the
  diagnostics report and the log use that form (there is a test asserting the password cannot
  reach the report).

The settings card also has a *Test* button that runs the yt-dlp updater's PyPI request through
the current proxy, so users can tell "proxy wrong" from "site down".

## One Folder Per Post

`settings["group_by_post"]` (off by default) puts a post's files into
`<save dir>/<post ID>_<title summary>/`, and inside that into a **media-type folder**:
`image/`, `gif/`, `video/` (or `audio/` for audio-only video downloads). `App._post_download_dir()`
builds the post folder and takes an optional `kind=` for the type layer; the video panel passes
its own kind, while the image panel leaves it empty because **one image download can mix types** —
a Weibo GIF post yields both `.gif` and `.mp4` items in a single call, so the bucketing has to
happen per item inside `images.download_images(..., group_by_type=True)` (`images.media_bucket()`).

The bucket names are ASCII and deliberately **not** routed through i18n: a folder name that
followed the UI language would shift every recorded path (and break duplicate detection) the
moment the user switches language.

Two rules keep it predictable:

- **The post ID comes from the link, not from the probe result.** `images.post_id_from_url()` is
  the single source of truth, so the image flow and the video flow for the same pasted URL compute
  the same folder — otherwise a post with both a video and GIFs would split across two folders.
  The probe's `post_id` is only a fallback for links with no recognisable ID.
- The folder name must be **stable**: `images._primary_path()` is what detects already-downloaded
  images, so a non-deterministic name would re-download everything. That is why the title is
  truncated and sanitised but the ID is mandatory (no ID → no grouping).

Known trade-off: Weibo PC links carry the base62 `mblogid` (`Rmr4XxLMD`), which matches the image
file names, while mobile links only carry the numeric mid — the same post opened through the two
link forms lands in two different folders. The title can also be edited by the author, which
yields a new folder.

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

## Thumbnail Loading

Image posts fetch thumbnails through `ImagePanel._load_thumbnails()`, which uses a fixed number
of **daemon** workers (`THUMB_WORKERS`) pulling from a queue instead of one thread per item: a
20-image post used to open 20 simultaneous connections (the concurrency test measures exactly
20 once the cap is removed), which invites rate limiting and socket exhaustion. Daemon threads
were chosen over `ThreadPoolExecutor` because its threads are non-daemon and get joined at
interpreter exit, so one slow request could delay closing the app. Workers exit as soon as the
epoch changes, so switching posts drops the queued work, and the epoch also keeps stale results
from landing in the grid.

Tests for this must run a **real mainloop** (`test_thumbnails.py` runs it in slices until a
predicate holds): the workers hop back with `self.after(...)` from a non-main thread, and Tk
raises `RuntimeError: main thread is not in main loop` when the main thread only pumps
`update()`. The first version of those tests failed for exactly that reason.

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

A fixed install section (macOS Gatekeeper / Windows SmartScreen / Linux `chmod`) is appended
to every release body: the builds are unsigned, so the first launch is always blocked and that
guidance used to live only in the developer docs. `--install-only` prints just that section and
needs no repository or tag, which is why the workflow's fallback chain uses it — a notes bug can
no longer hide the instructions.

Two constraints keep it working:

- The `publish-release` job must check out with `fetch-depth: 0` **and** `fetch-tags: true`.
  With the default depth of 1 there is no previous tag, so the notes silently come out
  empty — verified by running the generator in a `--depth 1` clone, which found 0 items.
- Failures fall back to the install section first and a minimal body second, so a notes bug never blocks publishing assets — and never hides the first-launch instructions.

Preview the notes for any range before tagging:

~~~bash
python3 build/release_notes.py v1.8.0 --repo Monkey0803/downloadYTB
~~~

## Packaged Verification

`App.run()` honours `VD_TEST_SCRIPT`: when that environment variable points at an existing file,
the file is `exec`'d with `{"app": app}` after the window is built and before `mainloop()`. That is
how behaviour gets verified in the frozen app:

```bash
launchctl setenv VD_TEST_SCRIPT /tmp/verify.py   # macOS: `open` does not inherit the shell env
open -n dist/VideoDownloader.app
```

Three things learned by getting them wrong, all inside this repository:

- The frozen bundle has **no `unittest`**: `from unittest.mock import patch` raises
  `ModuleNotFoundError`, and inside a Tk callback that error simply disappears. Stub by hand —
  keep the original attribute, assign a recorder, restore it in `finally`.
- Write a marker line at **module level** and wrap every `after()`-scheduled function in a
  try/except that records the traceback into the output file. Twice a smoke run produced an empty
  file because the real error happened inside a callback, and Tk swallowed it.
- Use `launchctl setenv` (a plain `open` does not pass environment variables) and `open -n`, so a
  stale already-running instance without the script cannot be reactivated instead.
- Smoke scripts must neutralise `settings.save` (or restore the file) — a verification run that
  saves state will add the new default keys to the user's real `settings.json`.

## Security and Configuration

Browser-cookie selections and paths are stored locally. Treat them as sensitive: do not commit settings files, credentials, cookies, or personal media. Respect target-platform terms, copyright, privacy requirements, and rate limits.
