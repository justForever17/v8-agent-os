from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from langchain_core.messages import HumanMessage, ToolMessage

from core.database import db
from core.json_safe import to_jsonable
from core.realtime_protocol import build_runtime_event, utc_now_iso
from erc.command_service import command_service
from erc.session_admission_service import session_admission_service


SESSION_COORDINATION_INTENTS = {"inform", "correct", "request"}
SESSION_COORDINATION_REPLY_STATUSES = {
    "acknowledged",
    "accepted",
    "conflict",
    "blocked",
    "completed",
}
SESSION_COORDINATION_PENDING_STATES = {
    "awaiting_authorization",
    "queued",
    "promoted",
    "injected",
}
SESSION_COORDINATION_TERMINAL_STATES = {
    "replied",
    "cancelled",
    "blocked",
    "failed",
    "expired",
}
SESSION_COORDINATION_WAITING_RUN_STATES = {
    "queued",
    "waiting_input",
    "waiting_approval",
    "waiting_external_tool",
    "paused",
}

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{5,180}$")
_DIRECT_SEND_RE = re.compile(
    r"(?:发送|发给|通知|告诉|转告|同步|询问|问(?:一下|问)?|纠偏|修正|协调|"
    r"send|tell|notify|message|sync|ask|correct|coordinate)",
    re.IGNORECASE,
)
_DIRECT_SEND_DENY_RE = re.compile(
    r"(?:不要|不准|禁止|别|取消|do\s+not|don't|must\s+not|never)"
    r".{0,24}(?:发送|发给|通知|告诉|转告|同步|询问|纠偏|修正|协调|send|tell|notify|message|sync|ask|correct|coordinate)",
    re.IGNORECASE,
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password|authorization|cookie)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
)
_AFFIRMATIVE_TOKENS = {
    "approve_send",
    "approved",
    "approve",
    "yes",
    "y",
    "ok",
    "同意",
    "允许",
    "允许发送",
    "确认",
    "确认发送",
    "发送",
}
_REJECT_TOKENS = {
    "reject_send",
    "reject",
    "rejected",
    "no",
    "n",
    "cancel",
    "取消",
    "拒绝",
    "不允许",
    "不要发送",
}
_LOCAL_OWNER_IDS = {"", "anonymous", "local", "local_trusted", "admin_ui"}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()


def _compact_text(value: Any, *, limit: int = 6000) -> str:
    text = re.sub(r"\r\n?", "\n", str(value or "")).strip()
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _contains_secret(value: str) -> bool:
    return any(pattern.search(str(value or "")) for pattern in _SECRET_PATTERNS)


def _safe_session_label(value: Any) -> str:
    text = _compact_text(value, limit=160)
    return "受保护会话" if _contains_secret(text) else text


def _json_payload(payload: dict[str, Any]) -> str:
    return json.dumps(to_jsonable(payload), ensure_ascii=False, separators=(",", ":"))


def _message_tool_calls(message: Any) -> list[dict[str, Any]]:
    raw_calls = getattr(message, "tool_calls", None)
    if raw_calls is None and isinstance(message, dict):
        raw_calls = message.get("tool_calls") or message.get("toolCalls")
    return [dict(item) for item in list(raw_calls or []) if isinstance(item, dict)]


def _message_role(message: Any) -> str:
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, ToolMessage):
        return "tool"
    if isinstance(message, dict):
        return str(message.get("role") or message.get("type") or "").strip().lower()
    return str(getattr(message, "type", "") or "").strip().lower()


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _message_additional_kwargs(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        value = message.get("additional_kwargs") or message.get("additionalKwargs")
    else:
        value = getattr(message, "additional_kwargs", None)
    return dict(value or {}) if isinstance(value, dict) else {}


def _context_read_succeeded(message: Any) -> bool:
    content = _message_content(message).strip()
    if not content:
        return False
    if content.startswith("{"):
        try:
            payload = json.loads(content)
        except Exception:
            payload = None
        if isinstance(payload, dict) and "ok" in payload:
            return payload.get("ok") is True
    normalized = content.lower()
    failure_markers = (
        "session context takeover failed",
        "do not claim that the historical session was successfully taken over",
        "session_context_unauthorized",
        "session_context_owner_unknown",
        "session_context_source_owner_unknown",
        "session_not_found",
        "unsupported_external_thread_ref",
        "invalid_session_id",
    )
    return not any(marker in normalized for marker in failure_markers)


def _latest_human(messages: Iterable[Any]) -> tuple[str, bool]:
    for message in reversed(list(messages or [])):
        if _message_role(message) not in {"user", "human"}:
            continue
        kwargs = _message_additional_kwargs(message)
        is_coordination = isinstance(kwargs.get("v8os_session_coordination"), dict)
        return _message_content(message), is_coordination
    return "", False


def _has_completed_context_read(messages: Iterable[Any], target_session_id: str) -> bool:
    call_ids: set[str] = set()
    latest_human_index = -1
    items = list(messages or [])
    for index, message in enumerate(items):
        if _message_role(message) in {"user", "human"}:
            latest_human_index = index
    for message in items[latest_human_index + 1 :]:
        for call in _message_tool_calls(message):
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = str(call.get("name") or function.get("name") or "").strip()
            if name != "session_context_broker":
                continue
            args = call.get("args") if isinstance(call.get("args"), dict) else function.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if not isinstance(args, dict):
                continue
            if str(args.get("sourceSessionId") or "").strip() != target_session_id:
                continue
            call_id = str(call.get("id") or "").strip()
            if call_id:
                call_ids.add(call_id)
        if _message_role(message) not in {"tool", "toolmessage"}:
            continue
        if isinstance(message, ToolMessage):
            name = str(getattr(message, "name", "") or "").strip()
            call_id = str(getattr(message, "tool_call_id", "") or "").strip()
            status = str(getattr(message, "status", "") or "").strip().lower()
        else:
            name = str(message.get("name") or message.get("toolName") or "").strip()
            call_id = str(message.get("tool_call_id") or message.get("toolCallId") or "").strip()
            status = str(message.get("status") or "").strip().lower()
        if (
            name == "session_context_broker"
            and call_id in call_ids
            and status != "error"
            and _context_read_succeeded(message)
        ):
            return True
    return False


def _response_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()

    def _visit(item: Any, depth: int = 0) -> None:
        if depth > 5 or item is None:
            return
        if isinstance(item, str):
            normalized = item.strip().lower()
            if normalized:
                tokens.add(normalized)
                tokens.update(part for part in re.split(r"[\s,;|/]+", normalized) if part)
            return
        if isinstance(item, dict):
            for nested in item.values():
                _visit(nested, depth + 1)
            return
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                _visit(nested, depth + 1)

    _visit(value)
    return tokens


class SessionCoordinationService:
    def __init__(self) -> None:
        self._dispatch_lock = threading.RLock()

    @staticmethod
    def detail_ref(message_id: str) -> str:
        return f"v8os-session-message:{str(message_id or 'unknown').strip() or 'unknown'}"

    def compact_ref(self, row: dict[str, Any], *, viewer_session_id: str = "") -> dict[str, Any]:
        source_session_id = str(row.get("sourceSessionId") or row.get("source_session_id") or "")
        target_session_id = str(row.get("targetSessionId") or row.get("target_session_id") or "")
        payload = {
            "messageId": row.get("messageId") or row.get("id"),
            "threadId": row.get("threadId") or row.get("thread_id"),
            "messageType": row.get("messageType") or row.get("message_type"),
            "sourceSessionId": source_session_id,
            "targetSessionId": target_session_id,
            "intent": row.get("intent"),
            "authority": row.get("authority"),
            "state": row.get("state"),
            "summary": _compact_text(row.get("summary") or row.get("content") or "", limit=1600),
            "replyStatus": row.get("replyStatus") or row.get("reply_status"),
            "replyToMessageId": row.get("replyToMessageId") or row.get("reply_to_message_id"),
            "hopCount": int(row.get("hopCount") or row.get("hop_count") or 1),
            "maxHops": int(row.get("maxHops") or row.get("max_hops") or 2),
            "detailRef": self.detail_ref(str(row.get("messageId") or row.get("id") or "")),
            "evidenceRefs": list(row.get("evidenceRefs") or []),
            "createdAt": row.get("createdAt") or row.get("created_at"),
            "updatedAt": row.get("updatedAt") or row.get("updated_at"),
            "errorCode": row.get("errorCode") or row.get("error_code"),
        }
        if viewer_session_id:
            payload["direction"] = "outgoing" if viewer_session_id == source_session_id else "incoming"
        return {key: value for key, value in payload.items() if value not in (None, "", [], {})}

    def list_for_session(self, session_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return []
        rows = db.list_session_coordination_messages(
            session_id=normalized_session_id,
            newest_first=True,
            limit=max(1, min(int(limit or 20), 50)),
        )
        return [
            self.compact_ref(row, viewer_session_id=normalized_session_id)
            for row in reversed(rows)
        ]

    def _expire_undelivered_for_target(self, target_session_id: str) -> int:
        expired = 0
        rows = db.list_session_coordination_messages(
            target_session_id=target_session_id,
            states=["awaiting_authorization", "queued", "promoted"],
            limit=500,
        )
        for row in rows:
            if not self._is_expired(row):
                continue
            updated = db.update_session_coordination_message(
                str(row.get("id") or ""),
                state="expired",
                error_code="delivery_expired",
            ) or row
            self._emit_transition(updated, "session_coordination.expired")
            expired += 1
        return expired

    @staticmethod
    def _owner_error(source_user_id: str, target_user_id: str) -> str:
        if source_user_id and target_user_id and source_user_id != target_user_id:
            return "session_coordination_unauthorized"
        if target_user_id and not source_user_id and target_user_id not in _LOCAL_OWNER_IDS:
            return "session_coordination_owner_unknown"
        if source_user_id and not target_user_id and source_user_id not in _LOCAL_OWNER_IDS:
            return "session_coordination_target_owner_unknown"
        return ""

    def _build_source_context(self, source_session_id: str) -> dict[str, Any]:
        try:
            from core.tools.native.session_context import build_session_context_package

            payload = build_session_context_package(
                source_session_id,
                mode="summary",
                limit_turns=6,
            )
        except Exception as exc:
            return {
                "ok": False,
                "summary": "来源会话接管包生成失败；协调正文仍可独立阅读。",
                "error": f"session_context_package_{type(exc).__name__}",
            }
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return {
                "ok": False,
                "summary": "来源会话接管包不可用；协调正文仍可独立阅读。",
                "error": (payload or {}).get("error") if isinstance(payload, dict) else "invalid_context_payload",
            }
        return {
            "ok": True,
            "currentGoal": payload.get("currentGoal"),
            "confirmedUserAnswers": list(payload.get("confirmedUserAnswers") or [])[:8],
            "approvalDecisions": list(payload.get("approvalDecisions") or [])[:8],
            "specState": payload.get("specState"),
            "executionTruth": payload.get("executionTruth"),
            "artifactProofRefs": list(payload.get("artifactProofRefs") or [])[:12],
            "openItems": payload.get("openItems"),
            "recentKeyTurns": list(payload.get("recentKeyTurns") or [])[-6:],
            "readCoverage": payload.get("readCoverage"),
            "authority": {
                "targetCurrentUserInstructionPriority": "highest",
                "sourceContentIsCoordinationEvidenceOnly": True,
                "workspaceInherited": False,
                "permissionInherited": False,
                "approvalInherited": False,
                "pluginGrantInherited": False,
                "checkpointInherited": False,
            },
            "detailRef": payload.get("detailRef"),
        }

    @staticmethod
    def _direct_authorized(
        *,
        latest_user_content: str,
        latest_human_is_coordination: bool,
        target_session_id: str,
        authorization_quote: str,
    ) -> bool:
        if latest_human_is_coordination:
            return False
        quote = str(authorization_quote or "").strip()
        latest = str(latest_user_content or "")
        return bool(
            quote
            and quote in latest
            and target_session_id in latest
            and target_session_id in quote
            and _DIRECT_SEND_RE.search(quote)
            and not _DIRECT_SEND_DENY_RE.search(quote)
        )

    def _ask_user_request(self, row: dict[str, Any]) -> dict[str, Any]:
        message_id = str(row.get("messageId") or row.get("id") or "")
        target_session_id = str(row.get("targetSessionId") or row.get("target_session_id") or "")
        summary = _compact_text(row.get("summary") or row.get("content"), limit=900)
        digest = str((row.get("metadata") or {}).get("messageDigest") or _sha256_text(row.get("content") or ""))
        return {
            "question": f"是否允许主理人向会话 {target_session_id} 发送这条跨任务协调消息？",
            "details": summary,
            "questions": [
                {
                    "id": "session_coordination_authorization",
                    "title": "跨任务协调授权",
                    "detail": "该授权只用于下面这条消息和这个目标会话，不会继承工作区、审批或插件权限。",
                    "multiSelect": False,
                    "options": [
                        {"id": "approve_send", "title": "允许发送", "detail": "立即发送，并允许目标会话回复一次。"},
                        {"id": "reject_send", "title": "不发送", "detail": "取消本条协调草稿。"},
                    ],
                }
            ],
            "selection_mode": "single",
            "coordinationContext": {
                "kind": "session_coordination_authorization",
                "draftMessageId": message_id,
                "targetSessionId": target_session_id,
                "messageDigest": digest,
                "preview": summary,
                "expiresAt": row.get("expiresAt") or row.get("expires_at"),
                "oneShot": True,
            },
        }

    def send(
        self,
        *,
        source_session_id: str,
        source_run_id: str,
        source_user_id: str,
        target_session_id: str,
        intent: str,
        content: str,
        authorization_quote: str,
        state: Optional[dict[str, Any]],
        tool_call_id: str,
    ) -> dict[str, Any]:
        target_session_id = str(target_session_id or "").strip()
        source_run_id = str(source_run_id or "").strip()
        intent = str(intent or "request").strip().lower() or "request"
        content = _compact_text(content, limit=6000)
        if not source_run_id:
            return self._error("source_run_required", "跨会话发送必须绑定当前 Supervisor run。")
        if not _SESSION_ID_RE.fullmatch(target_session_id):
            return self._error("invalid_target_session_id", "目标会话 ID 格式不合法。")
        if target_session_id == source_session_id:
            return self._error("self_target_not_allowed", "跨会话协调不能发送给当前会话自身。")
        if intent not in SESSION_COORDINATION_INTENTS:
            return self._error("invalid_intent", "协调意图只支持 inform、correct 或 request。")
        if not content:
            return self._error("empty_content", "协调消息不能为空。")
        if _contains_secret(content):
            return self._error("secret_detected", "消息疑似包含密钥或凭据，已阻止跨会话发送。")

        source_session = db.get_session(source_session_id)
        target_session = db.get_session(target_session_id)
        if not source_session or not target_session:
            return self._error("session_not_found", "来源或目标会话不存在。")
        effective_source_user = str(source_user_id or source_session.get("user_id") or "").strip()
        source_owner_error = self._owner_error(effective_source_user, str(source_session.get("user_id") or "").strip())
        target_owner_error = self._owner_error(effective_source_user, str(target_session.get("user_id") or "").strip())
        if source_owner_error or target_owner_error:
            return self._error(source_owner_error or target_owner_error, "无法确认两个会话属于同一用户，已拒绝发送。")

        messages = list((state or {}).get("messages") or []) if isinstance(state, dict) else []
        if not _has_completed_context_read(messages, target_session_id):
            return self._error(
                "target_context_read_required",
                "发送前必须在本轮先成功调用 session_context_broker 读取目标会话。",
                recommendedNextAction=f"Call session_context_broker(sourceSessionId='{target_session_id}', mode='summary', limitTurns=6).",
            )

        latest_user_content, latest_human_is_coordination = _latest_human(messages)
        direct_authorized = self._direct_authorized(
            latest_user_content=latest_user_content,
            latest_human_is_coordination=latest_human_is_coordination,
            target_session_id=target_session_id,
            authorization_quote=authorization_quote,
        )
        digest = _sha256_text(f"{source_session_id}\n{target_session_id}\n{intent}\n{content}")
        idempotency_key = f"coord-send:{source_run_id}:{target_session_id}:{digest}"
        with self._dispatch_lock:
            existing = db.get_session_coordination_message_by_idempotency(idempotency_key)
            if existing:
                payload = self._ok(existing)
                if str(existing.get("state") or "") == "awaiting_authorization":
                    payload["authorizationRequired"] = True
                    payload["askUserRequest"] = self._ask_user_request(existing)
                return payload
            self._expire_undelivered_for_target(target_session_id)
            if db.count_pending_session_coordination_messages(target_session_id) >= 20:
                return self._error("target_inbox_full", "目标会话已有 20 条未完成协调消息，请等待处理后再发送。")

            message_id = f"coord_{uuid.uuid4().hex}"
            thread_id = f"coordthread_{uuid.uuid4().hex}"
            now = datetime.now(timezone.utc)
            expires_at = (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")
            authority = "current_user_explicit" if direct_authorized else "ask_user_approved"
            next_state = "queued" if direct_authorized else "awaiting_authorization"
            row = db.add_session_coordination_message(
                message_id=message_id,
                thread_id=thread_id,
                message_type="request",
                source_session_id=source_session_id,
                target_session_id=target_session_id,
                source_run_id=source_run_id or None,
                target_run_id=None,
                source_user_id=effective_source_user or None,
                intent=intent,
                authority=authority,
                content=content,
                summary=content,
                context=self._build_source_context(source_session_id),
                evidence_refs=[],
                reply_to_message_id=None,
                reply_status=None,
                hop_count=1,
                max_hops=2,
                state=next_state,
                idempotency_key=idempotency_key,
                metadata={
                    "messageDigest": digest,
                    "toolCallId": tool_call_id,
                    "sourceSessionTitle": _safe_session_label(source_session.get("title")),
                    "targetSessionTitle": _safe_session_label(target_session.get("title")),
                    "replyRequired": True,
                },
                expires_at=expires_at,
                authorized_at=utc_now_iso() if direct_authorized else None,
            )
        row_message_id = str(row.get("id") or message_id)
        if direct_authorized:
            self._emit_transition(row, "session_coordination.queued")
            row = self.dispatch_message(row_message_id) or row
            return self._ok(row)
        payload = self._ok(row)
        payload["authorizationRequired"] = True
        payload["askUserRequest"] = self._ask_user_request(row)
        payload["recommendedNextAction"] = "Call ask_user with the returned askUserRequest fields."
        return payload

    def reply(
        self,
        *,
        current_session_id: str,
        current_run_id: str,
        current_user_id: str,
        message_id: str,
        reply_status: str,
        content: str,
        evidence_refs: Optional[list[str]],
        state: Optional[dict[str, Any]],
        tool_call_id: str,
    ) -> dict[str, Any]:
        parent = db.get_session_coordination_message(message_id)
        if not parent:
            return self._error("coordination_message_not_found", "找不到需要回复的跨会话消息。")
        inbound = self.inbound_from_state(state)
        if not inbound or str(inbound.get("messageId") or inbound.get("id") or "") != str(message_id or ""):
            return self._error("reply_not_bound_to_active_message", "只能回复当前注入给 Supervisor 的跨会话消息。")
        if str(parent.get("targetSessionId") or parent.get("target_session_id") or "") != current_session_id:
            return self._error("reply_session_mismatch", "当前会话不是该消息的目标会话。")
        if str(parent.get("messageType") or parent.get("message_type") or "") != "request" or int(parent.get("hopCount") or 1) != 1:
            return self._error("max_hops_exceeded", "跨会话协调最多两跳，回复消息不能再次回复。")
        reply_key = f"coord-reply:{message_id}"
        existing = db.get_session_coordination_message_by_idempotency(reply_key)
        if existing:
            return self._ok(existing)
        if str(parent.get("state") or "") != "injected":
            return self._error("reply_message_not_active", "协调消息尚未注入或已经终结，不能回复。")
        reply_status = str(reply_status or "acknowledged").strip().lower() or "acknowledged"
        if reply_status not in SESSION_COORDINATION_REPLY_STATUSES:
            return self._error("invalid_reply_status", "回复状态不符合协调契约。")
        content = _compact_text(content, limit=6000)
        if not content:
            return self._error("empty_reply", "跨会话回复不能为空。")
        if _contains_secret(content):
            return self._error("secret_detected", "回复疑似包含密钥或凭据，已阻止发送。")
        normalized_refs = [str(item or "").strip() for item in list(evidence_refs or []) if str(item or "").strip()][:16]
        if any(_contains_secret(item) for item in normalized_refs):
            return self._error("secret_detected", "证据引用疑似包含密钥或凭据，已阻止跨会话回复。")
        if reply_status == "completed" and not normalized_refs:
            reply_status = "accepted"

        target_session_id = str(parent.get("sourceSessionId") or parent.get("source_session_id") or "")
        target_session = db.get_session(target_session_id)
        current_session = db.get_session(current_session_id)
        if not target_session or not current_session:
            return self._error("reply_target_missing", "来源会话已不存在，无法投递回复。")
        owner_error = self._owner_error(
            str(current_user_id or current_session.get("user_id") or "").strip(),
            str(target_session.get("user_id") or "").strip(),
        )
        if owner_error:
            return self._error(owner_error, "来源会话 owner 与当前用户不一致，已拒绝回复。")

        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")
        reply_id = f"coord_{uuid.uuid4().hex}"
        row = db.add_session_coordination_message(
            message_id=reply_id,
            thread_id=str(parent.get("threadId") or parent.get("thread_id") or ""),
            message_type="reply",
            source_session_id=current_session_id,
            target_session_id=target_session_id,
            source_run_id=current_run_id or None,
            target_run_id=None,
            source_user_id=str(current_user_id or current_session.get("user_id") or "").strip() or None,
            intent=str(parent.get("intent") or "request"),
            authority="bounded_reply",
            content=content,
            summary=content,
            context={},
            evidence_refs=normalized_refs,
            reply_to_message_id=message_id,
            reply_status=reply_status,
            hop_count=2,
            max_hops=2,
            state="queued",
            idempotency_key=reply_key,
            metadata={
                "toolCallId": tool_call_id,
                "sourceSessionTitle": _safe_session_label(current_session.get("title")),
                "targetSessionTitle": _safe_session_label(target_session.get("title")),
                "finalReply": True,
            },
            expires_at=expires_at,
            authorized_at=utc_now_iso(),
        )
        actual_reply_id = str(row.get("id") or reply_id)
        db.update_session_coordination_message(
            message_id,
            state="replied",
            reply_status=reply_status,
            metadata_updates={"replyMessageId": actual_reply_id},
            timestamp_field="replied_at",
        )
        updated_parent = db.get_session_coordination_message(message_id) or parent
        self._emit_transition(updated_parent, "session_coordination.replied")
        self._emit_transition(row, "session_coordination.queued")
        row = self.dispatch_message(actual_reply_id) or row
        self.dispatch_for_session(current_session_id)
        return self._ok(row)

    @staticmethod
    def inbound_from_state(state: Optional[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        value = state.get("session_coordination") or state.get("sessionCoordination")
        if isinstance(value, dict):
            return dict(value)
        route_context = state.get("current_route_context")
        if isinstance(route_context, dict):
            value = route_context.get("sessionCoordination") or route_context.get("session_coordination")
            if isinstance(value, dict):
                return dict(value)
        return {}

    def status(self, *, current_session_id: str, current_user_id: str, message_id: str) -> dict[str, Any]:
        row = db.get_session_coordination_message(message_id)
        if not row:
            return self._error("coordination_message_not_found", "找不到跨会话协调消息。")
        if current_session_id not in {
            str(row.get("sourceSessionId") or row.get("source_session_id") or ""),
            str(row.get("targetSessionId") or row.get("target_session_id") or ""),
        }:
            return self._error("coordination_message_unauthorized", "当前会话无权读取这条协调消息。")
        session = db.get_session(current_session_id)
        if session and self._owner_error(str(current_user_id or ""), str(session.get("user_id") or "")):
            return self._error("coordination_message_unauthorized", "当前用户无权读取这条协调消息。")
        if str(row.get("state") or "") in {"awaiting_authorization", "queued", "promoted"} and self._is_expired(row):
            row = db.update_session_coordination_message(
                message_id,
                state="expired",
                error_code="delivery_expired",
            ) or row
            self._emit_transition(row, "session_coordination.expired")
        return self._ok(row)

    def cancel(self, *, current_session_id: str, current_user_id: str, message_id: str) -> dict[str, Any]:
        row = db.get_session_coordination_message(message_id)
        if not row:
            return self._error("coordination_message_not_found", "找不到跨会话协调消息。")
        if str(row.get("sourceSessionId") or row.get("source_session_id") or "") != current_session_id:
            return self._error("cancel_not_allowed", "只有来源会话可以取消尚未注入的协调消息。")
        if str(row.get("state") or "") not in {"awaiting_authorization", "queued", "promoted"}:
            return self._error("cancel_not_allowed", "消息已经注入或终结，不能再取消。")
        session = db.get_session(current_session_id)
        if session and self._owner_error(str(current_user_id or ""), str(session.get("user_id") or "")):
            return self._error("cancel_not_allowed", "当前用户无权取消这条协调消息。")
        target_run_id = str(row.get("targetRunId") or row.get("target_run_id") or "")
        if target_run_id:
            signal = command_service.peek_control_signal(target_run_id)
            if signal and str((signal.get("payload") or {}).get("messageId") or "") == message_id:
                command_service.clear_control_signal(target_run_id)
        updated = db.update_session_coordination_message(
            message_id,
            state="cancelled",
            error_code="cancelled_by_source",
            timestamp_field="cancelled_at",
        ) or row
        self._emit_transition(updated, "session_coordination.cancelled")
        return self._ok(updated)

    def handle_ask_user_resolution(self, interaction: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        request = interaction.get("request") if isinstance(interaction.get("request"), dict) else {}
        context = request.get("coordinationContext") if isinstance(request.get("coordinationContext"), dict) else {}
        if str(context.get("kind") or "") != "session_coordination_authorization":
            return {"handled": False, "reason": "not_session_coordination"}
        message_id = str(context.get("draftMessageId") or "").strip()
        row = db.get_session_coordination_message(message_id)
        if not row or str(row.get("state") or "") != "awaiting_authorization":
            return {"handled": False, "reason": "draft_not_waiting", "messageId": message_id}
        if self._is_expired(row):
            updated = db.update_session_coordination_message(
                message_id,
                state="expired",
                error_code="authorization_expired",
            ) or row
            self._emit_transition(updated, "session_coordination.expired")
            return {"handled": True, "approved": False, "reason": "expired", "messageId": message_id}
        interaction_session_id = str(interaction.get("session_id") or interaction.get("sessionId") or "").strip()
        interaction_run_id = str(interaction.get("run_id") or interaction.get("runId") or "").strip()
        if (
            interaction_session_id != str(row.get("sourceSessionId") or row.get("source_session_id") or "")
            or interaction_run_id != str(row.get("sourceRunId") or row.get("source_run_id") or "")
        ):
            updated = db.update_session_coordination_message(
                message_id,
                state="blocked",
                error_code="authorization_scope_mismatch",
            ) or row
            self._emit_transition(updated, "session_coordination.blocked")
            return {"handled": True, "approved": False, "reason": "scope_mismatch", "messageId": message_id}
        expected_digest = str((row.get("metadata") or {}).get("messageDigest") or "")
        if expected_digest and expected_digest != str(context.get("messageDigest") or ""):
            updated = db.update_session_coordination_message(
                message_id,
                state="blocked",
                error_code="authorization_digest_mismatch",
            ) or row
            self._emit_transition(updated, "session_coordination.blocked")
            return {"handled": True, "approved": False, "reason": "digest_mismatch", "messageId": message_id}
        tokens = _response_tokens(response)
        tokens.update(_response_tokens(interaction.get("answer_text")))
        approved = bool(tokens.intersection(_AFFIRMATIVE_TOKENS))
        rejected = bool(tokens.intersection(_REJECT_TOKENS))
        if not approved and not rejected:
            return {"handled": True, "approved": False, "reason": "answer_not_decisive", "messageId": message_id}
        if rejected:
            updated = db.update_session_coordination_message(
                message_id,
                state="cancelled",
                authorization_interaction_id=str(interaction.get("id") or "") or None,
                error_code="authorization_rejected",
                timestamp_field="cancelled_at",
            ) or row
            self._emit_transition(updated, "session_coordination.cancelled")
            return {"handled": True, "approved": False, "reason": "rejected", "messageId": message_id}
        updated = db.update_session_coordination_message(
            message_id,
            state="queued",
            authority="ask_user_approved",
            authorization_interaction_id=str(interaction.get("id") or "") or None,
            error_code="",
            timestamp_field="authorized_at",
        ) or row
        self._emit_transition(updated, "session_coordination.queued")
        delivered = self.dispatch_message(message_id) or updated
        return {"handled": True, "approved": True, "message": self.compact_ref(delivered)}

    def dispatch_message(self, message_id: str) -> Optional[dict[str, Any]]:
        with self._dispatch_lock:
            row = db.get_session_coordination_message(message_id)
            if not row or str(row.get("state") or "") not in {"queued", "promoted"}:
                return row
            if self._is_expired(row):
                updated = db.update_session_coordination_message(
                    message_id,
                    state="expired",
                    error_code="delivery_expired",
                ) or row
                self._emit_transition(updated, "session_coordination.expired")
                return updated
            target_session_id = str(row.get("targetSessionId") or row.get("target_session_id") or "")
            target_session = db.get_session(target_session_id)
            if not target_session:
                updated = db.update_session_coordination_message(
                    message_id,
                    state="blocked",
                    error_code="target_session_deleted",
                ) or row
                self._emit_transition(updated, "session_coordination.blocked")
                return updated
            delivery_owner_error = self._owner_error(
                str(row.get("sourceUserId") or row.get("source_user_id") or "").strip(),
                str(target_session.get("user_id") or target_session.get("userId") or "").strip(),
            )
            if delivery_owner_error:
                updated = db.update_session_coordination_message(
                    message_id,
                    state="blocked",
                    error_code="delivery_owner_changed",
                ) or row
                self._emit_transition(updated, "session_coordination.blocked")
                return updated
            lane = session_admission_service.get_lane_view(target_session_id)
            active_run_id = str(lane.get("activeRunId") or "").strip()
            active_run = db.get_run_record(active_run_id) if active_run_id else None
            run_status = str((active_run or {}).get("status") or "").strip().lower()
            if active_run_id and run_status == "running":
                existing_signal = command_service.peek_control_signal(active_run_id)
                if existing_signal:
                    same_message = (
                        str(existing_signal.get("command") or "") == "session_coordination"
                        and str((existing_signal.get("payload") or {}).get("messageId") or "") == message_id
                    )
                    if same_message:
                        return row
                    if str(row.get("state") or "") == "promoted":
                        return db.update_session_coordination_message(
                            message_id,
                            state="queued",
                            clear_target_run_id=True,
                        ) or row
                    return row
                updated = db.update_session_coordination_message(
                    message_id,
                    state="promoted",
                    target_run_id=active_run_id,
                    timestamp_field="promoted_at",
                ) or row
                command_service.issue_control_signal(
                    active_run_id,
                    command="session_coordination",
                    reason="cross_session_supervisor_message",
                    payload={"messageId": message_id},
                )
                self._emit_transition(updated, "session_coordination.promoted")
                return updated
            if active_run_id and run_status in SESSION_COORDINATION_WAITING_RUN_STATES:
                if str(row.get("state") or "") == "promoted":
                    return db.update_session_coordination_message(
                        message_id,
                        state="queued",
                        clear_target_run_id=True,
                    ) or row
                return row
            return self._wake_idle_target(row, target_session)

    def dispatch_for_session(self, session_id: str) -> Optional[dict[str, Any]]:
        rows = db.list_session_coordination_messages(
            target_session_id=session_id,
            states=["promoted", "queued"],
            limit=20,
        )
        if not rows:
            return None
        promoted = next((item for item in rows if str(item.get("state") or "") == "promoted"), None)
        return self.dispatch_message(str((promoted or rows[0]).get("id") or ""))

    def _wake_idle_target(self, row: dict[str, Any], target_session: dict[str, Any]) -> dict[str, Any]:
        message_id = str(row.get("id") or "")
        run_id = f"run_{uuid.uuid4().hex}"
        target_session_id = str(row.get("targetSessionId") or row.get("target_session_id") or "")
        db.create_run_record(
            run_id=run_id,
            session_id=target_session_id,
            conversation_id=target_session_id,
            user_id=str(target_session.get("user_id") or "anonymous"),
            run_type="chat",
            status="queued",
            trigger_source="session_coordination",
            metadata={
                "source": "session_coordination_messages",
                "coordinationMessageId": message_id,
                "coordinationThreadId": row.get("threadId") or row.get("thread_id"),
            },
        )
        updated = db.update_session_coordination_message(
            message_id,
            state="promoted",
            target_run_id=run_id,
            timestamp_field="promoted_at",
        ) or row
        try:
            from erc.command_router import runtime_command_router

            request = runtime_command_router.build_session_coordination_chat_request(
                session_id=target_session_id,
                message_id=message_id,
            )
            scheduled = runtime_command_router.schedule_chat_run(
                request,
                transport="session_coordination",
                run_id=run_id,
            )
        except Exception as exc:
            scheduled = None
            db.update_run_record(
                run_id,
                status="failed",
                error_message=f"session_coordination_schedule_failed: {type(exc).__name__}: {exc}",
            )
        if not scheduled:
            updated = db.update_session_coordination_message(
                message_id,
                state="queued",
                clear_target_run_id=True,
                error_code="wake_schedule_unavailable",
            ) or updated
            self._emit_transition(updated, "session_coordination.queued")
            return updated
        self._emit_transition(updated, "session_coordination.promoted")
        return updated

    def mark_injected(self, message_id: str, *, target_run_id: str) -> Optional[dict[str, Any]]:
        row = db.get_session_coordination_message(message_id)
        if not row or str(row.get("state") or "") in SESSION_COORDINATION_TERMINAL_STATES:
            return row
        updated = db.update_session_coordination_message(
            message_id,
            state="injected",
            target_run_id=target_run_id,
            error_code="",
            timestamp_field="injected_at",
        ) or row
        self._emit_transition(updated, "session_coordination.injected")
        return updated

    def mark_failed(
        self,
        message_id: str,
        *,
        error_code: str,
        metadata_updates: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        row = db.get_session_coordination_message(message_id)
        if not row or str(row.get("state") or "") in SESSION_COORDINATION_TERMINAL_STATES:
            return row
        updated = db.update_session_coordination_message(
            message_id,
            state="failed",
            error_code=_compact_text(error_code, limit=160) or "session_coordination_failed",
            metadata_updates=metadata_updates,
        ) or row
        self._emit_transition(updated, "session_coordination.failed")
        return updated

    def yield_to_human_guidance(self, run_id: str) -> None:
        rows = db.list_session_coordination_messages(
            target_run_id=run_id,
            states=["promoted"],
            limit=4,
        )
        for row in rows:
            updated = db.update_session_coordination_message(
                str(row.get("id") or ""),
                state="queued",
                clear_target_run_id=True,
                metadata_updates={"yieldedToHumanGuidance": True},
            ) or row
            self._emit_transition(updated, "session_coordination.queued")

    def on_run_available(self, session_id: str, run_id: str) -> None:
        del run_id
        self.dispatch_for_session(session_id)

    def on_run_terminal(self, session_id: str, run_id: str, *, status: str = "") -> None:
        promoted = db.list_session_coordination_messages(
            target_run_id=run_id,
            states=["promoted"],
            limit=20,
        )
        for row in promoted:
            updated = db.update_session_coordination_message(
                str(row.get("id") or ""),
                state="queued",
                clear_target_run_id=True,
                metadata_updates={"requeuedAfterRunTerminal": status or True},
            ) or row
            self._emit_transition(updated, "session_coordination.queued")
        injected = db.list_session_coordination_messages(
            target_run_id=run_id,
            states=["injected"],
            limit=20,
        )
        for row in injected:
            message_type = str(row.get("messageType") or row.get("message_type") or "")
            if message_type == "reply":
                if str(status or "").lower() == "completed":
                    updated = db.update_session_coordination_message(
                        str(row.get("id") or ""),
                        state="replied",
                        metadata_updates={"terminalRunStatus": status, "replyDelivered": True},
                        timestamp_field="replied_at",
                    ) or row
                    self._emit_transition(updated, "session_coordination.replied")
                else:
                    self.mark_failed(
                        str(row.get("id") or ""),
                        error_code=f"reply_delivery_run_{str(status or 'failed').lower()}",
                        metadata_updates={"terminalRunStatus": status},
                    )
                continue
            if message_type != "request":
                continue
            reply = db.get_session_coordination_message_by_idempotency(f"coord-reply:{row.get('id')}")
            if reply:
                continue
            updated = db.update_session_coordination_message(
                str(row.get("id") or ""),
                state="failed",
                error_code="reply_contract_not_satisfied",
                metadata_updates={"terminalRunStatus": status},
            ) or row
            self._emit_transition(updated, "session_coordination.failed")
        self.dispatch_for_session(session_id)

    def recover_pending(self) -> dict[str, Any]:
        recovered = 0
        expired = 0
        rows = db.list_session_coordination_messages(
            states=["awaiting_authorization", "queued", "promoted", "injected"],
            limit=500,
        )
        for row in rows:
            if self._is_expired(row):
                updated = db.update_session_coordination_message(
                    str(row.get("id") or ""),
                    state="expired",
                    error_code="delivery_expired",
                ) or row
                self._emit_transition(updated, "session_coordination.expired")
                expired += 1
                continue
            if str(row.get("state") or "") == "awaiting_authorization":
                continue
            if str(row.get("state") or "") == "injected":
                target_run_id = str(row.get("targetRunId") or row.get("target_run_id") or "").strip()
                target_session_id = str(row.get("targetSessionId") or row.get("target_session_id") or "").strip()
                run = db.get_run_record(target_run_id) if target_run_id else None
                run_status = str((run or {}).get("status") or "").strip().lower()
                if run_status in {"completed", "failed", "cancelled"}:
                    self.on_run_terminal(target_session_id, target_run_id, status=run_status)
                else:
                    self.mark_failed(
                        str(row.get("id") or ""),
                        error_code="engine_restart_after_coordination_injection",
                        metadata_updates={
                            "recoveredAfterEngineRestart": True,
                            "orphanedRunStatus": run_status or "missing",
                        },
                    )
                recovered += 1
                continue
            self.dispatch_message(str(row.get("id") or ""))
            recovered += 1
        return {"recovered": recovered, "expired": expired}

    def prepare_session_deletion(self, session_id: str) -> dict[str, int]:
        rows = db.list_session_coordination_messages(session_id=session_id, limit=500)
        cancelled = 0
        retained = 0
        for row in rows:
            message_id = str(row.get("id") or "")
            state = str(row.get("state") or "")
            metadata_key = "sourceSessionDeleted" if str(row.get("sourceSessionId") or "") == session_id else "targetSessionDeleted"
            if state in {"awaiting_authorization", "queued", "promoted"}:
                updated = db.update_session_coordination_message(
                    message_id,
                    state="cancelled",
                    error_code="session_deleted_before_delivery",
                    metadata_updates={metadata_key: True},
                    timestamp_field="cancelled_at",
                ) or row
                self._emit_transition(updated, "session_coordination.cancelled", skip_session_id=session_id)
                cancelled += 1
            else:
                db.update_session_coordination_message(
                    message_id,
                    metadata_updates={metadata_key: True},
                )
                retained += 1
        return {"cancelled": cancelled, "retained": retained}

    @staticmethod
    def _is_expired(row: dict[str, Any]) -> bool:
        raw = str(row.get("expiresAt") or row.get("expires_at") or "").strip()
        if not raw:
            return False
        try:
            expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return False
        return expires <= datetime.now(timezone.utc)

    def _emit_transition(self, row: dict[str, Any], topic: str, *, skip_session_id: str = "") -> None:
        source_session_id = str(row.get("sourceSessionId") or row.get("source_session_id") or "")
        target_session_id = str(row.get("targetSessionId") or row.get("target_session_id") or "")
        authorized_for_delivery = bool(
            row.get("authorizedAt")
            or row.get("authorized_at")
            or row.get("authorizationInteractionId")
            or row.get("authorization_interaction_id")
            or str(row.get("authority") or "") in {"current_user_explicit", "bounded_reply"}
        )
        for session_id in (source_session_id, target_session_id):
            if not session_id or session_id == skip_session_id or not db.get_session(session_id):
                continue
            if session_id == target_session_id and not authorized_for_delivery:
                continue
            direction = "outgoing" if session_id == source_session_id else "incoming"
            self._upsert_governance_message(session_id, row, topic=topic, direction=direction)

    def _upsert_governance_message(
        self,
        session_id: str,
        row: dict[str, Any],
        *,
        topic: str,
        direction: str,
    ) -> None:
        message_id = str(row.get("messageId") or row.get("id") or "")
        canonical_message_id = f"session_coordination:{message_id}:{direction}"
        node_id = f"{canonical_message_id}:governance"
        timestamp_ms = int(time.time() * 1000)
        message_ref = self.compact_ref(row, viewer_session_id=session_id)
        metadata = dict(row.get("metadata") or {})
        candidate_run_id = str(
            row.get("targetRunId")
            or row.get("sourceRunId")
            or row.get("target_run_id")
            or row.get("source_run_id")
            or ""
        ).strip()
        canonical_run_id = candidate_run_id if candidate_run_id and db.get_run_record(candidate_run_id) else None
        request_info = {
            **message_ref,
            "sourceSessionTitle": metadata.get("sourceSessionTitle"),
            "targetSessionTitle": metadata.get("targetSessionTitle"),
        }
        node = {
            "id": node_id,
            "kind": "governance",
            "governanceType": "session_coordination",
            "timestamp": timestamp_ms,
            "topic": topic,
            "status": row.get("state"),
            "reason": "outgoing" if direction == "outgoing" else "incoming",
            "question": row.get("summary") or row.get("content") or "",
            "requestInfo": request_info,
            "ownerRuntimeId": "chat",
            "ownerAgentKind": "supervisor",
            "ownerAgentId": "supervisor",
            "ownerStreamKey": f"session_coordination:{message_id}:{direction}",
            "traceGroupId": f"session_coordination:{row.get('threadId') or row.get('thread_id')}",
            "displayInMessage": True,
        }
        existing = db.get_chat_canonical_message(canonical_message_id)
        if existing:
            canonical = db.update_chat_canonical_message(
                canonical_message_id,
                state="completed",
                nodes=[node],
                metadata={
                    **dict(existing.get("metadata") or {}),
                    "governanceOnly": True,
                    "sessionCoordinationMessageId": message_id,
                    "direction": direction,
                },
                finalized_at=utc_now_iso(),
            ) or existing
        else:
            with self._dispatch_lock:
                existing = db.get_chat_canonical_message(canonical_message_id)
                if not existing:
                    db.create_chat_canonical_message(
                        message_id=canonical_message_id,
                        session_id=session_id,
                        run_id=canonical_run_id,
                        ordinal=db.get_next_chat_canonical_ordinal(session_id),
                        role="assistant",
                        state="completed",
                        nodes=[node],
                        artifacts=[],
                        content_text="",
                        reasoning_text="",
                        metadata={
                            "governanceOnly": True,
                            "sessionCoordinationMessageId": message_id,
                            "direction": direction,
                        },
                        finalized_at=utc_now_iso(),
                    )
                canonical = db.get_chat_canonical_message(canonical_message_id) or {}
        event = build_runtime_event(
            topic=topic,
            payload={
                **message_ref,
                "message_id": canonical_message_id,
                "node_id": node_id,
                "transcript_version": int(canonical.get("version") or 1),
                "direction": direction,
                "sourceSessionTitle": metadata.get("sourceSessionTitle"),
                "targetSessionTitle": metadata.get("targetSessionTitle"),
                "displayInMessage": True,
                "targets": ["message", "runtime_card", "history"],
            },
            session_id=session_id,
            conversation_id=session_id,
            run_id=canonical_run_id,
            seq=db.get_next_runtime_seq(session_id),
            source={
                "plane": "engine",
                "component": "session_coordination",
                "node": "session_coordination_service",
                "agent_id": "supervisor",
            },
        )
        db.add_runtime_event(event)

    def _ok(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "tool": "session_message_broker",
            "message": self.compact_ref(row),
            "summary": row.get("summary") or row.get("content") or "",
            "detailRef": self.detail_ref(str(row.get("messageId") or row.get("id") or "")),
        }

    @staticmethod
    def _error(error: str, summary: str, **extra: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "tool": "session_message_broker",
            "error": error,
            "summary": summary,
            **extra,
        }


session_coordination_service = SessionCoordinationService()
