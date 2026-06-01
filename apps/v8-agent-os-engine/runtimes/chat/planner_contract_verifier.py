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
        or str(skill_name).strip() in {"huashu-nuwa", "skill-creator"}
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
    }
    return repaired
