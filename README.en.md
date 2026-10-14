# Multi-Platform Downloader

A desktop downloader built with Python, Tkinter, yt-dlp, and gallery-dl. It supports video or image downloads from YouTube, X, Instagram, Douyin, Bilibili, and Weibo.

[中文 README](README.md) · [GitHub Repository](https://github.com/Monkey0803/downloadYTB) · [MIT License](LICENSE)

> This project only provides a download tool. Download content only when you have the right to do so, and follow the target platform's terms of service, copyright law, and privacy requirements.

## Features

- One desktop interface for parsing and downloading content from multiple platforms.
- Select an available video quality after probing a URL; audio-only MP3 downloads are supported.
- Parse X, Instagram, and Weibo image posts into a thumbnail grid, then download all images or selected originals; the grid is also keyboard-operable (Tab in, arrow keys to move the cursor, Space to toggle). Weibo GIFs retain their GIF format.
- Weibo supports videos, images/GIFs, and batch image downloads.
- Pause, resume from partial files, or cancel video downloads. Image downloads can also be cancelled; completed files are kept.
- Remember the last directory separately for each download mode. Video, audio-only, and image downloads do not overwrite one another's directory settings.
- Detect supported links from the clipboard, switch to the matching platform page, and start probing when the app regains focus.
- System, light, and dark themes.
- Simplified Chinese, Traditional Chinese, English, Korean, and Japanese UI languages; the selection is persisted in Settings.
- A macOS status-bar item plus runtime Dock/window logo switching. macOS-only menu-bar code is disabled on Windows and Linux.
- Remembers the window size and position, and falls back to a visible area when a saved position becomes unreachable (for example after unplugging an external display).
- Keyboard shortcuts: ⌘↩ probe, ⌘D start download, ⌘P pause/resume, ⌘L focus the URL field, ⌘, open Settings, Esc cancel an in-progress task.
- Keyboard operable: Tab moves between buttons, checkboxes, dropdowns and the sidebar; Return/Space activates, arrow keys switch segmented controls.
- Key events are written to a local log (Settings → Advanced → Diagnostics → Open log), so a failure survives a restart; the log rotates at 1 MB and keeps three files.
- Settings → Advanced → **Network proxy** takes an address such as `http://127.0.0.1:7890` and applies it to yt-dlp, gallery-dl and image downloads; leave it empty to connect directly (the `HTTP(S)_PROXY` environment variables are picked up automatically).
- Settings → Download → **Chapters, sponsor segments and rate limit** splits a video into one file per chapter, marks or cuts SponsorBlock segments (queried on every download), and caps the download speed or raises the fragment thread count (yt-dlp downloads only; images use a separate path).
- Settings → Advanced → **Import / export settings** writes your appearance, save-location and download/embedding preferences to a JSON file you can import on another machine (cookie paths, the proxy URL, history and queue are excluded for safety).
- Settings → Advanced → **Diagnostics → About** shows the version, dependencies, save location and log path in one place, with one-click copy of the diagnostics report; the menu bar version item opens it too.
- Settings → Download → **Embed in the file** can put subtitles into the video, save and embed the cover, and write metadata such as the title (all off by default; turning them on changes the produced files).
- Settings → Download → **After download** can open the folder, open the file, quit the app or sleep the computer once **everything has finished** (off by default; sleeping is safer than shutting down, which cannot be undone).
- Settings → Advanced → **Version and updates** shows the current version and can check for a new release on demand (or once a day automatically, switchable); when one exists the menu bar shows "New version x.y.z" and clicking it opens the releases page.
- Settings → Advanced → **yt-dlp update** installs the latest download engine locally (the bundled copy is untouched and the local one wins after a restart), so a site change no longer means waiting for a new app release.
- Settings → Download behaviour offers **one folder per post** (off by default): files from a single post go into a subfolder named after the post ID (e.g. `Rmr4XxLMD_…`), split further into `image` / `gif` / `video` subfolders; single videos and playlists are unaffected.
- Settings offers three interface text sizes (standard/large/extra large); theme colours are audited against WCAG contrast.
- A one-time welcome dialog explains the flow and cookie pitfalls on first install; it never returns once dismissed.
- Settings checks whether cookies can actually be read and offers an "Open System Settings" shortcut or a cookies.txt alternative when they cannot.
- One-click diagnostic copy (versions, OS, cookie status, last error) for pasting into an issue; it contains no personal paths or cookie contents.
- Cookies can be configured per platform (for example only Instagram), keeping the other platforms anonymous.
- On macOS the Dock badge shows download progress, turns into ✓ on completion, and the icon bounces once when the app is in the background (can be turned off in Settings).
- Audio-only downloads can target MP3, M4A, Opus, or FLAC (lossless formats hide the bitrate picker).
- YouTube downloads can include subtitles (manual and automatic are both attempted, saved as .srt).
- A video thumbnail preview appears after probing, so you can confirm the video before downloading.
- Re-downloading the same image post skips existing files instead of creating numbered copies (can be turned off in Settings).
- Playlist and channel links are supported: after probing you are told "this is a playlist with N items" and can pick the current video only, the first 10/25/50, or all of them (the default is the current video only, so a whole channel is never downloaded by accident).
- Any items in a probed playlist can be ticked for batch download; use "Load more" to extend the list 100 entries at a time, and files are named "index - title" so they sort correctly.
- Clipboard links only "ask first" by default: after copying a link, switching back to the app just shows a bar at the top instead of replacing what you were typing (configurable in Settings).
- Truncated status-line errors can be clicked to read in full and copy in one click.
- Subtitle languages can be ticked from a list instead of typing yt-dlp language codes.
- The sidebar's "Download queue" lines up several jobs from **video or image pages**: press Add to queue and it continues automatically after each one (or press Start next). Image jobs are remembered as images and switch to the image panel when they run. Paste several reads multiple links from the clipboard at once, ignoring surrounding prose, CJK punctuation and duplicates. The queue stores only the URL, platform and kind, so each job uses the quality/subtitle/save-location settings in effect when it runs.
- The sidebar's "Download history" keeps the last 50 finished files, each with Open file / Reveal in folder (the status line also has an Open file button).
- Taskbar progress: a Dock badge on macOS, a real taskbar progress bar on Windows that flashes the window on completion; KDE uses JobView and Unity / Ubuntu Dock use LauncherEntry (native GNOME shows nothing).
- ffmpeg is supplied through imageio-ffmpeg; a separate ffmpeg installation is normally unnecessary.

## Supported platforms

| Platform | Video | Audio only | Images | Notes |
| --- | --- | --- | --- | --- |
| YouTube | Yes | Yes | - | Quality and estimated size come from probing; subtitles optional; playlists and channels supported |
| X / Twitter | Yes | Yes | Yes | Image downloads use original-image URLs |
| Instagram | Yes | Yes | Yes | Browser cookies are commonly required |
| Douyin | Yes | Yes | - | Usually exposes a single audio-video stream |
| Bilibili | Yes | Yes | - | DASH video/audio streams are merged with ffmpeg |
| Weibo | Yes | Yes | Yes | Images/GIFs support multi-select batch downloads; browser cookies may be required |

“Supported” means that the project contains a parser and download path for the platform. It does not guarantee that every URL works under every account, region, network, or rate-limit condition.

## Download and first launch

Release archives live on the [Releases](https://github.com/Monkey0803/downloadYTB/releases)
page — pick `macos-arm64` (Apple silicon), `windows-x64` or `linux-x64`.

The **first launch is blocked by the OS** because the builds are unsigned; that is expected and
**not** a corrupted download:

| Platform | If the first launch is blocked |
| --- | --- |
| macOS | Right-click the app in Applications → **Open**, then Open again; or run `xattr -dr com.apple.quarantine /Applications/VideoDownloader.app` |
| Windows | On the SmartScreen prompt choose **More info** → **Run anyway** |
| Linux | `chmod +x VideoDownloader`, then run it |

macOS builds are ad-hoc signed (no Apple notarisation) and Windows builds are unsigned, which is
why the warnings appear; functionality is unaffected. The same instructions are appended to every
Release's notes.

## Repository layout

~~~text
downloadYTB/
├── main.py                    # Application entry point
├── app/
│   ├── gui.py                 # Window, sidebar, platform pages, download panels
│   ├── core.py                # yt-dlp probing, format selection, and video control
│   ├── images.py              # gallery-dl / Weibo probing and requests-based image downloads
│   ├── widgets.py             # Liquid Glass-style Tkinter widgets
│   ├── theme.py               # Light / dark / system theme handling
│   ├── settings.py            # JSON settings persistence
│   ├── menubar.py             # macOS NSStatusBar integration
│   └── ffmpeg_util.py         # Locates the imageio-ffmpeg binary
├── assets/                    # Application icons and logo assets
├── build/
│   ├── build_macos.sh         # Builds the macOS .app
│   ├── build_windows.bat      # Builds the Windows .exe
│   ├── build_linux.sh         # Builds the Linux executable
│   ├── make_icons.py          # Generates base icons
│   └── make_logo_variants.py  # Generates selectable logo variants
├── test_*.py                  # Unit, GUI, packaging, and live-network checks
├── requirements.txt           # Python dependencies
└── LICENSE
~~~

### Download request flow

~~~text
Tkinter event
    ├── VideoPanel / ImagePanel
    ├── background thread performs probing or downloading
    ├── core.py / images.py call third-party download engines
    └── after(...) returns to the Tk main thread for UI updates
~~~

Network work must not run on the Tk main thread. When changing the download flow, preserve the rule: do work in a background thread and use after to update Tk widgets on the main thread.

## Development setup

### macOS

This application uses Tkinter. On recent macOS versions, the system Python may use an old Tk build and render a blank window. Use a Python distribution with a current Tk, such as Homebrew Python 3.13 with python-tk@3.13:

~~~bash
brew install python@3.13 python-tk@3.13
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
~~~

If you already have a working Python 3.13 with Tk support, use that interpreter instead.

### Windows

~~~powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\pip.exe install -r requirements.txt
~~~

### Linux

On Linux, install the Tk development package and create a virtual environment:

~~~bash
sudo apt install python3-tk
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
~~~

The Cocoa and status-bar integrations are enabled only on macOS. Windows and Linux skip those platform-specific paths.

## Run the application

From the repository root after installing dependencies:

~~~bash
# macOS / Linux
.venv/bin/python main.py

# Windows
.venv\Scripts\python.exe main.py
~~~

The settings file is stored at:

- macOS: <code>~/Library/Application Support/VideoDownloader/settings.json</code>
- Windows: <code>%APPDATA%\VideoDownloader\settings.json</code>
- Linux: <code>~/.config/videodownloader/settings.json</code>

save_dir is the global default directory. save_dirs stores directories per download mode:

~~~json
{
  "save_dir": "/Users/example/Downloads",
  "save_dirs": {
    "youtube_video": "/Users/example/Movies",
    "youtube_audio": "/Users/example/Music",
    "x_image": "/Users/example/Pictures/X"
  }
}
~~~

When a mode has no dedicated directory, it falls back to save_dir, then to ~/Downloads. Never commit a settings file containing browser-cookie choices or personal paths.

## Tests and verification

The scripts are split by verification target. Some require a Tk window, macOS screenshots, live network access, or real platform content and are not suitable as headless CI tests without additional setup.

### Fast static checks

~~~bash
PYTHONPYCACHEPREFIX=/tmp/downloadytb-pycache \
  .venv/bin/python -m py_compile app/*.py main.py

git diff --check
~~~

### GUI and feature checks

~~~bash
# Basic window and platform pages
.venv/bin/python test_smoke_ui.py

# Translation resource completeness (five languages)
.venv/bin/python test_i18n.py

# Directory synchronization, per-mode directories, and restart persistence
.venv/bin/python test_save_dir_sync.py

# Automatic probing, quality selection, cancellation, pause/resume
.venv/bin/python test_fix_flow.py
.venv/bin/python test_pause.py

# X / Instagram image probing, thumbnails, selection, and cancellation
.venv/bin/python test_gui_flow.py
.venv/bin/python test_images.py

# Weibo URL and image/GIF batch parsing regression
.venv/bin/python test_weibo.py
~~~

Live tests access real platforms. Failures may be caused by expired URLs, login state, browser-cookie permissions, regional restrictions, network routing, or platform rate limits. Generated screenshots and test downloads are local evidence and should not be committed.

### Before running a test

1. Use a dedicated test output directory so personal files are not overwritten.
2. Never put real browser cookies, private URLs, or personal media in test fixtures.
3. Run syntax checks before live-network or GUI checks.
4. When reporting a bug, include reproduction steps, platform, URL type, login state, OS version, and the complete error text. Do not upload cookies or private content.

## Build distributable applications

PyInstaller is not a cross-compiler: build the macOS .app on macOS, the Windows .exe on Windows, and the Linux executable on Linux.

### macOS

~~~bash
bash build/build_macos.sh
~~~

Output: <code>dist/VideoDownloader.app</code>. The script uses <code>.venv/bin/python</code> and collects the Tkinter, gallery-dl, yt-dlp-ejs, and imageio-ffmpeg resources required by the app.

Local artifact checks:

~~~bash
file dist/VideoDownloader.app/Contents/MacOS/VideoDownloader
codesign --verify --deep --strict --verbose=2 dist/VideoDownloader.app
~~~

The default build uses an ad-hoc signature. That is not the same as a production Developer ID and notarized release. A formal macOS release also needs signing identity configuration, notarization, stapling, and Gatekeeper verification.

### Windows

~~~bat
build\build_windows.bat
~~~

Output: <code>dist\VideoDownloader\VideoDownloader.exe</code>.

### Linux

~~~bash
bash build/build_linux.sh
~~~

Output: <code>dist/VideoDownloader/VideoDownloader</code>. The target system needs a compatible glibc and graphical desktop environment; GitHub Actions builds the x64 package on Ubuntu 24.04.

### Automatic GitHub Release builds

Pushing a version tag such as <code>v1.3.0</code> starts <code>.github/workflows/release.yml</code>. It builds ZIP assets on macOS arm64, Windows x64, and Linux x64 runners, then creates or updates the matching GitHub Release. The workflow verifies that the tag matches the version in <code>app/__init__.py</code> before uploading assets.

## Development practices

### Changing the download engine

- Keep video probing and downloading in app/core.py.
- Keep image probing and downloading in app/images.py.
- Keep third-party engine details out of the GUI where possible; panels should coordinate input, state, threads, and callbacks.
- Convert engine failures into user-readable DownloadError or ImageError messages at the engine boundary.
- Preserve cancellation, pause, and resume semantics. Video downloads snapshot save_dir before starting; changing settings during a download must not move the current task.

### Changing the UI

- Put reusable controls in app/widgets.py; keep colors and theme values in app/theme.py.
- Never update Tk widgets directly from a worker thread.
- After changing a platform page, verify page switching, paste handling, probe failures, and download-state recovery.
- After changing directory persistence, verify video/audio isolation, X/Instagram image isolation, global fallback, and restart recovery.

### Changing the settings schema

- Declare new fields in DEFAULTS in app/settings.py.
- Make load() tolerant of old files and invalid field types.
- Keep save() limited to declared settings fields.
- Keep privacy-sensitive values local and out of test fixtures.

### Changing logos or packaging resources

1. Change the source assets or generator scripts.
2. Regenerate the resources under assets/.
3. Run the PyInstaller build on the target platform.
4. Verify the window icon, macOS Dock icon, status-bar icon, and packaged resource paths.

## Contribution workflow

1. Fork the repository or create a feature branch from main.
2. Keep one commit focused on one topic. A global commit-msg hook validates every commit message: the subject must be `<type>(<scope>): <description>` (type is feat / fix / refactor / chore / docs / test / perf / build / ci / revert, and this repository uses the scope `repo`), both the subject and the body must contain Chinese, line 2 must be empty, and the body needs at least two lines that each start with `- `.
3. Run static checks and the feature-specific GUI/live tests.
4. In a pull request, describe the problem, implementation, verification commands, unverified items, and platform differences.
5. Do not commit .venv, dist, build/work, screenshots, test_downloads, cookies, account information, or local settings.

Suggested local workflow:

~~~bash
git switch -c fix/short-description
git diff --check
PYTHONPYCACHEPREFIX=/tmp/downloadytb-pycache .venv/bin/python -m py_compile app/*.py main.py
git status --short
git commit -F - <<'MSG'
fix(repo): 取消图片下载时清理残留的临时文件

- 取消后删除 .part 等临时文件并保留已完成的文件
- 补充取消流程的回归测试
MSG
~~~

Before committing, confirm that generated files and personal information are not staged:

~~~bash
git diff --cached --stat
git diff --cached --check
git diff --cached --name-only
~~~

## Platform limitations

### Instagram

Instagram often rejects anonymous access, including for public posts. Select a browser with an active Instagram session in Settings: Chrome, Safari, Firefox, or Edge. On macOS, reading browser cookies may require Keychain approval or Full Disk Access.

### Douyin and Bilibili

- Douyin may trigger verification or HTTP 412 rate limiting depending on request frequency, IP address, or login state.
- Bilibili 1080P+, 4K, HDR, Dolby, and similar formats normally require the corresponding account entitlement.
- Bilibili playback URL APIs may return HTTP 412 for data-center or overseas IP addresses.
- These restrictions come from the platforms and the network environment. The app can provide clearer errors, but it cannot guarantee bypassing platform restrictions.

## What's next

Planned features and improvements live in [ROADMAP.md](ROADMAP.md) (with ICE scores
and code evidence); the completed UX audit is in [UX-AUDIT.md](UX-AUDIT.md).

## Privacy and security

- Application settings are stored locally in the user's profile.
- When browser cookies are enabled, the third-party download engines may read login credentials from the selected browser. Enable this only on a trusted machine and understand the security implications.
- This project does not intentionally upload cookies, download URLs, or downloaded content to a server maintained by the project owner.
- Third-party libraries and platforms have their own privacy policies, terms of service, and licenses.

## License

This project is released under the [MIT License](LICENSE). Third-party libraries and platform services remain subject to their own licenses and terms.
