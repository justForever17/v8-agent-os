from __future__ import annotations

import hashlib
import json
import re
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


def bind_engineering_task_workspace(
    task_brief: dict[str, Any],
    *,
    workspace_path: str,
    original_workspace_path: str = "",
) -> dict[str, Any]:
    """Project one engineering brief onto its actual execution worktree.

    The original workspace remains the authority boundary.  The active workspace
    path is execution-only and must follow the worktree allocated to this branch;
    otherwise a child receives a valid sandbox lease while its task capsule still
    points at the parent's checkout.
    """

    task = deepcopy(dict(task_brief or {}))
    active_workspace = str(workspace_path or "").strip()
    authority_workspace = str(original_workspace_path or "").strip()
    if not active_workspace:
        return task

    context = _task_context(task)
    context["workspacePath"] = active_workspace
    if authority_workspace:
        context["originalWorkspacePath"] = authority_workspace
    execution_contract = (
        deepcopy(context.get("engineeringExecutionContract"))
        if isinstance(context.get("engineeringExecutionContract"), dict)
        else {}
    )
    execution_contract["workspacePath"] = active_workspace
    if authority_workspace:
        execution_contract["originalWorkspacePath"] = authority_workspace
    context["engineeringExecutionContract"] = execution_contract
    task["context"] = context
    task["workspacePath"] = active_workspace
    if authority_workspace:
        task["originalWorkspacePath"] = authority_workspace

    capsule = effective_engineering_capsule(task)
    if capsule:
        capsule["workspacePath"] = active_workspace
        if authority_workspace:
            capsule["originalWorkspacePath"] = authority_workspace
        task["engineeringTaskCapsule"] = _seal_capsule(capsule)
        task = ensure_engineering_task_capsule(task)
    return task


def derive_grandchild_engineering_task(
    parent_task_brief: dict[str, Any] | None,
    child_task_brief: dict[str, Any],
    *,
    shell_dialect: str = "",
) -> dict[str, Any]:
    """Derive a lossless Capsule for a disposable grandchild mirror.

    Write authority is never inherited implicitly. A child may receive an explicit,
    narrower partition only when every requested path is covered by the parent's
    writeSet and the partition is strictly smaller than the parent authority.
    """

    child = deepcopy(dict(child_task_brief or {}))
    parent_capsule = effective_engineering_capsule(parent_task_brief)
    if not parent_capsule:
        return child
    child_context = _task_context(child)
    for stale_key in (
        "engineeringExecutionContract",
        "engineering_execution_contract",
        "engineeringTaskCapsule",
        "engineering_task_capsule",
    ):
        child_context.pop(stale_key, None)
    child_context["inheritedEngineeringContract"] = deepcopy(parent_capsule)
    child_context.setdefault("shellDialect", shell_dialect or parent_capsule.get("shellDialect") or "")
    child_context.setdefault(
        "artifactWriteDiscipline",
        "This grandchild receives engineering facts and acceptance criteria but no inherited write authority. Return evidence to the parent.",
    )
    child["context"] = child_context
    parent_write_set = _unique_text(_values(parent_capsule.get("writeSet")))
    requested_child_write_set = _unique_text(_values(child.get("writeSet")))

    def _path_key(value: Any) -> str:
        text = str(value or "").strip().strip("`'\"").replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        return re.sub(r"/+", "/", text).rstrip("/").casefold()

    def _covered_by_parent(child_path: str) -> bool:
        child_key = _path_key(child_path)
        return bool(
            child_key
            and any(
                child_key == parent_key or child_key.startswith(parent_key + "/")
                for parent_key in (_path_key(item) for item in parent_write_set)
                if parent_key
            )
        )

    child_keys = {_path_key(item) for item in requested_child_write_set if _path_key(item)}
    parent_keys = {_path_key(item) for item in parent_write_set if _path_key(item)}
    explicit_subset = bool(
        child_keys
        and parent_keys
        and all(_covered_by_parent(item) for item in requested_child_write_set)
        and (
            child_keys != parent_keys
            or any(
                child_key != parent_key and child_key.startswith(parent_key + "/")
                for child_key in child_keys
                for parent_key in parent_keys
            )
        )
    )
    child["readOnly"] = not explicit_subset
    child["writeRequired"] = explicit_subset
    child["readSet"] = _unique_text(
        [
            *_values(parent_capsule.get("readSet")),
            *_values(parent_capsule.get("writeSet")),
            *_values(child.get("readSet")),
        ]
    )
    child["writeSet"] = requested_child_write_set if explicit_subset else []
    if not explicit_subset:
        child["behaviorScope"] = _unique_text(
            [
                *_values(child.get("behaviorScope")),
                *_values(child.get("constraints")),
                (
                    "Read-only verification must reuse in-memory ToolMessage results. "
                    "Do not create temporary evidence, stdout/stderr capture, log, or report files, "
                    "and do not use shell output redirection."
                ),
            ]
        )
    if not list(child.get("expectedOutputs") or []):
        child["expectedOutputs"] = ["Compact evidence handoff for the parent task"]
    if child.get("acceptanceContract") in (None, "", [], {}):
        parent_acceptance = deepcopy(parent_capsule.get("acceptance") or "")
        child["acceptanceContract"] = (
            f"Return a compact evidence handoff to the parent. Evaluate against parent acceptance: {parent_acceptance}"
            if parent_acceptance
            else "Return a compact evidence handoff to the parent."
        )
    requested_expected_artifacts = _unique_text(_values(child.get("expectedArtifacts")))
    expected_artifacts = (
        [item for item in requested_expected_artifacts if any(_path_key(item) == key or _path_key(item).startswith(key + "/") for key in child_keys)]
        or list(requested_child_write_set)
        if explicit_subset
        else []
    )
    child["expectedArtifacts"] = expected_artifacts
    child_context["artifactWriteDiscipline"] = (
        "This disposable grandchild may write only the explicit strict-subset partition in its writeSet. It receives no other parent write authority."
        if explicit_subset
        else (
            "This grandchild receives engineering facts and acceptance criteria but no inherited write authority. "
            "Return in-memory ToolMessage evidence to the parent; do not create temporary capture or report files."
        )
    )
    child["context"] = child_context
    child["engineeringTaskCapsule"] = {
        "schemaVersion": "v8.engineering_task_capsule.v1",
        "parentCapsuleId": parent_capsule.get("capsuleId"),
        "taskId": str(child.get("taskBriefId") or child.get("id") or "").strip(),
        "workspacePath": parent_capsule.get("workspacePath"),
        "shellDialect": shell_dialect or parent_capsule.get("shellDialect") or "",
        "executionMode": "write" if explicit_subset else "verify",
        "writeRequired": explicit_subset,
        "criticalFiles": list(parent_capsule.get("criticalFiles") or []),
        "readSet": list(child.get("readSet") or []),
        "writeSet": list(child.get("writeSet") or []),
        "expectedOutputs": list(child.get("expectedOutputs") or []),
        "expectedArtifacts": expected_artifacts,
        "verificationContract": deepcopy(parent_capsule.get("verificationContract") or []),
        "proofExpectations": list(parent_capsule.get("proofExpectations") or []),
        "riskFlags": [
            *list(parent_capsule.get("riskFlags") or []),
            "grandchild_explicit_write_subset" if explicit_subset else "grandchild_write_authority_not_inherited",
        ],
        "acceptance": deepcopy(child.get("acceptanceContract") or ""),
    }
    return ensure_engineering_task_capsule(child, shell_dialect=shell_dialect)


def engineering_tool_allowed(tool_name: str, task_brief: dict[str, Any] | None) -> bool:
    name = str(tool_name or "").strip()
    mode = engineering_capsule_mode(task_brief)
    if name in ENGINEERING_FILE_MUTATION_TOOL_NAMES:
        return mode == "write"
    if name in ENGINEERING_COMMAND_TOOL_NAMES:
        # ``read_only`` is a delivery contract, not a ban on executing tests or
        # diagnostics.  The command guard still rejects commands that may mutate
        # the checkout, while the managed worktree finalizer proves that a
        # verification branch produced no file changes.
        return mode in {"read_only", "verify", "write"}
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
    "bind_engineering_task_workspace",
    "ENGINEERING_COMMAND_TOOL_NAMES",
    "ENGINEERING_FILE_MUTATION_TOOL_NAMES",
    "derive_grandchild_engineering_task",
    "effective_engineering_capsule",
    "engineering_capsule_mode",
    "engineering_tool_allowed",
    "ensure_engineering_task_capsule",
    "task_brief_from_route_context",
]
