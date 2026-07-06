import sqlite3
import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Iterator

from core.json_safe import to_jsonable
from core.multimodal_payload_adapter import normalize_artifact_record
from core.observability_db import ObservabilityDatabaseManager
from core.realtime_protocol import utc_now_iso
from core.time_truth import latest_utc_iso, normalize_utc_iso

class DatabaseManager:
    """
    Manages the SQLite database for V8Chat state persistence.
    Using WAL (Write-Ahead Logging) mode to support concurrent reads and writes
    without locking issues, ideal for replacing the old .jsonl files.
    """
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.observability_db = ObservabilityDatabaseManager(self.db_path.parent / "observability.db")
        self._runtime_write_lock = threading.RLock()
        self._init_db()

    def _run_write_with_retry(self, operation, *, retries: int = 8, lock_timeout_s: float = 0.0):
        delays = [0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5]
        with self._runtime_write_lock:
            for attempt in range(retries):
                try:
                    return operation()
                except sqlite3.OperationalError as exc:
                    if "database is locked" not in str(exc).lower():
                        raise
                    if attempt >= retries - 1:
                        raise
                    time.sleep(delays[min(attempt, len(delays) - 1)] + lock_timeout_s)

    @contextmanager
    def get_connection(self) -> Iterator[sqlite3.Connection]:
        """Returns a new database connection with WAL mode enabled."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for concurrency
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA busy_timeout=30000;')
        # Foreign keys support
        conn.execute('PRAGMA foreign_keys=ON;')
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Initializes the database schema if it doesn't exist."""
        with self.get_connection() as conn:
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
                    decision_source TEXT DEFAULT 'planner_auto',
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
            
            # Simple Schema Migration (Adding missing columns if upgrading)
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
                conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_episodes_class ON memory_workflow_episodes (workflow_class)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_candidates_class ON memory_workflow_candidates (workflow_class)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_workflow_candidates_source_runtime ON memory_workflow_candidates (source_runtime)')
                self._backfill_internal_computer_use_probe_sessions(conn)
                self._backfill_manual_rpa_sessions(conn)
            except Exception as e:
                print(f"[Database] Migration note: {e}")
            
            conn.commit()

    # --- Session Operations ---

    def _is_diagnostic_history_metadata(self, metadata: dict[str, Any], *, user_id: str | None = None, agent_id: str | None = None) -> bool:
        source = str(metadata.get("source") or metadata.get("diagnosticSource") or metadata.get("runtime") or "").strip()
        run_type = str(metadata.get("runType") or metadata.get("run_type") or "").strip()
        user = str(user_id or metadata.get("userId") or metadata.get("user_id") or "").strip()
        agent = str(agent_id or metadata.get("agentId") or metadata.get("agent_id") or "").strip()
        diagnostic_sources = {
            "planner_prompt_cache_live",
            "prompt_cache_streaming_live_matrix",
        }
        return (
            source in diagnostic_sources
            or run_type in diagnostic_sources
            or user == "prompt_cache_live"
            or agent in diagnostic_sources
        )

    def _should_hide_session_from_client_history(self, metadata: dict[str, Any], *, user_id: str | None = None, agent_id: str | None = None) -> bool:
        return bool(
            metadata.get("hiddenFromHistory") is True
            or metadata.get("hidden_from_history") is True
            or metadata.get("nonChatRun") is True
            or metadata.get("non_chat_run") is True
            or metadata.get("internalProbe") is True
            or metadata.get("internal_probe") is True
            or metadata.get("manualRpaRun") is True
            or metadata.get("manual_rpa_run") is True
            or metadata.get("governanceOnly") is True
            or metadata.get("governance_only") is True
            or self._is_diagnostic_history_metadata(metadata, user_id=user_id, agent_id=agent_id)
        )

    def _backfill_internal_computer_use_probe_sessions(self, conn: sqlite3.Connection) -> None:
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

    def _backfill_manual_rpa_sessions(self, conn: sqlite3.Connection) -> None:
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
    
    def create_or_update_session(self, session_id: str, title: str, user_id: str = "anonymous", agent_id: Optional[str] = None, metadata: dict = None):
        """Creates a new session or updates the updated_at timestamp if it exists."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, metadata FROM sessions WHERE id = ?', (session_id,))
            existing = cursor.fetchone()
            merged_metadata = metadata or {}
            if self._is_diagnostic_history_metadata(merged_metadata, user_id=user_id, agent_id=agent_id):
                merged_metadata = {
                    **merged_metadata,
                    "hiddenFromHistory": True,
                    "nonChatRun": True,
                    "diagnosticSession": True,
                }
            if existing:
                current_meta = {}
                if existing["metadata"]:
                    try:
                        current_meta = json.loads(existing["metadata"]) if isinstance(existing["metadata"], str) else dict(existing["metadata"])
                    except Exception:
                        current_meta = {}
                current_meta.update(merged_metadata)
                meta_str = json.dumps(current_meta, ensure_ascii=False)
                now_iso = utc_now_iso()
                if title and title not in ("New Chat", "新对话") and not self._is_internal_runtime_title(title):
                    cursor.execute('''
                        UPDATE sessions 
                        SET updated_at = ?, title = ?, agent_id = COALESCE(?, agent_id), metadata = ?
                        WHERE id = ?
                    ''', (now_iso, title, agent_id, meta_str, session_id))
                else:
                    cursor.execute('''
                        UPDATE sessions 
                        SET updated_at = ?, metadata = ?
                        WHERE id = ?
                    ''', (now_iso, meta_str, session_id))
            else:
                meta_str = json.dumps(merged_metadata, ensure_ascii=False)
                now_iso = utc_now_iso()
                cursor.execute('''
                    INSERT INTO sessions (id, title, user_id, agent_id, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (session_id, title or "新对话", user_id, agent_id, meta_str, now_iso, now_iso))
            conn.commit()

    def _is_internal_runtime_title(self, title: str | None) -> bool:
        normalized = str(title or "").strip()
        return normalized.startswith((
            "Hook · ",
            "Cron · ",
            "Automation · ",
            "Planner · ",
            "Planner lane · ",
            "Planner Prompt Cache Live Audit",
            "Prompt Cache Live Matrix",
        ))

    def _derive_latest_user_session_title(self, conn: sqlite3.Connection, session_id: str) -> Optional[str]:
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT content
            FROM messages
            WHERE session_id = ?
              AND role = 'user'
              AND TRIM(COALESCE(content, '')) != ''
            ORDER BY created_at DESC
            LIMIT 1
            ''',
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        content = str(row["content"] or "").strip()
        if not content:
            return None

        return f"{content[:50]}..." if len(content) > 50 else content

    def get_sessions(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM sessions ORDER BY updated_at DESC')
            sessions: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                data = dict(row)
                data["metadata"] = json.loads(data["metadata"]) if data.get("metadata") else {}
                if self._should_hide_session_from_client_history(
                    data["metadata"],
                    user_id=data.get("user_id"),
                    agent_id=data.get("agent_id"),
                ):
                    continue
                data["created_at"] = normalize_utc_iso(data.get("created_at")) or data.get("created_at")
                data["updated_at"] = normalize_utc_iso(data.get("updated_at")) or data.get("updated_at")

                workflow_cursor = conn.cursor()
                workflow_cursor.execute(
                    '''
                    SELECT
                        wl.id AS workflow_id,
                        wl.root_run_id,
                        wl.status AS workflow_status,
                        wl.owner_runtime AS workflow_owner_runtime,
                        wl.owner_agent_id AS workflow_owner_agent_id,
                        wl.current_step_id,
                        wl.recoverable,
                        wl.updated_at AS workflow_updated_at,
                        ws.id AS step_id,
                        ws.step_key,
                        ws.title AS step_title,
                        ws.status AS step_status,
                        ws.owner_runtime AS step_owner_runtime,
                        ws.owner_agent_id AS step_owner_agent_id,
                        ws.projection_json
                    FROM workflow_ledgers wl
                    LEFT JOIN workflow_steps ws ON ws.id = wl.current_step_id
                    WHERE wl.session_id = ?
                    ORDER BY wl.updated_at DESC
                    LIMIT 1
                    ''',
                    (data["id"],),
                )
                workflow_row = workflow_cursor.fetchone()
                preview_excerpt = None
                workflow_updated_at = None
                if workflow_row:
                    workflow = dict(workflow_row)
                    data["workflowId"] = workflow.get("workflow_id")
                    data["rootRunId"] = workflow.get("root_run_id")
                    data["workflowStatus"] = workflow.get("workflow_status")
                    data["ownerRuntime"] = workflow.get("step_owner_runtime") or workflow.get("workflow_owner_runtime")
                    data["ownerAgentId"] = workflow.get("step_owner_agent_id") or workflow.get("workflow_owner_agent_id")
                    data["currentStepId"] = workflow.get("step_id") or workflow.get("current_step_id")
                    data["currentStepKey"] = workflow.get("step_key")
                    data["currentStepTitle"] = workflow.get("step_title")
                    data["recoverable"] = bool(workflow.get("recoverable"))
                    data["stepStatus"] = workflow.get("step_status")
                    workflow_updated_at = workflow.get("workflow_updated_at")
                    data["workflowUpdatedAt"] = workflow_updated_at
                    projection = json.loads(workflow["projection_json"]) if workflow.get("projection_json") else {}
                    assistant_preview = projection.get("assistant_preview") if isinstance(projection, dict) else None
                    if isinstance(assistant_preview, dict):
                        preview_text = str(assistant_preview.get("content") or "").strip()
                        preview_reasoning = str(assistant_preview.get("reasoningContent") or "").strip()
                        preview_excerpt = preview_text or preview_reasoning or None
                    if preview_excerpt:
                        data["previewExcerpt"] = preview_excerpt[:120]
                        data["hasDurablePreview"] = True
                    else:
                        data["hasDurablePreview"] = False
                else:
                    data["hasDurablePreview"] = False

                activity_cursor = conn.cursor()
                activity_cursor.execute(
                    '''
                    SELECT
                        MAX(COALESCE(event_ts, created_at)) AS latest_runtime_event_at
                    FROM runtime_events
                    WHERE session_id = ?
                    ''',
                    (data["id"],),
                )
                activity_row = activity_cursor.fetchone()
                latest_runtime_event_at = activity_row["latest_runtime_event_at"] if activity_row else None
                if latest_runtime_event_at:
                    data["latestRuntimeEventAt"] = normalize_utc_iso(latest_runtime_event_at) or latest_runtime_event_at

                message_cursor = conn.cursor()
                message_cursor.execute(
                    '''
                    SELECT MAX(created_at) AS latest_message_at
                    FROM messages
                    WHERE session_id = ?
                    ''',
                    (data["id"],),
                )
                message_row = message_cursor.fetchone()
                latest_message_at = message_row["latest_message_at"] if message_row else None
                if latest_message_at:
                    data["latestMessageAt"] = normalize_utc_iso(latest_message_at) or latest_message_at

                canonical_message_cursor = conn.cursor()
                canonical_message_cursor.execute(
                    '''
                    SELECT MAX(created_at) AS latest_canonical_message_at
                    FROM chat_canonical_messages
                    WHERE session_id = ?
                      AND role IN ('user', 'assistant')
                      AND (
                        TRIM(COALESCE(content_text, '')) != ''
                        OR TRIM(COALESCE(reasoning_text, '')) != ''
                        OR TRIM(COALESCE(nodes_json, '')) NOT IN ('', '[]', '{}')
                      )
                    ''',
                    (data["id"],),
                )
                canonical_message_row = canonical_message_cursor.fetchone()
                latest_canonical_message_at = canonical_message_row["latest_canonical_message_at"] if canonical_message_row else None
                if latest_canonical_message_at:
                    data["latestCanonicalMessageAt"] = normalize_utc_iso(latest_canonical_message_at) or latest_canonical_message_at

                if (
                    self._is_internal_runtime_title(data.get("title"))
                    and data.get("ownerRuntime") not in {"automation", "computer_use"}
                    and not str(data.get("id") or "").startswith(("hook:", "cron:", "computer_use:"))
                ):
                    repaired_title = self._derive_latest_user_session_title(conn, data["id"])
                    if repaired_title:
                        data["title"] = repaired_title

                data["workflowUpdatedAt"] = normalize_utc_iso(workflow_updated_at) or workflow_updated_at
                data["lastActivityAt"] = latest_utc_iso(
                    workflow_updated_at,
                    latest_runtime_event_at,
                    latest_message_at,
                    data.get("updated_at"),
                )
                data["historySortAt"] = latest_utc_iso(
                    latest_canonical_message_at,
                    latest_message_at,
                    data.get("created_at"),
                )
                sessions.append(data)

            sessions.sort(key=lambda item: item.get("historySortAt") or item.get("created_at") or "", reverse=True)
            return sessions
            
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM sessions WHERE id = ?', (session_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["metadata"] = json.loads(data["metadata"]) if data.get("metadata") else {}
            data["created_at"] = normalize_utc_iso(data.get("created_at")) or data.get("created_at")
            data["updated_at"] = normalize_utc_iso(data.get("updated_at")) or data.get("updated_at")
            return data

    def delete_session(self, session_id: str):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
            conn.commit()

    # --- Message Operations ---
    
    def add_message(self, msg_id: str, session_id: str, role: str, content: str, reasoning_content: str = None, tool_calls: list = None, tool_results: list = None, images: list = None, metadata: dict = None, agent_id: str = None, agent_name: str = None, agent_avatar: str = None, agent_role_label: str = None):
        """Appends a new message to the session."""
        tc_str = json.dumps(tool_calls, default=str) if tool_calls else None
        tr_str = json.dumps(tool_results, default=str) if tool_results else None
        img_str = json.dumps(images) if images else None
        meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
        now_iso = utc_now_iso()
        with self.get_connection() as conn:
            conn.execute('''
                INSERT INTO messages (id, session_id, role, content, reasoning_content, tool_calls, tool_results, images, metadata_json, agent_id, agent_name, agent_avatar, agent_role_label, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    session_id = excluded.session_id,
                    role = excluded.role,
                    content = excluded.content,
                    reasoning_content = excluded.reasoning_content,
                    tool_calls = excluded.tool_calls,
                    tool_results = excluded.tool_results,
                    images = excluded.images,
                    metadata_json = excluded.metadata_json,
                    agent_id = excluded.agent_id,
                    agent_name = excluded.agent_name,
                    agent_avatar = excluded.agent_avatar,
                    agent_role_label = excluded.agent_role_label
            ''', (msg_id, session_id, role, content, reasoning_content, tc_str, tr_str, img_str, meta_str, agent_id, agent_name, agent_avatar, agent_role_label, now_iso))
            
            # Automatically bump session updated_at
            conn.execute('UPDATE sessions SET updated_at = ? WHERE id = ?', (now_iso, session_id))
            conn.commit()

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC', (session_id,))
            rows = []
            for row in cursor.fetchall():
                d = dict(row)
                if d["tool_calls"]:
                    d["tool_calls"] = json.loads(d["tool_calls"])
                if d["tool_results"]:
                    d["tool_results"] = json.loads(d["tool_results"])
                if d.get("images"):
                    d["images"] = json.loads(d["images"])
                d["metadata"] = json.loads(d["metadata_json"]) if d.get("metadata_json") else {}
                rows.append(d)
            return rows

    def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM messages WHERE id = ?', (message_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            if data.get("tool_calls"):
                data["tool_calls"] = json.loads(data["tool_calls"])
            if data.get("tool_results"):
                data["tool_results"] = json.loads(data["tool_results"])
            if data.get("images"):
                data["images"] = json.loads(data["images"])
            data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
            return data

    def _record_chat_message_deletion(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        message_id: str,
        canonical_message_id: Optional[str] = None,
        run_id: Optional[str] = None,
        source: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        normalized_session_id = str(session_id or "").strip()
        normalized_message_id = str(message_id or "").strip()
        if not normalized_session_id or not normalized_message_id:
            return

        now_iso = utc_now_iso()
        conn.execute(
            '''
            INSERT INTO chat_message_deletions
            (id, session_id, message_id, canonical_message_id, run_id, source, metadata_json, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, message_id) DO UPDATE SET
                canonical_message_id = COALESCE(excluded.canonical_message_id, chat_message_deletions.canonical_message_id),
                run_id = COALESCE(excluded.run_id, chat_message_deletions.run_id),
                source = excluded.source,
                metadata_json = excluded.metadata_json,
                deleted_at = excluded.deleted_at
            ''',
            (
                f"msgdel_{uuid.uuid4().hex}",
                normalized_session_id,
                normalized_message_id,
                canonical_message_id,
                run_id,
                source,
                json.dumps(to_jsonable(metadata or {}), ensure_ascii=False),
                now_iso,
            ),
        )

    def get_deleted_chat_message_ids(self, session_id: str) -> set[str]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return set()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT message_id, canonical_message_id
                FROM chat_message_deletions
                WHERE session_id = ?
                ''',
                (normalized_session_id,),
            )
            deleted: set[str] = set()
            for row in cursor.fetchall():
                for key in ("message_id", "canonical_message_id"):
                    value = str(row[key] or "").strip()
                    if value:
                        deleted.add(value)
            return deleted

    def delete_message(self, message_id: str, *, session_id: Optional[str] = None) -> Dict[str, Any]:
        normalized_message_id = str(message_id or "").strip()
        normalized_session_id = str(session_id or "").strip()
        if not normalized_message_id:
            return {"deleted": False, "message_id": message_id, "session_id": normalized_session_id}

        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('SELECT * FROM messages WHERE id = ?', (normalized_message_id,))
            legacy_row = cursor.fetchone()
            if legacy_row:
                legacy = dict(legacy_row)
                resolved_session_id = str(legacy.get("session_id") or normalized_session_id or "").strip()
                cursor.execute('DELETE FROM messages WHERE id = ?', (normalized_message_id,))
                self._record_chat_message_deletion(
                    conn,
                    session_id=resolved_session_id,
                    message_id=normalized_message_id,
                    run_id=None,
                    source="legacy_message",
                    metadata={"role": legacy.get("role")},
                )
                conn.commit()
                return {
                    "deleted": True,
                    "session_id": resolved_session_id,
                    "message_id": normalized_message_id,
                    "source": "legacy_message",
                    "physical_delete": True,
                }

            cursor.execute('SELECT * FROM chat_canonical_messages WHERE id = ?', (normalized_message_id,))
            canonical_row = cursor.fetchone()
            if canonical_row:
                canonical = dict(canonical_row)
                resolved_session_id = str(canonical.get("session_id") or normalized_session_id or "").strip()
                cursor.execute('DELETE FROM chat_canonical_messages WHERE id = ?', (normalized_message_id,))
                self._record_chat_message_deletion(
                    conn,
                    session_id=resolved_session_id,
                    message_id=normalized_message_id,
                    canonical_message_id=normalized_message_id,
                    run_id=canonical.get("run_id"),
                    source="canonical_message",
                    metadata={"role": canonical.get("role"), "state": canonical.get("state")},
                )
                conn.commit()
                return {
                    "deleted": True,
                    "session_id": resolved_session_id,
                    "message_id": normalized_message_id,
                    "source": "canonical_message",
                    "physical_delete": True,
                }

            if normalized_session_id:
                cursor.execute(
                    '''
                    SELECT *
                    FROM chat_canonical_messages
                    WHERE session_id = ?
                    ORDER BY ordinal ASC, created_at ASC
                    ''',
                    (normalized_session_id,),
                )
                for row in cursor.fetchall():
                    canonical = self._hydrate_chat_canonical_row(dict(row))
                    candidate_ids = {
                        str(canonical.get("id") or "").strip(),
                        str((canonical.get("metadata") or {}).get("messageId") or "").strip(),
                        str((canonical.get("metadata") or {}).get("clientMessageId") or "").strip(),
                    }
                    for node in canonical.get("nodes") or []:
                        if isinstance(node, dict):
                            candidate_ids.add(str(node.get("id") or "").strip())
                    for artifact in canonical.get("artifacts") or []:
                        if isinstance(artifact, dict):
                            candidate_ids.add(str(artifact.get("id") or "").strip())
                            candidate_ids.add(str(artifact.get("messageId") or "").strip())
                    canonical_id = str(canonical.get("id") or "").strip()
                    if normalized_message_id in candidate_ids or (
                        canonical_id and normalized_message_id.startswith(f"{canonical_id}:")
                    ):
                        cursor.execute('DELETE FROM chat_canonical_messages WHERE id = ?', (canonical_id,))
                        self._record_chat_message_deletion(
                            conn,
                            session_id=normalized_session_id,
                            message_id=normalized_message_id,
                            canonical_message_id=canonical_id,
                            run_id=canonical.get("run_id"),
                            source="canonical_projection_alias",
                            metadata={"role": canonical.get("role"), "state": canonical.get("state")},
                        )
                        if normalized_message_id != canonical_id:
                            self._record_chat_message_deletion(
                                conn,
                                session_id=normalized_session_id,
                                message_id=canonical_id,
                                canonical_message_id=canonical_id,
                                run_id=canonical.get("run_id"),
                                source="canonical_projection_alias",
                                metadata={"alias": normalized_message_id},
                            )
                        conn.commit()
                        return {
                            "deleted": True,
                            "session_id": normalized_session_id,
                            "message_id": normalized_message_id,
                            "canonical_message_id": canonical_id,
                            "source": "canonical_projection_alias",
                            "physical_delete": True,
                        }

            cursor.execute('SELECT * FROM runtime_events WHERE id = ?', (normalized_message_id,))
            event_row = cursor.fetchone()
            if event_row:
                event = dict(event_row)
                resolved_session_id = str(event.get("session_id") or normalized_session_id or "").strip()
                self._record_chat_message_deletion(
                    conn,
                    session_id=resolved_session_id,
                    message_id=normalized_message_id,
                    run_id=event.get("run_id"),
                    source="runtime_event_projection",
                    metadata={"topic": event.get("topic")},
                )
                conn.commit()
                return {
                    "deleted": True,
                    "session_id": resolved_session_id,
                    "message_id": normalized_message_id,
                    "source": "runtime_event_projection",
                    "physical_delete": False,
                }

            if normalized_session_id:
                self._record_chat_message_deletion(
                    conn,
                    session_id=normalized_session_id,
                    message_id=normalized_message_id,
                    source="client_projection",
                    metadata={"fallback": True},
                )
                conn.commit()
                return {
                    "deleted": True,
                    "session_id": normalized_session_id,
                    "message_id": normalized_message_id,
                    "source": "client_projection",
                    "physical_delete": False,
                }

            return {"deleted": False, "message_id": normalized_message_id, "session_id": normalized_session_id}

    def get_recent_messages(self, session_id: str, limit: int = 20, role: Optional[str] = None) -> List[Dict[str, Any]]:
        query = 'SELECT * FROM messages WHERE session_id = ?'
        params: list[Any] = [session_id]
        if role:
            query += ' AND role = ?'
            params.append(role)
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = []
            for row in reversed(cursor.fetchall()):
                d = dict(row)
                if d["tool_calls"]:
                    d["tool_calls"] = json.loads(d["tool_calls"])
                if d["tool_results"]:
                    d["tool_results"] = json.loads(d["tool_results"])
                if d.get("images"):
                    d["images"] = json.loads(d["images"])
                d["metadata"] = json.loads(d["metadata_json"]) if d.get("metadata_json") else {}
                rows.append(d)
            return rows

    def update_session_metadata(self, session_id: str, updates: Dict[str, Any]):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT metadata FROM sessions WHERE id = ?', (session_id,))
            row = cursor.fetchone()
            current = {}
            if row and row["metadata"]:
                try:
                    current = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else dict(row["metadata"])
                except Exception:
                    current = {}
            current.update(updates or {})
            conn.execute(
                '''
                UPDATE sessions
                SET metadata = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (json.dumps(current, ensure_ascii=False), session_id),
            )
            conn.commit()

    # --- Canonical Chat Transcript Operations ---

    def get_next_chat_canonical_ordinal(self, session_id: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal FROM chat_canonical_messages WHERE session_id = ?',
                (session_id,),
            )
            row = cursor.fetchone()
            return int(row["next_ordinal"]) if row else 1

    def create_chat_canonical_message(
        self,
        *,
        message_id: str,
        session_id: str,
        run_id: Optional[str],
        ordinal: int,
        role: str,
        state: str,
        nodes: list[dict[str, Any]],
        artifacts: Optional[list[dict[str, Any]]] = None,
        content_text: Optional[str] = None,
        reasoning_text: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        finalized_at: Optional[str] = None,
    ) -> None:
        nodes_str = json.dumps(to_jsonable(nodes or []), ensure_ascii=False)
        artifacts_str = json.dumps(to_jsonable(artifacts or []), ensure_ascii=False)
        metadata_str = json.dumps(to_jsonable(metadata or {}), ensure_ascii=False)
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO chat_canonical_messages
                    (id, session_id, run_id, ordinal, role, state, nodes_json, artifacts_json, content_text, reasoning_text, metadata_json, version, created_at, updated_at, finalized_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ''',
                    (
                        message_id,
                        session_id,
                        run_id,
                        ordinal,
                        role,
                        state,
                        nodes_str,
                        artifacts_str,
                        content_text,
                        reasoning_text,
                        metadata_str,
                        now_iso,
                        now_iso,
                        finalized_at,
                    ),
                )
                conn.execute('UPDATE sessions SET updated_at = ? WHERE id = ?', (now_iso, session_id))
                conn.commit()

        self._run_write_with_retry(_write)

    def update_chat_canonical_message(
        self,
        message_id: str,
        *,
        state: Optional[str] = None,
        nodes: Optional[list[dict[str, Any]]] = None,
        artifacts: Optional[list[dict[str, Any]]] = None,
        content_text: Optional[str] = None,
        reasoning_text: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        finalized_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_chat_canonical_message(message_id)
        if not existing:
            return None
        next_nodes = nodes if nodes is not None else existing.get("nodes") or []
        next_artifacts = artifacts if artifacts is not None else existing.get("artifacts") or []
        next_metadata = metadata if metadata is not None else existing.get("metadata") or {}
        next_state = state or existing.get("state") or "pending"
        next_content = existing.get("content_text") if content_text is None else content_text
        next_reasoning = existing.get("reasoning_text") if reasoning_text is None else reasoning_text
        next_version = int(existing.get("version") or 0) + 1
        finalized_value = finalized_at if finalized_at is not None else existing.get("finalized_at")
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE chat_canonical_messages
                    SET state = ?,
                        nodes_json = ?,
                        artifacts_json = ?,
                        content_text = ?,
                        reasoning_text = ?,
                        metadata_json = ?,
                        version = ?,
                        updated_at = ?,
                        finalized_at = ?
                    WHERE id = ?
                    ''',
                    (
                        next_state,
                        json.dumps(to_jsonable(next_nodes), ensure_ascii=False),
                        json.dumps(to_jsonable(next_artifacts), ensure_ascii=False),
                        next_content,
                        next_reasoning,
                        json.dumps(to_jsonable(next_metadata), ensure_ascii=False),
                        next_version,
                        now_iso,
                        finalized_value,
                        message_id,
                    ),
                )
                session_id = str(existing.get("session_id") or "").strip()
                if session_id:
                    conn.execute('UPDATE sessions SET updated_at = ? WHERE id = ?', (now_iso, session_id))
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_chat_canonical_message(message_id)

    def get_chat_canonical_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM chat_canonical_messages WHERE id = ?', (message_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._hydrate_chat_canonical_row(dict(row))

    def get_chat_canonical_message_by_run(
        self,
        *,
        session_id: str,
        run_id: str,
        role: str = "assistant",
    ) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ? AND run_id = ? AND role = ?
                ORDER BY ordinal DESC, updated_at DESC
                LIMIT 1
                ''',
                (session_id, run_id, role),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._hydrate_chat_canonical_row(dict(row))

    def get_chat_canonical_message_by_client_message_id(
        self,
        *,
        session_id: str,
        client_message_id: str,
        role: str = "user",
    ) -> Optional[Dict[str, Any]]:
        normalized_client_id = str(client_message_id or "").strip()
        if not session_id or not normalized_client_id:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ? AND role = ?
                ORDER BY ordinal DESC, updated_at DESC
                ''',
                (session_id, role),
            )
            for row in cursor.fetchall():
                hydrated = self._hydrate_chat_canonical_row(dict(row))
                metadata = hydrated.get("metadata") if isinstance(hydrated.get("metadata"), dict) else {}
                row_client_id = str(
                    metadata.get("clientMessageId")
                    or metadata.get("client_message_id")
                    or hydrated.get("id")
                    or ""
                ).strip()
                if row_client_id == normalized_client_id:
                    return hydrated
        return None

    def get_chat_canonical_messages(self, session_id: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ?
                  AND id NOT IN (
                    SELECT message_id
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                  AND id NOT IN (
                    SELECT COALESCE(canonical_message_id, '')
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                ORDER BY ordinal ASC, created_at ASC
                ''',
                (session_id, session_id, session_id),
            )
            return [self._hydrate_chat_canonical_row(dict(row)) for row in cursor.fetchall()]

    def get_chat_canonical_messages_before_ordinal(
        self,
        session_id: str,
        before_ordinal: int | None = None,
        *,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 500), 2000))
        ordinal_clause = ""
        params: list[Any] = [session_id]
        if before_ordinal is not None:
            ordinal_clause = "AND ordinal < ?"
            params.append(int(before_ordinal))
        params.extend([session_id, session_id, safe_limit])
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f'''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ?
                  {ordinal_clause}
                  AND id NOT IN (
                    SELECT message_id
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                  AND id NOT IN (
                    SELECT COALESCE(canonical_message_id, '')
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                ORDER BY ordinal DESC, created_at DESC
                LIMIT ?
                ''',
                tuple(params),
            )
            return [self._hydrate_chat_canonical_row(dict(row)) for row in cursor.fetchall()]

    def has_chat_canonical_message_before_ordinal(self, session_id: str, before_ordinal: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT 1
                FROM chat_canonical_messages
                WHERE session_id = ?
                  AND ordinal < ?
                  AND id NOT IN (
                    SELECT message_id
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                  AND id NOT IN (
                    SELECT COALESCE(canonical_message_id, '')
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                LIMIT 1
                ''',
                (session_id, int(before_ordinal), session_id, session_id),
            )
            return cursor.fetchone() is not None

    def get_chat_canonical_messages_since(self, session_id: str, since_ts: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM chat_canonical_messages
                WHERE session_id = ?
                  AND updated_at >= ?
                  AND id NOT IN (
                    SELECT message_id
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                  AND id NOT IN (
                    SELECT COALESCE(canonical_message_id, '')
                    FROM chat_message_deletions
                    WHERE session_id = ?
                  )
                ORDER BY ordinal ASC, created_at ASC
                ''',
                (session_id, since_ts, session_id, session_id),
            )
            return [self._hydrate_chat_canonical_row(dict(row)) for row in cursor.fetchall()]

    def get_chat_message_deletions_since(self, session_id: str, since_ts: str) -> List[str]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT message_id, canonical_message_id
                FROM chat_message_deletions
                WHERE session_id = ?
                  AND deleted_at >= ?
                ORDER BY deleted_at ASC
                ''',
                (session_id, since_ts),
            )
            deleted_ids: list[str] = []
            seen: set[str] = set()
            for row in cursor.fetchall():
                for key in ("message_id", "canonical_message_id"):
                    deleted_id = str(row[key] or "").strip()
                    if deleted_id and deleted_id not in seen:
                        seen.add(deleted_id)
                        deleted_ids.append(deleted_id)
            return deleted_ids

    def get_chat_canonical_max_version(self, session_id: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COALESCE(MAX(version), 0) AS max_version FROM chat_canonical_messages WHERE session_id = ?',
                (session_id,),
            )
            row = cursor.fetchone()
            return int(row["max_version"]) if row else 0

    def _hydrate_chat_canonical_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["nodes"] = json.loads(data["nodes_json"]) if data.get("nodes_json") else []
        data["artifacts"] = json.loads(data["artifacts_json"]) if data.get("artifacts_json") else []
        data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
        return data

    # --- Runtime Event / Run Operations ---

    def create_run_record(
        self,
        run_id: str,
        session_id: str,
        conversation_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        user_id: Optional[str] = None,
        run_type: str = "chat",
        status: str = "running",
        trigger_source: Optional[str] = None,
        agent_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        channel_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        meta_str = json.dumps(metadata) if metadata else None
        started_at = utc_now_iso()
        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT OR REPLACE INTO run_records
                    (id, session_id, conversation_id, thread_id, user_id, run_type, status, trigger_source, agent_id, workflow_id, channel_type, metadata, started_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        run_id,
                        session_id,
                        conversation_id or session_id,
                        thread_id,
                        user_id,
                        run_type,
                        status,
                        trigger_source,
                        agent_id,
                        workflow_id,
                        channel_type,
                        meta_str,
                        started_at,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def update_run_record(
        self,
        run_id: str,
        *,
        status: str,
        error_message: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        meta_str = json.dumps(metadata) if metadata else None
        terminal = status in {"completed", "failed", "cancelled"}
        finished_at = utc_now_iso() if terminal else None
        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE run_records
                    SET status = ?,
                        error_message = CASE
                            WHEN ? IN ('completed', 'failed', 'cancelled') THEN COALESCE(?, error_message)
                            ELSE ?
                        END,
                        metadata = COALESCE(?, metadata),
                        finished_at = CASE
                            WHEN ? IN ('completed', 'failed', 'cancelled') THEN COALESCE(?, finished_at)
                            ELSE NULL
                        END
                    WHERE id = ?
                    ''',
                    (
                        status,
                        status,
                        error_message,
                        error_message,
                        meta_str,
                        status,
                        finished_at,
                        run_id,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def update_run_record_if_status(
        self,
        run_id: str,
        *,
        expected_statuses: set[str],
        status: str,
        error_message: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Dict[str, Any]:
        normalized_expected = {str(item or "").strip() for item in set(expected_statuses or set()) if str(item or "").strip()}
        meta_str = json.dumps(metadata) if metadata else None
        terminal = status in {"completed", "failed", "cancelled"}
        finished_at = utc_now_iso() if terminal else None

        def _write():
            with self.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM run_records WHERE id = ?", (run_id,)).fetchone()
                if not row:
                    conn.rollback()
                    return {"updated": False, "reason": "run_not_found"}
                run_record = dict(row)
                current_status = str(run_record.get("status") or "").strip()
                if normalized_expected and current_status not in normalized_expected:
                    conn.rollback()
                    return {
                        "updated": False,
                        "reason": f"status_mismatch:{current_status or 'unknown'}",
                        "currentStatus": current_status,
                        "run_record": run_record,
                    }
                conn.execute(
                    '''
                    UPDATE run_records
                    SET status = ?,
                        error_message = CASE
                            WHEN ? IN ('completed', 'failed', 'cancelled') THEN COALESCE(?, error_message)
                            ELSE ?
                        END,
                        metadata = COALESCE(?, metadata),
                        finished_at = CASE
                            WHEN ? IN ('completed', 'failed', 'cancelled') THEN COALESCE(?, finished_at)
                            ELSE NULL
                        END
                    WHERE id = ?
                    ''',
                    (
                        status,
                        status,
                        error_message,
                        error_message,
                        meta_str,
                        status,
                        finished_at,
                        run_id,
                    ),
                )
                conn.commit()
                refreshed = dict(conn.execute("SELECT * FROM run_records WHERE id = ?", (run_id,)).fetchone() or {})
                return {"updated": True, "run_record": refreshed, "previousStatus": current_status}

        return self._run_write_with_retry(_write)

    def update_run_metadata_key_if_state(
        self,
        run_id: str,
        *,
        key: str,
        expected_state: str,
        next_value: Dict[str, Any],
        expected_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        marker_key = str(key or "").strip()
        if not marker_key:
            raise ValueError("metadata key is required")
        expected_marker_state = str(expected_state or "").strip().lower()
        expected_run_status = str(expected_status or "").strip()

        def _parse_metadata(raw: Any) -> Dict[str, Any]:
            if not raw:
                return {}
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}

        def _marker_state(metadata: Dict[str, Any]) -> str:
            marker = metadata.get(marker_key)
            if not isinstance(marker, dict):
                return ""
            return str(marker.get("state") or "").strip().lower()

        def _write():
            with self.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM run_records WHERE id = ?", (run_id,)).fetchone()
                if not row:
                    conn.rollback()
                    return {"updated": False, "reason": "run_not_found"}
                run_record = dict(row)
                status = str(run_record.get("status") or "").strip()
                if expected_run_status and status != expected_run_status:
                    conn.rollback()
                    return {
                        "updated": False,
                        "reason": f"run_status_mismatch:{status or 'unknown'}",
                        "currentStatus": status,
                    }
                metadata = _parse_metadata(run_record.get("metadata"))
                current_state = _marker_state(metadata)
                if current_state != expected_marker_state:
                    conn.rollback()
                    return {
                        "updated": False,
                        "reason": f"metadata_state_mismatch:{current_state or 'missing'}",
                        "currentState": current_state,
                        "currentStatus": status,
                    }
                next_metadata = dict(metadata)
                next_metadata[marker_key] = to_jsonable(next_value or {})
                conn.execute(
                    "UPDATE run_records SET metadata = ? WHERE id = ?",
                    (json.dumps(to_jsonable(next_metadata), ensure_ascii=False), run_id),
                )
                conn.commit()
                run_record["metadata"] = next_metadata
                return {"updated": True, "run_record": run_record}

        return self._run_write_with_retry(_write)

    def claim_runtime_episode_resume_schedule(
        self,
        run_id: str,
        *,
        marker_key: str,
        next_marker: Dict[str, Any],
        expected_marker_state: str = "waiting",
        expected_status: str = "running",
        terminal_states: Optional[set[str]] = None,
        active_states: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        key = str(marker_key or "").strip()
        if not key:
            raise ValueError("metadata marker key is required")
        normalized_terminal = {str(item or "").strip().lower() for item in (terminal_states or set()) if str(item or "").strip()}
        normalized_active = {str(item or "").strip().lower() for item in (active_states or set()) if str(item or "").strip()}
        expected_state = str(expected_marker_state or "").strip().lower()
        expected_run_status = str(expected_status or "").strip()

        def _parse_metadata(raw: Any) -> Dict[str, Any]:
            if not raw:
                return {}
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}

        def _write():
            with self.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM run_records WHERE id = ?", (run_id,)).fetchone()
                if not row:
                    conn.rollback()
                    return {"claimed": False, "reason": "run_not_found"}
                run_record = dict(row)
                status = str(run_record.get("status") or "").strip()
                if expected_run_status and status != expected_run_status:
                    conn.rollback()
                    return {
                        "claimed": False,
                        "reason": f"run_not_running:{status or 'unknown'}",
                        "currentStatus": status,
                    }
                metadata = _parse_metadata(run_record.get("metadata"))
                marker = metadata.get(key) if isinstance(metadata.get(key), dict) else {}
                marker_state = str((marker or {}).get("state") or "").strip().lower()
                if marker_state == "scheduled":
                    conn.rollback()
                    return {
                        "claimed": False,
                        "reason": "runtime_episode_resume_already_scheduled",
                        "currentState": marker_state,
                    }
                if marker_state != expected_state:
                    conn.rollback()
                    return {
                        "claimed": False,
                        "reason": "run_not_waiting_for_runtime_resume",
                        "currentState": marker_state,
                    }

                episode_rows = conn.execute(
                    """
                    SELECT id, state
                    FROM runtime_episodes
                    WHERE run_id = ? AND COALESCE(parent_episode_id, '') = ''
                    """,
                    (run_id,),
                ).fetchall()
                for episode_row in episode_rows:
                    episode_state = str(episode_row["state"] or "").strip().lower()
                    if episode_state in normalized_active or (
                        normalized_terminal and episode_state not in normalized_terminal
                    ):
                        conn.rollback()
                        return {
                            "claimed": False,
                            "reason": "top_level_runtime_episode_still_active",
                            "episodeId": episode_row["id"],
                            "episodeState": episode_state,
                        }

                next_metadata = dict(metadata)
                next_metadata[key] = to_jsonable(next_marker or {})
                conn.execute(
                    "UPDATE run_records SET metadata = ? WHERE id = ?",
                    (json.dumps(to_jsonable(next_metadata), ensure_ascii=False), run_id),
                )
                conn.commit()
                run_record["metadata"] = next_metadata
                return {
                    "claimed": True,
                    "run_record": run_record,
                    "topLevelEpisodeCount": len(episode_rows),
                }

        return self._run_write_with_retry(_write)

    def get_next_runtime_seq(self, session_id: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM runtime_events WHERE session_id = ?', (session_id,))
            row = cursor.fetchone()
            return int(row["next_seq"]) if row else 1

    def get_latest_runtime_seq(self, session_id: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM runtime_events WHERE session_id = ?', (session_id,))
            row = cursor.fetchone()
            return int(row["latest_seq"]) if row else 0

    def add_runtime_event(self, event: Dict[str, Any]):
        def _write():
            source_payload = to_jsonable(event.get("source") or {})
            event_payload = to_jsonable(event.get("payload") or {})
            with self.get_connection() as conn:
                session_id = event.get("session_id")
                seq = event.get("seq")
                for attempt in range(5):
                    if session_id:
                        row = conn.execute(
                            'SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM runtime_events WHERE session_id = ?',
                            (session_id,),
                        ).fetchone()
                        seq = int(row["next_seq"]) if row else 1
                    try:
                        conn.execute(
                            '''
                            INSERT INTO runtime_events
                            (id, session_id, run_id, seq, kind, topic, event_ts, source_json, payload_json)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''',
                            (
                                event["event_id"],
                                session_id,
                                event.get("run_id"),
                                seq,
                                event.get("kind", "event"),
                                event.get("topic"),
                                event.get("ts"),
                                json.dumps(source_payload, ensure_ascii=False),
                                json.dumps(event_payload, ensure_ascii=False),
                            ),
                        )
                        conn.commit()
                        return
                    except sqlite3.IntegrityError as exc:
                        if "runtime_events.session_id, runtime_events.seq" not in str(exc):
                            raise
                        if attempt >= 4:
                            raise
                        time.sleep(0.01 * (attempt + 1))

        self._run_write_with_retry(_write)

    def get_runtime_events(self, session_id: str, after_seq: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if after_seq is None:
                cursor.execute(
                    'SELECT * FROM runtime_events WHERE session_id = ? ORDER BY seq ASC',
                    (session_id,),
                )
            else:
                cursor.execute(
                    'SELECT * FROM runtime_events WHERE session_id = ? AND seq > ? ORDER BY seq ASC',
                    (session_id, after_seq),
                )
            rows = []
            for row in cursor.fetchall():
                data = dict(row)
                data["source"] = json.loads(data["source_json"]) if data.get("source_json") else {}
                data["payload"] = json.loads(data["payload_json"]) if data.get("payload_json") else {}
                rows.append(data)
            return rows

    def get_runtime_events_for_run(
        self,
        run_id: str,
        *,
        session_id: Optional[str] = None,
        after_seq: Optional[int] = None,
        before_seq: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM runtime_events WHERE run_id = ?"
        params: list[Any] = [run_id]
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if after_seq is not None:
            query += " AND seq > ?"
            params.append(after_seq)
        if before_seq is not None:
            query += " AND seq <= ?"
            params.append(before_seq)
        query += " ORDER BY seq ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                data = dict(row)
                data["source"] = json.loads(data["source_json"]) if data.get("source_json") else {}
                data["payload"] = json.loads(data["payload_json"]) if data.get("payload_json") else {}
                rows.append(data)
            return rows

    def add_runtime_snapshot(
        self,
        snapshot_id: str,
        session_id: str,
        run_id: Optional[str],
        latest_seq: int,
        snapshot_type: str,
        snapshot: Dict[str, Any],
    ):
        snapshot_str = json.dumps(to_jsonable(snapshot), ensure_ascii=False)
        created_at = utc_now_iso()
        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO runtime_snapshots
                    (id, session_id, run_id, latest_seq, snapshot_type, snapshot_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (snapshot_id, session_id, run_id, latest_seq, snapshot_type, snapshot_str, created_at),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def get_latest_runtime_snapshot(self, session_id: str, snapshot_type: str = "chat_projection") -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT * FROM runtime_snapshots
                WHERE session_id = ? AND snapshot_type = ?
                ORDER BY latest_seq DESC, created_at DESC
                LIMIT 1
                ''',
                (session_id, snapshot_type),
            )
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["snapshot"] = json.loads(data["snapshot_json"]) if data.get("snapshot_json") else {}
            return data

    # --- Runtime Episode Queue Operations ---

    def _hydrate_runtime_episode_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        for source, target, default in (
            ("need_json", "need", {}),
            ("inputs_json", "inputs", {}),
            ("required_runtime_access_json", "requiredRuntimeAccess", []),
            ("handoff_refs_json", "handoffRefs", []),
            ("continuation_token_json", "continuationToken", {}),
            ("retry_policy_json", "retryPolicy", {}),
            ("cancel_policy_json", "cancelPolicy", {}),
            ("resume_token_json", "resumeToken", {}),
            ("compensation_plan_json", "compensationPlan", {}),
            ("metadata_json", "metadata", {}),
        ):
            raw_value = data.get(source)
            if raw_value:
                try:
                    data[target] = json.loads(raw_value)
                except Exception:
                    data[target] = default
            else:
                data[target] = default
        data["episodeId"] = data.get("id")
        data["parentEpisodeId"] = data.get("parent_episode_id")
        data["rootEpisodeId"] = data.get("root_episode_id")
        data["runtimeAccess"] = data.get("requiredRuntimeAccess") or []
        data["lastHeartbeatAt"] = data.get("last_heartbeat_at")
        data["lastProgress"] = data.get("last_progress")
        data["errorCode"] = data.get("error_code")
        data["errorMessage"] = data.get("error_message")
        data["resultRef"] = data.get("result_ref")
        data["recoverable"] = bool(data.get("recoverable", 1))
        data["idempotencyKey"] = data.get("idempotency_key")
        data["deadlineAt"] = data.get("deadline_at")
        data["targetKind"] = data.get("target_kind")
        data["targetId"] = data.get("target_id")
        data["leaseGeneration"] = int(data.get("lease_generation") or 0)
        return data

    def upsert_runtime_episode_record(
        self,
        episode: Dict[str, Any],
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        priority: int = 0,
        enqueue: bool = False,
    ) -> Dict[str, Any]:
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            raise ValueError("runtime episode requires episodeId")
        now_iso = utc_now_iso()
        kind = str(episode.get("kind") or episode.get("runtimeKind") or "unknown").strip() or "unknown"
        state = str(episode.get("state") or "detected").strip() or "detected"
        resolved_session_id = str(session_id or episode.get("sessionId") or episode.get("session_id") or "").strip() or None
        resolved_run_id = str(run_id or episode.get("runId") or episode.get("run_id") or "").strip() or None
        parent_episode_id = str(episode.get("parentEpisodeId") or episode.get("parent_episode_id") or "").strip() or None
        root_episode_id = str(episode.get("rootEpisodeId") or episode.get("root_episode_id") or parent_episode_id or episode_id).strip() or episode_id
        source = str(episode.get("source") or (episode.get("need") or {}).get("source") or "").strip() or None
        reason = str(episode.get("reason") or (episode.get("need") or {}).get("reason") or "").strip() or None
        need = episode.get("need") if isinstance(episode.get("need"), dict) else {
            key: value
            for key, value in dict(episode).items()
            if key
            in {
                "kind",
                "source",
                "reason",
                "inputs",
                "requiredRuntimeAccess",
                "handoffRefs",
                "parentEpisodeId",
                "continuationTarget",
            }
        }
        inputs = episode.get("inputs") if isinstance(episode.get("inputs"), dict) else (need or {}).get("inputs") or {}
        required_runtime_access = (
            episode.get("requiredRuntimeAccess")
            or episode.get("runtimeAccess")
            or (need or {}).get("requiredRuntimeAccess")
            or []
        )
        handoff_refs = episode.get("handoffRefs") or []
        continuation_token = episode.get("continuationToken") or {}
        retry_policy = episode.get("retryPolicy") if isinstance(episode.get("retryPolicy"), dict) else {}
        cancel_policy = episode.get("cancelPolicy") if isinstance(episode.get("cancelPolicy"), dict) else {}
        resume_token = episode.get("resumeToken") if isinstance(episode.get("resumeToken"), dict) else continuation_token
        compensation_plan = episode.get("compensationPlan") if isinstance(episode.get("compensationPlan"), dict) else {}
        idempotency_key = str(episode.get("idempotencyKey") or episode.get("idempotency_key") or "").strip() or None
        deadline_at = str(episode.get("deadlineAt") or episode.get("deadline_at") or "").strip() or None
        target_kind = str(episode.get("targetKind") or episode.get("target_kind") or "").strip() or None
        target_id = str(episode.get("targetId") or episode.get("target_id") or "").strip() or None
        max_attempts = int(retry_policy.get("maxAttempts") or retry_policy.get("max_attempts") or episode.get("maxAttempts") or 1)
        metadata = episode.get("metadata") if isinstance(episode.get("metadata"), dict) else {}

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO runtime_episodes (
                        id, session_id, run_id, parent_episode_id, root_episode_id, kind, state,
                        source, reason, need_json, inputs_json, required_runtime_access_json,
                        handoff_refs_json, continuation_token_json, retry_policy_json,
                        cancel_policy_json, resume_token_json, idempotency_key, deadline_at,
                        compensation_plan_json, target_kind, target_id, result_ref, recoverable,
                        priority, metadata_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        session_id = COALESCE(excluded.session_id, runtime_episodes.session_id),
                        run_id = COALESCE(excluded.run_id, runtime_episodes.run_id),
                        parent_episode_id = COALESCE(excluded.parent_episode_id, runtime_episodes.parent_episode_id),
                        root_episode_id = COALESCE(excluded.root_episode_id, runtime_episodes.root_episode_id),
                        kind = excluded.kind,
                        state = excluded.state,
                        source = COALESCE(excluded.source, runtime_episodes.source),
                        reason = COALESCE(excluded.reason, runtime_episodes.reason),
                        need_json = excluded.need_json,
                        inputs_json = excluded.inputs_json,
                        required_runtime_access_json = excluded.required_runtime_access_json,
                        handoff_refs_json = excluded.handoff_refs_json,
                        continuation_token_json = excluded.continuation_token_json,
                        retry_policy_json = excluded.retry_policy_json,
                        cancel_policy_json = excluded.cancel_policy_json,
                        resume_token_json = excluded.resume_token_json,
                        idempotency_key = COALESCE(excluded.idempotency_key, runtime_episodes.idempotency_key),
                        deadline_at = COALESCE(excluded.deadline_at, runtime_episodes.deadline_at),
                        compensation_plan_json = excluded.compensation_plan_json,
                        target_kind = COALESCE(excluded.target_kind, runtime_episodes.target_kind),
                        target_id = COALESCE(excluded.target_id, runtime_episodes.target_id),
                        result_ref = COALESCE(excluded.result_ref, runtime_episodes.result_ref),
                        recoverable = excluded.recoverable,
                        priority = excluded.priority,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    ''',
                    (
                        episode_id,
                        resolved_session_id,
                        resolved_run_id,
                        parent_episode_id,
                        root_episode_id,
                        kind,
                        state,
                        source,
                        reason,
                        json.dumps(to_jsonable(need or {}), ensure_ascii=False),
                        json.dumps(to_jsonable(inputs or {}), ensure_ascii=False),
                        json.dumps(to_jsonable(required_runtime_access or []), ensure_ascii=False),
                        json.dumps(to_jsonable(handoff_refs or []), ensure_ascii=False),
                        json.dumps(to_jsonable(continuation_token or {}), ensure_ascii=False),
                        json.dumps(to_jsonable(retry_policy or {}), ensure_ascii=False),
                        json.dumps(to_jsonable(cancel_policy or {}), ensure_ascii=False),
                        json.dumps(to_jsonable(resume_token or {}), ensure_ascii=False),
                        idempotency_key,
                        deadline_at,
                        json.dumps(to_jsonable(compensation_plan or {}), ensure_ascii=False),
                        target_kind,
                        target_id,
                        episode.get("resultRef") or episode.get("result_ref"),
                        1 if episode.get("recoverable", True) else 0,
                        int(priority or episode.get("priority") or 0),
                        json.dumps(to_jsonable(metadata or {}), ensure_ascii=False),
                        str(episode.get("createdAt") or now_iso),
                        now_iso,
                    ),
                )
                if enqueue:
                    conn.execute(
                        '''
                        INSERT INTO runtime_episode_queue (
                            id, episode_id, session_id, run_id, kind, priority, state,
                            available_at, max_attempts, retry_policy_json, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                        ON CONFLICT(episode_id) DO UPDATE SET
                            state = CASE
                                WHEN runtime_episode_queue.state IN ('completed', 'cancelled') THEN runtime_episode_queue.state
                                ELSE 'queued'
                            END,
                            priority = excluded.priority,
                            max_attempts = excluded.max_attempts,
                            retry_policy_json = excluded.retry_policy_json,
                            available_at = excluded.available_at,
                            updated_at = excluded.updated_at
                        ''',
                        (
                            f"episode_queue:{episode_id}",
                            episode_id,
                            resolved_session_id,
                            resolved_run_id,
                            kind,
                            int(priority or episode.get("priority") or 0),
                            now_iso,
                            max(1, max_attempts),
                            json.dumps(to_jsonable(retry_policy or {}), ensure_ascii=False),
                            now_iso,
                            now_iso,
                        ),
                    )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_runtime_episode(episode_id) or {**episode, "episodeId": episode_id, "state": state}

    def enqueue_runtime_episode(
        self,
        episode_id: str,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        kind: str = "unknown",
        priority: int = 0,
        available_at: Optional[str] = None,
    ) -> None:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO runtime_episode_queue (
                        id, episode_id, session_id, run_id, kind, priority, state,
                        available_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                    ON CONFLICT(episode_id) DO UPDATE SET
                        state = 'queued',
                        priority = excluded.priority,
                        available_at = excluded.available_at,
                        updated_at = excluded.updated_at
                    ''',
                    (
                        f"episode_queue:{episode_id}",
                        episode_id,
                        session_id,
                        run_id,
                        kind or "unknown",
                        int(priority or 0),
                        available_at or now_iso,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def backfill_runtime_episode_binding(
        self,
        episode_id: str,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        resolved_session_id = str(session_id or "").strip() or None
        resolved_run_id = str(run_id or "").strip() or None
        if not resolved_session_id and not resolved_run_id:
            return
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE runtime_episodes
                    SET session_id = COALESCE(session_id, ?),
                        run_id = COALESCE(run_id, ?),
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (resolved_session_id, resolved_run_id, now_iso, episode_id),
                )
                conn.execute(
                    '''
                    UPDATE runtime_episode_queue
                    SET session_id = COALESCE(session_id, ?),
                        run_id = COALESCE(run_id, ?),
                        updated_at = ?
                    WHERE episode_id = ?
                    ''',
                    (resolved_session_id, resolved_run_id, now_iso, episode_id),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def claim_runtime_episode(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        kinds: Optional[List[str]] = None,
        require_bound_run: bool = False,
    ) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()
        expires_iso = (datetime.now(timezone.utc) + timedelta(seconds=int(lease_seconds or 60))).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        normalized_kinds = [str(item).strip() for item in list(kinds or []) if str(item).strip()]

        def _write():
            with self.get_connection() as conn:
                cursor = conn.cursor()
                params: list[Any] = [now_iso, now_iso]
                query = '''
                    SELECT q.*, e.state AS episode_state
                    FROM runtime_episode_queue q
                    JOIN runtime_episodes e ON e.id = q.episode_id
                    WHERE (
                        q.state IN ('queued', 'retry')
                        OR (q.state = 'leased' AND COALESCE(q.lease_expires_at, '') <= ?)
                    )
                      AND COALESCE(q.available_at, ?) <= ?
                '''
                params.append(now_iso)
                if normalized_kinds:
                    placeholders = ",".join("?" for _ in normalized_kinds)
                    query += f" AND q.kind IN ({placeholders})"
                    params.extend(normalized_kinds)
                if require_bound_run:
                    query += " AND COALESCE(q.session_id, '') <> '' AND COALESCE(q.run_id, '') <> ''"
                query += " ORDER BY q.priority DESC, q.created_at ASC LIMIT 1"
                cursor.execute(query, params)
                row = cursor.fetchone()
                if not row:
                    return None
                queue_id = row["id"]
                episode_id = row["episode_id"]
                attempt_count = int(row["attempt_count"] or 0) + 1
                conn.execute(
                    '''
                    UPDATE runtime_episode_queue
                    SET state = 'leased',
                        locked_by = ?,
                        lease_expires_at = ?,
                        attempt_count = ?,
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (worker_id, expires_iso, attempt_count, now_iso, queue_id),
                )
                conn.execute(
                    '''
                    UPDATE runtime_episodes
                    SET state = 'active',
                        worker_id = ?,
                        lease_expires_at = ?,
                        last_heartbeat_at = ?,
                        attempt_count = ?,
                        lease_generation = COALESCE(lease_generation, 0) + 1,
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (worker_id, expires_iso, now_iso, attempt_count, now_iso, episode_id),
                )
                conn.execute(
                    '''
                    INSERT INTO runtime_episode_leases (
                        id, episode_id, worker_id, state, acquired_at, expires_at, heartbeat_at, metadata_json
                    )
                    VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                    ''',
                    (
                        f"episode_lease:{episode_id}:{attempt_count}",
                        episode_id,
                        worker_id,
                        now_iso,
                        expires_iso,
                        now_iso,
                        json.dumps({"queueId": queue_id}, ensure_ascii=False),
                    ),
                )
                conn.commit()
                return episode_id

        episode_id = self._run_write_with_retry(_write)
        if not episode_id:
            return None
        return self.get_runtime_episode(str(episode_id))

    def heartbeat_runtime_episode(
        self,
        episode_id: str,
        *,
        worker_id: Optional[str] = None,
        progress: Optional[str] = None,
        lease_seconds: int = 60,
    ) -> None:
        now_iso = utc_now_iso()
        expires_iso = (datetime.now(timezone.utc) + timedelta(seconds=int(lease_seconds or 60))).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE runtime_episodes
                    SET last_heartbeat_at = ?,
                        lease_expires_at = ?,
                        last_progress = COALESCE(?, last_progress),
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (now_iso, expires_iso, progress, now_iso, episode_id),
                )
                conn.execute(
                    '''
                    UPDATE runtime_episode_queue
                    SET lease_expires_at = ?,
                        updated_at = ?
                    WHERE episode_id = ?
                    ''',
                    (expires_iso, now_iso, episode_id),
                )
                if worker_id:
                    conn.execute(
                        '''
                        UPDATE runtime_episode_leases
                        SET heartbeat_at = ?,
                            expires_at = ?
                        WHERE episode_id = ? AND worker_id = ? AND state = 'active'
                        ''',
                        (now_iso, expires_iso, episode_id, worker_id),
                    )
                conn.commit()

        self._run_write_with_retry(_write)

    def complete_runtime_episode(
        self,
        episode_id: str,
        *,
        state: str,
        result_ref: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()
        terminal = state in {"completed", "failed", "cancelled", "merged"}

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE runtime_episodes
                    SET state = ?,
                        result_ref = COALESCE(?, result_ref),
                        error_code = COALESCE(?, error_code),
                        error_message = COALESCE(?, error_message),
                        metadata_json = COALESCE(?, metadata_json),
                        completed_at = CASE WHEN ? THEN COALESCE(completed_at, ?) ELSE completed_at END,
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (
                        state,
                        result_ref,
                        error_code,
                        error_message,
                        json.dumps(to_jsonable(metadata), ensure_ascii=False) if metadata is not None else None,
                        1 if terminal else 0,
                        now_iso,
                        now_iso,
                        episode_id,
                    ),
                )
                conn.execute(
                    '''
                    UPDATE runtime_episode_queue
                    SET state = ?,
                        last_error = COALESCE(?, last_error),
                        updated_at = ?
                    WHERE episode_id = ?
                    ''',
                    ("completed" if state in {"completed", "merged"} else state, error_message, now_iso, episode_id),
                )
                conn.execute(
                    '''
                    UPDATE runtime_episode_leases
                    SET state = ?,
                        released_at = COALESCE(released_at, ?)
                    WHERE episode_id = ? AND state = 'active'
                    ''',
                    (state, now_iso, episode_id),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_runtime_episode(episode_id)

    def retry_runtime_episode(
        self,
        episode_id: str,
        *,
        error_message: Optional[str] = None,
        delay_seconds: int = 0,
    ) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()
        delay = max(0, int(delay_seconds or 0))
        available_at = now_iso if delay == 0 else (
            datetime.now(timezone.utc) + timedelta(seconds=delay)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE runtime_episodes
                    SET state = 'queued',
                        error_message = COALESCE(?, error_message),
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (error_message, now_iso, episode_id),
                )
                conn.execute(
                    '''
                    UPDATE runtime_episode_queue
                    SET state = 'retry',
                        last_error = COALESCE(?, last_error),
                        available_at = ?,
                        locked_by = NULL,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE episode_id = ?
                    ''',
                    (error_message, available_at, now_iso, episode_id),
                )
                conn.execute(
                    '''
                    UPDATE runtime_episode_leases
                    SET state = 'retry',
                        released_at = COALESCE(released_at, ?)
                    WHERE episode_id = ? AND state = 'active'
                    ''',
                    (now_iso, episode_id),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_runtime_episode(episode_id)

    def cancel_runtime_episode(
        self,
        episode_id: str,
        *,
        reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self.complete_runtime_episode(
            episode_id,
            state="cancelled",
            error_code="episode_cancelled",
            error_message=reason or "Runtime episode cancelled.",
            metadata={"recoverable": True, "cancelReason": reason or "manual"},
        )

    def resume_runtime_episode(
        self,
        episode_id: str,
        *,
        resume_token: Optional[Dict[str, Any]] = None,
        priority: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE runtime_episodes
                    SET state = 'queued',
                        resume_token_json = COALESCE(?, resume_token_json),
                        priority = COALESCE(?, priority),
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (
                        json.dumps(to_jsonable(resume_token), ensure_ascii=False) if resume_token is not None else None,
                        priority,
                        now_iso,
                        episode_id,
                    ),
                )
                cursor = conn.cursor()
                cursor.execute('SELECT session_id, run_id, kind, priority FROM runtime_episodes WHERE id = ?', (episode_id,))
                row = cursor.fetchone()
                if row:
                    conn.execute(
                        '''
                        INSERT INTO runtime_episode_queue (
                            id, episode_id, session_id, run_id, kind, priority, state, available_at, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                        ON CONFLICT(episode_id) DO UPDATE SET
                            state = 'queued',
                            priority = excluded.priority,
                            available_at = excluded.available_at,
                            updated_at = excluded.updated_at
                        ''',
                        (
                            f"episode_queue:{episode_id}",
                            episode_id,
                            row["session_id"],
                            row["run_id"],
                            row["kind"],
                            int(priority if priority is not None else (row["priority"] or 0)),
                            now_iso,
                            now_iso,
                            now_iso,
                        ),
                    )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_runtime_episode(episode_id)

    def add_runtime_episode_event_record(
        self,
        *,
        episode_id: str,
        topic: str,
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        state: Optional[str] = None,
    ) -> None:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO runtime_episode_events (
                        id, episode_id, session_id, run_id, topic, state, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        f"episode_event:{episode_id}:{uuid.uuid4().hex}",
                        episode_id,
                        session_id,
                        run_id,
                        topic,
                        state,
                        json.dumps(to_jsonable(payload or {}), ensure_ascii=False),
                        now_iso,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def add_runtime_episode_handoff(
        self,
        *,
        episode_id: str,
        handoff: Dict[str, Any],
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        handoff_id = str(handoff.get("handoffId") or handoff.get("id") or f"handoff:{episode_id}:{uuid.uuid4().hex[:10]}")
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT OR REPLACE INTO runtime_episode_handoffs (
                        id, episode_id, session_id, run_id, kind, status, confidence,
                        compact_summary, refs_json, raw_ref, detail_tool, consumer_hint, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        handoff_id,
                        episode_id,
                        session_id,
                        run_id,
                        handoff.get("kind"),
                        handoff.get("status"),
                        handoff.get("confidence"),
                        handoff.get("compactSummary") or handoff.get("summary"),
                        json.dumps(to_jsonable(handoff.get("refs") or []), ensure_ascii=False),
                        handoff.get("rawRef"),
                        handoff.get("detailTool"),
                        handoff.get("consumerHint"),
                        json.dumps(to_jsonable({**handoff, "handoffId": handoff_id}), ensure_ascii=False),
                        now_iso,
                    ),
                )
                cursor = conn.cursor()
                cursor.execute('SELECT handoff_refs_json FROM runtime_episodes WHERE id = ?', (episode_id,))
                row = cursor.fetchone()
                refs: list[Any] = []
                if row and row["handoff_refs_json"]:
                    try:
                        refs = json.loads(row["handoff_refs_json"])
                    except Exception:
                        refs = []
                compact_handoff = {**handoff, "handoffId": handoff_id}
                if not any(str(item.get("handoffId") or item.get("id") or "") == handoff_id for item in refs if isinstance(item, dict)):
                    refs.append(compact_handoff)
                conn.execute(
                    '''
                    UPDATE runtime_episodes
                    SET handoff_refs_json = ?, updated_at = ?
                    WHERE id = ?
                    ''',
                    (json.dumps(to_jsonable(refs), ensure_ascii=False), now_iso, episode_id),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return {**handoff, "handoffId": handoff_id}

    def list_runtime_episode_handoffs(self, episode_id: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT * FROM runtime_episode_handoffs
                WHERE episode_id = ?
                ORDER BY created_at ASC
                ''',
                (episode_id,),
            )
            rows = cursor.fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                data = dict(row)
                try:
                    payload = json.loads(data.get("payload_json") or "{}")
                except Exception:
                    payload = {}
                items.append({**data, "payload": payload})
            return items

    def list_runtime_episode_queue(
        self,
        *,
        active_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM runtime_episode_queue WHERE 1=1"
        params: list[Any] = []
        if active_only:
            query += " AND state IN ('queued', 'retry', 'leased')"
        query += " ORDER BY priority DESC, updated_at DESC LIMIT ?"
        params.append(int(limit or 100))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                data = dict(row)
                try:
                    data["retryPolicy"] = json.loads(data.get("retry_policy_json") or "{}")
                except Exception:
                    data["retryPolicy"] = {}
                items.append(data)
            return items

    def list_runtime_episode_leases(
        self,
        *,
        active_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM runtime_episode_leases WHERE 1=1"
        params: list[Any] = []
        if active_only:
            query += " AND state = 'active'"
        query += " ORDER BY heartbeat_at DESC, acquired_at DESC LIMIT ?"
        params.append(int(limit or 100))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                data = dict(row)
                try:
                    data["metadata"] = json.loads(data.get("metadata_json") or "{}")
                except Exception:
                    data["metadata"] = {}
                items.append(data)
            return items

    def get_runtime_episode(self, episode_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM runtime_episodes WHERE id = ?', (episode_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._hydrate_runtime_episode_row(dict(row))

    def list_runtime_episodes(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        parent_episode_id: Optional[str] = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM runtime_episodes WHERE 1=1"
        params: list[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if parent_episode_id:
            query += " AND parent_episode_id = ?"
            params.append(parent_episode_id)
        if active_only:
            query += " AND state IN ('detected', 'routed', 'queued', 'leased', 'active', 'waiting', 'waiting_child', 'waiting_external', 'waiting_approval')"
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(int(limit or 100))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._hydrate_runtime_episode_row(dict(row)) for row in cursor.fetchall()]

    def upsert_session_lane_record(
        self,
        *,
        session_id: str,
        active_run_id: Optional[str],
        queued_run_id: Optional[str],
        blocked_by_run_id: Optional[str],
        policy: str,
        state: str,
        last_transition: Optional[str],
        last_transition_ts: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        metadata_str = json.dumps(to_jsonable(metadata or {}), ensure_ascii=False)

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO session_lane_records
                    (session_id, active_run_id, queued_run_id, blocked_by_run_id, policy, state, last_transition, last_transition_ts, metadata_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(session_id) DO UPDATE SET
                        active_run_id = excluded.active_run_id,
                        queued_run_id = excluded.queued_run_id,
                        blocked_by_run_id = excluded.blocked_by_run_id,
                        policy = excluded.policy,
                        state = excluded.state,
                        last_transition = excluded.last_transition,
                        last_transition_ts = excluded.last_transition_ts,
                        metadata_json = excluded.metadata_json,
                        updated_at = CURRENT_TIMESTAMP
                    ''',
                    (
                        session_id,
                        active_run_id,
                        queued_run_id,
                        blocked_by_run_id,
                        policy,
                        state,
                        last_transition,
                        last_transition_ts,
                        metadata_str,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def get_session_lane_record(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM session_lane_records WHERE session_id = ?', (session_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
            return data

    def list_session_lane_records(self, *, limit: int = 500) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT * FROM session_lane_records
                ORDER BY updated_at DESC
                LIMIT ?
                ''',
                (int(limit),),
            )
            rows: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                data = dict(row)
                data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
                rows.append(data)
            return rows

    def add_session_lane_queue_entry(
        self,
        *,
        entry_id: str,
        session_id: str,
        run_id: str,
        action: str,
        policy: str,
        active_run_id: Optional[str] = None,
        interrupted_run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        metadata_str = json.dumps(to_jsonable(metadata or {}), ensure_ascii=False)

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO session_lane_queue_entries
                    (id, session_id, run_id, action, policy, active_run_id, interrupted_run_id, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        entry_id,
                        session_id,
                        run_id,
                        action,
                        policy,
                        active_run_id,
                        interrupted_run_id,
                        metadata_str,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def list_session_lane_queue_entries(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = 'SELECT * FROM session_lane_queue_entries WHERE 1=1'
        params: list[Any] = []
        if session_id:
            query += ' AND session_id = ?'
            params.append(session_id)
        if run_id:
            query += ' AND run_id = ?'
            params.append(run_id)
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                data = dict(row)
                data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
                rows.append(data)
            return rows

    # --- Chat User Message Queue Operations ---

    def _hydrate_chat_user_message_queue_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["attachments"] = json.loads(data["attachments_json"]) if data.get("attachments_json") else []
        data["fileUrls"] = json.loads(data["file_urls_json"]) if data.get("file_urls_json") else []
        data["request"] = json.loads(data["request_json"]) if data.get("request_json") else {}
        data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
        return data

    def _next_chat_user_message_queue_ordinal(self, conn, session_id: str) -> int:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal FROM chat_user_message_queue WHERE session_id = ?',
            (session_id,),
        )
        row = cursor.fetchone()
        return int(row["next_ordinal"]) if row else 1

    def add_chat_user_message_queue_item(
        self,
        *,
        queue_id: str,
        session_id: str,
        run_id: Optional[str],
        client_message_id: Optional[str],
        content: str,
        attachments: Optional[list[dict[str, Any]]] = None,
        file_urls: Optional[list[str]] = None,
        request_payload: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                ordinal = self._next_chat_user_message_queue_ordinal(conn, session_id)
                conn.execute(
                    '''
                    INSERT INTO chat_user_message_queue
                    (id, session_id, run_id, client_message_id, content, attachments_json, file_urls_json, request_json, state, ordinal, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                    ''',
                    (
                        queue_id,
                        session_id,
                        run_id,
                        client_message_id,
                        content,
                        json.dumps(to_jsonable(attachments or []), ensure_ascii=False),
                        json.dumps(to_jsonable(file_urls or []), ensure_ascii=False),
                        json.dumps(to_jsonable(request_payload or {}), ensure_ascii=False),
                        ordinal,
                        json.dumps(to_jsonable(metadata or {}), ensure_ascii=False),
                        now_iso,
                        now_iso,
                    ),
                )
                conn.execute('UPDATE sessions SET updated_at = ? WHERE id = ?', (now_iso, session_id))
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_chat_user_message_queue_item(queue_id) or {}

    def get_chat_user_message_queue_item(self, queue_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = str(queue_id or "").strip()
        if not normalized_id:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM chat_user_message_queue WHERE id = ?', (normalized_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._hydrate_chat_user_message_queue_row(dict(row))

    def get_chat_user_message_queue_item_by_client_message_id(
        self,
        *,
        session_id: str,
        client_message_id: str,
    ) -> Optional[Dict[str, Any]]:
        normalized_session_id = str(session_id or "").strip()
        normalized_client_message_id = str(client_message_id or "").strip()
        if not normalized_session_id or not normalized_client_message_id:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM chat_user_message_queue
                WHERE session_id = ?
                  AND client_message_id = ?
                  AND state != 'cancelled'
                ORDER BY created_at ASC
                LIMIT 1
                ''',
                (normalized_session_id, normalized_client_message_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._hydrate_chat_user_message_queue_row(dict(row))

    def list_chat_user_message_queue(
        self,
        *,
        session_id: str,
        states: Optional[list[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        normalized_states = [str(item).strip() for item in (states or []) if str(item).strip()]
        params: list[Any] = [session_id]
        query = 'SELECT * FROM chat_user_message_queue WHERE session_id = ?'
        if normalized_states:
            placeholders = ",".join("?" for _ in normalized_states)
            query += f' AND state IN ({placeholders})'
            params.extend(normalized_states)
        query += ' ORDER BY ordinal ASC, created_at ASC LIMIT ?'
        params.append(int(limit))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._hydrate_chat_user_message_queue_row(dict(row)) for row in cursor.fetchall()]

    def update_chat_user_message_queue_item(
        self,
        queue_id: str,
        *,
        content: Optional[str] = None,
        state: Optional[str] = None,
        run_id: Optional[str] = None,
        consumed_run_id: Optional[str] = None,
        metadata_updates: Optional[dict[str, Any]] = None,
        timestamp_field: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_chat_user_message_queue_item(queue_id)
        if not existing:
            return None
        allowed_timestamp_fields = {"promoted_at", "injected_at", "consumed_at", "cancelled_at"}
        now_iso = utc_now_iso()
        next_metadata = dict(existing.get("metadata") or {})
        if metadata_updates:
            next_metadata.update(metadata_updates)

        assignments = ["updated_at = ?", "metadata_json = ?"]
        values: list[Any] = [now_iso, json.dumps(to_jsonable(next_metadata), ensure_ascii=False)]
        if content is not None:
            assignments.append("content = ?")
            values.append(content)
        if state is not None:
            assignments.append("state = ?")
            values.append(state)
        if run_id is not None:
            assignments.append("run_id = ?")
            values.append(run_id)
        if consumed_run_id is not None:
            assignments.append("consumed_run_id = ?")
            values.append(consumed_run_id)
        if timestamp_field in allowed_timestamp_fields:
            assignments.append(f"{timestamp_field} = ?")
            values.append(now_iso)
        values.append(queue_id)

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    f"UPDATE chat_user_message_queue SET {', '.join(assignments)} WHERE id = ?",
                    tuple(values),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_chat_user_message_queue_item(queue_id)

    # --- Network Neighbor Operations ---

    def _hydrate_network_neighbor_link_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["linkId"] = data.get("id")
        data["peerId"] = data.get("peer_id")
        data["localNickname"] = data.get("local_nickname") or ""
        data["remoteNickname"] = data.get("remote_nickname") or ""
        data["localRole"] = data.get("local_role") or "primary"
        data["remoteRole"] = data.get("remote_role") or "companion"
        data["trustStatus"] = data.get("trust_status") or "trusted"
        data["workspaceBinding"] = json.loads(data["workspace_binding_json"]) if data.get("workspace_binding_json") else {}
        data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
        data["pairedAt"] = data.get("paired_at")
        data["lastSeenAt"] = data.get("last_seen_at")
        data["createdAt"] = data.get("created_at")
        data["updatedAt"] = data.get("updated_at")
        return data

    def upsert_network_neighbor_link(
        self,
        *,
        link_id: str,
        peer_id: str,
        local_nickname: str,
        remote_nickname: str,
        local_role: str,
        remote_role: str,
        trust_status: str = "trusted",
        workspace_binding: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        last_seen_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO network_neighbor_links
                    (id, peer_id, local_nickname, remote_nickname, local_role, remote_role, trust_status, workspace_binding_json, metadata_json, paired_at, last_seen_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(peer_id) DO UPDATE SET
                        id = excluded.id,
                        local_nickname = excluded.local_nickname,
                        remote_nickname = excluded.remote_nickname,
                        local_role = excluded.local_role,
                        remote_role = excluded.remote_role,
                        trust_status = excluded.trust_status,
                        workspace_binding_json = excluded.workspace_binding_json,
                        metadata_json = excluded.metadata_json,
                        last_seen_at = COALESCE(excluded.last_seen_at, network_neighbor_links.last_seen_at),
                        updated_at = excluded.updated_at
                    ''',
                    (
                        link_id,
                        peer_id,
                        local_nickname,
                        remote_nickname,
                        local_role or "primary",
                        remote_role or "companion",
                        trust_status or "trusted",
                        json.dumps(to_jsonable(workspace_binding or {}), ensure_ascii=False),
                        json.dumps(to_jsonable(metadata or {}), ensure_ascii=False),
                        now_iso,
                        last_seen_at or now_iso,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_neighbor_link_by_peer(peer_id) or {}

    def get_network_neighbor_link(self, link_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = str(link_id or "").strip()
        if not normalized_id:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_neighbor_links WHERE id = ?', (normalized_id,))
            row = cursor.fetchone()
            return self._hydrate_network_neighbor_link_row(dict(row)) if row else None

    def get_network_neighbor_link_by_peer(self, peer_id: str) -> Optional[Dict[str, Any]]:
        normalized_peer_id = str(peer_id or "").strip()
        if not normalized_peer_id:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_neighbor_links WHERE peer_id = ?', (normalized_peer_id,))
            row = cursor.fetchone()
            return self._hydrate_network_neighbor_link_row(dict(row)) if row else None

    def list_network_neighbor_links(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_neighbor_links ORDER BY updated_at DESC, paired_at DESC')
            return [self._hydrate_network_neighbor_link_row(dict(row)) for row in cursor.fetchall()]

    def delete_network_neighbor_link(self, link_id: str) -> bool:
        normalized_id = str(link_id or "").strip()
        if not normalized_id:
            return False

        def _write():
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM network_neighbor_links WHERE id = ?', (normalized_id,))
                conn.commit()
                return cursor.rowcount > 0

        return bool(self._run_write_with_retry(_write))

    def _hydrate_network_neighbor_message_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["messageId"] = data.get("id")
        data["linkId"] = data.get("link_id")
        data["fromPeerId"] = data.get("from_peer_id")
        data["fromNickname"] = data.get("from_nickname") or ""
        data["workspaceBinding"] = json.loads(data["workspace_binding_json"]) if data.get("workspace_binding_json") else {}
        data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
        data["createdAt"] = data.get("created_at")
        data["receivedAt"] = data.get("received_at")
        return data

    def _next_network_neighbor_message_seq(self, conn, link_id: str) -> int:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM network_neighbor_messages WHERE link_id = ?',
            (link_id,),
        )
        row = cursor.fetchone()
        return int(row["next_seq"]) if row else 1

    def add_network_neighbor_message(
        self,
        *,
        message_id: str,
        link_id: str,
        direction: str,
        from_peer_id: str,
        from_nickname: str,
        role: str,
        body: str,
        preview: str,
        status: str = "stored",
        run_id: Optional[str] = None,
        workspace_binding: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                seq = self._next_network_neighbor_message_seq(conn, link_id)
                conn.execute(
                    '''
                    INSERT INTO network_neighbor_messages
                    (id, link_id, seq, direction, from_peer_id, from_nickname, role, body, preview, status, run_id, workspace_binding_json, metadata_json, created_at, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        message_id,
                        link_id,
                        seq,
                        direction,
                        from_peer_id,
                        from_nickname,
                        role,
                        body,
                        preview,
                        status or "stored",
                        run_id,
                        json.dumps(to_jsonable(workspace_binding or {}), ensure_ascii=False),
                        json.dumps(to_jsonable(metadata or {}), ensure_ascii=False),
                        now_iso,
                        now_iso,
                    ),
                )
                conn.execute('UPDATE network_neighbor_links SET updated_at = ?, last_seen_at = ? WHERE id = ?', (now_iso, now_iso, link_id))
                conn.commit()
                return seq

        self._run_write_with_retry(_write)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_neighbor_messages WHERE id = ?', (message_id,))
            row = cursor.fetchone()
            return self._hydrate_network_neighbor_message_row(dict(row)) if row else {}

    def list_network_neighbor_messages(
        self,
        *,
        link_id: str,
        after_seq: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        params: list[Any] = [link_id]
        query = 'SELECT * FROM network_neighbor_messages WHERE link_id = ?'
        if after_seq is not None:
            query += ' AND seq > ?'
            params.append(int(after_seq))
        query += ' ORDER BY seq ASC LIMIT ?'
        params.append(max(1, min(int(limit or 50), 200)))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._hydrate_network_neighbor_message_row(dict(row)) for row in cursor.fetchall()]

    def _hydrate_network_neighbor_wake_queue_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["queueId"] = data.get("id")
        data["linkId"] = data.get("link_id")
        data["messageId"] = data.get("message_id")
        data["runId"] = data.get("run_id")
        data["attemptCount"] = int(data.get("attempt_count") or 0)
        data["maxAttempts"] = int(data.get("max_attempts") or 0)
        data["availableAt"] = data.get("available_at")
        data["claimedBy"] = data.get("claimed_by")
        data["leaseExpiresAt"] = data.get("lease_expires_at")
        data["lastError"] = data.get("last_error")
        data["payload"] = json.loads(data["payload_json"]) if data.get("payload_json") else {}
        data["createdAt"] = data.get("created_at")
        data["updatedAt"] = data.get("updated_at")
        data["completedAt"] = data.get("completed_at")
        data["failedAt"] = data.get("failed_at")
        return data

    def add_network_neighbor_wake_queue_item(
        self,
        *,
        queue_id: str,
        link_id: str,
        message_id: str,
        run_id: str,
        payload: Optional[dict[str, Any]] = None,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO network_neighbor_wake_queue
                    (id, link_id, message_id, run_id, state, attempt_count, max_attempts, available_at, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?)
                    ''',
                    (
                        queue_id,
                        link_id,
                        message_id,
                        run_id,
                        max(1, min(int(max_attempts or 3), 10)),
                        now_iso,
                        json.dumps(to_jsonable(payload or {}), ensure_ascii=False),
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_neighbor_wake_queue_item(queue_id) or {}

    def get_network_neighbor_wake_queue_item(self, queue_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = str(queue_id or "").strip()
        if not normalized_id:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_neighbor_wake_queue WHERE id = ?', (normalized_id,))
            row = cursor.fetchone()
            return self._hydrate_network_neighbor_wake_queue_row(dict(row)) if row else None

    def claim_next_network_neighbor_wake_item(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 180,
    ) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()
        lease_expires_at = latest_utc_iso(datetime.now(timezone.utc) + timedelta(seconds=max(30, int(lease_seconds or 180))))

        def _write():
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT *
                    FROM network_neighbor_wake_queue
                    WHERE attempt_count < max_attempts
                      AND (
                        (state IN ('queued', 'retry') AND (available_at IS NULL OR available_at <= ?))
                        OR (state = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                      )
                    ORDER BY created_at ASC
                    LIMIT 1
                    ''',
                    (now_iso, now_iso),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                queue_id = row["id"]
                cursor.execute(
                    '''
                    UPDATE network_neighbor_wake_queue
                    SET state = 'leased',
                        attempt_count = attempt_count + 1,
                        claimed_by = ?,
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND attempt_count < max_attempts
                      AND (
                        (state IN ('queued', 'retry') AND (available_at IS NULL OR available_at <= ?))
                        OR (state = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                      )
                    ''',
                    (worker_id, lease_expires_at, now_iso, queue_id, now_iso, now_iso),
                )
                if cursor.rowcount != 1:
                    conn.commit()
                    return None
                cursor.execute('SELECT * FROM network_neighbor_wake_queue WHERE id = ?', (queue_id,))
                claimed = cursor.fetchone()
                conn.commit()
                return self._hydrate_network_neighbor_wake_queue_row(dict(claimed)) if claimed else None

        return self._run_write_with_retry(_write)

    def complete_network_neighbor_wake_item(self, queue_id: str) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE network_neighbor_wake_queue
                    SET state = 'completed',
                        completed_at = ?,
                        available_at = NULL,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE id = ? AND state = 'leased'
                    ''',
                    (now_iso, now_iso, queue_id),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_neighbor_wake_queue_item(queue_id)

    def fail_network_neighbor_wake_item(
        self,
        queue_id: str,
        *,
        error: str,
        retry_delay_seconds: int = 30,
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_network_neighbor_wake_queue_item(queue_id)
        if not existing:
            return None
        now_iso = utc_now_iso()
        exhausted = int(existing.get("attemptCount") or 0) >= int(existing.get("maxAttempts") or 1)
        next_state = "failed" if exhausted else "retry"
        available_at = latest_utc_iso(datetime.now(timezone.utc) + timedelta(seconds=max(1, int(retry_delay_seconds or 30))))

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE network_neighbor_wake_queue
                    SET state = ?,
                        available_at = ?,
                        lease_expires_at = NULL,
                        last_error = ?,
                        failed_at = CASE WHEN ? = 'failed' THEN ? ELSE failed_at END,
                        updated_at = ?
                    WHERE id = ? AND state = 'leased'
                    ''',
                    (
                        next_state,
                        None if exhausted else available_at,
                        str(error or "")[:1000],
                        next_state,
                        now_iso,
                        now_iso,
                        queue_id,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_neighbor_wake_queue_item(queue_id)

    def list_network_neighbor_wake_queue(
        self,
        *,
        states: Optional[list[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        normalized_states = [str(item).strip() for item in (states or []) if str(item).strip()]
        params: list[Any] = []
        query = 'SELECT * FROM network_neighbor_wake_queue WHERE 1=1'
        if normalized_states:
            placeholders = ",".join("?" for _ in normalized_states)
            query += f' AND state IN ({placeholders})'
            params.extend(normalized_states)
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(max(1, min(int(limit or 50), 200)))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._hydrate_network_neighbor_wake_queue_row(dict(row)) for row in cursor.fetchall()]

    def _hydrate_network_neighbor_task_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["taskId"] = data.get("id")
        data["targetMode"] = data.get("target_mode") or "auto"
        data["originSessionId"] = data.get("origin_session_id")
        data["originRunId"] = data.get("origin_run_id")
        data["wakePolicy"] = data.get("wake_policy") or "inbox"
        data["requiredCapabilities"] = json.loads(data["required_capabilities_json"]) if data.get("required_capabilities_json") else []
        data["workspaceBinding"] = json.loads(data["workspace_binding_json"]) if data.get("workspace_binding_json") else {}
        data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
        data["deadlineAt"] = data.get("deadline_at")
        data["createdAt"] = data.get("created_at")
        data["updatedAt"] = data.get("updated_at")
        data["completedAt"] = data.get("completed_at")
        return data

    def upsert_network_neighbor_task(
        self,
        *,
        task_id: str,
        body: str,
        title: str | None = None,
        status: str = "queued",
        target_mode: str = "auto",
        origin_session_id: str | None = None,
        origin_run_id: str | None = None,
        wake_policy: str = "inbox",
        required_capabilities: Optional[list[str]] = None,
        workspace_binding: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        deadline_at: str | None = None,
    ) -> Dict[str, Any]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO network_neighbor_tasks
                    (id, title, body, status, target_mode, origin_session_id, origin_run_id, wake_policy, required_capabilities_json, workspace_binding_json, metadata_json, deadline_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        body = excluded.body,
                        status = excluded.status,
                        target_mode = excluded.target_mode,
                        origin_session_id = COALESCE(excluded.origin_session_id, network_neighbor_tasks.origin_session_id),
                        origin_run_id = COALESCE(excluded.origin_run_id, network_neighbor_tasks.origin_run_id),
                        wake_policy = excluded.wake_policy,
                        required_capabilities_json = excluded.required_capabilities_json,
                        workspace_binding_json = excluded.workspace_binding_json,
                        metadata_json = excluded.metadata_json,
                        deadline_at = COALESCE(excluded.deadline_at, network_neighbor_tasks.deadline_at),
                        updated_at = excluded.updated_at
                    ''',
                    (
                        task_id,
                        title,
                        body,
                        status or "queued",
                        target_mode or "auto",
                        origin_session_id,
                        origin_run_id,
                        wake_policy or "inbox",
                        json.dumps(to_jsonable(required_capabilities or []), ensure_ascii=False),
                        json.dumps(to_jsonable(workspace_binding or {}), ensure_ascii=False),
                        json.dumps(to_jsonable(metadata or {}), ensure_ascii=False),
                        deadline_at,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_neighbor_task(task_id) or {}

    def get_network_neighbor_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = str(task_id or "").strip()
        if not normalized_id:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_neighbor_tasks WHERE id = ?', (normalized_id,))
            row = cursor.fetchone()
            return self._hydrate_network_neighbor_task_row(dict(row)) if row else None

    def update_network_neighbor_task_status(self, task_id: str, *, status: str, completed: bool = False) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE network_neighbor_tasks
                    SET status = ?,
                        completed_at = CASE WHEN ? THEN COALESCE(completed_at, ?) ELSE completed_at END,
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (status, 1 if completed else 0, now_iso, now_iso, task_id),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_neighbor_task(task_id)

    def list_network_neighbor_tasks(self, *, statuses: Optional[list[str]] = None, limit: int = 50) -> List[Dict[str, Any]]:
        normalized_statuses = [str(item).strip() for item in (statuses or []) if str(item).strip()]
        params: list[Any] = []
        query = 'SELECT * FROM network_neighbor_tasks WHERE 1=1'
        if normalized_statuses:
            placeholders = ",".join("?" for _ in normalized_statuses)
            query += f' AND status IN ({placeholders})'
            params.extend(normalized_statuses)
        query += ' ORDER BY updated_at DESC, created_at DESC LIMIT ?'
        params.append(max(1, min(int(limit or 50), 200)))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._hydrate_network_neighbor_task_row(dict(row)) for row in cursor.fetchall()]

    def _hydrate_network_neighbor_assignment_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["assignmentId"] = data.get("id")
        data["taskId"] = data.get("task_id")
        data["linkId"] = data.get("link_id")
        data["peerId"] = data.get("peer_id")
        data["parentAssignmentId"] = data.get("parent_assignment_id")
        data["requiredCapabilities"] = json.loads(data["required_capabilities_json"]) if data.get("required_capabilities_json") else []
        data["wakePolicy"] = data.get("wake_policy") or "inbox"
        data["runId"] = data.get("run_id")
        data["resultId"] = data.get("result_id")
        data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
        data["createdAt"] = data.get("created_at")
        data["updatedAt"] = data.get("updated_at")
        data["completedAt"] = data.get("completed_at")
        data["depth"] = int(data.get("depth") or 0)
        return data

    def upsert_network_neighbor_assignment(
        self,
        *,
        assignment_id: str,
        task_id: str,
        link_id: str,
        peer_id: str,
        body: str,
        parent_assignment_id: str | None = None,
        depth: int = 0,
        status: str = "queued",
        required_capabilities: Optional[list[str]] = None,
        wake_policy: str = "inbox",
        run_id: str | None = None,
        result_id: str | None = None,
        error: str | None = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO network_neighbor_assignments
                    (id, task_id, link_id, peer_id, parent_assignment_id, depth, status, body, required_capabilities_json, wake_policy, run_id, result_id, error, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        body = excluded.body,
                        required_capabilities_json = excluded.required_capabilities_json,
                        wake_policy = excluded.wake_policy,
                        run_id = COALESCE(excluded.run_id, network_neighbor_assignments.run_id),
                        result_id = COALESCE(excluded.result_id, network_neighbor_assignments.result_id),
                        error = COALESCE(excluded.error, network_neighbor_assignments.error),
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    ''',
                    (
                        assignment_id,
                        task_id,
                        link_id,
                        peer_id,
                        parent_assignment_id,
                        max(0, int(depth or 0)),
                        status or "queued",
                        body,
                        json.dumps(to_jsonable(required_capabilities or []), ensure_ascii=False),
                        wake_policy or "inbox",
                        run_id,
                        result_id,
                        error,
                        json.dumps(to_jsonable(metadata or {}), ensure_ascii=False),
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_neighbor_assignment(assignment_id) or {}

    def get_network_neighbor_assignment(self, assignment_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = str(assignment_id or "").strip()
        if not normalized_id:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_neighbor_assignments WHERE id = ?', (normalized_id,))
            row = cursor.fetchone()
            return self._hydrate_network_neighbor_assignment_row(dict(row)) if row else None

    def update_network_neighbor_assignment_status(
        self,
        assignment_id: str,
        *,
        status: str,
        run_id: str | None = None,
        result_id: str | None = None,
        error: str | None = None,
        completed: bool = False,
    ) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE network_neighbor_assignments
                    SET status = ?,
                        run_id = COALESCE(?, run_id),
                        result_id = COALESCE(?, result_id),
                        error = COALESCE(?, error),
                        completed_at = CASE WHEN ? THEN COALESCE(completed_at, ?) ELSE completed_at END,
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (status, run_id, result_id, error, 1 if completed else 0, now_iso, now_iso, assignment_id),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_neighbor_assignment(assignment_id)

    def list_network_neighbor_assignments(
        self,
        *,
        task_id: str | None = None,
        link_id: str | None = None,
        statuses: Optional[list[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        params: list[Any] = []
        query = 'SELECT * FROM network_neighbor_assignments WHERE 1=1'
        if task_id:
            query += ' AND task_id = ?'
            params.append(task_id)
        if link_id:
            query += ' AND link_id = ?'
            params.append(link_id)
        normalized_statuses = [str(item).strip() for item in (statuses or []) if str(item).strip()]
        if normalized_statuses:
            placeholders = ",".join("?" for _ in normalized_statuses)
            query += f' AND status IN ({placeholders})'
            params.extend(normalized_statuses)
        query += ' ORDER BY created_at ASC LIMIT ?'
        params.append(max(1, min(int(limit or 100), 500)))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._hydrate_network_neighbor_assignment_row(dict(row)) for row in cursor.fetchall()]

    def _hydrate_network_neighbor_task_result_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["resultId"] = data.get("id")
        data["taskId"] = data.get("task_id")
        data["assignmentId"] = data.get("assignment_id")
        data["linkId"] = data.get("link_id")
        data["peerId"] = data.get("peer_id")
        data["needsAttention"] = bool(data.get("needs_attention"))
        data["requestedCapabilities"] = json.loads(data["requested_capabilities_json"]) if data.get("requested_capabilities_json") else []
        data["handoffReason"] = data.get("handoff_reason")
        data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
        data["createdAt"] = data.get("created_at")
        return data

    def add_network_neighbor_task_result(
        self,
        *,
        result_id: str,
        task_id: str,
        assignment_id: str,
        link_id: str,
        peer_id: str,
        status: str = "completed",
        summary: str | None = None,
        body: str | None = None,
        needs_attention: bool = False,
        requested_capabilities: Optional[list[str]] = None,
        handoff_reason: str | None = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT OR IGNORE INTO network_neighbor_task_results
                    (id, task_id, assignment_id, link_id, peer_id, status, summary, body, needs_attention, requested_capabilities_json, handoff_reason, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        result_id,
                        task_id,
                        assignment_id,
                        link_id,
                        peer_id,
                        status or "completed",
                        summary,
                        body,
                        1 if needs_attention else 0,
                        json.dumps(to_jsonable(requested_capabilities or []), ensure_ascii=False),
                        handoff_reason,
                        json.dumps(to_jsonable(metadata or {}), ensure_ascii=False),
                        now_iso,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_neighbor_task_result(result_id) or {}

    def get_network_neighbor_task_result(self, result_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = str(result_id or "").strip()
        if not normalized_id:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_neighbor_task_results WHERE id = ?', (normalized_id,))
            row = cursor.fetchone()
            return self._hydrate_network_neighbor_task_result_row(dict(row)) if row else None

    def list_network_neighbor_task_results(
        self,
        *,
        task_id: str | None = None,
        assignment_id: str | None = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        params: list[Any] = []
        query = 'SELECT * FROM network_neighbor_task_results WHERE 1=1'
        if task_id:
            query += ' AND task_id = ?'
            params.append(task_id)
        if assignment_id:
            query += ' AND assignment_id = ?'
            params.append(assignment_id)
        query += ' ORDER BY created_at ASC LIMIT ?'
        params.append(max(1, min(int(limit or 100), 500)))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._hydrate_network_neighbor_task_result_row(dict(row)) for row in cursor.fetchall()]

    def _hydrate_network_relay_outbox_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        data["outboxId"] = data.get("id")
        data["targetPeerId"] = data.get("target_peer_id")
        data["linkId"] = data.get("link_id")
        data["localMessageId"] = data.get("local_message_id")
        data["envelope"] = json.loads(data["envelope_json"]) if data.get("envelope_json") else {}
        data["attemptCount"] = int(data.get("attempt_count") or 0)
        data["maxAttempts"] = int(data.get("max_attempts") or 0)
        data["availableAt"] = data.get("available_at")
        data["claimedBy"] = data.get("claimed_by")
        data["leaseExpiresAt"] = data.get("lease_expires_at")
        data["relayMessageId"] = data.get("relay_message_id")
        data["lastError"] = data.get("last_error")
        data["createdAt"] = data.get("created_at")
        data["updatedAt"] = data.get("updated_at")
        data["publishedAt"] = data.get("published_at")
        data["failedAt"] = data.get("failed_at")
        return data

    def add_network_relay_outbox_item(
        self,
        *,
        outbox_id: str,
        target_peer_id: str,
        envelope: dict[str, Any],
        link_id: str | None = None,
        local_message_id: str | None = None,
        max_attempts: int = 5,
    ) -> Dict[str, Any]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO network_relay_outbox
                    (id, target_peer_id, link_id, local_message_id, envelope_json, state, attempt_count, max_attempts, available_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                    ''',
                    (
                        outbox_id,
                        target_peer_id,
                        link_id,
                        local_message_id,
                        json.dumps(to_jsonable(envelope or {}), ensure_ascii=False),
                        max(1, min(int(max_attempts or 5), 20)),
                        now_iso,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_relay_outbox_item(outbox_id) or {}

    def get_network_relay_outbox_item(self, outbox_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = str(outbox_id or "").strip()
        if not normalized_id:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_relay_outbox WHERE id = ?', (normalized_id,))
            row = cursor.fetchone()
            return self._hydrate_network_relay_outbox_row(dict(row)) if row else None

    def claim_next_network_relay_outbox_item(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 180,
    ) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()
        lease_expires_at = latest_utc_iso(datetime.now(timezone.utc) + timedelta(seconds=max(30, int(lease_seconds or 180))))

        def _write():
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT *
                    FROM network_relay_outbox
                    WHERE attempt_count < max_attempts
                      AND (
                        (state IN ('queued', 'retry') AND (available_at IS NULL OR available_at <= ?))
                        OR (state = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                      )
                    ORDER BY created_at ASC
                    LIMIT 1
                    ''',
                    (now_iso, now_iso),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                outbox_id = row["id"]
                cursor.execute(
                    '''
                    UPDATE network_relay_outbox
                    SET state = 'leased',
                        attempt_count = attempt_count + 1,
                        claimed_by = ?,
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND attempt_count < max_attempts
                      AND (
                        (state IN ('queued', 'retry') AND (available_at IS NULL OR available_at <= ?))
                        OR (state = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                      )
                    ''',
                    (worker_id, lease_expires_at, now_iso, outbox_id, now_iso, now_iso),
                )
                if cursor.rowcount != 1:
                    conn.commit()
                    return None
                cursor.execute('SELECT * FROM network_relay_outbox WHERE id = ?', (outbox_id,))
                claimed = cursor.fetchone()
                conn.commit()
                return self._hydrate_network_relay_outbox_row(dict(claimed)) if claimed else None

        return self._run_write_with_retry(_write)

    def complete_network_relay_outbox_item(self, outbox_id: str, *, relay_message_id: str | None = None) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE network_relay_outbox
                    SET state = 'published',
                        relay_message_id = COALESCE(?, relay_message_id),
                        published_at = ?,
                        available_at = NULL,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE id = ? AND state = 'leased'
                    ''',
                    (relay_message_id, now_iso, now_iso, outbox_id),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return self.get_network_relay_outbox_item(outbox_id)

    def fail_network_relay_outbox_item(
        self,
        outbox_id: str,
        *,
        error: str,
        retry_delay_seconds: int = 30,
    ) -> Optional[Dict[str, Any]]:
        existing = self.get_network_relay_outbox_item(outbox_id)
        if not existing:
            return None
        now_iso = utc_now_iso()
        exhausted = int(existing.get("attemptCount") or 0) >= int(existing.get("maxAttempts") or 1)
        next_state = "dead_letter" if exhausted else "retry"
        available_at = latest_utc_iso(datetime.now(timezone.utc) + timedelta(seconds=max(1, int(retry_delay_seconds or 30))))

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE network_relay_outbox
                    SET state = ?,
                        available_at = ?,
                        lease_expires_at = NULL,
                        last_error = ?,
                        failed_at = CASE WHEN ? = 'dead_letter' THEN ? ELSE failed_at END,
                        updated_at = ?
                    WHERE id = ? AND state = 'leased'
                    ''',
                    (
                        next_state,
                        None if exhausted else available_at,
                        str(error or "")[:1000],
                        next_state,
                        now_iso,
                        now_iso,
                        outbox_id,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        if exhausted:
            self.add_network_relay_dead_letter(
                direction="outbound",
                peer_id=str(existing.get("targetPeerId") or ""),
                outbox_id=outbox_id,
                envelope=existing.get("envelope") or {},
                reason=str(error or "")[:1000],
                metadata={"attemptCount": existing.get("attemptCount"), "maxAttempts": existing.get("maxAttempts")},
            )
        return self.get_network_relay_outbox_item(outbox_id)

    def list_network_relay_outbox(
        self,
        *,
        states: Optional[list[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        normalized_states = [str(item).strip() for item in (states or []) if str(item).strip()]
        params: list[Any] = []
        query = 'SELECT * FROM network_relay_outbox WHERE 1=1'
        if normalized_states:
            placeholders = ",".join("?" for _ in normalized_states)
            query += f' AND state IN ({placeholders})'
            params.extend(normalized_states)
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(max(1, min(int(limit or 50), 200)))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._hydrate_network_relay_outbox_row(dict(row)) for row in cursor.fetchall()]

    def get_network_relay_cursor(self, peer_id: str) -> str:
        normalized_peer_id = str(peer_id or "").strip()
        if not normalized_peer_id:
            return ""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT cursor FROM network_relay_inbox_cursor WHERE peer_id = ?', (normalized_peer_id,))
            row = cursor.fetchone()
            return str(row["cursor"] or "") if row else ""

    def upsert_network_relay_cursor(self, *, peer_id: str, cursor: str | None) -> dict[str, Any]:
        normalized_peer_id = str(peer_id or "").strip()
        if not normalized_peer_id:
            return {}
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO network_relay_inbox_cursor (peer_id, cursor, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(peer_id) DO UPDATE SET
                        cursor = excluded.cursor,
                        updated_at = excluded.updated_at
                    ''',
                    (normalized_peer_id, str(cursor or ""), now_iso),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return {"peerId": normalized_peer_id, "cursor": str(cursor or ""), "updatedAt": now_iso}

    def add_network_relay_delivery_ack(
        self,
        *,
        peer_id: str,
        relay_message_id: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ack_id = f"nrack_{uuid.uuid4().hex}"
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT OR IGNORE INTO network_relay_delivery_acks
                    (id, peer_id, relay_message_id, state, metadata_json, created_at, acked_at)
                    VALUES (?, ?, ?, 'acked', ?, ?, ?)
                    ''',
                    (
                        ack_id,
                        str(peer_id or "").strip(),
                        str(relay_message_id or "").strip(),
                        json.dumps(to_jsonable(metadata or {}), ensure_ascii=False),
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return {"id": ack_id, "peerId": str(peer_id or "").strip(), "relayMessageId": str(relay_message_id or "").strip(), "state": "acked"}

    def add_network_relay_dead_letter(
        self,
        *,
        direction: str,
        peer_id: str | None = None,
        relay_message_id: str | None = None,
        outbox_id: str | None = None,
        envelope: Optional[dict[str, Any]] = None,
        reason: str | None = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        dead_letter_id = f"nrdl_{uuid.uuid4().hex}"
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO network_relay_dead_letters
                    (id, direction, peer_id, relay_message_id, outbox_id, envelope_json, reason, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        dead_letter_id,
                        str(direction or "inbound").strip() or "inbound",
                        peer_id,
                        relay_message_id,
                        outbox_id,
                        json.dumps(to_jsonable(envelope or {}), ensure_ascii=False) if envelope is not None else None,
                        str(reason or "")[:1000],
                        json.dumps(to_jsonable(metadata or {}), ensure_ascii=False),
                        now_iso,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)
        return {"id": dead_letter_id, "direction": direction, "peerId": peer_id, "relayMessageId": relay_message_id, "reason": str(reason or "")[:1000]}

    def list_network_relay_dead_letters(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_relay_dead_letters ORDER BY created_at DESC LIMIT ?', (max(1, min(int(limit or 50), 200)),))
            payload: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                data = dict(row)
                data["deadLetterId"] = data.get("id")
                data["peerId"] = data.get("peer_id")
                data["relayMessageId"] = data.get("relay_message_id")
                data["outboxId"] = data.get("outbox_id")
                data["envelope"] = json.loads(data["envelope_json"]) if data.get("envelope_json") else {}
                data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
                data["createdAt"] = data.get("created_at")
                payload.append(data)
            return payload

    def claim_next_pending_chat_user_message(self, *, session_id: str, consumed_run_id: str) -> Optional[Dict[str, Any]]:
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT *
                    FROM chat_user_message_queue
                    WHERE session_id = ? AND state = 'pending'
                    ORDER BY ordinal ASC, created_at ASC
                    LIMIT 1
                    ''',
                    (session_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                queue_id = row["id"]
                conn.execute(
                    '''
                    UPDATE chat_user_message_queue
                    SET state = 'consumed',
                        consumed_at = ?,
                        consumed_run_id = ?,
                        updated_at = ?
                    WHERE id = ?
                    ''',
                    (now_iso, consumed_run_id, now_iso, queue_id),
                )
                conn.commit()
                data = dict(row)
                data["state"] = "consumed"
                data["consumed_at"] = now_iso
                data["consumed_run_id"] = consumed_run_id
                data["updated_at"] = now_iso
                return self._hydrate_chat_user_message_queue_row(data)

        return self._run_write_with_retry(_write)

    def requeue_promoted_chat_user_messages_for_run(
        self,
        *,
        session_id: str,
        run_id: str,
        reason: str,
    ) -> List[Dict[str, Any]]:
        normalized_session_id = str(session_id or "").strip()
        normalized_run_id = str(run_id or "").strip()
        if not normalized_session_id or not normalized_run_id:
            return []
        now_iso = utc_now_iso()

        def _write():
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT *
                    FROM chat_user_message_queue
                    WHERE session_id = ?
                      AND run_id = ?
                      AND state = 'promoted'
                      AND injected_at IS NULL
                    ORDER BY ordinal ASC, created_at ASC
                    ''',
                    (normalized_session_id, normalized_run_id),
                )
                rows = [dict(row) for row in cursor.fetchall()]
                if not rows:
                    return []
                for row in rows:
                    metadata = json.loads(row.get("metadata_json") or "{}")
                    metadata["requeuedReason"] = reason
                    metadata["requeuedAt"] = now_iso
                    conn.execute(
                        '''
                        UPDATE chat_user_message_queue
                        SET state = 'pending',
                            updated_at = ?,
                            metadata_json = ?
                        WHERE id = ?
                        ''',
                        (now_iso, json.dumps(to_jsonable(metadata), ensure_ascii=False), row["id"]),
                    )
                    row["state"] = "pending"
                    row["updated_at"] = now_iso
                    row["metadata_json"] = json.dumps(to_jsonable(metadata), ensure_ascii=False)
                conn.commit()
                return [self._hydrate_chat_user_message_queue_row(row) for row in rows]

        return self._run_write_with_retry(_write) or []

    def add_runtime_artifact(
        self,
        artifact_id: str,
        artifact_kind: str,
        mime_type: Optional[str] = None,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        message_id: Optional[str] = None,
        title: Optional[str] = None,
        source_path: Optional[str] = None,
        workspace_path: Optional[str] = None,
        external_url: Optional[str] = None,
        preview_url: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT OR REPLACE INTO runtime_artifacts
                    (id, session_id, run_id, message_id, artifact_kind, mime_type, title, source_path, workspace_path, external_url, preview_url, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        artifact_id,
                        session_id,
                        run_id,
                        message_id,
                        artifact_kind,
                        mime_type,
                        title,
                        source_path,
                        workspace_path,
                        external_url,
                        preview_url,
                        json.dumps(metadata or {}, ensure_ascii=False),
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def attach_runtime_artifacts_to_message(
        self,
        *,
        session_id: str,
        run_id: str,
        message_id: str,
    ) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                UPDATE runtime_artifacts
                SET message_id = ?
                WHERE session_id = ?
                  AND run_id = ?
                  AND (message_id IS NULL OR message_id = '')
                ''',
                (message_id, session_id, run_id),
            )
            conn.commit()
            return cursor.rowcount

    def list_runtime_artifacts(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM runtime_artifacts WHERE 1=1"
        params: list[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows: list[Dict[str, Any]] = []
            for row in cursor.fetchall():
                data = dict(row)
                data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
                rows.append(normalize_artifact_record(data))
            return rows

    def get_runtime_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM runtime_artifacts WHERE id = ?", (artifact_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
            return normalize_artifact_record(data)

    def get_run_record(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM run_records WHERE id = ?', (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["metadata"] = json.loads(data["metadata"]) if data.get("metadata") else {}
            return data

    def list_run_records(
        self,
        *,
        session_id: Optional[str] = None,
        run_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        query = 'SELECT * FROM run_records WHERE 1=1'
        params: list[Any] = []
        if session_id:
            query += ' AND session_id = ?'
            params.append(session_id)
        if run_type:
            query += ' AND run_type = ?'
            params.append(run_type)
        if status:
            query += ' AND status = ?'
            params.append(status)
        query += ' ORDER BY started_at DESC LIMIT ?'
        params.append(limit)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows: list[Dict[str, Any]] = []
            for row in cursor.fetchall():
                data = dict(row)
                data["metadata"] = json.loads(data["metadata"]) if data.get("metadata") else {}
                rows.append(data)
            return rows

    def add_engineering_proof_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        entry_id = str(entry.get("id") or uuid.uuid4())

        def _dump(value: Any) -> str:
            return json.dumps(to_jsonable(value if value is not None else []), ensure_ascii=False)

        with self.get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO engineering_proof_entries
                (id, session_id, run_id, task_brief_id, mode, patch_intent, read_set_json, write_set_json,
                 changed_files_json, commands_json, diagnostics_json, verification_status, residual_risks_json,
                 metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    entry_id,
                    entry.get("session_id") or entry.get("sessionId"),
                    entry.get("run_id") or entry.get("runId"),
                    entry.get("task_brief_id") or entry.get("taskBriefId"),
                    str(entry.get("mode") or "dry_run"),
                    str(entry.get("patch_intent") or entry.get("patchIntent") or ""),
                    _dump(entry.get("read_set") if "read_set" in entry else entry.get("readSet")),
                    _dump(entry.get("write_set") if "write_set" in entry else entry.get("writeSet")),
                    _dump(entry.get("changed_files") if "changed_files" in entry else entry.get("changedFiles")),
                    _dump(entry.get("commands")),
                    _dump(entry.get("diagnostics")),
                    str(entry.get("verification_status") or entry.get("verificationStatus") or "unverified"),
                    _dump(entry.get("residual_risks") if "residual_risks" in entry else entry.get("residualRisks")),
                    _dump(entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}),
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()
        row = self.get_engineering_proof_entry(entry_id)
        return row or {"id": entry_id}

    def get_engineering_proof_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM engineering_proof_entries WHERE id = ?", (entry_id,))
            row = cursor.fetchone()
            return self._engineering_proof_row_to_dict(row) if row else None

    def list_engineering_proof_entries(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM engineering_proof_entries WHERE 1=1"
        params: list[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if status:
            query += " AND verification_status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 20), 100)))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._engineering_proof_row_to_dict(row) for row in cursor.fetchall()]

    def _engineering_proof_row_to_dict(self, row: Any) -> Dict[str, Any]:
        data = dict(row)

        def _load(key: str, fallback: Any) -> Any:
            raw = data.pop(key, None)
            if not raw:
                return fallback
            try:
                return json.loads(raw)
            except Exception:
                return fallback

        data["readSet"] = _load("read_set_json", [])
        data["writeSet"] = _load("write_set_json", [])
        data["changedFiles"] = _load("changed_files_json", [])
        data["commands"] = _load("commands_json", [])
        data["diagnostics"] = _load("diagnostics_json", {})
        data["residualRisks"] = _load("residual_risks_json", [])
        data["metadata"] = _load("metadata_json", {})
        data["taskBriefId"] = data.pop("task_brief_id", None)
        data["patchIntent"] = data.pop("patch_intent", "")
        data["verificationStatus"] = data.pop("verification_status", "unverified")
        data["createdAt"] = data.pop("created_at", None)
        data["updatedAt"] = data.pop("updated_at", None)
        data["sessionId"] = data.pop("session_id", None)
        data["runId"] = data.pop("run_id", None)
        return data

    def upsert_engineering_workset_observation(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        entry_id = str(entry.get("id") or uuid.uuid4())

        def _dump(value: Any, fallback: Any) -> str:
            return json.dumps(to_jsonable(value if value is not None else fallback), ensure_ascii=False)

        with self.get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO engineering_workset_observations
                (id, session_id, run_id, task_brief_id, delegation_id, decision_source, phase, decision_json,
                 warning_or_block_reason, manual_override, outside_write_set_files_json, correlation_status,
                 metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    session_id = excluded.session_id,
                    run_id = excluded.run_id,
                    task_brief_id = excluded.task_brief_id,
                    delegation_id = excluded.delegation_id,
                    decision_source = excluded.decision_source,
                    phase = excluded.phase,
                    decision_json = excluded.decision_json,
                    warning_or_block_reason = excluded.warning_or_block_reason,
                    manual_override = excluded.manual_override,
                    outside_write_set_files_json = excluded.outside_write_set_files_json,
                    correlation_status = excluded.correlation_status,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                ''',
                (
                    entry_id,
                    entry.get("session_id") or entry.get("sessionId"),
                    entry.get("run_id") or entry.get("runId"),
                    entry.get("task_brief_id") or entry.get("taskBriefId"),
                    entry.get("delegation_id") or entry.get("delegationId"),
                    str(entry.get("decision_source") or entry.get("decisionSource") or "planner_auto"),
                    str(entry.get("phase") or "dispatch"),
                    _dump(entry.get("decision"), {}),
                    str(entry.get("warning_or_block_reason") or entry.get("warningOrBlockReason") or ""),
                    1 if bool(entry.get("manual_override") or entry.get("manualOverride")) else 0,
                    _dump(entry.get("outside_write_set_files") if "outside_write_set_files" in entry else entry.get("outsideWriteSetFiles"), []),
                    str(entry.get("correlation_status") or entry.get("correlationStatus") or ""),
                    _dump(entry.get("metadata"), {}),
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()
        row = self.get_engineering_workset_observation(entry_id)
        return row or {"id": entry_id}

    def get_engineering_workset_observation(self, entry_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM engineering_workset_observations WHERE id = ?", (entry_id,))
            row = cursor.fetchone()
            return self._engineering_workset_observation_row_to_dict(row) if row else None

    def list_engineering_workset_observations(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        task_brief_id: Optional[str] = None,
        decision_source: Optional[str] = None,
        limit: int = 40,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM engineering_workset_observations WHERE 1=1"
        params: list[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if task_brief_id:
            query += " AND task_brief_id = ?"
            params.append(task_brief_id)
        if decision_source:
            query += " AND decision_source = ?"
            params.append(decision_source)
        query += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 40), 200)))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._engineering_workset_observation_row_to_dict(row) for row in cursor.fetchall()]

    def _engineering_workset_observation_row_to_dict(self, row: Any) -> Dict[str, Any]:
        data = dict(row)

        def _load(key: str, fallback: Any) -> Any:
            raw = data.pop(key, None)
            if not raw:
                return fallback
            try:
                return json.loads(raw)
            except Exception:
                return fallback

        data["taskBriefId"] = data.pop("task_brief_id", None)
        data["delegationId"] = data.pop("delegation_id", None)
        data["decisionSource"] = data.pop("decision_source", "planner_auto")
        data["decision"] = _load("decision_json", {})
        data["warningOrBlockReason"] = data.pop("warning_or_block_reason", "")
        data["manualOverride"] = bool(data.pop("manual_override", 0))
        data["outsideWriteSetFiles"] = _load("outside_write_set_files_json", [])
        data["correlationStatus"] = data.pop("correlation_status", "")
        data["metadata"] = _load("metadata_json", {})
        data["createdAt"] = data.pop("created_at", None)
        data["updatedAt"] = data.pop("updated_at", None)
        data["sessionId"] = data.pop("session_id", None)
        data["runId"] = data.pop("run_id", None)
        return data

    def clear_memory_runtime_diagnostics(self) -> Dict[str, int]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            target_task_kinds = ("session_extraction", "maintenance", "periodic_summary")
            placeholders = ",".join("?" for _ in target_task_kinds)
            run_filter = f"""
                run_type = 'memory'
                AND COALESCE(json_extract(metadata, '$.task_kind'), '') IN ({placeholders})
            """
            cursor.execute(
                f"SELECT COUNT(*) AS count FROM run_records WHERE {run_filter}",
                target_task_kinds,
            )
            row = cursor.fetchone()
            run_count = int(row["count"]) if row else 0
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM model_invocation_logs
                WHERE run_id IN (SELECT id FROM run_records WHERE {run_filter})
                   OR capability_class = 'memory_extraction'
                   OR request_kind = 'memory_extraction'
                """,
                target_task_kinds,
            )
            row = cursor.fetchone()
            invocation_count = int(row["count"]) if row else 0
            cursor.execute(
                f"DELETE FROM runtime_events WHERE run_id IN (SELECT id FROM run_records WHERE {run_filter})",
                target_task_kinds,
            )
            deleted_events = cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0
            cursor.execute(
                f"DELETE FROM runtime_snapshots WHERE run_id IN (SELECT id FROM run_records WHERE {run_filter})",
                target_task_kinds,
            )
            deleted_snapshots = cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0
            cursor.execute(
                f"""
                DELETE FROM model_invocation_logs
                WHERE run_id IN (SELECT id FROM run_records WHERE {run_filter})
                   OR capability_class = 'memory_extraction'
                   OR request_kind = 'memory_extraction'
                """,
                target_task_kinds,
            )
            cursor.execute(
                f"DELETE FROM run_records WHERE {run_filter}",
                target_task_kinds,
            )
            conn.commit()
            return {
                "deletedRuns": run_count,
                "deletedInvocations": invocation_count,
                "deletedRuntimeEvents": deleted_events,
                "deletedRuntimeSnapshots": deleted_snapshots,
            }

    def add_pending_approval(
        self,
        approval_id: str,
        session_id: str,
        run_id: str,
        approval_kind: str,
        status: str,
        request: Dict[str, Any],
        response: Optional[Dict[str, Any]] = None,
        expires_at: Optional[str] = None,
    ):
        request_str = json.dumps(request, ensure_ascii=False)
        response_str = json.dumps(response, ensure_ascii=False) if response else None
        with self.get_connection() as conn:
            conn.execute(
                '''
                INSERT OR REPLACE INTO pending_approvals
                (id, session_id, run_id, approval_kind, status, request_json, response_json, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''',
                (approval_id, session_id, run_id, approval_kind, status, request_str, response_str, expires_at),
            )
            conn.commit()

    def get_pending_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM pending_approvals WHERE id = ?', (approval_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["request"] = json.loads(data["request_json"]) if data.get("request_json") else {}
            data["response"] = json.loads(data["response_json"]) if data.get("response_json") else None
            return data

    def list_pending_approvals(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = 'SELECT * FROM pending_approvals WHERE 1=1'
        params: list[Any] = []
        if session_id:
            query += ' AND session_id = ?'
            params.append(session_id)
        if run_id:
            query += ' AND run_id = ?'
            params.append(run_id)
        if status:
            query += ' AND status = ?'
            params.append(status)
        query += ' ORDER BY created_at DESC'

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = []
            for row in cursor.fetchall():
                data = dict(row)
                data["request"] = json.loads(data["request_json"]) if data.get("request_json") else {}
                data["response"] = json.loads(data["response_json"]) if data.get("response_json") else None
                rows.append(data)
            return rows

    def _skill_safety_review_from_row(self, row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        for column, output_key, fallback in [
            ("scan_payload_json", "scanPayload", {}),
            ("llm_review_json", "llmReview", None),
            ("reasons_json", "reasons", []),
            ("flagged_files_json", "flaggedFiles", []),
            ("finding_categories_json", "findingCategories", []),
        ]:
            raw = data.pop(column, None)
            if raw:
                try:
                    data[output_key] = json.loads(raw)
                except json.JSONDecodeError:
                    data[output_key] = fallback
            else:
                data[output_key] = fallback
        data["disabled"] = bool(data.get("disabled"))
        if "active" in data:
            data["active"] = bool(data.get("active"))
        return data

    def upsert_skill_safety_review(
        self,
        *,
        review_id: str,
        skill_id: str | None,
        skill_name: str | None,
        skill_path: str,
        instruction_path: str | None,
        identity_key: str,
        content_hash: str,
        manifest_hash: str | None,
        static_verdict: str,
        effective_verdict: str,
        user_override: str | None = None,
        disabled: bool = False,
        scan_payload: Dict[str, Any] | None = None,
        llm_review: Dict[str, Any] | None = None,
        reasons: list[Any] | None = None,
        flagged_files: list[Any] | None = None,
        finding_categories: list[Any] | None = None,
    ) -> Dict[str, Any]:
        now = latest_utc_iso()
        scan_payload_json = json.dumps(to_jsonable(scan_payload or {}), ensure_ascii=False)
        llm_review_json = json.dumps(to_jsonable(llm_review), ensure_ascii=False) if llm_review is not None else None
        reasons_json = json.dumps(to_jsonable(list(reasons or [])), ensure_ascii=False)
        flagged_files_json = json.dumps(to_jsonable(list(flagged_files or [])), ensure_ascii=False)
        finding_categories_json = json.dumps(to_jsonable(list(finding_categories or [])), ensure_ascii=False)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM skill_safety_reviews WHERE identity_key = ? AND content_hash = ?",
                (identity_key, content_hash),
            )
            existing = cursor.fetchone()
            if existing:
                existing_data = dict(existing)
                preserved_override = existing_data.get("user_override") if user_override is None else user_override
                preserved_disabled = bool(existing_data.get("disabled")) if user_override is None else bool(disabled)
                effective = "block" if preserved_disabled else effective_verdict
                cursor.execute(
                    '''
                    UPDATE skill_safety_reviews
                    SET skill_id = ?, skill_name = ?, skill_path = ?, instruction_path = ?,
                        manifest_hash = ?, static_verdict = ?, effective_verdict = ?,
                        user_override = ?, disabled = ?, scan_payload_json = ?, llm_review_json = ?,
                        reasons_json = ?, flagged_files_json = ?, finding_categories_json = ?,
                        reviewed_at = ?, active = 1, orphaned_at = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE identity_key = ? AND content_hash = ?
                    ''',
                    (
                        skill_id,
                        skill_name,
                        skill_path,
                        instruction_path,
                        manifest_hash,
                        static_verdict,
                        effective,
                        preserved_override,
                        1 if preserved_disabled else 0,
                        scan_payload_json,
                        llm_review_json,
                        reasons_json,
                        flagged_files_json,
                        finding_categories_json,
                        now,
                        identity_key,
                        content_hash,
                    ),
                )
            else:
                cursor.execute(
                    '''
                    INSERT INTO skill_safety_reviews
                    (id, skill_id, skill_name, skill_path, instruction_path, identity_key, content_hash,
                     manifest_hash, static_verdict, effective_verdict, user_override, disabled,
                     scan_payload_json, llm_review_json, reasons_json, flagged_files_json,
                     finding_categories_json, reviewed_at, active, orphaned_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ''',
                    (
                        review_id,
                        skill_id,
                        skill_name,
                        skill_path,
                        instruction_path,
                        identity_key,
                        content_hash,
                        manifest_hash,
                        static_verdict,
                        effective_verdict,
                        user_override,
                        1 if disabled else 0,
                        scan_payload_json,
                        llm_review_json,
                        reasons_json,
                        flagged_files_json,
                        finding_categories_json,
                        now,
                    ),
                )
            conn.commit()
            cursor.execute(
                "SELECT * FROM skill_safety_reviews WHERE identity_key = ? AND content_hash = ?",
                (identity_key, content_hash),
            )
            row = cursor.fetchone()
            return self._skill_safety_review_from_row(row) if row else {}

    def get_skill_safety_review(self, *, identity_key: str, content_hash: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM skill_safety_reviews WHERE identity_key = ? AND content_hash = ?",
                (identity_key, content_hash),
            )
            row = cursor.fetchone()
            return self._skill_safety_review_from_row(row) if row else None

    def get_skill_safety_review_by_id(self, review_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM skill_safety_reviews WHERE id = ?", (review_id,))
            row = cursor.fetchone()
            return self._skill_safety_review_from_row(row) if row else None

    def list_skill_safety_reviews(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM skill_safety_reviews WHERE 1=1"
        params: list[Any] = []
        normalized_status = str(status or "").strip().lower()
        if normalized_status == "disabled":
            query += " AND disabled = 1"
        elif normalized_status == "approved":
            query += " AND user_override = 'approved' AND disabled = 0"
        elif normalized_status == "review":
            query += " AND effective_verdict = 'review' AND disabled = 0"
        elif normalized_status == "blocked":
            query += " AND effective_verdict = 'block'"
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(500, int(limit or 100))))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._skill_safety_review_from_row(row) for row in cursor.fetchall()]

    def mark_skill_safety_reviews_inactive(
        self,
        *,
        skill_path: str,
        instruction_path: str | None = None,
    ) -> int:
        now = latest_utc_iso()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if instruction_path:
                cursor.execute(
                    '''
                    UPDATE skill_safety_reviews
                    SET active = 0, orphaned_at = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE skill_path = ? OR instruction_path = ?
                    ''',
                    (now, skill_path, instruction_path),
                )
            else:
                cursor.execute(
                    '''
                    UPDATE skill_safety_reviews
                    SET active = 0, orphaned_at = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE skill_path = ?
                    ''',
                    (now, skill_path),
                )
            conn.commit()
            return int(cursor.rowcount or 0)

    def update_skill_safety_review_override(
        self,
        *,
        review_id: str,
        user_override: str | None,
        disabled: bool,
        effective_verdict: str,
    ) -> Optional[Dict[str, Any]]:
        now = latest_utc_iso()
        approved_at = now if user_override == "approved" else None
        disabled_at = now if disabled else None
        revoked_at = now if user_override is None else None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                UPDATE skill_safety_reviews
                SET user_override = ?, disabled = ?, effective_verdict = ?,
                    approved_at = COALESCE(?, approved_at),
                    disabled_at = COALESCE(?, disabled_at),
                    revoked_at = COALESCE(?, revoked_at),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (
                    user_override,
                    1 if disabled else 0,
                    effective_verdict,
                    approved_at,
                    disabled_at,
                    revoked_at,
                    review_id,
                ),
            )
            conn.commit()
            cursor.execute("SELECT * FROM skill_safety_reviews WHERE id = ?", (review_id,))
            row = cursor.fetchone()
            return self._skill_safety_review_from_row(row) if row else None

    def _safety_allowlist_from_row(self, row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
        data = dict(row)
        raw_metadata = data.pop("metadata_json", None)
        if raw_metadata:
            try:
                data["metadata"] = json.loads(raw_metadata)
            except json.JSONDecodeError:
                data["metadata"] = {}
        else:
            data["metadata"] = {}
        data["enabled"] = bool(data.get("enabled"))
        return data

    def upsert_safety_allowlist_entry(
        self,
        *,
        entry_id: str,
        normalized_target_hash: str,
        normalized_target_label: str | None,
        path_plane: str,
        runtime_source: str,
        action: str,
        risk_code: str,
        governance_target: str | None = None,
        approval_id: str | None = None,
        approval_kind: str | None = None,
        source: str | None = None,
        enabled: bool = True,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        metadata_json = json.dumps(to_jsonable(metadata or {}), ensure_ascii=False)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                INSERT INTO safety_allowlist_entries
                (id, normalized_target_hash, normalized_target_label, path_plane, runtime_source, action,
                 risk_code, governance_target, approval_id, approval_kind, source, enabled, metadata_json,
                 revoked_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(normalized_target_hash, path_plane, runtime_source, action, risk_code)
                DO UPDATE SET
                    normalized_target_label = excluded.normalized_target_label,
                    governance_target = excluded.governance_target,
                    approval_id = excluded.approval_id,
                    approval_kind = excluded.approval_kind,
                    source = excluded.source,
                    enabled = excluded.enabled,
                    metadata_json = excluded.metadata_json,
                    revoked_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                ''',
                (
                    entry_id,
                    normalized_target_hash,
                    normalized_target_label,
                    path_plane,
                    runtime_source,
                    action,
                    risk_code,
                    governance_target,
                    approval_id,
                    approval_kind,
                    source,
                    1 if enabled else 0,
                    metadata_json,
                ),
            )
            conn.commit()
            cursor.execute(
                '''
                SELECT * FROM safety_allowlist_entries
                WHERE normalized_target_hash = ? AND path_plane = ? AND runtime_source = ? AND action = ? AND risk_code = ?
                ''',
                (normalized_target_hash, path_plane, runtime_source, action, risk_code),
            )
            row = cursor.fetchone()
            return self._safety_allowlist_from_row(row) if row else {}

    def find_safety_allowlist_entry(
        self,
        *,
        normalized_target_hash: str,
        path_plane: str,
        runtime_source: str,
        action: str,
        risk_code: str,
        enabled_only: bool = True,
    ) -> Optional[Dict[str, Any]]:
        query = '''
            SELECT * FROM safety_allowlist_entries
            WHERE normalized_target_hash = ? AND path_plane = ? AND runtime_source = ? AND action = ? AND risk_code = ?
        '''
        params: list[Any] = [normalized_target_hash, path_plane, runtime_source, action, risk_code]
        if enabled_only:
            query += " AND enabled = 1"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            return self._safety_allowlist_from_row(row) if row else None

    def list_safety_allowlist_entries(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM safety_allowlist_entries WHERE 1=1"
        params: list[Any] = []
        normalized_status = str(status or "").strip().lower()
        if normalized_status in {"active", "enabled"}:
            query += " AND enabled = 1"
        elif normalized_status in {"revoked", "disabled"}:
            query += " AND enabled = 0"
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(500, int(limit or 100))))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [self._safety_allowlist_from_row(row) for row in cursor.fetchall()]

    def revoke_safety_allowlist_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        now = latest_utc_iso()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                UPDATE safety_allowlist_entries
                SET enabled = 0, revoked_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (now, entry_id),
            )
            conn.commit()
            cursor.execute("SELECT * FROM safety_allowlist_entries WHERE id = ?", (entry_id,))
            row = cursor.fetchone()
            return self._safety_allowlist_from_row(row) if row else None

    def add_ask_user_interaction(
        self,
        *,
        interaction_id: str,
        session_id: str,
        run_id: str,
        assistant_message_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        question: Optional[str] = None,
        prompt: Optional[str] = None,
        request: Optional[Dict[str, Any]] = None,
        answer_text: Optional[str] = None,
        status: str = "pending",
        resolved_at: Optional[str] = None,
    ) -> None:
        request_payload = dict(request or {})
        normalized_question = str(question or request_payload.get("question") or request_payload.get("prompt") or "").strip() or None
        normalized_prompt = str(prompt or request_payload.get("prompt") or normalized_question or "").strip() or None
        if normalized_question:
            request_payload.setdefault("question", normalized_question)
        if normalized_prompt:
            request_payload.setdefault("prompt", normalized_prompt)
        request_payload.setdefault("interactionKind", "ask_user")
        request_str = json.dumps(request_payload, ensure_ascii=False)
        normalized_answer = str(answer_text or "").strip() or None
        with self.get_connection() as conn:
            conn.execute(
                '''
                INSERT OR REPLACE INTO ask_user_interactions
                (id, session_id, run_id, assistant_message_id, tool_call_id, question, prompt, request_json, answer_text, status, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    interaction_id,
                    session_id,
                    run_id,
                    assistant_message_id,
                    tool_call_id,
                    normalized_question,
                    normalized_prompt,
                    request_str,
                    normalized_answer,
                    status,
                    resolved_at,
                ),
            )
            conn.commit()

    def get_ask_user_interaction(self, interaction_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM ask_user_interactions WHERE id = ?', (interaction_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["request"] = json.loads(data["request_json"]) if data.get("request_json") else {}
            return data

    def list_ask_user_interactions(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = 'SELECT * FROM ask_user_interactions WHERE 1=1'
        params: list[Any] = []
        if session_id:
            query += ' AND session_id = ?'
            params.append(session_id)
        if run_id:
            query += ' AND run_id = ?'
            params.append(run_id)
        if status:
            query += ' AND status = ?'
            params.append(status)
        query += ' ORDER BY created_at DESC'

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = []
            for row in cursor.fetchall():
                data = dict(row)
                data["request"] = json.loads(data["request_json"]) if data.get("request_json") else {}
                rows.append(data)
            return rows

    def create_workflow_ledger(
        self,
        *,
        workflow_id: str,
        session_id: str,
        conversation_id: Optional[str],
        root_run_id: str,
        workflow_kind: str,
        status: str,
        owner_runtime: Optional[str],
        owner_agent_id: Optional[str],
        current_step_id: Optional[str] = None,
        parent_workflow_id: Optional[str] = None,
        resume_strategy: Optional[str] = None,
        recoverable: bool = True,
        last_error_code: Optional[str] = None,
        last_error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        metadata_str = json.dumps(metadata or {}, ensure_ascii=False)

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT OR REPLACE INTO workflow_ledgers
                    (id, session_id, conversation_id, root_run_id, parent_workflow_id, workflow_kind, status,
                     owner_runtime, owner_agent_id, current_step_id, resume_strategy, recoverable,
                     last_error_code, last_error_message, metadata_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ''',
                    (
                        workflow_id,
                        session_id,
                        conversation_id,
                        root_run_id,
                        parent_workflow_id,
                        workflow_kind,
                        status,
                        owner_runtime,
                        owner_agent_id,
                        current_step_id,
                        resume_strategy,
                        1 if recoverable else 0,
                        last_error_code,
                        last_error_message,
                        metadata_str,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def get_workflow_ledger(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM workflow_ledgers WHERE id = ?', (workflow_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
            data["recoverable"] = bool(data.get("recoverable"))
            return data

    def get_workflow_ledger_for_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT * FROM workflow_ledgers
                WHERE root_run_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                ''',
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["metadata"] = json.loads(data["metadata_json"]) if data.get("metadata_json") else {}
            data["recoverable"] = bool(data.get("recoverable"))
            return data

    def update_workflow_ledger(
        self,
        workflow_id: str,
        *,
        status: Optional[str] = None,
        owner_runtime: Optional[str] = None,
        owner_agent_id: Optional[str] = None,
        current_step_id: Optional[str] = None,
        resume_strategy: Optional[str] = None,
        recoverable: Optional[bool] = None,
        last_error_code: Optional[str] = None,
        last_error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        clear_error: bool = False,
    ) -> None:
        existing = self.get_workflow_ledger(workflow_id)
        if not existing:
            return

        merged_metadata = dict(existing.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE workflow_ledgers
                    SET status = COALESCE(?, status),
                        owner_runtime = COALESCE(?, owner_runtime),
                        owner_agent_id = COALESCE(?, owner_agent_id),
                        current_step_id = COALESCE(?, current_step_id),
                        resume_strategy = COALESCE(?, resume_strategy),
                        recoverable = COALESCE(?, recoverable),
                        last_error_code = CASE WHEN ? THEN NULL ELSE COALESCE(?, last_error_code) END,
                        last_error_message = CASE WHEN ? THEN NULL ELSE COALESCE(?, last_error_message) END,
                        metadata_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''',
                    (
                        status,
                        owner_runtime,
                        owner_agent_id,
                        current_step_id,
                        resume_strategy,
                        (1 if recoverable else 0) if recoverable is not None else None,
                        1 if clear_error else 0,
                        last_error_code,
                        1 if clear_error else 0,
                        last_error_message,
                        json.dumps(merged_metadata, ensure_ascii=False),
                        workflow_id,
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def upsert_workflow_step(
        self,
        *,
        step_id: str,
        workflow_id: str,
        session_id: str,
        run_id: Optional[str],
        sequence_index: int,
        step_key: str,
        title: str,
        status: str,
        owner_runtime: Optional[str],
        owner_agent_id: Optional[str],
        approval_id: Optional[str] = None,
        input_payload: Optional[Dict[str, Any]] = None,
        output_payload: Optional[Dict[str, Any]] = None,
        projection_payload: Optional[Dict[str, Any]] = None,
        last_event_seq: Optional[int] = None,
        retry_count: Optional[int] = None,
        resume_token: Optional[str] = None,
        last_error_code: Optional[str] = None,
        last_error_message: Optional[str] = None,
        merge_projection: bool = False,
        clear_error: bool = False,
    ) -> None:
        existing = self.get_workflow_step(step_id)
        projection = dict(existing.get("projection") or {}) if existing and merge_projection else {}
        if projection_payload:
            projection.update(projection_payload)
        input_data = input_payload if input_payload is not None else (existing.get("input") if existing else None)
        output_data = output_payload if output_payload is not None else (existing.get("output") if existing else None)
        retry_value = retry_count if retry_count is not None else (existing.get("retry_count") if existing else 0)

        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    INSERT OR REPLACE INTO workflow_steps
                    (id, workflow_id, session_id, run_id, sequence_index, step_key, title, status,
                     owner_runtime, owner_agent_id, approval_id, input_json, output_json, projection_json,
                     last_event_seq, retry_count, resume_token, last_error_code, last_error_message, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ''',
                    (
                        step_id,
                        workflow_id,
                        session_id,
                        run_id,
                        sequence_index,
                        step_key,
                        title,
                        status,
                        owner_runtime,
                        owner_agent_id,
                        approval_id,
                        json.dumps(input_data, ensure_ascii=False) if input_data is not None else None,
                        json.dumps(output_data, ensure_ascii=False) if output_data is not None else None,
                        json.dumps(projection, ensure_ascii=False) if projection else None,
                        last_event_seq if last_event_seq is not None else (existing.get("last_event_seq") if existing else 0),
                        retry_value,
                        resume_token if resume_token is not None else (existing.get("resume_token") if existing else None),
                        None if clear_error else (last_error_code if last_error_code is not None else (existing.get("last_error_code") if existing else None)),
                        None if clear_error else (last_error_message if last_error_message is not None else (existing.get("last_error_message") if existing else None)),
                    ),
                )
                conn.commit()

        self._run_write_with_retry(_write)

    def get_workflow_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM workflow_steps WHERE id = ?', (step_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["input"] = json.loads(data["input_json"]) if data.get("input_json") else None
            data["output"] = json.loads(data["output_json"]) if data.get("output_json") else None
            data["projection"] = json.loads(data["projection_json"]) if data.get("projection_json") else {}
            return data

    def get_workflow_steps(self, workflow_id: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT * FROM workflow_steps
                WHERE workflow_id = ?
                ORDER BY sequence_index ASC, created_at ASC
                ''',
                (workflow_id,),
            )
            rows: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                data = dict(row)
                data["input"] = json.loads(data["input_json"]) if data.get("input_json") else None
                data["output"] = json.loads(data["output_json"]) if data.get("output_json") else None
                data["projection"] = json.loads(data["projection_json"]) if data.get("projection_json") else {}
                rows.append(data)
            return rows

    def get_active_workflow_projection(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT ws.*
                FROM workflow_steps ws
                JOIN workflow_ledgers wl ON wl.id = ws.workflow_id
                WHERE ws.session_id = ?
                  AND wl.status IN ('running', 'waiting_approval', 'waiting_external_tool', 'paused', 'interrupted', 'recoverable_failed')
                  AND ws.projection_json IS NOT NULL
                ORDER BY ws.updated_at DESC
                LIMIT 1
                ''',
                (session_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["input"] = json.loads(data["input_json"]) if data.get("input_json") else None
            data["output"] = json.loads(data["output_json"]) if data.get("output_json") else None
            data["projection"] = json.loads(data["projection_json"]) if data.get("projection_json") else {}
            return data

    def list_active_run_records(self, statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        active_statuses = statuses or ["queued", "running", "waiting_approval", "waiting_input", "waiting_external_tool", "paused"]
        placeholders = ", ".join("?" for _ in active_statuses)
        query = f"SELECT * FROM run_records WHERE status IN ({placeholders}) ORDER BY started_at ASC"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, active_statuses)
            rows: List[Dict[str, Any]] = []
            for row in cursor.fetchall():
                data = dict(row)
                data["metadata"] = json.loads(data["metadata"]) if data.get("metadata") else {}
                rows.append(data)
            return rows

    def update_pending_approval(
        self,
        approval_id: str,
        *,
        status: str,
        response: Optional[Dict[str, Any]] = None,
    ):
        response_str = json.dumps(response, ensure_ascii=False) if response else None
        with self.get_connection() as conn:
            conn.execute(
                '''
                UPDATE pending_approvals
                SET status = ?,
                    response_json = COALESCE(?, response_json),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (status, response_str, approval_id),
            )
            conn.commit()

    def update_ask_user_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        answer_text: Optional[str] = None,
        request: Optional[Dict[str, Any]] = None,
        resolved_at: Optional[str] = None,
    ) -> None:
        request_str = json.dumps(request, ensure_ascii=False) if request is not None else None
        normalized_answer = str(answer_text or "").strip() or None
        with self.get_connection() as conn:
            conn.execute(
                '''
                UPDATE ask_user_interactions
                SET status = ?,
                    request_json = COALESCE(?, request_json),
                    answer_text = COALESCE(?, answer_text),
                    resolved_at = CASE
                        WHEN ? IN ('resolved', 'cancelled') THEN COALESCE(?, CURRENT_TIMESTAMP)
                        ELSE resolved_at
                    END
                WHERE id = ?
                ''',
                (status, request_str, normalized_answer, status, resolved_at, interaction_id),
            )
            conn.commit()

    # --- Telemetry / Usage Operations ---

    def add_model_invocation_log(self, record: Dict[str, Any]):
        self.observability_db.add_model_invocation_log(record)

    def upsert_usage_ledger(self, record: Dict[str, Any]):
        with self.get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO usage_ledger (
                    id, bucket_date, scope_type, scope_id, provider_id, model_id, role, capability_class,
                    invocations, success_count, error_count, input_tokens, output_tokens, total_tokens,
                    cost_total, latency_ms_total, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(bucket_date, scope_type, scope_id, model_id, role) DO UPDATE SET
                    provider_id=excluded.provider_id,
                    capability_class=excluded.capability_class,
                    invocations=usage_ledger.invocations + excluded.invocations,
                    success_count=usage_ledger.success_count + excluded.success_count,
                    error_count=usage_ledger.error_count + excluded.error_count,
                    input_tokens=usage_ledger.input_tokens + excluded.input_tokens,
                    output_tokens=usage_ledger.output_tokens + excluded.output_tokens,
                    total_tokens=usage_ledger.total_tokens + excluded.total_tokens,
                    cost_total=usage_ledger.cost_total + excluded.cost_total,
                    latency_ms_total=usage_ledger.latency_ms_total + excluded.latency_ms_total,
                    updated_at=CURRENT_TIMESTAMP
                ''',
                (
                    record.get("id"),
                    record.get("bucket_date"),
                    record.get("scope_type"),
                    record.get("scope_id"),
                    record.get("provider_id"),
                    record.get("model_id"),
                    record.get("role"),
                    record.get("capability_class"),
                    int(record.get("invocations") or 0),
                    int(record.get("success_count") or 0),
                    int(record.get("error_count") or 0),
                    int(record.get("input_tokens") or 0),
                    int(record.get("output_tokens") or 0),
                    int(record.get("total_tokens") or 0),
                    float(record.get("cost_total") or 0.0),
                    float(record.get("latency_ms_total") or 0.0),
                ),
            )
            conn.commit()

    def add_provider_health_log(self, record: Dict[str, Any]):
        self.observability_db.add_provider_health_log(record)

    def add_prompt_cache_event(self, record: Dict[str, Any]) -> None:
        self.observability_db.add_prompt_cache_event(record)

    def add_prompt_cache_segments(self, event_id: str, segments: List[Dict[str, Any]]) -> None:
        self.observability_db.add_prompt_cache_segments(event_id, segments)

    def get_llm_response_cache(self, response_cache_key: str) -> Optional[Dict[str, Any]]:
        return self.observability_db.get_llm_response_cache(response_cache_key)

    def upsert_llm_response_cache(self, record: Dict[str, Any]) -> None:
        self.observability_db.upsert_llm_response_cache(record)

    def increment_llm_response_cache_hit(self, response_cache_key: str) -> None:
        self.observability_db.increment_llm_response_cache_hit(response_cache_key)

    def get_prompt_cache_stats(self, limit: int = 50, days: int = 1) -> Dict[str, Any]:
        return self.observability_db.get_prompt_cache_stats(limit=limit, days=days)

    def get_prompt_cache_prefix_use_counts(self, days: int = 1) -> Dict[str, int]:
        return self.observability_db.get_prompt_cache_prefix_use_counts(days=days)

    def purge_prompt_cache(self) -> Dict[str, Any]:
        return self.observability_db.purge_prompt_cache()

    def get_counts_snapshot(self) -> Dict[str, int]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            counts: Dict[str, int] = {}
            for key, table in (
                ("sessions", "sessions"),
                ("messages", "messages"),
                ("runs", "run_records"),
                ("approvals", "pending_approvals"),
                ("ask_user_interactions", "ask_user_interactions"),
            ):
                cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
                row = cursor.fetchone()
                counts[key] = int(row["count"]) if row else 0
            cursor.execute("SELECT COUNT(*) AS count FROM pending_approvals WHERE status = 'pending'")
            row = cursor.fetchone()
            counts["pending_approvals"] = int(row["count"]) if row else 0
            cursor.execute("SELECT COUNT(*) AS count FROM run_records WHERE status IN ('queued', 'running', 'waiting_approval', 'waiting_input', 'waiting_external_tool', 'paused')")
            row = cursor.fetchone()
            counts["active_runs"] = int(row["count"]) if row else 0
            try:
                row = cursor.execute("SELECT COALESCE(SUM(invocations), 0) AS count FROM usage_ledger").fetchone()
                counts["invocations"] = int(row["count"]) if row else 0
            except Exception:
                try:
                    with self.observability_db.get_connection() as obs_conn:
                        row = obs_conn.execute("SELECT COUNT(*) AS count FROM model_invocation_logs").fetchone()
                        counts["invocations"] = int(row["count"]) if row else 0
                except Exception:
                    counts["invocations"] = 0
            return counts

    def get_recent_model_invocations(self, limit: int = 20, days: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.observability_db.get_recent_model_invocations(limit=limit, days=days)

    def list_model_invocations(
        self,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        capability_class: Optional[str] = None,
        request_kind: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        return self.observability_db.list_model_invocations(
            session_id=session_id,
            run_id=run_id,
            capability_class=capability_class,
            request_kind=request_kind,
            status=status,
            limit=limit,
        )

    def get_model_usage_distribution(self, days: int = 7, limit: int = 12) -> List[Dict[str, Any]]:
        rows = self.get_usage_ledger_model_usage_distribution(days=days, limit=limit)
        if rows:
            return rows
        return self.observability_db.get_model_usage_distribution(days=days, limit=limit)

    def get_model_invocation_window_totals(self, days: int = 1) -> Dict[str, Any]:
        totals = self.get_usage_ledger_window_totals(days=days)
        if int(totals.get("invocations") or 0) > 0 or int(totals.get("total_tokens") or 0) > 0:
            return totals
        return self.observability_db.get_model_invocation_window_totals(days=days)

    def get_usage_ledger_window_totals(self, days: int = 7) -> Dict[str, Any]:
        with self.get_connection() as conn:
            row = conn.execute(
                '''
                SELECT COALESCE(SUM(invocations), 0) AS invocations,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(cost_total), 0) AS cost_total
                FROM usage_ledger
                WHERE bucket_date >= date('now', ?)
                ''',
                (f'-{max(days - 1, 0)} day',),
            ).fetchone()
            return dict(row) if row else {"invocations": 0, "total_tokens": 0, "cost_total": 0.0}

    def get_usage_ledger_daily_activity(self, days: int = 7) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            return [dict(row) for row in conn.execute(
                '''
                SELECT bucket_date AS day,
                       COALESCE(SUM(invocations), 0) AS invocations,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM usage_ledger
                WHERE bucket_date >= date('now', ?)
                GROUP BY bucket_date
                ORDER BY bucket_date ASC
                ''',
                (f'-{max(days - 1, 0)} day',),
            ).fetchall()]

    def get_usage_ledger_model_usage_distribution(self, days: int = 7, limit: int = 12) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            return [dict(row) for row in conn.execute(
                '''
                SELECT model_id,
                       provider_id AS provider_name,
                       provider_id,
                       COALESCE(SUM(invocations), 0) AS invocations,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(cost_total), 0) AS cost_total
                FROM usage_ledger
                WHERE bucket_date >= date('now', ?)
                GROUP BY model_id, provider_id
                ORDER BY invocations DESC, total_tokens DESC
                LIMIT ?
                ''',
                (f'-{max(days - 1, 0)} day', limit),
            ).fetchall()]

    def get_daily_telemetry_activity(self, days: int = 7) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                WITH RECURSIVE dates(day) AS (
                    SELECT date('now', ?)
                    UNION ALL
                    SELECT date(day, '+1 day') FROM dates WHERE day < date('now')
                ),
                message_stats AS (
                    SELECT date(created_at) AS day, COUNT(*) AS messages
                    FROM messages
                    WHERE created_at >= datetime('now', ?)
                    GROUP BY date(created_at)
                ),
                run_stats AS (
                    SELECT date(started_at) AS day, COUNT(*) AS runs
                    FROM run_records
                    WHERE started_at >= datetime('now', ?)
                    GROUP BY date(started_at)
                )
                SELECT
                    dates.day AS day,
                    COALESCE(message_stats.messages, 0) AS messages,
                    COALESCE(run_stats.runs, 0) AS runs,
                    0 AS invocations,
                    0 AS total_tokens
                FROM dates
                LEFT JOIN message_stats ON message_stats.day = dates.day
                LEFT JOIN run_stats ON run_stats.day = dates.day
                ORDER BY dates.day ASC
                ''',
                (
                    f'-{max(days - 1, 0)} day',
                    f'-{max(days, 1)} day',
                    f'-{max(days, 1)} day',
                ),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        invocation_rows = {
            str(item.get("day")): item
            for item in self.get_usage_ledger_daily_activity(days=days)
        }
        if not invocation_rows:
            invocation_rows = {
                str(item.get("day")): item
                for item in self.observability_db.get_daily_invocation_activity(days=days)
            }
        for row in rows:
            invocations = invocation_rows.get(str(row.get("day"))) or {}
            row["invocations"] = int(invocations.get("invocations") or 0)
            row["total_tokens"] = int(invocations.get("total_tokens") or 0)
        return rows

    def get_provider_health_summary(self, days: int = 7) -> List[Dict[str, Any]]:
        return self.observability_db.get_provider_health_summary(days=days)

    def get_usage_ledger_totals(
        self,
        *,
        bucket_date: Optional[str] = None,
        scope_type: Optional[str] = None,
        scope_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        query = '''
            SELECT
                COALESCE(SUM(invocations), 0) AS invocations,
                COALESCE(SUM(success_count), 0) AS success_count,
                COALESCE(SUM(error_count), 0) AS error_count,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(cost_total), 0) AS cost_total,
                COALESCE(SUM(latency_ms_total), 0) AS latency_ms_total
            FROM usage_ledger
            WHERE 1=1
        '''
        params: list[Any] = []
        if bucket_date:
            query += ' AND bucket_date = ?'
            params.append(bucket_date)
        if scope_type:
            query += ' AND scope_type = ?'
            params.append(scope_type)
        if scope_id:
            query += ' AND scope_id = ?'
            params.append(scope_id)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            return dict(row) if row else {
                "invocations": 0,
                "success_count": 0,
                "error_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_total": 0.0,
                "latency_ms_total": 0.0,
            }

    def get_run_invocation_totals(self, run_id: str) -> Dict[str, Any]:
        return self.observability_db.get_run_invocation_totals(run_id)

    # --- Scope Binding / Project Registry Cache Operations ---

    def upsert_session_scope_binding(self, binding: Dict[str, Any]):
        with self.get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO session_scope_bindings (
                    session_id, conversation_id, thread_id, user_id, workspace_id, workspace_path,
                    project_id, workflow_id, channel_type, channel_remote_id, scope_hint,
                    resolved_scope, scope_source, scope_confidence, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    conversation_id=excluded.conversation_id,
                    thread_id=excluded.thread_id,
                    user_id=excluded.user_id,
                    workspace_id=excluded.workspace_id,
                    workspace_path=excluded.workspace_path,
                    project_id=excluded.project_id,
                    workflow_id=excluded.workflow_id,
                    channel_type=excluded.channel_type,
                    channel_remote_id=excluded.channel_remote_id,
                    scope_hint=excluded.scope_hint,
                    resolved_scope=excluded.resolved_scope,
                    scope_source=excluded.scope_source,
                    scope_confidence=excluded.scope_confidence,
                    status=excluded.status,
                    updated_at=CURRENT_TIMESTAMP
                ''',
                (
                    binding.get("session_id"),
                    binding.get("conversation_id"),
                    binding.get("thread_id"),
                    binding.get("user_id"),
                    binding.get("workspace_id"),
                    binding.get("workspace_path"),
                    binding.get("project_id"),
                    binding.get("workflow_id"),
                    binding.get("channel_type"),
                    binding.get("channel_remote_id"),
                    binding.get("scope_hint"),
                    binding.get("resolved_scope"),
                    binding.get("scope_source"),
                    float(binding.get("scope_confidence", 1.0)),
                    binding.get("status", "active"),
                ),
            )
            conn.commit()

    def get_session_scope_binding(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM session_scope_bindings WHERE session_id = ?',
                (session_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_latest_workflow_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT * FROM workflow_ledgers
                WHERE session_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                ''',
                (session_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def close_session_scope_binding(self, session_id: str, status: str = "inactive"):
        with self.get_connection() as conn:
            conn.execute(
                '''
                UPDATE session_scope_bindings
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                ''',
                (status, session_id),
            )
            conn.commit()

    def get_memory_extraction_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM memory_extraction_state WHERE session_id = ?',
                (session_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert_memory_extraction_state(self, state: Dict[str, Any]):
        payload = {
            "session_id": state.get("session_id"),
            "last_processed_message_id": state.get("last_processed_message_id"),
            "last_processed_message_count": int(state.get("last_processed_message_count") or 0),
            "last_content_hash": state.get("last_content_hash"),
            "last_run_id": state.get("last_run_id"),
            "last_processed_at": state.get("last_processed_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with self.get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO memory_extraction_state (
                    session_id,
                    last_processed_message_id,
                    last_processed_message_count,
                    last_content_hash,
                    last_run_id,
                    last_processed_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_processed_message_id = excluded.last_processed_message_id,
                    last_processed_message_count = excluded.last_processed_message_count,
                    last_content_hash = excluded.last_content_hash,
                    last_run_id = excluded.last_run_id,
                    last_processed_at = excluded.last_processed_at,
                    updated_at = CURRENT_TIMESTAMP
                ''',
                (
                    payload["session_id"],
                    payload["last_processed_message_id"],
                    payload["last_processed_message_count"],
                    payload["last_content_hash"],
                    payload["last_run_id"],
                    payload["last_processed_at"],
                ),
            )
            conn.commit()

    def upsert_workspace_project_binding(
        self,
        workspace_id: str,
        workspace_path: str,
        project_id: str,
        source: str,
        confidence: float = 1.0,
    ):
        with self.get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO workspace_project_bindings (
                    workspace_id, workspace_path, project_id, source, confidence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    workspace_path=excluded.workspace_path,
                    project_id=excluded.project_id,
                    source=excluded.source,
                    confidence=excluded.confidence,
                    updated_at=CURRENT_TIMESTAMP
                ''',
                (workspace_id, workspace_path, project_id, source, confidence),
            )
            conn.commit()

    def get_workspace_project_binding(
        self,
        *,
        workspace_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if workspace_id:
                cursor.execute(
                    'SELECT * FROM workspace_project_bindings WHERE workspace_id = ?',
                    (workspace_id,),
                )
            elif workspace_path:
                cursor.execute(
                    'SELECT * FROM workspace_project_bindings WHERE workspace_path = ?',
                    (workspace_path,),
                )
            else:
                return None
            row = cursor.fetchone()
            return dict(row) if row else None

    def sync_project_descriptor_cache(self, project: Dict[str, Any]):
        with self.get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO project_descriptors_cache (
                    project_id, name, workspace_id, workspace_path, default_scope, tags_json, active, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_id) DO UPDATE SET
                    name=excluded.name,
                    workspace_id=excluded.workspace_id,
                    workspace_path=excluded.workspace_path,
                    default_scope=excluded.default_scope,
                    tags_json=excluded.tags_json,
                    active=excluded.active,
                    synced_at=CURRENT_TIMESTAMP
                ''',
                (
                    project.get("project_id") or project.get("id"),
                    project.get("name"),
                    project.get("workspace_id"),
                    project.get("workspace_path"),
                    project.get("default_scope")
                    or project.get("defaultScope")
                    or (
                        f"project:{project.get('project_id') or project.get('id')}"
                        if (project.get("project_id") or project.get("id"))
                        else "global"
                    ),
                    json.dumps(project.get("tags", []), ensure_ascii=False),
                    1 if project.get("active", True) else 0,
                ),
            )
            conn.commit()

    def delete_project_descriptor_cache(self, project_id: str):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM project_descriptors_cache WHERE project_id = ?', (project_id,))
            conn.commit()

    def add_scope_resolution_event(self, event: Dict[str, Any]):
        with self.get_connection() as conn:
            conn.execute(
                '''
                INSERT INTO scope_resolution_events (
                    id, session_id, run_id, requested_scope, resolved_scope, source, confidence, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    event["id"],
                    event["session_id"],
                    event.get("run_id"),
                    event.get("requested_scope"),
                    event["resolved_scope"],
                    event["source"],
                    float(event.get("confidence", 1.0)),
                    json.dumps(event.get("evidence") or {}, ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_scope_resolution_events(self, session_id: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT * FROM scope_resolution_events
                WHERE session_id = ?
                ORDER BY created_at DESC
                ''',
                (session_id,),
            )
            rows = []
            for row in cursor.fetchall():
                item = dict(row)
                item["evidence"] = json.loads(item["evidence_json"]) if item.get("evidence_json") else {}
                rows.append(item)
            return rows

    # --- System Audit Log Operations ---
    
    def add_audit_log(self, source_type: str, action: str, status: str, details: str = None):
        """Appends a new audit log entry."""
        self.observability_db.add_audit_log(source_type, action, status, details)
            
    def get_audit_logs(self, limit: int = 100, offset: int = 0, source_type: str = None, status: str = None) -> List[Dict[str, Any]]:
        """Retrieves audit logs with optional filtering and pagination."""
        return self.observability_db.get_audit_logs(limit=limit, offset=offset, source_type=source_type, status=status)

    def clear_audit_logs(self, *, source_type: str = None, status: str = None) -> Dict[str, Any]:
        result = self.observability_db.clear_audit_logs(source_type=source_type, status=status)
        result.update({"source_type": source_type, "status": status})
        return result

# Singleton Instantiation
import os
from core.v8_agent_os_paths import STATE_DB_PATH

DB_PATH = STATE_DB_PATH
db = DatabaseManager(DB_PATH)
