from core.tools.media_downloader import (
    _choose_media_candidate,
    _extract_first_url,
    _extract_media_urls_from_json_like,
    _extract_media_urls_from_text,
    _guess_kind_from_url,
    _looks_like_direct_media,
    _looks_like_douyin_direct_media,
    _platform_from_url,
    _resolve_platform_share_page,
)


DOUYIN_SHARE_TEXT = (
    "0.58 复制打开抖音，看看【久恙的作品】无后之徒完整版 "
    "https://v.douyin.com/p_IqPKDrGXQ/ 05/10 :4pm"
)
DOUYIN_DIRECT_VIDEO_URL = (
    "https://v9-dy.ixigua.com/8bf8b38019cbd6f86e10746a024438bf/6a3025b6/"
    "video/tos/cn/tos-cn-ve-15/4ce0358531a346428f92572a1ef0e044/"
    "?a=6383&mime_type=video_mp4&dy_q=1781535529"
)
DOUYIN_PLAYWM_URL = (
    "https://aweme.snssdk.com/aweme/v1/playwm/"
    "?line=0&logo_name=aweme_diversion_search&ratio=720p&video_id=v0d00fg10000d8vdinfog65tq8ghtsbg"
)


def test_douyin_share_text_extracts_shortlink_and_platform() -> None:
    url = _extract_first_url(DOUYIN_SHARE_TEXT)

    assert url == "https://v.douyin.com/p_IqPKDrGXQ/"
    assert _platform_from_url(url) == "douyin"


def test_douyin_direct_media_hints_recognize_ixigua_video_url() -> None:
    assert _guess_kind_from_url(DOUYIN_DIRECT_VIDEO_URL) == "video"
    assert _looks_like_direct_media(DOUYIN_DIRECT_VIDEO_URL)
    assert _looks_like_douyin_direct_media(DOUYIN_DIRECT_VIDEO_URL)


def test_douyin_json_like_extraction_decodes_escaped_direct_url() -> None:
    escaped_url = DOUYIN_DIRECT_VIDEO_URL.replace("/", "\\/").replace("&", "\\u0026")
    hits = _extract_media_urls_from_json_like(
        {"aweme_detail": {"video": {"play_addr": {"url_list": [escaped_url]}}}},
        source="douyin_share_api",
    )

    assert hits == [
        {
            "url": DOUYIN_DIRECT_VIDEO_URL,
            "source": "douyin_share_api",
            "kind": "video",
        }
    ]


def test_douyin_candidate_weight_prefers_direct_video_over_short_generic_mp4() -> None:
    selected = _choose_media_candidate(
        [
            {
                "url": "https://cdn.example.com/a.mp4",
                "source": "generic_network_capture",
                "kind": "video",
            },
            {
                "url": "https://p3-pc.douyinpic.com/img/tos-cn-i-0813/cover.jpeg",
                "source": "douyin_network_capture",
                "kind": "image",
            },
            {
                "url": DOUYIN_DIRECT_VIDEO_URL,
                "source": "douyin_share_api",
                "kind": "video",
            },
        ],
        prefer="video",
    )

    assert selected is not None
    assert selected["url"] == DOUYIN_DIRECT_VIDEO_URL


def test_douyin_mobile_share_text_extracts_playwm_video_without_swallowing_json() -> None:
    html = (
        '{"video":{"play_addr":{"url_list":["'
        + DOUYIN_PLAYWM_URL.replace("/", "\\/").replace("&", "\\u0026")
        + '"]},"cover":{"url_list":["https:\\/\\/p3-sign.douyinpic.com\\/cover.webp?x=1"]}}}'
    )

    hits = _extract_media_urls_from_text(html, source="douyin_mobile_share_page")
    selected = _choose_media_candidate(hits, prefer="video")

    assert any(hit["url"] == DOUYIN_PLAYWM_URL for hit in hits)
    assert all('"}' not in hit["url"] for hit in hits)
    assert selected is not None
    assert selected["url"] == DOUYIN_PLAYWM_URL


def test_douyin_mobile_share_page_resolves_without_browser(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        text = (
            '{"video":{"play_addr":{"url_list":["'
            + DOUYIN_PLAYWM_URL.replace("/", "\\/").replace("&", "\\u0026")
            + '"]}}}'
        )

    class FakeRequests:
        def get(self, url, **kwargs):
            assert url == "https://www.iesdouyin.com/share/video/7655798900835020918/"
            assert kwargs["headers"]["Referer"] == "https://www.douyin.com/"
            return FakeResponse()

    monkeypatch.setattr(
        "core.tools.media_downloader._load_requests",
        lambda: (FakeRequests(), None),
    )

    result = _resolve_platform_share_page(
        "https://www.douyin.com/video/7655798900835020918",
        platform="douyin",
        prefer="video",
    )

    assert result is not None
    assert result["resolved"] is True
    assert result["strategy"] == "douyin_mobile_share_page"
    assert result["downloadUrl"] == DOUYIN_PLAYWM_URL
