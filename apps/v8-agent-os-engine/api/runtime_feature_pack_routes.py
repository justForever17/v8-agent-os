from __future__ import annotations

import hmac
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.runtime.feature_packs import (
    FEATURE_PACK_BY_ID,
    FEATURE_PACK_LOG_ROOT,
    FEATURE_PACK_PYTHON_ROOT,
    _feature_pack_receipt_runtime_compatibility,
    build_feature_pack_statuses,
)
from core.runtime.startup_profile import normalize_install_platform
from core.storage import storage
from core.system_base import get_internal_secret
from core.time_truth import utc_now_iso


router = APIRouter(prefix="/runtime-feature-packs", tags=["runtime-feature-packs"])


class FeaturePackStatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["installed", "not_installed", "installing", "failed"] | None = None
    targetDir: str | None = Field(default=None, max_length=4096)
    logRef: str | None = Field(default=None, max_length=4096)
    lastError: str | None = Field(default=None, max_length=8192)
    restartRequired: bool | None = None
    version: str | None = Field(default=None, max_length=256)
    assetRoot: str | None = Field(default=None, max_length=4096)
    receiptRef: str | None = Field(default=None, max_length=4096)
    operationId: str | None = Field(default=None, max_length=256)
    startedAt: str | None = Field(default=None, max_length=128)


class FeaturePackStatePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: FeaturePackStatePatch
    expectedOperationId: str | None


class _OperationConflict(RuntimeError):
    pass


class _InvalidInstalledState(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def require_feature_pack_internal_secret(
    x_v8_agent_os_secret: str | None = Header(default=None),
) -> None:
    expected_secret = get_internal_secret()
    if not expected_secret or not hmac.compare_digest(str(x_v8_agent_os_secret or ""), expected_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _normalized_operation_id(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _resolved_path(value: str) -> Path:
    try:
        return Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid feature-pack path") from exc


def _require_exact_path(field: str, value: str, expected: Path) -> None:
    if _resolved_path(value) != expected.expanduser().resolve(strict=False):
        raise HTTPException(status_code=422, detail=f"{field} is outside the canonical feature-pack path")


def _canonical_operation_uuid(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    try:
        parsed = str(UUID(normalized))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be a UUID") from exc
    if parsed != normalized:
        raise HTTPException(status_code=422, detail=f"{field} must be a canonical UUID")
    return parsed


def _validate_patch_paths(
    pack_id: str,
    patch: dict[str, Any],
    expected_operation_id: str | None,
) -> None:
    path_fields = {field for field in {"targetDir", "assetRoot", "receiptRef"} if patch.get(field) is not None}
    patch_operation_id = _normalized_operation_id(patch.get("operationId"))
    operation_id = patch_operation_id or expected_operation_id
    if path_fields and not operation_id:
        raise HTTPException(status_code=422, detail="Versioned feature-pack paths require an operation UUID")
    if path_fields:
        operation_id = _canonical_operation_uuid(operation_id, "operationId")
    version_root = (
        FEATURE_PACK_PYTHON_ROOT / pack_id / "versions" / str(operation_id or "")
    ).expanduser().resolve(strict=False)
    exact_paths = {
        "targetDir": version_root / "python",
        "assetRoot": version_root / "models",
        "receiptRef": version_root / "receipt.json",
    }
    for field, expected in exact_paths.items():
        value = patch.get(field)
        if value is not None and field in patch:
            _require_exact_path(field, str(value), expected)

    log_ref = patch.get("logRef")
    if log_ref is not None and "logRef" in patch:
        resolved_log = _resolved_path(str(log_ref))
        resolved_root = FEATURE_PACK_LOG_ROOT.expanduser().resolve(strict=False)
        try:
            relative = resolved_log.relative_to(resolved_root)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="logRef is outside the canonical feature-pack log path") from exc
        if not relative.parts:
            raise HTTPException(status_code=422, detail="logRef must identify a file")


def _normalized_patch(request: FeaturePackStatePatchRequest) -> dict[str, Any]:
    patch = request.patch.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="Feature-pack state patch is empty")
    if "status" in patch and patch["status"] is None:
        raise HTTPException(status_code=422, detail="status cannot be null")
    for field in {
        "targetDir",
        "logRef",
        "lastError",
        "version",
        "assetRoot",
        "receiptRef",
        "operationId",
        "startedAt",
    }:
        if field in patch and patch[field] is not None:
            patch[field] = str(patch[field]).strip() or None
    return patch


def patch_feature_pack_state(pack_id: str, request: FeaturePackStatePatchRequest) -> dict[str, Any]:
    normalized_pack_id = str(pack_id or "").strip()
    if normalized_pack_id not in FEATURE_PACK_BY_ID:
        raise HTTPException(status_code=404, detail="Unknown feature pack")

    patch = _normalized_patch(request)
    expected_operation_id = _normalized_operation_id(request.expectedOperationId)
    if request.expectedOperationId is not None and expected_operation_id is None:
        raise HTTPException(status_code=422, detail="expectedOperationId cannot be empty")
    if expected_operation_id is not None:
        expected_operation_id = _canonical_operation_uuid(expected_operation_id, "expectedOperationId")
    patch_operation_id = _normalized_operation_id(patch.get("operationId"))
    if patch_operation_id is not None:
        patch["operationId"] = _canonical_operation_uuid(patch_operation_id, "operationId")
    _validate_patch_paths(normalized_pack_id, patch, expected_operation_id)

    def mutate(current: Any) -> dict[str, Any]:
        registry = deepcopy(current) if isinstance(current, dict) else {}
        feature_packs = deepcopy(registry.get("featurePacks")) if isinstance(registry.get("featurePacks"), dict) else {}
        current_state = (
            deepcopy(feature_packs.get(normalized_pack_id))
            if isinstance(feature_packs.get(normalized_pack_id), dict)
            else {}
        )
        current_operation_id = _normalized_operation_id(current_state.get("operationId"))
        if current_operation_id != expected_operation_id:
            raise _OperationConflict("Feature-pack operation changed before this state update")

        next_state = {**current_state, **patch, "updatedAt": utc_now_iso()}
        feature_packs[normalized_pack_id] = next_state
        registry["featurePacks"] = feature_packs
        if str(next_state.get("status") or "") == "installed":
            compatible, reason, message, _ = _feature_pack_receipt_runtime_compatibility(
                normalized_pack_id,
                registry,
            )
            if not compatible:
                raise _InvalidInstalledState(
                    str(reason or "receipt_invalid"),
                    str(message or "Feature-pack receipt is incompatible"),
                )
        return registry

    try:
        registry = storage.mutate_config_domain("runtimeRegistry", mutate)
    except _OperationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except _InvalidInstalledState as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.reason, "message": str(exc)},
        ) from exc

    state = dict((registry.get("featurePacks") or {}).get(normalized_pack_id) or {})
    return {"ok": True, "packId": normalized_pack_id, "state": state}


def get_feature_pack_status_snapshot() -> dict[str, Any]:
    # Capture the authority timestamp before reading the registry. A slow
    # projection must not look newer than a config commit that completed
    # while the registry was being read.
    sampled_at = utc_now_iso()
    raw_registry = storage.get_runtime_registry_config()
    registry = raw_registry if isinstance(raw_registry, dict) else {}
    install_platform = normalize_install_platform(
        os.getenv("ENGINE_INSTALL_PLATFORM") or registry.get("installPlatform")
    )
    feature_packs = build_feature_pack_statuses(
        registry,
        install_platform=install_platform,
    )
    return {
        "sampledAt": sampled_at,
        "installPlatform": install_platform,
        "featurePacks": feature_packs,
    }


@router.get("/status")
async def get_feature_pack_status_route(
    _auth: None = Depends(require_feature_pack_internal_secret),
):
    return get_feature_pack_status_snapshot()


@router.patch("/{pack_id}/state")
async def patch_feature_pack_state_route(
    pack_id: str,
    request: FeaturePackStatePatchRequest,
    _auth: None = Depends(require_feature_pack_internal_secret),
):
    return patch_feature_pack_state(pack_id, request)
