"""窗口尺寸/位置持久化的纯逻辑测试（不需要 Tk）。

多显示器下窗口坐标可能是负数、也可能超出主屏，恢复时必须判断「够不够得着」；
用一个假 widget 注入屏幕矩形，避免测试结果依赖运行机器的显示器布局。
"""

from app import windowing

FALLBACK = "1020x745"


class _FakeWidget:
    """只实现 windowing.restore 用到的最小接口。"""

    def __init__(self, screen=(1920, 1080)):
        self.applied = ""
        self._screen = screen

    def geometry(self, value):
        self.applied = value

    def winfo_screenwidth(self):
        return self._screen[0]

    def winfo_screenheight(self):
        return self._screen[1]


def _with_rects(rects):
    original = windowing.screen_rects
    windowing.screen_rects = lambda widget: rects
    return original


def test_parse_geometry_accepts_all_tk_forms():
    assert windowing.parse_geometry("1020x745+120+80", FALLBACK) == (1020, 745, 120, 80)
    assert windowing.parse_geometry("1020x745", FALLBACK) == (1020, 745, None, None)
    # 副屏在主屏左侧/上方时坐标是负数
    assert windowing.parse_geometry("800x600-50-30", FALLBACK) == (800, 600, -50, -30)
    # 非法或空值退回默认尺寸且不带位置
    assert windowing.parse_geometry("", FALLBACK) == (1020, 745, None, None)
    assert windowing.parse_geometry("garbage", FALLBACK) == (1020, 745, None, None)


def test_is_reachable_needs_enough_overlap():
    rects = [(0, 0, 1920, 1080), (-1920, 0, 1920, 1080)]
    assert windowing.is_reachable(100, 100, 1020, 745, rects)
    assert windowing.is_reachable(-1800, 100, 1020, 745, rects)   # 副屏
    # 只露出 20px，不够 80px 的可见阈值
    assert not windowing.is_reachable(1900, 1000, 1020, 745, rects)
    assert not windowing.is_reachable(5000, 5000, 1020, 745, rects)


def test_restore_keeps_size_and_position_when_reachable():
    original = _with_rects([(0, 0, 1920, 1080)])
    try:
        widget = _FakeWidget()
        applied = windowing.restore(widget, "900x800+120+80", FALLBACK, 920, 700)
    finally:
        windowing.screen_rects = original
    # 宽度小于 minsize 会被抬到 920
    assert applied == "920x800+120+80"
    assert widget.applied == applied


def test_restore_drops_position_when_offscreen():
    """拔掉外接显示器后，窗口不应落在看不见的地方。"""
    original = _with_rects([(0, 0, 1920, 1080)])
    try:
        widget = _FakeWidget()
        applied = windowing.restore(widget, "1020x745+9000+9000", FALLBACK, 920, 700)
    finally:
        windowing.screen_rects = original
    assert applied == "1020x745"          # 位置被丢弃
    assert "+" not in applied


def test_restore_clamps_size_to_screen():
    original = _with_rects([(0, 0, 1280, 800)])
    try:
        widget = _FakeWidget(screen=(1280, 800))
        applied = windowing.restore(widget, "4000x3000", FALLBACK, 920, 700)
    finally:
        windowing.screen_rects = original
    assert applied == "1280x800"


if __name__ == "__main__":
    test_parse_geometry_accepts_all_tk_forms()
    test_is_reachable_needs_enough_overlap()
    test_restore_keeps_size_and_position_when_reachable()
    test_restore_drops_position_when_offscreen()
    test_restore_clamps_size_to_screen()
    print("PASS: 窗口几何解析、可见性判断与恢复")
