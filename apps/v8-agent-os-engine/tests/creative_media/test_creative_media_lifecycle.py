from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from core.creative_media_resource_authority import CreativeMediaResourceAuthorityError
from runtimes.creative_media import runtime as runtime_module
from runtimes.creative_media.runtime import JOB_STORE_FILE, CreativeMediaRuntime


class FakeJsonStorage:
    def __init__(self) -> None:
        self.payloads: dict[str, dict] = {}
        self.base_dir = None

    def read_json(self, filename: str) -> dict:
        return deepcopy(self.payloads.get(filename) or {})

    def write_json(self, filename: str, data: dict) -> None:
        self.payloads[filename] = deepcopy(data)


@pytest.fixture()
def lifecycle_runtime(monkeypatch: pytest.MonkeyPatch) -> tuple[CreativeMediaRuntime, FakeJsonStorage]:
    fake_storage = FakeJsonStorage()
    monkeypatch.setattr(runtime_module, "storage", fake_storage)
    runtime = CreativeMediaRuntime()
    monkeypatch.setattr(runtime, "_record_terminal_job_observations", lambda _job, _marker: {})
    return runtime, fake_storage


def _provider_job(
    runtime: CreativeMediaRuntime,
    *,
    adapter: str,
    status: str = "running",
    task_id: str = "provider-task-1",
    provider_id: str = "provider-a",
    base_url: str = "https://provider.test/api/v1",
) -> dict:
    job = runtime._new_job(
        modality="video",
        adapter=adapter,
        request={
            "operationKind": "video.text_to_video",
            "endpointBinding": {"baseUrl": base_url, "adapter": adapter, "providerId": provider_id},
        },
    )
    job["status"] = status
    job["providerTaskId"] = task_id
    job["providerResponse"] = {
        "providerId": provider_id,
        "taskId": task_id,
        "model": "provider-model",
    }
    return runtime._save_job(job)


def test_provider_handle_is_persisted_with_recoverable_job_state(lifecycle_runtime) -> None:
    runtime, fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="task/with-id")

    assert job["providerHandle"] == {
        "schema": "v8.creative_media_provider_handle.v1",
        "adapter": "minimax_video",
        "providerId": "provider-a",
        "taskId": "task/with-id",
        "operationKind": "video.text_to_video",
    }
    assert fake_storage.payloads[JOB_STORE_FILE]["jobs"][job["jobId"]]["providerHandle"] == job["providerHandle"]


def test_terminal_provider_job_without_proof_schedules_remote_reconciliation(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="minimax_video",
        status="failed",
        task_id="terminal-provider-unproved",
    )
    job["sessionId"] = "session-terminal-provider"
    job["canvasGraphRunId"] = "canvas-run-terminal-provider"
    job["canvasGraphNodeId"] = "action-terminal-provider"
    runtime._save_job(job, track_task=False)

    report = asyncio.run(runtime.cancel_job(job["jobId"]))
    stored = runtime.get_job(job["jobId"], refresh=False)

    assert report["status"] == "not_active"
    assert report["detailCode"] == "provider_terminal_reconciliation_scheduled"
    assert report["remoteTaskMayContinue"] is True
    assert report.get("terminalProof") is None
    assert stored["lifecycle"]["remoteReconcile"]["status"] == "pending"
    assert stored["lifecycle"]["remoteReconcile"]["remoteTaskMayContinue"] is True
    assert stored["lifecycle"]["remoteReconcile"]["nextReconcileAt"]


def test_terminal_governed_local_job_emits_strong_local_terminal_proof(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = runtime._new_job(
        modality="video",
        adapter="governed_local",
        request={"operationKind": "video.trim_exact"},
    )
    job["status"] = "failed"
    job["error"] = "local trim fixture failed"
    runtime._save_job(job, track_task=False)

    report = asyncio.run(runtime.cancel_job(job["jobId"]))
    stored = runtime.get_job(job["jobId"], refresh=False)

    assert report["status"] == "not_active"
    assert report["detailCode"] == "governed_local_job_already_terminal"
    assert report["remoteTaskMayContinue"] is False
    assert report["terminalProof"] == {
        "schema": "v8.creative_media_local_terminal_proof.v1",
        "jobId": job["jobId"],
        "status": "failed",
        "source": "governed_local_job_state",
        "observedAt": report["terminalProof"]["observedAt"],
    }
    assert stored["lifecycle"]["cancel"]["terminalProof"] == report["terminalProof"]
    assert "remoteReconcile" not in stored["lifecycle"]


def test_cancel_lifecycle_is_singleflight_and_shielded_from_caller_cancel(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="singleflight-task")
    provider_started = asyncio.Event()
    release_provider = asyncio.Event()
    calls = 0

    async def slow_provider_cancel(_job: dict) -> dict:
        nonlocal calls
        calls += 1
        provider_started.set()
        await release_provider.wait()
        return {
            "status": "completed",
            "detailCode": "singleflight_cancelled",
            "remoteTaskMayContinue": False,
        }

    monkeypatch.setattr(runtime, "_cancel_provider_job", slow_provider_cancel)

    async def scenario() -> dict:
        first = asyncio.create_task(runtime.cancel_job(job["jobId"]))
        await provider_started.wait()
        second = asyncio.create_task(runtime.cancel_job(job["jobId"]))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release_provider.set()
        return await second

    report = asyncio.run(scenario())
    assert report["status"] == "completed"
    assert calls == 1
    assert runtime.get_job(job["jobId"], refresh=False)["status"] == "cancelled"


def test_canvas_cleanup_preserves_graph_owner_until_explicit_route_cancel(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="canvas-owner-task")
    job["canvasGraphRunId"] = "canvas-run-owner"
    runtime._save_job(job)

    async def scenario() -> tuple[dict, bool]:
        started = asyncio.Event()

        async def owner() -> None:
            started.set()
            await asyncio.Event().wait()

        owner_task = asyncio.create_task(owner())
        await started.wait()
        runtime._register_job_resource(job["jobId"], "task", owner_task)
        await runtime.cancel_job(job["jobId"])
        report = await runtime.cleanup_job(job["jobId"])
        owner_was_cancelled = owner_task.cancelled()
        owner_task.cancel()
        await asyncio.gather(owner_task, return_exceptions=True)
        return report, owner_was_cancelled

    report, owner_was_cancelled = asyncio.run(scenario())
    assert report["status"] == "not_active"
    assert report["resources"]["tasks"] == [{"status": "not_active", "action": "preserved_graph_owner"}]
    assert owner_was_cancelled is False


def test_canvas_cleanup_preserves_graph_owner_when_provider_failed(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="agnes_images", status="failed", task_id="failed-image-task")
    job["canvasGraphRunId"] = "canvas-run-provider-failed"
    runtime._save_job(job)

    async def scenario() -> tuple[dict, bool]:
        started = asyncio.Event()

        async def owner() -> None:
            started.set()
            await asyncio.Event().wait()

        owner_task = asyncio.create_task(owner())
        await started.wait()
        runtime._register_job_resource(job["jobId"], "task", owner_task)
        report = await runtime.cleanup_job(job["jobId"])
        owner_was_cancelled = owner_task.cancelled()
        owner_task.cancel()
        await asyncio.gather(owner_task, return_exceptions=True)
        return report, owner_was_cancelled

    report, owner_was_cancelled = asyncio.run(scenario())
    assert report["status"] == "not_active"
    assert report["resources"]["tasks"] == [{"status": "not_active", "action": "preserved_graph_owner"}]
    assert owner_was_cancelled is False


def test_reserved_job_id_is_internal_and_reusable_for_one_fallback_chain(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    reserved_job_id = "cm_22222222222222222222222222222222"
    job = runtime._new_job(
        modality="video",
        adapter="volcengine_ark",
        request={
            "operationKind": "video.text_to_video",
            "_reservedJobId": reserved_job_id,
        },
    )

    assert job["jobId"] == reserved_job_id
    assert "_reservedJobId" not in job["request"]
    runtime._save_job(job)
    fallback = runtime._new_job(
        modality="video",
        adapter="agnes_video",
        request={
            "operationKind": "video.text_to_video",
            "_reservedJobId": reserved_job_id,
        },
    )
    assert fallback["jobId"] == reserved_job_id
    assert fallback["adapter"] == "agnes_video"


def test_create_job_preserves_public_reserved_job_id_without_request_leak(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    reserved_job_id = "cm_33333333333333333333333333333333"

    job = asyncio.run(
        runtime.create_job(
            {
                "modality": "image",
                "operationKind": "image.generate",
                "adapter": "unbound-fixture",
            },
            reserved_job_id=reserved_job_id,
        )
    )

    assert job["jobId"] == reserved_job_id
    assert "_reservedJobId" not in job["request"]
    assert runtime.get_job(reserved_job_id, refresh=False)["jobId"] == reserved_job_id


def test_volcengine_cancel_calls_bound_delete_route(lifecycle_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="volcengine_ark",
        task_id="task/one",
        provider_id="volcengine_seedance",
        base_url="https://ark.example/api/v3",
    )
    monkeypatch.setattr(
        runtime,
        "_configured_endpoint_binding",
        lambda *_args, **_kwargs: {
            "providerId": "volcengine_seedance",
            "providerMeta": {"api_key": "secret"},
            "baseUrl": "https://new-config.example/api/v3",
        },
    )
    monkeypatch.setattr(runtime, "_volc_credentials", lambda: {"apiKey": "", "baseUrl": "", "videoModel": "seedance-test"})
    calls: list[dict] = []

    async def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return {}

    monkeypatch.setattr(runtime, "_request_json", fake_request)
    report = asyncio.run(runtime.cancel_job(job["jobId"]))

    assert report["status"] == "completed"
    assert report["detailCode"] == "volcengine_cancel_request_accepted"
    assert report["remoteTaskMayContinue"] is True
    assert report.get("terminalProof") is None
    assert calls == [
        {
            "method": "DELETE",
            "url": "https://ark.example/api/v3/contents/generations/tasks/task%2Fone",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer secret"},
            "timeout": 60,
        }
    ]
    stored = runtime.get_job(job["jobId"], refresh=False)
    assert stored["status"] == "cancelled"
    assert stored["lifecycle"]["cancel"] == report


def test_dashscope_only_calls_cancel_for_pending_task(lifecycle_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _fake_storage = lifecycle_runtime
    pending = _provider_job(
        runtime,
        adapter="dashscope",
        status="queued",
        task_id="dash-pending",
        provider_id="aliyun_bailian_dashscope",
        base_url="https://dashscope.example/api/v1",
    )
    running = _provider_job(
        runtime,
        adapter="dashscope",
        status="running",
        task_id="dash-running",
        provider_id="aliyun_bailian_dashscope",
        base_url="https://dashscope.example/api/v1",
    )
    monkeypatch.setattr(
        runtime,
        "_configured_endpoint_binding",
        lambda *_args, **_kwargs: {
            "providerId": "aliyun_bailian_dashscope",
            "providerMeta": {"api_key": "secret"},
            "baseUrl": "https://new-config.example/api/v1",
        },
    )
    monkeypatch.setattr(
        runtime,
        "_dashscope_credentials",
        lambda: {"apiKey": "secret", "baseUrl": "https://fallback.invalid/api/v1"},
    )
    calls: list[tuple[str, str]] = []

    async def fake_request(method, url, **_kwargs):
        calls.append((method, url))
        return {"output": {"task_status": "CANCELED"}}

    monkeypatch.setattr(runtime, "_request_json", fake_request)
    pending_report = asyncio.run(runtime.cancel_job(pending["jobId"]))
    running_report = asyncio.run(runtime.cancel_job(running["jobId"]))

    assert pending_report["status"] == "completed"
    assert pending_report["detailCode"] == "dashscope_pending_cancel_request_accepted"
    assert pending_report["remoteTaskMayContinue"] is False
    assert pending_report["providerStatus"] == "cancelled"
    assert pending_report["providerStatusRaw"] == "canceled"
    assert pending_report["terminalProof"] == {
        "schema": "v8.creative_media_remote_terminal_proof.v1",
        "source": "dashscope_cancel_response",
        "providerHandle": pending["providerHandle"],
        "providerStatus": "cancelled",
        "observedAt": pending_report["completedAt"],
    }
    assert running_report["status"] == "unsupported"
    assert running_report["detailCode"] == "dashscope_only_cancels_pending_tasks"
    assert running_report["remoteTaskMayContinue"] is True
    assert calls == [("POST", "https://dashscope.example/api/v1/tasks/dash-pending/cancel")]


def test_comfyui_modern_job_cancel_dispatches_by_prompt_id(lifecycle_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="comfyui_workflow",
        task_id="prompt-target",
        provider_id="comfyui-local",
        base_url="http://127.0.0.1:8188",
    )
    monkeypatch.setattr(runtime, "_configured_endpoint_binding", lambda *_args, **_kwargs: {})
    calls: list[tuple[str, str]] = []

    async def fake_request(method, url, **_kwargs):
        calls.append((method, url))
        return {"cancelled": True}

    monkeypatch.setattr(runtime, "_request_json", fake_request)
    report = asyncio.run(runtime.cancel_job(job["jobId"]))

    assert report["status"] == "completed"
    assert report["detailCode"] == "comfyui_job_cancel_event_dispatched"
    assert report["remoteTaskMayContinue"] is True
    assert calls == [("POST", "http://127.0.0.1:8188/api/jobs/prompt-target/cancel")]


def test_comfyui_legacy_pending_prompt_uses_task_specific_queue_delete(lifecycle_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="comfyui_workflow",
        task_id="prompt-pending",
        provider_id="comfyui-local",
        base_url="http://127.0.0.1:8188",
    )
    monkeypatch.setattr(runtime, "_configured_endpoint_binding", lambda *_args, **_kwargs: {})
    calls: list[dict] = []

    async def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("/api/jobs/prompt-pending/cancel"):
            raise RuntimeError("Provider request failed (404) at job cancel")
        if method == "GET" and url.endswith("/queue"):
            return {"queue_pending": [[4, "prompt-pending", {}]], "queue_running": []}
        return {}

    monkeypatch.setattr(runtime, "_request_json", fake_request)
    report = asyncio.run(runtime.cancel_job(job["jobId"]))

    assert report["status"] == "completed"
    assert report["detailCode"] == "comfyui_pending_prompt_deleted"
    assert report["remoteTaskMayContinue"] is True
    assert [(call["method"], call["url"]) for call in calls] == [
        ("POST", "http://127.0.0.1:8188/api/jobs/prompt-pending/cancel"),
        ("GET", "http://127.0.0.1:8188/queue"),
        ("POST", "http://127.0.0.1:8188/queue"),
    ]
    assert calls[2]["json"] == {"delete": ["prompt-pending"]}


def test_comfyui_legacy_running_prompt_never_uses_global_interrupt(lifecycle_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="comfyui_workflow",
        task_id="prompt-target",
        provider_id="comfyui-local",
        base_url="http://127.0.0.1:8188",
    )
    monkeypatch.setattr(runtime, "_configured_endpoint_binding", lambda *_args, **_kwargs: {})
    calls: list[tuple[str, str]] = []

    async def fake_request(method, url, **_kwargs):
        calls.append((method, url))
        if url.endswith("/api/jobs/prompt-target/cancel"):
            raise RuntimeError("Provider request failed (405) at job cancel")
        if method == "GET":
            return {"queue_pending": [], "queue_running": [[1, "prompt-target", {}]]}
        return {}

    monkeypatch.setattr(runtime, "_request_json", fake_request)
    report = asyncio.run(runtime.cancel_job(job["jobId"]))

    assert report["status"] == "unsupported"
    assert report["detailCode"] == "comfyui_job_cancel_endpoint_unavailable"
    assert report["remoteTaskMayContinue"] is True
    assert calls == [
        ("POST", "http://127.0.0.1:8188/api/jobs/prompt-target/cancel"),
        ("GET", "http://127.0.0.1:8188/queue"),
    ]


def test_comfyui_cancelled_false_does_not_claim_remote_stop(lifecycle_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="comfyui_workflow",
        task_id="prompt-noop",
        provider_id="comfyui-local",
        base_url="http://127.0.0.1:8188",
    )
    monkeypatch.setattr(runtime, "_configured_endpoint_binding", lambda *_args, **_kwargs: {})

    async def fake_request(*_args, **_kwargs):
        return {"cancelled": False}

    monkeypatch.setattr(runtime, "_request_json", fake_request)
    report = asyncio.run(runtime.cancel_job(job["jobId"]))

    assert report["status"] == "not_active"
    assert report["detailCode"] == "comfyui_job_cancel_noop"
    assert report["remoteTaskMayContinue"] is True


def test_provider_without_cancel_api_stays_explicitly_unsupported(lifecycle_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="minimax-task")
    called = False

    async def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(runtime, "_request_json", fail_if_called)
    report = asyncio.run(runtime.cancel_job(job["jobId"]))

    assert report["status"] == "unsupported"
    assert report["detailCode"] == "minimax_video_cancel_not_supported"
    assert report["remoteTaskMayContinue"] is True
    assert called is False
    stored = runtime.get_job(job["jobId"], refresh=False)
    assert stored["status"] == "cancelled"
    assert stored["lifecycle"]["cancel"]["status"] == "unsupported"


def test_provider_cancel_failure_is_observable_and_does_not_claim_remote_stop(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="volcengine_ark",
        provider_id="volcengine_seedance",
        base_url="https://ark.example/api/v3",
    )
    monkeypatch.setattr(
        runtime,
        "_configured_endpoint_binding",
        lambda *_args, **_kwargs: {
            "providerId": "volcengine_seedance",
            "providerMeta": {"api_key": "secret"},
            "baseUrl": "https://ark.example/api/v3",
        },
    )
    monkeypatch.setattr(runtime, "_volc_credentials", lambda: {"apiKey": "", "baseUrl": "", "videoModel": "seedance-test"})

    async def fail_request(*_args, **_kwargs):
        raise RuntimeError("provider rejected cancellation")

    monkeypatch.setattr(runtime, "_request_json", fail_request)
    report = asyncio.run(runtime.cancel_job(job["jobId"]))

    assert report["status"] == "failed"
    assert report["detailCode"] == "provider_cancel_failed"
    assert report["remoteTaskMayContinue"] is True
    assert report["error"].endswith("provider rejected cancellation")
    assert runtime.get_job(job["jobId"], refresh=False)["status"] == "cancelled"


def test_volcengine_running_task_409_is_explicitly_not_cancellable(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="volcengine_ark",
        provider_id="volcengine_seedance",
        base_url="https://ark.example/api/v3",
    )
    monkeypatch.setattr(
        runtime,
        "_configured_endpoint_binding",
        lambda *_args, **_kwargs: {
            "providerId": "volcengine_seedance",
            "providerMeta": {"api_key": "secret"},
            "baseUrl": "https://ark.example/api/v3",
        },
    )
    monkeypatch.setattr(runtime, "_volc_credentials", lambda: {"apiKey": "", "baseUrl": "", "videoModel": "seedance-test"})

    async def reject_running_delete(*_args, **_kwargs):
        raise RuntimeError(
            "Provider request failed (409) at https://ark.example/api/v3/contents/generations/tasks/provider-task-1: "
            "InvalidAction.RunningTaskDeletion: Cannot delete task because it is currently running"
        )

    monkeypatch.setattr(runtime, "_request_json", reject_running_delete)
    report = asyncio.run(runtime.cancel_job(job["jobId"]))

    assert report["status"] == "unsupported"
    assert report["detailCode"] == "volcengine_running_task_not_cancellable"
    assert report["remoteTaskMayContinue"] is True
    assert runtime.get_job(job["jobId"], refresh=False)["status"] == "cancelled"


def test_recovered_custom_provider_does_not_receive_default_credentials(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="volcengine_ark",
        provider_id="custom-seedance",
        base_url="https://custom-provider.example/api/v3",
    )

    def missing_binding(*_args, **_kwargs):
        raise ValueError("configured provider was removed")

    monkeypatch.setattr(runtime, "_configured_endpoint_binding", missing_binding)
    monkeypatch.setattr(
        runtime,
        "_volc_credentials",
        lambda: {"apiKey": "default-secret", "baseUrl": "https://ark.default/api/v3", "videoModel": "seedance-test"},
    )
    called = False

    async def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(runtime, "_request_json", fail_if_called)
    report = asyncio.run(runtime.cancel_job(job["jobId"]))

    assert report["status"] == "failed"
    assert report["detailCode"] == "provider_cancel_failed"
    assert report["remoteTaskMayContinue"] is True
    assert called is False


def test_cleanup_cancels_task_terminates_process_and_releases_lease(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="cleanup-task")

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 0

        def wait(self, *, timeout: float):
            assert timeout == 3.0
            return self.returncode

    class FakeLease:
        def __init__(self) -> None:
            self.released = False

        async def release(self) -> None:
            self.released = True

    process = FakeProcess()
    lease = FakeLease()

    async def scenario() -> tuple[dict, asyncio.Task]:
        started = asyncio.Event()

        async def owner() -> None:
            started.set()
            await asyncio.Event().wait()

        owner_task = asyncio.create_task(owner())
        await started.wait()
        runtime._register_job_resource(job["jobId"], "task", owner_task)
        runtime._register_job_resource(job["jobId"], "process", process)
        runtime._register_job_resource(job["jobId"], "lease", lease)
        return await runtime.cleanup_job(job["jobId"]), owner_task

    report, owner_task = asyncio.run(scenario())

    assert report["status"] == "completed"
    assert report["detailCode"] == "local_resources_released"
    assert report["resources"]["tasks"] == [{"status": "completed", "action": "cancelled"}]
    assert report["resources"]["processes"] == [{"status": "completed", "action": "terminated"}]
    assert report["resources"]["leases"] == [{"status": "completed", "action": "release"}]
    assert owner_task.cancelled() is True
    assert process.terminated is True
    assert lease.released is True


def test_late_provider_poll_cannot_resurrect_cancelled_job(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="late-task")
    asyncio.run(runtime.cancel_job(job["jobId"]))

    stale_poll_result = dict(job)
    stale_poll_result["status"] = "succeeded"
    stale_poll_result["completedAt"] = "later-provider-write"
    saved = runtime._save_job(stale_poll_result)

    assert saved["status"] == "cancelled"
    assert saved["lifecycle"]["cancel"]["status"] == "unsupported"
    assert saved["completedAt"] != "later-provider-write"


def _cancelled_uncertain_canvas_job(
    runtime: CreativeMediaRuntime,
    *,
    adapter: str = "minimax_video",
    task_id: str = "remote-reconcile-task",
) -> dict:
    job = _provider_job(runtime, adapter=adapter, task_id=task_id)
    job["sessionId"] = "session-reconcile"
    job["canvasGraphRunId"] = "canvas-run-reconcile"
    job["canvasGraphNodeId"] = "action-reconcile"
    runtime._save_job(job, track_task=False)
    asyncio.run(runtime.cancel_job(job["jobId"]))
    return runtime.get_job(job["jobId"], refresh=False)


def test_uncertain_canvas_cancel_persists_remote_reconcile_schedule(lifecycle_runtime) -> None:
    runtime, fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime)

    report = job["lifecycle"]["remoteReconcile"]
    assert report == fake_storage.payloads[JOB_STORE_FILE]["jobs"][job["jobId"]]["lifecycle"]["remoteReconcile"]
    assert report["schema"] == "v8.creative_media_remote_reconcile.v1"
    assert report["jobId"] == job["jobId"]
    assert report["sessionId"] == "session-reconcile"
    assert report["canvasGraphRunId"] == "canvas-run-reconcile"
    assert report["canvasGraphNodeId"] == "action-reconcile"
    assert report["status"] == "pending"
    assert report["detailCode"] == "remote_terminal_proof_required"
    assert report["remoteTaskMayContinue"] is True
    assert report["attempt"] == 0
    assert report["nextReconcileAt"]
    assert report["terminalProof"] is None
    assert report["providerHandle"]["taskId"] == "remote-reconcile-task"


def test_remote_reconcile_keeps_active_provider_task_uncertain(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime)

    async def running_probe(_job: dict) -> dict:
        return {
            "providerStatus": "running",
            "providerStatusRaw": "processing",
            "source": "provider_test_status",
        }

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", running_probe)
    report = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))

    assert report["status"] == "waiting"
    assert report["detailCode"] == "provider_task_still_active"
    assert report["providerStatus"] == "running"
    assert report["remoteTaskMayContinue"] is True
    assert report["attempt"] == 1
    assert report["reconciledAt"]
    assert report["nextReconcileAt"]
    assert report["terminalProof"] is None
    stored = runtime.get_job(job["jobId"], refresh=False)
    assert stored["status"] == "cancelled"
    assert stored["lifecycle"]["cancel"]["remoteTaskMayContinue"] is True


@pytest.mark.parametrize("provider_status", ["cancelled", "failed", "succeeded"])
def test_remote_reconcile_requires_explicit_terminal_status_before_clearing_uncertainty(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
    provider_status: str,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime, task_id=f"terminal-{provider_status}")

    async def terminal_probe(_job: dict) -> dict:
        return {
            "providerStatus": provider_status,
            "providerStatusRaw": provider_status.upper(),
            "source": "provider_test_status",
        }

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", terminal_probe)
    report = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))

    assert report["status"] == "resolved"
    assert report["detailCode"] == "provider_terminal_status_confirmed"
    assert report["providerStatus"] == provider_status
    assert report["remoteTaskMayContinue"] is False
    assert report["nextReconcileAt"] is None
    assert report["terminalProof"] == {
        "schema": "v8.creative_media_remote_terminal_proof.v1",
        "source": "provider_test_status",
        "providerHandle": job["providerHandle"],
        "providerStatus": provider_status,
        "observedAt": report["reconciledAt"],
    }
    stored = runtime.get_job(job["jobId"], refresh=False)
    assert stored["status"] == "cancelled"
    assert stored["lifecycle"]["cancel"]["remoteTaskMayContinue"] is False
    assert stored["lifecycle"]["cancel"]["terminalProof"] == report["terminalProof"]


def test_remote_reconcile_failure_remains_uncertain_and_restart_scan_recovers(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime, task_id="restart-reconcile")

    async def unreachable_probe(_job: dict) -> dict:
        raise RuntimeError("provider is unreachable")

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", unreachable_probe)
    failed = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))
    assert failed["detailCode"] == "provider_status_check_failed"
    assert failed["remoteTaskMayContinue"] is True
    assert failed["nextReconcileAt"]

    recovered_runtime = CreativeMediaRuntime()
    monkeypatch.setattr(recovered_runtime, "_record_terminal_job_observations", lambda _job: None)

    async def recovered_probe(_job: dict) -> dict:
        return {
            "providerStatus": "cancelled",
            "providerStatusRaw": "CANCELED",
            "source": "provider_restart_status",
        }

    monkeypatch.setattr(recovered_runtime, "_probe_provider_remote_status", recovered_probe)
    summary = asyncio.run(recovered_runtime.reconcile_remote_jobs(force=True))

    assert summary["checked"] == 1
    assert summary["resolved"] == 1
    assert summary["uncertain"] == 0
    assert summary["reports"][0]["jobId"] == job["jobId"]
    assert summary["reports"][0]["terminalProof"]["source"] == "provider_restart_status"
    stored = fake_storage.payloads[JOB_STORE_FILE]["jobs"][job["jobId"]]
    assert stored["lifecycle"]["cancel"]["remoteTaskMayContinue"] is False
    assert recovered_runtime.list_remote_reconcile_reports(remote_task_may_continue=False)[0]["jobId"] == job["jobId"]


def test_restart_recovery_discovers_active_canvas_provider_orphan_only_when_explicit(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="restart-active-provider")
    job["sessionId"] = "session-restarted"
    job["canvasGraphRunId"] = "canvas-run-restarted"
    job["canvasGraphNodeId"] = "action-restarted"
    runtime._save_job(job, track_task=False)

    recovered_runtime = CreativeMediaRuntime()
    monkeypatch.setattr(recovered_runtime, "_record_terminal_job_observations", lambda _job: None)
    ordinary_cycle = asyncio.run(recovered_runtime.reconcile_remote_jobs())
    assert ordinary_cycle["checked"] == 0
    assert ordinary_cycle["recoveredOrphans"] == 0
    assert recovered_runtime.get_job(job["jobId"], refresh=False).get("lifecycle") is None

    async def terminal_probe(_job: dict) -> dict:
        return {
            "providerStatus": "failed",
            "providerStatusRaw": "FAILED",
            "source": "provider_restart_orphan_status",
        }

    monkeypatch.setattr(recovered_runtime, "_probe_provider_remote_status", terminal_probe)
    recovered_cycle = asyncio.run(
        recovered_runtime.reconcile_remote_jobs(
            recovery_candidates=[
                {
                    "sessionId": "session-restarted",
                    "graphRunId": "canvas-run-restarted",
                    "nodeId": "action-restarted",
                    "jobId": job["jobId"],
                }
            ]
        )
    )

    assert recovered_cycle["recoveredOrphans"] == 1
    assert recovered_cycle["checked"] == 1
    assert recovered_cycle["resolved"] == 1
    report = recovered_cycle["reports"][0]
    assert report["jobId"] == job["jobId"]
    assert report["canvasGraphRunId"] == "canvas-run-restarted"
    assert report["canvasGraphNodeId"] == "action-restarted"
    assert report["remoteTaskMayContinue"] is False
    assert report["terminalProof"]["providerStatus"] == "failed"
    assert report["terminalProof"]["source"] == "provider_restart_orphan_status"


def test_archived_canvas_provider_orphan_is_reconciled_without_broad_active_scan(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="archived-provider-orphan")
    job["sessionId"] = "session-archived-orphan"
    job["canvasGraphRunId"] = "canvas-run-archived-orphan"
    job["canvasGraphNodeId"] = "action-archived-orphan"
    job["status"] = "archived"
    job["archivedAt"] = "2026-08-16T00:00:00Z"
    runtime._save_job(job, track_task=False)

    async def terminal_probe(_job: dict) -> dict:
        return {
            "providerStatus": "failed",
            "providerStatusRaw": "-1",
            "source": "archived_orphan_status",
        }

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", terminal_probe)
    summary = asyncio.run(runtime.reconcile_remote_jobs())

    assert summary["recoveredOrphans"] == 1
    assert summary["checked"] == 1
    assert summary["resolved"] == 1
    report = summary["reports"][0]
    assert report["detailCode"] == "provider_terminal_status_confirmed"
    assert report["terminalProof"]["providerStatus"] == "failed"


def test_archived_canvas_provider_orphan_with_terminal_proof_is_not_rescanned(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime, task_id="archived-terminal-proof")
    stored = fake_storage.payloads[JOB_STORE_FILE]["jobs"][job["jobId"]]
    stored["status"] = "archived"
    stored["archivedAt"] = "2026-08-16T00:00:00Z"

    async def terminal_probe(_job: dict) -> dict:
        return {
            "providerStatus": "cancelled",
            "providerStatusRaw": "CANCELED",
            "source": "archived_terminal_status",
        }

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", terminal_probe)
    first = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))
    assert first["remoteTaskMayContinue"] is False
    calls = 0

    async def should_not_probe(_job: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"providerStatus": "running", "source": "unexpected"}

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", should_not_probe)
    summary = asyncio.run(runtime.reconcile_remote_jobs(force=True))
    assert summary["checked"] == 0
    assert calls == 0


def test_remote_reconcile_is_singleflight_and_shielded(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime, task_id="reconcile-singleflight")
    calls = 0

    async def scenario() -> tuple[dict, dict]:
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_probe(_job: dict) -> dict:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {
                "providerStatus": "running",
                "providerStatusRaw": "RUNNING",
                "source": "provider_singleflight_status",
            }

        monkeypatch.setattr(runtime, "_probe_provider_remote_status", slow_probe)
        first = asyncio.create_task(runtime.reconcile_remote_job(job["jobId"], force=True))
        await started.wait()
        second = asyncio.create_task(runtime.reconcile_remote_job(job["jobId"], force=True))
        await asyncio.sleep(0)
        first.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        return await second, runtime.get_job(job["jobId"], refresh=False)["lifecycle"]["remoteReconcile"]

    report, stored_report = asyncio.run(scenario())
    assert calls == 1
    assert report == stored_report
    assert report["attempt"] == 1


def test_resolved_remote_reconcile_is_idempotent_without_another_provider_call(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime, task_id="reconcile-idempotent")
    calls = 0

    async def terminal_probe(_job: dict) -> dict:
        nonlocal calls
        calls += 1
        return {
            "providerStatus": "cancelled",
            "providerStatusRaw": "cancelled",
            "source": "provider_idempotent_status",
        }

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", terminal_probe)
    first = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))
    second = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))

    assert calls == 1
    assert second == first
    assert second["remoteTaskMayContinue"] is False
    assert second["terminalProof"] is not None


def test_remote_reconciler_monitor_runs_due_scan_and_stops_cleanly(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime, task_id="monitor-reconcile")
    observed_cycles: list[dict] = []

    async def terminal_probe(_job: dict) -> dict:
        return {
            "providerStatus": "cancelled",
            "providerStatusRaw": "cancelled",
            "source": "provider_monitor_status",
        }

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", terminal_probe)

    async def scenario() -> dict:
        cycle_seen = asyncio.Event()

        def on_cycle(summary: dict) -> None:
            observed_cycles.append(deepcopy(summary))
            cycle_seen.set()

        task = runtime.start_remote_reconciler(interval_seconds=0.1, on_cycle=on_cycle)
        await asyncio.wait_for(cycle_seen.wait(), timeout=1.0)
        stopped = await runtime.stop_remote_reconciler()
        assert task.done() is True
        return stopped

    stopped = asyncio.run(scenario())
    assert stopped["status"] == "completed"
    assert stopped["cycles"] >= 1
    assert observed_cycles[0]["resolved"] == 1
    assert observed_cycles[0]["reports"][0]["jobId"] == job["jobId"]
    assert runtime.remote_reconciler_status()["running"] is False


def test_volcengine_remote_status_probe_is_read_only_and_strict(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="volcengine_ark",
        task_id="task/strict",
        provider_id="volcengine_seedance",
        base_url="https://ark.example/api/v3",
    )
    monkeypatch.setattr(
        runtime,
        "_configured_endpoint_binding",
        lambda *_args, **_kwargs: {
            "providerId": "volcengine_seedance",
            "providerMeta": {"api_key": "secret"},
            "baseUrl": "https://ark.example/api/v3",
        },
    )
    monkeypatch.setattr(runtime, "_volc_credentials", lambda: {"apiKey": "", "baseUrl": "", "videoModel": "test"})
    calls: list[tuple[str, str]] = []

    async def fake_request(method, url, **_kwargs):
        calls.append((method, url))
        return {"status": "completed"}

    monkeypatch.setattr(runtime, "_request_json", fake_request)
    result = asyncio.run(runtime._probe_provider_remote_status(job))

    assert result == {
        "providerStatus": "succeeded",
        "providerStatusRaw": "completed",
        "source": "volcengine_task_status",
    }
    assert calls == [("GET", "https://ark.example/api/v3/contents/generations/tasks/task%2Fstrict")]
    assert runtime.get_job(job["jobId"], refresh=False)["status"] == "running"


def test_unknown_adapter_reconcile_is_conservative(
    lifecycle_runtime,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime, adapter="unknown_async_adapter")

    report = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))

    assert report["status"] == "unsupported"
    assert report["detailCode"] == "provider_status_probe_not_supported"
    assert report["providerStatus"] == "unknown"
    assert report["remoteTaskMayContinue"] is True
    assert report["nextReconcileAt"]
    assert report["terminalProof"] is None


def test_remote_reconcile_rejects_provider_identity_drift_before_network_call(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime, adapter="volcengine_ark", task_id="identity-drift")
    monkeypatch.setattr(
        runtime,
        "_configured_endpoint_binding",
        lambda *_args, **_kwargs: {
            "providerId": "different-provider",
            "providerMeta": {"api_key": "different-secret"},
            "baseUrl": "https://different-provider.example/api/v3",
        },
    )
    monkeypatch.setattr(runtime, "_volc_credentials", lambda: {"apiKey": "", "baseUrl": "", "videoModel": "test"})
    called = False

    async def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(runtime, "_request_json", fail_if_called)
    report = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))

    assert report["status"] == "unsupported"
    assert report["detailCode"] == "provider_identity_changed"
    assert report["remoteTaskMayContinue"] is True
    assert report["terminalProof"] is None
    assert called is False


@pytest.mark.parametrize(
    ("provider_status", "expected"),
    [
        ("ordered", "queued"),
        ("2", "unknown"),
        ("200", "unknown"),
        ("-1", "unknown"),
        ("3", "unknown"),
        ("4", "unknown"),
    ],
)
def test_remote_reconcile_status_parser_covers_provider_contract_values(
    provider_status: str,
    expected: str,
) -> None:
    assert CreativeMediaRuntime._canonical_remote_provider_status(provider_status) == expected


def test_cancel_false_without_terminal_proof_is_forced_uncertain_and_scheduled(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="legacy-false-cancel")
    job["sessionId"] = "session-legacy-false"
    job["canvasGraphRunId"] = "canvas-run-legacy-false"
    job["canvasGraphNodeId"] = "action-legacy-false"
    runtime._save_job(job, track_task=False)

    async def legacy_cancel(_job: dict) -> dict:
        return {
            "status": "completed",
            "detailCode": "legacy_provider_reported_cancelled",
            "remoteTaskMayContinue": False,
        }

    monkeypatch.setattr(runtime, "_cancel_provider_job", legacy_cancel)
    report = asyncio.run(runtime.cancel_job(job["jobId"]))

    assert report["remoteTaskMayContinue"] is True
    assert report.get("terminalProof") is None
    stored = runtime.get_job(job["jobId"], refresh=False)
    assert stored["lifecycle"]["remoteReconcile"]["remoteTaskMayContinue"] is True
    assert stored["lifecycle"]["remoteReconcile"]["nextReconcileAt"]


def test_legacy_false_reconcile_without_terminal_proof_is_not_resolved(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="legacy-false-reconcile")
    job["sessionId"] = "session-legacy-reconcile"
    job["canvasGraphRunId"] = "canvas-run-legacy-reconcile"
    job["canvasGraphNodeId"] = "action-legacy-reconcile"
    runtime._schedule_remote_reconcile(job)
    runtime._save_job(job, track_task=False)
    stored = fake_storage.payloads[JOB_STORE_FILE]["jobs"][job["jobId"]]
    stored["lifecycle"]["remoteReconcile"]["remoteTaskMayContinue"] = False
    stored["lifecycle"]["remoteReconcile"]["terminalProof"] = None

    assert runtime.list_remote_reconcile_reports(remote_task_may_continue=False) == []
    assert runtime.list_remote_reconcile_reports(remote_task_may_continue=True)[0]["jobId"] == job["jobId"]

    async def running_probe(_job: dict) -> dict:
        return {
            "providerStatus": "running",
            "providerStatusRaw": "processing",
            "source": "legacy_false_probe",
        }

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", running_probe)
    summary = asyncio.run(runtime.reconcile_remote_jobs(force=True))

    assert summary["resolved"] == 0
    assert summary["uncertain"] == 1
    assert summary["reports"][0]["remoteTaskMayContinue"] is True
    assert summary["reports"][0]["terminalProof"] is None


def test_remote_reconcile_projection_is_durable_replayable_and_proof_compared(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime, task_id="projection-reconcile")

    async def terminal_probe(_job: dict) -> dict:
        return {
            "providerStatus": "cancelled",
            "providerStatusRaw": "CANCELED",
            "source": "projection_provider_status",
        }

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", terminal_probe)
    resolved = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))
    proof = resolved["terminalProof"]

    assert set(proof) == {"schema", "source", "providerHandle", "providerStatus", "observedAt"}
    assert resolved["projectionPending"] is True
    pending = runtime.list_remote_reconcile_reports(
        remote_task_may_continue=False,
        projection_pending=True,
    )
    assert [(item["sessionId"], item["canvasGraphRunId"], item["canvasGraphNodeId"], item["jobId"]) for item in pending] == [
        ("session-reconcile", "canvas-run-reconcile", "action-reconcile", job["jobId"])
    ]

    failed = runtime.mark_remote_reconcile_projected(
        job["jobId"],
        proof,
        projection_error="callback https://private.example/path Authorization: Bearer secret-value",
    )
    assert failed["projectionPending"] is True
    assert failed["projectionAttempts"] == 1
    assert "private.example" not in failed["lastProjectionError"]
    assert "secret-value" not in failed["lastProjectionError"]

    projected = runtime.mark_remote_reconcile_projected(
        job["jobId"],
        proof,
        projected_at="2026-08-16T00:00:00Z",
    )
    assert projected["projectionPending"] is False
    assert projected["projectionAttempts"] == 2
    assert projected["projectedAt"] == "2026-08-16T00:00:00Z"
    assert runtime.list_remote_reconcile_reports(projection_pending=True) == []
    assert runtime.mark_remote_reconcile_projected(job["jobId"], proof) == projected
    with pytest.raises(ValueError, match="terminal proof does not match"):
        runtime.mark_remote_reconcile_projected(job["jobId"], {**proof, "source": "wrong_source"})


def test_remote_reconcile_binding_resolution_failure_is_fail_closed(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(
        runtime,
        adapter="volcengine_ark",
        task_id="binding-resolution-failed",
        provider_id="volcengine_seedance",
        base_url="https://stored-provider.example/api/v3",
    )
    job["sessionId"] = "session-binding-failed"
    job["canvasGraphRunId"] = "canvas-run-binding-failed"
    job["canvasGraphNodeId"] = "action-binding-failed"
    runtime._schedule_remote_reconcile(job)
    runtime._save_job(job, track_task=False)
    monkeypatch.setattr(
        runtime,
        "_configured_endpoint_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("binding missing")),
    )
    monkeypatch.setattr(
        runtime,
        "_volc_credentials",
        lambda: {"apiKey": "fallback-secret", "baseUrl": "https://fallback.invalid", "videoModel": "seedance"},
    )
    called = False

    async def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(runtime, "_request_json", fail_if_called)
    report = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))

    assert report["status"] == "unsupported"
    assert report["detailCode"] == "provider_binding_unavailable"
    assert report["remoteTaskMayContinue"] is True
    assert called is False


@pytest.mark.parametrize("adapter", ["mureka_music", "tencent_hunyuan_3d"])
def test_remote_reconcile_requires_provider_credentials_before_probe(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
    adapter: str,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter=adapter, task_id=f"credentials-{adapter}")
    job["sessionId"] = "session-credentials"
    job["canvasGraphRunId"] = "canvas-run-credentials"
    job["canvasGraphNodeId"] = "action-credentials"
    runtime._schedule_remote_reconcile(job)
    runtime._save_job(job, track_task=False)
    monkeypatch.setattr(
        runtime,
        "_configured_provider_for_model",
        lambda *_args, **_kwargs: (
            "provider-a",
            {"base_url": "https://provider.example/api"},
            "hy-3d-3.0" if adapter == "tencent_hunyuan_3d" else "auto",
        ),
    )
    if adapter == "tencent_hunyuan_3d":
        monkeypatch.setattr(
            runtime,
            "_tencent_tokenhub_3d_endpoints",
            lambda _provider_meta: {"query": "https://provider.example/query"},
        )
    called = False

    async def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(runtime, "_request_json", fail_if_called)
    report = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))

    assert report["status"] == "unsupported"
    assert report["detailCode"] == "provider_credentials_unavailable"
    assert report["remoteTaskMayContinue"] is True
    assert called is False


def test_minimax_business_error_cannot_become_terminal_proof(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="minimax-business-error")
    job["sessionId"] = "session-minimax-error"
    job["canvasGraphRunId"] = "canvas-run-minimax-error"
    job["canvasGraphNodeId"] = "action-minimax-error"
    runtime._schedule_remote_reconcile(job)
    runtime._save_job(job, track_task=False)
    monkeypatch.setattr(
        runtime,
        "_configured_endpoint_binding",
        lambda *_args, **_kwargs: {
            "providerId": "provider-a",
            "providerMeta": {"api_key": "secret"},
            "baseUrl": "https://minimax.example",
        },
    )

    async def business_error(*_args, **_kwargs) -> dict:
        return {
            "status": "completed",
            "trace_id": "trace-private",
            "base_resp": {"status_code": 1001, "status_msg": "invalid request"},
        }

    monkeypatch.setattr(runtime, "_request_json", business_error)
    report = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))

    assert report["status"] == "waiting"
    assert report["detailCode"] == "provider_status_check_failed"
    assert report["remoteTaskMayContinue"] is True
    assert report["terminalProof"] is None


def test_mureka_business_code_without_task_status_cannot_become_terminal_proof(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="mureka_music", task_id="mureka-business-code")
    job["sessionId"] = "session-mureka-code"
    job["canvasGraphRunId"] = "canvas-run-mureka-code"
    job["canvasGraphNodeId"] = "action-mureka-code"
    runtime._schedule_remote_reconcile(job)
    runtime._save_job(job, track_task=False)
    monkeypatch.setattr(
        runtime,
        "_configured_provider_for_model",
        lambda *_args, **_kwargs: (
            "provider-a",
            {"base_url": "https://mureka.example", "api_key": "secret"},
            "auto",
        ),
    )

    async def business_code_only(*_args, **_kwargs) -> dict:
        return {"code": 200, "message": "request accepted"}

    monkeypatch.setattr(runtime, "_request_json", business_code_only)
    report = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))

    assert report["status"] == "waiting"
    assert report["detailCode"] == "provider_status_unrecognized"
    assert report["providerStatus"] == "unknown"
    assert report["remoteTaskMayContinue"] is True
    assert report["terminalProof"] is None


def test_mureka_explicit_task_status_can_create_terminal_proof(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="mureka_music", task_id="mureka-explicit-status")
    job["sessionId"] = "session-mureka-status"
    job["canvasGraphRunId"] = "canvas-run-mureka-status"
    job["canvasGraphNodeId"] = "action-mureka-status"
    runtime._schedule_remote_reconcile(job)
    runtime._save_job(job, track_task=False)
    monkeypatch.setattr(
        runtime,
        "_configured_provider_for_model",
        lambda *_args, **_kwargs: (
            "provider-a",
            {"base_url": "https://mureka.example", "api_key": "secret"},
            "auto",
        ),
    )

    async def explicit_status(*_args, **_kwargs) -> dict:
        return {"code": 200, "task_status": "completed"}

    monkeypatch.setattr(runtime, "_request_json", explicit_status)
    report = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))

    assert report["status"] == "resolved"
    assert report["providerStatus"] == "succeeded"
    assert report["remoteTaskMayContinue"] is False
    assert report["terminalProof"]["providerStatus"] == "succeeded"


def test_cancel_and_remote_reconcile_are_serialized_per_job_without_lost_phase(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="cancel-reconcile-race")
    job["sessionId"] = "session-cancel-reconcile"
    job["canvasGraphRunId"] = "canvas-run-cancel-reconcile"
    job["canvasGraphNodeId"] = "action-cancel-reconcile"
    runtime._schedule_remote_reconcile(job)
    runtime._save_job(job, track_task=False)

    async def scenario() -> tuple[dict, dict, dict]:
        cancel_started = asyncio.Event()
        release_cancel = asyncio.Event()
        reconcile_started = asyncio.Event()

        async def slow_cancel(_job: dict) -> dict:
            cancel_started.set()
            await release_cancel.wait()
            return {
                "status": "completed",
                "detailCode": "cancel_accepted_without_terminal_status",
                "remoteTaskMayContinue": True,
            }

        async def terminal_probe(_job: dict) -> dict:
            reconcile_started.set()
            return {
                "providerStatus": "cancelled",
                "providerStatusRaw": "CANCELED",
                "source": "concurrent_reconcile_status",
            }

        monkeypatch.setattr(runtime, "_cancel_provider_job", slow_cancel)
        monkeypatch.setattr(runtime, "_probe_provider_remote_status", terminal_probe)
        cancel_task = asyncio.create_task(runtime.cancel_job(job["jobId"]))
        await cancel_started.wait()
        reconcile_task = asyncio.create_task(runtime.reconcile_remote_job(job["jobId"], force=True))
        await asyncio.sleep(0)
        assert reconcile_started.is_set() is False
        release_cancel.set()
        cancel_report, reconcile_report = await asyncio.gather(cancel_task, reconcile_task)
        await asyncio.sleep(0)
        return cancel_report, reconcile_report, runtime.get_job(job["jobId"], refresh=False)

    cancel_report, reconcile_report, stored = asyncio.run(scenario())

    assert cancel_report["remoteTaskMayContinue"] is True
    assert reconcile_report["remoteTaskMayContinue"] is False
    assert set(stored["lifecycle"]) >= {"cancel", "remoteReconcile"}
    assert stored["lifecycle"]["cancel"]["terminalProof"] == reconcile_report["terminalProof"]
    assert stored["lifecycle"]["remoteReconcile"]["projectionPending"] is True
    assert runtime._lifecycle_tasks == {}


def test_cleanup_retries_failed_handles_and_preserves_monotonic_history(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="cleanup-retry")

    class FlakyProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.attempts = 0

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("process release failed")
            self.returncode = 0

        def wait(self, *, timeout: float):
            assert timeout == 3.0
            return self.returncode

    class FlakyLease:
        def __init__(self) -> None:
            self.attempts = 0

        async def release(self) -> None:
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("lease release failed")

    process = FlakyProcess()
    lease = FlakyLease()
    runtime._register_job_resource(job["jobId"], "process", process)
    runtime._register_job_resource(job["jobId"], "lease", lease)

    first = asyncio.run(runtime.cleanup_job(job["jobId"]))
    second = asyncio.run(runtime.cleanup_job(job["jobId"]))

    assert first["status"] == "failed"
    assert second["status"] == "failed"
    assert second["detailCode"] == "local_resource_cleanup_failed"
    assert second["attempt"] == 2
    assert second["historyCount"] == 2
    assert [item["status"] for item in second["history"]] == ["failed", "failed"]
    assert id(process) in runtime._job_processes[job["jobId"]]
    assert id(lease) in runtime._job_leases[job["jobId"]]

    third = asyncio.run(runtime.cleanup_job(job["jobId"]))
    fourth = asyncio.run(runtime.cleanup_job(job["jobId"]))

    assert third["status"] == "completed"
    assert third["attempt"] == 3
    assert [item["status"] for item in third["history"]] == ["failed", "failed", "completed"]
    assert job["jobId"] not in runtime._job_processes
    assert job["jobId"] not in runtime._job_leases
    assert fourth["status"] == "not_active"
    assert fourth["detailCode"] == "local_resources_already_released"
    assert fourth["attempt"] == 4
    assert fourth["historyCount"] == 4


def test_cleanup_is_singleflight_while_release_is_in_flight(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="cleanup-singleflight")

    async def scenario() -> tuple[dict, dict, int]:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        class SlowLease:
            async def release(self) -> None:
                nonlocal calls
                calls += 1
                started.set()
                await release.wait()

        runtime._register_job_resource(job["jobId"], "lease", SlowLease())
        first = asyncio.create_task(runtime.cleanup_job(job["jobId"]))
        await started.wait()
        second = asyncio.create_task(runtime.cleanup_job(job["jobId"]))
        await asyncio.sleep(0)
        release.set()
        first_report, second_report = await asyncio.gather(first, second)
        return first_report, second_report, calls

    first, second, calls = asyncio.run(scenario())

    assert calls == 1
    assert first == second
    assert first["status"] == "completed"
    assert first["attempt"] == 1
    assert job["jobId"] not in runtime._job_leases


def test_prepare_session_deletion_is_singleflight_and_keeps_remote_accepted_uncertain(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="delete-session-accepted")
    job["sessionId"] = "session-delete"
    job["canvasGraphRunId"] = "canvas-delete-run"
    job["canvasGraphNodeId"] = "canvas-delete-node"
    runtime._save_job(job, track_task=False)
    cancel_calls = 0

    async def accepted_cancel(_job: dict) -> dict:
        nonlocal cancel_calls
        cancel_calls += 1
        await asyncio.sleep(0)
        return {
            "status": "completed",
            "detailCode": "provider_cancel_request_accepted",
            "remoteTaskMayContinue": True,
        }

    monkeypatch.setattr(runtime, "_cancel_provider_job", accepted_cancel)

    async def scenario() -> tuple[dict, dict, bool]:
        owner_started = asyncio.Event()

        async def graph_owner() -> None:
            owner_started.set()
            await asyncio.Event().wait()

        owner = asyncio.create_task(graph_owner())
        await owner_started.wait()
        runtime._register_job_resource(job["jobId"], "task", owner)
        first, second = await asyncio.gather(
            runtime.prepare_session_deletion("session-delete"),
            runtime.prepare_session_deletion("session-delete"),
        )
        return first, second, owner.cancelled()

    first, second, owner_cancelled = asyncio.run(scenario())

    assert first == second
    assert first["status"] == "prepared"
    assert first["readyForDeletion"] is True
    assert first["attempt"] == 1
    assert first["remoteUncertainJobs"] == 1
    assert first["jobs"][0]["disposition"] == "owner_deleted"
    assert first["jobs"][0]["remoteTaskMayContinue"] is True
    assert set(first["jobs"][0]) == {
        "schema",
        "jobId",
        "disposition",
        "localStatus",
        "cancelStatus",
        "cleanupStatus",
        "remoteTaskMayContinue",
        "updatedAt",
    }
    assert owner_cancelled is True
    assert cancel_calls == 1
    assert runtime.get_session_deletion_tombstone("session-delete") == first
    assert fake_storage.payloads[JOB_STORE_FILE]["sessionDeletionTombstones"]["session-delete"] == first
    stored = runtime.get_job(job["jobId"], refresh=False)
    assert stored["lifecycle"]["sessionDeletion"]["disposition"] == "owner_deleted"
    assert stored["lifecycle"]["remoteReconcile"]["remoteTaskMayContinue"] is True
    assert stored["lifecycle"]["remoteReconcile"]["terminalProof"] is None
    assert asyncio.run(runtime.prepare_session_deletion("session-delete")) == first
    assert cancel_calls == 1


def test_prepare_session_deletion_blocks_then_retries_local_cleanup(lifecycle_runtime) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = runtime._new_job(
        modality="video",
        adapter="governed_local",
        request={"operationKind": "video.trim_exact", "sessionId": "session-cleanup-retry"},
    )
    job["status"] = "failed"
    runtime._save_job(job, track_task=False)

    class FailOnceLease:
        def __init__(self) -> None:
            self.attempts = 0

        async def release(self) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("first cleanup fails")

    lease = FailOnceLease()
    runtime._register_job_resource(job["jobId"], "lease", lease)

    first = asyncio.run(runtime.prepare_session_deletion("session-cleanup-retry"))
    second = asyncio.run(runtime.prepare_session_deletion("session-cleanup-retry"))

    assert first["status"] == "blocked"
    assert first["readyForDeletion"] is False
    assert first["localCleanupFailures"] == 1
    assert first["attempt"] == 1
    assert second["status"] == "prepared"
    assert second["readyForDeletion"] is True
    assert second["localCleanupFailures"] == 0
    assert second["attempt"] == 2
    assert second["historyCount"] == 2
    assert [item["status"] for item in second["history"]] == ["blocked", "prepared"]
    assert lease.attempts == 2
    cleanup = runtime.get_job(job["jobId"], refresh=False)["lifecycle"]["cleanup"]
    assert cleanup["attempt"] == 2
    assert [item["status"] for item in cleanup["history"]] == ["failed", "completed"]


def test_session_deletion_ready_gate_blocks_create_during_route_delete_barrier_and_retry(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, fake_storage = lifecycle_runtime
    create_calls = 0

    async def unexpected_create(*_args, **_kwargs) -> dict:
        nonlocal create_calls
        create_calls += 1
        raise AssertionError("create implementation must not run after deletion preparation")

    monkeypatch.setattr(runtime, "_create_job_impl", unexpected_create)

    async def scenario() -> tuple[dict, dict]:
        # This is the route's deterministic barrier: Creative Media cleanup is
        # complete, but the Session row has not yet been deleted. A concurrent
        # create must still observe the durable ready tombstone and fail closed.
        prepared = await runtime.prepare_session_deletion("session-delete-barrier")
        with pytest.raises(CreativeMediaResourceAuthorityError) as exc_info:
            await runtime.create_job(
                {
                    "sessionId": "session-delete-barrier",
                    "modality": "image",
                    "operationKind": "image.generate",
                }
            )
        assert exc_info.value.reason_code == "creative_media_session_closing"
        # Simulate the route's DB-delete failure: the tombstone remains ready,
        # and a retry rescans rather than reopening creation.
        retried = await runtime.prepare_session_deletion("session-delete-barrier")
        return prepared, retried

    first, second = asyncio.run(scenario())

    assert first["status"] == "prepared"
    assert first["acceptingNewJobs"] is False
    assert first["gateState"] == "closing"
    assert second == first
    assert create_calls == 0
    assert fake_storage.payloads[JOB_STORE_FILE].get("jobs") == {}


@pytest.mark.parametrize(
    ("error", "expected", "pending"),
    [
        ("RuntimeError: temporary sqlite busy", "transient", True),
        ("CreativeCanvasGraphError: Current session is unavailable", "owner_deleted", False),
        ("CreativeCanvasGraphConflict: Canvas graph workspace authority changed before reconciliation", "authority_changed", False),
        ("CreativeCanvasGraphConflict: Remote terminal proof is not bound to the current Canvas run", "lineage_missing", False),
    ],
)
def test_projection_failure_disposition_only_retries_transient_errors(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
    error: str,
    expected: str,
    pending: bool,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _cancelled_uncertain_canvas_job(runtime, task_id=f"projection-{expected}")

    async def terminal_probe(_job: dict) -> dict:
        return {
            "providerStatus": "cancelled",
            "providerStatusRaw": "CANCELED",
            "source": "projection_disposition_test",
        }

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", terminal_probe)
    resolved = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))
    report = runtime.mark_remote_reconcile_projected(
        job["jobId"],
        resolved["terminalProof"],
        projection_error=error,
    )

    assert report["projectionDisposition"] == expected
    assert report["projectionPending"] is pending
    assert bool(report.get("nextProjectionAt")) is pending
    assert bool(report.get("projectionResolvedAt")) is (not pending)


def test_session_deletion_late_terminal_proof_updates_tombstone_without_graph_projection_retry(
    lifecycle_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _fake_storage = lifecycle_runtime
    job = _provider_job(runtime, adapter="minimax_video", task_id="owner-deleted-late-proof")
    job["sessionId"] = "session-late-proof"
    runtime._save_job(job, track_task=False)

    async def accepted_cancel(_job: dict) -> dict:
        return {
            "status": "completed",
            "detailCode": "provider_cancel_request_accepted",
            "remoteTaskMayContinue": True,
        }

    async def terminal_probe(_job: dict) -> dict:
        return {
            "providerStatus": "cancelled",
            "providerStatusRaw": "CANCELED",
            "source": "late_owner_deleted_probe",
        }

    monkeypatch.setattr(runtime, "_cancel_provider_job", accepted_cancel)
    prepared = asyncio.run(runtime.prepare_session_deletion("session-late-proof"))
    assert prepared["remoteUncertainJobs"] == 1
    assert prepared["jobs"][0]["remoteTaskMayContinue"] is True

    monkeypatch.setattr(runtime, "_probe_provider_remote_status", terminal_probe)
    reconciled = asyncio.run(runtime.reconcile_remote_job(job["jobId"], force=True))
    tombstone = runtime.get_session_deletion_tombstone("session-late-proof")

    assert reconciled["remoteTaskMayContinue"] is False
    assert reconciled["projectionPending"] is False
    assert reconciled["projectionDisposition"] == "owner_deleted"
    assert runtime.list_remote_reconcile_reports(projection_pending=True) == []
    assert tombstone["remoteUncertainJobs"] == 0
    assert tombstone["jobs"][0]["remoteTaskMayContinue"] is False
    assert tombstone["jobs"][0]["remoteTerminalStatus"] == "cancelled"
