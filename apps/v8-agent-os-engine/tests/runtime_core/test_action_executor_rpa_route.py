from core.action_executor import ActionExecutor


def test_cron_rpa_target_builds_runtime_episode_need():
    need = ActionExecutor._build_rpa_route_need(
        target="template:github-star",
        payload={"variables": {"repo": "openai/codex"}},
        kwargs={"cron_job_id": "nightly-star"},
        task_name="Star repository",
        trigger_source="cron",
        session_id="session-1",
        run_id="run-1",
    )

    assert need["kind"] == "rpa"
    assert need["source"] == "cron"
    assert need["requiredRuntimeAccess"] == ["rpa.core"]
    assert need["targetKind"] == "local_runtime"
    assert need["targetId"] == "rpa"
    assert need["inputs"]["templateId"] == "github-star"
    assert need["inputs"]["variables"] == {"repo": "openai/codex"}
    assert need["inputs"]["cronJobId"] == "nightly-star"
    assert need["inputs"]["nonChatRun"] is True


def test_hook_rpa_payload_can_supply_draft_and_variables():
    need = ActionExecutor._build_rpa_route_need(
        target="rpa:draft:ignored-by-explicit-payload",
        payload={"payload": {"draftId": "draft-123", "variables": {"keyword": "V8"}}},
        kwargs={"event_name": "on_chat_end", "hook_name": "after-chat-rpa"},
        task_name="Run RPA draft",
        trigger_source="hook:on_chat_end",
        session_id="session-2",
        run_id="run-2",
    )

    assert need["source"] == "hook"
    assert need["inputs"]["draftId"] == "draft-123"
    assert need["inputs"]["variables"] == {"keyword": "V8"}
    assert need["inputs"]["eventName"] == "on_chat_end"
    assert need["inputs"]["hookName"] == "after-chat-rpa"


def test_rpa_robot_file_target_is_normalized():
    inputs = ActionExecutor._normalize_rpa_route_inputs(
        "E:/flows/daily.robot",
        payload={},
        kwargs={"trigger_source": "cron", "session_id": "session-3", "run_id": "run-3"},
    )

    assert inputs["robotFile"] == "E:/flows/daily.robot"
    assert inputs["mode"] == "execute"
