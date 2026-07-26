from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from core.database import db
from core.delegation_result_contract import parse_delegation_acceptance_text
from core.runtime_episodes import (
    ACTIVE_EPISODE_STATES,
    runtime_episode_parent_id,
    superseded_runtime_episode_ids,
)


RUNTIME_EXECUTION_HANDOFF_STATUSES = {"ready", "degraded"}
_PSEUDO_SIDE_EFFECT_TOOL_NAMES = {
    "write_native_file",
    "write_file",
    "run_system_command",
    "runtime_broker",
    "spec_broker",
    "creative_media_assets",
    "creative_media_jobs",
    "creative_media_edit",
}


def _pseudo_side_effect_tool_names(text: str) -> list[str]:
    """Detect textual tool markup that a provider failed to emit structurally.

    Keep the invariant narrow: ordinary prose mentioning a tool is allowed,
    while an XML/DSML-shaped invocation of a side-effect tool cannot be
    accepted as execution evidence.
    """

    normalized = str(text or "")
    if not re.search(r"<\s*tool_call\b", normalized, flags=re.IGNORECASE):
        return []
    names = {
        match.group(1).strip()
        for match in re.finditer(
            r"<\s*invoke\b[^>]*\bname\s*=\s*['\"]([^'\"]+)['\"]",
            normalized,
            flags=re.IGNORECASE,
        )
        if match.group(1).strip()
    }
    return sorted(name for name in names if name in _PSEUDO_SIDE_EFFECT_TOOL_NAMES)


@dataclass(frozen=True, slots=True)
class SupervisorCompletionDecision:
    action: str = "complete"
    reason: str = "eligible"
    details: dict[str, Any] = field(default_factory=dict)


def _is_optional_episode(episode: Mapping[str, Any]) -> bool:
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    metadata = episode.get("metadata") if isinstance(episode.get("metadata"), Mapping) else {}
    return any(
        bool(source.get("optional") or source.get("optionalLane") or source.get("degradedOk"))
        for source in (episode, inputs, metadata)
    ) or str(inputs.get("dependencyMode") or metadata.get("dependencyMode") or "").strip().lower() in {
        "optional",
        "degraded_ok",
    }


def _looks_forward_only(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized or len(normalized) > 700:
        return False
    forward_markers = (
        "开始",
        "接下来",
        "我将",
        "我会",
        "现在我重新",
        "现在让我",
        "准备启动",
        "准备开始",
        "正在启动",
        "starting",
        "i will",
        "next i",
    )
    result_markers = (
        "已完成",
        "完成并回流",
        "已回流",
        "交付",
        "结果",
        "证据",
        "来源",
        "限制",
        "缺少",
        "无法",
        "失败",
        "降级",
        "degraded",
        "completed",
        "ready",
    )
    return any(marker in normalized for marker in forward_markers) and not any(
        marker in normalized for marker in result_markers
    )


def _has_ready_runtime_handoff(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> bool:
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            continue
        for handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            status = str(handoff.get("status") or "").strip().lower()
            if status in RUNTIME_EXECUTION_HANDOFF_STATUSES:
                return True
    return False


def _required_runtime_degraded_handoffs(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    degraded: list[dict[str, Any]] = []
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            continue
        for handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            status = str(handoff.get("status") or "").strip().lower()
            if status == "degraded":
                degraded.append(
                    {
                        "episodeId": episode_id,
                        "handoffRefId": handoff.get("handoffRefId"),
                        "kind": handoff.get("kind"),
                        "status": status,
                    }
                )
    return degraded


def _delegation_acceptance_missing(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    final_text: str,
) -> list[str]:
    if parse_delegation_acceptance_text(final_text):
        return []
    pending: list[str] = []
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        if str(episode.get("parentEpisodeId") or episode.get("parent_episode_id") or "").strip():
            continue
        if str(episode.get("kind") or "").strip().lower() != "delegation":
            continue
        state = str(episode.get("state") or "").strip().lower()
        if state not in {"completed", "merged", "degraded"}:
            continue
        metadata = episode.get("metadata") if isinstance(episode.get("metadata"), Mapping) else {}
        acceptance = metadata.get("supervisorAcceptance") if isinstance(metadata.get("supervisorAcceptance"), Mapping) else {}
        acceptance_status = str(acceptance.get("status") or "").strip().lower()
        if acceptance_status in {"accepted", "retry", "ignored"}:
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            continue
        has_terminal_handoff = any(
            str(handoff.get("status") or "").strip().lower() in RUNTIME_EXECUTION_HANDOFF_STATUSES
            for handoff in list(handoffs_by_episode.get(episode_id, []) or [])
            if isinstance(handoff, Mapping)
        )
        if has_terminal_handoff:
            pending.append(episode_id)
    return pending


def _spec_tasks_need_proof(spec_brief: Mapping[str, Any]) -> list[dict[str, Any]]:
    traceability = spec_brief.get("traceability") if isinstance(spec_brief.get("traceability"), Mapping) else {}
    tasks = [dict(item) for item in list(traceability.get("tasks") or []) if isinstance(item, Mapping)]
    return [
        task
        for task in tasks
        if str(task.get("proofRequired") or task.get("independentAcceptance") or "").strip()
    ]


def _handoff_has_verifiable_proof(handoff: Mapping[str, Any]) -> bool:
    payload = handoff.get("payload") if isinstance(handoff.get("payload"), Mapping) else {}
    refs = handoff.get("refs") if isinstance(handoff.get("refs"), list) else payload.get("refs")
    if isinstance(refs, list) and any(str(item or "").strip() for item in refs):
        return True
    if str(handoff.get("raw_ref") or handoff.get("rawRef") or "").strip():
        return True
    if str(handoff.get("detail_tool") or handoff.get("detailTool") or "").strip():
        return True
    text = " ".join(
        str(value or "")
        for value in (
            handoff.get("compact_summary"),
            handoff.get("compactSummary"),
            handoff.get("summary"),
            payload.get("compactSummary"),
            payload.get("summary"),
            payload.get("proof"),
            payload.get("acceptance"),
            payload.get("verification"),
        )
    ).lower()
    return any(
        marker in text
        for marker in (
            "proof",
            "verified",
            "verification",
            "acceptance",
            "evidence",
            "artifact",
            "changed file",
            "touched file",
            "证明",
            "验收",
            "验证",
            "证据",
            "产物",
            "文件",
        )
    )


def _missing_spec_proof_handoffs(
    spec_brief: Mapping[str, Any],
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, Any] | None:
    required_tasks = _spec_tasks_need_proof(spec_brief)
    if not required_tasks:
        return None
    ready_handoffs: list[dict[str, Any]] = []
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            continue
        for handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            status = str(handoff.get("status") or "").strip().lower()
            if status in RUNTIME_EXECUTION_HANDOFF_STATUSES:
                ready_handoffs.append(dict(handoff))
    if any(_handoff_has_verifiable_proof(handoff) for handoff in ready_handoffs):
        return None
    return {
        "taskIds": [str(task.get("taskId") or "") for task in required_tasks[:8] if str(task.get("taskId") or "").strip()],
        "handoffCount": len(ready_handoffs),
        "message": "Approved Spec execution returned runtime handoff(s), but no verifiable proof/acceptance refs were found.",
    }


def _episode_task_briefs(episode: Mapping[str, Any]) -> list[dict[str, Any]]:
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    raw = inputs.get("workerBriefs") or inputs.get("taskBriefs") or inputs.get("tasks") or []
    return [dict(item) for item in list(raw or []) if isinstance(item, Mapping)]


def _brief_requires_write(brief: Mapping[str, Any], *, episode_kind: str) -> bool:
    if bool(brief.get("readOnly") or brief.get("read_only")):
        return False
    capsule = brief.get("engineeringTaskCapsule") if isinstance(brief.get("engineeringTaskCapsule"), Mapping) else {}
    capsule_mode = str(capsule.get("executionMode") or capsule.get("execution_mode") or "").strip().lower()
    if capsule_mode in {"read_only", "verify", "plan_only"}:
        return False
    if bool(brief.get("writeRequired") or brief.get("write_required")):
        return True
    if list(brief.get("writeSet") or brief.get("write_set") or []):
        return True
    capabilities = " ".join(str(item or "") for item in list(brief.get("requiredCapabilities") or [])).lower()
    tool_policy = brief.get("toolPolicy") if isinstance(brief.get("toolPolicy"), Mapping) else {}
    allowed_tools = {
        str(item or "").strip()
        for item in list(tool_policy.get("allowedTools") or brief.get("allowedTools") or [])
        if str(item or "").strip()
    }
    return episode_kind == "engineering" or "write_native_file" in allowed_tools or any(
        marker in capabilities for marker in ("workspace_mutation", "file_write", "implementation")
    )


def _required_write_episode(episode: Mapping[str, Any]) -> bool:
    if _is_optional_episode(episode):
        return False
    kind = str(episode.get("kind") or "").strip().lower()
    briefs = _episode_task_briefs(episode)
    if briefs:
        return any(_brief_requires_write(brief, episode_kind=kind) for brief in briefs)
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    if bool(inputs.get("readOnly") or inputs.get("read_only")):
        return False
    return bool(inputs.get("writeRequired") or inputs.get("write_required") or kind == "engineering")


def _handoff_payload(handoff: Mapping[str, Any]) -> dict[str, Any]:
    payload = handoff.get("payload") if isinstance(handoff.get("payload"), Mapping) else {}
    return {**dict(handoff), **dict(payload)}


def _collect_named_values(value: Any, keys: set[str], *, limit: int = 64) -> list[Any]:
    collected: list[Any] = []

    def _walk(item: Any) -> None:
        if len(collected) >= limit:
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key) in keys:
                    values = child if isinstance(child, list) else [child]
                    for candidate in values:
                        if candidate not in (None, "") and candidate not in collected:
                            collected.append(candidate)
                            if len(collected) >= limit:
                                return
                if isinstance(child, (Mapping, list, tuple)):
                    _walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                _walk(child)

    _walk(value)
    return collected


def _ref_text(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("path", "filePath", "file_path", "sourcePath", "workspaceRelativePath", "uri", "url", "ref"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _looks_like_file_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith(("artifact://", "workspace://")):
        return True
    if text.startswith("file://"):
        return True
    return bool(re.search(r"(?:^|[\\/])[^\\/]+\.[A-Za-z0-9]{1,12}$", text) or re.search(r"^[^\\/]+\.[A-Za-z0-9]{1,12}$", text))


def _existing_file_evidence(episode: Mapping[str, Any], handoffs: Iterable[Mapping[str, Any]]) -> list[str]:
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    engineering_workspace = (
        inputs.get("engineeringWorkspace")
        if isinstance(inputs.get("engineeringWorkspace"), Mapping)
        else {}
    )
    workspace_paths: list[Path] = []
    for workspace in (
        inputs.get("originalWorkspacePath"),
        inputs.get("original_workspace_path"),
        engineering_workspace.get("originalWorkspacePath"),
        engineering_workspace.get("original_workspace_path"),
        inputs.get("workspacePath"),
        inputs.get("workspace_path"),
    ):
        text = str(workspace or "").strip()
        if not text:
            continue
        try:
            candidate = Path(text).resolve()
        except Exception:
            continue
        if candidate not in workspace_paths:
            workspace_paths.append(candidate)
    values: list[Any] = []
    keys = {
        "artifactRefs",
        "artifacts",
        "changedPaths",
        "changed_paths",
        "changedFiles",
        "changed_files",
        "touchedFiles",
        "touched_files",
        "writtenFiles",
        "written_files",
        "outputFiles",
        "output_files",
    }
    for handoff in handoffs:
        values.extend(_collect_named_values(_handoff_payload(handoff), keys))
    evidence: list[str] = []
    episode_session_id = str(episode.get("sessionId") or episode.get("session_id") or "").strip()
    episode_run_id = str(episode.get("runId") or episode.get("run_id") or "").strip()
    for value in values:
        text = _ref_text(value)
        if not text or not _looks_like_file_path(text):
            continue
        if text.startswith("artifact://"):
            artifact_id = text[len("artifact://") :].strip("/\\")
            artifact = db.get_runtime_artifact(artifact_id) if artifact_id else None
            if not artifact:
                continue
            artifact_session_id = str(artifact.get("sessionId") or artifact.get("session_id") or "").strip()
            artifact_run_id = str(artifact.get("runId") or artifact.get("run_id") or "").strip()
            if episode_session_id and artifact_session_id and artifact_session_id != episode_session_id:
                continue
            if episode_run_id and artifact_run_id and artifact_run_id != episode_run_id:
                continue
            source_path = str(artifact.get("sourcePath") or artifact.get("source_path") or "").strip()
            if source_path:
                try:
                    candidate = Path(source_path)
                    if candidate.exists() and candidate.is_file():
                        evidence.append(str(candidate.resolve()))
                except Exception:
                    pass
            continue
        candidate_text = text
        if text.startswith("workspace://"):
            if not workspace_paths:
                continue
            candidate_text = text[len("workspace://") :].lstrip("/\\")
        elif text.startswith("file://"):
            candidate_text = text[7:]
        try:
            candidate = Path(candidate_text)
            candidates = [candidate] if candidate.is_absolute() else [root / candidate for root in workspace_paths]
            for resolved_candidate in candidates:
                if resolved_candidate.exists() and resolved_candidate.is_file():
                    evidence.append(str(resolved_candidate.resolve()))
                    break
        except Exception:
            continue
    return list(dict.fromkeys(evidence))[:32]


def _typed_creative_artifact_requirements(
    episode: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Return typed artifact obligations and whether workspace-file proof remains required.

    Creative Media artifacts and workspace files are different delivery planes.
    Only a typed Creative contract with ``output.kind=artifact`` may opt a
    write-required brief into governed artifact proof; prose such as
    ``expectedOutputs`` is deliberately not classified here.
    """

    if str(episode.get("kind") or "").strip().lower() != "creative_media":
        return [], True
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    requirements: list[dict[str, Any]] = []
    artifact_brief_keys: set[str] = set()

    def _append_contract(value: Any, *, brief_key: str = "") -> bool:
        if not isinstance(value, Mapping):
            return False
        contract = dict(value)
        schema = str(contract.get("schema") or "").strip()
        if schema not in {"v8.creative_canvas_task.v1", "v8.creative_media_execution.v1"}:
            return False
        output = contract.get("output") if isinstance(contract.get("output"), Mapping) else {}
        if str(output.get("kind") or "").strip().lower() != "artifact":
            return False
        execution = contract.get("execution") if isinstance(contract.get("execution"), Mapping) else {}
        arguments = execution.get("arguments") if isinstance(execution.get("arguments"), Mapping) else {}
        request = arguments.get("request") if isinstance(arguments.get("request"), Mapping) else {}
        if (
            str(execution.get("tool") or "").strip() != "creative_media_jobs"
            or str(arguments.get("action") or "").strip() != "create"
            or not str(request.get("modality") or "").strip()
            or not str(request.get("operationKind") or "").strip()
        ):
            return False
        requirements.append(
            {
                "taskBriefKey": brief_key,
                "request": dict(request),
                "outputKind": "artifact",
                "outputSlot": str(output.get("slot") or "").strip(),
            }
        )
        if brief_key:
            artifact_brief_keys.add(brief_key)
        return True

    for key in ("creativeMediaExecutionContract", "creative_media_execution_contract"):
        if key in inputs:
            _append_contract(inputs.get(key))

    briefs = _episode_task_briefs(episode)
    write_brief_keys: list[str] = []
    for index, brief in enumerate(briefs):
        brief_key = str(brief.get("taskBriefId") or f"brief:{index}").strip()
        if _brief_requires_write(brief, episode_kind="creative_media"):
            write_brief_keys.append(brief_key)
        context = brief.get("context") if isinstance(brief.get("context"), Mapping) else {}
        for key in (
            "creativeMediaExecutionContract",
            "creative_media_execution_contract",
            "canvasExecutionContract",
            "canvas_execution_contract",
        ):
            if key in context:
                _append_contract(context.get(key), brief_key=brief_key)
                break

    if write_brief_keys:
        requires_workspace_file = any(key not in artifact_brief_keys for key in write_brief_keys)
    else:
        requires_workspace_file = not bool(requirements)
    return requirements, requires_workspace_file


def _same_resolved_path(left: str, right: str) -> bool:
    if not str(left or "").strip() or not str(right or "").strip():
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except Exception:
        return False


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _creative_artifact_id(value: Any) -> str:
    if isinstance(value, Mapping):
        text = str(value.get("artifactId") or value.get("artifact_id") or value.get("id") or "").strip()
    else:
        text = str(value or "").strip()
    if text.startswith("artifact://"):
        text = text[len("artifact://") :].strip("/\\")
    return text if re.fullmatch(r"art_[A-Za-z0-9_-]+", text) else ""


def _creative_artifact_evidence(
    episode: Mapping[str, Any],
    handoffs: Iterable[Mapping[str, Any]],
    requirements: list[dict[str, Any]],
) -> tuple[list[str], str | None]:
    """Validate governed Creative artifacts without weakening workspace-file gates."""

    artifact_ids: list[str] = []
    job_proof_ids: set[str] = set()
    for handoff in handoffs:
        payload = _handoff_payload(handoff)
        for value in _collect_named_values(payload, {"artifactRefs", "artifact_refs"}):
            artifact_id = _creative_artifact_id(value)
            if artifact_id and artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
        for value in _collect_named_values(payload, {"proofRefs", "proof_refs", "jobRefs", "job_refs"}):
            text = _ref_text(value) or str(value or "").strip()
            if text.startswith("creative-media-job://"):
                job_id = text[len("creative-media-job://") :].strip("/\\")
                if job_id:
                    job_proof_ids.add(job_id)
    if not artifact_ids:
        return [], "required_creative_artifact_missing"

    episode_session_id = str(episode.get("sessionId") or episode.get("session_id") or "").strip()
    episode_run_id = str(episode.get("runId") or episode.get("run_id") or "").strip()
    if not episode_session_id or not episode_run_id:
        return [], "creative_artifact_lineage_mismatch"
    binding = db.get_session_scope_binding(episode_session_id)
    if not isinstance(binding, Mapping):
        return [], "creative_artifact_lineage_mismatch"
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    expected_workspace_id = str(
        inputs.get("workspaceId")
        or inputs.get("workspace_id")
        or episode.get("workspaceId")
        or episode.get("workspace_id")
        or binding.get("workspace_id")
        or binding.get("workspaceId")
        or ""
    ).strip()
    expected_project_id = str(
        inputs.get("projectId")
        or inputs.get("project_id")
        or episode.get("projectId")
        or episode.get("project_id")
        or binding.get("project_id")
        or binding.get("projectId")
        or ""
    ).strip()
    expected_workspace_path = str(
        inputs.get("workspacePath")
        or inputs.get("workspace_path")
        or binding.get("workspace_path")
        or binding.get("workspacePath")
        or ""
    ).strip()
    bound_workspace_id = str(binding.get("workspace_id") or binding.get("workspaceId") or "").strip()
    bound_project_id = str(binding.get("project_id") or binding.get("projectId") or "").strip()
    bound_workspace_path = str(binding.get("workspace_path") or binding.get("workspacePath") or "").strip()
    if (
        not expected_workspace_id
        or not expected_project_id
        or not expected_workspace_path
        or expected_workspace_id != bound_workspace_id
        or expected_project_id != bound_project_id
        or not _same_resolved_path(expected_workspace_path, bound_workspace_path)
    ):
        return [], "creative_artifact_lineage_mismatch"

    def _source_matches_scope(source_id: str) -> bool:
        source = db.get_session_source(session_id=episode_session_id, source_id=source_id)
        if not isinstance(source, Mapping):
            return False
        resource_ref = source.get("resourceRef") if isinstance(source.get("resourceRef"), Mapping) else {}
        metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
        source_binding = metadata.get("workspaceBinding") if isinstance(metadata.get("workspaceBinding"), Mapping) else {}
        source_workspace_id = str(resource_ref.get("workspaceId") or source_binding.get("workspaceId") or "").strip()
        source_project_id = str(resource_ref.get("projectId") or source_binding.get("projectId") or "").strip()
        source_workspace_root = str(
            resource_ref.get("workspaceRoot")
            or source_binding.get("activeWorkspaceRoot")
            or source_binding.get("authorityWorkspaceRoot")
            or ""
        ).strip()
        return bool(
            source_workspace_id == expected_workspace_id
            and source_project_id == expected_project_id
            and source_workspace_root
            and _same_resolved_path(source_workspace_root, expected_workspace_path)
        )

    for requirement in requirements:
        request = requirement.get("request") if isinstance(requirement.get("request"), Mapping) else {}
        source_ids = [
            str(item).strip()
            for item in [
                request.get("sourceId"),
                *(list(request.get("sourceIds") or []) if isinstance(request.get("sourceIds"), list) else []),
                request.get("maskSourceId"),
            ]
            if str(item or "").strip()
        ]
        if any(not _source_matches_scope(source_id) for source_id in dict.fromkeys(source_ids)):
            return [], "creative_artifact_lineage_mismatch"

    matched_requirements: set[int] = set()
    evidence: list[str] = []
    for artifact_id in artifact_ids:
        artifact = db.get_runtime_artifact(artifact_id)
        if not isinstance(artifact, Mapping):
            return [], "required_creative_artifact_missing"
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), Mapping) else {}
        if str(artifact.get("resourceRole") or artifact.get("resource_role") or "artifact").strip() != "artifact":
            return [], "creative_artifact_lineage_mismatch"
        if (
            str(artifact.get("sessionId") or artifact.get("session_id") or "").strip() != episode_session_id
            or str(artifact.get("runId") or artifact.get("run_id") or "").strip() != episode_run_id
            or str(metadata.get("storageClass") or metadata.get("storage_class") or "").strip() != "runtime_artifact"
            or str(metadata.get("workspaceId") or metadata.get("workspace_id") or "").strip() != expected_workspace_id
            or str(metadata.get("projectId") or metadata.get("project_id") or "").strip() != expected_project_id
            or not _same_resolved_path(
                str(metadata.get("workspacePath") or metadata.get("workspace_path") or "").strip(),
                expected_workspace_path,
            )
        ):
            return [], "creative_artifact_lineage_mismatch"
        source_path = str(artifact.get("sourcePath") or artifact.get("source_path") or "").strip()
        try:
            resolved_source_path = Path(source_path).resolve()
        except Exception:
            return [], "required_creative_artifact_missing"
        if (
            not source_path
            or not resolved_source_path.exists()
            or not resolved_source_path.is_file()
            or not _path_is_within(resolved_source_path, Path(expected_workspace_path))
        ):
            return [], "required_creative_artifact_missing"
        job_id = str(metadata.get("creativeMediaJobId") or metadata.get("creative_media_job_id") or "").strip()
        if not job_id or job_id not in job_proof_ids:
            return [], "creative_artifact_proof_missing"

        for index, requirement in enumerate(requirements):
            request = requirement.get("request") if isinstance(requirement.get("request"), Mapping) else {}
            lineage_keys = ("modality", "operationKind", "canvasOperationId", "sourceId", "maskSourceId")
            output_kind_matches = str(metadata.get("outputKind") or metadata.get("output_kind") or "").strip() == str(
                requirement.get("outputKind") or ""
            ).strip()
            expected_output_slot = str(requirement.get("outputSlot") or "").strip()
            output_slot_matches = bool(expected_output_slot) and str(
                metadata.get("outputSlot") or metadata.get("output_slot") or ""
            ).strip() == expected_output_slot
            if output_kind_matches and output_slot_matches and all(
                not str(request.get(key) or "").strip()
                or str(metadata.get(key) or "").strip() == str(request.get(key) or "").strip()
                for key in lineage_keys
            ):
                matched_requirements.add(index)
        evidence.append(str(resolved_source_path))

    if len(matched_requirements) != len(requirements):
        return [], "creative_artifact_lineage_mismatch"
    return list(dict.fromkeys(evidence))[:32], None


def _handoff_proof_evidence(handoffs: Iterable[Mapping[str, Any]]) -> list[str]:
    keys = {
        "proofRefs",
        "proof_refs",
        "verificationRefs",
        "verification_refs",
        "evidenceRefs",
        "evidence_refs",
        "verificationResults",
        "verification_results",
    }
    evidence: list[str] = []
    for handoff in handoffs:
        payload = _handoff_payload(handoff)
        for value in _collect_named_values(payload, keys):
            text = _ref_text(value) or str(value or "").strip()
            if text:
                evidence.append(text[:800])
        for verification in _collect_named_values(
            payload,
            {"verification", "verificationResult", "verificationResults", "verification_result", "verification_results"},
        ):
            if not isinstance(verification, Mapping) or not verification:
                continue
            status = str(verification.get("status") or verification.get("state") or "").strip().lower()
            passed = verification.get("passed")
            if passed is True or status in {"passed", "verified", "success", "completed"}:
                evidence.append(f"verification:{status or 'passed'}")
        for acceptance in _collect_named_values(payload, {"acceptanceCheck", "acceptance_check"}):
            if not isinstance(acceptance, Mapping):
                continue
            must = acceptance.get("must") if isinstance(acceptance.get("must"), Mapping) else {}
            if must.get("passed") is True:
                evidence.append("acceptance:must_passed")
    return list(dict.fromkeys(evidence))[:32]


def _non_spec_write_delivery_failure(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, Any] | None:
    for episode in episodes:
        if str(episode.get("parentEpisodeId") or episode.get("parent_episode_id") or "").strip():
            # Child delegation episodes are implementation details of the owning
            # runtime episode. Their artifacts and proof are merged into the
            # parent's typed handoff; evaluating them again can reject a valid
            # parent delivery merely because the child handoff is intentionally
            # compact.
            continue
        if not _required_write_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        state = str(episode.get("state") or "").strip().lower()
        handoffs = [dict(item) for item in list(handoffs_by_episode.get(episode_id, []) or []) if isinstance(item, Mapping)]
        statuses = {
            str(_handoff_payload(item).get("status") or "").strip().lower()
            for item in handoffs
        }
        if state == "degraded" or statuses.intersection({"degraded", "failed", "blocked", "error"}):
            return {
                "episodeId": episode_id,
                "reason": "required_write_runtime_degraded",
                "state": state,
                "handoffStatuses": sorted(status for status in statuses if status),
                "recoverable": True,
            }
        creative_requirements, requires_workspace_file = _typed_creative_artifact_requirements(episode)
        delivery_evidence: list[str] = []
        if creative_requirements:
            creative_evidence, creative_failure = _creative_artifact_evidence(
                episode,
                handoffs,
                creative_requirements,
            )
            if creative_failure:
                return {
                    "episodeId": episode_id,
                    "reason": creative_failure,
                    "state": state,
                    "recoverable": True,
                }
            delivery_evidence.extend(creative_evidence)
        if requires_workspace_file:
            file_evidence = _existing_file_evidence(episode, handoffs)
            if not file_evidence:
                return {
                    "episodeId": episode_id,
                    "reason": "required_write_files_missing",
                    "state": state,
                    "recoverable": True,
                }
            delivery_evidence.extend(file_evidence)
        proof_evidence = _handoff_proof_evidence(handoffs)
        if not proof_evidence:
            return {
                "episodeId": episode_id,
                "reason": "required_write_proof_missing",
                "state": state,
                "deliveryEvidence": delivery_evidence[:8],
                "recoverable": True,
            }
    return None


def _unresolved_research_evidence_gaps(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Return latest unresolved Research brief truth across bounded retries."""

    latest: dict[str, tuple[str, int, dict[str, Any]]] = {}
    sequence = 0
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        if str(episode.get("kind") or "").strip().lower() != "research":
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        for raw_handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            if not isinstance(raw_handoff, Mapping):
                continue
            sequence += 1
            handoff = _handoff_payload(raw_handoff)
            kind = str(handoff.get("kind") or "").strip().lower()
            if "research" not in kind:
                continue
            timestamp = str(
                handoff.get("createdAt")
                or raw_handoff.get("created_at")
                or episode.get("updatedAt")
                or episode.get("updated_at")
                or episode.get("createdAt")
                or episode.get("created_at")
                or ""
            )
            results = [dict(item) for item in list(handoff.get("taskBriefResults") or []) if isinstance(item, Mapping)]
            covered_ids = [str(item).strip() for item in list(handoff.get("coveredTaskBriefIds") or []) if str(item).strip()]
            missing_ids = [str(item).strip() for item in list(handoff.get("missingTaskBriefIds") or []) if str(item).strip()]

            def _record(brief_id: str, *, status: str, reasons: list[str] | None = None) -> None:
                if not brief_id:
                    return
                record = {
                    "episodeId": episode_id,
                    "handoffRefId": handoff.get("handoffRefId") or handoff.get("handoffId"),
                    "taskBriefId": brief_id,
                    "status": status,
                    "evidenceStatusReasons": list(reasons or [])[:8],
                }
                previous = latest.get(brief_id)
                key = (timestamp, sequence)
                if previous is None or key >= (previous[0], previous[1]):
                    latest[brief_id] = (timestamp, sequence, record)

            for result in results:
                brief_id = str(result.get("taskBriefId") or result.get("taskId") or "").strip()
                status = str(result.get("status") or "degraded").strip().lower()
                _record(
                    brief_id,
                    status=status,
                    reasons=[str(item) for item in list(result.get("evidenceStatusReasons") or []) if str(item).strip()],
                )
            for brief_id in covered_ids:
                _record(brief_id, status="ready")
            for brief_id in missing_ids:
                _record(brief_id, status="degraded", reasons=["missing_task_brief_evidence"])
            if str(handoff.get("status") or "").strip().lower() == "degraded" and not (results or covered_ids or missing_ids):
                fallback_ids = [
                    str(item).strip()
                    for item in list(handoff.get("taskBriefIds") or [])
                    if str(item).strip()
                ] or [f"research:{episode_id or 'unknown'}"]
                for brief_id in fallback_ids:
                    _record(brief_id, status="degraded", reasons=["research_handoff_degraded"])

    return [
        record
        for _timestamp, _sequence, record in latest.values()
        if str(record.get("status") or "").strip().lower() not in {"ready", "completed", "success", "ok"}
    ]


def _completed_downstream_carrying_research_gaps(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
    research_gaps: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return governed downstream evidence that explicitly carried gaps.

    Missing external evidence remains a claim blocker.  It may stop being a
    whole-run blocker only after a downstream runtime received the exact gap
    IDs and returned a ready handoff with its own local proof contract.
    """

    missing_ids = {
        str(item.get("taskBriefId") or "").strip()
        for item in research_gaps
        if str(item.get("taskBriefId") or "").strip()
    }
    if not missing_ids:
        return None
    downstream_kinds = {"engineering", "creative_media", "computer_use", "rpa", "delegation"}
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        kind = str(episode.get("kind") or "").strip().lower()
        if kind not in downstream_kinds:
            continue
        inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
        context = inputs.get("researchContext") if isinstance(inputs.get("researchContext"), Mapping) else {}
        carried_gaps = {
            str(item.get("taskBriefId") or item.get("taskId") or "").strip()
            for item in list(context.get("evidenceGaps") or [])
            if isinstance(item, Mapping)
            and str(item.get("taskBriefId") or item.get("taskId") or "").strip()
        }
        if not missing_ids.issubset(carried_gaps) or not bool(context.get("downstreamAllowed")):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        ready_handoffs: list[dict[str, Any]] = []
        for raw_handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            if not isinstance(raw_handoff, Mapping):
                continue
            handoff = _handoff_payload(raw_handoff)
            if str(handoff.get("status") or "").strip().lower() not in {"ready", "completed", "success", "ok"}:
                continue
            ready_handoffs.append(
                {
                    "handoffRefId": handoff.get("handoffRefId") or handoff.get("handoffId"),
                    "proofRefs": list(handoff.get("proofRefs") or handoff.get("verificationRefs") or [])[:8],
                    "artifactRefs": list(handoff.get("artifactRefs") or handoff.get("refs") or [])[:8],
                }
            )
        if ready_handoffs:
            return {
                "episodeId": episode_id,
                "kind": kind,
                "carriedTaskBriefIds": sorted(carried_gaps),
                "handoffs": ready_handoffs[:4],
            }
    return None


def evaluate_supervisor_completion(
    *,
    episodes: Iterable[Mapping[str, Any]] = (),
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    final_text: str = "",
    spec_mode: bool = False,
    spec_brief: Mapping[str, Any] | None = None,
    spec_has_pending_approval: bool | None = None,
) -> SupervisorCompletionDecision:
    normalized_episodes = [dict(item) for item in episodes if isinstance(item, Mapping)]
    normalized_handoffs = {
        str(episode_id): [dict(item) for item in items if isinstance(item, Mapping)]
        for episode_id, items in dict(handoffs_by_episode or {}).items()
    }

    active = [
        str(item.get("episodeId") or item.get("id") or "")
        for item in normalized_episodes
        if str(item.get("state") or "").strip().lower() in ACTIVE_EPISODE_STATES
    ]
    if active:
        return SupervisorCompletionDecision(
            action="waiting_runtime",
            reason="runtime_episode_active_at_stream_end",
            details={"episodeIds": active[:12]},
        )

    pseudo_tools = _pseudo_side_effect_tool_names(final_text)
    if pseudo_tools:
        return SupervisorCompletionDecision(
            action="fail",
            reason="supervisor_pseudo_tool_markup_not_executed",
            details={
                "toolNames": pseudo_tools,
                "nextAction": "retry_with_native_structured_tool_calls_or_report_blocker",
            },
        )

    superseded_ids = superseded_runtime_episode_ids(normalized_episodes, normalized_handoffs)
    effective_episodes = [
        episode
        for episode in normalized_episodes
        if not runtime_episode_parent_id(episode)
        and str(episode.get("episodeId") or episode.get("id") or episode.get("needId") or "").strip()
        not in superseded_ids
    ]

    for episode in effective_episodes:
        if _is_optional_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "")
        state = str(episode.get("state") or "").strip().lower()
        handoffs = normalized_handoffs.get(episode_id, [])
        if state in {"failed", "cancelled"} and not any(
            str(_handoff_payload(item).get("status") or "").strip().lower() in {"ready", "degraded"}
            for item in handoffs
        ):
            return SupervisorCompletionDecision(
                action="fail",
                reason="required_runtime_episode_failed_without_handoff",
                details={"episodeId": episode_id, "state": state},
            )
        for raw_handoff in handoffs:
            handoff = _handoff_payload(raw_handoff)
            status = str(handoff.get("status") or "").strip().lower()
            kind = str(handoff.get("kind") or "").strip().lower()
            run_mode = str(handoff.get("runMode") or "").strip().lower()
            if kind == "research_evidence_bundle" and status == "ready" and run_mode == "plan":
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="research_plan_only_claimed_evidence_ready",
                    details={"episodeId": episode_id, "handoffRefId": handoff.get("handoffRefId")},
                )
            if status in {"failed", "blocked"}:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="required_runtime_handoff_failed",
                    details={
                        "episodeId": episode_id,
                        "handoffRefId": handoff.get("handoffRefId"),
                        "status": status,
                    },
                )

    research_gaps = _unresolved_research_evidence_gaps(effective_episodes, normalized_handoffs)
    research_gap_continuation = None
    if research_gaps:
        research_gap_continuation = _completed_downstream_carrying_research_gaps(
            effective_episodes,
            normalized_handoffs,
            research_gaps,
        )
        if research_gap_continuation is None:
            return SupervisorCompletionDecision(
                action="fail",
                reason="research_brief_evidence_incomplete",
                details={
                    "missingTaskBriefIds": [str(item.get("taskBriefId") or "") for item in research_gaps[:12]],
                    "gaps": research_gaps[:12],
                    "nextAction": "retry_missing_research_briefs_once_or_continue_with_explicit_gaps",
                },
            )

    missing_delegation_acceptance = _delegation_acceptance_missing(
        effective_episodes,
        normalized_handoffs,
        final_text=final_text,
    )
    if missing_delegation_acceptance:
        return SupervisorCompletionDecision(
            action="fail",
            reason="delegation_supervisor_acceptance_missing",
            details={
                "episodeIds": missing_delegation_acceptance[:12],
                "nextAction": "record_accept_retry_or_ignore",
            },
        )

    if not spec_mode:
        write_delivery_failure = _non_spec_write_delivery_failure(effective_episodes, normalized_handoffs)
        if write_delivery_failure:
            return SupervisorCompletionDecision(
                action="fail",
                reason=str(write_delivery_failure.get("reason") or "required_write_delivery_incomplete"),
                details={
                    **write_delivery_failure,
                    "nextAction": "repair_or_retry_required_write_episode",
                },
            )

    if spec_mode:
        brief = dict(spec_brief or {})
        if not str(brief.get("specId") or "").strip() or str(brief.get("status") or "").strip().lower() in {
            "missing",
            "error",
        }:
            return SupervisorCompletionDecision(action="fail", reason="spec_stage_not_created")
        pipeline = brief.get("pipelineControl") if isinstance(brief.get("pipelineControl"), Mapping) else {}
        blocked_reason = str(pipeline.get("blockedReason") or "").strip()
        blocked_stage = str(pipeline.get("blockedByApproval") or "").strip()
        if blocked_reason in {"stage_format_invalid", "stage_analysis_invalid", "stage_contract_invalid"}:
            return SupervisorCompletionDecision(
                action="fail",
                reason=f"spec_{blocked_reason}",
                details={
                    "specId": brief.get("specId"),
                    "currentStage": brief.get("currentStage"),
                    "blockedReason": blocked_reason,
                },
            )
        if blocked_stage or blocked_reason == "approval_required":
            if spec_has_pending_approval is False:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="spec_stage_blocked_without_pending_approval",
                    details={
                        "specId": brief.get("specId"),
                        "currentStage": brief.get("currentStage"),
                        "blockedByApproval": blocked_stage,
                    },
                )
            return SupervisorCompletionDecision(
                action="waiting_approval",
                reason=blocked_reason or "approval_required",
                details={
                    "specId": brief.get("specId"),
                    "currentStage": brief.get("currentStage"),
                    "blockedByApproval": blocked_stage,
                },
            )
        if bool(pipeline.get("runtimeExecutionAllowed")) and not _has_ready_runtime_handoff(
            effective_episodes,
            normalized_handoffs,
        ):
            if not effective_episodes:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="spec_runtime_execution_episode_missing",
                    details={
                        "specId": brief.get("specId"),
                        "currentStage": brief.get("currentStage"),
                        "episodeCount": 0,
                    },
                )
            return SupervisorCompletionDecision(
                action="waiting_runtime",
                reason="spec_runtime_execution_handoff_pending",
                details={
                    "specId": brief.get("specId"),
                    "currentStage": brief.get("currentStage"),
                    "episodeCount": len(effective_episodes),
                },
            )
        if bool(pipeline.get("runtimeExecutionAllowed")):
            degraded_handoffs = _required_runtime_degraded_handoffs(effective_episodes, normalized_handoffs)
            if degraded_handoffs:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="spec_runtime_execution_degraded",
                    details={
                        "specId": brief.get("specId"),
                        "currentStage": brief.get("currentStage"),
                        "handoffs": degraded_handoffs[:8],
                    },
                )
            missing_proof = _missing_spec_proof_handoffs(brief, effective_episodes, normalized_handoffs)
            if missing_proof:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="spec_runtime_execution_proof_missing",
                    details={
                        "specId": brief.get("specId"),
                        "currentStage": brief.get("currentStage"),
                        **missing_proof,
                    },
                )
        # A fast client-side approval can be applied before the turn that wrote
        # the previous stage reaches finalization. In that race window the
        # pipeline legitimately has `nextStage=design|tasks` and no approval
        # block yet; the command router will schedule the continuation run.
        # Treating this as a failure poisons the run with a false terminal
        # status while the continuation is already in flight.

    if research_gap_continuation is None and effective_episodes and _looks_forward_only(final_text):
        return SupervisorCompletionDecision(
            action="complete",
            reason="forward_only_supervisor_advisory",
            details={
                "severity": "advisory",
                "finalTextPreview": str(final_text or "").strip()[:240],
                "message": "Supervisor ended with forward-looking wording; review delivery completeness without overriding its decision.",
            },
        )

    if research_gap_continuation is not None:
        return SupervisorCompletionDecision(
            action="complete",
            reason="research_gaps_carried_to_verified_downstream",
            details={
                "severity": "advisory",
                "missingTaskBriefIds": [str(item.get("taskBriefId") or "") for item in research_gaps[:12]],
                "gaps": research_gaps[:12],
                "downstream": research_gap_continuation,
                "message": (
                    "Unverified Research claims remain omitted, while a downstream runtime carried the exact gap IDs "
                    "and returned governed local delivery evidence."
                ),
            },
        )

    return SupervisorCompletionDecision()


__all__ = ["SupervisorCompletionDecision", "evaluate_supervisor_completion"]
