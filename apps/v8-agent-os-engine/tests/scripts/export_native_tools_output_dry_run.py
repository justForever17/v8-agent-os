from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = ENGINE_ROOT.parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "docs" / "tools"

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

from core.native_tools import NATIVE_TOOLS  # noqa: E402
from core.tools.native.registry import native_tool_family_for_name  # noqa: E402
from core.runtime_tool_access import RUNTIME_TOOL_GROUPS, filter_visible_tools_for_actor  # noqa: E402
from core.tool_surface import (  # noqa: E402
    MAX_TOOL_OUTPUT_LENGTH,
    TOOL_OUTPUT_TARGET_CHARS,
    record_raw_observation,
    runtime_kind_for_tool,
)
from graph.tool_routing import create_routed_tool_node  # noqa: E402
from runtimes.extensions.skills.loader import fetch_skill_instructions  # noqa: E402


EXCLUDED_TOOLS = {"ask_user"}
EXTRA_SYSTEM_TOOLS = [fetch_skill_instructions]
COMMAND_TOOL_NAMES = {
    "run_system_command",
    "execute_system_command",
    "command_session_broker",
    "read_background_output",
    "send_background_input",
    "terminate_background_command",
}
DEFAULT_VISIBLE_HARD_BUDGET = 8000
SUPERVISOR_DEFAULT_BUDGET = 1000
WORKSPACE_BROKER_VISIBLE_BUDGET = 1200
COMMAND_VISIBLE_BUDGET = 2500
GRANT_LIST_VISIBLE_BUDGET = 1800
DETAIL_VISIBLE_BUDGET = 4000
DEFAULT_VISIBLE_BUDGET = 6000

UNSAFE_REASONS: dict[str, str] = {
    "rpa_run_draft": "would execute a Robot Framework draft script",
    "rpa_run_existing_flow": "would execute a Robot Framework flow",
    "creative_media_create_job": "would create a provider-facing media job",
    "creative_media_compile_recipe": "compiles and persists a Creative Media recipe",
    "creative_media_register_asset": "would mutate the Creative Media asset ledger",
    "creative_media_create_character_bible": "would mutate the Creative Media character ledger",
    "creative_media_register_keyframe": "would mutate the Creative Media keyframe ledger",
    "creative_media_create_edit_plan": "would mutate the Creative Media edit-plan ledger",
    "creative_media_render_edit_plan": "would render media and record artifacts",
    "creative_media_create_quality_job": "would create a quality-check ledger entry",
    "creative_media_retry_job": "would retry a provider-facing media job",
    "computer_use_click": "would perform a real desktop click",
    "computer_use_type_text": "would type into the desktop",
    "computer_use_hotkey": "would send a real desktop hotkey",
    "computer_use_scroll": "would scroll the desktop",
    "computer_use_wait_for_element": "can block while polling the desktop",
    "computer_use_capture_screenshot": "would capture and write a screenshot artifact",
    "computer_use_open_app": "would launch an application",
    "computer_use_focus_window": "would focus a desktop window",
    "computer_use_find_and_type": "would type into the desktop",
    "computer_use_scroll_list": "would scroll the desktop",
    "computer_use_click_toolbar_action": "would click in a desktop toolbar",
    "computer_use_execute_plan": "would execute a desktop action plan",
    "computer_use_execute_task": "would execute a high-level desktop task",
    "computer_use_launch_app": "would launch an application",
    "computer_use_ensure_window": "would launch or focus an application window",
    "computer_use_click_target": "would click a visual desktop target",
    "computer_use_input_text": "would type into a desktop target",
    "computer_use_paste_text": "would paste text into the desktop",
    "computer_use_paste_files": "would paste files into the desktop",
    "computer_use_right_click_target": "would right-click a desktop target",
    "computer_use_hover_target": "would move the pointer over a desktop target",
    "computer_use_send_hotkey": "would send a real desktop hotkey",
    "computer_use_scroll_view": "would scroll the desktop",
    "computer_use_drag_pointer": "would drag the pointer on the desktop",
    "share_workspace_file": "would publish or ledger a workspace file",
    "write_native_file": "would write to the filesystem",
    "download_media_for_vision": "may download media and write artifacts",
    "web_broker": "may perform external network access",
    "web_fetch": "performs external network access",
    "web_read": "performs external network access",
    "web_extract": "performs external network access",
    "web_search": "performs external network access",
    "delegate_network_task": "would delegate work to a remote V8 peer",
    "http_request": "performs external network access",
    "s3_broker": "may access S3 or compatible object storage",
    "s3_upload_file": "would upload to object storage",
    "s3_list_objects": "would access object storage",
    "s3_download_file": "would download from object storage",
    "manage_process": "would manage a real host process",
    "mem_delete": "would delete durable memory",
    "mem_update": "would mutate durable memory",
    "vision_media_analyzer": "requires model/media inputs and may call a vision model",
}

STATEFUL_UNOBSERVED_REASONS: dict[str, str] = {
    "delegation_broker": "representative observe/resume requires an existing external_worker delegation session; dispatch has side effects",
}

BASE_SAFE_INVOCATIONS: dict[str, dict[str, Any]] = {
    "runtime_broker": {"mode": "list"},
    "mcp_server_config": {"mode": "mcp_list"},
    "spec_broker": {"mode": "brief", "workspace_path": ""},
    "workspace_broker": {"mode": "inspect", "path": ".", "depth": 1, "max_entries": 30},
    "rpa_list_robot_scripts": {"limit": 5},
    "creative_media_catalog": {"detail_level": "summary", "limit": 8},
    "creative_media_resolutions": {},
    "creative_media_list_jobs": {"modality": None, "status": None, "limit": 10},
    "creative_media_list_recipes": {"modality": None, "recipe_kind": None, "limit": 10},
    "creative_media_list_assets": {"modality": None, "role": None, "limit": 10},
    "creative_media_list_character_bibles": {"limit": 10},
    "creative_media_list_keyframes": {"recipe_id": None, "role": None, "character_bible_id": None, "limit": 10},
    "creative_media_list_edit_plans": {"recipe_id": None, "limit": 10},
    "creative_media_list_renders": {"plan_id": None, "status": None, "limit": 10},
    "creative_media_list_quality_jobs": {"status": None, "limit": 10},
    "creative_media_cost_ledger": {"limit": 10},
    "creative_media_safety_events": {"limit": 10},
    "creative_media_compile_work_order": {
        "request": {
            "intent": "simple_asset",
            "modality": "image",
            "assetRole": "background",
            "brief": "Dry-run calibration image for native tool output docs.",
            "aspectRatio": "16:9",
            "qualityTier": "draft",
            "costLimit": 0,
            "requestingRuntime": "calibration",
        }
    },
    "creative_media_list_work_orders": {"status": None, "requesting_runtime": None, "limit": 10},
    "computer_use_list_apps": {"app_query": None, "limit": 10, "include_running": True, "force_refresh": False},
    "computer_use_list_primitives": {"category": None, "detail_level": "summary"},
    "computer_use_desktop_capabilities": {"detail_level": "summary"},
    "computer_use_lookup_muscle_memory": {"goal": "observe available reusable desktop routes", "app": None, "variables_json": None, "limit": 3},
    "computer_use_list_muscle_memories": {"app": None, "status": None, "limit": 5},
    "computer_use_resolve_execution_route": {"goal": "observe available reusable desktop routes", "app": None, "target": None, "variables_json": None, "limit": 3},
    "computer_use_observe_scene": {
        "app": None,
        "window_title": None,
        "include_screenshot": False,
        "depth_limit": 1,
        "element_limit": 5,
    },
    "computer_use_observe": {
        "window_title": "__v8_calibration_no_such_window__",
        "include_screenshot": False,
        "depth_limit": 1,
        "element_limit": 5,
    },
    "computer_use_list_windows": {"title_filter": "__v8_calibration_no_such_window__", "limit": 5},
    "computer_use_find_element": {"name_contains": "__v8_calibration_no_such_element__", "limit": 3},
    "read_native_file": {
        "path": str(ENGINE_ROOT / "core" / "native_tools.py"),
        "start_line": 1,
        "end_line": 20,
    },
    "grep_search": {"query": "NATIVE_TOOLS", "path": str(ENGINE_ROOT / "core" / "native_tools.py"), "regex": False},
    "wait": {"seconds": 1, "note": "native tools output calibration"},
    "list_processes": {"name_pattern": "__v8_calibration_no_such_process__"},
    "manage_cron": {"action": "list"},
    "manage_hook": {"action": "list"},
    "read_audit_log": {"limit": 1},
    "research_broker": {
        "mode": "plan",
        "question": "Compare official docs for V8 provider APIs",
        "researchIntent": "source quality and provider API evidence",
        "freshness": "auto",
        "sourcePolicy": "authoritative",
        "maxShards": 4,
        "maxRounds": 1,
        "deliverable": "evidence_bundle",
    },
    "fetch_skill_instructions": {"skill_name": "__v8_calibration_missing_skill__", "detail_level": "summary"},
    "memory_broker": {"mode": "explain_injection", "limit": 3},
    "memory_recall": {"query": "native-tool-output-calibration", "limit": 1},
    "mem_summary": {"tier": "day", "date": "2099-01-01"},
    "memory_map": {"anchor_date": "2099-01-01"},
    "memory_map_expand": {"memory_ref": "calibration-missing-memory-ref"},
    "memory_read_day": {"memory_ref_or_date": "2099-01-01"},
    "write_todos": {
        "task_name": "native-tools-calibration",
        "plan_markdown": "Calibration-only plan.",
        "todos": ["capture agent-visible output shape"],
    },
    "update_todo": {"index": 0, "status": "done"},
}

SPECIAL_SCENARIOS = {
    "run_system_command",
    "command_session_broker",
    "read_background_output",
    "send_background_input",
    "terminate_background_command",
}
DETAIL_SCENARIOS = {"tool_observation_detail"}

LEDGER_ID_SCENARIOS: dict[str, dict[str, Any]] = {
    "creative_media_get_job": {
        "listTool": "creative_media_list_jobs",
        "listArgs": {"limit": 10},
        "listKey": "jobs",
        "idKey": "jobId",
        "argName": "job_id",
        "extraArgs": {"refresh": False},
    },
    "creative_media_job_artifacts": {
        "listTool": "creative_media_list_jobs",
        "listArgs": {"limit": 10},
        "listKey": "jobs",
        "idKey": "jobId",
        "argName": "job_id",
    },
    "creative_media_get_recipe": {
        "listTool": "creative_media_list_recipes",
        "listArgs": {"limit": 10},
        "listKey": "recipes",
        "idKey": "recipeId",
        "argName": "recipe_id",
    },
    "creative_media_get_character_bible": {
        "listTool": "creative_media_list_character_bibles",
        "listArgs": {"limit": 10},
        "listKey": "characterBibles",
        "idKey": "characterBibleId",
        "argName": "character_bible_id",
    },
    "creative_media_get_keyframe": {
        "listTool": "creative_media_list_keyframes",
        "listArgs": {"limit": 10},
        "listKey": "keyframes",
        "idKey": "keyframeId",
        "argName": "keyframe_id",
    },
    "creative_media_get_edit_plan": {
        "listTool": "creative_media_list_edit_plans",
        "listArgs": {"limit": 10},
        "listKey": "editPlans",
        "idKey": "planId",
        "argName": "plan_id",
    },
    "creative_media_get_render": {
        "listTool": "creative_media_list_renders",
        "listArgs": {"limit": 10},
        "listKey": "renders",
        "idKey": "renderJobId",
        "argName": "render_job_id",
    },
    "creative_media_get_quality_job": {
        "listTool": "creative_media_list_quality_jobs",
        "listArgs": {"limit": 10},
        "listKey": "qualityJobs",
        "idKey": "qualityJobId",
        "argName": "quality_job_id",
    },
}


DIRTY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ansi_escape", re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")),
    ("escaped_ansi", re.compile(r"\\u001b|\\x1b|\\033", re.IGNORECASE)),
    ("nul_byte", re.compile("\x00")),
    ("other_control_chars", re.compile(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]")),
    ("powershell_progress_marker", re.compile(r"\bProgressPreference\b|Write-Progress", re.IGNORECASE)),
    ("terminal_box_drawing_noise", re.compile(r"[╔╗╚╝║═]{3,}")),
    ("ffmpeg_stderr_long_line", re.compile(r"ffmpeg.*^.{1200,}$", re.IGNORECASE | re.MULTILINE | re.DOTALL)),
    ("very_long_line", re.compile(r"^.{2000,}$", re.MULTILINE)),
]

TRUNCATION_RE = re.compile(r"Original length:\s*(\d+)\s*chars")


@dataclass
class ToolCalibrationRecord:
    name: str
    status: str
    args: dict[str, Any] | None
    description: str
    output: str
    client_surface: dict[str, Any]
    diagnostics: dict[str, Any]
    representative: bool
    representative_reason: str
    scenario_name: str
    raw_chars: int
    visible_chars: int
    truncated_by_tool_node: bool
    skip_reason: str | None = None
    agent_visible: bool = True
    surface_visibility: dict[str, Any] | None = None


def _all_export_tools() -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for tool_ref in [*NATIVE_TOOLS, *EXTRA_SYSTEM_TOOLS]:
        name = str(getattr(tool_ref, "name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(tool_ref)
    return result


def _tool_names() -> list[str]:
    return [str(getattr(tool_ref, "name", "")).strip() for tool_ref in _all_export_tools()]


def _visibility_for_tool(tool_name: str, tools: list[Any]) -> dict[str, Any]:
    def names_for(actor: str, *, route_context: dict[str, Any] | None = None, runtime_access: list[str] | None = None) -> set[str]:
        return {
            str(getattr(tool_ref, "name", "")).strip()
            for tool_ref in filter_visible_tools_for_actor(
                tools,
                actor=actor,
                route_context=route_context or {},
                runtime_access=runtime_access,
            )
            if str(getattr(tool_ref, "name", "")).strip()
        }

    surface_names = {
        "supervisor/default": names_for("supervisor"),
        "supervisor/grants/computer_use.control": names_for(
            "supervisor",
            route_context={"runtimeToolGrants": [{"group": "computer_use.control", "runtimeKind": "computer_use"}]},
        ),
        "supervisor/grants/creative_media.core": names_for(
            "supervisor",
            route_context={"runtimeToolGrants": [{"group": "creative_media.core", "runtimeKind": "creative_media"}]},
        ),
        "supervisor/grants/research.core": names_for(
            "supervisor",
            route_context={"runtimeToolGrants": [{"group": "research.core", "runtimeKind": "research"}]},
        ),
        "supervisor/grants/automation.ops": names_for(
            "supervisor",
            route_context={"runtimeToolGrants": [{"group": "automation.ops", "runtimeKind": "automation"}]},
        ),
        "subagent/contextual_default": names_for("subagent", runtime_access=[]),
        "subagent/runtimeAccess/computer_use.control": names_for("subagent", runtime_access=["computer_use.control"]),
        "subagent/runtimeAccess/creative_media.core": names_for("subagent", runtime_access=["creative_media.core"]),
        "subagent/runtimeAccess/research.core": names_for("subagent", runtime_access=["research.core"]),
    }
    registered_groups = [
        group_name
        for group_name, group in RUNTIME_TOOL_GROUPS.items()
        if tool_name in set(group.get("toolNames") or [])
    ]
    return {
        "registered": True,
        "runtimeKind": runtime_kind_for_tool(tool_name),
        "toolFamily": native_tool_family_for_name(tool_name),
        "registeredGroups": registered_groups,
        "grantRequired": bool(registered_groups),
        "surfaces": {
            surface: {
                "visible": tool_name in names,
                "visibilityReason": "visible" if tool_name in names else ("grant_required" if registered_groups else "hidden_by_policy_or_surface"),
            }
            for surface, names in surface_names.items()
        },
        "agentVisibleBudget": TOOL_OUTPUT_TARGET_CHARS.get("default"),
        "rawRefPolicy": "artifact_or_runtime_ledger"
        if runtime_kind_for_tool(tool_name) in {"computer_use", "creative_media"}
        else "observability_tool_observation",
    }


def native_tool_names_to_export() -> list[str]:
    return [name for name in _tool_names() if name not in EXCLUDED_TOOLS]


def missing_invocation_names() -> list[str]:
    names = set(native_tool_names_to_export())
    covered = (
        set(BASE_SAFE_INVOCATIONS)
        | set(SPECIAL_SCENARIOS)
        | set(DETAIL_SCENARIOS)
        | set(LEDGER_ID_SCENARIOS)
        | set(UNSAFE_REASONS)
        | set(STATEFUL_UNOBSERVED_REASONS)
    )
    return sorted(names - covered)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _as_record(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _text_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _json_dumps(value)


def _compact_text(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", _redact_client_text(str(value or ""))).strip()
    if not text:
        return ""
    return text if len(text) <= limit else f"{text[: max(1, limit - 3)].rstrip()}..."


def _redact_client_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"\b[A-Za-z]:\\(?:Users|Projects|ProgramData|Windows|temp|Temp)[^\s,，;；)）\]}]*",
        "[local path]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bactiveWorkspaceRoot=\[local path\]", "activeWorkspaceRoot=[hidden]", text, flags=re.IGNORECASE)
    text = re.sub(r"\bworkspacePath=\[local path\]", "workspacePath=[hidden]", text, flags=re.IGNORECASE)
    return text


def _visible_lines(text: str) -> list[str]:
    return [
        line
        for line in (line.strip() for line in str(text or "").splitlines())
        if line
        and not re.match(r"^(```|---)$", line)
        and not re.match(r"^\[scenario:[^\]]+\]$", line, re.IGNORECASE)
    ]


def _pick_line(text: str, patterns: list[re.Pattern[str]]) -> str:
    lines = _visible_lines(text)
    for pattern in patterns:
        for line in lines:
            if pattern.search(line):
                return line
    return lines[0] if lines else ""


def _pick_matching_line(text: str, patterns: list[re.Pattern[str]]) -> str:
    lines = _visible_lines(text)
    for pattern in patterns:
        for line in lines:
            if pattern.search(line):
                return line
    return ""


def _pick_record_text(record: dict[str, Any] | None, keys: list[str]) -> str:
    if not record:
        return ""
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _has_failure_line(result_text: str) -> bool:
    for line in _visible_lines(result_text):
        if re.match(r"^[-*]\s+", line):
            continue
        if (
            re.search(r"^(status|result)[:：]\s*(failed|failure|error|exception)\b(?!\s*[=:])", line, re.IGNORECASE)
            or re.search(r"^(failed|failure|error|exception)[:：\s]", line, re.IGNORECASE)
            or re.search(r"^(失败|错误)[:：\s]", line)
        ):
            return True
    return False


def _client_summary(result: Any) -> str:
    if isinstance(result, str):
        return _compact_text(
            _pick_line(
                result,
                [
                    re.compile(
                        r"^(摘要|结果|答案|关键发现|正文内容|输出|状态|风险|限制|下一步|Summary|Result|Answer|Key findings|Content|Output|Status|Risk|Limitations|Next)[:：]",
                        re.IGNORECASE,
                    )
                ],
            )
        )
    record = _as_record(result)
    picked = _pick_record_text(record, ["summary", "message", "statusMessage", "answer", "result", "error", "status"])
    if picked:
        return _compact_text(picked)
    fallback = _text_value(result)
    if fallback.startswith("{") or fallback.startswith("["):
        return ""
    return _compact_text(fallback)


def _client_actionable(result_text: str, record: dict[str, Any] | None) -> str | None:
    direct = _pick_record_text(record, ["recommendedNextAction", "nextAction", "actionable"])
    if direct:
        return _compact_text(direct, 160)
    line = _pick_matching_line(result_text, [re.compile(r"^(下一步|Next|Action)[:：]", re.IGNORECASE)])
    return _compact_text(line, 160) if line else None


def _client_progress(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    progress = record.get("progress")
    if isinstance(progress, str) and progress.strip():
        return _compact_text(progress, 80)
    completed = record.get("completed", record.get("done", record.get("completedCount")))
    total = record.get("total", record.get("totalCount", record.get("targetCount")))
    if completed is not None and total is not None:
        return f"{completed}/{total}"
    return None


def _client_ref_ids(result_text: str, max_refs: int = 4) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    patterns = [
        re.compile(
            r"['\"]?\b(?:rawRef|detailRef|sectionRef|skillRef|relativeFileRef|memoryRef|answerPackRef|chunkRef|fileRef|episodeId|handoffId|jobId|artifactId)\b['\"]?\s*[:=]\s*['\"]?([^\"'`,\s，)）\]}]+)",
            re.IGNORECASE,
        ),
        re.compile(r"\btoolobs://[^\"'`,\s，)）\]}]+", re.IGNORECASE),
    ]
    for pattern in patterns:
        for match in pattern.finditer(result_text):
            value = match.group(1) if match.groups() else match.group(0)
            if value and value not in seen:
                refs.append(value)
                seen.add(value)
            if len(refs) >= max_refs:
                return refs
    return refs


def _client_status(state: str, result_text: str, record: dict[str, Any] | None) -> str:
    normalized_state = str(state or "").lower()
    status_text = str((record or {}).get("status") or (record or {}).get("state") or "").lower()
    combined = f"{status_text}\n{result_text[:2000]}".lower()
    if re.search(r"(unsafe_unobserved|blocked|safety_blocked|拒绝|阻断)", combined):
        return "blocked"
    if re.search(r"(stateful_unobserved|waiting|approval|ask_user|等待|审批)", combined):
        return "waiting"
    if (
        re.search(r"^(failed|failure|error|exception)$", status_text)
        or _has_failure_line(result_text)
    ):
        return "failed"
    if normalized_state in {"result", "completed", "invoked"}:
        return "completed"
    if normalized_state in {"call", "running"}:
        return "running"
    return "unknown"


def build_client_tool_surface(tool_name: str, result: Any, *, state: str = "result") -> dict[str, Any]:
    result_text = _text_value(result)
    record = _as_record(result)
    return {
        "title": str(tool_name or "tool").strip() or "tool",
        "status": _client_status(state, result_text, record),
        "summary": _client_summary(result),
        "progress": _client_progress(record),
        "actionable": _client_actionable(result_text, record),
        "refIds": _client_ref_ids(result_text),
    }


def _message_content(message: Any) -> str | None:
    if isinstance(message, ToolMessage):
        return str(message.content)
    content = getattr(message, "content", None)
    if content is None:
        return None
    if isinstance(content, str):
        return content
    return _json_dumps(content)


def _command_messages(command: Command) -> list[str]:
    update = getattr(command, "update", None)
    if not isinstance(update, dict):
        return []
    return [
        content
        for message in list(update.get("messages") or [])
        if (content := _message_content(message)) is not None
    ]


def _stringify_agent_visible_result(result: Any) -> str:
    if isinstance(result, ToolMessage):
        return str(result.content)
    if isinstance(result, Command):
        messages = _command_messages(result)
        if messages:
            return "\n\n".join(messages)
        return repr(result)
    if isinstance(result, dict):
        messages = [
            content
            for message in list(result.get("messages") or [])
            if (content := _message_content(message)) is not None
        ]
        if messages:
            return "\n\n".join(messages)
        return _json_dumps(result)
    if isinstance(result, list):
        parts = []
        for item in result:
            rendered = _stringify_agent_visible_result(item)
            if rendered:
                parts.append(rendered)
        return "\n\n".join(parts)
    content = getattr(result, "content", None)
    if content is not None:
        return str(content)
    return str(result)


async def _invoke_agent_visible(tool_ref: Any, args: dict[str, Any], *, state_extra: dict[str, Any] | None = None) -> str:
    node = create_routed_tool_node([tool_ref], name="native_tool_calibration", fallback_goto="supervisor")
    tool_name = str(getattr(tool_ref, "name", "unknown"))
    call_id = f"calibration-{tool_name}-{abs(hash(json.dumps(args, sort_keys=True, default=str))) % 1000000}"
    tool_call = {
        "name": tool_name,
        "args": dict(args or {}),
        "id": call_id,
        "type": "tool_call",
    }
    state = dict(state_extra or {})
    state["messages"] = list(state.get("messages") or []) + [AIMessage(content="", tool_calls=[tool_call])]
    result = await node(state)
    return _stringify_agent_visible_result(result)


async def _invoke_raw_tool(tool_ref: Any, args: dict[str, Any]) -> Any:
    if hasattr(tool_ref, "ainvoke"):
        return await tool_ref.ainvoke(dict(args or {}))
    if hasattr(tool_ref, "invoke"):
        result = tool_ref.invoke(dict(args or {}))
    elif callable(tool_ref):
        result = tool_ref(**dict(args or {}))
    else:
        return None
    if asyncio.iscoroutine(result):
        return await result
    return result


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = str(text or "").strip()
    if not stripped:
        return {}
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else {}
    except Exception:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(stripped[start : end + 1])
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_command_id(text: str) -> str:
    payload = _parse_json_object(text)
    for key in ("commandId", "sessionId"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    match = re.search(r'"(?:commandId|sessionId)"\s*:\s*"([^"]+)"', text)
    if match:
        return match.group(1)
    match = re.search(r"\[session:\s*([^\]\s]+)\]", text)
    if match:
        return match.group(1)
    match = re.search(r"<command session\s+([^>\s]+)>", text)
    return match.group(1) if match else ""


def _scenario_sections(sections: list[tuple[str, str]]) -> str:
    return "\n\n".join(
        f"[scenario:{name}]\n{content}".strip()
        for name, content in sections
        if str(content or "").strip()
    )


def analyze_output(text: str) -> dict[str, Any]:
    hits = []
    for key, pattern in DIRTY_PATTERNS:
        if pattern.search(text):
            hits.append(key)
    lines = text.splitlines()
    stripped = str(text or "").strip()
    json_like = stripped.startswith("{") or bool(re.match(r"^\[\s*(?:\{|\[|\")", stripped)) or "_v8ToolSurface" in stripped
    return {
        "charCount": len(text),
        "lineCount": len(lines),
        "maxLineLength": max((len(line) for line in lines), default=len(text)),
        "dirtySignals": hits,
        "jsonLikeVisible": json_like,
    }


def _raw_chars_from_visible(text: str) -> int:
    match = TRUNCATION_RE.search(text)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return len(text)
    return len(text)


def _make_record(
    *,
    name: str,
    status: str,
    args: dict[str, Any] | None,
    description: str,
    output: str,
    representative: bool,
    representative_reason: str,
    scenario_name: str,
    skip_reason: str | None = None,
    surface_visibility: dict[str, Any] | None = None,
) -> ToolCalibrationRecord:
    diagnostics = analyze_output(output)
    client_surface = build_client_tool_surface(name, output, state=status)
    return ToolCalibrationRecord(
        name=name,
        status=status,
        args=args,
        description=description,
        output=output,
        client_surface=client_surface,
        diagnostics=diagnostics,
        representative=representative,
        representative_reason=representative_reason,
        scenario_name=scenario_name,
        raw_chars=_raw_chars_from_visible(output),
        visible_chars=len(output),
        truncated_by_tool_node="...[OUTPUT TRUNCATED BY SYSTEM." in output,
        skip_reason=skip_reason,
        surface_visibility=surface_visibility,
    )


async def _start_helper_session(by_name: dict[str, Any], command: str, *, profile: str = "shell") -> tuple[str, str]:
    start_output = await _invoke_agent_visible(
        by_name["command_session_broker"],
        {"mode": "start", "command": command, "profile": profile, "debug": False},
    )
    return _parse_command_id(start_output), start_output


async def _cleanup_session(by_name: dict[str, Any], command_id: str) -> str:
    if not command_id:
        return ""
    try:
        return await _invoke_agent_visible(
            by_name["command_session_broker"],
            {"mode": "terminate", "session_id": command_id, "debug": False},
        )
    except Exception as exc:  # noqa: BLE001 - cleanup must not hide the sampled output
        return f"cleanup_error: {exc}"


async def _collect_command_scenario(name: str, tool_ref: Any, by_name: dict[str, Any], description: str) -> ToolCalibrationRecord:
    if name == "run_system_command":
        args = {
            "command": "powershell -NoProfile -Command \"Write-Output v8-run-system-start; Start-Sleep -Seconds 2; Write-Output v8-run-system-done\"",
            "mode": "session",
            "profile": "shell",
        }
        output = await _invoke_agent_visible(tool_ref, args)
        command_id = _parse_command_id(output)
        cleanup = await _cleanup_session(by_name, command_id)
        diagnostics_output = _scenario_sections([("session", output), ("cleanup", cleanup)]) if cleanup else output
        return _make_record(
            name=name,
            status="invoked",
            args=args,
            description=description,
            output=diagnostics_output,
            representative=True,
            representative_reason="sampled run_system_command(mode=session) through ToolNode and cleaned up the spawned session",
            scenario_name="session_start_cleanup",
        )

    if name == "command_session_broker":
        command = "python -c \"import time; time.sleep(1); print('v8-done')\""
        start_args = {"mode": "start", "command": command, "profile": "shell", "debug": False}
        start_output = await _invoke_agent_visible(tool_ref, start_args)
        command_id = _parse_command_id(start_output)
        await asyncio.sleep(1.6)
        observe_output = await _invoke_agent_visible(tool_ref, {"mode": "observe", "session_id": command_id, "debug": False}) if command_id else ""
        terminate_output = (
            await _invoke_agent_visible(tool_ref, {"mode": "terminate", "session_id": command_id, "debug": False})
            if command_id and ("[still running]" in observe_output or "[waiting for input]" in observe_output)
            else ""
        )
        output = _scenario_sections([("start", start_output), ("observe", observe_output), ("terminate", terminate_output)])
        return _make_record(
            name=name,
            status="invoked",
            args=start_args,
            description=description,
            output=output,
            representative=True,
            representative_reason="sampled start/observe/terminate agent-visible messages for a harmless short command session",
            scenario_name="start_observe_terminate",
        )

    if name == "read_background_output":
        command_id, setup_output = await _start_helper_session(
            by_name,
            "powershell -NoProfile -Command \"Write-Output v8-bg-read; Start-Sleep -Seconds 2\"",
        )
        args = {"command_id": command_id}
        output = await _invoke_agent_visible(tool_ref, args) if command_id else setup_output
        cleanup = await _cleanup_session(by_name, command_id)
        if cleanup:
            output = _scenario_sections([("read", output), ("cleanup", cleanup)])
        return _make_record(
            name=name,
            status="invoked",
            args=args,
            description=description,
            output=output,
            representative=bool(command_id),
            representative_reason="sampled read against an active harmless background command" if command_id else "could not create helper background session",
            scenario_name="active_session_read",
        )

    if name == "send_background_input":
        command_id, setup_output = await _start_helper_session(
            by_name,
            "powershell -NoProfile -Command \"$line = [Console]::In.ReadLine(); Write-Output ('v8-bg-input:' + $line); Start-Sleep -Milliseconds 300\"",
        )
        args = {"command_id": command_id, "input_text": "hello from calibration\n"}
        output = await _invoke_agent_visible(tool_ref, args) if command_id else setup_output
        cleanup = await _cleanup_session(by_name, command_id)
        if cleanup:
            output = _scenario_sections([("input", output), ("cleanup", cleanup)])
        return _make_record(
            name=name,
            status="invoked",
            args=args,
            description=description,
            output=output,
            representative=bool(command_id),
            representative_reason="sampled input against an active stdin-reading background command" if command_id else "could not create helper background session",
            scenario_name="active_session_input",
        )

    command_id, setup_output = await _start_helper_session(
        by_name,
        "powershell -NoProfile -Command \"Write-Output v8-bg-terminate; Start-Sleep -Seconds 10\"",
    )
    args = {"command_id": command_id}
    output = await _invoke_agent_visible(tool_ref, args) if command_id else setup_output
    return _make_record(
        name=name,
        status="invoked",
        args=args,
        description=description,
        output=output,
        representative=bool(command_id),
        representative_reason="sampled terminate against an active harmless background command" if command_id else "could not create helper background session",
        scenario_name="active_session_terminate",
    )


async def _collect_tool_observation_detail_scenario(tool_ref: Any, description: str) -> ToolCalibrationRecord:
    raw_ref = record_raw_observation(
        tool_name="native_tool_calibration_probe",
        tool_call_id="calibration-tool-observation-detail-source",
        runtime_kind="native",
        surface="native_tool_calibration",
        raw_content=_json_dumps(
            {
                "ok": False,
                "status": "not_found",
                "summary": "Calibration observation rendered as readable detail.",
                "error": "calibration resource was not found",
                "recommendedNextAction": "Use the summary and original rawRef to decide the next step.",
                "details": {"resourceType": "calibration_fixture", "attempts": 1},
            }
        ),
        budget_meta={"agentVisibleBudget": DETAIL_VISIBLE_BUDGET},
        metadata={"calibration": True},
    )
    args = {"raw_ref": raw_ref, "max_chars": 2000}
    output = await _invoke_agent_visible(tool_ref, args)
    return _make_record(
        name="tool_observation_detail",
        status="invoked",
        args=args,
        description=description,
        output=output,
        representative=True,
        representative_reason="read a seeded bounded observation through ToolNode without creating a recursive detail rawRef",
        scenario_name="seeded_observation_detail",
    )


async def _collect_ledger_id_scenario(name: str, tool_ref: Any, by_name: dict[str, Any], description: str) -> ToolCalibrationRecord:
    scenario = LEDGER_ID_SCENARIOS[name]
    list_tool = by_name[str(scenario["listTool"])]
    raw_list_result = await _invoke_raw_tool(list_tool, dict(scenario.get("listArgs") or {}))
    if isinstance(raw_list_result, dict):
        payload = raw_list_result
    else:
        payload = _parse_json_object(_stringify_agent_visible_result(raw_list_result))
    items = list(payload.get(str(scenario["listKey"])) or [])
    if not items:
        output = (
            f"empty_ledger_unobserved: {name} was not invoked because "
            f"{scenario['listTool']} returned no {scenario['listKey']} entries.\n\n"
            "The calibration script does not fabricate missing ids, so this file is a truthful "
            "agent-visible note rather than a fake not-found output."
        )
        return _make_record(
            name=name,
            status="empty_ledger_unobserved",
            args=None,
            description=description,
            output=output,
            representative=False,
            representative_reason=f"{scenario['listTool']} returned an empty ledger",
            scenario_name="empty_ledger",
            skip_reason="empty_ledger_unobserved",
        )
    first = items[0] if isinstance(items[0], dict) else {}
    item_id = str(first.get(str(scenario["idKey"])) or "").strip()
    if not item_id:
        output = (
            f"empty_ledger_unobserved: {name} was not invoked because the first "
            f"{scenario['listKey']} entry had no {scenario['idKey']}."
        )
        return _make_record(
            name=name,
            status="empty_ledger_unobserved",
            args=None,
            description=description,
            output=output,
            representative=False,
            representative_reason=f"{scenario['listTool']} did not expose a usable id",
            scenario_name="missing_ledger_id",
            skip_reason="empty_ledger_unobserved",
        )
    args = {str(scenario["argName"]): item_id, **dict(scenario.get("extraArgs") or {})}
    output = await _invoke_agent_visible(tool_ref, args)
    return _make_record(
        name=name,
        status="invoked",
        args=args,
        description=description,
        output=output,
        representative=True,
        representative_reason=f"resolved a real {scenario['idKey']} from {scenario['listTool']}",
        scenario_name="real_ledger_id",
    )


async def _collect_records_async(*, invoke: bool = True) -> list[ToolCalibrationRecord]:
    all_tools = _all_export_tools()
    by_name = {str(getattr(tool_ref, "name", "")): tool_ref for tool_ref in all_tools}
    records: list[ToolCalibrationRecord] = []
    for name in native_tool_names_to_export():
        tool_ref = by_name[name]
        description = str(getattr(tool_ref, "description", "") or "").strip()
        visibility = _visibility_for_tool(name, all_tools)

        if name in UNSAFE_REASONS:
            reason = UNSAFE_REASONS[name]
            output = (
                f"unsafe_unobserved: {reason}.\n\n"
                "A representative output requires an isolated live harness with explicit side-effect controls."
            )
            records.append(
                _make_record(
                    name=name,
                    status="unsafe_unobserved",
                    args=None,
                    description=description,
                    output=output,
                    representative=False,
                    representative_reason=reason,
                    scenario_name="unsafe_unobserved",
                    skip_reason=reason,
                    surface_visibility=visibility,
                )
            )
            continue

        if name in STATEFUL_UNOBSERVED_REASONS:
            reason = STATEFUL_UNOBSERVED_REASONS[name]
            output = (
                f"stateful_unobserved: {reason}.\n\n"
                "The script avoids fabricating a missing id output for stateful runtime surfaces."
            )
            records.append(
                _make_record(
                    name=name,
                    status="stateful_unobserved",
                    args=None,
                    description=description,
                    output=output,
                    representative=False,
                    representative_reason=reason,
                    scenario_name="stateful_unobserved",
                    skip_reason=reason,
                    surface_visibility=visibility,
                )
            )
            continue

        if not invoke:
            args = BASE_SAFE_INVOCATIONS.get(name)
            output = "planned_agent_visible_calibration: invocation not executed because --no-invoke was used."
            records.append(
                _make_record(
                    name=name,
                    status="planned",
                    args=args,
                    description=description,
                    output=output,
                    representative=False,
                    representative_reason="--no-invoke avoids executing even safe samples",
                    scenario_name="planned",
                    surface_visibility=visibility,
                )
            )
            continue

        try:
            if name in SPECIAL_SCENARIOS:
                record = await _collect_command_scenario(name, tool_ref, by_name, description)
            elif name in DETAIL_SCENARIOS:
                record = await _collect_tool_observation_detail_scenario(tool_ref, description)
            elif name in LEDGER_ID_SCENARIOS:
                record = await _collect_ledger_id_scenario(name, tool_ref, by_name, description)
            else:
                args = BASE_SAFE_INVOCATIONS.get(name)
                if args is None:
                    raise RuntimeError(f"no safe agent-visible calibration scenario is registered for {name}")
                output = await _invoke_agent_visible(tool_ref, args)
                record = _make_record(
                    name=name,
                    status="invoked",
                    args=args,
                    description=description,
                    output=output,
                    representative=True,
                    representative_reason="safe read-only or bounded calibration scenario executed through ToolNode",
                    scenario_name="safe_observe",
                )
        except Exception as exc:  # noqa: BLE001 - diagnostic script must keep exporting the matrix
            output = "\n".join(
                [
                    f"calibration_error: {exc.__class__.__name__}: {exc}",
                    "",
                    traceback.format_exc(),
                ]
            )
            record = _make_record(
                name=name,
                status="error",
                args=BASE_SAFE_INVOCATIONS.get(name),
                description=description,
                output=output,
                representative=False,
                representative_reason="calibration invocation failed",
                scenario_name="error",
                surface_visibility=visibility,
            )
        if record.surface_visibility is None:
            record.surface_visibility = visibility
        records.append(record)
    return records


def collect_records(*, invoke: bool = True) -> list[ToolCalibrationRecord]:
    return asyncio.run(_collect_records_async(invoke=invoke))


def _markdown_code(value: str, *, language: str = "") -> str:
    safe = value.replace("```", "`\u200b``")
    return f"```{language}\n{safe}\n```"


def render_tool_markdown(record: ToolCalibrationRecord, *, generated_at: str) -> str:
    surface_visibility = record.surface_visibility or {}
    metadata = {
        "tool": record.name,
        "generatedAt": generated_at,
        "agentVisible": record.agent_visible,
        "status": record.status,
        "representative": record.representative,
        "representativeReason": record.representative_reason,
        "scenarioName": record.scenario_name,
        "skipReason": record.skip_reason,
        "args": record.args,
        "rawChars": record.raw_chars,
        "visibleChars": record.visible_chars,
        "truncatedByToolNode": record.truncated_by_tool_node,
        "maxToolOutputLength": MAX_TOOL_OUTPUT_LENGTH,
        "surfaceVisibility": surface_visibility,
        "runtimeKind": surface_visibility.get("runtimeKind"),
        "toolFamily": surface_visibility.get("toolFamily"),
        "rawRefPolicy": surface_visibility.get("rawRefPolicy"),
        "agentVisibleBudget": surface_visibility.get("agentVisibleBudget"),
        "clientSurface": record.client_surface,
        "diagnostics": record.diagnostics,
    }
    parts = [
        f"# {record.name}",
        "",
        "## Metadata",
        "",
        _markdown_code(_json_dumps(metadata), language="json"),
        "",
        "## Description",
        "",
        record.description or "(no description)",
        "",
        "## Client-Visible Surface",
        "",
        _markdown_code(_json_dumps(record.client_surface), language="json"),
        "",
        "## Agent-Visible Output",
        "",
        _markdown_code(record.output),
        "",
    ]
    return "\n".join(parts)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _excerpt(text: str, limit: int = 2400) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.62))
    tail = max(1, limit - head - 120)
    return (
        text[:head].rstrip()
        + f"\n\n...[{len(text) - head - tail} chars omitted in top-10 report; see per-tool file for full output]...\n\n"
        + text[-tail:].lstrip()
    )


def render_top_giant_outputs_markdown(records: list[ToolCalibrationRecord], *, generated_at: str) -> str:
    top_records = sorted(records, key=lambda record: int(record.visible_chars or 0), reverse=True)[:10]
    rows = [
        "| Rank | Tool | Status | Agent chars | Client summary | Dirty |",
        "|---:|---|---|---:|---|---|",
    ]
    for rank, record in enumerate(top_records, start=1):
        dirty = ", ".join(record.diagnostics.get("dirtySignals") or [])
        if record.diagnostics.get("jsonLikeVisible"):
            dirty = f"{dirty}, jsonLikeVisible".strip(", ")
        rows.append(
            "| {rank} | `{name}` | {status} | {chars} | {summary} | {dirty} |".format(
                rank=rank,
                name=record.name,
                status=record.status,
                chars=record.visible_chars,
                summary=_compact_text(record.client_surface.get("summary"), 120).replace("|", "\\|") or "(empty)",
                dirty=dirty.replace("|", "\\|") or "-",
            )
        )
    parts = [
        "# Top 10 Giant Tool Outputs",
        "",
        f"Generated at: `{generated_at}`",
        "",
        "This report ranks tools by agent-visible output size. Client-visible surface should stay short; runtime-only JSON remains in raw/detail surfaces.",
        "",
        *rows,
        "",
    ]
    for rank, record in enumerate(top_records, start=1):
        parts.extend(
            [
                f"## {rank}. {record.name}",
                "",
                f"- Status: `{record.status}`",
                f"- Agent-visible chars: `{record.visible_chars}`",
                f"- Raw chars estimate: `{record.raw_chars}`",
                f"- Representative: `{record.representative}` — {record.representative_reason}",
                "",
                "### Client-Visible Surface",
                "",
                _markdown_code(_json_dumps(record.client_surface), language="json"),
                "",
                "### Agent-Visible Output Excerpt",
                "",
                _markdown_code(_excerpt(record.output)),
                "",
            ]
        )
    return "\n".join(parts)


def _prepare_output_dir(output_dir: Path, *, allow_non_default_output: bool) -> Path:
    resolved = output_dir.resolve()
    default_resolved = DEFAULT_OUTPUT_DIR.resolve()
    if not allow_non_default_output and not _same_path(resolved, default_resolved):
        raise ValueError(f"Refusing to clear non-default output dir: {resolved}; expected {default_resolved}")
    if not allow_non_default_output and (resolved.name != "tools" or resolved.parent.name != "docs"):
        raise ValueError(f"Refusing to clear unexpected output dir shape: {resolved}")
    if resolved.exists():
        for child in resolved.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def export_records(
    records: list[ToolCalibrationRecord],
    output_dir: Path,
    *,
    allow_non_default_output: bool = False,
) -> dict[str, Any]:
    previous_visible_by_name: dict[str, int] = {}
    prior_index_path = output_dir.resolve() / "_index.json"
    if prior_index_path.exists():
        try:
            prior_index = json.loads(prior_index_path.read_text(encoding="utf-8"))
            for item in list(prior_index.get("tools") or []):
                if isinstance(item, dict) and item.get("name"):
                    previous_visible_by_name[str(item.get("name"))] = int(item.get("visibleChars") or 0)
        except Exception:
            previous_visible_by_name = {}
    resolved_output_dir = _prepare_output_dir(output_dir, allow_non_default_output=allow_non_default_output)
    generated_at = datetime.now(timezone.utc).isoformat()
    for record in records:
        (resolved_output_dir / f"{record.name}.md").write_text(
            render_tool_markdown(record, generated_at=generated_at),
            encoding="utf-8",
        )
    (resolved_output_dir / "_top10_giant_tool_outputs.md").write_text(
        render_top_giant_outputs_markdown(records, generated_at=generated_at),
        encoding="utf-8",
    )

    index: dict[str, Any] = {
        "generatedAt": generated_at,
        "outputDir": str(resolved_output_dir),
        "agentVisible": True,
        "maxToolOutputLength": MAX_TOOL_OUTPUT_LENGTH,
        "toolOutputTargets": TOOL_OUTPUT_TARGET_CHARS,
        "surfaces": [
            "supervisor/default",
            "supervisor/grants/computer_use.control",
            "supervisor/grants/creative_media.core",
            "supervisor/grants/research.core",
            "supervisor/grants/automation.ops",
            "subagent/contextual_default",
            "subagent/runtimeAccess/computer_use.control",
            "subagent/runtimeAccess/creative_media.core",
            "subagent/runtimeAccess/research.core",
        ],
        "toolCount": len(records),
        "excludedTools": sorted(EXCLUDED_TOOLS),
        "statusCounts": {},
        "dirtySignalCounts": {},
        "jsonLikeVisibleCount": 0,
        "topVisibleChars": [],
        "topGiantReport": "_top10_giant_tool_outputs.md",
        "surfaceTopVisibleChars": {},
        "topRegressions": [],
        "unrepresentativeTools": [],
        "tools": [],
    }
    for record in records:
        index["statusCounts"][record.status] = int(index["statusCounts"].get(record.status, 0)) + 1
        for signal in record.diagnostics.get("dirtySignals") or []:
            index["dirtySignalCounts"][signal] = int(index["dirtySignalCounts"].get(signal, 0)) + 1
        if bool(record.diagnostics.get("jsonLikeVisible")):
            index["jsonLikeVisibleCount"] = int(index.get("jsonLikeVisibleCount") or 0) + 1
        if not record.representative:
            index["unrepresentativeTools"].append(
                {
                    "name": record.name,
                    "status": record.status,
                    "reason": record.representative_reason,
                }
            )
        surface_visibility = record.surface_visibility or {}
        previous_visible_chars = previous_visible_by_name.get(record.name)
        reduction_percent = None
        if previous_visible_chars is not None and previous_visible_chars > 0:
            reduction_percent = round(((previous_visible_chars - record.visible_chars) / previous_visible_chars) * 100, 2)
        index["tools"].append(
            {
                "name": record.name,
                "file": f"{record.name}.md",
                "agentVisible": record.agent_visible,
                "status": record.status,
                "representative": record.representative,
                "representativeReason": record.representative_reason,
                "rawChars": record.raw_chars,
                "visibleChars": record.visible_chars,
                "clientSurface": record.client_surface,
                "clientSummary": record.client_surface.get("summary"),
                "clientStatus": record.client_surface.get("status"),
                "clientRefIds": record.client_surface.get("refIds") or [],
                "previousVisibleChars": previous_visible_chars,
                "reductionPercent": reduction_percent,
                "truncatedByToolNode": record.truncated_by_tool_node,
                "dirtySignals": record.diagnostics.get("dirtySignals") or [],
                "jsonLikeVisible": bool(record.diagnostics.get("jsonLikeVisible")),
                "scenarioName": record.scenario_name,
                "maxLineLength": record.diagnostics.get("maxLineLength"),
                "skipReason": record.skip_reason,
                "runtimeKind": surface_visibility.get("runtimeKind"),
                "toolFamily": surface_visibility.get("toolFamily"),
                "registeredGroups": surface_visibility.get("registeredGroups") or [],
                "grantRequired": bool(surface_visibility.get("grantRequired")),
                "surfaceVisibility": surface_visibility.get("surfaces") or {},
                "agentVisibleBudget": surface_visibility.get("agentVisibleBudget"),
                "rawRefPolicy": surface_visibility.get("rawRefPolicy"),
            }
        )
    index["topVisibleChars"] = sorted(
        (
            {"name": item["name"], "visibleChars": item["visibleChars"], "status": item["status"]}
            for item in index["tools"]
        ),
        key=lambda item: int(item["visibleChars"] or 0),
        reverse=True,
    )[:15]
    for surface in list(index.get("surfaces") or []):
        rows = []
        for item in index["tools"]:
            surface_payload = ((item.get("surfaceVisibility") or {}).get(surface) or {})
            if not isinstance(surface_payload, dict) or not bool(surface_payload.get("visible")):
                continue
            rows.append(
                {
                    "name": item["name"],
                    "visibleChars": item["visibleChars"],
                    "status": item["status"],
                    "representative": item["representative"],
                }
            )
        index["surfaceTopVisibleChars"][surface] = sorted(
            rows,
            key=lambda item: int(item["visibleChars"] or 0),
            reverse=True,
        )[:10]
    index["topRegressions"] = sorted(
        (
            {
                "name": item["name"],
                "previousVisibleChars": item.get("previousVisibleChars"),
                "visibleChars": item.get("visibleChars"),
                "increaseChars": int(item.get("visibleChars") or 0) - int(item.get("previousVisibleChars") or 0),
            }
            for item in index["tools"]
            if item.get("previousVisibleChars") is not None
            and int(item.get("visibleChars") or 0) > int(item.get("previousVisibleChars") or 0)
        ),
        key=lambda item: int(item["increaseChars"] or 0),
        reverse=True,
    )[:10]
    (resolved_output_dir / "_index.json").write_text(_json_dumps(index) + "\n", encoding="utf-8")
    return index


def _record_visible_surfaces(record: ToolCalibrationRecord) -> set[str]:
    surfaces = ((record.surface_visibility or {}).get("surfaces") or {})
    return {
        str(surface)
        for surface, payload in dict(surfaces).items()
        if isinstance(payload, dict) and bool(payload.get("visible"))
    }


def _record_budget(record: ToolCalibrationRecord) -> int:
    name = str(record.name or "")
    lowered = name.lower()
    visible_surfaces = _record_visible_surfaces(record)
    if name in COMMAND_TOOL_NAMES:
        return COMMAND_VISIBLE_BUDGET
    if name == "grep_search":
        return WORKSPACE_BROKER_VISIBLE_BUDGET
    if name == "workspace_broker":
        return WORKSPACE_BROKER_VISIBLE_BUDGET
    if any(part in lowered for part in ("get_", "lookup", "read_", "detail")):
        return DETAIL_VISIBLE_BUDGET
    if any(surface.startswith("supervisor/grants/") or surface.startswith("subagent/runtimeAccess/") for surface in visible_surfaces):
        if any(part in lowered for part in ("list", "catalog", "capabilities", "resolutions", "observe")):
            return GRANT_LIST_VISIBLE_BUDGET
    if "supervisor/default" in visible_surfaces:
        return SUPERVISOR_DEFAULT_BUDGET
    return DEFAULT_VISIBLE_BUDGET


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export true agent-visible outputs for native tools into per-tool Markdown files.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-invoke", action="store_true", help="Only render planned/unsafe files without invoking safe samples.")
    parser.add_argument("--allow-non-default-output", action="store_true", help="Allow writing to a non-default output directory for local validation.")
    parser.add_argument("--fail-on-dirty", action="store_true", help="Exit non-zero when invoked outputs contain dirty signals.")
    parser.add_argument("--fail-on-over-limit", action="store_true", help="Exit non-zero when any visible output exceeds ToolNode max output length.")
    return parser.parse_args()


def _dirty_invoked_records(records: list[ToolCalibrationRecord]) -> list[ToolCalibrationRecord]:
    return [
        record
        for record in records
        if record.status == "invoked"
        and (
            bool(record.diagnostics.get("dirtySignals"))
            or bool(record.diagnostics.get("jsonLikeVisible"))
        )
    ]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    missing = missing_invocation_names()
    if missing:
        print(f"Missing calibration coverage entries: {', '.join(missing)}", file=sys.stderr)
        return 2

    records = collect_records(invoke=not args.no_invoke)
    try:
        index = export_records(records, args.output_dir, allow_non_default_output=bool(args.allow_non_default_output))
    except Exception as exc:
        print(f"Failed to export calibration outputs: {exc}", file=sys.stderr)
        return 2
    print(_json_dumps(index))
    if args.fail_on_dirty:
        dirty = _dirty_invoked_records(records)
        if dirty:
            print("Dirty native tool outputs detected: " + ", ".join(record.name for record in dirty), file=sys.stderr)
            return 1
    if args.fail_on_over_limit:
        over_limit = [record for record in records if record.visible_chars > MAX_TOOL_OUTPUT_LENGTH]
        if over_limit:
            print("Agent-visible outputs over ToolNode limit: " + ", ".join(record.name for record in over_limit), file=sys.stderr)
            return 1
        budget_over = [
            (record, _record_budget(record))
            for record in records
            if record.status == "invoked"
            and bool(_record_visible_surfaces(record))
            and record.visible_chars > _record_budget(record)
        ]
        if budget_over:
            print(
                "Agent-visible outputs over surface budget: "
                + ", ".join(f"{record.name}={record.visible_chars}>{budget}" for record, budget in budget_over),
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

