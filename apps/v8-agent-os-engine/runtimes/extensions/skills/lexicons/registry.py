from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable


_DEFAULT_REQUIRED_LOCALES = ("en", "zh-CN")
_MARKET_DIRNAME = "market"
_MANIFEST_FILENAME = "manifest.json"
_SEQUENCE_MAP_KEYS = {
    "querySynonyms",
    "artifactIntentSynonyms",
    "operationIntentSynonyms",
    "primaryThemeSynonyms",
    "secondaryThemeSynonyms",
    "secondaryThemePrimaryMap",
}
_WEIGHTED_MAP_KEYS = {
    "documentSubIntentSynonyms",
    "skillDocumentSubIntentSynonyms",
}
_ANCHOR_MAP_KEYS = {"artifactProfileAnchors"}
_FAMILY_MAP_KEYS = {
    "canonicalFamilies",
    "canonicalFamilyParents",
}
_SEQUENCE_KEYS = {
    "pluginHostQueryHints",
    "crossRuntimeEscapeTokens",
    "metaAdvisoryHints",
    "familyAdvisoryHints",
    "integrationOrToolingHints",
}
_TOKEN_KEY_RE = re.compile(r"^[a-z0-9_.-]+$|^[\u4e00-\u9fff]{2,}$")


def _normalize_text(text: Any) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _unique_preserve_order(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        normalized = _normalize_text(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return tuple(ordered)


def _unique_preserve_value(
    items: list[str],
    *,
    identity: Callable[[str], str] | None = None,
) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        marker = str(identity(text) if identity is not None else text)
        if not marker or marker in seen:
            continue
        seen.add(marker)
        ordered.append(text)
    return tuple(ordered)


def _unique_locale_labels(items: list[str]) -> tuple[str, ...]:
    return _unique_preserve_value(items, identity=lambda value: value.lower())


def _normalize_string_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return _unique_preserve_order([value])
    if not isinstance(value, list):
        return ()
    return _unique_preserve_order([str(item) for item in value])


def _normalize_sequence_map(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, tuple[str, ...]] = {}
    for key, items in value.items():
        key_name = _normalize_text(key)
        if not key_name:
            continue
        sequence = _normalize_string_list(items)
        if sequence:
            normalized[key_name] = sequence
    return normalized


def _normalize_weighted_map(value: Any) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, int]] = {}
    for group, rules in value.items():
        group_name = _normalize_text(group)
        if not group_name or not isinstance(rules, dict):
            continue
        normalized_rules: dict[str, int] = {}
        for synonym, raw_weight in rules.items():
            synonym_text = _normalize_text(synonym)
            if not synonym_text:
                continue
            try:
                normalized_rules[synonym_text] = int(raw_weight)
            except Exception:
                continue
        if normalized_rules:
            normalized[group_name] = normalized_rules
    return normalized


def _normalize_anchor_map(value: Any) -> dict[str, set[str]]:
    normalized_map = _normalize_sequence_map(value)
    return {key: set(items) for key, items in normalized_map.items()}


def _is_exact_token(term: str) -> bool:
    return bool(_TOKEN_KEY_RE.fullmatch(term))


def _merge_sequence_maps(target: dict[str, tuple[str, ...]], incoming: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    merged = dict(target)
    for key, items in incoming.items():
        merged[key] = _unique_preserve_order([*list(merged.get(key) or ()), *list(items or ())])
    return merged


def _merge_weighted_maps(target: dict[str, dict[str, int]], incoming: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    merged = {key: dict(value) for key, value in target.items()}
    for key, rules in incoming.items():
        bucket = merged.setdefault(key, {})
        for synonym, weight in rules.items():
            current = bucket.get(synonym)
            bucket[synonym] = max(int(weight), int(current)) if current is not None else int(weight)
    return merged


def _merge_anchor_maps(target: dict[str, set[str]], incoming: dict[str, set[str]]) -> dict[str, set[str]]:
    merged = {key: set(value) for key, value in target.items()}
    for key, items in incoming.items():
        merged.setdefault(key, set()).update(items)
    return merged


def _build_exact_index(sequence_map: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    return {
        key: tuple(values)
        for key, values in sequence_map.items()
        if _is_exact_token(key)
    }


def _build_phrase_index(sequence_map: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    return {
        key: tuple(values)
        for key, values in sequence_map.items()
        if not _is_exact_token(key)
    }


class ExtensionLexiconRegistry:
    def __init__(self, root_dir: Path | None = None, *, required_locales: tuple[str, ...] = _DEFAULT_REQUIRED_LOCALES) -> None:
        self._root_dir = Path(root_dir or Path(__file__).resolve().parent)
        self._market_root_dir = self._root_dir / _MARKET_DIRNAME
        self._required_locales = tuple(required_locales)
        self._manifest_signature = ""
        self._snapshot: dict[str, Any] = self._empty_snapshot()

    def _empty_layer(self) -> dict[str, Any]:
        return {
            "signature": "lexicon:empty",
            "locales": [],
            "loadErrors": [],
            "querySynonyms": {},
            "artifactIntentSynonyms": {},
            "operationIntentSynonyms": {},
            "primaryThemeSynonyms": {},
            "secondaryThemeSynonyms": {},
            "secondaryThemePrimaryMap": {},
            "documentSubIntentSynonyms": {},
            "skillDocumentSubIntentSynonyms": {},
            "artifactProfileAnchors": {},
            "pluginHostQueryHints": (),
            "crossRuntimeEscapeTokens": (),
            "metaAdvisoryHints": (),
            "familyAdvisoryHints": (),
            "integrationOrToolingHints": (),
            "querySynonymsExact": {},
            "querySynonymsPhrase": {},
        }

    def _empty_snapshot(self) -> dict[str, Any]:
        core_layer = self._empty_layer()
        market_layer = self._empty_layer()
        return {
            **core_layer,
            "coreSignature": core_layer["signature"],
            "coreLocales": [],
            "coreLoadErrors": [],
            "marketSignature": market_layer["signature"],
            "marketEnabled": False,
            "marketLocales": [],
            "marketLoadErrors": [],
            "marketProviders": [],
            "market": market_layer,
        }

    def _manifest(self) -> list[tuple[str, int, int]]:
        manifest: list[tuple[str, int, int]] = []
        if not self._root_dir.exists():
            return manifest
        for path in sorted(self._root_dir.glob("*.json")):
            if path.name == _MANIFEST_FILENAME:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            manifest.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
        return manifest

    def _market_manifest(self) -> list[tuple[str, int, int]]:
        manifest: list[tuple[str, int, int]] = []
        if not self._market_root_dir.exists():
            return manifest
        for provider_dir in sorted(path for path in self._market_root_dir.iterdir() if path.is_dir()):
            for path in sorted(provider_dir.glob("*.json")):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                relative_name = str(path.relative_to(self._root_dir)).replace("\\", "/")
                manifest.append((relative_name, int(stat.st_mtime_ns), int(stat.st_size)))
        return manifest

    def _manifest_signature_for(self, manifest: list[tuple[str, int, int]], *, prefix: str) -> str:
        if not manifest:
            return f"{prefix}:empty"
        digest = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        return f"{prefix}:{digest[:16]}"

    def _load_locale_file(self, path: Path) -> tuple[str | None, dict[str, Any] | None, str | None]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return None, None, f"{path.name}: {exc}"
        if not isinstance(payload, dict):
            return None, None, f"{path.name}: top-level payload must be an object"
        locale = str(payload.get("locale") or path.stem).strip()
        if not locale:
            return None, None, f"{path.name}: missing locale"
        return locale, payload, None

    def _copy_layer(self, layer: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "signature": str(layer.get("signature") or "lexicon:empty"),
            "locales": list(layer.get("locales") or []),
            "loadErrors": list(layer.get("loadErrors") or []),
        }
        for key in _SEQUENCE_MAP_KEYS:
            payload[key] = {name: tuple(values) for name, values in dict(layer.get(key) or {}).items()}
        for key in _WEIGHTED_MAP_KEYS:
            payload[key] = {
                name: {synonym: int(weight) for synonym, weight in dict(values).items()}
                for name, values in dict(layer.get(key) or {}).items()
            }
        for key in _FAMILY_MAP_KEYS:
            payload[key] = {name: tuple(values) for name, values in dict(layer.get(key) or {}).items()}
        payload["artifactProfileAnchors"] = {
            name: set(values) for name, values in dict(layer.get("artifactProfileAnchors") or {}).items()
        }
        for key in _SEQUENCE_KEYS:
            payload[key] = tuple(layer.get(key) or ())
        payload["querySynonymsExact"] = {
            key: tuple(values) for key, values in dict(layer.get("querySynonymsExact") or {}).items()
        }
        payload["querySynonymsPhrase"] = {
            key: tuple(values) for key, values in dict(layer.get("querySynonymsPhrase") or {}).items()
        }
        return payload

    def _build_layer(
        self,
        *,
        locale_files: dict[str, Path],
        required_locales: tuple[str, ...],
        signature: str,
        missing_prefix: str = "",
    ) -> dict[str, Any]:
        merged_sequence_maps = {key: {} for key in _SEQUENCE_MAP_KEYS}
        merged_weighted_maps = {key: {} for key in _WEIGHTED_MAP_KEYS}
        merged_anchor_maps = {key: {} for key in _ANCHOR_MAP_KEYS}
        merged_family_maps = {key: {} for key in _FAMILY_MAP_KEYS}
        merged_sequences = {key: () for key in _SEQUENCE_KEYS}
        locales: list[str] = []
        load_errors: list[str] = []

        ordered_paths = [locale_files[locale] for locale in required_locales if locale in locale_files]
        ordered_paths.extend(path for stem, path in locale_files.items() if stem not in required_locales)

        for required_locale in required_locales:
            if required_locale not in locale_files:
                label = f"{missing_prefix}{required_locale}.json" if missing_prefix else f"{required_locale}.json"
                load_errors.append(f"{label}: missing required locale file")

        for path in ordered_paths:
            locale, payload, error = self._load_locale_file(path)
            if error:
                label = f"{missing_prefix}{error}" if missing_prefix else error
                load_errors.append(label)
                continue
            assert payload is not None
            locales.append(locale or path.stem)

            for key in _SEQUENCE_MAP_KEYS:
                merged_sequence_maps[key] = _merge_sequence_maps(
                    merged_sequence_maps[key],
                    _normalize_sequence_map(payload.get(key)),
                )
            for key in _WEIGHTED_MAP_KEYS:
                merged_weighted_maps[key] = _merge_weighted_maps(
                    merged_weighted_maps[key],
                    _normalize_weighted_map(payload.get(key)),
                )
            for key in _ANCHOR_MAP_KEYS:
                merged_anchor_maps[key] = _merge_anchor_maps(
                    merged_anchor_maps[key],
                    _normalize_anchor_map(payload.get(key)),
                )
            for key in _FAMILY_MAP_KEYS:
                merged_family_maps[key] = _merge_sequence_maps(
                    merged_family_maps[key],
                    _normalize_sequence_map(payload.get(key)),
                )
            for key in _SEQUENCE_KEYS:
                merged_sequences[key] = _unique_preserve_order(
                    [*list(merged_sequences[key]), *list(_normalize_string_list(payload.get(key)))]
                )

        return {
            "signature": signature,
            "locales": locales,
            "loadErrors": load_errors,
            "querySynonyms": merged_sequence_maps["querySynonyms"],
            "artifactIntentSynonyms": merged_sequence_maps["artifactIntentSynonyms"],
            "operationIntentSynonyms": merged_sequence_maps["operationIntentSynonyms"],
            "primaryThemeSynonyms": merged_sequence_maps["primaryThemeSynonyms"],
            "secondaryThemeSynonyms": merged_sequence_maps["secondaryThemeSynonyms"],
            "secondaryThemePrimaryMap": merged_sequence_maps["secondaryThemePrimaryMap"],
            "documentSubIntentSynonyms": merged_weighted_maps["documentSubIntentSynonyms"],
            "skillDocumentSubIntentSynonyms": merged_weighted_maps["skillDocumentSubIntentSynonyms"],
            "artifactProfileAnchors": merged_anchor_maps["artifactProfileAnchors"],
            "canonicalFamilies": merged_family_maps["canonicalFamilies"],
            "canonicalFamilyParents": merged_family_maps["canonicalFamilyParents"],
            "pluginHostQueryHints": merged_sequences["pluginHostQueryHints"],
            "crossRuntimeEscapeTokens": merged_sequences["crossRuntimeEscapeTokens"],
            "metaAdvisoryHints": merged_sequences["metaAdvisoryHints"],
            "familyAdvisoryHints": merged_sequences["familyAdvisoryHints"],
            "integrationOrToolingHints": merged_sequences["integrationOrToolingHints"],
            "querySynonymsExact": _build_exact_index(merged_sequence_maps["querySynonyms"]),
            "querySynonymsPhrase": _build_phrase_index(merged_sequence_maps["querySynonyms"]),
        }

    def _load_market_layer(self, market_signature: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not self._market_root_dir.exists():
            return self._build_layer(locale_files={}, required_locales=(), signature=market_signature), []

        provider_states: list[dict[str, Any]] = []
        provider_descriptors: list[dict[str, Any]] = []
        for provider_dir in sorted(path for path in self._market_root_dir.iterdir() if path.is_dir()):
            locale_files: dict[str, Path] = {}
            for path in sorted(provider_dir.glob("*.json")):
                if path.name == _MANIFEST_FILENAME:
                    continue
                locale, _payload, error = self._load_locale_file(path)
                if error:
                    locale_files[path.stem] = path
                    continue
                locale_key = str(locale or path.stem).strip() or path.stem
                locale_files[locale_key] = path
            required_locales = self._required_locales
            manifest_payload: dict[str, Any] = {}
            manifest_path = provider_dir / _MANIFEST_FILENAME
            if manifest_path.exists():
                _locale, payload, error = self._load_locale_file(manifest_path)
                if error:
                    provider_state = self._build_layer(locale_files={}, required_locales=(), signature=f"market-provider:{provider_dir.name}:invalid")
                    provider_state["loadErrors"] = [f"{provider_dir.name}/{error}"]
                    provider_states.append(provider_state)
                    provider_descriptors.append(
                        {
                            "provider": provider_dir.name,
                            "signature": provider_state["signature"],
                            "locales": [],
                            "loadErrors": list(provider_state["loadErrors"]),
                        }
                    )
                    continue
                manifest_payload = payload or {}
                manifest_locales = manifest_payload.get("locales")
                if isinstance(manifest_locales, list):
                    normalized_locales = tuple(
                        str(item).strip()
                        for item in manifest_locales
                        if str(item).strip()
                    )
                    if normalized_locales:
                        required_locales = normalized_locales
            provider_manifest = []
            for path in sorted(provider_dir.glob("*.json")):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                provider_manifest.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
            provider_signature = self._manifest_signature_for(
                provider_manifest,
                prefix=f"market-provider:{provider_dir.name}",
            )
            provider_state = self._build_layer(
                locale_files=locale_files,
                required_locales=required_locales,
                signature=provider_signature,
                missing_prefix=f"{provider_dir.name}/",
            )
            provider_states.append(provider_state)
            provider_descriptors.append(
                {
                    "provider": provider_dir.name,
                    "signature": provider_signature,
                    "locales": list(provider_state.get("locales") or []),
                    "loadErrors": list(provider_state.get("loadErrors") or []),
                    "manifest": manifest_payload,
                }
            )

        if not provider_states:
            return self._build_layer(locale_files={}, required_locales=(), signature=market_signature), []

        merged_sequence_maps = {key: {} for key in _SEQUENCE_MAP_KEYS}
        merged_weighted_maps = {key: {} for key in _WEIGHTED_MAP_KEYS}
        merged_anchor_maps = {key: {} for key in _ANCHOR_MAP_KEYS}
        merged_sequences = {key: () for key in _SEQUENCE_KEYS}
        locales: list[str] = []
        load_errors: list[str] = []
        for provider_state in provider_states:
            locales.extend(str(item).strip() for item in list(provider_state.get("locales") or []) if str(item).strip())
            load_errors.extend(str(item).strip() for item in list(provider_state.get("loadErrors") or []) if str(item).strip())
            for key in _SEQUENCE_MAP_KEYS:
                merged_sequence_maps[key] = _merge_sequence_maps(
                    merged_sequence_maps[key],
                    dict(provider_state.get(key) or {}),
                )
            for key in _WEIGHTED_MAP_KEYS:
                merged_weighted_maps[key] = _merge_weighted_maps(
                    merged_weighted_maps[key],
                    dict(provider_state.get(key) or {}),
                )
            merged_anchor_maps["artifactProfileAnchors"] = _merge_anchor_maps(
                merged_anchor_maps["artifactProfileAnchors"],
                dict(provider_state.get("artifactProfileAnchors") or {}),
            )
            for key in _SEQUENCE_KEYS:
                merged_sequences[key] = _unique_preserve_order(
                    [*list(merged_sequences[key]), *list(provider_state.get(key) or ())]
                )

        return {
            "signature": market_signature,
            "locales": list(_unique_locale_labels(locales)),
            "loadErrors": list(_unique_preserve_value(load_errors)),
            "querySynonyms": merged_sequence_maps["querySynonyms"],
            "artifactIntentSynonyms": merged_sequence_maps["artifactIntentSynonyms"],
            "operationIntentSynonyms": merged_sequence_maps["operationIntentSynonyms"],
            "primaryThemeSynonyms": merged_sequence_maps["primaryThemeSynonyms"],
            "secondaryThemeSynonyms": merged_sequence_maps["secondaryThemeSynonyms"],
            "secondaryThemePrimaryMap": merged_sequence_maps["secondaryThemePrimaryMap"],
            "documentSubIntentSynonyms": merged_weighted_maps["documentSubIntentSynonyms"],
            "skillDocumentSubIntentSynonyms": merged_weighted_maps["skillDocumentSubIntentSynonyms"],
            "artifactProfileAnchors": merged_anchor_maps["artifactProfileAnchors"],
            "pluginHostQueryHints": merged_sequences["pluginHostQueryHints"],
            "crossRuntimeEscapeTokens": merged_sequences["crossRuntimeEscapeTokens"],
            "metaAdvisoryHints": merged_sequences["metaAdvisoryHints"],
            "familyAdvisoryHints": merged_sequences["familyAdvisoryHints"],
            "integrationOrToolingHints": merged_sequences["integrationOrToolingHints"],
            "querySynonymsExact": _build_exact_index(merged_sequence_maps["querySynonyms"]),
            "querySynonymsPhrase": _build_phrase_index(merged_sequence_maps["querySynonyms"]),
        }, provider_descriptors

    def _reload(
        self,
        *,
        core_manifest: list[tuple[str, int, int]],
        market_manifest: list[tuple[str, int, int]],
        core_signature: str,
        market_signature: str,
        signature: str,
    ) -> None:
        core_locale_files = {
            path.stem: path
            for path in sorted(self._root_dir.glob("*.json"))
            if path.name != _MANIFEST_FILENAME
        }
        core_layer = self._build_layer(
            locale_files=core_locale_files,
            required_locales=self._required_locales,
            signature=core_signature,
        )
        market_layer, market_providers = self._load_market_layer(market_signature)

        self._manifest_signature = signature
        self._snapshot = {
            **core_layer,
            "signature": signature,
            "coreSignature": core_signature,
            "coreLocales": list(core_layer.get("locales") or []),
            "coreLoadErrors": list(core_layer.get("loadErrors") or []),
            "marketSignature": market_signature,
            "marketEnabled": bool(market_manifest),
            "marketLocales": list(market_layer.get("locales") or []),
            "marketLoadErrors": list(market_layer.get("loadErrors") or []),
            "marketProviders": market_providers,
            "market": market_layer,
        }

    def ensure_fresh(self) -> dict[str, Any]:
        core_manifest = self._manifest()
        market_manifest = self._market_manifest()
        core_signature = self._manifest_signature_for(core_manifest, prefix="lexicon-core")
        market_signature = self._manifest_signature_for(market_manifest, prefix="lexicon-market")
        signature = self._manifest_signature_for(
            [
                ("core", core_signature, len(core_manifest)),
                ("market", market_signature, len(market_manifest)),
            ],
            prefix="lexicon",
        )
        if signature != self._manifest_signature:
            self._reload(
                core_manifest=core_manifest,
                market_manifest=market_manifest,
                core_signature=core_signature,
                market_signature=market_signature,
                signature=signature,
            )
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        payload = self._copy_layer(self._snapshot)
        payload["signature"] = str(self._snapshot.get("signature") or "lexicon:empty")
        payload["coreSignature"] = str(self._snapshot.get("coreSignature") or payload["signature"])
        payload["coreLocales"] = list(self._snapshot.get("coreLocales") or [])
        payload["coreLoadErrors"] = list(self._snapshot.get("coreLoadErrors") or [])
        payload["marketSignature"] = str(self._snapshot.get("marketSignature") or "lexicon-market:empty")
        payload["marketEnabled"] = bool(self._snapshot.get("marketEnabled"))
        payload["marketLocales"] = list(self._snapshot.get("marketLocales") or [])
        payload["marketLoadErrors"] = list(self._snapshot.get("marketLoadErrors") or [])
        payload["marketProviders"] = [
            {
                "provider": str(item.get("provider") or "").strip(),
                "signature": str(item.get("signature") or "").strip(),
                "locales": list(item.get("locales") or []),
                "loadErrors": list(item.get("loadErrors") or []),
                "manifest": dict(item.get("manifest") or {}),
            }
            for item in list(self._snapshot.get("marketProviders") or [])
        ]
        payload["market"] = self._copy_layer(dict(self._snapshot.get("market") or {}))
        return payload


_DEFAULT_REGISTRY: ExtensionLexiconRegistry | None = None


def get_extension_lexicon_registry(root_dir: Path | None = None) -> ExtensionLexiconRegistry:
    global _DEFAULT_REGISTRY
    if root_dir is not None:
        return ExtensionLexiconRegistry(root_dir=Path(root_dir))
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ExtensionLexiconRegistry()
    return _DEFAULT_REGISTRY
