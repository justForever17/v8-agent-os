from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from .audit_removed_openclaw import scan_removed_openclaw_residue
except ImportError:  # pragma: no cover - direct script execution
    from audit_removed_openclaw import scan_removed_openclaw_residue


CREATIVE_FACADES = {
    "creative_media_capabilities",
    "creative_media_plan",
    "creative_media_assets",
    "creative_media_jobs",
    "creative_media_edit",
    "creative_media_quality",
}

CREATIVE_AGENT_SURFACE_TARGETS = (
    "apps/v8-agent-os-engine/core/delegated_agent_charter.py",
    "apps/v8-agent-os-engine/core/delegation_broker.py",
    "apps/v8-agent-os-engine/core/runtime_episode_runner.py",
    "apps/v8-agent-os-engine/graph/tool_routing.py",
    "apps/v8-agent-os-engine/runtimes/creative_media/production_pack.py",
)

PLAINTEXT_SECRET_KEYS = {
    "apikey",
    "accesstoken",
    "clientsecret",
    "password",
    "refreshtoken",
    "secret",
    "token",
}


@dataclass(frozen=True)
class Violation:
    check: str
    detail: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _walk_json(value: object, *, path: str = "catalog") -> list[tuple[str, object]]:
    rows: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            next_path = f"{path}.{key}"
            rows.append((next_path, nested))
            rows.extend(_walk_json(nested, path=next_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            rows.extend(_walk_json(nested, path=f"{path}[{index}]"))
    return rows


def scan_runtime_surface_residue(repo_root: Path) -> list[Violation]:
    root = repo_root.resolve()
    violations = [
        Violation("removed_openclaw", f"{item.path}:{item.line}: {item.excerpt}")
        for item in scan_removed_openclaw_residue(root)
    ]

    catalog_path = root / "apps/v8-agent-os-engine/runtimes/plugin_manager/resources/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    for path, value in _walk_json(catalog):
        key = path.rsplit(".", 1)[-1].lower()
        if key in PLAINTEXT_SECRET_KEYS and value not in (None, "", False, [], {}):
            violations.append(Violation("plaintext_secret", path))
    for plugin in catalog.get("plugins", []):
        for server in plugin.get("mcpServers", []):
            if "*" in list(server.get("allowedTools") or []):
                violations.append(Violation("mcp_wildcard", f"{plugin.get('id')}:{server.get('id')}"))
        cli_profiles = {str(item.get("id") or ""): item for item in plugin.get("cliProfiles", [])}
        for skill in plugin.get("skills", []):
            revision = str(skill.get("revision") or "")
            source_kind = str(skill.get("sourceKind") or "git")
            if source_kind == "git" and not re.fullmatch(r"[0-9a-f]{40}", revision):
                violations.append(Violation("unpinned_skill", f"{plugin.get('id')}:{skill.get('id')}@{revision}"))
            elif source_kind == "managed_cli":
                source_component_id = str(skill.get("sourceComponentId") or "")
                source_profile = cli_profiles.get(source_component_id) or {}
                install_argv = list((source_profile.get("install") or {}).get("argv") or [])
                package_ref = str(install_argv[-1] if install_argv else "")
                if (
                    not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", revision)
                    or str(source_profile.get("ownership") or "") != "managed"
                    or not package_ref.endswith(f"@{revision}")
                ):
                    violations.append(
                        Violation(
                            "unpinned_managed_cli_skill",
                            f"{plugin.get('id')}:{skill.get('id')}@{revision}:{source_component_id}",
                        )
                    )
            elif source_kind not in {"git", "managed_cli"}:
                violations.append(Violation("unknown_skill_source", f"{plugin.get('id')}:{skill.get('id')}:{source_kind}"))
        for profile in plugin.get("cliProfiles", []):
            argv = list((profile.get("install") or {}).get("argv") or [])
            if len(argv) >= 2 and argv[:2] == ["npm", "install"]:
                package = str(argv[-1] or "")
                if "@" not in package.lstrip("@"):
                    violations.append(Violation("unpinned_npm", f"{plugin.get('id')}:{profile.get('id')}:{package}"))

    registry_path = root / "apps/v8-agent-os-engine/core/tools/native/registry.py"
    registry_text = registry_path.read_text(encoding="utf-8")
    exposed_creative = set(re.findall(r'["\'](creative_media_[a-z0-9_]+)["\']', registry_text))
    if exposed_creative != CREATIVE_FACADES:
        violations.append(
            Violation(
                "creative_surface",
                f"expected={sorted(CREATIVE_FACADES)} actual={sorted(exposed_creative)}",
            )
        )

    native_tools_path = root / "apps/v8-agent-os-engine/core/native_tools.py"
    native_tools_text = native_tools_path.read_text(encoding="utf-8")
    if re.search(r"from\s+core\.tools\.native\.creative_media(?:_facade)?\s+import\s+\*", native_tools_text):
        violations.append(Violation("creative_wildcard_import", native_tools_path.relative_to(root).as_posix()))

    creative_call_pattern = re.compile(r"\b(creative_media_[a-z0-9_]+)\s*\(")
    for relative in CREATIVE_AGENT_SURFACE_TARGETS:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        for name in creative_call_pattern.findall(text):
            if name not in CREATIVE_FACADES:
                violations.append(Violation("legacy_creative_agent_tool", f"{relative}:{name}"))

    cli_tool_path = root / "apps/v8-agent-os-engine/core/tools/plugin_cli.py"
    cli_tool_text = cli_tool_path.read_text(encoding="utf-8")
    signature = re.search(r"async\s+def\s+plugin_cli\s*\((.*?)\)\s*->", cli_tool_text, re.DOTALL)
    if not signature or re.search(r"\b(?:arguments|argv)\b", signature.group(1)):
        violations.append(Violation("arbitrary_cli_arguments", cli_tool_path.relative_to(root).as_posix()))

    service_path = root / "apps/v8-agent-os-engine/runtimes/plugin_manager/service.py"
    service_text = service_path.read_text(encoding="utf-8")
    execute_cli = re.search(
        r"\n\s{4}async\s+def\s+execute_cli\s*\(.*?(?=\n\s{4}def\s+|\n\s{4}async\s+def\s+|\Z)",
        service_text,
        re.DOTALL,
    )
    if not execute_cli:
        violations.append(Violation("plugin_cli_surface", "execute_cli_missing"))
    else:
        public_block = execute_cli.group(0)
        for key in ("argv", "stdoutTail", "stderrTail", "rawStdout", "rawStderr"):
            if re.search(rf'["\']{re.escape(key)}["\']\s*:', public_block):
                violations.append(Violation("raw_cli_output", f"execute_cli:{key}"))

    client_mentions = (
        root / "apps/v8-agent-os-web/src/components/chat/InputArea.tsx",
        root / "apps/v8-agent-os-phone/src/screens/ChatScreen.tsx",
    )
    for path in client_mentions:
        text = path.read_text(encoding="utf-8")
        if not re.search(r"\bpluginReferences\b", text):
            violations.append(Violation("plugin_reference_missing", path.relative_to(root).as_posix()))
        if re.search(r"contextMentions\s*[:=]\s*\[[\s\S]{0,1800}\.\.\.(?:selected|pending)Plugins\.map", text):
            violations.append(Violation("duplicate_plugin_grant_truth", path.relative_to(root).as_posix()))

    active_sources = (
        root / "apps/v8-agent-os-engine/core/tools/plugin_cli.py",
        root / "apps/v8-agent-os-engine/graph/supervisor_context.py",
        root / "apps/v8-agent-os-engine/core/delegation_broker.py",
        root / "apps/v8-agent-os-engine/core/tools/native/delegation.py",
    )
    forbidden = (
        (re.compile(r"\bpluginIds\b|\bplugin_ids\b"), "duplicate_plugin_grant_truth"),
        (re.compile(r"\barguments\s*:\s*(?:list|List)|arguments\[\]"), "arbitrary_cli_arguments"),
    )
    for path in active_sources:
        text = path.read_text(encoding="utf-8")
        for pattern, check in forbidden:
            if pattern.search(text):
                violations.append(Violation(check, path.relative_to(root).as_posix()))

    return violations


def main() -> int:
    violations = scan_runtime_surface_residue(_repo_root())
    if not violations:
        print("Runtime surface residual audit: clean")
        return 0
    print(f"Runtime surface residual audit: {len(violations)} violation(s)")
    for item in violations:
        print(f"[{item.check}] {item.detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
