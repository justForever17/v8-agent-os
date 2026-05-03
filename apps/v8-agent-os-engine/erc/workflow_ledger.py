from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.database import db
from core.realtime_protocol import utc_now_iso
from erc.event_bus import event_bus
from erc.models import RuntimeSource


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowLedgerService:
    """
    Phase 1 止血版 Workflow Ledger 服务。

    目标不是一步到位实现完整 workflow engine，而是先保证：
    1. run 一旦开始，流程实体就存在
    2. run 中断/失败后，当前步骤和恢复线索仍然存在
    3. assistant 中间输出可以挂到 durable step projection 上
    """

    def workflow_id_for_run(self, run_id: str) -> str:
        return f"wf_{run_id}"

    def main_step_id_for_run(self, run_id: str) -> str:
        return f"step_{run_id}_main"

    def _step_slug(self, value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9._:-]+", "_", (value or "step").strip()).strip("_")
        return normalized or "step"

    def _next_sequence_index(self, workflow_id: str) -> int:
        steps = db.get_workflow_steps(workflow_id)
        if not steps:
            return 0
        return max(int(step.get("sequence_index") or 0) for step in steps) + 1

    def ensure_workflow_for_run(
        self,
        *,
        run_id: str,
        session_id: str,
        conversation_id: Optional[str],
        runtime_kind: str,
        agent_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        workflow_id = self.workflow_id_for_run(run_id)
        step_id = self.main_step_id_for_run(run_id)

        if db.get_workflow_ledger(workflow_id) is None:
            db.create_workflow_ledger(
                workflow_id=workflow_id,
                session_id=session_id,
                conversation_id=conversation_id or session_id,
                root_run_id=run_id,
                workflow_kind=runtime_kind,
                status="created",
                owner_runtime=runtime_kind,
                owner_agent_id=agent_id,
                current_step_id=step_id,
                resume_strategy="replay_from_step",
                recoverable=True,
                metadata={"createdBy": runtime_kind, **(metadata or {})},
            )

        db.upsert_workflow_step(
            step_id=step_id,
            workflow_id=workflow_id,
            session_id=session_id,
            run_id=run_id,
            sequence_index=0,
            step_key=f"{runtime_kind}.main",
            title=f"{runtime_kind} 主流程",
            status="queued",
            owner_runtime=runtime_kind,
            owner_agent_id=agent_id,
            input_payload={"createdAt": utc_now_iso(), **(metadata or {})},
        )
        return {"workflow_id": workflow_id, "step_id": step_id}

    def mark_run_started(self, run_id: str, *, reason: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        workflow = db.get_workflow_ledger_for_run(run_id)
        if not workflow:
            return
        step_id = workflow.get("current_step_id") or self.main_step_id_for_run(run_id)
        db.update_workflow_ledger(
            workflow["id"],
            status="running",
            current_step_id=step_id,
            recoverable=True,
            metadata={"lastStartReason": reason, **(metadata or {})},
            clear_error=True,
        )
        step = db.get_workflow_step(step_id)
        if step:
            db.upsert_workflow_step(
                step_id=step_id,
                workflow_id=workflow["id"],
                session_id=step["session_id"],
                run_id=run_id,
                sequence_index=step.get("sequence_index") or 0,
                step_key=step["step_key"],
                title=step["title"],
                status="running",
                owner_runtime=step.get("owner_runtime"),
                owner_agent_id=step.get("owner_agent_id"),
                input_payload=step.get("input"),
                output_payload=step.get("output"),
                projection_payload=step.get("projection"),
                last_event_seq=step.get("last_event_seq"),
                retry_count=step.get("retry_count"),
                resume_token=step.get("resume_token"),
                clear_error=True,
            )

    def sync_run_status(
        self,
        run_id: str,
        *,
        run_status: str,
        reason: Optional[str] = None,
        approval_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        workflow = db.get_workflow_ledger_for_run(run_id)
        if not workflow:
            return

        workflow_status = {
            "queued": "created",
            "running": "running",
            "waiting_approval": "waiting_approval",
            "waiting_input": "recoverable_failed",
            "waiting_external_tool": "waiting_external_tool",
            "paused": "paused",
            "interrupted": "recoverable_failed",
            "completed": "completed",
            "failed": "recoverable_failed",
            "cancelled": "cancelled",
            "abandoned": "abandoned",
        }.get(run_status, run_status)

        recoverable = workflow_status not in {"completed", "cancelled", "abandoned"}
        step_status = {
            "waiting_approval": "waiting_approval",
            "waiting_external_tool": "waiting_external_tool",
            "paused": "paused",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "abandoned": "abandoned",
            "interrupted": "interrupted",
            "waiting_input": "repair_pending",
        }.get(run_status, "running")

        db.update_workflow_ledger(
            workflow["id"],
            status=workflow_status,
            resume_strategy="replay_from_step" if recoverable else workflow.get("resume_strategy"),
            recoverable=recoverable,
            last_error_message=reason if workflow_status in {"recoverable_failed", "failed"} else None,
            metadata={"lastRunStatus": run_status, **(metadata or {})},
            clear_error=workflow_status not in {"recoverable_failed", "failed"},
        )

        step_id = workflow.get("current_step_id") or self.main_step_id_for_run(run_id)
        step = db.get_workflow_step(step_id)
        if not step:
            return
        db.upsert_workflow_step(
            step_id=step_id,
            workflow_id=workflow["id"],
            session_id=step["session_id"],
            run_id=run_id,
            sequence_index=step.get("sequence_index") or 0,
            step_key=step["step_key"],
            title=step["title"],
            status=step_status,
            owner_runtime=step.get("owner_runtime"),
            owner_agent_id=step.get("owner_agent_id"),
            approval_id=approval_id or step.get("approval_id"),
            input_payload=step.get("input"),
            output_payload=step.get("output"),
            projection_payload=step.get("projection"),
            last_event_seq=step.get("last_event_seq"),
            retry_count=step.get("retry_count"),
            resume_token=step.get("resume_token"),
            last_error_message=reason if step_status in {"failed", "interrupted", "repair_pending"} else None,
            clear_error=step_status not in {"failed", "interrupted", "repair_pending"},
        )

    def bind_approval(
        self,
        run_id: str,
        *,
        approval_id: str,
        approval_kind: str,
        approval_status: str = "pending",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        workflow = db.get_workflow_ledger_for_run(run_id)
        if not workflow:
            return
        step_id = workflow.get("current_step_id") or self.main_step_id_for_run(run_id)
        step = db.get_workflow_step(step_id)
        if not step:
            return
        normalized_status = str(approval_status or "pending").strip().lower()
        if normalized_status != "pending":
            workflow_metadata = {
                "approvalKind": approval_kind,
                "approvalStatus": normalized_status,
                **(metadata or {}),
            }
            db.update_workflow_ledger(
                workflow["id"],
                recoverable=True,
                metadata=workflow_metadata,
            )
            db.upsert_workflow_step(
                step_id=step_id,
                workflow_id=workflow["id"],
                session_id=step["session_id"],
                run_id=run_id,
                sequence_index=step.get("sequence_index") or 0,
                step_key=step["step_key"],
                title=step["title"],
                status=step["status"],
                owner_runtime=step.get("owner_runtime"),
                owner_agent_id=step.get("owner_agent_id"),
                approval_id=approval_id,
                input_payload=step.get("input"),
                output_payload=step.get("output"),
                projection_payload=step.get("projection"),
                last_event_seq=step.get("last_event_seq"),
                retry_count=step.get("retry_count"),
                resume_token=step.get("resume_token"),
            )
            return
        db.update_workflow_ledger(
            workflow["id"],
            status="waiting_approval",
            recoverable=True,
            metadata={"approvalKind": approval_kind, **(metadata or {})},
        )
        db.upsert_workflow_step(
            step_id=step_id,
            workflow_id=workflow["id"],
            session_id=step["session_id"],
            run_id=run_id,
            sequence_index=step.get("sequence_index") or 0,
            step_key=step["step_key"],
            title=step["title"],
            status="waiting_approval",
            owner_runtime=step.get("owner_runtime"),
            owner_agent_id=step.get("owner_agent_id"),
            approval_id=approval_id,
            input_payload=step.get("input"),
            output_payload=step.get("output"),
            projection_payload=step.get("projection"),
            last_event_seq=step.get("last_event_seq"),
            retry_count=step.get("retry_count"),
            resume_token=step.get("resume_token") or approval_id,
        )

    def record_step_inputs(self, run_id: str, *, inputs: Dict[str, Any]) -> None:
        workflow = db.get_workflow_ledger_for_run(run_id)
        if not workflow:
            return
        step_id = workflow.get("current_step_id") or self.main_step_id_for_run(run_id)
        step = db.get_workflow_step(step_id)
        if not step:
            return
        merged_input = dict(step.get("input") or {})
        merged_input.update(inputs or {})
        db.upsert_workflow_step(
            step_id=step_id,
            workflow_id=workflow["id"],
            session_id=step["session_id"],
            run_id=run_id,
            sequence_index=step.get("sequence_index") or 0,
            step_key=step["step_key"],
            title=step["title"],
            status=step["status"],
            owner_runtime=step.get("owner_runtime"),
            owner_agent_id=step.get("owner_agent_id"),
            approval_id=step.get("approval_id"),
            input_payload=merged_input,
            output_payload=step.get("output"),
            projection_payload=step.get("projection"),
            last_event_seq=step.get("last_event_seq"),
            retry_count=step.get("retry_count"),
            resume_token=step.get("resume_token"),
        )

    def activate_runtime_step(
        self,
        run_id: str,
        *,
        owner_runtime: str,
        step_key: str,
        title: str,
        owner_agent_id: Optional[str] = None,
        input_payload: Optional[Dict[str, Any]] = None,
        projection_payload: Optional[Dict[str, Any]] = None,
        status: str = "running",
        resume_token: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        workflow = db.get_workflow_ledger_for_run(run_id)
        if not workflow:
            return None

        current_step = db.get_workflow_step(workflow.get("current_step_id") or "")
        reusing_current_step = (
            current_step
            and current_step.get("step_key") == step_key
            and current_step.get("owner_runtime") == owner_runtime
        )
        if reusing_current_step:
            step_id = str(current_step["id"])
            sequence_index = int(current_step.get("sequence_index") or 0)
            merged_input = dict(current_step.get("input") or {})
            merged_input.update(input_payload or {})
            merged_projection = dict(current_step.get("projection") or {})
            merged_projection.update(projection_payload or {})
        else:
            if current_step:
                previous_status = str(current_step.get("status") or "").strip().lower()
                if previous_status not in {"completed", "failed", "cancelled", "interrupted"}:
                    db.upsert_workflow_step(
                        step_id=str(current_step["id"]),
                        workflow_id=workflow["id"],
                        session_id=current_step["session_id"],
                        run_id=run_id,
                        sequence_index=int(current_step.get("sequence_index") or 0),
                        step_key=current_step["step_key"],
                        title=current_step["title"],
                        status="completed",
                        owner_runtime=current_step.get("owner_runtime"),
                        owner_agent_id=current_step.get("owner_agent_id"),
                        approval_id=current_step.get("approval_id"),
                        input_payload=current_step.get("input"),
                        output_payload=current_step.get("output"),
                        projection_payload=current_step.get("projection"),
                        last_event_seq=current_step.get("last_event_seq"),
                        retry_count=current_step.get("retry_count"),
                        resume_token=current_step.get("resume_token"),
                        last_error_message=None,
                        clear_error=True,
                    )
            sequence_index = self._next_sequence_index(workflow["id"])
            step_id = f"step_{run_id}_{sequence_index}_{self._step_slug(step_key)}"
            merged_input = dict(input_payload or {})
            merged_projection = dict(projection_payload or {})

        db.upsert_workflow_step(
            step_id=step_id,
            workflow_id=workflow["id"],
            session_id=workflow["session_id"],
            run_id=run_id,
            sequence_index=sequence_index,
            step_key=step_key,
            title=title,
            status=status,
            owner_runtime=owner_runtime,
            owner_agent_id=owner_agent_id,
            approval_id=current_step.get("approval_id") if reusing_current_step and current_step else None,
            input_payload=merged_input,
            output_payload=current_step.get("output") if reusing_current_step and current_step else None,
            projection_payload=merged_projection,
            last_event_seq=current_step.get("last_event_seq") if reusing_current_step and current_step else None,
            retry_count=current_step.get("retry_count") if reusing_current_step and current_step else None,
            resume_token=resume_token or (current_step.get("resume_token") if reusing_current_step and current_step else None),
            last_error_message=None,
            clear_error=True,
        )
        db.update_workflow_ledger(
            workflow["id"],
            status="running" if status == "running" else workflow.get("status"),
            owner_runtime=owner_runtime,
            owner_agent_id=owner_agent_id,
            current_step_id=step_id,
            recoverable=True,
            metadata={
                "lastRuntimeHandoff": {
                    "runtime": owner_runtime,
                    "step_key": step_key,
                    "title": title,
                    "at": utc_now_iso(),
                }
            },
            clear_error=status not in {"failed", "interrupted", "repair_pending"},
        )
        return {"workflow_id": workflow["id"], "step_id": step_id}

    def append_chat_projection(
        self,
        *,
        session_id: str,
        run_id: str,
        text_delta: str = "",
        reasoning_delta: str = "",
        agent_profile: Optional[Dict[str, Any]] = None,
        latest_seq: Optional[int] = None,
    ) -> None:
        workflow = db.get_workflow_ledger_for_run(run_id)
        if not workflow:
            return
        step_id = workflow.get("current_step_id") or self.main_step_id_for_run(run_id)
        step = db.get_workflow_step(step_id)
        if not step:
            return

        projection = dict(step.get("projection") or {})
        preview = dict(projection.get("assistant_preview") or {})
        preview.setdefault("id", f"preview_{run_id}")
        preview.setdefault("role", "assistant")
        preview.setdefault("runId", run_id)
        preview.setdefault("content", "")
        preview.setdefault("reasoningContent", "")
        preview.setdefault("timestamp", 0)
        if text_delta:
            preview["content"] = f"{preview.get('content', '')}{text_delta}"
        if reasoning_delta:
            preview["reasoningContent"] = f"{preview.get('reasoningContent', '')}{reasoning_delta}"
        if agent_profile:
            preview.update(
                {
                    "agentName": agent_profile.get("name") or preview.get("agentName") or "智能主管",
                    "agentAvatar": agent_profile.get("avatar") or preview.get("agentAvatar"),
                    "agentRoleLabel": agent_profile.get("roleLabel") or preview.get("agentRoleLabel") or "主理人",
                }
            )
        preview["updatedAt"] = _now_iso()
        projection["assistant_preview"] = preview

        db.upsert_workflow_step(
            step_id=step_id,
            workflow_id=workflow["id"],
            session_id=session_id,
            run_id=run_id,
            sequence_index=step.get("sequence_index") or 0,
            step_key=step["step_key"],
            title=step["title"],
            status=step["status"],
            owner_runtime=step.get("owner_runtime"),
            owner_agent_id=step.get("owner_agent_id"),
            approval_id=step.get("approval_id"),
            input_payload=step.get("input"),
            output_payload=step.get("output"),
            projection_payload=projection,
            last_event_seq=latest_seq if latest_seq is not None else step.get("last_event_seq"),
            retry_count=step.get("retry_count"),
            resume_token=step.get("resume_token"),
        )

    def clear_chat_projection(self, run_id: str) -> None:
        workflow = db.get_workflow_ledger_for_run(run_id)
        if not workflow:
            return
        step_id = workflow.get("current_step_id") or self.main_step_id_for_run(run_id)
        step = db.get_workflow_step(step_id)
        if not step:
            return
        projection = dict(step.get("projection") or {})
        if "assistant_preview" not in projection:
            return
        projection.pop("assistant_preview", None)
        db.upsert_workflow_step(
            step_id=step_id,
            workflow_id=workflow["id"],
            session_id=step["session_id"],
            run_id=run_id,
            sequence_index=step.get("sequence_index") or 0,
            step_key=step["step_key"],
            title=step["title"],
            status=step["status"],
            owner_runtime=step.get("owner_runtime"),
            owner_agent_id=step.get("owner_agent_id"),
            approval_id=step.get("approval_id"),
            input_payload=step.get("input"),
            output_payload=step.get("output"),
            projection_payload=projection,
            last_event_seq=step.get("last_event_seq"),
            retry_count=step.get("retry_count"),
            resume_token=step.get("resume_token"),
        )

    def get_session_projection_overlay(self, session_id: str) -> Optional[Dict[str, Any]]:
        step = db.get_active_workflow_projection(session_id)
        if not step:
            return None
        projection = step.get("projection") or {}
        preview = projection.get("assistant_preview")
        if not isinstance(preview, dict):
            return None
        if not preview.get("content") and not preview.get("reasoningContent"):
            return None
        return {
            "workflowId": step.get("workflow_id"),
            "stepId": step.get("id"),
            "lastEventSeq": step.get("last_event_seq") or 0,
            "assistantPreview": preview,
        }

    def get_session_workflow_view(self, session_id: str) -> Optional[Dict[str, Any]]:
        workflow = db.get_latest_workflow_for_session(session_id)
        if not workflow:
            return None
        steps = db.get_workflow_steps(str(workflow["id"]))
        current_step = next((step for step in steps if step["id"] == workflow.get("current_step_id")), None)
        history = [
            {
                "stepId": step.get("id"),
                "sequenceIndex": int(step.get("sequence_index") or 0),
                "stepKey": step.get("step_key"),
                "title": step.get("title"),
                "status": step.get("status"),
                "ownerRuntime": step.get("owner_runtime"),
                "ownerAgentId": step.get("owner_agent_id"),
                "updatedAt": step.get("updated_at"),
            }
            for step in steps[-8:]
        ]
        return {
            "workflowId": workflow.get("id"),
            "rootRunId": workflow.get("root_run_id"),
            "workflowKind": workflow.get("workflow_kind"),
            "status": workflow.get("status"),
            "recoverable": bool(workflow.get("recoverable")),
            "ownerRuntime": (current_step or {}).get("owner_runtime") or workflow.get("owner_runtime"),
            "ownerAgentId": (current_step or {}).get("owner_agent_id") or workflow.get("owner_agent_id"),
            "currentStepId": workflow.get("current_step_id"),
            "currentStepKey": (current_step or {}).get("step_key"),
            "currentStepTitle": (current_step or {}).get("title"),
            "currentStepStatus": (current_step or {}).get("status"),
            "runtimeHistory": history,
            "updatedAt": workflow.get("updated_at"),
        }

    def emit_reconciliation_event(self, session_id: str, run_id: str, *, reason: str, outcome: str) -> None:
        emitter = event_bus.create_emitter(
            session_id=session_id,
            conversation_id=session_id,
            run_id=run_id,
            source=RuntimeSource(
                plane="engine",
                component="workflow_ledger",
                node="orphan_reconciler",
                agent_id=None,
            ),
        )
        emitter.emit(
            "run.reconciled",
            {"reason": reason, "outcome": outcome, "run_id": run_id},
        )

    def reconcile_orphaned_runs(self) -> Dict[str, int]:
        updated = 0
        reviewed = 0
        for run_record in db.list_active_run_records():
            reviewed += 1
            run_id = run_record["id"]
            status = run_record.get("status") or "queued"
            session_id = run_record["session_id"]
            if status in {"waiting_approval", "paused", "waiting_input", "waiting_external_tool"}:
                workflow = db.get_workflow_ledger_for_run(run_id)
                if workflow:
                    db.update_workflow_ledger(
                        workflow["id"],
                        status=status if status != "waiting_input" else "recoverable_failed",
                        recoverable=True,
                        metadata={
                            "reconciledAt": utc_now_iso(),
                            "reconciledReason": "engine_restart",
                        },
                    )
                continue

            if status not in {"queued", "running"}:
                continue

            metadata = dict(run_record.get("metadata") or {})
            metadata.update(
                {
                    "orphaned": True,
                    "orphaned_at": utc_now_iso(),
                    "resume_strategy": "replay_from_step",
                }
            )
            db.update_run_record(run_id, status="interrupted", metadata=metadata)
            self.sync_run_status(
                run_id,
                run_status="interrupted",
                reason="engine_restart_orphan_reconciliation",
                metadata={
                    "reconciledAt": utc_now_iso(),
                    "reconciledReason": "engine_restart",
                },
            )
            self.emit_reconciliation_event(
                session_id,
                run_id,
                reason="engine_restart_orphan_reconciliation",
                outcome="interrupted_recoverable",
            )
            updated += 1

        return {"reviewed": reviewed, "updated": updated}


workflow_ledger_service = WorkflowLedgerService()
