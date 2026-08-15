from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

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
    monkeypatch.setattr(runtime, "_record_terminal_job_observations", lambda _job: None)
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
            "endpointBinding": {"baseUrl": base_url, "adapter": adapter},
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
