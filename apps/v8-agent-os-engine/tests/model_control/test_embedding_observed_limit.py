from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm_factory import OpenAICompatibleEmbedding


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(self.text)


def test_embedding_observed_provider_limit_retries_with_smaller_input(monkeypatch):
    calls: list[dict] = []

    def fake_post(_url, *, json, headers):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResponse(
                400,
                '{"error":{"message":"input at index 0 exceeds maximum token length of 8192, estimated token count: 18000"}}',
            )
        return _FakeResponse(200, payload={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("core.llm_factory.model_budget_service", SimpleNamespace(enforce_or_raise=lambda **_kwargs: None))
    monkeypatch.setattr(
        "core.llm_factory.model_telemetry_service",
        SimpleNamespace(record_aux_model_invocation=lambda **_kwargs: None),
    )

    embedding = OpenAICompatibleEmbedding(
        model_name="fixture-embedding",
        api_key="test",
        base_url="https://example.test/v1",
        max_tokens=32000,
        provider_id="fixture",
    )
    result = embedding.embed_query("A" * 95_000)

    assert result == [0.1, 0.2]
    assert len(calls) == 2
    assert len(calls[0]["input"][0]) > len(calls[1]["input"][0])
    assert len(calls[1]["input"][0]) <= int(8192 * 2.5 * 0.9)


def test_embedding_rejects_success_response_with_missing_vector_data(monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda *_args, **_kwargs: _FakeResponse(200, payload={"data": None}),
    )
    monkeypatch.setattr("core.llm_factory.model_budget_service", SimpleNamespace(enforce_or_raise=lambda **_kwargs: None))
    telemetry: list[dict] = []
    monkeypatch.setattr(
        "core.llm_factory.model_telemetry_service",
        SimpleNamespace(record_aux_model_invocation=lambda **kwargs: telemetry.append(kwargs)),
    )
    embedding = OpenAICompatibleEmbedding(
        model_name="fixture-embedding",
        api_key="test",
        base_url="https://example.test/v1",
        max_tokens=8192,
        provider_id="fixture",
    )

    with pytest.raises(RuntimeError, match="embedding_provider_invalid_response"):
        embedding.embed_documents(["first", "second"])

    assert telemetry[-1]["status"] == "failed"
    assert telemetry[-1]["error_code"] == "invalid_response"
    assert telemetry[-1]["metadata"]["resultCount"] is None
