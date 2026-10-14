"""设置导入导出（ROADMAP 第 12 项）。

换机器或重装后最烦的是把十几项设置手抄一遍。这里把「用户偏好」导出成一个 JSON 文件，
以后可以从文件导回来。

两条安全规则：

1. **只认识原本就存在的键**，而且类型必须与默认值一致、取值必须在允许范围内。未知键
   一律忽略——导入不是「照单全收」，一个手改坏的文件不该把界面弄成奇怪状态。
2. **不导出敏感或机器相关的东西**：Cookie 来源与 cookies.txt 路径（指向本机文件）、
   代理地址（可能带用户名密码）、下载历史与队列（本机绝对路径与临时任务）、窗口几何、
   上次更新检查时间。导入时这些键即使出现在文件里也会被忽略。
"""

import json
import os

from . import core, i18n, postaction, settings

# 导出文件的标记，便于识别「这是不是本应用的设置文件」
FORMAT_TAG = "videodownloader-settings"
FORMAT_VERSION = 1

# 不导出、也不导入的键（敏感或与本机绑定，换台机器就没有意义甚至有害）
EXCLUDED = frozenset({
    "browser_cookies",   # 指向本机浏览器
    "cookie_file",       # 指向本机 cookies.txt
    "platform_cookies",  # 同上，按平台覆盖
    "proxy",             # URL 里可能带用户名密码
    "download_history",  # 本机绝对路径
    "download_queue",    # 临时任务
    "window_geometry",   # 与本机屏幕绑定
    "last_update_check",  # 上次检查时间，纯本机状态
    "update_available",   # 同上
})

# 取值有明确范围的键：非法值直接忽略，免得界面进入奇怪状态
_ENUMS = {
    "theme": ("system", "light", "dark"),
    "font_scale": ("standard", "large", "xlarge"),
    "clipboard_action": ("suggest", "auto", "off"),
    "app_logo": ("classic", "neon", "sunset", "mint"),
    "audio_format": tuple(core.AUDIO_FORMATS),
    "after_download": tuple(postaction.OPTIONS),
    "language": tuple(code for _label, code in i18n.language_options()),
}


class SettingsImportError(Exception):
    """导入的文件不是可用的设置文件。"""


def _type_ok(value, default) -> bool:
    """类型必须与默认值一致；bool 不是 int（它是 int 的子类，会悄悄放行）。"""
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, dict):
        return isinstance(value, dict)
    if isinstance(default, list):
        return isinstance(value, list)
    return False


def _value_ok(key, value) -> bool:
    allowed = _ENUMS.get(key)
    if allowed is not None and value not in allowed:
        return False
    if key == "playlist_limit" and value < 0:
        return False
    if key in ("save_dir", "subtitle_langs") and len(value) > 4096:
        return False
    if key == "save_dirs":
        # 路径表：键值都得是字符串
        return all(isinstance(k, str) and isinstance(v, str)
                   for k, v in value.items())
    return True


def exportable(current) -> dict:
    """挑出可以导出的键（按键名排序，方便人看和 diff）。"""
    current = current or {}
    data = {"_format": FORMAT_TAG, "_format_version": FORMAT_VERSION}
    try:
        from . import __version__

        data["_app_version"] = __version__
    except Exception:
        pass
    for key in sorted(settings.DEFAULTS):
        if key in EXCLUDED:
            continue
        if key not in current:
            continue
        value = current[key]
        if _type_ok(value, settings.DEFAULTS[key]) and _value_ok(key, value):
            data[key] = value
    return data


def import_settings(data, current=None):
    """合并导入的数据，返回 ``(要写入的键值, 摘要)``。

    摘要里 ``ignored`` 统计被忽略的项（未知键、被排除的键、类型或取值不合法），
    ``rejected`` 只列出「本来认识但值不合法」的键名，方便如实告诉用户。
    """
    if not isinstance(data, dict):
        raise SettingsImportError("not-a-dict")
    merged, rejected = {}, []
    ignored = 0
    for key, value in (data or {}).items():
        if str(key).startswith("_"):
            continue                     # 导出文件的元信息，不算「忽略」
        if key in EXCLUDED or key not in settings.DEFAULTS:
            ignored += 1
            continue
        default = settings.DEFAULTS[key]
        if not _type_ok(value, default) or not _value_ok(key, value):
            ignored += 1
            rejected.append(key)
            continue
        merged[key] = value
    summary = {
        "applied": len(merged),
        "ignored": ignored,
        "rejected": sorted(rejected),
        "keys": sorted(merged),
    }
    return merged, summary


def read_file(path) -> dict:
    """读取设置文件；格式不对时抛 ``SettingsImportError``。"""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SettingsImportError(str(exc)) from exc
    if not isinstance(data, dict):
        raise SettingsImportError("not-a-dict")
    return data


def write_file(path, data) -> None:
    """写出设置文件（原子替换，避免中途失败留下半个文件）。"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    staging = f"{path}.tmp"
    with open(staging, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(staging, path)
