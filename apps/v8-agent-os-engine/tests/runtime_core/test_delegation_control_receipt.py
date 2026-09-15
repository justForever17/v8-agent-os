from __future__ import annotations

import asyncio
import json
import re

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
from graph.tool_routing import create_routed_tool_node


def receipt(count=1):
    return {"ok": True, "mode": "dispatch", "summary": "Queued tasks", "items": [
        {"taskBriefId": f"task-{i}", "delegationId": f"subagent::dispatch-owned::branch-{i}::verifier",
         "taskGoal": "Read the existing fixture and run its verifier. " * 30,
         "targetLabel": "Fixture Verifier", "status": "queued", "lane": "subagent",
         "effectiveExecution": {"capsuleAttached": True, "executionMode": "read_only", "readSet": ["verify.py"],
                                "writeSet": [], "toolPolicyMode": "allowlist"},
         "toolPolicy": {"mode": "allowlist", "allowedTools": []},
         "selectionTrace": "INTERNAL_SELECTION_DIAGNOSTIC"} for i in range(count)]}


def test_dispatched_control_handle_survives_actual_toolnode_and_next_sdk_request():
    payload = receipt()

    @tool("delegation_broker")
    def dispatch_fixture() -> str:
        """Return an isolated successful dispatch receipt."""
        return json.dumps(payload)

    assistant = AIMessage(content="", tool_calls=[{"id": "dispatch-original", "name": "delegation_broker", "args": {}}])
    node = create_routed_tool_node([dispatch_fixture], "supervisor_tools", "supervisor")
    message = asyncio.run(node({"messages": [assistant]})).update["messages"][0]
    expected = payload["items"][0]["delegationId"]
    assert expected in message.content, "the task label cannot replace the returned episode control identity"
    assert "delegation_id" in message.content and "inspect" in message.content and "await" in message.content
    assert '"allowedTools":[]' in message.content.replace(" ", "")
    assert "INTERNAL_SELECTION_DIAGNOSTIC" not in message.content
    model = V8OpenAICompatibleChatModel(model="fixture", api_key="fixture-only", base_url="https://fixture.invalid/v1")
    wire = model._get_request_payload([HumanMessage(content="Delegate and wait for the exact child."), assistant, message])
    assert wire["messages"][-1]["tool_call_id"] == "dispatch-original"
    assert expected in wire["messages"][-1]["content"]


def test_oversized_dispatch_handles_recover_as_complete_safe_pages(tmp_path, monkeypatch):
    import core.observability_db as observations
    from core.tool_surface import apply_tool_surface_budget
    from core.native_tools import tool_observation_detail
    from langchain_core.messages import ToolMessage

    monkeypatch.setattr(observations, "observability_db", observations.ObservabilityDatabaseManager(tmp_path / "observations.db"))
    payload = receipt(12)
    message = apply_tool_surface_budget(ToolMessage(content=json.dumps(payload), name="delegation_broker", tool_call_id="many"),
                                        {"agentVisibleBudget": 900}, tool_name="delegation_broker")
    assert len(message.content) <= 900
    recovery = json.loads(message.content.split("\n", 1)[1])
    assert recovery["truncated"] and recovery["dispatchItemsOmitted"] == 12
    raw_ref = recovery["rawRef"]
    chunks, offset = [], 0
    for index in range(100):
        message = tool_observation_detail.invoke({"type": "tool_call", "name": "tool_observation_detail", "id": f"read-{index}",
            "args": {"raw_ref": raw_ref, "max_chars": 60000, "start_char": offset}})
        page = apply_tool_surface_budget(message, {"agentVisibleBudget": 1400}).content
        assert len(page) <= 1400
        chunks.append(page.split("<preview>\n", 1)[1].split("\n</preview>", 1)[0])
        cursor = re.search(r"next_start_char=(\d+)", page)
        if not cursor:
            assert "[end of observation]" in page
            break
        next_offset = int(cursor.group(1))
        assert next_offset > offset
        offset = next_offset
    else:
        raise AssertionError("dispatch receipt never reached its final page")
    recovered = json.loads("".join(chunks))
    assert [i["delegationId"] for i in recovered["items"]] == [i["delegationId"] for i in payload["items"]]
    assert all(i["toolPolicy"]["allowedTools"] == [] for i in recovered["items"])
