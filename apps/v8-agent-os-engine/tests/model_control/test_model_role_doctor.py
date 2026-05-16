from core.model_role_doctor import diagnose_model_role


def test_text_generation_model_requires_long_context_window():
    result = diagnose_model_role(
        {
            "type": "TEXT",
            "capabilityClass": "chat_general",
            "contextWindow": 128000,
            "maxTokens": 4096,
            "capabilities": {"chat": True, "streaming": True},
        },
        role="supervisor",
    )

    assert result["blocking"] is True
    assert result["issues"][0]["code"] == "below_min_context_window"


def test_embedding_uses_context_window_as_input_limit_without_max_tokens():
    result = diagnose_model_role(
        {
            "type": "EMBEDDING",
            "capabilityClass": "embedding",
            "contextWindow": 8192,
            "maxTokens": None,
            "observedInputTokenLimit": 4096,
            "capabilities": {"embedding": True},
        },
        role="memory_embedding",
    )

    assert result["blocking"] is False
    assert result["effectiveInputLimit"] == 4096
    assert result["warnings"][0]["code"] == "observed_input_limit_lower_than_config"


def test_media_model_is_excluded_from_text_window_governance():
    result = diagnose_model_role(
        {
            "type": "VIDEO",
            "capabilityClass": "media_generation",
            "contextWindow": None,
            "maxTokens": None,
            "capabilities": {"video": True},
        },
        role="creative_media",
    )

    assert result["ok"] is True
    assert result["modelKind"] == "media"
    assert result["issues"] == []
