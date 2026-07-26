from __future__ import annotations

import json
from typing import Any


RUNTIME_ROUTE_KINDS = (
    "research",
    "engineering",
    "creative_media",
    "computer_use",
    "rpa",
    "delegation",
)


def runtime_route_contract_example(kind: str = "engineering") -> dict[str, Any]:
    """Return one copyable route example with stable JSON types.

    The values are intentionally obvious placeholders, while the object/array
    shape is a real contract that a model can copy without inventing aliases.
    """

    normalized_kind = str(kind or "engineering").strip().lower()
    if normalized_kind not in RUNTIME_ROUTE_KINDS:
        normalized_kind = "engineering"
    write_required = normalized_kind in {"engineering", "creative_media"}
    read_only = normalized_kind == "research"
    task_id = f"{normalized_kind}-task-001"
    if normalized_kind == "research":
        research_brief_ids = ["research-domain-a", "research-domain-b"]
        research_brief_goals = [
            "Verify <first fact domain> with current authoritative sources.",
            "Verify <second fact domain> with current authoritative sources.",
        ]
        task_briefs = []
        proof_expectations = [
            "One source-backed terminal result or explicit evidence gap per taskBriefId",
        ]
    elif normalized_kind == "engineering":
        task_briefs = [
            {
                "taskBriefId": "engineering-implementation",
                "goal": "Implement one coherent workspace change with a bounded workset.",
                "context": {
                    "symptom": "<current user request or failure symptom>",
                    "priorRefs": ["<relevant episode, handoff, spec, or proof ref>"],
                },
                "writeRequired": True,
                "readOnly": False,
                "writeSet": [
                    "<workspace-relative implementation path>",
                    "<closely coupled test path>",
                ],
                "expectedOutputs": ["Implemented change and its focused test"],
                "acceptanceContract": [
                    "The bounded implementation exists",
                    "The focused test passes",
                ],
                "constraints": [],
                "detailRefs": [],
                "dependencies": [],
            },
            {
                "taskBriefId": "engineering-verification",
                "goal": "Run final verification and persist machine-readable proof.",
                "context": {
                    "priorRefs": ["engineering-implementation"],
                },
                "writeRequired": True,
                "readOnly": False,
                "writeSet": ["<workspace-relative verification result path>"],
                "expectedOutputs": ["Machine-readable verification evidence"],
                "acceptanceContract": [
                    "Final verification succeeds or reports an explicit blocker",
                    "Proof identifies the verified implementation result",
                ],
                "constraints": [],
                "detailRefs": [],
                "dependencies": ["engineering-implementation"],
            },
        ]
        proof_expectations = [
            "changed-file or artifact references per taskBriefId",
            "final verification command/check and machine-readable outcome",
        ]
    elif normalized_kind == "creative_media":
        task_briefs = [
            {
                "taskBriefId": task_id,
                "goal": "Execute one exact media operation and return its governed artifact proof.",
                "context": {
                    "creativeMediaExecutionContract": {
                        "schema": "v8.creative_media_execution.v1",
                        "execution": {
                            "tool": "creative_media_jobs",
                            "arguments": {
                                "action": "create",
                                "request": {
                                    "modality": "image",
                                    "operationKind": "image.generate",
                                    "prompt": "<exact user-approved media instruction>",
                                },
                            },
                        },
                    }
                },
                "writeRequired": True,
                "readOnly": False,
                "writeSet": [".v8/creative-media/"],
                "expectedOutputs": ["One session-bound media artifact plus job proof"],
                "acceptanceContract": [
                    "The persisted job keeps the exact requested operationKind",
                    "The artifact and proof refs retain current session/run/workspace lineage",
                ],
                "constraints": [],
                "detailRefs": [],
            }
        ]
        proof_expectations = [
            "session-bound Creative Media artifact reference",
            "durable job/provider/model proof without raw provider payload",
        ]
    else:
        task_briefs = [
            {
                "taskBriefId": task_id,
                "goal": "Complete the requested task and verify the result.",
                "context": {
                    "symptom": "<current user request or failure symptom>",
                    "priorRefs": ["<relevant episode, handoff, spec, or proof ref>"],
                },
                "writeRequired": write_required,
                "readOnly": read_only,
                "writeSet": ["<workspace-relative target path>"] if write_required else [],
                "expectedOutputs": ["Result plus verification evidence"],
                "acceptanceContract": [
                    "The requested result exists",
                    "The verification command or check succeeds",
                ],
                "constraints": [],
                "detailRefs": [],
            }
        ]
        proof_expectations = [
            "artifact or changed-file reference",
            "verification command/check and outcome",
        ]
    return {
        "mode": "route",
        "routeKind": normalized_kind,
        "routeReason": "Continue the current task with a typed runtime handoff and verification.",
        "workspacePath": "<current workspace path>",
        **(
            {
                "researchBriefIds": research_brief_ids,
                "researchBriefGoals": research_brief_goals,
            }
            if normalized_kind == "research"
            else {"taskBriefs": task_briefs}
        ),
        "proofExpectations": proof_expectations,
    }


def runtime_route_parameter_guidance(kind: str = "engineering") -> dict[str, Any]:
    normalized_kind = str(kind or "engineering").strip().lower()
    research_route = normalized_kind == "research"
    guidance = {
        "canonicalTaskArray": "taskBriefs",
        "requiredPaths": [
            "routeKind",
            "routeReason",
            *(
                ["researchBriefIds[]", "researchBriefGoals[]"]
                if research_route
                else [
                    "taskBriefs[].taskBriefId",
                    "taskBriefs[].goal",
                ]
            ),
        ],
        "arrayPaths": [
            "taskBriefs",
            "taskBriefs[].writeSet",
            "taskBriefs[].expectedOutputs",
            "taskBriefs[].constraints",
            "taskBriefs[].detailRefs",
            "taskBriefs[].dependencies",
            "proofExpectations",
        ],
        "discipline": [
            (
                "For Research use matching researchBriefIds and researchBriefGoals primitive arrays; the Engine zips them into internal taskBriefs."
                if research_route
                else "Use taskBriefs for new calls; workerBriefs/tasks are read-only legacy aliases."
            ),
            "Coverage first: write one compact brief for every known work unit before adding optional detail. Completeness of taskBriefs outranks detail inside any one brief.",
            "Keep each goal to one sentence. For read-only Research, omit optional context before omitting a known fact domain.",
            "For research, put every currently known independent fact domain in the same initial ID/goal arrays; both arrays must have equal length and order. Do not route only the first domain and defer already-known domains to repair episodes.",
            "For engineering, one taskBrief is one coherent independently executable and acceptable work unit. Split separable implementation, generated results/documentation, and final verification into dependent briefs; do not assign one worker an unrelated project-wide writeSet or rename the same oversized brief as a repair.",
            "For Creative Media, when operationKind and all required inputs are already known, put the exact job in taskBriefs[].context.creativeMediaExecutionContract using schema v8.creative_media_execution.v1. The runtime executes that contract without re-inferring operationKind. Omit the contract only when the Creative Media Director must genuinely plan unresolved choices.",
            "Engineering writeSet entries are paths relative to the original bound workspace. Never copy an absolute managed-worktree path from a handoff. Declare every generated file deterministically, or confine variable names below one declared output directory; do not let versioned/cache/report variants escape the declared scope.",
            "Omit optional arrays when empty. For task ordering use the plural dependencies array; singular dependency is a read-only legacy alias.",
            "Preserve object and array types; use [] or omit an optional array, never an empty string.",
            "Do not JSON-encode researchBriefIds, researchBriefGoals, or taskBriefs into strings.",
            "For a write task include a bounded writeSet, expectedOutputs, and acceptanceContract.",
        ],
        "example": runtime_route_contract_example(kind),
    }
    if research_route:
        guidance["canonicalTaskMap"] = "researchBriefIds + researchBriefGoals"
        guidance.pop("canonicalTaskArray", None)
        guidance["arrayPaths"] = ["researchBriefIds", "researchBriefGoals", "researchBriefContexts", "proofExpectations"]
    return guidance


def render_runtime_route_contract(kind: str = "engineering", *, indent: int | None = 2) -> str:
    return json.dumps(runtime_route_contract_example(kind), ensure_ascii=False, indent=indent)


def render_runtime_route_repair_hint(
    kind: str = "engineering",
    *,
    invalid_fields: list[str] | None = None,
) -> str:
    fields = [str(item or "").strip() for item in list(invalid_fields or []) if str(item or "").strip()]
    lines = [
        "The tool call has a repairable parameter-shape error; it is not a permission denial or task failure.",
    ]
    if fields:
        lines.append("Invalid fields: " + ", ".join(fields[:8]))
    lines.extend(
        [
            "Retry the same tool once using the canonical contract below. Replace placeholder values but preserve every JSON type.",
            "Coverage first: list every known taskBriefId with a one-sentence goal before adding optional detail; never spend the argument budget on one exhaustive brief while dropping another.",
            (
                "For Research use matching researchBriefIds and researchBriefGoals arrays; do not also send taskBriefs."
                if str(kind or "").strip().lower() == "research"
                else "Use taskBriefs; arrays such as writeSet, expectedOutputs, constraints, detailRefs, dependencies, and proofExpectations must stay arrays. Omit optional arrays when empty; never use an empty string."
            ),
            "Do not encode researchBriefIds/researchBriefGoals/taskBriefs as JSON strings.",
            render_runtime_route_contract(kind, indent=None),
        ]
    )
    return "\n".join(lines)


__all__ = [
    "RUNTIME_ROUTE_KINDS",
    "render_runtime_route_contract",
    "render_runtime_route_repair_hint",
    "runtime_route_contract_example",
    "runtime_route_parameter_guidance",
]
