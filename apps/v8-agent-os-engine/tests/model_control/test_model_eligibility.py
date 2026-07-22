from core.model_eligibility import evaluate_model_eligibility, model_category


def test_text_model_requires_both_context_and_output_facts() -> None:
    result = evaluate_model_eligibility(
        {
            "type": "MULTIMODAL",
            "contextWindow": 262_144,
            "maxTokens": None,
            "capabilities": {"chat": True, "vision": True},
        }
    )

    assert result["status"] == "needs_facts"
    assert result["selectable"] is False
    assert result["requiredFacts"] == ["maxTokens"]


def test_user_confirmed_model_facts_remain_visible_in_eligibility() -> None:
    provenance = {
        "contextWindow": {"source": "user_confirmed", "confidence": "authoritative"},
        "maxTokens": {"source": "user_confirmed", "confidence": "authoritative"},
    }
    result = evaluate_model_eligibility(
        {
            "type": "TEXT",
            "contextWindow": 400_000,
            "maxTokens": 32_000,
            "capabilities": {"chat": True},
            "factProvenance": provenance,
        }
    )

    assert result["status"] == "ready"
    assert result["selectable"] is True
    assert result["factProvenance"] == provenance


def test_media_model_does_not_inherit_text_window_requirements() -> None:
    result = evaluate_model_eligibility({"type": "VIDEO", "capabilityClass": "media_generation"})

    assert result["status"] == "ready"
    assert result["requiredFacts"] == []


def test_inventory_category_keeps_vision_separate_from_text() -> None:
    assert model_category({"type": "TEXT", "capabilities": {"chat": True}}) == "text"
    assert model_category({"type": "MULTIMODAL", "capabilities": {"vision": True}}) == "vision"


def test_unreviewed_limits_warn_without_disabling_model() -> None:
    result = evaluate_model_eligibility(
        {"type": "TEXT", "contextWindow": 262_144, "maxTokens": 8_192}
    )

    assert result["selectable"] is True
    assert result["shortLabel"] == "可用 · 参数待复核"
    assert [item["code"] for item in result["warnings"]] == ["model_facts_unverified"]
