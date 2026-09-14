from __future__ import annotations

import importlib.util
import asyncio
import json
from pathlib import Path
import sys

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage


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
    calls[0]["id"] = "synthetic-call"
    capture.request({"messages": [{"role": "user", "content": "cross-graph-live-synthetic: hidden prompt"},
                                  {"role": "system", "content": "[registeredAgentIndex] PRIVATE REGISTRY BODY"},
                                  {"role": "assistant", "tool_calls": calls},
                                  {"role": "tool", "tool_call_id": "synthetic-call", "content": "PRIVATE REPLY targetAgentName expectedOutputs acceptanceContract agent_broker"}],
                     "tool_choice": "required",
                     "headers": {"Authorization": "DO NOT SAVE"}, "tools": [{"function": {"name": "delegation_broker", "description": "Delegate", "parameters": {"type": "object"}}}]})
    text = output.read_text(encoding="utf-8")
    for sensitive in ("PRIVATE TASK BODY", "NEVER CAPTURE THIS", "private-output", "hidden prompt", "DO NOT SAVE", "Authorization", "PRIVATE REGISTRY BODY", "PRIVATE REPLY"):
        assert sensitive not in text
    row = json.loads(text)
    assert row["boundary"] == "openai_final_request_payload"
    task = row["serializedHistoryToolCalls"][0]["arguments"]["tasks"][0]
    assert task["targetAgentName"]["value"] == "Named Agent"
    assert task["goal"]["length"] == len("PRIVATE TASK BODY") and task["goal"]["sha256"]
    assert task["executionLaneHint"] == {"type": "null"}
    assert row["toolChoice"] == "required" and row["availableToolNames"] == ["delegation_broker"]
    assert row["promptFacts"]["registeredAgentIndexPresent"] is True
    assert row["delegationReplyFacts"][0]["targetAgentNamePresent"] is True


def test_actual_native_sdk_binding_preserves_delegation_union_required_fields():
    from core.llm_chat_adapter import V8ChatModelAdapter
    from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
    from core.tools.native.delegation_surface import supervisor_delegation_broker
    from core.tools.native.agent import agent_broker
    from core.prompt_cache_gateway import _tool_schema_hash
    model = V8OpenAICompatibleChatModel(model="offline-fixture", api_key="test-only-not-a-credential")
    adapter = V8ChatModelAdapter(model_id="offline-fixture", provider_standard="openai", role="supervisor",
        meta={"capabilityClass": "chat_tool_calling", "capabilities": {"supportsTools": True, "supportsStreaming": True}},
        model_kwargs={}, builder=lambda: model).bind_tools([supervisor_delegation_broker, agent_broker], tool_choice="required")
    bound = adapter._get_runtime_model()
    payload = model._get_request_payload([HumanMessage(content="offline fixture")], **bound.kwargs)
    assert payload["tool_choice"] == "required"
    assert {tool["function"]["name"] for tool in payload["tools"]} == {"delegation_broker", "agent_broker"}
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


def test_capture_distinguishes_actual_model_requests_without_endpoint_credentials(tmp_path):
    from types import SimpleNamespace
    capture = capture_module.ScopedCapture("cross-graph-live-synthetic", tmp_path / "capture.jsonl")
    for model, model_ref in [("MiniMax-M3", "minimax-cn::MiniMax-M3"), ("fallback-fixture", "configured::fallback-fixture")]:
        capture.request({"model": model, "messages": [{"role": "user", "content": capture.marker}]}, model_ref=model_ref)
        capture.response(SimpleNamespace(content="", tool_calls=[], additional_kwargs={}, response_metadata={}))
    unsafe = "https://user:PRIVATE-CREDENTIAL@host.invalid/model?token=PRIVATE"
    capture.request({"model": unsafe, "messages": [{"content": capture.marker}]}, model_ref=unsafe)
    rows = [json.loads(line) for line in capture.output.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["modelIdentity"]["wireModel"]["value"] == "MiniMax-M3"
    assert rows[2]["modelIdentity"]["modelRef"]["value"] == "configured::fallback-fixture"
    assert rows[1]["elapsedMs"] >= 0 and rows[1]["finishedAt"] >= rows[0]["requestedAt"]
    assert "value" not in rows[4]["modelIdentity"]["wireModel"]
    assert "PRIVATE" not in capture.output.read_text(encoding="utf-8")


@pytest.mark.parametrize("ending", ["done", "eof", "error"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_sdk_sse_termination_facts_do_not_expose_content(tmp_path, monkeypatch, ending, asynchronous):
    import httpx
    import openai
    from openai import _streaming
    from core.llm_chat_adapter import V8ChatModelAdapter
    from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
    from types import SimpleNamespace

    for owner, names in ((V8OpenAICompatibleChatModel, ("_stream", "_astream", "_get_request_payload", "_create_chat_result", "_convert_chunk_to_generation_chunk")),
                         (V8ChatModelAdapter, ("_prepare_prompt_cache_request", "_validate_complete_tool_response")),
                         (_streaming.Stream, ("_iter_events",)), (_streaming.AsyncStream, ("_iter_events",))):
        for name in names:
            monkeypatch.setattr(owner, name, getattr(owner, name))
    capture = capture_module.ScopedCapture("cross-graph-live-synthetic", tmp_path / "capture.jsonl")
    capture.install()
    capture.request({"model": "fixture", "messages": [{"content": capture.marker}], "stop": "PRIVATE STOP"})
    body = 'data: {"id":"fixture","choices":[{"index":0,"delta":{"content":"PRIVATE RESPONSE"}}]}\n\n'
    body += 'data: [DONE]\n\n' if ending == "done" else 'event: error\ndata: {"error":{"message":"PRIVATE ERROR"}}\n\n' if ending == "error" else ""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body))
    async def consume_async():
        async with httpx.AsyncClient(transport=transport) as http:
            client = openai.AsyncOpenAI(api_key="synthetic", base_url="https://fixture.invalid/v1", http_client=http)
            stream = await client.chat.completions.create(model="fixture", messages=[], stream=True)
            return [event async for event in stream]
    def consume():
        if asynchronous:
            return asyncio.run(consume_async())
        with httpx.Client(transport=transport) as http:
            client = openai.OpenAI(api_key="synthetic", base_url="https://fixture.invalid/v1", http_client=http)
            return list(client.chat.completions.create(model="fixture", messages=[], stream=True))
    if ending == "error":
        with pytest.raises(openai.APIError):
            consume()
    else:
        assert len(consume()) == 1
    capture.response(SimpleNamespace(content="", tool_calls=[], additional_kwargs={}, response_metadata={}))
    text = capture.output.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    assert rows[-1]["sse"] == {"doneSeen": ending == "done", "namedErrorEventSeen": ending == "error", "cleanEOF": ending == "eof"}
    assert rows[0]["stopPolicy"]["sha256"] == capture_module._hash("PRIVATE STOP")
    assert "PRIVATE" not in text


def test_continuation_fingerprints_detect_wire_loss_and_history_replay_without_text(tmp_path):
    from types import SimpleNamespace
    capture = capture_module.ScopedCapture("cross-graph-live-synthetic", tmp_path / "capture.jsonl")
    content = "PRIVATE BODY <tool_call><invoke name=\"fixture\">hidden</invoke></tool_call>"
    reasoning = "PRIVATE REASONING secret=" + "not-a-real-credential"
    details = [{"type": "reasoning.text", "text": reasoning}]
    capture.request({"messages": [{"role": "user", "content": capture.marker},
                                  {"role": "assistant", "content": content, "reasoning_content": reasoning,
                                   "reasoning_details": details, "tool_calls": [{"id": "fixture"}]}],
                     "extra_body": {"reasoning_split": True}})
    # The public protocol marker crosses an actual delta boundary. Simulate
    # a converter dropping content while preserving reasoning continuation.
    for part in (content[:20], content[20:24], content[24:]):
        capture.conversion({"choices": [{"index": 0, "delta": {"content": part}}]}, None)
    capture.conversion({"choices": [{"index": 0, "delta": {"reasoning_details": details}}]}, None)
    capture.response(SimpleNamespace(content="", additional_kwargs={"reasoning_details": details}, tool_calls=[],
                                     usage_metadata=None, response_metadata={}))
    rows = [json.loads(line) for line in capture.output.read_text(encoding="utf-8").splitlines()]
    request, response = rows[0], rows[-1]
    assert request["reasoningSplitRequested"] is True
    history = request["assistantContinuationFacts"][0]["fields"]
    assert history["reasoning_details"] == response["continuationFacts"]["reasoning_details"]
    wire = response["wireContinuationFacts"][0]["fields"]["content"]
    assert wire["concatenatedFragmentsSha256"] == capture_module._hash(content)
    assert wire["chars"] == len(content) and wire["fragments"] == 3
    assert wire["protocolMarkers"]["<tool_call>"] == 1
    assert wire["protocolMarkers"]["</invoke>"] == 1
    assert response["continuationFacts"]["content"]["chars"] == 0
    evidence = capture.output.read_text(encoding="utf-8")
    for private in ("PRIVATE BODY", "PRIVATE REASONING", "not-a-real-credential", "hidden", "secret="):
        assert private not in evidence
    capture.request({"messages": [{"role": "user", "content": capture.marker}]})
    assert capture_module.wire_field_facts(capture.current.get()) == []


def test_native_reasoning_fingerprint_ignores_only_sdk_merge_indexes():
    def fingerprint(details):
        return capture_module.continuation_facts({"reasoning_details": details})["reasoning_details"]["nativeFieldsSha256"]
    native = [{"id": "native-block", "type": "reasoning.text", "format": "fixture", "text": "AA"}]
    merged = [{**native[0], "index": "lc_v8_reasoning_0"}]
    assert fingerprint(native) == fingerprint(merged)
    for change in ({"text": "A"}, {"id": "another-block"}, {"index": 7}, {"format": "changed"}):
        assert fingerprint(native) != fingerprint([{**native[0], **change}])


def test_raw_invalid_arguments_and_metadata_never_save_private_values(tmp_path):
    from types import SimpleNamespace
    capture = capture_module.ScopedCapture("cross-graph-live-synthetic", tmp_path / "capture.jsonl")
    private = '{"mode":"dispatch", "goal":"PRIVATE ARGUMENT", "任务":"秘密"'
    capture.request({"messages": [{"content": capture.marker}], "max_tokens": 1024,
                     "max_completion_tokens": 2048, "extra_body": {"max_tokens": 512, "credential": "NEVER SAVE"}})
    capture.response(SimpleNamespace(tool_calls=[], tool_call_chunks=[], additional_kwargs={},
        invalid_tool_calls=[{"name": "delegation_broker", "args": private, "error": "PRIVATE ERROR BODY"}],
        usage_metadata=None, response_metadata={"finish_reason": "length", "token_usage": {
            "completion_tokens": 1024, "completion_tokens_details": {"reasoning_tokens": 200, "secret": "PRIVATE USAGE"}}}))
    text = capture.output.read_text(encoding="utf-8")
    for secret in ("PRIVATE ARGUMENT", "秘密", "PRIVATE ERROR BODY", "PRIVATE USAGE", "NEVER SAVE"):
        assert secret not in text
    request, response = [json.loads(line) for line in text.splitlines()]
    assert request["outputCaps"]["topLevel.max_tokens"] == {"present": True, "value": 1024}
    assert request["outputCaps"]["topLevel.max_completion_tokens"]["value"] == 2048
    assert request["outputCaps"]["extra_body.max_tokens"]["value"] == 512
    assert response["finishReason"] == "length" and response["usageReported"]
    args = response["argumentViews"]["invalid_tool_calls"][0]["arguments"]
    assert args["chars"] == len(private) and args["utf8Bytes"] > len(private)
    assert args["sha256"] == capture_module._hash(private)
    assert args["errorCategory"] == "JSONDecodeError" and not args["completeObject"]
    capture.request({"messages": [{"content": "unrelated task"}]})
    capture.response(SimpleNamespace(tool_calls=[], usage_metadata={"total_tokens": 99}))
    assert capture.output.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("outcome", ["complete", "incomplete", "interrupted"])
def test_capture_real_sdk_and_adapter_boundaries_preserve_stream_and_rejection(tmp_path, monkeypatch, asynchronous, outcome):
    from langchain_openai import ChatOpenAI
    from core.llm_chat_adapter import V8ChatModelAdapter
    from core.llm_exceptions import V8LLMStructuredOutputError
    from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
    from core.tools.native.delegation_surface import supervisor_delegation_broker

    capture = capture_module.ScopedCapture("cross-graph-live-synthetic", tmp_path / "capture.jsonl")
    fragments = ['{"mode":', '"dispatch"']
    if outcome == "complete":
        fragments[-1] += '}'

    def fake_stream(model, messages, **kwargs):
        model._get_request_payload(messages, **kwargs)
        for index, fragment in enumerate(fragments):
            function = {"arguments": fragment}
            call = {"index": 0, "function": function}
            if index == 0:
                function["name"] = "delegation_broker"
                call.update({"id": "fixture-call", "type": "function"})
            yield model._convert_chunk_to_generation_chunk(
                {"choices": [{"index": 0, "delta": {"tool_calls": [call]}, "finish_reason": None}]}, AIMessageChunk, None)
        if outcome == "interrupted":
            raise TimeoutError("PRIVATE NETWORK ERROR")
        yield model._convert_chunk_to_generation_chunk(
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}, AIMessageChunk, None)
        if outcome == "complete":
            yield model._convert_chunk_to_generation_chunk(
                {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27}}, AIMessageChunk, None)

    async def fake_astream(model, messages, **kwargs):
        for chunk in fake_stream(model, messages, **kwargs):
            yield chunk

    monkeypatch.setattr(ChatOpenAI, "_stream", fake_stream)
    monkeypatch.setattr(ChatOpenAI, "_astream", fake_astream)
    # Capture monkey-patches are process local; restore every installed method.
    from openai import _streaming
    monkeypatch.setattr(_streaming.Stream, "_iter_events", _streaming.Stream._iter_events)
    monkeypatch.setattr(_streaming.AsyncStream, "_iter_events", _streaming.AsyncStream._iter_events)
    for owner, names in ((V8OpenAICompatibleChatModel, ("_stream", "_astream", "_get_request_payload", "_create_chat_result", "_convert_chunk_to_generation_chunk")),
                         (V8ChatModelAdapter, ("_prepare_prompt_cache_request", "_validate_complete_tool_response"))):
        for name in names:
            monkeypatch.setattr(owner, name, getattr(owner, name))
    capture.install()
    model = V8OpenAICompatibleChatModel(model="offline-fixture", api_key="test-only-not-a-credential")
    adapter = V8ChatModelAdapter(model_id="offline-fixture", provider_standard="openai", role="supervisor",
        meta={"model_record": {"outputTokenMode": "fixed", "maxTokens": 4000},
              "capabilityClass": "chat_tool_calling", "capabilities": {"supportsTools": True, "supportsStreaming": True}},
        model_kwargs={}, builder=lambda: model).bind_tools([supervisor_delegation_broker])
    messages = [HumanMessage(content=capture.marker)]
    def consume():
        if not asynchronous:
            return list(adapter.stream(messages, max_tokens=1200))
        async def collect():
            return [chunk async for chunk in adapter.astream(messages, max_tokens=1200)]
        return asyncio.run(collect())
    if outcome == "complete":
        assert consume()
    elif outcome == "incomplete":
        with pytest.raises(V8LLMStructuredOutputError):
            consume()
    else:
        with pytest.raises(Exception):
            consume()
    text = capture.output.read_text(encoding="utf-8")
    assert "PRIVATE NETWORK ERROR" not in text
    rows = [json.loads(line) for line in text.splitlines()]
    request = next(row for row in rows if row["boundary"] == "openai_final_request_payload")
    assert request["outputTokenPolicy"] == {"mode": "fixed", "maxTokens": 1200, "source": "request_budget"}
    caps = request["outputCaps"]
    assert caps["topLevel.max_completion_tokens"] == {"present": True, "value": 1200}
    assert caps["topLevel.max_tokens"]["present"] is False
    result = next(row for row in rows if row["boundary"] == "openai_sdk_assembled_response")
    assert result["toolCallsView"] == "sdk_parsed_may_repair_incomplete_json_not_wire_arguments"
    raw = result["argumentViews"]["tool_call_chunks"][0]["arguments"]
    assert raw["chars"] == len(''.join(fragments)) and raw["sha256"] == capture_module._hash(''.join(fragments))
    assert raw["completeObject"] == (outcome == "complete")
    deltas = [row for row in rows if row["boundary"] == "openai_sdk_input_delta_conversion"]
    assert sum(arg["arguments"]["chars"] for row in deltas for choice in row["choices"] for arg in choice["toolArguments"]) == raw["chars"]
    validations = [row for row in rows if row["boundary"] == "v8_adapter_complete_response_validation"]
    if outcome == "interrupted":
        assert result["streamEnd"] == "interrupted" and result["exceptionCategory"] == "TimeoutError"
        assert result["finishReason"] is None and not validations
    else:
        assert result["streamEnd"] == "completed" and result["finishReason"] == "tool_calls"
        assert validations[-1]["accepted"] == (outcome == "complete")
        assert validations[-1]["captureId"] == request["captureId"]
    if outcome == "incomplete":
        # This is the v7 counterexample: SDK parsed calls look usable while
        # the actual assembled JSON is incomplete and must remain rejected.
        assert result["toolCalls"][0]["arguments"]["mode"]["value"] == "dispatch"
        assert raw["jsonErrorOffset"] == len(''.join(fragments))
        assert result["usageReported"] is False and result["usage"] == {}
        assert validations[-1]["reason"] == "incomplete_tool_arguments"
    if outcome == "complete":
        assert result["usage"] == {"input_tokens": 20, "output_tokens": 7, "total_tokens": 27, "input_token_details": {}, "output_token_details": {}}
