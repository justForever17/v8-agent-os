from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from copy import deepcopy
from typing import Any, Iterable
from urllib.parse import urlparse

from core.database import db
from core.mcp_config_service import (
    list_mcp_server_configs,
    mcp_runtime_status_snapshot,
    request_mcp_inventory_refresh,
    validate_mcp_server_map,
)
from core.model_connection_tester import model_connection_tester
from core.model_control_plane import model_control_plane
from core.model_eligibility import evaluate_model_eligibility, model_category, model_kind
from core.model_ref import make_model_ref, parse_model_ref
from core.security.credentials import CredentialStoreError, credential_ref_store
from core.storage import storage
from core.time_truth import utc_now_iso
from erc.safety_guardian import safety_guardian


_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_API_STANDARDS = {"openai", "anthropic", "google", "gemini", "comfyui", "custom"}


class ConfigBrokerError(RuntimeError):
    def __init__(self, message: str, *, code: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return deepcopy(fallback)
    try:
        return json.loads(str(value))
    except Exception:
        return deepcopy(fallback)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _safe_refs(values: Iterable[Any] | None, *, limit: int = 12) -> list[str]:
    refs: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in refs:
            continue
        refs.append(text[:500])
        if len(refs) >= limit:
            break
    return refs


def _session_owner(session_id: str, explicit_owner: str = "") -> str:
    owner = str(explicit_owner or "").strip()
    if owner and owner.lower() != "anonymous":
        return owner
    session = db.get_session(str(session_id or "").strip()) if session_id else None
    session_owner = str((session or {}).get("user_id") or (session or {}).get("userId") or "").strip()
    return session_owner or owner


class ConfigBrokerService:
    """Durable model/MCP configuration control plane.

    Doctor validates declared facts, Safety decides whether a credential may be
    sent to the exact target, and this service alone owns commit/rollback.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

    @staticmethod
    def _target_snapshot(target_kind: str, target_id: str, config: dict[str, Any]) -> dict[str, Any]:
        if target_kind == "model":
            identity = parse_model_ref(target_id)
            if not identity:
                return {"providerExists": False, "modelExists": False}
            provider_id, model_id = identity
            providers = dict(config.get("providers") or {})
            provider_data = dict(providers.get(provider_id) or {})
            models = dict(provider_data.get("models") or {})
            return {
                "providerExists": provider_id in providers,
                "provider": dict(provider_data.get("provider") or {}),
                "modelExists": model_id in models,
                "model": dict(models.get(model_id) or {}),
            }
        if target_kind == "model_role":
            roles = dict(config.get("roles") or {})
            return {"exists": target_id in roles, "value": roles.get(target_id)}
        if target_kind == "agent_model_role":
            agents = dict((config.get("bindings") or {}).get("agents") or {})
            return {"exists": target_id in agents, "value": deepcopy(agents.get(target_id))}
        if target_kind == "mcp":
            servers = dict(config.get("mcpServers") or {})
            return {"exists": target_id in servers, "value": deepcopy(servers.get(target_id))}
        return {"unsupported": target_kind}

    @staticmethod
    def _target_config(target_kind: str) -> dict[str, Any]:
        if target_kind == "mcp":
            return deepcopy(storage.get_mcp_config() or {"mcpServers": {}})
        return model_control_plane.get_storage_safe_config()

    def _assert_target_revision(self, transaction: dict[str, Any]) -> dict[str, Any]:
        current_config = self._target_config(str(transaction.get("targetKind") or ""))
        try:
            return self._assert_target_revision_in_config(transaction, current_config)
        except ConfigBrokerError:
            self._update_transaction(
                str(transaction.get("transactionId") or ""),
                state="conflict",
                error_code="config_transaction_stale",
                error_message="目标配置已在计划后发生变化；事务未提交，也未覆盖新配置。",
            )
            raise

    def _assert_target_revision_in_config(
        self,
        transaction: dict[str, Any],
        current_config: dict[str, Any],
    ) -> dict[str, Any]:
        validation = dict(transaction.get("validation") or {})
        expected = str(validation.get("targetBeforeDigest") or "").strip()
        current_snapshot = self._target_snapshot(
            str(transaction.get("targetKind") or ""),
            str(transaction.get("targetId") or ""),
            current_config,
        )
        current_digest = _digest(current_snapshot)
        if expected and current_digest != expected:
            raise ConfigBrokerError(
                "目标配置已在计划后发生变化；请重新准备事务。",
                code="config_transaction_stale",
                status_code=409,
            )
        return current_snapshot

    def _insert_transaction(
        self,
        *,
        target_kind: str,
        target_id: str,
        operation: str,
        state: str,
        owner_id: str,
        session_id: str,
        run_id: str,
        before: dict[str, Any],
        proposed: dict[str, Any],
        validation: dict[str, Any] | None = None,
        error: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        transaction_id = f"cfg_txn_{uuid.uuid4().hex}"
        now = utc_now_iso()
        target_before = self._target_snapshot(target_kind, target_id, before)
        target_before_digest = _digest(target_before)
        validation_payload = {
            **dict(validation or {}),
            "targetBeforeDigest": target_before_digest,
        }
        plan_digest = _digest(
            {
                "targetKind": target_kind,
                "targetId": target_id,
                "operation": operation,
                "targetBeforeDigest": target_before_digest,
                "proposed": proposed,
            }
        )
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO config_broker_transactions
                (id,target_kind,target_id,operation,state,owner_id,session_id,run_id,plan_digest,
                 before_json,proposed_json,validation_json,error_code,error_message,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    transaction_id,
                    target_kind,
                    target_id,
                    operation,
                    state,
                    owner_id or None,
                    session_id or None,
                    run_id or None,
                    plan_digest,
                    _json(before),
                    _json(proposed),
                    _json(validation_payload),
                    (error or (None, None))[0],
                    (error or (None, None))[1],
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_transaction(transaction_id, owner_id=owner_id, include_private=True)

    def _update_transaction(self, transaction_id: str, **updates: Any) -> None:
        allowed = {
            "state",
            "proposed_json",
            "validation_json",
            "result_json",
            "error_code",
            "error_message",
            "committed_at",
            "rolled_back_at",
        }
        values = {key: value for key, value in updates.items() if key in allowed}
        values["updated_at"] = utc_now_iso()
        assignments = ",".join(f"{key}=?" for key in values)
        with db.get_connection() as conn:
            conn.execute(
                f"UPDATE config_broker_transactions SET {assignments} WHERE id=?",
                (*values.values(), transaction_id),
            )
            conn.commit()

    def get_transaction(
        self,
        transaction_id: str,
        *,
        owner_id: str = "",
        include_private: bool = False,
    ) -> dict[str, Any]:
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM config_broker_transactions WHERE id=?", (str(transaction_id or "").strip(),)).fetchone()
        if not row:
            raise ConfigBrokerError("配置事务不存在。", code="config_transaction_not_found", status_code=404)
        item = dict(row)
        stored_owner = str(item.get("owner_id") or "").strip()
        if stored_owner and stored_owner != str(owner_id or "").strip():
            raise ConfigBrokerError("配置事务不属于当前用户。", code="config_transaction_owner_mismatch", status_code=403)
        payload = {
            "transactionId": item["id"],
            "targetKind": item["target_kind"],
            "targetId": item["target_id"],
            "operation": item["operation"],
            "state": item["state"],
            "planDigest": item["plan_digest"],
            "validation": _loads(item.get("validation_json"), {}),
            "result": _loads(item.get("result_json"), {}),
            "error": (
                {"code": item.get("error_code"), "message": item.get("error_message")}
                if item.get("error_code") or item.get("error_message")
                else None
            ),
            "createdAt": item.get("created_at"),
            "updatedAt": item.get("updated_at"),
            "committedAt": item.get("committed_at"),
            "rolledBackAt": item.get("rolled_back_at"),
        }
        if include_private:
            payload["before"] = _loads(item.get("before_json"), {})
            payload["proposed"] = _loads(item.get("proposed_json"), {})
            payload["ownerId"] = stored_owner
            payload["sessionId"] = str(item.get("session_id") or "")
            payload["runId"] = str(item.get("run_id") or "")
        return payload

    def inventory(self, *, category: str = "", query: str = "", limit: int = 20, offset: int = 0) -> dict[str, Any]:
        config = model_control_plane.get_config()
        models = model_control_plane.list_models(config)
        provider_statuses = {
            str(item.get("providerId") or item.get("id") or ""): item
            for item in model_control_plane.get_provider_statuses(config)
        }
        normalized_category = str(category or "").strip().lower()
        category_aliases = {
            "llm": "text",
            "text_generation": "text",
            "multimodal": "vision",
            "image_understanding": "vision",
            "reranker": "rerank",
        }
        normalized_category = category_aliases.get(normalized_category, normalized_category)
        normalized_query = str(query or "").strip().lower()
        rows: list[dict[str, Any]] = []
        group_counts: dict[str, int] = {}
        for model in models:
            category_key = model_category(model)
            kind = model_kind(model)
            haystack = " ".join(
                [str(model.get("modelId") or ""), str(model.get("providerName") or ""), str(model.get("providerId") or "")]
            ).lower()
            if normalized_query and normalized_query not in haystack:
                continue
            provider_health = provider_statuses.get(str(model.get("providerId") or "")) or {}
            health_state = ""
            if str(provider_health.get("circuitState") or "") == "open":
                health_state = "circuit_open"
            elif int(provider_health.get("errorCount") or 0) > 0:
                health_state = "degraded"
            eligibility = evaluate_model_eligibility({**model, "healthStatus": health_state})
            group_counts[category_key] = group_counts.get(category_key, 0) + 1
            if normalized_category and normalized_category not in {
                category_key,
                kind,
                str(model.get("type") or "").lower(),
                str(model.get("capabilityClass") or "").lower(),
            }:
                continue
            rows.append(
                {
                    "modelRef": model.get("modelRef"),
                    "modelId": model.get("modelId"),
                    "providerId": model.get("providerId"),
                    "providerName": model.get("providerName"),
                    "type": model.get("type"),
                    "category": category_key,
                    "capabilityClass": model.get("capabilityClass"),
                    "status": eligibility.get("status"),
                    "statusLabel": eligibility.get("shortLabel"),
                    "contextWindow": model.get("contextWindow"),
                    "maxTokens": model.get("maxTokens"),
                    "defaultCategories": model.get("defaultCategories") or [],
                    "assignedRoles": model.get("assignedRoles") or [],
                    "providerHealth": provider_health.get("status") or provider_health.get("health") or None,
                    "requiredFacts": eligibility.get("requiredFacts") or [],
                    "warnings": [item.get("code") for item in eligibility.get("warnings") or []],
                }
            )
        rows.sort(
            key=lambda item: (
                0 if item.get("defaultCategories") else 1,
                0 if item.get("status") == "ready" else 1,
                str(item.get("providerName") or "").lower(),
                str(item.get("modelId") or "").lower(),
            )
        )
        bounded_limit = max(1, min(int(limit or 20), 50))
        bounded_offset = max(0, int(offset or 0))
        return {
            "ok": True,
            "mode": "inventory",
            "category": normalized_category or "all",
            "total": len(rows),
            "offset": bounded_offset,
            "limit": bounded_limit,
            "groups": [
                {"category": key, "count": group_counts[key]}
                for key in ("text", "vision", "embedding", "rerank", "media")
                if group_counts.get(key)
            ],
            "models": rows[bounded_offset : bounded_offset + bounded_limit],
            "summary": f"找到 {len(rows)} 个匹配模型；默认模型已置顶。",
        }

    def role_matrix(self) -> dict[str, Any]:
        config = model_control_plane.get_config()
        roles = []
        for card in model_control_plane.get_role_cards(config):
            roles.append(
                {
                    "role": card.get("key"),
                    "label": card.get("label"),
                    "group": card.get("group"),
                    "modelRef": card.get("resolvedModelRef"),
                    "model": card.get("resolvedModelName"),
                    "provider": card.get("resolvedProviderName"),
                    "binding": card.get("bindingState"),
                    "status": card.get("readiness"),
                    "reason": card.get("readinessReason"),
                }
            )
        models_by_ref = {str(item.get("modelRef") or ""): item for item in model_control_plane.list_models(config)}
        inherited = model_control_plane.resolve_model_for_role("subagent", config)
        agent_bindings = storage.get_agent_model_bindings()
        agents = []
        for agent in storage.get_all_agents():
            agent_id = str(agent.get("id") or "").strip()
            if not agent_id:
                continue
            explicit = str(agent_bindings.get(agent_id) or "").strip()
            explicit_record = model_control_plane.get_model_record(explicit, config) if explicit else None
            model_ref = str((explicit_record or {}).get("model_ref") or inherited.get("resolvedModelRef") or "")
            model_row = models_by_ref.get(model_ref) or {}
            eligibility = dict(model_row.get("eligibility") or evaluate_model_eligibility(model_row, role="subagent"))
            binding = "explicit" if explicit_record else ("invalid" if explicit else "inherited_subagent")
            agents.append(
                {
                    "role": f"agent:{agent_id}",
                    "agentId": agent_id,
                    "label": str(agent.get("name") or agent_id).strip() or agent_id,
                    "group": "subagent",
                    "modelRef": model_ref,
                    "model": model_row.get("modelId") or inherited.get("resolvedModelId") or "",
                    "provider": model_row.get("providerName") or "",
                    "binding": binding,
                    "status": "disabled" if agent.get("isEnabled") is False else eligibility.get("status") or "blocked",
                    "reason": eligibility.get("shortLabel") or "模型未就绪",
                }
            )
        agents.sort(key=lambda item: str(item.get("label") or "").lower())
        return {
            "ok": True,
            "mode": "role_matrix",
            "roles": roles,
            "agents": agents,
            "summary": f"当前有 {len(roles)} 个功能模型槽位和 {len(agents)} 个 Subagent 模型绑定。",
        }

    def _role_contract(self, role_key: str, config: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        definitions = model_control_plane.get_role_definitions(config)
        if role_key.startswith("agent:"):
            agent_id = role_key.removeprefix("agent:").strip()
            agent = storage.get_agent(agent_id) if agent_id else None
            if not agent:
                raise ConfigBrokerError("目标 Subagent 不存在。", code="model_agent_unknown", status_code=404)
            return dict(definitions["subagent"]), str(agent.get("name") or agent_id), agent_id
        if role_key not in definitions:
            raise ConfigBrokerError("未知模型角色。", code="model_role_unknown")
        return dict(definitions[role_key]), str(definitions[role_key].get("label") or role_key), ""

    def recommend(self, *, role: str, limit: int = 5) -> dict[str, Any]:
        config = model_control_plane.get_config()
        role_key = str(role or "").strip()
        role_definition, role_label, _agent_id = self._role_contract(role_key, config)
        candidates = []
        for model in model_control_plane.list_models(config):
            record = model_control_plane.get_model_record(str(model.get("modelRef") or ""), config)
            compatibility_record = {
                **dict(record or {}),
                "model": {
                    **dict((record or {}).get("model") or {}),
                    "capabilityClass": model.get("capabilityClass"),
                    "capabilities": dict(model.get("capabilities") or {}),
                    "type": model.get("type"),
                },
            }
            if not model_control_plane.is_model_compatible(role_definition, compatibility_record):
                continue
            eligibility = dict(model.get("eligibility") or {})
            if not eligibility.get("selectable"):
                continue
            score = 100
            if model.get("defaultCategories"):
                score += 20
            if role_key in list(model.get("assignedRoles") or []):
                score += 10
            if dict(model.get("capabilities") or {}).get("toolCalling") and role_key in {"supervisor", "subagent"}:
                score += 8
            candidates.append(
                {
                    "modelRef": model.get("modelRef"),
                    "modelId": model.get("modelId"),
                    "provider": model.get("providerName"),
                    "score": score,
                    "status": eligibility.get("shortLabel"),
                    "reason": "能力类别匹配且模型参数完整",
                }
            )
        candidates.sort(key=lambda item: (-int(item["score"]), str(item["provider"]), str(item["modelId"])))
        return {
            "ok": True,
            "mode": "recommend",
            "role": role_key,
            "candidates": candidates[: max(1, min(int(limit or 5), 10))],
            "summary": f"为 {role_label} 找到 {len(candidates)} 个可用候选。",
        }

    def prepare_model(
        self,
        *,
        provider_id: str,
        model_id: str,
        provider_name: str,
        base_url: str,
        api_standard: str,
        channel_id: str = "",
        wire_protocol: str = "",
        endpoint_path: str = "",
        model_type: str,
        context_window: int | None,
        max_tokens: int | None,
        capabilities: dict[str, Any] | None,
        evidence_refs: Iterable[Any] | None,
        credential_required: bool,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        provider_key = str(provider_id or "").strip()
        model_key = str(model_id or "").strip().strip("/")
        if not provider_key or not _PROVIDER_ID_RE.fullmatch(provider_key):
            raise ConfigBrokerError("providerId 格式无效。", code="provider_id_invalid")
        if not model_key:
            raise ConfigBrokerError("modelId 不能为空。", code="model_id_required")
        normalized_url = str(base_url or "").strip().rstrip("/")
        parsed_url = urlparse(normalized_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ConfigBrokerError("baseURL 必须是有效的 HTTP/HTTPS 地址。", code="provider_base_url_invalid")
        standard = str(api_standard or "openai").strip().lower()
        if standard not in _API_STANDARDS:
            raise ConfigBrokerError("apiStandard 不在受支持范围内。", code="provider_api_standard_invalid")
        refs = _safe_refs(evidence_refs)
        source = "web_research" if refs else "agent_proposed"
        caps = {str(key): bool(value) for key, value in dict(capabilities or {}).items()}
        model_patch = {
            "type": str(model_type or ("MULTIMODAL" if caps.get("vision") or caps.get("multimodal") else "TEXT")).strip().upper(),
            "contextWindow": int(context_window) if context_window else None,
            "maxTokens": int(max_tokens) if max_tokens else None,
            "capabilities": caps,
            "capabilitySource": source,
            "sourceRefs": refs,
            "isEnabled": True,
            "runtimeReady": True,
        }
        normalized_channel_id = str(channel_id or "").strip().lower()
        normalized_wire_protocol = str(wire_protocol or "").strip()
        normalized_endpoint_path = str(endpoint_path or "").strip().strip("/")
        if normalized_channel_id or normalized_wire_protocol or normalized_endpoint_path:
            model_patch["endpointBinding"] = {
                "version": 2,
                "route": model_key,
                "channelId": normalized_channel_id or "default",
                "wireProtocol": normalized_wire_protocol,
                "endpointPath": normalized_endpoint_path,
                "providerModelId": model_key,
                "protocolSource": "config_broker_explicit",
                "provenance": {
                    "source": "config_broker_explicit",
                    "confidence": "authoritative",
                },
            }
        eligibility = evaluate_model_eligibility(model_patch)
        provider_patch = {
            "name": str(provider_name or provider_key).strip() or provider_key,
            "base_url": normalized_url,
            "api_standard": standard,
            "is_enabled": True,
        }
        safe_before = model_control_plane.get_storage_safe_config()
        existing_provider = dict((safe_before.get("providers") or {}).get(provider_key) or {})
        existing_meta = dict(existing_provider.get("provider") or {})
        existing_ref = str(existing_meta.get("credentialRef") or "").strip()
        if existing_ref:
            provider_patch["credentialRef"] = existing_ref
        proposed = {
            "providerId": provider_key,
            "modelId": model_key,
            "provider": provider_patch,
            "model": model_patch,
            "source": source,
            "evidenceRefs": refs,
            "credentialRequired": bool(credential_required),
            "credentialPreviouslyConfigured": bool(existing_ref),
            "newCredentialRefs": [],
        }
        owner = _session_owner(session_id, owner_id)
        if eligibility.get("blocking"):
            transaction = self._insert_transaction(
                target_kind="model",
                target_id=make_model_ref(provider_key, model_key),
                operation="upsert",
                state="blocked",
                owner_id=owner,
                session_id=session_id,
                run_id=run_id,
                before=safe_before,
                proposed=proposed,
                validation={"doctor": eligibility, "evidenceRefs": refs},
                error=("model_facts_incomplete", "模型参数不完整；请补全上下文窗口和最大输出 tokens。"),
            )
            return {
                "ok": False,
                "mode": "model_prepare",
                "state": "blocked",
                "transactionId": transaction["transactionId"],
                "summary": "模型配置计划已阻断：缺少可供运行时使用的必要模型参数。",
                "requiredFacts": eligibility.get("requiredFacts") or [],
                "doctor": eligibility,
                "nextAction": "联网检索或由用户在 Model Hub 补全 contextWindow 与 maxTokens 后重新准备。",
            }
        needs_secret = bool(credential_required and not existing_ref)
        if needs_secret and (not owner or not str(session_id or "").strip() or not str(run_id or "").strip()):
            raise ConfigBrokerError(
                "安全凭据卡只能从已归属用户的活动会话中创建。",
                code="config_ui_action_scope_required",
                status_code=409,
            )
        transaction = self._insert_transaction(
            target_kind="model",
            target_id=make_model_ref(provider_key, model_key),
            operation="upsert",
            state="awaiting_secret" if needs_secret else "ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=safe_before,
            proposed=proposed,
            validation={"doctor": eligibility, "evidenceRefs": refs},
        )
        payload: dict[str, Any] = {
            "ok": True,
            "mode": "model_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "summary": "模型配置计划已验证，等待安全提交。",
            "doctor": eligibility,
            "evidenceRefs": refs,
            "nextAction": "提交配置事务。",
        }
        if needs_secret:
            from core.ui_action_requests import ui_action_request_service

            action = ui_action_request_service.create(
                kind="secret_input",
                owner_id=owner,
                session_id=session_id,
                run_id=run_id,
                title=f"连接 {provider_patch['name']}",
                description=f"API Key 仅会提交给 {parsed_url.hostname}，不会进入对话、工具参数或日志。",
                target_label=normalized_url,
                fields=[
                    {
                        "id": "apiKey",
                        "kind": "secret",
                        "label": "API Key",
                        "required": True,
                        "autocomplete": "off",
                        "binding": {"namespace": "model", "target": "provider", "targetName": "api_key"},
                    }
                ],
                handler_type="config_broker_secret",
                handler_ref=transaction["transactionId"],
            )
            payload["uiAction"] = action
            payload["nextAction"] = "等待用户在安全凭据卡中保存 API Key；保存后事务会自动验证并提交。"
        return payload

    def prepare_role_assignment(
        self,
        *,
        role: str,
        model_ref: str,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        config = model_control_plane.get_config()
        role_key = str(role or "").strip()
        role_definition, role_label, agent_id = self._role_contract(role_key, config)
        record = model_control_plane.get_model_record(str(model_ref or "").strip(), config)
        if not record:
            raise ConfigBrokerError("目标模型不存在。", code="model_not_found", status_code=404)
        model_row = next(
            (item for item in model_control_plane.list_models(config) if item.get("modelRef") == record.get("model_ref")),
            None,
        ) or {}
        eligibility = dict(model_row.get("eligibility") or evaluate_model_eligibility(model_row, role=role_key))
        compatibility_record = record
        if not isinstance(record.get("model"), dict):
            compatibility_record = {
                **record,
                "model": {
                    "capabilityClass": model_row.get("capabilityClass"),
                    "capabilities": dict(model_row.get("capabilities") or {}),
                    "type": model_row.get("type"),
                },
            }
        if not eligibility.get("selectable") or not model_control_plane.is_model_compatible(
            role_definition,
            compatibility_record,
        ):
            raise ConfigBrokerError("目标模型不满足该功能的运行条件。", code="model_role_ineligible", status_code=409)
        before = model_control_plane.get_storage_safe_config()
        proposed = {
            "role": role_key,
            "modelRef": str(record.get("model_ref") or model_ref),
            **({"agentId": agent_id} if agent_id else {}),
        }
        owner = _session_owner(session_id, owner_id)
        transaction = self._insert_transaction(
            target_kind="agent_model_role" if agent_id else "model_role",
            target_id=agent_id or role_key,
            operation="assign",
            state="ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed=proposed,
            validation={"doctor": eligibility},
        )
        return {
            "ok": True,
            "mode": "role_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "summary": f"已准备把 {role_label} 调整为 {record.get('model_id')}。",
            "nextAction": "提交配置事务。",
        }

    def _credentialize_mcp_config(self, raw: dict[str, Any]) -> dict[str, Any]:
        safe = deepcopy(raw or {"mcpServers": {}})
        servers = dict(safe.get("mcpServers") or {})
        changed = False
        created_refs: list[str] = []
        try:
            for server_name, raw_server in servers.items():
                server = dict(raw_server or {})
                refs = dict(server.get("x-v8-credential-refs") or {})
                for target in ("env", "headers"):
                    values = dict(server.get(target) or {})
                    for key, value in list(values.items()):
                        if value in (None, ""):
                            continue
                        binding_id = f"legacy_{target}_{key}"
                        if binding_id in refs:
                            values.pop(key, None)
                            changed = True
                            continue
                        reference = credential_ref_store.put(str(value), namespace="plugin")
                        created_refs.append(reference)
                        refs[binding_id] = {
                            "secretRef": reference,
                            "target": "header" if target == "headers" else "env",
                            "targetName": str(key),
                        }
                        values.pop(key, None)
                        changed = True
                    if values:
                        server[target] = values
                    else:
                        server.pop(target, None)
                if refs:
                    server["x-v8-credential-refs"] = refs
                servers[str(server_name)] = server
            safe["mcpServers"] = servers
            if changed:
                storage.save_mcp_config(safe)
            return safe
        except Exception:
            for reference in created_refs:
                try:
                    credential_ref_store.delete(reference)
                except Exception:
                    pass
            raise

    def prepare_mcp(
        self,
        *,
        operation: str,
        name: str,
        server: dict[str, Any] | None,
        credential_requirements: list[dict[str, Any]] | None,
        owner_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        server_name = str(name or "").strip()
        if not server_name:
            raise ConfigBrokerError("MCP server 名称不能为空。", code="mcp_name_required")
        try:
            before = self._credentialize_mcp_config(storage.get_mcp_config() or {"mcpServers": {}})
        except CredentialStoreError as exc:
            raise ConfigBrokerError(str(exc), code="credential_store_unavailable", status_code=503) from exc
        normalized_operation = str(operation or "install").strip().lower()
        if normalized_operation not in {"install", "remove"}:
            raise ConfigBrokerError("MCP 操作只支持 install/remove。", code="mcp_operation_invalid")
        proposed: dict[str, Any] = {"name": server_name, "operation": normalized_operation, "newCredentialRefs": []}
        requirements: list[dict[str, Any]] = []
        if normalized_operation == "install":
            normalized_server = validate_mcp_server_map({"mcpServers": {server_name: dict(server or {})}})[server_name]
            proposed["server"] = normalized_server
            for raw in credential_requirements or []:
                binding_id = str(raw.get("id") or raw.get("targetName") or "").strip()
                target = str(raw.get("target") or "env").strip().lower()
                target_name = str(raw.get("targetName") or "").strip()
                if not binding_id or target not in {"env", "header"} or not target_name:
                    raise ConfigBrokerError("MCP 凭据绑定必须声明 id、target 和 targetName。", code="mcp_credential_binding_invalid")
                requirements.append(
                    {
                        "id": binding_id,
                        "kind": "secret",
                        "label": str(raw.get("label") or target_name),
                        "required": bool(raw.get("required", True)),
                        "binding": {"namespace": "plugin", "target": target, "targetName": target_name},
                    }
                )
        owner = _session_owner(session_id, owner_id)
        if requirements and (not owner or not str(session_id or "").strip() or not str(run_id or "").strip()):
            raise ConfigBrokerError(
                "安全凭据卡只能从已归属用户的活动会话中创建。",
                code="config_ui_action_scope_required",
                status_code=409,
            )
        transaction = self._insert_transaction(
            target_kind="mcp",
            target_id=server_name,
            operation=normalized_operation,
            state="awaiting_secret" if requirements else "ready_to_commit",
            owner_id=owner,
            session_id=session_id,
            run_id=run_id,
            before=before,
            proposed=proposed,
            validation={"serverValidated": normalized_operation == "install"},
        )
        result: dict[str, Any] = {
            "ok": True,
            "mode": f"mcp_{normalized_operation}_prepare",
            "state": transaction["state"],
            "transactionId": transaction["transactionId"],
            "planDigest": transaction["planDigest"],
            "summary": f"MCP server `{server_name}` 的{('安装' if normalized_operation == 'install' else '移除')}计划已准备。",
            "nextAction": "提交配置事务。",
        }
        if requirements:
            from core.ui_action_requests import ui_action_request_service

            action = ui_action_request_service.create(
                kind="secret_input",
                owner_id=owner,
                session_id=session_id,
                run_id=run_id,
                title=f"配置 {server_name}",
                description="凭据只写入系统凭据库，不会进入 MCP 配置、对话或日志。",
                target_label=str((server or {}).get("url") or (server or {}).get("command") or server_name),
                fields=requirements,
                handler_type="config_broker_secret",
                handler_ref=transaction["transactionId"],
            )
            result["uiAction"] = action
            result["nextAction"] = "等待用户保存凭据；保存后事务会自动提交。"
        return result

    def attach_credentials_and_commit(
        self,
        transaction_id: str,
        *,
        bindings: list[dict[str, Any]],
        owner_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            transaction = self.get_transaction(transaction_id, owner_id=owner_id, include_private=True)
            if transaction["state"] != "awaiting_secret":
                raise ConfigBrokerError("该配置事务当前不接受凭据。", code="config_transaction_not_awaiting_secret", status_code=409)
            proposed = dict(transaction.get("proposed") or {})
            refs = list(proposed.get("newCredentialRefs") or [])
            refs.extend(str(item.get("secretRef") or "") for item in bindings if item.get("secretRef"))
            proposed["newCredentialRefs"] = sorted({item for item in refs if item})
            if transaction["targetKind"] == "model":
                provider_patch = dict(proposed.get("provider") or {})
                model_binding = next((item for item in bindings if str(item.get("target") or "") == "provider"), None)
                if not model_binding:
                    raise ConfigBrokerError("模型凭据绑定缺失。", code="model_credential_binding_missing")
                provider_patch["credentialRef"] = model_binding["secretRef"]
                provider_patch["credentialSource"] = "os_credential_store"
                proposed["provider"] = provider_patch
            else:
                server = dict(proposed.get("server") or {})
                server_refs = dict(server.get("x-v8-credential-refs") or {})
                for binding in bindings:
                    server_refs[str(binding.get("id") or binding.get("targetName") or uuid.uuid4().hex)] = {
                        "secretRef": binding.get("secretRef"),
                        "target": binding.get("target"),
                        "targetName": binding.get("targetName"),
                    }
                server["x-v8-credential-refs"] = server_refs
                proposed["server"] = server
            self._update_transaction(transaction_id, state="ready_to_commit", proposed_json=_json(proposed))
            return self.commit(transaction_id, owner_id=owner_id, user_confirmed_target=True)

    def _safety_check_model_target(self, proposed: dict[str, Any], *, user_confirmed_target: bool) -> dict[str, Any]:
        provider = dict(proposed.get("provider") or {})
        target = str(provider.get("base_url") or provider.get("baseUrl") or "").strip()
        has_managed_credential = bool(str(provider.get("credentialRef") or "").strip())
        decision = safety_guardian.assess_http_request(
            "POST",
            target,
            headers={"Authorization": "Bearer [managed-credential]"} if has_managed_credential else {},
            runtime_context={
                "source": "config_broker",
                "target": target,
                **({"credentialClass": "api_key"} if has_managed_credential else {}),
            },
        )
        payload = decision.to_payload()
        if decision.is_block():
            raise ConfigBrokerError(decision.reason, code=decision.risk_code or "safety_blocked", status_code=403)
        if decision.is_review() and not (user_confirmed_target and decision.allow_override):
            raise ConfigBrokerError(
                "目标需要用户确认后才能接收凭据。",
                code=decision.risk_code or "safety_review_required",
                status_code=409,
            )
        return {
            "verdict": decision.verdict,
            "riskCode": decision.risk_code,
            "userConfirmedExactTarget": bool(user_confirmed_target and decision.is_review()),
        }

    def commit(self, transaction_id: str, *, owner_id: str = "", user_confirmed_target: bool = False) -> dict[str, Any]:
        with self._lock:
            transaction = self.get_transaction(transaction_id, owner_id=owner_id, include_private=True)
            state = str(transaction.get("state") or "")
            if state == "committed":
                return {"ok": True, "mode": "commit", **self.get_transaction(transaction_id, owner_id=owner_id)}
            if state != "ready_to_commit":
                raise ConfigBrokerError("配置事务尚未达到可提交状态。", code="config_transaction_not_ready", status_code=409)
            self._assert_target_revision(transaction)
            proposed = dict(transaction.get("proposed") or {})
            validation = dict(transaction.get("validation") or {})
            target_mutated = False
            self._update_transaction(transaction_id, state="committing", error_code=None, error_message=None)

            def _capture_working_target(config: dict[str, Any]) -> None:
                working_target = self._target_snapshot(
                    transaction["targetKind"],
                    transaction["targetId"],
                    config,
                )
                validation["targetWorkingDigest"] = _digest(working_target)
                self._update_transaction(transaction_id, validation_json=_json(validation))

            try:
                if transaction["targetKind"] == "model":
                    validation["safety"] = self._safety_check_model_target(
                        proposed,
                        user_confirmed_target=bool(user_confirmed_target or proposed.get("credentialPreviouslyConfigured")),
                    )
                    result = model_control_plane.upsert_provider_model_records(
                        provider_id=str(proposed.get("providerId") or ""),
                        provider_patch=dict(proposed.get("provider") or {}),
                        model_id=str(proposed.get("modelId") or ""),
                        model_patch=dict(proposed.get("model") or {}),
                        source=str(proposed.get("source") or "agent_proposed"),
                        precondition=lambda current: self._assert_target_revision_in_config(transaction, current),
                    )
                    target_mutated = True
                    _capture_working_target(dict(result.get("config") or {}))
                    self._update_transaction(transaction_id, state="verifying", validation_json=_json(validation))
                    probe = model_connection_tester.test_model_connection(
                        provider_id=str(proposed.get("providerId") or ""),
                        model_id=str(proposed.get("modelId") or ""),
                        model_ref=make_model_ref(str(proposed.get("providerId") or ""), str(proposed.get("modelId") or "")),
                    )
                    validation["connection"] = {
                        "ok": bool(probe.get("ok")),
                        "status": probe.get("status"),
                        "summary": probe.get("message") or probe.get("summary"),
                    }
                    if not probe.get("ok"):
                        raise ConfigBrokerError("模型连接验证失败，已准备回滚。", code="model_connection_validation_failed", status_code=409)
                    public_result = {
                        "providerId": proposed.get("providerId"),
                        "modelId": proposed.get("modelId"),
                        "modelRef": make_model_ref(str(proposed.get("providerId") or ""), str(proposed.get("modelId") or "")),
                        "verified": True,
                    }
                elif transaction["targetKind"] == "model_role":
                    def _assign_role(config: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, config)
                        roles = dict(config.get("roles") or {})
                        roles[str(proposed.get("role") or "")] = str(proposed.get("modelRef") or "")
                        config["roles"] = roles
                        return config

                    saved_model_config = model_control_plane.mutate_config(_assign_role)
                    target_mutated = True
                    _capture_working_target(saved_model_config)
                    public_result = {"role": proposed.get("role"), "modelRef": proposed.get("modelRef")}
                elif transaction["targetKind"] == "agent_model_role":
                    def _assign_agent(config: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, config)
                        bindings = dict(config.get("bindings") or {})
                        agents = dict(bindings.get("agents") or {})
                        agents[str(proposed.get("agentId") or "")] = {"model_id": str(proposed.get("modelRef") or "")}
                        bindings["agents"] = agents
                        config["bindings"] = bindings
                        return config

                    saved_model_config = model_control_plane.mutate_config(_assign_agent)
                    target_mutated = True
                    _capture_working_target(saved_model_config)
                    public_result = {"agentId": proposed.get("agentId"), "modelRef": proposed.get("modelRef")}
                elif transaction["targetKind"] == "mcp":
                    server_name = str(proposed.get("name") or "")

                    def _mutate_mcp(current: dict[str, Any]) -> dict[str, Any]:
                        self._assert_target_revision_in_config(transaction, current)
                        servers = dict(current.get("mcpServers") or {})
                        if transaction["operation"] == "install":
                            servers[server_name] = deepcopy(dict(proposed.get("server") or {}))
                        else:
                            servers.pop(server_name, None)
                        current["mcpServers"] = servers
                        return current

                    saved_mcp = storage.mutate_mcp_config(_mutate_mcp)
                    target_mutated = True
                    _capture_working_target(saved_mcp)
                    request_mcp_inventory_refresh(reason="config_broker_commit")
                    public_result = {
                        "status": "success",
                        "installedServers": [server_name] if transaction["operation"] == "install" else [],
                        "removedServer": server_name if transaction["operation"] == "remove" else None,
                        "serverCount": len(dict(saved_mcp.get("mcpServers") or {})),
                        "refreshRequested": True,
                    }
                    validation["runtimeRefresh"] = {"requested": bool(public_result.get("refreshRequested"))}
                else:
                    raise ConfigBrokerError("不支持的配置事务目标。", code="config_transaction_target_invalid")
                target_after = self._target_snapshot(
                    transaction["targetKind"],
                    transaction["targetId"],
                    self._target_config(transaction["targetKind"]),
                )
                if target_mutated:
                    validation.setdefault("targetWorkingDigest", _digest(target_after))
                validation["targetAfterDigest"] = _digest(target_after)
                committed_at = utc_now_iso()
                self._update_transaction(
                    transaction_id,
                    state="committed",
                    validation_json=_json(validation),
                    result_json=_json(public_result),
                    committed_at=committed_at,
                )
                return {
                    "ok": True,
                    "mode": "commit",
                    "state": "committed",
                    "transactionId": transaction_id,
                    "summary": "配置已校验、提交并记录恢复点。",
                    "result": public_result,
                    "validation": validation,
                }
            except Exception as exc:
                code = exc.code if isinstance(exc, ConfigBrokerError) else "config_commit_failed"
                message = str(exc)
                if code == "config_transaction_stale":
                    self._update_transaction(
                        transaction_id,
                        state="conflict",
                        error_code=code,
                        error_message=message,
                    )
                    return {
                        "ok": False,
                        "mode": "commit",
                        "state": "conflict",
                        "transactionId": transaction_id,
                        "summary": "目标配置已变化；事务未写入，也未覆盖较新的配置。",
                        "error": {"code": code, "message": message},
                    }
                self._update_transaction(transaction_id, state="rolling_back", error_code=code, error_message=message)
                expected_digest = str(
                    validation.get("targetWorkingDigest" if target_mutated else "targetBeforeDigest") or ""
                ).strip()
                rollback = self._restore_snapshot(
                    transaction,
                    enforce_after_digest=True,
                    expected_current_digest=expected_digest,
                )
                state = (
                    "rolled_back"
                    if rollback.get("ok")
                    else ("conflict" if rollback.get("conflict") else "recovery_required")
                )
                self._update_transaction(
                    transaction_id,
                    state=state,
                    result_json=_json({"rollback": rollback}),
                    rolled_back_at=utc_now_iso() if rollback.get("ok") else None,
                )
                return {
                    "ok": False,
                    "mode": "commit",
                    "state": state,
                    "transactionId": transaction_id,
                    "summary": "配置提交失败，已回滚到提交前状态。" if rollback.get("ok") else "配置提交失败且自动恢复未完成，需要人工检查。",
                    "error": {"code": code, "message": message},
                    "rollback": rollback,
                }

    def _restore_snapshot(
        self,
        transaction: dict[str, Any],
        *,
        enforce_after_digest: bool = False,
        expected_current_digest: str = "",
    ) -> dict[str, Any]:
        errors: list[str] = []
        conflict = False
        target_kind = str(transaction.get("targetKind") or "")
        proposed = dict(transaction.get("proposed") or {})
        target_id = str(transaction.get("targetId") or proposed.get("name") or "")
        validation = dict(transaction.get("validation") or {})
        expected_after = str(
            expected_current_digest or validation.get("targetAfterDigest") or ""
        ).strip()

        def _assert_restore_revision(current: dict[str, Any]) -> None:
            if not enforce_after_digest:
                return
            current_snapshot = self._target_snapshot(target_kind, target_id, current)
            if not expected_after or _digest(current_snapshot) != expected_after:
                raise ConfigBrokerError(
                    "目标配置在提交后再次变化；为避免覆盖新配置，自动撤销已停止。",
                    code="config_rollback_conflict",
                    status_code=409,
                )
        try:
            before_config = dict(transaction.get("before") or {})
            before_target = self._target_snapshot(target_kind, target_id, before_config)
            if target_kind in {"model", "model_role", "agent_model_role"}:
                def _restore_model_config(config: dict[str, Any]) -> dict[str, Any]:
                    _assert_restore_revision(config)
                    if target_kind == "model":
                        identity = parse_model_ref(target_id)
                        if not identity:
                            raise ValueError("invalid model target")
                        provider_id, model_id = identity
                        providers = dict(config.get("providers") or {})
                        if not before_target.get("providerExists"):
                            providers.pop(provider_id, None)
                        else:
                            current_provider = dict(providers.get(provider_id) or {})
                            models = dict(current_provider.get("models") or {})
                            if before_target.get("modelExists"):
                                models[model_id] = deepcopy(before_target.get("model") or {})
                            else:
                                models.pop(model_id, None)
                            providers[provider_id] = {
                                **current_provider,
                                "provider": deepcopy(before_target.get("provider") or {}),
                                "models": models,
                            }
                        config["providers"] = providers
                    elif target_kind == "model_role":
                        roles = dict(config.get("roles") or {})
                        if before_target.get("exists"):
                            roles[target_id] = before_target.get("value")
                        else:
                            roles.pop(target_id, None)
                        config["roles"] = roles
                    else:
                        bindings = dict(config.get("bindings") or {})
                        agents = dict(bindings.get("agents") or {})
                        if before_target.get("exists"):
                            agents[target_id] = deepcopy(before_target.get("value"))
                        else:
                            agents.pop(target_id, None)
                        bindings["agents"] = agents
                        config["bindings"] = bindings
                    return config

                model_control_plane.mutate_config(_restore_model_config)
            elif target_kind == "mcp":
                def _restore_mcp_config(current: dict[str, Any]) -> dict[str, Any]:
                    _assert_restore_revision(current)
                    servers = dict(current.get("mcpServers") or {})
                    if before_target.get("exists"):
                        servers[target_id] = deepcopy(before_target.get("value") or {})
                    else:
                        servers.pop(target_id, None)
                    current["mcpServers"] = servers
                    return current

                storage.mutate_mcp_config(_restore_mcp_config)
                request_mcp_inventory_refresh(reason="config_broker_rollback")
            else:
                errors.append("unsupported snapshot target")
        except ConfigBrokerError as exc:
            if exc.code == "config_rollback_conflict":
                conflict = True
            errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))
        if not errors:
            for reference in list(proposed.get("newCredentialRefs") or []):
                try:
                    credential_ref_store.delete(str(reference))
                except Exception as exc:
                    errors.append(f"credential cleanup failed: {exc}")
        return {
            "ok": not errors,
            "conflict": conflict,
            **({"errorCode": "config_rollback_conflict"} if conflict else {}),
            "errors": errors,
        }

    def rollback(self, transaction_id: str, *, owner_id: str = "") -> dict[str, Any]:
        with self._lock:
            transaction = self.get_transaction(transaction_id, owner_id=owner_id, include_private=True)
            if transaction["state"] == "rolled_back":
                return {"ok": True, "mode": "rollback", **self.get_transaction(transaction_id, owner_id=owner_id)}
            if transaction["state"] != "committed":
                raise ConfigBrokerError("只有已提交事务可以执行精确撤销。", code="config_transaction_not_committed", status_code=409)
            self._update_transaction(transaction_id, state="rolling_back")
            rollback = self._restore_snapshot(transaction, enforce_after_digest=True)
            next_state = (
                "rolled_back"
                if rollback.get("ok")
                else ("conflict" if rollback.get("conflict") else "recovery_required")
            )
            self._update_transaction(
                transaction_id,
                state=next_state,
                result_json=_json({"rollback": rollback}),
                rolled_back_at=utc_now_iso() if rollback.get("ok") else None,
            )
            return {
                "ok": bool(rollback.get("ok")),
                "mode": "rollback",
                "state": next_state,
                "transactionId": transaction_id,
                "summary": (
                    "配置已恢复到事务前状态。"
                    if rollback.get("ok")
                    else (
                        "目标配置已在提交后变化；撤销已停止，未覆盖新配置。"
                        if rollback.get("conflict")
                        else "自动恢复未完成，需要人工检查。"
                    )
                ),
                "rollback": rollback,
            }

    def mcp_list(self) -> dict[str, Any]:
        payload = list_mcp_server_configs()
        return {"ok": True, "mode": "mcp_list", **payload, "summary": f"当前配置了 {payload.get('serverCount', 0)} 个 MCP server。"}

    def mcp_status(self) -> dict[str, Any]:
        payload = mcp_runtime_status_snapshot()
        return {"ok": not bool(payload.get("error")), "mode": "mcp_status", "status": payload, "summary": "已读取 MCP runtime 状态。" if not payload.get("error") else "MCP runtime 状态读取失败。"}


config_broker_service = ConfigBrokerService()


__all__ = ["ConfigBrokerError", "ConfigBrokerService", "config_broker_service"]
