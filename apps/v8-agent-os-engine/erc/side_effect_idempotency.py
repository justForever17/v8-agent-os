from __future__ import annotations

import hashlib
import json
import uuid
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
    owner_id: str | None = None
    attempt_count: int = 0
    state: str | None = None

    @property
    def requires_reconciliation(self) -> bool:
        return self.reason == "unknown_outcome_reconciliation_required" or self.state == "indeterminate"

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
            "ownerId": self.owner_id,
            "attemptCount": self.attempt_count,
            "state": self.state,
            "reconciliationRequired": self.requires_reconciliation,
        }


class SideEffectIdempotencyService:
    @staticmethod
    def _legacy_completed_receipt_exists(session_id: str, *, idempotency_key: str) -> bool:
        for event in reversed(db.get_runtime_events(session_id)):
            topic = str(event.get("topic") or "")
            if topic not in {"side_effect.started", "side_effect.completed", "side_effect.failed"}:
                continue
            payload = event.get("payload") or {}
            if str(payload.get("idempotencyKey") or payload.get("idempotency_key") or "") != idempotency_key:
                continue
            return topic == "side_effect.completed"
        return False

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
        lease_seconds: int = 300,
        replay_safe: bool = False,
    ) -> SideEffectReceipt:
        idempotency_key, payload_fingerprint = self.build_idempotency_key(
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            step_key=step_key,
            target_identity=target_identity,
            payload=payload or {},
        )
        owner_id = f"{str(node or 'side_effect').strip() or 'side_effect'}:{uuid.uuid4().hex}"
        legacy_completed = (
            db.get_side_effect_receipt(idempotency_key) is None
            and self._legacy_completed_receipt_exists(
                run_handle.session_id,
                idempotency_key=idempotency_key,
            )
        )
        claim = db.claim_side_effect_receipt(
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            idempotency_key=idempotency_key,
            effect_kind=effect_kind,
            step_key=step_key,
            target_identity=target_identity,
            payload_fingerprint=payload_fingerprint,
            owner_id=owner_id,
            metadata=metadata or {},
            lease_seconds=lease_seconds,
            retry_failed=True,
            replay_safe=bool(replay_safe),
            legacy_completed=legacy_completed,
        )
        prior_topic = str(claim.get("prior_topic") or "").strip() or None
        if not bool(claim.get("execute")):
            receipt = SideEffectReceipt(
                execute=False,
                effect_kind=effect_kind,
                step_key=step_key,
                target_identity=target_identity,
                payload_fingerprint=payload_fingerprint,
                idempotency_key=idempotency_key,
                reason=str(claim.get("reason") or "existing_receipt_detected"),
                prior_topic=prior_topic,
                owner_id=owner_id,
                attempt_count=int(claim.get("attempt_count") or 0),
                state=str(claim.get("state") or "").strip() or None,
            )
            run_handle.emit(
                (
                    "side_effect.reconciliation_required"
                    if receipt.requires_reconciliation
                    else "side_effect.skipped_duplicate"
                ),
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
            reason=str(claim.get("reason") or "claimed"),
            prior_topic=prior_topic,
            owner_id=owner_id,
            attempt_count=int(claim.get("attempt_count") or 1),
            state=str(claim.get("state") or "claimed").strip() or "claimed",
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
    ) -> bool:
        if not receipt.execute:
            return True
        accepted = db.complete_side_effect_receipt(
            idempotency_key=receipt.idempotency_key,
            owner_id=str(receipt.owner_id or ""),
            result=result or {},
        )
        if not accepted:
            run_handle.emit(
                "side_effect.receipt_rejected",
                {**receipt.as_dict(), "attemptedState": "completed", "node": node},
            )
            return False
        run_handle.emit(
            "side_effect.completed",
            {
                **receipt.as_dict(),
                "result": _jsonable(result or {}),
                "node": node,
            },
        )
        return True

    def fail(
        self,
        *,
        run_handle,
        receipt: SideEffectReceipt,
        node: str,
        error: str,
    ) -> bool:
        if not receipt.execute:
            return True
        accepted = db.fail_side_effect_receipt(
            idempotency_key=receipt.idempotency_key,
            owner_id=str(receipt.owner_id or ""),
            error=str(error or ""),
        )
        if not accepted:
            run_handle.emit(
                "side_effect.receipt_rejected",
                {**receipt.as_dict(), "attemptedState": "failed", "node": node},
            )
            return False
        run_handle.emit(
            "side_effect.failed",
            {
                **receipt.as_dict(),
                "error": str(error or ""),
                "node": node,
            },
        )
        return True

    def reconcile(
        self,
        *,
        run_handle,
        receipt: SideEffectReceipt,
        node: str,
        outcome: str,
        evidence: Dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        accepted = db.reconcile_side_effect_receipt(
            idempotency_key=receipt.idempotency_key,
            outcome=outcome,
            evidence=evidence or {},
            error=error,
        )
        run_handle.emit(
            "side_effect.reconciled" if accepted else "side_effect.receipt_rejected",
            {
                **receipt.as_dict(),
                "attemptedState": str(outcome or "").strip().lower(),
                "node": node,
                "evidence": _jsonable(evidence or {}),
            },
        )
        return accepted

side_effect_idempotency_service = SideEffectIdempotencyService()
