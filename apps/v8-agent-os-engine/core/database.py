import sqlite3
import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
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
                    run_id TEXT,
                    latest_seq INTEGER NOT NULL,
                    snapshot_type TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
                    FOREIGN KEY (run_id) REFERENCES run_records (id) ON DELETE SET NULL
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
                    run_id TEXT NOT NULL,
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
            conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_canonical_messages_session_id ON chat_canonical_messages (session_id, ordinal ASC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_canonical_messages_run_id ON chat_canonical_messages (run_id, updated_at DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_canonical_messages_updated_at ON chat_canonical_messages (updated_at DESC)')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_canonical_messages_session_ordinal ON chat_canonical_messages (session_id, ordinal)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_session_lane_records_active_run_id ON session_lane_records (active_run_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_session_lane_records_updated_at ON session_lane_records (updated_at DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_session_lane_queue_entries_session_id ON session_lane_queue_entries (session_id, created_at DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_session_lane_queue_entries_run_id ON session_lane_queue_entries (run_id, created_at DESC)')
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
            except Exception as e:
                print(f"[Database] Migration note: {e}")
            
            conn.commit()

    # --- Session Operations ---
    
    def create_or_update_session(self, session_id: str, title: str, user_id: str = "anonymous", agent_id: Optional[str] = None, metadata: dict = None):
        """Creates a new session or updates the updated_at timestamp if it exists."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, metadata FROM sessions WHERE id = ?', (session_id,))
            existing = cursor.fetchone()
            merged_metadata = metadata or {}
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
                if title and title not in ("New Chat", "新对话"):
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
                ''', (session_id, title, user_id, agent_id, meta_str, now_iso, now_iso))
            conn.commit()

    def _is_internal_runtime_title(self, title: str | None) -> bool:
        normalized = str(title or "").strip()
        return normalized.startswith(("Hook · ", "Cron · ", "Automation · "))

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

    def delete_message(self, message_id: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM messages WHERE id = ?', (message_id,))
            deleted = cursor.rowcount > 0
            if deleted:
                conn.commit()
            return deleted

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
                ORDER BY ordinal ASC, created_at ASC
                ''',
                (session_id,),
            )
            return [self._hydrate_chat_canonical_row(dict(row)) for row in cursor.fetchall()]

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
        finished_at = utc_now_iso() if status in {"completed", "failed", "cancelled"} else None
        def _write():
            with self.get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE run_records
                    SET status = ?,
                        error_message = COALESCE(?, error_message),
                        metadata = COALESCE(?, metadata),
                        finished_at = CASE
                            WHEN ? IN ('completed', 'failed', 'cancelled') THEN COALESCE(?, finished_at)
                            ELSE finished_at
                        END
                    WHERE id = ?
                    ''',
                    (status, error_message, meta_str, status, finished_at, run_id),
                )
                conn.commit()

        self._run_write_with_retry(_write)

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
                conn.execute(
                    '''
                    INSERT INTO runtime_events
                    (id, session_id, run_id, seq, kind, topic, event_ts, source_json, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        event["event_id"],
                        event.get("session_id"),
                        event.get("run_id"),
                        event.get("seq"),
                        event.get("kind", "event"),
                        event.get("topic"),
                        event.get("ts"),
                        json.dumps(source_payload, ensure_ascii=False),
                        json.dumps(event_payload, ensure_ascii=False),
                    ),
                )
                conn.commit()

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
                        reviewed_at = ?, updated_at = CURRENT_TIMESTAMP
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
                     finding_categories_json, reviewed_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
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
                  AND wl.status IN ('running', 'waiting_approval', 'paused', 'interrupted', 'recoverable_failed')
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
        active_statuses = statuses or ["queued", "running", "waiting_approval", "waiting_input", "paused"]
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
            cursor.execute("SELECT COUNT(*) AS count FROM run_records WHERE status IN ('queued', 'running', 'waiting_approval', 'waiting_input', 'paused')")
            row = cursor.fetchone()
            counts["active_runs"] = int(row["count"]) if row else 0
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
        return self.observability_db.get_model_usage_distribution(days=days, limit=limit)

    def get_model_invocation_window_totals(self, days: int = 1) -> Dict[str, Any]:
        return self.observability_db.get_model_invocation_window_totals(days=days)

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
