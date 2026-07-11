from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from core.tools.native import creative_media_psd as psd_tools


class _Binding:
    def __init__(self, root: Path) -> None:
        self.active_workspace_root = root
        self.main_workspace_root = root

    def as_dict(self) -> dict[str, str]:
        return {
            "activeWorkspaceRoot": str(self.active_workspace_root),
            "mainWorkspaceRoot": str(self.main_workspace_root),
        }


def _patch_workspace(monkeypatch, root: Path) -> None:
    binding = _Binding(root)

    def _binding(*_args, **_kwargs):
        return binding

    def _resolve(path: str, *_args, **_kwargs):
        raw = Path(path)
        resolved = raw if raw.is_absolute() else root / raw
        return {"ok": True, "resolvedPath": str(resolved), "summary": "ok", "binding": binding.as_dict()}

    monkeypatch.setattr(psd_tools, "build_workspace_binding", _binding)
    monkeypatch.setattr(psd_tools, "resolve_workspace_tool_path", _resolve)


def test_alpha_inspect_returns_clean_markdown_for_true_alpha(tmp_path: Path, monkeypatch) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    image_path = tmp_path / "subject.png"
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for x in range(4, 12):
        for y in range(4, 12):
            image.putpixel((x, y), (255, 64, 32, 255))
    image.save(image_path)

    output = psd_tools.creative_media_alpha_inspect.invoke({"path": "subject.png"})

    assert output.startswith("### Creative Media Alpha Inspect")
    assert not output.lstrip().startswith("{")
    assert "Status: true_alpha" in output
    assert "transparent pixels" in output
    assert "provider" not in output.lower()


def test_psd_compose_template_dry_run_returns_plan_without_writing(tmp_path: Path, monkeypatch) -> None:
    _patch_workspace(monkeypatch, tmp_path)

    output = psd_tools.creative_media_psd_compose_template.invoke(
        {
            "request": {
                "name": "hero-card",
                "canvas": {"width": 320, "height": 180, "background": "transparent"},
                "layers": [
                    {"name": "subject cutout", "path": "subject.png", "x": 12, "y": 8},
                    {"name": "title text", "path": "title.png", "x": 48, "y": 120},
                ],
                "dryRun": True,
            }
        }
    )

    assert output.startswith("### Creative Media PSD Compose Template")
    assert not output.lstrip().startswith("{")
    assert "Status: planned" in output
    assert "subject cutout" in output
    assert "dry run" in output.lower()
    assert not (tmp_path / ".v8").exists()


def test_psd_dependency_is_declared() -> None:
    requirements = (Path(__file__).resolve().parents[2] / "requirements/desktop-common.txt").read_text(encoding="utf-8")
    assert "psd-tools[composite]" in requirements


def test_psd_compose_template_writes_psd_and_preview_when_dependency_exists(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("psd_tools")
    _patch_workspace(monkeypatch, tmp_path)

    subject = tmp_path / "subject.png"
    title = tmp_path / "title.png"
    Image.new("RGBA", (24, 24), (255, 0, 0, 255)).save(subject)
    Image.new("RGBA", (64, 16), (0, 0, 255, 180)).save(title)

    recorded: list[Path] = []

    class _Artifacts:
        def record_local_file(self, **kwargs):
            path = Path(kwargs["file_path"])
            recorded.append(path)
            return {
                "artifactId": f"art_{path.stem}",
                "contentUrl": f"/v1/artifacts/art_{path.stem}/content",
                "sourcePath": str(path),
            }

    monkeypatch.setattr(psd_tools, "artifact_store", _Artifacts())

    output = psd_tools.creative_media_psd_compose_template.invoke(
        {
            "request": {
                "name": "hero-card",
                "canvas": {"width": 96, "height": 64, "background": "transparent"},
                "layers": [
                    {"name": "subject", "path": "subject.png", "x": 8, "y": 8},
                    {"name": "title", "path": "title.png", "x": 16, "y": 40},
                ],
            }
        }
    )

    assert output.startswith("### Creative Media PSD Compose Template")
    assert "Status: succeeded" in output
    assert (tmp_path / ".v8" / "creative-media" / "psd").exists()
    assert any(path.suffix.lower() == ".psd" and path.exists() for path in recorded)
    assert any(path.suffix.lower() == ".png" and path.exists() for path in recorded)
