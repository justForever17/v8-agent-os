from __future__ import annotations

import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from core.creative_media_resource_authority import CreativeMediaResourceAuthorityError
from core.database import DatabaseManager
from runtimes.creative_media import runtime as runtime_module
from runtimes.creative_media.runtime import JOB_STORE_FILE, CreativeMediaRuntime
from runtimes.creative_media.store import CreativeMediaStore, CreativeMediaStoreConflict


class StorageManager:
    """Durable-shaped test backend; unlike FakeJsonStorage it must not be used for jobs."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.payloads: dict[str, dict] = {}

    def read_json(self, filename: str) -> dict:
        return copy.deepcopy(self.payloads.get(filename) or {})

    def write_json(self, filename: str, data: dict) -> None:
        self.payloads[filename] = copy.deepcopy(data)


def test_runtime_job_write_uses_sqlite_and_does_not_rewrite_jobs_json(tmp_path, monkeypatch) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    backend = StorageManager(tmp_path)
    monkeypatch.setattr(runtime_module, "storage", backend)
    runtime = CreativeMediaRuntime()
    runtime._creative_media_store = CreativeMediaStore(database)
    runtime._creative_media_store_initialized = False
    job = runtime._new_job(
        modality="image",
        adapter="fixture",
        request={"operationKind": "image.generate"},
    )

    saved = runtime._save_job(job, track_task=False)

    assert runtime._creative_media_store.get_job(saved["jobId"]).payload["jobId"] == saved["jobId"]
    assert JOB_STORE_FILE not in backend.payloads


def test_runtime_list_jobs_uses_indexed_store_filters(tmp_path, monkeypatch) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    backend = StorageManager(tmp_path)
    monkeypatch.setattr(runtime_module, "storage", backend)
    runtime = CreativeMediaRuntime()
    runtime._creative_media_store = CreativeMediaStore(database)
    runtime._creative_media_store_initialized = False
    for modality in ("image", "video"):
        job = runtime._new_job(modality=modality, adapter="fixture", request={})
        runtime._save_job(job, track_task=False)

    assert [item["modality"] for item in runtime.list_jobs(modality="video")] == ["video"]


def test_governance_snapshot_is_fixed_redacted_projection(tmp_path, monkeypatch) -> None:
    backend = StorageManager(tmp_path)
    monkeypatch.setattr(runtime_module, "storage", backend)
    runtime = CreativeMediaRuntime()
    monkeypatch.setattr(runtime, "_store_is_active", lambda: False)
    private_path = "E:/private/creative/source.png"
    private_url = "https://provider.invalid/private-result"
    raw_prompt = "RAW_PROMPT_MUST_NOT_ESCAPE"
    provider_handle = "PROVIDER_HANDLE_MUST_NOT_ESCAPE"

    monkeypatch.setattr(
        runtime,
        "list_work_orders",
        lambda: [{
            "workOrderId": "work-order-1",
            "status": "running",
            "workOrderKind": "campaign",
            "intent": "campaign",
            "title": "Safe production",
            "recipeIds": ["recipe-1"],
            "workspaceId": "workspace-1",
            "projectId": "project-1",
            "workspacePath": private_path,
            "brief": raw_prompt,
            "externalUrl": private_url,
            "updatedAt": "2026-08-18T00:00:00Z",
        }],
    )
    monkeypatch.setattr(
        runtime_module.creative_recipe_compiler,
        "list_recipes",
        lambda: [{
            "recipeId": "recipe-1",
            "modality": "music",
            "recipeKind": "score",
            "executionStatus": "compiled",
            "prompt": raw_prompt,
            "providerPrompts": {"fixture": raw_prompt},
        }],
    )
    monkeypatch.setattr(
        runtime_module.creative_recipe_compiler,
        "list_assets",
        lambda: [{
            "assetId": "asset-1",
            "role": "reference",
            "modality": "image",
            "version": 2,
            "sourcePath": private_path,
            "externalUrl": private_url,
            "lineage": {"recipeId": "recipe-1", "workOrderId": "work-order-1"},
            "sourceRefs": ["source-1", private_path],
        }],
    )
    monkeypatch.setattr(
        runtime_module.creative_recipe_compiler,
        "list_character_bibles",
        lambda: [{
            "characterBibleId": "bible-1",
            "name": "Lead",
            "version": 1,
            "referenceImages": [{"sourcePath": private_path}],
        }],
    )
    monkeypatch.setattr(
        runtime_module.creative_recipe_compiler,
        "list_keyframes",
        lambda: [{
            "keyframeId": "keyframe-1",
            "role": "first_frame",
            "recipeId": "recipe-1",
            "artifactId": "private-artifact",
            "sourcePath": private_path,
        }],
    )
    monkeypatch.setattr(
        runtime,
        "list_jobs",
        lambda: [{
            "jobId": "job-1",
            "status": "running",
            "modality": "image",
            "operationKind": "image.generate",
            "adapter": "fixture",
            "request": {
                "recipeId": "recipe-1",
                "workOrderId": "work-order-1",
                "sourceRefs": ["source-1"],
                "prompt": raw_prompt,
                "externalUrl": private_url,
            },
            "providerHandle": {"taskId": provider_handle},
            "providerResponse": {"raw": "RAW_RESPONSE_MUST_NOT_ESCAPE"},
            "artifacts": [{"sourcePath": private_path}],
        }],
    )
    monkeypatch.setattr(
        runtime,
        "list_quality_jobs",
        lambda: [{
            "qualityJobId": "quality-1",
            "qualityProfile": "storyboard_frame",
            "status": "repairable",
            "summary": "One repair is available",
            "repairAttempts": [{"sourcePath": private_path}],
            "checks": [{"rawResponse": "RAW_CHECK_MUST_NOT_ESCAPE"}],
        }],
    )
    monkeypatch.setattr(
        runtime,
        "list_cost_ledger",
        lambda: [{
            "entryId": "cost-1",
            "operationKind": "image.generate",
            "provider": "fixture",
            "artifactCount": 1,
            "usage": {"raw": "RAW_USAGE_MUST_NOT_ESCAPE"},
        }],
    )
    monkeypatch.setattr(
        runtime,
        "list_safety_events",
        lambda: [{
            "eventId": "safety-1",
            "modality": "image",
            "events": [{"kind": "prompt_rewrite", "rawPrompt": raw_prompt}],
            "sanitizedPrompt": raw_prompt,
            "createdAt": "2026-08-18T00:00:00Z",
        }],
    )

    def read_versioned_store(filename: str, key: str) -> dict:
        if filename == runtime_module.EDIT_PLAN_STORE_FILE:
            return {key: {"plan-1": {
                "planId": "plan-1",
                "recipeId": "recipe-1",
                "status": "ready",
                "lineage": {"workOrderId": "work-order-1"},
                "tracks": [{"resolvedPath": private_path}],
            }}}
        if filename == runtime_module.RENDER_JOB_STORE_FILE:
            return {key: {"render-1": {
                "renderJobId": "render-1",
                "planId": "plan-1",
                "status": "succeeded",
                "artifacts": [{"sourcePath": private_path, "externalUrl": private_url}],
                "diagnostics": {"raw": "RAW_RENDER_MUST_NOT_ESCAPE"},
            }}}
        raise AssertionError(filename)

    monkeypatch.setattr(runtime, "_read_versioned_store", read_versioned_store)

    payload = runtime.governance_snapshot()
    projections = {
        ("workOrders", "workOrders"): {
            "workOrderId", "status", "workOrderKind", "intent", "title", "recipeId",
            "recipeIds", "recipeRefs", "workspaceId", "projectId", "createdAt", "updatedAt",
        },
        ("recipes", "recipes"): {
            "recipeId", "modality", "recipeKind", "musicKind", "executionStatus", "updatedAt",
        },
        ("assets", "assets"): {
            "assetId", "role", "modality", "version", "recipeId", "workOrderId", "sourceRefs",
        },
        ("jobs", "jobs"): {
            "jobId", "status", "modality", "operationKind", "adapter", "recipeId",
            "workOrderId", "sourceRefs", "createdAt", "updatedAt",
        },
        ("characterBibles", "characterBibles"): {"characterBibleId", "name", "version"},
        ("keyframes", "keyframes"): {"keyframeId", "role", "recipeId"},
        ("editPlans", "editPlans"): {
            "planId", "recipeId", "workOrderId", "sourceRefs", "status", "updatedAt",
        },
        ("renders", "renders"): {
            "renderJobId", "planId", "status", "updatedAt", "artifactCount",
        },
        ("qualityJobs", "qualityJobs"): {
            "qualityJobId", "qualityProfile", "status", "summary", "repairCount",
            "requiredFeaturePackId",
        },
        ("costLedger", "entries"): {"entryId", "operationKind", "provider", "artifactCount"},
        ("safetyEvents", "events"): {"eventId", "eventKind", "modality", "createdAt"},
    }
    assert set(payload) == {outer for outer, _inner in projections}
    for (outer, inner), expected_keys in projections.items():
        assert len(payload[outer][inner]) == 1
        assert set(payload[outer][inner][0]) == expected_keys

    assert payload["renders"]["renders"][0]["artifactCount"] == 1
    assert payload["qualityJobs"]["qualityJobs"][0]["repairCount"] == 1
    assert payload["safetyEvents"]["events"][0]["eventKind"] == "prompt_rewrite"
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        private_path,
        private_url,
        raw_prompt,
        provider_handle,
        "RAW_RESPONSE_MUST_NOT_ESCAPE",
        "RAW_CHECK_MUST_NOT_ESCAPE",
        "RAW_USAGE_MUST_NOT_ESCAPE",
        "RAW_RENDER_MUST_NOT_ESCAPE",
    ):
        assert forbidden not in serialized


def test_runtime_migration_keeps_jobs_json_read_only_and_byte_stable(tmp_path, monkeypatch) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    backend = StorageManager(tmp_path)
    monkeypatch.setattr(runtime_module, "storage", backend)
    source = tmp_path / JOB_STORE_FILE
    source.parent.mkdir(parents=True, exist_ok=True)
    legacy_job = {
        "jobId": "legacy-job",
        "sessionId": "session-a",
        "status": "queued",
        "modality": "image",
        "adapter": "fixture",
        "createdAt": "2026-08-18T00:00:00Z",
        "updatedAt": "2026-08-18T00:00:00Z",
    }
    original = json.dumps(
        {"version": 1, "jobs": {legacy_job["jobId"]: legacy_job}},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    source.write_bytes(original)
    runtime = CreativeMediaRuntime()
    runtime._creative_media_store = CreativeMediaStore(database)
    runtime._creative_media_store_initialized = False

    updated = runtime.get_job("legacy-job", refresh=False) or {}
    updated["status"] = "running"
    runtime._save_job(updated, track_task=False)

    assert source.read_bytes() == original
    assert JOB_STORE_FILE not in backend.payloads
    assert runtime._creative_media_store_migration.source_found is True
    assert runtime._creative_media_store.get_job("legacy-job").payload["status"] == "running"


def test_runtime_authority_rejection_has_zero_provider_file_ledger_or_artifact_side_effects(
    tmp_path,
    monkeypatch,
) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    backend = StorageManager(tmp_path)
    monkeypatch.setattr(runtime_module, "storage", backend)
    runtime = CreativeMediaRuntime()
    runtime._creative_media_store = CreativeMediaStore(database)
    runtime._creative_media_store_initialized = False

    calls = {"authority": 0, "provider": 0, "file": 0, "ledger": 0, "artifact": 0, "impl": 0}

    def reject(_request):
        calls["authority"] += 1
        raise CreativeMediaResourceAuthorityError(reason_code="cross_session_artifact")

    monkeypatch.setattr(
        runtime_module,
        "creative_media_resource_authority",
        SimpleNamespace(authorize_request_resources=reject),
    )

    async def unexpected_provider(*_args, **_kwargs):
        calls["provider"] += 1
        raise AssertionError("provider must not be called before authority")

    async def unexpected_impl(*_args, **_kwargs):
        calls["impl"] += 1
        raise AssertionError("job implementation must not be called before authority")

    def unexpected_file(*_args, **_kwargs):
        calls["file"] += 1
        raise AssertionError("media file must not be opened before authority")

    def unexpected_ledger(*_args, **_kwargs):
        calls["ledger"] += 1
        raise AssertionError("cost ledger must not be written before authority")

    def unexpected_artifact(*_args, **_kwargs):
        calls["artifact"] += 1
        raise AssertionError("artifact must not be written before authority")

    monkeypatch.setattr(runtime, "_request_json", unexpected_provider)
    monkeypatch.setattr(runtime, "_create_job_impl", unexpected_impl)
    monkeypatch.setattr(runtime, "_append_cost_entry", unexpected_ledger)
    monkeypatch.setattr(runtime, "_record_post_artifact", unexpected_artifact)
    monkeypatch.setattr(Path, "open", unexpected_file)
    monkeypatch.setattr(
        runtime,
        "_canonical_owner_scope",
        lambda request, require_write=False: {
            "sessionId": str(request.get("sessionId") or ""),
            "workspaceId": str(request.get("workspaceId") or ""),
            "projectId": str(request.get("projectId") or ""),
            "workspacePath": str(request.get("workspacePath") or ""),
        },
    )

    with pytest.raises(CreativeMediaResourceAuthorityError):
        asyncio.run(
            runtime.create_job(
                {
                    "sessionId": "session-a",
                    "workspaceId": "workspace-a",
                    "modality": "video",
                    "operationKind": "video.image_to_video",
                    "referenceAssetIds": ["artifact-b"],
                    "prompt": "fixture",
                }
            )
        )

    assert calls == {
        "authority": 1,
        "provider": 0,
        "file": 0,
        "ledger": 0,
        "artifact": 0,
        "impl": 0,
    }
    assert backend.payloads == {}


def test_canvas_artifact_transport_requires_exact_session_authority(tmp_path, monkeypatch) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    backend = StorageManager(tmp_path)
    monkeypatch.setattr(runtime_module, "storage", backend)
    runtime = CreativeMediaRuntime()
    runtime._creative_media_store = CreativeMediaStore(database)
    runtime._creative_media_store_initialized = False
    invoked = 0

    def reject_artifact(**_kwargs):
        raise CreativeMediaResourceAuthorityError(reason_code="cross_session_artifact")

    monkeypatch.setattr(
        runtime_module,
        "creative_media_resource_authority",
        SimpleNamespace(
            authorize_request_resources=lambda _request: [],
            resolve_artifact=reject_artifact,
        ),
    )

    async def unexpected_impl(*_args, **_kwargs):
        nonlocal invoked
        invoked += 1
        raise AssertionError("provider implementation must not receive foreign Canvas transport")

    monkeypatch.setattr(runtime, "_create_job_impl", unexpected_impl)

    with pytest.raises(CreativeMediaResourceAuthorityError):
        asyncio.run(
            runtime.create_job(
                {
                    "sessionId": "session-a",
                    "workspaceId": "workspace-a",
                    "modality": "video",
                    "operationKind": "video.reference_to_video",
                    "canvasInputs": [
                        {"origin": "artifact", "id": "artifact-b", "mediaType": "video"}
                    ],
                }
            )
        )

    assert invoked == 0
    assert backend.payloads == {}


def test_session_tombstone_sidecar_merges_concurrent_sessions(tmp_path, monkeypatch) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    backend = StorageManager(tmp_path)
    monkeypatch.setattr(runtime_module, "storage", backend)
    runtime = CreativeMediaRuntime()
    runtime._creative_media_store = CreativeMediaStore(database)
    runtime._creative_media_store_initialized = False
    barrier = Barrier(2)

    def persist(session_id: str) -> None:
        barrier.wait(timeout=5)
        runtime._write_session_deletion_tombstones(
            {
                session_id: {
                    "schema": "v8.creative_media_session_deletion.v1",
                    "sessionId": session_id,
                    "updatedAt": "2026-08-18T00:00:00Z",
                }
            }
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(persist, ("session-a", "session-b")))

    assert set(backend.payloads[runtime_module.SESSION_DELETION_TOMBSTONE_FILE]["tombstones"]) == {
        "session-a",
        "session-b",
    }


def test_asset_keyframe_and_edit_render_reads_are_exact_session_scoped(
    tmp_path,
    monkeypatch,
) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    backend = StorageManager(tmp_path)
    monkeypatch.setattr(runtime_module, "storage", backend)
    runtime = CreativeMediaRuntime()
    runtime._creative_media_store = CreativeMediaStore(database)
    runtime._creative_media_store_initialized = False
    caller_scope = {
        "sessionId": "session-a",
        "workspaceId": "workspace-shared",
        "projectId": "project-shared",
        "workspacePath": str(tmp_path),
    }
    monkeypatch.setattr(
        runtime,
        "_canonical_owner_scope",
        lambda _request, require_write=False: dict(caller_scope),
    )
    foreign = {
        "assetId": "asset-b",
        "keyframeId": "keyframe-b",
        "sessionId": "session-b",
        "workspaceId": "workspace-shared",
        "projectId": "project-shared",
        "workspacePath": str(tmp_path),
        "modality": "video",
        "sourcePath": str(tmp_path / "foreign.mp4"),
    }
    monkeypatch.setattr(
        runtime_module.creative_recipe_compiler,
        "list_assets",
        lambda **_kwargs: [dict(foreign)],
    )
    monkeypatch.setattr(
        runtime_module.creative_recipe_compiler,
        "get_keyframe",
        lambda _keyframe_id: dict(foreign),
    )
    monkeypatch.setattr(
        runtime_module.creative_recipe_compiler,
        "list_keyframes",
        lambda **_kwargs: [dict(foreign)],
    )

    assert runtime.list_assets(session_id="session-a") == []
    assert runtime.get_keyframe("keyframe-b", session_id="session-a") is None
    assert runtime.list_keyframes(session_id="session-a") == []

    calls = {"authority": 0, "path": 0, "ffmpeg": 0, "write": 0}

    def unexpected_authority(*_args, **_kwargs):
        calls["authority"] += 1
        raise AssertionError("foreign ledger entry must be rejected before resource resolution")

    monkeypatch.setattr(runtime, "_authorize_request_resources", unexpected_authority)
    monkeypatch.setattr(
        runtime,
        "_resolve_media_path",
        lambda *_args, **_kwargs: calls.__setitem__("path", calls["path"] + 1),
    )
    monkeypatch.setattr(
        runtime,
        "_save_render_job",
        lambda *_args, **_kwargs: calls.__setitem__("write", calls["write"] + 1),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_windowless",
        lambda *_args, **_kwargs: calls.__setitem__("ffmpeg", calls["ffmpeg"] + 1),
    )

    with pytest.raises(CreativeMediaResourceAuthorityError):
        runtime.create_edit_plan({**caller_scope, "assetIds": ["asset-b"]})

    backend.payloads[runtime_module.EDIT_PLAN_STORE_FILE] = {
        "version": 1,
        "editPlans": {
            "plan-b": {
                "planId": "plan-b",
                **foreign,
                "tracks": {"video": []},
            }
        },
    }
    with pytest.raises(CreativeMediaResourceAuthorityError):
        runtime.render_edit_plan({**caller_scope, "planId": "plan-b"})

    assert calls == {"authority": 0, "path": 0, "ffmpeg": 0, "write": 0}


def test_terminal_observation_outbox_is_atomic_and_replay_is_idempotent(
    tmp_path,
    monkeypatch,
) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    backend = StorageManager(tmp_path)
    monkeypatch.setattr(runtime_module, "storage", backend)
    runtime = CreativeMediaRuntime()
    store = CreativeMediaStore(database)
    runtime._creative_media_store = store
    runtime._creative_media_store_initialized = False
    scope = {
        "sessionId": "session-a",
        "workspaceId": "workspace-a",
        "projectId": "project-a",
        "workspacePath": str(tmp_path),
    }
    monkeypatch.setattr(
        runtime,
        "_canonical_owner_scope",
        lambda _request, require_write=False: dict(scope),
    )
    quality_calls = 0

    def deterministic_quality(request: dict) -> dict:
        nonlocal quality_calls
        quality_calls += 1
        quality_job_id = str(request["qualityJobId"])
        existing = store.get_quality_job(quality_job_id)
        if existing:
            return existing
        return store.save_quality_job(
            {
                "qualityJobId": quality_job_id,
                "jobId": request["jobId"],
                **scope,
                "status": "passed",
                "createdAt": "2026-08-18T00:00:00Z",
                "updatedAt": "2026-08-18T00:00:00Z",
            }
        )

    monkeypatch.setattr(runtime, "create_quality_job", deterministic_quality)
    job = runtime._new_job(
        modality="image",
        adapter="fixture",
        request={**scope, "operationKind": "image.generate"},
    )
    runtime._save_job(job, track_task=False)

    original_compare = store.compare_and_swap_job

    def fail_terminal_commit(*args, **kwargs):
        if str((kwargs.get("payload") or {}).get("status")) == "succeeded":
            raise CreativeMediaStoreConflict("injected primary CAS failure")
        return original_compare(*args, **kwargs)

    monkeypatch.setattr(store, "compare_and_swap_job", fail_terminal_commit)
    job["status"] = "succeeded"
    job["artifacts"] = [{"artifactId": "artifact-a"}]
    with pytest.raises(CreativeMediaStoreConflict, match="primary CAS failure"):
        runtime._save_job(job, track_task=False)
    assert store.list_cost_entries(session_id="session-a") == []
    assert store.list_quality_jobs(session_id="session-a") == []
    assert store.list_pending_terminal_observations(job_id=job["jobId"]) == []

    persisted = runtime.get_job(job["jobId"], refresh=False) or {}
    persisted["status"] = "succeeded"
    persisted["artifacts"] = [{"artifactId": "artifact-a"}]

    def fail_ack(*args, **kwargs):
        if kwargs.get("complete_terminal_observation"):
            raise CreativeMediaStoreConflict("injected ack loss")
        return original_compare(*args, **kwargs)

    monkeypatch.setattr(store, "compare_and_swap_job", fail_ack)
    runtime._save_job(persisted, track_task=False)
    assert len(store.list_cost_entries(session_id="session-a")) == 1
    assert len(store.list_quality_jobs(session_id="session-a")) == 1
    assert len(store.list_pending_terminal_observations(job_id=job["jobId"])) == 1
    assert quality_calls == 1

    monkeypatch.setattr(store, "compare_and_swap_job", original_compare)
    repaired = runtime.repair_terminal_observation_outbox(job_id=job["jobId"])
    assert repaired == {"processed": 1, "completed": 1, "failed": 0}
    assert len(store.list_cost_entries(session_id="session-a")) == 1
    assert len(store.list_quality_jobs(session_id="session-a")) == 1
    assert store.list_pending_terminal_observations(job_id=job["jobId"]) == []
    assert quality_calls == 1
