from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image


def split_locator_candidates(locator: str | None) -> list[str]:
    token = str(locator or "").strip()
    if not token:
        return []
    return [item.strip() for item in token.split("||") if item.strip()]


def derive_centered_dialog_seed_bounds(capture_bounds: list[int] | None) -> list[int] | None:
    if not isinstance(capture_bounds, list) or len(capture_bounds) != 4:
        return None
    left, top, right, bottom = [int(v) for v in capture_bounds]
    width = max(1, right - left)
    height = max(1, bottom - top)
    return [
        int(left + round(width * 0.20)),
        int(top + round(height * 0.10)),
        int(right - round(width * 0.20)),
        int(bottom - round(height * 0.04)),
    ]


def expand_scope_bounds(
    *,
    match: dict,
    capture_bounds: list[int] | None,
    scope_padding: list[int] | None = None,
) -> list[int] | None:
    bbox = list(match.get("bbox") or [])
    if len(bbox) != 4:
        return None
    left, top, right, bottom = [int(v) for v in bbox]
    width = max(1, right - left)
    height = max(1, bottom - top)
    if isinstance(scope_padding, list) and len(scope_padding) == 4:
        padding = [int(v) for v in scope_padding]
    else:
        padding = [
            max(48, int(width * 1.2)),
            max(40, int(height * 2.2)),
            max(48, int(width * 1.2)),
            max(220, int(height * 14.0)),
        ]
    expanded = [
        left - padding[0],
        top - padding[1],
        right + padding[2],
        bottom + padding[3],
    ]
    if isinstance(capture_bounds, list) and len(capture_bounds) == 4:
        capture_left, capture_top, capture_right, capture_bottom = [int(v) for v in capture_bounds]
        expanded = [
            max(capture_left, expanded[0]),
            max(capture_top, expanded[1]),
            min(capture_right, expanded[2]),
            min(capture_bottom, expanded[3]),
        ]
    if expanded[2] <= expanded[0] or expanded[3] <= expanded[1]:
        return None
    return expanded


def crop_capture_image_to_bounds(
    *,
    image_path: str | None,
    capture_bounds: list[int] | None,
    target_bounds: list[int] | None,
) -> tuple[str | None, str | None]:
    if (
        not image_path
        or not isinstance(capture_bounds, list)
        or len(capture_bounds) != 4
        or not isinstance(target_bounds, list)
        or len(target_bounds) != 4
    ):
        return None, None
    source_path = Path(str(image_path)).expanduser()
    if not source_path.exists():
        return None, None
    capture_left, capture_top, capture_right, capture_bottom = [int(v) for v in capture_bounds]
    target_left, target_top, target_right, target_bottom = [int(v) for v in target_bounds]
    local_left = max(0, target_left - capture_left)
    local_top = max(0, target_top - capture_top)
    local_right = min(max(1, capture_right - capture_left), target_right - capture_left)
    local_bottom = min(max(1, capture_bottom - capture_top), target_bottom - capture_top)
    if local_right <= local_left or local_bottom <= local_top:
        return None, None
    fd, temp_name = tempfile.mkstemp(prefix="v8chat-visual-scope-", suffix=".png")
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with Image.open(source_path) as image:
            cropped = image.crop((local_left, local_top, local_right, local_bottom))
            cropped.save(temp_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        return None, None
    return str(temp_path), str(temp_path)
