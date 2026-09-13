from __future__ import annotations

import pytest
from acp_bridge.backend import V8SessionRef
from acp_bridge.bridge import AcpBridge
from acp_bridge.content import history_updates, prompt_text, runtime_update
from acp_bridge.protocol import JsonRpcError


class Backend:
    def __init__(self):
        self.prompt = None
        self.workspace = None
        self.events = [{"type": "text_chunk", "runId": "run1", "content": "answer\n"}, {"type": "done", "runId": "run1", "status": "finished"}]

    def create_session(self, *, title, workspace_path, metadata):
        self.workspace = workspace_path
        return V8SessionRef("session1", workspace_path)

    def load_session(self, *, session_id):
        return V8SessionRef(session_id, self.workspace, raw={"messages": [{"id": "m1", "role": "user", "content": "问题"}, {"id": "m2", "role": "assistant", "content": "答案\n"}]})

    def stream_prompt(self, *, session_id, prompt, metadata):
        self.prompt = prompt
        yield from self.events


def rpc(bridge, method, params=None, request_id=1):
    return [m.as_dict() for m in bridge.handle_json_rpc({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})]


def initialized(tmp_path):
    backend, bridge = Backend(), None
    bridge = AcpBridge(backend)
    rpc(bridge, "initialize", {"protocolVersion": 1})
    rpc(bridge, "session/new", {"cwd": str(tmp_path), "mcpServers": []})
    return bridge, backend


def test_protocol_requires_initialize_and_advertises_only_supported_content(tmp_path):
    bridge = AcpBridge(Backend())
    assert rpc(bridge, "session/new", {"cwd": str(tmp_path)})[-1]["error"]
    response = rpc(bridge, "initialize", {"protocolVersion": 2})[-1]["result"]
    assert response["protocolVersion"] == 1
    assert response["agentCapabilities"]["promptCapabilities"] == {"image": False, "audio": False, "embeddedContext": True}
    assert response["_meta"]["v8os"]["launch"]["requiredEnv"] == []


def test_content_array_preserves_order_whitespace_and_embedded_resource():
    text = "1. 标题\n2. 内容  \n"
    assert prompt_text([{"type": "text", "text": text}, {"type": "resource", "resource": {"uri": "file:///a.py", "text": "print('中文')\n"}}]) == text + "\n\nResource: file:///a.py\nprint('中文')\n"


@pytest.mark.parametrize("value", ["plain string", [], [{"type": "image", "data": "secret"}], [{"type": "resource", "resource": {"blob": "x"}}]])
def test_unsupported_content_rejected_without_fabricating_text(value):
    with pytest.raises(JsonRpcError):
        prompt_text(value)


def test_prompt_and_tool_content_have_no_implicit_cap(tmp_path):
    bridge, backend = initialized(tmp_path)
    text = "证据\n" * 5000
    backend.events.insert(1, {"type": "tool_result", "runId": "run1", "tool": {"toolCallId": "t1", "toolName": "read", "resultStatus": "completed", "agentVisibleResult": text, "result": "short preview"}})
    messages = rpc(bridge, "session/prompt", {"sessionId": "session1", "prompt": [{"type": "text", "text": text}]})
    assert backend.prompt == text
    assert messages[-1]["result"]["stopReason"] == "end_turn"
    assert messages[1]["params"]["update"]["content"][0]["content"]["text"] == text


@pytest.mark.parametrize("ending", [[], [{"type": "done", "status": "failed"}], [{"type": "done"}], [{"type": "done", "status": "waiting_input"}], [{"type": "error", "error": "secret"}]])
def test_eof_waiting_and_failures_cannot_be_reported_as_success(tmp_path, ending):
    bridge, backend = initialized(tmp_path)
    backend.events = [{"type": "text_chunk", "runId": "run1", "content": "partial"}, *ending]
    result = rpc(bridge, "session/prompt", {"sessionId": "session1", "prompt": [{"type": "text", "text": "run"}]})
    assert result[0]["params"]["update"]["content"]["text"] == "partial"
    assert "error" in result[-1]
    assert "secret" not in str(result[-1])


def test_load_after_restart_uses_durable_id_and_replays_before_response(tmp_path):
    original, backend = initialized(tmp_path)
    restarted = AcpBridge(backend)
    rpc(restarted, "initialize", {"protocolVersion": 1})
    messages = rpc(restarted, "session/load", {"sessionId": "session1", "cwd": str(tmp_path), "mcpServers": []})
    assert [m["params"]["update"]["sessionUpdate"] for m in messages[:-1]] == ["user_message_chunk", "agent_message_chunk"]
    assert messages[1]["params"]["update"]["content"]["text"] == "答案\n"
    assert messages[-1]["result"]["modes"]["currentModeId"] == "reduced"
    assert restarted.sessions["session1"].v8_session_id == "session1"
    assert "error" in rpc(restarted, "session/load", {"sessionId": "session1", "cwd": str(tmp_path / "other")})[-1]


def test_mcp_input_is_not_silently_ignored(tmp_path):
    bridge, backend = initialized(tmp_path)
    result = rpc(bridge, "session/new", {"cwd": str(tmp_path), "mcpServers": [{"name": "external", "command": "danger"}]})
    assert result[-1]["error"]["code"] == -32602


def test_child_narrative_stays_in_activity_instead_of_supervisor_answer():
    update = runtime_update({"type": "text_chunk", "runtimeId": "research", "runId": "r1", "content": "child output"}, seen_tools=set())
    assert update["sessionUpdate"] == "tool_call"
    assert update["content"][0]["content"]["text"] == "child output"


def test_unclassified_tool_result_does_not_invent_success():
    update = runtime_update({"type": "tool_result", "tool": {"toolCallId": "unknown", "result": "unclassified"}}, seen_tools=set())
    assert update["status"] == "pending"
    assert update["_meta"]["v8os"]["resultStatus"] == "unknown"


def test_history_preserves_tool_and_reasoning_order_without_duplicate_text():
    result = list(history_updates({"messages": [{"role": "assistant", "runId": "r", "content": "beforeafter", "nodes": [
        {"kind": "narrative", "content": "before"},
        {"kind": "execution", "executionType": "reasoning", "content": "checking"},
        {"kind": "execution", "executionType": "tool_result", "toolCallId": "t", "agentVisibleResult": "source" * 1000, "resultStatus": "completed"},
        {"kind": "narrative", "content": "after"},
    ]}]}))
    assert [r["sessionUpdate"] for r in result] == ["agent_message_chunk", "agent_thought_chunk", "tool_call", "agent_message_chunk"]
    assert result[2]["content"][0]["content"]["text"] == "source" * 1000
    assert "".join(r["content"]["text"] for r in result if r["sessionUpdate"] == "agent_message_chunk") == "beforeafter"


def test_pending_question_restores_after_process_restart(tmp_path):
    bridge, backend = initialized(tmp_path)
    backend.load_session = lambda **_: V8SessionRef("session1", str(tmp_path), raw={
        "currentRun": {"id": "run1", "status": "waiting_input", "metadata": {"safetyApprovalMode": "manual"}},
        "askUserInteractions": [{"id": "q1", "run_id": "run1", "status": "pending"}],
    })
    rpc(bridge, "session/load", {"sessionId": "session1", "cwd": str(tmp_path)})
    assert bridge.sessions["session1"].safety_mode == "manual"
    answers = []
    backend.respond_ask_user = lambda **params: answers.append(params) or {"ok": True}
    backend.stream_session = lambda **_: (event for event in [{"type": "text_chunk", "runId": "run1", "content": "blue"}, {"type": "done", "runId": "run1", "status": "finished"}])
    result = rpc(bridge, "session/prompt", {"sessionId": "session1", "prompt": [{"type": "text", "text": "blue"}]})
    assert answers == [{"interaction_id": "q1", "answer": "blue"}]
    assert backend.prompt is None
    assert result[-1]["result"]["stopReason"] == "end_turn"
