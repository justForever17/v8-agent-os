from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import json
import wave

import httpx
import pytest

from core.audio.voice_manager import VoiceCustomizationManager, VoiceManagerError


def _wav_sample(duration_seconds: float = 10.0, sample_rate: int = 8_000) -> bytes:
    frame_count = round(duration_seconds * sample_rate)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(b"\x00\x01" * frame_count)
    return buffer.getvalue()


def _record(
    adapter_id: str,
    *,
    api_key: str = "configured-secret",
    eligible: bool | None = None,
    provider_overrides: dict | None = None,
) -> dict:
    details = {
        "minimax_tts": ("speech-2.8-hd", "https://api.minimaxi.com/v1/t2a_v2"),
        "aliyun_bailian_cosyvoice": ("cosyvoice-v3.5-plus", "https://dashscope.aliyuncs.com/api/v1"),
        "volcengine_doubao_voice": ("seed-tts-2.0", "https://openspeech.bytedance.com/api/v3/tts"),
        "xiaomi_mimo_tts": ("mimo-v2.5-tts", "https://api.xiaomimimo.com/v1"),
        "elevenlabs_tts": ("eleven_multilingual_v2", "https://api.elevenlabs.io/v1"),
        "openai_audio_speech": ("gpt-4o-mini-tts", "https://api.openai.com/v1"),
        "google_cloud_tts": ("google-cloud-tts", "https://texttospeech.googleapis.com"),
        "azure_speech_tts": ("azure-neural-tts", "https://example.tts.speech.microsoft.com"),
    }
    model_id, base_url = details[adapter_id]
    media_limits: dict = {"adapterProviderId": adapter_id, "providerModelId": model_id}
    if eligible is not None:
        media_limits["voiceCustomization"] = {"eligible": eligible}
    record = {
        "provider_id": adapter_id,
        "model_id": model_id,
        "provider": {
            "base_url": base_url,
            "api_key": api_key,
            "voice_app_id": "volc-app-id",
            "voice_resource_id": "volc-resource-id",
        },
        "model": {"parameterProfile": adapter_id, "mediaLimits": media_limits},
    }
    record["provider"].update(provider_overrides or {})
    return record


def _manager(
    monkeypatch: pytest.MonkeyPatch,
    adapter_id: str,
    handler,
    *,
    api_key: str = "configured-secret",
    eligible: bool | None = None,
    provider_overrides: dict | None = None,
) -> VoiceCustomizationManager:
    monkeypatch.setattr(
        "core.audio.voice_manager.model_control_plane.get_model_record",
        lambda _ref: _record(
            adapter_id,
            api_key=api_key,
            eligible=eligible,
            provider_overrides=provider_overrides,
        ),
    )
    return VoiceCustomizationManager(transport=httpx.MockTransport(handler))


def test_minimax_voice_design_matches_official_request_and_decodes_hex_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.minimaxi.com/v1/voice_design")
        assert request.headers["authorization"] == "Bearer configured-secret"
        assert json.loads(request.content) == {
            "prompt": "清晰、温暖的成年女性声音",
            "preview_text": "这是音色设计试听。",
            "voice_id": "V8_design_voice",
        }
        return httpx.Response(
            200,
            json={
                "voice_id": "V8_design_voice",
                "trial_audio": b"preview".hex(),
                "base_resp": {"status_code": 0},
            },
        )

    manager = _manager(monkeypatch, "minimax_tts", handler)
    result = asyncio.run(
        manager.design_voice(
            "model-ref",
            prompt="清晰、温暖的成年女性声音",
            preview_text="这是音色设计试听。",
            voice_id="V8_design_voice",
        )
    )

    assert result["voiceId"] == "V8_design_voice"
    assert result["previewAudio"] == "data:audio/mpeg;base64,cHJldmlldw=="
    assert result["assetPolicy"]["inventorySource"] == "remote"


def test_aliyun_list_is_remote_and_design_matches_official_request(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert request.url == httpx.URL(
            "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
        )
        assert request.headers["authorization"] == "Bearer configured-secret"
        if body["input"]["action"] == "list_voice":
            return httpx.Response(
                200,
                json={
                    "output": {
                        "voice_list": [
                            {"voice_id": "cosy-custom-1", "prefix": "announcer"},
                        ]
                    },
                    "code": "0",
                },
            )
        assert body == {
            "model": "voice-enrollment",
            "input": {
                "action": "create_voice",
                "target_model": "cosyvoice-v3.5-plus",
                "voice_prompt": "沉稳的新闻播报女声",
                "preview_text": "欢迎收听今天的新闻。",
                "language_hints": ["zh"],
                "prefix": "newsvoice",
            },
            "parameters": {"sample_rate": 24000, "response_format": "wav"},
        }
        return httpx.Response(
            200,
            json={
                "output": {
                    "voice_id": "cosy-designed-1",
                    "preview_audio": {
                        "data": base64.b64encode(b"wav-preview").decode("ascii"),
                        "sample_rate": 24000,
                        "response_format": "wav",
                    },
                },
                "code": "0",
            },
        )

    manager = _manager(monkeypatch, "aliyun_bailian_cosyvoice", handler)
    listed = asyncio.run(manager.list_voices("model-ref"))
    designed = asyncio.run(
        manager.design_voice(
            "model-ref",
            prompt="沉稳的新闻播报女声",
            preview_text="欢迎收听今天的新闻。",
            voice_id="newsvoice",
        )
    )

    custom = next(voice for voice in listed["voices"] if voice["value"] == "cosy-custom-1")
    assert custom["source"] == "remote"
    assert custom["availability"] == "available"
    assert designed["voiceId"] == "cosy-designed-1"
    assert designed["previewAudio"].startswith("data:audio/wav;base64,")
    assert requests[0] == {
        "model": "voice-enrollment",
        "input": {"action": "list_voice", "page_index": 0, "page_size": 100},
    }


def test_volcengine_design_uses_provider_slot_and_does_not_claim_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://openspeech.bytedance.com/api/v3/tts/voice_design")
        assert request.headers["x-api-app-key"] == "volc-app-id"
        assert request.headers["x-api-access-key"] == "configured-secret"
        assert request.headers["x-api-request-id"]
        assert "x-api-resource-id" not in request.headers
        assert "x-api-connect-id" not in request.headers
        assert json.loads(request.content) == {
            "speaker_id": "SLOT_voice_01",
            "text": "这是火山音色设计试听。",
            "prompt": {"text_prompt": "有亲和力的年轻男声"},
            "language": 0,
        }
        return httpx.Response(
            200,
            headers={"X-Tt-Logid": "volc-design-trace"},
            json={
                "speaker_id": "SLOT_voice_01",
                "demo_audio": "https://example.invalid/volc-design.wav",
                "status": 2,
            },
        )

    manager = _manager(monkeypatch, "volcengine_doubao_voice", handler)
    capabilities = manager.capabilities("model-ref")
    result = asyncio.run(
        manager.design_voice(
            "model-ref",
            prompt="有亲和力的年轻男声",
            preview_text="这是火山音色设计试听。",
            voice_id="SLOT_voice_01",
        )
    )

    assert capabilities["capabilities"]["list"] is False
    assert capabilities["assetPolicy"] == {
        "assetScope": "provider_slot",
        "inventorySource": "local_projection",
        "designFlow": "direct",
        "eligibilityStatus": "available",
        "consentRequired": False,
        "docsUrl": "https://www.volcengine.com/docs/6561/2277844",
        "applicationUrl": "",
    }
    assert result["voiceId"] == "SLOT_voice_01"
    assert result["previewAudioUrl"] == "https://example.invalid/volc-design.wav"
    with pytest.raises(VoiceManagerError) as error:
        asyncio.run(manager.list_voices("model-ref"))
    assert error.value.code == "unsupported_action"


def test_volcengine_design_supports_single_api_key_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "configured-secret"
        assert request.headers["x-api-request-id"]
        assert "x-api-app-key" not in request.headers
        assert "x-api-access-key" not in request.headers
        return httpx.Response(200, json={"speaker_id": "SLOT_voice_02", "status": 2})

    manager = _manager(
        monkeypatch,
        "volcengine_doubao_voice",
        handler,
        provider_overrides={"voice_app_id": "", "voice_resource_id": ""},
    )
    result = asyncio.run(
        manager.design_voice(
            "model-ref",
            prompt="清晰自然的旁白音色",
            preview_text="这是采用单 API Key 鉴权的试听。",
            voice_id="SLOT_voice_02",
        )
    )
    assert result["voiceId"] == "SLOT_voice_02"


def test_volcengine_clone_matches_official_nested_audio_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ignore_ledger(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("core.audio.voice_manager._upsert_ledger_entry", ignore_ledger)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url == httpx.URL("https://openspeech.bytedance.com/api/v3/tts/voice_clone")
        assert body["speaker_id"] == "SLOT_clone_01"
        assert body["audio"]["format"] == "wav"
        assert base64.b64decode(body["audio"]["data"]) == _wav_sample()
        assert body["extra_params"] == {"demo_text": "这是声音复刻后的试听。"}
        return httpx.Response(
            200,
            json={
                "speaker_id": "SLOT_clone_01",
                "speaker_status": [
                    {"status": 2, "demo_audio": "https://example.invalid/volc-clone.wav"}
                ],
                "status": 2,
            },
        )

    manager = _manager(monkeypatch, "volcengine_doubao_voice", handler)
    result = asyncio.run(
        manager.clone_voice(
            "model-ref",
            voice_id="SLOT_clone_01",
            preview_text="这是声音复刻后的试听。",
            filename="sample.wav",
            content_type="audio/wav",
            audio_bytes=_wav_sample(),
        )
    )
    assert result["voiceId"] == "SLOT_clone_01"
    assert result["previewAudioUrl"] == "https://example.invalid/volc-clone.wav"


def test_mimo_design_and_clone_are_ephemeral_and_never_enter_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []

    async def fail_if_persisted(*_args, **_kwargs) -> None:
        raise AssertionError("MiMo ephemeral references must not be written to the voice ledger")

    monkeypatch.setattr("core.audio.voice_manager._upsert_ledger_entry", fail_if_persisted)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.xiaomimimo.com/v1/chat/completions")
        assert request.headers["api-key"] == "configured-secret"
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "audio": {
                                "data": base64.b64encode(b"mimo-preview").decode("ascii"),
                                "media_type": "audio/wav",
                            }
                        }
                    }
                ]
            },
        )

    manager = _manager(monkeypatch, "xiaomi_mimo_tts", handler)
    designed = asyncio.run(
        manager.design_voice(
            "model-ref",
            prompt="梦幻、轻柔的少女声",
            preview_text="这是一段临时参考音色。",
        )
    )
    cloned = asyncio.run(
        manager.clone_voice(
            "model-ref",
            voice_id="",
            preview_text="这是一段参考音频克隆试听。",
            filename="sample.wav",
            content_type="audio/wav",
            audio_bytes=_wav_sample(),
        )
    )

    assert requests[0] == {
        "model": "mimo-v2.5-tts-voicedesign",
        "messages": [
            {"role": "user", "content": "梦幻、轻柔的少女声"},
            {"role": "assistant", "content": "这是一段临时参考音色。"},
        ],
        "audio": {"format": "wav", "optimize_text_preview": True},
    }
    assert requests[1]["model"] == "mimo-v2.5-tts-voiceclone"
    assert requests[1]["audio"]["voice"].startswith("data:audio/wav;base64,")
    assert designed["ephemeral"] is True
    assert cloned["ephemeral"] is True
    assert manager.capabilities("model-ref")["assetPolicy"]["inventorySource"] == "none"


def test_elevenlabs_design_commit_list_delete_and_clone_match_official_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    preview_text = (
        "Welcome to this V8OS voice design preview. The narrator should sound warm, precise, calm, "
        "and natural while reading a complete sentence for an international audience."
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["xi-api-key"] == "configured-secret"
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {"voice_id": "premade-1", "name": "Premade", "category": "premade"},
                        {"voice_id": "custom-1", "name": "Custom", "category": "cloned"},
                    ]
                },
            )
        if request.url.path == "/v1/text-to-voice/design":
            assert json.loads(request.content) == {
                "voice_description": "Warm international narrator",
                "model_id": "eleven_multilingual_ttv_v2",
                "text": preview_text,
            }
            return httpx.Response(
                200,
                json={
                    "previews": [
                        {
                            "generated_voice_id": "generated-1",
                            "audio_base_64": base64.b64encode(b"eleven-preview").decode("ascii"),
                            "media_type": "audio/mpeg",
                        }
                    ]
                },
            )
        if request.url.path == "/v1/text-to-voice":
            assert json.loads(request.content) == {
                "voice_name": "V8 Narrator",
                "voice_description": "Warm international narrator",
                "generated_voice_id": "generated-1",
            }
            return httpx.Response(200, json={"voice_id": "voice-committed-1"})
        if request.url.path == "/v1/voices/add":
            assert b'name="name"' in request.content
            assert b"V8 Clone" in request.content
            assert b'name="files"' in request.content
            return httpx.Response(200, json={"voice_id": "voice-cloned-1"})
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    manager = _manager(monkeypatch, "elevenlabs_tts", handler)
    listed = asyncio.run(manager.list_voices("model-ref"))
    designed = asyncio.run(
        manager.design_voice(
            "model-ref",
            prompt="Warm international narrator",
            preview_text=preview_text,
        )
    )
    committed = asyncio.run(
        manager.commit_design(
            "model-ref",
            generated_voice_id="generated-1",
            voice_name="V8 Narrator",
            voice_description="Warm international narrator",
        )
    )
    cloned = asyncio.run(
        manager.clone_voice(
            "model-ref",
            voice_id="V8 Clone",
            preview_text="",
            filename="sample.wav",
            content_type="audio/wav",
            audio_bytes=_wav_sample(),
        )
    )
    deleted = asyncio.run(manager.delete_voice("model-ref", "custom-1"))

    assert listed["voices"][0]["deletable"] is False
    assert listed["voices"][1]["deletable"] is True
    assert designed["candidates"][0]["generatedVoiceId"] == "generated-1"
    assert committed["voiceId"] == "voice-committed-1"
    assert cloned["voiceId"] == "voice-cloned-1"
    assert deleted["voiceId"] == "custom-1"
    assert calls == [
        ("GET", "/v2/voices"),
        ("POST", "/v1/text-to-voice/design"),
        ("POST", "/v1/text-to-voice"),
        ("POST", "/v1/voices/add"),
        ("DELETE", "/v1/voices/custom-1"),
    ]


@pytest.mark.parametrize(
    ("adapter_id", "prompt", "preview_text", "voice_id", "expected_code"),
    [
        ("minimax_tts", "清晰的声音", "试" * 501, "", "preview_text_too_long"),
        ("minimax_tts", "清晰的声音", "试听", "bad", "invalid_voice_id"),
        ("aliyun_bailian_cosyvoice", "新闻播报", "正常试听", "", "voice_id_required"),
        ("aliyun_bailian_cosyvoice", "新闻播报", "试" * 201, "newsvoice", "preview_text_too_long"),
        ("aliyun_bailian_cosyvoice", "新闻播报", "正常试听", "invalid-prefix", "invalid_voice_id"),
        ("volcengine_doubao_voice", "火山音色" * 51, "正常试听", "SLOT_voice_01", "design_prompt_too_long"),
        ("volcengine_doubao_voice", "火山音色", "试" * 301, "SLOT_voice_01", "preview_text_too_long"),
        ("elevenlabs_tts", "Too short", "x" * 100, "", "design_prompt_too_short"),
        ("elevenlabs_tts", "x" * 1001, "y" * 100, "", "design_prompt_too_long"),
        (
            "elevenlabs_tts",
            "Warm international narrator",
            "Too short for the provider",
            "",
            "preview_text_too_short",
        ),
    ],
)
def test_provider_specific_design_validation_blocks_invalid_requests_before_network(
    monkeypatch: pytest.MonkeyPatch,
    adapter_id: str,
    prompt: str,
    preview_text: str,
    voice_id: str,
    expected_code: str,
) -> None:
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500)

    manager = _manager(monkeypatch, adapter_id, handler)
    with pytest.raises(VoiceManagerError) as error:
        asyncio.run(
            manager.design_voice(
                "model-ref",
                prompt=prompt,
                preview_text=preview_text,
                voice_id=voice_id,
            )
        )

    assert error.value.code == expected_code
    assert request_count == 0


def test_minimax_design_prompt_is_not_truncated_by_an_undocumented_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = "清晰、自然、富有层次的声音。" * 50

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["prompt"] == prompt
        return httpx.Response(
            200,
            json={"voice_id": "generated-voice", "trial_audio": b"preview".hex(), "base_resp": {"status_code": 0}},
        )

    manager = _manager(monkeypatch, "minimax_tts", handler)
    result = asyncio.run(
        manager.design_voice(
            "model-ref",
            prompt=prompt,
            preview_text="试听文本",
        )
    )

    assert result["voiceId"] == "generated-voice"


@pytest.mark.parametrize(
    "adapter_id",
    [
        "minimax_tts",
        "aliyun_bailian_cosyvoice",
        "volcengine_doubao_voice",
        "xiaomi_mimo_tts",
        "elevenlabs_tts",
    ],
)
def test_capabilities_work_without_key_but_operations_fail_before_network(
    monkeypatch: pytest.MonkeyPatch,
    adapter_id: str,
) -> None:
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500)

    manager = _manager(monkeypatch, adapter_id, handler, api_key="")

    capabilities = manager.capabilities("model-ref")
    assert capabilities["credentialStatus"] == "missing"
    assert capabilities["capabilities"]["design"] is True
    assert capabilities["designConstraints"]["prompt"]["required"] is True
    assert "configured-secret" not in str(capabilities)
    with pytest.raises(VoiceManagerError) as error:
        asyncio.run(
            manager.design_voice(
                "model-ref",
                prompt="A clear voice",
                preview_text="Preview",
            )
        )
    assert error.value.code == "credential_missing"
    assert request_count == 0


@pytest.mark.parametrize(
    ("adapter_id", "expected_constraints"),
    [
        (
            "minimax_tts",
            {
                "prompt": {"required": True, "minChars": 1, "maxChars": None},
                "previewText": {"required": True, "minChars": 1, "maxChars": 500},
                "voiceId": {
                    "required": False,
                    "role": "custom_id",
                    "minChars": 8,
                    "maxChars": 256,
                    "format": "ascii_identifier",
                },
            },
        ),
        (
            "aliyun_bailian_cosyvoice",
            {
                "prompt": {"required": True, "minChars": 1, "maxChars": 500},
                "previewText": {"required": True, "minChars": 1, "maxChars": 200},
                "voiceId": {
                    "required": True,
                    "role": "prefix",
                    "minChars": 1,
                    "maxChars": 10,
                    "format": "ascii_alphanumeric",
                },
            },
        ),
        (
            "volcengine_doubao_voice",
            {
                "prompt": {"required": True, "minChars": 1, "maxChars": 200},
                "previewText": {"required": True, "minChars": 1, "maxChars": 300},
                "voiceId": {
                    "required": True,
                    "role": "provider_slot",
                    "minChars": None,
                    "maxChars": None,
                    "format": "",
                },
            },
        ),
        (
            "elevenlabs_tts",
            {
                "prompt": {"required": True, "minChars": 20, "maxChars": 1000},
                "previewText": {"required": True, "minChars": 100, "maxChars": 1000},
                "voiceId": {
                    "required": False,
                    "role": "none",
                    "minChars": None,
                    "maxChars": None,
                    "format": "",
                },
            },
        ),
    ],
)
def test_capability_contract_exposes_provider_design_constraints(
    monkeypatch: pytest.MonkeyPatch,
    adapter_id: str,
    expected_constraints: dict,
) -> None:
    manager = _manager(monkeypatch, adapter_id, lambda _request: httpx.Response(500))

    assert manager.capabilities("model-ref")["designConstraints"] == expected_constraints


@pytest.mark.parametrize(
    ("adapter_id", "expected_provider"),
    [
        ("openai_audio_speech", "openai_custom_voice"),
        ("google_cloud_tts", "google_instant_custom_voice"),
        ("azure_speech_tts", "azure_personal_voice"),
    ],
)
def test_qualification_only_providers_never_claim_operational_capabilities(
    monkeypatch: pytest.MonkeyPatch,
    adapter_id: str,
    expected_provider: str,
) -> None:
    manager = _manager(monkeypatch, adapter_id, lambda _request: httpx.Response(500), api_key="", eligible=False)
    unavailable = manager.capabilities("model-ref")
    assert unavailable["provider"] == expected_provider
    assert not any(unavailable["capabilities"].values())
    assert unavailable["assetPolicy"]["eligibilityStatus"] == "requires_approval"
    assert unavailable["assetPolicy"]["consentRequired"] is True
    assert unavailable["assetPolicy"]["applicationUrl"].startswith("https://")

    eligible_manager = _manager(
        monkeypatch,
        adapter_id,
        lambda _request: httpx.Response(500),
        api_key="",
        eligible=True,
    )
    assert eligible_manager.capabilities("model-ref")["assetPolicy"]["eligibilityStatus"] == "eligible"
