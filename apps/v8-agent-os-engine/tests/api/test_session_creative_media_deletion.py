from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import session_workflow_routes as routes


class _FakeDatabase:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def get_session(self, session_id: str) -> dict:
        return {"id": session_id, "user_id": "owner-a"}

    def delete_session(self, session_id: str) -> None:
        self.events.append(f"db.delete:{session_id}")


def test_session_delete_stops_before_any_destructive_step_when_media_cleanup_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(routes, "db", _FakeDatabase(events))

    async def blocked(_session_id: str) -> dict:
        events.append("media.cleanup")
        return {
            "status": "blocked",
            "detailCode": "local_resource_cleanup_requires_retry",
            "readyForDeletion": False,
            "attempt": 2,
            "jobCount": 1,
            "localCleanupFailures": 1,
            "remoteUncertainJobs": 1,
            "jobs": [{"providerHandle": {"taskId": "must-not-leak"}}],
        }

    monkeypatch.setattr(routes, "_prepare_creative_media_session_deletion", blocked)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(routes.delete_session("session-a"))

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "creative_media_cleanup_incomplete",
        "summary": "Creative Media local resources could not be released; retry Session deletion.",
        "cleanup": {
            "status": "blocked",
            "detailCode": "local_resource_cleanup_requires_retry",
            "readyForDeletion": False,
            "attempt": 2,
            "jobCount": 1,
            "localCleanupFailures": 1,
            "remoteUncertainJobs": 1,
        },
    }
    assert events == ["media.cleanup"]


def test_session_delete_closes_media_before_owner_truth_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(routes, "db", _FakeDatabase(events))

    async def prepared(_session_id: str) -> dict:
        events.append("media.cleanup")
        return {
            "status": "prepared",
            "detailCode": "session_jobs_closed",
            "readyForDeletion": True,
            "attempt": 1,
            "jobCount": 2,
            "localCleanupFailures": 0,
            "remoteUncertainJobs": 1,
        }

    monkeypatch.setattr(routes, "_prepare_creative_media_session_deletion", prepared)

    from runtimes.plugin_manager import service as plugin_service
    from erc import checkpoint_store as checkpoint_module
    from erc import session_coordination_service as coordination_module

    monkeypatch.setattr(
        plugin_service,
        "plugin_manager_service",
        SimpleNamespace(revoke_session_grants=lambda session_id: events.append(f"plugins.revoke:{session_id}")),
    )
    monkeypatch.setattr(
        coordination_module,
        "session_coordination_service",
        SimpleNamespace(prepare_session_deletion=lambda session_id: events.append(f"coordination.delete:{session_id}")),
    )

    async def delete_checkpoint(session_id: str) -> dict:
        events.append(f"checkpoint.delete:{session_id}")
        return {"deleted": True}

    monkeypatch.setattr(
        checkpoint_module,
        "checkpoint_store",
        SimpleNamespace(delete_thread=delete_checkpoint),
    )
    monkeypatch.setattr(routes, "_refresh_web_session_index_safely", lambda: events.append("index.refresh"))
    monkeypatch.setattr(
        routes.session_activity_broker,
        "publish",
        lambda **_kwargs: events.append("activity.publish"),
    )

    result = asyncio.run(routes.delete_session("session-a"))

    assert events == [
        "media.cleanup",
        "plugins.revoke:session-a",
        "coordination.delete:session-a",
        "checkpoint.delete:session-a",
        "db.delete:session-a",
        "index.refresh",
        "activity.publish",
    ]
    assert result["creativeMediaCleanup"] == {
        "status": "prepared",
        "detailCode": "session_jobs_closed",
        "readyForDeletion": True,
        "attempt": 1,
        "jobCount": 2,
        "localCleanupFailures": 0,
        "remoteUncertainJobs": 1,
    }
