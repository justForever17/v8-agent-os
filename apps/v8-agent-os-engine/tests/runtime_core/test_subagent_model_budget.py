from graph.agent_factories import DEFAULT_SUBAGENT_MAX_OUTPUT_TOKENS, subagent_model_kwargs


def test_subagent_model_budget_uses_configured_model_limit(monkeypatch):
    monkeypatch.setattr(
        "graph.agent_factories.llm_factory.get_model_max_output_tokens",
        lambda _model_id: 131072,
    )

    assert subagent_model_kwargs("provider::long-output-model") == {"max_tokens": 131072}


def test_subagent_model_budget_defaults_to_32k_when_model_limit_is_missing(monkeypatch):
    monkeypatch.setattr(
        "graph.agent_factories.llm_factory.get_model_max_output_tokens",
        lambda _model_id: None,
    )

    assert subagent_model_kwargs("provider::unknown-output-model") == {
        "max_tokens": DEFAULT_SUBAGENT_MAX_OUTPUT_TOKENS
    }
