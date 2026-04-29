import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List


_REGISTRY_PATH = Path(__file__).resolve().parent / "model_catalog" / "model_capability_registry.json"


def normalize_model_capability_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9.+()-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


class ModelCapabilityRegistry:
    def __init__(self, path: Path = _REGISTRY_PATH) -> None:
        self.path = path
        self._cache: Dict[str, Any] | None = None
        self._alias_index: Dict[str, Dict[str, Any]] | None = None

    def load(self) -> Dict[str, Any]:
        if self._cache is None:
            if not self.path.exists():
                self._cache = {"schemaVersion": 1, "models": []}
            else:
                with self.path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                self._cache = payload if isinstance(payload, dict) else {"schemaVersion": 1, "models": []}
        return deepcopy(self._cache)

    def _build_alias_index(self) -> Dict[str, Dict[str, Any]]:
        index: Dict[str, Dict[str, Any]] = {}
        payload = self.load()
        for item in payload.get("models") or []:
            if not isinstance(item, dict):
                continue
            keys: List[str] = []
            canonical = str(item.get("canonicalModelId") or "")
            display_name = str(item.get("displayName") or "")
            keys.append(canonical)
            if normalize_model_capability_key(display_name) == normalize_model_capability_key(canonical):
                keys.append(display_name)
            aliases = item.get("aliases")
            if isinstance(aliases, list):
                keys.extend(str(alias or "") for alias in aliases)
            for key in keys:
                normalized = normalize_model_capability_key(key)
                if normalized and normalized not in index:
                    index[normalized] = item
        return index

    def find(self, model_id: str) -> Dict[str, Any] | None:
        if self._alias_index is None:
            self._alias_index = self._build_alias_index()
        match = self._alias_index.get(normalize_model_capability_key(model_id))
        return deepcopy(match) if match else None


model_capability_registry = ModelCapabilityRegistry()
