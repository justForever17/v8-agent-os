from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from core.model_control_plane import model_control_plane


MAX_VOICE_SAMPLE_BYTES = 20 * 1024 * 1024
_MINIMAX_VOICE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]$")
_SUPPORTED_AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav"}


class VoiceManagerError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "voice_manager_error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class MiniMaxVoiceContext:
    model_ref: str
    model_id: str
    api_key: str
    api_root: str
    tts_model: str


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _detect_minimax_adapter(
    provider_id: str,
    model_id: str,
    provider_meta: dict[str, Any],
    model_meta: dict[str, Any],
    media_limits: dict[str, Any],
) -> bool:
    adapter_provider_id = _text(media_limits.get("adapterProviderId"))
    api_standard = _text(media_limits.get("apiStandard"))
    parameter_profile = _text(model_meta.get("parameterProfile"))
    if "minimax_tts" in {adapter_provider_id, api_standard, parameter_profile}:
        return True
    probe = " ".join(
        [
            provider_id,
            model_id,
            _text(provider_meta.get("name")),
            _text(provider_meta.get("base_url") or provider_meta.get("baseUrl")),
        ]
    ).lower()
    return "minimax" in probe and ("t2a" in probe or "speech" in probe)


def _minimax_api_root(base_url: str) -> str:
    normalized = _text(base_url) or "https://api.minimaxi.com/v1"
    normalized = normalized.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VoiceManagerError("MiniMax base URL is invalid.", code="invalid_provider_base_url")
    version_match = re.match(r"^(.*?/v1)(?:/.*)?$", normalized, flags=re.IGNORECASE)
    if version_match:
        return version_match.group(1).rstrip("/")
    return f"{normalized}/v1"


def _minimax_tts_model(model_id: str) -> str:
    leaf = _text(model_id).split("/")[-1]
    return leaf if leaf.startswith("speech-") else "speech-2.8-hd"


def _extract_vendor_error(payload: Any, fallback: str) -> str:
    root = _as_dict(payload)
    base_resp = _as_dict(root.get("base_resp") or root.get("baseResp"))
    data = _as_dict(root.get("data"))
    data_base_resp = _as_dict(data.get("base_resp") or data.get("baseResp"))
    for candidate in (
        base_resp.get("status_msg"),
        base_resp.get("message"),
        data_base_resp.get("status_msg"),
        root.get("status_text"),
        root.get("statusText"),
        root.get("error"),
        root.get("message"),
    ):
        value = _text(candidate)
        if value:
            return value
    return fallback


def _assert_vendor_success(response: httpx.Response, payload: Any) -> None:
    root = _as_dict(payload)
    base_resp = _as_dict(root.get("base_resp") or root.get("baseResp"))
    data = _as_dict(root.get("data"))
    if not base_resp:
        base_resp = _as_dict(data.get("base_resp") or data.get("baseResp"))
    vendor_status = base_resp.get("status_code")
    vendor_failed = vendor_status is not None and str(vendor_status).strip() not in {"", "0"}
    if response.is_success and not vendor_failed:
        return
    message = _extract_vendor_error(payload, f"MiniMax API returned HTTP {response.status_code}.")
    raise VoiceManagerError(
        message,
        status_code=response.status_code if not response.is_success else 502,
        code="minimax_request_failed",
    )


def _flatten_minimax_voices(payload: Any) -> list[dict[str, Any]]:
    root = _as_dict(payload)
    data = _as_dict(root.get("data"))
    source = data if data else root
    groups = (
        ("system_voice", "system", False),
        ("voice_cloning", "cloned", True),
        ("voice_generation", "generated", True),
    )
    voices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source_key, group, deletable in groups:
        items = source.get(source_key)
        if not isinstance(items, list):
            continue
        for raw_item in items:
            item = _as_dict(raw_item)
            voice_id = _text(item.get("voice_id") or item.get("voiceId") or item.get("id"))
            if not voice_id or voice_id in seen:
                continue
            seen.add(voice_id)
            name = _text(item.get("voice_name") or item.get("voiceName") or item.get("name")) or voice_id
            voices.append(
                {
                    "value": voice_id,
                    "label": f"{name} · {voice_id}" if name != voice_id else voice_id,
                    "group": group,
                    "deletable": deletable,
                    "source": "remote",
                }
            )
    return voices


class MiniMaxVoiceManager:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    @staticmethod
    def capabilities() -> dict[str, bool]:
        return {
            "supportsVoiceManager": True,
            "supportsList": True,
            "supportsDelete": True,
            "supportsCloneUpload": True,
        }

    def resolve_context(self, model_ref: str) -> MiniMaxVoiceContext:
        normalized_ref = _text(model_ref)
        record = model_control_plane.get_model_record(normalized_ref)
        if not record:
            raise VoiceManagerError("Configured TTS model was not found.", status_code=404, code="model_not_found")
        provider_id = _text(record.get("provider_id"))
        model_id = _text(record.get("model_id"))
        provider_meta = _as_dict(record.get("provider"))
        model_meta = _as_dict(record.get("model"))
        media_limits = _as_dict(model_meta.get("mediaLimits"))
        if not _detect_minimax_adapter(provider_id, model_id, provider_meta, model_meta, media_limits):
            raise VoiceManagerError(
                "Selected model is not a MiniMax TTS voice-management model.",
                status_code=422,
                code="unsupported_voice_manager",
            )
        api_key = _text(provider_meta.get("api_key") or provider_meta.get("apiKey"))
        if not api_key or api_key.startswith("oauth:") or "***" in api_key:
            raise VoiceManagerError(
                "MiniMax API key is not configured.",
                status_code=409,
                code="credential_missing",
            )
        return MiniMaxVoiceContext(
            model_ref=normalized_ref,
            model_id=model_id,
            api_key=api_key,
            api_root=_minimax_api_root(_text(provider_meta.get("base_url") or provider_meta.get("baseUrl"))),
            tts_model=_minimax_tts_model(model_id),
        )

    def _client(self, timeout_seconds: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            transport=self._transport,
        )

    @staticmethod
    def _headers(context: MiniMaxVoiceContext) -> dict[str, str]:
        return {"Authorization": f"Bearer {context.api_key}"}

    async def list_voices(self, model_ref: str) -> dict[str, Any]:
        context = self.resolve_context(model_ref)
        async with self._client(30.0) as client:
            response = await client.post(
                f"{context.api_root}/get_voice",
                headers={**self._headers(context), "Content-Type": "application/json"},
                json={"voice_type": "all"},
            )
        payload = self._response_json(response)
        _assert_vendor_success(response, payload)
        return self._result(context, voices=_flatten_minimax_voices(payload))

    async def delete_voice(self, model_ref: str, voice_id: str, voice_type: str) -> dict[str, Any]:
        context = self.resolve_context(model_ref)
        normalized_voice_id = _text(voice_id)
        normalized_voice_type = _text(voice_type) or "voice_cloning"
        if not normalized_voice_id:
            raise VoiceManagerError("voiceId is required.", code="voice_id_required")
        if normalized_voice_type not in {"voice_cloning", "voice_generation"}:
            raise VoiceManagerError("voiceType must be voice_cloning or voice_generation.", code="invalid_voice_type")
        async with self._client(30.0) as client:
            response = await client.post(
                f"{context.api_root}/delete_voice",
                headers={**self._headers(context), "Content-Type": "application/json"},
                json={"voice_id": normalized_voice_id, "voice_type": normalized_voice_type},
            )
        payload = self._response_json(response)
        _assert_vendor_success(response, payload)
        return self._result(context, voiceId=normalized_voice_id)

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
        if not _MINIMAX_VOICE_ID_PATTERN.fullmatch(normalized_voice_id):
            raise VoiceManagerError(
                "voiceId must be 8-256 characters, start with a letter, use letters, numbers, '-' or '_', and end with a letter or number.",
                code="invalid_voice_id",
            )
        if not audio_bytes:
            raise VoiceManagerError("Sample audio file is required.", code="sample_required")
        if len(audio_bytes) > MAX_VOICE_SAMPLE_BYTES:
            raise VoiceManagerError("Sample audio file exceeds the 20MB limit.", code="sample_too_large")
        suffix = "." + _text(filename).rsplit(".", 1)[-1].lower() if "." in _text(filename) else ""
        if suffix not in _SUPPORTED_AUDIO_SUFFIXES:
            raise VoiceManagerError("MiniMax voice cloning accepts MP3, M4A, or WAV audio.", code="unsupported_audio_format")
        normalized_content_type = _text(content_type) or "application/octet-stream"
        upload_files = {"file": (filename or f"sample{suffix}", audio_bytes, normalized_content_type)}
        async with self._client(120.0) as client:
            upload_response = await client.post(
                f"{context.api_root}/files/upload",
                headers=self._headers(context),
                data={"purpose": "voice_clone"},
                files=upload_files,
            )
            upload_payload = self._response_json(upload_response)
            _assert_vendor_success(upload_response, upload_payload)
            file_id = self._file_id(upload_payload)
            if not file_id:
                raise VoiceManagerError(
                    "MiniMax upload succeeded but no file_id was returned.",
                    status_code=502,
                    code="upload_file_id_missing",
                )
            clone_body: dict[str, Any] = {"file_id": file_id, "voice_id": normalized_voice_id}
            normalized_preview = _text(preview_text)
            if normalized_preview:
                clone_body.update({"text": normalized_preview, "model": context.tts_model})
            clone_response = await client.post(
                f"{context.api_root}/voice_clone",
                headers={**self._headers(context), "Content-Type": "application/json"},
                json=clone_body,
            )
        clone_payload = self._response_json(clone_response)
        _assert_vendor_success(clone_response, clone_payload)
        return self._result(context, voiceId=normalized_voice_id, fileId=file_id)

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise VoiceManagerError(
                f"MiniMax API returned a non-JSON response (HTTP {response.status_code}).",
                status_code=502,
                code="invalid_vendor_response",
            ) from exc
        return _as_dict(payload)

    @staticmethod
    def _file_id(payload: dict[str, Any]) -> str:
        candidates = (
            _as_dict(payload.get("file")).get("file_id"),
            _as_dict(payload.get("file")).get("id"),
            _as_dict(payload.get("data")).get("file_id"),
            _as_dict(_as_dict(payload.get("data")).get("file")).get("file_id"),
            payload.get("file_id"),
        )
        for candidate in candidates:
            value = _text(candidate)
            if value:
                return value
        return ""

    def _result(self, context: MiniMaxVoiceContext, **payload: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "provider": "minimax_tts",
            "capabilities": self.capabilities(),
            **payload,
        }


minimax_voice_manager = MiniMaxVoiceManager()


__all__ = [
    "MAX_VOICE_SAMPLE_BYTES",
    "MiniMaxVoiceManager",
    "VoiceManagerError",
    "minimax_voice_manager",
]
