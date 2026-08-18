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


def test_end_user_job_routes_require_owner_scope_before_runtime_access(monkeypatch) -> None:
    calls: list[str] = []

    class _GlobalJobRuntime:
        def list_jobs(self, **_kwargs) -> list[dict]:
            calls.append("global-list")
            return [{"jobId": "foreign-job", "sessionId": "session-b"}]

        @staticmethod
        def public_job_projection(job: dict) -> dict:
            return dict(job)

    monkeypatch.setattr(creative_media_routes, "get_internal_secret", lambda: "internal-fixture-secret")
    monkeypatch.setattr(creative_media_routes, "creative_media_runtime", _GlobalJobRuntime())
    app = FastAPI()
    app.include_router(creative_media_routes.router)

    with TestClient(app) as client:
        response = client.get(
            "/creative-media/jobs",
            headers={"x-v8-agent-os-secret": "internal-fixture-secret"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "creative_media_owner_scope_required"}
    assert calls == []


def test_job_routes_use_exact_owner_scope_and_reject_foreign_side_effects(monkeypatch) -> None:
    captured: list[tuple[str, dict]] = []
    side_effects = {"provider": 0, "file": 0, "cost": 0, "artifact": 0}

    class _ScopedJobRuntime:
        def list_authorized_jobs(self, **claims) -> list[dict]:
            captured.append(("list", dict(claims)))
            return [{"jobId": "own-job", "sessionId": "session-a"}]

        def get_authorized_job(self, _job_id: str, **claims):
            captured.append(("get", dict(claims)))
            raise PermissionError("foreign")

        async def refresh_authorized_job(self, _job_id: str, **claims):
            captured.append(("refresh", dict(claims)))
            raise PermissionError("foreign")

        def authorized_job_artifacts(self, _job_id: str, **_claims):
            side_effects["file"] += 1
            return []

        async def retry_authorized_job(self, _job_id: str, **claims):
            captured.append(("retry", dict(claims)))
            raise PermissionError("foreign")

        async def create_job(self, payload: dict) -> dict:
            captured.append(("create", dict(payload)))
            return {"jobId": "created", **payload}

        @staticmethod
        def public_job_projection(job: dict) -> dict:
            return dict(job)

    monkeypatch.setattr(creative_media_routes, "get_internal_secret", lambda: "internal-fixture-secret")
    monkeypatch.setattr(creative_media_routes, "creative_media_runtime", _ScopedJobRuntime())
    app = FastAPI()
    app.include_router(creative_media_routes.router)
    headers = {
        "x-v8-agent-os-secret": "internal-fixture-secret",
        "x-v8-session-id": "session-a",
        "x-v8-workspace-id": "workspace-a",
        "x-v8-project-id": "project-a",
        "x-v8-workspace-path": "E:/workspace-a",
    }

    with TestClient(app) as client:
        listed = client.get("/creative-media/jobs", headers=headers)
        foreign_get = client.get("/creative-media/jobs/foreign?refresh=false", headers=headers)
        foreign_refresh = client.get("/creative-media/jobs/foreign", headers=headers)
        foreign_artifacts = client.get("/creative-media/jobs/foreign/artifacts", headers=headers)
        foreign_retry = client.post(
            "/creative-media/jobs/foreign/retry",
            headers=headers,
            json={"sessionId": "session-b", "workspaceId": "workspace-b"},
        )
        created = client.post(
            "/creative-media/jobs",
            headers=headers,
            json={"sessionId": "session-b", "workspaceId": "workspace-b", "prompt": "owned"},
        )

    assert listed.status_code == 200
    assert listed.json()["jobs"] == [{"jobId": "own-job", "sessionId": "session-a"}]
    assert [response.status_code for response in (
        foreign_get,
        foreign_refresh,
        foreign_artifacts,
        foreign_retry,
    )] == [404, 404, 404, 404]
    assert created.status_code == 200
    created_payload = next(payload for action, payload in captured if action == "create")
    assert created_payload["sessionId"] == "session-a"
    assert created_payload["workspaceId"] == "workspace-a"
    assert created_payload["projectId"] == "project-a"
    assert created_payload["workspacePath"] == "E:/workspace-a"
    assert side_effects == {"provider": 0, "file": 0, "cost": 0, "artifact": 0}
    for action, claims in captured:
        if action == "create":
            continue
        assert claims["session_id"] == "session-a"
        assert claims["workspace_id"] == "workspace-a"


def test_admin_governance_snapshot_is_explicit_and_never_refreshes_jobs(monkeypatch) -> None:
    calls: list[str] = []

    class _GovernanceRuntime:
        def governance_snapshot(self) -> dict:
            calls.append("snapshot")
            return {
                "jobs": {"jobs": [{"jobId": "governed", "status": "running"}]},
                "workOrders": {"workOrders": []},
            }

        def archive_work_order(self, _work_order_id: str) -> dict:
            calls.append("archive")
            return {"workOrderId": "work-order"}

        def delete_work_order(self, _work_order_id: str) -> dict:
            calls.append("delete")
            return {"workOrderId": "work-order"}

    monkeypatch.setattr(creative_media_routes, "get_internal_secret", lambda: "internal-fixture-secret")
    monkeypatch.setattr(
        creative_media_routes,
        "get_creative_media_admin_governance_secret",
        lambda: "governance-fixture-secret-value-1234567890",
    )
    monkeypatch.setattr(creative_media_routes, "creative_media_runtime", _GovernanceRuntime())
    app = FastAPI()
    app.include_router(creative_media_routes.router)
    secret = {"x-v8-agent-os-secret": "internal-fixture-secret"}

    with TestClient(app) as client:
        denied = client.get("/creative-media/governance/snapshot", headers=secret)
        forged_legacy = client.get(
            "/creative-media/governance/snapshot",
            headers={**secret, "x-v8-agent-os-governance": "admin"},
        )
        wrong_capability = client.get(
            "/creative-media/governance/snapshot",
            headers={
                **secret,
                "x-v8-agent-os-admin-governance-secret": "wrong-governance-secret",
            },
        )
        denied_archive = client.post(
            "/creative-media/governance/work-orders/work-order/archive",
            headers={**secret, "x-v8-agent-os-governance": "admin"},
        )
        denied_delete = client.post(
            "/creative-media/governance/work-orders/work-order/delete",
            headers={
                **secret,
                "x-v8-agent-os-admin-governance-secret": "wrong-governance-secret",
            },
        )
        assert calls == []
        allowed = client.get(
            "/creative-media/governance/snapshot",
            headers={
                **secret,
                "x-v8-agent-os-admin-governance-secret": "governance-fixture-secret-value-1234567890",
            },
        )

    assert [
        denied.status_code,
        forged_legacy.status_code,
        wrong_capability.status_code,
        denied_archive.status_code,
        denied_delete.status_code,
    ] == [403, 403, 403, 403, 403]
    assert allowed.status_code == 200
    assert allowed.json()["jobs"]["jobs"] == [{"jobId": "governed", "status": "running"}]
    assert calls == ["snapshot"]


def test_reconciler_status_requires_secret_and_redacts_provider_internals(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeReconcilerRuntime:
        def remote_reconciler_status(self) -> dict:
            calls.append("status")
            return {
                "running": False,
                "lastCycle": {
                    "status": "failed",
                    "error": "provider response body https://provider.invalid/raw",
                    "reports": [{"providerHandle": {"taskId": "cycle-secret"}}],
                },
            }

        def list_remote_reconcile_reports(self) -> list[dict]:
            calls.append("reports")
            return [
                {
                    "adapter": "fixture_adapter",
                    "detailCode": "provider_task_still_active",
                    "remoteTaskMayContinue": True,
                    "providerHandle": {"taskId": "provider-secret"},
                    "providerStatusRaw": "RAW_PROVIDER_STATUS",
                    "externalUrl": "https://provider.invalid/result",
                    "sourcePath": "E:/private/source.png",
                    "rawResponse": {"token": "secret"},
                    "nextReconcileAt": "2026-08-18T00:00:00Z",
                },
                {
                    "adapter": "fixture_adapter",
                    "detailCode": "projection_pending",
                    "remoteTaskMayContinue": False,
                    "projectionPending": True,
                    "terminalProof": {"providerHandle": {"taskId": "proof-secret"}},
                    "rawResponse": {"body": "secret"},
                    "reconciledAt": "2026-08-18T00:01:00Z",
                },
            ]

    monkeypatch.setattr(creative_media_routes, "get_internal_secret", lambda: "internal-fixture-secret")
    monkeypatch.setattr(creative_media_routes, "creative_media_runtime", _FakeReconcilerRuntime())
    app = FastAPI()
    app.include_router(creative_media_routes.router)

    with TestClient(app) as client:
        assert client.get("/creative-media/reconciler/status").status_code == 401
        assert client.get(
            "/creative-media/reconciler/status",
            headers={"x-v8-agent-os-secret": "wrong"},
        ).status_code == 401
        assert calls == []
        response = client.get(
            "/creative-media/reconciler/status",
            headers={"x-v8-agent-os-secret": "internal-fixture-secret"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["worker"]["state"] == "failed"
    assert payload["uncertain"] == 1
    assert payload["projectionPending"] == 1
    assert payload["adapterDistribution"] == {"fixture_adapter": 2}
    assert payload["detailCodeDistribution"] == {
        "projection_pending": 1,
        "provider_task_still_active": 1,
    }
    assert calls == ["status", "reports"]
    response_text = response.text.lower()
    for forbidden in (
        "providerhandle",
        "providertaskid",
        "taskid",
        "externalurl",
        "sourcepath",
        "rawresponse",
        "provider.invalid",
        "secret",
    ):
        assert forbidden not in response_text


def test_reconciler_status_runtime_failure_returns_safe_503(monkeypatch) -> None:
    class _FailingRuntime:
        def remote_reconciler_status(self) -> dict:
            raise RuntimeError("provider=https://provider.invalid/raw body=secret")

    monkeypatch.setattr(creative_media_routes, "get_internal_secret", lambda: "internal-fixture-secret")
    monkeypatch.setattr(creative_media_routes, "creative_media_runtime", _FailingRuntime())
    app = FastAPI()
    app.include_router(creative_media_routes.router)

    with TestClient(app) as client:
        response = client.get(
            "/creative-media/reconciler/status",
            headers={"x-v8-agent-os-secret": "internal-fixture-secret"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Creative Media reconciler status unavailable"}
    response_text = response.text.lower()
    assert "provider.invalid" not in response_text
    assert "secret" not in response_text
    assert "raw" not in response_text
