from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.native_tools import runtime_broker
from core.spec_service import spec_service
from erc.runtime_context import bind_runtime_context


def _tool_payload(command: Any) -> dict[str, Any]:
    messages = list((getattr(command, "update", None) or {}).get("messages") or [])
    if not messages:
        return {}
    try:
        return json.loads(str(messages[0].content or "{}"))
    except json.JSONDecodeError:
        return {"raw": str(messages[0].content or "")}


def _create_approved_demo_spec(workspace: Path) -> str:
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
    return spec_id


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
        "must": acceptance_tiers.get("must"),
        "allowedWorkset": execution_contract.get("allowedWorkset"),
        "forbiddenScopes": execution_contract.get("forbiddenScopes"),
        "detailRefs": source_refs.get("detailRefs"),
        "handoffRequired": handoff_contract.get("requiredFields"),
    }


def build_export() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="v8os-spec-distribution-") as tmp:
        workspace = Path(tmp).resolve()
        spec_id = _create_approved_demo_spec(workspace)
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
        return {
            "ok": True,
            "kind": "spec_runtime_distribution_dry_run",
            "specId": spec_id,
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
            "agentSurfacePreview": [_agent_preview(item) for item in worker_briefs if isinstance(item, dict)],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a dry-run approved Spec runtime distribution payload.")
    parser.add_argument("--output", default="", help="Optional JSON output path.")
    args = parser.parse_args()

    payload = build_export()
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
