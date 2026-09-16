"""OAuth callback bridge acceptance at the production adapter boundary, offline."""
from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from core import model_telemetry
from core.database import DatabaseManager
from core.llm_chat_adapter import V8ChatModelAdapter
from core.model_budget_service import ModelBudgetService
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.prompt_cache_gateway import PreparedPromptCacheRequest
from core.provider_circuit import ProviderCircuitOpen, ProviderCircuitService
from erc.runtime_context import bind_runtime_context


MODES = ['invoke', 'ainvoke', 'stream', 'astream']
USAGE = {'input_tokens': 8, 'output_tokens': 4, 'total_tokens': 12}
TOOL = {'type': 'function', 'function': {'name': 'fixture_echo',
        'description': 'Offline fixture', 'parameters': {'type': 'object', 'properties': {}}}}


def _rows(database, table):
    with database.get_connection() as connection:
        return [dict(row) for row in connection.execute(f'SELECT * FROM {table}').fetchall()]


class _OAuthTransport:
    """The OAuth runtime shape: no BaseChatModel and no RunnableConfig callbacks."""
    def __init__(self, database, *, behavior='success'):
        self.database = database
        self.behavior = behavior
        self.calls = []
        self.closed = False
        self.configs = []
        self.entered = None

    def _enter(self, mode, kwargs):
        # Assert execution truth while the external transport is actually entered.
        holds = _rows(self.database, 'model_budget_reservations')
        permits = _rows(self.database, 'provider_circuit_attempts')
        assert sum(row['state'] == 'in_flight' for row in holds) == 1
        assert len(permits) == len(self.calls) + 1
        self.calls.append(mode)
        self.configs.append(kwargs.get('config'))

    def _cancel_if_requested(self):
        if self.behavior == 'cancel':
            raise asyncio.CancelledError('fixture user cancellation')

    def invoke(self, messages, **kwargs):
        self._enter('invoke', kwargs)
        self._cancel_if_requested()
        return AIMessage(content='fixture response', usage_metadata=dict(USAGE))

    async def ainvoke(self, messages, **kwargs):
        self._enter('ainvoke', kwargs)
        if self.behavior == 'wait':
            self.entered.set()
            await asyncio.Event().wait()
        await asyncio.sleep(0)
        self._cancel_if_requested()
        return AIMessage(content='fixture response', usage_metadata=dict(USAGE))

    def stream(self, messages, **kwargs):
        self._enter('stream', kwargs)
        try:
            yield AIMessageChunk(content='fixture ')
            self._cancel_if_requested()
            yield AIMessageChunk(content='response')
            yield AIMessageChunk(content='', usage_metadata=dict(USAGE))
        finally:
            self.closed = True

    async def astream(self, messages, **kwargs):
        self._enter('astream', kwargs)
        try:
            yield AIMessageChunk(content='fixture ')
            await asyncio.sleep(0)
            self._cancel_if_requested()
            yield AIMessageChunk(content='response')
            yield AIMessageChunk(content='', usage_metadata=dict(USAGE))
        finally:
            self.closed = True


class _BindableOAuthTransport(_OAuthTransport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bound_tools = []

    def bind_tools(self, tools, **kwargs):
        self.bound_tools.append(list(tools))
        return self


@pytest.fixture(params=['gemini_cli', 'openai_codex'])
def environment(request, tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / 'oauth-admission.db')
    budgets, circuits = ModelBudgetService(database), ProviderCircuitService(database)
    config = {'governance': {'providerFailureThreshold': 1, 'budgets': {
        'enabled': True, 'globalDailyTokenLimit': 1000, 'estimatedOutputTokens': 1}}}
    monkeypatch.setattr(model_telemetry, 'db', database)
    monkeypatch.setattr(model_telemetry, 'model_budget_service', budgets)
    monkeypatch.setattr(model_telemetry, 'provider_circuit_service', circuits)
    # Cache has its own acceptance tests; use a deterministic miss here.
    monkeypatch.setattr(V8ChatModelAdapter, '_prepare_prompt_cache_request',
        lambda self, messages, **kwargs: PreparedPromptCacheRequest(messages=list(messages),
            kwargs={}, diagnostics={'responseCacheDecision': 'miss'}, cache_hit_message=None))
    monkeypatch.setattr(V8ChatModelAdapter, '_finalize_prompt_cache_response',
                        lambda self, message, prepared: message)
    return database, budgets, circuits, config, request.param


def _adapter(environment, *, behavior='success', bindable=False, duplicate_callback=False):
    database, _, _, config, provider = environment
    runtime = (_BindableOAuthTransport if bindable else _OAuthTransport)(database, behavior=behavior)
    callback = model_telemetry.ModelTelemetryCallback(model_id='oauth-model', provider_id=provider,
        provider_name=provider, role='supervisor', governance_config_getter=lambda: config)
    model = V8ChatModelAdapter(model_id='oauth-model', provider_standard='gemini' if provider == 'gemini_cli' else 'openai',
        role='supervisor', meta={'model_ref': f'{provider}::oauth-model', 'provider_id': provider,
            'capabilityClass': 'chat_tool_calling', 'effectiveCapabilityMatrix': {
                'supports_native_tools': bindable, 'supports_streaming': True}},
        model_kwargs={'callbacks': [callback, callback] if duplicate_callback else [callback]},
        builder=lambda: runtime)
    model.cache = False
    return model, runtime, callback


def _call(model, mode):
    messages = [HumanMessage(content='offline fixture prompt')]
    if mode == 'invoke':
        return model.invoke(messages).content
    if mode == 'ainvoke':
        return asyncio.run(model.ainvoke(messages)).content
    if mode == 'stream':
        return ''.join(chunk.content for chunk in model.stream(messages))
    async def collect():
        return ''.join([chunk.content async for chunk in model.astream(messages)])
    return asyncio.run(collect())


@pytest.mark.parametrize('mode', MODES)
def test_each_oauth_mode_admits_before_transport_and_settles_usage_once(environment, mode):
    database, _, circuits, _, provider = environment
    model, runtime, callback = _adapter(environment)
    with bind_runtime_context(run_id='oauth-run'):
        assert _call(model, mode) == 'fixture response'
    assert runtime.calls == [mode]
    hold, = _rows(database, 'model_budget_reservations')
    assert hold['state'] == 'settled' and hold['actual_tokens'] == 12 and hold['ledger_accounted'] == 1
    assert hold['run_id'] == 'oauth-run'
    log, = database.list_model_invocations()
    assert log['status'] == 'completed' and log['total_tokens'] == 12
    assert len(_rows(database, 'provider_circuit_attempts')) == 1
    assert circuits.snapshot(provider)['circuitState'] == 'closed'
    assert callback._starts == {}


@pytest.mark.parametrize('mode', MODES)
def test_low_budget_is_typed_intervention_before_oauth_transport(environment, mode):
    database, _, _, config, _ = environment
    config['governance']['budgets']['globalDailyTokenLimit'] = 1
    model, runtime, _ = _adapter(environment)
    with pytest.raises(ModelGovernanceInterventionRequired):
        _call(model, mode)
    assert runtime.calls == []
    assert database.list_model_invocations() == []
    assert _rows(database, 'model_budget_reservations') == []


@pytest.mark.parametrize('mode', MODES)
def test_cancellation_keeps_unknown_usage_without_opening_circuit(environment, mode):
    database, _, circuits, _, provider = environment
    model, runtime, callback = _adapter(environment, behavior='cancel')
    received_error = None
    try:
        _call(model, mode)
    except BaseException as error:
        received_error = error
    hold, = _rows(database, 'model_budget_reservations')
    assert hold['state'] == 'unknown' and hold['estimated_tokens'] > 0
    assert circuits.snapshot(provider)['circuitState'] == 'closed'
    assert runtime.calls == [mode] and callback._starts == {}
    log, = database.list_model_invocations()
    assert log['error_code'] == 'cancelled'
    if mode in {'stream', 'astream'}:
        assert runtime.closed
    assert isinstance(received_error, asyncio.CancelledError), repr(received_error)


@pytest.mark.parametrize('mode', MODES)
def test_open_circuit_blocks_every_oauth_mode_before_transport(environment, mode):
    database, _, circuits, config, provider = environment
    permit = circuits.acquire(provider, config=config)
    circuits.finish(permit, success=False, config=config, error_code='timeout')
    model, runtime, _ = _adapter(environment)
    with pytest.raises(ProviderCircuitOpen):
        _call(model, mode)
    assert runtime.calls == [] and database.list_model_invocations() == []
    hold, = _rows(database, 'model_budget_reservations')
    assert hold['state'] == 'released'


@pytest.mark.parametrize('mode', MODES)
def test_duplicate_callback_registration_does_not_double_charge(environment, mode):
    database, _, _, _, _ = environment
    model, runtime, callback = _adapter(environment, duplicate_callback=True)
    assert _call(model, mode) == 'fixture response'
    hold, = _rows(database, 'model_budget_reservations')
    assert hold['actual_tokens'] == 12
    assert len(database.list_model_invocations()) == 1
    assert len(_rows(database, 'provider_circuit_attempts')) == 1
    assert runtime.calls == [mode] and callback._starts == {}


def test_wrapper_does_not_invent_bind_tools_for_plain_oauth_runtime(environment):
    model, runtime, _ = _adapter(environment)
    wrapped = model._get_base_model()
    assert not hasattr(runtime, 'bind_tools')
    assert not hasattr(wrapped, 'bind_tools')
    bound = model.bind_tools([TOOL])
    assert not hasattr(bound._get_runtime_model(), 'bind_tools')
    assert _call(bound, 'invoke') == 'fixture response'


@pytest.mark.parametrize('mode', MODES)
def test_real_bind_tools_preserves_admission_on_the_bound_runtime(environment, mode):
    database, _, _, _, _ = environment
    model, runtime, callback = _adapter(environment, bindable=True)
    bound = model.bind_tools([TOOL])
    assert _call(bound, mode) == 'fixture response'
    assert len(runtime.bound_tools) == 1 and runtime.bound_tools[0][0]['function']['name'] == 'fixture_echo'
    assert runtime.calls == [mode] and callback._starts == {}
    assert len(database.list_model_invocations()) == 1
    assert len(_rows(database, 'provider_circuit_attempts')) == 1


def test_cancelling_outer_ainvoke_task_releases_admission_immediately(environment):
    database, _, circuits, _, provider = environment
    model, runtime, callback = _adapter(environment, behavior='wait')
    async def cancel():
        runtime.entered = asyncio.Event()
        task = asyncio.create_task(model.ainvoke([HumanMessage(content='wait for user cancellation')]))
        await asyncio.wait_for(runtime.entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert callback._starts == {}
        hold, = _rows(database, 'model_budget_reservations')
        assert hold['state'] == 'unknown'
        assert circuits.snapshot(provider)['circuitState'] == 'closed'
    asyncio.run(cancel())


@pytest.mark.parametrize('mode', ['stream', 'astream'])
def test_closing_public_stream_releases_inner_transport_and_admission_immediately(environment, mode):
    database, _, circuits, _, provider = environment
    model, runtime, callback = _adapter(environment)
    def verify_closed():
        assert runtime.closed
        assert callback._starts == {}
        hold, = _rows(database, 'model_budget_reservations')
        assert hold['state'] == 'unknown'
        assert circuits.snapshot(provider)['circuitState'] == 'closed'
        log, = database.list_model_invocations()
        assert log['error_code'] == 'cancelled'
    messages = [HumanMessage(content='stop streaming now')]
    if mode == 'stream':
        iterator = model.stream(messages)
        assert next(iterator).content == 'fixture '
        iterator.close()
        verify_closed()
    else:
        async def close():
            iterator = model.astream(messages)
            assert (await anext(iterator)).content == 'fixture '
            await iterator.aclose()
            # Test before event-loop shutdown can implicitly close leaked generators.
            verify_closed()
        asyncio.run(close())


def test_stream_read_under_wait_for_can_close_in_parent_task(environment):
    database, _, circuits, _, provider = environment
    model, runtime, callback = _adapter(environment)

    async def exercise():
        iterator = model.astream([HumanMessage(content='close from parent task')])
        assert (await asyncio.wait_for(anext(iterator), timeout=3)).content == 'fixture '
        await iterator.aclose()
        assert runtime.closed and callback._starts == {}
        hold, = _rows(database, 'model_budget_reservations')
        assert hold['state'] == 'unknown'
        assert circuits.snapshot(provider)['circuitState'] == 'closed'

    asyncio.run(exercise())


@pytest.mark.parametrize('mode', MODES)
def test_prompt_emulated_tools_preserve_oauth_cancellation(environment, mode):
    database, _, circuits, _, provider = environment
    model, runtime, callback = _adapter(environment, behavior='cancel')
    bound = model.bind_tools([TOOL])
    received_error = None
    try:
        _call(bound, mode)
    except BaseException as error:
        received_error = error
    hold, = _rows(database, 'model_budget_reservations')
    assert hold['state'] == 'unknown'
    assert circuits.snapshot(provider)['circuitState'] == 'closed'
    assert callback._starts == {}
    assert runtime.calls == [{'stream': 'invoke', 'astream': 'ainvoke'}.get(mode, mode)]
    assert isinstance(received_error, asyncio.CancelledError), repr(received_error)
