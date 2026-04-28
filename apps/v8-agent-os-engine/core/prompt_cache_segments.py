from __future__ import annotations

import hashlib
from typing import Any, Iterable


PROMPT_CACHE_SEGMENT_TYPES = {"stable_static", "scoped_static", "dynamic", "unsafe"}


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
