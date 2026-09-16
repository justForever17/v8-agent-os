"""External model tokens retain their native owner without an Admin relay."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import network_supervisor_routes as routes
from core.client_auth_boundary import EngineControlBoundary
from core.remote_link.phone_gateway import create_phone_gateway_app
from runtimes.network_supervisor.models import NetworkSupervisorRuntimeConfig
from runtimes.network_supervisor.service import NetworkSupervisorService


@pytest.fixture
def model_apps(monkeypatch):
    service = NetworkSupervisorService()
    config = NetworkSupervisorRuntimeConfig.model_validate({
        'enabled': True, 'openaiCompat': {'enabled': True},
    })
    entries = [{'id': 'fixture', 'token': 'fixture-model-token', 'permissions': []}]
    service.get_config_model = lambda: config
    service._openai_compat_token_entries = lambda: entries
    monkeypatch.setattr(routes, 'network_supervisor_service', service)
    native = FastAPI()
    native.include_router(routes.router, prefix='/v1')
    native.add_middleware(EngineControlBoundary, secret_reader=lambda: 'fixture-service')
    return native, create_phone_gateway_app(client_app=native), entries


@pytest.mark.parametrize('gateway', [False, True])
@pytest.mark.parametrize('path,proof', [
    ('openai/models', {'authorization': 'Bearer fixture-model-token'}),
    ('anthropic/v1/models', {'x-api-key': 'fixture-model-token'}),
    ('anthropic/models', {'authorization': 'Bearer fixture-model-token'}),
])
def test_native_model_token_works_without_admin_and_revokes(model_apps, gateway, path, proof):
    native, ingress, entries = model_apps
    client = TestClient(ingress if gateway else native)
    url = '/v1/network-supervisor/' + path
    response = client.get(url, headers=proof)
    assert response.status_code == 200
    assert isinstance(response.json()['data'], list)
    entries.clear()
    assert client.get(url, headers=proof).status_code == 401


@pytest.mark.parametrize('gateway', [False, True])
@pytest.mark.parametrize('path,method', [
    ('openai/models', 'GET'), ('anthropic/v1/models', 'GET'),
    ('openai/chat/completions', 'POST'), ('anthropic/v1/messages', 'POST'),
])
def test_human_and_service_proof_never_replace_model_token(model_apps, gateway, path, method):
    native, ingress, _ = model_apps
    client = TestClient(ingress if gateway else native)
    for headers in ({}, {'authorization': 'Bearer fixture-phone'},
                    {'x-v8-agent-os-secret': 'fixture-service'}):
        response = client.request(method, '/v1/network-supervisor/' + path, headers=headers, json={})
        assert response.status_code == 401


def test_model_token_cannot_manage_tokens_or_engine(model_apps):
    native, ingress, _ = model_apps
    proof = {'authorization': 'Bearer fixture-model-token'}
    for app, expected in ((native, 401), (ingress, 404)):
        client = TestClient(app)
        assert client.get('/v1/network-supervisor/openai/compat/tokens', headers=proof).status_code == expected
        assert client.post('/v1/chat/submit', headers=proof, json={}).status_code == expected
