from __future__ import annotations

from core.tool_registry_index import build_tool_registry_index


def test_tool_registry_index_includes_native_runtime_and_virtual_tools():
    index = build_tool_registry_index()
    names = {str(item.get("canonicalToolName") or "") for item in index["items"]}

    assert "run_system_command" in names
    assert "workspace_broker" in names
    assert "research_broker" in names
    assert "ask_user" in names
    assert "fetch_skill_instructions" in names
    assert "vision_media_analyzer" in names
    assert index["count"] >= len(names)


def test_tool_registry_index_has_card_metadata():
    index = build_tool_registry_index()
    command = next(item for item in index["items"] if item.get("canonicalToolName") == "run_system_command")
    assert command["renderKind"] == "terminal"
    assert command["schemaHash"]
    assert command["origin"] in {"native", "runtime_grant"}

