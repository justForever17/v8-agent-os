from __future__ import annotations

import asyncio
from io import BytesIO
import json
import wave

import httpx
import pytest

from core.audio.voice_manager import VoiceCustomizationManager, VoiceManagerError
from core.audio.routes import _voice_manager_error


MODEL_REF = "minimax::t2a_v2%2Fspeech-2.8-hd"


def _wav_sample(duration_seconds: float, sample_rate: int = 8_000) -> bytes:
    frame_count = round(duration_seconds * sample_rate)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(b"\x00\x01" * frame_count)
    return buffer.getvalue()


def _model_record(adapter_id: str = "minimax_tts") -> dict:
    model_id = {
        "minimax_tts": "t2a_v2/speech-2.8-hd",
        "aliyun_bailian_cosyvoice": "cosyvoice-v3.5-plus",
        "volcengine_doubao_voice": "seed-tts-2.0",
    }[adapter_id]
    return {
        "provider_id": adapter_id,
        "model_id": model_id,
        "provider": {
            "name": adapter_id,
            "base_url": "https://api.minimaxi.com/v1/t2a_v2" if adapter_id == "minimax_tts" else "https://example.test/v1",
            "api_key": "configured-secret",
            "voice_app_id": "app-id",
            "voice_resource_id": "resource-id",
        },
        "model": {
            "parameterProfile": adapter_id,
            "mediaLimits": {"adapterProviderId": adapter_id, "providerModelId": model_id.rsplit("/", 1)[-1]},
        },
    }


def test_capabilities_are_declared_by_engine_adapter_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    records = {
        "minimax": _model_record("minimax_tts"),
        "aliyun": _model_record("aliyun_bailian_cosyvoice"),
        "volcengine": _model_record("volcengine_doubao_voice"),
    }
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda ref: records[ref])
    manager = VoiceCustomizationManager()

    assert manager.capabilities("minimax")["capabilities"] == {
        "clone": True,
        "design": True,
        "list": True,
        "delete": True,
        "preview": True,
        "commit": False,
    }
    assert manager.capabilities("aliyun")["provider"] == "aliyun_bailian_cosyvoice"
    assert manager.capabilities("aliyun")["assetPolicy"]["inventorySource"] == "remote"
    assert manager.capabilities("volcengine")["capabilities"]["delete"] is False
    assert manager.capabilities("volcengine")["capabilities"]["list"] is False
    assert manager.capabilities("volcengine")["assetPolicy"]["assetScope"] == "provider_slot"


def test_name_only_provider_is_not_treated_as_voice_customization_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _model_record()
    record["provider"]["name"] = "MiniMax Speech"
    record["model"]["parameterProfile"] = ""
    record["model"]["mediaLimits"] = {}
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda _ref: record)

    with pytest.raises(VoiceManagerError) as error:
        VoiceCustomizationManager().capabilities(MODEL_REF)

    assert error.value.code == "unsupported_voice_manager"


def test_list_voices_uses_engine_materialized_credential_and_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda _ref: _model_record())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.minimaxi.com/v1/get_voice")
        assert request.headers["authorization"] == "Bearer configured-secret"
        assert json.loads(request.content) == {"voice_type": "all"}
        return httpx.Response(
            200,
            json={
                "system_voice": [{"voice_id": "system-voice", "voice_name": "系统女声"}],
                "voice_cloning": [{"voice_id": "custom-voice"}],
                "voice_generation": [{"voice_id": "generated-voice"}],
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    result = asyncio.run(VoiceCustomizationManager(transport=httpx.MockTransport(handler)).list_voices(MODEL_REF))

    assert result["ok"] is True
    assert [voice["group"] for voice in result["voices"]] == ["system", "cloned", "generated"]
    assert result["voices"][0]["deletable"] is False
    assert result["voices"][1]["deletable"] is True
    assert "configured-secret" not in str(result)


def test_minimax_list_keeps_provider_confirmed_clone_while_activation_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda _ref: _model_record())
    monkeypatch.setattr(
        "core.audio.voice_manager._ledger_voices",
        lambda _context, **_kwargs: [
            {
                "value": "v8_pending_voice",
                "label": "v8_pending_voice",
                "group": "custom",
                "deletable": True,
                "source": "local_ledger",
                "availability": "pending_activation",
            }
        ],
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "system_voice": [],
                "voice_cloning": [],
                "voice_generation": [],
                "base_resp": {"status_code": 0},
            },
        )

    result = asyncio.run(VoiceCustomizationManager(transport=httpx.MockTransport(handler)).list_voices(MODEL_REF))

    assert result["voices"] == [
        {
            "value": "v8_pending_voice",
            "label": "v8_pending_voice",
            "group": "custom",
            "deletable": True,
            "source": "local_ledger",
            "availability": "pending_activation",
        }
    ]


def test_provider_error_projects_business_code_and_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda _ref: _model_record())

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"trace_id": "trace-voice-123"},
            json={"base_resp": {"status_code": 2013, "status_msg": "invalid params"}},
        )

    with pytest.raises(VoiceManagerError) as error:
        asyncio.run(VoiceCustomizationManager(transport=httpx.MockTransport(handler)).list_voices(MODEL_REF))

    assert error.value.status_code == 502
    assert error.value.code == "provider_request_failed"
    assert error.value.provider_code == "2013"
    assert error.value.trace_id == "trace-voice-123"


def test_aliyun_error_projects_body_code_and_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.audio.voice_manager.model_control_plane.get_model_record",
        lambda _ref: _model_record("aliyun_bailian_cosyvoice"),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            415,
            json={
                "code": "BadRequest.UnsupportedFileFormat",
                "message": "File format unsupported.",
                "request_id": "aliyun-request-123",
            },
        )

    with pytest.raises(VoiceManagerError) as error:
        asyncio.run(
            VoiceCustomizationManager(transport=httpx.MockTransport(handler)).delete_voice(
                "aliyun",
                "voice-id",
            )
        )

    assert error.value.provider_code == "BadRequest.UnsupportedFileFormat"
    assert error.value.trace_id == "aliyun-request-123"


def test_volcengine_error_projects_header_code_message_and_log_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.audio.voice_manager.model_control_plane.get_model_record",
        lambda _ref: _model_record("volcengine_doubao_voice"),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "X-Api-Status-Code": "45000000",
                "X-Api-Message": "voice sample rejected",
                "X-Tt-Logid": "volc-log-123",
            },
            json={},
        )

    with pytest.raises(VoiceManagerError) as error:
        asyncio.run(
            VoiceCustomizationManager(transport=httpx.MockTransport(handler)).clone_voice(
                "volcengine",
                voice_id="voice-id",
                preview_text="",
                filename="sample.wav",
                content_type="audio/wav",
                audio_bytes=_wav_sample(10.0),
            )
        )

    assert str(error.value) == "voice sample rejected"
    assert error.value.provider_code == "45000000"
    assert error.value.trace_id == "volc-log-123"


def test_engine_error_response_keeps_sanitized_provider_diagnostics() -> None:
    response = _voice_manager_error(
        VoiceManagerError(
            "invalid params",
            status_code=502,
            code="provider_request_failed",
            provider="minimax_tts",
            provider_code="2013",
            trace_id="trace-voice-123",
        )
    )
    payload = json.loads(response.body)

    assert payload == {
        "ok": False,
        "error": "invalid params",
        "errorCode": "provider_request_failed",
        "provider": "minimax_tts",
        "providerCode": "2013",
        "traceId": "trace-voice-123",
    }


def test_clone_keeps_minimax_file_id_as_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda _ref: _model_record())
    requests: list[httpx.Request] = []
    confirmed_voice_ids: list[str] = []

    async def capture_confirmed_clone(_context, voice_id: str) -> None:
        confirmed_voice_ids.append(voice_id)

    monkeypatch.setattr("core.audio.voice_manager._upsert_ledger_entry", capture_confirmed_clone)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/files/upload":
            assert request.headers["authorization"] == "Bearer configured-secret"
            assert b"voice_clone" in request.content
            return httpx.Response(
                200,
                json={"file": {"file_id": 12345}, "base_resp": {"status_code": 0, "status_msg": "success"}},
            )
        assert request.url.path == "/v1/voice_clone"
        assert json.loads(request.content) == {
            "file_id": 12345,
            "voice_id": "V8_custom_voice",
            "text": "你好",
            "model": "speech-2.8-hd",
        }
        return httpx.Response(
            200,
            json={"demo_audio": "https://example.test/preview.mp3", "base_resp": {"status_code": 0}},
        )

    result = asyncio.run(
        VoiceCustomizationManager(transport=httpx.MockTransport(handler)).clone_voice(
            MODEL_REF,
            voice_id="V8_custom_voice",
            preview_text="你好",
            filename="sample.wav",
            content_type="audio/wav",
            audio_bytes=_wav_sample(10.0),
        )
    )

    assert len(requests) == 2
    assert result["fileId"] == 12345
    assert result["availability"] == "pending_activation"
    assert result["sampleDurationSeconds"] == 10.0
    assert result["previewAudioUrl"] == "https://example.test/preview.mp3"
    assert confirmed_voice_ids == ["V8_custom_voice"]


def test_clone_rejects_audio_shorter_than_ten_seconds_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda _ref: _model_record())
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500)

    with pytest.raises(VoiceManagerError) as error:
        asyncio.run(
            VoiceCustomizationManager(transport=httpx.MockTransport(handler)).clone_voice(
                MODEL_REF,
                voice_id="V8_custom_voice",
                preview_text="",
                filename="sample.wav",
                content_type="audio/wav",
                audio_bytes=_wav_sample(9.99),
            )
        )

    assert error.value.code == "sample_too_short"
    assert request_count == 0


def test_clone_rejects_invalid_voice_id_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda _ref: _model_record())

    with pytest.raises(VoiceManagerError) as error:
        asyncio.run(
            VoiceCustomizationManager().clone_voice(
                MODEL_REF,
                voice_id="bad",
                preview_text="",
                filename="sample.wav",
                content_type="audio/wav",
                audio_bytes=_wav_sample(10.0),
            )
        )

    assert error.value.code == "invalid_voice_id"
