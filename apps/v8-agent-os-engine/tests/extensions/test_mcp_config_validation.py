from __future__ import annotations

import pytest

from core.mcp_config_service import McpConfigValidationError, validate_mcp_server_map


def test_mcp_config_requires_explicit_transport_type() -> None:
    with pytest.raises(McpConfigValidationError) as exc:
        validate_mcp_server_map({"mcpServers": {"context7": {"url": "https://mcp.context7.com/mcp"}}})

    assert exc.value.code == "missing_or_invalid_type"


def test_mcp_config_normalizes_stdio_server() -> None:
    result = validate_mcp_server_map(
        {
            "mcpServers": {
                "sqlite": {
                    "transport": "STDIO",
                    "command": "openai-dev-mcp",
                    "args": ["serve-sqlite"],
                    "env": {"FOO": "bar"},
                }
            }
        }
    )

    assert result["sqlite"]["type"] == "stdio"
    assert "transport" not in result["sqlite"]
    assert result["sqlite"]["command"] == "openai-dev-mcp"


def test_mcp_config_requires_type_specific_target() -> None:
    with pytest.raises(McpConfigValidationError) as stdio_exc:
        validate_mcp_server_map({"demo": {"type": "stdio", "url": "https://example.test/mcp"}})
    assert stdio_exc.value.code == "missing_command"

    with pytest.raises(McpConfigValidationError) as http_exc:
        validate_mcp_server_map({"demo": {"type": "http", "command": "npx"}})
    assert http_exc.value.code == "missing_url"
