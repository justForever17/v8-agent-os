from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.scoped_workspace_resource import (
    build_client_workspace_resource_admin_path,
    normalize_workspace_relative_path,
    resolve_scoped_workspace_resource,
)


class _FakeProject:
    def __init__(self, *, project_id: str, workspace_id: str, workspace_path: str, workspace_trust_state: str = "trusted"):
        self.project_id = project_id
        self.workspace_id = workspace_id
        self.workspace_path = workspace_path
        self.workspace_trust_state = workspace_trust_state


class ScopedWorkspaceResourceTests(unittest.TestCase):
    def test_normalize_workspace_relative_path_rejects_absolute_and_parent_segments(self):
        with self.assertRaises(ValueError):
            normalize_workspace_relative_path(r"C:\temp\foo.png")
        with self.assertRaises(ValueError):
            normalize_workspace_relative_path("../foo.png")
        self.assertEqual(normalize_workspace_relative_path(r"downloads\foo.png"), "downloads/foo.png")

    def test_resolve_project_workspace_from_explicit_workspace_binding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "downloads" / "asset.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"ok")
            project = _FakeProject(
                project_id="proj_demo",
                workspace_id="ws_demo",
                workspace_path=str(root),
            )
            with patch(
                "core.scoped_workspace_resource.project_registry_service.find_project_for_workspace",
                return_value=project,
            ):
                resolved = resolve_scoped_workspace_resource(
                    workspace_relative_path="downloads/asset.png",
                    path_plane="workspace_download",
                    workspace_id="ws_demo",
                )
            self.assertEqual(resolved.project_id, "proj_demo")
            self.assertEqual(resolved.workspace_id, "ws_demo")
            self.assertEqual(resolved.absolute_path, target.resolve())

    def test_resolve_main_workspace_when_no_scope_is_provided(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "artifacts" / "foo.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"ok")
            with patch(
                "core.scoped_workspace_resource.workspace_resolution_service.get_main_workspace_path",
                return_value=str(root),
            ):
                resolved = resolve_scoped_workspace_resource(
                    workspace_relative_path="artifacts/foo.jpg",
                    path_plane="workspace_artifact",
                )
            self.assertEqual(resolved.absolute_path, target.resolve())
            self.assertIsNone(resolved.project_id)
            self.assertIsNone(resolved.workspace_id)

    def test_resolve_scoped_workspace_resource_fail_closed_on_mismatched_binding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = _FakeProject(
                project_id="proj_actual",
                workspace_id="ws_demo",
                workspace_path=temp_dir,
            )
            with patch(
                "core.scoped_workspace_resource.project_registry_service.find_project_for_workspace",
                return_value=project,
            ):
                with self.assertRaises(PermissionError):
                    resolve_scoped_workspace_resource(
                        workspace_relative_path="foo.txt",
                        path_plane="workspace_download",
                        workspace_id="ws_demo",
                        project_id="proj_other",
                    )

    def test_resolve_scoped_workspace_resource_blocks_restricted_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = _FakeProject(
                project_id="proj_restricted",
                workspace_id="ws_restricted",
                workspace_path=temp_dir,
                workspace_trust_state="restricted",
            )
            with patch(
                "core.scoped_workspace_resource.project_registry_service.find_project_for_workspace",
                return_value=project,
            ):
                with self.assertRaises(PermissionError):
                    resolve_scoped_workspace_resource(
                        workspace_relative_path="foo.txt",
                        path_plane="workspace_download",
                        workspace_id="ws_restricted",
                    )

    def test_build_client_workspace_resource_admin_path_includes_scope(self):
        self.assertEqual(
            build_client_workspace_resource_admin_path(
                workspace_relative_path="downloads/foo.mp4",
                path_plane="workspace_download",
                workspace_id="ws_demo",
                project_id="proj_demo",
            ),
            "/api/client/workspace/resource?workspace_relative_path=downloads%2Ffoo.mp4&path_plane=workspace_download&workspace_id=ws_demo&project_id=proj_demo",
        )


if __name__ == "__main__":
    unittest.main()
