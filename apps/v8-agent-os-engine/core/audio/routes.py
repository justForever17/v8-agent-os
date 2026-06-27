import re
from typing import Any

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import io

from core.model_capability_registry import model_capability_registry, normalize_model_capability_key
from core.model_control_plane import model_control_plane

from .audio_config import AudioConfigManager
from .stt_provider import STTManager
from .tts_provider import TTSManager

router = APIRouter(prefix="/v1/audio", tags=["Audio"])

class TTSRequest(BaseModel):
    text: str


def _compact_model_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _tokens_from(value: Any) -> set[str]:
    tokens: set[str] = set()
    if value is None:
        return tokens
    if isinstance(value, dict):
        for key, enabled in value.items():
            if enabled:
                tokens.add(normalize_model_capability_key(key))
        return tokens
    if isinstance(value, (list, tuple, set)):
        for item in value:
            tokens.update(_tokens_from(item))
        return tokens
    text = normalize_model_capability_key(value)
    if text:
        tokens.add(text)
    return tokens


def _metadata_supports_audio_input(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False

    explicit_bool_keys = {
        "audioInput",
        "inputAudio",
        "supportsAudioInput",
        "supportsAudioUnderstanding",
        "audio_understanding",
    }
    for key in explicit_bool_keys:
        if metadata.get(key) is True:
            return True

    input_tokens: set[str] = set()
    for key in (
        "inputModalities",
        "input_modalities",
        "supportedInputModalities",
        "supported_inputs",
        "inputs",
        "modalities",
    ):
        input_tokens.update(_tokens_from(metadata.get(key)))
    if input_tokens.intersection({"audio", "input-audio", "audio-input"}):
        return True

    capability_tokens: set[str] = set()
    for key in ("capabilities", "capabilityTags", "tags", "features"):
        capability_tokens.update(_tokens_from(metadata.get(key)))
    if capability_tokens.intersection({"audio-input", "input-audio", "audio-understanding", "speech-understanding"}):
        return True
    if "audio" in capability_tokens and capability_tokens.intersection({"chat", "vision", "multimodal", "text"}):
        return True

    nested = metadata.get("metadata")
    if isinstance(nested, dict) and nested is not metadata:
        return _metadata_supports_audio_input(nested)
    return False


def _known_audio_input_model(model_id: str) -> bool:
    normalized = normalize_model_capability_key(model_id)
    compact = _compact_model_key(model_id)
    if not normalized and not compact:
        return False
    if any(marker in compact for marker in ("tts", "speech28", "speech26", "texttospeech")):
        return False
    known_exact_or_family = (
        "doubao-seed-2-0-lite",
        "doubao-seed-2-1-pro",
        "mimo-v2-5",
        "gpt-4o-audio",
        "gemini-2-5-flash",
        "gemini-2-5-pro",
    )
    if any(marker in normalized for marker in known_exact_or_family):
        return True
    compact_markers = (
        "doubaoseed20lite",
        "doubaoseed21pro",
        "mimov25",
        "gpt4oaudio",
        "gemini25flash",
        "gemini25pro",
    )
    return any(marker in compact for marker in compact_markers)


def _stt_status(config: dict[str, Any]) -> dict[str, Any]:
    stt = config.get("stt") if isinstance(config.get("stt"), dict) else {}
    active = str(stt.get("active_provider") or "").strip()
    providers = stt.get("providers") if isinstance(stt.get("providers"), dict) else {}
    if active == "baidu":
        provider = providers.get("baidu") if isinstance(providers.get("baidu"), dict) else {}
        usable = bool(str(provider.get("api_key") or "").strip() and str(provider.get("secret_key") or "").strip())
        return {"usable": usable, "provider": active, "reason": "" if usable else "baidu_credentials_missing"}
    if active == "custom":
        provider = providers.get("custom") if isinstance(providers.get("custom"), dict) else {}
        usable = bool(str(provider.get("endpoint") or "").strip())
        return {"usable": usable, "provider": active, "reason": "" if usable else "custom_endpoint_missing"}
    if active == "model_ref":
        return {
            "usable": False,
            "provider": active,
            "reason": "model_ref_stt_adapter_not_enabled",
        }
    return {"usable": False, "provider": active or "none", "reason": "stt_provider_not_configured"}


def _vision_audio_status() -> dict[str, Any]:
    try:
        resolution = model_control_plane.resolve_model_for_role("vision")
    except Exception as exc:
        return {"usable": False, "reason": f"vision_model_resolution_failed: {exc}"}

    model_id = str(resolution.get("resolvedModelId") or resolution.get("rawModelId") or "").strip()
    model_ref = str(resolution.get("resolvedModelRef") or "").strip()
    provider_id = str(resolution.get("resolvedProviderId") or "").strip()
    model_meta = resolution.get("resolvedModel") if isinstance(resolution.get("resolvedModel"), dict) else {}
    registry_meta = model_capability_registry.find(model_id) if model_id else None
    usable = (
        _metadata_supports_audio_input(model_meta)
        or _metadata_supports_audio_input(registry_meta)
        or _known_audio_input_model(model_id)
    )
    return {
        "usable": bool(usable),
        "modelId": model_id,
        "modelRef": model_ref,
        "providerId": provider_id,
        "reason": "" if usable else "vision_model_has_no_known_audio_input",
    }


def build_audio_input_status() -> dict[str, Any]:
    config = AudioConfigManager.get_config()
    stt = _stt_status(config)
    vision_audio = _vision_audio_status()
    route = "stt" if stt.get("usable") else "vision_audio" if vision_audio.get("usable") else "unavailable"
    return {
        "route": route,
        "stt": stt,
        "visionAudio": vision_audio,
    }

@router.get("/config")
async def get_audio_config():
    """获取当前 Audio 配置"""
    return AudioConfigManager.get_config()


@router.get("/input-status")
async def get_audio_input_status():
    """返回客户端语音输入应走的路径：STT、音频多模态或不可用。"""
    return build_audio_input_status()

@router.post("/config")
async def set_audio_config(config: dict):
    """更新 Audio 配置"""
    AudioConfigManager.save_config(config)
    return {"status": "success", "message": "Audio config saved successfully"}

@router.post("/tts/stream")
async def tts_stream(request: TTSRequest):
    """
    接收文本并返回流式语音文件
    依据 `config.json#audio` 自动路由到底层 Edge-TTS 或其他 Provider
    """
    if not request.text:
        raise HTTPException(status_code=400, detail="Text cannot be empty")
        
    provider = TTSManager.get_provider()
    
    # 强制让 FastAPI 识别为音频流的 MediaType
    return StreamingResponse(
        provider.synthesize_stream(request.text),
        media_type="audio/mpeg"
    )

@router.post("/stt/transcribe")
async def stt_transcribe(
    file: UploadFile = File(...),
):
    """
    接收前端录制的语音，转换成纯文本
    使用统一 `STTManager`
    """
    audio_bytes = await file.read()
    format_type = file.filename.split('.')[-1] if '.' in file.filename else "wav"
    
    provider = STTManager.get_provider()
    
    try:
        text = await provider.transcribe(audio_bytes, format_type)
        return {"text": text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
