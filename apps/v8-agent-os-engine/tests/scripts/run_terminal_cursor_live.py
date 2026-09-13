"""Isolated Windows PTY + bounded pipe output audit. Never imports user state."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

parser=argparse.ArgumentParser();parser.add_argument("--live",action="store_true");args=parser.parse_args()
if not args.live: parser.error("--live is required")
root=Path(__file__).resolve().parents[4]
state=Path(tempfile.mkdtemp(prefix="terminal-fixture-",dir=root/"tmp"))
os.environ["V8_AGENT_OS_HOME"]=str(state)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from core.tools.native.command import BackgroundProcess

workspace=state/"工作区 with spaces";workspace.mkdir()
script=workspace/"output.py"
script.write_text("import sys\nchunk=b'0123456789abcdef'*4096\nfor i in range(960):sys.stdout.buffer.write(chunk)\nsys.stdout.buffer.flush()\n",encoding="utf-8")
command=subprocess.list2cmdline([sys.executable,str(script)])
process=BackgroundProcess(command,cwd=str(workspace),interactive=False,terminal_mode="pipe",timeout_seconds=90)
start=time.monotonic()
try:
    while process.is_running and time.monotonic()-start<90: time.sleep(.05)
    assert not process.is_running and process.return_code==0,(process.return_code,process.failure_kind)
    expected=hashlib.sha256((b"0123456789abcdef"*4096)*960).hexdigest()
    for observer in range(2):
        cursor=0;digest=hashlib.sha256()
        while True:
            page=process.read_output(cursor)
            assert len(page["data"].encode())<=65536
            digest.update(page["data"].encode());cursor=page["cursor"]
            if not page["hasMore"]:break
        assert digest.hexdigest()==expected
    assert len(process.output_history)<=8
finally:
    if process.is_running: process.terminate()

pty=BackgroundProcess("powershell.exe -NoLogo -NoProfile",cwd=str(workspace),interactive=True,shell_dialect="cmd",timeout_seconds=60)
try:
    time.sleep(1)
    pty.write_input("Write-Output ('V8_CWD:' + (Get-Location).Path)\r")
    deadline=time.monotonic()+10;output="";cursor=0
    while time.monotonic()<deadline:
        page=pty.read_output(cursor);output+=page["data"];cursor=page["cursor"]
        if "V8_CWD:"+str(workspace) in output:break
        time.sleep(.05)
    assert "V8_CWD:"+str(workspace) in output,"PTY cwd was not observed"
    pty.write_input("while ($true) { Write-Output 'fixture flood'; Start-Sleep -Milliseconds 10 }\r")
    time.sleep(.3);pty.write_input("\x03");time.sleep(.3)
    stop_start=time.monotonic();pty.terminate()
    assert not pty.is_running and pty._read_return_code() is not None
    result={"level":"ACTUAL_WINDOWS_PTY_AND_PIPE","cwdVerified":True,"pipeBytes":process.output_log.size,"twoObserverHashMatch":True,"memoryTailChunks":len(process.output_history),"ctrlCSent":True,"terminationConfirmed":True,"terminationMs":(time.monotonic()-stop_start)*1000}
    out=root/"tmp/resident-web-evidence/terminal-native.json";out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2),encoding="utf-8");print(json.dumps(result))
finally:
    if pty.is_running:pty.terminate()
