"""视频下载器入口。"""

from app import ytdlp_update

# 必须早于任何 import yt_dlp 的模块：一旦 yt_dlp 被导入就进了 sys.modules，
# 再注入 sys.path 也不会生效（用户装的新版会被静默忽略）。
ytdlp_update.activate()

from app.gui import run  # noqa: E402  必须晚于 activate()

if __name__ == "__main__":
    run()
