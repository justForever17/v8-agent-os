from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import core.creative_canvas_preview as preview_module
from core.creative_canvas_preview import CreativeCanvasPreviewError, resolve_canvas_preview


def _bind_source(monkeypatch: pytest.MonkeyPatch, source: Path, fingerprint: str = "v8mf_test") -> None:
    monkeypatch.setattr(
        preview_module,
        "resolve_governed_media_path",
        lambda request: (source, {"kind": "source", "id": request.get("sourceId")}),
    )
    monkeypatch.setattr(preview_module, "governed_media_fingerprint", lambda path: fingerprint)


def test_image_preview_is_bounded_cached_and_kept_inside_runtime_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "workspace" / "portrait.png"
    source.parent.mkdir()
    Image.new("RGB", (2400, 1200), (180, 20, 70)).save(source)
    runtime_data = tmp_path / "runtime-data"
    monkeypatch.setattr(preview_module, "RUNTIME_DATA_HOME", runtime_data)
    _bind_source(monkeypatch, source)

    first = resolve_canvas_preview({"sessionId": "session-a", "sourceId": "source-a"})
    second = resolve_canvas_preview({"sessionId": "session-a", "sourceId": "source-a"})

    assert first.generated is True
    assert first.media_type == "image/webp"
    assert first.path == second.path
    assert first.path.is_relative_to(runtime_data)
    with Image.open(first.path) as image:
        assert image.width <= 1280
        assert image.height <= 960


def test_video_preview_uses_short_governed_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "workspace" / "clip.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    monkeypatch.setattr(preview_module, "RUNTIME_DATA_HOME", tmp_path / "runtime-data")
    _bind_source(monkeypatch, source)
    monkeypatch.setattr(preview_module, "governed_ffmpeg_pair", lambda: ("ffmpeg", "ffprobe", "7.1"))
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, timeout: int) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"proxy-video")
        assert timeout == 180

    monkeypatch.setattr(preview_module, "_run_ffmpeg", fake_run)
    result = resolve_canvas_preview({"sessionId": "session-a", "sourceId": "source-a"})

    assert result.media_type == "video/mp4"
    assert result.path.read_bytes() == b"proxy-video"
    assert "-t" in commands[0]
    assert commands[0][commands[0].index("-t") + 1] == "8"


def test_3d_preview_fails_honestly_until_godot_adapter_is_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "character.glb"
    source.write_bytes(b"glTF")
    _bind_source(monkeypatch, source)

    with pytest.raises(CreativeCanvasPreviewError, match="Godot adapter"):
        resolve_canvas_preview({"sessionId": "session-a", "sourceId": "source-a"})
