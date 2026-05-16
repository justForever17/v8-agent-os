from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from core.tool_surface import apply_tool_surface_budget


def test_tool_surface_envelope_preserves_refs_and_next_action_for_truncated_json():
    payload = {
        "ok": True,
        "runId": "run_123",
        "recommendedNextAction": "inspect raw evidence",
        "items": [{"text": "x" * 2000} for _ in range(20)],
    }
    message = ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id="call_123",
        name="creative_media_catalog",
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

    rendered = json.loads(str(result.content))
    envelope = rendered["_v8ToolSurface"]
    assert envelope["runId"] == "run_123"
    assert envelope["toolCallId"] == "call_123"
    assert envelope["runtimeKind"] == "creative_media"
    assert envelope["refs"]["rawRef"].startswith("toolobs://")
    assert envelope["omitted"]["wasBudgetTruncated"] is True
    assert "tool_observation_detail" in envelope["nextAction"]
