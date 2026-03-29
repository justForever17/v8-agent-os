from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence


def _string(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def normalize_bbox(value: Sequence[Any] | None) -> List[float]:
    if not isinstance(value, Sequence) or len(value) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    left, top, right, bottom = [_float(item, 0.0) for item in value]
    return [
        max(0.0, min(1.0, left)),
        max(0.0, min(1.0, top)),
        max(0.0, min(1.0, right)),
        max(0.0, min(1.0, bottom)),
    ]


def bbox_iou(a: Sequence[Any] | None, b: Sequence[Any] | None) -> float:
    left_a, top_a, right_a, bottom_a = normalize_bbox(a)
    left_b, top_b, right_b, bottom_b = normalize_bbox(b)
    inter_left = max(left_a, left_b)
    inter_top = max(top_a, top_b)
    inter_right = min(right_a, right_b)
    inter_bottom = min(bottom_a, bottom_b)
    inter_width = max(0.0, inter_right - inter_left)
    inter_height = max(0.0, inter_bottom - inter_top)
    inter_area = inter_width * inter_height
    area_a = max(0.0, right_a - left_a) * max(0.0, bottom_a - top_a)
    area_b = max(0.0, right_b - left_b) * max(0.0, bottom_b - top_b)
    union_area = area_a + area_b - inter_area
    if union_area <= 0.0:
        return 0.0
    return inter_area / union_area


@dataclass(slots=True)
class VisualElementCandidate:
    role: str
    label: str = ""
    bbox: List[float] = field(default_factory=list)
    confidence: float = 0.0
    text: str = ""
    interaction_hint: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "label": self.label,
            "bbox": list(self.bbox),
            "confidence": round(float(self.confidence), 4),
            "text": self.text,
            "interactionHint": self.interaction_hint,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class VisualHitZone:
    role: str
    bbox: List[float] = field(default_factory=list)
    gesture: str = ""
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "bbox": list(self.bbox),
            "gesture": self.gesture,
            "confidence": round(float(self.confidence), 4),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class OfflineVisualParseResult:
    parser_id: str
    page_identity_candidates: List[str] = field(default_factory=list)
    blocker_candidates: List[str] = field(default_factory=list)
    affordance_regions: List[Dict[str, Any]] = field(default_factory=list)
    element_candidates: List[VisualElementCandidate] = field(default_factory=list)
    candidate_hit_zones: List[VisualHitZone] = field(default_factory=list)
    visual_confidence: float = 0.0
    latency_ms: int = 0
    source: str = "offline"
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "parserId": self.parser_id,
            "pageIdentityCandidates": list(self.page_identity_candidates),
            "blockerCandidates": list(self.blocker_candidates),
            "affordanceRegions": [dict(item) for item in self.affordance_regions],
            "elementCandidates": [item.as_dict() for item in self.element_candidates],
            "candidateHitZones": [item.as_dict() for item in self.candidate_hit_zones],
            "visualConfidence": round(float(self.visual_confidence), 4),
            "latencyMs": int(self.latency_ms),
            "source": self.source,
            "rawPayload": dict(self.raw_payload),
        }


@dataclass(slots=True)
class BenchmarkElementExpectation:
    role: str
    bbox: List[float] = field(default_factory=list)
    min_iou: float = 0.35
    gesture: str = ""
    required: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "bbox": list(self.bbox),
            "minIou": round(float(self.min_iou), 4),
            "gesture": self.gesture,
            "required": bool(self.required),
        }


@dataclass(slots=True)
class OfflineBenchmarkCase:
    case_id: str
    app_id: str
    scene_id: str
    image_path: str
    tags: List[str] = field(default_factory=list)
    page_identity_candidates: List[str] = field(default_factory=list)
    blocker_candidates: List[str] = field(default_factory=list)
    required_elements: List[BenchmarkElementExpectation] = field(default_factory=list)
    required_hit_zones: List[BenchmarkElementExpectation] = field(default_factory=list)
    forbidden_blockers: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "caseId": self.case_id,
            "appId": self.app_id,
            "sceneId": self.scene_id,
            "imagePath": self.image_path,
            "tags": list(self.tags),
            "pageIdentityCandidates": list(self.page_identity_candidates),
            "blockerCandidates": list(self.blocker_candidates),
            "requiredElements": [item.as_dict() for item in self.required_elements],
            "requiredHitZones": [item.as_dict() for item in self.required_hit_zones],
            "forbiddenBlockers": list(self.forbidden_blockers),
            "notes": list(self.notes),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class OfflineBenchmarkCaseResult:
    case_id: str
    parser_id: str
    passed: bool
    score: float
    page_identity_passed: bool
    blocker_passed: bool
    required_elements_passed: int
    required_elements_total: int
    required_hit_zones_passed: int
    required_hit_zones_total: int
    latency_ms: int = 0
    reasons: List[str] = field(default_factory=list)
    result: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "caseId": self.case_id,
            "parserId": self.parser_id,
            "passed": bool(self.passed),
            "score": round(float(self.score), 4),
            "pageIdentityPassed": bool(self.page_identity_passed),
            "blockerPassed": bool(self.blocker_passed),
            "requiredElementsPassed": int(self.required_elements_passed),
            "requiredElementsTotal": int(self.required_elements_total),
            "requiredHitZonesPassed": int(self.required_hit_zones_passed),
            "requiredHitZonesTotal": int(self.required_hit_zones_total),
            "latencyMs": int(self.latency_ms),
            "reasons": list(self.reasons),
            "result": dict(self.result),
        }


def parse_visual_result(payload: Dict[str, Any] | None, *, parser_id: str = "unknown") -> OfflineVisualParseResult:
    data = dict(payload or {})
    element_candidates = [
        VisualElementCandidate(
            role=_string(item.get("role")),
            label=_string(item.get("label")),
            bbox=normalize_bbox(item.get("bbox")),
            confidence=_float(item.get("confidence"), 0.0),
            text=_string(item.get("text")),
            interaction_hint=_string(item.get("interactionHint") or item.get("gesture")),
            metadata=dict(item.get("metadata") or {}),
        )
        for item in list(data.get("elementCandidates") or [])
        if isinstance(item, dict) and _string(item.get("role"))
    ]
    hit_zones = [
        VisualHitZone(
            role=_string(item.get("role")),
            bbox=normalize_bbox(item.get("bbox")),
            gesture=_string(item.get("gesture")),
            confidence=_float(item.get("confidence"), 0.0),
            metadata=dict(item.get("metadata") or {}),
        )
        for item in list(data.get("candidateHitZones") or [])
        if isinstance(item, dict) and _string(item.get("role"))
    ]
    return OfflineVisualParseResult(
        parser_id=_string(data.get("parserId") or parser_id) or parser_id,
        page_identity_candidates=[_string(item) for item in list(data.get("pageIdentityCandidates") or []) if _string(item)],
        blocker_candidates=[_string(item) for item in list(data.get("blockerCandidates") or []) if _string(item)],
        affordance_regions=[dict(item) for item in list(data.get("affordanceRegions") or []) if isinstance(item, dict)],
        element_candidates=element_candidates,
        candidate_hit_zones=hit_zones,
        visual_confidence=_float(data.get("visualConfidence"), 0.0),
        latency_ms=int(_float(data.get("latencyMs"), 0.0)),
        source=_string(data.get("source") or "offline"),
        raw_payload=data,
    )


def parse_benchmark_case(payload: Dict[str, Any] | None) -> OfflineBenchmarkCase:
    data = dict(payload or {})
    return OfflineBenchmarkCase(
        case_id=_string(data.get("caseId")),
        app_id=_string(data.get("appId") or "desktop") or "desktop",
        scene_id=_string(data.get("sceneId")),
        image_path=_string(data.get("imagePath")),
        tags=[_string(item) for item in list(data.get("tags") or []) if _string(item)],
        page_identity_candidates=[_string(item) for item in list(data.get("pageIdentityCandidates") or []) if _string(item)],
        blocker_candidates=[_string(item) for item in list(data.get("blockerCandidates") or []) if _string(item)],
        required_elements=[
            BenchmarkElementExpectation(
                role=_string(item.get("role")),
                bbox=normalize_bbox(item.get("bbox")),
                min_iou=_float(item.get("minIou"), 0.35),
                gesture=_string(item.get("gesture")),
                required=bool(item.get("required", True)),
            )
            for item in list(data.get("requiredElements") or [])
            if isinstance(item, dict) and _string(item.get("role"))
        ],
        required_hit_zones=[
            BenchmarkElementExpectation(
                role=_string(item.get("role")),
                bbox=normalize_bbox(item.get("bbox")),
                min_iou=_float(item.get("minIou"), 0.35),
                gesture=_string(item.get("gesture")),
                required=bool(item.get("required", True)),
            )
            for item in list(data.get("requiredHitZones") or [])
            if isinstance(item, dict) and _string(item.get("role"))
        ],
        forbidden_blockers=[_string(item) for item in list(data.get("forbiddenBlockers") or []) if _string(item)],
        notes=[_string(item) for item in list(data.get("notes") or []) if _string(item)],
        metadata=dict(data.get("metadata") or {}),
    )


def _match_element(expectation: BenchmarkElementExpectation, candidates: Iterable[VisualElementCandidate]) -> bool:
    for candidate in list(candidates):
        if candidate.role != expectation.role:
            continue
        if bbox_iou(candidate.bbox, expectation.bbox) >= expectation.min_iou:
            return True
    return False


def _match_hit_zone(expectation: BenchmarkElementExpectation, candidates: Iterable[VisualHitZone]) -> bool:
    for candidate in list(candidates):
        if candidate.role != expectation.role:
            continue
        if expectation.gesture and candidate.gesture and candidate.gesture != expectation.gesture:
            continue
        if bbox_iou(candidate.bbox, expectation.bbox) >= expectation.min_iou:
            return True
    return False


def evaluate_offline_benchmark_case(
    case: OfflineBenchmarkCase,
    result: OfflineVisualParseResult,
) -> OfflineBenchmarkCaseResult:
    reasons: List[str] = []
    page_identity_passed = True
    if case.page_identity_candidates:
        page_identity_passed = any(item in result.page_identity_candidates for item in case.page_identity_candidates)
        if not page_identity_passed:
            reasons.append("page_identity_not_matched")
    blocker_passed = True
    if case.forbidden_blockers:
        blocker_passed = not any(item in result.blocker_candidates for item in case.forbidden_blockers)
        if not blocker_passed:
            reasons.append("forbidden_blocker_detected")

    required_elements_passed = 0
    for expectation in case.required_elements:
        if _match_element(expectation, result.element_candidates):
            required_elements_passed += 1
        elif expectation.required:
            reasons.append(f"missing_element:{expectation.role}")

    required_hit_zones_passed = 0
    for expectation in case.required_hit_zones:
        if _match_hit_zone(expectation, result.candidate_hit_zones):
            required_hit_zones_passed += 1
        elif expectation.required:
            reasons.append(f"missing_hit_zone:{expectation.role}")

    required_elements_total = len([item for item in case.required_elements if item.required])
    required_hit_zones_total = len([item for item in case.required_hit_zones if item.required])
    total_checks = 2 + required_elements_total + required_hit_zones_total
    passed_checks = (
        int(page_identity_passed)
        + int(blocker_passed)
        + required_elements_passed
        + required_hit_zones_passed
    )
    score = passed_checks / max(1, total_checks)
    return OfflineBenchmarkCaseResult(
        case_id=case.case_id,
        parser_id=result.parser_id,
        passed=not reasons,
        score=score,
        page_identity_passed=page_identity_passed,
        blocker_passed=blocker_passed,
        required_elements_passed=required_elements_passed,
        required_elements_total=required_elements_total,
        required_hit_zones_passed=required_hit_zones_passed,
        required_hit_zones_total=required_hit_zones_total,
        latency_ms=result.latency_ms,
        reasons=reasons,
        result=result.as_dict(),
    )


def summarize_offline_benchmark(results: Iterable[OfflineBenchmarkCaseResult]) -> Dict[str, Any]:
    items = list(results)
    total = len(items)
    passed = len([item for item in items if item.passed])
    avg_score = sum(item.score for item in items) / max(1, total)
    avg_latency = sum(item.latency_ms for item in items) / max(1, total)
    by_reason: Dict[str, int] = {}
    for item in items:
        for reason in item.reasons:
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "total": total,
        "passed": passed,
        "failed": max(0, total - passed),
        "passRate": round(passed / max(1, total), 4),
        "avgScore": round(avg_score, 4),
        "avgLatencyMs": round(avg_latency, 2),
        "topFailureReasons": dict(sorted(by_reason.items(), key=lambda row: row[1], reverse=True)),
    }
