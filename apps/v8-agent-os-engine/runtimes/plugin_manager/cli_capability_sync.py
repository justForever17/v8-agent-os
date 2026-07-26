from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .schema import CliAction, CliActionParameter


SNAPSHOT_SCHEMA_VERSION = "v8.plugin.cli-capabilities.v1"
INVENTORY_LINE_RE = re.compile(r"^-\s+([a-z0-9][a-z0-9-]*)\s+(.*)$")
DOMAIN_LINE_RE = re.compile(r"^\[([a-z0-9][a-z0-9_-]*)\]$")
VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)")
READ_ONLY_TOOL_PREFIXES = ("evaluate-", "probe-")
READ_ONLY_TOOL_NAMES = {"query-task"}


class CliCapabilitySyncError(RuntimeError):
    pass


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run(executable: str, *arguments: str, timeout: int = 30) -> str:
    completed = subprocess.run(
        [executable, *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_creation_flags(),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-800:]
        raise CliCapabilitySyncError(
            f"MediaKit schema discovery failed for {' '.join(arguments)}: {detail or completed.returncode}"
        )
    return str(completed.stdout or "")


def discover_mediakit_version(executable: str) -> str:
    version_output = _run(executable, "version", timeout=15)
    version_match = VERSION_RE.search(version_output)
    if not version_match:
        raise CliCapabilitySyncError("MediaKit version output did not contain a semantic version")
    return version_match.group(1)


def parse_mediakit_inventory(help_text: str) -> list[dict[str, str]]:
    domain = ""
    tools: list[dict[str, str]] = []
    for raw_line in str(help_text or "").splitlines():
        line = raw_line.strip()
        domain_match = DOMAIN_LINE_RE.fullmatch(line)
        if domain_match:
            domain = domain_match.group(1)
            continue
        tool_match = INVENTORY_LINE_RE.match(line)
        if not domain or not tool_match:
            continue
        tools.append(
            {
                "domain": domain,
                "tool": tool_match.group(1),
                "description": tool_match.group(2).strip(),
            }
        )
    if not tools:
        raise CliCapabilitySyncError("MediaKit --help-full did not expose any domain tools")
    pairs = {(item["domain"], item["tool"]) for item in tools}
    if len(pairs) != len(tools):
        raise CliCapabilitySyncError("MediaKit --help-full contains duplicate domain/tool entries")
    return tools


def _parse_json_object(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise CliCapabilitySyncError("schema command did not return a JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise CliCapabilitySyncError(f"schema command returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CliCapabilitySyncError("schema command returned a non-object payload")
    return payload


def _lower_camel(value: str) -> str:
    parts = [part for part in str(value or "").strip().split("_") if part]
    if not parts:
        return ""
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _digest(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "digest"}
    encoded = json.dumps(_canonical(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _schema_type(schema: dict[str, Any]) -> str:
    value = schema.get("type")
    if isinstance(value, list):
        normalized = [str(item) for item in value if str(item) != "null"]
        return normalized[0] if len(normalized) == 1 else "|".join(sorted(normalized))
    return str(value or "")


def _parameter_from_schema(name: str, schema: dict[str, Any], required: set[str]) -> dict[str, Any]:
    schema_type = _schema_type(schema)
    enum_values = list(schema.get("enum") or [])
    item_schema = dict(schema.get("items") or {}) if isinstance(schema.get("items"), dict) else {}
    item_enum = list(item_schema.get("enum") or [])
    if schema_type == "boolean":
        kind = "boolean"
    elif schema_type == "integer":
        kind = "integer"
    elif schema_type == "number":
        kind = "number"
    elif schema_type in {"array", "object"}:
        kind = "json"
    elif enum_values:
        kind = "enum"
    elif name.endswith("_path") or str(schema.get("format") or "") in {"path", "file"}:
        kind = "file"
    else:
        kind = "text"
    return {
        "name": _lower_camel(name),
        "sourceName": name,
        "kind": kind,
        "required": name in required,
        "flag": "--" + name.replace("_", "-"),
        "positional": False,
        "options": [str(item) for item in (enum_values or item_enum)],
        "description": str(schema.get("description") or "").strip() or None,
        "defaultValue": _canonical(schema.get("default")),
        "schema": _canonical(schema),
    }


def _allocate_action_ids(
    inventory: list[dict[str, str]],
    previous_snapshot: dict[str, Any] | None,
) -> dict[tuple[str, str], str]:
    previous = {
        (str(item.get("domain") or ""), str(item.get("tool") or "")): str(item.get("id") or "")
        for item in list((previous_snapshot or {}).get("actions") or [])
        if str(item.get("id") or "")
    }
    counts: dict[str, int] = {}
    for item in inventory:
        counts[item["tool"]] = counts.get(item["tool"], 0) + 1
    used = {value for value in previous.values() if value}
    result: dict[tuple[str, str], str] = {}
    for item in sorted(inventory, key=lambda candidate: (candidate["domain"], candidate["tool"])):
        pair = (item["domain"], item["tool"])
        existing = previous.get(pair)
        if existing:
            result[pair] = existing
            continue
        candidate = item["tool"] if counts[item["tool"]] == 1 and item["tool"] not in used else f"{item['domain']}.{item['tool']}"
        if candidate in used:
            suffix = 2
            while f"{candidate}.{suffix}" in used:
                suffix += 1
            candidate = f"{candidate}.{suffix}"
        used.add(candidate)
        result[pair] = candidate
    return result


def discover_mediakit_snapshot(
    *,
    executable: str,
    plugin_id: str,
    profile_id: str,
    previous_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cli_version = discover_mediakit_version(executable)
    inventory = parse_mediakit_inventory(_run(executable, "--help-full", timeout=20))
    action_ids = _allocate_action_ids(inventory, previous_snapshot)
    actions: list[dict[str, Any]] = []
    for item in sorted(inventory, key=lambda candidate: (candidate["domain"], candidate["tool"])):
        raw_schema = _parse_json_object(_run(executable, item["domain"], item["tool"], "--schema", timeout=20))
        input_schema = dict(raw_schema.get("input_schema") or {})
        output_schema = dict(raw_schema.get("output_schema") or {})
        properties = dict(input_schema.get("properties") or {})
        required = {str(name) for name in list(input_schema.get("required") or [])}
        parameters = [
            _parameter_from_schema(str(name), dict(schema or {}), required)
            for name, schema in sorted(properties.items())
        ]
        actions.append(
            {
                "id": action_ids[(item["domain"], item["tool"])],
                "domain": item["domain"],
                "tool": item["tool"],
                "argv": ["mediakit-cli", item["domain"], item["tool"]],
                "description": str(raw_schema.get("description") or item["description"] or "").strip(),
                "parameters": parameters,
                "inputSchema": _canonical(input_schema),
                "outputSchema": _canonical(output_schema),
                "mutating": not (
                    item["tool"].startswith(READ_ONLY_TOOL_PREFIXES)
                    or item["tool"] in READ_ONLY_TOOL_NAMES
                ),
                "source": "discovered_schema",
            }
        )
    snapshot = {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "adapter": "mediakit_cli_v1",
        "pluginId": plugin_id,
        "profileId": profile_id,
        "cliVersion": cli_version,
        "domainCount": len({item["domain"] for item in actions}),
        "actionCount": len(actions),
        "actions": actions,
    }
    snapshot["digest"] = _digest(snapshot)
    return snapshot


def _enum_values(node: dict[str, Any]) -> set[str]:
    values = list(node.get("enum") or [])
    if not values and isinstance(node.get("items"), dict):
        values = list((node.get("items") or {}).get("enum") or [])
    return {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in values}


def _schema_breaks(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    path: str,
    input_contract: bool,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    old_type = _schema_type(old)
    new_type = _schema_type(new)
    if old_type and new_type and old_type != new_type:
        issues.append({"code": "type_changed", "path": path, "before": old_type, "after": new_type})
        return issues
    old_enum = _enum_values(old)
    new_enum = _enum_values(new)
    if old_enum and new_enum and not old_enum.issubset(new_enum):
        issues.append({"code": "enum_narrowed", "path": path})
    old_properties = dict(old.get("properties") or {})
    new_properties = dict(new.get("properties") or {})
    for name in sorted(set(old_properties) - set(new_properties)):
        issues.append({"code": "property_removed", "path": f"{path}.{name}"})
    if input_contract:
        old_required = {str(item) for item in list(old.get("required") or [])}
        new_required = {str(item) for item in list(new.get("required") or [])}
        for name in sorted(new_required - old_required):
            issues.append({"code": "required_parameter_added", "path": f"{path}.{name}"})
    for name in sorted(set(old_properties).intersection(new_properties)):
        issues.extend(
            _schema_breaks(
                dict(old_properties[name] or {}),
                dict(new_properties[name] or {}),
                path=f"{path}.{name}",
                input_contract=input_contract,
            )
        )
    if isinstance(old.get("items"), dict) and isinstance(new.get("items"), dict):
        issues.extend(
            _schema_breaks(
                dict(old.get("items") or {}),
                dict(new.get("items") or {}),
                path=f"{path}[]",
                input_contract=input_contract,
            )
        )
    return issues


def compare_capability_snapshots(
    previous: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    if not previous:
        return {
            "classification": "baseline",
            "breaking": False,
            "addedActions": [str(item.get("id") or "") for item in list(candidate.get("actions") or [])],
            "removedActions": [],
            "issues": [],
        }
    old_actions = {str(item.get("id") or ""): dict(item) for item in list(previous.get("actions") or [])}
    new_actions = {str(item.get("id") or ""): dict(item) for item in list(candidate.get("actions") or [])}
    removed = sorted(set(old_actions) - set(new_actions))
    added = sorted(set(new_actions) - set(old_actions))
    issues: list[dict[str, str]] = [
        {"code": "action_removed", "path": f"actions.{action_id}"}
        for action_id in removed
    ]
    for action_id in sorted(set(old_actions).intersection(new_actions)):
        old_action = old_actions[action_id]
        new_action = new_actions[action_id]
        issues.extend(
            _schema_breaks(
                dict(old_action.get("inputSchema") or {}),
                dict(new_action.get("inputSchema") or {}),
                path=f"actions.{action_id}.input",
                input_contract=True,
            )
        )
        issues.extend(
            _schema_breaks(
                dict(old_action.get("outputSchema") or {}),
                dict(new_action.get("outputSchema") or {}),
                path=f"actions.{action_id}.output",
                input_contract=False,
            )
        )
    breaking = bool(issues)
    identical = str(previous.get("digest") or "") == str(candidate.get("digest") or "")
    return {
        "classification": "identical" if identical else "breaking" if breaking else "compatible",
        "breaking": breaking,
        "addedActions": added,
        "removedActions": removed,
        "issues": issues,
    }


def read_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or payload.get("schemaVersion") != SNAPSHOT_SCHEMA_VERSION:
        return None
    if str(payload.get("digest") or "") != _digest(payload):
        return None
    return payload


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sync_mediakit_capabilities(
    *,
    executable: str,
    plugin_id: str,
    profile_id: str,
    target_path: Path,
    previous_path: Path | None = None,
    block_breaking_upgrade: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    previous = read_snapshot(previous_path or target_path)
    cli_version = discover_mediakit_version(executable)
    if previous and str(previous.get("cliVersion") or "") == cli_version and not force_refresh:
        if target_path != (previous_path or target_path) or not target_path.exists():
            _atomic_write(target_path, previous)
        report = {
            "classification": "identical",
            "breaking": False,
            "addedActions": [],
            "removedActions": [],
            "issues": [],
            "previousVersion": cli_version,
            "candidateVersion": cli_version,
            "previousDigest": previous.get("digest"),
            "candidateDigest": previous.get("digest"),
            "cached": True,
        }
        report_path = target_path.with_name("compatibility.json")
        _atomic_write(report_path, report)
        return {
            "ok": True,
            "accepted": True,
            "snapshotPath": str(target_path),
            "compatibilityPath": str(report_path),
            "actionCount": int(previous.get("actionCount") or 0),
            **report,
        }
    candidate = discover_mediakit_snapshot(
        executable=executable,
        plugin_id=plugin_id,
        profile_id=profile_id,
        previous_snapshot=previous,
    )
    compatibility = compare_capability_snapshots(previous, candidate)
    report = {
        **compatibility,
        "previousVersion": (previous or {}).get("cliVersion"),
        "candidateVersion": candidate.get("cliVersion"),
        "previousDigest": (previous or {}).get("digest"),
        "candidateDigest": candidate.get("digest"),
    }
    report_path = target_path.with_name("compatibility.json")
    _atomic_write(report_path, report)
    if compatibility["breaking"] and previous and block_breaking_upgrade:
        _atomic_write(target_path.with_name("candidate.json"), candidate)
        return {
            "ok": False,
            "accepted": False,
            "snapshotPath": str(target_path),
            "compatibilityPath": str(report_path),
            "candidatePath": str(target_path.with_name("candidate.json")),
            "actionCount": int(candidate.get("actionCount") or 0),
            **report,
        }
    if target_path.exists():
        existing = read_snapshot(target_path)
        if existing:
            _atomic_write(target_path.with_name("previous.json"), existing)
    _atomic_write(target_path, candidate)
    history_path = target_path.parent / "history" / f"{candidate['cliVersion']}-{candidate['digest']}.json"
    _atomic_write(history_path, candidate)
    candidate_path = target_path.with_name("candidate.json")
    if candidate_path.exists():
        candidate_path.unlink()
    return {
        "ok": True,
        "accepted": True,
        "snapshotPath": str(target_path),
        "compatibilityPath": str(report_path),
        "actionCount": int(candidate.get("actionCount") or 0),
        **report,
    }


def actions_from_snapshot(snapshot: dict[str, Any] | None) -> list[CliAction]:
    actions: list[CliAction] = []
    for item in list((snapshot or {}).get("actions") or []):
        parameters = [
            CliActionParameter(
                name=str(parameter.get("name") or ""),
                sourceName=str(parameter.get("sourceName") or "") or None,
                kind=str(parameter.get("kind") or "text"),
                required=bool(parameter.get("required")),
                flag=str(parameter.get("flag") or "") or None,
                positional=bool(parameter.get("positional")),
                options=[str(value) for value in list(parameter.get("options") or [])],
                description=str(parameter.get("description") or "") or None,
                defaultValue=parameter.get("defaultValue"),
            )
            for parameter in list(item.get("parameters") or [])
            if str(parameter.get("name") or "")
        ]
        actions.append(
            CliAction(
                id=str(item.get("id") or ""),
                argv=[str(value) for value in list(item.get("argv") or [])],
                parameters=parameters,
                timeoutSeconds=600,
                mutating=bool(item.get("mutating")),
                description=str(item.get("description") or "") or None,
                source="discovered_schema",
                inputSchema=dict(item.get("inputSchema") or {}),
                outputSchema=dict(item.get("outputSchema") or {}),
            )
        )
    return actions


def merge_discovered_actions(declared: Iterable[CliAction], discovered: Iterable[CliAction]) -> list[CliAction]:
    discovered_by_id = {item.id: item for item in discovered}
    extras = [item for item in declared if item.id not in discovered_by_id]
    return [*sorted(discovered_by_id.values(), key=lambda item: item.id), *extras]
