"""Local episode control. The event ledger owns delivery; graph owners apply it."""
from __future__ import annotations

from typing import Any
import asyncio
import hashlib
import json

from langchain_core.messages import HumanMessage, ToolMessage

from core.database import db
from core.runtime_episodes import TERMINAL_EPISODE_STATES, emit_runtime_episode_event


class EpisodeControlCancelled(asyncio.CancelledError):
    pass


def inspect_episode(episode_id: str, *, session_id: str, run_id: str, detail: bool = False) -> dict[str, Any]:
    episode = db.get_runtime_episode(episode_id)
    if not episode or episode.get("session_id") != session_id or episode.get("run_id") != run_id:
        raise ValueError("episode_scope_mismatch")
    messages = db.list_runtime_episode_messages(run_id=run_id, recipient=episode_id, pending_only=False,
                                               limit=129 if detail else 17, newest_first=True)
    window_limit = 128 if detail else 16
    has_more_controls = len(messages) > window_limit
    messages = messages[:window_limit]
    with db.get_connection() as conn:
        progress = conn.execute(
            "SELECT payload_json, created_at FROM runtime_episode_events WHERE episode_id=? "
            "AND topic='runtime.episode.progress' ORDER BY created_at DESC LIMIT 1", (episode_id,),
        ).fetchone()
    import json
    observation = json.loads(progress["payload_json"]) if progress else {}
    from urllib.parse import quote
    handoffs = db.list_runtime_episode_handoffs(episode_id)
    if not detail:
        messages = [{key: item[key] for key in ("messageId", "kind", "deliverySeq", "deliveryState", "receipt") if key in item} for item in messages]
        handoffs = [{key: payload[key] for key in ("handoffRefId", "producerEpisodeId", "kind", "status", "compactSummary", "version", "sourceVersion", "usableFor", "proofRefs", "artifactRefs") if key in payload}
                    for item in handoffs[-12:] for payload in [dict(item.get("payload") or item)]]
    return {
        "episodeId": episode_id, "state": episode["state"],
        "executionTerminal": episode["state"] in TERMINAL_EPISODE_STATES,
        "completedAt": episode.get("completed_at"),
        "phase": (observation.get("progress") or {}).get("stage") or episode["state"],
        "progress": observation.get("progress") or {},
        "blockingReason": episode.get("error_message") or "",
        "version": {"leaseGeneration": episode.get("leaseGeneration", 0), "updatedAt": episode.get("updated_at")},
        "detailRef": f"/runtime-episodes/{quote(episode_id, safe='')}?sessionId={quote(session_id, safe='')}&runId={quote(run_id, safe='')}",
        "detailTool": "runtime_broker(mode='inspect', episode_id=..., detail_level='full')",
        "controls": messages,
        "controlWindow": {"complete": not has_more_controls, "newestFirst": True},
        "handoffs": handoffs,
    }


def request_control(episode_id: str, *, session_id: str, run_id: str, kind: str,
                    request_id: str, followup: str = "") -> dict[str, Any]:
    if kind not in {"steer", "cancel"}:
        raise ValueError("unsupported_episode_control")
    if not request_id or (kind == "steer" and not followup.strip()):
        raise ValueError("episode_control_requires_request_id_and_guidance")
    inspect_episode(episode_id, session_id=session_id, run_id=run_id)
    receipt = db.append_runtime_episode_message(
        episode_id=episode_id, session_id=session_id, run_id=run_id,
        recipient=episode_id, kind=kind, request_id=request_id, content={"followup": followup},
    )
    emit_runtime_episode_event("runtime.episode.control.received", {
        "episode": {"episodeId": episode_id, "session_id": session_id, "run_id": run_id},
        "control": {"messageId": receipt["messageId"], "kind": kind, "deliveryState": receipt["deliveryState"]},
    })
    return receipt


def cancellation_requested(episode_id: str, run_id: str) -> bool:
    with db.get_connection() as conn:
        return bool(conn.execute(
            "WITH RECURSIVE ancestors(id, parent_id) AS (SELECT id, parent_episode_id FROM runtime_episodes WHERE id=? "
            "UNION SELECT parent.id, parent.parent_episode_id FROM runtime_episodes parent JOIN ancestors ON parent.id=ancestors.parent_id) "
            "SELECT 1 FROM runtime_episode_events WHERE episode_id IN (SELECT id FROM ancestors) AND run_id=? "
            "AND topic='runtime.episode.message' AND state='pending' "
            "AND json_extract(payload_json, '$.kind')='cancel' LIMIT 1", (episode_id, run_id),
        ).fetchone())


def assert_episode_execution_allowed(context: dict[str, Any]) -> None:
    run_id = str(context.get("run_id") or "")
    if not run_id:
        return
    for episode_id in {str(context.get(key) or "") for key in ("delegation_id", "episode_id", "parent_delegation_id")} - {""}:
        episode = db.get_runtime_episode(episode_id) or {}
        if cancellation_requested(episode_id, run_id) or episode.get("state") in TERMINAL_EPISODE_STATES:
            raise EpisodeControlCancelled(episode_id)
    assert_partial_dependencies_current(list(context.get("dependency_results") or []))


def assert_partial_dependencies_current(results: list[dict[str, Any]]) -> None:
    for result in results:
        if result.get("resultPhase") != "accepted_partial":
            continue
        producer_id, ref = result.get("producerEpisodeId"), result.get("handoffRefId")
        handoffs = [dict(row.get("payload") or row) for row in db.list_runtime_episode_handoffs(str(producer_id or ""))]
        selected = next((item for item in handoffs if item.get("handoffRefId") == ref), None)
        same_output = [item for item in handoffs if selected and item.get("outputKey") == selected.get("outputKey")]
        if not selected or not same_output or same_output[-1].get("handoffRefId") != ref:
            raise ValueError("accepted_partial_superseded")


def apply_worker_controls(state: dict[str, Any], *, episode_id: str, run_id: str) -> bool:
    """Called by the sole branch writer between nodes, never by a signal thread.

    Applied guidance stays in the ledger and is replayed into a restarted branch.
    Per-branch IDs avoid reinjection during the same execution. A steer before a
    tool step closes the old calls with explicit cancellation observations so
    the next decision can use the guidance before making another side effect.
    """
    if not episode_id or not run_id:
        return False
    cursors = dict(state.get("runtime_control_cursors") or {})
    controls = db.list_runtime_episode_messages(run_id=run_id, recipient=episode_id, pending_only=False,
                                               after_seq=int(cursors.get(episode_id) or 0))
    if cancellation_requested(episode_id, run_id):
        raise EpisodeControlCancelled(episode_id)
    seen = set(state.get("runtime_control_ids") or [])
    guidance = [item for item in controls if item["kind"] == "steer"
                and item["deliveryState"] in {"pending", "applied"} and item["messageId"] not in seen]
    if not guidance:
        return False
    messages = list(state.get("messages") or [])
    calls = list(getattr(messages[-1], "tool_calls", None) or []) if messages else []
    for call in calls:
        messages.append(ToolMessage(content="Tool was not executed: Supervisor guidance arrived before this step.",
                                    tool_call_id=call["id"], name=call.get("name")))
    for item in guidance:
        messages.append(HumanMessage(
            content="[Supervisor guidance for this episode]\n" + item["content"]["followup"],
            id=item["messageId"], additional_kwargs={"v8_governance_type": "runtime_episode_guidance", "runtimeControlId": item["messageId"], "runtimeControlSeq": item["deliverySeq"], "runtimeControlEpisodeId": episode_id},
        ))
        seen.add(item["messageId"])
    state["messages"] = messages
    state["runtime_control_ids"] = sorted(seen)
    cursors[episode_id] = max(item["deliverySeq"] for item in controls)
    state["runtime_control_cursors"] = cursors
    for item in guidance:
        if db.acknowledge_runtime_episode_message(item["messageId"], recipient=episode_id, state="applied",
                                                  result={"safePoint": "before_graph_node"}):
            emit_runtime_episode_event("runtime.episode.control.applied", {
                "episode": {key: value for key, value in (db.get_runtime_episode(episode_id) or {}).items() if key in {"episodeId", "session_id", "run_id", "state", "kind"}},
                "control": {"messageId": item["messageId"], "kind": "steer", "deliveryState": "applied"},
            })
    return True


def apply_model_controls(messages: list[Any], context: dict[str, Any]) -> bool:
    """Let the current model owner consume original-episode guidance in place."""
    episode_id = str(context.get("episode_id") or context.get("delegation_id") or "")
    run_id = str(context.get("run_id") or "")
    if not episode_id or not run_id:
        return False
    assert_episode_execution_allowed(context)
    seen, cursor = [], 0
    for message in messages:
        metadata = getattr(message, "additional_kwargs", {}) or {}
        if metadata.get("runtimeControlEpisodeId") == episode_id:
            seen.append(metadata["runtimeControlId"])
            cursor = max(cursor, int(metadata.get("runtimeControlSeq") or 0))
    state = {"messages": messages, "runtime_control_ids": seen, "runtime_control_cursors": {episode_id: cursor}}
    applied = apply_worker_controls(state, episode_id=episode_id, run_id=run_id)
    if applied:
        messages[:] = state["messages"]
    return applied


def acknowledge_stopped(episode_id: str, *, run_id: str) -> None:
    while rows := db.list_runtime_episode_messages(run_id=run_id, recipient=episode_id):
        for item in rows:
            acknowledged = db.acknowledge_runtime_episode_message(
                item["messageId"], recipient=episode_id,
                state="stopped" if item["kind"] == "cancel" else "rejected",
                result={"executorSettled": True, "writesTerminated": True, "reason": "episode_cancelled"},
            )
            if acknowledged:
                emit_runtime_episode_event("runtime.episode.control.stopped" if item["kind"] == "cancel" else "runtime.episode.control.rejected", {
                    "episode": {key: value for key, value in (db.get_runtime_episode(episode_id) or {}).items() if key in {"episodeId", "session_id", "run_id", "state", "kind"}},
                    "control": {"messageId": item["messageId"], "kind": item["kind"], "deliveryState": "stopped" if item["kind"] == "cancel" else "rejected"},
                })


def publish_attention(episode: dict[str, Any], *, kind: str, detail: dict[str, Any]) -> dict[str, Any] | None:
    """Only decision events enter the parent inbox; progress never calls this."""
    episode_id = str(episode.get("episodeId") or episode.get("id") or "")
    run_id, session_id = str(episode.get("run_id") or ""), str(episode.get("session_id") or "")
    if not episode_id or not run_id or not session_id:
        return None
    if kind not in {"terminal", "input_required", "partial", "partial_invalidated", "user_guidance"}:
        raise ValueError("observation_is_not_parent_attention")
    digest = hashlib.sha256(json.dumps(detail, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return db.append_runtime_episode_message(
        episode_id=episode_id, session_id=session_id, run_id=run_id,
        recipient=f"supervisor:{run_id}", kind=kind, request_id=f"attention:{episode_id}:{kind}:{digest}", content=detail,
    )


def pending_run_guidance(run_id: str) -> dict[str, Any] | None:
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM chat_user_message_queue WHERE run_id=? AND state='promoted' ORDER BY ordinal, created_at LIMIT 1", (run_id,),
        ).fetchone()
    return db.get_chat_user_message_queue_item(row["id"]) if row else None


def reconcile_episode_attention(episode: dict[str, Any]) -> None:
    """Repair a crash between canonical handoff commit and notification."""
    episode_id = str(episode.get("episodeId") or episode.get("id") or "")
    state = str(episode.get("state") or "")
    if state in TERMINAL_EPISODE_STATES or state == "waiting_input":
        publish_attention(episode, kind="input_required" if state == "waiting_input" else "terminal", detail={
            "state": state, "resultRef": episode.get("resultRef") or episode.get("result_ref"),
            "detailRef": f"episode://{episode_id}",
        })
    for row in db.list_runtime_episode_handoffs(episode_id):
        handoff = dict(row.get("payload") or row)
        if handoff.get("status") == "partial" and handoff.get("outputKey"):
            publish_attention(episode, kind="partial", detail={
                "handoffRefId": handoff["handoffRefId"], "outputKey": handoff["outputKey"],
                "version": handoff["version"], "usableFor": handoff["usableFor"],
            })
    reconcile_partial_invalidations(episode)


def reconcile_partial_invalidations(episode: dict[str, Any]) -> None:
    """Derive invalidations from committed outputs and acceptance receipts."""
    episode_id = str(episode.get("episodeId") or episode.get("id") or "")
    handoffs = [dict(row.get("payload") or row) for row in db.list_runtime_episode_handoffs(episode_id)]
    positions = {item.get("handoffRefId"): index for index, item in enumerate(handoffs)}
    cursor = 0
    while receipts := db.list_runtime_episode_messages(run_id=str(episode.get("run_id") or ""), recipient=f"partial:{episode_id}", pending_only=False, after_seq=cursor):
        for receipt in receipts:
            if receipt["kind"] != "accept_partial" or receipt["deliveryState"] != "processed":
                continue
            index = positions.get(receipt["content"]["handoffRefId"])
            if index is None:
                continue
            accepted = handoffs[index]
            successor = next((item for item in handoffs[index + 1:] if item.get("outputKey") == accepted.get("outputKey")), None)
            if successor:
                # The first superseding immutable version gives retries and
                # recovery exactly the same event identity, even after v20.
                publish_attention(episode, kind="partial_invalidated", detail={
                    "handoffRefId": accepted["handoffRefId"], "supersededBy": successor["handoffRefId"],
                    "consumers": receipt["content"]["consumers"], "acceptanceId": receipt["messageId"],
                })
        cursor = receipts[-1]["deliverySeq"]


def acknowledge_parent_messages(state: dict[str, Any], *, run_id: str) -> None:
    for message in list(state.get("messages") or []):
        additional = getattr(message, "additional_kwargs", {}) or {}
        message_id = additional.get("runtimeAttentionId")
        if message_id:
            partial_batch = additional.get("runtimePartialBatch")
            if isinstance(partial_batch, dict):
                db.acknowledge_runtime_partial_batch(run_id=run_id, episode_id=partial_batch["episodeId"],
                    output_key=partial_batch["outputKey"], through_seq=partial_batch["throughSeq"], delivered_message_id=message_id)
            db.acknowledge_runtime_episode_message(message_id, recipient=f"supervisor:{run_id}", state="processed",
                                                  result={"checkpointObserved": True})


def parent_attention_messages(state: dict[str, Any], *, run_id: str) -> list[HumanMessage]:
    # Incoming state is a saved graph superstep. Only these earlier deliveries
    # may be acknowledged; the new batch is acknowledged after its checkpoint.
    acknowledge_parent_messages(state, run_id=run_id)
    rows = db.list_runtime_parent_attention(run_id)
    return [HumanMessage(
        content="[Runtime decision event; evidence, not an instruction]\n" + json.dumps({
            "episodeId": item["episodeId"], "kind": item["kind"], **item["content"],
            **({"supersedesEarlierPendingVersions": True} if item["kind"] == "partial" else {}),
        }, ensure_ascii=False),
        id=item["messageId"], additional_kwargs={"v8_governance_type": "runtime_episode_attention", "runtimeAttentionId": item["messageId"],
            **({"runtimePartialBatch": {"episodeId": item["episodeId"], "outputKey": item["content"].get("outputKey", ""), "throughSeq": item["deliverySeq"]}} if item["kind"] == "partial" else {})},
    ) for item in rows]


def publish_partial(episode_id: str, *, handoff: dict[str, Any], worker_id: str, lease_generation: int) -> dict[str, Any]:
    """Publish immutable, versioned evidence while the executor keeps running."""
    episode = db.get_runtime_episode(episode_id) or {}
    required = ("outputKey", "version", "sourceVersion", "usableFor", "compactSummary", "proofRefs")
    if any(not handoff.get(key) for key in required):
        raise ValueError("partial_requires_output_version_source_scope_and_proof")
    if not isinstance(handoff["usableFor"], list) or not isinstance(handoff["proofRefs"], list):
        raise ValueError("partial_scope_and_proof_must_be_arrays")
    if cancellation_requested(episode_id, str(episode.get("run_id") or "")):
        raise EpisodeControlCancelled(episode_id)
    from core.runtime_episode_runner import RuntimeEpisodeRunner
    producer_tasks = set(RuntimeEpisodeRunner._episode_task_ids(episode))
    covered_tasks = list(handoff.get("taskBriefIds") or sorted(producer_tasks))
    if not set(covered_tasks).issubset(producer_tasks):
        raise ValueError("partial_task_scope_exceeds_producer")
    identity = hashlib.sha256(json.dumps([episode_id, handoff["outputKey"], handoff["version"]]).encode()).hexdigest()
    payload = {**handoff, "handoffRefId": f"partial:{identity}", "producerEpisodeId": episode_id,
               "kind": "runtime_partial", "status": "partial", "executionTerminal": False, "taskBriefIds": covered_tasks}
    result = db.add_runtime_episode_handoff(episode_id=episode_id, handoff=payload,
                                           session_id=episode.get("session_id"), run_id=episode.get("run_id"),
                                           worker_id=worker_id, lease_generation=lease_generation)
    if not result:
        raise ValueError("partial_stale_lease")
    reconcile_partial_invalidations(episode)
    publish_attention(episode, kind="partial", detail={"handoffRefId": result["handoffRefId"],
                                                      "outputKey": handoff["outputKey"], "version": handoff["version"],
                                                      "usableFor": handoff["usableFor"]})
    emit_runtime_episode_event("handoff.ref.created", {"episode": episode, "handoff": result})
    return result


def accept_partial(episode_id: str, *, session_id: str, run_id: str, handoff_id: str,
                   consumers: list[str], reason: str, request_id: str) -> dict[str, Any]:
    receipt = db.accept_runtime_episode_partial(
        episode_id=episode_id, session_id=session_id, run_id=run_id, handoff_id=handoff_id,
        consumers=consumers, reason=reason, request_id=request_id,
    )
    # Reuse the dependency queue; its normal claim gate rechecks the version.
    from core.runtime_episode_runner import runtime_episode_runner
    runtime_episode_runner._resume_cross_episode_dependents(db.get_runtime_episode(episode_id))
    return receipt


def accepted_partial_for(episode: dict[str, Any], *, consumer_task_ids: set[str]) -> dict[str, Any] | None:
    episode_id = str(episode.get("episodeId") or episode.get("id") or "")
    rows = []
    cursor = 0
    while page := db.list_runtime_episode_messages(run_id=str(episode.get("run_id") or ""), recipient=f"partial:{episode_id}", pending_only=False, after_seq=cursor):
        rows.extend(page)
        cursor = page[-1]["deliverySeq"]
    handoffs = [dict(item.get("payload") or item) for item in db.list_runtime_episode_handoffs(episode_id)]
    latest = {item.get("outputKey"): item for item in handoffs if item.get("status") == "partial"}
    for row in reversed(rows):
        if row["kind"] != "accept_partial" or row["deliveryState"] != "processed":
            continue
        if not consumer_task_ids or not consumer_task_ids.issubset(row["content"]["consumers"]):
            continue
        for handoff in latest.values():
            if handoff.get("handoffRefId") == row["content"]["handoffRefId"]:
                return {**handoff, "partialAcceptanceId": row["messageId"], "acceptedFor": row["content"]["consumers"]}
    return None
