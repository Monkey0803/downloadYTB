"""修复验证：未解析直接「开始下载」→ 自动解析并下载；换 URL 后旧档位失效。
逐步截图到 screenshots/。运行：.venv/bin/python test_fix_flow.py"""

import os
import shutil
import subprocess
import tkinter as tk

from app import gui

URL_A = "https://www.youtube.com/watch?v=ZOkFN69TFh8"   # 用户报错的视频
URL_B = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"   # Big Buck Bunny（换 URL 场景）
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "screenshots")
DL = os.path.join(ROOT, "test_downloads", "fix_gui")
shutil.rmtree(DL, ignore_errors=True)
os.makedirs(OUT, exist_ok=True)

app = gui.App()
try:
    app._last_clipboard = app.clipboard_get()  # 抑制剪贴板自动解析干扰
except Exception:
    pass

panel = app.pages["youtube"].video_panel
panel.dir_var.set(DL)
results = []


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


# —— 步骤 1：不点解析，直接点「开始下载」 ——
def step1():
    panel.url_entry.set(URL_A)
    assert panel._info is None
    panel._on_download_btn()  # 模拟点击主按钮
    status = panel.status_label.cget("text")
    print("点击后状态:", panel._status_full[0])
    assert "解析" in status, f"应进入自动解析，实际: {status}"
    app.after(200, lambda: shot("fix_auto_probe.png"))
    app.after(400, wait_auto_download)


def wait_auto_download(tries=0):
    if panel._downloading:
        print("自动解析完成并已开始下载，档位:", panel.quality_var.get(),
              "| 标题:", panel.title_label.cget("text")[:40])
        results.append("自动解析→下载 OK")
        app.after(2500, step1_shot_and_cancel)
        return
    if tries > 90:
        return fail("自动解析→下载 超时: " + panel.status_label.cget("text"))
    app.after(500, wait_auto_download, tries + 1)


def step1_shot_and_cancel():
    shot("fix_auto_downloading.png")
    panel.on_cancel()
    app.after(500, wait_cancel1)


def wait_cancel1(tries=0):
    if not panel._downloading and panel._cancel_event is None:
        app.after(300, step2)
        return
    if tries > 40:
        return fail("取消超时")
    app.after(400, wait_cancel1, tries + 1)


# —— 步骤 2：已解析状态下选 144p 档下载到完成 ——
def step2():
    label = next(v for v in panel.quality_select._values if "144p" in v)
    panel.quality_var.set(label)
    print("选择档位:", label)
    panel._on_download_btn()
    app.after(500, wait_done2)


def wait_done2(tries=0):
    if tries % 20 == 0:
        print(f"  [wait_done2 t={tries}] downloading={panel._downloading} "
              f"busy={panel._busy} status={panel._status_full[0][:60]}")
    if not panel._downloading and panel._last_file:
        size = os.path.getsize(panel._last_file) / 1024 / 1024
        print(f"选档下载完成: {os.path.basename(panel._last_file)} ({size:.1f} MB)")
        results.append("解析后选档下载 OK")
        shot("fix_quality_done.png")
        app.after(300, step3)
        return
    if not panel._downloading and not panel._busy and tries > 4:
        return fail("选档下载失败: " + panel.status_label.cget("text"))
    if tries > 900:
        return fail("选档下载超时")
    app.after(500, wait_done2, tries + 1)


# —— 步骤 3：换 URL 后旧解析结果应失效，再下载自动重新解析 ——
def step3():
    panel.url_entry.set(URL_B)  # url_var trace 应立即使旧解析结果失效
    app.update()
    assert panel._info is None, "换 URL 后旧解析结果应失效"
    assert panel.quality_var.get() == "请先解析链接", panel.quality_var.get()
    print("换 URL 后档位已重置:", panel.quality_var.get())
    results.append("换 URL 档位失效 OK")
    shot("fix_url_changed.png")
    panel._on_download_btn()
    app.after(400, wait_b_download)


def wait_b_download(tries=0):
    if panel._downloading:
        title = panel.title_label.cget("text")
        print("新 URL 自动解析后开始下载, 标题:", title[:50])
        assert "Big Buck Bunny" in title, "应下载新 URL 的视频"
        results.append("新 URL 自动解析下载 OK")
        app.after(1200, finish)
        return
    if tries > 90:
        return fail("新 URL 自动下载超时: " + panel.status_label.cget("text"))
    app.after(500, wait_b_download, tries + 1)


def finish():
    shot("fix_new_url_downloading.png")
    panel.on_cancel()
    app.after(1500, app.destroy)


def _report_exc(exc, val, tb):
    import traceback
    print("CALLBACK EXCEPTION:")
    traceback.print_exception(exc, val, tb)


app.report_callback_exception = _report_exc
app.after(800, step1)
app.mainloop()
print("mainloop 退出. downloading=", panel._downloading, "busy=", panel._busy,
      "status=", panel._status_full[0])
print("RESULTS:", results)
assert len(results) == 4, results
print("FIX FLOW ALL PASSED")
