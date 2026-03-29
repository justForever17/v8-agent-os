from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NEW_HOME = Path.home() / ".v8-agent-os"
STATE_DB_PATH = NEW_HOME / "state.db"
REPORT_PATH = NEW_HOME / "logs" / "state_db_identity_cleanup_report.json"

STRUCTURED_STRING_REPLACEMENTS: list[tuple[str, str]] = [
    (str(Path.home() / ".v8chat"), str(NEW_HOME)),
    (".v8chat", ".v8-agent-os"),
    ("V8CHAT.md", "V8_AGENT_OS.md"),
    ("x-v8chat-", "x-v8-agent-os-"),
    ("project:v8chat", "project:v8-agent-os"),
    ("v8chat-web.session-token", "v8-agent-os-web.session-token"),
    ("v8chat-admin.session-token", "v8-agent-os-admin.session-token"),
    ("v8chat_admin_connection", "v8-agent-os_admin_connection"),
    ("v8chat_active_admin_connection_id", "v8-agent-os_active_admin_connection_id"),
    ("v8chat_memory", "v8_agent_os_memory"),
    ("system:v8chat", "system:v8-agent-os"),
    ("V8CHAT_", "V8_AGENT_OS_"),
]

STRUCTURED_KEYS = {
    "scope",
    "resolved_scope",
    "scope_hint",
    "project_id",
    "projectid",
    "projectscope",
    "projectslug",
    "workspace_path",
    "workspacepath",
    "workspaceid",
    "path",
    "filepath",
    "previewurl",
    "url",
    "configpath",
    "promptpath",
    "homedir",
    "rootdir",
    "toolroot",
    "source",
    "sourcepath",
    "projectscope",
    "collection",
    "collectionname",
    "headers",
    "cookie",
    "cookiename",
    "baseurl",
    "enginebaseurl",
    "enginewsbaseurl",
    "adminbaseurl",
    "adminapibaseurl",
    "desktoplivebridgebaseurl",
    "internalsecretheader",
    "protectedprocesspatterns",
    "group_wake_words",
    "wake_words",
    "identitytags",
    "systemname",
    "systemslug",
    "author",
}

SPECIAL_EXACT_REPLACEMENTS: dict[str, dict[str, str]] = {
    "protectedprocesspatterns": {"v8chat": "v8-agent-os"},
    "group_wake_words": {"v8chat": "v8-agent-os"},
    "wake_words": {"v8chat": "v8-agent-os"},
    "identitytags": {"system:v8chat": "system:v8-agent-os"},
    "tags": {"v8chat": "v8-agent-os"},
}

COLUMN_TARGETS: dict[str, list[str]] = {
    "run_records": ["metadata"],
    "runtime_events": ["source_json", "payload_json"],
    "sessions": ["metadata"],
    "runtime_snapshots": ["snapshot_json"],
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def apply_structured_string_replacements(text: str) -> str:
    updated = text
    for old, new in STRUCTURED_STRING_REPLACEMENTS:
        updated = updated.replace(old, new)
    return updated


def normalize_key(key: str | None) -> str:
    return str(key or "").strip().replace("-", "_").lower()


def rewrite_identity_value(value: Any, *, key_hint: str | None = None) -> Any:
    normalized_key = normalize_key(key_hint)
    if isinstance(value, dict):
        rewritten: dict[str, Any] = {}
        for key, nested in value.items():
            raw_key = str(key)
            next_key = apply_structured_string_replacements(raw_key) if normalized_key == "headers" else raw_key
            rewritten[next_key] = rewrite_identity_value(nested, key_hint=next_key)
        return rewritten
    if isinstance(value, list):
        return [rewrite_identity_value(item, key_hint=normalized_key or key_hint) for item in value]
    if isinstance(value, str):
        special_map = SPECIAL_EXACT_REPLACEMENTS.get(normalized_key)
        if special_map and value in special_map:
            return special_map[value]
        if normalized_key in STRUCTURED_KEYS:
            return apply_structured_string_replacements(value)
        return value
    return value


def rewrite_json_blob(blob_text: str) -> tuple[str, bool]:
    try:
        payload = json.loads(blob_text)
    except Exception:
        return blob_text, False
    rewritten = rewrite_identity_value(payload)
    serialized = json.dumps(rewritten, ensure_ascii=False)
    return serialized, serialized != blob_text


def count_structured_residue(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    token = "%v8chat%"
    counts: dict[str, dict[str, int]] = {}
    for table, columns in COLUMN_TARGETS.items():
        table_counts: dict[str, int] = {}
        existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column in columns:
            if column not in existing_columns:
                continue
            value = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE ?",
                (token,),
            ).fetchone()
            table_counts[column] = int(value[0] if value else 0)
        counts[table] = table_counts
    return counts


def clean_state_db(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        raise FileNotFoundError(f"state.db 不存在：{db_path}")

    backup_path = db_path.with_name(f"state_identity_cleanup_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(db_path, backup_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    report: dict[str, Any] = {
        "timestamp": utc_now_iso(),
        "dbPath": str(db_path),
        "backupPath": str(backup_path),
        "beforeCounts": count_structured_residue(conn),
        "updatedRows": {},
        "updatedCells": 0,
        "notes": [
            "本轮只清系统身份类结构化残留，不清历史对话正文、测试 prompt 正文、workspace 文档正文或物理仓库路径。",
            "runtime_snapshots.snapshot_json 仅在结构化字段命中时改写，消息 content / parts.text 不做替换。",
            "legacyNames=[\"v8chat\"] 保留，不视为脏数据。",
        ],
    }

    for table, columns in COLUMN_TARGETS.items():
        existing_columns = {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        active_columns = [column for column in columns if column in existing_columns]
        if not active_columns:
            continue
        rows = cur.execute(f"SELECT rowid, {', '.join(active_columns)} FROM {table}").fetchall()
        table_row_updates = 0
        for row in rows:
            rowid = row[0]
            values = row[1:]
            updates: dict[str, str] = {}
            for column, value in zip(active_columns, values):
                if value is None:
                    continue
                rewritten, changed = rewrite_json_blob(str(value))
                if changed:
                    updates[column] = rewritten
            if updates:
                assignments = ", ".join(f"{column}=?" for column in updates)
                params = [*updates.values(), rowid]
                cur.execute(f"UPDATE {table} SET {assignments} WHERE rowid=?", params)
                table_row_updates += 1
                report["updatedCells"] += len(updates)
        report["updatedRows"][table] = table_row_updates

    conn.commit()
    report["afterCounts"] = count_structured_residue(conn)
    conn.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="对 ~/.v8-agent-os/state.db 做第二轮系统身份结构化清洗。")
    parser.add_argument("--db", default=str(STATE_DB_PATH))
    parser.add_argument("--report-path", default=str(REPORT_PATH))
    args = parser.parse_args()

    report = clean_state_db(Path(args.db).expanduser())
    report_path = Path(args.report_path).expanduser()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
