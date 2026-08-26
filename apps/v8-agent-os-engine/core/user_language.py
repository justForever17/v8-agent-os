from __future__ import annotations

import re
from typing import Any


SUPPORTED_USER_LANGUAGES = {"en", "ja", "ko", "ru", "zh-CN"}


def infer_preferred_language(*values: Any, default: str = "en") -> str:
    """Infer the user-visible language from the first non-empty source."""

    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if re.search(r"[\u3040-\u30ff]", text):
            return "ja"
        if re.search(r"[\uac00-\ud7af]", text):
            return "ko"
        if re.search(r"[\u0400-\u04ff]", text):
            return "ru"
        if re.search(r"[\u4e00-\u9fff]", text):
            return "zh-CN"
        return "en"
    return default if default in SUPPORTED_USER_LANGUAGES else "en"


def normalize_preferred_language(value: Any, *, fallback: str = "") -> str:
    normalized = str(value or "").strip()
    if normalized.lower() in {"zh", "zh-cn", "cn", "chinese"}:
        return "zh-CN"
    if normalized in SUPPORTED_USER_LANGUAGES:
        return normalized
    return fallback if fallback in SUPPORTED_USER_LANGUAGES else ""
