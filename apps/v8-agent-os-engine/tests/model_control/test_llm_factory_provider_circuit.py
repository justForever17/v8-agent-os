from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
import requests

from core import llm_factory, model_telemetry
from core.database import DatabaseManager
from core.llm_factory import EmbeddingSimilarityReranker, OpenAICompatibleEmbedding, RestReranker
from core.model_budget_service import ModelBudgetService
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.prompt_budget import estimate_prompt_tokens
from core.provider_circuit import ProviderCircuitOpen, ProviderCircuitService


class Response:
    def __init__(self, status_code, text='', payload=None):
        self.status_code, self.text, self._payload = status_code, text, payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code != 200:
            raise requests.HTTPError(self.text, response=self)


@pytest.fixture
def state(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / 'aux-admission.db')
    config = {'governance': {'providerFailureThreshold': 1,
                            'budgets': {'enabled': True, 'globalDailyTokenLimit': 100000, 'estimatedOutputTokens': 1024}},
              'providers': {'provider': {'models': {'embed': {'costPerInput': 2}, 'rerank': {'costPerInput': 3}}}}}
    circuits = ProviderCircuitService(database)
    monkeypatch.setattr(model_telemetry, 'db', database)
    monkeypatch.setattr(model_telemetry, 'model_budget_service', ModelBudgetService(database))
    monkeypatch.setattr(model_telemetry, 'provider_circuit_service', circuits)
    monkeypatch.setattr(llm_factory.model_control_plane, 'get_config', lambda: config)
    monkeypatch.setattr(llm_factory.model_control_plane, 'save_config', lambda value: None)
    monkeypatch.setattr(llm_factory, '_EMBEDDING_OBSERVED_LIMITS', {})
    monkeypatch.setattr(llm_factory, '_RERANK_OBSERVED_QUERY_LIMITS', {})
    return database, circuits, config


def embedding(**kwargs):
    return OpenAICompatibleEmbedding('embed', 'fixture-key', 'https://example.test/v1',
                                     max_tokens=8192, provider_id='provider', **kwargs)


def reranker(**kwargs):
    return RestReranker('rerank', 'fixture-key', 'https://example.test/v1',
                       max_tokens=8192, provider_id='provider', **kwargs)


def rows(database, table):
    with database.get_connection() as conn:
        return [dict(row) for row in conn.execute(f'SELECT * FROM {table}').fetchall()]


def response_for(kind, usage=True):
    payload = ({'data': [{'index': 0, 'embedding': [1.0]}]} if kind == 'embedding'
               else {'results': [{'index': 0, 'relevance_score': .9}]})
    if usage:
        payload['usage'] = {'prompt_tokens': 8, 'total_tokens': 8}
    return Response(200, payload=payload)


def invoke(kind, client=None):
    if kind == 'embedding':
        return (client or embedding()).embed_query('hello')
    return (client or reranker()).rerank('query', ['document'])


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
def test_actual_usage_settles_once_with_input_price_and_no_output_estimate(state, monkeypatch, kind):
    database, _, config = state
    config['governance']['budgets']['globalDailyCostLimit'] = 1
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        assert rows(database, 'model_budget_reservations')[0]['state'] == 'in_flight'
        assert len(rows(database, 'provider_circuit_attempts')) == 1
        return response_for(kind)
    monkeypatch.setattr('requests.post', post)
    invoke(kind)
    reservation, = rows(database, 'model_budget_reservations')
    assert reservation['state'] == 'settled' and reservation['actual_tokens'] == 8
    assert reservation['ledger_accounted'] == 1
    assert reservation['estimated_tokens'] < 1024 and reservation['estimated_cost'] > 0
    logs = database.list_model_invocations()
    assert len(logs) == 1 and logs[0]['total_tokens'] == 8 and logs[0]['cost_total'] > 0
    assert len(rows(database, 'provider_circuit_attempts')) == 1 and calls[0]['timeout'] == 30


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
def test_missing_usage_keeps_unknown_estimate(state, monkeypatch, kind):
    database, _, _ = state
    monkeypatch.setattr('requests.post', lambda *_args, **_kwargs: response_for(kind, usage=False))
    invoke(kind)
    reservation, = rows(database, 'model_budget_reservations')
    assert reservation['state'] == 'unknown' and reservation['estimated_tokens'] > 0
    assert not reservation['ledger_accounted']
    assert len(database.list_model_invocations()) == 1


def open_for_probe(database, circuits, config):
    permit = circuits.acquire('provider', config=config)
    circuits.finish(permit, success=False, config=config, error_code='timeout')
    with database.get_connection() as conn:
        conn.execute('UPDATE provider_circuit_state SET open_until=0')
        conn.commit()


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
def test_cancellation_releases_probe_and_keeps_unknown_usage(state, monkeypatch, kind):
    database, circuits, config = state
    open_for_probe(database, circuits, config)
    def post(*args, **kwargs):
        raise asyncio.CancelledError('fixture cancellation')
    monkeypatch.setattr('requests.post', post)
    with pytest.raises(asyncio.CancelledError):
        invoke(kind)
    assert circuits.snapshot('provider')['probeInFlight'] is False
    assert circuits.snapshot('provider')['circuitAllowsAttempt'] is True
    assert rows(database, 'model_budget_reservations')[0]['state'] == 'unknown'
    logs = database.list_model_invocations()
    assert len(logs) == 1 and logs[0]['error_code'] == 'cancelled'


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
def test_transport_timeout_opens_circuit_and_prevents_next_http(state, monkeypatch, kind):
    database, circuits, _ = state
    calls = []
    def post(*args, **kwargs):
        calls.append(True)
        raise requests.Timeout('fixture timed out')
    monkeypatch.setattr('requests.post', post)
    with pytest.raises(requests.Timeout):
        invoke(kind)
    assert circuits.snapshot('provider')['circuitState'] == 'open'
    with pytest.raises(ProviderCircuitOpen):
        invoke(kind)
    assert calls == [True] and len(database.list_model_invocations()) == 1
    assert sorted(row['state'] for row in rows(database, 'model_budget_reservations')) == ['released', 'unknown']


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
def test_limit_retry_has_two_attempts_and_settles_error_usage(state, monkeypatch, kind):
    database, _, _ = state
    responses = [Response(400, 'input at index 0 exceeds maximum token length of 4096' if kind == 'embedding'
                          else 'query is too long', {'usage': {'prompt_tokens': 3, 'total_tokens': 3}}),
                 response_for(kind)]
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        return responses.pop(0)
    monkeypatch.setattr('requests.post', post)
    invoke(kind)
    assert len(calls) == 2
    reservations = rows(database, 'model_budget_reservations')
    assert len(reservations) == 2 and sum(row['actual_tokens'] for row in reservations) == 11
    assert len(rows(database, 'provider_circuit_attempts')) == 2
    logs = database.list_model_invocations()
    assert len(logs) == 2 and {row['status'] for row in logs} == {'failed', 'completed'}


def test_rerank_alternative_endpoint_is_another_attempt(state, monkeypatch):
    database, _, config = state
    config['governance']['providerFailureThreshold'] = 3
    client = reranker(api_flavor='nexa')
    calls = []
    def post(endpoint, **kwargs):
        calls.append(endpoint)
        return Response(404, 'endpoint not found') if len(calls) == 1 else response_for('reranker')
    monkeypatch.setattr('requests.post', post)
    invoke('reranker', client)
    assert len(calls) == 2 and calls[0] != calls[1]
    assert len(database.list_model_invocations()) == 2
    assert len(rows(database, 'provider_circuit_attempts')) == 2


@pytest.mark.parametrize('kind,payload', [
    ('embedding', {'data': None}),
    ('embedding', {'data': [{'embedding': []}]}),
    ('embedding', {'data': [{'embedding': [float('nan')]}]}),
    ('embedding', {'data': [{'index': 9, 'embedding': [1]}]}),
    ('reranker', {'results': []}),
    ('reranker', {'results': [{'index': 0}]}),
    ('reranker', {'results': [{'index': 7, 'relevance_score': .9}]}),
    ('reranker', {'results': [{'index': 0, 'relevance_score': float('nan')}]}),
    ('reranker', {'results': [{'document': 'not submitted', 'relevance_score': .9}]}),
])
def test_invalid_200_does_not_close_recovery_probe(state, monkeypatch, kind, payload):
    database, circuits, config = state
    open_for_probe(database, circuits, config)
    monkeypatch.setattr('requests.post', lambda *_args, **_kwargs: Response(200, payload=payload))
    with pytest.raises(RuntimeError, match='invalid_response'):
        invoke(kind)
    assert circuits.snapshot('provider')['circuitState'] != 'closed'
    assert database.list_model_invocations()[0]['status'] == 'failed'


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
def test_concurrent_requests_cannot_both_spend_last_budget(state, monkeypatch, kind):
    database, _, config = state
    estimate = (estimate_prompt_tokens('hello') if kind == 'embedding'
                else estimate_prompt_tokens('query') + estimate_prompt_tokens('document'))
    config['governance']['budgets']['globalDailyTokenLimit'] = estimate
    entered, unblock = threading.Event(), threading.Event()
    calls = []
    def post(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert unblock.wait(5)
        return response_for(kind)
    monkeypatch.setattr('requests.post', post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, kind)
        try:
            assert entered.wait(5)
            with pytest.raises(ModelGovernanceInterventionRequired):
                pool.submit(invoke, kind).result(timeout=5)
            assert calls == [True]
        finally:
            unblock.set()
        first.result(timeout=5)
    assert len(rows(database, 'provider_circuit_attempts')) == 1


def test_embedding_similarity_wrapper_has_only_embedding_attempt(state, monkeypatch):
    database, _, _ = state
    monkeypatch.setattr('requests.post', lambda *_args, **_kwargs: Response(200, payload={
        'data': [{'index': 0, 'embedding': [1.]}, {'index': 1, 'embedding': [1.]}],
        'usage': {'prompt_tokens': 8, 'total_tokens': 8}}))
    client = EmbeddingSimilarityReranker(embedding(), 'embed', provider_id='provider')
    assert client.rerank('q', ['d']) == [{'index': 0, 'document': 'd', 'relevance_score': 1.0}]
    assert len(rows(database, 'provider_circuit_attempts')) == 1
    assert len(database.list_model_invocations()) == 1


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
@pytest.mark.parametrize('auth', [{'type': 'api_key', 'header': 'x-api-key'}, {'type': 'api_key', 'query': 'key'}])
def test_auth_stays_in_transport_not_prompts_or_usage_metadata(state, monkeypatch, kind, auth):
    database, _, _ = state
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return response_for(kind)
    monkeypatch.setattr('requests.post', post)
    client = embedding(auth_contract=auth) if kind == 'embedding' else reranker(auth_contract=auth)
    invoke(kind, client)
    sent = calls[0]
    assert (sent.get('params', {}).get('key') or sent['headers'].get('x-api-key')) == 'fixture-key'
    assert 'fixture-key' not in str(sent['json'])
    assert 'fixture-key' not in str(database.list_model_invocations())


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
def test_only_one_half_open_http_probe_and_lease_covers_timeout(state, monkeypatch, kind):
    database, circuits, config = state
    config['governance'].update(providerCircuitProbeLeaseSeconds=1, maxFailoverSeconds=1)
    open_for_probe(database, circuits, config)
    entered, unblock = threading.Event(), threading.Event()
    calls = []
    def post(*args, **kwargs):
        calls.append(True)
        assert circuits.snapshot('provider')['retryAfterSeconds'] > kwargs['timeout']
        entered.set()
        assert unblock.wait(5)
        return response_for(kind)
    monkeypatch.setattr('requests.post', post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, kind)
        try:
            assert entered.wait(5)
            with pytest.raises(ProviderCircuitOpen):
                pool.submit(invoke, kind).result(timeout=5)
            assert calls == [True]
        finally:
            unblock.set()
        first.result(timeout=5)
    assert circuits.snapshot('provider')['circuitState'] == 'closed'
    assert len(database.list_model_invocations()) == 1


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
def test_query_auth_transport_error_is_not_persisted_as_a_secret(state, monkeypatch, kind):
    database, _, _ = state
    def post(*args, **kwargs):
        raise requests.Timeout('timed out: https://example.test?customCredential=fixture-key')
    monkeypatch.setattr('requests.post', post)
    with pytest.raises(requests.Timeout):
        invoke(kind)
    assert 'fixture-key' not in str(database.list_model_invocations())


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
def test_invalid_json_200_is_a_failed_attempt(state, monkeypatch, kind):
    database, circuits, _ = state
    class InvalidJsonResponse(Response):
        def json(self):
            raise ValueError('fixture bad json')
    monkeypatch.setattr('requests.post', lambda *_args, **_kwargs: InvalidJsonResponse(200))
    with pytest.raises(RuntimeError, match='invalid_response'):
        invoke(kind)
    invocation, = database.list_model_invocations()
    assert invocation['error_code'] == 'invalid_response'
    assert circuits.snapshot('provider')['circuitState'] == 'open'
