from __future__ import annotations

import builtins
import asyncio

import pytest

from core.runtime import startup_profile as profile
from core.runtime import feature_packs


SERVER = {"chat", "memory", "extensions", "automation", "network_supervisor", "engineering", "research", "plugin_manager"}


@pytest.fixture
def server(monkeypatch):
    registry = {"installProfile": "desktop", "installedRuntimeFamilies": list(profile.KNOWN_RUNTIME_FAMILIES), "featurePacks": {}}
    monkeypatch.setenv("ENGINE_INSTALL_PROFILE", "server")
    monkeypatch.setattr(profile, "ensure_runtime_registry_installation_state", lambda: None)
    monkeypatch.setattr(profile.storage, "get_runtime_registry_config", lambda: registry)
    monkeypatch.setattr(profile, "apply_feature_pack_python_paths", lambda _registry: [])
    return registry


def test_server_ignores_old_desktop_families_and_ambient_probes(server, monkeypatch):
    monkeypatch.setattr(profile, "_detect_installed_runtime_families", lambda _platform: pytest.fail("server must not probe ambient desktop modules"))
    monkeypatch.setattr(profile, "build_feature_pack_statuses", lambda *args, **kwargs: [])
    state = profile.get_runtime_registry_state()
    assert state["installProfile"] == state["startupProfile"] == "server"
    assert set(state["installedRuntimeFamilies"]) == SERVER
    assert not profile.service_enabled("audio", _state=state)
    assert profile.service_state("audio", _state=state)["reason"] == "not_installed"


def test_only_installed_and_restarted_media_pack_extends_server(server, monkeypatch):
    packs = [
        {"id": "computer_use_desktop", "runtimeFamilies": ["computer_use", "desktop_live"], "status": "installed"},
        {"id": "rpa_automation", "runtimeFamilies": ["rpa"], "status": "installed"},
        {"id": "creative_media", "runtimeFamilies": ["creative_media"], "status": "installed", "restartRequired": True},
    ]
    monkeypatch.setattr(profile, "build_feature_pack_statuses", lambda *args, **kwargs: packs)
    assert set(profile.installed_runtime_families()) == SERVER
    packs[-1]["restartRequired"] = False
    assert set(profile.installed_runtime_families()) == SERVER | {"creative_media"}


def test_server_never_loads_desktop_pack_paths(server, monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_INSTALL_PROFILE", " SERVER ")
    server["featurePacks"] = {"computer_use_desktop": {"status": "installed", "targetDir": str(tmp_path)}}
    monkeypatch.setattr(feature_packs, "_feature_pack_receipt_runtime_compatibility", lambda *args: pytest.fail("excluded pack must not load"))
    assert feature_packs.apply_feature_pack_python_paths(server) == []


def test_server_tools_preserve_browser_and_remove_desktop_and_media(server, monkeypatch):
    monkeypatch.setattr(profile, "build_feature_pack_statuses", lambda *args, **kwargs: [])
    from core.runtime_tool_access import runtime_tool_available, runtime_tool_group_available
    for tool in ("computer_use_list_apps", "computer_use_desktop_capabilities", "computer_use_click", "rpa_run_draft", "creative_media_jobs", "vision_media_analyzer"):
        assert not runtime_tool_available(tool), tool
    assert runtime_tool_group_available("browser.control")
    assert runtime_tool_available("web_broker")
    assert runtime_tool_available("delegation_broker")


def test_unselected_vector_health_never_imports_chroma(server, monkeypatch):
    from core import memory_backend_health
    monkeypatch.setattr(profile, "build_feature_pack_statuses", lambda *args, **kwargs: [])
    monkeypatch.setattr(memory_backend_health.importlib, "import_module", lambda name: pytest.fail(f"unexpected optional import: {name}"))
    health = memory_backend_health.inspect_memory_backend()
    assert health["mode"] == "sqlite_fts5"
    assert health["fts5OnlyDegraded"] is False


def test_selected_broken_vector_remains_degraded(server, monkeypatch):
    from core import memory_backend_health
    server["featurePacks"] = {"vector_memory": {"status": "failed"}}
    def unavailable(_name):
        raise ImportError("synthetic missing vector dependency")
    monkeypatch.setattr(memory_backend_health.importlib, "import_module", unavailable)
    health = memory_backend_health.inspect_memory_backend()
    assert health["mode"] == "fts5_only_degraded"
    assert health["warnings"]


@pytest.mark.parametrize("embedding_ready", [False, True])
def test_vector_collection_without_embedding_cannot_report_ready(server, monkeypatch, embedding_ready):
    from types import SimpleNamespace
    from core import memory_backend_health, vector_store
    monkeypatch.setattr(profile, "build_feature_pack_statuses", lambda *args, **kwargs: [{"id": "vector_memory", "status": "installed", "restartRequired": False}])
    monkeypatch.setattr(memory_backend_health.importlib, "import_module", lambda name: SimpleNamespace(__version__="fixture"))
    monkeypatch.setattr(vector_store, "get_vector_store", lambda: SimpleNamespace(collection=object(), embedding_model=object() if embedding_ready else None, reranker_model=None))
    health = memory_backend_health.inspect_memory_backend()
    assert health["vectorBackend"]["ready"] is embedding_ready
    assert health["fts5OnlyDegraded"] is (not embedding_ready)
    if not embedding_ready:
        assert any("embedding" in warning for warning in health["warnings"])


def test_image_dependency_failure_is_explicit(server, monkeypatch):
    from core.local_visual_support import build_inline_image_data_from_bytes
    original_import = builtins.__import__
    def missing_pillow(name, *args, **kwargs):
        if name == "PIL":
            raise ImportError("synthetic unavailable Pillow")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing_pillow)
    with pytest.raises(ValueError, match="creative_media"):
        build_inline_image_data_from_bytes(b"image")


def test_bulk_document_ingestion_reports_missing_pack_before_reading_files(server, monkeypatch):
    from fastapi import HTTPException
    from api.ops_routes import upload_memory_docs
    monkeypatch.setattr(profile, "build_feature_pack_statuses", lambda *args, **kwargs: [])
    with pytest.raises(HTTPException) as error:
        asyncio.run(upload_memory_docs(files=[]))
    assert error.value.status_code == 503
    assert error.value.detail["featurePack"] == "document_ingestion"


def test_media_runtime_can_import_tts_manager_without_installing_voice(server, monkeypatch):
    original_import = builtins.__import__
    def missing_voice(name, *args, **kwargs):
        if name == "edge_tts":
            raise ImportError("voice intentionally absent")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing_voice)
    from core.audio.tts_provider import EdgeTTSProvider, TTSManager, TTSProviderError
    assert TTSManager
    async def attempt():
        with pytest.raises(TTSProviderError, match="cloud_voice") as error:
            await anext(EdgeTTSProvider().synthesize_stream("fixture"))
        assert error.value.status_code == 503
    asyncio.run(attempt())


def test_unselected_vector_projects_fts_and_json_without_retry_backlog(server, monkeypatch, tmp_path):
    from core.knowledge_db import KnowledgeDB
    from core.knowledge_projection import KnowledgeProjectionService
    db = KnowledgeDB(tmp_path / "knowledge.db")
    service = KnowledgeProjectionService(db, memory_root=tmp_path / "memory")
    monkeypatch.setattr(profile, "build_feature_pack_statuses", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "_get_vector_store", lambda: pytest.fail("unselected vector backend accessed"))
    fact = db.write_knowledge(fact="服务器只安装基础依赖", category="project_rule", scope="project:server")
    result = service.process_outbox()
    assert result["completed"] == 1
    assert result["retry"] == 0
    assert db.fts_search("基础", scope="project:server")[0]["id"] == fact["factId"]
    assert service.health(deep=True)["vector"]["state"] == "not_selected"


def test_installing_vector_does_not_write_before_restart(server, monkeypatch, tmp_path):
    from core.knowledge_db import KnowledgeDB
    from core.knowledge_projection import KnowledgeProjectionService
    db = KnowledgeDB(tmp_path / "knowledge.db")
    service = KnowledgeProjectionService(db, memory_root=tmp_path / "memory")
    server["featurePacks"] = {"vector_memory": {"status": "installing"}}
    monkeypatch.setattr(profile, "build_feature_pack_statuses", lambda *args, **kwargs: [{"id": "vector_memory", "status": "installing", "restartRequired": True}])
    monkeypatch.setattr(service, "_get_vector_store", lambda: pytest.fail("pack activated before restart"))
    db.write_knowledge(fact="安装中仍可写文本记忆", category="project_rule", scope="project:server")
    assert service.process_outbox()["completed"] == 1


def test_vector_rebuild_drains_more_than_one_batch_without_duplicate_enqueues(server, monkeypatch, tmp_path):
    from core.knowledge_db import KnowledgeDB
    from core.knowledge_projection import KnowledgeProjectionService
    db = KnowledgeDB(tmp_path / "knowledge.db")
    service = KnowledgeProjectionService(db, memory_root=tmp_path / "memory")
    monkeypatch.setattr(profile, "build_feature_pack_statuses", lambda *args, **kwargs: [])
    for index in range(501):
        db.write_knowledge(fact=f"server memory {index}", category="project_rule", scope="project:server", fact_id=f"fact-{index}")
    # Original FTS-only writes completed before the user chose vector support.
    assert asyncio.run(service.recover_outbox())["completed"] == 501
    documents = {}
    class Collection:
        def get(self, **kwargs): return {"ids": list(documents)}
    class Vector:
        collection = Collection()
        def add_documents(self, rows):
            documents.update({row["id"]: row for row in rows})
            return [row["id"] for row in rows]
        def delete_by_ids(self, ids):
            for id_ in ids: documents.pop(id_, None)
    monkeypatch.setattr(profile, "build_feature_pack_statuses", lambda *args, **kwargs: [{"id": "vector_memory", "status": "installed", "restartRequired": False}])
    monkeypatch.setattr(service, "_get_vector_store", lambda: Vector())
    result = asyncio.run(service.recover_outbox(rebuild_vectors=True))
    assert result["processed"] == result["completed"] == 501
    assert len(documents) == 501
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_projection_outbox WHERE status != 'completed'").fetchone()[0] == 0
