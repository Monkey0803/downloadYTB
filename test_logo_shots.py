"""「更换 App Logo」验证脚本。

用法：
    .venv/bin/python test_logo_shots.py reset    # 第一程：重置为经典蓝 →
        设置页截图 → 切换霓虹 → 截图窗口 + Dock → 校验 settings.json
    .venv/bin/python test_logo_shots.py restart  # 第二程：重启后确认记住霓虹并截图
"""

import json
import os
import subprocess
import sys
import time

from app import gui, settings

MODE = sys.argv[1] if len(sys.argv) > 1 else "reset"
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "screenshots")
os.makedirs(OUT, exist_ok=True)

if MODE == "reset":
    data = settings.load()
    data["app_logo"] = "classic"
    settings.save(data)

app = gui.App()
try:
    app._last_clipboard = app.clipboard_get()
except Exception:
    pass
app.show_page("settings")
page = app.pages["settings"]


def shot_window(name):
    app.update_idletasks()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    subprocess.run(["screencapture", "-x",
                    f"-R{x - 4},{y - 34},{w + 8},{h + 40}",
                    os.path.join(OUT, name)])
    print("shot:", name)


def shot_dock(name):
    """Dock 默认自动隐藏：临时取消 autohide 截图后恢复。

    killall Dock 会丢失运行时 setApplicationIconImage_ 的图标，
    Dock 重启后需重新应用一次再截图。
    """
    sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
    subprocess.run(["defaults", "write", "com.apple.dock", "autohide",
                    "-bool", "false"])
    subprocess.run(["killall", "Dock"])
    time.sleep(2.5)
    app.apply_app_logo()  # Dock 重启后重新声明运行时图标
    time.sleep(1.0)
    subprocess.run(["screencapture", "-x",
                    f"-R0,{sh - 130},{sw},130",
                    os.path.join(OUT, name)])
    subprocess.run(["defaults", "write", "com.apple.dock", "autohide",
                    "-bool", "true"])
    subprocess.run(["killall", "Dock"])
    print("shot:", name)


def dump_dock_icon(name):
    """把运行中应用的 Dock 图标（NSApp.applicationIconImage）存成 PNG。"""
    try:
        from AppKit import NSApp, NSBitmapImageFileTypePNG, NSBitmapImageRep

        img = NSApp.applicationIconImage()
        rep = NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
        png = rep.representationUsingType_properties_(
            NSBitmapImageFileTypePNG, None)
        png.writeToFile_atomically_(os.path.join(OUT, name), True)
        print("dock icon dump:", name)
    except Exception as exc:
        print("dock icon dump failed:", exc)


def check_saved(expect):
    with open(settings.SETTINGS_PATH, encoding="utf-8") as fh:
        saved = json.load(fh).get("app_logo")
    print(f"settings.json app_logo = {saved!r}（期望 {expect!r}）",
          "OK" if saved == expect else "FAIL")


if MODE == "reset":
    def step1():
        print("启动时 current_logo:", app.current_logo())
        shot_window("settings_logo_classic.png")
        dump_dock_icon("dockicon_classic.png")
        page._on_logo_select("neon")
        app.after(800, step2)

    def step2():
        shot_window("settings_logo_neon.png")
        dump_dock_icon("dockicon_neon.png")
        shot_dock("dock_neon.png")
        check_saved("neon")
        app.after(300, app.destroy)

    app.after(1200, step1)
else:
    def step1():
        cur = app.current_logo()
        print("重启后 current_logo:", cur, "OK" if cur == "neon" else "FAIL")
        sel = [k for k, sw in page._logo_swatches.items() if sw._selected]
        print("设置页选中态:", sel, "OK" if sel == ["neon"] else "FAIL")
        shot_window("settings_logo_neon_restart.png")
        dump_dock_icon("dockicon_neon_restart.png")
        app.after(300, app.destroy)

    app.after(1200, step1)

app.mainloop()
print("LOGO TEST DONE:", MODE)
