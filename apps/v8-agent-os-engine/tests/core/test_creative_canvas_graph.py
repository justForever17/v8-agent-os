from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.creative_canvas_graph as graph_module
from core.creative_canvas_graph import (
    ACTION_DEFINITIONS,
    CreativeCanvasGraphConflict,
    CreativeCanvasGraphError,
    CreativeCanvasGraphService,
)


def test_motion_guidance_action_keeps_motion_and_video_ports_explicit():
    definition = ACTION_DEFINITIONS["creative_media.render_motion_guidance"]
    assert definition.capability == "video.render_motion_guidance"
    assert definition.inputs[0].media_types == ("motion",)
    assert definition.output_media_types == ("video",)
    assert definition.network_required is False
    references = ACTION_DEFINITIONS["creative_media.generate_video_from_references"]
    assert references.inputs[0].media_types == ("image", "video", "audio")
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


def test_command_history_survives_reload_and_appends_undo_redo_commands(canvas_service) -> None:
    service, database = canvas_service
    first = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Edit"),
        expected_revision=0,
    )
    moved_graph = _graph("source-a", prompt="Edit")
    moved_graph["nodes"][0]["x"] = 144
    second = service.save_graph(
        session_id="session-a",
        graph=moved_graph,
        expected_revision=first["revision"],
    )
    assert second["history"]["canUndo"] is True
    assert second["history"]["undoDepth"] == 2
    assert second["history"]["lastCommand"]["kind"] == "move_nodes"

    recovered_service = CreativeCanvasGraphService()
    undone = recovered_service.apply_history(
        session_id="session-a",
        direction="undo",
        expected_revision=second["revision"],
    )
    assert undone["graph"]["nodes"][0]["x"] == 0
    assert undone["history"]["canRedo"] is True
    assert undone["history"]["redoDepth"] == 1

    redone = recovered_service.apply_history(
        session_id="session-a",
        direction="redo",
        expected_revision=undone["revision"],
    )
    assert redone["graph"]["nodes"][0]["x"] == 144
    assert redone["history"]["canRedo"] is False
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT direction, target_command_id FROM creative_canvas_commands WHERE session_id = ? ORDER BY command_sequence",
            ("session-a",),
        ).fetchall()
    assert [row["direction"] for row in rows] == ["forward", "forward", "undo", "redo"]
    assert rows[2]["target_command_id"] == rows[3]["target_command_id"]


def test_viewport_updates_do_not_pollute_command_history(canvas_service) -> None:
    service, database = canvas_service
    first = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Edit"),
        expected_revision=0,
    )
    viewport_only = _graph("source-a", prompt="Edit")
    viewport_only["viewport"] = {"x": -120, "y": 84, "scale": 1.4}
    second = service.save_graph(
        session_id="session-a",
        graph=viewport_only,
        expected_revision=first["revision"],
    )
    assert second["revision"] == first["revision"] + 1
    assert second["history"]["undoDepth"] == first["history"]["undoDepth"]
    with database.get_connection() as conn:
        command_count = conn.execute(
            "SELECT COUNT(*) FROM creative_canvas_commands WHERE session_id = ?",
            ("session-a",),
        ).fetchone()[0]
    assert command_count == 1


def test_history_is_session_scoped_and_revision_fenced(canvas_service) -> None:
    service, _database = canvas_service
    saved_a = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Edit A"),
        expected_revision=0,
    )
    graph_b = _graph("source-b", prompt="Edit B")
    graph_b["graphId"] = "canvas-graph-test-b"
    saved_b = service.save_graph(
        session_id="session-b",
        graph=graph_b,
        expected_revision=0,
    )
    with pytest.raises(CreativeCanvasGraphConflict, match="revision changed"):
        service.apply_history(session_id="session-a", direction="undo", expected_revision=999)
    undone_b = service.apply_history(
        session_id="session-b",
        direction="undo",
        expected_revision=saved_b["revision"],
    )
    assert undone_b["graph"]["nodes"] == []
    unchanged_a = service.get_graph(session_id="session-a")
    assert unchanged_a["revision"] == saved_a["revision"]
    assert unchanged_a["graph"]["nodes"][0]["resourceId"] == "source-a"


def test_preflight_returns_node_level_diagnostics_and_honors_configured_candidates(canvas_service) -> None:
    service, _database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Edit"),
        expected_revision=0,
    )
    blocked = service.preflight_execution(
        session_id="session-a",
        graph_id=saved["graph"]["graphId"],
        graph_revision=saved["revision"],
        target_node_ids=["result-node"],
        available_operation_kinds=set(),
    )
    assert blocked["valid"] is False
    assert blocked["plan"]["actions"][0]["actionNodeId"] == "action-node"
    provider_issue = next(item for item in blocked["issues"] if item["code"] == "provider-unconfigured")
    assert provider_issue["nodeId"] == "action-node"
    assert provider_issue["capability"] == "image.edit"
    assert provider_issue["remediation"] == "configure_model"

    ready = service.preflight_execution(
        session_id="session-a",
        graph_id=saved["graph"]["graphId"],
        graph_revision=saved["revision"],
        target_node_ids=["result-node"],
        available_operation_kinds={"image.edit"},
    )
    assert ready["valid"] is True
    assert {item["code"] for item in ready["issues"]} == {"network-required", "possible-cost"}

    local_blocked = service.preflight_execution(
        session_id="session-a",
        graph_id=saved["graph"]["graphId"],
        graph_revision=saved["revision"],
        target_node_ids=["result-node"],
        available_operation_kinds={"image.edit"},
        unavailable_operation_reasons={"image.edit": "configured_local_runtime_unavailable"},
    )
    assert local_blocked["valid"] is False
    local_issue = next(item for item in local_blocked["issues"] if item["code"] == "local-runtime-unavailable")
    assert local_issue == {
        "severity": "error",
        "code": "local-runtime-unavailable",
        "nodeId": "action-node",
        "capability": "image.edit",
        "detail": "configured_local_runtime_unavailable",
        "remediation": "configure_local_runtime",
    }


def test_preflight_exposes_missing_prompt_without_http_error_shape(canvas_service) -> None:
    service, _database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a"),
        expected_revision=0,
    )
    result = service.preflight_execution(
        session_id="session-a",
        graph_id=saved["graph"]["graphId"],
        graph_revision=saved["revision"],
        target_node_ids=["result-node"],
        available_operation_kinds={"image.edit"},
    )
    assert result["valid"] is False
    assert result["plan"] is None
    assert result["issues"] == [{
        "severity": "error",
        "code": "missing-prompt",
        "nodeId": "action-node",
        "capability": "creative_media.edit_image",
        "detail": "Canvas action requires an instruction: creative_media.edit_image",
        "remediation": "configure_action",
    }]


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
