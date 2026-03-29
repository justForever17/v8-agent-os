from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image

from runtimes.computer_use.visual_locator_scope import (
    crop_capture_image_to_bounds,
    derive_centered_dialog_seed_bounds,
)

ACTION_TEXT_TOKENS = [
    "发送",
    "确定",
    "确认",
    "保存",
    "删除",
    "提交",
]

TITLE_TEXT_PREFIXES = [
    "发送给",
    "保存到",
    "确认删除",
    "确认发送",
]


def _offset_bounds(local_bounds: List[int] | None, origin_bounds: List[int] | None) -> List[int] | None:
    if not isinstance(local_bounds, list) or len(local_bounds) != 4:
        return None
    if not isinstance(origin_bounds, list) or len(origin_bounds) != 4:
        return list(local_bounds)
    left, top, right, bottom = [int(item) for item in local_bounds]
    origin_left, origin_top, _, _ = [int(item) for item in origin_bounds]
    return [
        left + origin_left,
        top + origin_top,
        right + origin_left,
        bottom + origin_top,
    ]


def _normalize_text(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _is_title_like_text(value: Any) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    return any(normalized.startswith(_normalize_text(token)) for token in TITLE_TEXT_PREFIXES)


def _is_action_like_text(value: Any) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    for token in ACTION_TEXT_TOKENS:
        token_normalized = _normalize_text(token)
        if normalized == token_normalized:
            return True
        if normalized.startswith(token_normalized) and normalized.endswith(")"):
            return True
    return False


def _derive_dialog_bounds_from_rows(
    *,
    rows: List[Dict[str, Any]],
    image_path: str,
) -> List[int] | None:
    try:
        with Image.open(image_path) as source:
            image_width, image_height = source.size
    except Exception:
        return None
    if image_width <= 0 or image_height <= 0:
        return None
    image_center_x = image_width / 2.0
    meaningful_rows: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        left = int(row.get("left") or 0)
        top = int(row.get("top") or 0)
        right = int(row.get("right") or left)
        bottom = int(row.get("bottom") or top)
        width = max(0, right - left)
        height = max(0, bottom - top)
        if width < 10 or height < 10:
            continue
        row_center_x = left + width / 2.0
        if abs(row_center_x - image_center_x) > image_width * 0.38:
            continue
        normalized_text = _normalize_text(text)
        is_close_icon = (
            normalized_text in {"x", "×", "关闭", "关"}
            and left >= int(image_width * 0.72)
            and top <= int(image_height * 0.18)
            and width <= 42
            and height <= 42
        )
        if is_close_icon:
            continue
        meaningful_rows.append(
            {
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
            }
        )
    if not meaningful_rows:
        return None
    left = min(int(item["left"]) for item in meaningful_rows)
    top = min(int(item["top"]) for item in meaningful_rows)
    right = max(int(item["right"]) for item in meaningful_rows)
    bottom = max(int(item["bottom"]) for item in meaningful_rows)
    width = max(1, right - left)
    height = max(1, bottom - top)
    if width < 120 or height < 120:
        return None
    padding_x = max(36, int(width * 0.22))
    padding_top = max(24, int(height * 0.12))
    padding_bottom = max(48, int(height * 0.32))
    return [
        max(0, left - padding_x),
        max(0, top - padding_top),
        min(image_width, right + padding_x),
        min(image_height, bottom + padding_bottom),
    ]


def _derive_dialog_from_action_candidates(
    *,
    visual_locator_runtime: Any,
    capture_image_path: str,
    capture_bounds: List[int] | None,
    seed_bounds: List[int] | None,
) -> Dict[str, Any] | None:
    if not capture_image_path or not isinstance(seed_bounds, list) or len(seed_bounds) != 4:
        return None
    title_candidates: List[Dict[str, Any]] = []
    action_candidates: List[Dict[str, Any]] = []
    for query in ("发送", "确认", "确定", "保存"):
        try:
            resolved = dict(
                visual_locator_runtime.locate(
                    locator=f"ocr:{query}",
                    timeout_ms=1800,
                    confidence=None,
                    multiple=True,
                    read_text=False,
                    search_image_path=capture_image_path,
                    search_bounds=seed_bounds,
                )
                or {}
            )
        except Exception:
            continue
        for match in [dict(item) for item in list(resolved.get("matches") or []) if isinstance(item, dict)]:
            bbox = list(match.get("bbox") or [])
            center = list(match.get("center") or [])
            text = str(match.get("text") or "").strip()
            if len(bbox) != 4 or len(center) != 2 or not text:
                continue
            if _is_title_like_text(text):
                title_candidates.append({"text": text, "bbox": [int(v) for v in bbox], "center": [int(v) for v in center]})
            elif _is_action_like_text(text):
                action_candidates.append({"text": text, "bbox": [int(v) for v in bbox], "center": [int(v) for v in center]})
    if not title_candidates or not action_candidates:
        return None

    seed_left, seed_top, seed_right, seed_bottom = [int(v) for v in seed_bounds]
    seed_width = max(1, seed_right - seed_left)
    seed_height = max(1, seed_bottom - seed_top)
    seed_center_x = seed_left + seed_width / 2.0

    ranked_titles: List[Dict[str, Any]] = []
    for candidate in title_candidates:
        bbox = list(candidate["bbox"])
        center = list(candidate["center"])
        width = max(1, bbox[2] - bbox[0])
        center_distance = abs(float(center[0]) - seed_center_x) / max(1.0, seed_width / 2.0)
        rel_y = (float(center[1]) - seed_top) / max(1.0, seed_height)
        score = 0.0
        if rel_y <= 0.42:
            score += 24.0
        if center_distance <= 0.35:
            score += 16.0
        score += min(24.0, width / 10.0)
        ranked_titles.append({**candidate, "score": score})

    ranked_actions: List[Dict[str, Any]] = []
    for candidate in action_candidates:
        bbox = list(candidate["bbox"])
        center = list(candidate["center"])
        width = max(1, bbox[2] - bbox[0])
        center_distance = abs(float(center[0]) - seed_center_x) / max(1.0, seed_width / 2.0)
        rel_y = (float(center[1]) - seed_top) / max(1.0, seed_height)
        score = 0.0
        if rel_y >= 0.58:
            score += 30.0
        elif rel_y >= 0.45:
            score += 16.0
        if center_distance <= 0.28:
            score += 18.0
        score += min(16.0, width / 8.0)
        if str(candidate.get("text") or "").strip().endswith(")"):
            score += 8.0
        ranked_actions.append({**candidate, "score": score})

    ranked_titles.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    ranked_actions.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    title = dict(ranked_titles[0])
    action = dict(ranked_actions[0])

    if int(action["center"][1]) <= int(title["center"][1]):
        return None
    if abs(int(action["center"][0]) - int(title["center"][0])) > int(seed_width * 0.28):
        return None

    dialog_left = max(seed_left, min(int(title["bbox"][0]), int(action["bbox"][0])) - max(70, int(seed_width * 0.08)))
    dialog_top = max(seed_top, int(title["bbox"][1]) - max(28, int(seed_height * 0.04)))
    dialog_right = min(seed_right, max(int(title["bbox"][2]), int(action["bbox"][2])) + max(70, int(seed_width * 0.08)))
    dialog_bottom = min(seed_bottom, int(action["bbox"][3]) + max(52, int(seed_height * 0.08)))
    dialog_bounds = [dialog_left, dialog_top, dialog_right, dialog_bottom]
    dialog_width = max(1, dialog_right - dialog_left)
    dialog_height = max(1, dialog_bottom - dialog_top)
    dialog_center_x = dialog_left + dialog_width / 2.0
    dialog_center_distance = abs(dialog_center_x - seed_center_x) / max(1.0, seed_width / 2.0)
    dialog_width_ratio = float(dialog_width) / float(max(1, seed_width))
    dialog_height_ratio = float(dialog_height) / float(max(1, seed_height))
    edge_touches = 0
    if abs(dialog_left - seed_left) <= 2:
        edge_touches += 1
    if abs(dialog_top - seed_top) <= 2:
        edge_touches += 1
    if abs(dialog_right - seed_right) <= 2:
        edge_touches += 1
    if abs(dialog_bottom - seed_bottom) <= 2:
        edge_touches += 1
    if dialog_width_ratio < 0.28 or dialog_height_ratio < 0.26:
        return None
    if dialog_center_distance > 0.42:
        return None
    if edge_touches >= 2:
        return None
    structural_zones = _derive_dialog_structural_zones(dialog_bounds)
    action_bbox = [int(v) for v in action["bbox"]]
    padded_button_left = max(dialog_left, int(action_bbox[0]) - 16)
    padded_button_top = max(dialog_top, int(action_bbox[1]) - 12)
    padded_button_right = min(dialog_right, int(action_bbox[2]) + 16)
    padded_button_bottom = min(dialog_bottom, int(action_bbox[3]) + 12)
    primary_action_button_bounds = [
        min(padded_button_left, padded_button_right),
        min(padded_button_top, padded_button_bottom),
        max(padded_button_left, padded_button_right),
        max(padded_button_top, padded_button_bottom),
    ]
    if primary_action_button_bounds[2] - primary_action_button_bounds[0] < 56:
        primary_action_button_bounds[0] = max(dialog_left, int(action_bbox[0]) - 32)
        primary_action_button_bounds[2] = min(dialog_right, int(action_bbox[2]) + 32)
    if primary_action_button_bounds[3] - primary_action_button_bounds[1] < 28:
        primary_action_button_bounds[1] = max(dialog_top, int(action_bbox[1]) - 18)
        primary_action_button_bounds[3] = min(dialog_bottom, int(action_bbox[3]) + 18)
    return {
        "dialogBounds": dialog_bounds,
        "titleCandidate": title,
        "actionCandidate": action,
        "titleZoneBounds": structural_zones.get("titleZoneBounds"),
        "contentZoneBounds": structural_zones.get("contentZoneBounds"),
        "actionZoneBounds": structural_zones.get("actionZoneBounds"),
        "primaryActionZoneBounds": structural_zones.get("primaryActionZoneBounds"),
        "primaryActionButtonBounds": primary_action_button_bounds,
        "confidence": 0.72,
        "confidenceLevel": "high",
        "reasons": ["ocr_action_title_pair_detected", "dialog_reconstructed_from_ocr_candidates"],
    }


def _derive_dialog_bounds_from_pixels(*, image_path: str) -> List[int] | None:
    try:
        with Image.open(image_path).convert("L") as source:
            width, height = source.size
            if width < 80 or height < 80:
                return None
            sample_points = [
                (0, 0),
                (width - 1, 0),
                (0, height - 1),
                (width - 1, height - 1),
                (max(0, width // 8), max(0, height // 8)),
                (max(0, width - width // 8 - 1), max(0, height // 8)),
            ]
            samples = [int(source.getpixel((int(x), int(y)))) for x, y in sample_points]
            bg = int(sum(samples) / max(1, len(samples)))
            threshold = min(255, bg + 18)
            min_x = width
            min_y = height
            max_x = -1
            max_y = -1
            for y in range(height):
                for x in range(width):
                    value = int(source.getpixel((x, y)))
                    if value >= threshold:
                        min_x = min(min_x, x)
                        min_y = min(min_y, y)
                        max_x = max(max_x, x)
                        max_y = max(max_y, y)
            if max_x <= min_x or max_y <= min_y:
                return None
            if (max_x - min_x) < int(width * 0.32) or (max_y - min_y) < int(height * 0.26):
                return None
            return [int(min_x), int(min_y), int(max_x + 1), int(max_y + 1)]
    except Exception:
        return None
    return None


def _derive_dialog_structural_zones(dialog_bounds: List[int] | None) -> Dict[str, List[int] | None]:
    if not isinstance(dialog_bounds, list) or len(dialog_bounds) != 4:
        return {
            "titleZoneBounds": None,
            "contentZoneBounds": None,
            "actionZoneBounds": None,
            "primaryActionZoneBounds": None,
        }
    left, top, right, bottom = [int(item) for item in dialog_bounds]
    width = max(1, right - left)
    height = max(1, bottom - top)
    title_zone = [
        int(left + round(width * 0.08)),
        int(top + round(height * 0.04)),
        int(right - round(width * 0.08)),
        int(top + round(height * 0.26)),
    ]
    content_zone = [
        int(left + round(width * 0.06)),
        int(top + round(height * 0.22)),
        int(right - round(width * 0.06)),
        int(bottom - round(height * 0.30)),
    ]
    action_zone = [
        int(left + round(width * 0.06)),
        int(bottom - round(height * 0.28)),
        int(right - round(width * 0.06)),
        int(bottom - round(height * 0.03)),
    ]
    primary_action_zone = [
        int(left + round(width * 0.18)),
        int(bottom - round(height * 0.24)),
        int(right - round(width * 0.18)),
        int(bottom - round(height * 0.04)),
    ]
    return {
        "titleZoneBounds": title_zone,
        "contentZoneBounds": content_zone,
        "actionZoneBounds": action_zone,
        "primaryActionZoneBounds": primary_action_zone,
    }


def _bounds_iou(a: List[int] | None, b: List[int] | None) -> float:
    if not isinstance(a, list) or len(a) != 4 or not isinstance(b, list) or len(b) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = [int(v) for v in a]
    bx1, by1, bx2, by2 = [int(v) for v in b]
    inter_left = max(ax1, bx1)
    inter_top = max(ay1, by1)
    inter_right = min(ax2, bx2)
    inter_bottom = min(ay2, by2)
    if inter_right <= inter_left or inter_bottom <= inter_top:
        return 0.0
    inter = (inter_right - inter_left) * (inter_bottom - inter_top)
    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    union = max(1, area_a + area_b - inter)
    return float(inter) / float(union)


def _score_dialog_observation(
    *,
    seed_bounds: List[int] | None,
    dialog_bounds: List[int] | None,
    pixel_local_bounds: List[int] | None,
    aggregated_local_bounds: List[int] | None,
    row_count: int,
    primary_action_button_bounds: List[int] | None,
) -> Dict[str, Any]:
    if not isinstance(seed_bounds, list) or len(seed_bounds) != 4:
        return {"confidence": 0.0, "level": "low", "suppressed": True, "reasons": ["seed_missing"]}
    if not isinstance(dialog_bounds, list) or len(dialog_bounds) != 4:
        return {"confidence": 0.0, "level": "low", "suppressed": True, "reasons": ["dialog_missing"]}

    seed_left, seed_top, seed_right, seed_bottom = [int(v) for v in seed_bounds]
    dialog_left, dialog_top, dialog_right, dialog_bottom = [int(v) for v in dialog_bounds]
    seed_width = max(1, seed_right - seed_left)
    seed_height = max(1, seed_bottom - seed_top)
    dialog_width = max(1, dialog_right - dialog_left)
    dialog_height = max(1, dialog_bottom - dialog_top)

    width_ratio = float(dialog_width) / float(seed_width)
    height_ratio = float(dialog_height) / float(seed_height)
    area_ratio = width_ratio * height_ratio
    touches = 0
    if abs(dialog_left - seed_left) <= 2:
        touches += 1
    if abs(dialog_top - seed_top) <= 2:
        touches += 1
    if abs(dialog_right - seed_right) <= 2:
        touches += 1
    if abs(dialog_bottom - seed_bottom) <= 2:
        touches += 1

    score = 0.4
    reasons: List[str] = []
    if 0.18 <= area_ratio <= 0.88:
        score += 0.22
        reasons.append("dialog_area_reasonable")
    else:
        score -= 0.24
        reasons.append("dialog_area_extreme")
    if touches <= 1:
        score += 0.14
        reasons.append("dialog_not_touching_seed_edges")
    elif touches >= 3:
        score -= 0.34
        reasons.append("dialog_saturates_seed")
    if isinstance(pixel_local_bounds, list) and isinstance(aggregated_local_bounds, list):
        iou = _bounds_iou(pixel_local_bounds, aggregated_local_bounds)
        if iou >= 0.35:
            score += 0.12
            reasons.append("pixel_ocr_bounds_agree")
        else:
            score -= 0.08
            reasons.append("pixel_ocr_bounds_diverge")
    if int(row_count) >= 2:
        score += 0.06
        reasons.append("ocr_rows_present")
    if isinstance(primary_action_button_bounds, list) and len(primary_action_button_bounds) == 4:
        score += 0.12
        reasons.append("primary_action_candidate_present")

    confidence = max(0.0, min(1.0, round(score, 2)))
    if confidence >= 0.68:
        level = "high"
    elif confidence >= 0.46:
        level = "medium"
    else:
        level = "low"
    suppressed = level == "low"
    if suppressed:
        reasons.append("dialog_low_confidence")
    return {
        "confidence": confidence,
        "level": level,
        "suppressed": suppressed,
        "reasons": reasons,
        "touches": touches,
        "areaRatio": round(area_ratio, 3),
    }


def _derive_primary_action_button_bounds(
    *,
    image_path: str,
    capture_bounds: List[int] | None,
    action_zone_bounds: List[int] | None,
) -> List[int] | None:
    cropped_action_path, temp_action_path = crop_capture_image_to_bounds(
        image_path=image_path,
        capture_bounds=capture_bounds,
        target_bounds=action_zone_bounds,
    )
    if not cropped_action_path or not isinstance(action_zone_bounds, list) or len(action_zone_bounds) != 4:
        return None
    try:
        with Image.open(cropped_action_path).convert("RGB") as source:
            width, height = source.size
            if width < 40 or height < 20:
                return None
            samples = []
            sample_points = [
                (0, 0),
                (width - 1, 0),
                (0, height - 1),
                (width - 1, height - 1),
                (max(0, width // 8), max(0, height // 8)),
                (max(0, width - width // 8 - 1), max(0, height // 8)),
            ]
            for x, y in sample_points:
                samples.append(source.getpixel((int(x), int(y))))
            bg_r = int(sum(pixel[0] for pixel in samples) / max(1, len(samples)))
            bg_g = int(sum(pixel[1] for pixel in samples) / max(1, len(samples)))
            bg_b = int(sum(pixel[2] for pixel in samples) / max(1, len(samples)))
            min_x = width
            min_y = height
            max_x = -1
            max_y = -1
            for y in range(height):
                for x in range(width):
                    r, g, b = source.getpixel((x, y))
                    distance = abs(int(r) - bg_r) + abs(int(g) - bg_g) + abs(int(b) - bg_b)
                    saturation = max(int(r), int(g), int(b)) - min(int(r), int(g), int(b))
                    if distance >= 70 and (saturation >= 18 or max(int(r), int(g), int(b)) >= max(bg_r, bg_g, bg_b) + 18):
                        min_x = min(min_x, x)
                        min_y = min(min_y, y)
                        max_x = max(max_x, x)
                        max_y = max(max_y, y)
            if max_x <= min_x or max_y <= min_y:
                return None
            if (max_x - min_x) < int(width * 0.18) or (max_y - min_y) < int(height * 0.18):
                return None
            action_left, action_top, _, _ = [int(item) for item in action_zone_bounds]
            return [
                int(action_left + min_x),
                int(action_top + min_y),
                int(action_left + max_x + 1),
                int(action_top + max_y + 1),
            ]
    finally:
        if temp_action_path:
            Path(temp_action_path).unlink(missing_ok=True)
    return None


def observe_centered_dialog_scope(
    *,
    visual_locator_runtime: Any,
    capture_image_path: str | None,
    capture_bounds: List[int] | None,
) -> Tuple[Dict[str, Any], List[str]]:
    observation: Dict[str, Any] = {
        "status": "unavailable",
        "seedStrategy": "centered_dialog",
        "seedBounds": None,
        "dialogBounds": None,
        "ocrObservation": None,
    }
    temp_paths: List[str] = []
    if not capture_image_path or not isinstance(capture_bounds, list) or len(capture_bounds) != 4:
        observation["status"] = "missing_capture"
        return observation, temp_paths

    seed_bounds = derive_centered_dialog_seed_bounds(capture_bounds)
    observation["seedBounds"] = list(seed_bounds or []) if isinstance(seed_bounds, list) else None
    if not isinstance(seed_bounds, list) or len(seed_bounds) != 4:
        observation["status"] = "seed_unresolved"
        return observation, temp_paths

    cropped_seed_path, temp_seed_path = crop_capture_image_to_bounds(
        image_path=capture_image_path,
        capture_bounds=capture_bounds,
        target_bounds=seed_bounds,
    )
    if temp_seed_path:
        temp_paths.append(temp_seed_path)
    search_image_path = cropped_seed_path or capture_image_path
    if not search_image_path or not Path(search_image_path).exists():
        observation["status"] = "seed_crop_missing"
        return observation, temp_paths

    try:
        observed = dict(
            visual_locator_runtime.observe_text(
                search_image_path=search_image_path,
            )
            or {}
        )
    except Exception as exc:
        observation["status"] = "observe_failed"
        observation["error"] = f"{exc.__class__.__name__}: {exc}"
        return observation, temp_paths

    rows = [dict(item) for item in list(observed.get("rows") or []) if isinstance(item, dict)]
    pixel_local_bounds = _derive_dialog_bounds_from_pixels(
        image_path=str(search_image_path),
    )
    aggregated_local_bounds = _derive_dialog_bounds_from_rows(
        rows=rows,
        image_path=str(search_image_path),
    )
    suggested_local_bounds = list(observed.get("suggestedDialogBounds") or [])
    dialog_local_bounds = (
        pixel_local_bounds
        or aggregated_local_bounds
        or (suggested_local_bounds if len(suggested_local_bounds) == 4 else None)
    )
    dialog_bounds = _offset_bounds(dialog_local_bounds, seed_bounds)
    structural_zones = _derive_dialog_structural_zones(dialog_bounds)
    primary_action_button_bounds = _derive_primary_action_button_bounds(
        image_path=capture_image_path,
        capture_bounds=capture_bounds,
        action_zone_bounds=structural_zones.get("primaryActionZoneBounds") or structural_zones.get("actionZoneBounds"),
    )
    confidence_payload = _score_dialog_observation(
        seed_bounds=seed_bounds,
        dialog_bounds=dialog_bounds,
        pixel_local_bounds=pixel_local_bounds,
        aggregated_local_bounds=aggregated_local_bounds,
        row_count=int(observed.get("rowCount") or len(list(observed.get("rows") or []))),
        primary_action_button_bounds=primary_action_button_bounds,
    )
    action_pair_resolution = None
    if bool(confidence_payload.get("suppressed")):
        action_pair_resolution = _derive_dialog_from_action_candidates(
            visual_locator_runtime=visual_locator_runtime,
            capture_image_path=capture_image_path,
            capture_bounds=capture_bounds,
            seed_bounds=seed_bounds,
        )
        if isinstance(action_pair_resolution, dict):
            dialog_bounds = list(action_pair_resolution.get("dialogBounds") or []) if isinstance(action_pair_resolution.get("dialogBounds"), list) else None
            structural_zones = {
                "titleZoneBounds": action_pair_resolution.get("titleZoneBounds"),
                "contentZoneBounds": action_pair_resolution.get("contentZoneBounds"),
                "actionZoneBounds": action_pair_resolution.get("actionZoneBounds"),
                "primaryActionZoneBounds": action_pair_resolution.get("primaryActionZoneBounds"),
            }
            primary_action_button_bounds = list(action_pair_resolution.get("primaryActionButtonBounds") or []) if isinstance(action_pair_resolution.get("primaryActionButtonBounds"), list) else None
            confidence_payload = {
                "confidence": float(action_pair_resolution.get("confidence") or 0.72),
                "level": str(action_pair_resolution.get("confidenceLevel") or "high"),
                "suppressed": False,
                "reasons": list(action_pair_resolution.get("reasons") or []),
            }
    if bool(confidence_payload.get("suppressed")):
        dialog_bounds = None
        structural_zones = _derive_dialog_structural_zones(None)
        primary_action_button_bounds = None
    observation.update(
        {
            "status": "observed" if not bool(confidence_payload.get("suppressed")) else "observed_low_confidence",
            "ocrObservation": {
                "text": str(observed.get("text") or ""),
                "rowCount": int(observed.get("rowCount") or len(list(observed.get("rows") or []))),
                "searchMode": str(observed.get("searchMode") or "").strip() or None,
                "pixelDialogBoundsLocal": list(pixel_local_bounds or []) if isinstance(pixel_local_bounds, list) else None,
                "aggregatedDialogBoundsLocal": list(aggregated_local_bounds or []) if isinstance(aggregated_local_bounds, list) else None,
                "suggestedDialogBoundsLocal": suggested_local_bounds if len(suggested_local_bounds) == 4 else None,
                "actionPairDialogBounds": list(action_pair_resolution.get("dialogBounds") or []) if isinstance(action_pair_resolution, dict) and isinstance(action_pair_resolution.get("dialogBounds"), list) else None,
            },
            "dialogBounds": dialog_bounds,
            "dialogConfidence": float(confidence_payload.get("confidence") or 0.0),
            "dialogConfidenceLevel": str(confidence_payload.get("level") or "low"),
            "dialogSuppressed": bool(confidence_payload.get("suppressed")),
            "dialogSuppressionReasons": list(confidence_payload.get("reasons") or []),
            "titleZoneBounds": structural_zones.get("titleZoneBounds"),
            "contentZoneBounds": structural_zones.get("contentZoneBounds"),
            "actionZoneBounds": structural_zones.get("actionZoneBounds"),
            "primaryActionZoneBounds": structural_zones.get("primaryActionZoneBounds"),
            "primaryActionButtonBounds": primary_action_button_bounds,
            "sourceImagePath": str(search_image_path),
        }
    )
    return observation, temp_paths
