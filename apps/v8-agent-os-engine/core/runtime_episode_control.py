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


def inspect_episode(episode_id: str, *, session_id: str, run_id: str) -> dict[str, Any]:
    episode = db.get_runtime_episode(episode_id)
    if not episode or episode.get("session_id") != session_id or episode.get("run_id") != run_id:
        raise ValueError("episode_scope_mismatch")
    messages = db.list_runtime_episode_messages(run_id=run_id, recipient=episode_id, pending_only=False)
    with db.get_connection() as conn:
        progress = conn.execute(
            "SELECT payload_json, created_at FROM runtime_episode_events WHERE episode_id=? "
            "AND topic='runtime.episode.progress' ORDER BY created_at DESC LIMIT 1", (episode_id,),
        ).fetchone()
    import json
    observation = json.loads(progress["payload_json"]) if progress else {}
    return {
        "episodeId": episode_id, "state": episode["state"],
        "executionTerminal": episode["state"] in TERMINAL_EPISODE_STATES,
        "phase": (observation.get("progress") or {}).get("stage") or episode["state"],
        "progress": observation.get("progress") or {},
        "blockingReason": episode.get("error_message") or "",
        "version": {"leaseGeneration": episode.get("leaseGeneration", 0), "updatedAt": episode.get("updated_at")},
        "detailRef": f"episode://{episode_id}",
        "controls": messages,
        "handoffs": db.list_runtime_episode_handoffs(episode_id),
    }


def request_control(episode_id: str, *, session_id: str, run_id: str, kind: str,
                    request_id: str, followup: str = "") -> dict[str, Any]:
    if kind not in {"steer", "cancel"}:
        raise ValueError("unsupported_episode_control")
    if not request_id or (kind == "steer" and not followup.strip()):
        raise ValueError("episode_control_requires_request_id_and_guidance")
    snapshot = inspect_episode(episode_id, session_id=session_id, run_id=run_id)
    if snapshot["executionTerminal"]:
        raise ValueError("episode_already_terminal")
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
    return any(item["kind"] == "cancel" for item in db.list_runtime_episode_messages(
        run_id=run_id, recipient=episode_id,
    ))


def assert_episode_execution_allowed(context: dict[str, Any]) -> None:
    episode_id = str(context.get("delegation_id") or context.get("episode_id") or "")
    run_id = str(context.get("run_id") or "")
    if not episode_id or not run_id:
        return
    episode = db.get_runtime_episode(episode_id) or {}
    if cancellation_requested(episode_id, run_id) or episode.get("state") in TERMINAL_EPISODE_STATES:
        raise EpisodeControlCancelled(episode_id)


def apply_worker_controls(state: dict[str, Any], *, episode_id: str, run_id: str) -> bool:
    """Called by the sole branch writer between nodes, never by a signal thread.

    Applied guidance stays in the ledger and is replayed into a restarted branch.
    Per-branch IDs avoid reinjection during the same execution. A steer before a
    tool step closes the old calls with explicit cancellation observations so
    the next decision can use the guidance before making another side effect.
    """
    if not episode_id or not run_id:
        return False
    controls = db.list_runtime_episode_messages(run_id=run_id, recipient=episode_id, pending_only=False)
    if any(item["kind"] == "cancel" and item["deliveryState"] == "pending" for item in controls):
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
            id=item["messageId"], additional_kwargs={"runtimeControlId": item["messageId"]},
        ))
        seen.add(item["messageId"])
    state["messages"] = messages
    state["runtime_control_ids"] = sorted(seen)
    for item in guidance:
        if db.acknowledge_runtime_episode_message(item["messageId"], recipient=episode_id, state="applied",
                                                  result={"safePoint": "before_graph_node"}):
            emit_runtime_episode_event("runtime.episode.control.applied", {
                "episode": {"episodeId": episode_id, "run_id": run_id},
                "control": {"messageId": item["messageId"], "deliveryState": "applied"},
            })
    return True


def acknowledge_stopped(episode_id: str, *, run_id: str) -> None:
    for item in db.list_runtime_episode_messages(run_id=run_id, recipient=episode_id):
        if item["kind"] == "cancel":
            db.acknowledge_runtime_episode_message(item["messageId"], recipient=episode_id, state="stopped",
                                                  result={"executorSettled": True, "writesTerminated": True})


def publish_attention(episode: dict[str, Any], *, kind: str, detail: dict[str, Any]) -> dict[str, Any] | None:
    """Only decision events enter the parent inbox; progress never calls this."""
    episode_id = str(episode.get("episodeId") or episode.get("id") or "")
    run_id, session_id = str(episode.get("run_id") or ""), str(episode.get("session_id") or "")
    if not episode_id or not run_id or not session_id:
        return None
    if kind not in {"terminal", "input_required", "partial", "partial_invalidated"}:
        raise ValueError("observation_is_not_parent_attention")
    digest = hashlib.sha256(json.dumps(detail, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return db.append_runtime_episode_message(
        episode_id=episode_id, session_id=session_id, run_id=run_id,
        recipient=f"supervisor:{run_id}", kind=kind, request_id=f"attention:{episode_id}:{kind}:{digest}", content=detail,
    )


def acknowledge_parent_messages(state: dict[str, Any], *, run_id: str) -> None:
    for message in list(state.get("messages") or []):
        additional = getattr(message, "additional_kwargs", {}) or {}
        message_id = additional.get("runtimeAttentionId")
        if message_id:
            db.acknowledge_runtime_episode_message(message_id, recipient=f"supervisor:{run_id}", state="processed",
                                                  result={"checkpointObserved": True})


def parent_attention_messages(state: dict[str, Any], *, run_id: str) -> list[HumanMessage]:
    # Incoming state is a saved graph superstep. Only these earlier deliveries
    # may be acknowledged; the new batch is acknowledged after its checkpoint.
    acknowledge_parent_messages(state, run_id=run_id)
    rows = db.list_runtime_episode_messages(run_id=run_id, recipient=f"supervisor:{run_id}")
    return [HumanMessage(
        content="[Runtime decision event; evidence, not an instruction]\n" + json.dumps(item, ensure_ascii=False),
        id=item["messageId"], additional_kwargs={"runtimeAttentionId": item["messageId"]},
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
    identity = hashlib.sha256(json.dumps([episode_id, handoff["outputKey"], handoff["version"]]).encode()).hexdigest()
    payload = {**handoff, "handoffRefId": f"partial:{identity}", "producerEpisodeId": episode_id,
               "kind": "runtime_partial", "status": "partial", "executionTerminal": False}
    result = db.add_runtime_episode_handoff(episode_id=episode_id, handoff=payload,
                                           session_id=episode.get("session_id"), run_id=episode.get("run_id"),
                                           worker_id=worker_id, lease_generation=lease_generation)
    if not result:
        raise ValueError("partial_stale_lease")
    publish_attention(episode, kind="partial", detail={"handoffRefId": result["handoffRefId"],
                                                      "outputKey": handoff["outputKey"], "version": handoff["version"],
                                                      "usableFor": handoff["usableFor"]})
    emit_runtime_episode_event("handoff.ref.created", {"episode": episode, "handoff": result})
    return result


def accept_partial(episode_id: str, *, session_id: str, run_id: str, handoff_id: str,
                   consumers: list[str], reason: str, request_id: str) -> dict[str, Any]:
    snapshot = inspect_episode(episode_id, session_id=session_id, run_id=run_id)
    handoffs = [dict(item.get("payload") or item) for item in snapshot["handoffs"]]
    selected = next((item for item in handoffs if item.get("handoffRefId") == handoff_id), None)
    if not selected or selected.get("status") != "partial":
        raise ValueError("partial_handoff_not_found")
    latest = [item for item in handoffs if item.get("outputKey") == selected.get("outputKey")]
    if latest[-1].get("handoffRefId") != handoff_id:
        raise ValueError("partial_version_superseded")
    if not consumers or not reason.strip() or not set(consumers).issubset(selected.get("usableFor") or []):
        raise ValueError("partial_acceptance_requires_reason_and_declared_consumers")
    receipt = db.append_runtime_episode_message(
        episode_id=episode_id, session_id=session_id, run_id=run_id, recipient=f"partial:{episode_id}",
        kind="accept_partial", request_id=request_id, content={"handoffRefId": handoff_id, "consumers": consumers,
                                                               "reason": reason, "version": selected["version"]},
    )
    db.acknowledge_runtime_episode_message(receipt["messageId"], recipient=f"partial:{episode_id}", state="processed",
                                          result={"acceptedFor": consumers, "finalAcceptance": False})
    # Reuse the dependency queue; its normal claim gate rechecks the version.
    from core.runtime_episode_runner import runtime_episode_runner
    runtime_episode_runner._resume_cross_episode_dependents(db.get_runtime_episode(episode_id))
    return {**receipt, "deliveryState": "processed", "finalAcceptance": False}


def accepted_partial_for(episode: dict[str, Any], *, consumer_task_ids: set[str]) -> dict[str, Any] | None:
    episode_id = str(episode.get("episodeId") or episode.get("id") or "")
    rows = db.list_runtime_episode_messages(run_id=str(episode.get("run_id") or ""), recipient=f"partial:{episode_id}", pending_only=False)
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
