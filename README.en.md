# Multi-Platform Downloader

A desktop downloader built with Python, Tkinter, yt-dlp, and gallery-dl. It supports video or image downloads from YouTube, X, Instagram, Douyin, and Bilibili.

[中文 README](README.md) · [GitHub Repository](https://github.com/Monkey0803/downloadYTB) · [MIT License](LICENSE)

> This project only provides a download tool. Download content only when you have the right to do so, and follow the target platform's terms of service, copyright law, and privacy requirements.

## Features

- One desktop interface for parsing and downloading content from multiple platforms.
- Select an available video quality after probing a URL; audio-only MP3 downloads are supported.
- Parse X and Instagram image posts into a thumbnail grid, then download all images or selected originals.
- Pause, resume from partial files, or cancel video downloads. Image downloads can also be cancelled; completed files are kept.
- Remember the last directory separately for each download mode. Video, audio-only, and image downloads do not overwrite one another's directory settings.
- Detect supported links from the clipboard, switch to the matching platform page, and start probing when the app regains focus.
- System, light, and dark themes.
- A macOS status-bar item plus runtime Dock/window logo switching. macOS-only menu-bar code is disabled on Windows and Linux.
- ffmpeg is supplied through imageio-ffmpeg; a separate ffmpeg installation is normally unnecessary.

## Supported platforms

| Platform | Video | Audio only | Images | Notes |
| --- | --- | --- | --- | --- |
| YouTube | Yes | Yes | - | Quality and estimated size come from probing |
| X / Twitter | Yes | Yes | Yes | Image downloads use original-image URLs |
| Instagram | Yes | Yes | Yes | Browser cookies are commonly required |
| Douyin | Yes | Yes | - | Usually exposes a single audio-video stream |
| Bilibili | Yes | Yes | - | DASH video/audio streams are merged with ffmpeg |

“Supported” means that the project contains a parser and download path for the platform. It does not guarantee that every URL works under every account, region, network, or rate-limit condition.

## Repository layout

~~~text
downloadYTB/
├── main.py                    # Application entry point
├── app/
│   ├── gui.py                 # Window, sidebar, platform pages, download panels
│   ├── core.py                # yt-dlp probing, format selection, and video control
│   ├── images.py              # gallery-dl probing and requests-based image downloads
│   ├── widgets.py             # Liquid Glass-style Tkinter widgets
│   ├── theme.py               # Light / dark / system theme handling
│   ├── settings.py            # JSON settings persistence
│   ├── menubar.py             # macOS NSStatusBar integration
│   └── ffmpeg_util.py         # Locates the imageio-ffmpeg binary
├── assets/                    # Application icons and logo assets
├── build/
│   ├── build_macos.sh         # Builds the macOS .app
│   ├── build_windows.bat      # Builds the Windows .exe
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

# Directory synchronization, per-mode directories, and restart persistence
.venv/bin/python test_save_dir_sync.py

# Automatic probing, quality selection, cancellation, pause/resume
.venv/bin/python test_fix_flow.py
.venv/bin/python test_pause.py

# X / Instagram image probing, thumbnails, selection, and cancellation
.venv/bin/python test_gui_flow.py
.venv/bin/python test_images.py
~~~

Live tests access real platforms. Failures may be caused by expired URLs, login state, browser-cookie permissions, regional restrictions, network routing, or platform rate limits. Generated screenshots and test downloads are local evidence and should not be committed.

### Before running a test

1. Use a dedicated test output directory so personal files are not overwritten.
2. Never put real browser cookies, private URLs, or personal media in test fixtures.
3. Run syntax checks before live-network or GUI checks.
4. When reporting a bug, include reproduction steps, platform, URL type, login state, OS version, and the complete error text. Do not upload cookies or private content.

## Build distributable applications

PyInstaller is not a cross-compiler: build the macOS .app on macOS and the Windows .exe on Windows.

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
2. Keep one commit focused on one topic. Use clear imperative English commit messages such as Fix image download cancellation.
3. Run static checks and the feature-specific GUI/live tests.
4. In a pull request, describe the problem, implementation, verification commands, unverified items, and platform differences.
5. Do not commit .venv, dist, build/work, screenshots, test_downloads, cookies, account information, or local settings.

Suggested local workflow:

~~~bash
git switch -c fix/short-description
git diff --check
PYTHONPYCACHEPREFIX=/tmp/downloadytb-pycache .venv/bin/python -m py_compile app/*.py main.py
git status --short
git commit -m "Fix short description"
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

## Privacy and security

- Application settings are stored locally in the user's profile.
- When browser cookies are enabled, the third-party download engines may read login credentials from the selected browser. Enable this only on a trusted machine and understand the security implications.
- This project does not intentionally upload cookies, download URLs, or downloaded content to a server maintained by the project owner.
- Third-party libraries and platforms have their own privacy policies, terms of service, and licenses.

## License

This project is released under the [MIT License](LICENSE). Third-party libraries and platform services remain subject to their own licenses and terms.
