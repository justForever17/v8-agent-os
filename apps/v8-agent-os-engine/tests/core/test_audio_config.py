from core.audio.audio_config import _normalize_audio_config
from core.audio.stt_provider import ModelRefSTTProvider, STTManager
from core.audio.tts_provider import ModelRefTTSProvider, TTSManager


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
