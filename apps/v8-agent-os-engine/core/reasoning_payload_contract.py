from __future__ import annotations

import re
from typing import Any


REASONING_KEYS = (
    "reasoning_content",
    "reasoningContent",
    "reasoning",
    "thinking",
    "thinking_delta",
    "reasoning_details",
    "thought",
    "thoughts",
    "analysis",
    "deliberation",
)
REASONING_KEY_SET = frozenset(REASONING_KEYS)

SIGNATURE_KEYS = ("thoughtSignature", "thought_signature")
SIGNATURE_KEY_SET = frozenset(SIGNATURE_KEYS)

VISIBLE_TEXT_KEYS = ("text", "output_text", "content", "value")
TEXT_BLOCK_TYPES = frozenset({"text", "output_text", "plain_text"})
VISIBLE_BLOCK_TYPES = frozenset({*TEXT_BLOCK_TYPES, "message"})
REASONING_BLOCK_TYPES = frozenset({"reasoning", "reasoning_content", "thinking", "thought", "analysis", "deliberation"})

THINK_TAG_PATTERN = re.compile(r"<think\b[^>]*>[\s\S]*?</think>", re.IGNORECASE)


def is_reasoning_key(value: Any) -> bool:
    return str(value or "") in REASONING_KEY_SET


def is_signature_key(value: Any) -> bool:
    return str(value or "") in SIGNATURE_KEY_SET


def is_reasoning_block_type(value: Any) -> bool:
    return str(value or "").strip().lower() in REASONING_BLOCK_TYPES
