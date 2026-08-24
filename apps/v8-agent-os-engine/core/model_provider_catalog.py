from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import parse_qsl, quote, quote_plus, unquote_plus, urlparse, urlunparse

import requests

from core.model_capability_registry import model_capability_registry
from core.media_model_capability_registry import media_model_capability_registry
from core.model_ref import make_model_ref
from core.model_thinking_control import (
    resolve_reasoning_effort_control_for_metadata,
    resolve_thinking_control_for_metadata,
)
from core.prompt_cache_gateway import prompt_cache_profile_id_for_provider
from core.reasoning_surface_contract import resolve_reasoning_surface_for_metadata


_CATALOG_PATH = Path(__file__).resolve().parent / "model_catalog" / "provider_catalog.json"
_CUSTOM_CATALOG_PATH = Path.home() / ".v8-agent-os" / "model_provider_catalog.custom.json"
_MANAGED_CATALOG_PATH = Path.home() / ".v8-agent-os" / "model_provider_catalog.managed.json"
_CREATIVE_MEDIA_MATRIX_PATH = (
    Path(__file__).resolve().parents[1]
    / "runtimes"
    / "creative_media"
    / "assets"
    / "media_provider_format_matrix.json"
)


def _catalog_payload_digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
_CREATIVE_MEDIA_CAPABILITY_OVERRIDES_PATH = (
    Path(__file__).resolve().parents[1]
    / "runtimes"
    / "creative_media"
    / "assets"
    / "media_model_capability_overrides.json"
)


_MEDIA_MODEL_TYPES = {
    "image": "IMAGE",
    "video": "VIDEO",
    "voice": "VOICE",
    "music": "MUSIC",
    "workflow": "WORKFLOW",
    "model3d": "MODEL3D",
}

_MEDIA_DEFAULT_MODEL_IDS = {
    "openai_images": ["gpt-image-2", "gpt-image-1"],
    "volcengine_seedream": ["doubao-seedream-4-0-250828"],
    "aliyun_bailian_image": ["wan2.6-image"],
    "stability_image": ["stable-image-core"],
    "fal_image": ["fal-image-model"],
    "replicate_image": ["replicate-image-model"],
    "volcengine_seedance": ["doubao-seedance-2-5", "doubao-seedance-2-0-260128", "doubao-seedance-2-0-mini"],
    "aliyun_bailian_video": ["wan2.7-t2v"],
    "google_veo": ["veo-3.1-generate-preview"],
    "runway_video": ["gen4_turbo"],
    "luma_video": ["ray-2"],
    "minimax_video": ["MiniMax-H3", "MiniMax-Hailuo-2.3"],
    "kling_video": ["kling-v2-1"],
    "v8_audio_tts": ["v8-audio-tts"],
    "openai_audio_speech": ["gpt-4o-mini-tts"],
    "elevenlabs_tts": ["eleven_multilingual_v2"],
    "minimax_tts": ["speech-02-hd"],
    "aliyun_bailian_cosyvoice": ["cosyvoice-v3-flash"],
    "mureka_music": ["mureka-o1"],
    "fal_music": ["fal-music-model"],
    "suno_placeholder": ["suno-future-generation"],
    "fal_3d": ["fal-3d-model"],
    "tripo3d_placeholder": ["tripo3d-model"],
}

_VOICE_MODEL_TOKENS = {
    "tts",
    "speech",
    "voice",
    "voiceclone",
    "voice-clone",
    "voicedesign",
    "voice-design",
}
_MUSIC_MODEL_TOKENS = {"music", "song", "mureka", "suno"}

_PLUGIN_ONLY_MEDIA_OPERATION_KINDS = {
    "video.lipsync",
    "video.avatar",
    "video.replacement",
    "video.style_repaint",
    "video.video_edit",
}

_PLUGIN_ONLY_MEDIA_MODEL_IDS = {
    "wan2.2-animate-mix",
    "wan2.2-s2v",
    "wan2.7-videoedit",
}

_MANAGED_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_MANAGED_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+\-\[\]]{0,255}$")
_CONSERVATIVE_2026_MAX_OUTPUT_TOKENS = 4096
_CONSERVATIVE_2026_MAX_OUTPUT_PROVENANCE = {
    "source": "v8_conservative_2026_default",
    "confidence": "estimated",
    "notes": (
        "The vendor maximum was not published in the audited catalog sources; "
        "4096 is a conservative V8 runtime default, not an official model limit."
    ),
}
_MANAGED_SENSITIVE_KEYS = {
    "secret",
    "secrets",
    "secretkey",
    "clientsecret",
    "apikey",
    "apikeys",
    "token",
    "tokens",
    "accesstoken",
    "refreshtoken",
    "password",
    "passphrase",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "credentialref",
    "credentialrefs",
    "authorization",
}
_MANAGED_SENSITIVE_SUFFIXES = (
    "apikey",
    "clientsecret",
    "accesstoken",
    "refreshtoken",
    "password",
    "passphrase",
    "credentialref",
)
_MANAGED_SENSITIVE_QUERY_KEYS = _MANAGED_SENSITIVE_KEYS | {
    "key",
    "auth",
    "bearer",
    "signature",
    "sig",
}
_MANAGED_STRONG_SENSITIVE_WORDS = {
    "authorization",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "passphrase",
    "secret",
    "secrets",
}
_MANAGED_SAFE_TOKEN_KEY_PATTERNS = (
    re.compile(r"^(?:missing)?(?:max|min)(?:input|output)?tokens$"),
    re.compile(
        r"^(?:accepted|budget|cached|completion|contextwindow|increment|input|output|prompt|reasoning|rejected|thinkingbudget|total)tokens$"
    ),
    re.compile(r"^(?:input|output)pricepermilliontokens$"),
    re.compile(r"^(?:input|output)tokenlimit$"),
    re.compile(r"^tokens?(?:budget|count|limit|window)$"),
)
_OAUTH_FILE_LOCKED_PROVIDER_FIELDS = {
    "baseurl",
    "apistandard",
    "channels",
    "defaultchannel",
    "auth",
    "transport",
    "probestrategy",
    "adapter",
    "wireprotocol",
}
_OAUTH_FILE_MODEL_TRANSPORT_FIELDS = _OAUTH_FILE_LOCKED_PROVIDER_FIELDS | {
    "endpoint",
    "endpointurl",
}


def _normalized_catalog_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _catalog_key_words(value: Any) -> tuple[str, ...]:
    expanded = re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])",
        " ",
        str(value or ""),
    )
    return tuple(part.lower() for part in re.split(r"[^A-Za-z0-9]+", expanded) if part)


def _is_sensitive_managed_field_key(value: Any) -> bool:
    normalized_key = _normalized_catalog_key(value)
    if (
        normalized_key in _MANAGED_SENSITIVE_KEYS
        or normalized_key.endswith(_MANAGED_SENSITIVE_SUFFIXES)
    ):
        return True
    words = _catalog_key_words(value)
    if _MANAGED_STRONG_SENSITIVE_WORDS.intersection(words):
        return True
    if ("api", "key") in zip(words, words[1:]):
        return True
    if any(word in {"token", "tokens"} for word in words):
        return not any(
            pattern.fullmatch(normalized_key)
            for pattern in _MANAGED_SAFE_TOKEN_KEY_PATTERNS
        )
    return False


def _is_sensitive_url_query_key(value: Any) -> bool:
    decoded = str(value or "")
    for _ in range(8):
        next_value = unquote_plus(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    else:
        return True
    normalized_key = _normalized_catalog_key(decoded)
    words = _catalog_key_words(decoded)
    return (
        normalized_key in _MANAGED_SENSITIVE_QUERY_KEYS
        or any(word in {"auth", "bearer", "key", "sig", "signature"} for word in words)
        or _is_sensitive_managed_field_key(decoded)
    )


def _redact_probe_error(value: Any, credential: str = "") -> str:
    text = str(value or "")
    secret = str(credential or "")
    if secret:
        encoded_candidates = {
            secret,
            quote(secret, safe=""),
            quote_plus(secret, safe=""),
        }
        for candidate in sorted(encoded_candidates, key=len, reverse=True):
            if candidate:
                text = text.replace(candidate, "[redacted]")
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?([^\s,;&]+)",
        r"\1[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|bearer|password|secret)\s*[:=]\s*)([^\s,;&]+)",
        r"\1[redacted]",
        text,
    )
    return text[:500]


def _validated_http_url(value: Any, field_path: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError(f"managed catalog {field_path} must contain an HTTP(S) URL") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ValueError(f"managed catalog {field_path} must contain an HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"managed catalog {field_path} must not contain URL userinfo")
    if parsed.fragment:
        raise ValueError(f"managed catalog {field_path} must not contain a URL fragment")
    query_keys = [key for key, _value in parse_qsl(parsed.query, keep_blank_values=True)]
    query_keys.extend(
        component.split("=", 1)[0]
        for component in parsed.query.split("&")
        if component
    )
    if any(_is_sensitive_url_query_key(key) for key in query_keys):
        raise ValueError(f"managed catalog {field_path} must not contain sensitive URL query parameters")
    return urlunparse(parsed)


def _validate_managed_url(value: Any, field_path: str) -> None:
    values = value if isinstance(value, list) else [value]
    if not values:
        raise ValueError(f"managed catalog {field_path} must contain an HTTP(S) URL")
    for item in values:
        _validated_http_url(item, field_path)


def resolve_probe_target(provider: Dict[str, Any], base_url: str = "") -> Dict[str, Any]:
    """Return a validated, secret-free HTTP probe target for broker preflight."""

    if not isinstance(provider, dict):
        raise ValueError("provider probe target must be an object")
    raw_base_url = str(base_url or provider.get("baseUrl") or provider.get("base_url") or "").strip()
    safe_base_url = _validated_http_url(raw_base_url, "probe base URL")
    parsed_base = urlparse(safe_base_url)
    explicit_url = str(provider.get("modelsUrl") or provider.get("models_url") or "").strip()
    if explicit_url:
        safe_url = _validated_http_url(explicit_url, "probe models URL")
    else:
        path = str(provider.get("modelsPath") or provider.get("models_path") or "/models").strip() or "/models"
        parsed_path = urlparse(path)
        if parsed_path.scheme or parsed_path.netloc or parsed_path.query or parsed_path.fragment:
            raise ValueError("provider modelsPath must be a relative path without query or fragment")
        clean_parts = [part for part in path.replace("\\", "/").strip("/").split("/") if part not in {"", "."}]
        if any(part == ".." for part in clean_parts):
            raise ValueError("provider modelsPath cannot traverse parent directories")
        target_path = "/".join(
            part
            for part in [parsed_base.path.rstrip("/"), "/".join(clean_parts) or "models"]
            if part
        )
        if not target_path.startswith("/"):
            target_path = f"/{target_path}"
        safe_url = _validated_http_url(
            urlunparse(parsed_base._replace(path=target_path, fragment="")),
            "probe models URL",
        )
    parsed = urlparse(safe_url)
    return {
        "url": safe_url,
        "baseUrl": safe_base_url.rstrip("/") if not parsed_base.query else safe_base_url,
        "scheme": parsed.scheme.lower(),
        "host": parsed.hostname or "",
        "port": parsed.port,
        "path": parsed.path or "/",
        "providerId": str(provider.get("id") or ""),
        "channelId": str(provider.get("channelId") or ""),
        "apiStandard": str(provider.get("apiStandard") or provider.get("api_standard") or ""),
        "probeStrategy": str(provider.get("probeStrategy") or provider.get("probe_strategy") or ""),
    }


def _validate_managed_identifier(value: Any, field_path: str, *, model: bool = False) -> str:
    raw = str(value or "").strip()
    pattern = _MANAGED_MODEL_ID_PATTERN if model else _MANAGED_PROVIDER_ID_PATTERN
    if not raw or not pattern.fullmatch(raw):
        kind = "model" if model else "provider/channel"
        raise ValueError(f"managed catalog {field_path} has an invalid {kind} id")
    return raw


def _validate_managed_tree(value: Any, field_path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = _normalized_catalog_key(key)
            if _is_sensitive_managed_field_key(key):
                raise ValueError(f"managed catalog cannot contain sensitive field: {field_path}.{key}")
            if normalized_key.endswith("url") or normalized_key.endswith("urls"):
                _validate_managed_url(child, f"{field_path}.{key}")
            if key in {"models", "channels"}:
                if not isinstance(child, list):
                    raise ValueError(f"managed catalog {field_path}.{key} must be a list")
                seen_ids: Set[str] = set()
                for index, entry in enumerate(child):
                    if not isinstance(entry, dict):
                        raise ValueError(f"managed catalog {field_path}.{key}[{index}] must be an object")
                    entry_id = _validate_managed_identifier(
                        entry.get("id"),
                        f"{field_path}.{key}[{index}].id",
                        model=key == "models",
                    )
                    if entry_id in seen_ids:
                        raise ValueError(f"managed catalog {field_path}.{key} contains duplicate id")
                    seen_ids.add(entry_id)
            _validate_managed_tree(child, f"{field_path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_managed_tree(child, f"{field_path}[{index}]")


def _validate_managed_catalog(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("managed catalog must be an object")
    providers = payload.get("providers", [])
    if not isinstance(providers, list):
        raise ValueError("managed catalog providers must be a list")
    seen_provider_ids: Set[str] = set()
    for index, provider in enumerate(providers):
        if not isinstance(provider, dict):
            raise ValueError(f"managed catalog providers[{index}] must be an object")
        provider_id = _validate_managed_identifier(provider.get("id"), f"providers[{index}].id")
        if provider_id in seen_provider_ids:
            raise ValueError("managed catalog contains duplicate provider id")
        seen_provider_ids.add(provider_id)
    _validate_managed_tree(payload)
    return deepcopy(payload)


def _merge_managed_keyed_list(base_items: Any, patch_items: Any) -> List[Dict[str, Any]]:
    base = [deepcopy(item) for item in _as_list(base_items) if isinstance(item, dict)]
    positions = {str(item.get("id") or ""): index for index, item in enumerate(base)}
    for item_patch in _as_list(patch_items):
        item_id = str(item_patch.get("id") or "")
        if item_id in positions:
            base[positions[item_id]] = _merge_managed_patch(base[positions[item_id]], item_patch)
        else:
            positions[item_id] = len(base)
            base.append(deepcopy(item_patch))
    return base


def _merge_managed_patch(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if key in {"models", "channels"}:
            merged[key] = _merge_managed_keyed_list(merged.get(key), value)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_managed_patch(dict(merged[key]), value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _creative_media_public_operations(values: Any) -> List[str]:
    return [
        str(item).strip()
        for item in _as_list(values)
        if str(item).strip() and str(item).strip() not in _PLUGIN_ONLY_MEDIA_OPERATION_KINDS
    ]


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _normalized_modality(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"audio", "speech", "tts"}:
        return "voice"
    if raw in {"3d", "model_3d", "model-3d"}:
        return "model3d"
    return raw


def _url_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = parsed.path if parsed.scheme or parsed.netloc else raw
    if not path.startswith("/"):
        path = f"/{path}"
    return path.rstrip("/")


def _url_host(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    return parsed.netloc.lower()


def _provider_catalog_connectable(provider: Dict[str, Any]) -> bool:
    if str(provider.get("apiStandard") or provider.get("api_standard") or "").strip().lower() == "catalog_only":
        return False
    if str(provider.get("adapter") or "").strip().lower() == "catalog_only":
        return False
    urls = [provider.get("baseUrl") or provider.get("base_url")]
    urls.extend(
        channel.get("baseUrl") or channel.get("base_url")
        for channel in _as_list(provider.get("channels"))
        if isinstance(channel, dict)
    )
    for value in urls:
        try:
            resolve_probe_target(provider, str(value or ""))
        except ValueError:
            continue
        else:
            return True
    return False


def _media_base_url_matches(root_provider: Dict[str, Any], media_provider: Dict[str, Any]) -> bool:
    root_base = root_provider.get("baseUrl") or root_provider.get("base_url") or ""
    media_base = media_provider.get("baseUrl") or media_provider.get("base_url") or ""
    root_host = _url_host(root_base)
    media_host = _url_host(media_base)
    if not root_host or not media_host or root_host != media_host:
        return False
    root_path = _url_path(root_base)
    media_path = _url_path(media_base)
    if not root_path or not media_path:
        return True
    return (
        root_path == media_path
        or media_path.startswith(f"{root_path}/")
        or root_path.startswith(f"{media_path}/")
    )


_PROVIDER_TOKEN_STOP_WORDS = {
    "ai",
    "api",
    "audio",
    "cloud",
    "compatible",
    "generation",
    "image",
    "images",
    "model",
    "models",
    "open",
    "provider",
    "speech",
    "text",
    "tts",
    "video",
    "voice",
}


def _provider_identity_tokens(provider: Dict[str, Any]) -> Set[str]:
    values = [
        provider.get("id"),
        provider.get("name"),
        provider.get("displayName"),
        provider.get("credentialRealm"),
    ]
    tokens: Set[str] = set()
    for value in values:
        raw = str(value or "").lower()
        for token in re.split(r"[^a-z0-9]+", raw):
            if len(token) < 3 or token in _PROVIDER_TOKEN_STOP_WORDS:
                continue
            tokens.add(token)
    return tokens


def _media_provider_matches_root(root_provider: Dict[str, Any], media_provider: Dict[str, Any]) -> bool:
    if _media_base_url_matches(root_provider, media_provider):
        return True
    root_realm = str(root_provider.get("credentialRealm") or "").strip().lower()
    media_realm = str(media_provider.get("credentialRealm") or "").strip().lower()
    if root_realm and media_realm and root_realm == media_realm:
        return True
    return bool(_provider_identity_tokens(root_provider) & _provider_identity_tokens(media_provider))


def _media_relative_submit_path(root_base_url: Any, source_base_url: Any, submit_path: Any) -> str:
    submit = _url_path(submit_path)
    if not submit:
        return ""
    source_base = _url_path(source_base_url)
    root_base = _url_path(root_base_url)
    if source_base and not submit.startswith(f"{source_base}/") and submit != source_base:
        full_path = f"{source_base}/{submit.lstrip('/')}"
    else:
        full_path = submit
    if root_base and (full_path == root_base or full_path.startswith(f"{root_base}/")):
        return full_path[len(root_base):].strip("/")
    return submit.strip("/")


def _media_endpoint_model_id(root_provider: Dict[str, Any], media_provider: Dict[str, Any], provider_model_id: str) -> str:
    request = dict(media_provider.get("request") or {})
    clean_model_id = str(provider_model_id or "").strip()
    relative_path = _media_relative_submit_path(
        root_provider.get("baseUrl") or root_provider.get("base_url") or "",
        media_provider.get("baseUrl") or media_provider.get("base_url") or "",
        request.get("submitPath") or "",
    )
    if clean_model_id and "{model}" in relative_path:
        return relative_path.replace("{model}", clean_model_id).strip("/")
    if relative_path and clean_model_id:
        return f"{relative_path}/{clean_model_id}"
    return clean_model_id


def _media_model_id_match_keys(value: Any) -> Set[str]:
    raw = str(value or "").strip().strip("/")
    if not raw:
        return set()
    keys: Set[str] = {raw}
    tail = raw.rsplit("/", 1)[-1].strip()
    if tail:
        keys.add(tail)
    for target in (raw, tail):
        for separator in (":", "?", "#"):
            if separator in target:
                clean = target.split(separator, 1)[0].strip().strip("/")
                if clean:
                    keys.add(clean)
                    if "/" in clean:
                        keys.add(clean.rsplit("/", 1)[-1].strip())
    return {item for item in keys if item}


def _media_model_match_quality(
    display_model_id: str,
    root_provider: Dict[str, Any],
    media_provider: Dict[str, Any],
    provider_model_id: str,
) -> int | None:
    display = str(display_model_id or "").strip().strip("/")
    if not display:
        return None
    root_endpoint_id = _media_endpoint_model_id(root_provider, media_provider, provider_model_id)
    official_endpoint_id = _media_endpoint_model_id(media_provider, media_provider, provider_model_id)
    actual_model_id = str(provider_model_id or "").strip()
    if display == root_endpoint_id:
        return 0
    if display == official_endpoint_id:
        return 1
    if display == actual_model_id:
        return 2
    display_keys = _media_model_id_match_keys(display)
    if actual_model_id in display_keys:
        return 3
    candidate_keys = (
        _media_model_id_match_keys(actual_model_id)
        | _media_model_id_match_keys(root_endpoint_id)
        | _media_model_id_match_keys(official_endpoint_id)
    )
    if display_keys & candidate_keys:
        return 4
    return None


def _load_media_capability_overrides() -> List[Dict[str, Any]]:
    if not _CREATIVE_MEDIA_CAPABILITY_OVERRIDES_PATH.exists():
        return []
    try:
        with _CREATIVE_MEDIA_CAPABILITY_OVERRIDES_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return []
    entries = payload.get("capabilityProfiles") if isinstance(payload, dict) else []
    return [dict(item) for item in _as_list(entries) if isinstance(item, dict)]


def _media_capability_profile(provider_id: str, model_id: str, operation_kind: str | None = None) -> Dict[str, Any]:
    provider = str(provider_id or "").strip()
    model = str(model_id or "").strip()
    operation = str(operation_kind or "").strip()
    if not provider or not model:
        return {}
    registry_entry = media_model_capability_registry.find(provider, model, operation or None)
    if registry_entry:
        operation_profiles = registry_entry.get("operationCapabilityProfiles") or {}
        profile = deepcopy(dict(operation_profiles.get(operation) or {})) if operation else {}
        if not profile:
            has_advanced_profile = any(
                [
                    registry_entry.get("nativeAudio"),
                    registry_entry.get("audioModes"),
                    registry_entry.get("audioPreservationPolicy"),
                    registry_entry.get("referenceInputs"),
                    registry_entry.get("resolution"),
                    registry_entry.get("duration"),
                    registry_entry.get("formats"),
                ]
            )
            if not has_advanced_profile:
                return {}
            profile = {
                "nativeAudio": bool(registry_entry.get("nativeAudio")),
                "audioModes": registry_entry.get("audioModes") or [],
                "audioPreservationPolicy": registry_entry.get("audioPreservationPolicy") or "",
                "inputModalities": registry_entry.get("inputModalities") or [],
                "outputStreams": registry_entry.get("outputStreams") or [],
                "referenceInputs": registry_entry.get("referenceInputs") or {},
                "resolution": registry_entry.get("resolution") or {},
                "duration": registry_entry.get("duration") or {},
                "formats": registry_entry.get("formats") or {},
            }
        profile.setdefault("confidence", registry_entry.get("confidence"))
        source_refs = registry_entry.get("sourceRefs") or []
        if source_refs:
            profile.setdefault("sourceRefs", source_refs)
            profile.setdefault("sourceUrl", source_refs[0].get("url") if isinstance(source_refs[0], dict) else "")
        return profile
    for item in _load_media_capability_overrides():
        if str(item.get("providerId") or "").strip() != provider:
            continue
        model_ids = item.get("modelIds")
        if isinstance(model_ids, list):
            normalized_model_ids = {str(value).strip() for value in model_ids}
        else:
            normalized_model_ids = {str(item.get("modelId") or "").strip()}
        if model not in normalized_model_ids:
            continue
        operation_kinds = item.get("operationKinds")
        if operation and isinstance(operation_kinds, list) and operation not in {str(value).strip() for value in operation_kinds}:
            continue
        profile = deepcopy(dict(item.get("capabilityProfile") or {}))
        if item.get("sourceUrl"):
            profile.setdefault("sourceUrl", item.get("sourceUrl"))
        if item.get("confidence"):
            profile.setdefault("confidence", item.get("confidence"))
        return profile
    return {}


class ModelProviderCatalog:
    def __init__(
        self,
        path: Path = _CATALOG_PATH,
        custom_path: Path = _CUSTOM_CATALOG_PATH,
        managed_path: Path | None = None,
    ) -> None:
        self.path = path
        self.custom_path = custom_path
        self.managed_path = (
            managed_path
            if managed_path is not None
            else (
                custom_path.with_name("model_provider_catalog.managed.json")
                if custom_path != _CUSTOM_CATALOG_PATH
                else _MANAGED_CATALOG_PATH
            )
        )
        self.managed_backup_path = Path(f"{self.managed_path}.bak")
        self.managed_rejected_path = Path(f"{self.managed_path}.rejected")
        self._asset_cache_lock = threading.RLock()
        self._cache: Dict[str, Any] | None = None
        self._creative_media_providers_cache: List[Dict[str, Any]] | None = None
        self._root_media_mappings_cache: Dict[str, Set[str]] | None = None
        self._custom_cache: Dict[str, Any] | None = None
        self._custom_lock = threading.RLock()
        self._managed_lock = threading.RLock()
        self._managed_status: Dict[str, Any] = {
            "ok": True,
            "state": "not_loaded",
            "path": str(self.managed_path),
            "backupPath": str(self.managed_backup_path),
            "backupAvailable": self.managed_backup_path.exists(),
            "rejectedAvailable": self.managed_rejected_path.exists(),
            "recoveryTombstoneAvailable": bool(self._managed_recovery_tombstones()),
        }

    def _load_builtin(self) -> Dict[str, Any]:
        with self._asset_cache_lock:
            if self._cache is None:
                with self.path.open("r", encoding="utf-8") as handle:
                    self._cache = json.load(handle)
        return deepcopy(self._cache)

    def _load_custom(self, *, strict: bool = False) -> Dict[str, Any]:
        with self._custom_lock:
            if not self.custom_path.exists():
                self._custom_cache = None
                return {"version": 1, "providers": []}
            try:
                with self.custom_path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if not isinstance(payload, dict):
                    raise ValueError("custom catalog must be an object")
            except Exception as exc:
                if strict:
                    raise ValueError("custom provider catalog is invalid") from exc
                return deepcopy(self._custom_cache or {"version": 1, "providers": []})
            self._custom_cache = deepcopy(payload)
            return deepcopy(payload)

    def _save_custom(self, payload: Dict[str, Any]) -> None:
        with self._custom_lock:
            self._atomic_write_managed_path(self.custom_path, payload)
            self._custom_cache = deepcopy(payload)

    def load_custom(self) -> Dict[str, Any]:
        """Return the custom overlay without merging built-in or managed entries."""

        return self._load_custom()

    def custom_digest(self) -> str:
        return _catalog_payload_digest(self._load_custom(strict=True))

    def _validate_managed_payload(self, payload: Any) -> Dict[str, Any]:
        validated = _validate_managed_catalog(payload)
        builtin_by_id = {
            str(provider.get("id") or ""): provider
            for provider in _as_list(self._load_builtin().get("providers"))
            if isinstance(provider, dict) and str(provider.get("id") or "").strip()
        }
        required_new_provider_fields = ("name", "baseUrl", "apiStandard", "auth", "probeStrategy")
        for provider_patch in _as_list(validated.get("providers")):
            provider_id = str(provider_patch.get("id") or "")
            builtin_provider = builtin_by_id.get(provider_id)
            effective_provider = (
                _merge_managed_patch(builtin_provider, provider_patch)
                if builtin_provider is not None
                else deepcopy(provider_patch)
            )
            if builtin_provider is None:
                missing = [
                    field
                    for field in required_new_provider_fields
                    if not effective_provider.get(field)
                ]
                if missing:
                    raise ValueError("managed catalog new provider is missing required fields")
            auth = effective_provider.get("auth")
            if not isinstance(auth, dict):
                raise ValueError("managed catalog provider auth must be an object")
            auth_type = str(auth.get("type") or "").strip()
            builtin_auth = dict((builtin_provider or {}).get("auth") or {})
            builtin_auth_type = str(builtin_auth.get("type") or "").strip()
            if builtin_auth_type == "oauth_file":
                locked_fields = [
                    key
                    for key in provider_patch
                    if _normalized_catalog_key(key) in _OAUTH_FILE_LOCKED_PROVIDER_FIELDS
                ]
                if locked_fields:
                    raise ValueError("managed catalog cannot change builtin oauth_file transport")
                unexpected_fields = set(provider_patch) - {"id", "models"}
                if unexpected_fields:
                    raise ValueError("managed catalog builtin oauth_file overlays only allow model metadata")
                for model_patch in _as_list(provider_patch.get("models")):
                    if not isinstance(model_patch, dict):
                        continue
                    locked_model_fields = [
                        key
                        for key in model_patch
                        if _normalized_catalog_key(key) in _OAUTH_FILE_MODEL_TRANSPORT_FIELDS
                    ]
                    if locked_model_fields:
                        raise ValueError("managed catalog cannot change builtin oauth_file model transport")
                if auth_type != "oauth_file":
                    raise ValueError("managed catalog cannot replace builtin oauth_file auth")
                if str(auth.get("path") or "") != str(builtin_auth.get("path") or ""):
                    raise ValueError("managed catalog cannot change builtin oauth_file auth.path")
            elif auth_type not in {"api_key", "none"}:
                raise ValueError("managed catalog auth.type must be api_key or none")
            for channel in _as_list(effective_provider.get("channels")):
                if not isinstance(channel, dict):
                    continue
                for auth_field in ("authContract", "auth"):
                    if auth_field not in channel:
                        continue
                    channel_auth = channel.get(auth_field)
                    if not isinstance(channel_auth, dict):
                        raise ValueError("managed catalog channel auth must be an object")
                    channel_auth_type = str(channel_auth.get("type") or "").strip().lower()
                    if channel_auth_type == "oauth_file":
                        if builtin_auth_type != "oauth_file" or channel_auth != builtin_auth:
                            raise ValueError(
                                "managed catalog channel cannot introduce or change oauth_file auth"
                            )
                    elif channel_auth_type not in {"api_key", "none"}:
                        raise ValueError(
                            "managed catalog channel auth.type must be api_key or none"
                        )
            for field in ("name", "apiStandard", "probeStrategy"):
                if not isinstance(effective_provider.get(field), str) or not str(effective_provider.get(field)).strip():
                    raise ValueError(f"managed catalog provider {field} must be a non-empty string")
        return validated

    def _read_managed_path(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise ValueError("managed catalog file does not exist")
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("managed catalog is unreadable or invalid JSON") from exc
        return self._validate_managed_payload(payload)

    def _set_managed_status(
        self,
        *,
        ok: bool,
        state: str,
        provider_count: int = 0,
        error: str = "",
    ) -> None:
        self._managed_status = {
            "ok": ok,
            "state": state,
            "backupAvailable": self.managed_backup_path.exists(),
            "rejectedAvailable": self.managed_rejected_path.exists(),
            "recoveryTombstoneAvailable": bool(self._managed_recovery_tombstones()),
            "providerCount": provider_count,
            **({"errorCode": "managed_catalog_invalid", "error": error} if error else {}),
        }

    def get_managed_status(self) -> Dict[str, Any]:
        with self._managed_lock:
            return deepcopy(self._managed_status)

    def load_managed(self) -> Dict[str, Any]:
        with self._managed_lock:
            if not self.managed_path.exists():
                self._set_managed_status(ok=True, state="absent")
                return {"version": 1, "providers": []}
            try:
                payload = self._read_managed_path(self.managed_path)
            except ValueError as exc:
                self._set_managed_status(ok=False, state="invalid", error=str(exc))
                raise
            self._set_managed_status(
                ok=True,
                state="ready",
                provider_count=len(_as_list(payload.get("providers"))),
            )
            return payload

    @staticmethod
    def _atomic_write_managed_path(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def _save_managed(self, payload: Dict[str, Any], *, preserve_current_backup: bool = True) -> None:
        validated = self._validate_managed_payload(payload)
        validated["version"] = int(validated.get("version") or 1)
        if preserve_current_backup and self.managed_path.exists():
            current = self._read_managed_path(self.managed_path)
            self._atomic_write_managed_path(self.managed_backup_path, current)
        self._atomic_write_managed_path(self.managed_path, validated)
        self._set_managed_status(
            ok=True,
            state="ready",
            provider_count=len(_as_list(validated.get("providers"))),
        )

    def _assert_managed_digest_locked(self, expected_digest: str | None) -> None:
        if expected_digest is None:
            return
        if self._managed_file_digest(self.managed_path) != str(expected_digest):
            raise ValueError("managed catalog digest conflict")

    @staticmethod
    def _assert_file_digest(path: Path, expected_digest: str, label: str) -> None:
        if not expected_digest or ModelProviderCatalog._managed_file_digest(path) != str(expected_digest):
            raise ValueError(f"managed catalog {label} digest conflict")

    def restore_managed_backup(
        self,
        *,
        expected_managed_digest: str | None = None,
    ) -> Dict[str, Any]:
        with self._managed_lock:
            self._assert_managed_digest_locked(expected_managed_digest)
            payload = self._read_managed_path(self.managed_backup_path)
            self._save_managed(payload, preserve_current_backup=False)
        return deepcopy(payload)

    @staticmethod
    def _managed_file_digest(path: Path) -> str:
        if not path.exists() or not path.is_file():
            return ""
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(64 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return ""
        return digest.hexdigest()

    def _managed_recovery_tombstone_path(
        self,
        current_digest: str,
        rejected_digest: str,
    ) -> Path:
        return Path(
            f"{self.managed_path}.recovery.{current_digest}.{rejected_digest}.tombstone"
        )

    def _managed_recovery_tombstones(self) -> List[Dict[str, Any]]:
        prefix = f"{self.managed_path.name}.recovery."
        suffix = ".tombstone"
        records: List[Dict[str, Any]] = []
        for path in sorted(self.managed_path.parent.glob(f"{prefix}*{suffix}")):
            identity = path.name[len(prefix) : -len(suffix)]
            parts = identity.split(".", 1)
            if len(parts) != 2:
                continue
            current_digest, rejected_digest = parts
            records.append(
                {
                    "path": path,
                    "currentDigest": current_digest,
                    "rejectedDigest": rejected_digest,
                    "digest": self._managed_file_digest(path),
                }
            )
        return records

    def managed_recovery_state(self) -> Dict[str, Any]:
        def _valid(path: Path) -> bool:
            try:
                self._read_managed_path(path)
            except ValueError:
                return False
            return True

        with self._managed_lock:
            tombstones = self._managed_recovery_tombstones()
            return {
                "managedExists": self.managed_path.exists(),
                "managedDigest": self._managed_file_digest(self.managed_path),
                "managedValid": _valid(self.managed_path) if self.managed_path.exists() else False,
                "backupExists": self.managed_backup_path.exists(),
                "backupDigest": self._managed_file_digest(self.managed_backup_path),
                "backupValid": _valid(self.managed_backup_path) if self.managed_backup_path.exists() else False,
                "rejectedExists": self.managed_rejected_path.exists(),
                "rejectedDigest": self._managed_file_digest(self.managed_rejected_path),
                "tombstoneExists": bool(tombstones),
                "tombstoneCount": len(tombstones),
                "tombstones": [
                    {
                        "currentDigest": item["currentDigest"],
                        "rejectedDigest": item["rejectedDigest"],
                        "digest": item["digest"],
                    }
                    for item in tombstones
                ],
            }

    def recover_managed_from_backup(
        self,
        *,
        expected_managed_digest: str,
        expected_backup_digest: str,
    ) -> Dict[str, Any]:
        """Replace the current overlay with its last valid backup, retaining exact bytes."""

        with self._managed_lock:
            self._assert_file_digest(
                self.managed_backup_path,
                expected_backup_digest,
                "backup",
            )
            backup = self._read_managed_path(self.managed_backup_path)
            current_digest = self._managed_file_digest(self.managed_path)
            rejected_digest = self._managed_file_digest(self.managed_rejected_path)
            if self.managed_rejected_path.exists():
                if self.managed_path.exists() or rejected_digest != str(expected_managed_digest):
                    raise ValueError("managed catalog has an unresolved rejected recovery file")
            else:
                if not self.managed_path.exists() or current_digest != str(expected_managed_digest):
                    raise ValueError("managed catalog managed digest conflict")
                os.replace(self.managed_path, self.managed_rejected_path)
            try:
                self._atomic_write_managed_path(self.managed_path, backup)
            except Exception:
                if self.managed_path.exists():
                    try:
                        self.managed_path.unlink()
                    except OSError:
                        pass
                if self.managed_rejected_path.exists() and not self.managed_path.exists():
                    os.replace(self.managed_rejected_path, self.managed_path)
                raise
            self._set_managed_status(
                ok=True,
                state="recovered",
                provider_count=len(_as_list(backup.get("providers"))),
            )
            return self.managed_recovery_state()

    def finalize_managed_recovery(
        self,
        *,
        expected_current_digest: str,
        expected_rejected_digest: str,
    ) -> Dict[str, Any]:
        """Accept a recovered overlay after both recovery artifacts pass CAS checks."""

        with self._managed_lock:
            self._assert_file_digest(self.managed_path, expected_current_digest, "current")
            self._assert_file_digest(self.managed_rejected_path, expected_rejected_digest, "rejected")
            current = self._read_managed_path(self.managed_path)
            tombstone_path = self._managed_recovery_tombstone_path(
                str(expected_current_digest),
                str(expected_rejected_digest),
            )
            if tombstone_path.exists():
                raise ValueError("managed catalog recovery tombstone already exists")
            try:
                os.replace(self.managed_rejected_path, tombstone_path)
            except OSError as exc:
                raise ValueError("managed catalog rejected recovery finalization failed") from exc
            self._set_managed_status(
                ok=True,
                state="ready",
                provider_count=len(_as_list(current.get("providers"))),
            )
            return self.managed_recovery_state()

    def rollback_managed_recovery(
        self,
        *,
        expected_current_digest: str,
        expected_rejected_digest: str = "",
    ) -> Dict[str, Any]:
        """Undo an active recovery or restore the pre-finalize recovery state."""

        with self._managed_lock:
            current_digest = self._managed_file_digest(self.managed_path)
            if not expected_current_digest or current_digest != str(expected_current_digest):
                raise ValueError("managed catalog changed after recovery")
            if not self.managed_rejected_path.exists():
                tombstones = [
                    item
                    for item in self._managed_recovery_tombstones()
                    if item["currentDigest"] == str(expected_current_digest)
                    and (
                        not expected_rejected_digest
                        or item["rejectedDigest"] == str(expected_rejected_digest)
                    )
                ]
                if len(tombstones) != 1:
                    raise ValueError("managed catalog recovery tombstone is missing or ambiguous")
                tombstone = tombstones[0]
                if tombstone["digest"] != tombstone["rejectedDigest"]:
                    raise ValueError("managed catalog recovery tombstone digest conflict")
                os.replace(tombstone["path"], self.managed_rejected_path)
                current = self._read_managed_path(self.managed_path)
                self._set_managed_status(
                    ok=True,
                    state="recovered",
                    provider_count=len(_as_list(current.get("providers"))),
                )
                return self.managed_recovery_state()
            rollback_temp = Path(f"{self.managed_path}.rollback-current")
            if rollback_temp.exists():
                raise ValueError("managed catalog rollback staging file already exists")
            os.replace(self.managed_path, rollback_temp)
            try:
                os.replace(self.managed_rejected_path, self.managed_path)
            except Exception:
                if rollback_temp.exists() and not self.managed_path.exists():
                    os.replace(rollback_temp, self.managed_path)
                raise
            finally:
                if rollback_temp.exists():
                    try:
                        rollback_temp.unlink()
                    except OSError:
                        pass
            try:
                restored = self._read_managed_path(self.managed_path)
            except ValueError as exc:
                self._set_managed_status(ok=False, state="invalid", error=str(exc))
            else:
                self._set_managed_status(
                    ok=True,
                    state="ready",
                    provider_count=len(_as_list(restored.get("providers"))),
                )
            return self.managed_recovery_state()

    def get_managed_provider(self, provider_id: str) -> Dict[str, Any] | None:
        target = _validate_managed_identifier(provider_id, "providerId")
        for provider in _as_list(self.load_managed().get("providers")):
            if str(provider.get("id") or "") == target:
                return deepcopy(provider)
        return None

    @staticmethod
    def _managed_provider_snapshot_digest(provider: Dict[str, Any] | None) -> str:
        snapshot = {
            "exists": provider is not None,
            "value": deepcopy(provider) if provider is not None else None,
        }
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def managed_provider_digest(self, provider_id: str) -> str:
        """Return the target-scoped digest used by managed provider rollback CAS."""

        target = _validate_managed_identifier(provider_id, "providerId")
        with self._managed_lock:
            provider = next(
                (
                    item
                    for item in _as_list(self.load_managed().get("providers"))
                    if str(item.get("id") or "") == target
                ),
                None,
            )
            return self._managed_provider_snapshot_digest(provider)

    def validate_managed_provider(self, provider_patch: Dict[str, Any]) -> Dict[str, Any]:
        """Validate one secret-free managed overlay patch without writing it."""

        if not isinstance(provider_patch, dict):
            raise ValueError("managed provider patch must be an object")
        stored_patch = deepcopy(provider_patch)
        stored_patch.pop("isCustom", None)
        stored_patch.pop("isManaged", None)
        _validate_managed_catalog({"version": 1, "providers": [stored_patch]})
        provider_id = str(stored_patch.get("id") or "")
        with self._managed_lock:
            payload = self.load_managed()
            providers = _as_list(payload.get("providers"))
            candidate_providers: List[Dict[str, Any]] = []
            replaced = False
            for provider in providers:
                if str(provider.get("id") or "") == provider_id:
                    candidate_providers.append(_merge_managed_patch(provider, stored_patch))
                    replaced = True
                else:
                    candidate_providers.append(deepcopy(provider))
            if not replaced:
                candidate_providers.append(deepcopy(stored_patch))
            self._validate_managed_payload(
                {
                    **payload,
                    "providers": candidate_providers,
                }
            )
        return stored_patch

    def _store_managed_provider(
        self,
        provider_patch: Dict[str, Any],
        *,
        merge_existing: bool,
        expected_managed_digest: str | None = None,
    ) -> Dict[str, Any]:
        stored_patch = self.validate_managed_provider(provider_patch)
        provider_id = str(stored_patch.get("id") or "")
        saved_patch = stored_patch
        with self._managed_lock:
            self._assert_managed_digest_locked(expected_managed_digest)
            payload = self.load_managed()
            providers = _as_list(payload.get("providers"))
            next_providers: List[Dict[str, Any]] = []
            replaced = False
            for provider in providers:
                if str(provider.get("id") or "") == provider_id:
                    saved_patch = (
                        _merge_managed_patch(provider, stored_patch)
                        if merge_existing
                        else stored_patch
                    )
                    next_providers.append(saved_patch)
                    replaced = True
                else:
                    next_providers.append(deepcopy(provider))
            if not replaced:
                next_providers.append(stored_patch)
            payload["providers"] = next_providers
            self._save_managed(payload)
        return deepcopy(saved_patch)

    def upsert_managed_provider(
        self,
        provider_patch: Dict[str, Any],
        *,
        expected_managed_digest: str | None = None,
    ) -> Dict[str, Any]:
        return self._store_managed_provider(
            provider_patch,
            merge_existing=True,
            expected_managed_digest=expected_managed_digest,
        )

    def restore_managed_provider(
        self,
        provider_id: str,
        provider_snapshot: Dict[str, Any] | None,
        *,
        expected_managed_digest: str | None = None,
        expected_provider_digest: str | None = None,
    ) -> Dict[str, Any] | None:
        target = _validate_managed_identifier(provider_id, "providerId")
        stored_snapshot: Dict[str, Any] | None = None
        if provider_snapshot is not None:
            if not isinstance(provider_snapshot, dict):
                raise ValueError("managed provider snapshot must be an object")
            snapshot_id = _validate_managed_identifier(
                provider_snapshot.get("id"),
                "providerSnapshot.id",
            )
            if snapshot_id != target:
                raise ValueError("managed provider snapshot id does not match providerId")
            stored_snapshot = self.validate_managed_provider(provider_snapshot)

        with self._managed_lock:
            payload = self.load_managed()
            providers = _as_list(payload.get("providers"))
            current_provider = next(
                (
                    item
                    for item in providers
                    if str(item.get("id") or "") == target
                ),
                None,
            )
            if expected_provider_digest is not None:
                current_provider_digest = self._managed_provider_snapshot_digest(current_provider)
                if current_provider_digest != str(expected_provider_digest):
                    raise ValueError("managed provider digest conflict")
            else:
                self._assert_managed_digest_locked(expected_managed_digest)

            next_providers: List[Dict[str, Any]] = []
            restored = False
            for provider in providers:
                if str(provider.get("id") or "") != target:
                    next_providers.append(deepcopy(provider))
                    continue
                restored = True
                if stored_snapshot is not None:
                    next_providers.append(deepcopy(stored_snapshot))
            if stored_snapshot is not None and not restored:
                next_providers.append(deepcopy(stored_snapshot))
            if stored_snapshot is None and not restored:
                return None
            payload["providers"] = next_providers
            self._save_managed(payload, preserve_current_backup=False)
        return deepcopy(stored_snapshot)

    def delete_managed_provider(
        self,
        provider_id: str,
        *,
        expected_managed_digest: str | None = None,
    ) -> bool:
        target = _validate_managed_identifier(provider_id, "providerId")
        with self._managed_lock:
            self._assert_managed_digest_locked(expected_managed_digest)
            payload = self.load_managed()
            providers = _as_list(payload.get("providers"))
            next_providers = [
                deepcopy(provider)
                for provider in providers
                if str(provider.get("id") or "") != target
            ]
            if len(next_providers) == len(providers):
                return False
            payload["providers"] = next_providers
            self._save_managed(payload)
        return True

    def _media_capabilities(self, modality: str) -> List[str]:
        normalized = _normalized_modality(modality)
        caps = {normalized}
        if normalized == "voice":
            caps.add("audio")
        if normalized == "workflow":
            caps.update({"workflow", "image"})
        if normalized == "model3d":
            caps.add("model3d")
        return sorted(item for item in caps if item)

    def _media_model_type(self, modality: str) -> str:
        return _MEDIA_MODEL_TYPES.get(_normalized_modality(modality), "MEDIA")

    def _media_operation_kinds(self, provider_entry: Dict[str, Any], modality: str) -> List[str]:
        explicit = provider_entry.get("operationKinds") or provider_entry.get("operations") or []
        if isinstance(explicit, list) and explicit:
            return [str(item) for item in explicit if str(item).strip()]
        normalized = _normalized_modality(modality)
        if normalized == "image":
            return ["image.generate"]
        if normalized == "video":
            adapter = str(provider_entry.get("adapter") or "")
            if adapter == "volcengine_ark":
                return ["video.text_to_video", "video.image_to_video", "video.first_last_frame"]
            return ["video.text_to_video"]
        if normalized == "voice":
            return ["voice.tts"]
        if normalized == "music":
            return ["music.brief"]
        if normalized == "model3d":
            return ["model3d.generate"]
        return []

    def _media_model_operation_kinds(self, provider_entry: Dict[str, Any], modality: str, model_id: str) -> List[str]:
        provider_id = str(provider_entry.get("id") or "").strip()
        registry_entry = media_model_capability_registry.find(provider_id, model_id)
        registry_operations = registry_entry.get("operationKinds") if registry_entry else None
        if isinstance(registry_operations, list) and registry_operations:
            normalized_modality = _normalized_modality(modality)
            prefixes = {
                "image": "image.",
                "video": "video.",
                "voice": "voice.",
                "music": "music.",
                "workflow": "workflow.",
                "model3d": "model3d.",
            }
            prefix = prefixes.get(normalized_modality)
            exact_operations = _creative_media_public_operations([
                str(item).strip()
                for item in registry_operations
                if str(item).strip() and (not prefix or str(item).strip().startswith(prefix))
            ])
            if str(model_id).strip().lower() != "wan2.2-animate-move":
                exact_operations = [item for item in exact_operations if item != "video.action_transfer"]
            if exact_operations:
                return exact_operations
        return _creative_media_public_operations(self._media_operation_kinds(provider_entry, modality))

    @staticmethod
    def _media_capability_modes(
        modality: str,
        operation_kinds: List[str],
        operation_capability_profiles: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        primary_modes = {
            "image.generate": "image.text_to_image",
            "image.edit": "image.image_to_image",
            "video.text_to_video": "video.text_to_video",
            "video.image_to_video": "video.image_to_video",
            "video.first_last_frame": "video.first_last_frame",
            "voice.tts": "voice.tts",
            "voice.design": "voice.design",
            "music.generate": "music.generate",
            "music.cover": "music.cover",
            "model3d.generate": "model3d.text_to_3d",
        }
        modes = [primary_modes[item] for item in operation_kinds if item in primary_modes]
        if _normalized_modality(modality) == "video" and "video.reference_to_video" in operation_kinds:
            profile = dict(operation_capability_profiles.get("video.reference_to_video") or {})
            reference_inputs = dict(profile.get("referenceInputs") or {})
            input_modalities = {str(item).strip() for item in (profile.get("inputModalities") or [])}
            if "image" in reference_inputs or "image" in input_modalities or not profile:
                modes.append("video.image_reference")
            if any(item in reference_inputs or item in input_modalities for item in ("video", "audio")):
                modes.append("video.multimodal_reference")
        return list(dict.fromkeys(modes))

    def _media_catalog_model(self, provider_entry: Dict[str, Any], modality: str, model_id: str) -> Dict[str, Any]:
        provider_id = str(provider_entry.get("id") or "")
        registry_entry = media_model_capability_registry.find(provider_id, model_id)
        request = dict(provider_entry.get("request") or {})
        model_request = dict((request.get("modelOverrides") or {}).get(model_id) or {})
        polling = dict(provider_entry.get("polling") or {})
        result = dict(provider_entry.get("result") or {})
        operation_kinds = self._media_model_operation_kinds(provider_entry, modality, model_id)
        operation_capability_profiles = {
            operation_kind: _media_capability_profile(provider_id, model_id, operation_kind)
            for operation_kind in operation_kinds
        }
        operation_capability_profiles = {key: value for key, value in operation_capability_profiles.items() if value}
        capability_modes = self._media_capability_modes(modality, operation_kinds, operation_capability_profiles)
        capability_profile = next(iter(operation_capability_profiles.values()), {})
        model_logo_assets = provider_entry.get("modelLogoAssets")
        model_logo_asset = ""
        if isinstance(model_logo_assets, dict):
            model_logo_asset = str(model_logo_assets.get(model_id) or "").strip()
        if not model_logo_asset and registry_entry:
            model_logo_asset = str(registry_entry.get("logoAsset") or "").strip()
        return {
            "id": model_id,
            "type": self._media_model_type(modality),
            "contextWindow": None,
            "maxTokens": None,
            "logoAsset": model_logo_asset,
            "capabilities": self._media_capabilities(modality),
            "operationKinds": operation_kinds,
            "parameterProfile": provider_entry.get("apiStandard") or provider_entry.get("adapter") or "media_generation",
            "capabilitySource": "media_model_capability_registry" if registry_entry else "provider_matrix",
            "mediaCapabilityRegistry": {
                "canonicalModelId": (registry_entry or {}).get("canonicalModelId"),
                "confidence": (registry_entry or {}).get("confidence"),
                "missingFields": (registry_entry or {}).get("missingFields") or [],
                "sourceRefs": (registry_entry or {}).get("sourceRefs") or [],
            }
            if registry_entry
            else {},
            "mediaLimits": {
                "modality": _normalized_modality(modality),
                "adapter": provider_entry.get("adapter") or "catalog_only",
                "apiStandard": provider_entry.get("apiStandard") or "",
                "capabilityModes": capability_modes,
                "operationKinds": operation_kinds,
                "operationCapabilityProfiles": operation_capability_profiles,
                "submitPath": model_request.get("submitPath") or request.get("submitPath") or "",
                "endpointPath": model_request.get("submitPath") or request.get("submitPath") or "",
                "pollingMode": polling.get("mode") or "none",
                "resultPaths": _as_list(result.get("paths")),
                "sizeFormat": request.get("sizeFormat") or "",
                "capabilityProfile": capability_profile,
                "mediaCapabilityRegistry": {
                    "canonicalModelId": (registry_entry or {}).get("canonicalModelId"),
                    "confidence": (registry_entry or {}).get("confidence"),
                    "missingFields": (registry_entry or {}).get("missingFields") or [],
                    "sourceRefs": (registry_entry or {}).get("sourceRefs") or [],
                }
                if registry_entry
                else {},
            },
        }

    def _provider_from_media_matrix_entry(self, modality: str, entry: Dict[str, Any]) -> Dict[str, Any] | None:
        provider_id = str(entry.get("id") or "").strip()
        if not provider_id:
            return None
        normalized_modality = _normalized_modality(modality)
        explicit_model_ids = entry.get("modelIds")
        if isinstance(explicit_model_ids, list):
            model_ids = [
                str(item).strip()
                for item in explicit_model_ids
                if str(item).strip() and str(item).strip().lower() not in _PLUGIN_ONLY_MEDIA_MODEL_IDS
            ]
        else:
            model_ids = []
        if not model_ids:
            model_ids = _MEDIA_DEFAULT_MODEL_IDS.get(provider_id) or [f"{provider_id}-model"]
        registry_model_ids = [
            str(model.get("canonicalModelId") or "").strip()
            for model in media_model_capability_registry.models_for_provider(provider_id)
            if str(model.get("canonicalModelId") or "").strip()
        ]
        for registry_model_id in registry_model_ids:
            if registry_model_id.lower() not in _PLUGIN_ONLY_MEDIA_MODEL_IDS and registry_model_id not in model_ids:
                model_ids.append(registry_model_id)
        auth = dict(entry.get("auth") or {})
        adapter = str(entry.get("adapter") or "catalog_only")
        api_standard = str(entry.get("apiStandard") or adapter or "media_generation")
        registry_provider = media_model_capability_registry.provider(provider_id) or {}
        logo_asset = entry.get("logoAsset") or registry_provider.get("logoAsset") or ""
        return {
            "id": provider_id,
            "name": entry.get("displayName") or provider_id,
            "apiStandard": api_standard,
            "providerKind": "media_generation",
            "mediaModality": normalized_modality,
            "catalogVisibility": "internal_capability",
            "adapter": adapter,
            "baseUrl": entry.get("baseUrlDefault") or "",
            "logoAsset": logo_asset,
            "auth": auth,
            "probeStrategy": entry.get("probeStrategy") or "catalog_only",
            "modelsPath": entry.get("modelsPath") or entry.get("models_path") or "",
            "sourceUrl": entry.get("sourceUrl") or "",
            "credentialHelp": entry.get("credentialHelp") or {},
            "lastCheckedAt": entry.get("lastCheckedAt") or "",
            "confidence": entry.get("confidence") or "provider_docs",
            "credentialRealm": entry.get("credentialRealm") or "",
            "request": entry.get("request") or {},
            "polling": entry.get("polling") or {},
            "result": entry.get("result") or {},
            "statusMap": entry.get("statusMap") or {},
            "capabilityProfile": entry.get("capabilityProfile") or {},
            "models": [self._media_catalog_model(entry, normalized_modality, model_id) for model_id in model_ids],
        }

    def _provider_from_media_registry_entry(self, entry: Dict[str, Any]) -> Dict[str, Any] | None:
        provider_id = str(entry.get("providerId") or "").strip()
        if not provider_id:
            return None
        models = [
            model
            for model in media_model_capability_registry.models_for_provider(provider_id)
            if str(model.get("canonicalModelId") or "").strip().lower() not in _PLUGIN_ONLY_MEDIA_MODEL_IDS
        ]
        if not models:
            return None
        primary_modality = _normalized_modality((entry.get("modalities") or [""])[0])
        provider_entry = {
            "id": provider_id,
            "displayName": entry.get("displayName") or provider_id,
            "adapter": "catalog_only",
            "apiStandard": "catalog_only",
            "operationKinds": sorted({operation for model in models for operation in (model.get("operationKinds") or [])}),
            "modelIds": [str(model.get("canonicalModelId") or "") for model in models if model.get("canonicalModelId")],
            "logoAsset": entry.get("logoAsset") or "",
            "sourceUrl": ((entry.get("sourceRefs") or [{}])[0] or {}).get("url") if isinstance((entry.get("sourceRefs") or [{}])[0], dict) else "",
            "confidence": entry.get("confidence") or "community_or_inferred",
            "probeStrategy": "catalog_only",
        }
        return {
            "id": provider_id,
            "name": entry.get("displayName") or provider_id,
            "apiStandard": "catalog_only",
            "providerKind": "media_generation",
            "mediaModality": primary_modality,
            "adapter": "catalog_only",
            "baseUrl": "",
            "logoAsset": entry.get("logoAsset") or "",
            "auth": {},
            "probeStrategy": "catalog_only",
            "sourceUrl": provider_entry.get("sourceUrl") or "",
            "credentialHelp": {},
            "confidence": entry.get("confidence") or "community_or_inferred",
            "models": [
                self._media_catalog_model(
                    {**provider_entry, "modelIds": [str(model.get("canonicalModelId") or "")], "operationKinds": model.get("operationKinds") or []},
                    str(model.get("modality") or primary_modality),
                    str(model.get("canonicalModelId") or ""),
                )
                for model in models
                if model.get("canonicalModelId")
            ],
        }

    def _creative_media_matrix_providers(self) -> List[Dict[str, Any]]:
        with self._asset_cache_lock:
            if self._creative_media_providers_cache is not None:
                return deepcopy(self._creative_media_providers_cache)
            if not _CREATIVE_MEDIA_MATRIX_PATH.exists():
                self._creative_media_providers_cache = []
                return []
            try:
                with _CREATIVE_MEDIA_MATRIX_PATH.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception:
                # Failed loads must be retried; do not turn a transient read failure into a cache entry.
                return []
            providers: List[Dict[str, Any]] = []
            modalities = payload.get("modalities") if isinstance(payload, dict) else {}
            if not isinstance(modalities, dict):
                return []
            for modality, entries in modalities.items():
                for entry in _as_list(entries):
                    if not isinstance(entry, dict):
                        continue
                    provider = self._provider_from_media_matrix_entry(str(modality), entry)
                    if provider:
                        providers.append(provider)
            seen = {str(provider.get("id") or "") for provider in providers}
            for entry in _as_list(media_model_capability_registry.load().get("providers")):
                if not isinstance(entry, dict):
                    continue
                provider_id = str(entry.get("providerId") or "")
                if provider_id in seen:
                    continue
                provider = self._provider_from_media_registry_entry(entry)
                if provider:
                    providers.append(provider)
                    seen.add(provider_id)
            self._creative_media_providers_cache = deepcopy(providers)
            return deepcopy(self._creative_media_providers_cache)

    def _root_media_mappings(self) -> Dict[str, Set[str]]:
        with self._asset_cache_lock:
            if self._root_media_mappings_cache is not None:
                return {
                    key: set(values)
                    for key, values in self._root_media_mappings_cache.items()
                }
            mappings: Dict[str, Set[str]] = {}
            for root in _as_list(self._load_builtin().get("providers")):
                if not isinstance(root, dict):
                    continue
                root_id = str(root.get("id") or "").strip()
                if not root_id:
                    continue
                for descriptor in _as_list(root.get("capabilityEntries")):
                    if not isinstance(descriptor, dict):
                        continue
                    source_provider_id = str(descriptor.get("sourceProviderId") or "").strip()
                    if source_provider_id:
                        mappings.setdefault(source_provider_id, set()).add(root_id)
            self._root_media_mappings_cache = {
                key: set(values)
                for key, values in mappings.items()
            }
            return {
                key: set(values)
                for key, values in self._root_media_mappings_cache.items()
            }

    @staticmethod
    def _resolved_capability_entries(
        provider: Dict[str, Any],
        media_providers_by_id: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        resolved: List[Dict[str, Any]] = []
        for descriptor in _as_list(provider.get("capabilityEntries")):
            if not isinstance(descriptor, dict):
                continue
            media_modality = _normalized_modality(
                descriptor.get("type") or descriptor.get("mediaModality")
            )
            source_provider_id = str(descriptor.get("sourceProviderId") or "").strip()
            sources: List[Dict[str, Any]] = []
            if source_provider_id:
                source = media_providers_by_id.get(source_provider_id)
                if source:
                    sources.append(source)
            else:
                sources = [
                    source
                    for source in media_providers_by_id.values()
                    if (
                        (not media_modality or _normalized_modality(source.get("mediaModality")) == media_modality)
                        and _media_provider_matches_root(provider, source)
                    )
                ]
            if not sources:
                resolved.append(
                    {
                        **deepcopy(descriptor),
                        "mediaModality": media_modality,
                        "models": _as_list(descriptor.get("models")),
                    }
                )
                continue
            for source in sorted(sources, key=lambda item: str(item.get("id") or "")):
                source_id = str(source.get("id") or "").strip()
                resolved.append(
                    {
                        **deepcopy(source),
                        **deepcopy(descriptor),
                        "sourceProviderId": source_id,
                        "mediaModality": _normalized_modality(
                            descriptor.get("type") or descriptor.get("mediaModality") or source.get("mediaModality")
                        ),
                    }
                )
        return resolved

    def load(self) -> Dict[str, Any]:
        builtin = self._load_builtin()
        try:
            managed = self.load_managed()
        except ValueError:
            # Runtime catalog remains usable while the strict managed surface
            # reports the invalid overlay and offers backup restoration.
            managed = {"version": 1, "providers": []}
        custom = self._load_custom()
        media_providers = self._creative_media_matrix_providers()
        media_providers_by_id = {
            str(item.get("id") or ""): item
            for item in media_providers
            if str(item.get("id") or "").strip()
        }

        effective_providers: List[Dict[str, Any]] = []
        effective_positions: Dict[str, int] = {}
        for entry in _as_list(builtin.get("providers")):
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            provider_id = str(entry.get("id") or "")
            if provider_id in effective_positions:
                continue
            effective_positions[provider_id] = len(effective_providers)
            effective_providers.append(deepcopy(entry))
        for provider_patch in _as_list(managed.get("providers")):
            provider_id = str(provider_patch.get("id") or "")
            if provider_id in effective_positions:
                position = effective_positions[provider_id]
                effective_providers[position] = _merge_managed_patch(
                    effective_providers[position],
                    provider_patch,
                )
            else:
                effective_positions[provider_id] = len(effective_providers)
                effective_providers.append(deepcopy(provider_patch))
            managed_position = effective_positions[provider_id]
            effective_providers[managed_position]["isManaged"] = True
            effective_providers[managed_position]["isCustom"] = False

        providers: List[Dict[str, Any]] = []
        seen_provider_ids: Set[str] = set()
        for entry in _as_list(custom.get("providers")):
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            provider_id = str(entry.get("id") or "")
            if provider_id in seen_provider_ids:
                continue
            item = deepcopy(entry)
            item["isCustom"] = True
            item["isManaged"] = False
            item.setdefault("promptCachingProfileId", prompt_cache_profile_id_for_provider(provider_id))
            capability_entries = self._resolved_capability_entries(item, media_providers_by_id)
            if capability_entries:
                item["capabilityEntries"] = capability_entries
            providers.append(item)
            seen_provider_ids.add(provider_id)
        for entry in effective_providers:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            provider_id = str(entry.get("id") or "")
            if provider_id in seen_provider_ids:
                continue
            item = deepcopy(entry)
            item.setdefault("isCustom", False)
            item.setdefault("isManaged", False)
            item.setdefault("promptCachingProfileId", prompt_cache_profile_id_for_provider(provider_id))
            capability_entries = self._resolved_capability_entries(item, media_providers_by_id)
            if capability_entries:
                item["capabilityEntries"] = capability_entries
            providers.append(item)
            seen_provider_ids.add(provider_id)
        for entry in media_providers:
            provider_id = str(entry.get("id") or "")
            if provider_id in seen_provider_ids:
                continue
            item = deepcopy(entry)
            item.setdefault("isCustom", False)
            item.setdefault("isManaged", False)
            item.setdefault("promptCachingProfileId", prompt_cache_profile_id_for_provider(provider_id))
            providers.append(item)
            seen_provider_ids.add(provider_id)
        return {
            **builtin,
            "providers": providers,
            "managedCatalogStatus": self.get_managed_status(),
        }

    def list_providers(self) -> List[Dict[str, Any]]:
        return _as_list(self.load().get("providers"))

    def get_provider(self, provider_id: str) -> Dict[str, Any] | None:
        target = str(provider_id or "").strip()
        for entry in self.list_providers():
            if str(entry.get("id") or "") == target:
                return entry
        return None

    def make_custom_provider_id(self, name: str, base_url: str) -> str:
        seed = f"{name}|{base_url}".strip()
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
        if not slug:
            slug = "provider"
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
        return f"custom-{slug}-{digest}"

    def build_custom_provider(
        self,
        name: str,
        base_url: str,
        provider_id: str = "",
        *,
        provider_kind: str = "chat",
        media_modality: str = "",
        api_standard: str = "openai",
        declared_capabilities: List[str] | None = None,
    ) -> Dict[str, Any]:
        clean_name = str(name or "").strip()
        clean_base_url = str(base_url or "").strip().rstrip("/")
        if not clean_name:
            raise ValueError("customProviderName is required")
        if not clean_base_url:
            raise ValueError("baseUrl is required")
        custom_id = str(provider_id or "").strip() or self.make_custom_provider_id(clean_name, clean_base_url)
        clean_provider_kind = str(provider_kind or "chat").strip() or "chat"
        clean_modality = _normalized_modality(media_modality)
        is_media = clean_provider_kind == "media_generation" or clean_modality in _MEDIA_MODEL_TYPES
        clean_api_standard = str(api_standard or ("media_generation" if is_media else "openai")).strip()
        allowed_capabilities = {"text", "vision", *_MEDIA_MODEL_TYPES.keys()}
        clean_declared_capabilities = list(dict.fromkeys(
            str(item or "").strip().lower()
            for item in (declared_capabilities or [])
            if str(item or "").strip().lower() in allowed_capabilities
        ))
        if not clean_declared_capabilities:
            clean_declared_capabilities = [clean_modality] if is_media and clean_modality else ["text"]
        capability_entries = [
            {
                "type": capability,
                "mediaModality": capability,
                "models": [],
            }
            for capability in clean_declared_capabilities
            if capability in _MEDIA_MODEL_TYPES
        ]
        return {
            "id": custom_id,
            "name": clean_name,
            "apiStandard": clean_api_standard,
            "baseUrl": clean_base_url,
            "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
            "probeStrategy": "catalog_only" if is_media else "openai_models",
            "providerKind": "media_generation" if is_media else clean_provider_kind,
            "mediaModality": clean_modality if is_media else "",
            "promptCachingProfileId": prompt_cache_profile_id_for_provider(custom_id),
            "confidence": "custom",
            "isCustom": True,
            "declaredCapabilities": clean_declared_capabilities,
            "capabilityEntries": capability_entries,
            "models": [],
        }

    def save_custom_provider(self, provider: Dict[str, Any]) -> Dict[str, Any]:
        provider_id = str(provider.get("id") or "").strip()
        if not provider_id:
            raise ValueError("provider id is required")
        with self._custom_lock:
            payload = self._load_custom(strict=True)
            providers = [item for item in _as_list(payload.get("providers")) if str((item or {}).get("id") or "") != provider_id]
            providers.insert(0, {**provider, "isCustom": True, "models": _as_list(provider.get("models"))})
            payload["version"] = int(payload.get("version") or 1)
            payload["providers"] = providers
            self._save_custom(payload)
            return deepcopy(providers[0])

    def delete_custom_provider(
        self,
        provider_id: str,
        *,
        expected_current_digest: str = "",
        before_persist=None,
    ) -> bool:
        target = str(provider_id or "").strip()
        with self._custom_lock:
            payload = self._load_custom(strict=True)
            if expected_current_digest and _catalog_payload_digest(payload) != expected_current_digest:
                raise ValueError("custom catalog digest changed")
            before = _as_list(payload.get("providers"))
            after = [item for item in before if str((item or {}).get("id") or "") != target]
            if len(after) == len(before):
                return False
            payload["providers"] = after
            if callable(before_persist):
                before_persist(deepcopy(payload))
            self._save_custom(payload)
            return True

    def restore_custom_provider(
        self,
        provider_id: str,
        provider: Dict[str, Any] | None,
        *,
        expected_current_digest: str = "",
    ) -> Dict[str, Any] | None:
        target = str(provider_id or "").strip()
        with self._custom_lock:
            payload = self._load_custom(strict=True)
            if expected_current_digest and _catalog_payload_digest(payload) != expected_current_digest:
                raise ValueError("custom catalog digest changed")
            providers = [
                deepcopy(item)
                for item in _as_list(payload.get("providers"))
                if str((item or {}).get("id") or "") != target
            ]
            restored = None
            if provider is not None:
                restored = {**deepcopy(provider), "id": target, "isCustom": True}
                providers.insert(0, restored)
            payload["providers"] = providers
            self._save_custom(payload)
            return deepcopy(restored)

    def _model_from_catalog(self, provider: Dict[str, Any], model_id: str) -> Dict[str, Any]:
        for item in _as_list(provider.get("models")):
            if str(item.get("id") or "") == model_id:
                return dict(item)
        return {"id": model_id}

    def _has_explicit_catalog_model(self, provider: Dict[str, Any], model_id: str) -> bool:
        for item in _as_list(provider.get("models")):
            if str(item.get("id") or "") == str(model_id or ""):
                return any(
                    item.get(key)
                    for key in (
                        "type",
                        "capabilities",
                        "capabilityClass",
                        "capabilityOverride",
                        "capabilityFactsOverride",
                        "explicitCapabilityOverride",
                        "mediaLimits",
                        "mediaCapabilityRegistry",
                        "parameterProfile",
                    )
                )
        return False

    def _media_model_from_root_provider(self, provider: Dict[str, Any], model_id: str) -> Dict[str, Any] | None:
        provider_id = str(provider.get("id") or "").strip()
        display_model_id = str(model_id or "").strip()
        if not provider_id or not display_model_id:
            return None
        provider_kind = str(provider.get("providerKind") or "").strip()
        media_modality = _normalized_modality(provider.get("mediaModality") or "")
        if "/" not in display_model_id and provider_kind != "media_generation" and media_modality not in _MEDIA_MODEL_TYPES:
            return None
        matches: List[tuple[int, int, str, Dict[str, Any], Dict[str, Any]]] = []
        root_mappings = self._root_media_mappings()
        for media_provider in self._creative_media_matrix_providers():
            media_provider_id = str(media_provider.get("id") or "")
            root_provider_ids = root_mappings.get(media_provider_id, set())
            provider_affinity = (
                0
                if provider_id in root_provider_ids or _media_provider_matches_root(provider, media_provider)
                else 1
                if _media_base_url_matches(provider, media_provider)
                else 2
            )
            for item in _as_list(media_provider.get("models")):
                actual_model_id = str(item.get("id") or "").strip()
                match_quality = _media_model_match_quality(display_model_id, provider, media_provider, actual_model_id)
                if match_quality is None:
                    continue
                matches.append((provider_affinity, match_quality, media_provider_id, dict(item), media_provider))
        if not matches:
            return None
        _, _, media_provider_id, item, media_provider = sorted(matches, key=lambda entry: (entry[0], entry[1], entry[2], str(entry[3].get("id") or "")))[0]
        actual_model_id = str(item.get("id") or "").strip()
        model = dict(item)
        media_limits = dict(model.get("mediaLimits") or {})
        media_limits.setdefault("adapterProviderId", media_provider_id)
        media_limits.setdefault("providerModelId", actual_model_id)
        media_limits.setdefault("displayModelId", display_model_id)
        model["id"] = display_model_id
        model["modelId"] = display_model_id
        model["adapter"] = media_limits.get("adapter") or media_provider.get("adapter") or model.get("adapter") or ""
        model["mediaLimits"] = media_limits
        model["sourceProviderId"] = media_provider_id
        model["sourceProviderName"] = media_provider.get("name") or media_provider_id
        return model

    def _headers_for_probe(self, provider: Dict[str, Any], credential: str) -> Dict[str, str]:
        auth = dict(provider.get("auth") or {})
        headers: Dict[str, str] = {"Accept": "application/json"}
        if credential and auth.get("header"):
            scheme = str(auth.get("scheme") or "").strip()
            header_value = f"{scheme} {credential}".strip() if scheme else credential
            headers[str(auth["header"])] = header_value
        if str(provider.get("apiStandard") or "").lower() == "anthropic":
            headers.setdefault("anthropic-version", "2023-06-01")
        return headers

    def resolve_probe_target(self, provider: Dict[str, Any], base_url: str = "") -> Dict[str, Any]:
        return resolve_probe_target(provider, base_url)

    def _models_url_for_probe(self, provider: Dict[str, Any], effective_base_url: str) -> str:
        return self.resolve_probe_target(provider, effective_base_url)["url"]

    def _classify_probe_exception(self, exc: Exception) -> str:
        message = str(exc).lower()
        if isinstance(exc, (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return "tls_or_network_error"
        if "ssl" in message or "tls" in message or "eof" in message or "connection" in message or "timeout" in message:
            return "tls_or_network_error"
        return "online_probe_failed"

    def _safe_models_url_preview(self, provider: Dict[str, Any], effective_base_url: str) -> str:
        try:
            return self._models_url_for_probe(provider, effective_base_url)
        except Exception:
            return ""

    def probe_provider(
        self,
        provider_id: str,
        *,
        credential: str = "",
        base_url: str = "",
        timeout: float = 20.0,
    ) -> Dict[str, Any]:
        provider = self.get_provider(provider_id)
        if not provider:
            return {"ok": False, "error": "provider_not_found", "models": []}
        return self.probe_provider_entry(provider, credential=credential, base_url=base_url, timeout=timeout)

    def probe_provider_entry(
        self,
        provider: Dict[str, Any],
        *,
        credential: str = "",
        base_url: str = "",
        timeout: float = 20.0,
    ) -> Dict[str, Any]:
        strategy = str(provider.get("probeStrategy") or "catalog_only")
        effective_base_url = str(base_url or provider.get("baseUrl") or "").rstrip("/")
        if strategy == "catalog_only":
            return {
                "ok": False,
                "source": "catalog_metadata",
                "provider": provider,
                "models": [],
                "rawCount": len(_as_list(provider.get("models"))),
                "modelCount": 0,
                "reason": "catalog_only_provider",
                "error": "This provider preset has no real online model-list probe. Enter a model ID manually; catalog entries are used only for icons, type hints, and capability metadata.",
            }
        if not effective_base_url:
            return {"ok": False, "source": "online", "provider": provider, "models": [], "reason": "missing_base_url", "error": "baseUrl is required"}

        auth = dict(provider.get("auth") or {})
        if auth.get("type") == "api_key" and not credential:
            resolved_url = self._safe_models_url_preview(provider, effective_base_url)
            return {
                "ok": False,
                "source": "online",
                "provider": provider,
                "models": [],
                "reason": "credential_required",
                "error": "API key is required before probing online models.",
                "resolvedModelsUrl": resolved_url,
            }

        if strategy == "comfyui":
            return self._probe_comfyui(provider, effective_base_url=effective_base_url, timeout=timeout)

        url = ""
        try:
            url = self._models_url_for_probe(provider, effective_base_url)
            params = {}
            if credential and auth.get("query"):
                params[str(auth["query"])] = credential
            response = None
            last_exc: Exception | None = None
            for attempt in range(2):
                try:
                    response = requests.get(
                        url,
                        headers=self._headers_for_probe(provider, credential),
                        params=params,
                        timeout=timeout,
                        allow_redirects=False,
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    if self._classify_probe_exception(exc) != "tls_or_network_error" or attempt > 0:
                        raise
            if response is None:
                raise last_exc or RuntimeError("online probe did not return a response")
            if 300 <= response.status_code < 400:
                return {
                    "ok": False,
                    "source": "online",
                    "provider": provider,
                    "models": [],
                    "reason": "redirect_not_allowed",
                    "statusCode": response.status_code,
                    "resolvedModelsUrl": url,
                    "error": "Provider probe redirects are not allowed.",
                }
            if not response.ok:
                reason = "online_probe_failed"
                if response.status_code in (401, 403):
                    reason = "unauthorized"
                elif response.status_code == 404:
                    reason = "endpoint_not_found"
                return {
                    "ok": False,
                    "source": "online",
                    "provider": provider,
                    "models": [],
                    "reason": reason,
                    "statusCode": response.status_code,
                    "resolvedModelsUrl": url,
                    "error": _redact_probe_error(response.text, credential),
                }
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if data is None and isinstance(payload, dict):
                data = payload.get("models")
            if not isinstance(data, list):
                return {
                    "ok": False,
                    "source": "online",
                    "provider": provider,
                    "models": [],
                    "reason": "invalid_models_response",
                    "resolvedModelsUrl": url,
                    "error": "The response did not contain a data/models array.",
                }
            models: List[Dict[str, Any]] = []
            allowed_model_ids = {
                str(item or "").strip()
                for item in _as_list(provider.get("probeModelAllowlist"))
                if str(item or "").strip()
            }
            for item in data:
                if isinstance(item, str):
                    model_id = item.strip()
                    online_metadata: Dict[str, Any] = {}
                else:
                    online_metadata = dict(item or {})
                    model_id = str(online_metadata.get("id") or online_metadata.get("name") or "").strip()
                if model_id.startswith("models/"):
                    model_id = model_id.split("/", 1)[1]
                if not model_id:
                    continue
                if allowed_model_ids and model_id not in allowed_model_ids:
                    continue
                models.append(self.normalize_model(provider, model_id, online_metadata=online_metadata))
            return {
                "ok": True,
                "source": "online",
                "provider": provider,
                "models": models,
                "rawCount": len(data),
                "modelCount": len(models),
                "resolvedModelsUrl": url,
            }
        except Exception as exc:
            reason = self._classify_probe_exception(exc)
            return {
                "ok": False,
                "source": "online",
                "provider": provider,
                "models": [],
                "reason": reason,
                "resolvedModelsUrl": url,
                "error": _redact_probe_error(exc, credential),
            }

    def _probe_comfyui(self, provider: Dict[str, Any], *, effective_base_url: str, timeout: float = 20.0) -> Dict[str, Any]:
        url = ""
        try:
            safe_base_url = _validated_http_url(effective_base_url, "probe base URL")
            parsed_base_url = urlparse(safe_base_url)
            url = _validated_http_url(
                urlunparse(
                    parsed_base_url._replace(
                        path=f"{parsed_base_url.path.rstrip('/')}/object_info",
                        fragment="",
                    )
                ),
                "ComfyUI probe URL",
            )
            response = requests.get(url, timeout=timeout, allow_redirects=False)
            if 300 <= response.status_code < 400:
                return {
                    "ok": False,
                    "source": "online",
                    "provider": provider,
                    "models": [],
                    "reason": "redirect_not_allowed",
                    "statusCode": response.status_code,
                    "resolvedModelsUrl": url,
                    "error": "Provider probe redirects are not allowed.",
                }
            if not response.ok:
                return {
                    "ok": False,
                    "source": "online",
                    "provider": provider,
                    "models": [],
                    "reason": "online_probe_failed",
                    "statusCode": response.status_code,
                    "resolvedModelsUrl": url,
                    "error": _redact_probe_error(response.text),
                }
            payload = response.json()
            checkpoint_names: List[str] = []
            loader = payload.get("CheckpointLoaderSimple") if isinstance(payload, dict) else None
            required = ((loader or {}).get("input") or {}).get("required") or {}
            ckpt_payload = required.get("ckpt_name")
            if isinstance(ckpt_payload, list) and ckpt_payload and isinstance(ckpt_payload[0], list):
                checkpoint_names = [str(item) for item in ckpt_payload[0][:50] if str(item).strip()]
            models = [
                self.normalize_model(
                    provider,
                    f"checkpoint:{name}",
                    online_metadata={"capabilities": ["workflow", "image"], "checkpoint": name},
                )
                for name in checkpoint_names
            ] or [self.normalize_model(provider, "comfyui-workflow", online_metadata={"capabilities": ["workflow", "image", "video", "audio"]})]
            return {
                "ok": True,
                "source": "online",
                "provider": provider,
                "models": models,
                "rawCount": len(checkpoint_names) or 1,
                "modelCount": len(models),
                "resolvedModelsUrl": url,
                "capabilitySource": "online",
            }
        except Exception as exc:
            return {
                "ok": False,
                "source": "online",
                "provider": provider,
                "models": [],
                "reason": "online_probe_failed",
                "resolvedModelsUrl": url,
                "error": _redact_probe_error(exc),
            }

    def _capabilities_from_online(self, metadata: Dict[str, Any]) -> Set[str]:
        caps: Set[str] = set()
        for key in ("capabilities", "capabilityTags", "modalities", "input_modalities", "output_modalities"):
            value = metadata.get(key)
            if isinstance(value, list):
                caps.update(str(item).strip().lower() for item in value if str(item).strip())
        methods = metadata.get("supportedGenerationMethods") or metadata.get("supported_generation_methods")
        if isinstance(methods, list):
            method_text = " ".join(str(item).lower() for item in methods)
            if "generatecontent" in method_text or "chat" in method_text:
                caps.add("chat")
            if "embed" in method_text:
                caps.add("embedding")
        if metadata.get("inputTokenLimit") or metadata.get("outputTokenLimit"):
            caps.add("chat")
        return caps

    def _capabilities_from_family(self, provider: Dict[str, Any], model_id: str, provider_kind: str) -> Set[str]:
        ident = f"{provider.get('id') or ''} {provider.get('name') or ''} {model_id}".lower()
        caps: Set[str] = set()
        if provider_kind == "media_generation" or "comfyui" in ident:
            modality = _normalized_modality(provider.get("mediaModality"))
            if modality:
                caps.update(self._media_capabilities(modality))
            else:
                caps.update({"workflow", "image"})
            return caps
        if any(token in ident for token in ("embed", "embedding", "bge-m3", "text-embedding", "qwen3-embed")):
            caps.add("embedding")
            return caps
        if any(token in ident for token in ("rerank", "reranker", "bge-reranker")):
            caps.add("rerank")
            return caps
        if any(token in ident for token in _VOICE_MODEL_TOKENS):
            caps.update({"audio", "voice"})
            return caps
        if any(token in ident for token in _MUSIC_MODEL_TOKENS):
            caps.add("music")
            return caps
        caps.add("chat")
        if any(token in ident for token in ("gpt-4o", "gpt-4.1", "gpt-4.5", "gpt-5", "gemini", "claude-3", "claude-sonnet", "claude-opus", "doubao-seed", "qwen-vl", "vl", "vision")):
            caps.update({"vision", "multimodal"})
        if any(token in ident for token in ("reason", "thinking", "r1", "o1", "o3", "gpt-5", "gpt-5.5", "gpt-5.4", "gemini-3", "gemini-2.5", "claude")):
            caps.add("reasoning")
        if any(token in ident for token in ("tts", "audio", "speech", "voice")):
            caps.update({"audio", "voice"})
        if any(token in ident for token in ("music", "song", "mureka", "suno")):
            caps.add("music")
        if any(token in ident for token in ("image", "dall-e", "gpt-image", "seedream")):
            caps.add("image")
        if any(token in ident for token in ("video", "veo", "seedance", "wan", "sora")):
            caps.add("video")
        if any(token in ident for token in ("3d", "model3d", "tripo")):
            caps.add("model3d")
        return caps

    def _normalize_capability_map(self, tags: Set[str], provider_kind: str) -> Dict[str, bool]:
        media = provider_kind == "media_generation" or bool(tags.intersection({"image", "video", "audio", "voice", "music", "workflow", "model3d"}))
        embedding = "embedding" in tags
        rerank = "rerank" in tags or "reranker" in tags
        chat = ("chat" in tags and not media) or (not media and not embedding and not rerank)
        tools = bool(tags.intersection({"tools", "tool", "function", "function_calling"}))
        streaming = "streaming" in tags or (chat and not media)
        return {
            "chat": chat,
            "reasoning": "reasoning" in tags,
            "toolCalling": tools,
            "vision": "vision" in tags,
            "multimodal": "multimodal" in tags or "vision" in tags,
            "streaming": streaming,
            "streamUsage": bool(tags.intersection({"streamusage", "stream_usage", "stream-usage"})),
            "embedding": embedding,
            "rerank": rerank,
            "image": "image" in tags,
            "video": "video" in tags,
            "audio": "audio" in tags or "voice" in tags,
            "voice": "voice" in tags or "audio" in tags,
            "music": "music" in tags,
            "workflow": "workflow" in tags,
            "model3d": "model3d" in tags,
        }

    def _parameter_profile_for_model(self, model: Dict[str, Any], capability_map: Dict[str, bool], provider_kind: str) -> str:
        explicit = str(model.get("parameterProfile") or "").strip()
        if explicit:
            return explicit
        model_id = str(model.get("id") or "").strip().lower()
        if capability_map.get("music"):
            return "music_brief"
        if capability_map.get("voice"):
            if "voiceclone" in model_id or "voice-clone" in model_id:
                return "voice_clone"
            if "voicedesign" in model_id or "voice-design" in model_id:
                return "voice_design"
            return "voice_tts"
        return provider_kind

    def _infer_model_type(self, capability_map: Dict[str, bool]) -> str:
        if capability_map.get("embedding"):
            return "EMBEDDING"
        if capability_map.get("rerank"):
            return "RERANK"
        media_types = [
            ("workflow", "WORKFLOW"),
            ("model3d", "MODEL3D"),
            ("music", "MUSIC"),
            ("video", "VIDEO"),
            ("image", "IMAGE"),
            ("voice", "VOICE"),
        ]
        matched = [model_type for key, model_type in media_types if capability_map.get(key)]
        if len(matched) == 1:
            return matched[0]
        if matched:
            return "MEDIA"
        if capability_map.get("vision") or capability_map.get("multimodal"):
            return "MULTIMODAL"
        return "TEXT"

    def _capability_source(self, catalog_model: Dict[str, Any], online_metadata: Dict[str, Any], family_caps: Set[str]) -> str:
        if online_metadata and self._capabilities_from_online(online_metadata):
            return "online"
        if catalog_model.get("capabilitySource"):
            return str(catalog_model.get("capabilitySource"))
        if catalog_model.get("capabilities"):
            return "catalog"
        if family_caps:
            return "heuristic"
        return "manual"

    def _capability_tags_from_registry(self, registry_entry: Dict[str, Any] | None) -> Set[str]:
        if not registry_entry:
            return set()
        tags = set()
        for item in registry_entry.get("capabilities") or []:
            raw = str(item or "").strip()
            normalized = raw.lower()
            if not normalized:
                continue
            tags.add(normalized)
            if normalized == "toolcalling":
                tags.add("tools")
            if normalized == "thinking":
                tags.add("reasoning")
            if normalized == "text":
                tags.add("chat")
        return tags

    def _drift_warning(
        self,
        model: Dict[str, Any],
        registry_entry: Dict[str, Any] | None,
        field: str,
        registry_field: str,
    ) -> Dict[str, Any] | None:
        if not registry_entry:
            return None
        provider_value = model.get(field)
        registry_value = registry_entry.get(registry_field)
        if provider_value in (None, "", 0) or registry_value in (None, "", 0):
            return None
        try:
            if int(provider_value) == int(registry_value):
                return None
        except Exception:
            if str(provider_value) == str(registry_value):
                return None
        return {
            "field": field,
            "providerValue": provider_value,
            "registryValue": registry_value,
            "policy": "online metadata and explicit provider override win; registry is preferred over legacy inline catalog",
        }

    @staticmethod
    def _fact_source_refs(values: Any) -> List[str]:
        refs: List[str] = []
        items = values if isinstance(values, (list, tuple, set)) else [values] if values else []
        for value in items:
            ref = str(value.get("url") or "").strip() if isinstance(value, dict) else str(value or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
        return refs

    def _catalog_fact_provenance(
        self,
        *,
        fact_key: str,
        source_kind: str,
        provider: Dict[str, Any],
        model: Dict[str, Any],
        registry_entry: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        explicit = dict((model.get("factProvenance") or {}).get(fact_key) or {})
        if explicit:
            return deepcopy(explicit)
        if source_kind == "online_provider_metadata":
            return {"source": source_kind, "confidence": "authoritative"}
        if source_kind == "model_capability_registry":
            source_refs = self._fact_source_refs((registry_entry or {}).get("sourceRefs") or [])
            raw_confidence = str((registry_entry or {}).get("confidence") or "").strip().lower()
        else:
            source_refs = self._fact_source_refs(model.get("sourceRefs") or [])
            if not source_refs:
                source_url = str(provider.get("modelFactsSourceUrl") or provider.get("sourceUrl") or "").strip()
                source_refs = [source_url] if source_url else []
            raw_confidence = str(model.get("confidence") or provider.get("confidence") or "").strip().lower()
        confidence = (
            "authoritative"
            if raw_confidence in {"official", "authoritative"}
            else "reviewed"
            if raw_confidence in {"provider_docs", "reviewed"}
            else "unverified"
        )
        return {
            "source": "official_docs" if confidence == "authoritative" else source_kind,
            "confidence": confidence,
            **({"sourceRefs": source_refs} if source_refs else {}),
        }

    def normalize_model(self, provider: Dict[str, Any], model_id: str, *, online_metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        online_metadata = dict(online_metadata or {})
        provider_id = str(provider.get("id") or "").strip()
        catalog_model = self._model_from_catalog(provider, model_id)
        media_catalog_model = self._media_model_from_root_provider(provider, model_id)
        has_explicit_catalog_model = self._has_explicit_catalog_model(provider, model_id)
        model = catalog_model if has_explicit_catalog_model else media_catalog_model or catalog_model
        if has_explicit_catalog_model and media_catalog_model:
            media_limits = deepcopy(dict(model.get("mediaLimits") or {}))
            suggested_limits = dict(media_catalog_model.get("mediaLimits") or {})
            for key in ("capabilityModes", "operationCapabilityProfiles", "mediaCapabilityRegistry"):
                if key not in media_limits and key in suggested_limits:
                    media_limits[key] = deepcopy(suggested_limits[key])
            model["mediaLimits"] = media_limits
        provider_kind = str(provider.get("providerKind") or "chat")
        registry_entry = model_capability_registry.find(model_id)
        explicit_provider_override = bool(
            model.get("capabilityOverride")
            or model.get("capabilityFactsOverride")
            or model.get("providerCapabilityOverride")
            or model.get("explicitCapabilityOverride")
        )
        catalog_caps = {str(item).strip().lower() for item in list(model.get("capabilities") or []) if str(item).strip()}
        online_caps = self._capabilities_from_online(online_metadata)
        registry_caps = self._capability_tags_from_registry(registry_entry)
        family_caps = self._capabilities_from_family(provider, model_id, provider_kind)
        capability_tags = catalog_caps | online_caps | registry_caps | family_caps
        capability_map = self._normalize_capability_map(capability_tags, provider_kind)
        provider_context_window = model.get("contextWindow")
        provider_max_tokens = model.get("maxOutputTokens") or model.get("maxTokens")
        registry_context_window = (registry_entry or {}).get("contextWindowTokens")
        registry_max_tokens = (registry_entry or {}).get("maxOutputTokens")
        online_context_window = (
            online_metadata.get("inputTokenLimit")
            or online_metadata.get("input_token_limit")
            or online_metadata.get("context_length")
        )
        online_max_tokens = (
            online_metadata.get("outputTokenLimit")
            or online_metadata.get("output_token_limit")
            or online_metadata.get("max_output_tokens")
        )
        if online_context_window:
            context_window = online_context_window
            context_source = "online_provider_metadata"
        elif explicit_provider_override and provider_context_window:
            context_window = provider_context_window
            context_source = "provider_catalog"
        elif registry_context_window:
            context_window = registry_context_window
            context_source = "model_capability_registry"
        else:
            context_window = provider_context_window
            context_source = "provider_catalog"
        if online_max_tokens:
            max_tokens = online_max_tokens
            max_tokens_source = "online_provider_metadata"
        elif explicit_provider_override and provider_max_tokens:
            max_tokens = provider_max_tokens
            max_tokens_source = "provider_catalog"
        elif registry_max_tokens:
            max_tokens = registry_max_tokens
            max_tokens_source = "model_capability_registry"
        else:
            max_tokens = provider_max_tokens
            max_tokens_source = "provider_catalog"
        requires_text_output_limit = (
            provider_kind == "chat"
            and not bool(provider.get("isCustom"))
            and bool(
                capability_map.get("chat")
                or capability_map.get("text")
                or capability_map.get("vision")
                or capability_map.get("multimodal")
            )
        )
        used_conservative_max_tokens = not max_tokens and requires_text_output_limit
        if used_conservative_max_tokens:
            max_tokens = _CONSERVATIVE_2026_MAX_OUTPUT_TOKENS
        capability_source = self._capability_source(model, online_metadata, family_caps)
        if registry_entry and not explicit_provider_override and capability_source in {"heuristic", "manual"}:
            capability_source = "model_capability_registry"
        drift_warnings = [
            warning
            for warning in [
                self._drift_warning(model, registry_entry, "contextWindow", "contextWindowTokens"),
                self._drift_warning(model, registry_entry, "maxOutputTokens", "maxOutputTokens"),
                self._drift_warning(model, registry_entry, "maxTokens", "maxOutputTokens"),
            ]
            if warning
        ]
        media_registry = model.get("mediaCapabilityRegistry") or (model.get("mediaLimits") or {}).get("mediaCapabilityRegistry") or {}
        capability_registry_payload = (
            {
                "canonicalModelId": (registry_entry or {}).get("canonicalModelId"),
                "displayName": (registry_entry or {}).get("displayName"),
                "confidence": (registry_entry or {}).get("confidence"),
                "missingFields": (registry_entry or {}).get("missingFields") or [],
                "sourceRefs": (registry_entry or {}).get("sourceRefs") or [],
            }
            if registry_entry
            else dict(media_registry)
            if media_registry
            else {}
        )
        media_model_type = str(model.get("type") or "").strip().upper() if model.get("mediaLimits") else ""
        resolved_model_type = (
            media_model_type
            if media_model_type in set(_MEDIA_MODEL_TYPES.values())
            else self._infer_model_type(capability_map)
        )
        reasoning_surface = resolve_reasoning_surface_for_metadata(
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "provider_record": provider,
                "model_record": {
                    **model,
                    **({"reasoningSurface": (registry_entry or {}).get("reasoningSurface")} if not model.get("reasoningSurface") else {}),
                },
            }
        )
        thinking_control = resolve_thinking_control_for_metadata(
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "provider_record": provider,
                "model_record": model,
            }
        )
        reasoning_effort_control = resolve_reasoning_effort_control_for_metadata(
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "provider_record": provider,
                "model_record": model,
            }
        )
        media_limits = deepcopy(dict(model.get("mediaLimits") or {}))
        raw_availability = model.get("availability")
        availability = (
            deepcopy(dict(raw_availability))
            if isinstance(raw_availability, dict)
            else {"status": str(raw_availability).strip()}
            if str(raw_availability or "").strip()
            else {}
        )
        catalog_connectable = _provider_catalog_connectable(provider)
        availability["catalogConnectable"] = catalog_connectable
        if not catalog_connectable:
            catalog_only = (
                str(provider.get("apiStandard") or provider.get("api_standard") or "").strip().lower() == "catalog_only"
                or str(provider.get("adapter") or "").strip().lower() == "catalog_only"
            )
            availability["catalogConnectReason"] = (
                "provider_runtime_unavailable" if catalog_only else "provider_endpoint_unconfigured"
            )
        else:
            availability.pop("catalogConnectReason", None)
        fact_provenance: Dict[str, Any] = {}
        if context_window:
            fact_provenance["contextWindow"] = self._catalog_fact_provenance(
                fact_key="contextWindow",
                source_kind=context_source,
                provider=provider,
                model=model,
                registry_entry=registry_entry,
            )
        if max_tokens:
            fact_provenance["maxTokens"] = (
                deepcopy(_CONSERVATIVE_2026_MAX_OUTPUT_PROVENANCE)
                if used_conservative_max_tokens
                else self._catalog_fact_provenance(
                    fact_key="maxTokens",
                    source_kind=max_tokens_source,
                    provider=provider,
                    model=model,
                    registry_entry=registry_entry,
                )
            )
        return {
            "id": model_id,
            "modelId": model_id,
            "modelRef": make_model_ref(str(provider.get("id") or ""), model_id),
            "type": resolved_model_type,
            "logoAsset": model.get("logoAsset") or online_metadata.get("logoAsset") or provider.get("logoAsset") or "",
            "promptCachingProfileId": provider.get("promptCachingProfileId")
            or prompt_cache_profile_id_for_provider(str(provider.get("id") or "")),
            "contextWindow": context_window,
            "maxTokens": max_tokens,
            "factProvenance": fact_provenance,
            "capabilities": capability_map,
            "reasoningSurface": reasoning_surface,
            "thinkingControl": thinking_control,
            "reasoningEffortControl": reasoning_effort_control,
            "capabilityTags": sorted(capability_tags),
            "capabilitySource": capability_source,
            "capabilityRegistryMatched": bool(registry_entry or media_registry),
            "capabilityRegistry": capability_registry_payload,
            "pricing": {
                "inputPerMillionTokens": (registry_entry or {}).get("inputPricePerMillionTokens"),
                "outputPerMillionTokens": (registry_entry or {}).get("outputPricePerMillionTokens"),
                "source": "benchlm",
            }
            if registry_entry
            else {},
            "driftWarnings": drift_warnings,
            "capabilityClass": (
                "media_generation"
                if capability_map.get("image")
                or capability_map.get("video")
                or capability_map.get("audio")
                or capability_map.get("voice")
                or capability_map.get("music")
                or capability_map.get("workflow")
                or capability_map.get("model3d")
                else "embedding"
                if capability_map.get("embedding")
                else "reranker"
                if capability_map.get("rerank")
                else "vision_multimodal"
                if capability_map.get("vision") or capability_map.get("multimodal")
                else "chat_reasoning"
                if capability_map.get("reasoning")
                else "chat_tool_calling"
                if capability_map.get("toolCalling")
                else "chat_general"
            ),
            "parameterProfile": self._parameter_profile_for_model(model, capability_map, provider_kind),
            "mediaLimits": media_limits,
            "operationKinds": deepcopy(model.get("operationKinds") or media_limits.get("operationKinds") or []),
            "adapter": model.get("adapter") or media_limits.get("adapter") or provider.get("adapter") or "",
            "rerankApiFlavor": model.get("rerankApiFlavor") or provider.get("rerankApiFlavor") or "",
            "availability": availability,
            "sourceRefs": deepcopy(
                model.get("sourceRefs")
                or capability_registry_payload.get("sourceRefs")
                or []
            ),
        }


model_provider_catalog = ModelProviderCatalog()
