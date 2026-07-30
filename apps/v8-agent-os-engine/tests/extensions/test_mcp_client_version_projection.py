from types import SimpleNamespace

from runtimes.extensions.mcp.client import _initialization_metadata


def test_initialization_metadata_reads_camel_case_sdk_contract() -> None:
    initialization = SimpleNamespace(
        serverInfo=SimpleNamespace(name="Godot MCP", version="1.4.2"),
        protocolVersion="2025-06-18",
    )

    assert _initialization_metadata(initialization) == {
        "serverInfoName": "Godot MCP",
        "serverInfoVersion": "1.4.2",
        "protocolVersion": "2025-06-18",
    }


def test_initialization_metadata_reads_snake_case_sdk_contract() -> None:
    initialization = SimpleNamespace(
        server_info={"name": "Figma MCP", "version": "2.3.4"},
        protocol_version="2024-11-05",
    )

    assert _initialization_metadata(initialization) == {
        "serverInfoName": "Figma MCP",
        "serverInfoVersion": "2.3.4",
        "protocolVersion": "2024-11-05",
    }


def test_initialization_metadata_does_not_invent_missing_versions() -> None:
    assert _initialization_metadata(SimpleNamespace()) == {
        "serverInfoName": None,
        "serverInfoVersion": None,
        "protocolVersion": None,
    }
