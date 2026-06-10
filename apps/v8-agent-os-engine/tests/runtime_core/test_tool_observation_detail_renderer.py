from __future__ import annotations

import json


def test_renderer_rejects_invalid_raw_ref() -> None:
    from core.tool_observation_detail import render_tool_observation_detail

    result = render_tool_observation_detail("raw://not-toolobs")

    assert "rawRef invalid" in result


def test_renderer_redacts_generic_preview(tmp_path, monkeypatch) -> None:
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager
    from core.tool_observation_detail import render_tool_observation_detail

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)
    temp_db.add_tool_observation_record(
        {
            "id": "obs-renderer-generic",
            "raw_ref": "toolobs://obs-renderer-generic",
            "tool_name": "run_system_command",
            "tool_call_id": "call-renderer",
            "runtime_kind": "native",
            "surface": "tool_node",
            "raw_chars": 80,
            "visible_chars": 20,
            "raw_sha256": "sha",
            "raw_body": "Authorization: Bearer sk-secret-token-value\nok",
            "budget": {"agentVisibleBudget": 1000},
            "metadata": {},
        }
    )

    result = render_tool_observation_detail("toolobs://obs-renderer-generic", max_chars=1000)

    assert "Tool observation detail" in result
    assert "sk-secret-token-value" not in result
    assert "Bearer=<redacted>" in result
    assert "[secrets redacted]" in result


def test_renderer_prefers_research_answer_pack(tmp_path, monkeypatch) -> None:
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager
    from core.tool_observation_detail import render_tool_observation_detail

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)
    payload = {
        "kind": "research_result_pack",
        "researchAnswerPack": {
            "answer": "Use the compact answer pack.",
            "score": {"label": "high"},
            "sources": [{"title": "Official docs", "url": "https://docs.example.test"}],
        },
        "sourceMatrix": [{"debug": "should not leak"}],
    }
    temp_db.add_tool_observation_record(
        {
            "id": "obs-renderer-research",
            "raw_ref": "toolobs://obs-renderer-research",
            "tool_name": "research_broker",
            "tool_call_id": "call-renderer",
            "runtime_kind": "research",
            "surface": "tool_node",
            "raw_chars": 120,
            "visible_chars": 80,
            "raw_sha256": "sha",
            "raw_body": json.dumps(payload, ensure_ascii=False),
            "budget": {"agentVisibleBudget": 1000},
            "metadata": {},
        }
    )

    result = render_tool_observation_detail("toolobs://obs-renderer-research", max_chars=4000)

    assert "Research result pack" in result
    assert "Use the compact answer pack." in result
    assert "https://docs.example.test" in result
    assert '"sourceMatrix"' not in result
