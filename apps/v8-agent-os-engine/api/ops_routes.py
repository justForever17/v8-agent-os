import asyncio
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, UploadFile, WebSocket
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal

from core.storage import storage
from core.safety_active_defense import safety_active_defense_monitor
from erc.safety_guardian import safety_guardian


router = APIRouter()


def _automation_admin_owner(request: Request) -> str:
    # Reuse the existing Admin-to-Engine relay authentication. A role header
    # alone is never a principal; only the authenticated Admin proxy sets it.
    from api.system_operation_routes import system_operation_owner
    try:
        owner = system_operation_owner(request)
    except HTTPException:
        raise HTTPException(status_code=401, detail={"code": "automation_admin_auth_required", "message": "请登录管理员后重试。"}) from None
    if request.headers.get("x-v8-admin-role") != "ADMIN":
        raise HTTPException(status_code=403, detail={"code": "automation_admin_required", "message": "此操作仅限管理员。"})
    return owner


class AutomationOutcomeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    observation: str = Field(min_length=1, max_length=2000)
    reference: str | None = Field(default=None, max_length=1000)


class AutomationReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["completed", "failed"]
    evidence: AutomationOutcomeEvidence


@router.get("/automation/deliveries")
def list_unknown_automation_deliveries(request: Request, owner: str = Depends(_automation_admin_owner)):
    from core.automation.delivery import automation_delivery_service
    try:
        return automation_delivery_service.list_for_admin(
            limit=int(request.query_params.get("limit", "25")), after=request.query_params.get("after"),
            ownership=request.query_params.get("ownership", "system,unresolved"),
        )
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "automation_reconciliation_invalid", "message": "分页参数无效。"}) from None
    except Exception:
        raise HTTPException(status_code=500, detail={"code": "automation_delivery_unavailable", "message": "暂时无法读取待核对任务。"}) from None


@router.post("/automation/deliveries/{delivery_id}/reconcile")
async def reconcile_automation_delivery(delivery_id: str, request: Request, owner: str = Depends(_automation_admin_owner)):
    from core.automation.delivery import automation_delivery_service
    from core.database import db
    try:
        body = await request.body()
        if len(body) > 16384:
            raise ValueError()
        incoming = AutomationReconciliationRequest.model_validate_json(body)
    except (ValueError, ValidationError):
        # Validation must not echo submitted evidence or unrecognized secret fields.
        raise HTTPException(status_code=422, detail={"code": "automation_reconciliation_invalid", "message": "请提供核对结果与非空观察证据。"}) from None
    if not db.get_automation_delivery(delivery_id):
        raise HTTPException(status_code=404, detail={"code": "automation_delivery_not_found", "message": "未找到这条任务记录。"})
    try:
        automation_delivery_service.reconcile_from_admin(
            delivery_id=delivery_id, outcome=incoming.outcome,
            evidence=incoming.evidence.model_dump(exclude_none=True), authenticated_owner=owner,
        )
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=409, detail={"code": "automation_reconciliation_conflict", "message": "任务状态已变化或与凭据冲突，请刷新后重新核对。"}) from None
    except Exception:
        raise HTTPException(status_code=500, detail={"code": "automation_delivery_unavailable", "message": "暂时无法保存核对结果。"}) from None
    item = db.get_automation_delivery(delivery_id)
    receipt = db.get_side_effect_receipt(item["receipt_key"]) if item["receipt_key"] else None
    return {"status": "success", "deliveryId": delivery_id, "phase": item["phase"],
            "receiptState": (receipt or {}).get("state") or "missing", "summary": "已记录人工核对结果，未再次执行该任务。"}


class HookToggleRequest(BaseModel):
    name: str
    enabled: bool


class CronRunRequest(BaseModel):
    job_id: str


class TerminalInputRequest(BaseModel):
    input_text: str


class SensitiveTerminalInputRequest(BaseModel):
    input_text: str
    secret_type: str | None = None


class SafetyDryRunRequest(BaseModel):
    command: str
    runtime_context: dict | None = None


class ActiveDefenseIncidentActionRequest(BaseModel):
    note: str | None = None


@router.get("/settings/safety-guardian")
async def get_safety_guardian_settings():
    try:
        return safety_guardian.export_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings/safety-guardian")
async def save_safety_guardian_settings(request: Request):
    try:
        data = await request.json()
        config = safety_guardian.save_config(data)
        return {"status": "success", "config": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/safety/dashboard")
async def get_safety_dashboard(limit: int = 80):
    try:
        return safety_guardian.build_dashboard_payload(limit=max(1, min(limit, 200)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/safety/allowlist")
async def list_safety_allowlist(status: str | None = None, limit: int = 100):
    try:
        return {"items": safety_guardian.list_safety_allowlist_entries(status=status, limit=max(1, min(limit, 200)))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/safety/allowlist/{entry_id}/revoke")
async def revoke_safety_allowlist(entry_id: str):
    try:
        entry = safety_guardian.revoke_safety_allowlist_entry(entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="safety allowlist entry not found")
        return {"status": "success", "entry": entry}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/safety/dry-run")
async def explain_safety_command(request: SafetyDryRunRequest):
    try:
        return safety_guardian.explain_system_command(request.command, runtime_context=request.runtime_context or {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/safety/active-defense/incidents/{incident_id}/ignore")
async def ignore_active_defense_incident(incident_id: str, request: ActiveDefenseIncidentActionRequest | None = None):
    try:
        incident = safety_active_defense_monitor.ignore_incident(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail="active defense incident not found")
        return {"status": "success", "incident": incident, "dashboard": safety_active_defense_monitor.dashboard(sample=False)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/safety/active-defense/incidents/{incident_id}/confirm")
async def confirm_active_defense_incident(incident_id: str, request: ActiveDefenseIncidentActionRequest | None = None):
    try:
        incident = safety_active_defense_monitor.confirm_incident(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail="active defense incident not found")
        return {"status": "success", "incident": incident, "dashboard": safety_active_defense_monitor.dashboard(sample=False)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hooks")
async def get_hooks_config():
    try:
        return storage.get_hooks_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hooks")
async def save_hooks_config(request: Request):
    try:
        data = await request.json()
        storage.save_hooks_config(data)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hooks/toggle")
async def toggle_hook(request: HookToggleRequest):
    try:
        config = storage.get_hooks_config()
        hooks_list = config.get("hooks", [])
        for hook in hooks_list:
            if hook.get("name") == request.name:
                hook["enabled"] = request.enabled
                config["hooks"] = hooks_list
                storage.save_hooks_config(config)
                return {"status": "success", "name": request.name, "enabled": request.enabled}
        raise HTTPException(status_code=404, detail=f"Hook '{request.name}' not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cron/config")
async def get_cron_config():
    try:
        return storage.get_cron_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cron/config")
async def save_cron_config(request: Request):
    try:
        data = await request.json()
        from core.cron_manager import cron_manager

        storage.save_cron_config(data)
        cron_manager.sync_jobs_to_scheduler()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cron/run")
async def run_cron_job(request: CronRunRequest, background_tasks: BackgroundTasks):
    try:
        from core.cron_manager import cron_manager

        config = storage.get_cron_config()
        jobs = config.get("jobs", [])
        target_job = next((job for job in jobs if job.get("id") == request.job_id), None)
        if not target_job:
            raise HTTPException(status_code=404, detail=f"Job '{request.job_id}' not found")

        background_tasks.add_task(cron_manager.execute_job, target_job)
        return {"status": "success", "message": f"Job '{request.job_id}' triggered"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cron/logs")
async def get_cron_execution_logs(limit: int = 100, offset: int = 0):
    try:
        from core.knowledge_db import knowledge_db

        return {"logs": knowledge_db.get_execution_logs(limit=limit, offset=offset)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit/logs")
async def get_audit_logs(limit: int = 100, offset: int = 0, source_type: str = None, status: str = None):
    try:
        from core.audit_logger import audit_logger

        logs = audit_logger.get_logs(limit=limit, offset=offset, source_type=source_type, status=status)
        return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/audit/logs")
async def clear_audit_logs(source_type: str = None, status: str = None):
    try:
        from core.database import db

        return db.clear_audit_logs(source_type=source_type, status=status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/upload")
async def upload_memory_docs(
    files: list[UploadFile] = File(...),
    chunk_size: int = Form(1500),
    chunk_overlap: int = Form(200),
    trusted_upload: bool = Form(False),
):
    try:
        from core.document_parser import DocumentIngestionDependencyError, document_parser
        from core.document_chunker import document_chunker
        from core.knowledge_db import knowledge_db
        from core.knowledge_projection import knowledge_projection_service
        from core.memory_observability import log_memory_observation
        from core.code_chunker import code_chunker
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        import logging
        import os
        import uuid

        logger = logging.getLogger(__name__)
        temp_dir = Path("workspace/temp_uploads")
        temp_dir.mkdir(parents=True, exist_ok=True)
        processed_count = 0
        total_chunks = 0
        total_chars = 0
        maintainer_source = "human_admin" if trusted_upload else "imported_document"
        confidence = 0.67 if trusted_upload else 0.60

        for file in files:
            file_path = temp_dir / file.filename
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            document_parser.ensure_document_ingestion_dependencies(file_path)

            markdown_content = document_parser.parse_file(file_path)
            total_chars += len(markdown_content)

            ext = os.path.splitext(file.filename)[1].lower()
            code_extensions = [".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".java", ".cpp", ".c", ".cs", ".rb", ".php", ".rs", ".html", ".htm"]
            parent_chunk_size = chunk_size * 2
            if ext in code_extensions:
                parent_chunks = code_chunker.chunk_code(
                    code_text=markdown_content,
                    filename=file.filename,
                    chunk_size=parent_chunk_size,
                    chunk_overlap=0,
                )
            else:
                parent_chunks = document_chunker.chunk_markdown(
                    markdown_text=markdown_content,
                    chunk_size=parent_chunk_size,
                    chunk_overlap=0,
                )

            child_splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            canonical_chunks = []
            for p_idx, p_chunk in enumerate(parent_chunks):
                parent_id = f"parent-{uuid.uuid4().hex[:8]}"
                canonical_chunks.append(
                    {
                        "id": parent_id,
                        "fact": p_chunk["text"],
                        "parent_id": None,
                        "metadata": {
                        "ingestionSource": "memory_upload",
                        "trustedUpload": trusted_upload,
                        "chunkRole": "parent",
                        "chunkSize": chunk_size,
                        "chunkOverlap": chunk_overlap,
                        },
                    }
                )
                child_texts = child_splitter.split_text(p_chunk["text"])
                for c_idx, c_text in enumerate(child_texts):
                    child_id = f"uload-{uuid.uuid4().hex[:8]}"
                    metadata = {str(k): str(v) for k, v in p_chunk["metadata"].copy().items()}
                    metadata.update(
                        {
                            "source_file": file.filename,
                            "chunk_idx": f"{p_idx}-{c_idx}",
                            "category": "user_document",
                            "scope": "global",
                            "parent_id": parent_id,
                        }
                    )
                    total_chunks += 1
                    canonical_chunks.append(
                        {
                            "id": child_id,
                            "fact": c_text,
                            "parent_id": parent_id,
                            "metadata": {
                            "ingestionSource": "memory_upload",
                            "trustedUpload": trusted_upload,
                            "chunkRole": "child",
                            "chunkSize": chunk_size,
                            "chunkOverlap": chunk_overlap,
                            "parentId": parent_id,
                            },
                        }
                    )

            if canonical_chunks:
                knowledge_db.replace_user_document_chunks(
                    filename=file.filename,
                    chunks=canonical_chunks,
                    maintainer_source=maintainer_source,
                    confidence=confidence,
                    promotion_reason="trusted_admin_upload" if trusted_upload else "document_upload",
                )
                knowledge_projection_service.process_outbox(limit=max(50, len(canonical_chunks) * 2))
                processed_count += 1

            file_path.unlink(missing_ok=True)

        log_memory_observation(
            "document_upload_index",
            "SUCCESS",
            trigger="admin_upload",
            callsLlm=False,
            fileCount=len(files),
            processedCount=processed_count,
            chunkCount=total_chunks,
            inputCharEstimate=total_chars,
            chunkSize=chunk_size,
            chunkOverlap=chunk_overlap,
            trustedUpload=trusted_upload,
            maintainerSource=maintainer_source,
            confidence=confidence,
        )
        return {
            "status": "success",
            "message": f"Successfully parsed {processed_count} files ({total_chars} chars) into {total_chunks} semantic chunks.",
            "trustedUpload": trusted_upload,
            "maintainerSource": maintainer_source,
            "confidence": confidence,
        }
    except DocumentIngestionDependencyError as e:
        raise HTTPException(status_code=424, detail=e.to_payload())
    except Exception as e:
        import logging

        logging.error(f"[Upload] Error processing documents: {e}")
        raise HTTPException(status_code=500, detail=f"Error parsing documents: {str(e)}")


@router.get("/memory/documents")
async def get_memory_documents():
    try:
        from core.knowledge_db import knowledge_db

        return {"documents": knowledge_db.get_user_documents()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/documents/{filename}")
async def delete_memory_document(filename: str):
    try:
        from core.knowledge_db import knowledge_db
        from core.knowledge_projection import knowledge_projection_service

        deleted_fact_ids = knowledge_db.delete_user_document(filename)
        if deleted_fact_ids:
            knowledge_projection_service.process_outbox(limit=max(50, len(deleted_fact_ids) * 2))
        return {
            "status": "success",
            "message": f"Deleted {len(deleted_fact_ids)} chunks for {filename}",
            "deleted_chunks": len(deleted_fact_ids),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def require_process_access(cmd_id: str, x_v8_agent_os_secret: str | None = Header(default=None), x_v8_agent_os_user_email: str | None = Header(default=None), x_v8_agent_os_user_id: str | None = Header(default=None), request: Request = None):
    import hmac
    from core.system_base import get_internal_secret
    from core.tools.native.command import _bg_processes
    from core.database import db
    from core.auth_context import EngineAuthContext, is_local_client
    context = request.scope.get("state", {}).get("engine_auth_context") if request else None
    trusted_context = isinstance(context, EngineAuthContext) and (is_local_client(context) or context.device_kind == "human_phone")
    if trusted_context:
        x_v8_agent_os_user_email, x_v8_agent_os_user_id = context.session_id, context.subject
    secret = get_internal_secret()
    if not trusted_context and (not secret or not hmac.compare_digest(secret, str(x_v8_agent_os_secret or "")) or not x_v8_agent_os_user_email):
        raise HTTPException(status_code=401, detail="Unauthorized")
    process = _bg_processes.get(cmd_id)
    if process is None: raise HTTPException(status_code=404, detail="Process not found")
    # A manual terminal also exists in the process registry. The process route
    # must enforce the same owner when used as an alternate transport.
    from core.client_terminal_broker import MANUAL_TERMINAL_SESSION_PREFIX, require_terminal_owner
    if str(process.session_id or "").startswith(MANUAL_TERMINAL_SESSION_PREFIX):
        try: require_terminal_owner(cmd_id, x_v8_agent_os_user_email)
        except PermissionError as exc: raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        return process
    owner = str((db.get_session(str(process.session_id or "")) or {}).get("user_id") or "")
    if trusted_context and context.device_kind == "human_phone" and (not owner or owner == "anonymous"):
        raise HTTPException(status_code=404, detail="Process session not found")
    if owner and owner != "anonymous" and owner not in {str(x_v8_agent_os_user_id or ""), str(x_v8_agent_os_user_email)}:
        raise HTTPException(status_code=403, detail="Process owner mismatch")
    return process


@router.get("/bg_processes/{cmd_id}")
async def get_bg_process_output(cmd_id: str, cursor: int = 0, _auth=Depends(require_process_access)):
    try:
        from core.native_tools import _bg_processes, _prune_stale_background_processes

        _prune_stale_background_processes()
        bg_proc = _bg_processes.get(cmd_id)
        if not bg_proc:
            return {"status": "not_found", "output": "", "is_running": False}
        try: chunk = bg_proc.read_output(cursor)
        except ValueError:
            chunk = bg_proc.read_output(0)
            return {"status": "success", "output": "", "outputReset": True, "outputCursor": 0, "outputGeneration": chunk["generation"], "is_running": bg_proc.is_running}
        output = chunk["data"]
        process = bg_proc.status_snapshot()
        return {
            "status": "success",
            "output": output,
            "outputCursor": chunk["cursor"],
            "outputGeneration": chunk["generation"],
            "outputHasMore": chunk["hasMore"],
            "outputTotalBytes": chunk["totalBytes"],
            "is_running": bg_proc.is_running,
            "ttyMode": process.get("tty_mode"),
            "screenMode": process.get("screen_mode"),
            "screenSnapshot": process.get("screen_snapshot"),
            "stableScreenSnapshot": process.get("stable_screen_snapshot"),
            "screenVersion": process.get("screen_version"),
            "rawFrameVersion": process.get("raw_frame_version"),
            "rawBytes": process.get("raw_bytes"),
            "cursor": process.get("cursor"),
            "cols": process.get("cols"),
            "rows": process.get("rows"),
            "alternateScreen": process.get("alternate_screen"),
            "awaitingInput": process.get("awaiting_input"),
            "observationState": process.get("observation_state"),
            "textEncoding": process.get("text_encoding"),
            "encodingState": process.get("encoding_state"),
            "encodingNotes": process.get("encoding_notes"),
            "lastScreenAt": process.get("last_screen_at"),
            "lastRawFrameAt": process.get("last_raw_frame_at"),
            "lastRawFramePreview": process.get("last_raw_frame_preview"),
            "commandDiagnostics": process.get("command_diagnostics"),
            "process": process,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bg_processes/{cmd_id}/input")
async def send_bg_process_input(cmd_id: str, request: TerminalInputRequest, _auth=Depends(require_process_access)):
    try:
        from core.native_tools import _bg_processes
        process = _bg_processes.get(cmd_id)
        if not process: raise HTTPException(status_code=404, detail="Process not found")
        try: await asyncio.to_thread(process.write_input, request.input_text)
        except RuntimeError as error:
            try: detail = json.loads(str(error))
            except ValueError: detail = str(error)
            raise HTTPException(status_code=409, detail=detail) from error
        return {"status": "success", "message": '{"ok":true}'}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bg_processes/{cmd_id}/sensitive-input")
async def send_bg_process_sensitive_input(cmd_id: str, request: SensitiveTerminalInputRequest, _auth=Depends(require_process_access)):
    try:
        from core.native_tools import _bg_processes

        process = _bg_processes.get(cmd_id)
        if process is None:
            raise HTTPException(status_code=404, detail=f"No active background command with ID: {cmd_id}")
        process_status = process.status_snapshot()
        if not bool(process_status.get("interactive")) or not bool(process_status.get("uses_tty")):
            raise HTTPException(
                status_code=409,
                detail={
                    "ok": False,
                    "kind": "command_session_not_interactive",
                    "error": "command_session_not_interactive",
                    "summary": "当前命令会话使用 pipe 后端，不接受交互输入。",
                    "terminalMode": process_status.get("terminal_mode"),
                    "resolvedTerminalMode": process_status.get("resolved_terminal_mode") or "pipe",
                    "backend": process_status.get("backend"),
                    "recommendedNextAction": "terminate_then_restart_with_pty",
                },
            )
        await asyncio.to_thread(process.write_input, request.input_text)
        return {
            "status": "success",
            "secretInputSeen": True,
            "secretType": request.secret_type or "terminal_secret",
            "target": "background_process_stdin",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bg_processes/{cmd_id}/terminate")
async def terminate_bg_process(cmd_id: str, _auth=Depends(require_process_access)):
    try:
        from core.native_tools import terminate_background_command

        result = terminate_background_command.invoke(cmd_id)
        from core.native_tools import _bg_processes
        process = _bg_processes.get(cmd_id)
        if process and process.is_running: raise HTTPException(status_code=409, detail="Process is still running")
        return {"status": "success", "message": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bg_processes/{cmd_id}/resize")
async def resize_bg_process(cmd_id: str, request: Request, _auth=Depends(require_process_access)):
    from core.native_tools import _bg_processes
    body = await request.json()
    return _bg_processes[cmd_id].resize_terminal(int(body.get("cols") or 80), int(body.get("rows") or 24))


@router.websocket("/bg_processes/{cmd_id}/ws")
async def bg_process_websocket(websocket: WebSocket, cmd_id: str):
    # Browser clients use the authenticated BFF cursor endpoint. Keep this legacy
    # endpoint closed rather than exposing command output without an identity.
    # Phone falls back to its existing authorized HTTP observation path.
    await websocket.close(code=1008)
