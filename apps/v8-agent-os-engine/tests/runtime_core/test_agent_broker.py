from __future__ import annotations

import json
from copy import deepcopy

from langchain_core.messages import ToolMessage

import core.tools.native.agent as agent_tools
import core.tools.native.delegation as delegation_tools
import core.native_tools as native_tools
from core.agents import build_subagent_registry_snapshot
from erc.runtime_context import bind_runtime_context
from graph.supervisor_builder import _make_dynamic_agent_node_resolver


class _FakeAgentStorage:
    def __init__(self) -> None:
        self.agents: dict[str, dict] = {
            "existing-researcher": {
                "id": "existing-researcher",
                "name": "Existing Researcher",
                "description": "Collects source-backed evidence.",
                "model": "model-default",
                "system_prompt": "Research carefully.",
                "tool_mode": "contextual_auto",
                "tools": [],
                "isEnabled": True,
                "capabilitySnapshot": {"specialistFamily": "research", "runtimeBindings": []},
            }
        }

    def get_all_agents(self):
        return [deepcopy(item) for item in self.agents.values()]

    def get_agent(self, agent_id: str):
        item = self.agents.get(agent_id)
        return deepcopy(item) if item else None

    def save_agent(self, payload):
        saved = deepcopy(payload)
        saved.setdefault("isEnabled", True)
        self.agents[str(saved["id"])] = saved

    def delete_agent(self, agent_id: str):
        return self.agents.pop(agent_id, None) is not None

    def get_default_agent_model_id(self):
        return "model-default"

    def get_supervisor_config(self):
        return {}


def _payload(value: str) -> dict:
    return json.loads(value)


def _create_args() -> dict:
    return {
        "mode": "create",
        "name": "UI Accessibility Auditor",
        "description": "Audits keyboard, contrast, and screen-reader behavior.",
        "systemPrompt": "Inspect the assigned UI boundary and return concise evidence.",
        "family": "engineering",
        "modelId": "model-default",
        "runtimeBindings": [{"runtimeKind": "engineering", "toolGroup": "engineering.core"}],
        "domainTags": ["accessibility", "frontend"],
        "operationCapabilities": ["audit", "verify"],
    }


def test_agent_broker_lists_all_or_one_family_and_is_supervisor_only(monkeypatch) -> None:
    fake = _FakeAgentStorage()
    monkeypatch.setattr(agent_tools, "storage", fake)

    with bind_runtime_context(runtime_kind="chat", actor_role="supervisor", agent_id="supervisor"):
        all_payload = _payload(agent_tools.agent_broker.func(mode="list", state={}))
        family_payload = _payload(agent_tools.agent_broker.func(mode="list", family="research", state={}))

    assert all_payload["count"] == 1
    assert all_payload["items"][0]["name"] == "Existing Researcher"
    assert family_payload["count"] == 1
    assert family_payload["family"] == "research"

    with bind_runtime_context(runtime_kind="subagent", actor_role="direct_subagent", agent_id="existing-researcher"):
        denied = _payload(agent_tools.agent_broker.func(mode="list", state={}))
    assert denied["error"] == "agent_broker_supervisor_only"


def test_agent_broker_create_requires_scoped_approval_then_persists_and_validates(monkeypatch) -> None:
    fake = _FakeAgentStorage()
    monkeypatch.setattr(agent_tools, "storage", fake)

    with bind_runtime_context(runtime_kind="chat", actor_role="supervisor", agent_id="supervisor"):
        draft = _payload(agent_tools.agent_broker.func(**_create_args(), state={}))
        digest = draft["authorizationDigest"]
        assert draft["error"] == "authorization_quote_required"

        approval_text = "同意创建 UI Accessibility Auditor"
        approved_state = {
            "messages": [
                ToolMessage(
                    content=approval_text,
                    name="ask_user",
                    tool_call_id="ask-create-agent",
                    additional_kwargs={"interactionKind": "ask_user"},
                )
            ]
        }
        created = _payload(
            agent_tools.agent_broker.func(
                **_create_args(),
                userAuthorizationQuote=approval_text,
                authorizationDigest=digest,
                state=approved_state,
            )
        )
        validated = _payload(
            agent_tools.agent_broker.func(
                mode="validate",
                agentName="UI Accessibility Auditor",
                state=approved_state,
            )
        )

    assert created["ok"] is True
    assert created["status"] == "created"
    assert created["item"]["runtimeBindings"][0]["runtimeKind"] == "engineering"
    assert fake.get_agent(created["item"]["agentId"])["createdBy"] == "supervisor"
    assert validated["ok"] is True
    assert validated["status"] == "ready"
    assert validated["item"]["effectiveModelId"] == "model-default"


def test_agent_broker_rejects_modified_contract_after_ask_user(monkeypatch) -> None:
    fake = _FakeAgentStorage()
    monkeypatch.setattr(agent_tools, "storage", fake)

    with bind_runtime_context(runtime_kind="chat", actor_role="supervisor", agent_id="supervisor"):
        draft = _payload(agent_tools.agent_broker.func(**_create_args(), state={}))
        changed_args = {**_create_args(), "description": "A materially different persistent role."}
        approval_text = "同意创建 UI Accessibility Auditor"
        result = _payload(
            agent_tools.agent_broker.func(
                **changed_args,
                userAuthorizationQuote=approval_text,
                authorizationDigest=draft["authorizationDigest"],
                state={
                    "messages": [
                        ToolMessage(
                            content=approval_text,
                            name="ask_user",
                            tool_call_id="ask-create-agent",
                            additional_kwargs={"interactionKind": "ask_user"},
                        )
                    ]
                },
            )
        )

    assert result["ok"] is False
    assert result["error"] == "authorization_digest_mismatch"
    assert fake.get_agent("ui-accessibility-auditor") is None


def test_dynamic_agent_node_resolver_loads_new_registry_entry_once() -> None:
    fake = _FakeAgentStorage()
    fake.save_agent(
        {
            "id": "new-agent",
            "name": "New Agent",
            "description": "Newly approved role.",
            "system_prompt": "Do the assigned task.",
        }
    )
    node_cache: dict[str, dict] = {}
    builds: list[list[str]] = []

    def _build(records):
        builds.append([str(item["id"]) for item in records])
        return {str(item["id"]): {"node": f"node:{item['id']}"} for item in records}

    resolve = _make_dynamic_agent_node_resolver(
        storage_manager=fake,
        agent_nodes_map=node_cache,
        build_agent_components=_build,
    )

    first = resolve("new-agent")
    second = resolve("new-agent")

    assert first == {"node": "node:new-agent"}
    assert second is first
    assert builds == [["new-agent"]]


def test_supervisor_can_create_validate_and_dispatch_new_agent_in_same_run(monkeypatch) -> None:
    fake = _FakeAgentStorage()
    monkeypatch.setattr(agent_tools, "storage", fake)
    monkeypatch.setattr(delegation_tools, "storage", fake)
    monkeypatch.setattr(native_tools, "storage", fake)
    monkeypatch.setattr(delegation_tools, "persist_runtime_episode", lambda episode, **_kwargs: dict(episode))
    monkeypatch.setattr(delegation_tools, "emit_runtime_episode_event", lambda *_args, **_kwargs: None)
    initial_snapshot = build_subagent_registry_snapshot(fake.get_all_agents())

    with bind_runtime_context(runtime_kind="chat", actor_role="supervisor", agent_id="supervisor"):
        draft = _payload(agent_tools.agent_broker.func(**_create_args(), state={}))
        approval_text = "同意创建 UI Accessibility Auditor"
        approval_message = ToolMessage(
            content=approval_text,
            name="ask_user",
            tool_call_id="ask-create-agent",
            additional_kwargs={"interactionKind": "ask_user"},
        )
        created_raw = agent_tools.agent_broker.func(
            **_create_args(),
            userAuthorizationQuote=approval_text,
            authorizationDigest=draft["authorizationDigest"],
            state={"messages": [approval_message]},
        )
        created = _payload(created_raw)
        validated = _payload(
            agent_tools.agent_broker.func(
                mode="validate",
                agentName="UI Accessibility Auditor",
                state={"messages": [approval_message]},
            )
        )
        created_message = ToolMessage(
            content=created_raw,
            name="agent_broker",
            tool_call_id="create-approved-agent",
        )
        command = delegation_tools.delegation_broker.func(
            mode="dispatch",
            tasks=[
                {
                    "taskBriefId": "audit-current-run",
                    "targetAgentName": "UI Accessibility Auditor",
                    "goal": "Audit one bounded UI surface and return evidence.",
                    "expectedOutputs": ["A concise accessibility audit."],
                    "acceptanceContract": "Report pass or fail with concrete evidence.",
                    "toolPolicy": {"mode": "none"},
                    "executionLaneHint": "subagent",
                }
            ],
            state={
                "session_id": "session-current-run-agent-create",
                "run_id": "run-current-run-agent-create",
                "subagent_registry_snapshot": initial_snapshot,
                "messages": [approval_message, created_message],
            },
            tool_call_id="dispatch-created-agent",
        )

    command_payload = json.loads(command.update["messages"][0].content)
    assert created["status"] == "created"
    assert validated["status"] == "ready"
    assert command_payload["items"][0]["status"] == "queued", command_payload
    assert command_payload["items"][0]["targetId"] == created["item"]["agentId"]
    assert command_payload["registryVersion"] != initial_snapshot["version"]
