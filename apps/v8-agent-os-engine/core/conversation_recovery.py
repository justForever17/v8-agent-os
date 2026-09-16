"""Atomic, user-authored revisions and inert branches of canonical messages."""
from __future__ import annotations

import json
import uuid
from typing import Any

from core.conversation_schema import transcript_state
from core.realtime_protocol import utc_now_iso

TERMINAL_MESSAGES = {"completed", "failed", "cancelled"}
TERMINAL_RUNS = {"completed", "failed", "cancelled", "interrupted", "aborted", "rejected", "degraded"}
SAFE_SESSION_SETTINGS = {"provider", "model", "modelRef", "temperature", "source", "locale", "language"}
SAFE_MESSAGE_METADATA = {"timestamp", "agentName", "agentAvatar", "agentRoleLabel", "agentId", "images",
                         "attachments", "composerPresentation", "contextMentions", "skillReferences",
                         "edited", "editedBy", "editedAt", "revisionVersion", "contentOrigin"}


class ConversationConflict(ValueError):
    def __init__(self, code: str, *, status: int = 409, **details: Any):
        super().__init__(code)
        self.status = status
        self.detail = {"code": code, **details}


def public_state(state: dict) -> dict:
    return {"transcriptRevision": int(state["transcript_revision"]), "contextEpoch": int(state["context_epoch"]),
            **({"branch": state["branch"]} if state.get("branch") else {})}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _inherited_reference(value: Any, child: str) -> Any:
    """Rebind governed local source links; never inherit a signed URL ticket."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    if isinstance(value, list):
        return [_inherited_reference(item, child) for item in value]
    if isinstance(value, dict):
        return {key: child if key in {"sessionId", "session_id"} else _inherited_reference(item, child)
                for key, item in value.items() if key not in {"v8sig", "v8exp"}}
    if isinstance(value, str):
        try:
            parts = urlsplit(value)
        except ValueError:
            return value
        if (not parts.scheme or parts.hostname in {"localhost", "127.0.0.1", "::1"}) and parts.path.startswith(
            ("/api/client/workspace/", "/api/workspace/", "/workspace/resource", "/v1/artifacts/", "/api/client/artifacts/", "/api/artifacts/")
        ):
            query = {k: v for k, v in parse_qsl(parts.query) if k not in {"v8sig", "v8exp"}}
            query["sessionId"] = child
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return value


def _text(content: str) -> str:
    if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > 1_000_000:
        raise ConversationConflict("invalid_message_text", status=422)
    if any(ord(c) < 32 and c not in "\n\r\t" for c in content):
        raise ConversationConflict("invalid_message_text", status=422)
    return content  # Preserve the user's Markdown and whitespace exactly.


def _owned_idle(conn, session_id: str, owner: str) -> dict:
    session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if session is None:
        raise ConversationConflict("session_not_found", status=404)
    if not owner or session["user_id"] != owner:
        raise ConversationConflict("session_owner_mismatch", status=403)
    lane = conn.execute("SELECT * FROM session_lane_records WHERE session_id=?", (session_id,)).fetchone()
    if lane and (lane["active_run_id"] or lane["queued_run_id"] or lane["blocked_by_run_id"]):
        raise ConversationConflict("conversation_busy")
    runs = conn.execute("SELECT status FROM run_records WHERE session_id=?", (session_id,)).fetchall()
    if any(row["status"] not in TERMINAL_RUNS for row in runs):
        raise ConversationConflict("conversation_busy")
    for table in ("pending_approvals", "ask_user_interactions"):
        if conn.execute(f"SELECT 1 FROM {table} WHERE session_id=? AND status='pending' LIMIT 1", (session_id,)).fetchone():
            raise ConversationConflict("conversation_pending_action")
    if conn.execute("SELECT 1 FROM runtime_episodes WHERE session_id=? AND state NOT IN ('completed','failed','cancelled','merged','degraded') LIMIT 1", (session_id,)).fetchone():
        raise ConversationConflict("conversation_busy")
    if conn.execute("SELECT 1 FROM chat_user_message_queue WHERE session_id=? AND state IN ('pending','promoted') LIMIT 1", (session_id,)).fetchone():
        raise ConversationConflict("conversation_busy")
    if conn.execute("SELECT 1 FROM runtime_side_effect_receipts WHERE session_id=? AND state NOT IN ('completed','failed') LIMIT 1", (session_id,)).fetchone():
        raise ConversationConflict("conversation_unsettled_effect")
    if conn.execute("""SELECT 1 FROM session_coordination_messages WHERE (source_session_id=? OR target_session_id=?)
        AND state IN ('queued','promoted','injected','blocked') LIMIT 1""", (session_id, session_id)).fetchone():
        raise ConversationConflict("conversation_pending_action")
    if conn.execute("""SELECT 1 FROM session_command_assignments a JOIN run_records r ON r.session_id=a.child_session_id
        WHERE a.root_session_id=? AND a.status='active' AND r.status NOT IN
        ('completed','failed','cancelled','interrupted','aborted','rejected','degraded') LIMIT 1""", (session_id,)).fetchone():
        raise ConversationConflict("conversation_unsettled_effect")
    return dict(session)


def _rows(conn, database, session_id: str) -> list[dict]:
    return [database._hydrate_chat_canonical_row(dict(row)) for row in conn.execute("""
        SELECT * FROM chat_canonical_messages m WHERE session_id=? AND NOT EXISTS (
            SELECT 1 FROM chat_message_deletions d WHERE d.session_id=m.session_id
            AND (d.message_id=m.id OR d.canonical_message_id=m.id)) ORDER BY ordinal, created_at
    """, (session_id,)).fetchall()]


def _expected(state: dict, expected_revision: int, message: dict | None = None, expected_version: int | None = None):
    if int(state["transcript_revision"]) != expected_revision or (
        message is not None and int(message["version"]) != expected_version
    ):
        raise ConversationConflict("message_revision_conflict", **public_state(state),
                                   currentMessageVersion=message["version"] if message else None,
                                   currentContent=message.get("content_text") if message else None)


def _editable(row: dict) -> None:
    if row["role"] not in {"user", "assistant"} or row["state"] not in TERMINAL_MESSAGES:
        raise ConversationConflict("message_not_editable")
    # Coordination/control cards can contain prose but are not authored text.
    if row["role"] == "assistant" and not any(n.get("kind") == "narrative" for n in row["nodes"]) and not row.get("content_text"):
        raise ConversationConflict("message_not_editable")


def _rotate(conn, session_id: str, now: str) -> dict:
    conn.execute("""UPDATE chat_session_transcript_state SET context_epoch=context_epoch+1,
        active_checkpoint_thread_id=?, updated_at=? WHERE session_id=?""",
                 (f"conversation_{uuid.uuid4().hex}", now, session_id))
    # Session/task grants belong to the old causal context. Installed/global
    # grants and credentials remain owned by their existing authorities.
    conn.execute("UPDATE plugin_grants SET revoked_at=? WHERE session_id=? AND revoked_at IS NULL", (now, session_id))
    conn.execute("""UPDATE session_command_assignments SET status='revoked',revision=revision+1,revoked_at=?,updated_at=?
        WHERE (root_session_id=? OR child_session_id=?) AND status='active'""", (now, now, session_id, session_id))
    # Previously approved deliveries may not revive an old checkpoint.
    conn.execute("UPDATE pending_approvals SET status='cancelled',updated_at=? WHERE session_id=? AND status='approved'", (now, session_id))
    state = transcript_state(conn, session_id)
    conn.execute("DELETE FROM memory_extraction_state WHERE session_id=?", (session_id,))
    conn.execute("UPDATE session_scope_bindings SET thread_id=?, updated_at=? WHERE session_id=?",
                 (state["active_checkpoint_thread_id"], now, session_id))
    conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
    return state


def _event(conn, database, session_id: str, topic: str, payload: dict, now: str) -> None:
    seq = database._allocate_runtime_event_seq(conn, session_id)
    conn.execute("""INSERT INTO runtime_events
        (id,session_id,run_id,seq,kind,topic,event_ts,source_json,payload_json) VALUES (?,?,NULL,?,'event',?,?,?,?)""",
                 (f"evt_{uuid.uuid4().hex}", session_id, seq, topic, now,
                  _json({"runtime": "chat", "component": "conversation_recovery"}), _json(payload)))


def _revise(conn, database, session_id: str, row: dict, rows: list[dict], content: str,
            owner: str, *, truncate: bool, mode: str, now: str) -> dict:
    _editable(row)
    descendants = [item for item in rows if item["ordinal"] > row["ordinal"]]
    if descendants and not truncate:
        raise ConversationConflict("message_has_descendants", descendantCount=len(descendants))
    before = transcript_state(conn, session_id)
    # Replace narrative positions only. Execution, reasoning, tool proof and
    # artifact nodes retain their original bytes and ordering.
    nodes, replaced = [], False
    for node in row["nodes"]:
        if node.get("kind") == "narrative":
            nodes.append({**node, "content": content if not replaced else ""})
            replaced = True
        else:
            nodes.append(node)
    if not replaced:
        nodes.append({"id": f"{row['id']}:revision", "kind": "narrative", "content": content})
    metadata = {**row["metadata"], "edited": True, "editedBy": "user", "editedAt": now,
                "revisionVersion": row["version"] + 1, "contentOrigin": "user_revision"}
    from core.provider_continuation import strip_private_provider_continuation
    metadata = strip_private_provider_continuation(metadata)
    conn.execute("""UPDATE chat_canonical_messages SET nodes_json=?,content_text=?,metadata_json=?,
        version=version+1,updated_at=? WHERE id=? AND version=?""",
                 (_json(nodes), content, _json(metadata), now, row["id"], row["version"]))
    if conn.execute("SELECT changes()").fetchone()[0] != 1:
        raise ConversationConflict("message_revision_conflict")
    for descendant in descendants:
        database._record_chat_message_deletion(conn, session_id=session_id, message_id=descendant["id"],
            canonical_message_id=descendant["id"], run_id=descendant["run_id"], source="conversation_revision",
            metadata={"anchorMessageId": row["id"]})
    state = _rotate(conn, session_id, now)
    revision_id = f"revision_{uuid.uuid4().hex}"
    conn.execute("""INSERT INTO chat_message_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (revision_id, session_id, row["id"], row["version"], row["version"] + 1, before["transcript_revision"],
         state["transcript_revision"], _json(row), content, _json([r["id"] for r in descendants]), owner, mode,
         state["context_epoch"], now))
    result = {"sessionId": session_id, "messageId": row["id"], "messageVersion": row["version"] + 1,
              "revisionId": revision_id, "truncatedMessageIds": [r["id"] for r in descendants], **public_state(state)}
    _event(conn, database, session_id, "message.revised", result, now)
    return result


def revise_message(database, *, session_id: str, message_id: str, owner: str, content: str,
                   expected_message_version: int, expected_transcript_revision: int, tail_policy: str = "reject") -> dict:
    content = _text(content)
    if tail_policy not in {"reject", "reject_if_descendants", "truncate"}:
        raise ConversationConflict("invalid_tail_policy", status=422)
    with database.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _owned_idle(conn, session_id, owner)
        rows = _rows(conn, database, session_id)
        row = next((r for r in rows if r["id"] == message_id), None)
        if row is None:
            raise ConversationConflict("message_not_found", status=404)
        _expected(transcript_state(conn, session_id), expected_transcript_revision, row, expected_message_version)
        result = _revise(conn, database, session_id, row, rows, content, owner, truncate=tail_policy == "truncate",
                         mode="replace_and_truncate" if tail_policy == "truncate" else "in_place", now=utc_now_iso())
        conn.commit()
    return result


def _branch_title(root: str, number: int) -> str:
    # regex is already a runtime dependency; \X preserves combining/ZWJ emoji.
    import regex
    suffix = f"({number})"
    title = ""
    for grapheme in regex.findall(r"\X", root):
        if len(title + grapheme + suffix) > 80:
            break
        title += grapheme
    return title + suffix


def restore_revision(database, *, session_id: str, message_id: str, revision_id: str, owner: str,
                     expected_message_version: int, expected_transcript_revision: int) -> dict:
    """Recovery is a new forward revision, never resurrection of old authority."""
    now = utc_now_iso()
    with database.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _owned_idle(conn, session_id, owner)
        ledger = conn.execute("SELECT * FROM chat_message_revisions WHERE session_id=? AND message_id=? AND revision_id=?",
                              (session_id, message_id, revision_id)).fetchone()
        if ledger is None:
            raise ConversationConflict("revision_not_found", status=404)
        rows = _rows(conn, database, session_id)
        row = next((r for r in rows if r["id"] == message_id), None)
        if row is None:
            raise ConversationConflict("message_not_found", status=404)
        _expected(transcript_state(conn, session_id), expected_transcript_revision, row, expected_message_version)
        # Restoring an old tail over newly continued work would invent another
        # causal chain. The user must branch/truncate that work first.
        tail = json.loads(ledger["superseded_ids_json"])
        if tail and any(r["ordinal"] > row["ordinal"] for r in rows):
            raise ConversationConflict("revision_restore_has_descendants")
        previous = json.loads(ledger["previous_snapshot_json"])
        for old_id in tail:
            conn.execute("DELETE FROM chat_message_deletions WHERE session_id=? AND canonical_message_id=? AND source='conversation_revision'",
                         (session_id, old_id))
        result = _revise(conn, database, session_id, row, rows, previous["content_text"], owner,
                         truncate=False, mode="restore", now=now)
        result["restoredMessageIds"] = tail
        conn.commit()
    return result


def create_branch(database, *, session_id: str, owner: str, turn_id: str, expected_transcript_revision: int,
                  expected_message_version: int | None = None, message_id: str | None = None,
                  content: str | None = None) -> dict:
    if content is not None:
        content = _text(content)
        if not message_id or expected_message_version is None:
            raise ConversationConflict("revision_anchor_required", status=422)
    from erc.chat_canonical_transcript import group_canonical_turn_rows, _stable_turn_id
    now = utc_now_iso()
    with database.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        source = _owned_idle(conn, session_id, owner)
        state = transcript_state(conn, session_id)
        rows = _rows(conn, database, session_id)
        group = next((g for g in group_canonical_turn_rows(rows) if _stable_turn_id(session_id, g) == turn_id), None)
        if not group:
            raise ConversationConflict("turn_not_found", status=404)
        anchor = next((r for r in group if r["id"] == message_id), None) if message_id else group[-1]
        if anchor is None:
            raise ConversationConflict("message_not_in_turn", status=422)
        _expected(state, expected_transcript_revision, anchor if expected_message_version is not None else None, expected_message_version)
        prefix = [r for r in rows if r["ordinal"] <= group[-1]["ordinal"]]
        if any(r["state"] not in TERMINAL_MESSAGES for r in prefix):
            raise ConversationConflict("turn_not_terminal")
        parent = conn.execute("SELECT * FROM chat_conversation_branches WHERE child_session_id=?", (session_id,)).fetchone()
        if parent is None:
            parent = conn.execute("SELECT * FROM chat_conversation_branches WHERE root_session_id=? ORDER BY branch_number LIMIT 1", (session_id,)).fetchone()
        root_id, root_title = (parent["root_session_id"], parent["root_title"]) if parent else (session_id, source["title"])
        used = {r[0] for r in conn.execute("SELECT branch_number FROM chat_conversation_branches WHERE root_session_id=?", (root_id,))}
        number = 1
        while number in used:
            number += 1
        child = f"conversation_{uuid.uuid4().hex}"
        title = _branch_title(root_title, number)
        settings = {k: v for k, v in json.loads(source["metadata"] or "{}").items() if k in SAFE_SESSION_SETTINGS}
        conn.execute("INSERT INTO sessions (id,title,user_id,created_at,updated_at,metadata) VALUES (?,?,?,?,?,?)",
                     (child, title, owner, now, now, _json(settings)))
        conn.execute("INSERT INTO chat_conversation_branches VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (child, root_id, session_id, turn_id, group[-1]["ordinal"], state["transcript_revision"], number, root_title, owner, now))
        # Scope identifiers and location are copied explicitly. No workflow,
        # remote channel, credential, runtime run or permission is inherited.
        conn.execute("""INSERT INTO session_scope_bindings
            (session_id,conversation_id,thread_id,user_id,workspace_id,workspace_path,project_id,
             scope_hint,resolved_scope,scope_source,scope_confidence,status)
            SELECT ?,?,?,user_id,workspace_id,workspace_path,project_id,scope_hint,resolved_scope,
                   'conversation_branch',scope_confidence,'active' FROM session_scope_bindings WHERE session_id=?""",
                     (child, child, child, session_id))
        copied_anchor = None
        for row in prefix:
            new_id = f"message_{uuid.uuid4().hex}"
            metadata = {k: _inherited_reference(v, child) if k in {"attachments", "images"} else v
                        for k, v in row["metadata"].items() if k in SAFE_MESSAGE_METADATA}
            metadata.update({"sourceSessionId": session_id, "sourceMessageId": row["id"],
                             "sourceMessageVersion": row["version"], "sourceRunId": row["run_id"], "branchInherited": True})
            # Materialize source references, never their blobs. Legacy source
            # message FKs point to the legacy mirror, so child ownership lives
            # in metadata while the source is bound to its new session.
            source_ids = {a.get("sourceId") or a.get("source_id") or a.get("id") for a in metadata.get("attachments", [])}
            source_map = {}
            for source_row in conn.execute("SELECT * FROM session_sources WHERE session_id=?", (session_id,)).fetchall():
                if source_row["message_id"] != row["id"] and source_row["id"] not in source_ids:
                    continue
                new_source_id = f"source_{uuid.uuid4().hex}"
                source_map[source_row["id"]] = new_source_id
                source_metadata = json.loads(source_row["metadata_json"] or "{}")
                source_metadata.update({"branchInherited": True, "readOnly": True, "sourceSessionId": session_id,
                                        "sourceId": source_row["id"], "canonicalMessageId": new_id})
                conn.execute("""INSERT INTO session_sources
                    (id,session_id,message_id,source_kind,mime_type,title,workspace_path,external_url,preview_url,resource_ref_json,metadata_json)
                    VALUES (?,?,NULL,?,?,?,?,?,?,?,?)""", (new_source_id, child, source_row["source_kind"], source_row["mime_type"],
                    source_row["title"], source_row["workspace_path"], _inherited_reference(source_row["external_url"], child),
                    _inherited_reference(source_row["preview_url"], child),
                    _json(_inherited_reference(json.loads(source_row["resource_ref_json"] or "{}"), child)), _json(source_metadata)))
            if metadata.get("attachments"):
                metadata["attachments"] = [{**a, "sourceId": source_map.get(a.get("sourceId") or a.get("source_id") or a.get("id"),
                                                                          a.get("sourceId") or a.get("source_id") or a.get("id"))}
                                           for a in metadata["attachments"]]
            nodes = [{**node, "branchInherited": True, "readOnly": True} for node in row["nodes"]]
            for node in nodes:
                if node.get("kind") == "artifact" and isinstance(node.get("artifact"), dict):
                    artifact = _inherited_reference(dict(node["artifact"]), child)
                    if artifact.get("sourceId") in source_map:
                        artifact["sourceId"] = source_map[artifact["sourceId"]]
                    node["artifact"] = {**artifact, "branchInherited": True, "readOnly": True}
            artifacts = _inherited_reference(list(row["artifacts"]), child)
            # Runtime artifact records survive owner deletion (SET NULL); this
            # explicit child reference both pins them and authorizes read-only
            # access through the child's independently checked workspace scope.
            artifact_rows = conn.execute("""SELECT * FROM runtime_artifacts WHERE
                message_id=? OR (session_id=? AND run_id=? AND message_id IS NULL)
                OR id IN (SELECT artifact_id FROM chat_branch_artifact_refs WHERE child_session_id=? AND source_message_id=?)""",
                (row["id"], session_id, row["run_id"], session_id, row["metadata"].get("sourceMessageId", row["id"]))).fetchall()
            known_artifacts = {a.get("id") or a.get("artifactId") for a in artifacts}
            for artifact_row in artifact_rows:
                if artifact_row["id"] not in known_artifacts:
                    from core.multimodal_payload_adapter import normalize_artifact_record
                    artifact = dict(artifact_row)
                    artifact["metadata"] = json.loads(artifact.get("metadata_json") or "{}")
                    artifacts.append(normalize_artifact_record(artifact))
            for artifact in artifacts:
                artifact_id = artifact.get("id") or artifact.get("artifactId")
                if artifact_id and conn.execute("SELECT 1 FROM runtime_artifacts WHERE id=?", (artifact_id,)).fetchone():
                    conn.execute("INSERT OR IGNORE INTO chat_branch_artifact_refs VALUES (?,?,?,?)",
                                 (child, artifact_id, session_id, row["id"]))
            conn.execute("""INSERT INTO chat_canonical_messages
                (id,session_id,run_id,ordinal,role,state,nodes_json,artifacts_json,content_text,reasoning_text,
                 metadata_json,version,created_at,updated_at,finalized_at) VALUES (?,?,NULL,?,?,?,?,?,?,?,?,1,?,?,?)""",
                         (new_id, child, row["ordinal"], row["role"], row["state"], _json(nodes),
                          _json(artifacts), row["content_text"], row["reasoning_text"], _json(metadata),
                          row["created_at"], now, row["finalized_at"]))
            if row["id"] == message_id:
                copied_anchor = new_id
        child_state = _rotate(conn, child, now)
        if content is not None:
            child_rows = _rows(conn, database, child)
            target = next(r for r in child_rows if r["id"] == copied_anchor)
            _revise(conn, database, child, target, child_rows, content, owner,
                    truncate=True, mode="branch_with_revision", now=now)
            child_state = transcript_state(conn, child)
        result = {"sessionId": child, "newSessionId": child, "sourceSessionId": session_id,
                  "title": title, "anchorTurnId": turn_id, "branchNumber": number, **public_state(child_state)}
        _event(conn, database, session_id, "session.branch.created", {**result, **public_state(state)}, now)
        _event(conn, database, child, "session.branch.created", result, now)
        conn.commit()
    return result
