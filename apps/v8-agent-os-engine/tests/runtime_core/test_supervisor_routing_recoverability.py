from __future__ import annotations

import json
from types import SimpleNamespace

from core.native_tools import _detect_session_preferred_command, run_system_command
from core.storage import _sanitize_stock_supervisor_prompt_text
from runtimes.chat.runtime import ChatRuntime


WEREWOLF_APP_REQUEST = (
    "调研狼人杀的玩法，以及配套狼人杀风格的前端界面以及图标，做一个AI狼人杀web应用，"
    "可以接入6个不同供应商的LLM也可指定某个LLM扮演多个角色"
)


def test_planner_fallback_splits_research_and_engineering_for_app_build() -> None:
    runtime = ChatRuntime()
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(
            latest_user_content=WEREWOLF_APP_REQUEST,
            planner_intent_diagnostics={"signals": []},
            planner_mode="suggest",
            task_shape_hint={
                "primaryTaskShape": "project_coding",
                "secondaryTaskShapes": ["research"],
                "suggestedFamilies": ["engineering", "research"],
                "optionalRuntimeGrants": ["research.core"],
            },
        )
    )

    plan = runtime._fallback_planner_plan(chat_run=chat_run, reason="empty_structured_output")

    assert plan["executionStrategy"] == "mixed"
    assert [brief["familyHint"] for brief in plan["taskBriefs"]] == ["research", "engineering"]
    assert plan["taskBriefs"][0]["runtimeAccess"] == ["research.core"]
    assert "<project-workspace>/" in plan["taskBriefs"][1]["writeSet"]
    assert "engineeringTaskCapsule" in plan["taskBriefs"][1]
    assert "mixed_research_engineering_fallback" in plan["riskFlags"]


def test_scaffold_command_is_not_allowed_in_sync_mode() -> None:
    command = (
        'cd "E:\\Projects\\test2" && npx create-next-app@latest ai-werewolf '
        '--typescript --tailwind --eslint --app --src-dir --import-alias "@/*" --use-npm'
    )

    assert _detect_session_preferred_command(command)
    payload = json.loads(run_system_command.func(command=command, mode="sync"))

    assert payload["ok"] is False
    assert payload["kind"] == "command_session_required"
    assert payload["redirect"]["tool"] == "command_session_broker"


def test_stock_supervisor_prompt_sanitizer_removes_default_english_planning_bias() -> None:
    prompt = (
        "# V8 Agent OS Runtime Orchestration Prompt\n\n"
        "## Tool Discipline\n"
        "- Prefer the best runtime-managed path for the current task.\n"
        "- Escalate to low-level or destructive tools only when clearly necessary and safe.\n\n"
        "## Language Protocol\n"
        "- Think and structure plans in English by default.\n"
        "- Reply to the user in the language they used most recently.\n"
        "- Keep canonical runtime, tool, model, and page names unforced; do not translate them unless clarity truly improves.\n\n"
    )

    sanitized = _sanitize_stock_supervisor_prompt_text(prompt)

    assert "Think and structure plans in English by default" not in sanitized
    assert "preferred user-visible language" in sanitized
    assert "## Multi-Runtime Orchestration" in sanitized
    assert "New project creation is a routing choice for Supervisor" in sanitized
