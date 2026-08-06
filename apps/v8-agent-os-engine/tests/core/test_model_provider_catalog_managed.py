from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import core.model_provider_catalog as model_provider_catalog_module
from core.model_provider_catalog import ModelProviderCatalog, resolve_probe_target


def _write_catalog(path: Path, providers: list[dict]) -> None:
    path.write_text(
        json.dumps({"version": 1, "providers": providers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _catalog(tmp_path: Path, providers: list[dict]) -> tuple[ModelProviderCatalog, Path, Path, Path]:
    builtin_path = tmp_path / "provider_catalog.json"
    custom_path = tmp_path / "custom.json"
    managed_path = tmp_path / "managed.json"
    _write_catalog(builtin_path, providers)
    return (
        ModelProviderCatalog(
            path=builtin_path,
            custom_path=custom_path,
            managed_path=managed_path,
        ),
        builtin_path,
        custom_path,
        managed_path,
    )


def test_managed_overlay_structurally_merges_provider_and_models(tmp_path: Path) -> None:
    catalog, builtin_path, _, _ = _catalog(
        tmp_path,
        [
            {
                "id": "alpha",
                "name": "Builtin Alpha",
                "baseUrl": "https://api.alpha.example/v1",
                "apiStandard": "openai",
                "auth": {"type": "api_key"},
                "probeStrategy": "openai_models",
                "channels": [
                    {
                        "id": "responses",
                        "baseUrl": "https://api.alpha.example/v1",
                    }
                ],
                "models": [
                    {
                        "id": "model-1",
                        "contextWindow": 128000,
                        "metadata": {"builtin": True},
                    },
                    {"id": "model-2", "contextWindow": 64000},
                ],
            }
        ],
    )
    builtin_before = builtin_path.read_bytes()

    saved = catalog.upsert_managed_provider(
        {
            "id": "alpha",
            "name": "Managed Alpha",
            "models": [
                {
                    "id": "model-1",
                    "maxOutputTokens": 16000,
                    "metadata": {"managed": True},
                },
                {"id": "model-3", "contextWindow": 256000},
            ],
        }
    )
    provider = catalog.get_provider("alpha")

    assert saved["id"] == "alpha"
    assert provider is not None
    assert provider["name"] == "Managed Alpha"
    assert provider["baseUrl"] == "https://api.alpha.example/v1"
    assert provider["isManaged"] is True
    assert provider["isCustom"] is False
    assert [model["id"] for model in provider["models"]] == ["model-1", "model-2", "model-3"]
    assert provider["models"][0]["contextWindow"] == 128000
    assert provider["models"][0]["maxOutputTokens"] == 16000
    assert provider["models"][0]["metadata"] == {"builtin": True, "managed": True}
    assert builtin_path.read_bytes() == builtin_before


def test_custom_provider_wins_over_managed_and_builtin_in_single_view(tmp_path: Path) -> None:
    catalog, _, custom_path, _ = _catalog(
        tmp_path,
        [
            {
                "id": "alpha",
                "name": "Builtin",
                "baseUrl": "https://builtin.example/v1",
                "apiStandard": "openai",
                "auth": {"type": "api_key"},
                "probeStrategy": "openai_models",
                "models": [],
            }
        ],
    )
    catalog.upsert_managed_provider(
        {"id": "alpha", "name": "Managed", "baseUrl": "https://managed.example/v1"}
    )
    _write_catalog(
        custom_path,
        [{"id": "alpha", "name": "Custom", "baseUrl": "http://127.0.0.1:8080/v1", "models": []}],
    )

    providers = [provider for provider in catalog.list_providers() if provider["id"] == "alpha"]

    assert len(providers) == 1
    assert providers[0]["name"] == "Custom"
    assert providers[0]["baseUrl"] == "http://127.0.0.1:8080/v1"
    assert providers[0]["isCustom"] is True
    assert providers[0]["isManaged"] is False


def test_custom_catalog_keeps_last_good_view_during_external_partial_write(tmp_path: Path) -> None:
    catalog, _, custom_path, _ = _catalog(tmp_path, [])
    provider = catalog.build_custom_provider(
        name="Custom",
        base_url="https://custom.example.test/v1",
        provider_id="custom",
    )
    catalog.save_custom_provider(provider)
    assert catalog.get_provider("custom")["isCustom"] is True

    custom_path.write_text("{partial", encoding="utf-8")

    assert catalog.get_provider("custom")["isCustom"] is True


def test_managed_provider_upsert_restore_delete_are_atomic_and_rollbackable(tmp_path: Path) -> None:
    catalog, _, _, managed_path = _catalog(tmp_path, [])

    first = catalog.upsert_managed_provider(
        {
            "id": "alpha",
            "name": "Alpha",
            "baseUrl": "https://api.alpha.example/v1",
            "apiStandard": "openai",
            "auth": {"type": "api_key"},
            "probeStrategy": "openai_models",
            "models": [{"id": "model-1", "contextWindow": 128000}],
        }
    )
    snapshot = catalog.get_managed_provider("alpha")
    assert snapshot == first

    updated = catalog.upsert_managed_provider(
        {"id": "alpha", "models": [{"id": "model-2", "contextWindow": 256000}]}
    )
    assert updated["name"] == "Alpha"
    assert [model["id"] for model in updated["models"]] == ["model-1", "model-2"]

    restored = catalog.restore_managed_provider("alpha", snapshot)
    assert restored == snapshot
    assert catalog.get_managed_provider("alpha") == snapshot
    assert catalog.delete_managed_provider("alpha") is True
    assert catalog.delete_managed_provider("alpha") is False
    assert catalog.load_managed()["providers"] == []

    catalog.upsert_managed_provider(first)
    assert catalog.restore_managed_provider("alpha", None) is None
    assert catalog.get_managed_provider("alpha") is None
    assert managed_path.exists()
    assert list(tmp_path.glob(".managed.json.*.tmp")) == []


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "secret",
        "apiKey",
        "access_token",
        "password",
        "cookie",
        "credentialRef",
        "clientSecret",
        "clientToken",
        "privateSecret",
    ],
)
def test_managed_overlay_rejects_sensitive_fields_at_any_depth(
    tmp_path: Path,
    sensitive_key: str,
) -> None:
    catalog, _, _, managed_path = _catalog(tmp_path, [])

    with pytest.raises(ValueError, match="sensitive field"):
        catalog.upsert_managed_provider(
            {
                "id": "alpha",
                "models": [
                    {
                        "id": "model-1",
                        "maxOutputTokens": 8192,
                        "transport": {sensitive_key: "must-not-be-persisted"},
                    }
                ],
            }
        )

    assert not managed_path.exists()


def test_managed_overlay_allows_non_secret_token_metadata(tmp_path: Path) -> None:
    catalog, _, _, _ = _catalog(tmp_path, [])

    saved = catalog.upsert_managed_provider(
        {
            "id": "alpha",
            "name": "Alpha",
            "baseUrl": "https://api.alpha.example/v1",
            "apiStandard": "openai",
            "auth": {"type": "api_key"},
            "probeStrategy": "openai_models",
            "models": [
                {
                    "id": "model-1",
                    "maxTokens": 8192,
                    "maxOutputTokens": 4096,
                    "contextWindowTokens": 32768,
                    "inputPricePerMillionTokens": 1.5,
                }
            ],
        }
    )

    assert saved["models"][0]["maxTokens"] == 8192


@pytest.mark.parametrize(
    "provider_patch,error_match",
    [
        ({"id": "bad id"}, "invalid provider/channel id"),
        ({"id": "alpha", "baseUrl": "ftp://api.example/v1"}, "HTTP\\(S\\) URL"),
        ({"id": "alpha", "models": [{"id": "bad model"}]}, "invalid model id"),
        (
            {"id": "alpha", "models": [{"id": "model-1"}, {"id": "model-1"}]},
            "duplicate id",
        ),
        (
            {"id": "alpha", "channels": [{"id": "responses"}, {"id": "responses"}]},
            "duplicate id",
        ),
    ],
)
def test_managed_overlay_rejects_invalid_identifiers_urls_and_duplicates(
    tmp_path: Path,
    provider_patch: dict,
    error_match: str,
) -> None:
    catalog, _, _, managed_path = _catalog(tmp_path, [])

    with pytest.raises(ValueError, match=error_match):
        catalog.upsert_managed_provider(provider_patch)

    assert not managed_path.exists()


def test_managed_overlay_load_fails_closed_for_duplicate_providers_and_invalid_json(tmp_path: Path) -> None:
    catalog, _, _, managed_path = _catalog(tmp_path, [])
    _write_catalog(managed_path, [{"id": "alpha"}, {"id": "alpha"}])

    with pytest.raises(ValueError, match="duplicate provider id"):
        catalog.load_managed()

    managed_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        catalog.load_managed()


def test_managed_overlay_merges_channels_by_id_without_dropping_siblings(tmp_path: Path) -> None:
    catalog, _, _, _ = _catalog(
        tmp_path,
        [
            {
                "id": "alpha",
                "name": "Alpha",
                "baseUrl": "https://api.alpha.example/v1",
                "apiStandard": "openai",
                "auth": {"type": "api_key"},
                "probeStrategy": "openai_models",
                "channels": [
                    {"id": "chat", "baseUrl": "https://api.alpha.example/v1", "apiStandard": "openai"},
                    {"id": "responses", "baseUrl": "https://api.alpha.example/v1", "apiStandard": "openai"},
                ],
                "models": [],
            }
        ],
    )

    catalog.upsert_managed_provider(
        {
            "id": "alpha",
            "channels": [{"id": "responses", "defaultWireProtocol": "openai.responses"}],
        }
    )
    provider = catalog.get_provider("alpha")

    assert provider is not None
    assert [channel["id"] for channel in provider["channels"]] == ["chat", "responses"]
    assert provider["channels"][1]["baseUrl"] == "https://api.alpha.example/v1"
    assert provider["channels"][1]["defaultWireProtocol"] == "openai.responses"


def test_runtime_catalog_survives_invalid_managed_overlay_with_visible_status(tmp_path: Path) -> None:
    catalog, _, _, managed_path = _catalog(
        tmp_path,
        [{"id": "builtin", "name": "Builtin", "models": []}],
    )
    managed_path.write_text("{broken", encoding="utf-8")

    payload = catalog.load()

    assert payload["providers"][0]["id"] == "builtin"
    assert any(provider["id"] == "builtin" for provider in payload["providers"])
    assert payload["managedCatalogStatus"]["ok"] is False
    assert payload["managedCatalogStatus"]["state"] == "invalid"
    assert "path" not in payload["managedCatalogStatus"]
    assert "backupPath" not in payload["managedCatalogStatus"]
    with pytest.raises(ValueError, match="invalid JSON"):
        catalog.load_managed()


def test_managed_provider_public_validation_allows_existing_partial_patch(tmp_path: Path) -> None:
    catalog, _, _, _ = _catalog(tmp_path, [])
    catalog.upsert_managed_provider(
        {
            "id": "alpha",
            "name": "Alpha",
            "baseUrl": "https://api.alpha.example/v1",
            "apiStandard": "openai",
            "auth": {"type": "api_key"},
            "probeStrategy": "openai_models",
            "models": [{"id": "model-1"}],
        }
    )

    validated = catalog.validate_managed_provider(
        {"id": "alpha", "models": [{"id": "model-2"}]}
    )

    assert validated == {"id": "alpha", "models": [{"id": "model-2"}]}


def test_managed_overlay_rejects_new_incomplete_provider_and_oauth_path_override(tmp_path: Path) -> None:
    catalog, _, _, _ = _catalog(
        tmp_path,
        [
            {
                "id": "trusted-oauth",
                "name": "Trusted OAuth",
                "baseUrl": "https://oauth.example/v1",
                "apiStandard": "openai",
                "auth": {"type": "oauth_file", "path": "trusted/oauth.json"},
                "probeStrategy": "openai_models",
                "models": [],
            }
        ],
    )

    with pytest.raises(ValueError, match="missing required fields"):
        catalog.validate_managed_provider({"id": "new-provider", "name": "New"})
    with pytest.raises(ValueError, match="only allow model metadata"):
        catalog.validate_managed_provider(
            {
                "id": "trusted-oauth",
                "name": "Attempted override",
            }
        )
    with pytest.raises(ValueError, match="cannot change builtin oauth_file transport"):
        catalog.validate_managed_provider(
            {
                "id": "trusted-oauth",
                "auth": {"type": "oauth_file", "path": "other/oauth.json"},
            }
        )
    with pytest.raises(ValueError, match="model transport"):
        catalog.validate_managed_provider(
            {
                "id": "trusted-oauth",
                "models": [{"id": "model-1", "baseUrl": "https://other.example/v1"}],
            }
        )
    with pytest.raises(ValueError, match="cannot introduce or change oauth_file auth"):
        catalog.validate_managed_provider(
            {
                "id": "new-provider",
                "name": "New Provider",
                "baseUrl": "https://api.new.example/v1",
                "apiStandard": "openai",
                "auth": {"type": "api_key"},
                "probeStrategy": "openai_models",
                "channels": [
                    {
                        "id": "stolen-oauth",
                        "baseUrl": "https://collector.example/v1",
                        "apiStandard": "openai",
                        "auth": {"type": "oauth_file", "path": "C:/private/token.json"},
                    }
                ],
            }
        )

    saved = catalog.upsert_managed_provider(
        {
            "id": "trusted-oauth",
            "models": [{"id": "model-1", "contextWindow": 256000}],
        }
    )
    assert saved["models"][0]["contextWindow"] == 256000


def test_managed_overlay_backup_restore_and_default_test_path_isolation(tmp_path: Path) -> None:
    builtin_path = tmp_path / "provider_catalog.json"
    custom_path = tmp_path / "custom.json"
    _write_catalog(builtin_path, [])
    catalog = ModelProviderCatalog(path=builtin_path, custom_path=custom_path)
    assert catalog.managed_path == tmp_path / "model_provider_catalog.managed.json"

    first = catalog.upsert_managed_provider(
        {
            "id": "alpha",
            "name": "Alpha",
            "baseUrl": "https://api.alpha.example/v1",
            "apiStandard": "openai",
            "auth": {"type": "api_key"},
            "probeStrategy": "openai_models",
            "models": [{"id": "model-1"}],
        }
    )
    catalog.upsert_managed_provider({"id": "alpha", "name": "Alpha Updated"})

    restored = catalog.restore_managed_backup()

    assert restored["providers"] == [first]
    assert catalog.get_managed_provider("alpha") == first


def test_normalized_managed_model_keeps_execution_and_reasoning_contracts(tmp_path: Path) -> None:
    catalog, _, _, _ = _catalog(tmp_path, [])
    catalog.upsert_managed_provider(
        {
            "id": "alpha",
            "name": "Alpha",
            "baseUrl": "https://api.alpha.example/v1",
            "apiStandard": "openai",
            "auth": {"type": "api_key"},
            "probeStrategy": "openai_models",
            "models": [
                {
                    "id": "reasoning-model",
                    "type": "TEXT",
                    "contextWindow": 300000,
                    "maxOutputTokens": 32000,
                    "capabilities": ["text", "reasoning"],
                    "reasoningEffortControl": {
                        "supportsReasoningEffort": True,
                        "levels": ["low", "high"],
                        "defaultLevel": "high",
                        "requestStyle": "openai_responses_reasoning_effort",
                    },
                    "operationKinds": ["chat.complete"],
                    "adapter": "openai_responses",
                    "availability": {"status": "stable"},
                    "sourceRefs": ["https://docs.alpha.example/models/reasoning-model"],
                }
            ],
        }
    )
    provider = catalog.get_provider("alpha")

    normalized = catalog.normalize_model(provider or {}, "reasoning-model")

    assert normalized["reasoningEffortControl"]["levels"] == ["auto", "low", "high"]
    assert normalized["operationKinds"] == ["chat.complete"]
    assert normalized["adapter"] == "openai_responses"
    assert normalized["availability"] == {
        "status": "stable",
        "catalogConnectable": True,
    }
    assert normalized["sourceRefs"] == ["https://docs.alpha.example/models/reasoning-model"]


def test_managed_recovery_preserves_and_restores_exact_invalid_bytes(tmp_path: Path) -> None:
    catalog, _, _, managed_path = _catalog(tmp_path, [])
    first = {
        "id": "alpha",
        "name": "Alpha",
        "baseUrl": "https://api.alpha.example/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "probeStrategy": "openai_models",
    }
    catalog.upsert_managed_provider(first)
    catalog.upsert_managed_provider({"id": "alpha", "name": "Alpha Updated"})
    invalid_bytes = b"{broken-managed-catalog"
    managed_path.write_bytes(invalid_bytes)

    before = catalog.managed_recovery_state()
    recovered = catalog.recover_managed_from_backup(
        expected_managed_digest=before["managedDigest"],
        expected_backup_digest=before["backupDigest"],
    )

    assert before["managedValid"] is False
    assert before["backupValid"] is True
    assert recovered["managedValid"] is True
    assert recovered["rejectedExists"] is True
    assert catalog.load_managed()["providers"] == [first]

    rolled_back = catalog.rollback_managed_recovery(
        expected_current_digest=recovered["managedDigest"]
    )

    assert rolled_back["managedValid"] is False
    assert rolled_back["rejectedExists"] is False
    assert managed_path.read_bytes() == invalid_bytes


def test_managed_provider_rollback_is_target_scoped_and_does_not_pollute_backup(
    tmp_path: Path,
) -> None:
    catalog, _, _, _ = _catalog(tmp_path, [])
    alpha_before = {
        "id": "alpha",
        "name": "Alpha",
        "baseUrl": "https://api.alpha.example/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "probeStrategy": "openai_models",
    }
    beta_before = {
        "id": "beta",
        "name": "Beta",
        "baseUrl": "https://api.beta.example/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "probeStrategy": "openai_models",
    }
    catalog.upsert_managed_provider(alpha_before)
    catalog.upsert_managed_provider(beta_before)
    catalog.upsert_managed_provider({"id": "alpha", "name": "Alpha Changed"})
    stale_full_digest = catalog.managed_recovery_state()["managedDigest"]
    expected_alpha_digest = catalog.managed_provider_digest("alpha")

    catalog.upsert_managed_provider({"id": "beta", "name": "Beta Changed"})
    backup_before_rollback = catalog.managed_backup_path.read_bytes()

    restored = catalog.restore_managed_provider(
        "alpha",
        alpha_before,
        expected_managed_digest=stale_full_digest,
        expected_provider_digest=expected_alpha_digest,
    )

    assert restored == alpha_before
    assert catalog.get_managed_provider("alpha") == alpha_before
    assert catalog.get_managed_provider("beta")["name"] == "Beta Changed"
    assert catalog.managed_backup_path.read_bytes() == backup_before_rollback


def test_managed_provider_rollback_rejects_target_drift(tmp_path: Path) -> None:
    catalog, _, _, _ = _catalog(tmp_path, [])
    provider = {
        "id": "alpha",
        "name": "Alpha",
        "baseUrl": "https://api.alpha.example/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "probeStrategy": "openai_models",
    }
    catalog.upsert_managed_provider(provider)
    expected_provider_digest = catalog.managed_provider_digest("alpha")
    catalog.upsert_managed_provider({"id": "alpha", "name": "Alpha Changed"})

    with pytest.raises(ValueError, match="provider digest conflict"):
        catalog.restore_managed_provider(
            "alpha",
            provider,
            expected_provider_digest=expected_provider_digest,
        )


def test_static_media_provider_projection_is_cached_and_copy_isolated(tmp_path: Path, monkeypatch) -> None:
    catalog, _, _, _ = _catalog(tmp_path, [])
    first = catalog._creative_media_matrix_providers()
    assert first
    first[0]["name"] = "mutated by caller"

    monkeypatch.setattr(
        catalog,
        "_provider_from_media_matrix_entry",
        lambda *_args, **_kwargs: pytest.fail("cached media assets must not be rebuilt"),
    )
    second = catalog._creative_media_matrix_providers()

    assert second
    assert second[0]["name"] != "mutated by caller"


@pytest.mark.parametrize(
    "url,error_match",
    [
        ("https://user:password@api.alpha.example/v1", "userinfo"),
        ("https://api.alpha.example/v1?api_key=secret", "sensitive URL query"),
        ("https://api.alpha.example/v1?clientToken=secret", "sensitive URL query"),
        ("https://api.alpha.example/v1?client%2554oken=secret", "sensitive URL query"),
        ("https://api.alpha.example/v1#access_token=secret", "URL fragment"),
    ],
)
def test_managed_urls_and_probe_target_reject_secret_bearing_components(
    tmp_path: Path,
    url: str,
    error_match: str,
) -> None:
    catalog, _, _, _ = _catalog(tmp_path, [])
    provider = {
        "id": "alpha",
        "name": "Alpha",
        "baseUrl": url,
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "probeStrategy": "openai_models",
    }

    with pytest.raises(ValueError, match=error_match):
        catalog.upsert_managed_provider(provider)
    with pytest.raises(ValueError, match=error_match):
        resolve_probe_target(provider)


def test_resolve_probe_target_returns_safe_target_metadata() -> None:
    target = resolve_probe_target(
        {
            "id": "alpha",
            "baseUrl": "https://api.alpha.example/v1?region=cn",
            "apiStandard": "openai",
            "probeStrategy": "openai_models",
        }
    )

    assert target == {
        "url": "https://api.alpha.example/v1/models?region=cn",
        "baseUrl": "https://api.alpha.example/v1?region=cn",
        "scheme": "https",
        "host": "api.alpha.example",
        "port": None,
        "path": "/v1/models",
        "providerId": "alpha",
        "channelId": "",
        "apiStandard": "openai",
        "probeStrategy": "openai_models",
    }


@pytest.mark.parametrize("strategy", ["openai_models", "comfyui"])
def test_provider_probe_never_follows_redirects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
) -> None:
    catalog, _, _, _ = _catalog(tmp_path, [])
    captured: dict = {}

    class RedirectResponse:
        ok = True
        status_code = 302
        text = "redirecting"

    def fake_get(*args, **kwargs):
        captured.update(kwargs)
        return RedirectResponse()

    monkeypatch.setattr(model_provider_catalog_module.requests, "get", fake_get)
    result = catalog.probe_provider_entry(
        {
            "id": "alpha",
            "name": "Alpha",
            "baseUrl": "https://api.alpha.example/v1",
            "apiStandard": "openai",
            "auth": {"type": "none"},
            "probeStrategy": strategy,
        }
    )

    assert captured["allow_redirects"] is False
    assert result["ok"] is False
    assert result["reason"] == "redirect_not_allowed"


def test_provider_probe_redacts_credentials_from_response_and_exception_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, _, _, _ = _catalog(tmp_path, [])
    credential = "sk-secret+/value"
    provider = {
        "id": "alpha",
        "name": "Alpha",
        "baseUrl": "https://api.alpha.example/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
        "probeStrategy": "openai_models",
    }

    class UnauthorizedResponse:
        ok = False
        status_code = 401
        text = f"api_key={credential}; encoded=sk-secret%2B%2Fvalue"

    monkeypatch.setattr(
        model_provider_catalog_module.requests,
        "get",
        lambda *args, **kwargs: UnauthorizedResponse(),
    )
    response_result = catalog.probe_provider_entry(provider, credential=credential)

    def fail_request(*args, **kwargs):
        raise RuntimeError(f"request failed with Authorization: Bearer {credential}")

    monkeypatch.setattr(model_provider_catalog_module.requests, "get", fail_request)
    exception_result = catalog.probe_provider_entry(provider, credential=credential)

    assert credential not in json.dumps(response_result, ensure_ascii=False)
    assert "sk-secret%2B%2Fvalue" not in json.dumps(response_result, ensure_ascii=False)
    assert credential not in json.dumps(exception_result, ensure_ascii=False)
    assert "[redacted]" in response_result["error"]
    assert "[redacted]" in exception_result["error"]


def test_catalog_connectability_overrides_declared_availability(tmp_path: Path) -> None:
    catalog, _, _, _ = _catalog(
        tmp_path,
        [
            {
                "id": "offline",
                "name": "Offline",
                "baseUrl": "not-a-url",
                "models": [{"id": "model-1", "availability": {"catalogConnectable": True}}],
            }
        ],
    )

    normalized = catalog.normalize_model(catalog.get_provider("offline") or {}, "model-1")

    assert normalized["availability"]["catalogConnectable"] is False
    assert normalized["availability"]["catalogConnectReason"] == "provider_endpoint_unconfigured"


def test_managed_mutations_and_recovery_use_digest_compare_and_swap(tmp_path: Path) -> None:
    catalog, _, _, managed_path = _catalog(tmp_path, [])
    first = {
        "id": "alpha",
        "name": "Alpha",
        "baseUrl": "https://api.alpha.example/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key"},
        "probeStrategy": "openai_models",
    }
    catalog.upsert_managed_provider(first)
    stale_digest = catalog.managed_recovery_state()["managedDigest"]
    catalog.upsert_managed_provider({"id": "alpha", "name": "Alpha Updated"})

    with pytest.raises(ValueError, match="digest conflict"):
        catalog.delete_managed_provider("alpha", expected_managed_digest=stale_digest)

    invalid_bytes = b"{broken-managed-catalog"
    managed_path.write_bytes(invalid_bytes)
    recovery_before = catalog.managed_recovery_state()
    with pytest.raises(ValueError, match="managed digest conflict"):
        catalog.recover_managed_from_backup(
            expected_managed_digest=stale_digest,
            expected_backup_digest=recovery_before["backupDigest"],
        )

    recovered = catalog.recover_managed_from_backup(
        expected_managed_digest=recovery_before["managedDigest"],
        expected_backup_digest=recovery_before["backupDigest"],
    )
    finalized = catalog.finalize_managed_recovery(
        expected_current_digest=recovered["managedDigest"],
        expected_rejected_digest=recovered["rejectedDigest"],
    )
    assert finalized["rejectedExists"] is False
    assert finalized["tombstoneExists"] is True

    finalize_rolled_back = catalog.rollback_managed_recovery(
        expected_current_digest=finalized["managedDigest"],
        expected_rejected_digest=recovered["rejectedDigest"],
    )

    assert finalize_rolled_back["managedValid"] is True
    assert finalize_rolled_back["rejectedExists"] is True
    assert finalize_rolled_back["tombstoneExists"] is False
    assert catalog.managed_rejected_path.read_bytes() == invalid_bytes


def test_media_and_root_caches_are_single_flight_retryable_and_copy_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix_path = tmp_path / "media_matrix.json"
    matrix_path.write_text(
        json.dumps({"modalities": {"image": [{"id": "cache-image", "displayName": "Cache image"}]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_provider_catalog_module, "_CREATIVE_MEDIA_MATRIX_PATH", matrix_path)
    catalog, _, _, _ = _catalog(
        tmp_path,
        [{"id": "root", "capabilityEntries": [{"sourceProviderId": "cache-image"}]}],
    )
    original_provider_builder = catalog._provider_from_media_matrix_entry
    build_started = threading.Event()
    release_build = threading.Event()
    build_calls = 0

    def slow_provider_builder(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        build_started.set()
        assert release_build.wait(timeout=2)
        return original_provider_builder(*args, **kwargs)

    monkeypatch.setattr(catalog, "_provider_from_media_matrix_entry", slow_provider_builder)
    results: list[list[dict]] = []
    workers = [threading.Thread(target=lambda: results.append(catalog._creative_media_matrix_providers())) for _ in range(3)]
    workers[0].start()
    assert build_started.wait(timeout=2)
    for worker in workers[1:]:
        worker.start()
    release_build.set()
    for worker in workers:
        worker.join(timeout=2)

    assert build_calls == 1
    assert len(results) == 3
    results[0][0]["name"] = "caller mutation"
    assert catalog._creative_media_matrix_providers()[0]["name"] != "caller mutation"

    root_first = catalog._root_media_mappings()
    root_first["cache-image"].add("caller mutation")
    assert catalog._root_media_mappings()["cache-image"] == {"root"}

    retry_path = tmp_path / "retry"
    retry_path.mkdir()
    retry_catalog, _, _, _ = _catalog(retry_path, [])
    retry_original_builder = retry_catalog._provider_from_media_matrix_entry
    retry_calls = 0

    def transient_provider_builder(*args, **kwargs):
        nonlocal retry_calls
        retry_calls += 1
        if retry_calls == 1:
            raise OSError("transient media asset failure")
        return retry_original_builder(*args, **kwargs)

    monkeypatch.setattr(retry_catalog, "_provider_from_media_matrix_entry", transient_provider_builder)
    with pytest.raises(OSError, match="transient media asset failure"):
        retry_catalog._creative_media_matrix_providers()
    assert retry_catalog._creative_media_matrix_providers()
    assert retry_calls == 2
