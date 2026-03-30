from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.storage import LEGACY_STRUCTURED_FILE_TO_DOMAIN
from core.v8_agent_os_paths import LEGACY_CONFIG_BACKUP_ROOT, V8_AGENT_OS_HOME


NEW_HOME = V8_AGENT_OS_HOME
OLD_HOME = Path.home() / ".v8chat"
CANONICAL_CONFIG = NEW_HOME / "config.json"
LEGACY_CONFIG = OLD_HOME / "config.json"
CANONICAL_PROMPT = NEW_HOME / "V8_AGENT_OS.md"
LEGACY_PROMPT = OLD_HOME / "V8CHAT.md"
BACKUPS_JSON_DIR = NEW_HOME / "backups" / "json"
BACKUPS_STATE_DIR = NEW_HOME / "backups" / "state"

ACTIVE_STANDALONE_FILES = {
    "config.json",
    "plugin.json",
    "computer_use.json",
    "network_supervisor_secrets.json",
    "network_supervisor_state.json",
    "V8_AGENT_OS.md",
    "state.db",
    "checkpoints.db",
    "users.json",
}

ACTIVE_RUNTIME_DIRS = {
    "agents",
    "commands",
    "core",
    "memory",
    "plugins",
    "rpa",
    "tools",
    "workspace",
}

RUNTIME_CACHES_AND_ARTIFACTS = {
    "extensions_runtime_cache.json",
    "skills_inventory_cache.json",
    "logs",
    "cache",
    "computer_use_traces",
    "web_fetch",
    "runtime",
    "sessions",
    "todos",
    "tmp",
    "_legacy_config_backup",
    "backups",
}

LEGACY_ALIAS_FILES = {"settings.json", *LEGACY_STRUCTURED_FILE_TO_DOMAIN.keys()}
OBSOLETE_ROOT_FILES = {
    "v8chat.db",
    "system_audit_log.db",
}

ENV_CANDIDATES = [
    REPO_ROOT / "apps" / "v8-agent-os-admin" / ".env",
    REPO_ROOT / "apps" / "v8-agent-os-admin" / ".env.local",
    REPO_ROOT / "apps" / "v8-agent-os-web" / ".env",
    REPO_ROOT / "apps" / "v8-agent-os-web" / ".env.local",
]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _safe_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _legacy_env_hits() -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for env_path in ENV_CANDIDATES:
        if not env_path.exists():
            continue
        raw = _safe_text(env_path)
        if "(127.0.0.1|localhost):(8000|5001)" in raw:
            # defensive fallback if a literal regex snippet lands in the file
            hits.append({"path": str(env_path), "legacyPortMatch": True})
            continue
        if any(token in raw for token in (":8000", ":5001", "localhost:8000", "127.0.0.1:8000", "localhost:5001", "127.0.0.1:5001")):
            hits.append({"path": str(env_path), "legacyPortMatch": True})
    return hits


def _top_level_domain_diff() -> dict[str, Any]:
    new_payload = _read_json(CANONICAL_CONFIG)
    old_payload = _read_json(LEGACY_CONFIG)
    new_keys = sorted(new_payload.keys())
    old_keys = sorted(old_payload.keys())
    return {
        "newOnly": [key for key in new_keys if key not in old_payload],
        "oldOnly": [key for key in old_keys if key not in new_payload],
        "changed": [
            key
            for key in sorted(set(new_keys) & set(old_keys))
            if json.dumps(new_payload.get(key), ensure_ascii=False, sort_keys=True)
            != json.dumps(old_payload.get(key), ensure_ascii=False, sort_keys=True)
        ],
    }


def _prompt_diff_summary() -> dict[str, Any]:
    new_text = _safe_text(CANONICAL_PROMPT)
    old_text = _safe_text(LEGACY_PROMPT)
    return {
        "canonicalPromptPath": str(CANONICAL_PROMPT),
        "legacyPromptPath": str(LEGACY_PROMPT),
        "canonicalExists": CANONICAL_PROMPT.exists(),
        "legacyExists": LEGACY_PROMPT.exists(),
        "sameContent": bool(new_text and old_text and new_text == old_text),
        "canonicalLength": len(new_text),
        "legacyLength": len(old_text),
    }


def _legacy_settings_keys() -> list[str]:
    config = _read_json(CANONICAL_CONFIG)
    system_base = config.get("systemBase") if isinstance(config.get("systemBase"), dict) else {}
    legacy_settings = system_base.get("legacySettings") if isinstance(system_base, dict) else []
    if not isinstance(legacy_settings, list):
        return []
    keys = []
    for item in legacy_settings:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key:
            keys.append(key)
    return keys


def _supervisor_profile(payload: dict[str, Any]) -> dict[str, str]:
    supervisor = payload.get("supervisor") if isinstance(payload.get("supervisor"), dict) else {}
    profile = supervisor.get("profile") if isinstance(supervisor, dict) else {}
    if not isinstance(profile, dict):
        profile = {}
    return {
        "name": str(profile.get("name") or "").strip(),
        "roleLabel": str(profile.get("roleLabel") or "").strip(),
        "avatar": str(profile.get("avatar") or "").strip(),
    }


def _supervisor_shadow_sources() -> dict[str, Any]:
    new_agents_supervisor = NEW_HOME / "agents" / "supervisor.md"
    old_agents_supervisor = OLD_HOME / "agents" / "supervisor.md"
    return {
        "canonicalProfile": _supervisor_profile(_read_json(CANONICAL_CONFIG)),
        "legacyProfile": _supervisor_profile(_read_json(LEGACY_CONFIG)),
        "canonicalAgentSupervisorExists": new_agents_supervisor.exists(),
        "legacyAgentSupervisorExists": old_agents_supervisor.exists(),
        "legacySettingsKeys": _legacy_settings_keys(),
    }


def _list_home_entries(home: Path) -> dict[str, list[str]]:
    files: list[str] = []
    dirs: list[str] = []
    if not home.exists():
        return {"files": files, "dirs": dirs}
    for child in sorted(home.iterdir(), key=lambda item: item.name.lower()):
        if child.is_dir():
            dirs.append(child.name)
        else:
            files.append(child.name)
    return {"files": files, "dirs": dirs}


def _classify_new_home() -> dict[str, Any]:
    inventory = _list_home_entries(NEW_HOME)
    files = inventory["files"]
    dirs = inventory["dirs"]
    root_backup_files = sorted([name for name in files if name.endswith(".bak")])
    state_backup_files = sorted([name for name in files if name.startswith("state_identity_cleanup_backup_") and name.endswith(".db")])
    return {
        "canonicalStructuredDomains": sorted(_read_json(CANONICAL_CONFIG).keys()),
        "activeStandaloneFiles": sorted([name for name in files if name in ACTIVE_STANDALONE_FILES]),
        "activeRuntimeDirectories": sorted([name for name in dirs if name in ACTIVE_RUNTIME_DIRS]),
        "runtimeCachesAndArtifacts": sorted(
            [name for name in files if name in RUNTIME_CACHES_AND_ARTIFACTS]
            + [name for name in dirs if name in RUNTIME_CACHES_AND_ARTIFACTS]
            + root_backup_files
            + state_backup_files
        ),
        "legacyCompatibilityInputs": {
            "legacyAliasFiles": sorted([name for name in files if name in LEGACY_ALIAS_FILES]),
            "legacyHomePresent": OLD_HOME.exists(),
            "legacyHomePath": str(OLD_HOME),
        },
        "obsoleteResidue": sorted([name for name in files if name in OBSOLETE_ROOT_FILES]),
    }


def _build_blockers() -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    diff = _top_level_domain_diff()
    if diff["oldOnly"]:
        blockers.append(
            {
                "kind": "old_home_only_domains",
                "domains": diff["oldOnly"],
                "message": "旧 home 里存在新 home 没有的结构化域，不能直接归档。",
            }
        )

    prompt_summary = _prompt_diff_summary()
    if prompt_summary["legacyExists"] and not prompt_summary["canonicalExists"]:
        blockers.append(
            {
                "kind": "missing_canonical_prompt",
                "message": "旧 home 仍有 V8CHAT.md，但 canonical V8_AGENT_OS.md 缺失。",
            }
        )

    supervisor_shadow = _supervisor_shadow_sources()
    canonical_profile = supervisor_shadow["canonicalProfile"]
    legacy_profile = supervisor_shadow["legacyProfile"]
    missing_profile_fields = [
        key
        for key in ("name", "roleLabel", "avatar")
        if legacy_profile.get(key) and not canonical_profile.get(key)
    ]
    if missing_profile_fields:
        blockers.append(
            {
                "kind": "legacy_supervisor_profile_only",
                "fields": missing_profile_fields,
                "message": "旧 home 里存在 canonical profile 尚未承接的 supervisor 信息。",
            }
        )

    if (OLD_HOME / "users.json").exists() and not (NEW_HOME / "users.json").exists():
        blockers.append(
            {
                "kind": "legacy_users_only",
                "message": "旧 home 里存在 users.json，但 canonical home 缺少对应用户数据文件。",
            }
        )

    return blockers


def _move(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    target = dst
    if target.exists():
        target = target.with_name(f"{target.stem}_{datetime.now().strftime('%H%M%S')}{target.suffix}")
    shutil.move(str(src), str(target))
    return str(target)


def _apply_retirement() -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_root = LEGACY_CONFIG_BACKUP_ROOT / f"runtime_compat_retire_{timestamp}"
    archive_root.mkdir(parents=True, exist_ok=True)
    BACKUPS_JSON_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_STATE_DIR.mkdir(parents=True, exist_ok=True)

    actions: dict[str, list[str]] = {
        "archivedLegacyHome": [],
        "archivedLegacyResidue": [],
        "relocatedJsonBackups": [],
        "relocatedStateBackups": [],
    }

    for name in sorted(OBSOLETE_ROOT_FILES | LEGACY_ALIAS_FILES):
        candidate = NEW_HOME / name
        if not candidate.exists():
            continue
        actions["archivedLegacyResidue"].append(
            _move(candidate, archive_root / "new_home_root_compat" / name)
        )

    for candidate in sorted(NEW_HOME.glob("*.bak")):
        actions["relocatedJsonBackups"].append(
            _move(candidate, BACKUPS_JSON_DIR / candidate.name)
        )

    for candidate in sorted(NEW_HOME.glob("state_identity_cleanup_backup_*.db")):
        actions["relocatedStateBackups"].append(
            _move(candidate, BACKUPS_STATE_DIR / candidate.name)
        )

    if OLD_HOME.exists():
        actions["archivedLegacyHome"].append(
            _move(OLD_HOME, archive_root / "home_v8chat")
        )

    report_path = archive_root / "retire-report.json"
    return {
        "backupRoot": str(archive_root),
        "reportPath": str(report_path),
        "actions": actions,
    }


def build_report(*, apply: bool) -> dict[str, Any]:
    report = {
        "selectedCanonicalConfigPath": str(CANONICAL_CONFIG) if CANONICAL_CONFIG.exists() else None,
        "canonicalStructuredDomains": _classify_new_home()["canonicalStructuredDomains"],
        "activeStandaloneFiles": _classify_new_home()["activeStandaloneFiles"],
        "runtimeCachesAndArtifacts": _classify_new_home()["runtimeCachesAndArtifacts"],
        "legacyCompatibilityInputs": _classify_new_home()["legacyCompatibilityInputs"],
        "obsoleteResidue": _classify_new_home()["obsoleteResidue"],
        "legacyHomeDiffSummary": {
            "topLevelDomains": _top_level_domain_diff(),
            "promptFiles": _prompt_diff_summary(),
        },
        "supervisorShadowSources": _supervisor_shadow_sources(),
        "envLegacyPortHits": _legacy_env_hits(),
        "newHomeInventory": _list_home_entries(NEW_HOME),
        "oldHomeInventory": _list_home_entries(OLD_HOME),
    }
    blockers = _build_blockers()
    report["actionsPlanned"] = {
        "archiveLegacyHome": OLD_HOME.exists(),
        "archiveLegacyRootFiles": sorted(_classify_new_home()["obsoleteResidue"] + _classify_new_home()["legacyCompatibilityInputs"]["legacyAliasFiles"]),
        "relocateRootJsonBackups": sorted([path.name for path in NEW_HOME.glob("*.bak")]),
        "relocateStateBackups": sorted([path.name for path in NEW_HOME.glob("state_identity_cleanup_backup_*.db")]),
    }
    report["applyRequested"] = apply
    report["applyBlocked"] = bool(apply and blockers)
    report["blockers"] = blockers
    return report


def emit(payload: dict[str, Any]) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and retire legacy runtime compatibility residue under ~/.v8-agent-os and ~/.v8chat.")
    parser.add_argument("--apply", action="store_true", help="Move legacy residue into archive/backup paths after passing safety checks.")
    args = parser.parse_args()

    report = build_report(apply=args.apply)
    if args.apply and report["blockers"]:
        emit(report)
        return 2

    if args.apply:
        apply_result = _apply_retirement()
        report.update(apply_result)
        report_path = Path(apply_result["reportPath"])
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    emit(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
