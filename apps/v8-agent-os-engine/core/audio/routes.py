from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import io

from .audio_config import AudioConfigManager
from .stt_provider import STTManager
from .tts_provider import TTSManager

router = APIRouter(prefix="/v1/audio", tags=["Audio"])

class TTSRequest(BaseModel):
    text: str

@router.get("/config")
async def get_audio_config():
    """获取当前 Audio 配置"""
    return AudioConfigManager.get_config()

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
