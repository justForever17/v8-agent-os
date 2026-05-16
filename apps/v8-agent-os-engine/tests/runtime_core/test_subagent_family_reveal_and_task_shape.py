from __future__ import annotations

from core.delegation_broker import (
    choose_best_local_agent_with_diagnostics,
    normalize_task_brief,
    reveal_subagent_family,
)
from core.task_shape_classifier import classify_task_shape
from api.models import ChatRequest
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
            "plannerSuitability": "high",
        },
    }


def test_remotion_is_project_coding_with_creative_media_secondary() -> None:
    hint = classify_task_shape("帮我用 Remotion 做一个短视频")

    assert hint["primaryTaskShape"] == "project_coding"
    assert "creative_media" in hint["secondaryTaskShapes"]
    assert hint["suggestedFamilies"][0] == "engineering"
    assert hint["autoRevealRecommendation"]["eligible"] is True
    assert hint["autoRevealRecommendation"]["families"] == ["engineering"]
    assert hint["policy"] == "hint_only_conservative_auto_reveal_recommendation_no_grant"


def test_seedance_provider_request_is_creative_media_hint_only() -> None:
    hint = classify_task_shape("用 Seedance 2.0 生成一个视频镜头")

    assert hint["primaryTaskShape"] == "creative_media"
    assert "creative_media" in hint["suggestedFamilies"]
    assert "creative_media.core" in hint["optionalRuntimeGrants"]
    assert hint["autoRevealRecommendation"]["eligible"] is True
    assert hint["autoRevealRecommendation"]["families"] == ["creative_media"]


def test_research_request_recommends_research_family_and_runtime_grant() -> None:
    hint = classify_task_shape("联网调研最新的 OpenAI API 官方文档，给出引用来源")

    assert hint["primaryTaskShape"] == "research"
    assert hint["suggestedFamilies"][0] == "research"
    assert "research.core" in hint["optionalRuntimeGrants"]
    assert hint["autoRevealRecommendation"]["families"] == ["research"]


def test_project_coding_with_latest_docs_keeps_engineering_primary_and_research_secondary() -> None:
    hint = classify_task_shape("修复 Next.js 项目问题，先查最新官方文档再改代码")

    assert hint["primaryTaskShape"] == "project_coding"
    assert hint["suggestedFamilies"][0] == "engineering"
    assert "research" in hint["secondaryTaskShapes"]
    assert "research.core" in hint["optionalRuntimeGrants"]


def test_research_plus_new_frontend_app_is_project_coding_with_research_secondary() -> None:
    hint = classify_task_shape(
        "调研狼人杀的玩法，以及配套狼人杀风格的前端界面以及图标，做一个AI狼人杀web应用，可以接入6个不同供应商的LLM"
    )

    assert hint["primaryTaskShape"] == "project_coding"
    assert hint["suggestedFamilies"][0] == "engineering"
    assert "research" in hint["secondaryTaskShapes"]
    assert "research.core" in hint["optionalRuntimeGrants"]


def test_multilingual_aliases_feed_task_shape_classifier() -> None:
    remotion_hint = classify_task_shape("Implementa un vídeo con Remotion")
    seedance_hint = classify_task_shape("Seedanceで動画を生成して")

    assert remotion_hint["primaryTaskShape"] == "project_coding"
    assert remotion_hint["autoRevealRecommendation"]["families"] == ["engineering"]
    assert seedance_hint["primaryTaskShape"] == "creative_media"
    assert seedance_hint["autoRevealRecommendation"]["families"] == ["creative_media"]


def test_output_modality_only_does_not_auto_reveal() -> None:
    hint = classify_task_shape("做一个视频")

    assert hint["primaryTaskShape"] == "creative_media"
    assert "output_modality_only" in hint["ambiguityFlags"]
    assert hint["autoRevealRecommendation"]["eligible"] is False


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
