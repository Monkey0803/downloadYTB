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

"%PYTHON%" -m PyInstaller ^
    --noconfirm ^
    --windowed ^
    --onedir ^
    --name "VideoDownloader" ^
    --icon "%CD%\assets\app.ico" ^
    --add-data "%CD%\assets;assets" ^
    --collect-all imageio_ffmpeg ^
    --collect-all gallery_dl ^
    --workpath build\work ^
    --specpath build ^
    main.py

if errorlevel 1 (
    echo 打包失败
    exit /b 1
)

echo.
echo 打包完成：dist\VideoDownloader\VideoDownloader.exe
