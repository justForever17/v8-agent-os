from __future__ import annotations

import asyncio
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
    assert (tmp_path / "_top10_giant_tool_outputs.md").exists()
    assert (tmp_path / "run_system_command.md").exists()
    assert not (tmp_path / "ask_user.md").exists()

    loaded_index = json.loads((tmp_path / "_index.json").read_text(encoding="utf-8"))
    assert loaded_index["toolCount"] == index["toolCount"]
    assert {"rawChars", "visibleChars", "truncatedByToolNode", "dirtySignals", "scenarioName"} <= set(
        loaded_index["tools"][0]
    )
    assert {"clientSurface", "clientSummary", "clientStatus", "clientRefIds"} <= set(loaded_index["tools"][0])
    assert loaded_index["topGiantReport"] == "_top10_giant_tool_outputs.md"
    by_name = {item["name"]: item for item in loaded_index["tools"]}
    assert by_name["runtime_broker"]["toolFamily"] == "runtime"
    assert by_name["runtime_broker"]["clientSurface"]["title"] == "runtime_broker"


def test_native_tools_dry_run_detail_scenario_is_representative(tmp_path, monkeypatch) -> None:
    import core.observability_db as observability_module
    from core.observability_db import ObservabilityDatabaseManager

    temp_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr(observability_module, "observability_db", temp_db)
    tool_ref = next(
        tool
        for tool in native_tools_dry_run._all_export_tools()
        if getattr(tool, "name", "") == "tool_observation_detail"
    )

    record = asyncio.run(
        native_tools_dry_run._collect_tool_observation_detail_scenario(
            tool_ref,
            "Read a bounded observation detail.",
        )
    )

    assert record.representative is True
    assert record.scenario_name == "seeded_observation_detail"
    assert record.output.startswith("Tool observation detail")
    assert "Calibration observation rendered as readable detail." in record.output
    assert "tool_observation_detail(raw_ref=" not in record.output
    assert record.diagnostics["jsonLikeVisible"] is False


def test_fail_on_dirty_includes_json_like_agent_visible_output() -> None:
    record = native_tools_dry_run._make_record(
        name="tool_observation_detail",
        status="invoked",
        args={},
        description="calibration",
        output='{"ok": false, "_v8ToolSurface": {}}',
        representative=True,
        representative_reason="test",
        scenario_name="test",
    )

    assert native_tools_dry_run._dirty_invoked_records([record]) == [record]


def test_client_surface_extracts_text_summary_without_runtime_json() -> None:
    record = native_tools_dry_run._make_record(
        name="web_broker",
        status="invoked",
        args={},
        description="calibration",
        output=(
            "Web broker (search)\n"
            "Summary: Found two useful sources.\n"
            "Sources:\n"
            "- Official docs — https://example.com/docs\n"
            "Next: read the official docs first.\n"
            "Raw ref: toolobs://abc"
        ),
        representative=True,
        representative_reason="test",
        scenario_name="test",
    )

    assert record.client_surface["summary"] == "Summary: Found two useful sources."
    assert record.client_surface["actionable"] == "Next: read the official docs first."
    assert record.client_surface["refIds"] == ["toolobs://abc"]


def test_client_surface_ref_ids_trim_wrapping_punctuation() -> None:
    record = native_tools_dry_run._make_record(
        name="research_broker",
        status="invoked",
        args={},
        description="calibration",
        output=(
            "Research plan\n"
            "Detail: tool_observation_detail(raw_ref='toolobs://toolobs_clean_ref')\n"
            "Raw: toolobs://toolobs_clean_ref\n"
        ),
        representative=True,
        representative_reason="test",
        scenario_name="test",
    )

    assert record.client_surface["refIds"] == ["toolobs://toolobs_clean_ref"]


def test_client_surface_skips_calibration_scenario_labels() -> None:
    record = native_tools_dry_run._make_record(
        name="run_system_command",
        status="invoked",
        args={},
        description="calibration",
        output=(
            "[scenario:session]\n"
            "$ powershell -NoProfile -Command \"Write-Output hello\"\n"
            "[completed with no output]\n"
        ),
        representative=True,
        representative_reason="test",
        scenario_name="test",
    )

    assert record.client_surface["summary"].startswith("$ powershell")
    assert record.client_surface["actionable"] is None


def test_client_surface_status_counts_do_not_mark_whole_tool_failed() -> None:
    record = native_tools_dry_run._make_record(
        name="creative_media_list_jobs",
        status="invoked",
        args={},
        description="calibration",
        output="Creative Media jobs (showing 3 of 17)\nStatus: failed=8, succeeded=9\nNext: inspect job details",
        representative=True,
        representative_reason="test",
        scenario_name="test",
    )

    assert record.client_surface["status"] == "completed"
    assert record.client_surface["summary"] == "Status: failed=8, succeeded=9"


def test_client_surface_unsafe_unobserved_is_blocked() -> None:
    record = native_tools_dry_run._make_record(
        name="write_native_file",
        status="unsafe_unobserved",
        args=None,
        description="calibration",
        output="unsafe_unobserved: would write to the filesystem.",
        representative=False,
        representative_reason="unsafe",
        scenario_name="unsafe_unobserved",
    )

    assert record.client_surface["status"] == "blocked"


def test_client_surface_redacts_local_paths_from_summary() -> None:
    record = native_tools_dry_run._make_record(
        name="read_native_file",
        status="invoked",
        args={},
        description="calibration",
        output=(
            "Summary: 路径不在当前 Active Workspace Root 内，已按硬工作区边界拒绝。 "
            "activeWorkspaceRoot=C:\\Users\\sunny\\.v8-agent-os\\workspace\n"
            "Next: 使用当前 Active Workspace Root 内的相对路径。"
        ),
        representative=True,
        representative_reason="test",
        scenario_name="safe_observe",
    )

    assert "C:\\Users" not in record.client_surface["summary"]
    assert "activeWorkspaceRoot=[hidden]" in record.client_surface["summary"]
    assert "C:\\Users" not in (record.client_surface["actionable"] or "")


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
