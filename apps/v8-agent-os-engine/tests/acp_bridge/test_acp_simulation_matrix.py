"""Framing regressions. End-to-end fault oracles live in stdio integration tests."""
import json
from io import StringIO
from acp_bridge.bridge import AcpBridge
from acp_bridge.stdio_server import run_stdio_server


def test_utf8_newline_transport_emits_only_json_and_ignores_notifications():
    request = {"jsonrpc": "2.0", "id": "中文", "method": "initialize", "params": {"protocolVersion": 1}}
    stdout = StringIO()
    run_stdio_server(stdin=StringIO(json.dumps(request, ensure_ascii=False) + '\n{"jsonrpc":"2.0","method":"unknown"}\n'), stdout=stdout, bridge=AcpBridge())
    messages = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(messages) == 1
    assert messages[0]["id"] == "中文"
    assert messages[0]["result"]["protocolVersion"] == 1


def test_content_length_is_utf8_byte_count_and_parse_failure_is_not_crash():
    body = json.dumps({"jsonrpc": "2.0", "id": "中文", "method": "initialize", "params": {"protocolVersion": 1}}, ensure_ascii=False)
    stdout = StringIO()
    run_stdio_server(stdin=StringIO(f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"), stdout=stdout, bridge=AcpBridge())
    header, response = stdout.getvalue().split("\r\n\r\n", 1)
    assert int(header.split(":", 1)[1]) == len(response.encode("utf-8"))
    assert json.loads(response)["id"] == "中文"
    bad = StringIO()
    run_stdio_server(stdin=StringIO("{broken\n"), stdout=bad, bridge=AcpBridge())
    assert json.loads(bad.getvalue())["error"]["code"] == -32700
