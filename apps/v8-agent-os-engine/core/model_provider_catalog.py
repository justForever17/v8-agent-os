from __future__ import annotations

import json
import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

import requests

from core.model_ref import make_model_ref


_CATALOG_PATH = Path(__file__).resolve().parent / "model_catalog" / "provider_catalog.json"
_CUSTOM_CATALOG_PATH = Path.home() / ".v8-agent-os" / "model_provider_catalog.custom.json"


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


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

    def load(self) -> Dict[str, Any]:
        builtin = self._load_builtin()
        custom = self._load_custom()
        providers: List[Dict[str, Any]] = []
        for entry in _as_list(custom.get("providers")):
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            item = deepcopy(entry)
            item["isCustom"] = True
            providers.append(item)
        for entry in _as_list(builtin.get("providers")):
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            item = deepcopy(entry)
            item.setdefault("isCustom", False)
            item.pop("modelsUrl", None)
            item.pop("models_url", None)
            providers.append(item)
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

    def build_custom_provider(self, name: str, base_url: str, provider_id: str = "") -> Dict[str, Any]:
        clean_name = str(name or "").strip()
        clean_base_url = str(base_url or "").strip().rstrip("/")
        if not clean_name:
            raise ValueError("customProviderName is required")
        if not clean_base_url:
            raise ValueError("baseUrl is required")
        custom_id = str(provider_id or "").strip() or self.make_custom_provider_id(clean_name, clean_base_url)
        return {
            "id": custom_id,
            "name": clean_name,
            "apiStandard": "openai",
            "baseUrl": clean_base_url,
            "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
            "probeStrategy": "openai_models",
            "recommendedTemperature": None,
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
            return {
                "ok": False,
                "source": "catalog",
                "provider": provider,
                "models": [],
                "reason": "catalog_only_provider_not_probeable",
                "error": "This provider does not expose a /models probe endpoint.",
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
                else:
                    model_id = str((item or {}).get("id") or (item or {}).get("name") or "").strip()
                if model_id.startswith("models/"):
                    model_id = model_id.split("/", 1)[1]
                if not model_id:
                    continue
                models.append(self.normalize_model(provider, model_id))
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

    def normalize_model(self, provider: Dict[str, Any], model_id: str) -> Dict[str, Any]:
        model = self._model_from_catalog(provider, model_id)
        capabilities = list(model.get("capabilities") or [])
        capability_map = {
            "chat": "chat" in capabilities or True,
            "reasoning": "reasoning" in capabilities,
            "toolCalling": "tools" in capabilities,
            "vision": "vision" in capabilities,
            "streaming": "streaming" in capabilities or True,
            "embedding": "embedding" in capabilities,
            "rerank": "rerank" in capabilities,
        }
        return {
            "id": model_id,
            "modelId": model_id,
            "modelRef": make_model_ref(str(provider.get("id") or ""), model_id),
            "type": "MULTIMODAL" if capability_map.get("vision") else "TEXT",
            "contextWindow": model.get("contextWindow"),
            "maxTokens": model.get("maxOutputTokens") or model.get("maxTokens"),
            "temperature": provider.get("recommendedTemperature"),
            "capabilities": capability_map,
            "capabilityTags": capabilities,
        }


model_provider_catalog = ModelProviderCatalog()
