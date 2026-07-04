from __future__ import annotations

import json

from core.native_tools import memory_broker
from core.tools.research_broker import research_broker
from core.tools.research_ledger import promote_experience_pack, store_evidence_bundle


class _FakeMemoryRuntime:
    def unified_recall(self, *, query: str, limit: int = 5, scope: str | None = None):
        return [
            {
                "id": "mem-project-preference",
                "scope": scope or "global",
                "category": "project_preference",
                "confidence": 0.86,
                "text": f"Relevant memory for {query}: prefer compact source-backed evidence packs.",
            }
        ][:limit]


def test_memory_broker_catalog_lists_domains_without_raw_ledgers() -> None:
    payload = json.loads(memory_broker.func(mode="catalog", scope="E:/Projects/test7"))

    domains = {item["domain"] for item in payload["domains"]}
    assert payload["ok"] is True
    assert "memory_core" in domains
    assert "research_experience" in domains
    assert "workflow_memory" in domains
    assert "raw" not in json.dumps(payload, ensure_ascii=False).lower()


def test_memory_broker_route_returns_compact_evidence_pack(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.native_tools._get_memory_runtime", lambda: _FakeMemoryRuntime())
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    bundle = store_evidence_bundle(
        {
            "evidenceBundleId": "bundle-sanyueqi",
            "question": "之前调研过三月七吗",
            "confidence": "high",
            "authorityScore": 78,
            "sourceMatrix": [{"title": "官方角色资料", "url": "https://sr.mihoyo.com/role/march7th", "host": "sr.mihoyo.com"}],
            "researchAnswerPack": {
                "answer": "三月七是《崩坏：星穹铁道》的列车组成员，调研结论应以官方角色设定和剧情文本为主。",
                "sources": [{"title": "官方角色资料", "url": "https://sr.mihoyo.com/role/march7th", "host": "sr.mihoyo.com"}],
                "score": {"qualityStatus": "usable_answer", "confidence": "high", "authorityScore": 78},
                "claimTable": [
                    {
                        "claim": "三月七的表达风格偏活泼、直接，并常用拍照和记录作为角色行为线索。",
                        "supportingSources": ["https://sr.mihoyo.com/role/march7th"],
                        "confidence": "high",
                    }
                ],
            },
            "finalExperiencePack": {
                "researchResult": "三月七是《崩坏：星穹铁道》的列车组成员，调研结论应以官方角色设定和剧情文本为主。",
                "claimTable": [
                    {
                        "claim": "三月七的表达风格偏活泼、直接，并常用拍照和记录作为角色行为线索。",
                        "supportingSources": ["https://sr.mihoyo.com/role/march7th"],
                        "confidence": "high",
                    }
                ],
            },
        },
        ttl_seconds=3600,
        scope="global",
    )
    assert promote_experience_pack(bundle["evidenceBundleId"], title="三月七角色调研")

    payload = json.loads(memory_broker.func(mode="route", query="之前调研过三月七吗", scope="global", limit=3))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert "research_experience" in payload["selectedDomains"]
    assert "memory_core" in payload["selectedDomains"]
    assert "evidencePacks" in payload
    research_pack = next(pack for pack in payload["evidencePacks"] if pack["sourceDomain"] == "research_experience")
    selected = research_pack["selectedEvidence"][0]
    assert selected["answer"].startswith("三月七是《崩坏：星穹铁道》")
    assert selected["sources"][0]["url"] == "https://sr.mihoyo.com/role/march7th"
    assert selected["score"]["confidence"] == "high"
    assert "三月七" in serialized
    assert "sourceMatrix" not in serialized
    assert "rawLedgers" in serialized


def test_low_quality_research_pack_requires_refresh(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))
    store_evidence_bundle(
        {
            "evidenceBundleId": "bundle-noisy-youtube",
            "question": "Research the external facts needed before implementation.",
            "confidence": "high",
            "authorityScore": 80,
            "summary": "Collected 4 ranked source(s).",
            "sourceMatrix": [
                {
                    "title": "How to Rethink and Reshape Your API Documentation - YouTube",
                    "url": "https://www.youtube.com/watch?v=noise",
                    "host": "www.youtube.com",
                    "snippet": "About Press Copyright Contact us Creators Advertise Developers Terms Privacy Policy & Safety How YouTube works.",
                }
            ],
        },
        ttl_seconds=3600,
        scope="global",
    )

    payload = json.loads(
        research_broker.func(
            mode="search_experience",
            query="Research the external facts needed before implementation.",
            includeArchived=True,
        )
    )

    assert payload["ok"] is True
    assert payload["items"] == []
    assert payload["reuseDecision"]["reuseDecision"] == "ignore"
    assert payload["reuseDecision"]["reason"] == "no_matching_experience_pack"
