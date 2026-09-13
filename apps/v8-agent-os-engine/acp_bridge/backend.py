from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import urllib.parse
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any


@dataclass
class V8SessionRef:
    session_id: str
    workspace_path: str | None = None
    title: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class AdminBffBackend:
    """Small HTTP backend for the local Admin BFF / Engine public routes.

    The ACP bridge does not own V8OS runtime truth. This backend only forwards
    requests into the existing client BFF/Engine entry points. Tests normally
    inject a fake backend so the bridge contract stays deterministic.
    """

    def __init__(
        self,
        *,
        admin_url: str | None = None,
        bearer_token: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.admin_url = (admin_url or os.environ.get("V8OS_ADMIN_URL") or "http://127.0.0.1:9528").rstrip("/")
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds
        self._auth_lock = threading.Lock()

    def _authenticate(self) -> None:
        if self.bearer_token:
            return
        with self._auth_lock:
            if self.bearer_token:
                return
            target = urllib.parse.urlsplit(self.admin_url)
            if target.hostname not in {"localhost", "127.0.0.1", "::1"} or target.username or target.password:
                raise RuntimeError("ACP local authentication requires a loopback Admin URL.")
            request = urllib.request.Request(
                f"{self.admin_url}/api/client/auth/local-session",
                data=b'{"surface":"cli","deviceName":"v8os-acp"}',
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with self._opener().open(request, timeout=self.timeout_seconds) as response:
                record = json.load(response)
            token = record.get("accessToken")
            if not isinstance(token, str) or not token:
                raise RuntimeError("Local Admin did not issue an ACP client session. Initialize the owner in Admin first.")
            self.bearer_token = token

    @staticmethod
    def _opener():
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise RuntimeError("ACP does not forward authenticated requests across redirects.")
        return urllib.request.build_opener(NoRedirect())

    def _headers(self) -> dict[str, str]:
        self._authenticate()
        headers = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with self._opener().open(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            # Do not reflect response bodies: an upstream error may contain a
            # submitted prompt or credential. The status is enough to retry or
            # find the detailed error in the authenticated V8OS surface.
            raise RuntimeError(f"ACP Admin request failed: HTTP {exc.code} ({urllib.parse.urlsplit(url).path}).") from exc
        except Exception as exc:
            raise RuntimeError(f"{method} {url} failed: {exc}") from exc

    def create_session(self, *, title: str | None, workspace_path: str | None, metadata: dict[str, Any]) -> V8SessionRef:
        # The local editor selected cwd explicitly, just like `v8os chat
        # --workspace`. Reuse the same project/trust owner, without selecting or
        # mutating a global workspace. No ACP-owned grants or bypass flags.
        project = self._request("POST", f"{self.admin_url}/api/client/projects", {
            "name": Path(workspace_path).name, "workspacePath": workspace_path,
            "workspaceTrustState": "trusted", "workspaceTrustSource": "cli_user_confirmed",
        })
        project_id = project.get("id") or project.get("projectId")
        if not project_id or not project.get("workspaceId"):
            raise RuntimeError("Admin did not confirm the selected workspace project.")
        payload = {
            "title": title or "V8OS ACP Session",
            "workspacePath": workspace_path,
            "projectId": project_id,
            "workspaceId": project["workspaceId"],
            "scopeMode": "explicit",
            "scopeHint": metadata.get("scopeHint"),
            "externalSurface": "acp_bridge",
            "clientGroup": "acp_bridge",
            "source": "acp_bridge",
            "metadata": {
                **(metadata or {}),
                "source": "acp_bridge",
                "externalSurface": "acp_bridge",
                "clientGroup": "acp_bridge",
                "historyGroup": "external_agent_clients",
            },
        }
        data = self._request("POST", f"{self.admin_url}/api/client/conversations", payload)
        session_id = str(data.get("id") or data.get("sessionId") or data.get("conversationId") or "").strip()
        if not session_id:
            raise RuntimeError("Admin BFF did not return a session id.")
        return V8SessionRef(session_id=session_id, workspace_path=workspace_path, title=title, raw=data)

    def load_session(self, *, session_id: str) -> V8SessionRef:
        data = self._request("GET", f"{self.admin_url}/api/client/conversations/{urllib.parse.quote(session_id, safe='')}")
        summary = data.get("summary") or {}
        metadata = summary.get("metadata") or {}
        scope = metadata.get("resolved_scope") or metadata.get("scopeBinding") or {}
        workspace = metadata.get("workspace_path") or metadata.get("workspacePath") or scope.get("workspace_path") or scope.get("workspacePath")
        if not workspace:
            for message in reversed(data.get("messages") or []):
                workspace = (message.get("metadata") or {}).get("workspace_path") or (message.get("metadata") or {}).get("workspacePath")
                if workspace:
                    break
        return V8SessionRef(
            session_id=session_id,
            workspace_path=workspace or summary.get("workspacePath"),
            title=str((data.get("summary") or {}).get("title") or data.get("title") or "").strip() or None,
            raw=data,
        )

    def cancel_session(self, *, session_id: str, run_id: str | None = None) -> dict[str, Any]:
        if not run_id:
            raise RuntimeError("Cannot cancel a run before its Engine identity is known.")
        return self._request("POST", f"{self.admin_url}/api/client/runs/{urllib.parse.quote(run_id, safe='')}/commands/cancel", {"reason": "acp_client_cancel"})

    def stream_prompt(self, *, session_id: str, prompt: str, metadata: dict[str, Any]):
        payload = {
            "session_id": session_id, "conversationId": session_id,
            "clientMessageId": metadata["clientMessageId"],
            "workspacePath": metadata.get("workspacePath"),
            "messages": [{"role": "user", "content": prompt}],
            "attachments": metadata.get("attachments") or [],
            "data": {"safetyApprovalMode": metadata.get("safetyApprovalMode", "reduced")},
        }
        yield from self._stream("POST", "/api/client/chat", payload)

    def stream_session(self, *, session_id: str):
        yield from self._stream("GET", f"/api/client/realtime/sessions/{urllib.parse.quote(session_id, safe='')}/stream", sse=True)

    def _stream(self, method: str, path: str, payload=None, *, sse=False):
        request = urllib.request.Request(
            f"{self.admin_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None,
            headers=self._headers(), method=method,
        )
        # Transport idle timeout, not an output/token/task budget. No automatic
        # POST retry: a lost response may already have started a real run.
        with self._opener().open(request, timeout=max(self.timeout_seconds, 300)) as response:
            if response.headers.get_content_type() not in {"application/x-ndjson", "text/event-stream"}:
                raise RuntimeError("ACP expected a V8OS event stream, not an acknowledgement.")
            event_name = "message"
            data_lines = []
            for raw in response:
                line = raw.decode("utf-8").rstrip("\r\n")
                if sse:
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        event = json.loads("\n".join(data_lines))
                        if event_name == "snapshot":
                            event = {"type": "snapshot", "data": event}
                        elif event_name == "error":
                            event = {"type": "error", "error": "V8OS event stream failed."}
                        yield event
                        data_lines = []
                        event_name = "message"
                elif line:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise RuntimeError("V8OS returned a non-object stream event.")
                    yield event

    def respond_permission(self, *, permission_id: str, approved: bool) -> dict[str, Any]:
        action = "approve" if approved else "reject"
        return self._request("POST", f"{self.admin_url}/api/client/approvals/{urllib.parse.quote(permission_id, safe='')}/{action}", {})

    def pending_interaction(self, *, session_id: str, run_id: str, kind: str) -> dict:
        if kind == "approval_requested":
            query = urllib.parse.urlencode({"session_id": session_id, "run_id": run_id, "status": "pending"})
            data = self._request("GET", f"{self.admin_url}/api/client/approvals?{query}")
            records = data.get("approvals") or []
        else:
            records = self.load_session(session_id=session_id).raw.get("askUserInteractions") or []
        for record in records:
            if (record.get("run_id") or record.get("runId")) == run_id and record.get("status") in {"pending", "waiting", "open"}:
                return {"type": kind, "runId": run_id, "data": record}
        raise RuntimeError("Engine reports a waiting run without a corresponding pending interaction.")

    def respond_ask_user(self, *, interaction_id: str, answer: str) -> dict[str, Any]:
        return self._request("POST", f"{self.admin_url}/api/client/ask-user/{urllib.parse.quote(interaction_id, safe='')}/respond", {"answer": answer})
