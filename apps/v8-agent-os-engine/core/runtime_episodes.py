from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

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
        context, episode = transition_runtime_episode(
            context,
            producer_id,
            state="failed" if handoff_status == "failed" else "completed",
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
