from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.native_tools import runtime_broker
from core.native_tools import NATIVE_TOOLS
from core.system_tools.baseline import select_baseline_system_tools
from core.spec_service import spec_service
from erc.runtime_context import bind_runtime_context
from graph.agent_factories import _build_agent_system_content, _format_delegated_task_contract
from graph.parallel_support import _child_request_from_send_state
from runtimes.extensions.skills.loader import fetch_skill_instructions

REPO_ROOT = ENGINE_ROOT.parents[1]
OUTPUT_ROOT = REPO_ROOT / "docs" / "chatruntime" / "runtime_deep_observation_reports"
DEFAULT_REFERENCE_SPEC_DIR = ENGINE_ROOT / "tests" / "fixtures" / "spec_runtime_distribution" / "counter-app-spec"


def _read_reference_text(path: Path) -> str:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return path.read_text(encoding="utf-8")


def _tool_payload(command: Any) -> dict[str, Any]:
    messages = list((getattr(command, "update", None) or {}).get("messages") or [])
    if not messages:
        return {}
    try:
        return json.loads(str(messages[0].content or "{}"))
    except json.JSONDecodeError:
        return {"raw": str(messages[0].content or "")}


def _read_reference_spec(sample_dir: Path) -> dict[str, str] | None:
    try:
        if not sample_dir.exists() or not sample_dir.is_dir():
            return None
        payload = {
            "requirements": _read_reference_text(sample_dir / "requirements.md"),
            "design": _read_reference_text(sample_dir / "design.md"),
            "tasks": _read_reference_text(sample_dir / "tasks.md"),
        }
        if all(value.strip() for value in payload.values()):
            return payload
    except Exception:
        return None
    return None


def _sample_feature_name(sample_dir: Path | None) -> str:
    source_dir = sample_dir or DEFAULT_REFERENCE_SPEC_DIR
    return f"{source_dir.name}-reference"


def _create_approved_demo_spec(workspace: Path, *, sample_spec_dir: Path | None = None) -> tuple[str, str]:
    reference = _read_reference_spec(sample_spec_dir or DEFAULT_REFERENCE_SPEC_DIR)
    if reference:
        created = spec_service.create_stage(
            workspace_path=str(workspace),
            user_request="Validate repo-local Spec runtime distribution fixture.",
            feature_name=_sample_feature_name(sample_spec_dir),
        )
        spec_id = str(created["specId"])
        spec_service.edit_stage(
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="requirements",
            action="rewrite_stage",
            content=reference["requirements"],
            reason="dry-run reference requirements import",
        )
        spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements", comment="dry-run approved")
        spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="design")
        spec_service.edit_stage(
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="design",
            action="rewrite_stage",
            content=reference["design"],
            reason="dry-run reference design import",
        )
        spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design", comment="dry-run approved")
        spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="tasks")
        spec_service.edit_stage(
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="tasks",
            action="rewrite_stage",
            content=reference["tasks"],
            reason="dry-run reference tasks import",
        )
        spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks", comment="dry-run approved")
        return spec_id, str((sample_spec_dir or DEFAULT_REFERENCE_SPEC_DIR).resolve())

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="Build a tiny browser counter app.",
        feature_name="dry-run-counter-app",
    )
    spec_id = str(created["specId"])
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="rewrite_stage",
        content=(
            "# Requirements\n\n"
            "- REQ-001: Build a browser counter app with an explicit `SPEC_DRY_RUN_COUNTER` marker.\n"
            "- REQ-002: The visible count must increase after clicking the button.\n"
        ),
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements", comment="dry-run approved")

    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="design")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content=(
            "# Design\n\n"
            "- DES-001: Implement the app as a self-contained `index.html` with inline JavaScript.\n"
            "- DES-002: Add `README.md` with launch and smoke-test instructions.\n"
        ),
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design", comment="dry-run approved")

    spec_service.create_stage(workspace_path=str(workspace), user_request="", spec_id=spec_id, stage="tasks")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content=(
            "# Tasks\n\n"
            "### TASK-001: Implement counter artifact\n\n"
            "- runtimeLane: Engineering\n"
            "- dependsOn: []\n"
            "- specRefs: REQ-001, REQ-002, DES-001\n"
            "- inputRefs: approved requirements/design/tasks\n"
            "- expectedOutput: index.html\n"
            "- acceptance: `index.html` contains `SPEC_DRY_RUN_COUNTER` and a clickable increment button.\n"
            "- proofRequired: report touched file and smoke verification.\n\n"
            "### TASK-002: Document usage\n\n"
            "- runtimeLane: Engineering\n"
            "- dependsOn: [TASK-001]\n"
            "- specRefs: REQ-001, DES-002\n"
            "- inputRefs: approved implementation target\n"
            "- expectedOutput: README.md\n"
            "- acceptance: README explains how to open and verify the counter.\n"
            "- proofRequired: report touched file.\n"
        ),
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks", comment="dry-run approved")
    return spec_id, "embedded-counter-fallback"


def _agent_preview(item: dict[str, Any]) -> dict[str, Any]:
    context = item.get("context") if isinstance(item.get("context"), dict) else {}
    acceptance_tiers = item.get("acceptanceTiers") if isinstance(item.get("acceptanceTiers"), dict) else {}
    execution_contract = context.get("engineeringExecutionContract") if isinstance(context.get("engineeringExecutionContract"), dict) else {}
    handoff_contract = context.get("handoffContract") if isinstance(context.get("handoffContract"), dict) else {}
    source_refs = execution_contract.get("sourceRefs") if isinstance(execution_contract.get("sourceRefs"), dict) else {}
    return {
        "taskBriefId": item.get("taskBriefId"),
        "goal": item.get("goal"),
        "specId": context.get("specId"),
        "taskDetailRef": context.get("taskDetailRef"),
        "specExecutionSummary": context.get("specExecutionSummary"),
        "frameworkDigest": context.get("frameworkDigest"),
        "must": acceptance_tiers.get("must"),
        "allowedWorkset": execution_contract.get("allowedWorkset"),
        "forbiddenScopes": execution_contract.get("forbiddenScopes"),
        "requirementIds": source_refs.get("requirementIds"),
        "designIds": source_refs.get("designIds"),
        "detailRefs": source_refs.get("detailRefs"),
        "handoffRequired": handoff_contract.get("requiredFields"),
    }


def _contains(blob: Any, needle: str) -> bool:
    return needle.lower() in json.dumps(blob, ensure_ascii=False).lower()


def _tool_description_preview() -> dict[str, Any]:
    tools = {
        str(getattr(tool, "name", "") or ""): tool
        for tool in [*select_baseline_system_tools(NATIVE_TOOLS), fetch_skill_instructions]
    }
    required = ["read_native_file", "write_native_file", "run_system_command", "command_session_broker", "fetch_skill_instructions"]
    previews: dict[str, str] = {}
    checks: dict[str, bool] = {}
    for name in required:
        description = str(getattr(tools.get(name), "description", "") or "").strip()
        previews[name] = description[:500]
        checks[f"{name}_has_actionable_description"] = len(description) >= 60
    skill_description = previews.get("fetch_skill_instructions", "")
    checks["fetch_skill_description_mentions_complete_skill_md"] = "complete SKILL.md" in skill_description
    checks["fetch_skill_description_mentions_relative_path"] = "relative_path" in skill_description
    return {"checks": checks, "previews": previews}


def _build_supervisor_owned_surfaces(worker_briefs: list[dict[str, Any]]) -> dict[str, Any]:
    parent_brief = dict(worker_briefs[0]) if worker_briefs else {}
    delegated_plan = _format_delegated_task_contract(parent_brief)
    subagent_system = _build_agent_system_content(
        agent_name="Dry Run Engineering Worker",
        agent_system_prompt="Follow the delegated task contract and return typed handoff evidence.",
        env_context="<environment>\nActive Workspace Root: <dry-run-workspace>\n</environment>\n",
        delegated_plan_context=delegated_plan,
        route_prompt_addition="",
    )
    parent_context = parent_brief.get("context") if isinstance(parent_brief.get("context"), dict) else {}
    child_brief = {
        "taskBriefId": "grandchild-verification-task",
        "goal": "Verify the assigned Spec task evidence and report concrete gaps, not only IDs.",
        "brief": "Read the inherited requirement/design/task refs before returning verification.",
        "runtimeAccess": ["delegation.recursive"],
        "acceptanceContract": "Parent subagent can use this handoff without guessing missing Spec context.",
        "context": {
            "specId": parent_context.get("specId"),
            "taskId": parent_context.get("taskId"),
            "specDocumentPaths": parent_context.get("specDocumentPaths"),
            "specExecutionSummary": parent_context.get("specExecutionSummary"),
            "frameworkDigest": parent_context.get("frameworkDigest"),
            "approvedRequirementSlice": parent_context.get("approvedRequirementSlice"),
            "approvedDesignSlice": parent_context.get("approvedDesignSlice"),
        },
    }
    child_request = _child_request_from_send_state(
        {
            "parallel_branch": {
                "agentId": "verification-worker",
                "agentName": "Verification Worker",
                "delegationId": "delegation-grandchild-dry-run",
                "invocationId": "invoke-grandchild-dry-run",
                "taskBriefId": "grandchild-verification-task",
                "reason": "grandchild-verification-task",
                "taskBrief": child_brief,
                "delegationDepth": 2,
            }
        },
        source_branch={
            "agentId": "engineering-worker",
            "agentName": "Engineering Worker",
            "delegationId": "delegation-parent-dry-run",
            "invocationId": "invoke-parent-dry-run",
            "allowChildDelegation": True,
        },
        source_agent_id="engineering-worker",
    )
    checks = {
        "subagent_prompt_has_no_legacy_planner_origin": "supervisor's planner/delegation pipeline" not in delegated_plan,
        "subagent_prompt_has_supervisor_runtime_origin": "supervisor's delegation/runtime pipeline" in delegated_plan,
        "subagent_prompt_has_spec_id": _contains(subagent_system, str(parent_context.get("specId") or "")),
        "subagent_prompt_has_task_goal": _contains(subagent_system, str(parent_brief.get("goal") or "")),
        "subagent_prompt_has_requirement_slice": _contains(subagent_system, "REQ-") or _contains(subagent_system, "需求"),
        "subagent_prompt_has_design_slice": _contains(subagent_system, "DES-") or _contains(subagent_system, "设计") or _contains(subagent_system, "index.html"),
        "subagent_prompt_omits_runtime_only_bundle": "specExecutionBundle" not in subagent_system,
        "grandchild_request_preserves_real_goal": bool(child_request and child_request.get("childTaskGoal") and child_request.get("childTaskGoal") != child_request.get("childTaskBriefId")),
        "grandchild_request_preserves_spec_context": _contains(child_request, str(parent_context.get("specId") or "")),
        "grandchild_request_not_id_only": _contains(child_request, "Verify the assigned Spec task evidence"),
    }
    return {
        "checks": checks,
        "delegatedTaskContractPreview": delegated_plan[:5000],
        "subagentSystemPreview": subagent_system[:8000],
        "grandchildRequestPreview": child_request,
    }


def build_export(*, sample_spec_dir: Path | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="v8os-spec-distribution-") as tmp:
        workspace = Path(tmp).resolve()
        spec_id, sample_source = _create_approved_demo_spec(workspace, sample_spec_dir=sample_spec_dir)
        with bind_runtime_context(
            runtime_kind="chat",
            session_id="dry-run-spec-distribution",
            run_id="run-dry-run-spec-distribution",
            rootRunId="root-dry-run-spec-distribution",
            workspace_path=str(workspace),
        ):
            command = runtime_broker.func(
                mode="route",
                runtime_kind="engineering",
                need={
                    "kind": "engineering",
                    "reason": "approved_spec_runtime_execution",
                    "specId": spec_id,
                },
                state={"current_route_context": {}},
                tool_call_id="call-dry-run-spec-distribution",
            )

        route_context = dict((getattr(command, "update", None) or {}).get("current_route_context") or {})
        episode = dict((route_context.get("capabilityEpisodes") or [{}])[-1])
        inputs = dict(episode.get("inputs") or {})
        bundle = dict(inputs.get("specExecutionBundle") or {})
        worker_briefs = list(inputs.get("workerBriefs") or [])
        agent_preview = [_agent_preview(item) for item in worker_briefs if isinstance(item, dict)]
        first_requirement_ids = [
            str(item)
            for item in (agent_preview[0].get("requirementIds") if agent_preview else []) or []
        ]
        validations = {
            "bundle_has_traceability": bool((bundle.get("traceability") or {}).get("frameworkDigest"))
            and bool(bundle.get("tasks")),
            "agent_preview_has_framework": _contains(agent_preview, "uni-app")
            or _contains(agent_preview, "inline JavaScript"),
            "agent_preview_has_task_slice": _contains(agent_preview, "TASK-")
            and (_contains(agent_preview, "需求") or _contains(agent_preview, "requirements")),
            "agent_preview_has_requirement_snippet": _contains(agent_preview, "env.template")
            or _contains(agent_preview, "SPEC_DRY_RUN_COUNTER"),
            "agent_preview_has_requirement_ids": _contains(agent_preview, "6.1")
            or _contains(agent_preview, "REQ-001"),
            "agent_preview_refs_are_task_scoped": sample_source == "embedded-counter-fallback"
            or "1.1" not in first_requirement_ids,
            "agent_preview_has_design_snippet": _contains(agent_preview, "utils/config.js")
            or _contains(agent_preview, "index.html"),
            "tool_surface_is_compact": not str(_tool_payload(command)).lstrip().startswith("["),
        }
        supervisor_owned_surfaces = _build_supervisor_owned_surfaces(worker_briefs)
        tool_description_surface = _tool_description_preview()
        validations.update(supervisor_owned_surfaces["checks"])
        validations.update(tool_description_surface["checks"])
        return {
            "ok": True,
            "kind": "spec_runtime_distribution_dry_run",
            "specId": spec_id,
            "sampleSource": sample_source,
            "passed": all(validations.values()),
            "validations": validations,
            "toolSurface": _tool_payload(command),
            "episode": {
                "episodeId": episode.get("episodeId"),
                "kind": episode.get("kind"),
                "state": episode.get("state"),
                "reason": episode.get("reason"),
            },
            "runtimeSurface": {
                "specExecutionBundle": bundle,
                "workerBriefs": worker_briefs,
                "taskBriefs": list(inputs.get("taskBriefs") or []),
                "proofExpectations": list(inputs.get("proofExpectations") or []),
            },
            "agentSurfacePreview": agent_preview,
            "supervisorOwnedAgentContent": supervisor_owned_surfaces,
            "subagentToolDescriptions": tool_description_surface,
        }


def _write_default_reports(payload: dict[str, Any]) -> dict[str, str]:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_ROOT / f"{stamp}_spec_runtime_distribution_dry_run.json"
    md_path = OUTPUT_ROOT / f"{stamp}_spec_runtime_distribution_dry_run.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    validation_lines = "\n".join(
        f"- {'PASS' if ok else 'FAIL'} `{name}`"
        for name, ok in dict(payload.get("validations") or {}).items()
    )
    preview = json.dumps(payload.get("agentSurfacePreview") or [], ensure_ascii=False, indent=2)
    md_path.write_text(
        f"""# Spec Runtime Distribution Dry Run

No model call, no database write, no persistent workspace mutation.

## Result

Overall: **{'PASS' if payload.get('passed') else 'FAIL'}**

Sample source: `{payload.get('sampleSource')}`

{validation_lines}

## Agent Surface Preview

```json
{preview}
```

## Supervisor-Owned Content Checks

```json
{json.dumps(payload.get('supervisorOwnedAgentContent') or {}, ensure_ascii=False, indent=2)[:12000]}
```
""",
        encoding="utf-8",
    )
    return {"json": str(json_path), "markdown": str(md_path)}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Export a dry-run approved Spec runtime distribution payload.")
    parser.add_argument("--output", default="", help="Optional JSON output path.")
    parser.add_argument("--sample-spec-dir", default=str(DEFAULT_REFERENCE_SPEC_DIR), help="Optional requirements/design/tasks sample directory.")
    parser.add_argument("--write-report", action="store_true", help="Write markdown/json reports under docs/chatruntime/runtime_deep_observation_reports.")
    args = parser.parse_args()

    payload = build_export(sample_spec_dir=Path(args.sample_spec_dir).expanduser() if args.sample_spec_dir else None)
    if args.write_report:
        payload["reports"] = _write_default_reports(payload)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(str(output))
    else:
        print(rendered)


if __name__ == "__main__":
    main()
