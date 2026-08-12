"""打包态「更换 App Logo」驱动脚本（经 VD_TEST_SCRIPT 在 .app 进程内执行）。

第一程（VD_LOGO_MODE=switch）：设置页截图（经典蓝选中）→ 切换霓虹 →
    截图窗口 + Dock 条 + 导出运行时 Dock 图标 → 退出。
第二程（VD_LOGO_MODE=restart）：确认启动恢复霓虹 → 截图 → 退出。

命名空间由 gui.run() 注入：app 为 App 实例。
"""

import os
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "screenshots")
MODE = os.environ.get("VD_LOGO_MODE", "switch")
os.makedirs(OUT, exist_ok=True)


def shot_window(name):
    """按 NSWindow 编号截图：即使窗口被其它应用遮挡也能拍到本窗口。"""
    app.update_idletasks()
    try:
        from AppKit import NSApp

        win_num = NSApp.windows()[0].windowNumber()
        subprocess.run(["screencapture", "-x", "-o", "-l", str(win_num),
                        os.path.join(OUT, name)])
        print("shot:", name, flush=True)
    except Exception as exc:
        print("shot failed:", name, exc, flush=True)


def shot_dock(name):
    sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
    subprocess.run(["screencapture", "-x", f"-R0,{sh - 130},{sw},130",
                    os.path.join(OUT, name)])
    print("shot:", name, flush=True)


def dump_dock_icon(name):
    try:
        from AppKit import NSApp, NSBitmapImageFileTypePNG, NSBitmapImageRep

        rep = NSBitmapImageRep.imageRepWithData_(
            NSApp.applicationIconImage().TIFFRepresentation())
        png = rep.representationUsingType_properties_(
            NSBitmapImageFileTypePNG, None)
        png.writeToFile_atomically_(os.path.join(OUT, name), True)
        print("dock icon dump:", name, flush=True)
    except Exception as exc:
        print("dock icon dump failed:", exc, flush=True)


def finish():
    app._destroy_app()


if MODE == "switch":
    def step1():
        print("pkg 启动 current_logo:", app.current_logo(), flush=True)
        app.show_page("settings")
        app.after(800, step2)

    def step2():
        shot_window("pkg_settings_classic.png")
        dump_dock_icon("pkg_dockicon_classic.png")
        app.pages["settings"]._on_logo_select("neon")
        app.after(1000, step3)

    def step3():
        shot_window("pkg_settings_neon.png")
        shot_dock("pkg_dock_neon.png")
        dump_dock_icon("pkg_dockicon_neon.png")
        app.after(400, finish)

    app.after(1500, step1)
else:
    def step1():
        cur = app.current_logo()
        print("pkg 重启 current_logo:", cur,
              "OK" if cur == "neon" else "FAIL", flush=True)
        app.show_page("settings")
        app.after(800, step2)

    def step2():
        shot_window("pkg_settings_neon_restart.png")
        shot_dock("pkg_dock_neon_restart.png")
        dump_dock_icon("pkg_dockicon_neon_restart.png")
        app.after(400, finish)

    app.after(1500, step1)
