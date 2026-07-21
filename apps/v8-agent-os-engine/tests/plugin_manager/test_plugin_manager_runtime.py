from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from langchain_core.tools import tool

from core.database import DatabaseManager
from core.native_tools import plugin_broker
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from core.supervisor_tool_policy import build_supervisor_tool_policy_snapshot
from erc.runtime_context import bind_runtime_context
from runtimes.plugin_manager.catalog import plugin_catalog_service
from runtimes.plugin_manager.requirements import compile_plugin_requirements
from runtimes.plugin_manager.service import PluginManagerError, PluginManagerService
from runtimes.plugin_manager.schema import CommandSpec
from runtimes.plugin_manager.schema import CliAction, CliActionParameter
import runtimes.plugin_manager.service as service_module


class _TestStorage:
    def __init__(self, root: Path) -> None:
        self.plugin_config = {
            "installRoot": str(root / "plugins"),
            "binRoot": str(root / "bin"),
            "allowSessionGrant": True,
        }
        self.mcp_config: dict = {"mcpServers": {}}

    def get_plugin_manager_config(self) -> dict:
        return copy.deepcopy(self.plugin_config)

    def get_mcp_config(self) -> dict:
        return copy.deepcopy(self.mcp_config)

    def save_mcp_config(self, payload: dict) -> None:
        self.mcp_config = copy.deepcopy(payload)


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[PluginManagerService, DatabaseManager, _TestStorage]:
    test_db = DatabaseManager(tmp_path / "state.db")
    test_storage = _TestStorage(tmp_path)
    monkeypatch.setattr(service_module, "db", test_db)
    monkeypatch.setattr(service_module, "storage", test_storage)
    monkeypatch.setattr(service_module, "PLUGIN_MANAGER_ROOT", tmp_path / "plugins")
    monkeypatch.setattr(service_module, "PLUGIN_MANAGER_BIN_ROOT", tmp_path / "bin")
    monkeypatch.setattr(service_module, "PLUGIN_MANAGER_LOG_ROOT", tmp_path / "logs")
    monkeypatch.setattr(service_module, "AGENT_SKILLS_ROOT", tmp_path / "skills")
    monkeypatch.setattr(service_module.shutil, "which", lambda _command, path=None: None)
    test_db.create_or_update_session("s1", "Plugin test", user_id="user-1")
    service = PluginManagerService(credential_store=CredentialRefStore(MemoryCredentialBackend()))
    monkeypatch.setattr(
        service,
        "_skills_cli_inventory",
        lambda force=False: {
            "ok": True,
            "tool": service_module.SKILLS_CLI_PACKAGE,
            "items": [],
            "lockEntries": {},
            "error": "",
        },
    )
    monkeypatch.setattr(service, "_refresh_extensions", lambda: None)
    return service, test_db, test_storage


def _mark_ready(service: PluginManagerService, plugin_id: str) -> None:
    manifest = service._manifest(plugin_id)
    policy = service._component_policy(manifest)
    service._upsert_installation(
        manifest,
        state="installed",
        health={"ok": True, "online": True, "checks": []},
        external=False,
    )
    for profile in policy["cliProfiles"]:
        service._register_component(
            manifest.id,
            profile.id,
            "cli",
            source_url=manifest.officialLinks.documentation,
            source_version=manifest.version,
            ownership=profile.ownership,
        )
    for skill in policy["skills"]:
        skill_root = service_module.AGENT_SKILLS_ROOT / skill.targetDirectory
        skill_root.mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text("---\nname: test-plugin-skill\n---\n", encoding="utf-8")
        service._register_component(
            manifest.id,
            skill.id,
            "skill",
            owned_path=str(skill_root),
            source_url=skill.repository,
            source_version=skill.revision,
        )
    if policy["mcpServers"]:
        service._install_mcp_components(manifest, policy["mcpServers"])
    for adapter in manifest.uiAdapters:
        service._register_component(manifest.id, adapter.id, "ui_adapter")
    for adapter in manifest.providerAdapters:
        service._register_component(manifest.id, adapter.id, "provider_adapter")
    for requirement in compile_plugin_requirements(
        manifest,
        component_ids=policy["activeComponentIds"],
    ):
        if requirement.required and requirement.kind in {"secret", "oauth"}:
            secret_ref = service._bind_credential(manifest, requirement, f"test-{requirement.id}")
            if requirement.kind == "oauth":
                server = next(
                    (item for item in policy["mcpServers"] if item.id == requirement.componentId),
                    None,
                )
                if server is not None:
                    mcp_payload = service_module.storage.get_mcp_config()
                    server_config = dict((mcp_payload.get("mcpServers") or {}).get(server.serverName) or {})
                    server_config["x-v8-oauth"] = {
                        "secretRef": secret_ref,
                        "pluginId": manifest.id,
                        "componentId": server.id,
                    }
                    mcp_payload.setdefault("mcpServers", {})[server.serverName] = server_config
                    service_module.storage.save_mcp_config(mcp_payload)
    with service_module.db.get_connection() as conn:
        conn.execute(
            "UPDATE plugin_installations SET configured=1, online=1 WHERE plugin_id=?",
            (plugin_id,),
        )
        conn.commit()


def test_builtin_catalog_has_17_signed_curated_plugins(runtime) -> None:
    service, _, _ = runtime
    catalog = plugin_catalog_service.load()
    assert catalog.revision >= 1
    assert len(catalog.plugins) == 17
    assert len({plugin.id for plugin in catalog.plugins}) == 17
    assert {plugin.id for plugin in catalog.plugins} == {
        "aliyun-bailian", "volcengine", "lark", "cloudflare", "supabase",
        "vercel", "google-workspace", "github", "aws", "wordpress",
        "azure", "figma", "hyperframes", "stripe", "docker", "amap", "office-suite",
    }
    assert all(plugin.artifacts for plugin in catalog.plugins)
    assert all(service.verify_brand_asset(plugin)["ok"] for plugin in catalog.plugins)
    assert all(
        skill.officialOrganization.lower() in {item.lower() for item in plugin.officialOrganizations}
        for plugin in catalog.plugins
        for skill in plugin.skills
    )
    assert all(
        server.officialOrganization.lower() in {item.lower() for item in plugin.officialOrganizations}
        for plugin in catalog.plugins
        for server in plugin.mcpServers
    )


def test_component_policy_prefers_cli_then_mcp_then_skill_only(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    monkeypatch.setattr(service_module, "_platform_name", lambda: "windows")

    github_plan = service.build_install_plan("github")
    assert github_plan["componentPolicy"]["transport"] == "cli"
    assert [item["componentId"] for item in github_plan["steps"]["cli"]] == ["gh"]
    assert github_plan["steps"]["mcp"] == []
    assert "github-mcp" in github_plan["componentPolicy"]["skippedComponentIds"]

    figma_plan = service.build_install_plan("figma")
    assert figma_plan["componentPolicy"]["transport"] == "mcp"
    assert [item["id"] for item in figma_plan["steps"]["mcp"]] == ["figma-remote-mcp"]

    office_plan = service.build_install_plan("office-suite")
    assert office_plan["componentPolicy"]["transport"] == "skill_only"
    assert office_plan["steps"]["cli"] == []
    assert office_plan["steps"]["mcp"] == []
    assert [item["id"] for item in office_plan["steps"]["skills"]] == ["office-documents-skill"]


def test_machine_discovery_adopts_existing_cli_and_official_skill_without_claiming_user_mcp(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, test_storage = runtime
    monkeypatch.setattr(service_module, "_platform_name", lambda: "windows")
    monkeypatch.setattr(
        service,
        "_discover_cli_commands",
        lambda profile: {"gh": "C:/Program Files/GitHub CLI/gh.exe"} if profile.id == "gh" else {},
    )
    skill_root = service_module.AGENT_SKILLS_ROOT / "gh"
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text("---\nname: gh\n---\n", encoding="utf-8")
    monkeypatch.setattr(
        service,
        "_skills_cli_inventory",
        lambda force=False: {
            "ok": True,
            "tool": service_module.SKILLS_CLI_PACKAGE,
            "items": [{"name": "gh", "path": str(skill_root), "scope": "global", "agents": ["Codex"]}],
            "lockEntries": {
                "gh": {
                    "source": "cli/cli",
                    "sourceUrl": "https://github.com/cli/cli.git",
                    "skillPath": "skills/gh/SKILL.md",
                }
            },
            "error": "",
        },
    )
    test_storage.mcp_config["mcpServers"]["github"] = {
        "command": "docker",
        "args": ["run", "github-mcp"],
        "disabled": False,
    }

    discovery = service.discover_machine_components("github", force=True)
    assert discovery["cli"][0]["action"] == "adopt"
    assert discovery["skills"][0]["action"] == "adopt"
    assert discovery["ordinaryMcp"] == [
        {
            "componentId": "github-mcp",
            "serverName": "github",
            "enabled": True,
            "managedBy": "extensions_runtime",
            "note": "User-managed MCP configuration is not owned or modified by Plugin Manager.",
        }
    ]

    plan = service.build_install_plan("github")
    assert plan["installable"] is True
    assert plan["approvalRequired"] is False
    assert plan["steps"]["cli"][0]["action"] == "adopt"
    assert plan["steps"]["skills"][0]["action"] == "adopt"
    assert plan["steps"]["mcp"] == []
    assert test_storage.mcp_config["mcpServers"]["github"]["disabled"] is False
    assert "x-v8-plugin-owner" not in test_storage.mcp_config["mcpServers"]["github"]


def test_machine_discovery_surfaces_skill_name_conflict_instead_of_overwriting(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    monkeypatch.setattr(service_module, "_platform_name", lambda: "windows")
    skill_root = service_module.AGENT_SKILLS_ROOT / "gh"
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text("---\nname: gh\n---\n", encoding="utf-8")
    monkeypatch.setattr(
        service,
        "_skills_cli_inventory",
        lambda force=False: {
            "ok": True,
            "tool": service_module.SKILLS_CLI_PACKAGE,
            "items": [{"name": "gh", "path": str(skill_root), "scope": "global", "agents": ["Codex"]}],
            "lockEntries": {
                "gh": {
                    "source": "someone/other-gh-skill",
                    "sourceUrl": "https://github.com/someone/other-gh-skill.git",
                    "skillPath": "SKILL.md",
                }
            },
            "error": "",
        },
    )

    discovery = service.discover_machine_components("github")
    assert discovery["skills"][0]["state"] == "conflict"
    plan = service.build_install_plan("github")
    assert plan["installable"] is False
    assert "skill_name_conflict" in plan["componentPolicy"]["blockingReasons"]


def test_reviewed_official_skills_are_mandatory_cli_companions(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    monkeypatch.setattr(service_module, "_platform_name", lambda: "windows")
    expected = {
        "cloudflare": "cloudflare-wrangler-skill",
        "supabase": "supabase-official-skill",
        "vercel": "vercel-cli-skill",
        "github": "github-cli-skill",
        "aws": "aws-sign-in-skill",
        "wordpress": "wordpress-wpcli-skill",
    }
    for plugin_id, skill_id in expected.items():
        plan = service.build_install_plan(plugin_id)
        assert plan["componentPolicy"]["transport"] == "cli"
        assert plan["steps"]["mcp"] == []
        assert skill_id in {item["id"] for item in plan["steps"]["skills"]}


def test_office_plugin_is_pinned_skill_only_and_advertises_artifacts_on_demand(runtime) -> None:
    service, _, _ = runtime
    manifest = service._manifest("office-suite")
    skill = manifest.skills[0]

    assert manifest.cliProfiles == []
    assert manifest.mcpServers == []
    assert skill.repository == "https://github.com/jezweb/claude-skills"
    assert skill.path == "skills/office"
    assert skill.revision == "10a1f16679a5aab8e0c2f4d04e8560402f34d04b"
    assert service.supervisor_availability_prompt() == ""

    _mark_ready(service, "office-suite")
    prompt = service.supervisor_availability_prompt()
    assert "[Plugin Catalog]" in prompt
    assert "office-suite (ready)" in prompt
    assert "DOCX" in prompt and "XLSX/CSV" in prompt and "PDF" in prompt and "PPTX" in prompt
    assert "plugin_broker(status)" in prompt
    assert "SKILL.md" not in prompt
    assert "npm install" not in prompt

    healthy = asyncio.run(service.doctor("office-suite", persist=False))
    assert healthy["ok"] is True
    skill_root = service_module.AGENT_SKILLS_ROOT / skill.targetDirectory
    (skill_root / "SKILL.md").unlink()
    unhealthy = asyncio.run(service.doctor("office-suite", persist=False))
    assert unhealthy["ok"] is False
    assert unhealthy["checks"][0]["kind"] == "skill-file"


def test_amap_cli_contract_is_pinned_typed_and_openclaw_free(runtime) -> None:
    service, _, _ = runtime
    manifest = service._manifest("amap")
    profile = manifest.cliProfiles[0]

    assert profile.install.argv[-1] == "@amap-lbs/amap-gui@1.0.3"
    assert {item.targetName for item in profile.configRequirements} == {"AMAP_KEY", "AMAP_SECURITY_KEY"}
    assert all(item.kind == "secret" and item.source == "manifest" for item in profile.configRequirements)
    assert {item.id for item in profile.actions} == {
        "status",
        "get-last-event",
        "map-state-get",
        "map-state-set",
        "route",
        "search-poi",
    }
    route = next(item for item in profile.actions if item.id == "route")
    route_spec = service._build_cli_action_spec(
        manifest,
        profile,
        route,
        {"from": "北京站", "to": "中关村", "type": "transit", "city": "北京"},
    )
    assert route_spec.argv[-8:] == [
        "--from",
        "北京站",
        "--to",
        "中关村",
        "--type",
        "transit",
        "--city",
        "北京",
    ]
    with pytest.raises(PluginManagerError) as invalid_mode:
        service._build_cli_action_spec(
            manifest,
            profile,
            route,
            {"from": "北京站", "to": "中关村", "type": "flying"},
        )
    assert invalid_mode.value.code == "plugin_cli_parameter_invalid"
    assert len(manifest.skills) == 1
    bundled_skill = manifest.skills[0]
    assert bundled_skill.id == "amap-map-cli-skill"
    assert bundled_skill.sourceKind == "managed_cli"
    assert bundled_skill.sourceComponentId == profile.id
    assert bundled_skill.path == "node_modules/@amap-lbs/amap-gui/SKILL.md"
    assert bundled_skill.targetDirectory == "amap-map-cli"
    manifest_text = manifest.model_dump_json().lower()
    assert "openclaw" not in manifest_text
    assert "clawhub" not in manifest_text


def test_catalog_projection_cache_keeps_installation_state_live(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    service._catalog_projection_cache = None
    service._catalog_installation_cache = None
    digest_calls = 0
    original_digest = service._manifest_digest

    def counted_digest(manifest):
        nonlocal digest_calls
        digest_calls += 1
        return original_digest(manifest)

    installation_snapshots = iter(
        [
            {},
            {
                "github": {
                    "state": "installed",
                    "configured": 1,
                    "online": 1,
                    "external_ownership": 0,
                }
            },
        ]
    )
    monkeypatch.setattr(service, "_manifest_digest", counted_digest)
    monkeypatch.setattr(service, "_installation_rows", lambda: next(installation_snapshots))

    first = service.list_catalog()
    service._invalidate_catalog_installation_cache()
    second = service.list_catalog()

    assert digest_calls == len(plugin_catalog_service.load().plugins)
    assert next(plugin for plugin in first["plugins"] if plugin["id"] == "github")["installation"]["installed"] is False
    assert next(plugin for plugin in second["plugins"] if plugin["id"] == "github")["installation"]["installed"] is True


def test_all_plugins_have_safe_windows_dry_run_plans(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    monkeypatch.setattr(service_module, "_platform_name", lambda: "windows")
    for manifest in plugin_catalog_service.load().plugins:
        plan = service.build_install_plan(manifest.id)
        assert plan["pluginId"] == manifest.id
        assert plan["platform"] == "windows"
        assert plan["steps"]["preflight"] is True
        assert all(isinstance(step["argv"], list) and step["argv"] for step in plan["steps"]["cli"])
        job = service.create_install_job(manifest.id, dry_run=True)
        assert job["state"] == "planned"
        assert job["dryRun"] is True


def test_grants_are_explicit_scoped_revocable_and_terminal_after_grandchild(runtime) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")

    assert service.projection_for(session_id="s1", run_id="r1")["grants"] == []
    with pytest.raises(PluginManagerError) as implicit_all:
        service.create_grant(
            plugin_id="github",
            scope="task",
            session_id="s1",
            run_id="r1",
            component_ids=[],
        )
    assert implicit_all.value.code == "grant_components_required"
    parent = service.create_grant(
        plugin_id="github",
        scope="task",
        session_id="s1",
        run_id="r1",
        component_ids=["gh"],
    )
    assert service.active_grants(session_id="s1", run_id="r2") == []
    assert [item["id"] for item in service.projection_for(session_id="s1", run_id="r1")["cliProfiles"]] == ["gh"]

    child = service.delegate_grants_to_subagent(
        plugin_references=[{"pluginId": "github", "componentIds": ["gh"]}],
        session_id="s1",
        run_id="r1",
        subagent_id="child-1",
        delegation_id="delegation-child-1",
        delegation_depth=1,
    )[0]
    assert child["componentIds"] == ["gh"]
    with pytest.raises(PluginManagerError) as implicit_all:
        service.delegate_grants_to_subagent(
            plugin_references=[{"pluginId": "github", "componentIds": []}],
            session_id="s1",
            run_id="r1",
            subagent_id="child-implicit",
            delegation_id="delegation-child-implicit",
            delegation_depth=1,
        )
    assert implicit_all.value.code == "delegation_components_required"
    grandchild = service.delegate_grants_to_subagent(
        plugin_references=[{"pluginId": "github", "componentIds": ["gh"]}],
        session_id="s1",
        run_id="r1",
        subagent_id="grandchild",
        delegation_id="delegation-grandchild",
        delegation_depth=2,
        parent_agent_id="child-1",
        parent_delegation_id="delegation-child-1",
    )[0]
    assert grandchild["parentGrantId"] == child["grantId"]
    assert grandchild["delegationDepth"] == 2
    with pytest.raises(PluginManagerError, match="不能继续扩散") as denied:
        service.create_grant(
            plugin_id="github",
            scope="task",
            session_id="s1",
            run_id="r1",
            grantee_type="subagent",
            grantee_id="great-grandchild",
            component_ids=["gh"],
            parent_grant_id=grandchild["grantId"],
            delegation_id="delegation-great-grandchild",
            delegation_depth=2,
        )
    assert denied.value.code == "grant_transitive_denied"

    expired = service.expire_task_grants(run_id="r1", reason="test_run_completed")
    assert expired["expired"] == 3
    assert service.active_grants(session_id="s1", run_id="r1") == []

    service.revoke_grant(parent["grantId"])
    assert service.active_grants(session_id="s1", run_id="r1") == []

    session_grant = service.create_grant(
        plugin_id="github",
        scope="session",
        session_id="s1",
        run_id=None,
        component_ids=["gh"],
    )
    assert session_grant["scope"] == "session"
    assert service.active_grants(session_id="s1", run_id="later-run")


def test_supervisor_can_authorize_ready_plugin_without_user_mention_and_delegate_subset(
    runtime,
) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")

    catalog = service.supervisor_catalog(plugin_id="github", session_id="s1", run_id="r1")
    item = catalog["items"][0]
    assert item["ready"] is True
    assert item["authorized"] is False
    assert {component["id"] for component in item["components"]} >= {"gh"}

    result = service.authorize_for_supervisor(
        plugin_id="github",
        component_ids=["gh"],
        session_id="s1",
        run_id="r1",
    )
    assert result["status"] == "authorized"
    assert result["grant"]["source"] == "supervisor_task"
    assert result["grant"]["scope"] == "task"

    child = service.delegate_grants_to_subagent(
        plugin_references=[{"pluginId": "github", "componentIds": ["gh"]}],
        session_id="s1",
        run_id="r1",
        subagent_id="child-1",
        delegation_id="delegation-child-1",
        delegation_depth=1,
    )[0]
    assert child["source"] == "delegation"
    assert child["parentGrantId"] == result["grant"]["grantId"]


def test_plugin_broker_authorizes_with_supervisor_and_projects_exact_subagent_grant(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    monkeypatch.setattr(service_module, "plugin_manager_service", service)
    locked_names = {
        item["name"]
        for item in build_supervisor_tool_policy_snapshot(None)["lockedNativeTools"]
    }
    assert "plugin_broker" in locked_names

    with bind_runtime_context(session_id="s1", run_id="r1", agent_id="supervisor", runtime_kind="chat"):
        output = asyncio.run(
            plugin_broker.coroutine(
                mode="authorize",
                plugin_id="github",
                component_ids=["gh"],
                tool_call_id="tool-plugin-authorize",
            )
        )
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["grant"]["source"] == "supervisor_task"

    child_grant = service.delegate_grants_to_subagent(
        plugin_references=[{"pluginId": "github", "componentIds": ["gh"]}],
        session_id="s1",
        run_id="r1",
        subagent_id="child-1",
        delegation_id="delegation-child-1",
        delegation_depth=1,
    )[0]

    with bind_runtime_context(
        session_id="s1",
        run_id="r1",
        agent_id="child-1",
        runtime_kind="subagent",
        delegation_id="delegation-child-1",
        delegation_depth=1,
    ):
        child_output = asyncio.run(
            plugin_broker.coroutine(
                mode="list",
                plugin_id="",
                component_ids=None,
                tool_call_id="tool-plugin-denied",
            )
        )
    child_payload = json.loads(child_output)
    assert child_payload["ok"] is True
    assert child_payload["count"] == 1
    assert child_payload["items"][0]["grantId"] == child_grant["grantId"]

    with bind_runtime_context(
        session_id="s1",
        run_id="r1",
        agent_id="child-1",
        runtime_kind="subagent",
        delegation_id="delegation-other",
        delegation_depth=1,
    ):
        isolated_output = asyncio.run(
            plugin_broker.coroutine(mode="list", plugin_id="", component_ids=None, tool_call_id="tool-plugin-isolated")
        )
    assert json.loads(isolated_output)["count"] == 0

    with bind_runtime_context(
        session_id="s1",
        run_id="r1",
        agent_id="child-1",
        runtime_kind="subagent",
        delegation_id="delegation-child-1",
        delegation_depth=1,
    ):
        denied_output = asyncio.run(
            plugin_broker.coroutine(
                mode="authorize",
                plugin_id="github",
                component_ids=["gh"],
                tool_call_id="tool-plugin-denied",
            )
        )
    assert json.loads(denied_output)["error"]["code"] == "plugin_authorize_supervisor_only"


def test_cli_requires_exact_grant_and_structured_manifest_action(runtime) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    with pytest.raises(PluginManagerError) as missing:
        asyncio.run(service.execute_cli(
            plugin_id="github",
            profile_id="gh",
            action_id="help",
            parameters={},
            session_id="s1",
            run_id="r1",
        ))
    assert missing.value.code == "plugin_cli_not_granted"

    service.create_grant(
        plugin_id="github",
        scope="task",
        session_id="s1",
        run_id="r1",
        component_ids=["gh"],
    )
    with pytest.raises(PluginManagerError) as denied:
        asyncio.run(service.execute_cli(
            plugin_id="github",
            profile_id="gh",
            action_id="totally-undeclared-command",
            parameters={},
            session_id="s1",
            run_id="r1",
        ))
    assert denied.value.code == "plugin_cli_action_unsupported"


def test_failed_install_rolls_back_owned_state(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, test_db, _ = runtime
    monkeypatch.setattr(service_module, "_platform_name", lambda: "windows")
    monkeypatch.setattr(service, "_execute_spec", lambda *_args, **_kwargs: {
        "argv": ["npm"], "returnCode": 0, "stdoutTail": "ok", "stderrTail": "", "durationMs": 1,
    })

    def fail_skill(*_args, **_kwargs):
        raise PluginManagerError("synthetic skill failure", code="skill_install_failed")

    monkeypatch.setattr(service, "_install_skill_component", fail_skill)
    plan = service.build_install_plan("aliyun-bailian")
    job = service.create_install_job(
        "aliyun-bailian",
        dry_run=False,
        approved=True,
        plan_digest=plan["planDigest"],
    )
    result = asyncio.run(service.run_install_job(job["jobId"]))
    assert result["state"] == "rolled_back"
    assert result["result"]["rollback"]["ok"] is True
    with test_db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM plugin_installations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM plugin_components").fetchone()[0] == 0
    assert not service._plugin_root("aliyun-bailian").exists()


def test_uninstall_stops_when_owned_mcp_config_drifted(runtime) -> None:
    service, _, test_storage = runtime
    manifest = service._manifest("figma")
    service._upsert_installation(
        manifest,
        state="installed",
        health={"ok": True, "online": True, "checks": []},
        external=False,
    )
    service._install_mcp_components(manifest)
    test_storage.mcp_config["mcpServers"]["figma"]["url"] = "https://example.invalid/changed"
    with pytest.raises(PluginManagerError) as drift:
        service.uninstall("figma")
    assert drift.value.code == "component_hash_drift"


def test_uninstall_stops_when_owned_file_was_modified(runtime) -> None:
    service, _, _ = runtime
    manifest = service._manifest("figma")
    service._upsert_installation(
        manifest,
        state="installed",
        health={"ok": True, "online": True, "checks": []},
        external=False,
    )
    owned = service._plugin_root("figma") / "adapter.json"
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_text('{"version":1}', encoding="utf-8")
    service._register_component("figma", "test-owned-file", "ui_adapter", owned_path=str(owned))
    owned.write_text('{"version":2,"userModified":true}', encoding="utf-8")
    with pytest.raises(PluginManagerError) as drift:
        service.uninstall("figma")
    assert drift.value.code == "component_hash_drift"


def test_managed_download_verifies_hash_before_commit(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    manifest = service._manifest("wordpress")
    content = b"verified fixture"
    target = service._plugin_root("wordpress") / "fixture.bin"

    class _Response:
        def __init__(self, body: bytes) -> None:
            self.content = body

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: _Response(content))
    valid = CommandSpec(
        argv=["v8-managed-download"],
        downloadUrl="https://github.com/wp-cli/wp-cli/releases/download/v2.12.0/wp-cli-2.12.0.phar",
        downloadTarget="{pluginRoot}/fixture.bin",
        downloadSha256=hashlib.sha256(content).hexdigest(),
    )
    assert service._execute_spec(manifest, valid)["returnCode"] == 0
    assert target.read_bytes() == content

    target.unlink()
    invalid = valid.model_copy(update={"downloadSha256": "0" * 64})
    result = service._execute_spec(manifest, invalid)
    assert result["returnCode"] == 1
    assert "mismatch" in result["stderrTail"]
    assert not target.exists()


def test_cli_configuration_requirements_store_secret_refs_without_plaintext(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, test_storage = runtime
    manifest = service._manifest("amap")
    service._upsert_installation(
        manifest,
        state="installed",
        health={"ok": True, "online": True, "checks": []},
        external=False,
    )
    service._register_component(
        manifest.id,
        "amap-gui-cli",
        "cli",
        source_url=manifest.officialLinks.documentation,
        source_version=manifest.version,
    )
    before = service.configuration_requirements("amap")
    requirements = [item for item in before["requirements"] if item["kind"] == "secret"]
    assert len(requirements) == 2
    assert all(item["status"] == "missing" and "secretRef" not in item for item in requirements)

    secret = "github-secret-must-never-leak"
    values = {item["id"]: f"{secret}-{index}" for index, item in enumerate(requirements)}
    result = asyncio.run(service.configure("amap", values))
    assert result["configuration"]["configured"] is True
    serialized = __import__("json").dumps({"mcp": test_storage.mcp_config, "plugin": test_storage.plugin_config})
    assert secret not in serialized
    bindings = service._credential_bindings("amap")
    assert all(bindings[item["id"]]["secret_ref"].startswith("cred:v8-plugin:") for item in requirements)
    requirement = requirements[0]

    monkeypatch.setenv(str(requirement["targetName"]), "explicit-import-secret")
    detected = service.detect_configuration_sources("amap")
    detected_requirement = next(item for item in detected["requirements"] if item["id"] == requirement["id"])
    assert detected_requirement["availableForImport"] is True
    imported = asyncio.run(
        service.import_configuration_source(
            "amap",
            requirement_id=requirement["id"],
            source_id=f"env:{requirement['targetName']}",
        )
    )
    assert imported == {
        "ok": True,
        "pluginId": "amap",
        "requirementId": requirement["id"],
        "status": "configured",
    }
    assert "explicit-import-secret" not in __import__("json").dumps(service.list_events(plugin_id="amap"))


def test_install_plan_digest_idempotency_and_step_journal(runtime) -> None:
    service, _, _ = runtime
    plan = service.build_install_plan("hyperframes")
    with pytest.raises(PluginManagerError) as stale:
        service.create_install_job(
            "hyperframes",
            dry_run=False,
            approved=True,
            plan_digest="stale",
        )
    assert stale.value.code == "installation_approval_required"

    first = service.create_install_job(
        "hyperframes",
        dry_run=False,
        approved=True,
        plan_digest=plan["planDigest"],
        idempotency_key="install-hyperframes-once",
    )
    second = service.create_install_job(
        "hyperframes",
        dry_run=False,
        approved=True,
        plan_digest=plan["planDigest"],
        idempotency_key="install-hyperframes-once",
    )
    assert second["jobId"] == first["jobId"]
    assert first["steps"][0]["type"] == "plan"
    assert first["planDigest"] == plan["planDigest"]


def test_concurrent_job_creation_allows_only_one_active_transaction(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, test_db, _ = runtime
    original_uuid4 = service_module.uuid.uuid4

    # Widen the check/insert window after the active-job query. Without a
    # per-plugin critical section both callers can observe "no active job".
    monkeypatch.setattr(service_module.uuid, "uuid4", lambda: (time.sleep(0.05), original_uuid4())[1])
    start = threading.Barrier(3)

    def create() -> dict:
        start.wait()
        plan = service.build_install_plan("hyperframes")
        return service.create_install_job(
            "hyperframes",
            dry_run=False,
            approved=True,
            plan_digest=plan["planDigest"],
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create) for _ in range(2)]
        start.wait()
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=5))
            except PluginManagerError as exc:
                outcomes.append(exc)

    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert any(
        isinstance(item, PluginManagerError) and item.code == "plugin_install_in_progress"
        for item in outcomes
    )
    with test_db.get_connection() as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM plugin_install_jobs WHERE plugin_id='hyperframes' AND dry_run=0"
        ).fetchone()[0]
    assert active == 1


def test_run_install_job_is_single_claim_and_external_installer_runs_once(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    monkeypatch.setattr(service_module, "_platform_name", lambda: "windows")
    calls = 0
    calls_lock = threading.Lock()

    def execute(*_args, **_kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return {"argv": ["winget"], "returnCode": 0, "stdoutTail": "ok", "stderrTail": "", "durationMs": 1}

    async def doctor(*_args, **_kwargs):
        return {"ok": True, "online": True, "checks": []}

    monkeypatch.setattr(service, "_execute_spec", execute)
    monkeypatch.setattr(service, "_execute_elevated_spec", execute)
    monkeypatch.setattr(service, "doctor", doctor)
    monkeypatch.setattr(
        service,
        "_install_skill_component",
        lambda manifest, skill: [
            service._register_component(
                manifest.id,
                str(skill["id"]),
                "skill",
                source_url=str(skill["repository"]),
                source_version=str(skill["revision"]),
            )
        ],
    )
    monkeypatch.setattr(
        service,
        "_discover_cli_commands",
        lambda _profile: {"gh": "C:/Program Files/GitHub CLI/gh.exe"} if calls else {},
    )
    plan = service.build_install_plan("github")
    job = service.create_install_job(
        "github",
        dry_run=False,
        approved=True,
        plan_digest=plan["planDigest"],
    )

    async def run_twice():
        return await asyncio.gather(
            service.run_install_job(job["jobId"]),
            service.run_install_job(job["jobId"]),
        )

    first, second = asyncio.run(run_twice())
    assert first["state"] == "ready"
    assert second["state"] in {
        "staging", "verifying", "installing", "waiting_for_elevation",
        "reconciling", "validating", "committing", "ready",
    }
    assert calls == 1


def test_install_journal_uses_declared_state_order(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    monkeypatch.setattr(service_module, "_platform_name", lambda: "windows")
    monkeypatch.setattr(service, "_execute_spec", lambda *_args, **_kwargs: {
        "argv": ["npm"], "returnCode": 0, "stdoutTail": "ok", "stderrTail": "", "durationMs": 1,
    })
    monkeypatch.setattr(service, "_install_skill_component", lambda *_args, **_kwargs: [])

    async def doctor(*_args, **_kwargs):
        return {"ok": True, "online": True, "checks": []}

    monkeypatch.setattr(service, "doctor", doctor)
    plan = service.build_install_plan("aliyun-bailian")
    job = service.create_install_job(
        "aliyun-bailian",
        dry_run=False,
        approved=True,
        plan_digest=plan["planDigest"],
    )
    result = asyncio.run(service.run_install_job(job["jobId"]))
    states = [step["state"] for step in result["steps"]]
    expected = ["awaiting_approval", "staging", "verifying", "installing", "validating", "committing", "ready"]
    positions = [states.index(state) for state in expected]
    assert positions == sorted(positions)


def test_restart_does_not_reconcile_a_job_that_has_not_started(runtime) -> None:
    service, _, _ = runtime
    plan = service.build_install_plan("github")
    job = service.create_install_job(
        "github",
        dry_run=False,
        approved=True,
        plan_digest=plan["planDigest"],
    )
    restarted = PluginManagerService(credential_store=CredentialRefStore(MemoryCredentialBackend()))
    assert restarted.get_install_job(job["jobId"])["state"] == "awaiting_approval"


def test_idempotency_key_rejects_changed_plan(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    plan = service.build_install_plan("hyperframes")
    service.create_install_job(
        "hyperframes",
        dry_run=False,
        approved=True,
        plan_digest=plan["planDigest"],
        idempotency_key="same-request",
    )
    changed = copy.deepcopy(plan)
    changed["catalogRevision"] += 1
    changed["planDigest"] = service_module._hash_value({key: value for key, value in changed.items() if key != "planDigest"})
    monkeypatch.setattr(service, "build_install_plan", lambda _plugin_id: copy.deepcopy(changed))
    with pytest.raises(PluginManagerError) as conflict:
        service.create_install_job(
            "hyperframes",
            dry_run=False,
            approved=True,
            plan_digest=changed["planDigest"],
            idempotency_key="same-request",
        )
    assert conflict.value.code == "plugin_install_idempotency_conflict"


def test_oauth_requirement_cannot_be_manually_filled_and_uses_oauth_ref(runtime) -> None:
    service, _, test_storage = runtime
    manifest = service._manifest("figma")
    service._upsert_installation(
        manifest,
        state="installed",
        health={"ok": True, "online": True, "checks": []},
        external=False,
    )
    service._install_mcp_components(manifest)
    requirement = service.configuration_requirements("figma")["requirements"][0]
    assert requirement["kind"] == "oauth"
    assert requirement["status"] == "missing"
    with pytest.raises(PluginManagerError) as manual:
        asyncio.run(service.configure("figma", {requirement["id"]: "manual-bearer-token"}))
    assert manual.value.code == "oauth_browser_flow_required"

    secret_ref = service._credential_store.put('{"tokens":{"access_token":"oauth-token","token_type":"bearer"}}')
    test_storage.mcp_config["mcpServers"]["figma"]["x-v8-oauth"] = {
        "secretRef": secret_ref,
        "pluginId": "figma",
        "componentId": "figma-remote-mcp",
    }
    configured = service.configuration_requirements("figma")
    assert configured["configured"] is True
    assert configured["requirements"][0]["status"] == "configured"
    assert "secretRef" not in configured["requirements"][0]


def test_restart_reconcile_never_replays_external_install(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    plan = service.build_install_plan("github")
    job = service.create_install_job(
        "github",
        dry_run=False,
        approved=True,
        plan_digest=plan["planDigest"],
    )
    with service_module.db.get_connection() as conn:
        conn.execute("UPDATE plugin_install_jobs SET state='installing' WHERE id=?", (job["jobId"],))
        conn.commit()
    restarted = PluginManagerService(credential_store=CredentialRefStore(MemoryCredentialBackend()))
    reconciled = restarted.get_install_job(job["jobId"])
    assert reconciled["state"] == "external_reconciliation_required"
    assert reconciled["externalReconciliation"] is True
    monkeypatch.setattr(
        restarted,
        "_execute_spec",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("external installer replayed")),
    )
    rerun = asyncio.run(restarted.run_install_job(job["jobId"]))
    assert rerun["state"] == "external_reconciliation_required"


def test_grant_invocation_revalidates_owner_component_digest_and_session_revoke(runtime) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    readiness = service.readiness_status("github")
    assert readiness["status"] == "ready"
    assert readiness["canAuthorize"] is True
    before_grant = service.authorization_status("github", session_id="s1", run_id="r1")
    assert before_grant["status"] == "invalid"
    assert before_grant["reason"] == "grant_missing"
    grant = service.create_grant(
        plugin_id="github",
        scope="task",
        session_id="s1",
        run_id="r1",
        component_ids=["gh"],
    )
    validated = service.validate_grant_for_invocation(
        grant_id=grant["grantId"],
        plugin_id="github",
        component_id="gh",
        session_id="s1",
        run_id="r1",
        grantee_type="supervisor",
        grantee_id="supervisor",
    )
    assert validated["ownerUserId"] == "user-1"
    assert validated["manifestDigest"]
    assert service.authorization_status("github", session_id="s1", run_id="r1")["status"] == "authorized"
    with pytest.raises(PluginManagerError) as denied:
        service.validate_grant_for_invocation(
            grant_id=grant["grantId"],
            plugin_id="github",
            component_id="github-mcp",
            session_id="s1",
            run_id="r1",
            grantee_type="supervisor",
            grantee_id="supervisor",
        )
    assert denied.value.code == "plugin_grant_component_denied"
    revoked = service.revoke_session_grants("s1")
    assert revoked["revoked"] == 1
    with pytest.raises(PluginManagerError) as inactive:
        service.validate_grant_for_invocation(
            grant_id=grant["grantId"],
            plugin_id="github",
            component_id="gh",
            session_id="s1",
            run_id="r1",
            grantee_type="supervisor",
            grantee_id="supervisor",
        )
    assert inactive.value.code == "plugin_grant_inactive"


def test_subagent_plugin_invocation_requires_exact_delegation_identity(runtime) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    service.create_grant(
        plugin_id="github",
        scope="task",
        session_id="s1",
        run_id="r1",
        component_ids=["gh"],
    )
    child = service.delegate_grants_to_subagent(
        plugin_references=[{"pluginId": "github", "componentIds": ["gh"]}],
        session_id="s1",
        run_id="r1",
        subagent_id="child-1",
        delegation_id="delegation-child-1",
        delegation_depth=1,
    )[0]
    assert service.active_grants(
        session_id="s1",
        run_id="r1",
        grantee_type="subagent",
        grantee_id="child-1",
    ) == []

    validated = service.validate_grant_for_invocation(
        grant_id=child["grantId"],
        plugin_id="github",
        component_id="gh",
        session_id="s1",
        run_id="r1",
        grantee_type="subagent",
        grantee_id="child-1",
        delegation_id="delegation-child-1",
        delegation_depth=1,
    )
    assert validated["delegationId"] == "delegation-child-1"
    assert validated["delegationDepth"] == 1

    with pytest.raises(PluginManagerError) as wrong_delegation:
        service.validate_grant_for_invocation(
            grant_id=child["grantId"],
            plugin_id="github",
            component_id="gh",
            session_id="s1",
            run_id="r1",
            grantee_type="subagent",
            grantee_id="child-1",
            delegation_id="delegation-other",
            delegation_depth=1,
        )
    assert wrong_delegation.value.code == "plugin_grant_delegation_mismatch"

    with pytest.raises(PluginManagerError) as wrong_depth:
        service.validate_grant_for_invocation(
            grant_id=child["grantId"],
            plugin_id="github",
            component_id="gh",
            session_id="s1",
            run_id="r1",
            grantee_type="subagent",
            grantee_id="child-1",
            delegation_id="delegation-child-1",
            delegation_depth=2,
        )
    assert wrong_depth.value.code == "plugin_grant_depth_mismatch"


def test_structured_cli_action_rejects_unknown_and_extra_parameters(runtime) -> None:
    service, _, _ = runtime
    manifest = service._manifest("github")
    profile = manifest.cliProfiles[0]
    action = CliAction(
        id="repo-view",
        argv=["gh", "repo", "view"],
        parameters=[CliActionParameter(name="json", kind="boolean", flag="--json")],
    )
    spec = service._build_cli_action_spec(manifest, profile, action, {"json": True})
    assert spec.argv[-1] == "--json"
    with pytest.raises(PluginManagerError) as unknown:
        service._build_cli_action_spec(manifest, profile, action, {"token": "must-not-be-accepted"})
    assert unknown.value.code == "plugin_cli_parameter_unknown"


def test_projection_never_exposes_raw_mcp_tool(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    _mark_ready(service, "figma")
    monkeypatch.setattr(service._manifest("figma").mcpServers[0], "allowedTools", ["get_repository"])
    service.create_grant(
        plugin_id="figma",
        scope="task",
        session_id="s1",
        run_id="r1",
        component_ids=["figma-remote-mcp"],
    )

    @tool
    async def get_repository(name: str) -> str:
        """Read a repository."""
        return name

    get_repository.metadata = {"server_name": "figma"}
    from runtimes.extensions.mcp.client import mcp_manager

    monkeypatch.setattr(mcp_manager, "get_tools", lambda: [get_repository])
    projection = service.projection_for(session_id="s1", run_id="r1")
    assert len(projection["mcpTools"]) == 1
    guarded = projection["mcpTools"][0]
    assert guarded is not get_repository
    assert guarded.name == "plugin__figma__get_repository"
    assert guarded.metadata["plugin_grant_id"]


def test_skill_install_fetches_and_verifies_exact_commit(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    manifest = service._manifest("aliyun-bailian")
    skill = manifest.skills[0].model_dump(mode="json")
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        command = [str(item) for item in argv]
        calls.append((command, dict(kwargs)))
        if command[:2] == ["git", "init"]:
            source_root = Path(command[2]) / skill["path"]
            source_root.mkdir(parents=True, exist_ok=True)
            (source_root / "SKILL.md").write_text("---\nname: bailian-cli\n---\n# official skill\n", encoding="utf-8")
        if command[-2:] == ["rev-parse", "HEAD"]:
            kwargs["stdout"].write((skill["revision"] + "\n").encode("utf-8"))
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(service_module.subprocess, "run", fake_run)
    inventory = {"ok": True, "tool": service_module.SKILLS_CLI_PACKAGE, "items": [], "lockEntries": {}, "error": ""}

    def fake_skills_cli(arguments, **_kwargs):
        assert arguments[:2] == ["add", arguments[1]]
        target = service_module.AGENT_SKILLS_ROOT / "bailian-cli"
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text("---\nname: bailian-cli\n---\n", encoding="utf-8")
        inventory["items"] = [{"name": "bailian-cli", "path": str(target), "scope": "global", "agents": ["Codex"]}]
        return {"returnCode": 0, "stdoutTail": "installed", "stderrTail": ""}

    monkeypatch.setattr(service, "_skills_cli_inventory", lambda force=False: copy.deepcopy(inventory))
    monkeypatch.setattr(service, "_run_skills_cli", fake_skills_cli)
    monkeypatch.setattr(
        service,
        "_register_component",
        lambda _plugin_id, component_id, component_type, **_kwargs: {
            "id": component_id,
            "type": component_type,
        },
    )
    components = service._install_skill_component(manifest, skill)

    assert [item["id"] for item in components] == [skill["id"]]
    commands = [item[0] for item in calls]
    repo_root = commands[0][2]
    assert commands == [
        ["git", "init", repo_root],
        ["git", "-C", repo_root, "remote", "add", "origin", skill["repository"]],
        ["git", "-C", repo_root, "fetch", "--depth", "1", "--no-tags", "origin", skill["revision"]],
        ["git", "-C", repo_root, "checkout", "--detach", "FETCH_HEAD"],
        ["git", "-C", repo_root, "rev-parse", "HEAD"],
    ]
    assert all(kwargs["shell"] is False for _, kwargs in calls)
    assert all(kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0" for _, kwargs in calls)
    assert not any("clone" in command or "--branch" in command for command in commands)
    assert (service_module.AGENT_SKILLS_ROOT / "bailian-cli" / "SKILL.md").is_file()


def test_managed_cli_skill_is_projected_as_generic_skill_resource(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("amap")
    skill = manifest.skills[0].model_dump(mode="json")
    source = service._plugin_root(manifest.id) / skill["path"]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("---\nname: amap-map-cli\n---\n# AMap Map CLI\n", encoding="utf-8")
    captured: dict = {}
    inventory = {"ok": True, "tool": service_module.SKILLS_CLI_PACKAGE, "items": [], "lockEntries": {}, "error": ""}

    def register(_plugin_id, component_id, component_type, **kwargs):
        captured.update(kwargs)
        return {"id": component_id, "type": component_type}

    monkeypatch.setattr(service, "_register_component", register)
    monkeypatch.setattr(
        service,
        "_run_skill_git_step",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("managed CLI Skill must not call Git")),
    )
    def fake_skills_cli(_arguments, **_kwargs):
        target = service_module.AGENT_SKILLS_ROOT / "amap-map-cli"
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        inventory["items"] = [{"name": "amap-map-cli", "path": str(target), "scope": "global", "agents": ["Codex"]}]
        return {"returnCode": 0, "stdoutTail": "installed", "stderrTail": ""}
    monkeypatch.setattr(service, "_skills_cli_inventory", lambda force=False: copy.deepcopy(inventory))
    monkeypatch.setattr(service, "_run_skills_cli", fake_skills_cli)

    components = service._install_skill_component(manifest, skill)

    target = service_module.AGENT_SKILLS_ROOT / "amap-map-cli" / "SKILL.md"
    assert components == [{"id": "amap-map-cli-skill", "type": "skill"}]
    assert "# AMap Map CLI" in target.read_text(encoding="utf-8")
    assert captured["source_version"] == "1.0.3"
    assert captured["ownership"] == "skills_cli"
    assert captured["metadata"]["installer"] == service_module.SKILLS_CLI_PACKAGE
    assert captured["metadata"]["managedSkillNames"] == ["amap-map-cli"]


def test_managed_cli_skills_cli_failure_leaves_no_partial_projection(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("amap")
    skill = manifest.skills[0].model_dump(mode="json")
    source = service._plugin_root(manifest.id) / skill["path"]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("---\nname: amap-map-cli\n---\n# AMap Map CLI\n", encoding="utf-8")
    monkeypatch.setattr(
        service,
        "_run_skills_cli",
        lambda *_args, **_kwargs: {"returnCode": 1, "stdoutTail": "", "stderrTail": "synthetic skills failure"},
    )

    with pytest.raises(PluginManagerError, match="synthetic skills failure") as failure:
        service._install_skill_component(manifest, skill)

    assert failure.value.code == "skill_install_failed"
    assert not (service_module.AGENT_SKILLS_ROOT / "amap-map-cli").exists()


def test_skills_cli_removal_deletes_the_global_canonical_skill(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    captured: dict[str, object] = {}

    def fake_skills_cli(arguments, **kwargs):
        captured["arguments"] = list(arguments)
        captured["timeout"] = kwargs.get("timeout_seconds")
        return {"returnCode": 0, "stdoutTail": "", "stderrTail": ""}

    monkeypatch.setattr(service, "_run_skills_cli", fake_skills_cli)
    result = service._remove_skills_cli_names(["gh"])

    assert result == {"ok": True, "removed": ["gh"], "error": ""}
    assert captured["arguments"] == ["remove", "gh", "--global", "--yes"]
    assert captured["timeout"] == 300


@pytest.mark.parametrize("failed_step", ["fetch", "checkout"])
def test_skill_install_failure_cleans_staging(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_step: str,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("aliyun-bailian")
    skill = manifest.skills[0].model_dump(mode="json")
    staging_root = tmp_path / f"staging-{failed_step}"

    def fake_mkdtemp(**_kwargs):
        staging_root.mkdir(parents=True, exist_ok=False)
        return str(staging_root)

    def fake_run(argv, **kwargs):
        command = [str(item) for item in argv]
        if command[:2] == ["git", "init"]:
            Path(command[2]).mkdir(parents=True, exist_ok=True)
        if failed_step in command:
            kwargs["stderr"].write(f"synthetic {failed_step} failure".encode("utf-8"))
            return type("Completed", (), {"returncode": 1})()
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(service_module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(service_module.subprocess, "run", fake_run)

    with pytest.raises(PluginManagerError) as failure:
        service._install_skill_component(manifest, skill)
    assert failure.value.code == "skill_install_failed"
    assert not staging_root.exists()
