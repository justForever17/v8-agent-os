from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import os
import sys
import threading
import time
import zipfile
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
from runtimes.plugin_manager import cli_capability_sync as capability_sync_module
from runtimes.plugin_manager.requirements import compile_plugin_requirements
from runtimes.plugin_manager.service import PluginManagerError, PluginManagerService
from runtimes.plugin_manager.schema import CliProfile, CommandSpec
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
        skill_names = list(skill.skillNames) or [skill.targetDirectory]
        skill_roots = []
        for skill_name in skill_names:
            skill_root = service_module.AGENT_SKILLS_ROOT / skill_name
            skill_root.mkdir(parents=True, exist_ok=True)
            (skill_root / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n", encoding="utf-8")
            skill_roots.append(str(skill_root))
        service._register_component(
            manifest.id,
            skill.id,
            "skill",
            owned_path=skill_roots[0],
            source_url=skill.repository,
            source_version=skill.revision,
            metadata={"skillNames": skill_names, "skillPaths": skill_roots},
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


def _cloudflare_manifest_with_runtime_support(service: PluginManagerService):
    manifest = service._manifest("cloudflare").model_copy(deep=True)
    manifest.cliProfiles.append(
        CliProfile(
            id="cloudflared-windows-amd64",
            commands=["cloudflared"],
            platforms=["windows"],
            architectures=["amd64"],
            exposure="runtime_support",
            ownership="managed",
            install=CommandSpec(
                argv=["v8-managed-download"],
                timeoutSeconds=900,
                downloadUrl=(
                    "https://github.com/cloudflare/cloudflared/releases/download/"
                    "2026.7.2/cloudflared-windows-amd64.exe"
                ),
                downloadTarget="{pluginRoot}/cloudflared.exe",
                downloadSha256="cdb5d4432f6ae1595654a692a51308b69d2bf7af961f5578d9391837cf072df9",
            ),
            detect=CommandSpec(argv=["{pluginRoot}/cloudflared.exe", "--version"]),
            version=CommandSpec(argv=["{pluginRoot}/cloudflared.exe", "--version"]),
        )
    )
    return manifest.__class__.model_validate(manifest.model_dump(mode="json"))


def _seed_reviewed_cli_action(
    service: PluginManagerService,
    plugin_id: str,
    *,
    action_id: str,
    command_path: list[str],
) -> None:
    manifest = service._manifest(plugin_id)
    profile = manifest.cliProfiles[0]
    target = service._cli_capability_snapshot_path(manifest, profile)
    assert target is not None
    payload = {
        "schemaVersion": capability_sync_module.SNAPSHOT_SCHEMA_VERSION,
        "adapter": "reviewed_help_v1",
        "pluginId": manifest.id,
        "profileId": profile.id,
        "cliVersion": "test-1.0.0",
        "reviewedRoots": sorted({command_path[0]}),
        "helpArguments": list(profile.capabilitySync.helpArguments),
        "helpPlacement": profile.capabilitySync.helpPlacement,
        "rootCommands": [{"id": command_path[0], "description": "Reviewed root"}],
        "missingRoots": [],
        "commandGroups": {},
        "refreshErrors": [],
        "actionCount": 1,
        "actions": [
            {
                "id": action_id,
                "commandPath": command_path,
                "argv": [profile.commands[0], *command_path],
                "description": "Reviewed typed action",
                "parameters": [],
                "inputSchema": {"type": "object", "properties": {}, "required": []},
                "outputSchema": {"type": "object"},
                "mutating": False,
                "source": "discovered_schema",
            }
        ],
    }
    payload["digest"] = capability_sync_module._digest(payload)
    capability_sync_module._atomic_write(target, payload)


def _godot_setup_projection(values: dict, *, probe_mcp: bool) -> dict:
    scenario = str(values.get("scenario") or "")
    prerequisites_ready = bool(values.get("godotExecutable") and values.get("projectPath") and scenario)
    editor_online = bool(probe_mcp and prerequisites_ready)
    return {
        "adapter": "godot_v1",
        "steps": {
            "application": {"state": "ready" if values.get("godotExecutable") else "missing"},
            "project": {"state": "ready" if values.get("projectPath") else "missing"},
            "scenario": {"state": "ready" if scenario else "missing", "value": scenario},
            "mcp": {"state": "ready" if editor_online else "unchecked"},
        },
        "readyForInstall": editor_online,
        "editorOnline": editor_online,
        "offlinePrerequisitesReady": prerequisites_ready,
        "blockingReasons": [] if editor_online else ["mcp"],
    }


def test_skills_cli_runs_without_a_console_window_on_windows(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    captured: dict[str, object] = {}

    monkeypatch.setattr(service_module.shutil, "which", lambda _command, path=None: "C:/npm/npx.cmd")

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return type("Completed", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()

    monkeypatch.setattr(service_module, "run_windowless", fake_run)

    result = service._run_skills_cli(["list", "--global"])

    assert result["returnCode"] == 0
    assert captured["args"][0][0] == "C:/npm/npx.cmd"
    assert captured["kwargs"]["capture_output"] is True


def test_skills_cli_inventory_probes_the_installed_tool_version(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    calls: list[list[str]] = []

    def fake_skills_cli(arguments, **_kwargs):
        call = [str(item) for item in arguments]
        calls.append(call)
        if call == ["--version"]:
            return {"returnCode": 0, "stdoutTail": "skills 1.5.19", "stderrTail": ""}
        return {"returnCode": 0, "stdoutTail": "[]", "stderrTail": ""}

    monkeypatch.setattr(service, "_run_skills_cli", fake_skills_cli)

    inventory = PluginManagerService._skills_cli_inventory(service, force=True)

    assert calls == [["--version"], ["list", "--global", "--json"]]
    assert inventory["toolVersion"] == "1.5.19"
    assert inventory["toolProbeOk"] is True


def test_builtin_catalog_has_18_signed_curated_plugins(runtime) -> None:
    service, _, _ = runtime
    catalog = plugin_catalog_service.load()
    assert catalog.revision >= 1
    assert len(catalog.plugins) == 18
    assert len({plugin.id for plugin in catalog.plugins}) == 18
    assert {plugin.id for plugin in catalog.plugins} == {
        "aliyun-bailian", "volcengine-mediakit", "volcengine", "lark", "cloudflare", "supabase",
        "vercel", "google-workspace", "github", "aws", "wordpress",
        "azure", "figma", "hyperframes", "stripe", "amap", "godot", "office-suite",
    }
    assert [plugin.id for plugin in catalog.plugins[:3]] == [
        "aliyun-bailian",
        "volcengine-mediakit",
        "volcengine",
    ]
    assert not (Path(service_module.__file__).parent / "resources" / "logos" / "docker.ico").exists()
    assert all(plugin.artifacts for plugin in catalog.plugins)
    assert all(service.verify_brand_asset(plugin)["ok"] for plugin in catalog.plugins)
    assert all(
        (
            skill.sourceTrust == "official"
            and skill.officialOrganization.lower() in {item.lower() for item in plugin.officialOrganizations}
        )
        or (
            skill.sourceTrust == "reviewed_community"
            and skill.officialOrganization.lower() in {item.lower() for item in plugin.reviewedOrganizations}
        )
        for plugin in catalog.plugins
        for skill in plugin.skills
    )
    assert all(
        (
            server.sourceTrust == "official"
            and server.officialOrganization.lower() in {item.lower() for item in plugin.officialOrganizations}
        )
        or (
            server.sourceTrust == "reviewed_community"
            and server.officialOrganization.lower() in {item.lower() for item in plugin.reviewedOrganizations}
        )
        for plugin in catalog.plugins
        for server in plugin.mcpServers
    )
    assert all(
        profile.exposure == "runtime_support" or profile.actions or profile.capabilitySync is not None
        for plugin in catalog.plugins
        for profile in plugin.cliProfiles
    )

    office = next(plugin for plugin in catalog.plugins if plugin.id == "office-suite")
    assert office.displayName == "基础日常包"
    assert office.publisher == "Anthropic / V8OS 精选"
    assert office.skills[0].skillNames == [
        "doc-coauthoring",
        "docx",
        "mcp-builder",
        "pdf",
        "pptx",
        "skill-creator",
        "xlsx",
    ]

    projected_office = next(item for item in service.list_catalog()["plugins"] if item["id"] == "office-suite")
    assert projected_office["componentCounts"]["skills"] == 7
    assert projected_office["declaredComponentCounts"]["skills"] == 7

    godot = next(plugin for plugin in catalog.plugins if plugin.id == "godot")
    assert godot.setupAdapter == "godot_v1"
    assert godot.mcpServers[0].sourceTrust == "reviewed_community"
    assert godot.mcpServers[0].revision == "e642402d179e48173f4774492cfe2e11181cd1fa"


def test_runtime_support_schema_rejects_agent_cli_surfaces(runtime) -> None:
    service, _, _ = runtime
    profile = _cloudflare_manifest_with_runtime_support(service).cliProfiles[-1]

    assert profile.exposure == "runtime_support"
    for update in (
        {"allowedArguments": ["--help"]},
        {"actions": [CliAction(id="status", argv=["cloudflared", "--version"])]},
        {"login": CommandSpec(argv=["cloudflared", "tunnel", "login"])},
        {"shimCommand": ["{pluginRoot}/cloudflared.exe"]},
    ):
        payload = profile.model_dump(mode="json")
        payload.update(update)
        with pytest.raises(ValueError, match="runtime-support"):
            CliProfile.model_validate(payload)


def test_cloudflared_runtime_support_is_lifecycle_visible_but_never_agent_visible(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, test_db, _ = runtime
    original_manifest = service._manifest
    manifest = _cloudflare_manifest_with_runtime_support(service)
    runtime_profile = next(item for item in manifest.cliProfiles if item.exposure == "runtime_support")
    monkeypatch.setattr(service_module, "_platform_name", lambda: "windows")
    monkeypatch.setattr(service_module, "_architecture_name", lambda: "amd64")
    monkeypatch.setattr(
        service,
        "_manifest",
        lambda plugin_id: manifest if plugin_id == manifest.id else original_manifest(plugin_id),
    )
    original_catalog_get = service_module.plugin_catalog_service.get
    monkeypatch.setattr(
        service_module.plugin_catalog_service,
        "get",
        lambda plugin_id: manifest if plugin_id == manifest.id else original_catalog_get(plugin_id),
    )

    policy = service._component_policy(manifest)
    assert [item.id for item in policy["agentCliProfiles"]] == ["wrangler"]
    assert [item.id for item in policy["runtimeSupportProfiles"]] == [runtime_profile.id]
    assert runtime_profile.id in policy["activeComponentIds"]
    assert runtime_profile.id not in policy["agentComponentIds"]
    assert policy["transport"] == "cli"
    plan = service.build_install_plan(manifest.id)
    assert [item["componentId"] for item in plan["steps"]["cli"]] == [
        "wrangler",
        runtime_profile.id,
    ]
    assert runtime_profile.install.downloadUrl.endswith("/2026.7.2/cloudflared-windows-amd64.exe")
    assert runtime_profile.install.downloadSha256 == (
        "cdb5d4432f6ae1595654a692a51308b69d2bf7af961f5578d9391837cf072df9"
    )
    assert service._ensure_cli_shims(manifest, runtime_profile) == []

    _mark_ready(service, manifest.id)
    assert runtime_profile.id in service._active_installed_component_ids(manifest)
    assert runtime_profile.id not in service._grantable_installed_component_ids(manifest)
    assert {item["id"] for item in service._grantable_components(manifest)} == {
        "wrangler",
        manifest.skills[0].id,
    }
    with pytest.raises(PluginManagerError) as denied_grant:
        service.create_grant(
            plugin_id=manifest.id,
            scope="task",
            session_id="s1",
            run_id="r1",
            component_ids=[runtime_profile.id],
        )
    assert denied_grant.value.code == "grant_component_invalid"

    grant = service.create_grant(
        plugin_id=manifest.id,
        scope="task",
        session_id="s1",
        run_id="r1",
        component_ids=["wrangler"],
    )
    with test_db.get_connection() as conn:
        conn.execute(
            "UPDATE plugin_grants SET component_ids_json=? WHERE id=?",
            (json.dumps(["wrangler", runtime_profile.id]), grant["grantId"]),
        )
        conn.commit()
    service._invalidate_grant_cache()
    projected = service.projection_for(session_id="s1", run_id="r1")
    assert projected["grants"][0]["componentIds"] == ["wrangler"]
    assert [item["id"] for item in projected["cliProfiles"]] == ["wrangler"]

    with test_db.get_connection() as conn:
        conn.execute(
            "UPDATE plugin_grants SET component_ids_json=? WHERE id=?",
            (json.dumps([runtime_profile.id]), grant["grantId"]),
        )
        conn.commit()
    service._invalidate_grant_cache()
    assert service.active_grants(session_id="s1", run_id="r1") == []
    privileged = service.resolve_privileged_channel(
        plugin_references=[
            {
                "pluginId": manifest.id,
                "componentIds": [runtime_profile.id],
                "scope": "task",
            }
        ],
        session_id="s1",
        run_id="r1",
    )
    assert privileged["projection"]["cliProfiles"] == []
    assert privileged["blocked"] == [
        {
            "pluginId": manifest.id,
            "status": "invalid",
            "reason": "runtime_support_not_grantable",
            "componentIds": [runtime_profile.id],
            "configurationUrl": f"/admin/plugins?plugin={manifest.id}",
        }
    ]
    with pytest.raises(PluginManagerError) as denied_execution:
        asyncio.run(
            service.execute_cli(
                plugin_id=manifest.id,
                profile_id=runtime_profile.id,
                action_id="version",
                parameters={},
                session_id="s1",
                run_id="r1",
            )
        )
    assert denied_execution.value.code == "plugin_cli_runtime_support_denied"


def test_cloudflared_runtime_support_version_probe_has_no_windows_console(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = _cloudflare_manifest_with_runtime_support(service)
    profile = manifest.cliProfiles[-1]
    captured: dict[str, object] = {}

    monkeypatch.setattr(service, "_refresh_process_cli_path", lambda: "")
    monkeypatch.setattr(service, "_resolve_execution_argv", lambda argv, **_kwargs: argv)

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return type("Completed", (), {"returncode": 0, "stdout": "cloudflared version 2026.7.2", "stderr": ""})()

    monkeypatch.setattr(service_module, "run_windowless", fake_run)
    result = service._execute_spec(manifest, profile.version)

    assert result["returnCode"] == 0
    assert captured["kwargs"]["shell"] is False


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
    assert [item["id"] for item in office_plan["steps"]["skills"]] == ["anthropic-daily-skills"]


def test_godot_setup_selects_scenario_skills_and_gates_install_on_live_mcp(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    probe_calls: list[bool] = []

    def fake_evaluate(values: dict, *, probe_mcp: bool = True) -> dict:
        probe_calls.append(probe_mcp)
        return _godot_setup_projection(values, probe_mcp=probe_mcp)

    monkeypatch.setattr(service_module, "evaluate_godot_setup", fake_evaluate)
    before = next(item for item in service.list_catalog()["plugins"] if item["id"] == "godot")
    assert before["componentCounts"]["skills"] == 11

    saved = service.update_plugin_setup(
        "godot",
        {
            "godotExecutable": "C:/Tools/Godot.exe",
            "projectPath": "C:/Projects/MyGame",
            "scenario": "2.5d",
        },
    )
    assert saved["status"]["offlinePrerequisitesReady"] is True
    assert saved["status"]["readyForInstall"] is False
    assert probe_calls[-1] is False

    after = next(item for item in service.list_catalog()["plugins"] if item["id"] == "godot")
    assert after["componentCounts"]["skills"] == 14
    policy = service._component_policy(service._manifest("godot"))
    assert {item.id for item in policy["skills"]} == {
        "gda-versioned-skill",
        "godot-scene-core-skills",
        "godot-scene-2d-skills",
        "godot-scene-3d-skills",
    }

    environment = service._setup_environment(service._manifest("godot"))
    assert environment["GDA_GODOT"] == "C:/Tools/Godot.exe"
    assert environment["GDA_PROJECT"] == "C:/Projects/MyGame"

    plan = service.build_install_plan("godot")
    assert probe_calls[-1] is True
    assert plan["installable"] is True
    assert plan["componentPolicy"]["transport"] == "cli_mcp"
    assert [item["componentId"] for item in plan["steps"]["cli"]] == ["gda-cli"]
    assert [item["id"] for item in plan["steps"]["mcp"]] == ["godot-ai-mcp"]


def test_godot_capability_sync_uses_managed_gda_binary(
    runtime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("godot")
    profile = next(item for item in manifest.cliProfiles if item.id == "gda-cli")
    plugin_root = tmp_path / "godot-plugin"
    captured: dict[str, object] = {}

    def fake_sync_gda_capabilities(**kwargs):
        captured.update(kwargs)
        return {"accepted": True, "actionCount": 48}

    monkeypatch.setattr(service_module, "sync_gda_capabilities", fake_sync_gda_capabilities)

    result = service._sync_cli_profile_capabilities(
        manifest,
        profile,
        plugin_root=plugin_root,
    )

    expected = plugin_root / "bin" / ("gda.exe" if os.name == "nt" else "gda")
    assert Path(str(captured["executable"])) == expected
    assert Path(str(captured["executable"])) != Path(sys.executable)
    assert result == {"accepted": True, "actionCount": 48}


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


def test_machine_discovery_uses_cached_startup_snapshot_until_explicit_refresh(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    detected = False

    def discover(profile):
        if detected and profile.id == "gh":
            return {"gh": "C:/Program Files/GitHub CLI/gh.exe"}
        return {}

    monkeypatch.setattr(service, "_discover_cli_commands", discover)

    initial = service.discover_machine_components("github", force=True)
    assert initial["cli"][0]["state"] == "missing"

    detected = True
    cached = service.discover_machine_components("github")
    assert cached["cli"][0]["state"] == "missing"

    refreshed = service.discover_machine_components("github", force=True)
    assert refreshed["cli"][0]["state"] == "detected"
    assert refreshed["summary"]["presentUnits"] == 1
    assert refreshed["summary"]["totalUnits"] == 2
    assert refreshed["summary"]["missingUnits"] == 1
    assert refreshed["summary"]["coverage"] == "partial"


def test_machine_discovery_projects_reviewed_cli_and_skill_updates_with_member_details(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("cloudflare")
    profile = manifest.cliProfiles[0]
    skill = manifest.skills[0]
    skill_name = skill.skillNames[0]
    skill_root = service_module.AGENT_SKILLS_ROOT / skill_name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n", encoding="utf-8")
    service._upsert_installation(
        manifest,
        state="installed",
        health={"ok": True, "online": True, "checks": []},
        external=False,
    )
    service._register_component(
        manifest.id,
        profile.id,
        "cli",
        source_version="4.0.0",
        ownership="managed",
    )
    monkeypatch.setattr(
        service,
        "_discover_cli_commands",
        lambda _profile: {"wrangler": "C:/v8/plugins/cloudflare/wrangler.cmd"},
    )
    monkeypatch.setattr(
        service,
        "_probe_cli_version",
        lambda *_args, **_kwargs: {
            "ok": True,
            "version": "4.0.0",
            "returnCode": 0,
            "durationMs": 3,
        },
    )
    monkeypatch.setattr(
        service,
        "_skills_cli_inventory",
        lambda force=False: {
            "ok": True,
            "tool": service_module.SKILLS_CLI_PACKAGE,
            "toolVersion": "1.5.19",
            "toolProbeOk": True,
            "items": [{"name": skill_name, "path": str(skill_root), "scope": "global", "agents": ["Codex"]}],
            "lockEntries": {
                skill_name: {
                    "sourceUrl": skill.repository,
                    "skillPath": f"{skill.path}/SKILL.md",
                    "ref": "0" * 40,
                }
            },
            "error": "",
        },
    )
    monkeypatch.setattr(
        service,
        "_cached_extension_skill_metadata",
        lambda: ({skill_name: {"description": "Reviewed Wrangler workflow guidance."}}, {}),
    )

    discovery = service.discover_machine_components(manifest.id, force=True)

    cli = discovery["cli"][0]
    assert cli["action"] == "update"
    assert cli["installedVersion"] == "4.0.0"
    assert cli["availableVersion"] == "4.110.0"
    assert cli["versionState"] == "available"
    assert cli["members"][0]["name"] == "wrangler"
    skill_projection = discovery["skills"][0]
    assert skill_projection["action"] == "update"
    assert skill_projection["installedVersion"] == "0" * 40
    assert skill_projection["availableVersion"] == skill.revision
    assert skill_projection["members"] == [
        {"name": skill_name, "description": "Reviewed Wrangler workflow guidance."}
    ]
    assert discovery["skillsCli"]["version"] == "1.5.19"
    assert discovery["summary"]["updatesAvailable"] == 2
    assert [item["componentType"] for item in discovery["components"]] == ["cli", "skill"]
    plan = service.build_install_plan(manifest.id)
    assert plan["steps"]["cli"][0]["action"] == "update"
    assert plan["steps"]["skills"][0]["action"] == "update"


def test_machine_discovery_keeps_managed_skill_upgradeable_when_lock_source_shape_changes(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("cloudflare")
    skill = manifest.skills[0]
    skill_name = skill.skillNames[0]
    skill_root = service_module.AGENT_SKILLS_ROOT / skill_name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n", encoding="utf-8")
    old_revision = "0" * 40
    service._upsert_installation(
        manifest,
        state="installed",
        health={"ok": True, "online": True, "checks": []},
        external=False,
    )
    service._register_component(
        manifest.id,
        skill.id,
        "skill",
        owned_path=str(skill_root),
        source_url=skill.repository,
        source_version=old_revision,
        ownership="skills_cli",
        metadata={
            "skillNames": [skill_name],
            "skillPaths": [str(skill_root)],
            "managedSkillNames": [skill_name],
            "adoptedSkillNames": [],
        },
    )
    monkeypatch.setattr(
        service,
        "_skills_cli_inventory",
        lambda force=False: {
            "ok": True,
            "tool": service_module.SKILLS_CLI_PACKAGE,
            "toolVersion": "1.5.19",
            "toolProbeOk": True,
            "items": [{"name": skill_name, "path": str(skill_root), "scope": "global", "agents": ["Codex"]}],
            "lockEntries": {
                skill_name: {
                    "source": "normalized-by-new-skills-cli",
                    "ref": skill.revision,
                }
            },
            "error": "",
        },
    )

    projection = service.discover_machine_components(manifest.id, force=True)["skills"][0]

    assert projection["state"] == "registered"
    assert projection["conflicts"] == []
    assert projection["detectedNames"] == [skill_name]
    assert projection["installedVersion"] == old_revision
    assert projection["availableVersion"] == skill.revision
    assert projection["versionState"] == "available"
    assert projection["updateSupported"] is True
    assert projection["action"] == "update"


def test_machine_discovery_uses_receipt_names_for_dynamic_skill_packages(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("lark")
    skill = manifest.skills[0]
    assert skill.skillNames == []
    skill_name = "lark-docs"
    skill_root = service_module.AGENT_SKILLS_ROOT / skill_name
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n", encoding="utf-8")
    old_revision = "0" * 40
    service._upsert_installation(
        manifest,
        state="installed",
        health={"ok": True, "online": True, "checks": []},
        external=False,
    )
    service._register_component(
        manifest.id,
        skill.id,
        "skill",
        owned_path=str(skill_root),
        source_url=skill.repository,
        source_version=old_revision,
        ownership="skills_cli",
        metadata={
            "skillNames": [skill_name],
            "skillPaths": [str(skill_root)],
            "managedSkillNames": [skill_name],
            "adoptedSkillNames": [],
        },
    )
    monkeypatch.setattr(
        service,
        "_skills_cli_inventory",
        lambda force=False: {
            "ok": True,
            "tool": service_module.SKILLS_CLI_PACKAGE,
            "toolVersion": "1.5.19",
            "toolProbeOk": True,
            "items": [{"name": skill_name, "path": str(skill_root), "scope": "global", "agents": ["Codex"]}],
            "lockEntries": {
                skill_name: {
                    "source": "normalized-by-new-skills-cli",
                    "ref": skill.revision,
                }
            },
            "error": "",
        },
    )

    projection = service.discover_machine_components(manifest.id, force=True)["skills"][0]

    assert projection["state"] == "registered"
    assert projection["conflicts"] == []
    assert projection["detectedNames"] == [skill_name]
    assert projection["installedVersion"] == old_revision
    assert projection["availableVersion"] == skill.revision
    assert projection["versionState"] == "available"
    assert projection["updateSupported"] is True
    assert projection["action"] == "update"


def test_machine_discovery_projects_mcp_handshake_version_protocol_and_tools(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, test_storage = runtime
    manifest = service._manifest("figma")
    server = manifest.mcpServers[0]
    test_storage.mcp_config["mcpServers"][server.serverName] = {
        "url": server.url,
        "disabled": False,
        "x-v8-plugin-owner": manifest.id,
        "x-v8-plugin-component": server.id,
    }
    service._upsert_installation(
        manifest,
        state="installed",
        health={"ok": True, "online": True, "checks": []},
        external=False,
    )
    service._register_component(
        manifest.id,
        server.id,
        "mcp",
        source_url=server.url,
        source_version="0",
    )
    monkeypatch.setattr(
        service,
        "_cached_mcp_status",
        lambda: {
            server.serverName: {
                "serverInfoName": "Figma MCP",
                "serverInfoVersion": "2.3.4",
                "protocolVersion": "2025-06-18",
                "tools": [{"name": "get_design_context", "description": "Read selected design context."}],
            }
        },
    )

    projection = service.discover_machine_components(manifest.id, force=True)["mcp"][0]

    assert projection["state"] == "registered"
    assert projection["action"] == "update"
    assert projection["runtimeVersion"] == "2.3.4"
    assert projection["protocolVersion"] == "2025-06-18"
    assert projection["members"] == [
        {"name": "get_design_context", "description": "Read selected design context."}
    ]


def test_machine_discovery_trusts_registered_skill_receipt_when_public_lock_is_missing(runtime) -> None:
    service, _, _ = runtime
    _mark_ready(service, "office-suite")

    discovery = service.discover_machine_components("office-suite", force=True)

    assert discovery["skills"][0]["state"] == "registered"
    assert discovery["summary"]["coverage"] == "complete"
    assert discovery["summary"]["presentUnits"] == discovery["summary"]["totalUnits"]


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


def test_daily_bundle_is_pinned_skill_only_and_advertises_artifacts_on_demand(runtime) -> None:
    service, _, _ = runtime
    manifest = service._manifest("office-suite")
    skill = manifest.skills[0]

    assert manifest.cliProfiles == []
    assert manifest.mcpServers == []
    assert skill.repository == "https://github.com/anthropics/skills"
    assert skill.path == "skills"
    assert skill.revision == "fa0fa64bdc967915dc8399e803be67759e1e62b8"
    assert len(skill.skillNames) == 7
    assert service.supervisor_availability_prompt() == ""

    _mark_ready(service, "office-suite")
    prompt = service.supervisor_availability_prompt()
    assert "[Plugin Catalog]" in prompt
    assert "office-suite (ready)" in prompt
    assert "does not alter ordinary Extensions routing" in prompt
    assert "current explicit reference" in prompt
    assert "DOCX" in prompt and "XLSX/CSV" in prompt and "PDF" in prompt and "PPTX" in prompt
    assert "plugin_broker(status)" in prompt
    assert "minimal task grant" in prompt
    assert "plugin_cli" in prompt
    assert "Component IDs are grant identifiers" in prompt
    assert "SKILL.md" not in prompt
    assert "npm install" not in prompt

    healthy = asyncio.run(service.doctor("office-suite", persist=False))
    assert healthy["ok"] is True
    missing_skill_root = service_module.AGENT_SKILLS_ROOT / skill.skillNames[-1]
    (missing_skill_root / "SKILL.md").unlink()
    unhealthy = asyncio.run(service.doctor("office-suite", persist=False))
    assert unhealthy["ok"] is False
    assert unhealthy["checks"][0]["kind"] == "skill-file"


def test_daily_bundle_discovers_each_official_skill_and_reports_partial_completion(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("office-suite")
    skill = manifest.skills[0]
    detected_names = skill.skillNames[:2]
    items = []
    lock_entries = {}
    for name in detected_names:
        skill_root = service_module.AGENT_SKILLS_ROOT / name
        skill_root.mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        items.append({"name": name, "path": str(skill_root), "scope": "global", "agents": ["Codex"]})
        lock_entries[name] = {
            "sourceUrl": "https://github.com/anthropics/skills.git",
            "skillPath": f"skills/{name}/SKILL.md",
        }
    monkeypatch.setattr(
        service,
        "_skills_cli_inventory",
        lambda force=False: {
            "ok": True,
            "tool": service_module.SKILLS_CLI_PACKAGE,
            "items": items,
            "lockEntries": lock_entries,
            "error": "",
        },
    )

    discovery = service.discover_machine_components("office-suite", force=True)
    item = discovery["skills"][0]

    assert item["componentId"] == "anthropic-daily-skills"
    assert item["state"] == "partial"
    assert item["action"] == "complete"
    assert item["expectedNames"] == skill.skillNames
    assert item["detectedNames"] == detected_names
    assert item["missingNames"] == skill.skillNames[2:]


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


def test_mediakit_cli_contract_is_pinned_typed_and_architecture_scoped(runtime) -> None:
    service, _, _ = runtime
    manifest = service._manifest("volcengine-mediakit")
    profile = manifest.cliProfiles[0]

    assert profile.architectures == ["amd64"]
    assert profile.install.downloadSha256 == "d69a22ce1e28f69db5f0048f6bbe6a4186f32412a4bdbc00bf0e8f8ab2caf14d"
    assert profile.install.archiveFormat == "zip"
    assert profile.install.archiveEntry == "mediakit-cli.exe"
    assert profile.environment == {
        "MEDIAKIT_SURFACE": "plugin",
        "MEDIAKIT_RUNTIME": "v8os",
    }
    assert service._component_policy(
        manifest,
        platform_name="windows",
        architecture_name="amd64",
    )["installable"] is True
    unsupported = service._component_policy(
        manifest,
        platform_name="windows",
        architecture_name="arm64",
    )
    assert unsupported["installable"] is False
    assert unsupported["architecture"] == "arm64"

    actions = {item.id: item for item in profile.actions}
    assert set(actions) == {
        "doctor",
        "list-domains",
        "video-asr",
        "audio-asr",
        "video-ocr",
        "image-ocr",
        "probe-video-metadata",
        "probe-audio-metadata",
        "extract-audio",
        "query-task",
    }
    audio_asr = service._build_cli_action_spec(
        manifest,
        profile,
        actions["audio-asr"],
        {
            "audioUrl": "https://example.com/sample.mp3",
            "contentType": "speech",
            "language": "cmn-Hans-CN",
            "enableSpeakerInfo": True,
        },
    )
    assert audio_asr.argv[-10:] == [
        "--cloud",
        "video",
        "asr-subtitles",
        "--audio-url",
        "https://example.com/sample.mp3",
        "--content-type",
        "speech",
        "--language",
        "cmn-Hans-CN",
        "--enable-speaker-info",
    ]
    with pytest.raises(PluginManagerError) as missing_url:
        service._build_cli_action_spec(manifest, profile, actions["video-ocr"], {})
    assert missing_url.value.code == "plugin_cli_parameter_missing"
    with pytest.raises(PluginManagerError) as invalid_mode:
        service._build_cli_action_spec(
            manifest,
            profile,
            actions["video-ocr"],
            {"videoUrl": "https://example.com/sample.mp4", "mode": "raw"},
        )
    assert invalid_mode.value.code == "plugin_cli_parameter_invalid"

    assert [item.skillNames for item in manifest.skills] == [
        [
            "byted-mediakit-shared",
            "byted-mediakit-editing",
            "byted-mediakit-audio",
            "byted-mediakit-image",
            "byted-mediakit-video",
        ],
        ["cinema-dna-21x9x3"],
        ["video-hyperframes"],
    ]
    assert manifest.skills[0].revision == "279e5bb97e97c6875ae2c6891c2c3fa9a43f39c0"
    assert manifest.skills[1].sourceTrust == "reviewed_community"
    assert manifest.skills[2].sourceLicense == "Apache-2.0"
    assert service._cli_credential_env(manifest, profile) == profile.environment


def test_cli_schema_parameters_serialize_json_numbers_and_default_true_booleans(runtime) -> None:
    service, _, _ = runtime
    manifest = service._manifest("volcengine-mediakit")
    profile = manifest.cliProfiles[0]
    action = CliAction(
        id="schema-action",
        argv=["mediakit-cli", "editing", "concat-video"],
        parameters=[
            CliActionParameter(name="videoUrls", kind="json", required=True, flag="--video-urls"),
            CliActionParameter(name="sampleCount", kind="integer", flag="--sample-count"),
            CliActionParameter(
                name="keepAudio",
                kind="boolean",
                flag="--keep-audio",
                defaultValue=True,
            ),
        ],
    )
    spec = service._build_cli_action_spec(
        manifest,
        profile,
        action,
        {
            "videoUrls": ["a.mp4", "b.mp4"],
            "sampleCount": 2,
            "keepAudio": False,
        },
    )
    assert spec.argv[-6:] == [
        "concat-video",
        "--video-urls",
        '["a.mp4","b.mp4"]',
        "--sample-count",
        "2",
        "--keep-audio=false",
    ]


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
        component_ids=["gh", "github-cli-skill"],
    )
    assert service.active_grants(session_id="s1", run_id="r2") == []
    assert [item["id"] for item in service.projection_for(session_id="s1", run_id="r1")["cliProfiles"]] == ["gh"]

    child = service.delegate_grants_to_subagent(
        plugin_references=[{"pluginId": "github", "componentIds": ["gh", "github-cli-skill"]}],
        session_id="s1",
        run_id="r1",
        subagent_id="child-1",
        delegation_id="delegation-child-1",
        delegation_depth=1,
    )[0]
    assert child["componentIds"] == ["gh", "github-cli-skill"]
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

    with pytest.raises(PluginManagerError) as equal_scope:
        service.delegate_grants_to_subagent(
            plugin_references=[
                {"pluginId": "github", "componentIds": ["gh", "github-cli-skill"]}
            ],
            session_id="s1",
            run_id="r1",
            subagent_id="grandchild-equal",
            delegation_id="delegation-grandchild-equal",
            delegation_depth=2,
            parent_agent_id="child-1",
            parent_delegation_id="delegation-child-1",
        )
    assert equal_scope.value.code == "grant_scope_not_strict_subset"

    with pytest.raises(PluginManagerError) as equal_create:
        service.create_grant(
            plugin_id="github",
            scope="task",
            session_id="s1",
            run_id="r1",
            grantee_type="subagent",
            grantee_id="grandchild-equal-create",
            component_ids=["gh", "github-cli-skill"],
            parent_grant_id=child["grantId"],
            delegation_id="delegation-grandchild-equal-create",
            delegation_depth=2,
            grant_source="delegation",
        )
    assert equal_create.value.code == "grant_scope_not_strict_subset"

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


def test_privileged_channel_requires_explicit_installed_plugin_and_exact_grant(runtime) -> None:
    service, _, _ = runtime

    default_route = service.resolve_privileged_channel(
        plugin_references=[],
        session_id="s1",
        run_id="r1",
    )
    assert default_route["active"] is False
    assert default_route["prefilterBypassed"] is False

    missing_route = service.resolve_privileged_channel(
        plugin_references=[{"pluginId": "github", "componentIds": ["gh"], "scope": "task"}],
        session_id="s1",
        run_id="r1",
    )
    assert missing_route["active"] is False
    assert missing_route["blocked"] == [
        {"pluginId": "github", "status": "not_installed", "reason": "not_installed"}
    ]

    _mark_ready(service, "github")
    ungranted_route = service.resolve_privileged_channel(
        plugin_references=[{"pluginId": "github", "componentIds": ["gh"], "scope": "task"}],
        session_id="s1",
        run_id="r1",
    )
    assert ungranted_route["active"] is True
    assert ungranted_route["prefilterBypassed"] is True
    assert ungranted_route["projection"]["cliProfiles"] == []
    assert ungranted_route["blocked"][0]["reason"] == "grant_missing"

    grant = service.create_grant(
        plugin_id="github",
        scope="task",
        session_id="s1",
        run_id="r1",
        component_ids=["gh"],
        grant_source="user_reference",
    )
    resolved_route = service.resolve_privileged_channel(
        plugin_references=[{"pluginId": "github", "componentIds": ["gh"], "scope": "task"}],
        session_id="s1",
        run_id="r1",
    )
    assert resolved_route["active"] is True
    assert resolved_route["projectedPluginIds"] == ["github"]
    assert resolved_route["blocked"] == []
    assert [item["id"] for item in resolved_route["projection"]["cliProfiles"]] == ["gh"]
    assert resolved_route["projection"]["grants"][0]["grantId"] == grant["grantId"]
    assert resolved_route["projection"]["grants"][0]["componentIds"] == ["gh"]


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


def test_cli_requires_exact_grant_and_structured_manifest_action(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    monkeypatch.setattr(service, "_sync_cli_profile_capabilities", lambda *_args, **_kwargs: None)
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


def test_named_plugin_status_reuses_extension_metadata_and_loads_cli_help_on_demand(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    _seed_reviewed_cli_action(
        service,
        "github",
        action_id="repo.view",
        command_path=["repo", "view"],
    )
    monkeypatch.setattr(service, "_sync_cli_profile_capabilities", lambda *_args, **_kwargs: None)
    skill_root = service_module.AGENT_SKILLS_ROOT / "gh"
    from runtimes.extensions.runtime import extensions_runtime_service

    monkeypatch.setattr(
        extensions_runtime_service,
        "list_skills",
        lambda **_kwargs: [
            {
                "skillRoot": str(skill_root),
                "skillName": "gh",
                "description": "Official GitHub CLI usage patterns.",
            }
        ],
    )
    refreshed_path = str(service_module.AGENT_SKILLS_ROOT.parent / "cli-bin")
    monkeypatch.setattr(service, "_cli_search_path", lambda: refreshed_path)

    payload = service.supervisor_catalog(plugin_id="github", session_id="s1", run_id="r1")
    usage = payload["items"][0]["usage"]

    assert usage["transport"] == "cli"
    assert usage["cli"][0]["componentId"] == "gh"
    assert usage["cli"][0]["command"] == "gh"
    assert usage["cli"][0]["available"] is True
    assert usage["cli"][0]["rootCommands"] == [
        {"id": "repo", "description": "Reviewed root"}
    ]
    assert usage["cli"][0]["actions"] == [
        {"id": "repo.view", "description": "Reviewed typed action", "mutating": False}
    ]
    assert "stdoutTail" not in json.dumps(usage)
    assert usage["skills"] == [
        {
            "componentId": "github-cli-skill",
            "name": "gh",
            "summary": "Official GitHub CLI usage patterns.",
        }
    ]
    assert usage["mcpTools"] == []
    assert "plugin_cli" in payload["nextAction"]
    assert "Never bypass the plugin grant with run_system_command" in payload["nextAction"]
    assert "does not need a plugin grant" not in payload["nextAction"]
    assert service_module.os.environ["PATH"] == refreshed_path


@pytest.mark.parametrize(
    ("plugin_id", "action_id", "command_path", "expected_ownership"),
    [
        ("cloudflare", "d1.list", ["d1", "list"], "managed"),
        ("github", "repo.view", ["repo", "view"], "external"),
    ],
)
def test_installed_cli_plugins_reach_governed_typed_invocation_for_managed_and_external_ownership(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
    plugin_id: str,
    action_id: str,
    command_path: list[str],
    expected_ownership: str,
) -> None:
    service, _, _ = runtime
    _mark_ready(service, plugin_id)
    manifest = service._manifest(plugin_id)
    profile = manifest.cliProfiles[0]
    assert profile.ownership == expected_ownership
    _seed_reviewed_cli_action(
        service,
        plugin_id,
        action_id=action_id,
        command_path=command_path,
    )
    monkeypatch.setattr(service, "_sync_cli_profile_capabilities", lambda *_args, **_kwargs: None)
    grant = service.create_grant(
        plugin_id=plugin_id,
        scope="task",
        session_id="s1",
        run_id="r1",
        component_ids=[profile.id],
    )
    projection = service.projection_for(session_id="s1", run_id="r1")
    assert projection["cliProfiles"] == [
        {
            "pluginId": manifest.id,
            "pluginName": manifest.displayName,
            "grantId": grant["grantId"],
            "id": profile.id,
            "command": profile.commands[0],
        }
    ]

    captured: dict[str, object] = {}

    def fake_execute(_manifest, spec, **_kwargs):
        captured["argv"] = list(spec.argv)
        return {
            "returnCode": 0,
            "stdoutTail": "ok",
            "stderrTail": "",
            "durationMs": 1,
        }

    monkeypatch.setattr(service, "_execute_spec", fake_execute)
    from core.tools.native import tool_governance
    from erc.safety_guardian import safety_guardian

    monkeypatch.setattr(
        tool_governance,
        "_enforce_safety_decision",
        lambda *_args, **_kwargs: (True, None),
    )
    monkeypatch.setattr(safety_guardian, "assess_system_command", lambda *_args, **_kwargs: {})

    result = asyncio.run(
        service.execute_cli(
            plugin_id=plugin_id,
            profile_id=profile.id,
            action_id=action_id,
            parameters={},
            session_id="s1",
            run_id="r1",
        )
    )
    assert result["status"] == "completed"
    assert list(captured["argv"])[-len(command_path):] == command_path


def test_mcp_only_and_skill_only_plugins_project_only_the_granted_transport(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    _mark_ready(service, "figma")
    _mark_ready(service, "office-suite")

    figma_manifest = service._manifest("figma")
    figma_server = figma_manifest.mcpServers[0]
    monkeypatch.setattr(figma_server, "allowedTools", ["get_repository"])

    @tool
    async def get_repository(name: str) -> str:
        """Read a repository."""
        return name

    get_repository.metadata = {"server_name": figma_server.serverName}
    from runtimes.extensions.mcp.client import mcp_manager

    monkeypatch.setattr(mcp_manager, "get_tools", lambda: [get_repository])
    service.create_grant(
        plugin_id="figma",
        scope="task",
        session_id="s1",
        run_id="r1",
        component_ids=[figma_server.id],
    )
    figma_projection = service.projection_for(session_id="s1", run_id="r1")
    assert len(figma_projection["mcpTools"]) == 1
    assert figma_projection["cliProfiles"] == []
    assert figma_projection["skills"] == []

    office_manifest = service._manifest("office-suite")
    office_skill = office_manifest.skills[0]
    service.create_grant(
        plugin_id="office-suite",
        scope="task",
        session_id="s1",
        run_id="r1",
        component_ids=[office_skill.id],
    )
    combined_projection = service.projection_for(session_id="s1", run_id="r1")
    office_projection = [
        item for item in combined_projection["skills"] if item["pluginId"] == "office-suite"
    ]
    assert len(office_projection) == 1
    assert office_projection[0]["installedRoots"]
    assert combined_projection["cliProfiles"] == []


def test_plugin_catalog_list_does_not_probe_cli_help(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    monkeypatch.setattr(
        service,
        "_execute_spec",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("list mode must stay lightweight")),
    )

    payload = service.supervisor_catalog()

    assert any(item["pluginId"] == "github" for item in payload["items"])
    assert all("usage" not in item for item in payload["items"])


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


def test_execute_spec_resolves_batch_launcher_without_shell(
    runtime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("cloudflare")
    launcher = tmp_path / "npm.cmd"
    launcher.write_text("@echo off\r\n", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(service, "_refresh_process_cli_path", lambda: str(tmp_path))
    monkeypatch.setattr(
        service_module.shutil,
        "which",
        lambda command, path=None: str(launcher) if command == "npm" else None,
    )

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return type("Completed", (), {"returncode": 0, "stdout": "11.10.0", "stderr": ""})()

    monkeypatch.setattr(service_module, "run_windowless", fake_run)

    result = service._execute_spec(manifest, CommandSpec(argv=["npm", "--version"]))

    assert result["returnCode"] == 0
    assert captured["argv"] == [str(launcher.resolve()), "--version"]
    assert captured["kwargs"]["shell"] is False


def test_managed_cli_shim_resolves_to_native_argv_without_batch_forwarding(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("cloudflare")
    profile = manifest.cliProfiles[0]
    target = service._plugin_root(manifest.id) / "node_modules" / ".bin" / "wrangler.cmd"
    target.parent.mkdir(parents=True, exist_ok=True)
    capture = service._plugin_root(manifest.id) / "node_modules" / "fixture" / "capture.py"
    capture.parent.mkdir(parents=True, exist_ok=True)
    capture.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    relative_capture = r"..\fixture\capture.py"
    target.write_text(
        "@echo off\r\n"
        f'SET "_prog={sys.executable}"\r\n'
        f'"%_prog%" "%dp0%\\{relative_capture}" %*\r\n',
        encoding="utf-8",
    )
    service._bin_root().mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        service,
        "_register_component",
        lambda _plugin_id, component_id, component_type, **kwargs: {
            "id": component_id,
            "type": component_type,
            "metadata": kwargs.get("metadata") or {},
        },
    )

    rows = service._ensure_cli_shims(manifest, profile)

    shim = service._bin_root() / "wrangler.cmd"
    shim_text = shim.read_text(encoding="utf-8")
    arguments = [
        "alpha&echo injected",
        "second arg",
        'quote"value',
        "trailing\\",
        "percent%PATH%",
        "pipe|value",
        "caret^value",
    ]
    resolved = service._resolve_execution_argv(
        [str(shim), *arguments],
        search_path="",
        manifest=manifest,
    )
    completed = service_module.run_windowless(
        resolved,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert len(rows) == 1
    assert resolved[:2] == [str(Path(sys.executable).resolve()), str(capture.resolve())]
    assert resolved[2:] == arguments
    assert str(target) not in shim_text
    assert str(capture) in shim_text
    assert rows[0]["metadata"]["target"] == str(target)
    assert completed.returncode == 0
    assert json.loads(completed.stdout.strip()) == arguments


def test_managed_cli_shim_rejects_unparseable_batch_forwarders(runtime) -> None:
    service, _, _ = runtime
    manifest = service._manifest("cloudflare")
    profile = manifest.cliProfiles[0]
    target = service._plugin_root(manifest.id) / "node_modules" / ".bin" / "wrangler.cmd"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('@echo off\r\n"unknown.cmd" %*\r\n', encoding="utf-8")
    service._bin_root().mkdir(parents=True, exist_ok=True)

    with pytest.raises(PluginManagerError) as exc:
        service._ensure_cli_shims(manifest, profile)

    assert exc.value.code == "plugin_cli_launcher_unsupported"


def test_managed_archive_download_extracts_only_the_declared_entry(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("volcengine-mediakit")
    target = service._plugin_root(manifest.id) / "bin" / "mediakit-cli.exe"
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("mediakit-cli.exe", b"verified executable")
        archive.writestr("unrelated.txt", b"must not be installed")
    content = archive_bytes.getvalue()

    class _Response:
        def __init__(self, body: bytes) -> None:
            self.content = body

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: _Response(content))
    spec = CommandSpec(
        argv=["v8-managed-download"],
        downloadUrl="https://github.com/volcengine/mediakit-cli/releases/download/v0.2.0/fixture.zip",
        downloadTarget="{pluginRoot}/bin/mediakit-cli.exe",
        downloadSha256=hashlib.sha256(content).hexdigest(),
        archiveFormat="zip",
        archiveEntry="mediakit-cli.exe",
    )

    result = service._execute_spec(manifest, spec)

    assert result["returnCode"] == 0
    assert target.read_bytes() == b"verified executable"
    assert not (target.parent / "unrelated.txt").exists()

    target.unlink()
    missing_entry = spec.model_copy(update={"archiveEntry": "missing.exe"})
    failed = service._execute_spec(manifest, missing_entry)
    assert failed["returnCode"] == 1
    assert "archive entry not found" in failed["stderrTail"]
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
    install_calls = 0
    version_probe_calls = 0
    calls_lock = threading.Lock()

    def execute(_manifest, spec, **_kwargs):
        nonlocal install_calls, version_probe_calls
        executable = str(spec.argv[0] if spec.argv else "").lower()
        with calls_lock:
            if "winget" in executable:
                install_calls += 1
            else:
                version_probe_calls += 1
        if "winget" in executable:
            time.sleep(0.05)
        return {
            "argv": list(spec.argv),
            "returnCode": 0,
            "stdoutTail": "gh version 2.80.0" if "winget" not in executable else "ok",
            "stderrTail": "",
            "durationMs": 1,
        }

    async def doctor(*_args, **_kwargs):
        return {"ok": True, "online": True, "checks": []}

    monkeypatch.setattr(service, "_execute_spec", execute)
    monkeypatch.setattr(service, "_execute_elevated_spec", execute)
    monkeypatch.setattr(service, "doctor", doctor)
    monkeypatch.setattr(
        service,
        "_sync_cli_profile_capabilities",
        lambda *_args, **_kwargs: {"ok": True, "accepted": True, "actionCount": 1},
    )
    monkeypatch.setattr(
        service,
        "_install_skill_component",
        lambda manifest, skill, **_kwargs: [
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
        lambda _profile: {"gh": "C:/Program Files/GitHub CLI/gh.exe"} if install_calls else {},
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
    assert first["state"] == "ready", json.dumps(
        {
            "first": first,
            "second": second,
            "installCalls": install_calls,
            "versionProbeCalls": version_probe_calls,
        },
        ensure_ascii=False,
        default=str,
    )
    assert second["state"] in {
        "staging", "verifying", "installing", "waiting_for_elevation",
        "reconciling", "validating", "committing", "ready",
    }
    assert install_calls == 1
    assert version_probe_calls >= 1


def test_install_journal_uses_declared_state_order(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    monkeypatch.setattr(service_module, "_platform_name", lambda: "windows")
    monkeypatch.setattr(service, "_execute_spec", lambda *_args, **_kwargs: {
        "argv": ["npm"], "returnCode": 0, "stdoutTail": "ok", "stderrTail": "", "durationMs": 1,
    })
    monkeypatch.setattr(service, "_install_skill_component", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        service,
        "_sync_cli_profile_capabilities",
        lambda *_args, **_kwargs: {"ok": True, "accepted": True, "actionCount": 1},
    )

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
    component_steps = [step for step in result["steps"] if step["type"] == "component"]
    assert component_steps
    assert {step["state"] for step in component_steps} == {"running", "completed"}
    assert result["progress"]["totalComponents"] > 0
    assert result["progress"]["completedComponents"] == result["progress"]["totalComponents"]
    assert result["progress"]["currentComponent"] is None
    assert result["progress"]["lastCompletedComponent"]["componentId"]


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


def test_github_cli_login_uses_reviewed_browser_adapter_instead_of_mcp_oauth(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    monkeypatch.setattr(service, "_run_cli_auth_status", lambda *_args: False)
    requirements = service.configuration_requirements("github")["requirements"]
    assert [(item["kind"], item["componentId"]) for item in requirements] == [("cli_login", "gh")]

    with pytest.raises(PluginManagerError) as oauth_error:
        service.prepare_oauth("github", component_id="gh")
    assert oauth_error.value.code == "plugin_oauth_component_not_found"

    captured: dict = {}

    class FakeProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setenv("GH_TOKEN", "temporary-token-must-not-reach-login")
    monkeypatch.setenv("GITHUB_TOKEN", "temporary-token-must-not-reach-login")
    opened_urls: list[str] = []
    monkeypatch.setattr(
        service_module,
        "open_system_browser",
        lambda url: opened_urls.append(url) or True,
    )

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(service_module.subprocess, "Popen", fake_popen)
    started = service.start_cli_login("github", component_id="gh")
    assert started["status"] == "waiting_for_browser"
    assert started["browserOpened"] is True
    assert started["authorizationUrl"] == "https://github.com/login/device"
    assert started["interactionHint"] == "device_code_clipboard"
    assert captured["argv"][-6:] == [
        "--web",
        "--clipboard",
        "--hostname",
        "github.com",
        "--git-protocol",
        "https",
    ]
    assert "GH_TOKEN" not in captured["kwargs"]["env"]
    assert "GITHUB_TOKEN" not in captured["kwargs"]["env"]
    assert captured["kwargs"]["shell"] is False
    repeated = service.start_cli_login("github", component_id="gh")
    assert repeated["status"] == "waiting_for_browser"
    assert repeated["browserOpened"] is True
    assert opened_urls == ["https://github.com/login/device"]
    assert service.cli_login_status("github", component_id="gh")["status"] == "waiting_for_browser"
    assert service.cancel_cli_login("github", component_id="gh")["status"] == "cancelled"


def test_cli_auth_status_poll_uses_windowless_process_flags(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    manifest, profile, adapter = service._cli_browser_auth_contract("github", component_id="gh")
    captured: dict = {}

    class _Completed:
        returncode = 0

    def _run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = dict(kwargs)
        return _Completed()

    monkeypatch.setattr(service_module, "run_windowless", _run)

    assert service._run_cli_auth_status(manifest, profile, adapter) is True
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["stdin"] is service_module.subprocess.DEVNULL


def test_github_cli_login_does_not_reopen_browser_when_credential_store_is_ready(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    monkeypatch.setattr(service, "_run_cli_auth_status", lambda *_args: True)
    monkeypatch.setattr(
        service_module,
        "open_system_browser",
        lambda *_args: (_ for _ in ()).throw(AssertionError("browser must not open for an authenticated CLI")),
    )
    monkeypatch.setattr(
        service_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("browser login must not restart")),
    )
    assert service.start_cli_login("github", component_id="gh")["status"] == "connected"


def test_cloudflare_cli_login_uses_wrangler_oauth_profile_and_keyring(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    _mark_ready(service, "cloudflare")
    _, profile, adapter = service._cli_browser_auth_contract(
        "cloudflare",
        component_id="wrangler",
    )
    assert adapter.status_argv(profile) == ["wrangler", "whoami", "--json"]
    monkeypatch.setattr(service, "_run_cli_auth_status", lambda *_args: False)
    requirements = service.configuration_requirements("cloudflare")["requirements"]
    assert [(item["kind"], item["componentId"]) for item in requirements] == [
        ("cli_login", "wrangler")
    ]

    captured: dict = {}

    class FakeProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "temporary-token")
    monkeypatch.setenv("CLOUDFLARE_API_KEY", "temporary-key")
    monkeypatch.setenv("CLOUDFLARE_EMAIL", "temporary@example.com")
    monkeypatch.setattr(
        service_module,
        "open_system_browser",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("Wrangler must own its dynamic OAuth browser URL")
        ),
    )

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(service_module.subprocess, "Popen", fake_popen)
    started = service.start_cli_login("cloudflare", component_id="wrangler", force=True)
    assert started["status"] == "waiting_for_browser"
    assert started["browserOpened"] is True
    assert started["authorizationUrl"] is None
    assert started["interactionHint"] == "browser_callback"
    assert captured["argv"][-2:] == ["login", "--use-keyring"]
    assert "CLOUDFLARE_API_TOKEN" not in captured["kwargs"]["env"]
    assert "CLOUDFLARE_API_KEY" not in captured["kwargs"]["env"]
    assert "CLOUDFLARE_EMAIL" not in captured["kwargs"]["env"]
    assert captured["kwargs"]["shell"] is False
    assert service.cancel_cli_login("cloudflare", component_id="wrangler")["status"] == "cancelled"


def test_github_cli_login_coalesces_a_concurrent_start(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    _mark_ready(service, "github")
    service._cli_auth_states[("github", "gh")] = {
        "status": "connecting",
        "startedAt": "2026-07-21T00:00:00Z",
        "browserOpened": False,
    }
    monkeypatch.setattr(
        service_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("concurrent start must not spawn another CLI")),
    )
    monkeypatch.setattr(
        service_module,
        "open_system_browser",
        lambda *_args: (_ for _ in ()).throw(AssertionError("concurrent start must not open another tab")),
    )

    result = service.start_cli_login("github", component_id="gh")

    assert result["status"] == "connecting"
    assert result["browserOpened"] is False


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
    skills_cli_calls: list[list[str]] = []
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

    monkeypatch.setattr(service_module, "run_windowless", fake_run)
    inventory = {"ok": True, "tool": service_module.SKILLS_CLI_PACKAGE, "items": [], "lockEntries": {}, "error": ""}

    def fake_skills_cli(arguments, **_kwargs):
        skills_cli_calls.append([str(item) for item in arguments])
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
    assert len(skills_cli_calls) == 1
    assert skills_cli_calls[0][0] == "add"
    reviewed_source = Path(skills_cli_calls[0][1])
    assert reviewed_source.name == Path(skill["path"]).name
    assert reviewed_source.is_relative_to(Path(repo_root))
    assert "#" not in skills_cli_calls[0][1]
    assert skills_cli_calls[0][2:] == [
        "--global",
        "--agent",
        "codex",
        "--copy",
        "--yes",
        "--skill",
        "bailian-cli",
    ]
    assert not any("clone" in command or "--branch" in command for command in commands)
    assert (service_module.AGENT_SKILLS_ROOT / "bailian-cli" / "SKILL.md").is_file()


def test_reviewed_skill_update_reinstalls_the_exact_pinned_revision(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("aliyun-bailian")
    skill = manifest.skills[0].model_dump(mode="json")
    name = skill["skillNames"][0]
    target = service_module.AGENT_SKILLS_ROOT / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    inventory = {
        "ok": True,
        "tool": service_module.SKILLS_CLI_PACKAGE,
        "items": [{"name": name, "path": str(target), "scope": "global", "agents": ["Codex"]}],
        "lockEntries": {
            name: {
                "sourceUrl": "file:///previous/v8-reviewed-checkout",
                "skillPath": f"{skill['path']}/SKILL.md",
                "ref": "0" * 40,
            }
        },
        "error": "",
    }
    monkeypatch.setattr(
        service,
        "_component_rows",
        lambda _plugin_id=None: [
            {
                "component_id": str(skill["id"]),
                "component_type": "skill",
                "source_url": str(skill["repository"]),
                "source_version": "0" * 40,
                "ownership": "skills_cli",
                "metadata_json": json.dumps({"skillNames": [name], "skillPaths": [str(target)]}),
            }
        ],
    )
    cli_arguments: list[str] = []

    def fake_git_step(argv, **_kwargs):
        command = [str(item) for item in argv]
        if command[:2] == ["git", "init"]:
            source = Path(command[2]) / skill["path"]
            source.mkdir(parents=True, exist_ok=True)
            (source / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        return {
            "returnCode": 0,
            "stdoutTail": skill["revision"] if command[-2:] == ["rev-parse", "HEAD"] else "",
            "stderrTail": "",
        }

    def fake_skills_cli(arguments, **_kwargs):
        cli_arguments.extend(str(item) for item in arguments)
        return {"returnCode": 0, "stdoutTail": "updated", "stderrTail": ""}

    monkeypatch.setattr(service, "_run_skill_git_step", fake_git_step)
    monkeypatch.setattr(service, "_skills_cli_inventory", lambda force=False: copy.deepcopy(inventory))
    monkeypatch.setattr(service, "_run_skills_cli", fake_skills_cli)
    monkeypatch.setattr(
        service,
        "_register_component",
        lambda _plugin_id, component_id, component_type, **_kwargs: {"id": component_id, "type": component_type},
    )

    service._install_skill_component(manifest, skill, action="update")

    assert cli_arguments[0] == "add"
    reviewed_source = Path(cli_arguments[1])
    assert reviewed_source.name == Path(skill["path"]).name
    assert "#" not in cli_arguments[1]
    assert cli_arguments[2:] == [
        "--global",
        "--agent",
        "codex",
        "--copy",
        "--yes",
        "--skill",
        name,
    ]


def test_reviewed_skill_update_rejects_an_unowned_same_name_skill(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("aliyun-bailian")
    skill = manifest.skills[0].model_dump(mode="json")
    name = skill["skillNames"][0]
    target = service_module.AGENT_SKILLS_ROOT / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    inventory = {
        "ok": True,
        "tool": service_module.SKILLS_CLI_PACKAGE,
        "items": [{"name": name, "path": str(target), "scope": "global", "agents": ["Codex"]}],
        "lockEntries": {
            name: {
                "sourceUrl": "https://github.com/example/unrelated",
                "skillPath": f"{skill['path']}/SKILL.md",
            }
        },
        "error": "",
    }

    def fake_git_step(argv, **_kwargs):
        command = [str(item) for item in argv]
        if command[:2] == ["git", "init"]:
            source = Path(command[2]) / skill["path"]
            source.mkdir(parents=True, exist_ok=True)
            (source / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        return {
            "returnCode": 0,
            "stdoutTail": skill["revision"] if command[-2:] == ["rev-parse", "HEAD"] else "",
            "stderrTail": "",
        }

    monkeypatch.setattr(service, "_run_skill_git_step", fake_git_step)
    monkeypatch.setattr(service, "_skills_cli_inventory", lambda force=False: copy.deepcopy(inventory))
    monkeypatch.setattr(
        service,
        "_run_skills_cli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unowned Skill must not be replaced")),
    )

    with pytest.raises(PluginManagerError) as failure:
        service._install_skill_component(manifest, skill, action="update")

    assert failure.value.code == "skill_name_conflict"


def test_skill_update_snapshot_restores_content_and_lock(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = runtime
    name = "reviewed-skill"
    target = service_module.AGENT_SKILLS_ROOT / name
    target.mkdir(parents=True, exist_ok=True)
    skill_file = target / "SKILL.md"
    skill_file.write_text("old skill", encoding="utf-8")
    lock_path = service_module.AGENT_SKILLS_ROOT.parent / ".skill-lock.json"
    lock_path.write_text(json.dumps({"skills": {name: {"ref": "old"}}}), encoding="utf-8")
    monkeypatch.setattr(
        service,
        "_skills_cli_inventory",
        lambda force=False: {
            "ok": True,
            "items": [{"name": name, "path": str(target)}],
            "lockEntries": {name: {"ref": "old"}},
            "error": "",
        },
    )
    backup = service_module.PLUGIN_MANAGER_ROOT / ".staging" / "job.skills.previous"

    snapshot = service._snapshot_skill_state([name], backup_root=backup)
    skill_file.write_text("new skill", encoding="utf-8")
    lock_path.write_text(json.dumps({"skills": {name: {"ref": "new"}}}), encoding="utf-8")

    restored = service._restore_skill_snapshot(snapshot)

    assert restored == {"ok": True, "restored": [name], "errors": []}
    assert skill_file.read_text(encoding="utf-8") == "old skill"
    assert json.loads(lock_path.read_text(encoding="utf-8"))["skills"][name]["ref"] == "old"
    assert not backup.exists()


def test_daily_bundle_installs_all_seven_skills_in_one_reviewed_cli_transaction(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _ = runtime
    manifest = service._manifest("office-suite")
    skill = manifest.skills[0].model_dump(mode="json")
    names = list(skill["skillNames"])
    inventory = {"ok": True, "tool": service_module.SKILLS_CLI_PACKAGE, "items": [], "lockEntries": {}, "error": ""}
    cli_arguments: list[str] = []
    registered: dict = {}

    def fake_git_step(argv, **_kwargs):
        command = [str(item) for item in argv]
        if command[:2] == ["git", "init"]:
            source_root = Path(command[2]) / skill["path"]
            for name in names:
                skill_root = source_root / name
                skill_root.mkdir(parents=True, exist_ok=True)
                (skill_root / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        return {
            "returnCode": 0,
            "stdoutTail": skill["revision"] if command[-2:] == ["rev-parse", "HEAD"] else "",
            "stderrTail": "",
        }

    def fake_skills_cli(arguments, **_kwargs):
        cli_arguments.extend(str(item) for item in arguments)
        items = []
        for name in names:
            target = service_module.AGENT_SKILLS_ROOT / name
            target.mkdir(parents=True, exist_ok=True)
            (target / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
            items.append({"name": name, "path": str(target), "scope": "global", "agents": ["Codex"]})
        inventory["items"] = items
        return {"returnCode": 0, "stdoutTail": "installed", "stderrTail": ""}

    def fake_register(_plugin_id, component_id, component_type, **kwargs):
        registered.update(kwargs)
        return {"id": component_id, "type": component_type}

    monkeypatch.setattr(service, "_run_skill_git_step", fake_git_step)
    monkeypatch.setattr(service, "_skills_cli_inventory", lambda force=False: copy.deepcopy(inventory))
    monkeypatch.setattr(service, "_run_skills_cli", fake_skills_cli)
    monkeypatch.setattr(service, "_register_component", fake_register)

    components = service._install_skill_component(manifest, skill)

    assert components == [{"id": "anthropic-daily-skills", "type": "skill"}]
    assert cli_arguments[-(len(names) + 1):] == ["--skill", *names]
    assert registered["metadata"]["skillNames"] == names
    assert len(registered["metadata"]["skillPaths"]) == 7
    assert registered["metadata"]["managedSkillNames"] == names


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
