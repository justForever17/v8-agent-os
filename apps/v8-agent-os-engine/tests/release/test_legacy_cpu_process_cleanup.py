"""Exercise the release harness's actual Bash launch/stop code with a listener.

The QEMU payload is replaced with a tiny native process; process ownership and
signals are real. Neither a package installation nor emulated CPU is claimed.
"""
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[4]


def _assert_launch_cleanup(tmp_path, service):
    source = (REPO / 'scripts/desktop/ci-linux-legacy-x64-admin-smoke.sh').read_text()
    launches = re.findall(r'^\(\n  cd "\$(?:engine_root|server_dir)"\n.*?\n\) >"\$\w+_log_path" 2>&1 &\n\w+_pid=\$!', source, re.S | re.M)
    assert len(launches) == 2
    stop = source[source.index('stop_process() {'):source.index('\ncleanup() {')]
    shim = tmp_path / 'qemu-x86_64-static'
    shim.write_text('''#!/usr/bin/env python3
import json, os, socket, time
from pathlib import Path
listener = socket.socket()
listener.bind(('127.0.0.1', int(os.environ.get('ENGINE_PORT') or os.environ['PORT'])))
listener.listen()
Path(os.environ['FIXTURE_READY']).write_text(json.dumps({'pid': os.getpid(), 'port': listener.getsockname()[1]}))
while True:
    time.sleep(1)
''')
    shim.chmod(0o700)
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0))
        port = available.getsockname()[1]
    settings = {name: str(tmp_path) for name in ('engine_root', 'server_dir', 'engine_home', 'admin_home')}
    settings.update({name: 'synthetic-fixture' for name in ('auth_secret', 'checkpoint_key', 'legacy_cpu_model', 'engine_python', 'node_binary', 'server_path')})
    settings.update(engine_port=str(port), admin_port=str(port), engine_log_path=str(tmp_path / 'engine.log'), admin_log_path=str(tmp_path / 'admin.log'))
    setup = '\n'.join(f'{name}={shlex.quote(value)}' for name, value in settings.items())
    launch = launches[0 if service == 'engine' else 1]
    script = f'set -euo pipefail\n{setup}\n{stop}\n{launch}\necho "${{{service}_pid}}"\nread -r proceed\nstop_process "${{{service}_pid}}" {service}\necho stopped\n'
    ready = tmp_path / 'ready.json'
    process = subprocess.Popen(['bash', '-c', script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
        env={**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'], 'FIXTURE_READY': str(ready)})
    try:
        tracked_pid = int(process.stdout.readline().strip())
        until = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < until:
            time.sleep(.01)
        actual = json.loads(ready.read_text())
        assert tracked_pid == actual['pid'], 'Harness must signal the listener, not an intermediate Bash PID'
        stdout, stderr = process.communicate('stop\n', timeout=10)
        assert process.returncode == 0 and stdout.strip() == 'stopped', stderr
        with socket.socket() as reuse:
            reuse.bind(('127.0.0.1', port))
    finally:
        # Kill only this test's new process group, including old-code orphans.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)


@unittest.skipUnless(sys.platform == 'linux', 'Bash/Linux process ownership')
class LegacyProcessCleanup(unittest.TestCase):
    def test_engine_listener_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='v8os-legacy-process-') as directory:
            _assert_launch_cleanup(Path(directory), 'engine')

    def test_admin_listener_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='v8os-legacy-process-') as directory:
            _assert_launch_cleanup(Path(directory), 'admin')


if __name__ == '__main__':
    unittest.main()
