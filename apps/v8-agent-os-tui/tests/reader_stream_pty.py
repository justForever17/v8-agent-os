"""Real Linux PTY + production reader; canonical events are synthetic."""
import argparse
import json
import os
import pty
import select
import subprocess
import tempfile
import termios
import time

parser = argparse.ArgumentParser()
parser.add_argument('--node', required=True)
parser.add_argument('--entry', required=True, help='Bundled reader_stream_fixture.mts')
args = parser.parse_args()

with tempfile.TemporaryDirectory(prefix='v8-reader-stream-') as state:
    master, slave = pty.openpty()
    baseline = termios.tcgetattr(slave)
    env = {**os.environ, 'TERM': 'xterm-256color', 'NO_COLOR': '1', 'V8_AGENT_OS_HOME': state}
    child = subprocess.Popen([args.node, args.entry], stdin=slave, stdout=slave, stderr=slave, env=env)
    output = bytearray()

    def drain(until):
        while time.monotonic() < until:
            if select.select([master], [], [], .05)[0]:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
            if b'READER_FIXTURE_DONE' in output:
                return

    try:
        drain(time.monotonic() + 15)
        text = output.decode('utf-8', errors='replace').replace('\r\n', '\n')
        assert 'READER_FIXTURE_DONE' in text, text[-2000:]
        assert text.count('只读一次的开头。') == 1, 'stream repeated already-spoken prose'
        assert '只读一次的开头。增量甲中文👩🏽‍💻\n\n末尾' in text, 'stream changed prose/blank lines'
        assert '末尾\n主理人 · 已完成\n' in text, 'terminal state was hidden'
        assert '消息已更新\n主理人 · 已完成\n修订后的文本' in text, 'revision was silently appended'
        assert '主理人 · 已完成\n新会话同ID' in text, 'new session reused previous message projection'
        assert text.count('消息已更新') == 1, 'normal streaming or session switch was mislabelled a revision'
        assert '\x1b[2J' not in text, 'linear reader unexpectedly repainted the screen'
        os.write(master, b'\x04')
        child.wait(timeout=10)
        assert child.returncode == 0
        assert termios.tcgetattr(slave)[3] & (termios.ICANON | termios.ECHO) == baseline[3] & (termios.ICANON | termios.ECHO)
        print(json.dumps({'platform': 'linux-pty', 'network': 'synthetic-canonical-stream', 'appendOnce': True,
            'unicodeAndBlankLinesExact': True, 'revisionAnnounced': True, 'terminalStateVisible': True,
            'sessionIdentityReset': True, 'terminalRestored': True, 'outputBytes': len(output)}))
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        os.close(master)
        os.close(slave)
