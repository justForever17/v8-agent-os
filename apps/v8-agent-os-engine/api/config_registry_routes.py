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
from core.models.control_plane import model_control_plane
from core.dependency_registry import build_dependency_status
from core.supervisor_tool_policy import build_supervisor_tool_policy_snapshot
from core.system_base import detect_desktop_tools_readiness
from core.storage import storage
from core.v8_agent_os_identity import render_system_identity_block
from core.v8_agent_os_paths import COMPUTER_USE_JSON_PATH, CONFIG_JSON_PATH, V8_AGENT_OS_HOME
from core.workspace_guard import build_workspace_path_status
from erc.runtime_stability import runtime_stability_service
from erc.safety_guardian import safety_guardian


router = APIRouter()


ConfigBuilder = Callable[[], dict[str, Any]]
ConfigSaver = Callable[[dict[str, Any]], dict[str, Any]]


def _get_plugin_host_service():
    return importlib.import_module("core.plugin_host").plugin_host_service


def _get_network_supervisor_service():
    return importlib.import_module("runtimes.network_supervisor.service").network_supervisor_service


def _get_project_registry_service():
    return importlib.import_module("runtimes.memory.project_registry").project_registry_service


def _config_source(domain_path: str) -> str:
    return f"config.json#{domain_path}"


def _config_save_path(domain_path: str) -> list[str]:
    return [f"{CONFIG_JSON_PATH}#{domain_path}"]


def _roles_snapshot(*keys: str) -> dict[str, str]:
    config = model_control_plane.get_config()
    roles = dict(config.get("roles") or {})
    return {key: str(roles.get(key) or "").strip() for key in keys}


def _update_role_bindings(updates: dict[str, Any]) -> dict[str, str]:
    config = model_control_plane.get_config()
    roles = dict(config.get("roles") or {})
    for key, value in dict(updates or {}).items():
        roles[str(key)] = str(value or "").strip()
    config["roles"] = roles
    model_control_plane.save_config(config)
    return {key: str(roles.get(key) or "").strip() for key in updates.keys()}


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
    config = model_control_plane.save_config(data)
    return _build_models_domain() | {
        "data": model_control_plane.build_payload(config),
    }


def _build_supervisor_domain() -> dict[str, Any]:
    supervisor_config = storage.get_supervisor_config() or {}
    supervisor_profile = storage.get_supervisor_profile()
    tool_policy = build_supervisor_tool_policy_snapshot(supervisor_config.get("allowed_tools"))
    return {
        "domain": "supervisor",
        "title": "主理人",
        "summary": "设置主理人的系统指令、角色绑定和公开资料。",
        "data": {
            "systemPrompt": storage.get_supervisor_prompt(),
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
        storage.write_text("V8_AGENT_OS.md", str(data.get("systemPrompt") or ""))
    if "identity" in data and isinstance(data.get("identity"), dict):
        storage.save_system_base_config({"identity": dict(data.get("identity") or {})})

    supervisor_config = dict(storage.get_supervisor_config() or {})
    if "allowedTools" in data:
        allowed_tools = data.get("allowedTools")
        supervisor_config["allowed_tools"] = allowed_tools if allowed_tools is not None else None
        storage.save_supervisor_config(supervisor_config)

    if "profile" in data and isinstance(data.get("profile"), dict):
        storage.save_supervisor_profile(dict(data.get("profile") or {}))

    bindings = dict(data.get("bindings") or {})
    role_updates: dict[str, Any] = {}
    if "supervisorModel" in bindings:
        role_updates["supervisor"] = str(bindings.get("supervisorModel") or "").strip()
    if "defaultReplyModel" in bindings:
        role_updates["default"] = str(bindings.get("defaultReplyModel") or "").strip()
    if role_updates:
        _update_role_bindings(role_updates)
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
    bindings = _roles_snapshot("extensions_reranker", "reranker")
    return {
        "domain": "extensions",
        "title": "扩展生态",
        "summary": "控制 Skills 与 MCP 候选是否进入二阶段重排，并绑定扩展专用重排模型。",
        "data": {
            "rerankPolicy": {
                "enabled": bool(((config.get("rerankPolicy") or {}).get("enabled"))),
            },
            "modelBindings": {
                "rerankerModel": bindings["extensions_reranker"],
                "fallbackRerankerModel": bindings["reranker"],
            },
        },
        "source": f"{_config_source('extensions')} + {_config_source('models')}",
        "savePath": [f"{CONFIG_JSON_PATH}#extensions", f"{CONFIG_JSON_PATH}#models"],
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["rerankPolicy", "modelBindings"],
    }


def _save_extensions_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    rerank_policy = dict(data.get("rerankPolicy") or {})
    model_bindings = dict(data.get("modelBindings") or {})
    storage.save_extensions_config({"rerankPolicy": {"enabled": bool(rerank_policy.get("enabled", False))}})
    _update_role_bindings({"extensions_reranker": model_bindings.get("rerankerModel")})
    return _build_extensions_domain()


def _build_context_domain() -> dict[str, Any]:
    return {
        "domain": "context",
        "title": "上下文管理",
        "summary": "调整会话长度、摘要策略和上下文压缩行为。",
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


def _build_plugin_host_domain() -> dict[str, Any]:
    config = storage.get_plugin_host_config()
    snapshot = _get_plugin_host_service().build_snapshot()
    warnings: list[str] = []
    if not config.get("enabled", True):
        warnings.append("PluginHostRuntime 当前已关闭；插件仍可扫描与安装，但不会接管渠道入站与出站。")
    if "channel" not in list(config.get("allowedFamilies") or []):
        warnings.append("当前未允许 channel 家族，渠道插件只会被发现，不会接管消息主链。")
    if str(config.get("hostMode") or "managed_local") == "external":
        warnings.append("当前使用外部 OpenClaw host；Engine 不会托管本地 sidecar/gateway。")
    return {
        "domain": "plugin-host",
        "title": "插件宿主",
        "summary": "控制 PluginHostRuntime 的启停、宿主模式、家族接管范围与启动扫描策略；插件注册表仍以 ~/.v8-agent-os/plugin.json 为准。",
        "data": {
            "config": config,
            "snapshot": snapshot,
        },
        "source": _config_source("pluginHost"),
        "savePath": _config_save_path("pluginHost"),
        "reloadRequired": False,
        "warnings": warnings,
        "advancedFields": ["snapshot"],
    }


def _save_plugin_host_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    config = data.get("config") if isinstance(data.get("config"), dict) else data
    previous = storage.get_plugin_host_config()
    storage.save_plugin_host_config(config)
    current = storage.get_plugin_host_config()
    if previous != current:
        asyncio.run(_get_plugin_host_service().stop())
        if bool(current.get("enabled", True)):
            asyncio.run(_get_plugin_host_service().start())
    return _build_plugin_host_domain()


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
        "advancedFields": ["stt.providers", "tts.custom", "tts.edge_tts"],
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
        from core.automation.cron import cron_manager

        cron_manager.sync_jobs_to_scheduler()
    except Exception:
        pass
    return _build_cron_domain()


def _build_automation_runtime_domain() -> dict[str, Any]:
    config = storage.get_automation_runtime_config()
    heartbeat = dict(config.get("supervisorHeartbeat") or {})
    return {
        "domain": "automation-runtime",
        "title": "AUTOMATION RUNTIME",
        "summary": "控制自动化保留系统任务，例如 Supervisor 心跳唤醒。",
        "data": config,
        "source": _config_source("automationRuntime"),
        "savePath": _config_save_path("automationRuntime"),
        "reloadRequired": True,
        "warnings": [
            "Supervisor heartbeat 属于系统保留自动化任务，不会作为普通用户 Cron Job 单独暴露。",
        ] if heartbeat.get("enabled") else [],
        "advancedFields": ["supervisorHeartbeat"],
    }


def _save_automation_runtime_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    storage.save_automation_runtime_config(data)
    try:
        from core.automation.cron import cron_manager

        cron_manager.sync_jobs_to_scheduler()
    except Exception:
        pass
    return _build_automation_runtime_domain()


def _build_workspace_domain() -> dict[str, Any]:
    workspace_config = storage.get_workspace_config() or {}
    workspace_path = str(workspace_config.get("agent_workspace_path") or "").strip()
    path_status = build_workspace_path_status(workspace_path)
    return {
        "domain": "workspace",
        "title": "工作区",
        "summary": "管理工作区路径和项目默认工作目录。",
        "data": {
            **workspace_config,
            "pathStatus": path_status,
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
    blocked_count = 0
    verdict_counts: dict[str, int] = {}
    try:
        for item in audit_logger.get_logs(limit=50, source_type="SAFETY"):
            if str(item.get("action") or "").strip() != "skill_scan":
                continue
            details_raw = str(item.get("details") or "").strip()
            details = json.loads(details_raw) if details_raw else {}
            if not isinstance(details, dict):
                details = {}
            verdict = str(details.get("verdict") or "unknown").strip() or "unknown"
            verdict_counts[verdict] = int(verdict_counts.get(verdict) or 0) + 1
            if verdict in {"high", "critical"}:
                blocked_count += 1
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
        blocked_count = 0
        verdict_counts = {}

    return {
        "domain": "safety",
        "title": "安全控制",
        "summary": "定义命令、文件、网络和运行时的安全护栏。",
        "data": {
            **safety_guardian.export_config(),
            "modelBindings": {
                "safetyReviewModel": safety_review_model,
            },
            "runtimeSummary": {
                "mode": "rules_audit_plus_llm_review" if safety_review_model else "rules_audit_first",
                "llmBound": bool(safety_review_model),
                "skillStaticScanEnabled": True,
                "blockedSkillScans": blocked_count,
                "verdictDistribution": verdict_counts,
                "recentSkillScans": recent_skill_scans,
            },
        },
        "source": f"{_config_source('safety')} + {_config_source('models')}",
        "savePath": [f"{CONFIG_JSON_PATH}#safety", f"{CONFIG_JSON_PATH}#models"],
        "reloadRequired": False,
        "warnings": [],
        "advancedFields": ["commandRules", "runtimeRules", "channelGroupGuard", "postActionRules", "modelBindings"],
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
        "advancedFields": ["memoryProfiles", "environmentPolicy"],
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
        }
    )
    if isinstance(data.get("memoryProfiles"), dict):
        storage.save_computer_use_memory(data.get("memoryProfiles") or {})
    return _build_computer_use_domain()


def _build_rpa_domain() -> dict[str, Any]:
    return {
        "domain": "rpa",
        "title": "RPA Runtime",
        "summary": "控制 RPA 发现模型和执行保护策略。",
        "data": {
            "modelBindings": {
                "discoveryModel": model_control_plane.get_role_model_id("rpa_discovery") or "",
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
        "summary": "管理项目注册表、默认项目和工作区绑定关系。",
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


def _build_system_base_domain() -> dict[str, Any]:
    system_base = storage.get_system_base_config()
    desktop_readiness = detect_desktop_tools_readiness()
    dependency_status = build_dependency_status()
    return {
        "domain": "system-base",
        "title": "系统基础配置",
        "summary": "设置服务联通、内部密钥、抓取缓存、桌面依赖和对象存储。",
        "data": {
            "bridge": dict(system_base.get("bridge") or {}),
            "webFetch": dict(system_base.get("webFetch") or {}),
            "desktopTools": dict(system_base.get("desktopTools") or {}),
            "desktopLive": dict(system_base.get("desktopLive") or {}),
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
        "advancedFields": ["webFetch", "desktopTools", "desktopLive", "detectedDesktopTools", "dependencyStatus", "runtimeInfo"],
    }


def _save_system_base_domain(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or payload or {})
    next_payload = {
        "bridge": dict(data.get("bridge") or {}),
        "webFetch": dict(data.get("webFetch") or {}),
        "desktopTools": dict(data.get("desktopTools") or {}),
        "desktopLive": dict(data.get("desktopLive") or {}),
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
            "首版远程 delegation 仍然是显式能力，不会自动把任务路由到远端节点。",
        ],
        "advancedFields": ["node", "discovery", "trust", "wake", "delegation"],
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
    return _build_network_supervisor_runtime_domain()


DOMAIN_REGISTRY: dict[str, tuple[ConfigBuilder, ConfigSaver]] = {
    "models": (_build_models_domain, _save_models_domain),
    "supervisor": (_build_supervisor_domain, _save_supervisor_domain),
    "agents": (_build_agents_domain, _save_agents_domain),
    "memory": (_build_memory_domain, _save_memory_domain),
    "extensions": (_build_extensions_domain, _save_extensions_domain),
    "context": (_build_context_domain, _save_context_domain),
    "plugin-host": (_build_plugin_host_domain, _save_plugin_host_domain),
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
    "music": (_build_music_domain, _save_music_domain),
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
