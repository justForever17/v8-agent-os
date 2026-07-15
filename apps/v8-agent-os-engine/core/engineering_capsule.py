from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable


ENGINEERING_FILE_MUTATION_TOOL_NAMES = {
    "download_media_for_vision",
    "write_native_file",
    "replace_native_file",
    "edit_native_file",
    "delete_native_file",
}

ENGINEERING_COMMAND_TOOL_NAMES = {
    "run_system_command",
    "execute_system_command",
    "command_session_broker",
    "send_background_input",
    "terminate_background_command",
}


def _values(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _unique_text(values: Iterable[Any], *, limit: int = 64) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("path") or value.get("ref") or value.get("id") or value.get("command")
        text = str(value or "").strip()
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _task_context(task_brief: dict[str, Any] | None) -> dict[str, Any]:
    task = task_brief if isinstance(task_brief, dict) else {}
    return dict(task.get("context") or {}) if isinstance(task.get("context"), dict) else {}


def _declared_tools(task_brief: dict[str, Any]) -> set[str]:
    policy = task_brief.get("toolPolicy") if isinstance(task_brief.get("toolPolicy"), dict) else {}
    values = [
        *_values(task_brief.get("allowedTools") or task_brief.get("allowed_tools")),
        *_values(policy.get("allowedTools") or policy.get("allowed_tools")),
    ]
    return {str(value or "").strip() for value in values if str(value or "").strip()}


def _seal_capsule(capsule: dict[str, Any]) -> dict[str, Any]:
    sealed = deepcopy(dict(capsule or {}))
    digest_payload = {key: value for key, value in sealed.items() if key != "capsuleId"}
    sealed["capsuleId"] = "engcap_" + hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return sealed


def effective_engineering_capsule(task_brief: dict[str, Any] | None) -> dict[str, Any]:
    """Return the authoritative engineering contract visible to one worker.

    A capsule is created only from explicit execution-contract fields. Merely
    classifying a task as engineering never grants file or shell authority.
    """

    task = dict(task_brief or {})
    context = _task_context(task)
    raw_capsule = task.get("engineeringTaskCapsule") or task.get("engineering_task_capsule")
    capsule = dict(raw_capsule or {}) if isinstance(raw_capsule, dict) else {}
    raw_contract = context.get("engineeringExecutionContract") or context.get("engineering_execution_contract")
    contract = dict(raw_contract or {}) if isinstance(raw_contract, dict) else {}
    declared_mutation_tools = _declared_tools(task) & ENGINEERING_FILE_MUTATION_TOOL_NAMES

    explicit_contract = bool(
        capsule
        or contract
        or declared_mutation_tools
        or task.get("writeSet")
        or task.get("write_set")
        or task.get("readSet")
        or task.get("read_set")
        or task.get("verificationMatrix")
        or task.get("verification_matrix")
        or task.get("proofExpectations")
        or task.get("proof_expectations")
        or task.get("writeRequired")
        or task.get("write_required")
    )
    if not explicit_contract:
        return {}

    read_set = _unique_text(
        [
            *_values(capsule.get("readSet") or capsule.get("read_set")),
            *_values(contract.get("mustRead") or contract.get("readSet") or contract.get("read_set")),
            *_values(task.get("readSet") or task.get("read_set")),
        ]
    )
    write_set = _unique_text(
        [
            *_values(capsule.get("writeSet") or capsule.get("write_set")),
            *_values(contract.get("allowedWorkset") or contract.get("writeSet") or contract.get("write_set")),
            *_values(task.get("writeSet") or task.get("write_set")),
        ]
    )
    expected_outputs = _unique_text(
        [
            *_values(task.get("expectedOutputs") or task.get("expected_outputs")),
            *_values(task.get("expectedOutput") or task.get("expected_output")),
        ]
    )
    explicit_expected_artifacts = _unique_text(
        [
            *_values(capsule.get("expectedArtifacts") or capsule.get("expected_artifacts")),
            *_values(contract.get("expectedArtifacts") or contract.get("expected_artifacts")),
            *_values(task.get("expectedArtifacts") or task.get("expected_artifacts")),
        ]
    )
    # Artifact existence checks operate on paths, never on prose acceptance
    # statements. If no dedicated artifact list is supplied, the bounded
    # writeSet is the authoritative set of files this task must produce or
    # update.
    expected_artifacts = explicit_expected_artifacts or list(write_set)
    verification = deepcopy(
        _first_present(
            capsule.get("verificationContract"),
            capsule.get("verification_contract"),
            contract.get("verificationMatrix"),
            contract.get("verificationContract"),
            task.get("verificationMatrix"),
            task.get("verification_matrix"),
        )
        or []
    )
    proof_expectations = _unique_text(
        [
            *_values(capsule.get("proofExpectations") or capsule.get("proof_expectations")),
            *_values(contract.get("proofExpectations") or contract.get("proof_expectations")),
            *_values(task.get("proofExpectations") or task.get("proof_expectations")),
        ]
    )
    risk_flags = _unique_text(
        [
            *_values(capsule.get("riskFlags") or capsule.get("risk_flags")),
            *_values(contract.get("riskFlags") or contract.get("risk_flags")),
        ],
        limit=24,
    )
    write_required = bool(
        write_set
        or declared_mutation_tools
        or capsule.get("writeRequired")
        or capsule.get("write_required")
        or contract.get("writeRequired")
        or contract.get("write_required")
        or task.get("writeRequired")
        or task.get("write_required")
    )
    acceptance = deepcopy(
        _first_present(
            contract.get("acceptance"),
            contract.get("acceptanceContract"),
            task.get("acceptanceContract"),
            task.get("acceptance_contract"),
        )
        or ""
    )
    missing_contract_fields: list[str] = []
    if write_required:
        if not write_set:
            missing_contract_fields.append("writeSet")
        if not expected_outputs:
            missing_contract_fields.append("expectedOutputs")
        if acceptance in (None, "", [], {}):
            missing_contract_fields.append("acceptanceContract")
    contract_status = "invalid" if missing_contract_fields else "valid"
    requested_mode = str(capsule.get("executionMode") or capsule.get("execution_mode") or "").strip().lower()
    execution_mode = (
        "read_only"
        if requested_mode == "read_only"
        else "write"
        if write_required and contract_status == "valid"
        else "verify"
        if requested_mode == "verify" or (verification and not write_required)
        else "read_only"
    )
    normalized = {
        "schemaVersion": "v8.engineering_task_capsule.v1",
        "taskId": str(
            _first_present(
                capsule.get("taskId"),
                contract.get("taskId"),
                task.get("taskBriefId"),
                task.get("task_brief_id"),
            )
            or ""
        ).strip(),
        "workspacePath": str(
            _first_present(
                capsule.get("workspacePath"),
                contract.get("workspacePath"),
                context.get("workspacePath"),
                task.get("workspacePath"),
            )
            or ""
        ).strip(),
        "shellDialect": str(
            _first_present(
                capsule.get("shellDialect"),
                contract.get("shellDialect"),
                context.get("shellDialect"),
            )
            or ""
        ).strip(),
        "executionMode": execution_mode,
        "contractStatus": contract_status,
        "missingContractFields": missing_contract_fields,
        "writeRequired": write_required,
        "criticalFiles": _unique_text(
            [
                *_values(capsule.get("criticalFiles") or capsule.get("critical_files")),
                *_values(task.get("criticalFiles") or task.get("critical_files")),
            ]
        ),
        "readSet": read_set,
        "writeSet": write_set,
        "expectedOutputs": expected_outputs,
        "expectedArtifacts": expected_artifacts,
        "verificationContract": verification,
        "proofExpectations": proof_expectations,
        "riskFlags": risk_flags,
        "acceptance": acceptance,
    }
    parent_capsule_id = str(capsule.get("parentCapsuleId") or "").strip()
    if parent_capsule_id:
        normalized["parentCapsuleId"] = parent_capsule_id
    inherited_contract = context.get("inheritedEngineeringContract")
    if isinstance(inherited_contract, dict) and inherited_contract:
        normalized["inheritedEngineeringContract"] = deepcopy(inherited_contract)
    for key, value in capsule.items():
        if key not in normalized and key not in {"capsuleId", "execution_mode", "write_required"}:
            normalized[key] = deepcopy(value)
    return _seal_capsule(normalized)


def engineering_capsule_mode(task_brief: dict[str, Any] | None) -> str:
    capsule = effective_engineering_capsule(task_brief)
    return str(capsule.get("executionMode") or "none") if capsule else "none"


def ensure_engineering_task_capsule(
    task_brief: dict[str, Any],
    *,
    shell_dialect: str = "",
) -> dict[str, Any]:
    task = deepcopy(dict(task_brief or {}))
    capsule = effective_engineering_capsule(task)
    if not capsule:
        return task
    context = _task_context(task)
    if shell_dialect and not capsule.get("shellDialect"):
        capsule["shellDialect"] = shell_dialect
        capsule = _seal_capsule(capsule)
    context.setdefault("shellDialect", capsule.get("shellDialect") or shell_dialect)
    existing_contract = (
        deepcopy(context.get("engineeringExecutionContract"))
        if isinstance(context.get("engineeringExecutionContract"), dict)
        else {}
    )
    context["engineeringExecutionContract"] = {
        **existing_contract,
        "schemaVersion": capsule.get("schemaVersion"),
        "capsuleId": capsule.get("capsuleId"),
        "taskId": capsule.get("taskId"),
        "workspacePath": capsule.get("workspacePath"),
        "shellDialect": capsule.get("shellDialect"),
        "executionMode": capsule.get("executionMode"),
        "contractStatus": capsule.get("contractStatus"),
        "missingContractFields": list(capsule.get("missingContractFields") or []),
        "writeRequired": capsule.get("writeRequired"),
        "allowedWorkset": list(capsule.get("writeSet") or []),
        "mustRead": list(capsule.get("readSet") or []),
        "expectedOutputs": list(capsule.get("expectedOutputs") or []),
        "expectedArtifacts": list(capsule.get("expectedArtifacts") or []),
        "verificationMatrix": deepcopy(capsule.get("verificationContract") or []),
        "proofExpectations": list(capsule.get("proofExpectations") or []),
        "riskFlags": list(capsule.get("riskFlags") or []),
        "acceptance": deepcopy(capsule.get("acceptance") or ""),
    }
    task["context"] = context
    task["engineeringTaskCapsule"] = capsule
    task["readSet"] = list(capsule.get("readSet") or [])
    task["writeSet"] = list(capsule.get("writeSet") or [])
    task["writeRequired"] = bool(capsule.get("writeRequired"))
    return task


def derive_grandchild_engineering_task(
    parent_task_brief: dict[str, Any] | None,
    child_task_brief: dict[str, Any],
    *,
    shell_dialect: str = "",
) -> dict[str, Any]:
    """Derive a lossless, read-only Capsule for recursive delegation.

    Facts, acceptance, verification, and proof requirements are preserved.
    Write authority is intentionally not inherited across a delegation depth.
    """

    child = deepcopy(dict(child_task_brief or {}))
    parent_capsule = effective_engineering_capsule(parent_task_brief)
    if not parent_capsule:
        return child
    child_context = _task_context(child)
    child_context["inheritedEngineeringContract"] = deepcopy(parent_capsule)
    child_context.setdefault("shellDialect", shell_dialect or parent_capsule.get("shellDialect") or "")
    child_context.setdefault(
        "artifactWriteDiscipline",
        "This grandchild receives engineering facts and acceptance criteria but no inherited write authority. Return evidence to the parent.",
    )
    child["context"] = child_context
    child["readOnly"] = True
    child["writeRequired"] = False
    child["readSet"] = _unique_text(
        [
            *_values(parent_capsule.get("readSet")),
            *_values(parent_capsule.get("writeSet")),
            *_values(child.get("readSet")),
        ]
    )
    child["writeSet"] = []
    if not list(child.get("expectedOutputs") or []):
        child["expectedOutputs"] = ["Compact evidence handoff for the parent task"]
    if child.get("acceptanceContract") in (None, "", [], {}):
        parent_acceptance = deepcopy(parent_capsule.get("acceptance") or "")
        child["acceptanceContract"] = (
            f"Return a compact evidence handoff to the parent. Evaluate against parent acceptance: {parent_acceptance}"
            if parent_acceptance
            else "Return a compact evidence handoff to the parent."
        )
    child["engineeringTaskCapsule"] = {
        "schemaVersion": "v8.engineering_task_capsule.v1",
        "parentCapsuleId": parent_capsule.get("capsuleId"),
        "taskId": str(child.get("taskBriefId") or child.get("id") or "").strip(),
        "workspacePath": parent_capsule.get("workspacePath"),
        "shellDialect": shell_dialect or parent_capsule.get("shellDialect") or "",
        "executionMode": "read_only",
        "writeRequired": False,
        "criticalFiles": list(parent_capsule.get("criticalFiles") or []),
        "readSet": list(child.get("readSet") or []),
        "writeSet": [],
        "expectedOutputs": list(child.get("expectedOutputs") or []),
        "expectedArtifacts": [],
        "verificationContract": deepcopy(parent_capsule.get("verificationContract") or []),
        "proofExpectations": list(parent_capsule.get("proofExpectations") or []),
        "riskFlags": [*list(parent_capsule.get("riskFlags") or []), "grandchild_write_authority_not_inherited"],
        "acceptance": deepcopy(child.get("acceptanceContract") or ""),
    }
    return ensure_engineering_task_capsule(child, shell_dialect=shell_dialect)


def engineering_tool_allowed(tool_name: str, task_brief: dict[str, Any] | None) -> bool:
    name = str(tool_name or "").strip()
    mode = engineering_capsule_mode(task_brief)
    if name in ENGINEERING_FILE_MUTATION_TOOL_NAMES:
        return mode == "write"
    if name in ENGINEERING_COMMAND_TOOL_NAMES:
        return mode in {"verify", "write"}
    return True


def task_brief_from_route_context(route_context: dict[str, Any] | None) -> dict[str, Any]:
    context = dict(route_context or {})
    nested = context.get("current_route_context")
    if isinstance(nested, dict):
        context = {**dict(nested), **context}
    for key in ("taskBrief", "task_brief", "engineeringTaskBrief", "engineering_task_brief"):
        value = context.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


__all__ = [
    "ENGINEERING_COMMAND_TOOL_NAMES",
    "ENGINEERING_FILE_MUTATION_TOOL_NAMES",
    "derive_grandchild_engineering_task",
    "effective_engineering_capsule",
    "engineering_capsule_mode",
    "engineering_tool_allowed",
    "ensure_engineering_task_capsule",
    "task_brief_from_route_context",
]
