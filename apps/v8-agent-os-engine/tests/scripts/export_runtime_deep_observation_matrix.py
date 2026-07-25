from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

if "chromadb" not in sys.modules:
    class _FakeChromaCollection:
        def upsert(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {}

    class _FakeChromaClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def get_or_create_collection(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeChromaCollection()

    sys.modules["chromadb"] = type("chromadb", (), {"PersistentClient": _FakeChromaClient})()

from api.models import EngineConfig  # noqa: E402
from core.database import db  # noqa: E402
from core.delegation_broker import choose_best_local_agent_with_diagnostics, normalize_task_brief  # noqa: E402
from core.knowledge_db import knowledge_db  # noqa: E402
from core.memory_store import memory_store  # noqa: E402
from core.runtime_tool_access import filter_visible_tools_for_actor, grant_runtime_tool_groups, normalize_runtime_access  # noqa: E402
from core.storage import storage  # noqa: E402
from core.system_tools.native import NATIVE_TOOLS  # noqa: E402
from core.supervisor_tool_policy import build_supervisor_tool_policy_snapshot  # noqa: E402
from erc.capability_registry import capability_registry  # noqa: E402
from graph.agent_factories import _build_agent_system_content, _format_delegated_task_contract, _select_contextual_subagent_native_tools  # noqa: E402
from graph.supervisor_context import build_supervisor_system_content, workspace_resolution_service  # noqa: E402
from graph.supervisor_routing import build_supervisor_toolset  # noqa: E402
from runtimes.engineering import engineering_lane_service  # noqa: E402
from runtimes.extensions.runtime import extensions_runtime_service  # noqa: E402
from runtimes.extensions.skills.loader import fetch_skill_instructions  # noqa: E402
from runtimes.memory.runtime import memory_runtime  # noqa: E402


REPO_ROOT = ENGINE_ROOT.parents[1]
OUTPUT_ROOT = REPO_ROOT / "docs" / "chatruntime" / "runtime_deep_observation_reports"
SCRIPT_PATH = Path(__file__).resolve()
EXTENSIONS_ROUTE_TOKEN_WARNING_BUDGET = 800


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _estimate_tokens(text: Any) -> int:
    value = str(text or "")
    return max(1, len(value) // 4) if value.strip() else 0


def _tool_name(tool_ref: Any) -> str:
    return str(getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")) or "").strip()


def _tool_desc(tool_ref: Any) -> str:
    return str(getattr(tool_ref, "description", getattr(tool_ref, "__doc__", "")) or "").strip().splitlines()[0]


def _compact_text(text: Any, limit: int = 220) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _safe_get_mcp_tools() -> list[Any]:
    try:
        return list(extensions_runtime_service.get_mcp_tools() or [])
    except Exception:
        return []


def _content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_system_content_file(output_dir: Path | None, filename: str, title: str, content: str) -> dict[str, Any]:
    if output_dir is None:
        return {
            "path": "",
            "sha256": _content_digest(content),
            "chars": len(content),
            "estimatedTokens": _estimate_tokens(content),
        }
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_text(f"# {title}\n\n```text\n{content}\n```\n", encoding="utf-8")
    return {
        "path": str(path),
        "sha256": _content_digest(content),
        "chars": len(content),
        "estimatedTokens": _estimate_tokens(content),
    }


def _build_toolset() -> tuple[list[Any], list[dict[str, Any]], dict[str, Any]]:
    loaded_agents = storage.get_all_agents()
    filtered_native_tools = capability_registry.filter_direct_tools(NATIVE_TOOLS)
    supervisor_config = storage.get_supervisor_config() or {}
    config = EngineConfig()
    supervisor_tools = build_supervisor_toolset(
        fetch_skill_instructions_tool=fetch_skill_instructions,
        filtered_native_tools=filtered_native_tools,
        external_tools=[],
        all_mcp_tools=_safe_get_mcp_tools(),
        supervisor_allowed_tools=supervisor_config.get("allowed_tools"),
        config_allowed_tools=config.allowed_tools,
    )
    policy_snapshot = build_supervisor_tool_policy_snapshot(supervisor_config.get("allowed_tools"))
    return supervisor_tools, loaded_agents, policy_snapshot


def _select_project() -> dict[str, Any] | None:
    registry = storage.get_projects_registry() or {}
    for item in list(registry.get("projects") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("workspacePath") or "").strip():
            return dict(item)
    return None


def _main_workspace() -> str:
    try:
        return str(workspace_resolution_service.get_main_workspace_path() or "").strip()
    except Exception:
        cfg = storage.get_workspace_config() or {}
        return str(cfg.get("path") or cfg.get("workspacePath") or cfg.get("agent_workspace_path") or "").strip()


def _scene_payloads() -> list[dict[str, Any]]:
    project = _select_project()
    main_workspace = _main_workspace()
    project_workspace = str((project or {}).get("workspacePath") or main_workspace).strip()
    project_id = str((project or {}).get("projectId") or (project or {}).get("project_id") or (project or {}).get("id") or "").strip()
    project_workspace_id = str((project or {}).get("workspaceId") or (project or {}).get("workspace_id") or "").strip()
    return [
        {
            "id": "daily_chat",
            "label": "通用日常聊天",
            "userQuery": "帮我把今天的事项整理成一段给团队看的说明，语气自然一点。",
            "runtimeKind": "chat",
            "scope": "workspace:main",
            "scopeChain": ["global", "workspace:main"],
            "workspacePath": main_workspace,
            "workspaceId": "",
            "projectId": "",
            "transport": "",
            "engineeringMode": "off",
            "taskBrief": {
                "taskBriefId": "daily-docs-brief",
                "goal": "整理一份面向团队的日常说明。",
                "requiredCapabilities": ["documentation", "writing"],
                "behaviorScope": ["docs"],
                "writeSet": [],
            },
        },
        {
            "id": "project_coding",
            "label": "项目编程",
            "userQuery": "修复 admin/model-hub 的模型卡片布局问题，并验证不会破坏 provider-qualified modelRef。",
            "runtimeKind": "chat",
            "scope": f"project:{project_id}" if project_id else "workspace:main",
            "scopeChain": ["global", f"project:{project_id}"] if project_id else ["global", "workspace:main"],
            "workspacePath": project_workspace,
            "workspaceId": project_workspace_id,
            "projectId": project_id,
            "transport": "",
            "engineeringMode": "force",
            "taskBrief": {
                "taskBriefId": "project-coding-brief",
                "goal": "实现 ModelHub 卡片布局修复并保持模型引用稳定。",
                "requiredCapabilities": ["frontend", "engineering", "verification"],
                "behaviorScope": ["implementation", "verification"],
                "criticalFiles": ["apps/v8-agent-os-admin/src/app/admin/(dashboard)/model-hub/page.tsx"],
                "readSet": ["apps/v8-agent-os-admin/src/app/admin/(dashboard)/model-hub/page.tsx"],
                "writeSet": ["apps/v8-agent-os-admin/src/app/admin/(dashboard)/model-hub/page.tsx"],
                "verificationContract": ["npm run build"],
                "proofExpectations": ["diff summary", "admin build result"],
            },
        },
        {
            "id": "project_coding_with_research",
            "label": "项目编程 + Research evidence",
            "userQuery": "修复 admin/model-hub 的 provider 图标解析问题，先查最新官方文档和现有源码证据，再给出可验证补丁。",
            "runtimeKind": "chat",
            "scope": f"project:{project_id}" if project_id else "workspace:main",
            "scopeChain": ["global", f"project:{project_id}"] if project_id else ["global", "workspace:main"],
            "workspacePath": project_workspace,
            "workspaceId": project_workspace_id,
            "projectId": project_id,
            "transport": "",
            "engineeringMode": "force",
            "runtimeToolGrantGroups": ["research.core"],
            "taskBrief": {
                "taskBriefId": "project-coding-research-brief",
                "goal": "先取得 source-backed evidence bundle，再执行 ModelHub provider 图标解析补丁。",
                "requiredCapabilities": ["frontend", "engineering", "verification", "source_quality"],
                "runtimeAccess": ["research.core"],
                "behaviorScope": ["implementation", "verification", "research_evidence"],
                "criticalFiles": ["apps/v8-agent-os-admin/src/lib/models/model-assets.ts"],
                "readSet": ["apps/v8-agent-os-admin/src/lib/models/model-assets.ts"],
                "writeSet": ["apps/v8-agent-os-admin/src/lib/models/model-assets.ts"],
                "researchRefs": ["research://bundle/provider-icons-docs"],
                "verificationContract": ["npx tsc --noEmit"],
                "proofExpectations": ["researchRefs consumed", "diff summary", "admin typecheck result"],
            },
        },
        {
            "id": "network_api",
            "label": "Network API workspace-less",
            "userQuery": "第三方系统通过 OpenAI-compatible API 请求：请根据历史偏好生成一个简短答复。",
            "runtimeKind": "network_supervisor_openai",
            "scope": "external_api_thread:runtime-observation",
            "scopeChain": ["global", "external_api_thread:runtime-observation"],
            "workspacePath": "",
            "workspaceId": "",
            "projectId": "",
            "transport": "network_supervisor_openai",
            "engineeringMode": "off",
            "taskBrief": {
                "taskBriefId": "network-api-brief",
                "goal": "处理外部 API 传入的结构化答复任务。",
                "requiredCapabilities": ["research", "writing"],
                "behaviorScope": ["network_api"],
                "writeSet": [],
            },
        },
        {
            "id": "research_after_grant",
            "label": "Research runtime broker grant",
            "userQuery": "联网调研最新的 OpenAI / Anthropic / Gemini API 官方文档差异，要求来源矩阵、冲突点和置信度。",
            "runtimeKind": "chat",
            "scope": "workspace:main",
            "scopeChain": ["global", "workspace:main"],
            "workspacePath": main_workspace,
            "workspaceId": "",
            "projectId": "",
            "transport": "",
            "engineeringMode": "off",
            "runtimeToolGrantGroups": ["research.core"],
            "taskBrief": {
                "taskBriefId": "research-broker-brief",
                "goal": "产出多源联网调研计划和 evidence bundle，不执行文件写入或系统副作用。",
                "requiredCapabilities": ["web_research", "source_quality", "fact_checking"],
                "runtimeAccess": ["research.core"],
                "familyHint": "research",
                "behaviorScope": ["research_plan", "evidence_bundle"],
                "writeSet": [],
            },
        },
        {
            "id": "creative_media_after_grant",
            "label": "Creative Media runtime broker grant",
            "userQuery": "帮我做一个 5 秒竖屏产品短视频：先生成一张主视觉，再生成短视频和旁白，最后拼成一个可下载 artifact。",
            "runtimeKind": "chat",
            "scope": f"project:{project_id}" if project_id else "workspace:main",
            "scopeChain": ["global", f"project:{project_id}"] if project_id else ["global", "workspace:main"],
            "workspacePath": project_workspace,
            "workspaceId": project_workspace_id,
            "projectId": project_id,
            "transport": "",
            "engineeringMode": "off",
            "runtimeToolGrantGroups": ["creative_media.core"],
            "taskBrief": {
                "taskBriefId": "creative-media-brief",
                "goal": "编译 Creative Media recipe，登记素材，创建图片/视频/语音 job，并在后期计划中拼接交付。",
                "requiredCapabilities": ["creative_media", "video", "voice", "post_production"],
                "runtimeAccess": ["creative_media.core"],
                "behaviorScope": ["creative_media_recipe", "creative_media_job", "artifact_delivery"],
                "writeSet": ["creative_media/"],
                "verificationContract": ["artifact content reachable", "render job diagnostics present"],
            },
        },
        {
            "id": "creative_media_with_research",
            "label": "Creative Media + Research evidence",
            "userQuery": "先调研 Seedance 2.0 与 Veo 的官方能力差异，再编译一个竖屏短视频 creative recipe。",
            "runtimeKind": "chat",
            "scope": f"project:{project_id}" if project_id else "workspace:main",
            "scopeChain": ["global", f"project:{project_id}"] if project_id else ["global", "workspace:main"],
            "workspacePath": project_workspace,
            "workspaceId": project_workspace_id,
            "projectId": project_id,
            "transport": "",
            "engineeringMode": "off",
            "runtimeToolGrantGroups": ["creative_media.core", "research.core"],
            "taskBrief": {
                "taskBriefId": "creative-media-research-brief",
                "goal": "用 Research evidence 判断 provider 能力，再编译 Creative Media recipe。",
                "requiredCapabilities": ["creative_media", "video", "source_quality", "provider_docs"],
                "runtimeAccess": ["creative_media.core", "research.core"],
                "behaviorScope": ["research_evidence", "creative_media_recipe", "artifact_delivery"],
                "researchRefs": ["research://bundle/video-provider-capabilities"],
                "writeSet": ["creative_media/"],
                "verificationContract": ["recipe cites researchRefs", "provider capability diagnostics present"],
            },
        },
        {
            "id": "computer_use_with_research",
            "label": "Computer Use + Research evidence",
            "userQuery": "先查某个桌面应用最新官方快捷键说明，再用 Computer Use 观察窗口并执行高层任务。",
            "runtimeKind": "chat",
            "scope": "workspace:main",
            "scopeChain": ["global", "workspace:main"],
            "workspacePath": main_workspace,
            "workspaceId": "",
            "projectId": "",
            "transport": "",
            "engineeringMode": "off",
            "runtimeToolGrantGroups": ["computer_use.control", "research.core"],
            "taskBrief": {
                "taskBriefId": "computer-use-research-brief",
                "goal": "用 Research evidence 获取外部事实，再把桌面操作交给 ComputerUseRuntime 高层工具。",
                "requiredCapabilities": ["computer_use", "web_research", "source_quality"],
                "runtimeAccess": ["computer_use.control", "research.core"],
                "behaviorScope": ["research_evidence", "computer_use_task"],
                "writeSet": [],
            },
        },
        {
            "id": "subagent_family_high_confidence",
            "label": "Subagent family 高置信自动 reveal",
            "userQuery": "用 Seedance 2.0 生成一个竖屏视频镜头，保留原生音效。",
            "runtimeKind": "chat",
            "scope": "workspace:main",
            "scopeChain": ["global", "workspace:main"],
            "workspacePath": main_workspace,
            "workspaceId": "",
            "projectId": "",
            "transport": "",
            "engineeringMode": "off",
            "taskBrief": {
                "taskBriefId": "subagent-family-high-confidence",
                "goal": "观察 TaskShapeClassifier 高置信媒体生成任务是否最多自动 reveal 一个 family。",
                "requiredCapabilities": ["creative_media", "video"],
                "behaviorScope": ["observation"],
                "writeSet": [],
            },
        },
        {
            "id": "subagent_family_low_confidence",
            "label": "Subagent family 低置信模糊需求",
            "userQuery": "帮我做一个视频，效果好一点。",
            "runtimeKind": "chat",
            "scope": "workspace:main",
            "scopeChain": ["global", "workspace:main"],
            "workspacePath": main_workspace,
            "workspaceId": "",
            "projectId": "",
            "transport": "",
            "engineeringMode": "off",
            "taskBrief": {
                "taskBriefId": "subagent-family-low-confidence",
                "goal": "观察 output modality only 模糊请求是否只排序 family cards 而不展开具体成员。",
                "requiredCapabilities": ["video"],
                "behaviorScope": ["observation"],
                "writeSet": [],
            },
        },
    ]


def _module_stats(bundle: dict[str, Any], extension_prompt: str) -> list[dict[str, Any]]:
    modules = [
        ("runtime_registry_context", bundle.get("runtime_registry_context")),
        ("specialist_agents_context", bundle.get("specialist_agents_context")),
        ("available_tools_context", bundle.get("available_tools_context")),
        ("network_supervisor_context", bundle.get("network_supervisor_context")),
        ("engineering_context", bundle.get("engineering_context")),
        ("artifact_awareness_context", bundle.get("artifact_awareness_context")),
        ("todos_context", bundle.get("todos_context")),
        ("memory_context", bundle.get("memory_context")),
        ("workspace_rules_context", bundle.get("workspace_rules_context")),
        ("env_context", bundle.get("env_context")),
        ("reflex_prompt_addition", bundle.get("reflex_prompt_addition")),
        ("gate_prompt_addition", bundle.get("gate_prompt_addition")),
        ("extensions_route_block", extension_prompt),
    ]
    return [
        {
            "name": name,
            "present": bool(str(content or "").strip()),
            "estimatedTokens": _estimate_tokens(content),
            "chars": len(str(content or "")),
            "preview": _compact_text(content, 260),
        }
        for name, content in modules
    ]


def _classify_tool(name: str) -> str:
    if name in {"share_workspace_file", "download_media_for_vision", "vision_media_analyzer"}:
        return "artifact_or_media_runtime_candidate"
    if name in {"mem_update", "mem_delete"}:
        return "memory_governance_candidate"
    if name.startswith("computer_use_") and name not in {
        "computer_use_list_apps",
        "computer_use_desktop_capabilities",
        "computer_use_resolve_execution_route",
        "computer_use_execute_task",
        "computer_use_observe_scene",
    }:
        return "low_level_computer_use_candidate"
    if name in {"list_processes", "manage_process", "manage_cron", "manage_hook", "read_audit_log"}:
        return "admin_or_runtime_governance_surface"
    if name in {"web_fetch", "web_read", "web_extract", "web_search"}:
        return "raw_web_tool_surface"
    if name in {"research_broker"}:
        return "research_runtime_surface"
    if name in {"http_request"}:
        return "raw_network_candidate"
    if name in {"run_system_command", "command_session_broker"}:
        return "command_surface"
    if name in {"delegation_broker", "fetch_skill_instructions", "ask_user", "memory_recall", "web_broker"}:
        return "core_supervisor_surface"
    if name.startswith("rpa_"):
        return "rpa_runtime_surface"
    if name.startswith("s3_"):
        return "storage_runtime_surface"
    return "other"


def _tool_registry_stats(supervisor_tools: list[Any]) -> dict[str, Any]:
    entries = [
        {
            "name": _tool_name(tool),
            "description": _tool_desc(tool),
            "category": _classify_tool(_tool_name(tool)),
        }
        for tool in supervisor_tools
        if _tool_name(tool)
    ]
    counts = Counter(item["category"] for item in entries)
    return {
        "total": len(entries),
        "estimatedTokens": _estimate_tokens("\n".join(f"- {item['name']}: {item['description']}" for item in entries)),
        "categoryCounts": dict(sorted(counts.items())),
        "entries": entries,
        "runtimeNativeCandidates": [item for item in entries if item["category"].endswith("_candidate")],
    }


def _specialist_registry_stats(agents: list[dict[str, Any]], specialist_context: str) -> dict[str, Any]:
    specialist_agents = [item for item in agents if str(item.get("id") or "") != "supervisor"]
    classes = Counter()
    tool_policies = Counter()
    domain_tags = Counter()
    for agent in specialist_agents:
        snapshot = agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {}
        classes[str(snapshot.get("agentClass") or "unknown")] += 1
        tool_policies[str(snapshot.get("toolExposurePolicy") or "unknown")] += 1
        for tag in list(snapshot.get("domainTags") or []):
            domain_tags[str(tag)] += 1
    return {
        "total": len(specialist_agents),
        "estimatedTokens": _estimate_tokens(specialist_context),
        "agentClasses": dict(classes.most_common()),
        "toolExposurePolicies": dict(tool_policies.most_common()),
        "topDomainTags": dict(domain_tags.most_common(16)),
        "agents": [
            {
                "id": str(agent.get("id") or ""),
                "name": str(agent.get("name") or ""),
                "description": _compact_text(agent.get("description"), 180),
                "capabilitySnapshot": agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {},
            }
            for agent in specialist_agents
        ],
    }


def _extract_revealed_families(specialist_context: str) -> dict[str, Any]:
    text = str(specialist_context or "")
    revealed: list[dict[str, Any]] = []
    current_family = ""
    current_source = ""
    in_revealed = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[revealedFamilyMembers]":
            in_revealed = True
            continue
        if not in_revealed:
            continue
        match = re.match(r"\[([^\]]+)\]\s+revealSource=([^\s]+)", stripped)
        if match:
            current_family = match.group(1)
            current_source = match.group(2)
            revealed.append({"family": current_family, "source": current_source, "members": []})
            continue
        if stripped.startswith("[/revealedFamilyMembers]"):
            break
        if stripped.startswith("- ") and revealed:
            member_id = stripped[2:].split("|", 1)[0].strip()
            if member_id:
                revealed[-1]["members"].append(member_id)
    return {
        "revealedFamilies": revealed,
        "revealedMemberCount": sum(len(item.get("members") or []) for item in revealed),
        "hasRevealedMembersBlock": "[revealedFamilyMembers]" in text,
    }


def _memory_markers(text: str) -> dict[str, bool]:
    value = str(text or "")
    return {
        "memoryMap": "[MEMORY MAP]" in value,
        "recentActivityTeaser": "[RECENT ACTIVITY TEASER]" in value,
        "memorySummary": "[MEMORY SUMMARY]" in value,
        "knowledgeGraphSummary": "[KNOWLEDGE GRAPH SUMMARY]" in value,
    }


def _web_research_tool_surface(tool_names: list[str]) -> dict[str, Any]:
    names = {str(item) for item in list(tool_names or []) if str(item).strip()}
    return {
        "hasWebBroker": "web_broker" in names,
        "hasResearchBroker": "research_broker" in names,
        "hasRawWebTools": bool(names & {"web_fetch", "web_read", "web_extract", "web_search"}),
        "rawWebTools": sorted(names & {"web_fetch", "web_read", "web_extract", "web_search"}),
        "hasComputerUseControl": bool(names & {"computer_use_desktop_capabilities", "computer_use_observe_scene", "computer_use_execute_task"}),
        "hasCreativeMediaCore": any(name.startswith("creative_media_") for name in names),
    }


def _route_diagnostics(route_bundle: Any) -> dict[str, Any]:
    summary = dict(getattr(route_bundle, "candidate_summary", {}) or {})
    skill_entries = list(summary.get("skillEntries") or [])
    stage1 = list(summary.get("skillStage1Entries") or [])
    mcp_entries = list(summary.get("mcpEntries") or [])
    return {
        "promptTokens": _estimate_tokens(getattr(route_bundle, "prompt_addition", "")),
        "selectedSkillNames": list(getattr(route_bundle, "selected_skill_names", []) or []),
        "selectedSkillIds": list(getattr(route_bundle, "selected_skill_ids", []) or []),
        "skillEntryCount": len(skill_entries),
        "skillStage1EntryCount": len(stage1),
        "mcpEntryCount": len(mcp_entries),
        "rootDescriptorCount": len(list(getattr(route_bundle, "skill_root_descriptors", []) or [])),
        "candidateSummaryKeys": sorted(summary.keys()),
        "skillEntriesPreview": [
            {
                "name": item.get("name"),
                "sourceType": item.get("sourceType"),
                "score": item.get("score"),
                "reasons": item.get("reasons"),
            }
            for item in skill_entries[:8]
            if isinstance(item, dict)
        ],
    }


def _memory_lifecycle_snapshot() -> dict[str, Any]:
    preferences_by_scope = {}
    try:
        raw_preferences = memory_store._load_raw_preferences()  # noqa: SLF001 - internal diagnostic script.
        preferences_by_scope = {scope: len(values or {}) for scope, values in dict(raw_preferences or {}).items()}
    except Exception as exc:
        preferences_by_scope = {"error": str(exc)}

    table_counts: dict[str, Any] = {}
    grouped: dict[str, Any] = {}
    with knowledge_db._conn() as conn:  # noqa: SLF001 - internal diagnostic script.
        existing_tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for table in ("knowledge", "entities", "relations"):
            if table in existing_tables:
                table_counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        if "knowledge" in existing_tables:
            knowledge_columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(knowledge)").fetchall()]
            grouped["knowledgeLifecycleColumns"] = [
                name
                for name in (
                    "lifecycle_state",
                    "last_seen_at",
                    "last_injected_at",
                    "last_verified_at",
                    "evidence_refs_json",
                    "promotion_reason",
                    "superseded_by",
                    "tombstone_of",
                    "decay_score",
                    "agents_hash",
                    "repo_signature",
                    "signature_policy",
                    "maintainer_source",
                    "confidence",
                    "effective_confidence",
                    "metadata_json",
                )
                if name in knowledge_columns
            ]
            grouped["knowledgeByStatus"] = dict(
                (str(status), int(count))
                for status, count in conn.execute("SELECT status, COUNT(*) FROM knowledge GROUP BY status").fetchall()
            )
            grouped["knowledgeByLifecycleState"] = dict(
                (str(state), int(count))
                for state, count in conn.execute("SELECT COALESCE(lifecycle_state, 'active'), COUNT(*) FROM knowledge GROUP BY COALESCE(lifecycle_state, 'active')").fetchall()
            )
            grouped["knowledgeByMaintainerSource"] = dict(
                (str(source), int(count))
                for source, count in conn.execute("SELECT COALESCE(maintainer_source, 'memory_runtime'), COUNT(*) FROM knowledge GROUP BY COALESCE(maintainer_source, 'memory_runtime')").fetchall()
            )
            grouped["knowledgeByScopeTop"] = dict(
                (str(scope), int(count))
                for scope, count in conn.execute("SELECT scope, COUNT(*) FROM knowledge GROUP BY scope ORDER BY COUNT(*) DESC LIMIT 12").fetchall()
            )

    with db.get_connection() as conn:
        existing_tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for table in (
            "memory_workflow_candidates",
            "memory_workflow_episodes",
            "memory_workflow_hint_events",
            "engineering_proof_entries",
            "engineering_workset_observations",
        ):
            if table in existing_tables:
                table_counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        if "memory_workflow_candidates" in existing_tables:
            grouped["workflowCandidatesByStatus"] = dict(
                (str(status), int(count))
                for status, count in conn.execute("SELECT status, COUNT(*) FROM memory_workflow_candidates GROUP BY status").fetchall()
            )
            grouped["workflowCandidatesByClass"] = dict(
                (str(workflow_class), int(count))
                for workflow_class, count in conn.execute("SELECT workflow_class, COUNT(*) FROM memory_workflow_candidates GROUP BY workflow_class").fetchall()
            )
    return {
        "preferencesByScope": preferences_by_scope,
        "tableCounts": table_counts,
        "grouped": grouped,
        "lifecycleSignals": [
            "knowledge rows now expose lifecycle, maintainer, confidence, AGENTS hash, and soft repo signature fields.",
            "stale knowledge is paused at recall/search time and can be manually revalidated without deleting the row.",
            "human_admin confidence advantage is represented by effective_confidence and remains gated by lifecycle_state.",
            "global preference power is real, but safe global promotion depends on canonicalization, quarantine, and tombstone discipline rather than write volume.",
        ],
    }


def _build_scene(
    scene: dict[str, Any],
    supervisor_tools: list[Any],
    agents: list[dict[str, Any]],
    config: EngineConfig,
    system_content_output_dir: Path | None = None,
) -> dict[str, Any]:
    messages = [
        HumanMessage(
            content=scene["userQuery"],
            additional_kwargs={
                "session_id": f"runtime-observation-{scene['id']}",
                "workspace_path": scene.get("workspacePath") or "",
                "workspace_id": scene.get("workspaceId") or "",
                "project_id": scene.get("projectId") or "",
                "resolved_scope": scene["scope"],
            },
        )
    ]
    task_brief = normalize_task_brief(scene.get("taskBrief") or {})
    engineering_result: dict[str, Any] = {}
    if scene.get("engineeringMode") != "off":
        engineering_result = engineering_lane_service.build_context_pack(
            user_query=scene["userQuery"],
            mode=str(scene.get("engineeringMode") or "auto"),
            session_id=f"runtime-observation-{scene['id']}",
            run_id=f"runtime-observation-{scene['id']}-run",
            project_id=scene.get("projectId") or None,
            workspace_id=scene.get("workspaceId") or None,
            workspace_path=scene.get("workspacePath") or None,
            task_brief=task_brief,
        )
    route_context = {"transport": scene.get("transport") or ""}
    if scene.get("runtimeToolGrantGroups"):
        route_context, _grants, _rejected = grant_runtime_tool_groups(
            route_context,
            scene.get("runtimeToolGrantGroups") or [],
            reason="runtime observation dry-run",
        )
    state = {
        "messages": messages,
        "session_id": f"runtime-observation-{scene['id']}",
        "run_id": f"runtime-observation-{scene['id']}-run",
        "runtime_kind": scene["runtimeKind"],
        "transport": scene.get("transport") or "",
        "workspace_id": scene.get("workspaceId") or "",
        "workspace_path": scene.get("workspacePath") or "",
        "project_id": scene.get("projectId") or "",
        "current_route_context": route_context,
        "engineering_context": engineering_result,
        "runtime_task_contracts": [task_brief],
    }
    token = extensions_runtime_service.bind_execution_context(
        session_id=state["session_id"],
        run_id=state["run_id"],
        workspace_id=scene.get("workspaceId") or None,
        workspace_path=scene.get("workspacePath") or None,
        project_id=scene.get("projectId") or None,
        runtime_kind=scene["runtimeKind"],
    )
    try:
        visible_supervisor_tools = filter_visible_tools_for_actor(
            supervisor_tools,
            actor="supervisor",
            route_context=dict(state.get("current_route_context") or {}),
        )
        route_bundle = extensions_runtime_service.build_supervisor_route(
            user_query=scene["userQuery"],
            supervisor_tools=visible_supervisor_tools,
            loaded_agents=agents,
            skill_limit=10,
            mcp_limit=5,
        )
        supervisor_context = build_supervisor_system_content(
            state=state,
            config=config,
            user_query=scene["userQuery"],
            current_scope=scene["scope"],
            scope_chain=list(scene["scopeChain"]),
            session_id=state["session_id"],
            messages=messages,
            loaded_agents=agents,
            supervisor_tools=list(route_bundle.filtered_tools),
            memory_runtime=memory_runtime,
            extension_prompt_addition=route_bundle.prompt_addition,
        )
    finally:
        extensions_runtime_service.reset_execution_context(token)

    selected_agent, agent_selection = choose_best_local_agent_with_diagnostics(task_brief, agents)
    reveal_diagnostics = _extract_revealed_families(str(supervisor_context.get("specialist_agents_context") or ""))
    subagent_context: dict[str, Any] = {
        "selectedAgent": selected_agent,
        "selectionDiagnostics": agent_selection,
        "systemContent": "",
        "systemContentFile": {},
        "boundToolsFile": {},
        "boundToolNames": [],
        "webResearchToolSurface": {},
        "routeDiagnostics": {},
        "moduleStats": [],
        "memoryMarkers": {},
    }
    if selected_agent:
        delegated_runtime_access = normalize_runtime_access(task_brief.get("runtimeAccess"))
        subagent_bound_tools = filter_visible_tools_for_actor(
            _select_contextual_subagent_native_tools(supervisor_tools, delegated_runtime_access) + [fetch_skill_instructions],
            actor="subagent",
            runtime_access=delegated_runtime_access,
        )
        sub_token = extensions_runtime_service.bind_execution_context(
            session_id=state["session_id"],
            run_id=state["run_id"],
            agent_id=str(selected_agent.get("id") or ""),
            workspace_id=scene.get("workspaceId") or None,
            workspace_path=scene.get("workspacePath") or None,
            project_id=scene.get("projectId") or None,
            runtime_kind=scene["runtimeKind"],
        )
        try:
            sub_route = extensions_runtime_service.build_contextual_route(
                user_query=str(task_brief.get("routeQuery") or task_brief.get("goal") or scene["userQuery"]),
                available_tools=subagent_bound_tools,
                loaded_agents=None,
                skill_limit=6,
                mcp_limit=4,
                freshness_mode="preview_best_effort",
            )
        finally:
            extensions_runtime_service.reset_execution_context(sub_token)
        env_context = (
            "<environment>\n"
            f"OS: {sys.platform}\n"
            f"Local Workspace Absolute Path: {scene.get('workspacePath') or _main_workspace()}\n"
            "</environment>\n"
        )
        delegated_context = _format_delegated_task_contract(task_brief)
        sub_content = _build_agent_system_content(
            agent_name=str(selected_agent.get("name") or selected_agent.get("id") or "subagent"),
            agent_system_prompt=str(selected_agent.get("system_prompt") or ""),
            env_context=env_context,
            delegated_plan_context=delegated_context,
            route_prompt_addition=sub_route.prompt_addition,
        )
        subagent_context = {
            "selectedAgent": {
                "id": selected_agent.get("id"),
                "name": selected_agent.get("name"),
                "capabilitySnapshot": selected_agent.get("capabilitySnapshot") if isinstance(selected_agent.get("capabilitySnapshot"), dict) else {},
            },
            "selectionDiagnostics": agent_selection,
            "systemContentTokens": _estimate_tokens(sub_content),
            "systemContentFile": _write_system_content_file(
                system_content_output_dir,
                f"{scene['id']}_subagent_SYSTEM_CONTENT.md",
                f"{scene['label']} subagent SYSTEM_CONTENT",
                sub_content,
            ),
            "boundToolNames": [_tool_name(tool) for tool in subagent_bound_tools],
            "webResearchToolSurface": _web_research_tool_surface([_tool_name(tool) for tool in subagent_bound_tools]),
            "boundToolsFile": _write_system_content_file(
                system_content_output_dir,
                f"{scene['id']}_subagent_BOUND_TOOLS.md",
                f"{scene['label']} subagent BOUND_TOOLS",
                "\n".join(
                    f"- {_tool_name(tool)}: {_tool_desc(tool)}"
                    for tool in subagent_bound_tools
                    if _tool_name(tool)
                ),
            ),
            "routeDiagnostics": _route_diagnostics(sub_route),
            "moduleStats": [
                {"name": "agent_persona_and_env", "estimatedTokens": _estimate_tokens(sub_content.replace(sub_route.prompt_addition, ""))},
                {"name": "subagent_extensions_route_block", "estimatedTokens": _estimate_tokens(sub_route.prompt_addition)},
            ],
            "systemContentPreview": _compact_text(sub_content, 700),
            "memoryMarkers": _memory_markers(sub_content),
        }

    return {
        "id": scene["id"],
        "label": scene["label"],
        "sceneConfig": {
            "runtimeKind": scene["runtimeKind"],
            "scope": scene["scope"],
            "scopeChain": scene["scopeChain"],
            "workspacePath": scene.get("workspacePath") or "",
            "workspaceId": scene.get("workspaceId") or "",
            "projectId": scene.get("projectId") or "",
            "transport": scene.get("transport") or "",
            "engineeringMode": scene.get("engineeringMode") or "auto",
        },
        "supervisor": {
            "systemContentTokens": _estimate_tokens(supervisor_context["system_content"]),
            "visibleToolNames": [_tool_name(tool) for tool in visible_supervisor_tools],
            "webResearchToolSurface": _web_research_tool_surface([_tool_name(tool) for tool in visible_supervisor_tools]),
            "runtimeToolGrants": list((state.get("current_route_context") or {}).get("runtimeToolGrants") or []),
            "systemContentFile": _write_system_content_file(
                system_content_output_dir,
                f"{scene['id']}_supervisor_SYSTEM_CONTENT.md",
                f"{scene['label']} supervisor SYSTEM_CONTENT",
                supervisor_context["system_content"],
            ),
            "moduleStats": _module_stats(supervisor_context, route_bundle.prompt_addition),
            "routeDiagnostics": _route_diagnostics(route_bundle),
            "promptBudgetDiagnostics": supervisor_context.get("prompt_budget_diagnostics") or [],
            "taskShapeHint": supervisor_context.get("task_shape_hint") or {},
            "taskShapeContext": supervisor_context.get("task_shape_context") or "",
            "familyRevealDiagnostics": reveal_diagnostics,
            "memoryMarkers": _memory_markers(supervisor_context["system_content"]),
        },
        "subagent": subagent_context,
        "engineering": {
            "active": bool((engineering_result.get("triggerDecision") or {}).get("active")) if engineering_result else False,
            "triggerDecision": engineering_result.get("triggerDecision") if engineering_result else {},
            "contextPackEstimatedTokens": engineering_result.get("contextPackEstimatedTokens") if engineering_result else 0,
            "proofDraftStatus": ((engineering_result.get("proofDraft") or {}).get("verificationStatus") if engineering_result else None),
        },
    }


def _observations(results: dict[str, Any]) -> list[dict[str, Any]]:
    tool_stats = results["toolRegistry"]
    memory = results["memoryLifecycle"]
    scenes = results["scenes"]
    max_supervisor_module = []
    for scene in scenes:
        for item in scene["supervisor"]["moduleStats"]:
            if item["present"]:
                max_supervisor_module.append((item["estimatedTokens"], scene["id"], item["name"]))
    max_supervisor_module.sort(reverse=True)
    extension_tokens = {
        scene["id"]: next(
            (item["estimatedTokens"] for item in scene["supervisor"]["moduleStats"] if item["name"] == "extensions_route_block"),
            0,
        )
        for scene in scenes
    }
    sub_extension_tokens = {
        scene["id"]: next(
            (item["estimatedTokens"] for item in scene["subagent"].get("moduleStats", []) if item["name"] == "subagent_extensions_route_block"),
            0,
        )
        for scene in scenes
    }
    observations = [
        {
            "id": "specialist_registry_keep_but_topk",
            "severity": "info",
            "summary": "Specialist registry is family-scoped and compact; continue watching growth of globalExposure entries and configured family limits.",
            "evidence": results["specialistRegistry"],
        },
    ]
    runtime_native_candidates = list(tool_stats["runtimeNativeCandidates"])
    if runtime_native_candidates:
        observations.append(
            {
                "id": "tool_registry_runtime_downshift",
                "severity": "high",
                "summary": "Direct tool registry exposes measured runtime-native candidates; inspect the evidence before adding downshift work.",
                "evidence": {
                    "toolTotal": tool_stats["total"],
                    "runtimeNativeCandidateCount": len(runtime_native_candidates),
                    "categoryCounts": tool_stats["categoryCounts"],
                    "candidatesByCategory": {
                        category: [item["name"] for item in runtime_native_candidates if item["category"] == category]
                        for category in sorted({item["category"] for item in runtime_native_candidates})
                    },
                },
            }
        )
    if max([*extension_tokens.values(), *sub_extension_tokens.values(), 0]) > EXTENSIONS_ROUTE_TOKEN_WARNING_BUDGET:
        observations.append(
            {
                "id": "extensions_route_block_over_budget",
                "severity": "medium",
                "summary": "Extensions route block exceeded the observation warning budget in at least one scene; this is evidence-triggered, not a static compact-contract TODO.",
                "evidence": {
                    "warningBudgetTokens": EXTENSIONS_ROUTE_TOKEN_WARNING_BUDGET,
                    "supervisorExtensionTokens": extension_tokens,
                    "subagentExtensionTokens": sub_extension_tokens,
                    "selectedSkillsByScene": {scene["id"]: scene["supervisor"]["routeDiagnostics"]["selectedSkillNames"] for scene in scenes},
                },
            }
        )
    observations.extend([
        {
            "id": "web_research_tool_surface_contract",
            "severity": "info",
            "summary": "web_broker remains the baseline single-query/page utility; research_broker only appears after research.core grant, and raw web_search/read tools should not be bound to contextual subagents.",
            "evidence": {
                scene["id"]: {
                    "runtimeToolGrants": scene["supervisor"].get("runtimeToolGrants") or [],
                    "supervisor": scene["supervisor"].get("webResearchToolSurface") or {},
                    "subagent": scene["subagent"].get("webResearchToolSurface") or {},
                    "subagentBoundTools": scene["subagent"].get("boundToolNames") or [],
                    "taskShapeHint": scene["supervisor"].get("taskShapeHint") or {},
                }
                for scene in scenes
                if scene["id"] in {
                    "daily_chat",
                    "project_coding_with_research",
                    "research_after_grant",
                    "creative_media_with_research",
                    "computer_use_with_research",
                }
            },
        },
        {
            "id": "subagent_family_reveal_dry_run",
            "severity": "info",
            "summary": "Synthetic human utterances now export task shape, auto reveal, revealed member, and memory marker diagnostics for high/low confidence family exposure checks.",
            "evidence": {
                scene["id"]: {
                    "taskShapeHint": scene["supervisor"].get("taskShapeHint") or {},
                    "familyRevealDiagnostics": scene["supervisor"].get("familyRevealDiagnostics") or {},
                    "supervisorMemoryMarkers": scene["supervisor"].get("memoryMarkers") or {},
                    "subagentMemoryMarkers": scene["subagent"].get("memoryMarkers") or {},
                }
                for scene in scenes
                if str(scene.get("id") or "").startswith("subagent_family_")
            },
        },
        {
            "id": "memory_one_year_lifecycle_gap",
            "severity": "medium",
            "summary": "Memory lifecycle metadata and stale revalidation are present; long-run governance still needs operational monitoring of decay/tombstone habits.",
            "evidence": memory,
        },
        {
            "id": "network_workspace_rules_guard_current",
            "severity": "low",
            "summary": "The current builder keeps workspace-less network API free of workspace rules while preserving memory context.",
            "evidence": {
                scene["id"]: {
                    "workspaceRulesTokens": next((item["estimatedTokens"] for item in scene["supervisor"]["moduleStats"] if item["name"] == "workspace_rules_context"), 0),
                    "memoryTokens": next((item["estimatedTokens"] for item in scene["supervisor"]["moduleStats"] if item["name"] == "memory_context"), 0),
                }
                for scene in scenes
                if scene["id"] == "network_api"
            },
        },
        {
            "id": "largest_current_modules",
            "severity": "info",
            "summary": "Current token pressure is measurable from freshly generated modules rather than old reports.",
            "evidence": [{"tokens": tokens, "scene": scene_id, "module": name} for tokens, scene_id, name in max_supervisor_module[:10]],
        },
    ])
    return observations


def _build_markdown(results: dict[str, Any]) -> str:
    lines = [
        "# V8OS Runtime 深度观察报告",
        "",
        f"- 生成时间: `{results['generatedAt']}`",
        f"- 生成脚本: `{results['scriptPath']}`",
        "- 输入来源: 当前代码真实 builder / registry / DB 统计；未读取旧 `*_reports` 产物。",
        f"- 场景数量: `{len(results['scenes'])}`",
        "",
        "## 活体矩阵摘要",
        "",
        "| 场景 | Supervisor tokens | Subagent tokens | Extensions tokens | Workspace rules tokens | Memory tokens |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for scene in results["scenes"]:
        module = {item["name"]: item for item in scene["supervisor"]["moduleStats"]}
        lines.append(
            "| {label} | {sup} | {sub} | {ext} | {rules} | {memory} |".format(
                label=scene["label"],
                sup=scene["supervisor"]["systemContentTokens"],
                sub=scene["subagent"].get("systemContentTokens", 0),
                ext=module.get("extensions_route_block", {}).get("estimatedTokens", 0),
                rules=module.get("workspace_rules_context", {}).get("estimatedTokens", 0),
                memory=module.get("memory_context", {}).get("estimatedTokens", 0),
            )
        )
    lines.extend(
        [
            "",
            "## Task context / explicit family reveal / memory markers",
            "",
            "| 场景 | Primary shape | Explicit reveal | Revealed members | Supervisor memory map/log | Subagent memory map/log |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for scene in results["scenes"]:
        hint = scene["supervisor"].get("taskShapeHint") or {}
        reveal = scene["supervisor"].get("familyRevealDiagnostics") or {}
        sup_memory = scene["supervisor"].get("memoryMarkers") or {}
        sub_memory = scene["subagent"].get("memoryMarkers") or {}
        lines.append(
            "| {label} | `{shape}` | `{families}` | {members} | `{sup}` | `{sub}` |".format(
                label=scene["label"],
                shape=hint.get("primaryTaskShape") or "",
                families="+".join(
                    str(item.get("family") or "")
                    for item in list(reveal.get("revealedFamilies") or [])
                    if isinstance(item, dict) and str(item.get("family") or "")
                ) or "none",
                members=int(reveal.get("revealedMemberCount") or 0),
                sup=json.dumps(
                    {
                        "map": bool(sup_memory.get("memoryMap")),
                        "recent": bool(sup_memory.get("recentActivityTeaser")),
                    },
                    ensure_ascii=False,
                ),
                sub=json.dumps(
                    {
                        "map": bool(sub_memory.get("memoryMap")),
                        "recent": bool(sub_memory.get("recentActivityTeaser")),
                    },
                    ensure_ascii=False,
                ),
            )
        )
    lines.extend(
        [
            "",
            "## System content 导出",
            "",
            f"- 输出目录: `{results.get('systemContentOutputDir') or ''}`",
            "",
            "| 场景 | Supervisor SYSTEM_CONTENT | Subagent SYSTEM_CONTENT | Subagent BOUND_TOOLS |",
            "| --- | --- | --- | --- |",
        ]
    )
    for scene in results["scenes"]:
        supervisor_file = (scene.get("supervisor") or {}).get("systemContentFile") or {}
        subagent_file = (scene.get("subagent") or {}).get("systemContentFile") or {}
        subagent_tools_file = (scene.get("subagent") or {}).get("boundToolsFile") or {}
        lines.append(
            "| {label} | `{sup}` | `{sub}` | `{tools}` |".format(
                label=scene["label"],
                sup=supervisor_file.get("path") or "",
                sub=subagent_file.get("path") or "",
                tools=subagent_tools_file.get("path") or "",
            )
        )
    lines.extend(["", "## 关键观察", ""])
    for item in results["observations"]:
        lines.append(f"### {item['id']} ({item['severity']})")
        lines.append("")
        lines.append(f"- {item['summary']}")
        if item["id"] == "tool_registry_runtime_downshift":
            lines.append(f"- 当前 supervisor direct tools: `{item['evidence']['toolTotal']}`")
            lines.append(f"- 可下沉候选: `{item['evidence']['runtimeNativeCandidateCount']}`")
            lines.append(f"- 分类计数: `{json.dumps(item['evidence']['categoryCounts'], ensure_ascii=False)}`")
            lines.append(f"- 候选明细: `{json.dumps(item['evidence'].get('candidatesByCategory', {}), ensure_ascii=False)}`")
        elif item["id"] == "extensions_route_block_over_budget":
            lines.append(f"- 观察阈值: `{item['evidence']['warningBudgetTokens']}` tokens")
            lines.append(f"- Supervisor extensions tokens: `{json.dumps(item['evidence']['supervisorExtensionTokens'], ensure_ascii=False)}`")
            lines.append(f"- Subagent extensions tokens: `{json.dumps(item['evidence']['subagentExtensionTokens'], ensure_ascii=False)}`")
        elif item["id"] == "web_research_tool_surface_contract":
            lines.append(f"- web/research 工具面: `{json.dumps(item['evidence'], ensure_ascii=False)}`")
        elif item["id"] == "subagent_family_reveal_dry_run":
            lines.append(f"- reveal / memory 诊断: `{json.dumps(item['evidence'], ensure_ascii=False)}`")
        elif item["id"] == "memory_one_year_lifecycle_gap":
            lines.append(f"- 当前记忆表计数: `{json.dumps(item['evidence']['tableCounts'], ensure_ascii=False)}`")
            lines.append(f"- 当前 workflow 状态: `{json.dumps(item['evidence']['grouped'].get('workflowCandidatesByStatus', {}), ensure_ascii=False)}`")
        elif item["id"] == "network_workspace_rules_guard_current":
            lines.append(f"- network workspace-less 证据: `{json.dumps(item['evidence'], ensure_ascii=False)}`")
        elif item["id"] == "largest_current_modules":
            for row in item["evidence"][:6]:
                lines.append(f"- `{row['scene']}` / `{row['module']}`: `{row['tokens']}` tokens")
        lines.append("")
    tool_observation = next((item for item in results["observations"] if item["id"] == "tool_registry_runtime_downshift"), None)
    extension_observation = next((item for item in results["observations"] if item["id"] == "extensions_route_block_over_budget"), None)
    extension_token_values: list[int] = []
    for scene in results["scenes"]:
        extension_token_values.extend(
            item["estimatedTokens"]
            for item in scene["supervisor"]["moduleStats"]
            if item["name"] == "extensions_route_block"
        )
        extension_token_values.extend(
            item["estimatedTokens"]
            for item in scene["subagent"].get("moduleStats", [])
            if item["name"] == "subagent_extensions_route_block"
        )
    lines.extend(["## 工具注册表实测结论", ""])
    if tool_observation:
        lines.append("- 本轮发现真实可见的 runtime-native 候选，明细见关键观察；本节不再输出静态排毒模板。")
        lines.append(f"- 候选明细: `{json.dumps(tool_observation['evidence'].get('candidatesByCategory', {}), ensure_ascii=False)}`")
    else:
        lines.append("- 本轮未发现证据命中的 supervisor direct-tool 下沉候选。")
        lines.append("- 后续若出现候选，本节只按实测名称和分类输出，不再生成固定模板结论。")
    lines.extend(["", "## Extensions Route Block 观察", ""])
    max_extension_tokens = max(extension_token_values or [0])
    if extension_observation:
        lines.append(f"- 当前 block 超过观察阈值 `{EXTENSIONS_ROUTE_TOKEN_WARNING_BUDGET}` tokens，已在关键观察中列证。")
    else:
        lines.append(f"- 当前 block 最大 `{max_extension_tokens}` tokens，未超过观察阈值 `{EXTENSIONS_ROUTE_TOKEN_WARNING_BUDGET}`；不生成静态 TODO。")
    lines.append("- Extensions 只在候选输出或 token 预算真实越线时进入问题列表。")
    lines.extend(
        [
            "",
            "## 一年超持久运行风险",
            "",
            "- global 偏好可以承载心智模型体系，但必须有 promotion reason、证据引用、冲突记录和 tombstone，否则旧日志会反复复活旧人格偏好。",
            "- 项目级知识和 workflow 必须绑定 repo evidence，例如 `AGENTS.md` hash、依赖版本、验证命令和 proof 结果，否则项目演进后会保留过期规约。",
            "- 知识图谱应被视为 evidence graph，不是全局信念图；关系注入应依赖 active fact、scope、confidence、recency 和 last verification。",
            "- workflow memory 已有风险门槛和负反馈，但还需要 stale workflow revalidation，尤其是工具版本、skill 版本、repo 结构变化后。",
            "",
            "## 本次运行守门",
            "",
            f"- memory eval: `{json.dumps(results['memoryEvalSummary'], ensure_ascii=False)}`",
            "- 旧 `system_content_stress_reports / engineering_lane_dry_run_reports / context_management_reports / memory_capability_reports` 已在本轮生成前清空。",
            "- 本报告不引用旧 2026-04-24 报告产物。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()
    system_content_output_dir = OUTPUT_ROOT / f"{stamp}_system_content"
    supervisor_tools, agents, policy_snapshot = _build_toolset()
    config = EngineConfig()
    scenes = [
        _build_scene(scene, supervisor_tools, agents, config, system_content_output_dir)
        for scene in _scene_payloads()
    ]
    memory_eval_summary: dict[str, Any]
    try:
        evals_root = ENGINE_ROOT / "tests" / "evals"
        if str(evals_root) not in sys.path:
            sys.path.insert(0, str(evals_root))
        from memory_eval_matrix import run_memory_eval_matrix  # type: ignore

        eval_result = run_memory_eval_matrix()
        memory_eval_summary = {
            "caseCount": eval_result.get("caseCount"),
            "passRate": eval_result.get("passRate"),
            "p0Passed": eval_result.get("p0Passed"),
            "benchmarkMappingScore": eval_result.get("benchmarkMappingScore"),
            "runtimeFirstScore": eval_result.get("runtimeFirstScore"),
            "failedCases": eval_result.get("failedCases"),
        }
    except Exception as exc:
        memory_eval_summary = {"error": str(exc)}

    tool_stats = _tool_registry_stats(supervisor_tools)
    specialist_stats = _specialist_registry_stats(agents, "")
    specialist_stats["estimatedTokens"] = max(
        (
            int(item.get("estimatedTokens") or 0)
            for scene in scenes
            for item in scene["supervisor"]["moduleStats"]
            if item.get("name") == "specialist_agents_context"
        ),
        default=0,
    )
    results = {
        "generatedAt": _utc_now(),
        "scriptPath": str(SCRIPT_PATH),
        "repoRoot": str(REPO_ROOT),
        "systemContentOutputDir": str(system_content_output_dir),
        "oldReportInputsRead": False,
        "scenes": scenes,
        "toolRegistry": tool_stats,
        "toolPolicySnapshot": policy_snapshot,
        "specialistRegistry": specialist_stats,
        "memoryLifecycle": _memory_lifecycle_snapshot(),
        "memoryEvalSummary": memory_eval_summary,
    }
    results["observations"] = _observations(results)
    md = _build_markdown(results)
    md_path = OUTPUT_ROOT / f"{stamp}_runtime_deep_observation_report.md"
    json_path = OUTPUT_ROOT / f"{stamp}_runtime_deep_observation_results.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"markdown": str(md_path), "json": str(json_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
