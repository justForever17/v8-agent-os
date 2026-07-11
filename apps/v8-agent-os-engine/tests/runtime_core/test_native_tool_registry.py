from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core import native_tools
from core.tools.native.registry import (
    NATIVE_TOOL_NAMES,
    build_native_tools,
    native_tool_family_for_name,
    native_tool_manifest,
    native_tool_names,
)


def _tool(name: str):
    return SimpleNamespace(name=name)


def test_registry_builds_current_native_tools_in_order() -> None:
    exported_names = [str(getattr(tool_ref, "name", "")).strip() for tool_ref in native_tools.NATIVE_TOOLS]

    assert exported_names == list(NATIVE_TOOL_NAMES)
    assert exported_names == native_tool_names()
    assert exported_names[:8] == [
        "run_system_command",
        "command_session_broker",
        "runtime_broker",
        "delegation_broker",
        "request_peer_help",
        "session_context_broker",
        "session_message_broker",
        "mcp_server_config",
    ]
    assert exported_names[-4:] == ["ask_user", "write_todos", "update_todo", "vision_media_analyzer"]
    assert build_native_tools(vars(native_tools)) == native_tools.NATIVE_TOOLS


def test_registry_validation_fails_for_missing_invalid_mismatched_or_duplicate_tools() -> None:
    namespace = {name: _tool(name) for name in native_tool_names()}

    missing = dict(namespace)
    missing.pop("runtime_broker")
    with pytest.raises(ValueError, match="missing=runtime_broker"):
        build_native_tools(missing)

    invalid = dict(namespace)
    invalid["runtime_broker"] = SimpleNamespace()
    with pytest.raises(ValueError, match="invalid=runtime_broker"):
        build_native_tools(invalid)

    mismatched = dict(namespace)
    mismatched["runtime_broker"] = _tool("other_runtime_broker")
    with pytest.raises(ValueError, match="mismatched=runtime_broker->other_runtime_broker"):
        build_native_tools(mismatched)

    duplicate = dict(namespace)
    duplicate["delegation_broker"] = _tool("runtime_broker")
    with pytest.raises(ValueError, match="duplicates=runtime_broker"):
        build_native_tools(duplicate)


def test_every_exported_tool_has_family_and_manifest_is_data_only() -> None:
    manifest = native_tool_manifest(vars(native_tools))

    assert len(manifest) == len(native_tool_names())
    assert {item["name"] for item in manifest} == set(native_tool_names())
    assert json.loads(json.dumps(manifest, ensure_ascii=False)) == manifest

    for item in manifest:
        assert item["family"]
        assert item["actualName"] == item["name"]
        assert native_tool_family_for_name(item["name"]) == item["family"]
        assert all(not callable(value) for value in item.values())
        payload = json.dumps(item, ensure_ascii=False).lower()
        assert "secret" not in payload
        assert "api_key" not in payload
        assert "token" not in payload


def test_phase6_legacy_imports_remain_available() -> None:
    from core.native_tools import (
        grep_search,
        manage_cron,
        memory_broker,
        read_native_file,
        rpa_run_draft,
        workspace_broker,
        write_native_file,
    )

    assert read_native_file.name == "read_native_file"
    assert write_native_file.name == "write_native_file"
    assert workspace_broker.name == "workspace_broker"
    assert grep_search.name == "grep_search"
    assert manage_cron.name == "manage_cron"
    assert memory_broker.name == "memory_broker"
    assert rpa_run_draft.name == "rpa_run_draft"


def test_phase7_spec_and_todo_imports_remain_available() -> None:
    from core.tools.native.spec import spec_broker as spec_broker_from_module
    from core.tools.native.todo import update_todo as update_todo_from_module
    from core.tools.native.todo import write_todos as write_todos_from_module
    from core.native_tools import spec_broker, update_todo, write_todos

    assert spec_broker.name == "spec_broker"
    assert write_todos.name == "write_todos"
    assert update_todo.name == "update_todo"
    assert spec_broker_from_module.name == spec_broker.name
    assert write_todos_from_module.name == write_todos.name
    assert update_todo_from_module.name == update_todo.name
    assert native_tool_family_for_name("spec_broker") == "spec"
    assert native_tool_family_for_name("write_todos") == "governance"
    assert native_tool_family_for_name("update_todo") == "governance"


def test_phase8_delegation_imports_remain_available() -> None:
    from core.tools.native.delegation import delegation_broker as delegation_broker_from_module
    from core.native_tools import delegation_broker

    assert delegation_broker.name == "delegation_broker"
    assert delegation_broker_from_module.name == delegation_broker.name
    assert native_tool_family_for_name("delegation_broker") == "runtime"


def test_phase9_runtime_imports_remain_available() -> None:
    from core.tools.native.runtime import runtime_broker as runtime_broker_from_module
    from core.native_tools import runtime_broker

    assert runtime_broker.name == "runtime_broker"
    assert runtime_broker_from_module.name == runtime_broker.name
    assert native_tool_family_for_name("runtime_broker") == "runtime"


def test_session_context_imports_remain_available() -> None:
    from core.tools.native.session_context import session_context_broker as broker_from_module
    from core.native_tools import session_context_broker

    assert session_context_broker.name == "session_context_broker"
    assert broker_from_module.name == session_context_broker.name
    assert native_tool_family_for_name("session_context_broker") == "conversation_history"


def test_session_message_imports_remain_available() -> None:
    from core.tools.native.session_coordination import session_message_broker as broker_from_module
    from core.native_tools import session_message_broker

    assert session_message_broker.name == "session_message_broker"
    assert broker_from_module.name == session_message_broker.name
    assert native_tool_family_for_name("session_message_broker") == "conversation_coordination"


def test_mcp_config_imports_remain_available() -> None:
    from core.tools.native.mcp import mcp_server_config as mcp_server_config_from_module
    from core.native_tools import mcp_server_config

    assert mcp_server_config.name == "mcp_server_config"
    assert mcp_server_config_from_module.name == mcp_server_config.name
    assert native_tool_family_for_name("mcp_server_config") == "extensions"


def test_plugin_broker_import_remains_available() -> None:
    from core.tools.native.plugin import plugin_broker as plugin_broker_from_module
    from core.native_tools import plugin_broker

    assert plugin_broker.name == "plugin_broker"
    assert plugin_broker_from_module.name == plugin_broker.name
    assert native_tool_family_for_name("plugin_broker") == "extensions"


def test_phase6_memory_runtime_legacy_patch_path(monkeypatch) -> None:
    class _FakeMemoryRuntime:
        def unified_recall(self, *, query: str, limit: int, scope=None):
            return [{"id": "mem-patched", "scope": scope or "global", "category": "fixture", "text": f"patched {query}"}]

    monkeypatch.setattr(native_tools, "_get_memory_runtime", lambda: _FakeMemoryRuntime())

    payload = json.loads(native_tools.memory_broker.func(mode="recall", query="hello", limit=1))

    assert payload["items"][0]["id"] == "mem-patched"
    assert payload["items"][0]["text"] == "patched hello"


def test_phase6_rpa_runtime_legacy_patch_path(monkeypatch) -> None:
    class _FakeScriptStore:
        def list_robot_scripts(self, *, limit: int):
            return [{"name": "demo.robot", "path": "flows/demo.robot", "updatedAt": "2026-06-10", "size": 123}]

    class _FakeRpaRuntime:
        script_store = _FakeScriptStore()

    monkeypatch.setattr(native_tools, "_get_rpa_runtime", lambda: _FakeRpaRuntime())

    payload = json.loads(native_tools.rpa_list_robot_scripts.func(limit=1))

    assert payload["scripts"][0]["name"] == "demo.robot"
    assert payload["scripts"][0]["path"] == "flows/demo.robot"

