from __future__ import annotations

import json
from pathlib import Path

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


def test_unconfigured_reasoning_content_is_unverified_thinking_not_text():
    events = _adapter_events({"additional_kwargs": {"reasoning_content": "progress-like text"}})

    assert [event.event_type for event in events] == ["reasoning_delta"]
    assert events[0].delta == "progress-like text"
    assert events[0].diagnostics["reasoningKind"] == "provider_reasoning"
    assert events[0].diagnostics["reasoningSurfaceMode"] == "unverified"
    assert events[0].diagnostics["reasoningUnverified"] is True


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
