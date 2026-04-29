from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List


_REGISTRY_PATH = Path(__file__).resolve().parent / "model_catalog" / "media_model_capability_registry.json"


def normalize_media_model_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9.+()-]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


class MediaModelCapabilityRegistry:
    def __init__(self, path: Path = _REGISTRY_PATH) -> None:
        self.path = path
        self._cache: Dict[str, Any] | None = None
        self._provider_index: Dict[str, Dict[str, Any]] | None = None
        self._model_index: Dict[tuple[str, str], Dict[str, Any]] | None = None

    def load(self) -> Dict[str, Any]:
        if self._cache is None:
            if not self.path.exists():
                self._cache = {"schemaVersion": 1, "providers": [], "models": []}
            else:
                with self.path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                self._cache = payload if isinstance(payload, dict) else {"schemaVersion": 1, "providers": [], "models": []}
        return deepcopy(self._cache)

    def _build_provider_index(self) -> Dict[str, Dict[str, Any]]:
        payload = self.load()
        index: Dict[str, Dict[str, Any]] = {}
        for item in payload.get("providers") or []:
            if not isinstance(item, dict):
                continue
            provider_id = str(item.get("providerId") or item.get("id") or "").strip()
            if provider_id:
                index[provider_id] = item
        return index

    def _build_model_index(self) -> Dict[tuple[str, str], Dict[str, Any]]:
        payload = self.load()
        index: Dict[tuple[str, str], Dict[str, Any]] = {}
        for item in payload.get("models") or []:
            if not isinstance(item, dict):
                continue
            keys: List[str] = [str(item.get("canonicalModelId") or "")]
            aliases = item.get("aliases")
            if isinstance(aliases, list):
                keys.extend(str(alias or "") for alias in aliases)
            provider_ids = item.get("providerIds") if isinstance(item.get("providerIds"), list) else []
            for provider_id in provider_ids:
                provider = str(provider_id or "").strip()
                if not provider:
                    continue
                for key in keys:
                    normalized = normalize_media_model_key(key)
                    if normalized and (provider, normalized) not in index:
                        index[(provider, normalized)] = item
        return index

    def provider(self, provider_id: str) -> Dict[str, Any] | None:
        if self._provider_index is None:
            self._provider_index = self._build_provider_index()
        match = self._provider_index.get(str(provider_id or "").strip())
        return deepcopy(match) if match else None

    def find(self, provider_id: str, model_id: str, operation_kind: str | None = None) -> Dict[str, Any] | None:
        if self._model_index is None:
            self._model_index = self._build_model_index()
        provider = str(provider_id or "").strip()
        model = normalize_media_model_key(model_id)
        match = self._model_index.get((provider, model))
        if not match:
            return None
        operation = str(operation_kind or "").strip()
        if operation:
            operation_kinds = {str(value).strip() for value in match.get("operationKinds") or []}
            if operation_kinds and operation not in operation_kinds:
                return None
        return deepcopy(match)

    def models_for_provider(self, provider_id: str) -> List[Dict[str, Any]]:
        provider = str(provider_id or "").strip()
        payload = self.load()
        matches = []
        for item in payload.get("models") or []:
            if not isinstance(item, dict):
                continue
            provider_ids = {str(value or "").strip() for value in item.get("providerIds") or []}
            if provider in provider_ids:
                matches.append(item)
        return deepcopy(matches)


media_model_capability_registry = MediaModelCapabilityRegistry()
