"""集中式主题调色板：Liquid Glass 深 / 浅两套配色 + 跟随系统检测。

所有自绘控件在绘制时从 `theme.C()`（当前调色板）取色，
切换主题后由界面层触发整棵控件树重绘（见 widgets.apply_theme_tree）。
"""

import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    BG: str              # 窗口磨砂底色
    CARD: str            # 卡片表面
    CARD_BORDER: str     # 卡片高光描边
    CARD_SHADOW: str     # 卡片柔和投影
    FIELD_BG: str        # 输入框内凹底色
    FIELD_BORDER: str
    ACCENT: str          # 主操作蓝
    ACCENT_HOVER: str
    ACCENT_DISABLED: str
    PILL_BG: str         # 次级胶囊按钮
    PILL_HOVER: str
    PILL_TEXT_DISABLED: str
    TEXT: str
    TEXT_SUB: str
    ERROR: str
    OK: str
    TRACK: str           # 进度条轨道
    SEG_ACTIVE: str      # 分段控件选中块
    SIDEBAR_BG: str      # 侧边栏底色
    SIDEBAR_ACTIVE: str  # 侧边栏选中项底色
    SIDEBAR_HOVER: str
    SIDEBAR_TEXT: str
    SIDEBAR_TEXT_ACTIVE: str
    THUMB_BG: str        # 图片缩略图占位底色
    CHECK_BORDER: str    # 复选框描边


LIGHT = Palette(
    name="light",
    BG="#F2F3F5",
    CARD="#FFFFFF",
    CARD_BORDER="#FFFFFF",
    CARD_SHADOW="#D8DCE2",
    FIELD_BG="#F7F8FA",
    FIELD_BORDER="#D9DEE5",
    ACCENT="#0A84FF",
    ACCENT_HOVER="#0066D6",
    ACCENT_DISABLED="#A8C7EE",
    PILL_BG="#EEF0F3",
    PILL_HOVER="#E4E7EC",
    PILL_TEXT_DISABLED="#B3BAC9",
    TEXT="#1C1C1E",
    TEXT_SUB="#69717C",
    ERROR="#D93025",
    OK="#1E8E3E",
    TRACK="#DDE2E8",
    SEG_ACTIVE="#FFFFFF",
    SIDEBAR_BG="#E7EBF0",
    SIDEBAR_ACTIVE="#FFFFFF",
    SIDEBAR_HOVER="#F2F3F5",
    SIDEBAR_TEXT="#56606D",
    SIDEBAR_TEXT_ACTIVE="#1C1C1E",
    THUMB_BG="#E3E7EC",
    CHECK_BORDER="#BCC4CE",
)

DARK = Palette(
    name="dark",
    BG="#15171E",
    CARD="#232733",
    CARD_BORDER="#3A4150",
    CARD_SHADOW="#0B0C11",
    FIELD_BG="#2A2F3C",
    FIELD_BORDER="#3A4150",
    ACCENT="#0A84FF",
    ACCENT_HOVER="#3D9BFF",
    ACCENT_DISABLED="#27496F",
    PILL_BG="#323848",
    PILL_HOVER="#3C4356",
    PILL_TEXT_DISABLED="#5B6273",
    TEXT="#F2F3F7",
    TEXT_SUB="#9AA3B5",
    ERROR="#FF6B5E",
    OK="#34D169",
    TRACK="#323848",
    SEG_ACTIVE="#4A5264",
    SIDEBAR_BG="#0F1117",
    SIDEBAR_ACTIVE="#2A2F3C",
    SIDEBAR_HOVER="#1C1F29",
    SIDEBAR_TEXT="#9AA3B5",
    SIDEBAR_TEXT_ACTIVE="#F2F3F7",
    THUMB_BG="#2A2F3C",
    CHECK_BORDER="#4A5264",
)

MODES = [("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")]

_mode = "system"
_current = LIGHT


def system_prefers_dark() -> bool:
    """检测操作系统是否处于深色外观。"""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=3,
            )
            return out.returncode == 0 and "dark" in out.stdout.lower()
        except Exception:
            return False
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
        except Exception:
            return False
    return False


def set_mode(mode: str) -> None:
    """设置主题模式（system / light / dark）并解析出当前调色板。"""
    global _mode, _current
    _mode = mode if mode in ("system", "light", "dark") else "system"
    if _mode == "dark" or (_mode == "system" and system_prefers_dark()):
        _current = DARK
    else:
        _current = LIGHT


def mode() -> str:
    return _mode


def C() -> Palette:
    """返回当前调色板。"""
    return _current


def is_dark() -> bool:
    return _current is DARK
