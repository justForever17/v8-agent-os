from __future__ import annotations

from datetime import datetime

from core.knowledge_db import knowledge_db
from core.memory_store import memory_store
from core.vector_store import get_vector_store


_ALLOWED_APP_SCOPES = {"app:chat", "app:coding", "app:writing"}
_SPECIFIC_SCOPE_PREFIXES = ("project:", "workspace:", "workflow:", "channel:")


def _is_valid_scope(scope: str) -> bool:
    normalized = str(scope or "").strip()
    if normalized == "global":
        return True
    if normalized in _ALLOWED_APP_SCOPES:
        return True
    return normalized.startswith(_SPECIFIC_SCOPE_PREFIXES)


def run(payload: dict | None = None):
    payload = payload or {}
    now = datetime.now().isoformat()

    prefs = memory_store._load_raw_preferences()
    valid_prefs = {scope: values for scope, values in prefs.items() if _is_valid_scope(scope)}
    if "global" not in valid_prefs:
        valid_prefs["global"] = {}
    memory_store._save_preferences(valid_prefs)

    invalid_fact_ids: list[str] = []
    active_rows: list[dict] = []
    with knowledge_db._conn() as conn:
        rows = conn.execute(
            """
            SELECT rowid, id, fact, category, scope
            FROM knowledge
            WHERE status = 'active'
            ORDER BY updated_at DESC
            """
        ).fetchall()
        for row in rows:
            item = dict(row)
            if not _is_valid_scope(item.get("scope")):
                invalid_fact_ids.append(item["id"])
                conn.execute(
                    "UPDATE knowledge SET status = 'deleted', updated_at = ? WHERE id = ?",
                    (now, item["id"]),
                )
                conn.execute("DELETE FROM knowledge_fts WHERE rowid = ?", (item["rowid"],))
                continue
            active_rows.append(item)

    vector_store = get_vector_store()
    rebuilt_vectors = 0
    if vector_store.client:
        try:
            vector_store.client.delete_collection(vector_store.collection_name)
        except Exception:
            pass
        if vector_store.embedding_model:
            vector_store.collection = vector_store.client.get_or_create_collection(
                name=vector_store.collection_name,
            )
            documents = [
                {
                    "id": item["id"],
                    "text": item["fact"],
                    "metadata": {
                        "category": item.get("category", "general"),
                        "scope": item.get("scope", "global"),
                    },
                }
                for item in active_rows
            ]
            if documents:
                vector_store.add_documents(documents)
                rebuilt_vectors = len(documents)
        else:
            vector_store.collection = None

    report = {
        "status": "ok",
        "preferences_scopes_kept": sorted(valid_prefs.keys()),
        "invalid_fact_ids_deleted": invalid_fact_ids,
        "active_knowledge_count": len(active_rows),
        "rebuilt_vector_count": rebuilt_vectors,
    }
    print(report)
    return report


if __name__ == "__main__":
    run()
