"""GUI 流程实测：X 图片解析→缩略图→全选下载→单选下载→取消；IG 报错 UI。
逐步截图到 screenshots/。运行：.venv/bin/python test_gui_flow.py"""

import os
import shutil
import subprocess

from app import gui

X_PHOTOS = "https://x.com/perrypumas/status/894001459754180609"
IG_POST = "https://www.instagram.com/p/BqvsDleB3lV/"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "screenshots")
DL = os.path.join(ROOT, "test_downloads", "gui_img")
shutil.rmtree(DL, ignore_errors=True)
os.makedirs(OUT, exist_ok=True)

app = gui.App()
try:
    app._last_clipboard = app.clipboard_get()
except Exception:
    pass

app.show_page("x")
xpage = app.pages["x"]
xpage.show_kind("image")
panel = xpage.image_panel
panel.dir_var.set(os.path.join(DL, "all"))


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


def step1_probe():
    panel.url_entry.set(X_PHOTOS)
    panel.on_probe()
    app.after(500, wait_probe)


def wait_probe(tries=0):
    if panel._post and not panel._busy:
        app.after(500, wait_thumbs)
        return
    if tries > 60:
        return fail("解析超时")
    app.after(500, wait_probe, tries + 1)


def wait_thumbs(tries=0):
    loaded = sum(1 for it in panel.picker._items if it.get("photo"))
    if loaded == len(panel.picker._items) or tries > 40:
        print(f"缩略图加载 {loaded}/{len(panel.picker._items)}")
        app.after(200, step2_shot_all)
        return
    app.after(500, wait_thumbs, tries + 1)


def step2_shot_all():
    shot("gui_x_images_parsed.png")
    n = len(panel.picker.selected_indices())
    print(f"已选 {n} 张 / 共 {len(panel._post.items)} 张")
    panel.on_download()
    app.after(300, wait_download_all)


def wait_download_all(tries=0):
    if not panel._downloading and panel._last_paths:
        print("全选下载完成:", [os.path.basename(p) for p in panel._last_paths])
        shot("gui_x_images_done.png")
        app.after(300, step3_single)
        return
    if tries > 120:
        return fail("全选下载超时")
    app.after(400, wait_download_all, tries + 1)


def step3_single():
    # 只选第 2 张
    panel.picker.set_all(False)
    panel.picker._items[1]["selected"] = True
    panel.picker._draw()
    panel._on_selection_change()
    panel.dir_var.set(os.path.join(DL, "single"))
    shot("gui_x_images_single_select.png")
    panel.on_download()
    app.after(300, wait_single)


def wait_single(tries=0, retried=[0]):
    if not panel._downloading:
        if panel._last_paths:
            files = os.listdir(os.path.join(DL, "single"))
            print("单选下载完成:", files)
            assert len(files) == 1, "应只下载 1 张"
            app.after(300, step4_cancel)
            return
        if not panel._busy and retried[0] < 3:
            # 网络抖动失败则重试
            retried[0] += 1
            print(f"单选下载失败（{panel.status_label.cget('text')}），第 {retried[0]} 次重试")
            panel.on_download()
    if tries > 150:
        return fail("单选下载超时")
    app.after(400, wait_single, tries + 1)


def step4_cancel():
    panel.picker.set_all(True)
    panel._on_selection_change()
    panel.dir_var.set(os.path.join(DL, "cancel"))
    panel.on_download()
    app.after(350, lambda: panel.on_cancel())
    app.after(700, wait_cancel)


def wait_cancel(tries=0):
    if not panel._downloading and panel._cancel_event is None:
        print("取消后状态栏:", panel.status_label.cget("text"))
        shot("gui_x_images_cancelled.png")
        parts = [f for f in os.listdir(os.path.join(DL, "cancel"))
                 if f.endswith(".part")] if os.path.isdir(os.path.join(DL, "cancel")) else []
        print("取消后 .part 残留:", parts or "无")
        app.after(300, step5_instagram)
        return
    if tries > 80:
        return fail("取消超时")
    app.after(300, wait_cancel, tries + 1)


def step5_instagram():
    app.show_page("instagram")
    igpage = app.pages["instagram"]
    igpage.show_kind("image")
    igpanel = igpage.image_panel
    igpanel.url_entry.set(IG_POST)
    igpanel.on_probe()
    app.after(500, wait_ig, igpanel)


def wait_ig(igpanel, tries=0):
    if not igpanel._busy and "Instagram" in igpanel.status_label.cget("text"):
        print("IG 报错文案:", igpanel._status_full)
        shot("gui_instagram_error.png")
        app.after(400, app.destroy)
        return
    if tries > 60:
        return fail("IG 报错超时")
    app.after(500, wait_ig, igpanel, tries + 1)


app.after(800, step1_probe)
app.mainloop()
print("GUI FLOW DONE")
