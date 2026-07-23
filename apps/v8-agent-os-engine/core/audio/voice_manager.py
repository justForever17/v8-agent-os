from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, replace
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
_ALIYUN_VOICE_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9]{1,10}$")
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
    commit: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "clone": self.clone,
            "design": self.design,
            "list": self.list,
            "delete": self.delete,
            "preview": self.preview,
            "commit": self.commit,
        }


@dataclass(frozen=True)
class VoiceAssetPolicy:
    asset_scope: str
    inventory_source: str
    design_flow: str
    eligibility_status: str
    consent_required: bool
    docs_url: str
    application_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "assetScope": self.asset_scope,
            "inventorySource": self.inventory_source,
            "designFlow": self.design_flow,
            "eligibilityStatus": self.eligibility_status,
            "consentRequired": self.consent_required,
            "docsUrl": self.docs_url,
            "applicationUrl": self.application_url,
        }


@dataclass(frozen=True)
class VoiceDesignConstraints:
    prompt_min_chars: int = 1
    prompt_max_chars: int | None = None
    preview_text_min_chars: int = 1
    preview_text_max_chars: int | None = None
    voice_id_required: bool = False
    voice_id_role: str = "none"
    voice_id_min_chars: int | None = None
    voice_id_max_chars: int | None = None
    voice_id_format: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt": {
                "required": True,
                "minChars": self.prompt_min_chars,
                "maxChars": self.prompt_max_chars,
            },
            "previewText": {
                "required": True,
                "minChars": self.preview_text_min_chars,
                "maxChars": self.preview_text_max_chars,
            },
            "voiceId": {
                "required": self.voice_id_required,
                "role": self.voice_id_role,
                "minChars": self.voice_id_min_chars,
                "maxChars": self.voice_id_max_chars,
                "format": self.voice_id_format,
            },
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
    asset_policy: VoiceAssetPolicy
    design_constraints: VoiceDesignConstraints


MINIMAX_CAPABILITIES = VoiceCustomizationCapabilities(
    clone=True,
    design=True,
    list=True,
    delete=True,
    preview=True,
)
ALIYUN_CAPABILITIES = VoiceCustomizationCapabilities(
    clone=True,
    design=True,
    list=True,
    delete=True,
    preview=True,
)
VOLCENGINE_CAPABILITIES = VoiceCustomizationCapabilities(
    clone=True,
    design=True,
    list=False,
    delete=False,
    preview=True,
)
MIMO_CAPABILITIES = VoiceCustomizationCapabilities(
    clone=True,
    design=True,
    list=False,
    delete=False,
    preview=True,
)
ELEVENLABS_CAPABILITIES = VoiceCustomizationCapabilities(
    clone=True,
    design=True,
    list=True,
    delete=True,
    preview=True,
    commit=True,
)
QUALIFICATION_ONLY_CAPABILITIES = VoiceCustomizationCapabilities(
    clone=False,
    design=False,
    list=False,
    delete=False,
    preview=False,
)


MINIMAX_DESIGN_CONSTRAINTS = VoiceDesignConstraints(
    preview_text_max_chars=500,
    voice_id_role="custom_id",
    voice_id_min_chars=8,
    voice_id_max_chars=256,
    voice_id_format="ascii_identifier",
)
ALIYUN_DESIGN_CONSTRAINTS = VoiceDesignConstraints(
    prompt_max_chars=500,
    preview_text_max_chars=200,
    voice_id_required=True,
    voice_id_role="prefix",
    voice_id_min_chars=1,
    voice_id_max_chars=10,
    voice_id_format="ascii_alphanumeric",
)
VOLCENGINE_DESIGN_CONSTRAINTS = VoiceDesignConstraints(
    prompt_max_chars=200,
    preview_text_max_chars=300,
    voice_id_required=True,
    voice_id_role="provider_slot",
)
MIMO_DESIGN_CONSTRAINTS = VoiceDesignConstraints()
ELEVENLABS_DESIGN_CONSTRAINTS = VoiceDesignConstraints(
    prompt_min_chars=20,
    prompt_max_chars=1000,
    preview_text_min_chars=100,
    preview_text_max_chars=1000,
)
NO_DESIGN_CONSTRAINTS = VoiceDesignConstraints()


MINIMAX_ASSET_POLICY = VoiceAssetPolicy(
    asset_scope="durable_remote",
    inventory_source="remote",
    design_flow="direct",
    eligibility_status="available",
    consent_required=False,
    docs_url="https://platform.minimaxi.com/docs/api-reference/voice-design-design",
)
ALIYUN_ASSET_POLICY = VoiceAssetPolicy(
    asset_scope="durable_remote",
    inventory_source="remote",
    design_flow="direct",
    eligibility_status="available",
    consent_required=False,
    docs_url="https://help.aliyun.com/en/model-studio/voice-design-api-references",
)
VOLCENGINE_ASSET_POLICY = VoiceAssetPolicy(
    asset_scope="provider_slot",
    inventory_source="local_projection",
    design_flow="direct",
    eligibility_status="available",
    consent_required=False,
    docs_url="https://www.volcengine.com/docs/6561/2277844",
)
MIMO_ASSET_POLICY = VoiceAssetPolicy(
    asset_scope="ephemeral_request",
    inventory_source="none",
    design_flow="ephemeral",
    eligibility_status="available",
    consent_required=False,
    docs_url="https://platform.xiaomimimo.com/static/docs/usage-guide/speech-synthesis-v2.5.md",
)
ELEVENLABS_ASSET_POLICY = VoiceAssetPolicy(
    asset_scope="durable_remote",
    inventory_source="remote",
    design_flow="preview_then_commit",
    eligibility_status="available",
    consent_required=False,
    docs_url="https://elevenlabs.io/docs/api-reference/text-to-voice/design",
)


QUALIFICATION_POLICIES = {
    "openai_custom_voice": VoiceAssetPolicy(
        asset_scope="qualification_only",
        inventory_source="none",
        design_flow="qualification_only",
        eligibility_status="requires_approval",
        consent_required=True,
        docs_url="https://developers.openai.com/api/docs/guides/text-to-speech#custom-voices",
        application_url="https://help.openai.com/en/articles/10362446-api-model-availability-by-usage-tier-and-verification-status",
    ),
    "google_instant_custom_voice": VoiceAssetPolicy(
        asset_scope="qualification_only",
        inventory_source="none",
        design_flow="qualification_only",
        eligibility_status="requires_approval",
        consent_required=True,
        docs_url="https://cloud.google.com/text-to-speech/docs/chirp3-instant-custom-voice",
        application_url="https://cloud.google.com/text-to-speech/docs/chirp3-instant-custom-voice#request-access",
    ),
    "azure_personal_voice": VoiceAssetPolicy(
        asset_scope="qualification_only",
        inventory_source="none",
        design_flow="qualification_only",
        eligibility_status="requires_approval",
        consent_required=True,
        docs_url="https://learn.microsoft.com/azure/ai-services/speech-service/personal-voice-overview",
        application_url="https://aka.ms/customneural",
    ),
}


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


def _audio_data_url(audio_bytes: bytes, media_type: str) -> str:
    normalized_type = _text(media_type) or "audio/wav"
    return f"data:{normalized_type};base64,{base64.b64encode(audio_bytes).decode('ascii')}"


def _base64_audio_data_url(value: Any, media_type: str = "audio/wav") -> str:
    normalized = _text(value)
    if not normalized:
        return ""
    if normalized.startswith("data:audio/"):
        return normalized
    try:
        base64.b64decode(normalized, validate=True)
    except (ValueError, TypeError):
        return ""
    return f"data:{media_type};base64,{normalized}"


def _audio_media_type_for_format(value: Any, fallback: str = "audio/wav") -> str:
    normalized = _text(value).lower().lstrip(".")
    return {
        "mp3": "audio/mpeg",
        "mpeg": "audio/mpeg",
        "m4a": "audio/mp4",
        "mp4": "audio/mp4",
        "ogg": "audio/ogg",
        "opus": "audio/ogg",
        "wav": "audio/wav",
    }.get(normalized, fallback)


def _hex_audio_data_url(value: Any, media_type: str = "audio/mpeg") -> str:
    normalized = _text(value)
    if not normalized:
        return ""
    try:
        audio_bytes = bytes.fromhex(normalized)
    except ValueError:
        return ""
    return _audio_data_url(audio_bytes, media_type)


def _voice_items(payload: Any) -> list[dict[str, Any]]:
    root = _as_dict(payload)
    output = _as_dict(root.get("output"))
    candidates = output.get("voice_list") or output.get("voices") or root.get("voices") or []
    voices: list[dict[str, Any]] = []
    for raw_item in candidates:
        item = _as_dict(raw_item)
        voice_id = _text(item.get("voice_id") or item.get("voiceId") or item.get("id"))
        if not voice_id:
            continue
        name = _text(item.get("name") or item.get("voice_name") or item.get("prefix")) or voice_id
        voices.append(
            {
                "value": voice_id,
                "label": f"{name} · {voice_id}" if name != voice_id else voice_id,
                "group": "custom",
                "deletable": True,
                "source": "remote",
                "availability": "available",
            }
        )
    return _dedupe_voices(voices)


class VoiceCustomizationAdapter:
    adapter_id = ""
    capabilities = VoiceCustomizationCapabilities(False, False, False, False, False)
    asset_policy = VoiceAssetPolicy("none", "none", "none", "unavailable", False, "")
    design_constraints = NO_DESIGN_CONSTRAINTS

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

    async def design_voice(
        self,
        context: VoiceAdapterContext,
        *,
        prompt: str,
        preview_text: str,
        voice_id: str,
        voice_name: str,
    ) -> dict[str, Any]:
        raise VoiceManagerError("Voice design is not supported.", code="unsupported_action", provider=self.adapter_id)

    async def commit_design(
        self,
        context: VoiceAdapterContext,
        *,
        generated_voice_id: str,
        voice_name: str,
        voice_description: str,
    ) -> dict[str, Any]:
        raise VoiceManagerError("Voice design commit is not supported.", code="unsupported_action", provider=self.adapter_id)


class MiniMaxVoiceAdapter(VoiceCustomizationAdapter):
    adapter_id = "minimax_tts"
    capabilities = MINIMAX_CAPABILITIES
    asset_policy = MINIMAX_ASSET_POLICY
    design_constraints = MINIMAX_DESIGN_CONSTRAINTS

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

    async def design_voice(
        self,
        context: VoiceAdapterContext,
        *,
        prompt: str,
        preview_text: str,
        voice_id: str,
        voice_name: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"prompt": prompt, "preview_text": preview_text}
        if voice_id:
            body["voice_id"] = voice_id
        async with self._client() as client:
            response = await client.post(
                f"{self._api_root(context.base_url)}/voice_design",
                headers={**self._headers(context), "Content-Type": "application/json"},
                json=body,
            )
        payload = _response_json(response, self.adapter_id)
        _assert_minimax_success(response, payload)
        resolved_voice_id = _text(payload.get("voice_id") or _as_dict(payload.get("data")).get("voice_id")) or voice_id
        result: dict[str, Any] = {
            "voiceId": resolved_voice_id,
            "availability": "available",
        }
        preview_audio = _hex_audio_data_url(payload.get("trial_audio"))
        if preview_audio:
            result["previewAudio"] = preview_audio
        return result

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
    asset_policy = ALIYUN_ASSET_POLICY
    design_constraints = ALIYUN_DESIGN_CONSTRAINTS

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
        async with self._client(30.0) as client:
            response = await client.post(
                self._endpoint(context),
                headers={"Authorization": f"Bearer {context.api_key}", "Content-Type": "application/json"},
                json={
                    "model": "voice-enrollment",
                    "input": {"action": "list_voice", "page_index": 0, "page_size": 100},
                },
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        return {"voices": _dedupe_voices([*ALIYUN_PRESET_VOICES, *_voice_items(payload)])}

    async def delete_voice(self, context: VoiceAdapterContext, voice_id: str, voice_type: str) -> dict[str, Any]:
        async with self._client(30.0) as client:
            response = await client.post(
                self._endpoint(context),
                headers={"Authorization": f"Bearer {context.api_key}", "Content-Type": "application/json"},
                json={"model": "voice-enrollment", "input": {"action": "delete_voice", "voice_id": voice_id}},
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        return {"voiceId": voice_id}

    async def design_voice(
        self,
        context: VoiceAdapterContext,
        *,
        prompt: str,
        preview_text: str,
        voice_id: str,
        voice_name: str,
    ) -> dict[str, Any]:
        input_payload: dict[str, Any] = {
            "action": "create_voice",
            "target_model": context.provider_model_id,
            "voice_prompt": prompt,
            "preview_text": preview_text,
            "language_hints": ["zh"],
        }
        prefix = voice_id or voice_name
        input_payload["prefix"] = prefix
        async with self._client() as client:
            response = await client.post(
                self._endpoint(context),
                headers={"Authorization": f"Bearer {context.api_key}", "Content-Type": "application/json"},
                json={
                    "model": "voice-enrollment",
                    "input": input_payload,
                    "parameters": {"sample_rate": 24000, "response_format": "wav"},
                },
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        output = _as_dict(payload.get("output"))
        resolved_voice_id = _text(output.get("voice_id") or output.get("voiceId")) or prefix
        result: dict[str, Any] = {"voiceId": resolved_voice_id, "availability": "available"}
        preview_payload = _as_dict(output.get("preview_audio"))
        preview_audio = _base64_audio_data_url(
            preview_payload.get("data")
            or output.get("preview_audio")
            or output.get("audio_data")
            or output.get("audio"),
            _audio_media_type_for_format(preview_payload.get("response_format"), "audio/wav"),
        )
        preview_url = _text(output.get("preview_url") or output.get("audio_url"))
        if preview_audio:
            result["previewAudio"] = preview_audio
        elif preview_url.startswith(("http://", "https://")):
            result["previewAudioUrl"] = preview_url
        return result

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
        return {"voiceId": resolved_voice_id, "availability": "available"}


class VolcengineVoiceAdapter(VoiceCustomizationAdapter):
    adapter_id = "volcengine_doubao_voice"
    capabilities = VOLCENGINE_CAPABILITIES
    asset_policy = VOLCENGINE_ASSET_POLICY
    design_constraints = VOLCENGINE_DESIGN_CONSTRAINTS

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
        headers = {
            "Content-Type": "application/json",
            "X-Api-Request-Id": str(uuid4()),
        }
        if app_id:
            headers.update({"X-Api-App-Key": app_id, "X-Api-Access-Key": context.api_key})
        else:
            headers["X-Api-Key"] = context.api_key
        return headers

    async def design_voice(
        self,
        context: VoiceAdapterContext,
        *,
        prompt: str,
        preview_text: str,
        voice_id: str,
        voice_name: str,
    ) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post(
                self._endpoint(context, "voice_design"),
                headers=self._headers(context),
                json={
                    "speaker_id": voice_id,
                    "text": preview_text,
                    "prompt": {"text_prompt": prompt},
                    "language": 0,
                },
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        result: dict[str, Any] = {
            "voiceId": _text(payload.get("speaker_id")) or voice_id,
            "availability": "provider_slot",
        }
        preview_url = _text(payload.get("demo_audio"))
        if preview_url.startswith(("http://", "https://")):
            result["previewAudioUrl"] = preview_url
        return result

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
        audio_format = _audio_suffix(filename).removeprefix(".")
        extra_params: dict[str, Any] = {}
        if preview_text:
            extra_params["demo_text"] = preview_text
        async with self._client() as client:
            response = await client.post(
                self._endpoint(context, "voice_clone"),
                headers=self._headers(context),
                json={
                    "speaker_id": voice_id,
                    "audio": {
                        "data": base64.b64encode(audio_bytes).decode("ascii"),
                        "format": audio_format,
                    },
                    "language": 0,
                    "extra_params": extra_params,
                },
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        resolved_voice_id = _text(payload.get("speaker_id")) or voice_id
        await _upsert_ledger_entry(context, resolved_voice_id)
        result: dict[str, Any] = {"voiceId": resolved_voice_id, "availability": "provider_slot"}
        speaker_status = payload.get("speaker_status") or []
        first_status = _as_dict(speaker_status[0]) if isinstance(speaker_status, list) and speaker_status else {}
        preview_url = _text(first_status.get("demo_audio") or payload.get("demo_audio"))
        if preview_url.startswith(("http://", "https://")):
            result["previewAudioUrl"] = preview_url
        return result


class XiaomiMiMoVoiceAdapter(VoiceCustomizationAdapter):
    adapter_id = "xiaomi_mimo_tts"
    capabilities = MIMO_CAPABILITIES
    asset_policy = MIMO_ASSET_POLICY
    design_constraints = MIMO_DESIGN_CONSTRAINTS

    @staticmethod
    def _endpoint(context: VoiceAdapterContext) -> str:
        base = (_text(context.base_url) or "https://api.xiaomimimo.com/v1").rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return f"{base}/chat/completions"

    @staticmethod
    def _headers(context: VoiceAdapterContext) -> dict[str, str]:
        return {"api-key": context.api_key, "Content-Type": "application/json"}

    @staticmethod
    def _preview_audio(payload: dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        choice = _as_dict(choices[0]) if choices else {}
        message = _as_dict(choice.get("message"))
        audio = _as_dict(message.get("audio"))
        return _base64_audio_data_url(audio.get("data"), _text(audio.get("media_type")) or "audio/wav")

    async def design_voice(
        self,
        context: VoiceAdapterContext,
        *,
        prompt: str,
        preview_text: str,
        voice_id: str,
        voice_name: str,
    ) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post(
                self._endpoint(context),
                headers=self._headers(context),
                json={
                    "model": "mimo-v2.5-tts-voicedesign",
                    "messages": [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": preview_text},
                    ],
                    "audio": {"format": "wav", "optimize_text_preview": True},
                },
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        preview_audio = self._preview_audio(payload)
        if not preview_audio:
            raise VoiceManagerError(
                "MiMo voice design returned no playable preview audio.",
                status_code=502,
                code="preview_audio_missing",
                provider=self.adapter_id,
                trace_id=_trace_id(response, payload),
            )
        return {"ephemeral": True, "availability": "ephemeral", "previewAudio": preview_audio}

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
        media_type = _text(content_type)
        if not media_type.startswith("audio/"):
            media_type = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}.get(
                _audio_suffix(filename),
                "audio/mpeg",
            )
        async with self._client() as client:
            response = await client.post(
                self._endpoint(context),
                headers=self._headers(context),
                json={
                    "model": "mimo-v2.5-tts-voiceclone",
                    "messages": [
                        {"role": "user", "content": ""},
                        {"role": "assistant", "content": preview_text},
                    ],
                    "audio": {"format": "wav", "voice": _audio_data_url(audio_bytes, media_type)},
                },
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        preview_audio = self._preview_audio(payload)
        if not preview_audio:
            raise VoiceManagerError(
                "MiMo voice clone returned no playable preview audio.",
                status_code=502,
                code="preview_audio_missing",
                provider=self.adapter_id,
                trace_id=_trace_id(response, payload),
            )
        return {"ephemeral": True, "availability": "ephemeral", "previewAudio": preview_audio}


class ElevenLabsVoiceAdapter(VoiceCustomizationAdapter):
    adapter_id = "elevenlabs_tts"
    capabilities = ELEVENLABS_CAPABILITIES
    asset_policy = ELEVENLABS_ASSET_POLICY
    design_constraints = ELEVENLABS_DESIGN_CONSTRAINTS

    @staticmethod
    def _origin(context: VoiceAdapterContext) -> str:
        base = (_text(context.base_url) or "https://api.elevenlabs.io/v1").rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise VoiceManagerError("ElevenLabs base URL is invalid.", code="invalid_provider_base_url")
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _headers(context: VoiceAdapterContext) -> dict[str, str]:
        return {"xi-api-key": context.api_key}

    async def list_voices(self, context: VoiceAdapterContext) -> dict[str, Any]:
        async with self._client(30.0) as client:
            response = await client.get(
                f"{self._origin(context)}/v2/voices",
                headers=self._headers(context),
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        voices: list[dict[str, Any]] = []
        for raw_item in payload.get("voices") or []:
            item = _as_dict(raw_item)
            voice_id = _text(item.get("voice_id") or item.get("voiceId"))
            if not voice_id:
                continue
            name = _text(item.get("name")) or voice_id
            category = _text(item.get("category")).lower()
            voices.append(
                {
                    "value": voice_id,
                    "label": f"{name} · {voice_id}" if name != voice_id else voice_id,
                    "group": category or "custom",
                    "deletable": category not in {"premade", "default"},
                    "source": "remote",
                    "availability": "available",
                }
            )
        return {"voices": _dedupe_voices(voices)}

    async def delete_voice(self, context: VoiceAdapterContext, voice_id: str, voice_type: str) -> dict[str, Any]:
        async with self._client(30.0) as client:
            response = await client.delete(
                f"{self._origin(context)}/v1/voices/{voice_id}",
                headers=self._headers(context),
            )
        payload = _response_json(response, self.adapter_id) if response.content else {}
        _assert_generic_provider_success(response, payload, self.adapter_id)
        return {"voiceId": voice_id}

    async def design_voice(
        self,
        context: VoiceAdapterContext,
        *,
        prompt: str,
        preview_text: str,
        voice_id: str,
        voice_name: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "voice_description": prompt,
            "model_id": "eleven_multilingual_ttv_v2",
        }
        if preview_text:
            body["text"] = preview_text
        async with self._client() as client:
            response = await client.post(
                f"{self._origin(context)}/v1/text-to-voice/design",
                headers={**self._headers(context), "Content-Type": "application/json"},
                json=body,
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        candidates: list[dict[str, Any]] = []
        for raw_item in payload.get("previews") or []:
            item = _as_dict(raw_item)
            generated_voice_id = _text(item.get("generated_voice_id"))
            preview_audio = _base64_audio_data_url(
                item.get("audio_base_64"),
                _text(item.get("media_type")) or "audio/mpeg",
            )
            if generated_voice_id and preview_audio:
                candidates.append({"generatedVoiceId": generated_voice_id, "previewAudio": preview_audio})
        if not candidates:
            raise VoiceManagerError(
                "ElevenLabs voice design returned no usable candidates.",
                status_code=502,
                code="design_candidates_missing",
                provider=self.adapter_id,
                trace_id=_trace_id(response, payload),
            )
        return {"candidates": candidates, "availability": "preview"}

    async def commit_design(
        self,
        context: VoiceAdapterContext,
        *,
        generated_voice_id: str,
        voice_name: str,
        voice_description: str,
    ) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.post(
                f"{self._origin(context)}/v1/text-to-voice",
                headers={**self._headers(context), "Content-Type": "application/json"},
                json={
                    "voice_name": voice_name,
                    "voice_description": voice_description,
                    "generated_voice_id": generated_voice_id,
                },
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        voice_id = _text(payload.get("voice_id") or payload.get("voiceId"))
        if not voice_id:
            raise VoiceManagerError(
                "ElevenLabs committed the design without returning a voice ID.",
                status_code=502,
                code="voice_id_missing",
                provider=self.adapter_id,
                trace_id=_trace_id(response, payload),
            )
        return {"voiceId": voice_id, "availability": "available"}

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
                f"{self._origin(context)}/v1/voices/add",
                headers=self._headers(context),
                data={"name": voice_id},
                files={"files": (filename, audio_bytes, _text(content_type) or "application/octet-stream")},
            )
        payload = _response_json(response, self.adapter_id)
        _assert_generic_provider_success(response, payload, self.adapter_id)
        resolved_voice_id = _text(payload.get("voice_id") or payload.get("voiceId"))
        if not resolved_voice_id:
            raise VoiceManagerError(
                "ElevenLabs voice clone returned no voice ID.",
                status_code=502,
                code="voice_id_missing",
                provider=self.adapter_id,
                trace_id=_trace_id(response, payload),
            )
        return {"voiceId": resolved_voice_id, "availability": "available"}


class QualificationOnlyVoiceAdapter(VoiceCustomizationAdapter):
    capabilities = QUALIFICATION_ONLY_CAPABILITIES
    design_constraints = NO_DESIGN_CONSTRAINTS

    def __init__(self, adapter_id: str, asset_policy: VoiceAssetPolicy) -> None:
        super().__init__()
        self.adapter_id = adapter_id
        self.asset_policy = asset_policy


_ADAPTER_ID_ALIASES = {
    "minimax_tts": "minimax_tts",
    "dashscope_cosyvoice_tts": "aliyun_bailian_cosyvoice",
    "aliyun_bailian_cosyvoice": "aliyun_bailian_cosyvoice",
    "volcengine_ark_voice": "volcengine_doubao_voice",
    "volcengine_doubao_voice": "volcengine_doubao_voice",
    "xiaomi_mimo_tts": "xiaomi_mimo_tts",
    "elevenlabs_tts": "elevenlabs_tts",
    "openai_audio": "openai_custom_voice",
    "openai_audio_speech": "openai_custom_voice",
    "google_cloud_tts": "google_instant_custom_voice",
    "azure_speech_tts": "azure_personal_voice",
}


class VoiceCustomizationManager:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        adapters = (
            MiniMaxVoiceAdapter(transport=transport),
            AliyunCosyVoiceAdapter(transport=transport),
            VolcengineVoiceAdapter(transport=transport),
            XiaomiMiMoVoiceAdapter(transport=transport),
            ElevenLabsVoiceAdapter(transport=transport),
            *(
                QualificationOnlyVoiceAdapter(adapter_id, policy)
                for adapter_id, policy in QUALIFICATION_POLICIES.items()
            ),
        )
        self._adapters = {adapter.adapter_id: adapter for adapter in adapters}

    def resolve_context(self, model_ref: str, *, require_credential: bool = True) -> VoiceAdapterContext:
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
        credential_configured = bool(api_key) and not api_key.startswith("oauth:") and "***" not in api_key
        if require_credential and not credential_configured and adapter.asset_policy.asset_scope != "qualification_only":
            raise VoiceManagerError(
                f"{adapter_id} API key is not configured.",
                status_code=409,
                code="credential_missing",
                provider=adapter_id,
            )
        asset_policy = adapter.asset_policy
        if asset_policy.asset_scope == "qualification_only":
            qualification = (
                _as_dict(model_meta.get("voiceCustomization"))
                or _as_dict(media_limits.get("voiceCustomization"))
                or _as_dict(provider_meta.get("voiceCustomization"))
            )
            asset_policy = replace(
                asset_policy,
                eligibility_status="eligible" if qualification.get("eligible") is True else "requires_approval",
            )
        provider_model_id = _text(media_limits.get("providerModelId")) or model_id.rsplit("/", 1)[-1]
        return VoiceAdapterContext(
            adapter_id=adapter_id,
            model_ref=normalized_ref,
            model_id=model_id,
            provider_id=provider_id,
            api_key=api_key if credential_configured else "",
            base_url=_text(provider_meta.get("base_url") or provider_meta.get("baseUrl")),
            provider_model_id=provider_model_id,
            provider_meta=provider_meta,
            model_meta=model_meta,
            media_limits=media_limits,
            capabilities=adapter.capabilities,
            asset_policy=asset_policy,
            design_constraints=adapter.design_constraints,
        )

    def _adapter(self, context: VoiceAdapterContext) -> VoiceCustomizationAdapter:
        return self._adapters[context.adapter_id]

    @staticmethod
    def _validate_design_inputs(
        context: VoiceAdapterContext,
        *,
        prompt: str,
        preview_text: str,
        voice_id: str,
    ) -> None:
        constraints = context.design_constraints
        if len(prompt) < constraints.prompt_min_chars:
            raise VoiceManagerError(
                f"prompt must contain at least {constraints.prompt_min_chars} characters.",
                code="design_prompt_too_short",
                provider=context.adapter_id,
            )
        if constraints.prompt_max_chars is not None and len(prompt) > constraints.prompt_max_chars:
            raise VoiceManagerError(
                f"prompt must contain no more than {constraints.prompt_max_chars} characters.",
                code="design_prompt_too_long",
                provider=context.adapter_id,
            )
        if len(preview_text) < constraints.preview_text_min_chars:
            raise VoiceManagerError(
                f"previewText must contain at least {constraints.preview_text_min_chars} characters.",
                code="preview_text_too_short",
                provider=context.adapter_id,
            )
        if (
            constraints.preview_text_max_chars is not None
            and len(preview_text) > constraints.preview_text_max_chars
        ):
            raise VoiceManagerError(
                f"previewText must contain no more than {constraints.preview_text_max_chars} characters.",
                code="preview_text_too_long",
                provider=context.adapter_id,
            )
        if constraints.voice_id_required and not voice_id:
            raise VoiceManagerError(
                "voiceId is required for this provider's voice-design flow.",
                code="voice_id_required",
                provider=context.adapter_id,
            )
        if not voice_id:
            return
        if constraints.voice_id_min_chars is not None and len(voice_id) < constraints.voice_id_min_chars:
            raise VoiceManagerError(
                "voiceId does not satisfy the provider's length requirements.",
                code="invalid_voice_id",
                provider=context.adapter_id,
            )
        if constraints.voice_id_max_chars is not None and len(voice_id) > constraints.voice_id_max_chars:
            raise VoiceManagerError(
                "voiceId does not satisfy the provider's length requirements.",
                code="invalid_voice_id",
                provider=context.adapter_id,
            )
        if constraints.voice_id_format == "ascii_identifier" and not _MINIMAX_VOICE_ID_PATTERN.fullmatch(voice_id):
            raise VoiceManagerError(
                "voiceId must start with a letter, use letters, numbers, '-' or '_', and end with a letter or number.",
                code="invalid_voice_id",
                provider=context.adapter_id,
            )
        if constraints.voice_id_format == "ascii_alphanumeric" and not _ALIYUN_VOICE_PREFIX_PATTERN.fullmatch(voice_id):
            raise VoiceManagerError(
                "voiceId must contain only ASCII letters or numbers.",
                code="invalid_voice_id",
                provider=context.adapter_id,
            )

    @staticmethod
    def _result(context: VoiceAdapterContext, **payload: Any) -> dict[str, Any]:
        result = {
            "ok": True,
            "provider": context.adapter_id,
            "capabilities": context.capabilities.as_dict(),
            "assetPolicy": context.asset_policy.as_dict(),
            "credentialStatus": "configured" if context.api_key else "missing",
            "sampleLimits": {
                "formats": sorted(suffix.removeprefix(".") for suffix in _SUPPORTED_AUDIO_SUFFIXES),
                "maxBytes": MAX_VOICE_SAMPLE_BYTES,
                "minDurationSeconds": MIN_VOICE_SAMPLE_SECONDS,
                "maxDurationSeconds": MAX_VOICE_SAMPLE_SECONDS,
            },
            **payload,
        }
        if context.capabilities.design:
            result["designConstraints"] = context.design_constraints.as_dict()
        return result

    def capabilities(self, model_ref: str) -> dict[str, Any]:
        context = self.resolve_context(model_ref, require_credential=False)
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
        if not normalized_voice_id and context.asset_policy.asset_scope != "ephemeral_request":
            raise VoiceManagerError("voiceId is required.", code="voice_id_required", provider=context.adapter_id)
        if not context.capabilities.clone:
            raise VoiceManagerError("Voice cloning is not supported.", code="unsupported_action", provider=context.adapter_id)
        normalized_preview_text = _text(preview_text)
        if context.asset_policy.asset_scope == "ephemeral_request" and not normalized_preview_text:
            raise VoiceManagerError(
                "previewText is required for an ephemeral reference voice.",
                code="preview_text_required",
                provider=context.adapter_id,
            )
        duration = _validate_voice_sample(filename, audio_bytes)
        result = await self._adapter(context).clone_voice(
            context,
            voice_id=normalized_voice_id,
            preview_text=normalized_preview_text,
            filename=filename,
            content_type=content_type,
            audio_bytes=audio_bytes,
        )
        return self._result(context, sampleDurationSeconds=round(duration, 3), **result)

    async def design_voice(
        self,
        model_ref: str,
        *,
        prompt: str,
        preview_text: str,
        voice_id: str = "",
        voice_name: str = "",
    ) -> dict[str, Any]:
        context = self.resolve_context(model_ref)
        if not context.capabilities.design:
            raise VoiceManagerError("Voice design is not supported.", code="unsupported_action", provider=context.adapter_id)
        normalized_prompt = _text(prompt)
        normalized_preview = _text(preview_text)
        normalized_voice_id = _text(voice_id)
        normalized_voice_name = _text(voice_name)
        if not normalized_prompt:
            raise VoiceManagerError("prompt is required.", code="design_prompt_required", provider=context.adapter_id)
        if not normalized_preview:
            raise VoiceManagerError(
                "previewText is required.",
                code="preview_text_required",
                provider=context.adapter_id,
            )
        self._validate_design_inputs(
            context,
            prompt=normalized_prompt,
            preview_text=normalized_preview,
            voice_id=normalized_voice_id,
        )
        return self._result(
            context,
            **await self._adapter(context).design_voice(
                context,
                prompt=normalized_prompt,
                preview_text=normalized_preview,
                voice_id=normalized_voice_id,
                voice_name=normalized_voice_name,
            ),
        )

    async def commit_design(
        self,
        model_ref: str,
        *,
        generated_voice_id: str,
        voice_name: str,
        voice_description: str = "",
    ) -> dict[str, Any]:
        context = self.resolve_context(model_ref)
        if not context.capabilities.commit:
            raise VoiceManagerError(
                "Voice design commit is not supported.",
                code="unsupported_action",
                provider=context.adapter_id,
            )
        normalized_generated_id = _text(generated_voice_id)
        normalized_name = _text(voice_name)
        if not normalized_generated_id:
            raise VoiceManagerError(
                "generatedVoiceId is required.",
                code="generated_voice_id_required",
                provider=context.adapter_id,
            )
        if not normalized_name:
            raise VoiceManagerError("voiceName is required.", code="voice_name_required", provider=context.adapter_id)
        return self._result(
            context,
            **await self._adapter(context).commit_design(
                context,
                generated_voice_id=normalized_generated_id,
                voice_name=normalized_name,
                voice_description=_text(voice_description),
            ),
        )


voice_customization_manager = VoiceCustomizationManager()


__all__ = [
    "MAX_VOICE_SAMPLE_BYTES",
    "MAX_VOICE_SAMPLE_SECONDS",
    "MIN_VOICE_SAMPLE_SECONDS",
    "VoiceAssetPolicy",
    "VoiceCustomizationCapabilities",
    "VoiceDesignConstraints",
    "VoiceCustomizationManager",
    "VoiceManagerError",
    "voice_customization_manager",
]
