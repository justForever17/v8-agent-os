from __future__ import annotations

import asyncio
import base64
import copy
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import runtimes.plugin_manager.catalog as catalog_module
from runtimes.plugin_manager.catalog import PluginCatalogService, canonical_catalog_bytes


class _CatalogStorage:
    def get_plugin_manager_config(self) -> dict:
        return {
            "catalogUrl": "https://catalog.example/catalog.json",
            "catalogSignatureUrl": "https://catalog.example/catalog.sig",
        }


class _Response:
    def __init__(self, payload=None, text: str = "") -> None:
        self._payload = payload
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return copy.deepcopy(self._payload)


def _signed_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    payload = json.loads(catalog_module.BUILTIN_CATALOG_PATH.read_text(encoding="utf-8"))
    private = Ed25519PrivateKey.generate()
    public_path = tmp_path / "public-key.txt"
    builtin_path = tmp_path / "builtin.json"
    builtin_sig_path = tmp_path / "builtin.sig"
    cache_path = tmp_path / "cache" / "catalog.json"
    cache_sig_path = tmp_path / "cache" / "catalog.sig"
    cache_bundle_path = tmp_path / "cache" / "catalog.bundle.json"
    public_path.write_text(
        base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii"),
        encoding="utf-8",
    )
    builtin_path.write_text(json.dumps(payload), encoding="utf-8")
    builtin_sig_path.write_text(
        base64.b64encode(private.sign(canonical_catalog_bytes(payload))).decode("ascii"),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_module, "PUBLIC_KEY_PATH", public_path)
    monkeypatch.setattr(catalog_module, "BUILTIN_CATALOG_PATH", builtin_path)
    monkeypatch.setattr(catalog_module, "BUILTIN_SIGNATURE_PATH", builtin_sig_path)
    monkeypatch.setattr(catalog_module, "CACHE_CATALOG_PATH", cache_path)
    monkeypatch.setattr(catalog_module, "CACHE_SIGNATURE_PATH", cache_sig_path)
    monkeypatch.setattr(catalog_module, "CACHE_BUNDLE_PATH", cache_bundle_path)
    monkeypatch.setattr(catalog_module, "PLUGIN_MANAGER_CACHE_ROOT", cache_path.parent)
    monkeypatch.setattr(catalog_module, "storage", _CatalogStorage())
    return payload, private


def test_corrupt_trusted_cache_falls_back_to_signed_builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload, _ = _signed_fixture(tmp_path, monkeypatch)
    catalog_module.CACHE_CATALOG_PATH.parent.mkdir(parents=True)
    catalog_module.CACHE_CATALOG_PATH.write_text(json.dumps(payload), encoding="utf-8")
    catalog_module.CACHE_SIGNATURE_PATH.write_text(base64.b64encode(b"x" * 64).decode("ascii"), encoding="utf-8")

    service = PluginCatalogService()
    assert service.load().revision == payload["revision"]
    snapshot = service.snapshot()
    assert snapshot["source"] == "builtin"
    assert "trusted cache rejected" in (snapshot["error"] or "")


@pytest.mark.parametrize("failure", ["bad_signature", "old_revision", "bad_schema", "offline"])
def test_remote_catalog_failures_keep_current_trusted_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    current, private = _signed_fixture(tmp_path, monkeypatch)
    candidate = copy.deepcopy(current)
    if failure == "old_revision":
        candidate["revision"] = current["revision"] - 1
    elif failure == "bad_schema":
        candidate["schemaVersion"] = "invalid"
    signature = private.sign(canonical_catalog_bytes(candidate))
    if failure == "bad_signature":
        signature = b"x" * 64

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            if failure == "offline":
                raise OSError("synthetic offline")
            if url.endswith(".sig"):
                return _Response(text=base64.b64encode(signature).decode("ascii"))
            return _Response(payload=candidate)

    monkeypatch.setattr(catalog_module.httpx, "AsyncClient", _Client)
    service = PluginCatalogService()
    assert service.load().revision == current["revision"]
    result = __import__("asyncio").run(service.refresh())
    assert result["status"] == "fallback"
    assert result["revision"] == current["revision"]
    assert result["source"] == "builtin"


def test_remote_catalog_accepts_signed_monotonic_unexpired_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, private = _signed_fixture(tmp_path, monkeypatch)
    candidate = copy.deepcopy(current)
    candidate["revision"] = current["revision"] + 1
    candidate["sequence"] = candidate["revision"]
    candidate["keyId"] = current["keyId"]
    candidate["expiresAt"] = "2099-01-01T00:00:00Z"
    candidate["revocations"] = []
    signature = private.sign(canonical_catalog_bytes(candidate))

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            if url.endswith(".sig"):
                return _Response(text=base64.b64encode(signature).decode("ascii"))
            return _Response(payload=candidate)

    monkeypatch.setattr(catalog_module.httpx, "AsyncClient", _Client)
    service = PluginCatalogService()
    result = __import__("asyncio").run(service.refresh())
    assert result["status"] == "updated"
    assert result["revision"] == candidate["revision"]
    assert result["source"] == "remote_signed"
    bundle = json.loads(catalog_module.CACHE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert bundle["payload"]["revision"] == candidate["revision"]
    catalog_module.verify_signature(bundle["payload"], bundle["signature"])


def test_remote_catalog_rejects_signed_payload_with_unrecognized_key_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, private = _signed_fixture(tmp_path, monkeypatch)
    candidate = copy.deepcopy(current)
    candidate["revision"] += 1
    candidate["sequence"] += 1
    candidate["keyId"] = "different-key-id"
    candidate["expiresAt"] = "2099-01-01T00:00:00Z"
    signature = private.sign(canonical_catalog_bytes(candidate))

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            if url.endswith(".sig"):
                return _Response(text=base64.b64encode(signature).decode("ascii"))
            return _Response(payload=candidate)

    monkeypatch.setattr(catalog_module.httpx, "AsyncClient", _Client)
    service = PluginCatalogService()
    result = __import__("asyncio").run(service.refresh())
    assert result["status"] == "fallback"
    assert "keyId" in (result["error"] or "")


def test_expired_signed_cache_is_not_activated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, private = _signed_fixture(tmp_path, monkeypatch)
    expired = copy.deepcopy(current)
    expired["revision"] += 1
    expired["sequence"] += 1
    expired["expiresAt"] = "2000-01-01T00:00:00Z"
    catalog_module.CACHE_CATALOG_PATH.parent.mkdir(parents=True)
    catalog_module.CACHE_CATALOG_PATH.write_text(json.dumps(expired), encoding="utf-8")
    catalog_module.CACHE_SIGNATURE_PATH.write_text(
        base64.b64encode(private.sign(canonical_catalog_bytes(expired))).decode("ascii"),
        encoding="utf-8",
    )

    service = PluginCatalogService()
    assert service.load().revision == current["revision"]
    snapshot = service.snapshot()
    assert snapshot["source"] == "builtin"
    assert "expired" in (snapshot["error"] or "")


def test_active_catalog_expiry_is_rechecked_without_process_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, _ = _signed_fixture(tmp_path, monkeypatch)
    expired = copy.deepcopy(current)
    expired["revision"] += 1
    expired["sequence"] += 1
    expired["expiresAt"] = "2000-01-01T00:00:00Z"
    service = PluginCatalogService()
    service._active = catalog_module.validate_catalog(expired)
    service._source = "remote_signed"

    loaded = service.load()
    assert loaded.revision == current["revision"]
    assert service.snapshot()["source"] == "builtin"


def test_same_sequence_cannot_replace_catalog_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, private = _signed_fixture(tmp_path, monkeypatch)
    candidate = copy.deepcopy(current)
    candidate["revision"] += 1
    candidate["sequence"] = current["sequence"]
    candidate["expiresAt"] = "2099-01-01T00:00:00Z"
    candidate["plugins"][0]["displayName"] += " changed"
    signature = private.sign(canonical_catalog_bytes(candidate))

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            if url.endswith(".sig"):
                return _Response(text=base64.b64encode(signature).decode("ascii"))
            return _Response(payload=candidate)

    monkeypatch.setattr(catalog_module.httpx, "AsyncClient", _Client)
    service = PluginCatalogService()
    result = __import__("asyncio").run(service.refresh())
    assert result["status"] == "fallback"
    assert "sequence" in (result["error"] or "")


def test_failed_cache_publish_keeps_previous_atomic_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, private = _signed_fixture(tmp_path, monkeypatch)
    current_signature = base64.b64encode(private.sign(canonical_catalog_bytes(current))).decode("ascii")
    catalog_module.CACHE_BUNDLE_PATH.parent.mkdir(parents=True)
    catalog_module.CACHE_BUNDLE_PATH.write_text(
        json.dumps({"payload": current, "signature": current_signature}),
        encoding="utf-8",
    )
    before = catalog_module.CACHE_BUNDLE_PATH.read_bytes()

    candidate = copy.deepcopy(current)
    candidate["revision"] += 1
    candidate["sequence"] += 1
    candidate["expiresAt"] = "2099-01-01T00:00:00Z"
    signature = private.sign(canonical_catalog_bytes(candidate))

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            if url.endswith(".sig"):
                return _Response(text=base64.b64encode(signature).decode("ascii"))
            return _Response(payload=candidate)

    monkeypatch.setattr(catalog_module.httpx, "AsyncClient", _Client)
    service = PluginCatalogService()
    assert service.load().revision == current["revision"]
    monkeypatch.setattr(catalog_module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("synthetic publish failure")))
    result = __import__("asyncio").run(service.refresh())
    assert result["status"] == "fallback"
    assert catalog_module.CACHE_BUNDLE_PATH.read_bytes() == before
    assert not list(catalog_module.PLUGIN_MANAGER_CACHE_ROOT.glob("catalog.bundle.*.tmp"))


def test_concurrent_refresh_cannot_publish_a_lower_sequence_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, private = _signed_fixture(tmp_path, monkeypatch)
    candidates = []
    for increment in (1, 2):
        candidate = copy.deepcopy(current)
        candidate["revision"] += increment
        candidate["sequence"] += increment
        candidate["expiresAt"] = "2099-01-01T00:00:00Z"
        candidates.append(candidate)
    signatures = [
        base64.b64encode(private.sign(canonical_catalog_bytes(candidate))).decode("ascii")
        for candidate in candidates
    ]
    client_count = 0

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            nonlocal client_count
            self.index = client_count
            client_count += 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            if self.index == 0:
                await asyncio.sleep(0.05)
            if url.endswith(".sig"):
                return _Response(text=signatures[self.index])
            return _Response(payload=candidates[self.index])

    monkeypatch.setattr(catalog_module.httpx, "AsyncClient", _Client)
    service = PluginCatalogService()

    async def refresh_twice():
        return await asyncio.gather(service.refresh(), service.refresh())

    results = asyncio.run(refresh_twice())
    assert [item["status"] for item in results] == ["updated", "updated"]
    assert service.load().sequence == current["sequence"] + 2
    bundle = json.loads(catalog_module.CACHE_BUNDLE_PATH.read_text(encoding="utf-8"))
    assert bundle["payload"]["sequence"] == current["sequence"] + 2
