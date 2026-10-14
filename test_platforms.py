"""按平台配置的测试：链接→平台映射，以及 Cookie 覆盖的优先级与界面写入。

优先级是本批最容易出错的地方，规则必须明确：
该平台的覆盖 > 全局 cookies.txt > 全局浏览器；覆盖值 "none" 表示该平台
明确不使用 Cookie。
"""

import os
import tempfile
from unittest.mock import Mock

from app import browser_cookies, gui, platforms, settings as settings_mod


def _reset():
    browser_cookies.set_browser("")
    browser_cookies.set_cookie_file("")
    browser_cookies.set_platform_overrides({})


def test_detect_maps_every_supported_platform():
    cases = {
        "https://www.youtube.com/watch?v=x": "youtube",
        "https://youtu.be/x": "youtube",
        "https://x.com/a/status/1": "x",
        "https://twitter.com/a/status/1": "x",
        "https://www.instagram.com/p/abc/": "instagram",
        "https://v.douyin.com/abc/": "douyin",
        "https://www.bilibili.com/video/BV1": "bilibili",
        "https://b23.tv/abc": "bilibili",
        "https://weibo.com/1/Abc": "weibo",
        "https://m.weibo.cn/status/123": "weibo",
    }
    for url, expected in cases.items():
        assert platforms.detect(url) == expected, url
    assert platforms.detect("https://example.com/x") is None
    assert platforms.detect("") is None
    # GUI 的同名函数必须与共享映射一致（历史调用点仍在用它）
    for url in cases:
        assert gui.detect_platform(url) == platforms.detect(url)


def test_platform_override_wins_over_global_sources():
    _reset()
    try:
        # 只设全局浏览器
        browser_cookies.set_browser("chrome")
        assert browser_cookies.ytdlp_options("instagram") == {
            "cookiesfrombrowser": ("chrome",)}
        assert browser_cookies.ytdlp_options() == {"cookiesfrombrowser": ("chrome",)}

        # 平台覆盖优先
        browser_cookies.set_platform_overrides({"instagram": "firefox"})
        assert browser_cookies.ytdlp_options("instagram") == {
            "cookiesfrombrowser": ("firefox",)}
        assert browser_cookies.ytdlp_options("weibo") == {
            "cookiesfrombrowser": ("chrome",)}

        # 覆盖值 none：该平台不带 Cookie，即使全局有 cookies.txt
        browser_cookies.set_platform_overrides({"youtube": browser_cookies.PLATFORM_NONE})
        handle = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        handle.write(b"# Netscape HTTP Cookie File\n")
        handle.close()
        try:
            browser_cookies.set_cookie_file(handle.name)
            assert browser_cookies.ytdlp_options("youtube") == {}
            assert browser_cookies.enabled("youtube") is False
            assert browser_cookies.gallery_dl_cookies("youtube") is None
            # 未覆盖的平台仍用全局 cookies.txt
            assert browser_cookies.ytdlp_options("weibo") == {"cookiefile": handle.name}
            assert browser_cookies.gallery_dl_cookies("weibo") == handle.name
        finally:
            os.unlink(handle.name)
    finally:
        _reset()


def test_platform_overrides_are_cleaned():
    _reset()
    try:
        browser_cookies.set_platform_overrides({
            "  X  ": "  Chrome ",     # 大小写与空白都被规整
            "instagram": "none",
            "bad": 123,               # 非字符串值忽略
            5: "chrome",              # 非字符串键忽略
            "empty": "",              # 空值忽略
        })
        assert browser_cookies.platform_overrides() == {"x": "chrome", "instagram": "none"}
        # 传入非 dict 不应崩，且会清空
        browser_cookies.set_platform_overrides("not-a-dict")
        assert browser_cookies.platform_overrides() == {}
    finally:
        _reset()


def test_platform_cookie_change_writes_only_non_defaults():
    """设置页写入：选「跟随全局」时应把该平台从配置里删掉，保持文件干净。"""
    _reset()
    saved = {}
    original_save = settings_mod.save
    settings_mod.save = lambda data: saved.update(data)
    try:
        page = Mock()
        page.app.settings = {"platform_cookies": {}}
        page._platform_cookie_options = [
            ("跟随全局", ""), ("不使用", browser_cookies.PLATFORM_NONE),
            ("Chrome", "chrome"),
        ]
        page._platform_vars = {
            "instagram": Mock(get=lambda: "Chrome"),
            "youtube": Mock(get=lambda: "不使用"),
        }

        gui.SettingsPage._on_platform_cookie_change(page, "instagram")
        assert page.app.settings["platform_cookies"] == {"instagram": "chrome"}
        # 引擎侧同步生效
        assert browser_cookies.ytdlp_options("instagram") == {
            "cookiesfrombrowser": ("chrome",)}

        gui.SettingsPage._on_platform_cookie_change(page, "youtube")
        assert page.app.settings["platform_cookies"] == {
            "instagram": "chrome", "youtube": browser_cookies.PLATFORM_NONE}

        # 改回「跟随全局」→ 该键被移除
        page._platform_vars["instagram"].get = lambda: "跟随全局"
        gui.SettingsPage._on_platform_cookie_change(page, "instagram")
        assert page.app.settings["platform_cookies"] == {
            "youtube": browser_cookies.PLATFORM_NONE}
        assert "platform_cookies" in saved
    finally:
        settings_mod.save = original_save
        _reset()


def test_unsupported_sites_are_named_and_boundary_safe():
    """常见但不支持的平台要能报出名字；域名匹配必须按边界，不能子串误判。"""
    cases = {
        "https://weixin.qq.com/sph/AhMkUsjhOF": ("platform_wechat_channels", False),
        "https://channels.weixin.qq.com/finder-preview/pages/sph?id=x":
            ("platform_wechat_channels", False),
        "https://mp.weixin.qq.com/s/abc": ("platform_wechat_article", False),
        "https://www.xiaohongshu.com/explore/abc": ("platform_xiaohongshu", True),
        "https://xhslink.com/abc": ("platform_xiaohongshu", True),
        "https://v.kuaishou.com/abc": ("platform_kuaishou", False),
        "https://www.tiktok.com/@a/video/1": ("platform_tiktok", True),
        "https://vt.tiktok.com/abc/": ("platform_tiktok", True),
    }
    for url, expected in cases.items():
        assert platforms.unsupported_site(url) == expected, url
    # 边界：相似域名不算；普通 weixin.qq.com 链接（非 /sph）也不当视频号
    for url in ("https://notweixin.qq.com/sph/x",
                "https://weixin.qq.com/other/x",
                "https://example.com/x",
                "https://www.youtube.com/watch?v=x",
                ""):
        assert platforms.unsupported_site(url) is None, url
    # 已支持的平台绝不能被当成「不支持」
    for url in cases:
        assert platforms.detect(url) is None


if __name__ == "__main__":
    test_detect_maps_every_supported_platform()
    test_platform_override_wins_over_global_sources()
    test_platform_overrides_are_cleaned()
    test_platform_cookie_change_writes_only_non_defaults()
    test_unsupported_sites_are_named_and_boundary_safe()
    print("PASS: 链接→平台映射、按平台 Cookie 优先级、不支持平台的识别")
