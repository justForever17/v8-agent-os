def ensure_system_operation_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS system_operation_credentials (
        owner_id TEXT NOT NULL, action TEXT NOT NULL,
        username TEXT NOT NULL, domain TEXT NOT NULL, secret_ref TEXT NOT NULL,
        version TEXT NOT NULL, updated_at REAL NOT NULL,
        PRIMARY KEY(owner_id, action)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS system_operation_requests (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, session_id TEXT NOT NULL,
        run_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
        payload_json TEXT NOT NULL, credential_version TEXT NOT NULL,
        state TEXT NOT NULL, expires_at REAL NOT NULL, result_json TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_system_operations_run ON system_operation_requests(run_id, created_at)")
