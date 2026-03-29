from fastapi import APIRouter, HTTPException

from .models import (
    RPACompileTracePayload,
    RPADraftPreparePayload,
    RPADraftRunPayload,
    RPAExistingFlowPayload,
    RPATemplateDecisionPayload,
    RPATemplateReviewPayload,
    RPATemplateRollbackPayload,
)
from runtimes.rpa.runtime import rpa_runtime


router = APIRouter()


@router.get("/rpa/availability")
async def get_rpa_availability():
    try:
        return rpa_runtime.availability()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/drafts")
async def list_rpa_drafts(limit: int = 100):
    try:
        return {"drafts": rpa_runtime.list_drafts(limit=max(1, min(limit, 200)))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/drafts/{script_id}")
async def get_rpa_draft(script_id: str):
    try:
        draft = rpa_runtime.get_draft(script_id)
        if draft is None:
            raise HTTPException(status_code=404, detail=f"Draft '{script_id}' not found")
        return draft
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/drafts/{script_id}/source-traces")
async def get_rpa_draft_source_traces(script_id: str, include_steps: bool = True, max_steps: int = 8):
    try:
        payload = rpa_runtime.get_draft_source_traces(
            script_id,
            include_steps=include_steps,
            max_steps=max(1, min(max_steps, 20)),
        )
        if payload is None:
            raise HTTPException(status_code=404, detail=f"Draft '{script_id}' not found")
        return payload
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/templates")
async def list_rpa_templates(limit: int = 100, app_id: str | None = None, status: str | None = None):
    try:
        templates = rpa_runtime.list_templates(
            limit=max(1, min(limit, 200)),
            app_id=app_id,
            status=status,
        )
        return {
            "templates": templates,
            "summary": rpa_runtime.template_service.summarize_templates(templates),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/templates/{template_id}")
async def get_rpa_template(template_id: str):
    try:
        payload = rpa_runtime.get_template(template_id)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
        return {
            "template": payload,
            "summary": rpa_runtime.template_service.summarize_templates([payload]),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/templates/{template_id}/history")
async def list_rpa_template_history(template_id: str, limit: int = 50):
    try:
        current = rpa_runtime.get_template(template_id)
        history = rpa_runtime.list_template_history(template_id, limit=max(1, min(limit, 200)))
        return {
            "templateId": template_id,
            "current": current,
            "history": history,
            "summary": {
                "historyCount": len(history),
                "currentStage": ((current or {}).get("governance") or {}).get("stage") if isinstance(current, dict) else None,
                "currentStatus": ((current or {}).get("status")) if isinstance(current, dict) else None,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/templates/{template_id}/review")
async def review_rpa_template(template_id: str, payload: RPATemplateReviewPayload):
    try:
        return rpa_runtime.review_template(
            template_id,
            decision=payload.decision,
            reviewer=payload.reviewer or "system",
            notes=payload.notes,
            metadata_patch=dict(payload.metadata_patch or {}),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/templates/{template_id}/approve")
async def approve_rpa_template(template_id: str, payload: RPATemplateDecisionPayload):
    try:
        return rpa_runtime.approve_template(
            template_id,
            reviewer=payload.reviewer or "system",
            notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/templates/{template_id}/freeze")
async def freeze_rpa_template(template_id: str, payload: RPATemplateDecisionPayload):
    try:
        return rpa_runtime.freeze_template(
            template_id,
            reviewer=payload.reviewer or "system",
            notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/templates/{template_id}/rollback")
async def rollback_rpa_template(template_id: str, payload: RPATemplateRollbackPayload):
    try:
        return rpa_runtime.rollback_template(
            template_id,
            revision=payload.revision,
            history_path=payload.history_path,
            reviewer=payload.reviewer or "system",
            notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/scripts")
async def list_rpa_robot_scripts(limit: int = 100):
    try:
        return {"scripts": rpa_runtime.list_robot_scripts(limit=max(1, min(limit, 200)))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/compile/{run_id}")
async def compile_rpa_draft_from_trace(run_id: str, payload: RPACompileTracePayload):
    try:
        return rpa_runtime.compile_trace_to_draft(run_id, save=payload.save)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/compile")
async def compile_rpa_draft_from_traces(payload: RPACompileTracePayload):
    try:
        run_ids = [str(item or "").strip() for item in list(payload.run_ids or []) if str(item or "").strip()]
        if not run_ids:
            raise HTTPException(status_code=400, detail="runIds 不能为空")
        return rpa_runtime.compile_traces_to_draft(run_ids, save=payload.save)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts/{script_id}/export")
async def export_rpa_draft(script_id: str, payload: RPADraftPreparePayload):
    try:
        return rpa_runtime.export_draft_to_robot(script_id=script_id, output_dir=payload.output_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts/{script_id}/prepare")
async def prepare_rpa_draft_run(script_id: str, payload: RPADraftPreparePayload):
    try:
        return rpa_runtime.prepare_draft_run(
            script_id=script_id,
            variables=dict(payload.variables or {}),
            output_dir=payload.output_dir,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts/{script_id}/run")
async def run_rpa_draft(script_id: str, payload: RPADraftRunPayload):
    try:
        return rpa_runtime.run_draft(
            script_id=script_id,
            variables=dict(payload.variables or {}),
            output_dir=payload.output_dir,
            timeout_ms=payload.timeout_ms,
            cwd=payload.cwd,
            session_id=payload.session_id,
            run_id=payload.run_id,
            user_id=payload.user_id or "anonymous",
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            trigger_source=payload.trigger_source or "manual",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/run-existing")
async def run_existing_rpa_flow(payload: RPAExistingFlowPayload):
    try:
        return rpa_runtime.run_existing_flow(
            robot_file=payload.robot_file,
            variables=dict(payload.variables or {}),
            output_dir=payload.output_dir,
            timeout_ms=payload.timeout_ms,
            cwd=payload.cwd,
            session_id=payload.session_id,
            run_id=payload.run_id,
            user_id=payload.user_id or "anonymous",
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            trigger_source=payload.trigger_source or "manual",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/prepare-existing")
async def prepare_existing_rpa_flow(payload: RPAExistingFlowPayload):
    try:
        return rpa_runtime.prepare_existing_run(
            robot_file=payload.robot_file,
            variables=dict(payload.variables or {}),
            output_dir=payload.output_dir,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
