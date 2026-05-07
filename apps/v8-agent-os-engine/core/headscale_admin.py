from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from core.v8_agent_os_paths import NETWORK_SUPERVISOR_SECRETS_PATH


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat().replace("+00:00", "Z")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _strip_api_suffix(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    for suffix in ("/api/v1", "/api", "/v1"):
        if raw.lower().endswith(suffix):
            return raw[: -len(suffix)].rstrip("/")
    return raw


def _read_secrets() -> dict[str, Any]:
    if not NETWORK_SUPERVISOR_SECRETS_PATH.exists():
        return {}
    try:
        return json.loads(NETWORK_SUPERVISOR_SECRETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_secrets(payload: dict[str, Any]) -> None:
    NETWORK_SUPERVISOR_SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    NETWORK_SUPERVISOR_SECRETS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _headscale_secret() -> dict[str, Any]:
    return dict(_read_secrets().get("headscale") or {})


def _api_key() -> str:
    return str(_headscale_secret().get("apiKey") or "").strip()


def set_headscale_api_key(api_key: str) -> dict[str, Any]:
    normalized = str(api_key or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="missing_headscale_api_key")
    payload = _read_secrets()
    payload["headscale"] = {
        **dict(payload.get("headscale") or {}),
        "apiKey": normalized,
        "updatedAt": _utc_iso(),
    }
    _write_secrets(payload)
    return headscale_secret_status()


def clear_headscale_api_key() -> dict[str, Any]:
    payload = _read_secrets()
    headscale = dict(payload.get("headscale") or {})
    headscale.pop("apiKey", None)
    headscale["updatedAt"] = _utc_iso()
    payload["headscale"] = headscale
    _write_secrets(payload)
    return headscale_secret_status()


def headscale_secret_status() -> dict[str, Any]:
    key = _api_key()
    return {
        "apiKeyConfigured": bool(key),
        "apiKeyFingerprint": _fingerprint(key) if key else "",
    }


def headscale_status_snapshot(provider_config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(provider_config or {})
    control_url = _strip_api_suffix(config.get("controlUrl"))
    secret_status = headscale_secret_status()
    warnings: list[str] = []
    if bool(config.get("enabled", False)) and not control_url:
        warnings.append("headscale_control_url_missing")
    if bool(config.get("enabled", False)) and not secret_status["apiKeyConfigured"]:
        warnings.append("headscale_api_key_not_configured")
    status = "not_configured"
    if control_url and secret_status["apiKeyConfigured"]:
        status = "configured"
    elif control_url:
        status = "missing_api_key"
    return {
        **secret_status,
        "status": status,
        "warnings": warnings,
        "capabilities": {
            "connect": bool(control_url and secret_status["apiKeyConfigured"]),
            "readUsers": bool(control_url and secret_status["apiKeyConfigured"]),
            "readNodes": bool(control_url and secret_status["apiKeyConfigured"]),
            "preAuthKeys": bool(control_url and secret_status["apiKeyConfigured"]),
            "dangerousMutations": False,
        },
        "recommendedNextAction": (
            "Configure a Headscale API key in Admin."
            if control_url and not secret_status["apiKeyConfigured"]
            else "Configure Headscale control URL and API key."
            if not control_url
            else "Headscale API is configured. Run a connection test."
        ),
    }


def _extract_items(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        raw = payload.get(key)
        if isinstance(raw, list):
            return [dict(item) for item in raw if isinstance(item, dict)]
    for value in payload.values():
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _redact_preauth_key(item: dict[str, Any], *, reveal: bool = False) -> dict[str, Any]:
    key = str(item.get("key") or "").strip()
    redacted = dict(item)
    if key and not reveal:
        redacted.pop("key", None)
        redacted["keyPrefix"] = key[:8]
        redacted["keyFingerprint"] = _fingerprint(key)
    elif key:
        redacted["keyPrefix"] = key[:8]
        redacted["keyFingerprint"] = _fingerprint(key)
        redacted["oneTimeSecret"] = True
    return redacted


def _device_class(os_name: Any, *names: Any) -> str:
    haystack = " ".join(str(item or "") for item in (os_name, *names)).strip().lower()
    if any(token in haystack for token in ("ios", "iphone", "ipad", "android", "phone", "mobile")):
        return "phone"
    if any(token in haystack for token in ("windows", "macos", "darwin", "linux", "desktop", "server")):
        return "computer"
    return "unknown"


def _candidate_from_node(node: dict[str, Any], *, source: str) -> dict[str, Any]:
    ips = [str(item).strip() for item in list(node.get("ipAddresses") or []) if str(item).strip()]
    name = str(node.get("name") or node.get("givenName") or node.get("hostName") or "").strip()
    preferred = name or (ips[0] if ips else "")
    if preferred and "." not in preferred and not preferred.startswith("100."):
        preferred = f"{preferred}"
    peer_base = f"http://{preferred}:9530" if preferred else ""
    os_name = str(node.get("os") or "").strip()
    device_class = _device_class(os_name, name, node.get("givenName"), node.get("hostName"))
    return {
        "id": f"{source}:{node.get('id') or name or (ips[0] if ips else '')}",
        "source": source,
        "transportProfileId": "headscale" if source == "headscale" else "tailscale",
        "hostName": name,
        "dnsName": name if "." in name else "",
        "ips": ips,
        "os": os_name,
        "online": bool(node.get("online")),
        "lastSeen": str(node.get("lastSeen") or "").strip(),
        "peerBaseUrl": peer_base,
        "deviceClass": device_class,
        "requiresApproval": device_class == "phone",
        "approvalReason": "phone_peer_requires_v8_phone_peer_support" if device_class == "phone" else "",
    }


class HeadscaleAdminClient:
    def __init__(self, provider_config: dict[str, Any] | None = None) -> None:
        self.provider_config = dict(provider_config or {})
        self.control_url = _strip_api_suffix(self.provider_config.get("controlUrl"))
        self.api_key = _api_key()
        if not self.control_url:
            raise HTTPException(status_code=400, detail="headscale_control_url_missing")
        if not self.api_key:
            raise HTTPException(status_code=400, detail="headscale_api_key_not_configured")

    def _url(self, path: str) -> str:
        return f"{self.control_url}/{path.lstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=4.0)) as client:
                response = await client.request(method, self._url(path), params=params, json=json_payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail={"failureClass": "headscale_timeout", "reason": str(exc)}) from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail={"failureClass": "headscale_unreachable", "reason": str(exc)}) from exc
        data = await _json_response(response)
        if response.status_code >= 400:
            failure = "auth_failed" if response.status_code in {401, 403} else "headscale_api_error"
            raise HTTPException(status_code=response.status_code, detail={"failureClass": failure, "statusCode": response.status_code, "response": data})
        return data

    async def version(self) -> dict[str, Any]:
        endpoints = ["/api/v1/version", "/api/v1/health", "/version", "/health"]
        last_error = ""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                for endpoint in endpoints:
                    try:
                        response = await client.get(self._url(endpoint), headers={"Accept": "application/json", "Authorization": f"Bearer {self.api_key}"})
                    except Exception as exc:
                        last_error = str(exc)
                        continue
                    data = await _json_response(response)
                    if response.status_code < 400:
                        return {"ok": True, "statusCode": response.status_code, "endpoint": endpoint, "version": data or response.text}
                    last_error = f"{endpoint}: {response.status_code}"
            return {"ok": False, "failureClass": "headscale_version_unreachable", "reason": last_error or "no_version_endpoint_responded"}
        except Exception as exc:
            return {"ok": False, "failureClass": "headscale_version_unreachable", "reason": str(exc)}

    async def status(self) -> dict[str, Any]:
        version = await self.version()
        swagger = await self.fetch_swagger_capabilities()
        return {
            "ok": bool(version.get("ok")),
            "kind": "headscale_admin_status",
            "controlUrl": self.control_url,
            **headscale_secret_status(),
            "version": version,
            "capabilities": swagger,
        }

    async def fetch_swagger_capabilities(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0)) as client:
                response = await client.get(self._url("/swagger/v1/openapiv2.json"), headers={"Accept": "application/json"})
            data = await _json_response(response)
            paths = set(dict(data.get("paths") or {}).keys()) if isinstance(data, dict) else set()
            return {
                "swaggerReadable": response.status_code < 400 and bool(paths),
                "users": "/api/v1/user" in paths,
                "nodes": "/api/v1/node" in paths,
                "preAuthKeys": "/api/v1/preauthkey" in paths,
                "policy": "/api/v1/policy" in paths,
                "routes": "/api/v1/node/{nodeId}/approve_routes" in paths,
            }
        except Exception:
            return {"swaggerReadable": False}

    async def users(self) -> dict[str, Any]:
        payload = await self.request("GET", "/api/v1/user")
        return {"ok": True, "items": _extract_items(payload, "users", "user"), "raw": payload}

    async def nodes(self) -> dict[str, Any]:
        payload = await self.request("GET", "/api/v1/node")
        nodes = _extract_items(payload, "nodes", "node")
        return {
            "ok": True,
            "items": nodes,
            "peerCandidates": [_candidate_from_node(node, source="headscale") for node in nodes],
            "raw": payload,
        }

    async def preauth_keys(self) -> dict[str, Any]:
        payload = await self.request("GET", "/api/v1/preauthkey")
        items = [_redact_preauth_key(item) for item in _extract_items(payload, "preAuthKeys", "preauthkeys", "preAuthKey")]
        return {"ok": True, "items": items, "raw": payload}

    async def create_preauth_key(self, body: dict[str, Any]) -> dict[str, Any]:
        user = str(body.get("user") or body.get("userId") or "").strip()
        if not user:
            raise HTTPException(status_code=400, detail="headscale_user_required")
        ttl_minutes = max(5, min(int(body.get("ttlMinutes") or 60), 60 * 24 * 30))
        expiration = _utc_iso(_utc_now() + timedelta(minutes=ttl_minutes))
        payload = {
            "user": user,
            "reusable": bool(body.get("reusable", False)),
            "ephemeral": bool(body.get("ephemeral", False)),
            "expiration": expiration,
            "aclTags": [str(item).strip() for item in list(body.get("aclTags") or []) if str(item).strip()],
        }
        response = await self.request("POST", "/api/v1/preauthkey", json_payload=payload)
        key = dict(response.get("preAuthKey") or response.get("preauthKey") or response)
        return {"ok": True, "preAuthKey": _redact_preauth_key(key, reveal=True)}

    async def register_node(self, body: dict[str, Any]) -> dict[str, Any]:
        user = str(body.get("user") or body.get("userId") or "").strip()
        key = str(body.get("key") or body.get("registrationKey") or "").strip()
        if not user or not key:
            raise HTTPException(status_code=400, detail="headscale_user_and_registration_key_required")
        payload = await self.request("POST", "/api/v1/node/register", params={"user": user, "key": key})
        return {"ok": True, "node": payload.get("node") or payload}

    async def rename_node(self, node_id: str, new_name: str) -> dict[str, Any]:
        normalized_name = str(new_name or "").strip()
        if not normalized_name:
            raise HTTPException(status_code=400, detail="headscale_node_name_required")
        payload = await self.request("POST", f"/api/v1/node/{quote(str(node_id), safe='')}/rename/{quote(normalized_name, safe='')}")
        return {"ok": True, "node": payload.get("node") or payload}

    async def expire_node(self, node_id: str, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return _review_required("headscale_expire_node", node_id)
        payload = await self.request("POST", f"/api/v1/node/{quote(str(node_id), safe='')}/expire")
        return {"ok": True, "node": payload.get("node") or payload}

    async def delete_node(self, node_id: str, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return _review_required("headscale_delete_node", node_id)
        payload = await self.request("DELETE", f"/api/v1/node/{quote(str(node_id), safe='')}")
        return {"ok": True, "result": payload}

    async def set_routes(self, node_id: str, routes: list[str], *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return _review_required("headscale_route_mutation", node_id)
        payload = await self.request("POST", f"/api/v1/node/{quote(str(node_id), safe='')}/approve_routes", json_payload={"routes": routes})
        return {"ok": True, "result": payload}

    async def set_tags(self, node_id: str, tags: list[str], *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return _review_required("headscale_tag_mutation", node_id)
        payload = await self.request("POST", f"/api/v1/node/{quote(str(node_id), safe='')}/tags", json_payload={"tags": tags})
        return {"ok": True, "result": payload}

    async def get_policy(self) -> dict[str, Any]:
        payload = await self.request("GET", "/api/v1/policy")
        return {"ok": True, "policy": payload}

    async def set_policy(self, body: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            return _review_required("headscale_policy_mutation", "policy")
        payload = await self.request("PUT", "/api/v1/policy", json_payload=dict(body.get("policy") or body))
        return {"ok": True, "result": payload}


def _review_required(operation: str, target: str) -> dict[str, Any]:
    return {
        "ok": False,
        "requiresSafetyReview": True,
        "operation": operation,
        "target": target,
        "recommendedNextAction": "Confirm this dangerous Headscale mutation from the Admin safety review UI.",
    }


async def _json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {"value": data}
    except Exception:
        text = response.text
        return {"text": text[:4000], "omittedChars": max(0, len(text) - 4000)}


def client_from_provider(provider_config: dict[str, Any] | None = None) -> HeadscaleAdminClient:
    if provider_config is not None:
        return HeadscaleAdminClient(provider_config)
    from core.storage import storage

    system_base = storage.get_system_base_config()
    remote = dict((system_base.get("remoteLink") or {}))
    for item in list(remote.get("meshProviders") or []):
        if isinstance(item, dict) and str(item.get("id") or item.get("kind") or "").strip().lower() == "headscale":
            return HeadscaleAdminClient(item)
    return HeadscaleAdminClient({})
