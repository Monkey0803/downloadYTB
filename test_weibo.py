"""微博 URL、视频和图片/GIF 解析的快速回归测试。

图片解析使用假会话，避免单元测试依赖微博实时内容；真实微博视频/图片样例
仍可通过 test_images.py 或手动在应用中验证。
"""

from app import images
from app.gui import detect_platform, extract_supported_url, is_weibo_video_url


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


if __name__ == "__main__":
    test_urls()
    test_weibo_media_type_is_detected_from_url()
    test_weibo_images_keep_gif_and_batch_items()
    test_weibo_mixed_media_keeps_live_photo_pair_name()
    print("PASS: Weibo URL and image/GIF parsing")
