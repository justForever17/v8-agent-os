from __future__ import annotations

from typing import Any, Dict, List


def _normalize_bounds(value: Any) -> List[int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [int(value[0]), int(value[1]), int(value[2]), int(value[3])]
    except Exception:
        return None


def _clamp_bounds(bounds: List[int], *, outer: List[int]) -> List[int]:
    left, top, right, bottom = [int(item) for item in bounds]
    outer_left, outer_top, outer_right, outer_bottom = [int(item) for item in outer]
    return [
        max(outer_left, min(left, outer_right)),
        max(outer_top, min(top, outer_bottom)),
        max(outer_left, min(right, outer_right)),
        max(outer_top, min(bottom, outer_bottom)),
    ]


def _candidate(
    *,
    bbox: List[int],
    label: str,
    semantic_hint: str,
    source_locator: str,
    reasons: List[str],
) -> Dict[str, Any]:
    left, top, right, bottom = [int(item) for item in bbox]
    return {
        "bbox": [left, top, right, bottom],
        "center": [
            int(left + max(1, right - left) // 2),
            int(top + max(1, bottom - top) // 2),
        ],
        "text": None,
        "label": str(label or "").strip() or None,
        "semanticHint": str(semantic_hint or "").strip() or None,
        "sourceLocator": str(source_locator or "").strip() or None,
        "providerId": "visual_semantic_candidate",
        "reasons": [str(item).strip() for item in list(reasons or []) if str(item).strip()],
    }


def build_semantic_visual_candidates(
    *,
    role: str,
    scope_bounds: List[int] | None,
    capture_bounds: List[int] | None,
) -> List[Dict[str, Any]]:
    normalized_role = str(role or "").strip().lower()
    bounds = _normalize_bounds(scope_bounds) or _normalize_bounds(capture_bounds)
    if normalized_role != "search_box" or not isinstance(bounds, list):
        return []

    left, top, right, bottom = [int(item) for item in bounds]
    width = max(1, right - left)
    height = max(1, bottom - top)

    sidebar_width = min(max(260, int(round(width * 0.34))), max(320, int(round(width * 0.42))))
    header_height = min(max(96, int(round(height * 0.17))), max(120, int(round(height * 0.22))))
    side_right = min(right, left + sidebar_width)
    header_bottom = min(bottom, top + header_height)

    left_search = _clamp_bounds(
        [
            left + int(round(sidebar_width * 0.06)),
            top + int(round(header_height * 0.22)),
            left + int(round(sidebar_width * 0.80)),
            top + int(round(header_height * 0.63)),
        ],
        outer=bounds,
    )
    top_search = _clamp_bounds(
        [
            left + int(round(width * 0.22)),
            top + int(round(height * 0.03)),
            left + int(round(width * 0.70)),
            top + int(round(height * 0.11)),
        ],
        outer=bounds,
    )
    right_action = _clamp_bounds(
        [
            left + int(round(sidebar_width * 0.82)),
            top + int(round(header_height * 0.18)),
            max(left + int(round(sidebar_width * 0.88)), side_right - int(round(sidebar_width * 0.05))),
            top + int(round(header_height * 0.64)),
        ],
        outer=[left, top, side_right, header_bottom],
    )
    avatar_strip = _clamp_bounds(
        [
            left,
            top,
            left + int(round(sidebar_width * 0.18)),
            top + int(round(header_height * 0.95)),
        ],
        outer=[left, top, side_right, header_bottom],
    )

    return [
        _candidate(
            bbox=left_search,
            label="左上搜索框长条区",
            semantic_hint="搜索框/搜索输入区，通常包含放大镜图标，可已有文本内容。",
            source_locator="semantic:search_box_strip_left",
            reasons=["位于左上头部区域", "横向长条形态明显", "优先作为搜索框候选"],
        ),
        _candidate(
            bbox=top_search,
            label="顶部搜索框长条区",
            semantic_hint="顶部横向搜索框，常见于浏览器或无侧边栏窗口。",
            source_locator="semantic:search_box_strip_top",
            reasons=["位于窗口顶部", "保留给无侧栏布局的搜索框场景"],
        ),
        _candidate(
            bbox=right_action,
            label="搜索框右侧附加操作区",
            semantic_hint="加号或其他附加操作按钮，不应优先当成搜索框。",
            source_locator="semantic:header_action_cluster",
            reasons=["靠近搜索框右侧", "更像附加操作区", "用于和搜索框做歧义裁判"],
        ),
        _candidate(
            bbox=avatar_strip,
            label="左侧头像/导航区",
            semantic_hint="头像或导航列，不是搜索框。",
            source_locator="semantic:sidebar_identity_strip",
            reasons=["位于左上身份栏", "作为负样本候选供裁判排除"],
        ),
    ]


def semantic_candidates_to_resolution(
    *,
    locator: str,
    role: str,
    scope_bounds: List[int] | None,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    normalized_candidates = [dict(item or {}) for item in list(candidates or []) if isinstance(item, dict)]
    return {
        "providerId": "visual_semantic_candidate",
        "status": "semantic_candidates_pending_judge",
        "locator": str(locator or "").strip() or None,
        "matchCount": 0,
        "matches": [],
        "scopeBounds": list(scope_bounds) if isinstance(scope_bounds, list) and len(scope_bounds) == 4 else None,
        "visualSemanticCandidates": normalized_candidates,
        "semanticRanking": {
            "role": str(role or "").strip() or "generic",
            "candidateCount": len(normalized_candidates),
            "selectedStrong": False,
            "rankedCandidates": [
                {
                    "bbox": list(item.get("bbox") or []),
                    "text": item.get("text"),
                    "label": item.get("label"),
                    "semanticHint": item.get("semanticHint"),
                    "sourceLocator": item.get("sourceLocator"),
                    "providerId": item.get("providerId"),
                    "score": None,
                    "reasons": list(item.get("reasons") or []),
                }
                for item in normalized_candidates
            ],
        },
    }
