from __future__ import annotations

import asyncio
import os
import re
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any


GODOT_MCP_URL = "http://127.0.0.1:8000/mcp"
GODOT_MINIMUM_VERSION = (4, 5, 0)
GODOT_RECOMMENDED_VERSION = (4, 7, 0)
GODOT_SCENARIOS = {"2d", "2.5d", "3d"}
GODOT_AI_FINGERPRINT_TOOLS = {
    "editor_state",
    "node_create",
    "project_manage",
    "scene_get_hierarchy",
}
VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?")


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _step(state: str, *, detail: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"state": state, "detail": detail, **extra}


def validate_godot_executable(value: str) -> dict[str, Any]:
    path = Path(str(value or "").strip()).expanduser()
    if not str(value or "").strip():
        return _step("missing", detail="godot_executable_required")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return _step("invalid", detail="godot_executable_not_found")
    if not resolved.is_file():
        return _step("invalid", detail="godot_executable_not_file")
    if os.name == "nt" and resolved.suffix.lower() != ".exe":
        return _step("invalid", detail="godot_executable_extension_invalid")
    try:
        completed = subprocess.run(
            [str(resolved), "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _step("invalid", detail="godot_version_probe_failed", error=type(exc).__name__)
    output = str(completed.stdout or completed.stderr or "").strip()
    match = VERSION_RE.search(output)
    if completed.returncode != 0 or not match:
        return _step("invalid", detail="godot_version_unrecognized")
    version = tuple(int(part or 0) for part in match.groups())
    if version < GODOT_MINIMUM_VERSION:
        return _step(
            "invalid",
            detail="godot_version_too_old",
            version=".".join(str(item) for item in version),
            minimumVersion="4.5",
        )
    return _step(
        "ready",
        version=".".join(str(item) for item in version),
        rawVersion=output[:160],
        path=str(resolved),
        upgradeRecommended=version < GODOT_RECOMMENDED_VERSION,
        recommendedVersion="4.7",
    )


def validate_godot_project(value: str) -> dict[str, Any]:
    path = Path(str(value or "").strip()).expanduser()
    if not str(value or "").strip():
        return _step("missing", detail="godot_project_required")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return _step("invalid", detail="godot_project_not_found")
    if not resolved.is_dir():
        return _step("invalid", detail="godot_project_not_directory")
    project_file = resolved / "project.godot"
    if not project_file.is_file():
        return _step("invalid", detail="godot_project_file_missing", path=str(resolved))
    return _step("ready", path=str(resolved), projectFile=str(project_file))


async def _probe_godot_mcp_async(*, timeout_seconds: float) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        GODOT_MCP_URL,
        timeout=timeout_seconds,
        sse_read_timeout=timeout_seconds,
    ) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=timeout_seconds),
        ) as session:
            initialized = await asyncio.wait_for(session.initialize(), timeout=timeout_seconds)
            tools_result = await asyncio.wait_for(session.list_tools(), timeout=timeout_seconds)

    tool_names = {
        str(getattr(tool, "name", "") or "").strip()
        for tool in list(getattr(tools_result, "tools", []) or [])
    }
    missing_fingerprint = sorted(GODOT_AI_FINGERPRINT_TOOLS - tool_names)
    if missing_fingerprint:
        return _step(
            "offline",
            detail="godot_mcp_wrong_server",
            endpoint=GODOT_MCP_URL,
            missingFingerprint=missing_fingerprint,
        )

    server_info = getattr(initialized, "serverInfo", None)
    return _step(
        "ready",
        endpoint=GODOT_MCP_URL,
        protocolVersion=str(getattr(initialized, "protocolVersion", "") or ""),
        serverName=str(getattr(server_info, "name", "") or "godot-ai"),
        toolCount=len(tool_names),
    )


def probe_godot_mcp(*, timeout_seconds: float = 3.0) -> dict[str, Any]:
    try:
        return asyncio.run(_probe_godot_mcp_async(timeout_seconds=timeout_seconds))
    except Exception as exc:
        return _step("offline", detail="godot_mcp_unreachable", endpoint=GODOT_MCP_URL, error=type(exc).__name__)


def evaluate_godot_setup(values: dict[str, Any], *, probe_mcp: bool = True) -> dict[str, Any]:
    executable = validate_godot_executable(str(values.get("godotExecutable") or ""))
    project = validate_godot_project(str(values.get("projectPath") or ""))
    scenario_value = str(values.get("scenario") or "").strip().lower()
    scenario = _step("ready", value=scenario_value) if scenario_value in GODOT_SCENARIOS else _step(
        "missing" if not scenario_value else "invalid",
        detail="godot_scenario_required" if not scenario_value else "godot_scenario_invalid",
    )
    prerequisites_ready = executable["state"] == "ready" and project["state"] == "ready"
    mcp = probe_godot_mcp() if probe_mcp and prerequisites_ready else _step(
        "blocked" if not prerequisites_ready else "unchecked",
        detail="godot_mcp_waiting_for_prerequisites" if not prerequisites_ready else "godot_mcp_not_checked",
        endpoint=GODOT_MCP_URL,
    )
    ready = prerequisites_ready and scenario["state"] == "ready" and mcp["state"] == "ready"
    return {
        "adapter": "godot_v1",
        "steps": {
            "application": executable,
            "project": project,
            "scenario": scenario,
            "mcp": mcp,
        },
        "readyForInstall": ready,
        "editorOnline": mcp["state"] == "ready",
        "offlinePrerequisitesReady": prerequisites_ready and scenario["state"] == "ready",
        "blockingReasons": [
            name
            for name, item in (("application", executable), ("project", project), ("scenario", scenario), ("mcp", mcp))
            if item["state"] != "ready"
        ],
    }


def stable_godot_setup_projection(values: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    return {
        "adapter": "godot_v1",
        "godotExecutable": str(values.get("godotExecutable") or ""),
        "projectPath": str(values.get("projectPath") or ""),
        "scenario": str(values.get("scenario") or ""),
        "status": status,
    }
