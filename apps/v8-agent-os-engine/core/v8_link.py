from __future__ import annotations

import os
import json
import platform
import socket
import subprocess
from typing import Any
from urllib.parse import urlparse


TRANSPORT_KINDS = {"manual_url", "lan", "wireguard", "tailscale", "headscale", "custom_vpn"}


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
            {"id": "headscale", "kind": "headscale", "label": "Headscale", "enabled": True},
            {"id": "custom-vpn", "kind": "custom_vpn", "label": "Custom VPN", "enabled": True},
        ],
        "diagnostics": {"readOnly": True},
        "meshProviders": [
            {
                "id": "tailscale",
                "kind": "tailscale",
                "enabled": True,
                "mode": "detect_only",
                "allowRouteMutation": False,
            },
            {
                "id": "headscale",
                "kind": "headscale",
                "enabled": False,
                "mode": "external_control_plane",
                "controlUrl": "",
                "namespace": "",
                "allowRouteMutation": False,
            },
        ],
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
        "meshProviders": normalize_mesh_provider_config(incoming.get("meshProviders")),
    }


def normalize_mesh_provider_config(value: Any) -> list[dict[str, Any]]:
    defaults = default_remote_link_config()["meshProviders"]
    providers_by_id = {
        str(item.get("id") or "").strip(): dict(item)
        for item in defaults
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    for item in list(value or []):
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("id") or item.get("kind") or "").strip().lower()
        if not provider_id:
            continue
        merged = {**providers_by_id.get(provider_id, {}), **item}
        merged["id"] = provider_id
        merged["kind"] = str(merged.get("kind") or provider_id).strip().lower()
        merged["enabled"] = bool(merged.get("enabled", provider_id == "tailscale"))
        merged["allowRouteMutation"] = False
        providers_by_id[provider_id] = merged
    return list(providers_by_id.values())


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


def _url_scheme_host_port(value: str, fallback_port: int) -> tuple[str, int]:
    parsed = urlparse(strip_api_suffix(value))
    scheme = parsed.scheme or "http"
    port = int(parsed.port or fallback_port)
    return scheme, port


def _url_for_host(value: str, host: str, fallback_port: int) -> str:
    scheme, port = _url_scheme_host_port(value, fallback_port)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def _device_class(os_name: Any, *names: Any) -> str:
    haystack = " ".join(str(item or "") for item in (os_name, *names)).strip().lower()
    if any(token in haystack for token in ("ios", "iphone", "ipad", "android", "phone", "mobile")):
        return "phone"
    if any(token in haystack for token in ("windows", "macos", "darwin", "linux", "desktop", "server")):
        return "computer"
    return "unknown"


def _candidate_approval_fields(device_class: str) -> dict[str, Any]:
    if device_class == "phone":
        return {
            "requiresApproval": True,
            "approvalReason": "phone_peer_requires_v8_phone_peer_support",
        }
    return {"requiresApproval": False, "approvalReason": ""}


def _tailscale_status() -> dict[str, Any]:
    raw = _run_readonly_command(["tailscale", "status", "--json"], timeout=2.5)
    if not raw:
        return {
            "id": "tailscale",
            "kind": "tailscale",
            "installed": False,
            "loggedIn": False,
            "status": "unavailable",
            "warnings": ["tailscale_cli_unavailable"],
            "recommendedNextAction": "Install and log in to Tailscale, then refresh V8 Link diagnostics.",
        }
    try:
        payload = json.loads(raw)
    except Exception:
        return {
            "id": "tailscale",
            "kind": "tailscale",
            "installed": True,
            "loggedIn": False,
            "status": "unreadable",
            "warnings": ["tailscale_status_unreadable"],
            "recommendedNextAction": "Run tailscale status locally and ensure the client is logged in.",
        }
    self_node = dict(payload.get("Self") or {})
    addresses = [str(item).strip() for item in list(self_node.get("TailscaleIPs") or []) if str(item).strip()]
    dns_name = str(self_node.get("DNSName") or "").strip().rstrip(".")
    online = bool(self_node.get("Online")) if "Online" in self_node else bool(addresses)
    peer_candidates: list[dict[str, Any]] = []
    for key, raw_peer in dict(payload.get("Peer") or {}).items():
        if not isinstance(raw_peer, dict):
            continue
        peer_dns = str(raw_peer.get("DNSName") or "").strip().rstrip(".")
        peer_ips = [str(item).strip() for item in list(raw_peer.get("TailscaleIPs") or []) if str(item).strip()]
        peer_host = str(raw_peer.get("HostName") or raw_peer.get("DNSName") or key or "").strip()
        peer_os = str(raw_peer.get("OS") or "").strip()
        preferred = peer_dns or (peer_ips[0] if peer_ips else "")
        if not preferred:
            continue
        device_class = _device_class(peer_os, peer_host, peer_dns)
        peer_candidates.append(
            {
                "id": f"tailscale:{peer_dns or peer_host or preferred}",
                "source": "tailscale",
                "transportProfileId": "tailscale",
                "hostName": peer_host,
                "dnsName": peer_dns,
                "ips": peer_ips,
                "os": peer_os,
                "online": bool(raw_peer.get("Online")),
                "lastSeen": str(raw_peer.get("LastSeen") or "").strip(),
                "peerBaseUrl": _url_for_host("", preferred, 9530),
                "deviceClass": device_class,
                **_candidate_approval_fields(device_class),
            }
        )
    return {
        "id": "tailscale",
        "kind": "tailscale",
        "installed": True,
        "loggedIn": bool(addresses or dns_name),
        "status": "online" if online else "offline",
        "hostName": str(self_node.get("HostName") or "").strip(),
        "dnsName": dns_name,
        "addresses": addresses,
        "tailnet": str((payload.get("CurrentTailnet") or {}).get("Name") or "").strip(),
        "peerCandidates": peer_candidates,
        "warnings": [] if addresses or dns_name else ["tailscale_logged_out_or_no_ip"],
        "recommendedNextAction": (
            "Use the recommended Tailscale URL as a V8 Link TransportProfile."
            if addresses or dns_name
            else "Log in to Tailscale on the Engine/Admin machine."
        ),
    }


def build_mesh_provider_status(*, admin_base_url: str = "", engine_base_url: str = "") -> dict[str, Any]:
    from core.storage import storage

    system_base = storage.get_system_base_config()
    remote_link = normalize_remote_link_config(
        dict(system_base.get("remoteLink") or {}),
        admin_base_url=admin_base_url or (system_base.get("bridge") or {}).get("adminBaseUrl") or "",
        engine_base_url=engine_base_url or (system_base.get("bridge") or {}).get("engineBaseUrl") or "",
    )
    bridge = dict(system_base.get("bridge") or {})
    admin_base = strip_api_suffix(admin_base_url or bridge.get("adminBaseUrl"))
    engine_base = strip_api_suffix(engine_base_url or bridge.get("engineBaseUrl"))
    providers: list[dict[str, Any]] = []
    provider_config = {
        str(item.get("id") or item.get("kind") or "").strip().lower(): dict(item)
        for item in list(remote_link.get("meshProviders") or [])
        if isinstance(item, dict)
    }
    tailscale_config = provider_config.get("tailscale", {})
    tailscale = {**tailscale_config, **_tailscale_status(), "enabled": bool(tailscale_config.get("enabled", True))}
    preferred_hosts = [str(tailscale.get("dnsName") or "").strip(), *list(tailscale.get("addresses") or [])]
    preferred_host = next((item for item in preferred_hosts if item), "")
    if preferred_host:
        tailscale["recommendedUrls"] = {
            "adminBaseUrl": _url_for_host(admin_base, preferred_host, 9528),
            "engineBaseUrl": _url_for_host(engine_base, preferred_host, 9530),
            "peerBaseUrl": _url_for_host(engine_base, preferred_host, 9530),
        }
    providers.append(tailscale)

    headscale_config = provider_config.get("headscale", {})
    headscale_enabled = bool(headscale_config.get("enabled", False))
    try:
        from core.headscale_admin import headscale_status_snapshot

        headscale_snapshot = headscale_status_snapshot(headscale_config)
    except Exception:
        headscale_snapshot = {}
    providers.append(
        {
            "id": "headscale",
            "kind": "headscale",
            "enabled": headscale_enabled,
            "installed": bool(headscale_config.get("controlUrl")),
            "loggedIn": bool(headscale_snapshot.get("apiKeyConfigured")),
            "status": str(
                headscale_snapshot.get("status")
                or ("configured" if headscale_enabled and headscale_config.get("controlUrl") else "not_configured")
            ),
            "controlUrl": str(headscale_config.get("controlUrl") or "").strip(),
            "namespace": str(headscale_config.get("namespace") or "").strip(),
            "allowRouteMutation": False,
            "apiKeyConfigured": bool(headscale_snapshot.get("apiKeyConfigured")),
            "apiKeyFingerprint": str(headscale_snapshot.get("apiKeyFingerprint") or ""),
            "capabilities": headscale_snapshot.get("capabilities") or {},
            "peerCandidates": list(headscale_snapshot.get("peerCandidates") or []),
            "warnings": list(headscale_snapshot.get("warnings") or ([] if not headscale_enabled else ["headscale_api_key_not_configured"])),
            "recommendedNextAction": (
                str(headscale_snapshot.get("recommendedNextAction") or "")
                if headscale_enabled
                else "Enable only if you already operate a Headscale control plane."
            ),
        }
    )
    peer_candidates = []
    for provider in providers:
        for candidate in list(provider.get("peerCandidates") or []):
            if isinstance(candidate, dict):
                peer_candidates.append(candidate)
    return {
        "ok": True,
        "kind": "v8_mesh_provider_status",
        "readOnly": True,
        "providers": providers,
        "peerCandidates": peer_candidates,
        "policy": {
            "installsClients": False,
            "mutatesRoutes": False,
            "managesKeys": bool(headscale_snapshot.get("apiKeyConfigured")),
            "requiresAuth": True,
        },
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
    info: list[str] = []
    tailscale_healthy = bool(vpn.get("tailscaleDetected") and vpn.get("tailscaleStatusReadable"))
    for label, value in (("admin", admin_base), ("engine", engine_base)):
        parsed = urlparse(value)
        if parsed.hostname and _host_is_loopback(parsed.hostname):
            target = info if tailscale_healthy else warnings
            target.append(f"{label}_loopback_not_reachable_from_phone")
    if not candidate_ips:
        warnings.append("no_private_ip_detected")
    if not vpn.get("wireguardDetected"):
        target = info if tailscale_healthy else warnings
        target.append("wireguard_not_detected")
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
        "info": info,
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
    mesh_status = build_mesh_provider_status(admin_base_url=admin_base, engine_base_url=engine_base)
    tailscale_recommended = {}
    for provider in list(mesh_status.get("providers") or []):
        if str(provider.get("kind") or "") == "tailscale":
            tailscale_recommended = dict(provider.get("recommendedUrls") or {})
            break
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
                for key, value in {
                    **(
                        {
                            "adminBaseUrl": tailscale_recommended.get("adminBaseUrl"),
                            "engineBaseUrl": tailscale_recommended.get("engineBaseUrl"),
                            "peerBaseUrl": tailscale_recommended.get("peerBaseUrl"),
                        }
                        if normalize_transport_kind(dict(item).get("kind")) == "tailscale"
                        else {}
                    ),
                    **dict(item),
                }.items()
                if key in {"id", "kind", "label", "enabled", "adminBaseUrl", "engineBaseUrl", "peerBaseUrl"}
                and value not in (None, "")
            }
            for item in remote_link.get("transportProfiles", [])
            if isinstance(item, dict)
        ],
        "capabilities": {
            "adminProxy": True,
            "phoneUpload": True,
            "artifactPreview": True,
            "runtimeEvents": True,
            "networkSupervisorPeers": True,
        },
        "diagnostics": diagnostics,
        "meshProviders": mesh_status.get("providers", []),
        "peerCandidates": mesh_status.get("peerCandidates", []),
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
