from __future__ import annotations

import threading

from core.database import DatabaseManager
from erc import side_effect_idempotency as module


class _Handle:
    session_id = "side-effect-session"
    run_id = "side-effect-run"

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, topic: str, payload: dict) -> dict:
        self.events.append((topic, payload))
        return payload


def _begin(service, handle, *, lease_seconds: int = 300, replay_safe: bool = False):
    return service.begin(
        run_handle=handle,
        effect_kind="test.effect",
        step_key="test.step",
        target_identity="target-1",
        payload={"value": "same"},
        node="test",
        lease_seconds=lease_seconds,
        replay_safe=replay_safe,
    )


def test_concurrent_begin_has_one_durable_claim(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "side-effects.db")
    monkeypatch.setattr(module, "db", database)
    service = module.SideEffectIdempotencyService()
    handles = [_Handle(), _Handle()]
    barrier = threading.Barrier(2)
    receipts = []

    def worker(handle):
        barrier.wait()
        receipts.append(_begin(service, handle))

    threads = [threading.Thread(target=worker, args=(handle,)) for handle in handles]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(receipt.execute for receipt in receipts) == 1
    assert sum(topic == "side_effect.started" for handle in handles for topic, _ in handle.events) == 1
    assert sum(topic == "side_effect.skipped_duplicate" for handle in handles for topic, _ in handle.events) == 1


def test_completed_receipt_never_replays_and_failed_receipt_can_retry(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "side-effects-retry.db")
    monkeypatch.setattr(module, "db", database)
    service = module.SideEffectIdempotencyService()
    first_handle = _Handle()
    first = _begin(service, first_handle)
    assert first.execute is True
    service.complete(run_handle=first_handle, receipt=first, node="test", result={"ok": True})

    duplicate = _begin(service, _Handle())
    assert duplicate.execute is False
    assert duplicate.reason == "completed_receipt"

    failed_handle = _Handle()
    failed = service.begin(
        run_handle=failed_handle,
        effect_kind="test.effect.failed",
        step_key="test.failed",
        target_identity="target-2",
        payload={"value": "failed"},
        node="test",
    )
    service.fail(run_handle=failed_handle, receipt=failed, node="test", error="injected")
    retried = service.begin(
        run_handle=_Handle(),
        effect_kind="test.effect.failed",
        step_key="test.failed",
        target_identity="target-2",
        payload={"value": "failed"},
        node="test",
    )
    assert retried.execute is True
    assert retried.reason == "failed_receipt_retry"


def test_expired_claim_requires_reconciliation_when_replay_is_not_proven_safe(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "side-effects-reclaim.db")
    monkeypatch.setattr(module, "db", database)
    service = module.SideEffectIdempotencyService()
    first = _begin(service, _Handle(), lease_seconds=1)
    assert first.execute is True
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_side_effect_receipts SET lease_expires_at = ? WHERE idempotency_key = ?",
            ("2000-01-01T00:00:00.000Z", first.idempotency_key),
        )
        conn.commit()

    reconciliation_handle = _Handle()
    unknown = _begin(service, reconciliation_handle, lease_seconds=1)
    assert unknown.execute is False
    assert unknown.reason == "unknown_outcome_reconciliation_required"
    assert unknown.requires_reconciliation is True
    assert [topic for topic, _ in reconciliation_handle.events] == [
        "side_effect.reconciliation_required"
    ]
    assert database.get_side_effect_receipt(first.idempotency_key)["state"] == "indeterminate"

    stale_handle = _Handle()
    assert service.complete(
        run_handle=stale_handle,
        receipt=first,
        node="test",
        result={"stale": True},
    ) is False
    assert [topic for topic, _ in stale_handle.events] == ["side_effect.receipt_rejected"]

    assert service.reconcile(
        run_handle=reconciliation_handle,
        receipt=unknown,
        node="test",
        outcome="completed",
        evidence={"externalState": "confirmed"},
    ) is True
    assert database.get_side_effect_receipt(first.idempotency_key)["state"] == "completed"
    duplicate = _begin(service, _Handle())
    assert duplicate.execute is False
    assert duplicate.reason == "completed_receipt"


def test_expired_claim_can_replay_only_when_caller_marks_effect_safe(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "side-effects-replay-safe.db")
    monkeypatch.setattr(module, "db", database)
    service = module.SideEffectIdempotencyService()
    first = _begin(service, _Handle(), lease_seconds=1, replay_safe=True)
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_side_effect_receipts SET lease_expires_at = ? WHERE idempotency_key = ?",
            ("2000-01-01T00:00:00.000Z", first.idempotency_key),
        )
        conn.commit()

    reclaimed_handle = _Handle()
    reclaimed = _begin(
        service,
        reclaimed_handle,
        lease_seconds=1,
        replay_safe=True,
    )

    assert reclaimed.execute is True
    assert reclaimed.reason == "expired_claim_reclaimed"
    assert reclaimed.attempt_count == 2
    assert service.complete(
        run_handle=reclaimed_handle,
        receipt=reclaimed,
        node="test",
        result={"ok": True},
    ) is True


def test_legacy_completed_event_is_migrated_without_replay(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "side-effects-legacy.db")
    monkeypatch.setattr(module, "db", database)
    service = module.SideEffectIdempotencyService()
    key, _ = service.build_idempotency_key(
        session_id=_Handle.session_id,
        run_id=_Handle.run_id,
        step_key="test.step",
        target_identity="target-1",
        payload={"value": "same"},
    )
    monkeypatch.setattr(
        database,
        "get_runtime_events",
        lambda _session_id: [
            {"topic": "side_effect.completed", "payload": {"idempotencyKey": key}}
        ],
    )

    receipt = _begin(service, _Handle())

    assert receipt.execute is False
    assert receipt.reason == "legacy_completed_receipt"
    assert database.get_side_effect_receipt(key)["state"] == "completed"
