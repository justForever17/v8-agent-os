import unittest
from unittest.mock import patch

from runtimes.chat import planner_orchestration as planner
from runtimes.chat.runtime import ChatRuntime


class PlannerOrchestrationTests(unittest.TestCase):
    def test_prompt_builders_match_chat_runtime_wrappers(self):
        runtime = ChatRuntime()

        self.assertEqual(runtime._planner_system_prompt(), planner.planner_system_prompt())
        self.assertEqual(runtime._planner_json_contract_prompt(), planner.planner_json_contract_prompt())
        self.assertIn("You are the V8 Agent OS planner lane.", planner.planner_system_prompt())
        self.assertIn("Return ONLY one compact JSON object", planner.planner_json_contract_prompt())

    def test_registry_lines_clip_and_summarize_specialists(self):
        long_description = "A" * 260
        registry = {
            "subagents": [
                {
                    "id": "writer-1",
                    "name": "Writing Specialist",
                    "description": long_description,
                    "capabilitySnapshot": {
                        "agentClass": "local_subagent",
                        "domainTags": ["writing", "review"],
                        "runtimeAffinities": ["delegation"],
                    },
                }
            ],
            "externalWorkers": [
                {
                    "id": "worker-1",
                    "name": "External Worker",
                    "description": "External execution lane",
                    "capabilitySnapshot": {"agentClass": "external_worker", "operationCapabilities": ["audit"]},
                }
            ],
        }

        compact = planner.planner_compact_specialist_entry(registry["subagents"][0])
        lines = planner.planner_registry_lines(registry)

        self.assertLessEqual(len(compact["description"]), planner.PLANNER_MAX_DESCRIPTION_CHARS)
        self.assertTrue(compact["description"].endswith("…"))
        self.assertIn("local_subagent", compact["capabilitySummary"])
        self.assertIn("[Local Subagents]", lines)
        self.assertIn("[External Workers]", lines)
        self.assertTrue(any("Writing Specialist" in line for line in lines))

    def test_normalize_planner_plan_payload_wraps_list_payload(self):
        fallback = {
            "planId": "fallback",
            "executionStrategy": "direct",
            "taskBriefs": [],
            "taskGraph": [],
            "qualityFlags": ["fallback"],
        }
        plan = planner.normalize_planner_plan_payload(
            [
                {"kind": "research", "goal": "Collect evidence"},
                {"kind": "engineering", "goal": "Implement patch", "dependency": ["task-1"]},
            ],
            fallback_plan=fallback,
        )

        self.assertEqual(plan["executionStrategy"], "mixed")
        self.assertIn("planner_list_payload_wrapped", plan["qualityFlags"])
        self.assertEqual([item["kind"] for item in plan["capabilityPlan"]], ["research", "engineering"])
        self.assertEqual(len(plan["taskBriefs"]), 2)
        self.assertEqual(len(plan["taskGraph"]), 2)
        self.assertEqual(plan["taskGraph"][1]["dependency"], ["task-1"])

    def test_normalize_planner_plan_payload_falls_back_invalid_strategy_and_rebuilds_graph(self):
        fallback = {
            "planId": "fallback",
            "executionStrategy": "delegate",
            "planSummary": "Fallback plan.",
            "taskBriefs": [{"taskBriefId": "fallback-task", "goal": "Fallback task"}],
            "taskGraph": [],
            "qualityFlags": [],
        }
        plan = planner.normalize_planner_plan_payload(
            {
                "planId": "raw",
                "executionStrategy": "parallel_magic",
                "taskBriefs": [{"taskBriefId": "task-1", "goal": "Do the actual work"}],
            },
            fallback_plan=fallback,
        )

        self.assertEqual(plan["executionStrategy"], "delegate")
        self.assertEqual(plan["taskGraph"], [{"taskBriefId": "task-1", "title": "Do the actual work", "dependency": [], "parallelGroup": ""}])

    def test_create_planner_chat_model_uses_planner_role_when_bound(self):
        class FakeFactory:
            def __init__(self):
                self.calls = []

            def create_for_role(self, role, **kwargs):
                self.calls.append((role, kwargs))
                return {"role": role, "kwargs": kwargs}

        factory = FakeFactory()
        with patch("core.models.control_plane.model_control_plane.get_config", return_value={"roles": {"planner": "deepseek::deepseek-v4-flash"}}):
            model = planner.create_planner_chat_model(model_factory=factory, temperature=0, _request_kind="planner")

        self.assertEqual(model["role"], "planner")
        self.assertEqual(factory.calls[0][1]["_request_kind"], "planner")

    def test_create_planner_chat_model_falls_back_to_supervisor_when_unbound(self):
        class FakeFactory:
            def __init__(self):
                self.calls = []

            def create_for_role(self, role, **kwargs):
                self.calls.append((role, kwargs))
                return role

        factory = FakeFactory()
        with patch("core.models.control_plane.model_control_plane.get_config", return_value={"roles": {"supervisor": "openai::gpt-5.5", "planner": ""}}):
            role = planner.create_planner_chat_model(model_factory=factory, temperature=0)

        self.assertEqual(role, "supervisor")
        self.assertEqual(factory.calls[0][0], "supervisor")


if __name__ == "__main__":
    unittest.main()
