from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState

from erc.runtime_context import get_runtime_context
from erc.session_coordination_service import session_coordination_service


__all__ = ["session_message_broker", "session_command_broker"]


@tool
def session_command_broker(
    mode: str,
    assignmentId: str = "",
    revision: int = 0,
    title: str = "",
    taskBrief: Optional[dict[str, Any]] = None,
    content: str = "",
    idempotencyKey: str = "",
    after: str = "",
    limit: int = 20,
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> str:
    """Create, list, continue or revoke persistent project work sessions.

    create: use a stable idempotencyKey, title, and a taskBrief with goal, explicit
    readSet/writeSet (empty means no writes), expectedOutputs, acceptanceContract.
    Sessions use the current root's ready workspace. Creation returns assignmentId
    and revision; it does not dispatch. continue: pass that assignmentId/revision,
    a stable idempotencyKey and the task instruction in content. This uses the
    durable project authorization across later root turns, including "continue".
    list discovers this root's assignments, with optional after cursor. revoke
    invalidates an exact assignment revision. Actor, owner, charter authority,
    Capsule and workspace identity are derived and rechecked on the server.
    Ordinary peer messages retain their separate evidence-only contract.
    """
    payload = session_coordination_service.command(
        context=get_runtime_context(), state=state or {}, mode=str(mode).strip().lower(),
        assignment_id=assignmentId, revision=revision, title=title, task=taskBrief,
        content=content, idempotency_key=idempotencyKey,
        after_id=after, limit=max(1, min(limit, 50)),
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@tool
def session_message_broker(
    mode: str,
    targetSessionId: str = "",
    messageId: str = "",
    intent: str = "request",
    content: str = "",
    userAuthorizationQuote: str = "",
    replyStatus: str = "acknowledged",
    evidenceRefs: Optional[list[str]] = None,
    state: Annotated[dict[str, Any], InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "session_message_broker",
) -> str:
    """Coordinate with another same-user V8OS Supervisor through a durable two-hop message.

    Use `send` only after this turn successfully calls `session_context_broker` for the
    exact target session. If the current user explicitly asked to send, pass a verbatim
    `userAuthorizationQuote` containing the target session ID and send/notify/ask/correct
    instruction. Otherwise the broker returns an exact `ask_user` request and waits for
    one-shot authorization. Use `reply` only for the active inbound coordination message.
    A reply is final and cannot be replied to again. This tool never inherits workspace,
    approval, plugin grants, credentials, checkpoints, or source-session permissions.
    """

    normalized_mode = str(mode or "").strip().lower()
    runtime_context = get_runtime_context() or {}
    session_id = str(runtime_context.get("session_id") or runtime_context.get("sessionId") or "").strip()
    run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip()
    user_id = str(runtime_context.get("user_id") or runtime_context.get("userId") or "").strip()
    if not session_id:
        return json.dumps(
            {
                "ok": False,
                "tool": "session_message_broker",
                "error": "runtime_session_missing",
                "summary": "当前运行没有可验证的 V8OS session，无法执行跨会话协调。",
            },
            ensure_ascii=False,
        )

    if normalized_mode == "send":
        payload = session_coordination_service.send(
            source_session_id=session_id,
            source_run_id=run_id,
            source_user_id=user_id,
            target_session_id=targetSessionId,
            intent=intent,
            content=content,
            authorization_quote=userAuthorizationQuote,
            state=state,
            tool_call_id=tool_call_id,
        )
    elif normalized_mode == "reply":
        payload = session_coordination_service.reply(
            current_session_id=session_id,
            current_run_id=run_id,
            current_user_id=user_id,
            message_id=messageId,
            reply_status=replyStatus,
            content=content,
            evidence_refs=evidenceRefs,
            state=state,
            tool_call_id=tool_call_id,
        )
    elif normalized_mode == "status":
        payload = session_coordination_service.status(
            current_session_id=session_id,
            current_user_id=user_id,
            message_id=messageId,
        )
    elif normalized_mode == "cancel":
        payload = session_coordination_service.cancel(
            current_session_id=session_id,
            current_user_id=user_id,
            message_id=messageId,
        )
    else:
        payload = {
            "ok": False,
            "tool": "session_message_broker",
            "error": "unsupported_mode",
            "summary": "mode 只支持 send、reply、status 或 cancel。",
            "supportedModes": ["send", "reply", "status", "cancel"],
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
