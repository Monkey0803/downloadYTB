# 多平台下载器

一个基于 Python、Tkinter、yt-dlp 和 gallery-dl 的桌面下载器，支持 YouTube、X、Instagram、抖音、哔哩哔哩和微博的视频或图片下载。

[English README](README.en.md) · [GitHub Repository](https://github.com/Monkey0803/downloadYTB) · [MIT License](LICENSE)

> 本项目只提供下载工具。请只下载你有权保存的内容，并遵守目标平台的服务条款、版权法和隐私规定。

## 功能概览

- 在统一的桌面界面中解析并下载多个平台的内容。
- 视频下载时选择可用清晰度；支持仅音频 MP3。
- X、Instagram 和微博图片帖子解析后显示缩略图，支持全选或单选原图下载；缩略图网格可用键盘操作（Tab 进入，方向键移动光标，空格勾选）；缩略图限量并发拉取（大图集不会一次开几十个连接）；微博 GIF 会按 GIF 保存。
- 微博支持视频、图片/GIF 以及多图批量下载。
- 视频下载支持暂停、断点续传和取消；图片下载支持取消，已完成文件会保留。
- 每一种下载方式分别记住保存目录。视频、仅音频、图片不会互相覆盖目录设置。
- 复制受支持的链接后切回应用，自动识别链接、切换平台并开始解析。
- 支持跟随系统、浅色和深色主题。
- 支持简体中文、繁体中文、英语、韩语和日语，语言可在设置页切换并持久化。
- macOS 支持 menubar 常驻图标、Dock/窗口图标切换；Windows 和 Linux 会跳过 macOS 专属功能。
- 记住窗口尺寸与位置；拔掉外接显示器导致位置失效时会自动退回可见区域。
- 快捷键：⌘↩ 解析、⌘D 开始下载、⌘P 暂停/继续、⌘L 定位链接输入框、⌘, 打开设置、Esc 取消进行中的任务。
- 键盘可用：Tab 可以在按钮、复选框、下拉、侧边栏之间移动，回车/空格激活；分段控件用方向键切换。
- 关键事件写入本机日志（设置「高级 → 诊断与反馈 → 打开日志」可直接定位文件），出错后重启仍能取证；日志超过 1 MB 自动轮转，最多保留 3 份。
- 设置「高级 → 网络代理」可填 `http://127.0.0.1:7890` 这类代理地址，yt-dlp / gallery-dl / 图片下载三条路径都会走它；留空即直连（系统 `HTTP(S)_PROXY` 环境变量会自动采用）。
- 设置「下载 → 完成后动作」可在**全部下载完成**后自动打开所在文件夹 / 打开文件 / 退出应用 / 系统睡眠（默认不处理；挂机下载选睡眠比关机稳妥，关机不可逆）。
- 设置「高级 → 版本与更新」显示当前版本，可手动「检查更新」（也可每天自动查一次，可关闭）：发现新版本会在菜单栏出现「发现新版本 x.y.z」，点它打开发布页。
- 设置「高级 → yt-dlp 更新」可把最新版下载引擎装到本机（App 内置副本不变，重启后优先使用）：站点改版导致下载失败时，不必等新版本 App。
- 设置「下载行为」可选**每个帖子单独建子文件夹**（默认关闭）：开启后同一帖子的图片/动图/视频放进以帖子 ID 命名的子文件夹（如 `Rmr4XxLMD_一组动图…`），并按类型再分 `image` / `gif` / `video` 子目录；单条视频与播放列表不受影响。
- 设置里可选界面字号（标准/大/特大），下载进行中不会立即生效；主题配色已按 WCAG 对比度审计修正。
- 首次安装会弹一次说明（三步流程 + Cookie 提示），看过即不再出现；设置里改回 `onboarding_done` 可再看。
- 设置页会实时体检 Cookie 是否真的可读；读不到时直接给出「打开系统设置」入口或改用 cookies.txt 的建议。
- 一键复制诊断信息（版本、系统、Cookie 状态、最近一次错误），便于反馈问题时粘贴到 issue，且不含个人路径与 Cookie 内容。
- Cookie 可按平台分别配置（例如只让 Instagram 带 Cookie），其余平台保持匿名访问。
- macOS 上把下载进度写进 Dock 角标，完成后显示 ✓ 并在应用不在前台时弹跳一次图标（可在设置中关闭）。
- 仅音频可选目标格式：MP3 / M4A / Opus / FLAC（无损格式不显示比特率）。
- YouTube 视频可同时下载字幕（人工与自动字幕都会尝试，保存为 .srt）。
- 解析视频后显示封面预览，下载前就能确认是不是想要的那条。
- 重复下载同一图片帖子时跳过已存在的文件，不再产生「(1)」副本（可在设置中关闭）。
- 支持播放列表 / 频道链接：解析后明确告知「这是播放列表、共多少条」，可只下当前视频、前 10/25/50 个或全部（默认只下当前视频，不会误下一整个频道）。
- 播放列表可勾选任意条目批量下载，列表不够用时可点「加载更多」逐次扩大（每次多取 100 条），文件按「序号 - 标题」命名便于排序。
- 剪贴板链接默认只「先提示」：复制链接后切回应用只在顶部出现一条提示，不会覆盖你正在输入的内容（可在设置里改回自动填入或关闭）。
- 状态栏被截断的长错误可以点开看完整内容并一键复制。
- 字幕语言可直接勾选常用语言，不必手写 yt-dlp 的语言代码。
- macOS 菜单栏常驻图标除「显示主窗口/退出」外，还有「粘贴剪贴板链接」「打开上次下载文件夹」「下载历史」「设置」四个快捷入口。
- 侧边栏「下载队列」可排队批量下载：在视频页或图片页填好链接点「加入队列」（图片任务会记成图片并在执行时自动切到图片面板），完成后自动接着下一个（也可手动「开始下一个」）；队列页的「批量粘贴」能从剪贴板一次排入多个链接（自动忽略说明文字、中文标点与重复链接）。队列只记 URL 与平台，执行时使用当时的清晰度/字幕/保存位置设置。
- 侧边栏「下载历史」保留最近 50 条完成记录，每条都能「打开文件」或定位到所在文件夹（状态栏也有「打开文件」按钮）。
- 任务栏进度：macOS 用 Dock 角标，Windows 用任务栏进度条并在完成时闪烁窗口；KDE 走 JobView，Unity / Ubuntu Dock 走 LauncherEntry（原生 GNOME 不显示）。
- ffmpeg 由 imageio-ffmpeg 提供，正常安装依赖后不需要单独安装 ffmpeg。

## 支持的平台

| 平台 | 视频 | 仅音频 | 图片 | 说明 |
| --- | --- | --- | --- | --- |
| YouTube | 支持 | 支持 | - | 按解析结果显示清晰度和大小估算；可同时下载字幕；支持播放列表 / 频道 |
| X / Twitter | 支持 | 支持 | 支持 | 图片下载使用原图链接 |
| Instagram | 支持 | 支持 | 支持 | 通常需要浏览器 Cookie |
| 抖音 | 支持 | 支持 | - | 通常是单档位音视频流 |
| 哔哩哔哩 | 支持 | 支持 | - | DASH 视频和音频由 ffmpeg 合并 |
| 微博 | 支持 | 支持 | 支持 | 图片/GIF 支持多选批量下载；可能需要浏览器 Cookie |

平台接口会变化，因此“支持”表示代码提供对应的解析和下载路径，不代表任何链接、地区、账号状态或网络环境下都一定可用。

## 下载与首次打开

发布包在 [Releases](https://github.com/Monkey0803/downloadYTB/releases) 页面，按平台选：
`macos-arm64`（Apple 芯片）、`windows-x64`、`linux-x64`。

解压后的**第一次打开会被系统拦一下**——这是免签名应用的标准提示，**不是文件损坏**：

| 平台 | 首次打开被拦住时怎么做 |
| --- | --- |
| macOS | 在「应用程序」里**右键点图标 → 打开**，再点一次「打开」；或执行 `xattr -dr com.apple.quarantine /Applications/VideoDownloader.app` |
| Windows | SmartScreen 提示「Windows 已保护你的电脑」→「更多信息」→「仍要运行」 |
| Linux | `chmod +x VideoDownloader` 后运行 |

macOS 产物目前是 ad-hoc 签名（未做 Apple 公证），Windows 产物未做代码签名，因此会出现上述
提示；两者都不影响功能。每份 Release 说明末尾也会附上这段指引。

## 项目结构

~~~text
downloadYTB/
├── main.py                    # 程序入口
├── app/
│   ├── gui.py                 # 主窗口、侧边栏、平台页和下载面板
│   ├── core.py                # yt-dlp 视频解析、格式选择和下载控制
│   ├── images.py              # gallery-dl / 微博图片解析和 requests 下载
│   ├── widgets.py             # Liquid Glass 风格 Tkinter 控件
│   ├── theme.py               # 浅色 / 深色 / 跟随系统主题
│   ├── settings.py            # JSON 设置持久化
│   ├── menubar.py             # macOS NSStatusBar 菜单栏实现
│   └── ffmpeg_util.py         # 定位 imageio-ffmpeg 的 ffmpeg
├── assets/                    # 应用图标和 Logo 资源
├── build/
│   ├── build_macos.sh         # 构建 macOS .app
│   ├── build_windows.bat      # 构建 Windows .exe
│   ├── build_linux.sh         # 构建 Linux 可执行文件
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

# 本地化资源完整性（五种语言）
.venv/bin/python test_i18n.py

# 保存目录同步、专属目录和重启持久化
.venv/bin/python test_save_dir_sync.py

# 视频自动解析、选档、取消、暂停/继续
.venv/bin/python test_fix_flow.py
.venv/bin/python test_pause.py

# X / Instagram 图片解析、缩略图、多选和取消
.venv/bin/python test_gui_flow.py
.venv/bin/python test_images.py

# 微博 URL、图片/GIF 批量解析回归
.venv/bin/python test_weibo.py
~~~

联网测试会访问真实平台，可能受到链接失效、登录状态、Cookie 权限、地区、网络出口和平台限流影响。测试输出和截图只用于本地验证，不应提交到仓库。

### 运行测试前的建议

1. 使用测试专用下载目录，避免覆盖个人文件。
2. 不要把真实账号 Cookie、私有链接或个人媒体文件写入测试脚本。
3. 先运行不联网的语法检查，再运行联网和 GUI 测试。
4. 报告问题时记录操作步骤、平台、链接类型、是否登录、系统版本和完整错误信息；不要上传 Cookie 或私人内容。

## 构建独立应用

PyInstaller 不支持交叉打包：macOS .app 应在 macOS 上构建，Windows .exe 应在 Windows 上构建，Linux 可执行文件应在 Linux 上构建。

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

### Linux

~~~bash
bash build/build_linux.sh
~~~

产物：<code>dist/VideoDownloader/VideoDownloader</code>。目标系统需要具备兼容的 glibc 和图形桌面环境；GitHub Actions 使用 Ubuntu 24.04 构建 x64 包。

### GitHub Release 自动构建

推送形如 <code>v1.3.0</code> 的版本 Tag 后，<code>.github/workflows/release.yml</code> 会在 macOS arm64、Windows x64 和 Linux x64 Runner 上分别构建 ZIP 包，并自动创建或更新同名 GitHub Release。工作流会校验 Tag 与 <code>app/__init__.py</code> 中的版本一致，避免上传错版资产。

Release 说明由 <code>build/release_notes.py</code> 读提交信息生成（按 feat/fix/docs 等分组，并把每条提交正文里的要点展开成子条目），因此**提交时写清楚的正文就是发布说明的内容**。如果某次改动改变了用户可见的默认行为，在提交正文里写上「行为变更」，它会出现在说明顶部的独立提示区。

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
2. 一个提交只解决一个主题。提交信息会被全局 commit-msg 钩子校验：标题必须写成 `<type>(<scope>): <描述>`（type 取 feat / fix / refactor / chore / docs / test / perf / build / ci / revert，本仓库统一使用 scope `repo`），标题和正文都要含中文，标题后空一行，正文每条以 `- ` 开头且至少两条。
3. 先运行静态检查，再运行与改动相关的 GUI/联网测试。
4. Pull Request 中说明：问题、实现方案、验证命令、未验证项和平台差异。
5. 不要提交 .venv、dist、build/work、screenshots、test_downloads、Cookie、账号信息或本地设置。

推荐的本地流程：

~~~bash
git switch -c fix/short-description
git diff --check
PYTHONPYCACHEPREFIX=/tmp/downloadytb-pycache .venv/bin/python -m py_compile app/*.py main.py
git status --short
git commit -F - <<'MSG'
fix(repo): 取消图片下载时清理残留的临时文件

- 取消后删除 .part 等临时文件并保留已完成的文件
- 补充取消流程的回归测试
MSG
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

## 后续计划

下一步的功能与优化项见 [ROADMAP.md](ROADMAP.md)（带 ICE 评分与代码证据）；
已完成的用户体验审计见 [UX-AUDIT.md](UX-AUDIT.md)。

## 隐私与安全

- 应用设置只保存在本机用户目录。
- 选择浏览器 Cookie 后，第三方下载引擎可能读取对应浏览器的登录凭据；只在可信本机上启用，并理解相关安全风险。
- 项目不会把 Cookie、下载链接或下载内容主动上传到本项目维护者的服务器。
- 使用第三方平台和第三方库时，仍应阅读其隐私政策、服务条款和许可证。

## 许可证

本项目使用 [MIT License](LICENSE)。第三方库和平台服务各自遵循其许可证及服务条款。
