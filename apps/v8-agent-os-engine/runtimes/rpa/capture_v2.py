from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.multimodal_payload_adapter import utc_now_iso
from core.v8_agent_os_paths import runtime_private_root
from runtimes.rpa.recording import CaptureVerificationRequired, _coerce_dict, _coerce_list, _safe_string


class CaptureBroker:
    """V2 capture orchestration: sessions, target ownership, and sidecar events."""

    def __init__(
        self,
        recording_manager,
        *,
        verifier: "ReplayVerifier | None" = None,
        request_root: Path | None = None,
    ) -> None:
        self.recording_manager = recording_manager
        self.verifier = verifier or ReplayVerifier(recording_manager)
        self.request_root = Path(request_root) if request_root else runtime_private_root("rpa") / "inspector_sessions"
        self.request_root.mkdir(parents=True, exist_ok=True)

    def start_session(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        recording = self.recording_manager.require_session(recording_id)
        target_lock = _coerce_dict(recording.get("targetLock"))
        target_lock.update(_coerce_dict(payload.get("targetLock")))
        platform = self._resolve_platform(recording=recording, payload=payload, target_lock=target_lock)
        session_id = _safe_string(payload.get("sessionId"), f"inspector_{uuid.uuid4().hex[:12]}")
        one_time_token = hashlib.sha256(f"{recording_id}:{session_id}:{uuid.uuid4().hex}".encode("utf-8")).hexdigest()
        now = utc_now_iso()
        session = {
            "schemaVersion": 1,
            "kind": "rpa_inspector_session",
            "sessionId": session_id,
            "recordingId": recording_id,
            "platform": platform,
            "state": "starting",
            "status": "starting",
            "createdAt": now,
            "updatedAt": now,
            "stepId": payload.get("stepId") or recording.get("stepId"),
            "selectedStepKey": payload.get("selectedStepKey") or recording.get("selectedStepKey"),
            "workflowSnapshot": payload.get("workflowSnapshot") or recording.get("workflowSnapshot") or {},
            "targetLock": target_lock,
            "sidecar": self._sidecar_descriptor(platform, payload),
            "oneTimeToken": one_time_token,
            "candidateCount": 0,
            "requestPath": None,
        }
        request_path = self._write_request_file(session)
        session["requestPath"] = str(request_path)
        if platform == "browser" and not self._browser_attach_available(payload):
            session.update(
                {
                    "state": "unavailable",
                    "status": "browser_inspector_unavailable",
                    "reason": "Browser inspector requires an attachable Agent Browser context; no attach handle was provided.",
                    "fallback": "browser_capture_legacy",
                }
            )
            recording = self.recording_manager.upsert_inspector_session(recording_id, session)
            return {"ok": False, "status": session["status"], "reason": session["reason"], "session": self._public_session(session), "recording": recording}
        if platform == "windows" and not payload.get("sidecarReady") and not _coerce_list(payload.get("mockCandidates")):
            session.update(
                {
                    "state": "unavailable",
                    "status": "windows_inspector_sidecar_unavailable",
                    "reason": "FlaUI inspector sidecar protocol is ready, but no product sidecar process was reported as available.",
                    "fallback": "native_inspector_legacy",
                }
            )
            recording = self.recording_manager.upsert_inspector_session(recording_id, session)
            return {"ok": False, "status": session["status"], "reason": session["reason"], "session": self._public_session(session), "recording": recording}
        session.update({"state": "waiting_sidecar", "status": "waiting_sidecar"})
        recording = self.recording_manager.upsert_inspector_session(recording_id, session)
        for candidate in _coerce_list(payload.get("mockCandidates")):
            if isinstance(candidate, dict):
                self.ingest_event(recording_id, session_id, {"type": "candidate", "candidate": candidate, "oneTimeToken": one_time_token})
        latest = self.recording_manager.get_inspector_session(recording_id, session_id) or session
        return {"ok": True, "status": latest.get("status") or "waiting_sidecar", "session": self._public_session(latest), "recording": self.recording_manager.get(recording_id)}

    def get_session(self, recording_id: str, session_id: str) -> Dict[str, Any]:
        session = self.recording_manager.get_inspector_session(recording_id, session_id)
        if not session:
            raise ValueError(f"Inspector session '{session_id}' not found.")
        return {"ok": True, "status": session.get("status") or session.get("state"), "session": self._public_session(session)}

    def ingest_event(self, recording_id: str, session_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        session = self.recording_manager.get_inspector_session(recording_id, session_id)
        if not session:
            raise ValueError(f"Inspector session '{session_id}' not found.")
        token = _safe_string(event.get("oneTimeToken") or event.get("token"))
        if token and token != session.get("oneTimeToken"):
            raise PermissionError("Inspector event token mismatch.")
        event_type = _safe_string(event.get("type") or event.get("event"), "candidate")
        if event_type in {"candidate", "element", "element_captured", "actionAdded"}:
            candidate = _coerce_dict(event.get("candidate") or event.get("element") or event.get("data") or event)
            pool_item = self._capture_pool_item_from_candidate(recording_id=recording_id, session=session, candidate=candidate)
            recording = self.recording_manager.add_capture_pool_item(recording_id, pool_item)
            session["candidateCount"] = int(session.get("candidateCount") or 0) + 1
            session["state"] = "candidate_received"
            session["status"] = "candidate_received"
            session["updatedAt"] = utc_now_iso()
            recording = self.recording_manager.upsert_inspector_session(recording_id, session)
            return {"ok": True, "status": "candidate_received", "capturePoolItem": pool_item, "recording": recording}
        if event_type in {"heartbeat", "ready", "error", "closed"}:
            session["state"] = event_type
            session["status"] = event_type
            session["lastEvent"] = {k: v for k, v in dict(event).items() if k != "oneTimeToken"}
            session["updatedAt"] = utc_now_iso()
            recording = self.recording_manager.upsert_inspector_session(recording_id, session)
            return {"ok": event_type != "error", "status": event_type, "session": self._public_session(session), "recording": recording}
        raise ValueError(f"Unsupported inspector event type: {event_type}")

    def _capture_pool_item_from_candidate(self, *, recording_id: str, session: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        locator_bundle = normalize_locator_bundle(candidate, platform=_safe_string(session.get("platform"), "windows"))
        anchor_bundle = normalize_anchor_bundle(candidate)
        proof = _coerce_dict(candidate.get("proof"))
        if not proof:
            proof = {"status": "unverified", "source": "inspector_sidecar"}
        temp_key = {
            "recordingId": recording_id,
            "sessionId": session.get("sessionId"),
            "candidate": locator_bundle.get("primaryLocator") or candidate,
        }
        temp_element_id = _safe_string(candidate.get("tempElementId"), f"temp_el_{hashlib.sha1(json.dumps(temp_key, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:12]}")
        primary_locator = _coerce_dict(locator_bundle.get("primaryLocator"))
        return {
            "tempElementId": temp_element_id,
            "label": _safe_string(candidate.get("label") or primary_locator.get("name") or primary_locator.get("automationId") or primary_locator.get("selector") or candidate.get("name"), temp_element_id)[:160],
            "source": "rpa_inspector_v2",
            "inspectorSessionId": session.get("sessionId"),
            "platform": session.get("platform"),
            "sourceStepId": session.get("stepId"),
            "stepId": session.get("stepId"),
            "targetStepId": session.get("stepId"),
            "targetWindow": _coerce_dict(anchor_bundle.get("window") or candidate.get("targetWindow")),
            "locatorBundle": locator_bundle,
            "anchorBundle": anchor_bundle,
            "proof": proof,
            "selector": legacy_selector_from_locator_bundle(locator_bundle),
            "selectorCandidates": _coerce_list(candidate.get("selectorCandidates")),
            "coordinate": _coerce_dict(candidate.get("coordinate") or candidate.get("windowRelativeCoordinate")),
            "fragileCoordinateFallback": not bool(primary_locator),
            "captureMode": "inspector_v2",
            "capturedAt": utc_now_iso(),
        }

    def _write_request_file(self, session: Dict[str, Any]) -> Path:
        request_path = self.request_root / f"{session['sessionId']}.request.json"
        request_payload = {
            "protocol": "v8-rpa-inspector-v2",
            "sessionId": session.get("sessionId"),
            "recordingId": session.get("recordingId"),
            "platform": session.get("platform"),
            "targetLock": session.get("targetLock"),
            "oneTimeToken": session.get("oneTimeToken"),
            "callback": {
                "method": "POST",
                "path": f"/rpa/recordings/{session.get('recordingId')}/inspector/sessions/{session.get('sessionId')}/events",
            },
        }
        request_path.write_text(json.dumps(request_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return request_path

    @staticmethod
    def _resolve_platform(*, recording: Dict[str, Any], payload: Dict[str, Any], target_lock: Dict[str, Any]) -> str:
        requested = _safe_string(payload.get("platform") or payload.get("adapter") or payload.get("kind")).lower()
        mode = _safe_string(target_lock.get("mode") or recording.get("targetMode")).lower()
        app_id = _safe_string(target_lock.get("appId") or recording.get("appId")).lower()
        if requested in {"browser", "windows", "macos", "linux"}:
            return requested
        if mode == "agent_browser" or app_id in {"browser", "chrome", "edge"}:
            return "browser"
        if mode in {"desktop_window", "desktop", "windows"}:
            return "windows"
        return "windows"

    @staticmethod
    def _sidecar_descriptor(platform: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if platform == "browser":
            return {"kind": "playwright_node_sidecar", "state": "attach_required", "version": "v2"}
        if platform == "windows":
            return {"kind": "flaui_inspector_sidecar", "state": "request_file_ready", "version": "v2"}
        return {"kind": f"{platform}_inspector_sidecar", "state": "reserved", "version": "v2"}

    @staticmethod
    def _browser_attach_available(payload: Dict[str, Any]) -> bool:
        return bool(payload.get("browserAttach") or payload.get("targetId") or payload.get("cdpEndpoint") or payload.get("wsEndpoint"))

    @staticmethod
    def _public_session(session: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(session)
        payload.pop("oneTimeToken", None)
        return payload


class ReplayVerifier:
    """V2 proof gate before capture-pool items may enter the object library."""

    def __init__(self, recording_manager, *, resolver: Optional[Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]] = None) -> None:
        self.recording_manager = recording_manager
        self.resolver = resolver

    def verify(self, recording_id: str, temp_element_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = self.recording_manager.get_capture_pool_item(recording_id, temp_element_id)
        if not item:
            raise ValueError(f"Capture pool item '{temp_element_id}' not found.")
        locator_bundle = _coerce_dict(item.get("locatorBundle"))
        primary_locator = _coerce_dict(locator_bundle.get("primaryLocator"))
        warnings: list[str] = []
        if not primary_locator:
            proof = self._proof("locator_missing", 0, payload, warnings + ["capture item has no primary locator"])
            updated = self.recording_manager.update_capture_pool_item(recording_id, temp_element_id, {"proof": proof})
            return {"ok": False, "status": proof["status"], "proof": proof, "recording": updated}
        find_payload = self._resolve_find_count(item, payload)
        warnings.extend(find_payload.get("warnings") or [])
        find_count = int(find_payload.get("findCount") or 0)
        highlight_ok = payload.get("highlightOk")
        if highlight_ok is False:
            status = "highlight_failed"
        elif find_count == 1:
            status = "verified"
        elif find_count == 0:
            status = "locator_unresolved"
        else:
            status = "locator_ambiguous"
        proof = self._proof(status, find_count, payload, warnings)
        locator_bundle["uniqueness"] = {
            "status": "unique" if status == "verified" else status,
            "count": find_count,
            "verifiedAt": proof["verifiedAt"],
            "source": find_payload.get("source") or "replay_verifier",
        }
        patch = {"proof": proof, "locatorBundle": locator_bundle}
        updated = self.recording_manager.update_capture_pool_item(recording_id, temp_element_id, patch)
        return {"ok": status == "verified", "status": status, "proof": proof, "recording": updated}

    def _resolve_find_count(self, item: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        if "findCount" in payload:
            return {"findCount": int(payload.get("findCount") or 0), "source": "request"}
        sidecar_uniqueness = _coerce_dict(_coerce_dict(item.get("locatorBundle")).get("uniqueness"))
        if sidecar_uniqueness.get("count") not in (None, ""):
            return {"findCount": int(sidecar_uniqueness.get("count") or 0), "source": sidecar_uniqueness.get("source") or "sidecar_uniqueness"}
        if self.resolver:
            return self.resolver(item, payload)
        if payload.get("allowLiveResolve"):
            return self._live_resolve(item, payload)
        return {"findCount": 0, "source": "unavailable", "warnings": ["live locator resolve was not enabled and sidecar did not provide uniqueness evidence"]}

    def _live_resolve(self, item: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from runtimes.computer_use.runtime import computer_use_runtime

            anchor = _coerce_dict(item.get("anchorBundle"))
            window = _coerce_dict(anchor.get("window") or item.get("targetWindow"))
            result = computer_use_runtime.find_elements(
                window_title=window.get("title") or window.get("windowTitle"),
                window_handle=window.get("handle") or window.get("windowHandle"),
                limit=int(payload.get("limit") or 20),
                depth_limit=int(payload.get("depthLimit") or 8),
            )
            return {"findCount": int(result.get("count") or len(result.get("elements") or [])), "source": "computer_use_find_elements"}
        except Exception as exc:
            return {"findCount": 0, "source": "computer_use_find_elements", "warnings": [f"live resolve failed: {exc}"]}

    @staticmethod
    def _proof(status: str, find_count: int, payload: Dict[str, Any], warnings: list[str]) -> Dict[str, Any]:
        return {
            "status": status,
            "findCount": find_count,
            "highlightRef": payload.get("highlightRef"),
            "screenshotRef": payload.get("screenshotRef"),
            "dryRunResult": _coerce_dict(payload.get("dryRunResult")),
            "warnings": warnings,
            "verifiedAt": utc_now_iso(),
            "verifier": "rpa_replay_verifier_v2",
        }


def normalize_locator_bundle(candidate: Dict[str, Any], *, platform: str) -> Dict[str, Any]:
    existing = _coerce_dict(candidate.get("locatorBundle"))
    if existing:
        existing.setdefault("platform", platform)
        return existing
    selector = _coerce_dict(candidate.get("selector"))
    selector_candidates = [item for item in _coerce_list(candidate.get("selectorCandidates")) if isinstance(item, dict)]
    primary = selector or (dict(selector_candidates[0]) if selector_candidates else {})
    for key in ("automationId", "name", "controlType", "className", "role", "selector", "css", "xpath", "text"):
        if candidate.get(key) not in (None, "", [], {}) and key not in primary:
            primary[key] = candidate.get(key)
    uniqueness = _coerce_dict(candidate.get("uniqueness"))
    if not uniqueness and candidate.get("findCount") not in (None, ""):
        uniqueness = {"count": int(candidate.get("findCount") or 0), "source": "sidecar"}
    return {
        "platform": platform,
        "primaryLocator": primary,
        "alternateLocators": selector_candidates[1:] if selector_candidates else [],
        "searchScope": _coerce_dict(candidate.get("searchScope") or candidate.get("targetWindow")),
        "uniqueness": uniqueness,
        "confidence": candidate.get("confidence") or primary.get("confidence"),
        "source": candidate.get("source") or "inspector_sidecar",
    }


def normalize_anchor_bundle(candidate: Dict[str, Any]) -> Dict[str, Any]:
    existing = _coerce_dict(candidate.get("anchorBundle"))
    if existing:
        return existing
    return {
        "window": _coerce_dict(candidate.get("targetWindow") or candidate.get("window")),
        "imageAnchor": _coerce_dict(candidate.get("imageAnchor") or candidate.get("screenshotAnchor")),
        "coordinateAnchor": _coerce_dict(candidate.get("coordinateAnchor")),
        "windowRelativeCoordinate": _coerce_dict(candidate.get("windowRelativeCoordinate") or candidate.get("coordinate")),
        "screenshotAnchor": _coerce_dict(candidate.get("screenshotAnchor")),
    }


def legacy_selector_from_locator_bundle(locator_bundle: Dict[str, Any]) -> Dict[str, Any]:
    primary = _coerce_dict(locator_bundle.get("primaryLocator"))
    return {
        key: value
        for key, value in {
            "automationId": primary.get("automationId") or primary.get("id"),
            "name": primary.get("name"),
            "controlType": primary.get("controlType") or primary.get("role"),
            "className": primary.get("className"),
            "css": primary.get("css") or primary.get("selector"),
            "xpath": primary.get("xpath"),
        }.items()
        if value not in (None, "", [], {})
    }
