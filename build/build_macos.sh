#!/usr/bin/env bash
# macOS 打包脚本：产出 dist/VideoDownloader.app
# 用法：在项目根目录执行  bash build/build_macos.sh
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "未找到虚拟环境 .venv，请先执行："
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

"$PYTHON" -m PyInstaller \
    --noconfirm \
    --windowed \
    --onedir \
    --name "VideoDownloader" \
    --icon "$PWD/assets/app.icns" \
    --add-data "$PWD/assets:assets" \
    --collect-all imageio_ffmpeg \
    --collect-all gallery_dl \
    --collect-all yt_dlp_ejs \
    --hidden-import AppKit \
    --workpath build/work \
    --specpath build \
    main.py

echo
echo "打包完成：dist/VideoDownloader.app"
