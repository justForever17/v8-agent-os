import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from core.client_auth_boundary import EngineControlBoundary
from core.client_listeners import LocalManagementApp


@pytest.fixture
def boundary():
    app = FastAPI()
    effects = []

    @app.api_route('/{path:path}', methods=['GET', 'POST'])
    async def execute(request: Request, path: str):
        effects.append(path)
        return {'received': await request.body() == b'intent'}

    app.add_middleware(EngineControlBoundary, secret_reader=lambda: 'synthetic-control')
    return app, effects


@pytest.mark.parametrize('path', ['/v1/chat/submit', '/v1/config-broker/network/prepare', '/v1/client-identity/bootstrap', '/workspace/private.txt'])
@pytest.mark.parametrize('headers', [{}, {'x-v8-agent-os-user-email': 'owner'}, {'authorization': 'Bearer valid-phone-but-not-control'}, {'x-v8-agent-os-secret': 'wrong'}])
def test_private_routes_require_service_proof_before_execution(boundary, path, headers):
    app, effects = boundary
    response = TestClient(app).post(path, headers=headers, content=b'intent')
    assert response.status_code == 401
    assert response.headers['x-v8-auth-stage'] == 'pre_execution'
    assert effects == []


def test_local_desktop_automatic_service_proof_needs_no_phone_login(boundary):
    app, effects = boundary
    response = TestClient(app).post('/v1/chat/submit', headers={'x-v8-agent-os-secret': 'synthetic-control'}, content=b'intent')
    assert response.json() == {'received': True}
    assert effects == ['v1/chat/submit']


def test_peer_and_public_client_surfaces_reach_their_own_auth_not_anonymous_control(boundary):
    app, effects = boundary
    client = TestClient(app)
    for path in ['/api/client/instance', '/api/client/pairing/consume', '/v1/network-supervisor/peer/neighbors/messages']:
        assert client.post(path).status_code == 200
    assert client.post('/v1/network-supervisor/peers').status_code == 401
    assert len(effects) == 3  # Real peer and client handlers separately validate their credentials.


def test_private_socket_wrapper_sets_authority_without_exposing_it_as_a_header(boundary):
    app, effects = boundary
    assert TestClient(LocalManagementApp(app)).post('/v1/chat/submit').status_code == 200
    assert TestClient(app).post('/v1/chat/submit', headers={'local_management': 'true'}).status_code == 401
    assert effects == ['v1/chat/submit']


def test_missing_internal_secret_never_authenticates_a_ws_ticket(monkeypatch):
    from core import realtime_protocol
    monkeypatch.setattr(realtime_protocol, 'get_internal_secret', lambda: '')
    assert realtime_protocol.verify_ws_ticket(None) is None
    assert realtime_protocol.verify_ws_ticket('malformed') is None


def test_inspector_callback_reaches_its_token_owner_but_recording_control_stays_private(monkeypatch):
    from api import rpa_routes
    from types import SimpleNamespace
    seen = []

    def ingest(recording_id, session_id, payload):
        if payload.get('oneTimeToken') != 'fixture-recording-token':
            raise PermissionError('Inspector event token mismatch.')
        seen.append((recording_id, session_id))
        return {'ok': True}

    monkeypatch.setattr(rpa_routes, '_rpa_runtime', lambda: SimpleNamespace(ingest_inspector_event=ingest))
    app = FastAPI()
    app.include_router(rpa_routes.router, prefix='/v1')
    app.add_middleware(EngineControlBoundary, secret_reader=lambda:'fixture-service')
    client = TestClient(app)
    path = '/v1/rpa/recordings/fixture-recording/inspector/sessions/fixture-session/events'
    assert client.post(path, json={'type':'closed', 'oneTimeToken':'wrong'}).status_code == 403
    assert seen == []
    assert client.post(path, json={'type':'closed', 'oneTimeToken':'fixture-recording-token'}).status_code == 200
    assert seen == [('fixture-recording', 'fixture-session')]
    assert client.get(path.removesuffix('/events')).status_code == 401
    assert client.post('/v1/rpa/recordings/fixture-recording/events', json={}).status_code == 401
