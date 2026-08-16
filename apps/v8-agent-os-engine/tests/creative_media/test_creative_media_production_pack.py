from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from runtimes.creative_media import runtime as creative_media_runtime_module
from runtimes.creative_media.production_pack import (
    PRODUCTION_PACK_STAGES,
    artifact_qa_markdown,
    build_production_pack,
    build_reference_media_pack,
    build_sample_approval_packet,
    production_pack_markdown,
    rank_candidates_markdown,
    reference_media_markdown,
    run_artifact_qa,
    sample_approval_markdown,
)


class FakeJsonStorage:
    def __init__(self) -> None:
        self.payloads: dict[str, dict] = {}

    def read_json(self, filename: str):
        return deepcopy(self.payloads.get(filename) or {})

    def write_json(self, filename: str, data) -> None:
        self.payloads[filename] = deepcopy(data)


def test_production_pack_has_stable_stage_contract_and_markdown_surface():
    pack = build_production_pack(
        {
            "goal": "Make a V8OS demo video.",
            "providerId": "agnes",
            "modelId": "videos/agnes-video-v2.0",
            "brief": {"summary": "Short product demo."},
            "scene_plan": {"status": "ready", "summary": "Three scenes with title, feature, CTA."},
            "sampleArtifactRefs": [{"artifactId": "artifact_sample", "kind": "video"}],
            "artifactProof": [{"artifactId": "artifact_final", "kind": "video", "status": "playable"}],
        }
    )

    assert pack["stageOrder"] == list(PRODUCTION_PACK_STAGES)
    for stage in PRODUCTION_PACK_STAGES:
        assert stage in pack
        assert pack[stage]["stage"] == stage

    markdown = production_pack_markdown(pack)
    assert markdown.startswith("## CreativeMediaProductionPack")
    assert not markdown.lstrip().startswith(("{", "["))
    assert "brief:" in markdown
    assert "scene_plan: ready" in markdown
    assert "Provider Lock" in markdown
    assert "Sample Approval" in markdown
    assert "Artifact Proof" in markdown
    assert "回流要求" in markdown
    assert "providerLock、sampleApproval、artifactProof、qa" in markdown


def test_rank_candidates_markdown_is_compact_and_not_raw_json():
    markdown = rank_candidates_markdown(
        [
            {
                "candidateId": "c1",
                "modality": "video",
                "operationKind": "video.text_to_video",
                "providerId": "agnes",
                "providerName": "Agnes AI",
                "modelId": "videos/agnes-video-v2.0",
                "modelRef": "model_ref://agnes/video",
                "available": True,
                "enabled": True,
                "priority": 10,
            },
            {
                "candidateId": "c2",
                "modality": "video",
                "operationKind": "video.text_to_video",
                "providerId": "catalog_only",
                "modelId": "catalog-model",
                "available": False,
                "enabled": True,
                "priority": 20,
                "readiness": {"reasonCodes": ["adapter_catalog_only"]},
            },
        ],
        modality="video",
        operation_kind="video.text_to_video",
    )

    assert markdown.startswith("## Creative Media")
    assert "videos/agnes-video-v2.0" in markdown
    assert "可执行" in markdown
    assert "配置错误：adapter_catalog_only" in markdown
    assert "candidateId" not in markdown
    assert not markdown.lstrip().startswith(("{", "["))


def test_rank_candidates_honors_saved_priority_and_does_not_promote_registry_suggestion():
    markdown = rank_candidates_markdown(
        [
            {
                "candidateId": "second",
                "source": "model_control_plane",
                "modality": "image",
                "operationKind": "image.generate",
                "providerName": "Configured B",
                "modelId": "model-b",
                "modelRef": "b::model-b",
                "enabled": True,
                "available": True,
                "priority": 20,
            },
            {
                "candidateId": "first",
                "source": "model_control_plane",
                "modality": "image",
                "operationKind": "image.generate",
                "providerName": "Configured A",
                "modelId": "model-a",
                "modelRef": "a::model-a",
                "enabled": True,
                "available": True,
                "priority": 10,
            },
            {
                "candidateId": "suggested",
                "source": "model_control_plane",
                "modality": "image",
                "operationKind": "image.generate",
                "providerName": "Suggested",
                "modelId": "model-suggested",
                "modelRef": "s::model-suggested",
                "enabled": False,
                "available": False,
                "priority": 1,
                "suggestedAdapter": "openai_images",
                "readiness": {"reasonCodes": ["adapter_not_configured"]},
            },
        ],
        operation_kind="image.generate",
    )

    assert markdown.index("model-a") < markdown.index("model-b") < markdown.index("model-suggested")
    assert "注册表建议（不授权执行）：adapter=openai_images" in markdown


def test_reference_media_pack_surfaces_required_analysis_slots():
    pack = build_reference_media_pack(
        {
            "goal": "Use the reference as a style guide.",
            "media": [{"artifactId": "ref-image", "kind": "image", "title": "style frame"}],
            "visualStyle": "Soft UI, colorful gradients, anime mascot.",
        }
    )
    markdown = reference_media_markdown(pack)

    assert "音频转写：待分析" in markdown
    assert "视觉风格：Soft UI" in markdown
    assert "镜头结构：待分析" in markdown
    assert "vision_media_analyzer" in markdown
    assert "不要进入批量生成" in markdown
    assert "ref-image" in markdown


def test_sample_approval_packet_maps_to_ask_user_arguments():
    packet = build_sample_approval_packet(
        {
            "question": "Choose the sample direction.",
            "selection_mode": "multiple",
            "media": [{"artifactId": "sample-a", "kind": "image", "title": "Sample A"}],
            "questions": [
                {
                    "id": "style",
                    "question": "Pick style",
                    "type": "single",
                    "options": [{"id": "a", "label": "A"}],
                }
            ],
        }
    )
    markdown = sample_approval_markdown(packet)

    assert packet["selection_mode"] == "multiple"
    assert packet["media"][0]["artifactId"] == "sample-a"
    assert "ask_user" in markdown
    assert "media/artifacts" in markdown
    assert "ProductionPack.sampleApproval" in markdown
    assert not markdown.lstrip().startswith(("{", "["))


def test_artifact_qa_checks_file_existence_and_required_kinds(tmp_path: Path, monkeypatch):
    fake_storage = FakeJsonStorage()
    monkeypatch.setattr(creative_media_runtime_module, "storage", fake_storage)
    audio = tmp_path / "sample.mp3"
    audio.write_bytes(b"not a real mp3 but exists")
    subtitle = tmp_path / "sample.vtt"
    subtitle.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n", encoding="utf-8")

    report = run_artifact_qa(
        {
            "artifacts": [{"title": "Sample audio", "kind": "music", "path": str(audio)}],
            "subtitles": [str(subtitle)],
            "requiredKinds": ["music", "video"],
        }
    )
    markdown = artifact_qa_markdown(report)

    assert report["checks"][0]["exists"] is True
    assert report["subtitleChecks"][0]["exists"] is True
    assert "video" in report["missingRequiredKinds"]
    assert "Sample audio: 存在" in markdown
    assert "缺失关键产物" in markdown
    assert not markdown.lstrip().startswith(("{", "["))
    quality_jobs = fake_storage.payloads["creative_media/quality_jobs.json"]["qualityJobs"]
    assert report["qaReportId"] in quality_jobs
