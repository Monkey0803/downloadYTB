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
    "language": "zh_CN",      # zh_CN / zh_TW / en / ko / ja
    "save_dir": "",           # 空表示使用 ~/Downloads
    "save_dirs": {},           # 按平台 + 下载类型记录的保存目录
    "browser_cookies": "",    # 空表示不读取浏览器 Cookie；可选 chrome/safari/firefox/edge
    "cookie_file": "",        # 手动导出的 cookies.txt 路径；优先于浏览器 Cookie
    "platform_cookies": {},    # 按平台覆盖 Cookie 来源，如 {"instagram": "chrome"}
    "dock_status": True,      # 是否在 macOS Dock 角标显示下载进度/完成状态
    "skip_existing_images": True,  # 图片已存在时跳过而不是另存副本
    "audio_format": "mp3",    # 仅音频目标格式：mp3 / m4a / opus / flac
    "subtitles_enabled": False,   # 视频下载时是否同时下载字幕
    "playlist_limit": 1,      # 播放列表下载范围：1=仅当前视频，0=全部，其余=前 N 个
    # 剪贴板链接处理方式：suggest=先提示（默认）/ auto=自动填入 / off=不处理
    "clipboard_action": "suggest",
    "subtitle_langs": "",     # 逗号分隔的字幕语言；空表示用代码内默认值
    "window_geometry": "",
    # 下载历史：最新的在前，最多 App.HISTORY_LIMIT 条（只保存路径等本地信息）
    "download_history": [],    # 上次的窗口尺寸/位置，如 "1020x745+120+80"
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
