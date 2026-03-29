from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


def _flatten(values: Iterable[Any]) -> List[Any]:
    flattened: List[Any] = []
    for item in values:
        if isinstance(item, (list, tuple, set)):
            flattened.extend(_flatten(item))
        else:
            flattened.append(item)
    return flattened


def _normalize_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    return text if text else None


def _looks_like_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(
        re.match(r"^[A-Za-z]:[\\/]", text)
        or text.startswith(("\\\\", "/", "./", "../"))
    )


def normalize_file_paths(*values: Any) -> List[str]:
    candidates = _flatten(values)
    normalized: List[str] = []
    for item in candidates:
        text = str(item or "").strip()
        if not text or not _looks_like_path(text):
            continue
        if text not in normalized:
            normalized.append(text)
    return normalized


def normalize_clipboard_payload(
    *,
    payload: Dict[str, Any] | None = None,
    text: Any = None,
    file_path: Any = None,
    file_paths: Any = None,
    attachment_paths: Any = None,
) -> Dict[str, Any]:
    payload_dict = dict(payload or {})
    resolved_text = _normalize_text(
        text
        if text is not None
        else payload_dict.get("text")
    )
    resolved_file_paths = normalize_file_paths(
        file_path,
        file_paths,
        attachment_paths,
        payload_dict.get("file_path"),
        payload_dict.get("file_paths"),
        payload_dict.get("attachment_paths"),
        payload_dict.get("paths"),
    )
    if resolved_file_paths and resolved_text:
        mode = "files_and_text"
    elif resolved_file_paths:
        mode = "files"
    elif resolved_text is not None:
        mode = "text"
    else:
        mode = "empty"
    return {
        "text": resolved_text,
        "file_paths": resolved_file_paths,
        "mode": mode,
        "has_payload": bool(resolved_file_paths or resolved_text is not None),
    }
