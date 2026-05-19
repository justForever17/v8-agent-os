from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.multimodal_payload_adapter import utc_now_iso
from core.v8_agent_os_paths import runtime_private_root
from runtimes.computer_use.trace_store import ComputerUseTraceStore, trace_store
from runtimes.computer_use.types import (
    ComputerUseTracePrimitive,
    ComputerUseTraceRecovery,
    ComputerUseTraceRisk,
    ComputerUseTraceScene,
    ComputerUseTraceStep,
    ComputerUseTraceTarget,
    ComputerUseTraceTiming,
    ComputerUseTraceVariable,
)


RECORDING_SCHEMA_VERSION = 1


def _safe_string(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _slug(value: Any, fallback: str = "rpa") -> str:
    text = _safe_string(value, fallback).lower()
    cleaned = "".join(ch if ch.isalnum() or ch in "._:-" else "-" for ch in text).strip("-")
    return cleaned or fallback


class RPARecordingManager:
    """Human recording adapter that writes Computer Use trace-compatible steps."""

    def __init__(
        self,
        *,
        trace_store_instance: ComputerUseTraceStore = trace_store,
        root_dir: Path | None = None,
    ) -> None:
        self.trace_store = trace_store_instance
        self.base_dir = Path(root_dir) if root_dir is not None else runtime_private_root("rpa") / "recordings"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, recording_id: str) -> Path:
        normalized = _slug(recording_id, "recording")
        return self.base_dir / f"{normalized}.json"

    def _write_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session["updatedAt"] = utc_now_iso()
        self._session_path(str(session["recordingSessionId"])).write_text(
            json.dumps(session, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return session

    def get(self, recording_id: str) -> Optional[Dict[str, Any]]:
        path = self._session_path(recording_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        sessions: List[Dict[str, Any]] = []
        for path in sorted(self.base_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            sessions.append(self._public_session(payload))
            if len(sessions) >= max(1, int(limit)):
                break
        return sessions

    def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        recording_id = f"rpa_rec_{uuid.uuid4().hex[:12]}"
        trace_run_id = f"rpa_recording_{uuid.uuid4().hex[:12]}"
        session_id = _safe_string(payload.get("sessionId"), f"rpa:recording:{recording_id}")
        target_mode = _safe_string(payload.get("targetMode"), "agent_browser")
        now = utc_now_iso()
        session = {
            "schemaVersion": RECORDING_SCHEMA_VERSION,
            "recordingSessionId": recording_id,
            "traceRunId": trace_run_id,
            "sessionId": session_id,
            "state": "recording",
            "targetMode": target_mode,
            "name": _safe_string(payload.get("name"), "Recorded RPA flow"),
            "goal": _safe_string(payload.get("goal"), _safe_string(payload.get("name"), "Recorded RPA flow")),
            "appId": _safe_string(payload.get("appId"), "desktop"),
            "browserKind": _safe_string(payload.get("browserKind")),
            "browserProfileId": _safe_string(payload.get("browserProfileId")),
            "windowHandle": payload.get("windowHandle"),
            "activeApp": _coerce_dict(payload.get("activeApp")),
            "captureOptions": _coerce_dict(payload.get("captureOptions")),
            "createdAt": now,
            "updatedAt": now,
            "startedBy": _safe_string(payload.get("userId"), "admin_ui"),
            "rawEvents": [],
            "stepIds": [],
            "createdDraftId": None,
            "compileError": None,
        }
        return self._public_session(self._write_session(session))

    def pause(self, recording_id: str) -> Dict[str, Any]:
        session = self._require_session(recording_id)
        if session.get("state") == "recording":
            session["state"] = "paused"
        return self._public_session(self._write_session(session))

    def resume(self, recording_id: str) -> Dict[str, Any]:
        session = self._require_session(recording_id)
        if session.get("state") in {"paused", "idle"}:
            session["state"] = "recording"
        return self._public_session(self._write_session(session))

    def cancel(self, recording_id: str) -> Dict[str, Any]:
        session = self._require_session(recording_id)
        session["state"] = "cancelled"
        return self._public_session(self._write_session(session))

    def append_event(self, recording_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        session = self._require_session(recording_id)
        if session.get("state") != "recording":
            raise ValueError("Recording session is not active.")
        raw_event = dict(event or {})
        event_id = _safe_string(raw_event.get("eventId"), f"evt_{uuid.uuid4().hex[:10]}")
        raw_event["eventId"] = event_id
        raw_event["recordedAt"] = raw_event.get("recordedAt") or utc_now_iso()
        raw_events = _coerce_list(session.get("rawEvents"))
        raw_events.append(raw_event)
        session["rawEvents"] = raw_events[-500:]

        step = self._step_from_event(session, raw_event, len(_coerce_list(session.get("stepIds"))) + 1)
        trace_payload = self.trace_store.append_step(
            run_id=str(session["traceRunId"]),
            session_id=str(session["sessionId"]),
            goal=str(session.get("goal") or session.get("name") or "Recorded RPA flow"),
            runtime_kind="computer_use",
            step=step,
            metadata={
                "traceSchemaVersion": 2,
                "recordedBy": "human",
                "recordingSessionId": session["recordingSessionId"],
                "recordingTargetMode": session.get("targetMode"),
                "rootGoal": session.get("goal") or session.get("name"),
                "source": "admin_rpa_recorder",
            },
        )
        step_ids = _coerce_list(session.get("stepIds"))
        step_ids.append(step.step_id)
        session["stepIds"] = step_ids
        session["stepCount"] = len(step_ids)
        session["lastTraceStepId"] = step.step_id
        self._write_session(session)
        return {
            "recording": self._public_session(session),
            "step": step.as_dict(),
            "trace": {
                "runId": trace_payload.get("runId"),
                "stepCount": trace_payload.get("stepCount"),
                "updatedAt": trace_payload.get("updatedAt"),
            },
        }

    def stop(self, recording_id: str) -> Dict[str, Any]:
        session = self._require_session(recording_id)
        if session.get("state") not in {"draft_ready", "failed"}:
            session["state"] = "stopped"
        return self._public_session(self._write_session(session))

    def mark_compiling(self, recording_id: str) -> Dict[str, Any]:
        session = self._require_session(recording_id)
        session["state"] = "compiling"
        return self._write_session(session)

    def mark_draft_ready(self, recording_id: str, draft: Dict[str, Any]) -> Dict[str, Any]:
        session = self._require_session(recording_id)
        session["state"] = "draft_ready"
        session["createdDraftId"] = draft.get("id")
        session["compileError"] = None
        return self._public_session(self._write_session(session))

    def mark_failed(self, recording_id: str, error: Any) -> Dict[str, Any]:
        session = self._require_session(recording_id)
        session["state"] = "failed"
        session["compileError"] = str(error)
        return self._public_session(self._write_session(session))

    def _require_session(self, recording_id: str) -> Dict[str, Any]:
        session = self.get(recording_id)
        if not session:
            raise ValueError(f"Recording session '{recording_id}' not found.")
        return session

    def _public_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(session)
        payload.pop("rawEvents", None)
        return payload

    def _step_from_event(self, session: Dict[str, Any], event: Dict[str, Any], index: int) -> ComputerUseTraceStep:
        action = _safe_string(event.get("action"), _safe_string(event.get("type"), "custom"))
        step_id = _safe_string(event.get("stepId"), f"recorded_{index:03d}_{uuid.uuid4().hex[:6]}")
        params = _coerce_dict(event.get("params"))
        raw_params = dict(params)
        target_payload = _coerce_dict(event.get("target"))
        coordinate = _coerce_dict(event.get("coordinate"))
        selector_candidates = _coerce_list(event.get("selectorCandidates"))
        sensitive = bool(event.get("sensitiveInput") or params.get("sensitiveInput"))
        variable_name = _safe_string(event.get("variableName") or params.get("variableName"))

        if action in {"type", "input"}:
            action = "type_text"
        if action == "assert":
            action = "assert_condition"
        if action == "wait":
            action = "wait_for_element"

        if sensitive:
            secret_name = variable_name or _safe_string(params.get("name"), "secret_value")
            params["text"] = f"${{secret:{secret_name}}}"
            raw_params["text"] = "[redacted]"
        variables: List[ComputerUseTraceVariable] = []
        if variable_name:
            variables.append(
                ComputerUseTraceVariable(
                    name=variable_name,
                    placeholder=f"{{{{{variable_name}}}}}",
                    original_key=_safe_string(params.get("originalKey"), "text"),
                    required=True,
                    source="human_recording",
                    example_value=None if sensitive else params.get("text"),
                )
            )

        selector = _coerce_dict(target_payload.get("selector"))
        if selector_candidates and not selector:
            first_selector = selector_candidates[0]
            if isinstance(first_selector, dict):
                selector = dict(first_selector)
        window = _coerce_dict(target_payload.get("window")) or {
            "title": event.get("windowTitle") or session.get("activeApp", {}).get("windowTitle"),
            "handle": event.get("windowHandle") or session.get("windowHandle"),
            "appId": session.get("appId"),
        }
        spatial_anchor = _coerce_dict(target_payload.get("spatialAnchor"))
        if coordinate:
            spatial_anchor = {
                **spatial_anchor,
                "x": coordinate.get("x"),
                "y": coordinate.get("y"),
                "viewport": _coerce_dict(event.get("viewport")),
                "screen": _coerce_dict(event.get("screen")),
                "monitorId": event.get("monitorId"),
                "fallback": True,
            }

        metadata = _coerce_dict(event.get("metadata"))
        metadata.update(
            {
                "recordedBy": "human",
                "recordingSessionId": session.get("recordingSessionId"),
                "sourceEventIds": [event.get("eventId")],
                "selectorCandidates": selector_candidates[:5],
                "coordinateFallback": bool(coordinate and not selector),
                "sensitiveInput": sensitive,
                "targetMode": session.get("targetMode"),
                "browserKind": session.get("browserKind"),
                "browserProfileId": session.get("browserProfileId"),
                "viewport": _coerce_dict(event.get("viewport")),
                "screen": _coerce_dict(event.get("screen")),
                "computerObservation": _coerce_dict(event.get("computerObservation") or metadata.get("computerObservation")),
            }
        )

        verification = _coerce_dict(event.get("verification"))
        if action == "assert_condition":
            verification["assertion"] = params.get("assertion") or params.get("text") or "human_recorded_assertion"
        if action == "wait_for_element":
            verification["wait"] = {
                "condition": params.get("condition") or "element_or_window_ready",
                "timeoutMs": params.get("timeoutMs") or 10000,
            }

        fragile = bool(metadata.get("coordinateFallback"))
        return ComputerUseTraceStep(
            step_id=step_id,
            app_id=_safe_string(session.get("appId"), "desktop"),
            action=action,
            intent=_safe_string(event.get("intent"), action),
            phase="verification" if action == "assert_condition" else "action",
            target=ComputerUseTraceTarget(window=window, selector=selector, spatial_anchor=spatial_anchor),
            params=params,
            raw_params=raw_params,
            variables=variables,
            verification=verification,
            recovery=ComputerUseTraceRecovery(
                transient=fragile,
                fallback_order=["selector", "window_anchor", "coordinate"] if fragile else [],
                strategy="prefer_selector_then_coordinate" if fragile else "",
                details={"fragileCoordinateFallback": fragile},
            ),
            risk=ComputerUseTraceRisk(
                level="review" if sensitive or fragile else "low",
                high_risk_action=sensitive,
                requires_pre_guard=sensitive,
                details={"sensitiveInput": sensitive, "fragileCoordinateFallback": fragile},
            ),
            artifacts=_coerce_list(event.get("artifacts")),
            timing=ComputerUseTraceTiming(
                wait_timeout_ms=int(params.get("timeoutMs") or (10000 if action == "wait_for_element" else 6000)),
                retry_limit=1,
                attempt_count=1,
            ),
            primitive=ComputerUseTracePrimitive(
                primitive_id=f"human_recording.{action}",
                category="human_recording",
                action=action,
                affordances=["recorded", "rpa_template_candidate"],
                requires_page_identity=False,
                requires_verification_contract=action in {"click", "type_text", "wait_for_element", "assert_condition"},
                requires_recovery_policy=fragile,
                supports_rpa_promotion=True,
                notes=["Recorded from Admin RPA recorder."],
            ),
            scene=ComputerUseTraceScene(
                page_identity=_safe_string(event.get("pageIdentity") or event.get("url") or session.get("appId"), "recorded_screen"),
                blocker_state="none",
                transition_state="recorded",
                affordances=["human_recorded"],
                confidence="medium" if selector else "low",
                reasons=["selector captured"] if selector else ["coordinate fallback"],
            ),
            signals={
                "humanRecorded": True,
                "fragileCoordinateFallback": fragile,
                "selectorCandidateCount": len(selector_candidates),
            },
            metadata=metadata,
        )
