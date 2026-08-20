"""冒烟测试：启动新版 sidebar UI，逐页截图后退出。"""

import os
import subprocess
import sys

from app import gui

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "screenshots")
os.makedirs(OUT, exist_ok=True)

# 清空剪贴板影响：smoke 阶段不希望自动解析
app = gui.App()
app._last_clipboard = None
try:
    app._last_clipboard = app.clipboard_get()
except Exception:
    pass


def shot(name):
    app.update_idletasks()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    path = os.path.join(OUT, name)
    subprocess.run(["screencapture", "-x", f"-R{x - 4},{y - 34},{w + 8},{h + 40}", path])
    print("shot:", path)


STEPS = [
    ("youtube", "smoke_youtube.png", None),
    ("x", "smoke_x_video.png", None),
    ("x", "smoke_x_image.png", lambda: app.pages["x"].show_kind("image")),
    ("instagram", "smoke_instagram.png", None),
    ("weibo", "smoke_weibo_video.png", None),
    ("weibo", "smoke_weibo_image.png", lambda: app.pages["weibo"].show_kind("image")),
    ("settings", "smoke_settings.png", None),
]


def run_step(i):
    if i >= len(STEPS):
        app.after(200, app.destroy)
        return
    key, name, pre = STEPS[i]
    app.show_page(key)
    if pre:
        pre()
    app.after(500, lambda: (shot(name), app.after(200, run_step, i + 1)))


app.after(900, run_step, 0)
app.mainloop()
print("SMOKE DONE")
