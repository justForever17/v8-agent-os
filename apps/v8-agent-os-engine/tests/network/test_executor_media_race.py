"""Deterministic publication/deletion interleavings against real SQLite/artifacts."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from core.artifact_store import ArtifactStore
from runtimes.network_supervisor.executors.media import RETENTION_MS
from tests.network.test_executor_media import bench, command, receipt, stage


def accepted(b):
    c = command(b)
    reservation, observation = stage(b, c)
    b.service.receipt(b.device["deviceId"], b.epoch, receipt(c, "succeeded", 3, observation=observation))
    return c, reservation["mediaId"]


def remove(b, media_id, mode):
    if mode == "delete":
        b.service.media.delete_owned(b.owner, media_id)
    else:
        b.clock[0] += RETENTION_MS / 1000 + 1
        b.service.media.cleanup()


def assert_gone(b, c, media_id, result):
    assert result["mediaStatus"] == "gone"
    assert "screenshotRef" not in result and "artifacts" not in result
    assert not b.service.media.path(media_id).exists()
    with b.identity.database() as db:
        assert db.execute("SELECT state FROM executor_media WHERE media_id=?", (media_id,)).fetchone()[0] == "deleted"
    assert b.service.status(b.owner, c["commandId"])["mediaStatus"] == "gone"


@pytest.mark.parametrize("mode", ["delete", "cleanup"])
def test_reentrant_removal_after_artifact_registration_cannot_return_available(bench, monkeypatch, mode):
    b = bench
    c, media_id = accepted(b)
    original = ArtifactStore.record_local_file
    order = []

    def record_then_remove(store, **kwargs):
        result = original(store, **kwargs)
        order.append("artifact_registered")
        # Same-thread owner callbacks must not deadlock a lifecycle lock.
        remove(b, media_id, mode)
        order.append("removal_completed")
        return result

    monkeypatch.setattr(ArtifactStore, "record_local_file", record_then_remove)
    result = b.service.status(b.owner, c["commandId"])
    assert order == ["artifact_registered", "removal_completed"]
    assert_gone(b, c, media_id, result)


@pytest.mark.parametrize("mode", ["delete", "cleanup"])
def test_parallel_removal_waits_for_artifact_owner_to_finish(bench, monkeypatch, mode):
    b = bench
    c, media_id = accepted(b)
    registered, release, attempting, removed = Event(), Event(), Event(), Event()
    original = ArtifactStore.record_local_file
    order = []

    def paused_record(store, **kwargs):
        result = original(store, **kwargs)
        registered.set()
        assert release.wait(5), "publication barrier was not released"
        order.append("artifact_owner_completed")
        return result

    def delete_concurrently():
        attempting.set()
        remove(b, media_id, mode)
        order.append("removal_completed")
        removed.set()

    monkeypatch.setattr(ArtifactStore, "record_local_file", paused_record)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(b.service.status, b.owner, c["commandId"])
        assert registered.wait(5)
        deletion = pool.submit(delete_concurrently)
        try:
            assert attempting.wait(5)
            assert not removed.wait(.2), "removal escaped the artifact publication lifecycle"
        finally:
            release.set()
        publication.result(timeout=5)
        deletion.result(timeout=5)
    assert order == ["artifact_owner_completed", "removal_completed"]
    assert_gone(b, c, media_id, b.service.status(b.owner, c["commandId"]))


def test_published_read_rechecks_reentrant_deletion_and_does_not_recreate(bench, monkeypatch):
    b = bench
    c, media_id = accepted(b)
    first = b.service.status(b.owner, c["commandId"])
    assert first["mediaStatus"] == "available"
    original = b.database.get_runtime_artifact

    def read_then_delete(artifact_id):
        result = original(artifact_id)
        b.service.media.delete_owned(b.owner, media_id)
        return result

    monkeypatch.setattr(b.database, "get_runtime_artifact", read_then_delete)
    assert_gone(b, c, media_id, b.service.status(b.owner, c["commandId"]))


def test_source_derivative_never_emits_agent_artifact_event_on_deleted_publication(bench, monkeypatch):
    from core.artifact_store import event_bus

    b = bench
    c, media_id = accepted(b)
    original = ArtifactStore.record_local_file

    def unexpected_event(**kwargs):
        raise AssertionError("source derivative must not emit artifact.recorded")

    def record_then_remove(store, **kwargs):
        result = original(store, **kwargs)
        b.service.media.delete_owned(b.owner, media_id)
        return result

    monkeypatch.setattr(event_bus, "create_emitter", unexpected_event)
    monkeypatch.setattr(ArtifactStore, "record_local_file", record_then_remove)
    assert_gone(b, c, media_id, b.service.status(b.owner, c["commandId"]))
