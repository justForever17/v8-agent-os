from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backend import AdminBffBackend, V8Backend, V8PromptResult
from .launch import ACP_PROTOCOL_VERSION, build_launch_manifest
from .protocol import JsonRpcError, JsonRpcMessage, error_response, notification, require_object, result_response
from .surface import PRODUCT_AGENT_NAME, compact_runtime_event, markdown_update_from_v8, permission_event_kind


@dataclass
class AcpSession:
    acp_session_id: str
    v8_session_id: str
    workspace_path: str | None = None
    title: str | None = None
    current_run_id: str | None = None
    last_client_message_id: str | None = None
    pending_permissions: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class AcpBridge:
    """ACP JSON-RPC adapter for third-party Agent Clients.

    This class intentionally does not implement V8OS runtime semantics. It only
    translates ACP-shaped requests into V8OS Admin/Engine entry points and turns
    runtime updates into compact Markdown-facing ACP notifications.
    """

    def __init__(self, backend: V8Backend | None = None) -> None:
        self.backend = backend or AdminBffBackend()
        self.sessions: dict[str, AcpSession] = {}

    def handle_json_rpc(self, payload: dict[str, Any]) -> list[JsonRpcMessage]:
        request_id = payload.get("id")
        method = str(payload.get("method") or "").strip()
        try:
            if payload.get("jsonrpc") != "2.0":
                raise JsonRpcError(-32600, "Only JSON-RPC 2.0 messages are supported.")
            if not method and request_id is not None and ("result" in payload or "error" in payload):
                return self._handle_client_response(payload)
            if not method:
                raise JsonRpcError(-32600, "Missing JSON-RPC method.")
            params = require_object(payload.get("params"), field_name="params")
            result, notifications = self.dispatch(method, params)
            messages = list(notifications)
            if request_id is not None:
                messages.append(result_response(request_id, result))
            return messages
        except JsonRpcError as exc:
            return [error_response(request_id, exc)]
        except Exception as exc:
            return [error_response(request_id, JsonRpcError(-32000, str(exc)))]

    def dispatch(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], list[JsonRpcMessage]]:
        if method == "initialize":
            return self.initialize(params), []
        if method == "session/new":
            return self.session_new(params), []
        if method == "session/load":
            return self.session_load(params), []
        if method == "session/prompt":
            return self.session_prompt(params)
        if method == "session/cancel":
            return self.session_cancel(params), []
        if method == "_v8os/terminal/create":
            return self.terminal_create(params), []
        if method == "_v8os/terminal/input":
            return self.terminal_input(params), []
        if method == "_v8os/terminal/resize":
            return self.terminal_resize(params), []
        if method == "_v8os/terminal/kill":
            return self.terminal_kill(params), []
        if method == "_v8os/permission/classify":
            return self.permission_classify(params), []
        if method == "_v8os/permission/request":
            return self.permission_request(params)
        if method == "_v8os/permission/respond":
            return self.permission_respond(params)
        raise JsonRpcError(-32601, f"Unsupported ACP method: {method}")

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_info = {
            "name": PRODUCT_AGENT_NAME,
            "version": "0.1.0",
            "description": "V8OS external Agent Client adapter",
        }
        agent_capabilities = {
            "loadSession": True,
            "promptCapabilities": {
                "image": True,
                "audio": True,
                "embeddedContext": True,
            },
            "mcpCapabilities": {},
        }
        return {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "agentInfo": agent_info,
            "agentCapabilities": agent_capabilities,
            "authMethods": [],
            "agent": {
                "name": PRODUCT_AGENT_NAME,
                "displayName": "V8OS Agent",
            },
            "capabilities": {
                "sessions": True,
                "streaming": True,
                "terminal": True,
                "workspace": True,
                "permissions": True,
            },
            "_meta": {
                "v8os": {
                    "canonicalId": "acp_bridge",
                    "surface": "third_party_agent_client",
                    "clientInfo": params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else None,
                    "launch": build_launch_manifest(),
                }
            },
        }

    def _normalize_workspace_path(self, workspace_path: Any) -> str | None:
        value = str(workspace_path or "").strip()
        if not value:
            return None
        resolved = Path(value).expanduser()
        if not resolved.is_absolute():
            raise JsonRpcError(
                -32602,
                "workspacePath must be absolute; V8OS workspace trust is resolved by the Admin/Engine scope service.",
            )
        return str(resolved)

    def session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace_path = self._normalize_workspace_path(params.get("workspacePath") or params.get("cwd"))
        title = str(params.get("title") or "V8OS ACP Session").strip()
        metadata = {
            "source": "acp_bridge",
            "scopeHint": params.get("scopeHint"),
        }
        ref = self.backend.create_session(title=title, workspace_path=workspace_path, metadata=metadata)
        acp_session_id = f"acp_{uuid.uuid4().hex[:16]}"
        session = AcpSession(
            acp_session_id=acp_session_id,
            v8_session_id=ref.session_id,
            workspace_path=workspace_path or ref.workspace_path,
            title=title,
            metadata={"source": "acp_bridge"},
        )
        self.sessions[acp_session_id] = session
        return self._session_payload(session, created=True)

    def session_load(self, params: dict[str, Any]) -> dict[str, Any]:
        acp_session_id = str(params.get("sessionId") or params.get("acpSessionId") or "").strip()
        if acp_session_id and acp_session_id in self.sessions:
            return self._session_payload(self.sessions[acp_session_id], created=False)
        v8_session_id = str(params.get("v8SessionId") or params.get("conversationId") or params.get("sessionId") or "").strip()
        if not v8_session_id:
            raise JsonRpcError(-32602, "session/load requires sessionId or v8SessionId.")
        ref = self.backend.load_session(session_id=v8_session_id)
        acp_session_id = acp_session_id if acp_session_id.startswith("acp_") else f"acp_{uuid.uuid4().hex[:16]}"
        session = AcpSession(
            acp_session_id=acp_session_id,
            v8_session_id=ref.session_id,
            workspace_path=ref.workspace_path,
            title=ref.title,
            metadata={"source": "acp_bridge", "loaded": True},
        )
        self.sessions[acp_session_id] = session
        return self._session_payload(session, created=False)

    def session_prompt(self, params: dict[str, Any]) -> tuple[dict[str, Any], list[JsonRpcMessage]]:
        session = self._require_session(params)
        prompt = str(params.get("prompt") or params.get("message") or "").strip()
        if not prompt:
            raise JsonRpcError(-32602, "session/prompt requires prompt.")
        result = self.backend.submit_prompt(
            session_id=session.v8_session_id,
            prompt=prompt,
            metadata={
                "source": "acp_bridge",
                "acpSessionId": session.acp_session_id,
                "workspacePath": session.workspace_path,
                "clientMessageId": str(params.get("clientMessageId") or f"acp_msg_{uuid.uuid4().hex[:12]}"),
            },
        )
        session.current_run_id = result.run_id
        session.last_client_message_id = str(params.get("clientMessageId") or "").strip() or session.last_client_message_id
        notifications = self._updates_for_prompt_result(session, result)
        return {
            "accepted": result.accepted,
            "sessionId": session.acp_session_id,
            "v8SessionId": result.session_id,
            "runId": result.run_id,
            "_meta": {
                "v8os": {
                    "sessionId": result.session_id,
                    "runId": result.run_id,
                    "workspacePath": session.workspace_path,
                }
            },
        }, notifications

    def session_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(params)
        requested_run_id = str(params.get("runId") or params.get("run_id") or session.current_run_id or "").strip() or None
        result = self.backend.cancel_session(session_id=session.v8_session_id, run_id=requested_run_id)
        cancelled_run_id = str(result.get("runId") or result.get("run_id") or requested_run_id or "").strip() or None
        if cancelled_run_id and cancelled_run_id == session.current_run_id:
            session.current_run_id = None
        return {
            "ok": bool(result.get("ok", True)),
            "sessionId": session.acp_session_id,
            "v8SessionId": session.v8_session_id,
            "requestedRunId": requested_run_id,
            "cancelledRunId": cancelled_run_id,
            "status": result.get("status") or ("cancelled" if result.get("ok", True) else "cancel_failed"),
            "_meta": {"v8os": {"sessionId": session.v8_session_id, "runId": cancelled_run_id, "canonicalId": "acp_bridge"}},
        }

    def _require_session(self, params: dict[str, Any]) -> AcpSession:
        acp_session_id = str(params.get("sessionId") or params.get("acpSessionId") or "").strip()
        session = self.sessions.get(acp_session_id)
        if session:
            return session
        v8_session_id = str(params.get("v8SessionId") or params.get("conversationId") or "").strip()
        for existing in self.sessions.values():
            if existing.v8_session_id == v8_session_id:
                return existing
        raise JsonRpcError(-32602, "Unknown ACP session. Call session/new or session/load first.")

    def _session_payload(self, session: AcpSession, *, created: bool) -> dict[str, Any]:
        return {
            "sessionId": session.acp_session_id,
            "created": created,
            "title": session.title,
            "workspacePath": session.workspace_path,
            "_meta": {
                "v8os": {
                    "sessionId": session.v8_session_id,
                    "workspacePath": session.workspace_path,
                    "canonicalId": "acp_bridge",
                }
            },
        }

    def _updates_for_prompt_result(self, session: AcpSession, result: V8PromptResult) -> list[JsonRpcMessage]:
        updates = result.updates or []
        if not updates:
            return []
        messages: list[JsonRpcMessage] = []
        for update in updates:
            payload = markdown_update_from_v8(update)
            payload.setdefault("_meta", {}).setdefault("v8os", {})["sessionId"] = result.session_id
            messages.append(notification("session/update", {"sessionId": session.acp_session_id, "update": payload}))
        return messages

    def project_runtime_event(self, *, acp_session_id: str, event: dict[str, Any]) -> JsonRpcMessage:
        return notification("session/update", {"sessionId": acp_session_id, "update": compact_runtime_event(event)})

    def terminal_create(self, params: dict[str, Any]) -> dict[str, Any]:
        from core.client_terminal_broker import create_terminal_session

        session = None
        try:
            session = self._require_session(params)
        except JsonRpcError:
            pass
        snapshot = create_terminal_session(
            profile_id=params.get("profileId"),
            cwd=str(params.get("cwd") or (session.workspace_path if session else "") or "").strip() or None,
            conversation_id=session.v8_session_id if session else None,
        )
        return self._terminal_payload(snapshot)

    def terminal_input(self, params: dict[str, Any]) -> dict[str, Any]:
        from core.client_terminal_broker import write_terminal_session_input

        terminal_id = str(params.get("terminalId") or params.get("sessionId") or "").strip()
        if not terminal_id:
            raise JsonRpcError(-32602, "_v8os/terminal/input requires terminalId.")
        snapshot = write_terminal_session_input(terminal_id, str(params.get("input") or params.get("data") or ""))
        return self._terminal_payload(snapshot)

    def terminal_resize(self, params: dict[str, Any]) -> dict[str, Any]:
        from core.client_terminal_broker import resize_terminal_session

        terminal_id = str(params.get("terminalId") or params.get("sessionId") or "").strip()
        if not terminal_id:
            raise JsonRpcError(-32602, "_v8os/terminal/resize requires terminalId.")
        snapshot = resize_terminal_session(
            terminal_id,
            cols=int(params.get("cols") or 80),
            rows=int(params.get("rows") or 24),
        )
        return self._terminal_payload(snapshot)

    def terminal_kill(self, params: dict[str, Any]) -> dict[str, Any]:
        from core.client_terminal_broker import terminate_terminal_session

        terminal_id = str(params.get("terminalId") or params.get("sessionId") or "").strip()
        if not terminal_id:
            raise JsonRpcError(-32602, "_v8os/terminal/kill requires terminalId.")
        snapshot = terminate_terminal_session(terminal_id)
        return self._terminal_payload(snapshot)

    def _terminal_payload(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": bool(snapshot.get("ok")),
            "terminalId": snapshot.get("sessionId"),
            "status": snapshot.get("status"),
            "output": snapshot.get("outputDelta") or "",
            "screen": snapshot.get("screenSnapshot") or "",
            "isRunning": bool(snapshot.get("isRunning")),
            "cols": snapshot.get("cols"),
            "rows": snapshot.get("rows"),
            "_meta": {
                "v8os": {
                    "commandId": snapshot.get("commandId"),
                    "cwd": snapshot.get("cwd"),
                    "canonicalId": "client_terminal_broker",
                }
            },
        }

    def permission_classify(self, params: dict[str, Any]) -> dict[str, Any]:
        kind = permission_event_kind(params.get("kind") or params.get("type") or params.get("event"))
        return {
            "kind": kind,
            "mapsToAcpPermission": kind == "permission",
            "mapsToAskUser": kind == "ask_user",
            "mapsToSpecApproval": kind == "spec_approval",
        }

    def permission_request(self, params: dict[str, Any]) -> tuple[dict[str, Any], list[JsonRpcMessage]]:
        kind = permission_event_kind(params.get("kind") or params.get("type") or params.get("event"))
        if kind != "permission":
            return {
                **self.permission_classify(params),
                "status": "not_acp_permission",
                "recommendedChannel": "ask_user" if kind == "ask_user" else "spec_approval" if kind == "spec_approval" else "diagnostic",
            }, []
        session = self._require_session(params)
        permission_id = str(params.get("permissionId") or f"perm_{uuid.uuid4().hex[:12]}").strip()
        acp_request_id = f"acp_permission_{permission_id}"
        reason = str(params.get("reason") or params.get("summary") or params.get("message") or "V8OS 需要一次安全授权。").strip()
        request_payload = {
            "permissionId": permission_id,
            "requestId": acp_request_id,
            "sessionId": session.acp_session_id,
            "status": "pending",
            "title": str(params.get("title") or "需要授权").strip(),
            "reason": reason,
            "action": str(params.get("action") or "").strip() or None,
            "target": str(params.get("target") or "").strip() or None,
            "toolCall": {
                "toolCallId": str(params.get("toolCallId") or params.get("tool_call_id") or f"call_v8_{permission_id}"),
                "title": str(params.get("title") or params.get("action") or "V8OS 安全授权").strip(),
                "kind": str(params.get("action") or "permission").strip(),
                "status": "pending",
            },
            "options": [
                {"id": "approve", "optionId": "approve", "label": "同意并继续", "name": "同意并继续", "kind": "allow_once"},
                {"id": "deny", "optionId": "deny", "label": "拒绝", "name": "拒绝", "kind": "reject_once"},
            ],
            "_meta": {
                "v8os": {
                    "sessionId": session.v8_session_id,
                    "runId": session.current_run_id,
                    "detailRef": params.get("detailRef") or params.get("rawRef"),
                    "canonicalId": "acp_bridge.permission",
                }
            },
        }
        session.pending_permissions[permission_id] = request_payload
        update = {
            "role": "assistant",
            "kind": "permission_request",
            "status": "pending",
            "content": f"需要授权：{reason}",
            "_meta": request_payload["_meta"],
        }
        return request_payload, [
            JsonRpcMessage({"jsonrpc": "2.0", "id": acp_request_id, "method": "session/request_permission", "params": request_payload}),
            notification("session/update", {"sessionId": session.acp_session_id, "update": update}),
        ]

    def permission_respond(self, params: dict[str, Any]) -> tuple[dict[str, Any], list[JsonRpcMessage]]:
        session = self._require_session(params)
        permission_id = str(params.get("permissionId") or "").strip()
        if not permission_id:
            request_id = str(params.get("requestId") or "").strip()
            for candidate_id, record in session.pending_permissions.items():
                if record.get("requestId") == request_id:
                    permission_id = candidate_id
                    break
        if not permission_id or permission_id not in session.pending_permissions:
            raise JsonRpcError(-32602, "_v8os/permission/respond requires a pending permissionId.")
        decision = str(params.get("decision") or params.get("status") or "").strip().lower()
        approved = decision in {"approve", "approved", "allow", "allowed", "yes", "true"}
        record = session.pending_permissions.pop(permission_id)
        return self._permission_response_messages(session, permission_id, approved, params.get("comment"), record)

    def _handle_client_response(self, payload: dict[str, Any]) -> list[JsonRpcMessage]:
        request_id = str(payload.get("id") or "").strip()
        if not request_id:
            return []
        for session in self.sessions.values():
            for permission_id, record in list(session.pending_permissions.items()):
                if record.get("requestId") != request_id:
                    continue
                if payload.get("error"):
                    approved = False
                    comment = "ACP client returned an error for the permission request."
                else:
                    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                    outcome = result.get("outcome") if isinstance(result.get("outcome"), dict) else result
                    option = str(
                        outcome.get("optionId")
                        or outcome.get("option_id")
                        or outcome.get("decision")
                        or outcome.get("status")
                        or ""
                    ).strip().lower()
                    approved = option in {"approve", "approved", "allow", "allowed", "yes", "true"}
                    comment = str(outcome.get("comment") or result.get("comment") or "").strip() or None
                session.pending_permissions.pop(permission_id, None)
                _, notifications = self._permission_response_messages(session, permission_id, approved, comment, record)
                return notifications
        return []

    def _permission_response_messages(
        self,
        session: AcpSession,
        permission_id: str,
        approved: bool,
        comment: Any,
        record: dict[str, Any],
    ) -> tuple[dict[str, Any], list[JsonRpcMessage]]:
        result = {
            "permissionId": permission_id,
            "requestId": record.get("requestId"),
            "sessionId": session.acp_session_id,
            "status": "approved" if approved else "denied",
            "comment": str(comment or "").strip() or None,
            "_meta": record.get("_meta") or {},
        }
        update = {
            "role": "assistant",
            "kind": "permission_response",
            "status": result["status"],
            "content": "授权已通过，V8OS 可以继续。" if approved else "授权被拒绝，V8OS 会停止相关操作。",
            "_meta": result["_meta"],
        }
        return result, [notification("session/update", {"sessionId": session.acp_session_id, "update": update})]
