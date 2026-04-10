from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage

from core.database import db
from erc.runtime_context import bind_runtime_context
from erc.safety_guardian import SafetyDecision, safety_guardian
from erc.kernel import erc_kernel
from erc.runtime_registry import runtime_registry
from core.runtime_contexts import (
    build_automation_context_blocks,
    build_automation_task_envelope,
    build_job_memory,
    build_recent_run_summaries,
    coerce_json_dict,
)
from core.storage import storage


def _slug(value: str, *, fallback: str = "automation") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._:-]+", "-", (value or "").strip()).strip("-").lower()
    return normalized or fallback


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


class AutomationRuntime:
    kind = "automation"

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "AutomationRuntime",
            "summary": "负责 Hook/Cron 自动化任务封装与调度上下文，不直接承担复杂业务编排。",
            "responsibilities": [
                "标准化 Hook/Cron 输入",
                "生成自动化会话上下文",
                "把自动化任务交给核心运行链执行",
            ],
            "routingKeywords": ["cron", "hook", "自动化", "定时任务", "系统触发"],
            "acceptedInputs": ["action envelope", "cron metadata", "hook payload"],
            "producedOutputs": ["automation session", "dispatch payload"],
            "ownedSteps": ["automation.prepare", "automation.dispatch"],
            "supportsPause": True,
            "supportsResume": False,
            "supportsApproval": True,
            "supportsRepair": False,
            "visibility": "internal",
            "promptHints": [
                "Hook/Cron 触发的任务先走 AutomationRuntime 整理上下文，不要直接让 Supervisor 处理原始事件噪音。",
            ],
            "capabilities": [
                {
                    "key": "automation.dispatch",
                    "label": "自动化任务分发",
                    "summary": "承接 Hook/Cron 事件并整理成标准执行请求。",
                    "accepts": ["event payload", "cron schedule context"],
                    "outputs": ["normalized automation request"],
                    "examples": ["定时唤醒任务", "生命周期钩子触发任务"],
                    "risk_level": "medium",
                }
            ],
        }

    def resolve_session_id(
        self,
        *,
        action_type: str,
        target: str,
        trigger_source: str | None,
        kwargs: Dict[str, Any],
    ) -> str:
        explicit = kwargs.get("session_id")
        if explicit:
            return str(explicit)

        if trigger_source == "cron":
            cron_job_id = kwargs.get("cron_job_id") or _slug(kwargs.get("task_name") or target)
            return f"cron:{cron_job_id}"

        event_name = kwargs.get("event_name")
        if event_name or str(trigger_source or "").startswith("hook"):
            hook_target = hashlib.md5(f"{action_type}:{target}".encode("utf-8")).hexdigest()[:10]
            parent_session_id = str(kwargs.get("parent_session_id") or "").strip()
            if parent_session_id:
                parent_key = hashlib.md5(parent_session_id.encode("utf-8")).hexdigest()[:10]
                return f"hook:{_slug(str(event_name or trigger_source or 'hook'))}:{hook_target}:{parent_key}"
            return f"hook:{_slug(str(event_name or trigger_source or 'hook'))}:{hook_target}"

        manual_key = hashlib.md5(
            json.dumps(
                {
                    "action_type": action_type,
                    "target": target,
                    "task_name": kwargs.get("task_name"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:10]
        return f"automation:{_slug(str(trigger_source or 'manual'))}:{manual_key}"

    def build_request_metadata(
        self,
        *,
        action_type: str,
        target: str,
        payload: Dict[str, Any],
        is_async: bool,
        trigger_source: str | None,
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        persisted_kwargs = {
            key: _jsonable(value)
            for key, value in kwargs.items()
            if key
            not in {
                "run_id",
                "session_id",
                "safety_override",
                "_declared_async",
            }
        }
        return {
            "runtime": "automation",
            "action_type": action_type,
            "action_target": target,
            "payload": _jsonable(payload or {}),
            "is_async": bool(is_async),
            "trigger_source": trigger_source,
            "task_name": kwargs.get("task_name") or f"{action_type}:{target}",
            "kwargs": persisted_kwargs,
        }

    def build_session_title(
        self,
        *,
        trigger_source: str | None,
        action_type: str,
        target: str,
        kwargs: Dict[str, Any],
    ) -> str:
        if trigger_source == "cron":
            return f"Cron · {kwargs.get('task_name') or kwargs.get('cron_job_id') or target}"
        if kwargs.get("event_name") or str(trigger_source or "").startswith("hook"):
            return f"Hook · {kwargs.get('event_name') or trigger_source}"
        return f"Automation · {action_type}:{target}"

    def _build_channel_instruction(
        self,
        *,
        trigger_reason: str,
        action_payload: Dict[str, Any],
    ) -> str:
        channel_id = action_payload.get("channel_id")
        chat_id = action_payload.get("chat_id")
        if not channel_id or "cron" not in str(trigger_reason or "").lower():
            return ""

        target_chat_info = f" and specific conversation ID '{chat_id}'" if chat_id else ""
        return (
            "[OUTBOUND CHANNEL]\n"
            "You have been awoken by a scheduled Cron job. You MUST use your external messaging or channel tool "
            f"to broadcast your final summary or result to the channel ID '{channel_id}'{target_chat_info} before finishing.\n"
            "[/OUTBOUND CHANNEL]"
        )

    def build_agent_execution_payload(
        self,
        *,
        target_graph_module_name: str,
        action_payload: Dict[str, Any],
        kwargs: Dict[str, Any],
        run_handle,
    ) -> Dict[str, Any]:
        trigger_reason = kwargs.get("event_name", kwargs.get("trigger", "unknown"))
        automation_session_id = kwargs.get("session_id") or run_handle.session_id
        context_session_id = kwargs.get("parent_session_id") or automation_session_id
        automation_session = db.get_session(automation_session_id)
        automation_metadata = coerce_json_dict(automation_session.get("metadata")) if automation_session else {}
        context_messages = db.get_messages(context_session_id)
        ctx_policy = storage.get_context_config()
        automation_policy = dict((ctx_policy.get("runtime_adapters") or {}).get("automation") or {})
        recent_limit = int(automation_policy.get("recent_run_limit") or 3)
        job_memory_limit = int(automation_policy.get("job_memory_limit") or 6)
        recent_summaries = build_recent_run_summaries(context_messages, limit=recent_limit)
        job_memory = automation_metadata.get("job_memory") or build_job_memory(context_messages, limit=job_memory_limit)
        context_blocks = build_automation_context_blocks(
            recent_summaries=recent_summaries,
            job_memory=job_memory,
        )
        task_name = kwargs.get("task_name") or f"agent:{target_graph_module_name}"
        trigger_label = "[Hook Trigger]" if str(trigger_reason).startswith("hook") else "[Automation Trigger]"
        channel_instruction = self._build_channel_instruction(
            trigger_reason=trigger_reason,
            action_payload=action_payload,
        )
        automation_id = (
            kwargs.get("cron_job_id")
            or kwargs.get("event_name")
            or kwargs.get("task_name")
            or target_graph_module_name
        )
        task_envelope = build_automation_task_envelope(
            trigger_label=trigger_label,
            task_description=task_name,
            payload=action_payload,
            channel_instruction=channel_instruction,
        )
        db.update_session_metadata(
            automation_session_id,
            {
                **automation_metadata,
                "automation_id": str(automation_id),
                "task_name": task_name,
                "job_memory": job_memory,
                "recent_run_summaries": recent_summaries,
                "action_payload_preview": action_payload,
                "parent_session_id": kwargs.get("parent_session_id"),
                "context_session_id": context_session_id,
            },
        )
        return {
            "messages": [
                HumanMessage(
                    content=task_envelope,
                    additional_kwargs={
                        "exec_context": kwargs,
                        "payload": action_payload,
                        "session_id": automation_session_id,
                        "parent_session_id": kwargs.get("parent_session_id"),
                        "context_session_id": context_session_id,
                        "user_id": kwargs.get("user_id"),
                        "project_id": kwargs.get("project_id"),
                        "workspace_id": kwargs.get("workspace_id"),
                        "workspace_path": kwargs.get("workspace_path"),
                        "workflow_id": kwargs.get("workflow_id"),
                        "resolved_scope": kwargs.get("resolved_scope"),
                        "scope_source": kwargs.get("scope_source"),
                        "scope_chain": list(kwargs.get("scope_chain") or []),
                        "recent_run_summaries": recent_summaries,
                        "job_memory": job_memory,
                        "context_adapter_blocks": context_blocks,
                        "automation": {
                            "kind": "hook" if str(trigger_reason).startswith("hook") else "automation",
                            "id": str(automation_id),
                            "session_id": automation_session_id,
                            "run_id": run_handle.run_id,
                        },
                    },
                )
            ],
            "hook_event": trigger_reason,
            "trigger_reason": trigger_reason,
            "hook_context": kwargs,
            "action_payload": action_payload,
        }

    def refresh_job_context(
        self,
        *,
        session_id: str,
    ) -> Dict[str, Any]:
        existing_session = db.get_session(session_id)
        existing_metadata = coerce_json_dict(existing_session.get("metadata")) if existing_session else {}
        updated_messages = db.get_messages(session_id)
        ctx_policy = storage.get_context_config()
        automation_policy = dict((ctx_policy.get("runtime_adapters") or {}).get("automation") or {})
        recent_limit = int(automation_policy.get("recent_run_limit") or 3)
        job_memory_limit = int(automation_policy.get("job_memory_limit") or 6)
        updated_recent_summaries = build_recent_run_summaries(updated_messages, limit=recent_limit)
        updated_job_memory = build_job_memory(updated_messages, limit=job_memory_limit)
        db.update_session_metadata(
            session_id,
            {
                **existing_metadata,
                "job_memory": updated_job_memory,
                "recent_run_summaries": updated_recent_summaries,
            },
        )
        return {
            "job_memory": updated_job_memory,
            "recent_run_summaries": updated_recent_summaries,
        }

    def begin_run(
        self,
        *,
        action_type: str,
        target: str,
        payload: Dict[str, Any],
        trigger_source: str | None,
        is_async: bool,
        kwargs: Dict[str, Any],
        run_id: str | None = None,
    ):
        session_id = self.resolve_session_id(
            action_type=action_type,
            target=target,
            trigger_source=trigger_source,
            kwargs=kwargs,
        )
        existing_session = db.get_session(session_id) or {}
        session_metadata = {
            "runtime": "automation",
            "trigger_source": trigger_source,
            "action_type": action_type,
            "action_target": target,
        }
        # Hook/automation can piggyback on an existing chat session. In that case,
        # keep the human-facing chat title instead of replacing it with an internal label.
        existing_title = str(existing_session.get("title") or "").strip()
        should_preserve_existing_title = bool(kwargs.get("session_id")) and bool(existing_title)
        db.create_or_update_session(
            session_id=session_id,
            title=existing_title if should_preserve_existing_title else self.build_session_title(
                trigger_source=trigger_source,
                action_type=action_type,
                target=target,
                kwargs=kwargs,
            ),
            user_id=str(kwargs.get("user_id") or "system"),
            metadata=session_metadata,
        )

        return erc_kernel.submit_run(
            session_id=session_id,
            conversation_id=session_id,
            user_id=str(kwargs.get("user_id") or "system"),
            runtime_kind="automation",
            trigger_source=trigger_source,
            agent_id=target if action_type == "agent" else None,
            metadata=self.build_request_metadata(
                action_type=action_type,
                target=target,
                payload=payload,
                is_async=is_async,
                trigger_source=trigger_source,
                kwargs=kwargs,
            ),
            run_id=run_id,
            initial_status="queued",
            component="automation_runtime",
            node="run_manager",
        )

    def begin_or_attach_run(
        self,
        *,
        action_type: str,
        target: str,
        payload: Dict[str, Any],
        trigger_source: str | None,
        is_async: bool,
        kwargs: Dict[str, Any],
    ):
        run_handle = None
        existing_run_id = kwargs.get("run_id")
        if existing_run_id:
            run_handle = self.attach_run(str(existing_run_id))
        if run_handle is None:
            run_handle = self.begin_run(
                action_type=action_type,
                target=target,
                payload=payload,
                trigger_source=trigger_source,
                is_async=is_async,
                kwargs=kwargs,
                run_id=existing_run_id,
            )
            run_handle.emit(
                "run.created",
                {
                    "run_id": run_handle.run_id,
                    "transport": "automation",
                    "trigger_source": trigger_source,
                    "action_type": action_type,
                    "action_target": target,
                },
            )
        return run_handle

    def run_preflight(
        self,
        *,
        run_handle,
        trigger_source: str | None,
        user_id: str | None,
    ) -> SafetyDecision:
        decision = safety_guardian.preflight_runtime(
            runtime_kind="automation",
            trigger_source=trigger_source,
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            user_id=user_id,
        )
        run_handle.emit("safety.preflight.checked", decision.to_payload())
        return decision

    def build_runtime_review_request(
        self,
        *,
        trigger_source: str | None,
        decision: SafetyDecision,
        subject: str,
    ) -> Dict[str, Any]:
        return safety_guardian.build_runtime_preflight_request(
            runtime_kind="automation",
            trigger_source=trigger_source or "manual",
            decision=decision,
            subject=subject,
        )

    def build_action_review_request(
        self,
        *,
        action_type: str,
        target: str,
        trigger_source: str | None,
        decision: SafetyDecision,
        task_name: str,
    ) -> Dict[str, Any]:
        return {
            "question": f"Safety Guardian 检测到自动化任务存在风险，是否继续执行？\n\n任务：{task_name}",
            "prompt": f"Safety Guardian 检测到自动化任务存在风险，是否继续执行？\n\n任务：{task_name}",
            "approvalKind": "safety_review",
            "automation": {
                "action_type": action_type,
                "target": target,
                "trigger_source": trigger_source,
                "task_name": task_name,
            },
            "safety": decision.to_payload(),
        }

    def _guard_payload(
        self,
        *,
        status: str,
        decision: SafetyDecision,
        run_handle,
        approval_id: str | None = None,
        guard_stage: str,
    ) -> Dict[str, Any]:
        payload = {
            "status": status,
            "guarded": True,
            "guard_stage": guard_stage,
            "verdict": decision.verdict,
            "reason": decision.reason,
            "risk_code": decision.risk_code,
            "details": decision.details,
            "run_id": run_handle.run_id,
            "session_id": run_handle.session_id,
        }
        if approval_id:
            payload["approval_id"] = approval_id
        return payload

    def handle_preflight_decision(
        self,
        *,
        run_handle,
        trigger_source: str | None,
        decision: SafetyDecision,
        task_name: str,
        safety_override: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if decision.is_allow() or (safety_override and decision.is_review()):
            return None

        error_message = f"Safety Guardian {decision.verdict}: {decision.reason}"
        if decision.is_review():
            approval = run_handle.request_approval(
                approval_kind="safety_review",
                request=self.build_runtime_review_request(
                    trigger_source=trigger_source,
                    decision=decision,
                    subject=task_name,
                ),
            )
            if str(approval.get("status") or "").strip().lower() != "pending":
                run_handle.emit(
                    "safety.preflight.auto_approved",
                    {
                        "approval_id": approval.get("approval_id"),
                        "policySource": approval.get("policySource"),
                        "reason": decision.reason,
                        "risk_code": decision.risk_code,
                        "details": decision.details,
                    },
                )
                return None
            return self._guard_payload(
                status="review_required",
                decision=decision,
                run_handle=run_handle,
                approval_id=approval.get("approval_id"),
                guard_stage="preflight",
            )

        run_handle.emit(
            "safety.preflight.blocked",
            {
                "reason": decision.reason,
                "risk_code": decision.risk_code,
                "details": decision.details,
            },
        )
        run_handle.fail(error_message, node="safety_guardian")
        return self._guard_payload(
            status="blocked",
            decision=decision,
            run_handle=run_handle,
            guard_stage="preflight",
        )

    def handle_action_decision(
        self,
        *,
        run_handle,
        action_type: str,
        target: str,
        trigger_source: str | None,
        decision: SafetyDecision,
        task_name: str,
        safety_override: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if decision.is_allow() or (safety_override and decision.is_review()):
            return None

        error_message = f"Safety Guardian {decision.verdict}: {decision.reason}"
        if decision.is_review():
            approval = run_handle.request_approval(
                approval_kind="safety_review",
                request=self.build_action_review_request(
                    action_type=action_type,
                    target=target,
                    trigger_source=trigger_source,
                    decision=decision,
                    task_name=task_name,
                ),
            )
            if str(approval.get("status") or "").strip().lower() != "pending":
                run_handle.emit(
                    "safety.action.auto_approved",
                    {
                        "approval_id": approval.get("approval_id"),
                        "policySource": approval.get("policySource"),
                        "reason": decision.reason,
                        "risk_code": decision.risk_code,
                        "details": decision.details,
                        "action_type": action_type,
                        "target": target,
                    },
                )
                return None
            return self._guard_payload(
                status="review_required",
                decision=decision,
                run_handle=run_handle,
                approval_id=approval.get("approval_id"),
                guard_stage="action",
            )

        run_handle.emit(
            "safety.action.blocked",
            {
                "reason": decision.reason,
                "risk_code": decision.risk_code,
                "details": decision.details,
            },
        )
        run_handle.fail(error_message, node="safety_guardian")
        return self._guard_payload(
            status="blocked",
            decision=decision,
            run_handle=run_handle,
            guard_stage="action",
        )

    def bind_execution_context(
        self,
        *,
        runtime_kind: str,
        trigger_source: str | None,
        run_handle,
        user_id: str | None,
        project_id: str | None,
        workspace_id: str | None,
    ):
        return bind_runtime_context(
            runtime_kind=runtime_kind,
            trigger_source=trigger_source,
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def observe_post_action(
        self,
        *,
        task_name: str,
        action_type: str,
        target: str,
        trigger_source: str | None,
        status: str,
        run_handle,
        runtime_kind: str = "automation",
        user_id: str | None = None,
    ) -> None:
        safety_guardian.observe_post_action(
            action_family="automation_action",
            summary=f"自动化任务已执行：{task_name}",
            details={
                "action_type": action_type,
                "target": target,
                "trigger_source": trigger_source,
                "status": status,
            },
            runtime_context={
                "runtime_kind": runtime_kind,
                "trigger_source": trigger_source,
                "session_id": run_handle.session_id,
                "run_id": run_handle.run_id,
                "user_id": user_id,
            },
        )

    def attach_run(self, run_id: str):
        return erc_kernel.attach_run(
            run_id,
            component="automation_runtime",
            node="resume_manager",
        )

    def build_invocation_from_run(self, run_record: Dict[str, Any]) -> Dict[str, Any]:
        metadata = dict(run_record.get("metadata") or {})
        kwargs = dict(metadata.get("kwargs") or {})
        kwargs["session_id"] = run_record.get("session_id")
        return {
            "action_type": metadata.get("action_type") or "command",
            "target": metadata.get("action_target") or "",
            "payload": dict(metadata.get("payload") or {}),
            "is_async": bool(metadata.get("is_async")),
            "trigger_source": metadata.get("trigger_source") or run_record.get("trigger_source"),
            "task_name": metadata.get("task_name") or f"{metadata.get('action_type') or 'command'}:{metadata.get('action_target') or ''}",
            "kwargs": kwargs,
        }


automation_runtime = runtime_registry.register(AutomationRuntime())
