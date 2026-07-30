from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from runtimes.plugin_manager import cli_capability_sync as capability_sync


def _action(
    action_id: str,
    *,
    required: list[str] | None = None,
    properties: dict | None = None,
    output_properties: dict | None = None,
) -> dict:
    return {
        "id": action_id,
        "domain": "video",
        "tool": action_id,
        "argv": ["mediakit-cli", "video", action_id],
        "description": action_id,
        "parameters": [],
        "inputSchema": {
            "type": "object",
            "properties": properties or {"source": {"type": "string"}},
            "required": required or [],
        },
        "outputSchema": {
            "type": "object",
            "properties": output_properties or {"url": {"type": "string"}},
        },
        "mutating": True,
        "source": "discovered_schema",
    }


def _snapshot(version: str, actions: list[dict]) -> dict:
    payload = {
        "schemaVersion": capability_sync.SNAPSHOT_SCHEMA_VERSION,
        "adapter": "mediakit_cli_v1",
        "pluginId": "volcengine-mediakit",
        "profileId": "mediakit-cli",
        "cliVersion": version,
        "domainCount": 1,
        "actionCount": len(actions),
        "actions": actions,
    }
    payload["digest"] = capability_sync._digest(payload)
    return payload


def test_parse_mediakit_inventory_keeps_all_domains_and_rejects_duplicates() -> None:
    inventory = capability_sync.parse_mediakit_inventory(
        """
        [audio]
        - probe-audio-metadata inspect audio
        [video]
        - probe-video-metadata inspect video
        """
    )
    assert inventory == [
        {"domain": "audio", "tool": "probe-audio-metadata", "description": "inspect audio"},
        {"domain": "video", "tool": "probe-video-metadata", "description": "inspect video"},
    ]
    with pytest.raises(capability_sync.CliCapabilitySyncError):
        capability_sync.parse_mediakit_inventory("[video]\n- inspect x\n- inspect y")


def test_discovery_compiles_typed_parameters_and_conservative_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(_executable: str, *arguments: str, timeout: int = 30) -> str:
        del timeout
        if arguments == ("version",):
            return "mediakit-cli version 0.2.0"
        if arguments == ("--help-full",):
            return "[video]\n- analyze-video-storyline analyze\n- probe-video-metadata probe"
        tool = arguments[1]
        return json.dumps(
            {
                "description": tool,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "video_urls": {"type": "array", "items": {"type": "string"}},
                        "enable_snapshot": {"type": "boolean", "default": True},
                        "sample_count": {"type": "integer"},
                    },
                    "required": ["video_urls"],
                },
                "output_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}},
            }
        )

    monkeypatch.setattr(capability_sync, "_run", fake_run)
    snapshot = capability_sync.discover_mediakit_snapshot(
        executable="mediakit-cli",
        plugin_id="volcengine-mediakit",
        profile_id="mediakit-cli",
    )
    assert snapshot["actionCount"] == 2
    actions = {item["id"]: item for item in snapshot["actions"]}
    assert actions["analyze-video-storyline"]["mutating"] is True
    assert actions["probe-video-metadata"]["mutating"] is False
    parameters = {item["name"]: item for item in actions["analyze-video-storyline"]["parameters"]}
    assert parameters["videoUrls"]["kind"] == "json"
    assert parameters["videoUrls"]["required"] is True
    assert parameters["enableSnapshot"]["kind"] == "boolean"
    assert parameters["enableSnapshot"]["defaultValue"] is True
    assert parameters["sampleCount"]["kind"] == "integer"


def test_snapshot_compatibility_blocks_removed_required_and_narrowed_contracts() -> None:
    previous = _snapshot(
        "0.2.0",
        [
            _action(
                "render",
                properties={
                    "source": {"type": "string"},
                    "format": {"type": "string", "enum": ["mp4", "mov"]},
                },
            ),
            _action("probe"),
        ],
    )
    candidate = _snapshot(
        "0.3.0",
        [
            _action(
                "render",
                required=["quality"],
                properties={
                    "source": {"type": "string"},
                    "format": {"type": "string", "enum": ["mp4"]},
                    "quality": {"type": "integer"},
                },
            )
        ],
    )
    result = capability_sync.compare_capability_snapshots(previous, candidate)
    assert result["classification"] == "breaking"
    assert {item["code"] for item in result["issues"]} == {
        "action_removed",
        "enum_narrowed",
        "required_parameter_added",
    }


def test_sync_preserves_last_known_good_and_caches_unchanged_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "capabilities" / "mediakit-cli.json"
    baseline = _snapshot("0.2.0", [_action("render"), _action("probe")])
    capability_sync._atomic_write(target, baseline)
    discovery_calls = 0

    monkeypatch.setattr(capability_sync, "discover_mediakit_version", lambda _executable: "0.2.0")

    def should_not_discover(**_kwargs):
        nonlocal discovery_calls
        discovery_calls += 1
        raise AssertionError("unchanged version must use the validated snapshot")

    monkeypatch.setattr(capability_sync, "discover_mediakit_snapshot", should_not_discover)
    cached = capability_sync.sync_mediakit_capabilities(
        executable="mediakit-cli",
        plugin_id="volcengine-mediakit",
        profile_id="mediakit-cli",
        target_path=target,
    )
    assert cached["accepted"] is True
    assert cached["cached"] is True
    assert discovery_calls == 0

    candidate = _snapshot("0.3.0", [_action("render")])
    monkeypatch.setattr(capability_sync, "discover_mediakit_version", lambda _executable: "0.3.0")
    monkeypatch.setattr(
        capability_sync,
        "discover_mediakit_snapshot",
        lambda **_kwargs: copy.deepcopy(candidate),
    )
    blocked = capability_sync.sync_mediakit_capabilities(
        executable="mediakit-cli",
        plugin_id="volcengine-mediakit",
        profile_id="mediakit-cli",
        target_path=target,
    )
    assert blocked["accepted"] is False
    assert capability_sync.read_snapshot(target)["cliVersion"] == "0.2.0"
    assert capability_sync.read_snapshot(target.with_name("candidate.json"))["cliVersion"] == "0.3.0"


def test_snapshot_actions_round_trip_source_defaults_and_json_schema() -> None:
    action = _action("render")
    action["parameters"] = [
        {
            "name": "clips",
            "sourceName": "clips",
            "kind": "json",
            "required": True,
            "flag": "--clips",
            "positional": False,
            "options": [],
            "description": "clip list",
            "defaultValue": None,
        },
        {
            "name": "keepAudio",
            "sourceName": "keep_audio",
            "kind": "boolean",
            "required": False,
            "flag": "--keep-audio",
            "positional": False,
            "options": [],
            "description": None,
            "defaultValue": True,
        },
    ]
    actions = capability_sync.actions_from_snapshot(_snapshot("0.2.0", [action]))
    assert actions[0].parameters[0].kind == "json"
    assert actions[0].parameters[0].description == "clip list"
    assert actions[0].parameters[1].sourceName == "keep_audio"
    assert actions[0].parameters[1].defaultValue is True


def test_gda_schema_sync_preserves_typed_headless_and_live_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "commands": [
            {
                "name": "scene inspect",
                "description": "Inspect a scene without opening the editor",
                "kind": "headless",
                "input": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                "output": {"type": "object", "properties": {"nodes": {"type": "array"}}},
                "constraints": {"platforms": [capability_sync._current_schema_platform()]},
            },
            {
                "name": "game run",
                "description": "Run the project",
                "kind": "live",
                "input": {"type": "object", "properties": {}},
                "output": {"type": "object", "properties": {"pid": {"type": "integer"}}},
            },
        ]
    }

    def fake_run(_executable: str, *arguments: str, timeout: int = 30) -> str:
        del timeout
        if arguments == ("--version",):
            return "gda 0.8.1"
        if arguments == ("schema",):
            return json.dumps(payload)
        raise AssertionError(arguments)

    monkeypatch.setattr(capability_sync, "_run", fake_run)
    snapshot = capability_sync.discover_gda_snapshot(
        executable="gda",
        plugin_id="godot",
        profile_id="gda-cli",
    )

    assert snapshot["adapter"] == "gda_cli_v1"
    assert snapshot["cliVersion"] == "0.8.1"
    assert snapshot["actionCount"] == 2
    actions = {item["id"]: item for item in snapshot["actions"]}
    assert actions["scene.inspect"]["executionKind"] == "headless"
    assert actions["scene.inspect"]["parameters"][0]["required"] is True
    assert actions["game.run"]["executionKind"] == "live"


def test_reviewed_help_parser_supports_cobra_and_yargs_types() -> None:
    roots = capability_sync.parse_reviewed_command_index(
        """
CORE COMMANDS
  repo:       Manage repositories
  issue:      Manage issues

FLAGS
  --help      Show help
""",
        executable="gh",
        reviewed_roots=["repo"],
    )
    assert roots == [{"id": "repo", "description": "Manage repositories"}]

    cobra_roots = capability_sync.parse_reviewed_command_index(
        """
Management Commands:
  container   Manage containers
  image       Manage images
""",
        executable="infra",
        reviewed_roots=["container", "image"],
    )
    assert [item["id"] for item in cobra_roots] == ["container", "image"]

    parameters = capability_sync.parse_reviewed_action_parameters(
        """
Usage: wrangler d1 list [flags]

GLOBAL FLAGS
  --config        Path to Wrangler configuration file  [string]
  --env-file      Path to an environment file  [array]
  --json          Return machine-readable output  [boolean] [default: false]
"""
    )
    by_name = {item["name"]: item for item in parameters}
    assert by_name["config"]["kind"] == "text"
    assert by_name["envFile"]["kind"] == "json"
    assert by_name["json"]["kind"] == "boolean"
    schema = capability_sync._input_schema_from_parameters(parameters)
    assert schema["properties"]["config"]["type"] == "string"
    assert schema["properties"]["envFile"]["type"] == "array"
    assert schema["properties"]["json"]["type"] == "boolean"


def test_reviewed_help_supports_suffix_and_prefix_help_dialects() -> None:
    assert capability_sync._help_argv(
        "aws",
        ["ec2", "describe-instances"],
        ["help"],
        "suffix",
    ) == ("aws", "ec2", "describe-instances", "help")
    assert capability_sync._help_argv(
        "wp",
        ["core", "download"],
        ["help"],
        "prefix",
    ) == ("wp", "help", "core", "download")


def test_reviewed_help_cache_invalidates_when_signed_review_contract_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "capabilities" / "cli-schema.json"
    baseline = {
        "schemaVersion": capability_sync.SNAPSHOT_SCHEMA_VERSION,
        "adapter": "reviewed_help_v1",
        "pluginId": "github",
        "profileId": "gh",
        "cliVersion": "2.96.0",
        "reviewedRoots": ["repo"],
        "helpArguments": ["--help"],
        "helpPlacement": "suffix",
        "rootCommands": [{"id": "repo", "description": "Manage repositories"}],
        "missingRoots": [],
        "commandGroups": {},
        "refreshErrors": [],
        "actionCount": 0,
        "actions": [],
    }
    baseline["digest"] = capability_sync._digest(baseline)
    capability_sync._atomic_write(target, baseline)
    calls: list[tuple[str, ...]] = []

    def fake_run(_executable: str, *arguments: str, timeout: int = 30) -> str:
        del timeout
        calls.append(arguments)
        if arguments == ("--version",):
            return "gh version 2.96.0"
        if arguments == ("--help",):
            return "CORE COMMANDS\n  issue: Manage issues\n  repo: Manage repositories\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(capability_sync, "_run", fake_run)
    result = capability_sync.sync_reviewed_help_capabilities(
        executable="gh",
        canonical_command="gh",
        version_arguments=["--version"],
        plugin_id="github",
        profile_id="gh",
        reviewed_roots=["repo", "issue"],
        help_arguments=["--help"],
        help_placement="suffix",
        target_path=target,
    )

    assert result["accepted"] is True
    assert result.get("cached") is not True
    assert calls.count(("--help",)) == 1
    assert capability_sync.read_snapshot(target)["reviewedRoots"] == ["issue", "repo"]


def test_reviewed_help_resolves_and_caches_one_exact_typed_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "capabilities" / "cli-schema.json"

    def fake_run(_executable: str, *arguments: str, timeout: int = 30) -> str:
        del timeout
        if arguments == ("--version",):
            return "gh version 2.96.0"
        if arguments == ("--help",):
            return "CORE COMMANDS\n  repo: Manage repositories\n"
        if arguments == ("repo", "--help"):
            return "AVAILABLE COMMANDS\n  view: View a repository\n"
        if arguments == ("repo", "view", "--help"):
            return "Usage: gh repo view [<repository>] [flags]\n\nFLAGS\n  -b, --branch string  View a branch\n  -w, --web            Open in a browser\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(capability_sync, "_run", fake_run)
    synced = capability_sync.sync_reviewed_help_capabilities(
        executable="gh",
        canonical_command="gh",
        version_arguments=["--version"],
        plugin_id="github",
        profile_id="gh",
        reviewed_roots=["repo"],
        help_arguments=["--help"],
        help_placement="suffix",
        target_path=target,
    )
    assert synced["accepted"] is True

    group = capability_sync.resolve_reviewed_help_capability(
        executable="gh",
        canonical_command="gh",
        command_path=["repo"],
        help_arguments=["--help"],
        help_placement="suffix",
        target_path=target,
        max_cached_actions=32,
    )
    assert group["kind"] == "group"
    assert [item["id"] for item in group["children"]] == ["view"]

    resolved = capability_sync.resolve_reviewed_help_capability(
        executable="gh",
        canonical_command="gh",
        command_path=["repo", "view"],
        help_arguments=["--help"],
        help_placement="suffix",
        target_path=target,
        max_cached_actions=32,
    )
    assert resolved["kind"] == "action"
    action = resolved["action"]
    assert action["id"] == "repo.view"
    assert action["mutating"] is False
    assert action["inputSchema"]["properties"]["branch"]["type"] == "string"
    assert action["inputSchema"]["properties"]["web"]["type"] == "boolean"
    assert [item["id"] for item in capability_sync.read_snapshot(target)["actions"]] == ["repo.view"]
