# 多平台下载器 — YouTube / X / Instagram / 抖音 / 哔哩哔哩

基于 Python + yt-dlp + gallery-dl + tkinter（标准库）的跨平台（macOS / Windows）
图形界面下载器，Liquid Glass 视觉风格，sidebar 多平台布局，支持深 / 浅主题。

## 功能

- **侧边栏多平台**：左侧切换 YouTube / X (Twitter) / Instagram / 抖音 / 哔哩哔哩 / 设置
- **YouTube**：视频下载（解析后按实际可用格式列出清晰度与大小估算，
  ffmpeg 合并为单个 mp4）、仅音频 MP3（192 kbps）
- **X (Twitter)**：
  - 视频下载（可选清晰度）
  - **图片下载**：解析推文中全部图片 → 缩略图网格多选（全选 / 单选）→
    下载 `name=orig` **原图**
- **Instagram**：视频与图片的代码路径同 X；但平台已封锁匿名访问
  （见下方「Instagram 限制」）
- **抖音 (Douyin)**：视频下载（音视频同轨，单文件 mp4）、仅音频 MP3。
  支持 `www.douyin.com/video/...` 与 `v.douyin.com` 分享短链。抖音视频通常
  只有单一清晰度档位，应用会优雅处理（不报错、不显示空列表）。被风控时
  可在「设置」中选择已登录抖音的浏览器 Cookie（见下方「抖音 / B 站限制」）
- **哔哩哔哩 (Bilibili)**：视频下载（DASH 分离的视频流 + 音频流由 ffmpeg
  自动合并为单文件、音视频同轨）、仅音频 MP3。支持 `bilibili.com/video/BV...`、
  `b23.tv` 短链与 BV 号链接。1080P+/4K 等高清晰度需要登录大会员，匿名最高
  1080P；拿不到高清时给出提示而非报错（见下方「抖音 / B 站限制」）
- **断点续传**：视频下载可暂停（保留 .part）→ 继续（从断点续传）→ 取消（清理临时文件）
- **图片下载可取消**：取消时删除未完成文件，已完成的保留
- **分别记住保存位置**：视频、仅音频，以及 X / Instagram 的图片下载方式
  分别保存上次选择的文件夹，下次切换到对应方式时自动恢复；未单独设置时使用
  「设置」中的默认保存位置
- **剪贴板自动识别**：复制受支持的链接后切回窗口，自动跳转平台页并解析
- **macOS menubar 常驻图标**（pyobjc / NSStatusBar）：屏幕顶部状态栏显示
  当前所选 Logo 的彩色小图（随设置页「应用图标」切换实时变化，深浅菜单栏
  下均可见；小图缺失时回退单色模板图）；菜单含当前下载状态（只读）、
  「显示主窗口」与「退出」。关闭主窗口（红色按钮）仅隐藏窗口、应用保留在
  menubar 运行，从 menubar「退出」（或 Cmd+Q）才真正退出；有下载进行中时
  退出会先弹确认并取消下载、清理临时文件。Windows 上自动停用此功能
- **主题设置**：跟随系统 / 浅色 / 深色，整套自绘控件双配色，
  设置持久化到用户目录 JSON
- **更换 App Logo**：设置页提供 4 款应用图标（经典蓝 / 霓虹 / 日落 / 薄荷），
  点击预览缩略图即切换，窗口图标与 macOS Dock 图标立即生效并持久化；
  Finder 中 .app 的静态图标（.icns）无法运行时更换，保持默认经典蓝。
  图标素材由 `build/make_logo_variants.py` 预处理（居中裁剪、flood-fill
  去黑/白底、羽化、缩放到 1024）生成到 `assets/logos/`
- ffmpeg 由 `imageio-ffmpeg` 自带的静态二进制提供，**无需用户单独安装**

## Instagram 限制

Instagram 已封锁匿名访问：未登录状态下公开帖子也会被重定向到登录页
（gallery-dl 与 yt-dlp 均如此）。应用会给出清晰的中文提示。

可尝试在「设置 → 浏览器 Cookie」中选择已登录 Instagram 的浏览器
（Chrome / Safari / Firefox / Edge），解析与下载会携带其 Cookie。
macOS 上读取浏览器 Cookie 可能需要授予相应权限（Safari 需要完全磁盘访问权限，
Chrome 需要解锁钥匙串）。

## 抖音 / B 站限制

- **抖音**：公开视频通常匿名即可解析与下载（音视频同轨）；高频访问可能触发
  风控（返回验证 / 412 等），此时应用会给出友好中文提示，可在「设置」中
  选择已登录抖音的浏览器 Cookie 后重试。抖音多为竖屏单档位视频。
- **哔哩哔哩**：
  - 视频为 DASH 分离流，下载时由 ffmpeg 自动合并为单个 mp4（音视频同轨）。
  - **1080P+ / 4K / HDR / 杜比** 等高清晰度需要登录大会员；匿名 / 普通账号
    最高 1080P。需要登录的清晰度可在「设置」中选择已登录（大会员）的浏览器
    Cookie；拿不到高清时应用会提示而非报错。
  - B 站对**数据中心 / 海外 IP** 的播放地址接口（playurl）有较强风控，
    可能直接返回 `HTTP 412`。在大陆住宅网络或配合登录 Cookie 的环境下可正常
    下载；应用已将该错误映射为「访问过于频繁或被风控拦截…」的友好提示。

## 项目结构

```
downloadYTB/
├── main.py                  # 入口
├── app/
│   ├── core.py              # 视频引擎：yt-dlp 封装（解析/清晰度/下载/暂停续传/取消）
│   ├── images.py            # 图片引擎：gallery-dl 解析 + requests 下载（多选/取消）
│   ├── gui.py               # sidebar 主窗口 + 平台页 + 视频/图片面板 + 设置页
│   ├── menubar.py           # macOS menubar 常驻图标（pyobjc NSStatusBar，非 macOS 返回 None）
│   ├── widgets.py           # Liquid Glass 自绘控件库（主题感知）
│   ├── theme.py             # 集中式调色板：深/浅两套配色 + 跟随系统检测
│   ├── settings.py          # 设置持久化（用户目录 JSON）
│   └── ffmpeg_util.py       # 定位 imageio-ffmpeg 的 ffmpeg 二进制路径
├── assets/
│   ├── app_logo.png / logo_neon.png / logo_sunset.png / logo_mint.png  # 原始 logo
│   ├── logos/               # 处理后的 4 款图标（{key}_icon.png 1024 / {key}_512.png / {key}_menubar.png 36px）
│   ├── app.icns / app.ico / app_icon*.png / menubar_template.png
├── requirements.txt
├── build/
│   ├── make_icons.py        # 从 app_logo.png 生成 icns/ico/窗口图标/menubar 模板
│   ├── make_logo_variants.py# 生成「更换 App Logo」的 4 款备选图标（assets/logos/）
│   ├── build_macos.sh       # PyInstaller 打包脚本（macOS）
│   └── build_windows.bat    # PyInstaller 打包脚本（Windows）
└── README.md
```

设置文件位置：macOS `~/Library/Application Support/VideoDownloader/settings.json`，
Windows `%APPDATA%\VideoDownloader\settings.json`。
其中 `save_dirs` 按 `youtube_video`、`youtube_audio`、`x_image` 等下载方式保存
专属目录；旧配置中的 `save_dir` 会继续作为未单独设置方式的默认目录。

## 开发运行

> **macOS 注意**：系统自带的 `/usr/bin/python3`（Python 3.9）绑定的是 Tk 8.5，
> 在新版 macOS 上窗口内容无法正常渲染（界面空白）。请使用带新版 Tk 的 Python，
> 例如：`brew install python-tk@3.13`，然后用 `python3.13 -m venv .venv` 创建虚拟环境。

```bash
# 1. 创建虚拟环境并安装依赖（macOS 推荐 python3.13，见上方注意）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt    # Windows: .venv\Scripts\pip install -r requirements.txt

# 2. 启动 GUI
.venv/bin/python main.py                     # Windows: .venv\Scripts\python main.py
```

## 打包独立应用

> 注意：PyInstaller **不支持交叉打包**，macOS 的 `.app` 只能在 macOS 上构建，
> Windows 的 `.exe` 只能在 Windows 上构建。

### macOS

```bash
bash build/build_macos.sh
# 产物：dist/VideoDownloader.app
```

首次打开如提示「无法验证开发者」，可在 访达 中右键 → 打开，或执行：

```bash
xattr -dr com.apple.quarantine dist/VideoDownloader.app
```

### Windows

```bat
build\build_windows.bat
:: 产物：dist\VideoDownloader\VideoDownloader.exe
```

## 常见问题

- **解析失败 / 下载失败**：平台经常调整接口，优先升级引擎：
  `.venv/bin/pip install -U yt-dlp gallery-dl`，然后重新打包。
- **X 内容**：单条推文的视频与图片匿名即可下载；但**用户时间线 / 媒体墙**
  需要登录（可配合「浏览器 Cookie」设置）。受保护账号或已删除推文无法下载。
- **Instagram**：见上方「Instagram 限制」。
- **抖音 / 哔哩哔哩**：见上方「抖音 / B 站限制」。B 站 412、抖音风控多与
  网络出口 IP / 是否登录有关，可切换网络或配合「浏览器 Cookie」设置。
- **会员 / 年龄限制视频**：需要登录凭证，可尝试「浏览器 Cookie」设置。
- **主题没有跟随系统变化**：「跟随系统」在应用启动与切换设置时检测
  （macOS 通过 `defaults read -g AppleInterfaceStyle`，Windows 读取注册表），
  系统外观变化后重新打开应用或重新选择一次主题即可。
