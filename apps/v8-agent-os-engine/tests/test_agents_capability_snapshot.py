import unittest

from core.agents import AgentConfig, default_subagent_configs, dump_agent_md, parse_agent_md


class AgentCapabilitySnapshotTests(unittest.TestCase):
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
            self.assertIn("Think before coding", agent.system_prompt)
            self.assertIn("Make surgical changes", agent.system_prompt)
            self.assertIn("Work from verifiable goals", agent.system_prompt)


if __name__ == "__main__":
    unittest.main()
