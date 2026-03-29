from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OLD_HOME = Path.home() / ".v8chat"
NEW_HOME = Path.home() / ".v8-agent-os"
CANONICAL_PROMPT_FILE = "V8_AGENT_OS.md"
LEGACY_PROMPT_FILE = "V8CHAT.md"
CANONICAL_IDENTITY = {
    "systemName": "V8 Agent OS",
    "systemSlug": "v8-agent-os",
    "author": "justForever17",
    "legacyNames": ["v8chat"],
    "identityTags": [
        "system:v8-agent-os",
        "author:justForever17",
        "identity:canonical",
    ],
}
STRUCTURED_STRING_REPLACEMENTS: list[tuple[str, str]] = [
    (str(OLD_HOME), str(NEW_HOME)),
    (".v8chat", ".v8-agent-os"),
    ("V8CHAT.md", "V8_AGENT_OS.md"),
    ("x-v8chat-", "x-v8-agent-os-"),
    ("project:v8chat", "project:v8-agent-os"),
    ("v8chat-web.session-token", "v8-agent-os-web.session-token"),
    ("v8chat-admin.session-token", "v8-agent-os-admin.session-token"),
    ("v8chat_admin_connection", "v8-agent-os_admin_connection"),
    ("v8chat_active_admin_connection_id", "v8-agent-os_active_admin_connection_id"),
    ("v8chat_memory", "v8_agent_os_memory"),
    ("V8CHAT_", "V8_AGENT_OS_"),
]
TEXTUAL_BRAND_REPLACEMENTS: list[tuple[str, str]] = [
    ("v8chat AI Application Architect & Assistant", "V8 Agent OS AI Application Architect & Assistant"),
    ("You are the v8chat", "You are the V8 Agent OS"),
    ("v8chat", "V8 Agent OS"),
]
ROOT_STRUCTURED_JSON_FILES = [
    "config.json",
    "plugin.json",
    "computer_use.json",
    "extensions_runtime_cache.json",
    "skills_inventory_cache.json",
]
STRUCTURED_JSON_DIRECTORIES = [
    Path("runtime") / "channels" / "instances",
]
SAFE_TEXT_DIRECTORIES = [
    "agents",
    "commands",
    "core",
]
SQLITE_TABLE_COLUMN_RULES: dict[str, list[str]] = {
    "run_records": ["metadata"],
    "runtime_events": ["source_json", "payload_json"],
    "runtime_snapshots": ["snapshot_json"],
    "workflow_ledgers": ["metadata_json"],
    "session_scope_bindings": ["workspace_path", "project_id", "scope_hint", "resolved_scope"],
    "workspace_project_bindings": ["workspace_path", "project_id"],
    "sessions": ["metadata"],
    "project_descriptors_cache": ["project_id", "metadata_json"],
}
SQLITE_DATABASE_FILES = {
    "state.db",
    "checkpoints.db",
    "system_audit_log.db",
    "v8chat.db",
}
JSON_KEY_ALLOWLIST = {
    "scope",
    "resolved_scope",
    "scope_hint",
    "project_id",
    "projectId",
    "workspace_path",
    "workspacePath",
    "path",
    "filePath",
    "previewUrl",
    "url",
    "configPath",
    "promptPath",
    "homeDir",
    "rootDir",
    "toolRoot",
    "source",
    "sourcePath",
    "projectScope",
    "projectSlug",
    "collection",
    "headers",
    "cookie",
    "cookieName",
    "baseUrl",
    "engineBaseUrl",
    "engineWsBaseUrl",
    "adminBaseUrl",
    "adminApiBaseUrl",
    "desktopLiveBridgeBaseUrl",
    "internalSecretHeader",
    "protectedProcessPatterns",
    "group_wake_words",
    "wake_words",
    "identityTags",
    "systemName",
    "systemSlug",
    "author",
    "legacyNames",
}
SPECIAL_KEY_STRING_REPLACEMENTS: dict[str, dict[str, str]] = {
    "protectedProcessPatterns": {
        "v8chat": "v8-agent-os",
    },
    "group_wake_words": {
        "v8chat": "v8-agent-os",
    },
    "wake_words": {
        "v8chat": "v8-agent-os",
    },
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def apply_structured_string_replacements(text: str) -> str:
    updated = text
    for old, new in STRUCTURED_STRING_REPLACEMENTS:
        updated = updated.replace(old, new)
    return updated


def apply_brand_text_replacements(text: str) -> str:
    updated = apply_structured_string_replacements(text)
    for old, new in TEXTUAL_BRAND_REPLACEMENTS:
        updated = updated.replace(old, new)
    return updated


def rewrite_json_value(value: Any, *, key_hint: str | None = None) -> Any:
    if isinstance(value, dict):
        rewritten: dict[str, Any] = {}
        for key, item in value.items():
            rewritten[str(key)] = rewrite_json_value(item, key_hint=str(key))
        return rewritten
    if isinstance(value, list):
        return [rewrite_json_value(item, key_hint=key_hint) for item in value]
    if isinstance(value, str):
        lowered_key = str(key_hint or "").strip()
        special_map = SPECIAL_KEY_STRING_REPLACEMENTS.get(lowered_key)
        if special_map and value in special_map:
            return special_map[value]
        if lowered_key in JSON_KEY_ALLOWLIST or any(token in value for token, _ in STRUCTURED_STRING_REPLACEMENTS):
            return apply_structured_string_replacements(value)
        if lowered_key in {"fact", "summary", "description", "title", "label"}:
            return apply_brand_text_replacements(value)
        return value
    return value


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_identity_in_config(config: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(config)
    system_base = payload.setdefault("systemBase", {})
    if not isinstance(system_base, dict):
        system_base = {}
        payload["systemBase"] = system_base
    system_base["identity"] = deepcopy(CANONICAL_IDENTITY)
    return payload


def migrate_root_json_files(new_home: Path, report: dict[str, Any]) -> None:
    rewritten: list[str] = []
    for relative in ROOT_STRUCTURED_JSON_FILES:
        path = new_home / relative
        if not path.exists():
            continue
        payload = load_json(path)
        payload = rewrite_json_value(payload)
        if relative == "config.json" and isinstance(payload, dict):
            payload = ensure_identity_in_config(payload)
        dump_json(path, payload)
        rewritten.append(relative)
    report["rootJsonFiles"] = rewritten


def migrate_structured_json_directories(new_home: Path, report: dict[str, Any]) -> None:
    rewritten: list[str] = []
    for relative_dir in STRUCTURED_JSON_DIRECTORIES:
        directory = new_home / relative_dir
        if not directory.exists():
            continue
        for path in directory.rglob("*.json"):
            payload = load_json(path)
            payload = rewrite_json_value(payload)
            dump_json(path, payload)
            rewritten.append(str(path.relative_to(new_home)))
    report["structuredJsonDirectories"] = rewritten


def migrate_prompt_and_safe_text_files(new_home: Path, report: dict[str, Any]) -> None:
    legacy_prompt = new_home / LEGACY_PROMPT_FILE
    canonical_prompt = new_home / CANONICAL_PROMPT_FILE
    if legacy_prompt.exists():
        content = legacy_prompt.read_text(encoding="utf-8-sig")
        canonical_prompt.write_text(apply_brand_text_replacements(content), encoding="utf-8")
        legacy_prompt.unlink()
    elif canonical_prompt.exists():
        canonical_prompt.write_text(apply_brand_text_replacements(canonical_prompt.read_text(encoding="utf-8-sig")), encoding="utf-8")

    rewritten: list[str] = [CANONICAL_PROMPT_FILE] if canonical_prompt.exists() else []
    for relative_dir in SAFE_TEXT_DIRECTORIES:
        directory = new_home / relative_dir
        if not directory.exists():
            continue
        for file_path in directory.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in {".md", ".json", ".txt"}:
                continue
            original = file_path.read_text(encoding="utf-8-sig")
            updated = apply_brand_text_replacements(original)
            if updated != original:
                file_path.write_text(updated, encoding="utf-8")
                rewritten.append(str(file_path.relative_to(new_home)))
    report["safeTextFiles"] = rewritten


def migrate_memory_md(new_home: Path, report: dict[str, Any]) -> None:
    memory_md = new_home / "memory" / "MEMORY.md"
    if not memory_md.exists():
        return
    content = apply_brand_text_replacements(memory_md.read_text(encoding="utf-8-sig"))
    lines = content.splitlines()
    keys = {
        "system_name:": f"system_name: {CANONICAL_IDENTITY['systemName']}",
        "system_slug:": f"system_slug: {CANONICAL_IDENTITY['systemSlug']}",
        "system_author:": f"system_author: {CANONICAL_IDENTITY['author']}",
    }
    seen = set()
    rewritten_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        matched = False
        for prefix, replacement in keys.items():
            if stripped.startswith(prefix):
                rewritten_lines.append(replacement)
                seen.add(prefix)
                matched = True
                break
        if not matched:
            rewritten_lines.append(line)
    for prefix, replacement in keys.items():
        if prefix not in seen:
            rewritten_lines.append(replacement)
    memory_md.write_text("\n".join(rewritten_lines).rstrip() + "\n", encoding="utf-8")
    report["memoryMarkdown"] = str(memory_md)


def is_identity_knowledge_item(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    if str(item.get("category") or "").strip() == "SystemIdentity":
        return True
    fact_text = str(item.get("fact") or "").strip().lower()
    if "v8 agent os" in fact_text and "justforever17" in fact_text:
        return True
    tags = item.get("tags")
    if isinstance(tags, list):
        normalized_tags = {str(tag).strip() for tag in tags}
        if set(CANONICAL_IDENTITY["identityTags"]).issubset(normalized_tags):
            return True
    return False


def migrate_knowledge_items(new_home: Path, report: dict[str, Any]) -> None:
    knowledge_root = new_home / "memory" / "knowledge"
    rewritten: list[str] = []
    if not knowledge_root.exists():
        report["knowledgeFiles"] = rewritten
        return
    for items_path in knowledge_root.rglob("items.json"):
        try:
            payload = load_json(items_path)
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        migrated: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            rewritten_item = rewrite_json_value(item)
            if is_identity_knowledge_item(rewritten_item):
                continue
            fact_text = str(rewritten_item.get("fact") or "").strip()
            if "v8chat" in fact_text.lower():
                rewritten_item["fact"] = apply_brand_text_replacements(fact_text)
            scope = str(rewritten_item.get("scope") or "").strip()
            if scope:
                rewritten_item["scope"] = apply_structured_string_replacements(scope)
            tags = rewritten_item.get("tags")
            if isinstance(tags, list):
                rewritten_item["tags"] = [apply_structured_string_replacements(str(tag)) for tag in tags]
            migrated.append(rewritten_item)
        dump_json(items_path, migrated)
        rewritten.append(str(items_path.relative_to(new_home)))
    report["knowledgeFiles"] = rewritten


def rewrite_json_blob(blob_text: str) -> str:
    try:
        payload = json.loads(blob_text)
    except Exception:
        return apply_structured_string_replacements(blob_text)
    return json.dumps(rewrite_json_value(payload), ensure_ascii=False)


def migrate_state_db(new_home: Path, report: dict[str, Any]) -> None:
    db_path = new_home / "state.db"
    if not db_path.exists():
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    updated_cells = 0
    for table, columns in SQLITE_TABLE_COLUMN_RULES.items():
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if not cur.fetchone():
            continue
        cur.execute(f"PRAGMA table_info({table})")
        existing_columns = {row[1] for row in cur.fetchall()}
        active_columns = [column for column in columns if column in existing_columns]
        if not active_columns:
            continue
        pk_columns = [row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall() if row[5] > 0]
        rowid_available = "rowid"
        select_columns = ", ".join([rowid_available, *active_columns])
        rows = cur.execute(f"SELECT {select_columns} FROM {table}").fetchall()
        for row in rows:
            rowid = row[0]
            values = row[1:]
            updates: dict[str, Any] = {}
            for column, value in zip(active_columns, values):
                if value is None:
                    continue
                new_value = value
                if column == "project_id":
                    if str(value).strip() == "v8chat":
                        new_value = "v8-agent-os"
                elif column in {"workspace_path", "scope_hint", "resolved_scope"}:
                    new_value = apply_structured_string_replacements(str(value))
                elif column.endswith("_json") or column in {"metadata", "payload"}:
                    new_value = rewrite_json_blob(str(value))
                else:
                    new_value = apply_structured_string_replacements(str(value))
                if new_value != value:
                    updates[column] = new_value
            if updates:
                assignments = ", ".join(f"{column}=?" for column in updates.keys())
                params = [*updates.values(), rowid]
                cur.execute(f"UPDATE {table} SET {assignments} WHERE rowid=?", params)
                updated_cells += len(updates)
    conn.commit()
    conn.close()
    report["stateDbUpdatedCells"] = updated_cells


def migrate_chroma_collection(new_home: Path, report: dict[str, Any]) -> None:
    chroma_root = new_home / "memory" / ".index" / "chroma_db"
    if not chroma_root.exists():
        report["chroma"] = {"status": "missing"}
        return
    try:
        import chromadb  # type: ignore
    except Exception as exc:  # pragma: no cover
        report["chroma"] = {"status": "skipped", "reason": f"chromadb_unavailable: {exc}"}
        return
    try:
        client = chromadb.PersistentClient(path=str(chroma_root))
        old_collection_name = "v8chat_memory"
        new_collection_name = "v8_agent_os_memory"
        try:
            old_collection = client.get_collection(old_collection_name)
        except Exception:
            report["chroma"] = {"status": "skipped", "reason": "old_collection_missing"}
            return
        try:
            client.delete_collection(new_collection_name)
        except Exception:
            pass
        new_collection = client.create_collection(new_collection_name)
        offset = 0
        migrated_count = 0
        while True:
            batch = old_collection.get(
                include=["metadatas", "documents", "embeddings"],
                offset=offset,
                limit=200,
            )
            ids = batch.get("ids") or []
            if not ids:
                break
            metadatas = [rewrite_json_value(metadata or {}) for metadata in (batch.get("metadatas") or [])]
            documents = [apply_brand_text_replacements(str(document or "")) for document in (batch.get("documents") or [])]
            embeddings = batch.get("embeddings")
            new_collection.add(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
            migrated_count += len(ids)
            offset += len(ids)
        report["chroma"] = {"status": "migrated", "count": migrated_count, "collection": new_collection_name}
    except Exception as exc:  # pragma: no cover
        report["chroma"] = {"status": "error", "reason": str(exc)}


def migrate_admin_storage(new_home: Path, report: dict[str, Any]) -> None:
    users_json = new_home / "users.json"
    if users_json.exists():
        payload = load_json(users_json)
        dump_json(users_json, rewrite_json_value(payload))
        report["usersJson"] = str(users_json)


def copy_runtime_home(old_home: Path, new_home: Path) -> None:
    new_home.mkdir(parents=True, exist_ok=True)
    for source in old_home.rglob("*"):
        relative = source.relative_to(old_home)
        target = new_home / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if source.name in SQLITE_DATABASE_FILES or source.name.endswith(("-wal", "-shm")):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def backup_sqlite_file(source_db: Path, target_db: Path) -> None:
    if not source_db.exists():
        return
    if source_db.stat().st_size == 0:
        target_db.write_bytes(b"")
        return
    target_db.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_db))
    target = sqlite3.connect(str(target_db))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def backup_sqlite_databases(old_home: Path, new_home: Path) -> list[str]:
    copied: list[str] = []
    for filename in SQLITE_DATABASE_FILES:
        source_db = old_home / filename
        target_db = new_home / filename
        if source_db.exists():
            backup_sqlite_file(source_db, target_db)
            copied.append(filename)
    return copied


def build_report(apply_changes: bool, old_home: Path, new_home: Path) -> dict[str, Any]:
    return {
        "mode": "apply" if apply_changes else "dry-run",
        "oldHome": str(old_home),
        "newHome": str(new_home),
        "timestamp": utc_now_iso(),
        "identity": deepcopy(CANONICAL_IDENTITY),
    }


def execute_migration(*, old_home: Path, new_home: Path, apply_changes: bool) -> dict[str, Any]:
    report = build_report(apply_changes, old_home, new_home)
    if not old_home.exists():
        raise FileNotFoundError(f"旧运行时根不存在：{old_home}")
    if not apply_changes:
        report["status"] = "dry-run-ready"
        report["oldHomeExists"] = True
        report["newHomeExists"] = new_home.exists()
        report["legacyPromptExists"] = (old_home / LEGACY_PROMPT_FILE).exists()
        report["stateDbExists"] = (old_home / "state.db").exists()
        return report

    backup_root = new_home.parent / f".v8-agent-os-backup-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if new_home.exists():
        shutil.copytree(new_home, backup_root)
        shutil.rmtree(new_home)
        report["existingNewHomeBackup"] = str(backup_root)

    copy_runtime_home(old_home, new_home)
    report["copied"] = True
    report["sqliteDatabases"] = backup_sqlite_databases(old_home, new_home)

    migrate_root_json_files(new_home, report)
    migrate_structured_json_directories(new_home, report)
    migrate_prompt_and_safe_text_files(new_home, report)
    migrate_memory_md(new_home, report)
    migrate_knowledge_items(new_home, report)
    migrate_admin_storage(new_home, report)
    migrate_state_db(new_home, report)
    migrate_chroma_collection(new_home, report)

    report_path = new_home / "logs" / "migration_v8_agent_os_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    dump_json(report_path, report)
    report["reportPath"] = str(report_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="将 ~/.v8chat 离线迁移到 ~/.v8-agent-os。")
    parser.add_argument("--old-home", default=str(OLD_HOME))
    parser.add_argument("--new-home", default=str(NEW_HOME))
    parser.add_argument("--apply", action="store_true", help="执行实际迁移。默认仅 dry-run。")
    args = parser.parse_args()

    report = execute_migration(
        old_home=Path(args.old_home).expanduser(),
        new_home=Path(args.new_home).expanduser(),
        apply_changes=bool(args.apply),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
