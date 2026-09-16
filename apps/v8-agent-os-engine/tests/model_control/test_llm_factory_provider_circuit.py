from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.llm_factory import EmbeddingSimilarityReranker, OpenAICompatibleEmbedding, RestReranker
from core.provider_circuit import ProviderCircuitOpen


class Response:
    def __init__(self, status_code, text='', payload=None):
        self.status_code, self.text, self._payload = status_code, text, payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        raise RuntimeError(self.text)


class CircuitSpy:
    def __init__(self, reject=False):
        self.reject, self.permits, self.finishes, self.releases = reject, [], [], []

    def acquire(self, provider_id, **kwargs):
        if self.reject:
            raise ProviderCircuitOpen(provider_id, retry_after_seconds=9, reason='probe_in_flight')
        permit = {'permitId': f'p{len(self.permits)}', 'providerId': provider_id, 'generation': 1, 'probe': False}
        self.permits.append((provider_id, kwargs))
        return permit

    def finish(self, permit, **kwargs):
        self.finishes.append((permit, kwargs))
        return True

    def release(self, permit, **kwargs):
        self.releases.append((permit, kwargs))
        return True


def _embedding(monkeypatch):
    monkeypatch.setattr('core.llm_factory.model_budget_service', SimpleNamespace(enforce_or_raise=lambda **_: None))
    monkeypatch.setattr('core.llm_factory.model_control_plane.get_config', lambda: {'governance': {}})
    monkeypatch.setattr('core.llm_factory.model_telemetry_service', SimpleNamespace(record_aux_model_invocation=lambda **_: None))
    return OpenAICompatibleEmbedding('embed', 'key', 'https://example.test/v1', max_tokens=8192, provider_id='provider')


def test_embedding_each_http_request_has_one_admission_and_limit_retry_is_separate(monkeypatch):
    embedding = _embedding(monkeypatch)
    circuit = CircuitSpy()
    monkeypatch.setattr('core.llm_factory.provider_circuit_service', circuit)
    responses = [Response(400, 'input at index 0 exceeds maximum token length of 4096'), Response(200, payload={'data': [{'index': 0, 'embedding': [1.0]}]})]
    monkeypatch.setattr('requests.post', lambda *_args, **_kwargs: responses.pop(0))
    assert embedding.embed_query('x' * 95000) == [1.0]
    assert len(circuit.permits) == 2 and len(circuit.finishes) == 2
    assert circuit.finishes[0][1]['success'] is False
    assert circuit.finishes[0][1]['error_code'] == 'invalid_request'
    assert circuit.finishes[1][1]['success'] is True


def test_embedding_circuit_rejection_prevents_http_request(monkeypatch):
    embedding = _embedding(monkeypatch)
    monkeypatch.setattr('core.llm_factory.provider_circuit_service', CircuitSpy(reject=True))
    called = []
    monkeypatch.setattr('requests.post', lambda *_args, **_kwargs: called.append(True))
    with pytest.raises(ProviderCircuitOpen):
        embedding.embed_query('hello')
    assert called == []


def test_embedding_transport_exception_finishes_failure_once(monkeypatch):
    embedding = _embedding(monkeypatch)
    circuit = CircuitSpy()
    monkeypatch.setattr('core.llm_factory.provider_circuit_service', circuit)
    monkeypatch.setattr('requests.post', lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('offline')))
    with pytest.raises(OSError):
        embedding.embed_query('hello')
    assert len(circuit.permits) == 1 and len(circuit.finishes) == 1
    assert circuit.finishes[0][1] == {'success': False, 'config': {'governance': {}}, 'error_code': 'provider_unavailable'}


def test_embedding_cancellation_finishes_unknown_transport_attempt(monkeypatch):
    embedding = _embedding(monkeypatch)
    circuit = CircuitSpy()
    monkeypatch.setattr('core.llm_factory.provider_circuit_service', circuit)
    monkeypatch.setattr('requests.post', lambda *_args, **_kwargs: (_ for _ in ()).throw(asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        embedding.embed_query('hello')
    assert len(circuit.finishes) == 1 and circuit.finishes[0][1]['success'] is False


def test_reranker_each_endpoint_and_input_retry_are_individual_admissions(monkeypatch):
    reranker = RestReranker('rerank', 'key', 'https://example.test/v1', max_tokens=8192, provider_id='provider')
    circuit = CircuitSpy()
    monkeypatch.setattr('core.llm_factory.provider_circuit_service', circuit)
    monkeypatch.setattr('core.llm_factory.model_budget_service', SimpleNamespace(enforce_or_raise=lambda **_: None))
    monkeypatch.setattr('core.llm_factory.model_control_plane.get_config', lambda: {'governance': {}})
    monkeypatch.setattr('core.llm_factory.model_telemetry_service', SimpleNamespace(record_aux_model_invocation=lambda **_: None))
    calls = []

    def post(endpoint, **kwargs):
        calls.append(endpoint)
        if len(calls) == 1:
            return Response(400, 'query is too long')
        if len(calls) == 2:
            return Response(200, payload={'results': [{'index': 0, 'relevance_score': .9}]})
        raise AssertionError('unexpected extra endpoint')

    monkeypatch.setattr('requests.post', post)
    assert reranker.rerank('query', ['doc'], top_k=1)[0]['index'] == 0
    assert len(circuit.permits) == 2 and len(circuit.finishes) == 2
    assert all(item[1]['error_code'] == 'invalid_request' for item in circuit.finishes[:1])


def test_embedding_similarity_wrapper_does_not_double_admit_injected_embedding(monkeypatch):
    class FakeEmbedding:
        def embed_documents(self, values):
            return [[1.0] for _ in values]
    circuit = CircuitSpy()
    monkeypatch.setattr('core.llm_factory.provider_circuit_service', circuit)
    reranker = EmbeddingSimilarityReranker(FakeEmbedding(), 'embed', provider_id='provider')
    assert reranker.rerank('q', ['d']) == [{'index': 0, 'document': 'd', 'relevance_score': 1.0}]
    assert circuit.permits == []
