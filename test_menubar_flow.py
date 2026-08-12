"""开发态验证 menubar 集成：

1. 启动 App，确认 menubar 控制器已创建；
2. 模拟点击红色关闭按钮（WM_DELETE_WINDOW 协议）→ 窗口应隐藏、进程常驻；
3. 调用 menubar「显示主窗口」回调 → 窗口应恢复 normal；
4. 验证状态文案接口；
5. 通过 quit_from_menubar 正常退出（无下载 → 直接退出）。

用法：.venv/bin/python test_menubar_flow.py
"""

import sys

from app.gui import App

app = App()
results = []


def check(name, ok):
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name, flush=True)


def step1():
    check("menubar 控制器已创建", app.menubar is not None)
    check("初始状态文案为「空闲」", app.menubar_status_text() == "空闲")
    # 模拟红色关闭按钮：Tk 对 WM_DELETE_WINDOW 协议的回调
    app._on_close_window()
    app.after(500, step2)


def step2():
    check("关闭窗口后为隐藏状态", app.state() == "withdrawn")
    # 模拟 menubar 菜单「显示主窗口」：与真实点击一致地走 pending + 轮询路径
    app.menubar.pending = "show"
    app.after(600, step3)


def step3():
    check("「显示主窗口」后窗口恢复", app.state() == "normal")
    # 菜单弹出时的状态刷新路径（menuNeedsUpdate_ → refresh_status）
    app.menubar.refresh_status()
    check("refresh_status 不抛异常", True)
    # 无下载时 quit_from_menubar 应直接退出
    app.quit_from_menubar()


app.after(1200, step1)
app.mainloop()

failed = [n for n, ok in results if not ok]
print("RESULT:", "ALL PASS" if not failed else f"FAILED: {failed}", flush=True)
sys.exit(1 if failed else 0)
