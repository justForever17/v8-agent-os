import inspect

from core.audio.audio_config import _normalize_audio_config
from core.audio.stt_provider import CustomSTTProvider, ModelRefSTTProvider, STTManager
from core.audio.tts_provider import (
    CustomTTSProvider,
    ModelRefTTSProvider,
    TTSManager,
    _audio_response_paths_for_protocol,
    _decode_audio_value,
    _decode_volcengine_audio_chunks,
    _extract_first_json_path,
    _extract_json_path,
    _model_ref_tts_provider_from_config,
)


def test_audio_config_preserves_model_ref_sections():
    normalized = _normalize_audio_config(
        {
            "stt": {
                "active_provider": "model_ref",
                "model_ref": {
                    "modelRef": "google_gemini_live_audio::gemini-3.1-flash-live-preview",
                    "mode": "audio_input",
                    "language": "auto",
                    "prompt": "转写为中文",
                },
            },
            "tts": {
                "active_provider": "model_ref",
                "model_ref": {
                    "modelRef": "google_gemini_tts::gemini-3.1-flash-tts-preview",
                    "voice": "Kore",
                    "format": "wav",
                    "speed": "1.0",
                },
            },
        }
    )

    assert normalized["stt"]["model_ref"]["modelRef"] == "google_gemini_live_audio::gemini-3.1-flash-live-preview"
    assert normalized["stt"]["model_ref"]["language"] == "auto"
    assert normalized["tts"]["model_ref"]["modelRef"] == "google_gemini_tts::gemini-3.1-flash-tts-preview"
    assert normalized["tts"]["model_ref"]["voice"] == "Kore"


def test_audio_config_keeps_legacy_custom_api_url_mapping():
    normalized = _normalize_audio_config(
        {
            "stt": {"providers": {"custom": {"api_url": "http://stt.local"}}},
            "tts": {"providers": {"custom": {"api_url": "http://tts.local"}}},
        }
    )

    assert normalized["stt"]["providers"]["custom"]["endpoint"] == "http://stt.local"
    assert normalized["tts"]["custom"]["endpoint"] == "http://tts.local"


def test_audio_config_preserves_custom_protocol_fields():
    normalized = _normalize_audio_config(
        {
            "stt": {
                "active_provider": "custom",
                "providers": {
                    "custom": {
                        "endpoint": "https://stt.example/v1/audio/transcriptions",
                        "api_key": "sk-test",
                        "protocol": "openai_transcription",
                        "model": "whisper-1",
                        "language": "zh-CN",
                        "fileField": "audio",
                        "responseTextPath": "data.text",
                        "headers": {"X-Test": "1"},
                    }
                },
            },
            "tts": {
                "active_provider": "custom",
                "custom": {
                    "endpoint": "https://api.minimaxi.com/v1/t2a_v2",
                    "api_key": "sk-test",
                    "voice": "male-qn-qingse",
                    "protocol": "minimax_t2a_v2",
                    "model": "speech-2.8-turbo",
                    "format": "mp3",
                    "speed": "1.1",
                    "responseAudioPath": "data.audio",
                    "headers": {"X-Test": "1"},
                },
            },
        }
    )

    custom_stt = normalized["stt"]["providers"]["custom"]
    assert custom_stt["protocol"] == "openai_transcription"
    assert custom_stt["model"] == "whisper-1"
    assert custom_stt["fileField"] == "audio"
    assert custom_stt["responseTextPath"] == "data.text"
    assert custom_stt["headers"] == {"X-Test": "1"}

    custom_tts = normalized["tts"]["custom"]
    assert custom_tts["protocol"] == "minimax_t2a_v2"
    assert custom_tts["model"] == "speech-2.8-turbo"
    assert custom_tts["responseAudioPath"] == "data.audio"
    assert custom_tts["headers"] == {"X-Test": "1"}


def test_custom_audio_managers_create_protocol_providers(monkeypatch):
    class STTFakeConfigManager:
        @staticmethod
        def get_config():
            return {
                "stt": {
                    "active_provider": "custom",
                    "providers": {
                        "custom": {
                            "endpoint": "https://stt.example/v1/audio/transcriptions",
                            "protocol": "json_base64",
                            "model": "custom-stt",
                            "language": "zh-CN",
                            "fileField": "audio",
                            "responseTextPath": "result.text",
                            "headers": {"X-Test": "1"},
                        }
                    },
                }
            }

    class TTSFakeConfigManager:
        @staticmethod
        def get_config():
            return {
                "tts": {
                    "active_provider": "custom",
                    "custom": {
                        "endpoint": "https://api.minimaxi.com/v1/t2a_v2",
                        "protocol": "minimax_t2a_v2",
                        "model": "speech-2.8-turbo",
                        "voice": "male-qn-qingse",
                        "format": "mp3",
                        "speed": "1.1",
                        "responseAudioPath": "data.audio",
                        "headers": {"X-Test": "1"},
                    },
                }
            }

    monkeypatch.setitem(STTManager.get_provider.__globals__, "AudioConfigManager", STTFakeConfigManager)
    monkeypatch.setitem(TTSManager.get_provider.__globals__, "AudioConfigManager", TTSFakeConfigManager)

    stt_provider = STTManager.get_provider()
    tts_provider = TTSManager.get_provider()

    assert isinstance(stt_provider, CustomSTTProvider)
    assert stt_provider.protocol == "json_base64"
    assert stt_provider.file_field == "audio"
    assert stt_provider.response_text_path == "result.text"
    assert stt_provider.extra_headers == {"X-Test": "1"}

    assert isinstance(tts_provider, CustomTTSProvider)
    assert tts_provider.protocol == "minimax_t2a_v2"
    assert tts_provider.model == "speech-2.8-turbo"
    assert tts_provider.response_audio_path == "data.audio"
    assert tts_provider.extra_headers == {"X-Test": "1"}


def test_model_ref_audio_providers_are_explicit_not_mock(monkeypatch):
    class STTFakeConfigManager:
        @staticmethod
        def get_config():
            return {"stt": {"active_provider": "model_ref", "model_ref": {"modelRef": "google_gemini_live_audio::gemini-3.1-flash-live-preview"}}}

    class TTSFakeConfigManager:
        @staticmethod
        def get_config():
            return {"tts": {"active_provider": "model_ref", "model_ref": {"modelRef": "google_gemini_tts::gemini-3.1-flash-tts-preview"}}}

    monkeypatch.setitem(STTManager.get_provider.__globals__, "AudioConfigManager", STTFakeConfigManager)
    monkeypatch.setitem(TTSManager.get_provider.__globals__, "AudioConfigManager", TTSFakeConfigManager)

    assert isinstance(STTManager.get_provider(), ModelRefSTTProvider)
    assert isinstance(TTSManager.get_provider(), ModelRefTTSProvider)


def test_model_ref_minimax_tts_resolves_to_system_tts_provider():
    provider = _model_ref_tts_provider_from_config(
        model_ref="minimax-cn::t2a_v2%2Fspeech-2.8-hd",
        voice="female-shaonv",
        audio_format="mp3",
        config={
            "providers": {
                "minimax-cn": {
                    "provider": {
                        "base_url": "https://api.minimaxi.com/v1",
                        "api_key": "sk-test",
                    },
                    "models": {
                        "t2a_v2/speech-2.8-hd": {
                            "modelType": "VOICE",
                            "parameterProfile": "minimax_tts",
                            "mediaLimits": {
                                "adapterProviderId": "minimax_tts",
                                "apiStandard": "minimax_tts",
                                "providerModelId": "speech-2.8-hd",
                                "operationKinds": ["voice.tts"],
                            },
                        }
                    },
                }
            }
        },
    )

    assert isinstance(provider, CustomTTSProvider)
    assert provider.endpoint == "https://api.minimaxi.com/v1/t2a_v2"
    assert provider.protocol == "minimax_t2a_v2"
    assert provider.model == "speech-2.8-hd"
    assert provider.voice == "female-shaonv"
    assert provider.audio_format == "mp3"


def test_model_ref_aliyun_cosyvoice_resolves_to_system_tts_provider():
    provider = _model_ref_tts_provider_from_config(
        model_ref="dashscope::services%2Faudio%2Ftts%2FSpeechSynthesizer%2Fcosyvoice-v3-flash",
        voice="longxiaochun",
        audio_format="wav",
        config={
            "providers": {
                "dashscope": {
                    "provider": {
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "api_key": "sk-test",
                    },
                    "models": {
                        "services/audio/tts/SpeechSynthesizer/cosyvoice-v3-flash": {
                            "modelType": "VOICE",
                            "parameterProfile": "dashscope_cosyvoice_tts",
                            "mediaLimits": {
                                "adapterProviderId": "aliyun_bailian_cosyvoice",
                                "apiStandard": "dashscope_cosyvoice_tts",
                                "providerModelId": "cosyvoice-v3-flash",
                                "submitPath": "/services/audio/tts/SpeechSynthesizer",
                                "operationKinds": ["voice.tts", "voice.clone"],
                            },
                        }
                    },
                }
            }
        },
    )

    assert isinstance(provider, CustomTTSProvider)
    assert provider.endpoint == "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
    assert provider.protocol == "aliyun_cosyvoice_tts"
    assert provider.model == "cosyvoice-v3-flash"
    assert provider.voice == "longxiaochun"
    assert provider.audio_format == "wav"


def test_model_ref_volcengine_doubao_tts_resolves_to_system_tts_provider():
    provider = _model_ref_tts_provider_from_config(
        model_ref="volcengine-ark::audio%2Fspeech%2Fdoubao-voice-synthesis-2-0",
        voice="zh_female_shuangkuaisisi_moon_bigtts",
        audio_format="mp3",
        config={
            "providers": {
                "volcengine-ark": {
                    "provider": {
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                        "api_key": "ak-test",
                        "voice_app_id": "app-test",
                        "voice_resource_id": "resource-test",
                    },
                    "models": {
                        "audio/speech/doubao-voice-synthesis-2-0": {
                            "modelType": "VOICE",
                            "parameterProfile": "volcengine_ark_voice",
                            "mediaLimits": {
                                "adapterProviderId": "volcengine_doubao_voice",
                                "apiStandard": "volcengine_ark_voice",
                                "providerModelId": "doubao-voice-synthesis-2-0",
                                "operationKinds": ["voice.tts", "voice.clone"],
                            },
                        }
                    },
                }
            }
        },
    )

    assert isinstance(provider, CustomTTSProvider)
    assert provider.endpoint == "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    assert provider.protocol == "volcengine_doubao_tts"
    assert provider.model == "doubao-voice-synthesis-2-0"
    assert provider.voice == "zh_female_shuangkuaisisi_moon_bigtts"
    assert provider.app_id == "app-test"
    assert provider.resource_id == "resource-test"


def test_model_ref_tts_stream_is_async_generator():
    provider = ModelRefTTSProvider(
        "minimax-cn::t2a_v2%2Fspeech-2.8-hd",
        voice="female-shaonv",
        audio_format="mp3",
    )

    stream = provider.synthesize_stream("你好")

    assert inspect.isasyncgen(stream)


def test_minimax_tts_protocol_builds_official_payload_and_decodes_audio():
    provider = CustomTTSProvider(
        endpoint="https://api.minimaxi.com/v1/t2a_v2",
        protocol="minimax_t2a_v2",
        model="speech-2.8-turbo",
        voice="Chinese (Mandarin)_News_Anchor",
        audio_format="mp3",
        speed="1.2",
        response_audio_path="data.audio",
    )

    payload = provider._build_payload("你好，V8OS。")

    assert payload["model"] == "speech-2.8-turbo"
    assert payload["text"] == "你好，V8OS。"
    assert payload["stream"] is False
    assert payload["output_format"] == "hex"
    assert payload["voice_setting"]["voice_id"] == "Chinese (Mandarin)_News_Anchor"
    assert payload["voice_setting"]["speed"] == 1.2
    assert payload["audio_setting"]["format"] == "mp3"

    response_payload = {"data": {"audio": "000102ff"}}
    assert _extract_json_path(response_payload, "data.audio") == "000102ff"
    assert _decode_audio_value("000102ff") == b"\x00\x01\x02\xff"


def test_aliyun_cosyvoice_protocol_builds_payload_and_decodes_audio_location():
    provider = CustomTTSProvider(
        endpoint="https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
        protocol="aliyun_cosyvoice_tts",
        model="cosyvoice-v3-flash",
        voice="longxiaochun",
        audio_format="wav",
    )

    payload = provider._build_payload("你好，V8OS。")

    assert payload == {
        "model": "cosyvoice-v3-flash",
        "input": {
            "text": "你好，V8OS。",
            "voice": "longxiaochun",
            "format": "wav",
            "sample_rate": 24000,
        },
    }
    response_payload = {"output": {"audio": {"url": "https://example.com/audio.wav"}}}
    assert _extract_first_json_path(
        response_payload,
        _audio_response_paths_for_protocol("aliyun_cosyvoice_tts"),
    ) == "https://example.com/audio.wav"


def test_volcengine_tts_protocol_builds_headers_payload_and_decodes_sse_audio():
    provider = CustomTTSProvider(
        endpoint="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
        protocol="volcengine_doubao_tts",
        api_key="ak-test",
        app_id="app-test",
        resource_id="resource-test",
        voice="zh_female_shuangkuaisisi_moon_bigtts",
        audio_format="mp3",
    )

    headers = provider._build_headers()
    payload = provider._build_payload("你好，V8OS。")

    assert headers["X-Api-App-Key"] == "app-test"
    assert headers["X-Api-Access-Key"] == "ak-test"
    assert headers["X-Api-Resource-Id"] == "resource-test"
    assert headers["Content-Type"] == "application/json"
    assert payload["user"]["uid"] == "v8-agent-os"
    assert payload["req_params"]["text"] == "你好，V8OS。"
    assert payload["req_params"]["speaker"] == "zh_female_shuangkuaisisi_moon_bigtts"
    assert payload["req_params"]["audio_params"]["format"] == "mp3"

    sse_payload = 'data: {"data":{"audio":"AAEC"}}\n\ndata: [DONE]\n'
    assert _decode_volcengine_audio_chunks(sse_payload) == [b"\x00\x01\x02"]
