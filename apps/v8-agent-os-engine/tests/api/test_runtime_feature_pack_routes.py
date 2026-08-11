from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api import runtime_feature_pack_routes as routes


OPERATION_ID = "11111111-2222-4333-8444-555555555555"
STALE_OPERATION_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _request(patch: dict, expected_operation_id: str | None):
    return routes.FeaturePackStatePatchRequest.model_validate(
        {
            "patch": patch,
            "expectedOperationId": expected_operation_id,
        }
    )


def test_feature_pack_state_route_requires_internal_secret(monkeypatch) -> None:
    monkeypatch.setattr(routes, "get_internal_secret", lambda: "internal-secret")

    with pytest.raises(HTTPException) as exc_info:
        routes.require_feature_pack_internal_secret("wrong")

    assert exc_info.value.status_code == 401
    routes.require_feature_pack_internal_secret("internal-secret")


def test_feature_pack_status_snapshot_uses_registry_and_runtime_platform(monkeypatch) -> None:
    registry = {
        "installPlatform": "linux",
        "featurePacks": {"rpa_automation": {"status": "installed"}},
    }
    captured: list[tuple[dict, str | None]] = []

    monkeypatch.setenv("ENGINE_INSTALL_PLATFORM", "windows")
    monkeypatch.setattr(routes.storage, "get_runtime_registry_config", lambda: deepcopy(registry))
    monkeypatch.setattr(
        routes,
        "build_feature_pack_statuses",
        lambda payload, *, install_platform=None: (
            captured.append((deepcopy(payload), install_platform))
            or [{"id": "rpa_automation", "status": "installed", "restartRequired": False}]
        ),
    )
    monkeypatch.setattr(routes, "utc_now_iso", lambda: "2026-08-11T10:00:00Z")

    response = routes.get_feature_pack_status_snapshot()

    assert captured == [(registry, "windows")]
    assert response == {
        "sampledAt": "2026-08-11T10:00:00Z",
        "installPlatform": "windows",
        "featurePacks": [
            {"id": "rpa_automation", "status": "installed", "restartRequired": False}
        ],
    }
    assert "internal-secret" not in str(response)


def test_feature_pack_status_snapshot_samples_before_registry_read(monkeypatch) -> None:
    order: list[str] = []

    def sample_time() -> str:
        order.append("sample")
        return "2026-08-11T10:00:00Z"

    def read_registry() -> dict:
        order.append("registry")
        return {}

    monkeypatch.setattr(routes, "utc_now_iso", sample_time)
    monkeypatch.setattr(routes.storage, "get_runtime_registry_config", read_registry)
    monkeypatch.setattr(routes, "build_feature_pack_statuses", lambda *_args, **_kwargs: [])

    response = routes.get_feature_pack_status_snapshot()

    assert order == ["sample", "registry"]
    assert response["sampledAt"] == "2026-08-11T10:00:00Z"


def test_feature_pack_state_request_rejects_unknown_fields_and_requires_cas() -> None:
    with pytest.raises(ValidationError):
        routes.FeaturePackStatePatchRequest.model_validate(
            {
                "patch": {"status": "installing", "arbitrary": "value"},
                "expectedOperationId": None,
            }
        )

    with pytest.raises(ValidationError):
        routes.FeaturePackStatePatchRequest.model_validate(
            {"patch": {"status": "installing"}}
        )

    with pytest.raises(ValidationError):
        routes.FeaturePackStatePatchRequest.model_validate(
            {
                "patch": {"status": "installing", "updatedAt": "2099-01-01T00:00:00Z"},
                "expectedOperationId": None,
            }
        )


def test_feature_pack_state_patch_merges_runtime_registry_and_enforces_operation_cas(monkeypatch) -> None:
    current = {
        "installProfile": "desktop",
        "policies": {"startup": "managed"},
        "featurePacks": {
            "rpa_automation": {"status": "not_installed", "customSibling": "preserve"},
            "computer_use_desktop": {"status": "installed", "version": "existing"},
        },
    }
    calls: list[str] = []

    def mutate_config_domain(domain: str, mutator):
        nonlocal current
        calls.append(domain)
        current = mutator(deepcopy(current))
        return deepcopy(current)

    monkeypatch.setattr(routes.storage, "mutate_config_domain", mutate_config_domain)
    monkeypatch.setattr(routes, "utc_now_iso", lambda: "2026-08-10T10:00:01Z")
    operation_id = OPERATION_ID
    response = routes.patch_feature_pack_state(
        "rpa_automation",
        _request(
            {
                "status": "installing",
                "operationId": operation_id,
                "startedAt": "2026-08-10T10:00:00Z",
            },
            None,
        ),
    )

    assert calls == ["runtimeRegistry"]
    assert response["state"]["operationId"] == operation_id
    assert response["state"]["updatedAt"] == "2026-08-10T10:00:01Z"
    assert current["installProfile"] == "desktop"
    assert current["policies"] == {"startup": "managed"}
    assert current["featurePacks"]["rpa_automation"]["customSibling"] == "preserve"
    assert current["featurePacks"]["computer_use_desktop"] == {
        "status": "installed",
        "version": "existing",
    }

    with pytest.raises(HTTPException) as exc_info:
        routes.patch_feature_pack_state(
            "rpa_automation",
            _request({"status": "failed", "operationId": None}, STALE_OPERATION_ID),
        )

    assert exc_info.value.status_code == 409
    assert current["featurePacks"]["rpa_automation"]["status"] == "installing"


def test_feature_pack_state_patch_validates_installed_receipt_before_commit(monkeypatch) -> None:
    current = {
        "featurePacks": {
            "rpa_automation": {
                "status": "installing",
                "operationId": OPERATION_ID,
            }
        }
    }

    def mutate_config_domain(_domain: str, mutator):
        nonlocal current
        current = mutator(deepcopy(current))
        return deepcopy(current)

    monkeypatch.setattr(routes.storage, "mutate_config_domain", mutate_config_domain)
    proposed_registries: list[dict] = []

    def incompatible_receipt(_pack_id: str, registry: dict):
        proposed_registries.append(deepcopy(registry))
        return False, "requirements_mismatch", "receipt mismatch", {}

    monkeypatch.setattr(routes, "_feature_pack_receipt_runtime_compatibility", incompatible_receipt)

    with pytest.raises(HTTPException) as exc_info:
        routes.patch_feature_pack_state(
            "rpa_automation",
            _request(
                {
                    "status": "installed",
                    "operationId": None,
                    "startedAt": None,
                },
                OPERATION_ID,
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "requirements_mismatch",
        "message": "receipt mismatch",
    }
    assert proposed_registries[0]["featurePacks"]["rpa_automation"]["status"] == "installed"
    assert proposed_registries[0]["featurePacks"]["rpa_automation"]["operationId"] is None
    assert current["featurePacks"]["rpa_automation"]["status"] == "installing"


def test_feature_pack_state_patch_rejects_unknown_pack_and_path_escape() -> None:
    with pytest.raises(HTTPException) as unknown:
        routes.patch_feature_pack_state(
            "unknown_pack",
            _request({"status": "installing"}, None),
        )
    assert unknown.value.status_code == 404

    with pytest.raises(HTTPException) as escaped:
        routes.patch_feature_pack_state(
            "rpa_automation",
            _request({"targetDir": "C:/outside/feature-pack"}, None),
        )
    assert escaped.value.status_code == 422


def test_feature_pack_state_patch_accepts_only_one_uuid_version_directory(monkeypatch) -> None:
    current = {
        "featurePacks": {
            "rpa_automation": {
                "status": "installing",
                "operationId": OPERATION_ID,
            }
        }
    }

    def mutate_config_domain(_domain: str, mutator):
        nonlocal current
        current = mutator(deepcopy(current))
        return deepcopy(current)

    monkeypatch.setattr(routes.storage, "mutate_config_domain", mutate_config_domain)
    monkeypatch.setattr(
        routes,
        "_feature_pack_receipt_runtime_compatibility",
        lambda _pack_id, _registry: (True, None, None, {}),
    )
    version_root = routes.FEATURE_PACK_PYTHON_ROOT / "rpa_automation" / "versions" / OPERATION_ID
    response = routes.patch_feature_pack_state(
        "rpa_automation",
        _request(
            {
                "status": "installed",
                "targetDir": str(version_root / "python"),
                "assetRoot": str(version_root / "models"),
                "receiptRef": str(version_root / "receipt.json"),
                "operationId": None,
                "startedAt": None,
            },
            OPERATION_ID,
        ),
    )

    assert response["state"]["targetDir"] == str(version_root / "python")
    assert response["state"]["receiptRef"] == str(version_root / "receipt.json")

    other_root = routes.FEATURE_PACK_PYTHON_ROOT / "rpa_automation" / "versions" / STALE_OPERATION_ID
    with pytest.raises(HTTPException) as mixed:
        routes.patch_feature_pack_state(
            "rpa_automation",
            _request(
                {
                    "targetDir": str(version_root / "python"),
                    "receiptRef": str(other_root / "receipt.json"),
                },
                OPERATION_ID,
            ),
        )
    assert mixed.value.status_code == 422

    legacy_target = routes.FEATURE_PACK_PYTHON_ROOT / "rpa_automation" / "python"
    with pytest.raises(HTTPException) as legacy:
        routes.patch_feature_pack_state(
            "rpa_automation",
            _request({"targetDir": str(legacy_target)}, OPERATION_ID),
        )
    assert legacy.value.status_code == 422


@pytest.mark.parametrize("field", ["operationId", "expectedOperationId"])
def test_feature_pack_state_patch_requires_canonical_operation_uuid(field: str) -> None:
    payload = {
        "patch": {"status": "installing", "operationId": "not-a-uuid" if field == "operationId" else None},
        "expectedOperationId": "not-a-uuid" if field == "expectedOperationId" else None,
    }
    request = routes.FeaturePackStatePatchRequest.model_validate(payload)

    with pytest.raises(HTTPException) as invalid:
        routes.patch_feature_pack_state("rpa_automation", request)

    assert invalid.value.status_code == 422


def test_runtime_feature_pack_router_is_mounted_under_v1() -> None:
    from api import routes as root_routes

    assert any(
        getattr(route, "original_router", None) is routes.router
        for route in root_routes.router.routes
    )
    status_route = next(
        route for route in routes.router.routes
        if route.path == "/runtime-feature-packs/status"
    )
    assert "GET" in status_route.methods
    assert [dependency.call for dependency in status_route.dependant.dependencies] == [
        routes.require_feature_pack_internal_secret
    ]
    assert any(route.path == "/runtime-feature-packs/{pack_id}/state" for route in routes.router.routes)
