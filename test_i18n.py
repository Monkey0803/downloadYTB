"""本地化资源完整性测试：五种语言均必须提供每个界面文案。"""

from app import i18n


EXPECTED_LANGUAGES = {"zh_CN", "zh_TW", "en", "ko", "ja"}


def main():
    actual = {code for code, _ in i18n.LANGUAGES}
    assert actual == EXPECTED_LANGUAGES

    for key, translations in i18n.T.items():
        missing = EXPECTED_LANGUAGES - set(translations)
        assert not missing, f"{key} missing translations: {sorted(missing)}"

    for code in sorted(EXPECTED_LANGUAGES):
        i18n.set_language(code)
        assert i18n.tr("app_title")
        assert i18n.tr("downloading_percent", percent=12.3,
                       speed="1 MB/s", eta="2s")
        assert i18n.tr("error_operation", message="test")

    i18n.set_language("zh_CN")
    print(f"PASS: {len(i18n.T)} keys cover {len(EXPECTED_LANGUAGES)} languages")


if __name__ == "__main__":
    main()
