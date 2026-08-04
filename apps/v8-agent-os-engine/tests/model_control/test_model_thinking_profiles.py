from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import model_thinking_control
from core.model_thinking_control import (
    resolve_reasoning_effort_control_for_metadata,
    resolve_thinking_control_for_metadata,
)


PROFILE_PATH = Path(__file__).resolve().parents[2] / "core" / "model_catalog" / "model_thinking_profiles.json"
KNOWN_LEVELS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}


def test_model_thinking_profile_catalog_has_stable_unique_contracts() -> None:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profiles = payload["profiles"]
    profile_ids = [str(profile["id"]) for profile in profiles]

    assert payload["schemaVersion"] == 1
    assert len(profile_ids) == len(set(profile_ids))
    assert set(payload["levelOrder"]) == KNOWN_LEVELS

    for profile in profiles:
        assert profile.get("modelPatterns")
        effort = profile.get("effort")
        if not effort:
            continue
        levels = effort.get("levels") or []
        assert levels
        assert set(levels).issubset(KNOWN_LEVELS)
        assert effort.get("defaultLevel") in levels
        assert profile.get("sourceRefs")
        assert all(str(source).startswith("https://") for source in profile["sourceRefs"])


def test_no_think_profiles_never_imply_support_when_explicitly_disabled() -> None:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    disabled_profiles = [
        profile
        for profile in payload["profiles"]
        if (profile.get("noThink") or {}).get("supported") is False
    ]

    assert {profile["id"] for profile in disabled_profiles} >= {
        "anthropic-fable-5",
        "anthropic-mythos-5",
        "gemini-3.5-flash",
    }


def test_profiles_expose_only_native_discrete_levels() -> None:
    def metadata(provider_id, model_id, wire_protocol=""):
        return {
            "provider_id": provider_id,
            "model_id": model_id,
            "model_record": {
                "capabilities": {"chat": True, "reasoning": True},
                "endpointBinding": {"wireProtocol": wire_protocol} if wire_protocol else {},
            },
        }

    deepseek_flash = resolve_reasoning_effort_control_for_metadata(
        metadata("deepseek", "deepseek-v4-flash", "openai.responses")
    )
    deepseek_pro = resolve_reasoning_effort_control_for_metadata(
        metadata("deepseek", "deepseek-v4-pro", "openai.chat_completions")
    )
    opus_5_thinking = resolve_thinking_control_for_metadata(
        metadata("anthropic", "claude-opus-5", "anthropic.messages")
    )
    gemini_25_pro = resolve_reasoning_effort_control_for_metadata(
        metadata("gemini-api", "gemini-2.5-pro", "gemini.generate_content")
    )
    gemini_25_flash = resolve_thinking_control_for_metadata(
        metadata("gemini-api", "gemini-2.5-flash", "gemini.generate_content")
    )
    legacy_claude = resolve_reasoning_effort_control_for_metadata(
        metadata("anthropic", "claude-sonnet-4-5", "anthropic.messages")
    )

    assert deepseek_flash["levels"] == ["auto", "low", "high", "max"]
    assert deepseek_flash["profileId"] == "deepseek-v4-flash"
    assert deepseek_pro["levels"] == ["auto", "high", "max"]
    assert deepseek_pro["profileId"] == "deepseek-v4-pro"
    assert opus_5_thinking["requestStyle"] == "anthropic_thinking_disabled"
    assert gemini_25_pro == {}
    assert gemini_25_flash["requestStyle"] == "gemini_thinking_budget_zero"
    assert legacy_claude == {}


def test_openrouter_reasoning_levels_do_not_leak_to_native_deepseek_r1() -> None:
    model_record = {"capabilities": {"chat": True, "reasoning": True}}

    mediated = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "openrouter",
            "model_id": "deepseek/deepseek-r1",
            "model_record": model_record,
        }
    )
    native = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "deepseek",
            "model_id": "deepseek-r1",
            "model_record": model_record,
        }
    )

    assert mediated["requestStyle"] == "openrouter_reasoning_effort"
    assert native == {}


@pytest.mark.parametrize("source", ["model_thinking_profile", "manual_selection", "manual"])
def test_profile_owned_controls_refresh_capability_facts_but_keep_user_state(monkeypatch, source) -> None:
    profile = {
        "id": "official-test-profile",
        "nativeFamily": "anthropic",
        "effort": {
            "levels": ["low", "high"],
            "defaultLevel": "high",
            "nativeRequestStyle": "anthropic_effort",
            "mandatory": False,
        },
        "noThink": {
            "supported": True,
            "nativeRequestStyle": "anthropic_thinking_disabled",
            "defaultDisabled": False,
        },
        "sourceRefs": ["https://docs.example.test/reasoning"],
    }
    monkeypatch.setattr(model_thinking_control, "_matching_profiles", lambda **_: [profile])
    model_record = {
        "type": "TEXT",
        "capabilities": {"chat": True, "reasoning": True},
        "endpointBinding": {"wireProtocol": "anthropic.messages"},
        "reasoningEffortControl": {
            "supportsReasoningEffort": True,
            "levels": ["none", "max"],
            "defaultLevel": "max",
            "selectedLevel": "high",
            "requestStyle": "stale_fake_style",
            "mandatory": True,
            "budgetByLevel": {"max": 999999},
            "source": source,
            "profileId": "official-test-profile",
        },
        "thinkingControl": {
            "supportsNoThink": True,
            "disabled": True,
            "requestStyle": "stale_fake_style",
            "defaultDisabled": True,
            "source": source,
            "profileId": "official-test-profile",
        },
    }
    metadata = {
        "provider_id": "proxy",
        "model_id": "test-model",
        "model_record": model_record,
    }

    effort = resolve_reasoning_effort_control_for_metadata(metadata)
    thinking = resolve_thinking_control_for_metadata(metadata)

    assert effort["levels"] == ["auto", "low", "high"]
    assert effort["defaultLevel"] == "high"
    assert effort["selectedLevel"] == "high"
    assert effort["requestStyle"] == "anthropic_effort"
    assert effort["mandatory"] is False
    assert effort["budgetByLevel"] == {}
    assert thinking["supportsNoThink"] is True
    assert thinking["disabled"] is True
    assert thinking["requestStyle"] == "anthropic_thinking_disabled"
    assert thinking["defaultDisabled"] is False


def test_manual_control_with_a_different_profile_id_keeps_its_explicit_facts(monkeypatch) -> None:
    profile = {
        "id": "official-test-profile",
        "nativeFamily": "anthropic",
        "effort": {
            "levels": ["low", "high"],
            "defaultLevel": "high",
            "nativeRequestStyle": "anthropic_effort",
        },
        "sourceRefs": ["https://docs.example.test/reasoning"],
    }
    monkeypatch.setattr(model_thinking_control, "_matching_profiles", lambda **_: [profile])

    effort = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "proxy",
            "model_id": "test-model",
            "model_record": {
                "type": "TEXT",
                "capabilities": {"reasoning": True},
                "endpointBinding": {"wireProtocol": "anthropic.messages"},
                "reasoningEffortControl": {
                    "supportsReasoningEffort": True,
                    "levels": ["max"],
                    "defaultLevel": "max",
                    "selectedLevel": "max",
                    "requestStyle": "custom_manual_style",
                    "source": "manual",
                    "profileId": "different-custom-profile",
                },
            },
        }
    )

    assert effort["levels"] == ["auto", "max"]
    assert effort["defaultLevel"] == "max"
    assert effort["selectedLevel"] == "max"
    assert effort["requestStyle"] == "custom_manual_style"


def test_profile_can_remove_materialized_no_think_support(monkeypatch) -> None:
    profile = {
        "id": "official-no-off-switch",
        "nativeFamily": "gemini",
        "noThink": {"supported": False},
        "sourceRefs": ["https://docs.example.test/reasoning"],
    }
    monkeypatch.setattr(model_thinking_control, "_matching_profiles", lambda **_: [profile])

    control = resolve_thinking_control_for_metadata(
        {
            "provider_id": "proxy",
            "model_id": "test-model",
            "model_record": {
                "thinkingControl": {
                    "supportsNoThink": True,
                    "disabled": True,
                    "source": "manual_selection",
                }
            },
        }
    )

    assert control == {}
