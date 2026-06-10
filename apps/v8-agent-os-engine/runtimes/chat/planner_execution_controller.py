from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from core.delegation_broker import expand_delegation_task_briefs
from core.time_truth import utc_now_iso
from erc.workflow_ledger import workflow_ledger_service
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel
from runtimes.chat.planner_orchestration import (
    PLANNER_MAX_REGISTRY_AGENTS,
    PLANNER_MAX_SKILL_REFERENCES,
    PLANNER_MODEL_MAX_TOKENS,
    PLANNER_MODEL_TIMEOUT_SECONDS,
    PLANNER_OUTPUT_MODE,
    PLANNER_REPAIR_MAX_TOKENS,
    PLANNER_REPAIR_TIMEOUT_SECONDS,
    PlannerPlanPayload,
    ainvoke_model_off_event_loop,
)
from runtimes.engineering.service import engineering_lane_service


def extract_planner_payload_from_text(value: Any) -> Any | None:
    text = str(getattr(value, "content", value) or "").strip()
    if not text:
        return None
    candidates = [text]
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    candidates.extend(candidate.strip() for candidate in fenced if candidate.strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            candidates.append(text[start : end + 1])
    for candidate in candidates:
        stripped = candidate.strip()
        if not stripped:
            continue
        try:
            return json.loads(stripped)
        except Exception:
            continue
    return None


def compact_planner_error(error: Any, *, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(error or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


async def try_repair_planner_plan_with_plain_json(
    chat_runtime: Any,
    *,
    planner_user_message: str,
    fallback_plan: dict[str, Any],
    structured_error: str,
    planner_repair_max_tokens: int = PLANNER_REPAIR_MAX_TOKENS,
    planner_repair_timeout_seconds: float = PLANNER_REPAIR_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        repair_model = chat_runtime._create_planner_chat_model(
            streaming=False,
            temperature=0,
            max_tokens=planner_repair_max_tokens,
            _request_kind="planner",
        )
        repair_prompt = (
            chat_runtime._planner_system_prompt()
            + "\n\nStructured-output validation failed. Repair your response by returning ONLY a valid JSON object "
            "matching the planner contract. Do not return a bare array. If you only know capability rows, wrap them "
            "inside capabilityPlan and provide matching taskBriefs.\n"
            f"Validation error: {structured_error[:1200]}"
        )
        raw_response = await ainvoke_model_off_event_loop(
            repair_model,
            [
                SystemMessage(content=repair_prompt),
                HumanMessage(content=planner_user_message),
            ],
            timeout_seconds=planner_repair_timeout_seconds,
        )
        parsed = extract_planner_payload_from_text(raw_response)
        if parsed is None:
            return None, "planner_repair_no_json_payload"
        plan = chat_runtime._normalize_planner_plan_payload(parsed, fallback_plan=fallback_plan)
        flags = [str(item).strip() for item in list(plan.get("qualityFlags") or []) if str(item).strip()]
        flags.append("planner_plain_json_repair_used")
        plan["qualityFlags"] = list(dict.fromkeys(flags))
        return plan, None
    except asyncio.TimeoutError:
        return None, f"planner_repair_timeout_after_{planner_repair_timeout_seconds:g}s"
    except Exception as exc:
        return None, f"planner_repair_failed: {exc}"


def build_planner_plan_created_payload(
    *,
    plan: dict[str, Any],
    expanded_task_briefs: list[dict[str, Any]],
    auto_dispatch_decision: dict[str, Any],
    chat_run: Any,
    planning_error: str | None,
    planner_repair_note: str | None,
) -> dict[str, Any]:
    return {
        "planId": plan.get("planId"),
        "executionStrategy": plan.get("executionStrategy"),
        "planSummary": plan.get("planSummary"),
        "macroTaskCount": len(list(plan.get("taskBriefs") or [])),
        "taskCount": len(expanded_task_briefs),
        "taskBriefs": list(plan.get("taskBriefs") or []),
        "dependencies": [
            {
                "taskBriefId": item.get("taskBriefId"),
                "dependency": list(item.get("dependency") or []),
                "parallelGroup": item.get("parallelGroup"),
            }
            for item in list(plan.get("taskGraph") or [])
        ],
        "globalAcceptanceContract": plan.get("globalAcceptanceContract"),
        "riskFlags": list(plan.get("riskFlags") or []),
        "codingPlannerContract": plan.get("codingPlannerContract") if isinstance(plan.get("codingPlannerContract"), dict) else {},
        "engineeringEvidenceGraphDigest": plan.get("engineeringEvidenceGraphDigest") if isinstance(plan.get("engineeringEvidenceGraphDigest"), dict) else {},
        "qualityFlags": list(plan.get("qualityFlags") or []),
        "repairCount": int(plan.get("repairCount") or 0),
        "autoDispatchDecision": auto_dispatch_decision,
        "dispatchEligibilityReason": plan.get("dispatchEligibilityReason"),
        "traceRef": {"runId": chat_run.active_run_id, "planId": plan.get("planId")},
        "usedFallback": bool(planning_error),
        "error": planning_error,
        "repair": planner_repair_note,
    }


def emit_planner_outcome_events(
    *,
    chat_run: Any,
    plan: dict[str, Any],
    payload: dict[str, Any],
    planning_error: str | None,
    planner_repair_note: str | None,
    planner_timeout_seconds: float,
) -> None:
    if planning_error:
        chat_run.emit_runtime_event(
            "planner.fallback.used",
            {
                "planId": plan.get("planId"),
                "summary": "Planner lane used deterministic fallback.",
                "reason": planning_error,
                "timeoutSeconds": planner_timeout_seconds
                if planning_error.startswith("planner_model_timeout_after_")
                else None,
                "messageSurfacePriority": "diagnostic",
                "traceRef": {"runId": chat_run.active_run_id, "planId": plan.get("planId")},
            },
            agent_id=None,
            node="planner_lane",
        )
        chat_run.emit_runtime_event(
            "planner.plan.failed",
            {
                "planId": plan.get("planId"),
                "summary": "Planner lane failed over to deterministic fallback.",
                "error": planning_error,
                "messageSurfacePriority": "diagnostic",
                "traceRef": {"runId": chat_run.active_run_id, "planId": plan.get("planId")},
            },
            agent_id=None,
            node="planner_lane",
        )
    elif planner_repair_note:
        chat_run.emit_runtime_event(
            "planner.output.repaired",
            {
                "planId": plan.get("planId"),
                "summary": "Planner lane repaired invalid structured output and kept model-authored plan.",
                "repair": planner_repair_note,
                "qualityFlags": list(plan.get("qualityFlags") or []),
                "messageSurfacePriority": "diagnostic",
                "traceRef": {"runId": chat_run.active_run_id, "planId": plan.get("planId")},
            },
            agent_id=None,
            node="planner_lane",
        )
    chat_run.emit_runtime_event(
        "planner.plan.created",
        payload,
        agent_id=None,
        node="planner_lane",
    )


async def ensure_planner_plan(
    chat_runtime: Any,
    *,
    chat_run: Any,
    timeout_seconds: float | None = None,
    defer_on_timeout: bool = False,
    planner_model_timeout_seconds: float = PLANNER_MODEL_TIMEOUT_SECONDS,
    planner_model_max_tokens: int = PLANNER_MODEL_MAX_TOKENS,
    planner_output_mode: str = PLANNER_OUTPUT_MODE,
    planner_max_registry_agents: int = PLANNER_MAX_REGISTRY_AGENTS,
    planner_max_skill_references: int = PLANNER_MAX_SKILL_REFERENCES,
) -> dict[str, Any] | None:
    if not chat_run.prepared.task_planning_mode or str(chat_run.prepared.planner_mode or "off").strip().lower() == "off":
        chat_run.prepared.planner_plan = None
        return None
    if chat_run.prepared.is_resume_request:
        return chat_run.prepared.planner_plan
    if isinstance(chat_run.prepared.planner_plan, dict) and chat_run.prepared.planner_plan:
        return chat_run.prepared.planner_plan

    registry = chat_runtime._planner_registry_snapshot()
    planner_request = {
        "plannerMode": chat_run.prepared.planner_mode,
        "taskPlanningMode": chat_run.prepared.task_planning_mode,
        "specMode": bool(getattr(chat_run.prepared, "spec_mode", False)),
        "specId": str(getattr(chat_run.prepared, "spec_id", "") or ""),
        "specBrief": dict(getattr(chat_run.prepared, "spec_brief", None) or {}),
        "intentDiagnostics": dict(chat_run.prepared.planner_intent_diagnostics or {}),
        "userRequest": str(chat_run.prepared.latest_user_content or "").strip(),
        "sessionScope": {
            "projectId": chat_run.scope_result.binding.project_id,
            "workspaceId": chat_run.scope_result.binding.workspace_id,
            "workspacePath": chat_run.scope_result.binding.workspace_path,
            "resolvedScope": chat_run.scope_result.binding.resolved_scope,
        },
        "skillReferences": [
            {
                "name": chat_runtime._planner_clip_text(item.get("name"), limit=80),
                "description": chat_runtime._planner_clip_text(item.get("description"), limit=180),
                "path": item.get("path"),
            }
            for item in list(chat_run.prepared.skill_references or [])[:planner_max_skill_references]
        ],
        "engineering": {
            "triggerDecision": dict(chat_run.prepared.engineering_trigger_decision or {}),
            "evidenceGraphDigest": (
                ((chat_run.prepared.engineering_context_pack or {}).get("contextPack") or {}).get("evidenceGraphDigest")
                if isinstance(chat_run.prepared.engineering_context_pack, dict)
                else {}
            ),
            "codingPlannerContractPreview": (
                ((chat_run.prepared.engineering_context_pack or {}).get("contextPack") or {}).get("codingPlannerContractPreview")
                if isinstance(chat_run.prepared.engineering_context_pack, dict)
                else {}
            ),
        },
        "specialists": {
            "localSubagents": [
                chat_runtime._planner_compact_specialist_entry(agent)
                for agent in list(registry.get("subagents") or [])[:planner_max_registry_agents]
            ],
            "externalWorkers": [
                chat_runtime._planner_compact_specialist_entry(worker, external=True)
                for worker in list(registry.get("externalWorkers") or [])[:planner_max_registry_agents]
            ],
        },
    }
    planner_user_message = (
        "[Planner Request]\n"
        f"Current Time: {utc_now_iso()}\n"
        f"Planner Mode: {chat_run.prepared.planner_mode}\n"
        f"Intent Signals: {', '.join(list(chat_run.prepared.planner_intent_diagnostics.get('signals') or [])) or str(chat_run.prepared.planner_intent_diagnostics.get('reason') or 'manual')}\n"
        f"User Request:\n{str(chat_run.prepared.latest_user_content or '').strip() or '(empty request)'}\n\n"
        "[Specialist Registry]\n"
        + "\n".join(chat_runtime._planner_registry_lines(registry))
        + "\n\n[Planner Input JSON]\n"
        + json.dumps(planner_request, ensure_ascii=False, separators=(",", ":"))
    )

    fallback_plan = chat_runtime._fallback_planner_plan(chat_run=chat_run, reason="planner_model_unavailable")
    plan = fallback_plan
    planning_error: str | None = None
    planner_repair_note: str | None = None
    planner_timeout_seconds = float(timeout_seconds or planner_model_timeout_seconds)
    force_deferred_fallback = bool(
        defer_on_timeout
        and chat_runtime._planner_request_requires_runtime_episode_fallback(chat_run)
    )
    try:
        base_planner_model = chat_runtime._create_planner_chat_model(
            streaming=False,
            temperature=0,
            max_tokens=planner_model_max_tokens,
            _request_kind="planner",
        )
        if planner_output_mode in {"native", "structured", "structured_output"}:
            planner_model = base_planner_model.with_structured_output(PlannerPlanPayload)
            raw_plan = await ainvoke_model_off_event_loop(
                planner_model,
                [
                    SystemMessage(content=chat_runtime._planner_system_prompt()),
                    HumanMessage(content=planner_user_message),
                ],
                timeout_seconds=planner_timeout_seconds,
            )
        else:
            raw_response = await ainvoke_model_off_event_loop(
                base_planner_model,
                [
                    SystemMessage(content=chat_runtime._planner_json_contract_prompt()),
                    HumanMessage(content=planner_user_message),
                ],
                timeout_seconds=planner_timeout_seconds,
            )
            if isinstance(raw_response, (BaseModel, dict, list)):
                raw_plan = raw_response
            else:
                raw_plan = extract_planner_payload_from_text(raw_response)
                if raw_plan is None:
                    raise ValueError("planner_json_no_parseable_payload")
        plan = chat_runtime._normalize_planner_plan_payload(raw_plan, fallback_plan=fallback_plan)
    except asyncio.TimeoutError:
        planning_error = f"planner_model_timeout_after_{planner_timeout_seconds:g}s"
        if defer_on_timeout:
            chat_run.prepared.planner_deferred = True
            chat_run.emit_runtime_event(
                "planner.deferred",
                {
                    "summary": "Planner lane deferred so Supervisor can start the first real turn.",
                    "reason": planning_error,
                    "timeoutSeconds": planner_timeout_seconds,
                    "fallbackContinues": force_deferred_fallback,
                    "messageSurfacePriority": "diagnostic",
                    "traceRef": {"runId": chat_run.active_run_id},
                },
                agent_id=None,
                node="planner_lane",
            )
            if not force_deferred_fallback:
                chat_run.prepared.planner_plan = None
                return None
        logging.getLogger("v8chat.chat_runtime").warning(
            "Planner lane fell back to deterministic plan for run '%s': %s",
            chat_run.active_run_id,
            planning_error,
        )
        fallback_plan = chat_runtime._fallback_planner_plan(chat_run=chat_run, reason=planning_error)
        plan = fallback_plan
    except Exception as exc:
        planning_error = compact_planner_error(exc)
        if defer_on_timeout:
            chat_run.prepared.planner_deferred = True
            chat_run.emit_runtime_event(
                "planner.deferred",
                {
                    "summary": "Planner lane deferred after an invalid first-turn planning attempt.",
                    "reason": planning_error,
                    "timeoutSeconds": planner_timeout_seconds,
                    "fallbackContinues": force_deferred_fallback,
                    "messageSurfacePriority": "diagnostic",
                    "traceRef": {"runId": chat_run.active_run_id},
                },
                agent_id=None,
                node="planner_lane",
            )
            if not force_deferred_fallback:
                chat_run.prepared.planner_plan = None
                return None
            logging.getLogger("v8chat.chat_runtime").warning(
                "Planner lane used deterministic deferred fallback for run '%s': %s",
                chat_run.active_run_id,
                planning_error,
            )
            fallback_plan = chat_runtime._fallback_planner_plan(chat_run=chat_run, reason=planning_error)
            plan = fallback_plan
        else:
            repaired_plan, repair_error = await try_repair_planner_plan_with_plain_json(
                chat_runtime,
                planner_user_message=planner_user_message,
                fallback_plan=fallback_plan,
                structured_error=planning_error,
            )
            if repaired_plan is not None:
                planner_repair_note = "plain_json_repair_used"
                planning_error = None
                plan = repaired_plan
                logging.getLogger("v8chat.chat_runtime").info(
                    "Planner lane repaired structured output for run '%s' via plain JSON retry.",
                    chat_run.active_run_id,
                )
            else:
                if repair_error:
                    planning_error = compact_planner_error(f"{planning_error}; {repair_error}")
                logging.getLogger("v8chat.chat_runtime").warning(
                    "Planner lane fell back to deterministic plan for run '%s': %s",
                    chat_run.active_run_id,
                    planning_error,
                )
                fallback_plan = chat_runtime._fallback_planner_plan(chat_run=chat_run, reason=planning_error)
                plan = fallback_plan

    plan = chat_runtime._validate_and_repair_planner_plan(plan, fallback_plan=fallback_plan)
    plan = chat_runtime._verify_and_repair_planner_contract(plan, fallback_plan=fallback_plan, chat_run=chat_run)
    if chat_runtime._planner_plan_violates_skill_execution_contract(chat_run, plan):
        repaired_fallback = chat_runtime._fallback_planner_plan(
            chat_run=chat_run,
            reason="planner_skill_execution_contract_violation",
        )
        flags = [
            str(item).strip()
            for item in list(repaired_fallback.get("qualityFlags") or [])
            if str(item).strip()
        ]
        flags.append("planner_skill_execution_contract_enforced")
        repaired_fallback["qualityFlags"] = list(dict.fromkeys(flags))
        repaired_fallback["repairCount"] = int(repaired_fallback.get("repairCount") or 0) + 1
        plan = chat_runtime._validate_and_repair_planner_plan(repaired_fallback, fallback_plan=repaired_fallback)
        plan = chat_runtime._verify_and_repair_planner_contract(plan, fallback_plan=repaired_fallback, chat_run=chat_run)
    plan = engineering_lane_service.enrich_planner_plan_with_engineering_contract(
        plan,
        engineering_context=chat_run.prepared.engineering_context_pack,
    )
    auto_dispatch_decision = chat_runtime._decide_planner_auto_dispatch(
        plan,
        registry=registry,
        planner_mode=chat_run.prepared.planner_mode,
        planner_dispatch_mode=chat_run.prepared.planner_dispatch_mode,
    )
    plan["autoDispatchDecision"] = auto_dispatch_decision
    plan["dispatchEligibilityReason"] = str(auto_dispatch_decision.get("reason") or "").strip()
    chat_run.prepared.planner_plan = plan
    workflow_ledger_service.activate_runtime_step(
        chat_run.active_run_id,
        owner_runtime="planner_lane",
        step_key="planner.pass",
        title="Planner lane",
        input_payload={
            "plannerMode": chat_run.prepared.planner_mode,
            "taskPlanningMode": chat_run.prepared.task_planning_mode,
            "specMode": bool(getattr(chat_run.prepared, "spec_mode", False)),
            "intentDiagnostics": dict(chat_run.prepared.planner_intent_diagnostics or {}),
            "userRequest": str(chat_run.prepared.latest_user_content or "").strip(),
            "plannerPlan": plan,
        },
        projection_payload={
            "plannerPlan": plan,
            "plannerDiagnostics": {
                "mode": chat_run.prepared.planner_mode,
                "dispatchMode": chat_run.prepared.planner_dispatch_mode,
                "intentDiagnostics": dict(chat_run.prepared.planner_intent_diagnostics or {}),
                "usedFallback": bool(planning_error),
                "error": planning_error,
                "repair": planner_repair_note,
                "qualityFlags": list(plan.get("qualityFlags") or []),
                "repairCount": int(plan.get("repairCount") or 0),
                "autoDispatchDecision": auto_dispatch_decision,
            },
        },
        status="completed",
    )
    expanded_task_briefs = expand_delegation_task_briefs(plan.get("taskBriefs") or [])
    payload = build_planner_plan_created_payload(
        plan=plan,
        expanded_task_briefs=expanded_task_briefs,
        auto_dispatch_decision=auto_dispatch_decision,
        chat_run=chat_run,
        planning_error=planning_error,
        planner_repair_note=planner_repair_note,
    )
    emit_planner_outcome_events(
        chat_run=chat_run,
        plan=plan,
        payload=payload,
        planning_error=planning_error,
        planner_repair_note=planner_repair_note,
        planner_timeout_seconds=planner_timeout_seconds,
    )
    return plan
