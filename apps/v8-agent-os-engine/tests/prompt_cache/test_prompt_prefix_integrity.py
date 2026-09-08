import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from core.prompt_cache_gateway import PromptCacheGateway
from core.prompt_cache_segments import build_prompt_segments_from_parts, static_prompt_parts_first
from graph.agent_factories import _build_agent_system_bundle
from graph.supervisor_context import _split_runtime_registry_prompt_parts


def test_worker_prefix_stays_identical_when_live_state_changes():
    bundles = [_build_agent_system_bundle(
        agent_name="Verifier", agent_system_prompt="Preserve credentials and exact source refs.",
        env_context=f"<environment>\nOS: Windows\nCurrent Time: {stamp}\nHost Load: {load}\nUser-Visible Language: zh-CN\n</environment>\n",
        collaboration_identity_context=f"<actor>run-{stamp}</actor>\n",
        delegated_plan_context=f"<task>step-{stamp}</task>\n", route_prompt_addition=f"<extension>{load}</extension>\n",
    ) for stamp, load in [(1, 20), (2, 50)]]
    prefixes = []
    for bundle in bundles:
        content, segments = bundle["content"], bundle["segments"]
        end = next(s["startOffset"] for s in segments if s["type"] == "dynamic")
        prefixes.append(content[:end])
        assert content.index("</environment>") < end
        assert "Current Time:" not in content[:end]
        assert "User-Visible Language: zh-CN" in content[:end]
        assert all(s["type"] == "dynamic" for s in segments if s["startOffset"] >= end)
    assert prefixes[0] == prefixes[1]
    assert bundles[0]["content"] != bundles[1]["content"]


def test_registry_recommendation_moves_without_broken_tag_or_lost_descriptor():
    raw = "<capability_registry>\n推荐路由: research\n- kind=research | Deep research\n</capability_registry>\n"
    parts = static_prompt_parts_first(_split_runtime_registry_prompt_parts(raw))
    content = "".join(p["text"] for p in parts)
    assert content.index("</capability_registry>") < content.index("推荐路由:")
    assert "- kind=research | Deep research" in parts[0]["text"]
    assert content.count("<capability_registry>") == content.count("</capability_registry>") == 1
    assert "推荐路由: research" in content


def test_anthropic_system_merge_keeps_segments_and_dynamic_summary_separate():
    from core.llm_chat_adapter import AnthropicSurface
    main = structured([{"type": "stable_static", "source": "main", "text": "Preserve credentials.\n"}])
    summary = SystemMessage(content="Partial task summary; claim-1 is still unverified")
    merged = AnthropicSurface().normalize_messages([main, summary, HumanMessage(content="continue")])
    segments = PromptCacheGateway()._segment_messages(merged)
    assert segments[0].source == "main" and segments[0].segment_type == "stable_static"
    assert all(s.segment_type == "dynamic" for s in segments[1:])
    assert "claim-1 is still unverified" in merged[0].content


def structured(parts):
    return SystemMessage(content="".join(p["text"] for p in parts), additional_kwargs={"v8_prompt_segments": build_prompt_segments_from_parts(parts)})


def test_credential_instructions_do_not_erase_verified_static_segments():
    message = structured([{"type": "stable_static", "source": "charter", "text": "Never disclose credentials or passwords.\n"},
                          {"type": "dynamic", "source": "task", "text": "Current task: verify."}])
    gateway = PromptCacheGateway()
    result = gateway.prepare_request(messages=[message, HumanMessage(content="check")], kwargs={},
        provider_id="minimax-cn", model_id="MiniMax-M3", record=False, streaming=True, lookup_response_cache=False)
    assert result.diagnostics["segments"][0]["type"] == "stable_static"
    assert result.diagnostics["segments"][0]["charCount"] > 0
    assert result.messages[0].content == message.content
    assert result.diagnostics["providerRequestPatch"]["observeOnly"] is True
    assert result.diagnostics["profileId"] == "minimax_implicit_prompt_cache"


@pytest.mark.parametrize("corruption", ["hash", "gap", "overlap"])
def test_invalid_segment_metadata_cannot_claim_a_reusable_prefix(corruption):
    message = structured([{"type": "stable_static", "source": "charter", "text": "credentials: opaque value"}])
    segment = message.additional_kwargs["v8_prompt_segments"][0]
    if corruption == "hash":
        segment["hash"] = "stale"
    elif corruption == "gap":
        segment["startOffset"] = 1
    else:
        message.additional_kwargs["v8_prompt_segments"].append(dict(segment))
    result = PromptCacheGateway()._segment_messages([message])
    assert [s.segment_type for s in result] == ["unsafe"]


@pytest.mark.parametrize("metadata", [["bad"], [{"startOffset": "not-a-number"}], "invalid"])
def test_malformed_anthropic_segment_metadata_does_not_break_the_request(metadata):
    from core.llm_chat_adapter import AnthropicSurface
    first = SystemMessage(content="Keep this instruction", additional_kwargs={"v8_prompt_segments": metadata})
    merged = AnthropicSurface().normalize_messages([first, SystemMessage(content="Keep this summary")])
    assert "Keep this instruction" in merged[0].content
    assert "Keep this summary" in merged[0].content
    assert all(s.segment_type == "dynamic" for s in PromptCacheGateway()._segment_messages(merged))


@pytest.mark.parametrize("secret", ["api_key=canary-secret-value", "plain task text"])
def test_invalid_metadata_cannot_fall_back_to_whole_system_active_cache(secret):
    message = structured([{"type": "unsafe", "source": "private", "text": secret}])
    message.additional_kwargs["v8_prompt_segments"][0]["hash"] = "stale"
    prepared = PromptCacheGateway().prepare_request(messages=[message], kwargs={}, provider_id="anthropic",
        model_id="fixture", record=False, lookup_response_cache=False)
    assert prepared.messages[0].content == secret
    assert "cache_control" not in prepared.diagnostics["providerRequestPatch"]
    assert all(s["type"] not in {"stable_static", "scoped_static"} for s in prepared.diagnostics["segments"])


def test_explicit_breakpoints_cover_stable_tail_without_caching_secret_or_dynamic_text():
    parts = [{"type": "stable_static", "source": f"rule{i}", "text": f"rule {i}\n"} for i in range(8)]
    parts.append({"type": "dynamic", "source": "task", "text": "Current task\n"})
    gateway = PromptCacheGateway()
    blocks, count = gateway._structured_cache_control_blocks(message=structured(parts), index=0, profile={"maxBreakpoints": 4})
    assert count == 4
    assert next(b for b in blocks if b["text"] == "rule 7\n")["cache_control"]
    assert "cache_control" not in blocks[-1]
    secret_parts = [parts[0], {"type": "stable_static", "source": "secret", "text": "api_key=canary-secret-value\n"}, parts[1]]
    blocks, count = gateway._structured_cache_control_blocks(message=structured(secret_parts), index=0, profile={"maxBreakpoints": 4})
    assert count == 1
    assert "cache_control" not in blocks[1] and "cache_control" not in blocks[2]
