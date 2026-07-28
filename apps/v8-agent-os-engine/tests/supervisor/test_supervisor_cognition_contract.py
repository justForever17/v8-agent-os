from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from core.native_tools import NATIVE_TOOLS
from core.runtime_route_contract import runtime_route_contract_example, runtime_route_parameter_guidance
from core.runtime_tool_access import filter_visible_tools_for_actor, grant_runtime_tool_groups
from core.storage import DEFAULT_SUPERVISOR_PROMPT_OVERLAY
from core.tools.research_broker import research_broker
from core.tools.native.runtime import runtime_broker
from core.tools.web_fetcher import web_broker
from erc.capability_registry import capability_registry
from graph.supervisor_context import render_supervisor_direct_tool_registry
import graph.supervisor_execution as module
from graph.supervisor_execution import SUPERVISOR_EXECUTION_AUTHORITY_MAX_CHARS
from graph.supervisor_routing import build_supervisor_toolset


def _tool_names(items):
    return [
        str(getattr(item, "name", getattr(item, "__name__", "")) or "").strip()
        for item in list(items or [])
    ]


def test_user_supervisor_prompt_overlay_defaults_to_empty():
    assert DEFAULT_SUPERVISOR_PROMPT_OVERLAY == ""


def test_research_web_and_managed_episode_descriptions_form_a_clear_ladder():
    summary = capability_registry.build_supervisor_summary(
        user_query="这段自然语言不能触发代码推荐路由"
    )

    assert "Runtime 责任卡，不是任务分类结果" in summary
    assert "推荐路由:" not in summary
    assert "<research_path_ladder>" in summary
    assert "这是唯一的调研选路规则" in summary
    assert "L1 web_broker｜一个已知页面或全新孤立窄事实" in summary
    assert "L2 research_broker｜一个可独立验真的多源问题" in summary
    assert "L3 Research episode｜多个独立事实域" in summary
    assert "不是待用户批准的方案" in summary
    assert "不要停在路线说明或实现偏好提问" in summary
    assert "全部已知域放进等长的 researchBriefIds/researchBriefGoals 数组" in summary
    assert "只在 L3 内对明确缺口补查一次" in summary
    assert "不得降级到 L1/L2" in summary
    assert "research:// 仅是血缘" in summary
    assert "get_evidence" in summary
    assert web_broker.description.startswith("L1 普通网页读取器")
    assert "Use this for one URL/page or one narrow inline lookup" in web_broker.description
    assert "must be repaired there" in web_broker.description
    assert research_broker.description.startswith("L2 聚焦证据工具")
    assert "exactly one focused question" in research_broker.description
    assert "put every known domain" in research_broker.description
    assert "initial researchBriefIds/researchBriefGoals arrays" in research_broker.description
    assert runtime_broker.description.startswith("L3 managed runtime entry")
    assert "initial parallel arrays contain every currently known domain" in runtime_broker.description
    assert "one bounded managed repair" in runtime_broker.description
    assert '"researchBriefIds":["domain-a","domain-b"]' in runtime_broker.description
    assert '"researchBriefGoals":["verify A","verify B"]' in runtime_broker.description
    assert "each brief is one coherent executable/acceptable unit" in runtime_broker.description
    assert "Repair only an exact" in runtime_broker.description
    assert "Engineering shape" in runtime_broker.description
    assert '"dependencies":["implementation"]' in runtime_broker.description

    runtime_schema = runtime_broker.args_schema.model_json_schema()
    public_fields = runtime_schema["properties"]
    assert "need" not in public_fields
    research_ids_description = public_fields["researchBriefIds"]["description"]
    assert "every currently known stable taskBriefId" in research_ids_description
    research_goals_description = public_fields["researchBriefGoals"]["description"]
    assert "matching researchBriefIds by position" in research_goals_description
    task_briefs_description = public_fields["taskBriefs"]["description"]
    assert "Non-Research execution briefs" in task_briefs_description
    assert "bounded writeSet" in task_briefs_description
    assert "Engine performs the strict field/type validation" in task_briefs_description

    direct_registry = render_supervisor_direct_tool_registry([web_broker, research_broker])
    assert "Runtime 能力卡负责模块职责和唯一的 `<research_path_ladder>`" in direct_registry
    assert "第二套路由规则" in direct_registry
    assert "首次 route 把当前已知" not in direct_registry
    assert "状态锁" not in direct_registry
    assert f"- web_broker: {web_broker.description.splitlines()[0]}" in direct_registry
    assert f"- research_broker: {research_broker.description.splitlines()[0]}" in direct_registry
    assert len(direct_registry) < 800

    route_example = runtime_route_contract_example("research")
    assert route_example["researchBriefIds"] == ["research-domain-a", "research-domain-b"]
    assert all(str(goal).startswith("Verify <") for goal in route_example["researchBriefGoals"])

    engineering_example = runtime_route_contract_example("engineering")
    engineering_briefs = engineering_example["taskBriefs"]
    assert [item["taskBriefId"] for item in engineering_briefs] == [
        "engineering-implementation",
        "engineering-verification",
    ]
    assert engineering_briefs[0]["dependencies"] == []
    assert engineering_briefs[1]["dependencies"] == ["engineering-implementation"]
    assert len(engineering_briefs[0]["writeSet"]) == 2
    assert len(engineering_briefs[1]["writeSet"]) == 1

    rpa_example = runtime_route_contract_example("rpa")
    rpa_execution = rpa_example["taskBriefs"][0]["context"]["rpaExecution"]
    assert rpa_execution == {
        "action": "execute",
        "draftId": "<approved RPA draft id>",
        "variables": {"<variable name>": "<typed value>"},
        "timeoutMs": 600000,
    }
    rpa_guidance = runtime_route_parameter_guidance("rpa")
    assert "taskBriefs[].context.rpaExecution" in rpa_guidance["requiredPaths"]
    assert "taskBriefs[].context.rpaExecution.traceRunIds" in rpa_guidance["arrayPaths"]
    assert "taskBriefs[].context.rpaExecution.runIds" in rpa_guidance["arrayPaths"]
    assert any("never infers these values from goal prose" in item for item in rpa_guidance["discipline"])
    validated_rpa = runtime_broker.args_schema.model_validate(rpa_example)
    assert validated_rpa.taskBriefs[0]["context"]["rpaExecution"] == rpa_execution


def test_runtime_broker_rejects_unknown_top_level_brief_fields_instead_of_dropping_them():
    response = runtime_broker.invoke(
        {
            "name": "runtime_broker",
            "type": "tool_call",
            "id": "call_runtime_contract_test",
            "args": {
                "mode": "route",
                "item": {"taskBriefId": "misplaced-brief", "goal": "must not be silently dropped"},
            },
        }
    )

    payload = json.loads(response.content)
    assert response.status == "error"
    assert payload["error"] == "typed_tool_arguments_invalid"
    assert payload["unknownFields"] == ["item"]
    assert "researchBriefIds/researchBriefGoals" in payload["nextAction"]


def test_supervisor_authority_map_balances_direct_engineering_and_runtime_routes():
    text = module.build_supervisor_execution_authority_contract(
        runtime_kind="chat",
        resolved_scope="engineering",
    )
    assert "Engineering Kernel" in text
    assert "bounded self-contained file/command work may be implemented directly" in text
    assert "dependent outputs, isolation, parallelism, execution proof, recovery, or durable handoff" in text
    assert "Follow the single `<research_path_ladder>`" in text
    assert "one bounded repair" in text
    assert "full Research, Creative Media, Computer Use, or RPA workflows" in text
    assert "grandchildren an explicitly narrower subset" in text
    assert "terminal handoff" in text
    assert "Never poll for a phantom handoff" in text
    assert "authority to begin its reversible in-scope work" in text
    assert "Do not end the turn with a proposed plan" in text
    assert "runtime routing are execution choices, not approval gates" in text
    assert "prompt overlay may be intentionally empty" in text
    assert "built-in cognition remains here" in text
    assert "Skill is optional method guidance" in text
    assert "detected OS and shell dialect" in text
    assert "native structured tool calls execute" in text
    assert "pseudo tool blocks" in text
    assert "10 steps" not in text
    assert len(text) < SUPERVISOR_EXECUTION_AUTHORITY_MAX_CHARS


def test_supervisor_tool_projection_keeps_delegation_stable_and_specialist_facades_granted():
    assembled = build_supervisor_toolset(
        fetch_skill_instructions_tool=SimpleNamespace(name="fetch_skill_instructions"),
        filtered_native_tools=capability_registry.filter_direct_tools(NATIVE_TOOLS),
        external_tools=[],
        all_mcp_tools=[],
        supervisor_allowed_tools=None,
        config_allowed_tools=None,
    )

    baseline_names = _tool_names(
        filter_visible_tools_for_actor(assembled, actor="supervisor", route_context={})
    )
    assert "delegation_broker" in baseline_names
    assert "runtime_broker" in baseline_names
    assert "run_system_command" in baseline_names
    assert "read_native_file" in baseline_names
    assert "write_native_file" in baseline_names
    assert not [name for name in baseline_names if name.startswith("creative_media_")]

    route_context, grants, rejected = grant_runtime_tool_groups(
        {},
        ["creative_media.core"],
        reason="bounded creative facade test",
    )
    assert rejected == []
    assert [item["group"] for item in grants] == ["creative_media.core"]
    granted_names = _tool_names(
        filter_visible_tools_for_actor(
            assembled,
            actor="supervisor",
            route_context=route_context,
        )
    )
    assert {name for name in granted_names if name.startswith("creative_media_")} == {
        "creative_media_capabilities",
        "creative_media_plan",
        "creative_media_assets",
        "creative_media_jobs",
        "creative_media_edit",
        "creative_media_quality",
    }


def test_prepare_supervisor_messages_injects_authority_map_without_replacing_history(monkeypatch):
    monkeypatch.setattr(module, "get_runtime_context", lambda: {"runtime_kind": "chat"})
    prepared_context = SimpleNamespace(messages=[HumanMessage(content="用户任务")], audit={})
    fake_orchestrator = SimpleNamespace(prepare=lambda **kwargs: prepared_context)
    result = module.prepare_supervisor_messages(
        messages=[HumanMessage(content="用户任务")],
        system_content="base supervisor prompt",
        prompt_segments=[],
        ensure_reasoning_content=lambda message: message,
        sanitize_message_chain=lambda messages: list(messages),
        context_orchestrator=fake_orchestrator,
        resolved_model_id="model",
        resolved_scope="scope",
        scope_chain=[],
        remaining_steps=10,
    )
    assert result == prepared_context.messages
    # The fake captures the leading system prompt for direct contract testing.
    captured = []
    fake_orchestrator.prepare = lambda **kwargs: captured.append(kwargs) or prepared_context
    module.prepare_supervisor_messages(
        messages=[HumanMessage(content="用户任务")],
        system_content="base supervisor prompt",
        prompt_segments=[],
        ensure_reasoning_content=lambda message: message,
        sanitize_message_chain=lambda messages: list(messages),
        context_orchestrator=fake_orchestrator,
        resolved_model_id="model",
        resolved_scope="scope",
        scope_chain=[],
        remaining_steps=10,
    )
    assert "Supervisor Execution Authority" in captured[0]["leading_system_content"]


def test_supervisor_debug_surface_does_not_print_opaque_continuation(capsys):
    module.debug_supervisor_messages(
        [
            AIMessage(
                content=[
                    {"type": "reasoning", "summary": [], "encrypted_content": "secret-opaque"},
                    {"type": "text", "text": "visible"},
                ]
            )
        ]
    )
    output = capsys.readouterr().out
    assert "secret-opaque" not in output
    assert "opaque provider continuation preserved" in output
    assert "visible" in output
