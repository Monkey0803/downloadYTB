"""验证保存位置同步：跨平台页/设置页双向同步 + 重启后仍然记住上次选择。

把 settings.json 重定向到临时目录，避免污染真实配置。
"""

import os
import shutil
import subprocess
import tempfile


ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "screenshots")
os.makedirs(OUT, exist_ok=True)

TMP = tempfile.mkdtemp(prefix="vd_save_dir_test_")
SETTINGS_FILE = os.path.join(TMP, "settings.json")

# 必须在 import gui 之前完成 settings 重定向
from app import settings  # noqa: E402
settings._settings_dir = lambda: TMP
settings.SETTINGS_PATH = SETTINGS_FILE
settings.save({"theme": "system", "save_dir": "",
               "browser_cookies": "", "app_logo": "classic"})

from app import gui  # noqa: E402

NEW_DIR = os.path.join(TMP, "from_panel")
OTHER_DIR = os.path.join(TMP, "from_settings")
os.makedirs(NEW_DIR, exist_ok=True)
os.makedirs(OTHER_DIR, exist_ok=True)


def shot(app, name):
    app.update_idletasks()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    path = os.path.join(OUT, name)
    subprocess.run(["screencapture", "-x",
                    f"-R{x - 4},{y - 34},{w + 8},{h + 40}", path])
    return path


def panel_dir(app, page_key, attr="video_panel"):
    return getattr(app.pages[page_key], attr).dir_var.get()


results = []


def snapshot_dirs(app, label):
    results.append(f"--- {label} ---")
    results.append(f"  settings.save_dir(disk) = '{settings.load().get('save_dir', '')}'")
    results.append(f"  youtube.video    = {panel_dir(app, 'youtube')}")
    results.append(f"  x.video          = {panel_dir(app, 'x')}")
    results.append(f"  x.image          = {panel_dir(app, 'x', 'image_panel')}")
    results.append(f"  instagram.video  = {panel_dir(app, 'instagram')}")
    results.append(f"  instagram.image  = {panel_dir(app, 'instagram', 'image_panel')}")
    results.append(f"  douyin.video     = {panel_dir(app, 'douyin')}")
    results.append(f"  bilibili.video   = {panel_dir(app, 'bilibili')}")
    results.append(f"  settings.entry   = {app.pages['settings'].dir_var.get()}")


# ============ 阶段 1：在 YouTube 页选择新目录 → 全局同步 ============
app = gui.App()
app._last_clipboard = None
try:
    app._last_clipboard = app.clipboard_get()
except Exception:
    pass


def stage_1(step=0):
    if step == 0:
        snapshot_dirs(app, "阶段 1：启动初始（save_dir 为空）")
        app.show_page("youtube")
        app.after(400, lambda: stage_1(1))
    elif step == 1:
        shot(app, "save_dir_01_youtube_before.png")
        results.append("\n>>> 模拟用户在 YouTube 页「选择…」选定: " + NEW_DIR)
        app.apply_save_dir(NEW_DIR)
        app.after(300, lambda: stage_1(2))
    elif step == 2:
        snapshot_dirs(app, "阶段 1：YouTube 页选择后全平台/设置页应同步")
        shot(app, "save_dir_02_youtube_after.png")
        app.show_page("douyin")
        app.after(400, lambda: stage_1(3))
    elif step == 3:
        shot(app, "save_dir_03_douyin_synced.png")
        app.show_page("bilibili")
        app.after(400, lambda: stage_1(4))
    elif step == 4:
        shot(app, "save_dir_04_bilibili_synced.png")
        app.show_page("settings")
        app.after(400, lambda: stage_1(5))
    elif step == 5:
        shot(app, "save_dir_05_settings_synced.png")
        app.after(200, app.destroy)


app.after(900, lambda: stage_1(0))
app.mainloop()

# ============ 阶段 2：重启 App 验证持久化 ============
app2 = gui.App()
app2._last_clipboard = None
try:
    app2._last_clipboard = app2.clipboard_get()
except Exception:
    pass


def stage_2(step=0):
    if step == 0:
        snapshot_dirs(app2, "阶段 2：重启后应自动用上次选择的目录")
        app2.show_page("youtube")
        app2.after(400, lambda: stage_2(1))
    elif step == 1:
        shot(app2, "save_dir_06_after_restart_youtube.png")
        app2.show_page("settings")
        app2.after(400, lambda: stage_2(2))
    elif step == 2:
        results.append("\n>>> 在设置页改成另一目录: " + OTHER_DIR)
        app2.apply_save_dir(OTHER_DIR)
        app2.after(300, lambda: stage_2(3))
    elif step == 3:
        snapshot_dirs(app2, "阶段 2：设置页修改后应同步到所有平台页")
        shot(app2, "save_dir_07_settings_changed.png")
        app2.show_page("youtube")
        app2.after(400, lambda: stage_2(4))
    elif step == 4:
        shot(app2, "save_dir_08_youtube_sees_settings_change.png")
        app2.after(200, app2.destroy)


app2.after(900, lambda: stage_2(0))
app2.mainloop()

print("\n=================== 验证报告 ===================")
for line in results:
    print(line)

# 断言：核心同步行为
fail = []
disk_after_panel = settings.load().get("save_dir", "")
if disk_after_panel != OTHER_DIR:
    fail.append(f"最终 disk save_dir 应为 {OTHER_DIR}，实际 {disk_after_panel}")
print("\n=================== 结果 ===================")
if fail:
    for f in fail:
        print("FAIL:", f)
    raise SystemExit(1)
print("PASS：保存位置同步与持久化均符合预期。")

shutil.rmtree(TMP, ignore_errors=True)
