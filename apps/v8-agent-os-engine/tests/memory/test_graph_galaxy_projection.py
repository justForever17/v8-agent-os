from __future__ import annotations

import json
import importlib
import asyncio

import pytest

from core.knowledge_db import KnowledgeDB
from runtimes.memory.knowledge_service import KnowledgeService


@pytest.fixture
def graph(tmp_path, monkeypatch):
    db = KnowledgeDB(tmp_path / "galaxy.db")
    for scope in ["global", "workspace:a", "workspace:b"]:
        db.add_scoped_relation("same", "USES", "shared", scope=scope, evidence_refs=[f"fixture://{scope}/evidence"])
    monkeypatch.setattr(importlib.import_module("runtimes.memory.knowledge_service"), "knowledge_db", db)
    return db


def test_exact_clusters_do_not_repeat_global_or_merge_relations(graph):
    clusters = [graph.get_graph_cluster(scopes=[scope]) for scope in ["global", "workspace:a", "workspace:b"]]
    assert [cluster["meta"]["totalRelations"] for cluster in clusters] == [1, 1, 1]
    assert len({cluster["links"][0]["relationId"] for cluster in clusters}) == 3
    assert [cluster["links"][0]["scope"] for cluster in clusters] == ["global", "workspace:a", "workspace:b"]
    assert clusters[1]["links"][0]["evidenceRefs"] == ["fixture://workspace:a/evidence"]
    assert graph.get_full_graph(scopes=["workspace:a"])["meta"]["totalRelations"] == 2


def test_scope_oracle_detects_the_old_implicit_global_mutant(graph, monkeypatch):
    original = graph._active_graph_relations_sql
    monkeypatch.setattr(graph, "_active_graph_relations_sql", lambda **kwargs: original(**(kwargs | {"include_global": True})))
    with pytest.raises(AssertionError):
        test_exact_clusters_do_not_repeat_global_or_merge_relations(graph)


def test_empty_and_wildcard_scope_never_expand_authority(graph):
    for scopes in [[], ["*"]]:
        with pytest.raises(ValueError):
            graph.get_graph_cluster(scopes=scopes)
    assert graph.get_graph_cluster(scopes=["workspace:empty"])["nodes"] == []


def test_node_and_edge_and_byte_budgets_keep_truthful_totals(graph):
    for left in range(15):
        for right in range(left + 1, 15):
            graph.add_scoped_relation(f"node-{left}", "LINK", f"node-{right}", scope="workspace:large", evidence_refs=["fixture://dense"])
    preview = graph.get_graph_cluster(scopes=["workspace:large"], node_limit=7, edge_limit=4, byte_limit=2048)
    assert preview["meta"]["totalRelations"] == 105
    assert preview["meta"]["totalEntities"] == 15
    assert len(preview["nodes"]) <= 7 and len(preview["links"]) <= 4
    assert preview["meta"]["truncated"] is True
    assert len(json.dumps(preview, ensure_ascii=False).encode()) <= 2048
    names = {node["id"] for node in preview["nodes"]}
    assert all(edge["source"] in names and edge["target"] in names for edge in preview["links"])


def test_delete_a_keeps_shared_entity_b_and_global_and_changes_version(graph):
    before = graph.get_graph_cluster(scopes=["workspace:a"])["meta"]["version"]
    assert graph.delete_entity("same", scope="workspace:a") is True
    assert graph.get_graph_cluster(scopes=["workspace:a"])["meta"]["totalRelations"] == 0
    assert graph.get_graph_cluster(scopes=["workspace:a"])["meta"]["version"] != before
    for scope in ["global", "workspace:b"]:
        assert graph.get_graph_cluster(scopes=[scope])["meta"]["totalRelations"] == 1
    with graph._conn() as conn:
        assert conn.execute("SELECT name FROM entities WHERE name = 'same'").fetchone()


def test_relation_menu_paginates_without_preview_limit(graph):
    for index in range(72):
        graph.add_scoped_relation("same", "USES", f"target-{index}", scope="workspace:a", evidence_refs=["fixture://page"])
    ids = []
    offset = 0
    while offset is not None:
        page = graph.query_graph_cluster_entity(entity="same", scopes=["workspace:a"], offset=offset, limit=17)
        assert page["total"] == 73
        assert all(item["scope"] == "workspace:a" for item in page["relations"])
        ids.extend(item["relationId"] for item in page["relations"])
        offset = page["nextOffset"]
    assert len(ids) == len(set(ids)) == 73


def test_overview_pages_global_once_and_rejects_forged_cluster(graph, monkeypatch):
    service = KnowledgeService()
    monkeypatch.setattr(service, "_build_graph_workspace_catalog", lambda: {"items": [
        {"workspaceKey": f"ws-{index}", "label": f"Workspace {index}", "_scopes": {"workspace:a"}, "writeScope": "workspace:a"}
        for index in range(21)
    ]})
    first = service.get_graph_overview()
    second = service.get_graph_overview(offset=8)
    assert len(first["items"]) == 9 and first["items"][0]["clusterId"] == "global"
    assert len(second["items"]) == 8 and all(item["scopeKind"] == "workspace" for item in second["items"])
    assert first["nextOffset"] == 8 and second["nextOffset"] == 16
    assert all(item["meta"]["totalRelations"] == 1 for item in first["items"])
    found = service.get_graph_overview(workspace_query="Workspace 20")
    assert found["totalWorkspaces"] == 21 and found["matchingWorkspaces"] == 1
    assert [item["label"] for item in found["items"]] == ["Global", "Workspace 20"]
    assert found["nextOffset"] is None
    with pytest.raises(ValueError, match="not_found"):
        service.get_graph_overview(cluster_id='["workspace","unregistered"]')


def test_global_background_entity_connects_only_to_explicit_workspace_through_api(graph, monkeypatch):
    from api.knowledge_routes import add_graph_relation, remove_graph_relation
    from api.models import GraphRelationPayload
    from fastapi import HTTPException
    from runtimes.memory.knowledge_service import knowledge_service

    monkeypatch.setattr(knowledge_service, "_build_graph_workspace_catalog", lambda: {"defaultWorkspaceKey": "a", "items": [
        {"workspaceKey": "a", "label": "A", "_scopes": {"workspace:a"}, "writeScope": "workspace:a"},
        {"workspaceKey": "b", "label": "B", "_scopes": {"workspace:b"}, "writeScope": "workspace:b"},
    ]})
    graph.add_scoped_relation("global-only", "OWNS", "global-target", scope="global", evidence_refs=["fixture://global"])
    global_before = graph.get_graph_cluster(scopes=["global"])
    result = asyncio.run(add_graph_relation(GraphRelationPayload(
        subject="global-only", predicate="USES", object="new-local-target", workspaceKey="b", maintainerSource="human_admin")))
    assert result["created"] is True
    assert graph.get_graph_cluster(scopes=["global"]) == global_before
    assert graph.query_graph_cluster_entity(entity="global-only", scopes=["workspace:a"])["total"] == 0
    local = graph.query_graph_cluster_entity(entity="global-only", scopes=["workspace:b"])
    assert local["total"] == 1 and local["relations"][0]["scope"] == "workspace:b"
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(remove_graph_relation(GraphRelationPayload(
            subject="global-only", predicate="OWNS", object="global-target", workspaceKey="b", scope="global")))
    assert rejected.value.status_code == 400
    assert graph.get_graph_cluster(scopes=["global"]) == global_before
