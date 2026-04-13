from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


_URL_PREFIX_RE = re.compile(r"^(https?://|file://|www\.)", re.IGNORECASE)
_WINDOWS_PATH_RE = re.compile(r"^[a-zA-Z]:[\\/]")
_ASCII_CODELIKE_RE = re.compile(r"^[\x20-\x7E]+$")
_WORKSPACE_RELATIVE_RE = re.compile(r"^(downloaded_media/|workspace/|src/|dist/|build/)", re.IGNORECASE)


def _string_tokens(values: Iterable[Any]) -> List[str]:
    normalized: List[str] = []
    for item in values:
        token = str(item or "").strip()
        if token:
            normalized.append(token)
    return normalized


def looks_like_url(text: str | None) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return bool(_URL_PREFIX_RE.match(value)) or ("." in value and " " not in value and "/" in value)


def looks_like_windows_path(text: str | None) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return bool(_WINDOWS_PATH_RE.match(value))


def looks_like_workspace_relative_path(text: str | None) -> bool:
    value = str(text or "").strip().strip("`")
    if not value:
        return False
    return bool(_WORKSPACE_RELATIVE_RE.match(value))


def looks_like_ascii_codeish(text: str | None) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if not _ASCII_CODELIKE_RE.match(value):
        return False
    if looks_like_url(value) or looks_like_windows_path(value) or looks_like_workspace_relative_path(value):
        return True
    punctuation_score = sum(1 for char in value if char in r"/\:_-.{}[]()=+@#%&*")
    return punctuation_score >= max(2, len(value) // 12)


def classify_target_input_kind(
    *,
    action_payload: Dict[str, Any],
    text: str | None,
    browser_lane_active: bool,
    browser_family: str | None = None,
) -> str:
    selector_key = str(action_payload.get("selector_key") or "").strip().lower()
    profile_action = str(action_payload.get("profile_action") or "").strip().lower()
    control_type = str(action_payload.get("control_type") or "").strip().lower()
    has_files = bool(
        action_payload.get("file_path")
        or action_payload.get("file_paths")
        or action_payload.get("attachment_paths")
    )
    if has_files:
        return "file_receiver"
    if selector_key == "address_bar" or profile_action == "address_bar":
        return "browser_address_bar"
    if browser_lane_active and browser_family:
        return "browser_dom_input"
    value = str(text or "").strip()
    if looks_like_url(value):
        return "url"
    if looks_like_windows_path(value) or looks_like_workspace_relative_path(value):
        return "path"
    if looks_like_ascii_codeish(value):
        return "ascii_code_like"
    if control_type in {"edit", "document", "combobox"}:
        return "text_edit"
    return "generic_text"


def deterministic_input_normalization_required(target_input_kind: str) -> bool:
    return target_input_kind in {
        "browser_address_bar",
        "browser_dom_input",
        "url",
        "path",
        "ascii_code_like",
        "file_receiver",
    }
