from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class V8SessionRef:
    session_id: str
    workspace_path: str | None = None
    title: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class V8PromptUpdate:
    kind: str
    text: str
    status: str = "completed"
    role: str = "assistant"
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    file_changes: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    detail_ref: str | None = None
    raw_ref: str | None = None
    run_id: str | None = None
    tool_call_id: str | None = None
    episode_id: str | None = None


@dataclass
class V8PromptResult:
    accepted: bool
    session_id: str
    run_id: str | None = None
    updates: list[V8PromptUpdate] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class V8Backend(Protocol):
    def create_session(self, *, title: str | None, workspace_path: str | None, metadata: dict[str, Any]) -> V8SessionRef:
        ...

    def load_session(self, *, session_id: str) -> V8SessionRef:
        ...

    def submit_prompt(self, *, session_id: str, prompt: str, metadata: dict[str, Any]) -> V8PromptResult:
        ...

    def cancel_session(self, *, session_id: str, run_id: str | None = None) -> dict[str, Any]:
        ...


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
        engine_url: str | None = None,
        bearer_token: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.admin_url = (admin_url or os.environ.get("V8OS_ADMIN_URL") or "http://127.0.0.1:9528").rstrip("/")
        self.engine_url = (engine_url or os.environ.get("V8OS_ENGINE_URL") or "http://127.0.0.1:9530").rstrip("/")
        self.bearer_token = bearer_token or os.environ.get("V8OS_CLIENT_TOKEN") or os.environ.get("V8OS_ADMIN_TOKEN")
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                raise RuntimeError(
                    f"{method} {url} returned {exc.code}: ACP bridge is not authorized. "
                    "Set V8OS_CLIENT_TOKEN (preferred) or V8OS_ADMIN_TOKEN from the Admin connection card, "
                    f"then retry. Response: {body[:500]}"
                ) from exc
            raise RuntimeError(f"{method} {url} returned {exc.code}: {body[:800]}") from exc
        except Exception as exc:
            raise RuntimeError(f"{method} {url} failed: {exc}") from exc

    def create_session(self, *, title: str | None, workspace_path: str | None, metadata: dict[str, Any]) -> V8SessionRef:
        payload = {
            "title": title or "V8OS ACP Session",
            "workspacePath": workspace_path,
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
        data = self._request("GET", f"{self.admin_url}/api/client/conversations/{session_id}?omitMessages=1")
        return V8SessionRef(
            session_id=session_id,
            title=str((data.get("summary") or {}).get("title") or data.get("title") or "").strip() or None,
            raw=data,
        )

    def submit_prompt(self, *, session_id: str, prompt: str, metadata: dict[str, Any]) -> V8PromptResult:
        payload = {
            "conversationId": session_id,
            "sessionId": session_id,
            "messages": [{"role": "user", "content": prompt}],
            "clientMessageId": metadata.get("clientMessageId"),
            "workspacePath": metadata.get("workspacePath"),
            "data": {
                "externalSurface": "acp_bridge",
                "clientGroup": "acp_bridge",
                "source": "acp_bridge",
                "compatIngressDiagnostics": {
                    "source": "acp_bridge",
                    "externalSurface": "acp_bridge",
                    "acpSessionId": metadata.get("acpSessionId"),
                }
            },
        }
        data = self._request("POST", f"{self.admin_url}/api/client/chat-submit", payload)
        run_id = str(data.get("runId") or data.get("run_id") or "").strip() or None
        return V8PromptResult(
            accepted=bool(data.get("accepted", True)),
            session_id=str(data.get("session_id") or data.get("conversationId") or session_id),
            run_id=run_id,
            updates=[
                V8PromptUpdate(
                    kind="status",
                    text="V8OS 已接收请求，后续进度会通过会话实时事件返回。",
                    status="accepted",
                    run_id=run_id,
                )
            ],
            raw=data,
        )

    def cancel_session(self, *, session_id: str, run_id: str | None = None) -> dict[str, Any]:
        # Current run cancellation is owned by Engine run control. If the local
        # Admin BFF is not available, expose a clean no-op rather than inventing
        # ACP-owned cancellation state.
        try:
            payload = {"sessionId": session_id}
            if run_id:
                payload["runId"] = run_id
            return self._request("POST", f"{self.engine_url}/runs/cancel", payload)
        except Exception as exc:
            return {
                "ok": False,
                "sessionId": session_id,
                "runId": run_id,
                "reason": "cancel_endpoint_unavailable",
                "message": str(exc),
            }
