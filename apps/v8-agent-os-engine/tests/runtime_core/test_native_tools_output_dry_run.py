from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

from langchain_core.messages import ToolMessage
from langgraph.types import Command


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "export_native_tools_output_dry_run.py"
SPEC = importlib.util.spec_from_file_location("export_native_tools_output_dry_run", SCRIPT_PATH)
assert SPEC is not None
native_tools_dry_run = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = native_tools_dry_run
SPEC.loader.exec_module(native_tools_dry_run)


def test_native_tools_dry_run_export_covers_registered_tools() -> None:
    exported_names = native_tools_dry_run.native_tool_names_to_export()

    assert "ask_user" in native_tools_dry_run.EXCLUDED_TOOLS
    assert "ask_user" not in exported_names
    assert native_tools_dry_run.missing_invocation_names() == []
    assert "dry-run-missing" not in json.dumps(native_tools_dry_run.BASE_SAFE_INVOCATIONS)


def test_native_tools_dry_run_export_writes_per_tool_markdown(tmp_path) -> None:
    records = native_tools_dry_run.collect_records(invoke=False)
    index = native_tools_dry_run.export_records(records, tmp_path, allow_non_default_output=True)

    assert index["toolCount"] == len(native_tools_dry_run.native_tool_names_to_export())
    assert index["agentVisible"] is True
    assert (tmp_path / "_index.json").exists()
    assert (tmp_path / "run_system_command.md").exists()
    assert not (tmp_path / "ask_user.md").exists()

    loaded_index = json.loads((tmp_path / "_index.json").read_text(encoding="utf-8"))
    assert loaded_index["toolCount"] == index["toolCount"]
    assert {"rawChars", "visibleChars", "truncatedByToolNode", "dirtySignals", "scenarioName"} <= set(
        loaded_index["tools"][0]
    )
    by_name = {item["name"]: item for item in loaded_index["tools"]}
    assert by_name["runtime_broker"]["toolFamily"] == "runtime"


def test_command_embedded_tool_messages_are_truncated() -> None:
    from graph.tool_routing import MAX_TOOL_OUTPUT_LENGTH, _truncate_agent_visible_result

    original = "x" * (MAX_TOOL_OUTPUT_LENGTH + 100)
    command = Command(update={"messages": [ToolMessage(content=original, tool_call_id="call-1")]})

    truncated = _truncate_agent_visible_result(command)
    message = truncated.update["messages"][0]

    assert isinstance(message, ToolMessage)
    assert len(message.content) < len(original)
    assert "OUTPUT TRUNCATED BY DYNAMIC TOOL OUTPUT BUDGET" in message.content
    assert f"Original length: {len(original)} chars" in message.content
    assert message.response_metadata["v8_tool_output_budget"]["wasBudgetTruncated"] is True


def test_tool_observation_detail_reads_bounded_redacted_preview(tmp_path, monkeypatch) -> None:
    from core.native_tools import tool_observation_detail
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)
    temp_db.add_tool_observation_record(
        {
            "id": "obs-test",
            "raw_ref": "toolobs://obs-test",
            "tool_name": "run_system_command",
            "tool_call_id": "call-test",
            "runtime_kind": "native",
            "surface": "tool_node",
            "raw_chars": 64,
            "visible_chars": 16,
            "raw_sha256": "sha",
            "raw_body": "api_key=super-secret-token-value\nsafe line",
            "budget": {"agentVisibleBudget": 1000},
            "metadata": {},
        }
    )

    result = tool_observation_detail.invoke({"raw_ref": "toolobs://obs-test", "max_chars": 1000})

    assert "Tool observation detail" in result
    assert "tool: run_system_command" in result
    assert "[secrets redacted]" in result
    assert "super-secret-token-value" not in result
    assert "api_key=<redacted>" in result


def test_tool_observation_detail_renders_research_pack_without_raw_json(tmp_path, monkeypatch) -> None:
    from core.native_tools import tool_observation_detail
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)
    payload = {
        "kind": "research_evidence_bundle",
        "question": "How should Research Runtime summarize results?",
        "finalExperiencePack": {
            "architectAgentId": "web-research-architect",
            "answer": "Web Research Architect final result:\n- Use source-backed findings only.",
            "keyFindings": [
                {
                    "claim": "Use a final research result pack instead of raw search snippets.",
                    "sourceTitle": "Research docs",
                    "sourceUrl": "https://docs.example.com/research",
                }
            ],
            "sourceUrls": [
                {"title": "Research docs", "url": "https://docs.example.com/research", "host": "docs.example.com"}
            ],
            "confidence": "high",
        },
        "sourceMatrix": [{"title": "Raw matrix entry", "url": "https://docs.example.com/research"}],
    }
    temp_db.add_tool_observation_record(
        {
            "id": "obs-research",
            "raw_ref": "toolobs://obs-research",
            "tool_name": "research_broker",
            "tool_call_id": "call-research",
            "runtime_kind": "research",
            "surface": "tool_node",
            "raw_chars": 1024,
            "visible_chars": 256,
            "raw_sha256": "sha",
            "raw_body": json.dumps(payload, ensure_ascii=False),
            "budget": {"agentVisibleBudget": 1000},
            "metadata": {},
        }
    )

    result = tool_observation_detail.invoke({"raw_ref": "toolobs://obs-research", "max_chars": 4000})

    assert "Research result pack" in result
    assert "agent: Web Research Architect" in result
    assert "Use source-backed findings only" in result
    assert "https://docs.example.com/research" in result
    assert '"sourceMatrix"' not in result


def test_tool_observation_detail_renders_web_payload_without_raw_json(tmp_path, monkeypatch) -> None:
    from core.native_tools import tool_observation_detail
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)
    payload = {
        "ok": True,
        "mode": "extract",
        "extract": "ui_snapshot",
        "title": "Example form",
        "finalUrl": "https://example.com/form",
        "extractionQuality": "usable",
        "contentFormat": "ui_snapshot",
        "uiSnapshot": [
            {"tag": "button", "role": "button", "text": "Submit"},
            {"tag": "a", "text": "Help", "href": "https://example.com/help"},
        ],
        "debug": {"transport": "not for agent"},
    }
    temp_db.add_tool_observation_record(
        {
            "id": "obs-web",
            "raw_ref": "toolobs://obs-web",
            "tool_name": "web_extract",
            "tool_call_id": "call-web",
            "runtime_kind": "web",
            "surface": "tool_node",
            "raw_chars": 1024,
            "visible_chars": 256,
            "raw_sha256": "sha",
            "raw_body": json.dumps(payload, ensure_ascii=False),
            "budget": {"agentVisibleBudget": 1000},
            "metadata": {},
        }
    )

    result = tool_observation_detail.invoke({"raw_ref": "toolobs://obs-web", "max_chars": 4000})

    assert "Web observation detail" in result
    assert "Example form" in result
    assert "button | button | Submit" in result
    assert "https://example.com/help" in result
    assert '"debug"' not in result
