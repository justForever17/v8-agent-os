import os
import importlib
import sys
import time
import uuid
import asyncio
from typing import Dict, Any, Optional
from core.knowledge_db import knowledge_db
from core.database import db
from core.runtime_signal_ingress import build_normalized_signal_payload
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from erc.event_bus import event_bus
from erc.models import RuntimeSource
from erc.runtime_context import bind_runtime_context
from erc.runtime_control import apply_control_signal, consume_stop_signal
from erc.side_effect_idempotency import side_effect_idempotency_service
from erc.runtime_stability import runtime_stability_service
from erc.run_service import run_service
from erc.session_admission_service import session_admission_service
from erc.safety_guardian import safety_guardian
from erc.snapshot_service import snapshot_service
from erc.workflow_ledger import workflow_ledger_service
from runtimes.automation.runtime import automation_runtime
from core.runtime_episodes import build_runtime_episode, enqueue_runtime_episode
from core.process_launch import run_windowless_bounded

_AUTOMATION_COMMAND_TIMEOUT_SECONDS = 60

class ActionExecutor:
    """
    A generic executor for running Background/Scheduled/Hook Actions.
    Action types supported: 'command', 'python', 'agent', 'rpa'.
    """
    _active_targets = set()
    _main_loop = None  # Reference to the main FastAPI event loop (captured on first async execute)

    @staticmethod
    def _uses_workflow_envelope(trigger_source: str | None, kwargs: Dict[str, Any]) -> bool:
        trigger = str(trigger_source or "").strip().lower()
        return trigger == "cron" or trigger.startswith("hook") or bool(kwargs.get("event_name"))

    @staticmethod
    def _normalize_rpa_route_inputs(target: str, payload: Optional[Dict[str, Any]], kwargs: Dict[str, Any]) -> Dict[str, Any]:
        source_payload = dict(payload or {})
        nested_payload = source_payload.get("payload") if isinstance(source_payload.get("payload"), dict) else {}
        merged = {**dict(nested_payload or {}), **{k: v for k, v in source_payload.items() if k != "payload"}}
        target_value = str(target or merged.get("target") or "").strip()
        inputs: Dict[str, Any] = {
            "mode": str(merged.get("mode") or merged.get("executionMode") or "execute").strip() or "execute",
            "variables": merged.get("variables") if isinstance(merged.get("variables"), dict) else {},
            "nonChatRun": True,
            "triggerSource": kwargs.get("trigger") or kwargs.get("trigger_source"),
            "cronJobId": kwargs.get("cron_job_id") or merged.get("cronJobId"),
            "hookName": kwargs.get("hook_name") or merged.get("hookName"),
            "eventName": kwargs.get("event_name") or merged.get("eventName"),
            "sessionId": kwargs.get("session_id"),
            "runId": kwargs.get("run_id"),
            "source": "automation",
        }
        for key in ("templateId", "draftId", "scriptId", "robotFile", "traceRunId", "traceRunIds", "runIds", "cwd", "outputDir", "timeoutMs"):
            if merged.get(key) not in (None, ""):
                inputs[key] = merged.get(key)
        lowered = target_value.lower()
        if target_value and not any(inputs.get(key) for key in ("templateId", "draftId", "scriptId", "robotFile")):
            if lowered.startswith("rpa:template:"):
                inputs["templateId"] = target_value.split(":", 2)[2]
            elif lowered.startswith("template:"):
                inputs["templateId"] = target_value.split(":", 1)[1]
            elif lowered.startswith("rpa:draft:"):
                inputs["draftId"] = target_value.split(":", 2)[2]
            elif lowered.startswith("draft:"):
                inputs["draftId"] = target_value.split(":", 1)[1]
            elif lowered.startswith("script:"):
                inputs["scriptId"] = target_value.split(":", 1)[1]
            elif lowered.startswith("robot:"):
                inputs["robotFile"] = target_value.split(":", 1)[1]
            elif lowered.endswith(".robot"):
                inputs["robotFile"] = target_value
            else:
                inputs["draftId"] = target_value
        if not any(inputs.get(key) for key in ("templateId", "draftId", "scriptId", "robotFile", "traceRunId", "traceRunIds", "runIds")):
            inputs["mode"] = str(merged.get("mode") or "prepare")
        return {k: v for k, v in inputs.items() if v not in (None, "", [], {})}

    @classmethod
    def _build_rpa_route_need(
        cls,
        *,
        target: str,
        payload: Optional[Dict[str, Any]],
        kwargs: Dict[str, Any],
        task_name: str,
        trigger_source: str | None,
        session_id: str,
        run_id: str,
    ) -> Dict[str, Any]:
        route_kwargs = {**kwargs, "trigger_source": trigger_source, "session_id": session_id, "run_id": run_id}
        inputs = cls._normalize_rpa_route_inputs(target, payload, route_kwargs)
        source = "cron" if str(trigger_source or "").lower() == "cron" else "hook" if str(trigger_source or "").lower().startswith("hook") else "automation"
        idempotency_key = str(
            (payload or {}).get("idempotencyKey")
            or kwargs.get("idempotency_key")
            or f"{source}:rpa:{target}:{inputs.get('templateId') or inputs.get('draftId') or inputs.get('robotFile') or inputs.get('scriptId') or run_id}"
        )
        return {
            "kind": "rpa",
            "source": source,
            "reason": task_name or target or "rpa automation",
            "inputs": inputs,
            "requiredRuntimeAccess": ["rpa.core"],
            "targetKind": "local_runtime",
            "targetId": "rpa",
            "idempotencyKey": idempotency_key,
        }

    @classmethod
    def _execute_rpa_route(
        cls,
        target: str,
        payload: Dict[str, Any],
        *,
        run_handle,
        task_name: str,
        trigger_source: str | None,
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        need = cls._build_rpa_route_need(
            target=target,
            payload=payload,
            kwargs=kwargs,
            task_name=task_name,
            trigger_source=trigger_source,
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
        )
        priority = int((payload or {}).get("priority") or kwargs.get("priority") or 0)
        episode = build_runtime_episode(
            need=need,
            kind="rpa",
            state="queued",
            required_runtime_access=["rpa.core"],
            extra={"targetKind": "local_runtime", "targetId": "rpa"},
        )
        persisted = enqueue_runtime_episode(
            episode,
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            priority=priority,
        )
        run_handle.emit(
            "runtime.episode.queued",
            {
                "episode": persisted,
                "episodeId": persisted.get("episodeId") or persisted.get("needId"),
                "kind": "rpa",
                "source": need.get("source"),
                "target": target,
            },
        )
        return {
            "status": "queued",
            "actionType": "rpa",
            "target": target,
            "episodeId": persisted.get("episodeId") or persisted.get("needId"),
            "run_id": run_handle.run_id,
            "session_id": run_handle.session_id,
            "runtimeKind": "rpa",
        }

    @classmethod
    def _build_automation_trigger_payload(
        cls,
        *,
        run_handle,
        action_type: str,
        target: str,
        task_name: str,
        trigger_source: str | None,
        is_async: bool,
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        trigger_value = str(trigger_source or "").strip().lower()
        if trigger_value == "cron":
            source_kind = "cron"
            signal_kind = "scheduled_trigger"
        elif trigger_value.startswith("hook"):
            source_kind = "hook"
            signal_kind = "hook_trigger"
        else:
            source_kind = "automation"
            signal_kind = "trigger"
        summary = task_name or f"{action_type}:{target}"
        payload = build_normalized_signal_payload(
            source_kind=source_kind,
            signal_kind=signal_kind,
            owner_runtime="automation",
            summary=summary,
            related_session_id=run_handle.session_id,
            related_run_id=run_handle.run_id,
            task_relevant=True,
            blocking=False,
            metadata={
                "trigger_source": trigger_source,
                "action_type": action_type,
                "target": target,
                "task_name": task_name,
                "event_name": kwargs.get("event_name"),
                "cron_job_id": kwargs.get("cron_job_id"),
                "hook_name": kwargs.get("hook_name"),
                "hook_target": kwargs.get("hook_target"),
                "parent_session_id": kwargs.get("parent_session_id"),
                "is_async": bool(is_async),
                "wake_ingress_envelope": kwargs.get("wake_ingress_envelope") or {},
            },
        )
        payload.update(
            {
                "trigger_source": trigger_source,
                "action_type": action_type,
                "target": target,
                "task_name": task_name,
                "is_async": bool(is_async),
                "wakeIngressEnvelope": kwargs.get("wake_ingress_envelope") or {},
                "triggerKind": (
                    (kwargs.get("wake_ingress_envelope") or {}).get("triggerKind")
                    if isinstance(kwargs.get("wake_ingress_envelope"), dict)
                    else None
                ),
                "targetBinding": (
                    (kwargs.get("wake_ingress_envelope") or {}).get("targetBinding")
                    if isinstance(kwargs.get("wake_ingress_envelope"), dict)
                    else None
                ),
                "recoveryAnchor": (
                    (kwargs.get("wake_ingress_envelope") or {}).get("recoveryAnchor")
                    if isinstance(kwargs.get("wake_ingress_envelope"), dict)
                    else None
                ),
            }
        )
        return payload

    @classmethod
    def _activate_automation_stage(
        cls,
        *,
        run_id: str,
        trigger_source: str | None,
        kwargs: Dict[str, Any],
        stage: str,
        title: str,
        input_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not cls._uses_workflow_envelope(trigger_source, kwargs):
            return
        workflow_ledger_service.activate_runtime_step(
            run_id,
            owner_runtime="automation",
            step_key=f"automation.{stage}",
            title=title,
            owner_agent_id="automation_runtime",
            input_payload=input_payload or {},
        )

    @classmethod
    def _consume_automation_control(
        cls,
        *,
        run_handle,
        action_type: str,
        target: str,
        trigger_source: str | None,
        kwargs: Dict[str, Any],
        stage: str,
    ) -> Optional[Dict[str, Any]]:
        signal = consume_stop_signal(run_handle.run_id)
        if signal is None:
            return None
        payload = apply_control_signal(
            run_handle,
            signal=signal,
            runtime_kind="automation",
            node="automation_runtime",
            extras={
                "actionType": action_type,
                "target": target,
                "stage": stage,
            },
        )
        cls._activate_automation_stage(
            run_id=run_handle.run_id,
            trigger_source=trigger_source,
            kwargs=kwargs,
            stage="finalize",
            title="Automation 收尾",
            input_payload={"control": payload, "stage": stage},
        )
        return {
            "status": payload["status"],
            "run_id": run_handle.run_id,
            "session_id": run_handle.session_id,
            "control": payload,
            "action_type": action_type,
            "target": target,
        }

    @classmethod
    def execute(cls, action_type: str, target: str, is_async: bool = False, payload: Optional[Dict[str, Any]] = None, **kwargs):
        """
        Executes the given action. If is_async and action type supports it natively (like agent),
        or if we want to fire-and-forget, this returns a coroutine or spawns a background task.
        However, for simpler synchronous calling context, this method handles scheduling if `is_async=True`.
        """
        payload = payload or {}
        
        kwargs = dict(kwargs)
        kwargs["_declared_async"] = is_async
            
        if is_async:
            if action_type == "agent":
                try:
                    loop = asyncio.get_running_loop()
                    cls._main_loop = loop  # Save main loop reference
                    loop.create_task(cls._execute_agent_async(target, payload, **kwargs))
                except RuntimeError:
                    # If no event loop is running, run it synchronously in a new event loop
                    asyncio.run(cls._execute_agent_async(target, payload, **kwargs))
            else:
                # Wrap synchronous command/python in thread to not block event loop if needed
                try:
                    loop = asyncio.get_running_loop()
                    cls._main_loop = loop  # Save main loop reference
                    loop.run_in_executor(None, cls._execute_sync, action_type, target, payload, kwargs)
                except RuntimeError:
                    cls._execute_sync(action_type, target, payload, kwargs)
        else:
            return cls._execute_sync(action_type, target, payload, kwargs)

    @classmethod
    def _execute_sync(cls, action_type: str, target: str, payload: Dict[str, Any], kwargs: Dict[str, Any]):
        trigger_source = kwargs.get("trigger", "manual")
        
        # Mutex Lock for Cron Jobs
        lock_key = None
        if trigger_source == "cron":
            lock_key = f"cron:{kwargs.get('cron_job_id', target)}"
            if lock_key in cls._active_targets:
                cls._log_audit_event(trigger_source, kwargs.get("task_name", "Cron Task"), target, "SKIPPED", "Mutex lock: already running")
                print(f"[ActionExecutor] Skipping {target} because it is already running (locked by {lock_key}).")
                return
            cls._active_targets.add(lock_key)

        log_id = str(uuid.uuid4())
        task_name = kwargs.get("task_name", f"{action_type}:{target}")
        run_handle = automation_runtime.begin_or_attach_run(
            action_type=action_type,
            target=target,
            payload=payload,
            trigger_source=trigger_source,
            is_async=bool(kwargs.get("_declared_async", False)),
            kwargs=kwargs,
        )
        cls._activate_automation_stage(
            run_id=run_handle.run_id,
            trigger_source=trigger_source,
            kwargs=kwargs,
            stage="prepare",
            title="Automation 准备",
            input_payload={
                "actionType": action_type,
                "target": target,
                "triggerSource": trigger_source,
                "taskName": task_name,
            },
        )
        run_handle.emit(
            "automation.trigger.normalized",
            cls._build_automation_trigger_payload(
                run_handle=run_handle,
                action_type=action_type,
                target=target,
                task_name=task_name,
                trigger_source=trigger_source,
                is_async=bool(kwargs.get("_declared_async", False)),
                kwargs=kwargs,
            ),
        )
        lane_policy = runtime_stability_service.session_lane_policy()
        lane_decision = session_admission_service.acquire(
            run_handle.session_id,
            run_handle.run_id,
            policy=lane_policy,
            runtime_kind="automation",
            metadata={
                "triggerSource": trigger_source,
                "actionType": action_type,
                "target": target,
            },
        )
        if not lane_decision.acquired:
            error_message = (
                f"Session lane busy: session '{run_handle.session_id}' is already running "
                f"'{lane_decision.rejected_by_run_id or lane_decision.active_run_id}'."
            )
            run_handle.emit(
                "run.lane.rejected",
                {
                    "policy": lane_decision.policy,
                    "busy_run_id": lane_decision.rejected_by_run_id or lane_decision.active_run_id,
                    "session_id": run_handle.session_id,
                },
            )
            run_service.transition_run(run_handle.run_id, status="cancelled", error_message=error_message)
            return {
                "status": "rejected",
                "reason": error_message,
                "run_id": run_handle.run_id,
                "session_id": run_handle.session_id,
            }
        if lane_decision.waited:
            run_handle.emit(
                "run.lane.queued",
                {
                    "policy": lane_decision.policy,
                    "blocked_by_run_id": lane_decision.active_run_id,
                    "interrupted_run_id": lane_decision.interrupted_run_id,
                },
            )
            run_handle.emit(
                "run.liveness.blocked",
                {
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": f"lane_busy:{lane_decision.active_run_id}",
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
            )
        run_handle.emit(
            "run.lane.acquired",
            {
                "policy": lane_decision.policy,
                "waited": lane_decision.waited,
                "previous_run_id": lane_decision.active_run_id,
                "interrupted_run_id": lane_decision.interrupted_run_id,
            },
        )
        if lane_decision.waited:
            run_handle.emit(
                "run.liveness.recovered",
                {
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": None,
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
            )
        preflight_decision = automation_runtime.run_preflight(
            run_handle=run_handle,
            trigger_source=trigger_source,
            user_id=kwargs.get("user_id"),
        )
        
        # 记录开始状态
        knowledge_db.log_execution(
            log_id=log_id,
            task_name=task_name,
            action_type=action_type,
            action_target=target,
            trigger_source=trigger_source,
            status="running",
            payload=payload
        )
        
        start_time = time.time()
        error_message = None
        status = "success"
        execution_receipt = None

        try:
            preflight_result = automation_runtime.handle_preflight_decision(
                run_handle=run_handle,
                trigger_source=trigger_source,
                decision=preflight_decision,
                task_name=task_name,
                safety_override=bool(kwargs.get("safety_override")),
            )
            if preflight_result is not None:
                status = str(preflight_result.get("status") or status)
                error_message = preflight_result.get("reason")
                cls._activate_automation_stage(
                    run_id=run_handle.run_id,
                    trigger_source=trigger_source,
                    kwargs=kwargs,
                    stage="finalize",
                    title="Automation 收尾",
                    input_payload={"result": preflight_result, "stage": "preflight"},
                )
                return preflight_result

            safety_decision = safety_guardian.assess_automation_action(
                action_type=action_type,
                target=target,
                payload=payload,
                trigger_source=trigger_source,
            )
            action_result = automation_runtime.handle_action_decision(
                run_handle=run_handle,
                action_type=action_type,
                target=target,
                trigger_source=trigger_source,
                decision=safety_decision,
                task_name=task_name,
                safety_override=bool(kwargs.get("safety_override")),
            )
            if action_result is not None:
                status = str(action_result.get("status") or status)
                error_message = action_result.get("reason")
                cls._activate_automation_stage(
                    run_id=run_handle.run_id,
                    trigger_source=trigger_source,
                    kwargs=kwargs,
                    stage="finalize",
                    title="Automation 收尾",
                    input_payload={"result": action_result, "stage": "action_guard"},
                )
                return action_result

            controlled = cls._consume_automation_control(
                run_handle=run_handle,
                action_type=action_type,
                target=target,
                trigger_source=trigger_source,
                kwargs=kwargs,
                stage="before_execute",
            )
            if controlled is not None:
                status = str(controlled.get("status") or status)
                error_message = str((controlled.get("control") or {}).get("reason") or "")
                return controlled

            run_handle.transition("running", reason=trigger_source, node="automation_runtime")
            cls._activate_automation_stage(
                run_id=run_handle.run_id,
                trigger_source=trigger_source,
                kwargs=kwargs,
                stage="execute",
                title="Automation 执行",
                input_payload={
                    "actionType": action_type,
                    "target": target,
                    "taskName": task_name,
                },
            )
            if cls._uses_workflow_envelope(trigger_source, kwargs):
                execution_receipt = cls._begin_execution_side_effect(
                    run_handle=run_handle,
                    action_type=action_type,
                    target=target,
                    trigger_source=trigger_source,
                    action_payload=payload,
                    kwargs=kwargs,
                )
                if not execution_receipt.execute:
                    if execution_receipt.requires_reconciliation:
                        status = "review_required"
                        error_message = "外部副作用结果未知，必须核对目标系统状态后再决定完成或重试。"
                        run_handle.fail(error_message, node="automation_runtime")
                        return {
                            "status": status,
                            "run_id": run_handle.run_id,
                            "session_id": run_handle.session_id,
                            "error": "side_effect_reconciliation_required",
                            "receipt": execution_receipt.as_dict(),
                        }
                    status = "skipped_duplicate"
                    cls._activate_automation_stage(
                        run_id=run_handle.run_id,
                        trigger_source=trigger_source,
                        kwargs=kwargs,
                        stage="finalize",
                        title="Automation 收尾",
                        input_payload={"status": status, "receipt": execution_receipt.as_dict()},
                    )
                    run_handle.complete(reason="side_effect_deduplicated", node="automation_runtime")
                    return {
                        "status": status,
                        "run_id": run_handle.run_id,
                        "session_id": run_handle.session_id,
                        "receipt": execution_receipt.as_dict(),
                    }
            if action_type == "command":
                with automation_runtime.bind_execution_context(
                    runtime_kind="automation",
                    trigger_source=trigger_source,
                    run_handle=run_handle,
                    user_id=kwargs.get("user_id"),
                    project_id=kwargs.get("project_id"),
                    workspace_id=kwargs.get("workspace_id"),
                ):
                    result = cls._execute_command(target, payload, **kwargs)
            elif action_type == "python":
                kwargs.setdefault("run_id", run_handle.run_id)
                kwargs.setdefault("session_id", run_handle.session_id)
                with automation_runtime.bind_execution_context(
                    runtime_kind="automation",
                    trigger_source=trigger_source,
                    run_handle=run_handle,
                    user_id=kwargs.get("user_id"),
                    project_id=kwargs.get("project_id"),
                    workspace_id=kwargs.get("workspace_id"),
                ):
                    result = cls._execute_python(target, payload, **kwargs)
            elif action_type == "agent":
                kwargs.setdefault("run_id", run_handle.run_id)
                kwargs.setdefault("session_id", run_handle.session_id)
                result = cls._execute_agent_sync(target, payload, **kwargs)
            elif action_type in {"rpa", "rpa_runtime"}:
                kwargs.setdefault("run_id", run_handle.run_id)
                kwargs.setdefault("session_id", run_handle.session_id)
                with automation_runtime.bind_execution_context(
                    runtime_kind="automation",
                    trigger_source=trigger_source,
                    run_handle=run_handle,
                    user_id=kwargs.get("user_id"),
                    project_id=kwargs.get("project_id"),
                    workspace_id=kwargs.get("workspace_id"),
                ):
                    result = cls._execute_rpa_route(
                        target,
                        payload,
                        run_handle=run_handle,
                        task_name=task_name,
                        trigger_source=trigger_source,
                        kwargs=kwargs,
                    )
            else:
                error_message = f"Unknown action type: {action_type}"
                print(f"[ActionExecutor] {error_message}")
                status = "failed"
                raise ValueError(error_message)
            if execution_receipt is not None:
                receipt_completed = side_effect_idempotency_service.complete(
                    run_handle=run_handle,
                    receipt=execution_receipt,
                    node="automation_runtime",
                    result={"status": "success", "actionType": action_type, "target": target},
                )
                if not receipt_completed:
                    raise RuntimeError(
                        "side_effect_receipt_completion_rejected: external outcome requires reconciliation"
                    )
            automation_runtime.observe_post_action(
                task_name=task_name,
                action_type=action_type,
                target=target,
                trigger_source=trigger_source,
                status=status,
                run_handle=run_handle,
                runtime_kind="automation",
                user_id=kwargs.get("user_id"),
            )
            controlled = cls._consume_automation_control(
                run_handle=run_handle,
                action_type=action_type,
                target=target,
                trigger_source=trigger_source,
                kwargs=kwargs,
                stage="after_execute",
            )
            if controlled is not None:
                status = str(controlled.get("status") or status)
                error_message = str((controlled.get("control") or {}).get("reason") or "")
                return controlled
            cls._activate_automation_stage(
                run_id=run_handle.run_id,
                trigger_source=trigger_source,
                kwargs=kwargs,
                stage="finalize",
                title="Automation 收尾",
                input_payload={"status": "success"},
            )
            run_handle.complete(reason="automation_finished", node="automation_runtime")
            return result
        except Exception as e:
            import traceback
            error_message = f"Error executing {action_type} target '{target}': {str(e)}\n{traceback.format_exc()}"
            print(f"[ActionExecutor] {error_message}")
            status = "failed"
            if execution_receipt is not None:
                side_effect_idempotency_service.fail(
                    run_handle=run_handle,
                    receipt=execution_receipt,
                    node="automation_runtime",
                    error=error_message,
                )
            run_handle.fail(error_message, node="automation_runtime")
            raise e
        finally:
            try:
                session_admission_service.release(run_handle.session_id, run_handle.run_id)
                run_handle.emit(
                    "run.lane.released",
                    {
                        "policy": lane_decision.policy,
                        "session_id": run_handle.session_id,
                    },
                )
            except Exception:
                pass
            if lock_key:
                cls._active_targets.discard(lock_key)
            duration_ms = int((time.time() - start_time) * 1000)
            knowledge_db.log_execution(
                log_id=log_id,
                task_name=task_name,
                action_type=action_type,
                action_target=target,
                trigger_source=trigger_source,
                status=status,
                payload=payload,
                error_message=error_message,
                duration_ms=duration_ms
            )
            # 记录系统审计日志 (轻量级)
            if trigger_source != "manual":
                cls._log_audit_event(trigger_source, task_name, target, status, error_message)

    @staticmethod
    def _log_audit_event(trigger_source: str, task_name: str, action_target: str, status: str, error_message: str = None):
        try:
            from core.audit_logger import audit_logger
            source_type = "SYSTEM"
            trigger_str = str(trigger_source).lower()
            if trigger_str.startswith("hook"):
                source_type = "HOOK"
            elif trigger_str.startswith("cron"):
                source_type = "CRON"

            inferred_action = task_name
            if "memory_agent" in action_target:
                if "chat_end" in trigger_str:
                    inferred_action = "Extract Session Memory"
                elif "cron" in trigger_str:
                    inferred_action = "Periodic Memory Processing"

            details = f"Target: {action_target}"
            if error_message:
                details += f" | Error: {error_message}"
                
            audit_logger.log(
                source_type=source_type,
                action=inferred_action,
                status=status.upper(),
                details=details
            )
        except Exception as e:
            print(f"[ActionExecutor] Failed to write audit log: {e}")

    @staticmethod
    def _persist_runtime_event(
        session_id: str,
        run_id: str,
        topic: str,
        payload: Dict[str, Any],
        *,
        node: str,
        agent_id: Optional[str] = None,
    ):
        emitter = event_bus.create_emitter(
            session_id=session_id,
            conversation_id=session_id,
            run_id=run_id,
            source=RuntimeSource(
                plane="engine",
                component="automation_runtime",
                node=node,
                agent_id=agent_id,
            ),
        )
        return emitter.emit(topic, payload)

    @staticmethod
    def _refresh_projection_snapshot(session_id: str, run_id: str):
        snapshot_service.refresh_chat_projection(session_id, run_id=run_id)

    @staticmethod
    def _execution_side_effect_target_identity(
        *,
        action_type: str,
        target: str,
        trigger_source: str | None,
        kwargs: Dict[str, Any],
    ) -> str:
        parts = [
            str(trigger_source or "").strip(),
            str(action_type or "").strip(),
            str(target or "").strip(),
            str(kwargs.get("cron_job_id") or "").strip(),
            str(kwargs.get("hook_name") or "").strip(),
            str(kwargs.get("event_name") or "").strip(),
        ]
        return "|".join(part for part in parts if part)

    @classmethod
    def _begin_execution_side_effect(
        cls,
        *,
        run_handle,
        action_type: str,
        target: str,
        trigger_source: str | None,
        action_payload: Dict[str, Any],
        kwargs: Dict[str, Any],
    ):
        return side_effect_idempotency_service.begin(
            run_handle=run_handle,
            effect_kind="automation.trigger_execution",
            step_key=f"automation.execute.{action_type}",
            target_identity=cls._execution_side_effect_target_identity(
                action_type=action_type,
                target=target,
                trigger_source=trigger_source,
                kwargs=kwargs,
            ),
            payload={
                "actionType": action_type,
                "target": target,
                "triggerSource": trigger_source,
                "payload": action_payload,
                "cronJobId": kwargs.get("cron_job_id"),
                "hookName": kwargs.get("hook_name"),
                "eventName": kwargs.get("event_name"),
            },
            node="automation_runtime",
            metadata={
                "cronJobId": kwargs.get("cron_job_id"),
                "hookName": kwargs.get("hook_name"),
                "eventName": kwargs.get("event_name"),
            },
        )

    @staticmethod
    def _execute_command(command: str, action_payload: Dict[str, Any], **kwargs):
        env = os.environ.copy()
        
        # Inject standard kwargs and payload into ENV
        for k, v in kwargs.items():
            env[f"V8_AGENT_OS_EXEC_ARG_{k.upper()}"] = str(v)
            env[f"V8_AGENT_OS_HOOK_ARG_{k.upper()}"] = str(v)
            
        # Hook compatibility
        if "event_name" in kwargs:
            env["V8_AGENT_OS_HOOK_EVENT"] = str(kwargs["event_name"])
            
        print(f"[ActionExecutor] Executing Command: {command}")
        process = run_windowless_bounded(
            command,
            shell=True,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_AUTOMATION_COMMAND_TIMEOUT_SECONDS,
        )
        if process.returncode != 0:
            print(f"[ActionExecutor] Command '{command}' Failed with code {process.returncode}:\n{process.stderr}")
        else:
            if process.stdout.strip():
                print(f"[ActionExecutor] Command '{command}' Success:\n{process.stdout.strip()}")
        return process

    @staticmethod
    def _execute_python(module_path: str, action_payload: Dict[str, Any], **kwargs):
        try:
            if module_path in sys.modules:
                module = sys.modules[module_path]
                importlib.reload(module)
            else:
                module = importlib.import_module(module_path)
        except ImportError as e:
            print(f"[ActionExecutor] Could not import python module '{module_path}': {e}")
            return
            
        run_func = getattr(module, 'run', None)
        if callable(run_func):
            import inspect
            sig = inspect.signature(run_func)
            params = sig.parameters
            has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            
            # Build the call kwargs adaptively based on what run() actually accepts
            call_kwargs = {}
            
            # Pass payload — support both 'payload' and 'action_payload' parameter names
            if "action_payload" in params or has_var_keyword:
                call_kwargs["action_payload"] = action_payload
            elif "payload" in params:
                call_kwargs["payload"] = action_payload
            
            # Backward compatibility: hooks pass event_name as first positional arg
            positional_args = []
            if "event_name" in kwargs:
                event_name = kwargs.pop("event_name")
                if "event_name" in params:
                    call_kwargs["event_name"] = event_name
                elif len([p for p in params.values() if p.default == inspect.Parameter.empty and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]) > 0:
                    positional_args.append(event_name)
            
            # Pass remaining kwargs only if run() accepts **kwargs
            if has_var_keyword:
                call_kwargs.update(kwargs)
            
            return run_func(*positional_args, **call_kwargs)
        else:
            print(f"[ActionExecutor] Python module '{module_path}' is missing a callable 'run' function.")

    @staticmethod
    def _execute_agent_sync(target_graph_module_name: str, action_payload: Dict[str, Any], **kwargs):
        try:
            target_module = importlib.import_module(f"graph.{target_graph_module_name}")
            compiled_graph = getattr(target_module, "compiled_graph", None)
            if compiled_graph is None:
                 compiled_graph = getattr(target_module, "app", None)
                 
            if compiled_graph is None:
                raise ValueError(f"Could not find 'compiled_graph' or 'app' in graph module {target_graph_module_name}")
            
            trigger_reason = kwargs.get("event_name", kwargs.get("trigger", "unknown"))
            channel_id = str(action_payload.get("channel_id") or "").strip()
            chat_id = action_payload.get("chat_id")
            run_id = kwargs.get("run_id")
            session_id = kwargs.get("session_id")
            run_handle = automation_runtime.attach_run(str(run_id)) if run_id else None
            if run_handle is None:
                fallback_session_id = automation_runtime.resolve_session_id(
                    action_type="agent",
                    target=target_graph_module_name,
                    trigger_source=trigger_reason,
                    kwargs=kwargs,
                )
                run_handle = type(
                    "_AutomationFallbackRunHandle",
                    (),
                    {
                        "run_id": run_id,
                        "session_id": session_id or fallback_session_id,
                    },
                )()
            payload = automation_runtime.build_agent_execution_payload(
                target_graph_module_name=target_graph_module_name,
                action_payload=action_payload,
                kwargs=kwargs,
                run_handle=run_handle,
            )

            with bind_runtime_context(
                runtime_kind="automation_agent",
                trigger_source=trigger_reason,
                session_id=kwargs.get("session_id"),
                run_id=kwargs.get("run_id"),
                user_id=kwargs.get("user_id"),
                project_id=kwargs.get("project_id"),
                workspace_id=kwargs.get("workspace_id"),
            ):
                result = compiled_graph.invoke(payload)
            
            if isinstance(result, dict) and result.get("hook_rejected"):
                error_feedback = result.get("hook_feedback", "No feedback provided by Agent.")
                raise Exception(f"Agent '{target_graph_module_name}' rejected the context: {error_feedback}")

            if kwargs.get("session_id"):
                automation_runtime.refresh_job_context(session_id=str(kwargs["session_id"]))
                
            return result

        except Exception as e:
            # Re-raise so the caller can handle reflection
            raise Exception(f"Agent Action '{target_graph_module_name}' execution failed: {str(e)}")

    @staticmethod
    async def _execute_agent_async(target_graph_module_name: str, action_payload: Dict[str, Any], **kwargs):
        if not target_graph_module_name:
            return

        trigger_source = kwargs.get("trigger", "manual")
        
        # Mutex Lock for Cron Jobs
        lock_key = None
        if trigger_source == "cron":
            lock_key = f"cron:{kwargs.get('cron_job_id', target_graph_module_name)}"
            if lock_key in ActionExecutor._active_targets:
                ActionExecutor._log_audit_event(trigger_source, kwargs.get("task_name", "Cron Task"), target_graph_module_name, "SKIPPED", "Mutex lock: already running")
                print(f"[ActionExecutor] Skipping {target_graph_module_name} because it is already running (locked by {lock_key}).")
                return
            ActionExecutor._active_targets.add(lock_key)

        log_id = str(uuid.uuid4())
        task_name = kwargs.get("task_name", f"agent:{target_graph_module_name}")
        run_handle = automation_runtime.begin_or_attach_run(
            action_type="agent",
            target=target_graph_module_name,
            payload=action_payload,
            trigger_source=trigger_source,
            is_async=True,
            kwargs=kwargs,
        )
        ActionExecutor._activate_automation_stage(
            run_id=run_handle.run_id,
            trigger_source=trigger_source,
            kwargs=kwargs,
            stage="prepare",
            title="Automation 准备",
            input_payload={
                "actionType": "agent",
                "target": target_graph_module_name,
                "triggerSource": trigger_source,
                "taskName": task_name,
            },
        )
        run_handle.emit(
            "automation.trigger.normalized",
            ActionExecutor._build_automation_trigger_payload(
                run_handle=run_handle,
                action_type="agent",
                target=target_graph_module_name,
                task_name=task_name,
                trigger_source=trigger_source,
                is_async=True,
                kwargs=kwargs,
            ),
        )
        lane_policy = runtime_stability_service.session_lane_policy()
        lane_decision = await session_admission_service.acquire_async(
            run_handle.session_id,
            run_handle.run_id,
            policy=lane_policy,
            runtime_kind="automation_agent",
            metadata={
                "triggerSource": trigger_source,
                "actionType": "agent",
                "target": target_graph_module_name,
            },
        )
        if not lane_decision.acquired:
            error_message = (
                f"Session lane busy: session '{run_handle.session_id}' is already running "
                f"'{lane_decision.rejected_by_run_id or lane_decision.active_run_id}'."
            )
            run_handle.emit(
                "run.lane.rejected",
                {
                    "policy": lane_decision.policy,
                    "busy_run_id": lane_decision.rejected_by_run_id or lane_decision.active_run_id,
                    "session_id": run_handle.session_id,
                },
            )
            run_service.transition_run(run_handle.run_id, status="cancelled", error_message=error_message)
            return
        if lane_decision.waited:
            run_handle.emit(
                "run.lane.queued",
                {
                    "policy": lane_decision.policy,
                    "blocked_by_run_id": lane_decision.active_run_id,
                    "interrupted_run_id": lane_decision.interrupted_run_id,
                },
            )
            run_handle.emit(
                "run.liveness.blocked",
                {
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": f"lane_busy:{lane_decision.active_run_id}",
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
            )
        run_handle.emit(
            "run.lane.acquired",
            {
                "policy": lane_decision.policy,
                "waited": lane_decision.waited,
                "previous_run_id": lane_decision.active_run_id,
                "interrupted_run_id": lane_decision.interrupted_run_id,
            },
        )
        if lane_decision.waited:
            run_handle.emit(
                "run.liveness.recovered",
                {
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": None,
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
            )
        preflight_decision = automation_runtime.run_preflight(
            run_handle=run_handle,
            trigger_source=trigger_source,
            user_id=kwargs.get("user_id"),
        )
        
        # 记录开始状态
        knowledge_db.log_execution(
            log_id=log_id,
            task_name=task_name,
            action_type="agent",
            action_target=target_graph_module_name,
            trigger_source=trigger_source,
            status="running",
            payload=action_payload
        )
        
        start_time = time.time()
        error_message = None
        status = "success"
        execution_receipt = None

        try:
            preflight_result = automation_runtime.handle_preflight_decision(
                run_handle=run_handle,
                trigger_source=trigger_source,
                decision=preflight_decision,
                task_name=task_name,
                safety_override=bool(kwargs.get("safety_override")),
            )
            if preflight_result is not None:
                status = str(preflight_result.get("status") or status)
                error_message = preflight_result.get("reason")
                ActionExecutor._activate_automation_stage(
                    run_id=run_handle.run_id,
                    trigger_source=trigger_source,
                    kwargs=kwargs,
                    stage="finalize",
                    title="Automation 收尾",
                    input_payload={"result": preflight_result, "stage": "preflight"},
                )
                return

            safety_decision = safety_guardian.assess_automation_action(
                action_type="agent",
                target=target_graph_module_name,
                payload=action_payload,
                trigger_source=trigger_source,
            )
            action_result = automation_runtime.handle_action_decision(
                run_handle=run_handle,
                action_type="agent",
                target=target_graph_module_name,
                trigger_source=trigger_source,
                decision=safety_decision,
                task_name=task_name,
                safety_override=bool(kwargs.get("safety_override")),
            )
            if action_result is not None:
                status = str(action_result.get("status") or status)
                error_message = action_result.get("reason")
                ActionExecutor._activate_automation_stage(
                    run_id=run_handle.run_id,
                    trigger_source=trigger_source,
                    kwargs=kwargs,
                    stage="finalize",
                    title="Automation 收尾",
                    input_payload={"result": action_result, "stage": "action_guard"},
                )
                return

            controlled = ActionExecutor._consume_automation_control(
                run_handle=run_handle,
                action_type="agent",
                target=target_graph_module_name,
                trigger_source=trigger_source,
                kwargs=kwargs,
                stage="before_execute",
            )
            if controlled is not None:
                status = str(controlled.get("status") or status)
                error_message = str((controlled.get("control") or {}).get("reason") or "")
                return

            try:
                target_module = importlib.import_module(target_graph_module_name)
            except ImportError:
                target_module = importlib.import_module(f"graph.{target_graph_module_name}")
                
            compiled_graph = getattr(target_module, "compiled_graph", None)
            if compiled_graph is None:
                 compiled_graph = getattr(target_module, "app", None)
                 
            if compiled_graph is None:
                print(f"[ActionExecutor] Async Agent Error: Could not find graph in {target_graph_module_name}")
                return

            trigger_reason = kwargs.get("event_name", kwargs.get("trigger", "unknown"))
            channel_id = str(action_payload.get("channel_id") or "").strip()
            chat_id = action_payload.get("chat_id")
            payload = automation_runtime.build_agent_execution_payload(
                target_graph_module_name=target_graph_module_name,
                action_payload=action_payload,
                kwargs=kwargs,
                run_handle=run_handle,
            )

            run_handle.transition("running", reason=trigger_source, node="automation_runtime")
            ActionExecutor._activate_automation_stage(
                run_id=run_handle.run_id,
                trigger_source=trigger_source,
                kwargs=kwargs,
                stage="execute",
                title="Automation 执行",
                input_payload={
                    "actionType": "agent",
                    "target": target_graph_module_name,
                    "taskName": task_name,
                },
            )
            if ActionExecutor._uses_workflow_envelope(trigger_source, kwargs):
                execution_receipt = ActionExecutor._begin_execution_side_effect(
                    run_handle=run_handle,
                    action_type="agent",
                    target=target_graph_module_name,
                    trigger_source=trigger_source,
                    action_payload=action_payload,
                    kwargs=kwargs,
                )
                if not execution_receipt.execute:
                    if execution_receipt.requires_reconciliation:
                        status = "review_required"
                        error_message = "外部副作用结果未知，必须核对目标系统状态后再决定完成或重试。"
                        run_handle.fail(error_message, node="automation_runtime")
                        return
                    status = "skipped_duplicate"
                    ActionExecutor._activate_automation_stage(
                        run_id=run_handle.run_id,
                        trigger_source=trigger_source,
                        kwargs=kwargs,
                        stage="finalize",
                        title="Automation 收尾",
                        input_payload={"status": status, "receipt": execution_receipt.as_dict()},
                    )
                    run_handle.complete(reason="side_effect_deduplicated", node="automation_runtime")
                    return
            with automation_runtime.bind_execution_context(
                runtime_kind="automation_agent",
                trigger_source=trigger_reason,
                run_handle=run_handle,
                user_id=kwargs.get("user_id"),
                project_id=kwargs.get("project_id"),
                workspace_id=kwargs.get("workspace_id"),
            ):
                result = await compiled_graph.ainvoke(payload)
            automation_runtime.observe_post_action(
                task_name=task_name,
                action_type="agent",
                target=target_graph_module_name,
                trigger_source=trigger_reason,
                status=status,
                run_handle=run_handle,
                runtime_kind="automation_agent",
                user_id=kwargs.get("user_id"),
            )
            automation_runtime.refresh_job_context(session_id=run_handle.session_id)
            if execution_receipt is not None:
                receipt_completed = side_effect_idempotency_service.complete(
                    run_handle=run_handle,
                    receipt=execution_receipt,
                    node="automation_runtime",
                    result={"status": "success", "actionType": "agent", "target": target_graph_module_name},
                )
                if not receipt_completed:
                    raise RuntimeError(
                        "side_effect_receipt_completion_rejected: external outcome requires reconciliation"
                    )
            controlled = ActionExecutor._consume_automation_control(
                run_handle=run_handle,
                action_type="agent",
                target=target_graph_module_name,
                trigger_source=trigger_source,
                kwargs=kwargs,
                stage="after_execute",
            )
            if controlled is not None:
                status = str(controlled.get("status") or status)
                error_message = str((controlled.get("control") or {}).get("reason") or "")
                return

        except ModelGovernanceInterventionRequired as e:
            request_payload = e.to_request_payload()
            approval = run_handle.request_approval(
                approval_kind=e.approval_kind,
                request=request_payload,
            )
            if str(approval.get("status") or "").strip().lower() == "pending":
                error_message = str(e)
                status = "review_required"
            else:
                error_message = str(e)
                status = "failed"
                run_handle.fail(error_message, node="automation_runtime")
        except Exception as e:
            import traceback
            error_message = f"Async Agent '{target_graph_module_name}' failed in background: {str(e)}\n{traceback.format_exc()}"
            print(f"[ActionExecutor] {error_message}")
            status = "failed"
            if execution_receipt is not None:
                side_effect_idempotency_service.fail(
                    run_handle=run_handle,
                    receipt=execution_receipt,
                    node="automation_runtime",
                    error=error_message,
                )
            run_handle.fail(error_message, node="automation_runtime")
            
        finally:
            if lock_key:
                ActionExecutor._active_targets.discard(lock_key)
            if status == "success":
                ActionExecutor._activate_automation_stage(
                    run_id=run_handle.run_id,
                    trigger_source=trigger_source,
                    kwargs=kwargs,
                    stage="finalize",
                    title="Automation 收尾",
                    input_payload={"status": status},
                )
                run_handle.complete(reason="automation_finished", node="automation_runtime")
            try:
                await session_admission_service.release_async(run_handle.session_id, run_handle.run_id)
                run_handle.emit(
                    "run.lane.released",
                    {
                        "policy": lane_decision.policy,
                        "session_id": run_handle.session_id,
                    },
                )
            except Exception:
                pass
            duration_ms = int((time.time() - start_time) * 1000)
            knowledge_db.log_execution(
                log_id=log_id,
                task_name=task_name,
                action_type="agent",
                action_target=target_graph_module_name,
                trigger_source=trigger_source,
                status=status,
                payload=action_payload,
                error_message=error_message,
                duration_ms=duration_ms
            )
            # 记录系统审计日志 (轻量级)
            if trigger_source != "manual":
                ActionExecutor._log_audit_event(trigger_source, task_name, target_graph_module_name, status, error_message)
