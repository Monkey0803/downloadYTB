"""从 assets/app_logo.png 生成应用图标。

处理流程：
1. 居中裁剪为正方形；
2. 从四角 flood-fill 移除纯黑背景（圆角外透明），边缘 1px 羽化；
3. 内容居中缩放到 1024 画布（按 macOS 图标栅格留边）；
4. 产出 assets/app.icns（16~1024 全尺寸）、assets/app.ico（16~256）、
   assets/app_icon.png（512，运行时窗口图标用）、
   assets/menubar_template.png（36px 单色模板图，macOS menubar 状态栏图标用，
   黑色 + alpha，运行时由 NSImage template 模式自动适配深浅菜单栏）。

用法：.venv/bin/python build/make_icons.py
"""

import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
SRC = os.path.join(ASSETS, "app_logo.png")

# 内容（含光晕）在 1024 画布中的目标尺寸；macOS 标准圆角矩形为 824，
# 光晕略大于圆角主体，这里取 880 让主体接近系统图标大小。
CANVAS = 1024
CONTENT = 880


def center_square(img):
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def remove_black_background(img):
    """把与四角连通的近黑区域变透明，边缘做 1px 羽化。"""
    rgb = img.convert("RGB")
    # 标记图：在副本上 flood fill 成品红色，再据此生成背景掩码
    marker = (255, 0, 255)
    flooded = rgb.copy()
    w, h = flooded.size
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        ImageDraw.floodfill(flooded, seed, marker, thresh=40)

    bg_mask = Image.new("L", (w, h), 0)
    src_px = flooded.load()
    mask_px = bg_mask.load()
    for y in range(h):
        for x in range(w):
            if src_px[x, y] == marker:
                mask_px[x, y] = 255

    # 羽化边缘，alpha = 255 - mask
    bg_mask = bg_mask.filter(ImageFilter.GaussianBlur(1))
    alpha = bg_mask.point(lambda v: 255 - v)
    out = img.convert("RGBA")
    out.putalpha(alpha)
    return out


def fit_canvas(img):
    """裁剪到内容包围盒，再居中缩放到标准画布。"""
    bbox = img.getbbox()
    content = img.crop(bbox)
    side = max(content.size)
    # 先放到正方形透明画布上保持比例
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(content, ((side - content.size[0]) // 2,
                           (side - content.size[1]) // 2))
    scaled = square.resize((CONTENT, CONTENT), Image.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    off = (CANVAS - CONTENT) // 2
    canvas.paste(scaled, (off, off))
    return canvas


def make_icns(master):
    iconset = os.path.join(ROOT, "build", "app.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    try:
        for size in (16, 32, 128, 256, 512):
            master.resize((size, size), Image.LANCZOS).save(
                os.path.join(iconset, f"icon_{size}x{size}.png"))
            master.resize((size * 2, size * 2), Image.LANCZOS).save(
                os.path.join(iconset, f"icon_{size}x{size}@2x.png"))
        out = os.path.join(ASSETS, "app.icns")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out],
                       check=True)
        return out
    finally:
        shutil.rmtree(iconset, ignore_errors=True)


def make_menubar_template():
    """绘制「下载箭头 + 托盘」单色模板图（呼应 logo 中心图形）。

    menubar 图标不直接缩小彩色 logo：18px 下玻璃质感会糊成色块，
    且无法适配深色菜单栏。模板图为纯黑 + alpha，由 AppKit 按菜单栏
    外观自动着色。输出 36px（@2x，运行时 setSize 为 18pt）。
    """
    S = 360  # 18pt 的 20 倍超采样
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    black = (0, 0, 0, 255)
    cx = S // 2

    # 箭头：圆角竖杆 + 三角头
    shaft_w, shaft_top, head_top = 56, 30, 150
    head_w, head_h = 150, 95
    d.rounded_rectangle([cx - shaft_w // 2, shaft_top,
                         cx + shaft_w // 2, head_top + 12],
                        radius=24, fill=black)
    d.polygon([(cx - head_w // 2, head_top), (cx + head_w // 2, head_top),
               (cx, head_top + head_h)], fill=black)

    # 托盘：U 形
    t, top, bot, l, r = 40, 240, 330, 36, S - 36
    d.rounded_rectangle([l, top, l + t, bot], radius=18, fill=black)
    d.rounded_rectangle([r - t, top, r, bot], radius=18, fill=black)
    d.rounded_rectangle([l, bot - t, r, bot], radius=18, fill=black)

    out = os.path.join(ASSETS, "menubar_template.png")
    img.resize((36, 36), Image.LANCZOS).save(out)
    return out


def make_ico(master):
    out = os.path.join(ASSETS, "app.ico")
    master.save(out, format="ICO",
                sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    return out


def main():
    img = Image.open(SRC)
    img = center_square(img)
    img = remove_black_background(img)
    master = fit_canvas(img)

    master.save(os.path.join(ASSETS, "app_icon_1024.png"))
    master.resize((512, 512), Image.LANCZOS).save(
        os.path.join(ASSETS, "app_icon.png"))
    icns = make_icns(master) if sys.platform == "darwin" else "(非 macOS 跳过)"
    ico = make_ico(master)
    menubar = make_menubar_template()
    print("生成：")
    print(" ", os.path.join(ASSETS, "app_icon_1024.png"))
    print(" ", os.path.join(ASSETS, "app_icon.png"))
    print(" ", icns)
    print(" ", ico)
    print(" ", menubar)


if __name__ == "__main__":
    main()
