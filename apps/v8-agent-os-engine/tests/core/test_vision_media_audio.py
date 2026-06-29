from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_core.messages import AIMessage

from core.multimodal_payload_adapter import build_multimodal_content, describe_multimodal_payload_shape
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


def test_multimodal_adapter_builds_openai_audio_url_payload():
    content = build_multimodal_content(
        prompt="请识别音频内容。",
        media_url="https://example.test/audio.mp3",
        mime_type="audio/mpeg",
        api_standard="openai",
        transport_mode="url_reference",
    )

    assert content == [
        {"type": "text", "text": "请识别音频内容。"},
        {
            "type": "input_audio",
            "input_audio": {
                "url": "https://example.test/audio.mp3",
                "format": "mp3",
            },
        },
    ]


def test_multimodal_adapter_builds_doubao_ark_audio_payload():
    content = build_multimodal_content(
        prompt="请识别音频内容。",
        media_url="https://example.test/audio.mp3",
        mime_type="audio/mpeg",
        api_standard="openai",
        transport_mode="url_reference",
        provider_id="volcengine-coding",
        model_id="doubao-seed-2-0-lite",
    )

    assert content == [
        {"type": "text", "text": "请识别音频内容。"},
        {
            "type": "input_audio",
            "input_audio": {
                "url": "https://example.test/audio.mp3",
                "format": "mp3",
            },
        },
    ]
    assert (
        describe_multimodal_payload_shape(
            mime_type="audio/mpeg",
            api_standard="openai",
            provider_id="volcengine-coding",
            model_id="doubao-seed-2-0-lite",
            transport_mode="url_reference",
        )
        == "audio:ark_input_audio:url_reference"
    )


def test_multimodal_adapter_builds_mimo_audio_url_payload():
    content = build_multimodal_content(
        prompt="请识别音频内容。",
        media_url="AAEC",
        mime_type="audio/mpeg",
        api_standard="openai",
        transport_mode="inline_base64_audio",
        provider_id="xiaomi-mimo",
        model_id="mimo-v2.5-pro",
    )

    assert content == [
        {"type": "text", "text": "请识别音频内容。"},
        {
            "type": "input_audio",
            "audio_url": "data:audio/mpeg;base64,AAEC",
        },
    ]
    assert (
        describe_multimodal_payload_shape(
            mime_type="audio/mpeg",
            api_standard="openai",
            provider_id="xiaomi-mimo",
            model_id="mimo-v2.5-pro",
            transport_mode="inline_base64_audio",
        )
        == "audio:mimo_audio_url:inline_base64_audio"
    )


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
    assert captured["config"]["metadata"]["payloadShape"] == "audio:openai_input_audio:inline_base64_audio"


def test_vision_media_analyzer_uses_mimo_audio_payload(monkeypatch, tmp_path: Path):
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
            "resolvedProviderId": "xiaomi-mimo",
            "resolvedModel": {"capabilityClass": "vision_multimodal"},
            "resolvedModelId": "mimo-v2.5-pro",
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
            return AIMessage(content="转写：你好。")

    monkeypatch.setattr(vision_module.llm_factory, "create_for_role", lambda role, temperature=0.1: FakeVisionModel())

    output = vision_module.vision_media_analyzer.func(file_path=str(audio_path), prompt="请逐字转写音频。")

    assert "转写：你好。" in output
    message_content = captured["messages"][0].content
    assert message_content[1]["type"] == "input_audio"
    assert message_content[1]["audio_url"].startswith("data:audio/mpeg;base64,")
    assert "input_audio" not in message_content[1]
    assert captured["config"]["metadata"]["payloadShape"] == "audio:mimo_audio_url:inline_base64_audio"


def test_vision_media_analyzer_audio_failure_hides_provider_raw_json(monkeypatch, tmp_path: Path):
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
            "resolvedProviderId": "volcengine-coding",
            "resolvedModel": {"capabilityClass": "vision_multimodal"},
            "resolvedModelId": "doubao-seed-2-0-lite",
        },
    )
    monkeypatch.setattr(vision_module.model_control_plane, "get_config", lambda: {})
    monkeypatch.setattr(vision_module.model_budget_service, "enforce_or_raise", lambda **kwargs: None)
    monkeypatch.setattr(vision_module.artifact_store, "record_local_file", lambda **kwargs: None)

    class FakeVisionModel:
        def invoke(self, messages, config):
            raise RuntimeError(
                "capability_mismatch: Error code: 400 - {'error': {'code': 'InvalidParameter', "
                "'message': 'messages.content.input_audio is not supported by this model'}}"
            )

    monkeypatch.setattr(vision_module.llm_factory, "create_for_role", lambda role, temperature=0.1: FakeVisionModel())

    output = vision_module.vision_media_analyzer.func(file_path=str(audio_path), prompt="请逐字转写音频。", tool_call_id="call_audio")

    assert "结果：音频识别失败" in output
    assert "原因：当前模型接口拒绝音频输入" in output
    assert "下一步：" in output
    assert "detailRef：tool_call:call_audio" in output
    assert "InvalidParameter" not in output
    assert "messages.content.input_audio" not in output
    assert "{'error'" not in output


def test_vision_media_analyzer_transcodes_non_mp3_audio_before_model_call(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"RIFFfake-wav")

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
    monkeypatch.setattr(vision_module.shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)

    def _fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"ID3fake-mp3")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(vision_module.subprocess, "run", _fake_run)

    captured: dict[str, object] = {}

    class FakeVisionModel:
        def invoke(self, messages, config):
            captured["messages"] = messages
            captured["config"] = config
            return AIMessage(content="转写：这是一段转换后的音频。")

    monkeypatch.setattr(vision_module.llm_factory, "create_for_role", lambda role, temperature=0.1: FakeVisionModel())

    output = vision_module.vision_media_analyzer.func(
        file_path=str(audio_path),
        prompt="请逐字转写音频。",
    )

    assert output.startswith("--- Vision Analysis Complete ---")
    assert "转换后的音频" in output
    message_content = captured["messages"][0].content
    assert message_content[1]["type"] == "input_audio"
    assert message_content[1]["input_audio"]["format"] == "mp3"
    assert captured["config"]["metadata"]["mimeType"] == "audio/mpeg"
    assert captured["config"]["metadata"]["audioTranscoded"] is True


def test_vision_media_analyzer_reports_ffmpeg_missing_for_non_mp3_audio(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"RIFFfake-wav")

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
    monkeypatch.setattr(vision_module.shutil, "which", lambda name: None)

    def _should_not_create_model(*args, **kwargs):
        raise AssertionError("audio conversion failure must stop before model initialization")

    monkeypatch.setattr(vision_module.llm_factory, "create_for_role", _should_not_create_model)

    output = vision_module.vision_media_analyzer.func(
        file_path=str(audio_path),
        prompt="请逐字转写音频。",
    )

    assert "无法转换为 MP3" in output
    assert "ffmpeg" in output


def test_vision_media_analyzer_downloads_and_transcodes_remote_audio(monkeypatch):
    monkeypatch.setattr(vision_module.shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)

    class FakeResponse:
        headers = {"Content-Type": "audio/wav"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"RIFFremote-wav"

    monkeypatch.setattr(vision_module.requests, "get", lambda *args, **kwargs: FakeResponse())

    def _fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"ID3remote-mp3")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(vision_module.subprocess, "run", _fake_run)

    data, byte_size, metadata = vision_module._build_inline_audio_data_from_url(
        "https://example.test/voice.wav",
        "audio/wav",
    )

    assert data
    assert byte_size == len(b"ID3remote-mp3")
    assert metadata["audioTranscoded"] is True
    assert metadata["audioRemoteDownloaded"] is True


def test_vision_media_analyzer_routes_large_audio_to_s3_after_mp3_normalization(monkeypatch, tmp_path: Path):
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"RIFFfake-wav")

    monkeypatch.setattr(vision_module, "_LARGE_MEDIA_S3_THRESHOLD", 4)
    monkeypatch.setattr(vision_module.shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(vision_module, "_try_upload_media_to_s3", lambda path: f"https://s3.example.test/{Path(path).name}")

    def _fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"ID3large-mp3")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(vision_module.subprocess, "run", _fake_run)

    payload_ref, byte_size, metadata, transport_mode = vision_module._prepare_audio_payload_from_file(
        audio_path,
        "audio/wav",
    )

    assert transport_mode == "url_reference"
    assert payload_ref.startswith("https://s3.example.test/")
    assert byte_size == len(b"ID3large-mp3")
    assert metadata["audioTranscoded"] is True
    assert metadata["mediaRoutedToS3"] is True


def test_vision_media_analyzer_routes_large_image_to_s3(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "large.png"
    image_path.write_bytes(b"not-real-image-but-routed")

    monkeypatch.setattr(vision_module, "_LARGE_MEDIA_S3_THRESHOLD", 4)
    monkeypatch.setattr(
        vision_module,
        "_route_local_media_file_to_url",
        lambda path, allow_workspace_fallback=False: ("https://s3.example.test/large.png", {"mediaRoutedToS3": True}),
    )
    monkeypatch.setattr(
        vision_module,
        "get_runtime_context",
        lambda: {"session_id": "session-image", "run_id": "run-image", "project_id": "test"},
    )
    monkeypatch.setattr(
        vision_module.model_control_plane,
        "resolve_model_for_role",
        lambda role: {
            "resolvedProvider": {"type": "API", "api_standard": "openai"},
            "resolvedProviderId": "openai-vision",
            "resolvedModel": {"capabilityClass": "vision_multimodal"},
            "resolvedModelId": "gpt-4o",
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
            return AIMessage(content="图片分析完成。")

    monkeypatch.setattr(vision_module.llm_factory, "create_for_role", lambda role, temperature=0.1: FakeVisionModel())

    output = vision_module.vision_media_analyzer.func(
        file_path=str(image_path),
        prompt="请描述图片。",
    )

    assert "图片分析完成" in output
    message_content = captured["messages"][0].content
    assert message_content[1] == {
        "type": "image_url",
        "image_url": {"url": "https://s3.example.test/large.png"},
    }
    assert captured["config"]["metadata"]["transportMode"] == "url_reference"
