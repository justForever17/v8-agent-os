from __future__ import annotations

from pathlib import Path

from core.artifact_policy.surface_policy import apply_artifact_surface_policy
from core.multimodal_payload_adapter import build_artifact_descriptor, normalize_artifact_record


def test_computer_use_screenshot_policy_marks_ephemeral_message_attachment(tmp_path: Path):
    screenshot = tmp_path / "observe.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\n")
    descriptor = build_artifact_descriptor(
        artifact_id="art-shot",
        file_path=screenshot,
        workspace_path=".v8-agent-os/artifacts/session/run/observe.png",
        metadata={
            "runtime": "computer_use",
            "capture": {"ok": True},
            "pathPlane": "workspace_artifact",
            "storageClass": "workspace",
        },
    )

    applied = apply_artifact_surface_policy(descriptor, session_id="session-1", run_id="run-1")
    metadata = applied["metadata"]

    assert applied["origin"] == "computer_use_screenshot"
    assert applied["autoAttachToMessage"] is True
    assert applied["surfaceVisible"] is True
    assert applied["supportsInlinePreview"] is True
    assert applied["previewKind"] == "image"
    assert metadata["ephemeral"] is True
    assert metadata["artifactSurfacePolicyRuleId"] == "computer_use_screenshot"


def test_policy_blocks_oversized_text_report_from_message_attachment(tmp_path: Path):
    report = tmp_path / "large.log"
    report.write_bytes(b"x" * (11 * 1024 * 1024))
    descriptor = build_artifact_descriptor(
        artifact_id="art-log",
        file_path=report,
        workspace_path="logs/large.log",
        metadata={"pathPlane": "workspace_artifact"},
    )

    applied = apply_artifact_surface_policy(descriptor, session_id="session-1", run_id="run-1")

    assert applied["autoAttachToMessage"] is False
    assert applied["surfaceVisible"] is False
    assert applied["metadata"]["autoAttachBlockedReason"] == "max_bytes_exceeded"


def test_normalized_artifact_exposes_auto_attach_fields():
    normalized = normalize_artifact_record(
        {
            "artifactId": "art-1",
            "kind": "image",
            "mimeType": "image/png",
            "metadata": {
                "autoAttachToMessage": True,
                "surfaceVisible": True,
                "supportsInlinePreview": True,
                "previewKind": "image",
                "ephemeral": True,
                "artifactSurfacePolicyRuleId": "computer_use_screenshot",
            },
        }
    )

    assert normalized["autoAttachToMessage"] is True
    assert normalized["surfaceVisible"] is True
    assert normalized["supportsInlinePreview"] is True
    assert normalized["previewKind"] == "image"
    assert normalized["ephemeral"] is True
    assert normalized["artifactSurfacePolicyRuleId"] == "computer_use_screenshot"
