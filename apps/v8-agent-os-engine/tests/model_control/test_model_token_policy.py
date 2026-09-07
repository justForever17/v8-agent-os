from copy import deepcopy

import pytest

from core.model_control_plane import model_control_plane
from core.model_eligibility import evaluate_model_eligibility
from core.model_control_plane import _fact_provenance_for_patch
from core.model_token_policy import prepare_output_token_kwargs, resolve_output_token_budget


def config_for(model):
    return {"providers": {"fixture": {"provider": {"api_standard": "openai", "base_url": "https://example.org/v1"}, "models": {"model": model}}}}


def model(**overrides):
    return {"type": "TEXT", "contextWindow": 1_000_000, "maxTokens": 4096,
            "capabilities": {"chat": True, "streaming": True}, **overrides}


def test_confirmed_estimated_default_migrates_without_erasing_rollback_value():
    raw = config_for(model(factProvenance={"maxTokens": {
        "source": "v8_conservative_2026_default", "confidence": "estimated",
    }}))
    original = deepcopy(raw)
    normalized = model_control_plane.normalize_config(raw)
    saved = normalized["providers"]["fixture"]["models"]["model"]
    assert saved.get("outputTokenMode") == "auto"
    assert saved["maxTokens"] == 4096
    assert model_control_plane.normalize_config(normalized) == normalized
    assert raw == original


@pytest.mark.parametrize("source,value", [("user_confirmed", 4096), ("", 4096), ("v8_conservative_2026_default", 8192)])
def test_manual_unknown_and_edited_estimate_values_are_not_reclassified(source, value):
    provenance = {"maxTokens": {"source": source, "confidence": "estimated"}} if source else {}
    normalized = model_control_plane.normalize_config(config_for(model(maxTokens=value, factProvenance=provenance)))
    saved = normalized["providers"]["fixture"]["models"]["model"]
    assert saved["maxTokens"] == value
    assert saved.get("outputTokenMode") != "auto"


def test_explicit_auto_output_does_not_require_a_fabricated_model_limit():
    result = evaluate_model_eligibility(model(maxTokens=None, outputTokenMode="auto"))
    assert result["selectable"]
    assert "maxTokens" not in result["requiredFacts"]


@pytest.mark.parametrize("window", [500_000, 128_000, 32_000])
def test_user_context_budget_below_model_capacity_is_selectable(window):
    result = evaluate_model_eligibility(model(contextWindow=window))
    assert result["selectable"]
    assert result["contextWindow"] == window


def test_auto_and_partial_edits_preserve_previous_provenance():
    previous = model(factProvenance={
        "maxTokens": {"source": "v8_conservative_2026_default", "confidence": "estimated"},
        "contextWindow": {"source": "user_confirmed", "confidence": "authoritative"},
    })
    patch = _fact_provenance_for_patch({"outputTokenMode": "auto", "maxTokens": 4096}, "manual", previous)
    assert patch["factProvenance"] == previous["factProvenance"]
    partial = _fact_provenance_for_patch({"temperature": 0.5}, "manual", previous)
    assert partial["factProvenance"] == previous["factProvenance"]
    manual = _fact_provenance_for_patch({"maxTokens": 8192}, "manual", previous)
    assert manual["outputTokenMode"] == "fixed"
    assert manual["factProvenance"]["maxTokens"]["source"] == "user_confirmed"
    assert manual["factProvenance"]["contextWindow"] == previous["factProvenance"]["contextWindow"]


def test_auto_omits_optional_budget_and_does_not_erase_old_value():
    meta = {"model_record": model(outputTokenMode="auto")}
    before = deepcopy(meta)
    assert resolve_output_token_budget(meta) == {"mode": "auto", "maxTokens": None, "source": "provider_default"}
    assert resolve_output_token_budget(meta, requires_value=True) == {
        "mode": "auto", "maxTokens": 32768, "source": "required_parameter_default",
    }
    assert meta == before


def test_resubmitting_unchanged_budget_does_not_invent_manual_provenance():
    previous = model()
    patch = _fact_provenance_for_patch({"maxTokens": 4096, "outputTokenMode": "fixed", "contextWindow": 1000000}, "manual", previous)
    assert patch["factProvenance"] == {}
    estimated = model(outputTokenMode="auto", factProvenance={"maxTokens": {
        "source": "v8_conservative_2026_default", "confidence": "estimated",
    }})
    fixed = _fact_provenance_for_patch({"maxTokens": 4096, "outputTokenMode": "fixed"}, "manual", estimated)
    assert fixed["factProvenance"]["maxTokens"]["source"] == "user_confirmed"


@pytest.mark.parametrize("builder,key", [("_build_openai_kwargs", "max_tokens"),
                                        ("_build_anthropic_kwargs", "max_tokens_to_sample"),
                                        ("_build_gemini_kwargs", "max_output_tokens")])
def test_factory_respects_user_cap_across_provider_aliases(builder, key):
    from core.llm_factory import LLMFactory
    meta = {"model_record": model(outputTokenMode="fixed"), "api_key": "test-only"}
    result = getattr(LLMFactory, builder)("fixture", meta, max_tokens=7500, max_completion_tokens=10000,
                                        extra_body={"max_tokens": 20000, "kept": True})
    assert result[key] == 4096
    assert result["extra_body"] == {"kept": True}
    assert "max_completion_tokens" not in result


def test_openai_sdk_serializes_auto_without_cap_and_fixed_with_user_cap():
    from core.llm_factory import LLMFactory
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    for mode, expected in [("auto", None), ("fixed", 4096)]:
        kwargs = LLMFactory._build_openai_kwargs("fixture", {"model_record": model(outputTokenMode=mode), "api_key": "test-only"})
        payload = ChatOpenAI(**kwargs)._get_request_payload([HumanMessage(content="test")])
        assert payload.get("max_completion_tokens") == expected
        assert "max_tokens" not in payload


def test_anthropic_thinking_cannot_raise_explicit_user_cap():
    from core.llm_factory import LLMFactory
    with pytest.raises(ValueError, match="output budget.*thinking budget"):
        LLMFactory._build_anthropic_kwargs("fixture", {"model_record": model(outputTokenMode="fixed")},
                                          thinking={"type": "enabled", "budget_tokens": 4096})


@pytest.mark.parametrize("value", [0, -1, 1.5, "bad", True, float("inf")])
def test_invalid_explicit_request_budget_does_not_silently_become_auto(value):
    with pytest.raises(ValueError, match="positive integer"):
        prepare_output_token_kwargs({}, {"max_tokens": value})


def test_aliases_do_not_mutate_caller_payload_or_raise_a_narrow_request_budget():
    payload = {"max_tokens": 2000, "extra_body": {"max_completion_tokens": 20000}, "model_kwargs": {"max_tokens": 4000}}
    before = deepcopy(payload)
    result, budget = prepare_output_token_kwargs({"model_record": model()}, payload)
    assert result == {"max_tokens": 2000, "extra_body": {}, "model_kwargs": {}}
    assert budget["source"] == "request_budget"
    assert payload == before


@pytest.mark.parametrize("mode", [None, "auto", "fixed"])
def test_catalog_reconnect_does_not_overwrite_existing_user_or_unknown_budget(mode):
    from core.model_catalog_connection import build_catalog_model_connection_plan
    existing = model(contextWindow=128000, maxTokens=9216)
    if mode:
        existing["outputTokenMode"] = mode
    plan = build_catalog_model_connection_plan(
        provider={"id": "fixture", "name": "Fixture", "apiStandard": "openai",
                  "baseUrl": "https://example.org/v1", "auth": {"type": "none"}},
        model=model(factProvenance={"maxTokens": {"source": "v8_conservative_2026_default", "confidence": "estimated"}}),
        model_id="fixture", existing_model=existing,
    )
    patch = plan["modelPatch"]
    assert patch["contextWindow"] == 128000
    assert patch["maxTokens"] == 9216
    assert patch["outputTokenMode"] == (mode or "fixed")
    assert "maxTokens" not in patch["factProvenance"]


def test_mode_enum_allowance_does_not_open_secret_patch_fields():
    from core.config_broker_service import _canonical_model_patch, ConfigBrokerError
    assert _canonical_model_patch({"outputTokenMode": "auto"}) == {"outputTokenMode": "auto"}
    for field in ["accessToken", "apiKey", "password"]:
        with pytest.raises(ConfigBrokerError) as failure:
            _canonical_model_patch({"outputTokenMode": "auto", field: "private"})
        assert failure.value.code == "config_secret_in_patch"
    with pytest.raises(ConfigBrokerError):
        _canonical_model_patch({"outputTokenMode": {"accessToken": "private"}})


@pytest.mark.parametrize("asynchronous,streaming", [(False, False), (True, False), (False, True), (True, True)])
def test_adapter_enforces_budget_at_actual_invoke_boundary(asynchronous, streaming):
    import asyncio
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
    from core.llm_chat_adapter import V8ChatModelAdapter
    captured = []

    class Native:
        def invoke(self, messages, **kwargs):
            captured.append(kwargs)
            return AIMessage(content="ok")
        async def ainvoke(self, messages, **kwargs):
            return self.invoke(messages, **kwargs)
        def stream(self, messages, **kwargs):
            captured.append(kwargs)
            yield AIMessageChunk(content="ok")
        async def astream(self, messages, **kwargs):
            for chunk in self.stream(messages, **kwargs):
                yield chunk

    adapter = V8ChatModelAdapter(model_id="fixture", provider_standard="openai", role="subagent",
                                meta={"model_record": model(), "capabilities": {"streamUsage": True}}, model_kwargs={}, builder=Native)
    messages = [HumanMessage(content="test")]
    kwargs = {"max_completion_tokens": 7500, "extra_body": {"max_tokens": 10000}}
    async def call():
        if streaming:
            return [chunk async for chunk in adapter.astream(messages, **kwargs)]
        return await adapter.ainvoke(messages, **kwargs)
    if asynchronous:
        asyncio.run(call())
    elif streaming:
        list(adapter.stream(messages, **kwargs))
    else:
        adapter.invoke(messages, **kwargs)
    assert len(captured) == 1
    assert captured[0]["max_tokens"] == 4096
    assert "max_completion_tokens" not in captured[0]
    assert "max_tokens" not in captured[0].get("extra_body", {})
    assert captured[0].get("stream_usage") is (True if streaming else None)
