from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from scripts.tools.repair_memory_index import run as run_memory_repair


HOME = Path.home() / ".v8-agent-os"
STATE_DB = HOME / "state.db"
KNOWLEDGE_DB = HOME / "memory" / ".index" / "knowledge.db"
CHROMA_SQLITE = HOME / "memory" / ".index" / "chroma_db" / "chroma.sqlite3"
PROJECTS_JSON = HOME / "projects.json"

OBSOLETE_SCOPE_PATTERNS = [
    re.compile(r"^project:memory-agent-[a-f0-9]+$"),
    re.compile(r"^project:memory-runtime-[a-f0-9]+$"),
    re.compile(r"^project:reg_scope_[a-f0-9]+$"),
    re.compile(r"^project:rag-[a-f0-9]+$"),
    re.compile(r"^project:rag-other-[a-f0-9]+$"),
]

OBSOLETE_SESSION_PATTERNS = [
    re.compile(r"^memory-agent-stability-[a-f0-9]+$"),
    re.compile(r"^scope-explicit-[a-f0-9]+$"),
    re.compile(r"^scope-workspace-[a-f0-9]+$"),
    re.compile(r"^channel:feishu:group:oc_scope_[a-f0-9]+$"),
]

LEGACY_FILES = [
    HOME / "v8chat.db",
    HOME / "system_audit_log.db",
    HOME / "memory" / "knowledge.db",
]


def _matches_any(value: str, patterns: list[re.Pattern[str]]) -> bool:
    normalized = str(value or "").strip()
    return any(pattern.match(normalized) for pattern in patterns)


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _fetch_all(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def run(payload: dict[str, Any] | None = None):
    payload = payload or {}
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = HOME / "backups" / f"obsolete_cleanup_{ts}"
    backup_root.mkdir(parents=True, exist_ok=True)

    state_conn = sqlite3.connect(str(STATE_DB))
    state_conn.execute("PRAGMA foreign_keys=ON")
    state_conn.row_factory = sqlite3.Row
    knowledge_conn = sqlite3.connect(str(KNOWLEDGE_DB))
    knowledge_conn.execute("PRAGMA foreign_keys=ON")
    knowledge_conn.row_factory = sqlite3.Row

    session_bindings = _fetch_all(
        state_conn,
        "SELECT * FROM session_scope_bindings ORDER BY updated_at DESC",
    )
    flagged_bindings = [row for row in session_bindings if _matches_any(row.get("resolved_scope", ""), OBSOLETE_SCOPE_PATTERNS)]
    flagged_session_ids = {
        row["session_id"]
        for row in flagged_bindings
        if row.get("session_id")
    }

    sessions = _fetch_all(state_conn, "SELECT * FROM sessions")
    flagged_sessions = [
        row
        for row in sessions
        if row.get("id") in flagged_session_ids or _matches_any(row.get("id", ""), OBSOLETE_SESSION_PATTERNS)
    ]
    flagged_session_ids.update(row["id"] for row in flagged_sessions if row.get("id"))

    flagged_messages = []
    flagged_runs = []
    flagged_events = []
    flagged_snapshots = []
    if flagged_session_ids:
        placeholders = ",".join("?" for _ in flagged_session_ids)
        params = tuple(sorted(flagged_session_ids))
        flagged_messages = _fetch_all(state_conn, f"SELECT * FROM messages WHERE session_id IN ({placeholders})", params)
        flagged_runs = _fetch_all(state_conn, f"SELECT * FROM run_records WHERE session_id IN ({placeholders})", params)
        flagged_events = _fetch_all(state_conn, f"SELECT * FROM runtime_events WHERE session_id IN ({placeholders})", params)
        flagged_snapshots = _fetch_all(state_conn, f"SELECT * FROM runtime_snapshots WHERE session_id IN ({placeholders})", params)

    project_descriptors = _fetch_all(state_conn, "SELECT * FROM project_descriptors_cache")
    flagged_descriptors = [
        row
        for row in project_descriptors
        if _matches_any(row.get("default_scope", ""), OBSOLETE_SCOPE_PATTERNS)
        or _matches_any(f"project:{row.get('project_id', '')}", OBSOLETE_SCOPE_PATTERNS)
    ]
    flagged_project_ids = {row["project_id"] for row in flagged_descriptors if row.get("project_id")}
    flagged_workspace_ids = {row["workspace_id"] for row in flagged_descriptors if row.get("workspace_id")}

    workspace_bindings = []
    if flagged_workspace_ids:
        placeholders = ",".join("?" for _ in flagged_workspace_ids)
        workspace_bindings = _fetch_all(
            state_conn,
            f"SELECT * FROM workspace_project_bindings WHERE workspace_id IN ({placeholders})",
            tuple(sorted(flagged_workspace_ids)),
        )

    scope_events = []
    if flagged_session_ids:
        placeholders = ",".join("?" for _ in flagged_session_ids)
        scope_events = _fetch_all(
            state_conn,
            f"SELECT * FROM scope_resolution_events WHERE session_id IN ({placeholders})",
            tuple(sorted(flagged_session_ids)),
        )

    knowledge_rows = _fetch_all(
        knowledge_conn,
        "SELECT rowid, * FROM knowledge ORDER BY updated_at DESC",
    )
    flagged_knowledge = [row for row in knowledge_rows if _matches_any(row.get("scope", ""), OBSOLETE_SCOPE_PATTERNS)]
    flagged_fact_ids = [row["id"] for row in flagged_knowledge if row.get("id")]
    flagged_rowids = [row["rowid"] for row in flagged_knowledge if row.get("rowid") is not None]

    projects_payload = {}
    flagged_projects_json = []
    if PROJECTS_JSON.exists():
        projects_payload = json.loads(PROJECTS_JSON.read_text(encoding="utf-8"))
        projects = list(projects_payload.get("projects") or [])
        flagged_projects_json = [
            row
            for row in projects
            if _matches_any(f"project:{row.get('id', '')}", OBSOLETE_SCOPE_PATTERNS)
            or _matches_any(str(row.get("defaultScope") or ""), OBSOLETE_SCOPE_PATTERNS)
        ]

    chroma_segment_dirs = [
        path.name
        for path in (HOME / "memory" / ".index" / "chroma_db").iterdir()
        if path.is_dir()
    ]
    chroma_conn = sqlite3.connect(str(CHROMA_SQLITE))
    chroma_conn.row_factory = sqlite3.Row
    active_vector_segments = {
        row["id"]
        for row in _fetch_all(chroma_conn, "SELECT id FROM segments WHERE scope = 'VECTOR'")
    }
    orphan_segment_dirs = [name for name in chroma_segment_dirs if name not in active_vector_segments]
    chroma_conn.close()

    _json_dump(
        backup_root / "cleanup_backup.json",
        {
            "session_bindings": flagged_bindings,
            "sessions": flagged_sessions,
            "messages": flagged_messages,
            "run_records": flagged_runs,
            "runtime_events": flagged_events,
            "runtime_snapshots": flagged_snapshots,
            "project_descriptors": flagged_descriptors,
            "workspace_bindings": workspace_bindings,
            "scope_resolution_events": scope_events,
            "knowledge_rows": flagged_knowledge,
            "projects_json_entries": flagged_projects_json,
            "orphan_segment_dirs": orphan_segment_dirs,
        },
    )

    if flagged_rowids:
        placeholders = ",".join("?" for _ in flagged_rowids)
        knowledge_conn.execute(f"DELETE FROM knowledge_fts WHERE rowid IN ({placeholders})", tuple(flagged_rowids))
    if flagged_fact_ids:
        placeholders = ",".join("?" for _ in flagged_fact_ids)
        knowledge_conn.execute(f"DELETE FROM relations WHERE source_fact_id IN ({placeholders})", tuple(flagged_fact_ids))
        knowledge_conn.execute(f"DELETE FROM knowledge WHERE id IN ({placeholders})", tuple(flagged_fact_ids))
    knowledge_conn.commit()
    knowledge_conn.close()

    if flagged_session_ids:
        placeholders = ",".join("?" for _ in flagged_session_ids)
        state_conn.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", tuple(sorted(flagged_session_ids)))

    if flagged_project_ids:
        placeholders = ",".join("?" for _ in flagged_project_ids)
        state_conn.execute(f"DELETE FROM project_descriptors_cache WHERE project_id IN ({placeholders})", tuple(sorted(flagged_project_ids)))
        state_conn.execute(f"DELETE FROM workspace_project_bindings WHERE project_id IN ({placeholders})", tuple(sorted(flagged_project_ids)))

    if flagged_workspace_ids:
        placeholders = ",".join("?" for _ in flagged_workspace_ids)
        state_conn.execute(f"DELETE FROM workspace_project_bindings WHERE workspace_id IN ({placeholders})", tuple(sorted(flagged_workspace_ids)))

    if flagged_session_ids:
        placeholders = ",".join("?" for _ in flagged_session_ids)
        state_conn.execute(f"DELETE FROM scope_resolution_events WHERE session_id IN ({placeholders})", tuple(sorted(flagged_session_ids)))

    state_conn.commit()
    state_conn.close()

    if flagged_projects_json:
        keep = [
            row
            for row in list(projects_payload.get("projects") or [])
            if row not in flagged_projects_json
        ]
        projects_payload["projects"] = keep
        PROJECTS_JSON.write_text(json.dumps(projects_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    project_area = HOME / "memory" / "knowledge" / "areas" / "projects"
    if project_area.exists():
        for child in list(project_area.iterdir()):
            if not child.is_dir():
                continue
            if _matches_any(f"project:{child.name}", OBSOLETE_SCOPE_PATTERNS):
                shutil.rmtree(child, ignore_errors=True)

    chroma_root = HOME / "memory" / ".index" / "chroma_db"
    for orphan in orphan_segment_dirs:
        shutil.rmtree(chroma_root / orphan, ignore_errors=True)

    removed_legacy_files = []
    for path in LEGACY_FILES:
        if path.exists() and path.is_file():
            if path.stat().st_size == 0:
                path.unlink()
                removed_legacy_files.append(str(path))

    repair_result = run_memory_repair()

    chroma_conn = sqlite3.connect(str(CHROMA_SQLITE))
    chroma_conn.row_factory = sqlite3.Row
    active_vector_segments_post = {
        row["id"]
        for row in _fetch_all(chroma_conn, "SELECT id FROM segments WHERE scope = 'VECTOR'")
    }
    chroma_conn.close()
    post_repair_orphan_dirs = [
        path.name
        for path in chroma_root.iterdir()
        if path.is_dir() and path.name not in active_vector_segments_post
    ]
    for orphan in post_repair_orphan_dirs:
        shutil.rmtree(chroma_root / orphan, ignore_errors=True)

    report = {
        "status": "ok",
        "backup_root": str(backup_root),
        "deleted_sessions": sorted(flagged_session_ids),
        "deleted_project_ids": sorted(flagged_project_ids),
        "deleted_fact_ids": flagged_fact_ids,
        "removed_orphan_segment_dirs": orphan_segment_dirs,
        "removed_post_repair_orphan_segment_dirs": post_repair_orphan_dirs,
        "removed_legacy_files": removed_legacy_files,
        "repair_result": repair_result,
    }
    print(report)
    return report


if __name__ == "__main__":
    run()
