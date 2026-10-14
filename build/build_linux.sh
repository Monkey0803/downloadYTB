#!/usr/bin/env bash
# Linux 打包脚本：产出 dist/VideoDownloader/VideoDownloader
# 用法：在项目根目录执行 bash build/build_linux.sh
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "未找到虚拟环境 .venv，请先执行："
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

APP_VERSION="$($PYTHON -c 'from app import __version__; print(__version__)')"

# 冻结包运行期读不到 yt_dlp 的 version.py（代码在归档里，路径是假的），
# 所以把内置版本号写成一个文件一起打进去，界面与诊断才能显示真实版本。
mkdir -p build/work
"$PYTHON" -c 'import yt_dlp.version; print(yt_dlp.version.__version__)' \
    > build/work/yt_dlp_bundled_version.txt \
    || printf '' > build/work/yt_dlp_bundled_version.txt

"$PYTHON" -m PyInstaller \
    --noconfirm \
    --windowed \
    --onedir \
    --name "VideoDownloader" \
    --add-data "$PWD/assets:assets" \
    --add-data "$PWD/build/work/yt_dlp_bundled_version.txt:." \
    --collect-all imageio_ffmpeg \
    --collect-all gallery_dl \
    --collect-all yt_dlp_ejs \
    --hidden-import mutagen \
    --workpath build/work \
    --specpath build \
    main.py

echo
echo "打包完成：dist/VideoDownloader/VideoDownloader（版本 ${APP_VERSION}）"
