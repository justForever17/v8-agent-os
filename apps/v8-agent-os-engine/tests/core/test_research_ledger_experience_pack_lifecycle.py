from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone


def test_experience_pack_archive_restore_and_hard_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    ledger = importlib.import_module("core.tools.research_ledger")

    bundle = ledger.store_evidence_bundle(
        {
            "question": "How should V8 reuse research?",
            "answer": "Use evidence bundles and promote durable experience packs.",
            "summary": "Use evidence bundles and promote durable experience packs.",
            "confidence": "high",
            "authorityScore": 84,
            "sourceMatrix": [{"title": "Design note", "host": "example.test", "url": "https://example.test/research"}],
            "researchAnswerPack": {
                "answer": "Use evidence bundles and promote durable experience packs.",
                "sources": [{"title": "Design note", "host": "example.test", "url": "https://example.test/research"}],
                "score": {"qualityStatus": "usable_answer", "confidence": "high", "authorityScore": 84},
            },
            "citations": [],
            "rawRefs": [],
        },
        ttl_seconds=3600,
        scope="project:test",
    )
    pack = ledger.promote_experience_pack(bundle["evidenceBundleId"], title="Research reuse", tags=["research"])
    assert pack

    assert ledger.search_experience_packs(query="research", scope="project:test")
    archived = ledger.archive_experience_pack(pack["experiencePackId"], initiated_by="test", reason="unit")
    assert archived and archived["status"] == "archived"
    assert ledger.search_experience_packs(query="research", scope="project:test") == []
    assert ledger.search_experience_packs_with_options(query="research", scope="project:test", include_archived=True)

    restored = ledger.restore_experience_pack(pack["experiencePackId"], initiated_by="test")
    assert restored and restored["status"] == "active"
    assert ledger.search_experience_packs(query="research", scope="project:test")

    assert ledger.delete_experience_pack(pack["experiencePackId"], confirm=True) is True
    assert ledger.get_experience_pack(pack["experiencePackId"], include_archived=True) is None
    assert ledger.get_evidence_bundle(bundle["evidenceBundleId"])


def test_spec_task_evidence_bundle_does_not_create_or_match_experience_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    ledger = importlib.import_module("core.tools.research_ledger")

    bundle = ledger.store_evidence_bundle(
        {
            "evidenceBundleId": "bundle-spec-task",
            "question": "TASK-001: Execute approved Spec spec_094d02189a1e4c20",
            "questionKind": "spec_task",
            "sourceKind": "spec_task",
            "answer": "The task has implementation instructions and should remain execution evidence only.",
            "summary": "Execution evidence for an approved Spec task.",
            "confidence": "high",
            "authorityScore": 90,
            "sourceMatrix": [{"title": "Spec task", "host": "local", "url": "spec://spec_094d02189a1e4c20/tasks#TASK-001"}],
            "researchAnswerPack": {
                "answer": "The task has implementation instructions and should remain execution evidence only.",
                "sources": [{"title": "Spec task", "host": "local", "url": "spec://spec_094d02189a1e4c20/tasks#TASK-001"}],
                "score": {"qualityStatus": "usable_answer", "confidence": "high", "authorityScore": 90},
            },
        },
        ttl_seconds=3600,
        scope="global",
    )

    stored = ledger.get_evidence_bundle(bundle["evidenceBundleId"])
    assert stored
    assert stored["questionKind"] == "spec_task"
    assert stored["sourceKind"] == "spec_task"
    assert ledger.promote_experience_pack(bundle["evidenceBundleId"], title="Should not promote") is None
    assert ledger.search_experience_packs(query="Execute approved Spec spec_094d02189a1e4c20", scope="global") == []
    assert ledger.search_experience_packs_with_options(
        query="Execute approved Spec spec_094d02189a1e4c20",
        scope="global",
        include_archived=True,
    ) == []


def test_experience_pack_reads_do_not_inflate_usage_and_stale_packs_are_archived(tmp_path, monkeypatch):
    ledger_path = tmp_path / "research_ledger.json"
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(ledger_path))
    ledger = importlib.import_module("core.tools.research_ledger")
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat().replace("+00:00", "Z")
    payload = {
        "version": 1,
        "evidenceBundles": [],
        "experiencePacks": [
            {
                "experiencePackId": "rxp_old",
                "status": "active",
                "title": "old research",
                "query": "old research",
                "summary": "old research result",
                "researchResult": "old research result",
                "qualityStatus": "reusable_candidate",
                "confidence": "high",
                "authorityScore": 90,
                "sourceUrls": ["https://example.test/old"],
                "topicFingerprint": "old-topic",
                "scope": "global",
                "freshnessWindow": "90d",
                "evidenceCheckedAt": old,
                "createdAt": old,
                "updatedAt": old,
                "usageCount": 3,
            }
        ],
    }
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")

    detail = ledger.get_experience_pack("rxp_old")
    assert detail and detail["usageCount"] == 3
    assert detail["freshnessState"] == "expired"
    assert ledger.get_experience_pack("rxp_old", record_usage=True)["usageCount"] == 4

    first = ledger.maintain_experience_packs()
    second = ledger.maintain_experience_packs()
    assert first["expiredArchivedCount"] == 1
    assert second["expiredArchivedCount"] == 0
    archived = ledger.get_experience_pack("rxp_old", include_archived=True)
    assert archived and archived["status"] == "archived"
    assert archived["archiveReason"] == "freshness_expired"


def test_experience_pack_maintenance_and_bulk_governance_are_recoverable(tmp_path, monkeypatch):
    ledger_path = tmp_path / "research_ledger.json"
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(ledger_path))
    ledger = importlib.import_module("core.tools.research_ledger")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "version": 1,
        "evidenceBundles": [],
        "experiencePacks": [
            {
                "experiencePackId": pack_id,
                "status": "active",
                "title": "same topic",
                "query": "same topic",
                "summary": "usable result",
                "researchResult": "usable result",
                "qualityStatus": "reusable_candidate",
                "confidence": "high",
                "authorityScore": 80,
                "sourceUrls": ["https://example.test/source"],
                "topicFingerprint": "same-topic",
                "scope": "project:test",
                "evidenceCheckedAt": created_at,
                "createdAt": created_at,
                "updatedAt": created_at,
            }
            for pack_id, created_at in (
                ("rxp_new", now),
                ("rxp_old", (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")),
            )
        ],
    }
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")

    maintenance = ledger.maintain_experience_packs()
    assert maintenance["duplicateArchivedCount"] == 1
    assert ledger.get_experience_pack("rxp_old", include_archived=True)["status"] == "archived"
    restored = ledger.bulk_update_experience_packs(["rxp_old"], action="restore", initiated_by="test")
    assert restored["updatedCount"] == 1
    archived = ledger.bulk_update_experience_packs(["rxp_old", "rxp_new"], action="archive", initiated_by="test")
    assert archived["updatedCount"] == 2


def test_experience_pack_maintenance_keeps_reusable_pack_over_newer_low_quality_duplicate(tmp_path, monkeypatch):
    ledger_path = tmp_path / "research_ledger.json"
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(ledger_path))
    ledger = importlib.import_module("core.tools.research_ledger")
    now = datetime.now(timezone.utc)
    older = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    newer = now.isoformat().replace("+00:00", "Z")
    payload = {
        "version": 1,
        "evidenceBundles": [],
        "experiencePacks": [
            {
                "experiencePackId": "rxp_reusable",
                "status": "active",
                "title": "same topic",
                "query": "same topic",
                "summary": "source-backed result",
                "researchResult": "source-backed result",
                "qualityStatus": "reusable_candidate",
                "confidence": "high",
                "authorityScore": 80,
                "sourceUrls": ["https://example.test/source"],
                "topicFingerprint": "same-topic",
                "scope": "project:test",
                "evidenceCheckedAt": older,
                "createdAt": older,
                "updatedAt": older,
            },
            {
                "experiencePackId": "rxp_low_quality",
                "status": "active",
                "title": "same topic",
                "query": "same topic",
                "summary": "unverified result",
                "qualityStatus": "low_quality_pack",
                "topicFingerprint": "same-topic",
                "scope": "project:test",
                "evidenceCheckedAt": newer,
                "createdAt": newer,
                "updatedAt": newer,
            },
        ],
    }
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")

    maintenance = ledger.maintain_experience_packs(now=now)

    assert maintenance["duplicateArchivedCount"] == 1
    assert ledger.get_experience_pack("rxp_reusable", include_archived=True)["status"] == "active"
    low_quality = ledger.get_experience_pack("rxp_low_quality", include_archived=True)
    assert low_quality["status"] == "archived"
    assert low_quality["archiveReason"] == "superseded_duplicate_topic"
