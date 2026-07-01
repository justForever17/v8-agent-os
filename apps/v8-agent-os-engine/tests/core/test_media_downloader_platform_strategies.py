from core.tools.media_downloader import (
    _canonicalize_platform_url,
    _load_platform_strategies,
    _platform_from_url,
    _resolve_platform_profile,
)


def test_platform_strategy_json_covers_major_share_platforms() -> None:
    strategies = _load_platform_strategies()
    platforms = strategies.get("platforms") or {}

    for platform in ("youtube", "x", "tiktok", "instagram", "douyin", "doubao", "jimeng"):
        assert platform in platforms
        assert isinstance(platforms[platform].get("profile"), dict)

    assert "t.co" in strategies["global"]["shortlinkHosts"]
    assert "vm.tiktok.com" in strategies["global"]["shortlinkHosts"]


def test_platform_detection_uses_json_host_rules() -> None:
    assert _platform_from_url("https://youtu.be/dQw4w9WgXcQ?si=abc") == "youtube"
    assert _platform_from_url("https://x.com/example/status/1234567890?s=20") == "x"
    assert _platform_from_url("https://fxtwitter.com/example/status/1234567890/video/1") == "x"
    assert _platform_from_url("https://vm.tiktok.com/ZMabcdef/") == "tiktok"
    assert _platform_from_url("https://www.instagram.com/reel/ABC123/?igsh=xyz") == "instagram"


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
