"""对比度审计（#12）。

把主题里所有「文字压在底色上」的组合按 WCAG 相对亮度公式算一遍，锁住下限：

- 正文/按钮文字等**普通文字**要求 ≥ 4.5:1
- 次要文字、大号文字要求 ≥ 3.0:1
- 非文字图形（对勾、焦点环）要求 ≥ 3.0:1
- **禁用态豁免**（WCAG 1.4.3 明确豁免 inactive 组件），但必须显式列出来，
  不能让「没测到」伪装成「通过了」

不依赖 Tk，跑得很快；任何配色改动都会在这里立刻反映出来。
"""

import unittest

from app import theme

# 普通文字：≥ 4.5
TEXT_PAIRS = (
    ("正文 on 页面", "TEXT", "BG", 4.5),
    ("正文 on 卡片", "TEXT", "CARD", 4.5),
    ("正文 on 输入框", "TEXT", "FIELD_BG", 4.5),
    ("实心按钮文字", None, "ACCENT_FILL", 4.5),          # 白字，见下
    ("实心按钮文字(悬停)", None, "ACCENT_FILL_HOVER", 4.5),
    ("侧边栏选中文字", "SIDEBAR_TEXT_ACTIVE", "SIDEBAR_ACTIVE", 4.5),
    ("分段控件选中文字", "TEXT", "SEG_ACTIVE", 4.5),
    ("错误文字 on 页面", "ERROR", "BG", 4.5),
    ("错误文字 on 卡片", "ERROR", "CARD", 4.5),
    ("成功文字 on 页面", "OK", "BG", 4.5),
    ("成功文字 on 卡片", "OK", "CARD", 4.5),
)

# 次要文字 / 非文字图形：≥ 3.0
SECONDARY_PAIRS = (
    ("次要文字 on 页面", "TEXT_SUB", "BG", 3.0),
    ("次要文字 on 卡片", "TEXT_SUB", "CARD", 3.0),
    ("次要文字 on 输入框", "TEXT_SUB", "FIELD_BG", 3.0),
    ("分段控件未选中文字", "TEXT_SUB", "FIELD_BG", 3.0),
    ("侧边栏文字", "SIDEBAR_TEXT", "SIDEBAR_BG", 3.0),
    ("输入框描边", "FIELD_BORDER", "FIELD_BG", 3.0),
    ("复选框描边", "CHECK_BORDER", "FIELD_BG", 3.0),
    ("主色对勾 on 选中块", None, "ACCENT", 3.0),          # 白线，见下
    ("进度条填充 on 轨道", "PROGRESS_FILL", "TRACK", 3.0),
)

# 明确豁免：禁用态按 WCAG 1.4.3 不受对比度要求约束（用于表达「不可用」）。
# 按主题分开声明：同一个角色在两套配色下的结果并不相同，混在一起会给出错误结论
# （深色的 ACCENT_DISABLED 白字其实有 5.9，根本不需要豁免）。
EXEMPT_PAIRS = {
    "light": (
        ("禁用主色按钮文字", None, "ACCENT_DISABLED"),
        ("禁用次级按钮文字", "PILL_TEXT_DISABLED", "PILL_BG"),
    ),
    "dark": (
        ("禁用次级按钮文字", "PILL_TEXT_DISABLED", "PILL_BG"),
    ),
}


def _luminance(color):
    value = color.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def linear(channel):
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(c) for c in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground, background):
    first, second = _luminance(foreground), _luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


WHITE = "#FFFFFF"


class ContrastTests(unittest.TestCase):
    def _check(self, palette, pairs, label):
        failures = []
        for name, fg_role, bg_role, minimum in pairs:
            fg = WHITE if fg_role is None else getattr(palette, fg_role)
            value = contrast(fg, getattr(palette, bg_role))
            if value + 1e-9 < minimum:
                failures.append(f"{name}: {value:.2f} < {minimum}")
        self.assertEqual(failures, [], f"{palette.name}（{label}）对比度不足：{failures}")

    def test_text_contrast_in_both_themes(self):
        for palette, label in ((theme.LIGHT, "浅色"), (theme.DARK, "深色")):
            self._check(palette, TEXT_PAIRS, label)

    def test_secondary_and_graphic_contrast(self):
        for palette, label in ((theme.LIGHT, "浅色"), (theme.DARK, "深色")):
            self._check(palette, SECONDARY_PAIRS, label)

    def test_disabled_pairs_are_low_but_explicitly_exempt(self):
        """禁用态本来就该显得「暗淡」，但要显式记录豁免，而不是漏测。

        豁免的理由是「达不到普通文字要求」；哪天某个组合真的达标了，就该把它
        从名单里移出去正式检查——这条断言就是提醒。
        """
        for palette in (theme.LIGHT, theme.DARK):
            for name, fg_role, bg_role in EXEMPT_PAIRS[palette.name]:
                fg = WHITE if fg_role is None else getattr(palette, fg_role)
                value = contrast(fg, getattr(palette, bg_role))
                self.assertLess(
                    value, 4.5,
                    f"{palette.name} 的 {name} 已达 {value:.2f}，"
                    "应移出豁免名单并纳入正式检查")

    def test_primary_button_label_pair_is_the_fixed_one(self):
        """白字压在亮蓝 #0A84FF 上只有 3.65，所以按钮改用 ACCENT_FILL。"""
        for palette in (theme.LIGHT, theme.DARK):
            self.assertGreaterEqual(contrast(WHITE, palette.ACCENT_FILL), 4.5)
            self.assertNotEqual(palette.ACCENT_FILL, palette.ACCENT,
                                "按钮填充色不应退回亮蓝 ACCENT")

    def test_contrast_helper_matches_known_values(self):
        """算法自检：黑白 21:1，同色 1:1。"""
        self.assertAlmostEqual(contrast("#000000", "#FFFFFF"), 21.0, places=1)
        self.assertAlmostEqual(contrast("#123456", "#123456"), 1.0, places=6)

    def test_every_pair_role_exists(self):
        """排版写错角色名会静默取到 None，这里让它在拼写阶段就炸掉。"""
        roles = set(theme.LIGHT.__dataclass_fields__)
        for pairs in (TEXT_PAIRS, SECONDARY_PAIRS):
            for name, fg_role, bg_role, _minimum in pairs:
                for role in (fg_role, bg_role):
                    if role is not None:
                        self.assertIn(role, roles, f"{name} 引用了不存在的角色 {role}")


if __name__ == "__main__":
    unittest.main()
