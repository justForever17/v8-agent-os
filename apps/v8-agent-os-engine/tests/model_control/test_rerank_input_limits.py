from __future__ import annotations

from core import llm_factory, model_telemetry
from core.database import DatabaseManager
from core.llm_factory import EmbeddingSimilarityReranker, LLMFactory, RestReranker, _safe_log_text
from core.model_budget_service import ModelBudgetService
from core.provider_circuit import ProviderCircuitService


class _Response:
    def __init__(self, status_code: int, text: str = "", payload: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_reranker_retries_query_too_long_with_observed_query_limit(tmp_path, monkeypatch) -> None:
    database = DatabaseManager(tmp_path / 'rerank-limits.db')
    monkeypatch.setattr(model_telemetry, 'db', database)
    monkeypatch.setattr(model_telemetry, 'model_budget_service', ModelBudgetService(database))
    monkeypatch.setattr(model_telemetry, 'provider_circuit_service', ProviderCircuitService(database))
    monkeypatch.setattr(llm_factory.model_control_plane, 'get_config', lambda: {'governance': {}})
    monkeypatch.setattr(llm_factory, '_RERANK_OBSERVED_QUERY_LIMITS', {})
    reranker = RestReranker(
        model_name="rerank-fixture",
        api_key="key",
        base_url="https://example.test/v1",
        max_tokens=32000,
        provider_id="fixture-provider",
    )
    calls: list[dict] = []

    def fake_post(endpoint, **kwargs):  # noqa: ANN001
        calls.append(dict(kwargs['json']))
        if len(calls) == 1:
            return _Response(400, '{"message":"Query is too long. Please provide a shorter query."}')
        return _Response(
            200,
            payload={
                "results": [
                    {
                        "index": 0,
                        "relevance_score": 0.9,
                    }
                ]
            },
        )

    monkeypatch.setattr('requests.post', fake_post)
    result = reranker.rerank("这是一段很长的查询" * 2000, ["doc one", "doc two"], top_k=1)

    assert result[0]["index"] == 0
    assert len(calls) == 2
    assert len(calls[1]["query"]) < len(calls[0]["query"])
    assert len(database.list_model_invocations()) == 2


def test_reranker_log_text_is_safe_for_gbk_stderr(monkeypatch) -> None:
    class _GbkStderr:
        encoding = "gbk"

    monkeypatch.setattr("core.llm_factory.sys.stderr", _GbkStderr())

    text = _safe_log_text("provider said ⚠️ retry with emoji ✨")

    text.encode("gbk")
    assert "\\u26a0" in text or "\\ufe0f" in text


class _FakeEmbedding:
    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vectors[text] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.vectors[text]


def test_embedding_similarity_reranker_orders_by_cosine_similarity() -> None:
    embedding = _FakeEmbedding(
        {
            "cat": [1.0, 0.0],
            "dog document": [0.0, 1.0],
            "mixed document": [0.5, 0.5],
            "cat document": [1.0, 0.0],
        }
    )
    reranker = EmbeddingSimilarityReranker(embedding_model=embedding, model_name="embed-fixture")

    result = reranker.rerank("cat", ["dog document", "mixed document", "cat document"], top_k=2)

    assert [row["index"] for row in result] == [2, 1]
    assert embedding.calls == [["cat", "dog document", "mixed document", "cat document"]]


def test_embedding_similarity_reranker_keeps_input_order_for_ties() -> None:
    embedding = _FakeEmbedding({"query": [1.0], "first": [1.0], "second": [1.0]})
    reranker = EmbeddingSimilarityReranker(embedding_model=embedding, model_name="embed-fixture")

    result = reranker.rerank("query", ["first", "second"], top_k=2)

    assert [row["index"] for row in result] == [0, 1]


def test_factory_uses_embedding_similarity_adapter_for_embedding_models(monkeypatch) -> None:
    metadata = {
        "is_found": True,
        "model_id": "embedding-fixture",
        "model_record": {"type": "EMBEDDING"},
        "capability_class": "embedding",
        "capabilities": {"embedding": True},
        "api_key": "key",
        "base_url": "https://example.test/v1",
        "global_context_window": 8192,
        "provider_id": "fixture-provider",
        "provider_name": "Fixture Provider",
    }

    monkeypatch.setattr(
        LLMFactory,
        "_resolve_model_metadata",
        classmethod(lambda cls, model_ref: metadata),
    )

    reranker = LLMFactory.create_reranker_model("fixture-provider::embedding-fixture")

    assert isinstance(reranker, EmbeddingSimilarityReranker)
    assert reranker.model_name == "embedding-fixture"
    assert reranker.capability_class == "embedding"
