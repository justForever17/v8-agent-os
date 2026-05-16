from typing import Any
import hashlib
import json
from urllib.parse import urlparse
import uuid

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .models import ModelConnectionTestPayload
from core.database import db
from core.extensions_runtime import extensions_runtime_service
from core.agents import build_specialist_family_registry
from core.json_safe import to_jsonable
from core.model_connection_tester import model_connection_tester
from core.model_control_plane import model_control_plane
from core.model_provider_catalog import model_provider_catalog
from core.model_role_doctor import diagnose_models
from core.prompt_cache_gateway import prompt_cache_profile_id_for_provider
from core.model_telemetry import model_telemetry_service
from core.skills_install_service import SkillInstallValidationError, install_skill_from_command, install_skills_from_zip
from core.storage import storage
from core.realtime_protocol import build_runtime_event
from erc.command_service import command_service
from erc.models import ApprovalRequest
from erc.safety_guardian import safety_guardian
from core.tools.research_ledger import (
    archive_experience_pack,
    delete_experience_pack,
    list_evidence_bundles,
    promote_experience_pack,
    research_ledger_summary,
    restore_experience_pack,
    search_experience_packs_with_options,
)
from runtimes.extensions.mcp.client import mcp_manager


router = APIRouter()

_ACTIVE_MCP_APP_GUIDANCE_STATUSES = {"queued", "running", "waiting_approval", "waiting_input", "waiting_external_tool", "paused"}


class McpConfigValidationError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def _credential_realm(provider_id: str, provider_meta: dict[str, Any] | None = None) -> str:
    meta = provider_meta or {}
    explicit = str(meta.get("credentialRealm") or meta.get("credential_realm") or "").strip()
    if explicit:
        return explicit
    probe = " ".join(
        [
            str(provider_id or ""),
            str(meta.get("id") or ""),
            str(meta.get("name") or ""),
            str(meta.get("base_url") or meta.get("baseUrl") or ""),
            str(meta.get("api_standard") or meta.get("apiStandard") or ""),
        ]
    ).lower()
    if "volces.com" in probe or "volcengine" in probe or "doubao" in probe or "ark.cn-" in probe:
        return "volcengine_ark"
    if "xiaomimimo" in probe or "mimo" in probe:
        return "xiaomi_mimo"
    if "deepseek" in probe:
        return "deepseek"
    return ""


def _stored_provider_credential(provider_id: str, catalog_provider: dict[str, Any] | None) -> tuple[str, str]:
    config = model_control_plane.get_config()
    providers = config.get("providers") or {}
    exact_provider = ((providers.get(provider_id) or {}).get("provider") or {}) if isinstance(providers, dict) else {}
    exact_key = str(exact_provider.get("api_key") or "").strip()
    if exact_key and not exact_key.startswith("oauth:"):
        return exact_key, "stored_provider"
    target_realm = _credential_realm(provider_id, catalog_provider)
    if not target_realm or not isinstance(providers, dict):
        return "", ""
    for saved_id, payload in providers.items():
        saved_provider = ((payload or {}).get("provider") or {}) if isinstance(payload, dict) else {}
        stored_key = str(saved_provider.get("api_key") or "").strip()
        if not stored_key or stored_key.startswith("oauth:"):
            continue
        if _credential_realm(str(saved_id), saved_provider) == target_realm:
            return stored_key, f"stored_provider_realm:{saved_id}"
    return "", ""


def _validate_mcp_server_map(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(config, dict):
        raise McpConfigValidationError("invalid_payload", "MCP 配置必须是 JSON 对象。")

    server_map = config.get("mcpServers") if "mcpServers" in config else config
    if not isinstance(server_map, dict):
        raise McpConfigValidationError("invalid_server_map", "`mcpServers` 必须是对象映射。")
    if not server_map:
        raise McpConfigValidationError("empty_server_map", "MCP 配置中至少需要包含一个 server。")

    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_server in server_map.items():
        server_name = str(raw_name or "").strip()
        if not server_name:
            raise McpConfigValidationError("empty_server_name", "MCP server 名称不能为空。")
        if not isinstance(raw_server, dict):
            raise McpConfigValidationError(
                "invalid_server_payload",
                f"MCP server `{server_name}` 的配置必须是对象。",
            )

        server = dict(raw_server)
        if "command" in server and not isinstance(server.get("command"), str):
            raise McpConfigValidationError("invalid_command", f"MCP server `{server_name}` 的 command 必须是字符串。")
        if "url" in server and not isinstance(server.get("url"), str):
            raise McpConfigValidationError("invalid_url", f"MCP server `{server_name}` 的 url 必须是字符串。")
        if "args" in server and not isinstance(server.get("args"), list):
            raise McpConfigValidationError("invalid_args", f"MCP server `{server_name}` 的 args 必须是数组。")
        if "env" in server and not isinstance(server.get("env"), dict):
            raise McpConfigValidationError("invalid_env", f"MCP server `{server_name}` 的 env 必须是对象。")
        if "headers" in server and not isinstance(server.get("headers"), dict):
            raise McpConfigValidationError("invalid_headers", f"MCP server `{server_name}` 的 headers 必须是对象。")

        disabled = bool(server.get("disabled", False))
        command = str(server.get("command") or "").strip()
        url = str(server.get("url") or "").strip()
        if not disabled and not command and not url:
            raise McpConfigValidationError(
                "missing_target",
                f"MCP server `{server_name}` 至少需要提供 command 或 url。",
            )
        normalized[server_name] = server

    return normalized


@router.get("/mcp/config")
async def get_mcp_config():
    try:
        return storage.get_mcp_config() or {"mcpServers": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mcp/status")
async def get_mcp_status():
    try:
        return {"servers": extensions_runtime_service.get_mcp_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mcp/apps/registry")
async def get_mcp_apps_registry():
    try:
        return mcp_manager.get_app_registry()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mcp/apps/resources/read")
async def read_mcp_app_resource(serverName: str, uri: str):
    try:
        return await mcp_manager.read_app_resource(server_name=serverName, uri=uri)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _json_rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json_rpc_error(request_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message, **({"data": data} if data else {})},
    }


def _mcp_app_emit_event(topic: str, *, session_id: str, run_id: str | None, payload: dict[str, Any]) -> dict[str, Any] | None:
    if not session_id:
        return None
    event = build_runtime_event(
        kind="event",
        topic=topic,
        session_id=session_id,
        run_id=run_id,
        seq=db.get_next_runtime_seq(session_id),
        payload=payload,
        source={
            "plane": "engine",
            "component": "mcp_apps",
            "node": "mcp_app_host",
            "agent_id": None,
        },
    )
    db.add_runtime_event(event)
    return event


def _mcp_app_context_to_text(params: dict[str, Any]) -> str:
    content = params.get("content")
    parts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("content") or "").strip()
                if text:
                    parts.append(text)
            elif item is not None:
                text = str(item).strip()
                if text:
                    parts.append(text)
    elif isinstance(content, str):
        parts.append(content.strip())
    elif content is not None:
        parts.append(json.dumps(to_jsonable(content), ensure_ascii=False, separators=(",", ":")))
    if not parts:
        for key in ("text", "message", "summary"):
            value = str(params.get(key) or "").strip()
            if value:
                parts.append(value)
                break
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        text = json.dumps(to_jsonable(params), ensure_ascii=False, separators=(",", ":"))
    if len(text) > 4000:
        return f"{text[:4000]}\n[omitted {len(text) - 4000} chars from MCP App context update]"
    return text


def _mcp_app_queue_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "sessionId": item.get("session_id"),
        "runId": item.get("run_id"),
        "clientMessageId": item.get("client_message_id"),
        "content": item.get("content") or "",
        "state": item.get("state") or "pending",
        "ordinal": item.get("ordinal"),
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
        "promotedAt": item.get("promoted_at"),
        "injectedAt": item.get("injected_at"),
        "consumedAt": item.get("consumed_at"),
        "cancelledAt": item.get("cancelled_at"),
    }


def _mcp_app_operation_fingerprint(
    *,
    app_instance_id: str,
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    payload = {
        "kind": "mcp_app_tool_call",
        "appInstanceId": app_instance_id,
        "serverName": server_name,
        "toolName": tool_name,
        "arguments": to_jsonable(arguments),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mcp_app_approval_matches(
    approval: dict[str, Any] | None,
    *,
    app_instance_id: str,
    tool_name: str,
    fingerprint: str,
) -> bool:
    if not approval:
        return False
    request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
    if str(approval.get("approval_kind") or "") != "mcp_app_tool_call":
        return False
    return (
        str(request.get("operationFingerprint") or "") == fingerprint
        and str(request.get("appInstanceId") or "") == app_instance_id
        and str(request.get("toolName") or "") == tool_name
    )


def _mcp_app_has_approved_operation(run_id: str, fingerprint: str) -> bool:
    if not run_id or not fingerprint:
        return False
    run_record = db.get_run_record(run_id) or {}
    metadata = dict(run_record.get("metadata") or {})
    for item in list(metadata.get("approvedSafetyOperations") or []):
        if isinstance(item, dict) and str(item.get("fingerprint") or "") == fingerprint:
            return True
    return False


def _mcp_app_request_tool_approval(
    *,
    instance: dict[str, Any],
    app_instance_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    safety_payload: dict[str, Any],
    fingerprint: str,
) -> dict[str, Any]:
    session_id = str(instance.get("sessionId") or "").strip()
    run_id = str(instance.get("runId") or "").strip()
    if not session_id or not run_id:
        raise ValueError("MCP app approval requires an attached chat session and run.")
    server_name = str(instance.get("serverName") or "").strip()
    approval = command_service.request_approval(
        ApprovalRequest(
            approval_id=f"approval_mcpapp_{uuid.uuid4().hex}",
            session_id=session_id,
            run_id=run_id,
            approval_kind="mcp_app_tool_call",
            request={
                "approvalKind": "mcp_app_tool_call",
                "question": f"MCP App 请求调用工具 `{tool_name}`，需要安全复核。是否允许继续？",
                "prompt": f"MCP App 请求调用工具 `{tool_name}`，需要安全复核。是否允许继续？",
                "summary": f"MCP App `{server_name}` 请求调用 `{tool_name}`。",
                "runtimeKind": "mcp_app",
                "targetSurface": "governance_hud",
                "operationFingerprint": fingerprint,
                "operationTargetFingerprint": f"mcp_app:{app_instance_id}:{tool_name}",
                "appInstanceId": app_instance_id,
                "serverName": server_name,
                "resourceUri": instance.get("resourceUri"),
                "toolName": tool_name,
                "argumentsPreview": to_jsonable(arguments),
                "safety": safety_payload,
                "riskCode": safety_payload.get("riskCode") or safety_payload.get("risk_code"),
                "eventSummary": safety_payload.get("eventSummary") or {},
            },
        )
    )
    _mcp_app_emit_event(
        "approval.requested",
        session_id=session_id,
        run_id=run_id,
        payload=approval,
    )
    return approval


@router.post("/mcp/apps/instances/{app_instance_id}/rpc")
async def mcp_app_instance_rpc(app_instance_id: str, payload: dict = Body(...)):
    request_id = payload.get("id")
    method = str(payload.get("method") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    instance = mcp_manager.get_app_instance(app_instance_id)
    if not instance:
        return _json_rpc_error(request_id, -32004, "Unknown MCP app instance")

    try:
        if method == "ui/initialize":
            return _json_rpc_result(
                request_id,
                {
                    "appInstanceId": app_instance_id,
                    "serverName": instance.get("serverName"),
                    "toolName": instance.get("toolName"),
                    "resourceUri": instance.get("resourceUri"),
                    "initialToolResult": instance.get("initialToolResult"),
                    "permissions": instance.get("permissions") or {},
                },
            )

        if method == "tools/call":
            tool_name = str(params.get("name") or params.get("toolName") or "").strip()
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            if not tool_name:
                return _json_rpc_error(request_id, -32602, "Missing tool name")
            fingerprint = _mcp_app_operation_fingerprint(
                app_instance_id=app_instance_id,
                server_name=str(instance.get("serverName") or ""),
                tool_name=tool_name,
                arguments=arguments,
            )
            approval_id = str(params.get("approvalId") or params.get("approval_id") or "").strip()
            if approval_id:
                approval = db.get_pending_approval(approval_id)
                if not _mcp_app_approval_matches(
                    approval,
                    app_instance_id=app_instance_id,
                    tool_name=tool_name,
                    fingerprint=fingerprint,
                ):
                    return _json_rpc_error(request_id, -32012, "Approval does not match this MCP app tool call")
                approval_status = str((approval or {}).get("status") or "").strip().lower()
                if approval_status == "pending":
                    return _json_rpc_result(
                        request_id,
                        {
                            "ok": False,
                            "status": "waiting_approval",
                            "approvalId": approval_id,
                            "message": "MCP app tool call is still waiting for V8 Safety approval.",
                        },
                    )
                if approval_status == "rejected":
                    return _json_rpc_error(request_id, -32013, "MCP app tool call approval was rejected")
                if approval_status != "approved":
                    return _json_rpc_error(request_id, -32014, f"Unsupported MCP app approval status: {approval_status}")
            decision = safety_guardian.assess_external_tool_call(
                tool_name=tool_name,
                params=arguments,
                tool_kind="mcp_app_tool",
                side_effect=str(params.get("sideEffect") or ""),
                runtime_context={
                    "runtime_kind": "mcp_app",
                    "serverName": instance.get("serverName"),
                    "appInstanceId": app_instance_id,
                },
            )
            if decision.is_block():
                return _json_rpc_error(
                    request_id,
                    -32010,
                    "MCP app tool call was blocked by V8 Safety",
                    {"safety": decision.to_payload()},
                )
            if decision.is_review() and not approval_id and not _mcp_app_has_approved_operation(
                str(instance.get("runId") or ""),
                fingerprint,
            ):
                approval = _mcp_app_request_tool_approval(
                    instance=instance,
                    app_instance_id=app_instance_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    safety_payload=decision.to_payload(),
                    fingerprint=fingerprint,
                )
                return _json_rpc_result(
                    request_id,
                    {
                        "ok": False,
                        "status": "waiting_approval",
                        "approvalId": approval.get("approval_id"),
                        "safety": decision.to_payload(),
                        "message": "MCP app tool call is waiting for V8 Safety approval. Retry this tools/call with approvalId after approval.",
                    },
                )
            result = await mcp_manager.call_app_tool(
                app_instance_id=app_instance_id,
                tool_name=tool_name,
                arguments=arguments,
            )
            return _json_rpc_result(
                request_id,
                {
                    **result,
                    "toolInvocationId": f"mcpapp_{uuid.uuid4().hex[:24]}",
                },
            )

        if method in {"ui/notifications/tool-result", "ui/notifications/tool-input", "ui/notifications/log"}:
            return _json_rpc_result(request_id, {"ok": True, "acknowledged": method})

        if method == "ui/updateModelContext":
            session_id = str(instance.get("sessionId") or "").strip()
            run_id = str(instance.get("runId") or "").strip()
            if not session_id:
                return _json_rpc_error(request_id, -32015, "MCP app instance is not attached to a chat session")
            content = _mcp_app_context_to_text(params)
            queue_id = f"queued_mcpapp_{uuid.uuid4().hex}"
            queue_item = db.add_chat_user_message_queue_item(
                queue_id=queue_id,
                session_id=session_id,
                run_id=run_id or None,
                client_message_id=f"mcpapp_context_{app_instance_id}_{uuid.uuid4().hex[:8]}",
                content=f"MCP App 上下文更新：\n{content}",
                attachments=[],
                file_urls=[],
                request_payload={
                    "source": "mcp_app.updateModelContext",
                    "appInstanceId": app_instance_id,
                    "serverName": instance.get("serverName"),
                    "resourceUri": instance.get("resourceUri"),
                    "params": to_jsonable(params),
                },
                metadata={
                    "source": "mcp_app.context_update",
                    "appInstanceId": app_instance_id,
                    "serverName": instance.get("serverName"),
                    "resourceUri": instance.get("resourceUri"),
                    "toolInvocationId": instance.get("toolInvocationId"),
                },
            )
            run_record = db.get_run_record(run_id) if run_id else None
            run_status = str((run_record or {}).get("status") or "").strip().lower()
            if run_record and run_status in _ACTIVE_MCP_APP_GUIDANCE_STATUSES:
                promoted = db.update_chat_user_message_queue_item(
                    queue_id,
                    state="promoted",
                    timestamp_field="promoted_at",
                    metadata_updates={"promotedBy": "mcp_app.updateModelContext"},
                ) or queue_item
                queue_payload = _mcp_app_queue_payload(promoted)
                command_service.issue_control_signal(
                    run_id,
                    command="guidance",
                    reason="mcp_app_context_update",
                    payload={"queueMessageId": queue_id, "source": "mcp_app.updateModelContext"},
                )
                _mcp_app_emit_event(
                    "human_guidance.promoted",
                    session_id=session_id,
                    run_id=run_id,
                    payload={
                        "queueMessage": queue_payload,
                        "state": "promoted",
                        "summary": "MCP App 上下文更新已提升为运行中引导，将在安全检查点注入。",
                        "source": "mcp_app.updateModelContext",
                    },
                )
                return _json_rpc_result(
                    request_id,
                    {
                        "ok": True,
                        "queued": True,
                        "promoted": True,
                        "event": "human_guidance.promoted",
                        "queueMessageId": queue_id,
                        "appInstanceId": app_instance_id,
                    },
                )
            _mcp_app_emit_event(
                "human_guidance.queued",
                session_id=session_id,
                run_id=run_id or None,
                payload={
                    "queueMessage": _mcp_app_queue_payload(queue_item),
                    "state": "pending",
                    "summary": "MCP App 上下文更新已排队，将在后续可用时作为引导处理。",
                    "source": "mcp_app.updateModelContext",
                },
            )
            return _json_rpc_result(
                request_id,
                {
                    "ok": True,
                    "queued": True,
                    "promoted": False,
                    "event": "human_guidance.queued",
                    "queueMessageId": queue_id,
                    "appInstanceId": app_instance_id,
                },
            )

        if method == "ui/openLink":
            url = str(params.get("url") or "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return _json_rpc_error(request_id, -32011, "Only http/https links can be opened")
            return _json_rpc_result(request_id, {"ok": True, "action": "openLink", "url": url})

        return _json_rpc_error(request_id, -32601, f"Unsupported MCP app RPC method: {method}")
    except ValueError as e:
        return _json_rpc_error(request_id, -32000, str(e))
    except Exception as e:
        return _json_rpc_error(request_id, -32603, str(e))


@router.post("/mcp/apps/instances/{app_instance_id}/close")
async def close_mcp_app_instance(app_instance_id: str):
    return mcp_manager.close_app_instance(app_instance_id)


@router.post("/mcp/config")
async def update_mcp_config(config: dict = Body(...)):
    try:
        new_servers = _validate_mcp_server_map(config)
        existing = storage.get_mcp_config() or {"mcpServers": {}}
        existing_servers = existing.get("mcpServers", {})
        existing_servers.update(new_servers)
        storage.save_mcp_config({"mcpServers": existing_servers})
        inventory_refresh = await extensions_runtime_service.refresh_inventory_if_changed(reason="mcp_config_update")
        runtime_health = extensions_runtime_service.build_health()
        return {"status": "success", "extensionsRuntime": runtime_health, "inventoryRefresh": inventory_refresh}
    except McpConfigValidationError as e:
        raise HTTPException(status_code=400, detail=e.to_payload())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/mcp/config/{server_name}")
async def delete_mcp_config_server(server_name: str):
    normalized_name = str(server_name or "").strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail={"code": "empty_server_name", "message": "MCP server 名称不能为空。"})
    try:
        existing = storage.get_mcp_config() or {"mcpServers": {}}
        existing_servers = existing.get("mcpServers", {})
        if not isinstance(existing_servers, dict):
            existing_servers = {}
        already_removed_from_config = normalized_name not in existing_servers
        if already_removed_from_config:
            next_servers = dict(existing_servers)
        else:
            next_servers = dict(existing_servers)
            next_servers.pop(normalized_name, None)
            storage.save_mcp_config({"mcpServers": next_servers})
        reload_result = await mcp_manager.reload_if_changed()
        if normalized_name not in set((reload_result.get("mcpChangedServers") or {}).get("removed") or []):
            removal_result = await mcp_manager.remove_server(normalized_name)
        else:
            removal_result = {"changed": True, "server": normalized_name, "reason": "delta_reload_removed"}
        inventory_refresh = await extensions_runtime_service.force_refresh_after_mcp_config_change(
            reason="mcp_config_delete",
            mcp_change={
                "reloadResult": reload_result,
                "removeResult": removal_result,
                "deletedServer": normalized_name,
                "alreadyRemovedFromConfig": already_removed_from_config,
            },
        )
        runtime_health = extensions_runtime_service.build_health()
        return {
            "status": "success",
            "deletedServer": normalized_name,
            "alreadyRemovedFromConfig": already_removed_from_config,
            "reloadResult": reload_result,
            "removeResult": removal_result,
            "extensionsRuntime": runtime_health,
            "inventoryRefresh": inventory_refresh,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/workspace")
async def get_workspace_config():
    try:
        return storage.get_workspace_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config/workspace")
async def update_workspace_config(config: dict = Body(...)):
    try:
        storage.save_workspace_config(config)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/reload")
async def reload_system():
    try:
        health = await extensions_runtime_service.reload()
        return {"status": "success", **health}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/list")
async def get_skills_list():
    try:
        supervisor_config = storage.get_supervisor_config() or {}
        return {
            "skills": list(extensions_runtime_service.list_skills(force_refresh=False)),
            "subagentFamilies": build_specialist_family_registry(
                storage.get_all_agents(),
                supervisor_config.get("specialistRegistry") if isinstance(supervisor_config.get("specialistRegistry"), dict) else {},
            ),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/safety/reviews")
async def list_skill_safety_reviews(status: str | None = None, limit: int = 100):
    try:
        from erc.safety_guardian import safety_guardian

        return {"items": safety_guardian.list_skill_safety_reviews(status=status, limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/safety/reviews/{review_id}/approve")
async def approve_skill_safety_review(review_id: str):
    try:
        from erc.safety_guardian import safety_guardian

        review = safety_guardian.approve_skill_safety_review(review_id)
        if not review:
            raise HTTPException(status_code=404, detail="skill safety review not found")
        extensions_runtime_service.request_skill_inventory_refresh(reason="skill_safety_approved")
        return {"status": "success", "review": review}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/safety/reviews/{review_id}/disable")
async def disable_skill_safety_review(review_id: str):
    try:
        from erc.safety_guardian import safety_guardian

        review = safety_guardian.disable_skill_safety_review(review_id)
        if not review:
            raise HTTPException(status_code=404, detail="skill safety review not found")
        extensions_runtime_service.request_skill_inventory_refresh(reason="skill_safety_disabled")
        return {"status": "success", "review": review}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/safety/reviews/{review_id}/revoke")
async def revoke_skill_safety_review(review_id: str):
    try:
        from erc.safety_guardian import safety_guardian

        review = safety_guardian.revoke_skill_safety_review(review_id)
        if not review:
            raise HTTPException(status_code=404, detail="skill safety review not found")
        extensions_runtime_service.request_skill_inventory_refresh(reason="skill_safety_revoked")
        return {"status": "success", "review": review}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/safety/reviews/{review_id}/rescan")
async def rescan_skill_safety_review(review_id: str):
    try:
        from erc.safety_guardian import safety_guardian

        review = safety_guardian.rescan_skill_safety_review(review_id)
        if not review:
            raise HTTPException(status_code=404, detail="skill safety review not found")
        extensions_runtime_service.request_skill_inventory_refresh(reason="skill_safety_rescan")
        return {"status": "success", "review": review}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/install/command")
async def install_skill_command(command: str = Body(..., embed=True)):
    try:
        result = install_skill_from_command(command)
        extensions_runtime_service.request_skill_inventory_refresh(reason="platform_skill_install_command")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/install/zip")
async def install_skill_zip(file: UploadFile = File(...)):
    try:
        content = await file.read()
        result = install_skills_from_zip(file.filename or "skills.zip", content)
        extensions_runtime_service.request_skill_inventory_refresh(reason="platform_skill_install_zip")
        return result
    except SkillInstallValidationError as e:
        raise HTTPException(status_code=400, detail=e.to_payload())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mcp/tools")
async def get_mcp_tools():
    try:
        from core.native_tools import NATIVE_TOOLS

        tools = extensions_runtime_service.get_mcp_tools()
        mcp_list = [
            {
                "name": tool.name,
                "description": getattr(tool, "description", ""),
                "serverName": getattr(tool, "metadata", {}).get("server_name", "Unknown")
                if getattr(tool, "metadata", None)
                else "Unknown",
            }
            for tool in tools
        ]
        native_list = [
            {
                "name": getattr(tool, "name", tool.__name__ if hasattr(tool, "__name__") else "unknown"),
                "description": getattr(tool, "description", getattr(tool, "__doc__", "")),
                "serverName": "系统原生能力 (Native Tools)",
            }
            for tool in NATIVE_TOOLS
        ]
        return {"mcpTools": native_list + mcp_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tools/registry-index")
async def get_tool_registry_index():
    try:
        from core.tool_registry_index import build_tool_registry_index

        return build_tool_registry_index()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
async def get_models_config():
    try:
        return model_control_plane.get_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models")
async def save_models_config(data: dict = Body(...)):
    try:
        config = model_control_plane.save_config(data)
        return {"status": "success", "config": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/control-plane")
async def get_model_control_plane():
    try:
        config = model_control_plane.get_config()
        return model_control_plane.build_payload(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/role-doctor")
async def get_model_role_doctor(role: str | None = None):
    try:
        config = model_control_plane.get_config()
        models = model_control_plane.list_models(config)
        return {
            "role": role or None,
            "diagnostics": diagnose_models(models, role=role),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/control-plane")
async def save_model_control_plane(data: dict = Body(...)):
    try:
        config = model_control_plane.save_config(data)
        return {"status": "success", **model_control_plane.build_payload(config)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/test-connection")
async def test_model_connection(payload: ModelConnectionTestPayload):
    try:
        result = model_connection_tester.test_model_connection(
            model_id=payload.model_id,
            model_ref=payload.model_ref or "",
            provider_id=payload.provider_id or "",
        )
        if result.get("ok"):
            return result
        raise HTTPException(status_code=422, detail=result)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/catalog")
async def get_model_provider_catalog():
    try:
        return model_provider_catalog.load()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/providers/probe")
async def probe_model_provider(data: dict = Body(...)):
    try:
        provider_id = str(data.get("providerId") or data.get("provider_id") or "").strip()
        custom_provider_name = str(data.get("customProviderName") or data.get("custom_provider_name") or "").strip()
        base_url = str(data.get("baseUrl") or data.get("base_url") or "").strip()
        is_custom_probe = provider_id in {"", "__custom__", "custom"} and bool(custom_provider_name or base_url)
        if not provider_id and not is_custom_probe:
            raise HTTPException(status_code=422, detail="providerId is required")
        credential = str(data.get("apiKey") or data.get("api_key") or "").strip()
        credential_source = "request" if credential else ""
        provider_kind = str(data.get("providerKind") or data.get("provider_kind") or "").strip()
        media_modality = str(data.get("mediaModality") or data.get("media_modality") or "").strip()
        api_standard = str(data.get("apiStandard") or data.get("api_standard") or "openai").strip()
        provider = None
        if is_custom_probe:
            provider = model_provider_catalog.build_custom_provider(
                custom_provider_name,
                base_url,
                provider_kind=provider_kind or "chat",
                media_modality=media_modality,
                api_standard=api_standard,
            )
            provider_id = str(provider.get("id") or "")
        elif not credential:
            catalog_provider = model_provider_catalog.get_provider(provider_id)
            stored_key, stored_source = _stored_provider_credential(provider_id, catalog_provider)
            if stored_key:
                credential = stored_key
                credential_source = stored_source
        result = (
            model_provider_catalog.probe_provider_entry(provider, credential=credential, base_url=base_url)
            if provider
            else model_provider_catalog.probe_provider(provider_id, credential=credential, base_url=base_url)
        )
        if is_custom_probe and result.get("ok"):
            saved_provider = model_provider_catalog.save_custom_provider(provider or {})
            result["provider"] = saved_provider
            result["providerId"] = saved_provider.get("id")
            result["customProviderSaved"] = True
        else:
            result["providerId"] = provider_id
        result["credentialSource"] = credential_source or "none"
        result["usedStoredCredential"] = credential_source.startswith("stored_provider")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/models/providers/custom/{provider_id}")
async def delete_custom_model_provider(provider_id: str):
    try:
        provider = model_provider_catalog.get_provider(provider_id)
        if not provider or not provider.get("isCustom"):
            raise HTTPException(status_code=404, detail="custom provider not found")
        deleted = model_provider_catalog.delete_custom_provider(provider_id)
        return {"ok": deleted, "providerId": provider_id}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/connect")
async def connect_model_provider(data: dict = Body(...)):
    try:
        provider_id = str(data.get("providerId") or data.get("provider_id") or "").strip()
        model_id = str(data.get("modelId") or data.get("model_id") or "").strip()
        if not provider_id or not model_id:
            raise HTTPException(status_code=422, detail="providerId and modelId are required")
        custom_provider_name = str(data.get("customProviderName") or data.get("custom_provider_name") or "").strip()
        base_url = str(data.get("baseUrl") or data.get("base_url") or "").strip()
        incoming_credential = str(data.get("apiKey") or data.get("api_key") or "").strip()
        provider_kind = str(data.get("providerKind") or data.get("provider_kind") or "").strip()
        media_modality = str(data.get("mediaModality") or data.get("media_modality") or "").strip()
        api_standard = str(data.get("apiStandard") or data.get("api_standard") or "").strip()
        requested_model_type = str(data.get("modelType") or data.get("type") or "").strip().upper()
        provider = model_provider_catalog.get_provider(provider_id)
        if not provider and provider_id in {"__custom__", "custom"}:
            if not incoming_credential:
                raise HTTPException(status_code=422, detail="apiKey is required before connecting this Provider")
            provider = model_provider_catalog.build_custom_provider(
                custom_provider_name,
                base_url,
                provider_kind=provider_kind or ("media_generation" if media_modality else "chat"),
                media_modality=media_modality,
                api_standard=api_standard or "openai",
            )
            provider = model_provider_catalog.save_custom_provider(provider)
            provider_id = str(provider.get("id") or "")
        if not provider:
            raise HTTPException(status_code=404, detail="provider not found")

        model = model_provider_catalog.normalize_model(provider, model_id)
        config = model_control_plane.get_config()
        providers = dict(config.get("providers") or {})
        existing = dict(providers.get(provider_id) or {})
        existing_provider = dict(existing.get("provider") or {})
        auth = dict(provider.get("auth") or {})
        credential = str(incoming_credential or existing_provider.get("api_key") or "").strip()
        if auth.get("type") == "api_key" and not credential:
            raise HTTPException(status_code=422, detail="apiKey is required before connecting this Provider")
        credential_mode = "oauthFile" if auth.get("type") == "oauth_file" else "apiKey"
        oauth_path = str(auth.get("path") or "")
        next_provider = {
            **existing_provider,
            "name": provider.get("name") or provider_id,
            "base_url": str(base_url or provider.get("baseUrl") or ""),
            "api_standard": api_standard or provider.get("apiStandard") or "openai",
            "providerKind": provider.get("providerKind") or existing_provider.get("providerKind") or "chat",
            "mediaModality": provider.get("mediaModality") or media_modality or existing_provider.get("mediaModality") or "",
            "type": "PLATFORM" if auth.get("type") == "oauth_file" else "API",
            "api_key": credential if auth.get("type") != "oauth_file" else f"oauth:{oauth_path}",
            "credential_mode": credential_mode,
            "oauth_preset": auth.get("preset") or existing_provider.get("oauth_preset") or "",
            "logoAsset": provider.get("logoAsset") or existing_provider.get("logoAsset") or "",
            "credentialRealm": provider.get("credentialRealm")
            or existing_provider.get("credentialRealm")
            or _credential_realm(provider_id, provider),
            "promptCachingProfileId": provider.get("promptCachingProfileId")
            or existing_provider.get("promptCachingProfileId")
            or prompt_cache_profile_id_for_provider(provider_id),
            "is_enabled": True,
        }
        is_custom_provider = bool(provider.get("isCustom"))
        is_oauth_provider = auth.get("type") == "oauth_file"
        media_model_types = {"MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"}
        catalog_model_type = str(model.get("type") or "TEXT").upper()
        catalog_capability_class = str(model.get("capabilityClass") or "")
        if catalog_capability_class == "media_generation" and requested_model_type in {"", "TEXT", "MULTIMODAL"}:
            normalized_model_type = catalog_model_type
        else:
            normalized_model_type = requested_model_type if requested_model_type in media_model_types | {"TEXT", "MULTIMODAL", "EMBEDDING", "RERANK"} else catalog_model_type
        is_media_provider = str(provider.get("providerKind") or "") == "media_generation" or normalized_model_type in media_model_types
        is_retrieval_model = normalized_model_type in {"EMBEDDING", "RERANK", "RERANKER"} or str(model.get("capabilityClass") or "").lower() in {"embedding", "reranker", "rerank"}
        registry_known_chat_model = bool(model.get("capabilityRegistryMatched")) and not is_media_provider
        clear_runtime_budget = is_media_provider or is_oauth_provider or (is_custom_provider and not registry_known_chat_model and not is_retrieval_model)
        managed_context_window = None if clear_runtime_budget else model.get("contextWindow")
        managed_max_tokens = None if clear_runtime_budget or is_retrieval_model else model.get("maxTokens")
        next_model = {
            "type": normalized_model_type or "TEXT",
            "contextWindow": managed_context_window,
            "maxTokens": managed_max_tokens,
            "capabilities": model.get("capabilities") or {},
            "capabilityClass": model.get("capabilityClass")
            or ("media_generation" if is_media_provider else "vision_multimodal" if (model.get("capabilities") or {}).get("vision") else "chat_general"),
            "capabilitySource": model.get("capabilitySource") or "manual",
            "parameterProfile": model.get("parameterProfile") or ("media_generation" if is_media_provider else "chat"),
            "mediaLimits": model.get("mediaLimits") or {},
            "logoAsset": model.get("logoAsset") or "",
            "capabilityRegistry": model.get("capabilityRegistry") or {},
            "pricing": model.get("pricing") or {},
            "driftWarnings": model.get("driftWarnings") or [],
            "promptCachingProfileId": model.get("promptCachingProfileId")
            or next_provider.get("promptCachingProfileId")
            or prompt_cache_profile_id_for_provider(provider_id),
            "isEnabled": True,
        }
        current_models = dict(existing.get("models") or {})
        if provider.get("singleActiveModel"):
            current_models = {}
        current_models[model_id] = next_model
        providers[provider_id] = {"provider": next_provider, "models": current_models}
        config["providers"] = providers
        saved = model_control_plane.save_config(config)
        return {
            "ok": True,
            "providerId": provider_id,
            "modelId": model_id,
            "modelRef": model.get("modelRef"),
            "config": saved,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/telemetry/overview")
async def get_telemetry_overview(days: int = 7):
    try:
        return model_telemetry_service.build_dashboard_overview(days=max(1, min(days, 30)))
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": "telemetry_overview_unavailable",
                "detail": str(e),
            },
        )


@router.get("/research-runtime/ledger")
async def get_research_runtime_ledger(scope: str = "global", includeArchived: bool = False):
    try:
        return research_ledger_summary(scope=scope or "global", include_archived=includeArchived)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/research-runtime/evidence")
async def get_research_runtime_evidence(scope: str = "global", limit: int = 30):
    try:
        return {
            "ok": True,
            "scope": scope or "global",
            "items": list_evidence_bundles(scope=scope or "global", limit=max(1, min(limit, 100))),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/research-runtime/experience")
async def get_research_runtime_experience(query: str = "", scope: str = "global", minConfidence: str = "", limit: int = 30, includeArchived: bool = False):
    try:
        items = search_experience_packs_with_options(
            query=query,
            scope=scope or "global",
            min_confidence=minConfidence,
            limit=max(1, min(limit, 100)),
            include_archived=includeArchived,
        )
        return {"ok": True, "scope": scope or "global", "query": query, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/research-runtime/experience/archive")
async def archive_research_runtime_experience(data: dict = Body(...)):
    try:
        pack = archive_experience_pack(
            str(data.get("experiencePackId") or "").strip(),
            initiated_by=str(data.get("initiatedBy") or "admin").strip(),
            reason=str(data.get("reason") or "").strip(),
        )
        if not pack:
            raise HTTPException(status_code=404, detail="experience pack not found")
        return {"ok": True, "item": pack}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/research-runtime/experience/restore")
async def restore_research_runtime_experience(data: dict = Body(...)):
    try:
        pack = restore_experience_pack(
            str(data.get("experiencePackId") or "").strip(),
            initiated_by=str(data.get("initiatedBy") or "admin").strip(),
        )
        if not pack:
            raise HTTPException(status_code=404, detail="experience pack not found")
        return {"ok": True, "item": pack}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/research-runtime/experience/{experience_pack_id}")
async def delete_research_runtime_experience(experience_pack_id: str, confirm: bool = False):
    try:
        deleted = delete_experience_pack(experience_pack_id, confirm=confirm)
        if not deleted:
            raise HTTPException(status_code=404 if confirm else 400, detail="experience pack not deleted")
        return {"ok": True, "deleted": True, "experiencePackId": experience_pack_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/research-runtime/experience/promote")
async def promote_research_runtime_experience(data: dict = Body(...)):
    try:
        pack = promote_experience_pack(
            str(data.get("evidenceBundleId") or "").strip(),
            title=str(data.get("title") or "").strip(),
            tags=data.get("tags") if isinstance(data.get("tags"), list) else None,
        )
        if not pack:
            raise HTTPException(status_code=404, detail="evidence bundle not found")
        return {"ok": True, "item": pack}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
