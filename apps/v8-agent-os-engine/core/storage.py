import json
import os
import shutil
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from uuid import uuid4

from core.context_policy import DEFAULT_CONTEXT_POLICY, normalize_context_policy
from core.delegation_broker import default_external_worker_descriptors, normalize_external_worker_descriptors
from core.agents import DEFAULT_SPECIALIST_FAMILIES, ensure_specialist_family, normalize_specialist_families_config
from core.runtime.supervisor_tool_policy import sanitize_supervisor_allowed_tools
from core.source_provider_registry import get_source_provider_config_defaults, get_source_router_defaults
from core.v8_agent_os_identity import default_system_identity, normalize_system_identity
from core.v8_agent_os_paths import (
    COMPUTER_USE_JSON_PATH,
    CONFIG_JSON_PATH,
    LEGACY_CONFIG_BACKUP_ROOT,
    MCP_JSON_PATH,
    RUNTIME_DATA_HOME,
    WORKSPACE_HOME,
    protected_runtime_paths,
    runtime_private_root,
)
from core.memory_maintenance_contract import normalize_cron_config_with_system_job


def _default_workspace_path() -> str:
    return str(WORKSPACE_HOME)


def _normalize_http_base_url(value: Any, fallback: str) -> str:
    normalized = str(value or "").strip() or fallback
    return normalized.rstrip("/")


def _derive_ws_base_url(http_base_url: str) -> str:
    normalized = _normalize_http_base_url(http_base_url, "http://127.0.0.1:9530/v1")
    if normalized.startswith("https://"):
        return normalized.replace("https://", "wss://", 1)
    if normalized.startswith("http://"):
        return normalized.replace("http://", "ws://", 1)
    if normalized.startswith("ws://") or normalized.startswith("wss://"):
        return normalized
    return f"ws://{normalized.lstrip('/')}"


def _normalize_admin_public_base_url(value: Any) -> str:
    normalized = _normalize_http_base_url(value, "http://127.0.0.1:9528/api")
    if normalized.endswith("/api"):
        return normalized[:-4]
    return normalized


def _default_supervisor_avatar_url(admin_base_url: Any) -> str:
    return f"{_normalize_admin_public_base_url(admin_base_url)}/brand-mark.png"


def _normalize_allowed_origins(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        candidate = str(item or "").strip().rstrip("/")
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


LEGACY_LOCAL_ENGINE_BASES = {
    "http://127.0.0.1:8000/v1": "http://127.0.0.1:9530/v1",
    "http://localhost:8000/v1": "http://127.0.0.1:9530/v1",
}

LEGACY_LOCAL_ENGINE_WS_BASES = {
    "ws://127.0.0.1:8000/v1": "ws://127.0.0.1:9530/v1",
    "ws://localhost:8000/v1": "ws://127.0.0.1:9530/v1",
}

LEGACY_LOCAL_ADMIN_BASES = {
    "http://127.0.0.1:5001/api": "http://127.0.0.1:9528/api",
    "http://localhost:5001/api": "http://127.0.0.1:9528/api",
}

LEGACY_NETWORK_BASES = {
    "http://127.0.0.1:8000": "http://127.0.0.1:9530",
    "http://localhost:8000": "http://127.0.0.1:9530",
}

LEGACY_NETWORK_WS_BASES = {
    "ws://127.0.0.1:8000": "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws",
    "ws://localhost:8000": "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws",
    "ws://127.0.0.1:8000/v1/network-supervisor/peer/ws": "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws",
    "ws://localhost:8000/v1/network-supervisor/peer/ws": "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws",
    "ws://127.0.0.1:9530": "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws",
    "ws://localhost:9530": "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws",
}

LEGACY_ADMIN_ORIGINS = {
    "http://127.0.0.1:5001",
    "http://localhost:5001",
}

MEMORY_RETRIEVAL_THRESHOLD_RECOMMENDED = 0.20

MEMORY_DURABLE_POLICY_PRESETS: dict[str, dict[str, Any]] = {
    "learning_first": {
        "label": "学习优先",
        "preference_importance_threshold": 25,
        "preference_confidence_threshold": 0.35,
        "knowledge_importance_threshold": 28,
        "knowledge_confidence_threshold": 0.35,
        "global_knowledge_importance_threshold": 42,
        "global_knowledge_confidence_threshold": 0.50,
        "global_operational_importance_threshold": 38,
        "global_operational_confidence_threshold": 0.45,
    },
    "balanced": {
        "label": "平衡",
        "preference_importance_threshold": 35,
        "preference_confidence_threshold": 0.45,
        "knowledge_importance_threshold": 35,
        "knowledge_confidence_threshold": 0.45,
        "global_knowledge_importance_threshold": 50,
        "global_knowledge_confidence_threshold": 0.60,
        "global_operational_importance_threshold": 45,
        "global_operational_confidence_threshold": 0.55,
    },
    "quality_first": {
        "label": "质量优先",
        "preference_importance_threshold": 48,
        "preference_confidence_threshold": 0.58,
        "knowledge_importance_threshold": 46,
        "knowledge_confidence_threshold": 0.54,
        "global_knowledge_importance_threshold": 60,
        "global_knowledge_confidence_threshold": 0.68,
        "global_operational_importance_threshold": 55,
        "global_operational_confidence_threshold": 0.62,
    },
}

MEMORY_DURABLE_POLICY_DEFAULTS: dict[str, Any] = {
    key: value
    for key, value in MEMORY_DURABLE_POLICY_PRESETS["balanced"].items()
    if key != "label"
}

LEGACY_LOW_MEMORY_DURABLE_POLICY: dict[str, Any] = {
    "preference_importance_threshold": 18,
    "preference_confidence_threshold": 0.18,
    "knowledge_importance_threshold": 20,
    "knowledge_confidence_threshold": 0.20,
    "global_knowledge_importance_threshold": 20,
    "global_knowledge_confidence_threshold": 0.20,
    "global_operational_importance_threshold": 20,
    "global_operational_confidence_threshold": 0.20,
}


def _replace_if_exact(value: Any, replacements: Dict[str, str]) -> str:
    normalized = str(value or "").strip().rstrip("/")
    return replacements.get(normalized, normalized)


def _maybe_migrate_legacy_local_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    next_payload = deepcopy(dict(payload or {}))
    bridge = (
        next_payload.setdefault("systemBase", {}).setdefault("bridge", {})
        if isinstance(next_payload.get("systemBase"), dict)
        else None
    )
    if bridge is None:
        next_payload["systemBase"] = {"bridge": {}}
        bridge = next_payload["systemBase"]["bridge"]

    changed = False

    current_engine_base = _replace_if_exact(bridge.get("engineBaseUrl"), LEGACY_LOCAL_ENGINE_BASES)
    current_engine_ws_base = _replace_if_exact(bridge.get("engineWsBaseUrl"), LEGACY_LOCAL_ENGINE_WS_BASES)
    current_admin_base = _replace_if_exact(bridge.get("adminBaseUrl"), LEGACY_LOCAL_ADMIN_BASES)

    if current_engine_base and current_engine_base != str(bridge.get("engineBaseUrl") or "").strip().rstrip("/"):
        bridge["engineBaseUrl"] = current_engine_base
        changed = True
    if current_engine_ws_base and current_engine_ws_base != str(bridge.get("engineWsBaseUrl") or "").strip().rstrip("/"):
        bridge["engineWsBaseUrl"] = current_engine_ws_base
        changed = True
    if current_admin_base and current_admin_base != str(bridge.get("adminBaseUrl") or "").strip().rstrip("/"):
        bridge["adminBaseUrl"] = current_admin_base
        changed = True

    network_node = (
        next_payload.get("networkSupervisorRuntime", {}).get("node", {})
        if isinstance(next_payload.get("networkSupervisorRuntime"), dict)
        else {}
    )
    if isinstance(network_node, dict):
        advertised_base = _replace_if_exact(network_node.get("advertisedBaseUrl"), LEGACY_NETWORK_BASES)
        advertised_ws = _replace_if_exact(network_node.get("advertisedWsUrl"), LEGACY_NETWORK_WS_BASES)
        if advertised_base and advertised_base != str(network_node.get("advertisedBaseUrl") or "").strip().rstrip("/"):
            network_node["advertisedBaseUrl"] = advertised_base
            changed = True
        if advertised_ws and advertised_ws != str(network_node.get("advertisedWsUrl") or "").strip().rstrip("/"):
            network_node["advertisedWsUrl"] = advertised_ws
            changed = True

    supervisor = next_payload.get("supervisor")
    profile = supervisor.get("profile") if isinstance(supervisor, dict) else None
    avatar = str(profile.get("avatar") or "").strip() if isinstance(profile, dict) else ""
    if avatar:
        for legacy_origin in LEGACY_ADMIN_ORIGINS:
            if avatar.startswith(f"{legacy_origin}/Avatar/"):
                profile["avatar"] = avatar.replace(legacy_origin, "http://127.0.0.1:9528", 1)
                changed = True
                break

    if changed:
        return next_payload
    return payload


_STOCK_SUPERVISOR_PROMPT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "- Keep long tasks resumable, inspectable, and approval-safe.\n",
        "- Keep long tasks resumable, inspectable, and stable.\n",
    ),
    (
        "- Prefer paths that preserve pause/resume, retry, approval, snapshots, run ledgers, and event trails.\n",
        "- Prefer paths that preserve pause/resume, retry, snapshots, run ledgers, and event trails.\n",
    ),
    (
        "## Runtime Worldview\n"
        "Think in terms of runtime boundaries and coordination:\n"
        "- CHAT RUNTIME: conversation, decomposition, orchestration, delegation.\n"
        "- MEMORY RUNTIME: long-term knowledge, preferences, recall, graph, artifacts.\n"
        "- AUTOMATION RUNTIME: hooks, cron, recurring jobs, durable automation.\n"
        "- WORKFLOW RUNTIME: multi-step structured execution and stateful task flows.\n"
        "- PLUGIN MANAGER RUNTIME: curated CLI, Skill, MCP, UI adapter installation and governed task grants.\n"
        "- COMPUTER USE RUNTIME: desktop/UI execution with guarded escalation.\n"
        "- RPA RUNTIME: deterministic scripted operational flows.\n\n",
        "## Runtime Worldview\n"
        "Think in runtime routes, not in giant capability catalogs.\n"
        "- Prefer the active runtime card and current route over memorizing every subsystem.\n"
        "- Treat Memory, Automation, Plugin Host, Computer Use, and RPA as managed execution planes that can be consulted or delegated when needed.\n"
        "- Only expand deeper runtime detail when the current task truly depends on it.\n\n",
    ),
    (
        "## Tool Discipline\n"
        "Tool priority order:\n"
        "1. Use the most appropriate runtime-managed path.\n"
        "2. Use route-selected skills / MCP and only explicitly granted plugin capabilities.\n"
        "3. Use baseline system tools for reading, writing, searching, commands, media inspection, and web access.\n"
        "4. Use low-level or destructive tools only when clearly necessary and safe.\n\n"
        "Do not assume that a route miss means a capability is forbidden. If the task is blocked or stale, expand carefully and switch capabilities deliberately.\n\n",
        "## Tool Discipline\n"
        "- Choose direct Supervisor execution, a runtime-managed path, or a named subagent by delivery quality, specialist context, parallelism, recovery, and proof needs.\n"
        "- Use route-selected skills / MCP and explicit plugin grants instead of exploring every tool family at once.\n"
        "- Baseline system tools are a normal direct execution surface; task size alone never removes them from the Supervisor.\n"
        "- Escalate to low-level or destructive tools only when clearly necessary and safe.\n\n"
        "Do not treat a route miss as a ban. Expand deliberately only when the task is blocked or stale.\n\n",
    ),
    (
        # Migration sanitizer only: this matches the brokered stock block
        # before the subagent runtime-authority boundary was added.
        "## Delegation Discipline\n"
        "- If a task is small and local, solve it directly.\n"
        "- If a task needs a distinct role, independent context, or parallel execution, use `delegation_broker`.\n"
        "- Treat Supervisor-authored task briefs as the canonical delegation contract.\n"
        "- Keep local subagents and external workers on the same brokered path instead of mixing old delegation tools.\n"
        "- Subagents should inherit relevant skills, MCP, explicit plugin grants, and baseline tool context instead of starting blind.\n\n",
        "## Delegation Discipline\n"
        "- Solve work directly whenever that is the clearest path, including long multi-file projects in session Engineering work mode.\n"
        "- Use `delegation_broker` or an Engineering episode when a distinct role, independent context, parallel execution, recovery, or durable proof materially helps.\n"
        "- Treat Supervisor-authored task briefs as the canonical delegation contract.\n"
        "- Keep local subagents and external workers on the same brokered path instead of mixing old delegation tools.\n"
        "- Subagents should inherit relevant skills, MCP, explicit plugin grants, and baseline tool context instead of starting blind.\n"
        "- Subagents do not have ComputerUse, RPA, or Memory runtime authority by default; keep those managed runtime actions, route gates, and final verification in the supervisor unless a brokered task explicitly grants a narrow surface.\n\n",
    ),
    (
        # Migration sanitizer only: this left-hand block matches older stock
        # V8_AGENT_OS.md files so they can be rewritten to the brokered
        # delegation contract below. It is not current prompt truth.
        "## Delegation Discipline\n"
        "- If a task is small and local, solve it directly.\n"
        "- If a task needs a distinct role, independent context, or parallel execution, delegate.\n"
        "- Use `create_agent` to create durable specialists for future turns.\n"
        "- Use `delegate_parallel` only for bounded fan-out, at most two subtasks, with isolated scopes.\n"
        "- Subagents should inherit relevant skills, MCP, explicit plugin grants, and baseline tool context instead of starting blind.\n\n",
        "## Delegation Discipline\n"
        "- If a task is small, local, and realistically finishes within 1-10 tool steps, solve it directly.\n"
        "- If a task needs a distinct role, independent context, parallel execution, broad multi-file implementation, or source-backed research plus coding, use `delegation_broker` / Engineering discipline first.\n"
        "- Treat Supervisor-authored task briefs as the canonical delegation contract.\n"
        "- Keep local subagents and external workers on the same brokered path instead of mixing old delegation tools.\n"
        "- Subagents should inherit relevant skills, MCP, explicit plugin grants, and baseline tool context instead of starting blind.\n"
        "- Subagents do not have ComputerUse, RPA, or Memory runtime authority by default; keep those managed runtime actions, route gates, and final verification in the supervisor unless a brokered task explicitly grants a narrow surface.\n\n",
    ),
    (
        "## Recoverability And Observability\n"
        "- Prefer paths that preserve pause/resume, retry, snapshots, run ledgers, and event trails.\n"
        "- Do not fake completion. If something is blocked, state what is blocked, what is done, and what should happen next.\n"
        "- When interacting with external channels or plugins, care about the real runtime state, not just the last message projection.\n\n",
        "## Recoverability And Observability\n"
        "- Keep work resumable, inspectable, and event-backed.\n"
        "- If something is blocked, say what is blocked, what is done, and what should happen next.\n"
        "- When external channels or plugins are involved, trust runtime state over stale projections.\n\n",
    ),
)


_PRODUCT_LANGUAGE_PROMPT_BLOCK = (
    "## Product Language\n"
    "- Use product words with users: 主理人中枢, 编程模式, 深度调研, 多媒体创作, 桌面操作, 自动流程, 记忆系统, 定时与触发, 插件管理中心, 网络连接, 安全系统, 子代理, 规格文档.\n"
    "- Canonical ids and tool names such as `runtime_broker`, `delegation_broker`, `spec_broker`, runtime ids, provider ids, and raw refs are for tool calls, diagnostics, logs, code, paths, and detail references.\n"
    "- Do not use internal tool names as ordinary user-facing nouns. If the user asks how V8OS works, explain the product word first and mention the canonical id only as a diagnostic identifier.\n\n"
)


def _sanitize_stock_supervisor_prompt_text(content: str) -> str:
    normalized = str(content or "")
    legacy_runtime_id = "plugin" + "_" + "host"
    legacy_product_label = "插件" + "桥接"
    normalized = normalized.replace(legacy_product_label, "插件管理中心")
    normalized = normalized.replace(
        f"skills / MCP / {legacy_runtime_id} candidates",
        "skills / MCP and explicit plugin grant candidates",
    )
    normalized = normalized.replace(
        f"skills, MCP, {legacy_runtime_id}, and baseline tool context",
        "skills, MCP, explicit plugin grants, and baseline tool context",
    )
    for source, target in _STOCK_SUPERVISOR_PROMPT_REPLACEMENTS:
        normalized = normalized.replace(source, target)
    normalized = normalized.replace(
        "## Language Protocol\n"
        "- Think and structure plans in English by default.\n"
        "- Reply to the user in the language they used most recently.\n"
        "- Keep canonical runtime, tool, model, and page names unforced; do not translate them unless clarity truly improves.\n\n",
        "## Language Protocol\n"
        "- Infer the preferred user-visible language from the latest human request and keep Supervisor plans, runtime briefs, tool summaries, and final replies in that language.\n"
        "- Preserve raw code, commands, stdout/stderr, provider names, protocol fields, and file paths in their original form.\n"
        "- Use product words for user-facing explanations; keep canonical ids, tool names, model ids, protocol fields, and page paths unchanged only in tool calls, diagnostics, logs, or exact references.\n\n",
    )
    normalized = normalized.replace(
        "## Language Protocol\n"
        "- Think and structure plans in the latest user's preferred language for user-visible orchestration; keep raw code, commands, stdout/stderr, provider names, protocol fields, and file paths unchanged.\n"
        "- Reply to the user in the language they used most recently.\n"
        "- Keep canonical runtime, tool, model, and page names unforced; do not translate them unless clarity truly improves.\n\n",
        "## Language Protocol\n"
        "- Infer the preferred user-visible language from the latest human request and keep Supervisor plans, runtime briefs, tool summaries, and final replies in that language.\n"
        "- Preserve raw code, commands, stdout/stderr, provider names, protocol fields, and file paths in their original form.\n"
        "- Use product words for user-facing explanations; keep canonical ids, tool names, model ids, protocol fields, and page paths unchanged only in tool calls, diagnostics, logs, or exact references.\n\n",
    )
    normalized = normalized.replace(
        "- Keep canonical runtime, tool, model, and page names unforced; do not translate them unless clarity truly improves.\n\n",
        "- Use product words for user-facing explanations; keep canonical ids, tool names, model ids, protocol fields, and page paths unchanged only in tool calls, diagnostics, logs, or exact references.\n\n",
    )
    if "## Product Language\n" not in normalized and "## Runtime Worldview\n" in normalized:
        normalized = normalized.replace("## Runtime Worldview\n", f"{_PRODUCT_LANGUAGE_PROMPT_BLOCK}## Runtime Worldview\n", 1)
    if "## Multi-Runtime Orchestration" not in normalized and "## Tool Discipline\n" in normalized:
        orchestration_block = (
            "## Multi-Runtime Orchestration\n"
            "- When a request combines research and implementation, keep Supervisor as the coordinator: gather source-backed evidence first, then choose the implementation route.\n"
            "- For complex or freshness-sensitive research, grant `research.core` and first call `research_broker(mode=\"search_experience\")` for reusable experience packs; run new `research_broker(mode=\"run\")` only when packs are missing, stale, low-confidence, or conflicting.\n"
            "- 编程模式 / Engineering work mode is a session-level Supervisor posture, not a mandatory Engineering Runtime route. In that mode the Supervisor may directly execute a long project with common tools.\n"
            "- Use an Engineering episode or a named registered subagent when specialist context, parallelism, recovery, or durable proof materially improves delivery; do not route merely because the task is large.\n"
            "- Before adding a hard restriction for an Agent failure, verify the Agent received the exact registry names, task contract, workspace facts, tool availability, and peer-boundary summary. Repair missing information first.\n"
            "- New project creation is a routing choice for Supervisor: use Engineering project-creation workspace mode after workspace inventory; do not treat an empty workspace alone as sufficient, but do not block Engineering only because repoDetected=false.\n"
            "- Do not say you are dispatching or assigning a subagent unless you actually call `delegation_broker`; if you choose direct Supervisor execution, say that directly.\n"
            "- Supervisor todos are cross-runtime milestones; Engineering proof, worksets, research evidence, media recipes, and command sessions stay in their runtime ledgers/cards.\n\n"
        )
        normalized = normalized.replace("## Tool Discipline\n", f"{orchestration_block}## Tool Discipline\n", 1)
    if "Use command sessions, not sync commands" not in normalized and "## Tool Discipline\n" in normalized:
        normalized = normalized.replace(
            "- Escalate to low-level or destructive tools only when clearly necessary and safe.\n\n",
            "- Escalate to low-level or destructive tools only when clearly necessary and safe.\n"
            "- Use command sessions, not sync commands, for scaffolding, dependency installs, dev servers, CLIs that may prompt, and long-running processes.\n\n",
            1,
        )
    return normalized


STRUCTURED_CONFIG_DEFAULTS: dict[str, Any] = {
    "ui": {
        "theme": "system",
    },
    "models": {
        "version": 2,
        "providers": {},
        "roles": {
            "default": "",
            "supervisor": "",
            "subagent": "",
            "summary": "",
            "extraction": "",
            "vision": "",
            "embedding": "",
            "reranker": "",
            "extensions_prefilter": "",
            "extensions_reranker": "",
            "channel": "",
            "automation": "",
            "computer_use_planner": "",
            "computer_use_visual_judge": "",
            "computer_use_candidate_reranker": "",
            "rpa_discovery": "",
        },
        "bindings": {"agents": {}},
        "governance": {
            "enabled": True,
            "stickyRunModel": True,
            "allowSameCapabilityFailover": True,
            "strictCapabilityMatch": True,
            "maxLocalRetries": 1,
            "maxProviderSwitches": 2,
            "defaultStreaming": True,
        },
        "routingPolicies": {
            "chat": "supervisor",
            "subagent": "subagent",
            "channel": "channel",
            "automation": "automation",
            "summary": "summary",
            "memoryExtraction": "extraction",
            "visionAnalysis": "vision",
            "embedding": "embedding",
            "reranker": "reranker",
            "computerUsePlanner": "computer_use_planner",
            "computerUseVisualJudge": "computer_use_visual_judge",
            "rpaDiscovery": "rpa_discovery",
        },
    },
    "mcp": {"mcpServers": {}},
    "memory": {
        "extraction_temperature": 0.1,
        "recall_strategy": "balanced",
        "recall_top_k": 3,
        "retrieval_threshold": MEMORY_RETRIEVAL_THRESHOLD_RECOMMENDED,
        "passive_injection_enabled": True,
        "passive_context_profile": "balanced",
        "passive_summary_enabled": True,
        "passive_memory_map_enabled": True,
        "passive_recent_activity_teaser_enabled": True,
        "passive_recent_activity_teaser_limit": 2,
        "passive_memory_map_node_limit": 4,
        "max_recent_days": 1,
        "max_context_tokens": 2000,
        "extraction_enabled": True,
        "extraction_mode": "auto",
        **MEMORY_DURABLE_POLICY_DEFAULTS,
        "workflowMemory": {
            "enabled": True,
            "hintInjectionEnabled": True,
            "progressiveHintsEnabled": True,
            "minSuccessCount": 2,
            "errorfulSuccessRequiresUserAcceptance": True,
            "maxInjectedHints": 2,
            "maxHintChars": 900,
            "maxActiveWorkflowGuidesPerRun": 2,
            "quarantineOnNegativeFeedback": True,
            "requireApprovalForSideEffects": True,
            "riskTierActivationPolicy": {
                "read_only": "auto",
                "low": "auto",
                "medium": "approval",
                "high": "approval",
                "critical": "quarantine",
            },
            "engineering": {
                "enabled": True,
                "extractFromProofLedger": True,
                "requireEngineeringModeForInjection": True,
                "requireVerifiedProofForActivation": True,
                "learnFailedVerificationAsAntiPattern": True,
                "minVerifiedSuccessCount": 2,
            },
        },
        "graph_enabled": True,
        "fts_enabled": True,
    },
    "extensions": {
        "prefilterPolicy": {
            "enabled": False,
            "mode": "two_stage",
            "skills": {
                "stage1Enabled": True,
                "stage1TopK": 20,
                "llmEnabled": True,
                "stage2TopK": 5,
                "llmTimeoutSeconds": 5,
            },
            "mcp": {
                "stage1Enabled": True,
                "stage1TopK": 20,
                "llmEnabled": True,
                "stage2TopK": 2,
                "llmTimeoutSeconds": 5,
            },
        },
    },
    "engineeringLane": {
        "enabled": True,
        "triggerMode": "auto",
        "worktreePlacement": "same_volume",
        "worktreeRoot": "",
        "contextPackBudget": 48000,
        "evidenceGraphEnabled": True,
        "evidenceGraphBudget": 16000,
        "codingExecutionContractEnabled": True,
        "worksetGovernanceMode": "observe_auto_block",
        "worksetObservationEnabled": True,
        "workbenchDryRunMatrixEnabled": True,
        "maxCriticalFiles": 24,
        "proofLedgerEnabled": True,
        "autoProofCollectionEnabled": True,
        "proofCollectionScope": "engineering_active",
        "diagnosticsProviders": {
            "git": True,
            "command": True,
            "lspBestEffort": True,
        },
        "worksetRiskMode": "read_only",
        "suppressDailyMemory": True,
        "suppressMemoryMap": True,
        "rankedWorkflowPathCount": 3,
    },
    "supervisor": {
        "allowed_tools": None,
        "profile": {
            "name": "智能主管",
            "roleLabel": "主理人",
            "avatar": "",
        },
        "delegation": {
            "externalWorkers": default_external_worker_descriptors(),
        },
        "specialistRegistry": {
            "familyModeEnabled": True,
            "maxMembersPerFamily": 10,
            "exposureMode": "family_cards",
            "families": DEFAULT_SPECIALIST_FAMILIES,
        },
        "research": {
            "enabled": True,
            "defaultShardCount": 10,
            "maxShardCount": 30,
            "maxRounds": 5,
            "evidenceTtlSeconds": 21600,
            "architectAgentSynthesisEnabled": True,
            "architectAgentTimeoutSeconds": 60,
        },
    },
    "workspace": {"agent_workspace_path": _default_workspace_path()},
    "hooks": {"hooks": []},
    "cron": {"jobs": []},
    "automationRuntime": {
        "wakeIngressPolicies": {
            "allowNudgeWithoutTarget": True,
            "defaultAttachPolicy": "new_session",
            "enabledSourceRuntimes": ["cron", "hook", "network_supervisor", "chat", "computer_use"],
        },
    },
    "networkSupervisorRuntime": {
        "enabled": False,
        "node": {
            "displayName": "V8 Node",
            "peerId": "",
            "advertisedBaseUrl": "http://127.0.0.1:9530",
            "advertisedWsUrl": "ws://127.0.0.1:9530/v1/network-supervisor/peer/ws",
            "transportProfileId": "",
            "peerBaseUrl": "",
        },
        "discovery": {
            "lanEnabled": False,
            "multicastGroup": "239.8.8.8",
            "multicastPort": 19530,
            "announceIntervalSeconds": 15,
            "peerExpirySeconds": 60,
            "wanBootstrapPeers": [],
        },
        "trust": {
            "enrollmentMode": "manual",
            "allowedScopes": [],
            "trustedPeers": [],
        },
        "wake": {
            "enabled": True,
            "ackTimeoutSeconds": 10,
        },
        "delegation": {
            "enabled": True,
            "maxConcurrent": 2,
            "defaultTimeoutSeconds": 120,
        },
        "relay": {
            "enabled": False,
            "activeAdapterId": "self-hosted",
            "protocolVersion": "v8-relay.v1",
            "endToEndEnvelopeRequired": True,
            "storeAndForwardRequired": True,
            "defaultTtlSeconds": 300,
            "maxPayloadBytes": 262144,
            "adapters": [
                {
                    "id": "self-hosted",
                    "kind": "self_hosted",
                    "displayName": "Self-hosted V8 Relay",
                    "enabled": True,
                    "baseUrl": "",
                    "websocketUrl": "",
                    "rendezvousPath": "/v1/relay/rendezvous",
                    "mailboxPath": "/v1/relay/mailbox",
                    "websocketPath": "/v1/relay/ws",
                },
                {
                    "id": "cloudflare",
                    "kind": "cloudflare",
                    "displayName": "Cloudflare Workers Relay",
                    "enabled": True,
                    "baseUrl": "",
                    "websocketUrl": "",
                    "rendezvousPath": "/v1/relay/rendezvous",
                    "mailboxPath": "/v1/relay/mailbox",
                    "websocketPath": "/v1/relay/ws",
                    "cloudflareAccountHint": "",
                    "cloudflareWorkerName": "",
                    "cloudflareQueueName": "",
                    "cloudflareDurableObjectNamespace": "",
                },
            ],
        },
        "openaiCompat": {
            "enabled": False,
            "modelAliases": ["v8os"],
            "adminRelayOnly": True,
            "allowWorkspaceHeaders": False,
            "allowRawWorkspacePath": False,
            "v8MainChainModeEnabled": False,
            "maxExternalTools": 8,
            "defaultScopeMode": "explicit",
        },
    },
    "context": DEFAULT_CONTEXT_POLICY,
    "audio": {
        "stt": {
            "active_provider": "baidu",
            "providers": {
                "baidu": {"api_key": "", "secret_key": ""},
                "volcengine": {"app_id": "", "access_token": "", "cluster": ""},
                "custom": {"endpoint": "", "api_key": ""},
            },
        },
        "tts": {
            "active_provider": "edge-tts",
            "edge_tts": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "volume": "+0%"},
            "custom": {"endpoint": "", "api_key": "", "voice": ""},
        },
    },
    "pluginManager": {
        "enabled": True,
        "catalogUrl": "https://raw.githubusercontent.com/justForever17/v8-agent-os/main/apps/v8-agent-os-engine/runtimes/plugin_manager/resources/catalog.json",
        "catalogSignatureUrl": "https://raw.githubusercontent.com/justForever17/v8-agent-os/main/apps/v8-agent-os-engine/runtimes/plugin_manager/resources/catalog.sig",
        "refreshOnStartup": True,
        "refreshIntervalHours": 24,
        "defaultGrantScope": "task",
        "allowSessionGrant": True,
        "requireExplicitMention": True,
        "installRoot": "",
        "binRoot": "",
    },
    "computerUse": {
        "candidateRerankEnabled": False,
        "browserLane": {
            "enabled": True,
            "mode": "auto_if_available",
            "provider": "engine_managed_cdp",
            "proxyPort": 3456,
            "connectTimeoutMs": 3000,
            "targetFamilies": ["chromium", "electron", "webview2"],
            "allowManagedLaunch": True,
            "profileMode": "dedicated_debug_profile",
            "userDataDir": "",
        },
        "observationPolicy": {
            "frameSequenceEnabled": True,
            "frameSequenceCount": 3,
            "frameSequenceIntervalMs": 200,
        },
        "inputPolicy": {
            "normalizeDeterministicTextIme": True,
        },
    },
    "runtimeStability": {"version": 1, "strictSupervisorDurability": True, "sessionLanePolicy": "queue"},
    "storageRetention": {
        "version": 2,
        "enabled": True,
        "policy": "disk_watermark",
        "protectUserVisibleTranscript": True,
        "diskWatermarks": {
            "warningRatio": 0.15,
            "criticalRatio": 0.10,
            "emergencyRatio": 0.05,
            "emergencyFreeBytes": 2 * 1024 * 1024 * 1024,
        },
        "budgets": {
            "logs": {
                "maxBytes": 1 * 1024 * 1024 * 1024,
                "mode": "rolling",
            },
            "checkpoints": {
                "maxBytes": 4 * 1024 * 1024 * 1024,
                "mode": "elastic",
            },
            "rawEvidence": {
                "maxBytes": 2 * 1024 * 1024 * 1024,
                "retentionDays": 30,
                "mode": "rolling",
            },
            "artifacts": {
                "maxBytes": 8 * 1024 * 1024 * 1024,
                "retentionDays": 60,
                "mode": "manual_prune",
            },
            "screenshots": {
                "maxBytes": 2 * 1024 * 1024 * 1024,
                "retentionDays": 14,
                "mode": "rolling",
            },
            "vectorDb": {
                "maxBytes": 4 * 1024 * 1024 * 1024,
                "mode": "warn_only",
            },
        },
    },
    "safety": {
        "enabled": True,
        "machinePosture": "dedicated_runtime_host",
        "commandRules": [
            {
                "id": "command_block",
                "label": "系统级阻断命令",
                "verdict": "block",
                "description": "命中后直接阻断，不允许 override。",
                "patterns": [
                    "shutdown",
                    "reboot",
                    "poweroff",
                    "diskpart",
                    "mkfs",
                    "format ",
                    "rm -rf /",
                    "remove-item",
                ],
            },
            {
                "id": "command_review",
                "label": "高风险复核命令",
                "verdict": "review",
                "description": "命中后进入 pending approval。",
                "patterns": [
                    "taskkill",
                    "pkill",
                    "kill",
                    "git push",
                    "curl -x post",
                    "invoke-webrequest",
                    "pip install",
                    "npm install",
                    "pnpm add",
                    "yarn add",
                ],
            },
        ],
        "fileRules": {
            "protectedPaths": [*protected_runtime_paths(include_home=True), str(Path.home() / ".ssh")],
            "blockedPathPatterns": [".ssh", ".aws", ".kube"],
            "reviewPathPatterns": [".v8chat", "projects.json", "hooks_config.json", "cron_config.json"],
            "protectedFileExtensions": [".db", ".sqlite", ".sqlite3"],
        },
        "processRules": {
            "protectedPatterns": ["v8-agent-os", "uvicorn main:app", "next dev", "next start"],
            "reviewPatterns": ["python", "node", "uvicorn"],
        },
        "networkRules": {
            "localHosts": ["127.0.0.1", "localhost", "::1"],
            "blockedHosts": [],
            "reviewHosts": [],
            "reviewMethods": ["POST", "PUT", "PATCH", "DELETE"],
        },
        "automationRules": {
            "blockedActionTypes": [],
            "reviewActionTypes": ["command"],
            "reviewTargetPatterns": [],
            "blockedTargetPatterns": [],
        },
        "runtimeRules": {
            "chat": {"auditTriggerSources": [], "reviewTriggerSources": [], "blockedTriggerSources": [], "auditScopePatterns": [], "reviewScopePatterns": [], "blockedScopePatterns": []},
            "automation": {"auditTriggerSources": [], "reviewTriggerSources": [], "blockedTriggerSources": [], "auditScopePatterns": [], "reviewScopePatterns": [], "blockedScopePatterns": []},
            "computer_use": {"auditTriggerSources": [], "reviewTriggerSources": [], "blockedTriggerSources": [], "auditScopePatterns": [], "reviewScopePatterns": [], "blockedScopePatterns": []},
            "rpa": {"auditTriggerSources": [], "reviewTriggerSources": [], "blockedTriggerSources": [], "auditScopePatterns": [], "reviewScopePatterns": [], "blockedScopePatterns": []},
        },
        "skillRules": {
            "declarationVerdict": "audit",
            "localSecretReadVerdict": "review",
            "browserProfileAccessVerdict": {
                "dedicated_runtime_host": "review",
                "developer_mixed_host": "block",
            },
            "downloadExecuteVerdict": "block",
            "persistenceVerdict": "block",
            "destructiveVerdict": "block",
            "binaryPayloadVerdict": "review",
            "llmReviewEnabledFor": ["review"],
        },
        "networkMutationRules": {
            "defaultExternalMutationVerdict": {
                "dedicated_runtime_host": "audit",
                "developer_mixed_host": "review",
            },
            "sensitivePayloadVerdict": "review",
            "credentialExfiltrationVerdict": "block",
        },
        "computerUseRules": {
            "defaultMutationVerdict": {
                "dedicated_runtime_host": "audit",
                "developer_mixed_host": "review",
            },
            "destructiveKeywordVerdict": "block",
            "hotkeyLifecycleVerdict": "review",
        },
        "systemIntegrityRules": {
            "packageInstallVerdict": {
                "dedicated_runtime_host": "audit",
                "developer_mixed_host": "review",
            },
            "destructiveCommandVerdict": "block",
        },
        "v8IntegrityRules": {
            "protectedConfigWriteVerdict": "review",
            "protectedRuntimeProcessVerdict": "block",
        },
        "channelGroupGuard": {
            "enabled": False,
            "allowlistOnly": False,
            "requireMention": False,
            "auditOnly": False,
            "allowlistGroups": [],
        },
        "postActionRules": {
            "enabledFamilies": [
                "command",
                "file_write",
                "http_request",
                "process",
                "cron_mutation",
                "hook_mutation",
                "background_command",
                "automation_action",
                "computer_use_action",
            ],
            "highlightFamilies": ["process", "cron_mutation", "hook_mutation", "http_request", "computer_use_action"],
            "mutatingHttpMethods": ["POST", "PUT", "PATCH", "DELETE"],
        },
        "activeDefense": {
            "enabled": False,
            "sampleIntervalSeconds": 20,
            "injectHostAlerts": True,
            "maxInjectedProcesses": 3,
            "highCpuPercent": 85,
            "highMemoryPercent": 25,
            "highMemoryRssMb": 2048,
            "networkTunnelPolicy": "confirm_first",
            "knownNetworkTools": [],
            "knownListeningPorts": ["tcp:9527", "tcp:9528", "tcp:9530"],
        },
    },
    "projects": {
        "version": 2,
        "defaultProjectId": None,
        "projects": [],
        "workspacePresentations": [],
    },
    "desktopPet": {
        "eventVoice": {
            "enabled": True,
            "mode": "system_tts",
            "voiceRef": "",
            "speakVoiceTags": True,
            "speakSupervisorReplies": True,
        },
        "actionTable": [
            {"id": "thinking", "event": "run.reasoning.delta", "emotion": "thinking", "spectrum": "violet"},
            {"id": "tool_started", "event": "tool.started", "emotion": "tool_calling", "spectrum": "blue"},
            {"id": "tool_finished", "event": "tool.finished", "emotion": "idle", "spectrum": "blue"},
            {"id": "waiting_answer", "event": "ask_user.requested", "emotion": "curious", "spectrum": "golden_amber"},
            {"id": "waiting_approval", "event": "approval.requested", "emotion": "curious", "spectrum": "golden_amber"},
            {"id": "artifact_ready", "event": "artifact.recorded", "emotion": "happy", "spectrum": "emerald_green"},
            {"id": "completed", "event": "run.completed", "emotion": "happy", "spectrum": "emerald_green"},
            {"id": "failed", "event": "run.failed", "emotion": "worried", "spectrum": "crimson_red"},
        ],
        "effectSpectrum": {
            "preset": "soft",
            "intensity": 0.75,
            "customGlowColor": "",
        },
    },
    "music": {"tracks": []},
    "webFetchProfiles": {"version": 1, "sites": {}},
    "mediaDownloadProfiles": {"version": 1, "platforms": {}},
    "runtimeRegistry": {
        "version": 1,
        "installProfile": "minimal",
        "installPlatform": "",
        "installedRuntimeFamilies": [],
        "featurePacks": {},
        "bootstrapManaged": False,
        "lastUpgradeAt": "",
        "startupProfile": "minimal",
        "policies": {},
    },
}


LEGACY_STRUCTURED_FILE_TO_DOMAIN = {
    "models.json": "models",
    "mcp_servers.json": "mcp",
    "memory_config.json": "memory",
    "supervisor_config.json": "supervisor",
    "workspace_config.json": "workspace",
    "hooks_config.json": "hooks",
    "cron_config.json": "cron",
    "automation_runtime.json": "automationRuntime",
    "network_supervisor_runtime.json": "networkSupervisorRuntime",
    "context_config.json": "context",
    "audio_config.json": "audio",
    "runtime_stability.json": "runtimeStability",
    "safety_guardian.json": "safety",
    "projects.json": "projects",
    "web_fetch_profiles.json": "webFetchProfiles",
    "media_download_profiles.json": "mediaDownloadProfiles",
    "runtime_registry.json": "runtimeRegistry",
}

EXTERNAL_IMPORT_FILE_TO_DOMAIN = {
    "models.json": "models",
    "mcp_servers.json": "mcp",
    "memory_config.json": "memory",
    "supervisor_config.json": "supervisor",
    "workspace_config.json": "workspace",
    "hooks_config.json": "hooks",
    "cron_config.json": "cron",
    "automation_runtime.json": "automationRuntime",
    "network_supervisor_runtime.json": "networkSupervisorRuntime",
    "context_config.json": "context",
    "audio_config.json": "audio",
    "runtime_stability.json": "runtimeStability",
    "safety_guardian.json": "safety",
    "projects.json": "projects",
    "runtime_registry.json": "runtimeRegistry",
}

class StorageManager:
    """
    Manages the `~/.v8-agent-os` directory structure and unified file I/O operations.
    Replaces the Prisma database with local Markdown and JSONL file persistence.
    """
    def __init__(self):
        # Resolve the absolute path to `~/.v8-agent-os`
        self.base_dir = CONFIG_JSON_PATH.parent
        self._legacy_model_bindings_migrated = False
        self._config_payload_cache_signature: tuple[int, int] | None = None
        self._config_payload_cache_data: dict[str, Any] | None = None
        self._initialize_structure()
        
    def _initialize_structure(self):
        """Ensures all required directories and default files exist."""
        dirs_to_create = [
            self.base_dir,
            self.base_dir / "core",
            self.base_dir / "core" / "oauth",
            self.base_dir / "core" / "oauth" / "providers",
            self.base_dir / "workspace",
            RUNTIME_DATA_HOME,
            runtime_private_root("computer_use"),
            runtime_private_root("rpa"),
            runtime_private_root("plugin_manager"),
            self.base_dir / "agents",
            self.base_dir / "commands",
            self.base_dir / "sessions",
            self.base_dir / "web_fetch",
            self.base_dir / "plugins",
        ]
        for d in dirs_to_create:
            d.mkdir(parents=True, exist_ok=True)
        defaults = {
            "V8_AGENT_OS.md": (
                "# V8 Agent OS Supervisor Prompt\n\n"
                "You are V8 Agent OS, the user-facing intelligent supervisor for a recoverable AI operating system.\n"
                "You are not a generic chat bot. Your primary responsibility is to understand the user's instruction, choose the right product path, keep work correct, recoverable, observable, and merge verified results.\n\n"
                "## Primary Goal\n"
                "- Solve user tasks with the smallest stable plan that still preserves recoverability.\n"
                "- Prefer the right V8OS mode over ad-hoc tool chaos when a task needs stronger context, boundaries, or proof.\n"
                "- Keep long tasks resumable, inspectable, and stable.\n\n"
                f"{_PRODUCT_LANGUAGE_PROMPT_BLOCK}"
                "## Runtime Worldview\n"
                "Think in product paths, not in giant capability catalogs.\n"
                "- Prefer the active mode card and current route over memorizing every subsystem.\n"
                "- Treat 记忆系统, 定时与触发, 插件管理中心, 桌面操作, and 自动流程 as managed systems that can be consulted or delegated when needed.\n"
                "- Only expand deeper diagnostic detail when the current task truly depends on it.\n\n"
                "## Multi-Runtime Orchestration\n"
                "- When a request combines research and implementation, keep Supervisor as the coordinator: gather source-backed evidence first, then choose the implementation route.\n"
                "- For complex or freshness-sensitive research, grant `research.core` and first call `research_broker(mode=\"search_experience\")` for reusable experience packs; run new `research_broker(mode=\"run\")` only when packs are missing, stale, low-confidence, or conflicting.\n"
                "- 编程模式 / Engineering work mode is a session-level Supervisor posture. It permits direct long-running project execution; it does not force an Engineering Runtime episode.\n"
                "- Use a named registered subagent or Engineering episode when specialist context, parallelism, recovery, or durable proof is useful. Do not auto-route solely because a task is large or multi-file.\n"
                "- Before adding a hard restriction for an Agent failure, verify the Agent received the exact registry names, task contract, workspace facts, tool availability, and peer-boundary summary. Repair missing information first.\n"
                "- New project creation can use Engineering project-creation workspace mode after workspace inventory; do not treat an empty workspace alone as sufficient, but do not block Engineering only because repoDetected=false.\n"
                "- Do not say you are dispatching or assigning a subagent unless you actually call `delegation_broker`; if you choose direct Supervisor execution, say that directly.\n"
                "- Supervisor todos are cross-runtime milestones; Engineering proof, worksets, research evidence, media recipes, and command sessions stay in their runtime ledgers/cards.\n\n"
                "## Tool Discipline\n"
                "- Choose direct Supervisor execution, a runtime-managed path, or a named subagent by delivery quality, specialist context, parallelism, recovery, and proof needs.\n"
                "- Use route-selected skills / MCP and explicit plugin grants instead of exploring every tool family at once.\n"
                "- Baseline system tools are a normal direct execution surface; task size alone never removes them from the Supervisor.\n"
                "- Escalate to low-level or destructive tools only when clearly necessary and safe.\n\n"
                "- Use command sessions, not sync commands, for scaffolding, dependency installs, dev servers, CLIs that may prompt, and long-running processes.\n\n"
                "Do not treat a route miss as a ban. Expand deliberately only when the task is blocked or stale.\n\n"
                "## Delegation Discipline\n"
                "- Solve work directly whenever that is the clearest path, including long multi-file projects in session Engineering work mode.\n"
                "- Delegate when a distinct role, independent context, parallel execution, recovery, or durable proof materially helps.\n"
                "- If the registered Agent list has a real capability gap, use `agent_broker` to list, propose, obtain one explicit user approval, create, validate, and then delegate to the exact new name in the same run.\n"
                "- Use `delegation_broker` as the internal canonical delegation entrypoint, but say 子代理 / 协作 worker to users.\n"
                "- For manual local delegation, choose an exact registered subagent name from the visible name+description registry and pass task.targetAgentName. Do not blindly dispatch by family.\n"
                "- Treat Supervisor-authored task briefs as the canonical delegation contract.\n"
                "- Keep local subagents and external workers on the same brokered path instead of mixing old delegation tools.\n"
                "- Subagents should inherit relevant skills, MCP, explicit plugin grants, and baseline tool context instead of starting blind.\n"
                "- Subagents do not have ComputerUse, RPA, or Memory runtime authority by default; keep those managed runtime actions, route gates, and final verification in the supervisor unless a brokered task explicitly grants a narrow surface.\n\n"
                "## Todo Discipline\n"
                "- For non-trivial tasks, create and maintain todos.\n"
                "- A plan is not decoration: keep it updated.\n"
                "- Prefer one `in_progress` item at a time unless parallel work is explicit.\n"
                "- If progress stalls, explain the blocker and adjust the plan.\n\n"
                "## Recoverability And Observability\n"
                "- Keep work resumable, inspectable, and event-backed.\n"
                "- If something is blocked, say what is blocked, what is done, and what should happen next.\n"
                "- When external channels or plugins are involved, trust runtime state over stale projections.\n\n"
                "## Language Protocol\n"
                "- Infer the preferred user-visible language from the latest human request and keep Supervisor plans, runtime briefs, tool summaries, and final replies in that language.\n"
                "- Preserve raw code, commands, stdout/stderr, provider names, protocol fields, and file paths in their original form.\n"
                "- Use product words for user-facing explanations; keep canonical ids, tool names, model ids, protocol fields, and page paths unchanged only in tool calls, diagnostics, logs, or exact references.\n\n"
                "## Collaboration Style\n"
                "- Be decisive, but do not guess when a runtime fact can be observed.\n"
                "- Prefer small, reversible changes over clever but brittle jumps.\n"
                "- When a task spans multiple runtimes, route intentionally instead of collapsing everything into one response.\n"
                "- When a user asks for implementation, move forward unless a choice is truly architecture-breaking.\n"
            ),
            "users.json": json.dumps({"users": []}, indent=2, ensure_ascii=False),
        }

        for filename, content in defaults.items():
            filepath = self.base_dir / filename
            if filepath.exists():
                continue
            try:
                with open(filepath, "w", encoding="utf-8", newline="\n") as f:
                    f.write(content)
            except PermissionError:
                continue

        self._sanitize_stock_supervisor_prompt_file()
        self._ensure_default_subagents()
        self._ensure_config_json_exists()
        self._remove_deprecated_subagent_model_bindings()
        self._migrate_computer_use_storage()
        self._migrate_legacy_structured_files()

    def _ensure_default_subagents(self):
        agents_dir = self.base_dir / "agents"
        try:
            agents_dir.mkdir(parents=True, exist_ok=True)
            from core.agents import (
                DEFAULT_SUBAGENT_TEMPLATE_VERSION,
                DEPRECATED_DEFAULT_SUBAGENT_IDS,
                default_subagent_configs,
                dump_agent_md,
                parse_agent_md,
            )

            backup_dir: Path | None = None

            def backup_once(path: Path) -> None:
                nonlocal backup_dir
                if not path.exists():
                    return
                if backup_dir is None:
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    backup_dir = self.base_dir / "backups" / "agents" / stamp
                    backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_dir / path.name)

            def is_managed_default(path: Path) -> bool:
                try:
                    content = path.read_text(encoding="utf-8")
                    parsed = parse_agent_md(content, path.name)
                except Exception:
                    return False
                if parsed.defaultTemplateVersion:
                    return True
                source = str((parsed.capabilitySnapshot or {}).get("source") or "").strip()
                if source == "system_default":
                    return True
                legacy_markers = (
                    "a focused V8 Agent OS subagent",
                    "Shared engineering discipline:",
                    "When delegated a task, respond with a compact result",
                )
                return any(marker in content for marker in legacy_markers)

            for deprecated_id in sorted(DEPRECATED_DEFAULT_SUBAGENT_IDS):
                deprecated_path = agents_dir / f"{deprecated_id}.md"
                if deprecated_path.exists() and is_managed_default(deprecated_path):
                    backup_once(deprecated_path)
                    deprecated_path.unlink()

            for agent_config in default_subagent_configs():
                agent_path = agents_dir / f"{agent_config.id}.md"
                should_write = not agent_path.exists()
                if agent_path.exists():
                    try:
                        existing = parse_agent_md(agent_path.read_text(encoding="utf-8"), agent_path.name)
                    except Exception:
                        existing = None
                    existing_version = getattr(existing, "defaultTemplateVersion", "") if existing else ""
                    if existing_version != DEFAULT_SUBAGENT_TEMPLATE_VERSION and is_managed_default(agent_path):
                        backup_once(agent_path)
                        should_write = True
                if should_write:
                    with open(agent_path, "w", encoding="utf-8", newline="\n") as handle:
                        handle.write(dump_agent_md(agent_config))

            # If an old default had been renamed manually but still declares a
            # system-default source, leave it intact. Only canonical default
            # ids and known deprecated ids are managed here.
        except (OSError, PermissionError) as exc:
            print(f"[Storage] Default subagent initialization skipped: {exc}")

    def _sanitize_stock_supervisor_prompt_file(self):
        filepath = self.base_dir / "V8_AGENT_OS.md"
        if not filepath.exists():
            return
        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, PermissionError):
            return
        sanitized = _sanitize_stock_supervisor_prompt_text(content)
        if sanitized == content:
            return
        try:
            with open(filepath, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(sanitized)
        except (OSError, PermissionError):
            return

    def _remove_deprecated_subagent_model_bindings(self) -> None:
        """Remove bindings for retired managed defaults after their files are gone.

        A user-authored agent file with the same id remains authoritative and keeps
        its binding. This cleanup only closes the residue left by a managed default
        that `_ensure_default_subagents` already removed.
        """

        try:
            from core.agents import DEPRECATED_DEFAULT_SUBAGENT_IDS

            payload = self._read_raw_config_payload()
            models = dict(payload.get("models") or {})
            bindings = dict(models.get("bindings") or {})
            agent_bindings = dict(bindings.get("agents") or {})
            changed = False
            for deprecated_id in sorted(DEPRECATED_DEFAULT_SUBAGENT_IDS):
                if (self.base_dir / "agents" / f"{deprecated_id}.md").exists():
                    continue
                if deprecated_id in agent_bindings:
                    agent_bindings.pop(deprecated_id, None)
                    changed = True
            if not changed:
                return
            bindings["agents"] = agent_bindings
            models["bindings"] = bindings
            payload["models"] = models
            self._write_config_payload(payload)
        except (OSError, PermissionError, TypeError, ValueError) as exc:
            print(f"[Storage] Deprecated subagent binding cleanup skipped: {exc}")

    def _default_system_base_config(self) -> dict[str, Any]:
        engine_base_url = _normalize_http_base_url(
            os.getenv("V8_AGENT_OS_ENGINE_URL") or os.getenv("PYTHON_ENGINE_URL"),
            "http://127.0.0.1:9530/v1",
        )
        admin_base_url = _normalize_http_base_url(
            os.getenv("NEXT_PUBLIC_API_BASE_URL"),
            "http://127.0.0.1:9528/api",
        )
        cache_dir = str(self.base_dir / "web_fetch")
        source_router_defaults = get_source_router_defaults()
        source_provider_defaults = get_source_provider_config_defaults()
        return {
            "identity": default_system_identity(),
            "bridge": {
                "engineBaseUrl": engine_base_url,
                "engineWsBaseUrl": _derive_ws_base_url(
                    os.getenv("V8_AGENT_OS_ENGINE_WS_URL")
                    or os.getenv("NEXT_PUBLIC_V8_AGENT_OS_ENGINE_WS_URL")
                    or engine_base_url
                ),
                "adminBaseUrl": admin_base_url,
                "desktopLiveBridgeBaseUrl": _normalize_http_base_url(
                    os.getenv("V8_AGENT_OS_DESKTOP_LIVE_BRIDGE_URL"),
                    "http://127.0.0.1:8011/v1",
                ),
                "internalSecret": str(os.getenv("V8_AGENT_OS_INTERNAL_SECRET") or uuid4().hex),
                "allowedOrigins": [],
            },
            "webFetch": {
                "bypassProxyEnv": str(os.getenv("V8_AGENT_OS_WEB_FETCH_BYPASS_PROXY_ENV") or "").strip().lower()
                in {"1", "true", "yes", "on"},
                "cacheDir": str(os.getenv("V8_AGENT_OS_WEB_FETCH_CACHE_DIR") or cache_dir),
                "adaptiveStorageFile": str(
                    os.getenv("V8_AGENT_OS_WEB_FETCH_ADAPTIVE_STORAGE_FILE")
                    or (Path(cache_dir) / "adaptive" / "global.db")
                ),
                "useAgentBrowserProfile": False,
                "agentBrowserProfileAllowlist": [],
                "sourceRouter": source_router_defaults,
                "providers": source_provider_defaults,
            },
            "desktopTools": {
                "tesseractPath": str(os.getenv("TESSERACT_PATH") or ""),
                "tessdataPrefix": str(os.getenv("TESSDATA_PREFIX") or ""),
            },
            "desktopLive": {
                "enabled": str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_ENABLED") or "true").strip().lower() not in {"0", "false", "no", "off"},
                "maxWidth": int(str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_MAX_WIDTH") or "960")),
                "maxHeight": int(str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_MAX_HEIGHT") or "540")),
                "targetFps": int(str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_TARGET_FPS") or "10")),
                "singleViewerOnly": str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_SINGLE_VIEWER_ONLY") or "true").strip().lower() not in {"0", "false", "no", "off"},
                "idleReleaseSeconds": int(str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_IDLE_RELEASE_SECONDS") or "15")),
                "captureDisplay": str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_CAPTURE_DISPLAY") or "primary"),
                "audioEnabled": str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_AUDIO_ENABLED") or "true").strip().lower() not in {"0", "false", "no", "off"},
                "audioSource": str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_AUDIO_SOURCE") or "system"),
                "audioSampleRate": int(str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_AUDIO_SAMPLE_RATE") or "48000")),
                "audioChannels": int(str(os.getenv("V8_AGENT_OS_DESKTOP_LIVE_AUDIO_CHANNELS") or "2")),
                "iceServers": [],
            },
            "remoteLink": {
                "enabled": True,
                "activeProfileId": "manual-local",
                "transportProfiles": [
                    {
                        "id": "manual-local",
                        "kind": "manual_url",
                        "label": "Manual / Local",
                        "enabled": True,
                        "adminBaseUrl": admin_base_url.replace("/api", "").rstrip("/"),
                        "engineBaseUrl": engine_base_url.replace("/v1", "").rstrip("/"),
                        "peerBaseUrl": engine_base_url.replace("/v1", "").rstrip("/"),
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
            },
            "s3": {},
            "legacySettings": [],
        }

    def _default_config_payload(self) -> dict[str, Any]:
        payload = {key: deepcopy(value) for key, value in STRUCTURED_CONFIG_DEFAULTS.items()}
        payload["systemBase"] = self._default_system_base_config()
        supervisor_profile = dict((payload.get("supervisor") or {}).get("profile") or {})
        if not str(supervisor_profile.get("avatar") or "").strip():
            supervisor_profile["avatar"] = _default_supervisor_avatar_url(
                ((payload.get("systemBase") or {}).get("bridge") or {}).get("adminBaseUrl")
            )
        payload.setdefault("supervisor", {})["profile"] = supervisor_profile
        return payload

    def _default_computer_use_payload(self) -> dict[str, Any]:
        return {"version": 1, "apps": {}}

    def _deep_merge(self, base: Any, incoming: Any) -> Any:
        if isinstance(base, dict) and isinstance(incoming, dict):
            merged = {key: deepcopy(value) for key, value in base.items()}
            for key, value in incoming.items():
                merged[key] = self._deep_merge(merged.get(key), value) if key in merged else deepcopy(value)
            return merged
        if isinstance(base, list) and isinstance(incoming, list):
            return deepcopy(incoming)
        return deepcopy(incoming if incoming is not None else base)

    def _is_semantically_empty(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, tuple, set, dict)):
            return len(value) == 0
        return False

    def _merge_external_preserving_current(
        self,
        current: Any,
        incoming: Any,
        *,
        path: list[str],
        conflicts: list[dict[str, Any]],
    ) -> Any:
        if isinstance(current, dict) and isinstance(incoming, dict):
            merged = {key: deepcopy(value) for key, value in current.items()}
            for key, incoming_value in incoming.items():
                next_path = [*path, str(key)]
                if key not in merged:
                    merged[key] = deepcopy(incoming_value)
                    continue
                merged[key] = self._merge_external_preserving_current(
                    merged.get(key),
                    incoming_value,
                    path=next_path,
                    conflicts=conflicts,
                )
            return merged

        if isinstance(current, list) and isinstance(incoming, list):
            if not current and incoming:
                return deepcopy(incoming)
            if current and incoming and current != incoming:
                conflicts.append(
                    {
                        "path": ".".join(path),
                        "current": deepcopy(current),
                        "incoming": deepcopy(incoming),
                    }
                )
            return deepcopy(current)

        if self._is_semantically_empty(current) and not self._is_semantically_empty(incoming):
            return deepcopy(incoming)

        if not self._is_semantically_empty(current) and not self._is_semantically_empty(incoming) and current != incoming:
            conflicts.append(
                {
                    "path": ".".join(path),
                    "current": deepcopy(current),
                    "incoming": deepcopy(incoming),
                }
            )
        return deepcopy(current)

    def _read_raw_config_payload(self) -> dict[str, Any]:
        if not CONFIG_JSON_PATH.exists():
            return {}
        try:
            payload = self._read_json_file(CONFIG_JSON_PATH)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _partition_config_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        known_domains = set(self._default_config_payload())
        supported = {
            key: deepcopy(value)
            for key, value in dict(payload or {}).items()
            if key in known_domains
        }
        ignored = {
            key: deepcopy(value)
            for key, value in dict(payload or {}).items()
            if key not in known_domains
        }
        return supported, ignored

    def _report_ignored_config_domains(self, ignored: dict[str, Any]) -> None:
        names = tuple(sorted(str(key) for key in ignored))
        if not names or names == getattr(self, "_last_ignored_config_domains", ()):
            return
        self._last_ignored_config_domains = names
        print(
            "[Storage] Ignoring unsupported config domains while preserving them on disk: "
            + ", ".join(names)
        )

    def _config_payload_for_persistence(self, payload: dict[str, Any]) -> dict[str, Any]:
        incoming_supported, incoming_ignored = self._partition_config_payload(dict(payload or {}))
        _, existing_ignored = self._partition_config_payload(self._read_raw_config_payload())
        preserved_ignored = {**existing_ignored, **incoming_ignored}
        normalized_supported = self._deep_merge(self._default_config_payload(), incoming_supported)
        return {**preserved_ignored, **normalized_supported}

    def _config_payload_signature(self) -> tuple[int, int] | None:
        try:
            stat = CONFIG_JSON_PATH.stat()
            return (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return None

    def _invalidate_config_payload_cache(self) -> None:
        self._config_payload_cache_signature = None
        self._config_payload_cache_data = None

    def _ensure_config_json_exists(self):
        if CONFIG_JSON_PATH.exists():
            return
        try:
            self.write_json("config.json", self._default_config_payload())
        except PermissionError:
            pass

    def _read_config_payload(self) -> dict[str, Any]:
        if not CONFIG_JSON_PATH.exists():
            self._ensure_config_json_exists()
        signature = self._config_payload_signature()
        if (
            signature is not None
            and getattr(self, "_config_payload_cache_signature", None) == signature
            and getattr(self, "_config_payload_cache_data", None) is not None
        ):
            return deepcopy(self._config_payload_cache_data)
        try:
            payload = self._read_json_file(CONFIG_JSON_PATH) if CONFIG_JSON_PATH.exists() else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            migrated_payload = _maybe_migrate_legacy_local_config(payload)
            if migrated_payload != payload:
                payload = migrated_payload
                self._write_config_payload(payload)
                signature = self._config_payload_signature()
        supported_payload, ignored_payload = self._partition_config_payload(payload if isinstance(payload, dict) else {})
        self._report_ignored_config_domains(ignored_payload)
        merged = self._deep_merge(self._default_config_payload(), supported_payload)
        if signature is not None:
            self._config_payload_cache_signature = signature
            self._config_payload_cache_data = deepcopy(merged)
        return deepcopy(merged)

    def _write_config_payload(self, payload: dict[str, Any]):
        self.write_json("config.json", payload)

    def _read_computer_use_payload(self) -> dict[str, Any]:
        if not COMPUTER_USE_JSON_PATH.exists():
            self._migrate_computer_use_storage()
        try:
            payload = self._read_json_file(COMPUTER_USE_JSON_PATH) if COMPUTER_USE_JSON_PATH.exists() else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        merged = self._deep_merge(
            self._default_computer_use_payload(),
            payload if isinstance(payload, dict) else {},
        )
        return merged

    def _write_computer_use_payload(self, payload: dict[str, Any]):
        filepath = COMPUTER_USE_JSON_PATH
        data = self._deep_merge(self._default_computer_use_payload(), dict(payload or {}))
        serialized = json.dumps(data, indent=2, ensure_ascii=False)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        temp_path = filepath.with_name(f".{filepath.name}.{uuid4().hex}.tmp")
        with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        try:
            self._replace_computer_use_file(temp_path, filepath)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
        backup_path = self._json_backup_path(filepath)
        backup_temp_path = backup_path.with_name(f".{backup_path.name}.{uuid4().hex}.tmp")
        try:
            with open(backup_temp_path, "w", encoding="utf-8", newline="\n") as backup_file:
                backup_file.write(serialized)
                backup_file.flush()
                os.fsync(backup_file.fileno())
            self._replace_computer_use_file(backup_temp_path, backup_path)
        finally:
            if backup_temp_path.exists():
                try:
                    backup_temp_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _replace_computer_use_file(source: Path, target: Path) -> None:
        for attempt in range(6):
            try:
                os.replace(source, target)
                return
            except PermissionError:
                if attempt >= 5:
                    raise
                time.sleep(0.04 * (2 ** attempt))

    def _migrate_computer_use_storage(self):
        default_payload = self._default_computer_use_payload()
        current_payload = {}
        if COMPUTER_USE_JSON_PATH.exists():
            try:
                current_payload = self._read_json_file(COMPUTER_USE_JSON_PATH)
            except (UnicodeDecodeError, json.JSONDecodeError):
                current_payload = {}

        config_payload = self._read_config_payload()
        legacy_domain_payload = (
            deepcopy(config_payload.get("computerUseMemory"))
            if isinstance(config_payload.get("computerUseMemory"), dict)
            else {}
        )

        legacy_file_path = self.base_dir / "computer_use_memory.json"
        legacy_file_payload = {}
        if legacy_file_path.exists():
            try:
                legacy_file_payload = self._read_json_file(legacy_file_path)
            except (UnicodeDecodeError, json.JSONDecodeError):
                legacy_file_payload = {}

        merged_payload = self._deep_merge(default_payload, current_payload if isinstance(current_payload, dict) else {})
        if isinstance(legacy_domain_payload, dict) and legacy_domain_payload:
            merged_payload = self._deep_merge(merged_payload, legacy_domain_payload)
        if isinstance(legacy_file_payload, dict) and legacy_file_payload:
            merged_payload = self._deep_merge(merged_payload, legacy_file_payload)

        if merged_payload != current_payload or not COMPUTER_USE_JSON_PATH.exists():
            self._write_computer_use_payload(merged_payload)

        config_changed = False
        if "computerUseMemory" in config_payload:
            config_payload.pop("computerUseMemory", None)
            config_changed = True
        if config_changed:
            self._write_config_payload(config_payload)

        if legacy_file_path.exists():
            backup_dir = self._legacy_backup_dir()
            self._archive_legacy_file(legacy_file_path, backup_dir)

    def _legacy_backup_dir(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = LEGACY_CONFIG_BACKUP_ROOT / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir

    def _archive_legacy_file(self, filepath: Path, backup_dir: Path):
        if not filepath.exists():
            return
        target = backup_dir / filepath.name
        if target.exists():
            target = backup_dir / f"{filepath.stem}-{uuid4().hex[:8]}{filepath.suffix}"
        shutil.move(str(filepath), str(target))

    def _legacy_settings_to_system_base(self, raw: dict[str, Any]) -> dict[str, Any]:
        current = self._default_system_base_config()
        if isinstance(raw.get("s3"), dict):
            current["s3"] = deepcopy(raw.get("s3"))
        elif isinstance(raw.get("s3_config"), dict):
            current["s3"] = deepcopy(raw.get("s3_config"))
        settings_list = raw.get("settings")
        if isinstance(settings_list, list):
            filtered_settings: list[dict[str, Any]] = []
            for item in settings_list:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "").strip()
                value = item.get("value")
                if key == "S3_CONFIG" and isinstance(value, dict):
                    current["s3"] = self._deep_merge(current.get("s3") or {}, value)
                    continue
                filtered_settings.append(deepcopy(item))
            current["legacySettings"] = filtered_settings
        return current

    def _system_base_to_legacy_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "s3": deepcopy((data or {}).get("s3") or {}),
            "settings": deepcopy((data or {}).get("legacySettings") or []),
        }
        return payload

    def _migrate_legacy_structured_files(self):
        backup_dir: Optional[Path] = None
        config_payload = self._read_config_payload()
        changed = False

        settings_path = self.base_dir / "settings.json"
        if settings_path.exists():
            try:
                raw_settings = self._read_json_file(settings_path)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raw_settings = {}
            merged = self._deep_merge(config_payload.get("systemBase", {}), self._legacy_settings_to_system_base(raw_settings))
            if merged != config_payload.get("systemBase"):
                config_payload["systemBase"] = merged
                changed = True
            backup_dir = backup_dir or self._legacy_backup_dir()
            self._archive_legacy_file(settings_path, backup_dir)

        for filename, domain in LEGACY_STRUCTURED_FILE_TO_DOMAIN.items():
            filepath = self.base_dir / filename
            if not filepath.exists():
                continue
            try:
                incoming = self._read_json_file(filepath)
            except (UnicodeDecodeError, json.JSONDecodeError):
                incoming = {}
            merged = self._deep_merge(config_payload.get(domain), incoming)
            if merged != config_payload.get(domain):
                config_payload[domain] = merged
                changed = True
            backup_dir = backup_dir or self._legacy_backup_dir()
            self._archive_legacy_file(filepath, backup_dir)

        if changed:
            self._write_config_payload(config_payload)

    def import_external_legacy_root(self, source_root: str | Path) -> dict[str, Any]:
        source_base = Path(source_root).expanduser()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = LEGACY_CONFIG_BACKUP_ROOT / f"external_v8_agent_os_{timestamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        raw_config = self._read_raw_config_payload()
        changed = False
        imported_domains: list[dict[str, Any]] = []
        skipped_files: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []

        def _copy_source_file(filepath: Path):
            if not filepath.exists() or not filepath.is_file():
                return
            target = backup_dir / filepath.name
            if target.exists():
                target = backup_dir / f"{filepath.stem}-{uuid4().hex[:8]}{filepath.suffix}"
            shutil.copy2(filepath, target)

        settings_path = source_base / "settings.json"
        if settings_path.exists():
            try:
                raw_settings = self._read_json_file(settings_path)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                skipped_files.append(
                    {
                        "file": str(settings_path),
                        "reason": f"invalid_json:{exc.__class__.__name__}",
                    }
                )
            else:
                _copy_source_file(settings_path)
                before = deepcopy(raw_config.get("systemBase") or {})
                domain_conflicts: list[dict[str, Any]] = []
                incoming = self._legacy_settings_to_system_base(raw_settings)
                merged = self._merge_external_preserving_current(
                    before,
                    incoming,
                    path=["systemBase"],
                    conflicts=domain_conflicts,
                )
                if merged != before:
                    raw_config["systemBase"] = merged
                    changed = True
                imported_domains.append(
                    {
                        "domain": "systemBase",
                        "sourceFile": str(settings_path),
                        "updated": merged != before,
                        "conflictCount": len(domain_conflicts),
                    }
                )
                conflicts.extend(domain_conflicts)
        else:
            skipped_files.append({"file": str(settings_path), "reason": "missing"})

        for filename, domain in EXTERNAL_IMPORT_FILE_TO_DOMAIN.items():
            filepath = source_base / filename
            if not filepath.exists():
                skipped_files.append({"file": str(filepath), "reason": "missing"})
                continue
            try:
                incoming = self._read_json_file(filepath)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                skipped_files.append(
                    {
                        "file": str(filepath),
                        "reason": f"invalid_json:{exc.__class__.__name__}",
                    }
                )
                continue

            _copy_source_file(filepath)
            before = deepcopy(raw_config.get(domain) or {})
            domain_conflicts: list[dict[str, Any]] = []
            merged = self._merge_external_preserving_current(
                before,
                incoming,
                path=[domain],
                conflicts=domain_conflicts,
            )
            if merged != before:
                raw_config[domain] = merged
                changed = True
            imported_domains.append(
                {
                    "domain": domain,
                    "sourceFile": str(filepath),
                    "updated": merged != before,
                    "conflictCount": len(domain_conflicts),
                }
            )
            conflicts.extend(domain_conflicts)

        computer_use_imported = False
        for filename in ("computer_use.json", "computer_use_memory.json"):
            filepath = source_base / filename
            if not filepath.exists():
                continue
            try:
                incoming = self._read_json_file(filepath)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                skipped_files.append(
                    {
                        "file": str(filepath),
                        "reason": f"invalid_json:{exc.__class__.__name__}",
                    }
                )
                continue
            _copy_source_file(filepath)
            before = deepcopy(self.get_computer_use_memory() or {})
            merged = self._deep_merge(before, incoming if isinstance(incoming, dict) else {})
            if merged != before:
                self.save_computer_use_memory(merged)
                changed = True
            imported_domains.append(
                {
                    "domain": "computerUse",
                    "sourceFile": str(filepath),
                    "updated": merged != before,
                    "conflictCount": 0,
                }
            )
            computer_use_imported = True
            break
        if not computer_use_imported:
            skipped_files.append({"file": str(source_base / "computer_use.json"), "reason": "missing"})
            skipped_files.append({"file": str(source_base / "computer_use_memory.json"), "reason": "missing"})

        if changed:
            self.write_json("config.json", raw_config)

        return {
            "sourceRoot": str(source_base),
            "configPath": str(CONFIG_JSON_PATH),
            "backupDir": str(backup_dir),
            "changed": changed,
            "importedDomains": imported_domains,
            "skippedFiles": skipped_files,
            "conflicts": conflicts,
        }

    def _ensure_legacy_model_bindings_migrated(self):
        if self._legacy_model_bindings_migrated:
            return

        self._legacy_model_bindings_migrated = True

        models_config = self.read_json("models.json") or {}
        roles = dict(models_config.get("roles") or {})
        bindings = dict(models_config.get("bindings") or {})
        agent_bindings = dict(bindings.get("agents") or {})
        models_changed = False

        def _normalize_model_id(value: Any) -> str:
            normalized = str(value or "").strip()
            if not normalized or normalized.lower() in {"__empty__", "none", "null"}:
                return ""
            return normalized

        def _assign_role_if_missing(role: str, value: Any):
            nonlocal models_changed
            normalized = _normalize_model_id(value)
            if not normalized:
                return
            if _normalize_model_id(roles.get(role)):
                return
            roles[role] = normalized
            models_changed = True

        settings_config = self.read_json("settings.json") or {}
        settings_list = list(settings_config.get("settings") or [])
        default_agent_setting = next((item.get("value") for item in settings_list if item.get("key") == "DEFAULT_AGENT_MODEL_ID"), "")
        vision_setting = next((item.get("value") for item in settings_list if item.get("key") == "VISION_MODEL_ID"), "") or settings_config.get("vision_model_id")
        supervisor_setting = next((item.get("value") for item in settings_list if item.get("key") == "SUPERVISOR_MODEL_ID"), "")
        _assign_role_if_missing("default", default_agent_setting)
        _assign_role_if_missing("vision", vision_setting)
        _assign_role_if_missing("supervisor", supervisor_setting)

        raw_config_payload = self._read_config_payload()
        current_supervisor = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["supervisor"],
            raw_config_payload.get("supervisor") if isinstance(raw_config_payload.get("supervisor"), dict) else {},
        )
        current_profile = dict(current_supervisor.get("profile") or {})
        bridge = (self.get_system_base_config() or {}).get("bridge") or {}
        legacy_profile_map = {
            "supervisor-name": "name",
            "supervisor-role": "roleLabel",
            "SUPERVISOR_AVATAR": "avatar",
        }
        next_settings = []
        profile_changed = False
        for item in settings_list:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key in {"DEFAULT_AGENT_MODEL_ID", "VISION_MODEL_ID", "SUPERVISOR_MODEL_ID"}:
                continue
            target_field = legacy_profile_map.get(key)
            if not target_field:
                next_settings.append(item)
                continue
            legacy_value = str(item.get("value") or "").strip()
            if legacy_value and not str(current_profile.get(target_field) or "").strip():
                current_profile[target_field] = legacy_value
                profile_changed = True
        if not str(current_profile.get("avatar") or "").strip():
            current_profile["avatar"] = _default_supervisor_avatar_url(bridge.get("adminBaseUrl"))
            profile_changed = True
        if profile_changed:
            current_supervisor["profile"] = current_profile
            raw_config_payload["supervisor"] = current_supervisor
            self._write_config_payload(raw_config_payload)
        settings_changed = next_settings != settings_list or "vision_model_id" in settings_config
        if settings_changed:
            settings_payload = dict(settings_config)
            settings_payload["settings"] = next_settings
            settings_payload.pop("vision_model_id", None)
            self.write_json("settings.json", settings_payload)

        memory_config = self.read_json("memory_config.json") or {}
        _assign_role_if_missing("extraction", memory_config.get("extraction_model") or memory_config.get("model_id"))
        _assign_role_if_missing("embedding", memory_config.get("embedding_model"))
        _assign_role_if_missing("reranker", memory_config.get("reranker_model"))
        memory_changed = False
        if memory_config.get("temperature") is not None and memory_config.get("extraction_temperature") is None:
            memory_config["extraction_temperature"] = memory_config.get("temperature")
            memory_changed = True
        for legacy_key in ("model_id", "extraction_model", "embedding_model", "reranker_model", "temperature"):
            if legacy_key in memory_config:
                memory_config.pop(legacy_key, None)
                memory_changed = True
        if memory_changed:
            self.write_json("memory_config.json", memory_config)

        context_config = self.read_json("context_config.json") or {}
        compression = dict(context_config.get("compression") or {})
        _assign_role_if_missing("summary", compression.get("summary_model"))
        context_changed = False
        if "summary_model" in compression:
            compression.pop("summary_model", None)
            context_changed = True
        if context_changed:
            context_payload = dict(context_config)
            context_payload["compression"] = compression
            self.write_json("context_config.json", context_payload)

        supervisor_config = self.read_json("supervisor_config.json") or {}
        _assign_role_if_missing("supervisor", supervisor_config.get("model_id"))
        supervisor_changed = False
        if "model_id" in supervisor_config:
            supervisor_config.pop("model_id", None)
            supervisor_changed = True
        if supervisor_changed:
            self.write_json("supervisor_config.json", supervisor_config)

        try:
            from core.agents import parse_agent_md

            agents_dir = self.base_dir / "agents"
            for file_path in agents_dir.glob("*.md"):
                with open(file_path, "r", encoding="utf-8") as handle:
                    agent_content = handle.read()
                parsed = parse_agent_md(agent_content, file_path.name)
                normalized = _normalize_model_id(parsed.model)
                if not normalized:
                    continue
                existing = agent_bindings.get(parsed.id)
                existing_model = ""
                if isinstance(existing, dict):
                    existing_model = _normalize_model_id(existing.get("model_id") or existing.get("modelId"))
                else:
                    existing_model = _normalize_model_id(existing)
                if existing_model:
                    continue
                agent_bindings[parsed.id] = {"model_id": normalized}
                models_changed = True
        except Exception as e:
            print(f"[Storage] Legacy agent model binding migration skipped: {e}")

        if models_changed:
            models_payload = dict(models_config)
            models_payload["roles"] = roles
            bindings["agents"] = agent_bindings
            models_payload["bindings"] = bindings
            self.save_models_config(models_payload)

    # --- Generic JSON helpers ---
    def read_json(self, filename: str) -> Dict[str, Any]:
        """Reads a JSON file from the base directory."""
        normalized_name = str(filename or "").replace("\\", "/").strip()
        if normalized_name == "config.json":
            return self._read_config_payload()
        if normalized_name in {"computer_use.json", "computer_use_memory.json"}:
            return self._read_computer_use_payload()
        if normalized_name == "settings.json":
            return self._system_base_to_legacy_settings(self.get_system_base_config())
        mapped_domain = LEGACY_STRUCTURED_FILE_TO_DOMAIN.get(normalized_name)
        if mapped_domain:
            return deepcopy(self._read_config_payload().get(mapped_domain) or {})

        filepath = self.base_dir / filename
        if not filepath.exists():
            return {}
        try:
            return self._read_json_file(filepath)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            backup_path = self._json_backup_path(filepath)
            quarantine_path = self._quarantine_corrupt_json(filepath)
            if backup_path.exists():
                try:
                    recovered = self._read_json_file(backup_path)
                    self.write_json(filename, recovered)
                    print(
                        f"[Storage] JSON 文件 {filepath.name} 已损坏，已从备份 {backup_path.name} 自动恢复。"
                    )
                    return recovered
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
            print(
                f"[Storage] JSON 文件 {filepath.name} 读取失败：{exc}。"
                f"{' 已隔离到 ' + str(quarantine_path) + '。' if quarantine_path else ''}"
            )
            return {}

    def write_json(self, filename: str, data: Dict[str, Any]):
        """Writes data to a JSON file in the base directory."""
        normalized_name = str(filename or "").replace("\\", "/").strip()
        if normalized_name == "config.json":
            filepath = CONFIG_JSON_PATH
            payload = self._config_payload_for_persistence(dict(data or {}))
            serialized = json.dumps(payload, indent=2, ensure_ascii=False)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            temp_path = filepath.with_name(f".{filepath.name}.{uuid4().hex}.tmp")
            with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.replace(temp_path, filepath)
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

            backup_path = self._json_backup_path(filepath)
            backup_temp_path = backup_path.with_name(f".{backup_path.name}.{uuid4().hex}.tmp")
            try:
                with open(backup_temp_path, "w", encoding="utf-8", newline="\n") as backup_file:
                    backup_file.write(serialized)
                    backup_file.flush()
                    os.fsync(backup_file.fileno())
                os.replace(backup_temp_path, backup_path)
            finally:
                if backup_temp_path.exists():
                    try:
                        backup_temp_path.unlink()
                    except OSError:
                        pass
            self._invalidate_config_payload_cache()
            return

        if normalized_name in {"computer_use.json", "computer_use_memory.json"}:
            self._write_computer_use_payload(data)
            return
        if normalized_name == "settings.json":
            config_payload = self._read_config_payload()
            incoming = self._legacy_settings_to_system_base(dict(data or {}))
            config_payload["systemBase"] = self._deep_merge(config_payload.get("systemBase", {}), incoming)
            self._write_config_payload(config_payload)
            return

        mapped_domain = LEGACY_STRUCTURED_FILE_TO_DOMAIN.get(normalized_name)
        if mapped_domain:
            config_payload = self._read_config_payload()
            config_payload[mapped_domain] = self._deep_merge(config_payload.get(mapped_domain), dict(data or {}))
            self._write_config_payload(config_payload)
            return

        filepath = self.base_dir / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(data, indent=2, ensure_ascii=False)
        temp_path = filepath.with_name(f".{filepath.name}.{uuid4().hex}.tmp")
        with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(temp_path, filepath)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
        backup_path = self._json_backup_path(filepath)
        backup_temp_path = backup_path.with_name(f".{backup_path.name}.{uuid4().hex}.tmp")
        try:
            with open(backup_temp_path, "w", encoding="utf-8", newline="\n") as backup_file:
                backup_file.write(serialized)
                backup_file.flush()
                os.fsync(backup_file.fileno())
            os.replace(backup_temp_path, backup_path)
        finally:
            if backup_temp_path.exists():
                try:
                    backup_temp_path.unlink()
                except OSError:
                    pass

    def _read_json_file(self, filepath: Path) -> Dict[str, Any]:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def _json_backup_path(self, filepath: Path) -> Path:
        try:
            relative = filepath.relative_to(self.base_dir)
        except ValueError:
            relative = Path(filepath.name)
        backup_root = self.base_dir / "backups" / "json" / relative.parent
        backup_root.mkdir(parents=True, exist_ok=True)
        return backup_root / f"{relative.name}.bak"

    def _quarantine_corrupt_json(self, filepath: Path) -> Optional[Path]:
        if not filepath.exists():
            return None
        quarantine_path = filepath.with_name(
            f"{filepath.stem}.corrupt_{uuid4().hex[:10]}{filepath.suffix}"
        )
        try:
            shutil.copy2(filepath, quarantine_path)
            return quarantine_path
        except OSError:
            return None

    def read_text(self, filename: str) -> str:
        filepath = self.base_dir / filename
        if not filepath.exists():
            return ""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def write_text(self, filename: str, content: str):
        filepath = self.base_dir / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8", newline="\n") as f:
            f.write(content or "")
            
    def get_supervisor_prompt(self) -> str:
        """Reads the global V8_AGENT_OS.md supervisor system prompt."""
        filepath = self.base_dir / "V8_AGENT_OS.md"
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def get_system_identity(self) -> Dict[str, Any]:
        system_base = self.get_system_base_config()
        return normalize_system_identity(system_base.get("identity"))

    def get_default_agent_model_id(self) -> Optional[str]:
        """Resolve the default subagent model role, falling back to roles.default."""
        config = self.get_models_config()
        roles = config.get("roles", {})
        subagent_model_id = roles.get("subagent")
        if subagent_model_id:
            return subagent_model_id
        default_model_id = roles.get("default")
        if default_model_id:
            return default_model_id
        return None


            
    # --- Specialized Accessors ---
    @property
    def mcp_config_path(self) -> Path:
        return MCP_JSON_PATH

    def get_mcp_config(self) -> Dict[str, Any]:
        if MCP_JSON_PATH.exists():
            payload = self._read_json_file(MCP_JSON_PATH)
        else:
            payload = self._read_config_payload().get("mcp") or {}
            if payload:
                self.save_mcp_config(payload if isinstance(payload, dict) else {})
        normalized = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["mcp"],
            payload if isinstance(payload, dict) else {},
        )
        normalized.setdefault("mcpServers", {})
        return normalized

    def save_mcp_config(self, data: Dict[str, Any]):
        payload = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["mcp"],
            dict(data or {}),
        )
        payload.setdefault("mcpServers", {})
        self.write_json("mcp.json", payload)
        
    def get_models_config(self) -> Dict[str, Any]:
        """Reads the unified models.json (model catalog + role assignments)."""
        self._ensure_legacy_model_bindings_migrated()
        return self.read_json("models.json")
    
    def save_models_config(self, data: Dict[str, Any]):
        """Saves the unified models.json."""
        payload = self._read_config_payload()
        payload["models"] = deepcopy(dict(data or {}))
        self._write_config_payload(payload)
    
    def get_role_model_id(self, role: str) -> str:
        """Get the model ID assigned to a specific role from models.json."""
        try:
            from core.model_control_plane import model_control_plane

            return model_control_plane.get_role_model_id(role)
        except Exception:
            config = self.get_models_config()
            roles = config.get("roles", {})
            return roles.get(role) or roles.get("default", "")

    def get_agent_model_bindings(self) -> Dict[str, str]:
        config = self.get_models_config()
        bindings = ((config.get("bindings") or {}).get("agents") or {})
        normalized: Dict[str, str] = {}
        for agent_id, payload in bindings.items():
            if isinstance(payload, dict):
                model_id = str(payload.get("model_id") or payload.get("modelId") or "").strip()
            else:
                model_id = str(payload or "").strip()
            if model_id:
                normalized[str(agent_id)] = model_id
        return normalized

    def get_agent_model_binding(self, agent_id: str) -> str:
        return self.get_agent_model_bindings().get(agent_id, "")

    def set_agent_model_binding(self, agent_id: str, model_id: Optional[str]):
        config = self.get_models_config() or {}
        bindings = config.setdefault("bindings", {})
        agents = bindings.setdefault("agents", {})
        resolved_model_id = str(model_id or "").strip()
        if resolved_model_id:
            agents[agent_id] = {"model_id": resolved_model_id}
        else:
            agents.pop(agent_id, None)
        self.save_models_config(config)
    
    # Backward compat aliases
    def get_routes(self) -> Dict[str, Any]:
        return self.get_models_config()
    def save_routes(self, data: Dict[str, Any]):
        self.save_models_config(data)

    # --- Memory Config Accessors ---
    def get_memory_config(self) -> Dict[str, Any]:
        self._ensure_legacy_model_bindings_migrated()
        return self.read_json("memory_config.json")

    def get_memory_config_metadata(self) -> Dict[str, Any]:
        raw_config = self._read_raw_config_payload()
        raw_memory = raw_config.get("memory") if isinstance(raw_config, dict) else {}
        threshold_is_user_defined = isinstance(raw_memory, dict) and "retrieval_threshold" in raw_memory
        return {
            "recommendedRetrievalThreshold": MEMORY_RETRIEVAL_THRESHOLD_RECOMMENDED,
            "retrievalThresholdSource": "user" if threshold_is_user_defined else "engine_default",
            "retrievalThresholdIsDefault": not threshold_is_user_defined,
            "durablePolicyDefaults": deepcopy(MEMORY_DURABLE_POLICY_DEFAULTS),
            "durablePolicyPresets": deepcopy(MEMORY_DURABLE_POLICY_PRESETS),
            "recommendedDurablePolicyPreset": "balanced",
        }

    def ensure_memory_runtime_defaults(self) -> Dict[str, Any]:
        self._ensure_config_json_exists()
        raw_config = self._read_raw_config_payload()
        if not isinstance(raw_config, dict):
            raw_config = {}
        raw_memory = raw_config.get("memory")
        if not isinstance(raw_memory, dict):
            raw_memory = {}

        applied: Dict[str, Any] = {}
        if "retrieval_threshold" not in raw_memory:
            raw_memory["retrieval_threshold"] = MEMORY_RETRIEVAL_THRESHOLD_RECOMMENDED
            raw_config["memory"] = raw_memory
            self._write_config_payload(raw_config)
            applied["retrieval_threshold"] = MEMORY_RETRIEVAL_THRESHOLD_RECOMMENDED
        if self._looks_like_legacy_low_memory_durable_policy(raw_memory):
            for key, value in MEMORY_DURABLE_POLICY_DEFAULTS.items():
                raw_memory[key] = value
            raw_config["memory"] = raw_memory
            self._write_config_payload(raw_config)
            applied["durable_policy_preset"] = "balanced"
        return applied

    def _looks_like_legacy_low_memory_durable_policy(self, raw_memory: Dict[str, Any]) -> bool:
        if not isinstance(raw_memory, dict):
            return False
        for key, expected in LEGACY_LOW_MEMORY_DURABLE_POLICY.items():
            current = raw_memory.get(key)
            if isinstance(expected, float):
                try:
                    if abs(float(current) - expected) > 0.0001:
                        return False
                except (TypeError, ValueError):
                    return False
            else:
                try:
                    if int(current) != expected:
                        return False
                except (TypeError, ValueError):
                    return False
        return True
        
    def save_memory_config(self, data: Dict[str, Any]):
        self.write_json("memory_config.json", data)

    # --- Extensions Config Accessors ---
    def get_extensions_config(self) -> Dict[str, Any]:
        data = self._read_config_payload().get("extensions") or {}
        normalized = self._deep_merge(STRUCTURED_CONFIG_DEFAULTS["extensions"], data if isinstance(data, dict) else {})
        legacy_policy = dict(normalized.get("rerankPolicy") or {})
        prefilter_policy = dict(normalized.get("prefilterPolicy") or {})
        default_prefilter_policy = dict(STRUCTURED_CONFIG_DEFAULTS["extensions"].get("prefilterPolicy") or {})
        default_skills_policy = dict(default_prefilter_policy.get("skills") or {})
        default_mcp_policy = dict(default_prefilter_policy.get("mcp") or {})
        if legacy_policy and not prefilter_policy:
            prefilter_policy = {
                "enabled": bool(legacy_policy.get("enabled", False)),
                "mode": "two_stage",
            }
        elif legacy_policy:
            prefilter_policy.setdefault("enabled", bool(legacy_policy.get("enabled", False)))
            prefilter_policy.setdefault("mode", "two_stage")

        def _normalize_stage_policy(raw: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "stage1Enabled": bool(raw.get("stage1Enabled", defaults.get("stage1Enabled", True))),
                "stage1TopK": max(1, min(int(raw.get("stage1TopK", defaults.get("stage1TopK", 20)) or defaults.get("stage1TopK", 20)), 100)),
                "llmEnabled": bool(raw.get("llmEnabled", defaults.get("llmEnabled", True))),
                "stage2TopK": max(1, min(int(raw.get("stage2TopK", defaults.get("stage2TopK", 5)) or defaults.get("stage2TopK", 5)), 50)),
                "llmTimeoutSeconds": max(5, min(int(raw.get("llmTimeoutSeconds", defaults.get("llmTimeoutSeconds", 5)) or defaults.get("llmTimeoutSeconds", 5)), 10)),
            }

        normalized["prefilterPolicy"] = {
            "enabled": bool(prefilter_policy.get("enabled", False)),
            "mode": str(prefilter_policy.get("mode") or "two_stage").strip() or "two_stage",
            "skills": _normalize_stage_policy(dict(prefilter_policy.get("skills") or {}), default_skills_policy),
            "mcp": _normalize_stage_policy(dict(prefilter_policy.get("mcp") or {}), default_mcp_policy),
        }
        normalized.pop("rerankPolicy", None)
        return normalized

    def save_extensions_config(self, data: Dict[str, Any]):
        payload = self._read_config_payload()
        normalized = dict(data or {})
        legacy_policy = dict(normalized.pop("rerankPolicy", {}) or {})
        prefilter_policy = dict(normalized.pop("prefilterPolicy", {}) or {})
        if legacy_policy and not prefilter_policy:
            prefilter_policy = {
                "enabled": bool(legacy_policy.get("enabled", False)),
                "mode": "two_stage",
            }
        default_prefilter_policy = dict(STRUCTURED_CONFIG_DEFAULTS["extensions"].get("prefilterPolicy") or {})
        default_skills_policy = dict(default_prefilter_policy.get("skills") or {})
        default_mcp_policy = dict(default_prefilter_policy.get("mcp") or {})

        def _normalize_stage_policy(raw: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "stage1Enabled": bool(raw.get("stage1Enabled", defaults.get("stage1Enabled", True))),
                "stage1TopK": max(1, min(int(raw.get("stage1TopK", defaults.get("stage1TopK", 20)) or defaults.get("stage1TopK", 20)), 100)),
                "llmEnabled": bool(raw.get("llmEnabled", defaults.get("llmEnabled", True))),
                "stage2TopK": max(1, min(int(raw.get("stage2TopK", defaults.get("stage2TopK", 5)) or defaults.get("stage2TopK", 5)), 50)),
                "llmTimeoutSeconds": max(5, min(int(raw.get("llmTimeoutSeconds", defaults.get("llmTimeoutSeconds", 5)) or defaults.get("llmTimeoutSeconds", 5)), 10)),
            }

        next_extensions = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["extensions"],
            {
                **normalized,
                "prefilterPolicy": {
                    "enabled": bool(prefilter_policy.get("enabled", False)),
                    "mode": str(prefilter_policy.get("mode") or "two_stage").strip() or "two_stage",
                    "skills": _normalize_stage_policy(dict(prefilter_policy.get("skills") or {}), default_skills_policy),
                    "mcp": _normalize_stage_policy(dict(prefilter_policy.get("mcp") or {}), default_mcp_policy),
                },
            },
        )
        next_extensions.pop("rerankPolicy", None)
        payload["extensions"] = next_extensions
        self._write_config_payload(payload)

    # --- Engineering Runtime Config Accessors (legacy key: engineeringLane) ---
    def get_engineering_lane_config(self) -> Dict[str, Any]:
        data = self._read_config_payload().get("engineeringLane") or {}
        raw_data = data if isinstance(data, dict) else {}
        merged = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["engineeringLane"],
            raw_data,
        )
        if raw_data.get("contextPackBudget") in (None, 2400, "2400"):
            merged["contextPackBudget"] = STRUCTURED_CONFIG_DEFAULTS["engineeringLane"]["contextPackBudget"]
        if raw_data.get("evidenceGraphBudget") in (None, 1800, "1800"):
            merged["evidenceGraphBudget"] = STRUCTURED_CONFIG_DEFAULTS["engineeringLane"]["evidenceGraphBudget"]
        trigger_mode = str(merged.get("triggerMode") or "auto").strip().lower()
        merged["triggerMode"] = trigger_mode if trigger_mode in {"auto", "force", "off"} else "auto"
        proof_scope = str(merged.get("proofCollectionScope") or "engineering_active").strip().lower()
        merged["proofCollectionScope"] = proof_scope if proof_scope in {"engineering_active", "force_only", "off"} else "engineering_active"
        workset_mode = str(merged.get("worksetRiskMode") or "read_only").strip().lower()
        merged["worksetRiskMode"] = workset_mode if workset_mode in {"read_only", "soft_gate", "off"} else "read_only"
        governance_mode = str(merged.get("worksetGovernanceMode") or "observe_auto_block").strip().lower()
        merged["worksetGovernanceMode"] = governance_mode if governance_mode in {"observe_auto_block", "read_only", "soft_gate", "off"} else "observe_auto_block"
        worktree_placement = str(merged.get("worktreePlacement") or "same_volume").strip().lower()
        merged["worktreePlacement"] = worktree_placement if worktree_placement in {"same_volume", "custom"} else "same_volume"
        merged["worktreeRoot"] = str(merged.get("worktreeRoot") or "").strip()
        if merged["worktreePlacement"] != "custom":
            merged["worktreeRoot"] = ""
        providers = merged.get("diagnosticsProviders") if isinstance(merged.get("diagnosticsProviders"), dict) else {}
        default_providers = STRUCTURED_CONFIG_DEFAULTS["engineeringLane"]["diagnosticsProviders"]
        merged["diagnosticsProviders"] = {
            "git": bool(providers.get("git", default_providers["git"])),
            "command": bool(providers.get("command", default_providers["command"])),
            "lspBestEffort": bool(providers.get("lspBestEffort", default_providers["lspBestEffort"])),
        }
        for key, default, minimum, maximum in (
            ("contextPackBudget", 48000, 800, 128000),
            ("evidenceGraphBudget", 16000, 600, 48000),
            ("maxCriticalFiles", 24, 4, 120),
            ("rankedWorkflowPathCount", 3, 1, 5),
        ):
            try:
                merged[key] = max(minimum, min(int(merged.get(key) or default), maximum))
            except (TypeError, ValueError):
                merged[key] = default
        for key in ("enabled", "evidenceGraphEnabled", "codingExecutionContractEnabled", "proofLedgerEnabled", "autoProofCollectionEnabled", "suppressDailyMemory", "suppressMemoryMap", "worksetObservationEnabled", "workbenchDryRunMatrixEnabled"):
            merged[key] = bool(merged.get(key))
        return merged

    def save_engineering_lane_config(self, data: Dict[str, Any]):
        payload = self._read_config_payload()
        current = self.get_engineering_lane_config()
        raw_data = dict(data or {})
        next_data = self._deep_merge(current, raw_data)
        if "contextPackBudget" in raw_data and raw_data.get("contextPackBudget") in (None, 2400, "2400"):
            next_data["contextPackBudget"] = STRUCTURED_CONFIG_DEFAULTS["engineeringLane"]["contextPackBudget"]
        if "evidenceGraphBudget" in raw_data and raw_data.get("evidenceGraphBudget") in (None, 1800, "1800"):
            next_data["evidenceGraphBudget"] = STRUCTURED_CONFIG_DEFAULTS["engineeringLane"]["evidenceGraphBudget"]
        trigger_mode = str(next_data.get("triggerMode") or "auto").strip().lower()
        next_data["triggerMode"] = trigger_mode if trigger_mode in {"auto", "force", "off"} else "auto"
        proof_scope = str(next_data.get("proofCollectionScope") or "engineering_active").strip().lower()
        next_data["proofCollectionScope"] = proof_scope if proof_scope in {"engineering_active", "force_only", "off"} else "engineering_active"
        workset_mode = str(next_data.get("worksetRiskMode") or "read_only").strip().lower()
        next_data["worksetRiskMode"] = workset_mode if workset_mode in {"read_only", "soft_gate", "off"} else "read_only"
        governance_mode = str(next_data.get("worksetGovernanceMode") or "observe_auto_block").strip().lower()
        next_data["worksetGovernanceMode"] = governance_mode if governance_mode in {"observe_auto_block", "read_only", "soft_gate", "off"} else "observe_auto_block"
        worktree_placement = str(next_data.get("worktreePlacement") or "same_volume").strip().lower()
        next_data["worktreePlacement"] = worktree_placement if worktree_placement in {"same_volume", "custom"} else "same_volume"
        next_data["worktreeRoot"] = str(next_data.get("worktreeRoot") or "").strip()
        if next_data["worktreePlacement"] == "custom":
            custom_root = Path(next_data["worktreeRoot"]).expanduser()
            if not next_data["worktreeRoot"] or not custom_root.is_absolute():
                raise ValueError("Custom engineering worktree root must be an absolute path.")
        else:
            next_data["worktreeRoot"] = ""
        providers = next_data.get("diagnosticsProviders") if isinstance(next_data.get("diagnosticsProviders"), dict) else {}
        default_providers = STRUCTURED_CONFIG_DEFAULTS["engineeringLane"]["diagnosticsProviders"]
        next_data["diagnosticsProviders"] = {
            "git": bool(providers.get("git", default_providers["git"])),
            "command": bool(providers.get("command", default_providers["command"])),
            "lspBestEffort": bool(providers.get("lspBestEffort", default_providers["lspBestEffort"])),
        }
        for key, default, minimum, maximum in (
            ("contextPackBudget", 48000, 800, 128000),
            ("evidenceGraphBudget", 16000, 600, 48000),
            ("maxCriticalFiles", 24, 4, 120),
            ("rankedWorkflowPathCount", 3, 1, 5),
        ):
            try:
                next_data[key] = max(minimum, min(int(next_data.get(key) or default), maximum))
            except (TypeError, ValueError):
                next_data[key] = default
        for key in ("enabled", "evidenceGraphEnabled", "codingExecutionContractEnabled", "proofLedgerEnabled", "autoProofCollectionEnabled", "suppressDailyMemory", "suppressMemoryMap", "worksetObservationEnabled", "workbenchDryRunMatrixEnabled"):
            next_data[key] = bool(next_data.get(key))
        payload["engineeringLane"] = self._deep_merge(STRUCTURED_CONFIG_DEFAULTS["engineeringLane"], next_data)
        self._write_config_payload(payload)

    # --- Storage Retention Config Accessors ---
    def _normalize_storage_retention_config(self, data: Dict[str, Any] | None) -> Dict[str, Any]:
        source = dict(data or {}) if isinstance(data, dict) else {}
        merged = self._deep_merge(STRUCTURED_CONFIG_DEFAULTS["storageRetention"], source)
        raw: Dict[str, Any] = {
            "version": 2,
            "enabled": bool(merged.get("enabled", True)),
            "policy": "disk_watermark",
            "protectUserVisibleTranscript": bool(merged.get("protectUserVisibleTranscript", True)),
        }
        default_watermarks = dict(STRUCTURED_CONFIG_DEFAULTS["storageRetention"].get("diskWatermarks") or {})
        watermarks = self._deep_merge(
            default_watermarks,
            merged.get("diskWatermarks") if isinstance(merged.get("diskWatermarks"), dict) else {},
        )

        def _ratio(name: str, default: float) -> float:
            try:
                return max(0.001, min(0.50, float(watermarks.get(name) or default)))
            except (TypeError, ValueError):
                return default

        warning_ratio = _ratio("warningRatio", 0.15)
        critical_ratio = min(warning_ratio, _ratio("criticalRatio", 0.10))
        emergency_ratio = min(critical_ratio, _ratio("emergencyRatio", 0.05))
        try:
            emergency_free_bytes = int(watermarks.get("emergencyFreeBytes") or 2 * 1024 * 1024 * 1024)
        except (TypeError, ValueError):
            emergency_free_bytes = 2 * 1024 * 1024 * 1024
        raw["diskWatermarks"] = {
            "warningRatio": warning_ratio,
            "criticalRatio": critical_ratio,
            "emergencyRatio": emergency_ratio,
            "emergencyFreeBytes": max(512 * 1024 * 1024, emergency_free_bytes),
        }
        budgets = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["storageRetention"].get("budgets") or {},
            merged.get("budgets") if isinstance(merged.get("budgets"), dict) else {},
        )
        normalized_budgets: Dict[str, Any] = {}
        minimum_budget_bytes = {
            "logs": 1 * 1024 * 1024 * 1024,
            "checkpoints": 1 * 1024 * 1024 * 1024,
        }
        for key, default_budget in (STRUCTURED_CONFIG_DEFAULTS["storageRetention"].get("budgets") or {}).items():
            budget = dict(budgets.get(key) if isinstance(budgets.get(key), dict) else {})
            try:
                budget_max = int(budget.get("maxBytes") or default_budget.get("maxBytes") or 0)
            except (TypeError, ValueError):
                budget_max = int(default_budget.get("maxBytes") or 0)
            budget["maxBytes"] = max(minimum_budget_bytes.get(key, 1 * 1024 * 1024), budget_max)
            if "retentionDays" in default_budget or "retentionDays" in budget:
                try:
                    retention_days = int(budget.get("retentionDays") or default_budget.get("retentionDays") or 0)
                except (TypeError, ValueError):
                    retention_days = int(default_budget.get("retentionDays") or 0)
                budget["retentionDays"] = max(0, retention_days)
            budget["mode"] = str(budget.get("mode") or default_budget.get("mode") or "warn_only").strip() or "warn_only"
            if key == "checkpoints":
                budget["mode"] = "elastic"
            elif key in {"artifacts", "knowledgeTruth", "memoryAuxiliary"}:
                budget["mode"] = "manual_prune" if key == "artifacts" else "warn_only"
            normalized_budgets[key] = budget
        raw["budgets"] = normalized_budgets
        return raw

    def get_storage_retention_config(self) -> Dict[str, Any]:
        payload = self._read_config_payload()
        return self._normalize_storage_retention_config(payload.get("storageRetention"))

    def save_storage_retention_config(self, data: Dict[str, Any]):
        payload = self._read_config_payload()
        current = self.get_storage_retention_config()
        payload["storageRetention"] = self._normalize_storage_retention_config(self._deep_merge(current, dict(data or {})))
        self._write_config_payload(payload)
        
    # --- Supervisor Config Accessors ---
    def _normalize_specialist_registry_config(self, data: Dict[str, Any] | None) -> Dict[str, Any]:
        raw = dict(data or {})

        def _as_bool(value: Any, default: bool) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off"}:
                    return False
            if value is None:
                return default
            return bool(value)

        raw["familyModeEnabled"] = _as_bool(raw.get("familyModeEnabled"), True)
        try:
            max_members = int(raw.get("maxMembersPerFamily") or 10)
        except (TypeError, ValueError):
            max_members = 10
        raw["maxMembersPerFamily"] = max(1, min(max_members, 50))
        exposure_mode = str(raw.get("exposureMode") or "family_cards").strip().lower()
        if exposure_mode not in {"family_cards", "legacy_matched_members"}:
            exposure_mode = "family_cards"
        raw["exposureMode"] = exposure_mode
        auto_reveal_raw = raw.get("autoReveal") if isinstance(raw.get("autoReveal"), dict) else {}
        try:
            min_confidence = float(auto_reveal_raw.get("minConfidence", 0.9))
        except (TypeError, ValueError):
            min_confidence = 0.9
        try:
            min_margin = float(auto_reveal_raw.get("minScoreMargin", 0.15))
        except (TypeError, ValueError):
            min_margin = 0.15
        try:
            max_families = int(auto_reveal_raw.get("maxFamilies", 1))
        except (TypeError, ValueError):
            max_families = 1
        raw["autoReveal"] = {
            "enabled": _as_bool(auto_reveal_raw.get("enabled"), True),
            "minConfidence": max(0.0, min(min_confidence, 1.0)),
            "minScoreMargin": max(0.0, min(min_margin, 1.0)),
            "maxFamilies": max(0, min(max_families, 3)),
            "requireNoAmbiguity": _as_bool(auto_reveal_raw.get("requireNoAmbiguity"), True),
        }
        raw["families"] = normalize_specialist_families_config(raw.get("families"))
        return raw

    def _normalize_research_config(self, data: Dict[str, Any] | None) -> Dict[str, Any]:
        defaults = dict((STRUCTURED_CONFIG_DEFAULTS["supervisor"] or {}).get("research") or {})
        raw = self._deep_merge(defaults, data if isinstance(data, dict) else {})

        def _as_bool(value: Any, default: bool) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off"}:
                    return False
            if value is None:
                return default
            return bool(value)

        def _as_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        raw["enabled"] = _as_bool(raw.get("enabled"), True)
        raw["defaultShardCount"] = max(1, min(_as_int(raw.get("defaultShardCount"), 10), 30))
        raw["maxShardCount"] = max(raw["defaultShardCount"], min(_as_int(raw.get("maxShardCount"), 30), 30))
        raw["maxRounds"] = max(1, min(_as_int(raw.get("maxRounds"), 5), 5))
        raw["evidenceTtlSeconds"] = max(60, min(_as_int(raw.get("evidenceTtlSeconds"), 21600), 7 * 24 * 60 * 60))
        raw["architectAgentSynthesisEnabled"] = _as_bool(raw.get("architectAgentSynthesisEnabled"), True)
        raw["architectAgentTimeoutSeconds"] = max(5, min(_as_int(raw.get("architectAgentTimeoutSeconds"), 60), 90))
        return raw

    def get_supervisor_config(self) -> Dict[str, Any]:
        self._ensure_legacy_model_bindings_migrated()
        config = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["supervisor"],
            self._read_config_payload().get("supervisor") if isinstance(self._read_config_payload().get("supervisor"), dict) else {},
        )
        should_save = False
        sanitized_allowed_tools = sanitize_supervisor_allowed_tools(config.get("allowed_tools"))
        if sanitized_allowed_tools != config.get("allowed_tools"):
            config["allowed_tools"] = sanitized_allowed_tools
            should_save = True
        delegation = dict(config.get("delegation") or {})
        normalized_external_workers = normalize_external_worker_descriptors(delegation.get("externalWorkers"))
        if normalized_external_workers != delegation.get("externalWorkers"):
            delegation["externalWorkers"] = normalized_external_workers
            config["delegation"] = delegation
            should_save = True
        specialist_registry = self._normalize_specialist_registry_config(config.get("specialistRegistry"))
        if specialist_registry != config.get("specialistRegistry"):
            config["specialistRegistry"] = specialist_registry
            should_save = True
        research_config = self._normalize_research_config(config.get("research"))
        if research_config != config.get("research"):
            config["research"] = research_config
            should_save = True
        if should_save:
            self.save_supervisor_config(config)
        return config
        
    def save_supervisor_config(self, data: Dict[str, Any]):
        next_payload = self._deep_merge(STRUCTURED_CONFIG_DEFAULTS["supervisor"], dict(data or {}))
        next_payload["allowed_tools"] = sanitize_supervisor_allowed_tools(next_payload.get("allowed_tools"))
        delegation = dict(next_payload.get("delegation") or {})
        delegation["externalWorkers"] = normalize_external_worker_descriptors(delegation.get("externalWorkers"))
        next_payload["delegation"] = delegation
        next_payload["specialistRegistry"] = self._normalize_specialist_registry_config(next_payload.get("specialistRegistry"))
        next_payload["research"] = self._normalize_research_config(next_payload.get("research"))
        payload = self._read_config_payload()
        payload["supervisor"] = next_payload
        self._write_config_payload(payload)

    def get_supervisor_profile(self) -> Dict[str, str]:
        supervisor_config = self.get_supervisor_config() or {}
        profile = dict(supervisor_config.get("profile") or {})
        bridge = (self.get_system_base_config() or {}).get("bridge") or {}
        default_avatar = _default_supervisor_avatar_url(bridge.get("adminBaseUrl"))
        return {
            "name": str(profile.get("name") or "智能主管"),
            "roleLabel": str(profile.get("roleLabel") or "主理人"),
            "avatar": str(profile.get("avatar") or default_avatar),
        }

    def save_supervisor_profile(self, profile: Dict[str, Any]):
        supervisor_config = self.get_supervisor_config() or {}
        bridge = (self.get_system_base_config() or {}).get("bridge") or {}
        current = self.get_supervisor_profile()
        next_profile = {
            "name": str(profile.get("name") or current.get("name") or "智能主管"),
            "roleLabel": str(profile.get("roleLabel") or current.get("roleLabel") or "主理人"),
            "avatar": str(profile.get("avatar") or current.get("avatar") or _default_supervisor_avatar_url(bridge.get("adminBaseUrl"))),
        }
        supervisor_config["profile"] = next_profile
        self.save_supervisor_config(supervisor_config)

    def get_agent_runtime_profile(self, agent_id: str) -> Dict[str, str]:
        if agent_id == "supervisor":
            return self.get_supervisor_profile()

        default_name = agent_id
        default_role = "Specialist Agent"
        default_avatar = f"https://api.dicebear.com/9.x/bottts-neutral/svg?seed={agent_id}"
        custom_agent = self.get_agent(agent_id)
        if custom_agent:
            return {
                "name": custom_agent.get("name") or default_name,
                "roleLabel": custom_agent.get("description") or default_role,
                "avatar": custom_agent.get("avatar") or default_avatar,
            }
        return {
            "name": default_name,
            "roleLabel": default_role,
            "avatar": default_avatar,
        }
        
    # --- Workspace Config Accessors ---
    def get_workspace_config(self) -> Dict[str, Any]:
        return self.read_json("workspace_config.json")
        
    def save_workspace_config(self, data: Dict[str, Any]):
        self.write_json("workspace_config.json", data)

    def get_system_base_config(self) -> Dict[str, Any]:
        data = self._read_config_payload().get("systemBase") or {}
        normalized = self._deep_merge(self._default_system_base_config(), data if isinstance(data, dict) else {})
        normalized["identity"] = normalize_system_identity(normalized.get("identity"))
        bridge = normalized.setdefault("bridge", {})
        bridge["engineBaseUrl"] = _normalize_http_base_url(bridge.get("engineBaseUrl"), "http://127.0.0.1:9530/v1")
        bridge["engineWsBaseUrl"] = _normalize_http_base_url(
            bridge.get("engineWsBaseUrl"),
            _derive_ws_base_url(bridge["engineBaseUrl"]),
        )
        bridge["adminBaseUrl"] = _normalize_http_base_url(bridge.get("adminBaseUrl"), "http://127.0.0.1:9528/api")
        bridge["internalSecret"] = str(bridge.get("internalSecret") or uuid4().hex)
        bridge["allowedOrigins"] = _normalize_allowed_origins(bridge.get("allowedOrigins"))
        normalized.pop("skills", None)
        normalized.setdefault("webFetch", {}).setdefault("cacheDir", str(self.base_dir / "web_fetch"))
        normalized["webFetch"].setdefault(
            "adaptiveStorageFile",
            str(Path(normalized["webFetch"]["cacheDir"]) / "adaptive" / "global.db"),
        )
        normalized["webFetch"].setdefault("useAgentBrowserProfile", False)
        normalized["webFetch"].setdefault("agentBrowserProfileAllowlist", [])
        normalized["webFetch"].setdefault("sourceRouter", get_source_router_defaults())
        normalized["webFetch"].setdefault("providers", {})
        for provider, provider_defaults in get_source_provider_config_defaults().items():
            normalized["webFetch"]["providers"].setdefault(provider, provider_defaults)
        normalized.setdefault("desktopTools", {})
        normalized.pop("channels", None)
        normalized.setdefault("desktopLive", {})
        desktop_live = normalized["desktopLive"]
        desktop_live["enabled"] = bool(desktop_live.get("enabled", True))
        desktop_live["maxWidth"] = max(320, int(desktop_live.get("maxWidth") or 960))
        desktop_live["maxHeight"] = max(180, int(desktop_live.get("maxHeight") or 540))
        desktop_live["targetFps"] = max(1, min(15, int(desktop_live.get("targetFps") or 10)))
        desktop_live["singleViewerOnly"] = bool(desktop_live.get("singleViewerOnly", True))
        desktop_live["idleReleaseSeconds"] = max(5, int(desktop_live.get("idleReleaseSeconds") or 15))
        desktop_live["audioEnabled"] = bool(desktop_live.get("audioEnabled", True))
        desktop_live["audioSource"] = str(desktop_live.get("audioSource") or "system").strip().lower()
        desktop_live["audioSampleRate"] = max(8000, min(96000, int(desktop_live.get("audioSampleRate") or 48000)))
        desktop_live["audioChannels"] = 1 if int(desktop_live.get("audioChannels") or 2) == 1 else 2
        if not isinstance(desktop_live.get("iceServers"), list):
            desktop_live["iceServers"] = []
        capture_display = str(desktop_live.get("captureDisplay") or "primary").strip().lower()
        desktop_live["captureDisplay"] = capture_display if capture_display in {"primary"} else "primary"
        normalized.setdefault("remoteLink", {})
        remote_link = normalized["remoteLink"]
        if not isinstance(remote_link, dict):
            remote_link = {}
        remote_link.setdefault("enabled", True)
        remote_link.setdefault("activeProfileId", "manual-local")
        if not isinstance(remote_link.get("transportProfiles"), list):
            remote_link["transportProfiles"] = []
        if not isinstance(remote_link.get("meshProviders"), list):
            remote_link["meshProviders"] = []
        normalized["remoteLink"] = remote_link
        normalized.setdefault("s3", {})
        legacy_settings = normalized.get("legacySettings")
        if not isinstance(legacy_settings, list):
            legacy_settings = []
        filtered_legacy_settings: list[dict[str, Any]] = []
        for item in legacy_settings:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            value = item.get("value")
            if key == "S3_CONFIG" and isinstance(value, dict):
                normalized["s3"] = self._deep_merge(normalized.get("s3") or {}, value)
                continue
            filtered_legacy_settings.append(dict(item))
        normalized["legacySettings"] = filtered_legacy_settings
        if normalized != data:
            payload = self._read_config_payload()
            payload["systemBase"] = normalized
            self._write_config_payload(payload)
        return normalized

    def save_system_base_config(self, data: Dict[str, Any]):
        payload = self._read_config_payload()
        merged = self._deep_merge(self.get_system_base_config(), dict(data or {}))
        merged.pop("channels", None)
        payload["systemBase"] = merged
        self._write_config_payload(payload)

    def get_system_settings(self) -> Dict[str, Any]:
        return self._system_base_to_legacy_settings(self.get_system_base_config())

    def save_system_settings(self, data: Dict[str, Any]):
        self.save_system_base_config(self._legacy_settings_to_system_base(dict(data or {})))

    # --- Hooks Config Accessors ---
    def get_hooks_config(self) -> Dict[str, Any]:
        return self.read_json("hooks_config.json")
        
    def save_hooks_config(self, data: Dict[str, Any]):
        self.write_json("hooks_config.json", data)

    # --- Cron Config Accessors ---
    def get_cron_config(self) -> Dict[str, Any]:
        data = self.read_json("cron_config.json")
        normalized = normalize_cron_config_with_system_job(data)
        if normalized != data:
            self.write_json("cron_config.json", normalized)
        return normalized
        
    def save_cron_config(self, data: Dict[str, Any]):
        self.write_json("cron_config.json", normalize_cron_config_with_system_job(data))

    # --- Automation Runtime Config Accessors ---
    def get_automation_runtime_config(self) -> Dict[str, Any]:
        data = self._read_config_payload().get("automationRuntime") or {}
        return self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["automationRuntime"],
            data if isinstance(data, dict) else {},
        )

    def save_automation_runtime_config(self, data: Dict[str, Any]):
        payload = self._read_config_payload()
        payload["automationRuntime"] = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["automationRuntime"],
            dict(data or {}),
        )
        self._write_config_payload(payload)

    # --- Network Supervisor Runtime Config Accessors ---
    def get_network_supervisor_runtime_config(self) -> Dict[str, Any]:
        data = self._read_config_payload().get("networkSupervisorRuntime") or {}
        return self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["networkSupervisorRuntime"],
            data if isinstance(data, dict) else {},
        )

    def save_network_supervisor_runtime_config(self, data: Dict[str, Any]):
        payload = self._read_config_payload()
        payload["networkSupervisorRuntime"] = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["networkSupervisorRuntime"],
            dict(data or {}),
        )
        self._write_config_payload(payload)

    # --- Context Config Accessors ---
    def get_context_config(self) -> Dict[str, Any]:
        self._ensure_legacy_model_bindings_migrated()
        raw = self.read_json("context_config.json")
        # Runtime reads must remain side-effect free. Historical or unknown
        # adapter fields are tolerated in storage and are only removed when a
        # user explicitly saves the supported context policy.
        return normalize_context_policy(raw)
        
    def save_context_config(self, data: Dict[str, Any]):
        self.write_json("context_config.json", normalize_context_policy(data))

    # --- Computer Use Runtime Config Accessors ---
    def get_computer_use_config(self) -> Dict[str, Any]:
        data = self._read_config_payload().get("computerUse") or {}
        return self._deep_merge(STRUCTURED_CONFIG_DEFAULTS["computerUse"], data if isinstance(data, dict) else {})

    def save_computer_use_config(self, data: Dict[str, Any]):
        payload = self._read_config_payload()
        payload["computerUse"] = self._deep_merge(STRUCTURED_CONFIG_DEFAULTS["computerUse"], dict(data or {}))
        self._write_config_payload(payload)

    # --- Plugin Manager Runtime Config Accessors ---
    def get_plugin_manager_config(self) -> Dict[str, Any]:
        data = self._read_config_payload().get("pluginManager") or {}
        normalized = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["pluginManager"],
            data if isinstance(data, dict) else {},
        )
        normalized["enabled"] = bool(normalized.get("enabled", True))
        normalized["refreshOnStartup"] = bool(normalized.get("refreshOnStartup", True))
        normalized["allowSessionGrant"] = bool(normalized.get("allowSessionGrant", True))
        normalized["requireExplicitMention"] = True
        normalized["defaultGrantScope"] = "task"
        return normalized

    def save_plugin_manager_config(self, data: Dict[str, Any]):
        payload = self._read_config_payload()
        merged = self._deep_merge(STRUCTURED_CONFIG_DEFAULTS["pluginManager"], dict(data or {}))
        merged["requireExplicitMention"] = True
        merged["defaultGrantScope"] = "task"
        payload["pluginManager"] = merged
        self._write_config_payload(payload)

    # --- Computer Use Memory Accessors ---
    def get_computer_use_memory(self) -> Dict[str, Any]:
        data = self.read_json("computer_use.json")
        if not data:
            data = {"version": 1, "apps": {}}
        data.setdefault("version", 1)
        data.setdefault("apps", {})
        return data

    def save_computer_use_memory(self, data: Dict[str, Any]):
        payload = dict(data or {})
        payload.setdefault("version", 1)
        payload.setdefault("apps", {})
        self.write_json("computer_use.json", payload)

    # --- Web Fetch Profile Accessors ---
    def get_web_fetch_profiles(self) -> Dict[str, Any]:
        data = self.read_json("web_fetch_profiles.json")
        if not data:
            data = {"version": 1, "sites": {}}
        data.setdefault("version", 1)
        data.setdefault("sites", {})
        return data

    def save_web_fetch_profiles(self, data: Dict[str, Any]):
        payload = dict(data or {})
        payload.setdefault("version", 1)
        payload.setdefault("sites", {})
        self.write_json("web_fetch_profiles.json", payload)

    # --- Runtime Registry Config Accessors ---
    def get_runtime_registry_config(self) -> Dict[str, Any]:
        data = self.read_json("runtime_registry.json")
        if not data:
            data = {
                "version": 1,
                "installProfile": "minimal",
                "installPlatform": "",
                "installedRuntimeFamilies": [],
                "featurePacks": {},
                "bootstrapManaged": False,
                "lastUpgradeAt": "",
                "startupProfile": "minimal",
                "policies": {},
            }
        data.setdefault("version", 1)
        install_profile = str(data.get("installProfile") or data.get("startupProfile") or "minimal").strip().lower()
        if install_profile == "standard":
            install_profile = "minimal"
        if install_profile not in {"minimal", "desktop"}:
            install_profile = "minimal"
        data["installProfile"] = install_profile
        data["startupProfile"] = install_profile
        install_platform = str(data.get("installPlatform") or "").strip().lower()
        if install_platform not in {"windows", "macos", "linux"}:
            install_platform = ""
        data["installPlatform"] = install_platform
        families = []
        for item in list(data.get("installedRuntimeFamilies") or []):
            normalized = str(item or "").strip()
            if normalized and normalized not in families:
                families.append(normalized)
        data["installedRuntimeFamilies"] = families
        from core.runtime.feature_packs import normalize_feature_pack_config

        data["featurePacks"] = normalize_feature_pack_config(data.get("featurePacks"))
        data["bootstrapManaged"] = bool(data.get("bootstrapManaged", False))
        data["lastUpgradeAt"] = str(data.get("lastUpgradeAt") or "").strip()
        data.setdefault("policies", {})
        return data

    def save_runtime_registry_config(self, data: Dict[str, Any]):
        payload = dict(data or {})
        payload.setdefault("version", 1)
        install_profile = str(payload.get("installProfile") or payload.get("startupProfile") or "minimal").strip().lower()
        if install_profile == "standard":
            install_profile = "minimal"
        if install_profile not in {"minimal", "desktop"}:
            install_profile = "minimal"
        payload["installProfile"] = install_profile
        payload["startupProfile"] = install_profile
        install_platform = str(payload.get("installPlatform") or "").strip().lower()
        if install_platform not in {"windows", "macos", "linux"}:
            install_platform = ""
        payload["installPlatform"] = install_platform
        families = []
        for item in list(payload.get("installedRuntimeFamilies") or []):
            normalized = str(item or "").strip()
            if normalized and normalized not in families:
                families.append(normalized)
        payload["installedRuntimeFamilies"] = families
        from core.runtime.feature_packs import normalize_feature_pack_config

        payload["featurePacks"] = normalize_feature_pack_config(payload.get("featurePacks"))
        payload["bootstrapManaged"] = bool(payload.get("bootstrapManaged", False))
        payload["lastUpgradeAt"] = str(payload.get("lastUpgradeAt") or "").strip()
        payload.setdefault("policies", {})
        self.write_json("runtime_registry.json", payload)

    # --- Runtime Stability Config Accessors ---
    def get_runtime_stability_config(self) -> Dict[str, Any]:
        data = self.read_json("runtime_stability.json")
        if not data:
            data = {
                "version": 1,
                "strictSupervisorDurability": True,
                "sessionLanePolicy": "queue",
            }
        data.setdefault("version", 1)
        data.setdefault("strictSupervisorDurability", True)
        data.setdefault("sessionLanePolicy", "queue")
        return data

    def save_runtime_stability_config(self, data: Dict[str, Any]):
        payload = dict(data or {})
        payload.setdefault("version", 1)
        payload.setdefault("strictSupervisorDurability", True)
        payload.setdefault("sessionLanePolicy", "queue")
        self.write_json("runtime_stability.json", payload)

    # --- Safety Guardian Config Accessors ---
    def _normalize_safety_guardian_config(self, data: Dict[str, Any] | None) -> Dict[str, Any]:
        payload = deepcopy(dict(data or {}))
        runtime_rules = dict(payload.get("runtimeRules") or {})
        if runtime_rules:
            payload["runtimeRules"] = runtime_rules
        elif "runtimeRules" in payload:
            payload["runtimeRules"] = {}
        return payload

    def get_safety_guardian_config(self) -> Dict[str, Any]:
        payload = self._read_config_payload()
        structured = payload.get("safety") if isinstance(payload.get("safety"), dict) else {}
        legacy = self.read_json("safety_guardian.json")
        merged = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["safety"],
            legacy if isinstance(legacy, dict) else {},
        )
        merged = self._deep_merge(
            merged,
            structured if isinstance(structured, dict) else {},
        )
        normalized = self._normalize_safety_guardian_config(merged)
        if normalized != structured:
            payload["safety"] = self._deep_merge(STRUCTURED_CONFIG_DEFAULTS["safety"], normalized)
            self._write_config_payload(payload)
        if isinstance(legacy, dict) and normalized != legacy:
            self.write_json("safety_guardian.json", normalized)
        return normalized

    def save_safety_guardian_config(self, data: Dict[str, Any]):
        normalized = self._normalize_safety_guardian_config(data)
        payload = self._read_config_payload()
        payload["safety"] = self._deep_merge(STRUCTURED_CONFIG_DEFAULTS["safety"], normalized)
        self._write_config_payload(payload)
        self.write_json("safety_guardian.json", normalized)

    def get_desktop_pet_config(self) -> Dict[str, Any]:
        data = self._read_config_payload().get("desktopPet") or {}
        return self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["desktopPet"],
            data if isinstance(data, dict) else {},
        )

    def save_desktop_pet_config(self, data: Dict[str, Any]):
        payload = self._read_config_payload()
        payload["desktopPet"] = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["desktopPet"],
            dict(data or {}),
        )
        self._write_config_payload(payload)

    def get_music_config(self) -> Dict[str, Any]:
        data = self._read_config_payload().get("music") or {}
        normalized = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["music"],
            data if isinstance(data, dict) else {},
        )
        normalized.setdefault("tracks", [])
        return normalized

    def save_music_config(self, data: Dict[str, Any]):
        payload = self._read_config_payload()
        payload["music"] = self._deep_merge(
            STRUCTURED_CONFIG_DEFAULTS["music"],
            dict(data or {}),
        )
        payload["music"].setdefault("tracks", [])
        self._write_config_payload(payload)

    def get_ui_config(self) -> Dict[str, Any]:
        data = self._read_config_payload().get("ui") or {}
        theme = str((data if isinstance(data, dict) else {}).get("theme") or "system").strip().lower()
        if theme not in {"light", "dark", "system"}:
            theme = "system"
        return {"theme": theme}

    def save_ui_config(self, data: Dict[str, Any]):
        theme = str((data or {}).get("theme") or "system").strip().lower()
        if theme not in {"light", "dark", "system"}:
            theme = "system"
        payload = self._read_config_payload()
        payload["ui"] = {"theme": theme}
        self._write_config_payload(payload)

    # --- Project Registry Accessors ---
    def get_projects_registry(self) -> Dict[str, Any]:
        data = self.read_json("projects.json")
        if not data:
            data = {"version": 2, "defaultProjectId": None, "projects": [], "workspacePresentations": []}
        data.setdefault("version", 2)
        data.setdefault("defaultProjectId", None)
        data.setdefault("projects", [])
        data.setdefault("workspacePresentations", [])
        return data

    def save_projects_registry(self, data: Dict[str, Any]):
        payload = {
            "version": 2,
            "defaultProjectId": data.get("defaultProjectId"),
            "projects": data.get("projects", []),
            "workspacePresentations": data.get("workspacePresentations", []),
        }
        self.write_json("projects.json", payload)

    # --- Agent Accessors ---
    def get_all_agents(self) -> List[Dict[str, Any]]:
        from core.runtime.agents import parse_agent_md
        self._ensure_legacy_model_bindings_migrated()
        agents_dir = self.base_dir / "agents"
        agents = []
        model_bindings = self.get_agent_model_bindings()
        for file_path in agents_dir.glob("*.md"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                agent_config = parse_agent_md(content, file_path.name)
                agent_payload = agent_config.model_dump()
                bound_model_id = model_bindings.get(agent_payload["id"])
                agent_payload["model"] = bound_model_id or ""
                agents.append(agent_payload)
            except Exception as e:
                print(f"Error reading agent file {file_path}: {e}")
        return agents

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        from core.runtime.agents import parse_agent_md
        self._ensure_legacy_model_bindings_migrated()
        agent_path = self.base_dir / "agents" / f"{agent_id}.md"
        if not agent_path.exists():
            return None
        try:
            with open(agent_path, "r", encoding="utf-8") as f:
                content = f.read()
            payload = parse_agent_md(content, f"{agent_id}.md").model_dump()
            bound_model_id = self.get_agent_model_binding(agent_id)
            payload["model"] = bound_model_id or ""
            return payload
        except Exception as e:
            print(f"Error reading agent file {agent_path}: {e}")
            return None

    def save_agent(self, agent_config_dict: Dict[str, Any]):
        from core.runtime.agents import dump_agent_md, AgentConfig
        payload = dict(agent_config_dict or {})
        snapshot = payload.get("capabilitySnapshot") if isinstance(payload.get("capabilitySnapshot"), dict) else {}
        payload["capabilitySnapshot"] = ensure_specialist_family(snapshot)
        payload.setdefault("globalExposure", False)
        agent_config = AgentConfig(**payload)
        agent_path = self.base_dir / "agents" / f"{agent_config.id}.md"
        md_content = dump_agent_md(agent_config)
        with open(agent_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        self.set_agent_model_binding(agent_config.id, agent_config.model)

    def delete_agent(self, agent_id: str) -> bool:
        agent_path = self.base_dir / "agents" / f"{agent_id}.md"
        if agent_path.exists():
            agent_path.unlink()
            self.set_agent_model_binding(agent_id, "")
            return True
        return False

    # --- Todos Accessors ---
    def _todos_root(self) -> Path:
        root = self.base_dir / "todos"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _todo_tasks_root(self) -> Path:
        root = self._todos_root() / "tasks"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _todo_sessions_root(self) -> Path:
        root = self._todos_root() / "sessions"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _todo_task_name_slug(self, task_name: str) -> str:
        normalized = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(task_name or "").strip())
        normalized = "-".join(part for part in normalized.split("-") if part)
        return normalized or "unnamed-task"

    def _todo_task_snapshot_payload(self, task_info: Dict[str, Any], resolved_todos: List[Dict[str, Any]]) -> Dict[str, Any]:
        snapshot = {
            "taskId": str(task_info.get("taskId") or "").strip(),
            "taskName": str(task_info.get("name") or "").strip(),
            "planMarkdown": str(task_info.get("plan") or ""),
            "runId": task_info.get("runId"),
            "sessionId": task_info.get("sessionId"),
            "createdAt": task_info.get("createdAt"),
            "updatedAt": task_info.get("updatedAt"),
            "isActive": bool(task_info.get("isActive", False)),
            "isStale": bool(task_info.get("isStale", False)),
            "items": [dict(item) for item in list(resolved_todos or [])],
        }
        snapshot["allCompleted"] = bool(snapshot["items"]) and all(
            str(item.get("status") or "") in ("done", "skipped") for item in snapshot["items"]
        )
        return snapshot

    def save_active_todos(self, task_info: Dict[str, Any], resolved_todos: List[Dict[str, Any]]):
        """Persist the latest task snapshot using taskId as the canonical key."""
        task_id = str((task_info or {}).get("taskId") or "").strip()
        task_name = str((task_info or {}).get("name") or "").strip()
        if not task_id:
            return

        snapshot = self._todo_task_snapshot_payload(task_info, resolved_todos)
        tasks_root = self._todo_tasks_root()
        sessions_root = self._todo_sessions_root()
        snapshot_path = tasks_root / f"{task_id}.json"
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)

        session_id = str(snapshot.get("sessionId") or "").strip()
        if session_id:
            session_index_path = sessions_root / f"{session_id}.json"
            previous_active_task_id: str | None = None
            if session_index_path.exists():
                try:
                    previous_index = json.loads(session_index_path.read_text(encoding="utf-8"))
                    previous_active_task_id = str(previous_index.get("activeTaskId") or "").strip() or None
                except Exception:
                    previous_active_task_id = None
            if previous_active_task_id and previous_active_task_id != task_id:
                previous_task_path = tasks_root / f"{previous_active_task_id}.json"
                if previous_task_path.exists():
                    try:
                        previous_snapshot = json.loads(previous_task_path.read_text(encoding="utf-8"))
                        if bool(previous_snapshot.get("isActive", False)):
                            previous_snapshot["isActive"] = False
                            with open(previous_task_path, "w", encoding="utf-8") as f:
                                json.dump(previous_snapshot, f, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
            index_payload = {
                "sessionId": session_id,
                "activeTaskId": task_id if snapshot.get("isActive") else None,
                "latestTaskId": task_id,
                "updatedAt": snapshot.get("updatedAt") or snapshot.get("createdAt") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            with open(session_index_path, "w", encoding="utf-8") as f:
                json.dump(index_payload, f, indent=2, ensure_ascii=False)

        if task_name:
            task_dir = self._todos_root() / self._todo_task_name_slug(task_name)
            task_dir.mkdir(parents=True, exist_ok=True)
            if snapshot.get("planMarkdown"):
                plan_path = task_dir / "计划文档.md"
                with open(plan_path, "w", encoding="utf-8") as f:
                    f.write(str(snapshot["planMarkdown"]))

            compat_todos_path = task_dir / "todos.json"
            with open(compat_todos_path, "w", encoding="utf-8") as f:
                json.dump(snapshot["items"], f, indent=2, ensure_ascii=False)

            compat_meta_path = task_dir / "task.json"
            with open(compat_meta_path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)

    def get_active_todo_snapshot(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> Optional[Dict[str, Any]]:
        tasks_root = self._todo_tasks_root()
        candidate_task_id: str | None = None

        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            session_index_path = self._todo_sessions_root() / f"{normalized_session_id}.json"
            if session_index_path.exists():
                try:
                    payload = json.loads(session_index_path.read_text(encoding="utf-8"))
                    candidate_task_id = str(
                        payload.get("activeTaskId") or payload.get("latestTaskId") or ""
                    ).strip() or None
                except Exception:
                    candidate_task_id = None

        if candidate_task_id:
            task_path = tasks_root / f"{candidate_task_id}.json"
            if task_path.exists():
                try:
                    payload = json.loads(task_path.read_text(encoding="utf-8"))
                    normalized_run_id = str(run_id or "").strip()
                    if normalized_run_id and not normalized_session_id:
                        payload_run_id = str(payload.get("runId") or "").strip()
                        if payload_run_id != normalized_run_id:
                            payload = None
                    if payload:
                        return payload
                except Exception:
                    pass

        normalized_run_id = str(run_id or "").strip()
        snapshots: list[Dict[str, Any]] = []
        for task_path in tasks_root.glob("*.json"):
            try:
                payload = json.loads(task_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if normalized_session_id and str(payload.get("sessionId") or "").strip() != normalized_session_id:
                continue
            if normalized_run_id and not normalized_session_id and str(payload.get("runId") or "").strip() != normalized_run_id:
                continue
            snapshots.append(payload)

        if not snapshots:
            return None

        snapshots.sort(
            key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
            reverse=True,
        )
        return snapshots[0]


storage = StorageManager()
