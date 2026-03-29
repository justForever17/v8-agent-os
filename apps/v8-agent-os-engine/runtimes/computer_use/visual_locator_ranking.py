from __future__ import annotations

from typing import Any, Dict, List
from pathlib import Path

from core.llm_factory import llm_factory
from core.model_control_plane import model_control_plane
from core.storage import storage


ACTION_BUTTON_TOKENS = {
    "发送",
    "确定",
    "确认",
    "保存",
    "删除",
    "提交",
    "继续",
    "下一步",
    "完成",
    "安装",
    "应用",
    "是",
}

TITLE_TOKENS = {
    "发送给",
    "保存到",
    "确认删除",
    "确认发送",
}

IMAGE_ACTION_HINTS = {
    "button",
    "send",
    "confirm",
    "submit",
    "ok",
    "yes",
    "apply",
    "save",
}

IMAGE_TITLE_HINTS = {
    "title",
    "header",
    "caption",
}

IMAGE_SEARCH_HINTS = {
    "search",
    "find",
    "lookup",
    "magnifier",
    "loupe",
}
_CANDIDATE_RERANK_BONUSES = (18.0, 10.0, 4.0, 1.0)
_CANDIDATE_RERANKER_CACHE: Dict[str, Any] = {}


def infer_visual_locator_role(locator: Any) -> str:
    ocr_role = _query_role(_parse_ocr_query(locator))
    if ocr_role != "generic":
        return ocr_role
    token = str(locator or "").strip()
    if ":" not in token:
        return "generic"
    scheme, value = token.split(":", 1)
    if scheme.strip().lower() != "image":
        return "generic"
    stem = Path(str(value or "").strip()).stem.lower()
    parts = {part for part in stem.replace("-", "_").split("_") if part}
    if parts & IMAGE_ACTION_HINTS:
        return "action_button"
    if parts & IMAGE_TITLE_HINTS:
        return "dialog_title"
    if parts & IMAGE_SEARCH_HINTS:
        return "search_box"
    return "generic"


def infer_visual_locator_chain_role(locators: List[Any] | None) -> str:
    for locator in list(locators or []):
        role = infer_visual_locator_role(locator)
        if role != "generic":
            return role
    return "generic"


def _normalize_text(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _parse_ocr_query(locator: Any) -> str | None:
    token = str(locator or "").strip()
    if ":" not in token:
        return None
    scheme, value = token.split(":", 1)
    if scheme.strip().lower() not in {"ocr", "text"}:
        return None
    normalized = str(value or "").strip()
    return normalized or None


def _query_role(query: str | None) -> str:
    normalized = _normalize_text(query)
    if not normalized:
        return "generic"
    if _normalize_text("搜索") in normalized:
        return "search_box"
    if any(_normalize_text(item) in normalized for item in TITLE_TOKENS):
        return "dialog_title"
    if any(_normalize_text(item) == normalized or _normalize_text(item) in normalized for item in ACTION_BUTTON_TOKENS):
        return "action_button"
    return "generic"


def _resolve_candidate_rerank_state() -> Dict[str, Any]:
    config = storage.get_computer_use_config() or {}
    enabled = bool(config.get("candidateRerankEnabled", False))
    if not enabled:
        return {
            "enabled": False,
            "available": False,
            "mode": "lexical",
            "role": "",
            "modelId": "",
            "reason": "disabled",
        }
    for role in ("computer_use_candidate_reranker", "reranker"):
        try:
            resolved = model_control_plane.resolve_model_for_role(role)
        except Exception as exc:
            return {
                "enabled": True,
                "available": False,
                "mode": "fallback",
                "role": role,
                "modelId": "",
                "reason": str(exc),
            }
        model_id = str(resolved.get("resolvedModelId") or "").strip()
        if model_id:
            return {
                "enabled": True,
                "available": True,
                "mode": "rerank",
                "role": role,
                "modelId": model_id,
                "reason": "",
            }
    return {
        "enabled": True,
        "available": False,
        "mode": "fallback",
        "role": "computer_use_candidate_reranker",
        "modelId": "",
        "reason": "未绑定桌面候选重排模型，且没有可回退的全局重排模型。",
    }


def _candidate_rerank_bonus(position: int) -> float:
    if position < 0:
        return 0.0
    if position < len(_CANDIDATE_RERANK_BONUSES):
        return float(_CANDIDATE_RERANK_BONUSES[position])
    return 0.0


def _build_candidate_rerank_query(locator: str, *, role: str) -> str:
    ocr_query = _parse_ocr_query(locator)
    if ocr_query:
        return ocr_query
    normalized_locator = str(locator or "").strip() or "visual candidate"
    normalized_role = str(role or "generic").strip() or "generic"
    return f"{normalized_role}: {normalized_locator}"


def _build_candidate_rerank_document(entry: Dict[str, Any], *, role: str) -> str:
    match = dict(entry.get("match") or {})
    bbox = list(match.get("bbox") or [])
    bbox_text = ",".join(str(int(item)) for item in bbox) if len(bbox) == 4 else "unknown"
    reasons = "；".join(str(item).strip() for item in list(entry.get("reasons") or []) if str(item).strip())
    return "\n".join(
        [
            f"role: {str(role or 'generic').strip() or 'generic'}",
            f"label: {str(match.get('label') or '').strip() or 'none'}",
            f"text: {str(match.get('text') or '').strip() or 'none'}",
            f"semantic_hint: {str(match.get('semanticHint') or '').strip() or 'none'}",
            f"source_locator: {str(match.get('sourceLocator') or entry.get('sourceLocator') or '').strip() or 'none'}",
            f"ui_role: {str(match.get('role') or '').strip() or 'unknown'}",
            f"bbox: {bbox_text}",
            f"heuristic_reasons: {reasons or 'none'}",
        ]
    ).strip()


def _get_candidate_reranker(model_id: str, *, role: str) -> Any:
    cached = _CANDIDATE_RERANKER_CACHE.get(model_id)
    if cached is not None:
        return cached
    reranker = llm_factory.create_reranker_model(
        model_id,
        role=role or "computer_use_candidate_reranker",
        capability_class="reranker",
    )
    _CANDIDATE_RERANKER_CACHE[model_id] = reranker
    return reranker


def _apply_candidate_rerank(
    ranked: List[Dict[str, Any]],
    *,
    locator: str,
    role: str,
    heuristic_strong: bool,
) -> Dict[str, Any]:
    state = _resolve_candidate_rerank_state()
    if not state.get("enabled"):
        return {**state, "applied": False, "candidateCount": len(ranked), "bonuses": []}
    if heuristic_strong:
        return {
            **state,
            "mode": "lexical",
            "applied": False,
            "candidateCount": len(ranked),
            "bonuses": [],
            "reason": "启发式已经给出强命中，不再额外触发候选重排。",
        }
    if len(ranked) <= 1:
        return {
            **state,
            "mode": "lexical",
            "applied": False,
            "candidateCount": len(ranked),
            "bonuses": [],
            "reason": "候选数量不足，无需额外重排。",
        }
    if not state.get("available") or not str(state.get("modelId") or "").strip():
        return {**state, "applied": False, "candidateCount": len(ranked), "bonuses": []}

    try:
        reranker = _get_candidate_reranker(str(state.get("modelId") or "").strip(), role=str(state.get("role") or "computer_use_candidate_reranker"))
        documents = [_build_candidate_rerank_document(entry, role=role) for entry in ranked]
        results = reranker.rerank(
            _build_candidate_rerank_query(locator, role=role),
            documents,
            top_k=len(documents),
        )
        order: List[int] = []
        seen_indexes: set[int] = set()
        for result in list(results or []):
            try:
                index = int(result.get("index"))
            except Exception:
                continue
            if 0 <= index < len(ranked) and index not in seen_indexes:
                order.append(index)
                seen_indexes.add(index)
        order.extend(index for index in range(len(ranked)) if index not in seen_indexes)

        bonuses: List[Dict[str, Any]] = []
        for rerank_position, ranked_index in enumerate(order):
            entry = ranked[ranked_index]
            base_score = float(entry.get("score") or 0.0)
            rank_bonus = _candidate_rerank_bonus(rerank_position)
            entry["baseScore"] = round(base_score, 2)
            entry["rankBonus"] = round(rank_bonus, 2)
            entry["rerankRank"] = rerank_position + 1
            entry["score"] = round(base_score + rank_bonus, 2)
            bonuses.append(
                {
                    "candidateIndex": int(entry.get("index", ranked_index)),
                    "rerankRank": rerank_position + 1,
                    "rankBonus": round(rank_bonus, 2),
                }
            )
        ranked.sort(
            key=lambda item: (
                float(item.get("score") or 0.0),
                float(item.get("baseScore") or item.get("score") or 0.0),
            ),
            reverse=True,
        )
        return {
            **state,
            "applied": True,
            "candidateCount": len(ranked),
            "bonuses": bonuses,
        }
    except Exception as exc:
        return {
            **state,
            "mode": "fallback",
            "applied": False,
            "candidateCount": len(ranked),
            "bonuses": [],
            "reason": str(exc),
        }


def _distance_score(distance_ratio: float, *, max_score: float) -> float:
    clipped = max(0.0, min(1.0, float(distance_ratio)))
    return max(0.0, max_score * (1.0 - clipped))


def _center_inside_bounds(center: List[int] | None, bounds: List[int] | None) -> bool:
    if not isinstance(center, list) or len(center) != 2 or not isinstance(bounds, list) or len(bounds) != 4:
        return False
    try:
        center_x, center_y = [int(item) for item in center]
        left, top, right, bottom = [int(item) for item in bounds]
    except Exception:
        return False
    return left <= center_x <= right and top <= center_y <= bottom


def _bounds_iou(a: List[int] | None, b: List[int] | None) -> float:
    if not isinstance(a, list) or len(a) != 4 or not isinstance(b, list) or len(b) != 4:
        return 0.0
    try:
        ax1, ay1, ax2, ay2 = [int(v) for v in a]
        bx1, by1, bx2, by2 = [int(v) for v in b]
    except Exception:
        return 0.0
    inter_left = max(ax1, bx1)
    inter_top = max(ay1, by1)
    inter_right = min(ax2, bx2)
    inter_bottom = min(ay2, by2)
    if inter_right <= inter_left or inter_bottom <= inter_top:
        return 0.0
    inter_area = max(1, inter_right - inter_left) * max(1, inter_bottom - inter_top)
    area_a = max(1, ax2 - ax1) * max(1, ay2 - ay1)
    area_b = max(1, bx2 - bx1) * max(1, by2 - by1)
    union = max(1, area_a + area_b - inter_area)
    return float(inter_area) / float(union)


def _bounds_area(bounds: List[int] | None) -> float:
    if not isinstance(bounds, list) or len(bounds) != 4:
        return 0.0
    try:
        left, top, right, bottom = [int(v) for v in bounds]
    except Exception:
        return 0.0
    return float(max(1, right - left) * max(1, bottom - top))


def _action_card_score(
    *,
    bbox: List[int] | None,
    preferred_bounds: List[int] | None,
    role: str,
) -> Dict[str, Any]:
    if role != "action_button":
        return {"score": 0.0, "reasons": []}
    if not isinstance(bbox, list) or len(bbox) != 4:
        return {"score": 0.0, "reasons": []}
    try:
        left, top, right, bottom = [int(v) for v in bbox]
    except Exception:
        return {"score": 0.0, "reasons": []}
    width = max(1, right - left)
    height = max(1, bottom - top)
    aspect_ratio = float(width) / float(max(1, height))
    bbox_area = _bounds_area(bbox)
    preferred_area = _bounds_area(preferred_bounds)
    coverage = float(bbox_area) / float(max(1.0, preferred_area)) if preferred_area > 0 else 0.0
    score = 0.0
    reasons: List[str] = []
    if width >= 120 and height >= 28 and 1.8 <= aspect_ratio <= 12.0:
        score += 14.0
        reasons.append("候选呈现独立宽按钮卡片形态。")
    elif width >= 80 and height >= 24 and 1.2 <= aspect_ratio <= 14.0:
        score += 6.0
        reasons.append("候选具备按钮卡片形态特征。")
    if 0.18 <= coverage <= 0.95:
        score += 10.0
        reasons.append("候选尺寸与主操作区覆盖关系合理。")
    elif 0.08 <= coverage < 0.18:
        score += 4.0
        reasons.append("候选覆盖了主操作区的一部分。")
    elif coverage > 0.0 and coverage < 0.05:
        score -= 14.0
        reasons.append("候选更像按钮内部文字，不像完整按钮卡片。")
    elif coverage >= 0.05 and coverage < 0.12:
        score -= 6.0
        reasons.append("候选更像按钮局部区域，不像完整按钮卡片。")
    return {"score": score, "reasons": reasons}


def _match_semantic_score(
    match: Dict[str, Any],
    *,
    query: str | None,
    scope_bounds: List[int] | None,
    role: str,
    preferred_bounds: List[int] | None = None,
    source_locator: str | None = None,
) -> Dict[str, Any]:
    bbox = list(match.get("bbox") or [])
    center = list(match.get("center") or [])
    if len(bbox) != 4 or len(center) != 2:
        return {"score": -9999.0, "reasons": ["缺少 bbox/center，无法排序。"]}
    left, top, right, bottom = [int(item) for item in bbox]
    center_x, center_y = [int(item) for item in center]
    text = str(match.get("text") or "").strip()
    normalized_text = _normalize_text(text)
    normalized_query = _normalize_text(query)
    normalized_source_locator = str(source_locator or "").strip()
    source_role = infer_visual_locator_role(normalized_source_locator) if normalized_source_locator else "generic"
    reasons: List[str] = []
    score = 0.0

    if normalized_query:
        if normalized_text == normalized_query:
            score += 50.0
            reasons.append("文本与 OCR 查询完全一致。")
        elif normalized_query in normalized_text:
            score += 34.0
            reasons.append("文本包含 OCR 查询。")
        else:
            score -= 8.0
            reasons.append("文本与 OCR 查询不完全吻合。")
    if normalized_text.startswith(_normalize_text("已发送")):
        score -= 80.0
        reasons.append("文本疑似背景消息“已发送”。")
    if normalized_text.startswith(_normalize_text("发送给")) and role == "action_button":
        score -= 60.0
        reasons.append("文本更像弹窗标题，不像动作按钮。")
    if normalized_text.endswith(")") and role == "action_button":
        score += 8.0
        reasons.append("文本带计数尾缀，更像确认按钮。")

    if isinstance(scope_bounds, list) and len(scope_bounds) == 4:
        scope_left, scope_top, scope_right, scope_bottom = [int(item) for item in scope_bounds]
        scope_width = max(1, scope_right - scope_left)
        scope_height = max(1, scope_bottom - scope_top)
        scope_center_x = scope_left + scope_width / 2.0
        rel_x = abs(float(center_x) - scope_center_x) / max(1.0, scope_width / 2.0)
        rel_y = (float(center_y) - scope_top) / max(1.0, scope_height)
        score += _distance_score(rel_x, max_score=22.0)
        if role == "action_button":
            if rel_y >= 0.62:
                score += 28.0 + min(12.0, (rel_y - 0.62) * 30.0)
                reasons.append("位于 scope 底部，更像动作按钮。")
            elif rel_y >= 0.45:
                score += 10.0
                reasons.append("位于 scope 中下部。")
            else:
                score -= 24.0
                reasons.append("位于 scope 偏上，更像标题或正文。")
        elif role == "dialog_title":
            if rel_y <= 0.32:
                score += 24.0
                reasons.append("位于 scope 顶部，更像弹窗标题。")
            else:
                score -= 16.0
                reasons.append("位置偏低，不像标题。")
        else:
            score += _distance_score(abs(rel_y - 0.5) * 2.0, max_score=10.0)
    if isinstance(preferred_bounds, list) and len(preferred_bounds) == 4:
        iou = _bounds_iou(bbox, preferred_bounds)
        if iou >= 0.38:
            score += 28.0
            reasons.append("与主操作区域高度重叠。")
        elif iou >= 0.12:
            score += 16.0
            reasons.append("与主操作区域存在明显重叠。")
        elif _center_inside_bounds(center, preferred_bounds):
            score += 18.0
            reasons.append("中心点位于主操作区域内。")
        elif role == "action_button":
            score -= 20.0
            reasons.append("不在主操作区域内。")
        card_rank = _action_card_score(
            bbox=bbox,
            preferred_bounds=preferred_bounds,
            role=role,
        )
        score += float(card_rank["score"])
        reasons.extend(list(card_rank["reasons"]))
    if source_role == "action_button":
        score += 8.0
        reasons.append("模板来源更像动作按钮。")
    elif source_role == "dialog_title" and role == "action_button":
        score -= 18.0
        reasons.append("模板来源更像标题，不像动作按钮。")
    if role == "action_button" and not normalized_text:
        if isinstance(preferred_bounds, list) and _center_inside_bounds(center, preferred_bounds):
            score += 12.0
            reasons.append("无文字候选位于主操作区域内。")
        else:
            score -= 6.0
            reasons.append("无文字候选缺少区域佐证。")
    elif role == "action_button" and normalized_text and isinstance(preferred_bounds, list) and len(preferred_bounds) == 4:
        bbox_area = _bounds_area(bbox)
        preferred_area = _bounds_area(preferred_bounds)
        coverage = float(bbox_area) / float(max(1.0, preferred_area)) if preferred_area > 0 else 0.0
        if coverage < 0.08:
            score -= 14.0
            reasons.append("文字候选面积过小，更像按钮标签而非可点击整体。")
        elif coverage < 0.14:
            score -= 6.0
            reasons.append("文字候选面积偏小，完整按钮证据不足。")
    return {
        "score": round(score, 2),
        "reasons": reasons,
    }


def rank_visual_locator_resolution(
    resolution: Dict[str, Any] | None,
    *,
    locator: str,
    scope_bounds: List[int] | None = None,
    role: str | None = None,
    preferred_bounds: List[int] | None = None,
) -> Dict[str, Any]:
    payload = dict(resolution or {})
    matches = [dict(item) for item in list(payload.get("matches") or []) if isinstance(item, dict)]
    if not matches:
        payload["semanticRanking"] = {
            "role": str(role or infer_visual_locator_role(locator) or _query_role(_parse_ocr_query(locator)) or "generic"),
            "selectedStrong": False,
            "candidateCount": 0,
        }
        return payload
    query = _parse_ocr_query(locator)
    resolved_role = str(role or "").strip() or infer_visual_locator_role(locator) or _query_role(query) or "generic"
    ranked: List[Dict[str, Any]] = []
    for index, match in enumerate(matches):
        rank = _match_semantic_score(
            match,
            query=query,
            scope_bounds=scope_bounds,
            role=resolved_role,
            preferred_bounds=preferred_bounds,
            source_locator=str(match.get("_sourceLocator") or locator).strip() or locator,
        )
        ranked.append(
            {
                "index": index,
                "match": dict(match),
                "score": float(rank["score"]),
                "reasons": list(rank["reasons"]),
                "sourceLocator": str(match.get("_sourceLocator") or locator).strip() or locator,
                "providerId": str(match.get("_providerId") or payload.get("providerId") or "").strip() or None,
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    heuristic_top_score = float(ranked[0]["score"]) if ranked else -9999.0
    heuristic_second_score = float(ranked[1]["score"]) if len(ranked) > 1 else -9999.0
    heuristic_selected_strong = bool(
        heuristic_top_score >= 30.0 and (heuristic_top_score - heuristic_second_score) >= 8.0
    )
    rerank_state = _apply_candidate_rerank(
        ranked,
        locator=locator,
        role=resolved_role,
        heuristic_strong=heuristic_selected_strong,
    )
    selected = dict(ranked[0]["match"])
    selected_index = int(ranked[0]["index"])
    selected_score = float(ranked[0]["score"])
    second_score = float(ranked[1]["score"]) if len(ranked) > 1 else -9999.0
    selected_strong = bool(selected_score >= 30.0 and (selected_score - second_score) >= 8.0)

    payload["matches"] = [selected] if selected_strong else [item["match"] for item in ranked]
    payload["matchCount"] = 1 if selected_strong else len(ranked)
    payload["semanticRanking"] = {
        "role": resolved_role,
        "candidateCount": len(ranked),
        "selectedIndex": selected_index,
        "selectedScore": round(selected_score, 2),
        "secondScore": round(second_score, 2) if len(ranked) > 1 else None,
        "selectedStrong": selected_strong,
        "preferredBounds": list(preferred_bounds) if isinstance(preferred_bounds, list) and len(preferred_bounds) == 4 else None,
        "rerank": {
            "enabled": bool(rerank_state.get("enabled", False)),
            "applied": bool(rerank_state.get("applied", False)),
            "mode": str(rerank_state.get("mode") or "lexical"),
            "role": str(rerank_state.get("role") or "").strip() or None,
            "modelId": str(rerank_state.get("modelId") or "").strip() or None,
            "reason": str(rerank_state.get("reason") or "").strip() or None,
            "bonuses": list(rerank_state.get("bonuses") or []),
        },
        "rankedCandidates": [
            {
                "index": int(item["index"]),
                "score": round(float(item["score"]), 2),
                "baseScore": round(float(item.get("baseScore") or item["score"]), 2),
                "rankBonus": round(float(item.get("rankBonus") or 0.0), 2),
                "rerankRank": int(item["rerankRank"]) if item.get("rerankRank") is not None else None,
                "text": str((item["match"] or {}).get("text") or "").strip() or None,
                "label": str((item["match"] or {}).get("label") or "").strip() or None,
                "semanticHint": str((item["match"] or {}).get("semanticHint") or "").strip() or None,
                "bbox": list((item["match"] or {}).get("bbox") or []),
                "reasons": list(item["reasons"]),
                "sourceLocator": item.get("sourceLocator"),
                "providerId": item.get("providerId"),
            }
            for item in ranked[:5]
        ],
    }
    return payload


def merge_visual_locator_candidate_resolutions(
    resolutions: List[Dict[str, Any]] | None,
    *,
    locator_candidates: List[str],
    scope_bounds: List[int] | None = None,
    role: str | None = None,
    preferred_bounds: List[int] | None = None,
) -> Dict[str, Any]:
    provider_ids: List[str] = []
    merged_matches: List[Dict[str, Any]] = []
    for resolution in list(resolutions or []):
        payload = dict(resolution or {})
        provider_id = str(payload.get("providerId") or "").strip() or None
        if provider_id and provider_id not in provider_ids:
            provider_ids.append(provider_id)
        source_locator = str(payload.get("locatorCandidate") or payload.get("locator") or "").strip() or None
        for match in [dict(item) for item in list(payload.get("matches") or []) if isinstance(item, dict)]:
            candidate = dict(match)
            if source_locator:
                candidate["_sourceLocator"] = source_locator
            if provider_id:
                candidate["_providerId"] = provider_id
            merged_matches.append(candidate)
    merged_payload: Dict[str, Any] = {
        "providerId": provider_ids[0] if len(provider_ids) == 1 else "mixed_visual_locator",
        "locator": locator_candidates[0] if locator_candidates else None,
        "locatorChain": list(locator_candidates),
        "providerIds": list(provider_ids),
        "matchCount": len(merged_matches),
        "matches": merged_matches,
    }
    if len(locator_candidates) > 1:
        merged_payload["locatorCandidateIndex"] = None
        merged_payload["locatorCandidate"] = None
    return rank_visual_locator_resolution(
        merged_payload,
        locator=locator_candidates[0] if locator_candidates else "",
        scope_bounds=scope_bounds,
        role=role,
        preferred_bounds=preferred_bounds,
    )
