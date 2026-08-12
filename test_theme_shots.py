"""主题截图验证：先解析一条 X 图片帖（让界面有真实内容），再分别在
Light / Dark / 跟随系统 三种模式下截取 各平台页 + 设置页。
运行：.venv/bin/python test_theme_shots.py"""

import os
import subprocess

from app import gui

X_PHOTOS = "https://x.com/perrypumas/status/894001459754180609"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "screenshots")
os.makedirs(OUT, exist_ok=True)

app = gui.App()
app.attributes("-topmost", True)  # 截图期间保持窗口前置
try:
    app._last_clipboard = app.clipboard_get()
except Exception:
    pass

xpage = app.pages["x"]
xpage.show_kind("image")
panel = xpage.image_panel


def shot(name):
    app.update_idletasks()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    subprocess.run(["screencapture", "-x",
                    f"-R{x - 4},{y - 34},{w + 8},{h + 40}",
                    os.path.join(OUT, name)])
    print("shot:", name)


def start():
    app.show_page("x")
    panel.url_entry.set(X_PHOTOS)
    panel.on_probe()
    app.after(500, wait_content)


def wait_content(tries=0):
    loaded = (panel._post is not None and not panel._busy and
              all(it.get("photo") for it in panel.picker._items))
    if loaded or tries > 50:
        app.after(300, run_modes, 0)
        return
    app.after(400, wait_content, tries + 1)


MODES = ["light", "dark", "system"]
PAGES = [("youtube", "youtube"), ("x", "x_images"),
         ("instagram", "instagram"), ("settings", "settings")]


def run_modes(mi):
    if mi >= len(MODES):
        app.after(300, app.destroy)
        return
    mode = MODES[mi]
    app.set_theme(mode)
    # 同步设置页分段控件显示
    sp = app.pages["settings"]
    sp.theme_var.set(mode)
    sp.theme_seg.refresh()
    sp._update_theme_hint()
    run_pages(mi, 0)


def run_pages(mi, pi):
    if pi >= len(PAGES):
        app.after(200, run_modes, mi + 1)
        return
    key, label = PAGES[pi]
    app.show_page(key)
    app.after(450, lambda: (shot(f"theme_{MODES[mi]}_{label}.png"),
                            app.after(150, run_pages, mi, pi + 1)))


app.after(800, start)
app.mainloop()
print("THEME SHOTS DONE")
