from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any, Iterable, Mapping

from erc.runtime_context import get_runtime_context
from core.time_truth import utc_now_iso
from core.database import db


ACTIVE_EPISODE_STATES = {
    "detected",
    "routed",
    "queued",
    "leased",
    "active",
    "waiting",
    "waiting_dependency",
    "waiting_child",
    "waiting_external",
    "waiting_approval",
    "waiting_input",
}
TERMINAL_EPISODE_STATES = {"completed", "degraded", "failed", "merged", "cancelled"}

TYPED_HANDOFF_KINDS = {
    "research": "research_evidence_bundle",
    "engineering": "engineering_patch_bundle",
    "creative_media": "asset_bundle",
    "computer_use": "computer_observation_bundle",
    "rpa": "rpa_trace_bundle",
    "delegation": "subagent_result_bundle",
    "verification": "verification_report",
}


def runtime_episode_parent_id(episode: Mapping[str, Any]) -> str:
    return str(episode.get("parentEpisodeId") or episode.get("parent_episode_id") or "").strip()


def _runtime_episode_raw_write_set(episode: Mapping[str, Any]) -> list[str]:
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    briefs = inputs.get("workerBriefs") or inputs.get("taskBriefs") or inputs.get("tasks") or []
    raw_input_write_set = inputs.get("writeSet") or inputs.get("write_set") or []
    values: list[Any] = (
        list(raw_input_write_set)
        if isinstance(raw_input_write_set, (list, tuple, set))
        else [raw_input_write_set]
    )
    for brief in list(briefs or []):
        if not isinstance(brief, Mapping):
            continue
        values.extend(list(brief.get("writeSet") or brief.get("write_set") or []))
        capsule = brief.get("engineeringTaskCapsule") if isinstance(brief.get("engineeringTaskCapsule"), Mapping) else {}
        values.extend(list(capsule.get("writeSet") or capsule.get("write_set") or []))

    return [str(value or "").strip().strip("`'\"").replace("\\", "/") for value in values if str(value or "").strip()]


def runtime_episode_write_set(episode: Mapping[str, Any]) -> list[str]:
    values = _runtime_episode_raw_write_set(episode)

    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip().strip("`'\"").replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        text = "/".join(part for part in text.split("/") if part).casefold()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _runtime_episode_has_repairable_write_contract(
    episode: Mapping[str, Any],
    *,
    replacement_write_set: set[str],
) -> bool:
    """Return true when a later precise contract can replace this malformed one.

    Directory placeholders and managed-worktree absolute paths are contract
    defects, not durable write obligations.  Explicit relative files remain
    obligations and must still be present in the replacement contract.
    """

    has_repairable_entry = False
    explicit_relative_files: set[str] = set()
    for raw_value in _runtime_episode_raw_write_set(episode):
        value = raw_value.strip()
        is_absolute = value.startswith("/") or (
            len(value) >= 3 and value[1] == ":" and value[2] == "/"
        )
        is_directory_placeholder = value.endswith("/")
        if is_absolute or is_directory_placeholder:
            has_repairable_entry = True
            continue
        normalized = "/".join(part for part in value.split("/") if part).casefold()
        if normalized:
            explicit_relative_files.add(normalized)
    return has_repairable_entry and explicit_relative_files.issubset(replacement_write_set)


def runtime_episode_task_brief_ids(episode: Mapping[str, Any]) -> list[str]:
    """Return the stable task identities owned by one runtime attempt.

    A repair may legitimately replace an over-broad directory write set with
    explicit files (or replace invalid absolute paths with workspace-relative
    paths).  The task brief id is the durable identity across those contract
    repairs; path containment alone cannot recognize the replacement.
    """

    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    briefs = inputs.get("workerBriefs") or inputs.get("taskBriefs") or inputs.get("tasks") or []
    normalized: list[str] = []
    for brief in list(briefs or []):
        if not isinstance(brief, Mapping):
            continue
        value = str(
            brief.get("taskBriefId")
            or brief.get("task_brief_id")
            or brief.get("taskId")
            or brief.get("task_id")
            or ""
        ).strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _runtime_episode_workspace_identity(episode: Mapping[str, Any]) -> str:
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    engineering_workspace = (
        inputs.get("engineeringWorkspace")
        if isinstance(inputs.get("engineeringWorkspace"), Mapping)
        else {}
    )
    value = (
        engineering_workspace.get("originalWorkspacePath")
        or engineering_workspace.get("original_workspace_path")
        or inputs.get("originalWorkspacePath")
        or inputs.get("original_workspace_path")
        or inputs.get("workspacePath")
        or inputs.get("workspace_path")
        or ""
    )
    return str(value).strip().replace("\\", "/").rstrip("/").casefold()


def _runtime_handoff_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = value.get("payload") if isinstance(value.get("payload"), Mapping) else {}
    return {**dict(value), **dict(payload)}


def _walk_runtime_handoff(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            if isinstance(child, (Mapping, list, tuple)):
                yield from _walk_runtime_handoff(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_runtime_handoff(child)


def runtime_handoffs_have_verified_write_delivery(
    handoffs: Iterable[Mapping[str, Any]],
    *,
    write_set: Iterable[str],
) -> bool:
    """Return true only for a ready delivery with semantic proof and file coverage.

    This intentionally does not accept a status label, an arbitrary successful
    command, or a git ref by itself. It is used solely to decide whether a newer
    retry may retire an older failed attempt from the active completion truth.
    """

    expected = {str(item or "").strip().replace("\\", "/").casefold() for item in write_set if str(item or "").strip()}
    if not expected:
        return False
    ready = False
    verified = False
    changed_paths: set[str] = set()
    for raw_handoff in handoffs:
        handoff = _runtime_handoff_payload(raw_handoff)
        if str(handoff.get("status") or "").strip().lower() in {"ready", "completed", "success", "ok"}:
            ready = True
        for item in _walk_runtime_handoff(handoff):
            verification = item.get("verificationEvidence")
            if isinstance(verification, Mapping) and verification.get("passed") is True:
                verified = True
            verification_results = item.get("verificationResults")
            if isinstance(verification_results, list):
                for result in verification_results:
                    if not isinstance(result, Mapping):
                        continue
                    status = str(result.get("status") or "").strip().lower()
                    if result.get("passed") is True or status in {"verified", "passed", "success", "completed"}:
                        verified = True
            acceptance_check = item.get("acceptanceCheck") or item.get("acceptance_check")
            if isinstance(acceptance_check, Mapping):
                must = acceptance_check.get("must")
                if isinstance(must, Mapping) and must.get("passed") is True:
                    verified = True
            for key in (
                "changedPaths",
                "changed_paths",
                "changedFiles",
                "changed_files",
                "createdFiles",
                "created_files",
                "modifiedFiles",
                "modified_files",
                "writtenFiles",
                "written_files",
            ):
                raw_values = item.get(key)
                values = raw_values if isinstance(raw_values, list) else [raw_values] if raw_values else []
                for value in values:
                    text = str(value or "").strip().strip("`'\"").replace("\\", "/").casefold()
                    while text.startswith("./"):
                        text = text[2:]
                    if text:
                        changed_paths.add(text)
            for ref in list(item.get("artifactRefs") or []):
                if isinstance(ref, Mapping):
                    value = ref.get("path") or ref.get("workspaceRelativePath")
                else:
                    value = ref
                text = str(value or "").strip().strip("`'\"").replace("\\", "/").casefold()
                while text.startswith("./"):
                    text = text[2:]
                if text and not text.startswith(("git://", "artifact://")):
                    changed_paths.add(text)
    covered = all(any(path == target or path.endswith("/" + target) for path in changed_paths) for target in expected)
    return ready and verified and covered


def superseded_runtime_episode_ids(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> set[str]:
    """Identify older top-level write attempts replaced by a proven retry."""

    rows = [dict(item) for item in episodes if isinstance(item, Mapping) and not runtime_episode_parent_id(item)]

    def _episode_id(item: Mapping[str, Any]) -> str:
        return str(item.get("episodeId") or item.get("id") or item.get("needId") or "").strip()

    def _time_key(item: Mapping[str, Any], index: int) -> tuple[str, int]:
        return (
            str(
                item.get("completed_at")
                or item.get("updated_at")
                or item.get("updatedAt")
                or item.get("created_at")
                or item.get("createdAt")
                or ""
            ),
            index,
        )

    proven: list[tuple[dict[str, Any], set[str], set[str], tuple[str, int]]] = []
    for index, episode in enumerate(rows):
        episode_id = _episode_id(episode)
        write_set = set(runtime_episode_write_set(episode))
        task_brief_ids = set(runtime_episode_task_brief_ids(episode))
        state = str(episode.get("state") or "").strip().lower()
        if (
            episode_id
            and state in {"completed", "merged"}
            and runtime_handoffs_have_verified_write_delivery(
                handoffs_by_episode.get(episode_id, []),
                write_set=write_set,
            )
        ):
            proven.append((episode, write_set, task_brief_ids, _time_key(episode, index)))

    superseded: set[str] = set()
    for index, episode in enumerate(rows):
        episode_id = _episode_id(episode)
        write_set = set(runtime_episode_write_set(episode))
        task_brief_ids = set(runtime_episode_task_brief_ids(episode))
        if not episode_id or not write_set:
            continue
        episode_time = _time_key(episode, index)
        workspace = _runtime_episode_workspace_identity(episode)
        episode_kind = normalize_capability_kind(episode.get("kind"))
        for candidate, candidate_write_set, candidate_task_brief_ids, candidate_time in proven:
            candidate_id = _episode_id(candidate)
            candidate_workspace = _runtime_episode_workspace_identity(candidate)
            if candidate_id == episode_id or candidate_time <= episode_time:
                continue
            candidate_kind = normalize_capability_kind(candidate.get("kind"))
            cross_kind_same_task = (
                candidate_kind != episode_kind
                and bool(task_brief_ids)
                and task_brief_ids.issubset(candidate_task_brief_ids)
                and write_set.issubset(candidate_write_set)
            )
            if candidate_kind != episode_kind and not cross_kind_same_task:
                continue
            if workspace and candidate_workspace and workspace != candidate_workspace:
                continue
            same_task_repair = (
                bool(task_brief_ids)
                and task_brief_ids.issubset(candidate_task_brief_ids)
                and _runtime_episode_has_repairable_write_contract(
                    episode,
                    replacement_write_set=candidate_write_set,
                )
            )
            if cross_kind_same_task or same_task_repair or write_set.issubset(candidate_write_set):
                superseded.add(episode_id)
                break
    return superseded


def normalize_capability_kind(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "engineering_runtime": "engineering",
        "project_coding": "engineering",
        "coding": "engineering",
        "research_runtime": "research",
        "creative": "creative_media",
        "creative_media_runtime": "creative_media",
        "computer": "computer_use",
        "computer_use_runtime": "computer_use",
        "desktop": "computer_use",
        "rpa_runtime": "rpa",
        "subagent": "delegation",
        "subagent_swarm": "delegation",
    }
    return aliases.get(normalized, normalized)


def episode_id_from(value: Any = None) -> str:
    existing = str(value or "").strip()
    return existing or f"episode_{uuid.uuid4().hex[:12]}"


def _normalize_runtime_access(items: Any) -> list[str]:
    normalized: list[str] = []
    if not isinstance(items, list):
        items = [items] if items else []
    for item in items:
        value = ""
        if isinstance(item, dict):
            value = str(item.get("group") or item.get("runtimeAccess") or item.get("name") or "").strip()
        else:
            value = str(item or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _resolve_runtime_binding(
    episode: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    runtime_context = get_runtime_context()
    payload = dict(episode or {})

    resolved_session_id = str(
        session_id
        or payload.get("sessionId")
        or payload.get("session_id")
        or runtime_context.get("session_id")
        or runtime_context.get("sessionId")
        or ""
    ).strip() or None
    resolved_run_id = str(
        run_id
        or payload.get("runId")
        or payload.get("run_id")
        or runtime_context.get("run_id")
        or runtime_context.get("runId")
        or ""
    ).strip() or None
    root_run_id = str(
        payload.get("rootRunId")
        or payload.get("root_run_id")
        or runtime_context.get("root_run_id")
        or runtime_context.get("rootRunId")
        or resolved_run_id
        or ""
    ).strip() or None
    return resolved_session_id, resolved_run_id, root_run_id


def build_runtime_episode(
    *,
    need: dict[str, Any] | None = None,
    kind: str,
    state: str = "detected",
    required_runtime_access: list[Any] | None = None,
    parent_episode_id: str | None = None,
    continuation_target: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(need or {})
    episode_id = episode_id_from(payload.get("episodeId") or payload.get("needId"))
    normalized_kind = normalize_capability_kind(kind or payload.get("kind"))
    session_id, run_id, root_run_id = _resolve_runtime_binding(payload)
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    inputs = dict(inputs)
    workspace_path = str(
        payload.get("workspacePath")
        or payload.get("workspace_path")
        or inputs.get("workspacePath")
        or inputs.get("workspace_path")
        or ""
    ).strip()
    if workspace_path:
        inputs.setdefault("workspacePath", workspace_path)
        inputs.setdefault("workspace_path", workspace_path)
    episode = {
        "needId": episode_id,
        "episodeId": episode_id,
        "kind": normalized_kind,
        "source": str(payload.get("source") or "supervisor"),
        "reason": str(payload.get("reason") or ""),
        "inputs": inputs,
        "requiredRuntimeAccess": _normalize_runtime_access(required_runtime_access or payload.get("requiredRuntimeAccess") or []),
        "handoffRefs": list(payload.get("handoffRefs") or []),
        "state": str(state or "detected"),
        "parentEpisodeId": str(parent_episode_id or payload.get("parentEpisodeId") or ""),
        "continuationTarget": str(continuation_target or payload.get("continuationTarget") or ""),
        "createdAt": str(payload.get("createdAt") or utc_now_iso()),
        "updatedAt": utc_now_iso(),
    }
    if session_id:
        episode["sessionId"] = session_id
        episode["session_id"] = session_id
    if run_id:
        episode["runId"] = run_id
        episode["run_id"] = run_id
    if root_run_id:
        episode["rootRunId"] = root_run_id
    for source_key, target_key in (
        ("retryPolicy", "retryPolicy"),
        ("cancelPolicy", "cancelPolicy"),
        ("resumeToken", "resumeToken"),
        ("idempotencyKey", "idempotencyKey"),
        ("deadlineAt", "deadlineAt"),
        ("compensationPlan", "compensationPlan"),
        ("targetKind", "targetKind"),
        ("targetId", "targetId"),
    ):
        if source_key in payload and payload.get(source_key) is not None:
            episode[target_key] = payload.get(source_key)
    if extra:
        episode.update({k: v for k, v in dict(extra).items() if v is not None})
    return episode


def upsert_runtime_episode(route_context: dict[str, Any] | None, episode: dict[str, Any]) -> dict[str, Any]:
    context = deepcopy(dict(route_context or {}))
    episode_id = str(episode.get("episodeId") or episode.get("needId") or "").strip()
    episodes = [dict(item) for item in list(context.get("capabilityEpisodes") or []) if isinstance(item, dict)]
    replaced = False
    for index, item in enumerate(episodes):
        existing_id = str(item.get("episodeId") or item.get("needId") or "").strip()
        if existing_id and existing_id == episode_id:
            episodes[index] = {**item, **episode, "updatedAt": episode.get("updatedAt") or utc_now_iso()}
            replaced = True
            break
    if not replaced:
        episodes.append(dict(episode))
    context["capabilityEpisodes"] = episodes[-50:]
    if str(episode.get("state") or "") in ACTIVE_EPISODE_STATES:
        context["activeCapabilityEpisodeId"] = episode_id
    context["lastCapabilityNeed"] = {
        "kind": str(episode.get("kind") or ""),
        "source": str(episode.get("source") or ""),
        "reason": str(episode.get("reason") or ""),
        "requiredRuntimeAccess": list(episode.get("requiredRuntimeAccess") or []),
    }
    return context


def persist_runtime_episode(
    episode: dict[str, Any],
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    priority: int = 0,
    enqueue: bool = False,
) -> dict[str, Any]:
    """Persist a RuntimeEpisode into the canonical SQLite queue tables."""
    session_binding, run_binding, _ = _resolve_runtime_binding(episode, session_id=session_id, run_id=run_id)
    try:
        persisted = db.upsert_runtime_episode_record(
            episode,
            session_id=session_binding,
            run_id=run_binding,
            priority=priority,
            enqueue=enqueue,
        )
        if session_binding or run_binding:
            try:
                db.backfill_runtime_episode_binding(
                    str(episode.get("episodeId") or episode.get("needId") or ""),
                    session_id=session_binding,
                    run_id=run_binding,
                )
            except Exception:
                pass
        return persisted
    except Exception:
        return dict(episode)


def heartbeat_runtime_episode(episode_id: str, *, progress: str = "") -> None:
    normalized_id = str(episode_id or "").strip()
    if not normalized_id:
        return
    try:
        db.heartbeat_runtime_episode(
            normalized_id,
            progress=str(progress or "").strip() or None,
        )
    except Exception:
        return


def enqueue_runtime_episode(
    episode: dict[str, Any],
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    return persist_runtime_episode(
        {**dict(episode or {}), "state": "queued"},
        session_id=session_id,
        run_id=run_id,
        priority=priority,
        enqueue=True,
    )


def transition_runtime_episode(
    route_context: dict[str, Any] | None,
    episode_id: str,
    *,
    state: str,
    **updates: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    context = deepcopy(dict(route_context or {}))
    episodes = [dict(item) for item in list(context.get("capabilityEpisodes") or []) if isinstance(item, dict)]
    found: dict[str, Any] | None = None
    for index, item in enumerate(episodes):
        existing_id = str(item.get("episodeId") or item.get("needId") or "").strip()
        if existing_id != str(episode_id or "").strip():
            continue
        item.update({k: v for k, v in updates.items() if v is not None})
        item["state"] = state
        item["updatedAt"] = utc_now_iso()
        if state in TERMINAL_EPISODE_STATES:
            item.setdefault("completedAt", item["updatedAt"])
        episodes[index] = item
        found = item
        break
    context["capabilityEpisodes"] = episodes[-50:]
    if found and state in ACTIVE_EPISODE_STATES:
        context["activeCapabilityEpisodeId"] = str(found.get("episodeId") or found.get("needId") or "")
    elif found and state in TERMINAL_EPISODE_STATES:
        active_id = str(context.get("activeCapabilityEpisodeId") or "").strip()
        found_id = str(found.get("episodeId") or found.get("needId") or "").strip()
        if active_id == found_id:
            next_active = ""
            for item in reversed(context["capabilityEpisodes"]):
                item_id = str(item.get("episodeId") or item.get("needId") or "").strip()
                item_state = str(item.get("state") or "").strip()
                if item_id and item_state in ACTIVE_EPISODE_STATES:
                    next_active = item_id
                    break
            if next_active:
                context["activeCapabilityEpisodeId"] = next_active
            else:
                context.pop("activeCapabilityEpisodeId", None)
    return context, found


def build_handoff_ref(
    *,
    producer_episode_id: str,
    kind: str,
    compact_summary: str,
    status: str = "ready",
    confidence: str | None = None,
    raw_ref: str | None = None,
    detail_tool: str | None = None,
    consumer_hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_kind = str(kind or "runtime_result").strip()
    artifact_kind = TYPED_HANDOFF_KINDS.get(normalize_capability_kind(normalized_kind), normalized_kind)
    artifact_id = str((extra or {}).get("artifactId") or f"artifact_{uuid.uuid4().hex[:12]}")
    handoff_id = f"handoff_{uuid.uuid4().hex[:12]}"
    ref = {
        "handoffRefId": handoff_id,
        "artifactId": artifact_id,
        "producerEpisodeId": str(producer_episode_id or ""),
        "kind": artifact_kind,
        "status": str(status or "ready"),
        "compactSummary": str(compact_summary or "").strip(),
        "createdAt": utc_now_iso(),
    }
    if confidence:
        ref["confidence"] = str(confidence)
    if raw_ref:
        ref["rawRef"] = str(raw_ref)
    if detail_tool:
        ref["detailTool"] = str(detail_tool)
    if consumer_hint:
        ref["consumerHint"] = str(consumer_hint)
    if extra:
        ref.update({k: v for k, v in dict(extra).items() if v is not None and k != "artifactId"})
    return ref


def append_handoff_ref(route_context: dict[str, Any] | None, handoff_ref: dict[str, Any]) -> dict[str, Any]:
    context = deepcopy(dict(route_context or {}))
    refs = [dict(item) for item in list(context.get("handoffRefs") or []) if isinstance(item, dict)]
    refs.append(dict(handoff_ref))
    context["handoffRefs"] = refs[-100:]
    producer_id = str(handoff_ref.get("producerEpisodeId") or "").strip()
    if producer_id:
        handoff_status = str(handoff_ref.get("status") or "").strip().lower()
        # A handoff can explicitly pause for one missing, user/Supervisor
        # supplied input.  Treat that as a live episode state; collapsing it
        # to ``completed`` is what previously released the lane while the
        # runtime still had work to do.
        if handoff_status in {"waiting_input", "awaiting_input", "needs_input"}:
            next_state = "waiting_input"
        elif handoff_status in {"running", "waiting", "pending"}:
            next_state = "waiting"
        else:
            next_state = "failed" if handoff_status == "failed" else "completed"
        context, episode = transition_runtime_episode(
            context,
            producer_id,
            state=next_state,
            resultRef=handoff_ref.get("handoffRefId"),
        )
        episodes = [dict(item) for item in list(context.get("capabilityEpisodes") or []) if isinstance(item, dict)]
        for index, item in enumerate(episodes):
            if str(item.get("episodeId") or item.get("needId") or "").strip() == producer_id:
                existing_refs = [str(ref) for ref in list(item.get("handoffRefs") or []) if str(ref).strip()]
                next_ref = str(handoff_ref.get("handoffRefId") or "").strip()
                if next_ref and next_ref not in existing_refs:
                    existing_refs.append(next_ref)
                item["handoffRefs"] = existing_refs
                episodes[index] = item
                break
        context["capabilityEpisodes"] = episodes[-50:]
    return context


def persist_handoff_ref(
    handoff_ref: dict[str, Any],
    *,
    session_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    producer_id = str((handoff_ref or {}).get("producerEpisodeId") or "").strip()
    if not producer_id:
        return dict(handoff_ref or {})
    try:
        return db.add_runtime_episode_handoff(
            episode_id=producer_id,
            handoff=handoff_ref,
            session_id=session_id,
            run_id=run_id,
        )
    except Exception:
        return dict(handoff_ref or {})


def emit_runtime_episode_event(topic: str, payload: dict[str, Any], *, source: dict[str, Any] | None = None) -> None:
    try:
        runtime_context = get_runtime_context()
        episode_payload = payload.get("episode") if isinstance(payload.get("episode"), dict) else payload
        session_id = str(
            runtime_context.get("session_id")
            or runtime_context.get("sessionId")
            or (episode_payload or {}).get("session_id")
            or (episode_payload or {}).get("sessionId")
            or payload.get("session_id")
            or payload.get("sessionId")
            or ""
        ).strip()
        run_id = str(
            runtime_context.get("run_id")
            or runtime_context.get("runId")
            or (episode_payload or {}).get("run_id")
            or (episode_payload or {}).get("runId")
            or payload.get("run_id")
            or payload.get("runId")
            or ""
        ).strip()
        episode_id = str(
            (episode_payload or {}).get("episodeId")
            or (episode_payload or {}).get("needId")
            or payload.get("episodeId")
            or ""
        ).strip()
        state = str((episode_payload or {}).get("state") or payload.get("state") or "").strip() or None
        if episode_id:
            if session_id or run_id:
                try:
                    db.backfill_runtime_episode_binding(
                        episode_id,
                        session_id=session_id or None,
                        run_id=run_id or None,
                    )
                except Exception:
                    pass
            db.add_runtime_episode_event_record(
                episode_id=episode_id,
                topic=topic,
                payload=payload,
                session_id=session_id or None,
                run_id=run_id or None,
                state=state,
            )
        if session_id:
            db.add_runtime_event(
                {
                    "event_id": f"evt_{topic.replace('.', '_')}_{uuid.uuid4().hex[:12]}",
                    "session_id": session_id,
                    "run_id": run_id,
                    "seq": db.get_next_runtime_seq(session_id),
                    "kind": "runtime_event",
                    "topic": topic,
                    "ts": utc_now_iso(),
                    "source": source or {"runtime": "runtime_episode", "component": "runtime_episode_runner"},
                    "payload": payload,
                }
            )
    except Exception:
        return
