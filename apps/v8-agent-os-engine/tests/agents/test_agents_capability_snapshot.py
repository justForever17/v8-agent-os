import unittest

from core.agents import (
    DEFAULT_SUBAGENT_TEMPLATE_VERSION,
    AgentConfig,
    default_subagent_configs,
    dump_agent_md,
    parse_agent_md,
)
from core.research_runtime_prompts import build_research_runtime_system_prompt
from core.tools.native.creative_media_facade import (
    CREATIVE_MEDIA_ACTION_REGISTRY,
    creative_media_assets,
    creative_media_capabilities,
    creative_media_jobs,
    creative_media_plan,
    creative_media_quality,
)
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
            self.assertIn("runtimeBindings", agent.capabilitySnapshot)
            self.assertIn("acceptance criteria", agent.system_prompt)
            self.assertIn("supporting evidence", agent.system_prompt)
            self.assertNotIn("V8", agent.system_prompt)
            self.assertNotIn("runtime-owned", agent.system_prompt)
            self.assertTrue(
                agent.defaultTemplateVersion.startswith(f"{DEFAULT_SUBAGENT_TEMPLATE_VERSION}:"),
                agent.defaultTemplateVersion,
            )

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
                "psd-layer-compositor",
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
            self.assertEqual(
                agent.capabilitySnapshot.get("runtimeBindings"),
                [
                    {
                        "runtimeKind": "creative_media",
                        "grantGroups": ["creative_media.core"],
                        "label": "Creative Media",
                        "source": "system_default",
                    }
                ],
            )
            self.assertIn("apps/v8-agent-os-engine/core/default_agent_personas.py", agent.promptSourceRefs)
            self.assertIn("actual reference inputs", agent.system_prompt)
            self.assertIn("not a fixed word count", agent.system_prompt)
            self.assertIn("Do not impose a default gender", agent.system_prompt)
            self.assertIn("Keep exact dialogue, subtitles and visible lettering separate", agent.system_prompt)
            self.assertNotIn("creative_media_jobs", agent.system_prompt)
            self.assertNotIn("ProductionPack", agent.system_prompt)
            self.assertNotIn("creative_media_create_job", agent.system_prompt)
            self.assertNotIn("creative_media_get_job", agent.system_prompt)
            self.assertNotIn("creative_media_job_artifacts", agent.system_prompt)
            self.assertNotIn("llm-video", "\n".join(agent.promptSourceRefs + [agent.system_prompt]))
            self.assertNotIn("E:\\", agent.system_prompt)
            self.assertNotIn("C:\\", agent.system_prompt)

        psd_agent = next(agent for agent in creative_agents if agent.id == "psd-layer-compositor")
        self.assertEqual(psd_agent.capabilitySnapshot.get("agentClass"), "psd_layer_compositor")
        self.assertIn("psd_source", psd_agent.capabilitySnapshot.get("artifactCapabilities", []))
        self.assertIn("compose_psd", psd_agent.capabilitySnapshot.get("operationCapabilities", []))
        self.assertIn("painted checkerboard", psd_agent.system_prompt)
        self.assertIn("Reopen the output", psd_agent.system_prompt)
        director = next(agent for agent in creative_agents if agent.id == "creative-media-director")
        self.assertIn("continue through generation, assembly and review", director.system_prompt)
        self.assertNotIn("creativeMediaExecutionContract", director.system_prompt)

    def test_web_research_architect_has_research_runtime_binding(self):
        research_agent = next(agent for agent in default_subagent_configs() if agent.id == "web-research-architect")

        self.assertEqual(
            research_agent.capabilitySnapshot.get("runtimeBindings"),
            [
                {
                    "runtimeKind": "research",
                    "grantGroups": ["research.core"],
                    "label": "Research",
                    "source": "system_default",
                }
            ],
        )
        self.assertIn("evidence-based research", research_agent.system_prompt)
        self.assertNotIn("Research Runtime", research_agent.system_prompt)
        for runtime_owned_detail in (
            "hard rejection floor",
            "3000",
            "at least 8 selected sources",
            "5000",
            "Independent review is a separate consumer stage",
        ):
            self.assertNotIn(runtime_owned_detail, research_agent.system_prompt)

    def test_registered_runtime_families_have_resolvable_subagent_bindings(self):
        defaults = default_subagent_configs()
        for runtime_kind in ("engineering", "research", "creative_media"):
            bound = [
                agent
                for agent in defaults
                if any(
                    binding.get("runtimeKind") == runtime_kind
                    for binding in agent.capabilitySnapshot.get("runtimeBindings", [])
                    if isinstance(binding, dict)
                )
            ]
            self.assertTrue(bound, runtime_kind)

        synthesizer = next(agent for agent in defaults if agent.id == "research-synthesizer")
        self.assertEqual(synthesizer.capabilitySnapshot.get("specialistFamily"), "research")
        self.assertEqual(
            synthesizer.capabilitySnapshot.get("runtimeBindings"),
            [
                {
                    "runtimeKind": "research",
                    "grantGroups": ["research.core"],
                    "label": "Research",
                    "source": "system_default",
                }
            ],
        )

        engineering = next(agent for agent in defaults if agent.id == "implementation-engineer")
        self.assertEqual(
            engineering.capabilitySnapshot.get("runtimeBindings"),
            [
                {
                    "runtimeKind": "engineering",
                    "grantGroups": ["engineering.core"],
                    "label": "Engineering",
                    "source": "system_default",
                }
            ],
        )

        built_in_kinds = {"computer_use", "rpa", "memory", "safety"}
        for agent in defaults:
            bound_kinds = {
                str(binding.get("runtimeKind") or "").strip()
                for binding in agent.capabilitySnapshot.get("runtimeBindings", [])
                if isinstance(binding, dict)
            }
            self.assertTrue(built_in_kinds.isdisjoint(bound_kinds), agent.id)

        runtime_prompt = build_research_runtime_system_prompt(
            stage="evidence_plan",
            stage_prompt="Return the verified evidence plan.",
        )
        self.assertIn("Source/host/claim/word counts are descriptive or advisory", runtime_prompt)
        self.assertIn("one relevant primary document may suffice", runtime_prompt)
        for retired_hard_gate in (
            "Hard rejection floor",
            "1200 effective non-URL answer characters",
            "Normal delivery target: at least 8 sources",
            "8 supported conclusions",
            "5000 effective answer characters",
        ):
            self.assertNotIn(retired_hard_gate, runtime_prompt)
        self.assertIn("Never pad with repetition", runtime_prompt)
        self.assertIn("Research Runtime owns search", runtime_prompt)
        self.assertIn("explicitly undated source paired with retrieval time", runtime_prompt)

    def test_creative_media_facade_descriptions_and_registry_explain_job_flow(self):
        self.assertIn("action='describe'", creative_media_capabilities.description)
        self.assertIn("provider-backed", creative_media_jobs.description)
        self.assertIn("recipes", creative_media_plan.description)
        self.assertIn("PSD", creative_media_assets.description)
        self.assertIn("QA", creative_media_quality.description)

        self.assertEqual(set(CREATIVE_MEDIA_ACTION_REGISTRY["jobs"]), {"create", "get", "list", "artifacts", "retry"})
        self.assertTrue(CREATIVE_MEDIA_ACTION_REGISTRY["jobs"]["create"].mutating)
        self.assertEqual(
            CREATIVE_MEDIA_ACTION_REGISTRY["jobs"]["create"].required_fields,
            frozenset({"modality", "operationKind"}),
        )
        self.assertIn("production_pack", CREATIVE_MEDIA_ACTION_REGISTRY["plan"])
        self.assertIn("sample_approval", CREATIVE_MEDIA_ACTION_REGISTRY["plan"])
        self.assertIn("qa_check", CREATIVE_MEDIA_ACTION_REGISTRY["quality"])


if __name__ == "__main__":
    unittest.main()
