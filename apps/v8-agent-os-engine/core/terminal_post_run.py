from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from core.database import db
from core.hooks_manager import hooks_manager
from core.memory_extraction_policy import MEMORY_EXTRACTION_MODE_AUTO, resolve_memory_extraction_mode
from core.storage import storage
from erc.run_service import run_service


logger = logging.getLogger("v8chat.terminal_post_run")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TerminalPostRunService:
    def __init__(self):
        self._lock = threading.RLock()
        self._dispatched_keys: set[str] = set()
        self._proof_dispatched_keys: set[str] = set()

    def dispatch(self, *, session_id: str, run_id: str, source_component: str) -> bool:
        if not session_id or not run_id:
            return False

        run_record = db.get_run_record(run_id)
        if not run_record:
            return False
        if run_record.get("status") not in {"completed", "failed", "cancelled"}:
            return False

        dispatch_key = f"{session_id}:{run_id}"
        self._finalize_workflow_guides(
            session_id=session_id,
            run_id=run_id,
            run_status=str(run_record.get("status") or ""),
        )
        self._schedule_engineering_proof_if_needed(
            session_id=session_id,
            run_id=run_id,
            source_component=source_component,
            dispatch_key=dispatch_key,
            run_metadata=dict(run_record.get("metadata") or {}),
        )
        with self._lock:
            metadata = dict(run_record.get("metadata") or {})
            if metadata.get("memory_terminal_dispatched") or dispatch_key in self._dispatched_keys:
                return False
            self._dispatched_keys.add(dispatch_key)
            try:
                run_service.update_metadata(
                    run_id,
                    {
                        "memory_terminal_dispatched": True,
                        "memory_terminal_source": source_component,
                        "memory_terminal_dispatched_at": _utc_now_iso(),
                    },
                )
            except Exception as exc:
                logger.warning("Failed to persist terminal memory dispatch marker for %s: %s", run_id, exc)

        self._schedule_memory_extraction(session_id=session_id, run_id=run_id, source_component=source_component)
        self._run_non_memory_hooks(session_id=session_id, run_id=run_id)
        return True

    @staticmethod
    def _finalize_workflow_guides(*, session_id: str, run_id: str, run_status: str) -> None:
        try:
            from runtimes.memory.workflow_service import workflow_memory_service

            workflow_memory_service.finalize_guides_for_run(
                session_id=session_id,
                run_id=run_id,
                run_status=run_status,
            )
        except Exception as exc:
            logger.warning("Workflow guide finalization failed for run %s: %s", run_id, exc)

    def _schedule_engineering_proof_if_needed(
        self,
        *,
        session_id: str,
        run_id: str,
        source_component: str,
        dispatch_key: str,
        run_metadata: dict,
    ) -> bool:
        try:
            engineering_config = storage.get_engineering_lane_config() or {}
            if not bool(engineering_config.get("enabled", True)):
                return False
            if not bool(engineering_config.get("proofLedgerEnabled", True)):
                return False
            if not bool(engineering_config.get("autoProofCollectionEnabled", True)):
                return False
        except Exception as exc:
            logger.warning("Failed to read Engineering Runtime config for run %s: %s", run_id, exc)
            return False

        with self._lock:
            if run_metadata.get("engineering_proof_terminal_dispatched") or dispatch_key in self._proof_dispatched_keys:
                return False
            self._proof_dispatched_keys.add(dispatch_key)
            try:
                run_service.update_metadata(
                    run_id,
                    {
                        "engineering_proof_terminal_dispatched": True,
                        "engineering_proof_terminal_source": source_component,
                        "engineering_proof_terminal_dispatched_at": _utc_now_iso(),
                    },
                )
            except Exception as exc:
                logger.warning("Failed to persist terminal engineering proof marker for %s: %s", run_id, exc)

        def _worker():
            try:
                from runtimes.engineering.service import engineering_lane_service

                result = engineering_lane_service.collect_terminal_proof(
                    session_id=session_id,
                    run_id=run_id,
                    source_component=source_component,
                )
                logger.info("Engineering proof terminal collection result for run %s: %s", run_id, result.get("status"))
            except Exception as exc:
                logger.exception("Engineering proof collection failed for session %s run %s: %s", session_id, run_id, exc)

        threading.Thread(
            target=_worker,
            name=f"engineering-proof-{run_id[:12]}",
            daemon=True,
        ).start()
        return True

    def _schedule_memory_extraction(self, *, session_id: str, run_id: str, source_component: str) -> None:
        from agents.runners.maintenance_runner import memory_agent_runner

        memory_config = storage.get_memory_config() or {}
        if resolve_memory_extraction_mode(memory_config) != MEMORY_EXTRACTION_MODE_AUTO:
            logger.info("Memory extraction is in manual mode; terminal scheduler skipped for session %s", session_id)
            return

        def _worker():
            try:
                memory_agent_runner.run_session_extraction(
                    session_id=session_id,
                    trigger_source=f"terminal:{source_component}",
                    parent_run_id=run_id,
                )
            except Exception as exc:
                logger.exception("Memory extraction scheduling failed for session %s run %s: %s", session_id, run_id, exc)

        threading.Thread(
            target=_worker,
            name=f"memory-terminal-{run_id[:12]}",
            daemon=True,
        ).start()

    def _run_non_memory_hooks(self, *, session_id: str, run_id: str) -> None:
        try:
            hooks_manager.execute_hook(
                "on_chat_end",
                session_id=session_id,
                parent_run_id=run_id,
                exclude_targets=["agents.memory_agent"],
                exclude_names=["Memory Agent System"],
            )
        except Exception as exc:
            logger.warning("Non-memory on_chat_end hooks failed for session %s: %s", session_id, exc)


terminal_post_run_service = TerminalPostRunService()
