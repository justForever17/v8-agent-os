from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.memory_store import MemoryStore
from core.storage import storage
from runtimes.memory.project_registry import project_registry_service


def _store_without_disk_initialization() -> MemoryStore:
    return object.__new__(MemoryStore)


def test_project_signatures_resolve_their_own_workspace_roots():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        alpha = root / "alpha"
        beta = root / "beta"
        alpha.mkdir()
        beta.mkdir()
        (alpha / "AGENTS.md").write_text("alpha rules", encoding="utf-8")
        (beta / "AGENTS.md").write_text("beta rules", encoding="utf-8")

        def get_project(project_id: str):
            path = alpha if project_id == "alpha" else beta if project_id == "beta" else None
            return SimpleNamespace(workspace_path=str(path)) if path else None

        store = _store_without_disk_initialization()
        with patch("erc.runtime_context.get_runtime_context", return_value={}), patch.object(
            project_registry_service,
            "get_project",
            side_effect=get_project,
        ):
            alpha_signature = store._soft_signature_for_scope("project:alpha")
            beta_signature = store._soft_signature_for_scope("project:beta")

    assert Path(alpha_signature["workspaceRoot"]) == alpha.resolve()
    assert Path(beta_signature["workspaceRoot"]) == beta.resolve()
    assert alpha_signature["agentsHash"] != beta_signature["agentsHash"]
    assert alpha_signature["repoSignature"] != beta_signature["repoSignature"]
    assert alpha_signature["resolution"] == "project_registry"
    assert beta_signature["resolution"] == "project_registry"


def test_unknown_project_scope_never_falls_back_to_main_workspace():
    with tempfile.TemporaryDirectory() as temp_dir:
        main = Path(temp_dir) / "main"
        main.mkdir()
        (main / "AGENTS.md").write_text("main rules", encoding="utf-8")
        store = _store_without_disk_initialization()
        with patch("erc.runtime_context.get_runtime_context", return_value={}), patch.object(
            project_registry_service,
            "get_project",
            return_value=None,
        ), patch.object(
            storage,
            "get_workspace_config",
            return_value={"agent_workspace_path": str(main)},
        ):
            signature = store._soft_signature_for_scope("project:missing")

    assert signature["resolution"] == "unresolved_scope"
    assert signature["workspaceRoot"] == ""
    assert signature["agentsHash"] == ""
    assert signature["repoSignature"] == ""


def test_project_scope_never_borrows_a_different_runtime_workspace():
    with tempfile.TemporaryDirectory() as temp_dir:
        other = Path(temp_dir) / "other"
        other.mkdir()
        (other / "AGENTS.md").write_text("other rules", encoding="utf-8")
        store = _store_without_disk_initialization()
        with patch(
            "erc.runtime_context.get_runtime_context",
            return_value={
                "workspace_path": str(other),
                "project_id": "other",
                "resolved_scope": "project:other",
            },
        ), patch.object(project_registry_service, "get_project", return_value=None):
            signature = store._soft_signature_for_scope("project:missing")

    assert signature["resolution"] == "unresolved_scope"
    assert signature["workspaceRoot"] == ""
    assert signature["repoSignature"] == ""


def test_persisted_workspace_root_is_authoritative_for_revalidation():
    with tempfile.TemporaryDirectory() as temp_dir:
        persisted = Path(temp_dir) / "persisted"
        registry = Path(temp_dir) / "registry"
        persisted.mkdir()
        registry.mkdir()
        (persisted / "AGENTS.md").write_text("persisted rules", encoding="utf-8")
        (registry / "AGENTS.md").write_text("registry rules", encoding="utf-8")
        store = _store_without_disk_initialization()
        with patch("erc.runtime_context.get_runtime_context", return_value={}), patch.object(
            project_registry_service,
            "get_project",
            return_value=SimpleNamespace(workspace_path=str(registry)),
        ):
            signature = store._soft_signature_for_scope(
                "project:demo",
                metadata={"workspaceRoot": str(persisted)},
            )

    assert Path(signature["workspaceRoot"]) == persisted.resolve()
    assert signature["resolution"] == "fact_metadata"


def test_signature_refresh_revalidates_each_workspace_with_its_own_signature():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        alpha = root / "alpha"
        beta = root / "beta"
        alpha.mkdir()
        beta.mkdir()
        (alpha / "AGENTS.md").write_text("alpha rules", encoding="utf-8")
        (beta / "AGENTS.md").write_text("beta rules", encoding="utf-8")

        def get_project(project_id: str):
            path = alpha if project_id == "alpha" else beta if project_id == "beta" else None
            return SimpleNamespace(workspace_path=str(path)) if path else None

        store = _store_without_disk_initialization()
        with patch("erc.runtime_context.get_runtime_context", return_value={}), patch.object(
            project_registry_service,
            "list_projects",
            return_value=[],
        ), patch.object(
            project_registry_service,
            "list_workspace_presentations",
            return_value=[],
        ), patch.object(
            project_registry_service,
            "get_project",
            side_effect=get_project,
        ), patch(
            "core.knowledge_db.knowledge_db.mark_stale_for_signature_mismatch",
            side_effect=[2, 3],
        ) as mark_stale:
            result = store._mark_stale_for_signature_mismatch(["project:alpha", "project:beta"])

    assert result["staleMarked"] == 5
    assert [item["scope"] for item in result["resolvedScopes"]] == ["project:alpha", "project:beta"]
    calls = mark_stale.call_args_list
    assert [call.kwargs["scopes"] for call in calls] == [["project:alpha"], ["project:beta"]]
    assert calls[0].kwargs["agents_hash"] != calls[1].kwargs["agents_hash"]
    assert calls[0].kwargs["repo_signature"] != calls[1].kwargs["repo_signature"]
