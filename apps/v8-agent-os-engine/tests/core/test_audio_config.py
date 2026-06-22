from core.audio.audio_config import _normalize_audio_config
from core.audio.stt_provider import CustomSTTProvider, ModelRefSTTProvider, STTManager
from core.audio.tts_provider import CustomTTSProvider, ModelRefTTSProvider, TTSManager


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
