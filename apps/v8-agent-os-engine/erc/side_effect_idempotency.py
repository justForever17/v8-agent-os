from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict

from core.database import db


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _fingerprint(payload: Dict[str, Any]) -> str:
    return hashlib.md5(
        json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


@dataclass(slots=True)
class SideEffectReceipt:
    execute: bool
    effect_kind: str
    step_key: str
    target_identity: str
    payload_fingerprint: str
    idempotency_key: str
    reason: str | None = None
    prior_topic: str | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "execute": self.execute,
            "effectKind": self.effect_kind,
            "stepKey": self.step_key,
            "targetIdentity": self.target_identity,
            "payloadFingerprint": self.payload_fingerprint,
            "idempotencyKey": self.idempotency_key,
            "reason": self.reason,
            "priorTopic": self.prior_topic,
        }


class SideEffectIdempotencyService:
    _RELEVANT_TOPICS = {
        "side_effect.started",
        "side_effect.completed",
        "side_effect.failed",
        "side_effect.skipped_duplicate",
    }

    def build_idempotency_key(
        self,
        *,
        session_id: str,
        run_id: str | None,
        step_key: str,
        target_identity: str,
        payload: Dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        payload_fingerprint = _fingerprint(payload or {})
        material = {
            "session_id": str(session_id or "").strip(),
            "run_id": str(run_id or "").strip(),
            "step_key": str(step_key or "").strip(),
            "target_identity": str(target_identity or "").strip(),
            "payload_fingerprint": payload_fingerprint,
        }
        return _fingerprint(material), payload_fingerprint

    def begin(
        self,
        *,
        run_handle,
        effect_kind: str,
        step_key: str,
        target_identity: str,
        payload: Dict[str, Any] | None = None,
        node: str,
        metadata: Dict[str, Any] | None = None,
    ) -> SideEffectReceipt:
        idempotency_key, payload_fingerprint = self.build_idempotency_key(
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            step_key=step_key,
            target_identity=target_identity,
            payload=payload or {},
        )
        latest = self._latest_receipt_event(run_handle.session_id, idempotency_key=idempotency_key)
        if latest is not None and str(latest.get("topic") or "") != "side_effect.failed":
            receipt = SideEffectReceipt(
                execute=False,
                effect_kind=effect_kind,
                step_key=step_key,
                target_identity=target_identity,
                payload_fingerprint=payload_fingerprint,
                idempotency_key=idempotency_key,
                reason="existing_receipt_detected",
                prior_topic=str(latest.get("topic") or ""),
            )
            run_handle.emit(
                "side_effect.skipped_duplicate",
                {
                    **receipt.as_dict(),
                    "metadata": _jsonable(metadata or {}),
                },
            )
            return receipt
        receipt = SideEffectReceipt(
            execute=True,
            effect_kind=effect_kind,
            step_key=step_key,
            target_identity=target_identity,
            payload_fingerprint=payload_fingerprint,
            idempotency_key=idempotency_key,
        )
        run_handle.emit(
            "side_effect.started",
            {
                **receipt.as_dict(),
                "metadata": _jsonable(metadata or {}),
            },
        )
        return receipt

    def complete(
        self,
        *,
        run_handle,
        receipt: SideEffectReceipt,
        node: str,
        result: Dict[str, Any] | None = None,
    ) -> None:
        if not receipt.execute:
            return
        run_handle.emit(
            "side_effect.completed",
            {
                **receipt.as_dict(),
                "result": _jsonable(result or {}),
                "node": node,
            },
        )

    def fail(
        self,
        *,
        run_handle,
        receipt: SideEffectReceipt,
        node: str,
        error: str,
    ) -> None:
        if not receipt.execute:
            return
        run_handle.emit(
            "side_effect.failed",
            {
                **receipt.as_dict(),
                "error": str(error or ""),
                "node": node,
            },
        )

    def _latest_receipt_event(self, session_id: str, *, idempotency_key: str) -> Dict[str, Any] | None:
        events = db.get_runtime_events(session_id)
        for event in reversed(events):
            if str(event.get("topic") or "") not in self._RELEVANT_TOPICS:
                continue
            payload = event.get("payload") or {}
            if str(payload.get("idempotencyKey") or payload.get("idempotency_key") or "") != idempotency_key:
                continue
            return event
        return None


side_effect_idempotency_service = SideEffectIdempotencyService()
