"""Real AF_UNIX lifecycle; no Engine, user state or provider is started."""
import asyncio
import socket
import stat
import sys

import httpx
import pytest
from fastapi import FastAPI, Request
from core.client_listeners import ClientListeners

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux private Unix socket")


def test_private_socket_is_usable_private_and_removed_after_stop(tmp_path):
    async def scenario():
        app = FastAPI()

        @app.get('/proof')
        async def proof(request: Request):
            return {'localManagement': request.scope['state'].get('local_management')}

        listener = ClientListeners(app, tmp_path, {'remoteLink': {'phoneGateway': {'enabled': False}}})
        await listener.start()
        target = listener.local_path
        try:
            assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
            async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(target)), base_url='http://local') as client:
                assert (await client.get('/proof')).json() == {'localManagement': True}
        finally:
            await listener.stop()
        assert not target.exists()
        await listener.stop()
    asyncio.run(scenario())


def test_stop_does_not_remove_replacement_socket(tmp_path):
    async def scenario():
        listener = ClientListeners(FastAPI(), tmp_path, {'remoteLink': {'phoneGateway': {'enabled': False}}})
        await listener.start()
        target = listener.local_path
        target.unlink()
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            replacement.bind(str(target))
            identity = target.stat().st_ino
            await listener.stop()
            assert target.stat().st_ino == identity
        finally:
            replacement.close()
            target.unlink(missing_ok=True)
    asyncio.run(scenario())


def test_existing_listener_is_not_replaced(tmp_path):
    async def scenario():
        config = {'remoteLink': {'phoneGateway': {'enabled': False}}}
        first = ClientListeners(FastAPI(), tmp_path, config)
        await first.start()
        target = first.local_path
        identity = target.stat().st_ino
        try:
            with pytest.raises(RuntimeError, match='already_running'):
                await ClientListeners(FastAPI(), tmp_path, config).start()
            assert target.stat().st_ino == identity
        finally:
            await first.stop()
    asyncio.run(scenario())
