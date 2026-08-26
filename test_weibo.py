"""微博 URL、视频和图片/GIF 解析的快速回归测试。

图片解析使用假会话，避免单元测试依赖微博实时内容；真实微博视频/图片样例
仍可通过 test_images.py 或手动在应用中验证。
"""

from unittest.mock import Mock

from app import images
from app import i18n
from app.gui import ImagePanel, detect_platform, extract_supported_url, is_weibo_video_url


def test_urls():
    urls = [
        "https://weibo.com/7827771738/N4xlMvjhI",
        "https://m.weibo.cn/status/5265462252536899",
        "https://video.weibo.com/show?fid=1034:4967272104787984",
    ]
    for url in urls:
        assert extract_supported_url(url) == url
        assert detect_platform(url) == "weibo"


def test_weibo_media_type_is_detected_from_url():
    assert not is_weibo_video_url("https://weibo.com/2054300185/Re75Mb3AE")
    assert is_weibo_video_url("https://weibo.com/tv/show/1034:123")
    assert is_weibo_video_url("https://video.weibo.com/show?fid=1034:123")


def test_weibo_short_status_id_falls_back_to_base62_when_ytdlp_fails():
    assert images._weibo_status_id(
        "https://weibo.com/7042073622/Rf8KP9bMU", None) == "5336085752190268"


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def get(self, url, **kwargs):
        if "statuses/show" in url:
            return _Response({
                "id": 123,
                "mblogid": "AbCd",
                "text_raw": "微博图片测试",
                "user": {"screen_name": "测试用户"},
                "pic_ids": ["pic1", "pic2"],
            })
        return _Response({})


class _YDL:
    def __init__(self, options):
        self.cookiejar = []

    def extract_info(self, url, download=False):
        return {"id": "123"}


class _FailingYDL(_YDL):
    def extract_info(self, url, download=False):
        raise RuntimeError("旧版提取器无法解析页面 JSON")


def test_weibo_video_only_post_survives_ytdlp_parse_failure():
    class VideoSession(_Session):
        def get(self, url, **kwargs):
            if "statuses/show" in url:
                return _Response({
                    "id": 5336085752190268,
                    "mblogid": "Rf8KP9bMU",
                    "text_raw": "纯视频微博",
                    "user": {"screen_name": "测试用户"},
                    "pic_ids": [],
                    "page_info": {
                        "object_type": "video",
                        "media_info": {
                            "playback_list": [{
                                "play_info": {
                                    "height": 1080,
                                    "url": "https://f.video.weibocdn.com/demo.mp4",
                                },
                            }],
                            "big_pic_info": {
                                "pic_middle": {
                                    "url": "https://wx4.sinaimg.cn/or360/demo.jpg",
                                },
                            },
                        },
                    },
                })
            raise AssertionError(f"unexpected request: {url}")

    old_ydl = images.yt_dlp.YoutubeDL
    old_session = images._weibo_session
    try:
        images.yt_dlp.YoutubeDL = _FailingYDL
        images._weibo_session = lambda ydl: VideoSession()
        post = images.probe_images("https://weibo.com/7042073622/Rf8KP9bMU")
    finally:
        images.yt_dlp.YoutubeDL = old_ydl
        images._weibo_session = old_session

    assert post.has_video
    assert [(item.media_type, item.extension, item.filename) for item in post.items] == [
        ("video", "mp4", "Rf8KP9bMU_video_1"),
    ]
    assert post.items[0].thumb_url == "https://wx4.sinaimg.cn/or360/demo.jpg"


def test_weibo_visitor_fallback_uses_fixed_referer():
    class VisitorResponse:
        def __init__(self, url="", text=""):
            self.url = url
            self.text = text

        def raise_for_status(self):
            return None

        def close(self):
            return None

    class VisitorSession:
        def __init__(self):
            self.headers = {"User-Agent": "Chrome/146.0.0.0"}
            self.status_requests = 0
            self.referers = []

        def get(self, url, **kwargs):
            self.referers.append(kwargs.get("headers", {}).get("Referer"))
            if "statuses/show" in url:
                self.status_requests += 1
                if self.status_requests == 1:
                    return VisitorResponse("https://passport.weibo.com/visitor/visitor")
                return _Response({"id": 123})
            return VisitorResponse()

        def post(self, url, **kwargs):
            self.referers.append(kwargs.get("headers", {}).get("Referer"))
            return VisitorResponse(text=(
                'window.gen_callback && gen_callback({"data":'
                '{"tid":"guest","new_tid":false,"confidence":90}});'))

    session = VisitorSession()
    assert images._weibo_status_data(session, "123") == {"id": 123}
    assert session.referers == [None, "https://weibo.com/", "https://weibo.com/", None]


def test_non_weibo_hostname_is_not_routed_to_weibo_parser():
    assert images._is_weibo_url("https://weibo.com/7042073622/Rf8KP9bMU")
    assert images._is_weibo_url("https://m.weibo.cn/status/123")
    assert not images._is_weibo_url("https://notweibo.com/7042073622/Rf8KP9bMU")
    assert not images._is_weibo_url("https://example.com/?next=weibo.com")


def test_weibo_images_keep_gif_and_batch_items():
    old_ydl = images.yt_dlp.YoutubeDL
    old_session = images._weibo_session
    old_type = images._weibo_image_type
    try:
        images.yt_dlp.YoutubeDL = _YDL
        images._weibo_session = lambda ydl: _Session()
        images._weibo_image_type = lambda session, url: (
            "gif" if url.endswith("pic1.jpg") else "jpg")
        post = images.probe_images("https://m.weibo.cn/status/123")
    finally:
        images.yt_dlp.YoutubeDL = old_ydl
        images._weibo_session = old_session
        images._weibo_image_type = old_type

    assert post.author == "测试用户"
    assert post.title == "微博图片测试"
    assert [item.extension for item in post.items] == ["gif", "jpg"]
    assert [item.filename for item in post.items] == ["AbCd_1", "AbCd_2"]


def test_weibo_mixed_media_keeps_live_photo_pair_name():
    class MixedSession(_Session):
        def get(self, url, **kwargs):
            if "statuses/show" in url:
                return _Response({
                    "id": 123,
                    "mblogid": "Live1",
                    "text_raw": "微博 Live Photo",
                    "user": {"screen_name": "测试用户"},
                    "pic_ids": ["pic1"],
                    "pic_infos": {
                        "pic1": {
                            "original": {"url": "https://wx1.sinaimg.cn/large/pic1.jpg"},
                            "thumbnail": {"url": "https://wx1.sinaimg.cn/mw690/pic1.jpg"},
                        },
                    },
                    "mix_media_info": {
                        "items": [{
                            "type": "video",
                            "data": {"media_info": {
                                "mp4_hd_url": "https://f.us.sinaimg.cn/live.mp4",
                            }},
                        }],
                    },
                })
            return _Response({})

    old_ydl = images.yt_dlp.YoutubeDL
    old_session = images._weibo_session
    old_type = images._weibo_image_type
    try:
        images.yt_dlp.YoutubeDL = _YDL
        images._weibo_session = lambda ydl: MixedSession()
        images._weibo_image_type = lambda session, url: "jpg"
        post = images.probe_images("https://m.weibo.cn/status/123")
    finally:
        images.yt_dlp.YoutubeDL = old_ydl
        images._weibo_session = old_session
        images._weibo_image_type = old_type

    assert post.has_video
    assert [(item.media_type, item.extension, item.filename) for item in post.items] == [
        ("image", "jpg", "Live1_1"),
        ("video", "mp4", "Live1_1"),
    ]
    assert post.items[1].thumb_url == post.items[0].thumb_url


def test_weibo_gif_mp4_keeps_both_formats_with_shared_thumbnail():
    class GifSession(_Session):
        def get(self, url, **kwargs):
            if "statuses/show" in url:
                return _Response({
                    "id": 123,
                    "mblogid": "Gif1",
                    "text_raw": "微博动图",
                    "user": {"screen_name": "测试用户"},
                    "pic_ids": ["gif1"],
                    "pic_infos": {
                        "gif1": {
                            "type": "gif",
                            "original": {
                                "url": "https://wx1.sinaimg.cn/large/gif1.gif",
                            },
                            "thumbnail": {
                                "url": "https://wx1.sinaimg.cn/wap180/gif1.gif",
                            },
                            "video": (
                                "https://g.us.sinaimg.cn/gif1.mp4"
                                "?label=gif_mp4"
                            ),
                        },
                    },
                })
            raise AssertionError(f"unexpected request: {url}")

    old_ydl = images.yt_dlp.YoutubeDL
    old_session = images._weibo_session
    try:
        images.yt_dlp.YoutubeDL = _YDL
        images._weibo_session = lambda ydl: GifSession()
        post = images.probe_images("https://m.weibo.cn/status/123")
    finally:
        images.yt_dlp.YoutubeDL = old_ydl
        images._weibo_session = old_session

    assert [(item.media_type, item.extension, item.filename) for item in post.items] == [
        ("image", "gif", "Gif1_1"),
        ("video", "mp4", "Gif1_1"),
    ]
    assert post.items[1].thumb_url == post.items[0].thumb_url


def test_weibo_mixed_media_renders_after_probe():
    class Button:
        def set_text(self, text):
            self.text = text

    post = images.ImagePost(
        url="https://weibo.com/1/Test",
        title="mixed",
        author="tester",
        items=[
            images.ImageItem("https://example.com/a.jpg", "", 1),
            images.ImageItem(
                "https://example.com/a.mp4", "", 2,
                extension="mp4", media_type="video"),
        ],
    )
    panel = Mock()
    panel.download_btn = Button()
    panel.app.f_small = None

    ImagePanel._probe_done(panel, post)

    items = panel.picker.set_items.call_args.args[0]
    assert len(items) == 2
    assert not any(item["selected"] for item in items)
    panel.all_var.set.assert_called_once_with(False)
    assert panel.download_btn.text == i18n.tr("download_selected_media")
    panel._on_selection_change.assert_called_once_with()
    panel._load_thumbnails.assert_called_once_with(post)


if __name__ == "__main__":
    test_urls()
    test_weibo_media_type_is_detected_from_url()
    test_weibo_short_status_id_falls_back_to_base62_when_ytdlp_fails()
    test_weibo_video_only_post_survives_ytdlp_parse_failure()
    test_weibo_visitor_fallback_uses_fixed_referer()
    test_non_weibo_hostname_is_not_routed_to_weibo_parser()
    test_weibo_images_keep_gif_and_batch_items()
    test_weibo_mixed_media_keeps_live_photo_pair_name()
    test_weibo_gif_mp4_keeps_both_formats_with_shared_thumbnail()
    test_weibo_mixed_media_renders_after_probe()
    print("PASS: Weibo URL and image/GIF parsing")
