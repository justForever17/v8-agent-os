from __future__ import annotations

import ast
import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from types import ModuleType, SimpleNamespace

import pytest

import core.creative_canvas_graph as graph_module
import erc.event_bus as event_bus_module
from core.creative_canvas_graph import (
    ACTION_DEFINITIONS,
    CreativeCanvasGraphConflict,
    CreativeCanvasGraphError,
    CreativeCanvasGraphService,
)
from core.runtime_projection import project_runtime_timeline_from_events


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
    monkeypatch.setattr(event_bus_module, "db", database)
    monkeypatch.setattr(
        graph_module.workspace_authority_service,
        "resolve",
        lambda **kwargs: _authority(tmp_path / "other-workspace")
        if kwargs.get("session_id") == "session-c"
        else _authority(workspace),
    )
    return CreativeCanvasGraphService(), database


def _canvas_graph_run_state_events(database: DatabaseManager, session_id: str = "session-a") -> list[dict]:
    return [
        event
        for event in database.get_runtime_events(session_id)
        if event.get("topic") == "canvas.graph.run.state"
    ]


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

    async def cancel_job(self, _job_id: str) -> dict:
        return {
            "status": "not_active",
            "detailCode": "fake_provider_terminal",
            "remoteTaskMayContinue": False,
        }

    async def cleanup_job(self, _job_id: str) -> dict:
        return {
            "status": "not_active",
            "detailCode": "fake_cleanup_empty",
            "remoteTaskMayContinue": False,
        }

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


def _chain_graph(source_id: str) -> dict:
    graph = _graph(source_id, prompt="First paid edit")
    graph["nodes"][1]["nodeId"] = "action-a"
    graph["nodes"][2]["nodeId"] = "result-a"
    graph["nodes"][2]["producerActionNodeId"] = "action-a"
    graph["edges"][0]["to"] = "action-a"
    graph["edges"][1]["from"] = "action-a"
    graph["edges"][1]["to"] = "result-a"
    graph["nodes"].extend([
        {
            "nodeId": "action-b",
            "kind": "action",
            "actionDefinitionId": "creative_media.edit_image",
            "prompt": "Second paid edit",
            "parameters": {},
            "configurationRevision": 1,
            "title": "Second edit",
            "x": 1140,
            "y": 0,
            "width": 280,
            "height": 190,
        },
        {
            "nodeId": "result-b",
            "kind": "result",
            "producerActionNodeId": "action-b",
            "outputSlot": "image_derivative",
            "mediaType": "image",
            "title": "Final result",
            "x": 1520,
            "y": 0,
            "width": 280,
            "height": 190,
        },
    ])
    graph["edges"].extend([
        {
            "edgeId": "result-a-action-b",
            "from": "result-a",
            "to": "action-b",
            "fromPort": "right",
            "toPort": "left",
            "fromPortId": "output",
            "toPortId": "image",
            "dataType": "image",
            "role": "data",
            "order": 0,
            "note": "Reuse the paid ancestor",
        },
        {
            "edgeId": "action-b-result-b",
            "from": "action-b",
            "to": "result-b",
            "fromPort": "right",
            "toPort": "left",
            "fromPortId": "output",
            "toPortId": "input",
            "dataType": "image",
            "role": "data",
            "order": 0,
            "note": "",
        },
    ])
    return graph


class _BranchRetryRuntime(_FakeCreativeRuntime):
    def __init__(self, database: DatabaseManager) -> None:
        super().__init__(database)
        self.node_attempts: dict[str, int] = {}

    async def create_job(self, request: dict) -> dict:
        node_id = str(request["canvasGraphNodeId"])
        self.node_attempts[node_id] = self.node_attempts.get(node_id, 0) + 1
        if node_id == "action-b" and self.node_attempts[node_id] == 1:
            self.counter += 1
            self.requests.append(dict(request))
            return {"jobId": f"inner-{self.counter}", "status": "failed", "error": "provider exploded"}
        return await super().create_job(request)


class _PendingLifecycleRuntime(_FakeCreativeRuntime):
    def __init__(self, database: DatabaseManager) -> None:
        super().__init__(database)
        self.poll_started = asyncio.Event()
        self.release_poll = asyncio.Event()
        self.cancelled_job_ids: list[str] = []
        self.cleaned_job_ids: list[str] = []
        self.jobs: dict[str, dict] = {}

    async def create_job(self, request: dict) -> dict:
        self.requests.append(dict(request))
        job = {"jobId": "inner-pending", "status": "running", "artifacts": []}
        self.jobs[job["jobId"]] = job
        return dict(job)

    async def refresh_job(self, job_id: str) -> dict:
        self.poll_started.set()
        await self.release_poll.wait()
        return dict(self.jobs[job_id])

    async def cancel_job(self, job_id: str) -> dict:
        self.cancelled_job_ids.append(job_id)
        self.jobs[job_id]["status"] = "cancelled"
        self.release_poll.set()
        return {
            "status": "completed",
            "detailCode": "fake_cancelled",
            "remoteTaskMayContinue": False,
        }

    async def cleanup_job(self, job_id: str) -> dict:
        self.cleaned_job_ids.append(job_id)
        return {
            "status": "completed",
            "detailCode": "fake_cleanup",
            "remoteTaskMayContinue": False,
        }


class _SlowCancelLifecycleRuntime(_PendingLifecycleRuntime):
    def __init__(self, database: DatabaseManager) -> None:
        super().__init__(database)
        self.cancel_started = asyncio.Event()
        self.release_cancel = asyncio.Event()

    async def cancel_job(self, job_id: str) -> dict:
        self.cancelled_job_ids.append(job_id)
        self.cancel_started.set()
        self.release_poll.set()
        await self.release_cancel.wait()
        self.jobs[job_id]["status"] = "cancelled"
        return {
            "status": "completed",
            "detailCode": "fake_slow_cancelled",
            "remoteTaskMayContinue": False,
        }


class _PollFailureRuntime(_FakeCreativeRuntime):
    def __init__(self, database: DatabaseManager) -> None:
        super().__init__(database)
        self.jobs: dict[str, dict] = {}
        self.cancelled_job_ids: list[str] = []
        self.cleaned_job_ids: list[str] = []
        self.refresh_calls = 0

    async def create_job(self, request: dict) -> dict:
        self.counter += 1
        self.requests.append(dict(request))
        job_id = f"inner-poll-{self.counter}"
        if self.counter == 1:
            job = {"jobId": job_id, "status": "running", "artifacts": []}
            self.jobs[job_id] = job
            return dict(job)
        artifact_id = f"artifact-retry-{self.counter}"
        self.database.add_runtime_artifact(
            artifact_id,
            "image",
            "image/png",
            session_id=request["sessionId"],
            run_id=request.get("runId") or None,
            title=f"{artifact_id}.png",
            preview_url=f"/preview/{artifact_id}",
            metadata={"canvasOperationId": request["canvasOperationId"]},
        )
        job = {
            "jobId": job_id,
            "status": "succeeded",
            "artifacts": [{"artifactId": artifact_id, "mediaType": "image", "previewUrl": f"/preview/{artifact_id}"}],
        }
        self.jobs[job_id] = job
        return dict(job)

    async def refresh_job(self, job_id: str) -> dict:
        self.refresh_calls += 1
        if self.refresh_calls == 1:
            raise RuntimeError("transient provider poll failure")
        return dict(self.jobs[job_id])

    async def cancel_job(self, job_id: str) -> dict:
        self.cancelled_job_ids.append(job_id)
        self.jobs[job_id]["status"] = "cancelled"
        return {
            "status": "completed",
            "detailCode": "fake_poll_failure_cancelled",
            "remoteTaskMayContinue": False,
        }

    async def cleanup_job(self, job_id: str) -> dict:
        self.cleaned_job_ids.append(job_id)
        return {
            "status": "completed",
            "detailCode": "fake_poll_failure_cleanup",
            "remoteTaskMayContinue": False,
        }


class _ReservedSubmissionRuntime(_PendingLifecycleRuntime):
    def __init__(self, database: DatabaseManager) -> None:
        super().__init__(database)
        self.submission_started = asyncio.Event()
        self.reserved_job_id = "cm_11111111111111111111111111111111"

    def _reserve_job_id(self) -> str:
        return self.reserved_job_id

    async def create_job(self, request: dict, *, reserved_job_id: str = "") -> dict:
        assert reserved_job_id == self.reserved_job_id
        self.requests.append(dict(request))
        job = {"jobId": reserved_job_id, "status": "running", "artifacts": []}
        self.jobs[job["jobId"]] = job
        self.submission_started.set()
        await asyncio.Event().wait()
        return dict(job)


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


def test_execution_emits_only_canonical_graph_run_state_changes(canvas_service) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Change the jacket to silver"),
        expected_revision=0,
    )
    runtime = _FakeCreativeRuntime(database)

    job = asyncio.run(service.execute_as_creative_job(runtime, {
        "modality": "workflow",
        "operationKind": "canvas.graph.execute",
        "sessionId": "session-a",
        "graphId": saved["graph"]["graphId"],
        "graphRevision": saved["revision"],
        "canvasOperationId": "canvas-op-realtime",
        "targetNodeIds": ["result-node"],
    }))

    assert job["status"] == "succeeded"
    events = _canvas_graph_run_state_events(database)
    assert [event["payload"]["status"] for event in events] == ["queued", "running", "completed"]
    assert [event["run_id"] for event in events] == [None, None, None]
    assert all(event["source"] == {
        "plane": "engine",
        "component": "creative_canvas_graph",
        "node": "graph_run_state",
        "agent_id": None,
    } for event in events)
    assert events[0]["payload"] == {
        "schema": "v8.creative_canvas_graph_run_state.v1",
        "sessionId": "session-a",
        "workspaceId": "workspace-a",
        "graphId": "canvas-graph-test",
        "graphRunId": job["canvasGraphRunId"],
        "canvasOperationId": "canvas-op-realtime",
        "runId": None,
        "status": "queued",
    }
    timeline = project_runtime_timeline_from_events(events)
    assert [entry["status"] for entry in timeline] == ["queued", "running", "completed"]
    assert [entry["metadata"] for entry in timeline] == [event["payload"] for event in events]


def test_realtime_append_failure_does_not_falsify_committed_graph_state(
    canvas_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Change the jacket to silver"),
        expected_revision=0,
    )
    runtime = _FakeCreativeRuntime(database)

    def fail_create_emitter(**_kwargs):
        raise RuntimeError("runtime event store unavailable")

    monkeypatch.setattr(graph_module.event_bus, "create_emitter", fail_create_emitter)
    job = asyncio.run(service.execute_as_creative_job(runtime, {
        "modality": "workflow",
        "operationKind": "canvas.graph.execute",
        "sessionId": "session-a",
        "graphId": saved["graph"]["graphId"],
        "graphRevision": saved["revision"],
        "canvasOperationId": "canvas-op-event-failure",
        "targetNodeIds": ["result-node"],
    }))

    assert job["status"] == "succeeded"
    assert service.get_graph(session_id="session-a")["runtime"]["status"] == "succeeded"
    assert _canvas_graph_run_state_events(database) == []


def test_direct_execution_preflight_binds_the_current_session_and_workspace(canvas_service) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Change the jacket to silver"),
        expected_revision=0,
    )

    prepared = service.prepare_direct_execution(
        session_id="session-a",
        graph_id=saved["graph"]["graphId"],
        graph_revision=saved["revision"],
        target_node_ids=["result-node"],
    )
    assert prepared["targetNodeIds"] == ["result-node"]

    with pytest.raises(CreativeCanvasGraphError, match="current session"):
        service.prepare_direct_execution(
            session_id="session-b",
            graph_id=saved["graph"]["graphId"],
            graph_revision=saved["revision"],
            target_node_ids=["result-node"],
        )

    with database.get_connection() as conn:
        conn.execute(
            "UPDATE creative_canvas_graphs SET workspace_key = ? WHERE session_id = ?",
            ("different-workspace", "session-a"),
        )
        conn.commit()
    with pytest.raises(CreativeCanvasGraphConflict, match="workspace binding changed"):
        service.prepare_direct_execution(
            session_id="session-a",
            graph_id=saved["graph"]["graphId"],
            graph_revision=saved["revision"],
            target_node_ids=["result-node"],
        )


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


def test_cancel_run_propagates_to_current_provider_job_and_cleanup(
    canvas_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Long provider edit"),
        expected_revision=0,
    )
    runtime = _PendingLifecycleRuntime(database)
    real_sleep = asyncio.sleep

    async def immediate_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(graph_module.asyncio, "sleep", immediate_sleep)

    async def scenario() -> dict:
        execution = asyncio.create_task(service.execute_as_creative_job(runtime, {
            "modality": "workflow",
            "operationKind": "canvas.graph.execute",
            "sessionId": "session-a",
            "graphId": saved["graph"]["graphId"],
            "graphRevision": saved["revision"],
            "canvasOperationId": "canvas-op-cancel",
            "targetNodeIds": ["result-node"],
        }))
        await runtime.poll_started.wait()
        active = service.get_graph(session_id="session-a")["runtime"]
        cancelled = await service.cancel_run(
            runtime,
            session_id="session-a",
            graph_run_id=active["graphRunId"],
            reason="user_cancelled",
        )
        await execution
        return cancelled

    cancelled = asyncio.run(scenario())
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"]["code"] == "user_cancelled"
    assert cancelled["recovery"]["canRetry"] is False
    assert runtime.cancelled_job_ids == ["inner-pending"]
    assert runtime.cleaned_job_ids == ["inner-pending"]
    recovered = service.get_graph(session_id="session-a")["runtime"]
    assert recovered["status"] == "cancelled"
    assert recovered["nodeStates"]["action-node"]["state"] == "cancelled"
    assert recovered["nodeStates"]["action-node"]["providerCancellation"] == "completed"
    assert recovered["nodeStates"]["action-node"]["providerCleanup"] == "completed"
    assert [event["payload"]["status"] for event in _canvas_graph_run_state_events(database)] == [
        "queued",
        "running",
        "cancelling",
        "cancelled",
    ]


def test_cancel_during_provider_submission_uses_the_reserved_job_handle(canvas_service) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Long provider edit"),
        expected_revision=0,
    )
    runtime = _ReservedSubmissionRuntime(database)

    async def scenario() -> tuple[dict, dict]:
        execution = asyncio.create_task(service.execute_as_creative_job(runtime, {
            "modality": "workflow",
            "operationKind": "canvas.graph.execute",
            "sessionId": "session-a",
            "graphId": saved["graph"]["graphId"],
            "graphRevision": saved["revision"],
            "canvasOperationId": "canvas-op-reserved-cancel",
            "targetNodeIds": ["result-node"],
        }))
        await runtime.submission_started.wait()
        active = service.get_graph(session_id="session-a")["runtime"]
        assert active["nodeStates"]["action-node"]["jobId"] == runtime.reserved_job_id
        cancelled = await service.cancel_run(
            runtime,
            session_id="session-a",
            graph_run_id=active["graphRunId"],
            reason="user_cancelled",
        )
        outer_job = await execution
        return cancelled, outer_job

    cancelled, outer_job = asyncio.run(scenario())
    assert cancelled["status"] == "cancelled"
    assert outer_job["status"] == "cancelled"
    assert runtime.cancelled_job_ids == [runtime.reserved_job_id]
    assert runtime.cleaned_job_ids == [runtime.reserved_job_id]
    recovered = service.get_graph(session_id="session-a")["runtime"]
    assert recovered["nodeStates"]["action-node"]["jobId"] == runtime.reserved_job_id
    assert recovered["nodeStates"]["action-node"]["providerCancellation"] == "completed"
    assert recovered["nodeStates"]["action-node"]["providerCleanup"] == "completed"


def test_graph_lifecycle_preserves_structured_provider_outcomes(canvas_service) -> None:
    service, _database = canvas_service

    class StructuredLifecycleRuntime:
        async def cancel_job(self, _job_id: str) -> dict:
            return {"status": "unsupported", "remoteTaskMayContinue": True}

        async def cleanup_job(self, _job_id: str) -> dict:
            return {"status": "failed", "remoteTaskMayContinue": True}

    lifecycle = asyncio.run(service._cancel_and_cleanup_job(StructuredLifecycleRuntime(), "provider-job"))

    assert lifecycle == {
        "providerCancellation": "unsupported",
        "providerCancellationDetailCode": "",
        "providerCancellationRemoteTaskMayContinue": True,
        "providerCancellationError": "",
        "providerCleanup": "failed",
        "providerCleanupDetailCode": "",
        "providerCleanupError": "",
    }


def test_parent_task_cancellation_cleans_provider_job_and_remains_cancelled(
    canvas_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Long provider edit"),
        expected_revision=0,
    )
    runtime = _PendingLifecycleRuntime(database)
    real_sleep = asyncio.sleep

    async def immediate_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(graph_module.asyncio, "sleep", immediate_sleep)

    async def scenario() -> None:
        execution = asyncio.create_task(service.execute_as_creative_job(runtime, {
            "modality": "workflow",
            "operationKind": "canvas.graph.execute",
            "sessionId": "session-a",
            "graphId": saved["graph"]["graphId"],
            "graphRevision": saved["revision"],
            "canvasOperationId": "canvas-op-parent-cancel",
            "targetNodeIds": ["result-node"],
        }))
        await runtime.poll_started.wait()
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(scenario())
    assert runtime.cancelled_job_ids == ["inner-pending"]
    assert runtime.cleaned_job_ids == ["inner-pending"]
    recovered = service.get_graph(session_id="session-a")["runtime"]
    assert recovered["status"] == "cancelled"
    assert recovered["nodeStates"]["action-node"]["errorCode"] == "parent_graph_cancelled"
    assert recovered["nodeStates"]["action-node"]["providerCancellation"] == "completed"
    assert recovered["nodeStates"]["action-node"]["providerCleanup"] == "completed"
    assert [event["payload"]["status"] for event in _canvas_graph_run_state_events(database)] == [
        "queued",
        "running",
        "cancelled",
    ]


def test_stale_retry_state_event_is_dropped_after_run_is_cancelled(canvas_service) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_chain_graph("source-a"),
        expected_revision=0,
    )
    runtime = _BranchRetryRuntime(database)
    request = {
        "modality": "workflow",
        "operationKind": "canvas.graph.execute",
        "sessionId": "session-a",
        "graphId": saved["graph"]["graphId"],
        "graphRevision": saved["revision"],
        "canvasOperationId": "canvas-op-stale-event",
        "targetNodeIds": ["result-b"],
    }
    failed = asyncio.run(service.execute_as_creative_job(runtime, request))
    graph_run_id = str(failed["canvasGraphRunId"])
    claim = service.claim_failed_retry(session_id="session-a", graph_run_id=graph_run_id)
    stale_row = dict(claim["run"])

    cancelled = asyncio.run(service.cancel_run(
        runtime,
        session_id="session-a",
        graph_run_id=graph_run_id,
        reason="user_cancelled",
    ))
    assert cancelled["status"] == "cancelled"
    assert service._emit_graph_run_state_event(
        row=stale_row,
        status="running",
        transition="retry_failed_branch",
        retry_of_graph_run_id=graph_run_id,
    ) is False
    assert [event["payload"]["status"] for event in _canvas_graph_run_state_events(database)] == [
        "queued",
        "running",
        "failed",
        "running",
        "cancelling",
        "cancelled",
    ]


def test_guarded_event_append_cannot_publish_retry_running_after_cancel(
    canvas_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_chain_graph("source-a"),
        expected_revision=0,
    )
    runtime = _BranchRetryRuntime(database)
    failed = asyncio.run(service.execute_as_creative_job(runtime, {
        "modality": "workflow",
        "operationKind": "canvas.graph.execute",
        "sessionId": "session-a",
        "graphId": saved["graph"]["graphId"],
        "graphRevision": saved["revision"],
        "canvasOperationId": "canvas-op-guarded-event",
        "targetNodeIds": ["result-b"],
    }))
    graph_run_id = str(failed["canvasGraphRunId"])
    append_started = Event()
    release_append = Event()
    original_append = database.add_runtime_event_if_current

    def delayed_append(event: dict, **guard) -> bool:
        payload = dict(event.get("payload") or {})
        if payload.get("transition") == "retry_failed_branch":
            append_started.set()
            assert release_append.wait(timeout=5)
        return original_append(event, **guard)

    monkeypatch.setattr(database, "add_runtime_event_if_current", delayed_append)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed_future = executor.submit(
            service.claim_failed_retry,
            session_id="session-a",
            graph_run_id=graph_run_id,
        )
        assert append_started.wait(timeout=5)
        cancelled_future = executor.submit(
            asyncio.run,
            service.cancel_run(
                runtime,
                session_id="session-a",
                graph_run_id=graph_run_id,
                reason="user_cancelled",
            ),
        )
        deadline = time.monotonic() + 5
        while service._run_status(graph_run_id=graph_run_id) != "cancelling":
            if time.monotonic() >= deadline:
                raise AssertionError("cancellation did not commit before the delayed event append")
            time.sleep(0.01)
        release_append.set()
        claim = claimed_future.result(timeout=5)
        cancelled = cancelled_future.result(timeout=5)

    assert claim["run"]["status"] == "running"
    assert cancelled["status"] == "cancelled"
    assert [event["payload"]["status"] for event in _canvas_graph_run_state_events(database)] == [
        "queued",
        "running",
        "failed",
        "cancelling",
        "cancelled",
    ]


def test_explicit_cancel_owns_slow_provider_lifecycle_and_outer_job_finishes(
    canvas_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Slow provider edit"),
        expected_revision=0,
    )
    runtime = _SlowCancelLifecycleRuntime(database)
    real_sleep = asyncio.sleep

    async def immediate_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(graph_module.asyncio, "sleep", immediate_sleep)

    async def scenario() -> tuple[dict, dict]:
        execution = asyncio.create_task(service.execute_as_creative_job(runtime, {
            "modality": "workflow",
            "operationKind": "canvas.graph.execute",
            "sessionId": "session-a",
            "graphId": saved["graph"]["graphId"],
            "graphRevision": saved["revision"],
            "canvasOperationId": "canvas-op-slow-cancel",
            "targetNodeIds": ["result-node"],
        }))
        await runtime.poll_started.wait()
        active = service.get_graph(session_id="session-a")["runtime"]
        cancellation = asyncio.create_task(service.cancel_run(
            runtime,
            session_id="session-a",
            graph_run_id=active["graphRunId"],
            reason="user_cancelled",
        ))
        await runtime.cancel_started.wait()
        outer_job = await asyncio.wait_for(execution, timeout=1)
        assert outer_job["status"] == "cancelled"
        runtime.release_cancel.set()
        cancelled = await cancellation
        return cancelled, outer_job

    cancelled, outer_job = asyncio.run(scenario())
    assert cancelled["status"] == "cancelled"
    assert outer_job["status"] == "cancelled"
    assert runtime.cancelled_job_ids == ["inner-pending"]
    assert runtime.cleaned_job_ids == ["inner-pending"]
    recovered = service.get_graph(session_id="session-a")["runtime"]
    assert recovered["status"] == "cancelled"
    assert recovered["nodeStates"]["action-node"]["providerCancellation"] == "completed"
    assert recovered["nodeStates"]["action-node"]["providerCleanup"] == "completed"


def test_provider_poll_failure_cleans_active_job_before_failed_retry(canvas_service, monkeypatch: pytest.MonkeyPatch) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_graph("source-a", prompt="Retry after provider poll failure"),
        expected_revision=0,
    )
    runtime = _PollFailureRuntime(database)
    real_sleep = asyncio.sleep

    async def immediate_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(graph_module.asyncio, "sleep", immediate_sleep)
    request = {
        "modality": "workflow",
        "operationKind": "canvas.graph.execute",
        "sessionId": "session-a",
        "graphId": saved["graph"]["graphId"],
        "graphRevision": saved["revision"],
        "canvasOperationId": "canvas-op-poll-failure",
        "targetNodeIds": ["result-node"],
    }

    failed = asyncio.run(service.execute_as_creative_job(runtime, request))
    assert failed["status"] == "failed"
    graph_run_id = str(failed["canvasGraphRunId"])
    run = service.get_run(session_id="session-a", graph_run_id=graph_run_id)
    state = run["nodeStates"]["action-node"]
    assert state["providerCancellation"] == "completed"
    assert state["providerCleanup"] == "completed"
    assert state["providerCancellationRemoteTaskMayContinue"] is False
    assert state["recoverable"] is True
    assert runtime.cancelled_job_ids == ["inner-poll-1"]
    assert runtime.cleaned_job_ids == ["inner-poll-1"]
    assert runtime.jobs["inner-poll-1"]["status"] == "cancelled"

    claim = service.claim_failed_retry(session_id="session-a", graph_run_id=graph_run_id)
    retried = asyncio.run(service.retry_failed_run(
        runtime,
        session_id="session-a",
        graph_run_id=graph_run_id,
        claim=claim,
    ))
    assert retried["status"] == "succeeded"
    assert runtime.counter == 2
    assert runtime.cancelled_job_ids == ["inner-poll-1"]
    assert runtime.cleaned_job_ids == ["inner-poll-1"]
    assert service.get_run(session_id="session-a", graph_run_id=graph_run_id)["status"] == "succeeded"


def test_startup_reconciliation_marks_orphaned_runs_interrupted_and_retryable(canvas_service) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_chain_graph("source-a"),
        expected_revision=0,
    )
    plan = service.execution_contract_summary(
        session_id="session-a",
        graph_id=saved["graph"]["graphId"],
        graph_revision=saved["revision"],
        target_node_ids=["result-b"],
    )
    states = {
        "action-a": {"state": "succeeded", "attempt": 1, "artifactId": "artifact-paid"},
        "action-b": {"state": "running", "attempt": 1, "jobId": "provider-orphan"},
    }
    with database.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO creative_canvas_graph_runs(
                graph_run_id, graph_id, session_id, canvas_operation_id, graph_revision,
                target_node_ids_json, plan_json, node_states_json, status, current_node_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', 'action-b', ?, ?)
            """,
            (
                "canvas-run-orphan",
                saved["graph"]["graphId"],
                "session-a",
                "canvas-op-orphan",
                saved["revision"],
                '["result-b"]',
                graph_module.json.dumps(plan),
                graph_module.json.dumps(states),
                "2026-08-05T00:00:00Z",
                "2026-08-05T00:00:00Z",
            ),
        )
        conn.commit()

    result = service.reconcile_startup()
    assert result == {"interruptedRuns": 1, "interruptedNodes": 1}
    run = service.get_run(session_id="session-a", graph_run_id="canvas-run-orphan")
    assert run["status"] == "interrupted"
    assert run["error"]["code"] == "engine_restart_provider_task_unknown"
    assert run["recovery"] == {
        "canRetry": False,
        "mode": None,
        "reason": "engine_restart_provider_task_unknown",
    }
    assert run["nodeStates"]["action-a"]["state"] == "succeeded"
    assert run["nodeStates"]["action-b"]["state"] == "interrupted"
    assert run["nodeStates"]["action-b"]["recoverable"] is False
    assert run["nodeStates"]["action-b"]["providerCancellationRemoteTaskMayContinue"] is True
    events = _canvas_graph_run_state_events(database)
    assert [event["payload"]["status"] for event in events] == ["interrupted"]
    assert events[0]["payload"]["recovery"] == {"canRetry": False, "mode": None}


def test_retry_failed_branch_reuses_run_operation_and_paid_ancestor(canvas_service) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_chain_graph("source-a"),
        expected_revision=0,
    )
    runtime = _BranchRetryRuntime(database)
    request = {
        "modality": "workflow",
        "operationKind": "canvas.graph.execute",
        "sessionId": "session-a",
        "projectId": "project-a",
        "workspaceId": "workspace-a",
        "workspacePath": "C:/workspace-a",
        "graphId": saved["graph"]["graphId"],
        "graphRevision": saved["revision"],
        "canvasOperationId": "canvas-op-branch",
        "targetNodeIds": ["result-b"],
    }

    failed = asyncio.run(service.execute_as_creative_job(runtime, request))
    assert failed["status"] == "failed"
    failed_run_id = failed["canvasGraphRunId"]

    rejected = asyncio.run(service.execute_as_creative_job(runtime, {
        **request,
        "retryGraphRunId": "canvas-run-not-this-operation",
    }))
    assert rejected["status"] == "failed"
    assert "not bound to the current session" in rejected["error"]
    assert runtime.node_attempts == {"action-a": 1, "action-b": 1}

    retried = asyncio.run(service.execute_as_creative_job(runtime, {
        **request,
        "retryGraphRunId": failed_run_id,
    }))
    assert retried["status"] == "succeeded"
    assert retried["canvasGraphRunId"] == failed_run_id
    assert retried["canvasOperationId"] == "canvas-op-branch"
    assert runtime.node_attempts == {"action-a": 1, "action-b": 2}
    assert [item["canvasOperationId"] for item in runtime.requests] == [
        "canvas-op-branch",
        "canvas-op-branch",
        "canvas-op-branch",
    ]
    assert runtime.requests[-1]["projectId"] == "project-a"
    assert runtime.requests[-1]["workspaceId"] == "workspace-a"
    assert Path(runtime.requests[-1]["workspacePath"]).name == "workspace"
    run = service.get_run(session_id="session-a", graph_run_id=failed_run_id)
    assert run["status"] == "succeeded"
    assert run["nodeStates"]["action-a"]["attempt"] == 1
    assert run["nodeStates"]["action-b"]["attempt"] == 2
    with pytest.raises(CreativeCanvasGraphError, match="current session"):
        service.get_run(session_id="session-b", graph_run_id=failed_run_id)
    recovered = service.get_graph(session_id="session-a")["runtime"]
    assert recovered["canvasOperationId"] == "canvas-op-branch"
    assert recovered["graphRevision"] == saved["revision"]
    assert recovered["targetNodeIds"] == ["result-b"]
    events = _canvas_graph_run_state_events(database)
    assert [event["payload"]["status"] for event in events] == [
        "queued",
        "running",
        "failed",
        "running",
        "completed",
    ]
    assert events[2]["payload"]["recovery"] == {"canRetry": True, "mode": "failed_branch"}
    assert events[3]["payload"]["transition"] == "retry_failed_branch"
    assert events[3]["payload"]["retryOfGraphRunId"] == failed_run_id


def test_failed_branch_retry_claim_is_atomic_across_service_instances(canvas_service) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_chain_graph("source-a"),
        expected_revision=0,
    )
    runtime = _BranchRetryRuntime(database)
    failed = asyncio.run(service.execute_as_creative_job(runtime, {
        "modality": "workflow",
        "operationKind": "canvas.graph.execute",
        "sessionId": "session-a",
        "graphId": saved["graph"]["graphId"],
        "graphRevision": saved["revision"],
        "canvasOperationId": "canvas-op-atomic-retry",
        "targetNodeIds": ["result-b"],
    }))
    assert failed["status"] == "failed"
    graph_run_id = str(failed["canvasGraphRunId"])
    contenders = [service, CreativeCanvasGraphService()]
    start = Barrier(len(contenders))

    def attempt_claim(candidate: CreativeCanvasGraphService) -> tuple[str, object]:
        start.wait()
        try:
            return "claimed", candidate.claim_failed_retry(
                session_id="session-a",
                graph_run_id=graph_run_id,
            )
        except CreativeCanvasGraphConflict as exc:
            return "conflict", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt_claim, contenders))

    assert sorted(outcome[0] for outcome in outcomes) == ["claimed", "conflict"]
    assert service.get_run(session_id="session-a", graph_run_id=graph_run_id)["status"] == "running"
    retry_events = [
        event for event in _canvas_graph_run_state_events(database)
        if event["payload"].get("transition") == "retry_failed_branch"
    ]
    assert len(retry_events) == 1
    assert retry_events[0]["payload"]["retryOfGraphRunId"] == graph_run_id
    assert runtime.node_attempts == {"action-a": 1, "action-b": 1}


def test_cancelled_retry_claim_never_submits_another_provider_job(canvas_service) -> None:
    service, database = canvas_service
    saved = service.save_graph(
        session_id="session-a",
        graph=_chain_graph("source-a"),
        expected_revision=0,
    )
    runtime = _BranchRetryRuntime(database)
    failed = asyncio.run(service.execute_as_creative_job(runtime, {
        "modality": "workflow",
        "operationKind": "canvas.graph.execute",
        "sessionId": "session-a",
        "graphId": saved["graph"]["graphId"],
        "graphRevision": saved["revision"],
        "canvasOperationId": "canvas-op-cancelled-claim",
        "targetNodeIds": ["result-b"],
    }))
    graph_run_id = str(failed["canvasGraphRunId"])
    request_count = len(runtime.requests)
    claim = service.claim_failed_retry(session_id="session-a", graph_run_id=graph_run_id)

    cancelled = asyncio.run(service.cancel_run(
        runtime,
        session_id="session-a",
        graph_run_id=graph_run_id,
    ))
    retried = asyncio.run(service.retry_failed_run(
        runtime,
        session_id="session-a",
        graph_run_id=graph_run_id,
        claim=claim,
    ))

    assert cancelled["status"] == "cancelled"
    assert retried["status"] == "cancelled"
    assert len(runtime.requests) == request_count
    assert runtime.node_attempts == {"action-a": 1, "action-b": 1}
    assert service.get_run(session_id="session-a", graph_run_id=graph_run_id)["status"] == "cancelled"


def test_engine_lifespan_awaits_canvas_graph_startup_reconciliation() -> None:
    main_path = Path(__file__).resolve().parents[2] / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
    }
    lifespan = functions["lifespan"]
    startup = functions["_start_lifespan_services"]
    lifespan_awaited_calls = {
        node.value.func.id
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    }
    startup_awaited_calls = {
        node.value.func.id
        for node in ast.walk(startup)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    }
    assert "_start_lifespan_services" in lifespan_awaited_calls
    assert "_reconcile_creative_canvas_graph_runs" in startup_awaited_calls


def test_canvas_graph_lifecycle_routes_bridge_session_scoped_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import creative_canvas_routes

    runtime = object()
    runtime_module = ModuleType("runtimes.creative_media.runtime")
    runtime_module.creative_media_runtime = runtime
    monkeypatch.setitem(sys.modules, "runtimes.creative_media.runtime", runtime_module)

    class RouteService:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def get_run(self, **kwargs):
            self.calls.append(("get", kwargs))
            return {"status": "failed", **kwargs}

        async def cancel_run(self, received_runtime, **kwargs):
            self.calls.append(("cancel", received_runtime, kwargs))
            return {"status": "cancelled", **kwargs}

        async def retry_failed_run(self, received_runtime, **kwargs):
            self.calls.append(("retry", received_runtime, kwargs))
            return {"status": "succeeded", "canvasGraphRunId": kwargs["graph_run_id"]}

        def prepare_direct_execution(self, **kwargs):
            self.calls.append(("prepare-direct", kwargs))
            return {"actions": [], "targetNodeIds": kwargs["target_node_ids"]}

        async def execute_as_creative_job(self, received_runtime, request):
            self.calls.append(("execute-direct", received_runtime, request))
            return {"status": "succeeded", "canvasGraphRunId": request["graphRunId"]}

        def claim_failed_retry(self, **kwargs):
            self.calls.append(("claim-retry", kwargs))
            return {
                "graphRevision": 1,
                "run": {"graph_run_id": kwargs["graph_run_id"]},
                "projectedRun": {"graphRunId": kwargs["graph_run_id"], "status": "running"},
            }

    route_service = RouteService()
    monkeypatch.setattr(creative_canvas_routes, "creative_canvas_graph_service", route_service)

    fetched = asyncio.run(creative_canvas_routes.get_canvas_graph_run("session-a", "run-a"))
    cancelled = asyncio.run(creative_canvas_routes.cancel_canvas_graph_run(
        "session-a",
        "run-a",
        {"reason": "user_cancelled"},
    ))
    started = asyncio.run(creative_canvas_routes.start_canvas_graph_run(
        "session-a",
        {
            "graphId": "graph-a",
            "graphRevision": 1,
            "targetNodeIds": ["result-a"],
            "canvasOperationId": "browser-must-not-control-operation-id",
        },
    ))
    retried = asyncio.run(creative_canvas_routes.retry_canvas_graph_failed_branch("session-a", "run-a"))

    assert fetched["session_id"] == "session-a"
    assert cancelled["status"] == "cancelled"
    assert started["accepted"] is True
    assert started["status"] == "queued"
    assert retried["accepted"] is True
    assert [call[0] for call in route_service.calls] == ["get", "cancel", "prepare-direct", "execute-direct", "claim-retry", "retry"]
    assert route_service.calls[1][1] is runtime
    assert route_service.calls[1][2] == {
        "session_id": "session-a",
        "graph_run_id": "run-a",
        "reason": "user_cancelled",
    }
    assert route_service.calls[2][1] == {
        "session_id": "session-a",
        "graph_id": "graph-a",
        "graph_revision": 1,
        "target_node_ids": ["result-a"],
    }
    assert route_service.calls[3][1] is runtime
    assert route_service.calls[3][2]["sessionId"] == "session-a"
    assert route_service.calls[3][2]["graphId"] == "graph-a"
    assert route_service.calls[3][2]["canvasOperationId"].startswith("canvas-operation-")
    assert route_service.calls[3][2]["canvasOperationId"] != "browser-must-not-control-operation-id"
    assert route_service.calls[3][2]["graphRunId"].startswith("canvas-run-")
    assert route_service.calls[4][1] == {"session_id": "session-a", "graph_run_id": "run-a"}
    assert route_service.calls[5][1] is runtime
    assert route_service.calls[5][2]["session_id"] == "session-a"
    assert route_service.calls[5][2]["graph_run_id"] == "run-a"
    assert route_service.calls[5][2]["claim"]["projectedRun"]["status"] == "running"


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
