from __future__ import annotations

import json
import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Set

import requests

from core.model_ref import make_model_ref


_CATALOG_PATH = Path(__file__).resolve().parent / "model_catalog" / "provider_catalog.json"
_CUSTOM_CATALOG_PATH = Path.home() / ".v8-agent-os" / "model_provider_catalog.custom.json"
_CREATIVE_MEDIA_MATRIX_PATH = (
    Path(__file__).resolve().parents[1]
    / "runtimes"
    / "creative_media"
    / "assets"
    / "media_provider_format_matrix.json"
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
    "stability_image": ["stable-image-core"],
    "fal_image": ["fal-image-model"],
    "replicate_image": ["replicate-image-model"],
    "volcengine_seedance": ["doubao-seedance-1-0-pro-fast-251015"],
    "google_veo": ["veo-3.1-generate-preview"],
    "runway_video": ["gen4_turbo"],
    "luma_video": ["ray-2"],
    "minimax_video": ["video-01"],
    "kling_video": ["kling-v2-1"],
    "v8_audio_tts": ["v8-audio-tts"],
    "openai_audio_speech": ["gpt-4o-mini-tts"],
    "elevenlabs_tts": ["eleven_multilingual_v2"],
    "minimax_tts": ["speech-02-hd"],
    "mureka_music": ["mureka-o1"],
    "fal_music": ["fal-music-model"],
    "suno_placeholder": ["suno-future-generation"],
    "fal_3d": ["fal-3d-model"],
    "tripo3d_placeholder": ["tripo3d-model"],
}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _normalized_modality(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"audio", "speech", "tts"}:
        return "voice"
    if raw in {"3d", "model_3d", "model-3d"}:
        return "model3d"
    return raw


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

    def _media_catalog_model(self, provider_entry: Dict[str, Any], modality: str, model_id: str) -> Dict[str, Any]:
        request = dict(provider_entry.get("request") or {})
        polling = dict(provider_entry.get("polling") or {})
        result = dict(provider_entry.get("result") or {})
        return {
            "id": model_id,
            "type": self._media_model_type(modality),
            "capabilities": self._media_capabilities(modality),
            "parameterProfile": provider_entry.get("apiStandard") or provider_entry.get("adapter") or "media_generation",
            "mediaLimits": {
                "modality": _normalized_modality(modality),
                "adapter": provider_entry.get("adapter") or "catalog_only",
                "apiStandard": provider_entry.get("apiStandard") or "",
                "submitPath": request.get("submitPath") or "",
                "pollingMode": polling.get("mode") or "none",
                "resultPaths": _as_list(result.get("paths")),
                "sizeFormat": request.get("sizeFormat") or "",
            },
        }

    def _provider_from_media_matrix_entry(self, modality: str, entry: Dict[str, Any]) -> Dict[str, Any] | None:
        provider_id = str(entry.get("id") or "").strip()
        if not provider_id:
            return None
        normalized_modality = _normalized_modality(modality)
        model_ids = _MEDIA_DEFAULT_MODEL_IDS.get(provider_id) or [f"{provider_id}-model"]
        auth = dict(entry.get("auth") or {})
        adapter = str(entry.get("adapter") or "catalog_only")
        api_standard = str(entry.get("apiStandard") or adapter or "media_generation")
        return {
            "id": provider_id,
            "name": entry.get("displayName") or provider_id,
            "apiStandard": api_standard,
            "providerKind": "media_generation",
            "mediaModality": normalized_modality,
            "adapter": adapter,
            "baseUrl": entry.get("baseUrlDefault") or "",
            "auth": auth,
            "probeStrategy": "catalog_only",
            "sourceUrl": entry.get("sourceUrl") or "",
            "credentialHelp": entry.get("credentialHelp") or {},
            "lastCheckedAt": entry.get("lastCheckedAt") or "",
            "confidence": entry.get("confidence") or "provider_docs",
            "request": entry.get("request") or {},
            "polling": entry.get("polling") or {},
            "result": entry.get("result") or {},
            "statusMap": entry.get("statusMap") or {},
            "models": [self._media_catalog_model(entry, normalized_modality, model_id) for model_id in model_ids],
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
            providers.append(item)
            seen_provider_ids.add(str(item.get("id") or ""))
        for entry in _as_list(builtin.get("providers")):
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            item = deepcopy(entry)
            item.setdefault("isCustom", False)
            item.pop("modelsUrl", None)
            item.pop("models_url", None)
            providers.append(item)
            seen_provider_ids.add(str(item.get("id") or ""))
        for entry in self._creative_media_matrix_providers():
            provider_id = str(entry.get("id") or "")
            if provider_id in seen_provider_ids:
                continue
            item = deepcopy(entry)
            item.setdefault("isCustom", False)
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
            models = [
                self.normalize_model(provider, str(item.get("id") or item.get("modelId") or ""))
                for item in _as_list(provider.get("models"))
                if str(item.get("id") or item.get("modelId") or "").strip()
            ]
            return {
                "ok": True,
                "source": "catalog",
                "provider": provider,
                "models": models,
                "rawCount": len(_as_list(provider.get("models"))),
                "modelCount": len(models),
                "reason": "catalog_only_provider",
            }
        if not effective_base_url:
            return {"ok": False, "source": "online", "provider": provider, "models": [], "reason": "missing_base_url", "error": "baseUrl is required"}

        auth = dict(provider.get("auth") or {})
        if auth.get("type") == "api_key" and not credential:
            return {
                "ok": False,
                "source": "online",
                "provider": provider,
                "models": [],
                "reason": "credential_required",
                "error": "API key is required before probing online models.",
                "resolvedModelsUrl": f"{effective_base_url}/models",
            }

        if strategy == "comfyui":
            return self._probe_comfyui(provider, effective_base_url=effective_base_url, timeout=timeout)

        try:
            url = f"{effective_base_url}/models"
            params = {}
            if credential and auth.get("query"):
                params[str(auth["query"])] = credential
            response = requests.get(
                url,
                headers=self._headers_for_probe(provider, credential),
                params=params,
                timeout=timeout,
            )
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
            return {
                "ok": False,
                "source": "online",
                "provider": provider,
                "models": [],
                "reason": "online_probe_failed",
                "resolvedModelsUrl": f"{effective_base_url}/models",
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
        chat = "chat" in tags or (not media and not embedding and not rerank)
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
        if catalog_model.get("capabilities"):
            return "catalog"
        if family_caps:
            return "heuristic"
        return "manual"

    def normalize_model(self, provider: Dict[str, Any], model_id: str, *, online_metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
        online_metadata = dict(online_metadata or {})
        model = self._model_from_catalog(provider, model_id)
        provider_kind = str(provider.get("providerKind") or "chat")
        catalog_caps = {str(item).strip().lower() for item in list(model.get("capabilities") or []) if str(item).strip()}
        online_caps = self._capabilities_from_online(online_metadata)
        family_caps = self._capabilities_from_family(provider, model_id, provider_kind)
        capability_tags = catalog_caps | online_caps | family_caps
        capability_map = self._normalize_capability_map(capability_tags, provider_kind)
        context_window = (
            model.get("contextWindow")
            or online_metadata.get("inputTokenLimit")
            or online_metadata.get("input_token_limit")
            or online_metadata.get("context_length")
        )
        max_tokens = (
            model.get("maxOutputTokens")
            or model.get("maxTokens")
            or online_metadata.get("outputTokenLimit")
            or online_metadata.get("output_token_limit")
            or online_metadata.get("max_output_tokens")
        )
        capability_source = self._capability_source(model, online_metadata, family_caps)
        return {
            "id": model_id,
            "modelId": model_id,
            "modelRef": make_model_ref(str(provider.get("id") or ""), model_id),
            "type": self._infer_model_type(capability_map),
            "contextWindow": context_window,
            "maxTokens": max_tokens,
            "capabilities": capability_map,
            "capabilityTags": sorted(capability_tags),
            "capabilitySource": capability_source,
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
            "parameterProfile": model.get("parameterProfile") or provider_kind,
            "mediaLimits": model.get("mediaLimits") or {},
        }


model_provider_catalog = ModelProviderCatalog()
