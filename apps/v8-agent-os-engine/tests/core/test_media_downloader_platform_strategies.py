from core.tools.media_downloader import (
    _canonicalize_platform_url,
    _choose_media_candidate,
    _cookie_retry_source,
    _extract_media_urls_from_text,
    _guess_kind_from_url,
    _infer_media_quality,
    _load_platform_strategies,
    _launch_chromium_browser,
    _looks_like_direct_media,
    _looks_like_platform_direct_media,
    _platform_from_url,
    _resolve_platform_profile,
    _resolve_platform_share_page,
    _should_retry_with_browser_cookies,
    _yt_dlp_format_selector,
    download_media_for_vision,
)


X_DIRECT_VIDEO_720P_URL = "https://video.twimg.com/amplify_video/2071802906408267777/vid/avc1/736x720/PsvYsY2PDz-jlCby.mp4?tag=28"
X_DIRECT_VIDEO_360P_URL = "https://video.twimg.com/amplify_video/2071802906408267777/vid/avc1/368x360/skpTWGI2yGKulm4k.mp4?tag=28"
X_DIRECT_VIDEO_270P_URL = "https://video.twimg.com/amplify_video/2071802906408267777/vid/avc1/276x270/ehCFr0y6FmwIbr4o.mp4?tag=28"
TIKTOK_PROXY_LOW_BITRATE_URL = (
    "https://snappdown.com/api/proxy/media?url=https%3A%2F%2Fv16-webapp-prime.us.tiktok.com%2Fvideo%2Ftos%2Falisg%2F"
    "tos-alisg-pve-0037c001%2Flow%2F%3Fbt%3D529%26mime_type%3Dvideo_mp4&platform=tiktok"
)
TIKTOK_PROXY_HIGH_BITRATE_URL = (
    "https://snappdown.com/api/proxy/media?url=https%3A%2F%2Fv16-webapp-prime.us.tiktok.com%2Fvideo%2Ftos%2Falisg%2F"
    "tos-alisg-pve-0037c001%2Fhigh%2F%3Fbt%3D1223%26mime_type%3Dvideo_mp4&platform=tiktok"
)
YOUTUBE_VIDEO_ONLY_720P_URL = "https://rr4---sn-ab5l6ny7.googlevideo.com/videoplayback?itag=136&mime=video%2Fmp4"
YOUTUBE_AUDIO_ONLY_URL = "https://rr4---sn-ab5l6ny7.googlevideo.com/videoplayback?itag=139&mime=audio%2Fmp4"
XIAOHONGSHU_DIRECT_VIDEO_URL = (
    "https://sns-video-v3.xhscdn.com/stream/1/110/258/"
    "01ea38f8ef2f7bf0010370019eee8cd424_258.mp4?sign=d93ad1b56f530509375b71feebe04b0f&t=6a4908cc"
)
BILIBILI_DIRECT_VIDEO_URL = (
    "https://upos-sz-mirrorhw.bilivideo.com/upgcxcode/52/58/39281295852/"
    "39281295852-1-192.mp4?platform=html5&deadline=1782875936"
)
KUAISHOU_DIRECT_VIDEO_URL = (
    "https://tymov2.a.kwimgs.com/upic/2026/06/01/17/"
    "BMjAyNjA2MDExNzM3MTVfNDU2NTcwMzQxN18xOTc2MTA4Mzc5MjBfMV8z_b_Bcc968158a6f4fb4a13f0bb4282f22886.mp4"
    "?clientCacheKey=3xzixyxrcstit9u_b.mp4&tt=b"
)


def test_platform_strategy_json_covers_major_share_platforms() -> None:
    strategies = _load_platform_strategies()
    platforms = strategies.get("platforms") or {}

    for platform in (
        "youtube",
        "x",
        "tiktok",
        "instagram",
        "douyin",
        "doubao",
        "jimeng",
        "xiaohongshu",
        "bilibili",
        "kuaishou",
    ):
        assert platform in platforms
        assert isinstance(platforms[platform].get("profile"), dict)

    assert "xhslink.com" in strategies["global"]["shortlinkHosts"]
    assert "b23.tv" in strategies["global"]["shortlinkHosts"]
    assert "v.kuaishou.com" in strategies["global"]["shortlinkHosts"]
    assert "t.co" in strategies["global"]["shortlinkHosts"]
    assert "vm.tiktok.com" in strategies["global"]["shortlinkHosts"]


def test_platform_detection_uses_json_host_rules() -> None:
    assert _platform_from_url("https://youtu.be/dQw4w9WgXcQ?si=abc") == "youtube"
    assert _platform_from_url("https://x.com/example/status/1234567890?s=20") == "x"
    assert _platform_from_url("https://fxtwitter.com/example/status/1234567890/video/1") == "x"
    assert _platform_from_url(X_DIRECT_VIDEO_720P_URL) == "x"
    assert _platform_from_url("https://vm.tiktok.com/ZMabcdef/") == "tiktok"
    assert _platform_from_url("https://v16-webapp-prime.us.tiktok.com/video/tos/alisg/example/?mime_type=video_mp4") == "tiktok"
    assert _platform_from_url(YOUTUBE_VIDEO_ONLY_720P_URL) == "youtube"
    assert _platform_from_url("https://www.instagram.com/reel/ABC123/?igsh=xyz") == "instagram"
    assert _platform_from_url("http://xhslink.com/o/8mxNz3OKlKo") == "xiaohongshu"
    assert _platform_from_url(XIAOHONGSHU_DIRECT_VIDEO_URL) == "xiaohongshu"
    assert _platform_from_url("https://b23.tv/rPjLwZR") == "bilibili"
    assert _platform_from_url(BILIBILI_DIRECT_VIDEO_URL) == "bilibili"
    assert _platform_from_url("https://v.kuaishou.com/Kd1wWjJF") == "kuaishou"
    assert _platform_from_url(KUAISHOU_DIRECT_VIDEO_URL) == "kuaishou"


def test_platform_profiles_are_loaded_from_strategy_json() -> None:
    platform, profile = _resolve_platform_profile("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert platform == "youtube"
    assert profile["defaultReferer"] == "https://www.youtube.com/"
    assert profile["defaultPrefer"] == "video"


def test_youtube_canonicalization_preserves_core_query_and_drops_tracking() -> None:
    canonical, metadata = _canonicalize_platform_url(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc&utm_source=share&list=PL123&t=42s"
    )

    assert metadata["changed"] is True
    assert canonical == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&t=42s"


def test_x_canonicalization_normalizes_wrapper_and_status_suffix() -> None:
    canonical, metadata = _canonicalize_platform_url(
        "https://fxtwitter.com/example/status/1234567890/video/1?s=20"
    )

    assert metadata["changed"] is True
    assert canonical == "https://x.com/example/status/1234567890"


def test_instagram_canonicalization_strips_share_tracking() -> None:
    canonical, metadata = _canonicalize_platform_url("https://www.instagram.com/reel/ABC123/?igsh=xyz&utm_source=ig_web_copy_link")

    assert metadata["changed"] is True
    assert canonical == "https://www.instagram.com/reel/ABC123/"


def test_canonicalization_preserves_signed_direct_media_query() -> None:
    direct = "https://v9-dy.ixigua.com/video/tos/cn/tos-cn-ve-15/abc/?mime_type=video_mp4&dy_q=1781535529&l=signed"

    canonical, metadata = _canonicalize_platform_url(direct)

    assert metadata["strategy"] == "skip_direct_media_or_empty"
    assert canonical == direct


def test_xiaohongshu_discovery_page_is_not_misclassified_as_direct_media() -> None:
    page_url = (
        "https://www.xiaohongshu.com/discovery/item/6a38f8ef000000000f030354"
        "?app_platform=android&type=video&xsec_token=sample"
    )

    assert not _looks_like_direct_media(page_url)


def test_cn_platform_direct_media_hints_recognize_observed_video_urls() -> None:
    assert _looks_like_platform_direct_media("xiaohongshu", XIAOHONGSHU_DIRECT_VIDEO_URL)
    assert _looks_like_platform_direct_media("bilibili", BILIBILI_DIRECT_VIDEO_URL)
    assert _looks_like_platform_direct_media("kuaishou", KUAISHOU_DIRECT_VIDEO_URL)


def test_quality_score_prefers_highest_x_resolution() -> None:
    selected = _choose_media_candidate(
        [
            {"url": X_DIRECT_VIDEO_270P_URL, "source": "x_network_capture", "kind": "video"},
            {"url": X_DIRECT_VIDEO_720P_URL, "source": "x_network_capture", "kind": "video"},
            {"url": X_DIRECT_VIDEO_360P_URL, "source": "x_network_capture", "kind": "video"},
        ],
        prefer="video",
    )

    assert _infer_media_quality(X_DIRECT_VIDEO_720P_URL)["height"] == 720
    assert selected is not None
    assert selected["url"] == X_DIRECT_VIDEO_720P_URL


def test_nested_tiktok_proxy_candidates_prefer_higher_bitrate() -> None:
    hits = _extract_media_urls_from_text(
        f"{TIKTOK_PROXY_LOW_BITRATE_URL}\n{TIKTOK_PROXY_HIGH_BITRATE_URL}",
        source="tiktok_html_scan",
    )
    selected = _choose_media_candidate(hits, prefer="video")

    assert any("bt=529" in hit["url"] for hit in hits)
    assert any("bt=1223" in hit["url"] for hit in hits)
    assert selected is not None
    assert "bt=1223" in selected["url"]


def test_youtube_direct_streams_keep_video_and_audio_separate() -> None:
    assert _guess_kind_from_url(YOUTUBE_VIDEO_ONLY_720P_URL) == "video"
    assert _guess_kind_from_url(YOUTUBE_AUDIO_ONLY_URL) == "audio"
    assert _infer_media_quality(YOUTUBE_VIDEO_ONLY_720P_URL)["height"] == 720
    assert _yt_dlp_format_selector("video", ffmpeg_available=True) == (
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"
    )
    assert _yt_dlp_format_selector("video", ffmpeg_available=False) == "best[ext=mp4]/best"


def test_youtube_cookie_retry_is_explicitly_strategy_gated() -> None:
    _, youtube_profile = _resolve_platform_profile("https://youtube.com/shorts/dnhV3HKzrqo")

    assert _should_retry_with_browser_cookies("Sign in to confirm you’re not a bot. Use --cookies-from-browser")
    assert _cookie_retry_source(youtube_profile) == "chrome"
    assert _cookie_retry_source({"retryOrder": ["no_cookie", "cookie_if_needed"]}) == ""


def test_cn_platform_candidate_weights_prefer_observed_direct_video_urls() -> None:
    generic_video = "https://cdn.example.com/video.mp4"
    low_value_cover = "https://sns-webpic-qc.xhscdn.com/cover.jpg"

    for platform, direct_url in (
        ("xiaohongshu", XIAOHONGSHU_DIRECT_VIDEO_URL),
        ("bilibili", BILIBILI_DIRECT_VIDEO_URL),
        ("kuaishou", KUAISHOU_DIRECT_VIDEO_URL),
    ):
        selected = _choose_media_candidate(
            [
                {"url": generic_video, "source": "generic_network_capture", "kind": "video"},
                {"url": low_value_cover, "source": f"{platform}_network_capture", "kind": "image"},
                {"url": direct_url, "source": f"{platform}_network_capture", "kind": "video"},
            ],
            prefer="video",
        )

        assert selected is not None
        assert selected["url"] == direct_url


def test_browser_launch_falls_back_to_system_channel(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeChromium:
        def launch(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("bundled browser missing")
            return "browser"

    class FakePlaywright:
        chromium = FakeChromium()

    monkeypatch.setattr(
        "core.tools.media_downloader._system_chromium_executable_candidates",
        lambda: [],
    )

    browser, launch_mode, errors = _launch_chromium_browser(FakePlaywright())

    assert browser == "browser"
    assert launch_mode == "channel:chrome"
    assert errors and "bundled browser missing" in errors[0]
    assert calls == [{"headless": True}, {"headless": True, "channel": "chrome"}]


def test_bilibili_playurl_api_resolves_observed_mp4_without_browser(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeRequests:
        def get(self, url, **kwargs):
            if url.endswith("/x/web-interface/view"):
                assert kwargs["params"] == {"bvid": "BV1tBjx6cExc"}
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "bvid": "BV1tBjx6cExc",
                            "aid": 116786368421588,
                            "cid": 39281295852,
                        },
                    }
                )
            if url.endswith("/x/player/playurl"):
                assert kwargs["params"]["bvid"] == "BV1tBjx6cExc"
                assert kwargs["params"]["cid"] == "39281295852"
                assert kwargs["params"]["platform"] == "html5"
                return FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "durl": [
                                {
                                    "url": BILIBILI_DIRECT_VIDEO_URL,
                                }
                            ]
                        },
                    }
                )
            raise AssertionError(url)

    monkeypatch.setattr(
        "core.tools.media_downloader._load_requests",
        lambda: (FakeRequests(), None),
    )

    result = _resolve_platform_share_page(
        "https://www.bilibili.com/video/BV1tBjx6cExc/",
        platform="bilibili",
        prefer="video",
    )

    assert result is not None
    assert result["resolved"] is True
    assert result["strategy"] == "bilibili_playurl_api"
    assert result["downloadUrl"] == BILIBILI_DIRECT_VIDEO_URL


def test_kuaishou_graphql_api_resolves_observed_mp4_without_browser(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "visionVideoDetail": {
                        "photo": {
                            "photoUrl": KUAISHOU_DIRECT_VIDEO_URL,
                            "manifest": {
                                "adaptationSet": [
                                    {
                                        "representation": [
                                            {
                                                "url": KUAISHOU_DIRECT_VIDEO_URL,
                                            }
                                        ]
                                    }
                                ]
                            },
                        }
                    }
                }
            }

    class FakeRequests:
        def post(self, url, **kwargs):
            assert url == "https://www.kuaishou.com/graphql"
            assert kwargs["json"]["variables"]["photoId"] == "3xzixyxrcstit9u"
            return FakeResponse()

    monkeypatch.setattr(
        "core.tools.media_downloader._load_requests",
        lambda: (FakeRequests(), None),
    )

    result = _resolve_platform_share_page(
        "https://www.kuaishou.com/short-video/3xzixyxrcstit9u",
        platform="kuaishou",
        prefer="video",
    )

    assert result is not None
    assert result["resolved"] is True
    assert result["strategy"] == "kuaishou_graphql_api"
    assert result["downloadUrl"] == KUAISHOU_DIRECT_VIDEO_URL


def test_download_media_tool_description_is_agent_actionable() -> None:
    schema = download_media_for_vision.args_schema.model_json_schema()
    description = schema.get("description") or ""
    properties = schema.get("properties") or {}

    assert "pasted social share text" in description
    assert "vision_media_analyzer" in description
    assert "does not perform visual/audio understanding" in description

    assert "Media page/share text" in properties["url"]["description"]
    assert "Select the target media type" in properties["prefer"]["description"]
    assert "does not automatically call vision_media_analyzer" in properties["auto_chain_to_vision"]["description"]
