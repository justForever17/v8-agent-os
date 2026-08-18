from __future__ import annotations

from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from api.models import ChatRequestData
from core.database import DatabaseManager
from core.realtime_protocol import utc_now_iso
from erc.session_coordination_service import SessionCoordinationService
import erc.session_coordination_service as coordination_module


SOURCE_SESSION_ID = "session-source-001"
TARGET_SESSION_ID = "session-target-001"
USER_ID = "owner-001"


def _context_read_state(target_session_id: str, user_text: str) -> dict:
    return {
        "messages": [
            HumanMessage(content=user_text),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-context-read",
                        "name": "session_context_broker",
                        "args": {
                            "sourceSessionId": target_session_id,
                            "mode": "summary",
                            "limitTurns": 6,
                        },
                    }
                ],
            ),
            ToolMessage(
                content="Target context loaded.",
                name="session_context_broker",
                tool_call_id="call-context-read",
            ),
        ]
    }


def _add_queued_message(db: DatabaseManager, *, message_id: str = "coord-queued-001") -> dict:
    return db.add_session_coordination_message(
        message_id=message_id,
        thread_id="coord-thread-001",
        message_type="request",
        source_session_id=SOURCE_SESSION_ID,
        target_session_id=TARGET_SESSION_ID,
        source_run_id="run-source-001",
        target_run_id=None,
        source_user_id=USER_ID,
        intent="request",
        authority="current_user_explicit",
        content="请核对目标任务的当前阻塞。",
        summary="请核对目标任务的当前阻塞。",
        context={"recentKeyTurns": []},
        evidence_refs=[],
        reply_to_message_id=None,
        reply_status=None,
        hop_count=1,
        max_hops=2,
        state="queued",
        idempotency_key=f"test:{message_id}",
        metadata={"replyRequired": True},
        expires_at="2099-01-01T00:00:00Z",
        authorized_at=utc_now_iso(),
    )


@pytest.fixture()
def coordination_harness(tmp_path, monkeypatch):
    test_db = DatabaseManager(tmp_path / "session-coordination.sqlite3")
    test_db.create_or_update_session(SOURCE_SESSION_ID, "Source Task", user_id=USER_ID)
    test_db.create_or_update_session(TARGET_SESSION_ID, "Target Task", user_id=USER_ID)
    monkeypatch.setattr(coordination_module, "db", test_db)
    service = SessionCoordinationService()
    monkeypatch.setattr(
        service,
        "_build_source_context",
        lambda _session_id: {
            "ok": True,
            "currentGoal": "Finish the source task",
            "recentKeyTurns": [
                {"role": "user", "contentPreview": f"turn-{index}"}
                for index in range(6)
            ],
            "authority": {"workspaceInherited": False},
        },
    )
    return test_db, service


def test_send_requires_same_turn_target_context_read(coordination_harness, monkeypatch):
    _db, service = coordination_harness
    monkeypatch.setattr(service, "dispatch_message", lambda message_id: coordination_module.db.get_session_coordination_message(message_id))
    result = service.send(
        source_session_id=SOURCE_SESSION_ID,
        source_run_id="run-source-001",
        source_user_id=USER_ID,
        target_session_id=TARGET_SESSION_ID,
        intent="request",
        content="请核对目标任务。",
        authorization_quote=f"请通知 {TARGET_SESSION_ID} 核对目标任务。",
        state={"messages": [HumanMessage(content=f"请通知 {TARGET_SESSION_ID} 核对目标任务。")]},
        tool_call_id="tool-send-001",
    )
    assert result["ok"] is False
    assert result["error"] == "target_context_read_required"

    failed_read_state = {
        "messages": [
            HumanMessage(content=f"请通知 {TARGET_SESSION_ID} 核对目标任务。"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-failed-context",
                        "name": "session_context_broker",
                        "args": {"sourceSessionId": TARGET_SESSION_ID, "mode": "summary", "limitTurns": 6},
                    }
                ],
            ),
            ToolMessage(
                content="Session context takeover failed\nError: session_context_unauthorized",
                name="session_context_broker",
                tool_call_id="call-failed-context",
            ),
        ]
    }
    failed_read = service.send(
        source_session_id=SOURCE_SESSION_ID,
        source_run_id="run-source-failed-read",
        source_user_id=USER_ID,
        target_session_id=TARGET_SESSION_ID,
        intent="request",
        content="请核对目标任务。",
        authorization_quote=f"请通知 {TARGET_SESSION_ID} 核对目标任务。",
        state=failed_read_state,
        tool_call_id="tool-send-failed-read",
    )
    assert failed_read["ok"] is False
    assert failed_read["error"] == "target_context_read_required"


def test_unapproved_send_creates_one_shot_ask_user_draft(coordination_harness, monkeypatch):
    db, service = coordination_harness
    monkeypatch.setattr(service, "dispatch_message", lambda message_id: db.get_session_coordination_message(message_id))
    state = _context_read_state(TARGET_SESSION_ID, "帮我看看另一个任务的情况。")
    target_updated_before = db.get_session(TARGET_SESSION_ID)["updated_at"]
    result = service.send(
        source_session_id=SOURCE_SESSION_ID,
        source_run_id="run-source-ask",
        source_user_id=USER_ID,
        target_session_id=TARGET_SESSION_ID,
        intent="inform",
        content="来源任务发现目标合同需要纠偏。",
        authorization_quote="",
        state=state,
        tool_call_id="tool-send-ask",
    )
    assert result["ok"] is True
    assert result["authorizationRequired"] is True
    assert result["message"]["state"] == "awaiting_authorization"
    context = result["askUserRequest"]["coordinationContext"]
    assert context["oneShot"] is True
    assert db.get_chat_canonical_messages(SOURCE_SESSION_ID) == []
    assert db.get_chat_canonical_messages(TARGET_SESSION_ID) == []
    assert db.get_session(TARGET_SESSION_ID)["updated_at"] == target_updated_before

    resolved = service.handle_ask_user_resolution(
        {
            "id": "ask-interaction-001",
            "session_id": SOURCE_SESSION_ID,
            "run_id": "run-source-ask",
            "request": {"coordinationContext": context},
            "answer_text": "允许发送",
        },
        {"answer": "approve_send"},
    )
    assert resolved["handled"] is True
    assert resolved["approved"] is True
    row = db.get_session_coordination_message(context["draftMessageId"])
    assert row["state"] == "queued"
    assert row["authority"] == "ask_user_approved"
    assert row["authorizationInteractionId"] == "ask-interaction-001"
    assert len(db.get_chat_canonical_messages(TARGET_SESSION_ID)) == 1

    replay = service.handle_ask_user_resolution(
        {
            "id": "ask-interaction-002",
            "session_id": SOURCE_SESSION_ID,
            "run_id": "run-source-ask",
            "request": {"coordinationContext": context},
        },
        {"answer": "approve_send"},
    )
    assert replay["handled"] is False
    assert replay["reason"] == "draft_not_waiting"


def test_ask_user_authorization_is_bound_to_source_session_and_run(coordination_harness, monkeypatch):
    db, service = coordination_harness
    monkeypatch.setattr(service, "dispatch_message", lambda message_id: db.get_session_coordination_message(message_id))
    state = _context_read_state(TARGET_SESSION_ID, "帮我问另一个任务。")
    draft = service.send(
        source_session_id=SOURCE_SESSION_ID,
        source_run_id="run-source-bound",
        source_user_id=USER_ID,
        target_session_id=TARGET_SESSION_ID,
        intent="request",
        content="请报告阻塞。",
        authorization_quote="",
        state=state,
        tool_call_id="tool-send-bound",
    )
    context = draft["askUserRequest"]["coordinationContext"]
    result = service.handle_ask_user_resolution(
        {
            "id": "ask-interaction-wrong-run",
            "session_id": SOURCE_SESSION_ID,
            "run_id": "run-source-other",
            "request": {"coordinationContext": context},
        },
        {"answer": "approve_send"},
    )
    assert result["handled"] is True
    assert result["approved"] is False
    assert result["reason"] == "scope_mismatch"
    assert db.get_session_coordination_message(context["draftMessageId"])["state"] == "blocked"


def test_negated_send_quote_never_grants_direct_authority(coordination_harness, monkeypatch):
    db, service = coordination_harness
    monkeypatch.setattr(service, "dispatch_message", lambda message_id: db.get_session_coordination_message(message_id))
    user_text = f"不要向 {TARGET_SESSION_ID} 发送任何跨任务消息。"
    result = service.send(
        source_session_id=SOURCE_SESSION_ID,
        source_run_id="run-source-negated",
        source_user_id=USER_ID,
        target_session_id=TARGET_SESSION_ID,
        intent="inform",
        content="这条消息不应获得直接授权。",
        authorization_quote=user_text,
        state=_context_read_state(TARGET_SESSION_ID, user_text),
        tool_call_id="tool-send-negated",
    )
    assert result["ok"] is True
    assert result["authorizationRequired"] is True
    assert result["message"]["state"] == "awaiting_authorization"


def test_direct_send_is_idempotent_and_projects_governance_not_user(coordination_harness, monkeypatch):
    db, service = coordination_harness
    monkeypatch.setattr(service, "dispatch_message", lambda message_id: db.get_session_coordination_message(message_id))
    user_text = f"请通知 {TARGET_SESSION_ID}：先停止沿用旧审批，核对最新用户目标。"
    state = _context_read_state(TARGET_SESSION_ID, user_text)
    kwargs = {
        "source_session_id": SOURCE_SESSION_ID,
        "source_run_id": "run-source-direct",
        "source_user_id": USER_ID,
        "target_session_id": TARGET_SESSION_ID,
        "intent": "correct",
        "content": "先停止沿用旧审批，核对目标会话最新用户目标。",
        "authorization_quote": user_text,
        "state": state,
        "tool_call_id": "tool-send-direct",
    }
    first = service.send(**kwargs)
    second = service.send(**kwargs)
    assert first["message"]["messageId"] == second["message"]["messageId"]
    assert first["message"]["authority"] == "current_user_explicit"
    assert first["message"]["state"] == "queued"

    source_messages = db.get_chat_canonical_messages(SOURCE_SESSION_ID)
    target_messages = db.get_chat_canonical_messages(TARGET_SESSION_ID)
    assert len(source_messages) == 1
    assert len(target_messages) == 1
    for canonical in [*source_messages, *target_messages]:
        assert canonical["role"] == "assistant"
        assert canonical["content_text"] == ""
        assert canonical["metadata"]["governanceOnly"] is True
        assert canonical["nodes"][0]["governanceType"] == "session_coordination"


def test_concurrent_duplicate_send_creates_one_durable_message(coordination_harness, monkeypatch):
    db, service = coordination_harness
    monkeypatch.setattr(service, "dispatch_message", lambda message_id: db.get_session_coordination_message(message_id))
    user_text = f"请通知 {TARGET_SESSION_ID} 同步当前阻塞。"
    kwargs = {
        "source_session_id": SOURCE_SESSION_ID,
        "source_run_id": "run-source-concurrent",
        "source_user_id": USER_ID,
        "target_session_id": TARGET_SESSION_ID,
        "intent": "inform",
        "content": "同步当前阻塞。",
        "authorization_quote": user_text,
        "state": _context_read_state(TARGET_SESSION_ID, user_text),
        "tool_call_id": "tool-send-concurrent",
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: service.send(**kwargs), range(2)))
    assert results[0]["message"]["messageId"] == results[1]["message"]["messageId"]
    assert len(db.list_session_coordination_messages(session_id=SOURCE_SESSION_ID, limit=20)) == 1


def test_target_inbox_is_bounded_to_twenty_pending_messages(coordination_harness, monkeypatch):
    db, service = coordination_harness
    monkeypatch.setattr(service, "dispatch_message", lambda message_id: db.get_session_coordination_message(message_id))
    for index in range(20):
        _add_queued_message(db, message_id=f"coord-capacity-{index:02d}")
    user_text = f"请通知 {TARGET_SESSION_ID} 再处理一条消息。"
    result = service.send(
        source_session_id=SOURCE_SESSION_ID,
        source_run_id="run-source-capacity",
        source_user_id=USER_ID,
        target_session_id=TARGET_SESSION_ID,
        intent="request",
        content="第 21 条未完成消息不应进入目标队列。",
        authorization_quote=user_text,
        state=_context_read_state(TARGET_SESSION_ID, user_text),
        tool_call_id="tool-send-capacity",
    )
    assert result["ok"] is False
    assert result["error"] == "target_inbox_full"
    assert db.count_pending_session_coordination_messages(TARGET_SESSION_ID) == 20


def test_expired_undelivered_messages_do_not_block_target_capacity(coordination_harness, monkeypatch):
    db, service = coordination_harness
    monkeypatch.setattr(service, "dispatch_message", lambda message_id: db.get_session_coordination_message(message_id))
    for index in range(20):
        _add_queued_message(db, message_id=f"coord-expired-capacity-{index:02d}")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE session_coordination_messages SET expires_at = ? WHERE target_session_id = ?",
            ("2000-01-01T00:00:00Z", TARGET_SESSION_ID),
        )
        conn.commit()
    user_text = f"请通知 {TARGET_SESSION_ID} 处理新的消息。"
    result = service.send(
        source_session_id=SOURCE_SESSION_ID,
        source_run_id="run-source-after-expiry",
        source_user_id=USER_ID,
        target_session_id=TARGET_SESSION_ID,
        intent="request",
        content="过期消息清理后允许进入。",
        authorization_quote=user_text,
        state=_context_read_state(TARGET_SESSION_ID, user_text),
        tool_call_id="tool-send-after-expiry",
    )
    assert result["ok"] is True
    assert db.count_pending_session_coordination_messages(TARGET_SESSION_ID) == 1
    expired = db.list_session_coordination_messages(
        target_session_id=TARGET_SESSION_ID,
        states=["expired"],
        limit=30,
    )
    assert len(expired) == 20


@pytest.mark.parametrize(
    ("target_session_id", "content", "expected_error"),
    [
        (SOURCE_SESSION_ID, "普通消息", "self_target_not_allowed"),
        (TARGET_SESSION_ID, "api_key=super-secret-value", "secret_detected"),
    ],
)
def test_send_rejects_self_target_and_secrets(
    coordination_harness,
    target_session_id,
    content,
    expected_error,
):
    _db, service = coordination_harness
    state = _context_read_state(target_session_id, f"请通知 {target_session_id} 处理。")
    result = service.send(
        source_session_id=SOURCE_SESSION_ID,
        source_run_id="run-source-security",
        source_user_id=USER_ID,
        target_session_id=target_session_id,
        intent="request",
        content=content,
        authorization_quote=f"请通知 {target_session_id} 处理。",
        state=state,
        tool_call_id="tool-send-security",
    )
    assert result["ok"] is False
    assert result["error"] == expected_error


def test_send_rejects_cross_user_and_unknown_owner(coordination_harness):
    db, service = coordination_harness
    db.create_or_update_session("session-other-user", "Other", user_id="owner-002")
    user_text = "请通知 session-other-user 处理。"
    result = service.send(
        source_session_id=SOURCE_SESSION_ID,
        source_run_id="run-source-owner",
        source_user_id=USER_ID,
        target_session_id="session-other-user",
        intent="request",
        content="请处理。",
        authorization_quote=user_text,
        state=_context_read_state("session-other-user", user_text),
        tool_call_id="tool-send-owner",
    )
    assert result["ok"] is False
    assert result["error"] == "session_coordination_unauthorized"

    db.create_or_update_session("session-unknown-owner", "Unknown", user_id="")
    unknown_text = "请通知 session-unknown-owner 处理。"
    unknown = service.send(
        source_session_id=SOURCE_SESSION_ID,
        source_run_id="run-source-unknown-owner",
        source_user_id=USER_ID,
        target_session_id="session-unknown-owner",
        intent="request",
        content="请处理。",
        authorization_quote=unknown_text,
        state=_context_read_state("session-unknown-owner", unknown_text),
        tool_call_id="tool-send-unknown-owner",
    )
    assert unknown["ok"] is False
    assert unknown["error"] == "session_coordination_target_owner_unknown"


def test_reply_is_bounded_to_second_hop_and_completed_requires_evidence(coordination_harness, monkeypatch):
    db, service = coordination_harness
    parent = _add_queued_message(db, message_id="coord-parent-001")
    db.update_session_coordination_message(parent["id"], state="injected", target_run_id="run-target-001")
    monkeypatch.setattr(service, "dispatch_message", lambda message_id: db.get_session_coordination_message(message_id))
    monkeypatch.setattr(service, "dispatch_for_session", lambda _session_id: None)
    inbound = service.compact_ref(db.get_session_coordination_message(parent["id"]), viewer_session_id=TARGET_SESSION_ID)
    result = service.reply(
        current_session_id=TARGET_SESSION_ID,
        current_run_id="run-target-001",
        current_user_id=USER_ID,
        message_id=parent["id"],
        reply_status="completed",
        content="已核对，等待目标会话自己的审批。",
        evidence_refs=[],
        state={"session_coordination": inbound},
        tool_call_id="tool-reply-001",
    )
    assert result["ok"] is True
    assert result["message"]["messageType"] == "reply"
    assert result["message"]["hopCount"] == 2
    assert result["message"]["replyStatus"] == "accepted"
    assert db.get_session_coordination_message(parent["id"])["state"] == "replied"

    third_hop = service.reply(
        current_session_id=SOURCE_SESSION_ID,
        current_run_id="run-source-002",
        current_user_id=USER_ID,
        message_id=result["message"]["messageId"],
        reply_status="acknowledged",
        content="不应允许第三跳。",
        evidence_refs=[],
        state={"session_coordination": result["message"]},
        tool_call_id="tool-reply-002",
    )
    assert third_hop["ok"] is False
    assert third_hop["error"] == "max_hops_exceeded"


def test_running_target_promotes_to_control_signal(coordination_harness, monkeypatch):
    db, service = coordination_harness
    row = _add_queued_message(db, message_id="coord-running-001")
    db.create_run_record(
        "run-target-running",
        TARGET_SESSION_ID,
        user_id=USER_ID,
        run_type="chat",
        status="running",
    )
    monkeypatch.setattr(
        coordination_module.session_admission_service,
        "get_lane_view",
        lambda _session_id: {"activeRunId": "run-target-running"},
    )
    issued = []
    monkeypatch.setattr(coordination_module.command_service, "peek_control_signal", lambda _run_id: None)
    monkeypatch.setattr(
        coordination_module.command_service,
        "issue_control_signal",
        lambda run_id, **kwargs: issued.append((run_id, kwargs)) or {"command": kwargs["command"]},
    )
    result = service.dispatch_message(row["id"])
    assert result["state"] == "promoted"
    assert result["targetRunId"] == "run-target-running"
    assert issued == [
        (
            "run-target-running",
            {
                "command": "session_coordination",
                "reason": "cross_session_supervisor_message",
                "payload": {"messageId": row["id"]},
            },
        )
    ]


def test_dispatch_rechecks_owner_before_delivery(coordination_harness, monkeypatch):
    db, service = coordination_harness
    row = _add_queued_message(db, message_id="coord-owner-change-001")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE sessions SET user_id = ? WHERE id = ?",
            ("owner-002", TARGET_SESSION_ID),
        )
        conn.commit()
    monkeypatch.setattr(
        service,
        "_wake_idle_target",
        lambda *_args, **_kwargs: pytest.fail("changed owner must block before wake"),
    )

    result = service.dispatch_message(row["id"])

    assert result["state"] == "blocked"
    assert result["errorCode"] == "delivery_owner_changed"


def test_waiting_target_keeps_message_queued_without_parallel_run(coordination_harness, monkeypatch):
    db, service = coordination_harness
    row = _add_queued_message(db, message_id="coord-waiting-001")
    db.create_run_record(
        "run-target-waiting",
        TARGET_SESSION_ID,
        user_id=USER_ID,
        run_type="chat",
        status="waiting_approval",
    )
    monkeypatch.setattr(
        coordination_module.session_admission_service,
        "get_lane_view",
        lambda _session_id: {"activeRunId": "run-target-waiting"},
    )
    monkeypatch.setattr(
        service,
        "_wake_idle_target",
        lambda *_args, **_kwargs: pytest.fail("waiting target must not create a parallel run"),
    )
    result = service.dispatch_message(row["id"])
    assert result["state"] == "queued"
    assert result["targetRunId"] is None


def test_human_guidance_requeues_promoted_coordination(coordination_harness):
    db, service = coordination_harness
    row = _add_queued_message(db, message_id="coord-guidance-001")
    db.update_session_coordination_message(
        row["id"],
        state="promoted",
        target_run_id="run-target-guidance",
    )
    service.yield_to_human_guidance("run-target-guidance")
    updated = db.get_session_coordination_message(row["id"])
    assert updated["state"] == "queued"
    assert updated["targetRunId"] is None
    assert updated["metadata"]["yieldedToHumanGuidance"] is True


def test_idle_target_uses_its_own_session_to_schedule_chat_run(coordination_harness, monkeypatch):
    db, service = coordination_harness
    row = _add_queued_message(db, message_id="coord-idle-001")
    monkeypatch.setattr(
        coordination_module.session_admission_service,
        "get_lane_view",
        lambda _session_id: {"activeRunId": None},
    )
    scheduled = []
    fake_request = SimpleNamespace(data=SimpleNamespace(session_coordination_message_id=row["id"]))
    from erc.command_router import runtime_command_router

    monkeypatch.setattr(
        runtime_command_router,
        "build_session_coordination_chat_request",
        lambda *, session_id, message_id: scheduled.append(("build", session_id, message_id)) or fake_request,
    )
    monkeypatch.setattr(
        runtime_command_router,
        "schedule_chat_run",
        lambda request, *, transport, run_id=None: scheduled.append(("schedule", request, transport, run_id)) or run_id,
    )
    result = service.dispatch_message(row["id"])
    assert result["state"] == "promoted"
    assert result["targetRunId"].startswith("run_")
    run = db.get_run_record(result["targetRunId"])
    assert run["session_id"] == TARGET_SESSION_ID
    assert run["user_id"] == USER_ID
    assert run["trigger_source"] == "session_coordination"
    assert scheduled[0] == ("build", TARGET_SESSION_ID, row["id"])
    assert scheduled[1][2] == "session_coordination"


def test_coordination_wake_request_uses_target_canonical_history_and_scope(coordination_harness, monkeypatch):
    db, _service = coordination_harness
    db.create_chat_canonical_message(
        message_id="target-user-message-001",
        session_id=TARGET_SESSION_ID,
        run_id=None,
        ordinal=1,
        role="user",
        state="completed",
        nodes=[
            {
                "id": "target-user-message-001:narrative",
                "kind": "narrative",
                "role": "user",
                "content": "目标任务最新指令：不要修改数据库。",
                "timestamp": 1,
            }
        ],
        content_text="目标任务最新指令：不要修改数据库。",
        reasoning_text="",
        metadata={},
        finalized_at=utc_now_iso(),
    )
    import erc.command_router as command_router_module
    import erc.chat_canonical_transcript as canonical_transcript_module

    monkeypatch.setattr(command_router_module, "db", db)
    monkeypatch.setattr(canonical_transcript_module, "db", db)
    monkeypatch.setattr(
        command_router_module.runtime_command_router,
        "_scope_payload_for_session",
        lambda session_id: {
            "workspace_id": "target-workspace" if session_id == TARGET_SESSION_ID else "wrong-workspace",
            "workspace_path": "E:/target-workspace" if session_id == TARGET_SESSION_ID else "E:/wrong-workspace",
            "project_id": "target-project",
            "scope_hint": "workspace",
            "scope_mode": "explicit",
        },
    )
    request = command_router_module.runtime_command_router.build_session_coordination_chat_request(
        session_id=TARGET_SESSION_ID,
        message_id="coord-wake-request-001",
    )
    assert request.messages[-1].role == "user"
    assert request.messages[-1].content == "目标任务最新指令：不要修改数据库。"
    assert request.workspace_id == "target-workspace"
    assert request.workspace_path == "E:/target-workspace"
    assert request.project_id == "target-project"
    assert request.data._session_coordination_message_id == "coord-wake-request-001"
    assert "sessionCoordinationMessageId" not in ChatRequestData.model_json_schema().get("properties", {})


def test_terminal_run_without_structured_reply_fails_honestly(coordination_harness, monkeypatch):
    db, service = coordination_harness
    row = _add_queued_message(db, message_id="coord-missed-reply-001")
    db.update_session_coordination_message(
        row["id"],
        state="injected",
        target_run_id="run-target-terminal",
    )
    monkeypatch.setattr(service, "dispatch_for_session", lambda _session_id: None)
    service.on_run_terminal(TARGET_SESSION_ID, "run-target-terminal", status="completed")
    updated = db.get_session_coordination_message(row["id"])
    assert updated["state"] == "failed"
    assert updated["errorCode"] == "reply_contract_not_satisfied"


def test_delivered_reply_becomes_terminal_after_source_summary_run(coordination_harness, monkeypatch):
    db, service = coordination_harness
    reply = db.add_session_coordination_message(
        message_id="coord-reply-delivery-001",
        thread_id="coord-thread-delivery-001",
        message_type="reply",
        source_session_id=TARGET_SESSION_ID,
        target_session_id=SOURCE_SESSION_ID,
        source_run_id="run-target-reply",
        target_run_id="run-source-summary",
        source_user_id=USER_ID,
        intent="request",
        authority="bounded_reply",
        content="目标任务已接受纠偏。",
        summary="目标任务已接受纠偏。",
        context={},
        evidence_refs=["run:target-proof"],
        reply_to_message_id="coord-parent-delivery-001",
        reply_status="completed",
        hop_count=2,
        max_hops=2,
        state="injected",
        idempotency_key="test:coord-reply-delivery-001",
        metadata={"finalReply": True},
        expires_at="2099-01-01T00:00:00Z",
        authorized_at=utc_now_iso(),
    )
    monkeypatch.setattr(service, "dispatch_for_session", lambda _session_id: None)
    service.on_run_terminal(SOURCE_SESSION_ID, "run-source-summary", status="completed")
    updated = db.get_session_coordination_message(reply["id"])
    assert updated["state"] == "replied"
    assert updated["metadata"]["replyDelivered"] is True


def test_engine_restart_closes_orphaned_injected_request(coordination_harness, monkeypatch):
    db, service = coordination_harness
    row = _add_queued_message(db, message_id="coord-restart-request-001")
    db.create_run_record(
        "run-target-restart",
        TARGET_SESSION_ID,
        user_id=USER_ID,
        run_type="chat",
        status="running",
    )
    db.update_session_coordination_message(
        row["id"],
        state="injected",
        target_run_id="run-target-restart",
    )
    monkeypatch.setattr(service, "dispatch_for_session", lambda _session_id: None)

    result = service.recover_pending()

    assert result["recovered"] == 1
    updated = db.get_session_coordination_message(row["id"])
    assert updated["state"] == "failed"
    assert updated["errorCode"] == "engine_restart_after_coordination_injection"
    assert updated["metadata"]["recoveredAfterEngineRestart"] is True
    assert updated["metadata"]["orphanedRunStatus"] == "running"


def test_engine_restart_reconciles_interrupted_injected_request_as_terminal(coordination_harness, monkeypatch):
    db, service = coordination_harness
    row = _add_queued_message(db, message_id="coord-restart-interrupted-001")
    db.create_run_record(
        "run-target-interrupted",
        TARGET_SESSION_ID,
        user_id=USER_ID,
        run_type="chat",
        status="running",
    )
    db.update_run_record("run-target-interrupted", status="interrupted")
    db.update_session_coordination_message(
        row["id"],
        state="injected",
        target_run_id="run-target-interrupted",
    )
    monkeypatch.setattr(service, "dispatch_for_session", lambda _session_id: None)

    result = service.recover_pending()

    assert result["recovered"] == 1
    updated = db.get_session_coordination_message(row["id"])
    assert updated["state"] == "failed"
    assert updated["errorCode"] == "reply_contract_not_satisfied"
    assert updated["metadata"]["terminalRunStatus"] == "interrupted"


def test_engine_restart_reconciles_completed_reply_delivery(coordination_harness, monkeypatch):
    db, service = coordination_harness
    db.create_run_record(
        "run-source-restart-summary",
        SOURCE_SESSION_ID,
        user_id=USER_ID,
        run_type="chat",
        status="completed",
    )
    reply = db.add_session_coordination_message(
        message_id="coord-restart-reply-001",
        thread_id="coord-thread-restart-reply-001",
        message_type="reply",
        source_session_id=TARGET_SESSION_ID,
        target_session_id=SOURCE_SESSION_ID,
        source_run_id="run-target-restart-reply",
        target_run_id="run-source-restart-summary",
        source_user_id=USER_ID,
        intent="request",
        authority="bounded_reply",
        content="目标任务已返回冲突结论。",
        summary="目标任务已返回冲突结论。",
        context={},
        evidence_refs=[],
        reply_to_message_id="coord-restart-parent-001",
        reply_status="conflict",
        hop_count=2,
        max_hops=2,
        state="injected",
        idempotency_key="test:coord-restart-reply-001",
        metadata={"finalReply": True},
        expires_at="2099-01-01T00:00:00Z",
        authorized_at=utc_now_iso(),
    )
    monkeypatch.setattr(service, "dispatch_for_session", lambda _session_id: None)

    result = service.recover_pending()

    assert result["recovered"] == 1
    updated = db.get_session_coordination_message(reply["id"])
    assert updated["state"] == "replied"
    assert updated["metadata"]["replyDelivered"] is True


def test_session_deletion_cancels_undelivered_and_retains_delivered_audit(coordination_harness):
    db, service = coordination_harness
    queued = _add_queued_message(db, message_id="coord-delete-queued-001")
    delivered = _add_queued_message(db, message_id="coord-delete-injected-001")
    db.update_session_coordination_message(
        delivered["id"],
        state="injected",
        target_run_id="run-target-delete",
    )

    result = service.prepare_session_deletion(TARGET_SESSION_ID)

    assert result == {"cancelled": 1, "retained": 1}
    cancelled = db.get_session_coordination_message(queued["id"])
    retained = db.get_session_coordination_message(delivered["id"])
    assert cancelled["state"] == "cancelled"
    assert cancelled["errorCode"] == "session_deleted_before_delivery"
    assert cancelled["metadata"]["targetSessionDeleted"] is True
    assert retained["state"] == "injected"
    assert retained["metadata"]["targetSessionDeleted"] is True
