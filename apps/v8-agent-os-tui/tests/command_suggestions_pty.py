"""Real installed npm TUI + Linux PTY; isolated synthetic HTTP Engine, no provider.

Requires the test-only terminal emulator: python3 -m pip install pyte==0.8.2
The production package does not depend on pyte or this fixture.
"""
import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import pty
import select
import signal
import struct
import subprocess
import tempfile
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pyte

parser = argparse.ArgumentParser()
parser.add_argument('--node', required=True)
parser.add_argument('--bin', required=True)
parser.add_argument('--evidence', required=True)
parser.add_argument('--reader-only', action='store_true')
args = parser.parse_args()
evidence = pathlib.Path(args.evidence)
evidence.mkdir(parents=True, exist_ok=True)
mutations = []
slow_read = threading.Event()
release_read = threading.Event()
read_started = threading.Event()
approval_available = threading.Event()
card_read_wait = threading.Event()
card_read_started = threading.Event()
release_card = threading.Event()
instance = 'slash-pty-fixture'
approval = {'id': 'fixture-approval', 'sessionId': 'other', 'status': 'pending', 'request': {'action': 'synthetic-write', 'target': '/synthetic/result.txt'}}


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/v1/client-identity/instance':
            result = {'instanceId': instance, 'initialized': True}
        elif self.path == '/v1/client-identity/owner':
            result = {'initialized': True, 'user': {'sessionIdentifier': 'fixture'}}
        elif '/quick-index?' in self.path:
            if slow_read.is_set():
                read_started.set()
                release_read.wait(10)
            result = {'sessions': [{'id': 'original', 'title': 'Synthetic history', 'status': 'completed'}]}
        elif '/turns?' in self.path:
            if '/other/' in self.path and card_read_wait.is_set():
                card_read_started.set()
                release_card.wait(10)
            result = {'messages': [{'id': f'm{i}', 'role': 'assistant', 'content': '\n'.join(f'HISTORY {i:02d}:{line:02d}' for line in range(12)), 'ordinal': i} for i in range(20)], 'syncCursor': 'fixture-cursor'}
        elif '/scope' in self.path:
            result = {'binding': {'workspace_path': '/synthetic/workspace'}}
        elif '/snapshot?' in self.path:
            result = {'currentRun': {'id': 'run', 'status': 'completed'}, 'approvals': [approval] if approval_available.is_set() else []}
        elif self.path.startswith('/v1/approvals?'):
            result = {'approvals': [approval] if approval_available.is_set() else []}
        elif '/timeline/sync' in self.path:
            result = {'messages': [], 'syncCursor': 'fixture-cursor'}
        else:
            result = {'events': []}
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def do_POST(self):
        # An unintended send/approval is a test failure even if UI hides it.
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        mutations.append(self.path)
        approved = self.path == '/v1/approvals/fixture-approval/approve'
        if approved:
            approval_available.clear()
        self.send_response(200 if approved else 400)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok":true}' if approved else b'{"detail":"unexpected fixture mutation"}')

    def log_message(self, *_):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
threading.Thread(target=server.serve_forever, daemon=True).start()


def scenario(reader=False):
    label = 'reader' if reader else 'repaint'
    with tempfile.TemporaryDirectory(prefix='v8-slash-pty-') as directory:
        state = pathlib.Path(directory)
        view_file = state / 'runtime' / 'tui' / f'view-{hashlib.sha256(instance.encode()).hexdigest()}.json'
        view_file.parent.mkdir(parents=True)
        draft = 'DRAFT-original-中👩‍🚀'
        view_file.write_text(json.dumps({'instanceId': instance, 'sessionId': 'original', 'drafts': {'original': {'text': draft, 'attachments': []}}, 'sidebar': False, 'detail': False, 'workspace': '/synthetic/workspace', 'scroll': {}, 'retryRequests': {}}))
        (state / 'config.json').write_text(json.dumps({'systemBase': {'bridge': {'engineBaseUrl': f'http://127.0.0.1:{server.server_port}'}}}))
        master, slave = pty.openpty()
        baseline = termios.tcgetattr(slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 110, 0, 0))
        env = {**os.environ, 'TERM': 'xterm-256color', 'NO_COLOR': '1', 'V8_AGENT_OS_HOME': str(state)}
        env.pop('V8OS_ENGINE_URL', None)
        env.pop('V8_AGENT_OS_ENGINE_URL', None)
        child = subprocess.Popen([args.node, args.bin] + (['--screen-reader'] if reader else []), stdin=slave, stdout=slave, stderr=slave, env=env)
        screen = pyte.Screen(110, 30)
        stream = pyte.ByteStream(screen)
        output = bytearray()
        frames = {}

        def drain(seconds=.3):
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                if select.select([master], [], [], .04)[0]:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    output.extend(data)
                    if not reader:
                        stream.feed(data)

        def send(data, pause=.3):
            os.write(master, data.encode() if isinstance(data, str) else data)
            drain(pause)

        def frame(name):
            value = '\n'.join(screen.display) if not reader else output.decode(errors='replace')
            frames[name] = value
            return value

        def saved():
            return json.loads(view_file.read_text())

        try:
            drain(2)
            assert child.poll() is None, output.decode(errors='replace')[-2000:]
            if not reader:
                assert 'DRAFT-original' in frame('initial'), frame('initial')
                send('\x1b[5~')  # Pause follow and scroll into actual history.
                before = [line.strip() for line in screen.display if line.strip().startswith('HISTORY')]
                assert before, frame('scrolled')
            send('\x10')
            opened = frame('candidates')
            assert '搜索：' in opened and '/multiline' in opened, opened
            send('he'); send('\t')
            complete = frame('completed')
            assert '/help' in complete, complete
            assert '帮助 / 首次安装' not in complete, 'Tab executed the selected action'
            send('\x1b', .5)
            if not reader:
                after = [line.strip() for line in screen.display if line.strip().startswith('HISTORY')]
                assert before == after, 'menu changed the history anchor'
                assert 'DRAFT-original' in frame('escape-restored')
            assert saved()['drafts']['original']['text'] == draft
            send('\x10'); send('no-such-command')
            assert '没有匹配' in frame('empty')
            send('\r\r\x1b[20~')
            assert not mutations, mutations
            send('\x1b', .5)
            send('\x10'); send('help'); send('\r')
            assert '帮助 / 首次安装' in frame('help')
            send('\r'); send('\r\r')
            assert not mutations, mutations
            assert saved()['drafts']['original']['text'] == draft
            if reader:
                # An unfinished number belongs to its page. It must not select
                # a different page's approval action after Escape/navigation.
                send('\x10'); send('help'); send('\r')
                send('3'); send('\x1b', .5)
                send('\x10'); send('approval'); send('\r'); send('\r')
                assert '后续消息审批模式：逐项审批' not in output.decode(errors='replace'), 'old menu number selected an approval action on a new page'
            # A paste while querying belongs to the original draft, including
            # embedded controls; it cannot run /approval, F9 or any other action.
            send('\x10'); send('approval')
            payload = '\n/approval\n/stop\x1b[20~\x1b]52;c;fixture\x07'
            send('\x1b[200~' + payload + '\x1b[201~', .7)
            send('\r')
            assert saved()['drafts']['original']['text'] == draft + payload
            assert not mutations, mutations
            assert b'\x1b]52;c;fixture' not in output, 'paste escaped text rendering'
            send('\x03')
            # A slash within an existing message is ordinary text.
            send('path'); send('/'); send('part', .6)
            assert saved()['drafts']['original']['text'] == 'path/part'
            send('\x03'); send('/')
            if not reader:
                # Actual terminal resize: six rows max, selected command and
                # composer survive at laptop, narrow SSH, and tiny dimensions.
                for columns, rows in [(80, 24), (40, 12), (24, 10), (110, 30)]:
                    screen.resize(lines=rows, columns=columns)
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
                    os.kill(child.pid, signal.SIGWINCH)
                    drain(.45)
                    current = frame(f'resize-{columns}x{rows}')
                    assert any(line.lstrip().startswith('> /') for line in screen.display), current
                    assert '/multiline' in current, current
                    assert 'Esc' in screen.display[-1], 'narrow terminal lost the return shortcut'
                    assert screen.cursor.y < rows
                for _ in range(23):
                    send('\x1b[B', .03)
                drain(.4)
                assert '/help' in frame('selection-end')
            send('\x1b', .5)
            # Slow HTTP read is real, while results are synthetic. Local query,
            # selection and Escape remain responsive before it completes.
            slow_read.set(); release_read.clear(); read_started.clear()
            send('\x02', .1)
            assert read_started.wait(2), 'slow-read fixture was not exercised'
            send('\x10'); send('help')
            assert '搜索：help' in frame('busy-candidates')
            send('\r'); assert '帮助 / 首次安装' in frame('busy-help')
            slow_read.clear(); release_read.set(); drain(.6)
            assert '帮助 / 首次安装' in frame('late-read-fenced')
            send('\x1b', .5)
            if reader:
                # A real Surface approval card arrives after attaching its
                # different session. A number typed on the old loading page
                # cannot silently become "approve" on the new card.
                approval_available.set(); card_read_wait.set(); release_card.clear(); card_read_started.clear()
                send('\x10'); send('inbox'); send('\r', .5)
                send('\x1b[B'); send('\r', .05)
                assert card_read_started.wait(2), 'approval session attach was not exercised'
                send('3', .1)
                card_read_wait.clear(); release_card.set(); drain(.6)
                assert '审批详情' in frame('approval-default')
                send('\r', .6)
                assert not mutations, 'a number from the previous page approved a new card'
                send('\x1b[B'); send('\r', .5)
                send('3'); send('\r', .6)
                assert mutations == ['/v1/approvals/fixture-approval/approve'], mutations
                send('\x1b', .5)
            send('\x10'); send('new'); send('\r', .6)
            assert saved()['sessionId'] == ''
            assert saved()['drafts']['original']['text'] == ''
            assert saved()['drafts'].get('new', {}).get('text', '') == ''
            send('\x04', .7)
            child.wait(timeout=10)
            assert child.returncode == 0
            assert termios.tcgetattr(slave)[3] & (termios.ICANON | termios.ECHO) == baseline[3] & (termios.ICANON | termios.ECHO)
            assert mutations == (['/v1/approvals/fixture-approval/approve'] if reader else []), mutations
            return {'mode': label, 'draftAndAnchorRestored': True, 'tabOnlyCompletes': True, 'noMatchF9AndRepeatedEnterInert': True, 'pasteExactAndInert': True, 'busyLocalNavigationAndLateReadFence': True, 'narrowComposerVisible': not reader, 'readerNumberCannotCrossPages': reader, 'explicitApprovalOnly': reader, 'terminalRestored': True, 'httpMutations': mutations.copy()}
        finally:
            slow_read.clear(); release_read.set(); card_read_wait.clear(); release_card.set()
            if child.poll() is None:
                child.terminate(); child.wait(timeout=10)
            (evidence / f'{label}.ansi').write_bytes(output)
            (evidence / f'{label}-frames.json').write_text(json.dumps(frames, ensure_ascii=False, indent=2))
            os.close(master); os.close(slave)


try:
    result = {'platform': 'linux-pty', 'packageBin': str(pathlib.Path(args.bin).resolve()), 'engine': 'isolated synthetic HTTP fixture', 'scenarios': [scenario(True)] if args.reader_only else [scenario(), scenario(True)]}
    (evidence / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
finally:
    server.shutdown()
