from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.workspace_share import resolve_workspace_file_to_share
from erc.runtime_context import bind_runtime_context


class ShareWorkspaceFileToolTests(unittest.TestCase):
    def test_share_main_workspace_relative_file_returns_short_surface(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "docs" / "demo.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"%PDF-1.4\n")
            descriptor = {
                "workspaceRoot": str(root),
                "workspaceId": None,
                "projectId": None,
            }
            with patch("core.workspace_share.workspace_resolution_service.resolve_workspace_descriptor", return_value=descriptor), patch(
                "core.workspace_share.workspace_resolution_service.get_main_workspace_path",
                return_value=str(root),
            ), bind_runtime_context(runtime_kind="chat", session_id="sess_demo"):
                result = resolve_workspace_file_to_share("docs/demo.pdf", "auto")
            self.assertTrue(result["ok"])
            self.assertEqual(result["filename"], "demo.pdf")
            self.assertEqual(result["viewerKind"], "pdf")
            self.assertNotIn("resourceRef", result)
            self.assertEqual(result["workspaceRelativePath"], "docs/demo.pdf")
            self.assertIn("/api/client/workspace/resource?", result["url"])
            self.assertIn("path_plane=workspace_artifact", result["url"])

    def test_share_project_workspace_absolute_file_keeps_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as main_dir:
            root = Path(temp_dir)
            target = root / "models" / "demo.glb"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"glTF")
            descriptor = {
                "workspaceRoot": str(root),
                "workspaceId": "ws_demo",
                "projectId": "proj_demo",
            }
            with patch("core.workspace_share.workspace_resolution_service.resolve_workspace_descriptor", return_value=descriptor), patch(
                "core.workspace_share.workspace_resolution_service.get_main_workspace_path",
                return_value=str(main_dir),
            ), bind_runtime_context(runtime_kind="chat", session_id="sess_demo", workspace_id="ws_demo", project_id="proj_demo"):
                result = resolve_workspace_file_to_share(str(target), "preview")
            self.assertEqual(result["viewerKind"], "model")
            self.assertEqual(result["workspaceId"], "ws_demo")
            self.assertEqual(result["projectId"], "proj_demo")
            self.assertNotIn("resourceRef", result)
            self.assertIn("workspace_id=ws_demo", result["url"])
            self.assertIn("project_id=proj_demo", result["url"])

    def test_share_download_file_uses_workspace_download_plane(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "notes.md"
            target.write_text("# Notes\n", encoding="utf-8")
            descriptor = {
                "workspaceRoot": str(root),
                "workspaceId": None,
                "projectId": None,
            }
            with patch("core.workspace_share.workspace_resolution_service.resolve_workspace_descriptor", return_value=descriptor), patch(
                "core.workspace_share.workspace_resolution_service.get_main_workspace_path",
                return_value=str(root),
            ), bind_runtime_context(runtime_kind="chat", session_id="sess_demo"):
                result = resolve_workspace_file_to_share("notes.md", "download")
            self.assertTrue(result["ok"])
            self.assertEqual(result["filename"], "notes.md")
            self.assertEqual(result["viewerKind"], "download")
            self.assertFalse(result["previewable"])
            self.assertNotIn("resourceRef", result)
            self.assertIn("path_plane=workspace_download", result["url"])

    def test_share_workspace_file_rejects_outside_path(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside_dir:
            root = Path(temp_dir)
            target = Path(outside_dir) / "secret.txt"
            target.write_text("nope", encoding="utf-8")
            descriptor = {
                "workspaceRoot": str(root),
                "workspaceId": None,
                "projectId": None,
            }
            with patch("core.workspace_share.workspace_resolution_service.resolve_workspace_descriptor", return_value=descriptor), patch(
                "core.workspace_share.workspace_resolution_service.get_main_workspace_path",
                return_value=str(root),
            ), bind_runtime_context(runtime_kind="chat", session_id="sess_demo"):
                with self.assertRaises(PermissionError):
                    resolve_workspace_file_to_share(str(target), "auto")


if __name__ == "__main__":
    unittest.main()
