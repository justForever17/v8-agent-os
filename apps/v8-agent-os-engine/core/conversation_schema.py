"""Add revision lineage to the canonical transcript; never a second chat store."""
from __future__ import annotations

import sqlite3


def ensure_conversation_schema(conn: sqlite3.Connection) -> None:
    run_columns = {row[1] for row in conn.execute("PRAGMA table_info(run_records)")}
    if "context_epoch" not in run_columns:
        conn.execute("ALTER TABLE run_records ADD COLUMN context_epoch INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE run_records SET context_epoch=COALESCE(json_extract(metadata,'$.contextEpoch'),0)")
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_session_transcript_state (
        session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        transcript_revision INTEGER NOT NULL DEFAULT 0,
        context_epoch INTEGER NOT NULL DEFAULT 0,
        derived_context_epoch INTEGER NOT NULL DEFAULT 0,
        active_checkpoint_thread_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_message_revisions (
        revision_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        message_id TEXT NOT NULL,
        base_message_version INTEGER NOT NULL,
        result_message_version INTEGER NOT NULL,
        base_transcript_revision INTEGER NOT NULL,
        result_transcript_revision INTEGER NOT NULL,
        previous_snapshot_json TEXT NOT NULL,
        replacement_text TEXT NOT NULL,
        superseded_ids_json TEXT NOT NULL,
        edited_by TEXT NOT NULL,
        mode TEXT NOT NULL,
        context_epoch INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_revision_message ON chat_message_revisions(session_id, message_id, result_message_version)")
    # Parent/root ids deliberately have no cascading FK. A materialized child
    # remains usable after deletion of its source conversation.
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_conversation_branches (
        child_session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        root_session_id TEXT NOT NULL, parent_session_id TEXT NOT NULL,
        anchor_turn_id TEXT NOT NULL, anchor_last_ordinal INTEGER NOT NULL,
        source_transcript_revision INTEGER NOT NULL, branch_number INTEGER NOT NULL,
        root_title TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(root_session_id, branch_number)
    )""")
    conn.execute("""INSERT OR IGNORE INTO chat_session_transcript_state
        (session_id,transcript_revision,context_epoch,active_checkpoint_thread_id,updated_at)
        SELECT s.id, COALESCE((SELECT SUM(version) FROM chat_canonical_messages m WHERE m.session_id=s.id),0),
        0, s.id, strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM sessions s
        WHERE NOT EXISTS (SELECT 1 FROM chat_session_transcript_state t WHERE t.session_id=s.id)""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS chat_transcript_session_insert AFTER INSERT ON sessions BEGIN
        INSERT OR IGNORE INTO chat_session_transcript_state
        (session_id,transcript_revision,context_epoch,active_checkpoint_thread_id,updated_at)
        VALUES (NEW.id,0,0,NEW.id,strftime('%Y-%m-%dT%H:%M:%fZ','now'));
    END""")
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_branch_artifact_refs (
        child_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        artifact_id TEXT NOT NULL REFERENCES runtime_artifacts(id) ON DELETE RESTRICT,
        source_session_id TEXT NOT NULL, source_message_id TEXT NOT NULL,
        PRIMARY KEY(child_session_id, artifact_id)
    )""")
    # Every canonical mutation advances the session revision, including changes
    # to a low-version historical row and deletion of the last visible message.
    for action, ref in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS chat_transcript_{action.lower()}
            AFTER {action} ON chat_canonical_messages BEGIN
            UPDATE chat_session_transcript_state SET transcript_revision=transcript_revision+1,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE session_id={ref}.session_id;
        END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS chat_transcript_tombstone AFTER INSERT ON chat_message_deletions BEGIN
        UPDATE chat_session_transcript_state SET transcript_revision=transcript_revision+1,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE session_id=NEW.session_id;
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS chat_transcript_restore AFTER DELETE ON chat_message_deletions BEGIN
        UPDATE chat_session_transcript_state SET transcript_revision=transcript_revision+1,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE session_id=OLD.session_id;
    END""")
    # Capture authority at run creation, in the same writer transaction. Missing
    # epoch on historical runs means epoch zero, never the current epoch.
    conn.execute("DROP TRIGGER IF EXISTS chat_run_context_epoch")
    conn.execute("""CREATE TRIGGER chat_run_context_epoch AFTER INSERT ON run_records BEGIN
        UPDATE run_records SET context_epoch=COALESCE((SELECT context_epoch FROM chat_session_transcript_state WHERE session_id=NEW.session_id),0),
            metadata=json_set(COALESCE(metadata,'{}'),'$.contextEpoch',
            COALESCE((SELECT context_epoch FROM chat_session_transcript_state WHERE session_id=NEW.session_id),0))
        WHERE id=NEW.id;
    END""")


def transcript_state(conn: sqlite3.Connection, session_id: str) -> dict:
    row = conn.execute("SELECT * FROM chat_session_transcript_state WHERE session_id=?", (session_id,)).fetchone()
    state = dict(row) if row else {"session_id": session_id, "transcript_revision": 0, "derived_context_epoch": 0,
                                  "context_epoch": 0, "active_checkpoint_thread_id": session_id}
    branch = conn.execute("SELECT * FROM chat_conversation_branches WHERE child_session_id=?", (session_id,)).fetchone()
    if branch:
        state["branch"] = {"sourceSessionId": branch["parent_session_id"], "parentSessionId": branch["parent_session_id"],
                           "rootSessionId": branch["root_session_id"], "anchorTurnId": branch["anchor_turn_id"],
                           "branchNumber": branch["branch_number"], "rootTitle": branch["root_title"]}
    return state


def assert_run_epoch(conn: sqlite3.Connection, session_id: str, run_id: str | None) -> None:
    if not run_id:
        return
    row = conn.execute("SELECT session_id, context_epoch FROM run_records WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise ValueError("conversation_run_missing")
    epoch = int(row["context_epoch"])
    if row["session_id"] != session_id or epoch != transcript_state(conn, session_id)["context_epoch"]:
        raise ValueError("conversation_context_superseded")
