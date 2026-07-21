from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException

from core.audio.audio_config import AudioConfigManager
from core.audit_logger import audit_logger
from core.model_control_plane import normalize_config_temperature
from core.models.control_plane import model_control_plane
from core.dependency_registry import build_dependency_status
from core.context_window_guard import validate_text_role_model_window
from core.prompt_budget import (
    DEFAULT_SUPERVISOR_PROMPT_BUDGET_TOKENS,
    DEFAULT_WORKSPACE_RULES_BUDGET_TOKENS,
    enforce_prompt_budget,
)
from core.supervisor_tool_policy import build_supervisor_tool_policy_snapshot
from core.system_base import detect_desktop_tools_readiness
from core.storage import MEMORY_DURABLE_POLICY_DEFAULTS, storage
from core.v8_link import build_link_manifest, build_mesh_provider_status, normalize_remote_link_config
from core.v8_agent_os_identity import render_system_identity_block
from core.v8_agent_os_paths import COMPUTER_USE_JSON_PATH, CONFIG_JSON_PATH, V8_AGENT_OS_HOME
from core.workspace_guard import build_workspace_path_status
from erc.runtime_stability import runtime_stability_service
from erc.safety_guardian import safety_guardian


router = APIRouter()


ConfigBuilder = Callable[[], dict[str, Any]]
ConfigSaver = Callable[[dict[str, Any]], dict[str, Any]]


def _get_network_supervisor_service():
    return importlib.import_module("runtimes.network_supervisor.service").network_supervisor_service


def _get_network_relay_worker_service():
    return importlib.import_module("runtimes.network_supervisor.relay_runtime").network_relay_worker_service


def _get_project_registry_service():
    return importlib.import_module("runtimes.memory.project_registry").project_registry_service


def _config_source(domain_path: str) -> str:
    if domain_path == "mcp":
        return "mcp.json"
    return f"config.json#{domain_path}"


def _config_save_path(domain_path: str) -> list[str]:
    if domain_path == "mcp":
        return [str(storage.mcp_config_path)]
    return [f"{CONFIG_JSON_PATH}#{domain_path}"]


def _roles_snapshot(*keys: str) -> dict[str, str]:
    config = model_control_plane.get_config()
    roles = dict(config.get("roles") or {})
    return {key: str(roles.get(key) or "").strip() for key in keys}


def _update_role_bindings(updates: dict[str, Any]) -> dict[str, str]:
    config = model_control_plane.get_config()
    roles = dict(config.get("roles") or {})
    for key, value in dict(updates or {}).items():
        role_key = str(key)
        model_ref = str(value or "").strip()
        validation = validate_text_role_model_window(role_key, model_ref)
        if not validation.get("ok"):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": validation.get("reason") or "invalid_context_window",
                    "message": validation.get("message") or "该文本模型上下文窗口不满足长上下文运行时要求。",
                    "role": role_key,
                    "modelRef": model_ref,
                    "minimumRequiredContextWindowTokens": validation.get("minimumRequiredContextWindowTokens"),
                    "participant": validation.get("participant"),
                },
            )
        roles[role_key] = model_ref
    config["roles"] = roles
    model_control_plane.save_config(config)
    return {key: str(roles.get(key) or "").strip() for key in updates.keys()}


def _update_role_parameters(updates: dict[str, Any]) -> dict[str, Any]:
    config = model_control_plane.get_config()
    role_parameters = dict(config.get("roleParameters") or {})
    for role_key, value in dict(updates or {}).items():
        existing = dict(role_parameters.get(str(role_key)) or {})
        if isinstance(value, dict) and "temperature" in value:
            existing["temperature"] = normalize_config_temperature(value.get("temperature"))
        role_parameters[str(role_key)] = existing
    config["roleParameters"] = role_parameters
    model_control_plane.save_config(config)
    return {key: dict(role_parameters.get(str(key)) or {}) for key in updates.keys()}


def _build_models_domain() -> dict[str, Any]:
    payload = model_control_plane.build_payload(model_control_plane.get_config())
    return {
        "domain": "models",
        "title": "模型中心",
        "summary": "集中维护供应商目录、模型目录与连接健康，不再承载功能模型重复配置。",
        "data": payload,
        "source": _config_source("models"),
        "savePath": _config_save_path("models"),
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["governance", "routingPolicies", "providersOverview", "governanceSummary"],
    }


def _save_models_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    current = model_control_plane.get_config()
    current_roles = dict(current.get("roles") or {})
    incoming_roles = dict(data.get("roles") or {})
    changed_roles = {
        str(key): str(value or "").strip()
        for key, value in incoming_roles.items()
        if str(current_roles.get(str(key)) or "").strip() != str(value or "").strip()
    }
    if changed_roles:
        for role_key, model_ref in changed_roles.items():
            validation = validate_text_role_model_window(role_key, model_ref)
            if not validation.get("ok"):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": validation.get("reason") or "invalid_context_window",
                        "message": validation.get("message") or "该文本模型上下文窗口不满足长上下文运行时要求。",
                        "role": role_key,
                        "modelRef": model_ref,
                        "minimumRequiredContextWindowTokens": validation.get("minimumRequiredContextWindowTokens"),
                        "participant": validation.get("participant"),
                    },
                )
    config = model_control_plane.save_config(data)
    return _build_models_domain() | {
        "data": model_control_plane.build_payload(config),
    }


def _build_supervisor_domain() -> dict[str, Any]:
    supervisor_config = storage.get_supervisor_config() or {}
    supervisor_profile = storage.get_supervisor_profile()
    supervisor_prompt = storage.get_supervisor_prompt()
    prompt_budget = enforce_prompt_budget(
        source="V8_AGENT_OS.md",
        text=supervisor_prompt,
        budget_tokens=DEFAULT_SUPERVISOR_PROMPT_BUDGET_TOKENS,
        truncate=True,
        omission_reason="supervisor_prompt_runtime_truncated",
    )
    tool_policy = build_supervisor_tool_policy_snapshot(supervisor_config.get("allowed_tools"))
    delegation = dict(supervisor_config.get("delegation") or {})
    return {
        "domain": "supervisor",
        "title": "主理人",
        "summary": "设置主理人的系统指令、角色绑定和公开资料。",
        "data": {
            "systemPrompt": supervisor_prompt,
            "promptBudgetDiagnostics": [prompt_budget.diagnostic()],
            "identity": storage.get_system_identity(),
            "identityBlock": render_system_identity_block(storage.get_system_identity()),
            "allowedTools": tool_policy["allowedTools"],
            "lockedNativeTools": tool_policy["lockedNativeTools"],
            "runtimeManagedTools": tool_policy["runtimeManagedTools"],
            "profile": {
                "name": supervisor_profile.get("name") or "智能主管",
                "roleLabel": supervisor_profile.get("roleLabel") or "主理人",
                "avatar": supervisor_profile.get("avatar") or "",
            },
            "bindings": {
                "supervisorModel": model_control_plane.get_role_model_id("supervisor") or "",
                "defaultReplyModel": model_control_plane.get_role_model_id("default") or "",
            },
            "modelParameters": {
                "supervisor": model_control_plane.get_role_parameters("supervisor"),
                "subagent": model_control_plane.get_role_parameters("subagent"),
            },
            "delegation": {
                "externalWorkers": list(delegation.get("externalWorkers") or []),
                "recursive": dict(delegation.get("recursive") or {}),
            },
            "specialistRegistry": dict(supervisor_config.get("specialistRegistry") or {}),
            "research": dict(supervisor_config.get("research") or {}),
        },
        "source": f"V8_AGENT_OS.md + {_config_source('systemBase.identity')} + {_config_source('supervisor')} + {_config_source('models')}",
        "savePath": [
            str(V8_AGENT_OS_HOME / "V8_AGENT_OS.md"),
            f"{CONFIG_JSON_PATH}#systemBase.identity",
            f"{CONFIG_JSON_PATH}#supervisor",
            f"{CONFIG_JSON_PATH}#models",
        ],
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["allowedTools", "systemPrompt"],
    }


def _save_supervisor_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    if "systemPrompt" in data:
        prompt_budget = enforce_prompt_budget(
            source="V8_AGENT_OS.md",
            text=str(data.get("systemPrompt") or ""),
            budget_tokens=DEFAULT_SUPERVISOR_PROMPT_BUDGET_TOKENS,
            truncate=False,
            omission_reason="supervisor_prompt_save_budget_exceeded",
        )
        if prompt_budget.save_rejected:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"V8_AGENT_OS.md 超过 {prompt_budget.budget_tokens} estimated tokens "
                    f"({prompt_budget.estimated_tokens})，已拒绝保存。"
                ),
            )
        storage.write_text("V8_AGENT_OS.md", str(data.get("systemPrompt") or ""))
    if "identity" in data and isinstance(data.get("identity"), dict):
        storage.save_system_base_config({"identity": dict(data.get("identity") or {})})

    supervisor_config = dict(storage.get_supervisor_config() or {})
    if "allowedTools" in data:
        allowed_tools = data.get("allowedTools")
        supervisor_config["allowed_tools"] = allowed_tools if allowed_tools is not None else None

    if "profile" in data and isinstance(data.get("profile"), dict):
        current_profile = storage.get_supervisor_profile()
        incoming_profile = dict(data.get("profile") or {})
        supervisor_config["profile"] = {
            "name": str(incoming_profile.get("name") or current_profile.get("name") or "智能主管"),
            "roleLabel": str(incoming_profile.get("roleLabel") or current_profile.get("roleLabel") or "主理人"),
            "avatar": str(incoming_profile.get("avatar") or current_profile.get("avatar") or ""),
        }

    if "delegation" in data and isinstance(data.get("delegation"), dict):
        delegation = dict(supervisor_config.get("delegation") or {})
        incoming = dict(data.get("delegation") or {})
        if "externalWorkers" in incoming:
            delegation["externalWorkers"] = list(incoming.get("externalWorkers") or [])
        if "recursive" in incoming and isinstance(incoming.get("recursive"), dict):
            recursive = dict(delegation.get("recursive") or {})
            recursive.update(dict(incoming.get("recursive") or {}))
            delegation["recursive"] = recursive
        supervisor_config["delegation"] = delegation

    if "specialistRegistry" in data and isinstance(data.get("specialistRegistry"), dict):
        supervisor_config["specialistRegistry"] = dict(data.get("specialistRegistry") or {})
    if "research" in data and isinstance(data.get("research"), dict):
        supervisor_config["research"] = dict(data.get("research") or {})

    storage.save_supervisor_config(supervisor_config)

    bindings = dict(data.get("bindings") or {})
    role_updates: dict[str, Any] = {}
    if "supervisorModel" in bindings:
        role_updates["supervisor"] = str(bindings.get("supervisorModel") or "").strip()
    if "defaultReplyModel" in bindings:
        role_updates["default"] = str(bindings.get("defaultReplyModel") or "").strip()
    if role_updates:
        _update_role_bindings(role_updates)
    if "modelParameters" in data and isinstance(data.get("modelParameters"), dict):
        _update_role_parameters(dict(data.get("modelParameters") or {}))
    return _build_supervisor_domain()


def _build_agents_domain() -> dict[str, Any]:
    agents = storage.get_all_agents()
    return {
        "domain": "agents",
        "title": "智能体",
        "summary": "维护子智能体目录、职责说明和模型绑定概览。",
        "data": {
            "count": len(agents),
            "agents": agents,
        },
        "source": f"agents/*.md + {_config_source('models')}",
        "savePath": [str(V8_AGENT_OS_HOME / "agents"), f"{CONFIG_JSON_PATH}#models"],
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["agents"],
    }


def _save_agents_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    agents = list(data.get("agents") or [])
    for agent in agents:
        if isinstance(agent, dict) and agent.get("id"):
            storage.save_agent(agent)
    return _build_agents_domain()


def _build_memory_domain() -> dict[str, Any]:
    config = storage.get_memory_config() or {}
    for key, value in MEMORY_DURABLE_POLICY_DEFAULTS.items():
        config.setdefault(key, value)
    bindings = _roles_snapshot("extraction", "embedding", "reranker")
    return {
        "domain": "memory",
        "title": "记忆管理",
        "summary": "控制记忆提取、召回和检索模型绑定。",
        "data": {
            **config,
            "modelBindings": {
                "extractionModel": bindings["extraction"],
                "embeddingModel": bindings["embedding"],
                "rerankerModel": bindings["reranker"],
            },
            "durablePolicyDefaults": deepcopy(MEMORY_DURABLE_POLICY_DEFAULTS),
        },
        "source": f"{_config_source('memory')} + {_config_source('models')}",
        "savePath": [f"{CONFIG_JSON_PATH}#memory", f"{CONFIG_JSON_PATH}#models"],
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["graph_enabled", "fts_enabled", "retrieval_threshold"],
    }


def _save_memory_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    model_bindings = dict(data.pop("modelBindings", {}) or {})
    data.pop("durablePolicyDefaults", None)
    _update_role_bindings(
        {
            "extraction": model_bindings.get("extractionModel"),
            "embedding": model_bindings.get("embeddingModel"),
            "reranker": model_bindings.get("rerankerModel"),
        }
    )
    storage.save_memory_config(data)
    return _build_memory_domain()


def _build_extensions_domain() -> dict[str, Any]:
    config = storage.get_extensions_config() or {}
    bindings = _roles_snapshot("extensions_prefilter", "extensions_reranker")
    policy = dict(config.get("prefilterPolicy") or {})
    skills_policy = dict(policy.get("skills") or {})
    mcp_policy = dict(policy.get("mcp") or {})
    return {
        "domain": "extensions",
        "title": "扩展生态",
        "summary": "控制普通 Skills 与 MCP 候选树是否进入 LLM 预筛，并绑定扩展专用预筛模型。插件能力不参与该预筛。",
        "data": {
            "prefilterPolicy": {
                "enabled": bool(policy.get("enabled")),
                "mode": str(policy.get("mode") or "two_stage").strip() or "two_stage",
                "skills": {
                    "stage1Enabled": bool(skills_policy.get("stage1Enabled", True)),
                    "stage1TopK": int(skills_policy.get("stage1TopK") or 20),
                    "llmEnabled": bool(skills_policy.get("llmEnabled", True)),
                    "stage2TopK": int(skills_policy.get("stage2TopK") or 5),
                    "llmTimeoutSeconds": int(skills_policy.get("llmTimeoutSeconds") or 5),
                },
                "mcp": {
                    "stage1Enabled": bool(mcp_policy.get("stage1Enabled", True)),
                    "stage1TopK": int(mcp_policy.get("stage1TopK") or 20),
                    "llmEnabled": bool(mcp_policy.get("llmEnabled", True)),
                    "stage2TopK": int(mcp_policy.get("stage2TopK") or 2),
                    "llmTimeoutSeconds": int(mcp_policy.get("llmTimeoutSeconds") or 5),
                },
            },
            "modelBindings": {
                "prefilterModel": bindings["extensions_prefilter"] or bindings["extensions_reranker"],
            },
        },
        "source": f"{_config_source('extensions')} + {_config_source('models')}",
        "savePath": [f"{CONFIG_JSON_PATH}#extensions", f"{CONFIG_JSON_PATH}#models"],
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["prefilterPolicy", "modelBindings"],
    }


def _save_extensions_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    prefilter_policy = dict(data.get("prefilterPolicy") or {})
    model_bindings = dict(data.get("modelBindings") or {})
    skills_policy = dict(prefilter_policy.get("skills") or {})
    mcp_policy = dict(prefilter_policy.get("mcp") or {})
    storage.save_extensions_config({
        "prefilterPolicy": {
            "enabled": bool(prefilter_policy.get("enabled", False)),
            "mode": str(prefilter_policy.get("mode") or "two_stage").strip() or "two_stage",
            "skills": {
                "stage1Enabled": bool(skills_policy.get("stage1Enabled", True)),
                "stage1TopK": int(skills_policy.get("stage1TopK") or 20),
                "llmEnabled": bool(skills_policy.get("llmEnabled", True)),
                "stage2TopK": int(skills_policy.get("stage2TopK") or 5),
                "llmTimeoutSeconds": int(skills_policy.get("llmTimeoutSeconds") or 5),
            },
            "mcp": {
                "stage1Enabled": bool(mcp_policy.get("stage1Enabled", True)),
                "stage1TopK": int(mcp_policy.get("stage1TopK") or 20),
                "llmEnabled": bool(mcp_policy.get("llmEnabled", True)),
                "stage2TopK": int(mcp_policy.get("stage2TopK") or 2),
                "llmTimeoutSeconds": int(mcp_policy.get("llmTimeoutSeconds") or 5),
            },
        },
    })
    _update_role_bindings({"extensions_prefilter": model_bindings.get("prefilterModel")})
    return _build_extensions_domain()


def _build_engineering_lane_domain() -> dict[str, Any]:
    config = storage.get_engineering_lane_config() or {}
    return {
        "domain": "engineering-lane",
        "title": "Engineering Runtime",
        "summary": "工程专用 ContextPack、Proof Ledger 与行为链提示治理。",
        "data": {
            **config,
        },
        "source": _config_source("engineeringLane"),
        "savePath": [f"{CONFIG_JSON_PATH}#engineeringLane"],
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": [
            "triggerMode",
            "worktreePlacement",
            "worktreeRoot",
            "contextPackBudget",
            "evidenceGraphEnabled",
            "codingExecutionContractEnabled",
            "worksetGovernanceMode",
            "worksetObservationEnabled",
            "workbenchDryRunMatrixEnabled",
            "proofLedgerEnabled",
            "suppressDailyMemory",
            "suppressMemoryMap",
            "rankedWorkflowPathCount",
        ],
    }


def _save_engineering_lane_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    data.pop("modelBindings", None)
    data.pop("plannerReadiness", None)
    storage.save_engineering_lane_config(data)
    return _build_engineering_lane_domain()


def _build_context_domain() -> dict[str, Any]:
    return {
        "domain": "context",
        "title": "上下文预算与治理",
        "summary": "调整 token 预算、历史压缩、运行时适配窗口与摘要模型绑定。",
        "data": {
            "policy": storage.get_context_config() or {},
            "modelBindings": {
                "summaryModel": model_control_plane.get_role_model_id("summary") or "",
            },
        },
        "source": f"{_config_source('context')} + {_config_source('models')}",
        "savePath": [f"{CONFIG_JSON_PATH}#context", f"{CONFIG_JSON_PATH}#models"],
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["runtime_adapters", "compression"],
    }


def _save_context_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    policy = dict(data.get("policy") or {})
    model_bindings = dict(data.get("modelBindings") or {})
    _update_role_bindings({"summary": model_bindings.get("summaryModel")})
    storage.save_context_config(policy)
    return _build_context_domain()


def _build_audio_domain() -> dict[str, Any]:
    return {
        "domain": "audio",
        "title": "多模设置",
        "summary": "配置语音识别、语音合成和音频服务提供商。",
        "data": AudioConfigManager.get_config(),
        "source": _config_source("audio"),
        "savePath": _config_save_path("audio"),
        "reloadRequired": True,
        "warnings": [],
        "advancedFields": ["stt.providers", "stt.model_ref", "tts.custom", "tts.edge_tts", "tts.model_ref"],
    }


def _save_audio_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    AudioConfigManager.save_config(data)
    return _build_audio_domain()


def _build_hooks_domain() -> dict[str, Any]:
    return {
        "domain": "hooks",
        "title": "动作钩子",
        "summary": "管理生命周期钩子、触发目标和启用状态。",
        "data": storage.get_hooks_config() or {"hooks": []},
        "source": _config_source("hooks"),
        "savePath": _config_save_path("hooks"),
        "reloadRequired": True,
        "warnings": [],
        "advancedFields": ["hooks"],
    }


def _save_hooks_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    storage.save_hooks_config(data)
    return _build_hooks_domain()


def _build_cron_domain() -> dict[str, Any]:
    config = storage.get_cron_config() or {"jobs": []}
    return {
        "domain": "cron",
        "title": "定时任务",
        "summary": "管理系统定时任务、触发计划和输出策略。",
        "data": config,
        "source": _config_source("cron"),
        "savePath": _config_save_path("cron"),
        "reloadRequired": True,
        "warnings": [],
        "advancedFields": ["jobs"],
    }


def _save_cron_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    storage.save_cron_config(data)
    try:
        from core.cron_manager import cron_manager

        cron_manager.sync_jobs_to_scheduler()
    except Exception:
        pass
    return _build_cron_domain()


def _build_automation_runtime_domain() -> dict[str, Any]:
    config = storage.get_automation_runtime_config()
    wake_policies = dict(config.get("wakeIngressPolicies") or {})
    return {
        "domain": "automation-runtime",
        "title": "AUTOMATION RUNTIME",
        "summary": "控制非人类触发入口的 Wake ingress 策略，不再提供独立 heartbeat 功能面。",
        "data": config,
        "source": _config_source("automationRuntime"),
        "savePath": _config_save_path("automationRuntime"),
        "reloadRequired": True,
        "warnings": [
            "没有显式 targetBinding 或 recoveryAnchor 的 hooks/cron 只会被当作 nudge，不会获得隐式 wake 权限。",
        ] if wake_policies else [],
        "advancedFields": ["wakeIngressPolicies"],
    }


def _save_automation_runtime_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    storage.save_automation_runtime_config(data)
    try:
        from core.cron_manager import cron_manager

        cron_manager.sync_jobs_to_scheduler()
    except Exception:
        pass
    return _build_automation_runtime_domain()


def _build_workspace_domain() -> dict[str, Any]:
    workspace_config = storage.get_workspace_config() or {}
    workspace_path = str(workspace_config.get("agent_workspace_path") or "").strip()
    path_status = build_workspace_path_status(workspace_path)
    agents_md_path = Path(workspace_path).expanduser() / ".agents" / "rules" / "AGENTS.md" if workspace_path else None
    agents_md_content = ""
    agents_md_budget: dict[str, object] | None = None
    if agents_md_path and agents_md_path.is_file():
        try:
            agents_md_content = agents_md_path.read_text(encoding="utf-8")
        except Exception:
            agents_md_content = ""
        budget_result = enforce_prompt_budget(
            source=str(agents_md_path),
            text=agents_md_content,
            budget_tokens=DEFAULT_WORKSPACE_RULES_BUDGET_TOKENS,
            truncate=True,
            omission_reason="workspace_agents_md_runtime_truncated",
        )
        agents_md_budget = budget_result.diagnostic()
    return {
        "domain": "workspace",
        "title": "工作区",
        "summary": "管理工作区路径和项目默认工作目录。",
        "data": {
            **workspace_config,
            "pathStatus": path_status,
            "agentsRules": {
                "canonicalPath": str(agents_md_path) if agents_md_path else "",
                "exists": bool(agents_md_path and agents_md_path.is_file()),
                "budgetDiagnostics": agents_md_budget,
            },
        },
        "source": _config_source("workspace"),
        "savePath": _config_save_path("workspace"),
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": [],
    }


def _save_workspace_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    raw_path = str(data.get("agent_workspace_path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="主工作区路径不能为空")
    normalized_path = str(Path(raw_path).expanduser())
    if not Path(normalized_path).is_absolute():
        raise HTTPException(status_code=400, detail="主工作区必须使用绝对路径")
    path_status = build_workspace_path_status(normalized_path)
    if bool(path_status.get("isLegacyResidue")):
        audit_logger.log(
            source_type="SYSTEM",
            action="workspace_legacy_residue_blocked",
            status="WARNING",
            details=json.dumps(
                {
                    "path": normalized_path,
                    "reason": path_status.get("legacyReason") or path_status.get("reason"),
                    "recommendedPath": path_status.get("recommendedPath"),
                    "source": "config_registry.workspace.save",
                },
                ensure_ascii=False,
            ),
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"{path_status.get('reason')} 推荐路径：{path_status.get('recommendedPath') or ''}".strip()
            ),
        )
    storage.save_workspace_config({"agent_workspace_path": normalized_path})
    return _build_workspace_domain()


def _build_runtime_stability_domain() -> dict[str, Any]:
    payload = runtime_stability_service.build_payload()
    return {
        "domain": "runtime-stability",
        "title": "稳定性策略",
        "summary": "控制长期任务的持久化护栏和同会话任务策略。",
        "data": payload,
        "source": _config_source("runtimeStability"),
        "savePath": _config_save_path("runtimeStability"),
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["paths", "allowedSessionLanePolicies", "summaries"],
    }


def _save_runtime_stability_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    runtime_stability_service.save_config(data)
    return _build_runtime_stability_domain()


def _build_safety_domain() -> dict[str, Any]:
    safety_review_model = model_control_plane.get_role_model_id("safety_review") or ""
    recent_skill_scans = []
    verdict_counts: dict[str, int] = {"audit": 0, "review": 0, "block": 0}
    skill_verdict_counts: dict[str, int] = {"audit": 0, "review": 0, "block": 0}
    try:
        for item in audit_logger.get_logs(limit=50, source_type="SAFETY"):
            details_raw = str(item.get("details") or "").strip()
            details = json.loads(details_raw) if details_raw else {}
            if not isinstance(details, dict):
                details = {}
            verdict = str(details.get("verdict") or "unknown").strip() or "unknown"
            if verdict in {"audit", "review", "block"}:
                verdict_counts[verdict] = int(verdict_counts.get(verdict) or 0) + 1
            if str(item.get("action") or "").strip() != "skill_scan":
                continue
            if verdict in {"audit", "review", "block"}:
                skill_verdict_counts[verdict] = int(skill_verdict_counts.get(verdict) or 0) + 1
            if len(recent_skill_scans) < 6:
                recent_skill_scans.append(
                    {
                        "skillName": str(details.get("skillName") or "").strip() or "未知 Skill",
                        "verdict": verdict,
                        "confidence": details.get("confidence"),
                        "skillTrustScore": details.get("skillTrustScore"),
                        "auditId": str(details.get("auditId") or item.get("id") or "").strip(),
                        "timestamp": item.get("timestamp"),
                        "reasons": list(details.get("reasons") or [])[:4],
                    }
                )
    except Exception:
        recent_skill_scans = []
        verdict_counts = {"audit": 0, "review": 0, "block": 0}
        skill_verdict_counts = {"audit": 0, "review": 0, "block": 0}

    safety_config = safety_guardian.export_config()
    posture = str(safety_config.get("machinePosture") or "dedicated_runtime_host").strip() or "dedicated_runtime_host"

    return {
        "domain": "safety",
        "title": "安全控制",
        "summary": "按机器姿态、治理目标和执行面定义安全护栏，默认以审计优先、恶意链路复核/阻断为主。",
        "data": {
            **safety_config,
            "modelBindings": {
                "safetyReviewModel": safety_review_model,
            },
            "governancePolicies": {
                "machinePosture": posture,
                "governanceTargets": [
                    "system_integrity",
                    "v8_integrity",
                    "private_data_exfiltration",
                    "skill_supply_chain",
                    "external_mutation",
                    "operator_posture",
                ],
                "skillStrategy": "declaration_audit_first",
            },
            "runtimeSummary": {
                "machinePosture": posture,
                "mode": "goal_layered_posture_aware",
                "llmBound": bool(safety_review_model),
                "safetyReviewModel": safety_review_model or None,
                "auditCount": int(verdict_counts.get("audit") or 0),
                "reviewCount": int(verdict_counts.get("review") or 0),
                "blockCount": int(verdict_counts.get("block") or 0),
                "verdictDistribution": verdict_counts,
            },
            "skillScanSummary": {
                "enabled": True,
                "verdictDistribution": skill_verdict_counts,
                "recentSkillScans": recent_skill_scans,
            },
        },
        "source": f"{_config_source('safety')} + {_config_source('models')}",
        "savePath": [f"{CONFIG_JSON_PATH}#safety", f"{CONFIG_JSON_PATH}#models"],
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": [
            "machinePosture",
            "commandRules",
            "runtimeRules",
            "skillRules",
            "networkMutationRules",
            "computerUseRules",
            "systemIntegrityRules",
            "v8IntegrityRules",
            "channelGroupGuard",
            "postActionRules",
            "modelBindings",
        ],
    }


def _save_safety_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    model_bindings = dict(data.pop("modelBindings", {}) or {})
    _update_role_bindings({"safety_review": model_bindings.get("safetyReviewModel")})
    safety_guardian.save_config(data)
    return _build_safety_domain()


def _build_computer_use_domain() -> dict[str, Any]:
    memory_config = storage.get_computer_use_memory() or {"version": 1, "apps": {}}
    runtime_config = storage.get_computer_use_config() or {}
    browser_lane = dict(runtime_config.get("browserLane") or {})
    observation_policy = dict(runtime_config.get("observationPolicy") or {})
    input_policy = dict(runtime_config.get("inputPolicy") or {})
    return {
        "domain": "computer-use",
        "title": "桌面操作",
        "summary": "控制桌面操作的规划模型、视觉裁判和环境感知策略。",
        "data": {
            "modelBindings": {
                "plannerModel": model_control_plane.get_role_model_id("computer_use_planner") or "",
                "visualJudgeModel": model_control_plane.get_role_model_id("computer_use_visual_judge") or "",
                "ocrAssistModel": model_control_plane.get_role_model_id("vision") or "",
                "candidateRerankerModel": model_control_plane.get_role_model_id("computer_use_candidate_reranker") or "",
                "fallbackRerankerModel": model_control_plane.get_role_model_id("reranker") or "",
            },
            "candidateRerankEnabled": bool(runtime_config.get("candidateRerankEnabled", False)),
            "browserLane": {
                "enabled": bool(browser_lane.get("enabled", True)),
                "mode": str(browser_lane.get("mode") or "auto_if_available"),
                "provider": str(browser_lane.get("provider") or "engine_managed_cdp"),
                "proxyPort": int(browser_lane.get("proxyPort") or 3456),
                "connectTimeoutMs": int(browser_lane.get("connectTimeoutMs") or 3000),
                "targetFamilies": list(browser_lane.get("targetFamilies") or ["chromium", "electron", "webview2"]),
                "allowManagedLaunch": bool(browser_lane.get("allowManagedLaunch", True)),
                "profileMode": str(browser_lane.get("profileMode") or "dedicated_debug_profile"),
                "userDataDir": str(browser_lane.get("userDataDir") or browser_lane.get("debugUserDataDir") or ""),
            },
            "observationPolicy": {
                "frameSequenceEnabled": bool(observation_policy.get("frameSequenceEnabled", True)),
                "frameSequenceCount": int(observation_policy.get("frameSequenceCount") or 3),
                "frameSequenceIntervalMs": int(observation_policy.get("frameSequenceIntervalMs") or 200),
            },
            "inputPolicy": {
                "normalizeDeterministicTextIme": bool(input_policy.get("normalizeDeterministicTextIme", True)),
            },
            "memoryProfiles": memory_config,
            "environmentPolicy": {
                "runtimeFirst": True,
                "interruptRequiresBlocker": True,
                "noiseIgnoredByDefault": True,
            },
        },
        "source": f"{_config_source('computerUse')} + {_config_source('models')} + {COMPUTER_USE_JSON_PATH}",
        "savePath": [f"{CONFIG_JSON_PATH}#computerUse", f"{CONFIG_JSON_PATH}#models", str(COMPUTER_USE_JSON_PATH)],
        "reloadRequired": False,
        "warnings": [
            "普通系统通知、切歌和无关窗口切换默认不会升级为桌面操作中断。",
            "候选重排只作用于文本化候选排序，不会替代视觉裁判。",
        ],
        "advancedFields": ["browserLane", "observationPolicy", "inputPolicy", "memoryProfiles", "environmentPolicy"],
    }


def _save_computer_use_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    model_bindings = dict(data.get("modelBindings") or {})
    _update_role_bindings(
        {
            "computer_use_planner": model_bindings.get("plannerModel"),
            "computer_use_visual_judge": model_bindings.get("visualJudgeModel"),
            "vision": model_bindings.get("ocrAssistModel"),
            "computer_use_candidate_reranker": model_bindings.get("candidateRerankerModel"),
        }
    )
    storage.save_computer_use_config(
        {
            "candidateRerankEnabled": bool(data.get("candidateRerankEnabled", False)),
            "browserLane": dict(data.get("browserLane") or {}),
            "observationPolicy": dict(data.get("observationPolicy") or {}),
            "inputPolicy": dict(data.get("inputPolicy") or {}),
        }
    )
    if isinstance(data.get("memoryProfiles"), dict):
        storage.save_computer_use_memory(data.get("memoryProfiles") or {})
    return _build_computer_use_domain()


def _build_rpa_domain() -> dict[str, Any]:
    roles = _roles_snapshot("rpa_discovery")
    return {
        "domain": "rpa",
        "title": "RPA Runtime",
        "summary": "控制 RPA 发现模型和执行保护策略。",
        "data": {
            "modelBindings": {
                "discoveryModel": roles.get("rpa_discovery") or "",
            },
            "executionPolicy": {
                "runtimeFirst": True,
                "localRecoveryPreferred": True,
                "sideEffectIdempotency": True,
            },
        },
        "source": _config_source("models"),
        "savePath": _config_save_path("models"),
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["executionPolicy"],
    }


def _save_rpa_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    model_bindings = dict(data.get("modelBindings") or {})
    _update_role_bindings({"rpa_discovery": model_bindings.get("discoveryModel")})
    return _build_rpa_domain()


def _build_mcp_domain() -> dict[str, Any]:
    mcp_config = storage.get_mcp_config() or {"mcpServers": {}}
    return {
        "domain": "mcp",
        "title": "扩展生态",
        "summary": "管理 MCP 服务配置和可用工具。",
        "data": {
            "config": mcp_config,
        },
        "source": _config_source("mcp"),
        "savePath": _config_save_path("mcp"),
        "reloadRequired": True,
        "warnings": [],
        "advancedFields": ["config"],
    }


def _save_mcp_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    config = dict(data.get("config") or data or {})
    storage.save_mcp_config(config)
    return _build_mcp_domain()


def _build_projects_domain() -> dict[str, Any]:
    return {
        "domain": "projects",
        "title": "项目与工作区",
        "summary": "管理默认工作区、项目工作区与绑定关系。",
        "data": {
            "defaultProjectId": storage.get_projects_registry().get("defaultProjectId"),
            "projects": [item.model_dump(by_alias=True, exclude_none=True) for item in _get_project_registry_service().list_projects()],
        },
        "source": _config_source("projects"),
        "savePath": _config_save_path("projects"),
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["projects"],
    }


def _save_projects_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    registry = {
        "version": 1,
        "defaultProjectId": data.get("defaultProjectId"),
        "projects": data.get("projects", []),
    }
    storage.save_projects_registry(registry)
    return _build_projects_domain()


def _build_desktop_pet_domain() -> dict[str, Any]:
    return {
        "domain": "desktop-pet",
        "title": "桌宠设置",
        "summary": "配置桌宠的事件播报、动作映射和光效，不承载聊天或连接设置。",
        "data": storage.get_desktop_pet_config(),
        "source": _config_source("desktopPet"),
        "savePath": _config_save_path("desktopPet"),
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["actionTable", "effectSpectrum"],
    }


def _save_desktop_pet_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    storage.save_desktop_pet_config(data)
    return _build_desktop_pet_domain()


def _build_music_domain() -> dict[str, Any]:
    return {
        "domain": "music",
        "title": "音乐",
        "summary": "管理背景音乐曲目列表和启用状态。",
        "data": storage.get_music_config(),
        "source": _config_source("music"),
        "savePath": _config_save_path("music"),
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["tracks"],
    }


def _save_music_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    storage.save_music_config(data)
    return _build_music_domain()


def _build_ui_domain() -> dict[str, Any]:
    return {
        "domain": "ui",
        "title": "界面偏好",
        "summary": "管理本机 Web、Admin 与桌面 Shell 共用的界面主题。",
        "data": storage.get_ui_config(),
        "source": _config_source("ui"),
        "savePath": _config_save_path("ui"),
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": [],
    }


def _save_ui_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    storage.save_ui_config(data)
    return _build_ui_domain()


def _build_system_base_domain() -> dict[str, Any]:
    system_base = storage.get_system_base_config()
    desktop_readiness = detect_desktop_tools_readiness()
    dependency_status = build_dependency_status()
    bridge = dict(system_base.get("bridge") or {})
    remote_link = normalize_remote_link_config(
        dict(system_base.get("remoteLink") or {}),
        admin_base_url=bridge.get("adminBaseUrl") or "",
        engine_base_url=bridge.get("engineBaseUrl") or "",
    )
    return {
        "domain": "system-base",
        "title": "系统基础配置",
        "summary": "设置服务联通、内部密钥、抓取缓存、桌面依赖和对象存储。",
        "data": {
            "bridge": bridge,
            "webFetch": dict(system_base.get("webFetch") or {}),
            "desktopTools": dict(system_base.get("desktopTools") or {}),
            "desktopLive": dict(system_base.get("desktopLive") or {}),
            "remoteLink": remote_link,
            "remoteLinkManifest": build_link_manifest(),
            "remoteLinkMeshStatus": build_mesh_provider_status(
                admin_base_url=bridge.get("adminBaseUrl") or "",
                engine_base_url=bridge.get("engineBaseUrl") or "",
            ),
            "s3": dict(system_base.get("s3") or {}),
            "desktopReadiness": {
                "status": desktop_readiness.get("status"),
                "ocrReady": desktop_readiness.get("ocrReady"),
                "imageLocatorReady": desktop_readiness.get("imageLocatorReady"),
                "pointLocatorReady": desktop_readiness.get("pointLocatorReady"),
                "missingItems": list(desktop_readiness.get("missingItems") or []),
            },
            "detectedDesktopTools": dict(desktop_readiness.get("detectedDesktopTools") or {}),
            "dependencyStatus": dependency_status,
            "runtimeInfo": {
                "engineHost": str(os.getenv("ENGINE_HOST") or "0.0.0.0"),
                "enginePort": int(str(os.getenv("ENGINE_PORT") or "9530")),
                "engineReload": str(os.getenv("ENGINE_RELOAD") or "").strip().lower() in {"1", "true", "yes", "on"},
            },
        },
        "source": _config_source("systemBase"),
        "savePath": _config_save_path("systemBase"),
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["webFetch", "desktopTools", "desktopLive", "remoteLink", "detectedDesktopTools", "dependencyStatus", "runtimeInfo"],
    }


def _save_system_base_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    next_payload = {
        "bridge": dict(data.get("bridge") or {}),
        "webFetch": dict(data.get("webFetch") or {}),
        "desktopTools": dict(data.get("desktopTools") or {}),
        "desktopLive": dict(data.get("desktopLive") or {}),
        "remoteLink": dict(data.get("remoteLink") or {}),
        "s3": dict(data.get("s3") or {}),
    }
    storage.save_system_base_config(next_payload)
    return _build_system_base_domain()


def _build_network_supervisor_runtime_domain() -> dict[str, Any]:
    config = storage.get_network_supervisor_runtime_config()
    status = _get_network_supervisor_service().status_payload()
    return {
        "domain": "network-supervisor-runtime",
        "title": "NETWORK SUPERVISOR RUNTIME",
        "summary": "管理多 Supervisor 组网、发现、信任、定向唤醒与显式远程委派。",
        "data": config,
        "status": status,
        "source": (
            f"{_config_source('networkSupervisorRuntime')} + "
            "~/.v8-agent-os/network_supervisor_secrets.json + "
            "~/.v8-agent-os/network_supervisor_state.json"
        ),
        "savePath": _config_save_path("networkSupervisorRuntime"),
        "reloadRequired": False,
        "warnings": [
            "私钥和 peer token 不会写入 config.json，只会保存到本地 secret 文件。",
            "OpenAI compat API Key 不写入 config.json，只保存在 network supervisor secret 文件。",
            "V8 Relay 只保存 adapter 元信息与公网入口，不保存 Cloudflare token 或云端密钥。",
            "首版远程 delegation 仍然是显式能力，不会自动把任务路由到远端节点。",
        ],
        "advancedFields": ["node", "discovery", "trust", "wake", "delegation", "relay", "openaiCompat"],
    }


def _save_network_supervisor_runtime_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    storage.save_network_supervisor_runtime_config(data)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(_get_network_supervisor_service().reload())
        if bool(data.get("enabled", False)):
            loop.create_task(_get_network_relay_worker_service().start())
        else:
            loop.create_task(_get_network_relay_worker_service().stop())
    return _build_network_supervisor_runtime_domain()


DOMAIN_REGISTRY: dict[str, tuple[ConfigBuilder, ConfigSaver]] = {
    "models": (_build_models_domain, _save_models_domain),
    "supervisor": (_build_supervisor_domain, _save_supervisor_domain),
    "agents": (_build_agents_domain, _save_agents_domain),
    "memory": (_build_memory_domain, _save_memory_domain),
    "extensions": (_build_extensions_domain, _save_extensions_domain),
    "engineering-lane": (_build_engineering_lane_domain, _save_engineering_lane_domain),
    "context": (_build_context_domain, _save_context_domain),
    "audio": (_build_audio_domain, _save_audio_domain),
    "hooks": (_build_hooks_domain, _save_hooks_domain),
    "cron": (_build_cron_domain, _save_cron_domain),
    "automation-runtime": (_build_automation_runtime_domain, _save_automation_runtime_domain),
    "workspace": (_build_workspace_domain, _save_workspace_domain),
    "runtime-stability": (_build_runtime_stability_domain, _save_runtime_stability_domain),
    "safety": (_build_safety_domain, _save_safety_domain),
    "computer-use": (_build_computer_use_domain, _save_computer_use_domain),
    "rpa": (_build_rpa_domain, _save_rpa_domain),
    "mcp": (_build_mcp_domain, _save_mcp_domain),
    "projects": (_build_projects_domain, _save_projects_domain),
    "desktop-pet": (_build_desktop_pet_domain, _save_desktop_pet_domain),
    "music": (_build_music_domain, _save_music_domain),
    "ui": (_build_ui_domain, _save_ui_domain),
    "network-supervisor-runtime": (_build_network_supervisor_runtime_domain, _save_network_supervisor_runtime_domain),
    "system-base": (_build_system_base_domain, _save_system_base_domain),
    "system-misc": (_build_system_base_domain, _save_system_base_domain),
}


def _resolve_domain(domain: str) -> tuple[ConfigBuilder, ConfigSaver]:
    normalized = str(domain or "").strip().lower()
    if normalized == "system-misc":
        normalized = "system-base"
    handler = DOMAIN_REGISTRY.get(normalized)
    if handler is None:
        raise HTTPException(status_code=404, detail=f"Unknown config registry domain: {domain}")
    return handler


@router.get("/config-registry")
async def list_config_registry_domains():
    try:
        seen: set[str] = set()
        domains: list[dict[str, Any]] = []
        for builder, _ in DOMAIN_REGISTRY.values():
            payload = builder()
            domain = str(payload.get("domain") or "")
            if domain in seen:
                continue
            seen.add(domain)
            domains.append(payload)
        return {
            "domains": domains,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/config-registry/{domain}")
async def get_config_registry_domain(domain: str):
    try:
        builder, _ = _resolve_domain(domain)
        return builder()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/config-registry/{domain}")
async def save_config_registry_domain(domain: str, payload: dict = Body(default_factory=dict)):
    try:
        _, saver = _resolve_domain(domain)
        return saver(payload or {})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
