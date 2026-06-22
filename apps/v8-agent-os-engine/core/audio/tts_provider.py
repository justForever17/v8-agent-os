import edge_tts
import aiohttp
import json
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
    def __init__(
        self,
        endpoint: str,
        api_key: str = None,
        voice: str = None,
        protocol: str = "json_audio_stream",
        model: str = "",
        audio_format: str = "mp3",
        speed: str = "",
        response_audio_path: str = "",
        headers: dict | str | None = None,
    ):
        self.endpoint = endpoint
        self.api_key = api_key
        self.voice = voice
        self.protocol = protocol or "json_audio_stream"
        self.model = model or ""
        self.audio_format = audio_format or "mp3"
        self.speed = speed or ""
        self.response_audio_path = response_audio_path or ""
        self.extra_headers = _coerce_headers(headers)

    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        headers = dict(self.extra_headers)
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        payload = self._build_payload(text)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.endpoint, json=payload, headers=headers) as response:
                    if response.status == 200:
                        if self.response_audio_path or self.protocol == "minimax_t2a_v2":
                            response_json = await response.json(content_type=None)
                            audio_value = _extract_json_path(response_json, self.response_audio_path or "data.audio")
                            if isinstance(audio_value, str) and audio_value.startswith(("http://", "https://")):
                                async with session.get(audio_value) as audio_response:
                                    if audio_response.status == 200:
                                        async for chunk in audio_response.content.iter_any():
                                            if chunk:
                                                yield chunk
                            else:
                                audio_bytes = _decode_audio_value(audio_value)
                                if audio_bytes:
                                    yield audio_bytes
                        else:
                            async for chunk in response.content.iter_any():
                                if chunk:
                                    yield chunk
                    else:
                        print(f"[CustomTTS] Failed with status {response.status}")
        except Exception as e:
            print(f"[CustomTTS] Stream Exception: {e}")

    def _build_payload(self, text: str) -> dict:
        if self.protocol == "openai_speech":
            payload = {
                "model": self.model or "gpt-4o-mini-tts",
                "input": text,
                "voice": self.voice or "alloy",
                "response_format": self.audio_format or "mp3",
            }
            if self.speed:
                try:
                    payload["speed"] = float(self.speed)
                except ValueError:
                    payload["speed"] = self.speed
            return payload
        if self.protocol == "minimax_t2a_v2":
            speed_value: float | str = 1
            if self.speed:
                try:
                    speed_value = float(self.speed)
                except ValueError:
                    speed_value = self.speed
            return {
                "model": self.model or "speech-2.8-turbo",
                "text": text,
                "stream": False,
                "voice_setting": {
                    "voice_id": self.voice or "male-qn-qingse",
                    "speed": speed_value,
                    "vol": 1,
                    "pitch": 0,
                },
                "audio_setting": {
                    "sample_rate": 32000,
                    "bitrate": 128000,
                    "format": self.audio_format or "mp3",
                    "channel": 1,
                },
                "output_format": "hex",
                "subtitle_enable": False,
            }
        payload = {"text": text}
        if self.voice:
            payload["voice"] = self.voice
        if self.model:
            payload["model"] = self.model
        if self.audio_format:
            payload["format"] = self.audio_format
        if self.speed:
            payload["speed"] = self.speed
        return payload


def _coerce_headers(value: dict | str | None) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items() if key and val is not None}
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return {str(key): str(val) for key, val in parsed.items() if key and val is not None}
    return {}


def _extract_json_path(payload: object, path: str | None) -> object:
    current = payload
    for part in (path or "").split("."):
        key = part.strip()
        if not key:
            continue
        if isinstance(current, dict):
            current = current.get(key)
            continue
        if isinstance(current, list) and key.isdigit():
            index = int(key)
            current = current[index] if 0 <= index < len(current) else None
            continue
        return None
    return current


def _decode_audio_value(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str) or not value:
        return b""
    stripped = value.strip()
    try:
        return bytes.fromhex(stripped)
    except ValueError:
        pass
    try:
        import base64
        return base64.b64decode(stripped)
    except Exception:
        return b""

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
                    voice=cust_conf.get("voice"),
                    protocol=cust_conf.get("protocol") or "json_audio_stream",
                    model=cust_conf.get("model") or "",
                    audio_format=cust_conf.get("format") or "mp3",
                    speed=cust_conf.get("speed") or "",
                    response_audio_path=cust_conf.get("responseAudioPath") or cust_conf.get("response_audio_path") or "",
                    headers=cust_conf.get("headers"),
                )
        elif active == "model_ref":
            model_ref = (tts_conf.get("model_ref") or {}).get("modelRef") or ""
            return ModelRefTTSProvider(str(model_ref))
        
        return MockTTSProvider()
