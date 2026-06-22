import edge_tts
import aiohttp
from abc import ABC, abstractmethod
from typing import AsyncGenerator

from .audio_config import AudioConfigManager

class TTSProvider(ABC):
    """
    通用语音合成 (Text-to-Speech) 接口类
    """
    @abstractmethod
    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """
        流式合成文本，返回音频二进制流的异步生成器
        :param text: 要合成的文本
        :return: bytes 流
        """
        pass

class EdgeTTSProvider(TTSProvider):
    """
    基于 edge-tts 的免费高质量流式语音合成
    """
    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural", rate: str = "+0%", volume: str = "+0%"):
        self.voice = voice
        self.rate = rate
        self.volume = volume

    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, volume=self.volume)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]

class CustomTTSProvider(TTSProvider):
    """
    自建 TTS 供应商 (基于 HTTP/SSE 流式请求返回音频二进制流)
    """
    def __init__(self, endpoint: str, api_key: str = None, voice: str = None):
        self.endpoint = endpoint
        self.api_key = api_key
        self.voice = voice

    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            
        payload = {
            "text": text
        }
        if self.voice:
            payload["voice"] = self.voice

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.endpoint, json=payload, headers=headers) as response:
                    if response.status == 200:
                        async for chunk in response.content.iter_any():
                            if chunk:
                                yield chunk
                    else:
                        print(f"[CustomTTS] Failed with status {response.status}")
        except Exception as e:
            print(f"[CustomTTS] Stream Exception: {e}")

class MockTTSProvider(TTSProvider):
    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        # Fallback， yield empty
        yield b""

class ModelRefTTSProvider(TTSProvider):
    def __init__(self, model_ref: str = ""):
        self.model_ref = model_ref

    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        target = self.model_ref or "未选择模型"
        raise RuntimeError(
            f"TTS 已配置为使用已配置模型替代（{target}），但当前音频模型合成适配器尚未启用。"
            "请改用 Edge TTS / 自建 TTS API，或补齐 model_ref TTS 适配器。"
        )

class TTSManager:
    @staticmethod
    def get_provider() -> TTSProvider:
        config = AudioConfigManager.get_config()
        tts_conf = config.get("tts", {})
        active = tts_conf.get("active_provider", "edge-tts")
        
        if active == "edge-tts":
            edge_conf = tts_conf.get("edge_tts", {})
            return EdgeTTSProvider(
                voice=edge_conf.get("voice", "zh-CN-XiaoxiaoNeural"),
                rate=edge_conf.get("rate", "+0%"),
                volume=edge_conf.get("volume", "+0%")
            )
        elif active == "custom":
            cust_conf = tts_conf.get("custom", {})
            ep = cust_conf.get("endpoint")
            if ep:
                return CustomTTSProvider(
                    endpoint=ep,
                    api_key=cust_conf.get("api_key"),
                    voice=cust_conf.get("voice")
                )
        elif active == "model_ref":
            model_ref = (tts_conf.get("model_ref") or {}).get("modelRef") or ""
            return ModelRefTTSProvider(str(model_ref))
        
        return MockTTSProvider()
