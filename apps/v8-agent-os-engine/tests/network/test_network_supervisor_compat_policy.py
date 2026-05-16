from __future__ import annotations

import pytest

from core.llm_factory import RestReranker
from runtimes.network_supervisor.compat_ingress_filter import classify_compat_turn
from runtimes.network_supervisor.compat_model_budget import resolve_compat_model_budget


def test_claude_code_tool_result_user_message_is_not_human_turn():
    payload = {
        "model": "v8",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "file content\n<system-reminder>hidden reminder</system-reminder>",
                    }
                ],
            }
        ],
        "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
    }

    result = classify_compat_turn("anthropic", payload, raw_ref="raw:test")

    assert result.request_kind == "tool_result_resume"
    assert result.latest_human_utterance == ""
    assert result.suppress_passive_rag is True
    assert result.external_tools_primary is True


def test_claude_code_human_text_with_external_tools_keeps_rag_query():
    payload = {
        "model": "v8",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "old result"},
                    {"type": "text", "text": "请根据之前的实现继续补 network runtime compat。"},
                ],
            }
        ],
        "tools": [{"name": "Read", "input_schema": {"type": "object"}}],
    }

    result = classify_compat_turn("anthropic", payload, raw_ref="raw:test")

    assert result.request_kind == "human_turn"
    assert result.latest_human_utterance == "请根据之前的实现继续补 network runtime compat。"
    assert result.suppress_passive_rag is False
    assert result.external_tools_primary is True
    assert result.suppress_extensions_prefilter is True


def test_claude_code_side_question_keeps_text_after_system_reminder():
    payload = {
        "model": "v8",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "<system-reminder>This is a side question from the user.</system-reminder>\n\n这个设计会影响 RAG 吗？",
                    }
                ],
            }
        ],
    }

    result = classify_compat_turn("anthropic", payload, raw_ref="raw:test")

    assert result.request_kind == "human_turn"
    assert result.latest_human_utterance == "这个设计会影响 RAG 吗？"
    assert result.suppress_passive_rag is False


def test_compat_budget_uses_configured_context_window(monkeypatch):
    def fake_resolve(role):
        assert role == "supervisor"
        return {
            "bindingState": "explicit",
            "resolvedModelRef": "provider/model",
            "resolvedModelId": "model",
            "resolvedModel": {"contextWindow": 4096},
        }

    monkeypatch.setattr(
        "runtimes.network_supervisor.compat_model_budget.model_control_plane.resolve_model_for_role",
        fake_resolve,
    )

    budget = resolve_compat_model_budget("v8", aliases=["v8"])

    assert budget.requested_alias == "v8"
    assert budget.execution_model_ref == "provider/model"
    assert budget.context_window_tokens == 4096
    assert budget.max_external_message_tokens <= 4096
    assert budget.max_external_tools_payload_tokens <= 4096


def test_compat_budget_missing_context_window_fails_fast(monkeypatch):
    monkeypatch.setattr(
        "runtimes.network_supervisor.compat_model_budget.model_control_plane.resolve_model_for_role",
        lambda role: {
            "bindingState": "explicit",
            "resolvedModelRef": "provider/model",
            "resolvedModelId": "model",
            "resolvedModel": {},
        },
    )
    monkeypatch.setattr(
        "core.llm_factory.LLMFactory._resolve_model_metadata",
        lambda model_ref: {},
    )

    with pytest.raises(ValueError, match="missing_context_window"):
        resolve_compat_model_budget("v8", aliases=["v8"])


def test_reranker_requires_context_window():
    reranker = RestReranker(
        model_name="rerank-model",
        api_key="test",
        base_url="https://example.invalid/v1",
        max_tokens=None,
    )

    with pytest.raises(ValueError, match="missing_context_window"):
        reranker._prepare_payload_documents("query", ["doc"])
