from __future__ import annotations

import asyncio
import logging

import pytest

from api import platform_routes


def _instance(**overrides):
    return {
        "appInstanceId": "mcpapp-1",
        "serverName": "fixture",
        "toolName": "fixture_tool",
        "resourceUri": "ui://fixture/app",
        "sessionId": "session-1",
        "runId": "run-1",
        "permissions": {},
        **overrides,
    }


def test_initialize_exposes_supported_host_contract(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(platform_routes.mcp_manager, "get_app_instance", lambda _: _instance())

    response = asyncio.run(
        platform_routes.mcp_app_instance_rpc(
            "mcpapp-1",
            {"jsonrpc": "2.0", "id": 1, "method": "ui/initialize", "params": {}},
        )
    )

    result = response["result"]
    assert result["hostCapabilities"]["displayModes"] == ["inline", "fullscreen"]
    assert result["hostContext"]["displayMode"] == "inline"
    assert result["appInstanceId"] == "mcpapp-1"


def test_display_mode_alias_is_supported_and_logged(monkeypatch: pytest.MonkeyPatch, caplog):
    updates = []
    monkeypatch.setattr(platform_routes.mcp_manager, "get_app_instance", lambda _: _instance())
    monkeypatch.setattr(
        platform_routes.mcp_manager,
        "update_app_instance",
        lambda app_instance_id, **payload: updates.append((app_instance_id, payload)),
    )

    with caplog.at_level(logging.WARNING):
        response = asyncio.run(
            platform_routes.mcp_app_instance_rpc(
                "mcpapp-1",
                {"jsonrpc": "2.0", "id": 2, "method": "ui/requestDisplayMode", "params": {"mode": "fullscreen"}},
            )
        )

    assert response["result"]["displayMode"] == "fullscreen"
    assert updates == [("mcpapp-1", {"displayMode": "fullscreen"})]
    assert "Deprecated MCP App RPC method" in caplog.text


def test_open_link_obeys_manifest_permission_and_scheme(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        platform_routes.mcp_manager,
        "get_app_instance",
        lambda _: _instance(permissions={"openLinks": False}),
    )
    denied = asyncio.run(
        platform_routes.mcp_app_instance_rpc(
            "mcpapp-1",
            {"jsonrpc": "2.0", "id": 3, "method": "ui/open-link", "params": {"url": "https://example.com"}},
        )
    )
    assert denied["error"]["code"] == -32019

    monkeypatch.setattr(platform_routes.mcp_manager, "get_app_instance", lambda _: _instance())
    invalid = asyncio.run(
        platform_routes.mcp_app_instance_rpc(
            "mcpapp-1",
            {"jsonrpc": "2.0", "id": 4, "method": "ui/open-link", "params": {"url": "file:///secret"}},
        )
    )
    assert invalid["error"]["code"] == -32011


def test_tool_call_stops_at_manifest_permission(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        platform_routes.mcp_manager,
        "get_app_instance",
        lambda _: _instance(permissions={"toolCalls": False}),
    )

    response = asyncio.run(
        platform_routes.mcp_app_instance_rpc(
            "mcpapp-1",
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "dangerous"}},
        )
    )

    assert response["error"]["code"] == -32016


def test_guidance_queue_uses_canonical_method_source(monkeypatch: pytest.MonkeyPatch):
    queued = {}
    emitted = []

    def add_queue_item(**payload):
        queued.update(payload)
        return {
            "id": payload["queue_id"],
            "session_id": payload["session_id"],
            "run_id": payload["run_id"],
            "client_message_id": payload["client_message_id"],
            "content": payload["content"],
            "state": "pending",
        }

    monkeypatch.setattr(platform_routes.db, "add_chat_user_message_queue_item", add_queue_item)
    monkeypatch.setattr(platform_routes.db, "get_run_record", lambda _: None)
    monkeypatch.setattr(
        platform_routes,
        "_mcp_app_emit_event",
        lambda topic, **payload: emitted.append((topic, payload)),
    )

    result = platform_routes._mcp_app_enqueue_guidance(
        instance=_instance(),
        app_instance_id="mcpapp-1",
        params={"content": [{"type": "text", "text": "保留这个选择"}]},
        source_method="ui/update-model-context",
        prefix="MCP App 上下文更新",
    )

    assert result["queued"] is True
    assert result["promoted"] is False
    assert queued["content"] == "MCP App 上下文更新：\n保留这个选择"
    assert queued["request_payload"]["source"] == "mcp_app.update-model-context"
    assert emitted[0][0] == "human_guidance.queued"


def test_size_notification_and_teardown(monkeypatch: pytest.MonkeyPatch):
    updates = []
    monkeypatch.setattr(platform_routes.mcp_manager, "get_app_instance", lambda _: _instance())
    monkeypatch.setattr(
        platform_routes.mcp_manager,
        "update_app_instance",
        lambda app_instance_id, **payload: updates.append(payload),
    )
    monkeypatch.setattr(
        platform_routes.mcp_manager,
        "close_app_instance",
        lambda app_instance_id: {"ok": True, "appInstanceId": app_instance_id, "status": "closed"},
    )

    size = asyncio.run(
        platform_routes.mcp_app_instance_rpc(
            "mcpapp-1",
            {"jsonrpc": "2.0", "id": 6, "method": "ui/notifications/size-changed", "params": {"width": 1200, "height": 14000}},
        )
    )
    teardown = asyncio.run(
        platform_routes.mcp_app_instance_rpc(
            "mcpapp-1",
            {"jsonrpc": "2.0", "id": 7, "method": "ui/teardown", "params": {}},
        )
    )

    assert size["result"]["ok"] is True
    assert updates == [{"preferredSize": {"width": 1200, "height": 10000}}]
    assert teardown["result"]["status"] == "closed"
