"""文件与路径工具：资源定位与「用系统默认程序打开」。

从 gui.py 拆出来（ROADMAP 第 13 项）。这里只依赖 os / sys / subprocess，不碰 Tk，
因此可以单独测试，也不会让界面模块继续膨胀。

``asset_path`` 依赖 ``__file__`` 定位 assets/，模块与 gui.py 同在 app/ 下，
所以打包（sys._MEIPASS）与源码运行两种情况的行为都保持不变。
"""

import os
import subprocess
import sys


def asset_path(name: str):
    """返回 assets 资源的绝对路径；兼容 PyInstaller 打包（sys._MEIPASS）。"""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "assets", name)
    return path if os.path.isfile(path) else None


# 「更换 App Logo」候选：key 对应 assets/logos/{key}_icon.png（1024，Dock 用）、
# assets/logos/{key}_512.png（512，窗口图标 + 设置页预览用）
# 与 assets/logos/{key}_menubar.png（36px@2x，macOS menubar 状态栏图标用）
LOGO_OPTIONS = [("classic", "classic_blue"), ("neon", "neon"),
                ("sunset", "sunset"), ("mint", "mint")]
def open_in_file_manager(path):
    if not path:
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def open_path(path):
    """用系统默认程序打开文件本身（而不是在文件管理器里定位它）。"""
    if not path:
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - 只打开用户自己刚下载的文件
    else:
        subprocess.Popen(["xdg-open", path])


def logo_icon_path(key: str, size: int = 1024):
    """返回某款 logo 处理后图标的绝对路径；缺失时返回 None。"""
    name = f"{key}_icon.png" if size >= 1024 else f"{key}_512.png"
    return asset_path(os.path.join("logos", name))


def logo_menubar_path(key: str):
    """返回某款 logo 的 menubar 彩色小图路径；缺失时返回 None。"""
    return asset_path(os.path.join("logos", f"{key}_menubar.png"))
