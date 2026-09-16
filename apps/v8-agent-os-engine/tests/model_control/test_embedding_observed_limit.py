from __future__ import annotations

import pytest

from core import llm_factory, model_telemetry
from core.database import DatabaseManager
from core.llm_factory import OpenAICompatibleEmbedding
from core.model_budget_service import ModelBudgetService
from core.provider_circuit import ProviderCircuitService


@pytest.fixture(autouse=True)
def isolated_telemetry(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / 'embedding-limits.db')
    monkeypatch.setattr(model_telemetry, 'db', database)
    monkeypatch.setattr(model_telemetry, 'model_budget_service', ModelBudgetService(database))
    monkeypatch.setattr(model_telemetry, 'provider_circuit_service', ProviderCircuitService(database))
    monkeypatch.setattr(llm_factory.model_control_plane, 'get_config', lambda: {'governance': {}})
    monkeypatch.setattr(llm_factory, '_EMBEDDING_OBSERVED_LIMITS', {})
    return database


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

    def fake_post(_url, *, json, headers, timeout):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResponse(
                400,
                '{"error":{"message":"input at index 0 exceeds maximum token length of 8192, estimated token count: 18000"}}',
            )
        return _FakeResponse(200, payload={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    monkeypatch.setattr("requests.post", fake_post)

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


def test_embedding_rejects_success_response_with_missing_vector_data(monkeypatch, isolated_telemetry):
    monkeypatch.setattr(
        "requests.post",
        lambda *_args, **_kwargs: _FakeResponse(200, payload={"data": None}),
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

    invocation, = isolated_telemetry.list_model_invocations()
    assert invocation["status"] == "failed"
    assert "invalid_response" in invocation["error_message"]
