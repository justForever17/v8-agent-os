import asyncio
import json
import os
import platform
import re
import string
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Union

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .models import (
    ChannelBindingPayload,
    GraphEntityPayload,
    GraphRelationPayload,
    PreferenceMutationPayload,
    PreferenceQuarantineMutationPayload,
    ProjectDescriptorPayload,
    WorkspaceBindingPayload,
    WorkflowBindingPayload,
)
from core.model_control_plane import model_control_plane
from core.memory_store import MEMORY_ROOT
from core.realtime_protocol import format_ndjson
from core.response_normalizer import extract_text_and_reasoning, normalize_tool_calls
from core.storage import MEMORY_DURABLE_POLICY_DEFAULTS, storage
from core.system_tools.baseline import build_baseline_system_tool_descriptors
from core.workspace_capability import ensure_workspace_side_effect_allowed
from core.workspace_resolution import workspace_resolution_service
from core.workspace_guard import build_workspace_path_status
from runtimes.chat.runtime import StreamFilter
from runtimes.memory.prompts import render_memory_admin_chat_prompt
from runtimes.memory.project_registry import DEFAULT_AGENTS_TEMPLATE, WorkspaceTrustRequiredError, project_registry_service
from runtimes.memory.runtime import memory_runtime
from runtimes.memory.workflow_service import WORKFLOW_MEMORY_DEFAULTS
from runtimes.rpa.default_templates import ensure_system_rpa_seed_templates


router = APIRouter()


def _update_role_binding(role: str, model_id: str | None):
    config = model_control_plane.get_config()
    roles = dict(config.get("roles") or {})
    roles[role] = str(model_id or "").strip()
    config["roles"] = roles
    model_control_plane.save_config(config)


def _get_role_binding(role: str) -> str:
    return str(model_control_plane.get_role_model_id(role) or "").strip()


class MemoryLogFilePayload(BaseModel):
    relativePath: str
    content: str


_MEMORY_DAILY_ROOT = (MEMORY_ROOT / "daily").resolve()


def _ensure_memory_daily_root() -> Path:
    _MEMORY_DAILY_ROOT.mkdir(parents=True, exist_ok=True)
    return _MEMORY_DAILY_ROOT


def _normalize_memory_log_relative_path(relative_path: str) -> str:
    normalized = str(relative_path or "").strip().replace("\\", "/").strip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="relativePath is required.")
    if normalized.endswith("/") or not normalized.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files under memory/daily are allowed.")
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise HTTPException(status_code=400, detail="Invalid relativePath.")
    return normalized


def _resolve_memory_log_path(relative_path: str) -> Path:
    normalized = _normalize_memory_log_relative_path(relative_path)
    root = _ensure_memory_daily_root()
    target = (root / normalized).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="relativePath escaped memory/daily.")
    if target.suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="Only .md files under memory/daily are allowed.")
    return target


def _memory_logs_sort_key(path: Path) -> tuple[int, int, str]:
    if path.is_dir():
        return (0, 0, path.name.lower())
    summary_rank = 0 if path.name.lower() == "summary.md" else 1
    date_rank = 0
    if path.name[:10].count("-") == 2:
        date_rank = -int(path.name[:10].replace("-", ""))
    return (1, summary_rank, f"{date_rank}:{path.name.lower()}")


def _build_memory_log_tree_node(path: Path, root: Path) -> dict | None:
    relative_path = "" if path == root else path.relative_to(root).as_posix()
    if path.is_dir():
        children = sorted(list(path.iterdir()), key=_memory_logs_sort_key)
        visible_children = []
        for child in children:
            if not child.is_dir() and child.suffix.lower() != ".md":
                continue
            child_node = _build_memory_log_tree_node(child, root)
            if child_node is not None:
                visible_children.append(child_node)
        if not visible_children:
            return None
        return {
            "id": relative_path or "daily",
            "name": path.name if path != root else "daily",
            "kind": "directory",
            "relativePath": relative_path,
            "children": visible_children,
        }
    return {
        "id": relative_path,
        "name": path.name,
        "kind": "file",
        "relativePath": relative_path,
        "children": [],
    }


def _read_memory_log_file(relative_path: str) -> dict:
    target = _resolve_memory_log_path(relative_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Memory log file not found.")
    content = target.read_text(encoding="utf-8")
    updated_at = datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "relativePath": target.relative_to(_ensure_memory_daily_root()).as_posix(),
        "content": content,
        "exists": True,
        "updatedAt": updated_at,
    }


@router.get("/agents")
async def get_agents():
    try:
        return {"agents": storage.get_all_agents()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/tool-surface")
async def get_agent_tool_surface():
    try:
        return {
            "baselineSystemTools": build_baseline_system_tool_descriptors(),
            "toolModes": {
                "recommended": "contextual_auto",
                "modes": {
                    "contextual_auto": {
                        "status": "recommended",
                        "selectorPolicy": "delegated_task_contextual_route",
                    },
                    "explicit": {
                        "status": "legacy_compatibility",
                        "selectorPolicy": "manual_candidate_pinning",
                    },
                },
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents")
async def save_agent(agent: dict = Body(...)):
    try:
        storage.save_agent(agent)
        return {"status": "success", "id": agent.get("id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    try:
        success = storage.delete_agent(agent_id)
        if not success:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/context/config")
async def get_context_config():
    try:
        return {
            "policy": storage.get_context_config() or {},
            "bindings": {
                "summary_model": _get_role_binding("summary"),
            },
            "source_map": {
                "summary_model": "models.roles.summary",
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/context/config")
async def update_context_config(config: dict = Body(...)):
    try:
        if not isinstance(config, dict) or "policy" not in config:
            raise HTTPException(status_code=400, detail="Expected payload with { policy, bindings }.")
        policy = dict(config.get("policy") or {})
        bindings = dict(config.get("bindings") or {})
        summary_model = str(bindings.get("summary_model") or "").strip()
        _update_role_binding("summary", summary_model)
        storage.save_context_config(policy)
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/config")
async def get_memory_config():
    try:
        config = storage.get_memory_config() or {}
        metadata = storage.get_memory_config_metadata()
        config.setdefault("recall_strategy", "balanced")
        config.setdefault("recall_top_k", 3)
        config.setdefault("retrieval_threshold", metadata["recommendedRetrievalThreshold"])
        config.setdefault("passive_injection_enabled", True)
        config.setdefault("passive_context_profile", "balanced")
        config.setdefault("passive_summary_enabled", True)
        config.setdefault("passive_memory_map_enabled", True)
        config.setdefault("passive_recent_activity_teaser_enabled", True)
        config.setdefault("passive_recent_activity_teaser_limit", 2)
        config.setdefault("passive_memory_map_node_limit", 4)
        config.setdefault("max_recent_days", 1)
        config.setdefault("max_context_tokens", 2000)
        config.setdefault("extraction_enabled", True)
        for key, value in MEMORY_DURABLE_POLICY_DEFAULTS.items():
            config.setdefault(key, value)
        workflow_memory = config.get("workflowMemory")
        if not isinstance(workflow_memory, dict):
            workflow_memory = {}
        config["workflowMemory"] = {**WORKFLOW_MEMORY_DEFAULTS, **workflow_memory}
        config.setdefault("graph_enabled", True)
        config.setdefault("fts_enabled", True)
        config["extraction_model"] = _get_role_binding("extraction")
        config["embedding_model"] = _get_role_binding("embedding")
        config["reranker_model"] = _get_role_binding("reranker")
        config["recommended_retrieval_threshold"] = metadata["recommendedRetrievalThreshold"]
        config["retrieval_threshold_source"] = metadata["retrievalThresholdSource"]
        config["retrieval_threshold_is_default"] = metadata["retrievalThresholdIsDefault"]
        config["durable_policy_defaults"] = metadata["durablePolicyDefaults"]
        config["durable_policy_presets"] = metadata.get("durablePolicyPresets") or {}
        config["recommended_durable_policy_preset"] = metadata.get("recommendedDurablePolicyPreset") or "balanced"
        return config
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/config")
async def update_memory_config(config: dict = Body(...)):
    try:
        next_config = dict(config or {})
        for ui_only_key in (
            "recommended_retrieval_threshold",
            "retrieval_threshold_source",
            "retrieval_threshold_is_default",
            "durable_policy_defaults",
            "durable_policy_presets",
            "recommended_durable_policy_preset",
        ):
            next_config.pop(ui_only_key, None)
        if "extraction_model" in next_config:
            extraction_model = str(next_config.get("extraction_model") or "").strip()
            _update_role_binding("extraction", "" if extraction_model.lower() == "none" else extraction_model)
            next_config.pop("extraction_model", None)
        if "embedding_model" in next_config:
            embedding_model = str(next_config.get("embedding_model") or "").strip()
            _update_role_binding("embedding", "" if embedding_model.lower() == "none" else embedding_model)
            next_config.pop("embedding_model", None)
        if "reranker_model" in next_config:
            reranker_model = str(next_config.get("reranker_model") or "").strip()
            _update_role_binding("reranker", "" if reranker_model.lower() == "none" else reranker_model)
            next_config.pop("reranker_model", None)
        if "recall_strategy" in next_config:
            recall_strategy = str(next_config.get("recall_strategy") or "balanced").strip().lower()
            next_config["recall_strategy"] = recall_strategy if recall_strategy in {"balanced", "semantic", "keyword"} else "balanced"
        if "passive_context_profile" in next_config:
            profile = str(next_config.get("passive_context_profile") or "balanced").strip().lower()
            next_config["passive_context_profile"] = profile if profile in {"light", "balanced", "detailed"} else "balanced"
        if "recall_top_k" in next_config:
            try:
                next_config["recall_top_k"] = max(1, min(int(next_config.get("recall_top_k") or 3), 10))
            except (TypeError, ValueError):
                next_config["recall_top_k"] = 3
        if "retrieval_threshold" in next_config:
            try:
                threshold = float(next_config.get("retrieval_threshold") or 0.0)
            except (TypeError, ValueError):
                threshold = 0.0
            next_config["retrieval_threshold"] = max(0.0, min(threshold, 1.0))
        for key, default in (
            ("passive_recent_activity_teaser_limit", 2),
            ("passive_memory_map_node_limit", 4),
            ("preference_importance_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["preference_importance_threshold"]),
            ("knowledge_importance_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["knowledge_importance_threshold"]),
            ("global_knowledge_importance_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["global_knowledge_importance_threshold"]),
            ("global_operational_importance_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_importance_threshold"]),
        ):
            if key in next_config:
                try:
                    upper_bound = 12 if key == "passive_recent_activity_teaser_limit" else 12 if key == "passive_memory_map_node_limit" else 100
                    lower_bound = 1 if key in {"passive_recent_activity_teaser_limit", "passive_memory_map_node_limit"} else 0
                    next_config[key] = max(lower_bound, min(int(next_config.get(key) or default), upper_bound))
                except (TypeError, ValueError):
                    next_config[key] = default
        for key, default in (
            ("preference_confidence_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["preference_confidence_threshold"]),
            ("knowledge_confidence_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["knowledge_confidence_threshold"]),
            ("global_knowledge_confidence_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["global_knowledge_confidence_threshold"]),
            ("global_operational_confidence_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_confidence_threshold"]),
        ):
            if key in next_config:
                try:
                    next_config[key] = max(0.0, min(float(next_config.get(key) or default), 1.0))
                except (TypeError, ValueError):
                    next_config[key] = default
        if "workflowMemory" in next_config:
            workflow_memory = next_config.get("workflowMemory")
            if not isinstance(workflow_memory, dict):
                workflow_memory = {}
            normalized_workflow = {**WORKFLOW_MEMORY_DEFAULTS, **workflow_memory}
            for key in (
                "enabled",
                "hintInjectionEnabled",
                "progressiveHintsEnabled",
                "errorfulSuccessRequiresUserAcceptance",
                "quarantineOnNegativeFeedback",
                "requireApprovalForSideEffects",
            ):
                normalized_workflow[key] = bool(normalized_workflow.get(key))
            for key, default, minimum, maximum in (
                ("minSuccessCount", WORKFLOW_MEMORY_DEFAULTS["minSuccessCount"], 1, 10),
                ("maxInjectedHints", WORKFLOW_MEMORY_DEFAULTS["maxInjectedHints"], 0, 5),
                ("maxHintChars", WORKFLOW_MEMORY_DEFAULTS["maxHintChars"], 240, 2400),
                ("maxActiveWorkflowGuidesPerRun", WORKFLOW_MEMORY_DEFAULTS["maxActiveWorkflowGuidesPerRun"], 0, 10),
            ):
                try:
                    normalized_workflow[key] = max(minimum, min(int(normalized_workflow.get(key) or default), maximum))
                except (TypeError, ValueError):
                    normalized_workflow[key] = default
            if not isinstance(normalized_workflow.get("riskTierActivationPolicy"), dict):
                normalized_workflow["riskTierActivationPolicy"] = WORKFLOW_MEMORY_DEFAULTS["riskTierActivationPolicy"]
            next_config["workflowMemory"] = normalized_workflow
        storage.save_memory_config(next_config)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/preferences")
async def get_memory_preferences():
    try:
        return memory_runtime.get_preference_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/memory/preferences")
async def upsert_memory_preference(payload: PreferenceMutationPayload):
    try:
        value = (payload.value or "").strip()
        if not payload.key.strip():
            raise HTTPException(status_code=400, detail="Preference key is required.")
        if not value:
            raise HTTPException(status_code=400, detail="Preference value is required.")
        memory_runtime.upsert_preference(
            key=payload.key.strip(),
            value=value,
            scope=(payload.scope or "global").strip() or "global",
        )
        return {"updated": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/preferences")
async def delete_memory_preference(payload: PreferenceMutationPayload):
    try:
        deleted = memory_runtime.delete_preference(
            key=payload.key.strip(),
            scope=(payload.scope or "global").strip() or "global",
        )
        return {"deleted": deleted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/preferences/quarantine/restore")
async def restore_quarantined_global_preference(payload: PreferenceQuarantineMutationPayload):
    try:
        restored = memory_runtime.restore_global_preference_quarantine(record_id=payload.record_id)
        return {"restored": bool(restored), "item": restored}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/preferences/quarantine")
async def delete_quarantined_global_preference(payload: PreferenceQuarantineMutationPayload):
    try:
        deleted = memory_runtime.delete_global_preference_quarantine(record_id=payload.record_id)
        return {"deleted": deleted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/dashboard")
async def get_memory_dashboard():
    try:
        return memory_runtime.get_dashboard()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/dashboard")
async def clear_memory_dashboard_diagnostics():
    try:
        return memory_runtime.clear_diagnostics()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/logs/tree")
async def get_memory_logs_tree():
    try:
        root = _ensure_memory_daily_root()
        children = sorted(list(root.iterdir()), key=_memory_logs_sort_key)
        tree = []
        for child in children:
            if not child.is_dir() and child.suffix.lower() != ".md":
                continue
            node = _build_memory_log_tree_node(child, root)
            if node is not None:
                tree.append(node)
        return {
            "tree": tree
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/logs/file")
async def get_memory_logs_file(relative_path: str):
    try:
        return _read_memory_log_file(relative_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/memory/logs/file")
async def save_memory_logs_file(payload: MemoryLogFilePayload):
    try:
        target = _resolve_memory_log_path(payload.relativePath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.content or "", encoding="utf-8")
        return _read_memory_log_file(payload.relativePath)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/logs/file")
async def delete_memory_logs_file(relative_path: str):
    try:
        target = _resolve_memory_log_path(relative_path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Memory log file not found.")
        target.unlink()
        return {"deleted": True, "relativePath": relative_path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/workflows")
async def list_memory_workflow_candidates(
    status: str = None,
    q: str = None,
    limit: int = 50,
    workflow_class: str = Query(default=None, alias="class"),
    proof_backed: str = Query(default=None, alias="proofBacked"),
    verification_status: str = Query(default=None, alias="verificationStatus"),
    source_runtime: str = Query(default=None, alias="sourceRuntime"),
):
    try:
        proof_filter = None
        if proof_backed is not None:
            proof_filter = str(proof_backed).strip().lower() in {"1", "true", "yes"}
        items = memory_runtime.list_workflow_candidates(
            status=status,
            query=q,
            limit=limit,
            workflow_class=workflow_class,
            proof_backed=proof_filter,
            verification_status=verification_status,
            source_runtime=source_runtime,
        )
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/workflows/{candidate_id}")
async def get_memory_workflow_candidate(candidate_id: str):
    try:
        candidate = memory_runtime.get_workflow_candidate(candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Workflow candidate not found")
        return candidate
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/memory/workflows/{candidate_id}")
async def update_memory_workflow_candidate(candidate_id: str, updates: dict = Body(...)):
    try:
        return memory_runtime.update_workflow_candidate(candidate_id, updates)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/workflows/{candidate_id}")
async def delete_memory_workflow_candidate(candidate_id: str):
    try:
        deleted = memory_runtime.delete_workflow_candidate(candidate_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Workflow candidate not found")
        return {"deleted": True, "id": candidate_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/workflows/{candidate_id}/merge")
async def merge_memory_workflow_candidates(candidate_id: str, payload: dict = Body(...)):
    try:
        source_ids = payload.get("sourceIds") or payload.get("source_ids") or []
        if not isinstance(source_ids, list):
            raise HTTPException(status_code=400, detail="sourceIds must be a list")
        return memory_runtime.merge_workflow_candidates(target_id=candidate_id, source_ids=[str(item) for item in source_ids])
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/workflows/{candidate_id}/episodes")
async def list_memory_workflow_episodes(candidate_id: str, limit: int = 50):
    try:
        return {
            "items": memory_runtime.list_workflow_episodes(candidate_id=candidate_id, limit=limit),
            "candidateId": candidate_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/workflows/{candidate_id}/hint-events")
async def list_memory_workflow_hint_events(candidate_id: str, limit: int = 50):
    try:
        return {
            "items": memory_runtime.list_workflow_hint_events(candidate_id=candidate_id, limit=limit),
            "candidateId": candidate_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/workflows/{candidate_id}/hint-events")
async def record_memory_workflow_hint_event(candidate_id: str, payload: dict = Body(...)):
    try:
        return memory_runtime.record_workflow_hint_event(
            candidate_id=candidate_id,
            query=str(payload.get("query") or ""),
            hint=payload.get("hint") if isinstance(payload.get("hint"), dict) else {},
            session_id=payload.get("sessionId") or payload.get("session_id"),
            run_id=payload.get("runId") or payload.get("run_id"),
            outcome=str(payload.get("outcome") or "injected"),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/graph/entity/{entity}")
async def get_graph_entity(entity: str):
    try:
        return {"entity": entity, "relations": memory_runtime.query_entity(entity=entity)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/graph/multi-hop/{entity}")
async def get_graph_multi_hop(entity: str, hops: int = 2):
    try:
        return {"start": entity, "hops": hops, "paths": memory_runtime.query_multi_hop(entity=entity, hops=hops)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/search")
async def memory_fts_search(q: str, scope: str = None):
    try:
        results = memory_runtime.search_full_text(query=q, scope=scope, limit=20)
        valid_results = [
            item
            for item in results
            if str(item.get("scope") or "global").strip() == "global"
            or str(item.get("scope") or "").startswith(("project:", "channel:"))
        ]
        return {"query": q, "results": valid_results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/recall-preview")
async def memory_recall_preview(q: str, scope: str = None, latency_tier: str = "balanced"):
    try:
        preview = memory_runtime.preview_unified_recall(query=q, scope=scope, limit=8)
        injection_pack = memory_runtime.build_memory_injection_pack(
            user_query=q,
            scope=scope or "global",
            latency_tier=latency_tier,
            target_role="diagnostic_preview",
        )
        metadata = storage.get_memory_config_metadata()
        return {
            **preview,
            "memoryInjectionPack": injection_pack,
            "threshold_source": metadata["retrievalThresholdSource"],
            "recommended_retrieval_threshold": metadata["recommendedRetrievalThreshold"],
            "retrieval_threshold_is_default": metadata["retrievalThresholdIsDefault"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/knowledge")
async def get_all_knowledge(scope: str = None, limit: int = 50, status: str = "active"):
    try:
        results = memory_runtime.list_knowledge(scope=scope, limit=limit, status=status)
        return {"items": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/knowledge-health")
async def get_knowledge_health():
    try:
        return {
            "projection": memory_runtime.get_projection_health(),
            "graph": memory_runtime.get_graph_stats(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/knowledge-resolution-candidates")
async def get_knowledge_resolution_candidates(limit: int = 100):
    try:
        items = memory_runtime.list_knowledge_resolution_candidates(limit=limit)
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/knowledge-resolution-candidates/{candidate_id}/resolve")
async def resolve_knowledge_resolution_candidate(candidate_id: str, body: dict = Body(...)):
    try:
        resolution = str(body.get("resolution") or "").strip()
        if not resolution:
            raise HTTPException(status_code=400, detail="resolution is required")
        return memory_runtime.resolve_knowledge_candidate(candidate_id=candidate_id, resolution=resolution)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/knowledge-cleanup-plans")
async def create_knowledge_cleanup_plan(body: dict = Body(default={})):
    """Create a review-only plan; this endpoint never deletes knowledge."""
    try:
        return memory_runtime.create_knowledge_cleanup_plan(
            unused_days=int(body.get("unusedDays") or 180),
            low_evidence_confidence=float(body.get("lowEvidenceConfidence") or 0.55),
            max_candidates=int(body.get("maxCandidates") or 1000),
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/graph/all")
async def get_full_graph(limit: int = 100):
    try:
        return memory_runtime.get_full_graph(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/graph/search")
async def search_graph_entities(keyword: str, limit: int = 20):
    try:
        return {"items": memory_runtime.search_entities(keyword=keyword, limit=limit), "keyword": keyword}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/graph/entity")
async def add_graph_entity(payload: GraphEntityPayload):
    try:
        memory_runtime.add_entity(
            name=payload.name,
            entity_type=payload.entity_type,
            maintainer_source=payload.maintainer_source or "human_admin",
            confidence=payload.confidence or 1.0,
        )
        return {"created": True, "name": payload.name.lower(), "entityType": payload.entity_type}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/graph/entity")
async def remove_graph_entity(payload: GraphEntityPayload):
    try:
        deleted = memory_runtime.delete_entity(name=payload.name, scope=payload.scope)
        return {"deleted": deleted, "name": payload.name.lower()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/graph/relation")
async def add_graph_relation(payload: GraphRelationPayload):
    try:
        if not payload.scope or not payload.source_fact_ids:
            raise HTTPException(status_code=400, detail="scope and sourceFactIds are required")
        memory_runtime.add_relation(
            subject=payload.subject,
            predicate=payload.predicate,
            object_name=payload.object_name,
            scope=payload.scope,
            source_fact_ids=payload.source_fact_ids,
            confidence=payload.confidence or 1.0,
            maintainer_source=payload.maintainer_source or "human_admin",
        )
        return {
            "created": True,
            "relation": {
                "subject": payload.subject.lower(),
                "predicate": payload.predicate.upper(),
                "object": payload.object_name.lower(),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/graph/relation")
async def remove_graph_relation(payload: GraphRelationPayload):
    try:
        deleted = memory_runtime.delete_relation(
            subject=payload.subject,
            predicate=payload.predicate,
            object_name=payload.object_name,
            scope=payload.scope,
        )
        return {
            "deleted": deleted,
            "relation": {
                "subject": payload.subject.lower(),
                "predicate": payload.predicate.upper(),
                "object": payload.object_name.lower(),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/knowledge/{fact_id}")
async def delete_knowledge_item(fact_id: str):
    try:
        return {"deleted": memory_runtime.delete_knowledge(fact_id=fact_id), "id": fact_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/memory/knowledge/{fact_id}")
async def update_knowledge_item(fact_id: str, body: dict = Body(...)):
    try:
        new_fact = body.get("fact", "")
        category = body.get("category")
        scope = body.get("scope")
        if not new_fact:
            raise HTTPException(status_code=400, detail="fact is required")
        ok = memory_runtime.update_knowledge(
            fact_id=fact_id,
            new_fact=new_fact,
            category=category,
            scope=scope,
            maintainer_source=body.get("maintainerSource") or body.get("maintainer_source") or "human_admin",
            confidence=body.get("confidence"),
        )
        return {"updated": ok, "id": fact_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/knowledge/{fact_id}/restore")
async def restore_knowledge_item(fact_id: str):
    try:
        restored = memory_runtime.restore_knowledge(fact_id=fact_id)
        return {"restored": restored, "id": fact_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/knowledge/{fact_id}/revalidate")
async def revalidate_knowledge_item(fact_id: str, body: dict | None = Body(default=None)):
    try:
        body = body or {}
        maintainer_source = body.get("maintainerSource") or body.get("maintainer_source") or "human_admin"
        revalidated = memory_runtime.revalidate_knowledge(
            fact_id=fact_id,
            maintainer_source=maintainer_source,
        )
        return {"revalidated": revalidated, "id": fact_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects")
async def list_projects():
    try:
        return {
            "defaultProjectId": storage.get_projects_registry().get("defaultProjectId"),
            "mainWorkspacePath": workspace_resolution_service.get_main_workspace_path(),
            "projects": [item.model_dump(by_alias=True, exclude_none=True) for item in project_registry_service.list_projects()],
            "workspacePresentations": project_registry_service.list_workspace_presentations(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspace-presentations")
async def list_workspace_presentations():
    try:
        return {"items": project_registry_service.list_workspace_presentations()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/workspace-presentations")
async def update_workspace_presentation(payload: dict = Body(...)):
    try:
        workspace_path = str(payload.get("workspacePath") or payload.get("workspace_path") or "").strip()
        updates = {
            key: payload[key]
            for key in ("displayName", "display_name", "pinned")
            if key in payload
        }
        if not updates:
            raise HTTPException(status_code=400, detail="workspace_presentation_update_required")
        return project_registry_service.patch_workspace_presentation(workspace_path, updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _folder_display_name(path: Path) -> str:
    raw = str(path)
    name = path.name
    if name:
        return name
    return raw.rstrip("\\/") or raw


def _safe_resolve_folder(value: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="path_required")
    return Path(raw).expanduser().resolve(strict=False)


def _can_create_in_folder(path: Path) -> bool:
    try:
        return path.exists() and path.is_dir() and os.access(path, os.W_OK)
    except Exception:
        return False


WORKSPACE_RULES_BUDGET_TOKENS = 10_000


def _estimate_prompt_tokens(text: str) -> int:
    raw = str(text or "")
    if not raw:
        return 0
    cjk_count = 0
    non_cjk_visible = 0
    for char in raw:
        codepoint = ord(char)
        if (
            0x4E00 <= codepoint <= 0x9FFF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        ):
            cjk_count += 1
        elif not char.isspace():
            non_cjk_visible += 1
    return cjk_count + ((non_cjk_visible + 3) // 4)


def _rules_budget_diagnostics(content: str, *, save_rejected: bool = False) -> dict:
    estimated = _estimate_prompt_tokens(content)
    return {
        "estimatedTokens": estimated,
        "budgetTokens": WORKSPACE_RULES_BUDGET_TOKENS,
        "truncated": estimated > WORKSPACE_RULES_BUDGET_TOKENS,
        "saveRejected": save_rejected,
        "omittedReason": "workspace_agents_md_budget_exceeded" if estimated > WORKSPACE_RULES_BUDGET_TOKENS else "",
    }


def _workspace_rules_path(workspace_path: str) -> Path:
    return Path(str(workspace_path or "").strip()).expanduser().resolve(strict=False) / ".agents" / "rules" / "AGENTS.md"


def _workspace_rules_context(payload: dict) -> dict:
    return {
        "runtime_kind": "chat",
        "session_id": str(payload.get("sessionId") or payload.get("session_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspaceId") or payload.get("workspace_id") or "").strip() or None,
        "workspace_path": str(payload.get("workspacePath") or payload.get("workspace_path") or "").strip() or None,
        "project_id": str(payload.get("projectId") or payload.get("project_id") or "").strip() or None,
    }


def _read_workspace_rules_response(workspace_path: str, binding: dict) -> dict:
    normalized_workspace = str(Path(str(workspace_path or "")).expanduser().resolve(strict=False))
    rules_path = _workspace_rules_path(normalized_workspace)
    exists = rules_path.is_file()
    content = rules_path.read_text(encoding="utf-8") if exists else ""
    return {
        "workspacePath": normalized_workspace,
        "path": str(rules_path),
        "exists": exists,
        "content": content,
        "suggestedContent": DEFAULT_AGENTS_TEMPLATE,
        "workspaceStatus": build_workspace_path_status(normalized_workspace),
        "budgetDiagnostics": _rules_budget_diagnostics(content),
        "workspaceBinding": binding,
    }


def _folder_node(
    path: Path,
    *,
    root: bool = False,
    children: list[dict] | None = None,
    has_more: bool = False,
    cursor: str | None = None,
) -> dict:
    node = {
        "root": root,
        "path": str(path),
        "name": _folder_display_name(path),
        "children": children or [],
        "hasMore": has_more,
        "canSelect": True,
        "canCreate": _can_create_in_folder(path),
    }
    if cursor:
        node["cursor"] = cursor
    return node


def _common_folder_roots() -> list[Path]:
    candidates: list[Path] = []
    main_workspace = workspace_resolution_service.get_main_workspace_path()
    if main_workspace:
        candidates.append(Path(main_workspace).expanduser())
    home = Path.home()
    candidates.append(home)
    current_platform = platform.system().lower()
    if current_platform == "windows":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                candidates.append(drive)
    elif current_platform == "darwin":
        candidates.extend([Path("/Users"), Path("/Volumes"), Path("/")])
    else:
        candidates.extend([Path("/home"), Path("/mnt"), Path("/media"), Path("/")])

    seen: set[str] = set()
    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=False)
        except Exception:
            continue
        key = str(resolved).lower() if platform.system().lower() == "windows" else str(resolved)
        if key in seen or not resolved.exists() or not resolved.is_dir():
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def _list_child_folders(path: Path, *, max_children: int, cursor: int = 0) -> tuple[list[dict], bool, str | None]:
    children: list[Path] = []
    try:
        for child in path.iterdir():
            try:
                if child.is_dir():
                    children.append(child)
            except Exception:
                continue
    except Exception as exc:
        raise HTTPException(status_code=403, detail=f"directory_unreadable: {exc}")
    children.sort(key=lambda item: item.name.lower())
    start = max(0, int(cursor or 0))
    bounded = children[start:start + max_children]
    next_offset = start + len(bounded)
    has_more = next_offset < len(children)
    return [_folder_node(child) for child in bounded], has_more, str(next_offset) if has_more else None


def _build_folder_tree(path: Path, *, max_depth: int, max_children: int, cursor: int = 0) -> dict:
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="directory_not_found")
    children, has_more, next_cursor = _list_child_folders(path, max_children=max_children, cursor=cursor)
    if max_depth > 1:
        next_children: list[dict] = []
        for child in children:
            child_path = Path(str(child.get("path") or ""))
            try:
                nested_children, nested_has_more, nested_cursor = _list_child_folders(child_path, max_children=max_children)
                nested_patch = {**child, "children": nested_children, "hasMore": nested_has_more}
                if nested_cursor:
                    nested_patch["cursor"] = nested_cursor
                next_children.append(nested_patch)
            except HTTPException:
                next_children.append(child)
        children = next_children
    return _folder_node(path, root=True, children=children, has_more=has_more, cursor=next_cursor)


@router.get("/workspace/folders")
async def list_workspace_folders(
    path: str = Query("", alias="path"),
    max_depth: int = Query(1, alias="maxDepth"),
    max_children: int = Query(80, alias="maxChildren"),
    cursor: str = Query("", alias="cursor"),
):
    try:
        bounded_depth = max(0, min(int(max_depth or 1), 2))
        bounded_children = max(1, min(int(max_children or 80), 120))
        cursor_offset = max(0, int(str(cursor or "0").strip() or "0"))
        if str(path or "").strip():
            root_path = _safe_resolve_folder(path)
            return {
                "platform": platform.system().lower() or "unknown",
                "root": _build_folder_tree(root_path, max_depth=bounded_depth, max_children=bounded_children, cursor=cursor_offset),
            }
        return {
            "platform": platform.system().lower() or "unknown",
            "roots": [_folder_node(root, root=True) for root in _common_folder_roots()],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workspace/folders")
async def create_workspace_folder(payload: dict = Body(...)):
    try:
        parent = _safe_resolve_folder(str(payload.get("parentPath") or payload.get("path") or ""))
        folder_name = str(payload.get("folderName") or payload.get("name") or "").strip()
        if not folder_name:
            raise HTTPException(status_code=400, detail="folder_name_required")
        if re.search(r"[\\/:*?\"<>|\x00-\x1F]", folder_name):
            raise HTTPException(status_code=400, detail="folder_name_invalid")
        if not parent.exists() or not parent.is_dir():
            raise HTTPException(status_code=404, detail="parent_directory_not_found")
        target = (parent / folder_name).resolve(strict=False)
        try:
            target.relative_to(parent.resolve(strict=False))
        except ValueError:
            raise HTTPException(status_code=400, detail="folder_path_escape")
        target.mkdir(parents=False, exist_ok=False)
        return {
            "platform": platform.system().lower() or "unknown",
            "folder": _folder_node(target, root=True),
        }
    except HTTPException:
        raise
    except FileExistsError:
        raise HTTPException(status_code=409, detail="folder_already_exists")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"permission_denied: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspace/agents-rules")
async def get_workspace_agents_rules(
    workspace_path: str = Query("", alias="workspacePath"),
    workspace_id: str | None = Query(default=None, alias="workspaceId"),
    project_id: str | None = Query(default=None, alias="projectId"),
    session_id: str | None = Query(default=None, alias="sessionId"),
):
    payload = {
        "workspacePath": workspace_path,
        "workspaceId": workspace_id,
        "projectId": project_id,
        "sessionId": session_id,
    }
    context = _workspace_rules_context(payload)
    preflight = ensure_workspace_side_effect_allowed(
        context,
        runtime_kind="chat",
        operation="workspace_rules",
        subject=str(workspace_path or ""),
    )
    if not preflight.get("ok"):
        raise HTTPException(status_code=403, detail=preflight)
    binding = dict(preflight.get("binding") or {})
    workspace_root = str(binding.get("activeWorkspaceRoot") or context.get("workspace_path") or workspace_resolution_service.get_main_workspace_path())
    return _read_workspace_rules_response(workspace_root, binding)


@router.post("/workspace/agents-rules")
async def save_workspace_agents_rules(payload: dict = Body(...)):
    context = _workspace_rules_context(payload)
    preflight = ensure_workspace_side_effect_allowed(
        context,
        runtime_kind="chat",
        operation="workspace_rules",
        subject=str(context.get("workspace_path") or ""),
    )
    if not preflight.get("ok"):
        raise HTTPException(status_code=403, detail=preflight)
    binding = dict(preflight.get("binding") or {})
    workspace_root = str(binding.get("activeWorkspaceRoot") or context.get("workspace_path") or workspace_resolution_service.get_main_workspace_path())
    rules_path = _workspace_rules_path(workspace_root)
    if payload.get("ensureOnly") is True:
        rules_path.parent.mkdir(parents=True, exist_ok=True)
        (rules_path.parent.parent / "skills").mkdir(parents=True, exist_ok=True)
        if not rules_path.exists():
            rules_path.write_text(DEFAULT_AGENTS_TEMPLATE, encoding="utf-8")
        return _read_workspace_rules_response(workspace_root, binding)

    content = str(payload.get("content") if isinstance(payload.get("content"), str) else DEFAULT_AGENTS_TEMPLATE)
    diagnostics = _rules_budget_diagnostics(content, save_rejected=True)
    if diagnostics["estimatedTokens"] > WORKSPACE_RULES_BUDGET_TOKENS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"AGENTS.md exceeds {WORKSPACE_RULES_BUDGET_TOKENS} estimated tokens ({diagnostics['estimatedTokens']}).",
                "budgetDiagnostics": diagnostics,
            },
        )
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    (rules_path.parent.parent / "skills").mkdir(parents=True, exist_ok=True)
    rules_path.write_text(content, encoding="utf-8")
    return _read_workspace_rules_response(workspace_root, binding)


@router.post("/projects")
async def create_project(payload: ProjectDescriptorPayload):
    try:
        project = project_registry_service.save_project(payload.model_dump(by_alias=True, exclude_none=True))
        ensure_system_rpa_seed_templates()
        return project.model_dump(by_alias=True, exclude_none=True)
    except WorkspaceTrustRequiredError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        if str(e) == "workspace_trust_state_invalid":
            raise HTTPException(status_code=400, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    try:
        project = project_registry_service.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return project.model_dump(by_alias=True, exclude_none=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_id}")
async def patch_project(project_id: str, updates: dict = Body(...)):
    try:
        project = project_registry_service.patch_project(project_id, updates)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return project.model_dump(by_alias=True, exclude_none=True)
    except WorkspaceTrustRequiredError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        if str(e) == "workspace_trust_state_invalid":
            raise HTTPException(status_code=400, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    try:
        ok = project_registry_service.delete_project(project_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return {"status": "success", "projectId": project_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/bind-workspace")
async def bind_project_workspace(project_id: str, payload: WorkspaceBindingPayload):
    try:
        project = project_registry_service.bind_workspace(
            project_id=project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            workspace_trust_state=payload.workspace_trust_state,
            workspace_trust_source=payload.workspace_trust_source,
            source=payload.source or "admin_selected",
            confidence=payload.confidence or 1.0,
        )
        ensure_system_rpa_seed_templates()
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return project.model_dump(by_alias=True, exclude_none=True)
    except WorkspaceTrustRequiredError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        if str(e) == "workspace_trust_state_invalid":
            raise HTTPException(status_code=400, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/bind-channel")
async def bind_project_channel(project_id: str, payload: ChannelBindingPayload):
    try:
        project = project_registry_service.bind_channel(
            project_id=project_id,
            channel_type=payload.channel_type,
            remote_id=payload.remote_id,
            mode=payload.mode or "default",
        )
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return project.model_dump(by_alias=True, exclude_none=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/bind-workflow")
async def bind_project_workflow(project_id: str, payload: WorkflowBindingPayload):
    try:
        project = project_registry_service.bind_workflow(
            project_id=project_id,
            workflow_id=payload.workflow_id,
            mode=payload.mode or "default",
        )
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return project.model_dump(by_alias=True, exclude_none=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AdminChatRequest(BaseModel):
    message: str


@router.post("/memory/admin-chat")
async def memory_admin_chat(request: AdminChatRequest):
    from core.knowledge_db import knowledge_db
    from core.memory_router import MemoryRouter
    from langchain_core.messages import AIMessage as _AIMessage
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def search_knowledge(query: str, scope: str = "") -> str:
        results = memory_runtime.query_knowledge(query=query, scope=scope or None, limit=10)
        if not results:
            return f"No results for '{query}'."
        lines = [f"Found {len(results)} items:"]
        for result in results:
            lines.append(f"  [{result.get('scope','?')}] {result.get('fact','')} (id: {result.get('id','?')})")
        return "\n".join(lines)

    @lc_tool
    def get_knowledge_stats() -> str:
        try:
            summary = memory_runtime.get_preference_summary()
            prefs = summary["preferences"]
            total_prefs = summary["total"]
            pref_scopes = ", ".join(prefs.keys())
            knowledge_count = memory_runtime.get_knowledge_count()
            with knowledge_db._conn() as conn:
                scope_counts = conn.execute("SELECT scope, COUNT(*) FROM knowledge WHERE status = 'active' GROUP BY scope").fetchall()
                scope_breakdown = ", ".join(f"{row[0]}: {row[1]}" for row in scope_counts) if scope_counts else "None"
            return (
                f"Knowledge Base: {knowledge_count} total active entries. Breakdown by scope: {scope_breakdown}.\n"
                f"User Preferences: {total_prefs} total items across scopes: {pref_scopes}."
            )
        except Exception as e:
            return f"Error getting stats: {e}"

    @lc_tool
    def get_graph_stats() -> str:
        stats = memory_runtime.get_graph_stats()
        top = ", ".join(f"{entity['name']}({entity['degree']})" for entity in stats.get("top_entities", [])[:5])
        return f"Graph stats: {stats['entities']} entities, {stats['relations']} relations.\nTop entities: {top or 'none'}"

    @lc_tool
    def get_recent_logs(days: int = 2) -> str:
        return memory_runtime.get_recent_logs(days=days) or "No recent logs found."

    @lc_tool
    def query_entity(entity: str, scope: str = "global") -> str:
        relations = memory_runtime.query_entity(entity=entity, scopes=[scope, "global"])
        if not relations:
            return f"No graph relations found for '{entity}'."
        lines = [f"Relations for '{entity}':"]
        for relation in relations:
            lines.append(f"  ({relation['subject']}) -[{relation['predicate']}]-> ({relation['object']})")
        return "\n".join(lines)

    @lc_tool
    def delete_graph_relation(subject: str, predicate: str, object: str, scope: str = "global") -> str:
        success = memory_runtime.delete_relation(
            subject=subject,
            predicate=predicate,
            object_name=object,
            scope=scope,
        )
        if success:
            return f"Successfully deleted relation: ({subject}) -[{predicate}]-> ({object})"
        return f"Relation not found or could not be deleted: ({subject}) -[{predicate}]-> ({object})"

    @lc_tool
    def delete_graph_entities(entities: Union[str, List[str]], scope: str = "global") -> str:
        if isinstance(entities, str):
            entities = [entities]
        results = []
        for entity in entities:
            success = memory_runtime.delete_entity(name=entity, scope=scope)
            if success:
                results.append(f"Successfully deleted entity '{entity}' and all associated relations.")
            else:
                results.append(f"Entity '{entity}' not found or could not be deleted.")
        return "\n".join(results)

    @lc_tool
    def add_graph_relation(
        subject: str,
        predicate: str,
        object: str,
        scope: str,
        source_fact_ids: List[str],
    ) -> str:
        memory_runtime.add_relation(
            subject=subject,
            predicate=predicate,
            object_name=object,
            scope=scope,
            source_fact_ids=source_fact_ids,
            maintainer_source="human_admin",
        )
        return f"Successfully added relation: ({subject}) -[{predicate}]-> ({object})"

    @lc_tool
    def add_graph_entity(entity_name: str, entity_type: str = "concept") -> str:
        knowledge_db.add_entity(entity_name, entity_type, maintainer_source="human_admin")
        return f"Successfully added entity '{entity_name}' of type '{entity_type}'."

    @lc_tool
    def add_knowledge(fact: str, category: str = "general", scope: str = "global") -> str:
        fact_id = memory_runtime.add_knowledge(
            fact=fact,
            category=category,
            scope=scope,
            maintainer_source="human_admin",
        )
        return f"Successfully added new knowledge with ID: {fact_id}"

    @lc_tool
    def update_knowledge(fact_id: str, new_fact: str) -> str:
        memory_runtime.update_knowledge(fact_id=fact_id, new_fact=new_fact, maintainer_source="human_admin")
        return f"Successfully updated knowledge fact {fact_id}."

    @lc_tool
    def delete_knowledge(fact_id: str) -> str:
        memory_runtime.delete_knowledge(fact_id=fact_id)
        return f"Successfully deleted knowledge fact {fact_id}."

    @lc_tool
    def search_graph_entities(keyword: str, limit: int = 20) -> str:
        entities = knowledge_db.search_entities(keyword, limit)
        if not entities:
            return f"No entities found matching '{keyword}'."
        lines = [f"Found {len(entities)} entities matching '{keyword}':"]
        for entity in entities:
            lines.append(f"  - {entity['name']} (type: {entity['type']})")
        return "\n".join(lines)

    @lc_tool
    def get_isolated_entities(limit: int = 50) -> str:
        entities = knowledge_db.get_isolated_entities(limit)
        if not entities:
            return "No isolated entities found in the graph."
        lines = [f"Found {len(entities)} isolated entities:"]
        for entity in entities:
            lines.append(f"  - {entity['name']} (type: {entity['type']})")
        return "\n".join(lines)

    admin_tools = [
        search_knowledge,
        get_knowledge_stats,
        get_graph_stats,
        get_recent_logs,
        query_entity,
        search_graph_entities,
        get_isolated_entities,
        delete_graph_relation,
        delete_graph_entities,
        add_graph_relation,
        add_graph_entity,
        add_knowledge,
        update_knowledge,
        delete_knowledge,
    ]

    try:
        router_instance = MemoryRouter()
        base_llm = router_instance.get_extractor_llm()
        llm = base_llm.bind_tools(admin_tools)
        raw_llm = base_llm
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LLM init failed: {e}")

    system_prompt = render_memory_admin_chat_prompt()

    async def stream_admin_chat():
        import re as _re

        try:
            messages = [SystemMessage(content=system_prompt), HumanMessage(content=request.message)]
            tool_map = {tool.name: tool for tool in admin_tools}
            text_filter = StreamFilter(["NONE", "None", "null", "```json", "```"])

            for _ in range(10):
                response = await asyncio.get_event_loop().run_in_executor(None, lambda: llm.invoke(messages))
                parsed_tool_calls = normalize_tool_calls(getattr(response, "tool_calls", []))
                is_native_tool_call = bool(parsed_tool_calls)

                if not parsed_tool_calls and isinstance(response.content, str) and ("DSML" in response.content or "invoke name=" in response.content):
                    matches = _re.finditer(r'invoke\s+name="([^"]+)"\s*>(.*?)</\s*\|?[ \t]*DSML', response.content, _re.DOTALL)
                    for match in matches:
                        tool_name = match.group(1).strip()
                        tool_args_str = match.group(2).strip()
                        tool_args = {}
                        if tool_args_str:
                            try:
                                tool_args = json.loads(tool_args_str)
                            except Exception:
                                pass
                        parsed_tool_calls.append({"name": tool_name, "args": tool_args, "id": f"call_{uuid.uuid4().hex[:12]}"})
                    if not parsed_tool_calls:
                        for match in _re.finditer(r'invoke\s+name="([^"]+)"', response.content):
                            parsed_tool_calls.append({"name": match.group(1).strip(), "args": {}, "id": f"call_{uuid.uuid4().hex[:12]}"})

                if not parsed_tool_calls:
                    break

                _, reasoning_text = extract_text_and_reasoning(response)
                if reasoning_text:
                    yield format_ndjson({"type": "thinking", "token": reasoning_text + "\n"})

                if is_native_tool_call:
                    messages.append(response)
                else:
                    messages.append(
                        _AIMessage(
                            content="",
                            tool_calls=[{"name": call["name"], "args": call["args"], "id": call["id"]} for call in parsed_tool_calls],
                        )
                    )

                for tool_call in parsed_tool_calls:
                    tool_name = tool_call.get("name")
                    tool_args = tool_call.get("args", {})
                    tool_call_id = tool_call.get("id", f"call_{uuid.uuid4().hex[:12]}")
                    yield format_ndjson({"type": "tool_call", "name": tool_name, "arguments": tool_args})

                    if tool_name in tool_map:
                        try:
                            result = await asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda current_tool=tool_map[tool_name], args=tool_args: current_tool.invoke(args),
                            )
                        except Exception as tool_exc:
                            result = f"Error: {tool_exc}"
                    else:
                        result = f"Unknown tool: {tool_name}"

                    result_str = str(result)
                    yield format_ndjson({"type": "tool_result", "name": tool_name, "result": result_str})
                    messages.append(ToolMessage(content=result_str, tool_call_id=tool_call_id))

            async for chunk in raw_llm.astream(messages):
                token = chunk.content
                if isinstance(token, str):
                    filtered_token = text_filter.process(token)
                    if filtered_token:
                        yield format_ndjson({"token": filtered_token})

            final_token = text_filter.flush()
            if final_token:
                yield format_ndjson({"token": final_token})
        except Exception as e:
            import traceback

            traceback.print_exc()
            yield format_ndjson({"error": str(e)})

    return StreamingResponse(stream_admin_chat(), media_type="application/x-ndjson")
