from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from core.database import (
    DatabaseManager,
    RuntimeEpisodeHandoffConflict,
    RuntimeEpisodeIdempotencyConflict,
    _runtime_episode_payload_fingerprint,
)
from core.runtime_episodes import (
    RuntimeEpisodeDurabilityError,
    append_handoff_ref,
    build_handoff_ref,
    build_runtime_episode,
    heartbeat_runtime_episode,
    persist_handoff_ref,
    persist_runtime_episode,
    resolve_runtime_episode_current_handoff,
)
import core.runtime_episodes as runtime_episodes_module


def _episode(*, episode_id: str, key: str, reason: str = "same") -> dict:
    return build_runtime_episode(
        need={
            "episodeId": episode_id,
            "idempotencyKey": key,
            "kind": "research",
            "source": "test",
            "reason": reason,
            "inputs": {"question": "same"},
        },
        kind="research",
        state="queued",
    )


def _create_binding(database: DatabaseManager, *, session_id: str, run_id: str) -> None:
    database.create_or_update_session(session_id, "Episode durability", user_id="test-user")
    database.create_run_record(
        run_id=run_id,
        session_id=session_id,
        run_type="runtime_episode_test",
        status="running",
    )


@pytest.mark.parametrize(
    ("handoff_status", "episode_state"),
    [("degraded", "degraded"), ("cancelled", "cancelled")],
)
def test_route_context_preserves_non_success_terminal_handoff_state(
    handoff_status,
    episode_state,
):
    projected = append_handoff_ref(
        {
            "capabilityEpisodes": [
                {"episodeId": "episode-route-state", "kind": "research", "state": "active"}
            ]
        },
        {
            "handoffRefId": f"handoff-{handoff_status}",
            "producerEpisodeId": "episode-route-state",
            "status": handoff_status,
        },
    )

    episode = projected["capabilityEpisodes"][0]
    assert episode["state"] == episode_state
    assert episode["resultRef"] == f"handoff-{handoff_status}"


def test_episode_idempotency_ledger_returns_original_and_rejects_payload_reuse(tmp_path):
    database = DatabaseManager(tmp_path / "episode-idempotency.db")
    first = database.upsert_runtime_episode_record(_episode(episode_id="episode-a", key="request-1"))
    retry = database.upsert_runtime_episode_record(_episode(episode_id="episode-b", key="request-1"))

    assert first["episodeId"] == "episode-a"
    assert retry["episodeId"] == "episode-a"
    with pytest.raises(RuntimeEpisodeIdempotencyConflict):
        database.upsert_runtime_episode_record(
            _episode(episode_id="episode-c", key="request-1", reason="different")
        )

    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT episode_id FROM runtime_episode_idempotency WHERE idempotency_key = ?",
            ("request-1",),
        ).fetchone()
    assert row["episode_id"] == "episode-a"


def test_episode_idempotency_key_is_session_scoped(tmp_path):
    database = DatabaseManager(tmp_path / "episode-idempotency-scope.db")
    _create_binding(database, session_id="session-a", run_id="run-a")
    _create_binding(database, session_id="session-a", run_id="run-retry")
    _create_binding(database, session_id="session-b", run_id="run-b")
    first = database.upsert_runtime_episode_record(
        _episode(episode_id="episode-session-a", key="same-request"),
        session_id="session-a",
        run_id="run-a",
    )
    second = database.upsert_runtime_episode_record(
        _episode(episode_id="episode-session-b", key="same-request"),
        session_id="session-b",
        run_id="run-b",
    )
    retry_same_session = database.upsert_runtime_episode_record(
        _episode(episode_id="episode-retry", key="same-request"),
        session_id="session-a",
        run_id="run-retry",
    )

    assert first["episodeId"] == "episode-session-a"
    assert second["episodeId"] == "episode-session-b"
    assert retry_same_session["episodeId"] == "episode-session-a"


def test_legacy_unbound_idempotency_row_can_bind_same_episode(tmp_path):
    database = DatabaseManager(tmp_path / "episode-idempotency-backfill.db")
    episode = _episode(episode_id="episode-legacy", key="legacy-request")
    database.upsert_runtime_episode_record(episode)
    legacy_payload = {
        "sessionId": None,
        "parentEpisodeId": None,
        "kind": "research",
        "source": "test",
        "reason": "same",
        "inputs": {"question": "same"},
        "requiredRuntimeAccess": [],
        "handoffRefs": [],
        "targetKind": None,
        "targetId": None,
    }
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episode_idempotency SET payload_fingerprint = ? WHERE idempotency_key = ?",
            (_runtime_episode_payload_fingerprint(legacy_payload), "legacy-request"),
        )
        conn.commit()
    _create_binding(database, session_id="session-bound", run_id="run-bound")

    rebound = database.upsert_runtime_episode_record(
        episode,
        session_id="session-bound",
        run_id="run-bound",
    )

    assert rebound["episodeId"] == "episode-legacy"
    assert rebound["session_id"] == "session-bound"
    assert rebound["run_id"] == "run-bound"
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT session_id, run_id, payload_fingerprint FROM runtime_episode_idempotency WHERE idempotency_key = ?",
            ("legacy-request",),
        ).fetchone()
    assert row["session_id"] == "session-bound"
    assert row["run_id"] == "run-bound"
    assert row["payload_fingerprint"] != _runtime_episode_payload_fingerprint(legacy_payload)


def test_legacy_unbound_key_cannot_create_scoped_duplicate_episode(tmp_path):
    database = DatabaseManager(tmp_path / "episode-idempotency-legacy-duplicate.db")
    database.upsert_runtime_episode_record(
        _episode(episode_id="episode-original", key="legacy-duplicate")
    )
    _create_binding(database, session_id="session-bound", run_id="run-bound")

    with pytest.raises(RuntimeEpisodeIdempotencyConflict):
        database.upsert_runtime_episode_record(
            _episode(episode_id="episode-duplicate", key="legacy-duplicate"),
            session_id="session-bound",
            run_id="run-bound",
        )

    with database.get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_episode_idempotency"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_episodes"
        ).fetchone()[0] == 1


def test_episode_persistence_does_not_fabricate_missing_session_or_run(tmp_path):
    database = DatabaseManager(tmp_path / "episode-binding-fail-closed.db")

    with pytest.raises(sqlite3.IntegrityError):
        database.upsert_runtime_episode_record(
            _episode(episode_id="episode-unbound", key="request-unbound"),
            session_id="missing-session",
            run_id="missing-run",
        )

    with database.get_connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM sessions WHERE id = 'missing-session'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM run_records WHERE id = 'missing-run'"
        ).fetchone() is None


def test_corrupted_episode_contract_json_is_diagnosed_without_exposing_raw_content(tmp_path):
    database = DatabaseManager(tmp_path / "episode-corrupted-contract.db")
    episode = _episode(episode_id="episode-corrupted-contract", key="request-corrupted-contract")
    database.upsert_runtime_episode_record(episode)
    malformed_need = '{"secret":"must-not-leak"'

    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episodes SET need_json = ?, required_runtime_access_json = ? WHERE id = ?",
            (malformed_need, "{}", episode["episodeId"]),
        )
        conn.commit()

    restored = database.get_runtime_episode(episode["episodeId"])

    assert restored is not None
    assert restored["contractCorrupted"] is True
    assert restored["contractStatus"] == "corrupted_persisted_json"
    assert restored["executionSupported"] is False
    errors = {item["sourceField"]: item for item in restored["contractDecodeErrors"]}
    assert errors["need_json"]["reason"] == "invalid_json"
    assert errors["required_runtime_access_json"]["reason"] == "unexpected_json_type"
    assert len(errors["need_json"]["rawSha256"]) == 64
    assert errors["need_json"]["rawByteLength"] == len(malformed_need.encode("utf-8"))
    assert "must-not-leak" not in json.dumps(restored, ensure_ascii=False)


def test_blank_contract_json_is_not_confused_with_null_and_projection_is_rebuilt(tmp_path):
    database = DatabaseManager(tmp_path / "episode-blank-contract.db")
    episode = _episode(episode_id="episode-blank-contract", key="request-blank-contract")
    database.upsert_runtime_episode_record(episode)
    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=episode["episodeId"],
            kind="research",
            compact_summary="canonical evidence survives projection corruption",
            status="ready",
        ),
    )
    assert persisted is not None
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episodes "
            "SET need_json = ?, inputs_json = NULL, handoff_refs_json = ? WHERE id = ?",
            (" \t", "", episode["episodeId"]),
        )
        conn.commit()

    restored = database.get_runtime_episode(episode["episodeId"])

    assert restored is not None
    assert restored["contractCorrupted"] is True
    errors = {item["sourceField"]: item for item in restored["contractDecodeErrors"]}
    assert errors["need_json"]["reason"] == "invalid_json"
    assert "inputs_json" not in errors
    assert restored["inputs"] == {}
    assert restored["projectionRecovered"] is True
    assert restored["handoffRefs"] == [persisted]


def test_corrupted_handoff_projection_is_rebuilt_without_disabling_episode(tmp_path):
    database = DatabaseManager(tmp_path / "episode-corrupted-handoff-projection.db")
    episode = _episode(
        episode_id="episode-corrupted-handoff-projection",
        key="request-corrupted-handoff-projection",
    )
    database.upsert_runtime_episode_record(episode)
    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=episode["episodeId"],
            kind="research",
            compact_summary="canonical research evidence",
            status="ready",
        ),
    )
    assert persisted is not None
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episodes SET handoff_refs_json = ? WHERE id = ?",
            ('[{"secret":"projection-must-not-leak"', episode["episodeId"]),
        )
        conn.commit()

    restored = database.get_runtime_episode(episode["episodeId"])

    assert restored is not None
    assert restored.get("contractCorrupted") is not True
    assert restored["executionSupported"] is True
    assert restored["projectionRecovered"] is True
    assert restored["projectionStatus"] == "recovered_from_canonical_handoffs"
    assert restored["handoffRefs"] == [persisted]
    assert restored["metadata"]["projectionIntegrity"]["recoverySource"] == "runtime_episode_handoffs"
    assert "projection-must-not-leak" not in json.dumps(restored, ensure_ascii=False)


def test_concurrent_episode_ingress_claims_one_episode(tmp_path):
    database = DatabaseManager(tmp_path / "episode-idempotency-concurrent.db")
    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[Exception] = []

    def worker(episode_id: str):
        try:
            barrier.wait()
            record = database.upsert_runtime_episode_record(_episode(episode_id=episode_id, key="request-2"))
            results.append(record["episodeId"])
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"episode-{index}",)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 2
    assert len(set(results)) == 1


def test_episode_persistence_and_heartbeat_fail_closed(monkeypatch):
    class BrokenDb:
        def upsert_runtime_episode_record(self, *args, **kwargs):
            raise OSError("disk unavailable")

        def heartbeat_runtime_episode(self, *args, **kwargs):
            return False

    monkeypatch.setattr(runtime_episodes_module, "db", BrokenDb())
    episode = _episode(episode_id="episode-broken", key="request-broken")

    with pytest.raises(RuntimeEpisodeDurabilityError):
        persist_runtime_episode(episode)
    with pytest.raises(RuntimeEpisodeDurabilityError):
        heartbeat_runtime_episode("episode-broken")


def test_handoff_persistence_fails_closed_on_rejection_and_database_error(monkeypatch):
    handoff = {
        "handoffRefId": "handoff-broken",
        "producerEpisodeId": "episode-broken",
        "kind": "research",
    }

    class RejectedDb:
        def add_runtime_episode_handoff(self, *args, **kwargs):
            return None

    monkeypatch.setattr(runtime_episodes_module, "db", RejectedDb())
    with pytest.raises(RuntimeEpisodeDurabilityError, match="persistence was rejected"):
        persist_handoff_ref(handoff)

    class BrokenDb:
        def add_runtime_episode_handoff(self, *args, **kwargs):
            raise OSError("disk unavailable")

    monkeypatch.setattr(runtime_episodes_module, "db", BrokenDb())
    with pytest.raises(RuntimeEpisodeDurabilityError, match="failed to persist"):
        persist_handoff_ref(handoff)


def test_handoff_builder_unifies_delivery_identity_and_protects_canonical_fields():
    handoff = build_handoff_ref(
        producer_episode_id="episode-builder",
        kind="research",
        compact_summary="evidence ready",
        extra={
            "artifactId": "artifact-explicit",
            "claimCount": 3,
            "detailTool": "research_broker(mode='get_evidence')",
        },
    )

    assert handoff["handoffId"] == handoff["handoffRefId"]
    assert handoff["artifactId"] == "artifact-explicit"
    assert handoff["claimCount"] == 3
    assert handoff["detailTool"] == "research_broker(mode='get_evidence')"
    with pytest.raises(ValueError, match="cannot override canonical fields: producerEpisodeId"):
        build_handoff_ref(
            producer_episode_id="episode-builder",
            kind="research",
            compact_summary="evidence ready",
            extra={"producerEpisodeId": "episode-other"},
        )


def test_handoff_replay_is_idempotent_and_repairs_embedded_projection(tmp_path):
    database = DatabaseManager(tmp_path / "episode-handoff-replay.db")
    episode = _episode(episode_id="episode-handoff-replay", key="request-handoff-replay")
    database.upsert_runtime_episode_record(episode)
    handoff = build_handoff_ref(
        producer_episode_id=episode["episodeId"],
        kind="research",
        compact_summary="canonical evidence",
        status="ready",
    )

    first = database.add_runtime_episode_handoff(episode_id=episode["episodeId"], handoff=handoff)
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episodes SET handoff_refs_json = '[]' WHERE id = ?",
            (episode["episodeId"],),
        )
        conn.commit()
    replay = database.add_runtime_episode_handoff(episode_id=episode["episodeId"], handoff=dict(handoff))

    rows = database.list_runtime_episode_handoffs(episode["episodeId"])
    stored_episode = database.get_runtime_episode(episode["episodeId"])
    assert first == replay
    assert len(rows) == 1
    assert rows[0]["id"] == handoff["handoffRefId"]
    assert rows[0]["payload"]["handoffId"] == rows[0]["payload"]["handoffRefId"]
    assert rows[0]["payload"]["payloadDigest"]
    assert rows[0]["payload"]["envelopeDigest"]
    assert rows[0]["payload"]["schemaVersion"] == "v8.runtime_handoff.v2"
    assert rows[0]["deliveryIntegrity"]["status"] == "verified"
    assert rows[0]["deliverySupported"] is True
    assert stored_episode["handoffRefs"] == [rows[0]["payload"]]


def test_corrupted_handoff_payload_returns_typed_non_delivery_diagnostic(tmp_path):
    database = DatabaseManager(tmp_path / "episode-handoff-payload-corrupt.db")
    episode = _episode(
        episode_id="episode-handoff-payload-corrupt",
        key="request-handoff-payload-corrupt",
    )
    database.upsert_runtime_episode_record(episode)
    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=episode["episodeId"],
            kind="research",
            compact_summary="evidence before storage corruption",
            status="ready",
        ),
    )
    assert persisted is not None
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episode_handoffs SET payload_json = ? WHERE id = ?",
            ('{"secret":"handoff-must-not-leak"', persisted["handoffId"]),
        )
        conn.commit()

    row = database.list_runtime_episode_handoffs(episode["episodeId"])[0]

    assert row["payloadCorrupted"] is True
    assert row["deliverySupported"] is False
    assert row["payloadStatus"] == "corrupted_persisted_json"
    assert row["payload"]["errorCode"] == "runtime_handoff_payload_corrupted"
    assert row["payload"]["status"] == "failed"
    assert row["payload"]["handoffRefId"] == persisted["handoffId"]
    assert len(row["payload"]["payloadIntegrity"]["rawSha256"]) == 64
    assert "payload_json" not in row
    assert "handoff-must-not-leak" not in json.dumps(row, ensure_ascii=False)


@pytest.mark.parametrize(
    ("raw_payload", "reason"),
    [("", "invalid_json"), (" \t", "invalid_json"), (None, "missing_json")],
)
def test_empty_handoff_payload_is_never_fabricated_from_duplicate_columns(
    tmp_path,
    raw_payload,
    reason,
):
    database = DatabaseManager(tmp_path / f"episode-empty-handoff-{reason}.db")
    episode = _episode(episode_id=f"episode-empty-handoff-{reason}", key=f"request-{reason}")
    database.upsert_runtime_episode_record(episode)
    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=episode["episodeId"],
            kind="research",
            compact_summary="must not be reconstructed from duplicate columns",
            status="ready",
        ),
    )
    assert persisted is not None
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episode_handoffs SET payload_json = ? WHERE id = ?",
            (raw_payload, persisted["handoffId"]),
        )
        conn.commit()

    row = database.list_runtime_episode_handoffs(episode["episodeId"])[0]

    assert row["payloadCorrupted"] is True
    assert row["deliverySupported"] is False
    assert row["payload"]["payloadIntegrity"]["reason"] == reason
    assert row["payload"]["errorCode"] == "runtime_handoff_payload_corrupted"
    assert "must not be reconstructed" not in json.dumps(row["payload"], ensure_ascii=False)


def test_handoff_payload_digest_mismatch_is_not_consumable(tmp_path):
    database = DatabaseManager(tmp_path / "episode-handoff-digest-corrupt.db")
    episode = _episode(
        episode_id="episode-handoff-digest-corrupt",
        key="request-handoff-digest-corrupt",
    )
    database.upsert_runtime_episode_record(episode)
    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=episode["episodeId"],
            kind="research",
            compact_summary="original evidence",
            status="ready",
        ),
    )
    assert persisted is not None
    tampered = {**persisted, "compactSummary": "tampered evidence"}
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episode_handoffs SET payload_json = ? WHERE id = ?",
            (json.dumps(tampered, ensure_ascii=False), persisted["handoffId"]),
        )
        conn.commit()

    row = database.list_runtime_episode_handoffs(episode["episodeId"])[0]

    assert row["payloadCorrupted"] is True
    assert row["payload"]["payloadIntegrity"]["reason"] == "payload_digest_mismatch"
    assert row["payload"]["payloadIntegrity"]["storedDigest"] == persisted["payloadDigest"]
    assert row["payload"]["payloadIntegrity"]["computedDigest"] != persisted["payloadDigest"]


@pytest.mark.parametrize(
    ("removed_digests", "reason"),
    [
        (("envelopeDigest",), "missing_envelope_digest"),
        (("payloadDigest",), "missing_payload_digest"),
        (("payloadDigest", "envelopeDigest"), "missing_payload_digest"),
    ],
)
def test_current_handoff_with_deleted_digest_is_not_consumable(
    tmp_path,
    removed_digests,
    reason,
):
    database = DatabaseManager(tmp_path / f"episode-handoff-missing-{reason}.db")
    episode = _episode(episode_id=f"episode-handoff-missing-{reason}", key=f"request-{reason}")
    database.upsert_runtime_episode_record(episode)
    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=episode["episodeId"],
            kind="research",
            compact_summary="digest protected evidence",
            status="ready",
        ),
    )
    assert persisted is not None
    tampered = dict(persisted)
    for key in removed_digests:
        tampered.pop(key, None)
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episode_handoffs SET payload_json = ? WHERE id = ?",
            (json.dumps(tampered, ensure_ascii=False), persisted["handoffId"]),
        )
        conn.commit()

    row = database.list_runtime_episode_handoffs(episode["episodeId"])[0]
    selected, diagnostic = resolve_runtime_episode_current_handoff(
        {**episode, "resultRef": persisted["handoffId"]},
        [row],
    )

    assert row["deliverySupported"] is False
    assert row["deliveryIntegrity"]["status"] == "corrupted"
    assert row["deliveryIntegrity"]["reason"] == reason
    assert selected is None
    assert diagnostic["resolution"] == "current_handoff_payload_corrupted"


@pytest.mark.parametrize(
    ("mutated_field", "mutated_value", "reason"),
    [
        ("handoffId", "handoff-payload-rebound", "handoff_id_row_mismatch"),
        ("handoffRefId", "handoff-ref-rebound", "handoff_ref_id_row_mismatch"),
        ("producerEpisodeId", "episode-lineage-rebound", "producer_episode_row_mismatch"),
        ("identityAliases", ["handoff-alias-rebound"], "envelope_digest_mismatch"),
    ],
)
def test_handoff_envelope_rejects_payload_identity_or_alias_rebinding(
    tmp_path,
    mutated_field,
    mutated_value,
    reason,
):
    database = DatabaseManager(tmp_path / f"episode-handoff-{mutated_field}.db")
    episode = _episode(episode_id=f"episode-handoff-{mutated_field}", key=f"request-{mutated_field}")
    database.upsert_runtime_episode_record(episode)
    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff={
            **build_handoff_ref(
                producer_episode_id=episode["episodeId"],
                kind="research",
                compact_summary="identity protected evidence",
                status="ready",
            ),
            "identityAliases": ["handoff-legacy-b", "handoff-legacy-a"],
        },
    )
    assert persisted is not None
    assert persisted["identityAliases"] == ["handoff-legacy-a", "handoff-legacy-b"]
    tampered = {**persisted, mutated_field: mutated_value}
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episode_handoffs SET payload_json = ? WHERE id = ?",
            (json.dumps(tampered, ensure_ascii=False), persisted["handoffId"]),
        )
        conn.commit()

    row = database.list_runtime_episode_handoffs(episode["episodeId"])[0]
    selected, diagnostic = resolve_runtime_episode_current_handoff(
        {**episode, "resultRef": persisted["handoffId"]},
        [row],
    )

    assert row["deliverySupported"] is False
    assert row["deliveryIntegrity"]["reason"] == reason
    assert selected is None
    assert diagnostic["errorCode"] == "runtime_handoff_payload_corrupted"
    with pytest.raises(RuntimeEpisodeHandoffConflict, match="durable envelope is corrupted"):
        database.add_runtime_episode_handoff(
            episode_id=episode["episodeId"],
            handoff=persisted,
        )


def test_handoff_envelope_rejects_row_to_payload_identity_mismatch(tmp_path):
    database = DatabaseManager(tmp_path / "episode-handoff-row-identity.db")
    episode = _episode(episode_id="episode-handoff-row-identity", key="request-row-identity")
    database.upsert_runtime_episode_record(episode)
    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=episode["episodeId"],
            kind="research",
            compact_summary="row-bound evidence",
            status="ready",
        ),
    )
    assert persisted is not None
    rebound_row_id = "handoff-row-rebound"
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episode_handoffs SET id = ? WHERE id = ?",
            (rebound_row_id, persisted["handoffId"]),
        )
        conn.commit()

    row = database.list_runtime_episode_handoffs(episode["episodeId"])[0]
    selected, diagnostic = resolve_runtime_episode_current_handoff(
        {**episode, "resultRef": persisted["handoffId"]},
        [row],
    )

    assert row["id"] == rebound_row_id
    assert row["deliverySupported"] is False
    assert row["deliveryIntegrity"]["reason"] == "handoff_id_row_mismatch"
    assert selected is None
    assert diagnostic["resolution"] == "result_ref_not_found"
    rebound_selected, rebound_diagnostic = resolve_runtime_episode_current_handoff(
        {**episode, "resultRef": rebound_row_id},
        [row],
    )
    assert rebound_selected is None
    assert rebound_diagnostic["resolution"] == "current_handoff_payload_corrupted"


def test_legacy_handoff_is_retained_unverified_until_authoritative_replay(tmp_path):
    database = DatabaseManager(tmp_path / "episode-handoff-legacy-envelope.db")
    episode = _episode(episode_id="episode-handoff-legacy-envelope", key="request-legacy-envelope")
    database.upsert_runtime_episode_record(episode)
    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff=build_handoff_ref(
            producer_episode_id=episode["episodeId"],
            kind="research",
            compact_summary="legacy evidence remains recoverable",
            status="ready",
        ),
    )
    assert persisted is not None
    legacy_payload = {
        **persisted,
        "schemaVersion": "v8.runtime_handoff.v1",
    }
    legacy_payload.pop("payloadDigest", None)
    legacy_payload.pop("envelopeDigest", None)
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episode_handoffs SET payload_json = ? WHERE id = ?",
            (json.dumps(legacy_payload, ensure_ascii=False), persisted["handoffId"]),
        )
        conn.commit()

    legacy_row = database.list_runtime_episode_handoffs(episode["episodeId"])[0]
    selected, diagnostic = resolve_runtime_episode_current_handoff(
        {**episode, "resultRef": persisted["handoffId"]},
        [legacy_row],
    )

    assert legacy_row["payload"]["compactSummary"] == "legacy evidence remains recoverable"
    assert legacy_row.get("payloadCorrupted") is not True
    assert legacy_row["deliverySupported"] is False
    assert legacy_row["deliveryIntegrity"]["status"] == "legacy_unverified"
    assert legacy_row["deliveryIntegrity"]["recoverable"] is True
    assert selected is None
    assert diagnostic["resolution"] == "current_handoff_integrity_unverified"
    assert diagnostic["deliveryIntegrity"]["recoveryAction"]

    unverified_replay = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff=legacy_row["payload"],
    )
    retained_row = database.list_runtime_episode_handoffs(episode["episodeId"])[0]
    assert unverified_replay is not None
    assert unverified_replay["deliverySupported"] is False
    assert unverified_replay["deliveryState"] == "legacy_unverified"
    assert unverified_replay["recoveryAction"]
    assert retained_row["deliveryIntegrity"]["status"] == "legacy_unverified"

    committed = database.commit_runtime_episode_delivery(
        episode["episodeId"],
        handoff=legacy_row["payload"],
        state="completed",
        expected_state="queued",
    )
    assert committed is not None
    upgraded = committed["handoff"]
    upgraded_row = database.list_runtime_episode_handoffs(episode["episodeId"])[0]
    assert upgraded["schemaVersion"] == "v8.runtime_handoff.v2"
    assert upgraded["envelopeDigest"]
    assert upgraded_row["deliveryIntegrity"]["status"] == "verified"
    assert upgraded_row["deliverySupported"] is True


def test_handoff_identity_conflict_preserves_original_evidence(tmp_path):
    database = DatabaseManager(tmp_path / "episode-handoff-conflict.db")
    episode = _episode(episode_id="episode-handoff-conflict", key="request-handoff-conflict")
    database.upsert_runtime_episode_record(episode)
    handoff = build_handoff_ref(
        producer_episode_id=episode["episodeId"],
        kind="research",
        compact_summary="original evidence",
        status="ready",
    )
    database.add_runtime_episode_handoff(episode_id=episode["episodeId"], handoff=handoff)

    with pytest.raises(RuntimeEpisodeHandoffConflict) as captured:
        database.add_runtime_episode_handoff(
            episode_id=episode["episodeId"],
            handoff={**handoff, "compactSummary": "mutated evidence"},
        )

    rows = database.list_runtime_episode_handoffs(episode["episodeId"])
    assert captured.value.code == "runtime_episode_handoff_conflict"
    assert captured.value.existing_digest != captured.value.incoming_digest
    assert len(rows) == 1
    assert rows[0]["payload"]["compactSummary"] == "original evidence"
    assert database.get_runtime_episode(episode["episodeId"])["handoffRefs"][0]["compactSummary"] == "original evidence"


def test_handoff_identity_alias_cannot_be_reused_by_another_episode(tmp_path):
    database = DatabaseManager(tmp_path / "episode-handoff-alias-scope.db")
    first_episode = _episode(episode_id="episode-alias-owner", key="request-alias-owner")
    second_episode = _episode(episode_id="episode-alias-contender", key="request-alias-contender")
    database.upsert_runtime_episode_record(first_episode)
    database.upsert_runtime_episode_record(second_episode)
    database.add_runtime_episode_handoff(
        episode_id=first_episode["episodeId"],
        handoff={
            "handoffId": "handoff-canonical-owner",
            "identityAliases": ["handoff-legacy-alias"],
            "producerEpisodeId": first_episode["episodeId"],
            "kind": "research_evidence_bundle",
            "status": "ready",
            "compactSummary": "first episode owns both identities",
        },
    )

    with pytest.raises(RuntimeEpisodeHandoffConflict) as captured:
        database.add_runtime_episode_handoff(
            episode_id=second_episode["episodeId"],
            handoff={
                "handoffRefId": "handoff-legacy-alias",
                "producerEpisodeId": second_episode["episodeId"],
                "kind": "research_evidence_bundle",
                "status": "ready",
                "compactSummary": "second episode must not acquire the alias",
            },
        )

    assert captured.value.existing_episode_id == first_episode["episodeId"]
    assert len(database.list_runtime_episode_handoffs(first_episode["episodeId"])) == 1
    assert database.list_runtime_episode_handoffs(second_episode["episodeId"]) == []


def test_episode_upsert_cannot_erase_canonical_handoff_projection(tmp_path):
    database = DatabaseManager(tmp_path / "episode-handoff-upsert.db")
    episode = _episode(episode_id="episode-handoff-upsert", key="request-handoff-upsert")
    database.upsert_runtime_episode_record(episode)
    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff={
            "handoffRefId": "handoff-legacy-ref-only",
            "producerEpisodeId": episode["episodeId"],
            "kind": "research_evidence_bundle",
            "status": "ready",
            "compactSummary": "legacy alias normalized",
        },
    )

    database.upsert_runtime_episode_record({**episode, "handoffRefs": []})

    stored = database.get_runtime_episode(episode["episodeId"])
    assert persisted["handoffId"] == "handoff-legacy-ref-only"
    assert persisted["handoffRefId"] == "handoff-legacy-ref-only"
    assert stored["handoffRefs"] == [persisted]


def test_expected_state_handoff_cannot_write_through_another_worker_lease(tmp_path):
    database = DatabaseManager(tmp_path / "episode-handoff-cas.db")
    episode = _episode(episode_id="episode-handoff-cas", key="request-handoff-cas")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    started = database.complete_runtime_episode(
        episode["episodeId"],
        state="active",
        expected_state="queued",
    )
    assert started is not None
    with database.get_connection() as conn:
        conn.execute(
            """
            UPDATE runtime_episodes
            SET worker_id = 'worker-owner', lease_generation = 1,
                lease_expires_at = '2999-01-01T00:00:00.000Z'
            WHERE id = ?
            """,
            (episode["episodeId"],),
        )
        conn.commit()

    assert database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff={
            "handoffRefId": "handoff-stale-unfenced",
            "producerEpisodeId": episode["episodeId"],
            "kind": "research",
        },
    ) is None

    persisted = database.add_runtime_episode_handoff(
        episode_id=episode["episodeId"],
        handoff={
            "handoffRefId": "handoff-stale",
            "producerEpisodeId": episode["episodeId"],
            "kind": "research",
        },
        expected_state="active",
    )

    assert persisted is None
    assert database.list_runtime_episode_handoffs(episode["episodeId"]) == []


def _claimed_delivery_episode(database: DatabaseManager, *, episode_id: str, worker_id: str) -> tuple[dict, dict]:
    episode = _episode(episode_id=episode_id, key=f"request-{episode_id}")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    claimed = database.claim_runtime_episode(
        worker_id=worker_id,
        lease_seconds=30,
        kinds=["research"],
    )
    assert claimed is not None
    return episode, claimed


def _ready_delivery_handoff(episode_id: str, *, summary: str = "canonical delivery") -> dict:
    return build_handoff_ref(
        producer_episode_id=episode_id,
        kind="research_evidence_bundle",
        compact_summary=summary,
        status="ready",
    )


def test_atomic_episode_delivery_commits_all_durable_projections(tmp_path):
    database = DatabaseManager(tmp_path / "episode-delivery-atomic.db")
    episode, claimed = _claimed_delivery_episode(
        database,
        episode_id="episode-delivery-atomic",
        worker_id="worker-delivery",
    )
    handoff = _ready_delivery_handoff(episode["episodeId"])

    delivery = database.commit_runtime_episode_delivery(
        episode["episodeId"],
        handoff=handoff,
        state="completed",
        metadata={"proof": "verified"},
        worker_id="worker-delivery",
        lease_generation=int(claimed["leaseGeneration"]),
    )

    assert delivery is not None
    stored = delivery["episode"]
    persisted_handoff = delivery["handoff"]
    assert stored["state"] == "completed"
    assert stored["resultRef"] == persisted_handoff["handoffId"]
    assert stored["handoffRefs"] == [persisted_handoff]
    assert stored["metadata"]["handoff"] == persisted_handoff
    assert stored["metadata"]["proof"] == "verified"
    rows = database.list_runtime_episode_handoffs(episode["episodeId"])
    assert len(rows) == 1
    assert rows[0]["payload"] == persisted_handoff
    with database.get_connection() as conn:
        queue = conn.execute(
            "SELECT state, locked_by, lease_expires_at FROM runtime_episode_queue WHERE episode_id = ?",
            (episode["episodeId"],),
        ).fetchone()
        leases = conn.execute(
            "SELECT state, released_at FROM runtime_episode_leases WHERE episode_id = ?",
            (episode["episodeId"],),
        ).fetchall()
    assert dict(queue) == {"state": "completed", "locked_by": None, "lease_expires_at": None}
    assert len(leases) == 1
    assert leases[0]["state"] == "completed"
    assert leases[0]["released_at"]


def test_atomic_episode_delivery_failpoint_rolls_back_every_projection(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / "episode-delivery-rollback.db")
    episode, claimed = _claimed_delivery_episode(
        database,
        episode_id="episode-delivery-rollback",
        worker_id="worker-rollback",
    )

    def fail_after_transition(stage: str) -> None:
        if stage == "after_episode_transition":
            raise RuntimeError("simulated process loss before commit")

    monkeypatch.setattr(database, "_runtime_episode_delivery_failpoint", fail_after_transition)
    with pytest.raises(RuntimeError, match="simulated process loss"):
        database.commit_runtime_episode_delivery(
            episode["episodeId"],
            handoff=_ready_delivery_handoff(episode["episodeId"]),
            state="completed",
            worker_id="worker-rollback",
            lease_generation=int(claimed["leaseGeneration"]),
        )

    stored = database.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "active"
    assert stored["resultRef"] is None
    assert stored["handoffRefs"] == []
    assert database.list_runtime_episode_handoffs(episode["episodeId"]) == []
    with database.get_connection() as conn:
        queue = conn.execute(
            "SELECT state, locked_by FROM runtime_episode_queue WHERE episode_id = ?",
            (episode["episodeId"],),
        ).fetchone()
        active_lease_count = conn.execute(
            "SELECT COUNT(*) FROM runtime_episode_leases WHERE episode_id = ? AND state = 'active'",
            (episode["episodeId"],),
        ).fetchone()[0]
    assert dict(queue) == {"state": "leased", "locked_by": "worker-rollback"}
    assert active_lease_count == 1


def test_atomic_episode_delivery_stale_fence_and_expected_state_are_zero_write(tmp_path):
    database = DatabaseManager(tmp_path / "episode-delivery-stale.db")
    episode, claimed = _claimed_delivery_episode(
        database,
        episode_id="episode-delivery-stale",
        worker_id="worker-current",
    )
    handoff = _ready_delivery_handoff(episode["episodeId"])

    rejected = database.commit_runtime_episode_delivery(
        episode["episodeId"],
        handoff=handoff,
        state="completed",
        worker_id="worker-current",
        lease_generation=int(claimed["leaseGeneration"]) + 1,
    )

    assert rejected is None
    stored = database.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "active"
    assert stored["resultRef"] is None
    assert stored["handoffRefs"] == []
    assert database.list_runtime_episode_handoffs(episode["episodeId"]) == []

    unclaimed = _episode(episode_id="episode-delivery-expected-state", key="request-expected-state")
    database.upsert_runtime_episode_record(unclaimed, enqueue=True)
    assert database.commit_runtime_episode_delivery(
        unclaimed["episodeId"],
        handoff=_ready_delivery_handoff(unclaimed["episodeId"]),
        state="failed",
        expected_state="waiting_child",
    ) is None
    assert database.get_runtime_episode(unclaimed["episodeId"])["state"] == "queued"
    assert database.list_runtime_episode_handoffs(unclaimed["episodeId"]) == []


def test_concurrent_atomic_episode_delivery_cas_has_one_winner(tmp_path):
    db_path = tmp_path / "episode-delivery-concurrent.db"
    first_manager = DatabaseManager(db_path)
    second_manager = DatabaseManager(db_path)
    episode, claimed = _claimed_delivery_episode(
        first_manager,
        episode_id="episode-delivery-concurrent",
        worker_id="worker-concurrent",
    )
    barrier = threading.Barrier(2)
    results: list[dict | None] = []
    errors: list[BaseException] = []

    def commit(manager: DatabaseManager) -> None:
        try:
            barrier.wait()
            results.append(
                manager.commit_runtime_episode_delivery(
                    episode["episodeId"],
                    handoff=_ready_delivery_handoff(episode["episodeId"]),
                    state="completed",
                    worker_id="worker-concurrent",
                    lease_generation=int(claimed["leaseGeneration"]),
                )
            )
        except BaseException as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    threads = [
        threading.Thread(target=commit, args=(manager,))
        for manager in (first_manager, second_manager)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len([result for result in results if result is not None]) == 1
    assert len([result for result in results if result is None]) == 1
    stored = first_manager.get_runtime_episode(episode["episodeId"])
    handoffs = first_manager.list_runtime_episode_handoffs(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "completed"
    assert len(handoffs) == 1
    assert stored["resultRef"] == handoffs[0]["payload"]["handoffId"]


def test_delete_session_cleans_episode_and_side_effect_ledgers(tmp_path):
    database = DatabaseManager(tmp_path / "episode-delete-session.db")
    _create_binding(database, session_id="session-delete", run_id="run-delete")
    database.upsert_runtime_episode_record(
        _episode(episode_id="episode-delete", key="request-delete"),
        session_id="session-delete",
        run_id="run-delete",
        enqueue=True,
    )
    database.claim_side_effect_receipt(
        session_id="session-delete",
        run_id="run-delete",
        idempotency_key="side-effect-delete",
        effect_kind="test.effect",
        step_key="delete.step",
        target_identity="target",
        payload_fingerprint="payload",
        owner_id="owner",
    )

    database.delete_session("session-delete")

    assert database.get_session("session-delete") is None
    with database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_episodes").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_episode_idempotency"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_side_effect_receipts"
        ).fetchone()[0] == 0


def test_unfenced_cancel_cannot_overwrite_a_reclaimed_active_lease(tmp_path):
    database = DatabaseManager(tmp_path / "episode-cancel-fence.db")
    episode = _episode(episode_id="episode-cancel-fence", key="request-cancel-fence")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    first = database.claim_runtime_episode(worker_id="worker-a", lease_seconds=30, kinds=["research"])
    assert first is not None
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE runtime_episodes SET lease_expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00.000Z", episode["episodeId"]),
        )
        conn.execute(
            "UPDATE runtime_episode_queue SET lease_expires_at = ? WHERE episode_id = ?",
            ("2000-01-01T00:00:00.000Z", episode["episodeId"]),
        )
        conn.commit()
    second = database.claim_runtime_episode(worker_id="worker-b", lease_seconds=30, kinds=["research"])
    assert second is not None

    assert database.heartbeat_runtime_episode(
        episode["episodeId"],
        progress="stale unfenced heartbeat",
    ) is False
    assert database.resume_runtime_episode(
        episode["episodeId"],
        resume_token={"stale": True},
    ) is None
    assert database.cancel_runtime_episode(episode["episodeId"], reason="stale request") is None
    assert database.complete_runtime_episode(
        episode["episodeId"],
        state="failed",
        error_message="stale completion",
    ) is None
    current = database.get_runtime_episode(episode["episodeId"])
    assert current is not None
    assert current["state"] == "active"
    assert current["worker_id"] == "worker-b"
    assert current["lastProgress"] != "stale unfenced heartbeat"


def test_fenced_waiting_transition_releases_lease_for_immediate_resume(tmp_path):
    database = DatabaseManager(tmp_path / "episode-waiting-resume.db")
    episode = _episode(episode_id="episode-waiting-resume", key="request-waiting-resume")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    claimed = database.claim_runtime_episode(
        worker_id="worker-waiting",
        lease_seconds=30,
        kinds=["research"],
    )
    assert claimed is not None

    waiting = database.complete_runtime_episode(
        episode["episodeId"],
        state="waiting_child",
        worker_id="worker-waiting",
        lease_generation=int(claimed["leaseGeneration"]),
    )

    assert waiting is not None
    assert waiting["state"] == "waiting_child"
    assert not waiting["worker_id"]
    assert not waiting["lease_expires_at"]
    with database.get_connection() as conn:
        queue = conn.execute(
            "SELECT state, locked_by, lease_expires_at FROM runtime_episode_queue WHERE episode_id = ?",
            (episode["episodeId"],),
        ).fetchone()
    assert dict(queue) == {
        "state": "waiting_child",
        "locked_by": None,
        "lease_expires_at": None,
    }

    resumed = database.resume_runtime_episode(
        episode["episodeId"],
        resume_token={"source": "child_handoff"},
        expected_state="waiting_child",
    )
    assert resumed is not None
    assert resumed["state"] == "queued"


def test_retry_clears_episode_and_queue_ownership_projection(tmp_path):
    database = DatabaseManager(tmp_path / "episode-retry-release.db")
    episode = _episode(episode_id="episode-retry-release", key="request-retry-release")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    claimed = database.claim_runtime_episode(
        worker_id="worker-retry",
        lease_seconds=30,
        kinds=["research"],
    )
    assert claimed is not None

    retried = database.retry_runtime_episode(
        episode["episodeId"],
        worker_id="worker-retry",
        lease_generation=int(claimed["leaseGeneration"]),
    )

    assert retried is not None
    assert retried["state"] == "queued"
    assert retried["worker_id"] is None
    assert retried["lease_expires_at"] is None
    with database.get_connection() as conn:
        queue = conn.execute(
            "SELECT state, locked_by, lease_expires_at FROM runtime_episode_queue WHERE episode_id = ?",
            (episode["episodeId"],),
        ).fetchone()
    assert dict(queue) == {
        "state": "retry",
        "locked_by": None,
        "lease_expires_at": None,
    }


def test_unfenced_retry_cannot_reset_a_live_or_owned_waiting_episode(tmp_path):
    database = DatabaseManager(tmp_path / "episode-retry-fence.db")
    active = _episode(episode_id="episode-retry-active", key="request-retry-active")
    waiting = _episode(episode_id="episode-retry-waiting", key="request-retry-waiting")
    database.upsert_runtime_episode_record(active, enqueue=True)
    database.upsert_runtime_episode_record(waiting, enqueue=True)
    with database.get_connection() as conn:
        conn.execute(
            """
            UPDATE runtime_episodes
            SET state = 'active', worker_id = 'worker-active',
                lease_expires_at = '2999-01-01T00:00:00.000Z'
            WHERE id = ?
            """,
            (active["episodeId"],),
        )
        conn.execute(
            """
            UPDATE runtime_episodes
            SET state = 'waiting', worker_id = 'worker-waiting',
                lease_expires_at = '2999-01-01T00:00:00.000Z'
            WHERE id = ?
            """,
            (waiting["episodeId"],),
        )
        conn.commit()

    assert database.retry_runtime_episode(active["episodeId"], error_message="stale") is None
    assert database.retry_runtime_episode(waiting["episodeId"], error_message="stale") is None

    active_current = database.get_runtime_episode(active["episodeId"])
    waiting_current = database.get_runtime_episode(waiting["episodeId"])
    assert active_current["state"] == "active"
    assert active_current["worker_id"] == "worker-active"
    assert waiting_current["state"] == "waiting"
    assert waiting_current["worker_id"] == "worker-waiting"


def test_bulk_cancel_clears_active_episode_ownership_projection(tmp_path):
    database = DatabaseManager(tmp_path / "episode-bulk-cancel-release.db")
    _create_binding(database, session_id="session-cancel", run_id="run-cancel")
    episode = _episode(episode_id="episode-bulk-cancel", key="request-bulk-cancel")
    database.upsert_runtime_episode_record(
        episode,
        session_id="session-cancel",
        run_id="run-cancel",
        enqueue=True,
    )
    claimed = database.claim_runtime_episode(
        worker_id="worker-cancel",
        lease_seconds=30,
        kinds=["research"],
    )
    assert claimed is not None

    cancelled = database.cancel_active_runtime_episodes_for_run(
        "run-cancel",
        reason="test cancellation",
    )

    current = next(item for item in cancelled if item["episodeId"] == episode["episodeId"])
    assert current["state"] == "cancelled"
    assert current["worker_id"] is None
    assert current["lease_expires_at"] is None


def test_resume_clears_legacy_episode_and_queue_ownership_projection(tmp_path):
    database = DatabaseManager(tmp_path / "episode-resume-release.db")
    episode = _episode(episode_id="episode-resume-release", key="request-resume-release")
    database.upsert_runtime_episode_record(episode, enqueue=True)
    with database.get_connection() as conn:
        conn.execute(
            """
            UPDATE runtime_episodes
            SET state = 'waiting', worker_id = 'legacy-worker',
                lease_expires_at = '2000-01-01T00:00:00.000Z'
            WHERE id = ?
            """,
            (episode["episodeId"],),
        )
        conn.execute(
            """
            UPDATE runtime_episode_queue
            SET state = 'leased', locked_by = 'legacy-worker',
                lease_expires_at = '2000-01-01T00:00:00.000Z'
            WHERE episode_id = ?
            """,
            (episode["episodeId"],),
        )
        conn.commit()

    resumed = database.resume_runtime_episode(
        episode["episodeId"],
        resume_token={"source": "legacy_recovery"},
        expected_state="waiting",
    )

    assert resumed is not None
    assert resumed["state"] == "queued"
    assert resumed["worker_id"] is None
    assert resumed["lease_expires_at"] is None
    with database.get_connection() as conn:
        queue = conn.execute(
            "SELECT state, locked_by, lease_expires_at FROM runtime_episode_queue WHERE episode_id = ?",
            (episode["episodeId"],),
        ).fetchone()
    assert dict(queue) == {
        "state": "queued",
        "locked_by": None,
        "lease_expires_at": None,
    }
