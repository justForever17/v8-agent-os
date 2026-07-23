import edge_tts
import aiohttp
import json
import uuid
from abc import ABC, abstractmethod
from typing import AsyncGenerator

from core.model_ref import parse_model_ref

from .audio_config import AudioConfigManager


class TTSProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider_code: str = "",
        trace_id: str = "",
        status_code: int = 502,
    ) -> None:
        super().__init__(message)
        self.provider_code = provider_code
        self.trace_id = trace_id
        self.status_code = status_code


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
        app_id: str = "",
        resource_id: str = "",
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
        self.app_id = app_id or ""
        self.resource_id = resource_id or ""

    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        headers = self._build_headers()
        payload = self._build_payload(text)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.endpoint, json=payload, headers=headers) as response:
                    if response.status == 200:
                        content_type = (response.headers.get("Content-Type") or "").lower()
                        if content_type.startswith("audio/"):
                            async for chunk in response.content.iter_any():
                                if chunk:
                                    yield chunk
                        elif self.protocol == "volcengine_doubao_tts":
                            text_payload = await response.text()
                            for audio_bytes in _decode_volcengine_audio_chunks(text_payload):
                                if audio_bytes:
                                    yield audio_bytes
                        elif self.response_audio_path or self.protocol in {"minimax_t2a_v2", "aliyun_cosyvoice_tts"}:
                            response_json = await response.json(content_type=None)
                            if self.protocol == "minimax_t2a_v2":
                                base_resp = response_json.get("base_resp") if isinstance(response_json, dict) else None
                                base_resp = base_resp if isinstance(base_resp, dict) else {}
                                provider_code = str(base_resp.get("status_code") or "")
                                if provider_code not in {"", "0"}:
                                    raise TTSProviderError(
                                        str(base_resp.get("status_msg") or "MiniMax TTS synthesis failed."),
                                        provider_code=provider_code,
                                        trace_id=str(response_json.get("trace_id") or ""),
                                    )
                            audio_value = _extract_first_json_path(response_json, _audio_response_paths_for_protocol(self.protocol, self.response_audio_path))
                            if isinstance(audio_value, str) and audio_value.startswith(("http://", "https://")):
                                async with session.get(audio_value) as audio_response:
                                    if audio_response.status == 200:
                                        async for chunk in audio_response.content.iter_any():
                                            if chunk:
                                                yield chunk
                                    else:
                                        raise TTSProviderError(
                                            f"TTS audio download returned HTTP {audio_response.status}.",
                                            status_code=502,
                                        )
                            else:
                                audio_bytes = _decode_audio_value(audio_value)
                                if audio_bytes:
                                    yield audio_bytes
                                else:
                                    trace_id = str(response_json.get("trace_id") or "") if isinstance(response_json, dict) else ""
                                    raise TTSProviderError(
                                        "The TTS provider returned no playable audio.",
                                        trace_id=trace_id,
                                    )
                        else:
                            async for chunk in response.content.iter_any():
                                if chunk:
                                    yield chunk
                    else:
                        raise TTSProviderError(
                            f"TTS provider returned HTTP {response.status}.",
                            status_code=502,
                        )
        except TTSProviderError:
            raise
        except Exception as error:
            raise TTSProviderError(f"TTS request failed: {error}") from error

    def _build_headers(self) -> dict[str, str]:
        headers = dict(self.extra_headers)
        if self.protocol == "volcengine_doubao_tts":
            if self.app_id:
                headers.setdefault("X-Api-App-Key", self.app_id)
            if self.api_key:
                headers.setdefault("X-Api-Access-Key", self.api_key)
            if self.resource_id:
                headers.setdefault("X-Api-Resource-Id", self.resource_id)
            headers.setdefault("X-Api-Connect-Id", uuid.uuid4().hex)
            headers.setdefault("Content-Type", "application/json")
            return headers
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        return headers

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
        if self.protocol == "aliyun_cosyvoice_tts":
            return {
                "model": self.model or "cosyvoice-v3-flash",
                "input": {
                    "text": text,
                    "voice": self.voice or "longxiaochun",
                    "format": self.audio_format or "mp3",
                    "sample_rate": 24000,
                },
            }
        if self.protocol == "volcengine_doubao_tts":
            return {
                "user": {
                    "uid": "v8-agent-os",
                },
                "req_params": {
                    "text": text,
                    "speaker": self.voice or "zh_female_shuangkuaisisi_moon_bigtts",
                    "audio_params": {
                        "format": self.audio_format or "mp3",
                        "sample_rate": 24000,
                    },
                },
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


def _extract_first_json_path(payload: object, paths: list[str]) -> object:
    for path in paths:
        value = _extract_json_path(payload, path)
        if value is not None:
            return value
    return None


def _audio_response_paths_for_protocol(protocol: str, explicit_path: str = "") -> list[str]:
    if explicit_path:
        return [explicit_path]
    if protocol == "minimax_t2a_v2":
        return ["data.audio"]
    if protocol == "aliyun_cosyvoice_tts":
        return [
            "output.audio.url",
            "output.audio.data",
            "output.audio",
            "output.url",
            "audio",
            "data.audio",
        ]
    return ["audio", "data.audio", "output.audio"]


def _decode_audio_value(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str) or not value:
        return b""
    stripped = value.strip()
    if stripped.startswith("data:") and "," in stripped:
        stripped = stripped.split(",", 1)[1].strip()
    try:
        return bytes.fromhex(stripped)
    except ValueError:
        pass
    try:
        import base64
        return base64.b64decode(stripped)
    except Exception:
        return b""


def _decode_base64_audio_value(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        return b""
    stripped = value.strip()
    if stripped.startswith("data:") and "," in stripped:
        stripped = stripped.split(",", 1)[1].strip()
    try:
        import base64
        return base64.b64decode(stripped, validate=True)
    except Exception:
        return b""


def _decode_volcengine_audio_chunks(payload: str) -> list[bytes]:
    chunks: list[bytes] = []
    for raw_line in str(payload or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            event = json.loads(line)
        except Exception:
            decoded = _decode_base64_audio_value(line) or _decode_audio_value(line)
            if decoded:
                chunks.append(decoded)
            continue
        for path in [
            "data.audio",
            "audio",
            "result.audio",
            "payload.audio",
            "data",
        ]:
            value = _extract_json_path(event, path)
            decoded = _decode_base64_audio_value(value) or _decode_audio_value(value)
            if decoded:
                chunks.append(decoded)
                break
    if not chunks:
        decoded = _decode_base64_audio_value(payload) or _decode_audio_value(payload)
        if decoded:
            chunks.append(decoded)
    return chunks


def _aliyun_cosyvoice_endpoint(base_url: str, submit_path: str = "") -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return ""
    if normalized.endswith("/compatible-mode/v1"):
        normalized = f"{normalized[:-len('/compatible-mode/v1')]}/api/v1"
    elif normalized.endswith("/compatible-mode"):
        normalized = f"{normalized[:-len('/compatible-mode')]}/api/v1"
    elif "/api/v1" not in normalized:
        normalized = f"{normalized}/api/v1"
    path = (submit_path or "/services/audio/tts/SpeechSynthesizer").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    if normalized.endswith(path):
        return normalized
    return f"{normalized}{path}"


def _volcengine_doubao_tts_endpoint(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized or "volces.com/api/v3" in normalized or "ark.cn-" in normalized:
        return "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    return normalized


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

    if (
        adapter_provider_id == "aliyun_bailian_cosyvoice"
        or api_standard == "dashscope_cosyvoice_tts"
        or parameter_profile == "dashscope_cosyvoice_tts"
    ):
        if not base_url:
            raise RuntimeError("阿里云 CosyVoice TTS 模型缺少 baseURL，无法合成语音。")
        endpoint = _aliyun_cosyvoice_endpoint(
            base_url,
            str(media_limits.get("submitPath") or "/services/audio/tts/SpeechSynthesizer"),
        )
        return CustomTTSProvider(
            endpoint=endpoint,
            api_key=api_key,
            voice=voice,
            protocol="aliyun_cosyvoice_tts",
            model=provider_model_id,
            audio_format=audio_format or "mp3",
            speed=speed,
        )

    if (
        adapter_provider_id == "volcengine_doubao_voice"
        or api_standard == "volcengine_ark_voice"
        or parameter_profile == "volcengine_ark_voice"
    ):
        voice_app_id = str(provider_meta.get("voice_app_id") or provider_meta.get("voiceAppId") or "").strip()
        voice_resource_id = str(provider_meta.get("voice_resource_id") or provider_meta.get("voiceResourceId") or "").strip()
        if not api_key:
            raise RuntimeError("火山豆包语音 TTS 模型缺少 Access Key/API Key，无法合成语音。")
        if not voice_app_id:
            raise RuntimeError("火山豆包语音 TTS 模型缺少 provider.voice_app_id，无法合成语音。")
        if not voice_resource_id:
            raise RuntimeError("火山豆包语音 TTS 模型缺少 provider.voice_resource_id，无法合成语音。")
        return CustomTTSProvider(
            endpoint=_volcengine_doubao_tts_endpoint(base_url),
            api_key=api_key,
            voice=voice,
            protocol="volcengine_doubao_tts",
            model=provider_model_id,
            audio_format=audio_format or "mp3",
            speed=speed,
            app_id=voice_app_id,
            resource_id=voice_resource_id,
        )

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
    def get_provider(config: dict | None = None) -> TTSProvider:
        config = (
            AudioConfigManager.get_config()
            if config is None
            else AudioConfigManager.normalize_config(config)
        )
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
