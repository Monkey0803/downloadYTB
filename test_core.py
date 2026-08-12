"""核心引擎实测（需联网）：YouTube 解析 / 1080p 下载合并 / 仅音频 /
暂停→续传 / 取消清理。直接运行：.venv/bin/python test_core.py"""

import os
import shutil
import threading
import time

from app import core

URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Big Buck Bunny (CC)
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "test_downloads", "core")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT, exist_ok=True)


def check_probe():
    info = core.probe(URL)
    print(f"标题: {info.title} | 时长: {info.duration_text} | 上传者: {info.uploader}")
    for q in info.qualities:
        print("  档位:", q.display, "| max_height:", q.max_height)
    assert any(q.label == "1080p" for q in info.qualities), "缺少 1080p 档位"
    assert any(q.size_bytes for q in info.qualities), "档位缺少大小估算"
    return info


def check_video_1080():
    path = core.download_video(URL, height=1080, save_dir=OUT)
    size = os.path.getsize(path)
    print(f"1080p 下载完成: {os.path.basename(path)} ({size/1024/1024:.1f} MB)")
    assert path.endswith(".mp4") and size > 5 * 1024 * 1024
    return path


def check_audio():
    path = core.download_audio(URL, save_dir=OUT)
    size = os.path.getsize(path)
    print(f"音频下载完成: {os.path.basename(path)} ({size/1024/1024:.1f} MB)")
    assert path.endswith(".mp3") and size > 1024 * 1024
    return path


def check_pause_resume():
    out = os.path.join(OUT, "pause")
    os.makedirs(out, exist_ok=True)
    cancel = threading.Event()
    pause = threading.Event()
    state = {"percent": 0.0}

    def progress(p, *a):
        if p:
            state["percent"] = p
            if p > 8:
                pause.set()

    partial = None
    try:
        core.download_video(URL, height=720, save_dir=out,
                            progress=progress, cancel_event=cancel, pause_event=pause)
        raise AssertionError("应触发 DownloadPaused")
    except core.DownloadPaused as exc:
        partial = exc.partial_files
        parts = [f for f in os.listdir(out) if ".part" in f or ".ytdl" in f]
        print(f"暂停于 {state['percent']:.1f}%，保留临时文件: {parts}")
        assert parts, "暂停后应保留 .part 临时文件"

    t0 = time.time()
    path = core.download_video(URL, height=720, save_dir=out)
    print(f"续传完成: {os.path.basename(path)} ({os.path.getsize(path)/1024/1024:.1f} MB)，"
          f"耗时 {time.time()-t0:.1f}s")
    return path


def check_cancel():
    out = os.path.join(OUT, "cancel")
    os.makedirs(out, exist_ok=True)
    cancel = threading.Event()

    def progress(p, *a):
        if p and p > 5:
            cancel.set()

    try:
        core.download_video(URL, height=720, save_dir=out,
                            progress=progress, cancel_event=cancel)
        raise AssertionError("应触发 DownloadCancelled")
    except core.DownloadCancelled:
        leftovers = os.listdir(out)
        print(f"取消成功，残留文件: {leftovers or '无'}")
        assert not leftovers, "取消后应清理全部临时文件"


if __name__ == "__main__":
    print("== probe =="); check_probe()
    print("== 1080p =="); check_video_1080()
    print("== audio =="); check_audio()
    print("== pause/resume =="); check_pause_resume()
    print("== cancel =="); check_cancel()
    print("ALL CORE TESTS PASSED")
