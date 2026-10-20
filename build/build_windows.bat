@echo off
REM Windows 打包脚本：产出 dist\VideoDownloader\VideoDownloader.exe
REM 用法：在项目根目录执行  build\build_windows.bat
cd /d "%~dp0\.."

set PYTHON=.venv\Scripts\python.exe
if not exist "%PYTHON%" (
    echo 未找到虚拟环境 .venv，请先执行：
    echo   python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

REM 冻结包运行期读不到 yt_dlp 的 version.py（代码在归档里），把版本号写成文件一起打包。
REM 用 Python 自己写文件，并在写不出来/写成空文件时直接失败：旧写法是
REM   python -c ... > build\work\yt_dlp_bundled_version.txt 2>nul
REM 它把失败信息也一起丢掉，于是能静默把一个 0 字节的版本文件打进发布包
REM （真机上出现过：关于对话框、设置页与诊断报告里的版本号都是空的）。
if not exist build\work mkdir build\work
"%PYTHON%" -c "import pathlib, yt_dlp.version as v; f = pathlib.Path('build/work/yt_dlp_bundled_version.txt'); f.write_text(v.__version__ + chr(10), encoding='utf-8'); print('yt-dlp bundled version:', v.__version__)"
if errorlevel 1 (
    echo 无法读取 yt-dlp 版本，打包中止
    exit /b 1
)
for %%A in (build\work\yt_dlp_bundled_version.txt) do if %%~zA EQU 0 (
    echo yt-dlp 版本文件是空的，打包中止
    exit /b 1
)

"%PYTHON%" -m PyInstaller ^
    --noconfirm ^
    --windowed ^
    --onedir ^
    --name "VideoDownloader" ^
    --icon "%CD%\assets\app.ico" ^
    --add-data "%CD%\assets;assets" ^
    --add-data "%CD%\build\work\yt_dlp_bundled_version.txt;." ^
    --collect-all imageio_ffmpeg ^
    --collect-all gallery_dl ^
    --collect-all yt_dlp_ejs ^
    --hidden-import mutagen ^
    --workpath build\work ^
    --specpath build ^
    main.py

if errorlevel 1 (
    echo 打包失败
    exit /b 1
)

echo.
echo 打包完成：dist\VideoDownloader\VideoDownloader.exe
