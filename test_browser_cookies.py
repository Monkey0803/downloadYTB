"""浏览器 Cookie 读取失败的回归测试。

背景：macOS 上浏览器数据目录受 TCC 保护，未获「完全磁盘访问权限」的
打包 App 读取 Chrome 目录会被拒绝；yt-dlp 内部用 os.walk 查找 Cookie
数据库，而 os.walk 默认静默吞掉权限错误，于是抛出把搜索根目录当成
数据库位置的误导性消息：

    ERROR: could not find chrome cookies database in "/.../Google/Chrome"

这里用真实的 yt-dlp 原始消息验证：
1. 该消息被识别为「Cookie 读取失败」而非普通解析失败；
2. 微博图片解析降级为匿名重试，不再整体失败；
3. 匿名也失败时才报错，且给出可操作的指引（不是 yt-dlp 的原始消息）；
4. cookies.txt 优先于浏览器 Cookie。
"""

import os
import tempfile
from unittest.mock import Mock

from app import browser_cookies, core, i18n, images
from app.gui import ImagePanel

# 截图中的真实报错原文（yt-dlp yt_dlp/cookies.py 抛出的 FileNotFoundError）。
CHROME_DB_MISSING = (
    'ERROR: could not find chrome cookies database in '
    '"/Users/x/Library/Application Support/Google/Chrome"'
)
COOKIE_TXT_MISSING = "ERROR: unable to open cookie database: /tmp/missing.txt"


def _use_browser(name):
    images.set_browser_cookies(name)
    core.set_browser_cookies(name)


def _use_cookie_file(path):
    images.set_cookie_file(path)
    core.set_cookie_file(path)


def _reset_cookie_state():
    _use_browser("")
    _use_cookie_file("")


def test_load_error_is_recognised_and_not_over_reported():
    assert browser_cookies.is_load_error(CHROME_DB_MISSING)
    assert browser_cookies.is_load_error(FileNotFoundError(CHROME_DB_MISSING))
    assert browser_cookies.is_load_error(COOKIE_TXT_MISSING)
    # 普通解析/网络错误不能被误判成 Cookie 问题
    assert not browser_cookies.is_load_error("HTTP Error 404: Not Found")
    assert not browser_cookies.is_load_error("Unable to download webpage: timed out")
    assert not browser_cookies.is_load_error(None)


def test_cookie_file_takes_priority_over_browser():
    _reset_cookie_state()
    try:
        _use_browser("chrome")
        assert browser_cookies.ytdlp_options() == {"cookiesfrombrowser": ("chrome",)}
        assert browser_cookies.gallery_dl_cookies() == ["chrome"]

        handle = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        handle.write(b"# Netscape HTTP Cookie File\n")
        handle.close()
        try:
            _use_cookie_file(handle.name)
            # cookies.txt 不读浏览器数据库，优先使用
            assert browser_cookies.ytdlp_options() == {"cookiefile": handle.name}
            assert browser_cookies.gallery_dl_cookies() == handle.name
            assert not browser_cookies.cookie_file_missing()
        finally:
            os.unlink(handle.name)

        # 文件被删除后不再生效：回退到浏览器 Cookie，并可识别为「已配置但不存在」
        assert browser_cookies.cookie_file_missing()
        assert browser_cookies.ytdlp_options() == {"cookiesfrombrowser": ("chrome",)}
        _use_browser("")
        assert browser_cookies.ytdlp_options() == {}
        assert browser_cookies.gallery_dl_cookies() is None
    finally:
        _reset_cookie_state()


class _CookieRejectingYDL:
    """带 Cookie 参数时在构造阶段抛错，模拟无权限读取浏览器数据库。"""

    def __init__(self, options):
        if "cookiesfrombrowser" in options or "cookiefile" in options:
            raise FileNotFoundError(CHROME_DB_MISSING)
        self.options = options
        self.cookiejar = []

    def extract_info(self, url, download=False):
        return {"id": "123"}


class _AlwaysRejectingYDL:
    def __init__(self, options):
        raise FileNotFoundError(CHROME_DB_MISSING)


class _StubSession:
    """微博状态接口的最小假会话。"""

    def get(self, url, **kwargs):
        response = Mock()
        response.status_code = 200
        response.url = url
        response.json.return_value = {
            "id": 123,
            "mblogid": "AbCd",
            "text_raw": "微博图片测试",
            "user": {"screen_name": "测试用户"},
            "pic_ids": ["pic1"],
        }
        response.raise_for_status.return_value = None
        return response


def _probe_weibo_with_ydl(ydl_cls, url="https://m.weibo.cn/status/123"):
    old_ydl = images.yt_dlp.YoutubeDL
    old_session = images._weibo_session
    old_type = images._weibo_image_type
    try:
        images.yt_dlp.YoutubeDL = ydl_cls
        images._weibo_session = lambda ydl: _StubSession()
        images._weibo_image_type = lambda session, url: "jpg"
        return images.probe_images(url)
    finally:
        images.yt_dlp.YoutubeDL = old_ydl
        images._weibo_session = old_session
        images._weibo_image_type = old_type


def test_weibo_images_survive_cookie_load_failure():
    """本次报错的直接回归：Cookie 读不到时图片解析仍应成功。"""
    _reset_cookie_state()
    try:
        _use_browser("chrome")
        post = _probe_weibo_with_ydl(_CookieRejectingYDL)
        assert [item.filename for item in post.items] == ["AbCd_1"]
        # 降级必须如实告知，避免用户以为用的是登录态
        assert post.warning == i18n.tr("cookie_fallback_warning")
    finally:
        _reset_cookie_state()

    # 未启用 Cookie 时不产生任何降级提示
    post = _probe_weibo_with_ydl(_CookieRejectingYDL)
    assert post.warning == ""


def test_weibo_images_report_actionable_error_when_anonymous_also_fails():
    _reset_cookie_state()
    try:
        _use_browser("chrome")
        try:
            # 该 URL 无法从链接推断状态 ID，解析失败会直接上报
            _probe_weibo_with_ydl(_AlwaysRejectingYDL,
                                  "https://weibo.com/about")
        except images.ImageError as exc:
            message = str(exc)
        else:
            raise AssertionError("expected ImageError")
    finally:
        _reset_cookie_state()

    assert message == i18n.tr("error_browser_cookie_unavailable")
    # 不再是 yt-dlp 那条只提目录、无从下手的原始消息
    assert "cookies database" not in message


class _CookieThenAnonymousYDL:
    """带 Cookie 抛错；匿名调用返回可用的视频格式。"""

    def __init__(self, options):
        if "cookiesfrombrowser" in options or "cookiefile" in options:
            raise FileNotFoundError(CHROME_DB_MISSING)
        self.cookiejar = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download=False):
        return {
            "id": "vid1",
            "title": "demo",
            "duration": 10,
            "formats": [
                {"format_id": "137", "ext": "mp4", "vcodec": "avc1",
                 "acodec": "none", "height": 1080, "format_note": "1080p",
                 "tbr": 4000, "filesize": 4_000_000},
                {"format_id": "140", "ext": "m4a", "vcodec": "none",
                 "acodec": "mp4a", "abr": 128, "filesize": 160_000},
            ],
        }


def test_video_probe_falls_back_to_anonymous_on_cookie_failure():
    _reset_cookie_state()
    old_ydl = core.yt_dlp.YoutubeDL
    try:
        _use_browser("chrome")
        core.yt_dlp.YoutubeDL = _CookieThenAnonymousYDL
        info = core.probe("https://www.youtube.com/watch?v=vid1")
        assert info.title == "demo"
        assert info.qualities  # 匿名解析拿到了可用格式
        assert info.warning == i18n.tr("cookie_fallback_warning")
    finally:
        core.yt_dlp.YoutubeDL = old_ydl
        _reset_cookie_state()


def test_wrap_error_marks_cookie_failure_separately():
    err = core._wrap_error(FileNotFoundError(CHROME_DB_MISSING))
    assert getattr(err, "cookie_unavailable", False)
    assert str(err) == i18n.tr("error_browser_cookie_unavailable")
    # 普通错误不应带上该标记
    plain = core._wrap_error(Exception("HTTP Error 404: Not Found"))
    assert not getattr(plain, "cookie_unavailable", False)


def test_image_post_warning_is_shown_in_status_bar():
    post = images.ImagePost(
        url="https://weibo.com/1/Test",
        title="t",
        author="a",
        items=[images.ImageItem("https://example.com/a.jpg", "", 1)],
        warning=i18n.tr("cookie_fallback_warning"),
    )
    panel = Mock()
    panel.download_btn = Mock()
    panel.app.f_small = None

    ImagePanel._probe_done(panel, post)

    panel._set_status.assert_called_once_with(post.warning)


if __name__ == "__main__":
    test_load_error_is_recognised_and_not_over_reported()
    test_cookie_file_takes_priority_over_browser()
    test_weibo_images_survive_cookie_load_failure()
    test_weibo_images_report_actionable_error_when_anonymous_also_fails()
    test_video_probe_falls_back_to_anonymous_on_cookie_failure()
    test_wrap_error_marks_cookie_failure_separately()
    test_image_post_warning_is_shown_in_status_bar()
    print("PASS: 浏览器 Cookie 读取失败的识别、降级与提示")
