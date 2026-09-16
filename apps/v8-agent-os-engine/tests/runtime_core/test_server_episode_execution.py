from __future__ import annotations

import asyncio

import pytest

from core.database import DatabaseManager
from core.runtime import startup_profile
import core.runtime_episode_control as control_module
import core.runtime_episode_runner as runner_module
from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.runtime_episodes import build_handoff_ref, build_runtime_episode
from core.runtime_tool_access import runtime_kind_available


@pytest.fixture
def server_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_INSTALL_PROFILE", "server")
    monkeypatch.setattr(startup_profile.storage, "get_runtime_registry_config", lambda: {
        "installProfile": "server", "installPlatform": "linux", "featurePacks": {},
        # Restored desktop registries cannot authorize local server execution.
        "installedRuntimeFamilies": list(startup_profile.KNOWN_RUNTIME_FAMILIES),
    })
    manager = DatabaseManager(tmp_path / "server-episodes.db")
    manager.create_or_update_session("server-review-session", "Server execution fixture")
    manager.create_run_record("server-review-run", "server-review-session", status="running")
    monkeypatch.setattr(runner_module, "db", manager)
    monkeypatch.setattr(control_module, "db", manager)
    runner = RuntimeEpisodeRunner()
    # Parent model resumption is outside this execution/queue transaction test.
    monkeypatch.setattr(runner, "_maybe_schedule_chat_handoff_resume", lambda *_args: None)
    monkeypatch.setattr(runner, "_maybe_resume_parent_episode", lambda *_args, **_kwargs: None)
    return manager, runner


def enqueue_and_claim(manager, runner, kind, *, target_kind="local_runtime"):
    episode = build_runtime_episode(
        need={"kind": kind, "source": "test", "reason": "queued before the server installation"},
        kind=kind, state="queued", continuation_target="runtime_episode_runner",
        extra={"targetKind": target_kind, "retryPolicy": {"maxAttempts": 3}},
    )
    manager.upsert_runtime_episode_record(
        episode, session_id="server-review-session", run_id="server-review-run", enqueue=True,
    )
    claimed = manager.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, require_bound_run=True)
    assert claimed is not None
    return claimed


def executor_probe(calls):
    async def execute(episode):
        calls.append(episode["episodeId"])
        return build_handoff_ref(
            producer_episode_id=episode["episodeId"], kind="test_handoff",
            compact_summary="Synthetic execution boundary reached", status="ready", confidence="high",
        )
    return execute


@pytest.mark.parametrize("kind", ["computer_use", "rpa", "creative_media"])
def test_restored_local_episode_fails_durably_before_unavailable_executor(server_runner, monkeypatch, kind):
    manager, runner = server_runner
    assert runtime_kind_available(kind) is False
    claimed = enqueue_and_claim(manager, runner, kind)
    called = []
    monkeypatch.setattr(runner, f"_execute_{kind}", executor_probe(called))
    # Child needs must not bypass local availability before executor dispatch.
    monkeypatch.setattr(runner, "_should_dispatch_child_needs", lambda _episode: pytest.fail("unavailable runtime reached child dispatch"))
    asyncio.run(runner._execute_episode(claimed))

    assert called == []
    stored = manager.get_runtime_episode(claimed["episodeId"])
    assert stored["state"] == "failed"
    assert stored["errorCode"] == "runtime_unavailable"
    handoffs = manager.list_runtime_episode_handoffs(claimed["episodeId"])
    assert len(handoffs) == 1
    assert handoffs[0]["payload"]["status"] == "failed"
    assert handoffs[0]["payload"]["recoverable"] is False
    assert stored["resultRef"] == handoffs[0]["payload"]["handoffRefId"]
    assert manager.claim_runtime_episode(worker_id=runner.worker_id, require_bound_run=True) is None
    with manager.get_connection() as conn:
        topics = [row[0] for row in conn.execute(
            "SELECT topic FROM runtime_episode_events WHERE episode_id=? ORDER BY created_at",
            (claimed["episodeId"],),
        )]
    assert "runtime.episode.failed" in topics
    assert "runtime.episode.retry_scheduled" not in topics


@pytest.mark.parametrize("kind", ["computer_use", "rpa", "creative_media"])
@pytest.mark.parametrize("target_kind", ["network_peer", "external_worker"])
def test_remote_target_does_not_require_local_feature_pack(server_runner, monkeypatch, kind, target_kind):
    manager, runner = server_runner
    assert runtime_kind_available(kind) is False
    claimed = enqueue_and_claim(manager, runner, kind, target_kind=target_kind)
    called = []
    monkeypatch.setattr(runner, f"_execute_{target_kind}_target", executor_probe(called))
    asyncio.run(runner._execute_episode(claimed))
    assert called == [claimed["episodeId"]]
    assert manager.get_runtime_episode(claimed["episodeId"])["state"] == "completed"


@pytest.mark.parametrize("kind", ["engineering", "research"])
def test_installed_server_runtime_still_executes(server_runner, monkeypatch, kind):
    manager, runner = server_runner
    assert runtime_kind_available(kind) is True
    claimed = enqueue_and_claim(manager, runner, kind)
    called = []
    monkeypatch.setattr(runner, f"_execute_{kind}", executor_probe(called))
    asyncio.run(runner._execute_episode(claimed))
    assert called == [claimed["episodeId"]]
    assert manager.get_runtime_episode(claimed["episodeId"])["state"] == "completed"


def test_server_media_episode_runs_when_canonical_pack_state_is_ready(server_runner, monkeypatch):
    manager, runner = server_runner
    snapshot = startup_profile.get_runtime_registry_state()
    snapshot["installedRuntimeFamilies"].append("creative_media")
    # Receipt validation belongs to startup_profile; the runner must use that
    # owner rather than permanently deny media merely because profile=server.
    monkeypatch.setattr(startup_profile, "get_runtime_registry_state", lambda: snapshot)
    assert runtime_kind_available("creative_media") is True
    claimed = enqueue_and_claim(manager, runner, "creative_media")
    called = []
    monkeypatch.setattr(runner, "_execute_creative_media", executor_probe(called))
    asyncio.run(runner._execute_episode(claimed))
    assert called == [claimed["episodeId"]]
    assert manager.get_runtime_episode(claimed["episodeId"])["state"] == "completed"
