"""SQLite schema bootstrap and additive migrations.

This module is the single owner of schema creation and migration ordering. It is
connection-bound by design: DatabaseManager owns connection lifecycle and legacy
catalog repair, while this module owns one transaction's schema work.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from core.realtime_protocol import utc_now_iso

def ensure_session_command_tables(conn: sqlite3.Connection) -> None:
    """Create the canonical durable root/child assignment relation."""
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS session_command_assignments (
            id TEXT PRIMARY KEY,
            root_session_id TEXT NOT NULL,
            child_session_id TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            contract_json TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            idempotency_key TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            revoked_at TIMESTAMP,
            FOREIGN KEY (root_session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (child_session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            UNIQUE (root_session_id, idempotency_key)
        )
        '''
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_command_root ON session_command_assignments (root_session_id, status, updated_at DESC)"
    )

def ensure_runtime_safety_tables(conn: sqlite3.Connection) -> None:
    """Create idempotency and receipt ledgers without requiring a schema bump."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runtime_automation_deliveries (
            delivery_id TEXT PRIMARY KEY,
            definition_kind TEXT NOT NULL,
            definition_id TEXT NOT NULL,
            definition_revision TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            source_run_id TEXT,
            execution_run_id TEXT NOT NULL,
            envelope_json TEXT NOT NULL,
            phase TEXT NOT NULL DEFAULT 'pending',
            owner_id TEXT,
            lease_expires_at TEXT,
            available_at TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            receipt_key TEXT,
            last_error TEXT,
            admitted_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_event_id, definition_kind, definition_id),
            FOREIGN KEY (source_session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_automation_delivery_pending ON runtime_automation_deliveries(phase, available_at, lease_expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_automation_delivery_triage ON runtime_automation_deliveries(phase, created_at, delivery_id)")
    approval_columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_approvals)").fetchall()}
    if approval_columns and "resume_json" not in approval_columns:
        conn.execute("ALTER TABLE pending_approvals ADD COLUMN resume_json TEXT NOT NULL DEFAULT '{}'")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_episode_idempotency (
            idempotency_key TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL UNIQUE,
            session_id TEXT,
            run_id TEXT,
            payload_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_side_effect_receipts (
            idempotency_key TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT,
            effect_kind TEXT NOT NULL,
            step_key TEXT NOT NULL,
            target_identity TEXT NOT NULL,
            payload_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL,
            owner_id TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            lease_expires_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            failed_at TEXT,
            last_error TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_runtime_side_effect_receipts_session "
        "ON runtime_side_effect_receipts (session_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_runtime_side_effect_receipts_state "
        "ON runtime_side_effect_receipts (state, lease_expires_at)"
    )
    runtime_sequence_table_existed = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'runtime_event_sequence_heads'"
        ).fetchone()
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_event_sequence_heads (
            session_id TEXT PRIMARY KEY,
            last_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """
    )
    existing_tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if not runtime_sequence_table_existed and "runtime_events" in existing_tables:
        snapshot_union = (
            "UNION ALL "
            "SELECT session_id, MAX(latest_seq) AS last_seq "
            "FROM runtime_snapshots GROUP BY session_id"
            if "runtime_snapshots" in existing_tables
            else ""
        )
        sequence_rows = conn.execute(
            f"""
            SELECT session_id, MAX(last_seq) AS last_seq
            FROM (
                SELECT session_id, MAX(seq) AS last_seq
                FROM runtime_events
                GROUP BY session_id
                {snapshot_union}
            )
            WHERE COALESCE(session_id, '') <> ''
            GROUP BY session_id
            """
        ).fetchall()
        now_iso = utc_now_iso()
        for row in sequence_rows:
            conn.execute(
                """
                INSERT INTO runtime_event_sequence_heads (session_id, last_seq, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_seq = MAX(runtime_event_sequence_heads.last_seq, excluded.last_seq),
                    updated_at = CASE
                        WHEN excluded.last_seq > runtime_event_sequence_heads.last_seq
                        THEN excluded.updated_at
                        ELSE runtime_event_sequence_heads.updated_at
                    END
                """,
                (str(row["session_id"]), int(row["last_seq"] or 0), now_iso),
            )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_canvas_graph_run_event_outbox (
            outbox_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            outbox_id TEXT NOT NULL UNIQUE,
            event_id TEXT NOT NULL UNIQUE,
            graph_run_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            expected_status TEXT NOT NULL,
            expected_updated_at TEXT NOT NULL,
            transition TEXT NOT NULL DEFAULT '',
            retry_of_graph_run_id TEXT,
            run_row_json TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_attempt_at TEXT,
            runtime_event_id TEXT,
            created_at TEXT NOT NULL,
            projected_at TEXT,
            UNIQUE (graph_run_id, expected_status, expected_updated_at, transition),
            FOREIGN KEY (graph_run_id) REFERENCES creative_canvas_graph_runs(graph_run_id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """
    )
    outbox_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(creative_canvas_graph_run_event_outbox)").fetchall()
    }
    if "next_attempt_at" not in outbox_columns:
        conn.execute(
            "ALTER TABLE creative_canvas_graph_run_event_outbox ADD COLUMN next_attempt_at TEXT"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvas_graph_run_event_outbox_pending "
        "ON creative_canvas_graph_run_event_outbox (projected_at, outbox_sequence)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvas_graph_run_event_outbox_session "
        "ON creative_canvas_graph_run_event_outbox (session_id, outbox_sequence)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_canvas_graph_remote_terminal_receipts (
            job_id TEXT NOT NULL,
            proof_digest TEXT NOT NULL,
            session_id TEXT NOT NULL,
            graph_run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            provider_status TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            PRIMARY KEY (job_id, proof_digest),
            FOREIGN KEY (graph_run_id) REFERENCES creative_canvas_graph_runs(graph_run_id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvas_graph_remote_terminal_receipts_run "
        "ON creative_canvas_graph_remote_terminal_receipts (graph_run_id, node_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_canvas_output_reviews (
            output_version_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            graph_id TEXT NOT NULL,
            result_node_id TEXT NOT NULL,
            decision TEXT NOT NULL DEFAULT 'pending'
                CHECK (decision IN ('pending', 'approved', 'rejected')),
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            note TEXT NOT NULL DEFAULT '',
            selected_for_delivery INTEGER NOT NULL DEFAULT 0
                CHECK (selected_for_delivery IN (0, 1)),
            delivery_manifest_artifact_id TEXT,
            delivery_status TEXT NOT NULL DEFAULT 'idle'
                CHECK (delivery_status IN ('idle', 'pending', 'failed', 'delivered')),
            delivery_attempt INTEGER NOT NULL DEFAULT 0 CHECK (delivery_attempt >= 0),
            delivery_lease_id TEXT,
            delivery_lease_expires_at TEXT,
            delivery_error_detail_code TEXT,
            delivery_manifest_digest TEXT,
            delivery_manifest_bytes_digest TEXT,
            delivery_manifest_relative_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reviewed_at TEXT,
            delivered_at TEXT,
            FOREIGN KEY (output_version_id)
                REFERENCES creative_canvas_node_outputs(output_version_id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (graph_id) REFERENCES creative_canvas_graphs(graph_id) ON DELETE CASCADE
        )
        """
    )
    review_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(creative_canvas_output_reviews)").fetchall()
    }
    for column_name, column_sql in (
        ("delivery_status", "TEXT NOT NULL DEFAULT 'idle'"),
        ("delivery_attempt", "INTEGER NOT NULL DEFAULT 0"),
        ("delivery_lease_id", "TEXT"),
        ("delivery_lease_expires_at", "TEXT"),
        ("delivery_error_detail_code", "TEXT"),
        ("delivery_manifest_digest", "TEXT"),
        ("delivery_manifest_bytes_digest", "TEXT"),
        ("delivery_manifest_relative_path", "TEXT"),
    ):
        if column_name not in review_columns:
            conn.execute(
                f"ALTER TABLE creative_canvas_output_reviews ADD COLUMN {column_name} {column_sql}"
            )
    conn.execute(
        """
        UPDATE creative_canvas_output_reviews
        SET delivery_status = CASE
                WHEN delivered_at IS NOT NULL THEN 'delivered'
                WHEN delivery_manifest_artifact_id IS NOT NULL THEN 'failed'
                ELSE 'idle'
            END,
            delivery_attempt = CASE
                WHEN delivery_attempt > 0 THEN delivery_attempt
                WHEN delivery_manifest_artifact_id IS NOT NULL THEN 1
                ELSE 0
            END
        WHERE delivery_status = 'idle'
          AND (delivered_at IS NOT NULL OR delivery_manifest_artifact_id IS NOT NULL)
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvas_output_reviews_session "
        "ON creative_canvas_output_reviews (session_id, updated_at DESC)"
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_canvas_output_reviews_selected_result
        ON creative_canvas_output_reviews (graph_id, result_node_id)
        WHERE selected_for_delivery = 1
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_canvas_output_review_heads (
            graph_id TEXT NOT NULL,
            result_node_id TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            selected_output_version_id TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (graph_id, result_node_id),
            FOREIGN KEY (graph_id) REFERENCES creative_canvas_graphs(graph_id) ON DELETE CASCADE
        )
        """
    )
    graph_table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'creative_canvas_graphs'"
    ).fetchone()
    if graph_table_exists:
        conn.execute(
            """
            INSERT INTO creative_canvas_output_review_heads(
                graph_id, result_node_id, revision, selected_output_version_id, updated_at
            )
            SELECT graph_id, result_node_id, MAX(revision),
                   MAX(CASE WHEN selected_for_delivery = 1 THEN output_version_id END),
                   MAX(updated_at)
            FROM creative_canvas_output_reviews
            GROUP BY graph_id, result_node_id
            ON CONFLICT(graph_id, result_node_id) DO UPDATE SET
                revision = MAX(creative_canvas_output_review_heads.revision, excluded.revision),
                selected_output_version_id = COALESCE(
                    excluded.selected_output_version_id,
                    creative_canvas_output_review_heads.selected_output_version_id
                ),
                updated_at = MAX(creative_canvas_output_review_heads.updated_at, excluded.updated_at)
            """
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvas_output_review_heads_selected "
        "ON creative_canvas_output_review_heads (selected_output_version_id)"
    )

def ensure_creative_media_store_tables(conn: sqlite3.Connection) -> None:
    """Create the additive Creative Media operational store.

    These rows deliberately do not reference ``sessions``. A deleted
    Session must not erase the minimum provider lifecycle and projection
    evidence required to reconcile or quarantine a remote task.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_media_jobs (
            job_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            workspace_id TEXT NOT NULL DEFAULT '',
            project_id TEXT NOT NULL DEFAULT '',
            modality TEXT NOT NULL DEFAULT '',
            adapter TEXT NOT NULL DEFAULT '',
            operation_kind TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            provider_task_id TEXT,
            next_reconcile_at TEXT,
            projection_pending INTEGER NOT NULL DEFAULT 0
                CHECK (projection_pending IN (0, 1)),
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_jobs_session "
        "ON creative_media_jobs (session_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_jobs_status "
        "ON creative_media_jobs (status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_jobs_adapter "
        "ON creative_media_jobs (adapter, status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_jobs_reconcile_due "
        "ON creative_media_jobs (next_reconcile_at, adapter, status) "
        "WHERE next_reconcile_at IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_jobs_projection_pending "
        "ON creative_media_jobs (projection_pending, next_reconcile_at) "
        "WHERE projection_pending = 1"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_media_job_lifecycle (
            job_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '',
            detail_code TEXT NOT NULL DEFAULT '',
            remote_task_may_continue INTEGER
                CHECK (remote_task_may_continue IN (0, 1)),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (job_id, phase),
            FOREIGN KEY (job_id) REFERENCES creative_media_jobs(job_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_lifecycle_status "
        "ON creative_media_job_lifecycle (phase, status, updated_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_media_job_projections (
            job_id TEXT NOT NULL,
            projection_kind TEXT NOT NULL,
            state TEXT NOT NULL,
            projection_pending INTEGER NOT NULL DEFAULT 0
                CHECK (projection_pending IN (0, 1)),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            next_attempt_at TEXT,
            proof_digest TEXT,
            detail_code TEXT NOT NULL DEFAULT '',
            last_error TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            projected_at TEXT,
            PRIMARY KEY (job_id, projection_kind),
            FOREIGN KEY (job_id) REFERENCES creative_media_jobs(job_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_projections_pending "
        "ON creative_media_job_projections "
        "(projection_pending, next_attempt_at, updated_at) "
        "WHERE projection_pending = 1"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_media_cost_entries (
            entry_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            operation_kind TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_cost_job "
        "ON creative_media_cost_entries (job_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_cost_session "
        "ON creative_media_cost_entries (session_id, created_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_media_quality_jobs (
            quality_job_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            quality_profile TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    quality_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(creative_media_quality_jobs)").fetchall()
    }
    if "revision" not in quality_columns:
        conn.execute(
            "ALTER TABLE creative_media_quality_jobs "
            "ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_quality_job "
        "ON creative_media_quality_jobs (job_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_quality_status "
        "ON creative_media_quality_jobs (status, created_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_media_safety_events (
            event_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            policy TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_safety_job "
        "ON creative_media_safety_events (job_id, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_safety_session "
        "ON creative_media_safety_events (session_id, created_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_media_work_orders (
            work_order_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL DEFAULT '',
            requesting_runtime TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT,
            deleted_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_work_orders_session "
        "ON creative_media_work_orders (session_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_work_orders_status "
        "ON creative_media_work_orders (status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_work_orders_runtime "
        "ON creative_media_work_orders (requesting_runtime, updated_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_media_terminal_observation_outbox (
            job_id TEXT PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            payload_json TEXT NOT NULL,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (job_id) REFERENCES creative_media_jobs(job_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_creative_media_terminal_observation_pending "
        "ON creative_media_terminal_observation_outbox (state, updated_at, job_id) "
        "WHERE state = 'pending'"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS creative_media_store_migrations (
            source_kind TEXT NOT NULL,
            source_identity TEXT NOT NULL,
            source_digest TEXT NOT NULL,
            imported_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            skip_reasons_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            PRIMARY KEY (source_kind, source_identity, source_digest)
        )
        """
    )
    migration_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(creative_media_store_migrations)").fetchall()
    }
    if "skip_reasons_json" not in migration_columns:
        conn.execute(
            "ALTER TABLE creative_media_store_migrations "
            "ADD COLUMN skip_reasons_json TEXT NOT NULL DEFAULT '{}'"
        )

def backfill_internal_computer_use_probe_sessions(conn: sqlite3.Connection) -> None:
    """Hide legacy Computer Use observe/probe sessions from chat history.

    These sessions are diagnostic runtime probes created without an explicit
    chat session/run. Keep their rows for diagnostics, but mark them as
    internal so Phone/Web history filters do not show them as user chats.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, metadata
        FROM sessions
        WHERE id LIKE 'computer_use:%'
           OR title LIKE 'Computer Use%'
        """
    )
    for row in cursor.fetchall():
        metadata: dict[str, Any] = {}
        raw_metadata = row["metadata"]
        if raw_metadata:
            try:
                metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else dict(raw_metadata)
            except Exception:
                metadata = {}
        runtime = str(metadata.get("runtime") or "").strip()
        trigger_source = str(metadata.get("trigger_source") or metadata.get("triggerSource") or "").strip()
        goal = str(metadata.get("goal") or "").strip().lower()
        is_probe = (
            runtime == "computer_use"
            and trigger_source in {"computer_use_api", "computer_use_compat_http"}
            and goal in {"observe_desktop", "observe_scene:desktop"}
        )
        if not is_probe:
            continue
        updated_metadata = {
            **metadata,
            "hiddenFromHistory": True,
            "internalProbe": True,
            "ephemeral": True,
        }
        if updated_metadata == metadata:
            continue
        cursor.execute(
            "UPDATE sessions SET metadata = ? WHERE id = ?",
            (json.dumps(updated_metadata, ensure_ascii=False), row["id"]),
        )

def backfill_manual_rpa_sessions(conn: sqlite3.Connection) -> None:
    """Hide legacy manual RPA run sessions from normal chat history.

    Manual RPA runs have their own RPA ledger/trace surface. If no
    Supervisor/chat user message created them, they should not appear as
    conversations in Phone/Web history.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, metadata
        FROM sessions
        WHERE id LIKE 'rpa:draft:%'
           OR id LIKE 'rpa:file:%'
        """
    )
    for row in cursor.fetchall():
        metadata: dict[str, Any] = {}
        raw_metadata = row["metadata"]
        if raw_metadata:
            try:
                metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else dict(raw_metadata)
            except Exception:
                metadata = {}
        if metadata.get("hiddenFromHistory") is True:
            continue
        updated_metadata = {
            **metadata,
            "runtime": metadata.get("runtime") or "rpa",
            "hiddenFromHistory": True,
            "manualRpaRun": True,
            "nonChatRun": True,
        }
        cursor.execute(
            "UPDATE sessions SET metadata = ? WHERE id = ?",
            (json.dumps(updated_metadata, ensure_ascii=False), row["id"]),
        )

def ensure_base_tables(conn: sqlite3.Connection) -> None:
    """Execute this schema responsibility on the caller-owned connection."""
    # 1. Sessions Table (Threads)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            user_id TEXT NOT NULL,
            agent_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT -- Store JSON string for extra settings like provider, model
        )
    ''')

    # 2. Messages Table (Append-only History)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            reasoning_content TEXT,
            tool_calls TEXT, -- JSON string
            tool_results TEXT, -- JSON string
            images TEXT, -- JSON string array
            metadata_json TEXT, -- JSON string object
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            agent_id TEXT,
            agent_name TEXT,
            agent_avatar TEXT,
            agent_role_label TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
        )
    ''')

    # 3. System Audit Log Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS system_audit_log (
            id TEXT PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source_type TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS run_records (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            conversation_id TEXT,
            thread_id TEXT,
            user_id TEXT,
            run_type TEXT NOT NULL,
            status TEXT NOT NULL,
            trigger_source TEXT,
            agent_id TEXT,
            workflow_id TEXT,
            channel_type TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            error_message TEXT,
            metadata TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS runtime_events (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT,
            seq INTEGER NOT NULL,
            kind TEXT NOT NULL,
            topic TEXT NOT NULL,
            event_ts TEXT,
            source_json TEXT,
            payload_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS runtime_snapshots (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            latest_seq INTEGER NOT NULL,
            snapshot_type TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS runtime_episodes (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            run_id TEXT,
            parent_episode_id TEXT,
            root_episode_id TEXT,
            kind TEXT NOT NULL,
            state TEXT NOT NULL,
            source TEXT,
            reason TEXT,
            need_json TEXT,
            inputs_json TEXT,
            required_runtime_access_json TEXT,
            handoff_refs_json TEXT,
            continuation_token_json TEXT,
            retry_policy_json TEXT,
            cancel_policy_json TEXT,
            resume_token_json TEXT,
            idempotency_key TEXT,
            deadline_at TEXT,
            compensation_plan_json TEXT,
            target_kind TEXT,
            target_id TEXT,
            lease_generation INTEGER DEFAULT 0,
            result_ref TEXT,
            recoverable INTEGER DEFAULT 1,
            priority INTEGER DEFAULT 0,
            attempt_count INTEGER DEFAULT 0,
            worker_id TEXT,
            lease_expires_at TEXT,
            last_heartbeat_at TEXT,
            last_progress TEXT,
            error_code TEXT,
            error_message TEXT,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL,
            FOREIGN KEY (parent_episode_id) REFERENCES runtime_episodes (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS runtime_episode_events (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL,
            session_id TEXT,
            run_id TEXT,
            topic TEXT NOT NULL,
            state TEXT,
            payload_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (episode_id) REFERENCES runtime_episodes (id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS runtime_episode_queue (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL UNIQUE,
            session_id TEXT,
            run_id TEXT,
            kind TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'queued',
            available_at TEXT,
            locked_by TEXT,
            lease_expires_at TEXT,
            attempt_count INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 1,
            retry_policy_json TEXT,
            last_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (episode_id) REFERENCES runtime_episodes (id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS runtime_episode_handoffs (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL,
            session_id TEXT,
            run_id TEXT,
            kind TEXT,
            status TEXT,
            confidence TEXT,
            compact_summary TEXT,
            refs_json TEXT,
            raw_ref TEXT,
            detail_tool TEXT,
            consumer_hint TEXT,
            payload_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (episode_id) REFERENCES runtime_episodes (id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS runtime_episode_leases (
            id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            state TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at TEXT,
            heartbeat_at TEXT,
            released_at TEXT,
            metadata_json TEXT,
            FOREIGN KEY (episode_id) REFERENCES runtime_episodes (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS chat_canonical_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT,
            ordinal INTEGER NOT NULL,
            role TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            nodes_json TEXT NOT NULL,
            artifacts_json TEXT,
            content_text TEXT,
            reasoning_text TEXT,
            metadata_json TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finalized_at TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS chat_message_deletions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            canonical_message_id TEXT,
            run_id TEXT,
            source TEXT NOT NULL,
            metadata_json TEXT,
            deleted_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS session_lane_records (
            session_id TEXT PRIMARY KEY,
            active_run_id TEXT,
            queued_run_id TEXT,
            blocked_by_run_id TEXT,
            policy TEXT NOT NULL DEFAULT 'queue',
            state TEXT NOT NULL DEFAULT 'idle',
            last_transition TEXT,
            last_transition_ts TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (active_run_id) REFERENCES run_records (id) ON DELETE SET NULL,
            FOREIGN KEY (queued_run_id) REFERENCES run_records (id) ON DELETE SET NULL,
            FOREIGN KEY (blocked_by_run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS session_lane_queue_entries (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT,
            action TEXT NOT NULL,
            policy TEXT NOT NULL,
            active_run_id TEXT,
            interrupted_run_id TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE CASCADE,
            FOREIGN KEY (active_run_id) REFERENCES run_records (id) ON DELETE SET NULL,
            FOREIGN KEY (interrupted_run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS pending_approvals (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            approval_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            request_json TEXT NOT NULL,
            response_json TEXT,
            resume_json TEXT NOT NULL DEFAULT '{}',
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS chat_user_message_queue (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT,
            client_message_id TEXT,
            content TEXT NOT NULL,
            attachments_json TEXT,
            file_urls_json TEXT,
            request_json TEXT,
            state TEXT NOT NULL DEFAULT 'pending',
            ordinal INTEGER NOT NULL DEFAULT 0,
            promoted_at TIMESTAMP,
            injected_at TIMESTAMP,
            consumed_at TIMESTAMP,
            consumed_run_id TEXT,
            cancelled_at TIMESTAMP,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL,
            FOREIGN KEY (consumed_run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS session_coordination_messages (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            message_type TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            target_session_id TEXT NOT NULL,
            source_run_id TEXT,
            target_run_id TEXT,
            source_user_id TEXT,
            intent TEXT NOT NULL,
            authority TEXT NOT NULL,
            authorization_interaction_id TEXT,
            content TEXT NOT NULL,
            summary TEXT,
            context_json TEXT,
            evidence_refs_json TEXT,
            reply_to_message_id TEXT,
            reply_status TEXT,
            hop_count INTEGER NOT NULL DEFAULT 1,
            max_hops INTEGER NOT NULL DEFAULT 2,
            state TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            error_code TEXT,
            metadata_json TEXT,
            expires_at TIMESTAMP,
            authorized_at TIMESTAMP,
            promoted_at TIMESTAMP,
            injected_at TIMESTAMP,
            replied_at TIMESTAMP,
            cancelled_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS network_neighbor_links (
            id TEXT PRIMARY KEY,
            peer_id TEXT NOT NULL UNIQUE,
            local_nickname TEXT,
            remote_nickname TEXT,
            local_role TEXT NOT NULL DEFAULT 'primary',
            remote_role TEXT NOT NULL DEFAULT 'companion',
            trust_status TEXT NOT NULL DEFAULT 'trusted',
            workspace_binding_json TEXT,
            metadata_json TEXT,
            paired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS network_neighbor_messages (
            id TEXT PRIMARY KEY,
            link_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            direction TEXT NOT NULL,
            from_peer_id TEXT NOT NULL,
            from_nickname TEXT,
            role TEXT,
            body TEXT NOT NULL,
            preview TEXT,
            status TEXT NOT NULL DEFAULT 'stored',
            run_id TEXT,
            workspace_binding_json TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (link_id) REFERENCES network_neighbor_links (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS network_neighbor_wake_queue (
            id TEXT PRIMARY KEY,
            link_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'queued',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            available_at TIMESTAMP,
            claimed_by TEXT,
            lease_expires_at TIMESTAMP,
            last_error TEXT,
            payload_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            failed_at TIMESTAMP,
            FOREIGN KEY (link_id) REFERENCES network_neighbor_links (id) ON DELETE CASCADE,
            FOREIGN KEY (message_id) REFERENCES network_neighbor_messages (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS network_neighbor_tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            target_mode TEXT NOT NULL DEFAULT 'auto',
            origin_session_id TEXT,
            origin_run_id TEXT,
            wake_policy TEXT NOT NULL DEFAULT 'inbox',
            required_capabilities_json TEXT,
            workspace_binding_json TEXT,
            metadata_json TEXT,
            deadline_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS network_neighbor_assignments (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            link_id TEXT NOT NULL,
            peer_id TEXT NOT NULL,
            parent_assignment_id TEXT,
            depth INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'queued',
            body TEXT NOT NULL,
            required_capabilities_json TEXT,
            wake_policy TEXT NOT NULL DEFAULT 'inbox',
            run_id TEXT,
            result_id TEXT,
            error TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES network_neighbor_tasks (id) ON DELETE CASCADE,
            FOREIGN KEY (link_id) REFERENCES network_neighbor_links (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS network_neighbor_task_results (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL,
            link_id TEXT NOT NULL,
            peer_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            summary TEXT,
            body TEXT,
            needs_attention INTEGER NOT NULL DEFAULT 0,
            requested_capabilities_json TEXT,
            handoff_reason TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES network_neighbor_tasks (id) ON DELETE CASCADE,
            FOREIGN KEY (assignment_id) REFERENCES network_neighbor_assignments (id) ON DELETE CASCADE,
            FOREIGN KEY (link_id) REFERENCES network_neighbor_links (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS network_relay_outbox (
            id TEXT PRIMARY KEY,
            target_peer_id TEXT NOT NULL,
            link_id TEXT,
            local_message_id TEXT,
            envelope_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'queued',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            available_at TIMESTAMP,
            claimed_by TEXT,
            lease_expires_at TIMESTAMP,
            relay_message_id TEXT,
            last_error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            published_at TIMESTAMP,
            failed_at TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS network_relay_inbox_cursor (
            peer_id TEXT PRIMARY KEY,
            cursor TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS network_relay_delivery_acks (
            id TEXT PRIMARY KEY,
            peer_id TEXT NOT NULL,
            relay_message_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'acked',
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            acked_at TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS network_relay_dead_letters (
            id TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            peer_id TEXT,
            relay_message_id TEXT,
            outbox_id TEXT,
            envelope_json TEXT,
            reason TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS skill_safety_reviews (
            id TEXT PRIMARY KEY,
            skill_id TEXT,
            skill_name TEXT,
            skill_path TEXT NOT NULL,
            instruction_path TEXT,
            identity_key TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            manifest_hash TEXT,
            static_verdict TEXT,
            effective_verdict TEXT NOT NULL,
            user_override TEXT,
            disabled INTEGER DEFAULT 0,
            scan_payload_json TEXT,
            llm_review_json TEXT,
            reasons_json TEXT,
            flagged_files_json TEXT,
            finding_categories_json TEXT,
            reviewed_at TIMESTAMP,
            approved_at TIMESTAMP,
            disabled_at TIMESTAMP,
            revoked_at TIMESTAMP,
            active INTEGER DEFAULT 1,
            orphaned_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_safety_identity_hash ON skill_safety_reviews (identity_key, content_hash)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_skill_safety_disabled ON skill_safety_reviews (disabled, updated_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_skill_safety_skill_id ON skill_safety_reviews (skill_id)')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS safety_allowlist_entries (
            id TEXT PRIMARY KEY,
            normalized_target_hash TEXT NOT NULL,
            normalized_target_label TEXT,
            path_plane TEXT NOT NULL,
            runtime_source TEXT NOT NULL,
            action TEXT NOT NULL,
            risk_code TEXT NOT NULL,
            governance_target TEXT,
            approval_id TEXT,
            approval_kind TEXT,
            source TEXT,
            enabled INTEGER DEFAULT 1,
            metadata_json TEXT,
            revoked_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_safety_allowlist_key
        ON safety_allowlist_entries (normalized_target_hash, path_plane, runtime_source, action, risk_code)
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_safety_allowlist_enabled ON safety_allowlist_entries (enabled, updated_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_safety_allowlist_approval ON safety_allowlist_entries (approval_id)')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS ask_user_interactions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            assistant_message_id TEXT,
            tool_call_id TEXT,
            question TEXT,
            prompt TEXT,
            request_json TEXT NOT NULL,
            answer_text TEXT,
            status TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE CASCADE,
            FOREIGN KEY (assistant_message_id) REFERENCES chat_canonical_messages (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS workflow_ledgers (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            conversation_id TEXT,
            root_run_id TEXT NOT NULL,
            parent_workflow_id TEXT,
            workflow_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            owner_runtime TEXT,
            owner_agent_id TEXT,
            current_step_id TEXT,
            resume_strategy TEXT,
            recoverable INTEGER DEFAULT 1,
            last_error_code TEXT,
            last_error_message TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (root_run_id) REFERENCES run_records (id) ON DELETE CASCADE,
            FOREIGN KEY (parent_workflow_id) REFERENCES workflow_ledgers (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS workflow_steps (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            run_id TEXT,
            sequence_index INTEGER DEFAULT 0,
            step_key TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            owner_runtime TEXT,
            owner_agent_id TEXT,
            approval_id TEXT,
            input_json TEXT,
            output_json TEXT,
            projection_json TEXT,
            last_event_seq INTEGER DEFAULT 0,
            retry_count INTEGER DEFAULT 0,
            resume_token TEXT,
            last_error_code TEXT,
            last_error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (workflow_id) REFERENCES workflow_ledgers (id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL,
            FOREIGN KEY (approval_id) REFERENCES pending_approvals (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS session_scope_bindings (
            session_id TEXT PRIMARY KEY,
            conversation_id TEXT,
            thread_id TEXT,
            user_id TEXT,
            workspace_id TEXT,
            workspace_path TEXT,
            project_id TEXT,
            workflow_id TEXT,
            channel_type TEXT,
            channel_remote_id TEXT,
            scope_hint TEXT,
            resolved_scope TEXT NOT NULL,
            scope_source TEXT NOT NULL,
            scope_confidence REAL DEFAULT 1.0,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS workspace_project_bindings (
            workspace_id TEXT PRIMARY KEY,
            workspace_path TEXT NOT NULL,
            project_id TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS scope_resolution_events (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT,
            requested_scope TEXT,
            resolved_scope TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            evidence_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS project_descriptors_cache (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            workspace_id TEXT,
            workspace_path TEXT,
            default_scope TEXT NOT NULL,
            tags_json TEXT,
            active INTEGER DEFAULT 1,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_extraction_state (
            session_id TEXT PRIMARY KEY,
            last_processed_message_id TEXT,
            last_processed_message_count INTEGER DEFAULT 0,
            last_content_hash TEXT,
            last_run_id TEXT,
            last_processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_workflow_episodes (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            run_id TEXT,
            scope TEXT DEFAULT 'global',
            task_family TEXT,
            task_family_signature TEXT NOT NULL,
            initial_user_intent TEXT,
            first_action_signature TEXT,
            runtime_lane TEXT,
            ordered_actions_json TEXT,
            tool_skill_sequence_json TEXT,
            failure_markers_json TEXT,
            user_correction_points_json TEXT,
            final_success_evidence TEXT,
            user_verdict TEXT,
            side_effect_scope TEXT,
            privacy_scope TEXT,
            status TEXT DEFAULT 'candidate',
            confidence REAL DEFAULT 0.5,
            extraction_source TEXT,
            workflow_class TEXT DEFAULT 'general',
            source_runtime TEXT,
            proof_refs_json TEXT,
            verification_backed INTEGER DEFAULT 0,
            workset_risk TEXT,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_workflow_candidates (
            id TEXT PRIMARY KEY,
            task_family_signature TEXT NOT NULL UNIQUE,
            task_family TEXT,
            scope TEXT DEFAULT 'global',
            canonical_trigger_patterns_json TEXT,
            first_action_triggers_json TEXT,
            golden_path_steps_json TEXT,
            anti_patterns_json TEXT,
            verification_steps_json TEXT,
            success_count INTEGER DEFAULT 0,
            correction_count INTEGER DEFAULT 0,
            negative_feedback_count INTEGER DEFAULT 0,
            maturity_score REAL DEFAULT 0,
            status TEXT DEFAULT 'candidate',
            confidence REAL DEFAULT 0.5,
             source_episode_ids_json TEXT,
            risk_tier TEXT DEFAULT 'low',
            approval_required INTEGER DEFAULT 0,
            last_hint_outcome TEXT,
            guide_state_json TEXT,
            merge_suggestion_json TEXT,
            workflow_class TEXT DEFAULT 'general',
            source_runtime TEXT,
            proof_backed INTEGER DEFAULT 0,
            verification_backed INTEGER DEFAULT 0,
            last_verification_status TEXT,
            workset_risk TEXT,
            outside_write_set_count INTEGER DEFAULT 0,
            manual_override_count INTEGER DEFAULT 0,
            proof_entry_ids_json TEXT,
            last_seen_at TEXT,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_workflow_hint_events (
            id TEXT PRIMARY KEY,
            candidate_id TEXT,
            session_id TEXT,
            run_id TEXT,
            query TEXT,
            injected_hint_json TEXT,
            outcome TEXT DEFAULT 'injected',
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (candidate_id) REFERENCES memory_workflow_candidates (id) ON DELETE SET NULL,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_workflow_guide_states (
            id TEXT PRIMARY KEY,
            candidate_id TEXT,
            session_id TEXT,
            run_id TEXT,
            query TEXT,
            state TEXT DEFAULT 'matched',
            current_step_index INTEGER DEFAULT 0,
            last_event_topic TEXT,
            outcome TEXT,
            is_current INTEGER DEFAULT 1,
            expires_at TEXT,
            finalized_at TEXT,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (candidate_id) REFERENCES memory_workflow_candidates (id) ON DELETE SET NULL,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS engineering_proof_entries (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            run_id TEXT,
            task_brief_id TEXT,
            mode TEXT DEFAULT 'dry_run',
            patch_intent TEXT,
            read_set_json TEXT,
            write_set_json TEXT,
            changed_files_json TEXT,
            commands_json TEXT,
            diagnostics_json TEXT,
            verification_status TEXT DEFAULT 'unverified',
            residual_risks_json TEXT,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('CREATE INDEX IF NOT EXISTS idx_engineering_proof_entries_session_id ON engineering_proof_entries (session_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_engineering_proof_entries_run_id ON engineering_proof_entries (run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_engineering_proof_entries_status ON engineering_proof_entries (verification_status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_engineering_proof_entries_created_at ON engineering_proof_entries (created_at DESC)')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS engineering_workset_observations (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            run_id TEXT,
            task_brief_id TEXT,
            delegation_id TEXT,
            decision_source TEXT DEFAULT 'supervisor_auto',
            phase TEXT DEFAULT 'dispatch',
            decision_json TEXT,
            warning_or_block_reason TEXT,
            manual_override INTEGER DEFAULT 0,
            outside_write_set_files_json TEXT,
            correlation_status TEXT,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('CREATE INDEX IF NOT EXISTS idx_engineering_workset_observations_session_id ON engineering_workset_observations (session_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_engineering_workset_observations_run_id ON engineering_workset_observations (run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_engineering_workset_observations_task_brief_id ON engineering_workset_observations (task_brief_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_engineering_workset_observations_created_at ON engineering_workset_observations (created_at DESC)')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS model_invocation_logs (
            id TEXT PRIMARY KEY,
            run_id TEXT,
            session_id TEXT,
            provider_id TEXT,
            provider_name TEXT,
            model_id TEXT NOT NULL,
            role TEXT,
            capability_class TEXT,
            request_kind TEXT,
            status TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost_input REAL DEFAULT 0,
            cost_output REAL DEFAULT 0,
            cost_total REAL DEFAULT 0,
            latency_ms REAL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            is_streaming INTEGER DEFAULT 0,
            metadata_json TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS usage_ledger (
            id TEXT PRIMARY KEY,
            bucket_date TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            provider_id TEXT,
            model_id TEXT NOT NULL,
            role TEXT,
            capability_class TEXT,
            invocations INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost_total REAL DEFAULT 0,
            latency_ms_total REAL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS provider_health_logs (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            provider_name TEXT,
            model_id TEXT,
            run_id TEXT,
            session_id TEXT,
            status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            latency_ms REAL DEFAULT 0,
            detail_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS prompt_cache_events (
            id TEXT PRIMARY KEY,
            provider_id TEXT,
            model_id TEXT,
            model_ref TEXT,
            role TEXT,
            profile_id TEXT,
            static_prefix_key TEXT,
            response_cache_key TEXT,
            decision TEXT NOT NULL,
            skip_reason TEXT,
            provider_patch_json TEXT,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS prompt_cache_segments (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            segment_type TEXT NOT NULL,
            source TEXT,
            content_hash TEXT NOT NULL,
            char_count INTEGER DEFAULT 0,
            estimated_tokens INTEGER DEFAULT 0,
            metadata_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES prompt_cache_events (id) ON DELETE CASCADE
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS llm_response_cache (
            response_cache_key TEXT PRIMARY KEY,
            static_prefix_key TEXT,
            provider_id TEXT,
            model_id TEXT,
            model_ref TEXT,
            role TEXT,
            response_body_json TEXT NOT NULL,
            metadata_json TEXT,
            hit_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS runtime_artifacts (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            run_id TEXT,
            message_id TEXT,
            resource_role TEXT NOT NULL DEFAULT 'artifact',
            source_id TEXT,
            auto_attach_to_message INTEGER NOT NULL DEFAULT 1,
            artifact_kind TEXT NOT NULL,
            mime_type TEXT,
            title TEXT,
            source_path TEXT,
            workspace_path TEXT,
            external_url TEXT,
            preview_url TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE SET NULL,
            FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL,
            FOREIGN KEY (message_id) REFERENCES messages (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS managed_git_repositories (
            repository_id TEXT PRIMARY KEY,
            project_id TEXT,
            original_workspace_root TEXT NOT NULL,
            repository_root TEXT NOT NULL,
            workspace_relative_path TEXT NOT NULL DEFAULT '.',
            state TEXT NOT NULL,
            head_commit TEXT,
            default_branch TEXT,
            initialized_by_v8os INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.execute("DROP INDEX IF EXISTS idx_managed_git_repository_root")
    conn.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_managed_git_workspace_topology
        ON managed_git_repositories(repository_root, original_workspace_root)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS engineering_worktrees (
            worktree_id TEXT PRIMARY KEY,
            repository_id TEXT NOT NULL,
            session_id TEXT,
            run_id TEXT,
            delegation_id TEXT,
            parent_worktree_id TEXT,
            worktree_kind TEXT NOT NULL DEFAULT 'task',
            branch_name TEXT NOT NULL,
            base_commit TEXT NOT NULL,
            worktree_root TEXT NOT NULL,
            worktree_workspace_root TEXT NOT NULL,
            state TEXT NOT NULL,
            change_set_json TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY (repository_id) REFERENCES managed_git_repositories(repository_id) ON DELETE RESTRICT
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_engineering_worktrees_run_state
        ON engineering_worktrees(run_id, state, created_at)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sandbox_execution_leases (
            lease_id TEXT PRIMARY KEY,
            policy_id TEXT NOT NULL,
            policy_digest TEXT NOT NULL,
            write_set_digest TEXT NOT NULL,
            repository_id TEXT NOT NULL,
            worktree_id TEXT NOT NULL,
            session_id TEXT,
            run_id TEXT,
            delegation_id TEXT,
            actor_role TEXT NOT NULL,
            runtime_kind TEXT NOT NULL,
            execution_mode TEXT NOT NULL,
            network_profile TEXT NOT NULL,
            state TEXT NOT NULL,
            capabilities_json TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            error_code TEXT,
            created_at TEXT NOT NULL,
            activated_at TEXT,
            expires_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (repository_id) REFERENCES managed_git_repositories(repository_id) ON DELETE RESTRICT,
            FOREIGN KEY (worktree_id) REFERENCES engineering_worktrees(worktree_id) ON DELETE RESTRICT
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_sandbox_execution_leases_run_state
        ON sandbox_execution_leases(run_id, state, created_at)
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS session_sources (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            message_id TEXT,
            source_kind TEXT NOT NULL,
            mime_type TEXT,
            title TEXT,
            workspace_path TEXT,
            external_url TEXT,
            preview_url TEXT,
            resource_ref_json TEXT,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
            FOREIGN KEY (message_id) REFERENCES messages (id) ON DELETE SET NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS workspace_media_assets (
            asset_id TEXT PRIMARY KEY,
            workspace_key TEXT NOT NULL,
            workspace_id TEXT,
            project_id TEXT,
            workspace_relative_path TEXT NOT NULL,
            title TEXT NOT NULL,
            media_type TEXT NOT NULL,
            mime_type TEXT,
            byte_size INTEGER,
            content_sha256 TEXT,
            origin_kind TEXT NOT NULL,
            origin_id TEXT,
            origin_session_id TEXT,
            origin_run_id TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        )
    ''')
    conn.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_media_asset_locator
        ON workspace_media_assets(workspace_key, workspace_relative_path)
        WHERE deleted_at IS NULL
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_workspace_media_assets_workspace
        ON workspace_media_assets(workspace_key, updated_at DESC)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS workspace_media_folders (
            folder_id TEXT PRIMARY KEY,
            workspace_key TEXT NOT NULL,
            workspace_id TEXT,
            project_id TEXT,
            parent_folder_id TEXT,
            folder_kind TEXT NOT NULL DEFAULT 'custom',
            title TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (parent_folder_id) REFERENCES workspace_media_folders(folder_id) ON DELETE CASCADE
        )
    ''')
    conn.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_media_folder_sibling
        ON workspace_media_folders(workspace_key, COALESCE(parent_folder_id, ''), title)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS workspace_media_folder_items (
            asset_id TEXT PRIMARY KEY,
            folder_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (asset_id) REFERENCES workspace_media_assets(asset_id) ON DELETE CASCADE,
            FOREIGN KEY (folder_id) REFERENCES workspace_media_folders(folder_id) ON DELETE CASCADE
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_workspace_media_folder_items_folder
        ON workspace_media_folder_items(folder_id, updated_at DESC)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS session_media_asset_uses (
            session_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            canvas_node_id TEXT,
            context_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (session_id, asset_id),
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (asset_id) REFERENCES workspace_media_assets(asset_id) ON DELETE CASCADE
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_session_media_asset_uses_asset
        ON session_media_asset_uses(asset_id, updated_at DESC)
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS creative_canvas_graphs (
            graph_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL UNIQUE,
            workspace_key TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 3,
            revision INTEGER NOT NULL DEFAULT 1,
            graph_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_creative_canvas_graphs_workspace
        ON creative_canvas_graphs(workspace_key, updated_at DESC)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS creative_canvas_graph_runs (
            graph_run_id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            chat_run_id TEXT,
            canvas_operation_id TEXT NOT NULL,
            graph_revision INTEGER NOT NULL,
            target_node_ids_json TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            node_states_json TEXT NOT NULL,
            status TEXT NOT NULL,
            current_node_id TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE (session_id, canvas_operation_id),
            FOREIGN KEY (graph_id) REFERENCES creative_canvas_graphs(graph_id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (chat_run_id) REFERENCES run_records(id) ON DELETE SET NULL
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_creative_canvas_graph_runs_session
        ON creative_canvas_graph_runs(session_id, updated_at DESC)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS creative_canvas_node_outputs (
            output_version_id TEXT PRIMARY KEY,
            graph_run_id TEXT NOT NULL,
            graph_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            action_node_id TEXT NOT NULL,
            result_node_id TEXT NOT NULL,
            version_index INTEGER NOT NULL,
            artifact_id TEXT,
            job_id TEXT,
            media_type TEXT,
            output_slot TEXT,
            config_digest TEXT NOT NULL,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (graph_id, result_node_id, version_index),
            FOREIGN KEY (graph_run_id) REFERENCES creative_canvas_graph_runs(graph_run_id) ON DELETE CASCADE,
            FOREIGN KEY (graph_id) REFERENCES creative_canvas_graphs(graph_id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_creative_canvas_node_outputs_result
        ON creative_canvas_node_outputs(graph_id, result_node_id, created_at DESC)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS creative_canvas_commands (
            command_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            command_id TEXT NOT NULL UNIQUE,
            graph_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            base_revision INTEGER NOT NULL,
            result_revision INTEGER NOT NULL,
            direction TEXT NOT NULL,
            command_kind TEXT NOT NULL,
            target_command_id TEXT,
            affected_node_ids_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            inverse_json TEXT NOT NULL,
            before_graph_json TEXT NOT NULL,
            after_graph_json TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'human',
            created_at TEXT NOT NULL,
            FOREIGN KEY (graph_id) REFERENCES creative_canvas_graphs(graph_id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_creative_canvas_commands_graph
        ON creative_canvas_commands(graph_id, command_sequence ASC)
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_creative_canvas_commands_session
        ON creative_canvas_commands(session_id, command_sequence DESC)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS workspace_canvas_templates (
            template_id TEXT PRIMARY KEY,
            workspace_key TEXT NOT NULL,
            workspace_id TEXT,
            project_id TEXT,
            title TEXT NOT NULL,
            description TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1,
            revision INTEGER NOT NULL DEFAULT 1,
            template_json TEXT NOT NULL,
            created_from_session_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_canvas_template_title
        ON workspace_canvas_templates(workspace_key, title COLLATE NOCASE)
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_workspace_canvas_templates_workspace
        ON workspace_canvas_templates(workspace_key, updated_at DESC)
    ''')

    # Indexes for fast querying
    conn.execute('CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages (session_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions (user_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON system_audit_log (timestamp DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_source ON system_audit_log (source_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_run_records_session_id ON run_records (session_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_run_records_started_at ON run_records (started_at DESC)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_events_session_seq ON runtime_events (session_id, seq)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_events_run_id ON runtime_events (run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_events_topic ON runtime_events (topic)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_snapshots_session_id ON runtime_snapshots (session_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episodes_session_id ON runtime_episodes (session_id, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episodes_run_id ON runtime_episodes (run_id, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episodes_state ON runtime_episodes (state, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episodes_parent ON runtime_episodes (parent_episode_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episode_events_episode ON runtime_episode_events (episode_id, created_at ASC)')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runtime_episode_message_delivery ON runtime_episode_events (run_id, topic, state, json_extract(payload_json, '$.recipient'), CAST(json_extract(payload_json, '$.deliverySeq') AS INTEGER)) WHERE topic='runtime.episode.message'")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episode_events_session ON runtime_episode_events (session_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episode_queue_state ON runtime_episode_queue (state, priority DESC, available_at ASC, created_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episode_queue_episode ON runtime_episode_queue (episode_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episode_handoffs_episode ON runtime_episode_handoffs (episode_id, created_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episode_leases_episode ON runtime_episode_leases (episode_id, heartbeat_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_canonical_messages_session_id ON chat_canonical_messages (session_id, ordinal ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_canonical_messages_run_id ON chat_canonical_messages (run_id, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_canonical_messages_updated_at ON chat_canonical_messages (updated_at DESC)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_canonical_messages_session_ordinal ON chat_canonical_messages (session_id, ordinal)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_message_deletions_session_message ON chat_message_deletions (session_id, message_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_message_deletions_session_id ON chat_message_deletions (session_id, deleted_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_session_lane_records_active_run_id ON session_lane_records (active_run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_session_lane_records_updated_at ON session_lane_records (updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_session_lane_queue_entries_session_id ON session_lane_queue_entries (session_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_session_lane_queue_entries_run_id ON session_lane_queue_entries (run_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_user_message_queue_session_state ON chat_user_message_queue (session_id, state, ordinal ASC, created_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_user_message_queue_run_id ON chat_user_message_queue (run_id, state, created_at ASC)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_session_coordination_idempotency ON session_coordination_messages (idempotency_key)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_session_coordination_target_state ON session_coordination_messages (target_session_id, state, created_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_session_coordination_source_updated ON session_coordination_messages (source_session_id, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_session_coordination_thread_hop ON session_coordination_messages (thread_id, hop_count ASC, created_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_session_coordination_target_run ON session_coordination_messages (target_run_id, state, updated_at DESC)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_session_coordination_authorization_use ON session_coordination_messages (authorization_interaction_id) WHERE authorization_interaction_id IS NOT NULL')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_links_peer_id ON network_neighbor_links (peer_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_links_updated_at ON network_neighbor_links (updated_at DESC)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_network_neighbor_messages_link_seq ON network_neighbor_messages (link_id, seq)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_messages_link_received ON network_neighbor_messages (link_id, received_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_messages_run_id ON network_neighbor_messages (run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_wake_queue_state ON network_neighbor_wake_queue (state, available_at ASC, created_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_wake_queue_link ON network_neighbor_wake_queue (link_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_wake_queue_run ON network_neighbor_wake_queue (run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_tasks_status ON network_neighbor_tasks (status, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_tasks_origin_session ON network_neighbor_tasks (origin_session_id, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_assignments_task ON network_neighbor_assignments (task_id, created_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_assignments_link ON network_neighbor_assignments (link_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_assignments_status ON network_neighbor_assignments (status, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_task_results_task ON network_neighbor_task_results (task_id, created_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_neighbor_task_results_assignment ON network_neighbor_task_results (assignment_id, created_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_relay_outbox_state ON network_relay_outbox (state, available_at ASC, created_at ASC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_relay_outbox_target_peer ON network_relay_outbox (target_peer_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_relay_outbox_message ON network_relay_outbox (local_message_id)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_network_relay_delivery_acks_message ON network_relay_delivery_acks (relay_message_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_network_relay_dead_letters_peer ON network_relay_dead_letters (peer_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_pending_approvals_session_id ON pending_approvals (session_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ask_user_interactions_session_id ON ask_user_interactions (session_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ask_user_interactions_run_id ON ask_user_interactions (run_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_run_records_status ON run_records (status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_workflow_ledgers_session_id ON workflow_ledgers (session_id, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_workflow_ledgers_root_run_id ON workflow_ledgers (root_run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_workflow_ledgers_status ON workflow_ledgers (status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_workflow_steps_workflow_id ON workflow_steps (workflow_id, sequence_index)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_workflow_steps_session_id ON workflow_steps (session_id, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_workflow_steps_run_id ON workflow_steps (run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ssb_project_id ON session_scope_bindings (project_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ssb_workspace_id ON session_scope_bindings (workspace_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ssb_channel_remote ON session_scope_bindings (channel_type, channel_remote_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ssb_status ON session_scope_bindings (status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_extraction_processed_at ON memory_extraction_state (last_processed_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_episodes_session_id ON memory_workflow_episodes (session_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_episodes_signature ON memory_workflow_episodes (task_family_signature)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_episodes_created_at ON memory_workflow_episodes (created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_candidates_status ON memory_workflow_candidates (status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_candidates_signature ON memory_workflow_candidates (task_family_signature)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_candidates_updated_at ON memory_workflow_candidates (updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_hint_events_candidate_id ON memory_workflow_hint_events (candidate_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_hint_events_created_at ON memory_workflow_hint_events (created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_guide_states_candidate_id ON memory_workflow_guide_states (candidate_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_guide_states_session_run ON memory_workflow_guide_states (session_id, run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_scope_resolution_events_session_id ON scope_resolution_events (session_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_project_descriptors_cache_workspace_id ON project_descriptors_cache (workspace_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_model_invocation_logs_run_id ON model_invocation_logs (run_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_model_invocation_logs_model_id ON model_invocation_logs (model_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_model_invocation_logs_started_at ON model_invocation_logs (started_at DESC)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_ledger_scope_bucket ON usage_ledger (bucket_date, scope_type, scope_id, model_id, role)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_provider_health_logs_provider_id ON provider_health_logs (provider_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_prompt_cache_events_created_at ON prompt_cache_events (created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_prompt_cache_events_prefix_key ON prompt_cache_events (static_prefix_key)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_prompt_cache_events_decision ON prompt_cache_events (decision)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_prompt_cache_segments_event_id ON prompt_cache_segments (event_id, ordinal)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_llm_response_cache_expires_at ON llm_response_cache (expires_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_llm_response_cache_prefix_key ON llm_response_cache (static_prefix_key)')
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS computer_use_fact_ledger (
            id TEXT PRIMARY KEY,
            query_hash TEXT NOT NULL UNIQUE,
            target_kind TEXT,
            canonical_target_json TEXT NOT NULL,
            evidence_json TEXT,
            source TEXT,
            confidence REAL DEFAULT 0,
            ttl_seconds INTEGER DEFAULT 900,
            verified_at REAL,
            expires_at REAL,
            use_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_computer_use_fact_ledger_expires ON computer_use_fact_ledger (expires_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_computer_use_fact_ledger_target ON computer_use_fact_ledger (target_kind, updated_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_artifacts_session_id ON runtime_artifacts (session_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_artifacts_run_id ON runtime_artifacts (run_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_session_sources_session_id ON session_sources (session_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_session_sources_message_id ON session_sources (message_id, created_at ASC)')

    # Plugin Manager owns installation, component provenance, transaction,
    # explicit grants and audit history.  Runtime state belongs in SQLite;
    # config.json only stores policy and catalog refresh settings.
    conn.execute('''
        CREATE TABLE IF NOT EXISTS plugin_installations (
            plugin_id TEXT PRIMARY KEY,
            manifest_version TEXT NOT NULL,
            catalog_revision INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL,
            install_root TEXT,
            external_ownership INTEGER NOT NULL DEFAULT 0,
            configured INTEGER NOT NULL DEFAULT 0,
            online INTEGER NOT NULL DEFAULT 0,
            health_json TEXT,
            installed_at TEXT,
            updated_at TEXT NOT NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_maintenance_cursors (
            phase TEXT PRIMARY KEY,
            cursor_value TEXT,
            cycle_count INTEGER DEFAULT 0,
            last_batch_count INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS plugin_components (
            id TEXT PRIMARY KEY,
            plugin_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            component_type TEXT NOT NULL,
            owned_path TEXT,
            source_url TEXT,
            source_version TEXT,
            content_sha256 TEXT,
            ownership TEXT NOT NULL DEFAULT 'managed',
            state TEXT NOT NULL DEFAULT 'installed',
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(plugin_id, component_id),
            FOREIGN KEY (plugin_id) REFERENCES plugin_installations (plugin_id) ON DELETE CASCADE
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS plugin_install_jobs (
            id TEXT PRIMARY KEY,
            plugin_id TEXT NOT NULL,
            action TEXT NOT NULL,
            state TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 1,
            approval_required INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0,
            plan_json TEXT,
            snapshot_json TEXT,
            result_json TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS plugin_grants (
            id TEXT PRIMARY KEY,
            plugin_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            session_id TEXT NOT NULL,
            run_id TEXT,
            grantee_type TEXT NOT NULL,
            grantee_id TEXT NOT NULL,
            component_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked_at TEXT,
            parent_grant_id TEXT,
            delegation_id TEXT,
            delegation_depth INTEGER,
            grant_source TEXT NOT NULL DEFAULT 'user_reference',
            CHECK(scope IN ('task', 'session')),
            CHECK(grantee_type IN ('supervisor', 'subagent'))
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS plugin_events (
            id TEXT PRIMARY KEY,
            plugin_id TEXT,
            job_id TEXT,
            grant_id TEXT,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            actor_type TEXT,
            actor_id TEXT,
            session_id TEXT,
            run_id TEXT,
            details_json TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS config_broker_transactions (
            id TEXT PRIMARY KEY,
            target_kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            state TEXT NOT NULL,
            owner_id TEXT,
            session_id TEXT,
            run_id TEXT,
            plan_digest TEXT NOT NULL,
            before_json TEXT,
            proposed_json TEXT NOT NULL,
            validation_json TEXT,
            result_json TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            committed_at TEXT,
            rolled_back_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ui_action_requests (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            state TEXT NOT NULL,
            owner_id TEXT,
            session_id TEXT,
            run_id TEXT,
            title TEXT NOT NULL,
            description TEXT,
            target_label TEXT,
            fields_json TEXT NOT NULL,
            handler_type TEXT NOT NULL,
            handler_ref TEXT NOT NULL,
            result_json TEXT,
            error_code TEXT,
            error_message TEXT,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            submitted_at TEXT
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_plugin_components_plugin ON plugin_components (plugin_id, component_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_plugin_jobs_plugin ON plugin_install_jobs (plugin_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_plugin_grants_session ON plugin_grants (session_id, run_id, revoked_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_plugin_events_plugin ON plugin_events (plugin_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_config_broker_transactions_target ON config_broker_transactions (target_kind, target_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_config_broker_transactions_session ON config_broker_transactions (session_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ui_action_requests_session ON ui_action_requests (session_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ui_action_requests_expires ON ui_action_requests (state, expires_at)')

def migrate_legacy_schema(conn: sqlite3.Connection) -> bool:
    """Execute this schema responsibility on the caller-owned connection."""
    # Simple Schema Migration (Adding missing columns if upgrading)
    migration_succeeded = True
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(messages)")
        columns = [row['name'] for row in cursor.fetchall()]
        if 'reasoning_content' not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN reasoning_content TEXT")
        if 'agent_id' not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN agent_id TEXT")
        if 'agent_name' not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN agent_name TEXT")
        if 'agent_avatar' not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN agent_avatar TEXT")
        if 'agent_role_label' not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN agent_role_label TEXT")
        cursor.execute("PRAGMA table_info(skill_safety_reviews)")
        skill_review_columns = [row['name'] for row in cursor.fetchall()]
        if 'active' not in skill_review_columns:
            conn.execute("ALTER TABLE skill_safety_reviews ADD COLUMN active INTEGER DEFAULT 1")
        if 'orphaned_at' not in skill_review_columns:
            conn.execute("ALTER TABLE skill_safety_reviews ADD COLUMN orphaned_at TIMESTAMP")
        if 'images' not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN images TEXT")
        if 'metadata_json' not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN metadata_json TEXT")

        cursor.execute("PRAGMA table_info(engineering_worktrees)")
        engineering_worktree_columns = [row['name'] for row in cursor.fetchall()]
        if engineering_worktree_columns and 'worktree_kind' not in engineering_worktree_columns:
            conn.execute(
                "ALTER TABLE engineering_worktrees "
                "ADD COLUMN worktree_kind TEXT NOT NULL DEFAULT 'task'"
            )

        cursor.execute("PRAGMA table_info(runtime_events)")
        runtime_event_columns = [row['name'] for row in cursor.fetchall()]
        if runtime_event_columns and 'event_ts' not in runtime_event_columns:
            conn.execute("ALTER TABLE runtime_events ADD COLUMN event_ts TEXT")

        cursor.execute("PRAGMA table_info(run_records)")
        run_columns = [row['name'] for row in cursor.fetchall()]
        if run_columns and 'thread_id' not in run_columns:
            conn.execute("ALTER TABLE run_records ADD COLUMN thread_id TEXT")
        if run_columns and 'workflow_id' not in run_columns:
            conn.execute("ALTER TABLE run_records ADD COLUMN workflow_id TEXT")
        if run_columns and 'channel_type' not in run_columns:
            conn.execute("ALTER TABLE run_records ADD COLUMN channel_type TEXT")

        cursor.execute("PRAGMA table_info(runtime_artifacts)")
        artifact_columns = [row['name'] for row in cursor.fetchall()]
        if artifact_columns and 'message_id' not in artifact_columns:
            conn.execute("ALTER TABLE runtime_artifacts ADD COLUMN message_id TEXT")
        if artifact_columns and 'resource_role' not in artifact_columns:
            conn.execute("ALTER TABLE runtime_artifacts ADD COLUMN resource_role TEXT NOT NULL DEFAULT 'artifact'")
        if artifact_columns and 'source_id' not in artifact_columns:
            conn.execute("ALTER TABLE runtime_artifacts ADD COLUMN source_id TEXT")
        if artifact_columns and 'auto_attach_to_message' not in artifact_columns:
            conn.execute("ALTER TABLE runtime_artifacts ADD COLUMN auto_attach_to_message INTEGER NOT NULL DEFAULT 1")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_artifacts_source_id ON runtime_artifacts (source_id, created_at DESC)')

        cursor.execute("PRAGMA table_info(chat_canonical_messages)")
        canonical_columns = [row['name'] for row in cursor.fetchall()]
        if canonical_columns and 'artifacts_json' not in canonical_columns:
            conn.execute("ALTER TABLE chat_canonical_messages ADD COLUMN artifacts_json TEXT")
        if canonical_columns and 'content_text' not in canonical_columns:
            conn.execute("ALTER TABLE chat_canonical_messages ADD COLUMN content_text TEXT")
        if canonical_columns and 'reasoning_text' not in canonical_columns:
            conn.execute("ALTER TABLE chat_canonical_messages ADD COLUMN reasoning_text TEXT")
        if canonical_columns and 'metadata_json' not in canonical_columns:
            conn.execute("ALTER TABLE chat_canonical_messages ADD COLUMN metadata_json TEXT")
        if canonical_columns and 'version' not in canonical_columns:
            conn.execute("ALTER TABLE chat_canonical_messages ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        if canonical_columns and 'updated_at' not in canonical_columns:
            conn.execute("ALTER TABLE chat_canonical_messages ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        if canonical_columns and 'finalized_at' not in canonical_columns:
            conn.execute("ALTER TABLE chat_canonical_messages ADD COLUMN finalized_at TIMESTAMP")

        cursor.execute("PRAGMA table_info(runtime_episodes)")
        runtime_episode_columns = [row['name'] for row in cursor.fetchall()]
        for column_name, ddl in (
            ("retry_policy_json", "ALTER TABLE runtime_episodes ADD COLUMN retry_policy_json TEXT"),
            ("cancel_policy_json", "ALTER TABLE runtime_episodes ADD COLUMN cancel_policy_json TEXT"),
            ("resume_token_json", "ALTER TABLE runtime_episodes ADD COLUMN resume_token_json TEXT"),
            ("idempotency_key", "ALTER TABLE runtime_episodes ADD COLUMN idempotency_key TEXT"),
            ("deadline_at", "ALTER TABLE runtime_episodes ADD COLUMN deadline_at TEXT"),
            ("compensation_plan_json", "ALTER TABLE runtime_episodes ADD COLUMN compensation_plan_json TEXT"),
            ("target_kind", "ALTER TABLE runtime_episodes ADD COLUMN target_kind TEXT"),
            ("target_id", "ALTER TABLE runtime_episodes ADD COLUMN target_id TEXT"),
            ("lease_generation", "ALTER TABLE runtime_episodes ADD COLUMN lease_generation INTEGER DEFAULT 0"),
        ):
            if runtime_episode_columns and column_name not in runtime_episode_columns:
                conn.execute(ddl)
        cursor.execute("PRAGMA table_info(runtime_episode_queue)")
        runtime_episode_queue_columns = [row['name'] for row in cursor.fetchall()]
        for column_name, ddl in (
            ("max_attempts", "ALTER TABLE runtime_episode_queue ADD COLUMN max_attempts INTEGER DEFAULT 1"),
            ("retry_policy_json", "ALTER TABLE runtime_episode_queue ADD COLUMN retry_policy_json TEXT"),
        ):
            if runtime_episode_queue_columns and column_name not in runtime_episode_queue_columns:
                conn.execute(ddl)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episodes_idempotency ON runtime_episodes (idempotency_key)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_runtime_episodes_target ON runtime_episodes (target_kind, target_id)')

        cursor.execute("PRAGMA table_info(memory_workflow_candidates)")
        workflow_candidate_columns = [row['name'] for row in cursor.fetchall()]
        if workflow_candidate_columns and 'risk_tier' not in workflow_candidate_columns:
            conn.execute("ALTER TABLE memory_workflow_candidates ADD COLUMN risk_tier TEXT DEFAULT 'low'")
        if workflow_candidate_columns and 'approval_required' not in workflow_candidate_columns:
            conn.execute("ALTER TABLE memory_workflow_candidates ADD COLUMN approval_required INTEGER DEFAULT 0")
        if workflow_candidate_columns and 'last_hint_outcome' not in workflow_candidate_columns:
            conn.execute("ALTER TABLE memory_workflow_candidates ADD COLUMN last_hint_outcome TEXT")
        if workflow_candidate_columns and 'guide_state_json' not in workflow_candidate_columns:
            conn.execute("ALTER TABLE memory_workflow_candidates ADD COLUMN guide_state_json TEXT")
        if workflow_candidate_columns and 'merge_suggestion_json' not in workflow_candidate_columns:
            conn.execute("ALTER TABLE memory_workflow_candidates ADD COLUMN merge_suggestion_json TEXT")
        for column_name, ddl in (
            ("workflow_class", "ALTER TABLE memory_workflow_candidates ADD COLUMN workflow_class TEXT DEFAULT 'general'"),
            ("source_runtime", "ALTER TABLE memory_workflow_candidates ADD COLUMN source_runtime TEXT"),
            ("proof_backed", "ALTER TABLE memory_workflow_candidates ADD COLUMN proof_backed INTEGER DEFAULT 0"),
            ("verification_backed", "ALTER TABLE memory_workflow_candidates ADD COLUMN verification_backed INTEGER DEFAULT 0"),
            ("last_verification_status", "ALTER TABLE memory_workflow_candidates ADD COLUMN last_verification_status TEXT"),
            ("workset_risk", "ALTER TABLE memory_workflow_candidates ADD COLUMN workset_risk TEXT"),
            ("outside_write_set_count", "ALTER TABLE memory_workflow_candidates ADD COLUMN outside_write_set_count INTEGER DEFAULT 0"),
            ("manual_override_count", "ALTER TABLE memory_workflow_candidates ADD COLUMN manual_override_count INTEGER DEFAULT 0"),
            ("proof_entry_ids_json", "ALTER TABLE memory_workflow_candidates ADD COLUMN proof_entry_ids_json TEXT"),
        ):
            if workflow_candidate_columns and column_name not in workflow_candidate_columns:
                conn.execute(ddl)
        cursor.execute("PRAGMA table_info(memory_workflow_episodes)")
        workflow_episode_columns = [row['name'] for row in cursor.fetchall()]
        for column_name, ddl in (
            ("workflow_class", "ALTER TABLE memory_workflow_episodes ADD COLUMN workflow_class TEXT DEFAULT 'general'"),
            ("source_runtime", "ALTER TABLE memory_workflow_episodes ADD COLUMN source_runtime TEXT"),
            ("proof_refs_json", "ALTER TABLE memory_workflow_episodes ADD COLUMN proof_refs_json TEXT"),
            ("verification_backed", "ALTER TABLE memory_workflow_episodes ADD COLUMN verification_backed INTEGER DEFAULT 0"),
            ("workset_risk", "ALTER TABLE memory_workflow_episodes ADD COLUMN workset_risk TEXT"),
        ):
            if workflow_episode_columns and column_name not in workflow_episode_columns:
                conn.execute(ddl)
        cursor.execute("PRAGMA table_info(memory_workflow_guide_states)")
        workflow_guide_columns = [row['name'] for row in cursor.fetchall()]
        added_is_current = bool(workflow_guide_columns and 'is_current' not in workflow_guide_columns)
        for column_name, ddl in (
            ("is_current", "ALTER TABLE memory_workflow_guide_states ADD COLUMN is_current INTEGER DEFAULT 1"),
            ("expires_at", "ALTER TABLE memory_workflow_guide_states ADD COLUMN expires_at TEXT"),
            ("finalized_at", "ALTER TABLE memory_workflow_guide_states ADD COLUMN finalized_at TEXT"),
        ):
            if workflow_guide_columns and column_name not in workflow_guide_columns:
                conn.execute(ddl)
        if added_is_current:
            conn.execute("UPDATE memory_workflow_guide_states SET is_current = 0")
            conn.execute(
                """
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY candidate_id,
                                            CASE WHEN run_id IS NOT NULL THEN 'run:' || run_id
                                                 ELSE 'session:' || COALESCE(session_id, '') END
                               ORDER BY updated_at DESC, created_at DESC, id DESC
                           ) AS rank_no
                    FROM memory_workflow_guide_states
                )
                UPDATE memory_workflow_guide_states
                SET is_current = 1
                WHERE id IN (SELECT id FROM ranked WHERE rank_no = 1)
                """
            )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_episodes_class ON memory_workflow_episodes (workflow_class)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_candidates_class ON memory_workflow_candidates (workflow_class)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_candidates_source_runtime ON memory_workflow_candidates (source_runtime)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_guide_states_expiry ON memory_workflow_guide_states (is_current, expires_at)')
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_workflow_guide_current_run
            ON memory_workflow_guide_states (candidate_id, run_id)
            WHERE run_id IS NOT NULL AND is_current = 1
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_workflow_guide_current_session
            ON memory_workflow_guide_states (candidate_id, session_id)
            WHERE run_id IS NULL AND session_id IS NOT NULL AND is_current = 1
            """
        )
        # Historical rows predate guide TTL/finalization.  Leaving the
        # new columns NULL would make thousands of stale pending states
        # immortal, so close non-current snapshots and give the one
        # current pending state a deterministic migration TTL.
        conn.execute(
            """
            UPDATE memory_workflow_guide_states
            SET finalized_at = COALESCE(finalized_at, updated_at, created_at, CURRENT_TIMESTAMP),
                expires_at = NULL
            WHERE is_current = 0 AND finalized_at IS NULL
            """
        )
        conn.execute(
            """
            UPDATE memory_workflow_guide_states
            SET finalized_at = COALESCE(finalized_at, updated_at, created_at, CURRENT_TIMESTAMP),
                expires_at = NULL
            WHERE is_current = 1
              AND state IN ('helped', 'ignored', 'conflict', 'failed', 'verified', 'contradicted')
              AND finalized_at IS NULL
            """
        )
        conn.execute(
            """
            UPDATE memory_workflow_guide_states
            SET expires_at = strftime(
                    '%Y-%m-%dT%H:%M:%fZ',
                    datetime(COALESCE(updated_at, created_at, CURRENT_TIMESTAMP), '+72 hours')
                )
            WHERE is_current = 1
              AND state NOT IN ('helped', 'ignored', 'conflict', 'failed', 'verified', 'contradicted')
              AND expires_at IS NULL
              AND finalized_at IS NULL
            """
        )
        backfill_internal_computer_use_probe_sessions(conn)
        backfill_manual_rpa_sessions(conn)
    except Exception as e:
        migration_succeeded = False
        print(f"[Database] Migration note: {e}")

    return migration_succeeded

def initialize_database_schema(
    conn: sqlite3.Connection,
    *,
    supported_schema_version: int,
    repair_known_schema_objects: Callable[[sqlite3.Connection], None],
) -> None:
    """Create/upgrade all tables using one explicit connection boundary."""

    repair_known_schema_objects(conn)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS model_budget_reservations (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            run_id TEXT,
            project_id TEXT,
            provider_id TEXT,
            model_id TEXT,
            bucket_date TEXT NOT NULL,
            estimated_tokens INTEGER NOT NULL,
            estimated_cost REAL NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('reserved','in_flight','settled','released','unknown')),
            actual_tokens INTEGER,
            actual_cost REAL,
            ledger_accounted INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_model_budget_reservations_scope ON model_budget_reservations (state, bucket_date, run_id, project_id)')
    reservation_columns = {row[1] for row in conn.execute('PRAGMA table_info(model_budget_reservations)').fetchall()}
    if 'ledger_accounted' not in reservation_columns:
        conn.execute('ALTER TABLE model_budget_reservations ADD COLUMN ledger_accounted INTEGER NOT NULL DEFAULT 0')
    conn.commit()
    schema_version_row = conn.execute("PRAGMA user_version").fetchone()
    schema_version = int(schema_version_row[0] if schema_version_row else 0)
    if schema_version == supported_schema_version:
        # Safety ledgers were added without a schema bump.  A database
        # created by an earlier v2 binary may therefore have the
        # current version marker but still lack these tables.
        ensure_runtime_safety_tables(conn)
        ensure_creative_media_store_tables(conn)
        ensure_session_command_tables(conn)
        from core.conversation_schema import ensure_conversation_schema
        ensure_conversation_schema(conn)
        conn.commit()
        return
    if schema_version > supported_schema_version:
        raise RuntimeError(
            "Database schema version "
            f"{schema_version} is newer than supported version {supported_schema_version}"
        )
    ensure_runtime_safety_tables(conn)
    ensure_session_command_tables(conn)

    ensure_base_tables(conn)
    ensure_creative_media_store_tables(conn)
    from core.system_operations.schema import ensure_system_operation_tables
    ensure_system_operation_tables(conn)
    migration_succeeded = migrate_legacy_schema(conn)
    if migration_succeeded:
        from core.conversation_schema import ensure_conversation_schema
        ensure_conversation_schema(conn)
        conn.execute(f"PRAGMA user_version = {supported_schema_version}")
    conn.commit()
