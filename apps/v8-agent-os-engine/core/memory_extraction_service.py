from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

from core.database import db
from core.memory_extraction_policy import memory_extraction_runtime_session_id
from core.realtime_protocol import build_runtime_event


logger = logging.getLogger("v8chat.memory_extraction_service")

_ACTIVE_CHAT_RUN_STATUSES = {
    "queued",
    "running",
    "waiting_approval",
    "waiting_input",
    "waiting_external_tool",
    "paused",
}


class MemoryExtractionService:
    def __init__(self) -> None:
        self._scheduled_sessions: set[str] = set()
        self._scheduled_sessions_lock = threading.Lock()

    @staticmethod
    def _latest_terminal_run_id(session_id: str) -> str | None:
        for run in db.list_run_records(session_id=session_id, limit=20):
            if str(run.get("status") or "").strip().lower() in {"completed", "failed", "cancelled", "interrupted"}:
                return str(run.get("id") or "").strip() or None
        return None

    @staticmethod
    def _active_chat_run(session_id: str) -> dict[str, Any] | None:
        get_lane = getattr(db, "get_session_lane_record", None)
        if callable(get_lane):
            lane = get_lane(session_id) or {}
            lane_run_id = str(lane.get("active_run_id") or "").strip()
            if lane_run_id:
                record = db.get_run_record(lane_run_id)
                if record and str(record.get("status") or "").strip().lower() in _ACTIVE_CHAT_RUN_STATUSES:
                    return record
        for run in db.list_run_records(session_id=session_id, run_type="chat", limit=10):
            if str(run.get("status") or "").strip().lower() in _ACTIVE_CHAT_RUN_STATUSES:
                return run
        return None

    @staticmethod
    def _active_memory_run(session_id: str) -> dict[str, Any] | None:
        runtime_session_id = memory_extraction_runtime_session_id(session_id)
        get_lane = getattr(db, "get_session_lane_record", None)
        if callable(get_lane):
            lane = get_lane(runtime_session_id) or {}
            lane_run_id = str(lane.get("active_run_id") or "").strip()
            if lane_run_id:
                record = db.get_run_record(lane_run_id)
                if record and str(record.get("status") or "").strip().lower() in _ACTIVE_CHAT_RUN_STATUSES:
                    return record
        for run in db.list_run_records(session_id=runtime_session_id, run_type="memory", limit=10):
            if str(run.get("status") or "").strip().lower() in _ACTIVE_CHAT_RUN_STATUSES:
                return run
        return None

    @staticmethod
    def _emit(
        *,
        session_id: str,
        parent_run_id: str | None,
        topic: str,
        payload: dict[str, Any],
    ) -> None:
        db.add_runtime_event(
            build_runtime_event(
                kind="event",
                topic=topic,
                session_id=session_id,
                run_id=parent_run_id,
                source={
                    "plane": "engine",
                    "component": "memory_extraction_service",
                    "node": "manual_session_extraction",
                    "agent_id": "memory_agent",
                },
                payload=payload,
            )
        )

    def schedule_manual(
        self,
        *,
        session_id: str,
        user_id: str = "system",
        trigger_source: str = "manual:composer",
    ) -> dict[str, Any]:
        active_run = self._active_chat_run(session_id)
        if active_run:
            return {
                "accepted": False,
                "status": "busy",
                "reason": "chat_run_active",
                "summary": "当前任务仍在运行，请在本轮结束后再整理记忆。",
            }

        if self._active_memory_run(session_id):
            return {
                "accepted": False,
                "status": "busy",
                "reason": "memory_extraction_active",
                "summary": "当前任务的记忆正在整理，请稍后查看结果。",
            }

        with self._scheduled_sessions_lock:
            if session_id in self._scheduled_sessions:
                return {
                    "accepted": False,
                    "status": "busy",
                    "reason": "memory_extraction_active",
                    "summary": "当前任务的记忆正在整理，请稍后查看结果。",
                }
            self._scheduled_sessions.add(session_id)

        request_id = f"memory_manual_{uuid.uuid4().hex[:16]}"
        parent_run_id = self._latest_terminal_run_id(session_id)
        queued_payload = {
            "requestId": request_id,
            "status": "queued",
            "summary": "记忆整理已开始，Memory Agent 会读取当前任务的完整会话并更新长期记忆。",
            "triggerSource": trigger_source,
        }
        self._emit(
            session_id=session_id,
            parent_run_id=parent_run_id,
            topic="memory.extraction.manual.queued",
            payload=queued_payload,
        )

        def _worker() -> None:
            try:
                from agents.runners.maintenance_runner import memory_agent_runner

                result = memory_agent_runner.run_session_extraction(
                    session_id=session_id,
                    trigger_source=trigger_source,
                    parent_run_id=parent_run_id,
                    user_id=user_id or "system",
                    manual=True,
                )
                memory_result = dict(result.get("result") or {})
                status = str(memory_result.get("status") or "completed").strip().lower()
                terminal_topic = (
                    "memory.extraction.manual.failed"
                    if status in {"failed", "rejected"}
                    else "memory.extraction.manual.completed"
                )
                summary = (
                    "记忆整理未完成，请稍后重试。"
                    if terminal_topic.endswith("failed")
                    else "记忆整理已完成。"
                )
                self._emit(
                    session_id=session_id,
                    parent_run_id=parent_run_id,
                    topic=terminal_topic,
                    payload={
                        "requestId": request_id,
                        "status": status,
                        "summary": summary,
                        "preferenceCount": memory_result.get("persisted_preference_count") or memory_result.get("preference_count"),
                        "knowledgeCount": memory_result.get("persisted_knowledge_count") or memory_result.get("knowledge_count"),
                    },
                )
            except Exception as exc:
                logger.exception("Manual memory extraction failed for session %s: %s", session_id, exc)
                self._emit(
                    session_id=session_id,
                    parent_run_id=parent_run_id,
                    topic="memory.extraction.manual.failed",
                    payload={
                        "requestId": request_id,
                        "status": "failed",
                        "summary": "记忆整理未完成，请稍后重试。",
                        "errorCode": "manual_memory_extraction_failed",
                    },
                )
            finally:
                with self._scheduled_sessions_lock:
                    self._scheduled_sessions.discard(session_id)

        try:
            threading.Thread(
                target=_worker,
                name=f"memory-manual-{request_id[-8:]}",
                daemon=True,
            ).start()
        except Exception:
            with self._scheduled_sessions_lock:
                self._scheduled_sessions.discard(session_id)
            raise
        return {
            "accepted": True,
            "status": "queued",
            "requestId": request_id,
            "summary": queued_payload["summary"],
        }


memory_extraction_service = MemoryExtractionService()
