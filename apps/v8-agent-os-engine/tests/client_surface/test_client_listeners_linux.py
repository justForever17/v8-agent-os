"""Real AF_UNIX lifecycle; no Engine, user state or provider is started."""
import asyncio
import os
import socket
import stat
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from core.client_listeners import ClientListeners, _local_management_directory

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


@pytest.mark.parametrize('long_root', [False, True])
def test_stop_does_not_remove_replacement_socket(tmp_path, long_root):
    async def scenario():
        home = tmp_path / ('state' * 30 if long_root else 'state')
        listener = ClientListeners(FastAPI(), home, {'remoteLink': {'phoneGateway': {'enabled': False}}})
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
            await listener.stop()
            replacement.close()
            target.unlink(missing_ok=True)
            target.parent.rmdir()
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


@pytest.mark.parametrize('component', ['s' * 130, '工作区' * 15], ids=['ascii', 'utf8-bytes'])
def test_long_state_root_supports_private_http_and_restart(component):
    async def scenario(base):
        home = base / component
        home.mkdir()
        assert len(os.fsencode(home / 'runtime/client-control/engine.sock')) > 107
        if not component.isascii():
            assert len(str(home / 'runtime/client-control/engine.sock')) <= 107
        app = FastAPI()

        @app.get('/proof')
        async def proof(request: Request):
            return {'localManagement': request.scope['state'].get('local_management')}

        config = {'remoteLink': {'phoneGateway': {'enabled': False}}}
        listener = ClientListeners(app, home, config)
        target = None
        try:
            await listener.start()
            target = listener.local_path
            assert len(os.fsencode(target)) <= 107
            assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
            async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(target)), base_url='http://local') as client:
                assert (await client.get('/proof')).json() == {'localManagement': True}
            with pytest.raises(RuntimeError, match='already_running'):
                await ClientListeners(app, home, config).start()
            await listener.stop()
            assert not target.exists()
            await listener.start()
            assert listener.local_path == target
            async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(target)), base_url='http://local') as client:
                assert (await client.get('/proof')).json() == {'localManagement': True}
        finally:
            await listener.stop()
            if target is not None:
                target.parent.rmdir()
    with tempfile.TemporaryDirectory(prefix='v8s-', dir='/tmp') as short_root:
        asyncio.run(scenario(Path(short_root)))


def test_long_state_root_alias_cannot_start_second_listener(tmp_path):
    async def scenario():
        home = tmp_path / ('s' * 130)
        home.mkdir()
        alias = tmp_path / 'alias'
        alias.symlink_to(home, target_is_directory=True)
        config = {'remoteLink': {'phoneGateway': {'enabled': False}}}
        listener = ClientListeners(FastAPI(), home, config)
        await listener.start()
        target = listener.local_path
        try:
            with pytest.raises(RuntimeError, match='already_running'):
                await ClientListeners(FastAPI(), alias, config).start()
            assert target.is_socket()
        finally:
            await listener.stop()
            target.parent.rmdir()
    asyncio.run(scenario())


def test_distinct_long_state_roots_have_independent_listeners(tmp_path):
    async def scenario():
        listeners = []
        paths = []
        try:
            for label in ('first', 'second'):
                app = FastAPI()

                async def proof(identity=label):
                    return {'identity': identity}

                app.add_api_route('/proof', proof)
                listener = ClientListeners(app, tmp_path / ('s' * 130) / label,
                                           {'remoteLink': {'phoneGateway': {'enabled': False}}})
                listeners.append(listener)
                await listener.start()
                paths.append(listener.local_path)
            assert paths[0] != paths[1]
            for target, label in zip(paths, ('first', 'second')):
                async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(target)), base_url='http://local') as client:
                    assert (await client.get('/proof')).json() == {'identity': label}
            await listeners[0].stop()
            assert paths[1].is_socket()
        finally:
            for listener in listeners:
                await listener.stop()
            for target in paths:
                target.parent.rmdir()
    asyncio.run(scenario())


@pytest.mark.parametrize('conflict', ['symlink', 'open_permissions', 'foreign_owner'])
def test_long_root_rejects_unsafe_directory_without_modifying_it(tmp_path, monkeypatch, conflict):
    home = tmp_path / ('s' * 130)
    directory = _local_management_directory(home)
    protected = tmp_path / 'protected'
    protected.mkdir(mode=0o700)
    marker = protected / 'preserve.txt'
    marker.write_text('preserve')
    try:
        if conflict == 'symlink':
            directory.symlink_to(protected, target_is_directory=True)
        else:
            directory.mkdir(mode=0o700)
            if conflict == 'open_permissions':
                directory.chmod(0o755)
            else:
                actual_lstat = type(directory).lstat

                def foreign_lstat(path):
                    details = actual_lstat(path)
                    if path == directory:
                        fields = list(details)
                        fields[4] = os.geteuid() + 1
                        return os.stat_result(fields)
                    return details

                monkeypatch.setattr(type(directory), 'lstat', foreign_lstat)
        listener = ClientListeners(FastAPI(), home, {'remoteLink': {'phoneGateway': {'enabled': False}}})
        with pytest.raises(RuntimeError, match='directory_permissions_invalid'):
            asyncio.run(listener.start())
        assert marker.read_text() == 'preserve'
        assert not (protected / 'engine.sock').exists()
        if conflict == 'symlink':
            assert directory.is_symlink()
        elif conflict == 'open_permissions':
            assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    finally:
        if directory.is_symlink():
            directory.unlink()
        else:
            directory.rmdir()


def test_long_root_recovers_only_owned_stale_socket(tmp_path):
    async def scenario():
        home = tmp_path / ('s' * 130)
        directory = _local_management_directory(home)
        directory.mkdir(mode=0o700)
        target = directory / 'engine.sock'
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(target))
        stale.close()
        listener = ClientListeners(FastAPI(), home, {'remoteLink': {'phoneGateway': {'enabled': False}}})
        try:
            await listener.start()
            assert listener.status()['localManagement']['ready'] is True
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
        finally:
            await listener.stop()
            target.unlink(missing_ok=True)
            directory.rmdir()
    asyncio.run(scenario())
