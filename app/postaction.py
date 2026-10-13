"""下载完成后的后续动作（ROADMAP 第 8 项）。

为什么值得做：播放列表 / 队列下载往往是「挂上就走」，下完之后还要自己回来打开文件夹，
或者让机器空转一整夜。

一条统一规则：**只在应用真正空闲时触发**——没有面板在下载、队列里也没有待办。
否则给播放列表的 25 个条目各开一次文件夹窗口，比不做还烦人。

两条安全约束：

- **睡眠而不是关机**：关机不可逆（误触发就会丢掉没保存的东西），睡眠随时能唤醒，
  对「挂机下载一整夜」这个真实场景完全够用。
- **任何动作失败都不影响应用**：打开文件夹失败、系统没有对应的睡眠命令，都只记日志。
"""

import subprocess
import sys

from . import i18n

# 动作代号（ASCII，会写进 settings.json，不可改）
OPTIONS = ("none", "open_folder", "open_file", "quit", "sleep")

# 「挂机」类动作：会让用户离开，因此必须等队列彻底跑完
DISRUPTIVE = ("quit", "sleep")

SLEEP_COMMANDS = {
    "darwin": ["pmset", "sleepnow"],
    "win32": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
    "linux": ["systemctl", "suspend"],
}


def normalize(value) -> str:
    """把设置里的值收敛成合法代号，非法一律当 ``none``。"""
    code = str(value or "").strip().lower()
    return code if code in OPTIONS else "none"


def is_disruptive(value) -> bool:
    return normalize(value) in DISRUPTIVE


def sleep_command(platform=None):
    """当前平台的睡眠命令；不支持的平台返回 ``None``。"""
    key = sys.platform if platform is None else platform
    if key.startswith("linux"):
        key = "linux"
    return list(SLEEP_COMMANDS.get(key) or []) or None


def run_sleep() -> bool:
    """让系统进入睡眠；返回是否真的发出了命令。"""
    command = sleep_command()
    if not command:
        return False
    subprocess.Popen(command)          # noqa: S603 - 命令是常量表里取出的
    return True


def value_for_label(text) -> str:
    """界面文案反查动作代号（下拉框只存显示文本）。"""
    wanted = str(text or "")
    for code in OPTIONS:
        if label(code) == wanted:
            return code
    return "none"


def label(value) -> str:
    """动作代号对应的界面文案。"""
    return i18n.tr({
        "open_folder": "after_download_open_folder",
        "open_file": "after_download_open_file",
        "quit": "after_download_quit",
        "sleep": "after_download_sleep",
    }.get(normalize(value), "after_download_none"))
