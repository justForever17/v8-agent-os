from langchain_core.messages import AIMessage

from graph.no_progress_breaker import (
    MIN_REMAINING_STEPS_FOR_TOOL_ROUND,
    apply_remaining_steps_guard,
)


def _tool_response() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": "call_1", "name": "run_system_command", "args": {"command": "pwd"}}],
    )


def test_remaining_steps_guard_keeps_tool_call_when_round_can_complete() -> None:
    response = _tool_response()

    guarded, diagnostics = apply_remaining_steps_guard(
        response,
        MIN_REMAINING_STEPS_FOR_TOOL_ROUND + 1,
    )

    assert guarded is response
    assert diagnostics is None


def test_remaining_steps_guard_stops_new_tool_round_without_resetting_graph() -> None:
    guarded, diagnostics = apply_remaining_steps_guard(
        _tool_response(),
        MIN_REMAINING_STEPS_FOR_TOOL_ROUND,
    )

    assert guarded.tool_calls == []
    assert diagnostics == {
        "reason": "insufficient_remaining_steps_for_tool_round",
        "remaining_steps": MIN_REMAINING_STEPS_FOR_TOOL_ROUND,
        "minimum_steps": MIN_REMAINING_STEPS_FOR_TOOL_ROUND,
        "suppressed_tool_names": ["run_system_command"],
        "checkpoint_preserved": True,
    }
    assert guarded.additional_kwargs["execution_progress_guard"] == diagnostics
    assert "不会" not in str(guarded.content)
    assert "停止开启新的工具回合" in str(guarded.content)


def test_remaining_steps_guard_does_not_replace_final_response() -> None:
    response = AIMessage(content="done")

    guarded, diagnostics = apply_remaining_steps_guard(response, 0)

    assert guarded is response
    assert diagnostics is None


def test_remaining_steps_guard_does_not_invent_a_budget_when_framework_value_is_missing() -> None:
    response = _tool_response()

    guarded, diagnostics = apply_remaining_steps_guard(response, None)

    assert guarded is response
    assert diagnostics is None
