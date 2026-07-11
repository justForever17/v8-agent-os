from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.artifact_store import artifact_store
from core.workspace_capability import WorkspaceBinding
from core.workspace_share import resolve_workspace_file_to_share
from erc.runtime_context import bind_runtime_context


def _trusted_binding(
    workspace_root: Path,
    *,
    main_workspace_root: Path | None = None,
    workspace_id: str = "",
    project_id: str = "",
) -> WorkspaceBinding:
    main_root = (main_workspace_root or workspace_root).resolve()
    active_root = workspace_root.resolve()
    scoped = active_root != main_root
    return WorkspaceBinding(
        runtime_kind="chat",
        workspace_id=workspace_id,
        project_id=project_id,
        active_workspace_root=active_root,
        main_workspace_root=main_root,
        source="project_registry" if scoped else "main_workspace",
        uses_scoped_workspace=scoped,
        is_scoped_override=scoped,
        trust_state="trusted",
        trust_source="test_explicit_trust",
        side_effects_allowed=True,
    )


class ShareWorkspaceFileToolTests(unittest.TestCase):
    def test_share_main_workspace_relative_file_returns_short_surface(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "docs" / "demo.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"%PDF-1.4\n")
            with patch(
                "core.workspace_share.build_workspace_binding",
                return_value=_trusted_binding(root),
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
            with patch(
                "core.workspace_share.build_workspace_binding",
                return_value=_trusted_binding(
                    root,
                    main_workspace_root=Path(main_dir),
                    workspace_id="ws_demo",
                    project_id="proj_demo",
                ),
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
            with patch(
                "core.workspace_share.build_workspace_binding",
                return_value=_trusted_binding(root),
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
            with patch(
                "core.workspace_share.build_workspace_binding",
                return_value=_trusted_binding(root),
            ), bind_runtime_context(runtime_kind="chat", session_id="sess_demo"):
                with self.assertRaises(PermissionError):
                    resolve_workspace_file_to_share(str(target), "auto")

    def test_adopt_workspace_file_records_runtime_artifact_origin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "docs" / "demo.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"%PDF-1.4\n")
            with patch(
                "core.workspace_share.build_workspace_binding",
                return_value=_trusted_binding(root, workspace_id="ws_demo", project_id="proj_demo"),
            ), patch("core.artifact_store.db.add_runtime_artifact") as add_artifact, bind_runtime_context(
                runtime_kind="chat",
                session_id="sess_demo",
                workspace_id="ws_demo",
                project_id="proj_demo",
            ):
                artifact = artifact_store.adopt_workspace_file(path="docs/demo.pdf", mode="preview")

        self.assertEqual(artifact["origin"], "workspace_adopted")
        self.assertEqual(artifact["metadata"]["origin"], "workspace_adopted")
        self.assertEqual(artifact["pathPlane"], "workspace_artifact")
        self.assertEqual(artifact["workspaceRelativePath"], "docs/demo.pdf")
        self.assertEqual(artifact["workspaceId"], "ws_demo")
        self.assertEqual(artifact["projectId"], "proj_demo")
        self.assertIn("/v1/artifacts/", artifact["contentUrl"])
        add_artifact.assert_called_once()


if __name__ == "__main__":
    unittest.main()
