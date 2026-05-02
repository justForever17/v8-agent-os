from __future__ import annotations

from typing import Any

from api.models import ExternalToolSpec
from runtimes.network_supervisor.anthropic_compat import build_anthropic_message_response
from runtimes.network_supervisor.openai_compat import build_openai_completion_response


class CompatWireEmitter:
    """Protocol-specific response adapter for external compat clients.

    Ingress filtering and ChatRuntime execution stay separate. This emitter is
    the single place that turns V8 runtime events back into OpenAI/Anthropic
    wire payloads for non-streaming routes; streaming routes can use the same
    contract as they are gradually thinned.
    """

    def openai_chat_completion(
        self,
        *,
        response_id: str,
        model_name: str,
        events: list[dict[str, Any]],
        external_tools: list[ExternalToolSpec] | None = None,
    ) -> dict[str, Any]:
        return build_openai_completion_response(
            response_id=response_id,
            model_name=model_name,
            events=events,
            external_tools=external_tools,
        )

    def anthropic_message(
        self,
        *,
        response_id: str,
        model_name: str,
        events: list[dict[str, Any]],
        external_tools: list[ExternalToolSpec] | None = None,
        include_thinking: bool = False,
    ) -> dict[str, Any]:
        return build_anthropic_message_response(
            response_id=response_id,
            model_name=model_name,
            events=events,
            external_tools=external_tools,
            include_thinking=include_thinking,
        )


compat_wire_emitter = CompatWireEmitter()
