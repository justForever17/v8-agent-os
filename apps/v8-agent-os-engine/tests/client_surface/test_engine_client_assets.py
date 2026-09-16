import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from core import client_identity, auth_context
from core.client_identity.service import ClientIdentityService
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from core.client_identity.resources import sign_resource_url
from api import client_asset_routes


@pytest.fixture
def assets(tmp_path, monkeypatch):
    service = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()))
    service.owners.bootstrap(login='owner', name='Owner', now=service.clock())
    monkeypatch.setattr(client_identity, '_service', service)
    monkeypatch.setattr(auth_context, '_read_internal_secret', lambda: 'synthetic-local')
    app = FastAPI()
    app.include_router(client_asset_routes.router)
    return TestClient(app), service


def png():
    buffer = io.BytesIO()
    Image.new('RGB', (600, 400), 'navy').save(buffer, format='PNG')
    return buffer.getvalue()


def test_avatar_upload_commits_real_image_and_signed_read_survives_without_admin(assets):
    client, service = assets
    ticket = service.create_ticket(base_url='https://phone.example.invalid')
    pair = service.consume_ticket(code=ticket['pairingCode'], instance_id=ticket['instanceId'])
    response = client.post('/api/client/user-avatar-upload', headers={'authorization': 'Bearer ' + pair['accessToken']}, files={'file': ('avatar.png', png(), 'image/png')})
    assert response.status_code == 200
    data = response.json()
    path = service.home / 'assets/user-media/avatar' / data['path'].split('/')[-1]
    with Image.open(path) as image:
        assert image.size == (256, 256) and image.format == 'WEBP'
    signed = sign_resource_url(service, data['path'], service.verify_access(pair['accessToken']))
    assert client.get(data['path']).status_code == 401
    assert client.get(signed).content == path.read_bytes()
    service.revoke(service.owner()['id'], pair['deviceId'])
    assert client.get(signed).status_code == 401
    assert 'v8sig' not in service.owners.path.read_text()


def test_playlist_upload_retains_receipt_without_replacing_the_live_background(assets):
    client, service = assets
    response = client.post('/api/client/user-background-upload', headers={'x-v8-agent-os-secret': 'synthetic-local', 'x-v8-background-intent': 'playlist'}, files={'file': ('bg.png', png(), 'image/png')})
    assert response.status_code == 200
    data = response.json()
    assert service.owner()['appearance'] == {}
    directory = service.home / 'assets/user-media/background'
    receipt = json.loads((directory / ('.receipt-' + data['path'].split('/')[-1] + '.json')).read_text())
    assert receipt['userId'] == service.owner()['id']
    assert receipt['media'] == data['path']
    assert list(directory.glob('*.thumb.webp'))


def test_invalid_upload_never_changes_profile_or_leaves_files(assets):
    client, service = assets
    before = service.owners.path.read_bytes()
    for content, kind in [(b'not a video', 'video/mp4'), (b'not an image', 'image/png')]:
        response = client.post('/api/client/user-background-upload', headers={'x-v8-agent-os-secret': 'synthetic-local'}, files={'file': ('fake', content, kind)})
        assert response.status_code == 400
    assert service.owners.path.read_bytes() == before
    assert not list((service.home / 'assets/user-media/background').glob('*'))


def test_auth_rejection_happens_before_upload_bytes_are_parsed(assets):
    client, service = assets
    response = client.post('/api/client/user-avatar-upload', content=b'bad multipart')
    assert response.status_code == 401
    assert not (service.home / 'assets').exists()
