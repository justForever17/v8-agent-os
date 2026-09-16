"""The shared ingress preserves peer proof without granting Phone/control identity."""
import asyncio
import base64
import json
from copy import deepcopy

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException, Request
from core.client_auth_boundary import EngineControlBoundary
from core.remote_link.phone_gateway import create_phone_gateway_app
from runtimes.network_supervisor.models import NetworkEnvelope, NetworkSupervisorRuntimeConfig
from runtimes.network_supervisor.service import NetworkSupervisorService


def protocol_node(peer_id):
    service = NetworkSupervisorService()
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    config = NetworkSupervisorRuntimeConfig.model_validate({'node': {'peerId': peer_id}})
    state = {}
    service.get_config_model = lambda: config
    service.read_state = lambda: deepcopy(state)
    service.write_state = lambda value: (state.clear(), state.update(deepcopy(value)))
    service._private_key = lambda: key
    service._local_identity = lambda: {'peerId': peer_id, 'publicKey': public}
    return service, public


@pytest.fixture
def gateway():
    sender, public = protocol_node('peer_sender')
    receiver, _ = protocol_node('peer_receiver')
    requests = []
    native = FastAPI()

    @native.post('/v1/network-supervisor/peer/neighbors/messages')
    async def receive(request: Request):
        if request.headers.get('x-v8-peer-token') != 'fixture-peer-proof':
            raise HTTPException(401, 'peer_token_required')
        raw = await request.body()
        envelope = NetworkEnvelope.model_validate(json.loads(raw))
        receiver.verify_envelope(envelope, provided_public_key=public, allow_untrusted=True)
        requests.append((raw, dict(request.headers)))
        return {'accepted': envelope.payload['messageId']}

    native.add_middleware(EngineControlBoundary, secret_reader=lambda:'fixture-service')
    return create_phone_gateway_app(client_app=native), sender, requests


def call(app, payload, headers=None, path='/v1/network-supervisor/peer/neighbors/messages'):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://ingress') as client:
            return await client.post(path,content=payload,headers={'content-type':'application/json',**(headers or {})})
    return asyncio.run(run())


def test_signed_peer_traffic_reaches_engine_without_admin_and_without_identity_promotion(gateway):
    app, sender, requests = gateway
    envelope = sender.build_envelope(message_type='neighbor.message',to_peer_id='peer_receiver',payload={'messageId':'fixture-message','body':'hello'})
    wire=json.dumps(envelope.model_dump(by_alias=True), indent=2).encode()
    response=call(app,wire,{'x-v8-peer-token':'fixture-peer-proof','authorization':'Bearer phone-fixture','x-v8-agent-os-secret':'fixture-service','cookie':'must-not-forward=1'})
    assert response.status_code == 200 and response.json()=={'accepted':'fixture-message'}
    assert requests[0][0] == wire
    forwarded=requests[0][1]
    assert forwarded['x-v8-peer-token']=='fixture-peer-proof'
    assert not {'authorization','cookie','x-v8-agent-os-secret'} & forwarded.keys()


@pytest.mark.parametrize('proof',[{}, {'authorization':'Bearer phone-fixture'}, {'x-v8-agent-os-secret':'fixture-service'}, {'x-v8-peer-token':'wrong'}])
def test_other_credentials_cannot_replace_peer_proof(gateway,proof):
    app,sender,requests=gateway
    envelope=sender.build_envelope(message_type='neighbor.message',to_peer_id='peer_receiver',payload={'messageId':'never'})
    assert call(app,json.dumps(envelope.model_dump(by_alias=True)).encode(),proof).status_code==401
    assert requests==[]


def test_peer_gateway_retains_signature_body_and_route_limits(gateway):
    app,sender,requests=gateway
    envelope=sender.build_envelope(message_type='neighbor.message',to_peer_id='peer_receiver',payload={'messageId':'original'}).model_dump(by_alias=True)
    envelope['payload']['messageId']='forged'
    assert call(app,json.dumps(envelope).encode(),{'x-v8-peer-token':'fixture-peer-proof'}).status_code in (400,401,403)
    assert requests==[]
    assert call(app,b'{}',{'x-v8-peer-token':'fixture-peer-proof'},'/v1/network-supervisor/peers').status_code==404
    assert call(app,b'x'*(262144+1),{'x-v8-peer-token':'fixture-peer-proof'}).status_code==413
