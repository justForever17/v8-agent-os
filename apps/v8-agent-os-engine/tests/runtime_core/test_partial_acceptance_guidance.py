from __future__ import annotations

import asyncio
import json
import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from core.database import DatabaseManager
from core import runtime_episode_control as control
from core import runtime_episode_runner as runner
from core.runtime_episodes import build_runtime_episode
from core.tools.native import delegation, runtime
from core.tools.native.delegation_surface import supervisor_delegation_broker
from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
from erc.runtime_context import bind_runtime_context
from graph.tool_routing import create_routed_tool_node


@pytest.fixture
def database(tmp_path, monkeypatch):
    instance = DatabaseManager(tmp_path / "partial-guidance.db")
    instance.create_or_update_session("session", "partial guidance", user_id="test")
    instance.create_run_record(run_id="run", session_id="session", run_type="chat", status="running")
    for module in (control, runner, runtime, delegation):
        monkeypatch.setattr(module, "db", instance)
    monkeypatch.setattr(control, "emit_runtime_episode_event", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.runtime_episode_runner, "_resume_cross_episode_dependents", lambda *_a: None)
    instance.upsert_runtime_episode_record(build_runtime_episode(
        need={"episodeId": "producer-A", "kind": "delegation", "inputs": {"workerBriefs": [{"taskBriefId": "A", "goal": "public fixture"}]}},
        kind="delegation", state="queued"), session_id="session", run_id="run", enqueue=True)
    claim = instance.claim_runtime_episode(worker_id="owner", lease_seconds=300)
    yield instance, claim


def publish(database, version):
    instance, claim = database
    return control.publish_partial("producer-A", handoff={"outputKey": "ready", "version": version, "sourceVersion": "source-" + version,
        "usableFor": ["consumer-B", "consumer-C"], "compactSummary": "Public READY observation", "proofRefs": ["proof://public/ready"]},
        worker_id="owner", lease_generation=claim["leaseGeneration"])


def tool_call(tool, args, identity, config=None):
    assistant = AIMessage(content="", tool_calls=[{"id": identity, "name": tool.name, "args": args}])
    node = create_routed_tool_node([tool], "supervisor_tools", "supervisor")
    with bind_runtime_context(session_id="session", run_id="run", actor_role="supervisor"):
        result = asyncio.run(node({"messages": [HumanMessage(content="public request"), assistant]}, config or {}))
    commands = result if isinstance(result, list) else [result]
    reply = next(item for command in commands for item in command.update["messages"] if isinstance(item, ToolMessage))
    return assistant, reply


def inspection(tool=runtime.runtime_broker):
    key = "delegation_id" if tool.name == "delegation_broker" else "episode_id"
    assistant, reply = tool_call(tool, {"mode": "inspect", key: "producer-A"}, "inspect-public")
    assert reply.content.startswith("Runtime episode inspection\n")
    return assistant, reply, json.loads(reply.content.split("\n", 1)[1])


@pytest.mark.parametrize("tool", [runtime.runtime_broker, supervisor_delegation_broker])
def test_partial_inspect_toolnode_surface_and_next_request_offer_exact_canonical_action(database, tool):
    instance, _ = database
    partial = publish(database, "v1")
    assistant, reply, view = inspection(tool)
    assert view["executionTerminal"] is False
    action = view["handoffs"][0]["acceptanceAction"]
    assert action["tool"] == "runtime_broker"
    assert action["arguments"] == {"mode": "accept_partial", "episode_id": "producer-A", "handoff_id": partial["handoffRefId"]}
    assert action["allowedConsumers"] == ["consumer-B", "consumer-C"]
    assert action["requiredArguments"] == ["consumers", "reason"]
    assert "subset" in action["guidance"] and "inspect" in action["guidance"].lower()
    assert "not authorization" in action["guidance"]
    model = V8OpenAICompatibleChatModel(model="public-model", api_key="test-key", base_url="https://fixture.invalid/v1")
    payload = model._get_request_payload([HumanMessage(content="public request"), assistant, reply], tools=[convert_to_openai_tool(runtime.runtime_broker)])
    next_view = json.loads(payload["messages"][-1]["content"].split("\n", 1)[1])
    assert next_view["handoffs"][0]["acceptanceAction"] == action
    assert payload["messages"][-1]["tool_call_id"] == payload["messages"][-2]["tool_calls"][0]["id"]
    assert "accept_partial" in payload["tools"][0]["function"]["parameters"]["properties"]["mode"]["description"]
    assert instance.list_runtime_episode_messages(run_id="run", recipient="partial:producer-A", pending_only=False) == []
    assert instance.get_runtime_episode("producer-A")["state"] == "active"
    assert all("acceptanceAction" not in item["payload"] for item in instance.list_runtime_episode_handoffs("producer-A"))


def test_old_partial_has_no_action_and_hint_does_not_bypass_acceptance(database):
    instance, _ = database
    first = publish(database, "v1")
    _, _, first_view = inspection()
    old_action = first_view["handoffs"][0]["acceptanceAction"]
    second = publish(database, "v2")
    _, _, view = inspection()
    handoffs = {item["handoffRefId"]: item for item in view["handoffs"]}
    assert "acceptanceAction" not in handoffs[first["handoffRefId"]]
    assert handoffs[second["handoffRefId"]]["acceptanceAction"]["arguments"]["handoff_id"] == second["handoffRefId"]
    assert instance.list_runtime_episode_messages(run_id="run", recipient="partial:producer-A", pending_only=False) == []
    _, stale = tool_call(runtime.runtime_broker, {**old_action["arguments"], "consumers": ["consumer-B"], "reason": "checked"}, "stale")
    assert "partial_version_superseded" in stale.content
    latest = handoffs[second["handoffRefId"]]["acceptanceAction"]["arguments"]
    _, outside = tool_call(runtime.runtime_broker, {**latest, "consumers": ["unrelated"], "reason": "checked"}, "wrong-scope")
    assert "partial_acceptance_requires_reason_and_declared_consumers" in outside.content
    assert instance.list_runtime_episode_messages(run_id="run", recipient="partial:producer-A", pending_only=False) == []
    tool_call(runtime.runtime_broker, {**latest, "consumers": ["consumer-B"], "reason": "inspected public proof"}, "explicit-accept")
    receipts = instance.list_runtime_episode_messages(run_id="run", recipient="partial:producer-A", pending_only=False)
    assert len(receipts) == 1 and receipts[0]["content"]["consumers"] == ["consumer-B"]
    assert receipts[0]["receipt"]["finalAcceptance"] is False
    assert instance.get_runtime_episode("producer-A")["state"] == "active"


def test_terminal_review_rejection_points_partial_to_inspect_without_accepting(database):
    instance, _ = database
    partial = publish(database, "v1")
    _, reply = tool_call(supervisor_delegation_broker, {"mode": "review_result", "delegation_id": "producer-A",
        "handoff_id": partial["handoffRefId"], "task_brief_id": "A", "decision": "accept", "followup": "public proof"}, "wrong-review")
    assert "delegation_result_not_terminal" in reply.content
    assert "runtime_broker" in reply.content and "accept_partial" in reply.content
    assert instance.list_runtime_episode_messages(run_id="run", recipient="partial:producer-A", pending_only=False) == []


def test_terminal_result_keeps_final_review_owner(database):
    instance, claim = database
    publish(database, "v1")
    instance.commit_runtime_episode_delivery("producer-A", state="completed", session_id="session", run_id="run",
        worker_id="owner", lease_generation=claim["leaseGeneration"], handoff={"handoffId": "final-public", "kind": "delegation",
        "status": "ready", "results": [{"taskBriefId": "A", "delegationId": "producer-A", "status": "ok", "resultText": "verified fact"}]})
    _, _, view = inspection()
    assert view["executionTerminal"] is True
    assert all("acceptanceAction" not in item for item in view["handoffs"])
    _, reply = tool_call(supervisor_delegation_broker, {"mode": "review_result", "delegation_id": "producer-A", "handoff_id": "final-public",
        "task_brief_id": "A", "decision": "accept", "followup": "inspected final public proof"}, "review-final")
    assert "accepted" in reply.content
    assert instance.get_runtime_episode("producer-A")["metadata"]["supervisorAcceptance"]["results"]["A"]["status"] == "accepted"
    assert "partial" in supervisor_delegation_broker.description and "runtime_broker" in supervisor_delegation_broker.description


@pytest.mark.parametrize("tool", [runtime.runtime_broker, supervisor_delegation_broker])
def test_inspection_keeps_proof_and_action_whole_in_actual_next_request(database, tool):
    _, claim = database
    summary = "Public evidence retained: " + "observed process remains active; " * 100
    control.publish_partial("producer-A", handoff={"outputKey": "ready", "version": "v1", "sourceVersion": "source-v1",
        "usableFor": ["consumer-B"], "compactSummary": summary, "proofRefs": ["proof://public/exact-ready"]},
        worker_id="owner", lease_generation=claim["leaseGeneration"])
    assistant, reply, view = inspection(tool)
    assert view["handoffs"][0]["compactSummary"] == summary
    assert view["handoffs"][0]["proofRefs"] == ["proof://public/exact-ready"]
    action = view["handoffs"][0]["acceptanceAction"]
    assert action["arguments"]["mode"] == "accept_partial"
    model = V8OpenAICompatibleChatModel(model="fixture", api_key="fixture-key", base_url="https://fixture.invalid/v1")
    wire = model._get_request_payload([HumanMessage(content="public request"), assistant, reply])
    assert json.loads(wire["messages"][-1]["content"].split("\n", 1)[1]) == view


@pytest.mark.parametrize("tool", [runtime.runtime_broker, supervisor_delegation_broker])
def test_inspection_honors_small_budget_with_complete_recovery_not_broken_proof(database, tool):
    _, claim = database
    control.publish_partial("producer-A", handoff={"outputKey": "ready", "version": "v1", "sourceVersion": "source-v1",
        "usableFor": ["consumer-B"], "compactSummary": "public observation " * 2000, "proofRefs": ["proof://public/full"]},
        worker_id="owner", lease_generation=claim["leaseGeneration"])
    key = "delegation_id" if tool.name == "delegation_broker" else "episode_id"
    _, reply = tool_call(tool, {"mode": "inspect", key: "producer-A"}, "bounded-inspect",
                         {"configurable": {"toolOutputHardMaxChars": 1200}})
    view = json.loads(reply.content.split("\n", 1)[1])
    assert len(reply.content) <= 1200
    assert view["executionTerminal"] is False and view["truncated"] is True
    assert view["handoffsOmitted"] == 1 and view["controlsOmitted"] == 0
    assert view["rawRef"].startswith("toolobs://") and "tool_observation_detail" in view["detailTool"]
    assert "acceptanceAction" not in reply.content and "read" in view["nextAction"].lower()
    # The original handoff remains complete; a preview is never an acceptance.
    stored = database[0].list_runtime_episode_handoffs("producer-A")[0]["payload"]
    assert len(stored["compactSummary"]) == len("public observation " * 2000)
    assert database[0].list_runtime_episode_messages(run_id="run", recipient="partial:producer-A", pending_only=False) == []
    from core.tool_observation_detail import render_tool_observation_detail
    parts, offset = [], 0
    for _ in range(50):
        page = render_tool_observation_detail(view["rawRef"], max_chars=1000, start_char=offset)
        assert "<preview>\n" in page
        parts.append(page.split("<preview>\n", 1)[1].split("\n</preview>", 1)[0])
        next_page = re.search(r"next_start_char=(\d+)", page)
        if not next_page:
            assert "[end of observation]" in page
            break
        assert int(next_page[1]) > offset
        offset = int(next_page[1])
    else:
        pytest.fail("inspection recovery did not finish")
    recovered = json.loads("".join(parts))
    assert recovered["handoffs"][0]["compactSummary"] == stored["compactSummary"]
    assert recovered["handoffs"][0]["proofRefs"] == stored["proofRefs"]
    assert recovered["handoffs"][0]["acceptanceAction"]["arguments"]["handoff_id"] == stored["handoffRefId"]


def test_child_partial_publication_returns_exact_receipt_and_stays_nonterminal(database):
    instance, claim = database
    partial = {"outputKey": "ready", "version": "v1", "sourceVersion": "source-v1", "usableFor": ["consumer-B"],
               "compactSummary": "Public READY observation", "proofRefs": ["proof://public/ready"]}
    assistant = AIMessage(content="", tool_calls=[{"id": "publish-public", "name": "delegation_broker",
                                                 "args": {"mode": "publish_partial", "partial_handoff": partial}}])
    node = create_routed_tool_node([delegation.delegation_broker], "worker_tools", "worker")
    with bind_runtime_context(actor_role="direct_subagent", agent_id="child", subagent_id="child",
                              delegation_id="producer-A", delegation_depth=1, session_id="session", run_id="run",
                              runtime_episode_lease={"episodeId": "producer-A", "worker_id": "owner",
                                                     "lease_generation": claim["leaseGeneration"]}):
        result = asyncio.run(node({"messages": [HumanMessage(content="Publish readiness and continue A."), assistant]}))
    commands = result if isinstance(result, list) else [result]
    reply = next(item for command in commands for item in command.update["messages"] if isinstance(item, ToolMessage))
    assert reply.content.startswith("Partial handoff published\n")
    receipt = json.loads(reply.content.split("\n", 1)[1])
    stored = instance.list_runtime_episode_handoffs("producer-A")[0]["payload"]
    for key in ("handoffRefId", "outputKey", "version", "sourceVersion", "usableFor"):
        assert receipt[key] == stored[key]
    assert receipt["executionTerminal"] is False and receipt["status"] == "partial"
    assert "Continue" in receipt["nextAction"] and "not" in receipt["nextAction"]
    assert instance.get_runtime_episode("producer-A")["state"] == "active"
    assert instance.list_runtime_episode_messages(run_id="run", recipient="partial:producer-A", pending_only=False) == []
    model = V8OpenAICompatibleChatModel(model="fixture", api_key="fixture-key", base_url="https://fixture.invalid/v1")
    wire = model._get_request_payload([HumanMessage(content="public request"), assistant, reply])
    assert json.loads(wire["messages"][-1]["content"].split("\n", 1)[1]) == receipt
