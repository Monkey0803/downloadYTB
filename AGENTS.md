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

Run `test_i18n.py`, `test_save_dir_sync.py`, and relevant unit tests for local changes. GUI scripts such as `test_smoke_ui.py` require Tk; `test_core.py`, `test_images.py`, and several GUI flows use live services and may require valid URLs, login cookies, and network access. Use a dedicated test download directory and never include private URLs or cookies in fixtures.

## Commits and Pull Requests

Use focused imperative English commit subjects, following the existing `feat:` and `docs:` pattern, for example `fix: preserve audio save directory`. Keep one logical change per commit. Pull requests should describe the problem, implementation, verification commands, unverified platform/network cases, and include screenshots for visible UI changes. Before submitting, inspect `git diff --cached --stat` and `git diff --cached --check`.

## Security and Configuration

Browser-cookie selections and paths are stored locally. Treat them as sensitive: do not commit settings files, credentials, cookies, or personal media. Respect target-platform terms, copyright, privacy requirements, and rate limits.
