from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.database import db
from erc.command_service import command_service
from erc.event_bus import event_bus, SessionEventEmitter
from erc.models import ApprovalRequest, RunDescriptor, RuntimeSource
from erc.run_service import run_service
from erc.snapshot_service import snapshot_service
from erc.workflow_ledger import workflow_ledger_service


@dataclass(slots=True)
class RunHandle:
    descriptor: RunDescriptor
    emitter: SessionEventEmitter

    @property
    def run_id(self) -> str:
        return self.descriptor.run_id

    @property
    def session_id(self) -> str:
        return self.descriptor.session_id

    def emit(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        kind: str = "event",
        source: Optional[RuntimeSource] = None,
    ) -> Dict[str, Any]:
        return self.emitter.emit(topic, payload, kind=kind, source=source)

    def transition(self, to_status: str, *, reason: str, node: str = "run_manager") -> Dict[str, Any]:
        previous = self.descriptor.status
        self.descriptor.status = to_status
        run_service.transition_run(self.run_id, status=to_status)
        workflow_ledger_service.sync_run_status(
            self.run_id,
            run_status=to_status,
            reason=reason,
            metadata={"transitionNode": node, "previousStatus": previous},
        )
        return self.emit(
            "run.state.changed",
            {"from_status": previous, "to_status": to_status, "reason": reason},
            source=RuntimeSource(
                plane="engine",
                component="erc",
                node=node,
                agent_id=self.descriptor.agent_id,
            ),
        )

    def refresh_chat_snapshot(self) -> Dict[str, Any]:
        return snapshot_service.refresh_chat_projection(self.session_id, run_id=self.run_id)

    def complete(self, *, reason: str = "completed", node: str = "run_manager") -> Dict[str, Any]:
        self.transition("completed", reason=reason, node=node)
        self.emit(
            "run.completed",
            {"status": "finished", "reason": reason},
            source=RuntimeSource(plane="engine", component="erc", node=node, agent_id=self.descriptor.agent_id),
        )
        run_service.transition_run(self.run_id, status="completed")
        return self.refresh_chat_snapshot()

    def fail(self, error_message: str, *, node: str = "run_manager") -> Dict[str, Any]:
        self.transition("failed", reason=error_message, node=node)
        self.emit(
            "run.failed",
            {"error": error_message},
            source=RuntimeSource(plane="engine", component="erc", node=node, agent_id=self.descriptor.agent_id),
        )
        run_service.transition_run(self.run_id, status="failed", error_message=error_message)
        return self.refresh_chat_snapshot()

    def request_approval(
        self,
        *,
        approval_kind: str,
        request: Dict[str, Any],
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        if str(approval_kind or "").strip().lower() == "ask_user":
            raise ValueError("ask_user must use ask_user_interactions, not pending_approvals")
        approval = command_service.request_approval(
            ApprovalRequest(
                approval_id=f"approval_{uuid.uuid4().hex}",
                session_id=self.session_id,
                run_id=self.run_id,
                approval_kind=approval_kind,
                request=request,
                expires_at=expires_at,
            )
        )
        workflow_ledger_service.bind_approval(
            self.run_id,
            approval_id=approval["approval_id"],
            approval_kind=approval_kind,
            approval_status=approval.get("status") or "pending",
            metadata={
                "policySource": approval.get("policySource"),
                "autoApproved": bool(approval.get("autoApproved")),
            },
        )
        self.emit(
            "approval.requested",
            approval,
            source=RuntimeSource(plane="engine", component="erc", node="command_service", agent_id=self.descriptor.agent_id),
        )
        if str(approval.get("status") or "").strip().lower() == "pending":
            self.transition("waiting_approval", reason=approval_kind, node="command_service")
            run_service.transition_run(self.run_id, status="waiting_approval")
        else:
            approval_event_payload = {
                "approval_id": approval["approval_id"],
                "run_id": self.run_id,
                "approval_kind": approval_kind,
                "response": approval.get("response") or {},
                "policySource": approval.get("policySource"),
            }
            self.emit(
                "approval.auto_approved",
                approval_event_payload,
                source=RuntimeSource(
                    plane="engine",
                    component="erc",
                    node="command_service",
                    agent_id=self.descriptor.agent_id,
                ),
            )
            self.emit(
                "approval.approved",
                approval_event_payload,
                source=RuntimeSource(
                    plane="engine",
                    component="erc",
                    node="command_service",
                    agent_id=self.descriptor.agent_id,
                ),
            )
        return approval

    def request_ask_user_interaction(
        self,
        *,
        request: Dict[str, Any],
        assistant_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        interaction_id = f"ask_{uuid.uuid4().hex}"
        tool_call_id = str(request.get("toolCallId") or "").strip() or None
        question = str(request.get("question") or request.get("prompt") or "").strip() or None
        prompt = str(request.get("prompt") or question or "").strip() or None
        db.add_ask_user_interaction(
            interaction_id=interaction_id,
            session_id=self.session_id,
            run_id=self.run_id,
            assistant_message_id=assistant_message_id,
            tool_call_id=tool_call_id,
            question=question,
            prompt=prompt,
            request=request,
            status="pending",
        )
        interaction = db.get_ask_user_interaction(interaction_id) or {
            "id": interaction_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "assistant_message_id": assistant_message_id,
            "tool_call_id": tool_call_id,
            "question": question,
            "prompt": prompt,
            "request": dict(request or {}),
            "status": "pending",
        }
        payload = {
            "id": interaction.get("id"),
            "interactionId": interaction.get("id"),
            "session_id": interaction.get("session_id"),
            "sessionId": interaction.get("session_id"),
            "run_id": interaction.get("run_id"),
            "runId": interaction.get("run_id"),
            "assistant_message_id": interaction.get("assistant_message_id"),
            "assistantMessageId": interaction.get("assistant_message_id"),
            "toolCallId": interaction.get("tool_call_id"),
            "question": interaction.get("question"),
            "prompt": interaction.get("prompt"),
            "interactionKind": "ask_user",
            "status": interaction.get("status") or "pending",
            "request": interaction.get("request") or {},
            "createdAt": interaction.get("created_at"),
        }
        self.emit(
            "ask_user.requested",
            payload,
            source=RuntimeSource(
                plane="engine",
                component="erc",
                node="command_service",
                agent_id=self.descriptor.agent_id,
            ),
        )
        self.transition("waiting_input", reason="ask_user", node="command_service")
        run_service.transition_run(self.run_id, status="waiting_input")
        return interaction


class ExecutionRuntimeCore:
    def submit_run(
        self,
        *,
        session_id: str,
        conversation_id: Optional[str],
        user_id: str,
        runtime_kind: str,
        trigger_source: Optional[str],
        agent_id: Optional[str],
        workflow_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        channel_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        initial_status: str = "queued",
        component: str = "erc",
        node: str = "submitter",
        run_id: Optional[str] = None,
    ) -> RunHandle:
        return self.begin_run(
            session_id=session_id,
            conversation_id=conversation_id,
            user_id=user_id,
            runtime_kind=runtime_kind,
            trigger_source=trigger_source,
            agent_id=agent_id,
            workflow_id=workflow_id,
            thread_id=thread_id,
            channel_type=channel_type,
            metadata=metadata,
            initial_status=initial_status,
            component=component,
            node=node,
            run_id=run_id,
        )

    def _emitter_for_run(self, run_record: Dict[str, Any], *, component: str = "erc", node: str = "run_manager") -> SessionEventEmitter:
        return event_bus.create_emitter(
            session_id=run_record["session_id"],
            conversation_id=run_record.get("conversation_id") or run_record["session_id"],
            run_id=run_record["id"],
            source=RuntimeSource(
                plane="engine",
                component=component,
                node=node,
                agent_id=run_record.get("agent_id"),
            ),
        )

    def begin_run(
        self,
        *,
        session_id: str,
        conversation_id: Optional[str],
        user_id: str,
        runtime_kind: str,
        trigger_source: Optional[str],
        agent_id: Optional[str],
        workflow_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        channel_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        initial_status: str = "running",
        component: str = "chat_runtime",
        node: str = "run_manager",
        run_id: Optional[str] = None,
    ) -> RunHandle:
        descriptor = RunDescriptor(
            run_id=run_id or f"run_{uuid.uuid4().hex}",
            session_id=session_id,
            conversation_id=conversation_id or session_id,
            user_id=user_id,
            runtime_kind=runtime_kind,
            trigger_source=trigger_source,
            agent_id=agent_id,
            workflow_id=workflow_id,
            thread_id=thread_id,
            channel_type=channel_type,
            metadata=metadata or {},
            status=initial_status,
        )
        run_service.create_run(descriptor)
        workflow_ledger_service.ensure_workflow_for_run(
            run_id=descriptor.run_id,
            session_id=session_id,
            conversation_id=descriptor.conversation_id,
            runtime_kind=runtime_kind,
            agent_id=agent_id,
            metadata={
                "trigger_source": trigger_source,
                "channel_type": channel_type,
                "thread_id": thread_id,
                **(metadata or {}),
            },
        )
        emitter = event_bus.create_emitter(
            session_id=session_id,
            conversation_id=descriptor.conversation_id,
            run_id=descriptor.run_id,
            source=RuntimeSource(
                plane="engine",
                component=component,
                node=node,
                agent_id=agent_id,
            ),
        )
        return RunHandle(descriptor=descriptor, emitter=emitter)

    def attach_run(
        self,
        run_id: str,
        *,
        component: str = "erc",
        node: str = "run_manager",
    ) -> Optional[RunHandle]:
        run_record = run_service.get_run(run_id)
        if not run_record:
            return None
        descriptor = RunDescriptor(
            run_id=run_record["id"],
            session_id=run_record["session_id"],
            conversation_id=run_record.get("conversation_id") or run_record["session_id"],
            user_id=run_record.get("user_id") or "anonymous",
            runtime_kind=run_record.get("run_type") or "chat",
            trigger_source=run_record.get("trigger_source"),
            agent_id=run_record.get("agent_id"),
            workflow_id=run_record.get("workflow_id"),
            thread_id=run_record.get("thread_id"),
            channel_type=run_record.get("channel_type"),
            metadata=dict(run_record.get("metadata") or {}),
            status=run_record.get("status") or "queued",
        )
        emitter = self._emitter_for_run(run_record, component=component, node=node)
        return RunHandle(descriptor=descriptor, emitter=emitter)

    def pause_run(self, run_id: str, *, reason: str = "manual_pause") -> Optional[Dict[str, Any]]:
        run_record = run_service.get_run(run_id)
        if not run_record:
            return None
        emitter = self._emitter_for_run(run_record, component="erc", node="command_service")
        transition_event = emitter.emit(
            "run.state.changed",
            {"from_status": run_record.get("status"), "to_status": "paused", "reason": reason},
        )
        event = emitter.emit(
            "run.paused",
            {"run_id": run_id, "reason": reason},
        )
        command_service.pause_run(run_id, reason=reason)
        return {"transition_event": transition_event, "command_event": event}

    def resume_run(self, run_id: str, *, reason: str = "manual_resume") -> Optional[Dict[str, Any]]:
        run_record = run_service.get_run(run_id)
        if not run_record:
            return None
        emitter = self._emitter_for_run(run_record, component="erc", node="command_service")
        transition_event = emitter.emit(
            "run.state.changed",
            {"from_status": run_record.get("status"), "to_status": "running", "reason": reason},
        )
        event = emitter.emit(
            "run.resumed",
            {"run_id": run_id, "reason": reason},
        )
        command_service.resume_run(run_id, reason=reason)
        workflow_ledger_service.sync_run_status(run_id, run_status="running", reason=reason)
        return {"transition_event": transition_event, "command_event": event}

    def cancel_run(self, run_id: str, *, reason: str = "manual_cancel") -> Optional[Dict[str, Any]]:
        run_record = run_service.get_run(run_id)
        if not run_record:
            return None
        emitter = self._emitter_for_run(run_record, component="erc", node="command_service")
        transition_event = emitter.emit(
            "run.state.changed",
            {"from_status": run_record.get("status"), "to_status": "cancelled", "reason": reason},
        )
        event = emitter.emit(
            "run.cancelled",
            {"run_id": run_id, "reason": reason},
        )
        command_service.cancel_run(run_id, reason=reason)
        workflow_ledger_service.sync_run_status(run_id, run_status="cancelled", reason=reason)
        try:
            from core.engineering_sandbox.service import get_engineering_sandbox_service

            cleanup = get_engineering_sandbox_service().abort_run_workspaces(
                run_id=run_id,
                error_code="run_cancelled",
            )
            if cleanup.get("worktreeIds") or cleanup.get("leaseIds"):
                emitter.emit(
                    "engineering.worktree.cancelled",
                    {
                        "summary": "本轮未交付的隔离工程工作已关闭，变更证据仍保留用于诊断。",
                        "worktreeCount": len(cleanup.get("worktreeIds") or []),
                        "leaseCount": len(cleanup.get("leaseIds") or []),
                        "reason": reason,
                    },
                )
        except Exception:
            logging.getLogger("v8chat.erc").exception(
                "Failed to close managed engineering workspaces for cancelled run '%s'",
                run_id,
            )
        return {"transition_event": transition_event, "command_event": event}

    def interrupt_run(self, run_id: str, *, reason: str = "manual_interrupt") -> Optional[Dict[str, Any]]:
        run_record = run_service.get_run(run_id)
        if not run_record:
            return None
        emitter = self._emitter_for_run(run_record, component="erc", node="command_service")
        transition_event = emitter.emit(
            "run.state.changed",
            {"from_status": run_record.get("status"), "to_status": "paused", "reason": reason},
        )
        event = emitter.emit(
            "run.interrupted",
            {"run_id": run_id, "reason": reason},
        )
        command_service.interrupt_run(run_id, reason=reason)
        workflow_ledger_service.sync_run_status(run_id, run_status="interrupted", reason=reason)
        return {"transition_event": transition_event, "command_event": event}

    def retry_run(self, run_id: str, *, reason: str = "manual_retry") -> Optional[Dict[str, Any]]:
        run_record = run_service.get_run(run_id)
        if not run_record:
            return None
        emitter = self._emitter_for_run(run_record, component="erc", node="command_service")
        event = emitter.emit(
            "run.retry.requested",
            {"run_id": run_id, "reason": reason},
        )
        command_service.retry_run(run_id, reason=reason)
        return {"command_event": event}

    def approve(self, approval_id: str, *, response: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        approval = command_service.approve(approval_id, response=response)
        if not approval:
            return None
        approval_kind = str(approval.get("approval_kind") or "").strip()
        run_record = run_service.get_run(approval["run_id"])
        if not run_record:
            return None
        emitter = self._emitter_for_run(run_record, component="erc", node="command_service")
        if approval_kind == "mcp_app_tool_call":
            event = emitter.emit(
                "approval.approved",
                {
                    "approval_id": approval_id,
                    "run_id": approval["run_id"],
                    "approval_kind": approval_kind,
                    "response": response or {},
                },
            )
            return {"command_event": event, "approval": approval}
        transition_event = emitter.emit(
            "run.state.changed",
            {"from_status": run_record.get("status"), "to_status": "running", "reason": "approval_approved"},
        )
        event = emitter.emit(
            "approval.approved",
            {
                "approval_id": approval_id,
                "run_id": approval["run_id"],
                "response": response or {},
            },
        )
        run_service.transition_run(approval["run_id"], status="running")
        workflow_ledger_service.sync_run_status(
            approval["run_id"],
            run_status="running",
            reason="approval_approved",
            metadata={"approval_id": approval_id},
        )
        return {"transition_event": transition_event, "command_event": event, "approval": approval}

    def resolve_ask_user_interaction(
        self,
        interaction_id: str,
        *,
        response: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        interaction = db.get_ask_user_interaction(interaction_id)
        if not interaction:
            return None
        answer_text = str((response or {}).get("answer") or "").strip() or None
        db.update_ask_user_interaction(
            interaction_id,
            status="resolved",
            answer_text=answer_text,
        )
        interaction = db.get_ask_user_interaction(interaction_id)
        if not interaction:
            return None
        run_record = run_service.get_run(interaction["run_id"])
        if not run_record:
            return {"interaction": interaction}
        emitter = self._emitter_for_run(run_record, component="erc", node="command_service")
        transition_event = emitter.emit(
            "run.state.changed",
            {"from_status": run_record.get("status"), "to_status": "running", "reason": "ask_user_resolved"},
        )
        event = emitter.emit(
            "ask_user.resolved",
            {
                "interactionId": interaction_id,
                "run_id": interaction["run_id"],
                "toolCallId": interaction.get("tool_call_id"),
                "answer": answer_text,
                "response": response or {},
            },
        )
        run_service.transition_run(interaction["run_id"], status="running")
        workflow_ledger_service.sync_run_status(
            interaction["run_id"],
            run_status="running",
            reason="ask_user_resolved",
            metadata={"interaction_id": interaction_id},
        )
        return {"transition_event": transition_event, "command_event": event, "interaction": interaction}

    def reject(self, approval_id: str, *, response: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        approval = command_service.reject(approval_id, response=response)
        if not approval:
            return None
        approval_kind = str(approval.get("approval_kind") or "").strip()
        run_record = run_service.get_run(approval["run_id"])
        if not run_record:
            return None
        emitter = self._emitter_for_run(run_record, component="erc", node="command_service")
        if approval_kind == "mcp_app_tool_call":
            event = emitter.emit(
                "approval.rejected",
                {
                    "approval_id": approval_id,
                    "run_id": approval["run_id"],
                    "approval_kind": approval_kind,
                    "response": response or {},
                },
            )
            return {"command_event": event, "approval": approval}
        transition_event = emitter.emit(
            "run.state.changed",
            {"from_status": run_record.get("status"), "to_status": "waiting_input", "reason": "approval_rejected"},
        )
        event = emitter.emit(
            "approval.rejected",
            {
                "approval_id": approval_id,
                "run_id": approval["run_id"],
                "response": response or {},
            },
        )
        run_service.transition_run(approval["run_id"], status="waiting_input")
        workflow_ledger_service.sync_run_status(
            approval["run_id"],
            run_status="waiting_input",
            reason="approval_rejected",
            metadata={"approval_id": approval_id},
        )
        return {"transition_event": transition_event, "command_event": event, "approval": approval}

    def peek_control_signal(self, run_id: str) -> Optional[Dict[str, Any]]:
        return command_service.peek_control_signal(run_id)

    def consume_control_signal(self, run_id: str) -> Optional[Dict[str, Any]]:
        return command_service.consume_control_signal(run_id)


erc_kernel = ExecutionRuntimeCore()
