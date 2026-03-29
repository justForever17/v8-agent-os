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
);

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
);

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
);

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
);

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
);

CREATE TABLE IF NOT EXISTS workspace_project_bindings (
    workspace_id TEXT PRIMARY KEY,
    workspace_path TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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
);

CREATE TABLE IF NOT EXISTS project_descriptors_cache (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    workspace_id TEXT,
    workspace_path TEXT,
    default_scope TEXT NOT NULL,
    tags_json TEXT,
    active INTEGER DEFAULT 1,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
