from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.storage import storage
from core.v8_agent_os_paths import PLUGIN_MANAGER_CACHE_ROOT

from .schema import PluginCatalog


RESOURCE_ROOT = Path(__file__).resolve().parent / "resources"
BUILTIN_CATALOG_PATH = RESOURCE_ROOT / "catalog.json"
BUILTIN_SIGNATURE_PATH = RESOURCE_ROOT / "catalog.sig"
PUBLIC_KEY_PATH = RESOURCE_ROOT / "catalog-public-key.txt"
CACHE_CATALOG_PATH = PLUGIN_MANAGER_CACHE_ROOT / "catalog.json"
CACHE_SIGNATURE_PATH = PLUGIN_MANAGER_CACHE_ROOT / "catalog.sig"
CACHE_BUNDLE_PATH = PLUGIN_MANAGER_CACHE_ROOT / "catalog.bundle.json"


def canonical_catalog_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def catalog_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_catalog_bytes(payload)).hexdigest()


def validate_catalog(payload: dict[str, Any]) -> PluginCatalog:
    return PluginCatalog.model_validate(payload)


def verify_signature(payload: dict[str, Any], signature_text: str) -> None:
    public_key_raw = base64.b64decode(PUBLIC_KEY_PATH.read_text(encoding="utf-8").strip())
    signature_raw = base64.b64decode(str(signature_text or "").strip())
    Ed25519PublicKey.from_public_bytes(public_key_raw).verify(signature_raw, canonical_catalog_bytes(payload))


def validate_catalog_expiry(catalog: PluginCatalog, *, label: str) -> None:
    if not catalog.expiresAt:
        raise ValueError(f"{label} catalog expiresAt is required")
    expires = datetime.fromisoformat(str(catalog.expiresAt).replace("Z", "+00:00"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        raise ValueError(f"{label} catalog is expired")


def validate_remote_catalog_metadata(candidate: PluginCatalog, current: PluginCatalog) -> None:
    if not str(candidate.keyId or "").strip():
        raise ValueError("remote catalog keyId is required")
    if str(candidate.keyId).strip() != str(current.keyId or "").strip():
        raise ValueError(
            f"remote catalog keyId mismatch: {candidate.keyId!s} != {current.keyId!s}"
        )
    sequence = candidate.sequence if candidate.sequence is not None else candidate.revision
    current_sequence = current.sequence if current.sequence is not None else current.revision
    if sequence < current_sequence:
        raise ValueError(f"catalog sequence rollback rejected: {sequence} < {current_sequence}")
    if candidate.revision < current.revision:
        raise ValueError(
            f"catalog revision rollback rejected: {candidate.revision} < {current.revision}"
        )
    if sequence == current_sequence and catalog_sha256(
        candidate.model_dump(mode="json")
    ) != catalog_sha256(current.model_dump(mode="json")):
        raise ValueError("catalog sequence must increase when signed catalog contents change")
    validate_catalog_expiry(candidate, label="remote")
    revoked = {str(item).strip().lower() for item in candidate.revocations}
    if any(plugin.id in revoked for plugin in candidate.plugins):
        raise ValueError("remote catalog contains a revoked plugin manifest")


def validate_builtin_catalog_metadata(catalog: PluginCatalog) -> None:
    if not str(catalog.keyId or "").strip():
        raise ValueError("builtin catalog keyId is required")
    validate_catalog_expiry(catalog, label="builtin")


def _atomic_write_cache_bundle(payload: dict[str, Any], signature: str) -> None:
    PLUGIN_MANAGER_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    bundle = json.dumps(
        {"payload": payload, "signature": str(signature or "").strip()},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="catalog.bundle.",
        suffix=".tmp",
        dir=str(PLUGIN_MANAGER_CACHE_ROOT),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(bundle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, CACHE_BUNDLE_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)


class PluginCatalogService:
    def __init__(self) -> None:
        self._active: PluginCatalog | None = None
        self._source = "builtin"
        self._last_error = ""
        self._refresh_lock = asyncio.Lock()

    def _read_catalog(self, path: Path) -> PluginCatalog:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return validate_catalog(payload)

    def load(self) -> PluginCatalog:
        if self._active is not None:
            try:
                validate_catalog_expiry(self._active, label="active")
                return self._active
            except ValueError as exc:
                self._last_error = str(exc)
                self._active = None
                self._source = "builtin"
        builtin_payload = json.loads(BUILTIN_CATALOG_PATH.read_text(encoding="utf-8"))
        verify_signature(builtin_payload, BUILTIN_SIGNATURE_PATH.read_text(encoding="utf-8"))
        builtin = validate_catalog(builtin_payload)
        validate_builtin_catalog_metadata(builtin)
        cache_available = CACHE_BUNDLE_PATH.exists() or (
            CACHE_CATALOG_PATH.exists() and CACHE_SIGNATURE_PATH.exists()
        )
        if cache_available:
            try:
                if CACHE_BUNDLE_PATH.exists():
                    bundle = json.loads(CACHE_BUNDLE_PATH.read_text(encoding="utf-8"))
                    payload = bundle.get("payload")
                    signature = bundle.get("signature")
                    if not isinstance(payload, dict) or not isinstance(signature, str):
                        raise ValueError("trusted cache bundle is malformed")
                else:
                    payload = json.loads(CACHE_CATALOG_PATH.read_text(encoding="utf-8"))
                    signature = CACHE_SIGNATURE_PATH.read_text(encoding="utf-8")
                verify_signature(payload, signature)
                cached = validate_catalog(payload)
                validate_remote_catalog_metadata(cached, builtin)
                self._active = cached
                self._source = "trusted_cache"
                return cached
            except Exception as exc:
                self._last_error = f"trusted cache rejected: {type(exc).__name__}: {exc}"
        self._active = builtin
        self._source = "builtin"
        return builtin

    async def refresh(self) -> dict[str, Any]:
        async with self._refresh_lock:
            return await self._refresh_once()

    async def _refresh_once(self) -> dict[str, Any]:
        current = self.load()
        config = storage.get_plugin_manager_config()
        catalog_url = str(config.get("catalogUrl") or "").strip()
        signature_url = str(config.get("catalogSignatureUrl") or "").strip()
        if not catalog_url or not signature_url:
            return self.snapshot(status="skipped", error="catalog URL is not configured")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                catalog_response, signature_response = await asyncio.gather(
                    client.get(catalog_url),
                    client.get(signature_url),
                )
            catalog_response.raise_for_status()
            signature_response.raise_for_status()
            payload = catalog_response.json()
            signature = signature_response.text.strip()
            verify_signature(payload, signature)
            candidate = validate_catalog(payload)
            validate_remote_catalog_metadata(candidate, current)
            _atomic_write_cache_bundle(payload, signature)
            self._active = candidate
            self._source = "remote_signed"
            self._last_error = ""
            return self.snapshot(status="updated")
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            return self.snapshot(status="fallback", error=self._last_error)

    def get(self, plugin_id: str):
        normalized = str(plugin_id or "").strip().lower()
        return next((item for item in self.load().plugins if item.id == normalized), None)

    def snapshot(self, *, status: str = "ready", error: str = "") -> dict[str, Any]:
        catalog = self.load()
        return {
            "status": status,
            "source": self._source,
            "revision": catalog.revision,
            "count": len(catalog.plugins),
            "sha256": catalog_sha256(catalog.model_dump(mode="json")),
            "error": error or self._last_error or None,
        }


plugin_catalog_service = PluginCatalogService()
