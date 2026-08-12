"""GUI 视频链路验证（新版 sidebar UI）：YouTube 页解析 → 下载中截图 →
暂停截图 → 继续 → 取消清理。运行：.venv/bin/python test_gui_shots.py"""

import glob
import os
import shutil
import subprocess

from app import gui

URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "screenshots")
DL = os.path.join(ROOT, "test_downloads", "gui_video")
shutil.rmtree(DL, ignore_errors=True)
os.makedirs(OUT, exist_ok=True)

app = gui.App()
try:
    app._last_clipboard = app.clipboard_get()
except Exception:
    pass
app.show_page("youtube")
panel = app.pages["youtube"].video_panel
panel.dir_var.set(DL)


def shot(name):
    app.update_idletasks()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    subprocess.run(["screencapture", "-x",
                    f"-R{x - 4},{y - 34},{w + 8},{h + 40}",
                    os.path.join(OUT, name)])
    print("shot:", name)


def fail(msg):
    print("FAIL:", msg)
    app.destroy()


def start():
    panel.url_entry.set(URL)
    panel.on_probe()
    app.after(500, wait_probe)


def wait_probe(tries=0):
    if panel._info and not panel._busy:
        for q in panel._info.qualities:
            if q.label == "480p":
                panel.quality_var.set(q.display)
                break
        panel.on_download()
        app.after(400, wait_downloading)
        return
    if tries > 80:
        return fail("解析超时")
    app.after(500, wait_probe, tries + 1)


def wait_downloading(tries=0):
    if panel._last_percent >= 3:
        shot("gui_downloading.png")
        panel.on_pause()
        app.after(300, wait_paused)
        return
    if tries > 200:
        return fail("下载进度超时")
    app.after(300, wait_downloading, tries + 1)


def wait_paused(tries=0):
    if panel._paused:
        shot("gui_paused.png")
        print("暂停态状态栏:", panel.status_label.cget("text"),
              "| 主按钮:", panel.download_btn._text)
        parts = glob.glob(os.path.join(DL, "*.part*"))
        print("暂停后保留 .part:", [os.path.basename(p) for p in parts] or "无")
        panel.on_resume()  # 继续下载，验证断点续传入口
        app.after(600, wait_resumed)
        return
    if tries > 60:
        return fail("暂停超时")
    app.after(300, wait_paused, tries + 1)


def wait_resumed(tries=0):
    if panel._downloading and panel._last_percent > 3:
        print(f"续传进行中，进度 {panel._last_percent:.1f}%")
        panel.on_cancel()
        app.after(500, wait_cancelled)
        return
    if tries > 90:
        return fail("续传超时")
    app.after(400, wait_resumed, tries + 1)


def wait_cancelled(tries=0):
    if not panel._downloading and not panel._paused and panel._cancel_event is None:
        parts = glob.glob(os.path.join(DL, "*.part*"))
        print("取消后状态栏:", panel.status_label.cget("text"))
        print("取消后残留 .part:", parts or "无")
        print("主按钮:", panel.download_btn._text)
        app.after(400, app.destroy)
        return
    if tries > 60:
        return fail("取消超时")
    app.after(400, wait_cancelled, tries + 1)


app.after(800, start)
app.mainloop()
print("GUI VIDEO DONE")
