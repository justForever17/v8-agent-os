from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import re
from secrets import token_hex
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from mutagen import File as MutagenFile
from mutagen import MutagenError

from core.model_control_plane import model_control_plane
from core.storage import storage
from core.system_base import get_admin_base_url
from core.v8_agent_os_paths import V8_AGENT_OS_HOME


MAX_VOICE_SAMPLE_BYTES = 20 * 1024 * 1024
MIN_VOICE_SAMPLE_SECONDS = 10.0
MAX_VOICE_SAMPLE_SECONDS = 5 * 60.0
_MINIMAX_VOICE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]$")
_SUPPORTED_AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav"}
_VOICE_LEDGER_FILENAME = "audio_voice_ledger.json"
_VOICE_SAMPLE_DIR = V8_AGENT_OS_HOME / "tmp" / "voice-samples"
_VOICE_SAMPLE_TTL_SECONDS = 30 * 60
_LEDGER_LOCK = asyncio.Lock()


class VoiceManagerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        code: str = "voice_manager_error",
        provider: str = "",
        provider_code: str = "",
        trace_id: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.provider = _diagnostic_text(provider)
        self.provider_code = _diagnostic_text(provider_code)
        self.trace_id = _diagnostic_text(trace_id)


@dataclass(frozen=True)
class VoiceCustomizationCapabilities:
    clone: bool
    design: bool
    list: bool
    delete: bool
    preview: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "clone": self.clone,
            "design": self.design,
            "list": self.list,
            "delete": self.delete,
            "preview": self.preview,
        }


@dataclass(frozen=True)
class VoiceAdapterContext:
    adapter_id: str
    model_ref: str
    model_id: str
    provider_id: str
    api_key: str
    base_url: str
    provider_model_id: str
    provider_meta: dict[str, Any]
    model_meta: dict[str, Any]
    media_limits: dict[str, Any]
    capabilities: VoiceCustomizationCapabilities


MINIMAX_CAPABILITIES = VoiceCustomizationCapabilities(
    clone=True,
    design=False,
    list=True,
    delete=True,
    preview=True,
)
ALIYUN_CAPABILITIES = VoiceCustomizationCapabilities(
    clone=True,
    design=False,
    list=True,
    delete=True,
    preview=False,
)
VOLCENGINE_CAPABILITIES = VoiceCustomizationCapabilities(
    clone=True,
    design=False,
    list=True,
    delete=False,
    preview=False,
)


ALIYUN_PRESET_VOICES = [
    {"value": "longxiaochun", "label": "龙小淳 · longxiaochun", "group": "preset", "source": "preset"},
    {"value": "longwan", "label": "龙婉 · longwan", "group": "preset", "source": "preset"},
    {"value": "longcheng", "label": "龙橙 · longcheng", "group": "preset", "source": "preset"},
    {"value": "longhua", "label": "龙华 · longhua", "group": "preset", "source": "preset"},
    {"value": "longxiaoxia", "label": "龙小夏 · longxiaoxia", "group": "preset", "source": "preset"},
]
VOLCENGINE_PRESET_VOICES = [
    {
        "value": "zh_female_shuangkuaisisi_moon_bigtts",
        "label": "爽快思思 · zh_female_shuangkuaisisi_moon_bigtts",
        "group": "preset",
        "source": "preset",
    },
    {
        "value": "zh_female_wanwanxiaohe_moon_bigtts",
        "label": "湾湾小何 · zh_female_wanwanxiaohe_moon_bigtts",
        "group": "preset",
        "source": "preset",
    },
    {
        "value": "zh_male_wennuanahu_moon_bigtts",
        "label": "温暖阿虎 · zh_male_wennuanahu_moon_bigtts",
        "group": "preset",
        "source": "preset",
    },
    {
        "value": "zh_male_shaonianzixin_moon_bigtts",
        "label": "少年梓辛 · zh_male_shaonianzixin_moon_bigtts",
        "group": "preset",
        "source": "preset",
    },
]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _diagnostic_text(value: Any) -> str:
    normalized = re.sub(r"[\x00-\x1f\x7f]+", " ", _text(value))
    return re.sub(r"\s+", " ", normalized)[:256]


def _nested_value(source: Any, paths: tuple[tuple[str, ...], ...]) -> Any:
    for segments in paths:
        cursor = source
        for segment in segments:
            cursor = _as_dict(cursor).get(segment)
        if cursor not in (None, ""):
            return cursor
    return None


def _trace_id(response: httpx.Response, payload: Any) -> str:
    for key in ("trace_id", "x-trace-id", "trace-id", "x-request-id", "request-id", "x-tt-logid"):
        value = response.headers.get(key)
        if value:
            return _diagnostic_text(value)
    value = _nested_value(
        payload,
        (
            ("trace_id",),
            ("traceId",),
            ("request_id",),
            ("requestId",),
            ("ResponseMetadata", "RequestId"),
            ("response_metadata", "request_id"),
        ),
    )
    return _diagnostic_text(value)


def _provider_error_message(response: httpx.Response, payload: Any, fallback: str) -> str:
    for key in ("x-api-message", "x-error-message"):
        value = response.headers.get(key)
        if value:
            return _diagnostic_text(value)
    value = _nested_value(
        payload,
        (
            ("base_resp", "status_msg"),
            ("baseResp", "statusMsg"),
            ("data", "base_resp", "status_msg"),
            ("ResponseMetadata", "Error", "Message"),
            ("error", "message"),
            ("message",),
            ("status_text",),
            ("statusText",),
            ("error",),
        ),
    )
    return _diagnostic_text(value) or fallback


def _provider_error_code(response: httpx.Response, payload: Any) -> str:
    for key in ("x-api-status-code", "x-error-code"):
        value = response.headers.get(key)
        if value:
            return _diagnostic_text(value)
    value = _nested_value(
        payload,
        (
            ("base_resp", "status_code"),
            ("baseResp", "statusCode"),
            ("data", "base_resp", "status_code"),
            ("ResponseMetadata", "Error", "Code"),
            ("error", "code"),
            ("code",),
            ("status_code",),
        ),
    )
    return _diagnostic_text(value)


def _raise_provider_error(
    response: httpx.Response,
    payload: Any,
    *,
    provider: str,
    default_message: str,
) -> None:
    raise VoiceManagerError(
        _provider_error_message(response, payload, default_message),
        status_code=502,
        code="provider_request_failed",
        provider=provider,
        provider_code=_provider_error_code(response, payload),
        trace_id=_trace_id(response, payload),
    )


def _assert_minimax_success(response: httpx.Response, payload: Any) -> None:
    base_resp = _as_dict(_as_dict(payload).get("base_resp") or _as_dict(payload).get("baseResp"))
    if not base_resp:
        base_resp = _as_dict(_as_dict(_as_dict(payload).get("data")).get("base_resp"))
    vendor_status = base_resp.get("status_code")
    vendor_failed = vendor_status is not None and _text(vendor_status) not in {"", "0"}
    if response.is_success and not vendor_failed:
        return
    _raise_provider_error(
        response,
        payload,
        provider="minimax_tts",
        default_message=f"MiniMax API returned HTTP {response.status_code}.",
    )


def _assert_generic_provider_success(response: httpx.Response, payload: Any, provider: str) -> None:
    provider_code = _provider_error_code(response, payload)
    normalized_code = provider_code.lower()
    business_failed = bool(provider_code) and normalized_code not in {
        "0",
        "200",
        "20000000",
        "ok",
        "success",
    }
    if response.is_success and not business_failed:
        return
    _raise_provider_error(
        response,
        payload,
        provider=provider,
        default_message=f"{provider} API returned HTTP {response.status_code}.",
    )


def _response_json(response: httpx.Response, provider: str) -> dict[str, Any]:
    try:
        return _as_dict(response.json())
    except ValueError as exc:
        raise VoiceManagerError(
            f"{provider} API returned a non-JSON response (HTTP {response.status_code}).",
            status_code=502,
            code="invalid_provider_response",
            provider=provider,
            trace_id=_trace_id(response, {}),
        ) from exc


def _audio_suffix(filename: str) -> str:
    normalized = _text(filename)
    return "." + normalized.rsplit(".", 1)[-1].lower() if "." in normalized else ""


def _validate_voice_sample(filename: str, audio_bytes: bytes) -> float:
    if not audio_bytes:
        raise VoiceManagerError("Sample audio file is required.", code="sample_required")
    if len(audio_bytes) > MAX_VOICE_SAMPLE_BYTES:
        raise VoiceManagerError("Sample audio file exceeds the 20MB limit.", code="sample_too_large")
    suffix = _audio_suffix(filename)
    if suffix not in _SUPPORTED_AUDIO_SUFFIXES:
        raise VoiceManagerError("Voice cloning accepts MP3, M4A, or WAV audio.", code="unsupported_audio_format")
    try:
        audio = MutagenFile(BytesIO(audio_bytes))
        duration = float(getattr(getattr(audio, "info", None), "length", 0.0) or 0.0)
    except (MutagenError, ValueError, TypeError, OSError) as exc:
        raise VoiceManagerError("Unable to read the sample audio duration.", code="invalid_audio_file") from exc
    if duration <= 0:
        raise VoiceManagerError("Unable to read the sample audio duration.", code="invalid_audio_file")
    if duration < MIN_VOICE_SAMPLE_SECONDS:
        raise VoiceManagerError(
            f"Sample audio must be at least {MIN_VOICE_SAMPLE_SECONDS:g} seconds; detected {duration:.2f} seconds.",
            code="sample_too_short",
        )
    if duration > MAX_VOICE_SAMPLE_SECONDS:
        raise VoiceManagerError(
            f"Sample audio must not exceed {MAX_VOICE_SAMPLE_SECONDS:g} seconds; detected {duration:.2f} seconds.",
            code="sample_too_long",
        )
    return duration


def _dedupe_voices(voices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for voice in voices:
        voice_id = _text(voice.get("value"))
        if not voice_id or voice_id in seen:
            continue
        seen.add(voice_id)
        result.append(voice)
    return result


def _read_ledger() -> list[dict[str, Any]]:
    payload = storage.read_json(_VOICE_LEDGER_FILENAME)
    entries = payload if isinstance(payload, list) else _as_dict(payload).get("entries")
    return [dict(item) for item in entries or [] if isinstance(item, dict)]


def _write_ledger(entries: list[dict[str, Any]]) -> None:
    storage.write_json(_VOICE_LEDGER_FILENAME, entries)  # type: ignore[arg-type]


async def _upsert_ledger_entry(context: VoiceAdapterContext, voice_id: str) -> None:
    async with _LEDGER_LOCK:
        entries = _read_ledger()
        filtered = [
            item
            for item in entries
            if not (
                _text(item.get("provider")) == context.adapter_id
                and _text(item.get("modelRef")) == context.model_ref
                and _text(item.get("voiceId")) == voice_id
            )
        ]
        filtered.append(
            {
                "provider": context.adapter_id,
                "modelRef": context.model_ref,
                "voiceId": voice_id,
                "label": voice_id,
                "group": "custom",
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
        )
        _write_ledger(filtered)


async def _remove_ledger_entry(context: VoiceAdapterContext, voice_id: str) -> None:
    async with _LEDGER_LOCK:
        entries = _read_ledger()
        _write_ledger(
            [
                item
                for item in entries
                if not (
                    _text(item.get("provider")) == context.adapter_id
                    and _text(item.get("modelRef")) == context.model_ref
                    and _text(item.get("voiceId")) == voice_id
                )
            ]
        )


def _ledger_voices(
    context: VoiceAdapterContext,
    *,
    availability: str = "confirmed",
) -> list[dict[str, Any]]:
    return [
        {
            "value": _text(item.get("voiceId")),
            "label": _text(item.get("label")) or _text(item.get("voiceId")),
            "group": _text(item.get("group")) or "custom",
            "deletable": context.capabilities.delete,
            "source": "local_ledger",
            "availability": availability,
        }
        for item in _read_ledger()
        if _text(item.get("provider")) == context.adapter_id
        and _text(item.get("modelRef")) == context.model_ref
        and _text(item.get("voiceId"))
    ]


def _is_private_host(hostname: str) -> bool:
    host = hostname.lower()
    if not host or host in {"localhost", "::1", "0.0.0.0"} or host.endswith((".localhost", ".local")):
        return True
    if host.startswith(("127.", "10.", "192.168.")):
        return True
    match = re.match(r"^172\.(\d+)\.", host)
    return bool(match and 16 <= int(match.group(1)) <= 31)


def _admin_public_origin() -> str:
    configured = _text(get_admin_base_url()).rstrip("/")
    if configured.endswith("/api"):
        configured = configured[:-4]
    parsed = urlparse(configured)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or _is_private_host(parsed.hostname or ""):
        raise VoiceManagerError(
            "Aliyun voice cloning requires a publicly reachable Admin base URL in systemBase.bridge.adminBaseUrl.",
            code="public_sample_url_required",
            provider="aliyun_bailian_cosyvoice",
        )
    return configured


def _cleanup_expired_samples() -> None:
    if not _VOICE_SAMPLE_DIR.exists():
        return
    now_ms = int(time.time() * 1000)
    for candidate in _VOICE_SAMPLE_DIR.iterdir():
        try:
            expires_at = int(candidate.name.split("-", 1)[0])
        except (TypeError, ValueError):
            continue
        if expires_at <= now_ms:
            candidate.unlink(missing_ok=True)


def _publish_voice_sample(filename: str, audio_bytes: bytes) -> str:
    public_origin = _admin_public_origin()
    _cleanup_expired_samples()
    _VOICE_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    expires_at = int(time.time() * 1000) + _VOICE_SAMPLE_TTL_SECONDS * 1000
    token = token_hex(24)
    suffix = _audio_suffix(filename) or ".audio"
    (_VOICE_SAMPLE_DIR / f"{expires_at}-{token}{suffix}").write_bytes(audio_bytes)
    return f"{public_origin}/api/audio/voice-samples/{token}"


def _flatten_minimax_voices(payload: Any) -> list[dict[str, Any]]:
    root = _as_dict(payload)
    source = _as_dict(root.get("data")) or root
    groups = (
        ("system_voice", "system", False),
        ("voice_cloning", "cloned", True),
        ("voice_generation", "generated", True),
    )
    voices: list[dict[str, Any]] = []
    for source_key, group, deletable in groups:
        for raw_item in source.get(source_key) or []:
            item = _as_dict(raw_item)
            voice_id = _text(item.get("voice_id") or item.get("voiceId") or item.get("id"))
            if not voice_id:
                continue
            name = _text(item.get("voice_name") or item.get("voiceName") or item.get("name")) or voice_id
            voices.append(
                {
                    "value": voice_id,
                    "label": f"{name} · {voice_id}" if name != voice_id else voice_id,
                    "group": group,
                    "deletable": deletable,
                    "source": "remote",
                    "availability": "available",
                }
            )
    return _dedupe_voices(voices)


class VoiceCustomizationAdapter:
    adapter_id = ""
    capabilities = VoiceCustomizationCapabilities(False, False, False, False, False)

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self, timeout_seconds: float = 120.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=10.0), transport=self._transport)

    async def list_voices(self, context: VoiceAdapterContext) -> dict[str, Any]:
        raise VoiceManagerError("Voice listing is not supported.", code="unsupported_action", provider=self.adapter_id)

    async def delete_voice(self, context: VoiceAdapterContext, voice_id: str, voice_type: str) -> dict[str, Any]:
        raise VoiceManagerError("Voice deletion is not supported.", code="unsupported_action", provider=self.adapter_id)

    async def clone_voice(
        self,
        context: VoiceAdapterContext,
        *,
        voice_id: str,
        preview_text: str,
        filename: str,
        content_type: str,
        audio_bytes: bytes,
    ) -> dict[str, Any]:
        raise VoiceManagerError("Voice cloning is not supported.", code="unsupported_action", provider=self.adapter_id)


class MiniMaxVoiceAdapter(VoiceCustomizationAdapter):
    adapter_id = "minimax_tts"
    capabilities = MINIMAX_CAPABILITIES

    @staticmethod
    def _api_root(base_url: str) -> str:
        normalized = _text(base_url) or "https://api.minimaxi.com/v1"
        normalized = normalized.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise VoiceManagerError("MiniMax base URL is invalid.", code="invalid_provider_base_url")
        version_match = re.match(r"^(.*?/v1)(?:/.*)?$", normalized, flags=re.IGNORECASE)
        return (version_match.group(1) if version_match else f"{normalized}/v1").rstrip("/")

    @staticmethod
    def _headers(context: VoiceAdapterContext) -> dict[str, str]:
        return {"Authorization": f"Bearer {context.api_key}"}

    @staticmethod
    def _file_id(payload: dict[str, Any]) -> int | None:
        value = _nested_value(
            payload,
            (
                ("file", "file_id"),
                ("file", "id"),
                ("data", "file_id"),
                ("data", "file", "file_id"),
                ("file_id",),
            ),
        )
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            return parsed if parsed > 0 else None
        return None

    async def list_voices(self, context: VoiceAdapterContext) -> dict[str, Any]:
        async with self._client(30.0) as client:
            response = await client.post(
                f"{self._api_root(context.base_url)}/get_voice",
                headers={**self._headers(context), "Content-Type": "application/json"},
                json={"voice_type": "all"},
            )
        payload = _response_json(response, self.adapter_id)
        _assert_minimax_success(response, payload)
        return {
            "voices": _dedupe_voices(
                [
                    *_flatten_minimax_voices(payload),
                    *_ledger_voices(context, availability="pending_activation"),
                ]
            )
        }

    async def delete_voice(self, context: VoiceAdapterContext, voice_id: str, voice_type: str) -> dict[str, Any]:
        normalized_type = _text(voice_type) or "voice_cloning"
        if normalized_type not in {"voice_cloning", "voice_generation"}:
            raise VoiceManagerError("voiceType must be voice_cloning or voice_generation.", code="invalid_voice_type")
        async with self._client(30.0) as client:
            response = await client.post(
                f"{self._api_root(context.base_url)}/delete_voice",
                headers={**self._headers(context), "Content-Type": "application/json"},
                json={"voice_id": voice_id, "voice_type": normalized_type},
            )
        payload = _response_json(response, self.adapter_id)
        _assert_minimax_success(response, payload)
        await _remove_ledger_entry(context, voice_id)
        return {"voiceId": voice_id}

    async def clone_voice(
        self,
        context: VoiceAdapterContext,
        *,
        voice_id: str,
        preview_text: str,
        filename: str,
        content_type: str,
        audio_bytes: bytes,
    ) -> dict[str, Any]:
        if not _MINIMAX_VOICE_ID_PATTERN.fullmatch(voice_id):
            raise VoiceManagerError(
                "voiceId must be 8-256 characters, start with a letter, use letters, numbers, '-' or '_', and end with a letter or number.",
                code="invalid_voice_id",
                provider=self.adapter_id,
            )
        suffix = _audio_suffix(filename)
        upload_files = {"file": (filename or f"sample{suffix}", audio_bytes, _text(content_type) or "application/octet-stream")}
        async with self._client() as client:
            upload_response = await client.post(
                f"{self._api_root(context.base_url)}/files/upload",
                headers=self._headers(context),
                data={"purpose": "voice_clone"},
                files=upload_files,
            )
            upload_payload = _response_json(upload_response, self.adapter_id)
            _assert_minimax_success(upload_response, upload_payload)
            file_id = self._file_id(upload_payload)
            if file_id is None:
                raise VoiceManagerError(
                    "MiniMax upload succeeded but no integer file_id was returned.",
                    status_code=502,
                    code="upload_file_id_missing",
                    provider=self.adapter_id,
                    trace_id=_trace_id(upload_response, upload_payload),
                )
            clone_body: dict[str, Any] = {"file_id": file_id, "voice_id": voice_id}
            if preview_text:
                clone_body.update({"text": preview_text, "model": context.provider_model_id})
            clone_response = await client.post(
                f"{self._api_root(context.base_url)}/voice_clone",
                headers={**self._headers(context), "Content-Type": "application/json"},
                json=clone_body,
            )
        clone_payload = _response_json(clone_response, self.adapter_id)
        _assert_minimax_success(clone_response, clone_payload)
        await _upsert_ledger_entry(context, voice_id)
        result: dict[str, Any] = {
            "voiceId": voice_id,
            "fileId": file_id,
            "availability": "pending_activation",
        }
        preview_url = _text(clone_payload.get("demo_audio"))
        if preview_url.startswith(("http://", "https://")):
            result["previewAudioUrl"] = preview_url
        return result


class AliyunCosyVoiceAdapter(VoiceCustomizationAdapter):
    adapter_id = "aliyun_bailian_cosyvoice"
    capabilities = ALIYUN_CAPABILITIES

    @staticmethod
    def _endpoint(context: VoiceAdapterContext) -> str:
        base = _text(context.base_url) or "https://dashscope.aliyuncs.com/api/v1"
        base = base.rstrip("/")
        if base.endswith("/compatible-mode/v1"):
            base = f"{base[:-len('/compatible-mode/v1')]}/api/v1"
        elif base.endswith("/compatible-mode"):
            base = f"{base[:-len('/compatible-mode')]}/api/v1"
        elif "/api/v1" not in base:
            base = f"{base}/api/v1"
        return f"{base}/services/audio/tts/customization"

    async def list_voices(self, context: VoiceAdapterContext) -> dict[str, Any]:
        return {"voices": _dedupe_voices([*ALIYUN_PRESET_VOICES, *_ledger_voices(context)])}

    async def delete_voice(self, context: VoiceAdapterContext, voice_id: str, voice_type: str) -> dict[str, Any]:
        async with self._client(30.0) as client:
            response = await client.post(
                self._endpoint(context),
                headers={"Authorization": f"Bearer {context.api_key}", "Content-Type": "application/json"},
                json={"model": "voice-enrollment", "input": {"action": "delete_voice", "voice_id": voice_id}},
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        await _remove_ledger_entry(context, voice_id)
        return {"voiceId": voice_id}

    async def clone_voice(
        self,
        context: VoiceAdapterContext,
        *,
        voice_id: str,
        preview_text: str,
        filename: str,
        content_type: str,
        audio_bytes: bytes,
    ) -> dict[str, Any]:
        sample_url = _publish_voice_sample(filename, audio_bytes)
        async with self._client() as client:
            response = await client.post(
                self._endpoint(context),
                headers={"Authorization": f"Bearer {context.api_key}", "Content-Type": "application/json"},
                json={
                    "model": "voice-enrollment",
                    "input": {
                        "action": "create_voice",
                        "target_model": context.provider_model_id,
                        "prefix": voice_id,
                        "url": sample_url,
                        "language_hints": ["zh"],
                    },
                },
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        resolved_voice_id = _text(
            _nested_value(
                payload,
                (("output", "voice_id"), ("output", "voiceId"), ("data", "voice_id"), ("voice_id",), ("voiceId",)),
            )
        ) or voice_id
        await _upsert_ledger_entry(context, resolved_voice_id)
        return {"voiceId": resolved_voice_id}


class VolcengineVoiceAdapter(VoiceCustomizationAdapter):
    adapter_id = "volcengine_doubao_voice"
    capabilities = VOLCENGINE_CAPABILITIES

    @staticmethod
    def _endpoint(context: VoiceAdapterContext, suffix: str) -> str:
        base = (_text(context.base_url) or "https://openspeech.bytedance.com/api/v3/tts").rstrip("/")
        if "/api/v3/tts/" in base:
            base = f"{base.split('/api/v3/tts/', 1)[0]}/api/v3/tts"
        elif base.endswith("/api/v3"):
            base = f"{base}/tts"
        elif "volces.com/api/v3" in base or "ark.cn-" in base:
            base = "https://openspeech.bytedance.com/api/v3/tts"
        return f"{base}/{suffix.lstrip('/')}"

    @staticmethod
    def _headers(context: VoiceAdapterContext) -> dict[str, str]:
        app_id = _text(context.provider_meta.get("voice_app_id") or context.provider_meta.get("voiceAppId"))
        resource_id = _text(context.provider_meta.get("voice_resource_id") or context.provider_meta.get("voiceResourceId"))
        if not app_id:
            raise VoiceManagerError("Volcengine voice_app_id is missing.", code="credential_missing", provider=context.adapter_id)
        if not resource_id:
            raise VoiceManagerError("Volcengine voice_resource_id is missing.", code="credential_missing", provider=context.adapter_id)
        return {
            "Content-Type": "application/json",
            "X-Api-App-Key": app_id,
            "X-Api-Access-Key": context.api_key,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Connect-Id": str(uuid4()),
        }

    async def list_voices(self, context: VoiceAdapterContext) -> dict[str, Any]:
        refreshed: list[dict[str, Any]] = []
        for voice in _ledger_voices(context):
            try:
                async with self._client(30.0) as client:
                    response = await client.post(
                        self._endpoint(context, "get_voice"),
                        headers=self._headers(context),
                        json={"speaker_id": voice["value"]},
                    )
                payload = _response_json(response, self.adapter_id)
                _assert_generic_provider_success(response, payload, self.adapter_id)
                status = _text(payload.get("status") or payload.get("message"))
                refreshed.append({**voice, "label": f"{voice['label']} · {status}" if status else voice["label"]})
            except VoiceManagerError:
                refreshed.append(voice)
        return {"voices": _dedupe_voices([*VOLCENGINE_PRESET_VOICES, *refreshed])}

    async def clone_voice(
        self,
        context: VoiceAdapterContext,
        *,
        voice_id: str,
        preview_text: str,
        filename: str,
        content_type: str,
        audio_bytes: bytes,
    ) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post(
                self._endpoint(context, "voice_clone"),
                headers=self._headers(context),
                json={"speaker_id": voice_id, "audio": base64.b64encode(audio_bytes).decode("ascii"), "language": 0},
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        resolved_voice_id = _text(
            _nested_value(payload, (("data", "speaker_id"), ("speaker_id",), ("data", "speakerId"), ("speakerId",)))
        ) or voice_id
        await _upsert_ledger_entry(context, resolved_voice_id)
        return {"voiceId": resolved_voice_id}


_ADAPTER_ID_ALIASES = {
    "minimax_tts": "minimax_tts",
    "dashscope_cosyvoice_tts": "aliyun_bailian_cosyvoice",
    "aliyun_bailian_cosyvoice": "aliyun_bailian_cosyvoice",
    "volcengine_ark_voice": "volcengine_doubao_voice",
    "volcengine_doubao_voice": "volcengine_doubao_voice",
}


class VoiceCustomizationManager:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        adapters = (
            MiniMaxVoiceAdapter(transport=transport),
            AliyunCosyVoiceAdapter(transport=transport),
            VolcengineVoiceAdapter(transport=transport),
        )
        self._adapters = {adapter.adapter_id: adapter for adapter in adapters}

    def resolve_context(self, model_ref: str) -> VoiceAdapterContext:
        normalized_ref = _text(model_ref)
        record = model_control_plane.get_model_record(normalized_ref)
        if not record:
            raise VoiceManagerError("Configured TTS model was not found.", status_code=404, code="model_not_found")
        provider_id = _text(record.get("provider_id"))
        model_id = _text(record.get("model_id"))
        provider_meta = _as_dict(record.get("provider"))
        model_meta = _as_dict(record.get("model"))
        media_limits = _as_dict(model_meta.get("mediaLimits"))
        declared_ids = (
            _text(media_limits.get("adapterProviderId")),
            _text(media_limits.get("apiStandard")),
            _text(model_meta.get("parameterProfile")),
        )
        adapter_id = next((_ADAPTER_ID_ALIASES[item] for item in declared_ids if item in _ADAPTER_ID_ALIASES), "")
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise VoiceManagerError(
                "Selected model does not explicitly declare supported voice-customization capabilities.",
                status_code=422,
                code="unsupported_voice_manager",
            )
        api_key = _text(provider_meta.get("api_key") or provider_meta.get("apiKey"))
        if not api_key or api_key.startswith("oauth:") or "***" in api_key:
            raise VoiceManagerError(
                f"{adapter_id} API key is not configured.",
                status_code=409,
                code="credential_missing",
                provider=adapter_id,
            )
        provider_model_id = _text(media_limits.get("providerModelId")) or model_id.rsplit("/", 1)[-1]
        return VoiceAdapterContext(
            adapter_id=adapter_id,
            model_ref=normalized_ref,
            model_id=model_id,
            provider_id=provider_id,
            api_key=api_key,
            base_url=_text(provider_meta.get("base_url") or provider_meta.get("baseUrl")),
            provider_model_id=provider_model_id,
            provider_meta=provider_meta,
            model_meta=model_meta,
            media_limits=media_limits,
            capabilities=adapter.capabilities,
        )

    def _adapter(self, context: VoiceAdapterContext) -> VoiceCustomizationAdapter:
        return self._adapters[context.adapter_id]

    @staticmethod
    def _result(context: VoiceAdapterContext, **payload: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "provider": context.adapter_id,
            "capabilities": context.capabilities.as_dict(),
            "sampleLimits": {
                "formats": sorted(suffix.removeprefix(".") for suffix in _SUPPORTED_AUDIO_SUFFIXES),
                "maxBytes": MAX_VOICE_SAMPLE_BYTES,
                "minDurationSeconds": MIN_VOICE_SAMPLE_SECONDS,
                "maxDurationSeconds": MAX_VOICE_SAMPLE_SECONDS,
            },
            **payload,
        }

    def capabilities(self, model_ref: str) -> dict[str, Any]:
        context = self.resolve_context(model_ref)
        return self._result(context, voices=[])

    async def list_voices(self, model_ref: str) -> dict[str, Any]:
        context = self.resolve_context(model_ref)
        if not context.capabilities.list:
            raise VoiceManagerError("Voice listing is not supported.", code="unsupported_action", provider=context.adapter_id)
        return self._result(context, **await self._adapter(context).list_voices(context))

    async def delete_voice(self, model_ref: str, voice_id: str, voice_type: str = "") -> dict[str, Any]:
        context = self.resolve_context(model_ref)
        normalized_voice_id = _text(voice_id)
        if not normalized_voice_id:
            raise VoiceManagerError("voiceId is required.", code="voice_id_required", provider=context.adapter_id)
        if not context.capabilities.delete:
            raise VoiceManagerError("Voice deletion is not supported.", code="unsupported_action", provider=context.adapter_id)
        return self._result(
            context,
            **await self._adapter(context).delete_voice(context, normalized_voice_id, voice_type),
        )

    async def clone_voice(
        self,
        model_ref: str,
        *,
        voice_id: str,
        preview_text: str,
        filename: str,
        content_type: str,
        audio_bytes: bytes,
    ) -> dict[str, Any]:
        context = self.resolve_context(model_ref)
        normalized_voice_id = _text(voice_id)
        if not normalized_voice_id:
            raise VoiceManagerError("voiceId is required.", code="voice_id_required", provider=context.adapter_id)
        if not context.capabilities.clone:
            raise VoiceManagerError("Voice cloning is not supported.", code="unsupported_action", provider=context.adapter_id)
        duration = _validate_voice_sample(filename, audio_bytes)
        result = await self._adapter(context).clone_voice(
            context,
            voice_id=normalized_voice_id,
            preview_text=_text(preview_text),
            filename=filename,
            content_type=content_type,
            audio_bytes=audio_bytes,
        )
        return self._result(context, sampleDurationSeconds=round(duration, 3), **result)


voice_customization_manager = VoiceCustomizationManager()


__all__ = [
    "MAX_VOICE_SAMPLE_BYTES",
    "MAX_VOICE_SAMPLE_SECONDS",
    "MIN_VOICE_SAMPLE_SECONDS",
    "VoiceCustomizationCapabilities",
    "VoiceCustomizationManager",
    "VoiceManagerError",
    "voice_customization_manager",
]
