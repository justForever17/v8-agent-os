import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from runtimes.extensions import runtime as module
from runtimes.plugin_manager.service import plugin_manager_service


def test_unchanged_route_survives_elapsed_time_but_not_input_or_authority_changes(monkeypatch):
    service = module.ExtensionsRuntimeService()
    context = {"session_id": "cache-test", "workspace_id": "first", "run_id": "audit-run"}
    inventory = {"items": [], "revision": "skills:1", "visibleRootSignature": "roots:1"}
    freshness = {"skillContext": {}, "dirtyVisibleRoots": []}
    configuration = {"prefilterPolicy": {"enabled": False}}
    grants = []
    now = [1.0]
    monkeypatch.setattr(service, "_resolve_event_context", lambda: dict(context))
    monkeypatch.setattr(service, "_apply_inventory_freshness_mode", lambda **_: dict(freshness))
    monkeypatch.setattr(service, "_resolve_skill_inventory", lambda **_: dict(inventory))
    monkeypatch.setattr(service, "_skill_inventory_status", lambda: {})
    monkeypatch.setattr(service, "_mcp_inventory_status", lambda: {"inventoryRevision": "mcp:1"})
    monkeypatch.setattr(service, "_resolve_prefilter_policy", lambda: configuration["prefilterPolicy"])
    monkeypatch.setattr(module.storage, "get_extensions_config", lambda: configuration)
    monkeypatch.setattr(module, "_ensure_extension_lexicon_state", lambda: {"signature": "lexicon:1"})
    monkeypatch.setattr(plugin_manager_service, "active_grants", lambda **_: list(grants))
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    selected = Mock(side_effect=lambda **kwargs: module.ExtensionRouteBundle(
        prompt_addition=kwargs["user_query"], filtered_tools=kwargs["available_tools"],
        selected_skill_names=[], selected_skill_ids=[], skill_root_descriptors=[],
        exposed_mcp_tool_names=[], candidate_summary={},
    ))
    monkeypatch.setattr(service, "build_contextual_route", selected)
    tools = [SimpleNamespace(name="read_native_file")]
    agents = [{"name": "reviewer", "role": "read-only"}]

    def route(query="inspect current document"):
        return service.build_supervisor_route(user_query=query, supervisor_tools=tools, loaded_agents=agents)

    first = route()
    now[0] += 3600
    hit = route()
    assert selected.call_count == 1
    assert hit.candidate_summary["routeCacheHit"] is True
    assert first.candidate_summary["routeCacheHit"] is False
    emitted = Mock()
    monkeypatch.setattr(service, "_emit", emitted)
    service.emit_route_selected(user_query="inspect current document", route_bundle=hit)
    emitted.assert_not_called()
    route("generate an animation")
    inventory["revision"] = "skills:2"
    route()
    configuration["prefilterPolicy"]["enabled"] = True
    route()
    agents[0]["role"] = "different task"  # Same count is not the same agent configuration.
    route()
    grants.append({"grantId": "grant-added"})
    route()
    grants.clear()
    # Returning to a prior exact key may reuse it: the revoked grant is absent.
    assert route().candidate_summary["routeCacheHit"] is True
    context["workspace_id"] = "another"
    route()
    assert selected.call_count == 7
    freshness["dirtyVisibleRoots"] = ["changed-root"]
    route()
    route()
    assert selected.call_count == 9  # No clean cached shortlist bypasses a pending invalidation.


@pytest.mark.parametrize("new_field_type", ["string", "integer"])
def test_mcp_reconnect_replaces_cached_session_bound_tool(monkeypatch, new_field_type):
    asyncio.run(_exercise_mcp_reconnect(monkeypatch, new_field_type))


async def _exercise_mcp_reconnect(monkeypatch, new_field_type):
    from langchain_mcp_adapters.tools import load_mcp_tools
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
    from runtimes.extensions.mcp.client import MCPManager

    class Session:
        def __init__(self, field_type):
            self.field_type, self.closed, self.calls = field_type, False, []

        async def list_tools(self, **_kwargs):
            return ListToolsResult(tools=[Tool(name="owned_probe", description="Owned offline probe",
                inputSchema={"type": "object", "properties": {"value": {"type": self.field_type}}, "required": ["value"]})])

        async def call_tool(self, name, arguments, **_kwargs):
            assert not self.closed, "cached MCP wrapper invoked the closed connection"
            self.calls.append(arguments)
            return CallToolResult(content=[TextContent(type="text", text="owned_success")])

    old_session, new_session = Session("string"), Session(new_field_type)
    old_tools, new_tools = await load_mcp_tools(old_session), await load_mcp_tools(new_session)
    manager = MCPManager()
    manager._server_config_fingerprints = {"owned": "same-config"}
    manager._server_state = {"owned": {"status": "connected"}}
    manager._server_tools = {"owned": old_tools}
    first_revision = manager._commit_inventory_revision()
    service = module.ExtensionsRuntimeService()
    monkeypatch.setattr(service, "_resolve_event_context", lambda: {"session_id": "owned-session", "run_id": "owned-run"})
    monkeypatch.setattr(service, "_apply_inventory_freshness_mode", lambda **_: {"skillContext": {}, "dirtyVisibleRoots": []})
    monkeypatch.setattr(service, "_resolve_skill_inventory", lambda **_: {"items": [], "revision": "skills:1", "visibleRootSignature": "roots:1"})
    monkeypatch.setattr(service, "_skill_inventory_status", lambda: {})
    monkeypatch.setattr(service, "_mcp_inventory_status", lambda: {"inventoryRevision": manager.get_inventory_revision()})
    monkeypatch.setattr(service, "_resolve_prefilter_policy", lambda: {"enabled": False})
    monkeypatch.setattr(module.storage, "get_extensions_config", lambda: {})
    monkeypatch.setattr(module, "_ensure_extension_lexicon_state", lambda: {"signature": "lexicon:1"})
    monkeypatch.setattr(plugin_manager_service, "active_grants", lambda **_: [])
    selected = Mock(side_effect=lambda **kwargs: module.ExtensionRouteBundle(
        prompt_addition="owned", filtered_tools=list(kwargs["available_tools"]), selected_skill_names=[],
        selected_skill_ids=[], skill_root_descriptors=[], exposed_mcp_tool_names=[], candidate_summary={}))
    monkeypatch.setattr(service, "build_contextual_route", selected)
    service.build_supervisor_route(user_query="owned probe", supervisor_tools=old_tools)
    assert manager._commit_inventory_revision() == first_revision
    assert service.build_supervisor_route(user_query="owned probe", supervisor_tools=old_tools).candidate_summary["routeCacheHit"]

    old_session.closed = True
    manager._server_tools = {"owned": new_tools}
    assert manager._commit_inventory_revision() != first_revision
    result = service.build_supervisor_route(user_query="owned probe", supervisor_tools=new_tools)
    assert result.candidate_summary["routeCacheHit"] is False
    assert result.filtered_tools[0] is new_tools[0]
    assert selected.call_count == 2
    value = 42 if new_field_type == "integer" else "synthetic"
    await result.filtered_tools[0].ainvoke({"value": value})
    assert new_session.calls == [{"value": value}]
    assert old_session.calls == []


def test_same_grant_id_with_reduced_scenario_components_invalidates_route(monkeypatch):
    scenario = {"scenario": "existing_project"}
    manifest = SimpleNamespace(id="owned", setupAdapter="godot_v1", cliProfiles=[], mcpServers=[], uiAdapters=[], providerAdapters=[],
        skills=[SimpleNamespace(id="general", setupScenarios=[], sourceKind="local"),
                SimpleNamespace(id="project-only", setupScenarios=["existing_project"], sourceKind="local")])
    monkeypatch.setattr(plugin_manager_service, "_manifest", lambda _id: manifest)
    monkeypatch.setattr(plugin_manager_service, "_setup_values", lambda _id: dict(scenario))
    monkeypatch.setattr(plugin_manager_service, "_component_rows", lambda _id: [{"component_id": item.id} for item in manifest.skills])
    grant = {"grantId": "same-session-grant", "componentIds": ["general", "project-only"], "pluginId": "owned",
             "scope": "session", "manifestDigest": "unchanged", "expiresAt": "2099-01-01T00:00:00Z"}
    active = Mock(side_effect=lambda **_: [plugin_manager_service._grant_with_grantable_components(grant)])
    monkeypatch.setattr(plugin_manager_service, "active_grants", active)
    context = {"session_id": "owned-session", "run_id": "first-run", "runtime_kind": "chat", "agent_id": "supervisor"}
    service = module.ExtensionsRuntimeService()
    monkeypatch.setattr(service, "_resolve_event_context", lambda: dict(context))
    monkeypatch.setattr(service, "_apply_inventory_freshness_mode", lambda **_: {"skillContext": {}, "dirtyVisibleRoots": []})
    monkeypatch.setattr(service, "_resolve_skill_inventory", lambda **_: {"items": [], "revision": "skills:1", "visibleRootSignature": "roots:1"})
    monkeypatch.setattr(service, "_skill_inventory_status", lambda: {})
    monkeypatch.setattr(service, "_mcp_inventory_status", lambda: {"inventoryRevision": "mcp:1"})
    monkeypatch.setattr(service, "_resolve_prefilter_policy", lambda: {"enabled": False})
    monkeypatch.setattr(module.storage, "get_extensions_config", lambda: {})
    monkeypatch.setattr(module, "_ensure_extension_lexicon_state", lambda: {"signature": "lexicon:1"})
    selected = Mock(side_effect=lambda **_: module.ExtensionRouteBundle(
        prompt_addition="owned", filtered_tools=[], selected_skill_names=[],
        selected_skill_ids=list(plugin_manager_service._grant_with_grantable_components(grant)["componentIds"]),
        skill_root_descriptors=[], exposed_mcp_tool_names=[], candidate_summary={}))
    monkeypatch.setattr(service, "build_contextual_route", selected)

    first = service.build_supervisor_route(user_query="inspect owned project", supervisor_tools=[])
    assert first.selected_skill_ids == ["general", "project-only"]
    assert service.build_supervisor_route(user_query="inspect owned project", supervisor_tools=[]).candidate_summary["routeCacheHit"]
    scenario["scenario"] = "new_project"
    context["run_id"] = "second-run"
    narrowed = service.build_supervisor_route(user_query="inspect owned project", supervisor_tools=[])
    assert narrowed.candidate_summary["routeCacheHit"] is False
    assert narrowed.selected_skill_ids == ["general"]
    assert selected.call_count == 2
    assert active.call_args.kwargs == {"session_id": "owned-session", "run_id": "second-run", "grantee_type": "supervisor",
                                      "grantee_id": "supervisor", "delegation_id": None}
