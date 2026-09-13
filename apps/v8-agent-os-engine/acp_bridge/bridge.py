from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .backend import AdminBffBackend
from .content import event_data, event_kind, event_run_id, history_updates, prompt_text, runtime_update, text_update
from .launch import ACP_PROTOCOL_VERSION, build_launch_manifest
from .protocol import JsonRpcError, JsonRpcMessage, error_response, notification, require_object, result_response


@dataclass
class AcpSession:
    acp_session_id: str
    v8_session_id: str
    workspace_path: str | None = None
    current_run_id: str | None = None
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    prompt_lock: threading.Lock = field(default_factory=threading.Lock)
    pending_input: dict | None = None
    pending_approval: dict | None = None
    safety_mode: str = "reduced"


class AcpBridge:
    """ACP v1 adapter; Engine owns runs, permissions, history and recovery."""

    def __init__(self, backend=None):
        self.backend = backend or AdminBffBackend()
        self.sessions: dict[str, AcpSession] = {}
        self.client_capabilities: dict = {}
        self.initialized = False
        self._pending: dict[str, tuple[threading.Event, dict]] = {}
        self._pending_lock = threading.Lock()

    def handle_json_rpc(self, payload, emit=None):
        messages = []
        send = emit or messages.append
        request_id = payload.get("id")
        try:
            if payload.get("jsonrpc") != "2.0":
                raise JsonRpcError(-32600, "Only JSON-RPC 2.0 is supported.")
            method = payload.get("method")
            if method is None and request_id is not None and ("result" in payload or "error" in payload):
                with self._pending_lock:
                    pending = self._pending.get(str(request_id))
                    if pending:
                        pending[1].update(payload)
                        pending[0].set()
                return []
            if not isinstance(method, str) or not method:
                raise JsonRpcError(-32600, "Missing method.")
            params = require_object(payload.get("params"))
            if method == "initialize":
                result = self.initialize(params)
            elif not self.initialized:
                raise JsonRpcError(-32000, "Call initialize before session methods.")
            elif method == "session/new":
                result = self.session_new(params)
            elif method in {"session/load", "session/resume"}:
                result = self.session_load(params, send, replay=method == "session/load")
            elif method == "session/prompt":
                result = self.session_prompt(params, send)
            elif method == "session/cancel":
                result = self.session_cancel(params)
            elif method == "session/set_mode":
                session = self._session(params)
                if session.prompt_lock.locked():
                    raise JsonRpcError(-32000, "Wait for the active prompt before changing its permission mode.")
                if params.get("modeId") not in {"manual", "reduced", "minimal"}:
                    raise JsonRpcError(-32602, "Unknown permission mode.")
                session.safety_mode = params["modeId"]
                result = {}
            else:
                raise JsonRpcError(-32601, f"Unsupported ACP method: {method}")
            if request_id is not None:
                send(result_response(request_id, result))
        except JsonRpcError as exc:
            if request_id is not None:
                send(error_response(request_id, exc))
        except Exception:
            if request_id is not None:
                send(error_response(request_id, JsonRpcError(-32000, "V8OS operation failed. Check the authenticated Admin task details.")))
        return messages

    def initialize(self, params):
        if not isinstance(params.get("protocolVersion"), int) or isinstance(params.get("protocolVersion"), bool):
            raise JsonRpcError(-32602, "protocolVersion must be an integer.")
        self.client_capabilities = require_object(params.get("clientCapabilities"))
        self.initialized = True
        return {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "agentInfo": {"name": "v8os", "title": "V8OS", "version": "0.1.0"},
            "agentCapabilities": {
                "loadSession": True, "sessionCapabilities": {"resume": {}},
                "promptCapabilities": {"image": False, "audio": False, "embeddedContext": True},
                "mcpCapabilities": {},
            },
            "authMethods": [],
            "_meta": {"v8os": {"canonicalId": "acp_bridge", "launch": build_launch_manifest(),
                                  "mcpServerConfiguration": "Use V8OS Plugin Manager; client-supplied servers are not yet supported."}},
        }

    def _workspace(self, params):
        if not isinstance(params.get("mcpServers", []), list):
            raise JsonRpcError(-32602, "mcpServers must be an array.")
        if params.get("mcpServers"):
            raise JsonRpcError(-32602, "Client-supplied MCP servers are not yet supported; configure them in V8OS Plugin Manager.")
        value = params.get("cwd")
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise JsonRpcError(-32602, "cwd must be an absolute workspace path.")
        if not Path(value).is_dir():
            raise JsonRpcError(-32602, "cwd must be an existing directory.")
        return str(Path(value).resolve())

    def session_new(self, params):
        workspace = self._workspace(params)
        ref = self.backend.create_session(title="V8OS ACP Session", workspace_path=workspace, metadata={"source": "acp_bridge"})
        # The Engine ID survives ACP process restarts; no in-memory alias owner.
        self.sessions[ref.session_id] = AcpSession(ref.session_id, ref.session_id, workspace)
        return {"sessionId": ref.session_id, "modes": self._modes(self.sessions[ref.session_id])}

    @staticmethod
    def _modes(session):
        return {"currentModeId": session.safety_mode, "availableModes": [
            {"id": "manual", "name": "Ask for approval"},
            {"id": "reduced", "name": "Reduced approvals"},
            {"id": "minimal", "name": "Full access", "description": "Explicitly allow ordinary operations; OS/V8 core, credentials and task boundaries remain protected."},
        ]}

    def session_load(self, params, send, *, replay=True):
        workspace = self._workspace(params)
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise JsonRpcError(-32602, "sessionId is required.")
        existing = self.sessions.get(session_id)
        if existing and existing.prompt_lock.locked():
            raise JsonRpcError(-32000, "The session already has an active prompt.")
        ref = self.backend.load_session(session_id=session_id)
        if not ref.workspace_path:
            raise JsonRpcError(-32000, "The stored session workspace could not be resolved; select its workspace in V8OS before loading it.")
        if Path(ref.workspace_path).resolve() != Path(workspace):
            raise JsonRpcError(-32602, "cwd does not match the stored session workspace.")
        session = AcpSession(session_id, ref.session_id, ref.workspace_path or workspace)
        current_run = ref.raw.get("currentRun") or {}
        session.current_run_id = current_run.get("id") or current_run.get("runId")
        stored_mode = (current_run.get("metadata") or {}).get("safetyApprovalMode")
        if stored_mode in {"manual", "reduced", "minimal"}:
            session.safety_mode = stored_mode
        for interaction in ref.raw.get("askUserInteractions") or []:
            if interaction.get("status") in {"pending", "waiting", "open"} and (interaction.get("run_id") or interaction.get("runId")) == session.current_run_id:
                session.pending_input = {"id": interaction.get("id") or interaction.get("interactionId")}
                break
        for approval in ref.raw.get("approvals") or []:
            if approval.get("status") == "pending" and (approval.get("run_id") or approval.get("runId")) == session.current_run_id:
                session.pending_approval = approval
                break
        self.sessions[session_id] = session
        if replay:
            for update in history_updates(ref.raw):
                self._send_update(session, update, send)
        return {"modes": self._modes(session)}

    def _session(self, params):
        session = self.sessions.get(params.get("sessionId"))
        if not session:
            raise JsonRpcError(-32602, "Unknown session. Call session/new or session/load.")
        return session

    @staticmethod
    def _send_update(session, update, send):
        send(notification("session/update", {"sessionId": session.acp_session_id, "update": update}))

    def session_cancel(self, params):
        session = self._session(params)
        session.cancel_requested.set()
        if session.current_run_id:
            result = self.backend.cancel_session(session_id=session.v8_session_id, run_id=session.current_run_id)
            if result.get("ok") is False:
                raise JsonRpcError(-32000, "Engine could not cancel the run.")
        return {}

    def _request_client(self, session, method, params, send):
        request_id = f"v8os_{uuid.uuid4().hex}"
        ready, response = threading.Event(), {}
        with self._pending_lock:
            self._pending[request_id] = (ready, response)
        try:
            send(JsonRpcMessage({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
            while not ready.wait(0.1):
                if session.cancel_requested.is_set():
                    return None
            if response.get("error"):
                raise JsonRpcError(-32000, "ACP client could not complete the interaction.")
            return require_object(response.get("result"), field_name="client result")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def _interaction(self, session, event, send):
        data = event_data(event)
        kind = event_kind(event)
        request = data.get("request") if isinstance(data.get("request"), dict) else data
        interaction_id = data.get("id") or data.get("approvalId") or data.get("interactionId") or request.get("interactionId")
        if not interaction_id:
            raise JsonRpcError(-32000, "V8OS interaction has no persistent identity.")
        question = str(request.get("question") or request.get("summary") or request.get("reason") or data.get("title") or "V8OS needs your input.")
        spec = str(data.get("approvalKind") or data.get("approval_kind") or request.get("approvalKind") or "").startswith("spec")
        if spec:
            self._send_update(session, text_update(f"{question}\n\nComplete this Spec stage decision in V8OS Admin."), send)
            raise JsonRpcError(-32000, "A Spec stage decision is waiting in Admin; it is not a tool safety permission.")
        if kind == "ask_user":
            elicitation = self.client_capabilities.get("elicitation") or {}
            if elicitation.get("form") is None:
                session.pending_input = {"id": interaction_id}
                self._send_update(session, text_update(f"{question}\n\nPlease reply to continue."), send)
                return "input_required"
            result = self._request_client(session, "elicitation/create", {
                "sessionId": session.acp_session_id, "mode": "form", "message": question,
                "requestedSchema": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
            }, send)
            if result is None:
                self.session_cancel({"sessionId": session.acp_session_id})
                return "resuming"
            if result.get("action") != "accept":
                session.pending_input = {"id": interaction_id}
                return "input_required"
            answer = (result.get("content") or {}).get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise JsonRpcError(-32602, "The elicitation answer must be a non-empty string.")
            result = self.backend.respond_ask_user(interaction_id=str(interaction_id), answer=answer)
        else:
            tool_id = str(data.get("toolCallId") or request.get("toolCallId") or f"approval:{interaction_id}")
            summary = request.get("eventSummary") or data.get("eventSummary") or {}
            summary = summary if isinstance(summary, dict) else {}
            target = summary.get("target") or summary.get("targetLabel") or request.get("target")
            action = summary.get("action") or request.get("action")
            result = self._request_client(session, "session/request_permission", {
                "sessionId": session.acp_session_id,
                "toolCall": {"toolCallId": tool_id, "title": question, "kind": "other", "status": "pending",
                             "content": [{"type": "content", "content": {"type": "text", "text": question}}],
                             "rawInput": {"target": target, "action": action, "riskCode": request.get("riskCode")}},
                "options": [{"optionId": "approve", "name": "Allow once", "kind": "allow_once"},
                            {"optionId": "deny", "name": "Reject", "kind": "reject_once"}],
            }, send)
            if result is None or (result.get("outcome") or {}).get("outcome") == "cancelled":
                self.session_cancel({"sessionId": session.acp_session_id})
                return "resuming"
            outcome = result.get("outcome") or {}
            if outcome.get("outcome") != "selected" or outcome.get("optionId") not in {"approve", "deny"}:
                raise JsonRpcError(-32602, "Permission response selected an unknown option.")
            result = self.backend.respond_permission(permission_id=str(interaction_id), approved=outcome["optionId"] == "approve")
            if result.get("ok") is False:
                raise JsonRpcError(-32000, "V8OS did not accept the permission response.")
            if outcome["optionId"] == "deny":
                return "refusal"
        if result.get("ok") is False or result.get("resume_scheduled") is False:
            raise JsonRpcError(-32000, "V8OS could not resume after the response; the task is still waiting.")
        session.current_run_id = result.get("resumed_run_id") or result.get("runId") or session.current_run_id
        return "resuming"

    def session_prompt(self, params, send):
        session = self._session(params)
        text = prompt_text(params.get("prompt"))
        if not session.prompt_lock.acquire(blocking=False):
            raise JsonRpcError(-32000, "A prompt is already running in this session.")
        session.cancel_requested.clear()
        seen_tools, seen_events, seen_interactions = set(), set(), set()
        delivered_text: dict[str, str] = {}
        try:
            if session.pending_approval:
                outcome = self._interaction(session, {"type": "approval_requested", "data": session.pending_approval}, send)
                if outcome == "refusal":
                    session.pending_approval = None
                    return {"stopReason": "refusal"}
                session.pending_approval = None
                stream = self.backend.stream_session(session_id=session.v8_session_id)
            elif session.pending_input:
                response = self.backend.respond_ask_user(interaction_id=str(session.pending_input["id"]), answer=text)
                if response.get("ok") is False:
                    raise JsonRpcError(-32000, "V8OS rejected the user response.")
                session.pending_input = None
                session.current_run_id = response.get("resumed_run_id") or response.get("runId") or session.current_run_id
                stream = self.backend.stream_session(session_id=session.v8_session_id)
            else:
                session.current_run_id = None
                stream = self.backend.stream_prompt(session_id=session.v8_session_id, prompt=text, metadata={
                    "clientMessageId": f"acp_msg_{uuid.uuid4().hex}", "workspacePath": session.workspace_path, "safetyApprovalMode": session.safety_mode,
                })
            resumed = False
            while True:
                terminal = None
                for event in stream:
                    if not isinstance(event, dict):
                        raise JsonRpcError(-32000, "Invalid V8OS stream event.")
                    data = event_data(event)
                    kind = event_kind(event)
                    run_id = event_run_id(event)
                    if run_id and not session.current_run_id:
                        session.current_run_id = run_id
                        if session.cancel_requested.is_set():
                            self.session_cancel({"sessionId": session.acp_session_id})
                    if kind == "snapshot":
                        run = data.get("currentRun") or {}
                        if (run.get("id") or run.get("runId")) != session.current_run_id:
                            continue
                        state = run.get("status")
                        if state in {"completed", "succeeded", "finished", "failed", "cancelled", "canceled"}:
                            updates = [update for update in history_updates(data)
                                       if update["sessionUpdate"] == "agent_message_chunk"
                                       and (update.get("_meta") or {}).get("v8os", {}).get("runId") == session.current_run_id]
                            stored_text = "".join(update["content"]["text"] for update in updates)
                            delivered = delivered_text.get(session.current_run_id, "")
                            if stored_text and not stored_text.startswith(delivered):
                                raise JsonRpcError(-32000, "Live output differs from persisted history; reload the session to recover its canonical content.")
                            if stored_text[len(delivered):]:
                                self._send_update(session, text_update(stored_text[len(delivered):], message_id=updates[-1].get("messageId")), send)
                                delivered_text[session.current_run_id] = stored_text
                            terminal = state
                            break
                        continue
                    if session.current_run_id and run_id and run_id != session.current_run_id:
                        continue
                    identity = event.get("eventId") or event.get("event_id") or event.get("seq")
                    if identity:
                        if identity in seen_events:
                            continue
                        seen_events.add(identity)
                    if kind in {"approval_requested", "ask_user"}:
                        if str(event.get("status") or data.get("status") or "pending") not in {"pending", "waiting", "open", "requested"}:
                            continue
                        interaction_key = (kind, str(data.get("id") or data.get("approvalId") or data.get("interactionId") or ""))
                        if interaction_key in seen_interactions:
                            continue
                        seen_interactions.add(interaction_key)
                        outcome = self._interaction(session, event, send)
                        if outcome == "input_required":
                            return {"stopReason": "end_turn", "_meta": {"v8os": {"status": "waiting_input", "complete": False}}}
                        if outcome == "refusal":
                            terminal = "refusal"
                            break
                        resumed = True
                        continue
                    if kind == "error":
                        raise JsonRpcError(-32000, "V8OS run failed; inspect the task in Admin.", {"runId": session.current_run_id})
                    if kind == "done":
                        status = str(event.get("status") or data.get("status") or data.get("state") or "")
                        if not resumed and status in {"waiting_approval", "waiting_input"}:
                            pending = self.backend.pending_interaction(
                                session_id=session.v8_session_id, run_id=session.current_run_id,
                                kind="approval_requested" if status == "waiting_approval" else "ask_user",
                            )
                            outcome = self._interaction(session, pending, send)
                            if outcome == "input_required":
                                return {"stopReason": "end_turn", "_meta": {"v8os": {"status": "waiting_input", "complete": False}}}
                            if outcome == "refusal":
                                terminal = "refusal"
                                break
                            resumed = True
                        if resumed or status in {"running", "queued", "waiting_runtime"}:
                            resumed = True
                            break
                        terminal = status or "unknown"
                        break
                    update = runtime_update(event, seen_tools=seen_tools)
                    if update:
                        if update["sessionUpdate"] == "agent_message_chunk":
                            delivered_text[session.current_run_id] = delivered_text.get(session.current_run_id, "") + update["content"]["text"]
                        self._send_update(session, update, send)
                stream.close()
                if terminal:
                    if terminal in {"failed", "error", "interrupted", "aborted"}:
                        raise JsonRpcError(-32000, f"V8OS run ended with status {terminal}.", {"runId": session.current_run_id})
                    if terminal not in {"completed", "succeeded", "finished", "cancelled", "canceled", "refusal", "end_turn", "max_tokens", "max_turn_requests"}:
                        raise JsonRpcError(-32000, f"V8OS did not confirm completion: {terminal}.")
                    stop = "cancelled" if terminal in {"cancelled", "canceled"} else "refusal" if terminal == "refusal" else terminal if terminal in {"max_tokens", "max_turn_requests"} else "end_turn"
                    return {"stopReason": stop, "_meta": {"v8os": {"runId": session.current_run_id, "status": terminal}}}
                if not resumed:
                    raise JsonRpcError(-32000, "V8OS stream ended without a terminal event; task outcome is unknown.", {"runId": session.current_run_id})
                stream = self.backend.stream_session(session_id=session.v8_session_id)
                resumed = False
        finally:
            if "stream" in locals():
                stream.close()
            session.prompt_lock.release()
