from __future__ import annotations

from core.model_role_doctor import diagnose_model_role


def test_model_role_doctor_warns_without_blocking_usable_text_context():
    diagnostic = diagnose_model_role(
        {
            "type": "TEXT",
            "contextWindow": 128000,
            "maxTokens": 8192,
            "capabilities": {"streaming": True},
        },
        role="supervisor",
    )

    assert diagnostic["blocking"] is False
    assert diagnostic["issues"] == []
    assert diagnostic["warnings"][0]["code"] == "context_budget_below_recommended"


def test_model_role_doctor_treats_retrieval_context_as_input_window():
    embedding = diagnose_model_role(
        {
            "type": "EMBEDDING",
            "contextWindow": 32000,
            "maxTokens": None,
            "observedInputTokenLimit": 8192,
            "capabilities": {"embedding": True},
        },
        role="memory_embedding",
    )
    rerank = diagnose_model_role(
        {
            "type": "RERANK",
            "contextWindow": 8192,
            "observedRerankQueryTokenLimit": 1024,
            "capabilities": {"rerank": True},
        },
        role="memory_rerank",
    )

    assert embedding["blocking"] is False
    assert embedding["effectiveInputLimit"] == 8192
    assert embedding["warnings"][0]["code"] == "observed_input_limit_lower_than_config"
    assert rerank["blocking"] is False
    assert rerank["warnings"][0]["code"] == "observed_rerank_query_limit"


def test_model_role_doctor_excludes_media_from_text_window_rules():
    diagnostic = diagnose_model_role(
        {
            "type": "VIDEO",
            "contextWindow": None,
            "maxTokens": None,
        },
        role="creative_media",
    )

    assert diagnostic["modelKind"] == "media"
    assert diagnostic["blocking"] is False
