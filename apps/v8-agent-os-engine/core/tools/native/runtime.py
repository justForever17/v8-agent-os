from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Annotated, Any, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from core.delegation_broker import normalize_task_brief, normalize_task_briefs
from core.runtime_episodes import (
    build_runtime_episode,
    emit_runtime_episode_event,
    enqueue_runtime_episode,
    normalize_capability_kind,
    upsert_runtime_episode,
)
from core.runtime_tool_access import (
    RUNTIME_BROKER_TOOL_NAME,
    grant_runtime_tool_groups,
    normalize_runtime_access,
    revoke_runtime_tool_groups,
    runtime_access_from_route_context,
    runtime_tool_groups_catalog,
)
from core.spec_service import spec_service
from erc.runtime_context import get_runtime_context


def _runtime_broker_payload(
    *,
    mode: str,
    ok: bool,
    summary: str,
    grants: list[dict[str, Any]] | None = None,
    groups: list[dict[str, Any]] | None = None,
    rejected: list[str] | None = None,
    error: str | None = None,
    detail_level: str = "summary",
    changed: list[dict[str, Any]] | None = None,
    episode: dict[str, Any] | None = None,
    next_action: str | None = None,
) -> str:
    normalized_detail = str(detail_level or "summary").strip().lower()
    group_items = list(groups or [])
    if normalized_detail not in {"catalog", "detail", "full"}:
        original_group_count = len(group_items)
        group_items = [
            {
                "group": str(item.get("group") or ""),
                "kind": str(item.get("runtimeKind") or ""),
                "label": str(item.get("label") or item.get("group") or ""),
            }
            for item in group_items
            if isinstance(item, dict)
        ][:6]
    else:
        original_group_count = len(group_items)
    payload = {
        "mode": mode,
        "ok": ok,
        "summary": summary,
        "activeGrants": [str((item or {}).get("group") or item) for item in list(grants or [])],
        "availableGroups": group_items,
        "rejected": list(rejected or []),
        "detailMode": normalized_detail if normalized_detail in {"catalog", "detail", "full"} else "summary",
        "detailTool": "runtime_broker(mode='list', detail_level='catalog') for compact catalog; detail_level='full' for diagnostics",
    }
    if changed is not None:
        payload["changed"] = list(changed or [])
    if episode:
        episode_id = str(episode.get("episodeId") or episode.get("needId") or "")
        episode_kind = str(episode.get("kind") or "")
        episode_state = str(episode.get("state") or "")
        payload["episode"] = {
            "episodeId": episode_id,
            "kind": episode_kind,
            "state": episode_state,
            "reason": str(episode.get("reason") or ""),
            "continuationTarget": str(episode.get("continuationTarget") or ""),
        }
        payload["queuedEpisodeId"] = episode_id
        payload["episodeKind"] = episode_kind
        payload["state"] = episode_state
        payload["nextAction"] = "wait_episode"
    if next_action:
        payload["recommendedNextAction"] = next_action
    if normalized_detail not in {"catalog", "detail", "full"} and groups:
        omitted_tools = sum(len(list(item.get("toolNames") or [])) for item in list(groups or []) if isinstance(item, dict))
        payload["omitted"] = {
            "toolNames": omitted_tools,
            "availableGroups": max(0, original_group_count - len(group_items)),
            "reason": "default list is a compact route menu; capability_registry already describes runtime details",
        }
    if error:
        payload["error"] = error
    if normalized_detail in {"catalog", "detail", "full"}:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


_RUNTIME_ROUTE_DEFAULT_GROUPS: dict[str, list[str]] = {
    "engineering": ["delegation.recursive"],
    "research": ["research.core"],
    "creative_media": ["creative_media.core"],
    "computer_use": ["computer_use.control"],
    "rpa": ["rpa.run"],
    "delegation": ["delegation.recursive"],
    "memory": ["memory.read"],
}


def _normalize_capability_kind(value: Any) -> str:
    return normalize_capability_kind(value)


def _capability_route_groups(
    *,
    need: dict[str, Any],
    runtime_kind: Optional[str],
    tool_group: Optional[str],
    tool_groups: Optional[list[str]],
) -> list[str]:
    kind = _normalize_capability_kind(need.get("kind") or runtime_kind)
    requested: list[str] = []
    requested.extend(list(need.get("requiredRuntimeAccess") or []))
    requested.extend(list(tool_groups or []))
    if tool_group:
        requested.append(tool_group)
    requested.extend(_RUNTIME_ROUTE_DEFAULT_GROUPS.get(kind, []))
    return normalize_runtime_access(requested, runtime_kind=runtime_kind or kind)


def _planner_task_briefs_from_state(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    state = dict(state or {})
    planner_plan = state.get("planner_plan")
    briefs: list[Any] = []
    if isinstance(planner_plan, dict):
        for key in ("workerBriefs", "worker_briefs", "taskBriefs", "task_briefs", "tasks"):
            value = planner_plan.get(key)
            if isinstance(value, list) and value:
                briefs = value
                break
    if not briefs:
        route_context = dict(state.get("current_route_context") or {})
        for episode in list(route_context.get("capabilityEpisodes") or []):
            if not isinstance(episode, dict):
                continue
            inputs = episode.get("inputs")
            if not isinstance(inputs, dict):
                continue
            for key in ("workerBriefs", "worker_briefs", "taskBriefs", "task_briefs", "tasks"):
                value = inputs.get(key)
                if isinstance(value, list) and value:
                    briefs = value
                    break
            if briefs:
                break
    return normalize_task_briefs(briefs)


def _minimal_route_task_from_need(need: dict[str, Any], kind: str) -> dict[str, Any]:
    inputs = dict(need.get("inputs") or {}) if isinstance(need.get("inputs"), dict) else {}
    blocked_tool = str(need.get("tool") or inputs.get("blockedTool") or "").strip()
    args = dict(inputs.get("blockedToolArgs") or {}) if isinstance(inputs.get("blockedToolArgs"), dict) else {}
    command = str(args.get("command") or args.get("_raw") or "").strip()
    target_path = str(args.get("path") or args.get("filePath") or args.get("file_path") or "").strip()
    reason = str(need.get("reason") or inputs.get("brief") or inputs.get("query") or "").strip()
    goal = (
        command
        or target_path
        or reason
        or (f"Handle blocked Supervisor tool {blocked_tool} through {kind} runtime." if blocked_tool else f"Run {kind} runtime episode.")
    )
    brief = {
        "taskBriefId": f"route-{kind}-minimal",
        "title": goal[:96],
        "goal": goal,
        "brief": goal,
        "familyHint": "engineering" if kind == "engineering" else ("research" if kind == "research" else "generalist"),
        "executionLaneHint": "auto",
        "requiredCapabilities": ["workspace_mutation", "verification"] if kind == "engineering" else [],
        "acceptanceContract": "Return a compact handoff with outcome, evidence, and next steps.",
    }
    workspace = str(inputs.get("workspacePath") or inputs.get("workspace_path") or "").strip()
    if workspace:
        brief["workspacePath"] = workspace
        brief["writeSet"] = [target_path or workspace]
    if blocked_tool:
        brief["context"] = {"blockedTool": blocked_tool, **({"workspacePath": workspace} if workspace else {})}
    return brief


def _safe_compact_text(value: Any, *, limit: int = 6000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _spec_id_from_route_need(need: dict[str, Any], inputs: dict[str, Any], state: dict[str, Any] | None) -> str:
    for source in (
        need,
        inputs,
        dict((state or {}).get("current_route_context") or {}),
        dict(state or {}),
    ):
        for key in ("specId", "spec_id", "currentSpecId", "activeSpecId"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _workspace_path_from_route(inputs: dict[str, Any], state: dict[str, Any] | None) -> str:
    runtime_context = get_runtime_context()
    for source in (
        inputs,
        dict((state or {}).get("current_route_context") or {}),
        dict(state or {}),
        runtime_context,
    ):
        workspace = str(source.get("workspacePath") or source.get("workspace_path") or "").strip()
        if workspace:
            return workspace
    return ""


def _spec_runtime_execution_allowed(spec_brief: dict[str, Any]) -> bool:
    pipeline = dict(spec_brief.get("pipelineControl") or {})
    approved = {str(item).strip().lower() for item in list(spec_brief.get("approvedStages") or [])}
    return bool(pipeline.get("runtimeExecutionAllowed")) or {"requirements", "design", "tasks"}.issubset(approved)


def _split_spec_refs(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    refs = re.findall(r"\b(?:REQ|BFIX|DES|TASK|TSK|T|AC)-\d{2,}\b", text, flags=re.IGNORECASE)
    if refs:
        normalized: list[str] = []
        for ref in refs:
            item = ref.upper()
            match = re.match(r"^(?:TASK|TSK|T)-(\d+)$", item)
            if match:
                item = f"TASK-{int(match.group(1)):03d}"
            normalized.append(item)
        return list(dict.fromkeys(normalized))
    return []


def _extract_task_field(excerpt: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        match = re.search(
            rf"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*[:：](?:\*\*)?\s*(.+?)\s*$",
            excerpt,
        )
        if match:
            return match.group(1).strip()
    for label in labels:
        match = re.search(
            rf"(?im)^\s*\|\s*(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*\|\s*(.+?)\s*\|\s*$",
            excerpt,
        )
        if match:
            value = re.sub(r"`([^`]+)`", r"\1", match.group(1)).strip()
            return value.strip()
    return ""


def _extract_task_block(excerpt: str, labels: tuple[str, ...], *, limit: int = 1200) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?ims)^\s*\*\*(?:{label_pattern})\*\*\s*[:：]?\s*\n(?P<body>.*?)(?=\n\s*\*\*[^*\n]+?\*\*\s*[:：]|\n---|\n###\s+|\Z)",
        excerpt,
    )
    if not match:
        return ""
    return _safe_compact_text(match.group("body"), limit=limit)


def _task_id_aliases(task_id: str) -> list[str]:
    normalized = str(task_id or "").strip().upper()
    aliases = [normalized] if normalized else []
    match = re.match(r"^TASK-(\d+)$", normalized)
    if match:
        number = int(match.group(1))
        aliases.extend([f"TSK-{number:03d}", f"T-{number:03d}", f"T-{number:02d}"])
    return list(dict.fromkeys([item for item in aliases if item]))


def _canonical_task_id(raw: Any, *, index: int = 0) -> str:
    text = str(raw or "").strip().upper()
    match = re.match(r"^(?:TASK|TSK|T)-(\d+)$", text)
    if match:
        return f"TASK-{int(match.group(1)):03d}"
    return f"TASK-{index + 1:03d}"


def _extract_task_ids_from_markdown(markdown: str) -> list[str]:
    text = str(markdown or "")
    seen: set[str] = set()
    ids: list[str] = []

    def add_from(pattern: str) -> None:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
            raw = match.group(1) if match.lastindex else match.group(0)
            task_id = _canonical_task_id(raw, index=len(ids))
            if task_id not in seen:
                seen.add(task_id)
                ids.append(task_id)

    add_from(r"^\s*#{2,6}\s+((?:TASK|TSK|T)-\d{2,})\b")
    add_from(r"^\s*[-*]\s*(?:\[[ xX]\]\s*)?((?:TASK|TSK|T)-\d{2,})\b")
    if ids:
        return ids
    pattern = re.compile(r"\b(?:TASK|TSK|T)-\d{2,}\b", flags=re.IGNORECASE)
    for match in pattern.finditer(text):
        task_id = _canonical_task_id(match.group(0), index=len(ids))
        if task_id not in seen:
            seen.add(task_id)
            ids.append(task_id)
    return ids


def _find_task_line_match(text: str, refs: list[str]) -> re.Match[str] | None:
    if not refs:
        return None
    ref_pattern = "|".join(re.escape(ref) for ref in refs)
    preferred_patterns = (
        rf"(?im)^\s*#{{2,6}}\s+(?:{ref_pattern})\b.*$",
        rf"(?im)^\s*(?:[-*]\s*(?:\[[ xX]\]\s*)?)(?:{ref_pattern})\b.*$",
    )
    for pattern in preferred_patterns:
        match = re.search(pattern, text)
        if match:
            return match
    for pattern in (
        rf"(?im)^\s*(?!\|.*~)(?:{ref_pattern})\b.*$",
        rf"(?im)^.*\b(?:{ref_pattern})\b.*$",
    ):
        match = re.search(pattern, text)
        if match:
            return match
    return None


def _task_sections_from_markdown(markdown: str, task_ids: list[str]) -> list[dict[str, Any]]:
    text = str(markdown or "")
    sections: list[dict[str, Any]] = []
    normalized_task_ids = [_canonical_task_id(task_id, index=index) for index, task_id in enumerate(task_ids)]
    heading_task_ids = _extract_task_ids_from_markdown(text)
    if heading_task_ids:
        normalized_task_ids = heading_task_ids
    if not normalized_task_ids:
        normalized_task_ids = _extract_task_ids_from_markdown(text)
    for index, task_id in enumerate(normalized_task_ids):
        normalized_id = _canonical_task_id(task_id, index=index)
        refs = _task_id_aliases(normalized_id)
        match = _find_task_line_match(text, refs)
        if not match:
            sections.append(
                {
                    "taskId": normalized_id,
                    "title": f"Execute approved {normalized_id}",
                    "excerpt": normalized_id,
                }
            )
            continue
        start = text.rfind("\n", 0, match.start()) + 1
        next_task = re.search(r"(?im)^\s*(?:#{2,6}\s*)?(?:[-*]\s*(?:\[[ xX]\]\s*)?)?(?:TASK|TSK|T)-\d{2,}\b", text[match.end() :])
        next_heading = re.search(r"(?m)^##+\s+", text[match.end() :])
        candidates = [len(text)]
        if next_task:
            candidates.append(match.end() + next_task.start())
        if next_heading:
            candidates.append(match.end() + next_heading.start())
        end = min(candidates)
        excerpt = text[start:end].strip()
        first_line = excerpt.splitlines()[0] if excerpt else normalized_id
        title = re.sub(r"(?i)^.*\b(?:TASK|TSK|T)-\d{2,}\b\s*[:：.\-、]?\s*", "", first_line).strip()
        title = re.sub(r"^\[[ xX]\]\s*", "", title).strip("-:： ")
        output_labels = ("expectedOutput", "expected output", "output", "输出", "产物", "预期输出", "预期输出路径")
        output_file = _extract_task_block(
            excerpt,
            ("输出文件", "预期输出路径", "预期输出", "输出路径"),
            limit=900,
        )
        if not output_file:
            output_file = _extract_task_field(
            excerpt,
                output_labels,
            )
        if not output_file:
            output_match = re.search(
                r"(?ims)\*\*(?:输出文件|预期输出路径|预期输出|输出路径)\*\*\s*[:：]?\s*\n(?P<body>.*?)(?:\n---|\n###\s+|\Z)",
                excerpt,
            )
            if output_match:
                output_file = _safe_compact_text(output_match.group("body"), limit=900)
        acceptance = _extract_task_block(excerpt, ("acceptance", "验收", "验收标准"), limit=1200)
        if not acceptance:
            acceptance = _extract_task_field(excerpt, ("acceptance", "验收"))
        if not acceptance:
            acceptance_match = re.search(r"(?ims)\*\*验收标准\*\*\s*[:：]?\s*\n(?P<body>.*?)(?:\n---|\n###\s+|\Z)", excerpt)
            if acceptance_match:
                acceptance = _safe_compact_text(acceptance_match.group("body"), limit=1200)
        sections.append(
            {
                "taskId": normalized_id,
                "title": title or f"Execute approved {normalized_id}",
                "excerpt": _safe_compact_text(excerpt, limit=5000),
                "runtimeLane": _extract_task_field(
                    excerpt,
                    (
                        "runtimeLane",
                        "runtime lane",
                        "Runtime",
                        "Lane",
                        "执行泳道",
                        "执行通道",
                        "执行频道",
                        "执行方",
                        "执行角色",
                        "执行者",
                    ),
                ),
                "dependsOn": _split_spec_refs(_extract_task_field(excerpt, ("dependsOn", "depends on", "Depends", "依赖"))),
                "specRefs": _split_spec_refs(
                    " ".join(
                        [
                            _extract_task_field(excerpt, ("specRefs", "spec refs", "Refs", "引用")),
                            _extract_task_field(excerpt, ("需求引用", "requirement refs", "requirements")),
                            _extract_task_field(excerpt, ("设计引用", "design refs", "design")),
                        ]
                    )
                ),
                "inputRefs": _extract_task_field(excerpt, ("inputRefs", "input refs", "输入")),
                "expectedOutput": output_file,
                "acceptance": acceptance,
                "proofRequired": _extract_task_field(excerpt, ("proofRequired", "proof required", "proof", "证明")),
            }
        )
    return sections


def _approved_spec_execution_bundle(
    need: dict[str, Any],
    inputs: dict[str, Any],
    *,
    state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    spec_id = _spec_id_from_route_need(need, inputs, state)
    if not spec_id:
        return None
    workspace_path = _workspace_path_from_route(inputs, state)
    if not workspace_path:
        return None
    try:
        detail = spec_service.read_spec(workspace_path=workspace_path, spec_id=spec_id, max_chars=80000)
    except Exception as exc:  # noqa: BLE001 - keep runtime route recoverable.
        return {
            "kind": "SpecExecutionBundle",
            "specId": spec_id,
            "workspacePath": workspace_path,
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }
    spec_brief = dict(detail.get("specBrief") or {})
    if not _spec_runtime_execution_allowed(spec_brief):
        return None
    stages = dict(detail.get("stages") or {})
    docs: dict[str, Any] = {}
    for stage in ("requirements", "bugfix", "design", "tasks"):
        payload = stages.get(stage)
        if not isinstance(payload, dict):
            continue
        docs[stage] = {
            "stage": stage,
            "detailRef": payload.get("documentRef") or f"spec://{spec_id}/{stage}",
            "relativePath": payload.get("relativePath"),
            "ids": list(payload.get("ids") or []),
            "content": _safe_compact_text(payload.get("content"), limit=12000 if stage == "tasks" else 9000),
            "truncated": bool(payload.get("truncated")),
        }
    task_doc = dict(docs.get("tasks") or {})
    traceability = dict(spec_brief.get("traceability") or {}) if isinstance(spec_brief.get("traceability"), dict) else {}
    task_sections = [
        dict(item)
        for item in list(traceability.get("tasks") or [])
        if isinstance(item, dict)
    ]
    parsed_task_sections = _task_sections_from_markdown(
        str(task_doc.get("content") or ""),
        list(task_doc.get("ids") or []),
    )
    if task_sections:
        parsed_by_id = {
            str(item.get("taskId") or "").strip(): item
            for item in parsed_task_sections
            if str(item.get("taskId") or "").strip()
        }
        task_sections = [
            {
                **dict(parsed_by_id.get(str(item.get("taskId") or "").strip()) or {}),
                **item,
            }
            for item in task_sections
        ]
    else:
        task_sections = parsed_task_sections
    return {
        "kind": "SpecExecutionBundle",
        "status": "ready",
        "specId": spec_id,
        "featureName": spec_brief.get("featureName"),
        "workspacePath": workspace_path,
        "specDir": spec_brief.get("specDir"),
        "approvedStages": list(spec_brief.get("approvedStages") or []),
        "pipelineControl": dict(spec_brief.get("pipelineControl") or {}),
        "documents": docs,
        "tasks": task_sections,
        "traceability": {
            "frameworkDigest": traceability.get("frameworkDigest"),
            "missingRefs": list(traceability.get("missingRefs") or [])[:20],
            "distributionChecks": traceability.get("distributionChecks") if isinstance(traceability.get("distributionChecks"), dict) else {},
        },
        "distribution": {
            "strategy": "task_sliced_with_stage_context",
            "mainRuntimeReceives": ["SpecExecutionBundle", "all approved stage summaries/content", "task briefs"],
            "subagentReceives": [
                "assigned task excerpt",
                "linked requirement/design refs",
                "detailRefs",
                "engineeringExecutionContract",
                "handoffContract",
            ],
            "grandchildReceives": [
                "parent task slice",
                "required refs only",
                "allowedWorkset/forbiddenScopes",
                "handoffRequired",
            ],
        },
    }


def _required_runtime_access_from_spec_bundle(bundle: dict[str, Any], kind: str) -> list[str]:
    lanes = " ".join(
        str(task.get("runtimeLane") or task.get("excerpt") or "")
        for task in list(bundle.get("tasks") or [])
        if isinstance(task, dict)
    ).lower()
    groups: list[str] = []
    if kind == "engineering":
        groups.append("delegation.recursive")
    if any(token in lanes for token in ("research", "调研", "evidence", "source")):
        groups.append("research.core")
    if any(token in lanes for token in ("delegation", "subagent", "agent", "子agent", "孙agent", "并行")):
        groups.append("delegation.recursive")
    if any(token in lanes for token in ("creative", "media", "image", "video", "audio", "素材", "视频", "图片")):
        groups.append("creative_media.core")
    return list(dict.fromkeys(groups))


def _spec_task_runtime_family(task: dict[str, Any], route_kind: str) -> str:
    lane = str(task.get("runtimeLane") or "").strip().lower()
    probe = " ".join(
        [
            str(task.get("title") or ""),
            str(task.get("excerpt") or ""),
            str(task.get("expectedOutput") or ""),
        ]
    ).lower()
    if "research" in lane or "调研" in lane:
        return "research"
    if "engineering" in lane or "工程" in lane:
        return "engineering"
    if "qa" in lane or "test" in lane or "验证" in lane:
        return "engineering"
    if "creative" in lane or "media" in lane or "创意" in lane:
        return "creative_media"
    if "delegation" in lane or "subagent" in lane or "子agent" in lane or "孙agent" in lane:
        return "delegation"
    strong_engineering_markers = (
        "skill.md",
        "verification-report",
        "delivery-summary",
        "quality_check.py",
        "merge_research.py",
        "目录初始化",
        "脚本复制",
        "创建目录",
        "创建完整",
        "skill构建",
        "skill 构建",
        "质量验证",
        "最终交付",
        "交付文档",
        "构建与质量自检",
    )
    if any(token in probe for token in strong_engineering_markers):
        return "engineering"
    if "supervisor" in lane or "governance" in lane or "主管" in lane:
        return "governance"
    if any(token in probe for token in ("research", "调研", "source", "evidence", "citation", "来源")):
        return "research"
    if any(token in probe for token in ("delegation", "subagent", "agent swarm", "子agent", "孙agent", "并行子")):
        return "research" if "research" in probe or "调研" in probe else "delegation"
    if any(token in probe for token in ("creative", "media", "image", "video", "audio", "素材", "视频", "图片")):
        return "creative_media"
    if any(token in probe for token in ("governance", "supervisor", "验收", "确认", "检查点")):
        return "governance"
    if any(token in probe for token in ("engineering", "工程", "write", "file", "artifact", "skill.md", "目录", "构建", "验证")):
        return "engineering"
    return _normalize_capability_kind(route_kind) or "engineering"


def _spec_task_required_capabilities(family: str, *, writes_artifact: bool) -> list[str]:
    if family == "research":
        capabilities = ["source_backed_research", "evidence_pack", "research_handoff"]
        if writes_artifact:
            capabilities.append("workspace_mutation")
        return capabilities
    if family == "creative_media":
        return ["creative_asset_request", "artifact_handoff"]
    if family == "delegation":
        return ["delegation", "handoff"]
    if family == "governance":
        return ["spec_section_read", "verification", "handoff_reconciliation"]
    capabilities = ["spec_section_read", "verification", "proof_handoff"]
    if writes_artifact:
        capabilities.append("workspace_mutation")
    return capabilities


def _spec_task_execution_lane(family: str) -> str:
    if family in {"research", "delegation", "governance"}:
        return "subagent"
    if family == "creative_media":
        return "auto"
    return "engineering"


def _spec_task_deliverable_kind(family: str) -> str:
    if family == "research":
        return "evidence"
    if family == "creative_media":
        return "artifact"
    if family == "delegation":
        return "handoff"
    if family == "governance":
        return "verification"
    return "artifact"


def _spec_task_writes_artifact(task: dict[str, Any], family: str) -> bool:
    if family == "creative_media":
        return True
    if family == "research":
        output = str(task.get("expectedOutput") or task.get("expectedOutputs") or "")
        text = " ".join(
            str(task.get(key) or "")
            for key in ("taskId", "title", "excerpt", "taskExcerpt", "expectedOutput", "expectedOutputs", "acceptance", "proofRequired")
        )
        return bool(_spec_task_expected_paths(output, text))
    if family != "engineering":
        return False
    lane = str(task.get("runtimeLane") or "").strip().lower()
    expected_output = str(task.get("expectedOutput") or "")
    text = " ".join(
        str(task.get(key) or "")
        for key in ("taskId", "title", "excerpt", "expectedOutput", "acceptance", "proofRequired")
    ).lower()
    verification_markers = ("验收", "验证", "测试", "检查", "verify", "verification", "test", "validate")
    if (
        any(marker in text for marker in verification_markers)
        and not _spec_task_expected_paths(expected_output)
    ):
        return False
    # Verification/checkpoint/final-summary tasks may inspect artifacts or produce
    # user-visible summaries, but they should not be treated as content-writing
    # workers unless a concrete artifact path is present.
    if any(marker in lane for marker in ("verification", "governance", "supervisor")):
        return bool(re.search(r"(?i)(?:skill\.md|[\\/\w.-]+\.(?:md|txt|json|py|ts|tsx|js|jsx|html|css|yml|yaml))", text))
    if re.search(r"(?i)(?:skill\.md|[\\/\w.-]+\.(?:md|txt|json|py|ts|tsx|js|jsx|html|css|yml|yaml))", text):
        return True
    if any(marker in text for marker in ("目录初始化", "创建目录", "空目录", "最终交付摘要", "交付整理")):
        return False
    return any(
        marker in text
        for marker in (
            "写入",
            "创建完整",
            "组装构建",
            "skill构建",
            "skill 构建",
            "build skill",
            "assemble skill",
            "write artifact",
        )
    )


def _spec_task_validates_skill_artifact(task: dict[str, Any], family: str, *, writes_artifact: bool) -> bool:
    if family != "engineering":
        return False
    text = " ".join(
        str(task.get(key) or "")
        for key in ("taskId", "title", "excerpt", "expectedOutput", "acceptance", "proofRequired")
    ).lower()
    validation_markers = ("质量验证", "交付前质量验证", "validate", "validation")
    skill_build_markers = (
        "skill.md",
        "skill 构建",
        "skill构建",
        "生成 skill",
        "生成skill",
        "组装 skill",
        "组装skill",
        "build skill",
        "assemble skill",
        "write skill",
        "skill artifact",
    )
    if "skill.md" in text:
        return writes_artifact or any(marker in text for marker in validation_markers)
    if any(marker in text for marker in skill_build_markers):
        return True
    return "skill" in text and any(marker in text for marker in validation_markers)


_SPEC_OUTPUT_PATH_PATTERN = re.compile(
    r"(?i)(?:`([^`]+)`|(?<![\w.-])([\w@.$~][\w@.$~\-/\\]*(?:\.(?:md|txt|json|py|ts|tsx|js|jsx|html|css|yml|yaml|png|jpg|jpeg|webp|svg|mp3|wav|mp4|mov)|[\\/])))(?![\w.-])"
)


def _spec_task_expected_paths(*values: Any) -> list[str]:
    """Extract likely artifact paths from a compact task section.

    The result is intentionally conservative: it is a workset hint for
    delegated agents, not a filesystem permission grant by itself.
    """

    paths: list[str] = []
    for value in values:
        text = str(value or "")
        if not text:
            continue
        for match in _SPEC_OUTPUT_PATH_PATTERN.finditer(text):
            candidate = str(match.group(1) or match.group(2) or "").strip()
            if not candidate:
                continue
            candidate = candidate.strip("`'\"，,。;；:：")
            lowered = candidate.lower()
            if (
                lowered in {"http://", "https://"}
                or lowered.startswith(("http://", "https://", "spec://"))
                or any(marker in candidate for marker in ("<", ">", "\r", "\n"))
            ):
                continue
            if not re.search(r"(?i)([\\/]|(?:^|[\\/])?[\w@.$~-]+\.(?:md|txt|json|py|ts|tsx|js|jsx|html|css|yml|yaml|png|jpg|jpeg|webp|svg|mp3|wav|mp4|mov)$)", candidate):
                continue
            if candidate not in paths:
                paths.append(candidate)
    return paths[:16]


def _spec_stage_slice(markdown: Any, refs: list[str], *, stage: str, limit: int = 5200) -> str:
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized_refs = [str(ref or "").strip().upper() for ref in refs if str(ref or "").strip()]
    if not text or not normalized_refs:
        return ""
    headings = list(re.finditer(r"(?m)^#{2,6}\s+(.+?)\s*$", text))
    selected: list[str] = []
    for index, match in enumerate(headings):
        start = match.start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[start:end].strip()
        upper_block = block.upper()
        title = str(match.group(1) or "").strip()
        include = any(ref in upper_block for ref in normalized_refs)
        if not include and stage == "design":
            heading_number = re.match(r"^\s*(\d+)(?:\.\d+)*[.、:\s]", title)
            if heading_number:
                section_number = int(heading_number.group(1))
                include = any(
                    (ref_number := re.search(r"(\d+)$", ref)) is not None
                    and int(ref_number.group(1)) == section_number
                    for ref in normalized_refs
                )
        if include and block not in selected:
            selected.append(block)
    if not selected:
        return ""
    return _safe_compact_text("\n\n".join(selected), limit=limit)


def _preferred_agent_for_spec_task(
    task: dict[str, Any],
    *,
    family: str,
    writes_artifact: bool,
    validates_skill_artifact: bool,
    expected_paths: list[str],
) -> str:
    if validates_skill_artifact:
        return "skill-workflow-curator"
    if family == "research":
        text = " ".join(
            str(task.get(key) or "")
            for key in ("taskId", "title", "excerpt", "expectedOutput", "acceptance", "proofRequired")
        ).lower()
        verification_markers = ("验收", "验证", "测试", "检查", "verify", "verification", "test", "validate")
        if not writes_artifact and any(marker in text for marker in verification_markers):
            return "verification-engineer"
        return "web-research-architect"
    if family != "engineering":
        return ""
    text = " ".join(
        str(task.get(key) or "")
        for key in ("taskId", "title", "excerpt", "expectedOutput", "acceptance", "proofRequired")
    ).lower()
    suffixes = {Path(path.rstrip("/\\")).suffix.lower() for path in expected_paths if path.rstrip("/\\")}
    verification_markers = ("验收", "验证", "测试", "检查", "verify", "verification", "test", "validate")
    if not writes_artifact and any(marker in text for marker in verification_markers):
        return "verification-engineer"
    if suffixes.intersection({".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}):
        return "frontend-product-engineer"
    if suffixes and suffixes.issubset({".md", ".mdx", ".txt", ".rst"}):
        return "docs-delivery-writer"
    if writes_artifact or expected_paths or any(
        marker in text
        for marker in ("创建目录", "目录初始化", "mkdir", "implement", "write", "create", "实现", "写入", "编写")
    ):
        return "implementation-engineer"
    if any(marker in text for marker in ("规划", "拆解", "方案", "plan", "decompose")):
        return "project-planner"
    return "implementation-engineer"


def _spec_task_engineering_execution_contract(
    *,
    spec_id: str,
    workspace_path: str,
    task_id: str,
    task: dict[str, Any],
    family: str,
    writes_artifact: bool,
    requirement_doc: dict[str, Any],
    design_doc: dict[str, Any],
    task_doc: dict[str, Any],
    spec_refs: list[str],
    expected_paths: list[str],
    acceptance: str,
    proof: str,
) -> dict[str, Any]:
    detail_refs = [
        ref
        for ref in [
            requirement_doc.get("detailRef"),
            design_doc.get("detailRef"),
            f"spec://{spec_id}/tasks#{task_id}",
        ]
        if ref
    ]
    allowed_workset = list(expected_paths or ([workspace_path] if workspace_path else []))
    source_refs = {
        "specId": spec_id,
        "taskId": task_id,
        "requirementIds": [
            ref
            for ref in spec_refs
            if str(ref).upper().startswith(("REQ-", "BFIX-"))
            or re.match(r"^\d{1,2}\.\d{1,2}$", str(ref))
        ],
        "designIds": [ref for ref in spec_refs if str(ref).upper().startswith("DES-")],
        "detailRefs": detail_refs,
    }
    return {
        "workspacePath": workspace_path,
        "taskId": task_id,
        "runtimeFamily": family,
        "writeRequired": bool(writes_artifact),
        "allowedWorkset": allowed_workset,
        "expectedArtifacts": list(expected_paths or []),
        "sourceRefs": source_refs,
        "mustRead": [
            "Read the assigned task excerpt first.",
            "Use detailRefs to read approved requirements/design/task sections when the compact brief is insufficient.",
        ],
        "acceptance": [item for item in [acceptance, proof] if item],
        "forbiddenScopes": [
            "Do not read/write outside the Active Workspace Root unless another root is explicitly granted.",
            "Do not edit files outside allowedWorkset when concrete expected artifacts are listed.",
            "Do not use older specs, memory, or chat history to override the approved current Spec.",
            "Do not perform destructive commands or cross-project changes without approval.",
        ],
    }


def _spec_task_handoff_contract(*, spec_id: str, task_id: str, writes_artifact: bool) -> dict[str, Any]:
    required_fields = [
        "status",
        "specId",
        "taskId",
        "summary",
        "changedFiles",
        "commandsRun",
        "testResults",
        "artifacts",
        "proofRefs",
        "blockers",
        "residualRisks",
    ]
    return {
        "type": "engineering_typed_handoff",
        "requiredFields": required_fields,
        "completionRule": (
            "Return a typed handoff with verifiable proof/artifact/test result. "
            "A plain 'done' message is not enough."
        ),
        "mustInclude": [
            f"specId={spec_id}",
            f"taskId={task_id}",
            "what changed and why",
            "verification commands/results, including skipped or failed checks",
            "artifact/proof/detail refs when available",
        ],
        "writeRequired": bool(writes_artifact),
    }


def _brief_family_hint(brief: dict[str, Any]) -> str:
    family = str(brief.get("familyHint") or "").strip().lower()
    if family:
        return family
    capsule = brief.get("engineeringTaskCapsule") if isinstance(brief.get("engineeringTaskCapsule"), dict) else {}
    lane = str(capsule.get("runtimeLane") or "").strip().lower()
    if "research" in lane or "调研" in lane:
        return "research"
    if "delegation" in lane or "subagent" in lane or "子agent" in lane or "孙agent" in lane:
        return "delegation"
    if "creative" in lane or "media" in lane or "创意" in lane:
        return "creative_media"
    return "engineering"


def _canonical_spec_detail_ref(spec_id: str, detail_ref: Any, *, fallback_stage: str = "", fallback_id: str = "") -> str:
    ref = str(detail_ref or "").strip()
    spec = str(spec_id or "").strip()
    if not ref and fallback_stage and fallback_id:
        ref = f"spec://{spec}/{fallback_stage}#{fallback_id}" if spec else f"spec://{fallback_stage}#{fallback_id}"
    if spec and ref.startswith("spec://") and not ref.startswith(f"spec://{spec}/"):
        suffix = ref[len("spec://") :]
        if suffix.startswith("/"):
            suffix = suffix[1:]
        if suffix and "/" not in suffix.split("#", 1)[0]:
            return f"spec://{spec}/{suffix}"
    return ref


def _shape_spec_task_briefs_for_route(briefs: list[dict[str, Any]], *, kind: str, spec_id: str, workspace_path: str) -> list[dict[str, Any]]:
    if not briefs:
        return briefs
    normalized_kind = _normalize_capability_kind(kind)
    if normalized_kind in {"engineering", "delegation"}:
        # Spec tasks are the approved delivery contract. The route layer must
        # not merge, drop, or rename them; dependency-aware execution and UI
        # compaction belong to lower layers.
        return briefs
    return briefs


def _task_briefs_from_spec_bundle(bundle: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    spec_id = str(bundle.get("specId") or "").strip()
    workspace_path = str(bundle.get("workspacePath") or "").strip()
    docs = dict(bundle.get("documents") or {})
    traceability = dict(bundle.get("traceability") or {}) if isinstance(bundle.get("traceability"), dict) else {}
    framework_digest = str(traceability.get("frameworkDigest") or "").strip()
    requirement_doc = dict(docs.get("requirements") or docs.get("bugfix") or {})
    design_doc = dict(docs.get("design") or {})
    task_doc = dict(docs.get("tasks") or {})
    tasks = [item for item in list(bundle.get("tasks") or []) if isinstance(item, dict)]
    if not tasks:
        tasks = [
            {
                "taskId": "TASK-001",
                "title": f"Execute approved Spec {spec_id}",
                "excerpt": "Execute the approved Spec and return typed handoff/proof.",
                "runtimeLane": kind,
                "specRefs": list(requirement_doc.get("ids") or [])[:8] + list(design_doc.get("ids") or [])[:8],
            }
        ]
    briefs: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        task_id = str(task.get("taskId") or f"TASK-{index + 1:03d}").strip().upper().replace("TSK-", "TASK-")
        lane = str(task.get("runtimeLane") or kind or "engineering").strip()
        family = _spec_task_runtime_family(task, kind)
        if family == "governance" and kind != "governance":
            continue
        output = str(task.get("expectedOutput") or task.get("expectedOutputs") or "").strip()
        acceptance = str(task.get("acceptance") or task.get("acceptanceProof") or "").strip()
        proof = str(task.get("proofRequired") or task.get("proof") or "").strip()
        requirement_refs = list(task.get("requirementRefs") or [])
        design_refs = list(task.get("designRefs") or [])
        explicit_spec_refs = list(task.get("specRefs") or [])
        if explicit_spec_refs:
            spec_refs = explicit_spec_refs
        elif requirement_refs or design_refs:
            spec_refs = requirement_refs + design_refs
        else:
            spec_refs = list(requirement_doc.get("ids") or [])[:8] + list(design_doc.get("ids") or [])[:8]
        writes_artifact = _spec_task_writes_artifact(task, family)
        validates_skill_artifact = _spec_task_validates_skill_artifact(task, family, writes_artifact=writes_artifact)
        expected_paths = _spec_task_expected_paths(output)
        allowed_write_set = list(expected_paths or ([workspace_path] if workspace_path else []))
        execution_contract = _spec_task_engineering_execution_contract(
            spec_id=spec_id,
            workspace_path=workspace_path,
            task_id=task_id,
            task=task,
            family=family,
            writes_artifact=writes_artifact,
            requirement_doc=requirement_doc,
            design_doc=design_doc,
            task_doc=task_doc,
            spec_refs=spec_refs,
            expected_paths=expected_paths,
            acceptance=acceptance,
            proof=proof,
        )
        handoff_contract = _spec_task_handoff_contract(
            spec_id=spec_id,
            task_id=task_id,
            writes_artifact=writes_artifact,
        )
        child_signal = re.search(
            r"(?i)sub\s*agent|sub-agent|worker|parallel|fanout|子\s*agent|孙\s*agent|子agent|孙agent|并行",
            " ".join([lane, str(task.get("title") or ""), str(task.get("taskExcerpt") or task.get("excerpt") or "")]),
        )
        allow_child = bool(
            child_signal
            and family in {"delegation", "engineering", "research"}
            and not (family == "research" and re.search(r"(?i)调研 Agent|research agent", str(task.get("title") or "")))
        )
        requirement_snippet_text = " / ".join(
            f"{snippet.get('id')}: {snippet.get('summary')}"
            for snippet in list(task.get("requirementSnippets") or [])[:6]
            if isinstance(snippet, dict) and snippet.get("summary")
        )
        design_snippet_text = " / ".join(
            f"{snippet.get('title') or snippet.get('id')}: {snippet.get('summary')}"
            for snippet in list(task.get("designSnippets") or [])[:4]
            if isinstance(snippet, dict) and snippet.get("summary")
        )
        task_detail_ref = _canonical_spec_detail_ref(
            spec_id,
            task.get("detailRef"),
            fallback_stage="tasks",
            fallback_id=task_id,
        )
        requirement_ids = requirement_refs or [
            ref for ref in spec_refs if str(ref).upper().startswith(("REQ-", "BFIX-"))
        ]
        design_ids = design_refs or [ref for ref in spec_refs if str(ref).upper().startswith("DES-")]
        approved_requirement_slice = _spec_stage_slice(
            requirement_doc.get("content"),
            requirement_ids,
            stage="requirements",
        )
        approved_design_slice = _spec_stage_slice(
            design_doc.get("content"),
            design_ids,
            stage="design",
        )
        spec_document_paths = {
            key: value
            for key, value in {
                "requirements": requirement_doc.get("relativePath"),
                "design": design_doc.get("relativePath"),
                "tasks": task_doc.get("relativePath"),
            }.items()
            if value
        }
        preferred_agent_id = _preferred_agent_for_spec_task(
            task,
            family=family,
            writes_artifact=writes_artifact,
            validates_skill_artifact=validates_skill_artifact,
            expected_paths=expected_paths,
        )
        spec_execution_summary = _safe_compact_text(
            "\n".join(
                item
                for item in [
                    f"Framework / architecture everyone must follow: {framework_digest}" if framework_digest else "",
                    f"Task: {task.get('taskExcerpt') or task.get('excerpt') or task.get('title') or task_id}",
                    f"Requirements: {requirement_snippet_text}" if requirement_snippet_text else "",
                    f"Design: {design_snippet_text}" if design_snippet_text else "",
                ]
                if item
            ),
            limit=4200,
        )
        context = {
            "source": "approved_spec_execution_bundle",
            "specId": spec_id,
            "taskId": task_id,
            "taskDetailRef": task_detail_ref,
            "taskExcerpt": task.get("taskExcerpt") or task.get("excerpt") or task.get("title") or task_id,
            "specExecutionSummary": spec_execution_summary,
            "frameworkDigest": framework_digest,
            "approvedRequirementSlice": approved_requirement_slice,
            "approvedDesignSlice": approved_design_slice,
            "specDocumentPaths": spec_document_paths,
            "specRefUsage": (
                "spec:// refs are traceability identifiers, not URLs. Never pass them to curl, web tools, or shell commands. "
                "Use the approved slices attached here; if more context is needed, read the listed workspace-relative Spec document path."
            ),
            "runtimeLane": lane,
            "specRefs": spec_refs,
            "engineeringExecutionContract": execution_contract,
            "handoffContract": handoff_contract,
            "stageRefs": {
                "requirements": requirement_doc.get("detailRef"),
                "design": design_doc.get("detailRef"),
                "tasks": task_doc.get("detailRef"),
            },
            "stageContent": {
                "requirements": requirement_doc.get("content"),
                "design": design_doc.get("content"),
                "tasks": task_doc.get("content"),
            },
            "specExecutionBundle": {
                key: value
                for key, value in bundle.items()
                if key not in {"documents"}
            },
        }
        brief = normalize_task_brief(
            {
                "taskBriefId": task_id,
                "title": task.get("title") or task_id,
                "goal": f"{task_id}: {task.get('title') or 'Execute approved Spec task'}",
                "context": context,
                "writeSet": allowed_write_set,
                "behaviorScope": ["approved_spec_execution", "runtime_first", "verification"],
                "requiredCapabilities": _spec_task_required_capabilities(family, writes_artifact=writes_artifact),
                "acceptanceContract": {
                    "must": [
                        f"Execute approved {task_id} only within the approved Spec scope.",
                        "Use the linked requirements/design/task refs as the source of truth.",
                        "Return a typed handoff with specId, taskId, touched artifacts, and proof or degraded blocker.",
                        *([f"Expected output: {output}"] if output else []),
                        *([f"Acceptance: {acceptance}"] if acceptance else []),
                        *([f"Proof required: {proof}"] if proof else []),
                    ],
                    "should": [
                        "Read exact detailRef sections when the compact excerpt is insufficient.",
                        "Do not use older specs or memory to override the approved current Spec.",
                    ],
                    "nice": [],
                },
                "dependency": list(task.get("dependsOn") or []),
                "parallelGroup": lane or kind,
                "executionLaneHint": _spec_task_execution_lane(family),
                "familyHint": "" if family == "governance" else family,
                **({"preferredAgentId": preferred_agent_id} if preferred_agent_id else {}),
                "deliverableKind": "skill_artifact" if validates_skill_artifact else _spec_task_deliverable_kind(family),
                "writeRequired": writes_artifact,
                **({"validateSkillArtifact": True} if validates_skill_artifact else {}),
                "allowChildDelegation": allow_child,
                "childDelegationBudget": {"maxDepth": 1, "inherits": ["taskId", "specId", "specRefs", "detailRefs"]} if allow_child else {},
                "specRefs": {
                    "specId": spec_id,
                    "taskId": task_id,
                    "requirementIds": requirement_refs
                    or requirement_ids,
                    "designIds": design_refs or design_ids,
                    "detailRefs": [
                        ref
                        for ref in [
                            requirement_doc.get("detailRef"),
                            design_doc.get("detailRef"),
                            task_detail_ref,
                        ]
                        if ref
                    ],
                },
                "engineeringTaskCapsule": {
                    "deliverableKind": "skill_artifact" if validates_skill_artifact else _spec_task_deliverable_kind(family),
                    "writeRequired": writes_artifact,
                    **({"validateSkillArtifact": True} if validates_skill_artifact else {}),
                    "specId": spec_id,
                    "taskId": task_id,
                    "requirementIds": requirement_refs
                    or [ref for ref in spec_refs if str(ref).upper().startswith(("REQ-", "BFIX-"))],
                    "designIds": design_refs or [ref for ref in spec_refs if str(ref).upper().startswith("DES-")],
                    "frameworkDigest": framework_digest,
                    "runtimeLane": lane,
                    "workspacePath": workspace_path,
                    "writeSet": allowed_write_set,
                    "allowedWorkset": list(execution_contract.get("allowedWorkset") or []),
                    "expectedArtifacts": list(execution_contract.get("expectedArtifacts") or []),
                    "forbiddenScopes": list(execution_contract.get("forbiddenScopes") or []),
                    "proofExpectations": [
                        "Report exact touched files/artifacts.",
                        "Reference approved specId and taskId.",
                        "Attach verification result or recoverable blocker.",
                    ],
                    "handoffRequired": list(handoff_contract.get("requiredFields") or []),
                },
                "proofExpectations": [
                    "Typed runtime handoff",
                    "Touched files/artifacts",
                    "Verification proof or degraded blocker",
                ],
            }
        )
        briefs.append(brief)
    return _shape_spec_task_briefs_for_route(briefs, kind=kind, spec_id=spec_id, workspace_path=workspace_path)


_SPEC_TASK_REF_KEYS = (
    "taskRef",
    "taskRefs",
    "taskId",
    "taskIds",
    "specTaskRef",
    "specTaskRefs",
    "specTaskId",
    "specTaskIds",
)


def _normalize_spec_task_ref(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    match = re.search(r"\b(?:TASK|TSK|T)[\s_-]*(\d{1,6})\b", text)
    if not match:
        return ""
    number = int(match.group(1))
    width = max(3, len(match.group(1)))
    return f"TASK-{number:0{width}d}"


def _iter_spec_task_ref_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values: list[Any] = []
        for item in value:
            values.extend(_iter_spec_task_ref_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for key in _SPEC_TASK_REF_KEYS:
            if key in value:
                values.extend(_iter_spec_task_ref_values(value.get(key)))
        return values
    text = str(value or "").strip()
    if not text:
        return []
    matches = re.findall(r"\b(?:TASK|TSK|T)[\s_-]*\d{1,6}\b", text, flags=re.IGNORECASE)
    return matches or [text]


def _requested_spec_task_refs(need: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for source in (need, inputs):
        if not isinstance(source, dict):
            continue
        for key in _SPEC_TASK_REF_KEYS:
            for value in _iter_spec_task_ref_values(source.get(key)):
                ref = _normalize_spec_task_ref(value)
                if ref and ref not in refs:
                    refs.append(ref)
    return refs


def _spec_task_ref_from_brief(brief: dict[str, Any]) -> str:
    candidates: list[Any] = [
        brief.get("taskBriefId"),
        brief.get("taskId"),
        brief.get("id"),
    ]
    context = brief.get("context") if isinstance(brief.get("context"), dict) else {}
    candidates.extend([context.get("taskId"), context.get("taskRef")])
    spec_refs = brief.get("specRefs") if isinstance(brief.get("specRefs"), dict) else {}
    candidates.extend([spec_refs.get("taskId"), spec_refs.get("taskRef")])
    capsule = brief.get("engineeringTaskCapsule") if isinstance(brief.get("engineeringTaskCapsule"), dict) else {}
    candidates.extend([capsule.get("taskId"), capsule.get("taskRef")])
    for candidate in candidates:
        ref = _normalize_spec_task_ref(candidate)
        if ref:
            return ref
    return ""


def _filter_spec_task_briefs_by_refs(
    briefs: list[dict[str, Any]],
    requested_refs: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    if not requested_refs:
        return list(briefs), []
    requested = set(requested_refs)
    selected: list[dict[str, Any]] = []
    matched: list[str] = []
    for brief in briefs:
        ref = _spec_task_ref_from_brief(brief)
        if ref in requested:
            selected.append(brief)
            if ref not in matched:
                matched.append(ref)
    return selected, matched


def _infer_route_kind_from_payload(payload: dict[str, Any], *fallbacks: Any) -> str:
    candidates: list[str] = []
    for key in ("kind", "runtimeKind", "runtime_kind", "runtime", "capability", "routeIntent", "route_intent", "tool"):
        value = payload.get(key)
        if value is not None:
            candidates.append(str(value))
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    for key in ("kind", "runtimeKind", "capability", "routeIntent", "blockedTool"):
        value = inputs.get(key)
        if value is not None:
            candidates.append(str(value))
    candidates.extend([str(item) for item in fallbacks if item])
    joined = " ".join(candidates).strip().lower().replace("-", "_")
    if not joined:
        return ""
    if any(token in joined for token in ("engineer", "project", "coding", "implementation", "write_native_file", "run_system_command", "install", "build", "workspace")):
        return "engineering"
    if any(token in joined for token in ("research", "search", "evidence", "web_research")):
        return "research"
    if any(token in joined for token in ("delegation", "subagent", "worker", "agent_swarm")):
        return "delegation"
    if any(token in joined for token in ("creative", "media", "asset", "image", "video", "audio")):
        return "creative_media"
    if any(token in joined for token in ("computer_use", "desktop", "browser", "screen")):
        return "computer_use"
    if "rpa" in joined or "trace" in joined:
        return "rpa"
    return _normalize_capability_kind(joined)


def _coerce_route_need_payload(
    need: Any,
    *,
    runtime_kind: Optional[str],
    tool_group: Optional[str],
    reason: Optional[str],
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(need, dict):
        payload = dict(need)
    elif isinstance(need, str):
        raw = need.strip()
        payload = {}
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = dict(parsed)
                elif isinstance(parsed, list):
                    payload = {"taskBriefs": parsed, "reason": reason or "capability_route"}
                else:
                    payload = {"routeIntent": raw, "reason": reason or raw}
            except Exception:
                payload = {"routeIntent": raw, "reason": reason or raw}
    elif need:
        payload = {"reason": str(need)}
    else:
        payload = {}

    route_kind = _infer_route_kind_from_payload(payload, runtime_kind, tool_group, reason)
    if route_kind:
        payload["kind"] = route_kind
    if reason and not str(payload.get("reason") or "").strip():
        payload["reason"] = str(reason).strip()

    inputs = dict(payload.get("inputs") or {}) if isinstance(payload.get("inputs"), dict) else {}
    for source_key, target_key in (
        ("cwd", "workspacePath"),
        ("workspace", "workspacePath"),
        ("workspacePath", "workspacePath"),
        ("workspace_path", "workspacePath"),
        ("task", "task"),
        ("query", "query"),
        ("brief", "brief"),
    ):
        value = payload.get(source_key)
        if value is not None and str(value).strip():
            inputs.setdefault(target_key, value)

    runtime_context = get_runtime_context()
    state_dict = dict(state or {})
    for source in (state_dict, dict(state_dict.get("current_route_context") or {}), runtime_context):
        workspace = str(source.get("workspace_path") or source.get("workspacePath") or "").strip()
        if workspace:
            inputs.setdefault("workspacePath", workspace)
            break
    if inputs:
        payload["inputs"] = inputs
    return payload


def _enrich_route_need_for_episode(
    need: dict[str, Any],
    *,
    kind: str,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    enriched = dict(need or {})
    enriched["kind"] = kind
    enriched.setdefault("source", "supervisor")
    enriched.setdefault("reason", str(enriched.get("reason") or "capability_route").strip() or "capability_route")
    inputs = dict(enriched.get("inputs") or {}) if isinstance(enriched.get("inputs"), dict) else {}
    planner_briefs = _planner_task_briefs_from_state(state)
    spec_bundle = _approved_spec_execution_bundle(enriched, inputs, state=state)
    requested_spec_task_refs = _requested_spec_task_refs(enriched, inputs)
    if spec_bundle:
        inputs.setdefault("specExecutionBundle", spec_bundle)
        enriched.setdefault("specId", spec_bundle.get("specId"))
        if str(spec_bundle.get("status") or "") == "ready":
            spec_groups = _required_runtime_access_from_spec_bundle(spec_bundle, kind)
            if spec_groups:
                existing_groups = list(enriched.get("requiredRuntimeAccess") or [])
                enriched["requiredRuntimeAccess"] = list(dict.fromkeys([*existing_groups, *spec_groups]))

    if kind in {"engineering", "delegation"}:
        route_tasks = planner_briefs or normalize_task_briefs(inputs.get("workerBriefs") or inputs.get("taskBriefs") or inputs.get("tasks") or [])
        task_filter_applied = False
        if (
            spec_bundle
            and str(spec_bundle.get("status") or "") == "ready"
            and (not route_tasks or all(str(task.get("taskBriefId") or "").startswith("route-") for task in route_tasks))
        ):
            route_tasks = _task_briefs_from_spec_bundle(spec_bundle, kind)
        if requested_spec_task_refs:
            original_task_count = len(route_tasks)
            selected_tasks, matched_refs = _filter_spec_task_briefs_by_refs(route_tasks, requested_spec_task_refs)
            if selected_tasks:
                route_tasks = selected_tasks
            else:
                fallback = normalize_task_brief(_minimal_route_task_from_need(enriched, kind))
                fallback["taskBriefId"] = requested_spec_task_refs[0]
                fallback_context = dict(fallback.get("context") or {})
                fallback_context.update(
                    {
                        "source": "requested_spec_task_ref",
                        "requestedSpecTaskRefs": requested_spec_task_refs,
                        "specTaskRefMissing": True,
                    }
                )
                fallback["context"] = fallback_context
                route_tasks = [fallback]
            inputs["selectedSpecTaskIds"] = matched_refs or requested_spec_task_refs
            inputs["specTaskFilter"] = {
                "requested": requested_spec_task_refs,
                "matched": matched_refs,
                "omittedTaskCount": max(0, original_task_count - len(route_tasks)),
                "reason": "explicit_task_ref",
            }
            inputs["targetCount"] = len(route_tasks)
            task_filter_applied = True
        if not route_tasks:
            route_tasks = [normalize_task_brief(_minimal_route_task_from_need(enriched, kind))]
        if task_filter_applied or not inputs.get("workerBriefs"):
            inputs["workerBriefs"] = route_tasks
        if task_filter_applied or not inputs.get("tasks"):
            inputs["tasks"] = route_tasks
        if task_filter_applied or not inputs.get("taskBriefs"):
            inputs["taskBriefs"] = route_tasks
        if kind == "engineering":
            inputs.setdefault(
                "proofExpectations",
                [
                    "Execute through Engineering Runtime.",
                    "Return touched files, commands, verification proof, and remaining risks.",
                ],
            )
    elif kind == "research":
        route_briefs = planner_briefs or normalize_task_briefs(inputs.get("taskBriefs") or inputs.get("tasks") or [])
        brief_query = ""
        for brief in route_briefs:
            if not isinstance(brief, dict):
                continue
            for key in ("routeQuery", "query", "question", "goal", "title"):
                value = str(brief.get(key) or "").strip()
                if value:
                    brief_query = value
                    break
            if brief_query:
                break
        query = str(inputs.get("query") or enriched.get("query") or brief_query or enriched.get("reason") or "").strip()
        if query:
            inputs.setdefault("query", query)
            inputs.setdefault("question", query)
        if not inputs.get("taskBriefs"):
            inputs["taskBriefs"] = route_briefs or [normalize_task_brief(_minimal_route_task_from_need(enriched, kind))]
        inputs.setdefault("sourcePolicy", "multi_source_evidence")
        research_blob = json.dumps(inputs.get("taskBriefs") or [], ensure_ascii=False, default=str).lower()
        if any(marker in research_blob for marker in ("full_read", "multi_source", "evidence_bundle", "claim_table", "claimtable", "sourcematrix", "source_matrix", "citations")):
            inputs.setdefault("mode", "run")

    enriched["inputs"] = inputs
    return enriched


_RUNTIME_LIST_ROUTE_INTENT_MARKERS = (
    "episode",
    "route",
    "wait_episode",
    "queued",
    "queue",
    "handoff",
    "plan_only",
    "work_plan",
    "dispatch",
    "degraded",
    "runtime path",
    "创建 episode",
    "创建运行时",
    "进入运行时",
    "运行时路径",
    "路由",
    "入队",
    "等待",
    "回流",
    "交接",
    "派发",
    "委派",
    "降级",
)


def _runtime_list_request_should_route(
    *,
    need: Any,
    runtime_kind: Optional[str],
    tool_group: Optional[str],
    reason: Optional[str],
    detail_level: str,
) -> bool:
    """Correct list calls that are clearly asking for episode routing.

    Some models use runtime_broker(mode="list") while their arguments say they
    want to create/wait for an episode. Catalog/detail list calls must remain
    harmless discovery, so this only triggers for summary-level list requests
    with explicit route/episode intent.
    """

    normalized_detail = str(detail_level or "summary").strip().lower()
    if normalized_detail in {"catalog", "detail", "full"}:
        return False
    route_kind = _infer_route_kind_from_payload(
        need if isinstance(need, dict) else {},
        runtime_kind,
        tool_group,
        reason,
    )
    if route_kind not in _RUNTIME_ROUTE_DEFAULT_GROUPS:
        return False
    if need:
        return True
    probe = " ".join(
        str(item or "")
        for item in (
            runtime_kind,
            tool_group,
            reason,
        )
    ).strip().lower()
    if not probe:
        return False
    return any(marker in probe for marker in _RUNTIME_LIST_ROUTE_INTENT_MARKERS)


def _append_runtime_episode(
    route_context: dict[str, Any],
    *,
    need: dict[str, Any],
    kind: str,
    groups: list[dict[str, Any]],
    allow_direct_fallback: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    route_context = dict(route_context or {})
    runtime_context = get_runtime_context()
    session_id = str(
        runtime_context.get("session_id")
        or runtime_context.get("sessionId")
        or route_context.get("session_id")
        or route_context.get("sessionId")
        or ""
    ).strip() or None
    run_id = str(
        runtime_context.get("run_id")
        or runtime_context.get("runId")
        or route_context.get("run_id")
        or route_context.get("runId")
        or ""
    ).strip() or None
    root_run_id = str(
        runtime_context.get("root_run_id")
        or runtime_context.get("rootRunId")
        or route_context.get("root_run_id")
        or route_context.get("rootRunId")
        or run_id
        or ""
    ).strip() or None
    workspace_path = str(
        runtime_context.get("workspace_path")
        or runtime_context.get("workspacePath")
        or route_context.get("workspace_path")
        or route_context.get("workspacePath")
        or ""
    ).strip() or None
    bound_need = dict(need or {})
    if session_id:
        bound_need.setdefault("sessionId", session_id)
        bound_need.setdefault("session_id", session_id)
    if run_id:
        bound_need.setdefault("runId", run_id)
        bound_need.setdefault("run_id", run_id)
    if root_run_id:
        bound_need.setdefault("rootRunId", root_run_id)
    inputs = dict(bound_need.get("inputs") or {}) if isinstance(bound_need.get("inputs"), dict) else {}
    if workspace_path:
        bound_need.setdefault("workspacePath", workspace_path)
        bound_need.setdefault("workspace_path", workspace_path)
        inputs.setdefault("workspacePath", workspace_path)
        inputs.setdefault("workspace_path", workspace_path)
    bound_need["inputs"] = inputs
    episode = build_runtime_episode(
        need=bound_need,
        kind=kind,
        state="queued",
        required_runtime_access=[str((item or {}).get("group") or item) for item in groups],
        continuation_target=str(bound_need.get("continuationTarget") or "runtime_episode_runner"),
        extra={"allowDirectFallback": bool(allow_direct_fallback)},
    )
    persisted = enqueue_runtime_episode(episode, session_id=session_id, run_id=run_id, priority=int(need.get("priority") or 0))
    merged_episode = {**episode, **{k: v for k, v in persisted.items() if k in {"session_id", "sessionId", "run_id", "runId", "state", "lastHeartbeatAt"}}}
    if session_id:
        merged_episode.setdefault("sessionId", session_id)
        merged_episode.setdefault("session_id", session_id)
    if run_id:
        merged_episode.setdefault("runId", run_id)
        merged_episode.setdefault("run_id", run_id)
    if root_run_id:
        merged_episode.setdefault("rootRunId", root_run_id)
    return upsert_runtime_episode(route_context, merged_episode), merged_episode


def _emit_runtime_episode_event(topic: str, payload: dict[str, Any]) -> None:
    emit_runtime_episode_event(topic, payload, source={"runtime": "supervisor", "tool": "runtime_broker"})


@tool
def runtime_broker(
    mode: str = "list",
    runtime_kind: Optional[str] = None,
    tool_group: Optional[str] = None,
    tool_groups: Optional[list[str]] = None,
    reason: Optional[str] = None,
    detail_level: str = "summary",
    need: Any = None,
    allow_direct_fallback: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> Command:
    """Supervisor route broker for active execution runtimes.

    Use `mode='route'` with `need={'kind':'research'|'engineering'|'creative_media'|'computer_use'|'rpa'|'delegation', ...}` when strengthened execution is useful: deep evidence, multi-file engineering, media provider work, real desktop/RPA work, or concrete subagent dispatch.
    Do not route ordinary passive support through this tool unless the task explicitly needs it. Memory is usually queried with `memory_broker`; cron/hooks are configured with `manage_cron`/`manage_hook`; Extensions/PluginHost/Network Supervisor are support/discovery surfaces.
    Use `mode='list'` only as a compact route menu; capability details already live in `<capability_registry>`.
    Use `mode='grant'` only for explicit run-scoped tool group access, not as a substitute for runtime execution.
    A route result queues an episode and returns a waitable typed handoff path; do not claim the runtime completed until the handoff/proof returns.
    """
    normalized_mode = str(mode or "list").strip().lower()
    route_context = dict((state or {}).get("current_route_context") or {})
    if normalized_mode == "list" and _runtime_list_request_should_route(
        need=need,
        runtime_kind=runtime_kind,
        tool_group=tool_group,
        reason=reason,
        detail_level=detail_level,
    ):
        normalized_mode = "route"

    if normalized_mode == "list":
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Runtime tool groups available for run-scoped grant.",
                            groups=runtime_tool_groups_catalog(),
                            grants=[
                                {"group": group, "runtimeKind": group.split(".", 1)[0]}
                                for group in runtime_access_from_route_context(route_context)
                            ],
                            detail_level=detail_level,
                            next_action="Prefer runtime_broker(mode='route', need={'kind':'research'|'engineering'|...}); use grant only for explicit tool group access.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": route_context,
            },
        )

    if normalized_mode == "wait_episode":
        active_episode = next(
            (
                item
                for item in list(route_context.get("capabilityEpisodes") or [])
                if isinstance(item, dict)
                and str(item.get("state") or "").strip().lower()
                in {"queued", "active", "running", "leased"}
            ),
            None,
        )
        if not active_episode:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="No queued or active runtime episode is available to wait for.",
                                error="no_active_episode",
                                detail_level=detail_level,
                                next_action="Call runtime_broker(mode='route', need={...}) first, or continue after an existing handoff.",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                },
            )
        episode_id = str(active_episode.get("episodeId") or active_episode.get("needId") or "").strip()
        episode_kind = str(active_episode.get("kind") or "").strip()
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Runtime episode wait requested; the graph will continue at runtime_episode_wait.",
                            episode=active_episode,
                            detail_level=detail_level,
                            next_action="Runtime wait is a graph transition; do not keep calling runtime_broker(mode='wait_episode').",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": route_context,
                "planner_dispatch_status": {
                    "mode": "runtime_broker_wait_episode",
                    "dispatched": True,
                    "blocked": False,
                    "reason": "runtime_episode_wait_requested",
                    "episodeId": episode_id,
                    "episodeKind": episode_kind,
                    "episodeCount": 1,
                    "nextAction": "wait_episode",
                },
            },
        )

    if normalized_mode == "route":
        need_payload = _coerce_route_need_payload(
            need,
            runtime_kind=runtime_kind,
            tool_group=tool_group,
            reason=reason,
            state=state,
        )
        route_kind = _normalize_capability_kind(need_payload.get("kind") or runtime_kind or tool_group)
        if not route_kind:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="runtime_broker(mode=route) requires need.kind or runtime_kind.",
                                error="missing_capability_kind",
                                next_action="Call runtime_broker(mode='route', need={'kind':'research'|'engineering'|...}).",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                },
            )
        need_payload = _enrich_route_need_for_episode(need_payload, kind=route_kind, state=state)
        requested_groups = _capability_route_groups(
            need=need_payload,
            runtime_kind=runtime_kind or route_kind,
            tool_group=tool_group,
            tool_groups=tool_groups,
        )
        updated_context = route_context
        grants: list[dict[str, Any]] = []
        rejected: list[str] = []
        if requested_groups:
            updated_context, grants, rejected = grant_runtime_tool_groups(
                route_context,
                requested_groups,
                reason=str(reason or need_payload.get("reason") or "capability_route").strip(),
            )
        updated_context, episode = _append_runtime_episode(
            updated_context,
            need=need_payload,
            kind=route_kind,
            groups=grants,
            allow_direct_fallback=allow_direct_fallback,
        )
        _emit_runtime_episode_event("capability.need.detected", {"episode": episode})
        _emit_runtime_episode_event("runtime.episode.queued", {"episode": episode})
        if route_kind in {"engineering", "delegation"}:
            next_action = "wait_episode"
        elif route_kind == "research":
            next_action = "wait_episode"
        elif route_kind == "creative_media":
            next_action = "wait_episode"
        elif route_kind in {"computer_use", "rpa"}:
            next_action = "wait_episode"
        else:
            next_action = "wait_episode"
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=not rejected,
                            summary=f"Routed capability need to {route_kind}.",
                            grants=grants,
                            rejected=rejected,
                            error="unknown_tool_group" if rejected else None,
                            detail_level=detail_level,
                            changed=grants,
                            episode=episode,
                            next_action="Runtime episode queued; the graph will wait for the typed handoff automatically. Do not call runtime_broker(mode='wait_episode') unless a previous route result was not handed to the graph.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": updated_context,
                "planner_dispatch_status": {
                    "mode": "runtime_broker_route",
                    "dispatched": True,
                    "blocked": False,
                    "reason": "runtime_episode_queued",
                    "episodeId": str(episode.get("episodeId") or ""),
                    "episodeKind": route_kind,
                    "episodeCount": 1,
                    "nextAction": "wait_episode",
                },
            },
        )

    requested_groups = list(tool_groups or [])
    if tool_group:
        requested_groups.append(tool_group)
    requested_groups = normalize_runtime_access(requested_groups, runtime_kind=runtime_kind)

    if normalized_mode == "status":
        active_groups = runtime_access_from_route_context(route_context)
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Current run-scoped runtime tool grants.",
                            groups=runtime_tool_groups_catalog(),
                            grants=[
                                {"group": group, "runtimeKind": group.split(".", 1)[0]}
                                for group in active_groups
                            ],
                            detail_level=detail_level,
                            next_action="Use granted tools or grant/revoke a group.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": route_context,
            },
        )

    if normalized_mode == "grant":
        if not requested_groups:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="runtime_broker(mode=grant) requires tool_group or tool_groups.",
                                error="missing_tool_group",
                                next_action="Call list, then grant a group id.",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                },
            )
        updated_context, grants, rejected = grant_runtime_tool_groups(
            route_context,
            requested_groups,
            reason=str(reason or "").strip(),
        )
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=not rejected,
                            summary=(
                                "Runtime tool group granted for this run. It will be visible on the next supervisor step."
                                if not rejected
                                else "Some requested runtime tool groups were not granted."
                            ),
                            grants=grants,
                            groups=runtime_tool_groups_catalog() if str(detail_level or "").strip().lower() in {"catalog", "detail", "full"} else [],
                            rejected=rejected,
                            error="unknown_tool_group" if rejected else None,
                            detail_level=detail_level,
                            changed=grants,
                            next_action="Next step can use the granted tools.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": updated_context,
            },
        )

    if normalized_mode == "revoke":
        updated_context, grants = revoke_runtime_tool_groups(
            route_context,
            requested_groups if requested_groups else None,
        )
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Runtime tool grants updated for this run.",
                            grants=grants,
                            detail_level=detail_level,
                            changed=grants,
                            next_action="Continue with the remaining grants.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": updated_context,
            },
        )

    return Command(
        goto="supervisor",
        update={
            "messages": [
                ToolMessage(
                    content=_runtime_broker_payload(
                        mode=normalized_mode or "unknown",
                        ok=False,
                        summary=f"Unsupported runtime_broker mode: {normalized_mode}",
                        error="unsupported_mode",
                        next_action="Use one of: list, route, wait_episode, status, grant, revoke.",
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
            "current_route_context": route_context,
        },
    )
__all__ = [
    "runtime_broker",
    "_append_runtime_episode",
    "_capability_route_groups",
    "_coerce_route_need_payload",
    "_emit_runtime_episode_event",
    "_enrich_route_need_for_episode",
    "_infer_route_kind_from_payload",
    "_minimal_route_task_from_need",
    "_normalize_capability_kind",
    "_planner_task_briefs_from_state",
    "_runtime_broker_payload",
    "_runtime_list_request_should_route",
]
