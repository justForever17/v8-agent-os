from __future__ import annotations

import importlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

from core.system_base import get_internal_secret
from core.v8_agent_os_paths import V8_AGENT_OS_HOME


class _LazyCreativeMediaRuntime:
    def __getattr__(self, name: str):
        runtime = importlib.import_module("runtimes.creative_media.runtime").creative_media_runtime
        return getattr(runtime, name)


creative_media_runtime = _LazyCreativeMediaRuntime()


_CREATIVE_MEDIA_ADMIN_GOVERNANCE_SECRET_PATH = (
    V8_AGENT_OS_HOME / "secrets" / "creative-media-admin-governance-secret"
)
_RECONCILER_STATUS_SCHEMA = "v8.creative_media_reconciler_status.v1"
_TERMINAL_PROJECTION_DISPOSITIONS = {
    "owner_deleted",
    "authority_changed",
    "lineage_missing",
    "applied",
}
_SAFE_RECONCILER_CYCLE_KEYS = {
    "schema",
    "status",
    "detailCode",
    "scanned",
    "recoveredOrphans",
    "eligible",
    "checked",
    "resolved",
    "uncertain",
    "hasMore",
    "stopped",
    "completedAt",
}


def _safe_reconciler_label(value: Any, fallback: str = "unknown") -> str:
    """Keep governance labels bounded and free of URLs/provider payloads."""
    raw = str(value or "").strip()
    if not raw:
        return fallback
    if len(raw) > 96 or any(
        not (
            "a" <= char <= "z"
            or "A" <= char <= "Z"
            or "0" <= char <= "9"
            or char in "._:-"
        )
        for char in raw
    ):
        return fallback
    return raw


def _safe_reconciler_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _parse_reconciler_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reconciler_projection_pending(report: dict[str, Any]) -> bool:
    disposition = str(report.get("projectionDisposition") or "").strip().lower()
    if disposition in _TERMINAL_PROJECTION_DISPOSITIONS:
        return False
    return bool(
        report.get("projectionPending")
        or (
            report.get("terminalProof")
            and not report.get("projectedAt")
        )
    )


def _reconciler_quarantined(report: dict[str, Any]) -> bool:
    disposition = str(report.get("projectionDisposition") or "").strip().lower()
    status = str(report.get("status") or "").strip().lower()
    detail_code = str(report.get("detailCode") or "").strip().lower()
    return bool(
        report.get("quarantine")
        or report.get("quarantined")
        or "quarantine" in disposition
        or "quarantine" in status
        or "quarantine" in detail_code
    )


def _safe_reconciler_cycle(value: Any) -> dict[str, Any]:
    cycle = value if isinstance(value, dict) else {}
    safe: dict[str, Any] = {}
    for key in _SAFE_RECONCILER_CYCLE_KEYS:
        if key not in cycle:
            continue
        item = cycle.get(key)
        if key in {"schema", "status", "detailCode"}:
            safe[key] = _safe_reconciler_label(item)
        elif key in {"scanned", "recoveredOrphans", "eligible", "checked", "resolved", "uncertain"}:
            safe[key] = _safe_reconciler_count(item)
        elif key in {"hasMore", "stopped"}:
            safe[key] = bool(item)
        elif key == "completedAt":
            parsed = _parse_reconciler_time(item)
            if parsed is not None:
                safe[key] = parsed.isoformat().replace("+00:00", "Z")
    # Preserve the fact that a cycle failed without returning exception text,
    # provider responses, URLs, or any other raw diagnostic body.
    safe["hasError"] = bool(cycle.get("error") or cycle.get("cycleCallbackError"))
    return safe


def _build_reconciler_governance_status() -> dict[str, Any]:
    """Return a bounded operational projection for Admin governance only."""
    try:
        raw_status = creative_media_runtime.remote_reconciler_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Creative Media reconciler status unavailable") from exc
    status = raw_status if isinstance(raw_status, dict) else {}
    running = bool(status.get("running"))
    last_cycle = _safe_reconciler_cycle(status.get("lastCycle"))

    reports: list[dict[str, Any]] = []
    list_reports = getattr(creative_media_runtime, "list_remote_reconcile_reports", None)
    if callable(list_reports):
        try:
            candidate_reports = list_reports()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Creative Media reconciler reports unavailable") from exc
        reports = [item for item in candidate_reports if isinstance(item, dict)]

    uncertain = sum(bool(item.get("remoteTaskMayContinue")) for item in reports)
    projection_pending = sum(_reconciler_projection_pending(item) for item in reports)
    quarantine_count = sum(_reconciler_quarantined(item) for item in reports)
    adapter_distribution: dict[str, int] = {}
    detail_distribution: dict[str, int] = {}
    oldest: datetime | None = None
    for report in reports:
        adapter = _safe_reconciler_label(report.get("adapter"))
        detail_code = _safe_reconciler_label(report.get("detailCode"))
        adapter_distribution[adapter] = adapter_distribution.get(adapter, 0) + 1
        detail_distribution[detail_code] = detail_distribution.get(detail_code, 0) + 1
        if not (bool(report.get("remoteTaskMayContinue")) or _reconciler_projection_pending(report)):
            continue
        for field in ("createdAt", "reconciledAt", "observedAt", "nextReconcileAt"):
            candidate = _parse_reconciler_time(report.get(field))
            if candidate is not None and (oldest is None or candidate < oldest):
                oldest = candidate
            if candidate is not None:
                break

    # Newer runtimes may already expose these safe aggregates. Prefer the
    # report-derived values when reports are available, and use the aggregate
    # fallback for older/fake runtimes that only expose status().
    if not reports:
        uncertain = _safe_reconciler_count(status.get("uncertain"))
        projection_pending = _safe_reconciler_count(status.get("projectionPending"))
        quarantine_count = _safe_reconciler_count(status.get("quarantineCount"))
        raw_oldest = status.get("oldest")
        if isinstance(raw_oldest, dict):
            oldest = _parse_reconciler_time(raw_oldest.get("at") or raw_oldest.get("oldestAt"))
        raw_adapters = status.get("adapterDistribution")
        raw_details = status.get("detailCodeDistribution")
        if isinstance(raw_adapters, dict):
            adapter_distribution = {
                _safe_reconciler_label(key): _safe_reconciler_count(value)
                for key, value in raw_adapters.items()
            }
        if isinstance(raw_details, dict):
            detail_distribution = {
                _safe_reconciler_label(key): _safe_reconciler_count(value)
                for key, value in raw_details.items()
            }

    now = datetime.now(timezone.utc)
    oldest_payload: dict[str, Any] = {"at": None, "ageSeconds": None}
    if oldest is not None:
        oldest_payload = {
            "at": oldest.isoformat().replace("+00:00", "Z"),
            "ageSeconds": max(0, int((now - oldest).total_seconds())),
        }
        raw_oldest = status.get("oldest")
        if not reports and isinstance(raw_oldest, dict) and raw_oldest.get("ageSeconds") is not None:
            oldest_payload["ageSeconds"] = _safe_reconciler_count(raw_oldest.get("ageSeconds"))
    elif not reports and isinstance(status.get("oldest"), dict):
        raw_oldest = status["oldest"]
        if raw_oldest.get("ageSeconds") is not None:
            oldest_payload = {
                "at": str(raw_oldest.get("at") or raw_oldest.get("oldestAt") or "") or None,
                "ageSeconds": _safe_reconciler_count(raw_oldest.get("ageSeconds")),
            }
    cycle_status = str(last_cycle.get("status") or "").lower()
    worker_state = "running" if running else ("failed" if cycle_status == "failed" else "idle")
    last_cycle_at = last_cycle.get("completedAt")
    return {
        "schema": _RECONCILER_STATUS_SCHEMA,
        "worker": {
            "state": worker_state,
            "running": running,
            "lastCycleAt": last_cycle_at,
            "lastCycle": last_cycle,
        },
        "uncertain": uncertain,
        "projectionPending": projection_pending,
        "oldest": oldest_payload,
        "adapterDistribution": dict(sorted(adapter_distribution.items())),
        "detailCodeDistribution": dict(sorted(detail_distribution.items())),
        "quarantineCount": quarantine_count,
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
    }


def require_creative_media_internal_secret(
    x_v8_agent_os_secret: str | None = Header(default=None),
) -> None:
    expected_secret = str(get_internal_secret() or "").strip()
    if not expected_secret or not hmac.compare_digest(
        str(x_v8_agent_os_secret or ""),
        expected_secret,
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_creative_media_admin_governance_secret() -> str:
    """Read the Admin-owned local capability without creating or repairing it."""

    try:
        secret = _CREATIVE_MEDIA_ADMIN_GOVERNANCE_SECRET_PATH.read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return ""
    return secret if len(secret) >= 32 else ""


@dataclass(frozen=True)
class CreativeMediaOwnerScope:
    session_id: str
    workspace_id: str
    project_id: str = ""
    workspace_path: str = ""


def require_creative_media_owner_scope(request: Request) -> CreativeMediaOwnerScope:
    session_id = str(request.headers.get("x-v8-session-id") or "").strip()
    workspace_id = str(request.headers.get("x-v8-workspace-id") or "").strip()
    if not session_id or not workspace_id:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_required")
    return CreativeMediaOwnerScope(
        session_id=session_id,
        workspace_id=workspace_id,
        project_id=str(request.headers.get("x-v8-project-id") or "").strip(),
        workspace_path=str(request.headers.get("x-v8-workspace-path") or "").strip(),
    )


def require_creative_media_admin_governance(
    x_v8_agent_os_admin_governance_secret: str | None = Header(default=None),
) -> None:
    expected_secret = get_creative_media_admin_governance_secret()
    if not expected_secret or not hmac.compare_digest(
        str(x_v8_agent_os_admin_governance_secret or ""),
        expected_secret,
    ):
        raise HTTPException(status_code=403, detail="creative_media_admin_governance_required")


_OWNER_SCOPE_KEYS = {
    "sessionId",
    "session_id",
    "workspaceId",
    "workspace_id",
    "projectId",
    "project_id",
    "workspacePath",
    "workspace_path",
}


def _owner_scoped_payload(body: dict, scope: CreativeMediaOwnerScope) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in dict(body or {}).items()
        if key not in _OWNER_SCOPE_KEYS
    }
    payload.update(
        {
            "sessionId": scope.session_id,
            "workspaceId": scope.workspace_id,
            "projectId": scope.project_id,
            "workspacePath": scope.workspace_path,
        }
    )
    return payload


def _owner_scope_kwargs(scope: CreativeMediaOwnerScope) -> dict[str, str]:
    return {
        "session_id": scope.session_id,
        "workspace_id": scope.workspace_id,
        "project_id": scope.project_id,
        "workspace_path": scope.workspace_path,
    }


def _raise_owner_resource_not_found(detail: str, exc: Exception | None = None) -> None:
    raise HTTPException(status_code=404, detail=detail) from exc


router = APIRouter(
    prefix="/creative-media",
    tags=["creative-media"],
    dependencies=[Depends(require_creative_media_internal_secret)],
)


@router.get("/catalog")
async def get_creative_media_catalog():
    return creative_media_runtime.catalog()


@router.get("/reconciler/status")
async def get_creative_media_reconciler_status():
    return _build_reconciler_governance_status()


@router.get(
    "/governance/snapshot",
    dependencies=[Depends(require_creative_media_admin_governance)],
)
async def get_creative_media_governance_snapshot():
    return creative_media_runtime.governance_snapshot()


@router.post(
    "/governance/work-orders/{work_order_id}/archive",
    dependencies=[Depends(require_creative_media_admin_governance)],
)
async def governance_archive_creative_media_work_order(work_order_id: str):
    try:
        return {"workOrder": creative_media_runtime.archive_work_order(work_order_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc))


@router.post(
    "/governance/work-orders/{work_order_id}/delete",
    dependencies=[Depends(require_creative_media_admin_governance)],
)
async def governance_delete_creative_media_work_order(work_order_id: str):
    try:
        return {"workOrder": creative_media_runtime.delete_work_order(work_order_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc))


@router.get("/resolutions")
async def get_creative_media_resolutions():
    return creative_media_runtime.resolutions()


@router.get("/recipe-libraries")
async def get_creative_media_recipe_libraries():
    return creative_media_runtime.recipe_libraries()


@router.get("/model-preferences")
async def get_creative_media_model_preferences():
    return creative_media_runtime.get_model_preferences()


@router.post("/model-preferences")
async def save_creative_media_model_preferences(body: dict = Body(...)):
    try:
        return creative_media_runtime.save_model_preferences(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/recipes/compile")
async def compile_creative_media_recipe(
    body: dict = Body(...),
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        recipe = creative_media_runtime.compile_recipe(_owner_scoped_payload(body, scope))
        return {"recipe": recipe}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/recipes")
async def list_creative_media_recipes(
    modality: str | None = None,
    recipeKind: str | None = None,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "recipes": creative_media_runtime.list_recipes(
                modality=modality,
                recipe_kind=recipeKind,
                **_owner_scope_kwargs(scope),
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc


@router.get("/recipes/{recipe_id}")
async def get_creative_media_recipe(
    recipe_id: str,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        recipe = creative_media_runtime.get_recipe(recipe_id, **_owner_scope_kwargs(scope))
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media recipe not found", exc)
    if not recipe:
        raise HTTPException(status_code=404, detail="creative media recipe not found")
    return {"recipe": recipe}


@router.post("/work-orders/compile")
async def compile_creative_media_work_order(
    body: dict = Body(...),
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {"workOrder": creative_media_runtime.compile_work_order(_owner_scoped_payload(body, scope))}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/work-orders")
async def list_creative_media_work_orders(
    status: str | None = None,
    requestingRuntime: str | None = None,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "workOrders": creative_media_runtime.list_work_orders(
                status=status,
                requesting_runtime=requestingRuntime,
                **_owner_scope_kwargs(scope),
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc


@router.post("/work-orders/{work_order_id}/archive")
async def archive_creative_media_work_order(
    work_order_id: str,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "workOrder": creative_media_runtime.archive_work_order(
                work_order_id,
                **_owner_scope_kwargs(scope),
            )
        }
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media work order not found", exc)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/work-orders/{work_order_id}/delete")
async def delete_creative_media_work_order(
    work_order_id: str,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "workOrder": creative_media_runtime.delete_work_order(
                work_order_id,
                **_owner_scope_kwargs(scope),
            )
        }
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media work order not found", exc)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/assets")
async def register_creative_media_asset(
    body: dict = Body(...),
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        asset = creative_media_runtime.register_asset(_owner_scoped_payload(body, scope))
        return {"asset": asset}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/assets")
async def list_creative_media_assets(
    modality: str | None = None,
    role: str | None = None,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "assets": creative_media_runtime.list_assets(
                modality=modality,
                role=role,
                **_owner_scope_kwargs(scope),
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc


@router.post("/character-bibles")
async def create_creative_media_character_bible(
    body: dict = Body(...),
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "characterBible": creative_media_runtime.create_character_bible(
                _owner_scoped_payload(body, scope)
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/character-bibles")
async def list_creative_media_character_bibles(
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "characterBibles": creative_media_runtime.list_character_bibles(
                **_owner_scope_kwargs(scope)
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc


@router.get("/character-bibles/{bible_id}")
async def get_creative_media_character_bible(
    bible_id: str,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        bible = creative_media_runtime.get_character_bible(
            bible_id,
            **_owner_scope_kwargs(scope),
        )
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media character bible not found", exc)
    if not bible:
        raise HTTPException(status_code=404, detail="creative media character bible not found")
    return {"characterBible": bible}


@router.post("/keyframes")
async def register_creative_media_keyframe(
    body: dict = Body(...),
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "keyframe": creative_media_runtime.register_keyframe(
                _owner_scoped_payload(body, scope)
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/keyframes")
async def list_creative_media_keyframes(
    recipeId: str | None = None,
    role: str | None = None,
    characterBibleId: str | None = None,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "keyframes": creative_media_runtime.list_keyframes(
                recipe_id=recipeId,
                role=role,
                character_bible_id=characterBibleId,
                **_owner_scope_kwargs(scope),
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc


@router.get("/keyframes/{keyframe_id}")
async def get_creative_media_keyframe(
    keyframe_id: str,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        keyframe = creative_media_runtime.get_keyframe(
            keyframe_id,
            **_owner_scope_kwargs(scope),
        )
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media keyframe not found", exc)
    if not keyframe:
        raise HTTPException(status_code=404, detail="creative media keyframe not found")
    return {"keyframe": keyframe}


@router.post("/edit-plans")
async def create_creative_media_edit_plan(
    body: dict = Body(...),
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {"editPlan": creative_media_runtime.create_edit_plan(_owner_scoped_payload(body, scope))}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/edit-plans")
async def list_creative_media_edit_plans(
    recipeId: str | None = None,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "editPlans": creative_media_runtime.list_edit_plans(
                recipe_id=recipeId,
                **_owner_scope_kwargs(scope),
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc


@router.get("/edit-plans/{plan_id}")
async def get_creative_media_edit_plan(
    plan_id: str,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        plan = creative_media_runtime.get_edit_plan(plan_id, **_owner_scope_kwargs(scope))
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media edit plan not found", exc)
    if not plan:
        raise HTTPException(status_code=404, detail="creative media edit plan not found")
    return {"editPlan": plan}


@router.post("/renders")
async def render_creative_media_edit_plan(
    body: dict = Body(...),
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {"render": creative_media_runtime.render_edit_plan(_owner_scoped_payload(body, scope))}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/renders")
async def list_creative_media_renders(
    planId: str | None = None,
    status: str | None = None,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "renders": creative_media_runtime.list_renders(
                plan_id=planId,
                status=status,
                **_owner_scope_kwargs(scope),
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc


@router.get("/renders/{render_job_id}")
async def get_creative_media_render(
    render_job_id: str,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        render = creative_media_runtime.get_render(
            render_job_id,
            **_owner_scope_kwargs(scope),
        )
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media render job not found", exc)
    if not render:
        raise HTTPException(status_code=404, detail="creative media render job not found")
    return {"render": render}


@router.post("/jobs")
async def create_creative_media_job(
    body: dict = Body(...),
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        job = await creative_media_runtime.create_job(_owner_scoped_payload(body, scope))
        return {"job": creative_media_runtime.public_job_projection(job)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jobs")
async def list_creative_media_jobs(
    modality: str | None = None,
    status: str | None = None,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        jobs = creative_media_runtime.list_authorized_jobs(
            modality=modality,
            status=status,
            **_owner_scope_kwargs(scope),
        )
        return {
            "jobs": [
                creative_media_runtime.public_job_projection(job)
                for job in jobs
            ]
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc


@router.get("/jobs/{job_id}")
async def get_creative_media_job(
    job_id: str,
    refresh: bool = True,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        if refresh:
            job = await creative_media_runtime.refresh_authorized_job(
                job_id,
                **_owner_scope_kwargs(scope),
            )
        else:
            job = creative_media_runtime.get_authorized_job(
                job_id,
                **_owner_scope_kwargs(scope),
            )
        if not job:
            raise HTTPException(status_code=404, detail="creative media job not found")
        return {"job": creative_media_runtime.public_job_projection(job)}
    except HTTPException:
        raise
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media job not found", exc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jobs/{job_id}/artifacts")
async def get_creative_media_job_artifacts(
    job_id: str,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        job = creative_media_runtime.get_authorized_job(
            job_id,
            **_owner_scope_kwargs(scope),
        )
        if not job:
            raise HTTPException(status_code=404, detail="creative media job not found")
        return {
            "artifacts": creative_media_runtime.authorized_job_artifacts(
                job_id,
                **_owner_scope_kwargs(scope),
            )
        }
    except HTTPException:
        raise
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media job not found", exc)


@router.post("/quality-jobs")
async def create_creative_media_quality_job(
    body: dict = Body(...),
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "qualityJob": creative_media_runtime.create_quality_job(
                _owner_scoped_payload(body, scope)
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/quality-jobs")
async def list_creative_media_quality_jobs(
    status: str | None = None,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "qualityJobs": creative_media_runtime.list_quality_jobs(
                status=status,
                **_owner_scope_kwargs(scope),
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc


@router.get("/quality-jobs/{quality_job_id}")
async def get_creative_media_quality_job(
    quality_job_id: str,
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        quality_job = creative_media_runtime.get_quality_job(
            quality_job_id,
            **_owner_scope_kwargs(scope),
        )
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media quality job not found", exc)
    if not quality_job:
        raise HTTPException(status_code=404, detail="creative media quality job not found")
    return {"qualityJob": quality_job}


@router.post("/jobs/{job_id}/retry")
async def retry_creative_media_job(
    job_id: str,
    body: dict = Body(default_factory=dict),
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        job = await creative_media_runtime.retry_authorized_job(
            job_id,
            request=_owner_scoped_payload(body, scope),
            **_owner_scope_kwargs(scope),
        )
        return {"job": creative_media_runtime.public_job_projection(job)}
    except PermissionError as exc:
        _raise_owner_resource_not_found("creative media job not found", exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/cost-ledger")
async def get_creative_media_cost_ledger(
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "entries": creative_media_runtime.list_cost_ledger(
                **_owner_scope_kwargs(scope)
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc


@router.get("/safety-events")
async def get_creative_media_safety_events(
    scope: CreativeMediaOwnerScope = Depends(require_creative_media_owner_scope),
):
    try:
        return {
            "events": creative_media_runtime.list_safety_events(
                **_owner_scope_kwargs(scope)
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="creative_media_owner_scope_denied") from exc
