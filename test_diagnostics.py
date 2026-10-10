"""诊断报告的内容与隐私测试。

报告会被贴进公开 issue，所以除了「信息够用」，还必须验证「不含个人数据」：
家目录要替换成 ~，保存目录路径不能出现，Cookie 只报来源与状态。
"""

import os

from app import browser_cookies, diagnostics

HOME = os.path.expanduser("~")


def _report(**settings):
    settings.setdefault("save_dirs", {})
    # 固定一个体检结果，避免报告内容随本机环境变化
    probe = browser_cookies.ProbeResult(browser_cookies.PROBE_OK, cookie_count=7)
    return diagnostics.build_report(settings, probe=probe)


def test_report_contains_environment_facts():
    report = _report()
    assert "诊断信息" in report
    for label in ("应用版本", "系统", "Python", "yt-dlp", "gallery-dl",
                  "ffmpeg", "保存目录", "Cookie 来源", "Cookie 状态"):
        assert label in report, label
    assert "可用（读取到 7 条）" in report


def test_report_does_not_leak_personal_paths():
    report = _report(save_dirs={
        "youtube_video": os.path.join(HOME, "secret-dir", "ytb"),
        "x_image": os.path.join(HOME, "another", "x"),
    })
    # 保存目录只报数量，不报路径
    assert "已按下载方式自定义 2 项" in report
    assert "secret-dir" not in report
    assert "another" not in report
    # 家目录被替换成 ~
    assert HOME not in report


def test_report_includes_cookie_failure_detail_but_sanitized():
    detail = f'could not find chrome cookies database in "{HOME}/Library/Application Support/Google/Chrome"'
    probe = browser_cookies.ProbeResult(
        browser_cookies.PROBE_UNREADABLE, detail=detail)
    report = diagnostics.build_report({"save_dirs": {}}, probe=probe)
    assert "Cookie 状态: 不可用" in report
    assert "cookies database" in report      # 保留可排查的关键信息
    assert HOME not in report                # 但已脱敏


def test_report_reports_cookie_source_kinds():
    original_browser = browser_cookies.browser()
    original_file = browser_cookies.cookie_file()
    try:
        browser_cookies.set_browser("chrome")
        browser_cookies.set_cookie_file("")
        assert "Cookie 来源: 浏览器 chrome" in _report()

        browser_cookies.set_browser("")
        browser_cookies.set_cookie_file("/tmp/does-not-exist-cookies.txt")
        assert "cookies.txt（已配置但文件不存在）" in _report()

        browser_cookies.set_cookie_file("")
        assert "Cookie 来源: 未启用" in _report()
    finally:
        browser_cookies.set_browser(original_browser)
        browser_cookies.set_cookie_file(original_file)


def test_last_error_round_trip():
    diagnostics.record_error("解析失败：示例错误")
    assert diagnostics.last_error() == "解析失败：示例错误"
    assert "最近一次错误: 解析失败：示例错误" in _report()

    diagnostics.record_error("")
    assert "最近一次错误: 无" in _report()


if __name__ == "__main__":
    test_report_contains_environment_facts()
    test_report_does_not_leak_personal_paths()
    test_report_includes_cookie_failure_detail_but_sanitized()
    test_report_reports_cookie_source_kinds()
    test_last_error_round_trip()
    print("PASS: 诊断报告内容与隐私")
