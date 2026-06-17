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
from core.spec_service import spec_service
from erc.runtime_context import bind_runtime_context

REPO_ROOT = ENGINE_ROOT.parents[1]
OUTPUT_ROOT = REPO_ROOT / "docs" / "chatruntime" / "runtime_deep_observation_reports"
DEFAULT_REFERENCE_SPEC_DIR = Path("E:/Projects/pdf2docx/.kiro/specs/pdf-to-docx-converter")


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
            "requirements": (sample_dir / "requirements.md").read_text(encoding="utf-8", errors="ignore"),
            "design": (sample_dir / "design.md").read_text(encoding="utf-8", errors="ignore"),
            "tasks": (sample_dir / "tasks.md").read_text(encoding="utf-8", errors="ignore"),
        }
        if all(value.strip() for value in payload.values()):
            return payload
    except Exception:
        return None
    return None


def _create_approved_demo_spec(workspace: Path, *, sample_spec_dir: Path | None = None) -> tuple[str, str]:
    reference = _read_reference_spec(sample_spec_dir or DEFAULT_REFERENCE_SPEC_DIR)
    if reference:
        created = spec_service.create_stage(
            workspace_path=str(workspace),
            user_request="Validate Kiro-style PDF to DOCX converter Spec distribution.",
            feature_name="pdf-to-docx-converter-reference",
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
""",
        encoding="utf-8",
    )
    return {"json": str(json_path), "markdown": str(md_path)}


def main() -> None:
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
