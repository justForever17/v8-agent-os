import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.models import ChatMessage, ChatRequest, ChatRequestData
from core.database import db
from core.runtime_episodes import build_handoff_ref, build_runtime_episode
from graph.workflow_assembly import build_planner_auto_dispatch_node, build_runtime_episode_wait_node
from graph.parallel_support import build_parallel_delegate_join_node
from runtimes.chat.runtime import ChatRuntime, PlannerPlanPayload


class ChatPlannerModeTests(unittest.TestCase):
    def test_planner_model_timeout_uses_deterministic_fallback_event(self):
        class SlowPlannerModel:
            def with_structured_output(self, _schema):
                return self

            async def ainvoke(self, _messages):
                await asyncio.sleep(0.5)
                return {}

        emitted: list[tuple[str, dict]] = []
        runtime = ChatRuntime()
        chat_run = SimpleNamespace(
            active_run_id="run-planner-timeout",
            prepared=SimpleNamespace(
                task_planning_mode=True,
                planner_mode="force",
                is_resume_request=False,
                planner_plan=None,
                planner_intent_diagnostics={"reason": "test"},
                latest_user_content="请规划一次调研和工程实现",
                skill_references=[],
                engineering_trigger_decision={},
                engineering_context_pack=None,
                planner_dispatch_mode="suggest",
                task_shape_hint={},
            ),
            scope_result=SimpleNamespace(
                binding=SimpleNamespace(
                    project_id="project-test",
                    workspace_id="workspace-test",
                    workspace_path="E:/Projects/v8chat",
                    resolved_scope="project",
                )
            ),
            emit_runtime_event=lambda topic, payload, **_: emitted.append((topic, payload)),
        )

        with (
            patch("runtimes.chat.runtime.PLANNER_MODEL_TIMEOUT_SECONDS", 0.1),
            patch("runtimes.chat.runtime.llm_factory.create_for_role", return_value=SlowPlannerModel()),
            patch("runtimes.chat.runtime.workflow_ledger_service.activate_runtime_step", lambda *_, **__: None),
            patch("runtimes.chat.runtime.engineering_lane_service.enrich_planner_plan_with_engineering_contract", lambda plan, **_: plan),
            patch.object(runtime, "_planner_registry_snapshot", return_value={"subagents": [], "externalWorkers": []}),
        ):
            plan = asyncio.run(runtime.ensure_planner_plan(chat_run=chat_run))

        topics = [topic for topic, _payload in emitted]
        self.assertIsInstance(plan, dict)
        self.assertIn("planner.fallback.used", topics)
        self.assertIn("planner.plan.failed", topics)
        self.assertIn("planner.plan.created", topics)
        self.assertTrue((chat_run.prepared.planner_plan or {}).get("planId"))

    def test_planner_structured_failure_repairs_with_plain_json_before_fallback(self):
        class BrokenStructuredPlanner:
            def with_structured_output(self, _schema):
                return self

            async def ainvoke(self, _messages):
                raise ValueError("structured parser rejected bare list")

        class PlainJsonRepairPlanner:
            async def ainvoke(self, _messages):
                return SimpleNamespace(
                    content='[{"kind":"research","reason":"collect sources"},{"kind":"engineering","reason":"implement with proof"}]'
                )

        emitted: list[tuple[str, dict]] = []
        runtime = ChatRuntime()
        chat_run = SimpleNamespace(
            active_run_id="run-planner-repair",
            prepared=SimpleNamespace(
                task_planning_mode=True,
                planner_mode="force",
                is_resume_request=False,
                planner_plan=None,
                planner_intent_diagnostics={"reason": "test"},
                latest_user_content="请先调研再实现，并给出验证结果",
                skill_references=[],
                engineering_trigger_decision={},
                engineering_context_pack=None,
                planner_dispatch_mode="suggest",
                task_shape_hint={},
            ),
            scope_result=SimpleNamespace(
                binding=SimpleNamespace(
                    project_id="project-test",
                    workspace_id="workspace-test",
                    workspace_path="E:/Projects/v8chat",
                    resolved_scope="project",
                )
            ),
            emit_runtime_event=lambda topic, payload, **_: emitted.append((topic, payload)),
        )

        with (
            patch("runtimes.chat.runtime.llm_factory.create_for_role", side_effect=[BrokenStructuredPlanner(), PlainJsonRepairPlanner()]),
            patch("runtimes.chat.runtime.workflow_ledger_service.activate_runtime_step", lambda *_, **__: None),
            patch("runtimes.chat.runtime.engineering_lane_service.enrich_planner_plan_with_engineering_contract", lambda plan, **_: plan),
            patch.object(runtime, "_planner_registry_snapshot", return_value={"subagents": [], "externalWorkers": []}),
        ):
            plan = asyncio.run(runtime.ensure_planner_plan(chat_run=chat_run))

        topics = [topic for topic, _payload in emitted]
        self.assertIsInstance(plan, dict)
        self.assertIn("planner.output.repaired", topics)
        self.assertNotIn("planner.fallback.used", topics)
        self.assertEqual([item.get("kind") for item in plan.get("capabilityPlan") or []], ["research", "engineering"])
        self.assertIn("planner_plain_json_repair_used", plan.get("qualityFlags") or [])

    def test_planner_payload_wraps_bare_capability_plan_list(self):
        payload = PlannerPlanPayload.model_validate(
            [
                {
                    "capability": "research",
                    "kind": "research",
                    "runtime": "research",
                    "reason": "needs current sources",
                }
            ]
        )

        self.assertEqual(payload.executionStrategy, "delegate")
        self.assertEqual(payload.capabilityPlan[0]["kind"], "research")
        self.assertIn("planner_list_payload_wrapped", payload.qualityFlags)

    def test_task_planning_mode_maps_to_force_planner_mode(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="Please plan this migration.")],
            data=ChatRequestData(taskPlanningMode=True),
        )

        _, task_planning_mode, planner_mode, planner_dispatch_mode, diagnostics, *_ = runtime._resolve_request_context(request)

        self.assertTrue(task_planning_mode)
        self.assertEqual(planner_mode, "force")
        self.assertEqual(planner_dispatch_mode, "suggest")
        self.assertTrue(diagnostics.get("matched"))

    def test_auto_planner_mode_uses_intent_detection(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="Break down this refactor into tasks with tests.")],
            data=ChatRequestData(plannerMode="auto"),
        )

        _, task_planning_mode, planner_mode, planner_dispatch_mode, diagnostics, *_ = runtime._resolve_request_context(request)

        self.assertEqual(planner_mode, "auto")
        self.assertEqual(planner_dispatch_mode, "suggest")
        self.assertTrue(task_planning_mode)
        self.assertIn("explicit_planning", diagnostics.get("signals") or [])

    def test_off_planner_mode_disables_legacy_task_planning_flag(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="Plan this, but this request explicitly disables planner mode.")],
            data=ChatRequestData(taskPlanningMode=True, plannerMode="off"),
        )

        _, task_planning_mode, planner_mode, planner_dispatch_mode, *_ = runtime._resolve_request_context(request)

        self.assertEqual(planner_mode, "off")
        self.assertEqual(planner_dispatch_mode, "suggest")
        self.assertFalse(task_planning_mode)

    def test_planner_dispatch_mode_accepts_auto_and_off(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="Plan and delegate this migration.")],
            data=ChatRequestData(plannerMode="force", plannerDispatchMode="auto"),
        )

        _, task_planning_mode, planner_mode, planner_dispatch_mode, *_ = runtime._resolve_request_context(request)

        self.assertTrue(task_planning_mode)
        self.assertEqual(planner_mode, "force")
        self.assertEqual(planner_dispatch_mode, "auto")

    def test_normalize_planner_plan_payload_rebuilds_missing_graph(self):
        fallback_plan = {
            "planId": "plan_fallback",
            "executionStrategy": "delegate",
            "planSummary": "Fallback summary",
            "taskGraph": [],
            "taskBriefs": [],
            "globalAcceptanceContract": "Fallback acceptance",
            "riskFlags": [],
        }

        normalized = ChatRuntime._normalize_planner_plan_payload(
            {
                "planId": "plan_test",
                "executionStrategy": "mixed",
                "planSummary": "Use a mixed strategy",
                "taskBriefs": [
                    {
                        "goal": "Implement planner lane",
                        "writeSet": ["apps/v8-agent-os-engine"],
                        "behaviorScope": ["workspace_changes"],
                        "requiredCapabilities": ["planning"],
                        "acceptanceContract": "Planner lane should emit a structured plan.",
                        "dependency": [],
                        "parallelGroup": "phase-1",
                        "executionLaneHint": "subagent",
                    }
                ],
                "globalAcceptanceContract": "Produce a valid plan.",
                "riskFlags": ["planner_pass"],
            },
            fallback_plan=fallback_plan,
        )

        self.assertEqual(normalized["executionStrategy"], "mixed")
        self.assertEqual(normalized["planId"], "plan_test")
        self.assertEqual(len(normalized["taskBriefs"]), 1)
        self.assertEqual(len(normalized["taskGraph"]), 1)
        self.assertEqual(normalized["taskGraph"][0]["parallelGroup"], "phase-1")
        self.assertTrue(normalized["taskBriefs"][0]["taskBriefId"])

    def test_normalize_planner_plan_payload_rejects_invalid_strategy(self):
        fallback_plan = {
            "planId": "plan_fallback",
            "executionStrategy": "direct",
            "planSummary": "Fallback summary",
            "taskGraph": [],
            "taskBriefs": [
                {
                    "taskBriefId": "task-fallback",
                    "goal": "Fallback task",
                    "context": "",
                    "writeSet": [],
                    "behaviorScope": [],
                    "requiredCapabilities": [],
                    "acceptanceContract": "Fallback acceptance",
                    "dependency": [],
                    "parallelGroup": "",
                    "executionLaneHint": "subagent",
                }
            ],
            "globalAcceptanceContract": "Fallback acceptance",
            "riskFlags": ["fallback"],
        }

        normalized = ChatRuntime._normalize_planner_plan_payload(
            {
                "executionStrategy": "surprise_mode",
                "taskBriefs": [],
            },
            fallback_plan=fallback_plan,
        )

        self.assertEqual(normalized["executionStrategy"], "direct")
        self.assertEqual(normalized["taskBriefs"][0]["taskBriefId"], "task-fallback")
        self.assertEqual(normalized["globalAcceptanceContract"], "Fallback acceptance")

    def test_validate_and_repair_planner_plan_adds_required_contract_fields(self):
        fallback_plan = {
            "planId": "plan_fallback",
            "executionStrategy": "delegate",
            "planSummary": "Fallback summary",
            "taskGraph": [],
            "taskBriefs": [
                {
                    "taskBriefId": "task-fallback",
                    "goal": "Fallback task",
                    "context": "",
                    "writeSet": [],
                    "behaviorScope": ["execution"],
                    "requiredCapabilities": [],
                    "acceptanceContract": "Fallback acceptance",
                    "dependency": [],
                    "parallelGroup": "",
                    "executionLaneHint": "subagent",
                }
            ],
            "globalAcceptanceContract": "Fallback acceptance",
            "riskFlags": [],
            "qualityFlags": [],
            "repairCount": 0,
        }

        repaired = ChatRuntime._validate_and_repair_planner_plan(
            {
                "planId": "plan_test",
                "executionStrategy": "delegate",
                "planSummary": "Ship the change",
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "",
                        "dependency": ["missing-task"],
                        "executionLaneHint": "subagent",
                    }
                ],
                "globalAcceptanceContract": "",
            },
            fallback_plan=fallback_plan,
        )

        self.assertEqual(repaired["executionStrategy"], "delegate")
        self.assertEqual(repaired["taskBriefs"][0]["goal"], "Ship the change")
        self.assertEqual(repaired["taskBriefs"][0]["dependency"], [])
        self.assertTrue(repaired["taskBriefs"][0]["behaviorScope"])
        self.assertTrue(repaired["taskBriefs"][0]["acceptanceContract"])
        self.assertIn("missing_goal_repaired", repaired["qualityFlags"])
        self.assertIn("invalid_dependency_removed", repaired["qualityFlags"])
        self.assertGreaterEqual(repaired["repairCount"], 1)

    def test_planner_auto_dispatch_suggest_never_dispatches(self):
        decision = ChatRuntime._decide_planner_auto_dispatch(
            {
                "executionStrategy": "delegate",
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "Implement code",
                        "requiredCapabilities": ["software_engineering", "implement"],
                        "executionLaneHint": "subagent",
                    }
                ],
            },
            registry={
                "subagents": [
                    {
                        "id": "code-impl",
                        "name": "Code Implementer",
                        "capabilitySnapshot": {
                            "agentClass": "executor",
                            "domainTags": ["software_engineering"],
                            "operationCapabilities": ["implement"],
                            "plannerSuitability": "high",
                        },
                    }
                ],
                "externalWorkers": [],
            },
            planner_mode="force",
            planner_dispatch_mode="suggest",
        )

        self.assertFalse(decision["willDispatch"])
        self.assertEqual(decision["reason"], "suggest_only")

    def test_planner_auto_dispatch_allows_safe_matching_subagent(self):
        decision = ChatRuntime._decide_planner_auto_dispatch(
            {
                "executionStrategy": "delegate",
                "qualityFlags": [],
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "Implement code",
                        "requiredCapabilities": ["software_engineering", "implement"],
                        "writeSet": ["apps/v8-agent-os-engine"],
                        "behaviorScope": ["workspace_changes"],
                        "executionLaneHint": "subagent",
                    }
                ],
            },
            registry={
                "subagents": [
                    {
                        "id": "code-impl",
                        "name": "Code Implementer",
                        "capabilitySnapshot": {
                            "agentClass": "executor",
                            "domainTags": ["software_engineering"],
                            "operationCapabilities": ["implement", "workspace_changes"],
                            "artifactCapabilities": ["apps/v8-agent-os-engine"],
                            "plannerSuitability": "high",
                        },
                    }
                ],
                "externalWorkers": [],
            },
            planner_mode="force",
            planner_dispatch_mode="auto",
        )

        self.assertTrue(decision["willDispatch"])
        self.assertEqual(decision["reason"], "eligible")
        self.assertEqual(decision["selectedTargets"][0]["targetId"], "code-impl")

    def test_planner_auto_dispatch_blocks_parallel_write_conflict(self):
        decision = ChatRuntime._decide_planner_auto_dispatch(
            {
                "executionStrategy": "delegate",
                "taskBriefs": [
                    {"taskBriefId": "task-1", "goal": "A", "writeSet": ["same/file.py"], "executionLaneHint": "subagent"},
                    {"taskBriefId": "task-2", "goal": "B", "writeSet": ["same/file.py"], "executionLaneHint": "subagent"},
                ],
            },
            registry={"subagents": [], "externalWorkers": []},
            planner_mode="force",
            planner_dispatch_mode="auto",
        )

        self.assertFalse(decision["willDispatch"])
        self.assertEqual(decision["reason"], "write_set_conflict")

    def test_planner_auto_dispatch_blocks_disabled_external_worker(self):
        decision = ChatRuntime._decide_planner_auto_dispatch(
            {
                "executionStrategy": "delegate",
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "Use coding CLI",
                        "requiredCapabilities": ["software_engineering"],
                        "executionLaneHint": "external_worker",
                        "preferredWorkerType": "coding_cli",
                    }
                ],
            },
            registry={
                "subagents": [],
                "externalWorkers": [
                    {
                        "id": "coding-cli-worker",
                        "name": "Coding CLI Worker",
                        "enabled": False,
                        "workerType": "coding_cli",
                        "capabilitySnapshot": {
                            "agentClass": "external_worker",
                            "domainTags": ["software_engineering"],
                            "externalWorkerSuitability": "high",
                        },
                    }
                ],
            },
            planner_mode="force",
            planner_dispatch_mode="auto",
        )

        self.assertFalse(decision["willDispatch"])
        self.assertEqual(decision["reason"], "no_matching_target")

    def test_planner_auto_dispatch_node_enqueues_episode_without_tool_message(self):
        node = build_planner_auto_dispatch_node()
        command = node(
            {
                "planner_plan": {
                    "planId": "plan-1",
                    "capabilityPlan": [
                        {
                            "kind": "engineering",
                            "source": "planner",
                            "reason": "implementation_required",
                            "taskBriefId": "task-1",
                        }
                    ],
                    "taskBriefs": [{"taskBriefId": "task-1", "goal": "Do work"}],
                    "autoDispatchDecision": {
                        "mode": "auto",
                        "willDispatch": True,
                        "reason": "eligible",
                    },
                }
            }
        )

        self.assertEqual(getattr(command, "goto", None), "runtime_episode")
        self.assertNotIn("messages", command.update)
        self.assertNotIn("parallel_results", command.update)
        self.assertTrue(command.update["planner_dispatch_status"]["dispatched"])
        self.assertEqual(command.update["planner_dispatch_status"]["episodeCount"], 1)
        episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
        self.assertEqual(episode["kind"], "engineering")
        self.assertEqual(episode["state"], "queued")

    def test_runtime_episode_wait_node_merges_completed_handoff(self):
        node = build_runtime_episode_wait_node()
        episode_id = "episode_wait_node_test"
        episode = build_runtime_episode(
            need={"episodeId": episode_id, "kind": "research", "reason": "need evidence"},
            kind="research",
            state="queued",
            continuation_target="runtime_episode_runner",
        )
        db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
        handoff = build_handoff_ref(
            producer_episode_id=episode_id,
            kind="research",
            compact_summary="Research evidence bundle ready.",
            status="ready",
        )
        db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
        db.complete_runtime_episode(episode_id, state="completed", result_ref=handoff["handoffRefId"])

        command = asyncio.run(
            node(
                {
                    "current_route_context": {
                        "capabilityEpisodes": [episode],
                    }
                }
            )
        )

        self.assertEqual(getattr(command, "goto", None), "supervisor")
        refs = command.update["current_route_context"]["handoffRefs"]
        self.assertTrue(any(item.get("handoffRefId") == handoff["handoffRefId"] for item in refs))
        self.assertEqual(command.update["planner_dispatch_status"]["state"], "handoff_ready")

    def test_parallel_join_routes_pending_child_delegations_from_top_level(self):
        join_node = build_parallel_delegate_join_node()
        child_state = {
            "messages": [],
            "parallel_branch": {
                "invocationId": "delegation_child",
                "branchIndex": 0,
                "agentId": "child-agent",
                "agentName": "Child Agent",
                "reason": "Review one isolated file",
                "taskBriefId": "task-child",
                "delegationId": "subagent::child",
                "parentDelegationId": "subagent::parent",
                "delegationDepth": 2,
                "lane": "subagent",
            },
        }

        command = join_node(
            {
                "parallel_invocations": [{"invocationId": "delegation_parent", "expected": 1}],
                "parallel_results": [
                    {
                        "invocationId": "delegation_parent",
                        "status": "waiting_child_delegation",
                        "childDelegationRequestIds": ["child_req"],
                    }
                ],
                "pending_child_delegations": [
                    {
                        "requestId": "child_req",
                        "sourceInvocationId": "delegation_parent",
                        "sourceDelegationId": "subagent::parent",
                        "send": {"node": "parallel_delegate_task", "arg": child_state},
                    }
                ],
            }
        )

        self.assertEqual(command.goto, "supervisor")
        self.assertIn("child_req", command.update["routed_child_delegation_request_ids"])
        self.assertNotIn("parallel_invocations", command.update)
        self.assertNotIn("messages", command.update)
        route_context = command.update["current_route_context"]
        child_episode = route_context["capabilityEpisodes"][-1]
        self.assertEqual(child_episode["kind"], "delegation")
        self.assertEqual(child_episode["state"], "queued")
        self.assertEqual(child_episode["parentEpisodeId"], "subagent::parent")
        self.assertEqual(route_context["lastChildDelegationRouted"]["childEpisodeIds"], [child_episode["episodeId"]])

    def test_parallel_join_creates_handoff_ref_for_completed_subagent(self):
        join_node = build_parallel_delegate_join_node()

        command = join_node(
            {
                "parallel_invocations": [{"invocationId": "delegation_parent", "expected": 1}],
                "current_route_context": {
                    "activeCapabilityEpisodeId": "subagent::child",
                    "capabilityEpisodes": [
                        {
                            "episodeId": "subagent::child",
                            "needId": "subagent::child",
                            "kind": "delegation",
                            "state": "waiting",
                        }
                    ]
                },
                "parallel_results": [
                    {
                        "invocationId": "delegation_parent",
                        "delegationId": "subagent::child",
                        "status": "ok",
                        "taskBriefId": "task-child",
                        "agentId": "child-agent",
                        "compactTranscript": "Reviewed the isolated file and found no blocking issues.",
                    }
                ],
            }
        )

        route_context = command.update["current_route_context"]
        self.assertEqual(command.goto, "supervisor")
        self.assertEqual(route_context["handoffRefs"][0]["producerEpisodeId"], "subagent::child")
        self.assertIn("Reviewed the isolated file", route_context["handoffRefs"][0]["compactSummary"])
        self.assertEqual(route_context["capabilityEpisodes"][0]["state"], "completed")
        self.assertNotIn("activeCapabilityEpisodeId", route_context)
        self.assertEqual(route_context["lastDelegationHandoff"]["handoffRefs"], [route_context["handoffRefs"][0]["handoffRefId"]])


if __name__ == "__main__":
    unittest.main()
