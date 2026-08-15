from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from api import session_workflow_routes as routes


class _FakeDatabase:
    def __init__(self, session_ids: set[str]) -> None:
        self._session_ids = session_ids

    def get_session(self, session_id: str):
        return {"id": session_id} if session_id in self._session_ids else None


class _FakeMemoryRuntime:
    def __init__(self, artifacts: list[dict]) -> None:
        self._artifacts = artifacts

    def list_artifacts(self, *, session_id: str, run_id: str | None, limit: int) -> list[dict]:
        items = self._artifacts
        if run_id:
            items = [item for item in items if str(item.get("runId") or item.get("run_id") or "") == run_id]
        return items[:limit]

    def get_artifact(self, artifact_id: str):
        return next(
            (item for item in self._artifacts if str(item.get("artifactId") or item.get("id") or "") == artifact_id),
            None,
        )


def _request(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://engine.local") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def _artifact(
    artifact_id: str,
    *,
    session_id: str,
    source_path: Path | None,
    metadata: dict | None = None,
    workspace_path: str | None = None,
) -> dict:
    return {
        "id": artifact_id,
        "artifact_kind": "document",
        "mime_type": "text/plain",
        "session_id": session_id,
        "run_id": "run-1",
        "title": f"{artifact_id}.txt",
        "source_path": str(source_path) if source_path else None,
        "workspace_path": workspace_path,
        "metadata": metadata or {},
    }


def _build_app(monkeypatch, tmp_path: Path) -> tuple[FastAPI, dict[str, Path]]:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    runtime_root = tmp_path / "runtime-artifacts"
    workspace_a.mkdir()
    workspace_b.mkdir()
    runtime_root.mkdir()

    paths = {
        "workspace_a": workspace_a,
        "workspace_b": workspace_b,
        "workspace_file": workspace_a / "artifact.txt",
        "other_workspace_file": workspace_b / "other.txt",
        "runtime_file": runtime_root / "runtime.txt",
    }
    paths["workspace_file"].write_bytes(b"0123456789")
    paths["other_workspace_file"].write_bytes(b"other")
    paths["runtime_file"].write_bytes(b"runtime")

    metadata_a = {
        "workspaceId": "workspace-a",
        "projectId": "project-a",
        "workspaceRoot": str(workspace_a),
        "workspaceRelativePath": "artifact.txt",
        "storageClass": "workspace",
        "pathPlane": "workspace_artifact",
    }
    artifacts = [
        _artifact("art-good", session_id="session-a", source_path=paths["workspace_file"], metadata=metadata_a),
        _artifact("art-relative-source", session_id="session-a", source_path=Path("artifact.txt"), metadata=metadata_a),
        _artifact("art-other-session", session_id="session-b", source_path=paths["workspace_file"], metadata=metadata_a),
        _artifact(
            "art-other-workspace",
            session_id="session-a",
            source_path=paths["other_workspace_file"],
            metadata={
                "workspaceId": "workspace-b",
                "projectId": "project-b",
                "workspaceRoot": str(workspace_b),
                "workspaceRelativePath": "other.txt",
                "storageClass": "workspace",
                "pathPlane": "workspace_artifact",
            },
        ),
        _artifact(
            "art-root-mismatch",
            session_id="session-a",
            source_path=paths["other_workspace_file"],
            metadata={
                "workspaceId": "workspace-a",
                "projectId": "project-a",
                "workspaceRoot": str(workspace_b),
                "workspaceRelativePath": "other.txt",
                "storageClass": "workspace",
                "pathPlane": "workspace_artifact",
            },
        ),
        _artifact(
            "art-workspace-path-escape",
            session_id="session-a",
            source_path=paths["runtime_file"],
            metadata={
                "workspaceId": "workspace-a",
                "projectId": "project-a",
                "workspaceRoot": str(workspace_a),
                "storageClass": "workspace",
                "pathPlane": "workspace_artifact",
            },
        ),
        _artifact(
            "art-runtime-private",
            session_id="session-a",
            source_path=paths["runtime_file"],
            metadata={
                "workspaceId": "workspace-a",
                "projectId": "project-a",
                "workspacePath": str(workspace_a),
                "storageClass": "runtime_artifact",
                "pathPlane": "runtime",
            },
        ),
        _artifact(
            "art-relative-path-escape",
            session_id="session-a",
            source_path=paths["runtime_file"],
            metadata={
                "workspaceId": "workspace-a",
                "projectId": "project-a",
                "workspaceRoot": str(workspace_a),
                "workspaceRelativePath": "../runtime-artifacts/runtime.txt",
                "storageClass": "runtime_artifact",
                "pathPlane": "runtime",
            },
        ),
        _artifact(
            "art-misaligned-workspace-path",
            session_id="session-a",
            source_path=paths["runtime_file"],
            workspace_path="unrelated.txt",
            metadata={
                "workspaceId": "workspace-a",
                "projectId": "project-a",
                "storageClass": "runtime_artifact",
                "pathPlane": "runtime",
            },
        ),
        _artifact("art-unbound-legacy", session_id="session-a", source_path=paths["runtime_file"]),
    ]

    authorities = {
        "session-a": SimpleNamespace(workspace_id="workspace-a", project_id="project-a", workspace_root=str(workspace_a)),
        "session-b": SimpleNamespace(workspace_id="workspace-a", project_id="project-a", workspace_root=str(workspace_a)),
        "session-c": SimpleNamespace(workspace_id="workspace-b", project_id="project-b", workspace_root=str(workspace_b)),
        "session-no-workspace": SimpleNamespace(workspace_id="", project_id="", workspace_root=""),
    }
    monkeypatch.setattr(routes, "db", _FakeDatabase(set(authorities)))
    monkeypatch.setattr(routes, "_memory_runtime", lambda: _FakeMemoryRuntime(artifacts))
    monkeypatch.setattr(
        routes,
        "workspace_authority_service",
        SimpleNamespace(resolve=lambda *, runtime_kind, session_id: authorities[session_id]),
    )

    app = FastAPI()
    app.include_router(routes.router, prefix="/v1")
    return app, paths


def test_generic_artifact_routes_require_canonical_session_id(monkeypatch, tmp_path: Path) -> None:
    app, _ = _build_app(monkeypatch, tmp_path)

    for path in ("/v1/artifacts", "/v1/artifacts/art-good", "/v1/artifacts/art-good/content"):
        response = _request(app, "GET", path)
        assert response.status_code == 422


def test_artifact_list_filters_session_and_workspace_mismatches(monkeypatch, tmp_path: Path) -> None:
    app, _ = _build_app(monkeypatch, tmp_path)

    response = _request(app, "GET", "/v1/artifacts?sessionId=session-a")
    assert response.status_code == 200
    assert [item["artifactId"] for item in response.json()["artifacts"]] == [
        "art-good",
        "art-relative-source",
        "art-runtime-private",
    ]

    path_scoped = _request(app, "GET", "/v1/sessions/session-a/artifacts")
    assert path_scoped.status_code == 200
    assert [item["artifactId"] for item in path_scoped.json()["artifacts"]] == [
        "art-good",
        "art-relative-source",
        "art-runtime-private",
    ]

    missing_session = _request(app, "GET", "/v1/artifacts?sessionId=session-missing")
    assert missing_session.status_code == 404
    assert missing_session.json() == {"detail": "Artifact not found"}

    missing_workspace = _request(app, "GET", "/v1/artifacts?sessionId=session-no-workspace")
    assert missing_workspace.status_code == 404
    assert missing_workspace.json() == {"detail": "Artifact not found"}


def test_artifact_detail_fails_closed_across_session_workspace_and_path(monkeypatch, tmp_path: Path) -> None:
    app, _ = _build_app(monkeypatch, tmp_path)

    authorized = _request(app, "GET", "/v1/artifacts/art-good?sessionId=session-a")
    assert authorized.status_code == 200
    assert authorized.json()["artifactId"] == "art-good"

    for path in (
        "/v1/artifacts/art-good?sessionId=session-b",
        "/v1/artifacts/art-good?sessionId=session-c",
        "/v1/artifacts/art-other-workspace?sessionId=session-a",
        "/v1/artifacts/art-root-mismatch?sessionId=session-a",
        "/v1/artifacts/art-workspace-path-escape?sessionId=session-a",
        "/v1/artifacts/art-relative-path-escape?sessionId=session-a",
        "/v1/artifacts/art-misaligned-workspace-path?sessionId=session-a",
        "/v1/artifacts/art-unbound-legacy?sessionId=session-a",
    ):
        response = _request(app, "GET", path)
        assert response.status_code == 404
        assert response.json() == {"detail": "Artifact not found"}

    runtime_private = _request(app, "GET", "/v1/artifacts/art-runtime-private?sessionId=session-a")
    assert runtime_private.status_code == 200


def test_artifact_content_preserves_range_inline_and_download(monkeypatch, tmp_path: Path) -> None:
    app, _ = _build_app(monkeypatch, tmp_path)

    inline = _request(app, "GET", "/v1/artifacts/art-good/content?sessionId=session-a")
    assert inline.status_code == 200
    assert inline.content == b"0123456789"
    assert inline.headers["content-disposition"].startswith("inline;")

    download = _request(app, "GET", "/v1/artifacts/art-good/content?sessionId=session-a&download=true")
    assert download.status_code == 200
    assert download.content == b"0123456789"
    assert download.headers["content-disposition"].startswith("attachment;")

    ranged = _request(
        app,
        "GET",
        "/v1/artifacts/art-good/content?sessionId=session-a",
        headers={"range": "bytes=2-5"},
    )
    assert ranged.status_code == 206
    assert ranged.content == b"2345"
    assert ranged.headers["content-range"] == "bytes 2-5/10"

    relative_source = _request(app, "GET", "/v1/artifacts/art-relative-source/content?sessionId=session-a")
    assert relative_source.status_code == 200
    assert relative_source.content == b"0123456789"

    unauthorized = _request(app, "GET", "/v1/artifacts/art-good/content?sessionId=session-b")
    assert unauthorized.status_code == 404
    assert unauthorized.json() == {"detail": "Artifact not found"}
