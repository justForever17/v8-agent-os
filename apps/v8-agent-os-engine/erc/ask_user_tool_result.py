from __future__ import annotations

from typing import Any


def resolve_ask_user_tool_result_interaction(
    interactions: list[dict[str, Any]],
    *,
    pending_interaction_id: str | None = None,
    candidate_tool_call_id: str | None = None,
    output_text: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a resumed ask_user tool result back to its original tool call.

    LangGraph resume events may surface a transient run id as the tool_call_id.
    The stored ask_user interaction is the authoritative bridge back to the
    original model-issued tool call.
    """

    if not interactions:
        return None

    normalized_pending_id = str(pending_interaction_id or "").strip()
    if normalized_pending_id:
        for interaction in interactions:
            if str(interaction.get("id") or "").strip() == normalized_pending_id:
                return interaction

    normalized_output = str(output_text or "").strip()
    if normalized_output:
        for interaction in interactions:
            if str(interaction.get("answer_text") or "").strip() == normalized_output:
                return interaction

    normalized_candidate = str(candidate_tool_call_id or "").strip()
    if normalized_candidate:
        for interaction in interactions:
            if str(interaction.get("tool_call_id") or "").strip() == normalized_candidate:
                return interaction

    return interactions[0]
