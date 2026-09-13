from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest
from langchain_core.messages import HumanMessage


MODULE_PATH = Path(__file__).with_name("run_cross_graph_provider_capture.py")
spec = importlib.util.spec_from_file_location("cross_graph_provider_capture", MODULE_PATH)
capture_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = capture_module
spec.loader.exec_module(capture_module)


def test_capture_requires_live_and_rejects_real_state_before_install(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_module.ScopedCapture, "install", lambda _self: pytest.fail("capture must not install"))
    args = ["--state-root", str(tmp_path), "--workspace-marker", "cross-graph-live-synthetic", "--output", str(tmp_path / "capture.jsonl")]
    with pytest.raises(SystemExit, match="2"):
        capture_module.main(args)
    with pytest.raises(SystemExit, match="2"):
        capture_module.main(["--live", *args, "--state-root", str(Path.home() / ".v8-agent-os")])
    assert not (tmp_path / "capture.jsonl").exists()


def test_capture_scopes_payload_and_never_saves_goal_or_other_values(tmp_path):
    output = tmp_path / "capture.jsonl"
    capture = capture_module.ScopedCapture("cross-graph-live-synthetic", output)
    capture.request({"messages": [{"role": "user", "content": "unrelated"}]})
    assert not output.exists()
    calls = [{"function": {"name": "delegation_broker", "arguments": json.dumps({"mode": "dispatch", "tasks": [{
        "goal": "PRIVATE TASK BODY", "targetAgentName": "Named Agent", "executionLaneHint": None,
        "context": {"password": "NEVER CAPTURE THIS"}, "expectedOutputs": ["private-output"]}]})}}]
    capture.request({"messages": [{"role": "user", "content": "cross-graph-live-synthetic: hidden prompt"},
                                  {"role": "assistant", "tool_calls": calls}],
                     "headers": {"Authorization": "DO NOT SAVE"}, "tools": [{"function": {"name": "delegation_broker", "description": "Delegate", "parameters": {"type": "object"}}}]})
    text = output.read_text(encoding="utf-8")
    for sensitive in ("PRIVATE TASK BODY", "NEVER CAPTURE THIS", "private-output", "hidden prompt", "DO NOT SAVE", "Authorization"):
        assert sensitive not in text
    row = json.loads(text)
    assert row["boundary"] == "openai_final_request_payload"
    task = row["serializedHistoryToolCalls"][0]["arguments"]["tasks"][0]
    assert task["targetAgentName"]["value"] == "Named Agent"
    assert task["goal"]["length"] == len("PRIVATE TASK BODY") and task["goal"]["sha256"]
    assert task["executionLaneHint"] == {"type": "null"}


def test_actual_native_sdk_binding_preserves_delegation_union_required_fields():
    from core.llm_chat_adapter import V8ChatModelAdapter
    from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
    from core.tools.native.delegation_surface import supervisor_delegation_broker
    from core.prompt_cache_gateway import _tool_schema_hash
    model = V8OpenAICompatibleChatModel(model="offline-fixture", api_key="test-only-not-a-credential")
    adapter = V8ChatModelAdapter(model_id="offline-fixture", provider_standard="openai", role="supervisor",
        meta={"capabilityClass": "chat_tool_calling", "capabilities": {"supportsTools": True, "supportsStreaming": True}},
        model_kwargs={}, builder=lambda: model).bind_tools([supervisor_delegation_broker])
    bound = adapter._get_runtime_model()
    payload = model._get_request_payload([HumanMessage(content="offline fixture")], **bound.kwargs)
    function = payload["tools"][0]["function"]
    assert "targetAgentName" in function["description"]
    tasks = function["parameters"]["properties"]["tasks"]
    array = next(item for item in tasks["anyOf"] if item.get("type") == "array")
    local, external = array["items"]["anyOf"]
    assert {"taskBriefId", "goal", "expectedOutputs", "acceptanceContract", "targetAgentName"}.issubset(local["required"])
    assert {"taskBriefId", "goal", "expectedOutputs", "acceptanceContract", "executionLaneHint"}.issubset(external["required"])
    assert external["properties"]["executionLaneHint"]["const"] == "external_worker"
    assert local["properties"]["targetAgentName"]["minLength"] == 1
    assert len(local["properties"]) > 5
    assert adapter.bind_tools([])._bound_model is None
    assert _tool_schema_hash([supervisor_delegation_broker])
