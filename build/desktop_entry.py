#!/usr/bin/env python3
"""生成 Linux 桌面启动项与安装脚本（ROADMAP 第 14 项）。

以前 Linux 用户解压后只能自己写 `.desktop`：菜单里没有图标、任务栏也认不出窗口。
这里在打包时生成两个文件放进 Linux 包：

- ``videodownloader.desktop`` —— 桌面启动项。文件名**必须**是 ``videodownloader``，
  因为 ``app/taskbar_linux.py`` 用 ``FALLBACK_DESKTOP_ID``（同名字符串）向 Unity
  的 LauncherEntry 报告进度；名字对不上，任务栏进度条就会变成一个独立图标。
- ``install-desktop.sh`` —— 把启动项装进 ``~/.local/share/applications``、把图标装进
  ``~/.local/share/icons``，并尽可能地刷新桌面数据库。它**就地**指向解压目录，
  不复制那一百多兆的程序本体；目录移动过就再跑一次。

两个函数都是纯字符串拼接，便于离线测试；``main()`` 只负责写文件。
"""

import argparse
import os
import stat
import sys

# 应用在 Linux 上的标识：与 taskbar_linux.FALLBACK_DESKTOP_ID 一致
APP_ID = "videodownloader"
APP_NAME = "VideoDownloader"
EXECUTABLE = "VideoDownloader"
ICON_SIZE = 256
ICON_FILE = f"{APP_ID}.png"

# .desktop 的 Categories 必须是已注册的主类别；AudioVideo 覆盖「能放音视频」的工具
CATEGORIES = "AudioVideo;Player;Utility;"
# 冻结后的可执行文件名即 WM_CLASS，任务栏靠它把窗口归到启动项名下
STARTUP_WM_CLASS = APP_NAME


def desktop_text(exec_path: str, icon_path: str, version: str = "") -> str:
    """返回 .desktop 内容；``exec_path`` 与 ``icon_path`` 写成绝对路径。"""
    comment = "Download video and images from supported platforms"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        f"Name[zh_CN]={APP_NAME}",
        f"Comment={comment}",
        # Exec 必须加引号：解压目录里带空格（"~/Downloads/My Apps"）时不加会被截断
        f'Exec="{exec_path}"',
        f"Icon={icon_path}",
        "Terminal=false",
        f"Categories={CATEGORIES}",
        f"StartupWMClass={STARTUP_WM_CLASS}",
        "StartupNotify=true",
    ]
    if version:
        lines.append(f"X-VideoDownloader-Version={version}")
    return "\n".join(lines) + "\n"


def install_script_text(version: str = "") -> str:
    """返回安装脚本内容（POSIX sh，可重复执行，缺工具也不失败）。"""
    banner = f"{APP_NAME} {version}".strip()
    return f"""#!/bin/sh
# {banner} 桌面启动项安装脚本
#
# 做的事：把启动项与图标装到当前用户目录，让应用出现在系统菜单里。
# 不复制程序本体——启动项直接指向本脚本所在的目录，移动过目录就再跑一次。
# 卸载：rm ~/.local/share/applications/{APP_ID}.desktop 与
#       rm ~/.local/share/icons/hicolor/{ICON_SIZE}x{ICON_SIZE}/apps/{ICON_FILE}
set -eu

here=$(cd "$(dirname "$0")" && pwd)
app_dir="$here"

if [ ! -x "$app_dir/{EXECUTABLE}" ]; then
    echo "找不到可执行文件：$app_dir/{EXECUTABLE}" >&2
    echo "请在本脚本所在的解压目录中运行（不要单独把它复制走）。" >&2
    exit 1
fi

data_home="${{XDG_DATA_HOME:-$HOME/.local/share}}"
apps_dir="$data_home/applications"
icon_dir="$data_home/icons/hicolor/{ICON_SIZE}x{ICON_SIZE}/apps"
mkdir -p "$apps_dir" "$icon_dir"

src_icon="$app_dir/{ICON_FILE}"
if [ -f "$src_icon" ]; then
    cp -f "$src_icon" "$icon_dir/{ICON_FILE}"
    icon_path="$icon_dir/{ICON_FILE}"
else
    # 图标缺失不致命：用可执行文件自身当图标路径，桌面环境会退回默认图标
    icon_path="$app_dir/{EXECUTABLE}"
fi

# Exec 用绝对路径并加引号：解压目录可能带空格
cat > "$apps_dir/{APP_ID}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Download video and images from supported platforms
Exec="$app_dir/{EXECUTABLE}"
Icon=$icon_path
Terminal=false
Categories={CATEGORIES}
StartupWMClass={STARTUP_WM_CLASS}
StartupNotify=true
EOF
chmod +x "$apps_dir/{APP_ID}.desktop"

# 刷新缓存：这两个工具在最小化安装里可能没有，缺了不算失败
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$apps_dir" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$data_home/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "已安装桌面启动项：$apps_dir/{APP_ID}.desktop"
echo "指向的程序：$app_dir/{EXECUTABLE}"
echo "如果菜单里还没出现，注销再登录一次即可。"
"""


def write_bundle_files(target_dir: str, version: str = "", icon_source: str = "",
                       executable: str = EXECUTABLE) -> dict:
    """把 .desktop、安装脚本与图标写进包目录，返回写出的路径。"""
    os.makedirs(target_dir, exist_ok=True)
    app_dir = os.path.abspath(target_dir)
    exec_path = os.path.join(app_dir, executable)
    icon_target = os.path.join(app_dir, ICON_FILE)

    if icon_source and os.path.isfile(icon_source):
        with open(icon_source, "rb") as src, open(icon_target, "wb") as dst:
            dst.write(src.read())

    icon_path = icon_target if os.path.isfile(icon_target) else exec_path
    desktop_path = os.path.join(app_dir, f"{APP_ID}.desktop")
    with open(desktop_path, "w", encoding="utf-8") as handle:
        handle.write(desktop_text(exec_path, icon_path, version))

    script_path = os.path.join(app_dir, "install-desktop.sh")
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(install_script_text(version))
    mode = os.stat(script_path).st_mode
    os.chmod(script_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return {"desktop": desktop_path, "script": script_path,
            "icon": icon_target if os.path.isfile(icon_target) else ""}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 Linux 桌面启动项与安装脚本")
    parser.add_argument("--dir", required=True, help="Linux 包目录（PyInstaller onedir 输出）")
    parser.add_argument("--version", default="", help="版本号，写进启动项备注")
    parser.add_argument("--icon", default="", help="图标来源 PNG（复制成 videodownloader.png）")
    parser.add_argument("--executable", default=EXECUTABLE, help="包内可执行文件名")
    args = parser.parse_args(argv)

    written = write_bundle_files(args.dir, args.version, args.icon, args.executable)
    for kind, path in written.items():
        if path:
            print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
