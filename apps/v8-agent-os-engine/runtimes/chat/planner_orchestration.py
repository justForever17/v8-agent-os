from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import uuid
from typing import Any, Literal

from core.delegation_broker import normalize_task_brief, summarize_capability_snapshot
from core.llm_factory import llm_factory
from core.runtime_episodes import normalize_capability_kind
from pydantic import BaseModel, Field, model_validator


PLANNER_MODEL_TIMEOUT_SECONDS = float(os.getenv("V8_PLANNER_MODEL_TIMEOUT_SECONDS", "240"))
PLANNER_FIRST_TURN_BUDGET_SECONDS = float(os.getenv("V8_PLANNER_FIRST_TURN_BUDGET_SECONDS", "0.35"))
PLANNER_REPAIR_TIMEOUT_SECONDS = float(os.getenv("V8_PLANNER_REPAIR_TIMEOUT_SECONDS", "18"))
PLANNER_MODEL_MAX_TOKENS = int(os.getenv("V8_PLANNER_MODEL_MAX_TOKENS", "6000"))
PLANNER_REPAIR_MAX_TOKENS = int(os.getenv("V8_PLANNER_REPAIR_MAX_TOKENS", "4000"))
PLANNER_MAX_REGISTRY_AGENTS = int(os.getenv("V8_PLANNER_MAX_REGISTRY_AGENTS", "8"))
PLANNER_MAX_SKILL_REFERENCES = int(os.getenv("V8_PLANNER_MAX_SKILL_REFERENCES", "6"))
PLANNER_MAX_DESCRIPTION_CHARS = int(os.getenv("V8_PLANNER_MAX_DESCRIPTION_CHARS", "160"))
PLANNER_OUTPUT_MODE = os.getenv("V8_PLANNER_OUTPUT_MODE", "json").strip().lower()


async def ainvoke_model_off_event_loop(model: Any, messages: list[Any], *, timeout_seconds: float) -> Any:
    """Invoke a planner model with a bounded wait in the current event loop.

    LangChain/OpenAI clients keep httpx/asyncio locks in their instances, so
    crossing event loops can make later model calls fail with lock ownership
    errors. The chat submit path already runs in the background run, so
    same-loop invocation is the least surprising and most recoverable behavior.
    """

    result = model.ainvoke(messages)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=max(0.1, timeout_seconds))
    return result


class PlannerTaskBriefPayload(BaseModel):
    taskBriefId: str = ""
    goal: str = ""
    context: str | dict[str, Any] = ""
    writeSet: list[str] = Field(default_factory=list)
    criticalFiles: list[str] = Field(default_factory=list)
    readSet: list[str] = Field(default_factory=list)
    verificationMatrix: list[str] = Field(default_factory=list)
    proofExpectations: list[str] = Field(default_factory=list)
    engineeringTaskCapsule: dict[str, Any] = Field(default_factory=dict)
    behaviorScope: list[str] = Field(default_factory=list)
    requiredCapabilities: list[str] = Field(default_factory=list)
    acceptanceContract: str = ""
    dependency: list[str] = Field(default_factory=list)
    parallelGroup: str = ""
    executionLaneHint: Literal["subagent", "external_worker", "auto"] = "auto"
    familyHint: str = ""
    preferredAgentId: str = ""
    preferredWorkerType: str = ""
    targetCount: int = 1
    workerBriefs: list[dict[str, Any]] = Field(default_factory=list)
    fanoutReason: str = ""
    allowChildDelegation: bool = False
    childDelegationBudget: dict[str, Any] = Field(default_factory=dict)
    writeSetPartitions: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_common_model_shapes(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get("engineeringTaskCapsule") is None:
            data["engineeringTaskCapsule"] = {}
        if data.get("childDelegationBudget") is None:
            data["childDelegationBudget"] = {}
        if data.get("writeSetPartitions") is None:
            data["writeSetPartitions"] = []
        for key in ("taskBriefId", "goal", "acceptanceContract", "parallelGroup", "familyHint", "preferredAgentId", "preferredWorkerType", "fanoutReason"):
            if data.get(key) is None:
                data[key] = ""
        if data.get("executionLaneHint") is None:
            data["executionLaneHint"] = "auto"
        if data.get("targetCount") is None:
            data["targetCount"] = 1
        if "allow_child_delegation" in data and "allowChildDelegation" not in data:
            data["allowChildDelegation"] = data.get("allow_child_delegation")
        if "child_delegation_budget" in data and "childDelegationBudget" not in data:
            data["childDelegationBudget"] = data.get("child_delegation_budget")
        if "write_set_partitions" in data and "writeSetPartitions" not in data:
            data["writeSetPartitions"] = data.get("write_set_partitions")
        acceptance = data.get("acceptanceContract")
        if isinstance(acceptance, (dict, list)):
            data["acceptanceContract"] = json.dumps(acceptance, ensure_ascii=False, separators=(",", ":"))
        for key in (
            "writeSet",
            "criticalFiles",
            "readSet",
            "verificationMatrix",
            "proofExpectations",
            "behaviorScope",
            "requiredCapabilities",
            "dependency",
        ):
            field_value = data.get(key)
            if isinstance(field_value, str):
                stripped = field_value.strip()
                data[key] = [stripped] if stripped else []
        return data


class PlannerTaskNodePayload(BaseModel):
    taskBriefId: str = ""
    title: str = ""
    dependency: list[str] = Field(default_factory=list)
    parallelGroup: str = ""


class PlannerPlanPayload(BaseModel):
    planId: str = ""
    executionStrategy: Literal["direct", "delegate", "mixed"] = "direct"
    planSummary: str = ""
    capabilityPlan: list[dict[str, Any]] = Field(default_factory=list)
    taskGraph: list[PlannerTaskNodePayload] = Field(default_factory=list)
    taskBriefs: list[PlannerTaskBriefPayload] = Field(default_factory=list)
    handoffPlan: list[dict[str, Any]] = Field(default_factory=list)
    globalAcceptanceContract: str = ""
    riskFlags: list[str] = Field(default_factory=list)
    codingPlannerContract: dict[str, Any] = Field(default_factory=dict)
    qualityFlags: list[str] = Field(default_factory=list)
    repairCount: int = 0
    autoDispatchDecision: dict[str, Any] = Field(default_factory=dict)
    dispatchEligibilityReason: str = ""

    @model_validator(mode="before")
    @classmethod
    def _wrap_common_model_shapes(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {
                "executionStrategy": "mixed" if len(value) > 1 else "delegate",
                "capabilityPlan": [item for item in value if isinstance(item, dict)],
                "qualityFlags": ["planner_list_payload_wrapped"],
            }
        if isinstance(value, dict):
            data = dict(value)
            for key in ("globalAcceptanceContract", "planSummary", "dispatchEligibilityReason"):
                field_value = data.get(key)
                if isinstance(field_value, (dict, list)):
                    data[key] = json.dumps(field_value, ensure_ascii=False, separators=(",", ":"))
                elif field_value is None:
                    data[key] = ""
            for key in ("riskFlags", "qualityFlags"):
                field_value = data.get(key)
                if isinstance(field_value, str):
                    stripped = field_value.strip()
                    data[key] = [stripped] if stripped else []
                elif field_value is None:
                    data[key] = []
            if data.get("codingPlannerContract") is None:
                data["codingPlannerContract"] = {}
            if data.get("autoDispatchDecision") is None:
                data["autoDispatchDecision"] = {}
            elif not isinstance(data.get("autoDispatchDecision"), dict):
                data["autoDispatchDecision"] = {"raw": data.get("autoDispatchDecision")}
            return data
        return value


def planner_clip_text(value: Any, *, limit: int = PLANNER_MAX_DESCRIPTION_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def planner_compact_specialist_entry(item: dict[str, Any], *, external: bool = False) -> dict[str, Any]:
    snapshot = item.get("capabilitySnapshot") if isinstance(item.get("capabilitySnapshot"), dict) else {}
    capability = summarize_capability_snapshot(snapshot)
    entry: dict[str, Any] = {
        "id": str(item.get("id") or "").strip(),
        "name": planner_clip_text(item.get("name") or item.get("id") or ("external-worker" if external else "subagent"), limit=80),
        "description": planner_clip_text(item.get("description") or "", limit=PLANNER_MAX_DESCRIPTION_CHARS),
    }
    if capability:
        entry["capabilitySummary"] = planner_clip_text(capability, limit=PLANNER_MAX_DESCRIPTION_CHARS)
    family = item.get("familyId") or item.get("family") or item.get("family_id")
    if family:
        entry["familyHint"] = planner_clip_text(family, limit=80)
    return {key: value for key, value in entry.items() if value}


def planner_registry_lines(registry: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    subagents = list(registry.get("subagents") or [])
    external_workers = list(registry.get("externalWorkers") or [])
    if subagents:
        lines.append("[Local Subagents]")
        for agent in subagents[: min(PLANNER_MAX_REGISTRY_AGENTS, 12)]:
            snapshot = agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {}
            capability = summarize_capability_snapshot(snapshot)
            lines.append(
                f"- {planner_clip_text(agent.get('name') or agent.get('id') or 'unknown-agent', limit=80)} "
                f"({str(agent.get('id') or '').strip()}): "
                f"{planner_clip_text(agent.get('description') or 'No description')}"
                f"{' | ' + planner_clip_text(capability) if capability else ''}"
            )
        if len(subagents) > min(PLANNER_MAX_REGISTRY_AGENTS, 12):
            lines.append(f"- … {len(subagents) - min(PLANNER_MAX_REGISTRY_AGENTS, 12)} more local subagents omitted from planner prompt.")
    if external_workers:
        lines.append("[External Workers]")
        for worker in external_workers[: min(PLANNER_MAX_REGISTRY_AGENTS, 12)]:
            snapshot = worker.get("capabilitySnapshot") if isinstance(worker.get("capabilitySnapshot"), dict) else {}
            capability = summarize_capability_snapshot(snapshot)
            lines.append(
                f"- {planner_clip_text(worker.get('name') or worker.get('id') or 'external-worker', limit=80)} "
                f"({str(worker.get('id') or '').strip()}): "
                f"{planner_clip_text(worker.get('description') or 'No description')}"
                f"{' | ' + planner_clip_text(capability) if capability else ''}"
            )
        if len(external_workers) > min(PLANNER_MAX_REGISTRY_AGENTS, 12):
            lines.append(f"- … {len(external_workers) - min(PLANNER_MAX_REGISTRY_AGENTS, 12)} more external workers omitted from planner prompt.")
    if not lines:
        lines.append("- No local subagents or external workers are currently registered.")
    return lines


def planner_system_prompt() -> str:
    return (
        "You are the V8 Agent OS planner lane.\n"
        "You are a non-executing orchestration planner. Produce only a structured planning contract.\n"
        "Your job is to decide whether the request should be handled directly by the supervisor, delegated, or split into a mixed strategy.\n"
        "Core discipline:\n"
        "- Slice before execute.\n"
        "- Keep the minimum task count that preserves write-set isolation, behavior isolation, and acceptance clarity.\n"
        "- Prefer direct execution only for small, bounded tasks that fit within 1-10 tool steps and a tiny writeSet.\n"
        "- Use delegation or mixed strategy when specialized capability, independent context, parallel work, broad multi-file implementation, research+implementation, or external worker execution materially helps.\n"
        "- Every task brief must be broker-ready and concrete.\n"
        "- Define acceptance contracts before execution starts.\n"
        "- Do not pretend work has already been done.\n"
        "- Do not execute tools, browse, or simulate outputs.\n"
        "- Broad product tasks may require multiple runtime lanes; model them explicitly instead of flattening them into one direct task.\n"
        "Output rules:\n"
        "- executionStrategy must be one of: direct, delegate, mixed.\n"
        "- capabilityPlan should list runtime needs such as research, engineering, creative_media, computer_use, rpa, or delegation.\n"
        "- handoffPlan should describe refs passed between runtime episodes, e.g. research evidence refs into engineering implementation.\n"
        "- taskBriefs must align with executionStrategy.\n"
        "- direct may still include one compact task brief for governance and verification.\n"
        "- Keep output compact: at most 6 capabilityPlan rows, 6 taskBriefs, 6 handoffPlan rows, and 8 short strings in any list unless the user explicitly requested more.\n"
        "- Never include long prose, copied registry text, source dumps, or implementation details in planner fields; use concise broker-ready phrases.\n"
        "- preferredAgentId and preferredWorkerType are optional hints, not guesses.\n"
        "- familyHint may name a specialist family such as engineering or creative_media; it guides delegation_broker selection but does not reveal members or grant runtime tools.\n"
        "- executionLaneHint must be one of: subagent, external_worker, auto.\n"
        "- If one logical task needs multiple parallel workers, keep it as one macro task and set targetCount to the exact requested fanout; optionally provide workerBriefs with one atomic brief per worker.\n"
        "- targetCount is explicit upper-agent intent, not an automatic default. Do not set it above 1 unless parallel workers materially help and their work can be isolated.\n"
        "- Keep riskFlags short and concrete.\n"
        "Research plus implementation discipline:\n"
        "- If the request combines research/search with building, code, frontend, or app creation, use executionStrategy=mixed.\n"
        "- Put the research evidence task before implementation; the research task should request source quality, conflicts, citations, and compact evidence refs.\n"
        "- Put the implementation task in the engineering family when it creates or changes project files, with an explicit tentative writeSet and proof expectations.\n"
        "- Project scaffolding, dependency installs, and dev servers should be session-observed commands, not blocking sync commands.\n"
        "- If a plan would need scaffolding + dependencies + implementation + verification, it should not be one direct supervisor task.\n"
        "Writing and skill execution discipline:\n"
        "- Supervisor todos only track orchestration milestones: clarify goal, route runtimes/subagents, wait for typed handoff, merge, verify, deliver.\n"
        "- Runtime-internal plans belong to their runtime ledgers/cards/artifacts, not supervisor todos.\n"
        "- Simple bounded prose such as a short reply, one-paragraph explanation, or quick summary may remain direct with the supervisor.\n"
        "- Ambiguous deliverables such as 'write a document/proposal/report' must ask the user whether they want direct body text, research-backed writing, or a saved file before routing.\n"
        "- Source-backed writing must route Research first and pass compact researchRefs/evidenceBundle into the final writing step.\n"
        "- File/repository writing such as README/docs/*.md/docx/pdf/save-to-path is Engineering or future Document Runtime work because it mutates artifacts.\n"
        "- Skill-driven writing must delegate to a writing-family subagent with a WritingExecutionBrief; the delegated subagent's first action is fetch_skill_instructions(skill_name).\n"
        "- For skill design/review/curation tasks, prefer preferredAgentId=skill-workflow-curator. Do not create durable subagents for ordinary short prose.\n"
        "Engineering lane discipline when EngineeringEvidenceGraph is provided:\n"
        "- Prefer evidenceGraphDigest over raw guessing for critical files, writeSet, and verification choices.\n"
        "- Populate codingPlannerContract with criticalFiles, readSet, writeSet, ownershipPlan, verificationMatrix, mergeOrder, riskFlags, and proofExpectations.\n"
        "- Add engineeringTaskCapsule to each task brief when the task touches code; keep it compact and do not copy full repo evidence.\n"
        "- If writeSet cannot be proven, say so in riskFlags instead of pretending certainty.\n"
    )


def planner_json_contract_prompt() -> str:
    return (
        planner_system_prompt()
        + "\n\nReturn ONLY one compact JSON object. Do not wrap it in Markdown.\n"
        "Required shape:\n"
        "{"
        "\"planId\":\"plan_short_id\","
        "\"executionStrategy\":\"direct|delegate|mixed\","
        "\"planSummary\":\"one compact sentence\","
        "\"capabilityPlan\":[{\"kind\":\"research|engineering|creative_media|computer_use|rpa|delegation\",\"reason\":\"short reason\",\"taskBriefId\":\"task-1\",\"state\":\"detected\"}],"
        "\"taskBriefs\":[{\"taskBriefId\":\"task-1\",\"goal\":\"atomic broker-ready task\",\"context\":\"compact context\",\"writeSet\":[],\"readSet\":[],\"behaviorScope\":[],\"requiredCapabilities\":[],\"acceptanceContract\":\"concrete proof or blocker\",\"dependency\":[],\"executionLaneHint\":\"auto\",\"familyHint\":\"\"}],"
        "\"handoffPlan\":[{\"fromTaskBriefId\":\"task-1\",\"toTaskBriefId\":\"task-2\",\"refs\":[\"handoffRef\"],\"reason\":\"short handoff reason\"}],"
        "\"globalAcceptanceContract\":\"one compact acceptance contract\","
        "\"riskFlags\":[],"
        "\"qualityFlags\":[],"
        "\"codingPlannerContract\":{}"
        "}\n"
        "If you need only capability rows, still wrap them inside this object and create matching taskBriefs.\n"
    )


def normalize_planner_plan_payload(raw_plan: Any, *, fallback_plan: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw_plan, BaseModel):
        payload = raw_plan.model_dump(mode="json")
    elif isinstance(raw_plan, list):
        capability_items = [dict(item) for item in raw_plan if isinstance(item, dict)]
        capability_plan: list[dict[str, Any]] = []
        task_briefs: list[dict[str, Any]] = []
        for index, item in enumerate(capability_items):
            task_id = str(item.get("taskBriefId") or item.get("id") or f"task-{index + 1}").strip()
            raw_kind = (
                item.get("kind")
                or item.get("capability")
                or item.get("runtimeKind")
                or item.get("runtime")
                or item.get("familyHint")
            )
            kind = normalize_capability_kind(raw_kind)
            reason = str(item.get("reason") or item.get("goal") or item.get("title") or raw_kind or "runtime capability required").strip()
            if kind:
                capability_plan.append(
                    {
                        "kind": kind,
                        "source": item.get("source") or "planner_model_repair",
                        "reason": reason,
                        "taskBriefId": task_id,
                        "state": "detected",
                        **({"requiredRuntimeAccess": item.get("requiredRuntimeAccess")} if item.get("requiredRuntimeAccess") else {}),
                    }
                )
            task_briefs.append(
                normalize_task_brief(
                    {
                        "taskBriefId": task_id,
                        "goal": str(item.get("goal") or item.get("title") or reason or f"Handle {kind or 'runtime'} task").strip(),
                        "context": item.get("context") if isinstance(item.get("context"), dict) else {"plannerListItem": item},
                        "writeSet": list(item.get("writeSet") or []),
                        "behaviorScope": list(item.get("behaviorScope") or ([kind] if kind else [])),
                        "requiredCapabilities": list(item.get("requiredCapabilities") or ([kind] if kind else [])),
                        "acceptanceContract": str(item.get("acceptanceContract") or "Return a typed handoff with results, proof, blockers, and residual risk.").strip(),
                        "dependency": list(item.get("dependency") or []),
                        "parallelGroup": str(item.get("parallelGroup") or "").strip(),
                        "executionLaneHint": str(item.get("executionLaneHint") or "auto").strip() or "auto",
                        "familyHint": str(item.get("familyHint") or ("engineering" if kind == "engineering" else "research" if kind == "research" else "creative_media" if kind == "creative_media" else "")).strip(),
                        "targetCount": int(item.get("targetCount") or 1),
                        "workerBriefs": list(item.get("workerBriefs") or []),
                        "allowChildDelegation": bool(item.get("allowChildDelegation") or item.get("allow_child_delegation") or False),
                        "childDelegationBudget": (
                            dict(item.get("childDelegationBudget") or item.get("child_delegation_budget") or {})
                            if isinstance(item.get("childDelegationBudget") or item.get("child_delegation_budget") or {}, dict)
                            else {}
                        ),
                        "writeSetPartitions": (
                            list(item.get("writeSetPartitions") or item.get("write_set_partitions") or [])
                            if isinstance(item.get("writeSetPartitions") or item.get("write_set_partitions") or [], list)
                            else []
                        ),
                    },
                    index=index,
                )
            )
        payload = {
            "executionStrategy": "mixed" if len(raw_plan) > 1 else "delegate",
            "capabilityPlan": capability_plan,
            "taskBriefs": task_briefs,
            "qualityFlags": ["planner_list_payload_wrapped"],
        }
    elif isinstance(raw_plan, dict):
        payload = dict(raw_plan or {})
    else:
        payload = dict(fallback_plan or {})
    normalized_briefs = [
        normalize_task_brief(item, index=index)
        for index, item in enumerate(list(payload.get("taskBriefs") or []))
    ]
    if not normalized_briefs:
        normalized_briefs = list(fallback_plan.get("taskBriefs") or [])
    normalized_graph: list[dict[str, Any]] = []
    graph_rows = list(payload.get("taskGraph") or [])
    task_lookup = {str(item.get("taskBriefId") or "").strip(): item for item in normalized_briefs}
    for index, item in enumerate(graph_rows):
        row = dict(item or {})
        task_brief_id = str(row.get("taskBriefId") or normalized_briefs[min(index, len(normalized_briefs) - 1)].get("taskBriefId") or "").strip()
        normalized_graph.append(
            {
                "taskBriefId": task_brief_id,
                "title": str(row.get("title") or task_lookup.get(task_brief_id, {}).get("goal") or task_brief_id or f"Task {index + 1}").strip(),
                "dependency": [str(dep).strip() for dep in list(row.get("dependency") or task_lookup.get(task_brief_id, {}).get("dependency") or []) if str(dep).strip()],
                "parallelGroup": str(row.get("parallelGroup") or task_lookup.get(task_brief_id, {}).get("parallelGroup") or "").strip(),
            }
        )
    if not normalized_graph:
        normalized_graph = [
            {
                "taskBriefId": str(item.get("taskBriefId") or f"task-{index + 1}").strip(),
                "title": str(item.get("goal") or item.get("taskBriefId") or f"Task {index + 1}").strip(),
                "dependency": [str(dep).strip() for dep in list(item.get("dependency") or []) if str(dep).strip()],
                "parallelGroup": str(item.get("parallelGroup") or "").strip(),
            }
            for index, item in enumerate(normalized_briefs)
        ]
    execution_strategy = str(payload.get("executionStrategy") or fallback_plan.get("executionStrategy") or "direct").strip().lower()
    if execution_strategy not in {"direct", "delegate", "mixed"}:
        execution_strategy = str(fallback_plan.get("executionStrategy") or "direct")
    plan_summary = str(payload.get("planSummary") or fallback_plan.get("planSummary") or "").strip()
    global_acceptance = str(payload.get("globalAcceptanceContract") or fallback_plan.get("globalAcceptanceContract") or "").strip()
    risk_flags = [str(item).strip() for item in list(payload.get("riskFlags") or fallback_plan.get("riskFlags") or []) if str(item).strip()]
    quality_flags = [str(item).strip() for item in list(payload.get("qualityFlags") or fallback_plan.get("qualityFlags") or []) if str(item).strip()]
    capability_plan = [
        dict(item)
        for item in list(payload.get("capabilityPlan") or fallback_plan.get("capabilityPlan") or [])
        if isinstance(item, dict)
    ]
    handoff_plan = [
        dict(item)
        for item in list(payload.get("handoffPlan") or fallback_plan.get("handoffPlan") or [])
        if isinstance(item, dict)
    ]
    return {
        "planId": str(payload.get("planId") or fallback_plan.get("planId") or f"plan_{uuid.uuid4().hex[:10]}").strip(),
        "executionStrategy": execution_strategy,
        "planSummary": plan_summary or str(fallback_plan.get("planSummary") or "").strip(),
        "capabilityPlan": capability_plan,
        "taskGraph": normalized_graph,
        "taskBriefs": normalized_briefs,
        "handoffPlan": handoff_plan,
        "globalAcceptanceContract": global_acceptance or str(fallback_plan.get("globalAcceptanceContract") or "").strip(),
        "riskFlags": risk_flags,
        "codingPlannerContract": payload.get("codingPlannerContract") if isinstance(payload.get("codingPlannerContract"), dict) else dict(fallback_plan.get("codingPlannerContract") or {}),
        "qualityFlags": quality_flags,
        "repairCount": int(payload.get("repairCount") or fallback_plan.get("repairCount") or 0),
        "autoDispatchDecision": payload.get("autoDispatchDecision") if isinstance(payload.get("autoDispatchDecision"), dict) else dict(fallback_plan.get("autoDispatchDecision") or {}),
        "dispatchEligibilityReason": str(payload.get("dispatchEligibilityReason") or fallback_plan.get("dispatchEligibilityReason") or "").strip(),
    }


def create_planner_chat_model(*, model_factory: Any | None = None, **kwargs: Any) -> Any:
    """Create the configured planner model, falling back only when unbound.

    The generic Planner is a separate role from the Supervisor. Existing
    installations may not have the role bound yet, so an unconfigured planner
    inherits the supervisor model for compatibility. If a planner model is
    explicitly configured but invalid, let the model control plane surface that
    error instead of silently hiding it.
    """

    try:
        from core.models.control_plane import model_control_plane

        roles = dict((model_control_plane.get_config() or {}).get("roles") or {})
        planner_binding = str(roles.get("planner") or "").strip()
    except Exception:
        planner_binding = ""
    role = "planner" if planner_binding else "supervisor"
    factory = model_factory or llm_factory
    return factory.create_for_role(role, **kwargs)
