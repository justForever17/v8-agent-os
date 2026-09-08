from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from core.context_orchestrator import ContextOrchestrator
from core.runtime_tool_access import filter_visible_tools_for_actor
from core.tools.native.delegation import delegation_broker
from core.tools.native.delegation_surface import supervisor_delegation_broker
from graph.agent_factories import _build_agent_system_bundle, _delegated_task_messages


def task(**extra):
    return {"taskBriefId": "verify", "goal": "Verify saved evidence", "expectedOutputs": ["Finding and source"],
            "acceptanceContract": ["Use exact source refs"], **extra}


def test_manual_schema_matches_dispatch_but_preserves_internal_and_child_contracts():
    original = task()
    public = filter_visible_tools_for_actor([delegation_broker], actor="supervisor")[0]
    assert public is supervisor_delegation_broker
    schema = convert_to_openai_tool(public)["function"]["parameters"]
    assert "worker_briefs" not in schema["properties"]
    assert "list" not in schema["properties"]["mode"]["enum"]
    # A missing local target cannot reach execution via the new public schema.
    with pytest.raises(ValidationError):
        public.args_schema.model_validate({"mode": "dispatch", "tasks": [original]})
    for target in ["", "   "]:
        with pytest.raises(ValidationError):
            public.args_schema.model_validate({"mode": "dispatch", "tasks": [task(targetAgentName=target)]})
    for candidate in [task(targetAgentName="Verification Engineer"), task(executionLaneHint="external_worker")]:
        parsed = public.args_schema.model_validate({"mode": "dispatch", "tasks": [candidate]})
        assert parsed.tasks == [candidate]
    assert delegation_broker.args_schema.model_validate({"mode": "dispatch", "tasks": [original]}).tasks == [original]
    assert original == task()


def test_public_dispatch_forwards_exact_contract_and_injected_state(monkeypatch):
    import core.tools.native.delegation_surface as surface
    captured = []
    monkeypatch.setattr(surface.delegation_broker, "func", lambda **kwargs: captured.append(kwargs) or "dispatched")
    brief = task(targetAgentName="Verification Engineer", readOnly=True, writeSet=[],
                 evidenceRefs=["research_exact"], allowChildDelegation=False)
    original = deepcopy(brief)
    state = {"run_id": "owned-run"}
    result = surface.supervisor_delegation_broker.invoke({"type": "tool_call", "id": "call-1", "name": "delegation_broker",
        "args": {"mode": "dispatch", "tasks": [brief], "state": state}})
    assert result.content == "dispatched"
    assert captured[0]["tasks"] == [original]
    assert captured[0]["state"] == state
    assert captured[0]["tool_call_id"] == "call-1"


def test_worker_guidance_matches_write_receipts_and_actual_tool_binding():
    plain = _build_agent_system_bundle(agent_name="Verifier", agent_system_prompt="Inspect evidence.",
        env_context="<environment>\nUser-Visible Language: zh-CN\n</environment>\n")
    assert "another fresh read after each successful write" not in plain["content"]
    assert "reuse the returned version" in plain["content"]
    assert "workers cannot call spec_broker" in plain["content"]
    assert "only for short synchronous commands" not in plain["content"]
    assert "rank_models" not in plain["content"]
    creative = _build_agent_system_bundle(agent_name="Verifier", agent_system_prompt="Inspect evidence.", env_context="",
        available_tool_names=["creative_media_jobs", "creative_media_capabilities"])
    assert "rank_models" in creative["content"]


def test_early_tool_evidence_reaches_context_owner_without_count_based_loss(monkeypatch):
    import core.context_orchestrator as module
    monkeypatch.setattr(module.storage, "get_context_config", lambda: {"compression": {"enabled": True, "use_llm_summary": False}})
    monkeypatch.setattr(module.llm_factory, "get_model_context_window", lambda _model: 100000)
    monkeypatch.setattr(module, "get_runtime_context", lambda: {})
    instruction = HumanMessage(content="Verify claim and preserve exact source", additional_kwargs={"v8_governance_type": "delegated_task_instruction"})
    messages = [instruction]
    for index in range(20):
        messages.extend([AIMessage(content="", tool_calls=[{"name": "read_native_file", "args": {"path": f"file{index}"}, "id": f"r{index}"}]),
                         ToolMessage(content=f"claim-{index} citation S{index} sha256:version-{index}", name="read_native_file", tool_call_id=f"r{index}")])
    branch = _delegated_task_messages([HumanMessage(content="unrelated parent task"), *messages], task())
    prepared = ContextOrchestrator().prepare(messages=[SystemMessage(content="Follow the task contract"), *branch],
        runtime_kind="engineering", target_role="agent:verifier", resolved_model_id="fixture", resolved_scope="workspace:test", scope_chain=["workspace:test"])
    assert not prepared.audit["compaction_applied"]
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "r0" and "sha256:version-0" in m.content for m in prepared.messages)
    assert all("unrelated parent task" not in str(m.content) for m in prepared.messages)
    assert len([m for m in prepared.messages if isinstance(m, ToolMessage)]) == 20
