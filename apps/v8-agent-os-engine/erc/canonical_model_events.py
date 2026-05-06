from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from core.chat_output_extractor import extract_text_and_reasoning
from core.reasoning_surface_contract import evaluate_reasoning_payload, normalize_reasoning_surface


CanonicalModelEventType = Literal[
    "reasoning_delta",
    "reasoning_suppressed",
    "text_delta",
    "tool_call_start",
    "tool_call_delta",
    "tool_call_done",
    "tool_result",
    "message_done",
]

CanonicalModelEventScope = Literal["assistant_root", "tool_internal", "runtime_internal"]


@dataclass(slots=True)
class CanonicalModelEvent:
    event_type: CanonicalModelEventType
    run_id: str
    model_run_id: str
    scope: CanonicalModelEventScope
    delta: str = ""
    snapshot: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)


def normalized_model_run_id(model_run_id: str | None) -> str:
    return (model_run_id or "").strip() or "__default__"


def _lower_metadata_value(value: Any) -> str:
    return str(value or "").strip().lower()


def _metadata_path_contains_tool_node(value: Any) -> bool:
    if isinstance(value, (list, tuple)):
        return any(_metadata_path_contains_tool_node(item) for item in value)
    text = _lower_metadata_value(value)
    return bool(text and ("supervisor_tools" in text or text.endswith("_tools") or ":tools" in text))


def _metadata_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "trusted"}


_UNTRUSTED_REASONING_PROGRESS_PATTERNS = (
    "让我",
    "我先",
    "我现在",
    "现在开始",
    "接下来",
    "计划已",
    "工具",
    "调用",
    "检查",
    "读取",
    "搜索",
    "创建",
    "写入",
    "观察",
    "用户想",
    "用户要求",
    "let me",
    "i need to",
    "i will",
    "i'll",
    "now i",
    "next i",
    "calling",
    "call the",
    "use the tool",
    "check",
    "read",
    "search",
    "create",
    "write",
)


def _looks_like_untrusted_reasoning_progress(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if any(pattern in lowered for pattern in _UNTRUSTED_REASONING_PROGRESS_PATTERNS):
        return True
    # Very short fragments around tool boundaries are usually progress narration
    # from providers that misuse reasoning_content as a streaming text channel.
    return len(text) <= 24 and not any(marker in lowered for marker in ("because", "therefore", "所以", "因为", "推理"))


def longest_overlap_suffix_prefix(previous: str, current: str) -> int:
    max_overlap = min(len(previous), len(current))
    for size in range(max_overlap, 0, -1):
        if previous[-size:] == current[:size]:
            return size
    return 0


def consume_canonical_stream_value(
    snapshots: dict[str, str],
    model_run_id: str | None,
    raw_value: str,
    *,
    allow_token_delta: bool,
) -> tuple[str, str]:
    """Return (delta, full snapshot) from provider token or cumulative-snapshot streams.

    Text streams stay strict because non-monotonic text snapshots are often provider
    corrections. Reasoning streams allow token-delta fallback because several
    providers send reasoning as independent short fragments.
    """
    run_key = normalized_model_run_id(model_run_id)
    current_value = raw_value or ""
    if not current_value:
        return "", snapshots.get(run_key, "")

    previous_value = snapshots.get(run_key, "")
    if not previous_value:
        snapshots[run_key] = current_value
        return current_value, current_value

    if current_value == previous_value:
        return "", previous_value

    if current_value.startswith(previous_value):
        suffix = current_value[len(previous_value):]
        snapshots[run_key] = current_value
        return suffix, current_value

    if previous_value.endswith(current_value) or current_value in previous_value:
        return "", previous_value

    overlap = longest_overlap_suffix_prefix(previous_value, current_value)
    if overlap > 0:
        suffix = current_value[overlap:]
        snapshot = previous_value + suffix
        snapshots[run_key] = snapshot
        return suffix, snapshot

    if allow_token_delta:
        snapshot = previous_value + current_value
        snapshots[run_key] = snapshot
        return current_value, snapshot

    # Strict text streams treat non-monotonic values as provider correction
    # snapshots. Do not replace the active baseline here: the chat runtime may
    # still have short text deltas buffered for a later flush, and replacing the
    # baseline would make those buffered chunks patch the wrong canonical text.
    return "", previous_value


class LangChainCanonicalModelEventAdapter:
    """Normalize LangChain callback payloads before they can mutate transcript rows."""

    def scope_for_event(self, event: dict[str, Any]) -> CanonicalModelEventScope:
        metadata = event.get("metadata") if isinstance(event, dict) else None
        metadata = metadata if isinstance(metadata, dict) else {}
        tags = event.get("tags") if isinstance(event, dict) else None
        tags = tags if isinstance(tags, list) else []

        explicit_scope = str(
            metadata.get("v8_model_scope")
            or metadata.get("model_scope")
            or metadata.get("scope")
            or ""
        ).strip()
        if explicit_scope in {"assistant_root", "tool_internal", "runtime_internal"}:
            return explicit_scope  # type: ignore[return-value]

        lowered_tags = {str(tag or "").strip().lower() for tag in tags}
        if "tool_internal" in lowered_tags or "runtime_internal" in lowered_tags:
            return "tool_internal"

        if metadata.get("tool_name") or metadata.get("tool_call_id") or metadata.get("v8_tool_runtime"):
            return "tool_internal"

        langgraph_node = _lower_metadata_value(metadata.get("langgraph_node") or metadata.get("node"))
        if langgraph_node in {"supervisor_tools", "tools", "tool", "tool_node"} or langgraph_node.endswith("_tools"):
            return "tool_internal"

        if _metadata_path_contains_tool_node(metadata.get("langgraph_path")) or _metadata_path_contains_tool_node(
            metadata.get("checkpoint_ns")
        ):
            return "tool_internal"

        return "assistant_root"

    def _reasoning_payload_decision(self, event: dict[str, Any], payload: Any, reasoning_surface: dict[str, Any] | None) -> dict[str, Any]:
        surface = normalize_reasoning_surface(reasoning_surface)
        decision = evaluate_reasoning_payload(surface, payload)
        if decision.get("accepted"):
            return decision
        metadata = event.get("metadata") if isinstance(event, dict) else None
        metadata = metadata if isinstance(metadata, dict) else {}
        tags = event.get("tags") if isinstance(event, dict) else None
        tags = tags if isinstance(tags, list) else []
        if surface.get("mode") != "hidden" and (
            any(_metadata_truthy(metadata.get(key)) for key in ("v8_trusted_reasoning", "trusted_reasoning", "reasoning_trusted"))
            or {str(tag or "").strip().lower() for tag in tags}.intersection({"v8:trusted_reasoning", "trusted_reasoning", "reasoning:trusted"})
        ):
            return {
                **decision,
                "accepted": True,
                "matchedField": decision.get("matchedField") or "metadata.trusted_reasoning",
                "reasoningKind": {
                    "typed_thinking": "raw_thinking",
                    "reasoning_summary": "summary",
                    "provider_reasoning": "provider_reasoning",
                }.get(str(surface.get("mode") or ""), "hidden"),
            }
        return decision

    def normalize_chat_model_stream(
        self,
        event: dict[str, Any],
        *,
        text_snapshots: dict[str, str],
        reasoning_snapshots: dict[str, str],
        reasoning_surface: dict[str, Any] | None = None,
    ) -> list[CanonicalModelEvent]:
        return self._normalize_chat_model_payload(
            event,
            payload=(event.get("data") or {}).get("chunk") if isinstance(event.get("data"), dict) else None,
            text_snapshots=text_snapshots,
            reasoning_snapshots=reasoning_snapshots,
            terminal=False,
            suppress_reasoning=False,
            reasoning_surface=reasoning_surface,
        )

    def normalize_chat_model_end(
        self,
        event: dict[str, Any],
        *,
        text_snapshots: dict[str, str],
        reasoning_snapshots: dict[str, str],
        suppress_reasoning: bool = False,
        emitted_text: str = "",
        reasoning_surface: dict[str, Any] | None = None,
    ) -> list[CanonicalModelEvent]:
        return self._normalize_chat_model_payload(
            event,
            payload=(event.get("data") or {}).get("output") if isinstance(event.get("data"), dict) else None,
            text_snapshots=text_snapshots,
            reasoning_snapshots=reasoning_snapshots,
            terminal=True,
            suppress_reasoning=suppress_reasoning,
            emitted_text=emitted_text,
            reasoning_surface=reasoning_surface,
        )

    def _normalize_chat_model_payload(
        self,
        event: dict[str, Any],
        *,
        payload: Any,
        text_snapshots: dict[str, str],
        reasoning_snapshots: dict[str, str],
        terminal: bool,
        suppress_reasoning: bool,
        emitted_text: str = "",
        reasoning_surface: dict[str, Any] | None = None,
    ) -> list[CanonicalModelEvent]:
        scope = self.scope_for_event(event)
        run_id = str(event.get("parent_run_id") or event.get("run_id") or "").strip()
        model_run_id = str(event.get("run_id") or "").strip()
        if scope != "assistant_root":
            return []

        raw_text, raw_reasoning = extract_text_and_reasoning(payload)
        if not raw_text and isinstance(payload, str):
            raw_text = payload

        events: list[CanonicalModelEvent] = []
        if raw_text:
            if terminal:
                text_delta, text_snapshot, text_diagnostics = self._consume_terminal_text(
                    text_snapshots,
                    model_run_id,
                    raw_text,
                    emitted_text=emitted_text,
                )
            else:
                text_delta, text_snapshot = consume_canonical_stream_value(
                    text_snapshots,
                    model_run_id,
                    raw_text,
                    allow_token_delta=False,
                )
                text_diagnostics = {}
            if text_delta:
                events.append(
                    CanonicalModelEvent(
                        event_type="text_delta",
                        run_id=run_id,
                        model_run_id=model_run_id,
                        scope=scope,
                        delta=text_delta,
                        snapshot=text_snapshot,
                        diagnostics=text_diagnostics,
                    )
                )

        if terminal and raw_text:
            suppress_reasoning = True

        if raw_reasoning and not suppress_reasoning:
            reasoning_decision = self._reasoning_payload_decision(event, payload, reasoning_surface)
            if not reasoning_decision.get("accepted"):
                suppressed_delta, suppressed_snapshot = consume_canonical_stream_value(
                    reasoning_snapshots,
                    model_run_id,
                    raw_reasoning,
                    allow_token_delta=True,
                )
                if suppressed_delta:
                    events.append(
                        CanonicalModelEvent(
                            event_type="reasoning_suppressed",
                            run_id=run_id,
                            model_run_id=model_run_id,
                            scope=scope,
                            delta=suppressed_delta,
                            snapshot=suppressed_snapshot,
                            diagnostics={
                                "reasoningSuppressed": True,
                                "reason": "reasoning_surface_not_trusted",
                                "surface": reasoning_decision.get("reasoningSurfaceMode") or "hidden",
                                "reasoningKind": "hidden",
                                "reasoningSurfaceMode": reasoning_decision.get("reasoningSurfaceMode") or "hidden",
                                "reasoningSurfaceTrust": reasoning_decision.get("reasoningSurfaceTrust") or "unknown",
                                "reasoningDisplayKind": reasoning_decision.get("reasoningDisplayKind") or "hidden",
                                "matchedField": reasoning_decision.get("matchedField") or "",
                                "looksLikeProgress": _looks_like_untrusted_reasoning_progress(raw_reasoning),
                            },
                        )
                    )
                return events

            reasoning_delta, reasoning_snapshot = consume_canonical_stream_value(
                reasoning_snapshots,
                model_run_id,
                raw_reasoning,
                allow_token_delta=True,
            )
            if reasoning_delta:
                events.append(
                    CanonicalModelEvent(
                        event_type="reasoning_delta",
                        run_id=run_id,
                        model_run_id=model_run_id,
                        scope=scope,
                        delta=reasoning_delta,
                        snapshot=reasoning_snapshot,
                        diagnostics={
                            "trustedReasoning": True,
                            "reasoningKind": reasoning_decision.get("reasoningKind") or "provider_reasoning",
                            "reasoningSurfaceMode": reasoning_decision.get("reasoningSurfaceMode"),
                            "reasoningSurfaceTrust": reasoning_decision.get("reasoningSurfaceTrust"),
                            "reasoningDisplayKind": reasoning_decision.get("reasoningDisplayKind"),
                            "reasoningRequestStyle": reasoning_decision.get("reasoningRequestStyle"),
                            "matchedField": reasoning_decision.get("matchedField"),
                        },
                    )
                )

        return events

    def _consume_terminal_text(
        self,
        snapshots: dict[str, str],
        model_run_id: str | None,
        raw_value: str,
        *,
        emitted_text: str,
    ) -> tuple[str, str, dict[str, Any]]:
        run_key = normalized_model_run_id(model_run_id)
        current_value = raw_value or ""
        if not current_value:
            return "", snapshots.get(run_key, ""), {}

        previous_value = snapshots.get(run_key, "")
        if not previous_value:
            if current_value in emitted_text:
                snapshots[run_key] = current_value
                return "", current_value, {}
            snapshots[run_key] = current_value
            return current_value, current_value, {}

        if current_value == previous_value:
            snapshots[run_key] = current_value
            return "", current_value, {}
        if current_value.startswith(previous_value):
            snapshots[run_key] = current_value
            return current_value[len(previous_value):], current_value, {}
        if previous_value.endswith(current_value) or current_value in previous_value:
            return "", previous_value, {}

        overlap = longest_overlap_suffix_prefix(previous_value, current_value)
        if overlap > 0:
            snapshots[run_key] = current_value
            return current_value[overlap:], current_value, {}

        snapshots[run_key] = current_value
        return current_value, current_value, {
            "terminalTextCorrection": True,
            "previousRunTextChars": len(previous_value),
        }
