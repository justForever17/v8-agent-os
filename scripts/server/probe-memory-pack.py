"""Operation oracle for the explicit pack-install smoke, using its isolated state."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
from pathlib import Path
import sys


def verify_engine_dependency_contract(engine):
    from importlib.metadata import version
    from packaging.requirements import Requirement
    for line in (engine / "requirements/base.txt").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker is None or requirement.marker.evaluate():
            installed = version(requirement.name)
            assert requirement.specifier.contains(installed), f"Pack overrides Engine contract: {requirement.name}=={installed} violates {requirement}"


def document_probe():
    import httpx
    from docx import Document
    from core.client_listeners import _local_management_directory
    from core.knowledge_db import knowledge_db
    from core.memory_backend_health import inspect_memory_backend
    assert inspect_memory_backend()["mode"] == "sqlite_fts5"
    document = Document()
    document.add_paragraph("服务器文档验收 DOC_PACK_CONTENT_PROOF")
    content = io.BytesIO()
    document.save(content)
    socket = _local_management_directory(Path(os.environ["V8_AGENT_OS_HOME"])) / "engine.sock"
    with httpx.Client(transport=httpx.HTTPTransport(uds=str(socket)), base_url="http://localhost", timeout=30) as client:
        response = client.post("/v1/memory/upload", files={"files": ("fixture.docx", content.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert response.status_code == 200, response.text
    with knowledge_db._conn() as conn:
        rows = conn.execute("SELECT fact FROM knowledge WHERE source_session = ? AND status = 'active'", ("fixture.docx",)).fetchall()
        assert any("DOC_PACK_CONTENT_PROOF" in row["fact"] for row in rows)
        assert conn.execute("SELECT COUNT(*) FROM knowledge_projection_outbox WHERE status != 'completed'").fetchone()[0] == 0
    assert knowledge_db.fts_search("服务器文档验收", scope="global")
    return {"docxParsedAndPersisted": True, "ftsRecall": True, "outboxCompleted": True, "embeddingInvoked": False}


def vector_probe():
    # Only the external embedding model boundary is synthetic. Chroma,
    # canonical knowledge and projection persistence are the installed product.
    from core.memory_router import MemoryRouter
    from core.knowledge_db import knowledge_db
    from core.knowledge_projection import KnowledgeProjectionService
    from core.vector_store import VectorStore
    class Embeddings:
        def embed_documents(self, texts):
            return [[1.0, float("alive" in text), float("changed" in text), 0.5] for text in texts]
        def embed_query(self, text):
            return self.embed_documents([text])[0]
    MemoryRouter.get_embedding_model = lambda self: Embeddings()
    MemoryRouter.get_reranker_model = lambda self: None
    db = knowledge_db
    memory_root = Path(os.environ["V8_AGENT_OS_HOME"]) / "vector-probe-memory"
    vector = VectorStore(db_dir=memory_root / ".index/chroma_db")
    service = KnowledgeProjectionService(db, memory_root=memory_root)
    service._root_vector_store = vector
    first = db.write_knowledge(fact="alive vector fixture", category="project_rule", scope="project:vector-pack", fact_id="vector-alive")
    replaced = db.write_knowledge(fact="changed vector fixture", category="project_rule", scope="project:vector-pack", relation="replace", target_fact_id=first["factId"], fact_id="vector-changed")
    asyncio.run(service.recover_outbox(rebuild_vectors=True))
    persisted = vector.collection.get(include=["documents"])
    assert first["factId"] not in persisted["ids"], persisted["ids"]
    assert replaced["factId"] in persisted["ids"]
    results = vector.similarity_search_with_rerank("changed", top_k=1)
    assert results[0]["id"] == replaced["factId"]
    db.delete_knowledge(replaced["factId"])
    asyncio.run(service.recover_outbox(rebuild_vectors=True))
    assert replaced["factId"] not in vector.collection.get(include=[])["ids"]
    vector.close()
    reopened = VectorStore(db_dir=memory_root / ".index/chroma_db")
    assert replaced["factId"] not in reopened.collection.get(include=[])["ids"]
    reopened.close()
    return {"chromaPersisted": True, "replacedRevisionExcluded": True, "vectorQuery": True, "tombstoneSurvivesReopen": True, "embeddingBoundary": "synthetic deterministic model; no provider claim"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--pack", choices=("document_ingestion", "vector_memory"), required=True)
    args = parser.parse_args()
    if not os.environ.get("V8_AGENT_OS_HOME", "").startswith("/tmp/v8-server-packs-"):
        parser.error("This probe requires the pack smoke's disposable state directory")
    sys.path.insert(0, str(args.engine.resolve()))
    from core.runtime.startup_profile import get_runtime_registry_state
    get_runtime_registry_state()
    verify_engine_dependency_contract(args.engine)
    result = document_probe() if args.pack == "document_ingestion" else vector_probe()
    result["engineDependencyContract"] = True
    print(json.dumps(result, ensure_ascii=False))
