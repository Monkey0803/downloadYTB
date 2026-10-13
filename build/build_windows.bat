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

REM 冻结包运行期读不到 yt_dlp 的 version.py（代码在归档里），把版本号写成文件一起打包
if not exist build\work mkdir build\work
"%PYTHON%" -c "import yt_dlp.version; print(yt_dlp.version.__version__)" > build\work\yt_dlp_bundled_version.txt 2>nul

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
    --workpath build\work ^
    --specpath build ^
    main.py

if errorlevel 1 (
    echo 打包失败
    exit /b 1
)

echo.
echo 打包完成：dist\VideoDownloader\VideoDownloader.exe
