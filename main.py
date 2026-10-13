"""视频下载器入口。"""

import os
import sys

from app import ytdlp_update

# 必须早于任何 import yt_dlp 的模块：一旦 yt_dlp 被导入就进了 sys.modules，
# 再注入 sys.path 也不会生效（用户装的新版会被静默忽略）。
ytdlp_update.activate()

try:
    from app.gui import run  # noqa: E402  必须晚于 activate()
except ImportError as exc:  # pragma: no cover - 需要坏的本机副本才能触发
    # 本机副本坏了（例如新版本要求更高的 Python）不能让 App 起不来：
    # 标记这份为坏，设置环境变量后重启一次，这次会自动改用内置副本。
    if ytdlp_update.disable_current(str(exc)):
        os.environ[ytdlp_update.DISABLE_ENV] = "1"
        os.execv(sys.executable, [sys.executable] + sys.argv)
    raise

if __name__ == "__main__":
    run()
