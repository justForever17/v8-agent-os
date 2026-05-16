from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, Optional

from api.models import ChatMessage, ChatRequest, EngineConfig
from core.database import db

from erc.kernel import erc_kernel
from erc.models import RuntimeCommand, RuntimeEventsPayload, RuntimeSnapshotPayload
from erc.recovery_policy import derive_recovery_class
from erc.session_runtime import session_runtime_service
from runtimes.automation.runtime import automation_runtime
from runtimes.memory.scope_resolution import session_scope_binding_service


ChatScheduler = Callable[..., Optional[str]]


class RuntimeCommandRouter:
    def __init__(self) -> None:
        self._schedule_chat_run: Optional[ChatScheduler] = None

    def configure(self, *, schedule_chat_run: ChatScheduler) -> None:
        self._schedule_chat_run = schedule_chat_run

    def get_snapshot(self, session_id: str) -> Dict[str, Any]:
        payload = session_runtime_service.get_snapshot(session_id)
        return RuntimeSnapshotPayload(
            session_id=payload["session_id"],
            latest_seq=int(payload.get("latestSeq") or 0),
            snapshot=payload.get("snapshot"),
            runtime_timeline=list(payload.get("runtimeTimeline") or []),
            todos=payload.get("todos"),
            current_run=payload.get("currentRun"),
            runtime_status=payload.get("runtimeStatus"),
            workflow=payload.get("workflow"),
            workflow_projection=payload.get("workflowProjection"),
            approvals=list(payload.get("approvals") or []),
            ask_user_interactions=list(payload.get("askUserInteractions") or []),
            controls=payload.get("controls"),
            recoverable=payload.get("recoverable"),
            summary=payload.get("summary"),
            source=payload.get("source"),
            context_governance=payload.get("contextGovernance"),
            context_governance_history=list(payload.get("contextGovernanceHistory") or []),
            lane=payload.get("lane"),
            liveness=payload.get("liveness"),
            recovery_class=payload.get("recoveryClass"),
        ).as_dict()

    def get_events(self, session_id: str, *, after_seq: Optional[int] = None) -> Dict[str, Any]:
        payload = session_runtime_service.get_runtime_events(session_id, after_seq=after_seq)
        return RuntimeEventsPayload(
            session_id=payload["session_id"],
            latest_seq=int(payload.get("latestSeq") or 0),
            events=list(payload.get("events") or []),
        ).as_dict()

    def dispatch_run_command(self, command: RuntimeCommand) -> Optional[Dict[str, Any]]:
        topic = command.topic if command.topic.startswith("run.") else f"run.{command.topic}"
        run_id = command.run_id
        if not run_id:
            raise ValueError(f"{topic} requires run_id")

        if topic == "run.pause":
            return erc_kernel.pause_run(run_id, reason=command.reason or "manual_pause")
        if topic == "run.resume":
            run_record = db.get_run_record(run_id)
            if not run_record:
                return None
            recovery_class = derive_recovery_class(run_record)
            if recovery_class.get("class") != "resume_supported":
                if recovery_class.get("class") == "retry_only":
                    raise ValueError(f"Run '{run_id}' 当前类型仅支持 retry，不支持真正 resume。")
                raise ValueError(f"Run '{run_id}' 当前类型不支持真正 resume，请重新提交或改用 retry。")
            result = erc_kernel.resume_run(run_id, reason=command.reason or "manual_resume")
            if result:
                self._schedule_resume(run_record, result)
            return result
        if topic == "run.cancel":
            return erc_kernel.cancel_run(run_id, reason=command.reason or "manual_cancel")
        if topic == "run.interrupt":
            return erc_kernel.interrupt_run(run_id, reason=command.reason or "manual_interrupt")
        if topic == "run.retry":
            result = erc_kernel.retry_run(run_id, reason=command.reason or "manual_retry")
            if result:
                self._schedule_retry(run_id, result)
            return result
        raise ValueError(f"Unsupported run command topic: {topic}")

    def dispatch_approval_command(self, command: RuntimeCommand) -> Optional[Dict[str, Any]]:
        topic = command.topic if command.topic.startswith("approval.") else f"approval.{command.topic}"
        approval_id = command.approval_id
        if not approval_id:
            raise ValueError(f"{topic} requires approval_id")

        if topic == "approval.approve":
            result = erc_kernel.approve(approval_id, response=command.response)
            if result:
                self._resume_from_approval(result.get("approval") or {}, command.response or {})
            return result
        if topic == "approval.reject":
            return erc_kernel.reject(approval_id, response=command.response)
        raise ValueError(f"Unsupported approval command topic: {topic}")

    def dispatch_ask_user_command(self, command: RuntimeCommand) -> Optional[Dict[str, Any]]:
        topic = command.topic if command.topic.startswith("ask_user.") else f"ask_user.{command.topic}"
        interaction_id = command.interaction_id
        if not interaction_id:
            raise ValueError(f"{topic} requires interaction_id")

        if topic == "ask_user.respond":
            result = erc_kernel.resolve_ask_user_interaction(interaction_id, response=command.response)
            if result:
                self._resume_from_ask_user(result.get("interaction") or {}, command.response or {})
            return result
        raise ValueError(f"Unsupported ask_user command topic: {topic}")

    def _scope_payload_for_session(self, session_id: str) -> Dict[str, Any]:
        binding = session_scope_binding_service.get_binding(session_id)
        if not binding:
            return {}
        return {
            "project_id": binding.project_id,
            "workspace_id": binding.workspace_id,
            "workspace_path": binding.workspace_path,
            "scope_hint": binding.resolved_scope,
            "scope_mode": "explicit",
        }

    def _engine_config_from_run(self, run_record: Dict[str, Any] | None) -> EngineConfig:
        metadata = dict((run_record or {}).get("metadata") or {})
        return EngineConfig(
            provider=metadata.get("provider") or "openai",
            model_name=metadata.get("model") or "gpt-4o",
        )

    def _chat_messages_from_session(self, session_id: str) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        for record in db.get_messages(session_id):
            metadata = record.get("metadata") or {}
            messages.append(
                ChatMessage(
                    role=record.get("role") or "user",
                    content=record.get("content") or "",
                    name=metadata.get("tool_name") if record.get("role") == "tool" else None,
                    tool_call_id=metadata.get("tool_call_id") if record.get("role") == "tool" else None,
                )
            )
        return messages

    def _build_retry_chat_request(self, run_record: Dict[str, Any]) -> ChatRequest:
        scope_payload = self._scope_payload_for_session(run_record["session_id"])
        return ChatRequest(
            messages=self._chat_messages_from_session(run_record["session_id"]),
            config=self._engine_config_from_run(run_record),
            session_id=run_record["session_id"],
            conversation_id=run_record.get("conversation_id") or run_record["session_id"],
            user_id=run_record.get("user_id") or "anonymous",
            project_id=scope_payload.get("project_id"),
            workspace_id=scope_payload.get("workspace_id"),
            workspace_path=scope_payload.get("workspace_path"),
            scope_hint=scope_payload.get("scope_hint"),
            scope_mode=scope_payload.get("scope_mode") or "explicit",
        )

    def _build_resume_chat_request(self, approval: Dict[str, Any], response: Dict[str, Any] | None = None) -> ChatRequest | None:
        run_record = db.get_run_record(approval.get("run_id", ""))
        if not run_record:
            return None
        scope_payload = self._scope_payload_for_session(run_record["session_id"])
        resume_value = dict(response or {})
        resume_value.setdefault("approval_id", approval.get("id") or approval.get("approval_id"))
        return ChatRequest(
            messages=[],
            config=self._engine_config_from_run(run_record),
            session_id=run_record["session_id"],
            conversation_id=run_record.get("conversation_id") or run_record["session_id"],
            user_id=run_record.get("user_id") or "anonymous",
            project_id=scope_payload.get("project_id"),
            workspace_id=scope_payload.get("workspace_id"),
            workspace_path=scope_payload.get("workspace_path"),
            scope_hint=scope_payload.get("scope_hint"),
            scope_mode=scope_payload.get("scope_mode") or "explicit",
            resume_run_id=run_record["id"],
            resume_value=resume_value,
        )

    def _build_resume_chat_request_from_ask_user(self, interaction: Dict[str, Any], response: Dict[str, Any] | None = None) -> ChatRequest | None:
        run_record = db.get_run_record(interaction.get("run_id", ""))
        if not run_record:
            return None
        scope_payload = self._scope_payload_for_session(run_record["session_id"])
        resume_value = dict(response or {})
        resume_value.setdefault("interactionId", interaction.get("id"))
        if interaction.get("tool_call_id"):
            resume_value.setdefault("toolCallId", interaction.get("tool_call_id"))
        if interaction.get("answer_text"):
            resume_value.setdefault("answer", interaction.get("answer_text"))
        return ChatRequest(
            messages=[],
            config=self._engine_config_from_run(run_record),
            session_id=run_record["session_id"],
            conversation_id=run_record.get("conversation_id") or run_record["session_id"],
            user_id=run_record.get("user_id") or "anonymous",
            project_id=scope_payload.get("project_id"),
            workspace_id=scope_payload.get("workspace_id"),
            workspace_path=scope_payload.get("workspace_path"),
            scope_hint=scope_payload.get("scope_hint"),
            scope_mode=scope_payload.get("scope_mode") or "explicit",
            resume_run_id=run_record["id"],
            resume_value=resume_value,
        )

    def _build_manual_resume_chat_request(self, run_record: Dict[str, Any]) -> ChatRequest:
        scope_payload = self._scope_payload_for_session(run_record["session_id"])
        metadata = dict(run_record.get("metadata") or {})
        resume_value = dict(metadata.get("manual_resume_value") or {})
        return ChatRequest(
            messages=[],
            config=self._engine_config_from_run(run_record),
            session_id=run_record["session_id"],
            conversation_id=run_record.get("conversation_id") or run_record["session_id"],
            user_id=run_record.get("user_id") or "anonymous",
            project_id=scope_payload.get("project_id"),
            workspace_id=scope_payload.get("workspace_id"),
            workspace_path=scope_payload.get("workspace_path"),
            scope_hint=scope_payload.get("scope_hint"),
            scope_mode=scope_payload.get("scope_mode") or "explicit",
            resume_run_id=run_record["id"],
            resume_value=resume_value,
        )

    def _resume_mode_for_run(self, run_record: Dict[str, Any]) -> str | None:
        run_type = str(run_record.get("run_type") or "").strip()
        metadata = dict(run_record.get("metadata") or {})
        if run_type == "chat":
            return "chat"
        if run_type == "automation" and str(metadata.get("action_type") or "").strip() == "agent":
            return "automation_agent"
        return None

    def _schedule_retry(self, run_id: str, result: Dict[str, Any]) -> None:
        run_record = db.get_run_record(run_id)
        if not run_record:
            return
        if run_record.get("run_type") == "automation":
            invocation = automation_runtime.build_invocation_from_run(run_record)
            next_handle = automation_runtime.begin_run(
                action_type=invocation["action_type"],
                target=invocation["target"],
                payload=invocation["payload"],
                trigger_source=invocation["trigger_source"],
                is_async=invocation["is_async"],
                kwargs=invocation["kwargs"],
            )
            invocation_kwargs = dict(invocation["kwargs"])
            invocation_kwargs["run_id"] = next_handle.run_id
            invocation_kwargs["session_id"] = next_handle.session_id
            self._schedule_automation_run(
                action_type=invocation["action_type"],
                target=invocation["target"],
                payload=invocation["payload"],
                is_async=invocation["is_async"],
                trigger_source=invocation["trigger_source"],
                kwargs=invocation_kwargs,
            )
            result["next_run_id"] = next_handle.run_id
        elif self._schedule_chat_run is not None:
            next_run_id = f"run_{uuid.uuid4().hex}"
            retry_request = self._build_retry_chat_request(run_record)
            self._schedule_chat_run(retry_request, transport="system_retry", run_id=next_run_id)
            result["next_run_id"] = next_run_id

        if result.get("next_run_id") and result.get("command_event"):
            result["command_event"].setdefault("payload", {})
            result["command_event"]["payload"]["next_run_id"] = result["next_run_id"]

    def _schedule_resume(self, run_record: Dict[str, Any], result: Dict[str, Any]) -> None:
        resume_mode = self._resume_mode_for_run(run_record)
        if resume_mode == "automation_agent":
            invocation = automation_runtime.build_invocation_from_run(run_record)
            invocation_kwargs = dict(invocation["kwargs"])
            invocation_kwargs["run_id"] = run_record["id"]
            invocation_kwargs["session_id"] = run_record["session_id"]
            invocation_kwargs["resume_reason"] = "manual_resume"
            self._schedule_automation_run(
                action_type=invocation["action_type"],
                target=invocation["target"],
                payload=invocation["payload"],
                is_async=invocation["is_async"],
                trigger_source=invocation["trigger_source"],
                kwargs=invocation_kwargs,
            )
        elif resume_mode == "chat" and self._schedule_chat_run is not None:
            resume_request = self._build_manual_resume_chat_request(run_record)
            self._schedule_chat_run(resume_request, transport="system_resume")

        result["resume_mode"] = resume_mode
        result["resume_scheduled"] = bool(resume_mode)

    def _resume_from_approval(self, approval: Dict[str, Any], response: Dict[str, Any]) -> None:
        if str(approval.get("approval_kind") or "").strip() == "mcp_app_tool_call":
            return
        run_record = db.get_run_record(approval.get("run_id", ""))
        if not run_record:
            return
        if run_record.get("run_type") == "automation":
            invocation = automation_runtime.build_invocation_from_run(run_record)
            invocation_kwargs = dict(invocation["kwargs"])
            invocation_kwargs["run_id"] = run_record["id"]
            invocation_kwargs["session_id"] = run_record["session_id"]
            invocation_kwargs["safety_override"] = True
            self._schedule_automation_run(
                action_type=invocation["action_type"],
                target=invocation["target"],
                payload=invocation["payload"],
                is_async=invocation["is_async"],
                trigger_source=invocation["trigger_source"],
                kwargs=invocation_kwargs,
            )
            return
        if self._schedule_chat_run is None:
            return
        resume_request = self._build_resume_chat_request(approval, response)
        if resume_request:
            self._schedule_chat_run(resume_request, transport="system_resume")

    def _resume_from_ask_user(self, interaction: Dict[str, Any], response: Dict[str, Any]) -> None:
        if self._schedule_chat_run is None:
            return
        resume_request = self._build_resume_chat_request_from_ask_user(interaction, response)
        if resume_request:
            self._schedule_chat_run(resume_request, transport="system_resume")

    def _schedule_automation_run(
        self,
        *,
        action_type: str,
        target: str,
        payload: Dict[str, Any],
        is_async: bool,
        trigger_source: str | None,
        kwargs: Dict[str, Any],
    ):
        from core.action_executor import ActionExecutor

        execute_kwargs = dict(kwargs or {})
        if trigger_source:
            execute_kwargs.setdefault("trigger", trigger_source)
        return ActionExecutor.execute(
            action_type=action_type,
            target=target,
            is_async=is_async,
            payload=payload,
            **execute_kwargs,
        )


runtime_command_router = RuntimeCommandRouter()
