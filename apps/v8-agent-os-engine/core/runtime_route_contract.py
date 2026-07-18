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
    return {
        "mode": "route",
        "need": {
            "kind": normalized_kind,
            "source": "supervisor",
            "reason": "Continue the current task with a typed runtime handoff and verification.",
            "inputs": {
                "workspacePath": "<current workspace path>",
                "taskBriefs": [
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
                ],
                "proofExpectations": [
                    "artifact or changed-file reference",
                    "verification command/check and outcome",
                ],
            },
        },
    }


def runtime_route_parameter_guidance(kind: str = "engineering") -> dict[str, Any]:
    return {
        "canonicalTaskArray": "need.inputs.taskBriefs",
        "requiredPaths": [
            "need.kind",
            "need.reason",
            "need.inputs.taskBriefs[].taskBriefId",
            "need.inputs.taskBriefs[].goal",
        ],
        "arrayPaths": [
            "need.inputs.taskBriefs",
            "need.inputs.taskBriefs[].writeSet",
            "need.inputs.taskBriefs[].expectedOutputs",
            "need.inputs.taskBriefs[].constraints",
            "need.inputs.taskBriefs[].detailRefs",
            "need.inputs.taskBriefs[].dependencies",
            "need.inputs.proofExpectations",
        ],
        "discipline": [
            "Use taskBriefs for new calls; workerBriefs/tasks are read-only legacy aliases.",
            "Omit optional arrays when empty. For task ordering use the plural dependencies array; singular dependency is a read-only legacy alias.",
            "Preserve object and array types; use [] or omit an optional array, never an empty string.",
            "Do not send need={} and do not JSON-encode need, inputs, or taskBriefs into strings.",
            "For a write task include a bounded writeSet, expectedOutputs, and acceptanceContract.",
        ],
        "example": runtime_route_contract_example(kind),
    }


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
            "Use need.inputs.taskBriefs; arrays such as writeSet, expectedOutputs, constraints, detailRefs, dependencies, and proofExpectations must stay arrays. Omit optional arrays when empty; never use an empty string.",
            "Do not send need={} and do not encode need/inputs/taskBriefs as JSON strings.",
            render_runtime_route_contract(kind),
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
