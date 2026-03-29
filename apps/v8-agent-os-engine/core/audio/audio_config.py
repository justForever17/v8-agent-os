from copy import deepcopy

from core.storage import storage

AUDIO_CONFIG_PATH = storage.base_dir / "config.json"

DEFAULT_AUDIO_CONFIG = {
    "stt": {
        "active_provider": "baidu",
        "providers": {
            "baidu": {
                "api_key": "",
                "secret_key": ""
            },
            "volcengine": {
                "app_id": "",
                "access_token": "",
                "cluster": ""
            },
            "custom": {
                "endpoint": "",
                "api_key": ""
            }
        }
    },
    "tts": {
        "active_provider": "edge-tts",
        "edge_tts": {
            "voice": "zh-CN-XiaoxiaoNeural",
            "rate": "+0%",
            "volume": "+0%"
        },
        "custom": {
            "endpoint": "",
            "api_key": "",
            "voice": ""
        }
    }
}


def _normalize_audio_config(config: dict | None) -> dict:
    raw = config if isinstance(config, dict) else {}
    normalized = deepcopy(DEFAULT_AUDIO_CONFIG)

    stt_raw = raw.get("stt") if isinstance(raw.get("stt"), dict) else {}
    normalized["stt"]["active_provider"] = stt_raw.get("active_provider", normalized["stt"]["active_provider"])
    stt_providers_raw = stt_raw.get("providers") if isinstance(stt_raw.get("providers"), dict) else {}
    for provider_name, provider_defaults in normalized["stt"]["providers"].items():
        incoming = stt_providers_raw.get(provider_name) if isinstance(stt_providers_raw.get(provider_name), dict) else {}
        merged = {**provider_defaults, **incoming}
        if provider_name == "custom" and incoming.get("api_url") and not merged.get("endpoint"):
            merged["endpoint"] = incoming["api_url"]
        normalized["stt"]["providers"][provider_name] = merged

    tts_raw = raw.get("tts") if isinstance(raw.get("tts"), dict) else {}
    normalized["tts"]["active_provider"] = tts_raw.get("active_provider", normalized["tts"]["active_provider"])

    legacy_tts_providers = tts_raw.get("providers") if isinstance(tts_raw.get("providers"), dict) else {}
    legacy_edge = legacy_tts_providers.get("edge-tts") if isinstance(legacy_tts_providers.get("edge-tts"), dict) else {}
    legacy_custom = legacy_tts_providers.get("custom") if isinstance(legacy_tts_providers.get("custom"), dict) else {}
    edge_tts_raw = tts_raw.get("edge_tts") if isinstance(tts_raw.get("edge_tts"), dict) else {}
    custom_tts_raw = tts_raw.get("custom") if isinstance(tts_raw.get("custom"), dict) else {}

    normalized["tts"]["edge_tts"] = {
        **normalized["tts"]["edge_tts"],
        **legacy_edge,
        **edge_tts_raw,
    }
    normalized["tts"]["custom"] = {
        **normalized["tts"]["custom"],
        **legacy_custom,
        **custom_tts_raw,
    }
    if legacy_custom.get("api_url") and not normalized["tts"]["custom"].get("endpoint"):
        normalized["tts"]["custom"]["endpoint"] = legacy_custom["api_url"]

    return normalized

class AudioConfigManager:
    @staticmethod
    def get_config() -> dict:
        """读取当前的 Audio 配置对象，如果不存在则写入默认对象"""
        if not AUDIO_CONFIG_PATH.exists():
            AudioConfigManager.save_config(DEFAULT_AUDIO_CONFIG)
            return DEFAULT_AUDIO_CONFIG
        
        try:
            config = storage.read_json("audio_config.json")
            normalized = _normalize_audio_config(config)
            if normalized != config:
                AudioConfigManager.save_config(normalized)
            return normalized
        except Exception as e:
            print(f"Error loading audio config: {e}. Returning default.")
            return DEFAULT_AUDIO_CONFIG
            
    @staticmethod
    def save_config(config: dict):
        """保存配置对象到本地磁盘"""
        AUDIO_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        storage.write_json("audio_config.json", _normalize_audio_config(config))
