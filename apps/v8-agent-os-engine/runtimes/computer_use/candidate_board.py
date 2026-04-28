from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


_SOURCE_BASE_SCORE = {
    "browser_dom": 0.92,
    "accessibility": 0.88,
    "selector_memory": 0.84,
    "visual_locator": 0.76,
    "ocr_text": 0.68,
    "semantic_region": 0.62,
    "history": 0.58,
    "image_template": 0.54,
}

_ACTION_WORDS = {
    "button",
    "menuitem",
    "link",
    "checkbox",
    "radio",
    "tab",
    "submit",
    "star",
    "save",
    "open",
    "登录",
    "确定",
    "保存",
    "打开",
}

_RISKY_WORDS = {
    "delete",
    "remove",
    "destroy",
    "purchase",
    "pay",
    "transfer",
    "unstar",
    "删除",
    "移除",
    "付款",
    "支付",
    "转账",
}


@dataclass(slots=True)
class CandidateBoardCandidate:
    candidateId: str
    source: str
    role: str
    bbox: dict[str, float] | None = None
    center: dict[str, float] | None = None
    label: str | None = None
    text: str | None = None
    score: float = 0.0
    risk: str = "low"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidateId,
            "source": self.source,
            "role": self.role,
            "bbox": self.bbox,
            "center": self.center,
            "label": self.label,
            "text": self.text,
            "score": round(float(self.score), 4),
            "risk": self.risk,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(slots=True)
class CandidateBoard:
    version: int
    goal: str
    sources: list[str]
    candidates: list[CandidateBoardCandidate]
    summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "goal": self.goal,
            "sources": list(self.sources),
            "candidates": [item.as_dict() for item in self.candidates],
            "summary": dict(self.summary or {}),
        }


def candidate_board_source_catalog() -> list[dict[str, Any]]:
    return [
        {"source": "accessibility", "summary": "Driver/UIA/AX/AT-SPI 元素树候选。"},
        {"source": "ocr_text", "summary": "OCR 或文本 locator 提取的可见文字候选。"},
        {"source": "image_template", "summary": "模板/图像定位命中的视觉候选。"},
        {"source": "semantic_region", "summary": "页面语义区域和启发式 affordance 候选。"},
        {"source": "selector_memory", "summary": "历史稳定 selector/锚点候选，分辨率变化时降权。"},
        {"source": "browser_dom", "summary": "浏览器 DOM/CDP lane 候选。"},
        {"source": "history", "summary": "历史成功点和同任务 resume 候选。"},
    ]


def _stable_id(source: str, role: str, bbox: dict[str, float] | None, label: str | None, text: str | None) -> str:
    payload = json.dumps(
        {
            "source": source,
            "role": role,
            "bbox": _rounded_bbox(bbox),
            "label": str(label or "")[:96],
            "text": str(text or "")[:96],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"cand_{digest}"


def _rounded_bbox(bbox: dict[str, float] | None) -> dict[str, float] | None:
    if not bbox:
        return None
    return {
        key: round(float(value), 2)
        for key, value in bbox.items()
        if key in {"x", "y", "width", "height", "left", "top", "right", "bottom"}
    }


def _normalize_bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    if {"x", "y", "width", "height"}.issubset(value.keys()):
        width = max(float(value.get("width") or 0), 0.0)
        height = max(float(value.get("height") or 0), 0.0)
        if width <= 0 or height <= 0:
            return None
        return {
            "x": float(value.get("x") or 0),
            "y": float(value.get("y") or 0),
            "width": width,
            "height": height,
        }
    if {"left", "top", "right", "bottom"}.issubset(value.keys()):
        left = float(value.get("left") or 0)
        top = float(value.get("top") or 0)
        right = float(value.get("right") or 0)
        bottom = float(value.get("bottom") or 0)
        width = max(right - left, 0.0)
        height = max(bottom - top, 0.0)
        if width <= 0 or height <= 0:
            return None
        return {"x": left, "y": top, "width": width, "height": height}
    return None


def _center_from_bbox(bbox: dict[str, float] | None) -> dict[str, float] | None:
    if not bbox:
        return None
    return {
        "x": float(bbox["x"]) + float(bbox["width"]) / 2.0,
        "y": float(bbox["y"]) + float(bbox["height"]) / 2.0,
    }


def _risk_for(label: str | None, text: str | None, role: str | None) -> str:
    hay = " ".join([str(label or ""), str(text or ""), str(role or "")]).lower()
    if any(word in hay for word in _RISKY_WORDS):
        return "high" if any(word in hay for word in ("delete", "destroy", "删除", "转账")) else "medium"
    return "low"


def _role_for(source_role: Any, label: str | None, text: str | None) -> str:
    raw = str(source_role or "").strip().lower()
    if raw:
        return raw
    hay = " ".join([str(label or ""), str(text or "")]).lower()
    if any(word in hay for word in ("search", "搜索", "查找")):
        return "search_box"
    if any(word in hay for word in _ACTION_WORDS):
        return "action_button"
    return "generic_region"


def _goal_bonus(goal: str, label: str | None, text: str | None, role: str | None) -> float:
    goal_text = str(goal or "").lower()
    hay = " ".join([str(label or ""), str(text or ""), str(role or "")]).lower()
    bonus = 0.0
    for token in re.findall(r"[a-z0-9_\-/]+|[\u4e00-\u9fff]{1,4}", goal_text):
        if len(token) >= 2 and token in hay:
            bonus += 0.03
    if "star" in goal_text or "星标" in goal_text:
        if "star" in hay or "星标" in hay:
            bonus += 0.18
    if "登录" in goal_text or "login" in goal_text:
        if "sign in" in hay or "login" in hay or "登录" in hay:
            bonus += 0.12
    return min(bonus, 0.25)


def _candidate(
    *,
    goal: str,
    source: str,
    role: Any = None,
    bbox: Any = None,
    label: Any = None,
    text: Any = None,
    metadata: dict[str, Any] | None = None,
) -> CandidateBoardCandidate | None:
    normalized_bbox = _normalize_bbox(bbox)
    label_text = str(label or "").strip() or None
    visible_text = str(text or "").strip() or None
    resolved_role = _role_for(role, label_text, visible_text)
    if not normalized_bbox and not (label_text or visible_text):
        return None
    base = _SOURCE_BASE_SCORE.get(source, 0.45)
    if resolved_role in {"button", "action_button", "link", "menuitem"}:
        base += 0.05
    score = max(min(base + _goal_bonus(goal, label_text, visible_text, resolved_role), 0.99), 0.0)
    risk = _risk_for(label_text, visible_text, resolved_role)
    return CandidateBoardCandidate(
        candidateId=_stable_id(source, resolved_role, normalized_bbox, label_text, visible_text),
        source=source,
        role=resolved_role,
        bbox=normalized_bbox,
        center=_center_from_bbox(normalized_bbox),
        label=label_text,
        text=visible_text,
        score=score,
        risk=risk,
        metadata=dict(metadata or {}),
    )


def _iter_nested_dicts(payload: Any, *, limit: int = 500) -> Iterable[dict[str, Any]]:
    stack = [payload]
    seen = 0
    while stack and seen < limit:
        item = stack.pop()
        if isinstance(item, dict):
            seen += 1
            yield item
            for value in item.values():
                if isinstance(value, (dict, list, tuple)):
                    stack.append(value)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)


def _collect_accessibility(goal: str, observation: Any) -> list[CandidateBoardCandidate]:
    items: list[CandidateBoardCandidate] = []
    for node in _iter_nested_dicts(observation):
        bbox = (
            node.get("bbox")
            or node.get("bounds")
            or node.get("rect")
            or node.get("boundingBox")
            or node.get("rectangle")
        )
        label = node.get("name") or node.get("label") or node.get("ariaLabel") or node.get("title")
        text = node.get("text") or node.get("value") or node.get("description")
        role = node.get("role") or node.get("controlType") or node.get("type")
        candidate = _candidate(
            goal=goal,
            source="accessibility",
            role=role,
            bbox=bbox,
            label=label,
            text=text,
            metadata={"rawRole": role},
        )
        if candidate:
            items.append(candidate)
    return items


def _collect_locator(goal: str, locator_resolution: Any) -> list[CandidateBoardCandidate]:
    payload = locator_resolution if isinstance(locator_resolution, dict) else {}
    items: list[CandidateBoardCandidate] = []
    for match in list(payload.get("matches") or payload.get("candidates") or []):
        if not isinstance(match, dict):
            continue
        source = str(match.get("source") or match.get("provider") or "visual_locator").strip() or "visual_locator"
        if source not in _SOURCE_BASE_SCORE:
            source = "visual_locator"
        candidate = _candidate(
            goal=goal,
            source=source,
            role=match.get("role") or match.get("type"),
            bbox=match.get("bbox") or match.get("bounds") or match.get("rect"),
            label=match.get("label") or match.get("name"),
            text=match.get("text") or match.get("ocrText"),
            metadata={"confidence": match.get("confidence"), "provider": match.get("provider")},
        )
        if candidate:
            items.append(candidate)
    for item in list((payload.get("semanticRanking") or {}).get("rankedCandidates") or []):
        if not isinstance(item, dict):
            continue
        candidate = _candidate(
            goal=goal,
            source="semantic_region",
            role=item.get("role") or item.get("kind"),
            bbox=item.get("bbox") or item.get("bounds") or item.get("rect"),
            label=item.get("label") or item.get("name"),
            text=item.get("text") or item.get("summary"),
            metadata={"rank": item.get("rank"), "score": item.get("score")},
        )
        if candidate:
            items.append(candidate)
    return items


def _collect_visual_observation(goal: str, visual_observation: Any) -> list[CandidateBoardCandidate]:
    payload = visual_observation if isinstance(visual_observation, dict) else {}
    items: list[CandidateBoardCandidate] = []
    for key, role in (
        ("primaryActionButtonBounds", "action_button"),
        ("actionZoneBounds", "action_zone"),
        ("dialogBounds", "dialog"),
        ("searchBoxBounds", "search_box"),
    ):
        candidate = _candidate(
            goal=goal,
            source="semantic_region",
            role=role,
            bbox=payload.get(key),
            label=key,
            text=payload.get("summary") or payload.get("screenSummary"),
            metadata={"visualObservationKey": key},
        )
        if candidate:
            items.append(candidate)
    return items


def _collect_explicit(goal: str, source: str, candidates: Any) -> list[CandidateBoardCandidate]:
    items: list[CandidateBoardCandidate] = []
    for item in list(candidates or []):
        if not isinstance(item, dict):
            continue
        candidate = _candidate(
            goal=goal,
            source=source,
            role=item.get("role") or item.get("type"),
            bbox=item.get("bbox") or item.get("bounds") or item.get("rect"),
            label=item.get("label") or item.get("name"),
            text=item.get("text") or item.get("summary"),
            metadata={key: value for key, value in item.items() if key not in {"bbox", "bounds", "rect", "label", "name", "text", "summary"}},
        )
        if candidate:
            items.append(candidate)
    return items


def _dedupe(candidates: list[CandidateBoardCandidate]) -> list[CandidateBoardCandidate]:
    by_id: dict[str, CandidateBoardCandidate] = {}
    for candidate in candidates:
        existing = by_id.get(candidate.candidateId)
        if existing is None or candidate.score > existing.score:
            by_id[candidate.candidateId] = candidate
    return sorted(by_id.values(), key=lambda item: (item.score, item.source), reverse=True)


def build_candidate_board(
    *,
    goal: str,
    locator_resolution: dict[str, Any] | None = None,
    visual_observation: dict[str, Any] | None = None,
    observation: dict[str, Any] | None = None,
    selector_memory_candidates: list[dict[str, Any]] | None = None,
    browser_candidates: list[dict[str, Any]] | None = None,
    history_candidates: list[dict[str, Any]] | None = None,
    limit: int = 24,
) -> CandidateBoard:
    candidates: list[CandidateBoardCandidate] = []
    candidates.extend(_collect_accessibility(goal, observation or {}))
    candidates.extend(_collect_locator(goal, locator_resolution or {}))
    candidates.extend(_collect_visual_observation(goal, visual_observation or {}))
    candidates.extend(_collect_explicit(goal, "selector_memory", selector_memory_candidates))
    candidates.extend(_collect_explicit(goal, "browser_dom", browser_candidates))
    candidates.extend(_collect_explicit(goal, "history", history_candidates))
    deduped = _dedupe(candidates)[: max(int(limit or 24), 1)]
    sources = sorted({item.source for item in deduped})
    return CandidateBoard(
        version=1,
        goal=str(goal or "").strip(),
        sources=sources,
        candidates=deduped,
        summary={
            "count": len(deduped),
            "sources": sources,
            "topCandidateId": deduped[0].candidateId if deduped else None,
            "topSource": deduped[0].source if deduped else None,
            "riskCounts": {
                risk: sum(1 for item in deduped if item.risk == risk)
                for risk in ("low", "medium", "high")
            },
        },
    )
