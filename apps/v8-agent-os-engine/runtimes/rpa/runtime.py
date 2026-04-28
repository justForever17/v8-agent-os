from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from core.audit_logger import audit_logger
from core.database import db
from erc.kernel import erc_kernel
from erc.runtime_context import bind_runtime_context
from erc.runtime_control import apply_control_signal, consume_stop_signal
from erc.runtime_registry import runtime_registry
from erc.run_service import run_service
from erc.safety_guardian import SafetyDecision, safety_guardian
from erc.side_effect_idempotency import side_effect_idempotency_service
from erc.workflow_ledger import workflow_ledger_service
from runtimes.computer_use.runtime import computer_use_runtime
from runtimes.rpa.compiler import RPATraceCompiler, rpa_trace_compiler
from runtimes.rpa.default_templates import ensure_system_rpa_seed_templates
from runtimes.rpa.execution_semantics import normalize_script_assessment_status, outcome_family_for_execution_state
from runtimes.rpa.robot_adapter import RobotFrameworkAdapter, robot_framework_adapter
from runtimes.rpa.store import RPAScriptStore, rpa_script_store
from runtimes.rpa.template_service import RPATemplateService, rpa_template_service


def _slug(value: str, *, fallback: str = "rpa") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._:-]+", "-", str(value or "").strip()).strip("-").lower()
    return normalized or fallback


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _render_template_value(value: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", value.strip())
        if match:
            return variables.get(match.group(1), value)
        return value
    if isinstance(value, dict):
        return {str(key): _render_template_value(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_template_value(item, variables) for item in value]
    return value


def _is_unresolved_template_placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"\{\{\s*[a-zA-Z0-9_]+\s*\}\}", value.strip()))


class RPARuntime:
    kind = "rpa"

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "RPARuntime",
            "summary": "负责 trace 编译、.robot 导出、RPA 执行与失败后回退，不承担自由式桌面探索。",
            "responsibilities": [
                "把 ComputerUse trace 编译成 draft 和 .robot",
                "运行 Robot Framework / RPA Framework 流程",
                "在失败时回退 ComputerUse 并尝试局部修补",
            ],
            "routingKeywords": ["RPA", "复现流程", "Robot Framework", "自动化脚本", "流程模板"],
            "acceptedInputs": ["trace run ids", "draft id", "robot script", "variables"],
            "producedOutputs": ["draft", "robot script", "execution result", "repair metadata"],
            "ownedSteps": ["rpa.compile", "rpa.execute_draft", "rpa.local_repair", "rpa.export_robot"],
            "supportsPause": True,
            "supportsResume": False,
            "supportsApproval": True,
            "supportsRepair": True,
            "visibility": "specialized",
            "promptHints": [
                "已经存在稳定流程、希望快速复现或导出脚本时，优先交给 RPARuntime。",
                "当 RPA 失败时，允许它局部回退到 ComputerUse，而不是让 Supervisor 自己重跑整段流程。",
            ],
            "capabilities": [
                {
                    "key": "rpa.compile_execute",
                    "label": "RPA 编译与执行",
                    "summary": "管理 draft、.robot 导出、运行与失败修补闭环。",
                    "accepts": ["trace", "draft", "variables"],
                    "outputs": ["robot flow", "trust assessment", "fallback result"],
                    "examples": ["将成功探索固化成可复用流程", "运行已有 RPA 并在失败时局部修补"],
                    "risk_level": "high",
                }
            ],
            "metadata": {
                "managedToolPrefixes": ["rpa_"],
                "managedToolGroups": ["rpa.run"],
            },
        }

    def __init__(
        self,
        *,
        compiler: RPATraceCompiler = rpa_trace_compiler,
        adapter: RobotFrameworkAdapter = robot_framework_adapter,
        script_store: RPAScriptStore = rpa_script_store,
        template_service: RPATemplateService = rpa_template_service,
    ) -> None:
        self.compiler = compiler
        self.adapter = adapter
        self.script_store = script_store
        self.template_service = template_service
        ensure_system_rpa_seed_templates(self.script_store)

    def list_drafts(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        return self.script_store.list_drafts(limit=limit)

    def list_robot_scripts(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        return self.script_store.list_robot_scripts(limit=limit)

    def availability(self) -> Dict[str, Any]:
        return self.adapter.availability()

    def compile_trace_to_draft(self, run_id: str, *, save: bool = True) -> Dict[str, Any]:
        draft = self.compiler.compile_run_to_draft(run_id, save=save)
        self._log_audit(
            action=f"Compile trace to RPA draft: {run_id}",
            status="SUCCESS",
            details=json.dumps({"runId": run_id, "scriptId": draft.get("id")}, ensure_ascii=False),
        )
        return draft

    def compile_traces_to_draft(self, run_ids: list[str], *, save: bool = True) -> Dict[str, Any]:
        draft = self.compiler.compile_runs_to_draft(run_ids, save=save)
        self._log_audit(
            action="Compile traces to merged RPA draft",
            status="SUCCESS",
            details=json.dumps({"runIds": run_ids, "scriptId": draft.get("id")}, ensure_ascii=False),
        )
        return draft

    def get_draft(self, script_id: str) -> Optional[Dict[str, Any]]:
        return self.script_store.get_draft(script_id)

    def list_templates(self, *, limit: int = 100, app_id: str | None = None, status: str | None = None) -> list[Dict[str, Any]]:
        return self.template_service.list_templates(limit=limit, app_id=app_id, status=status)

    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        return self.template_service.get_template(template_id)

    def list_template_history(self, template_id: str, *, limit: int = 50) -> list[Dict[str, Any]]:
        return self.template_service.list_template_history(template_id, limit=limit)

    def recommend_execution_route(
        self,
        *,
        goal: str,
        app_id: str | None = None,
        variables: Dict[str, Any] | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        limit: int = 5,
        allow_materialization: bool = False,
    ) -> Dict[str, Any]:
        return self.template_service.recommend_execution_route(
            goal=goal,
            app_id=app_id,
            variables=variables,
            session_id=session_id,
            run_id=run_id,
            limit=limit,
            allow_materialization=allow_materialization,
        )

    def review_template(
        self,
        template_id: str,
        *,
        decision: str,
        reviewer: str = "system",
        notes: str | None = None,
        metadata_patch: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = self.template_service.review_template(
            template_id,
            decision=decision,
            reviewer=reviewer,
            notes=notes,
            metadata_patch=metadata_patch,
        )
        self._log_audit(
            action=f"Review RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps(
                {
                    "templateId": template_id,
                    "decision": decision,
                    "reviewer": reviewer,
                },
                ensure_ascii=False,
            ),
        )
        return payload

    def approve_template(
        self,
        template_id: str,
        *,
        reviewer: str = "system",
        notes: str | None = None,
    ) -> Dict[str, Any]:
        payload = self.template_service.approve_template(template_id, reviewer=reviewer, notes=notes)
        self._log_audit(
            action=f"Approve RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps({"templateId": template_id, "reviewer": reviewer}, ensure_ascii=False),
        )
        return payload

    def freeze_template(
        self,
        template_id: str,
        *,
        reviewer: str = "system",
        notes: str | None = None,
    ) -> Dict[str, Any]:
        payload = self.template_service.freeze_template(template_id, reviewer=reviewer, notes=notes)
        self._log_audit(
            action=f"Freeze RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps({"templateId": template_id, "reviewer": reviewer}, ensure_ascii=False),
        )
        return payload

    def rollback_template(
        self,
        template_id: str,
        *,
        revision: int | None = None,
        history_path: str | None = None,
        reviewer: str = "system",
        notes: str | None = None,
    ) -> Dict[str, Any]:
        payload = self.template_service.rollback_template(
            template_id,
            revision=revision,
            history_path=history_path,
            reviewer=reviewer,
            notes=notes,
        )
        self._log_audit(
            action=f"Rollback RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps(
                {
                    "templateId": template_id,
                    "revision": revision,
                    "historyPath": history_path,
                    "reviewer": reviewer,
                },
                ensure_ascii=False,
            ),
        )
        return payload

    def get_draft_source_traces(
        self,
        script_id: str,
        *,
        include_steps: bool = True,
        max_steps: int = 8,
    ) -> Optional[Dict[str, Any]]:
        draft = self.get_draft(script_id)
        if not draft:
            return None
        source = dict(draft.get("source") or {})
        run_ids = [str(item).strip() for item in list(source.get("traceRunIds") or []) if str(item).strip()]
        if not run_ids and str(source.get("traceRunId") or "").strip():
            run_ids = [str(source.get("traceRunId")).strip()]
        return {
            "scriptId": script_id,
            "scriptName": draft.get("name"),
            "source": source,
            "traceBundle": self.compiler.trace_store.get_trace_bundle(
                run_ids,
                include_steps=include_steps,
                max_steps=max_steps,
            ),
        }

    def export_draft_to_robot(
        self,
        *,
        script_id: str,
        output_dir: str | Path | None = None,
    ) -> Dict[str, Any]:
        draft = self.get_draft(script_id)
        if not draft:
            raise ValueError(f"未找到 draft: {script_id}")
        exported = self.adapter.export_script(
            script=draft,
            output_dir=Path(output_dir) if output_dir is not None else None,
        )
        self._log_audit(
            action=f"Export RPA draft: {script_id}",
            status="SUCCESS",
            details=json.dumps({"scriptId": script_id, "path": exported["path"]}, ensure_ascii=False),
        )
        return exported

    def prepare_draft_run(
        self,
        *,
        script_id: str,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: str | Path | None = None,
    ) -> Dict[str, Any]:
        prepared = self.adapter.prepare_draft_run(
            script_id=script_id,
            variables=variables,
            output_dir=Path(output_dir) if output_dir is not None else None,
        )
        self._log_audit(
            action=f"Prepare RPA draft run: {script_id}",
            status="INFO",
            details=json.dumps({"scriptId": script_id, "variables": _jsonable(variables or {})}, ensure_ascii=False),
        )
        return prepared

    def prepare_existing_run(
        self,
        *,
        robot_file: str | Path,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: str | Path | None = None,
    ) -> Dict[str, Any]:
        prepared = self.adapter.prepare_existing_run(
            robot_file=Path(robot_file),
            variables=variables,
            output_dir=Path(output_dir) if output_dir is not None else None,
        )
        self._log_audit(
            action=f"Prepare existing robot flow: {Path(robot_file).name}",
            status="INFO",
            details=json.dumps({"robotFile": str(robot_file), "variables": _jsonable(variables or {})}, ensure_ascii=False),
        )
        return prepared

    def _resolve_session_id(
        self,
        *,
        script_id: str | None = None,
        robot_file: str | Path | None = None,
        session_id: str | None = None,
    ) -> str:
        if session_id:
            return str(session_id)
        if script_id:
            return f"rpa:draft:{_slug(script_id)}"
        if robot_file:
            digest = hashlib.md5(str(robot_file).encode("utf-8")).hexdigest()[:10]
            return f"rpa:file:{digest}"
        return "rpa:manual"

    def _build_session_title(self, *, script_id: str | None = None, robot_file: str | Path | None = None) -> str:
        if script_id:
            return f"RPA Draft · {script_id}"
        if robot_file:
            return f"RPA Flow · {Path(robot_file).name}"
        return "RPA Runtime"

    def _build_run_metadata(
        self,
        *,
        mode: str,
        prepared: Dict[str, Any],
        variables: Dict[str, Any],
        trigger_source: str | None,
        cwd: str | None,
    ) -> Dict[str, Any]:
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        script_metadata = dict(script.get("metadata") or {}) if script else {}
        template_policy = self._resolve_template_execution_policy(mode=mode, prepared=prepared)
        execution_state = "queued"
        return {
            "runtime": "rpa",
            "mode": mode,
            "trigger_source": trigger_source,
            "variables": _jsonable(variables),
            "cwd": cwd,
            "command": list(prepared.get("command") or []),
            "availability": _jsonable(prepared.get("available") or {}),
            "script": _jsonable(script or {}),
            "export": _jsonable(prepared.get("export") or {}),
            "robotFile": prepared.get("robotFile"),
            "assessment": _jsonable((script or {}).get("assessment") if script else {}),
            "trustStatus": normalize_script_assessment_status(((script or {}).get("assessment") or {}).get("status") if script else None),
            "templateGovernance": _jsonable(script_metadata.get("templateGovernance") or {}),
            "templateStatus": script_metadata.get("templateStatus") or script_metadata.get("templateGovernance", {}).get("templateStatus"),
            "templateExecutionPolicy": _jsonable(template_policy),
            "templateExecutionPath": template_policy.get("executionPath"),
            "executionState": execution_state,
            "outcomeFamily": outcome_family_for_execution_state(execution_state),
        }

    def _log_audit(self, *, action: str, status: str, details: str | None = None) -> None:
        try:
            audit_logger.log("RPA", action, status, details)
        except Exception:
            pass

    def _record_run_feedback(
        self,
        *,
        prepared: Dict[str, Any],
        execution_state: str,
        feedback: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any] | None:
        outcome_family = outcome_family_for_execution_state(execution_state)
        try:
            script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
            if script:
                self.script_store.record_run_feedback(
                    script=script,
                    execution_state=execution_state,
                    outcome_family=outcome_family,
                    feedback=feedback,
                )
                template_id = str(((script.get("source") or {}).get("templateId")) or "").strip()
                if template_id:
                    return self.template_service.register_template_execution_feedback(
                        template_id,
                        execution_state=execution_state,
                        outcome_family=outcome_family,
                        feedback=dict(feedback or {}),
                        actor="rpa_runtime",
                    )
        except Exception:
            return None
        return None

    def _update_run_metadata(self, run_id: str, **updates: Any) -> None:
        try:
            next_updates = dict(updates or {})
            execution_state = next_updates.get("executionState")
            if "outcomeFamily" not in next_updates and execution_state not in (None, ""):
                next_updates["outcomeFamily"] = outcome_family_for_execution_state(execution_state)
            run_service.update_metadata(run_id, next_updates)
        except Exception:
            pass

    def _merged_feedback_suggestions(self, payload: Dict[str, Any] | None) -> Dict[str, Any]:
        feedback = dict(payload or {})
        preflight_hints = [dict(item) for item in list(feedback.get("preflightHints") or []) if isinstance(item, dict)]
        return {
            **({"selectorMemoryCandidate": dict(feedback.get("selectorMemoryCandidate") or {})} if isinstance(feedback.get("selectorMemoryCandidate"), dict) and feedback.get("selectorMemoryCandidate") else {}),
            **({"appProfileRecommendation": dict(feedback.get("appProfileRecommendation") or {})} if isinstance(feedback.get("appProfileRecommendation"), dict) and feedback.get("appProfileRecommendation") else {}),
            **({"playbookRecommendation": dict(feedback.get("playbookRecommendation") or {})} if isinstance(feedback.get("playbookRecommendation"), dict) and feedback.get("playbookRecommendation") else {}),
            **({"preflightHints": preflight_hints} if preflight_hints else {}),
        }

    def _feedback_suggestions_from_computer_use_execution(self, execution: Dict[str, Any] | None) -> Dict[str, Any]:
        payload = dict(execution or {})
        run_id = str(payload.get("runId") or "").strip()
        if not run_id:
            return {}
        trace = self.compiler.trace_store.get_trace(run_id)
        if not isinstance(trace, dict):
            return {}
        merged: Dict[str, Any] = {}
        preflight_hints: list[dict] = []
        seen_preflight_keys: set[str] = set()
        for step in list(trace.get("steps") or []):
            if not isinstance(step, dict):
                continue
            metadata = dict(step.get("metadata") or {})
            feedback = dict(metadata.get("feedbackSuggestions") or {})
            if not merged.get("selectorMemoryCandidate") and isinstance(feedback.get("selectorMemoryCandidate"), dict):
                merged["selectorMemoryCandidate"] = dict(feedback.get("selectorMemoryCandidate") or {})
            if not merged.get("appProfileRecommendation") and isinstance(feedback.get("appProfileRecommendation"), dict):
                merged["appProfileRecommendation"] = dict(feedback.get("appProfileRecommendation") or {})
            if not merged.get("playbookRecommendation") and isinstance(feedback.get("playbookRecommendation"), dict):
                merged["playbookRecommendation"] = dict(feedback.get("playbookRecommendation") or {})
            for item in list(feedback.get("preflightHints") or []):
                if not isinstance(item, dict):
                    continue
                signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if signature in seen_preflight_keys:
                    continue
                seen_preflight_keys.add(signature)
                preflight_hints.append(dict(item))
        if preflight_hints:
            merged["preflightHints"] = preflight_hints
        return merged

    def _consume_control_signal(self, *, run_handle, stage: str) -> Dict[str, Any] | None:
        signal = consume_stop_signal(run_handle.run_id)
        if signal is None:
            return None
        return apply_control_signal(
            run_handle,
            signal=signal,
            runtime_kind="rpa",
            node="rpa_runtime",
            extras={"stage": stage},
        )

    def _finalize_controlled(
        self,
        *,
        run_handle,
        prepared: Dict[str, Any],
        control: Dict[str, Any],
        assessment: Dict[str, Any],
        template_policy: Dict[str, Any],
        execution_state: str,
        extra_payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        self._update_run_metadata(
            run_handle.run_id,
            executionState=execution_state,
            trustStatus=normalize_script_assessment_status(assessment.get("status")),
            templateExecutionPolicy=template_policy,
            templateExecutionPath=template_policy.get("executionPath"),
            control=control,
        )
        self._record_run_feedback(prepared=prepared, execution_state=execution_state)
        result = {
            **prepared,
            "status": execution_state,
            "outcomeFamily": outcome_family_for_execution_state(execution_state),
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
            "templateExecutionPolicy": template_policy,
            "control": control,
        }
        if extra_payload:
            result.update(extra_payload)
        return result

    def _consume_or_finalize_control(
        self,
        *,
        run_handle,
        stage: str,
        prepared: Dict[str, Any],
        assessment: Dict[str, Any],
        template_policy: Dict[str, Any],
        extra_payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any] | None:
        control = self._consume_control_signal(run_handle=run_handle, stage=stage)
        if control is None:
            return None
        return self._finalize_controlled(
            run_handle=run_handle,
            prepared=prepared,
            control=control,
            assessment=assessment,
            template_policy=template_policy,
            execution_state=str(control.get("status") or "paused"),
            extra_payload=extra_payload,
        )

    def _begin_run(
        self,
        *,
        session_id: str,
        user_id: str,
        trigger_source: str | None,
        metadata: Dict[str, Any],
        title: str,
        run_id: str | None = None,
    ):
        db.create_or_update_session(
            session_id=session_id,
            title=title,
            user_id=user_id,
            metadata={
                "runtime": "rpa",
                "trigger_source": trigger_source,
                "mode": metadata.get("mode"),
            },
        )
        run_handle = erc_kernel.submit_run(
            session_id=session_id,
            conversation_id=session_id,
            user_id=user_id,
            runtime_kind="rpa",
            trigger_source=trigger_source,
            agent_id=None,
            metadata=metadata,
            run_id=run_id,
            initial_status="queued",
            component="rpa_runtime",
            node="run_manager",
        )
        run_handle.emit(
            "run.created",
            {
                "run_id": run_handle.run_id,
                "transport": "rpa",
                "trigger_source": trigger_source,
                "mode": metadata.get("mode"),
            },
        )
        return run_handle

    def _run_preflight(self, *, run_handle, trigger_source: str | None, user_id: str) -> SafetyDecision:
        decision = safety_guardian.preflight_runtime(
            runtime_kind="rpa",
            trigger_source=trigger_source,
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            user_id=user_id,
        )
        run_handle.emit("safety.preflight.checked", decision.to_payload())
        return decision

    def _handle_preflight_decision(
        self,
        *,
        run_handle,
        decision: SafetyDecision,
        trigger_source: str | None,
        subject: str,
    ) -> Optional[Dict[str, Any]]:
        safety_guardian.log_decision_event(
            action="rpa_preflight",
            decision=decision,
            subject=subject,
            metadata={"runId": run_handle.run_id, "sessionId": run_handle.session_id, "triggerSource": trigger_source},
        )
        if decision.is_allow():
            return None
        if decision.is_review():
            approval = run_handle.request_approval(
                approval_kind="safety_review",
                request=safety_guardian.build_runtime_preflight_request(
                    runtime_kind="rpa",
                    trigger_source=trigger_source or "manual",
                    decision=decision,
                    subject=subject,
                ),
            )
            if str(approval.get("status") or "").strip().lower() != "pending":
                self._log_audit(
                    action=f"RPA preflight auto-approved: {subject}",
                    status="INFO",
                    details=json.dumps(
                        {
                            "approvalId": approval.get("approval_id"),
                            "policySource": approval.get("policySource"),
                            "reason": decision.reason,
                        },
                        ensure_ascii=False,
                    ),
                )
                return None
            self._log_audit(
                action=f"RPA preflight review: {subject}",
                status="WARNING",
                details=json.dumps({"approvalId": approval.get("approval_id"), "reason": decision.reason}, ensure_ascii=False),
            )
            return {
                "status": "review_required",
                "outcomeFamily": outcome_family_for_execution_state("review_required"),
                "reason": decision.reason,
                "approvalId": approval.get("approval_id"),
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
            }
        error_message = f"Safety Guardian blocked RPA run: {decision.reason}"
        run_handle.emit("safety.preflight.blocked", decision.to_payload())
        run_handle.fail(error_message, node="safety_guardian")
        self._log_audit(action=f"RPA preflight blocked: {subject}", status="ERROR", details=error_message)
        return {
            "status": "blocked",
            "outcomeFamily": outcome_family_for_execution_state("blocked"),
            "reason": decision.reason,
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
        }

    def _resolve_template_execution_policy(self, *, mode: str, prepared: Dict[str, Any]) -> Dict[str, Any]:
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        metadata = dict(script.get("metadata") or {}) if script else {}
        source = dict(script.get("source") or {}) if script else {}
        governance = dict(metadata.get("templateGovernance") or {})
        promotion_gate = dict(metadata.get("templatePromotionGate") or {})
        has_template_governance = bool(governance) or bool(source.get("templateId") or source.get("templateStage") or source.get("templateStatus"))
        stage = str(governance.get("stage") or source.get("templateStage") or "").strip() or ("candidate" if has_template_governance else "unmanaged")
        status = str(metadata.get("templateStatus") or governance.get("templateStatus") or source.get("templateStatus") or "").strip() or ("candidate" if has_template_governance else "unmanaged")
        metadata_rollout = str(metadata.get("templateRolloutMode") or "").strip()
        rollout_mode = str(governance.get("rolloutMode") or metadata_rollout or "").strip() or ("candidate_shadow" if has_template_governance else "robot_default")
        allow_computer_use_fallback = bool(governance.get("allowComputerUseFallback", True))
        prefer_template_execution = bool(governance.get("preferTemplateExecution"))
        approval_required = bool(governance.get("approvalRequired", True))
        has_computer_use_source = self._has_computer_use_replay_capability(mode=mode, prepared=prepared)
        promotion_gate_blocked = bool(promotion_gate.get("blockedPromotion"))
        if promotion_gate_blocked:
            rollout_mode = "computer_use_first"
        execution_path = "robot"
        if has_computer_use_source and rollout_mode == "computer_use_first":
            execution_path = "computer_use_first"
        elif rollout_mode == "template_preferred_with_fallback":
            execution_path = "template_preferred_with_fallback"
        elif rollout_mode == "template_preferred":
            execution_path = "template_preferred"
        elif rollout_mode == "candidate_shadow":
            execution_path = "candidate_shadow"
        suppress_compile_review = bool(stage == "approved_live" and prefer_template_execution)
        bypass_compile_block = bool(execution_path == "computer_use_first" and has_computer_use_source)
        return {
            "stage": stage,
            "status": status,
            "recommendedDecision": governance.get("recommendedDecision"),
            "rolloutMode": rollout_mode,
            "executionPath": execution_path,
            "preferTemplateExecution": prefer_template_execution,
            "allowComputerUseFallback": allow_computer_use_fallback,
            "approvalRequired": approval_required,
            "hasComputerUseSource": has_computer_use_source,
            "promotionGate": promotion_gate,
            "promotionGateBlocked": promotion_gate_blocked,
            "suppressCompileReview": suppress_compile_review,
            "bypassCompileBlock": bypass_compile_block,
            "confidence": governance.get("confidence"),
            "reasons": list(governance.get("reasons") or []),
        }

    def _has_computer_use_replay_capability(self, *, mode: str, prepared: Dict[str, Any]) -> bool:
        if mode != "draft":
            return False
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        if not script or not list(script.get("steps") or []):
            return False
        source = script.get("source") if isinstance(script.get("source"), dict) else {}
        source_type = str(source.get("type") or "").strip()
        if source_type in {"computer_use_trace", "computer_use_trace_merge", "rpa_template_candidate"}:
            return True
        if any(str(step.get("use") or "").strip() == "computer_use_playbook" for step in list(script.get("steps") or []) if isinstance(step, dict)):
            return True
        if str(source.get("traceRunId") or "").strip():
            return True
        if [item for item in list(source.get("traceRunIds") or []) if str(item).strip()]:
            return True
        return False

    def _required_approvals(self, prepared: Dict[str, Any], *, template_policy: Dict[str, Any] | None = None) -> list[Dict[str, Any]]:
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        approvals: list[Dict[str, Any]] = []
        assessment = script.get("assessment") if isinstance(script.get("assessment"), dict) else {}
        suppress_compile_review = bool((template_policy or {}).get("suppressCompileReview"))
        if (
            assessment
            and not suppress_compile_review
            and str(assessment.get("status") or "").strip().lower() in {"review_required", "fallback_heavy"}
        ):
            approvals.append(
                {
                    "stepId": "script",
                    "use": "script",
                    "mode": "compile_fallback_heavy" if str(assessment.get("status") or "").strip().lower() == "fallback_heavy" else "compile_review_required",
                    "reason": " / ".join(str(item) for item in list(assessment.get("reasons") or [])[:3]) or "编译结果需要人工复核",
                    "confidence": assessment.get("score"),
                }
            )
        for step in list(script.get("steps") or []):
            approval = step.get("approval") if isinstance(step.get("approval"), dict) else None
            if approval and approval.get("required", True):
                approvals.append(
                    {
                        "stepId": step.get("stepId"),
                        "use": step.get("use"),
                        "mode": approval.get("mode"),
                        "reason": approval.get("reason"),
                        "confidence": ((step.get("assessment") or {}).get("score") if isinstance(step.get("assessment"), dict) else None),
                    }
        )
        return approvals

    def _compile_block_result(
        self,
        *,
        prepared: Dict[str, Any],
        run_handle,
        assessment: Dict[str, Any],
        subject: str,
        template_policy: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        reasons = [str(item) for item in list(assessment.get("reasons") or []) if str(item).strip()]
        reason = " / ".join(reasons[:3]) or "编译准入未通过"
        run_handle.emit(
            "rpa.compile.blocked",
            {
                "subject": subject,
                "assessment": assessment,
                "reason": reason,
            },
        )
        run_handle.fail(f"RPA 编译准入未通过: {reason}", node="rpa_runtime")
        self._update_run_metadata(
            run_handle.run_id,
            executionState="compile_blocked",
            reason=reason,
            trustStatus=normalize_script_assessment_status(assessment.get("status")),
            assessment=assessment,
        )
        self._log_audit(
            action=f"RPA compile blocked: {subject}",
            status="ERROR",
            details=json.dumps({"runId": run_handle.run_id, "reason": reason, "assessment": assessment}, ensure_ascii=False),
        )
        self._record_run_feedback(prepared=prepared, execution_state="compile_blocked")
        return {
            **prepared,
            "status": "compile_blocked",
            "outcomeFamily": outcome_family_for_execution_state("compile_blocked"),
            "reason": reason,
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
            "assessment": assessment,
            "templateExecutionPolicy": template_policy or self._resolve_template_execution_policy(mode="draft", prepared=prepared),
        }

    def _supports_computer_use_fallback(self, *, mode: str, prepared: Dict[str, Any]) -> bool:
        if not self._has_computer_use_replay_capability(mode=mode, prepared=prepared):
            return False
        policy = self._resolve_template_execution_policy(mode=mode, prepared=prepared)
        return bool(policy.get("allowComputerUseFallback", True))

    def _extract_failed_step_context(self, *, execution: Dict[str, Any], script: Dict[str, Any]) -> Dict[str, Any]:
        script_steps = [item for item in list(script.get("steps") or []) if isinstance(item, dict)]
        if not script_steps:
            return {}
        output_chunks = [str(execution.get("stdout") or ""), str(execution.get("stderr") or "")]
        joined_output = "\n".join(chunk for chunk in output_chunks if chunk).strip()
        logged_step_ids = re.findall(r"STEP_ID:([A-Za-z0-9._:-]+)", joined_output)
        step_id_to_index = {
            str(step.get("stepId") or ""): index
            for index, step in enumerate(script_steps)
            if str(step.get("stepId") or "").strip()
        }
        if logged_step_ids:
            last_step_id = str(logged_step_ids[-1]).strip()
            if last_step_id in step_id_to_index:
                index = step_id_to_index[last_step_id]
                return {
                    "stepId": last_step_id,
                    "stepIndex": index,
                    "matchedFrom": "stdout_marker",
                    "remainingSteps": max(0, len(script_steps) - index),
                }
        for index, step in enumerate(script_steps):
            step_id = str(step.get("stepId") or "").strip()
            if step_id and step_id in joined_output:
                return {
                    "stepId": step_id,
                    "stepIndex": index,
                    "matchedFrom": "output_match",
                    "remainingSteps": max(0, len(script_steps) - index),
                }
        return {}

    def _draft_to_computer_use_steps(
        self,
        *,
        script: Dict[str, Any],
        variables: Dict[str, Any],
        start_index: int = 0,
    ) -> list[Dict[str, Any]]:
        app_id = str(script.get("appId") or "").strip() or None
        plan_steps: list[Dict[str, Any]] = []
        draft_steps = [item for item in list(script.get("steps") or []) if isinstance(item, dict)]
        safe_start_index = max(0, int(start_index or 0))
        for local_index, step in enumerate(draft_steps[safe_start_index:], start=safe_start_index):
            if not isinstance(step, dict):
                continue
            use = str(step.get("use") or "").strip()
            if not use:
                continue
            params = _render_template_value(dict(step.get("params") or {}), variables)
            step_metadata = dict(step.get("metadata") or {})
            target = dict(step.get("target") or {})
            window = dict(target.get("window") or {})
            selector = dict(target.get("selector") or {})
            risk = dict(step.get("risk") or {})
            risk_details = dict(risk.get("details") or {})
            timing = dict(step.get("timing") or {})
            target_strategy = dict(step_metadata.get("targetStrategyApplied") or {})
            strategy_payload = dict(target_strategy.get("strategy") or {})
            clipboard_payload = dict(step_metadata.get("clipboardPayload") or {})

            payload: Dict[str, Any] = {
                "action": use,
                **dict(params or {}),
            }
            if app_id and payload.get("app_id") is None:
                payload["app_id"] = app_id
            if window.get("title") and payload.get("window_title") is None:
                payload["window_title"] = window.get("title")
            if window.get("className") and payload.get("class_name") is None:
                payload["class_name"] = window.get("className")
            if window.get("windowHandle") not in (None, "") and payload.get("window_handle") is None:
                payload["window_handle"] = window.get("windowHandle")
            if window.get("processName") and payload.get("process_name") is None:
                payload["process_name"] = window.get("processName")
            if selector.get("selectorKey") and payload.get("selector_key") is None:
                payload["selector_key"] = selector.get("selectorKey")
            if selector.get("elementId") and payload.get("element_id") is None:
                payload["element_id"] = selector.get("elementId")
            if selector.get("name") and payload.get("name") is None:
                payload["name"] = selector.get("name")
            if selector.get("automationId") and payload.get("automation_id") is None:
                payload["automation_id"] = selector.get("automationId")
            if selector.get("controlType") and payload.get("control_type") is None:
                payload["control_type"] = selector.get("controlType")
            if selector.get("className") and payload.get("class_name") is None:
                payload["class_name"] = selector.get("className")
            if selector.get("handle") not in (None, "") and payload.get("handle") is None:
                payload["handle"] = selector.get("handle")
            if payload.get("toolbar_action_name") and payload.get("action_name") is None:
                payload["action_name"] = payload.get("toolbar_action_name")
            if risk_details.get("visualExpectation") and payload.get("visual_expectation") is None:
                payload["visual_expectation"] = risk_details.get("visualExpectation")
            if risk_details.get("targetText") not in (None, "") and payload.get("target_text") is None:
                payload["target_text"] = risk_details.get("targetText")
            if risk_details.get("postActionSettleTimeoutMs") not in (None, "") and payload.get("post_action_settle_timeout_ms") is None:
                payload["post_action_settle_timeout_ms"] = risk_details.get("postActionSettleTimeoutMs")
            if risk_details.get("postActionSettlePollMs") not in (None, "") and payload.get("post_action_settle_poll_ms") is None:
                payload["post_action_settle_poll_ms"] = risk_details.get("postActionSettlePollMs")
            if risk_details.get("postActionStableRounds") not in (None, "") and payload.get("post_action_stable_rounds") is None:
                payload["post_action_stable_rounds"] = risk_details.get("postActionStableRounds")
            if risk_details.get("abortOnMajorDeviation") not in (None, "") and payload.get("abort_on_major_deviation") is None:
                payload["abort_on_major_deviation"] = bool(risk_details.get("abortOnMajorDeviation"))
            if strategy_payload:
                if payload.get("query_mode") in (None, "") and strategy_payload.get("query_mode") not in (None, ""):
                    payload["query_mode"] = strategy_payload.get("query_mode")
                if payload.get("preferred_result_region") in (None, "") and strategy_payload.get("preferred_result_region") not in (None, ""):
                    payload["preferred_result_region"] = strategy_payload.get("preferred_result_region")
                if payload.get("preferred_result_index") in (None, "") and strategy_payload.get("preferred_result_index") not in (None, ""):
                    payload["preferred_result_index"] = strategy_payload.get("preferred_result_index")
                if payload.get("required_exact_match") is None and strategy_payload.get("required_exact_match") is not None:
                    payload["required_exact_match"] = bool(strategy_payload.get("required_exact_match"))
                if not list(payload.get("forbidden_result_tokens") or []) and list(strategy_payload.get("forbidden_result_tokens") or []):
                    payload["forbidden_result_tokens"] = list(strategy_payload.get("forbidden_result_tokens") or [])
                if payload.get("search_selector_key") in (None, "") and strategy_payload.get("search_selector_key") not in (None, ""):
                    payload["search_selector_key"] = strategy_payload.get("search_selector_key")
                if payload.get("result_selector_key") in (None, "") and strategy_payload.get("result_selector_key") not in (None, ""):
                    payload["result_selector_key"] = strategy_payload.get("result_selector_key")
            if clipboard_payload:
                if payload.get("text") in (None, "") and clipboard_payload.get("text") not in (None, ""):
                    payload["text"] = clipboard_payload.get("text")
                if payload.get("file_path") in (None, "") and not list(payload.get("file_paths") or []) and not list(payload.get("attachment_paths") or []):
                    file_paths = list(clipboard_payload.get("file_paths") or [])
                    if len(file_paths) == 1:
                        payload["file_path"] = file_paths[0]
                    elif file_paths:
                        payload["file_paths"] = file_paths
            if _is_unresolved_template_placeholder(payload.get("file_paths")):
                payload.pop("file_paths", None)
            if _is_unresolved_template_placeholder(payload.get("attachment_paths")):
                payload.pop("attachment_paths", None)
            if _is_unresolved_template_placeholder(payload.get("file_path")):
                payload.pop("file_path", None)
            if use == "open_app" and payload.get("wait_timeout_ms") is None and timing.get("waitTimeoutMs") not in (None, ""):
                payload["wait_timeout_ms"] = timing.get("waitTimeoutMs")
            if use == "wait_for_element" and payload.get("timeout_ms") is None and timing.get("waitTimeoutMs") not in (None, ""):
                payload["timeout_ms"] = timing.get("waitTimeoutMs")
            if payload.get("require_visual_guard") is None:
                payload["require_visual_guard"] = bool(
                    risk.get("requiresPreGuard") or risk.get("requiresPostGuard") or step.get("approval")
                )
            payload["_draft_step_id"] = step.get("stepId")
            payload["_draft_step_index"] = local_index
            plan_steps.append(payload)
        return plan_steps

    def _run_computer_use_fallback(
        self,
        *,
        prepared: Dict[str, Any],
        run_id: str | None,
        variables: Dict[str, Any],
        session_id: str,
        user_id: str,
        project_id: str | None,
        workspace_id: str | None,
        workspace_path: str | None,
        failed_step: Dict[str, Any] | None = None,
    ) -> Optional[Dict[str, Any]]:
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        if not script:
            return None
        failed_step = dict(failed_step or {})
        start_index = max(0, int(failed_step.get("stepIndex") or 0))
        steps = self._draft_to_computer_use_steps(script=script, variables=variables, start_index=start_index)
        if not steps:
            return None
        execution = computer_use_runtime.execute_plan(
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            goal=str(script.get("goal") or script.get("name") or script.get("id") or "computer_use_fallback"),
            steps=steps,
            continue_on_error=False,
            max_steps=max(5, len(steps)),
        )
        feedback_suggestions = self._feedback_suggestions_from_computer_use_execution(execution)
        return {
            "status": "completed",
            "type": "computer_use_fallback",
            "mode": "step_level" if failed_step else "full_script",
            "sourceScriptId": script.get("id"),
            "sourceTraceRunId": ((script.get("source") or {}).get("traceRunId") if isinstance(script.get("source"), dict) else None),
            "sourceTraceRunIds": list(((script.get("source") or {}).get("traceRunIds") or [])) if isinstance(script.get("source"), dict) else [],
            "fallbackStepId": failed_step.get("stepId") if failed_step else None,
            "fallbackStepIndex": start_index,
            "recoveredStepCount": len(steps),
            "execution": execution,
            **({"feedbackSuggestions": feedback_suggestions} if feedback_suggestions else {}),
        }

    def _repair_trace_from_fallback(
        self,
        *,
        prepared: Dict[str, Any],
        fallback_payload: Dict[str, Any],
        failed_step: Dict[str, Any] | None = None,
    ) -> Optional[Dict[str, Any]]:
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        if not script:
            return None
        execution = fallback_payload.get("execution") if isinstance(fallback_payload.get("execution"), dict) else {}
        trace_run_id = str(execution.get("runId") or "").strip()
        trace = self.compiler.trace_store.get_trace(trace_run_id) if trace_run_id else None
        if not trace:
            executed_steps = [dict(item) for item in list(execution.get("steps") or []) if isinstance(item, dict)]
            if not executed_steps:
                return None
            synthetic_steps: list[Dict[str, Any]] = []
            for index, payload in enumerate(executed_steps, start=1):
                selector: Dict[str, Any] = {}
                if payload.get("selector_key"):
                    selector["selectorKey"] = payload.get("selector_key")
                if payload.get("element_id"):
                    selector["elementId"] = payload.get("element_id")
                if payload.get("name"):
                    selector["name"] = payload.get("name")
                if payload.get("automation_id"):
                    selector["automationId"] = payload.get("automation_id")
                if payload.get("control_type"):
                    selector["controlType"] = payload.get("control_type")
                if payload.get("class_name"):
                    selector["className"] = payload.get("class_name")
                if payload.get("handle") not in (None, ""):
                    selector["handle"] = payload.get("handle")

                window: Dict[str, Any] = {}
                if payload.get("window_title"):
                    window["title"] = payload.get("window_title")
                if payload.get("window_handle") not in (None, ""):
                    window["windowHandle"] = payload.get("window_handle")
                if payload.get("process_name"):
                    window["processName"] = payload.get("process_name")
                if payload.get("class_name"):
                    window["className"] = payload.get("class_name")

                params = {
                    key: value
                    for key, value in payload.items()
                    if not str(key).startswith("_")
                    and key
                    not in {
                        "action",
                        "name",
                        "automation_id",
                        "control_type",
                        "class_name",
                        "handle",
                        "window_title",
                        "window_handle",
                        "process_name",
                        "element_id",
                    }
                }
                synthetic_steps.append(
                    {
                        "stepId": payload.get("_draft_step_id") or f"repair_step_{index}",
                        "index": index,
                        "appId": payload.get("app_id") or script.get("appId") or "desktop",
                        "action": payload.get("action"),
                        "intent": payload.get("action"),
                        "phase": "action",
                        "params": params,
                        "rawParams": dict(params),
                        "target": {
                            "window": window,
                            "selector": selector,
                        },
                        "verification": {"passed": True, "status": "completed"},
                        "recovery": {"performed": False, "transient": False},
                        "risk": {},
                        "timing": {"attemptCount": 1, "retryLimit": 1},
                        "signals": {
                            "binding": {
                                "requestedAppId": payload.get("app_id") or script.get("appId") or "desktop",
                                "resolvedAppId": payload.get("app_id") or script.get("appId") or "desktop",
                                "bindingMode": "explicit",
                                "bindingConfidence": 1.0,
                                "bindingEvidence": {},
                            },
                            "preflight": {
                                "focusConfirmed": True,
                                "windowBound": bool(window),
                                "sceneBound": bool(window),
                                "blockerDetected": False,
                                "riskDowngraded": False,
                            },
                            "verification": {
                                "passed": True,
                                "status": "completed",
                                "level": "verified",
                            },
                            "recovery": {
                                "semanticPathTried": True,
                                "controlledFallbackTried": False,
                                "visualFallbackTried": False,
                                "strictVerificationApplied": True,
                                "finalRecoveryStage": "semantic_path",
                            },
                            "failureCategory": "unknown",
                        },
                        "metadata": {
                            "status": "completed",
                            "syntheticRepairTrace": True,
                        },
                    }
                )
            trace = {
                "version": 1,
                "runId": trace_run_id or f"repair:{script.get('id')}",
                "sessionId": execution.get("sessionId") or ((script.get("source") or {}).get("traceSessionId")),
                "runtimeKind": "computer_use",
                "goal": script.get("goal") or script.get("name") or script.get("id"),
                "metadata": {
                    "traceSchemaVersion": 2,
                    "appId": script.get("appId") or "desktop",
                    "syntheticRepairTrace": True,
                },
                "steps": synthetic_steps,
                "stepCount": len(synthetic_steps),
            }

        repaired = self.compiler.repair_script_from_trace(
            script_payload=script,
            trace=trace,
            start_index=max(0, int((failed_step or {}).get("stepIndex") or fallback_payload.get("fallbackStepIndex") or 0)),
            save=True,
        )
        return {
            "scriptId": repaired.get("id"),
            "templateId": (repaired.get("source") or {}).get("templateId"),
            "templateRevision": (repaired.get("metadata") or {}).get("templateCandidateRevision"),
            "templateGovernance": dict((repaired.get("metadata") or {}).get("templateGovernance") or {}),
            "repairTraceRunId": trace.get("runId"),
            "repairTraceSessionId": trace.get("sessionId"),
            "repairedFromStepIndex": max(0, int((failed_step or {}).get("stepIndex") or fallback_payload.get("fallbackStepIndex") or 0)),
            "patchedStepCount": int(((repaired.get("metadata") or {}).get("lastRepair") or {}).get("patchedStepCount") or 0),
            "localRepairCount": int((repaired.get("metadata") or {}).get("localRepairCount") or 0),
            "syntheticTrace": bool(dict(trace.get("metadata") or {}).get("syntheticRepairTrace")),
        }

    def _request_step_approval(self, *, run_handle, prepared: Dict[str, Any], subject: str) -> Optional[Dict[str, Any]]:
        template_policy = self._resolve_template_execution_policy(mode="draft" if isinstance(prepared.get("script"), dict) else "existing_robot", prepared=prepared)
        approvals = self._required_approvals(prepared, template_policy=template_policy)
        if not approvals:
            return None
        approval = run_handle.request_approval(
            approval_kind="rpa_review",
            request={
                "question": f"RPA 流程包含高风险步骤，是否继续执行？\n\n目标：{subject}",
                "prompt": f"RPA 流程包含高风险步骤，是否继续执行？\n\n目标：{subject}",
                "approvalKind": "rpa_review",
                "rpa": {
                    "subject": subject,
                    "scriptId": (prepared.get("script") or {}).get("id"),
                    "robotFile": prepared.get("robotFile") or (prepared.get("export") or {}).get("path"),
                    "requiredApprovals": approvals,
                },
            },
        )
        if str(approval.get("status") or "").strip().lower() != "pending":
            self._log_audit(
                action=f"RPA step approval auto-approved: {subject}",
                status="INFO",
                details=json.dumps(
                    {
                        "approvalId": approval.get("approval_id"),
                        "steps": approvals,
                        "policySource": approval.get("policySource"),
                    },
                    ensure_ascii=False,
                ),
            )
            return None
        self._log_audit(
            action=f"RPA step approval requested: {subject}",
            status="WARNING",
            details=json.dumps({"approvalId": approval.get("approval_id"), "steps": approvals}, ensure_ascii=False),
        )
        return {
            "status": "review_required",
            "outcomeFamily": outcome_family_for_execution_state("review_required"),
            "approvalId": approval.get("approval_id"),
            "requiredApprovals": approvals,
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
        }

    def _execute_computer_use_primary(
        self,
        *,
        prepared: Dict[str, Any],
        run_handle,
        subject: str,
        mode: str,
        variables: Optional[Dict[str, Any]],
        user_id: str,
        project_id: str | None,
        workspace_id: str | None,
        workspace_path: str | None,
        assessment: Dict[str, Any],
        template_policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        controlled = self._consume_or_finalize_control(
            run_handle=run_handle,
            stage="computer_use_primary_prepare",
            prepared=prepared,
            assessment=assessment,
            template_policy=template_policy,
        )
        if controlled is not None:
            return controlled
        run_handle.transition("running", reason="computer_use_first", node="rpa_runtime")
        self._update_run_metadata(
            run_handle.run_id,
            executionState="running_computer_use_primary",
            trustStatus=normalize_script_assessment_status(assessment.get("status")),
            templateExecutionPolicy=template_policy,
            templateExecutionPath=template_policy.get("executionPath"),
        )
        run_handle.emit(
            "rpa.execution.routed",
            {
                "mode": mode,
                "subject": subject,
                "routing": template_policy,
            },
        )
        run_handle.emit(
            "rpa.execution.computer_use_primary.started",
            {
                "mode": mode,
                "subject": subject,
                "routing": template_policy,
            },
        )
        execution_payload = self._run_computer_use_fallback(
            prepared=prepared,
            run_id=run_handle.run_id,
            variables=dict(variables or {}),
            session_id=run_handle.session_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            failed_step=None,
        )
        if isinstance(execution_payload, dict) and isinstance(execution_payload.get("control"), dict):
            return self._finalize_controlled(
                run_handle=run_handle,
                prepared=prepared,
                control=dict(execution_payload.get("control") or {}),
                assessment=assessment,
                template_policy=template_policy,
                execution_state=str(execution_payload.get("status") or "paused"),
                extra_payload={"computerUse": execution_payload},
            )
        if not isinstance(execution_payload, dict):
            raise RuntimeError("template governance routed to computer_use_first, but no ComputerUse plan was available")
        repair_payload = None
        try:
            repair_payload = self._repair_trace_from_fallback(
                prepared=prepared,
                fallback_payload=execution_payload,
                failed_step=None,
            )
        except Exception as repair_exc:
            repair_payload = {
                "status": "failed",
                "error": str(repair_exc),
            }
            run_handle.emit(
                "rpa.execution.repair.failed",
                {
                    "mode": mode,
                    "subject": subject,
                    "repair": repair_payload,
                },
            )
        if isinstance(repair_payload, dict) and repair_payload.get("scriptId"):
            run_handle.emit(
                "rpa.execution.repair.completed",
                {
                    "mode": mode,
                    "subject": subject,
                    "repair": repair_payload,
                },
            )
        controlled = self._consume_or_finalize_control(
            run_handle=run_handle,
            stage="computer_use_primary_finalize",
            prepared=prepared,
            assessment=assessment,
            template_policy=template_policy,
            extra_payload={"computerUse": execution_payload, "repair": repair_payload},
        )
        if controlled is not None:
            return controlled
        run_handle.emit(
            "rpa.execution.computer_use_primary.completed",
            {
                "mode": mode,
                "subject": subject,
                "computerUse": execution_payload,
                "repair": repair_payload,
                "routing": template_policy,
            },
        )
        run_handle.complete(reason="computer_use_primary", node="rpa_runtime")
        self._update_run_metadata(
            run_handle.run_id,
            executionState="completed_via_computer_use_primary",
            trustStatus=normalize_script_assessment_status(assessment.get("status")),
            templateExecutionPolicy=template_policy,
            templateExecutionPath=template_policy.get("executionPath"),
            computerUse={
                "type": "computer_use",
                "mode": execution_payload.get("mode"),
                "sourceScriptId": execution_payload.get("sourceScriptId"),
                "sourceTraceRunId": execution_payload.get("sourceTraceRunId"),
                "sourceTraceRunIds": execution_payload.get("sourceTraceRunIds"),
                "recoveredStepCount": execution_payload.get("recoveredStepCount"),
            },
            repair=repair_payload,
        )
        self._record_run_feedback(
            prepared=prepared,
            execution_state="completed_via_computer_use_primary",
            feedback={
                "computerUsePrimary": True,
                "localRepairApplied": bool(isinstance(repair_payload, dict) and repair_payload.get("scriptId")),
                "repairedSteps": int(repair_payload.get("patchedStepCount") or 0) if isinstance(repair_payload, dict) else 0,
                **self._merged_feedback_suggestions(
                    execution_payload.get("feedbackSuggestions") if isinstance(execution_payload, dict) else {}
                ),
            },
        )
        return {
            **prepared,
            "status": "completed_via_computer_use_primary",
            "outcomeFamily": outcome_family_for_execution_state("completed_via_computer_use_primary"),
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
            "templateExecutionPolicy": template_policy,
            "computerUse": execution_payload,
            **({"repair": repair_payload} if repair_payload is not None else {}),
        }

    def _execute_prepared(
        self,
        *,
        prepared: Dict[str, Any],
        subject: str,
        mode: str,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: str | Path | None = None,
        timeout_ms: int = 600000,
        cwd: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "system",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        trigger_source: str | None = "manual",
    ) -> Dict[str, Any]:
        effective_session_id = self._resolve_session_id(
            script_id=(prepared.get("script") or {}).get("id"),
            robot_file=prepared.get("robotFile") or (prepared.get("export") or {}).get("path"),
            session_id=session_id,
        )
        metadata = self._build_run_metadata(
            mode=mode,
            prepared=prepared,
            variables=dict(variables or {}),
            trigger_source=trigger_source,
            cwd=cwd,
        )
        run_handle = self._begin_run(
            session_id=effective_session_id,
            user_id=user_id,
            trigger_source=trigger_source,
            metadata=metadata,
            title=self._build_session_title(
                script_id=(prepared.get("script") or {}).get("id"),
                robot_file=prepared.get("robotFile") or (prepared.get("export") or {}).get("path"),
            ),
            run_id=run_id,
        )
        workflow_ledger_service.activate_runtime_step(
            run_handle.run_id,
            owner_runtime="rpa",
            step_key="rpa.execute_draft" if mode == "draft" else "rpa.execute_existing",
            title="RPA 执行流程",
            owner_agent_id="rpa_runtime",
            input_payload={
                "mode": mode,
                "subject": subject,
                "scriptId": (prepared.get("script") or {}).get("id") if isinstance(prepared.get("script"), dict) else None,
                "robotFile": prepared.get("robotFile") or (prepared.get("export") or {}).get("path"),
            },
        )
        preflight = self._handle_preflight_decision(
            run_handle=run_handle,
            decision=self._run_preflight(run_handle=run_handle, trigger_source=trigger_source, user_id=user_id),
            trigger_source=trigger_source,
            subject=subject,
        )
        if preflight is not None:
            self._update_run_metadata(
                run_handle.run_id,
                executionState=preflight.get("status"),
                reason=preflight.get("reason"),
                approvalId=preflight.get("approvalId"),
            )
            return {**prepared, **preflight, "templateExecutionPolicy": self._resolve_template_execution_policy(mode=mode, prepared=prepared)}

        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        assessment = script.get("assessment") if isinstance(script.get("assessment"), dict) else {}
        normalized_assessment_status = normalize_script_assessment_status(assessment.get("status"))
        if assessment:
            assessment = {**assessment, "status": normalized_assessment_status}
        template_policy = self._resolve_template_execution_policy(mode=mode, prepared=prepared)
        controlled = self._consume_or_finalize_control(
            run_handle=run_handle,
            stage="pre_execute",
            prepared=prepared,
            assessment=assessment,
            template_policy=template_policy,
        )
        if controlled is not None:
            return controlled
        if assessment and normalized_assessment_status == "compile_blocked" and not bool(template_policy.get("bypassCompileBlock")):
            return self._compile_block_result(
                prepared=prepared,
                run_handle=run_handle,
                assessment=assessment,
                subject=subject,
                template_policy=template_policy,
            )
        if assessment and normalized_assessment_status == "compile_blocked" and bool(template_policy.get("bypassCompileBlock")):
            run_handle.emit(
                "rpa.execution.policy.override",
                {
                    "mode": mode,
                    "subject": subject,
                    "reason": "compile_block_bypassed_by_template_policy",
                    "routing": template_policy,
                    "assessment": assessment,
                },
            )

        step_approval = self._request_step_approval(run_handle=run_handle, prepared=prepared, subject=subject)
        if step_approval is not None:
            self._update_run_metadata(
                run_handle.run_id,
                executionState="review_required",
                approvalId=step_approval.get("approvalId"),
                requiredApprovals=step_approval.get("requiredApprovals"),
                trustStatus=normalized_assessment_status,
                templateExecutionPolicy=template_policy,
                templateExecutionPath=template_policy.get("executionPath"),
            )
            self._record_run_feedback(prepared=prepared, execution_state="review_required")
            return {
                **prepared,
                **step_approval,
                "outcomeFamily": outcome_family_for_execution_state("review_required"),
                "templateExecutionPolicy": template_policy,
            }

        if str(template_policy.get("executionPath") or "").strip() == "computer_use_first":
            return self._execute_computer_use_primary(
                prepared=prepared,
                run_handle=run_handle,
                subject=subject,
                mode=mode,
                variables=variables,
                user_id=user_id,
                project_id=project_id,
                workspace_id=workspace_id,
                workspace_path=workspace_path,
                assessment=assessment,
                template_policy=template_policy,
            )

        controlled = self._consume_or_finalize_control(
            run_handle=run_handle,
            stage="before_robot_start",
            prepared=prepared,
            assessment=assessment,
            template_policy=template_policy,
        )
        if controlled is not None:
            return controlled
        run_handle.transition("running", reason=trigger_source or "manual", node="rpa_runtime")
        self._update_run_metadata(
            run_handle.run_id,
            executionState="running_robot",
            trustStatus=normalized_assessment_status,
            templateExecutionPolicy=template_policy,
            templateExecutionPath=template_policy.get("executionPath"),
        )
        run_handle.emit(
            "rpa.execution.routed",
            {
                "mode": mode,
                "subject": subject,
                "routing": template_policy,
            },
        )
        run_handle.emit(
            "rpa.execution.started",
            {
                "mode": mode,
                "subject": subject,
                "command": list(prepared.get("command") or []),
                "cwd": cwd,
                "routing": template_policy,
            },
        )
        self._log_audit(
            action=f"RPA execution started: {subject}",
            status="INFO",
            details=json.dumps({"runId": run_handle.run_id, "mode": mode}, ensure_ascii=False),
        )

        try:
            command_receipt = side_effect_idempotency_service.begin(
                run_handle=run_handle,
                effect_kind="rpa.external_command",
                step_key=f"rpa.execute.{mode}",
                target_identity="|".join(
                    part
                    for part in [
                        str(mode or "").strip(),
                        str(subject or "").strip(),
                        str(cwd or workspace_path or "").strip(),
                    ]
                    if part
                ),
                payload={
                    "command": list(prepared.get("command") or []),
                    "timeoutMs": timeout_ms,
                    "cwd": cwd or workspace_path,
                    "subject": subject,
                    "mode": mode,
                },
                node="rpa_runtime",
                metadata={"subject": subject, "mode": mode},
            )
            if not command_receipt.execute:
                execution = {
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                    "skippedDuplicate": True,
                    "receipt": command_receipt.as_dict(),
                }
                run_handle.emit(
                    "rpa.execution.deduplicated",
                    {
                        "mode": mode,
                        "subject": subject,
                        "receipt": command_receipt.as_dict(),
                    },
                )
            else:
                with bind_runtime_context(
                    runtime_kind="rpa",
                    trigger_source=trigger_source,
                    session_id=run_handle.session_id,
                    run_id=run_handle.run_id,
                    user_id=user_id,
                    project_id=project_id,
                    workspace_id=workspace_id,
                ):
                    execution = self.adapter.run_command(
                        command=list(prepared["command"]),
                        timeout_ms=timeout_ms,
                        cwd=cwd or workspace_path,
                    )
                side_effect_idempotency_service.complete(
                    run_handle=run_handle,
                    receipt=command_receipt,
                    node="rpa_runtime",
                    result={"returncode": execution.get("returncode"), "subject": subject, "mode": mode},
                )
            run_handle.emit(
                "rpa.execution.finished",
                {
                    "mode": mode,
                    "subject": subject,
                    "returncode": execution.get("returncode"),
                    "stdout": execution.get("stdout"),
                    "stderr": execution.get("stderr"),
                },
            )
            controlled = self._consume_or_finalize_control(
                run_handle=run_handle,
                stage="after_robot_execution",
                prepared=prepared,
                assessment=assessment,
                template_policy=template_policy,
                extra_payload={"execution": execution},
            )
            if controlled is not None:
                return controlled
            if int(execution.get("returncode") or 0) != 0:
                error_message = execution.get("stderr") or execution.get("stdout") or f"Robot 流程执行失败: {subject}"
                controlled = self._consume_or_finalize_control(
                    run_handle=run_handle,
                    stage="before_fallback",
                    prepared=prepared,
                    assessment=assessment,
                    template_policy=template_policy,
                    extra_payload={"execution": execution},
                )
                if controlled is not None:
                    return controlled
                fallback_payload = None
                failed_step = self._extract_failed_step_context(execution=execution, script=script)
                if self._supports_computer_use_fallback(mode=mode, prepared=prepared):
                    run_handle.emit(
                        "rpa.execution.fallback.started",
                        {
                            "mode": mode,
                            "subject": subject,
                            "reason": error_message,
                            "fallback": "computer_use",
                            "failedStep": failed_step or None,
                        },
                    )
                    try:
                        self._update_run_metadata(
                            run_handle.run_id,
                            executionState="fallback_running",
                            trustStatus=normalize_script_assessment_status(assessment.get("status")),
                            fallbackStepId=failed_step.get("stepId") if failed_step else None,
                            fallbackStepIndex=failed_step.get("stepIndex") if failed_step else None,
                            templateExecutionPolicy=template_policy,
                            templateExecutionPath=template_policy.get("executionPath"),
                        )
                        fallback_payload = self._run_computer_use_fallback(
                            prepared=prepared,
                            run_id=run_handle.run_id,
                            variables=dict(variables or {}),
                            session_id=run_handle.session_id,
                            user_id=user_id,
                            project_id=project_id,
                            workspace_id=workspace_id,
                            workspace_path=workspace_path,
                            failed_step=failed_step,
                        )
                        if isinstance(fallback_payload, dict) and isinstance(fallback_payload.get("control"), dict):
                            return self._finalize_controlled(
                                run_handle=run_handle,
                                prepared=prepared,
                                control=dict(fallback_payload.get("control") or {}),
                                assessment=assessment,
                                template_policy=template_policy,
                                execution_state=str(fallback_payload.get("status") or "paused"),
                                extra_payload={
                                    "execution": execution,
                                    "fallback": fallback_payload,
                                },
                            )
                        repair_payload = None
                        try:
                            repair_payload = self._repair_trace_from_fallback(
                                prepared=prepared,
                                fallback_payload=fallback_payload,
                                failed_step=failed_step,
                            )
                            if repair_payload is not None:
                                run_handle.emit(
                                    "rpa.execution.repair.completed",
                                    {
                                        "mode": mode,
                                        "subject": subject,
                                        "repair": repair_payload,
                                    },
                                )
                        except Exception as repair_exc:
                            repair_payload = {
                                "status": "failed",
                                "error": str(repair_exc),
                            }
                            run_handle.emit(
                                "rpa.execution.repair.failed",
                                {
                                    "mode": mode,
                                    "subject": subject,
                                    "repair": repair_payload,
                                },
                            )
                        run_handle.emit(
                            "rpa.execution.fallback.completed",
                            {
                                "mode": mode,
                                "subject": subject,
                                "fallback": fallback_payload,
                                "repair": repair_payload,
                            },
                        )
                        controlled = self._consume_or_finalize_control(
                            run_handle=run_handle,
                            stage="fallback_finalize",
                            prepared=prepared,
                            assessment=assessment,
                            template_policy=template_policy,
                            extra_payload={"execution": execution, "fallback": fallback_payload, "repair": repair_payload},
                        )
                        if controlled is not None:
                            return controlled
                        run_handle.complete(reason="computer_use_fallback", node="rpa_runtime")
                        self._update_run_metadata(
                            run_handle.run_id,
                            executionState="completed_with_fallback",
                            trustStatus=normalize_script_assessment_status(assessment.get("status")),
                            templateExecutionPolicy=template_policy,
                            templateExecutionPath=template_policy.get("executionPath"),
                            fallback={
                                "type": "computer_use",
                                "mode": fallback_payload.get("mode") if isinstance(fallback_payload, dict) else None,
                                "sourceScriptId": fallback_payload.get("sourceScriptId") if isinstance(fallback_payload, dict) else None,
                                "sourceTraceRunId": fallback_payload.get("sourceTraceRunId") if isinstance(fallback_payload, dict) else None,
                                "sourceTraceRunIds": fallback_payload.get("sourceTraceRunIds") if isinstance(fallback_payload, dict) else None,
                                "fallbackStepId": fallback_payload.get("fallbackStepId") if isinstance(fallback_payload, dict) else None,
                                "fallbackStepIndex": fallback_payload.get("fallbackStepIndex") if isinstance(fallback_payload, dict) else None,
                                "recoveredStepCount": fallback_payload.get("recoveredStepCount") if isinstance(fallback_payload, dict) else None,
                            },
                            repair=repair_payload,
                        )
                        self._log_audit(
                            action=f"RPA execution completed via fallback: {subject}",
                            status="SUCCESS",
                            details=json.dumps(
                                {
                                    "runId": run_handle.run_id,
                                    "returncode": execution.get("returncode"),
                                    "fallback": "computer_use",
                                },
                                ensure_ascii=False,
                            ),
                        )
                        self._record_run_feedback(
                            prepared=prepared,
                            execution_state="completed_with_fallback",
                            feedback={
                                "stepLevelFallback": bool(isinstance(fallback_payload, dict) and fallback_payload.get("mode") == "step_level"),
                                "recoveredSteps": int(fallback_payload.get("recoveredStepCount") or 0) if isinstance(fallback_payload, dict) else 0,
                                "localRepairApplied": bool(isinstance(repair_payload, dict) and repair_payload.get("scriptId")),
                                "repairedSteps": int(repair_payload.get("patchedStepCount") or 0) if isinstance(repair_payload, dict) else 0,
                                **self._merged_feedback_suggestions(
                                    fallback_payload.get("feedbackSuggestions") if isinstance(fallback_payload, dict) else {}
                                ),
                            },
                        )
                        return {
                            **prepared,
                            "status": "completed_with_fallback",
                            "outcomeFamily": outcome_family_for_execution_state("completed_with_fallback"),
                            "runId": run_handle.run_id,
                            "sessionId": run_handle.session_id,
                            "execution": execution,
                            "templateExecutionPolicy": template_policy,
                            "fallback": fallback_payload,
                            **({"repair": repair_payload} if repair_payload is not None else {}),
                        }
                    except Exception as fallback_exc:
                        fallback_payload = {
                            "status": "failed",
                            "type": "computer_use_fallback",
                            "error": str(fallback_exc),
                        }
                        run_handle.emit(
                            "rpa.execution.fallback.failed",
                            {
                                "mode": mode,
                                "subject": subject,
                                "reason": error_message,
                                "fallback": fallback_payload,
                            },
                        )
                        self._update_run_metadata(
                            run_handle.run_id,
                            executionState="fallback_failed",
                            trustStatus=normalize_script_assessment_status(assessment.get("status")),
                            templateExecutionPolicy=template_policy,
                            templateExecutionPath=template_policy.get("executionPath"),
                            fallback=fallback_payload,
                        )
                run_handle.fail(error_message, node="rpa_runtime")
                self._update_run_metadata(
                    run_handle.run_id,
                    executionState="failed",
                    trustStatus=normalize_script_assessment_status(assessment.get("status")),
                    templateExecutionPolicy=template_policy,
                    templateExecutionPath=template_policy.get("executionPath"),
                    error=error_message,
                )
                self._log_audit(
                    action=f"RPA execution failed: {subject}",
                    status="ERROR",
                    details=json.dumps({"runId": run_handle.run_id, "returncode": execution.get("returncode")}, ensure_ascii=False),
                )
                self._record_run_feedback(
                    prepared=prepared,
                    execution_state="fallback_failed"
                    if isinstance(fallback_payload, dict) and str(fallback_payload.get("type") or "").strip() == "computer_use_fallback"
                    else "failed",
                    feedback={
                        "stepLevelFallback": bool(isinstance(fallback_payload, dict) and fallback_payload.get("mode") == "step_level"),
                        "recoveredSteps": int(fallback_payload.get("recoveredStepCount") or 0) if isinstance(fallback_payload, dict) else 0,
                        **self._merged_feedback_suggestions(
                            fallback_payload.get("feedbackSuggestions") if isinstance(fallback_payload, dict) else {}
                        ),
                    },
                )
                return {
                    **prepared,
                    "status": "failed",
                    "outcomeFamily": outcome_family_for_execution_state("failed"),
                    "runId": run_handle.run_id,
                    "sessionId": run_handle.session_id,
                    "execution": execution,
                    "templateExecutionPolicy": template_policy,
                    **({"fallback": fallback_payload} if fallback_payload is not None else {}),
                }
            controlled = self._consume_or_finalize_control(
                run_handle=run_handle,
                stage="success_finalize",
                prepared=prepared,
                assessment=assessment,
                template_policy=template_policy,
                extra_payload={"execution": execution},
            )
            if controlled is not None:
                return controlled
            run_handle.complete(reason="rpa_finished", node="rpa_runtime")
            self._update_run_metadata(
                run_handle.run_id,
                executionState="completed",
                trustStatus=normalize_script_assessment_status(assessment.get("status")),
                templateExecutionPolicy=template_policy,
                templateExecutionPath=template_policy.get("executionPath"),
            )
            self._log_audit(
                action=f"RPA execution completed: {subject}",
                status="SUCCESS",
                details=json.dumps({"runId": run_handle.run_id, "returncode": execution.get("returncode")}, ensure_ascii=False),
            )
            self._record_run_feedback(prepared=prepared, execution_state="completed")
            return {
                **prepared,
                "status": "completed",
                "outcomeFamily": outcome_family_for_execution_state("completed"),
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
                "execution": execution,
                "templateExecutionPolicy": template_policy,
            }
        except Exception as exc:
            error_message = str(exc)
            controlled = self._consume_or_finalize_control(
                run_handle=run_handle,
                stage="exception",
                prepared=prepared,
                assessment=assessment,
                template_policy=template_policy,
                extra_payload={"error": error_message},
            )
            if controlled is not None:
                return controlled
            fallback_payload = None
            failed_step = {}
            if self._supports_computer_use_fallback(mode=mode, prepared=prepared):
                run_handle.emit(
                    "rpa.execution.fallback.started",
                    {
                        "mode": mode,
                        "subject": subject,
                        "reason": error_message,
                        "fallback": "computer_use",
                    },
                )
                try:
                    self._update_run_metadata(
                        run_handle.run_id,
                        executionState="fallback_running",
                        trustStatus=normalize_script_assessment_status(assessment.get("status")),
                        templateExecutionPolicy=template_policy,
                        templateExecutionPath=template_policy.get("executionPath"),
                    )
                    fallback_payload = self._run_computer_use_fallback(
                        prepared=prepared,
                        run_id=run_handle.run_id,
                        variables=dict(variables or {}),
                        session_id=run_handle.session_id,
                        user_id=user_id,
                        project_id=project_id,
                        workspace_id=workspace_id,
                        workspace_path=workspace_path,
                        failed_step=failed_step,
                    )
                    if isinstance(fallback_payload, dict) and isinstance(fallback_payload.get("control"), dict):
                        return self._finalize_controlled(
                            run_handle=run_handle,
                            prepared=prepared,
                            control=dict(fallback_payload.get("control") or {}),
                            assessment=assessment,
                            template_policy=template_policy,
                            execution_state=str(fallback_payload.get("status") or "paused"),
                            extra_payload={"fallback": fallback_payload, "error": error_message},
                        )
                    repair_payload = None
                    try:
                        repair_payload = self._repair_trace_from_fallback(
                            prepared=prepared,
                            fallback_payload=fallback_payload,
                            failed_step=failed_step,
                        )
                        if repair_payload is not None:
                            run_handle.emit(
                                "rpa.execution.repair.completed",
                                {
                                    "mode": mode,
                                    "subject": subject,
                                    "repair": repair_payload,
                                },
                            )
                    except Exception as repair_exc:
                        repair_payload = {
                            "status": "failed",
                            "error": str(repair_exc),
                        }
                        run_handle.emit(
                            "rpa.execution.repair.failed",
                            {
                                "mode": mode,
                                "subject": subject,
                                "repair": repair_payload,
                            },
                        )
                    run_handle.emit(
                        "rpa.execution.fallback.completed",
                        {
                            "mode": mode,
                            "subject": subject,
                            "fallback": fallback_payload,
                            "repair": repair_payload,
                        },
                    )
                    controlled = self._consume_or_finalize_control(
                        run_handle=run_handle,
                        stage="exception_fallback_finalize",
                        prepared=prepared,
                        assessment=assessment,
                        template_policy=template_policy,
                        extra_payload={"fallback": fallback_payload, "repair": repair_payload, "error": error_message},
                    )
                    if controlled is not None:
                        return controlled
                    run_handle.complete(reason="computer_use_fallback", node="rpa_runtime")
                    self._update_run_metadata(
                        run_handle.run_id,
                        executionState="completed_with_fallback",
                        trustStatus=normalize_script_assessment_status(assessment.get("status")),
                        templateExecutionPolicy=template_policy,
                        templateExecutionPath=template_policy.get("executionPath"),
                        fallback={
                            "type": "computer_use",
                            "mode": fallback_payload.get("mode") if isinstance(fallback_payload, dict) else None,
                            "sourceScriptId": fallback_payload.get("sourceScriptId") if isinstance(fallback_payload, dict) else None,
                            "sourceTraceRunId": fallback_payload.get("sourceTraceRunId") if isinstance(fallback_payload, dict) else None,
                            "sourceTraceRunIds": fallback_payload.get("sourceTraceRunIds") if isinstance(fallback_payload, dict) else None,
                            "fallbackStepId": fallback_payload.get("fallbackStepId") if isinstance(fallback_payload, dict) else None,
                            "fallbackStepIndex": fallback_payload.get("fallbackStepIndex") if isinstance(fallback_payload, dict) else None,
                            "recoveredStepCount": fallback_payload.get("recoveredStepCount") if isinstance(fallback_payload, dict) else None,
                        },
                        repair=repair_payload,
                    )
                    self._log_audit(
                        action=f"RPA execution completed via fallback: {subject}",
                        status="SUCCESS",
                        details=json.dumps(
                            {"runId": run_handle.run_id, "fallback": "computer_use"},
                            ensure_ascii=False,
                        ),
                    )
                    self._record_run_feedback(
                        prepared=prepared,
                        execution_state="completed_with_fallback",
                        feedback={
                            "stepLevelFallback": bool(isinstance(fallback_payload, dict) and fallback_payload.get("mode") == "step_level"),
                            "recoveredSteps": int(fallback_payload.get("recoveredStepCount") or 0) if isinstance(fallback_payload, dict) else 0,
                            "localRepairApplied": bool(isinstance(repair_payload, dict) and repair_payload.get("scriptId")),
                            "repairedSteps": int(repair_payload.get("patchedStepCount") or 0) if isinstance(repair_payload, dict) else 0,
                            **self._merged_feedback_suggestions(
                                fallback_payload.get("feedbackSuggestions") if isinstance(fallback_payload, dict) else {}
                            ),
                        },
                    )
                    return {
                        **prepared,
                        "status": "completed_with_fallback",
                        "outcomeFamily": outcome_family_for_execution_state("completed_with_fallback"),
                        "runId": run_handle.run_id,
                        "sessionId": run_handle.session_id,
                        "error": error_message,
                        "templateExecutionPolicy": template_policy,
                        "fallback": fallback_payload,
                        **({"repair": repair_payload} if repair_payload is not None else {}),
                    }
                except Exception as fallback_exc:
                    fallback_payload = {
                        "status": "failed",
                        "type": "computer_use_fallback",
                        "error": str(fallback_exc),
                    }
                    run_handle.emit(
                        "rpa.execution.fallback.failed",
                        {
                            "mode": mode,
                            "subject": subject,
                            "reason": error_message,
                            "fallback": fallback_payload,
                        },
                    )
                    self._update_run_metadata(
                        run_handle.run_id,
                        executionState="fallback_failed",
                        trustStatus=normalize_script_assessment_status(assessment.get("status")),
                        templateExecutionPolicy=template_policy,
                        templateExecutionPath=template_policy.get("executionPath"),
                        fallback=fallback_payload,
                    )
            run_handle.emit(
                "rpa.execution.failed",
                {"mode": mode, "subject": subject, "error": error_message},
            )
            run_handle.fail(error_message, node="rpa_runtime")
            self._update_run_metadata(
                run_handle.run_id,
                executionState="failed",
                trustStatus=normalize_script_assessment_status(assessment.get("status")),
                templateExecutionPolicy=template_policy,
                templateExecutionPath=template_policy.get("executionPath"),
                error=error_message,
            )
            self._log_audit(action=f"RPA execution failed: {subject}", status="ERROR", details=error_message)
            self._record_run_feedback(
                prepared=prepared,
                execution_state="fallback_failed"
                if isinstance(fallback_payload, dict) and str(fallback_payload.get("type") or "").strip() == "computer_use_fallback"
                else "failed",
                feedback={
                    "stepLevelFallback": bool(isinstance(fallback_payload, dict) and fallback_payload.get("mode") == "step_level"),
                    "recoveredSteps": int(fallback_payload.get("recoveredStepCount") or 0) if isinstance(fallback_payload, dict) else 0,
                    **self._merged_feedback_suggestions(
                        fallback_payload.get("feedbackSuggestions") if isinstance(fallback_payload, dict) else {}
                    ),
                },
            )
            return {
                **prepared,
                "status": "failed",
                "outcomeFamily": outcome_family_for_execution_state("failed"),
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
                "error": error_message,
                "templateExecutionPolicy": template_policy,
                **({"fallback": fallback_payload} if fallback_payload is not None else {}),
            }

    def run_draft(
        self,
        *,
        script_id: str,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: str | Path | None = None,
        timeout_ms: int = 600000,
        cwd: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "system",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        trigger_source: str | None = "manual",
    ) -> Dict[str, Any]:
        prepared = self.prepare_draft_run(script_id=script_id, variables=variables, output_dir=output_dir)
        subject = str(((prepared.get("script") or {}).get("name")) or script_id)
        return self._execute_prepared(
            prepared=prepared,
            subject=subject,
            mode="draft",
            variables=variables,
            output_dir=output_dir,
            timeout_ms=timeout_ms,
            cwd=cwd,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            trigger_source=trigger_source,
        )

    def run_existing_flow(
        self,
        *,
        robot_file: str | Path,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: str | Path | None = None,
        timeout_ms: int = 600000,
        cwd: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "system",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        trigger_source: str | None = "manual",
    ) -> Dict[str, Any]:
        prepared = self.prepare_existing_run(
            robot_file=robot_file,
            variables=variables,
            output_dir=output_dir,
        )
        subject = str(Path(robot_file).name)
        return self._execute_prepared(
            prepared=prepared,
            subject=subject,
            mode="existing_robot",
            variables=variables,
            output_dir=output_dir,
            timeout_ms=timeout_ms,
            cwd=cwd,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            trigger_source=trigger_source,
        )


rpa_runtime = runtime_registry.register(RPARuntime())
