import sys
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END
from langgraph.types import Command

from core.context_governance import emit_context_prepared_event
from erc.runtime_context import get_runtime_context


def _safe_console_text(value) -> str:
    text = str(value)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except Exception:
        return text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")


def _safe_print(value) -> None:
    print(_safe_console_text(value))


def _tool_signature(message: AIMessage) -> str | None:
    tool_calls = list(getattr(message, "tool_calls", None) or [])
    if not tool_calls:
        return None
    normalized_calls = []
    for tool_call in tool_calls:
        normalized_calls.append(
            {
                "name": tool_call.get("name"),
                "args": tool_call.get("args"),
            }
        )
    return json.dumps(normalized_calls, ensure_ascii=False, sort_keys=True)


def _detect_no_progress_loop(messages) -> dict | None:
    recent = [message for message in messages if not isinstance(message, SystemMessage)][-12:]
    signatures: list[str] = []
    for message in recent:
        if isinstance(message, HumanMessage):
            signatures.clear()
            continue
        if not isinstance(message, AIMessage):
            continue
        signature = _tool_signature(message)
        if not signature:
            continue
        signatures.append(signature)

    if len(signatures) < 3:
        return None

    trailing_count = 1
    last_signature = signatures[-1]
    for signature in reversed(signatures[:-1]):
        if signature != last_signature:
            break
        trailing_count += 1

    if trailing_count < 3:
        return None

    try:
        decoded = json.loads(last_signature)
        tool_names = [str(item.get("name") or "?") for item in decoded if isinstance(item, dict)]
    except Exception:
        tool_names = ["unknown"]
    return {
        "count": trailing_count,
        "tool_names": tool_names or ["unknown"],
    }


def prepare_supervisor_messages(
    *,
    messages,
    system_content: str,
    ensure_reasoning_content,
    sanitize_message_chain,
    context_orchestrator,
    resolved_model_id: str | None,
    resolved_scope: str | None,
    scope_chain,
    remaining_steps: int,
):
    runtime_kind = str(get_runtime_context().get("runtime_kind") or "chat")
    prepared = [m for m in messages if not isinstance(m, SystemMessage)]
    prepared = [ensure_reasoning_content(message) for message in prepared]
    prepared = sanitize_message_chain(prepared)
    prepared_context = context_orchestrator.prepare(
        messages=prepared,
        runtime_kind=runtime_kind,
        target_role="supervisor",
        resolved_model_id=resolved_model_id,
        resolved_scope=resolved_scope,
        scope_chain=scope_chain,
        leading_system_content=system_content,
        keep_recent_override=5,
    )
    prepared = prepared_context.messages
    emit_context_prepared_event(
        prepared_context.audit,
        component="graph",
        node="supervisor_context",
        agent_id="supervisor",
    )
    if prepared_context.audit.get("block_count"):
        _safe_print(f"[ContextAudit] {json.dumps(prepared_context.audit, ensure_ascii=False)}")

    loop_info = _detect_no_progress_loop(prepared)
    if loop_info is not None:
        tool_list = ", ".join(loop_info["tool_names"])
        prepared.append(
            SystemMessage(
                content=(
                    "⚠️ LOOP BREAKER: The current run appears to be repeating the same tool call pattern "
                    f"{loop_info['count']} times with no clear new progress. "
                    f"Repeated tools: {tool_list}. "
                    "Do NOT call the same tool again unless you have genuinely new evidence. "
                    "You MUST either: 1) provide a concise progress summary and stop, "
                    "2) return a recoverable failure with the blocker, or 3) ask for approval / user input if that is the only safe next step."
                )
            )
        )
        _safe_print(f"[LoopBreaker] Repeated tool pattern detected ({tool_list}) x{loop_info['count']}")

    return prepared


def debug_supervisor_messages(messages) -> None:
    _safe_print(f"[DEBUG] Supervisor LLM final messages ({len(messages)} total):")
    for i, message in enumerate(messages):
        tool_context = ""
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            tool_names = [tool_call.get("name", "?") for tool_call in message.tool_calls]
            tool_context = f" [tool_calls: {tool_names}]"
        elif isinstance(message, ToolMessage):
            tool_context = f" [tool_call_id: {message.tool_call_id}]"
        _safe_print(f"  [{i}] {message.type}{tool_context}: {str(message.content)[:120]}")


def route_supervisor_response(response) -> Command:
    additional_kwargs = dict(getattr(response, "additional_kwargs", None) or {})
    current_route_context = dict(additional_kwargs.get("v8_delegation_context") or {})
    if hasattr(response, "tool_calls") and response.tool_calls:
        return Command(
            goto="supervisor_tools",
            update={"messages": [response], "current_route_context": current_route_context},
        )
    return Command(goto=END, update={"messages": [response], "current_route_context": current_route_context})
