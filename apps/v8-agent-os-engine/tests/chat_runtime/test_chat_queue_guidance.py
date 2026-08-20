from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

import api.chat_realtime_routes as routes
import erc.event_bus as event_bus_module
import erc.run_service as run_service_module
import erc.workflow_ledger as workflow_ledger_module
from api.models import ChatMessage, ChatRequest, ChatRequestData
from core.database import DatabaseManager
from runtimes.memory.scope_resolution import ScopeBindingConflictError


def _install_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DatabaseManager:
    test_db = DatabaseManager(tmp_path / "queue-guidance.sqlite3")
    monkeypatch.setattr(routes, "db", test_db)
    monkeypatch.setattr(event_bus_module, "db", test_db)
    monkeypatch.setattr(run_service_module, "db", test_db)
    monkeypatch.setattr(workflow_ledger_module, "db", test_db)
    return test_db


def _install_chat_runtime(monkeypatch: pytest.MonkeyPatch, **members) -> SimpleNamespace:
    runtime = SimpleNamespace(**members)
    monkeypatch.setattr(routes, "_get_chat_runtime", lambda: runtime)
    return runtime


def _create_active_chat_run(test_db: DatabaseManager, *, session_id: str, run_id: str, status: str = "streaming") -> None:
    test_db.create_or_update_session(session_id, "Queue Session")
    test_db.create_run_record(run_id=run_id, session_id=session_id, run_type="chat", status=status)
    test_db.upsert_session_lane_record(
        session_id=session_id,
        active_run_id=run_id,
        queued_run_id=None,
        blocked_by_run_id=None,
        policy="queue_when_busy",
        state=status,
        last_transition="started",
        last_transition_ts="2026-05-25T00:00:00Z",
        metadata={},
    )


def _chat_request(
    *,
    session_id: str | None = None,
    data_session_id: str | None = None,
    client_message_id: str = "client-1",
    content: str = "continue this",
    include_workspace: bool = True,
) -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content=content)],
        session_id=session_id,
        clientMessageId=client_message_id,
        projectId="project-test" if include_workspace else None,
        workspaceId="workspace-test" if include_workspace else None,
        workspacePath="E:\\Projects\\test-workspace" if include_workspace else None,
        data=ChatRequestData(conversationId=data_session_id, clientMessageId=client_message_id),
    )


def test_chat_submit_requires_workspace_binding_for_user_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_db(monkeypatch, tmp_path)
    _install_chat_runtime(
        monkeypatch,
        prepare_run_context=lambda *_args, **_kwargs: pytest.fail(
            "chat runtime should not prepare without workspace binding"
        ),
    )

    request = _chat_request(session_id="session-no-binding", include_workspace=False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.chat_submit(request))

    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "workspace_binding_required"


def test_chat_submit_allows_existing_session_workspace_binding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_db(monkeypatch, tmp_path)
    fake_chat_run = SimpleNamespace(session_id="session-bound", active_run_id="run-bound")
    monkeypatch.setattr(
        routes.session_scope_binding_service,
        "get_binding",
        lambda _session_id: SimpleNamespace(
            status="active",
            project_id="project-bound",
            workspace_id="workspace-bound",
            workspace_path="E:\\Projects\\bound-workspace",
        ),
    )
    _install_chat_runtime(
        monkeypatch,
        prepare_run_context=lambda *_args, **_kwargs: fake_chat_run,
        record_request_inputs=lambda _chat_run: {"id": "msg-bound", "role": "user"},
    )
    monkeypatch.setattr(routes, "_schedule_chat_run", lambda *_args, **_kwargs: None)

    response = asyncio.run(routes.chat_submit(_chat_request(session_id="session-bound", include_workspace=False)))

    assert response["accepted"] is True
    assert response["session_id"] == "session-bound"


def test_chat_submit_queues_against_data_conversation_id_and_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    _create_active_chat_run(test_db, session_id="session-queue", run_id="run-active", status="streaming")

    request = _chat_request(data_session_id="session-queue", client_message_id="client-repeat", content="第二条消息")
    first = asyncio.run(routes.chat_submit(request))
    second = asyncio.run(routes.chat_submit(_chat_request(data_session_id="session-queue", client_message_id="client-repeat", content="第二条消息")))

    assert first["queued"] is True
    assert first["session_id"] == "session-queue"
    assert first["conversationId"] == "session-queue"
    assert first["run_id"] == "run-active"
    assert second["queued"] is True
    assert second["queuedMessage"]["id"] == first["queuedMessage"]["id"]

    items = test_db.list_chat_user_message_queue(session_id="session-queue")
    assert len(items) == 1
    assert items[0]["client_message_id"] == "client-repeat"
    assert items[0]["content"] == "第二条消息"
    assert items[0]["state"] == "pending"


def test_queue_insert_is_atomic_across_reconnect_race(tmp_path: Path) -> None:
    test_db = DatabaseManager(tmp_path / "queue-race.sqlite3")
    _create_active_chat_run(test_db, session_id="session-race", run_id="run-race", status="streaming")

    def insert(index: int) -> dict:
        return test_db.add_chat_user_message_queue_item(
            queue_id=f"queued-race-{index}",
            session_id="session-race",
            run_id="run-race",
            client_message_id="client-race",
            content="同一条重连消息",
            metadata={"attempt": index},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(insert, (1, 2)))

    assert len({item["id"] for item in results}) == 1
    queued = test_db.list_chat_user_message_queue(session_id="session-race")
    assert len(queued) == 1
    assert queued[0]["ordinal"] == 1


def test_websocket_start_uses_same_active_run_queue_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    _create_active_chat_run(
        test_db,
        session_id="session-websocket-queue",
        run_id="run-websocket-active",
        status="streaming",
    )
    request = _chat_request(
        session_id="session-websocket-queue",
        client_message_id="client-websocket-guidance",
        content="运行中补充这条要求",
    )

    queued = routes._queue_chat_request_while_run_active(
        request,
        session_id="session-websocket-queue",
        source="chat_websocket_while_run_active",
    )

    assert queued is not None
    assert queued["activeRun"]["id"] == "run-websocket-active"
    assert queued["event"]["topic"] == "human_guidance.queued"
    assert queued["event"]["run_id"] == "run-websocket-active"
    assert queued["queueItem"]["metadata"]["source"] == "chat_websocket_while_run_active"
    assert test_db.list_run_records(
        session_id="session-websocket-queue",
        run_type="chat",
        limit=20,
    ) == [test_db.get_run_record("run-websocket-active")]
    items = test_db.list_chat_user_message_queue(session_id="session-websocket-queue")
    assert len(items) == 1
    assert items[0]["content"] == "运行中补充这条要求"


def test_interrupted_lane_is_not_reported_as_an_active_chat_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    _create_active_chat_run(test_db, session_id="session-interrupted", run_id="run-interrupted", status="interrupted")

    assert routes._find_active_chat_run("session-interrupted") is None


def test_chat_submit_defers_engineering_context_pack_until_background_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_db(monkeypatch, tmp_path)
    calls: list[dict] = []
    scheduled: list[tuple[ChatRequest, str, str | None]] = []
    fake_chat_run = SimpleNamespace(session_id="session-light", active_run_id="run-light")

    def fake_prepare(request: ChatRequest, **kwargs):
        calls.append(dict(kwargs))
        return fake_chat_run

    _install_chat_runtime(
        monkeypatch,
        prepare_run_context=fake_prepare,
        record_request_inputs=lambda _chat_run: {"id": "msg-light", "role": "user"},
    )
    monkeypatch.setattr(routes, "_schedule_chat_run", lambda request, *, transport, run_id=None: scheduled.append((request, transport, run_id)) or run_id)

    response = asyncio.run(routes.chat_submit(_chat_request(session_id="session-light", client_message_id="client-light", content="开始完整实现")))

    assert response["accepted"] is True
    assert calls and calls[0]["transport"] == "submit"
    assert calls[0]["build_engineering_context"] is False
    assert scheduled and scheduled[0][1] == "submit"


def test_chat_submit_defers_graph_thread_until_acceptance_response_is_sent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_db(monkeypatch, tmp_path)
    scheduled: list[tuple[ChatRequest, str, str | None]] = []
    fake_chat_run = SimpleNamespace(session_id="session-background", active_run_id="run-background")
    background_tasks = BackgroundTasks()

    _install_chat_runtime(
        monkeypatch,
        prepare_run_context=lambda *_args, **_kwargs: fake_chat_run,
        record_request_inputs=lambda _chat_run: {"id": "msg-background", "role": "user"},
    )
    monkeypatch.setattr(
        routes,
        "_schedule_chat_run",
        lambda request, *, transport, run_id=None: scheduled.append((request, transport, run_id)) or run_id,
    )

    response = asyncio.run(
        routes.chat_submit(
            _chat_request(session_id="session-background", client_message_id="client-background"),
            background_tasks,
        )
    )

    assert response["accepted"] is True
    assert scheduled == []
    asyncio.run(background_tasks())
    assert scheduled and scheduled[0][1:] == ("submit", response["runId"])


def test_chat_submit_preannounces_attachment_before_background_graph(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_db(monkeypatch, tmp_path)
    order: list[str] = []
    fake_chat_run = SimpleNamespace(session_id="session-attachment", active_run_id="run-attachment")
    background_tasks = BackgroundTasks()
    request = _chat_request(session_id="session-attachment", client_message_id="client-attachment", content="看看附件")
    request.attachments = [  # type: ignore[assignment]
        {
            "id": "source-image",
            "name": "sample.png",
            "workspacePath": str(tmp_path / "sample.png"),
            "mimeType": "image/png",
            "mediaKind": "image",
        }
    ]

    _install_chat_runtime(
        monkeypatch,
        prepare_run_context=lambda *_args, **_kwargs: fake_chat_run,
        record_request_inputs=lambda _chat_run: {"id": "msg-attachment", "role": "user"},
        preannounce_attachment_preflight=lambda _chat_run: order.append("preannounce") or [],
    )
    monkeypatch.setattr(
        routes,
        "_schedule_chat_run",
        lambda *_args, **_kwargs: order.append("schedule"),
    )

    response = asyncio.run(routes.chat_submit(request, background_tasks))

    assert response["accepted"] is True
    assert order == ["preannounce"]
    asyncio.run(background_tasks())
    assert order == ["preannounce", "schedule"]


def test_chat_submit_canvas_direct_starts_background_graph_without_vision_preannounce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_db(monkeypatch, tmp_path)
    order: list[str] = []
    fake_chat_run = SimpleNamespace(
        session_id="session-canvas-direct",
        active_run_id="run-canvas-direct",
        prepared=SimpleNamespace(canvas_supervisor_direct=True),
    )
    background_tasks = BackgroundTasks()
    request = _chat_request(
        session_id="session-canvas-direct",
        client_message_id="client-canvas-direct",
        content="本消息来自画布",
    )
    request.attachments = [  # type: ignore[assignment]
        {
            "id": "source-image",
            "name": "sample.png",
            "workspacePath": str(tmp_path / "sample.png"),
            "mimeType": "image/png",
            "mediaKind": "image",
        }
    ]

    _install_chat_runtime(
        monkeypatch,
        prepare_run_context=lambda *_args, **_kwargs: fake_chat_run,
        record_request_inputs=lambda _chat_run: {"id": "msg-canvas", "role": "user"},
        preannounce_attachment_preflight=lambda _chat_run: order.append("preannounce") or [],
    )
    monkeypatch.setattr(
        routes,
        "_schedule_chat_run",
        lambda *_args, **_kwargs: order.append("schedule"),
    )

    response = asyncio.run(routes.chat_submit(request, background_tasks))

    assert response["accepted"] is True
    assert order == []
    asyncio.run(background_tasks())
    assert order == ["schedule"]


def test_completed_run_consumes_next_pending_message_in_same_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-drain", "Queue Drain")
    test_db.create_run_record(run_id="run-done", session_id="session-drain", run_type="chat", status="completed")
    test_db.add_chat_user_message_queue_item(
        queue_id="queue-next",
        session_id="session-drain",
        run_id="run-done",
        client_message_id="client-next",
        content="下一条继续",
        request_payload=_chat_request(session_id="session-drain", client_message_id="client-next", content="下一条继续").model_dump(mode="json", by_alias=True),
    )
    scheduled: list[tuple[ChatRequest, str, str | None]] = []
    monkeypatch.setattr(routes, "_schedule_chat_run", lambda request, *, transport, run_id=None: scheduled.append((request, transport, run_id)) or run_id)
    import core.terminal_post_run as terminal_post_run

    monkeypatch.setattr(terminal_post_run.terminal_post_run_service, "dispatch", lambda **_: None)

    routes._fire_on_chat_end_if_terminal("session-drain", "run-done")

    assert len(scheduled) == 1
    queued_request, transport, next_run_id = scheduled[0]
    assert transport == "queued_user_message"
    assert next_run_id and next_run_id.startswith("run_")
    assert queued_request.session_id == "session-drain"
    assert queued_request.conversation_id == "session-drain"
    assert queued_request.client_message_id == "client-next"
    assert queued_request.messages[-1].content == "下一条继续"
    assert test_db.get_chat_user_message_queue_item("queue-next")["state"] == "consumed"


def test_terminal_post_run_failure_does_not_poison_completed_run_or_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-terminal-fail", "Terminal Failure")
    test_db.create_run_record(run_id="run-done", session_id="session-terminal-fail", run_type="chat", status="completed")
    test_db.add_chat_user_message_queue_item(
        queue_id="queue-next",
        session_id="session-terminal-fail",
        run_id="run-done",
        client_message_id="client-next",
        content="终端治理失败后继续发送",
        request_payload=_chat_request(
            session_id="session-terminal-fail",
            client_message_id="client-next",
            content="终端治理失败后继续发送",
        ).model_dump(mode="json", by_alias=True),
    )
    scheduled: list[tuple[ChatRequest, str, str | None]] = []
    monkeypatch.setattr(routes, "_schedule_chat_run", lambda request, *, transport, run_id=None: scheduled.append((request, transport, run_id)) or run_id)
    import core.terminal_post_run as terminal_post_run

    def fail_dispatch(**_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(terminal_post_run.terminal_post_run_service, "dispatch", fail_dispatch)

    routes._fire_on_chat_end_if_terminal("session-terminal-fail", "run-done")

    assert test_db.get_run_record("run-done")["status"] == "completed"
    assert len(scheduled) == 1
    assert scheduled[0][0].session_id == "session-terminal-fail"
    assert test_db.get_chat_user_message_queue_item("queue-next")["state"] == "consumed"
    events = test_db.get_runtime_events("session-terminal-fail")
    assert any(event["topic"] == "terminal_post_run.failed" for event in events)


def test_promoted_guidance_requeues_when_run_completes_before_injection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-guidance", "Guidance")
    test_db.create_run_record(run_id="run-done", session_id="session-guidance", run_type="chat", status="completed")
    test_db.add_chat_user_message_queue_item(
        queue_id="queue-guidance",
        session_id="session-guidance",
        run_id="run-done",
        client_message_id="client-guidance",
        content="请当前轮修正方向",
        request_payload=_chat_request(session_id="session-guidance", client_message_id="client-guidance", content="请当前轮修正方向").model_dump(mode="json", by_alias=True),
    )
    test_db.update_chat_user_message_queue_item(
        "queue-guidance",
        state="promoted",
        run_id="run-done",
        timestamp_field="promoted_at",
    )
    scheduled: list[tuple[ChatRequest, str, str | None]] = []
    monkeypatch.setattr(routes, "_schedule_chat_run", lambda request, *, transport, run_id=None: scheduled.append((request, transport, run_id)) or run_id)
    import core.terminal_post_run as terminal_post_run

    monkeypatch.setattr(terminal_post_run.terminal_post_run_service, "dispatch", lambda **_: None)

    routes._fire_on_chat_end_if_terminal("session-guidance", "run-done")

    assert len(scheduled) == 1
    assert scheduled[0][0].session_id == "session-guidance"
    assert scheduled[0][0].client_message_id == "client-guidance"
    item = test_db.get_chat_user_message_queue_item("queue-guidance")
    assert item["state"] == "consumed"
    assert item["metadata"]["requeuedReason"] == "run_completed_before_guidance_injection"


def test_injected_guidance_is_not_consumed_as_next_user_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-injected", "Injected")
    test_db.create_run_record(run_id="run-done", session_id="session-injected", run_type="chat", status="completed")
    test_db.add_chat_user_message_queue_item(
        queue_id="queue-injected",
        session_id="session-injected",
        run_id="run-done",
        client_message_id="client-injected",
        content="已注入的引导",
        request_payload=_chat_request(session_id="session-injected", client_message_id="client-injected", content="已注入的引导").model_dump(mode="json", by_alias=True),
    )
    test_db.update_chat_user_message_queue_item(
        "queue-injected",
        state="injected",
        run_id="run-done",
        timestamp_field="injected_at",
    )
    scheduled: list[tuple[ChatRequest, str, str | None]] = []
    monkeypatch.setattr(routes, "_schedule_chat_run", lambda request, *, transport, run_id=None: scheduled.append((request, transport, run_id)) or run_id)
    import core.terminal_post_run as terminal_post_run

    monkeypatch.setattr(terminal_post_run.terminal_post_run_service, "dispatch", lambda **_: None)

    routes._fire_on_chat_end_if_terminal("session-injected", "run-done")

    assert scheduled == []
    assert test_db.get_chat_user_message_queue_item("queue-injected")["state"] == "injected"


def test_background_system_resume_worker_failure_triggers_runtime_recovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-runtime-resume", "Runtime Resume")
    test_db.create_run_record(
        run_id="run-runtime-resume",
        session_id="session-runtime-resume",
        run_type="chat",
        status="running",
    )
    request = ChatRequest(
        messages=[],
        session_id="session-runtime-resume",
        conversation_id="session-runtime-resume",
        resume_run_id="run-runtime-resume",
    )
    recoveries: list[dict[str, str]] = []

    async def broken_iter_chat_events(*_args, **_kwargs):
        raise RuntimeError("background drain crashed")
        yield {}

    monkeypatch.setattr(routes, "iter_chat_events", broken_iter_chat_events)
    monkeypatch.setattr(routes, "_fire_on_chat_end_if_terminal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        routes.runtime_command_router,
        "recover_runtime_episode_resume_worker_failure",
        lambda run_id, **kwargs: recoveries.append({"run_id": run_id, **kwargs}) or {
            "resume_scheduled": True,
            "worker_crash_count": 1,
        },
    )

    asyncio.run(routes._drain_chat_run(request, transport="system_resume", run_id="run-runtime-resume"))

    assert recoveries and recoveries[0]["run_id"] == "run-runtime-resume"
    assert "RuntimeError: background drain crashed" in recoveries[0]["error_message"]
    topics = [event["topic"] for event in test_db.get_runtime_events("session-runtime-resume")]
    assert "run.resume.worker.failed" in topics
    assert "run.resume.worker.recovery_scheduled" in topics


def test_background_resume_first_event_timeout_fails_still_running_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-timeout-running", "Resume Timeout")
    test_db.create_run_record(
        run_id="run-timeout-running",
        session_id="session-timeout-running",
        run_type="chat",
        status="running",
    )
    request = ChatRequest(
        messages=[],
        session_id="session-timeout-running",
        conversation_id="session-timeout-running",
        resume_run_id="run-timeout-running",
    )

    async def never_iter_chat_events(*_args, **_kwargs):
        await asyncio.Event().wait()
        yield {}

    monkeypatch.setattr(routes, "iter_chat_events", never_iter_chat_events)
    monkeypatch.setattr(routes, "_BACKGROUND_CHAT_FIRST_EVENT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(routes, "_fire_on_chat_end_if_terminal", lambda *_args, **_kwargs: None)

    asyncio.run(routes._drain_chat_run(request, transport="system_resume", run_id="run-timeout-running"))

    run_record = test_db.get_run_record("run-timeout-running") or {}
    assert run_record.get("status") == "failed"
    assert (run_record.get("metadata") or {}).get("resume_worker_timeout") is True
    topics = [event["topic"] for event in test_db.get_runtime_events("session-timeout-running")]
    assert "run.resume.worker.first_event_timeout" in topics


def test_background_resume_first_event_timeout_does_not_overwrite_completed_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-timeout-completed", "Resume Timeout")
    test_db.create_run_record(
        run_id="run-timeout-completed",
        session_id="session-timeout-completed",
        run_type="chat",
        status="completed",
    )
    request = ChatRequest(
        messages=[],
        session_id="session-timeout-completed",
        conversation_id="session-timeout-completed",
        resume_run_id="run-timeout-completed",
    )

    async def never_iter_chat_events(*_args, **_kwargs):
        await asyncio.Event().wait()
        yield {}

    monkeypatch.setattr(routes, "iter_chat_events", never_iter_chat_events)
    monkeypatch.setattr(routes, "_BACKGROUND_CHAT_FIRST_EVENT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(routes, "_fire_on_chat_end_if_terminal", lambda *_args, **_kwargs: None)

    asyncio.run(routes._drain_chat_run(request, transport="system_resume", run_id="run-timeout-completed"))

    run_record = test_db.get_run_record("run-timeout-completed") or {}
    assert run_record.get("status") == "completed"
    assert (run_record.get("metadata") or {}).get("resume_worker_timeout") is None
    topics = [event["topic"] for event in test_db.get_runtime_events("session-timeout-completed")]
    assert "run.resume.worker.first_event_timeout" in topics
    assert "run.resume.worker.first_event_timeout_ignored" in topics


def test_scope_conflict_terminalizes_accepted_run_and_persists_terminal_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-scope-conflict", "Scope Conflict")
    test_db.create_run_record(
        run_id="run-scope-conflict",
        session_id="session-scope-conflict",
        run_type="chat",
        status="queued",
    )
    request = ChatRequest(
        messages=[],
        session_id="session-scope-conflict",
        conversation_id="session-scope-conflict",
        resume_run_id="run-scope-conflict",
    )
    conflict_payload = {
        "error": "scope_conflict",
        "message": "This session is bound to another workspace.",
        "changedAnchors": {"workspace_path": {"previous": "E:/one", "requested": "E:/two"}},
        "recommendedAction": "create_new_session",
    }

    async def conflicting_stream(*_args, **_kwargs):
        raise ScopeBindingConflictError(conflict_payload)
        yield {}

    _install_chat_runtime(monkeypatch, stream_legacy_events=conflicting_stream)

    async def collect_events():
        return [
            event
            async for event in routes.iter_chat_events(
                request,
                transport="background",
                run_id="run-scope-conflict",
            )
        ]

    events = asyncio.run(collect_events())
    run_record = test_db.get_run_record("run-scope-conflict") or {}

    assert run_record.get("status") == "failed"
    assert run_record.get("error_message") == conflict_payload["message"]
    assert (run_record.get("metadata") or {}).get("scope_conflict") is True
    assert [event["topic"] for event in events] == ["scope.conflict", "run.state.changed", "run.failed"]
    persisted = test_db.get_runtime_events_for_run("run-scope-conflict")
    assert [event["topic"] for event in persisted] == ["scope.conflict", "run.state.changed", "run.failed"]


def test_scope_conflict_does_not_overwrite_an_already_interrupted_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-scope-race", "Scope Race")
    test_db.create_run_record(
        run_id="run-scope-race",
        session_id="session-scope-race",
        run_type="chat",
        status="interrupted",
    )
    request = ChatRequest(
        messages=[],
        session_id="session-scope-race",
        conversation_id="session-scope-race",
        resume_run_id="run-scope-race",
    )

    async def conflicting_stream(*_args, **_kwargs):
        raise ScopeBindingConflictError(
            {
                "error": "scope_conflict",
                "message": "This session is bound to another workspace.",
                "changedAnchors": {"workspace_path": {"previous": "E:/one", "requested": "E:/two"}},
            }
        )
        yield {}

    _install_chat_runtime(monkeypatch, stream_legacy_events=conflicting_stream)

    async def collect_events():
        return [
            event
            async for event in routes.iter_chat_events(
                request,
                transport="background",
                run_id="run-scope-race",
            )
        ]

    events = asyncio.run(collect_events())

    assert (test_db.get_run_record("run-scope-race") or {}).get("status") == "interrupted"
    assert [event["topic"] for event in events] == ["scope.conflict"]
    assert [event["topic"] for event in test_db.get_runtime_events_for_run("run-scope-race")] == ["scope.conflict"]


def test_background_non_resume_worker_failure_still_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_db = _install_db(monkeypatch, tmp_path)
    test_db.create_or_update_session("session-background", "Background")
    test_db.create_run_record(
        run_id="run-background",
        session_id="session-background",
        run_type="chat",
        status="running",
    )
    request = ChatRequest(
        messages=[],
        session_id="session-background",
        conversation_id="session-background",
    )
    recoveries: list[str] = []

    async def broken_iter_chat_events(*_args, **_kwargs):
        raise RuntimeError("ordinary background drain crashed")
        yield {}

    monkeypatch.setattr(routes, "iter_chat_events", broken_iter_chat_events)
    monkeypatch.setattr(routes, "_fire_on_chat_end_if_terminal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        routes.runtime_command_router,
        "recover_runtime_episode_resume_worker_failure",
        lambda run_id, **_kwargs: recoveries.append(run_id),
    )

    with pytest.raises(RuntimeError, match="ordinary background drain crashed"):
        asyncio.run(routes._drain_chat_run(request, transport="background", run_id="run-background"))

    assert recoveries == []
