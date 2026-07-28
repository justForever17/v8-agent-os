from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
from core.reasoning_surface_contract import resolve_reasoning_surface_for_metadata
from erc.canonical_model_events import LangChainCanonicalModelEventAdapter


def _adapter_events(payload, *, surface=None):
    adapter = LangChainCanonicalModelEventAdapter()
    return adapter.normalize_chat_model_stream(
        {
            "event": "on_chat_model_stream",
            "run_id": "model-run",
            "data": {"chunk": payload},
        },
        text_snapshots={},
        reasoning_snapshots={},
        reasoning_surface=surface,
    )


def test_anthropic_typed_thinking_block_is_raw_thinking():
    events = _adapter_events(
        {"content": [{"type": "thinking", "thinking": "typed thought"}]},
        surface={
            "mode": "typed_thinking",
            "trust": "official",
            "requestStyle": "anthropic_thinking",
            "responseFields": ["content[type=thinking]"],
            "displayKind": "raw_thinking",
        },
    )

    assert [event.event_type for event in events] == ["reasoning_delta"]
    assert events[0].delta == "typed thought"
    assert events[0].diagnostics["reasoningKind"] == "raw_thinking"


def test_gemini_adapter_fixture_streams_only_the_public_thought_summary():
    fixture_path = Path(__file__).resolve().parents[2] / "core" / "model_catalog" / "reasoning_surface_fixtures" / "gemini_thought_summary.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    for model_id in fixture["modelIds"]:
        surface = resolve_reasoning_surface_for_metadata(
            {"provider_id": fixture["providerId"], "model_id": model_id}
        )
        assert surface["mode"] == "reasoning_summary"
        assert surface["trust"] == "adapter_verified"
        assert surface["requestStyle"] == "gemini_include_thoughts"
        assert surface["responseFields"] == ["content[type=thinking]"]

        events = _adapter_events(fixture["streamDelta"], surface=surface)
        assert [event.event_type for event in events] == ["reasoning_delta"]
        assert events[0].delta == "先核对约束，再组织答案。"
        assert events[0].diagnostics["reasoningKind"] == "summary"
        assert "opaque-thought-signature" not in str(events)


def test_gemini_langchain_adapter_projects_thought_part_into_canonical_summary():
    google_types = pytest.importorskip("google.genai.types")
    gemini_chat_models = pytest.importorskip("langchain_google_genai.chat_models")
    summary = "Inspect the constraints before answering."
    message = gemini_chat_models._parse_response_candidate(
        google_types.Candidate(
            content=google_types.Content(
                role="model",
                parts=[google_types.Part(text=summary, thought=True)],
            )
        ),
        streaming=True,
        model_name_for_content="gemini-3.1-pro-preview",
    )
    surface = resolve_reasoning_surface_for_metadata(
        {"provider_id": "gemini-api", "model_id": "gemini-3.1-pro-preview"}
    )

    assert message.content == [{"type": "thinking", "thinking": summary}]
    events = _adapter_events(message, surface=surface)
    assert [event.event_type for event in events] == ["reasoning_delta"]
    assert events[0].delta == summary
    assert events[0].diagnostics["reasoningKind"] == "summary"


def test_gemini_adapter_fixture_terminal_output_keeps_text_and_summary_separate():
    fixture_path = Path(__file__).resolve().parents[2] / "core" / "model_catalog" / "reasoning_surface_fixtures" / "gemini_thought_summary.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    surface = resolve_reasoning_surface_for_metadata(
        {"provider_id": fixture["providerId"], "model_id": fixture["modelIds"][0]}
    )

    events = LangChainCanonicalModelEventAdapter().normalize_chat_model_end(
        {
            "event": "on_chat_model_end",
            "run_id": "gemini-terminal-run",
            "data": {"output": fixture["terminalOutput"]},
        },
        text_snapshots={},
        reasoning_snapshots={},
        reasoning_surface=surface,
    )

    assert [event.event_type for event in events] == ["text_delta", "reasoning_delta"]
    assert events[0].delta == "已完成。"
    assert events[1].delta == "先核对约束，再组织答案。"
    assert events[1].diagnostics["matchedField"] == "content[type=thinking]"
    assert "opaque-thought-signature" not in str(events)


def test_unknown_gemini_model_keeps_thought_blocks_fail_closed():
    surface = resolve_reasoning_surface_for_metadata(
        {"provider_id": "gemini-api", "model_id": "gemini-unverified-future-model"}
    )
    events = _adapter_events(
        {"content": [{"type": "thinking", "thinking": "must stay hidden"}]},
        surface=surface,
    )

    assert surface["mode"] == "hidden"
    assert [event.event_type for event in events] == ["reasoning_suppressed"]


def test_openai_reasoning_summary_is_summary_kind():
    events = _adapter_events(
        {"reasoning": {"summary": "short summary"}},
        surface={
            "mode": "reasoning_summary",
            "trust": "official",
            "requestStyle": "openai_reasoning",
            "responseFields": ["reasoning.summary"],
            "displayKind": "summary",
        },
    )

    assert [event.event_type for event in events] == ["reasoning_delta"]
    assert events[0].delta == "short summary"
    assert events[0].diagnostics["reasoningKind"] == "summary"


def test_openai_typed_summary_never_includes_sibling_raw_reasoning():
    events = _adapter_events(
        {
            "content": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "Public summary."}],
                    "reasoning": "PRIVATE RAW COT",
                }
            ]
        },
        surface={
            "mode": "reasoning_summary",
            "trust": "official",
            "requestStyle": "openai_reasoning",
            "responseFields": ["reasoning.summary"],
            "displayKind": "summary",
        },
    )

    assert [event.event_type for event in events] == ["reasoning_delta"]
    assert events[0].delta == "Public summary."
    assert "PRIVATE RAW COT" not in events[0].delta


def test_provider_reasoning_block_with_visible_summary_becomes_canonical_reasoning():
    events = _adapter_events(
        {
            "content": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "先核对约束，再执行。"}],
                    "encrypted_content": "opaque-provider-state",
                }
            ]
        },
        surface={
            "mode": "reasoning_summary",
            "trust": "official",
            "requestStyle": "openai_reasoning",
            "responseFields": ["reasoning.summary"],
            "displayKind": "summary",
        },
    )

    assert [event.event_type for event in events] == ["reasoning_delta"]
    assert events[0].delta == "先核对约束，再执行。"
    assert "opaque-provider-state" not in str(events[0].diagnostics)


def test_provider_reasoning_block_with_empty_summary_is_not_fabricated_as_thinking():
    events = _adapter_events(
        {
            "content": [
                {
                    "type": "reasoning",
                    "summary": [],
                    "encrypted_content": "opaque-provider-state",
                }
            ]
        },
        surface={
            "mode": "reasoning_summary",
            "trust": "official",
            "requestStyle": "openai_reasoning",
            "responseFields": ["reasoning.summary"],
            "displayKind": "summary",
        },
    )

    assert events == []


def test_terminal_reasoning_summary_with_text_is_visible_when_not_streamed():
    adapter = LangChainCanonicalModelEventAdapter()
    events = adapter.normalize_chat_model_end(
        {
            "event": "on_chat_model_end",
            "run_id": "model-terminal-summary",
            "data": {
                "output": {
                    "content": [
                        {
                            "type": "reasoning",
                            "summary": [{"type": "summary_text", "text": "先检查约束，再给出结果。"}],
                            "encrypted_content": "opaque-terminal-state",
                        },
                        {"type": "text", "text": "计算结果为 42。"},
                    ]
                }
            },
        },
        text_snapshots={},
        reasoning_snapshots={},
        reasoning_surface={
            "mode": "reasoning_summary",
            "trust": "official",
            "requestStyle": "openai_reasoning",
            "responseFields": ["reasoning.summary"],
            "displayKind": "summary",
        },
    )

    assert [event.event_type for event in events] == ["text_delta", "reasoning_delta"]
    assert events[1].delta == "先检查约束，再给出结果。"
    assert "opaque-terminal-state" not in str(events[1].diagnostics)


def test_unconfigured_reasoning_content_is_suppressed_from_human_reasoning():
    events = _adapter_events({"additional_kwargs": {"reasoning_content": "progress-like text"}})

    assert [event.event_type for event in events] == ["reasoning_suppressed"]
    assert events[0].delta == "progress-like text"
    assert events[0].diagnostics["reasoningKind"] == "hidden"
    assert events[0].diagnostics["reasoningSurfaceMode"] == "hidden"


def test_openai_raw_reasoning_fields_do_not_impersonate_an_official_summary():
    surface = resolve_reasoning_surface_for_metadata(
        {"provider_id": "openai", "model_id": "gpt-5.5"}
    )
    assert surface["responseFields"] == ["reasoning.summary"]

    for field in ("reasoning", "reasoning_content"):
        events = _adapter_events(
            {"additional_kwargs": {field: "PRIVATE RAW COT"}},
            surface=surface,
        )
        assert [event.event_type for event in events] == ["reasoning_suppressed"]
        assert all(event.event_type != "reasoning_delta" for event in events)


def test_suppressed_raw_reasoning_cannot_prefix_a_later_public_summary():
    adapter = LangChainCanonicalModelEventAdapter()
    reasoning_snapshots = {}
    surface = {
        "mode": "reasoning_summary",
        "trust": "official",
        "requestStyle": "openai_reasoning",
        "responseFields": ["reasoning.summary"],
        "displayKind": "summary",
    }
    raw_events = adapter.normalize_chat_model_stream(
        {
            "event": "on_chat_model_stream",
            "run_id": "same-model-run",
            "data": {"chunk": {"additional_kwargs": {"reasoning_content": "PRIVATE RAW COT"}}},
        },
        text_snapshots={},
        reasoning_snapshots=reasoning_snapshots,
        reasoning_surface=surface,
    )
    summary_events = adapter.normalize_chat_model_stream(
        {
            "event": "on_chat_model_stream",
            "run_id": "same-model-run",
            "data": {
                "chunk": {
                    "reasoning": {
                        "summary": [{"type": "summary_text", "text": "Public summary."}]
                    }
                }
            },
        },
        text_snapshots={},
        reasoning_snapshots=reasoning_snapshots,
        reasoning_surface=surface,
    )

    assert [event.event_type for event in raw_events] == ["reasoning_suppressed"]
    assert [event.event_type for event in summary_events] == ["reasoning_delta"]
    assert summary_events[0].delta == "Public summary."
    assert summary_events[0].snapshot == "Public summary."
    assert reasoning_snapshots["same-model-run"] == "Public summary."


def test_explicit_user_disabled_reasoning_stays_suppressed():
    events = _adapter_events(
        {"additional_kwargs": {"reasoning_content": "hidden by user"}},
        surface={
            "mode": "hidden",
            "trust": "unknown",
            "requestStyle": "none",
            "displayKind": "hidden",
            "disabled": True,
        },
    )

    assert [event.event_type for event in events] == ["reasoning_suppressed"]
    assert events[0].diagnostics["reasoningKind"] == "hidden"
    assert events[0].diagnostics["reasoningSurfaceMode"] == "hidden"


def test_plain_content_still_becomes_text_delta():
    events = _adapter_events({"content": "hello user"})

    assert [event.event_type for event in events] == ["text_delta"]
    assert events[0].delta == "hello user"


def test_mimo_doubao_fixture_models_have_adapter_verified_reasoning_surface():
    fixture_path = Path(__file__).resolve().parents[2] / "core" / "model_catalog" / "reasoning_surface_fixtures" / "mimo_doubao_streaming.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    for item in payload["fixtures"]:
        surface = resolve_reasoning_surface_for_metadata(
            {
                "provider_id": item["providerId"],
                "model_id": item["modelId"],
            }
        )
        assert surface["mode"] == "provider_reasoning"
        assert surface["trust"] == "adapter_verified"

        events = _adapter_events(item["rawDelta"], surface=surface)
        assert [event.event_type for event in events] == ["reasoning_delta"]
        assert events[0].diagnostics["reasoningKind"] == "provider_reasoning"
        assert events[0].delta


def test_stale_hidden_config_is_replaced_by_verified_mimo_contract():
    surface = resolve_reasoning_surface_for_metadata(
        {
            "provider_id": "xiaomi-mimo-tokenplan",
            "model_id": "mimo-v2.5-pro",
            "model_record": {
                "reasoningSurface": {
                    "mode": "hidden",
                    "trust": "unknown",
                    "requestStyle": "none",
                    "displayKind": "hidden",
                    "source": "auto_hidden_legacy",
                }
            },
        }
    )

    assert surface["mode"] == "provider_reasoning"
    assert surface["trust"] == "adapter_verified"


def test_doubao_dot_alias_resolves_to_verified_contract():
    surface = resolve_reasoning_surface_for_metadata(
        {
            "provider_id": "volcengine-ark",
            "model_id": "doubao-seed-2.0-pro",
        }
    )

    assert surface["mode"] == "provider_reasoning"
    assert surface["trust"] == "adapter_verified"


def test_minimax_m3_reasoning_details_are_trusted_provider_reasoning():
    surface = resolve_reasoning_surface_for_metadata(
        {
            "provider_id": "minimax",
            "model_id": "MiniMax-M3",
        }
    )

    assert surface["mode"] == "provider_reasoning"
    assert surface["trust"] == "official"
    assert "reasoning_details" in surface["responseFields"]

    events = _adapter_events(
        {
            "additional_kwargs": {
                "reasoning_details": [
                    {
                        "type": "reasoning.text",
                        "format": "MiniMax-response-v1",
                        "text": "I should inspect the task before calling tools.",
                    }
                ]
            }
        },
        surface=surface,
    )

    assert [event.event_type for event in events] == ["reasoning_delta"]
    assert events[0].delta == "I should inspect the task before calling tools."
    assert events[0].diagnostics["reasoningKind"] == "provider_reasoning"


def test_minimax_raw_response_reaches_canonical_reasoning_event():
    message = V8OpenAICompatibleChatModel(
        model="MiniMax-M3",
        api_key="test-key",
    )._create_chat_result(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Visible answer.",
                        "reasoning_details": [
                            {
                                "type": "reasoning.text",
                                "format": "MiniMax-response-v1",
                                "text": "Inspect the request before answering.",
                            }
                        ],
                    },
                    "finish_reason": "stop",
                }
            ]
        }
    ).generations[0].message
    surface = resolve_reasoning_surface_for_metadata(
        {
            "provider_id": "minimax",
            "model_id": "MiniMax-M3",
        }
    )

    events = LangChainCanonicalModelEventAdapter().normalize_chat_model_end(
        {
            "event": "on_chat_model_end",
            "run_id": "minimax-model-run",
            "data": {"output": message},
        },
        text_snapshots={},
        reasoning_snapshots={},
        reasoning_surface=surface,
    )

    assert [event.event_type for event in events] == ["text_delta", "reasoning_delta"]
    assert events[0].delta == "Visible answer."
    assert events[1].delta == "Inspect the request before answering."
    assert events[1].diagnostics["matchedField"] == "additional_kwargs.reasoning_details"


def test_minimax_m3_inline_think_tags_are_trusted_provider_reasoning_not_text():
    surface = resolve_reasoning_surface_for_metadata(
        {
            "provider_id": "minimax",
            "model_id": "MiniMax-M3",
        }
    )

    assert "content[inline_think]" in surface["responseFields"]

    events = _adapter_events(
        {"content": "<think>I should inspect the task.</think>\nVisible answer."},
        surface=surface,
    )

    assert [event.event_type for event in events] == ["text_delta", "reasoning_delta"]
    assert events[0].delta == "Visible answer."
    assert events[1].delta == "I should inspect the task."
    assert events[1].diagnostics["reasoningKind"] == "provider_reasoning"
    assert events[1].diagnostics["matchedField"] == "content[inline_think]"


def test_explicit_user_disabled_hidden_is_not_overridden_by_builtin_contract():
    surface = resolve_reasoning_surface_for_metadata(
        {
            "provider_id": "xiaomi-mimo-tokenplan",
            "model_id": "mimo-v2.5-pro",
            "model_record": {
                "reasoningSurface": {
                    "mode": "hidden",
                    "trust": "unknown",
                    "requestStyle": "none",
                    "displayKind": "hidden",
                    "disabled": True,
                }
            },
        }
    )

    assert surface["mode"] == "hidden"
    assert surface["disabled"] is True
