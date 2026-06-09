from __future__ import annotations

from core import context_window_guard as guard_module
from core.context_window_guard import MIN_PLANNER_CONTEXT_WINDOW_TOKENS, MIN_TEXT_CONTEXT_WINDOW_TOKENS, ContextWindowGuard, validate_text_role_model_window
from core.provider_compatibility import normalize_provider_error


def _install_fake_metadata(monkeypatch, windows: dict[str, tuple[int | None, str]]):
    def fake_metadata(model_ref: str):
        window, model_type = windows.get(model_ref, (None, "TEXT"))
        return {
            "is_found": model_ref in windows,
            "global_context_window": window,
            "model_record": {"type": model_type},
            "capability_class": "embedding" if model_type == "EMBEDDING" else "chat",
        }

    monkeypatch.setattr(guard_module.llm_factory, "get_model_metadata", fake_metadata)


def test_effective_window_uses_minimum_text_generation_participant(monkeypatch):
    _install_fake_metadata(
        monkeypatch,
        {
            "supervisor-256k": (262_144, "TEXT"),
            "summary-1m": (1_000_000, "TEXT"),
        },
    )
    monkeypatch.setattr(guard_module.storage, "get_role_model_id", lambda role: "summary-1m")

    resolved = ContextWindowGuard().resolve(
        target_role="supervisor",
        runtime_kind="chat",
        model_ref="supervisor-256k",
        compression={"use_llm_summary": True, "trigger_ratio": 0.9},
    )

    assert resolved["effectiveContextWindowTokens"] == 262_144
    assert resolved["triggerLimitTokens"] == int(262_144 * 0.9)
    assert {item["role"] for item in resolved["participants"]} == {"supervisor", "summary"}


def test_summary_model_can_lower_effective_window(monkeypatch):
    _install_fake_metadata(
        monkeypatch,
        {
            "supervisor-1m": (1_000_000, "TEXT"),
            "summary-256k": (262_144, "TEXT"),
        },
    )
    monkeypatch.setattr(guard_module.storage, "get_role_model_id", lambda role: "summary-256k")

    resolved = ContextWindowGuard().resolve(
        target_role="supervisor",
        runtime_kind="chat",
        model_ref="supervisor-1m",
        compression={"use_llm_summary": True},
    )

    assert resolved["effectiveContextWindowTokens"] == 262_144
    assert resolved["summaryInputBudgetTokens"] <= 262_144


def test_non_text_models_do_not_enter_context_window_participants(monkeypatch):
    _install_fake_metadata(
        monkeypatch,
        {
            "supervisor-1m": (1_000_000, "TEXT"),
            "summary-1m": (1_000_000, "TEXT"),
            "image-model": (None, "IMAGE"),
            "reranker": (32_000, "RERANK"),
        },
    )
    monkeypatch.setattr(guard_module.storage, "get_role_model_id", lambda role: "summary-1m")

    resolved = ContextWindowGuard().resolve(
        target_role="supervisor",
        runtime_kind="chat",
        model_ref="supervisor-1m",
        compression={"use_llm_summary": True},
        extra_participants=[
            {"role": "media", "runtimeKind": "creative_media", "modelRef": "image-model"},
            {"role": "reranker", "runtimeKind": "memory", "modelRef": "reranker"},
        ],
    )

    assert [item["modelRef"] for item in resolved["participants"]] == ["supervisor-1m", "summary-1m"]


def test_missing_and_below_min_windows_are_reported(monkeypatch):
    _install_fake_metadata(
        monkeypatch,
        {
            "small-chat": (128_000, "TEXT"),
            "summary-missing": (None, "TEXT"),
        },
    )
    monkeypatch.setattr(guard_module.storage, "get_role_model_id", lambda role: "summary-missing")

    resolved = ContextWindowGuard().resolve(
        target_role="supervisor",
        runtime_kind="chat",
        model_ref="small-chat",
        compression={"use_llm_summary": True, "default_context_window_tokens": 32_000},
    )

    assert resolved["effectiveContextWindowTokens"] == 32_000
    assert {item["reason"] for item in resolved["warnings"]} == {"below_min_context_window", "missing_context_window"}
    validation = validate_text_role_model_window("supervisor", "small-chat")
    assert validation["ok"] is False
    assert validation["minimumRequiredContextWindowTokens"] == MIN_TEXT_CONTEXT_WINDOW_TOKENS


def test_planner_role_uses_smaller_context_window_binding_floor(monkeypatch):
    _install_fake_metadata(
        monkeypatch,
        {
            "planner-64k": (65_536, "TEXT"),
            "planner-16k": (16_384, "TEXT"),
        },
    )

    accepted = validate_text_role_model_window("planner", "planner-64k")
    rejected = validate_text_role_model_window("planner", "planner-16k")

    assert accepted["ok"] is True
    assert rejected["ok"] is False
    assert rejected["minimumRequiredContextWindowTokens"] == MIN_PLANNER_CONTEXT_WINDOW_TOKENS


def test_provider_context_overflow_is_normalized():
    normalized = normalize_provider_error(Exception("context_length_exceeded: too many tokens for maximum context"))
    assert normalized["code"] == "context_window_overflow"
    assert normalized["retryable"] is False
