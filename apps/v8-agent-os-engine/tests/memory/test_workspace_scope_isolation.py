from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.knowledge_db import KnowledgeDB
from core.memory_store import MemoryStore
from core.storage import storage
from runtimes.memory.models import ProjectDescriptor
from runtimes.memory.scope_resolution import ScopeResolutionService, build_scope_chain
from runtimes.memory.workspace_scope import canonical_workspace_scope, expand_workspace_scope_chain
from runtimes.memory.workflow_service import WORKFLOW_MEMORY_DEFAULTS, WorkflowMemoryService
from runtimes.engineering.service import EngineeringLaneService


class _Registry:
    def __init__(self, projects):
        self.projects = list(projects)

    def list_projects(self):
        return list(self.projects)

    def list_workspace_presentations(self):
        return []

    def get_project(self, project_id):
        return next((item for item in self.projects if item.project_id == project_id), None)

    def find_project_for_workspace(self, *, workspace_id=None, workspace_path=None):
        path = str(workspace_path or "").replace("\\", "/").rstrip("/").lower()
        return next(
            (
                item
                for item in self.projects
                if (workspace_id and item.workspace_id == workspace_id)
                or (path and str(item.workspace_path or "").replace("\\", "/").rstrip("/").lower() == path)
            ),
            None,
        )

    def find_project_for_workflow(self, workflow_id):
        return None

    def find_project_for_channel(self, channel_type, remote_id):
        return None


def _project(project_id: str, workspace_id: str, name: str, path: Path) -> ProjectDescriptor:
    return ProjectDescriptor(
        id=project_id,
        name=name,
        workspaceId=workspace_id,
        workspacePath=str(path),
    ).normalized()


def test_workspace_chain_uses_one_physical_workspace_and_shared_global():
    root = Path(__file__).resolve().parent / ".scope-isolation-fixture"
    alpha = root / "alpha"
    beta = root / "beta"
    alpha.mkdir(parents=True, exist_ok=True)
    beta.mkdir(parents=True, exist_ok=True)
    try:
        projects = [
            _project("alpha", "alpha-workspace", "Alpha", alpha),
            _project("alpha-alias", "alpha-alias-workspace", "Alpha alias", alpha),
            _project("beta", "beta-workspace", "Beta", beta),
        ]
        registry = _Registry(projects)
        with patch.object(storage, "get_workspace_config", return_value={"agent_workspace_path": str(alpha)}):
            chain = build_scope_chain(
                resolved_scope="project:beta",
                workspace_path=str(alpha),
                project_id="beta",
                workspace_id="beta-workspace",
                channel_type="web",
                channel_remote_id="shared-channel",
                project_registry=registry,
            )

        assert "global" in chain
        assert canonical_workspace_scope(str(alpha)) in chain
        assert "project:alpha" not in chain
        assert "project:alpha-alias" not in chain
        assert "workspace:alpha-workspace" not in chain
        assert "project:beta" not in chain
        assert "workspace:beta-workspace" not in chain
        assert "channel:web:shared-channel" not in chain
        assert "workspace:main" not in chain
    finally:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        if root.exists():
            root.rmdir()


def test_missing_physical_workspace_cannot_recall_stale_aliases():
    missing = Path(__file__).resolve().parent / ".missing-scope-workspace"
    project = _project("gone", "gone-workspace", "Gone", missing)
    registry = _Registry([project])
    with patch.object(storage, "get_workspace_config", return_value={"agent_workspace_path": str(missing)}):
        chain = expand_workspace_scope_chain(
            resolved_scope="project:gone",
            workspace_path=str(missing),
            project_id="gone",
            workspace_id="gone-workspace",
            project_registry=registry,
        )
    assert chain == ["global"]


def test_unproven_alias_and_channel_metadata_cannot_expand_global_recall():
    registry = _Registry([])
    chain = build_scope_chain(
        resolved_scope="project:other-workspace",
        project_id="other-workspace",
        workspace_id="other-workspace-id",
        channel_type="phone",
        channel_remote_id="shared-device",
        project_registry=registry,
    )

    assert chain == ["global"]


def test_engineering_descriptor_uses_physical_workspace_not_stale_ids():
    root = Path(__file__).resolve().parent / ".engineering-scope-fixture"
    alpha = root / "alpha"
    alpha.mkdir(parents=True, exist_ok=True)
    try:
        service = object.__new__(EngineeringLaneService)
        chain = service._scope_chain_for_descriptor(
            {
                "workspaceRoot": str(alpha),
                "projectId": "beta-project",
                "workspaceId": "beta-workspace",
            }
        )
        assert "global" in chain
        assert canonical_workspace_scope(str(alpha)) in chain
        assert "project:beta-project" not in chain
        assert "workspace:beta-workspace" not in chain
    finally:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        if root.exists():
            root.rmdir()


def test_scope_resolution_canonicalizes_stale_ids_to_path_owner():
    root = Path(__file__).resolve().parent / ".scope-canonicalization-fixture"
    alpha = root / "alpha"
    beta = root / "beta"
    alpha.mkdir(parents=True, exist_ok=True)
    beta.mkdir(parents=True, exist_ok=True)
    try:
        alpha_project = _project("alpha", "alpha-workspace", "Alpha", alpha)
        beta_project = _project("beta", "beta-workspace", "Beta", beta)
        service = ScopeResolutionService(
            project_registry=_Registry([alpha_project, beta_project]),
            binding_service=SimpleNamespace(get_binding=lambda session_id: None, upsert_binding=lambda binding: binding),
            resolution_repo=SimpleNamespace(append_event=lambda event: None),
        )
        with patch.object(storage, "get_workspace_config", return_value={"agent_workspace_path": str(alpha)}):
            result = service.resolve(
                session_id="scope-canonicalization",
                workspace_path=str(alpha),
                project_id="beta",
                workspace_id="beta-workspace",
                scope_hint="project:beta",
            )
        assert result.binding.project_id == "alpha"
        assert result.binding.workspace_id == "alpha-workspace"
        assert Path(result.binding.workspace_path) == alpha.resolve()
        assert "project:beta" not in result.scope_chain
    finally:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        if root.exists():
            root.rmdir()


def test_knowledge_queries_never_use_unspecified_scopes_and_keep_global():
    store = object.__new__(MemoryStore)
    calls = []

    class _KnowledgeProjection:
        def get_all_knowledge(self, *, scope=None, limit=20):
            calls.append(scope)
            return [{"id": scope, "fact": scope, "scope": scope}]

        def fts_search(self, query, *, scope=None, limit=20):
            calls.append(scope)
            return [{"id": scope, "fact": scope, "scope": scope}]

    with patch("core.knowledge_db.knowledge_db", _KnowledgeProjection()):
        results = store.query_knowledge(scopes=["global", "project:alpha"], limit=10)
        all_results = store.query_knowledge(query="anything", scopes=None, limit=10)

    assert calls[:2] == ["project:alpha", "global"]
    assert {item["scope"] for item in results} == {"project:alpha", "global"}
    assert calls[2:] == ["global"]
    assert {item["scope"] for item in all_results} == {"global"}


def test_graph_scope_filter_keeps_alpha_and_global_out_of_beta():
    db_path = Path(__file__).with_name(".scope-isolation-graph.sqlite")
    if db_path.exists():
        db_path.unlink()
    graph_db = KnowledgeDB(db_path)
    try:
        graph_db.add_knowledge("fact-global", "global fact", scope="global")
        graph_db.add_knowledge("fact-alpha", "alpha fact", scope="project:alpha")
        graph_db.add_knowledge("fact-beta", "beta fact", scope="project:beta")
        graph_db.add_scoped_relation("shared", "USES", "global-tool", scope="global", source_fact_ids=["fact-global"])
        graph_db.add_scoped_relation("shared", "USES", "alpha-tool", scope="project:alpha", source_fact_ids=["fact-alpha"])
        graph_db.add_scoped_relation("shared", "USES", "beta-tool", scope="project:beta", source_fact_ids=["fact-beta"])

        relations = graph_db.query_entity("shared", scopes=["global", "project:alpha"])
        returned_scopes = {item["scope"] for item in relations}
        returned_objects = {item["object"] for item in relations}
    finally:
        graph_db.db_path.unlink(missing_ok=True)

    assert returned_scopes == {"global", "project:alpha"}
    assert "beta-tool" not in returned_objects


def test_workflow_hints_keep_same_workspace_and_global_only():
    service = WorkflowMemoryService()

    def candidate(candidate_id: str, scope: str):
        return {
            "id": candidate_id,
            "status": "active_hint",
            "scope": scope,
            "workflowClass": "general",
            "task_family": "deploy release workflow",
            "canonicalTriggerPatterns": ["deploy release"],
            "firstActionTriggers": ["deploy"],
            "goldenPathSteps": ["deploy"],
            "antiPatterns": [],
            "verificationSteps": [],
            "maturity_score": 0.8,
            "confidence": 0.9,
            "metadata": {},
        }

    with (
        patch(
            "runtimes.memory.workflow_service.workflow_memory_config",
            return_value={**WORKFLOW_MEMORY_DEFAULTS, "maxInjectedHints": 5},
        ),
        patch.object(
            service,
            "list_candidates",
            return_value=[
                candidate("alpha", "project:alpha"),
                candidate("beta", "project:beta"),
                candidate("shared", "global"),
            ],
        ),
    ):
        hints = service.match_hints(
            query="deploy release now",
            scope_chain=["global", "project:alpha"],
            limit=5,
        )

    assert {item["id"] for item in hints} == {"alpha", "shared"}
