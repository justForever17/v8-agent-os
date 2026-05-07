from __future__ import annotations

import os
import platform
import socket
import subprocess
from typing import Any
from urllib.parse import urlparse


TRANSPORT_KINDS = {"manual_url", "lan", "wireguard", "tailscale", "custom_vpn"}


def normalize_transport_kind(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return normalized if normalized in TRANSPORT_KINDS else "manual_url"


def strip_api_suffix(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    for suffix in ("/api", "/v1"):
        if raw.lower().endswith(suffix):
            return raw[: -len(suffix)].rstrip("/")
    return raw


def with_api_suffix(value: Any, suffix: str) -> str:
    base = strip_api_suffix(value)
    if not base:
        return ""
    return f"{base}/{suffix.strip('/')}"


def derive_ws_url(base_url: str) -> str:
    base = strip_api_suffix(base_url)
    if not base:
        return ""
    if base.startswith("https://"):
        return f"wss://{base.removeprefix('https://')}/v1/network-supervisor/peer/ws"
    if base.startswith("http://"):
        return f"ws://{base.removeprefix('http://')}/v1/network-supervisor/peer/ws"
    return ""


def default_remote_link_config(*, admin_base_url: str = "", engine_base_url: str = "") -> dict[str, Any]:
    admin_base = strip_api_suffix(admin_base_url)
    engine_base = strip_api_suffix(engine_base_url)
    return {
        "enabled": True,
        "activeProfileId": "manual-local",
        "transportProfiles": [
            {
                "id": "manual-local",
                "kind": "manual_url",
                "label": "Manual / Local",
                "enabled": True,
                "adminBaseUrl": admin_base,
                "engineBaseUrl": engine_base,
                "peerBaseUrl": engine_base,
            },
            {"id": "lan", "kind": "lan", "label": "LAN", "enabled": True},
            {"id": "wireguard", "kind": "wireguard", "label": "WireGuard", "enabled": True},
            {"id": "tailscale", "kind": "tailscale", "label": "Tailscale", "enabled": True},
            {"id": "custom-vpn", "kind": "custom_vpn", "label": "Custom VPN", "enabled": True},
        ],
        "diagnostics": {"readOnly": True},
    }


def normalize_remote_link_config(config: dict[str, Any] | None, *, admin_base_url: str = "", engine_base_url: str = "") -> dict[str, Any]:
    base = default_remote_link_config(admin_base_url=admin_base_url, engine_base_url=engine_base_url)
    incoming = dict(config or {})
    profiles_by_id: dict[str, dict[str, Any]] = {
        str(item.get("id") or "").strip(): dict(item)
        for item in base["transportProfiles"]
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    for item in list(incoming.get("transportProfiles") or []):
        if not isinstance(item, dict):
            continue
        profile_id = str(item.get("id") or "").strip()
        if not profile_id:
            continue
        merged = {**profiles_by_id.get(profile_id, {}), **item}
        merged["id"] = profile_id
        merged["kind"] = normalize_transport_kind(merged.get("kind"))
        merged["enabled"] = bool(merged.get("enabled", True))
        for key in ("adminBaseUrl", "engineBaseUrl", "peerBaseUrl"):
            if key in merged:
                merged[key] = strip_api_suffix(merged.get(key))
        profiles_by_id[profile_id] = merged

    profiles = list(profiles_by_id.values())
    active_profile_id = str(incoming.get("activeProfileId") or base["activeProfileId"]).strip()
    if active_profile_id not in profiles_by_id and profiles:
        active_profile_id = str(profiles[0].get("id") or "")

    return {
        "enabled": bool(incoming.get("enabled", base["enabled"])),
        "activeProfileId": active_profile_id,
        "transportProfiles": profiles,
        "diagnostics": {"readOnly": True, **dict(incoming.get("diagnostics") or {})},
    }


def _host_is_loopback(host: str) -> bool:
    normalized = host.lower().strip("[]")
    return normalized in {"localhost", "::1"} or normalized.startswith("127.")


def _is_private_ip(value: str) -> bool:
    try:
        import ipaddress

        ip = ipaddress.ip_address(value)
        return bool(ip.is_private or ip.is_link_local)
    except Exception:
        return False


def _candidate_ips() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    hostnames = {socket.gethostname()}
    try:
        hostnames.add(socket.getfqdn())
    except Exception:
        pass
    for host in hostnames:
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            continue
        for family, _, _, _, sockaddr in infos:
            ip = str(sockaddr[0])
            if ip in seen or _host_is_loopback(ip):
                continue
            seen.add(ip)
            items.append(
                {
                    "address": ip,
                    "family": "ipv6" if family == socket.AF_INET6 else "ipv4",
                    "private": _is_private_ip(ip),
                }
            )
    return items[:12]


def _run_readonly_command(args: list[str], *, timeout: float = 2.0) -> str:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return (result.stdout or result.stderr or "").strip()
    except Exception:
        return ""


def _vpn_presence() -> dict[str, Any]:
    system = platform.system().lower()
    probe_text = ""
    if system == "windows":
        probe_text = _run_readonly_command(["ipconfig", "/all"])
    else:
        probe_text = _run_readonly_command(["sh", "-c", "ifconfig 2>/dev/null || ip addr 2>/dev/null"])
    lower = probe_text.lower()
    tailscale_json = _run_readonly_command(["tailscale", "status", "--json"], timeout=2.5)
    return {
        "wireguardDetected": "wireguard" in lower or "wg" in lower,
        "tailscaleDetected": bool(tailscale_json) or "tailscale" in lower,
        "tailscaleStatusReadable": bool(tailscale_json and tailscale_json.startswith("{")),
    }


def _port_probe(url: str) -> dict[str, Any]:
    parsed = urlparse(strip_api_suffix(url))
    host = parsed.hostname or ""
    if not host:
        return {"url": url, "reachable": False, "reason": "missing_host"}
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, int(port)), timeout=0.7):
            return {"url": url, "host": host, "port": port, "reachable": True}
    except Exception as exc:
        return {"url": url, "host": host, "port": port, "reachable": False, "reason": type(exc).__name__}


def build_vpn_diagnostics(*, admin_base_url: str = "", engine_base_url: str = "") -> dict[str, Any]:
    admin_base = strip_api_suffix(admin_base_url)
    engine_base = strip_api_suffix(engine_base_url)
    candidate_ips = _candidate_ips()
    vpn = _vpn_presence()
    warnings: list[str] = []
    for label, value in (("admin", admin_base), ("engine", engine_base)):
        parsed = urlparse(value)
        if parsed.hostname and _host_is_loopback(parsed.hostname):
            warnings.append(f"{label}_loopback_not_reachable_from_phone")
    if not candidate_ips:
        warnings.append("no_private_ip_detected")
    if not vpn.get("wireguardDetected"):
        warnings.append("wireguard_not_detected")
    if not vpn.get("tailscaleDetected"):
        warnings.append("tailscale_not_detected")
    return {
        "readOnly": True,
        "platform": platform.system().lower() or os.name,
        "candidateIps": candidate_ips,
        "vpn": vpn,
        "reachability": {
            "admin": _port_probe(admin_base) if admin_base else {"reachable": False, "reason": "missing_url"},
            "engine": _port_probe(engine_base) if engine_base else {"reachable": False, "reason": "missing_url"},
        },
        "warnings": warnings,
        "notes": [
            "V8 only observes VPN state; it does not change WireGuard/Tailscale routes, DNS, MTU, or keys.",
        ],
    }


def _active_profile(remote_link: dict[str, Any]) -> dict[str, Any]:
    active_id = str(remote_link.get("activeProfileId") or "").strip()
    profiles = [dict(item) for item in list(remote_link.get("transportProfiles") or []) if isinstance(item, dict)]
    for item in profiles:
        if str(item.get("id") or "").strip() == active_id:
            return item
    return profiles[0] if profiles else {}


def build_link_manifest(*, request_admin_origin: str | None = None) -> dict[str, Any]:
    from core.storage import storage

    system_base = storage.get_system_base_config()
    bridge = dict(system_base.get("bridge") or {})
    configured_admin_api = str(bridge.get("adminBaseUrl") or "").strip()
    configured_engine_api = str(bridge.get("engineBaseUrl") or "").strip()
    request_admin_base = strip_api_suffix(request_admin_origin or "")
    admin_base = request_admin_base or strip_api_suffix(configured_admin_api)
    engine_base = strip_api_suffix(configured_engine_api)
    remote_link = normalize_remote_link_config(
        dict(system_base.get("remoteLink") or {}),
        admin_base_url=admin_base,
        engine_base_url=engine_base,
    )
    active = _active_profile(remote_link)
    transport_kind = normalize_transport_kind(active.get("kind"))
    diagnostics = build_vpn_diagnostics(admin_base_url=admin_base, engine_base_url=engine_base)
    warnings = list(diagnostics.get("warnings") or [])
    if not remote_link.get("enabled", True):
        warnings.append("remote_link_disabled")
    return {
        "ok": True,
        "kind": "v8_link_manifest",
        "version": "1",
        "transportKind": transport_kind,
        "activeProfileId": str(active.get("id") or remote_link.get("activeProfileId") or ""),
        "admin": {
            "baseUrl": admin_base,
            "apiBaseUrl": with_api_suffix(admin_base, "api"),
            "configuredApiBaseUrl": configured_admin_api,
        },
        "engine": {
            "baseUrl": engine_base,
            "apiBaseUrl": with_api_suffix(engine_base, "v1"),
            "directExposure": False,
        },
        "profiles": [
            {
                key: value
                for key, value in dict(item).items()
                if key in {"id", "kind", "label", "enabled", "adminBaseUrl", "engineBaseUrl", "peerBaseUrl"}
                and value not in (None, "")
            }
            for item in remote_link.get("transportProfiles", [])
            if isinstance(item, dict)
        ],
        "capabilities": {
            "adminProxy": True,
            "phoneUpload": True,
            "runtimeEvents": True,
            "networkSupervisorPeers": True,
        },
        "diagnostics": diagnostics,
        "warnings": warnings,
    }


def resolve_peer_transport_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    from core.storage import storage

    current = dict(endpoint or {})
    system_base = storage.get_system_base_config()
    bridge = dict(system_base.get("bridge") or {})
    remote_link = normalize_remote_link_config(
        dict(system_base.get("remoteLink") or {}),
        admin_base_url=strip_api_suffix(bridge.get("adminBaseUrl")),
        engine_base_url=strip_api_suffix(bridge.get("engineBaseUrl")),
    )
    profiles = {
        str(item.get("id") or "").strip(): dict(item)
        for item in list(remote_link.get("transportProfiles") or [])
        if isinstance(item, dict)
    }
    profile_id = str(current.get("transportProfileId") or "").strip()
    profile = profiles.get(profile_id) if profile_id else None
    base_url = strip_api_suffix(current.get("peerBaseUrl") or current.get("baseUrl"))
    warnings: list[str] = []
    if not base_url and profile:
        base_url = strip_api_suffix(profile.get("peerBaseUrl") or profile.get("engineBaseUrl") or profile.get("adminBaseUrl"))
        warnings.append("resolved_from_transport_profile")
    if not base_url:
        warnings.append("missing_peer_base_url")
    transport_kind = normalize_transport_kind((profile or {}).get("kind") or current.get("transportKind"))
    current["configuredBaseUrl"] = str(current.get("baseUrl") or "").strip()
    current["baseUrl"] = base_url
    current["resolvedBaseUrl"] = base_url
    current["transportProfileId"] = profile_id or str((profile or {}).get("id") or "").strip()
    current["transportKind"] = transport_kind
    current["routeWarnings"] = warnings
    if base_url and not str(current.get("wsUrl") or "").strip():
        current["wsUrl"] = derive_ws_url(base_url)
    return current
