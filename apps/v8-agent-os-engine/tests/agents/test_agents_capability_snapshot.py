import unittest

from core.agents import AgentConfig, default_subagent_configs, dump_agent_md, parse_agent_md
from graph.supervisor_context import render_agent_tool_surface_summary


class AgentCapabilitySnapshotTests(unittest.TestCase):
    def test_supervisor_registry_renders_contextual_auto_as_dynamic_tools(self):
        self.assertEqual(
            render_agent_tool_surface_summary({"tool_mode": "contextual_auto", "tools": []}),
            "tools=dynamic(contextual_auto; selected per taskBrief)",
        )
        self.assertEqual(
            render_agent_tool_surface_summary({"tool_mode": "explicit", "tools": ["read_native_file", "web_broker"]}),
            "tools=fixed:2",
        )

    def test_agent_frontmatter_round_trips_capability_snapshot(self):
        config = AgentConfig(
            id="example-agent",
            name="Example Agent",
            description="Example",
            tool_mode="contextual_auto",
            system_prompt="You are an example agent.",
            capabilitySnapshot={
                "agentClass": "executor",
                "domainTags": ["software_engineering"],
                "operationCapabilities": ["implement"],
            },
        )

        content = dump_agent_md(config)
        parsed = parse_agent_md(content, "example-agent.md")

        self.assertEqual(parsed.capabilitySnapshot["agentClass"], "executor")
        self.assertEqual(parsed.capabilitySnapshot["domainTags"], ["software_engineering"])
        self.assertEqual(parsed.roleLabel, "")

    def test_default_subagents_are_contextual_and_have_high_quality_prompts(self):
        defaults = default_subagent_configs()
        self.assertGreaterEqual(len(defaults), 6)

        engineering_agents = [agent for agent in defaults if "software_engineering" in agent.capabilitySnapshot.get("domainTags", [])]
        distractors = [agent for agent in defaults if "software_engineering" not in agent.capabilitySnapshot.get("domainTags", [])]

        self.assertGreaterEqual(len(engineering_agents), 4)
        self.assertGreaterEqual(len(distractors), 2)
        for agent in defaults:
            self.assertEqual(agent.tool_mode, "contextual_auto")
            self.assertTrue(agent.capabilitySnapshot.get("agentClass"))
            self.assertIn("delegated task brief", agent.system_prompt)
            self.assertIn("Keep the solution surgical", agent.system_prompt)
            self.assertIn("Define evidence before claiming completion", agent.system_prompt)

    def test_default_creative_media_subagents_are_seeded_safely(self):
        defaults = default_subagent_configs()
        creative_agents = [
            agent
            for agent in defaults
            if agent.capabilitySnapshot.get("specialistFamily") == "creative_media"
        ]

        self.assertEqual(
            {agent.id for agent in creative_agents},
            {
                "creative-media-director",
                "visual-recipe-engineer",
                "character-continuity-designer",
                "motion-shot-director",
                "audio-post-producer",
            },
        )
        for agent in creative_agents:
            self.assertEqual(agent.createdBy, "system")
            self.assertFalse(agent.globalExposure)
            self.assertEqual(agent.tool_mode, "contextual_auto")
            self.assertIn("creative_media", agent.capabilitySnapshot.get("domainTags", []))
            self.assertIn("docs/creative-runtime/V8_AGENT_OS_MULTIMEDIA_CREATIVE_RUNTIME_BLUEPRINT_ZH.md", agent.promptSourceRefs)
            self.assertIn("Provider-facing image/video/music prompts default to English", agent.system_prompt)
            self.assertIn("Seedance 2.0 exact models", agent.system_prompt)
            self.assertIn("native audiovisual video models", agent.system_prompt)
            self.assertNotIn("llm-video", "\n".join(agent.promptSourceRefs + [agent.system_prompt]))
            self.assertNotIn("E:\\", agent.system_prompt)
            self.assertNotIn("C:\\", agent.system_prompt)


if __name__ == "__main__":
    unittest.main()
