from fastapi import APIRouter, Body, HTTPException
from starlette.concurrency import run_in_threadpool

from .models import (
    RPACompileTracePayload,
    RPADraftCreatePayload,
    RPADraftPatchPayload,
    RPADraftPreparePayload,
    RPADraftRunPayload,
    RPADraftStepValidationPayload,
    RPAExistingFlowPayload,
    RPARecordingBrowserCapturePayload,
    RPARecordingCaptureAssistantPayload,
    RPARecordingDesktopSamplePayload,
    RPARecordingEventPayload,
    RPARecordingStartPayload,
    RPARecordingStopPayload,
    RPATemplateDecisionPayload,
    RPATemplateReviewPayload,
    RPATemplateRollbackPayload,
)


router = APIRouter()


def _rpa_runtime():
    from runtimes.rpa.runtime import rpa_runtime

    return rpa_runtime


@router.get("/rpa/availability")
async def get_rpa_availability():
    try:
        return _rpa_runtime().availability()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/drafts")
async def list_rpa_drafts(limit: int = 100, includeArchived: bool = False):
    try:
        return {"drafts": _rpa_runtime().list_drafts(limit=max(1, min(limit, 200)), include_archived=includeArchived)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts")
async def create_rpa_draft(payload: RPADraftCreatePayload):
    try:
        return _rpa_runtime().create_draft(payload.model_dump(by_alias=True, exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/drafts/{script_id}")
async def get_rpa_draft(script_id: str):
    try:
        draft = _rpa_runtime().get_draft(script_id)
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
        payload = _rpa_runtime().get_draft_source_traces(
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


@router.post("/rpa/drafts/{script_id}/patch")
async def patch_rpa_draft(script_id: str, payload: RPADraftPatchPayload):
    try:
        patch = payload.model_dump(by_alias=True, exclude_none=True)
        return _rpa_runtime().patch_draft(script_id, patch)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts/{script_id}/validate-step")
async def validate_rpa_draft_step(script_id: str, payload: RPADraftStepValidationPayload):
    try:
        return _rpa_runtime().validate_draft_step(
            script_id,
            step=dict(payload.step or {}),
            index=payload.index,
            mode=payload.mode,
            variables=dict(payload.variables or {}),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts/{script_id}/approve-template")
async def approve_rpa_draft_as_template(script_id: str, payload: RPATemplateDecisionPayload):
    try:
        return _rpa_runtime().approve_draft_as_template(
            script_id,
            reviewer=payload.reviewer or "admin_ui",
            notes=payload.notes,
            metadata_patch=dict(payload.metadata_patch or {}),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts/{script_id}/archive")
async def archive_rpa_draft(script_id: str, payload: dict | None = Body(default=None)):
    try:
        data = payload or {}
        return _rpa_runtime().archive_draft(
            script_id,
            actor=str(data.get("actor") or "admin_ui"),
            reason=data.get("reason"),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts/{script_id}/restore")
async def restore_rpa_draft(script_id: str, payload: dict | None = None):
    try:
        data = payload or {}
        return _rpa_runtime().restore_draft(script_id, actor=str(data.get("actor") or "admin_ui"))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/rpa/drafts/{script_id}")
async def delete_rpa_draft(script_id: str, confirm: bool = False):
    try:
        return _rpa_runtime().delete_draft(script_id, confirm=confirm)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/recordings")
async def list_rpa_recordings(limit: int = 20):
    try:
        return {"recordings": _rpa_runtime().list_recordings(limit=max(1, min(limit, 100)))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/recordings/{recording_id}")
async def get_rpa_recording(recording_id: str):
    try:
        recording = _rpa_runtime().get_recording(recording_id)
        if recording is None:
            raise HTTPException(status_code=404, detail=f"Recording '{recording_id}' not found")
        return recording
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/start")
async def start_rpa_recording(payload: RPARecordingStartPayload):
    try:
        return _rpa_runtime().start_recording(payload.model_dump(by_alias=True, exclude_none=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/events")
async def append_rpa_recording_event(recording_id: str, payload: RPARecordingEventPayload):
    try:
        return _rpa_runtime().append_recording_event(recording_id, payload.model_dump(by_alias=True, exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/desktop-sample")
async def sample_rpa_recording_desktop(recording_id: str, payload: RPARecordingDesktopSamplePayload):
    try:
        return _rpa_runtime().sample_recording_desktop(
            recording_id,
            payload.model_dump(by_alias=True, exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/browser-capture/start")
async def start_rpa_browser_capture(recording_id: str, payload: RPARecordingBrowserCapturePayload):
    try:
        return _rpa_runtime().start_browser_capture(
            recording_id,
            payload.model_dump(by_alias=True, exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/browser-capture/poll")
async def poll_rpa_browser_capture(recording_id: str, payload: RPARecordingBrowserCapturePayload):
    try:
        return _rpa_runtime().poll_browser_capture(
            recording_id,
            payload.model_dump(by_alias=True, exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/browser-capture/stop")
async def stop_rpa_browser_capture(recording_id: str, payload: RPARecordingBrowserCapturePayload):
    try:
        return _rpa_runtime().stop_browser_capture(
            recording_id,
            payload.model_dump(by_alias=True, exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/capture-assistant/status")
async def get_rpa_capture_assistant_status():
    try:
        return _rpa_runtime().capture_assistant_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/capture-assistant/start-service")
async def start_rpa_capture_assistant_service(payload: dict | None = Body(default=None)):
    try:
        return _rpa_runtime().start_capture_assistant_service(dict(payload or {}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/capture-assistant/start")
async def start_rpa_capture_assistant(recording_id: str, payload: RPARecordingCaptureAssistantPayload):
    try:
        return _rpa_runtime().start_capture_assistant(
            recording_id,
            payload.model_dump(by_alias=True, exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/capture-assistant/prepare-target")
async def prepare_rpa_capture_assistant_target(recording_id: str, payload: RPARecordingCaptureAssistantPayload):
    try:
        return _rpa_runtime().prepare_capture_target(
            recording_id,
            payload.model_dump(by_alias=True, exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/capture-assistant/poll")
async def poll_rpa_capture_assistant(recording_id: str, payload: RPARecordingCaptureAssistantPayload):
    try:
        return _rpa_runtime().poll_capture_assistant(
            recording_id,
            payload.model_dump(by_alias=True, exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/capture-assistant/capture")
async def capture_rpa_capture_assistant_event(recording_id: str, payload: RPARecordingEventPayload):
    try:
        return _rpa_runtime().capture_assistant_event(
            recording_id,
            payload.model_dump(by_alias=True, exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/capture-pool/{temp_element_id}/save")
async def save_rpa_capture_pool_item(recording_id: str, temp_element_id: str, payload: dict | None = Body(default=None)):
    try:
        data = dict(payload or {})
        return _rpa_runtime().save_capture_pool_item(
            recording_id,
            temp_element_id,
            name=data.get("name"),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/capture-assistant/stop")
async def stop_rpa_capture_assistant(recording_id: str, payload: RPARecordingCaptureAssistantPayload):
    try:
        return _rpa_runtime().stop_capture_assistant(
            recording_id,
            payload.model_dump(by_alias=True, exclude_none=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/pause")
async def pause_rpa_recording(recording_id: str):
    try:
        return _rpa_runtime().pause_recording(recording_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/resume")
async def resume_rpa_recording(recording_id: str):
    try:
        return _rpa_runtime().resume_recording(recording_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/cancel")
async def cancel_rpa_recording(recording_id: str):
    try:
        return _rpa_runtime().cancel_recording(recording_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/recordings/{recording_id}/stop")
async def stop_rpa_recording(recording_id: str, payload: RPARecordingStopPayload):
    try:
        return await run_in_threadpool(
            _rpa_runtime().stop_recording,
            recording_id,
            compile_draft=payload.compile_draft,
            save=payload.save,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/templates")
async def list_rpa_templates(limit: int = 100, app_id: str | None = None, status: str | None = None, includeArchived: bool = False):
    try:
        runtime = _rpa_runtime()
        templates = runtime.list_templates(
            limit=max(1, min(limit, 200)),
            app_id=app_id,
            status=status,
            include_archived=includeArchived,
        )
        return {
            "templates": templates,
            "summary": runtime.template_service.summarize_templates(templates),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/templates/{template_id}")
async def get_rpa_template(template_id: str):
    try:
        runtime = _rpa_runtime()
        payload = runtime.get_template(template_id)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
        return {
            "template": payload,
            "summary": runtime.template_service.summarize_templates([payload]),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rpa/templates/{template_id}/history")
async def list_rpa_template_history(template_id: str, limit: int = 50):
    try:
        runtime = _rpa_runtime()
        current = runtime.get_template(template_id)
        history = runtime.list_template_history(template_id, limit=max(1, min(limit, 200)))
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
        return _rpa_runtime().review_template(
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


@router.post("/rpa/templates/{template_id}/archive")
async def archive_rpa_template(template_id: str, payload: dict | None = Body(default=None)):
    try:
        data = payload or {}
        template = _rpa_runtime().archive_template(
            template_id,
            actor=str(data.get("actor") or "admin_ui"),
            reason=data.get("reason"),
        )
        return {"template": template, "summary": _rpa_runtime().template_service.summarize_templates([template])}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/templates/{template_id}/restore")
async def restore_rpa_template(template_id: str, payload: dict | None = None):
    try:
        data = payload or {}
        template = _rpa_runtime().restore_template(template_id, actor=str(data.get("actor") or "admin_ui"))
        return {"template": template, "summary": _rpa_runtime().template_service.summarize_templates([template])}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/rpa/templates/{template_id}")
async def delete_rpa_template(template_id: str, confirm: bool = False):
    try:
        return _rpa_runtime().delete_template(template_id, confirm=confirm, actor="admin_ui")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/templates/{template_id}/approve")
async def approve_rpa_template(template_id: str, payload: RPATemplateDecisionPayload):
    try:
        return _rpa_runtime().approve_template(
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
        return _rpa_runtime().freeze_template(
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
        return _rpa_runtime().rollback_template(
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
        return {"scripts": _rpa_runtime().list_robot_scripts(limit=max(1, min(limit, 200)))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/compile/{run_id}")
async def compile_rpa_draft_from_trace(run_id: str, payload: RPACompileTracePayload):
    try:
        return await run_in_threadpool(_rpa_runtime().compile_trace_to_draft, run_id, save=payload.save)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/compile")
async def compile_rpa_draft_from_traces(payload: RPACompileTracePayload):
    try:
        run_ids = [str(item or "").strip() for item in list(payload.run_ids or []) if str(item or "").strip()]
        if not run_ids:
            raise HTTPException(status_code=400, detail="runIds 不能为空")
        return await run_in_threadpool(_rpa_runtime().compile_traces_to_draft, run_ids, save=payload.save)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts/{script_id}/export")
async def export_rpa_draft(script_id: str, payload: RPADraftPreparePayload):
    try:
        return await run_in_threadpool(_rpa_runtime().export_draft_to_robot, script_id=script_id, output_dir=payload.output_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts/{script_id}/prepare")
async def prepare_rpa_draft_run(script_id: str, payload: RPADraftPreparePayload):
    try:
        return await run_in_threadpool(
            _rpa_runtime().prepare_draft_run,
            script_id=script_id,
            variables=dict(payload.variables or {}),
            output_dir=payload.output_dir,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/drafts/{script_id}/run")
async def run_rpa_draft(script_id: str, payload: RPADraftRunPayload):
    try:
        return await run_in_threadpool(
            _rpa_runtime().run_draft,
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
            non_chat_run=payload.non_chat_run,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/run-existing")
async def run_existing_rpa_flow(payload: RPAExistingFlowPayload):
    try:
        return await run_in_threadpool(
            _rpa_runtime().run_existing_flow,
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
            non_chat_run=payload.non_chat_run,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rpa/prepare-existing")
async def prepare_existing_rpa_flow(payload: RPAExistingFlowPayload):
    try:
        return await run_in_threadpool(
            _rpa_runtime().prepare_existing_run,
            robot_file=payload.robot_file,
            variables=dict(payload.variables or {}),
            output_dir=payload.output_dir,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
