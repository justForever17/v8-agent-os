from __future__ import annotations

from copy import deepcopy
from typing import Any


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _append_unique(values: list[Any], item: Any) -> None:
    if item not in values:
        values.append(item)


def _skill_name_from_context(
    *,
    skill_references: list[dict[str, Any]],
    task_shape_hint: dict[str, Any],
) -> str:
    writing_route = task_shape_hint.get("writingRoute") if isinstance(task_shape_hint.get("writingRoute"), dict) else {}
    skill_name = str(writing_route.get("skillName") or writing_route.get("skill") or "").strip()
    if skill_name:
        return skill_name
    for item in skill_references:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("skillName") or "").strip()
            if name:
                return name
    return ""


def _requires_skill_execution(
    *,
    skill_references: list[dict[str, Any]],
    task_shape_hint: dict[str, Any],
) -> bool:
    if not skill_references:
        return False
    writing_route = task_shape_hint.get("writingRoute") if isinstance(task_shape_hint.get("writingRoute"), dict) else {}
    mode = str(writing_route.get("mode") or "").strip()
    return mode == "skill_subagent" or bool(writing_route.get("requiresSkillExecution"))


def _requires_skill_artifact(task: dict[str, Any], *, task_shape_hint: dict[str, Any], skill_name: str) -> bool:
    writing_route = task_shape_hint.get("writingRoute") if isinstance(task_shape_hint.get("writingRoute"), dict) else {}
    required_contracts = {str(item).strip() for item in _as_list(task.get("requiredSkillContracts")) if str(item).strip()}
    return (
        bool(task.get("validateSkillArtifact"))
        or bool(writing_route.get("requiresArtifact"))
        or "skill-creator" in required_contracts
    )


def _ensure_skill_read_contract(
    task: dict[str, Any],
    *,
    skill_name: str,
    requires_artifact: bool,
    quality_flags: list[str],
) -> int:
    repair_count = 0
    capabilities = [str(item).strip() for item in _as_list(task.get("requiredCapabilities")) if str(item).strip()]
    if "fetch_skill_instructions" not in capabilities:
        capabilities.insert(0, "fetch_skill_instructions")
        quality_flags.append("planner_contract_skill_fetch_required")
        repair_count += 1
    if "writing" not in capabilities:
        capabilities.append("writing")
        repair_count += 1
    task["requiredCapabilities"] = list(dict.fromkeys(capabilities))

    context = task.get("context") if isinstance(task.get("context"), dict) else {}
    writing_brief = context.get("writingExecutionBrief") if isinstance(context.get("writingExecutionBrief"), dict) else {}
    skill = writing_brief.get("skill") if isinstance(writing_brief.get("skill"), dict) else {}
    if skill_name and not str(skill.get("idOrName") or "").strip():
        skill["idOrName"] = skill_name
        repair_count += 1
    if skill_name and not str(skill.get("name") or "").strip():
        skill["name"] = skill_name
    skill["firstActionRequired"] = "fetch_skill_instructions"
    writing_brief["skill"] = skill
    if str(writing_brief.get("subagentFirstAction") or "").strip() != "fetch_skill_instructions":
        writing_brief["subagentFirstAction"] = "fetch_skill_instructions"
        quality_flags.append("planner_contract_skill_first_action_required")
        repair_count += 1

    required_reads = [
        dict(item)
        for item in _as_list(writing_brief.get("requiredInstructionReads"))
        if isinstance(item, dict)
    ]

    def _has_read(name: str, *, relative_path: str = "", detail_level: str = "") -> bool:
        for item in required_reads:
            item_name = str(item.get("skillName") or item.get("name") or "").strip()
            if item_name != name:
                continue
            if relative_path and str(item.get("relativePath") or item.get("relative_path") or "").strip().replace("\\", "/") != relative_path:
                continue
            if detail_level and str(item.get("detailLevel") or item.get("detail_level") or "").strip() != detail_level:
                continue
            return True
        return False

    if skill_name and not _has_read(skill_name, detail_level="full"):
        required_reads.insert(0, {"skillName": skill_name, "detailLevel": "full", "reason": "primary skill workflow"})
        quality_flags.append("planner_contract_primary_skill_full_read_required")
        repair_count += 1
    if requires_artifact and skill_name == "huashu-nuwa":
        for relative_path, reason, flag in (
            (
                "references/skill-template.md",
                "huashu-nuwa generated skill template",
                "planner_contract_huashu_template_read_required",
            ),
            (
                "references/extraction-framework.md",
                "huashu-nuwa extraction and synthesis framework",
                "planner_contract_huashu_framework_read_required",
            ),
        ):
            if not _has_read(skill_name, relative_path=relative_path):
                required_reads.append(
                    {
                        "skillName": skill_name,
                        "relativePath": relative_path,
                        "reason": reason,
                    }
                )
                quality_flags.append(flag)
                repair_count += 1
    if requires_artifact and not _has_read("skill-creator"):
        required_reads.append(
            {
                "skillName": "skill-creator",
                "detailLevel": "full",
                "reason": "skill artifact schema and quality contract",
            }
        )
        quality_flags.append("planner_contract_skill_creator_full_read_required")
        repair_count += 1
    writing_brief["requiredInstructionReads"] = required_reads

    if requires_artifact:
        contracts = [str(item).strip() for item in _as_list(task.get("requiredSkillContracts")) if str(item).strip()]
        if skill_name:
            _append_unique(contracts, skill_name)
        _append_unique(contracts, "skill-creator")
        task["requiredSkillContracts"] = contracts
        task["validateSkillArtifact"] = True
        artifact_contract = writing_brief.get("skillArtifactContract") if isinstance(writing_brief.get("skillArtifactContract"), dict) else {}
        if artifact_contract.get("requiredValidator") != "SkillArtifactValidator":
            artifact_contract["requiredValidator"] = "SkillArtifactValidator"
            repair_count += 1
        artifact_contract.setdefault(
            "mustPass",
            [
                "yaml_frontmatter",
                "name_description",
                "trigger_guidance",
                "honesty_boundary",
                "research_references",
                "source_markers",
                "loadable_skill",
            ],
        )
        writing_brief["skillArtifactContract"] = artifact_contract

    context["writingExecutionBrief"] = writing_brief
    task["context"] = context
    return repair_count


def _ensure_boundary_contract(
    repaired: dict[str, Any],
    *,
    task_shape_hint: dict[str, Any],
    quality_flags: list[str],
) -> int:
    boundary = task_shape_hint.get("boundaryDecision") if isinstance(task_shape_hint.get("boundaryDecision"), dict) else {}
    if not boundary:
        return 0
    primary_runtime = str(boundary.get("primaryRuntime") or "").strip()
    execution_mode = str(boundary.get("executionMode") or "").strip()
    forbidden = {str(item).strip() for item in _as_list(boundary.get("forbiddenRoutes")) if str(item).strip()}
    repair_count = 0
    tasks = [dict(item) for item in _as_list(repaired.get("taskBriefs")) if isinstance(item, dict)]
    capability_plan = [dict(item) for item in _as_list(repaired.get("capabilityPlan")) if isinstance(item, dict)]

    def _has_capability(kind: str) -> bool:
        return any(str(item.get("kind") or item.get("runtimeKind") or "").strip() == kind for item in capability_plan)

    def _append_capability(kind: str, reason: str) -> None:
        nonlocal repair_count
        if _has_capability(kind):
            return
        task_id = str((tasks[0] if tasks else {}).get("taskBriefId") or "task-1")
        capability_plan.append(
            {
                "kind": kind,
                "source": "task_boundary_resolver",
                "reason": reason,
                "taskBriefId": task_id,
                "state": "detected",
            }
        )
        repair_count += 1

    if primary_runtime == "engineering":
        _append_capability("engineering", str(boundary.get("reason") or "task_boundary_requires_engineering"))
        for task in tasks:
            family = str(task.get("familyHint") or "").strip()
            lane = str(task.get("executionLaneHint") or "").strip()
            if family in {"creative_media", "computer_use"} or lane in {"creative_media", "computer_use"}:
                task["familyHint"] = "engineering"
                task["executionLaneHint"] = "auto"
                if "computer_use_for_literal_terminal_only" in forbidden and (
                    family == "computer_use" or lane == "computer_use"
                ):
                    quality_flags.append("planner_boundary_terminal_native_command_repaired")
                else:
                    quality_flags.append("planner_boundary_primary_engineering_repaired")
                repair_count += 1
            if execution_mode == "code_video_runtime":
                support = [str(item).strip() for item in _as_list(task.get("supportingRuntimes")) if str(item).strip()]
                if "creative_media" not in support:
                    support.append("creative_media")
                    task["supportingRuntimes"] = support
                    quality_flags.append("planner_boundary_code_video_supports_creative_media")
                    repair_count += 1
    elif primary_runtime == "creative_media":
        _append_capability("creative_media", str(boundary.get("reason") or "task_boundary_requires_creative_media"))
    elif primary_runtime == "research":
        _append_capability("research", str(boundary.get("reason") or "task_boundary_requires_research"))
    elif primary_runtime == "rpa":
        _append_capability("rpa", str(boundary.get("reason") or "task_boundary_requires_rpa"))
    elif primary_runtime == "computer_use":
        _append_capability("computer_use", str(boundary.get("reason") or "task_boundary_requires_computer_use"))

    if "computer_use_for_literal_terminal_only" in forbidden:
        for task in tasks:
            if str(task.get("familyHint") or "").strip() == "computer_use":
                task["familyHint"] = "engineering"
                task["executionLaneHint"] = "auto"
                quality_flags.append("planner_boundary_terminal_native_command_repaired")
                repair_count += 1
        capability_plan = [
            item
            for item in capability_plan
            if str(item.get("kind") or item.get("runtimeKind") or "").strip() != "computer_use"
        ]
    if "computer_use_explicitly_excluded" in forbidden:
        for task in tasks:
            if str(task.get("familyHint") or "").strip() == "computer_use":
                task["familyHint"] = "auto"
                task["executionLaneHint"] = "auto"
                repair_count += 1
        next_plan = [
            item
            for item in capability_plan
            if str(item.get("kind") or item.get("runtimeKind") or "").strip() != "computer_use"
        ]
        if len(next_plan) != len(capability_plan):
            quality_flags.append("planner_boundary_computer_use_exclusion_repaired")
            repair_count += 1
        capability_plan = next_plan
    if "rpa_explicitly_excluded" in forbidden:
        for task in tasks:
            if str(task.get("familyHint") or "").strip() == "rpa":
                task["familyHint"] = "auto"
                task["executionLaneHint"] = "auto"
                repair_count += 1
        next_plan = [
            item
            for item in capability_plan
            if str(item.get("kind") or item.get("runtimeKind") or "").strip() != "rpa"
        ]
        if len(next_plan) != len(capability_plan):
            quality_flags.append("planner_boundary_rpa_exclusion_repaired")
            repair_count += 1
        capability_plan = next_plan

    repaired["taskBriefs"] = tasks
    repaired["capabilityPlan"] = capability_plan
    if repair_count:
        repaired["taskBoundaryDecision"] = boundary
    return repair_count


def _ensure_delegation_task_contract(
    repaired: dict[str, Any],
    *,
    quality_flags: list[str],
) -> int:
    """Make a delegation capability executable without inventing a full plan."""

    capability_plan = [dict(item) for item in _as_list(repaired.get("capabilityPlan")) if isinstance(item, dict)]
    delegation_caps = [
        item
        for item in capability_plan
        if str(item.get("kind") or item.get("runtimeKind") or "").strip() == "delegation"
    ]
    if not delegation_caps:
        return 0

    tasks = [dict(item) for item in _as_list(repaired.get("taskBriefs")) if isinstance(item, dict)]
    repair_count = 0

    def _task_has_subagent_shape(task: dict[str, Any]) -> bool:
        if str(task.get("executionLaneHint") or "").strip() in {"subagent", "delegation"}:
            return True
        if task.get("workerBriefs") or task.get("workers") or task.get("tasks"):
            return True
        required = {str(item).strip() for item in _as_list(task.get("requiredCapabilities")) if str(item).strip()}
        return bool(required & {"subagent_execution", "delegation", "peer_review", "independent_review"})

    def _make_subagent_task(capability: dict[str, Any], index: int) -> dict[str, Any]:
        reason = str(capability.get("reason") or capability.get("brief") or capability.get("summary") or "").strip()
        title = str(capability.get("title") or "Subagent review").strip()
        task_id = str(capability.get("taskBriefId") or capability.get("taskId") or f"task-delegation-{index + 1}").strip()
        goal = reason or "Run one focused subagent review and return structured findings, risks, and recommended next steps."
        return {
            "taskBriefId": task_id,
            "title": title[:96],
            "goal": goal,
            "executionLaneHint": "subagent",
            "familyHint": str(capability.get("familyHint") or capability.get("family") or "").strip() or "auto",
            "requiredCapabilities": ["subagent_execution", "independent_review"],
            "acceptanceContract": {
                "must": [
                    "Return a structured result bundle with status, findings, residual risks, and recovery hints.",
                    "Do not claim success when no real worker task was confirmed.",
                ],
                "should": ["Reuse parent evidence refs and gate/risk context when present."],
                "nice": ["Suggest a narrower retry if the task cannot be completed."],
            },
        }

    if not tasks:
        tasks = [_make_subagent_task(delegation_caps[0], 0)]
        quality_flags.append("planner_contract_delegation_task_created")
        repair_count += 1
    elif not any(_task_has_subagent_shape(task) for task in tasks):
        first_cap = delegation_caps[0]
        linked_id = str(first_cap.get("taskBriefId") or first_cap.get("taskId") or "").strip()
        target_index = 0
        if linked_id:
            for index, task in enumerate(tasks):
                if str(task.get("taskBriefId") or task.get("taskId") or "").strip() == linked_id:
                    target_index = index
                    break
        task = dict(tasks[target_index])
        task["executionLaneHint"] = "subagent"
        task.setdefault("familyHint", str(first_cap.get("familyHint") or first_cap.get("family") or "").strip() or "auto")
        capabilities = [str(item).strip() for item in _as_list(task.get("requiredCapabilities")) if str(item).strip()]
        for capability in ("subagent_execution", "independent_review"):
            if capability not in capabilities:
                capabilities.append(capability)
        task["requiredCapabilities"] = capabilities
        if not task.get("acceptanceContract"):
            task["acceptanceContract"] = {
                "must": ["Return status, findings, residual risks, and recovery hints."],
                "should": ["Use parent evidence refs when available."],
                "nice": ["Suggest a narrower retry if blocked."],
            }
        tasks[target_index] = task
        quality_flags.append("planner_contract_delegation_task_shaped")
        repair_count += 1

    repaired["taskBriefs"] = tasks
    return repair_count


def _ensure_spec_contract(
    repaired: dict[str, Any],
    *,
    task_shape_hint: dict[str, Any],
    quality_flags: list[str],
) -> int:
    if not bool(task_shape_hint.get("specMode")):
        return 0
    repair_count = 0
    spec_id = str(task_shape_hint.get("specId") or "").strip()
    spec_brief = task_shape_hint.get("specBrief") if isinstance(task_shape_hint.get("specBrief"), dict) else {}
    pipeline = spec_brief.get("pipelineControl") if isinstance(spec_brief.get("pipelineControl"), dict) else {}
    if not spec_id or not spec_brief or spec_brief.get("status") in {"missing", "error"}:
        quality_flags.append("spec_brief_missing")
        return 1
    if not bool(pipeline.get("runtimeExecutionAllowed")):
        quality_flags.append("spec_runtime_not_approved")
        return 1

    spec_context = {
        "specId": spec_id,
        "featureName": spec_brief.get("featureName"),
        "approvedStages": list(spec_brief.get("approvedStages") or []),
        "linkedSections": list(spec_brief.get("linkedSections") or [])[:8],
        "documents": {
            stage: {
                "detailRef": value.get("detailRef"),
                "ids": list(value.get("ids") or [])[:12],
                "version": value.get("version"),
                "status": value.get("status"),
            }
            for stage, value in dict(spec_brief.get("documents") or {}).items()
            if isinstance(value, dict)
        },
    }
    tasks = [dict(item) for item in _as_list(repaired.get("taskBriefs")) if isinstance(item, dict)]
    changed = False
    for task in tasks:
        context = dict(task.get("context") or {}) if isinstance(task.get("context"), dict) else {}
        if context.get("specBrief") != spec_context:
            context["specBrief"] = spec_context
            task["context"] = context
            changed = True
    if changed:
        repaired["taskBriefs"] = tasks
        quality_flags.append("spec_brief_context_attached")
        repair_count += 1
    return repair_count


def verify_and_repair_planner_contract(
    plan: dict[str, Any],
    *,
    fallback_plan: dict[str, Any] | None = None,
    skill_references: list[dict[str, Any]] | None = None,
    task_shape_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Repair high-level planner contracts without replacing model planning.

    This verifier intentionally stays narrow: it does not author a new plan, but
    enforces contracts that must be present before runtime routing and subagent
    execution can safely proceed.
    """

    repaired = deepcopy(plan or fallback_plan or {})
    skill_refs = [dict(item) for item in _as_list(skill_references) if isinstance(item, dict)]
    hint = dict(task_shape_hint or {})
    quality_flags = [str(item).strip() for item in _as_list(repaired.get("qualityFlags")) if str(item).strip()]
    repair_count = int(repaired.get("repairCount") or 0)
    tasks = [dict(item) for item in _as_list(repaired.get("taskBriefs")) if isinstance(item, dict)]
    skill_name = _skill_name_from_context(skill_references=skill_refs, task_shape_hint=hint)
    requires_skill = _requires_skill_execution(skill_references=skill_refs, task_shape_hint=hint)

    repair_count += _ensure_boundary_contract(
        repaired,
        task_shape_hint=hint,
        quality_flags=quality_flags,
    )
    repair_count += _ensure_spec_contract(
        repaired,
        task_shape_hint=hint,
        quality_flags=quality_flags,
    )
    tasks = [dict(item) for item in _as_list(repaired.get("taskBriefs")) if isinstance(item, dict)]

    if requires_skill and tasks:
        repaired_any = False
        for task in tasks:
            context = task.get("context") if isinstance(task.get("context"), dict) else {}
            writing_brief = context.get("writingExecutionBrief") if isinstance(context.get("writingExecutionBrief"), dict) else {}
            task_skill = writing_brief.get("skill") if isinstance(writing_brief.get("skill"), dict) else {}
            task_skill_name = str(task_skill.get("idOrName") or task_skill.get("name") or skill_name).strip()
            required = {str(item).strip() for item in _as_list(task.get("requiredCapabilities")) if str(item).strip()}
            looks_like_skill_task = (
                bool(writing_brief)
                or task_skill_name
                or "writing" in required
                or str(task.get("familyHint") or "").strip() in {"writing", "engineering"}
            )
            if not looks_like_skill_task:
                continue
            repair_count += _ensure_skill_read_contract(
                task,
                skill_name=task_skill_name or skill_name,
                requires_artifact=_requires_skill_artifact(task, task_shape_hint=hint, skill_name=task_skill_name or skill_name),
                quality_flags=quality_flags,
            )
            repaired_any = True
        if not repaired_any and tasks:
            repair_count += _ensure_skill_read_contract(
                tasks[0],
                skill_name=skill_name,
                requires_artifact=_requires_skill_artifact(tasks[0], task_shape_hint=hint, skill_name=skill_name),
                quality_flags=quality_flags,
            )
        repaired["taskBriefs"] = tasks

    repair_count += _ensure_delegation_task_contract(repaired, quality_flags=quality_flags)
    tasks = [dict(item) for item in _as_list(repaired.get("taskBriefs")) if isinstance(item, dict)]
    delegation_caps = [
        item
        for item in _as_list(repaired.get("capabilityPlan"))
        if isinstance(item, dict) and str(item.get("kind") or item.get("runtimeKind") or "").strip() == "delegation"
    ]
    if delegation_caps and not tasks:
        quality_flags.append("planner_contract_delegation_without_tasks")
        repair_count += 1
    for task in tasks:
        if str(task.get("executionLaneHint") or "").strip() == "subagent":
            has_task_shape = bool(str(task.get("goal") or "").strip() or task.get("workerBriefs") or task.get("requiredCapabilities"))
            if not has_task_shape:
                quality_flags.append("planner_contract_subagent_missing_task_shape")
                repair_count += 1

    repaired["taskBriefs"] = tasks
    repaired["qualityFlags"] = list(dict.fromkeys(quality_flags))
    repaired["repairCount"] = repair_count
    repaired["plannerContractVerifier"] = {
        "schema": "v8.planner_contract_verifier.v1",
        "repairCount": repair_count,
        "qualityFlags": repaired["qualityFlags"],
        "skillContractChecked": bool(requires_skill),
        "specContractChecked": bool(hint.get("specMode")),
    }
    return repaired
