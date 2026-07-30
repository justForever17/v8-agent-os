from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.creative_canvas_graph as graph_module
from core.creative_canvas_graph import (
    CreativeCanvasGraphConflict,
    CreativeCanvasGraphError,
    CreativeCanvasGraphService,
)
from core.database import DatabaseManager


def _authority(path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_root=str(path),
        workspace_id="workspace-a",
        project_id="project-a",
        side_effects_allowed=True,
    )


def _graph(source_id: str, *, prompt: str = "") -> dict:
    return {
        "schema": "v8.creative_canvas_graph.v1",
        "version": 3,
        "graphId": "canvas-graph-test",
        "nodes": [
            {
                "nodeId": "source-node",
                "kind": "resource",
                "origin": "source",
                "resourceId": source_id,
                "mediaType": "image",
                "title": "Source",
                "x": 0,
                "y": 0,
                "width": 280,
                "height": 190,
            },
            {
                "nodeId": "action-node",
                "kind": "action",
                "actionDefinitionId": "creative_media.edit_image",
                "prompt": prompt,
                "parameters": {},
                "configurationRevision": 1,
                "title": "Edit image",
                "x": 380,
                "y": 0,
                "width": 280,
                "height": 190,
            },
            {
                "nodeId": "result-node",
                "kind": "result",
                "producerActionNodeId": "action-node",
                "outputSlot": "image_derivative",
                "mediaType": "image",
                "title": "Result",
                "x": 760,
                "y": 0,
                "width": 280,
                "height": 190,
            },
        ],
        "edges": [
            {
                "edgeId": "source-action",
                "from": "source-node",
                "to": "action-node",
                "fromPort": "right",
                "toPort": "left",
                "fromPortId": "output",
                "toPortId": "image",
                "dataType": "image",
                "role": "data",
                "order": 0,
                "note": "Retain the composition",
            },
            {
                "edgeId": "action-result",
                "from": "action-node",
                "to": "result-node",
                "fromPort": "right",
                "toPort": "left",
                "fromPortId": "output",
                "toPortId": "input",
                "dataType": "image",
                "role": "data",
                "order": 0,
                "note": "",
            },
        ],
        "viewport": {"x": 24, "y": 24, "scale": 1},
    }


@pytest.fixture()
def canvas_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database = DatabaseManager(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for session_id in ("session-a", "session-b", "session-c"):
        database.create_or_update_session(session_id, session_id, user_id="user")
    database.add_session_source(
        source_id="source-a",
        session_id="session-a",
        source_kind="web_upload",
        mime_type="image/png",
        title="source.png",
        workspace_path=".v8/uploads/source.png",
    )
    database.add_session_source(
        source_id="source-b",
        session_id="session-b",
        source_kind="web_upload",
        mime_type="image/png",
        title="source-b.png",
        workspace_path=".v8/uploads/source-b.png",
    )
    monkeypatch.setattr(graph_module, "db", database)
    monkeypatch.setattr(
        graph_module.workspace_authority_service,
        "resolve",
        lambda **kwargs: _authority(tmp_path / "other-workspace")
        if kwargs.get("session_id") == "session-c"
        else _authority(workspace),
    )
    return CreativeCanvasGraphService(), database


def test_draft_save_is_recoverable_but_execution_checks_only_the_target_subgraph(canvas_service) -> None:
    service, _database = canvas_service
    saved = service.save_graph(session_id="session-a", graph=_graph("source-a"), expected_revision=0)
    assert saved["revision"] == 1
    assert saved["graph"]["edges"][0]["note"] == "Retain the composition"

    with pytest.raises(CreativeCanvasGraphError, match="requires an instruction"):
        service.execution_contract_summary(
            session_id="session-a",
            graph_id="canvas-graph-test",
            graph_revision=1,
            target_node_ids=["result-node"],
        )

    configured = _graph("source-a", prompt="Change the jacket to silver")
    configured["nodes"].extend([
        {
            "nodeId": "draft-action",
            "kind": "action",
            "actionDefinitionId": "creative_media.generate_video",
            "prompt": "",
            "parameters": {},
            "configurationRevision": 1,
            "title": "Draft video",
            "x": 0,
            "y": 320,
            "width": 280,
            "height": 190,
        },
        {
            "nodeId": "draft-result",
            "kind": "result",
            "producerActionNodeId": "draft-action",
            "outputSlot": "video",
            "mediaType": "video",
            "title": "Draft result",
            "x": 380,
            "y": 320,
            "width": 280,
            "height": 190,
        },
    ])
    configured["edges"].append({
        "edgeId": "draft-result-edge",
        "from": "draft-action",
        "to": "draft-result",
        "role": "data",
        "fromPort": "right",
        "toPort": "left",
        "fromPortId": "output",
        "toPortId": "input",
        "dataType": "video",
        "order": 0,
        "note": "",
    })
    saved = service.save_graph(session_id="session-a", graph=configured, expected_revision=1)
    plan = service.execution_contract_summary(
        session_id="session-a",
        graph_id="canvas-graph-test",
        graph_revision=saved["revision"],
        target_node_ids=["result-node"],
    )
    assert [item["actionNodeId"] for item in plan["actions"]] == ["action-node"]
    assert plan["resourceRefs"] == [{"origin": "source", "id": "source-a", "mediaType": "image"}]
    assert plan["actions"][0]["relationshipNotes"] == [{
        "edgeId": "source-action",
        "fromNodeId": "source-node",
        "toNodeId": "action-node",
        "note": "Retain the composition",
    }]


def test_graph_rejects_cross_session_resources_and_stale_revisions(canvas_service) -> None:
    service, _database = canvas_service
    service.save_graph(session_id="session-a", graph=_graph("source-a", prompt="Edit"), expected_revision=0)
    with pytest.raises(CreativeCanvasGraphConflict, match="revision changed"):
        service.save_graph(session_id="session-a", graph=_graph("source-a", prompt="Edit again"), expected_revision=0)
    with pytest.raises(CreativeCanvasGraphError, match="current session"):
        service.save_graph(session_id="session-b", graph=_graph("source-a", prompt="Foreign"), expected_revision=0)


class _FakeCreativeRuntime:
    def __init__(self, database: DatabaseManager) -> None:
        self.database = database
        self.counter = 0
        self.requests: list[dict] = []

    def _new_job(self, *, modality: str, adapter: str, request: dict) -> dict:
        return {"jobId": f"outer-{self.counter + 1}", "modality": modality, "adapter": adapter, "request": request}

    def _save_job(self, job: dict) -> dict:
        return dict(job)

    async def create_job(self, request: dict) -> dict:
        self.counter += 1
        self.requests.append(dict(request))
        artifact_id = f"artifact-{self.counter}"
        self.database.add_runtime_artifact(
            artifact_id,
            "image",
            "image/png",
            session_id=request["sessionId"],
            run_id=request.get("runId") or None,
            title=f"result-{self.counter}.png",
            preview_url=f"/preview/{artifact_id}",
            metadata={"canvasOperationId": request["canvasOperationId"]},
        )
        return {
            "jobId": f"inner-{self.counter}",
            "status": "succeeded",
            "artifacts": [{"artifactId": artifact_id, "mediaType": "image", "previewUrl": f"/preview/{artifact_id}"}],
        }


def test_execution_updates_persistent_result_slot_and_keeps_versions(canvas_service) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Change the jacket to silver"),
        expected_revision=0,
    )
    runtime = _FakeCreativeRuntime(database)
    for operation_id in ("canvas-op-1", "canvas-op-2"):
        job = asyncio.run(service.execute_as_creative_job(runtime, {
                "modality": "workflow",
                "operationKind": "canvas.graph.execute",
                "sessionId": "session-a",
                "projectId": "project-a",
                "workspaceId": "workspace-a",
                "workspacePath": "C:/workspace-a",
                "graphId": "canvas-graph-test",
                "graphRevision": saved["revision"],
                "canvasOperationId": operation_id,
                "targetNodeIds": ["result-node"],
            }))
        assert job["status"] == "succeeded"

    recovered = service.get_graph(session_id="session-a")
    versions = recovered["runtime"]["outputs"]["result-node"]
    assert [item["version"] for item in versions] == [2, 1]
    assert [item["artifactId"] for item in versions] == ["artifact-2", "artifact-1"]
    assert runtime.requests[0]["prompt"] == (
        "Change the jacket to silver\n\n"
        "关系说明（来自画布）：\n"
        "- Retain the composition"
    )
    assert runtime.requests[0]["projectId"] == "project-a"
    assert runtime.requests[0]["workspaceId"] == "workspace-a"


def test_execution_rejects_a_second_active_graph_run_for_the_session(canvas_service) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Edit once"),
        expected_revision=0,
    )
    with database.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO creative_canvas_graph_runs(
                graph_run_id, graph_id, session_id, canvas_operation_id, graph_revision,
                target_node_ids_json, plan_json, node_states_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '[]', '{}', '{}', 'running', ?, ?)
            """,
            (
                "canvas-run-active",
                saved["graph"]["graphId"],
                "session-a",
                "canvas-op-active",
                saved["revision"],
                "2026-07-29T00:00:00Z",
                "2026-07-29T00:00:00Z",
            ),
        )
        conn.commit()

    runtime = _FakeCreativeRuntime(database)
    job = asyncio.run(service.execute_as_creative_job(runtime, {
        "modality": "workflow",
        "operationKind": "canvas.graph.execute",
        "sessionId": "session-a",
        "graphId": saved["graph"]["graphId"],
        "graphRevision": saved["revision"],
        "canvasOperationId": "canvas-op-second",
        "targetNodeIds": ["result-node"],
    }))

    assert job["status"] == "failed"
    assert "already active" in job["error"]
    assert runtime.requests == []


def test_workspace_template_removes_session_resources_and_requires_rebinding(canvas_service) -> None:
    service, _database = canvas_service
    service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Template edit"),
        expected_revision=0,
    )
    template = service.save_template(session_id="session-a", title="Portrait edit")
    assert template["graph"]["nodes"][0]["kind"] == "input"
    assert "resourceId" not in template["graph"]["nodes"][0]

    instantiated = service.instantiate_template(
        session_id="session-b",
        template_id=template["templateId"],
        expected_revision=0,
        mode="replace",
    )
    assert instantiated["graph"]["nodes"][0]["kind"] == "input"
    with pytest.raises(CreativeCanvasGraphError, match="unbound workflow input"):
        service.execution_contract_summary(
            session_id="session-b",
            graph_id=instantiated["graph"]["graphId"],
            graph_revision=instantiated["revision"],
            target_node_ids=["result-node"],
        )

    with pytest.raises(CreativeCanvasGraphError, match="current workspace"):
        service.get_template(session_id="session-c", template_id=template["templateId"])

    service.delete_template(session_id="session-b", template_id=template["templateId"])
    with pytest.raises(CreativeCanvasGraphError, match="current workspace"):
        service.get_template(session_id="session-a", template_id=template["templateId"])


def test_workspace_template_requires_an_action_card(canvas_service) -> None:
    service, _database = canvas_service
    graph = _graph("source-a")
    graph["nodes"] = [graph["nodes"][0]]
    graph["edges"] = []
    service.save_graph(session_id="session-a", graph=graph, expected_revision=0)

    with pytest.raises(CreativeCanvasGraphError, match="at least one action card"):
        service.save_template(session_id="session-a", title="Source only")


@pytest.mark.parametrize("value", ["not-a-number", "NaN", float("inf"), True])
def test_graph_rejects_non_finite_or_non_numeric_geometry(canvas_service, value) -> None:
    service, _database = canvas_service
    graph = _graph("source-a")
    graph["nodes"][0]["x"] = value

    with pytest.raises(CreativeCanvasGraphError, match="Canvas node x"):
        service.save_graph(session_id="session-a", graph=graph, expected_revision=0)
