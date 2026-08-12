"""为「更换 App Logo」功能生成 4 款备选图标。

复用 build/make_icons.py 的同套处理逻辑：
1. 居中裁剪为正方形；
2. 从四角 flood-fill 去底（PIL floodfill 以种子像素颜色为基准，
   黑底 neon/sunset 与白底 mint 同一套逻辑都能正确去除），边缘 1px 羽化；
3. 内容居中缩放到 1024 透明画布。

产出（每款 key ∈ classic/neon/sunset/mint）：
    assets/logos/{key}_icon.png     1024，运行时 Dock 图标用
    assets/logos/{key}_512.png      512，窗口图标 + 设置页预览用
    assets/logos/{key}_menubar.png  36px（18pt@2x），macOS menubar 状态栏图标用

用法：.venv/bin/python build/make_logo_variants.py
"""

import os
import sys

from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_icons import ASSETS, center_square, fit_canvas, remove_black_background

LOGOS = {
    "classic": "app_logo.png",
    "neon": "logo_neon.png",
    "sunset": "logo_sunset.png",
    "mint": "logo_mint.png",
}

OUT_DIR = os.path.join(ASSETS, "logos")

MENUBAR_PX = 36  # 18pt @2x；运行时 NSImage setSize 为 18pt


def make_menubar_png(master, out_path):
    """从 1024 主图生成 menubar 彩色小图。

    1024 画布四周有 macOS 图标栅格留白，直接缩小会让 18pt 下内容
    只剩 ~15pt；这里先裁到内容包围盒让图形占满画布，再 LANCZOS
    缩小并做轻度 UnsharpMask，保证 menubar 实际尺寸下轮廓清晰。
    """
    bbox = master.getbbox()
    content = master.crop(bbox)
    side = max(content.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(content, ((side - content.size[0]) // 2,
                           (side - content.size[1]) // 2))
    small = square.resize((MENUBAR_PX, MENUBAR_PX), Image.LANCZOS)
    small = small.filter(ImageFilter.UnsharpMask(radius=1.2, percent=90,
                                                 threshold=2))
    small.save(out_path)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for key, fname in LOGOS.items():
        src = os.path.join(ASSETS, fname)
        img = Image.open(src)
        img = center_square(img)
        img = remove_black_background(img)  # 以四角种子色为底色基准去底
        master = fit_canvas(img)

        icon_path = os.path.join(OUT_DIR, f"{key}_icon.png")
        small_path = os.path.join(OUT_DIR, f"{key}_512.png")
        menubar_path = os.path.join(OUT_DIR, f"{key}_menubar.png")
        master.save(icon_path)
        master.resize((512, 512), Image.LANCZOS).save(small_path)
        make_menubar_png(master, menubar_path)
        print(f"{key:8s} {fname} ->")
        print(f"  {icon_path}")
        print(f"  {small_path}")
        print(f"  {menubar_path}")


if __name__ == "__main__":
    main()
