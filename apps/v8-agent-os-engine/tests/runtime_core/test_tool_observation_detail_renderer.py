from __future__ import annotations

import json


def test_renderer_rejects_invalid_raw_ref() -> None:
    from core.tool_observation_detail import render_tool_observation_detail

    result = render_tool_observation_detail("raw://not-toolobs")

    assert "rawRef invalid" in result
    assert not result.lstrip().startswith("{")


def test_renderer_missing_raw_ref_is_readable(monkeypatch, tmp_path) -> None:
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager
    from core.tool_observation_detail import render_tool_observation_detail

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)

    result = render_tool_observation_detail("toolobs://missing-observation")

    assert "rawRef not found" in result
    assert "toolobs://missing-observation" in result
    assert not result.lstrip().startswith("{")


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


def test_renderer_formats_generic_json_without_raw_json(tmp_path, monkeypatch) -> None:
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager
    from core.tool_observation_detail import render_tool_observation_detail

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)
    temp_db.add_tool_observation_record(
        {
            "id": "obs-renderer-json",
            "raw_ref": "toolobs://obs-renderer-json",
            "tool_name": "experimental_json_tool",
            "tool_call_id": "call-renderer-json",
            "runtime_kind": "native",
            "surface": "tool_node",
            "raw_chars": 180,
            "visible_chars": 80,
            "raw_sha256": "sha",
            "raw_body": json.dumps(
                {
                    "ok": False,
                    "status": "not_found",
                    "summary": "The requested calibration resource is unavailable.",
                    "error": "resource_missing",
                    "recommendedNextAction": "Choose another resource.",
                    "details": {"resourceType": "fixture", "attempts": 1},
                }
            ),
            "budget": {"agentVisibleBudget": 1000},
            "metadata": {},
        }
    )

    result = render_tool_observation_detail("toolobs://obs-renderer-json", max_chars=2000)

    assert result.startswith("Tool observation detail")
    assert "status: failed" in result
    assert "Summary: The requested calibration resource is unavailable." in result
    assert "Error: resource_missing" in result
    assert "Next: Choose another resource." in result
    assert "- details: resourceType=fixture; attempts=1" in result
    assert not result.lstrip().startswith("{")
    assert '"recommendedNextAction"' not in result


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


def test_renderer_web_detail_preserves_useful_content_shape(tmp_path, monkeypatch) -> None:
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager
    from core.tool_observation_detail import render_tool_observation_detail

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)
    payload = {
        "ok": True,
        "mode": "read",
        "title": "Reference page",
        "finalUrl": "https://example.com/reference",
        "text": (
            "Steps:\n"
            "1. Create an episode.\n"
            "2. Wait for typed handoff.\n\n"
            "| Field | Meaning |\n"
            "| episodeId | durable run unit |"
        ),
        "debug": {"transport": "not for agent"},
    }
    temp_db.add_tool_observation_record(
        {
            "id": "obs-renderer-web-content",
            "raw_ref": "toolobs://obs-renderer-web-content",
            "tool_name": "web_broker",
            "tool_call_id": "call-renderer",
            "runtime_kind": "web",
            "surface": "tool_node",
            "raw_chars": 320,
            "visible_chars": 120,
            "raw_sha256": "sha",
            "raw_body": json.dumps(payload, ensure_ascii=False),
            "budget": {"agentVisibleBudget": 1000},
            "metadata": {},
        }
    )

    result = render_tool_observation_detail("toolobs://obs-renderer-web-content", max_chars=4000)

    assert "Web observation detail" in result
    assert "Content:" in result
    assert "1. Create an episode." in result
    assert "| Field | Meaning |" in result
    assert "https://example.com/reference" in result
    assert '"debug"' not in result


def test_renderer_delegation_detail_keeps_result_and_hides_runtime_control_noise(tmp_path, monkeypatch) -> None:
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager
    from core.tool_observation_detail import render_tool_observation_detail

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)
    temp_db.add_tool_observation_record(
        {
            "id": "obs-delegation-result",
            "raw_ref": "toolobs://obs-delegation-result",
            "tool_name": "delegation_broker",
            "tool_call_id": "call-delegation",
            "runtime_kind": "subagent_swarm",
            "surface": "tool_node",
            "raw_chars": 1800,
            "visible_chars": 280,
            "raw_sha256": "internal-sha",
            "raw_body": json.dumps(
                {
                    "ok": True,
                    "mode": "observe",
                    "summary": "Collected one local result.",
                    "registryVersion": "subagents:internal",
                    "registryHash": "internal-hash",
                    "items": [
                        {
                            "taskBriefId": "task-1",
                            "targetLabel": "Reviewer",
                            "status": "ok",
                            "toolPolicy": {"mode": "none", "allowedTools": [], "forbiddenTools": []},
                            "resultText": "OWNER_ISOLATION_OK",
                            "summary": "The review completed.",
                            "localSelfCheck": "Evidence was checked.",
                            "acceptanceHint": "Accept, retry, or ignore.",
                        }
                    ],
                    "recommendedNextAction": "accept_retry_or_ignore",
                },
                ensure_ascii=False,
            ),
            "budget": {"agentVisibleBudget": 1000},
            "metadata": {},
        }
    )

    result = render_tool_observation_detail("toolobs://obs-delegation-result", max_chars=4000)

    assert result.startswith("Delegation result (observe)")
    assert "Tool authority: none" in result
    assert "Exact result: OWNER_ISOLATION_OK" in result
    assert "The review completed." in result
    assert "Evidence was checked." in result
    assert "registryVersion" not in result
    assert "internal-hash" not in result
    assert "rawRef" not in result
    assert "raw=" not in result
