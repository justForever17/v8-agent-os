from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_web_manual_terminal_uses_authenticated_cursor_backpressure() -> None:
    panel = _read("apps/v8-agent-os-web/src/components/chat/ManualTerminalPanel.tsx")
    web_config = _read("apps/v8-agent-os-web/next.config.ts")
    viewport = _read("apps/v8-agent-os-web/src/components/chat/TerminalViewport.tsx")

    assert "/api/client/terminal/sessions/" in panel
    assert "?cursor=${lease.cursor}" in viewport
    assert "lease.terminal.write(text, resolve)" in viewport
    assert "terminal.onData" in viewport
    assert "onKeyDownCapture" not in panel
    assert ":9530" not in panel
    assert ":9530" not in web_config
    assert "isSurfaceVisible" in viewport


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
    admin_service_route = _read("apps/v8-agent-os-web/src/app/api/admin/terminal/[[...segments]]/route.ts")
    admin_client_route = _read("apps/v8-agent-os-web/src/app/api/admin/client/terminal/[[...segments]]/route.ts")

    assert "getClientProxyConfig" in web_route
    assert "${clientApiBaseUrl}/terminal" in web_route
    assert "verifyServiceAuth" in admin_service_route
    assert "x-v8-agent-os-secret" in admin_service_route
    assert "resolveInternalSecret" in admin_client_route
    assert "x-v8-agent-os-secret" in admin_client_route
