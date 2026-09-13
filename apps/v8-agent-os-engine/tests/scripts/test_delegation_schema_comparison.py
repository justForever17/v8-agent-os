from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk

from tests.scripts import run_delegation_schema_comparison as probe


def fixture_args(tmp_path, monkeypatch):
    state = tmp_path / "synthetic-state"
    state.mkdir()
    with sqlite3.connect(state / "state.db") as connection:
        connection.execute("CREATE TABLE run_records (id TEXT, status TEXT)")
        connection.execute("INSERT INTO run_records VALUES ('fixture-run','cancelled')")
    monkeypatch.setenv("V8_AGENT_OS_HOME", str(state))
    capture = tmp_path / "captured.jsonl"
    capture.write_text(json.dumps({"boundary": "openai_final_request_payload", "tools": [{"name": "delegation_broker",
        "description": "captured public fixture", "parameters": {"properties": {"tasks": {"anyOf": [{"type": "array", "items": {
            "anyOf": [{"required": ["targetAgentName"]}]}}]}}}}]}), encoding="utf-8")
    return SimpleNamespace(live=True, state_root=state, source_run_id="fixture-run", captured_schema=capture,
                           output_dir=tmp_path / "results", expected_model="fixture-model", marker="cross-graph-live-schema-comparison-fixture")


@pytest.mark.parametrize("case", ["no_live", "environment_mismatch", "real_state", "source_running", "existing_output"])
def test_probe_guards_reject_before_provider_calls(tmp_path, monkeypatch, case):
    args = fixture_args(tmp_path, monkeypatch)
    if case == "no_live":
        args.live = False
    elif case == "environment_mismatch":
        monkeypatch.delenv("V8_AGENT_OS_HOME")
    elif case == "real_state":
        from pathlib import Path
        args.state_root = Path.home() / ".v8-agent-os"
        monkeypatch.setenv("V8_AGENT_OS_HOME", str(args.state_root))
    elif case == "source_running":
        with sqlite3.connect(args.state_root / "state.db") as connection:
            connection.execute("UPDATE run_records SET status='running'")
    else:
        args.output_dir.mkdir()
    with pytest.raises(ValueError):
        probe.guard(args)
    assert not (args.output_dir / "result.json").exists()


def test_probe_rejects_inconsistent_captured_public_schemas(tmp_path, monkeypatch):
    args = fixture_args(tmp_path, monkeypatch)
    first = args.captured_schema.read_text(encoding="utf-8")
    second = json.loads(first)
    second["tools"][0]["description"] = "changed schema"
    args.captured_schema.write_text(first + "\n" + json.dumps(second), encoding="utf-8")
    with pytest.raises(ValueError, match="consistent_supervisor_public_schema"):
        probe.guard(args)


def test_probe_six_sequential_fresh_samples_without_retry_or_tool_execution(tmp_path, monkeypatch):
    from core.llm_factory import llm_factory
    from core.llm_exceptions import V8LLMStructuredOutputError
    from core.tools.native.delegation import delegation_broker
    from core.tools.native.delegation_surface import supervisor_delegation_broker
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from tests.scripts.run_cross_graph_provider_capture import ScopedCapture
    args = fixture_args(tmp_path, monkeypatch)
    probe.guard(args)
    schema = convert_to_openai_tool(supervisor_delegation_broker)
    monkeypatch.setattr(ScopedCapture, "install", lambda _self: None)
    monkeypatch.setattr(delegation_broker, "func", lambda **_kwargs: pytest.fail("never execute a returned tool"))
    calls, prompts, creations = [], [], []

    class FixtureModel:
        model_id = "fixture-model"
        _meta = {"model_record": {"outputTokenMode": "auto"}, "model_ref": "fixture-model", "model_id": "fixture-model"}
        _model_kwargs = {}
        max_retries = 0

        def bind_tools(self, tools, **kwargs):
            assert kwargs == {"tool_choice": "required"}
            calls.append(tools)
            return self

        def _get_runtime_model(self):
            return self

        def stream(self, messages):
            prompts.append([message.content for message in messages])
            if len(prompts) == 2:
                raise V8LLMStructuredOutputError(code="model_output_incomplete", message="PRIVATE ERROR",
                                               details={"reason": "incomplete_tool_arguments"})
            expected = json.loads(messages[-1].content.split("\n", 1)[1])
            yield AIMessageChunk(content="", tool_calls=[{"name": "delegation_broker", "id": "fixture", "args": expected}])

    def create(role, **kwargs):
        creations.append((role, kwargs))
        return FixtureModel()
    monkeypatch.setattr(llm_factory, "create_for_role", create)
    report = probe.run_comparison(args, schema)
    assert len(creations) == len(calls) == len(prompts) == 6
    assert all(item == ("supervisor", {"streaming": True, "max_retries": 0}) for item in creations)
    assert all(prompt == prompts[0] for prompt in prompts)
    assert all(call == [schema] for call in calls[::2])
    assert all(call == calls[1] for call in calls[1::2]) and calls[0] != calls[1]
    assert [row["group"] for row in report["results"]] == ["capturedPublic", "internal"] * 3
    assert report["results"][1]["adapterAccepted"] is False
    assert all(row["publicValid"] and row["internalValid"] and row["matchesExplicitTask"] for row in report["results"] if row["adapterAccepted"])
    assert "PRIVATE ERROR" not in (args.output_dir / "result.json").read_text(encoding="utf-8")


def test_probe_does_not_coerce_nested_json_or_treat_mode_only_as_success():
    from core.tools.native.delegation import delegation_broker
    from core.tools.native.delegation_surface import supervisor_delegation_broker
    for arguments in ({"mode": "dispatch"}, {"mode": "dispatch", "tasks": '[]'}):
        result = probe.response_checks(SimpleNamespace(tool_calls=[{"name": "delegation_broker", "args": arguments}]),
                                       supervisor_delegation_broker.args_schema, delegation_broker.args_schema,
                                       {"mode": "dispatch", "tasks": [{"taskBriefId": "expected"}]})
        assert not result["matchesExplicitTask"]
        if isinstance(arguments.get("tasks"), str):
            assert not result["publicValid"]


def test_real_factory_native_id_matches_qualified_ref_without_network(monkeypatch):
    import httpx
    from core.llm_factory import LLMFactory, model_control_plane
    canonical = "fixture-provider::FixtureNative"
    monkeypatch.setattr(httpx.Client, "send", lambda *_a, **_k: pytest.fail("factory identity check must not use network"))
    monkeypatch.setattr(httpx.AsyncClient, "send", lambda *_a, **_k: pytest.fail("factory identity check must not use network"))
    monkeypatch.setattr(model_control_plane, "resolve_model_for_role", lambda role: {"resolvedModelRef": canonical})
    monkeypatch.setattr(model_control_plane, "get_role_temperature", lambda role: None)
    monkeypatch.setattr(LLMFactory, "_resolve_model_metadata", classmethod(lambda cls, ref: {
        "is_found": True, "model_ref": canonical, "model_id": "FixtureNative", "provider_id": "fixture-provider",
        "api_standard": "openai", "wire_protocol": "openai.chat_completions", "api_key": "test-only-not-a-credential",
        "model_record": {"outputTokenMode": "auto"}, "capabilities": {"supportsTools": True, "supportsStreaming": True},
    }))
    model = LLMFactory.create_for_role("supervisor", streaming=True, max_retries=0)
    assert model.model_id == "FixtureNative" and model.model_id != canonical
    assert probe.assert_expected_model(model, canonical) == {"canonicalModelRef": canonical, "nativeModelId": "FixtureNative"}
    assert model._get_base_model().max_retries == 0
    for expected in ("different-provider::FixtureNative", "fixture-provider::DifferentNative", "FixtureNative"):
        with pytest.raises(ValueError, match="configured_model_changed"):
            probe.assert_expected_model(model, expected)
    model.model_id = "unannounced-wire-change"
    with pytest.raises(ValueError, match="configured_model_changed"):
        probe.assert_expected_model(model, canonical)
