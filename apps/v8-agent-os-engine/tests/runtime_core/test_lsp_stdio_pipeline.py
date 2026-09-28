"""Tests for LSP Stdio pipeline, JSON-RPC framing, and process lifecycle."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from runtimes.engineering.lsp.lsp_protocol import (
    Diagnostic,
    DiagnosticSeverity,
    LSPRequest,
    LSPResponse,
    LSPStatus,
    LSPStreamDecoder,
    Position,
    Range,
    encode_lsp_message,
)
from runtimes.engineering.lsp.lsp_process import LSPProcessInstance


def test_encode_lsp_message():
    payload = {"jsonrpc": "2.0", "id": 1, "method": "test"}
    encoded = encode_lsp_message(payload)
    assert encoded.startswith(b"Content-Length: ")
    assert b"\r\n\r\n" in encoded
    header, body = encoded.split(b"\r\n\r\n", 1)
    content_len = int(header.split(b": ")[1])
    assert len(body) == content_len
    assert json.loads(body.decode("utf-8")) == payload


def test_lsp_stream_decoder_single_message():
    decoder = LSPStreamDecoder()
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
    encoded = encode_lsp_message(payload)

    messages = decoder.feed(encoded)
    assert len(messages) == 1
    assert messages[0] == payload


def test_lsp_stream_decoder_chunked_feed():
    decoder = LSPStreamDecoder()
    payload = {"jsonrpc": "2.0", "id": 42, "method": "workspace/diagnostic"}
    encoded = encode_lsp_message(payload)

    # Feed byte by byte
    all_messages = []
    for byte in encoded:
        msgs = decoder.feed(bytes([byte]))
        all_messages.extend(msgs)

    assert len(all_messages) == 1
    assert all_messages[0] == payload


def test_lsp_stream_decoder_multiple_messages_in_one_chunk():
    decoder = LSPStreamDecoder()
    msg1 = {"jsonrpc": "2.0", "id": 1, "method": "m1"}
    msg2 = {"jsonrpc": "2.0", "id": 2, "method": "m2"}
    combined = encode_lsp_message(msg1) + encode_lsp_message(msg2)

    messages = decoder.feed(combined)
    assert len(messages) == 2
    assert messages[0] == msg1
    assert messages[1] == msg2


def test_lsp_stream_decoder_malformed_header_recovery():
    decoder = LSPStreamDecoder()
    malformed = b"Not-Content-Length: abc\r\n\r\n"
    valid = encode_lsp_message({"jsonrpc": "2.0", "method": "valid"})

    messages = decoder.feed(malformed + valid)
    assert len(messages) == 1
    assert messages[0]["method"] == "valid"


def test_diagnostic_models():
    diag_dict = {
        "range": {
            "start": {"line": 10, "character": 4},
            "end": {"line": 10, "character": 12},
        },
        "message": "Type mismatch",
        "severity": 1,
        "code": "TS2322",
        "source": "typescript",
    }
    diag = Diagnostic.from_dict(diag_dict)
    assert diag.message == "Type mismatch"
    assert diag.severity == DiagnosticSeverity.ERROR
    assert diag.severity.to_label() == "error"
    assert diag.code == "TS2322"
    assert diag.range.start.line == 10
    assert diag.range.end.character == 12

    out_dict = diag.to_dict()
    assert out_dict["code"] == "TS2322"
    assert out_dict["severityLabel"] == "error"


def test_lsp_process_instance_mock_handshake_and_request():
    async def _run():
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_stdin = AsyncMock()
        mock_process.stdin = mock_stdin

        # Mock stdout as an async reader
        read_queue = asyncio.Queue()

        async def mock_read(n=-1):
            return await read_queue.get()

        mock_stdout = MagicMock()
        mock_stdout.read = mock_read
        mock_process.stdout = mock_stdout

        # Mock stderr
        mock_stderr = MagicMock()
        mock_stderr.readline = AsyncMock(return_value=b"")
        mock_process.stderr = mock_stderr

        status_transitions = []

        def on_status_change(lang, status):
            status_transitions.append((lang, status))

        instance = LSPProcessInstance(
            language="ts",
            process=mock_process,
            workspace_root="/test/workspace",
            on_status_change=on_status_change,
        )

        assert instance.status == LSPStatus.INITIALIZING

        # Simulate server initialize response
        async def respond_to_init():
            await asyncio.sleep(0.01)
            resp = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {"hoverProvider": True}}}
            await read_queue.put(encode_lsp_message(resp))

        init_task = asyncio.create_task(respond_to_init())
        ok = await instance.handshake(timeout_sec=2.0)
        await init_task
        assert ok is True
        assert instance.status == LSPStatus.ACTIVE

        # Simulate custom request
        async def respond_to_hover():
            await asyncio.sleep(0.01)
            resp = {"jsonrpc": "2.0", "id": 2, "result": {"contents": "type Foo = string"}}
            await read_queue.put(encode_lsp_message(resp))

        hover_task = asyncio.create_task(respond_to_hover())
        hover_result = await instance.send_request("textDocument/hover", {"position": {"line": 1, "character": 1}}, timeout_sec=2.0)
        await hover_task
        assert hover_result == {"contents": "type Foo = string"}

        # Simulate server publishing diagnostics
        diag_event = {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "file:///test/workspace/src/foo.ts",
                "diagnostics": [
                    {
                        "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 5}},
                        "message": "Cannot find name 'foo'",
                        "severity": 1,
                        "code": 2304,
                    }
                ],
            },
        }
        await read_queue.put(encode_lsp_message(diag_event))
        await asyncio.sleep(0.02)

        diags = instance.get_diagnostics("/test/workspace/src/foo.ts")
        assert len(diags) == 1
        assert diags[0].message == "Cannot find name 'foo'"
        assert diags[0].severity == DiagnosticSeverity.ERROR

        # Clean shutdown
        await instance.shutdown(timeout_sec=0.1)
        assert instance.status == LSPStatus.IDLE

    asyncio.run(_run())
