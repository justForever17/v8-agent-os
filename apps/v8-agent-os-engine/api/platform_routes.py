from typing import Any
import hashlib
import json
import logging
from urllib.parse import urlparse
import uuid

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from .models import ModelConnectionTestPayload, ModelReasoningRepairPayload
from core.database import db
from core.extensions_runtime import extensions_runtime_service
from core.agents import build_specialist_family_registry
from core.json_safe import to_jsonable
from core.model_connection_tester import model_connection_tester
from core.model_reasoning_repair import model_reasoning_repair_service
from core.model_control_plane import model_control_plane
from core.model_ref import make_model_ref
from core.model_provider_catalog import model_provider_catalog
from core.model_provider_channels import resolve_provider_channel
from core.model_protocol_registry import suggest_model_protocol
from core.model_role_doctor import diagnose_models
from core.model_thinking_control import resolve_reasoning_effort_control_for_metadata
from core.prompt_cache_gateway import prompt_cache_profile_id_for_provider
from core.model_telemetry import model_telemetry_service
from core.mcp_config_service import McpConfigValidationError, validate_mcp_server_map
from core.skills_install_service import SkillInstallValidationError, install_skill_from_command, install_skills_from_zip
from core.storage import storage
from core.source_provider_registry import get_source_provider_capabilities, get_source_router_defaults
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
logger = logging.getLogger(__name__)

_ACTIVE_MCP_APP_GUIDANCE_STATUSES = {"queued", "running", "waiting_approval", "waiting_input", "waiting_external_tool", "paused"}
_DEMO_MCP_APP_SERVER = "v8-demo-fixture"
_DEMO_MCP_APP_URIS = {"ui://v8-demo/counter", "ui://v8-demo/review-panel"}
_MCP_APP_LEGACY_METHODS = {
    "ui/updateModelContext": "ui/update-model-context",
    "ui/openLink": "ui/open-link",
    "ui/requestDisplayMode": "ui/request-display-mode",
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


def _demo_mcp_app_resource(server_name: str, uri: str) -> dict[str, Any] | None:
    if server_name != _DEMO_MCP_APP_SERVER or uri not in _DEMO_MCP_APP_URIS:
        return None
    title = "V8 MCP App Demo"
    body = "这是一个用于 Phone/Web 历史回放验收的 UI:// fixture。"
    if uri.endswith("/review-panel"):
        title = "Research Review"
        body = "MCP App 可以在工具结果位置嵌入交互面板；这里展示证据摘要、确认按钮和本地状态。"
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(135deg, #f8fafc, #eef2ff);
      color: #0f172a;
    }}
    .app {{
      min-height: 220px;
      padding: 18px;
      display: grid;
      gap: 14px;
      box-sizing: border-box;
    }}
    .title {{ font-size: 18px; font-weight: 750; letter-spacing: .01em; }}
    .body {{ color: #475569; line-height: 1.55; }}
    .panel {{
      border: 1px solid rgba(99, 102, 241, .18);
      border-radius: 14px;
      background: rgba(255, 255, 255, .72);
      padding: 14px;
      box-shadow: 0 12px 30px rgba(15, 23, 42, .08);
    }}
    .row {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    button {{
      border: 0;
      border-radius: 999px;
      padding: 9px 14px;
      background: #4f46e5;
      color: white;
      font-weight: 650;
      cursor: pointer;
    }}
    .ghost {{ background: #e2e8f0; color: #334155; }}
    .note {{ font-size: 12px; color: #64748b; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: linear-gradient(135deg, #020617, #111827); color: #f8fafc; }}
      .body {{ color: #cbd5e1; }}
      .panel {{ background: rgba(15, 23, 42, .72); border-color: rgba(129, 140, 248, .35); }}
      .ghost {{ background: #1e293b; color: #cbd5e1; }}
      .note {{ color: #94a3b8; }}
    }}
  </style>
</head>
<body>
  <main class="app">
    <section class="panel">
      <div class="title">{title}</div>
      <p class="body">{body}</p>
      <div class="row">
        <button type="button" onclick="window.__count=(window.__count||0)+1;document.getElementById('count').textContent=window.__count">本地计数 <span id="count">0</span></button>
        <button type="button" class="ghost" onclick="document.getElementById('status').textContent='已在 iframe 内响应点击'">确认</button>
      </div>
      <p id="status" class="note">静态 fixture 不调用真实 MCP 工具；用于验证 UI:// iframe/WebView 渲染链路。</p>
    </section>
  </main>
</body>
</html>"""
    return {
        "serverName": server_name,
        "uri": uri,
        "mimeType": "text/html;profile=mcp-app",
        "html": html,
        "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "uiMeta": {"title": title, "source": "demo_fixture"},
        "csp": {"connectDomains": [], "resourceDomains": [], "frameDomains": []},
        "permissions": {"toolCalls": False, "openLinks": False},
    }


@router.get("/mcp/apps/resources/read")
async def read_mcp_app_resource(serverName: str, uri: str):
    try:
        demo = _demo_mcp_app_resource(serverName, uri)
        if demo is not None:
            return demo
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


def _mcp_app_permission_allows(instance: dict[str, Any], permission: str, *, default: bool = True) -> bool:
    permissions = instance.get("permissions") if isinstance(instance.get("permissions"), dict) else {}
    value = permissions.get(permission)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return bool(value.get("enabled", True))
    return bool(value)


def _mcp_app_tool_permission_error(instance: dict[str, Any], tool_name: str) -> str | None:
    permissions = instance.get("permissions") if isinstance(instance.get("permissions"), dict) else {}
    if not _mcp_app_permission_allows(instance, "toolCalls", default=True):
        return "MCP app manifest does not allow tool calls"
    allowed_tools = permissions.get("allowedTools") or permissions.get("tools")
    if isinstance(allowed_tools, list):
        allowed = {str(item or "").strip() for item in allowed_tools if str(item or "").strip()}
        if allowed and tool_name not in allowed:
            return "MCP app manifest does not allow this tool"
    return None


def _mcp_app_validate_plugin_grant(instance: dict[str, Any]) -> None:
    plugin_id = str(instance.get("pluginId") or "").strip()
    if not plugin_id:
        return
    from runtimes.plugin_manager.service import plugin_manager_service

    plugin_manager_service.validate_grant_for_invocation(
        grant_id=str(instance.get("grantId") or ""),
        plugin_id=plugin_id,
        component_id=str(instance.get("componentId") or ""),
        session_id=str(instance.get("sessionId") or ""),
        run_id=str(instance.get("runId") or ""),
        grantee_type="supervisor",
        grantee_id="supervisor",
        manifest_digest=str(instance.get("pluginDigest") or "") or None,
    )


def _mcp_app_enqueue_guidance(
    *,
    instance: dict[str, Any],
    app_instance_id: str,
    params: dict[str, Any],
    source_method: str,
    prefix: str,
) -> dict[str, Any]:
    session_id = str(instance.get("sessionId") or "").strip()
    run_id = str(instance.get("runId") or "").strip()
    if not session_id:
        raise ValueError("MCP app instance is not attached to a chat session")
    content = _mcp_app_context_to_text(params)
    queue_id = f"queued_mcpapp_{uuid.uuid4().hex}"
    source_label = source_method.removeprefix("ui/")
    queue_item = db.add_chat_user_message_queue_item(
        queue_id=queue_id,
        session_id=session_id,
        run_id=run_id or None,
        client_message_id=f"mcpapp_{source_label}_{app_instance_id}_{uuid.uuid4().hex[:8]}",
        content=f"{prefix}：\n{content}",
        attachments=[],
        file_urls=[],
        request_payload={
            "source": f"mcp_app.{source_label}",
            "appInstanceId": app_instance_id,
            "serverName": instance.get("serverName"),
            "resourceUri": instance.get("resourceUri"),
            "params": to_jsonable(params),
        },
        metadata={
            "source": f"mcp_app.{source_label}",
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
            metadata_updates={"promotedBy": f"mcp_app.{source_label}"},
        ) or queue_item
        queue_payload = _mcp_app_queue_payload(promoted)
        command_service.issue_control_signal(
            run_id,
            command="guidance",
            reason=f"mcp_app_{source_label}",
            payload={"queueMessageId": queue_id, "source": f"mcp_app.{source_label}"},
        )
        _mcp_app_emit_event(
            "human_guidance.promoted",
            session_id=session_id,
            run_id=run_id,
            payload={
                "queueMessage": queue_payload,
                "state": "promoted",
                "summary": f"{prefix}已提升为运行中引导，将在安全检查点注入。",
                "source": f"mcp_app.{source_label}",
            },
        )
        return {
            "ok": True,
            "queued": True,
            "promoted": True,
            "event": "human_guidance.promoted",
            "queueMessageId": queue_id,
            "appInstanceId": app_instance_id,
        }
    _mcp_app_emit_event(
        "human_guidance.queued",
        session_id=session_id,
        run_id=run_id or None,
        payload={
            "queueMessage": _mcp_app_queue_payload(queue_item),
            "state": "pending",
            "summary": f"{prefix}已排队，将在后续可用时作为引导处理。",
            "source": f"mcp_app.{source_label}",
        },
    )
    return {
        "ok": True,
        "queued": True,
        "promoted": False,
        "event": "human_guidance.queued",
        "queueMessageId": queue_id,
        "appInstanceId": app_instance_id,
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
    requested_method = str(payload.get("method") or "").strip()
    method = _MCP_APP_LEGACY_METHODS.get(requested_method, requested_method)
    if method != requested_method:
        logger.warning(
            "Deprecated MCP App RPC method %s used for %s; migrate to %s before compatibility removal.",
            requested_method,
            app_instance_id,
            method,
        )
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    instance = mcp_manager.get_app_instance(app_instance_id)
    if not instance:
        return _json_rpc_error(request_id, -32004, "Unknown MCP app instance")

    try:
        if method == "ui/initialize":
            return _json_rpc_result(
                request_id,
                {
                    "protocolVersion": str(params.get("protocolVersion") or "2025-06-18"),
                    "hostInfo": {"name": "V8 Agent OS", "version": "1"},
                    "hostCapabilities": {
                        "displayModes": ["inline", "fullscreen"],
                        "openLinks": True,
                        "serverTools": True,
                    },
                    "hostContext": {
                        "displayMode": instance.get("displayMode") or "inline",
                    },
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
            permission_error = _mcp_app_tool_permission_error(instance, tool_name)
            if permission_error:
                return _json_rpc_error(request_id, -32016, permission_error)
            try:
                _mcp_app_validate_plugin_grant(instance)
            except Exception as exc:
                return _json_rpc_error(request_id, -32017, f"Plugin grant is not valid for this MCP app: {exc}")
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

        if method in {
            "ui/notifications/tool-result",
            "ui/notifications/tool-input",
            "ui/notifications/log",
            "ui/notifications/size-changed",
        }:
            if method == "ui/notifications/size-changed":
                width = max(0, min(10000, int(params.get("width") or 0)))
                height = max(0, min(10000, int(params.get("height") or 0)))
                mcp_manager.update_app_instance(app_instance_id, preferredSize={"width": width, "height": height})
            return _json_rpc_result(request_id, {"ok": True, "acknowledged": method})

        if method in {"ui/message", "ui/update-model-context"}:
            if not _mcp_app_permission_allows(instance, "messages", default=True):
                return _json_rpc_error(request_id, -32018, "MCP app manifest does not allow host messages")
            prefix = "MCP App 消息" if method == "ui/message" else "MCP App 上下文更新"
            result = _mcp_app_enqueue_guidance(
                instance=instance,
                app_instance_id=app_instance_id,
                params=params,
                source_method=method,
                prefix=prefix,
            )
            return _json_rpc_result(
                request_id,
                result,
            )

        if method == "ui/open-link":
            if not _mcp_app_permission_allows(instance, "openLinks", default=True):
                return _json_rpc_error(request_id, -32019, "MCP app manifest does not allow opening links")
            url = str(params.get("url") or "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return _json_rpc_error(request_id, -32011, "Only http/https links can be opened")
            return _json_rpc_result(request_id, {"ok": True, "action": "open-link", "url": url})

        if method == "ui/request-display-mode":
            requested_mode = str(params.get("mode") or params.get("displayMode") or "inline").strip().lower()
            if requested_mode not in {"inline", "fullscreen"}:
                return _json_rpc_error(request_id, -32602, "Only inline and fullscreen display modes are supported")
            mcp_manager.update_app_instance(app_instance_id, displayMode=requested_mode)
            return _json_rpc_result(
                request_id,
                {"ok": True, "displayMode": requested_mode},
            )

        if method in {"ui/teardown", "ui/notifications/teardown"}:
            result = mcp_manager.close_app_instance(app_instance_id)
            return _json_rpc_result(request_id, {**result, "acknowledged": method})

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
        new_servers = validate_mcp_server_map(config)
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
async def get_skills_list(
    session_id: str | None = Query(default=None, alias="sessionId"),
    workspace_path: str | None = Query(default=None, alias="workspacePath"),
    workspace_id: str | None = Query(default=None, alias="workspaceId"),
    project_id: str | None = Query(default=None, alias="projectId"),
):
    try:
        supervisor_config = storage.get_supervisor_config() or {}
        return {
            "skills": list(
                extensions_runtime_service.list_skills(
                    force_refresh=False,
                    session_id=str(session_id or "").strip() or None,
                    explicit_workspace_path=str(workspace_path or "").strip() or None,
                    explicit_workspace_id=str(workspace_id or "").strip() or None,
                    explicit_project_id=str(project_id or "").strip() or None,
                    runtime_kind="chat",
                )
            ),
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
        # Public-by-default: credentials and other runtime-only values never
        # leave the Engine. Internal code uses the control-plane object directly.
        return model_control_plane.get_public_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models", deprecated=True)
async def save_models_config(data: dict = Body(...)):
    try:
        # Legacy whole-config writes are compatibility-only. Preserve credentials
        # omitted by the public GET response so an old read/modify/write client
        # cannot erase a Provider secret. New clients use the atomic routes.
        current = model_control_plane.get_config()
        incoming = dict(data or {})
        incoming_providers = dict(incoming.get("providers") or {})
        current_providers = dict(current.get("providers") or {})
        for provider_id, provider_data in incoming_providers.items():
            if not isinstance(provider_data, dict):
                continue
            provider_meta = dict(provider_data.get("provider") or {})
            existing_meta = dict((current_providers.get(provider_id) or {}).get("provider") or {})
            incoming_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "").strip()
            if not incoming_key or incoming_key == "****":
                existing_key = str(existing_meta.get("api_key") or existing_meta.get("apiKey") or "").strip()
                if existing_key:
                    provider_meta["api_key"] = existing_key
            provider_data["provider"] = provider_meta
            incoming_providers[provider_id] = provider_data
        incoming["providers"] = incoming_providers
        config = model_control_plane.save_config(incoming)
        return {"status": "success", "config": model_control_plane.get_public_config(config)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/control-plane")
async def get_model_control_plane():
    try:
        config = model_control_plane.get_config()
        return model_control_plane.build_payload(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/public")
async def get_public_models_config():
    """Return the human/admin-facing model contract without credentials."""
    try:
        return model_control_plane.get_public_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/models/providers/{provider_id}")
async def upsert_model_provider(provider_id: str, data: dict = Body(...)):
    try:
        provider_patch = dict(data.get("provider") or data)
        provider_patch.pop("providerId", None)
        provider_patch.pop("provider_id", None)
        result = model_control_plane.upsert_provider_record(provider_id, provider_patch)
        return {
            "ok": True,
            "providerId": provider_id,
            "provider": model_control_plane.get_public_config().get("providers", {}).get(provider_id, {}).get("provider", {}),
            "models": result.get("models") or {},
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/models/providers/{provider_id}")
async def remove_model_provider(provider_id: str):
    try:
        removed = model_control_plane.remove_provider_record(provider_id)
        if not removed:
            raise HTTPException(status_code=404, detail="provider not found")
        return {"ok": True, "providerId": provider_id}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/models/bindings")
async def upsert_model_binding(data: dict = Body(...)):
    try:
        provider_id = str(data.get("providerId") or data.get("provider_id") or "").strip()
        model_id = str(data.get("modelId") or data.get("model_id") or "").strip()
        model_patch = dict(data.get("model") or data.get("modelPatch") or {})
        reserved = {
            "providerId", "provider_id", "modelId", "model_id", "model", "modelPatch",
            "sourceProviderId", "source_provider_id", "sourceModelId", "source_model_id",
            "source", "replaceProviderModels", "replace_provider_models",
        }
        if not model_patch:
            model_patch = {key: value for key, value in data.items() if key not in reserved}
        result = model_control_plane.upsert_model_record(
            provider_id=provider_id,
            model_id=model_id,
            model_patch=model_patch,
            source_provider_id=str(data.get("sourceProviderId") or data.get("source_provider_id") or ""),
            source_model_id=str(data.get("sourceModelId") or data.get("source_model_id") or ""),
            source=str(data.get("source") or "manual"),
            replace_provider_models=bool(data.get("replaceProviderModels", data.get("replace_provider_models", False))),
        )
        public_config = model_control_plane.get_public_config()
        public_provider = dict((public_config.get("providers") or {}).get(provider_id) or {})
        return {
            "ok": True,
            "providerId": provider_id,
            "modelId": model_id,
            "modelRef": make_model_ref(provider_id, model_id),
            "provider": public_provider.get("provider") or {},
            "model": dict((public_provider.get("models") or {}).get(model_id) or {}),
        }
    except ValueError as e:
        status = 404 if str(e) == "provider not found" else 422
        raise HTTPException(status_code=status, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/models/bindings")
async def remove_model_binding(data: dict = Body(...)):
    try:
        provider_id = str(data.get("providerId") or data.get("provider_id") or "").strip()
        model_id = str(data.get("modelId") or data.get("model_id") or "").strip()
        removed = model_control_plane.remove_model_record(provider_id=provider_id, model_id=model_id)
        if not removed:
            raise HTTPException(status_code=404, detail="model not found")
        return {"ok": True, "providerId": provider_id, "modelId": model_id}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/supervisor-reasoning-effort")
async def get_supervisor_reasoning_effort_control():
    try:
        config = model_control_plane.get_config()
        resolution = model_control_plane.resolve_model_for_role("supervisor", config)
        provider_id = str(resolution.get("resolvedProviderId") or "").strip()
        model_id = str(resolution.get("resolvedModelId") or "").strip()
        model_ref = str(resolution.get("resolvedModelRef") or "").strip()
        model_record = dict(resolution.get("resolvedModel") or {})
        provider_record = dict(resolution.get("resolvedProvider") or {})
        control = resolve_reasoning_effort_control_for_metadata(
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "model_ref": model_ref,
                "provider_record": provider_record,
                "model_record": model_record,
                "api_standard": provider_record.get("api_standard") or provider_record.get("apiStandard") or "openai",
                "capabilities": dict(model_record.get("capabilities") or {}),
                "capability_class": model_record.get("capabilityClass") or model_record.get("capability_class") or "",
            }
        )
        supported = bool(control.get("supportsReasoningEffort"))
        reason = ""
        if not model_id:
            reason = "supervisor_model_unbound"
        elif not supported:
            reason = "supervisor_model_does_not_support_normalized_reasoning_effort"
        return {
            "role": "supervisor",
            "modelRef": model_ref,
            "modelId": model_id,
            "providerId": provider_id,
            "bindingState": resolution.get("bindingState") or "",
            "supported": supported,
            "visible": supported,
            "defaultLevel": control.get("defaultLevel") or "auto",
            "levels": list(control.get("levels") or []),
            "requestStyle": control.get("requestStyle") or "",
            "reason": reason,
        }
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


@router.get("/models/defaults")
async def get_model_default_categories():
    try:
        config = model_control_plane.get_config()
        return {"categories": model_control_plane.get_default_categories(config)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/defaults")
async def set_model_default_category(data: dict = Body(...)):
    try:
        model_ref = str(data.get("modelRef") or data.get("model_ref") or "").strip()
        if not model_ref:
            provider_id = str(data.get("providerId") or data.get("provider_id") or "").strip()
            model_id = str(data.get("modelId") or data.get("model_id") or "").strip()
            if provider_id and model_id:
                model_ref = make_model_ref(provider_id, model_id)
        if not model_ref:
            raise HTTPException(status_code=422, detail="modelRef is required")
        result = model_control_plane.set_default_model_for_category(
            model_ref=model_ref,
            category=str(data.get("category") or data.get("categoryKey") or data.get("category_key") or "").strip() or None,
        )
        return {"status": "success", **result}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
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


@router.post("/models/repair-reasoning")
async def repair_model_reasoning(payload: ModelReasoningRepairPayload):
    try:
        result = model_reasoning_repair_service.repair_reasoning_surface(
            model_id=payload.model_id or "",
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
        declared_capabilities = data.get("declaredCapabilities") or data.get("declared_capabilities") or []
        if not isinstance(declared_capabilities, list):
            declared_capabilities = []
        requested_channels = data.get("channels") if isinstance(data.get("channels"), list) else []
        requested_default_channel_id = str(
            data.get("defaultChannelId") or data.get("default_channel_id") or ""
        ).strip().lower()
        provider = None
        if is_custom_probe:
            provider = model_provider_catalog.build_custom_provider(
                custom_provider_name,
                base_url,
                provider_kind=provider_kind or "chat",
                media_modality=media_modality,
                api_standard=api_standard,
                declared_capabilities=declared_capabilities,
            )
            if requested_channels:
                provider["channels"] = requested_channels
                provider["defaultChannelId"] = requested_default_channel_id
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
        voice_app_id = str(data.get("voiceAppId") or data.get("voice_app_id") or "").strip()
        voice_resource_id = str(data.get("voiceResourceId") or data.get("voice_resource_id") or "").strip()
        provider_kind = str(data.get("providerKind") or data.get("provider_kind") or "").strip()
        media_modality = str(data.get("mediaModality") or data.get("media_modality") or "").strip()
        api_standard = str(data.get("apiStandard") or data.get("api_standard") or "").strip()
        declared_capabilities = data.get("declaredCapabilities") or data.get("declared_capabilities") or []
        if not isinstance(declared_capabilities, list):
            declared_capabilities = []
        requested_model_type = str(data.get("modelType") or data.get("type") or "").strip().upper()
        endpoint_path = str(data.get("endpointPath") or data.get("endpoint_path") or "").strip()
        provider_model_id = str(data.get("providerModelId") or data.get("provider_model_id") or "").strip()
        operation_kind = str(data.get("operationKind") or data.get("operation_kind") or "").strip()
        adapter = str(data.get("adapter") or "").strip()
        wire_protocol = str(data.get("wireProtocol") or data.get("wire_protocol") or "").strip()
        channel_id = str(data.get("channelId") or data.get("channel_id") or "").strip().lower()
        requested_channels = data.get("channels") if isinstance(data.get("channels"), list) else []
        requested_default_channel_id = str(
            data.get("defaultChannelId") or data.get("default_channel_id") or ""
        ).strip().lower()
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
                declared_capabilities=declared_capabilities,
            )
            if requested_channels:
                provider["channels"] = requested_channels
                provider["defaultChannelId"] = requested_default_channel_id or channel_id
            provider = model_provider_catalog.save_custom_provider(provider)
            provider_id = str(provider.get("id") or "")
        if not provider:
            raise HTTPException(status_code=404, detail="provider not found")

        model = model_provider_catalog.normalize_model(provider, model_id)
        channel_provider = {
            **provider,
            **({"channels": requested_channels, "defaultChannelId": requested_default_channel_id or channel_id} if requested_channels else {}),
        }
        requested_channel = resolve_provider_channel(
            channel_provider,
            channel_id=channel_id,
            wire_protocol=wire_protocol,
        )
        if channel_id and requested_channel.get("id") != channel_id:
            raise HTTPException(status_code=422, detail=f"unknown Provider channel: {channel_id}")
        if not wire_protocol and requested_channel.get("source") == "configured":
            wire_protocol = str(requested_channel.get("defaultWireProtocol") or "")
        protocol_advice = suggest_model_protocol(
            provider_id,
            api_standard or provider.get("apiStandard") or "openai",
            provider_model_id or model.get("id") or model_id,
            provider_meta=provider,
            model_meta=model,
        )
        if not endpoint_path and wire_protocol and str(model.get("type") or "").upper() not in {
            "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "MEDIA", "WORKFLOW", "MODEL3D",
        }:
            endpoint_path = str(protocol_advice.get("endpointPath") or "")
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
            "voice_app_id": voice_app_id or existing_provider.get("voice_app_id") or "",
            "voice_resource_id": voice_resource_id or existing_provider.get("voice_resource_id") or "",
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
        if requested_channels:
            next_provider["channels"] = requested_channels
            next_provider["defaultChannelId"] = requested_default_channel_id or channel_id
        elif provider.get("channels"):
            next_provider["channels"] = provider.get("channels")
            next_provider["defaultChannelId"] = str(provider.get("defaultChannelId") or channel_id or "")
        selected_channel = resolve_provider_channel(
            next_provider,
            channel_id=channel_id,
            wire_protocol=wire_protocol,
        )
        if channel_id and selected_channel.get("id") != channel_id:
            raise HTTPException(status_code=422, detail=f"unknown Provider channel: {channel_id}")
        if selected_channel.get("source") == "configured":
            next_provider["base_url"] = str(selected_channel.get("baseUrl") or next_provider.get("base_url") or "")
            next_provider["api_standard"] = str(selected_channel.get("apiStandard") or next_provider.get("api_standard") or "openai")
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
        if endpoint_path or provider_model_id or operation_kind or adapter or wire_protocol or channel_id:
            next_model["endpointBinding"] = {
                "route": model_id,
                "endpointPath": endpoint_path,
                "providerModelId": provider_model_id,
                "operationKind": operation_kind,
                "adapter": adapter,
                "wireProtocol": wire_protocol,
                "channelId": str(selected_channel.get("id") or channel_id or ""),
                "protocolConfidence": "authoritative" if wire_protocol and requested_channel.get("source") == "configured" else str(protocol_advice.get("confidence") or "hint"),
                "protocolSource": "channel" if wire_protocol and requested_channel.get("source") == "configured" else str(protocol_advice.get("source") or "fallback"),
                "protocolSourceRefs": list(protocol_advice.get("sourceRefs") or []),
                "protocolWarning": "" if wire_protocol and requested_channel.get("source") == "configured" else str(protocol_advice.get("warning") or ""),
                "provenance": {
                    "source": "quick_connect",
                    "confidence": "authoritative",
                },
            }
        mutation = model_control_plane.upsert_provider_model_records(
            provider_id=provider_id,
            provider_patch=next_provider,
            model_id=model_id,
            model_patch=next_model,
            source="quick_connect",
            replace_provider_models=bool(provider.get("singleActiveModel")),
        )
        saved = model_control_plane.get_public_config(dict(mutation.get("config") or {}))
        return {
            "ok": True,
            "providerId": provider_id,
            "modelId": model_id,
            "modelRef": make_model_ref(provider_id, model_id),
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


@router.get("/research-runtime/source-providers")
async def get_research_runtime_source_providers():
    try:
        system_base = storage.get_system_base_config()
        web_fetch = dict(system_base.get("webFetch") or {})
        configured_providers = web_fetch.get("providers") if isinstance(web_fetch.get("providers"), dict) else {}
        registry = get_source_provider_capabilities()
        providers: list[dict[str, Any]] = []
        for provider_id, capability in registry.items():
            if not isinstance(capability, dict):
                continue
            configured = configured_providers.get(provider_id) if isinstance(configured_providers.get(provider_id), dict) else {}
            auth_env = str(configured.get("authEnv") or capability.get("authEnv") or "").strip()
            api_key = str(configured.get("apiKey") or "").strip()
            providers.append(
                {
                    "id": provider_id,
                    "displayName": capability.get("displayName") or provider_id,
                    "region": capability.get("region") or "unknown",
                    "role": capability.get("role") or "discovery",
                    "supports": capability.get("supports") or [],
                    "costTier": capability.get("costTier") or "unknown",
                    "latencyTier": capability.get("latencyTier") or "unknown",
                    "requiresProxy": capability.get("requiresProxy", "auto"),
                    "supportsLoginProfile": bool(capability.get("supportsLoginProfile")),
                    "outputFormats": capability.get("outputFormats") or ["search_results"],
                    "implemented": bool(capability.get("implemented")),
                    "enabled": bool(configured.get("enabled", capability.get("enabledByDefault", True))),
                    "authEnv": auth_env,
                    "hasConfiguredKey": bool(api_key),
                    "baseUrl": str(configured.get("baseUrl") or capability.get("baseUrl") or "").strip(),
                    "credentialHelp": capability.get("credentialHelp") or {},
                }
            )
        return {
            "ok": True,
            "sourceRouter": web_fetch.get("sourceRouter") or get_source_router_defaults(),
            "providers": providers,
        }
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
