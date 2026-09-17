"""Linux PTY acceptance against the actual installed npm bin; no fake renderer."""
import argparse, fcntl, json, os, pathlib, pty, select, signal, struct, subprocess, tempfile, termios, time

p = argparse.ArgumentParser()
p.add_argument('--bin', required=True)
p.add_argument('--node', required=True)
p.add_argument('--state')
p.add_argument('--expect-unsupported-node', action='store_true')
args = p.parse_args()
root = pathlib.Path(args.state or tempfile.mkdtemp(prefix='v8-tui-pty-'))
root.mkdir(parents=True, exist_ok=True)
workspace = root / 'workspace'
workspace.mkdir(exist_ok=True)

def scenario(reader=False):
    master, slave = pty.openpty()
    baseline = termios.tcgetattr(slave)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 128, 0, 0))
    env = {**os.environ, 'TERM': 'xterm-256color', 'NO_COLOR': '1', 'V8_AGENT_OS_HOME': str(root)}
    child = subprocess.Popen([args.node, args.bin] + (['--screen-reader'] if reader else []), stdin=slave, stdout=slave, stderr=slave, env=env)
    output = bytearray()
    def drain(seconds=.25):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if select.select([master], [], [], .05)[0]:
                try: output.extend(os.read(master, 65536))
                except OSError: break
    def send(data, pause=.25):
        os.write(master, data.encode() if isinstance(data, str) else data); drain(pause)
    try:
        drain(2)
        if args.expect_unsupported_node:
            child.wait(timeout=10); drain(.1)
            assert child.returncode == 2
            assert 'Node.js 22+' in output.decode(errors='replace')
            assert b'\x1b[' not in output, 'unsupported Node entered terminal control mode'
            assert termios.tcgetattr(slave) == baseline
            assert not (root / 'runtime/tui/view.json').exists(), 'unsupported Node wrote client state'
            return {'unsupportedNodeRejected': True, 'terminalUntouched': True, 'clientStateAbsent': True}
        assert child.poll() is None, output.decode(errors='replace')[-3000:]
        # Clear previous draft, then paste split markers and UTF-8 bytes.
        send('\x03')
        text = 'A中é👨‍👩‍👧‍👦👍🏽🇨🇳Z\n/stop\n/exit\x03\x1b]52;c;inert\x07'
        paste = ('\x1b[200~' + text + '\x1b[201~').encode()
        for byte in paste: os.write(master, bytes([byte]))
        drain(.7)
        saved = json.loads((root / 'runtime/tui/view.json').read_text())
        draft = saved['drafts'].get(saved.get('sessionId') or 'new')
        assert draft['text'] == text, repr(draft['text'])
        assert 'unknown' not in draft
        assert b'\x1b]52;' not in output, 'content executed an OSC sequence'
        send('\r')
        assert child.poll() is None
        saved = json.loads((root / 'runtime/tui/view.json').read_text())
        assert saved['drafts'][saved.get('sessionId') or 'new']['text'] == text
        for columns in [104, 80, 64, 59, 128]:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, columns, 0, 0)); os.kill(child.pid, signal.SIGWINCH); drain(.15)
        os.kill(child.pid, signal.SIGTSTP); drain(.4)
        assert termios.tcgetattr(slave)[3] & termios.ICANON
        os.kill(child.pid, signal.SIGCONT); drain(.5)
        # Function-key help, explicit escape and Ctrl+C do not stop Engine.
        send('\x1bOP'); assert '帮助' in output.decode(errors='replace')
        send('\x1b', .4); send('\x03'); send('\x04', .7)
        child.wait(timeout=10); drain(.1)
        assert child.returncode == 0
        assert termios.tcgetattr(slave)[3] & termios.ICANON == baseline[3] & termios.ICANON
        assert termios.tcgetattr(slave)[3] & termios.ECHO == baseline[3] & termios.ECHO
        assert '后台服务继续运行' in output.decode(errors='replace')
        return {'screenReader': reader, 'pasteExact': True, 'enterDidNotSubmitPaste': True, 'resize': [128,104,80,64,59,128], 'rawModeRestored': True, 'outputBytes': len(output)}
    finally:
        if child.poll() is None: child.terminate(); child.wait(timeout=10)
        os.close(master); os.close(slave)

print(json.dumps({'platform': 'linux-pty', 'scenarios': [scenario()] if args.expect_unsupported_node else [scenario(), scenario(True)]}))
