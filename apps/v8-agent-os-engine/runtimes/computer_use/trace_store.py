from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.multimodal_payload_adapter import utc_now_iso
from core.storage import storage
from runtimes.computer_use.types import ComputerUseTraceStep


class ComputerUseTraceStore:
    _LOCAL_GOAL_PREFIXES = (
        "launch_app:",
        "launch_app_recover:",
        "open_app",
        "ensure_window",
        "observe_scene",
        "click_target",
        "input_text",
        "paste_text",
        "paste_files",
        "right_click_target",
        "hover_target",
        "send_hotkey",
        "scroll_view",
        "drag_pointer",
        "focus_window",
        "observe",
        "find_element",
        "click",
        "type_text",
        "hotkey",
        "scroll",
        "wait_for_element",
        "capture_screenshot",
        "find_and_type",
        "scroll_list",
        "click_toolbar_action",
        "execute_plan",
    )

    def __init__(self, root_dir: Path | None = None) -> None:
        base_dir = root_dir
        if base_dir is None:
            configured_base = getattr(storage, "base_dir", None)
            if configured_base:
                base_dir = Path(configured_base)
            else:
                base_dir = Path.home() / ".v8-agent-os"
        self.base_dir = Path(base_dir)
        self.trace_dir = self.base_dir / "computer_use_traces"
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def _trace_path(self, run_id: str) -> Path:
        normalized = str(run_id or "").strip() or "unknown"
        return self.trace_dir / f"{normalized}.json"

    def get_trace(self, run_id: str) -> Optional[Dict[str, Any]]:
        path = self._trace_path(run_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_traces(self, run_ids: List[str]) -> List[Dict[str, Any]]:
        traces: List[Dict[str, Any]] = []
        for run_id in run_ids:
            trace = self.get_trace(run_id)
            if trace:
                traces.append(trace)
        return traces

    def _trace_summary(
        self,
        trace: Dict[str, Any],
        *,
        include_steps: bool = False,
        max_steps: int = 6,
    ) -> Dict[str, Any]:
        steps = [item for item in list(trace.get("steps") or []) if isinstance(item, dict)]
        summary: Dict[str, Any] = {
            "runId": trace.get("runId"),
            "sessionId": trace.get("sessionId"),
            "runtimeKind": trace.get("runtimeKind"),
            "goal": trace.get("goal"),
            "stepCount": int(trace.get("stepCount") or len(steps)),
            "createdAt": trace.get("createdAt"),
            "updatedAt": trace.get("updatedAt"),
            "metadata": dict(trace.get("metadata") or {}),
        }
        if include_steps:
            preview = steps[: max(1, int(max_steps))]
            summary["steps"] = preview
            summary["truncated"] = len(steps) > len(preview)
        return summary

    def get_trace_bundle(
        self,
        run_ids: List[str],
        *,
        include_steps: bool = False,
        max_steps: int = 6,
    ) -> Dict[str, Any]:
        ordered_run_ids = [str(item or "").strip() for item in run_ids if str(item or "").strip()]
        traces = self.get_traces(ordered_run_ids)
        return {
            "runIds": ordered_run_ids,
            "found": [self._trace_summary(item, include_steps=include_steps, max_steps=max_steps) for item in traces],
            "missing": [run_id for run_id in ordered_run_ids if all(str(trace.get("runId")) != run_id for trace in traces)],
        }

    def list_traces(
        self,
        *,
        limit: int = 100,
        session_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        traces: List[Dict[str, Any]] = []
        for path in sorted(self.trace_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                trace = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if session_id and str(trace.get("sessionId") or "").strip() != str(session_id).strip():
                continue
            traces.append(self._trace_summary(trace))
            if len(traces) >= max(1, int(limit)):
                break
        return traces

    def _looks_like_local_goal(self, value: Any) -> bool:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return True
        if normalized.startswith("plan_step_"):
            return True
        return any(normalized.startswith(prefix) for prefix in self._LOCAL_GOAL_PREFIXES)

    def append_step(
        self,
        *,
        run_id: str,
        session_id: str,
        goal: str | None,
        runtime_kind: str,
        step: ComputerUseTraceStep,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path = self._trace_path(run_id)
        incoming_metadata = dict(metadata or {})
        root_goal = str(
            incoming_metadata.get("rootGoal")
            or incoming_metadata.get("root_goal")
            or incoming_metadata.get("requestedRootGoal")
            or ""
        ).strip()
        normalized_goal = str(goal or "").strip()
        effective_goal = root_goal or normalized_goal
        existing = self.get_trace(run_id) or {
            "version": 1,
            "runId": run_id,
            "sessionId": session_id,
            "runtimeKind": runtime_kind,
            "goal": effective_goal,
            "createdAt": utc_now_iso(),
            "updatedAt": utc_now_iso(),
            "metadata": {},
            "steps": [],
        }
        existing["sessionId"] = session_id
        existing["runtimeKind"] = runtime_kind
        if root_goal:
            existing["goal"] = root_goal
        elif normalized_goal and not self._looks_like_local_goal(normalized_goal):
            existing["goal"] = normalized_goal
        merged_metadata = dict(existing.get("metadata") or {})
        merged_metadata.update(incoming_metadata)
        merged_metadata["traceSchemaVersion"] = 2
        source_goals = [
            str(item).strip()
            for item in list(merged_metadata.get("sourceGoals") or [])
            if str(item).strip()
        ]
        for candidate in (root_goal, normalized_goal):
            if candidate and candidate not in source_goals:
                source_goals.append(candidate)
        if source_goals:
            merged_metadata["sourceGoals"] = source_goals[-12:]
        existing["metadata"] = merged_metadata

        step_payload = step.as_dict()
        current_steps = list(existing.get("steps") or [])
        step_payload["index"] = len(current_steps) + 1
        current_steps.append(step_payload)
        existing["steps"] = current_steps
        existing["stepCount"] = len(current_steps)
        existing["updatedAt"] = utc_now_iso()
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return existing


trace_store = ComputerUseTraceStore()
