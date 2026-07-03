from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backend import AdminBffBackend, V8Backend, V8PromptResult
from .protocol import JsonRpcError, JsonRpcMessage, error_response, notification, require_object, result_response
from .surface import PRODUCT_AGENT_NAME, compact_runtime_event, markdown_update_from_v8, permission_event_kind


@dataclass
class AcpSession:
    acp_session_id: str
    v8_session_id: str
    workspace_path: str | None = None
    title: str | None = None
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
        raise JsonRpcError(-32601, f"Unsupported ACP method: {method}")

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": "0.1",
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
        result = self.backend.cancel_session(session_id=session.v8_session_id)
        return {
            "ok": bool(result.get("ok", True)),
            "sessionId": session.acp_session_id,
            "v8SessionId": session.v8_session_id,
            "status": result.get("status") or ("cancelled" if result.get("ok", True) else "cancel_failed"),
            "_meta": {"v8os": {"sessionId": session.v8_session_id, "canonicalId": "acp_bridge"}},
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
