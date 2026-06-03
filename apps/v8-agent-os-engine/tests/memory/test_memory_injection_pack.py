from __future__ import annotations

from core.memory_store import MemoryStore


def test_memory_injection_pack_explains_selected_and_rejected_memory(monkeypatch) -> None:
    store = MemoryStore()

    def fake_preview_unified_recall(*, query, limit, scope, scopes):  # noqa: ANN001
        assert query == "继续修 test7 的工程问题"
        assert limit == 5
        assert scope == "workspace:test7"
        assert scopes == ["global", "workspace:test7"]
        return {
            "items": [
                {
                    "id": "m1",
                    "fact": "test7 工作区最近生成了三月七 perspective skill。",
                    "scope": "workspace:test7",
                    "source": "engineering_handoff",
                    "category": "project_fact",
                    "final_relevance_score": 0.91,
                    "accepted": True,
                },
                {
                    "id": "m2",
                    "fact": "另一个工作区的 Chrome 调试偏好。",
                    "scope": "workspace:other",
                    "source": "daily",
                    "category": "preference",
                    "final_relevance_score": 0.22,
                    "accepted": False,
                    "reject_reason": "below_threshold",
                },
            ],
            "accepted_items": [
                {
                    "id": "m1",
                    "fact": "test7 工作区最近生成了三月七 perspective skill。",
                    "scope": "workspace:test7",
                    "source": "engineering_handoff",
                    "category": "project_fact",
                    "final_relevance_score": 0.91,
                    "accepted": True,
                }
            ],
            "effective_acceptance_threshold": 0.5,
            "diagnostics": {"accepted_count": 1, "rejected_count": 1},
        }

    monkeypatch.setattr(store, "preview_unified_recall", fake_preview_unified_recall)

    pack = store.build_memory_injection_pack(
        user_query="继续修 test7 的工程问题",
        scope="workspace:test7",
        scope_chain=["global", "workspace:test7"],
        session_id="sess-1",
        run_id="run-1",
        latency_tier="balanced",
    )

    assert pack["version"] == "memory_injection_pack_v3"
    assert pack["mode"] == "balanced"
    assert pack["scope"]["chain"] == ["global", "workspace:test7"]
    assert pack["stats"]["selectedCount"] == 1
    assert pack["selectedMemory"][0]["id"] == "m1"
    assert "accepted_by_unified_recall" in pack["selectedMemory"][0]["whySelected"]
    assert pack["rejectedMemory"][0]["id"] == "m2"
    assert pack["rejectedMemory"][0]["doNotInjectReason"] == "below_threshold"
    assert pack["doNotInjectReasons"][0]["reason"] == "below_threshold"


def test_memory_injection_pack_latency_tiers_control_candidate_limit(monkeypatch) -> None:
    store = MemoryStore()
    seen_limits: list[int] = []

    def fake_preview_unified_recall(*, query, limit, scope, scopes):  # noqa: ANN001
        seen_limits.append(limit)
        return {
            "items": [],
            "accepted_items": [],
            "effective_acceptance_threshold": 0.5,
            "diagnostics": {"accepted_count": 0, "rejected_count": 0},
        }

    monkeypatch.setattr(store, "preview_unified_recall", fake_preview_unified_recall)

    for tier in ("fast", "balanced", "accurate", "unknown"):
        store.build_memory_injection_pack(user_query="q", latency_tier=tier)

    assert seen_limits == [3, 5, 8, 5]
