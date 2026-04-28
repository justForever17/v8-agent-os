from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict


POLICY_PATH = Path(__file__).resolve().parent / "artifact_surface_policy.json"


@lru_cache(maxsize=1)
def load_artifact_surface_policy() -> Dict[str, Any]:
    try:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload.setdefault("version", 1)
            payload.setdefault("defaults", {})
            payload.setdefault("rules", [])
            return payload
    except Exception:
        pass
    return {
        "version": 1,
        "defaults": {
            "autoAttachToMessage": False,
            "surfaceVisible": False,
            "supportsInlinePreview": False,
            "previewKind": "file",
        },
        "rules": [],
    }


def _lower_set(values: Any) -> set[str]:
    return {str(item).strip().lower() for item in list(values or []) if str(item).strip()}


def _artifact_extension(descriptor: Dict[str, Any]) -> str:
    for key in ("sourcePath", "workspacePath", "title"):
        value = str(descriptor.get(key) or "").strip()
        if value:
            suffix = Path(value).suffix.lower()
            if suffix:
                return suffix
    return ""


def _artifact_size(descriptor: Dict[str, Any]) -> int | None:
    source_path = str(descriptor.get("sourcePath") or "").strip()
    if not source_path:
        return None
    try:
        path = Path(source_path)
        if path.exists():
            return int(path.stat().st_size)
    except Exception:
        return None
    return None


def _infer_origin(descriptor: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    explicit = str(
        descriptor.get("origin")
        or descriptor.get("artifactOrigin")
        or metadata.get("origin")
        or metadata.get("artifactOrigin")
        or ""
    ).strip()
    if explicit:
        return explicit
    if (
        str(metadata.get("runtime") or "").strip() == "computer_use"
        and metadata.get("capture") is not None
        and str(metadata.get("pathPlane") or "").strip() == "workspace_artifact"
    ):
        return "computer_use_screenshot"
    return ""


def _matches_rule(rule: Dict[str, Any], *, descriptor: Dict[str, Any], metadata: Dict[str, Any], origin: str) -> bool:
    kind = str(descriptor.get("kind") or "").strip().lower()
    mime_type = str(descriptor.get("mimeType") or descriptor.get("mime_type") or "").strip().lower()
    path_plane = str(metadata.get("pathPlane") or metadata.get("path_plane") or "").strip()
    extension = _artifact_extension(descriptor)

    expected_origin = str(rule.get("origin") or "").strip()
    if expected_origin and expected_origin != origin:
        return False
    expected_kind = str(rule.get("kind") or "").strip().lower()
    if expected_kind and expected_kind != kind:
        return False
    expected_plane = str(rule.get("pathPlane") or "").strip()
    if expected_plane and expected_plane != path_plane:
        return False
    mime_types = _lower_set(rule.get("mimeTypes"))
    if mime_types and mime_type not in mime_types:
        return False
    extensions = _lower_set(rule.get("extensions"))
    if extensions and extension not in extensions:
        return False
    return True


def _auto_attached_count_for_run(*, run_id: str | None, origin: str, rule_id: str) -> int:
    if not run_id:
        return 0
    try:
        from core.database import db

        count = 0
        for artifact in db.list_runtime_artifacts(run_id=run_id, limit=1000):
            metadata = dict(artifact.get("metadata") or {})
            if not bool(metadata.get("autoAttachToMessage") or metadata.get("auto_attach_to_message")):
                continue
            if origin and str(metadata.get("origin") or "").strip() != origin:
                continue
            if rule_id and str(metadata.get("artifactSurfacePolicyRuleId") or "").strip() != rule_id:
                continue
            count += 1
        return count
    except Exception:
        return 0


def apply_artifact_surface_policy(
    descriptor: Dict[str, Any],
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> Dict[str, Any]:
    payload = dict(descriptor or {})
    metadata = dict(payload.get("metadata") or {})
    policy = load_artifact_surface_policy()
    defaults = dict(policy.get("defaults") or {})
    origin = _infer_origin(payload, metadata)
    rule = next(
        (
            dict(item)
            for item in list(policy.get("rules") or [])
            if isinstance(item, dict) and _matches_rule(item, descriptor=payload, metadata=metadata, origin=origin)
        ),
        {},
    )
    effective = {**defaults, **rule}
    rule_id = str(effective.get("id") or "").strip() or None
    size = _artifact_size(payload)
    blocked_reason = ""
    max_bytes = effective.get("maxBytes")
    if size is not None and max_bytes not in (None, ""):
        try:
            if size > int(max_bytes):
                blocked_reason = "max_bytes_exceeded"
        except Exception:
            pass
    max_per_run = effective.get("maxPerRun")
    if not blocked_reason and max_per_run not in (None, ""):
        try:
            if _auto_attached_count_for_run(run_id=run_id, origin=origin, rule_id=rule_id or "") >= int(max_per_run):
                blocked_reason = "max_per_run_exceeded"
        except Exception:
            pass

    auto_attach = bool(effective.get("autoAttachToMessage")) and not blocked_reason
    surface_visible = bool(effective.get("surfaceVisible")) and auto_attach
    supports_inline = bool(effective.get("supportsInlinePreview")) and auto_attach
    preview_kind = str(effective.get("previewKind") or payload.get("previewKind") or "file").strip() or "file"

    if origin:
        metadata["origin"] = origin
        payload["origin"] = origin
        payload["artifactOrigin"] = origin
    metadata["artifactSurfacePolicyVersion"] = policy.get("version")
    metadata["artifactSurfacePolicyRuleId"] = rule_id
    metadata["autoAttachToMessage"] = auto_attach
    metadata["surfaceVisible"] = surface_visible
    metadata["supportsInlinePreview"] = supports_inline
    metadata["previewKind"] = preview_kind
    metadata["ephemeral"] = bool(effective.get("ephemeral"))
    if size is not None:
        metadata["byteSize"] = size
    if blocked_reason:
        metadata["autoAttachBlockedReason"] = blocked_reason

    payload["metadata"] = metadata
    payload["surfaceVisible"] = surface_visible
    payload["autoAttachToMessage"] = auto_attach
    payload["supportsInlinePreview"] = supports_inline
    payload["previewKind"] = preview_kind
    payload["artifactSurfacePolicyRuleId"] = rule_id
    payload["artifactSurfacePolicyVersion"] = policy.get("version")
    if session_id:
        payload.setdefault("sessionId", session_id)
    if run_id:
        payload.setdefault("runId", run_id)
    return payload
