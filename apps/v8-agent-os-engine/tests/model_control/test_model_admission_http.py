"""Real SDK/HTTP/SQLite admission; the provider itself is an offline fixture."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest
from langchain_core.messages import HumanMessage

from core.database import DatabaseManager
from core.llm_chat_adapter import V8ChatModelAdapter
from core.llm_factory import LLMFactory
from core.model_budget_service import ModelBudgetService
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core import model_telemetry as telemetry
from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
from core.prompt_cache_gateway import PreparedPromptCacheRequest
from core.provider_circuit import ProviderCircuitOpen, ProviderCircuitService
from erc.runtime_context import bind_runtime_context


def test_real_http_sdk_concurrent_admission_then_actual_over_estimate(tmp_path, monkeypatch):
    database = DatabaseManager(tmp_path / 'http-admission.db')
    budgets = ModelBudgetService(database)
    circuits = ProviderCircuitService(database)
    config = {'governance': {'budgets': {'enabled': True, 'globalDailyTokenLimit': 15,
                                        'estimatedOutputTokens': 8}}}
    monkeypatch.setattr(telemetry, 'db', database)
    monkeypatch.setattr(telemetry, 'model_budget_service', budgets)
    monkeypatch.setattr(telemetry, 'provider_circuit_service', circuits)
    monkeypatch.setattr('core.llm_factory.model_control_plane.get_config', lambda: config)
    # Disable only local cache storage: exercise real admission, SDK, wire,
    # response parsing, telemetry and transaction owners.
    monkeypatch.setattr(V8ChatModelAdapter, '_prepare_prompt_cache_request',
        lambda self, messages, **kwargs: PreparedPromptCacheRequest(
            messages=list(messages), kwargs={}, diagnostics={}, cache_hit_message=None))
    monkeypatch.setattr(V8ChatModelAdapter, '_finalize_prompt_cache_response', lambda self, message, prepared: message)
    entered, release = threading.Event(), threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            entered.set()
            if not release.wait(10):
                self.send_error(503)
                return
            body = json.dumps({'id': 'local-fixture', 'object': 'chat.completion', 'created': 1,
                'model': 'fixture', 'choices': [{'index': 0, 'finish_reason': 'stop',
                    'message': {'role': 'assistant', 'content': 'wire response'}}],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 20, 'total_tokens': 28}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    kwargs = LLMFactory._attach_telemetry({}, {'provider_id': 'fixture', 'provider_name': 'Fixture'},
        model_id='fixture', role='supervisor')
    raw = V8OpenAICompatibleChatModel(model='fixture', api_key='synthetic-fixture',
        base_url=f'http://127.0.0.1:{server.server_port}/v1', max_retries=0, timeout=10)
    adapter = V8ChatModelAdapter(model_id='fixture', provider_standard='openai', role='supervisor',
        meta={}, model_kwargs=kwargs, builder=lambda: raw)

    def invoke():
        with bind_runtime_context(run_id='shared-run', project_id='project'):
            return adapter.invoke([HumanMessage(content='hello')])

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(invoke)
            try:
                assert entered.wait(8), 'First invocation never reached actual HTTP transport'
                with pytest.raises(ModelGovernanceInterventionRequired) as rejected:
                    pool.submit(invoke).result(timeout=5)
                assert rejected.value.details['code'] == 'global_daily_tokens'
                assert len(requests) == 1
            finally:
                release.set()
            assert first.result(timeout=5).content == 'wire response'
        assert database.get_usage_ledger_totals()['total_tokens'] == 28
        assert database.get_usage_ledger_totals()['invocations'] == 1
        assert 'max_tokens' not in requests[0] and 'max_completion_tokens' not in requests[0]
        with pytest.raises(ModelGovernanceInterventionRequired):
            invoke()
        assert len(requests) == 1
        with database.get_connection() as conn:
            assert tuple(conn.execute('SELECT state,actual_tokens,ledger_accounted FROM model_budget_reservations').fetchone()) == ('settled', 28, 1)
    finally:
        release.set()
        raw.root_client.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
