"""图片引擎 + X 视频实测（需联网）：
- X 视频推文下载（yt-dlp）
- X 图片帖解析出多张图：全选下载 / 只选一张下载，校验原图分辨率
- 图片下载中途取消生效
- Instagram 匿名访问降级提示
直接运行：.venv/bin/python test_images.py"""

import os
import shutil
import threading

from PIL import Image

from app import core, images

X_VIDEO = "https://x.com/perrypumas/status/1065692031626829824"
X_PHOTOS = "https://x.com/perrypumas/status/894001459754180609"  # 4 张图
IG_POST = "https://www.instagram.com/p/BqvsDleB3lV/"

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "test_downloads", "imgtest")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT, exist_ok=True)


def check_x_video():
    info = core.probe(X_VIDEO)
    print(f"X 视频: {info.title!r} | 档位: {[q.display for q in info.qualities]}")
    path = core.download_video(X_VIDEO, save_dir=OUT)
    print(f"X 视频下载完成: {os.path.basename(path)} "
          f"({os.path.getsize(path)/1024/1024:.2f} MB)")
    assert os.path.getsize(path) > 100 * 1024


def check_x_photos_all():
    post = images.probe_images(X_PHOTOS)
    print(f"X 图片帖: 共 {len(post.items)} 张 | 作者 {post.author!r}")
    assert len(post.items) >= 4, "应解析出 4 张图"
    for it in post.items:
        print(f"  图{it.index}: {it.size_text}  orig={'name=orig' in it.url}  {it.url}")
        assert "name=orig" in it.url, "应为 orig 原图链接"
    d = os.path.join(OUT, "all")
    paths = images.download_images(post.items, save_dir=d)
    assert len(paths) == len(post.items)
    for p in paths:
        im = Image.open(p)
        print(f"  已存 {os.path.basename(p)}  实际分辨率 {im.size[0]}x{im.size[1]} "
              f"({os.path.getsize(p)/1024:.0f} KB)")
    return post


def check_x_photo_single(post):
    d = os.path.join(OUT, "single")
    paths = images.download_images([post.items[1]], save_dir=d)
    assert len(paths) == 1 and len(os.listdir(d)) == 1
    im = Image.open(paths[0])
    print(f"单张下载: {os.path.basename(paths[0])} 分辨率 {im.size[0]}x{im.size[1]}")
    # 与解析出的宽高一致 = 拿到原图
    it = post.items[1]
    if it.width and it.height:
        assert im.size == (it.width, it.height), "下载分辨率应与解析的原图一致"


def check_cancel(post):
    d = os.path.join(OUT, "cancel")
    cancel = threading.Event()
    state = {"n": 0}

    def progress(percent, text):
        state["n"] += 1
        if state["n"] >= 2:  # 第一张进行中即取消
            cancel.set()

    try:
        images.download_images(post.items, save_dir=d,
                               progress=progress, cancel_event=cancel)
        raise AssertionError("应触发 ImageCancelled")
    except images.ImageCancelled:
        leftover_parts = [f for f in os.listdir(d) if f.endswith(".part")]
        print(f"取消生效：目录文件 {os.listdir(d) or '无'}，.part 残留 {leftover_parts or '无'}")
        assert not leftover_parts, "取消后不应残留 .part"


def check_instagram():
    try:
        images.probe_images(IG_POST)
        print("IG 解析意外成功（平台政策可能变化）")
    except images.ImageError as e:
        print(f"IG 降级提示: {e}")


if __name__ == "__main__":
    print("== X 视频 =="); check_x_video()
    print("== X 图片全选 =="); post = check_x_photos_all()
    print("== X 图片单张 =="); check_x_photo_single(post)
    print("== X 图片取消 =="); check_cancel(post)
    print("== Instagram =="); check_instagram()
    print("ALL IMAGE TESTS PASSED")
