"""窗口尺寸/位置的解析与可见性判断。

拆成独立模块是为了让「恢复上次窗口几何」这段逻辑可以脱离 Tk 单测：
Tk 只能报告主屏尺寸，多显示器下用户可能把窗口放在负坐标或超出主屏的
区域，因此这里用 NSScreen 取全部屏幕矩形（macOS），其他平台退回主屏。
"""

import re
import sys

# "1020x745" 或 "1020x745+120+80"；坐标允许为负（副屏在主屏左侧/上方）
_GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?$")

# 窗口至少要有多大面积落在某块屏幕内才认为「够得着」
MIN_VISIBLE_PX = 80


def parse_geometry(value: str, fallback: str):
    """解析 Tk 的几何字符串。

    返回 ``(width, height, x, y)``；坐标缺失或格式非法时为 ``None``，
    此时退回 fallback 的尺寸，由窗口管理器决定摆放位置。
    """
    match = _GEOMETRY_RE.match((value or "").strip())
    if not match:
        match = _GEOMETRY_RE.match((fallback or "").strip())
        if not match:
            return None
        return int(match.group(1)), int(match.group(2)), None, None

    width, height = int(match.group(1)), int(match.group(2))
    if match.group(3) is None:
        return width, height, None, None
    return width, height, int(match.group(3)), int(match.group(4))


def screen_rects(widget):
    """返回所有屏幕的矩形列表 ``[(x, y, width, height), ...]``。

    macOS 走 NSScreen，可覆盖副屏与负坐标；取不到时退回 Tk 的主屏尺寸。
    """
    if sys.platform == "darwin":
        try:
            from AppKit import NSScreen
            # NSScreen 的原点在左下角，Tk 在左上角：按主屏高度换算。
            screens = NSScreen.screens()
            if screens:
                primary_height = screens[0].frame().size.height
                rects = []
                for screen in screens:
                    frame = screen.frame()
                    rects.append((
                        int(frame.origin.x),
                        int(primary_height - frame.origin.y - frame.size.height),
                        int(frame.size.width),
                        int(frame.size.height),
                    ))
                return rects
        except Exception:
            pass
    return [(0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight())]


def is_reachable(x, y, width, height, rects, min_visible=MIN_VISIBLE_PX):
    """窗口矩形与任一屏幕是否有足够的重叠面积。"""
    for rx, ry, rw, rh in rects:
        overlap_w = min(x + width, rx + rw) - max(x, rx)
        overlap_h = min(y + height, ry + rh) - max(y, ry)
        if overlap_w >= min_visible and overlap_h >= min_visible:
            return True
    return False


def restore(widget, saved: str, fallback: str, min_width: int, min_height: int) -> str:
    """按保存的几何恢复窗口，返回实际应用的几何字符串。

    尺寸会被夹在 [minsize, 屏幕] 之间；位置只在「确实够得着」时才采用，
    避免拔掉外接显示器后窗口落在看不见的地方。
    """
    parsed = parse_geometry(saved, fallback)
    if parsed is None:
        return ""
    width, height, x, y = parsed

    screen_w = widget.winfo_screenwidth()
    screen_h = widget.winfo_screenheight()
    width = max(min_width, min(width, screen_w))
    height = max(min_height, min(height, screen_h))

    if x is not None and is_reachable(x, y, width, height, screen_rects(widget)):
        geometry = f"{width}x{height}{x:+d}{y:+d}"
    else:
        geometry = f"{width}x{height}"
    widget.geometry(geometry)
    return geometry
