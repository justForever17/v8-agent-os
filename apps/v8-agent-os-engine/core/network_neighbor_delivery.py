"""Atomic inbox receipts using the existing neighbor message and wake tables.

The signed sender's business identity, not a recent-history scan or an in-memory
nonce, owns deduplication. A receipt and its optional wake are one transaction.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from core.realtime_protocol import utc_now_iso


class NeighborExecutionPaused(RuntimeError):
    def __init__(self, state: str):
        super().__init__(state)
        self.state = state


def message_text(value: Any) -> tuple[str, str, bool]:
    body = str(value or "")
    preview = body[:800] + ("…" if len(body) > 800 else "")
    # Transport has an explicit byte limit. Do not silently replace a task or
    # answer with a preview before sending/persisting its authoritative body.
    return body, preview, False


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def receive_message(database, *, link: dict, envelope, body: str, preview: str,
                    workspace_binding: dict, metadata: dict, business_id: str,
                    wake_payload: dict | None = None, task_seed: dict | None = None,
                    assignment_seed: dict | None = None, result_seed: dict | None = None) -> dict:
    link_id = str(link.get("linkId") or link.get("id") or "")
    receipt_id = "nmsg_in_" + _digest([link_id, envelope.from_peer_id, envelope.message_type, business_id])
    fingerprint = _digest(envelope.payload)
    now = utc_now_iso()
    with database.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM network_neighbor_messages WHERE id = ?", (receipt_id,)).fetchone()
        if row:
            stored = database._hydrate_network_neighbor_message_row(dict(row))
            if stored["metadata"].get("payloadDigest") != fingerprint:
                raise HTTPException(status_code=409, detail="Neighbor message identity reused with different content")
            queue_row = conn.execute("SELECT * FROM network_neighbor_wake_queue WHERE message_id = ?", (receipt_id,)).fetchone()
            conn.commit()
            return {"message": stored, "queue": database._hydrate_network_neighbor_wake_queue_row(dict(queue_row)) if queue_row else None, "duplicate": True}
        if task_seed is not None and assignment_seed is not None:
            task, assignment = _store_assignment(database, conn, task_seed, assignment_seed, now)
            wake_payload = {**(wake_payload or {}), "task": task, "assignment": assignment}
        if result_seed is not None:
            result = _store_result(database, conn, result_seed, now)
            if wake_payload is not None:
                wake_payload = {**wake_payload, "result": result}
        seq = database._next_network_neighbor_message_seq(conn, link_id)
        stored_metadata = {**metadata, "remoteMessageId": business_id, "payloadDigest": fingerprint}
        conn.execute(
            """INSERT INTO network_neighbor_messages
               (id, link_id, seq, direction, from_peer_id, from_nickname, role, body, preview,
                status, workspace_binding_json, metadata_json, created_at, received_at)
               VALUES (?, ?, ?, 'inbound', ?, ?, ?, ?, ?, 'received', ?, ?, ?, ?)""",
            (receipt_id, link_id, seq, envelope.from_peer_id, str(link.get("remoteNickname") or envelope.from_peer_id),
             str(link.get("remoteRole") or "companion"), body, preview,
             json.dumps(workspace_binding, ensure_ascii=False), json.dumps(stored_metadata, ensure_ascii=False), now, now),
        )
        row = conn.execute("SELECT * FROM network_neighbor_messages WHERE id = ?", (receipt_id,)).fetchone()
        stored = database._hydrate_network_neighbor_message_row(dict(row))
        queue = None
        if wake_payload is not None:
            queue_id = "nwake_" + receipt_id.removeprefix("nmsg_in_")
            run_id = "run_" + uuid.uuid4().hex
            payload = {**wake_payload, "link": link, "inboundMessage": stored, "workspaceBinding": workspace_binding, "sourcePeerId": envelope.from_peer_id}
            conn.execute(
                """INSERT INTO network_neighbor_wake_queue
                   (id, link_id, message_id, run_id, state, attempt_count, max_attempts, available_at, payload_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'queued', 0, 3, ?, ?, ?, ?)""",
                (queue_id, link_id, receipt_id, run_id, now, json.dumps(payload, ensure_ascii=False), now, now),
            )
            queue = database._hydrate_network_neighbor_wake_queue_row(dict(conn.execute(
                "SELECT * FROM network_neighbor_wake_queue WHERE id = ?", (queue_id,)).fetchone()))
        conn.execute("UPDATE network_neighbor_links SET last_seen_at = ?, updated_at = ? WHERE id = ?", (now, now, link_id))
        conn.commit()
        return {"message": stored, "queue": queue, "duplicate": False}


def _store_assignment(database, conn, task: dict, assignment: dict, now: str) -> tuple[dict, dict]:
    """Task projections and intake receipt commit together under BEGIN IMMEDIATE."""
    task_id, assignment_id = task["task_id"], assignment["assignment_id"]
    row = conn.execute("SELECT * FROM network_neighbor_tasks WHERE id = ?", (task_id,)).fetchone()
    if row:
        stored = database._hydrate_network_neighbor_task_row(dict(row))
        if stored["body"] != task["body"] or stored.get("metadata", {}).get("fromPeerId") != assignment["peer_id"]:
            raise HTTPException(status_code=409, detail="Task identity or content conflict")
    else:
        conn.execute(
            """INSERT INTO network_neighbor_tasks
               (id,title,body,status,target_mode,origin_session_id,origin_run_id,wake_policy,
                required_capabilities_json,workspace_binding_json,metadata_json,created_at,updated_at)
               VALUES (?,?,?,'received','inbound',?,?,?,?,?,?,?,?)""",
            (task_id, task["title"], task["body"], task.get("origin_session_id"), task.get("origin_run_id"),
             task["wake_policy"], json.dumps(task["required_capabilities"]),
             json.dumps(task["workspace_binding"], ensure_ascii=False), json.dumps(task["metadata"]), now, now),
        )
    row = conn.execute("SELECT * FROM network_neighbor_assignments WHERE id = ?", (assignment_id,)).fetchone()
    if row:
        stored = database._hydrate_network_neighbor_assignment_row(dict(row))
        if (stored["taskId"], stored["peerId"], stored["linkId"], stored.get("metadata", {}).get("payloadDigest")) != (
            task_id, assignment["peer_id"], assignment["link_id"], assignment["metadata"]["payloadDigest"]):
            raise HTTPException(status_code=409, detail="Assignment identity or content conflict")
    else:
        conn.execute(
            """INSERT INTO network_neighbor_assignments
               (id,task_id,link_id,peer_id,parent_assignment_id,depth,status,body,required_capabilities_json,
                wake_policy,metadata_json,created_at,updated_at)
               VALUES (?,?,?,?,?,?,'received',?,?,?,?,?,?)""",
            (assignment_id, task_id, assignment["link_id"], assignment["peer_id"], assignment.get("parent_assignment_id"),
             assignment["depth"], assignment["body"], json.dumps(assignment["required_capabilities"]),
             assignment["wake_policy"], json.dumps(assignment["metadata"]), now, now),
        )
    return (
        database._hydrate_network_neighbor_task_row(dict(conn.execute("SELECT * FROM network_neighbor_tasks WHERE id = ?", (task_id,)).fetchone())),
        database._hydrate_network_neighbor_assignment_row(dict(conn.execute("SELECT * FROM network_neighbor_assignments WHERE id = ?", (assignment_id,)).fetchone())),
    )


def _store_result(database, conn, result: dict, now: str) -> dict:
    row = conn.execute("SELECT * FROM network_neighbor_assignments WHERE id = ?", (result["assignment_id"],)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Unknown assignment")
    assignment = database._hydrate_network_neighbor_assignment_row(dict(row))
    if (assignment["taskId"], assignment["peerId"], assignment["linkId"]) != (result["task_id"], result["peer_id"], result["link_id"]):
        raise HTTPException(status_code=403, detail="Result sender does not own assignment")
    if assignment["status"] in {"completed", "failed", "cancelled"} and assignment.get("resultId") not in (None, "", result["result_id"]):
        raise HTTPException(status_code=409, detail="Assignment already has a terminal result")
    row = conn.execute("SELECT * FROM network_neighbor_task_results WHERE id = ?", (result["result_id"],)).fetchone()
    if row:
        stored = database._hydrate_network_neighbor_task_result_row(dict(row))
        if (stored["taskId"], stored["assignmentId"], stored["peerId"], stored["body"], stored["status"]) != (
            result["task_id"], result["assignment_id"], result["peer_id"], result["body"], result["status"]):
            raise HTTPException(status_code=409, detail="Result identity conflict")
    else:
        conn.execute(
            """INSERT INTO network_neighbor_task_results
               (id,task_id,assignment_id,link_id,peer_id,status,summary,body,needs_attention,
                requested_capabilities_json,handoff_reason,metadata_json,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (result["result_id"], result["task_id"], result["assignment_id"], result["link_id"], result["peer_id"],
             result["status"], result["summary"], result["body"], int(result["needs_attention"]),
             json.dumps(result["requested_capabilities"]), result.get("handoff_reason"), json.dumps(result["metadata"]), now),
        )
    terminal = result["status"] in {"completed", "failed", "cancelled"}
    conn.execute(
        """UPDATE network_neighbor_assignments SET status=?,result_id=?,updated_at=?,
           completed_at=CASE WHEN ? THEN ? ELSE completed_at END WHERE id=?""",
        (result["status"], result["result_id"], now, terminal, now, result["assignment_id"]),
    )
    return database._hydrate_network_neighbor_task_result_row(dict(conn.execute(
        "SELECT * FROM network_neighbor_task_results WHERE id = ?", (result["result_id"],)).fetchone()))


def settle_wake(database, item: dict, *, state: str, error: str = "") -> bool:
    """Fence terminal writes against both worker identity and claim generation."""
    now = utc_now_iso()
    with database.get_connection() as conn:
        cursor = conn.execute(
            """UPDATE network_neighbor_wake_queue SET state = ?, last_error = ?, updated_at = ?,
               completed_at = CASE WHEN ? = 'completed' THEN ? ELSE completed_at END,
               failed_at = CASE WHEN ? = 'failed' THEN ? ELSE failed_at END, lease_expires_at = NULL
               WHERE id = ? AND state = 'leased' AND claimed_by = ? AND attempt_count = ?""",
            (state, error or None, now, state, now, state, now, item["queueId"], item["claimedBy"], item["attemptCount"]),
        )
        conn.commit()
        return cursor.rowcount == 1


def renew_wake(database, item: dict, *, seconds: int = 180) -> bool:
    now = utc_now_iso()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    with database.get_connection() as conn:
        cursor = conn.execute(
            """UPDATE network_neighbor_wake_queue SET lease_expires_at = ?, updated_at = ?
               WHERE id = ? AND state = 'leased' AND claimed_by = ? AND attempt_count = ? AND lease_expires_at > ?""",
            (expires, now, item["queueId"], item["claimedBy"], item["attemptCount"], now),
        )
        conn.commit()
        return cursor.rowcount == 1


def claim_waiting_delivery(database, item: dict, *, worker_id: str) -> dict | None:
    """Claim only delivery of an already-settled run, never re-execute the graph."""
    expires = (datetime.now(timezone.utc) + timedelta(seconds=180)).isoformat().replace("+00:00", "Z")
    with database.get_connection() as conn:
        changed = conn.execute(
            """UPDATE network_neighbor_wake_queue SET state = 'leased', claimed_by = ?,
               attempt_count = attempt_count + 1, lease_expires_at = ?, updated_at = ?
               WHERE id = ? AND state IN ('waiting_input','waiting_approval')""",
            (worker_id, expires, utc_now_iso(), item["queueId"]),
        ).rowcount
        conn.commit()
    return database.get_network_neighbor_wake_queue_item(item["queueId"]) if changed else None
