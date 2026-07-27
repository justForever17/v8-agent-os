from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from api.knowledge_routes import add_graph_relation
from api.models import GraphRelationPayload
from core.knowledge_db import knowledge_db
from core.storage import storage
from core.workspace_resolution import workspace_resolution_service
from runtimes.memory import memory_runtime
from runtimes.memory.knowledge_service import KnowledgeService
from runtimes.memory.project_registry import project_registry_service


def _project(project_id: str, name: str, workspace_path: Path):
    return SimpleNamespace(
        active=True,
        project_id=project_id,
        workspace_id=project_id,
        workspace_path=str(workspace_path),
        name=name,
    )


def test_graph_catalog_projects_internal_scopes_as_isolated_workspaces():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        main_workspace = root / "main"
        other_workspace = root / "other"
        empty_workspace = root / "empty"
        missing_workspace = root / "missing"
        main_workspace.mkdir()
        other_workspace.mkdir()
        empty_workspace.mkdir()
        projects = [
            _project("main", "Main workspace", main_workspace),
            _project("other", "Other workspace", other_workspace),
            _project("empty", "Empty workspace", empty_workspace),
            _project("missing", "Missing workspace", missing_workspace),
        ]
        raw_scopes = [
            {"scope": "global", "relationCount": 2, "workspaceRoots": []},
            {"scope": "project:main", "relationCount": 1, "workspaceRoots": [str(main_workspace)]},
            {"scope": "workspace:main", "relationCount": 1, "workspaceRoots": [str(main_workspace)]},
            {"scope": "project:other", "relationCount": 3, "workspaceRoots": [str(other_workspace)]},
            {"scope": "project:missing", "relationCount": 9, "workspaceRoots": [str(missing_workspace)]},
        ]

        with (
            patch.object(knowledge_db, "list_graph_scopes", return_value=raw_scopes),
            patch.object(project_registry_service, "list_projects", return_value=projects),
            patch.object(project_registry_service, "list_workspace_presentations", return_value=[]),
            patch.object(storage, "get_workspace_config", return_value={
                "agent_workspace_path": str(main_workspace),
                "projectId": "main",
                "workspaceId": "main",
            }),
            patch.object(workspace_resolution_service, "get_main_workspace_path", return_value=str(main_workspace)),
        ):
            catalog = KnowledgeService().list_graph_workspaces()

    assert catalog["defaultWorkspaceKey"]
    assert [item["label"] for item in catalog["items"]] == ["Main workspace", "Empty workspace", "Other workspace"]
    assert [item["relationCount"] for item in catalog["items"]] == [4, 2, 5]
    assert [item["workspacePath"] for item in catalog["items"]] == [
        str(main_workspace),
        str(empty_workspace),
        str(other_workspace),
    ]
    assert catalog["items"][0]["isDefault"] is True
    assert all("scope" not in item and "writeScope" not in item for item in catalog["items"])


def test_graph_catalog_uses_default_workspace_even_when_only_global_knowledge_exists():
    with tempfile.TemporaryDirectory() as temp_dir:
        main_workspace = Path(temp_dir) / "main"
        main_workspace.mkdir()
        project = _project("main", "Default workspace", main_workspace)

        with (
            patch.object(
                knowledge_db,
                "list_graph_scopes",
                return_value=[{"scope": "global", "relationCount": 3, "workspaceRoots": []}],
            ),
            patch.object(project_registry_service, "list_projects", return_value=[project]),
            patch.object(project_registry_service, "list_workspace_presentations", return_value=[]),
            patch.object(storage, "get_workspace_config", return_value={
                "agent_workspace_path": str(main_workspace),
                "projectId": "main",
                "workspaceId": "main",
            }),
            patch.object(workspace_resolution_service, "get_main_workspace_path", return_value=str(main_workspace)),
        ):
            catalog = KnowledgeService().list_graph_workspaces()

    assert len(catalog["items"]) == 1
    assert catalog["items"][0]["label"] == "Default workspace"
    assert catalog["items"][0]["relationCount"] == 3


def test_admin_workspace_relation_uses_governed_manual_evidence_ref():
    recorded: dict = {}

    with (
        patch.object(memory_runtime, "get_graph_workspace_write_scope", return_value="workspace:ws_demo"),
        patch.object(memory_runtime, "add_relation", side_effect=lambda **kwargs: recorded.update(kwargs)),
    ):
        result = asyncio.run(
            add_graph_relation(
                GraphRelationPayload(
                    subject="supervisor",
                    predicate="USES",
                    object="memory",
                    workspaceKey="ws_demo",
                    maintainerSource="human_admin",
                )
            )
        )

    assert result["created"] is True
    assert recorded["scope"] == "workspace:ws_demo"
    assert recorded["source_fact_ids"] == []
    assert recorded["evidence_refs"] == ["admin://memory/graph/ws_demo"]
