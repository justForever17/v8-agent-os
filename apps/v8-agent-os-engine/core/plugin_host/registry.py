from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.storage import storage
from core.v8_agent_os_paths import PLUGIN_INSTALL_LOG_ROOT

from .capability import build_capability_summary, build_capability_surface, channel_labels, classify_plugin_type, plugin_display_name
from .health import evaluate_plugin_health
from .manifest import build_manifest_summary, iter_plugin_dir_candidates, read_json_file, read_package_manifest, read_plugin_manifest
from .models import install_path_for
from .setup import build_setup_surface


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_within_root(candidate: Path, root: Path) -> bool:
    try:
        normalized_candidate = candidate.expanduser().resolve()
        normalized_root = root.expanduser().resolve()
    except Exception:
        normalized_candidate = candidate.expanduser()
        normalized_root = root.expanduser()
    return normalized_candidate == normalized_root or normalized_root in normalized_candidate.parents


def _read_managed_local_openclaw_config(plugin_root: Path) -> dict[str, Any]:
    config_path = plugin_root / "openclaw.json"
    if not config_path.exists():
        return {}
    try:
        payload = read_json_file(config_path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_configured_plugin_candidates(plugin_root: Path) -> list[Path]:
    payload = _read_managed_local_openclaw_config(plugin_root)
    plugins_payload = dict(payload.get("plugins") or {})
    installs_payload = dict(plugins_payload.get("installs") or {})
    load_paths = list(dict(plugins_payload.get("load") or {}).get("paths") or [])

    candidates: list[Path] = []
    seen: set[str] = set()

    def _append_candidate(raw_path: str | Path | None) -> None:
        path_text = str(raw_path or "").strip()
        if not path_text:
            return
        candidate = Path(path_text).expanduser()
        if not candidate.is_absolute():
            candidate = (plugin_root / candidate).expanduser()
        try:
            normalized = str(candidate.resolve())
        except Exception:
            normalized = str(candidate)
        if normalized in seen:
            return
        if not candidate.exists():
            return
        seen.add(normalized)
        candidates.append(candidate)

    for install in installs_payload.values():
        if not isinstance(install, dict):
            continue
        _append_candidate(install.get("installPath") or install.get("sourcePath"))

    for load_path in load_paths:
        _append_candidate(load_path)

    return candidates


def _iter_plugin_dirs_from_root(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_dir() and ((root / "openclaw.plugin.json").exists() or (root / "package.json").exists()):
        package_manifest = read_package_manifest(root)
        manifest = read_plugin_manifest(root)
        if manifest or isinstance(package_manifest.get("openclaw"), dict):
            return [root]
    return iter_plugin_dir_candidates(root)


def _build_plugin_record(*, plugin_dir: Path, previous: dict[str, Any] | None = None) -> dict[str, Any] | None:
    manifest = read_plugin_manifest(plugin_dir)
    package_manifest = read_package_manifest(plugin_dir)
    plugin_id = str(manifest.get("id") or package_manifest.get("name") or plugin_dir.name).strip()
    if not plugin_id:
        return None
    plugin_type = classify_plugin_type(manifest=manifest, package_manifest=package_manifest)
    openclaw_meta = package_manifest.get("openclaw") if isinstance(package_manifest, dict) else {}
    install_meta = openclaw_meta.get("install") if isinstance(openclaw_meta, dict) else {}
    compatible_host_version = str((openclaw_meta or {}).get("hostVersionRange") or "").strip() or None
    channels = channel_labels(manifest=manifest, package_manifest=package_manifest)
    capability_surface = build_capability_surface(
        plugin_id=plugin_id,
        manifest=manifest,
        package_manifest=package_manifest,
        channels=channels,
    )
    setup_state = str((previous or {}).get("setupState") or "installed").strip().lower()
    if setup_state not in {"installed", "onboarded", "needs_user_action", "failed", "active"}:
        setup_state = "installed"
    setup_surface = build_setup_surface(
        plugin_id=plugin_id,
        manifest=manifest,
        package_manifest=package_manifest,
        setup_state=setup_state,
    )

    activation_state = str((previous or {}).get("activationState") or "active").strip().lower()
    if activation_state not in {"active", "disabled"}:
        activation_state = "active"
    lifecycle_state = str((previous or {}).get("lifecycleState") or "installed").strip().lower()
    if lifecycle_state not in {"discovered", "installed", "onboarded", "active", "degraded", "incompatible", "disabled"}:
        lifecycle_state = "installed"
    if activation_state == "disabled":
        lifecycle_state = "disabled"
    elif lifecycle_state == "disabled":
        lifecycle_state = "installed"
    health_state = str((previous or {}).get("healthState") or ("healthy" if activation_state == "active" else "disabled")).strip().lower()
    record = {
        "pluginId": plugin_id,
        "displayName": plugin_display_name(plugin_id=plugin_id, manifest=manifest, package_manifest=package_manifest),
        "pluginType": plugin_type,
        "source": "openclaw-plugin-root",
        "installSpec": str((previous or {}).get("installSpec") or (install_meta or {}).get("npmSpec") or package_manifest.get("name") or "").strip(),
        "installPath": str(plugin_dir.resolve()),
        "manifestSummary": build_manifest_summary(plugin_id=plugin_id, manifest=manifest, package_manifest=package_manifest, channels=channels),
        "capabilities": build_capability_summary(capability_surface),
        "capabilitySurface": capability_surface,
        "setupState": setup_state,
        "setupSurface": setup_surface,
        "activationState": activation_state,
        "healthState": health_state,
        "compatibleHostVersion": compatible_host_version,
        "lifecycleState": lifecycle_state,
        "warnings": list((previous or {}).get("warnings") or []),
        "lastScanAt": _now_iso(),
    }
    record.update(evaluate_plugin_health(record))
    return record


def default_plugin_registry() -> dict[str, Any]:
    return storage.get_plugin_registry()


def save_plugin_registry(payload: dict[str, Any]) -> dict[str, Any]:
    storage.save_plugin_registry(payload)
    return storage.get_plugin_registry()


def scan_plugin_registry() -> dict[str, Any]:
    current = storage.get_plugin_registry()
    existing_plugins = dict(current.get("plugins") or {})
    plugin_host_config = storage.get_plugin_host_config()
    managed_local = dict(plugin_host_config.get("managedLocal") or {})
    plugin_root = Path(str(managed_local.get("rootDir") or current.get("pluginRoot") or ""))
    extensions_root = plugin_root / "extensions"
    plugin_root.mkdir(parents=True, exist_ok=True)
    extensions_root.mkdir(parents=True, exist_ok=True)
    PLUGIN_INSTALL_LOG_ROOT.mkdir(parents=True, exist_ok=True)

    next_plugins: dict[str, Any] = {}
    candidate_roots = [extensions_root, *_iter_configured_plugin_candidates(plugin_root)]
    seen_plugin_dirs: set[str] = set()
    for candidate_root in candidate_roots:
        for plugin_dir in _iter_plugin_dirs_from_root(candidate_root):
            try:
                normalized_plugin_dir = str(plugin_dir.resolve())
            except Exception:
                normalized_plugin_dir = str(plugin_dir)
            if normalized_plugin_dir in seen_plugin_dirs:
                continue
            seen_plugin_dirs.add(normalized_plugin_dir)
            candidate_manifest = read_plugin_manifest(plugin_dir)
            candidate_package = read_package_manifest(plugin_dir)
            candidate_id = str(candidate_manifest.get("id") or candidate_package.get("name") or plugin_dir.name).strip()
            previous = existing_plugins.get(candidate_id) if isinstance(existing_plugins.get(candidate_id), dict) else None
            record = _build_plugin_record(plugin_dir=plugin_dir, previous=previous)
            if record:
                next_plugins[record["pluginId"]] = record

    for plugin_id, previous in existing_plugins.items():
        if plugin_id in next_plugins or not isinstance(previous, dict):
            continue
        previous_install_path = Path(str(previous.get("installPath") or "")).expanduser()
        if not str(previous_install_path):
            continue
        if not (
            _path_within_root(previous_install_path, extensions_root)
            or previous_install_path in _iter_configured_plugin_candidates(plugin_root)
        ):
            continue
        removed = dict(previous)
        removed["lastScanAt"] = _now_iso()
        removed.update(evaluate_plugin_health(removed))
        next_plugins[plugin_id] = removed

    payload = {
        **current,
        "pluginRoot": str(plugin_root),
        "pluginExtensionsRoot": str(extensions_root),
        "pluginInstallLogRoot": str(PLUGIN_INSTALL_LOG_ROOT),
        "plugins": next_plugins,
    }
    storage.save_plugin_registry(payload)
    return storage.get_plugin_registry()


def update_plugin_record(plugin_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    payload = storage.get_plugin_registry()
    plugins = dict(payload.get("plugins") or {})
    current = dict(plugins.get(plugin_id) or {})
    current.update(patch or {})
    current["pluginId"] = plugin_id
    current["lastScanAt"] = _now_iso()
    plugins[plugin_id] = current
    payload["plugins"] = plugins
    storage.save_plugin_registry(payload)
    return storage.get_plugin_registry()


def upsert_install_job(job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    payload = storage.get_plugin_registry()
    jobs = dict(payload.get("installJobs") or {})
    current = dict(jobs.get(job_id) or {})
    current.update(patch or {})
    current["jobId"] = job_id
    jobs[job_id] = current
    payload["installJobs"] = jobs
    storage.save_plugin_registry(payload)
    return current


def remove_install_job(job_id: str) -> None:
    payload = storage.get_plugin_registry()
    jobs = dict(payload.get("installJobs") or {})
    jobs.pop(job_id, None)
    payload["installJobs"] = jobs
    storage.save_plugin_registry(payload)


def stable_install_path(plugin_id: str) -> Path:
    plugin_host_config = storage.get_plugin_host_config()
    managed_local = dict(plugin_host_config.get("managedLocal") or {})
    plugin_root = Path(str(managed_local.get("rootDir") or storage.get_plugin_registry().get("pluginRoot") or ""))
    return install_path_for(plugin_root, plugin_id)
