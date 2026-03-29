from __future__ import annotations

import importlib
from typing import Any

from core.engine_runtime_health import inspect_engine_runtime


def inspect_memory_backend() -> dict[str, Any]:
    engine_runtime = inspect_engine_runtime()
    interpreter_path = str(engine_runtime.get("interpreterPath") or "")
    expected_path = str(engine_runtime.get("expectedInterpreterPath") or "") or None
    interpreter_drift = bool(engine_runtime.get("interpreterDrift"))

    chroma_available = False
    chroma_version: str | None = None
    chroma_error: str | None = None
    try:
        chromadb = importlib.import_module("chromadb")
        chroma_available = True
        chroma_version = str(getattr(chromadb, "__version__", "") or "")
    except Exception as exc:  # pragma: no cover - degraded path
        chroma_error = str(exc).strip() or exc.__class__.__name__

    vector_ready = False
    collection_ready = False
    embedding_ready = False
    reranker_ready = False
    vector_store_path: str | None = None
    vector_error: str | None = None
    try:
        from core.vector_store import get_vector_store

        vector_store = get_vector_store()
        vector_store_path = str(getattr(vector_store, "db_dir", "") or "")
        collection_ready = getattr(vector_store, "collection", None) is not None
        embedding_ready = getattr(vector_store, "embedding_model", None) is not None
        reranker_ready = getattr(vector_store, "reranker_model", None) is not None
        vector_ready = bool(chroma_available and collection_ready)
    except Exception as exc:  # pragma: no cover - degraded path
        vector_error = str(exc).strip() or exc.__class__.__name__

    warnings: list[str] = []
    if interpreter_drift and expected_path:
        warnings.append(f"解释器漂移：当前 {interpreter_path}，期望 {expected_path}")
    if not chroma_available and chroma_error:
        warnings.append(f"chromadb 不可导入：{chroma_error}")
    if chroma_available and not vector_ready:
        if vector_error:
            warnings.append(f"向量后端未就绪：{vector_error}")
        elif not collection_ready:
            warnings.append("向量后端未就绪：collection 尚未初始化，当前可能退化到 SQLite FTS5。")

    return {
        "design": "sqlite_fts5_plus_chromadb",
        "mode": "sqlite_fts5_plus_chromadb" if vector_ready else "fts5_only_degraded",
        "interpreterPath": interpreter_path,
        "expectedInterpreterPath": expected_path,
        "interpreterDrift": interpreter_drift,
        "chromadb": {
            "available": chroma_available,
            "version": chroma_version,
            "error": chroma_error,
        },
        "vectorBackend": {
            "ready": vector_ready,
            "collectionReady": collection_ready,
            "embeddingReady": embedding_ready,
            "rerankerReady": reranker_ready,
            "path": vector_store_path,
            "error": vector_error,
        },
        "fts5OnlyDegraded": not vector_ready,
        "warnings": warnings,
    }
