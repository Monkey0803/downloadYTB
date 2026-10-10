"""本地化资源完整性测试。

两类检查：
1. 五种语言均必须提供每个已有文案；
2. 源码里 ``i18n.tr("字面量")`` 调用的键必须真的存在于文案表——第 2 类是
   必要的：只查第 1 类时，一个「压根没加进文案表」的键会静默通过，界面
   直接把 key 当文案显示出来（真实踩过一次：字幕卡片标题显示成 subtitles）。
"""

import pathlib
import re

from app import i18n

EXPECTED_LANGUAGES = {"zh_CN", "zh_TW", "en", "ko", "ja"}
ROOT = pathlib.Path(__file__).resolve().parent
# 只统计字面量调用；形如 i18n.tr(label_key) 的动态键无法静态检查
_LITERAL_CALL = re.compile(r'i18n\.tr\(\s*"([A-Za-z0-9_]+)"')


def check_translations_complete():
    actual = {code for code, _ in i18n.LANGUAGES}
    assert actual == EXPECTED_LANGUAGES

    for key, translations in i18n.T.items():
        missing = EXPECTED_LANGUAGES - set(translations)
        assert not missing, f"{key} missing translations: {sorted(missing)}"


def check_every_used_key_exists():
    """源码里用到的字面量键都必须在文案表里。"""
    used = set()
    sources = sorted(ROOT.glob("app/*.py")) + sorted(ROOT.glob("main.py"))
    for path in sources:
        used.update(_LITERAL_CALL.findall(path.read_text(encoding="utf-8")))
    assert used, "没有扫描到任何 i18n.tr 字面量调用，检查规则可能失效了"

    missing = sorted(key for key in used if key not in i18n.T)
    assert not missing, f"这些文案键被使用但未定义（界面会直接显示 key）：{missing}"
    return len(used)


def check_formatting_placeholders():
    for code in sorted(EXPECTED_LANGUAGES):
        i18n.set_language(code)
        assert i18n.tr("app_title")
        assert i18n.tr("downloading_percent", percent=12.3, speed="1 MB/s", eta="2s")
        assert i18n.tr("error_operation", message="test")


def main():
    check_translations_complete()
    used = check_every_used_key_exists()
    check_formatting_placeholders()
    i18n.set_language("zh_CN")
    print(f"PASS: {len(i18n.T)} keys cover {len(EXPECTED_LANGUAGES)} languages; "
          f"{used} literal keys used in source all defined")


if __name__ == "__main__":
    main()
