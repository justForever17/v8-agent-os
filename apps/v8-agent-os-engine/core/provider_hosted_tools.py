"""Governed model-level access to provider-hosted tools.

Provider-hosted tools execute inside the upstream Responses service rather
than V8OS. They stay disabled unless the user explicitly enables an exact,
locally allowlisted profile on the model binding.

Only hosted web search is admitted initially. Other Responses tools require
additional configuration or a V8OS lifecycle (vector stores, containers,
computer-use loops, MCP credentials, or artifact handling) and must not be
silently activated by a generic switch.
"""

from __future__ import annotations

from typing import Any, Mapping


OPENAI_RESPONSES_PROTOCOL = "openai.responses"
DEFAULT_PROVIDER_HOSTED_TOOLS: tuple[str, ...] = ("web_search",)
SUPPORTED_PROVIDER_HOSTED_TOOLS = frozenset(DEFAULT_PROVIDER_HOSTED_TOOLS)


def normalize_provider_hosted_tools(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    requested = raw.get("tools")
    if isinstance(requested, list):
        tools = [
            str(item).strip()
            for item in requested
            if str(item).strip() in SUPPORTED_PROVIDER_HOSTED_TOOLS
        ]
    else:
        tools = list(DEFAULT_PROVIDER_HOSTED_TOOLS)
    tools = list(dict.fromkeys(tools)) or list(DEFAULT_PROVIDER_HOSTED_TOOLS)
    enabled = bool(raw.get("enabled", False))
    return {
        "enabled": enabled,
        "tools": tools,
        "source": "manual"
        if enabled or str(raw.get("source") or "").strip() == "manual"
        else "default_disabled",
    }


def provider_hosted_tool_schemas(
    *,
    wire_protocol: Any,
    config: Any,
) -> list[dict[str, Any]]:
    normalized = normalize_provider_hosted_tools(config)
    if str(wire_protocol or "").strip() != OPENAI_RESPONSES_PROTOCOL:
        return []
    if not normalized["enabled"]:
        return []
    return [{"type": tool_type} for tool_type in normalized["tools"]]


__all__ = [
    "DEFAULT_PROVIDER_HOSTED_TOOLS",
    "OPENAI_RESPONSES_PROTOCOL",
    "SUPPORTED_PROVIDER_HOSTED_TOOLS",
    "normalize_provider_hosted_tools",
    "provider_hosted_tool_schemas",
]
