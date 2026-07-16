from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch



from core.storage import storage  # noqa: E402
from graph import supervisor_context  # noqa: E402


ENGINE_ROOT = Path(__file__).resolve().parents[2]


class _MemoryRuntimeStub:
    def build_session_context(self, **_kwargs):  # noqa: ANN003
        return ""

    def list_artifacts(self, **_kwargs):  # noqa: ANN003
        return []


def _agent(agent_id: str, family: str, *, global_exposure: bool = False, ops: list[str] | None = None) -> dict:
    return {
        "id": agent_id,
        "name": agent_id,
        "description": "specialist",
        "globalExposure": global_exposure,
        "tool_mode": "contextual_auto",
        "tools": [],
        "capabilitySnapshot": {
            "specialistFamily": family,
            "agentClass": "executor",
            "operationCapabilities": ops or ["implement"],
            "domainTags": [family],
        },
    }


def _build_context_bundle(*, specialist_registry: dict, query: str, agents: list[dict], state: dict | None = None) -> dict:
    supervisor_context._STABLE_SYSTEM_CONTEXT_CACHE.clear()
    with (
        patch.object(supervisor_context.storage, "get_workspace_config", return_value={"agent_workspace_path": ""}),
        patch.object(supervisor_context.storage, "get_supervisor_config", return_value={
            "delegation": {"externalWorkers": []},
            "specialistRegistry": specialist_registry,
        }),
        patch.object(supervisor_context.storage, "get_system_identity", return_value={}),
        patch.object(supervisor_context.workspace_resolution_service, "get_main_workspace_path", return_value=str(ENGINE_ROOT)),
        patch.object(supervisor_context.capability_registry, "build_supervisor_summary", return_value=""),
    ):
        bundle = supervisor_context.build_supervisor_system_content(
            state=state or {},
            config=SimpleNamespace(system_prompt="Base prompt"),
            user_query=query,
            current_scope="global",
            scope_chain=["global"],
            session_id=None,
            messages=[],
            loaded_agents=agents,
            supervisor_tools=[],
            memory_runtime=_MemoryRuntimeStub(),
        )
    return bundle


def _build_context(*, specialist_registry: dict, query: str, agents: list[dict], state: dict | None = None) -> str:
    bundle = _build_context_bundle(specialist_registry=specialist_registry, query=query, agents=agents, state=state)
    return str(bundle["specialist_agents_context"])


class SpecialistRegistryConfigTests(unittest.TestCase):
    def test_family_cards_hide_non_global_members_and_keep_global_visible(self):
        agents = [_agent("global-writer", "writing", global_exposure=True)]
        agents.extend(_agent(f"eng-{index}", "engineering") for index in range(12))
        agents.extend(_agent(f"writing-{index}", "writing") for index in range(2))

        rendered = _build_context(
            specialist_registry={"familyModeEnabled": True, "maxMembersPerFamily": 10, "exposureMode": "family_cards"},
            query="please implement and test this runtime fix",
            agents=agents,
        )

        self.assertIn("familyMode=family_cards", rendered)
        self.assertIn("global-writer | family=writing", rendered)
        self.assertIn("[familyCapabilityCards]", rendered)
        self.assertIn("- engineering | members=12", rendered)
        self.assertNotIn("eng-0 | class=", rendered)
        self.assertIn("hiddenMembers=14; revealRequired=true", rendered)

    def test_legacy_matched_members_mode_still_supports_limited_expansion(self):
        agents = [_agent("global-writer", "writing", global_exposure=True)]
        agents.extend(_agent(f"eng-{index}", "engineering") for index in range(12))

        rendered = _build_context(
            specialist_registry={"familyModeEnabled": True, "maxMembersPerFamily": 10, "exposureMode": "legacy_matched_members"},
            query="please implement and test this runtime fix",
            agents=agents,
        )

        self.assertIn("familyMode=legacy_matched_members; familyLimit=10", rendered)
        self.assertIn("[engineering]", rendered)
        self.assertIn("eng-9", rendered)
        self.assertNotIn("- eng-10 | class=", rendered)
        self.assertIn("- name=eng-10 | id=eng-10", rendered)
        self.assertIn("2 more hidden by familyLimit=10", rendered)

    def test_family_mode_off_exposes_all_subagents_in_compact_form(self):
        agents = [_agent(f"eng-{index}", "engineering") for index in range(12)]
        agents.append(_agent("writing-0", "writing"))

        rendered = _build_context(
            specialist_registry={"familyModeEnabled": False, "maxMembersPerFamily": 2},
            query="please implement this runtime fix",
            agents=agents,
        )

        self.assertIn("familyMode=off", rendered)
        self.assertIn("[nonGlobalSubagents]", rendered)
        self.assertIn("eng-11", rendered)
        self.assertIn("writing-0", rendered)
        self.assertNotIn("more hidden by familyLimit", rendered)

    def test_specialist_registry_config_is_clamped(self):
        normalized = storage._normalize_specialist_registry_config({
            "familyModeEnabled": "false",
            "maxMembersPerFamily": 500,
            "exposureMode": "unknown",
        })

        self.assertFalse(normalized["familyModeEnabled"])
        self.assertEqual(normalized["maxMembersPerFamily"], 50)
        self.assertEqual(normalized["exposureMode"], "family_cards")

    def test_remotion_task_shape_high_confidence_auto_reveals_engineering_only(self):
        agents = [
            _agent("eng-impl", "engineering", ops=["implement", "code"]),
            _agent("media-motion", "creative_media", ops=["video", "storyboard"]),
        ]
        bundle = _build_context_bundle(
            specialist_registry={"familyModeEnabled": True, "exposureMode": "family_cards"},
            query="帮我用 Remotion 做一个视频",
            agents=agents,
        )

        rendered = str(bundle["specialist_agents_context"])
        self.assertEqual(bundle["task_shape_hint"]["primaryTaskShape"], "project_coding")
        self.assertIn("primary=project_coding", bundle["task_shape_context"])
        self.assertIn("autoReveal=eligible", bundle["task_shape_context"])
        self.assertIn("- engineering | members=1", rendered)
        self.assertIn("recommended=true", rendered)
        self.assertIn("[engineering] revealSource=task_shape_high_confidence", rendered)
        self.assertIn("eng-impl | class=", rendered)
        self.assertNotIn("media-motion | class=", rendered)

    def test_seedance_high_confidence_auto_reveals_only_creative_media(self):
        agents = [
            _agent("eng-impl", "engineering", ops=["implement", "code"]),
            _agent("media-motion", "creative_media", ops=["video", "storyboard"]),
        ]
        bundle = _build_context_bundle(
            specialist_registry={"familyModeEnabled": True, "exposureMode": "family_cards"},
            query="用 Seedance 2.0 生成一个竖屏视频镜头",
            agents=agents,
        )

        rendered = str(bundle["specialist_agents_context"])
        self.assertEqual(bundle["task_shape_hint"]["primaryTaskShape"], "creative_media")
        self.assertIn("autoReveal=eligible", bundle["task_shape_context"])
        self.assertIn("autoRevealFamilies=creative_media", rendered)
        self.assertIn("[creative_media] revealSource=task_shape_high_confidence", rendered)
        self.assertIn("media-motion | class=", rendered)
        self.assertNotIn("eng-impl | class=", rendered)

    def test_psd_layered_asset_high_confidence_auto_reveals_creative_media(self):
        agents = [
            _agent("eng-impl", "engineering", ops=["implement", "code"]),
            _agent("psd-layer-compositor", "creative_media", ops=["plan_layers", "compose_psd"]),
        ]
        bundle = _build_context_bundle(
            specialist_registry={"familyModeEnabled": True, "exposureMode": "family_cards"},
            query="请做一套可编辑 PSD 分层海报，包含抠图角色、透明背景清理和图层命名。",
            agents=agents,
        )

        rendered = str(bundle["specialist_agents_context"])
        self.assertEqual(bundle["task_shape_hint"]["primaryTaskShape"], "creative_media")
        self.assertIn("autoReveal=eligible", bundle["task_shape_context"])
        self.assertIn("autoRevealFamilies=creative_media", rendered)
        self.assertIn("[creative_media] revealSource=task_shape_high_confidence", rendered)
        self.assertIn("psd-layer-compositor | class=", rendered)
        self.assertNotIn("eng-impl | class=", rendered)

    def test_output_modality_only_keeps_family_cards_without_member_reveal(self):
        agents = [
            _agent("eng-impl", "engineering", ops=["implement", "code"]),
            _agent("media-motion", "creative_media", ops=["video", "storyboard"]),
        ]
        bundle = _build_context_bundle(
            specialist_registry={"familyModeEnabled": True, "exposureMode": "family_cards"},
            query="帮我做一个视频，效果好一点",
            agents=agents,
        )

        rendered = str(bundle["specialist_agents_context"])
        self.assertEqual(bundle["task_shape_hint"]["primaryTaskShape"], "creative_media")
        self.assertFalse(bundle["task_shape_hint"]["autoRevealRecommendation"]["eligible"])
        self.assertIn("autoRevealFamilies=none", rendered)
        self.assertIn("- creative_media | members=1", rendered)
        self.assertIn("recommended=true", rendered)
        self.assertNotIn("[revealedFamilyMembers]", rendered)
        self.assertNotIn("media-motion | class=", rendered)
        self.assertNotIn("eng-impl | class=", rendered)

    def test_high_confidence_auto_reveal_can_be_disabled(self):
        agents = [
            _agent("eng-impl", "engineering", ops=["implement", "code"]),
            _agent("media-motion", "creative_media", ops=["video", "storyboard"]),
        ]
        bundle = _build_context_bundle(
            specialist_registry={
                "familyModeEnabled": True,
                "exposureMode": "family_cards",
                "autoReveal": {"enabled": False},
            },
            query="帮我用 Remotion 做一个视频",
            agents=agents,
        )

        rendered = str(bundle["specialist_agents_context"])
        self.assertIn("autoRevealFamilies=none", rendered)
        self.assertNotIn("eng-impl | class=", rendered)
        self.assertNotIn("media-motion | class=", rendered)

    def test_explicit_subagent_family_mention_reveals_only_that_family(self):
        agents = [
            _agent("eng-impl", "engineering", ops=["implement", "code"]),
            _agent("media-motion", "creative_media", ops=["video", "storyboard"]),
            _agent("writer-docs", "writing", ops=["document"]),
        ]
        rendered = _build_context(
            specialist_registry={"familyModeEnabled": True, "exposureMode": "family_cards"},
            query="@creative_media 做一个镜头设计",
            agents=agents,
            state={"explicit_subagent_families": ["creative_media"]},
        )

        self.assertIn("[revealedFamilyMembers]", rendered)
        self.assertIn("[creative_media] revealSource=user_explicit_mention", rendered)
        self.assertIn("media-motion | class=", rendered)
        self.assertNotIn("eng-impl | class=", rendered)
        self.assertNotIn("writer-docs | class=", rendered)

    def test_no_family_subagent_reveals_under_freelancers_not_engineering(self):
        no_family = _agent("free-helper", "engineering", ops=["help"])
        no_family["capabilitySnapshot"].pop("specialistFamily", None)
        agents = [
            _agent("eng-impl", "engineering", ops=["implement", "code"]),
            no_family,
        ]

        rendered = _build_context(
            specialist_registry={"familyModeEnabled": True, "exposureMode": "family_cards"},
            query="@freelancers 帮我找通用协作子代理",
            agents=agents,
            state={"explicit_subagent_families": ["freelancers"]},
        )

        self.assertIn("[freelancers] revealSource=user_explicit_mention", rendered)
        self.assertIn("free-helper | class=", rendered)
        self.assertNotIn("eng-impl | class=", rendered)

    def test_legacy_top_level_family_does_not_fall_back_to_freelancers(self):
        legacy = _agent("legacy-docs", "engineering", ops=["document"])
        legacy["capabilitySnapshot"].pop("specialistFamily", None)
        legacy["specialistFamily"] = "writing"
        agents = [
            legacy,
            _agent("free-helper", "freelancers", ops=["help"]),
        ]

        rendered = _build_context(
            specialist_registry={"familyModeEnabled": True, "exposureMode": "family_cards"},
            query="@writing 找写作协作",
            agents=agents,
            state={"explicit_subagent_families": ["writing"]},
        )

        self.assertIn("[writing] revealSource=user_explicit_mention", rendered)
        self.assertIn("legacy-docs | class=", rendered)
        self.assertNotIn("free-helper | class=", rendered)


if __name__ == "__main__":
    unittest.main()

