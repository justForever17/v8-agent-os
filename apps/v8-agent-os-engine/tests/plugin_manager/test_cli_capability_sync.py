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
