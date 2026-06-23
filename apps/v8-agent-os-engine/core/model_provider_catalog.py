from __future__ import annotations

import json
import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Set

import requests

from core.model_capability_registry import model_capability_registry
from core.media_model_capability_registry import media_model_capability_registry
from core.model_ref import make_model_ref
from core.prompt_cache_gateway import prompt_cache_profile_id_for_provider
from core.reasoning_surface_contract import resolve_reasoning_surface_for_metadata


_CATALOG_PATH = Path(__file__).resolve().parent / "model_catalog" / "provider_catalog.json"
_CUSTOM_CATALOG_PATH = Path.home() / ".v8-agent-os" / "model_provider_catalog.custom.json"
_CREATIVE_MEDIA_MATRIX_PATH = (
    Path(__file__).resolve().parents[1]
    / "runtimes"
    / "creative_media"
    / "assets"
    / "media_provider_format_matrix.json"
)
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
    "volcengine_seedance": ["doubao-seedance-1-0-pro-fast-251015"],
    "aliyun_bailian_video": ["wan2.7-t2v"],
    "google_veo": ["veo-3.1-generate-preview"],
    "runway_video": ["gen4_turbo"],
    "luma_video": ["ray-2"],
    "minimax_video": ["MiniMax-Hailuo-2.3"],
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

_MEDIA_ADAPTER_ROOT_PROVIDERS = {
    "agnes_image": "agnes",
    "agnes_video": "agnes",
    "minimax_image": "minimax-cn",
    "minimax_video": "minimax-cn",
    "minimax_tts": "minimax-cn",
    "minimax_music": "minimax-cn",
}

_MEDIA_MODEL_PREFIXES = {"image", "video", "voice", "music", "workflow", "model3d"}

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


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _normalized_modality(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"audio", "speech", "tts"}:
        return "voice"
    if raw in {"3d", "model_3d", "model-3d"}:
        return "model3d"
    return raw


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
    def __init__(self, path: Path = _CATALOG_PATH, custom_path: Path = _CUSTOM_CATALOG_PATH) -> None:
        self.path = path
        self.custom_path = custom_path
        self._cache: Dict[str, Any] | None = None

    def _load_builtin(self) -> Dict[str, Any]:
        if self._cache is None:
            with self.path.open("r", encoding="utf-8") as handle:
                self._cache = json.load(handle)
        return deepcopy(self._cache)

    def _load_custom(self) -> Dict[str, Any]:
        if not self.custom_path.exists():
            return {"version": 1, "providers": []}
        try:
            with self.custom_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                return {"version": 1, "providers": []}
            return payload
        except Exception:
            return {"version": 1, "providers": []}

    def _save_custom(self, payload: Dict[str, Any]) -> None:
        self.custom_path.parent.mkdir(parents=True, exist_ok=True)
        with self.custom_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

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
            exact_operations = [
                str(item).strip()
                for item in registry_operations
                if str(item).strip() and (not prefix or str(item).strip().startswith(prefix))
            ]
            if exact_operations:
                return exact_operations
        return self._media_operation_kinds(provider_entry, modality)

    def _media_catalog_model(self, provider_entry: Dict[str, Any], modality: str, model_id: str) -> Dict[str, Any]:
        provider_id = str(provider_entry.get("id") or "")
        registry_entry = media_model_capability_registry.find(provider_id, model_id)
        request = dict(provider_entry.get("request") or {})
        polling = dict(provider_entry.get("polling") or {})
        result = dict(provider_entry.get("result") or {})
        operation_kinds = self._media_model_operation_kinds(provider_entry, modality, model_id)
        operation_capability_profiles = {
            operation_kind: _media_capability_profile(provider_id, model_id, operation_kind)
            for operation_kind in operation_kinds
        }
        operation_capability_profiles = {key: value for key, value in operation_capability_profiles.items() if value}
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
                "operationKinds": operation_kinds,
                "operationCapabilityProfiles": operation_capability_profiles,
                "submitPath": request.get("submitPath") or "",
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
            model_ids = [str(item).strip() for item in explicit_model_ids if str(item).strip()]
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
            if registry_model_id not in model_ids:
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
        models = media_model_capability_registry.models_for_provider(provider_id)
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
        if not _CREATIVE_MEDIA_MATRIX_PATH.exists():
            return []
        try:
            with _CREATIVE_MEDIA_MATRIX_PATH.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
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
        return providers

    def load(self) -> Dict[str, Any]:
        builtin = self._load_builtin()
        custom = self._load_custom()
        providers: List[Dict[str, Any]] = []
        seen_provider_ids: Set[str] = set()
        for entry in _as_list(custom.get("providers")):
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            item = deepcopy(entry)
            item["isCustom"] = True
            item.setdefault("promptCachingProfileId", prompt_cache_profile_id_for_provider(str(item.get("id") or "")))
            providers.append(item)
            seen_provider_ids.add(str(item.get("id") or ""))
        for entry in _as_list(builtin.get("providers")):
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            item = deepcopy(entry)
            item.setdefault("isCustom", False)
            item.setdefault("promptCachingProfileId", prompt_cache_profile_id_for_provider(str(item.get("id") or "")))
            providers.append(item)
            seen_provider_ids.add(str(item.get("id") or ""))
        for entry in self._creative_media_matrix_providers():
            provider_id = str(entry.get("id") or "")
            if provider_id in seen_provider_ids:
                continue
            item = deepcopy(entry)
            item.setdefault("isCustom", False)
            item.setdefault("promptCachingProfileId", prompt_cache_profile_id_for_provider(provider_id))
            providers.append(item)
            seen_provider_ids.add(provider_id)
        return {**builtin, "providers": providers}

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
            "models": [],
        }

    def save_custom_provider(self, provider: Dict[str, Any]) -> Dict[str, Any]:
        provider_id = str(provider.get("id") or "").strip()
        if not provider_id:
            raise ValueError("provider id is required")
        payload = self._load_custom()
        providers = [item for item in _as_list(payload.get("providers")) if str((item or {}).get("id") or "") != provider_id]
        providers.insert(0, {**provider, "isCustom": True, "models": _as_list(provider.get("models"))})
        payload["version"] = int(payload.get("version") or 1)
        payload["providers"] = providers
        self._save_custom(payload)
        return deepcopy(providers[0])

    def delete_custom_provider(self, provider_id: str) -> bool:
        target = str(provider_id or "").strip()
        payload = self._load_custom()
        before = _as_list(payload.get("providers"))
        after = [item for item in before if str((item or {}).get("id") or "") != target]
        if len(after) == len(before):
            return False
        payload["providers"] = after
        self._save_custom(payload)
        return True

    def _model_from_catalog(self, provider: Dict[str, Any], model_id: str) -> Dict[str, Any]:
        for item in _as_list(provider.get("models")):
            if str(item.get("id") or "") == model_id:
                return dict(item)
        return {"id": model_id}

    def _media_model_from_root_provider(self, provider_id: str, model_id: str) -> Dict[str, Any] | None:
        if "/" not in model_id:
            return None
        prefix, actual_model_id = model_id.split("/", 1)
        modality = _normalized_modality(prefix)
        if modality not in _MEDIA_MODEL_PREFIXES or not actual_model_id:
            return None
        for media_provider in self._creative_media_matrix_providers():
            media_provider_id = str(media_provider.get("id") or "")
            if _MEDIA_ADAPTER_ROOT_PROVIDERS.get(media_provider_id) != provider_id:
                continue
            if _normalized_modality(media_provider.get("mediaModality")) != modality:
                continue
            for item in _as_list(media_provider.get("models")):
                if str(item.get("id") or "") != actual_model_id:
                    continue
                model = dict(item)
                media_limits = dict(model.get("mediaLimits") or {})
                media_limits.setdefault("adapterProviderId", media_provider_id)
                media_limits.setdefault("providerModelId", actual_model_id)
                media_limits.setdefault("displayModelId", model_id)
                model["id"] = model_id
                model["modelId"] = model_id
                model["adapter"] = media_limits.get("adapter") or media_provider.get("adapter") or model.get("adapter") or ""
                model["mediaLimits"] = media_limits
                model["sourceProviderId"] = media_provider_id
                model["sourceProviderName"] = media_provider.get("name") or media_provider_id
                return model
        return None

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

    def _models_url_for_probe(self, provider: Dict[str, Any], effective_base_url: str) -> str:
        explicit_url = str(provider.get("modelsUrl") or provider.get("models_url") or "").strip()
        if explicit_url:
            return explicit_url
        path = str(provider.get("modelsPath") or provider.get("models_path") or "/models").strip() or "/models"
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{effective_base_url}{path}"

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
            return f"{effective_base_url}/models"

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

        url = self._models_url_for_probe(provider, effective_base_url)
        try:
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
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    if self._classify_probe_exception(exc) != "tls_or_network_error" or attempt > 0:
                        raise
            if response is None:
                raise last_exc or RuntimeError("online probe did not return a response")
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
                    "error": response.text[:500],
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
                "error": str(exc),
            }

    def _probe_comfyui(self, provider: Dict[str, Any], *, effective_base_url: str, timeout: float = 20.0) -> Dict[str, Any]:
        url = f"{effective_base_url}/object_info"
        try:
            response = requests.get(url, timeout=timeout)
            if not response.ok:
                return {
                    "ok": False,
                    "source": "online",
                    "provider": provider,
                    "models": [],
                    "reason": "online_probe_failed",
                    "statusCode": response.status_code,
                    "resolvedModelsUrl": url,
                    "error": response.text[:500],
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
                "error": str(exc),
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

    def normalize_model(self, provider: Dict[str, Any], model_id: str, *, online_metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        online_metadata = dict(online_metadata or {})
        provider_id = str(provider.get("id") or "").strip()
        model = self._media_model_from_root_provider(provider_id, model_id) or self._model_from_catalog(provider, model_id)
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
        context_window = (
            online_metadata.get("inputTokenLimit")
            or online_metadata.get("input_token_limit")
            or online_metadata.get("context_length")
            or (provider_context_window if explicit_provider_override else None)
            or registry_context_window
            or provider_context_window
        )
        max_tokens = (
            online_metadata.get("outputTokenLimit")
            or online_metadata.get("output_token_limit")
            or online_metadata.get("max_output_tokens")
            or (provider_max_tokens if explicit_provider_override else None)
            or registry_max_tokens
            or provider_max_tokens
        )
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
        return {
            "id": model_id,
            "modelId": model_id,
            "modelRef": make_model_ref(str(provider.get("id") or ""), model_id),
            "type": self._infer_model_type(capability_map),
            "logoAsset": model.get("logoAsset") or online_metadata.get("logoAsset") or "",
            "promptCachingProfileId": provider.get("promptCachingProfileId")
            or prompt_cache_profile_id_for_provider(str(provider.get("id") or "")),
            "contextWindow": context_window,
            "maxTokens": max_tokens,
            "capabilities": capability_map,
            "reasoningSurface": reasoning_surface,
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
            "mediaLimits": model.get("mediaLimits") or {},
        }


model_provider_catalog = ModelProviderCatalog()
