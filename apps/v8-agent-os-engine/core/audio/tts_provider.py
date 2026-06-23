import edge_tts
import aiohttp
import json
from abc import ABC, abstractmethod
from typing import AsyncGenerator

from core.model_ref import parse_model_ref

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
    def __init__(self, model_ref: str = "", voice: str = "", audio_format: str = "mp3", speed: str = ""):
        self.model_ref = model_ref
        self.voice = voice
        self.audio_format = audio_format or "mp3"
        self.speed = speed or ""

    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        provider = _model_ref_tts_provider_from_config(
            model_ref=self.model_ref,
            voice=self.voice,
            audio_format=self.audio_format,
            speed=self.speed,
        )
        async for chunk in provider.synthesize_stream(text):
            yield chunk


def _model_ref_tts_provider_from_config(
    *,
    model_ref: str,
    voice: str = "",
    audio_format: str = "mp3",
    speed: str = "",
    config: dict | None = None,
) -> TTSProvider:
    parsed = parse_model_ref(model_ref)
    if not parsed:
        target = model_ref or "未选择模型"
        raise RuntimeError(f"TTS model_ref 无效：{target}")
    provider_id, model_id = parsed
    if config is None:
        from core.model_control_plane import model_control_plane

        config = model_control_plane.get_config()
    provider_entry = ((config or {}).get("providers") or {}).get(provider_id) or {}
    provider_meta = provider_entry.get("provider") or {}
    model_entry = (provider_entry.get("models") or {}).get(model_id) or {}
    if not provider_entry or not model_entry:
        raise RuntimeError(f"TTS model_ref 未在 Model Hub 中找到：{model_ref}")

    base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").strip().rstrip("/")
    api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "").strip()
    media_limits = model_entry.get("mediaLimits") or {}
    adapter_provider_id = str(media_limits.get("adapterProviderId") or "").strip()
    api_standard = str(media_limits.get("apiStandard") or "").strip()
    parameter_profile = str(model_entry.get("parameterProfile") or "").strip()
    provider_model_id = str(media_limits.get("providerModelId") or "").strip() or model_id.rsplit("/", 1)[-1]

    if adapter_provider_id == "minimax_tts" or api_standard == "minimax_tts" or parameter_profile == "minimax_tts":
        if not base_url:
            raise RuntimeError("MiniMax TTS 模型缺少 baseURL，无法合成语音。")
        path_prefix = model_id.rsplit("/", 1)[0] if "/" in model_id else "t2a_v2"
        endpoint = f"{base_url}/{path_prefix.strip('/')}"
        return CustomTTSProvider(
            endpoint=endpoint,
            api_key=api_key,
            voice=voice,
            protocol="minimax_t2a_v2",
            model=provider_model_id,
            audio_format=audio_format or "mp3",
            speed=speed,
            response_audio_path="data.audio",
        )

    if adapter_provider_id == "openai_tts" or api_standard in {"openai_speech", "openai_audio_speech"}:
        if not base_url:
            raise RuntimeError("OpenAI-compatible TTS 模型缺少 baseURL，无法合成语音。")
        endpoint = base_url if base_url.endswith("/audio/speech") else f"{base_url}/audio/speech"
        return CustomTTSProvider(
            endpoint=endpoint,
            api_key=api_key,
            voice=voice,
            protocol="openai_speech",
            model=provider_model_id,
            audio_format=audio_format or "mp3",
            speed=speed,
        )

    raise RuntimeError(
        f"TTS 已配置为使用已配置模型替代（{model_ref}），但该模型没有可用的系统 TTS 适配器。"
        "请改用 Edge TTS / 自建 TTS API，或选择 MiniMax/OpenAI-compatible TTS 模型。"
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
            model_ref_conf = tts_conf.get("model_ref") or {}
            model_ref = model_ref_conf.get("modelRef") or ""
            return ModelRefTTSProvider(
                str(model_ref),
                voice=str(model_ref_conf.get("voice") or ""),
                audio_format=str(model_ref_conf.get("format") or "mp3"),
                speed=str(model_ref_conf.get("speed") or ""),
            )
        
        return MockTTSProvider()
