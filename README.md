# 多平台下载器

一个基于 Python、Tkinter、yt-dlp 和 gallery-dl 的桌面下载器，支持 YouTube、X、Instagram、抖音和哔哩哔哩的视频或图片下载。

[English README](README.en.md) · [GitHub Repository](https://github.com/Monkey0803/downloadYTB) · [MIT License](LICENSE)

> 本项目只提供下载工具。请只下载你有权保存的内容，并遵守目标平台的服务条款、版权法和隐私规定。

## 功能概览

- 在统一的桌面界面中解析并下载多个平台的内容。
- 视频下载时选择可用清晰度；支持仅音频 MP3。
- X 和 Instagram 图片帖子解析后显示缩略图，支持全选或单选原图下载。
- 视频下载支持暂停、断点续传和取消；图片下载支持取消，已完成文件会保留。
- 每一种下载方式分别记住保存目录。视频、仅音频、图片不会互相覆盖目录设置。
- 复制受支持的链接后切回应用，自动识别链接、切换平台并开始解析。
- 支持跟随系统、浅色和深色主题。
- macOS 支持 menubar 常驻图标、Dock/窗口图标切换；Windows 和 Linux 会跳过 macOS 专属功能。
- ffmpeg 由 imageio-ffmpeg 提供，正常安装依赖后不需要单独安装 ffmpeg。

## 支持的平台

| 平台 | 视频 | 仅音频 | 图片 | 说明 |
| --- | --- | --- | --- | --- |
| YouTube | 支持 | 支持 | - | 按解析结果显示清晰度和大小估算 |
| X / Twitter | 支持 | 支持 | 支持 | 图片下载使用原图链接 |
| Instagram | 支持 | 支持 | 支持 | 通常需要浏览器 Cookie |
| 抖音 | 支持 | 支持 | - | 通常是单档位音视频流 |
| 哔哩哔哩 | 支持 | 支持 | - | DASH 视频和音频由 ffmpeg 合并 |

平台接口会变化，因此“支持”表示代码提供对应的解析和下载路径，不代表任何链接、地区、账号状态或网络环境下都一定可用。

## 项目结构

~~~text
downloadYTB/
├── main.py                    # 程序入口
├── app/
│   ├── gui.py                 # 主窗口、侧边栏、平台页和下载面板
│   ├── core.py                # yt-dlp 视频解析、格式选择和下载控制
│   ├── images.py              # gallery-dl 图片解析和 requests 下载
│   ├── widgets.py             # Liquid Glass 风格 Tkinter 控件
│   ├── theme.py               # 浅色 / 深色 / 跟随系统主题
│   ├── settings.py            # JSON 设置持久化
│   ├── menubar.py             # macOS NSStatusBar 菜单栏实现
│   └── ffmpeg_util.py         # 定位 imageio-ffmpeg 的 ffmpeg
├── assets/                    # 应用图标和 Logo 资源
├── build/
│   ├── build_macos.sh         # 构建 macOS .app
│   ├── build_windows.bat      # 构建 Windows .exe
│   ├── make_icons.py          # 生成基础图标
│   └── make_logo_variants.py  # 生成可切换 Logo 变体
├── test_*.py                  # 单元、GUI、打包和联网验证脚本
├── requirements.txt           # Python 依赖
└── LICENSE
~~~

### 一次下载的调用链

~~~text
Tkinter 事件
    ├── VideoPanel / ImagePanel
    ├── 后台线程执行解析或下载
    ├── core.py / images.py 调用第三方下载引擎
    └── after(...) 回到 Tk 主线程更新进度、状态和按钮
~~~

耗时网络操作不能直接在 Tk 主线程执行。修改下载流程时，保持“后台线程执行工作、after 回主线程更新 UI”的约束。

## 开发环境

### macOS

项目使用 Tkinter。新版 macOS 上，系统自带 Python 的 Tk 版本可能导致窗口空白，建议使用带新版 Tk 的 Python 3.13，例如 Homebrew 的 Python Tk：

~~~bash
brew install python@3.13 python-tk@3.13
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
~~~

如果你已经有可用的 Python 3.13，也可以直接替换上面的解释器路径。

### Windows

~~~powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\pip.exe install -r requirements.txt
~~~

### Linux

Linux 可以尝试使用系统 Tk 开发包和 Python 虚拟环境：

~~~bash
sudo apt install python3-tk
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
~~~

macOS menubar 和 Cocoa 相关功能只在 macOS 上启用；Windows、Linux 运行时会跳过这些平台专属代码。

## 运行项目

安装依赖后，在项目根目录启动：

~~~bash
# macOS / Linux
.venv/bin/python main.py

# Windows
.venv\Scripts\python.exe main.py
~~~

首次启动后，设置文件会写入用户目录：

- macOS：<code>~/Library/Application Support/VideoDownloader/settings.json</code>
- Windows：<code>%APPDATA%\VideoDownloader\settings.json</code>
- Linux：<code>~/.config/videodownloader/settings.json</code>

其中 save_dir 是全局默认目录，save_dirs 是按下载方式保存的专属目录。例如：

~~~json
{
  "save_dir": "/Users/example/Downloads",
  "save_dirs": {
    "youtube_video": "/Users/example/Movies",
    "youtube_audio": "/Users/example/Music",
    "x_image": "/Users/example/Pictures/X"
  }
}
~~~

未设置专属目录时会回退到全局默认目录，再未设置时使用 ~/Downloads。不要把包含浏览器 Cookie 选择或个人路径的设置文件提交到 GitHub。

## 测试与验证

项目中的脚本按验证目标分开。不是所有脚本都适合在 CI 中无头运行，其中一些需要 Tk 窗口、macOS 截图、真实网络或真实平台内容。

### 快速静态检查

~~~bash
PYTHONPYCACHEPREFIX=/tmp/downloadytb-pycache \
  .venv/bin/python -m py_compile app/*.py main.py

git diff --check
~~~

### GUI 和功能验证

~~~bash
# 基础窗口和平台页面
.venv/bin/python test_smoke_ui.py

# 保存目录同步、专属目录和重启持久化
.venv/bin/python test_save_dir_sync.py

# 视频自动解析、选档、取消、暂停/继续
.venv/bin/python test_fix_flow.py
.venv/bin/python test_pause.py

# X / Instagram 图片解析、缩略图、多选和取消
.venv/bin/python test_gui_flow.py
.venv/bin/python test_images.py
~~~

联网测试会访问真实平台，可能受到链接失效、登录状态、Cookie 权限、地区、网络出口和平台限流影响。测试输出和截图只用于本地验证，不应提交到仓库。

### 运行测试前的建议

1. 使用测试专用下载目录，避免覆盖个人文件。
2. 不要把真实账号 Cookie、私有链接或个人媒体文件写入测试脚本。
3. 先运行不联网的语法检查，再运行联网和 GUI 测试。
4. 报告问题时记录操作步骤、平台、链接类型、是否登录、系统版本和完整错误信息；不要上传 Cookie 或私人内容。

## 构建独立应用

PyInstaller 不支持交叉打包：macOS .app 应在 macOS 上构建，Windows .exe 应在 Windows 上构建。

### macOS

~~~bash
bash build/build_macos.sh
~~~

产物：<code>dist/VideoDownloader.app</code>。构建脚本会使用 <code>.venv/bin/python</code>，收集 Tkinter、gallery-dl、yt-dlp-ejs 和 imageio-ffmpeg 所需资源。

本地检查产物：

~~~bash
file dist/VideoDownloader.app/Contents/MacOS/VideoDownloader
codesign --verify --deep --strict --verbose=2 dist/VideoDownloader.app
~~~

项目默认生成 ad-hoc 签名，不等于经过 Apple notarization 的正式发布包。正式分发前还需要配置 Developer ID、签名、notarization、stapling 和 Gatekeeper 验证。

### Windows

~~~bat
build\build_windows.bat
~~~

产物：<code>dist\VideoDownloader\VideoDownloader.exe</code>。

## 开发方法

### 修改下载引擎

- 视频解析和下载放在 app/core.py。
- 图片解析和下载放在 app/images.py。
- GUI 不应直接拼接第三方引擎的复杂逻辑；面板只负责输入、状态、线程和回调。
- 新增错误时优先在引擎层转换成用户可理解的 DownloadError 或 ImageError 文案。
- 保持取消、暂停和继续的事件语义。视频下载开始前要快照 save_dir，下载过程中修改设置不应改变当前任务目录。

### 修改界面

- 通用控件优先放在 app/widgets.py，颜色和主题值放在 app/theme.py。
- 不要在后台线程直接调用 Tk 控件。
- 修改平台页面后，至少验证页面切换、链接粘贴、解析失败和下载状态恢复。
- 修改保存目录逻辑时，同时验证：视频/音频隔离、X/Instagram 图片隔离、全局默认回退、重启后恢复。

### 修改设置格式

- 在 app/settings.py 的 DEFAULTS 中声明新字段。
- load() 必须对旧配置和错误类型容错。
- save() 不应写入未声明的临时字段。
- 对隐私敏感字段保持本地存储，不要上传测试配置。

### 修改 Logo 或打包资源

1. 修改原始资源或生成脚本。
2. 重新生成 assets/ 下的资源。
3. 在对应平台重新执行 PyInstaller 构建。
4. 验证窗口图标、macOS Dock 图标、menubar 图标和打包后资源路径。

## 贡献流程

1. Fork 仓库或从 main 创建功能分支。
2. 一个提交只解决一个主题，提交信息使用清晰的英文动词，例如 Fix image download cancellation。
3. 先运行静态检查，再运行与改动相关的 GUI/联网测试。
4. Pull Request 中说明：问题、实现方案、验证命令、未验证项和平台差异。
5. 不要提交 .venv、dist、build/work、screenshots、test_downloads、Cookie、账号信息或本地设置。

推荐的本地流程：

~~~bash
git switch -c fix/short-description
git diff --check
PYTHONPYCACHEPREFIX=/tmp/downloadytb-pycache .venv/bin/python -m py_compile app/*.py main.py
git status --short
git commit -m "Fix short description"
~~~

提交前确认没有把生成文件或个人信息加入暂存区：

~~~bash
git diff --cached --stat
git diff --cached --check
git diff --cached --name-only
~~~

## 平台限制

### Instagram

Instagram 常会拒绝匿名访问，公开帖子也可能被重定向到登录页。可以在设置中选择已登录的 Chrome、Safari、Firefox 或 Edge 浏览器 Cookie。macOS 读取浏览器 Cookie 可能需要钥匙串或完全磁盘访问权限。

### 抖音和哔哩哔哩

- 抖音可能因访问频率、IP 或登录状态触发验证和 412 风控。
- 哔哩哔哩的 1080P+、4K、HDR、杜比等清晰度通常需要对应账号权限。
- 哔哩哔哩对数据中心或海外 IP 的播放地址接口可能返回 412。
- 这些限制来自平台和网络环境，应用只能提供清晰的错误提示，不能保证绕过平台限制。

## 隐私与安全

- 应用设置只保存在本机用户目录。
- 选择浏览器 Cookie 后，第三方下载引擎可能读取对应浏览器的登录凭据；只在可信本机上启用，并理解相关安全风险。
- 项目不会把 Cookie、下载链接或下载内容主动上传到本项目维护者的服务器。
- 使用第三方平台和第三方库时，仍应阅读其隐私政策、服务条款和许可证。

## 许可证

本项目使用 [MIT License](LICENSE)。第三方库和平台服务各自遵循其许可证及服务条款。
