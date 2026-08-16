from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import creative_media_routes


class _FakeRuntime:
    @staticmethod
    def catalog() -> dict:
        return {"ok": True, "source": "creative-media-fixture"}


def test_creative_media_http_surface_requires_internal_service_secret(monkeypatch) -> None:
    monkeypatch.setattr(creative_media_routes, "get_internal_secret", lambda: "internal-fixture-secret")
    monkeypatch.setattr(creative_media_routes, "creative_media_runtime", _FakeRuntime())
    app = FastAPI()
    app.include_router(creative_media_routes.router)

    with TestClient(app) as client:
        assert client.get("/creative-media/catalog").status_code == 401
        assert client.get(
            "/creative-media/catalog",
            headers={"x-v8-agent-os-secret": "wrong"},
        ).status_code == 401
        response = client.get(
            "/creative-media/catalog",
            headers={"x-v8-agent-os-secret": "internal-fixture-secret"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "source": "creative-media-fixture"}


def test_creative_media_http_surface_fails_closed_without_configured_secret(monkeypatch) -> None:
    monkeypatch.setattr(creative_media_routes, "get_internal_secret", lambda: "")
    app = FastAPI()
    app.include_router(creative_media_routes.router)

    with TestClient(app) as client:
        response = client.get(
            "/creative-media/catalog",
            headers={"x-v8-agent-os-secret": "caller-supplied"},
        )

    assert response.status_code == 401
