from __future__ import annotations

import asyncio

from core.database import db
from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.runtime_episodes import build_runtime_episode


def test_runtime_episode_queue_claim_and_unknown_executor_completes_recoverably():
    kind = "test_unknown_episode"
    episode = build_runtime_episode(
        need={"kind": kind, "source": "test", "reason": "exercise queue"},
        kind=kind,
        state="queued",
        continuation_target="runtime_episode_runner",
    )
    db.upsert_runtime_episode_record(episode, enqueue=True)

    runner = RuntimeEpisodeRunner()
    claimed = db.claim_runtime_episode(worker_id=runner.worker_id, lease_seconds=30, kinds=[kind])
    assert claimed is not None
    assert claimed["episodeId"] == episode["episodeId"]
    assert claimed["state"] == "active"

    asyncio.run(runner._execute_episode(claimed))

    stored = db.get_runtime_episode(episode["episodeId"])
    assert stored is not None
    assert stored["state"] == "failed"
    assert stored["resultRef"]
    assert stored["recoverable"] is True
