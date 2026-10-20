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


def _ffprobe_in_dir(directory: str) -> bool:
    """该目录里有没有 ffprobe（Windows 上那个文件叫 ffprobe.exe）。"""
    for name in ("ffprobe", "ffprobe.exe"):
        candidate = os.path.join(directory, name)
        # os.access(X_OK) 在 Windows 上对存在的文件恒为真，留着只为 POSIX 严格性
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return True
    return False


def system_ffmpeg_dir():
    """PATH 上「ffmpeg 与 ffprobe 成对存在」的目录；没有则返回 None。

    只认成对的：yt-dlp 拿到**目录**形式的 ``ffmpeg_location`` 时会在该目录里同时
    找 ffmpeg 与 ffprobe，单独一个 ffprobe 拿不出可用的组合。
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    directory = os.path.dirname(ffmpeg)
    return directory if _ffprobe_in_dir(directory) else None


def has_ffprobe() -> bool:
    """yt-dlp **真的能用上** ffprobe 吗（按章节切分与 SponsorBlock 剪切需要它）。

    为什么不是「系统里有没有 ffprobe」：``ffmpeg_location_for_ytdlp()`` 会把 ffmpeg
    的**文件路径**当 ``ffmpeg_location`` 交给 yt-dlp，而 yt-dlp 拿到文件路径时只在
    该文件所在目录里找 ffprobe（实测 CreateProcess 会补 ``.exe``），**PATH 上的
    ffprobe 用不到**。所以只有两种形态算数：

    - ffprobe(.exe) 与内置 ffmpeg 同目录（yt-dlp 能在那儿找到）；
    - PATH 上有成对的 ffmpeg + ffprobe，且这次下载需要它——
      ``ffmpeg_location_for_ytdlp(need_ffprobe=True)`` 会把那个**目录**交出去。

    之前只看 ``shutil.which("ffprobe")``，于是「装了 ffmpeg 但没放到内置 ffmpeg
    旁边」时界面报「可用」，而真机实测 yt-dlp 的 ``probe_available`` 是 False，
    末章缺 end_time 的视频会以 ``Unable to determine video duration: ffprobe not
    found`` 失败——提示用户去装他早就装好的东西。
    """
    bundled = get_ffmpeg_path()
    if bundled and _ffprobe_in_dir(os.path.dirname(bundled)):
        return True
    return system_ffmpeg_dir() is not None


def ffmpeg_location_for_ytdlp(need_ffprobe: bool = False):
    """返回适合传给 yt-dlp `ffmpeg_location` 选项的路径（目录或文件均可）。

    找不到 ffmpeg 时返回 None，此时 yt-dlp 仍可下载已合并的单一格式，
    但无法执行音视频合并 / 音频转码。

    ``need_ffprobe=True``（这次下载会碰章节）时的顺序：
    内置 ffmpeg 旁边就有 ffprobe → 仍然用内置那把（保持静态二进制优先）；
    否则退到 PATH 上成对的 ffmpeg 目录，让 yt-dlp 同时找到 ffmpeg 与 ffprobe。
    两者都没有时照旧给内置 ffmpeg——此时 ``has_ffprobe()`` 也是 False，界面已经
    提前警告过了。
    """
    bundled = get_ffmpeg_path()
    if need_ffprobe:
        if bundled and _ffprobe_in_dir(os.path.dirname(bundled)):
            return bundled
        system = system_ffmpeg_dir()
        if system:
            return system
    return bundled
