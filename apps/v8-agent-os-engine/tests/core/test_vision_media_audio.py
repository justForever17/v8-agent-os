from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage

from core.multimodal_payload_adapter import build_multimodal_content
from core.tools import vision_media_analyzer as vision_module


def test_multimodal_adapter_builds_openai_input_audio_payload():
    content = build_multimodal_content(
        prompt="请转写这段语音。",
        media_url="AAEC",
        mime_type="audio/mpeg",
        api_standard="openai",
        transport_mode="inline_base64_audio",
    )

    assert content == [
        {"type": "text", "text": "请转写这段语音。"},
        {"type": "input_audio", "input_audio": {"data": "AAEC", "format": "mp3"}},
    ]


def test_multimodal_adapter_builds_gemini_inline_audio_payload():
    content = build_multimodal_content(
        prompt="Summarize the audio.",
        media_url="AAEC",
        mime_type="audio/wav",
        api_standard="gemini",
        transport_mode="inline_base64_audio",
    )

    assert content == [
        {"type": "text", "text": "Summarize the audio."},
        {"type": "media", "data": "AAEC", "mime_type": "audio/wav"},
    ]


def test_vision_media_analyzer_accepts_audio_and_strips_reasoning(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "voice.mp3"
    audio_path.write_bytes(b"\x00\x01fake-mp3")

    monkeypatch.setattr(
        vision_module,
        "get_runtime_context",
        lambda: {"session_id": "session-audio", "run_id": "run-audio", "project_id": "test"},
    )
    monkeypatch.setattr(
        vision_module.model_control_plane,
        "resolve_model_for_role",
        lambda role: {
            "resolvedProvider": {"type": "API", "api_standard": "openai"},
            "resolvedProviderId": "openai-audio",
            "resolvedModel": {"capabilityClass": "vision_multimodal"},
            "resolvedModelId": "gpt-4o-audio-preview",
        },
    )
    monkeypatch.setattr(vision_module.model_control_plane, "get_config", lambda: {})
    monkeypatch.setattr(vision_module.model_budget_service, "enforce_or_raise", lambda **kwargs: None)
    monkeypatch.setattr(vision_module.artifact_store, "record_local_file", lambda **kwargs: None)

    captured: dict[str, object] = {}

    class FakeVisionModel:
        def invoke(self, messages, config):
            captured["messages"] = messages
            captured["config"] = config
            return AIMessage(
                content=[
                    {"type": "reasoning", "text": "hidden chain of thought"},
                    {"type": "text", "text": "转写：你好，V8OS。"},
                ],
                additional_kwargs={"reasoning_content": "also hidden"},
            )

    monkeypatch.setattr(vision_module.llm_factory, "create_for_role", lambda role, temperature=0.1: FakeVisionModel())

    output = vision_module.vision_media_analyzer.func(
        file_path=str(audio_path),
        prompt="请逐字转写音频。",
    )

    assert output.startswith("--- Vision Analysis Complete ---")
    assert "转写：你好，V8OS。" in output
    assert "hidden chain of thought" not in output
    assert "also hidden" not in output

    message_content = captured["messages"][0].content
    assert message_content[1]["type"] == "input_audio"
    assert message_content[1]["input_audio"]["format"] == "mp3"
    assert captured["config"]["metadata"]["mediaKind"] == "audio"
    assert captured["config"]["metadata"]["transportMode"] == "inline_base64_audio"
