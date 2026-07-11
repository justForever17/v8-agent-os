from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.tools import tool

from runtimes.chat.runtime import ChatRuntime
from runtimes.plugin_manager.guarded_tools import build_guarded_mcp_tool


def test_figma_url_extracts_only_file_and_node_identity() -> None:
    ref = ChatRuntime._extract_figma_canvas_ref(
        {
            "content": [
                {
                    "type": "text",
                    "text": "Open https://www.figma.com/design/AbC_123/My-File?node-id=12%3A34&access_token=secret",
                }
            ]
        }
    )
    assert ref == {
        "fileKey": "AbC_123",
        "nodeId": "12:34",
        "externalUrl": "https://www.figma.com/design/AbC_123?node-id=12:34",
    }
    assert "secret" not in ref["externalUrl"]


def test_non_figma_or_lookalike_domain_is_rejected() -> None:
    assert ChatRuntime._extract_figma_canvas_ref("https://www.figma.com.evil.example/design/key") is None
    assert ChatRuntime._extract_figma_canvas_ref("https://example.com/design/key") is None


@tool
def _figma_design_context(node_id: str = "") -> str:
    """Read one Figma design context."""

    return node_id


def _guarded_figma_tool():
    _figma_design_context.metadata = {"server_name": "figma"}
    return build_guarded_mcp_tool(
        _figma_design_context,
        plugin_id="figma",
        component_id="figma-remote-mcp",
        grant={
            "grantId": "grant-1",
            "manifestDigest": "digest-1",
        },
    )


def test_ungranted_figma_text_cannot_create_ui_canvas_payload() -> None:
    runtime = ChatRuntime()
    chat_run = SimpleNamespace(
        session_id="session-1",
        active_run_id="run-1",
        prepared=SimpleNamespace(plugin_authorizations=[]),
    )
    with patch("runtimes.chat.runtime.mcp_manager.find_app_for_tool", return_value={"serverName": "figma"}):
        payload = runtime._build_mcp_app_payload(
            chat_run=chat_run,
            tool_name="get_design_context",
            tool_invocation_id="tool-1",
            output={"url": "https://www.figma.com/design/FigmaKey/Canvas?node-id=1-2"},
        )
    assert payload is None


def test_runtime_validated_figma_grant_becomes_privileged_ui_canvas_payload() -> None:
    runtime = ChatRuntime()
    guarded = _guarded_figma_tool()
    grant = {
        "grantId": "grant-1",
        "manifestDigest": "digest-1",
        "expiresAt": "2026-07-12T00:00:00Z",
    }
    chat_run = SimpleNamespace(
        session_id="session-1",
        active_run_id="run-1",
        prepared=SimpleNamespace(plugin_authorizations=[]),
    )
    with (
        patch("runtimes.chat.runtime.mcp_manager.find_app_for_tool", return_value={"serverName": "figma"}),
        patch("runtimes.plugin_manager.service.plugin_manager_service.validate_grant_for_invocation", return_value=grant),
    ):
        payload = runtime._build_mcp_app_payload(
            chat_run=chat_run,
            tool_name=guarded.name,
            tool_invocation_id="tool-1",
            output={"url": "https://www.figma.com/design/FigmaKey/Canvas?node-id=1-2"},
        )
    assert payload is not None
    assert payload["renderer"] == "figma"
    assert payload["resourceUri"].startswith("ui://plugins/figma/canvas/")
    assert payload["pluginId"] == "figma"
    assert payload["pluginDigest"] == "digest-1"
    assert payload["grantId"] == "grant-1"
    assert payload["expiresAt"] == "2026-07-12T00:00:00Z"
    assert payload["presentation"] == {"web": "edge_to_edge", "phone": "modal"}
    assert payload["allowedFrameOrigins"] == ["https://www.figma.com"]
    assert payload["fileKey"] == "FigmaKey"
    assert payload["nodeId"] == "1-2"
