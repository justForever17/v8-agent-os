from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _normalize_bounds(value: Any) -> List[int]:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return [int(value[0]), int(value[1]), int(value[2]), int(value[3])]
        except Exception:
            return []
    if isinstance(value, dict):
        candidate = value.get("bounds")
        if isinstance(candidate, (list, tuple)) and len(candidate) == 4:
            try:
                return [int(candidate[0]), int(candidate[1]), int(candidate[2]), int(candidate[3])]
            except Exception:
                return []
    return []


def _center(bounds: Iterable[int]) -> List[float]:
    left, top, right, bottom = [int(item) for item in bounds]
    return [left + max(1, right - left) / 2.0, top + max(1, bottom - top) / 2.0]


def _relative_rect(bounds: List[int], container: List[int]) -> List[float]:
    left, top, right, bottom = bounds
    c_left, c_top, c_right, c_bottom = container
    width = max(1, c_right - c_left)
    height = max(1, c_bottom - c_top)
    return [
        round((left - c_left) / width, 4),
        round((top - c_top) / height, 4),
        round((right - c_left) / width, 4),
        round((bottom - c_top) / height, 4),
    ]


def _relative_point(point: List[float], container: List[int]) -> List[float]:
    c_left, c_top, c_right, c_bottom = container
    width = max(1, c_right - c_left)
    height = max(1, c_bottom - c_top)
    return [
        round((point[0] - c_left) / width, 4),
        round((point[1] - c_top) / height, 4),
    ]


def _absolute_point(relative_point: List[float], container: List[int]) -> List[int]:
    c_left, c_top, c_right, c_bottom = container
    width = max(1, c_right - c_left)
    height = max(1, c_bottom - c_top)
    return [
        int(round(c_left + float(relative_point[0]) * width)),
        int(round(c_top + float(relative_point[1]) * height)),
    ]


def _bounds_size(bounds: List[int]) -> tuple[int, int]:
    if not bounds:
        return (0, 0)
    left, top, right, bottom = bounds
    return (max(1, int(right) - int(left)), max(1, int(bottom) - int(top)))


def _relative_size_delta(previous: List[int], current: List[int]) -> float:
    previous_width, previous_height = _bounds_size(previous)
    current_width, current_height = _bounds_size(current)
    if previous_width <= 0 or previous_height <= 0:
        return 0.0
    width_delta = abs(current_width - previous_width) / float(previous_width)
    height_delta = abs(current_height - previous_height) / float(previous_height)
    return max(width_delta, height_delta)


def _normalize_relative_point(value: Any) -> List[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        x = min(1.0, max(0.0, float(value[0])))
        y = min(1.0, max(0.0, float(value[1])))
    except Exception:
        return None
    return [round(x, 4), round(y, 4)]


def _normalize_relative_rect(value: Any) -> List[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        left = min(1.0, max(0.0, float(value[0])))
        top = min(1.0, max(0.0, float(value[1])))
        right = min(1.0, max(0.0, float(value[2])))
        bottom = min(1.0, max(0.0, float(value[3])))
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return [round(left, 4), round(top, 4), round(right, 4), round(bottom, 4)]


def center_relative_rect(relative_rect: List[float] | None) -> List[float] | None:
    rect = _normalize_relative_rect(relative_rect)
    if rect is None:
        return None
    left, top, right, bottom = rect
    return [round((left + right) / 2.0, 4), round((top + bottom) / 2.0, 4)]


def offset_relative_point(relative_point: List[float] | None, bias: List[float] | None) -> List[float] | None:
    normalized_point = _normalize_relative_point(relative_point)
    if normalized_point is None:
        return relative_point
    normalized_bias = _normalize_relative_point(bias)
    if normalized_bias is None and not (
        isinstance(bias, (list, tuple))
        and len(bias) == 2
    ):
        return normalized_point
    try:
        x = min(1.0, max(0.0, float(normalized_point[0]) + float(bias[0])))
        y = min(1.0, max(0.0, float(normalized_point[1]) + float(bias[1])))
    except Exception:
        return normalized_point
    return [round(x, 4), round(y, 4)]


def build_relative_point_candidates(
    *,
    suggested_point: List[float] | None = None,
    point_rect: List[float] | None = None,
    point_bias: List[float] | None = None,
    point_biases: List[List[float]] | None = None,
    center_only: bool = False,
) -> List[List[float]]:
    base_points: List[List[float]] = []
    rect_center = center_relative_rect(point_rect)
    if rect_center is not None:
        base_points.append(rect_center)
    normalized_point = _normalize_relative_point(suggested_point)
    if normalized_point is not None:
        base_points.append(normalized_point)
    if not base_points:
        return []
    if bool(center_only):
        return [list(base_points[0])]

    biases: List[List[float] | None] = [None]
    if isinstance(point_bias, (list, tuple)) and len(point_bias) == 2:
        biases.append([float(point_bias[0]), float(point_bias[1])])
    for item in point_biases or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                biases.append([float(item[0]), float(item[1])])
            except Exception:
                continue

    candidates: List[List[float]] = []
    seen: set[tuple[float, float]] = set()
    for base_point in base_points:
        for bias in biases:
            candidate = offset_relative_point(base_point, bias) if bias is not None else list(base_point)
            normalized_candidate = _normalize_relative_point(candidate)
            if normalized_candidate is None:
                continue
            key = (normalized_candidate[0], normalized_candidate[1])
            if key in seen:
                continue
            seen.add(key)
            candidates.append(normalized_candidate)
    return candidates


def _center_from_relative_rect(relative_rect: List[float], container: List[int]) -> List[int]:
    left, top, right, bottom = relative_rect
    relative_center = [float(left + right) / 2.0, float(top + bottom) / 2.0]
    return _absolute_point(relative_center, container)


def build_spatial_anchor(
    *,
    target: Dict[str, Any] | None,
    observation: Dict[str, Any] | None,
) -> Dict[str, Any]:
    target_bounds = _normalize_bounds(target or {})
    if not target_bounds:
        return {}
    metadata = dict((observation or {}).get("metadata") or {}) if isinstance(observation, dict) else {}
    window_bounds = _normalize_bounds(metadata.get("windowBounds"))
    display_bounds = _normalize_bounds(metadata.get("displayBounds"))
    anchor: Dict[str, Any] = {}
    if window_bounds:
        anchor["windowRelativeRect"] = _relative_rect(target_bounds, window_bounds)
        anchor["windowBounds"] = list(window_bounds)
    if display_bounds:
        anchor["screenRelativePoint"] = _relative_point(_center(target_bounds), display_bounds)
        anchor["displayBounds"] = list(display_bounds)
    display_id = str(metadata.get("displayId") or "").strip()
    if display_id:
        anchor["displayId"] = display_id
    dpi_scale = metadata.get("dpiScale")
    if dpi_scale not in (None, ""):
        try:
            anchor["dpiScale"] = round(float(dpi_scale), 3)
        except Exception:
            pass
    anchor["anchorSource"] = "uia_observation" if observation else "action_target"
    return anchor


def spatial_anchor_compatibility(
    *,
    spatial_anchor: Dict[str, Any] | None,
    observation: Dict[str, Any] | None,
) -> Dict[str, Any]:
    anchor = dict(spatial_anchor or {})
    metadata = dict((observation or {}).get("metadata") or {}) if isinstance(observation, dict) else {}
    current_display_bounds = _normalize_bounds(metadata.get("displayBounds"))
    current_window_bounds = _normalize_bounds(metadata.get("windowBounds"))
    anchor_display_bounds = _normalize_bounds(anchor.get("displayBounds"))
    anchor_window_bounds = _normalize_bounds(anchor.get("windowBounds"))
    reasons: List[str] = []
    warnings: List[str] = []
    penalty = 0

    if anchor_display_bounds and current_display_bounds and anchor_display_bounds != current_display_bounds:
        reasons.append("display_bounds_changed")
        penalty += 40
    if anchor_window_bounds and current_window_bounds:
        delta = _relative_size_delta(anchor_window_bounds, current_window_bounds)
        if delta > 0.10:
            warnings.append("window_bounds_size_changed")
            penalty += 20
    anchor_dpi = anchor.get("dpiScale")
    current_dpi = metadata.get("dpiScale")
    if anchor_dpi not in (None, "") and current_dpi not in (None, ""):
        try:
            if abs(float(anchor_dpi) - float(current_dpi)) > 0.05:
                reasons.append("dpi_scale_changed")
                penalty += 35
        except Exception:
            warnings.append("dpi_scale_unparseable")
            penalty += 10

    return {
        "compatible": not reasons,
        "penalty": penalty,
        "reasons": reasons,
        "warnings": warnings,
        "current": {
            "displayBounds": current_display_bounds or None,
            "windowBounds": current_window_bounds or None,
            "dpiScale": current_dpi if current_dpi not in (None, "") else None,
        },
        "anchor": {
            "displayBounds": anchor_display_bounds or None,
            "windowBounds": anchor_window_bounds or None,
            "dpiScale": anchor_dpi if anchor_dpi not in (None, "") else None,
        },
    }


def resolve_absolute_click_point(
    *,
    suggested_point: List[float] | None,
    spatial_anchor: Dict[str, Any] | None,
    observation: Dict[str, Any] | None,
) -> List[int] | None:
    metadata = dict((observation or {}).get("metadata") or {}) if isinstance(observation, dict) else {}
    window_bounds = _normalize_bounds(metadata.get("windowBounds"))
    display_bounds = _normalize_bounds(metadata.get("displayBounds"))
    normalized_point = None
    if isinstance(suggested_point, (list, tuple)) and len(suggested_point) == 2:
        try:
            normalized_point = [float(suggested_point[0]), float(suggested_point[1])]
        except Exception:
            normalized_point = None
    anchor = dict(spatial_anchor or {})
    if normalized_point and window_bounds:
        return _absolute_point(normalized_point, window_bounds)
    if normalized_point and display_bounds:
        return _absolute_point(normalized_point, display_bounds)
    relative_rect = anchor.get("windowRelativeRect")
    compatibility = spatial_anchor_compatibility(spatial_anchor=anchor, observation=observation)
    incompatible = not bool(compatibility.get("compatible"))
    if isinstance(relative_rect, (list, tuple)) and len(relative_rect) == 4 and window_bounds and not incompatible:
        try:
            return _center_from_relative_rect([float(item) for item in relative_rect], window_bounds)
        except Exception:
            pass
    screen_relative_point = anchor.get("screenRelativePoint")
    if isinstance(screen_relative_point, (list, tuple)) and len(screen_relative_point) == 2 and display_bounds and not incompatible:
        try:
            return _absolute_point([float(screen_relative_point[0]), float(screen_relative_point[1])], display_bounds)
        except Exception:
            pass
    return None
