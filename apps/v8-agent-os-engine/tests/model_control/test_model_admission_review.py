"""Independent admission counterexamples using the real LangChain callback manager.

The transport is deterministic and offline; budget/circuit state is a temporary
SQLite database. Individual tests repair only an earlier defect in memory when
needed to expose the next independent boundary.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
import sqlite3

import pytest
from langchain_core.caches import InMemoryCache
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from core.database import DatabaseManager
from core import model_budget_service as budget_module
from core.model_budget_service import ModelBudgetService
from core import model_telemetry as telemetry
from core.provider_circuit import ProviderCircuitService
from core.llm_chat_adapter import V8ChatModelAdapter
from core.prompt_cache_gateway import PreparedPromptCacheRequest
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from erc.runtime_context import bind_runtime_context


class _OfflineChatTransport(BaseChatModel):
    calls: list[str] = Field(default_factory=list)
    cached_response: bool = False
    cancel_transport: bool = False

    @property
    def _llm_type(self):
        return 'offline-admission-review'

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append('transport')
        if self.cancel_transport:
            raise asyncio.CancelledError('user cancelled')
        response_metadata = ({'v8_prompt_cache': {'responseCacheDecision': 'hit'}}
                             if self.cached_response else {})
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content='fixture response', response_metadata=response_metadata,
            usage_metadata={'input_tokens': 8, 'output_tokens': 4, 'total_tokens': 12}))])


def _config(**limits):
    return {'governance': {'providerFailureThreshold': 1, 'providerCircuitCooldownSeconds': 60,
                          'budgets': {'enabled': True, 'estimatedOutputTokens': 1, **limits}}}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / 'admission-review.db')
    budgets = ModelBudgetService(database)
    circuits = ProviderCircuitService(database)
    monkeypatch.setattr(telemetry, 'db', database)
    monkeypatch.setattr(telemetry, 'model_budget_service', budgets)
    monkeypatch.setattr(telemetry, 'provider_circuit_service', circuits)
    return database, budgets, circuits


def _callback(config=None):
    return telemetry.ModelTelemetryCallback(model_id='model', provider_id='provider',
        provider_name='Fixture', role='supervisor',
        governance_config_getter=(lambda: config) if config is not None else None)


def _adapter(inner, callback, monkeypatch, *, cached=False):
    """Use the production callback placement, cache boundary and inner invoke."""
    def prepare(self, messages, **kwargs):
        hit = AIMessage(content='fixture response', usage_metadata={
            'input_tokens': 8, 'output_tokens': 4, 'total_tokens': 12},
            response_metadata={'v8_prompt_cache': {'responseCacheDecision': 'hit'}}) if cached else None
        return PreparedPromptCacheRequest(messages=list(messages), kwargs={},
            diagnostics={'responseCacheDecision': 'hit' if cached else 'miss'}, cache_hit_message=hit)
    monkeypatch.setattr(V8ChatModelAdapter, '_prepare_prompt_cache_request', prepare)
    monkeypatch.setattr(V8ChatModelAdapter, '_finalize_prompt_cache_response', lambda self, message, prepared: message)
    return V8ChatModelAdapter(model_id='model', provider_standard='openai', role='supervisor',
        meta={'api_standard': 'openai', 'model_ref': 'provider::model',
              'capabilityClass': 'chat_tool_calling', 'capabilities': {'supportsTools': True}},
        model_kwargs={'callbacks': [callback]}, builder=lambda: inner)


def test_active_hold_in_another_run_does_not_spend_this_run_budget(isolated):
    _, budgets, _ = isolated
    config = _config(runMaxTokens=100)
    first = budgets.reserve(config=config, run_id='A', estimated_tokens=60)
    second = budgets.reserve(config=config, run_id='B', estimated_tokens=70)
    assert first and second


def test_yesterday_unknown_hold_does_not_spend_today_global_budget(isolated):
    database, budgets, _ = isolated
    config = _config(globalDailyTokenLimit=100)
    old = budgets.reserve(config=config, run_id='A', estimated_tokens=80)
    budgets.mark_dispatched(old)
    budgets.release(old, reason='cancelled')
    with database.get_connection() as conn:
        conn.execute("UPDATE model_budget_reservations SET bucket_date='2000-01-01' WHERE id=?", (old,))
        conn.commit()
    assert budgets.reserve(config=config, run_id='B', estimated_tokens=50)


def test_real_langchain_success_is_not_replaced_by_settlement_name_error(isolated, monkeypatch):
    _, _, _ = isolated
    inner = _OfflineChatTransport()
    model = _adapter(inner, _callback(_config(globalDailyTokenLimit=100)), monkeypatch)
    with bind_runtime_context(run_id='run'):
        answer = model.invoke([HumanMessage(content='hello')])
    assert answer.content == 'fixture response' and inner.calls == ['transport']


def test_ledger_accounted_and_settled_change_together_without_double_charge(isolated, monkeypatch):
    database, budgets, _ = isolated
    # Isolate ordering from the independent missing nullcontext import.
    monkeypatch.setattr(budget_module, 'nullcontext', nullcontext, raising=False)
    config = _config(globalDailyTokenLimit=20)
    model = _adapter(_OfflineChatTransport(), _callback(config), monkeypatch)
    with bind_runtime_context(run_id='run'):
        assert model.invoke([HumanMessage(content='x')]).content == 'fixture response'
    with database.get_connection() as conn:
        row = conn.execute('SELECT state,actual_tokens,ledger_accounted FROM model_budget_reservations').fetchone()
    assert tuple(row) == ('settled', 12, 1)
    assert budgets.reserve(config=config, run_id='second', estimated_tokens=8)


def test_optional_telemetry_storage_failure_does_not_discard_real_answer(isolated, monkeypatch):
    database, _, _ = isolated
    monkeypatch.setattr(database, 'add_model_invocation_log',
                        lambda _: (_ for _ in ()).throw(sqlite3.OperationalError('fixture observability unavailable')))
    inner = _OfflineChatTransport()
    model = _adapter(inner, _callback(), monkeypatch)
    answer = model.invoke([HumanMessage(content='hello')])
    assert answer.content == 'fixture response' and inner.calls == ['transport']


def test_real_langchain_cache_hit_does_not_need_provider_admission(isolated, monkeypatch):
    _, _, circuits = isolated
    config = _config()
    inner = _OfflineChatTransport()
    model = _adapter(inner, _callback(config), monkeypatch)
    model.cache = InMemoryCache()
    messages = [HumanMessage(content='cached question')]
    assert model.invoke(messages).content == 'fixture response'
    for _ in range(2):
        fault = circuits.acquire('provider', config=config)
        circuits.finish(fault, success=False, config=config, error_code='timeout')
    assert circuits.snapshot('provider')['circuitState'] == 'open'
    assert model.invoke(messages).content == 'fixture response'
    assert inner.calls == ['transport']


def test_local_v8_response_cache_hit_does_not_leave_unknown_usage_hold(isolated, monkeypatch):
    database, _, _ = isolated
    inner = _OfflineChatTransport()
    model = _adapter(inner, _callback(_config(globalDailyTokenLimit=100)), monkeypatch, cached=True)
    with bind_runtime_context(run_id='run'):
        model.invoke([HumanMessage(content='local cached answer')])
    with database.get_connection() as conn:
        rows = conn.execute("SELECT state FROM model_budget_reservations WHERE state IN ('reserved','in_flight','unknown')").fetchall()
    assert rows == []
    assert inner.calls == []


def test_user_cancel_does_not_open_provider_circuit(isolated, monkeypatch):
    _, _, circuits = isolated
    model = _adapter(_OfflineChatTransport(cancel_transport=True), _callback(_config()), monkeypatch)
    with pytest.raises(asyncio.CancelledError):
        model.invoke([HumanMessage(content='cancel fixture')])
    assert circuits.snapshot('provider')['circuitState'] == 'closed'


def _embedding(isolated, monkeypatch, config):
    from core import llm_factory
    monkeypatch.setattr(llm_factory.model_control_plane, 'get_config', lambda: config)
    return llm_factory.OpenAICompatibleEmbedding('model', 'fixture-placeholder', 'https://fixture.invalid/v1',
                                                 max_tokens=8192, provider_id='provider')


def test_embedding_estimate_blocks_before_http_when_one_token_budget_cannot_cover_input(isolated, monkeypatch):
    embedding = _embedding(isolated, monkeypatch, _config(globalDailyTokenLimit=1))
    calls = []
    class Response:
        status_code, text = 200, ''
        def json(self):
            return {'data': [{'index': 0, 'embedding': [1.0]}]}
    def post(*args, **kwargs):
        calls.append(True)
        return Response()
    monkeypatch.setattr('requests.post', post)
    with pytest.raises(ModelGovernanceInterventionRequired):
        embedding.embed_query('some input requiring more than one token ' * 20)
    assert calls == []


def test_embedding_cancel_does_not_open_provider_circuit(isolated, monkeypatch):
    _, _, circuits = isolated
    embedding = _embedding(isolated, monkeypatch, _config())
    def post(*args, **kwargs):
        raise asyncio.CancelledError('user cancelled')
    monkeypatch.setattr('requests.post', post)
    with pytest.raises(asyncio.CancelledError):
        embedding.embed_query('fixture')
    assert circuits.snapshot('provider')['circuitState'] == 'closed'


def test_invalid_embedding_200_does_not_close_recovery_probe(isolated, monkeypatch):
    database, _, circuits = isolated
    config = _config()
    embedding = _embedding(isolated, monkeypatch, config)
    permit = circuits.acquire('provider', config=config)
    circuits.finish(permit, success=False, config=config, error_code='timeout')
    with database.get_connection() as conn:
        conn.execute("UPDATE provider_circuit_state SET open_until=0 WHERE provider_id='provider'")
        conn.commit()
    class Response:
        status_code, text = 200, ''
        def json(self):
            return {'data': None}
    monkeypatch.setattr('requests.post', lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match='invalid_response'):
        embedding.embed_query('fixture')
    assert circuits.snapshot('provider')['circuitState'] != 'closed'
