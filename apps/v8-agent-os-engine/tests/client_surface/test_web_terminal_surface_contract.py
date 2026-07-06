from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_web_manual_terminal_uses_bff_ticketed_websocket() -> None:
    panel = _read("apps/v8-agent-os-web/src/components/chat/ManualTerminalPanel.tsx")
    web_config = _read("apps/v8-agent-os-web/next.config.ts")

    assert "/api/client/terminal/sessions/" in panel
    assert "/ws-ticket" in panel
    assert "/api/terminal-ws/sessions/" in panel
    assert "term.onData" in panel
    assert "onKeyDownCapture" not in panel
    assert ":9530" not in panel
    assert 'source: "/api/terminal-ws/:path*"' in web_config
    assert "/v1/terminal/:path*" in web_config


def test_web_terminal_panel_consumes_agent_process_tabs() -> None:
    chat_client = _read("apps/v8-agent-os-web/src/app/chat/ChatClient.tsx")
    panel = _read("apps/v8-agent-os-web/src/components/chat/ManualTerminalPanel.tsx")

    assert "terminalTabIdForProcess" in chat_client
    assert "visibleTerminalProcesses" in chat_client
    assert "setTerminalOpen(true)" in chat_client
    assert "hiddenTerminalTabIds" in chat_client
    assert "processes={visibleTerminalProcesses}" in chat_client
    assert "InteractiveTerminalCard" in panel
    assert "kind: 'process'" in panel


def test_terminal_proxy_routes_are_service_bound() -> None:
    web_route = _read("apps/v8-agent-os-web/src/app/api/client/terminal/[[...segments]]/route.ts")
    admin_service_route = _read("apps/v8-agent-os-admin/src/app/api/terminal/[[...segments]]/route.ts")
    admin_client_route = _read("apps/v8-agent-os-admin/src/app/api/client/terminal/[[...segments]]/route.ts")

    assert "${adminApiBaseUrl}/terminal" in web_route
    assert "verifyServiceAuth" in admin_service_route
    assert "x-v8-agent-os-secret" in admin_service_route
    assert "resolveInternalSecret" in admin_client_route
    assert "x-v8-agent-os-secret" in admin_client_route
