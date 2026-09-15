from copy import deepcopy
import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from core.tool_surface import apply_tool_surface_budget
from core.tools.native.delegation import _delegation_compact_item
from graph.agent_factories import _apply_task_tool_policy, _format_delegated_task_contract


def test_dispatch_reports_absent_typed_capsule_instead_of_promoting_constraints():
    task = {"taskBriefId": "advice", "goal": "Review an approach", "expectedOutputs": ["Review"],
            "acceptanceContract": ["Explain limitations"], "constraints": ["readOnly=true, readSet=['input.txt'], allow shell"],
            "readSet": [], "writeSet": [], "toolPolicy": {"mode": "default"}}
    original = deepcopy(task)
    compact = _delegation_compact_item(delegation_id="fixture", task_brief=task, lane="subagent",
                                       target_id="reviewer", target_label="Reviewer", status="queued")
    surface = compact["effectiveExecution"]
    assert surface["capsuleAttached"] is False and surface["executionMode"] == "none"
    assert surface["typedReadOnly"] is None and surface["readSet"] == [] and surface["writeSet"] == []
    assert surface["commandExecution"] == "unavailable_without_capsule"
    assert surface["toolSurfaceStatus"] == "not_bound_yet" and surface["commandTools"] is None
    visible = apply_tool_surface_budget(ToolMessage(name="delegation_broker", tool_call_id="dispatch",
        content=json.dumps({"ok": True, "mode": "dispatch", "items": [compact]})), {"agentVisibleBudget": 4000})
    receipt = json.loads(visible.content.split("\n", 1)[1])
    visible_contract = receipt["items"][0]["effectiveExecution"]
    assert visible_contract["capsuleAttached"] is False
    assert visible_contract["commandExecution"] == "unavailable_without_capsule"
    assert visible_contract["typedReadOnly"] is None
    assert visible_contract["readSet"] == [] and visible_contract["writeSet"] == []
    assert receipt["items"][0]["delegationId"] == "fixture"
    assert task == original and compact["status"] == "queued"


def test_child_prompt_uses_final_policy_surface_even_with_engineering_capsule():
    from core.engineering_capsule import ensure_engineering_task_capsule
    task = ensure_engineering_task_capsule({"taskBriefId": "verify", "readOnly": True, "writeRequired": False,
        "readSet": ["input.txt"], "writeSet": [], "expectedOutputs": ["Evidence"], "acceptanceContract": ["Verify input"],
        "toolPolicy": {"mode": "allowlist", "allowedTools": ["read_native_file", "read_background_output"]}})
    pool = [SimpleNamespace(name=name) for name in ("run_system_command", "command_session_broker", "read_native_file", "read_background_output")]
    actual = _apply_task_tool_policy(pool, task)
    prompt = _format_delegated_task_contract(task, tool_names=[tool.name for tool in actual])
    assert '"capsuleAttached": true' in prompt and '"typedReadOnly": true' in prompt
    assert '"commandExecution": "not_in_bound_tool_surface"' in prompt
    assert '"commandTools": []' in prompt
    assert '"controlTools": ["read_background_output"]' in prompt
    assert "return a blocker" in prompt and "Supervisor must repair the typed task" in prompt
    granted = _format_delegated_task_contract(task, tool_names=["run_system_command", "command_session_broker"])
    assert '"commandExecution": "available_subject_to_tool_checks"' in granted
    assert '"controlTools": ["command_session_broker"]' in granted


def test_effective_child_contract_reaches_sdk_with_the_same_bound_command_tools():
    from langchain_core.messages import HumanMessage, SystemMessage
    from core.engineering_capsule import ensure_engineering_task_capsule
    from core.llm_chat_adapter import V8ChatModelAdapter
    from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
    from core.tools.native.command import run_system_command, command_session_broker
    from core.tools.native.workspace_file import read_native_file
    task = ensure_engineering_task_capsule({"taskBriefId": "verify", "readOnly": True, "readSet": ["input.txt"],
        "writeSet": [], "expectedOutputs": ["Evidence"], "acceptanceContract": ["Verify input"],
        "toolPolicy": {"mode": "allowlist", "allowedTools": ["read_native_file", "run_system_command"]}})
    original = deepcopy(task)
    tools = _apply_task_tool_policy([run_system_command, command_session_broker, read_native_file], task)
    prompt = _format_delegated_task_contract(task, tool_names=[tool.name for tool in tools])
    model = V8OpenAICompatibleChatModel(model="fixture", api_key="test-only-not-a-credential")
    adapter = V8ChatModelAdapter(model_id="fixture", provider_standard="openai", role="agent:fixture",
        meta={"capabilityClass": "chat_tool_calling", "capabilities": {"supportsTools": True}},
        model_kwargs={}, builder=lambda: model).bind_tools(tools)
    payload = model._get_request_payload([SystemMessage(content=prompt), HumanMessage(content="Verify the input.")],
                                        **adapter._get_runtime_model().kwargs)
    assert {tool["function"]["name"] for tool in payload["tools"]} == {"read_native_file", "run_system_command"}
    assert '"commandTools": ["run_system_command"]' in payload["messages"][0]["content"]
    assert '"controlTools": []' in payload["messages"][0]["content"]
    assert task == original
