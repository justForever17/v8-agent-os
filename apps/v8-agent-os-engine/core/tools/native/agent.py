from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from core.actor_identity import resolve_collaboration_actor
from core.agents import normalize_specialist_family_id
from core.runtime_tool_access import normalize_subagent_runtime_bindings
from core.storage import StorageManager
from erc.runtime_context import get_runtime_context


storage = StorageManager()

_CREATE_INTENT_RE = re.compile(
    r"(?:创建|新增|建立|注册|同意|批准|确认|授权|可以|开始创建|create|add|register|approve|authorize|go\s+ahead|yes)",
    re.IGNORECASE,
)


def _json_payload(*, ok: bool, mode: str, summary: str, **extra: Any) -> str:
    payload = {"ok": ok, "tool": "agent_broker", "mode": mode, "summary": summary, **extra}
    return json.dumps(
        {key: value for key, value in payload.items() if value not in (None, "", [], {})},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item or ""))
        return "\n".join(part for part in parts if part.strip()).strip()
    return str(content or "").strip()


def _authorization_sources(state: dict[str, Any] | None) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    for message in reversed(list((state or {}).get("messages") or [])[-16:]):
        text = _message_text(message)
        if not text:
            continue
        if isinstance(message, HumanMessage):
            sources.append(("user", text))
            continue
        if isinstance(message, ToolMessage):
            name = str(getattr(message, "name", "") or "").strip().lower()
            additional = dict(getattr(message, "additional_kwargs", {}) or {})
            if name == "ask_user" or str(additional.get("interactionKind") or "").strip().lower() == "ask_user":
                sources.append(("ask_user", text))
    return sources


def _slug_agent_id(name: str, explicit_id: str = "") -> str:
    raw = str(explicit_id or name or "").strip().lower()
    normalized = re.sub(r"\s+", "-", raw)
    normalized = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE)
    normalized = re.sub(r"-+", "-", normalized).strip(".-_")
    if not normalized:
        normalized = f"agent-{hashlib.sha256(str(name or '').encode('utf-8')).hexdigest()[:10]}"
    if normalized == "supervisor":
        normalized = "supervisor-specialist"
    return normalized[:80]


def _compact_agent(agent: dict[str, Any]) -> dict[str, Any]:
    snapshot = agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {}
    bindings = normalize_subagent_runtime_bindings(snapshot.get("runtimeBindings") or snapshot.get("runtime_bindings"))
    return {
        "agentId": str(agent.get("id") or "").strip(),
        "name": str(agent.get("name") or agent.get("id") or "").strip(),
        "description": re.sub(r"\s+", " ", str(agent.get("description") or "").strip())[:320],
        "family": normalize_specialist_family_id(
            snapshot.get("specialistFamily") or snapshot.get("family") or agent.get("family")
        ),
        "runtimeBindings": bindings,
        "modelId": str(agent.get("model") or "").strip(),
        "toolMode": str(agent.get("tool_mode") or agent.get("toolMode") or "contextual_auto").strip(),
        "createdBy": str(agent.get("createdBy") or "").strip(),
        "enabled": agent.get("isEnabled") is not False,
    }


def _find_agent(*, agent_id: str = "", agent_name: str = "") -> tuple[dict[str, Any] | None, str]:
    agents = [agent for agent in storage.get_all_agents() if isinstance(agent, dict)]
    normalized_id = str(agent_id or "").strip()
    normalized_name = str(agent_name or "").strip().casefold()
    if normalized_id:
        return next((agent for agent in agents if str(agent.get("id") or "").strip() == normalized_id), None), ""
    if normalized_name:
        matches = [agent for agent in agents if str(agent.get("name") or "").strip().casefold() == normalized_name]
        if len(matches) > 1:
            return None, "agent_name_ambiguous"
        return (matches[0] if matches else None), ""
    return None, "agent_identity_required"


def _creation_payload(
    *,
    agent_id: str,
    name: str,
    description: str,
    family: str,
    system_prompt: str,
    model_id: str,
    runtime_bindings: Any,
    tool_mode: str,
    tools: list[str] | None,
    domain_tags: list[str] | None,
    operation_capabilities: list[str] | None,
    artifact_capabilities: list[str] | None,
) -> tuple[dict[str, Any] | None, str]:
    normalized_name = str(name or "").strip()
    normalized_description = re.sub(r"\s+", " ", str(description or "").strip())
    normalized_prompt = str(system_prompt or "").strip()
    if not normalized_name:
        return None, "name_required"
    if not normalized_description:
        return None, "description_required"
    if not normalized_prompt:
        return None, "system_prompt_required"
    if len(normalized_name) > 120 or len(normalized_description) > 1000 or len(normalized_prompt) > 30000:
        return None, "agent_contract_too_large"
    normalized_id = _slug_agent_id(normalized_name, agent_id)
    raw_bindings = runtime_bindings if runtime_bindings is not None else []
    bindings = normalize_subagent_runtime_bindings(raw_bindings)
    raw_binding_items = [raw_bindings] if isinstance(raw_bindings, (str, dict)) else list(raw_bindings or [])
    if raw_binding_items and not bindings:
        return None, "runtime_binding_invalid"
    normalized_tool_mode = str(tool_mode or "contextual_auto").strip().lower()
    if normalized_tool_mode not in {"contextual_auto", "explicit"}:
        return None, "tool_mode_invalid"
    family_id = normalize_specialist_family_id(family)

    def _text_list(values: list[str] | None, *, limit: int = 24) -> list[str]:
        result: list[str] = []
        for value in list(values or []):
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    payload = {
        "id": normalized_id,
        "name": normalized_name,
        "description": normalized_description,
        "model": str(model_id or "").strip(),
        "tools": _text_list(tools, limit=32),
        "tool_mode": normalized_tool_mode,
        "system_prompt": normalized_prompt,
        "createdBy": "supervisor",
        "globalExposure": False,
        "reflection_enabled": False,
        "max_reflections": 3,
        "capabilitySnapshot": {
            "agentClass": "specialist",
            "specialistFamily": family_id,
            "domainTags": _text_list(domain_tags),
            "operationCapabilities": _text_list(operation_capabilities),
            "artifactCapabilities": _text_list(artifact_capabilities),
            "runtimeAffinities": [binding["runtimeKind"] for binding in bindings],
            "runtimeBindings": bindings,
            "toolExposurePolicy": normalized_tool_mode,
            "source": "supervisor_created",
        },
    }
    return payload, ""


def _creation_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _authorization_result(
    *,
    state: dict[str, Any] | None,
    quote: str,
    provided_digest: str,
    expected_digest: str,
) -> tuple[bool, str]:
    normalized_quote = str(quote or "").strip()
    if not normalized_quote:
        return False, "authorization_quote_required"
    matched_source = ""
    for source_kind, source_text in _authorization_sources(state):
        if normalized_quote in source_text:
            matched_source = source_kind
            break
    if not matched_source:
        return False, "authorization_quote_not_observed"
    if not _CREATE_INTENT_RE.search(normalized_quote):
        return False, "authorization_intent_missing"
    if matched_source == "ask_user":
        if not provided_digest or provided_digest != expected_digest:
            return False, "authorization_digest_mismatch"
    elif provided_digest and provided_digest != expected_digest:
        return False, "authorization_digest_mismatch"
    return True, matched_source


@tool
def agent_broker(
    mode: str = "list",
    family: str = "",
    agentId: str = "",
    agentName: str = "",
    name: str = "",
    description: str = "",
    systemPrompt: str = "",
    modelId: str = "",
    runtimeBindings: Any = None,
    toolMode: str = "contextual_auto",
    tools: list[str] | None = None,
    domainTags: list[str] | None = None,
    operationCapabilities: list[str] | None = None,
    artifactCapabilities: list[str] | None = None,
    userAuthorizationQuote: str = "",
    authorizationDigest: str = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """List, inspect, create, or validate persistent registered subagents.

    This is the persistent Agent registry control plane; it never dispatches work.
    `list` without `family` returns all enabled registered subagents; with `family`
    it returns only that family. `create` requires a complete name, description,
    systemPrompt, optional model/runtime bindings, and explicit current-user approval.
    If approval is missing, use the returned askUser payload, then retry with the
    exact answer as userAuthorizationQuote and the unchanged authorizationDigest.
    After a successful create/validate, call delegation_broker separately with the
    exact task.targetAgentName. Delete remains an Admin-only operation.
    """

    normalized_mode = str(mode or "list").strip().lower()
    runtime_context = get_runtime_context() or {}
    has_actor_identity = any(
        runtime_context.get(key) not in (None, "")
        for key in ("actor_role", "actorRole", "runtime_kind", "runtimeKind", "agent_id", "agentId")
    )
    actor = resolve_collaboration_actor(
        runtime_context=runtime_context,
        actor=None if has_actor_identity else "supervisor",
    )
    if not actor.is_supervisor:
        return _json_payload(
            ok=False,
            mode=normalized_mode,
            summary="只有主理人可以管理持久子 Agent 注册表。",
            error="agent_broker_supervisor_only",
        )

    if normalized_mode == "list":
        family_id = normalize_specialist_family_id(family, default="") if str(family or "").strip() else ""
        items = []
        for agent in storage.get_all_agents():
            if not isinstance(agent, dict) or str(agent.get("id") or "").strip() == "supervisor" or agent.get("isEnabled") is False:
                continue
            compact = _compact_agent(agent)
            if family_id and compact.get("family") != family_id:
                continue
            items.append(compact)
        items.sort(key=lambda item: (str(item.get("family") or ""), str(item.get("name") or "").casefold()))
        return _json_payload(
            ok=True,
            mode=normalized_mode,
            summary=(f"找到 {len(items)} 个 {family_id} 家族子 Agent。" if family_id else f"找到 {len(items)} 个已注册子 Agent。"),
            family=family_id or None,
            items=items,
            count=len(items),
            nextAction="Choose an exact name, then call delegation_broker with task.targetAgentName.",
        )

    if normalized_mode in {"inspect", "validate"}:
        agent, error = _find_agent(agent_id=agentId, agent_name=agentName or name)
        if error or agent is None:
            return _json_payload(
                ok=False,
                mode=normalized_mode,
                summary="没有找到唯一匹配的已注册子 Agent。",
                error=error or "agent_not_found",
            )
        compact = _compact_agent(agent)
        if normalized_mode == "inspect":
            return _json_payload(ok=True, mode=normalized_mode, summary=f"已读取 {compact['name']} 的注册信息。", item=compact)
        default_model = str(storage.get_default_agent_model_id() or "").strip()
        effective_model = str(compact.get("modelId") or default_model).strip()
        ready = bool(compact.get("agentId") and compact.get("name") and compact.get("description") and effective_model)
        return _json_payload(
            ok=ready,
            mode=normalized_mode,
            summary=(f"{compact['name']} 已可用于本轮精确委派。" if ready else f"{compact['name']} 的模型绑定尚未就绪。"),
            status="ready" if ready else "needs_model_configuration",
            item={**compact, "effectiveModelId": effective_model},
            nextAction=("Call delegation_broker with task.targetAgentName." if ready else "Configure a model binding, then validate again."),
        )

    if normalized_mode != "create":
        return _json_payload(
            ok=False,
            mode=normalized_mode,
            summary="mode 只支持 list、inspect、create 或 validate。",
            error="unsupported_mode",
            supportedModes=["list", "inspect", "create", "validate"],
        )

    creation, error = _creation_payload(
        agent_id=agentId,
        name=name or agentName,
        description=description,
        family=family,
        system_prompt=systemPrompt,
        model_id=modelId,
        runtime_bindings=runtimeBindings,
        tool_mode=toolMode,
        tools=tools,
        domain_tags=domainTags,
        operation_capabilities=operationCapabilities,
        artifact_capabilities=artifactCapabilities,
    )
    if error or creation is None:
        return _json_payload(ok=False, mode=normalized_mode, summary="新子 Agent 合同不完整或无效。", error=error or "invalid_agent_contract")
    existing_by_id = storage.get_agent(str(creation.get("id") or ""))
    existing_by_name, name_error = _find_agent(agent_name=str(creation.get("name") or ""))
    if existing_by_id or existing_by_name or name_error == "agent_name_ambiguous":
        return _json_payload(
            ok=False,
            mode=normalized_mode,
            summary="相同 ID 或名称的持久子 Agent 已存在；请先 inspect，不要重复创建。",
            error="agent_already_exists",
            existing=(_compact_agent(existing_by_id or existing_by_name) if (existing_by_id or existing_by_name) else None),
        )

    digest = _creation_digest(creation)
    authorized, authorization_source = _authorization_result(
        state=state,
        quote=userAuthorizationQuote,
        provided_digest=str(authorizationDigest or "").strip(),
        expected_digest=digest,
    )
    if not authorized:
        compact = _compact_agent(creation)
        return _json_payload(
            ok=False,
            mode=normalized_mode,
            summary="创建持久子 Agent 会修改本机注册表，需要当前用户明确确认。",
            error=authorization_source,
            authorizationDigest=digest,
            proposedAgent=compact,
            askUser={
                "question": f"是否创建并注册子 Agent「{compact['name']}」？",
                "details": (
                    f"用途：{compact['description']}\n"
                    f"家族：{compact['family']}\n"
                    f"Runtime 绑定：{', '.join(item['runtimeKind'] for item in compact['runtimeBindings']) or '无'}\n"
                    "确认仅授权创建这一份注册合同；不会立即执行任务。"
                ),
            },
            nextAction="Call ask_user with askUser, then retry create with the unchanged authorizationDigest and the exact answer as userAuthorizationQuote.",
        )

    created_id = str(creation.get("id") or "").strip()
    try:
        storage.save_agent(creation)
        created = storage.get_agent(created_id)
        if not created:
            raise RuntimeError("created agent could not be reloaded")
    except Exception as exc:
        try:
            if storage.get_agent(created_id):
                storage.delete_agent(created_id)
        except Exception:
            pass
        return _json_payload(
            ok=False,
            mode=normalized_mode,
            summary="子 Agent 注册未完成，已回滚本次新建文件。",
            error="agent_create_failed",
            detail=str(exc)[:500],
        )

    compact = _compact_agent(created)
    return _json_payload(
        ok=True,
        mode=normalized_mode,
        summary=f"已创建并加载子 Agent「{compact['name']}」。",
        status="created",
        authorizationSource=authorization_source,
        item=compact,
        nextAction="Call agent_broker(mode='validate') and then delegation_broker with the exact task.targetAgentName.",
    )


__all__ = ["agent_broker"]
