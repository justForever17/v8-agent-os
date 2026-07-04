from __future__ import annotations

import importlib


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
