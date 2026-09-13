from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import socket
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import HTTPException, WebSocket

from api.models import ChatRequest
from core.database import db
from core.network_compat_pending import NetworkCompatPendingStore
from core.run_ledger import run_ledger_service
from core.storage import storage
from core.v8_link import resolve_peer_transport_endpoint
from core.v8_agent_os_paths import (
    NETWORK_SUPERVISOR_SECRETS_PATH,
    NETWORK_SUPERVISOR_STATE_PATH,
    V8_AGENT_OS_HOME,
)
from erc.kernel import erc_kernel
from erc.models import RuntimeSource
from erc.run_service import run_service
from erc.runtime_context import get_runtime_context
from erc.workflow_ledger import workflow_ledger_service
from runtimes.network_supervisor.compat_ingress_filter import get_recent_compat_ingress_events
from runtimes.network_supervisor.models import (
    NetworkEnvelope,
    NetworkRelayConfig,
    NetworkSupervisorRuntimeConfig,
    NetworkTraceContext,
    NetworkPeerMutationPayload,
    NetworkDiagnosticsPayload,
    NetworkDelegationRequestPayload,
    TrustedPeerConfig,
)
from runtimes.network_supervisor.neighbor_workspace import resolve_network_neighbor_workspace_binding


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: Optional[datetime] = None) -> str:
    target = dt or _utc_now()
    return target.isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    normalized = str(value or "").strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(raw: str) -> str:
    return hashlib.sha256(str(raw or "").encode("utf-8")).hexdigest()[:16]


def _generate_peer_id() -> str:
    return f"peer_{uuid.uuid4().hex[:12]}"


def _join_url(base_url: str, path: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    suffix = str(path or "").strip() or "/"
    if not suffix.startswith("/"):
        suffix = f"/{suffix}"
    return f"{base}{suffix}" if base else suffix


def _default_ws_url(base_url: str, path: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if base.startswith("https://"):
        base = f"wss://{base.removeprefix('https://')}"
    elif base.startswith("http://"):
        base = f"ws://{base.removeprefix('http://')}"
    return _join_url(base, path)


def _state_default() -> dict[str, Any]:
    return {
        "discoveredPeers": {},
        "delegations": {},
        "neighborPairingInvites": {},
        "neighborPairingConsumed": {},
        "seenNonces": {},
    }


def _secrets_default() -> dict[str, Any]:
    return {
        "privateKey": "",
        "publicKey": "",
        "publicKeyFingerprint": "",
        "localPeerToken": "",
        "peerTokens": {},
        "openaiCompatTokens": [],
    }


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, service: "NetworkSupervisorService") -> None:
        self.service = service

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.service.handle_discovery_packet(data, addr)


class NetworkSupervisorService:
    protocol_version = "1"

    def __init__(self) -> None:
        self._started = False
        self._lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = None
        self._announce_task: asyncio.Task | None = None
        self._bootstrap_task: asyncio.Task | None = None
        self._discovery_transport: asyncio.DatagramTransport | None = None
        self._discovery_socket: socket.socket | None = None
        self._discovery_sender: socket.socket | None = None
        self._waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._active_inbound_tasks: dict[str, asyncio.Task] = {}
        self._seen_nonces: dict[str, float] = {}
        self._last_announce_at: str | None = None
        self._discovery_error: str | None = None
        self._advertised_endpoint_cache: tuple[tuple[str, str, str, str], float, dict[str, str]] | None = None
        self._compat_rate_state: dict[str, deque[float]] = {}
        self._compat_active_counts: dict[str, int] = {}

    def _ensure_runtime_dir(self) -> None:
        V8_AGENT_OS_HOME.mkdir(parents=True, exist_ok=True)
        NETWORK_SUPERVISOR_SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        NETWORK_SUPERVISOR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    def read_state(self) -> dict[str, Any]:
        self._ensure_runtime_dir()
        self._pending_store().ensure_migrated()
        if not NETWORK_SUPERVISOR_STATE_PATH.exists():
            self.write_state(_state_default())
        try:
            payload = json.loads(NETWORK_SUPERVISOR_STATE_PATH.read_text(encoding="utf-8"))
            payload.pop("pendingExternalTools", None)
            return payload
        except Exception:
            return _state_default()

    def write_state(self, payload: dict[str, Any]) -> None:
        self._ensure_runtime_dir()
        # Import before retiring the legacy field; other domain snapshots must
        # never overwrite the database's pending waits or consumed tombstones.
        self._pending_store().ensure_migrated()
        payload = {key: value for key, value in payload.items() if key != "pendingExternalTools"}
        NETWORK_SUPERVISOR_STATE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _pending_store(self) -> NetworkCompatPendingStore:
        return NetworkCompatPendingStore(db, NETWORK_SUPERVISOR_STATE_PATH)

    @contextmanager
    def _pending_transaction(self):
        abandoned = []
        with self._pending_store().transaction() as pending:
            now_ts = time.time()
            for item in pending.values():
                expires_at = float(item.get("expiresAtTs") or 0)
                if item.get("status") == "waiting_external_tool" and expires_at and expires_at < now_ts:
                    item.update(status="external_tool_abandoned", abandonedAt=_utc_iso(), lastReason="expired_waiting_for_client_tool_result")
                    abandoned.append(dict(item))
            yield pending
        # Run/ledger updates use their own DB connections, after the pending
        # transaction releases its lock. They cannot reverse a claimed result.
        for item in abandoned:
            self._complete_abandoned_external_tool_run(item)

    def pending_external_tools_snapshot(self) -> dict[str, dict[str, Any]]:
        pending = self._pending_store().snapshot()
        now_ts = time.time()
        if not any(item.get("status") == "waiting_external_tool" and 0 < float(item.get("expiresAtTs") or 0) < now_ts
                   for item in pending.values()):
            for item in pending.values():
                if item.get("status") == "external_tool_abandoned":
                    self._complete_abandoned_external_tool_run(item)
            return pending
        with self._pending_transaction() as pending:
            return pending

    def _external_tool_pending_key(
        self,
        protocol: str,
        wire_tool_call_id: str,
        *,
        compat_session_id: str | None = None,
    ) -> str:
        session = str(compat_session_id or "global").strip() or "global"
        return f"{str(protocol or '').strip().lower()}:{session}:{str(wire_tool_call_id or '').strip()}"

    def _complete_abandoned_external_tool_run(self, item: dict[str, Any]) -> None:
        run_id = str(item.get("runId") or "").strip()
        if not run_id:
            return
        try:
            from erc.run_service import run_service
            from erc.workflow_ledger import workflow_ledger_service

            run_record = run_service.get_run(run_id)
            if not run_record:
                return
            if str(run_record.get("status") or "").strip() != "waiting_external_tool":
                return
            transition = run_service.transition_run_if_status(
                run_id,
                expected_statuses={"waiting_external_tool"},
                status="abandoned",
                metadata={
                    "external_tool_final_reason": "external_tool_abandoned",
                    "abandonedExternalTool": {
                        "protocol": item.get("protocol"),
                        "wireToolCallId": item.get("wireToolCallId"),
                        "externalWireName": item.get("externalWireName"),
                    },
                },
            )
            if not bool(transition.get("updated")):
                return
            run_record = dict(transition.get("run_record") or run_record)
            workflow_ledger_service.sync_run_status(
                run_id,
                run_status="abandoned",
                reason="external_tool_abandoned",
                metadata={"externalToolStatus": "external_tool_abandoned"},
            )
            run_ledger_service.record_event(
                event_type="external_tool.abandoned",
                run_id=run_id,
                session_id=run_record.get("session_id"),
                runtime_kind=run_record.get("run_type"),
                source="network_supervisor",
                summary=f"External tool abandoned: {item.get('externalWireName') or item.get('wireToolCallId')}",
                refs={
                    "runId": run_id,
                    "sessionId": run_record.get("session_id"),
                    "wireToolCallId": item.get("wireToolCallId"),
                },
                payload={
                    "protocol": item.get("protocol"),
                    "externalWireName": item.get("externalWireName"),
                    "reason": "expired_waiting_for_client_tool_result",
                },
            )
        except Exception:
            # Network Supervisor diagnostics must not break request handling.
            return

    def record_pending_external_tool(
        self, **kwargs: Any,
    ) -> None:
        with self._pending_transaction() as pending:
            event = self._record_pending_external_tool_locked(pending=pending, **kwargs)
        if event:
            run_ledger_service.record_event(**event)

    def _record_pending_external_tool_locked(
        self,
        *,
        pending: dict[str, dict[str, Any]],
        protocol: str,
        run_id: str,
        wire_tool_call_id: str,
        internal_alias_name: str,
        external_wire_name: str,
        compat_session_id: str | None = None,
        external_thread_id: str | None = None,
        external_user_id: str | None = None,
        origin_token_hash: str = "",
        ttl_seconds: int = 900,
        checkpoint_resume_supported: bool = False,
    ) -> dict[str, Any] | None:
        wire_id = str(wire_tool_call_id or "").strip()
        if not wire_id:
            return
        key = self._external_tool_pending_key(protocol, wire_id, compat_session_id=compat_session_id)
        existing = pending.get(key)
        if existing:
            if str(existing.get("originTokenHash") or "") != str(origin_token_hash):
                raise HTTPException(status_code=409, detail="Pending external tool identity changed")
            # Repeated status reads cannot resurrect already-consumed results.
            if str(existing.get("status") or "") != "waiting_external_tool":
                return
            return
        now = _utc_now()
        pending[key] = {
            "protocol": str(protocol or "").strip().lower(),
            "runId": str(run_id or "").strip(),
            "compatSessionId": str(compat_session_id or "").strip(),
            "externalThreadId": str(external_thread_id or "").strip(),
            "externalUserId": str(external_user_id or "").strip(),
            "originTokenHash": str(origin_token_hash),
            "wireToolCallId": wire_id,
            "internalAliasName": str(internal_alias_name or "").strip(),
            "externalWireName": str(external_wire_name or "").strip(),
            "checkpointResumeSupported": bool(checkpoint_resume_supported),
            "status": "waiting_external_tool",
            "createdAt": _utc_iso(now),
            "expiresAt": _utc_iso(now + timedelta(seconds=max(30, int(ttl_seconds or 900)))),
            "expiresAtTs": time.time() + max(30, int(ttl_seconds or 900)),
        }
        return dict(
            event_type="external_tool.waiting",
            run_id=str(run_id or "").strip(),
            session_id=str(compat_session_id or "").strip() or None,
            runtime_kind="network_supervisor",
            source="network_supervisor",
            summary=f"Waiting for external tool result: {external_wire_name or wire_id}",
            refs={
                "runId": str(run_id or "").strip(),
                "wireToolCallId": wire_id,
                "compatSessionId": str(compat_session_id or "").strip(),
            },
            payload={
                "protocol": str(protocol or "").strip().lower(),
                "internalAliasName": str(internal_alias_name or "").strip(),
                "externalWireName": str(external_wire_name or "").strip(),
                "checkpointResumeSupported": bool(checkpoint_resume_supported),
                "expiresAt": pending[key].get("expiresAt"),
            },
        )

    @staticmethod
    def _compact_tool_result_preview(value: Any, limit: int = 4000) -> str:
        try:
            if isinstance(value, str):
                text = value
            else:
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(value or "")
        text = text.strip()
        if len(text) <= limit:
            return text
        marker = "\n...[truncated external tool result]"
        return text[: max(0, int(limit) - len(marker))] + marker

    def _find_pending_external_tool(
        self,
        pending: dict[str, dict[str, Any]],
        *,
        protocol: str,
        wire_tool_call_id: str,
        compat_session_id: str | None = None,
        external_thread_id: str | None = None,
        origin_token_hash: str = "",
    ) -> tuple[str, dict[str, Any]] | tuple[None, None]:
        normalized_protocol = str(protocol or "").strip().lower()
        wire_id = str(wire_tool_call_id or "").strip()
        if not normalized_protocol or not wire_id:
            return None, None
        candidates: list[tuple[str, dict[str, Any]]] = []
        for key, item in pending.items():
            if str(item.get("protocol") or "").strip().lower() != normalized_protocol:
                continue
            if str(item.get("wireToolCallId") or "").strip() != wire_id:
                continue
            if str(item.get("status") or "") != "waiting_external_tool":
                continue
            if str(item.get("originTokenHash") or "") != origin_token_hash:
                continue
            if compat_session_id and str(item.get("compatSessionId") or "") != compat_session_id:
                continue
            if str(item.get("externalThreadId") or "") != str(external_thread_id or ""):
                continue
            candidates.append((key, item))
        if len(candidates) != 1:
            # Reused provider call ids must not silently pick a different run.
            return None, None
        key, item = candidates[0]
        return key, item

    def claim_external_tool_results(
        self, **kwargs: Any,
    ) -> dict[str, Any]:
        if not any(str(value or "").strip() for value in kwargs.get("wire_tool_call_ids") or []):
            return {"matched": [], "unmatchedIds": [], "resumeRunId": None, "resumeValue": None}
        with self._pending_transaction() as pending:
            result = self._claim_external_tool_results_locked(pending=pending, **kwargs)
        event = result.pop("ledgerEvent", None)
        received = result.pop("_receivedReceipts", [])
        if received:
            result["receivedReceipts"] = [self.external_tool_receipt_status(item) for item in received]
        if event:
            run_ledger_service.record_event(**event)
        return result

    def _claim_external_tool_results_locked(
        self,
        *,
        pending: dict[str, dict[str, Any]],
        protocol: str,
        wire_tool_call_ids: list[str],
        tool_results: list[dict[str, Any]] | None = None,
        compat_session_id: str | None = None,
        external_thread_id: str | None = None,
        origin_token_hash: str = "",
        delivery_run_id: str | None = None,
    ) -> dict[str, Any]:
        ids = [str(item or "").strip() for item in list(wire_tool_call_ids or []) if str(item or "").strip()]
        if not ids:
            return {"matched": [], "unmatchedIds": [], "resumeRunId": None, "resumeValue": None}
        results_by_id = {
            str(item.get("wireToolCallId") or item.get("tool_call_id") or item.get("toolUseId") or "").strip(): dict(item)
            for item in list(tool_results or [])
            if isinstance(item, dict)
        }
        # Work on a detached candidate until the entire batch is validated.
        pending_candidate = dict(pending)
        matched: list[dict[str, Any]] = []
        unmatched: list[str] = []
        changed = False
        now = _utc_iso()
        for wire_id in ids:
            key, item = self._find_pending_external_tool(
                pending_candidate,
                protocol=protocol,
                wire_tool_call_id=wire_id,
                compat_session_id=compat_session_id,
                external_thread_id=external_thread_id,
                origin_token_hash=origin_token_hash,
            )
            if not key or not item:
                unmatched.append(wire_id)
                continue
            result = dict(results_by_id.get(wire_id) or {"wireToolCallId": wire_id})
            item = dict(item)
            item["status"] = "external_tool_result_received"
            item["resolvedAt"] = now
            item["toolResultPreview"] = self._compact_tool_result_preview(result.get("content") or result)
            item["toolResultReceipt"] = result
            item["receiptId"] = str(key)
            item["deliveryRunId"] = str(delivery_run_id or item.get("runId") or "")
            pending_candidate[str(key)] = item
            matched.append({**item, "toolResult": result})
            changed = True
        if unmatched:
            # A mixed/foreign/repeated result batch must not consume its valid
            # subset before the caller learns the request was rejected.
            return {"matched": [], "unmatchedIds": unmatched, "resumeRunId": None,
                    "resumeValue": None, "pendingMissReason": "pending_owner_or_state_mismatch",
                    "_receivedReceipts": [dict(item) for item in pending.values()
                        if item.get("protocol") == protocol and item.get("originTokenHash") == origin_token_hash
                        and str(item.get("externalThreadId") or "") == str(external_thread_id or "")
                        and item.get("wireToolCallId") in unmatched and item.get("receiptId")]}
        checkpoint_matched = [item for item in matched if bool(item.get("checkpointResumeSupported"))]
        run_ids = [
            str(item.get("runId") or "").strip()
            for item in checkpoint_matched
            if str(item.get("runId") or "").strip()
        ]
        if len(set(run_ids)) > 1:
            # One HTTP continuation can resume one checkpoint. Reject before
            # committing any result so each original run remains resumable.
            return {"matched": [], "unmatchedIds": ids, "resumeRunId": None,
                    "resumeValue": None, "pendingMissReason": "multiple_checkpoint_runs"}
        if changed:
            pending.update(pending_candidate)
        resume_run_id = run_ids[0] if run_ids and all(item == run_ids[0] for item in run_ids) else None
        resume_value = None
        ledger_event = None
        if resume_run_id:
            for item in matched:
                item["deliveryRunId"] = resume_run_id
                pending[item["receiptId"]]["deliveryRunId"] = resume_run_id
            resume_value = {
                "kind": "external_tool_result",
                "protocol": str(protocol or "").strip().lower(),
                "toolResults": [
                    {
                        "wireToolCallId": item.get("wireToolCallId"),
                        "externalWireName": item.get("externalWireName"),
                        "internalAliasName": item.get("internalAliasName"),
                        "content": (item.get("toolResult") or {}).get("content"),
                    }
                    for item in matched
                ],
                "pendingIds": [f"{item.get('protocol')}:{item.get('wireToolCallId')}" for item in matched],
            }
            first_item = matched[0] if matched else {}
            ledger_event = dict(
                event_type="external_tool.resumed",
                run_id=resume_run_id,
                session_id=str(first_item.get("compatSessionId") or "").strip() or None,
                runtime_kind="network_supervisor",
                source="network_supervisor",
                summary=f"External tool result received: {first_item.get('externalWireName') or first_item.get('wireToolCallId')}",
                refs={
                    "runId": resume_run_id,
                    "wireToolCallIds": [item.get("wireToolCallId") for item in matched],
                },
                payload={
                    "protocol": str(protocol or "").strip().lower(),
                    "matchedCount": len(matched),
                    "unmatchedIds": unmatched,
                },
            )
        return {
            "matched": matched,
            "unmatchedIds": unmatched,
            "resumeRunId": resume_run_id,
            "resumeValue": resume_value,
            "pendingMissReason": "no_pending_external_tool" if unmatched and not matched else None,
            "ledgerEvent": ledger_event,
        }

    def mark_external_tool_results_seen(self, *, protocol: str, wire_tool_call_ids: list[str]) -> None:
        self.claim_external_tool_results(protocol=protocol, wire_tool_call_ids=wire_tool_call_ids)

    @staticmethod
    def external_tool_receipt_status(item: dict[str, Any]) -> dict[str, Any]:
        delivery_run_id = str(item.get("deliveryRunId") or "")
        run = db.get_run_record(delivery_run_id) if delivery_run_id else None
        status = str((run or {}).get("status") or "")
        recovery_required = status in {"", "failed", "error", "abandoned"}
        return {"receiptId": item.get("receiptId"), "originalRunId": item.get("runId"),
                "deliveryRunId": delivery_run_id or None, "resultStored": bool(item.get("resultStored")),
                "deliveryState": "recovery_required" if recovery_required else status,
                "recoveryRequired": recovery_required}

    def pending_external_tools_summary(self, limit: int = 10) -> dict[str, Any]:
        pending = self.pending_external_tools_snapshot()
        waiting = [
            dict(item)
            for item in pending.values()
            if str(item.get("status") or "") == "waiting_external_tool"
        ]
        abandoned = [
            dict(item)
            for item in pending.values()
            if str(item.get("status") or "") == "external_tool_abandoned"
        ]
        resolved = [
            dict(item)
            for item in pending.values()
            if str(item.get("status") or "") in {"external_tool_result_received", "resumed_from_external_tool_result"}
        ]
        waiting.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        recent = sorted(pending.values(), key=lambda item: str(item.get("createdAt") or ""), reverse=True)[: max(1, min(int(limit or 10), 50))]
        # Full receipts stay in the database, never in the general status panel.
        recent = [{**{key: value for key, value in item.items() if key != "toolResultReceipt"},
                   **(self.external_tool_receipt_status(item) if item.get("receiptId") else {})} for item in recent]
        recent_ingress = get_recent_compat_ingress_events(limit=max(1, min(int(limit or 10), 25)))
        recent_recovery_hints: list[dict[str, Any]] = []
        for event in recent_ingress:
            diagnostics = dict((event or {}).get("diagnostics") or {})
            for hint in diagnostics.get("recoveryHints") or []:
                if isinstance(hint, dict):
                    recent_recovery_hints.append(
                        {
                            "protocol": event.get("protocol"),
                            "code": hint.get("code"),
                            "toolName": hint.get("toolName"),
                            "message": hint.get("message"),
                            "observedAt": event.get("observedAt"),
                        }
                    )
        recent_failures = [
            dict(item)
            for item in recent
            if str(item.get("status") or "") in {"external_tool_abandoned", "failed", "error"}
        ]
        return {
            "waitingCount": len(waiting),
            "abandonedCount": len(abandoned),
            "resolvedCount": len(resolved),
            "recent": [dict(item) for item in recent],
            "recentFailures": recent_failures[: max(1, min(int(limit or 10), 10))],
            "recoveryHints": recent_recovery_hints[: max(1, min(int(limit or 10), 10))],
        }

    def read_secrets(self) -> dict[str, Any]:
        self._ensure_runtime_dir()
        if not NETWORK_SUPERVISOR_SECRETS_PATH.exists():
            self.write_secrets(_secrets_default())
        try:
            return json.loads(NETWORK_SUPERVISOR_SECRETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return _secrets_default()

    def write_secrets(self, payload: dict[str, Any]) -> None:
        self._ensure_runtime_dir()
        NETWORK_SUPERVISOR_SECRETS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_config_model(self) -> NetworkSupervisorRuntimeConfig:
        return NetworkSupervisorRuntimeConfig.model_validate(storage.get_network_supervisor_runtime_config())

    def save_config_model(self, config: NetworkSupervisorRuntimeConfig) -> NetworkSupervisorRuntimeConfig:
        storage.save_network_supervisor_runtime_config(config.model_dump(by_alias=True))
        return self.get_config_model()

    def ensure_local_identity(self) -> dict[str, Any]:
        secrets_payload = self.read_secrets()
        config = self.get_config_model()
        changed = False

        if not str(secrets_payload.get("privateKey") or "").strip():
            private_key = Ed25519PrivateKey.generate()
            public_key = private_key.public_key()
            secrets_payload["privateKey"] = base64.b64encode(private_key.private_bytes_raw()).decode("utf-8")
            secrets_payload["publicKey"] = base64.b64encode(public_key.public_bytes_raw()).decode("utf-8")
            changed = True

        if not str(secrets_payload.get("localPeerToken") or "").strip():
            secrets_payload["localPeerToken"] = secrets.token_urlsafe(24)
            changed = True

        public_key_raw = str(secrets_payload.get("publicKey") or "").strip()
        secrets_payload["publicKeyFingerprint"] = _fingerprint(public_key_raw) if public_key_raw else ""

        if not str(config.node.peer_id or "").strip():
            config.node.peer_id = _generate_peer_id()
            changed = True

        if changed:
            self.write_secrets(secrets_payload)
            self.save_config_model(config)

        from runtimes.network_supervisor.transport_setup import advertised_endpoint
        admin_base = str((storage.get_system_base_config().get("bridge") or {}).get("adminBaseUrl") or "http://127.0.0.1:9528")
        address_key = (config.node.advertised_base_url, config.node.advertised_ws_url, config.node.peer_base_url or "", admin_base)
        cached_address = self._advertised_endpoint_cache
        if cached_address and cached_address[0] == address_key and time.monotonic() - cached_address[1] < 10:
            public_endpoint = cached_address[2]
        else:
            public_endpoint = advertised_endpoint(config.node.model_dump(by_alias=True), admin_base)
            self._advertised_endpoint_cache = (address_key, time.monotonic(), public_endpoint)
        return {
            "peerId": config.node.peer_id,
            "displayName": config.node.display_name,
            "publicKey": secrets_payload.get("publicKey") or "",
            "publicKeyFingerprint": secrets_payload.get("publicKeyFingerprint") or "",
            "localPeerTokenFingerprint": _fingerprint(secrets_payload.get("localPeerToken") or ""),
            "advertisedBaseUrl": public_endpoint["advertisedBaseUrl"],
            "advertisedWsUrl": public_endpoint["advertisedWsUrl"],
            "transportProfileId": config.node.transport_profile_id or "",
            "peerBaseUrl": public_endpoint["peerBaseUrl"],
        }

    def _private_key(self) -> Ed25519PrivateKey:
        self.ensure_local_identity()
        raw = base64.b64decode(self.read_secrets()["privateKey"])
        return Ed25519PrivateKey.from_private_bytes(raw)

    def _local_identity(self) -> dict[str, Any]:
        return self.ensure_local_identity()

    def _trusted_peer_map(self) -> dict[str, TrustedPeerConfig]:
        config = self.get_config_model()
        return {item.peer_id: item for item in config.trust.trusted_peers}

    def _peer_token_map(self) -> dict[str, str]:
        secrets_payload = self.read_secrets()
        raw = secrets_payload.get("peerTokens") or {}
        return {str(key): str(value or "") for key, value in dict(raw).items() if str(key).strip()}

    def _local_peer_token(self) -> str:
        return str(self.read_secrets().get("localPeerToken") or "").strip()

    def _serialize_unsigned(self, payload: dict[str, Any]) -> bytes:
        clone = dict(payload)
        clone["signature"] = ""
        return _json_dumps(clone).encode("utf-8")

    def _sign_payload(self, payload: dict[str, Any]) -> str:
        signature = self._private_key().sign(self._serialize_unsigned(payload))
        return base64.b64encode(signature).decode("utf-8")

    def _load_public_key(self, encoded_public_key: str) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded_public_key))

    def _prune_seen_nonces(self) -> None:
        now = time.time()
        if not self._seen_nonces:
            state = self.read_state()
            raw = state.get("seenNonces") or {}
            self._seen_nonces = {
                str(key): float(value)
                for key, value in dict(raw).items()
                if float(value) > now
            }
            if raw != self._seen_nonces:
                state["seenNonces"] = dict(self._seen_nonces)
                self.write_state(state)
            return
        stale = [nonce for nonce, expiry in self._seen_nonces.items() if expiry <= now]
        if not stale:
            return
        for nonce in stale:
            self._seen_nonces.pop(nonce, None)
        state = self.read_state()
        state["seenNonces"] = dict(self._seen_nonces)
        self.write_state(state)

    def _mark_nonce_seen(self, nonce: str, expires_at: str) -> None:
        self._prune_seen_nonces()
        try:
            expiry_epoch = _parse_utc(expires_at).timestamp()
        except Exception:
            expiry_epoch = time.time() + 60
        self._seen_nonces[nonce] = expiry_epoch
        state = self.read_state()
        state["seenNonces"] = dict(self._seen_nonces)
        self.write_state(state)

    async def start(self) -> None:
        async with self._lock:
            self._ensure_runtime_dir()
            self.ensure_local_identity()
            if self._started:
                return
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0))
            self._started = True
            config = self.get_config_model()
            if config.enabled and config.discovery.lan_enabled:
                try:
                    await self._start_discovery_listener(config)
                    self._announce_task = asyncio.create_task(self._announce_loop())
                except OSError as exc:
                    self._discovery_error = type(exc).__name__
            if config.enabled and config.discovery.wan_bootstrap_peers:
                self._bootstrap_task = asyncio.create_task(self._bootstrap_known_peers())

    async def stop(self) -> None:
        async with self._lock:
            self._started = False
            for task in (self._announce_task, self._bootstrap_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            self._announce_task = None
            self._bootstrap_task = None
            for task in list(self._active_inbound_tasks.values()):
                if not task.done():
                    task.cancel()
            self._active_inbound_tasks.clear()
            if self._discovery_transport is not None:
                self._discovery_transport.close()
                self._discovery_transport = None
            if self._discovery_socket is not None:
                try:
                    self._discovery_socket.close()
                except OSError:
                    pass
                self._discovery_socket = None
            if self._discovery_sender is not None:
                try:
                    self._discovery_sender.close()
                except OSError:
                    pass
                self._discovery_sender = None
            if self._http_client is not None:
                await self._http_client.aclose()
                self._http_client = None

    async def reload(self) -> None:
        # Config refresh must not cancel accepted inbound work or close its HTTP client.
        if not self._started:
            await self.start()
            return
        async with self._lock:
            for task in (self._announce_task, self._bootstrap_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            self._announce_task = self._bootstrap_task = None
            for resource in (self._discovery_transport, self._discovery_socket, self._discovery_sender):
                if resource is not None:
                    resource.close()
            self._discovery_transport = self._discovery_socket = self._discovery_sender = None
            self._discovery_error = None
            config = self.get_config_model()
            if config.enabled and config.discovery.lan_enabled:
                try:
                    await self._start_discovery_listener(config)
                    self._announce_task = asyncio.create_task(self._announce_loop())
                except OSError as exc:
                    self._discovery_error = type(exc).__name__
            if config.enabled and config.discovery.wan_bootstrap_peers:
                self._bootstrap_task = asyncio.create_task(self._bootstrap_known_peers())

    def build_envelope(
        self,
        *,
        message_type: str,
        to_peer_id: str,
        payload: dict[str, Any],
        trace: Optional[NetworkTraceContext] = None,
        expires_in_seconds: int = 30,
    ) -> NetworkEnvelope:
        identity = self._local_identity()
        envelope_payload = {
            "version": self.protocol_version,
            "messageId": f"msg_{uuid.uuid4().hex}",
            "messageType": message_type,
            "sentAt": _utc_iso(),
            "expiresAt": _utc_iso(_utc_now() + timedelta(seconds=expires_in_seconds)),
            "fromPeerId": identity["peerId"],
            "toPeerId": to_peer_id,
            "nonce": uuid.uuid4().hex,
            "signature": "",
            "trace": (trace or NetworkTraceContext()).model_dump(by_alias=True, exclude_none=True),
            "payload": payload,
        }
        # Sign exactly the validated wire form; optional trace fields materialize as
        # null on model_dump and must not change the signed bytes after construction.
        envelope = NetworkEnvelope.model_validate(envelope_payload)
        envelope.signature = self._sign_payload(envelope.model_dump(by_alias=True))
        return envelope

    def verify_envelope(
        self,
        envelope: NetworkEnvelope,
        *,
        allow_untrusted: bool = False,
        provided_public_key: str | None = None,
        mark_nonce_seen: bool = True,
    ) -> dict[str, Any]:
        self.ensure_local_identity()
        if envelope.version != self.protocol_version:
            raise HTTPException(status_code=400, detail="Unsupported protocol version")
        try:
            expires_at = _parse_utc(envelope.expires_at)
            sent_at = _parse_utc(envelope.sent_at)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid envelope timestamp: {exc}") from exc
        now = _utc_now()
        if expires_at < now:
            raise HTTPException(status_code=400, detail="Envelope already expired")
        if sent_at > now + timedelta(seconds=15):
            raise HTTPException(status_code=400, detail="Envelope timestamp is in the future")

        local_peer_id = str(self.get_config_model().node.peer_id or "").strip()
        if envelope.to_peer_id and envelope.to_peer_id != local_peer_id:
            raise HTTPException(status_code=403, detail="Envelope was addressed to a different peer")

        self._prune_seen_nonces()
        if mark_nonce_seen and envelope.nonce in self._seen_nonces:
            raise HTTPException(status_code=409, detail="Envelope nonce already used")

        trusted_peer = self._trusted_peer_map().get(envelope.from_peer_id)
        public_key = provided_public_key or (trusted_peer.public_key if trusted_peer else None)
        if not public_key and not allow_untrusted:
            raise HTTPException(status_code=403, detail="Peer is not trusted")
        if not public_key:
            public_key = str(envelope.payload.get("publicKey") or "").strip()
        if not public_key:
            raise HTTPException(status_code=400, detail="Missing public key for envelope verification")
        try:
            verifier = self._load_public_key(public_key)
            verifier.verify(
                base64.b64decode(envelope.signature),
                self._serialize_unsigned(envelope.model_dump(by_alias=True)),
            )
        except Exception as exc:
            raise HTTPException(status_code=403, detail=f"Envelope signature verification failed: {exc}") from exc

        if mark_nonce_seen:
            self._mark_nonce_seen(envelope.nonce, envelope.expires_at)
        return {
            "trustedPeer": trusted_peer.model_dump(by_alias=True) if trusted_peer else None,
            "publicKey": public_key,
        }

    def verify_inbound_peer_token(self, token: str | None) -> None:
        expected = self._local_peer_token()
        if not expected:
            raise HTTPException(status_code=500, detail="Local peer token is missing")
        if str(token or "").strip() != expected:
            raise HTTPException(status_code=401, detail="Invalid peer token")

    def verify_openai_compat_token(self, token: str | None) -> dict[str, Any]:
        config = self.get_config_model()
        if not config.enabled or not config.openai_compat.enabled:
            raise HTTPException(status_code=403, detail="OpenAI compat branch is disabled")
        provided = str(token or "").strip()
        if not provided:
            raise HTTPException(status_code=401, detail="Missing bearer token")
        for item in self._openai_compat_token_entries():
            if provided == str(item.get("token") or "").strip():
                return item
        raise HTTPException(status_code=401, detail="Invalid compat token")

    def _default_openai_compat_permissions(self) -> list[str]:
        return [
            "compat:chat",
            "compat:external_tool_roundtrip",
            "compat:support_tools",
            "memory:global_read",
            "memory:persist",
        ]

    def openai_compat_token_has_permission(self, token_entry: dict[str, Any] | None, permission: str) -> bool:
        if token_entry is not None and not isinstance(token_entry, dict):
            return True
        permissions = list((token_entry or {}).get("permissions") or [])
        normalized = {str(item).strip() for item in permissions if str(item).strip()}
        return "*" in normalized or str(permission or "").strip() in normalized

    def _normalize_openai_compat_token_entry(self, item: Any) -> dict[str, Any] | None:
        if isinstance(item, str):
            token = item.strip()
            if not token:
                return None
            fingerprint = _fingerprint(token)
            return {
                "id": f"legacy_{fingerprint}",
                "label": "Legacy compat token",
                "token": token,
                "fingerprint": fingerprint,
                "createdAt": None,
                "source": "legacy_string",
                "mode": "third_party_managed",
                "permissions": self._default_openai_compat_permissions(),
            }
        if not isinstance(item, dict):
            return None
        token = str(item.get("token") or "").strip()
        if not token:
            return None
        fingerprint = str(item.get("fingerprint") or "").strip() or _fingerprint(token)
        token_id = str(item.get("id") or "").strip() or f"oct_{fingerprint}"
        return {
            "id": token_id,
            "label": str(item.get("label") or "").strip() or "OpenAI compat token",
            "token": token,
            "fingerprint": fingerprint,
            "createdAt": str(item.get("createdAt") or "").strip() or None,
            "source": str(item.get("source") or "").strip() or "managed",
            "mode": str(item.get("mode") or "").strip() or "third_party_managed",
            "permissions": list(item.get("permissions") or self._default_openai_compat_permissions()),
        }

    def _openai_compat_token_entries(self) -> list[dict[str, Any]]:
        secrets_payload = self.read_secrets()
        entries: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()
        for item in list(secrets_payload.get("openaiCompatTokens") or []):
            normalized = self._normalize_openai_compat_token_entry(item)
            if not normalized:
                continue
            token = str(normalized.get("token") or "").strip()
            if token in seen_tokens:
                continue
            seen_tokens.add(token)
            entries.append(normalized)
        return entries

    def list_openai_compat_tokens(self) -> dict[str, Any]:
        return {"items": self._openai_compat_token_entries()}

    def create_openai_compat_token(self, label: str | None = None) -> dict[str, Any]:
        secrets_payload = self.read_secrets()
        entries = self._openai_compat_token_entries()
        token = f"v8oa_{secrets.token_urlsafe(32)}"
        created_at = _utc_iso()
        entry = {
            "id": f"oct_{uuid.uuid4().hex[:12]}",
            "label": str(label or "").strip() or "OpenAI compat token",
            "token": token,
            "fingerprint": _fingerprint(token),
            "createdAt": created_at,
            "source": "managed",
            "mode": "third_party_managed",
            "permissions": self._default_openai_compat_permissions(),
        }
        entries.append(entry)
        secrets_payload["openaiCompatTokens"] = entries
        self.write_secrets(secrets_payload)
        return entry

    def begin_openai_compat_request(self, token_entry: dict[str, Any] | None) -> dict[str, Any]:
        config = self.get_config_model().openai_compat
        fingerprint = str((token_entry or {}).get("fingerprint") or "anonymous").strip() or "anonymous"
        now = time.time()
        rate_limit = max(1, int(getattr(config, "rate_limit_per_minute", 120) or 120))
        concurrent_limit = max(1, int(getattr(config, "max_concurrent_requests_per_key", 8) or 8))
        window = self._compat_rate_state.setdefault(fingerprint, deque())
        while window and now - float(window[0]) > 60.0:
            window.popleft()
        if len(window) >= rate_limit:
            raise HTTPException(status_code=429, detail="Compat rate limit exceeded")
        active = int(self._compat_active_counts.get(fingerprint) or 0)
        if active >= concurrent_limit:
            raise HTTPException(status_code=429, detail="Compat concurrency limit exceeded")
        window.append(now)
        self._compat_active_counts[fingerprint] = active + 1
        return {"fingerprint": fingerprint, "startedAt": now}

    def finish_openai_compat_request(self, lease: dict[str, Any] | None) -> None:
        fingerprint = str((lease or {}).get("fingerprint") or "").strip()
        if not fingerprint:
            return
        active = max(0, int(self._compat_active_counts.get(fingerprint) or 0) - 1)
        if active:
            self._compat_active_counts[fingerprint] = active
        else:
            self._compat_active_counts.pop(fingerprint, None)

    def delete_openai_compat_token(self, token_id: str) -> dict[str, Any]:
        target = str(token_id or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="Missing token id")
        secrets_payload = self.read_secrets()
        entries = self._openai_compat_token_entries()
        kept = [
            item
            for item in entries
            if target
            not in {
                str(item.get("id") or "").strip(),
                str(item.get("fingerprint") or "").strip(),
                str(item.get("token") or "").strip(),
            }
        ]
        if len(kept) == len(entries):
            raise HTTPException(status_code=404, detail="OpenAI compat token not found")
        secrets_payload["openaiCompatTokens"] = kept
        self.write_secrets(secrets_payload)
        return {"deleted": True, "tokenId": target, "remainingCount": len(kept)}

    def _merge_discovered_peer(self, payload: dict[str, Any]) -> None:
        state = self.read_state()
        discovered = dict(state.get("discoveredPeers") or {})
        peer_id = str(payload.get("peerId") or "").strip()
        if not peer_id:
            return
        discovered[peer_id] = {
            **dict(discovered.get(peer_id) or {}),
            **payload,
            "lastSeenAt": _utc_iso(),
        }
        state["discoveredPeers"] = discovered
        self.write_state(state)

    def handle_discovery_packet(self, raw_data: bytes, addr: tuple[str, int]) -> None:
        try:
            packet = json.loads(raw_data.decode("utf-8"))
            peer_id = str(packet.get("peerId") or "").strip()
            if not peer_id or peer_id == str(self.get_config_model().node.peer_id or "").strip():
                return
            public_key = str(packet.get("publicKey") or "").strip()
            signature = str(packet.get("signature") or "").strip()
            if not public_key or not signature:
                return
            verifier = self._load_public_key(public_key)
            unsigned = dict(packet)
            unsigned["signature"] = ""
            verifier.verify(base64.b64decode(signature), _json_dumps(unsigned).encode("utf-8"))
            if _parse_utc(str(packet.get("expiresAt") or "")) < _utc_now():
                return
            self._merge_discovered_peer(
                {
                    "peerId": peer_id,
                    "displayName": str(packet.get("displayName") or "").strip() or peer_id,
                    "baseUrl": str(packet.get("baseUrl") or "").strip(),
                    "wsUrl": str(packet.get("wsUrl") or "").strip(),
                    "transportProfileId": str(packet.get("transportProfileId") or "").strip(),
                    "peerBaseUrl": str(packet.get("peerBaseUrl") or "").strip(),
                    "publicKey": public_key,
                    "publicKeyFingerprint": _fingerprint(public_key),
                    "source": "lan",
                    "address": f"{addr[0]}:{addr[1]}",
                }
            )
        except Exception:
            return

    def _build_discovery_packet(self) -> dict[str, Any]:
        identity = self._local_identity()
        packet = {
            "protocolVersion": self.protocol_version,
            "peerId": identity["peerId"],
            "displayName": self.get_config_model().node.display_name,
            "baseUrl": identity["advertisedBaseUrl"],
            "wsUrl": identity["advertisedWsUrl"],
            "transportProfileId": self.get_config_model().node.transport_profile_id or "",
            "peerBaseUrl": identity["peerBaseUrl"],
            "publicKey": identity["publicKey"],
            "publicKeyFingerprint": identity["publicKeyFingerprint"],
            "sentAt": _utc_iso(),
            "expiresAt": _utc_iso(_utc_now() + timedelta(seconds=20)),
            "signature": "",
        }
        packet["signature"] = self._sign_payload(packet)
        return packet

    async def _start_discovery_listener(self, config: NetworkSupervisorRuntimeConfig) -> None:
        loop = asyncio.get_running_loop()
        group = config.discovery.multicast_group
        port = config.discovery.multicast_port

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sender = None
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", port))
            membership = socket.inet_aton(group) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.setblocking(False)
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            transport, _ = await loop.create_datagram_endpoint(lambda: _DiscoveryProtocol(self), sock=sock)
        except BaseException:
            sock.close()
            if sender is not None:
                sender.close()
            raise
        self._discovery_transport = transport
        self._discovery_socket = sock
        self._discovery_sender = sender

    async def _announce_loop(self) -> None:
        while self._started:
            try:
                config = self.get_config_model()
                if not (config.enabled and config.discovery.lan_enabled and self._discovery_sender is not None):
                    await asyncio.sleep(3.0)
                    continue
                packet = self._build_discovery_packet()
                payload = _json_dumps(packet).encode("utf-8")
                self._discovery_sender.sendto(payload, (config.discovery.multicast_group, config.discovery.multicast_port))
                self._last_announce_at = _utc_iso()
                await asyncio.sleep(max(3, int(config.discovery.announce_interval_seconds or 15)))
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(5.0)

    async def _bootstrap_known_peers(self) -> None:
        await asyncio.sleep(1.0)
        config = self.get_config_model()
        for peer_id in config.discovery.wan_bootstrap_peers:
            normalized = str(peer_id or "").strip()
            if not normalized:
                continue
            try:
                await self.join_peer(normalized)
            except Exception:
                continue

    def _trusted_peer(self, peer_id: str) -> TrustedPeerConfig:
        trusted = self._trusted_peer_map().get(str(peer_id or "").strip())
        if trusted is None:
            raise HTTPException(status_code=404, detail=f"Unknown trusted peer: {peer_id}")
        return trusted

    def _has_peer_token(self, peer_id: str) -> bool:
        return bool(str(self._peer_token_map().get(str(peer_id or "").strip()) or "").strip())

    def _peer_endpoint(self, peer_id: str) -> dict[str, Any]:
        trusted = self._trusted_peer_map().get(peer_id)
        if trusted:
            return resolve_peer_transport_endpoint(trusted.model_dump(by_alias=True))
        state = self.read_state()
        discovered = dict(state.get("discoveredPeers") or {}).get(peer_id)
        if discovered:
            return resolve_peer_transport_endpoint(discovered)
        raise HTTPException(status_code=404, detail=f"Unknown peer: {peer_id}")

    def _peer_headers(self, peer_id: str) -> dict[str, str]:
        peer_tokens = self._peer_token_map()
        token = str(peer_tokens.get(peer_id) or "").strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-V8-Peer-Token"] = token
        return headers

    async def _post_peer(self, peer_id: str, path: str, envelope: NetworkEnvelope) -> dict[str, Any]:
        endpoint = self._peer_endpoint(peer_id)
        base_url = str(endpoint.get("baseUrl") or "").rstrip("/")
        try:
            parsed_url = urlsplit(base_url)
            if base_url and (parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname or parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment or parsed_url.path):
                raise ValueError()
            _ = parsed_url.port
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"failureClass": "peer_invalid_origin", "peerId": peer_id}) from exc
        if len(_json_dumps(envelope.model_dump(by_alias=True)).encode("utf-8")) > 262_144:
            raise HTTPException(status_code=413, detail={"failureClass": "peer_request_too_large", "peerId": peer_id})
        if not base_url:
            raise HTTPException(
                status_code=400,
                detail={
                    "failureClass": "route_conflict",
                    "peerId": peer_id,
                    "transportProfileId": endpoint.get("transportProfileId") or "",
                    "routeWarnings": endpoint.get("routeWarnings") or [],
                    "recommendedNextAction": "Set peerBaseUrl/baseUrl or attach a TransportProfile with a peerBaseUrl.",
                },
            )
        client = self._http_client
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0))
            self._http_client = client
        try:
            response = await client.post(
                f"{base_url}/v1/network-supervisor/{path.lstrip('/')}",
                headers=self._peer_headers(peer_id),
                json=envelope.model_dump(by_alias=True),
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "failureClass": "peer_unreachable",
                    "peerId": peer_id,
                    "baseUrl": base_url,
                    "transportProfileId": endpoint.get("transportProfileId") or "",
                    "reason": str(exc),
                    "recommendedNextAction": "Check V8 Link diagnostics, VPN route/DNS/MTU, and peer auth before retrying.",
                },
            ) from exc
        if len(response.content) > 1_048_576:
            raise HTTPException(status_code=502, detail={"failureClass": "peer_response_too_large", "peerId": peer_id})
        try:
            payload = response.json() if response.content else {}
        except ValueError as exc:
            raise HTTPException(status_code=502, detail={"failureClass": "peer_invalid_response", "peerId": peer_id,
                "httpStatus": response.status_code}) from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail={"failureClass": "peer_invalid_response", "peerId": peer_id})
        if response.is_error:
            detail = payload.get("detail") or payload.get("error") or response.text
            failure_class = "auth_failed" if response.status_code in {401, 403} else "peer_error"
            raise HTTPException(
                status_code=response.status_code,
                detail={
                    "failureClass": failure_class,
                    "peerId": peer_id,
                    "baseUrl": base_url,
                    "detail": detail,
                },
            )
        # HTTP success is not proof of receipt. Bind the signed response to this request.
        try:
            reply = NetworkEnvelope.model_validate(payload)
            expected_types = {
                "neighbor.message": {"neighbor.message.ack"},
                "neighbor.pairing.consume": {"neighbor.pairing.accepted"},
                "peer.join_request": {"peer.join_response"},
                "peer.challenge_request": {"peer.challenge_response"},
                "wake.request": {"wake.ack"},
                "delegation.request": {"delegation.accepted", "delegation.result", "delegation.failed"},
                "neighbor.task.assign": {"neighbor.task.ack"},
                "neighbor.task.result": {"neighbor.task.ack"},
                "neighbor.task.handoff_request": {"neighbor.task.ack"},
                "neighbor.task.ack": {"neighbor.task.ack"},
            }
            expected = expected_types.get(envelope.message_type, {f"{envelope.message_type}.ack"})
            if reply.from_peer_id != peer_id or reply.to_peer_id != envelope.from_peer_id or reply.message_type not in expected:
                raise ValueError("peer_response_identity_mismatch")
            if reply.payload.get("requestMessageId") != envelope.message_id:
                raise ValueError("peer_response_request_mismatch")
            if reply.trace.model_dump() != envelope.trace.model_dump():
                raise ValueError("peer_response_trace_mismatch")
            if envelope.message_type == "neighbor.message" and reply.payload.get("messageId") != envelope.payload.get("messageId"):
                raise ValueError("peer_response_message_mismatch")
            if envelope.message_type == "neighbor.message" and reply.payload.get("status") not in {"received", "duplicate"}:
                raise ValueError("peer_response_not_received")
            if envelope.message_type.startswith("neighbor.task.") and envelope.message_type != "neighbor.task.ack":
                if reply.payload.get("status") not in {"received", "duplicate"}:
                    raise ValueError("peer_response_not_received")
                for identity_field in ("taskId", "assignmentId", "resultId"):
                    if envelope.payload.get(identity_field) and reply.payload.get(identity_field) != envelope.payload[identity_field]:
                        raise ValueError("peer_response_task_mismatch")
            public_key = str(endpoint.get("publicKey") or "").strip()
            if not public_key:
                raise ValueError("peer_response_identity_unpinned")
            self.verify_envelope(reply, allow_untrusted=True, provided_public_key=public_key,
                                 mark_nonce_seen=False)
        except (ValueError, HTTPException) as exc:
            raise HTTPException(status_code=502, detail={"failureClass": "peer_unverified_response", "peerId": peer_id}) from exc
        return payload

    def _is_peer_online(
        self,
        discovered_peer: dict[str, Any],
        *,
        expiry_seconds: int,
        now: datetime,
    ) -> bool:
        last_seen = str(discovered_peer.get("lastSeenAt") or "").strip()
        if not last_seen:
            return False
        try:
            return _parse_utc(last_seen) >= now - timedelta(seconds=expiry_seconds)
        except Exception:
            return False

    def _peer_view_payload(
        self,
        *,
        peer_id: str,
        trusted_peer: TrustedPeerConfig | None,
        discovered_peer: dict[str, Any],
        peer_tokens: dict[str, str],
        expiry_seconds: int,
        now: datetime,
    ) -> dict[str, Any]:
        public_key = trusted_peer.public_key if trusted_peer else str(discovered_peer.get("publicKey") or "").strip()
        last_seen = str(discovered_peer.get("lastSeenAt") or "").strip() or None
        return {
            "peerId": peer_id,
            "displayName": (
                trusted_peer.display_name
                if trusted_peer and trusted_peer.display_name
                else str(discovered_peer.get("displayName") or "").strip() or peer_id
            ),
            "baseUrl": trusted_peer.base_url if trusted_peer else str(discovered_peer.get("baseUrl") or "").strip(),
            "wsUrl": trusted_peer.ws_url if trusted_peer else str(discovered_peer.get("wsUrl") or "").strip(),
            "transportProfileId": (
                trusted_peer.transport_profile_id
                if trusted_peer
                else str(discovered_peer.get("transportProfileId") or "").strip()
            ),
            "peerBaseUrl": (
                trusted_peer.peer_base_url
                if trusted_peer
                else str(discovered_peer.get("peerBaseUrl") or "").strip()
            ),
            "resolvedBaseUrl": resolve_peer_transport_endpoint(
                trusted_peer.model_dump(by_alias=True) if trusted_peer else dict(discovered_peer)
            ).get("resolvedBaseUrl", ""),
            "transportKind": resolve_peer_transport_endpoint(
                trusted_peer.model_dump(by_alias=True) if trusted_peer else dict(discovered_peer)
            ).get("transportKind", "manual_url"),
            "publicKey": public_key,
            "publicKeyFingerprint": _fingerprint(public_key) if public_key else "",
            "trusted": bool(trusted_peer),
            "discovered": bool(discovered_peer),
            "online": self._is_peer_online(discovered_peer, expiry_seconds=expiry_seconds, now=now),
            "lastSeenAt": last_seen,
            "allowedScopes": list(trusted_peer.allowed_scopes) if trusted_peer else [],
            "allowedWorkspaces": list(trusted_peer.allowed_workspaces) if trusted_peer else [],
            "tokenFingerprint": _fingerprint(peer_tokens.get(peer_id) or ""),
            "source": str(discovered_peer.get("source") or ("manual" if trusted_peer else "unknown")),
            "address": str(discovered_peer.get("address") or "").strip(),
        }

    def _delegation_availability_payload(self) -> dict[str, Any]:
        config = self.get_config_model()
        reasons: list[str] = []
        trusted_items = [item for item in self.list_peers() if bool(item.get("trusted"))]
        online_trusted_items = [item for item in trusted_items if bool(item.get("online"))]
        if not config.enabled:
            reasons.append("runtime_disabled")
        if not config.delegation.enabled:
            reasons.append("delegation_disabled")
        if not trusted_items:
            reasons.append("no_trusted_peers")
        elif not online_trusted_items:
            reasons.append("no_online_trusted_peers")
        return {
            "available": len(reasons) == 0,
            "reasons": reasons,
        }

    def _relay_adapter_payload(self, relay_config: NetworkRelayConfig, adapter: Any) -> dict[str, Any]:
        base_url = str(getattr(adapter, "base_url", "") or "").strip().rstrip("/")
        websocket_url = str(getattr(adapter, "websocket_url", "") or "").strip()
        adapter_id = str(getattr(adapter, "id", "") or "").strip()
        kind = str(getattr(adapter, "kind", "") or "self_hosted").strip()
        enabled = bool(getattr(adapter, "enabled", True))
        configured = bool(base_url and enabled)
        warnings: list[str] = []
        if kind == "cloudflare":
            warnings.append("cloudflare_adapter_requires_external_worker")
        if relay_config.enabled and enabled and not base_url:
            warnings.append("missing_relay_base_url")
        return {
            "id": adapter_id,
            "kind": kind,
            "displayName": str(getattr(adapter, "display_name", "") or adapter_id or kind).strip(),
            "enabled": enabled,
            "configured": configured,
            "status": "ready" if relay_config.enabled and configured else ("needs_configuration" if relay_config.enabled and enabled else "disabled"),
            "baseUrl": base_url,
            "websocketUrl": websocket_url or (_default_ws_url(base_url, getattr(adapter, "websocket_path", "/v1/relay/ws")) if base_url else ""),
            "endpoints": {
                "wellKnown": _join_url(base_url, "/.well-known/v8-relay") if base_url else "",
                "rendezvous": _join_url(base_url, getattr(adapter, "rendezvous_path", "/v1/relay/rendezvous")) if base_url else "",
                "mailbox": _join_url(base_url, getattr(adapter, "mailbox_path", "/v1/relay/mailbox")) if base_url else "",
                "websocket": websocket_url or (_default_ws_url(base_url, getattr(adapter, "websocket_path", "/v1/relay/ws")) if base_url else ""),
            },
            "cloudflare": {
                "accountHint": str(getattr(adapter, "cloudflare_account_hint", "") or "").strip(),
                "workerName": str(getattr(adapter, "cloudflare_worker_name", "") or "").strip(),
                "queueName": str(getattr(adapter, "cloudflare_queue_name", "") or "").strip(),
                "durableObjectNamespace": str(getattr(adapter, "cloudflare_durable_object_namespace", "") or "").strip(),
            } if kind == "cloudflare" else None,
            "warnings": warnings,
        }

    def relay_status_payload(self) -> dict[str, Any]:
        config = self.get_config_model()
        identity = self._local_identity()
        relay_config = config.relay
        adapters = [self._relay_adapter_payload(relay_config, item) for item in list(relay_config.adapters or [])]
        outbox_items = db.list_network_relay_outbox(states=["queued", "retry", "leased", "published", "dead_letter"], limit=200)
        outbox_counts = {
            "queued": len([item for item in outbox_items if item.get("state") == "queued"]),
            "retry": len([item for item in outbox_items if item.get("state") == "retry"]),
            "leased": len([item for item in outbox_items if item.get("state") == "leased"]),
            "published": len([item for item in outbox_items if item.get("state") == "published"]),
            "deadLetter": len([item for item in outbox_items if item.get("state") == "dead_letter"]),
        }
        active_adapter_id = str(relay_config.active_adapter_id or "").strip()
        active_adapter = next((item for item in adapters if item.get("id") == active_adapter_id), None)
        if active_adapter is None and adapters:
            active_adapter = adapters[0]
            active_adapter_id = str(active_adapter.get("id") or "")
        reasons: list[str] = []
        if not config.enabled:
            reasons.append("runtime_disabled")
        if not relay_config.enabled:
            reasons.append("relay_disabled")
        if not active_adapter:
            reasons.append("no_relay_adapter")
        elif not bool(active_adapter.get("configured")):
            reasons.append("active_adapter_not_configured")
        return {
            "enabled": bool(relay_config.enabled),
            "available": len(reasons) == 0,
            "reasons": reasons,
            "activeAdapterId": active_adapter_id,
            "activeAdapter": active_adapter or {},
            "adapters": adapters,
            "protocol": {
                "name": "V8 Relay",
                "version": str(relay_config.protocol_version or "v8-relay.v1"),
                "wireEnvelope": "network_supervisor.signed_envelope",
                "delivery": ["rendezvous", "mailbox", "websocket"],
                "trust": "short_code_pairing_then_peer_token",
                "endToEndEnvelopeRequired": bool(relay_config.end_to_end_envelope_required),
                "storeAndForwardRequired": bool(relay_config.store_and_forward_required),
                "selfHostable": True,
                "cloudflareAdapter": "optional",
                "workspacePathPolicy": "remote_workspace_path_is_metadata_only",
                "defaultTtlSeconds": int(relay_config.default_ttl_seconds or 300),
                "maxPayloadBytes": int(relay_config.max_payload_bytes or 262144),
            },
            "localNode": {
                "peerId": identity.get("peerId") or "",
                "displayName": identity.get("displayName") or "",
                "publicKeyFingerprint": identity.get("publicKeyFingerprint") or "",
            },
            "runtime": {
                "outbox": outbox_counts,
                "deadLetterCount": len(db.list_network_relay_dead_letters(limit=50)),
            },
        }

    def save_relay_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = self.get_config_model()
        incoming = dict(payload.get("relay") if "relay" in payload else payload)
        current = config.relay.model_dump(by_alias=True)
        if isinstance(incoming.get("adapters"), list):
            current_adapters = {
                str(item.get("id") or "").strip(): dict(item)
                for item in list(current.get("adapters") or [])
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
            for item in list(incoming.get("adapters") or []):
                if not isinstance(item, dict):
                    continue
                adapter_id = str(item.get("id") or "").strip()
                if not adapter_id:
                    continue
                current_adapters[adapter_id] = {**dict(current_adapters.get(adapter_id) or {}), **dict(item), "id": adapter_id}
            incoming["adapters"] = list(current_adapters.values())
        config.relay = NetworkRelayConfig.model_validate({**current, **incoming})
        self.save_config_model(config)
        return {"ok": True, "relay": config.relay.model_dump(by_alias=True), "status": self.relay_status_payload()}

    def build_relay_probe_envelope(self, *, adapter_id: str | None = None) -> dict[str, Any]:
        status = self.relay_status_payload()
        identity = self._local_identity()
        envelope = self.build_envelope(
            message_type="relay.probe",
            to_peer_id=str(identity.get("peerId") or ""),
            payload={
                "protocol": status.get("protocol"),
                "adapterId": str(adapter_id or status.get("activeAdapterId") or "").strip(),
                "intent": "relay_config_probe",
            },
            trace=NetworkTraceContext(),
            expires_in_seconds=60,
        )
        return {
            "ok": True,
            "protocol": status.get("protocol"),
            "adapter": status.get("activeAdapter"),
            "envelope": envelope.model_dump(by_alias=True),
        }

    def record_openai_compat_memory_adapter_status(self, result: dict[str, Any]) -> dict[str, Any]:
        state = self.read_state()
        payload = dict(result or {})
        payload.setdefault("updatedAt", _utc_iso())
        state["openaiCompatMemoryAdapter"] = payload
        recent = [dict(item) for item in list(state.get("openaiCompatMemoryAdapterRecent") or []) if isinstance(item, dict)]
        recent.insert(0, payload)
        state["openaiCompatMemoryAdapterRecent"] = recent[:10]
        self.write_state(state)
        return payload

    def status_payload(self) -> dict[str, Any]:
        config = self.get_config_model()
        identity = self._local_identity()
        state = self.read_state()
        discovered = dict(state.get("discoveredPeers") or {})
        delegations = dict(state.get("delegations") or {})
        openai_compat_tokens = self._openai_compat_token_entries()
        expiry_seconds = int(config.discovery.peer_expiry_seconds or 60)
        now = _utc_now()
        online_count = 0
        for item in discovered.values():
            last_seen = str(item.get("lastSeenAt") or "").strip()
            if not last_seen:
                continue
            try:
                if _parse_utc(last_seen) >= now - timedelta(seconds=expiry_seconds):
                    online_count += 1
            except Exception:
                continue
        return {
            "enabled": bool(config.enabled),
            "started": bool(self._started),
            "node": {
                "peerId": identity["peerId"],
                "displayName": config.node.display_name,
                "advertisedBaseUrl": identity["advertisedBaseUrl"],
                "advertisedWsUrl": identity["advertisedWsUrl"],
                "transportProfileId": config.node.transport_profile_id or "",
                "peerBaseUrl": identity["peerBaseUrl"],
                "publicKeyFingerprint": identity["publicKeyFingerprint"],
                "localPeerTokenFingerprint": identity["localPeerTokenFingerprint"],
            },
            "discovery": {
                "listenerError": self._discovery_error,
                "lanEnabled": bool(config.discovery.lan_enabled),
                "wanBootstrapPeers": list(config.discovery.wan_bootstrap_peers),
                "lastAnnounceAt": self._last_announce_at,
                "onlinePeerCount": online_count,
                "discoveredPeerCount": len(discovered),
            },
            "delegation": {
                "enabled": bool(config.delegation.enabled),
                "maxConcurrent": int(config.delegation.max_concurrent or 0),
                "activeInbound": len([item for item in self._active_inbound_tasks.values() if not item.done()]),
                "trackedCount": len(delegations),
            },
            "relay": self.relay_status_payload(),
            "openaiCompat": {
                "enabled": bool(config.openai_compat.enabled),
                "adminRelayOnly": bool(config.openai_compat.admin_relay_only),
                "available": bool(config.enabled and config.openai_compat.enabled and openai_compat_tokens),
                "tokenCount": len(openai_compat_tokens),
                "modelAliases": list(config.openai_compat.model_aliases or ["v8os"]),
                "baseUrlHint": "http://localhost:9528/api/network-supervisor/openai/v1",
                "chatCompletionsPath": "/chat/completions",
                "modelsPath": "/models",
                "maxExternalTools": int(config.openai_compat.max_external_tools or 0),
                "maxExternalSystemTokens": int(config.openai_compat.max_external_system_tokens or 0),
                "maxExternalMessageTokens": int(config.openai_compat.max_external_message_tokens or 0),
                "maxExternalToolDescriptionTokens": int(config.openai_compat.max_external_tool_description_tokens or 0),
                "maxExternalToolSchemaBytes": int(config.openai_compat.max_external_tool_schema_bytes or 0),
                "maxExternalToolsPayloadTokens": int(config.openai_compat.max_external_tools_payload_tokens or 0),
                "maxMemoryHintTokens": int(config.openai_compat.max_memory_hint_tokens or 0),
                "maxWorkflowHintTokens": int(config.openai_compat.max_workflow_hint_tokens or 0),
                "allowWorkspaceHeaders": bool(config.openai_compat.allow_workspace_headers),
                "allowRawWorkspacePath": bool(config.openai_compat.allow_raw_workspace_path),
                "v8MainChainModeEnabled": bool(config.openai_compat.v8_main_chain_mode_enabled),
                "defaultScopeMode": str(config.openai_compat.default_scope_mode or "explicit"),
                "memoryAdapter": dict(state.get("openaiCompatMemoryAdapter") or {}),
                "recentMemoryAdapter": [
                    dict(item)
                    for item in list(state.get("openaiCompatMemoryAdapterRecent") or [])[:5]
                    if isinstance(item, dict)
                ],
            },
            "anthropicCompat": {
                "enabled": bool(config.openai_compat.enabled),
                "adminRelayOnly": bool(config.openai_compat.admin_relay_only),
                "available": bool(config.enabled and config.openai_compat.enabled and openai_compat_tokens),
                "tokenCount": len(openai_compat_tokens),
                "modelAliases": list(config.openai_compat.model_aliases or ["v8os"]),
                "baseUrlHint": "http://localhost:9528/api/network-supervisor/anthropic",
                "messagesPath": "/v1/messages",
                "modelsPath": "/v1/models",
                "authSchemes": ["x-api-key", "Authorization: Bearer"],
            },
            "compatIngress": {
                "maxExternalPayloadTokens": int(config.openai_compat.max_external_tools_payload_tokens or 0),
                "recent": get_recent_compat_ingress_events(limit=5),
            },
            "pendingExternalTools": self.pending_external_tools_summary(limit=8),
            "delegationAvailability": self._delegation_availability_payload(),
            "toolAvailability": {
                "delegate_network_task": self._delegation_availability_payload(),
            },
        }

    def list_peers(self) -> list[dict[str, Any]]:
        config = self.get_config_model()
        state = self.read_state()
        discovered = dict(state.get("discoveredPeers") or {})
        trusted = self._trusted_peer_map()
        peer_tokens = self._peer_token_map()
        expiry_seconds = int(config.discovery.peer_expiry_seconds or 60)
        now = _utc_now()
        peer_ids = sorted(set(discovered.keys()) | set(trusted.keys()))
        payload: list[dict[str, Any]] = []
        for peer_id in peer_ids:
            trusted_peer = trusted.get(peer_id)
            discovered_peer = dict(discovered.get(peer_id) or {})
            payload.append(
                self._peer_view_payload(
                    peer_id=peer_id,
                    trusted_peer=trusted_peer,
                    discovered_peer=discovered_peer,
                    peer_tokens=peer_tokens,
                    expiry_seconds=expiry_seconds,
                    now=now,
                )
            )
        payload.sort(key=lambda item: (not item["trusted"], not item["online"], item["peerId"]))
        return payload

    def list_peers_payload(self) -> dict[str, Any]:
        items = self.list_peers()
        trusted_items = [item for item in items if bool(item.get("trusted"))]
        discovered_items = [item for item in items if bool(item.get("discovered")) and not bool(item.get("trusted"))]
        try:
            from core.v8_link import build_mesh_provider_status

            mesh_candidates = list(build_mesh_provider_status().get("peerCandidates") or [])
        except Exception:
            mesh_candidates = []
        return {
            "items": items,
            "trustedItems": trusted_items,
            "discoveredItems": discovered_items,
            "meshCandidates": mesh_candidates,
        }

    def upsert_peer(self, payload: NetworkPeerMutationPayload) -> dict[str, Any]:
        config = self.get_config_model()
        trusted = {item.peer_id: item for item in config.trust.trusted_peers}
        existing = trusted.get(payload.peer_id)
        next_peer = TrustedPeerConfig.model_validate(
            {
                "peerId": payload.peer_id,
                "displayName": payload.display_name if payload.display_name is not None else (existing.display_name if existing else payload.peer_id),
                "baseUrl": payload.base_url if payload.base_url is not None else (existing.base_url if existing else ""),
                "wsUrl": payload.ws_url if payload.ws_url is not None else (existing.ws_url if existing else ""),
                "transportProfileId": (
                    payload.transport_profile_id
                    if payload.transport_profile_id is not None
                    else (existing.transport_profile_id if existing else None)
                ),
                "peerBaseUrl": payload.peer_base_url if payload.peer_base_url is not None else (existing.peer_base_url if existing else None),
                "publicKey": payload.public_key if payload.public_key is not None else (existing.public_key if existing else ""),
                "allowedScopes": payload.allowed_scopes if payload.allowed_scopes is not None else (list(existing.allowed_scopes) if existing else []),
                "allowedWorkspaces": payload.allowed_workspaces if payload.allowed_workspaces is not None else (list(existing.allowed_workspaces) if existing else []),
            }
        )
        trusted[next_peer.peer_id] = next_peer
        config.trust.trusted_peers = list(trusted.values())
        self.save_config_model(config)

        secrets_payload = self.read_secrets()
        peer_tokens = dict(secrets_payload.get("peerTokens") or {})
        if payload.peer_token is not None:
            normalized = str(payload.peer_token or "").strip()
            if normalized:
                peer_tokens[payload.peer_id] = normalized
            else:
                peer_tokens.pop(payload.peer_id, None)
            secrets_payload["peerTokens"] = peer_tokens
            self.write_secrets(secrets_payload)
        return {"ok": True, "peerId": payload.peer_id}

    def delete_peer(self, peer_id: str) -> dict[str, Any]:
        config = self.get_config_model()
        config.trust.trusted_peers = [item for item in config.trust.trusted_peers if item.peer_id != peer_id]
        self.save_config_model(config)
        secrets_payload = self.read_secrets()
        peer_tokens = dict(secrets_payload.get("peerTokens") or {})
        peer_tokens.pop(peer_id, None)
        secrets_payload["peerTokens"] = peer_tokens
        self.write_secrets(secrets_payload)
        return {"ok": True, "peerId": peer_id}

    def get_delegation(self, delegation_id: str) -> dict[str, Any]:
        state = self.read_state()
        delegations = dict(state.get("delegations") or {})
        entry = delegations.get(delegation_id)
        if not isinstance(entry, dict):
            raise HTTPException(status_code=404, detail=f"Unknown delegationId: {delegation_id}")
        return dict(entry)

    def _network_source(self, node: str) -> RuntimeSource:
        return RuntimeSource(plane="engine", component="network_supervisor", node=node, agent_id="network_supervisor")

    def _network_title(self, task: str, *, peer_id: str) -> str:
        normalized = str(task or "").strip() or "Remote delegation"
        title = normalized[:72]
        return f"Network -> {peer_id}: {title}"

    def _delegation_entry(self, delegation_id: str) -> dict[str, Any]:
        state = self.read_state()
        delegations = dict(state.get("delegations") or {})
        entry = dict(delegations.get(delegation_id) or {})
        entry.setdefault("delegationId", delegation_id)
        return entry

    def _store_delegation(self, delegation_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        state = self.read_state()
        delegations = dict(state.get("delegations") or {})
        current = dict(delegations.get(delegation_id) or {})
        current.update({key: value for key, value in dict(updates or {}).items() if value is not None})
        current["delegationId"] = delegation_id
        current["updatedAt"] = _utc_iso()
        delegations[delegation_id] = current
        state["delegations"] = delegations
        self.write_state(state)
        return current

    def _delegation_trace(
        self,
        *,
        delegation_id: str,
        source_run_id: str | None = None,
        source_session_id: str | None = None,
        workflow_id: str | None = None,
    ) -> NetworkTraceContext:
        return NetworkTraceContext(
            source_run_id=source_run_id,
            source_session_id=source_session_id,
            workflow_id=workflow_id,
            delegation_id=delegation_id,
        )

    def _assert_peer_scope_access(
        self,
        trusted_peer: TrustedPeerConfig,
        *,
        scope_hint: str | None,
        workspace_id: str | None,
        workspace_path: str | None,
    ) -> None:
        config = self.get_config_model()
        allowed_scopes = list(trusted_peer.allowed_scopes or config.trust.allowed_scopes or [])
        if allowed_scopes and scope_hint and scope_hint not in allowed_scopes:
            raise HTTPException(status_code=403, detail=f"Peer '{trusted_peer.peer_id}' is not allowed to access scope '{scope_hint}'")

        allowed_workspaces = {
            str(item).strip()
            for item in list(trusted_peer.allowed_workspaces or [])
            if str(item).strip()
        }
        if allowed_workspaces:
            candidates = {str(value).strip() for value in [workspace_id, workspace_path] if str(value or "").strip()}
            if not candidates or candidates.isdisjoint(allowed_workspaces):
                raise HTTPException(status_code=403, detail=f"Peer '{trusted_peer.peer_id}' is not allowed to access the requested workspace")

    def _ensure_session(
        self,
        *,
        session_id: str,
        title: str,
        user_id: str,
        metadata: dict[str, Any],
    ) -> None:
        db.create_or_update_session(
            session_id=session_id,
            title=title,
            user_id=user_id or "anonymous",
            metadata=metadata,
        )

    def _maybe_auto_enroll_peer(self, envelope: NetworkEnvelope, *, public_key: str) -> None:
        config = self.get_config_model()
        if config.trust.enrollment_mode != "open":
            return
        if envelope.from_peer_id in self._trusted_peer_map():
            return
        payload = dict(envelope.payload or {})
        base_url = str(payload.get("baseUrl") or "").strip()
        if not base_url:
            return
        next_peer = TrustedPeerConfig.model_validate(
            {
                "peerId": envelope.from_peer_id,
                "displayName": str(payload.get("displayName") or "").strip() or envelope.from_peer_id,
                "baseUrl": base_url,
                "wsUrl": str(payload.get("wsUrl") or "").strip(),
                "transportProfileId": str(payload.get("transportProfileId") or "").strip() or None,
                "peerBaseUrl": str(payload.get("peerBaseUrl") or "").strip() or None,
                "publicKey": public_key,
                "allowedScopes": list(config.trust.allowed_scopes or []),
                "allowedWorkspaces": [],
            }
        )
        peers = {item.peer_id: item for item in config.trust.trusted_peers}
        peers[next_peer.peer_id] = next_peer
        config.trust.trusted_peers = list(peers.values())
        self.save_config_model(config)

    async def _send_callback(
        self,
        *,
        peer_id: str,
        message_type: str,
        payload: dict[str, Any],
        trace: NetworkTraceContext,
    ) -> dict[str, Any]:
        envelope = self.build_envelope(
            message_type=message_type,
            to_peer_id=peer_id,
            payload=payload,
            trace=trace,
            expires_in_seconds=60,
        )
        return await self._post_peer(peer_id, "peer/delegations", envelope)

    async def join_peer(self, peer_id: str) -> dict[str, Any]:
        endpoint = self._peer_endpoint(peer_id)
        trace = self._delegation_trace(delegation_id=f"join_{uuid.uuid4().hex}")
        local_identity = self._local_identity()
        envelope = self.build_envelope(
            message_type="peer.join_request",
            to_peer_id=peer_id,
            payload={
                "peerId": local_identity["peerId"],
                "displayName": self.get_config_model().node.display_name,
                "baseUrl": self._local_identity()["advertisedBaseUrl"],
                "wsUrl": self._local_identity()["advertisedWsUrl"],
                "transportProfileId": self.get_config_model().node.transport_profile_id or "",
                "peerBaseUrl": self._local_identity()["peerBaseUrl"],
                "publicKey": local_identity["publicKey"],
                "publicKeyFingerprint": local_identity["publicKeyFingerprint"],
                "baseUrlHint": str(endpoint.get("baseUrl") or "").strip(),
            },
            trace=trace,
        )
        response_payload = await self._post_peer(peer_id, "peer/join", envelope)
        response_envelope = NetworkEnvelope.model_validate(response_payload)
        verified = self.verify_envelope(response_envelope, allow_untrusted=True)
        self._merge_discovered_peer(
            {
                "peerId": response_envelope.from_peer_id,
                "displayName": str(response_envelope.payload.get("displayName") or response_envelope.from_peer_id),
                "baseUrl": str(response_envelope.payload.get("baseUrl") or "").strip(),
                "wsUrl": str(response_envelope.payload.get("wsUrl") or "").strip(),
                "transportProfileId": str(response_envelope.payload.get("transportProfileId") or "").strip(),
                "peerBaseUrl": str(response_envelope.payload.get("peerBaseUrl") or "").strip(),
                "publicKey": verified["publicKey"],
                "publicKeyFingerprint": _fingerprint(verified["publicKey"]),
                "source": "join",
            }
        )
        return {"ok": True, "messageType": response_envelope.message_type, "peer": response_envelope.payload}

    async def challenge_peer(self, peer_id: str, note: str | None = None) -> dict[str, Any]:
        endpoint = self._peer_endpoint(peer_id)
        if not self._has_peer_token(peer_id):
            raise HTTPException(status_code=400, detail=f"Peer '{peer_id}' is missing a peer token for challenge")
        envelope = self.build_envelope(
            message_type="peer.challenge_request",
            to_peer_id=peer_id,
            payload={
                "note": str(note or "").strip(),
                "requestedBy": self._local_identity()["peerId"],
                "publicKeyFingerprint": self._local_identity()["publicKeyFingerprint"],
            },
            trace=self._delegation_trace(delegation_id=f"challenge_{uuid.uuid4().hex}"),
        )
        response_payload = await self._post_peer(peer_id, "peer/challenge", envelope)
        response_envelope = NetworkEnvelope.model_validate(response_payload)
        self.verify_envelope(response_envelope, provided_public_key=str(endpoint.get("publicKey") or "").strip() or None)
        return {"ok": True, "messageType": response_envelope.message_type, "payload": response_envelope.payload}

    async def wake_peer(self, peer_id: str, note: str | None = None, delegation_hint: str | None = None) -> dict[str, Any]:
        config = self.get_config_model()
        if not config.wake.enabled:
            raise HTTPException(status_code=400, detail="Directed wake is disabled")
        trusted = self._trusted_peer(peer_id)
        envelope = self.build_envelope(
            message_type="wake.request",
            to_peer_id=peer_id,
            payload={
                "note": str(note or "").strip(),
                "delegationHint": str(delegation_hint or "").strip() or None,
            },
            trace=self._delegation_trace(delegation_id=f"wake_{uuid.uuid4().hex}"),
            expires_in_seconds=max(10, int(config.wake.ack_timeout_seconds or 10)),
        )
        response_payload = await self._post_peer(trusted.peer_id, "peer/wake", envelope)
        response_envelope = NetworkEnvelope.model_validate(response_payload)
        self.verify_envelope(response_envelope)
        return {"ok": True, "messageType": response_envelope.message_type, "payload": response_envelope.payload}

    async def delegate_task(
        self,
        *,
        peer_id: str,
        task: str,
        timeout_seconds: int | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        scope_hint: str | None = None,
    ) -> dict[str, Any]:
        config = self.get_config_model()
        if not config.enabled:
            raise HTTPException(status_code=400, detail="Network supervisor runtime is disabled")
        if not config.delegation.enabled:
            raise HTTPException(status_code=400, detail="Remote delegation is disabled")
        trusted = self._trusted_peer(peer_id)
        resolved_task = str(task or "").strip()
        if not resolved_task:
            raise HTTPException(status_code=400, detail="Delegation task cannot be empty")

        runtime_context = get_runtime_context()
        session_id = str(runtime_context.get("session_id") or f"network_outbound_{uuid.uuid4().hex}").strip()
        conversation_id = str(runtime_context.get("conversation_id") or session_id).strip()
        user_id = str(runtime_context.get("user_id") or "anonymous").strip() or "anonymous"
        source_run_id = str(runtime_context.get("run_id") or "").strip() or None
        resolved_project_id = str(project_id or runtime_context.get("project_id") or "").strip() or None
        resolved_workspace_id = str(workspace_id or runtime_context.get("workspace_id") or "").strip() or None
        resolved_workspace_path = str(workspace_path or runtime_context.get("workspace_path") or "").strip() or None
        resolved_scope_hint = str(scope_hint or runtime_context.get("resolved_scope") or runtime_context.get("scope_hint") or "").strip() or None

        delegation_id = f"delegation_{uuid.uuid4().hex}"
        self._ensure_session(
            session_id=session_id,
            title=self._network_title(resolved_task, peer_id=trusted.peer_id),
            user_id=user_id,
            metadata={
                "conversation_id": conversation_id,
                "project_id": resolved_project_id,
                "workspace_id": resolved_workspace_id,
                "workspace_path": resolved_workspace_path,
                "scope_hint": resolved_scope_hint,
            },
        )
        outer_handle = erc_kernel.submit_run(
            session_id=session_id,
            conversation_id=conversation_id,
            user_id=user_id,
            runtime_kind="network_supervisor",
            trigger_source="delegate_network_task",
            agent_id="network_supervisor",
            metadata={
                "peerId": trusted.peer_id,
                "direction": "outbound",
                "delegationId": delegation_id,
                "task": resolved_task,
                "sourceRunId": source_run_id,
                "project_id": resolved_project_id,
                "workspace_id": resolved_workspace_id,
                "workspace_path": resolved_workspace_path,
                "scope_hint": resolved_scope_hint,
            },
            initial_status="queued",
            component="network_supervisor",
            node="delegate_tool",
        )
        outer_handle.transition("running", reason="delegate_network_task", node="delegate_tool")
        workflow_ledger_service.activate_runtime_step(
            outer_handle.run_id,
            owner_runtime="network_supervisor",
            step_key="network.wait_remote",
            title="等待远端节点执行",
            owner_agent_id="network_supervisor",
            input_payload={"peerId": trusted.peer_id, "delegationId": delegation_id, "task": resolved_task},
        )
        outer_handle.emit(
            "network.delegation.started",
            {"delegationId": delegation_id, "peerId": trusted.peer_id, "task": resolved_task},
            source=self._network_source("delegate_tool"),
        )
        self._store_delegation(
            delegation_id,
            {
                "direction": "outbound",
                "peerId": trusted.peer_id,
                "status": "pending",
                "task": resolved_task,
                "outerRunId": outer_handle.run_id,
                "sourceRunId": source_run_id,
                "sourceSessionId": session_id,
                "projectId": resolved_project_id,
                "workspaceId": resolved_workspace_id,
                "workspacePath": resolved_workspace_path,
                "scopeHint": resolved_scope_hint,
                "createdAt": _utc_iso(),
            },
        )

        wait_timeout = int(timeout_seconds or config.delegation.default_timeout_seconds or 120)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiters[delegation_id] = future
        trace = self._delegation_trace(
            delegation_id=delegation_id,
            source_run_id=outer_handle.run_id,
            source_session_id=session_id,
            workflow_id=workflow_ledger_service.workflow_id_for_run(outer_handle.run_id),
        )
        try:
            response_payload = await self._post_peer(
                trusted.peer_id,
                "peer/delegations",
                self.build_envelope(
                    message_type="delegation.request",
                    to_peer_id=trusted.peer_id,
                    payload={
                        "task": resolved_task,
                        "projectId": resolved_project_id,
                        "workspaceId": resolved_workspace_id,
                        "workspacePath": resolved_workspace_path,
                        "scopeHint": resolved_scope_hint,
                    },
                    trace=trace,
                    expires_in_seconds=max(30, wait_timeout),
                ),
            )
            accepted_envelope = NetworkEnvelope.model_validate(response_payload)
            self.verify_envelope(accepted_envelope)
            self.handle_protocol_callback(accepted_envelope)
            callback_payload = await asyncio.wait_for(future, timeout=wait_timeout)
        except asyncio.TimeoutError as exc:
            self._store_delegation(delegation_id, {"status": "timed_out", "lastError": "remote timeout"})
            outer_handle.emit(
                "network.delegation.timed_out",
                {"delegationId": delegation_id, "peerId": trusted.peer_id, "timeoutSeconds": wait_timeout},
                source=self._network_source("delegate_tool"),
            )
            outer_handle.fail(f"远端节点 '{trusted.peer_id}' 在 {wait_timeout}s 内未返回结果", node="delegate_tool")
            raise HTTPException(status_code=504, detail=f"Peer '{trusted.peer_id}' timed out") from exc
        except HTTPException as exc:
            status_label = "rejected" if exc.status_code in {400, 401, 403, 404, 409} else "failed"
            self._store_delegation(delegation_id, {"status": status_label, "lastError": str(exc.detail)})
            outer_handle.emit(
                "network.delegation.failed",
                {"delegationId": delegation_id, "peerId": trusted.peer_id, "error": str(exc.detail)},
                source=self._network_source("delegate_tool"),
            )
            outer_handle.fail(f"远端委派失败：{exc.detail}", node="delegate_tool")
            raise
        finally:
            waiter = self._waiters.get(delegation_id)
            if waiter is future:
                self._waiters.pop(delegation_id, None)

        if str(callback_payload.get("status") or "").strip().lower() != "completed":
            detail = str(callback_payload.get("error") or "Remote delegation failed").strip()
            raise HTTPException(status_code=502, detail=detail)
        return {
            "delegationId": delegation_id,
            "peerId": trusted.peer_id,
            "status": "completed",
            "result": str(callback_payload.get("content") or "").strip(),
            "outerRunId": outer_handle.run_id,
        }

    def handle_peer_join_request(self, envelope: NetworkEnvelope) -> NetworkEnvelope:
        verified = self.verify_envelope(
            envelope,
            allow_untrusted=True,
            provided_public_key=str(envelope.payload.get("publicKey") or "").strip(),
        )
        self._maybe_auto_enroll_peer(envelope, public_key=verified["publicKey"])
        self._merge_discovered_peer(
            {
                "peerId": envelope.from_peer_id,
                "displayName": str(envelope.payload.get("displayName") or envelope.from_peer_id),
                "baseUrl": str(envelope.payload.get("baseUrl") or "").strip(),
                "wsUrl": str(envelope.payload.get("wsUrl") or "").strip(),
                "transportProfileId": str(envelope.payload.get("transportProfileId") or "").strip(),
                "peerBaseUrl": str(envelope.payload.get("peerBaseUrl") or "").strip(),
                "publicKey": verified["publicKey"],
                "publicKeyFingerprint": _fingerprint(verified["publicKey"]),
                "source": "join",
            }
        )
        return self.build_envelope(
            message_type="peer.join_response",
            to_peer_id=envelope.from_peer_id,
            payload={
                "requestMessageId": envelope.message_id,
                "peerId": self._local_identity()["peerId"],
                "displayName": self.get_config_model().node.display_name,
                "baseUrl": self._local_identity()["advertisedBaseUrl"],
                "wsUrl": self._local_identity()["advertisedWsUrl"],
                "transportProfileId": self.get_config_model().node.transport_profile_id or "",
                "peerBaseUrl": self._local_identity()["peerBaseUrl"],
                "publicKey": self._local_identity()["publicKey"],
                "publicKeyFingerprint": self._local_identity()["publicKeyFingerprint"],
                "trusted": envelope.from_peer_id in self._trusted_peer_map(),
            },
            trace=envelope.trace,
        )

    def handle_peer_challenge_request(self, envelope: NetworkEnvelope) -> NetworkEnvelope:
        verified = self.verify_envelope(envelope)
        return self.build_envelope(
            message_type="peer.challenge_response",
            to_peer_id=envelope.from_peer_id,
            payload={
                "requestMessageId": envelope.message_id,
                "ok": True,
                "peerId": self._local_identity()["peerId"],
                "displayName": self.get_config_model().node.display_name,
                "baseUrl": self._local_identity()["advertisedBaseUrl"],
                "wsUrl": self._local_identity()["advertisedWsUrl"],
                "transportProfileId": self.get_config_model().node.transport_profile_id or "",
                "peerBaseUrl": self._local_identity()["peerBaseUrl"],
                "publicKey": self._local_identity()["publicKey"],
                "publicKeyFingerprint": self._local_identity()["publicKeyFingerprint"],
                "receivedAt": _utc_iso(),
                "verifiedPeerFingerprint": _fingerprint(verified["publicKey"]),
                "note": str(envelope.payload.get("note") or "").strip(),
            },
            trace=envelope.trace,
        )

    def handle_peer_wake_request(self, envelope: NetworkEnvelope) -> NetworkEnvelope:
        config = self.get_config_model()
        if envelope.message_type != "wake.request" or not config.enabled or not config.wake.enabled:
            raise HTTPException(status_code=403, detail="Wake request disabled or invalid")
        self.verify_envelope(envelope)
        return self.build_envelope(
            message_type="wake.ack",
            to_peer_id=envelope.from_peer_id,
            payload={
                "requestMessageId": envelope.message_id,
                "ok": True,
                "peerId": self._local_identity()["peerId"],
                "receivedAt": _utc_iso(),
                "delegationHint": str(envelope.payload.get("delegationHint") or "").strip() or None,
            },
            trace=envelope.trace,
            expires_in_seconds=max(10, int(self.get_config_model().wake.ack_timeout_seconds or 10)),
        )

    async def handle_peer_delegations(self, envelope: NetworkEnvelope) -> NetworkEnvelope:
        if envelope.message_type == "delegation.request":
            if not self.get_config_model().enabled:
                raise HTTPException(status_code=403, detail="Network runtime is disabled")
            verified = self.verify_envelope(envelope)
            trusted_peer = self._trusted_peer(envelope.from_peer_id)
            self._assert_peer_scope_access(
                trusted_peer,
                scope_hint=str(envelope.payload.get("scopeHint") or "").strip() or None,
                workspace_id=str(envelope.payload.get("workspaceId") or "").strip() or None,
                workspace_path=str(envelope.payload.get("workspacePath") or "").strip() or None,
            )
            return await self._handle_inbound_delegation_request(envelope, verified["publicKey"])

        if envelope.message_type not in {"delegation.accepted", "delegation.progress", "delegation.result", "delegation.failed"}:
            raise HTTPException(status_code=400, detail="Unsupported delegation callback")
        self.verify_envelope(envelope)
        self.handle_protocol_callback(envelope)
        return self.build_envelope(
            message_type=f"{envelope.message_type}.ack",
            to_peer_id=envelope.from_peer_id,
            payload={"ok": True, "receivedAt": _utc_iso(), "requestMessageId": envelope.message_id},
            trace=envelope.trace,
        )

    async def _handle_inbound_delegation_request(self, envelope: NetworkEnvelope, public_key: str) -> NetworkEnvelope:
        config = self.get_config_model()
        if not config.delegation.enabled:
            raise HTTPException(status_code=400, detail="Remote delegation is disabled on target node")
        active_inbound = len([task for task in self._active_inbound_tasks.values() if not task.done()])
        max_concurrent = max(1, int(config.delegation.max_concurrent or 1))
        if active_inbound >= max_concurrent:
            raise HTTPException(status_code=429, detail="Inbound delegation concurrency limit reached")

        delegation_id = str(envelope.trace.delegation_id or "").strip() or f"delegation_{uuid.uuid4().hex}"
        existing = self._delegation_entry(delegation_id)
        if existing:
            if existing.get("peerId") != envelope.from_peer_id or existing.get("direction") != "inbound":
                raise HTTPException(status_code=403, detail="Delegation identity belongs to another peer")
            if existing.get("task") != str(envelope.payload.get("task") or "").strip():
                raise HTTPException(status_code=409, detail="Delegation identity reused with a different task")
            existing_status = str(existing.get("status") or "").strip().lower()
            if existing_status == "completed":
                return self.build_envelope(
                    message_type="delegation.result",
                    to_peer_id=envelope.from_peer_id,
                    payload={
                        "requestMessageId": envelope.message_id,
                        "delegationId": delegation_id,
                        "status": "completed",
                        "content": str(existing.get("result") or "").strip(),
                        "childRunId": existing.get("childRunId"),
                        "outerRunId": existing.get("outerRunId"),
                    },
                    trace=envelope.trace,
                    expires_in_seconds=120,
                )
            if existing_status == "failed":
                return self.build_envelope(
                    message_type="delegation.failed",
                    to_peer_id=envelope.from_peer_id,
                    payload={
                        "requestMessageId": envelope.message_id,
                        "delegationId": delegation_id,
                        "status": "failed",
                        "error": str(existing.get("lastError") or "Delegation already failed"),
                    },
                    trace=envelope.trace,
                    expires_in_seconds=120,
                )
            return self.build_envelope(
                message_type="delegation.accepted",
                to_peer_id=envelope.from_peer_id,
                payload={
                    "requestMessageId": envelope.message_id,
                    "delegationId": delegation_id,
                    "status": existing_status or "accepted",
                    "outerRunId": existing.get("outerRunId"),
                    "childRunId": existing.get("childRunId"),
                },
                trace=envelope.trace,
                expires_in_seconds=120,
            )

        source_session_id = str(envelope.trace.source_session_id or "").strip() or f"network_source_{delegation_id}"
        inbound_session_id = f"network_inbound_{delegation_id}"
        self._ensure_session(
            session_id=inbound_session_id,
            title=self._network_title(str(envelope.payload.get("task") or ""), peer_id=envelope.from_peer_id),
            user_id="network-peer",
            metadata={
                "delegationId": delegation_id,
                "direction": "inbound",
                "sourcePeerId": envelope.from_peer_id,
                "sourceSessionId": source_session_id,
            },
        )
        outer_handle = erc_kernel.submit_run(
            session_id=inbound_session_id,
            conversation_id=inbound_session_id,
            user_id="network-peer",
            runtime_kind="network_supervisor",
            trigger_source="peer.delegation_request",
            agent_id="network_supervisor",
            metadata={
                "delegationId": delegation_id,
                "peerId": envelope.from_peer_id,
                "direction": "inbound",
                "task": str(envelope.payload.get("task") or "").strip(),
                "sourceRunId": envelope.trace.source_run_id,
                "sourceSessionId": source_session_id,
                "workflowId": envelope.trace.workflow_id,
            },
            initial_status="queued",
            component="network_supervisor",
            node="peer_ingress",
        )
        outer_handle.transition("running", reason="peer.delegation_request", node="peer_ingress")
        workflow_ledger_service.activate_runtime_step(
            outer_handle.run_id,
            owner_runtime="network_supervisor",
            step_key="network.receive",
            title="接收远端委派",
            owner_agent_id="network_supervisor",
            input_payload={"delegationId": delegation_id, "sourcePeerId": envelope.from_peer_id},
        )
        self._store_delegation(
            delegation_id,
            {
                "direction": "inbound",
                "peerId": envelope.from_peer_id,
                "status": "accepted",
                "task": str(envelope.payload.get("task") or "").strip(),
                "outerRunId": outer_handle.run_id,
                "sourceRunId": envelope.trace.source_run_id,
                "sourceSessionId": source_session_id,
                "projectId": str(envelope.payload.get("projectId") or "").strip() or None,
                "workspaceId": str(envelope.payload.get("workspaceId") or "").strip() or None,
                "workspacePath": str(envelope.payload.get("workspacePath") or "").strip() or None,
                "scopeHint": str(envelope.payload.get("scopeHint") or "").strip() or None,
                "createdAt": _utc_iso(),
                "publicKeyFingerprint": _fingerprint(public_key),
            },
        )
        outer_handle.emit(
            "network.delegation.received",
            {"delegationId": delegation_id, "sourcePeerId": envelope.from_peer_id, "task": str(envelope.payload.get("task") or "").strip()},
            source=self._network_source("peer_ingress"),
        )
        task = asyncio.create_task(self._execute_inbound_delegation(envelope=envelope, outer_run_id=outer_handle.run_id))
        self._active_inbound_tasks[delegation_id] = task
        task.add_done_callback(lambda _: self._active_inbound_tasks.pop(delegation_id, None))
        return self.build_envelope(
            message_type="delegation.accepted",
            to_peer_id=envelope.from_peer_id,
            payload={"delegationId": delegation_id, "status": "accepted", "outerRunId": outer_handle.run_id,
                     "requestMessageId": envelope.message_id},
            trace=envelope.trace,
            expires_in_seconds=max(60, int(self.get_config_model().delegation.default_timeout_seconds or 120)),
        )

    def handle_protocol_callback(self, envelope: NetworkEnvelope) -> dict[str, Any]:
        delegation_id = str(envelope.trace.delegation_id or "").strip()
        if not delegation_id:
            raise HTTPException(status_code=400, detail="Missing delegationId in trace")
        entry = self._delegation_entry(delegation_id)
        if not entry or entry.get("direction") != "outbound" or entry.get("peerId") != envelope.from_peer_id:
            raise HTTPException(status_code=403, detail="Delegation callback sender does not own this task")
        if envelope.payload.get("delegationId") not in (None, "", delegation_id):
            raise HTTPException(status_code=409, detail="Delegation callback identity mismatch")
        settled = str(entry.get("status") or "")
        if settled in {"completed", "failed", "cancelled"}:
            duplicate_result = settled == "completed" and envelope.message_type == "delegation.result" and str(envelope.payload.get("content") or "").strip() == str(entry.get("result") or "").strip()
            duplicate_failure = settled == "failed" and envelope.message_type == "delegation.failed" and str(envelope.payload.get("error") or "Remote delegation failed").strip() == str(entry.get("lastError") or "").strip()
            if duplicate_result or duplicate_failure:
                return {"status": settled, "duplicate": True}
            raise HTTPException(status_code=409, detail="Delegation is already terminal")
        outer_run_id = str(entry.get("outerRunId") or "").strip()
        outer_handle = erc_kernel.attach_run(outer_run_id, component="network_supervisor", node="callback") if outer_run_id else None
        payload = dict(envelope.payload or {})

        if envelope.message_type == "delegation.accepted":
            self._store_delegation(delegation_id, {"status": "accepted", "acceptedAt": _utc_iso()})
            if outer_handle:
                outer_handle.emit(
                    "network.delegation.accepted",
                    {"delegationId": delegation_id, "peerId": envelope.from_peer_id},
                    source=self._network_source("callback"),
                )
            return {"status": "accepted"}

        if envelope.message_type == "delegation.progress":
            progress = str(payload.get("progress") or payload.get("content") or "").strip()
            self._store_delegation(
                delegation_id,
                {
                    "status": "running",
                    "lastProgressAt": _utc_iso(),
                    "progress": progress[:1200],
                    "childRunId": payload.get("childRunId"),
                },
            )
            if outer_handle:
                run_service.update_metadata(
                    outer_handle.run_id,
                    {"networkProgress": progress[:1200], "networkChildRunId": payload.get("childRunId")},
                )
                outer_handle.emit(
                    "network.delegation.progress",
                    {
                        "delegationId": delegation_id,
                        "peerId": envelope.from_peer_id,
                        "progress": progress[:1200],
                        "childRunId": payload.get("childRunId"),
                    },
                    source=self._network_source("callback"),
                )
            return {"status": "running"}

        if envelope.message_type == "delegation.result":
            content = str(payload.get("content") or "").strip()
            self._store_delegation(
                delegation_id,
                {
                    "status": "completed",
                    "completedAt": _utc_iso(),
                    "result": content,
                    "childRunId": payload.get("childRunId"),
                },
            )
            if outer_handle:
                run_service.update_metadata(
                    outer_handle.run_id,
                    {
                        "networkResult": content[:4000],
                        "networkChildRunId": payload.get("childRunId"),
                    },
                )
                outer_handle.emit(
                    "network.delegation.result",
                    {
                        "delegationId": delegation_id,
                        "peerId": envelope.from_peer_id,
                        "content": content,
                        "childRunId": payload.get("childRunId"),
                    },
                    source=self._network_source("callback"),
                )
                outer_handle.complete(reason="remote delegation completed", node="callback")
            waiter = self._waiters.get(delegation_id)
            if waiter and not waiter.done():
                waiter.set_result({"status": "completed", "content": content})
            return {"status": "completed", "content": content}

        if envelope.message_type == "delegation.failed":
            error_message = str(payload.get("error") or "Remote delegation failed").strip()
            self._store_delegation(
                delegation_id,
                {
                    "status": "failed",
                    "failedAt": _utc_iso(),
                    "lastError": error_message,
                    "childRunId": payload.get("childRunId"),
                },
            )
            if outer_handle:
                outer_handle.emit(
                    "network.delegation.failed",
                    {
                        "delegationId": delegation_id,
                        "peerId": envelope.from_peer_id,
                        "error": error_message,
                        "childRunId": payload.get("childRunId"),
                    },
                    source=self._network_source("callback"),
                )
                outer_handle.fail(error_message, node="callback")
            waiter = self._waiters.get(delegation_id)
            if waiter and not waiter.done():
                waiter.set_result({"status": "failed", "error": error_message})
            return {"status": "failed", "error": error_message}

        if envelope.message_type == "wake.ack":
            return {"status": "acknowledged", "payload": payload}

        raise HTTPException(status_code=400, detail=f"Unsupported callback message type: {envelope.message_type}")

    async def _execute_inbound_delegation(self, *, envelope: NetworkEnvelope, outer_run_id: str) -> None:
        delegation_id = str(envelope.trace.delegation_id or "").strip()
        outer_handle = erc_kernel.attach_run(outer_run_id, component="network_supervisor", node="executor")
        if outer_handle is None:
            self._store_delegation(delegation_id, {"status": "failed", "lastError": "Missing outer run"})
            return

        child_session_id = f"network_exec_{delegation_id}"
        self._ensure_session(
            session_id=child_session_id,
            title=self._network_title(str(envelope.payload.get("task") or ""), peer_id=envelope.from_peer_id),
            user_id="network-peer",
            metadata={"delegationId": delegation_id, "direction": "inbound_child", "sourcePeerId": envelope.from_peer_id},
        )
        workflow_ledger_service.activate_runtime_step(
            outer_run_id,
            owner_runtime="network_supervisor",
            step_key="network.execute_local",
            title="在本地 chat runtime 执行远端任务",
            owner_agent_id="network_supervisor",
            input_payload={"delegationId": delegation_id, "childSessionId": child_session_id},
        )
        workspace_binding = resolve_network_neighbor_workspace_binding(
            peer_id=envelope.from_peer_id,
            local_role="companion",
            remote_project_id=str(envelope.payload.get("projectId") or "").strip() or None,
            remote_workspace_id=str(envelope.payload.get("workspaceId") or "").strip() or None,
            remote_workspace_path=str(envelope.payload.get("workspacePath") or "").strip() or None,
        )
        request = ChatRequest.model_validate(
            {
                "messages": [{"role": "user", "content": str(envelope.payload.get("task") or "").strip()}],
                "stream": True,
                "sessionId": child_session_id,
                "conversationId": child_session_id,
                "userId": "network-peer",
                "projectId": workspace_binding.get("projectId"),
                "workspaceId": workspace_binding.get("workspaceId"),
                "workspacePath": workspace_binding.get("workspacePath"),
                "scopeHint": str(envelope.payload.get("scopeHint") or "").strip() or None,
            }
        )
        trace = envelope.trace
        child_run_id: str | None = None
        aggregated_text = ""
        last_progress_at = 0.0
        try:
            async for event in _get_chat_runtime().stream_legacy_events(request, transport="network_supervisor"):
                event_type = str(event.get("type") or "").strip()
                if event_type == "connected":
                    child_run_id = str(event.get("run_id") or "").strip() or None
                    if child_run_id:
                        run_service.update_metadata(outer_run_id, {"networkChildRunId": child_run_id})
                        self._store_delegation(delegation_id, {"childRunId": child_run_id, "status": "running"})
                elif event_type == "text_chunk":
                    chunk = str(event.get("content") or "")
                    if not chunk:
                        continue
                    aggregated_text += chunk
                    now = time.time()
                    if now - last_progress_at >= 0.8:
                        last_progress_at = now
                        await self._send_callback(
                            peer_id=envelope.from_peer_id,
                            message_type="delegation.progress",
                            payload={
                                "delegationId": delegation_id,
                                "status": "running",
                                "progress": aggregated_text[-1200:],
                                "childRunId": child_run_id,
                            },
                            trace=trace,
                        )
                elif event_type == "done":
                    status = str(event.get("status") or "").strip().lower()
                    if status == "finished":
                        await self._send_callback(
                            peer_id=envelope.from_peer_id,
                            message_type="delegation.result",
                            payload={
                                "delegationId": delegation_id,
                                "status": "completed",
                                "content": aggregated_text,
                                "childRunId": child_run_id,
                            },
                            trace=trace,
                        )
                        self._store_delegation(delegation_id, {"status": "completed", "completedAt": _utc_iso(), "result": aggregated_text, "childRunId": child_run_id})
                        workflow_ledger_service.activate_runtime_step(
                            outer_run_id,
                            owner_runtime="network_supervisor",
                            step_key="network.return_result",
                            title="把执行结果返回给来源节点",
                            owner_agent_id="network_supervisor",
                            projection_payload={"delegationId": delegation_id, "childRunId": child_run_id},
                        )
                        outer_handle.complete(reason="network inbound delegation completed", node="executor")
                    else:
                        error_message = f"Child chat runtime exited with status '{status or 'unknown'}'"
                        await self._send_callback(
                            peer_id=envelope.from_peer_id,
                            message_type="delegation.failed",
                            payload={"delegationId": delegation_id, "status": "failed", "error": error_message, "childRunId": child_run_id},
                            trace=trace,
                        )
                        self._store_delegation(delegation_id, {"status": "failed", "failedAt": _utc_iso(), "lastError": error_message, "childRunId": child_run_id})
                        outer_handle.fail(error_message, node="executor")
                    return
                elif event_type == "error":
                    error_message = str(event.get("error") or "Unknown remote execution error").strip()
                    await self._send_callback(
                        peer_id=envelope.from_peer_id,
                        message_type="delegation.failed",
                        payload={"delegationId": delegation_id, "status": "failed", "error": error_message, "childRunId": child_run_id},
                        trace=trace,
                    )
                    self._store_delegation(delegation_id, {"status": "failed", "failedAt": _utc_iso(), "lastError": error_message, "childRunId": child_run_id})
                    outer_handle.fail(error_message, node="executor")
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_message = f"Network inbound delegation crashed: {exc}"
            try:
                await self._send_callback(
                    peer_id=envelope.from_peer_id,
                    message_type="delegation.failed",
                    payload={"delegationId": delegation_id, "status": "failed", "error": error_message, "childRunId": child_run_id},
                    trace=trace,
                )
            except Exception:
                pass
            self._store_delegation(delegation_id, {"status": "failed", "failedAt": _utc_iso(), "lastError": error_message, "childRunId": child_run_id})
            outer_handle.fail(error_message, node="executor")

    async def websocket_handshake(self, websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            await websocket.send_json({"type": "network_supervisor.hello", "status": self.status_payload()})
            while True:
                payload = await websocket.receive_json()
                await websocket.send_json({"type": "network_supervisor.status", "echo": payload, "status": self.status_payload()})
        except Exception:
            await websocket.close()


network_supervisor_service = NetworkSupervisorService()
def _get_chat_runtime():
    from runtimes.chat.runtime import chat_runtime

    return chat_runtime
