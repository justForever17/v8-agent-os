from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


CONTEXT_POLICY_SCHEMA_VERSION = 2

DEFAULT_CONTEXT_POLICY: Dict[str, Any] = {
    "schema_version": CONTEXT_POLICY_SCHEMA_VERSION,
    "recursion_limit": 100,
    "compression": {
        "enabled": True,
        "default_context_window_tokens": 32000,
        "soft_trigger_ratio": 0.55,
        "hard_trigger_ratio": 0.75,
        "keep_recent_messages": 6,
        "use_llm_summary": False,
        "max_summary_input_tokens": 5000,
        "max_summary_input_messages": 60,
        "max_summary_output_tokens": 800,
    },
    "runtime_adapters": {
        "plugin_host": {
            "window_size": 15,
            "max_summary_items": 8,
        },
        "automation": {
            "recent_run_limit": 3,
            "job_memory_limit": 6,
        },
    },
}


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    return max(minimum, min(normalized, maximum))


def _coerce_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        normalized = default
    return max(minimum, min(normalized, maximum))


def normalize_context_policy(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    data = deepcopy(raw or {})
    compression = dict(data.get("compression") or {})
    runtime_adapters = dict(data.get("runtime_adapters") or {})
    plugin_host_adapter = dict(runtime_adapters.get("plugin_host") or runtime_adapters.get("channel") or {})
    automation_adapter = dict(runtime_adapters.get("automation") or {})

    keep_recent_default = compression.get("keep_recent_messages", compression.get("keep_recent", 6))
    max_window_default = compression.get("default_context_window_tokens", 32000)

    normalized: Dict[str, Any] = {
        "schema_version": CONTEXT_POLICY_SCHEMA_VERSION,
        "recursion_limit": _coerce_int(data.get("recursion_limit", 100), 100, minimum=10, maximum=5000),
        "compression": {
            "enabled": bool(compression.get("enabled", True)),
            "default_context_window_tokens": _coerce_int(
                max_window_default,
                32000,
                minimum=2048,
                maximum=2_000_000,
            ),
            "soft_trigger_ratio": _coerce_float(
                compression.get("soft_trigger_ratio", 0.55),
                0.55,
                minimum=0.10,
                maximum=0.95,
            ),
            "hard_trigger_ratio": _coerce_float(
                compression.get("hard_trigger_ratio", 0.75),
                0.75,
                minimum=0.15,
                maximum=0.99,
            ),
            "keep_recent_messages": _coerce_int(
                keep_recent_default,
                6,
                minimum=1,
                maximum=100,
            ),
            "use_llm_summary": bool(compression.get("use_llm_summary", False)),
            "max_summary_input_tokens": _coerce_int(
                compression.get("max_summary_input_tokens", 5000),
                5000,
                minimum=512,
                maximum=200_000,
            ),
            "max_summary_input_messages": _coerce_int(
                compression.get("max_summary_input_messages", 60),
                60,
                minimum=5,
                maximum=200,
            ),
            "max_summary_output_tokens": _coerce_int(
                compression.get("max_summary_output_tokens", 800),
                800,
                minimum=128,
                maximum=8000,
            ),
        },
        "runtime_adapters": {
            "plugin_host": {
                "window_size": _coerce_int(
                    plugin_host_adapter.get("window_size", 15),
                    15,
                    minimum=3,
                    maximum=100,
                ),
                "max_summary_items": _coerce_int(
                    plugin_host_adapter.get("max_summary_items", 8),
                    8,
                    minimum=1,
                    maximum=50,
                ),
            },
            "automation": {
                "recent_run_limit": _coerce_int(
                    automation_adapter.get("recent_run_limit", 3),
                    3,
                    minimum=1,
                    maximum=20,
                ),
                "job_memory_limit": _coerce_int(
                    automation_adapter.get("job_memory_limit", 6),
                    6,
                    minimum=1,
                    maximum=50,
                ),
            },
        },
    }

    compression_out = normalized["compression"]
    if compression_out["hard_trigger_ratio"] <= compression_out["soft_trigger_ratio"]:
        compression_out["hard_trigger_ratio"] = min(
            0.99,
            round(compression_out["soft_trigger_ratio"] + 0.10, 2),
        )

    return normalized
