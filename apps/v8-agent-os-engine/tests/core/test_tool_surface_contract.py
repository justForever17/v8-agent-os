from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from core.tool_surface import apply_tool_surface_budget


def test_tool_surface_generic_json_preserves_refs_and_next_action_without_raw_json():
    payload = {
        "ok": True,
        "runId": "run_123",
        "recommendedNextAction": "inspect raw evidence",
        "items": [{"text": "x" * 2000} for _ in range(20)],
    }
    message = ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id="call_123",
        name="experimental_json_tool",
    )

    result = apply_tool_surface_budget(
        message,
        {
            "runId": "run_123",
            "agentVisibleBudget": 1400,
            "hardMaxChars": 15000,
            "targetChars": 12000,
            "budgetSource": "unit_test",
        },
    )

    rendered = str(result.content)
    assert rendered.startswith("experimental json tool result")
    assert "_v8ToolSurface" not in rendered
    assert not rendered.lstrip().startswith("{")
    assert "Items:" in rendered
    assert "inspect raw evidence" in rendered
    assert "tool_observation_detail(raw_ref='toolobs://" in rendered
    assert result.response_metadata["v8_tool_output_budget"]["semanticTruncationStrategy"] == "decision_summary_surface"


def test_tool_observation_detail_json_is_readable_without_recursive_raw_ref():
    message = ToolMessage(
        content=json.dumps(
            {
                "ok": False,
                "error": "raw_ref_not_found",
                "rawRef": "toolobs://missing",
            }
        ),
        tool_call_id="call_detail",
        name="tool_observation_detail",
    )

    result = apply_tool_surface_budget(message, {"agentVisibleBudget": 1000})

    rendered = str(result.content)
    assert rendered.startswith("tool observation detail result")
    assert "Status: failed" in rendered
    assert "raw_ref_not_found" in rendered
    assert not rendered.lstrip().startswith("{")
    assert "tool_observation_detail(raw_ref=" not in rendered
    assert "rawRef" not in result.response_metadata["v8_tool_output_budget"]


def test_tool_observation_detail_long_text_stays_text_when_truncated():
    message = ToolMessage(
        content="Tool observation detail\nrawRef: toolobs://source\n\n" + ("readable line\n" * 200),
        tool_call_id="call_detail_long",
        name="tool_observation_detail",
    )

    result = apply_tool_surface_budget(message, {"agentVisibleBudget": 500})

    rendered = str(result.content)
    metadata = result.response_metadata["v8_tool_output_budget"]
    assert not rendered.lstrip().startswith("{")
    assert "tool observation detail truncated" in rendered
    assert "tool_observation_detail(raw_ref=" not in rendered
    assert "rawRef" not in metadata
    assert metadata["semanticTruncationStrategy"] == "tool_observation_detail_surface"
