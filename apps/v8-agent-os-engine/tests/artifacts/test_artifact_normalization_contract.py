import core.artifact_store as artifact_store_module
from core.artifact_store import ArtifactStore
from core.multimodal_payload_adapter import infer_media_kind, normalize_artifact_record


def test_code_artifacts_are_not_projected_as_documents() -> None:
    assert infer_media_kind("text/x-python", "src/main.py") == "code"
    assert infer_media_kind("video/mp2t", "src/app.ts") == "code"
    assert infer_media_kind("text/markdown", "README.md") == "document"

    normalized = normalize_artifact_record(
        {
            "id": "art_code",
            "artifact_kind": "document",
            "mime_type": "text/x-python",
            "source_path": "E:/workspace/src/main.py",
            "preview_url": "/v1/artifacts/art_code/content",
        }
    )
    assert normalized["kind"] == "code"
    assert normalized["artifact_kind"] == "code"


def test_runtime_artifact_subtitle_never_exposes_local_or_internal_path() -> None:
    normalized = normalize_artifact_record(
        {
            "id": "art_runtime_image",
            "mime_type": "image/png",
            "source_path": "E:/workspace/creative_media/cm_private/image.png",
            "workspace_path": "creative_media/cm_private/image.png",
            "displaySubtitle": "E:/workspace/creative_media/cm_private/image.png",
            "metadata": {
                "storageClass": "runtime_artifact",
                "pathPlane": "runtime",
                "canonicalPath": "E:/workspace/creative_media/cm_private/image.png",
            },
        }
    )

    assert normalized["displaySubtitle"] == "image/png"
    assert normalized["sourcePath"].endswith("creative_media/cm_private/image.png")


def test_workspace_artifact_subtitle_keeps_only_safe_relative_path() -> None:
    normalized = normalize_artifact_record(
        {
            "id": "art_workspace_markdown",
            "mime_type": "text/markdown",
            "source_path": "E:/workspace/docs/README.md",
            "workspace_path": "docs/README.md",
            "metadata": {
                "storageClass": "workspace",
                "pathPlane": "workspace_artifact",
                "workspaceRelativePath": "docs\\README.md",
                "canonicalPath": "E:/workspace/docs/README.md",
            },
        }
    )

    assert normalized["displaySubtitle"] == "docs/README.md"


def test_computer_use_local_artifact_uses_canonical_content_endpoint(tmp_path, monkeypatch) -> None:
    screenshot = tmp_path / "capture.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")
    stored: dict = {}
    store = ArtifactStore()

    monkeypatch.setattr(
        artifact_store_module.db,
        "list_runtime_artifacts",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        artifact_store_module.db,
        "add_runtime_artifact",
        lambda **kwargs: stored.update(kwargs),
    )
    monkeypatch.setattr(store, "_emit_artifact_recorded_event", lambda **_kwargs: None)

    artifact = store.record_local_file(
        file_path=screenshot,
        session_id="session-computer-use",
        run_id="run-computer-use",
        workspace_path=".v8-agent-os/artifacts/session-computer-use/run-computer-use/capture.png",
        metadata={
            "runtime": "computer_use",
            "origin": "computer_use_screenshot",
            "capture": {"ok": True},
            "pathPlane": "workspace_artifact",
            "storageClass": "workspace",
        },
        source_component="computer_use_runtime",
    )

    canonical_url = f"/v1/artifacts/{artifact['artifactId']}/content"
    assert artifact["previewUrl"] == canonical_url
    assert artifact["contentUrl"] == canonical_url
    assert stored["preview_url"] == canonical_url
    assert stored["source_path"] == str(screenshot)
