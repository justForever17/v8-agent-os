import asyncio
import hashlib
from copy import deepcopy
import json
import re
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from core.delegation_broker import normalize_task_brief, task_brief_contract_diagnostics
from core.engineering_capsule import effective_engineering_capsule
from erc.runtime_context import bind_runtime_context
from core.tools.native.delegation_surface import supervisor_delegation_broker
from graph.agent_factories import _apply_task_tool_policy
from graph.parallel_support import _validate_required_verification_evidence
from graph.tool_routing import create_routed_tool_node


def misplaced_task():
    return {"taskBriefId": "read-only-execution", "targetAgentName": "Verification Engineer",
        "goal": "Read the supplied script, run it, and report the observed command result. GOAL_TAIL",
        "context": {"readOnly": "true", "writeRequired": "false", "writeSet": "", "readSet": ["verify.py"],
            "command": "python -B -u verify.py", "commandParameters": {"mode": "session", "timeout_seconds": "210"},
            "evidence": "original evidence CONTEXT_TAIL"},
        "expectedOutputs": ["Observed result"], "acceptanceContract": ["Keep the script unchanged; report the execution proof"],
        "toolPolicy": {"mode": "default"}}


def invoke_task(task):
    with bind_runtime_context(actor_role="supervisor", agent_id="supervisor", runtime_kind="chat"):
        command = supervisor_delegation_broker.invoke({"type": "tool_call", "name": "delegation_broker", "id": "context-repair",
            "args": {"mode": "dispatch", "tasks": [task]}})
    return json.loads(command.update["messages"][0].content)


def test_real_public_dispatch_rejects_shadow_context_before_episode_creation(monkeypatch):
    import core.tools.native.delegation as native
    monkeypatch.setattr(native, "persist_runtime_episode", lambda *args, **kwargs: pytest.fail("Invalid task reached episode persistence"))
    raw = misplaced_task()
    original = deepcopy(raw)
    assert effective_engineering_capsule(normalize_task_brief(raw)) == {}
    payload = invoke_task(raw)
    assert payload["ok"] is False and payload["error"] == "task_context_execution_fields"
    assert payload["executionOutcome"] == "not_executed"
    assert set(payload["repairFields"]) == {"tasks.0.readOnly", "tasks.0.writeRequired", "tasks.0.readSet", "tasks.0.writeSet"}
    corrected = payload["exampleTasks"][0]
    assert raw == original and corrected["goal"] == raw["goal"] and corrected["context"] == raw["context"]
    assert corrected["readOnly"] is True and corrected["writeRequired"] is False and corrected["writeSet"] == []
    supervisor_delegation_broker.args_schema.model_validate({"mode": "dispatch", "tasks": [corrected]})
    assert not any(task_brief_contract_diagnostics([corrected]).values())
    assert effective_engineering_capsule(normalize_task_brief(raw)) == {}, "Returning a proposal cannot authorize the original task"


@pytest.mark.parametrize("repeats", [35, 180])
def test_error_and_complete_example_reach_actual_toolnode_agent_surface(monkeypatch, repeats):
    import core.tool_surface as surfaces
    monkeypatch.setattr(surfaces, "record_raw_observation", lambda **kwargs: "toolobs://fixture-context-repair")
    monkeypatch.setattr(surfaces, "_persist_agent_visible_observation", lambda *args, **kwargs: None)
    task = misplaced_task()
    task["goal"] = "Preserve authorized requirements. " * repeats + " GOAL_TAIL"
    task["context"]["evidence"] = "Keep all supplied evidence. " * 180 + " CONTEXT_TAIL"
    task["context"]["readSet"] = ["E:/fixture/isolated-workspace/" + "read-only-validation/" * 5 + name for name in ("verify.py", "result.txt")]
    original_call = AIMessage(content="", tool_calls=[{"name": "delegation_broker", "id": "shadow", "args": {"mode": "dispatch", "tasks": [task]}}])
    node = create_routed_tool_node([supervisor_delegation_broker], "supervisor_tools", "supervisor")
    with bind_runtime_context(actor_role="supervisor", agent_id="supervisor", runtime_kind="chat"):
        result = asyncio.run(node({"messages": [original_call]}))
    message = result[0].update["messages"][0]
    assert "tasks.0.readOnly" in message.content and '"tasks.0.readOnly":true' in message.content
    assert "Nothing has been dispatched" in message.content and "truncated" not in message.content
    assert "executionRequired" not in message.content and "requiredToolNames" not in message.content
    from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
    model = V8OpenAICompatibleChatModel(model="fixture", api_key="test-only-not-a-credential")
    sdk = model._get_request_payload([original_call, message])
    restored = json.loads(sdk["messages"][0]["tool_calls"][0]["function"]["arguments"])["tasks"][0]
    assert restored["goal"] == task["goal"] and restored["context"] == task["context"]
    assert hashlib.sha256(task["goal"].encode()).hexdigest() in sdk["messages"][1]["content"]
    assert hashlib.sha256(json.dumps(task["context"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() in sdk["messages"][1]["content"]


def test_long_task_receipt_retains_full_context_for_existing_detail_reader(monkeypatch):
    import core.tool_surface as surfaces
    captured = []
    monkeypatch.setattr(surfaces, "record_raw_observation", lambda **kwargs: captured.append(kwargs["raw_content"]) or "toolobs://fixture-complete-repair")
    monkeypatch.setattr(surfaces, "_persist_agent_visible_observation", lambda *args, **kwargs: None)
    task = misplaced_task()
    task["context"]["evidence"] = "Evidence to preserve. " * 1000 + " COMPLETE_CONTEXT_TAIL"
    payload = invoke_task(task)
    visible = surfaces.apply_tool_surface_budget(ToolMessage(name="delegation_broker", tool_call_id="long",
        content=json.dumps(payload)), {"agentVisibleBudget": 4000})
    saved = json.loads(captured[0])["exampleTasks"][0]
    assert saved["context"] == task["context"] and saved["goal"] == task["goal"]
    assert "toolobs://fixture-complete-repair" in visible.content and "truncated" not in visible.content
    supervisor_delegation_broker.args_schema.model_validate({"mode": "dispatch", "tasks": [saved]})


@pytest.mark.parametrize("value", ["maybe", None, 0, 1, ""])
def test_diagnostic_never_invents_a_boolean_permission_from_an_unknown_value(value):
    task = misplaced_task()
    task["context"]["readOnly"] = value
    payload = invoke_task(task)
    assert payload["exampleTasks"][0]["readOnly"] == value
    assert type(payload["exampleTasks"][0]["readOnly"]) is type(value)
    assert payload["unresolvedFields"] == ["tasks.0.readOnly"]
    assert effective_engineering_capsule(normalize_task_brief(task)) == {}


def test_mixed_batch_is_rejected_before_any_task_is_dispatched(monkeypatch):
    import core.tools.native.delegation as native
    dispatched = []
    monkeypatch.setattr(native, "persist_runtime_episode", lambda *args, **kwargs: dispatched.append(kwargs) or pytest.fail("Partial batch dispatch"))
    valid = {"taskBriefId": "first-valid", "targetAgentName": "Verification Engineer", "goal": "Analyze supplied facts",
        "context": {"evidence": "The evidence is supplied"}, "expectedOutputs": ["Analysis"],
        "acceptanceContract": ["Explain the supplied evidence"], "toolPolicy": {"mode": "none"}}
    bad = misplaced_task()
    with bind_runtime_context(actor_role="supervisor", agent_id="supervisor", runtime_kind="chat"):
        command = supervisor_delegation_broker.invoke({"type": "tool_call", "name": "delegation_broker", "id": "mixed-batch",
            "args": {"mode": "dispatch", "tasks": [valid, bad]}})
    payload = json.loads(command.update["messages"][0].content)
    assert payload["error"] == "task_context_execution_fields" and not dispatched
    assert all(path.startswith("tasks.1.") for path in payload["repairFields"])
    assert payload["exampleTasks"][0] == valid
    assert payload["exampleTasks"][1]["goal"] == bad["goal"] and payload["exampleTasks"][1]["context"] == bad["context"]


@pytest.mark.parametrize("policy", [{"mode": "none"}, {"mode": "allowlist", "allowedTools": []},
    {"mode": "allowlist", "allowedTools": ["read_native_file"]}, {"mode": "default", "forbiddenTools": ["run_system_command"]}])
def test_explicit_repair_keeps_empty_allowlist_and_forbidden_command(policy):
    task = misplaced_task()
    task["toolPolicy"] = deepcopy(policy)
    corrected = invoke_task(task)["exampleTasks"][0]
    normalized = normalize_task_brief(corrected)
    assert corrected["toolPolicy"] == policy
    tools = _apply_task_tool_policy([SimpleNamespace(name=name) for name in ("read_native_file", "run_system_command", "write_native_file")], normalized)
    assert "run_system_command" not in {tool.name for tool in tools}
    assert "write_native_file" not in {tool.name for tool in tools}


def test_corrected_typed_task_restores_existing_command_binding_and_exact_proof_requirement():
    from core.tools.native.command import run_system_command, command_session_broker
    from core.tools.native.workspace_file import read_native_file, write_native_file
    task = invoke_task(misplaced_task())["exampleTasks"][0]
    task["context"]["runSystemCommandParams"] = {"command": "python -B -u verify.py", "mode": "sync", "timeout_seconds": 30}
    normalized = normalize_task_brief(task)
    bound = _apply_task_tool_policy([run_system_command, command_session_broker, read_native_file, write_native_file], normalized)
    assert {tool.name for tool in bound} == {"run_system_command", "command_session_broker", "read_native_file"}
    branch = {"taskBrief": normalized}
    read_call = AIMessage(content="", tool_calls=[{"name": "read_native_file", "id": "read", "args": {"path": "verify.py"}}])
    read = ToolMessage(name="read_native_file", tool_call_id="read", content=json.dumps({"ok": True, "path": "verify.py", "content": "print('verified')"}))
    helper_call = AIMessage(content="", tool_calls=[{"name": "run_system_command", "id": "helper", "args": {"command": "python -V", "mode": "sync", "timeout_seconds": 30}}])
    helper = ToolMessage(name="run_system_command", tool_call_id="helper", content=json.dumps({"ok": True, "kind": "command_result", "command": "python -V", "returnCode": 0}))
    for messages in ([read_call, read], [read_call, read, helper_call, helper]):
        failure = _validate_required_verification_evidence(branch=branch, delta_messages=messages)
        assert failure and "required_command_not_executed:python -B -u verify.py" in failure["verificationEvidenceMismatches"]
    command = AIMessage(content="", tool_calls=[{"name": "run_system_command", "id": "real", "args": task["context"]["runSystemCommandParams"]}])
    receipt = ToolMessage(name="run_system_command", tool_call_id="real", content=json.dumps({"ok": True, "kind": "command_result", "command": "python -B -u verify.py", "returnCode": 0}))
    assert _validate_required_verification_evidence(branch=branch, delta_messages=[read_call, read, command, receipt]) is None


@pytest.mark.parametrize("context", [{"evidence": {"readOnly": True, "readSet": ["example.py"]}},
    {"command": "python sample.py", "discussion": "Compare the syntax; no execution requested"}, "Supplied analysis facts"])
def test_analysis_only_context_is_not_a_command_execution_requirement(context):
    task = {"taskBriefId": "analysis", "goal": "Explain the supplied facts", "context": context,
        "expectedOutputs": ["Explanation"], "acceptanceContract": ["Explain clearly"], "toolPolicy": {"mode": "none"}}
    assert not any(task_brief_contract_diagnostics([task]).values())
    assert effective_engineering_capsule(normalize_task_brief(task)) == {}
    assert _validate_required_verification_evidence(branch={"taskBrief": normalize_task_brief(task)}, delta_messages=[]) is None


def test_public_schema_teaches_actual_top_level_fields_without_inventing_new_authority_keys():
    schema = supervisor_delegation_broker.args_schema.model_json_schema()
    task = schema["$defs"]["ManualLocalDelegationTask"]["properties"]
    assert "tasks[i]" in task["context"]["description"]
    assert "Top-level" in task["readOnly"]["description"]
    assert task["readOnly"]["type"] == "boolean"
    assert not {"executionRequired", "intent", "requiredToolNames"} & set(task)


def test_nested_worker_context_is_rejected_with_a_complete_corrected_macro():
    from core.tools.native.delegation import delegation_broker
    child = misplaced_task()
    macro = {"taskBriefId": "macro", "targetAgentName": "Verification Engineer", "goal": "Review supplied tasks",
        "context": {"evidence": "supplied"}, "expectedOutputs": ["Two reviews"], "acceptanceContract": ["Use the supplied evidence"],
        "workerBriefs": [child]}
    # workerBriefs is an internal macro contract, not a public task field.
    with bind_runtime_context(actor_role="supervisor", agent_id="supervisor", runtime_kind="chat"):
        command = delegation_broker.func(mode="dispatch", tasks=[macro], tool_call_id="macro-repair", state={})
    payload = json.loads(command.update["messages"][0].content)
    assert payload["error"] == "task_context_execution_fields"
    assert "tasks.0.workerBriefs.0.readOnly" in payload["repairFields"]
    corrected = payload["exampleTasks"][0]
    assert corrected["goal"] == macro["goal"] and corrected["workerBriefs"][0]["context"] == child["context"]
    assert not any(task_brief_contract_diagnostics([corrected]).values())


def test_valid_typed_capsule_preserves_context_facts_without_shadow_rejection():
    task = misplaced_task()
    task.update(readOnly=True, writeRequired=False, readSet=["verify.py"], writeSet=[])
    before = deepcopy(task)
    assert not any(task_brief_contract_diagnostics([task]).values())
    assert task == before


def test_oversized_repair_uses_actual_safe_paged_detail_without_partial_json(tmp_path, monkeypatch):
    import core.observability_db as observations
    from core.native_tools import tool_observation_detail
    from core.tool_surface import apply_tool_surface_budget
    from core.tool_observation_detail import _redact_tool_observation_preview
    monkeypatch.setattr(observations, "observability_db", observations.ObservabilityDatabaseManager(tmp_path / "observations.db"))
    tasks = []
    for index in range(3):
        task = misplaced_task()
        task["taskBriefId"] += str(index)
        task["goal"] = "Preserved goal " * 80 + f" GOAL_END_{index}"
        task["context"]["readSet"] = ["E:/fixture/" + "long-evidence-path/" * 6 + str(n) + ".txt" for n in range(8)]
        task["context"]["privateInfo"] = {"token": 'SYNTHETIC_SECRET_escaped_"value\\n_never_visible'}
        tasks.append(task)
    with bind_runtime_context(actor_role="supervisor", agent_id="supervisor", runtime_kind="chat"):
        raw_message = supervisor_delegation_broker.invoke({"type": "tool_call", "name": "delegation_broker", "id": "oversized",
            "args": {"mode": "dispatch", "tasks": tasks}}).update["messages"][0]
    visible = apply_tool_surface_budget(raw_message.model_copy(update={"name": "delegation_broker"}), {"agentVisibleBudget": 2500})
    assert len(visible.content) <= 2500 and "Omitted complete patch: 12 fields across 3 tasks" in visible.content
    assert "truncated" not in visible.content and '"tasks.0.readOnly"' not in visible.content
    raw_ref = visible.additional_kwargs["v8_tool_output_budget"]["rawRef"]
    assert raw_ref in visible.content
    offset, pieces = 0, []
    for index in range(150):
        message = tool_observation_detail.invoke({"type": "tool_call", "name": "tool_observation_detail", "id": f"page-{index}",
            "args": {"raw_ref": raw_ref, "max_chars": 60000, "start_char": offset}})
        page = apply_tool_surface_budget(message, {"agentVisibleBudget": 1400}).content
        assert len(page) <= 1400 and "SYNTHETIC_SECRET" not in page
        pieces.append(page.split("<preview>\n", 1)[1].split("\n</preview>", 1)[0])
        cursor = re.search(r"next_start_char=(\d+)", page)
        if not cursor:
            assert "[end of observation]" in page
            break
        next_offset = int(cursor.group(1))
        assert next_offset > offset
        offset = next_offset
    else:
        pytest.fail("Repair detail did not reach its complete final page")
    complete = json.loads("".join(pieces))
    expected = json.loads(_redact_tool_observation_preview(raw_message.content))
    assert complete == expected
    for index, restored in enumerate(complete["exampleTasks"]):
        assert restored["goal"] == tasks[index]["goal"]
        assert restored["context"]["readSet"] == tasks[index]["context"]["readSet"]
        assert restored["context"]["privateInfo"]["token"] == "<redacted>"
        hashes = complete["preservedTaskHashes"][index]
        assert hashes["goalSha256"] == hashlib.sha256(tasks[index]["goal"].encode()).hexdigest()
        assert hashes["contextSha256"] == hashlib.sha256(json.dumps(tasks[index]["context"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    supervisor_delegation_broker.args_schema.model_validate({"mode": "dispatch", "tasks": complete["exampleTasks"]})
