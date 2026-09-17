"""A disconnected executor through real tool/event/history/shared-client projections."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from langchain_core.messages import ToolMessage

from tests.network.test_executor_media import bench, command


def test_disconnect_unknown_outcome_is_preserved_to_canonical_reload_and_human_surface(bench, monkeypatch):
    from core.tool_surface import apply_tool_surface_budget
    from core.runtime_projection import project_chat_messages_from_events
    import runtimes.chat.runtime as runtime_module
    import erc.chat_canonical_transcript as transcript
    from tests.chat_runtime.test_chat_transcript_cleanup import FakeChatRun
    from erc.runtime_context import bind_runtime_context

    b = bench
    c = command(b)
    b.service.disconnect(b.device["deviceId"], b.epoch)
    unknown = b.service.status(b.owner, c["commandId"])
    assert unknown["status"] == "unknown_outcome" and unknown["businessVerification"] == "unverified"
    monkeypatch.setattr(runtime_module, "db", b.database)
    monkeypatch.setattr(transcript, "db", b.database)
    monkeypatch.setattr(runtime_module.workflow_ledger_service, "append_chat_projection", lambda **_: None)
    runtime = runtime_module.ChatRuntime()
    runtime._get_agent_profile = lambda _: {"name": "Supervisor", "avatar": "", "roleLabel": "Supervisor"}
    run = FakeChatRun(); run.session_id = "session1"; run.active_run_id = "run1"
    stream = runtime_module.ChatStreamState()

    async def invoke_callbacks():
        with bind_runtime_context(session_id="session1", run_id="run1"):
            projected = apply_tool_surface_budget(ToolMessage(name="device_broker", tool_call_id="read-status", content=json.dumps(unknown)),
                                                  {"agentVisibleBudget": 1200})
            assert runtime._resolve_tool_result_status(projected)[0] == "unknown"
            await runtime.handle_stream_event(run, stream, {"event": "on_tool_start", "run_id": "status-callback", "name": "device_broker",
                "data": {"input": {"toolCallId": "read-status", "mode": "status", "command_id": c["commandId"]}}})
            return await runtime.handle_stream_event(run, stream, {"event": "on_tool_end", "run_id": "status-callback", "name": "device_broker",
                "data": {"output": projected}})

    emitted = asyncio.run(invoke_callbacks())
    live = emitted[0]["tool"]
    assert live["resultStatus"] == "unknown"
    canonical = b.database.get_chat_canonical_message(stream.assistant_message_id)
    # Canonical transcript is a separate persistence path from runtime events.
    result_nodes = [n for n in canonical["nodes"] if n.get("executionType") == "tool_result"]
    assert len(result_nodes) == 1 and result_nodes[0]["resultStatus"] == "unknown"
    for index, event in enumerate(run.events):
        b.database.add_runtime_event({**event, "event_id": f"surface-event-{index}", "session_id": "session1", "run_id": "run1", "ts": "2026-09-17T01:00:00Z"})
    restored = b.database.get_runtime_events("session1")
    history = project_chat_messages_from_events(restored)
    parts = [part for message in history for part in message["parts"] if part["type"] == "tool_result"]
    assert len(parts) == 1 and parts[0]["resultStatus"] == "unknown"
    reloaded = project_chat_messages_from_events(json.loads(json.dumps(restored)))
    reload_part = next(p for m in reloaded for p in m["parts"] if p["type"] == "tool_result")
    assert reload_part["resultStatus"] == "unknown"
    surfaces = [live, result_nodes[0], parts[0], reload_part]
    assert all("unverified" in str(item.get("agentVisibleResult")) for item in surfaces)

    # Exercise the real installed shared client owner, without editing its package.
    module = Path(__file__).resolve().parents[3] / "v8-agent-os-phone/node_modules/@v8/session-realtime/dist/client-tool-surface.js"
    node = shutil.which("node")
    if not node or not module.is_file():
        pytest.skip("Shared Human Surface requires the locked Phone dependency installation")
    script = """
import {pathToFileURL} from 'node:url';
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const {buildClientToolSurface} = await import(pathToFileURL(process.argv[1]));
const results = JSON.parse(readFileSync(0, 'utf8')).map(item => buildClientToolSurface({
  toolName: 'device_broker', state: 'result', result: item.agentVisibleResult,
  resultStatus: item.resultStatus, resultReasonCode: item.resultReasonCode
}));
for (const result of results) {
  assert.equal(result.status, 'unknown');
  assert.ok(result.summary.includes('结果未知') && result.summary.includes('未核验'));
  assert.ok(!result.summary.includes('commandId') && !result.summary.includes('precondition'));
}
console.log(JSON.stringify(results.map(({status,summary})=>({status,summary}))));
"""
    checked = subprocess.run([node, "--input-type=module", "-e", script, str(module)], input=json.dumps(surfaces), text=True,
                             capture_output=True, encoding="utf-8", timeout=30, check=False)
    assert checked.returncode == 0, checked.stderr
    assert len(json.loads(checked.stdout)) == 4
