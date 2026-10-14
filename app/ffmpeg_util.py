"""定位 ffmpeg 二进制路径。

优先使用 imageio-ffmpeg 自带的跨平台静态二进制（随 pip 安装、可被
PyInstaller 一并打包），找不到时回退到系统 PATH 中的 ffmpeg。
"""

import os
import shutil


def get_ffmpeg_path():
    """返回 ffmpeg 可执行文件的绝对路径；找不到则返回 None。"""
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    return shutil.which("ffmpeg")


def has_ffprobe() -> bool:
    """当前能否用到 ffprobe（按章节切分与 SponsorBlock 剪切需要它）。

    只看两个位置：ffmpeg 所在目录（yt-dlp 会在那儿找同名工具）与 PATH。
    imageio-ffmpeg 带的二进制里**没有** ffprobe，所以打包态通常为 False——
    界面据此提示，而不是等用户下载到一半再失败。
    """
    import shutil

    ffmpeg = get_ffmpeg_path()
    if ffmpeg:
        candidate = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return True
    return shutil.which("ffprobe") is not None


def ffmpeg_location_for_ytdlp():
    """返回适合传给 yt-dlp `ffmpeg_location` 选项的路径（目录或文件均可）。

    找不到 ffmpeg 时返回 None，此时 yt-dlp 仍可下载已合并的单一格式，
    但无法执行音视频合并 / 音频转码。
    """
    return get_ffmpeg_path()
