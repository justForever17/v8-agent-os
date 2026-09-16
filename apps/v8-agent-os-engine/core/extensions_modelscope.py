"""ModelScope public catalog adapter. Never runs webpage commands or deploys resources."""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any
from urllib.parse import quote

import httpx

from core.skills_archive import MAX_ARCHIVE_BYTES
from core.skills_install_service import get_skill_receipt, install_skills_from_zip
from core.extensions_store_operations import checkpoint

BASE = "https://modelscope.cn"
_HTTP_SLOTS = threading.BoundedSemaphore(4)


def item_id(value: str) -> str:
    if not re.fullmatch(r"@?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) or any(p in {".", ".."} for p in value.split("/")):
        from core.extensions_store_service import ExtensionStoreError
        raise ExtensionStoreError("invalid_modelscope_id", "魔搭条目必须包含作者和稳定 ID。")
    return value


def read_public(path: str, *, method: str = "GET", params: dict | None = None,
                archive: bool = False) -> Any:
    from core.extensions_store_service import ExtensionStoreError
    limit = MAX_ARCHIVE_BYTES if archive else 4 * 1024 * 1024
    if not _HTTP_SLOTS.acquire(timeout=30):
        raise ExtensionStoreError("source_busy", "来源请求繁忙，请稍后重试。", status_code=503)
    try:
        started = time.monotonic()
        with httpx.Client(timeout=httpx.Timeout(20, connect=10), follow_redirects=False) as client:
            kwargs = {"json": params or {}} if method == "PUT" else {"params": params or {}}
            with client.stream(method, f"{BASE}{path}", **kwargs) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes(64 * 1024):
                    if archive:
                        checkpoint("downloading")
                    content.extend(chunk)
                    if len(content) > limit or time.monotonic() - started > 90:
                        raise ExtensionStoreError("source_budget", "来源响应超过下载大小或时间限制。")
        if archive:
            return bytes(content)
        payload = json.loads(content)
        if payload.get("success") is not True or not isinstance(payload.get("data"), dict):
            raise ExtensionStoreError("source_response", "魔搭来源返回无效响应。", status_code=502)
        return payload["data"]
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise ExtensionStoreError(f"modelscope_http_{status}",
            f"魔搭来源请求失败（HTTP {status}），请稍后重试或手动选择来源。", status_code=502) from None
    except httpx.HTTPError:
        raise ExtensionStoreError("modelscope_network", "无法连接魔搭来源，请检查网络后重试。", status_code=502) from None
    finally:
        _HTTP_SLOTS.release()


def _summary(raw: dict[str, Any], kind: str) -> dict[str, Any]:
    identity = item_id(str(raw.get("id") or ""))
    detail_url = f"{BASE}/{'skills' if kind == 'skills' else 'mcp/servers'}/{quote(identity, safe='/@._-')}"
    result = {"id": identity, "provider": "modelscope", "itemKey": f"modelscope:{kind}:{identity}",
              "name": raw.get("display_name") or raw.get("name") or identity,
              "title": raw.get("name") or identity, "description": str(raw.get("description") or ""),
              "detailUrl": detail_url, "source": identity, "skillId": identity,
              "revision": raw.get("last_modified") or "", "installs": raw.get("downloads") or 0,
              "stars": raw.get("github_stars") or 0, "repositoryUrl": raw.get("source_url") or detail_url,
              "serverName": stable_server_name(identity), "license": raw.get("license") or "",
              "isHosted": raw.get("is_hosted"), "execution": "skill" if kind == "skills" else "unknown"}
    return result


def stable_server_name(identity: str) -> str:
    import hashlib
    return "modelscope-" + re.sub(r"[^A-Za-z0-9_.-]", "-", identity)[-60:] + "-" + hashlib.sha256(identity.encode()).hexdigest()[:8]


def list_items(kind: str, *, query: str, limit: int, page: int, refresh: bool) -> dict[str, Any]:
    from core.extensions_store_service import ExtensionStoreError, _cache_key, _load_cached_value, _normalize_limit
    window = 3000 if kind == "skills" else 100
    # ModelScope caps page_number * page_size, not only the returned row count.
    # A stable divisor keeps every page within that window without skipping its
    # final entries or changing offsets when the user requests the next page.
    page_size = next(size for size in range(_normalize_limit(limit), 0, -1) if window % size == 0)
    if page < 1 or page * page_size > window:
        raise ExtensionStoreError("catalog_window_exceeded", "已到魔搭查询窗口末页，请缩小搜索范围。", status_code=400)
    params = {"search": query.strip(), "page_number": page, "page_size": page_size}
    key = _cache_key(f"modelscope-{kind}-v1", json.dumps(params, sort_keys=True))
    rows_key = "skills" if kind == "skills" else "mcp_server_list"
    total_key = "total" if kind == "skills" else "total_count"
    def valid(data: Any) -> bool:
        return (isinstance(data, dict) and isinstance(data.get(rows_key), list)
                and all(isinstance(row, dict) for row in data[rows_key])
                and (data.get(total_key) is None or isinstance(data[total_key], int) and data[total_key] >= 0))
    def load() -> dict[str, Any]:
        data = read_public("/openapi/v1/skills" if kind == "skills" else "/openapi/v1/mcp/servers",
                           method="GET" if kind == "skills" else "PUT", params=params)
        if not valid(data):
            raise ExtensionStoreError("source_response", "魔搭目录响应不完整，请稍后重试。", status_code=502)
        return data
    data, freshness, error = _load_cached_value(key, refresh=refresh, accepts=valid, loader=load)
    raw = data[rows_key]
    items = []
    invalid_rows = 0
    for row in raw:
        try:
            items.append(_summary(row, kind))
        except ExtensionStoreError as exc:
            if exc.code != "invalid_modelscope_id":
                raise
            invalid_rows += 1
    from core.storage import storage
    servers = (storage.get_mcp_config() or {}).get("mcpServers", {}) if kind == "mcp" else {}
    for row in items:
        receipt = get_skill_receipt("modelscope", row["id"]) if kind == "skills" else None
        row["installed"] = bool(receipt) if kind == "skills" else row["serverName"] in servers
        row["installedRevision"] = (receipt or {}).get("revision")
    total = data.get(total_key)
    at_window = page * page_size == window
    has_more = bool(not at_window and len(raw) == page_size and (total is None or page * page_size < total))
    warnings = ["来源暂不可达，显示该来源上次缓存。"] if error else []
    if invalid_rows:
        warnings.append(f"{invalid_rows} 个来源条目的身份信息不完整，已跳过。")
    if at_window and (total is None or total > window):
        warnings.append(f"魔搭每次查询最多浏览 {window} 项；请缩小搜索范围以查找更多。")
    return {"provider": "modelscope", "items": items, "query": query, "page": page,
            "pageSize": page_size, "catalogLimit": window,
            "returnedCount": len(items), "total": total, "hasMore": has_more, "partial": bool(invalid_rows),
            "nextCursor": str(page + 1) if has_more else None, "freshness": "stale" if error else freshness,
            "sourceCoverage": "catalog", "warnings": warnings}


def _detail(kind: str, identity: str, refresh: bool) -> dict[str, Any]:
    from core.extensions_store_service import _cache_key, _load_cached_value
    identity = item_id(identity)
    path = f"/openapi/v1/{'skills' if kind == 'skills' else 'mcp/servers'}/{quote(identity, safe='/@._-')}"
    def load() -> dict[str, Any]:
        raw = read_public(path)
        # Dedicated operational URLs must never enter the public disk cache.
        raw.pop("operational_urls", None)
        return raw
    data, _, error = _load_cached_value(_cache_key(f"modelscope-{kind}-detail-v1", identity),
        refresh=refresh, accepts=lambda v: isinstance(v, dict), loader=load)
    if refresh and error is not None:
        raise error
    return data


def skill_detail(identity: str, *, refresh: bool = False) -> dict[str, Any]:
    raw = _detail("skills", identity, refresh)
    return {**_summary(raw, "skills"), "markdown": str(raw.get("readme") or raw.get("description") or ""),
            "downloadMethod": "zip", "contentPolicy": "完整包；脚本不会在安装时执行"}


def mcp_detail(identity: str, *, refresh: bool = False, private: bool = False) -> dict[str, Any]:
    from core.extensions_store_service import _candidate_from_config, _public_candidate, _server_configs_from_payload
    from core.mcp_config_service import mcp_config_revision
    from core.storage import storage
    raw = _detail("mcp", identity, refresh)
    summary = _summary(raw, "mcp")
    server_name = summary["serverName"]
    candidates = []
    for entry in raw.get("server_config") or []:
        if not isinstance(entry, dict):
            continue
        for _, config, inputs in _server_configs_from_payload(entry, default_server_name=server_name):
            config = dict(config)
            config.setdefault("type", "stdio" if config.get("command") else "http")
            if str(config.get("command") or "").lower() in {"bash", "sh", "powershell", "pwsh", "cmd", "curl", "wget"}:
                continue
            candidate = _candidate_from_config(server_name=server_name, config=config, source="modelscope", input_defs=inputs)
            if candidate:
                candidates.append(candidate)
    # A hosted capability is an option to connect an existing deployment, never permission to create one.
    if raw.get("is_hosted") is True:
        for transport in ("http", "sse"):
            config = {"type": transport, "url": "${input:endpoint}"}
            candidate = _candidate_from_config(server_name=server_name, config=config, source="modelscope-hosted",
                input_defs={"endpoint": {"description": "已有部署的完整专属连接地址", "password": True}})
            candidate["label"] = f"连接已有托管服务 · {transport.upper()}"
            candidate["requirements"] = [{"key": "endpoint", "target": "url", "name": "endpoint",
                "label": "专属连接地址", "placeholder": "endpoint", "required": True, "secret": True,
                "valueTemplate": "${input:endpoint}"}]
            candidates.insert(0, candidate)
    servers = (storage.get_mcp_config() or {}).get("mcpServers", {})
    return {**summary, "markdown": str(raw.get("readme") or ""), "isHosted": raw.get("is_hosted"),
            "canInstall": bool(candidates), "candidates": candidates if private else [_public_candidate(c) for c in candidates],
            "configRevision": mcp_config_revision(servers.get(server_name)), "configured": server_name in servers,
            "setupUrl": summary["detailUrl"], "warnings": []}


def install_skill(payload: dict[str, Any]) -> dict[str, Any]:
    from core.extensions_store_service import ExtensionStoreError
    identity = item_id(str(payload.get("skillId") or ""))
    detail = skill_detail(identity, refresh=True)
    if payload.get("revision") and payload["revision"] != detail.get("revision"):
        raise ExtensionStoreError("source_changed", "来源已更新，请重新打开详情确认。", status_code=409)
    checkpoint("downloading")
    content = read_public(f"/skills/{quote(identity, safe='/@._-')}/archive/zip/master.zip", archive=True)
    checkpoint("validating")
    return install_skills_from_zip("modelscope.zip", content,
        identity={"provider": "modelscope", "itemId": identity, "revision": detail.get("revision")},
        overwrite=bool(payload.get("overwrite")), selected_skill=payload.get("selectedSkill"))
