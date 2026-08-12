"""应用设置持久化：JSON 存储在用户目录，跨平台。

macOS:   ~/Library/Application Support/VideoDownloader/settings.json
Windows: %APPDATA%/VideoDownloader/settings.json
Linux:   ~/.config/videodownloader/settings.json
"""

import json
import os
import sys

DEFAULTS = {
    "theme": "system",        # system / light / dark
    "save_dir": "",           # 空表示使用 ~/Downloads
    "save_dirs": {},           # 按平台 + 下载类型记录的保存目录
    "browser_cookies": "",    # 空表示不读取浏览器 Cookie；可选 chrome/safari/firefox/edge
    "app_logo": "classic",    # 应用图标：classic / neon / sunset / mint
}


def _settings_dir() -> str:
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/VideoDownloader")
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "VideoDownloader")
    return os.path.expanduser("~/.config/videodownloader")


SETTINGS_PATH = os.path.join(_settings_dir(), "settings.json")


def load() -> dict:
    data = dict(DEFAULTS)
    data["save_dirs"] = dict(DEFAULTS["save_dirs"])
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            for k in DEFAULTS:
                if k in stored:
                    data[k] = stored[k]
            # 配置文件可能被手动编辑，异常类型不能影响应用启动。
            if not isinstance(data.get("save_dirs"), dict):
                data["save_dirs"] = {}
    except (OSError, ValueError):
        pass
    return data


def save(data: dict) -> None:
    try:
        os.makedirs(_settings_dir(), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({k: data.get(k, v) for k, v in DEFAULTS.items()},
                      f, ensure_ascii=False, indent=2)
    except OSError:
        pass
