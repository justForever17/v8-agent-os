from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import core.creative_canvas_graph as graph_module
from core.creative_canvas_graph import CreativeCanvasGraphService
from core.database import DatabaseManager


MAX_GRAPH_SAVE_BUDGET_MS = 2500.0
MAX_GRAPH_PREFLIGHT_BUDGET_MS = 750.0


def _maximum_action_graph() -> tuple[dict, list[str]]:
    nodes: list[dict] = [{
        "nodeId": "source-shared",
        "kind": "resource",
        "origin": "source",
        "resourceId": "source-performance",
        "mediaType": "image",
        "title": "Shared source",
        "x": 0,
        "y": 0,
        "width": 280,
        "height": 190,
    }]
    edges: list[dict] = []
    targets: list[str] = []
    for index in range(79):
        action_id = f"action-{index}"
        result_id = f"result-{index}"
        x = (index % 8) * 760 + 340
        y = (index // 8) * 280
        nodes.extend([
            {
                "nodeId": action_id,
                "kind": "action",
                "actionDefinitionId": "creative_media.edit_image",
                "prompt": "Preserve composition",
                "parameters": {},
                "configurationRevision": 1,
                "title": f"Action {index}",
                "x": x,
                "y": y,
                "width": 280,
                "height": 190,
            },
            {
                "nodeId": result_id,
                "kind": "result",
                "producerActionNodeId": action_id,
                "outputSlot": "image_derivative",
                "mediaType": "image",
                "title": f"Result {index}",
                "x": x + 340,
                "y": y,
                "width": 280,
                "height": 190,
            },
        ])
        edges.extend([
            {
                "edgeId": f"input-action-{index}",
                "from": "source-shared",
                "to": action_id,
                "fromPortId": "output",
                "toPortId": "image",
                "dataType": "image",
                "role": "data",
                "order": 0,
            },
            {
                "edgeId": f"action-result-{index}",
                "from": action_id,
                "to": result_id,
                "fromPortId": "output",
                "toPortId": "input",
                "dataType": "image",
                "role": "data",
                "order": 0,
            },
        ])
        targets.append(result_id)
    return {
        "schema": "v8.creative_canvas_graph.v1",
        "version": 3,
        "graphId": "canvas-performance-max",
        "nodes": nodes,
        "edges": edges,
        "viewport": {"x": 24, "y": 24, "scale": 1},
    }, targets


def test_maximum_canvas_graph_save_and_preflight_baseline(tmp_path: Path, monkeypatch):
    database = DatabaseManager(tmp_path / "state.db")
    database.create_or_update_session("session-performance", "Performance", user_id="user")
    database.add_session_source(
        source_id="source-performance",
        session_id="session-performance",
        source_kind="web_upload",
        mime_type="image/png",
        title="source.png",
        workspace_path=".v8/uploads/source.png",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(graph_module, "db", database)
    monkeypatch.setattr(
        graph_module.workspace_authority_service,
        "resolve",
        lambda **_kwargs: SimpleNamespace(
            workspace_root=str(workspace),
            workspace_id="workspace-performance",
            project_id="project-performance",
            side_effects_allowed=True,
        ),
    )
    graph, targets = _maximum_action_graph()
    service = CreativeCanvasGraphService()

    save_started = time.perf_counter()
    saved = service.save_graph(
        session_id="session-performance",
        graph=graph,
        expected_revision=0,
    )
    save_ms = (time.perf_counter() - save_started) * 1000

    preflight_started = time.perf_counter()
    preflight = service.preflight_execution(
        session_id="session-performance",
        graph_id=saved["graph"]["graphId"],
        graph_revision=saved["revision"],
        target_node_ids=targets,
        available_operation_kinds={"image.edit"},
    )
    preflight_ms = (time.perf_counter() - preflight_started) * 1000

    metrics = {
        "schema": "v8.creative_canvas.performance.v1",
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "actions": len(targets),
        "saveMs": round(save_ms, 3),
        "preflightMs": round(preflight_ms, 3),
        "saveBudgetMs": MAX_GRAPH_SAVE_BUDGET_MS,
        "preflightBudgetMs": MAX_GRAPH_PREFLIGHT_BUDGET_MS,
    }
    print(json.dumps(metrics, sort_keys=True))
    assert len(graph["nodes"]) == 159
    assert len(preflight["plan"]["actions"]) == 79
    assert preflight["valid"] is True
    assert save_ms <= MAX_GRAPH_SAVE_BUDGET_MS
    assert preflight_ms <= MAX_GRAPH_PREFLIGHT_BUDGET_MS
