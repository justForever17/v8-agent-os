from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .schema import CliAction, CliActionParameter


SNAPSHOT_SCHEMA_VERSION = "v8.plugin.cli-capabilities.v1"
INVENTORY_LINE_RE = re.compile(r"^-\s+([a-z0-9][a-z0-9-]*)\s+(.*)$")
DOMAIN_LINE_RE = re.compile(r"^\[([a-z0-9][a-z0-9_-]*)\]$")
VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
COMMAND_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$", re.I)
HELP_SECTION_EXCLUSIONS = {
    "arguments", "examples", "flags", "global flags", "help topics", "inherited flags",
    "learn more", "options", "usage",
}
READ_ONLY_COMMANDS = {
    "describe", "diff", "get", "help", "history", "images", "info", "inspect", "list",
    "logs", "ps", "search", "show", "status", "tail", "version", "view", "whoami",
}
READ_ONLY_COMMAND_PREFIXES = (
    "describe-", "diff-", "get-", "list-", "query-", "search-", "show-",
)
READ_ONLY_TOOL_PREFIXES = ("evaluate-", "probe-")
READ_ONLY_TOOL_NAMES = {"query-task"}
GDA_READ_ONLY_COMMANDS = {
    "daemon status", "diag errors", "export get", "export list", "game get", "game rect",
    "game tree", "info", "logger tail", "node get", "node list", "perf monitor", "perf monitors",
    "project dependencies", "project find-references", "project find-unused-resources", "project get",
    "project info", "project list", "project statistics", "resource get", "resource uid", "scene get",
    "scene get-exports", "scene list", "screen capture", "screen frames", "script get", "script list",
    "script validate", "shader get", "skill",
}


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
        env={
            **os.environ,
            "AWS_PAGER": "",
            "CLICOLOR": "0",
            "GIT_PAGER": "cat",
            "NO_COLOR": "1",
            "PAGER": "cat",
            "PYTHONUTF8": "1",
        },
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-800:]
        raise CliCapabilitySyncError(
            f"CLI schema discovery failed for {' '.join(arguments)}: {detail or completed.returncode}"
        )
    return str(completed.stdout or completed.stderr or "")


def _clean_help_text(value: str) -> str:
    text = ANSI_RE.sub("", str(value or "")).replace("\r", "")
    while "\b" in text:
        text = re.sub(r"[^\n]\x08", "", text)
    return text


def _command_name(value: str) -> str:
    return str(value or "").strip().rstrip(":*")


def parse_reviewed_command_index(
    help_text: str,
    *,
    executable: str,
    command_prefix: Iterable[str] | None = None,
    reviewed_roots: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    reviewed = {
        _command_name(item).lower()
        for item in list(reviewed_roots or [])
        if _command_name(item) and not _command_name(item).startswith("-")
    }
    executable_names = {
        Path(str(executable or "")).name.lower(),
        Path(str(executable or "")).stem.lower(),
    }
    prefix = [_command_name(item).lower() for item in list(command_prefix or [])]
    command_section = False
    commands: dict[str, dict[str, str]] = {}
    for raw_line in _clean_help_text(help_text).splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        header = stripped.rstrip(":").strip().lower()
        looks_like_header = bool(
            stripped.endswith(":")
            or (stripped.upper() == stripped and re.search(r"[A-Z]", stripped))
        )
        if looks_like_header and not raw_line[:1].isspace():
            command_section = (
                header not in HELP_SECTION_EXCLUSIONS
                and ("command" in header or stripped.upper() == stripped)
            )
            continue
        if not command_section or not raw_line[:1].isspace():
            continue
        match = re.match(r"^\s{2,}([A-Za-z0-9][A-Za-z0-9._:-]*):\s+(\S.*)$", raw_line)
        if not match:
            match = re.match(r"^\s{2,}(.+?)\s{2,}(\S.*)$", raw_line)
        if not match:
            continue
        usage, description = match.groups()
        usage_tokens = usage.strip().split()
        if not usage_tokens:
            continue
        if usage_tokens[0].lower().rstrip(":") in executable_names:
            usage_tokens = usage_tokens[1:]
        if prefix and [item.lower().rstrip(":") for item in usage_tokens[: len(prefix)]] == prefix:
            usage_tokens = usage_tokens[len(prefix) :]
        if not usage_tokens:
            continue
        name = _command_name(usage_tokens[0])
        if not COMMAND_TOKEN_RE.fullmatch(name) or name.startswith("-"):
            continue
        normalized = name.lower()
        if reviewed and normalized not in reviewed:
            continue
        commands.setdefault(
            normalized,
            {"id": name, "description": description.strip()[:500]},
        )
    return [commands[key] for key in sorted(commands)]


def _parameter_name(value: str) -> str:
    parts = [part for part in re.split(r"[-_\s]+", str(value or "").strip()) if part]
    if not parts:
        return ""
    return parts[0].lower() + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _usage_line(help_text: str) -> str:
    lines = _clean_help_text(help_text).splitlines()
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if re.match(r"^usage\s*:", stripped, re.I):
            return re.sub(r"^usage\s*:\s*", "", stripped, flags=re.I)
        if stripped.lower() == "usage" and index + 1 < len(lines):
            return lines[index + 1].strip()
    return ""


def _kind_from_help(type_hint: str, description: str) -> str:
    combined = f"{type_hint} {description}".lower()
    if "[boolean]" in combined or type_hint.lower() in {"bool", "boolean"}:
        return "boolean"
    if "[string]" in combined or type_hint.lower() in {"str", "string"}:
        return "text"
    if "[number]" in combined or type_hint.lower() in {"float", "number"}:
        return "number"
    if type_hint.lower() in {"int", "integer"}:
        return "integer"
    if "[array]" in combined or type_hint.lower() in {"array", "json"}:
        return "json"
    if any(token in type_hint.lower() for token in ("file", "path", "directory")):
        return "file"
    return "text" if type_hint else "boolean"


def parse_reviewed_action_parameters(help_text: str) -> list[dict[str, Any]]:
    text = _clean_help_text(help_text)
    parameters: dict[str, dict[str, Any]] = {}
    usage = _usage_line(text)
    for match in re.finditer(r"(?P<optional>\[)?<(?P<angle>[A-Za-z][A-Za-z0-9_.-]*)(?:\.\.\.)?>\]?|\[(?P<bracket>[A-Za-z][A-Za-z0-9_.-]*)(?:\.\.\.)?\]", usage):
        source_name = str(match.group("angle") or match.group("bracket") or "")
        if source_name.lower() in {"command", "flags", "options", "subcommand"}:
            continue
        name = _parameter_name(source_name)
        if not name:
            continue
        parameters.setdefault(
            name,
            {
                "name": name,
                "sourceName": source_name,
                "kind": "text",
                "required": bool(match.group("angle") and not match.group("optional")),
                "flag": None,
                "positional": True,
                "options": [],
                "description": None,
                "defaultValue": None,
            },
        )

    in_options = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        header = stripped.rstrip(":").lower()
        if header in {"flags", "global flags", "inherited flags", "options"}:
            in_options = True
            continue
        if in_options and stripped and not raw_line[:1].isspace():
            in_options = False
        if not in_options:
            continue
        match = re.match(
            r"^\s*(?:-[A-Za-z0-9],\s*)?(?P<flag>--[A-Za-z0-9][A-Za-z0-9-]*)(?:[ =](?P<type><[^>]+>|[A-Za-z][A-Za-z0-9_-]*))?\s{2,}(?P<description>\S.*)$",
            raw_line,
        )
        if not match:
            continue
        flag = match.group("flag")
        if flag in {"--help", "--version"}:
            continue
        source_name = flag[2:]
        name = _parameter_name(source_name)
        type_hint = str(match.group("type") or "").strip("<>")
        description = match.group("description").strip()
        choice_match = re.search(r"\[choices?:\s*([^\]]+)\]", description, re.I)
        choices = []
        if choice_match:
            choices = [item.strip().strip("\"'") for item in choice_match.group(1).split(",") if item.strip()]
        kind = "enum" if choices else _kind_from_help(type_hint, description)
        parameters[name] = {
            "name": name,
            "sourceName": source_name,
            "kind": kind,
            "required": bool(re.search(r"\brequired\b", description, re.I)),
            "flag": flag,
            "positional": False,
            "options": choices,
            "description": description[:500],
            "defaultValue": True if "[default: true]" in description.lower() else False if "[default: false]" in description.lower() else None,
        }
    return list(parameters.values())


def _input_schema_from_parameters(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    type_by_kind = {
        "boolean": "boolean", "integer": "integer", "number": "number", "json": "object",
    }
    for parameter in parameters:
        name = str(parameter.get("name") or "")
        if not name:
            continue
        parameter_kind = str(parameter.get("kind") or "")
        schema_type = type_by_kind.get(parameter_kind, "string")
        if parameter_kind == "json" and "[array]" in str(parameter.get("description") or "").lower():
            schema_type = "array"
        schema: dict[str, Any] = {"type": schema_type}
        if parameter.get("description"):
            schema["description"] = str(parameter["description"])
        if parameter.get("options"):
            schema["enum"] = list(parameter["options"])
        properties[name] = schema
        if parameter.get("required"):
            required.append(name)
    return {"type": "object", "properties": properties, "required": sorted(required)}


def _help_argv(
    executable: str,
    command_path: Iterable[str],
    help_arguments: Iterable[str],
    help_placement: str,
) -> tuple[str, ...]:
    path = [str(item) for item in command_path]
    help_tokens = [str(item) for item in help_arguments]
    return tuple(
        [executable, *help_tokens, *path]
        if help_placement == "prefix"
        else [executable, *path, *help_tokens]
    )


def _version_identity(output: str) -> str:
    cleaned = _clean_help_text(output).strip()
    match = VERSION_RE.search(cleaned)
    if match:
        return match.group(1)
    first_line = next((line.strip() for line in cleaned.splitlines() if line.strip()), "")
    if not first_line:
        raise CliCapabilitySyncError("CLI version command returned no version identity")
    return first_line[:200]


def _resolve_reviewed_help_path(
    *,
    executable: str,
    command_path: list[str],
    help_arguments: list[str],
    help_placement: str,
    snapshot: dict[str, Any],
    canonical_command: str,
) -> dict[str, Any]:
    normalized_path = [_command_name(item).lower() for item in command_path]
    if not normalized_path or len(normalized_path) > 8 or any(not COMMAND_TOKEN_RE.fullmatch(item) for item in normalized_path):
        raise CliCapabilitySyncError("CLI command path must contain 1-8 literal command tokens")
    root_ids = {str(item.get("id") or "").lower() for item in list(snapshot.get("rootCommands") or [])}
    if normalized_path[0] not in root_ids:
        raise CliCapabilitySyncError("CLI command root is not in the signed reviewed command set")
    groups = dict(snapshot.get("commandGroups") or {})
    final_text = ""
    final_children: list[dict[str, str]] = []
    for index in range(len(normalized_path)):
        prefix = normalized_path[: index + 1]
        argv = _help_argv(executable, prefix, help_arguments, help_placement)
        final_text = _run(argv[0], *argv[1:], timeout=20)
        final_children = parse_reviewed_command_index(
            final_text,
            executable=executable,
            command_prefix=prefix,
        )
        groups[".".join(prefix)] = final_children
        if index + 1 < len(normalized_path):
            child_ids = {str(item.get("id") or "").lower() for item in final_children}
            if normalized_path[index + 1] not in child_ids:
                raise CliCapabilitySyncError("CLI subcommand is not present in its parent help contract")
    if final_children:
        return {
            "kind": "group",
            "commandPath": normalized_path,
            "children": final_children,
            "commandGroups": groups,
        }
    parameters = parse_reviewed_action_parameters(final_text)
    action_id = ".".join(normalized_path)
    return {
        "kind": "action",
        "commandPath": normalized_path,
        "commandGroups": groups,
        "action": {
            "id": action_id,
            "commandPath": normalized_path,
            "argv": [canonical_command, *normalized_path],
            "description": next((line.strip() for line in _clean_help_text(final_text).splitlines() if line.strip()), action_id)[:500],
            "parameters": parameters,
            "inputSchema": _input_schema_from_parameters(parameters),
            "outputSchema": {"type": "object"},
            "mutating": not (
                normalized_path[-1] in READ_ONLY_COMMANDS
                or normalized_path[-1].startswith(READ_ONLY_COMMAND_PREFIXES)
            ),
            "source": "discovered_schema",
        },
    }


def _reviewed_root_names(values: Iterable[str]) -> list[str]:
    return sorted(
        {
            _command_name(item).lower()
            for item in values
            if _command_name(item)
            and not _command_name(item).startswith("-")
            and COMMAND_TOKEN_RE.fullmatch(_command_name(item))
        }
    )


def discover_reviewed_help_snapshot(
    *,
    executable: str,
    canonical_command: str,
    version_arguments: list[str],
    plugin_id: str,
    profile_id: str,
    reviewed_roots: list[str],
    help_arguments: list[str],
    help_placement: str,
    previous_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cli_version = _version_identity(_run(executable, *version_arguments, timeout=20))
    help_argv = _help_argv(executable, [], help_arguments, help_placement)
    help_text = _run(help_argv[0], *help_argv[1:], timeout=20)
    normalized_reviewed = _reviewed_root_names(reviewed_roots)
    root_commands = parse_reviewed_command_index(
        help_text,
        executable=executable,
        reviewed_roots=normalized_reviewed,
    )
    if not root_commands:
        raise CliCapabilitySyncError("CLI help did not expose any reviewed root commands")
    root_ids = {str(item.get("id") or "").lower() for item in root_commands}
    provisional = {
        "rootCommands": root_commands,
        "commandGroups": {},
    }
    actions: list[dict[str, Any]] = []
    refresh_errors: list[dict[str, str]] = []
    for previous_action in list((previous_snapshot or {}).get("actions") or []):
        command_path = [
            str(item).strip().lower()
            for item in list(previous_action.get("commandPath") or [])
            if str(item).strip()
        ]
        if not command_path:
            command_path = [
                item for item in str(previous_action.get("id") or "").split(".") if item
            ]
        try:
            resolved = _resolve_reviewed_help_path(
                executable=executable,
                command_path=command_path,
                help_arguments=help_arguments,
                help_placement=help_placement,
                snapshot=provisional,
                canonical_command=canonical_command,
            )
        except CliCapabilitySyncError as exc:
            refresh_errors.append(
                {"actionId": str(previous_action.get("id") or ""), "error": str(exc)}
            )
            continue
        provisional["commandGroups"] = dict(resolved.get("commandGroups") or {})
        if resolved.get("kind") == "action":
            actions.append(dict(resolved.get("action") or {}))
    snapshot = {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "adapter": "reviewed_help_v1",
        "pluginId": plugin_id,
        "profileId": profile_id,
        "cliVersion": cli_version,
        "reviewedRoots": normalized_reviewed,
        "helpArguments": list(help_arguments),
        "helpPlacement": help_placement,
        "rootCommands": root_commands,
        "missingRoots": sorted(set(normalized_reviewed) - root_ids),
        "commandGroups": provisional["commandGroups"],
        "refreshErrors": refresh_errors,
        "actionCount": len(actions),
        "actions": sorted(actions, key=lambda item: str(item.get("id") or "")),
    }
    snapshot["digest"] = _digest(snapshot)
    return snapshot


def sync_reviewed_help_capabilities(
    *,
    executable: str,
    canonical_command: str,
    version_arguments: list[str],
    plugin_id: str,
    profile_id: str,
    reviewed_roots: list[str],
    help_arguments: list[str],
    help_placement: str,
    target_path: Path,
    previous_path: Path | None = None,
    block_breaking_upgrade: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    previous = read_snapshot(previous_path or target_path)
    cli_version = _version_identity(_run(executable, *version_arguments, timeout=20))
    same_review_contract = bool(
        previous
        and list(previous.get("reviewedRoots") or []) == _reviewed_root_names(reviewed_roots)
        and list(previous.get("helpArguments") or []) == list(help_arguments)
        and str(previous.get("helpPlacement") or "") == help_placement
    )
    if (
        previous
        and str(previous.get("cliVersion") or "") == cli_version
        and same_review_contract
        and not force_refresh
    ):
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
            "rootCount": len(list(previous.get("rootCommands") or [])),
            "actionCount": int(previous.get("actionCount") or 0),
            **report,
        }
    candidate = discover_reviewed_help_snapshot(
        executable=executable,
        canonical_command=canonical_command,
        version_arguments=version_arguments,
        plugin_id=plugin_id,
        profile_id=profile_id,
        reviewed_roots=reviewed_roots,
        help_arguments=help_arguments,
        help_placement=help_placement,
        previous_snapshot=previous,
    )
    compatibility = compare_capability_snapshots(previous, candidate)
    report = {
        **compatibility,
        "previousVersion": (previous or {}).get("cliVersion"),
        "candidateVersion": candidate.get("cliVersion"),
        "previousDigest": (previous or {}).get("digest"),
        "candidateDigest": candidate.get("digest"),
        "refreshErrors": list(candidate.get("refreshErrors") or []),
    }
    report_path = target_path.with_name("compatibility.json")
    _atomic_write(report_path, report)
    if compatibility["breaking"] and previous and block_breaking_upgrade:
        candidate_path = target_path.with_name("candidate.json")
        _atomic_write(candidate_path, candidate)
        return {
            "ok": False,
            "accepted": False,
            "snapshotPath": str(target_path),
            "compatibilityPath": str(report_path),
            "candidatePath": str(candidate_path),
            "rootCount": len(list(candidate.get("rootCommands") or [])),
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
        "rootCount": len(list(candidate.get("rootCommands") or [])),
        "actionCount": int(candidate.get("actionCount") or 0),
        **report,
    }


def resolve_reviewed_help_capability(
    *,
    executable: str,
    canonical_command: str,
    command_path: list[str],
    help_arguments: list[str],
    help_placement: str,
    target_path: Path,
    max_cached_actions: int,
    block_breaking_upgrade: bool = True,
) -> dict[str, Any]:
    previous = read_snapshot(target_path)
    if not previous or previous.get("adapter") != "reviewed_help_v1":
        raise CliCapabilitySyncError("CLI reviewed help baseline is unavailable")
    resolved = _resolve_reviewed_help_path(
        executable=executable,
        command_path=command_path,
        help_arguments=help_arguments,
        help_placement=help_placement,
        snapshot=previous,
        canonical_command=canonical_command,
    )
    candidate = json.loads(json.dumps(previous, ensure_ascii=False))
    candidate["commandGroups"] = dict(resolved.get("commandGroups") or {})
    if resolved.get("kind") == "group":
        candidate["digest"] = _digest(candidate)
        _atomic_write(target_path, candidate)
        return {
            "ok": True,
            "kind": "group",
            "commandPath": list(resolved.get("commandPath") or []),
            "children": list(resolved.get("children") or []),
        }
    action = dict(resolved.get("action") or {})
    action_id = str(action.get("id") or "")
    actions_by_id = {
        str(item.get("id") or ""): dict(item)
        for item in list(candidate.get("actions") or [])
        if str(item.get("id") or "")
    }
    if action_id not in actions_by_id and len(actions_by_id) >= max_cached_actions:
        raise CliCapabilitySyncError("CLI reviewed action cache is full")
    actions_by_id[action_id] = action
    candidate["actions"] = [actions_by_id[key] for key in sorted(actions_by_id)]
    candidate["actionCount"] = len(actions_by_id)
    candidate["digest"] = _digest(candidate)
    compatibility = compare_capability_snapshots(previous, candidate)
    if compatibility["breaking"] and block_breaking_upgrade:
        _atomic_write(target_path.with_name("candidate.json"), candidate)
        raise CliCapabilitySyncError("CLI action help changed incompatibly; previous action contract was preserved")
    _atomic_write(target_path, candidate)
    return {
        "ok": True,
        "kind": "action",
        "commandPath": list(resolved.get("commandPath") or []),
        "action": action,
    }


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


def discover_gda_version(executable: str) -> str:
    version_output = _run(executable, "--version", timeout=15)
    version_match = VERSION_RE.search(version_output)
    if not version_match:
        raise CliCapabilitySyncError("gda version output did not contain a semantic version")
    return version_match.group(1)


def _current_schema_platform() -> str:
    system = platform.system().lower()
    return "macos" if system == "darwin" else system


def discover_gda_snapshot(
    *,
    executable: str,
    plugin_id: str,
    profile_id: str,
) -> dict[str, Any]:
    cli_version = discover_gda_version(executable)
    payload = _parse_json_object(_run(executable, "schema", timeout=30))
    commands = list(payload.get("commands") or [])
    current_platform = _current_schema_platform()
    actions: list[dict[str, Any]] = []
    for raw in commands:
        item = dict(raw or {})
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        constraints = dict(item.get("constraints") or {})
        supported_platforms = {str(value).lower() for value in list(constraints.get("platforms") or [])}
        if supported_platforms and current_platform not in supported_platforms:
            continue
        input_schema = dict(item.get("input") or {})
        output_schema = dict(item.get("output") or {})
        properties = dict(input_schema.get("properties") or {})
        required = {str(value) for value in list(input_schema.get("required") or [])}
        actions.append(
            {
                "id": name.replace(" ", "."),
                "domain": name.split(" ", 1)[0],
                "tool": name,
                "argv": ["gda", *name.split(), "--json"],
                "description": str(item.get("description") or "").strip(),
                "parameters": [
                    _parameter_from_schema(str(parameter), dict(schema or {}), required)
                    for parameter, schema in sorted(properties.items())
                ],
                "inputSchema": _canonical(input_schema),
                "outputSchema": _canonical(output_schema),
                "errorSchema": _canonical(dict(item.get("error") or {})),
                "constraints": _canonical(constraints),
                "executionKind": str(item.get("kind") or "headless"),
                "mutating": name not in GDA_READ_ONLY_COMMANDS,
                "source": "discovered_schema",
            }
        )
    if not actions:
        raise CliCapabilitySyncError("gda schema did not expose any commands for this platform")
    snapshot = {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "adapter": "gda_cli_v1",
        "pluginId": plugin_id,
        "profileId": profile_id,
        "cliVersion": cli_version,
        "domainCount": len({item["domain"] for item in actions}),
        "actionCount": len(actions),
        "actions": sorted(actions, key=lambda item: item["id"]),
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
    old_roots = {str(item.get("id") or "") for item in list(previous.get("rootCommands") or [])}
    new_roots = {str(item.get("id") or "") for item in list(candidate.get("rootCommands") or [])}
    removed = sorted(set(old_actions) - set(new_actions))
    added = sorted(set(new_actions) - set(old_actions))
    issues: list[dict[str, str]] = [
        {"code": "action_removed", "path": f"actions.{action_id}"}
        for action_id in removed
    ]
    issues.extend(
        {"code": "root_command_removed", "path": f"rootCommands.{command_id}"}
        for command_id in sorted(old_roots - new_roots)
    )
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


def sync_gda_capabilities(
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
    cli_version = discover_gda_version(executable)
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
    candidate = discover_gda_snapshot(
        executable=executable,
        plugin_id=plugin_id,
        profile_id=profile_id,
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
        candidate_path = target_path.with_name("candidate.json")
        _atomic_write(candidate_path, candidate)
        return {
            "ok": False,
            "accepted": False,
            "snapshotPath": str(target_path),
            "compatibilityPath": str(report_path),
            "candidatePath": str(candidate_path),
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
