"""Engine-owned Phone/Web wire API, backed by the existing business services."""
from __future__ import annotations

import json
import re
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request

from core.auth_context import is_local_client, require_client_principal
from core.client_transport import InternalResponse, internal_json

router = APIRouter(prefix="/api/client", tags=["client"])
SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}"


def _database(request: Request):
    configured = getattr(request.app.state, "client_database", None)
    if configured is not None:
        return configured
    from core.database import db
    return db


def _owner_matches(row: dict, principal) -> bool:
    owner = str(row.get("user_id") or row.get("userId") or "").strip().casefold()
    return bool(owner and owner in {principal.subject.casefold(), principal.session_id.casefold()})


def require_session(request: Request, principal, session_id: str, *, allow_new: bool = False):
    row = _database(request).get_session(session_id)
    if row is None and allow_new:
        return None
    if row is None or not _owner_matches(row, principal):
        # Existence and non-ownership deliberately have the same wire outcome.
        raise HTTPException(404, "session_not_found")
    request.scope.setdefault("state", {})["client_session_id"] = session_id
    return row


def _record_session(request: Request, principal, method: str, record_id: str):
    row = getattr(_database(request), method)(record_id)
    if not row and method == "get_message":
        row = _database(request).get_chat_canonical_message(record_id)
    if not row:
        raise HTTPException(404, "resource_not_found")
    session_id = str(row.get("session_id") or row.get("sessionId") or "")
    require_session(request, principal, session_id)
    return session_id


def _json_object(value):
    return value if isinstance(value, dict) else {}


async def _payload(request: Request) -> dict:
    try:
        value = await request.json()
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, "invalid_json") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, "json_object_required")
    return value


def build_chat_payload(body: dict, principal) -> dict:
    """Preserve the existing Web/Phone composer contract; never accept its owner."""
    data = _json_object(body.get("data"))
    session_id = str(body.get("session_id") or body.get("conversationId") or data.get("conversationId") or uuid.uuid4())
    messages = [item for item in body.get("messages", []) if isinstance(item, dict)]
    client_id = body.get("clientMessageId") or body.get("client_message_id") or data.get("clientMessageId") or data.get("client_message_id")
    file_urls = [url for url in [*data.get("fileUrls", []), *body.get("fileUrls", [])] if isinstance(url, str) and url.strip()]
    attachments, seen = [], set()
    for item in [*data.get("attachments", []), *body.get("attachments", [])]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("url") or item.get("publicUrl") or item.get("workspacePath") or "").casefold()
        if key and key not in seen:
            attachments.append(item)
            seen.add(key)
    for url in file_urls:
        if url.casefold() not in seen:
            attachments.append({"url": url, "publicUrl": url, "source": "legacy_fileUrls"})
            seen.add(url.casefold())
    current_content = str((messages[-1].get("content") if messages else "") or "")
    result = {
        "session_id": session_id, "conversationId": session_id,
        "clientMessageId": client_id, "user_id": principal.session_id,
        "stream": True, "title": current_content[:30] or "New Chat",
        "messages": messages, "fileUrls": file_urls, "attachments": attachments,
        "config": {"provider": str(data.get("provider") or ""), "model_name": str(data.get("model") or "")},
        "data": {**data, "conversationId": session_id, "clientMessageId": client_id,
                 "fileUrls": file_urls, "attachments": attachments},
    }
    for snake, camel in (("project_id", "projectId"), ("workspace_id", "workspaceId"),
                         ("workspace_path", "workspacePath"), ("scope_hint", "scopeHint"), ("scope_mode", "scopeMode")):
        value = body.get(snake, body.get(camel, data.get(camel)))
        if value is not None:
            result[snake] = value
    result.setdefault("scope_mode", "explicit")
    for field in ("tool_outputs", "resume_run_id"):
        if field in body:
            result[field] = body[field]
    for location in (data, body):
        for key in ("supervisorRuntimeMode", "supervisor_runtime_mode"):
            if key in location:
                value = location[key]
                if not isinstance(value, str) or value not in {"auto", "engineering", "research", "creative_media", "computer_use", "rpa"}:
                    raise HTTPException(400, "invalid_supervisor_runtime_mode")
                result["data"]["supervisorRuntimeMode"] = value
                return result
    return result


def _relative_resource_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return value
    path = parts.path
    if path.startswith("/v1/artifacts/"):
        path = "/api/client" + path[3:]
    elif path.startswith("/api/artifacts/"):
        path = "/api/client" + path[4:]
    elif path.startswith("/api/workspace/"):
        path = "/api/client" + path[4:]
    elif path.startswith("/workspace/"):
        path = "/api/client/workspace/files/" + path.removeprefix("/workspace/")
    if path != parts.path:
        return path + (("?" + parts.query) if parts.query else "")
    return value


_RESOURCE_CONTENT_URL = re.compile(r"(?:https?://(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?/|/(?:api/(?:client/)?workspace/|workspace/|(?:v1|api(?:/client)?)/artifacts/))[^\s\"'<>\\)]+")


def _surface_url(value: str, request: Request, principal) -> str:
    normalized = _relative_resource_url(value)
    if not normalized.startswith("/api/client/") or not ("/content" in normalized or normalized.startswith(("/api/client/workspace/resource?", "/api/client/workspace/files/"))):
        return normalized
    from core.client_identity import get_identity_service
    from core.client_identity.resources import sign_resource_url
    parts = urlsplit(normalized)
    query = {key: values[-1] for key, values in parse_qs(parts.query).items() if key not in {"v8sig", "v8exp"}}
    session_id = str(query.get("sessionId") or request.scope.get("state", {}).get("client_session_id") or "")
    if not session_id:
        return normalized
    try:
        require_session(request, principal, session_id)
    except HTTPException:
        # A user/Agent can write a broken link in otherwise valid text. Never
        # sign it, and do not let that link suppress the entire conversation.
        return normalized
    query["sessionId"] = session_id
    canonical = parts.path + "?" + urlencode(query)
    # Cache only within this authenticated response/stream. Rotate well before
    # the 10-minute capability expiry; there is no authorization decision cache.
    cache = request.scope.setdefault("state", {}).setdefault("client_resource_urls", {})
    cache_key = (canonical, int(time.time() // 300))
    signed = cache.get(cache_key)
    if signed is None:
        if len(cache) >= 1024:
            cache.clear()
        signed = sign_resource_url(get_identity_service(), canonical, principal, session_id=session_id)
        cache[cache_key] = signed
    return signed + (("#" + parts.fragment) if parts.fragment else "")


def normalize_client_surface(value, request: Request, principal):
    """Only transport URLs are adapted here; business projection stays in Engine."""
    if isinstance(value, list):
        return [normalize_client_surface(item, request, principal) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"raw", "rawEnvelope", "raw_envelope"}:
            continue
        if isinstance(item, str):
            if key.lower().endswith(("url", "path")):
                normalized = _surface_url(item, request, principal)
                if key == "adminPath":
                    result["signedUrl"] = normalized
                    item = _relative_resource_url(item)
                else:
                    item = normalized
            elif key in {"content", "contentText", "content_text", "label", "question", "reason", "result"}:
                item = _RESOURCE_CONTENT_URL.sub(lambda match: _surface_url(match[0], request, principal), item)
        result[key] = normalize_client_surface(item, request, principal)
    return result


async def _session_index(request, principal):
    from api.session_history_paging import page_session_history, SessionHistoryCursorError
    payload = await internal_json(request, principal, "/sessions/quick-index")
    # Filter BEFORE pagination. The old cursor principal binding was not a row ACL.
    with _database(request).get_connection() as connection:
        owned_ids = {row["id"] for row in connection.execute("SELECT id, user_id FROM sessions")
                     if _owner_matches(dict(row), principal)}
    payload["sessions"] = [item for item in payload.get("sessions", [])
                           if str(item.get("id") or item.get("sessionId") or "") in owned_ids]
    if "limit" not in request.query_params:
        return payload["sessions"]
    try:
        limit = max(1, min(200, int(request.query_params.get("limit", "80"))))
        paged = page_session_history(payload, authority=principal.issuer, principal=principal.subject,
                                    query=request.query_params.get("q", "")[:200],
                                    cursor=request.query_params.get("cursor", ""), limit=limit)
    except ValueError as exc:
        raise HTTPException(409 if isinstance(exc, SessionHistoryCursorError) else 400, str(exc)) from exc
    return {"items": paged["sessions"], "pageInfo": paged["pageInfo"]}


async def _conversation_detail(request, principal, session_id):
    compact = request.query_params.get("omitMessages") == "1"
    snapshot = await internal_json(request, principal, f"/sessions/{session_id}/snapshot", query={"compact": int(compact)})
    history = {} if compact else await internal_json(request, principal, f"/sessions/{session_id}/history")
    result = {**snapshot, **history, "id": session_id, "projection": snapshot}
    result["messages"] = [] if compact else history.get("timeline") or history.get("messages") or _json_object(snapshot.get("snapshot")).get("messages", [])
    result["timeline"] = [] if compact else history.get("timeline") or history.get("messages", [])
    return result


# Exact public-to-business mappings. There is intentionally no arbitrary /v1 proxy.
_SIMPLE_ROUTES = (
    (r"projects", {"GET", "POST"}, "/projects"),
    (r"workspace-presentations", {"GET", "PUT"}, "/workspace-presentations"),
    (r"workspace/folders", {"GET", "POST"}, "/workspace/folders"),
    (r"workspace/resource", {"GET", "HEAD"}, "/workspace/resource"),
    (rf"sessions/({SEGMENT})/(scope|scope/re-resolve|scope/history|processes|workbench/files/read)", {"GET", "PUT", "POST"}, r"/sessions/\1/\2"),
    (rf"artifacts(?:/({SEGMENT})(/content)?)?", {"GET", "HEAD"}, None),
    (r"sources", {"GET"}, "/sources"),
    (rf"messages/({SEGMENT})", {"DELETE"}, r"/messages/\1"),
    (rf"runs/({SEGMENT})/commands/(interrupt|retry|cancel|resume|pause)", {"POST"}, r"/runs/\1/commands/\2"),
    (r"runs", {"GET"}, "/runs"),
    (rf"runs/({SEGMENT})", {"GET"}, r"/runs/\1"),
    (rf"approvals(?:/({SEGMENT})/(approve|reject|refresh-spec-review))?", {"GET", "POST"}, None),
    (rf"ask-user/({SEGMENT})/respond", {"POST"}, r"/ask-user/\1/respond"),
    (rf"specs(?:/({SEGMENT})(?:/(analysis|stages/{SEGMENT}(?:/(?:approve|revise|edit))?))?)?", {"GET", "POST"}, None),
    (rf"commands(?:/({SEGMENT}))?", {"GET"}, None),
    (r"skills/list", {"GET"}, "/skills/list"),
    (r"plugins/(mentions|catalog)", {"GET"}, r"/api/plugins/\1"),
    (r"models/supervisor-reasoning-effort", {"GET", "PATCH"}, "/models/supervisor-reasoning-effort"),
    (r"memory/session-extraction", {"POST"}, "/memory/session-extraction"),
    (rf"ui-actions/({SEGMENT})(/submit)?", {"GET", "POST"}, r"/ui/actions/\1\2"),
    (r"mcp-apps/resources/read", {"GET"}, "/mcp/apps/resources/read"),
    (rf"mcp-apps/instances/({SEGMENT})/(rpc|close)", {"POST"}, r"/mcp/apps/instances/\1/\2"),
    (r"audio/input-status", {"GET"}, "/audio/input-status"),
    (r"audio/stt", {"POST"}, "/audio/stt/transcribe"),
    (r"audio/tts", {"POST"}, "/audio/tts/stream"),
)


def _is_phone_principal(principal) -> bool:
    return str(getattr(principal, "device_kind", "") or "") == "human_phone"


def _allow_local_or_phone(principal) -> bool:
    return is_local_client(principal) or _is_phone_principal(principal)


def _desktop_viewer_id(principal) -> str:
    device_id = str(getattr(principal, "device_id", "") or "").strip()
    return f"{principal.subject}:{device_id}" if device_id else str(principal.subject)


def _authorize_desktop_live(request: Request, principal, path: str, body: dict, query: dict) -> dict:
    """Bind every desktop-live operation to the Engine-issued viewer identity."""
    from core.desktop_live import desktop_live_service

    if (path == "desktop-live/status" and request.method == "GET") or (path == "desktop-live/prepare" and request.method == "POST"):
        return body
    if path == "desktop-live/session" and request.method == "POST":
        body = dict(body)
        body["viewer_id"] = _desktop_viewer_id(principal)
        return body
    path_session_id = path.removeprefix("desktop-live/session/") if path.startswith("desktop-live/session/") else ""
    session_id = str(path_session_id or body.get("sessionId") or body.get("session_id") or query.get("sessionId") or query.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(400, "sessionId_required")
    try:
        session = desktop_live_service.touch_session(session_id)
    except Exception as exc:
        raise HTTPException(404, "desktop_live_session_not_found") from exc
    if str(session.viewer_id) != _desktop_viewer_id(principal):
        raise HTTPException(403, "desktop_live_viewer_mismatch")
    if path in {"desktop-live/offer", "desktop-live/candidate"}:
        body = dict(body)
        body["session_id"] = session_id
        body["viewer_id"] = _desktop_viewer_id(principal)
    return body


def resolve_client_target(path: str, method: str) -> str | None:
    for pattern, methods, target in _SIMPLE_ROUTES:
        match = re.fullmatch(pattern, path)
        if match and method in methods:
            return match.expand(target) if target else "/" + path
    return None


async def _authorize_target(request, principal, path: str, payload: dict, query: dict):
    session_id = str(query.get("sessionId") or query.get("session_id") or payload.get("sessionId") or payload.get("session_id") or request.headers.get("x-v8-session-id") or "")
    parts = path.split("/")
    if parts[0] == "sessions":
        session_id = parts[1]
    elif len(parts) > 1 and parts[0] in {"runs", "approvals", "ask-user", "messages", "chat-queue"}:
        method = {"runs": "get_run_record", "approvals": "get_pending_approval", "ask-user": "get_ask_user_interaction",
                  "messages": "get_message", "chat-queue": "get_chat_user_message_queue_item"}[parts[0]]
        session_id = _record_session(request, principal, method, parts[1])
    if session_id:
        require_session(request, principal, session_id)
    if parts[0] in {"artifacts", "sources"} and not session_id:
        raise HTTPException(400, "sessionId_required")
    if parts[0] == "ui-actions" and not session_id:
        raise HTTPException(400, "sessionId_required")
    if parts[0] == "mcp-apps":
        from api.platform_routes import mcp_manager
        instance_id = parts[2] if len(parts) > 2 and parts[1] == "instances" else query.get("appInstanceId", "")
        instance = mcp_manager.get_app_instance(instance_id)
        if not instance or instance.get("status") == "closed":
            raise HTTPException(404, "mcp_app_instance_unavailable")
        require_session(request, principal, str(instance.get("sessionId") or ""))
        if parts[1] == "resources" and (query.get("serverName") != instance.get("serverName") or query.get("uri") != instance.get("resourceUri")):
            raise HTTPException(403, "mcp_app_resource_mismatch")
    return session_id


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def client_api(request: Request, path: str):
    principal = None
    if request.method in {"GET", "HEAD"} and request.query_params.get("v8sig"):
        from core.client_identity import get_identity_service
        from core.client_identity.resources import verify_resource_request
        principal = verify_resource_request(get_identity_service(), request.url.path + "?" + request.url.query, method=request.method)
        if principal is None:
            raise HTTPException(401, "resource_capability_invalid")
    if principal is None:
        try:
            principal = require_client_principal(request)
        except HTTPException as exc:
            if exc.status_code == 401:
                exc.headers = {**(exc.headers or {}), "x-v8-auth-stage": "pre_execution"}
            raise
    if request.scope.get("state", {}).get("phone_gateway") and principal.device_kind == "local_client":
        raise HTTPException(403, "phone_device_credential_required")
    query = dict(request.query_params)
    method = request.method
    if path == "runtime/bridge" and method == "GET":
        if not is_local_client(principal):
            raise HTTPException(403, "local_client_required")
        return {"bridgeMode": "engine_direct", "workspaceAssetBaseUrl": "/api/workspace/files"}
    if path == "ui-preferences/theme" and method in {"GET", "PUT"}:
        if not is_local_client(principal):
            raise HTTPException(403, "local_client_required")
        body = await _payload(request) if method == "PUT" else {}
        if method == "PUT" and body.get("theme") not in {"light", "dark", "system"}:
            raise HTTPException(400, "invalid_theme")
        result = await internal_json(request, principal, "/config-registry/ui", method="POST" if method == "PUT" else "GET",
                                     payload={"data": {"theme": body["theme"]}} if method == "PUT" else None)
        theme = _json_object(result.get("data", result)).get("theme", "system")
        return {"theme": theme if theme in {"light", "dark", "system"} else "system"}
    if path == "chat-queue" and method == "GET":
        require_session(request, principal, query.get("session_id", ""))
        return await internal_json(request, principal, "/chat/queued-messages", query=query)
    if path.startswith("workspace/files/") and method in {"GET", "HEAD"}:
        from core.scoped_workspace_resource import normalize_workspace_relative_path
        try:
            relative = normalize_workspace_relative_path(path.removeprefix("workspace/files/"))
        except ValueError as exc:
            raise HTTPException(400, "workspace_resource_path_invalid") from exc
        if query.get("sessionId"):
            require_session(request, principal, query["sessionId"])
        resource_query = {"workspace_relative_path": relative, "path_plane": "workspace_download"}
        for key in ("workspace_id", "project_id"):
            if query.get(key):
                resource_query[key] = query[key]
        return InternalResponse(request, principal, "/workspace/resource", query=resource_query)
    if path == "link/manifest" and method == "GET":
        from core.client_identity import get_identity_service
        return get_identity_service().manifest(str(request.base_url).rstrip("/"))
    if path == "supervisor-profile" and method == "GET":
        data = await internal_json(request, principal, "/config-registry/supervisor")
        profile = _json_object(_json_object(data.get("data")).get("profile"))
        return {"name": str(profile.get("name") or "智能主管"), "roleLabel": str(profile.get("roleLabel") or "主理人"), "avatar": str(profile.get("avatar") or "")}
    if path == "desktop-pet/config" and method == "GET":
        if not is_local_client(principal):
            raise HTTPException(403, "local_client_required")
        return await internal_json(request, principal, "/config-registry/desktop-pet")
    if re.fullmatch(rf"(?:terminal|bg_processes)(?:/{SEGMENT})*", path):
        if not _allow_local_or_phone(principal):
            raise HTTPException(403, "local_or_phone_client_required")
        # Match a real native route as well as a narrow local-only prefix. This
        # retains method/schema/owner checks without exposing other /v1 APIs.
        from starlette.routing import Match
        from core.client_transport import internal_scope
        scoped = internal_scope(request, principal, "/" + path)
        native = getattr(request.app.state, "client_internal_router", None)
        if native is None or not any(route.matches(scoped)[0] == Match.FULL for route in native.routes):
            raise HTTPException(404, "client_route_not_found")
        body = await _payload(request) if method in {"POST", "PUT", "PATCH"} else {}
        return InternalResponse(request, principal, "/" + path,
                                body=json.dumps(body).encode() if body else None)
    if re.fullmatch(rf"(?:rpa|desktop-live)(?:/{SEGMENT})*", path):
        if not _allow_local_or_phone(principal):
            raise HTTPException(403, "local_or_phone_client_required")
        body = await _payload(request) if method in {"POST", "PUT", "PATCH"} and "application/json" in request.headers.get("content-type", "") else {}
        query = dict(request.query_params)
        if path.startswith("desktop-live"):
            body = _authorize_desktop_live(request, principal, path, body, query)
        elif method in {"POST", "PUT", "PATCH"}:
            # RPA run/compile payloads carry a session binding. Preserve the
            # native schema while making the Engine principal authoritative.
            session_id = str(body.get("sessionId") or body.get("session_id") or "").strip()
            if session_id:
                require_session(request, principal, session_id)
            if _is_phone_principal(principal) and (path == "rpa/compile" or path.startswith("rpa/compile/")):
                run_ids = [path.rsplit("/", 1)[-1]] if path.startswith("rpa/compile/") else body.get("runIds", body.get("run_ids", []))
                if not isinstance(run_ids, list):
                    raise HTTPException(400, "runIds_invalid")
                for run_id in run_ids:
                    _record_session(request, principal, "get_run_record", str(run_id))
            if _is_phone_principal(principal):
                body = dict(body)
                body["userId"] = body["user_id"] = principal.subject
        from starlette.routing import Match
        from core.client_transport import internal_scope
        target_path = "/" + path
        # Web's historical release adapter maps to the canonical Engine delete.
        if path == "desktop-live/release" and method == "POST":
            target_path = "/desktop-live/session/" + str(body.get("sessionId") or body.get("session_id") or "")
            method = "DELETE"
        scoped = internal_scope(request, principal, target_path, method=method, query=query)
        native = getattr(request.app.state, "client_internal_router", None)
        if native is None or not any(route.matches(scoped)[0] == Match.FULL for route in native.routes):
            raise HTTPException(404, "client_route_not_found")
        return InternalResponse(request, principal, target_path, method=method,
                                body=json.dumps(body).encode() if body else None,
                                query=query)
    if path.startswith("runs/") and method == "GET":
        run_id = path.split("/", 1)[1]
        record = _database(request).get_run_record(run_id)
        if not record:
            raise HTTPException(404, "run_not_found")
        session_id = str(record.get("session_id") or record.get("sessionId") or "")
        requested_session = str(query.get("sessionId") or query.get("session_id") or "")
        if requested_session and requested_session != session_id:
            raise HTTPException(404, "run_not_found")
        require_session(request, principal, session_id)
        return normalize_client_surface(record, request, principal)
    if path == "config-distribution" or re.fullmatch(rf"config-distribution/{SEGMENT}(?:/{SEGMENT})?", path):
        from api.config_distribution_routes import distribution_request
        return await distribution_request(request, principal, path.removeprefix("config-distribution").lstrip("/"))
    if re.fullmatch(rf"supervisor-peers(?:/{SEGMENT}(?:/timeline)?)?", path):
        from api.client_peers import client_peers
        return await client_peers(request, principal, path)
    if path == "connection" and method == "GET":
        from core.client_identity import get_identity_service
        service = get_identity_service()
        manifest = service.manifest(str(request.base_url).rstrip("/"))
        return {"connection": {"adminBaseUrl": manifest["admin"]["baseUrl"], "adminApiBaseUrl": manifest["admin"]["apiBaseUrl"],
                "bridgeMode": "engine_direct", "reachable": True, "transportKind": manifest["transportKind"],
                "transportProfileId": manifest.get("activeProfileId", ""), "linkManifest": manifest,
                "vpnDiagnostics": manifest.get("diagnostics", {})}, "linkManifest": manifest,
                "user": service.client_user(principal)}
    if path == "conversations":
        if method == "GET":
            return await _session_index(request, principal)
        if method == "POST":
            body = await _payload(request)
            body["userId"] = body["user_id"] = principal.session_id
            # Do not let a create request overwrite someone else's existing ID.
            existing_id = str(body.get("id") or body.get("sessionId") or body.get("session_id") or "")
            if existing_id:
                require_session(request, principal, existing_id, allow_new=True)
            return await internal_json(request, principal, "/sessions", method="POST", payload=body)
        if method == "DELETE":
            items = await _session_index(request, principal)
            if isinstance(items, dict):
                raise HTTPException(400, "clear_sessions_cannot_be_paginated")
            deleted = 0
            for item in items:
                await internal_json(request, principal, f"/sessions/{item['id']}", method="DELETE")
                deleted += 1
            return {"success": True, "deleted": deleted}
    match = re.fullmatch(rf"conversations/({SEGMENT})(?:/(turns|turn-index|sync))?", path)
    if match:
        session_id, action = match.groups()
        require_session(request, principal, session_id)
        target = f"/sessions/{session_id}"
        if method == "GET":
            if not action:
                result = await _conversation_detail(request, principal, session_id)
            else:
                suffix = "timeline/sync" if action == "sync" else action
                result = await internal_json(request, principal, f"{target}/{suffix}", query=query)
            return normalize_client_surface(result, request, principal)
        if not action and method in {"PATCH", "DELETE"}:
            body = await _payload(request) if method == "PATCH" else None
            if body is not None:
                body = {key: value for key, value in body.items() if key in {"title", "pinned", "supervisorWorkMode", "supervisorRuntimeMode"}}
                body["userId"] = principal.session_id
            return await internal_json(request, principal, target, method=method, payload=body)
    if path in {"chat-submit", "chat"} and method == "POST":
        body = build_chat_payload(await _payload(request), principal)
        require_session(request, principal, body["session_id"], allow_new=True)
        target = "/chat/submit" if path == "chat-submit" else "/chat/stream"
        return InternalResponse(request, principal, target, body=json.dumps(body).encode())
    match = re.fullmatch(rf"chat-queue/({SEGMENT})(/promote)?", path)
    if match and method in {"PATCH", "DELETE", "POST"}:
        _record_session(request, principal, "get_chat_user_message_queue_item", match[1])
        return InternalResponse(request, principal, f"/chat/queued-messages/{match[1]}{match[2] or ''}")
    if path == "upload" and method == "POST":
        # Cache bytes before parsing so the same multipart boundary/body reaches
        # the native upload handler. Its workspace side-effect guard still runs.
        raw = await request.body()
        form = await request.form()
        try:
            session_id = str(form.get("sessionId") or form.get("session_id") or form.get("conversationId") or "")
            if session_id:
                require_session(request, principal, session_id)
        finally:
            # Starlette stores multipart uploads in SpooledTemporaryFile. The
            # adapter only needs the scalar session binding; close every file
            # before forwarding the original bytes to the native handler.
            for value in form.values():
                close = getattr(value, "close", None)
                if close is not None:
                    close_result = close()
                    if hasattr(close_result, "__await__"):
                        await close_result
        result = await internal_json(request, principal, "/chat/upload", method="POST", raw_body=raw)
        return normalize_client_surface(result, request, principal)
    if path.startswith("realtime/") and method == "GET":
        from api.client_realtime import client_realtime
        return await client_realtime(request, principal, path)
    target = resolve_client_target(path, method)
    if target:
        if path == "runs" and query.get("sessionId") and query.get("session_id") and query["sessionId"] != query["session_id"]:
            raise HTTPException(400, "session_filter_conflict")
        body = await _payload(request) if method in {"POST", "PUT", "PATCH"} and "application/json" in request.headers.get("content-type", "") else {}
        session_id = await _authorize_target(request, principal, path, body, query)
        if path == "runs" and "sessionId" in query:
            query["session_id"] = query.pop("sessionId")
        if path == "sources":
            query["session_id"] = session_id
            query.pop("sessionId", None)
        if path.startswith("artifacts") and "runId" in query:
            query["run_id"] = query.pop("runId")
        if path == "approvals" and method == "GET" and not session_id:
            result = await internal_json(request, principal, target, query=query)
            approvals = result.get("approvals", [])
            result["approvals"] = [item for item in approvals if _owner_matches(_database(request).get_session(str(item.get("session_id") or item.get("sessionId") or "")) or {}, principal)]
            return result
        binary = path == "workspace/resource" or path.endswith("/content") or path in {"audio/stt", "audio/tts"}
        if binary:
            return InternalResponse(request, principal, target, query=query,
                                    body=json.dumps(body).encode() if body else None)
        result = await internal_json(request, principal, target, method=method, query=query, payload=body if method in {"POST", "PUT", "PATCH"} else None)
        if path == "runs" and isinstance(result, dict) and isinstance(result.get("runs"), list):
            result["runs"] = [item for item in result["runs"] if _owner_matches(_database(request).get_session(str(item.get("session_id") or item.get("sessionId") or "")) or {}, principal)]
        return normalize_client_surface(result, request, principal)
    raise HTTPException(404, "client_route_not_found")
