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
            "sourceMatrix": [{"title": "Design note", "host": "example.test", "url": "https://example.test/research"}],
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
