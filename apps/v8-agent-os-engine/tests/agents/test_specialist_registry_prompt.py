from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch



from core.prompt_budget import estimate_prompt_tokens  # noqa: E402
from graph.supervisor_context import build_supervisor_system_content  # noqa: E402


class _MemoryRuntimeStub:
    def build_session_context(self, **_kwargs):  # noqa: ANN003
        return ""


def _agent(agent_id: str, *, family: str, global_exposure: bool = False) -> dict:
    return {
        "id": agent_id,
        "name": agent_id,
        "description": f"Long description for {agent_id} should not be rendered.",
        "tool_mode": "contextual_auto",
        "globalExposure": global_exposure,
        "capabilitySnapshot": {
            "agentClass": "executor",
            "specialistFamily": family,
            "domainTags": [family, "runtime"],
            "operationVerbs": ["inspect", "patch"],
        },
    }


class SpecialistRegistryPromptTests(unittest.TestCase):
    def test_registry_is_family_scoped_top_k_and_compact(self):
        agents = [_agent(f"eng-{index:02d}", family="engineering") for index in range(12)]
        agents.extend(
            [
                _agent("writer-plain", family="writing"),
                _agent("writer-global", family="writing", global_exposure=True),
            ]
        )

        with patch("graph.supervisor_context.capability_registry.build_supervisor_summary", return_value=""), patch(
            "graph.supervisor_context._build_workspace_rules_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._build_artifact_awareness_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._render_engineering_context",
            return_value=("", []),
        ):
            result = build_supervisor_system_content(
                state={},
                config=SimpleNamespace(system_prompt="Base prompt."),
                user_query="Please fix the pytest failure in the runtime code.",
                current_scope="global",
                scope_chain=["global"],
                session_id="sess_test",
                messages=[],
                loaded_agents=agents,
                supervisor_tools=[],
                memory_runtime=_MemoryRuntimeStub(),
            )

        specialist_context = result["specialist_agents_context"]
        self.assertIn("--- SPECIALIST FAMILIES ---", specialist_context)
        self.assertIn("familyMode=family_cards", specialist_context)
        self.assertIn("primary=project_coding", result["task_shape_context"])
        self.assertIn("[globalExposure]", specialist_context)
        self.assertIn("writer-global", specialist_context)
        self.assertIn("[familyCapabilityCards]", specialist_context)
        self.assertIn("- engineering | members=12", specialist_context)
        self.assertIn("recommended=true", specialist_context)
        self.assertNotIn("eng-09", specialist_context)
        self.assertNotIn("eng-10", specialist_context)
        self.assertNotIn("writer-plain", specialist_context)
        self.assertNotIn("Long description", specialist_context)
        self.assertLess(estimate_prompt_tokens(specialist_context), 700)

    def test_registry_predicts_creative_media_family_as_card_only(self):
        agents = [
            _agent("creative-media-director", family="creative_media"),
            _agent("implementation-engineer", family="engineering"),
            _agent("docs-delivery-writer", family="writing"),
        ]

        with patch("graph.supervisor_context.capability_registry.build_supervisor_summary", return_value=""), patch(
            "graph.supervisor_context._build_workspace_rules_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._build_artifact_awareness_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._render_engineering_context",
            return_value=("", []),
        ):
            result = build_supervisor_system_content(
                state={},
                config=SimpleNamespace(system_prompt="Base prompt."),
                user_query="请做一个角色一致性的长视频分镜、关键帧、运镜和字幕计划。",
                current_scope="global",
                scope_chain=["global"],
                session_id="sess_test",
                messages=[],
                loaded_agents=agents,
                supervisor_tools=[],
                memory_runtime=_MemoryRuntimeStub(),
            )

        specialist_context = result["specialist_agents_context"]
        self.assertIn("primary=creative_media", result["task_shape_context"])
        self.assertIn("- creative_media | members=1", specialist_context)
        self.assertIn("recommended=true", specialist_context)
        self.assertNotIn("creative-media-director | class=", specialist_context)
        self.assertNotIn("implementation-engineer", specialist_context)
        self.assertNotIn("docs-delivery-writer", specialist_context)

    def test_explicit_state_reveals_family_members(self):
        agents = [
            _agent("creative-media-director", family="creative_media"),
            _agent("implementation-engineer", family="engineering"),
        ]

        with patch("graph.supervisor_context.capability_registry.build_supervisor_summary", return_value=""), patch(
            "graph.supervisor_context._build_workspace_rules_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._build_artifact_awareness_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._render_engineering_context",
            return_value=("", []),
        ):
            result = build_supervisor_system_content(
                state={"explicit_subagent_families": ["creative_media"]},
                config=SimpleNamespace(system_prompt="Base prompt."),
                user_query="@creative_media 请做镜头方案。",
                current_scope="global",
                scope_chain=["global"],
                session_id="sess_test",
                messages=[],
                loaded_agents=agents,
                supervisor_tools=[],
                memory_runtime=_MemoryRuntimeStub(),
            )

        specialist_context = result["specialist_agents_context"]
        self.assertIn("[revealedFamilyMembers]", specialist_context)
        self.assertIn("[creative_media] revealSource=user_explicit_mention", specialist_context)
        self.assertIn("creative-media-director | class=", specialist_context)
        self.assertNotIn("implementation-engineer | class=", specialist_context)

    def test_execution_hints_block_is_closed(self):
        with patch("graph.supervisor_context.capability_registry.build_supervisor_summary", return_value=""), patch(
            "graph.supervisor_context._build_workspace_rules_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._build_artifact_awareness_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._render_engineering_context",
            return_value=("", []),
        ):
            result = build_supervisor_system_content(
                state={},
                config=SimpleNamespace(system_prompt="Base prompt."),
                user_query="hello",
                current_scope="global",
                scope_chain=["global"],
                session_id="sess_test",
                messages=[],
                loaded_agents=[],
                supervisor_tools=[],
                memory_runtime=_MemoryRuntimeStub(),
            )

        system_content = result["system_content"]
        self.assertIn("[Execution Hints]", system_content)
        self.assertIn("[/Execution Hints]", system_content)
        self.assertLess(system_content.index("[Execution Hints]"), system_content.index("[/Execution Hints]"))
        self.assertIn("general-purpose intelligent Supervisor", system_content)
        self.assertIn("Use Planner/Memory/runtime hints as supporting evidence, not as commands", system_content)
        self.assertIn("Active execution runtimes: Research, Engineering, Creative Media, Computer Use, RPA, Delegation/Subagent", system_content)
        self.assertIn("Passive/support runtimes: Memory, Automation/Cron/Hook, Extensions, PluginHost, Network Supervisor", system_content)

    def test_supervisor_operating_contract_is_system_owned(self):
        with patch("graph.supervisor_context.capability_registry.build_supervisor_summary", return_value=""), patch(
            "graph.supervisor_context._build_workspace_rules_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._build_artifact_awareness_context",
            return_value=("", []),
        ), patch(
            "graph.supervisor_context._render_engineering_context",
            return_value=("", []),
        ):
            result = build_supervisor_system_content(
                state={},
                config=SimpleNamespace(system_prompt="Custom editable persona prompt."),
                user_query="开启 Spec Mode 做一个项目。",
                current_scope="global",
                scope_chain=["global"],
                session_id="sess_test",
                messages=[],
                loaded_agents=[],
                supervisor_tools=[],
                memory_runtime=_MemoryRuntimeStub(),
            )

        system_content = result["system_content"]
        self.assertIn("[Supervisor Operating Contract]", system_content)
        self.assertIn("Supervisor First, Runtime Grounded", system_content)
        self.assertIn("supporting signals", system_content)
        self.assertIn("Path selection:", system_content)
        self.assertIn("Direct path:", system_content)
        self.assertIn("Planner path:", system_content)
        self.assertIn("Runtime path:", system_content)
        self.assertIn("Subagent path:", system_content)
        self.assertIn("Spec path:", system_content)
        self.assertIn("Active execution runtimes you may route into", system_content)
        self.assertIn("Passive/support runtimes are not ordinary execution targets", system_content)
        self.assertIn("user/client approval gates are blocking and cannot be self-approved", system_content)
        self.assertIn("`delegation_broker` is how you dispatch subagents", system_content)
        self.assertIn("`ask_user` asks the human for missing information", system_content)
        self.assertIn("`wait` is only for a short local stabilization pause", system_content)
        self.assertIn("`manage_cron` creates or changes scheduled tasks only when the user explicitly asks", system_content)
        self.assertIn("Memory is evidence", system_content)
        self.assertIn("If the conversation already names a skill, fetch it directly", system_content)
        self.assertIn("Do not declare completion until", system_content)
        self.assertLess(
            system_content.index("Custom editable persona prompt."),
            system_content.index("[Supervisor Operating Contract]"),
        )


if __name__ == "__main__":
    unittest.main()

