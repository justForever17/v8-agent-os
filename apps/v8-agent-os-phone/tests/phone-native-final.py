"""Narrow final native gates. Requires the disposable emulator and synthetic fixture.
Run --live --serial emulator-5586 --output <directory> --step <gate>.
Transport gates run in order: transport-pair, transport-offline,
transport-bodies, transport-restore. They create/remove only a synthetic C
profile and preserve the handed-off A/B profiles and independent drafts.
This never reads credentials or modifies real profile data.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument('--live', action='store_true', required=True)
parser.add_argument('--serial', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--step', required=True)
args = parser.parse_args()
if not args.serial.startswith('emulator-'):
    parser.error('Only an isolated emulator is accepted.')
sys.stdout.reconfigure(encoding='utf-8')
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)

def adb(*command):
    return subprocess.run(['adb', '-s', args.serial, *command], check=True, capture_output=True, timeout=30).stdout

def ui():
    adb('shell', 'uiautomator', 'dump', '/sdcard/phone-final-ui.xml')
    raw = adb('exec-out', 'cat', '/sdcard/phone-final-ui.xml')
    (output / 'latest.xml').write_bytes(raw)
    root = ET.fromstring(raw)
    assert not any(node.get('text') == 'Something went wrong' for node in root.iter('node')), 'Native error boundary'
    return root

def texts(root):
    return [node.get('text') or node.get('content-desc') for node in root.iter('node') if node.get('text') or node.get('content-desc')]

def wait(label=None, editor=False, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root = ui()
        matches = [node for node in root.iter('node') if (node.get('class') == 'android.widget.EditText' if editor else label in (node.get('text'), node.get('content-desc')))]
        if matches:
            return matches[-1]
    raise AssertionError(f'Missing {label or "editor"}: {texts(root)}')

def tap_node(node):
    x1, y1, x2, y2 = map(int, re.findall(r'\d+', node.get('bounds')))
    adb('shell', 'input', 'tap', str((x1+x2)//2), str((y1+y2)//2))

def tap(label):
    tap_node(wait(label))

def navigate(label):
    tap('导航'); tap(label)

def type_text(value):
    tap_node(wait(editor=True)); ui()
    adb('shell', 'input', 'keycombination', '113', '29')
    adb('shell', 'input', 'text', value)
    adb('shell', 'input', 'keyevent', '4')
    assert wait(editor=True).get('text') == value

def fixture(payload=None, route='control'):
    request = urllib.request.Request(f'http://127.0.0.1:22836/fixture/{route}',
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)

def capture(name):
    (output / f'{name}.png').write_bytes(adb('exec-out', 'screencap', '-p'))
    return texts(ui())

def restart():
    adb('shell', 'am', 'force-stop', 'com.v8agentos.phone')
    adb('shell', 'am', 'start', '-n', 'com.v8agentos.phone/.MainActivity')

def dismiss_read_error():
    if '读取会话失败' in texts(ui()): tap('OK')

def assert_draft(profile):
    expected = f'Independent_{profile}1_9131_UNSENT'
    assert wait(editor=True).get('text') == expected, texts(ui())
    wait(f'{profile} synthetic message')

result = {'step': args.step, 'serial': args.serial}
if args.step == 'identity':
    assert_draft('A')
    navigate('连接与设备'); tap('切换'); assert_draft('B')
    result['B'] = capture('B-native')
    print('A to B passed', flush=True)
    adb('shell', 'input', 'keyevent', '3'); time.sleep(1)
    adb('shell', 'am', 'start', '-n', 'com.v8agentos.phone/.MainActivity'); assert_draft('B')
    restart(); assert_draft('B')
    print('B foreground and force-stop passed', flush=True)
    navigate('连接与设备'); tap('切换'); assert_draft('A')
    restart(); assert_draft('A')
    result['A'] = capture('A-light-native')
    result['checks'] = ['A/B/A message and draft isolation', 'B background/foreground', 'B and A force-stop restart']
elif args.step == 'inspect':
    result['visible'] = capture('inspect')
elif args.step == 'resources':
    if '已缓存产物' in texts(ui()): tap('OK')
    identity = fixture()['longArtifactId']
    uri = f'v8agentosphone://artifacts?conversationId=session-1&artifactId={identity}'
    adb('shell', f"am start -a android.intent.action.VIEW -d '{uri}' com.v8agentos.phone")
    wait('Long identity check')
    cached = []
    for index in range(2):
        tap('打开内容')
        wait('已缓存产物')
        visible = capture(f'long-resource-{index}')
        paths = [text.split('：', 1)[1] for text in visible if text.startswith('文件已保存到：')]
        assert len(paths) == 1, visible
        cached.append(paths[0])
        tap('OK')
    assert cached[0].rsplit('/', 1)[0] == cached[1].rsplit('/', 1)[0]
    assert len(cached[0].split('/')[-2]) < 80
    result.update({'identityLength': len(identity), 'cachedFiles': cached, 'checks': ['native file creation for long resource identity', 'same identity reuses short directory mapping']})
    tap('V8 Agent OS'); assert_draft('A')
elif args.step == 'intent':
    navigate('工作区'); tap('A task 3')
    type_text('Final_Unknown_Receipt_A3')
    fixture({'holdSubmit': True})
    previous = len(fixture()['submitReceipts'])
    tap('发送消息')
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        receipts = fixture()['submitReceipts']
        if len(receipts) > previous: break
        time.sleep(0.1)
    assert len(receipts) > previous
    first = receipts[-1]
    restart()
    fixture({'holdSubmit': False})
    assert wait(editor=True).get('text') == 'Final_Unknown_Receipt_A3'
    visible = capture('intent-restart')
    assert any('上次发送尚未确认' in text for text in visible), visible
    tap('发送消息')
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        receipts = fixture()['submitReceipts']
        if len(receipts) > previous + 1: break
        time.sleep(0.1)
    assert len(receipts) > previous + 1
    assert receipts[-1]['clientMessageId'] == first['clientMessageId'] and receipts[-1]['duplicate']
    result.update({'first': first, 'retry': receipts[-1], 'checks': ['force-stop during unresolved native submission restores unknown state', 'explicit retry preserves clientMessageId']})
    navigate('工作区'); tap('A task 1'); assert_draft('A')
elif args.step == 'terminal':
    fixture({'terminalEnabled': True, 'terminalReset': False})
    navigate('工作区'); tap('A task 4')
    tap('…-process')
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        visible = texts(ui())
        if any('中文 prefix' in text for text in visible): break
    assert any('中文 prefix' in text for text in visible), visible
    initial = fixture(route='metrics')['terminalReads']
    assert any(row['cursor'] > 0 for row in initial)
    fixture({'terminalReset': True})  # same generation reset is still authoritative
    visible = capture('terminal-reset')
    reads = fixture(route='metrics')['terminalReads']
    fixture({'terminalReset': False})
    reset_at = next(index for index, row in enumerate(reads) if row['reset'] and row['cursor'] > 0)
    assert any(row['cursor'] == 0 for row in reads[reset_at+1:]), reads
    assert all(text.count('中文 prefix') <= 1 for text in visible), visible
    navigate('连接与设备'); tap('切换'); assert_draft('B')
    fixture({'terminalEnabled': False})
    navigate('连接与设备'); tap('切换')
    navigate('工作区'); tap('A task 1'); assert_draft('A')
    result.update({'reads': reads, 'checks': ['native terminal HTTP output reachable with UTF-8 byte cursor', 'same-generation reset requests cursor zero', 'switch away from active terminal to B and back']})
elif args.step == 'prepare-A':
    visible = texts(ui())
    if any('读取会话失败' in text for text in visible): tap('OK')
    if wait(editor=True).get('text') == 'Independent_B1_9131_UNSENT':
        navigate('连接与设备'); tap('切换')
    assert_draft('A')
    result['checks'] = ['A1 selected for native gate']
elif args.step == 'transport-pair':
    if '连接这台设备' not in texts(ui()):
        assert_draft('A')
        navigate('连接与设备'); tap('配对另一台设备'); tap('备用配对链接')
    tap_node(wait(editor=True)); ui()
    link = json.dumps({'instanceId': 'fixture-C', 'code': 'synthetic-code',
        'adminUrls': ['http://127.0.0.1:22836/C', 'http://127.0.0.1:22836/wrong']}, separators=(',', ':'))
    link = 'v8agentosphone://pair?manifest=' + urllib.parse.quote(link, safe='')
    adb('shell', 'input', 'keycombination', '113', '29')
    for start in range(0, len(link), 60):
        adb('shell', f"input text '{link[start:start+60]}'")
        ui()  # Wait for controlled TextInput to commit each injected key burst.
    adb('shell', 'input', 'keyevent', '4'); ui()
    tap('连接并进入 V8 OS')
    navigate('工作区'); tap('C task 1')
    type_text('Native_C_Transport_Draft')
    navigate('连接与设备')
    root = ui()
    labels = [node for node in root.iter('node') if node.get('class') == 'android.widget.Button' and node.get('content-desc') == '127.0.0.1:22836']
    tap_node(labels[0])  # newly paired C is first; preserve A/B names
    type_text('Transport_C_9131'); tap('保存')
    tap('V8 Agent OS')
    assert wait(editor=True).get('text') == 'Native_C_Transport_Draft'
    result['checks'] = ['paired independent synthetic C with wrong alias', 'C draft prepared without changing A/B']
elif args.step == 'transport-offline':
    fixture({'offlineC': True, 'resetRequestCounts': True})
    restart()
    dismiss_read_error()
    assert wait(editor=True).get('text') == 'Native_C_Transport_Draft'
    visible = capture('C-offline-cached')
    requests = fixture(route='metrics')['credentialRequests']
    assert any(row['profile'] == 'wrong' for row in requests), requests
    assert all(not row['authorization'] and not row['refreshToken'] for row in requests), requests
    result.update({'requests': requests, 'checks': ['native cold start offline keeps paired C user and draft', 'wrong alias receives only unauthenticated instance probes']})
    fixture({'offlineC': False})
elif args.step == 'transport-bodies':
    fixture({'holdBodiesC': True, 'resetRequestCounts': True})
    restart()
    samples = []
    start = time.monotonic()
    try:
        while time.monotonic() - start < 7:
            metrics = fixture(route='metrics')
            samples.append({'seconds': round(time.monotonic()-start, 2), 'held': metrics['heldBodies'], 'maximum': metrics['maximumHeldBodies']})
            time.sleep(0.25)
    finally: fixture({'holdBodiesC': False})
    assert max(row['maximum'] for row in samples) == 2, samples
    result.update({'samples': samples, 'checks': ['native headers with held bodies never exceed two finite reads', 'cached C draft remains readable after held-body failures']})
    dismiss_read_error()
    assert wait(editor=True).get('text') == 'Native_C_Transport_Draft'
    restart()
elif args.step == 'transport-restore':
    fixture({'offlineC': False, 'holdBodiesC': False})
    dismiss_read_error()
    navigate('连接与设备'); type_text('127.0.0.1')
    tap('切换')
    current = wait(editor=True).get('text')
    if current == 'Independent_B1_9131_UNSENT':
        navigate('连接与设备'); type_text('127.0.0.1'); tap('切换')
    assert_draft('A')
    navigate('连接与设备'); type_text('Transport_C_9131'); tap('Transport_C_9131')
    tap('移除连接'); tap('移除连接')
    tap('V8 Agent OS'); assert_draft('A')
    result['checks'] = ['removed only synthetic temporary C profile', 'A1 and A/B pairing preserved']
elif args.step == 'long-stream':
    assert_draft('A')
    before = fixture(route='metrics')
    samples = []
    fixture({'streamPaddingBytes': 32768})
    start = time.monotonic()
    try:
        for sample in range(7):
            raw = adb('shell', 'dumpsys', 'meminfo', 'com.v8agentos.phone').decode()
            metrics = fixture(route='metrics')
            pss = re.search(r'TOTAL PSS:\s*(\d+)', raw)
            rss = re.search(r'TOTAL RSS:\s*(\d+)', raw)
            row = {'seconds': round(time.monotonic()-start, 2), 'pssKiB': int(pss.group(1)) if pss else None, 'rssKiB': int(rss.group(1)) if rss else None, **metrics}
            samples.append(row)
            print(json.dumps(row), flush=True)
            if sample < 6: time.sleep(20)
    finally:
        fixture({'streamPaddingBytes': 0})
    after = fixture(route='metrics')
    assert after['streamBytes'] - before['streamBytes'] > 60*1024*1024
    assert after['streamConnections'] == before['streamConnections'], 'Unexpected stream rotation'
    assert after['activeStreams'] == 2
    assert_draft('A')
    result.update({'before': before, 'after': after, 'samples': samples, 'checks': ['over 60 MiB in one continuous native detail connection', '2 active streams', 'draft/UI still intact'], 'limitations': 'Synthetic transport heartbeat load, not a provider or UI token-throughput benchmark. Does not establish arbitrary producer backpressure or physical-device memory limits.'})
else:
    raise ValueError(args.step)
(output / f'{args.step}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False), flush=True)
