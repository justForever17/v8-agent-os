from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from core.model_control_plane import model_control_plane

from .openai_compat import resolve_openai_compat_model_alias


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _derived_tokens(context_window: int, numerator: int, denominator: int = 1) -> int:
    if denominator <= 0:
        denominator = 1
    return max(1, int(context_window) * int(numerator) // int(denominator))


@dataclass(slots=True)
class CompatModelBudget:
    requested_alias: str
    execution_model_ref: str
    execution_model_id: str
    role: str
    context_window_tokens: int
    max_external_tools: int
    max_external_payload_tokens: int
    max_external_system_tokens: int
    max_external_message_tokens: int
    max_external_tool_description_tokens: int
    max_external_tool_schema_bytes: int
    max_external_tools_payload_tokens: int
    diagnostics: dict[str, Any]

    def as_diagnostics(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("diagnostics", None)
        payload.update(dict(self.diagnostics or {}))
        payload["contextWindowSource"] = "model_config"
        return payload


def _context_window_from_resolution(resolution: dict[str, Any], execution_model_ref: str) -> int:
    resolved_model = dict(resolution.get("resolvedModel") or {})
    context_window = _positive_int(resolved_model.get("contextWindow"))
    if context_window:
        return context_window

    # Compatibility fallback for older call sites that only hydrate metadata
    # through LLMFactory. It still reads model configuration metadata; it does
    # not introduce a route-local default context window.
    try:
        from core.llm_factory import LLMFactory

        meta = LLMFactory._resolve_model_metadata(execution_model_ref)  # noqa: SLF001 - compatibility metadata bridge
        context_window = _positive_int(meta.get("global_context_window"))
        if context_window:
            return context_window
    except Exception:
        pass
    raise ValueError(
        "missing_context_window: Network compat requires the configured execution model "
        f"'{execution_model_ref or resolution.get('resolvedModelId') or 'unknown'}' to define contextWindow"
    )


def resolve_compat_model_budget(
    requested_model: Any,
    *,
    aliases: list[str] | None = None,
    compat_config: Any | None = None,
    role: str = "supervisor",
) -> CompatModelBudget:
    requested_alias = resolve_openai_compat_model_alias(requested_model, aliases)
    resolution = model_control_plane.resolve_model_for_role(role)
    execution_model_ref = str(resolution.get("resolvedModelRef") or resolution.get("resolvedModelId") or "").strip()
    execution_model_id = str(resolution.get("resolvedModelId") or execution_model_ref).strip()
    if not execution_model_ref:
        raise ValueError(f"missing_context_window: no configured execution model for role '{role}'")

    context_window = _context_window_from_resolution(resolution, execution_model_ref)

    configured_tool_count = _positive_int(getattr(compat_config, "max_external_tools", None))
    derived_tool_count = max(1, context_window // 256)
    max_external_tools = min(configured_tool_count or derived_tool_count, derived_tool_count)

    # These budgets are derived from the configured model context window. The
    # compat config may lower them for diagnostics or deployment policy, but it
    # cannot raise them beyond the model's declared window.
    derived_payload = context_window
    derived_system = _derived_tokens(context_window, 1, 4)
    derived_messages = _derived_tokens(context_window, 3, 4)
    derived_tool_description = _derived_tokens(context_window, 1, 20)
    derived_tools_payload = _derived_tokens(context_window, 1, 2)
    derived_schema_bytes = max(1, context_window * 6)

    def _lower_configured(attr: str, derived: int) -> int:
        configured = _positive_int(getattr(compat_config, attr, None))
        return min(configured, derived) if configured else derived

    diagnostics = {
        "requestedWireAlias": requested_alias,
        "executionRole": role,
        "executionBindingState": resolution.get("bindingState"),
        "executionModelRef": execution_model_ref,
        "executionModelId": execution_model_id,
        "contextWindowTokens": context_window,
        "budgetPolicy": "derived_from_configured_context_window",
        "configMayOnlyLowerBudget": True,
    }

    return CompatModelBudget(
        requested_alias=requested_alias,
        execution_model_ref=execution_model_ref,
        execution_model_id=execution_model_id,
        role=role,
        context_window_tokens=context_window,
        max_external_tools=max_external_tools,
        max_external_payload_tokens=_lower_configured("max_external_tools_payload_tokens", derived_payload),
        max_external_system_tokens=_lower_configured("max_external_system_tokens", derived_system),
        max_external_message_tokens=_lower_configured("max_external_message_tokens", derived_messages),
        max_external_tool_description_tokens=_lower_configured("max_external_tool_description_tokens", derived_tool_description),
        max_external_tool_schema_bytes=_lower_configured("max_external_tool_schema_bytes", derived_schema_bytes),
        max_external_tools_payload_tokens=_lower_configured("max_external_tools_payload_tokens", derived_tools_payload),
        diagnostics=diagnostics,
    )
