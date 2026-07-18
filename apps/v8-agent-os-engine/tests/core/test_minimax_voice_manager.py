from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from core.audio.voice_manager import MiniMaxVoiceManager, VoiceManagerError


MODEL_REF = "minimax::t2a_v2%2Fspeech-2.8-hd"


def _model_record() -> dict:
    return {
        "provider_id": "minimax",
        "model_id": "t2a_v2/speech-2.8-hd",
        "provider": {
            "name": "MiniMax 中国站",
            "base_url": "https://api.minimaxi.com/v1/t2a_v2",
            "api_key": "configured-secret",
        },
        "model": {
            "parameterProfile": "minimax_tts",
            "mediaLimits": {"adapterProviderId": "minimax_tts"},
        },
    }


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

    result = asyncio.run(MiniMaxVoiceManager(transport=httpx.MockTransport(handler)).list_voices(MODEL_REF))

    assert result["ok"] is True
    assert [voice["group"] for voice in result["voices"]] == ["system", "cloned", "generated"]
    assert result["voices"][0]["deletable"] is False
    assert result["voices"][1]["deletable"] is True
    assert "configured-secret" not in str(result)


def test_http_200_with_minimax_business_error_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda _ref: _model_record())

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"base_resp": {"status_code": 1004, "status_msg": "invalid api key"}})

    with pytest.raises(VoiceManagerError) as error:
        asyncio.run(MiniMaxVoiceManager(transport=httpx.MockTransport(handler)).list_voices(MODEL_REF))

    assert error.value.status_code == 502
    assert error.value.code == "minimax_request_failed"
    assert "invalid api key" in str(error.value)


def test_clone_uploads_audio_then_calls_clone_without_returning_raw_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda _ref: _model_record())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/files/upload":
            assert request.headers["authorization"] == "Bearer configured-secret"
            assert b"voice_clone" in request.content
            assert b"sample-audio" in request.content
            return httpx.Response(
                200,
                json={"file": {"file_id": 12345}, "base_resp": {"status_code": 0, "status_msg": "success"}},
            )
        assert request.url.path == "/v1/voice_clone"
        assert json.loads(request.content) == {
            "file_id": "12345",
            "voice_id": "V8_custom_voice",
            "text": "你好",
            "model": "speech-2.8-hd",
        }
        return httpx.Response(200, json={"demo_audio": "raw-provider-payload", "base_resp": {"status_code": 0}})

    result = asyncio.run(
        MiniMaxVoiceManager(transport=httpx.MockTransport(handler)).clone_voice(
            MODEL_REF,
            voice_id="V8_custom_voice",
            preview_text="你好",
            filename="sample.wav",
            content_type="audio/wav",
            audio_bytes=b"sample-audio",
        )
    )

    assert len(requests) == 2
    assert result == {
        "ok": True,
        "provider": "minimax_tts",
        "capabilities": {
            "supportsVoiceManager": True,
            "supportsList": True,
            "supportsDelete": True,
            "supportsCloneUpload": True,
        },
        "voiceId": "V8_custom_voice",
        "fileId": "12345",
    }


def test_clone_rejects_invalid_voice_id_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.audio.voice_manager.model_control_plane.get_model_record", lambda _ref: _model_record())

    with pytest.raises(VoiceManagerError) as error:
        asyncio.run(
            MiniMaxVoiceManager().clone_voice(
                MODEL_REF,
                voice_id="bad",
                preview_text="",
                filename="sample.wav",
                content_type="audio/wav",
                audio_bytes=b"sample",
            )
        )

    assert error.value.code == "invalid_voice_id"
