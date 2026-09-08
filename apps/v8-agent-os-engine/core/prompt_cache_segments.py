from __future__ import annotations

import hashlib
from typing import Any, Iterable


PROMPT_CACHE_SEGMENT_TYPES = {"stable_static", "scoped_static", "dynamic", "unsafe"}


def static_prompt_parts_first(parts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable partition of complete blocks; callers must not split tag boundaries."""
    parts = list(parts)
    reusable = {"stable_static", "scoped_static"}
    return ([part for part in parts if part.get("type") in reusable]
            + [part for part in parts if part.get("type") not in reusable])


def split_environment_prompt_parts(text: str, *, source_prefix: str) -> list[dict[str, str]]:
    """Keep the environment wrapper intact; volatile measurements have their own block."""
    prefixes = {"Current Time:": "current_time", "Host Load:": "host_load", "Host Alerts:": "host_alerts"}
    stable: list[str] = []
    dynamic: list[dict[str, str]] = []
    for line in str(text or "").splitlines(keepends=True):
        name = next((name for prefix, name in prefixes.items() if line.strip().startswith(prefix)), "")
        if name:
            dynamic.append({"source": f"{source_prefix}.{name}", "type": "dynamic", "text": line, "scope": "environment"})
        else:
            stable.append(line)
    parts = ([{"source": f"{source_prefix}.static", "type": "scoped_static", "text": "".join(stable), "scope": "environment"}] if stable else [])
    if dynamic:
        parts.extend([
            {"source": f"{source_prefix}.updates_open", "type": "dynamic", "text": "\n<environment_updates>\n", "scope": "environment"},
            *dynamic,
            {"source": f"{source_prefix}.updates_close", "type": "dynamic", "text": "\n</environment_updates>\n", "scope": "environment"},
        ])
    return parts


def hash_prompt_segment(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_prompt_segments_from_parts(parts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    offset = 0
    for index, part in enumerate(parts):
        text = str(part.get("text") or "")
        segment_type = str(part.get("type") or "dynamic").strip()
        if segment_type not in PROMPT_CACHE_SEGMENT_TYPES:
            segment_type = "dynamic"
        start_offset = offset
        end_offset = start_offset + len(text)
        offset = end_offset
        if not text:
            continue
        source = str(part.get("source") or f"part:{index}")
        segments.append(
            {
                "type": segment_type,
                "source": source,
                "scope": str(part.get("scope") or ""),
                "hash": hash_prompt_segment(text),
                "charCount": len(text),
                "estimatedTokens": max(1, len(text) // 4),
                "startOffset": start_offset,
                "endOffset": end_offset,
            }
        )
    return segments
