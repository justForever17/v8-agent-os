from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional

from core.database import db
from core.json_safe import to_jsonable


_CORRECTION_RE = re.compile(
    r"(不对|错了|不是这个|你误解|应该|别|不要|wrong|not that|instead|should have)",
    re.IGNORECASE,
)


def _compact_text(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _payload_summary(payload: Dict[str, Any]) -> str:
    for key in ("summary", "label", "message", "reason", "toolName", "tool_name", "action", "status", "error"):
        value = payload.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return _compact_text(value)
    return ""


def _status_from_event(topic: str, payload: Dict[str, Any]) -> str:
    raw = str(payload.get("status") or payload.get("state") or payload.get("verdict") or "").strip().lower()
    if any(token in topic.lower() for token in ("failed", "blocked", "error", "rejected")):
        return "failed"
    if raw in {"failed", "error", "blocked", "rejected", "cancelled"}:
        return "failed"
    if any(token in topic.lower() for token in ("completed", "finished", "recorded", "resolved", "approved")):
        return "completed"
    if raw in {"completed", "success", "succeeded", "ok", "passed", "approved"}:
        return "completed"
    return raw or "observed"


def _tool_name(topic: str, payload: Dict[str, Any]) -> str:
    for key in ("toolName", "tool_name", "name", "tool", "command", "action"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if topic.startswith("tool."):
        return "tool"
    return topic.split(".", 1)[0]


def _event_is_workflow_relevant(topic: str, payload: Dict[str, Any]) -> bool:
    if topic.startswith("memory."):
        return False
    prefixes = (
        "tool.",
        "delegation.",
        "subagent.",
        "extension.",
        "computer_use.",
        "rpa.",
        "safety.",
        "artifact.",
        "approval.",
        "run.completed",
        "run.failed",
    )
    return topic.startswith(prefixes) or bool(payload.get("toolName") or payload.get("tool_name"))


def _risk_scope_from_tools(tools: Iterable[str], topics: Iterable[str]) -> str:
    text = " ".join([*map(str, tools), *map(str, topics)]).lower()
    if any(token in text for token in ("computer_use", "desktop", "rpa", "click", "paste")):
        return "desktop_or_rpa"
    if any(token in text for token in ("command_session", "run_system_command", "powershell", "shell", "install")):
        return "external_process"
    if any(token in text for token in ("write", "s3_", "upload", "download", "artifact.recorded", "file")):
        return "writes_files"
    if any(token in text for token in ("http", "web_", "network", "fetch", "mcp", "extension.")):
        return "external_network"
    if any(token in text for token in ("read", "search", "observe", "recall")):
        return "read_only"
    return "low_side_effect"


class WorkflowEvidenceCollector:
    """Build compact procedural evidence from V8 runtime facts before LLM distillation."""

    def collect_session(
        self,
        *,
        session_id: str,
        run_id: Optional[str] = None,
        max_events: int = 240,
    ) -> List[Dict[str, Any]]:
        events = (
            db.get_runtime_events_for_run(run_id, session_id=session_id, limit=max_events)
            if run_id
            else db.get_runtime_events(session_id)[-max_events:]
        )
        relevant: List[Dict[str, Any]] = []
        successful_actions: List[str] = []
        failure_markers: List[str] = []
        verification_steps: List[str] = []
        tools: List[str] = []
        topics: List[str] = []
        runtime_lanes: List[str] = []

        for event in events:
            topic = str(event.get("topic") or "").strip()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if not topic or not _event_is_workflow_relevant(topic, payload):
                continue
            status = _status_from_event(topic, payload)
            tool = _tool_name(topic, payload)
            summary = _payload_summary(payload) or tool
            runtime_id = str(payload.get("runtimeId") or payload.get("runtime") or "").strip()
            if runtime_id:
                runtime_lanes.append(runtime_id)
            if tool:
                tools.append(tool)
            topics.append(topic)
            compact = {
                "seq": event.get("seq"),
                "topic": topic,
                "status": status,
                "tool": tool,
                "summary": summary,
                "runtimeId": runtime_id,
            }
            relevant.append(compact)
            if status == "failed":
                failure_markers.append(f"{topic}: {summary}")
                continue
            if status == "completed":
                if topic.startswith(("tool.", "delegation.", "extension.", "computer_use.", "rpa.", "artifact.")):
                    successful_actions.append(f"{tool}: {summary}")
                if topic.startswith(("artifact.", "run.completed", "delegation.")) or "verification" in topic.lower():
                    verification_steps.append(f"{topic}: {summary}")

        if not relevant:
            return []

        messages = db.get_messages(session_id)
        user_messages = [str(item.get("content") or "").strip() for item in messages if item.get("role") == "user" and str(item.get("content") or "").strip()]
        user_corrections = [msg[:220] for msg in user_messages if _CORRECTION_RE.search(msg)]
        initial_intent = user_messages[0][:260] if user_messages else ""
        has_success = bool(successful_actions or verification_steps or any(item.get("topic") == "run.completed" for item in relevant))
        if not has_success and not failure_markers:
            return []

        task_family = _compact_text(initial_intent or (successful_actions[0] if successful_actions else "runtime workflow"), 120)
        signature_base = json.dumps(
            {
                "intent": task_family,
                "tools": tools[:8],
                "topics": topics[:8],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha1(signature_base.encode("utf-8")).hexdigest()[:12]
        side_effect_scope = _risk_scope_from_tools(tools, topics)
        episode_id = f"mw_ev_{session_id[:12]}_{digest}"
        return [
            {
                "id": episode_id,
                "taskFamily": task_family or "runtime workflow",
                "taskFamilySignature": f"wf:{digest}",
                "initialUserIntent": initial_intent,
                "canonicalTriggerPatterns": [initial_intent, task_family],
                "firstActionSignature": successful_actions[0] if successful_actions else (failure_markers[0] if failure_markers else ""),
                "runtimeLane": ",".join(sorted(set(runtime_lanes or ["chat"]))[:4]),
                "orderedActions": successful_actions[:10],
                "toolSkillSequence": list(dict.fromkeys(tools))[:16],
                "failureMarkers": failure_markers[:8],
                "userCorrectionPoints": user_corrections[:6],
                "finalSuccessEvidence": verification_steps[0] if verification_steps else ("runtime completed with successful actions" if has_success else ""),
                "userVerdict": "user_correction_present" if user_corrections else "",
                "sideEffectScope": side_effect_scope,
                "privacyScope": "local_runtime",
                "goldenPathSteps": successful_actions[:8],
                "antiPatterns": failure_markers[:6],
                "verificationSteps": verification_steps[:6],
                "confidence": 0.78 if has_success and not failure_markers else 0.68 if has_success else 0.35,
                "evidenceSource": "runtime_events",
                "runtimeEvidence": relevant[:80],
                "evidenceSummary": {
                    "eventCount": len(relevant),
                    "successfulActionCount": len(successful_actions),
                    "failureCount": len(failure_markers),
                    "userCorrectionCount": len(user_corrections),
                },
            }
        ]


workflow_evidence_collector = WorkflowEvidenceCollector()
