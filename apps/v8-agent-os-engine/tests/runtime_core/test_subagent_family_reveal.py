from __future__ import annotations

from api.models import ChatRequest
from core.delegation_broker import (
    choose_best_local_agent_with_diagnostics,
    normalize_task_brief,
    reveal_subagent_family,
)
from graph.agent_factories import _format_delegated_task_contract
from graph.parallel_support import _compact_transcript, _extract_tool_names
from langchain_core.messages import AIMessage, ToolMessage
from runtimes.chat.runtime import ChatRuntime
from unittest.mock import patch


def _agent(agent_id: str, family: str, ops: list[str]) -> dict:
    return {
        "id": agent_id,
        "name": agent_id,
        "isEnabled": True,
        "description": "specialist",
        "capabilitySnapshot": {
            "specialistFamily": family,
            "agentClass": "executor",
            "domainTags": [family],
            "operationCapabilities": ops,
        },
    }


def test_family_hint_filters_local_agent_selection() -> None:
    agents = [
        _agent("eng", "engineering", ["implement", "code"]),
        _agent("media", "creative_media", ["implement", "video"]),
    ]
    task = normalize_task_brief(
        {
            "goal": "Implement a Remotion scene",
            "requiredCapabilities": ["implement"],
            "familyHint": "engineering",
        }
    )

    selected, diagnostics = choose_best_local_agent_with_diagnostics(task, agents)

    assert selected and selected["id"] == "eng"
    assert diagnostics["targetFamily"] == "engineering"
    assert "familyHint:engineering" in diagnostics["matchSignals"]


def test_writing_family_hint_selects_writing_agent() -> None:
    agents = [
        _agent("eng", "engineering", ["write", "fetch_skill_instructions"]),
        _agent("writer", "writing", ["write", "fetch_skill_instructions"]),
    ]
    task = normalize_task_brief(
        {
            "goal": "Write using a named skill",
            "requiredCapabilities": ["write", "fetch_skill_instructions"],
            "familyHint": "writing",
        }
    )

    selected, diagnostics = choose_best_local_agent_with_diagnostics(task, agents)

    assert selected and selected["id"] == "writer"
    assert diagnostics["targetFamily"] == "writing"


def test_preferred_skill_workflow_curator_wins_for_skill_review() -> None:
    agents = [
        _agent("writer", "writing", ["write", "fetch_skill_instructions"]),
        _agent("skill-workflow-curator", "engineering", ["skill_review", "fetch_skill_instructions"]),
    ]
    task = normalize_task_brief(
        {
            "goal": "Review and improve a skill workflow",
            "requiredCapabilities": ["skill_review", "fetch_skill_instructions"],
            "familyHint": "engineering",
            "preferredAgentId": "skill-workflow-curator",
        }
    )

    selected, diagnostics = choose_best_local_agent_with_diagnostics(task, agents)

    assert selected and selected["id"] == "skill-workflow-curator"
    assert diagnostics["selectionReason"] == "preferredAgentId"
    assert "preferredAgentId:skill-workflow-curator" in diagnostics["matchSignals"]


def test_delegated_writing_brief_requires_fetch_skill_instructions() -> None:
    prompt = _format_delegated_task_contract(
        {
            "goal": "Write a proposal with a skill",
            "context": {
                "writingExecutionBrief": {
                    "schema": "v8.writing_execution_brief.v1",
                    "skill": {"idOrName": "doc-coauthoring", "selectionReason": "user named it"},
                    "subagentFirstAction": "fetch_skill_instructions",
                    "authorizedRefs": {"researchRefs": [], "memoryRefs": [], "workspaceRefs": []},
                    "forbiddenInventions": ["Do not invent sources."],
                    "acceptanceCriteria": ["Return final draft plus execution notes."],
                }
            },
        },
    )

    assert "Writing Execution Brief" in prompt
    assert "fetch_skill_instructions(skill_name='doc-coauthoring')" in prompt
    assert "Do not invent sources" in prompt


def test_parallel_subagent_handoff_preserves_empty_ai_tool_call_name() -> None:
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_fetch_skill",
                    "name": "fetch_skill_instructions",
                    "args": {"skill_name": "huashu-nuwa"},
                }
            ],
        ),
        ToolMessage(content='{"summary":"skill loaded"}', tool_call_id="call_fetch_skill"),
    ]

    assert _extract_tool_names(messages) == ["fetch_skill_instructions"]
    assert "使用工具: fetch_skill_instructions" in _compact_transcript(messages)


def test_reveal_subagent_family_returns_compact_members() -> None:
    agents = [
        _agent("eng", "engineering", ["implement", "code"]),
        _agent("media", "creative_media", ["video"]),
    ]

    payload = reveal_subagent_family("creative_media", agents)

    assert payload["found"] is True
    assert payload["memberCount"] == 1
    assert payload["members"][0]["agentId"] == "media"
    assert payload["members"][0]["capabilitySnapshot"]["operationCapabilities"] == ["video"]


def test_chat_runtime_resolves_structured_subagent_family_mention() -> None:
    runtime = ChatRuntime()
    request = ChatRequest(
        messages=[{"role": "user", "content": "请让这个家族参与"}],
        data={
            "contextMentions": [
                {
                    "kind": "subagent_family",
                    "familyId": "creative_media",
                    "name": "Creative Media",
                }
            ]
        },
    )

    with (
        patch("runtimes.chat.runtime.storage.get_supervisor_config", return_value={"specialistRegistry": {"families": []}}),
        patch("runtimes.chat.runtime.storage.get_all_agents", return_value=[_agent("media", "creative_media", ["video"])]),
    ):
        skill_refs = runtime._normalize_skill_references(request)
        mentions = runtime._normalize_context_mentions(request, skill_references=skill_refs)
        resolved = runtime._resolve_explicit_subagent_families(request, mentions)

    assert resolved == ["creative_media"]


def test_chat_runtime_requires_at_prefix_for_raw_family_reveal() -> None:
    runtime = ChatRuntime()
    with (
        patch("runtimes.chat.runtime.storage.get_supervisor_config", return_value={"specialistRegistry": {"families": []}}),
        patch("runtimes.chat.runtime.storage.get_all_agents", return_value=[_agent("media", "creative_media", ["video"])]),
    ):
        plain = ChatRequest(messages=[{"role": "user", "content": "用 creative_media 做视频"}], data={})
        explicit = ChatRequest(messages=[{"role": "user", "content": "@creative_media 做视频"}], data={})

        assert runtime._resolve_explicit_subagent_families(plain, []) == []
        assert runtime._resolve_explicit_subagent_families(explicit, []) == ["creative_media"]
