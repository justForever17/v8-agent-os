from __future__ import annotations

import threading
import uuid
from typing import Any, Callable, Dict, Optional

from api.models import ChatMessage, ChatRequest, ChatRequestData, EngineConfig
from core.database import db
from core.realtime_protocol import build_runtime_event
from core.runtime_episodes import ACTIVE_EPISODE_STATES, TERMINAL_EPISODE_STATES
from core.spec_service import spec_service
from erc.chat_canonical_transcript import build_canonical_chat_turn_window

from erc.kernel import erc_kernel
from erc.models import RuntimeCommand, RuntimeEventsPayload, RuntimeSnapshotPayload
from erc.recovery_policy import derive_recovery_class
from erc.run_service import run_service
from erc.session_runtime import session_runtime_service
from erc.workflow_ledger import workflow_ledger_service
from runtimes.automation.runtime import automation_runtime
from runtimes.memory.scope_resolution import session_scope_binding_service


ChatScheduler = Callable[..., Optional[str]]
RUNTIME_EPISODE_RESUME_METADATA_KEY = "runtimeEpisodeResume"
RUNTIME_EPISODE_RESUME_TERMINAL_STATES = TERMINAL_EPISODE_STATES
RUNTIME_EPISODE_RESUME_MAX_WORKER_RETRIES = 2


class RuntimeCommandRouter:
    def __init__(self) -> None:
        self._schedule_chat_run: Optional[ChatScheduler] = None
        self._runtime_episode_resume_lock = threading.Lock()

    def configure(self, *, schedule_chat_run: ChatScheduler) -> None:
        self._schedule_chat_run = schedule_chat_run

    def schedule_chat_run(
        self,
        request: ChatRequest,
        *,
        transport: str,
        run_id: str | None = None,
    ) -> Optional[str]:
        if not self._schedule_chat_run:
            return None
        return self._schedule_chat_run(request, transport=transport, run_id=run_id)

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
            session_coordination_messages=list(payload.get("sessionCoordinationMessages") or []),
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
            pending_approval = db.get_pending_approval(approval_id)
            if pending_approval and self._approval_kind(pending_approval) == "spec_stage_approval":
                preflight = self._preflight_spec_stage_approval(pending_approval)
                if isinstance(preflight, dict) and preflight.get("ok") is False:
                    run_record = db.get_run_record(str(pending_approval.get("run_id") or ""))
                    if run_record:
                        self._emit_resume_event(
                            run_record,
                            "approval.blocked",
                            {
                                "approvalKind": "spec_stage_approval",
                                "approvalId": approval_id,
                                "reason": str(preflight.get("kind") or "spec_stage_not_ready"),
                                "stage": preflight.get("stage"),
                            },
                        )
                    return {
                        "approval": pending_approval,
                        "spec_stage_approval": preflight,
                        "resume_mode": "chat",
                        "resume_scheduled": False,
                        "resume_error": "spec_stage_approval_preflight_failed",
                    }
            result = erc_kernel.approve(approval_id, response=command.response)
            if result:
                approval = result.get("approval") or {}
                spec_approval_result = None
                if self._approval_kind(approval) == "spec_stage_approval":
                    spec_approval_result = self._apply_spec_stage_approval(approval, command.response or {})
                    result["spec_stage_approval"] = spec_approval_result
                if isinstance(spec_approval_result, dict) and spec_approval_result.get("ok") is False:
                    resume_info = {
                        "resume_mode": "chat",
                        "resume_scheduled": False,
                        "resume_error": "spec_stage_approval_apply_failed",
                    }
                else:
                    resume_info = self._resume_from_approval(approval, command.response or {})
                if resume_info:
                    result.update(resume_info)
                if self._approval_kind(approval) == "spec_stage_approval" and not bool(
                    (resume_info or {}).get("resume_scheduled")
                ):
                    reason = str((resume_info or {}).get("resume_error") or "spec_approval_resume_not_scheduled")
                    self._restore_waiting_approval_after_resume_failure(approval, reason=reason)
                    result["resume_error"] = reason
            return result
        if topic == "approval.reject":
            result = erc_kernel.reject(approval_id, response=command.response)
            if result:
                approval = result.get("approval") or {}
                if self._approval_kind(approval) == "spec_stage_approval":
                    resume_info = self._resume_spec_revision(approval, command.response or {})
                    if resume_info:
                        result.update(resume_info)
            return result
        raise ValueError(f"Unsupported approval command topic: {topic}")

    def dispatch_ask_user_command(self, command: RuntimeCommand) -> Optional[Dict[str, Any]]:
        topic = command.topic if command.topic.startswith("ask_user.") else f"ask_user.{command.topic}"
        interaction_id = command.interaction_id
        if not interaction_id:
            raise ValueError(f"{topic} requires interaction_id")

        if topic == "ask_user.respond":
            result = erc_kernel.resolve_ask_user_interaction(interaction_id, response=command.response)
            if result:
                interaction = result.get("interaction") or {}
                result["spec_clarification"] = self._record_spec_clarification_from_ask_user(
                    interaction,
                    command.response or {},
                )
                try:
                    from erc.session_coordination_service import session_coordination_service

                    result["session_coordination"] = session_coordination_service.handle_ask_user_resolution(
                        interaction,
                        command.response or {},
                    )
                except Exception as exc:
                    result["session_coordination"] = {
                        "handled": False,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                self._resume_from_ask_user(interaction, command.response or {})
            return result
        raise ValueError(f"Unsupported ask_user command topic: {topic}")

    def _record_spec_clarification_from_ask_user(
        self,
        interaction: Dict[str, Any],
        response: Dict[str, Any],
    ) -> Dict[str, Any]:
        request = interaction.get("request") if isinstance(interaction.get("request"), dict) else {}
        spec_context = request.get("specContext") if isinstance(request.get("specContext"), dict) else {}
        if str(spec_context.get("kind") or spec_context.get("contextKind") or "").strip().lower() not in {"spec_clarification", "spec-clarification"}:
            return {"recorded": False, "reason": "not_spec_clarification"}
        spec_id = str(spec_context.get("specId") or spec_context.get("spec_id") or "").strip()
        if not spec_id:
            return {"recorded": False, "reason": "spec_id_pending"}
        run_record = db.get_run_record(str(interaction.get("run_id") or ""))
        scope_payload = self._scope_payload_for_session(str(interaction.get("session_id") or ""))
        workspace_path = (
            str(spec_context.get("workspacePath") or spec_context.get("workspace_path") or "").strip()
            or self._workspace_path_for_run(run_record or {}, scope_payload)
        )
        if not workspace_path:
            return {"recorded": False, "reason": "workspace_path_missing", "specId": spec_id}
        stage = str(spec_context.get("stage") or spec_context.get("specStage") or "").strip().lower()
        answer = str((response or {}).get("answer") or interaction.get("answer_text") or "").strip()
        try:
            recorded = spec_service.record_clarification(
                workspace_path=workspace_path,
                spec_id=spec_id,
                stage=stage,
                question=str(request.get("question") or request.get("prompt") or ""),
                answer=answer,
                source_run_id=str(interaction.get("run_id") or ""),
                tool_call_id=str(interaction.get("tool_call_id") or ""),
                interaction_id=str(interaction.get("id") or ""),
                feature_name=str(spec_context.get("featureName") or ""),
            )
        except Exception as exc:
            return {"recorded": False, "reason": f"{type(exc).__name__}: {exc}", "specId": spec_id, "stage": stage}
        return {"recorded": True, "specId": recorded.get("specId"), "stage": recorded.get("stage")}

    def _scope_payload_for_session(self, session_id: str) -> Dict[str, Any]:
        binding = session_scope_binding_service.get_binding(session_id)
        if not binding:
            return {}
        return {
            "project_id": binding.project_id,
            "workspace_id": binding.workspace_id,
            "workspace_path": binding.workspace_path,
            "scope_hint": binding.scope_hint,
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

    def _canonical_chat_messages_for_coordination(self, session_id: str) -> list[ChatMessage]:
        turn_window = build_canonical_chat_turn_window(
            session_id,
            limit_turns=12,
        )
        messages: list[ChatMessage] = []
        for record in list(turn_window.get("messages") or []):
            if not isinstance(record, dict):
                continue
            role = str(record.get("role") or "").strip().lower()
            content = str(record.get("content") or "").strip()
            if role not in {"user", "assistant", "system"} or not content:
                continue
            messages.append(ChatMessage(role=role, content=content))
        return messages or self._chat_messages_from_session(session_id)

    def build_session_coordination_chat_request(self, *, session_id: str, message_id: str) -> ChatRequest:
        session = db.get_session(session_id) or {}
        latest_runs = db.list_run_records(session_id=session_id, limit=1)
        latest_run = latest_runs[0] if latest_runs else None
        scope_payload = self._scope_payload_for_session(session_id)
        request_data = ChatRequestData()
        request_data._session_coordination_message_id = message_id
        return ChatRequest(
            messages=self._canonical_chat_messages_for_coordination(session_id),
            config=self._engine_config_from_run(latest_run),
            session_id=session_id,
            conversation_id=session_id,
            user_id=str(session.get("user_id") or (latest_run or {}).get("user_id") or "anonymous"),
            project_id=scope_payload.get("project_id"),
            workspace_id=scope_payload.get("workspace_id"),
            workspace_path=scope_payload.get("workspace_path"),
            scope_hint=scope_payload.get("scope_hint"),
            scope_mode=scope_payload.get("scope_mode") or "explicit",
            data=request_data,
        )

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

    def _build_spec_revision_chat_request(
        self,
        approval: Dict[str, Any],
        response: Dict[str, Any] | None = None,
    ) -> ChatRequest | None:
        run_record = db.get_run_record(str(approval.get("run_id") or ""))
        if not run_record:
            return None
        request_payload = approval.get("request") if isinstance(approval.get("request"), dict) else {}
        spec_id = str(request_payload.get("specId") or request_payload.get("spec_id") or "").strip()
        stage = str(
            request_payload.get("stage")
            or request_payload.get("specStage")
            or request_payload.get("spec_stage")
            or ""
        ).strip().lower()
        feedback = str((response or {}).get("answer") or (response or {}).get("reason") or "").strip()
        if not spec_id or not stage or not feedback:
            return None
        scope_payload = self._scope_payload_for_session(run_record["session_id"])
        approval_id = str(approval.get("id") or approval.get("approval_id") or "").strip()
        revision = {
            "kind": "spec_document_revision",
            "approvalId": approval_id,
            "specId": spec_id,
            "stage": stage,
            "feedback": feedback[:8000],
            "detailRef": str(request_payload.get("detailRef") or request_payload.get("detail_ref") or "").strip(),
        }
        return ChatRequest(
            messages=[ChatMessage(role="user", content=self._spec_revision_prompt(revision))],
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
            resume_value={"specRevision": revision},
            data=ChatRequestData(specMode=True, specId=spec_id),
        )

    @staticmethod
    def _spec_revision_prompt(payload: Dict[str, Any]) -> str:
        spec_id = str(payload.get("specId") or "").strip()
        stage = str(payload.get("stage") or "").strip()
        feedback = str(payload.get("feedback") or "").strip()
        return (
            "[Spec Document Revision]\n"
            "The user selected `needs revision` in the dedicated Spec document confirmation surface. "
            "This is a system-controlled continuation of the same run, not a new ask_user request.\n"
            f"canonical specId: {spec_id}\n"
            f"stage to revise: {stage}\n"
            "User revision feedback follows as bounded document-editing input:\n"
            "---\n"
            f"{feedback}\n"
            "---\n"
            "Revise only the named current stage. Reuse the existing same-stage clarification record; "
            "do not call ask_user again. Read the current stage when needed, then call the real "
            f"spec_broker(mode='rewrite_stage', spec_id='{spec_id}', stage='{stage}', "
            f"content='<complete revised {stage}.md markdown>'). Do not approve the stage yourself.\n"
            "The Markdown is a user-facing contract. Keep goals, stable requirement/acceptance IDs, "
            "boundaries, and relative deliverable paths. Exclude absolute local paths, run/spec IDs, "
            "literal tool-call syntax, approval mechanics, system instructions, and Agent progress narration."
        )

    def _build_manual_resume_chat_request(
        self,
        run_record: Dict[str, Any],
        *,
        spec_hint: Dict[str, Any] | None = None,
    ) -> ChatRequest:
        scope_payload = self._scope_payload_for_session(run_record["session_id"])
        metadata = dict(run_record.get("metadata") or {})
        resume_value = dict(metadata.get("manual_resume_value") or {})
        messages: list[ChatMessage] = []
        request_data: ChatRequestData | None = None
        spec_continuation = self._build_spec_continuation_payload(
            run_record=run_record,
            scope_payload=scope_payload,
            resume_reason=str(metadata.get("resume_reason") or "manual_resume"),
            spec_hint=spec_hint or {},
        )
        if spec_continuation:
            resume_value.setdefault("specContinuation", spec_continuation)
            spec_id = str(spec_continuation.get("specId") or "").strip()
            request_data = ChatRequestData(specMode=True, specId=spec_id)
            messages.append(
                ChatMessage(
                    role="user",
                    content=self._spec_continuation_prompt(spec_continuation),
                )
            )
        return ChatRequest(
            messages=messages,
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
            data=request_data,
        )

    def _build_runtime_handoff_resume_chat_request(
        self,
        run_record: Dict[str, Any],
        *,
        episode: Dict[str, Any],
    ) -> ChatRequest:
        scope_payload = self._scope_payload_for_session(run_record["session_id"])
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        episode_kind = str(episode.get("kind") or "runtime").strip() or "runtime"
        episode_state = str(episode.get("state") or "").strip()
        inputs = episode.get("inputs") if isinstance(episode.get("inputs"), dict) else {}
        need = episode.get("need") if isinstance(episode.get("need"), dict) else {}
        spec_id = str(
            inputs.get("specId")
            or inputs.get("spec_id")
            or need.get("specId")
            or need.get("spec_id")
            or ""
        ).strip()
        resume_value = {
            "runtimeEpisodeHandoff": {
                "kind": "runtime_episode_terminal",
                "episodeId": episode_id,
                "episodeKind": episode_kind,
                "episodeState": episode_state,
                "runId": run_record.get("id"),
                "sessionId": run_record.get("session_id"),
                **({"specId": spec_id} if spec_id else {}),
            }
        }
        request_data = ChatRequestData(specMode=True, specId=spec_id) if spec_id else None
        return ChatRequest(
            messages=[
                ChatMessage(
                    role="user",
                    content=(
                        "[Runtime Episode Terminal]\n"
                        "A previously routed runtime episode has now reached a terminal state. "
                        "Continue by merging any available typed handoff into the user-facing answer; if the episode failed "
                        "or was cancelled without a usable handoff, report that terminal outcome accurately. "
                        "Do not route a new runtime episode, rewrite Spec documents, or perform direct file or command work.\n"
                        f"episodeId: {episode_id}\n"
                        f"episodeKind: {episode_kind}\n"
                        f"episodeState: {episode_state}\n"
                        f"specId: {spec_id or '(none)'}"
                    ),
                )
            ],
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
            data=request_data,
        )

    def _build_spec_continuation_payload(
        self,
        *,
        run_record: Dict[str, Any],
        scope_payload: Dict[str, Any],
        resume_reason: str,
        spec_hint: Dict[str, Any] | None = None,
    ) -> Dict[str, Any] | None:
        workspace_path = self._workspace_path_for_run(run_record, scope_payload)
        if not workspace_path:
            return None
        hint = dict(spec_hint or {})
        spec_id = str(hint.get("specId") or hint.get("spec_id") or "").strip()
        if not spec_id:
            try:
                listing = spec_service.list_specs(workspace_path=workspace_path, include_archived=False, limit=1)
            except Exception:
                return None
            spec_item = next(
                (
                    item
                    for item in list(listing.get("specs") or [])
                    if isinstance(item, dict) and str(item.get("specId") or "").strip()
                ),
                None,
            )
            if not spec_item:
                return None
            spec_id = str(spec_item.get("specId") or "").strip()
        try:
            brief = spec_service.build_brief(workspace_path=workspace_path, spec_id=spec_id)
        except Exception:
            return None
        pipeline = brief.get("pipelineControl") if isinstance(brief.get("pipelineControl"), dict) else {}
        blocked_stage = str(pipeline.get("blockedByApproval") or "").strip()
        if blocked_stage:
            return None
        next_stage = str(pipeline.get("nextStage") or "").strip()
        approved = [str(item) for item in list(brief.get("approvedStages") or []) if str(item or "").strip()]
        if not approved and not bool(pipeline.get("runtimeExecutionAllowed")):
            return None
        if not next_stage:
            return None
        return {
            "kind": "spec_approval_continuation",
            "specId": spec_id,
            "featureName": brief.get("featureName"),
            "workspacePath": workspace_path,
            "currentStage": brief.get("currentStage"),
            "approvedStages": approved,
            "nextStage": next_stage,
            "runtimeExecutionAllowed": bool(pipeline.get("runtimeExecutionAllowed")),
            "detailRef": f"spec://{spec_id}/{brief.get('currentStage')}" if brief.get("currentStage") else f"spec://{spec_id}",
            "resumeReason": resume_reason,
            "runId": run_record.get("id"),
        }

    def _workspace_path_for_run(self, run_record: Dict[str, Any], scope_payload: Dict[str, Any]) -> str:
        candidates: list[Any] = [
            scope_payload.get("workspace_path"),
            scope_payload.get("workspacePath"),
        ]
        metadata = dict(run_record.get("metadata") or {})
        candidates.extend(
            [
                metadata.get("workspace_path"),
                metadata.get("workspacePath"),
            ]
        )
        task_shape = metadata.get("taskShapeHint") if isinstance(metadata.get("taskShapeHint"), dict) else {}
        spec_brief = task_shape.get("specBrief") if isinstance(task_shape.get("specBrief"), dict) else {}
        candidates.extend([spec_brief.get("workspacePath"), spec_brief.get("workspace_path")])
        engineering_pack = (
            metadata.get("engineeringContextPack")
            if isinstance(metadata.get("engineeringContextPack"), dict)
            else {}
        )
        workspace = engineering_pack.get("workspace") if isinstance(engineering_pack.get("workspace"), dict) else {}
        candidates.extend(
            [
                workspace.get("workspaceRoot"),
                workspace.get("workspacePath"),
                workspace.get("root"),
            ]
        )
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _spec_continuation_prompt(payload: Dict[str, Any]) -> str:
        next_stage = str(payload.get("nextStage") or "").strip()
        spec_id = str(payload.get("specId") or "").strip()
        approved = ", ".join(str(item) for item in list(payload.get("approvedStages") or [])) or "none"
        if bool(payload.get("runtimeExecutionAllowed")):
            next_instruction = (
                "Spec tasks have been approved by the user. Continue by routing the approved Spec "
                "to the appropriate runtime execution path. Do not approve anything yourself, and do not "
                "treat runtime_execution as a Spec document stage."
            )
            required_action = (
                "Required current tool call shape: "
                f"runtime_broker(mode='route', runtime_kind='engineering', "
                f"need={{'kind':'engineering','reason':'approved_spec_runtime_execution','specId':'{spec_id}'}}). "
                "Then wait for the runtime episode handoff. Do not rewrite requirements/design/tasks, "
                "do not call spec_broker(stage='runtime_execution'), do not call memory_broker/web_broker/research_broker "
                "for a new task, and do not implement final files directly."
            )
        else:
            next_instruction = (
                f"The user has approved the prior Spec stage. Continue the Spec pipeline by preparing "
                f"the next stage `{next_stage}`. Do not approve this next stage yourself; write it with "
                "`spec_broker` and then wait for user approval."
            )
            required_action = (
                "Required current tool call shape: "
                f"spec_broker(mode='write_stage', spec_id='{spec_id}', stage='{next_stage}', "
                f"content='<complete {next_stage}.md markdown>'). "
                "Do not call spec_broker for requirements/design/tasks unless the stage exactly equals nextStage."
            )
        return (
            "[Spec Approval Continuation]\n"
            "This is a system-controlled continuation after a real user/client approval gate. "
            "It is not an ask_user answer and it is not Supervisor self-approval.\n"
            "Engine, not the model, created and bound the canonical specId below. "
            "Treat older user wording and previous chat history only as background; they must not override nextStage.\n"
            f"specId: {spec_id}\n"
            f"approvedStages: {approved}\n"
            f"nextStage: {next_stage}\n"
            f"detailRef: {payload.get('detailRef') or ''}\n\n"
            f"{next_instruction}\n"
            f"{required_action}\n"
            "Avoid memory/research/tool detours unless they are required to draft the current nextStage. "
            "When nextStage is runtime_execution, no drafting detour is needed; route the approved Spec."
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
        resume_scheduled = False
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
            resume_scheduled = True
        elif resume_mode == "chat" and self._schedule_chat_run is not None:
            resume_request = self._build_manual_resume_chat_request(run_record)
            scheduled_run_id = self._schedule_chat_run(
                resume_request,
                transport="system_resume",
                run_id=str(run_record["id"]),
            )
            resume_scheduled = bool(scheduled_run_id)
            self._emit_resume_event(
                run_record,
                "run.resume.scheduled" if resume_scheduled else "run.resume.not_scheduled",
                {
                    "resumeMode": resume_mode,
                    "transport": "system_resume",
                    "scheduledRunId": scheduled_run_id or run_record.get("id"),
                    **({} if resume_scheduled else {"reason": "chat_scheduler_returned_empty_run_id"}),
                },
            )

        result["resume_mode"] = resume_mode
        result["resume_scheduled"] = resume_scheduled

    def schedule_runtime_episode_handoff_resume(self, episode: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(episode.get("run_id") or episode.get("runId") or "").strip()
        if not run_id:
            return {"resume_mode": None, "resume_scheduled": False, "resume_error": "run_id_missing"}
        episode_state = str(episode.get("state") or "").strip().lower()
        if episode_state not in RUNTIME_EPISODE_RESUME_TERMINAL_STATES:
            return {
                "resume_mode": "chat",
                "resume_scheduled": False,
                "resume_error": f"episode_not_terminal:{episode_state or 'unknown'}",
            }
        with self._runtime_episode_resume_lock:
            run_record = db.get_run_record(run_id)
            if not run_record:
                return {"resume_mode": None, "resume_scheduled": False, "resume_error": "run_not_found"}
            if self._resume_mode_for_run(run_record) != "chat":
                return {
                    "resume_mode": self._resume_mode_for_run(run_record),
                    "resume_scheduled": False,
                    "resume_error": "not_chat_run",
                }
            status = str(run_record.get("status") or "").strip()
            if status not in {"running"}:
                return {
                    "resume_mode": "chat",
                    "resume_scheduled": False,
                    "resume_error": f"run_not_running:{status or 'unknown'}",
                }
            metadata = dict(run_record.get("metadata") or {})
            marker = (
                dict(metadata.get(RUNTIME_EPISODE_RESUME_METADATA_KEY) or {})
                if isinstance(metadata.get(RUNTIME_EPISODE_RESUME_METADATA_KEY), dict)
                else {}
            )
            marker_state = str(marker.get("state") or "").strip().lower()
            if marker_state == "scheduled":
                return {
                    "resume_mode": "chat",
                    "resume_scheduled": False,
                    "resume_error": "runtime_episode_resume_already_scheduled",
                }
            if marker_state != "waiting":
                return {
                    "resume_mode": "chat",
                    "resume_scheduled": False,
                    "resume_error": "run_not_waiting_for_runtime_resume",
                }
            if self._schedule_chat_run is None:
                self._emit_resume_event(
                    run_record,
                    "run.resume.not_scheduled",
                    {
                        "resumeMode": "chat",
                        "transport": "system_resume",
                        "reason": "chat_scheduler_unavailable",
                        "runtimeEpisodeId": episode.get("episodeId") or episode.get("id"),
                    },
                )
                return {"resume_mode": "chat", "resume_scheduled": False, "resume_error": "chat_scheduler_unavailable"}
            episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
            scheduled_marker = {
                **marker,
                "state": "scheduled",
                "resumeKind": "runtime_episode_terminal",
                "episodeId": episode_id,
                "episodeState": episode_state,
            }
            claim = run_service.claim_runtime_episode_resume_schedule(
                run_id,
                marker_key=RUNTIME_EPISODE_RESUME_METADATA_KEY,
                next_marker=scheduled_marker,
                terminal_states=RUNTIME_EPISODE_RESUME_TERMINAL_STATES,
                active_states=ACTIVE_EPISODE_STATES,
            )
            if not bool(claim.get("claimed")):
                return {
                    "resume_mode": "chat",
                    "resume_scheduled": False,
                    "resume_error": str(claim.get("reason") or "runtime_episode_resume_claim_failed"),
                }
            run_record = dict(claim.get("run_record") or run_record)
            resume_request = self._build_runtime_handoff_resume_chat_request(run_record, episode=episode)
            try:
                scheduled_run_id = self._schedule_chat_run(
                    resume_request,
                    transport="system_resume",
                    run_id=str(run_record["id"]),
                )
            except Exception as exc:
                run_service.update_metadata_key_if_state(
                    run_id,
                    key=RUNTIME_EPISODE_RESUME_METADATA_KEY,
                    expected_state="scheduled",
                    next_value={
                        **scheduled_marker,
                        "state": "waiting",
                        "lastError": "chat_scheduler_raised",
                    },
                    expected_status="running",
                )
                self._emit_resume_event(
                    run_record,
                    "run.resume.not_scheduled",
                    {
                        "resumeMode": "chat",
                        "transport": "system_resume",
                        "reason": "chat_scheduler_raised",
                        "runtimeEpisodeId": episode_id,
                        "runtimeEpisodeState": episode_state,
                    },
                )
                return {
                    "resume_mode": "chat",
                    "resume_scheduled": False,
                    "resume_error": f"chat_scheduler_raised:{type(exc).__name__}",
                }
            scheduled = bool(scheduled_run_id)
            if not scheduled:
                run_service.update_metadata_key_if_state(
                    run_id,
                    key=RUNTIME_EPISODE_RESUME_METADATA_KEY,
                    expected_state="scheduled",
                    next_value={
                        **scheduled_marker,
                        "state": "waiting",
                        "lastError": "chat_scheduler_returned_empty_run_id",
                    },
                    expected_status="running",
                )
            self._emit_resume_event(
                run_record,
                "run.resume.scheduled" if scheduled else "run.resume.not_scheduled",
                {
                    "resumeMode": "chat",
                    "transport": "system_resume",
                    "resumeReason": "runtime_episode_terminal",
                    "scheduledRunId": scheduled_run_id or run_record.get("id"),
                    "runtimeEpisodeId": episode_id,
                    "runtimeEpisodeKind": episode.get("kind"),
                    "runtimeEpisodeState": episode_state,
                    **({} if scheduled else {"reason": "chat_scheduler_returned_empty_run_id"}),
                },
            )
            return {
                "resume_mode": "chat",
                "resume_scheduled": scheduled,
                "resumed_run_id": scheduled_run_id or run_record["id"],
                **({} if scheduled else {"resume_error": "chat_scheduler_returned_empty_run_id"}),
            }

    def recover_runtime_episode_resume_worker_failure(
        self,
        run_id: str,
        *,
        error_message: str = "",
    ) -> Dict[str, Any]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return {"resume_mode": None, "resume_scheduled": False, "resume_error": "run_id_missing"}
        run_record = db.get_run_record(normalized_run_id)
        if not run_record:
            return {"resume_mode": None, "resume_scheduled": False, "resume_error": "run_not_found"}
        if self._resume_mode_for_run(run_record) != "chat":
            return {
                "resume_mode": self._resume_mode_for_run(run_record),
                "resume_scheduled": False,
                "resume_error": "not_chat_run",
            }
        status = str(run_record.get("status") or "").strip()
        if status != "running":
            return {
                "resume_mode": "chat",
                "resume_scheduled": False,
                "resume_error": f"run_not_running:{status or 'unknown'}",
            }
        metadata = dict(run_record.get("metadata") or {})
        marker = (
            dict(metadata.get(RUNTIME_EPISODE_RESUME_METADATA_KEY) or {})
            if isinstance(metadata.get(RUNTIME_EPISODE_RESUME_METADATA_KEY), dict)
            else {}
        )
        marker_state = str(marker.get("state") or "").strip().lower()
        episode_id = str(marker.get("episodeId") or "").strip()
        if marker_state != "scheduled" or not episode_id:
            return {
                "resume_mode": "chat",
                "resume_scheduled": False,
                "resume_error": "runtime_episode_resume_not_scheduled",
            }
        try:
            crash_count = int(marker.get("workerCrashCount") or 0)
        except (TypeError, ValueError):
            crash_count = 0
        next_crash_count = crash_count + 1
        truncated_error = str(error_message or "").strip()[:500]
        if crash_count >= RUNTIME_EPISODE_RESUME_MAX_WORKER_RETRIES:
            failed_marker = {
                **marker,
                "state": "failed",
                "lastError": "chat_resume_worker_failed",
                "lastWorkerError": truncated_error,
                "workerCrashCount": next_crash_count,
            }
            run_service.update_metadata_key_if_state(
                normalized_run_id,
                key=RUNTIME_EPISODE_RESUME_METADATA_KEY,
                expected_state="scheduled",
                next_value=failed_marker,
                expected_status="running",
            )
            self._emit_resume_event(
                run_record,
                "run.resume.not_scheduled",
                {
                    "resumeMode": "chat",
                    "transport": "system_resume",
                    "resumeReason": "runtime_episode_terminal",
                    "runtimeEpisodeId": episode_id,
                    "reason": "chat_resume_worker_retry_limit_exceeded",
                    "workerCrashCount": next_crash_count,
                },
            )
            return {
                "resume_mode": "chat",
                "resume_scheduled": False,
                "resume_error": "chat_resume_worker_retry_limit_exceeded",
                "worker_crash_count": next_crash_count,
            }
        episode = db.get_runtime_episode(episode_id)
        if not episode:
            return {
                "resume_mode": "chat",
                "resume_scheduled": False,
                "resume_error": "runtime_episode_not_found_for_worker_recovery",
            }
        reset_marker = {
            **marker,
            "state": "waiting",
            "lastError": "chat_resume_worker_failed",
            "lastWorkerError": truncated_error,
            "workerCrashCount": next_crash_count,
        }
        reset = run_service.update_metadata_key_if_state(
            normalized_run_id,
            key=RUNTIME_EPISODE_RESUME_METADATA_KEY,
            expected_state="scheduled",
            next_value=reset_marker,
            expected_status="running",
        )
        if not bool(reset.get("updated")):
            return {
                "resume_mode": "chat",
                "resume_scheduled": False,
                "resume_error": str(reset.get("reason") or "runtime_episode_resume_reset_failed"),
            }
        result = self.schedule_runtime_episode_handoff_resume(episode)
        result["worker_recovery"] = True
        result["worker_crash_count"] = next_crash_count
        return result

    def _resume_from_approval(self, approval: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any] | None:
        approval_kind = self._approval_kind(approval)
        if approval_kind == "mcp_app_tool_call":
            return None
        run_record = db.get_run_record(approval.get("run_id", ""))
        if not run_record:
            return None
        if approval_kind == "spec_stage_approval":
            if self._schedule_chat_run is None:
                self._emit_resume_event(
                    run_record,
                    "run.resume.not_scheduled",
                    {
                        "resumeMode": "chat",
                        "approvalKind": approval_kind,
                        "approvalId": approval.get("id") or approval.get("approval_id"),
                        "reason": "chat_scheduler_unavailable",
                    },
                )
                return {"resume_mode": "chat", "resume_scheduled": False}
            request_payload = approval.get("request") if isinstance(approval.get("request"), dict) else {}
            resume_request = self._build_manual_resume_chat_request(
                run_record,
                spec_hint=request_payload,
            )
            has_spec_continuation = bool(
                isinstance(resume_request.resume_value, dict)
                and isinstance(resume_request.resume_value.get("specContinuation"), dict)
            )
            if not has_spec_continuation:
                self._emit_resume_event(
                    run_record,
                    "run.resume.not_scheduled",
                    {
                        "resumeMode": "chat",
                        "approvalKind": approval_kind,
                        "approvalId": approval.get("id") or approval.get("approval_id"),
                        "reason": "spec_continuation_unavailable",
                    },
                )
                return {
                    "resume_mode": "chat",
                    "resume_scheduled": False,
                    "spec_continuation": False,
                    "resume_error": "spec_continuation_unavailable",
                }
            self._emit_resume_event(
                run_record,
                "run.resume.scheduling",
                {
                    "resumeMode": "chat",
                    "approvalKind": approval_kind,
                    "approvalId": approval.get("id") or approval.get("approval_id"),
                    "transport": "system_resume",
                    "specContinuation": True,
                    "specId": request_payload.get("specId") or request_payload.get("spec_id"),
                    "stage": request_payload.get("stage"),
                },
            )
            scheduled_run_id = self._schedule_chat_run(
                resume_request,
                transport="system_resume",
                run_id=str(run_record["id"]),
            )
            scheduled = bool(scheduled_run_id)
            self._emit_resume_event(
                run_record,
                "run.resume.scheduled" if scheduled else "run.resume.not_scheduled",
                {
                    "resumeMode": "chat",
                    "approvalKind": approval_kind,
                    "approvalId": approval.get("id") or approval.get("approval_id"),
                    "transport": "system_resume",
                    "scheduledRunId": scheduled_run_id or run_record.get("id"),
                    "specContinuation": has_spec_continuation,
                    "specId": request_payload.get("specId") or request_payload.get("spec_id"),
                    "stage": request_payload.get("stage"),
                    **({} if scheduled else {"reason": "chat_scheduler_returned_empty_run_id"}),
                },
            )
            return {
                "resume_mode": "chat",
                "resume_scheduled": scheduled,
                "resumed_run_id": scheduled_run_id or run_record["id"],
                "spec_continuation": has_spec_continuation,
                **({} if scheduled else {"resume_error": "chat_scheduler_returned_empty_run_id"}),
            }
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
            return {"resume_mode": "automation_agent", "resume_scheduled": True}
        if self._schedule_chat_run is None:
            self._emit_resume_event(
                run_record,
                "run.resume.not_scheduled",
                {
                    "resumeMode": "chat",
                    "approvalKind": approval_kind,
                    "approvalId": approval.get("id") or approval.get("approval_id"),
                    "reason": "chat_scheduler_unavailable",
                },
            )
            return {"resume_mode": "chat", "resume_scheduled": False}
        resume_request = self._build_resume_chat_request(approval, response)
        if resume_request:
            scheduled_run_id = self._schedule_chat_run(
                resume_request,
                transport="system_resume",
                run_id=str(run_record["id"]),
            )
            self._emit_resume_event(
                run_record,
                "run.resume.scheduled" if scheduled_run_id else "run.resume.not_scheduled",
                {
                    "resumeMode": "chat",
                    "approvalKind": approval_kind,
                    "approvalId": approval.get("id") or approval.get("approval_id"),
                    "transport": "system_resume",
                    "scheduledRunId": scheduled_run_id or run_record.get("id"),
                    **({} if scheduled_run_id else {"reason": "chat_scheduler_returned_empty_run_id"}),
                },
            )
            return {
                "resume_mode": "chat",
                "resume_scheduled": bool(scheduled_run_id),
                "resumed_run_id": scheduled_run_id or run_record["id"],
            }
        return {"resume_mode": "chat", "resume_scheduled": False}

    def _resume_spec_revision(self, approval: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any] | None:
        run_record = db.get_run_record(str(approval.get("run_id") or ""))
        feedback = str((response or {}).get("answer") or (response or {}).get("reason") or "").strip()
        if not run_record or not feedback:
            return None
        if self._schedule_chat_run is None:
            self._emit_resume_event(
                run_record,
                "run.resume.not_scheduled",
                {
                    "resumeMode": "chat",
                    "resumeReason": "spec_revision_requested",
                    "approvalKind": "spec_stage_approval",
                    "approvalId": approval.get("id") or approval.get("approval_id"),
                    "reason": "chat_scheduler_unavailable",
                    "restoredStatus": "waiting_input",
                },
            )
            return {
                "resume_mode": "chat",
                "resume_scheduled": False,
                "resume_error": "chat_scheduler_unavailable",
            }
        resume_request = self._build_spec_revision_chat_request(approval, response)
        if resume_request is None:
            self._emit_resume_event(
                run_record,
                "run.resume.not_scheduled",
                {
                    "resumeMode": "chat",
                    "resumeReason": "spec_revision_requested",
                    "approvalKind": "spec_stage_approval",
                    "approvalId": approval.get("id") or approval.get("approval_id"),
                    "reason": "spec_revision_context_unavailable",
                    "restoredStatus": "waiting_input",
                },
            )
            return {
                "resume_mode": "chat",
                "resume_scheduled": False,
                "resume_error": "spec_revision_context_unavailable",
            }
        resume_transition = erc_kernel.resume_run(
            str(run_record["id"]),
            reason="spec_revision_requested",
        )
        if not resume_transition:
            return {
                "resume_mode": "chat",
                "resume_scheduled": False,
                "resume_error": "spec_revision_run_not_found",
            }
        try:
            scheduled_run_id = self._schedule_chat_run(
                resume_request,
                transport="system_resume",
                run_id=str(run_record["id"]),
            )
        except Exception as exc:
            reason = f"chat_scheduler_raised:{type(exc).__name__}"
            self._restore_waiting_input_after_revision_resume_failure(approval, reason=reason)
            return {
                "resume_mode": "chat",
                "resume_scheduled": False,
                "resume_error": reason,
                "resume_transition_event": resume_transition.get("transition_event"),
                "resume_command_event": resume_transition.get("command_event"),
            }
        scheduled = bool(scheduled_run_id)
        if not scheduled:
            self._restore_waiting_input_after_revision_resume_failure(
                approval,
                reason="chat_scheduler_returned_empty_run_id",
            )
        self._emit_resume_event(
            run_record,
            "run.resume.scheduled" if scheduled else "run.resume.not_scheduled",
            {
                "resumeMode": "chat",
                "resumeReason": "spec_revision_requested",
                "approvalKind": "spec_stage_approval",
                "approvalId": approval.get("id") or approval.get("approval_id"),
                "transport": "system_resume",
                "scheduledRunId": scheduled_run_id or run_record.get("id"),
                "specId": resume_request.data.spec_id if resume_request.data else None,
                "stage": (resume_request.resume_value or {}).get("specRevision", {}).get("stage"),
                **({} if scheduled else {"reason": "chat_scheduler_returned_empty_run_id"}),
            },
        )
        return {
            "resume_mode": "chat",
            "resume_scheduled": scheduled,
            "resumed_run_id": scheduled_run_id or run_record["id"],
            "spec_revision": True,
            "resume_transition_event": resume_transition.get("transition_event"),
            "resume_command_event": resume_transition.get("command_event"),
            **({} if scheduled else {"resume_error": "chat_scheduler_returned_empty_run_id"}),
        }

    def _resume_from_ask_user(self, interaction: Dict[str, Any], response: Dict[str, Any]) -> None:
        if self._schedule_chat_run is None:
            return
        resume_request = self._build_resume_chat_request_from_ask_user(interaction, response)
        if resume_request:
            self._schedule_chat_run(
                resume_request,
                transport="system_resume",
                run_id=str(interaction.get("run_id") or ""),
            )

    @staticmethod
    def _approval_kind(approval: Dict[str, Any]) -> str:
        request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
        return str(
            approval.get("approval_kind")
            or request.get("approvalKind")
            or request.get("approval_kind")
            or ""
        ).strip()

    def _preflight_spec_stage_approval(self, approval: Dict[str, Any]) -> Dict[str, Any]:
        request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
        run_record = db.get_run_record(str(approval.get("run_id") or ""))
        if not run_record:
            return {"ok": False, "kind": "run_not_found"}
        scope_payload = self._scope_payload_for_session(str(run_record.get("session_id") or ""))
        workspace_path = (
            str(request.get("workspacePath") or request.get("workspace_path") or "").strip()
            or self._workspace_path_for_run(run_record, scope_payload)
        )
        spec_id = str(request.get("specId") or request.get("spec_id") or "").strip()
        stage = str(request.get("stage") or request.get("specStage") or request.get("spec_stage") or "").strip().lower()
        if not workspace_path:
            return {"ok": False, "kind": "workspace_path_missing", "stage": stage, "specId": spec_id}
        if not spec_id:
            return {"ok": False, "kind": "spec_id_missing", "stage": stage}
        if not stage:
            return {"ok": False, "kind": "spec_stage_missing", "specId": spec_id}
        try:
            return spec_service.validate_stage_approval(
                workspace_path=workspace_path,
                spec_id=spec_id,
                stage=stage,
            )
        except Exception as exc:
            return {
                "ok": False,
                "kind": "spec_stage_validation_failed",
                "error": f"{type(exc).__name__}: {exc}",
                "specId": spec_id,
                "stage": stage,
                "workspacePath": workspace_path,
            }

    def _apply_spec_stage_approval(self, approval: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
        request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
        run_record = db.get_run_record(str(approval.get("run_id") or ""))
        if not run_record:
            return {"ok": False, "error": "run_not_found"}
        scope_payload = self._scope_payload_for_session(str(run_record.get("session_id") or ""))
        workspace_path = (
            str(request.get("workspacePath") or request.get("workspace_path") or "").strip()
            or self._workspace_path_for_run(run_record, scope_payload)
        )
        spec_id = str(request.get("specId") or request.get("spec_id") or "").strip()
        stage = str(request.get("stage") or request.get("specStage") or request.get("spec_stage") or "").strip().lower()
        if not workspace_path:
            return {"ok": False, "error": "workspace_path_missing"}
        if not spec_id:
            return {"ok": False, "error": "spec_id_missing"}
        if not stage:
            return {"ok": False, "error": "spec_stage_missing"}
        approval_id = str(approval.get("id") or approval.get("approval_id") or "").strip()
        approver = str(response.get("source") or response.get("approver") or "user").strip() or "user"
        comment = str(response.get("comment") or response.get("reason") or "approved via governance approval").strip()
        try:
            approved = spec_service.approve_stage(
                workspace_path=workspace_path,
                spec_id=spec_id,
                stage=stage,
                approver=f"{approver}:{approval_id}" if approval_id else approver,
                comment=comment,
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "specId": spec_id,
                "stage": stage,
                "workspacePath": workspace_path,
            }
        if not isinstance(approved, dict) or approved.get("ok") is False:
            payload = dict(approved or {})
            payload.update(
                {
                    "ok": False,
                    "specId": spec_id,
                    "stage": stage,
                    "workspacePath": workspace_path,
                    "approvalId": approval_id,
                }
            )
            return payload
        return {
            "ok": True,
            "specId": spec_id,
            "stage": stage,
            "workspacePath": workspace_path,
            "approvalId": approval_id,
            "nextStage": approved.get("nextStage") if isinstance(approved, dict) else None,
        }

    def _restore_waiting_approval_after_resume_failure(self, approval: Dict[str, Any], *, reason: str) -> None:
        run_id = str(approval.get("run_id") or "").strip()
        if not run_id:
            return
        run_record = db.get_run_record(run_id)
        if not run_record or str(run_record.get("status") or "") != "running":
            return
        approval_id = str(approval.get("id") or approval.get("approval_id") or "").strip()
        metadata = {
            "approval_resume_scheduled": False,
            "approval_resume_error": reason,
            "approval_id": approval_id,
            "approval_kind": self._approval_kind(approval),
        }
        transition = run_service.transition_run_if_status(
            run_id,
            expected_statuses={"running"},
            status="waiting_approval",
            metadata=metadata,
        )
        if not bool(transition.get("updated")):
            return
        run_record = dict(transition.get("run_record") or run_record)
        workflow_ledger_service.sync_run_status(
            run_id,
            run_status="waiting_approval",
            reason=reason,
            metadata=metadata,
        )
        self._emit_resume_event(
            run_record,
            "run.resume.not_scheduled",
            {
                "resumeMode": "chat",
                "approvalKind": self._approval_kind(approval),
                "approvalId": approval_id,
                "reason": reason,
                "restoredStatus": "waiting_approval",
            },
        )

    def _restore_waiting_input_after_revision_resume_failure(self, approval: Dict[str, Any], *, reason: str) -> None:
        run_id = str(approval.get("run_id") or "").strip()
        if not run_id:
            return
        run_record = db.get_run_record(run_id)
        if not run_record or str(run_record.get("status") or "") != "running":
            return
        approval_id = str(approval.get("id") or approval.get("approval_id") or "").strip()
        metadata = {
            "spec_revision_resume_scheduled": False,
            "spec_revision_resume_error": reason,
            "approval_id": approval_id,
            "approval_kind": self._approval_kind(approval),
        }
        transition = run_service.transition_run_if_status(
            run_id,
            expected_statuses={"running"},
            status="waiting_input",
            metadata=metadata,
        )
        if not bool(transition.get("updated")):
            return
        run_record = dict(transition.get("run_record") or run_record)
        workflow_ledger_service.sync_run_status(
            run_id,
            run_status="waiting_input",
            reason=reason,
            metadata=metadata,
        )
        self._emit_resume_event(
            run_record,
            "run.resume.not_scheduled",
            {
                "resumeMode": "chat",
                "resumeReason": "spec_revision_requested",
                "approvalKind": self._approval_kind(approval),
                "approvalId": approval_id,
                "reason": reason,
                "restoredStatus": "waiting_input",
            },
        )

    def _emit_resume_event(self, run_record: Dict[str, Any], topic: str, payload: Dict[str, Any]) -> None:
        session_id = str(run_record.get("session_id") or "").strip()
        run_id = str(run_record.get("id") or "").strip()
        if not session_id or not run_id:
            return
        try:
            db.add_runtime_event(
                build_runtime_event(
                    topic=topic,
                    session_id=session_id,
                    conversation_id=run_record.get("conversation_id") or session_id,
                    run_id=run_id,
                    payload=payload,
                    source={
                        "plane": "engine",
                        "component": "erc",
                        "node": "command_router",
                        "agent_id": None,
                    },
                )
            )
        except Exception:
            return

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
