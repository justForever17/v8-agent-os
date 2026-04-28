import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile, WebSocket
from pydantic import BaseModel

from core.storage import storage
from erc.safety_guardian import safety_guardian


router = APIRouter()


class HookToggleRequest(BaseModel):
    name: str
    enabled: bool


class CronRunRequest(BaseModel):
    job_id: str


class TerminalInputRequest(BaseModel):
    input_text: str


class SafetyDryRunRequest(BaseModel):
    command: str
    runtime_context: dict | None = None


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
        from core.vector_store import get_vector_store
        from core.knowledge_db import knowledge_db
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
        vs = get_vector_store()
        maintainer_source = "human_admin" if trusted_upload else "imported_document"
        confidence = 0.67 if trusted_upload else 0.60

        for file in files:
            file_path = temp_dir / file.filename
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            document_parser.ensure_document_ingestion_dependencies(file_path)

            deleted_ids = knowledge_db.delete_user_document(file.filename)
            if deleted_ids:
                vs.delete_by_ids(deleted_ids)

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
            docs_to_add = []
            for p_idx, p_chunk in enumerate(parent_chunks):
                parent_id = f"parent-{uuid.uuid4().hex[:8]}"
                knowledge_db.add_knowledge(
                    fact_id=parent_id,
                    fact=p_chunk["text"],
                    category="user_document",
                    scope="global",
                    source_session=file.filename,
                    parent_id=None,
                    maintainer_source=maintainer_source,
                    confidence=confidence,
                    promotion_reason="trusted_admin_upload" if trusted_upload else "document_upload",
                    metadata={
                        "ingestionSource": "memory_upload",
                        "trustedUpload": trusted_upload,
                        "chunkRole": "parent",
                        "chunkSize": chunk_size,
                        "chunkOverlap": chunk_overlap,
                    },
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
                    docs_to_add.append({"id": child_id, "text": c_text, "metadata": metadata})
                    total_chunks += 1
                    knowledge_db.add_knowledge(
                        fact_id=child_id,
                        fact=c_text,
                        category="user_document",
                        scope="global",
                        source_session=file.filename,
                        parent_id=parent_id,
                        maintainer_source=maintainer_source,
                        confidence=confidence,
                        promotion_reason="trusted_admin_upload" if trusted_upload else "document_upload",
                        metadata={
                            "ingestionSource": "memory_upload",
                            "trustedUpload": trusted_upload,
                            "chunkRole": "child",
                            "chunkSize": chunk_size,
                            "chunkOverlap": chunk_overlap,
                            "parentId": parent_id,
                        },
                    )

            if docs_to_add:
                vs.add_documents(docs_to_add)
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
        from core.vector_store import get_vector_store

        deleted_fact_ids = knowledge_db.delete_user_document(filename)
        if deleted_fact_ids:
            vs = get_vector_store()
            vs.delete_by_ids(deleted_fact_ids)
        return {
            "status": "success",
            "message": f"Deleted {len(deleted_fact_ids)} chunks for {filename}",
            "deleted_chunks": len(deleted_fact_ids),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bg_processes/{cmd_id}")
async def get_bg_process_output(cmd_id: str):
    try:
        from core.native_tools import _bg_processes, _prune_stale_background_processes

        _prune_stale_background_processes()
        bg_proc = _bg_processes.get(cmd_id)
        if not bg_proc:
            return {"status": "not_found", "output": "", "is_running": False}
        output = bg_proc.get_new_output()
        process = bg_proc.status_snapshot()
        return {
            "status": "success",
            "output": output,
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
async def send_bg_process_input(cmd_id: str, request: TerminalInputRequest):
    try:
        from core.native_tools import send_background_input

        result = send_background_input.invoke({"command_id": cmd_id, "input_text": request.input_text})
        return {"status": "success", "message": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bg_processes/{cmd_id}/terminate")
async def terminate_bg_process(cmd_id: str):
    try:
        from core.native_tools import terminate_background_command

        result = terminate_background_command.invoke(cmd_id)
        return {"status": "success", "message": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/bg_processes/{cmd_id}/ws")
async def bg_process_websocket(websocket: WebSocket, cmd_id: str):
    await websocket.accept()
    from core.native_tools import _bg_processes

    if cmd_id not in _bg_processes:
        await websocket.send_text(f"Error: No active background command with ID {cmd_id}.")
        await websocket.close()
        return

    bg_proc = _bg_processes[cmd_id]
    initial_output = "".join(bg_proc.output_history)
    if initial_output:
        await websocket.send_text(initial_output)

    async def read_from_process():
        try:
            while bg_proc.is_running:
                output = bg_proc.get_new_output()
                if output:
                    await websocket.send_text(output)
                await asyncio.sleep(0.05)
            final_output = bg_proc.get_new_output()
            if final_output:
                await websocket.send_text(final_output)
        finally:
            await websocket.close()

    async def write_to_process():
        try:
            while True:
                data = await websocket.receive_text()
                bg_proc.write_input(data)
        except Exception:
            return

    await asyncio.gather(read_from_process(), write_to_process())
