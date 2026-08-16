from __future__ import annotations

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import runtimes.creative_media.runtime as runtime_module
from runtimes.creative_media.runtime import CreativeMediaRuntime


def _job(
    job_id: str,
    *,
    session_id: str,
    workspace_id: str,
    workspace_path: Path,
) -> dict:
    return {
        "jobId": job_id,
        "sessionId": session_id,
        "projectId": f"project-{workspace_id}",
        "workspaceId": workspace_id,
        "workspacePath": str(workspace_path),
        "modality": "image",
        "operationKind": "image.generate",
        "status": "failed",
        "request": {
            "sessionId": session_id,
            "projectId": f"project-{workspace_id}",
            "workspaceId": workspace_id,
            "workspacePath": str(workspace_path),
            "modality": "image",
            "operationKind": "image.generate",
            "prompt": "fixture",
        },
        "artifacts": [{"artifactId": f"artifact-{job_id}", "kind": "image"}],
        "createdAt": "2026-08-16T00:00:00Z",
        "updatedAt": "2026-08-16T00:00:00Z",
    }


@pytest.fixture
def authorized_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    jobs = {
        "job-a": _job(
            "job-a",
            session_id="session-a",
            workspace_id="workspace-a",
            workspace_path=workspace_a,
        ),
        "job-b": _job(
            "job-b",
            session_id="session-b",
            workspace_id="workspace-b",
            workspace_path=workspace_b,
        ),
        "job-wrong-workspace": _job(
            "job-wrong-workspace",
            session_id="session-a",
            workspace_id="workspace-b",
            workspace_path=workspace_b,
        ),
    }
    authorities = {
        "session-a": SimpleNamespace(
            workspace_id="workspace-a",
            project_id="project-workspace-a",
            workspace_root=str(workspace_a),
            side_effects_allowed=True,
        ),
        "session-b": SimpleNamespace(
            workspace_id="workspace-b",
            project_id="project-workspace-b",
            workspace_root=str(workspace_b),
            side_effects_allowed=True,
        ),
    }
    runtime = CreativeMediaRuntime()
    monkeypatch.setattr(runtime, "_read_jobs", lambda: {"schemaVersion": 1, "jobs": copy.deepcopy(jobs)})
    monkeypatch.setattr(
        runtime_module.db,
        "get_session",
        lambda session_id: {"id": session_id} if session_id in authorities else None,
    )
    monkeypatch.setattr(
        runtime_module.workspace_authority_service,
        "resolve",
        lambda *, session_id, **_: authorities[session_id],
    )
    return runtime, jobs, authorities


def test_job_reads_and_lists_are_scoped_to_current_session_and_workspace(authorized_runtime) -> None:
    runtime, _, _ = authorized_runtime

    assert runtime.get_authorized_job("job-a", session_id="session-a")["jobId"] == "job-a"
    assert runtime.authorized_job_artifacts("job-a", session_id="session-a") == [
        {"artifactId": "artifact-job-a", "kind": "image"}
    ]
    assert [job["jobId"] for job in runtime.list_authorized_jobs(session_id="session-a")] == ["job-a"]

    with pytest.raises(PermissionError, match="current session"):
        runtime.get_authorized_job("job-b", session_id="session-a")
    with pytest.raises(PermissionError, match="current workspace"):
        runtime.get_authorized_job("job-wrong-workspace", session_id="session-a")
    with pytest.raises(PermissionError, match="Current session is unavailable"):
        runtime.get_authorized_job("job-a", session_id="")


def test_refresh_checks_authority_before_provider_poll(authorized_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, jobs, _ = authorized_runtime
    refreshed: list[str] = []

    async def fake_refresh(job_id: str):
        refreshed.append(job_id)
        return copy.deepcopy(jobs[job_id])

    monkeypatch.setattr(runtime, "refresh_job", fake_refresh)

    assert asyncio.run(runtime.refresh_authorized_job("job-a", session_id="session-a"))["jobId"] == "job-a"
    with pytest.raises(PermissionError, match="current session"):
        asyncio.run(runtime.refresh_authorized_job("job-b", session_id="session-a"))
    assert refreshed == ["job-a"]


def test_retry_preserves_original_authority_and_rejects_restricted_workspace(
    authorized_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _, authorities = authorized_runtime
    captured: list[dict] = []

    async def fake_create_job(payload: dict):
        captured.append(copy.deepcopy(payload))
        return {"jobId": "retry-job", **payload}

    monkeypatch.setattr(runtime, "create_job", fake_create_job)
    asyncio.run(
        runtime.retry_authorized_job(
            "job-a",
            session_id="session-a",
            request={
                "prompt": "retry prompt",
                "sessionId": "session-b",
                "workspaceId": "workspace-b",
                "workspacePath": "/outside",
                "project_id": "project-b",
            },
        )
    )

    assert captured[0]["sessionId"] == "session-a"
    assert captured[0]["workspaceId"] == "workspace-a"
    assert captured[0]["workspacePath"] == authorities["session-a"].workspace_root
    assert captured[0]["projectId"] == "project-workspace-a"
    assert captured[0]["prompt"] == "retry prompt"

    authorities["session-a"].side_effects_allowed = False
    with pytest.raises(PermissionError, match="does not allow Creative Media writes"):
        asyncio.run(runtime.retry_authorized_job("job-a", session_id="session-a", request={}))
    assert len(captured) == 1


def test_jobs_store_lock_prevents_different_job_last_writer_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = CreativeMediaRuntime()
    stored = {"schemaVersion": 1, "jobs": {}}
    read_count = 0
    read_count_lock = threading.Lock()
    second_reader_entered = threading.Event()

    def read_jobs() -> dict:
        nonlocal read_count
        with read_count_lock:
            read_count += 1
            current_read = read_count
        snapshot = copy.deepcopy(stored)
        if current_read == 1:
            second_reader_entered.wait(timeout=0.2)
        else:
            second_reader_entered.set()
        return snapshot

    def write_jobs(payload: dict) -> None:
        stored.clear()
        stored.update(copy.deepcopy(payload))

    monkeypatch.setattr(runtime, "_read_jobs", read_jobs)
    monkeypatch.setattr(runtime, "_write_jobs", write_jobs)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                runtime._save_job,
                {"jobId": job_id, "status": "queued", "artifacts": []},
                track_task=False,
            )
            for job_id in ("job-one", "job-two")
        ]
        for future in futures:
            future.result(timeout=2)

    assert set(stored["jobs"]) == {"job-one", "job-two"}
