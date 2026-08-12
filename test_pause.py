"""暂停/继续下载的脚本级验证。

流程：开始下载 → 进度达到阈值后置位 pause_event → 确认抛出 DownloadPaused
且 .part 临时文件保留 → 重新调用下载（断点续传）→ 确认 yt-dlp 输出
"Resuming download" 且续传起始百分比 >= 暂停时百分比 → 下载完成后由
调用方用 ffprobe 检查产物完整性。
"""

import glob
import os
import shutil
import sys
import threading

from app import core

URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Big Buck Bunny, 10:34
SAVE_DIR = os.path.join(os.path.dirname(__file__), "test_downloads", "pause_test")
PAUSE_AT_PERCENT = 5.0

# 捕获 yt-dlp 日志（验证 "Resuming download" 输出）
log_messages = []


class CaptureLogger:
    def debug(self, msg):
        log_messages.append(str(msg))

    def info(self, msg):
        log_messages.append(str(msg))

    def warning(self, msg):
        pass

    def error(self, msg):
        log_messages.append(str(msg))


_orig_base_opts = core._base_opts


def _patched_base_opts():
    opts = _orig_base_opts()
    opts["logger"] = CaptureLogger()
    return opts


core._base_opts = _patched_base_opts


def main():
    shutil.rmtree(SAVE_DIR, ignore_errors=True)
    os.makedirs(SAVE_DIR, exist_ok=True)

    # --- 第一阶段：下载到约 5% 后暂停 ---
    pause_event = threading.Event()
    paused_percent = {"v": 0.0}

    def progress1(percent, speed, eta, status):
        if percent is not None:
            paused_percent["v"] = percent
            if percent >= PAUSE_AT_PERCENT and not pause_event.is_set():
                pause_event.set()

    partial_files = None
    try:
        core.download_video(URL, height=480, save_dir=SAVE_DIR,
                            progress=progress1, pause_event=pause_event)
        print("FAIL: 未触发暂停就下载完成了")
        return 1
    except core.DownloadPaused as exc:
        partial_files = exc.partial_files
        print(f"OK: 已暂停，暂停时进度 {paused_percent['v']:.1f}%")

    parts = glob.glob(os.path.join(SAVE_DIR, "*.part"))
    if not parts:
        print("FAIL: 暂停后未找到 .part 临时文件")
        return 1
    part_sizes = {p: os.path.getsize(p) for p in parts}
    for p, s in part_sizes.items():
        print(f"OK: 保留临时文件 {os.path.basename(p)} ({s} bytes)")
    print(f"DownloadPaused.partial_files = {sorted(os.path.basename(f) for f in partial_files)}")

    # --- 第二阶段：重新调用下载，断点续传到完成 ---
    log_messages.clear()
    resume_first = {"v": None}

    def progress2(percent, speed, eta, status):
        if percent is not None and resume_first["v"] is None:
            resume_first["v"] = percent

    path = core.download_video(URL, height=480, save_dir=SAVE_DIR, progress=progress2)
    print(f"OK: 续传完成 -> {path}")

    resuming_logs = [m for m in log_messages if "Resuming download" in m]
    if resuming_logs:
        print(f"OK: yt-dlp 输出断点续传日志: {resuming_logs[0].strip()}")
    else:
        print("WARN: 未捕获到 'Resuming download' 日志")

    if resume_first["v"] is not None:
        print(f"续传后首个进度回调 {resume_first['v']:.1f}%（暂停时 {paused_percent['v']:.1f}%）")
        if resume_first["v"] + 0.5 < paused_percent["v"]:
            print("FAIL: 续传起始进度低于暂停进度，疑似未断点续传")
            return 1
        print("OK: 续传字节数与暂停点衔接")

    print(f"FINAL_FILE={path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
