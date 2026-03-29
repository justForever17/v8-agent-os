from __future__ import annotations

from typing import Any

from .profiles import renderable_profile_fields


def classify_plugin_type(*, manifest: dict[str, Any], package_manifest: dict[str, Any]) -> str:
    channels = manifest.get("channels")
    openclaw_meta = package_manifest.get("openclaw") if isinstance(package_manifest, dict) else {}
    if isinstance(channels, list) and channels:
        return "channel"
    if isinstance(openclaw_meta, dict) and isinstance(openclaw_meta.get("channel"), dict):
        return "channel"
    return "plugin"


def channel_labels(*, manifest: dict[str, Any], package_manifest: dict[str, Any]) -> list[str]:
    channels = manifest.get("channels")
    if isinstance(channels, list):
        return [str(item).strip() for item in channels if str(item).strip()]
    openclaw_meta = package_manifest.get("openclaw") if isinstance(package_manifest, dict) else {}
    channel_meta = openclaw_meta.get("channel") if isinstance(openclaw_meta, dict) else {}
    channel_id = str((channel_meta or {}).get("id") or "").strip()
    return [channel_id] if channel_id else []


def plugin_display_name(*, plugin_id: str, manifest: dict[str, Any], package_manifest: dict[str, Any]) -> str:
    openclaw_meta = package_manifest.get("openclaw") if isinstance(package_manifest, dict) else {}
    channel_meta = openclaw_meta.get("channel") if isinstance(openclaw_meta, dict) else {}
    for candidate in (
        (channel_meta or {}).get("label"),
        manifest.get("name"),
        package_manifest.get("description"),
        package_manifest.get("name"),
        plugin_id,
    ):
        label = str(candidate or "").strip()
        if label:
            return label[:72]
    return plugin_id


def extract_config_fields(*, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    config_schema = manifest.get("configSchema") if isinstance(manifest, dict) else {}
    ui_hints = manifest.get("uiHints") if isinstance(manifest, dict) else {}
    properties = config_schema.get("properties") if isinstance(config_schema, dict) else {}
    required = set(config_schema.get("required") or []) if isinstance(config_schema, dict) else set()
    fields: list[dict[str, Any]] = []
    if not isinstance(properties, dict):
        return fields

    for key, schema in properties.items():
        schema_dict = schema if isinstance(schema, dict) else {}
        hint = ui_hints.get(key) if isinstance(ui_hints, dict) else {}
        fields.append(
            {
                "key": str(key),
                "type": str(schema_dict.get("type") or "string"),
                "required": key in required,
                "label": str((hint or {}).get("label") or key),
                "help": str((hint or {}).get("help") or "").strip() or None,
                "enum": [str(item) for item in list(schema_dict.get("enum") or []) if str(item).strip()] or None,
            }
        )
    return fields


def resolve_renderable_config_fields(
    *,
    plugin_id: str,
    manifest: dict[str, Any],
    package_manifest: dict[str, Any],
) -> dict[str, Any]:
    manifest_fields = extract_config_fields(manifest=manifest)
    if manifest_fields:
        return {"renderMode": "config_schema", "renderableFields": manifest_fields}
    return renderable_profile_fields(plugin_id=plugin_id, package_manifest=package_manifest)


def build_capability_surface(
    *,
    plugin_id: str,
    manifest: dict[str, Any],
    package_manifest: dict[str, Any],
    channels: list[str],
) -> dict[str, Any]:
    openclaw_meta = package_manifest.get("openclaw") if isinstance(package_manifest, dict) else {}
    channel_meta = openclaw_meta.get("channel") if isinstance(openclaw_meta, dict) else {}
    install_meta = openclaw_meta.get("install") if isinstance(openclaw_meta, dict) else {}
    extensions = list((openclaw_meta or {}).get("extensions") or []) if isinstance(openclaw_meta, dict) else []
    render_surface = resolve_renderable_config_fields(
        plugin_id=plugin_id,
        manifest=manifest,
        package_manifest=package_manifest,
    )
    config_fields = list(render_surface.get("renderableFields") or [])
    return {
        "channels": channels,
        "docsPath": str((channel_meta or {}).get("docsPath") or "").strip() or None,
        "docsLabel": str((channel_meta or {}).get("docsLabel") or "").strip() or None,
        "aliases": [str(item).strip() for item in list((channel_meta or {}).get("aliases") or []) if str(item).strip()],
        "preferOver": [str(item).strip() for item in list((channel_meta or {}).get("preferOver") or []) if str(item).strip()],
        "selectionLabel": str((channel_meta or {}).get("selectionLabel") or "").strip() or None,
        "detailLabel": str((channel_meta or {}).get("detailLabel") or "").strip() or None,
        "blurb": str((channel_meta or {}).get("blurb") or "").strip() or None,
        "supportsSetupWizard": bool((openclaw_meta or {}).get("setupEntry")),
        "supportsInstallSpec": bool((install_meta or {}).get("npmSpec") or package_manifest.get("name")),
        "extensions": [str(item).strip() for item in extensions if str(item).strip()],
        "configFields": config_fields,
        "renderMode": str(render_surface.get("renderMode") or "config_schema"),
        "renderableFields": config_fields,
        "configFieldCount": len(config_fields),
    }


def build_capability_summary(capability_surface: dict[str, Any]) -> dict[str, Any]:
    return {
        "channels": list(capability_surface.get("channels") or []),
        "docsPath": capability_surface.get("docsPath"),
        "supportsSetupWizard": bool(capability_surface.get("supportsSetupWizard")),
        "supportsInstallSpec": bool(capability_surface.get("supportsInstallSpec")),
        "configFieldCount": int(capability_surface.get("configFieldCount") or 0),
    }
