from __future__ import annotations

from core.v8_link import (
    _build_connection_endpoints,
    default_remote_link_config,
    is_stable_cloudflare_origin,
    normalize_remote_link_config,
)


def test_remote_link_defaults_include_stable_cloudflare_transport() -> None:
    config = default_remote_link_config(admin_base_url="http://127.0.0.1:9528")
    profiles = {item["id"]: item for item in config["transportProfiles"]}

    assert profiles["cloudflare-tunnel"]["kind"] == "cloudflare_tunnel"
    assert profiles["cloudflare-tunnel"]["enabled"] is True


def test_remote_link_normalization_preserves_cloudflare_domain() -> None:
    config = normalize_remote_link_config(
        {
            "activeProfileId": "cloudflare-tunnel",
            "transportProfiles": [
                {
                    "id": "cloudflare-tunnel",
                    "kind": "cloudflare-tunnel",
                    "adminBaseUrl": "https://v8.example.com/api/",
                }
            ],
        }
    )
    profile = next(item for item in config["transportProfiles"] if item["id"] == "cloudflare-tunnel")

    assert profile["kind"] == "cloudflare_tunnel"
    assert profile["adminBaseUrl"] == "https://v8.example.com"


def test_connection_endpoints_cover_ipv4_ipv6_and_remote_domain() -> None:
    remote_link = normalize_remote_link_config(
        {
            "transportProfiles": [
                {
                    "id": "cloudflare-tunnel",
                    "kind": "cloudflare_tunnel",
                    "enabled": True,
                    "adminBaseUrl": "https://v8.example.com",
                }
            ]
        }
    )
    endpoints = _build_connection_endpoints(
        admin_base_url="http://127.0.0.1:9528",
        remote_link=remote_link,
        candidate_ips=[
            {"address": "192.168.1.8", "family": "ipv4", "private": True},
            {"address": "fd12:3456:789a::8", "family": "ipv6", "private": True},
        ],
        tailscale_recommended={},
    )

    by_kind = {item["kind"]: item for item in endpoints}
    assert by_kind["lan"]["baseUrl"] == "http://192.168.1.8:9528"
    assert by_kind["lan_ipv6"]["baseUrl"] == "http://[fd12:3456:789a::8]:9528"
    assert by_kind["cloudflare_tunnel"]["baseUrl"] == "https://v8.example.com"
    assert by_kind["cloudflare_tunnel"]["scope"] == "remote"


def test_quick_tunnel_is_not_a_phone_realtime_endpoint() -> None:
    remote_link = normalize_remote_link_config(
        {
            "activeProfileId": "cloudflare-tunnel",
            "transportProfiles": [
                {
                    "id": "cloudflare-tunnel",
                    "kind": "cloudflare_tunnel",
                    "enabled": True,
                    "adminBaseUrl": "https://temporary.trycloudflare.com",
                }
            ],
        }
    )
    endpoints = _build_connection_endpoints(
        admin_base_url="http://127.0.0.1:9528",
        remote_link=remote_link,
        candidate_ips=[],
        tailscale_recommended={},
    )

    assert is_stable_cloudflare_origin("https://v8.example.com") is True
    assert is_stable_cloudflare_origin("http://v8.example.com") is False
    assert is_stable_cloudflare_origin("https://temporary.trycloudflare.com") is False
    assert all(item["kind"] != "cloudflare_tunnel" for item in endpoints)
