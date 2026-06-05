from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from core.models.factory import llm_factory
from core.background_model_output import parse_background_json_object
from core.model_control_plane import model_control_plane
from core.multimodal_payload_adapter import build_multimodal_content

from runtimes.computer_use.candidate_board import CandidateBoard, CandidateBoardCandidate


_VISUAL_ACTOR_ROLES = ("computer_use_visual_actor", "computer_use_visual_judge", "vision")


@dataclass(slots=True)
class VisualActorRequest:
    goal: str
    screenshotArtifactId: str | None = None
    screenshotPath: str | None = None
    candidateBoard: CandidateBoard | dict[str, Any] | None = None
    clickableTree: list[dict[str, Any]] = field(default_factory=list)
    previousFrameSummary: str | None = None
    displayBounds: dict[str, Any] | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        board = self.candidateBoard
        if isinstance(board, CandidateBoard):
            board_payload: dict[str, Any] | None = board.as_dict()
        elif isinstance(board, dict):
            board_payload = dict(board)
        else:
            board_payload = None
        return {
            "goal": self.goal,
            "screenshotArtifactId": self.screenshotArtifactId,
            "screenshotPath": self.screenshotPath,
            "candidateBoard": board_payload,
            "clickableTree": list(self.clickableTree or []),
            "previousFrameSummary": self.previousFrameSummary,
            "displayBounds": dict(self.displayBounds or {}),
            "context": dict(self.context or {}),
        }


@dataclass(slots=True)
class VisualActorProposal:
    status: str
    actionType: str | None = None
    candidateId: str | None = None
    normalizedPoint: dict[str, float] | None = None
    absolutePoint: dict[str, float] | None = None
    confidence: float = 0.0
    expectedStateChange: str | None = None
    source: str = "candidate_board_heuristic"
    risk: str = "low"
    reason: str | None = None
    modelRole: str | None = None
    modelId: str | None = None
    providerId: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "actionType": self.actionType,
            "candidateId": self.candidateId,
            "normalizedPoint": self.normalizedPoint,
            "absolutePoint": self.absolutePoint,
            "confidence": round(float(self.confidence), 4),
            "expectedStateChange": self.expectedStateChange,
            "source": self.source,
            "risk": self.risk,
            "reason": self.reason,
            "modelRole": self.modelRole,
            "modelId": self.modelId,
            "providerId": self.providerId,
            "metadata": dict(self.metadata or {}),
        }


class VisualActorProvider:
    provider_id = "computer_use_visual_actor_provider"

    def _role_state(self) -> dict[str, Any]:
        last_error: str | None = None
        for role in _VISUAL_ACTOR_ROLES:
            try:
                resolved = model_control_plane.resolve_model_for_role(role)
            except Exception as exc:  # pragma: no cover - defensive
                last_error = str(exc)
                continue
            model_id = str(resolved.get("resolvedModelId") or "").strip()
            provider_id = str(resolved.get("resolvedProviderId") or "").strip()
            if model_id:
                return {
                    "available": True,
                    "role": role,
                    "modelId": model_id,
                    "providerId": provider_id,
                    "fallbackRoles": list(_VISUAL_ACTOR_ROLES[1:]),
                }
        return {
            "available": False,
            "role": "computer_use_visual_actor",
            "modelId": None,
            "providerId": None,
            "fallbackRoles": list(_VISUAL_ACTOR_ROLES[1:]),
            "reason": last_error or "no_visual_actor_or_vision_role_bound",
        }

    def is_available(self) -> bool:
        return bool(self._role_state().get("available"))

    def availability_summary(self) -> dict[str, Any]:
        state = self._role_state()
        return {
            "providerId": self.provider_id,
            "available": bool(state.get("available")),
            "role": state.get("role"),
            "modelId": state.get("modelId"),
            "providerIdForModel": state.get("providerId"),
            "fallbackRoles": list(state.get("fallbackRoles") or []),
            "mode": "multimodal_model_proposal_with_candidate_board_fallback",
            "executionPolicy": "proposal_only_then_safety_guard_and_post_action_verification",
            "reason": state.get("reason"),
        }

    def propose(self, request: VisualActorRequest) -> VisualActorProposal:
        state = self._role_state()
        candidates = _board_candidates(request.candidateBoard)
        if state.get("available"):
            model_proposal, model_error = self._propose_with_model(request, candidates, state=state)
            if model_proposal is not None:
                return model_proposal
        else:
            model_error = state.get("reason")
        candidate = _top_safe_candidate(request.candidateBoard)
        if candidate is None:
            return VisualActorProposal(
                status="no_candidate",
                reason="candidate_board_empty",
                modelRole=str(state.get("role") or "computer_use_visual_actor"),
                modelId=state.get("modelId"),
                providerId=state.get("providerId"),
                metadata={"goal": request.goal},
            )
        absolute = dict(candidate.center or {})
        normalized = _normalized_point(absolute, request.displayBounds)
        return VisualActorProposal(
            status="proposed",
            actionType=_action_type_for(candidate),
            candidateId=candidate.candidateId,
            normalizedPoint=normalized,
            absolutePoint=absolute or None,
            confidence=min(max(float(candidate.score or 0.0), 0.0), 0.99),
            expectedStateChange=_expected_state_change(request.goal, candidate),
            source="candidate_board_heuristic" if not state.get("available") else "visual_actor_role_ready_candidate_board",
            risk=candidate.risk,
            reason="top_candidate_selected",
            modelRole=str(state.get("role") or "computer_use_visual_actor"),
            modelId=state.get("modelId"),
            providerId=state.get("providerId"),
            metadata={
                "candidateSource": candidate.source,
                "candidateRole": candidate.role,
                "modelAvailable": bool(state.get("available")),
                "fallbackReason": model_error,
                "proposalPolicy": "no_direct_execution",
            },
        )

    def _propose_with_model(
        self,
        request: VisualActorRequest,
        candidates: list[CandidateBoardCandidate],
        *,
        state: dict[str, Any],
    ) -> tuple[VisualActorProposal | None, str | None]:
        if not candidates:
            return None, "candidate_board_empty"
        screenshot_ref = _screenshot_data_url(request.screenshotPath)
        if not screenshot_ref:
            return None, "screenshot_unavailable_for_multimodal_actor"
        candidate_payload = [item.as_dict() for item in candidates[:12]]
        prompt = (
            "You are V8OS Computer Use Visual Actor. Propose exactly one next UI action.\n"
            "Return strict JSON only, no markdown. Schema:\n"
            "{\"status\":\"proposed|no_action\",\"actionType\":\"click|focus_or_type|toggle|hover\","
            "\"candidateId\":\"...\",\"confidence\":0.0,\"expectedStateChange\":\"...\","
            "\"reason\":\"...\",\"pureVisual\":{\"x\":0.0,\"y\":0.0}|null}.\n"
            "Prefer a candidateId from the candidate board. Use pureVisual only if no candidate matches.\n"
            "Never propose destructive or high-risk actions. Goal and candidate board follow.\n"
            f"Goal: {request.goal}\n"
            f"Previous frame: {request.previousFrameSummary or ''}\n"
            f"Display bounds: {json.dumps(request.displayBounds or {}, ensure_ascii=False)}\n"
            f"Candidates: {json.dumps(candidate_payload, ensure_ascii=False)}"
        )
        try:
            model = llm_factory.create_for_role(str(state.get("role") or "computer_use_visual_actor"), temperature=0, streaming=False)
            content = build_multimodal_content(
                prompt=prompt,
                media_url=screenshot_ref,
                mime_type="image/png",
                transport_mode="inline_base64_image",
            )
            response = model.invoke([HumanMessage(content=content)])
            payload = _parse_json_response(response)
            proposal = _proposal_from_model_payload(
                payload,
                request=request,
                candidates=candidates,
                state=state,
            )
            if proposal is None:
                return None, "model_payload_failed_validation"
            return proposal, None
        except Exception as exc:
            return None, f"model_invoke_failed:{type(exc).__name__}:{str(exc)[:180]}"


def _board_candidates(board: CandidateBoard | dict[str, Any] | None) -> list[CandidateBoardCandidate]:
    if isinstance(board, CandidateBoard):
        return list(board.candidates or [])
    if not isinstance(board, dict):
        return []
    candidates: list[CandidateBoardCandidate] = []
    for item in list(board.get("candidates") or []):
        if not isinstance(item, dict):
            continue
        candidates.append(
            CandidateBoardCandidate(
                candidateId=str(item.get("candidateId") or ""),
                source=str(item.get("source") or "unknown"),
                role=str(item.get("role") or "generic_region"),
                bbox=dict(item.get("bbox") or {}) or None,
                center=dict(item.get("center") or {}) or None,
                label=item.get("label"),
                text=item.get("text"),
                score=float(item.get("score") or 0.0),
                risk=str(item.get("risk") or "low"),
                metadata=dict(item.get("metadata") or {}),
            )
        )
    return candidates


def _screenshot_data_url(path: str | None) -> str | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    target = Path(raw)
    if not target.exists() or not target.is_file():
        return None
    try:
        encoded = base64.b64encode(target.read_bytes()).decode("ascii")
    except Exception:
        return None
    return f"data:image/png;base64,{encoded}"


def _parse_json_response(value: Any) -> dict[str, Any]:
    payload, _sanitized, _error = parse_background_json_object(value)
    return payload if isinstance(payload, dict) else {}


def _proposal_from_model_payload(
    payload: dict[str, Any],
    *,
    request: VisualActorRequest,
    candidates: list[CandidateBoardCandidate],
    state: dict[str, Any],
) -> VisualActorProposal | None:
    status = str(payload.get("status") or "proposed").strip().lower()
    if status not in {"proposed", "no_action"}:
        return None
    if status == "no_action":
        return VisualActorProposal(
            status="no_action",
            reason=str(payload.get("reason") or "model_declined_action"),
            source="visual_actor_model",
            modelRole=str(state.get("role") or "computer_use_visual_actor"),
            modelId=state.get("modelId"),
            providerId=state.get("providerId"),
            metadata={"modelAvailable": True, "proposalPolicy": "no_direct_execution"},
        )
    by_id = {item.candidateId: item for item in candidates}
    candidate_id = str(payload.get("candidateId") or "").strip()
    candidate = by_id.get(candidate_id)
    pure_visual = payload.get("pureVisual") if isinstance(payload.get("pureVisual"), dict) else None
    if candidate is None and not pure_visual:
        return None
    if candidate is not None and candidate.risk == "high":
        return None
    action_type = str(payload.get("actionType") or (_action_type_for(candidate) if candidate else "click")).strip() or "click"
    if action_type not in {"click", "focus_or_type", "toggle", "hover"}:
        return None
    absolute = dict(candidate.center or {}) if candidate is not None else _absolute_from_normalized(pure_visual, request.displayBounds)
    normalized = _normalized_point(absolute, request.displayBounds)
    try:
        confidence = float(payload.get("confidence") or (candidate.score if candidate else 0.45))
    except Exception:
        confidence = 0.45
    return VisualActorProposal(
        status="proposed",
        actionType=action_type,
        candidateId=candidate.candidateId if candidate else None,
        normalizedPoint=normalized,
        absolutePoint=absolute or None,
        confidence=min(max(confidence, 0.0), 0.99),
        expectedStateChange=str(payload.get("expectedStateChange") or (_expected_state_change(request.goal, candidate) if candidate else "UI should show measurable progress")),
        source="visual_actor_model",
        risk=candidate.risk if candidate else "medium",
        reason=str(payload.get("reason") or "model_selected_candidate"),
        modelRole=str(state.get("role") or "computer_use_visual_actor"),
        modelId=state.get("modelId"),
        providerId=state.get("providerId"),
        metadata={
            "candidateSource": candidate.source if candidate else "pure_visual",
            "candidateRole": candidate.role if candidate else "pure_visual",
            "modelAvailable": True,
            "proposalPolicy": "no_direct_execution",
            "pureVisual": dict(pure_visual or {}) or None,
        },
    )


def _absolute_from_normalized(point: dict[str, Any] | None, display_bounds: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(point, dict):
        return None
    bounds = dict(display_bounds or {})
    width = float(bounds.get("width") or bounds.get("right") or 0)
    height = float(bounds.get("height") or bounds.get("bottom") or 0)
    if width <= 0 or height <= 0:
        return None
    try:
        x = max(min(float(point.get("x") or 0), 1.0), 0.0) * width
        y = max(min(float(point.get("y") or 0), 1.0), 0.0) * height
    except Exception:
        return None
    return {"x": x, "y": y}


def _top_safe_candidate(board: CandidateBoard | dict[str, Any] | None) -> CandidateBoardCandidate | None:
    candidates = _board_candidates(board)
    if not candidates:
        return None
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if candidate.risk != "high":
            return candidate
    return sorted(candidates, key=lambda item: item.score, reverse=True)[0]


def _normalized_point(point: dict[str, Any], display_bounds: dict[str, Any] | None) -> dict[str, float] | None:
    if not point:
        return None
    bounds = dict(display_bounds or {})
    width = float(bounds.get("width") or bounds.get("right") or 0)
    height = float(bounds.get("height") or bounds.get("bottom") or 0)
    if width <= 0 or height <= 0:
        return None
    return {
        "x": round(max(min(float(point.get("x") or 0) / width, 1.0), 0.0), 6),
        "y": round(max(min(float(point.get("y") or 0) / height, 1.0), 0.0), 6),
    }


def _action_type_for(candidate: CandidateBoardCandidate) -> str:
    role = str(candidate.role or "").lower()
    if role in {"search_box", "textbox", "edit", "input"}:
        return "focus_or_type"
    if role in {"checkbox", "radio", "toggle"}:
        return "toggle"
    return "click"


def _expected_state_change(goal: str, candidate: CandidateBoardCandidate) -> str:
    goal_text = str(goal or "").lower()
    label = " ".join([str(candidate.label or ""), str(candidate.text or "")]).strip()
    if "star" in goal_text or "星标" in goal_text:
        return "target state should become Starred or show an authentication boundary"
    if "登录" in goal_text or "login" in goal_text:
        return "login form or authenticated account state should become visible"
    if label:
        return f"UI should visibly react to candidate '{label[:80]}'"
    return "UI should show a measurable post-action state change"


def create_visual_actor_provider() -> VisualActorProvider:
    return VisualActorProvider()
