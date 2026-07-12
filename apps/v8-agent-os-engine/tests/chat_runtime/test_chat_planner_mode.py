import asyncio
import unittest
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from api.models import ChatMessage, ChatRequest, ChatRequestData, EngineConfig
from core.database import db
from core.runtime_episodes import build_handoff_ref, build_runtime_episode
from graph.workflow_assembly import build_planner_auto_dispatch_node, build_runtime_episode_wait_node
from graph.parallel_support import build_parallel_delegate_join_node
from graph.supervisor_builder import _is_request_model_override
from graph.supervisor_turn import _filter_spec_tools_for_mode, _should_force_memory_broker_first, _spec_mode_stage_guidance
from runtimes.chat.planner_contract_verifier import verify_and_repair_planner_contract
from runtimes.chat.runtime import ChatRuntime, PlannerPlanPayload


class ChatPlannerModeTests(unittest.TestCase):
    def test_spec_revision_resume_starts_a_fresh_supervisor_turn(self):
        runtime = ChatRuntime()
        chat_run = SimpleNamespace(
            is_resume_request=True,
            request=ChatRequest(
                messages=[ChatMessage(role="user", content="[Spec Document Revision]")],
                data=ChatRequestData(specMode=True, specId="spec_revision"),
                resume_run_id="run_revision",
                resume_value={
                    "specRevision": {
                        "specId": "spec_revision",
                        "stage": "requirements",
                        "feedback": "删除内部路径。",
                    }
                },
            ),
        )
        expected = object()
        runtime.create_execution_bundle = AsyncMock(return_value=expected)
        runtime.create_resume_bundle = AsyncMock()

        resolved = asyncio.run(runtime.resolve_execution_bundle(chat_run=chat_run))

        self.assertIs(resolved, expected)
        runtime.create_execution_bundle.assert_awaited_once_with(chat_run=chat_run)
        runtime.create_resume_bundle.assert_not_awaited()

    def test_spec_revision_discipline_bundle_injects_one_required_tool_correction(self):
        runtime = ChatRuntime()
        runtime._recursion_limit = lambda: 50
        chat_run = SimpleNamespace(
            request=ChatRequest(
                messages=[ChatMessage(role="user", content="[Spec Document Revision]")],
                config=EngineConfig(provider="openai", model_name="gpt-test"),
                resume_value={
                    "specRevision": {
                        "specId": "spec_revision",
                        "stage": "requirements",
                        "feedback": "删除内部路径。",
                    }
                },
            ),
            lc_messages=[ChatMessage(role="user", content="revision")],
            session_id="session_revision",
            transport="system_resume",
            run_handle=object(),
            prepared=SimpleNamespace(
                planner_plan=None,
                engineering_context_pack=None,
                task_shape_hint=None,
                explicit_subagent_families=[],
                context_mentions=[],
                context_session_refs=[],
                session_coordination_message=None,
            ),
        )
        runner_bundle = SimpleNamespace(diagnostics={})
        with (
            patch(
                "runtimes.chat.runtime.supervisor_runner.get_state_snapshot",
                new=AsyncMock(return_value={"messages": [ChatMessage(role="assistant", content="I will revise.")]}),
            ),
            patch(
                "runtimes.chat.runtime.supervisor_runner.create_execution_bundle",
                new=AsyncMock(return_value=runner_bundle),
            ) as create_bundle,
        ):
            result = asyncio.run(
                runtime.create_spec_revision_discipline_bundle(
                    chat_run=chat_run,
                    previous_bundle=SimpleNamespace(runner_bundle=object()),
                )
            )

        self.assertIsNotNone(result)
        correction = create_bundle.await_args.kwargs["messages"][-1]
        self.assertIn("without creating a real pending Spec approval", correction.content)
        self.assertIn("mode='rewrite_stage'", correction.content)
        self.assertTrue(runner_bundle.diagnostics["specRevisionDiscipline"])

    def test_pending_spec_approval_truth_ignores_rejected_rows(self):
        runtime = ChatRuntime()
        chat_run = SimpleNamespace(active_run_id="run_spec")
        with patch(
            "runtimes.chat.runtime.db.list_pending_approvals",
            return_value=[{"approval_kind": "spec_stage_approval", "status": "pending"}],
        ):
            self.assertTrue(runtime._has_pending_spec_stage_approval(chat_run))
        with patch("runtimes.chat.runtime.db.list_pending_approvals", return_value=[]):
            self.assertFalse(runtime._has_pending_spec_stage_approval(chat_run))

    def test_model_profile_data_resolves_per_run_engine_config(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="写一段说明")],
            data=ChatRequestData(modelProfile="xiaomi-mimo-tokenplan::mimo-v2.5-pro"),
        )
        override = EngineConfig(
            provider="xiaomi-mimo-tokenplan",
            model_name="mimo-v2.5-pro",
            api_key="test-key",
            base_url="https://example.test/v1",
        )

        with patch(
            "runtimes.chat.runtime.resolve_engine_config_for_model_ref",
            return_value={
                "engine_config": override,
                "resolution": {
                    "bindingState": "request_override",
                    "resolvedModelId": "mimo-v2.5-pro",
                    "resolvedProviderId": "xiaomi-mimo-tokenplan",
                },
            },
        ) as resolver:
            runtime._resolve_engine_config(request)

        resolver.assert_called_once_with("xiaomi-mimo-tokenplan::mimo-v2.5-pro")
        self.assertEqual(request.config.provider, "xiaomi-mimo-tokenplan")
        self.assertEqual(request.config.model_name, "mimo-v2.5-pro")
        self.assertEqual(request.config.api_key, "test-key")
        self.assertEqual(request.config.base_url, "https://example.test/v1")

    def test_planner_chat_model_uses_planner_role_when_bound(self):
        sentinel = object()
        with (
            patch("core.models.control_plane.model_control_plane.get_config", return_value={"roles": {"planner": "deepseek::deepseek-v4-flash"}}),
            patch("runtimes.chat.runtime.llm_factory.create_for_role", return_value=sentinel) as create_for_role,
        ):
            created = ChatRuntime._create_planner_chat_model(temperature=0, _request_kind="planner")

        self.assertIs(created, sentinel)
        create_for_role.assert_called_once()
        self.assertEqual(create_for_role.call_args.args[0], "planner")
        self.assertEqual(create_for_role.call_args.kwargs["_request_kind"], "planner")

    def test_planner_chat_model_falls_back_to_supervisor_when_unbound(self):
        sentinel = object()
        with (
            patch("core.models.control_plane.model_control_plane.get_config", return_value={"roles": {"supervisor": "openai::gpt-5.5", "planner": ""}}),
            patch("runtimes.chat.runtime.llm_factory.create_for_role", return_value=sentinel) as create_for_role,
        ):
            created = ChatRuntime._create_planner_chat_model(temperature=0, _request_kind="planner")

        self.assertIs(created, sentinel)
        create_for_role.assert_called_once()
        self.assertEqual(create_for_role.call_args.args[0], "supervisor")
        self.assertEqual(create_for_role.call_args.kwargs["_request_kind"], "planner")

    def test_planner_prompt_maps_skill_swarm_to_research_or_top_level_delegation(self):
        prompt = ChatRuntime()._planner_system_prompt()

        self.assertIn("parallel agents, subagents, Agent Swarm", prompt)
        self.assertIn("Research Runtime task", prompt)
        self.assertIn("Do not assume a subagent can spawn child agents", prompt)
        self.assertIn("huashu-nuwa", prompt)

    def test_spec_mode_unapproved_spec_blocks_planner_auto_dispatch(self):
        plan = {
            "planId": "plan-spec-blocked",
            "executionStrategy": "delegate",
            "capabilityPlan": [{"kind": "engineering", "taskBriefId": "task-1", "reason": "implement"}],
            "taskBriefs": [{"taskBriefId": "task-1", "goal": "Implement approved spec."}],
        }

        repaired = verify_and_repair_planner_contract(
            plan,
            fallback_plan=plan,
            task_shape_hint={
                "specMode": True,
                "specId": "spec_blocked",
                "specBrief": {
                    "specId": "spec_blocked",
                    "pipelineControl": {
                        "runtimeExecutionAllowed": False,
                        "blockedReason": "approval_required",
                    },
                },
            },
        )
        decision = ChatRuntime._decide_planner_auto_dispatch(
            repaired,
            registry={"subagents": [], "externalWorkers": []},
            planner_mode="force",
            planner_dispatch_mode="auto",
        )

        self.assertIn("spec_runtime_not_approved", repaired["qualityFlags"])
        self.assertFalse(decision["willDispatch"])
        self.assertEqual(decision["reason"], "planner_quality_flags_block_dispatch")

    def test_spec_mode_approved_spec_attaches_compact_spec_brief_to_tasks(self):
        plan = {
            "planId": "plan-spec-approved",
            "executionStrategy": "delegate",
            "capabilityPlan": [{"kind": "engineering", "taskBriefId": "task-1", "reason": "implement"}],
            "taskBriefs": [{"taskBriefId": "task-1", "goal": "Implement approved spec."}],
        }

        repaired = verify_and_repair_planner_contract(
            plan,
            fallback_plan=plan,
            task_shape_hint={
                "specMode": True,
                "specId": "spec_ready",
                "specBrief": {
                    "specId": "spec_ready",
                    "featureName": "Spec Ready",
                    "approvedStages": ["requirements", "design", "tasks"],
                    "pipelineControl": {"runtimeExecutionAllowed": True},
                    "documents": {
                        "requirements": {"detailRef": "spec://spec_ready/requirements", "ids": ["REQ-001"], "version": 1, "status": "approved"},
                        "design": {"detailRef": "spec://spec_ready/design", "ids": ["DES-001"], "version": 1, "status": "approved"},
                        "tasks": {"detailRef": "spec://spec_ready/tasks", "ids": ["TASK-001"], "version": 1, "status": "approved"},
                    },
                    "linkedSections": [
                        {"stage": "tasks", "detailRef": "spec://spec_ready/tasks", "ids": ["TASK-001"]},
                    ],
                },
            },
        )

        task_context = repaired["taskBriefs"][0]["context"]
        self.assertIn("spec_brief_context_attached", repaired["qualityFlags"])
        self.assertEqual(task_context["specBrief"]["specId"], "spec_ready")
        self.assertIn("tasks", task_context["specBrief"]["documents"])

    def test_spec_mode_current_spec_brief_replaces_stale_task_context(self):
        plan = {
            "planId": "plan-spec-current",
            "executionStrategy": "delegate",
            "capabilityPlan": [{"kind": "engineering", "taskBriefId": "task-1", "reason": "implement"}],
            "taskBriefs": [
                {
                    "taskBriefId": "task-1",
                    "goal": "Implement current approved spec.",
                    "context": {"specBrief": {"specId": "spec_old", "documents": {"tasks": {"ids": ["TASK-OLD"]}}}},
                }
            ],
        }

        repaired = verify_and_repair_planner_contract(
            plan,
            fallback_plan=plan,
            task_shape_hint={
                "specMode": True,
                "specId": "spec_current",
                "specBrief": {
                    "specId": "spec_current",
                    "featureName": "Current Spec",
                    "approvedStages": ["requirements", "design", "tasks"],
                    "pipelineControl": {"runtimeExecutionAllowed": True},
                    "documents": {
                        "requirements": {"detailRef": "spec://spec_current/requirements", "ids": ["REQ-001"], "version": 1, "status": "approved"},
                        "design": {"detailRef": "spec://spec_current/design", "ids": ["DES-001"], "version": 1, "status": "approved"},
                        "tasks": {"detailRef": "spec://spec_current/tasks", "ids": ["TASK-001"], "version": 1, "status": "approved"},
                    },
                },
            },
        )

        task_context = repaired["taskBriefs"][0]["context"]
        self.assertEqual(task_context["specBrief"]["specId"], "spec_current")
        self.assertEqual(task_context["specBrief"]["documents"]["tasks"]["ids"], ["TASK-001"])
        self.assertNotIn("spec_old", str(task_context))

    def test_supervisor_request_model_override_is_explicit_and_non_default(self):
        self.assertTrue(
            _is_request_model_override(
                EngineConfig(provider="xiaomi-mimo-tokenplan", model_name="mimo-v2.5-pro"),
                "doubao-seed-2.0-pro",
            )
        )
        self.assertFalse(_is_request_model_override(EngineConfig(), "doubao-seed-2.0-pro"))
        self.assertFalse(
            _is_request_model_override(
                EngineConfig(provider="volcengine-coding", model_name="doubao-seed-2.0-pro"),
                "doubao-seed-2.0-pro",
            )
        )

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

    def test_planner_first_turn_timeout_defers_without_fallback_plan(self):
        class SlowPlannerModel:
            def with_structured_output(self, _schema):
                return self

            async def ainvoke(self, _messages):
                await asyncio.sleep(0.5)
                return {}

        emitted: list[tuple[str, dict]] = []
        runtime = ChatRuntime()
        chat_run = SimpleNamespace(
            active_run_id="run-planner-defer",
            prepared=SimpleNamespace(
                task_planning_mode=True,
                planner_mode="force",
                is_resume_request=False,
                planner_plan=None,
                planner_deferred=False,
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
            patch("runtimes.chat.runtime.llm_factory.create_for_role", return_value=SlowPlannerModel()),
            patch("runtimes.chat.runtime.workflow_ledger_service.activate_runtime_step") as activate_runtime_step,
            patch.object(runtime, "_planner_registry_snapshot", return_value={"subagents": [], "externalWorkers": []}),
        ):
            plan = asyncio.run(runtime.ensure_planner_plan(
                chat_run=chat_run,
                timeout_seconds=0.1,
                defer_on_timeout=True,
            ))

        topics = [topic for topic, _payload in emitted]
        self.assertIsNone(plan)
        self.assertTrue(chat_run.prepared.planner_deferred)
        self.assertIsNone(chat_run.prepared.planner_plan)
        self.assertIn("planner.deferred", topics)
        self.assertNotIn("planner.fallback.used", topics)
        self.assertNotIn("planner.plan.created", topics)
        activate_runtime_step.assert_not_called()
        deferred_payload = next(payload for topic, payload in emitted if topic == "planner.deferred")
        self.assertEqual(deferred_payload.get("messageSurfacePriority"), "diagnostic")
        self.assertFalse(deferred_payload.get("fallbackContinues"))

    def test_explicit_runtime_episode_defer_continues_with_fallback_plan(self):
        class SlowPlannerModel:
            def with_structured_output(self, _schema):
                return self

            async def ainvoke(self, _messages):
                await asyncio.sleep(0.5)
                return {}

        emitted: list[tuple[str, dict]] = []
        runtime = ChatRuntime()
        chat_run = SimpleNamespace(
            active_run_id="run-planner-defer-runtime-fallback",
            prepared=SimpleNamespace(
                task_planning_mode=True,
                planner_mode="force",
                is_resume_request=False,
                planner_plan=None,
                planner_deferred=False,
                planner_intent_diagnostics={"reason": "test"},
                latest_user_content=(
                    "必须创建 Engineering episode 和 Delegation/Subagent episode，"
                    "deliverableKind=plan_only，writeRequired=false，并等待 typed handoff。"
                ),
                skill_references=[],
                engineering_trigger_decision={},
                engineering_context_pack=None,
                planner_dispatch_mode="auto",
                task_shape_hint={"primaryTaskShape": "writing", "secondaryTaskShapes": ["delegation"]},
                live_audit_context={"runtimeSubagentClosureLiveAudit": True},
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
            patch("runtimes.chat.runtime.llm_factory.create_for_role", return_value=SlowPlannerModel()),
            patch("runtimes.chat.runtime.workflow_ledger_service.activate_runtime_step", lambda *_, **__: None),
            patch("runtimes.chat.runtime.engineering_lane_service.enrich_planner_plan_with_engineering_contract", lambda plan, **_: plan),
            patch.object(runtime, "_planner_registry_snapshot", return_value={"subagents": [], "externalWorkers": []}),
        ):
            plan = asyncio.run(
                runtime.ensure_planner_plan(
                    chat_run=chat_run,
                    timeout_seconds=0.1,
                    defer_on_timeout=True,
                )
            )

        topics = [topic for topic, _payload in emitted]
        self.assertIsInstance(plan, dict)
        self.assertTrue(chat_run.prepared.planner_deferred)
        self.assertTrue((chat_run.prepared.planner_plan or {}).get("planId"))
        self.assertIn("planner.deferred", topics)
        self.assertIn("planner.fallback.used", topics)
        self.assertIn("planner.plan.created", topics)
        kinds = {str(item.get("kind") or "") for item in plan.get("capabilityPlan") or []}
        self.assertIn("engineering", kinds)
        self.assertIn("delegation", kinds)
        engineering_item = next(item for item in plan.get("capabilityPlan") or [] if item.get("kind") == "engineering")
        self.assertEqual(engineering_item.get("deliverableKind"), "plan_only")
        self.assertFalse(engineering_item.get("writeRequired"))

    def test_approved_spec_runtime_execution_uses_deterministic_route_without_planner_model(self):
        emitted: list[tuple[str, dict]] = []
        runtime = ChatRuntime()
        spec_brief = {
            "specId": "spec-approved",
            "featureName": "Approved Counter",
            "workspacePath": r"E:\Projects\test3",
            "approvedStages": ["requirements", "design", "tasks"],
            "pipelineControl": {
                "currentStage": "tasks",
                "nextStage": "runtime_execution",
                "runtimeExecutionAllowed": True,
                "approvedStages": ["requirements", "design", "tasks"],
            },
            "documents": {
                "requirements": {"detailRef": "spec://spec-approved/requirements", "ids": ["REQ-001"], "relativePath": ".v8/specs/counter/requirements.md", "version": 1, "status": "approved"},
                "design": {"detailRef": "spec://spec-approved/design", "ids": ["DES-001"], "relativePath": ".v8/specs/counter/design.md", "version": 1, "status": "approved"},
                "tasks": {"detailRef": "spec://spec-approved/tasks", "ids": ["TASK-001"], "relativePath": ".v8/specs/counter/tasks.md", "version": 1, "status": "approved"},
            },
            "linkedSections": [{"stage": "tasks", "detailRef": "spec://spec-approved/tasks", "ids": ["TASK-001"]}],
        }
        chat_run = SimpleNamespace(
            active_run_id="run-approved-spec-route",
            prepared=SimpleNamespace(
                task_planning_mode=True,
                planner_mode="force",
                is_resume_request=False,
                planner_plan=None,
                planner_deferred=False,
                planner_intent_diagnostics={"reason": "approved_spec_runtime_execution"},
                latest_user_content="[Spec Approval Continuation] nextStage: runtime_execution",
                skill_references=[],
                engineering_trigger_decision={},
                engineering_context_pack=None,
                planner_dispatch_mode="auto",
                task_shape_hint={"specMode": True, "specId": "spec-approved", "specBrief": spec_brief},
                live_audit_context={},
                spec_mode=True,
                spec_id="spec-approved",
                spec_brief=spec_brief,
            ),
            scope_result=SimpleNamespace(
                binding=SimpleNamespace(
                    project_id="project-test",
                    workspace_id="workspace-test",
                    workspace_path=r"E:\Projects\test3",
                    resolved_scope="project",
                )
            ),
            emit_runtime_event=lambda topic, payload, **_: emitted.append((topic, payload)),
        )

        with (
            patch("runtimes.chat.runtime.llm_factory.create_for_role", side_effect=AssertionError("planner model must not be called")),
            patch("runtimes.chat.runtime.workflow_ledger_service.activate_runtime_step", lambda *_, **__: None),
            patch("runtimes.chat.runtime.engineering_lane_service.enrich_planner_plan_with_engineering_contract", lambda plan, **_: plan),
            patch.object(runtime, "_planner_registry_snapshot", return_value={"subagents": [], "externalWorkers": []}),
        ):
            plan = asyncio.run(runtime.ensure_planner_plan(chat_run=chat_run))

        self.assertIsInstance(plan, dict)
        self.assertTrue((chat_run.prepared.planner_plan or {}).get("planId"))
        self.assertIn("approved_spec_execution_deterministic_route", plan.get("qualityFlags") or [])
        self.assertIn("engineering", {str(item.get("kind") or "") for item in plan.get("capabilityPlan") or []})
        self.assertTrue((plan.get("autoDispatchDecision") or {}).get("willDispatch"))
        task = plan["taskBriefs"][0]
        self.assertEqual(task["context"]["specBrief"]["specId"], "spec-approved")
        self.assertEqual(task["context"]["approvedSpecDigest"]["summary"], "Approved Spec concrete delivery digest generated from requirements/design/tasks.")
        engineering_capsule = task.get("engineeringTaskCapsule") if isinstance(task.get("engineeringTaskCapsule"), dict) else {}
        self.assertEqual(engineering_capsule.get("specId"), "spec-approved")

    def test_approved_spec_execution_plan_carries_traceability_slices_to_task_context(self):
        runtime = ChatRuntime()
        spec_brief = {
            "specId": "spec-traceable",
            "featureName": "Traceable PDF Converter",
            "workspacePath": r"E:\Projects\test3",
            "approvedStages": ["requirements", "design", "tasks"],
            "pipelineControl": {
                "currentStage": "tasks",
                "nextStage": "runtime_execution",
                "runtimeExecutionAllowed": True,
            },
            "documents": {
                "requirements": {"detailRef": "spec://spec-traceable/requirements", "ids": ["6.1", "6.2"], "relativePath": ".v8/specs/pdf/requirements.md", "status": "approved"},
                "design": {"detailRef": "spec://spec-traceable/design", "ids": ["DES-SECTION-01"], "relativePath": ".v8/specs/pdf/design.md", "status": "approved"},
                "tasks": {"detailRef": "spec://spec-traceable/tasks", "ids": ["TASK-2.1"], "relativePath": ".v8/specs/pdf/tasks.md", "status": "approved"},
            },
            "linkedSections": [
                {"stage": "requirements", "detailRef": "spec://spec-traceable/requirements", "ids": ["6.1", "6.2"]},
                {"stage": "design", "detailRef": "spec://spec-traceable/design", "ids": ["DES-SECTION-01"]},
                {"stage": "tasks", "detailRef": "spec://spec-traceable/tasks", "ids": ["TASK-2.1"]},
            ],
            "traceability": {
                "frameworkDigest": "全员必须使用uni-app/Vue/JavaScript微信小程序工程，不得改成Python脚本。",
                "tasks": [
                    {
                        "taskId": "TASK-2.1",
                        "title": "创建环境变量模板文件",
                        "requirementRefs": ["6.1", "6.5"],
                        "requirementSnippets": [
                            {"id": "6.1", "summary": "系统 SHALL 提供.env.template模板文件", "detailRef": "spec://requirements#6.1"}
                        ],
                        "designRefs": ["DES-SECTION-01"],
                        "designSnippets": [
                            {"id": "DES-SECTION-01", "title": "配置管理设计", "summary": "使用utils/config.js读取.env文件并做启动期校验。", "detailRef": "spec://design#配置管理设计"}
                        ],
                        "taskExcerpt": "- [x] 2.1 创建环境变量模板文件\n  - _需求: 6.1, 6.5_",
                        "detailRef": "spec://tasks#TASK-2.1",
                    }
                ],
                "distributionChecks": {"taskCount": 1, "hasFrameworkDigest": True, "allTasksHaveDesignRefs": True},
            },
        }
        chat_run = SimpleNamespace(
            prepared=SimpleNamespace(
                latest_user_content="[Spec Approval Continuation] nextStage: runtime_execution",
                spec_brief=spec_brief,
                spec_id="spec-traceable",
            ),
            scope_result=SimpleNamespace(
                binding=SimpleNamespace(
                    project_id="project-test",
                    workspace_id="workspace-test",
                    workspace_path=r"E:\Projects\test3",
                    resolved_scope="project",
                )
            ),
        )

        plan = runtime._fallback_spec_execution_planner_plan(chat_run=chat_run, reason="approved_spec_runtime_execution_allowed")

        task = plan["taskBriefs"][0]
        context = task["context"]
        digest = context["approvedSpecDigest"]
        self.assertIn("uni-app/Vue/JavaScript", context["frameworkDigest"])
        self.assertIn("TASK-2.1", context["specExecutionSummary"])
        self.assertIn("6.1: 系统 SHALL 提供.env.template", context["specExecutionSummary"])
        self.assertIn("使用utils/config.js", context["specExecutionSummary"])
        self.assertEqual(context["taskDetailRefs"], ["spec://tasks#TASK-2.1"])
        self.assertEqual(digest["taskSlices"][0]["requirementRefs"], ["6.1", "6.5"])
        self.assertIn("frameworkDigest", task["engineeringTaskCapsule"])

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

    def test_planner_model_prompt_uses_compact_registry_context(self):
        class CapturingPlanner:
            messages = []

            def with_structured_output(self, _schema):
                return self

            async def ainvoke(self, messages):
                CapturingPlanner.messages = list(messages)
                return PlannerPlanPayload(
                    planId="plan_compact",
                    executionStrategy="delegate",
                    planSummary="Compact plan",
                    capabilityPlan=[{"kind": "engineering", "reason": "implementation"}],
                    taskBriefs=[{"taskBriefId": "task-1", "goal": "Implement with proof"}],
                )

        long_description = "long-description " * 80
        registry = {
            "subagents": [
                {
                    "id": f"agent-{index}",
                    "name": f"Agent {index}",
                    "description": long_description,
                    "capabilitySnapshot": {
                        "huge": "capability-detail " * 80,
                        "tools": ["write_native_file", "run_system_command"],
                    },
                }
                for index in range(24)
            ],
            "externalWorkers": [],
        }
        runtime = ChatRuntime()
        chat_run = SimpleNamespace(
            active_run_id="run-planner-compact",
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
            emit_runtime_event=lambda *_args, **_kwargs: None,
        )

        with (
            patch("runtimes.chat.runtime.llm_factory.create_for_role", return_value=CapturingPlanner()),
            patch("runtimes.chat.runtime.workflow_ledger_service.activate_runtime_step", lambda *_, **__: None),
            patch("runtimes.chat.runtime.engineering_lane_service.enrich_planner_plan_with_engineering_contract", lambda plan, **_: plan),
            patch.object(runtime, "_planner_registry_snapshot", return_value=registry),
        ):
            plan = asyncio.run(runtime.ensure_planner_plan(chat_run=chat_run))

        prompt = "\n".join(str(getattr(message, "content", message) or "") for message in CapturingPlanner.messages)
        self.assertEqual(plan.get("planId"), "plan_compact")
        self.assertIn("more local subagents omitted", prompt)
        self.assertNotIn("capabilitySnapshot", prompt)
        self.assertLess(prompt.count("long-description"), 160)

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

    def test_planner_payload_coerces_plan_level_object_fields(self):
        payload = PlannerPlanPayload.model_validate(
            {
                "executionStrategy": "mixed",
                "globalAcceptanceContract": {
                    "proof": ["handoff returned"],
                    "blocker": "report exact failed episode",
                },
                "riskFlags": "planner_contract_shape_drift",
                "qualityFlags": None,
                "autoDispatchDecision": "auto",
            }
        )

        self.assertIn("handoff returned", payload.globalAcceptanceContract)
        self.assertEqual(payload.riskFlags, ["planner_contract_shape_drift"])
        self.assertEqual(payload.qualityFlags, [])
        self.assertEqual(payload.autoDispatchDecision, {"raw": "auto"})

    def test_planner_primary_path_uses_compact_json_contract(self):
        class JsonPlanner:
            messages = []

            def with_structured_output(self, _schema):
                raise AssertionError("Planner primary path should not use native structured output by default.")

            async def ainvoke(self, messages):
                JsonPlanner.messages = list(messages)
                return SimpleNamespace(
                    content='{"planId":"plan-json","executionStrategy":"mixed","planSummary":"Research then implement.","capabilityPlan":[{"kind":"research","reason":"collect evidence","taskBriefId":"task-1"},{"kind":"engineering","reason":"implement","taskBriefId":"task-2"}],"taskBriefs":[{"taskBriefId":"task-1","goal":"Collect source-backed evidence.","acceptanceContract":"Return evidence refs."},{"taskBriefId":"task-2","goal":"Implement with proof.","dependency":["task-1"],"acceptanceContract":"Return proof."}],"globalAcceptanceContract":"Return evidence and proof."}'
                )

        emitted: list[tuple[str, dict]] = []
        runtime = ChatRuntime()
        chat_run = SimpleNamespace(
            active_run_id="run-planner-json-primary",
            prepared=SimpleNamespace(
                task_planning_mode=True,
                planner_mode="force",
                is_resume_request=False,
                planner_plan=None,
                planner_intent_diagnostics={"reason": "test"},
                latest_user_content="请先调研再实现",
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
            patch("runtimes.chat.runtime.llm_factory.create_for_role", return_value=JsonPlanner()),
            patch("runtimes.chat.runtime.workflow_ledger_service.activate_runtime_step", lambda *_, **__: None),
            patch("runtimes.chat.runtime.engineering_lane_service.enrich_planner_plan_with_engineering_contract", lambda plan, **_: plan),
            patch.object(runtime, "_planner_registry_snapshot", return_value={"subagents": [], "externalWorkers": []}),
        ):
            plan = asyncio.run(runtime.ensure_planner_plan(chat_run=chat_run))

        topics = [topic for topic, _payload in emitted]
        prompt = "\n".join(str(getattr(message, "content", message) or "") for message in JsonPlanner.messages)
        self.assertEqual(plan.get("planId"), "plan-json")
        self.assertIn("Return ONLY one compact JSON object", prompt)
        self.assertIn("research", [item.get("kind") for item in plan.get("capabilityPlan") or []])
        self.assertNotIn("planner.fallback.used", topics)

    def test_planner_task_brief_coerces_object_acceptance_contract(self):
        payload = PlannerPlanPayload.model_validate(
            {
                "executionStrategy": "delegate",
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "Implement the runtime handoff",
                        "engineeringTaskCapsule": None,
                        "writeSet": "apps/v8-agent-os-engine",
                        "acceptanceContract": {
                            "requiredProof": ["episode completed", "handoff returned"],
                            "risk": "do not let Supervisor bypass runtime",
                        },
                    }
                ],
            }
        )

        brief = payload.taskBriefs[0]
        self.assertEqual(brief.engineeringTaskCapsule, {})
        self.assertEqual(brief.writeSet, ["apps/v8-agent-os-engine"])
        self.assertIn("requiredProof", brief.acceptanceContract)
        self.assertIn("handoff returned", brief.acceptanceContract)

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

    def test_auto_planner_mode_is_advisory_without_llm_planning(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="Break down this refactor into tasks with tests.")],
            data=ChatRequestData(plannerMode="auto"),
        )

        _, task_planning_mode, planner_mode, planner_dispatch_mode, diagnostics, *_ = runtime._resolve_request_context(request)

        self.assertEqual(planner_mode, "auto")
        self.assertEqual(planner_dispatch_mode, "suggest")
        self.assertFalse(task_planning_mode)
        self.assertIn("explicit_planning", diagnostics.get("signals") or [])
        self.assertTrue(diagnostics.get("advisoryOnly"))

    def test_explicit_engineering_request_does_not_force_planner_by_itself(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="请使用 Engineering Runtime 开发这个项目。")],
        )

        _, task_planning_mode, planner_mode, planner_dispatch_mode, diagnostics, engineering_mode, explicit_engineering_requested, *_ = runtime._resolve_request_context(request)

        self.assertFalse(task_planning_mode)
        self.assertEqual(planner_mode, "off")
        self.assertEqual(planner_dispatch_mode, "suggest")
        self.assertEqual(engineering_mode, "force")
        self.assertTrue(explicit_engineering_requested)
        self.assertIn("explicit_engineering_runtime", diagnostics.get("signals") or [])

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

    def test_spec_mode_does_not_force_planner_before_approved_spec(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="用 Spec 模式规划这个功能。")],
            data=ChatRequestData(specMode=True, plannerMode="off"),
        )

        _, task_planning_mode, planner_mode, planner_dispatch_mode, diagnostics, *rest = runtime._resolve_request_context(request)

        self.assertFalse(task_planning_mode)
        self.assertEqual(planner_mode, "off")
        self.assertEqual(planner_dispatch_mode, "suggest")
        self.assertIn("spec_mode", diagnostics.get("signals") or [])
        self.assertTrue(rest[-1])

    def test_explicit_natural_language_activates_spec_mode(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="开启 Spec Mode，为当前工作区编写需求文档。")],
        )

        resolved = runtime._resolve_request_context(request)

        self.assertTrue(resolved[-1])
        self.assertIn("spec_mode", resolved[4].get("signals") or [])

    def test_natural_language_spec_mode_negation_does_not_activate(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="不要开启 Spec Mode，直接解释现有代码即可。")],
        )

        resolved = runtime._resolve_request_context(request)

        self.assertFalse(resolved[-1])

    def test_recent_spec_mode_is_inherited_only_for_bounded_continuation(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            session_id="session-spec-trust-continuation",
            messages=[ChatMessage(role="user", content="工作区已信任，请继续刚才的 requirements 阶段。")],
        )
        canonical_rows = [
            {
                "role": "user",
                "metadata": {"specMode": True},
                "content_text": "开启 Spec Mode，为当前工作区编写需求文档。",
            }
        ]

        with (
            patch("runtimes.chat.runtime.db.get_chat_canonical_messages", return_value=canonical_rows),
            patch.object(ChatRuntime, "_latest_session_spec_id", return_value=""),
        ):
            prepared = runtime.prepare_request(request)

        self.assertTrue(prepared.spec_mode)
        self.assertIn("spec_mode_continuation", prepared.planner_intent_diagnostics.get("signals") or [])

    def test_recent_spec_mode_is_not_inherited_for_unrelated_new_task(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            session_id="session-spec-unrelated-task",
            messages=[ChatMessage(role="user", content="解释一下这个函数的返回值。")],
        )
        canonical_rows = [
            {
                "role": "user",
                "metadata": {"specMode": True},
                "content_text": "开启 Spec Mode，为当前工作区编写需求文档。",
            }
        ]

        with patch("runtimes.chat.runtime.db.get_chat_canonical_messages", return_value=canonical_rows):
            prepared = runtime.prepare_request(request)

        self.assertFalse(prepared.spec_mode)

    def test_governance_resume_preserves_spec_mode_and_current_spec(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            session_id="session-spec-resume",
            resume_run_id="run-spec-resume",
            messages=[ChatMessage(role="assistant", content="等待用户确认")],
            resume_value={"answer": "是，直接起草"},
        )
        canonical_rows = [
            {
                "role": "user",
                "run_id": "run-spec-resume",
                "metadata": {"specMode": True},
                "content_text": "开启 Spec Mode。",
            }
        ]

        with (
            patch(
                "runtimes.chat.runtime.db.get_run_record",
                return_value={"id": "run-spec-resume", "session_id": "session-spec-resume"},
            ),
            patch("runtimes.chat.runtime.db.get_chat_canonical_messages", return_value=canonical_rows),
            patch.object(ChatRuntime, "_latest_session_spec_id", return_value="spec-resume-current") as latest_spec,
        ):
            prepared = runtime.prepare_request(request)

        self.assertTrue(prepared.spec_mode)
        self.assertEqual(prepared.spec_id, "spec-resume-current")
        self.assertIn("spec_mode_resume", prepared.planner_intent_diagnostics.get("signals") or [])
        latest_spec.assert_called_once_with("session-spec-resume")

    def test_spec_broker_hidden_until_spec_mode(self):
        tools = [
            SimpleNamespace(name="ask_user"),
            SimpleNamespace(name="fetch_skill_instructions"),
            SimpleNamespace(name="spec_broker"),
            SimpleNamespace(name="tool_observation_detail"),
            SimpleNamespace(name="memory_broker"),
            SimpleNamespace(name="runtime_broker"),
            SimpleNamespace(name="delegation_broker"),
            SimpleNamespace(name="research_broker"),
            SimpleNamespace(name="web_broker"),
            SimpleNamespace(name="write_todos"),
            SimpleNamespace(name="update_todo"),
            SimpleNamespace(name="workspace_broker"),
            SimpleNamespace(name="run_system_command"),
            SimpleNamespace(name="write_native_file"),
        ]

        hidden = _filter_spec_tools_for_mode(tools, {"current_route_context": {"specMode": False}})
        initial_visible = _filter_spec_tools_for_mode(tools, {"current_route_context": {"specMode": True}})
        staged_visible = _filter_spec_tools_for_mode(tools, {"current_route_context": {"specMode": True, "specId": "spec-1"}})
        execution_visible = _filter_spec_tools_for_mode(
            tools,
            {
                "current_route_context": {
                    "specMode": True,
                    "specId": "spec-1",
                    "specBrief": {"pipelineControl": {"runtimeExecutionAllowed": True}},
                }
            },
        )

        self.assertEqual(
            [item.name for item in hidden],
            ["ask_user", "fetch_skill_instructions", "tool_observation_detail", "memory_broker", "runtime_broker", "delegation_broker", "research_broker", "web_broker", "write_todos", "update_todo", "workspace_broker", "run_system_command", "write_native_file"],
        )
        self.assertEqual(
            [item.name for item in initial_visible],
            ["ask_user", "fetch_skill_instructions", "spec_broker", "tool_observation_detail", "memory_broker", "research_broker", "web_broker"],
        )
        self.assertEqual(
            [item.name for item in staged_visible],
            ["ask_user", "fetch_skill_instructions", "spec_broker", "tool_observation_detail", "memory_broker", "research_broker", "web_broker"],
        )
        self.assertEqual(
            [item.name for item in execution_visible],
            ["ask_user", "spec_broker", "tool_observation_detail", "runtime_broker"],
        )

    def test_approved_spec_runtime_stage_guides_runtime_broker_first(self):
        tools = [
            SimpleNamespace(name="memory_broker"),
            SimpleNamespace(name="runtime_broker"),
            SimpleNamespace(name="write_todos"),
            SimpleNamespace(name="write_native_file"),
            SimpleNamespace(name="research_broker"),
            SimpleNamespace(name="fetch_skill_instructions"),
        ]
        state = {
            "current_route_context": {
                "specMode": True,
                "specId": "spec-ready",
                "specBrief": {"pipelineControl": {"runtimeExecutionAllowed": True}},
            }
        }

        guidance = _spec_mode_stage_guidance(
            state=state,
            user_query="[Spec Approval Continuation] nextStage: runtime_execution",
            selected_tools=tools,
        )

        self.assertIsNotNone(guidance)
        self.assertIn("Spec Runtime Execution Gate", guidance.content)
        self.assertIn("runtime_broker(mode='route'", guidance.content)
        self.assertIn("Do not call memory_broker", guidance.content)
        self.assertIn("fetch_skill_instructions", guidance.content)
        self.assertIn("write_native_file directly", guidance.content)
        self.assertIn("Supervisor todo tools are hidden in Spec Mode", guidance.content)
        self.assertIn("approved Spec tasks as the execution contract", guidance.content)

    def test_spec_continuation_runtime_stage_overrides_missing_spec_id_gate(self):
        tools = [
            SimpleNamespace(name="spec_broker"),
            SimpleNamespace(name="memory_broker"),
            SimpleNamespace(name="runtime_broker"),
            SimpleNamespace(name="write_native_file"),
        ]
        state = {
            "current_route_context": {
                "specMode": True,
                "specContinuation": {
                    "specId": "spec-ready",
                    "nextStage": "runtime_execution",
                    "runtimeExecutionAllowed": True,
                },
            }
        }

        visible = _filter_spec_tools_for_mode(tools, state)
        guidance = _spec_mode_stage_guidance(
            state=state,
            user_query="[SPEC CONTINUATION] activeSpecId: spec-ready nextStage: runtime_execution",
            selected_tools=visible,
        )

        self.assertEqual([item.name for item in visible], ["spec_broker", "runtime_broker"])
        self.assertIsNotNone(guidance)
        self.assertIn("Spec Runtime Execution Gate", guidance.content)
        self.assertIn("specId=spec-ready", guidance.content)
        self.assertIn("runtime_broker(mode='route'", guidance.content)
        self.assertNotIn("no specId exists yet", guidance.content)

    def test_spec_mode_recovers_current_session_spec_id_from_prior_events(self):
        runtime = ChatRuntime()
        with (
            patch(
                "runtimes.chat.runtime.db.get_runtime_events",
                return_value=[
                    {
                        "payload": {
                            "tool": {
                                "result": {
                                    "kind": "spec_stage_written",
                                    "specId": "spec-session-ready",
                                }
                            }
                        },
                        "source": {},
                    }
                ],
            ),
            patch("runtimes.chat.runtime.db.get_chat_canonical_messages", return_value=[]),
        ):
            spec_id = runtime._latest_session_spec_id("session-spec")

        self.assertEqual(spec_id, "spec-session-ready")

    def test_spec_mode_prepare_uses_current_session_spec_when_request_has_no_spec_id(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            session_id="session-spec-prepare",
            messages=[ChatMessage(role="user", content="继续执行已审批的 Spec")],
            data=ChatRequestData(specMode=True),
        )

        with patch.object(ChatRuntime, "_latest_session_spec_id", return_value="spec-from-session") as latest_spec:
            prepared = runtime.prepare_request(request)

        latest_spec.assert_called_once_with("session-spec-prepare")
        self.assertEqual(prepared.spec_id, "spec-from-session")

    def test_approved_spec_runtime_stage_does_not_force_memory_first(self):
        tools = [SimpleNamespace(name="memory_broker"), SimpleNamespace(name="runtime_broker")]
        state = {
            "current_route_context": {
                "specMode": True,
                "specId": "spec-ready",
                "specBrief": {"pipelineControl": {"runtimeExecutionAllowed": True}},
            }
        }

        forced = _should_force_memory_broker_first(
            user_query="[Spec Approval Continuation] previous stages approved",
            passive_rag_diagnostics={"has_recall_cue": True},
            selected_tools=tools,
            state=state,
        )

        self.assertFalse(forced)

    def test_spec_mode_without_spec_id_guidance_reads_skill_before_stage(self):
        tools = [
            SimpleNamespace(name="fetch_skill_instructions"),
            SimpleNamespace(name="spec_broker"),
            SimpleNamespace(name="memory_broker"),
        ]
        state = {
            "current_route_context": {"specMode": True},
            "context_mentions": [{"kind": "skill", "name": "huashu-nuwa"}],
        }

        guidance = _spec_mode_stage_guidance(
            state=state,
            user_query="使用女娲技能生成玲的 skill",
            selected_tools=tools,
            messages=[],
        )

        self.assertIsNotNone(guidance)
        content = str(guidance.content)
        self.assertIn("fetch_skill_instructions(skill_name='huashu-nuwa'", content)
        self.assertIn("fetch_skill_instructions(skill_name='skill-creator'", content)
        self.assertIn("spec_broker", content)
        self.assertIn("mode='write_stage'", content)
        self.assertIn("clean user-facing contract", content)
        self.assertIn("absolute workspace paths", content)
        self.assertIn("You may use `memory_broker`, `research_broker`, or `web_broker`", content)
        self.assertIn("do not call Delegation or assume subagents can spawn grandchildren", content)
        self.assertIn("Do not call `runtime_broker`", content)

    def test_spec_mode_existing_spec_guidance_names_read_and_edit_modes(self):
        tools = [SimpleNamespace(name="spec_broker")]
        state = {"current_route_context": {"specMode": True, "specId": "spec-1"}}

        guidance = _spec_mode_stage_guidance(
            state=state,
            user_query="继续",
            selected_tools=tools,
            messages=[],
        )

        self.assertIsNotNone(guidance)
        content = str(guidance.content)
        self.assertIn("mode='read'|'read_stage'", content)
        self.assertIn("mode='write_stage'|'rewrite_stage'|'edit'|'write'|'update'", content)
        self.assertIn("do not call `spec_broker(mode='approve')` yourself", content)
        self.assertIn("Approval is a user/client governance event", content)
        self.assertIn("do not assume subagents can spawn grandchildren implicitly", content)

    def test_spec_mode_tasks_stage_guidance_includes_pipeline_template(self):
        tools = [SimpleNamespace(name="spec_broker")]
        state = {
            "current_route_context": {
                "specMode": True,
                "specId": "spec-1",
                "specBrief": {"pipelineControl": {"nextStage": "tasks", "runtimeExecutionAllowed": False}},
            }
        }

        guidance = _spec_mode_stage_guidance(
            state=state,
            user_query="继续",
            selected_tools=tools,
            messages=[],
        )

        self.assertIsNotNone(guidance)
        content = str(guidance.content)
        self.assertIn("Tasks stage rule", content)
        self.assertIn("| Task ID | Runtime lane | Goal | Depends on | Spec refs | Expected output | Acceptance / proof |", content)
        self.assertIn("If the approved requirements/design do not have formal REQ/DES IDs", content)

    def test_direct_tool_registry_first_lines_explain_spec_and_runtime_actions(self):
        from core.tools.native.delegation import delegation_broker
        from core.tools.native.runtime import runtime_broker
        from core.tools.native.spec import spec_broker
        from runtimes.extensions.skills.loader import fetch_skill_instructions

        spec_first_line = str(getattr(spec_broker, "description", "") or "").strip().split("\n")[0]
        runtime_first_line = str(getattr(runtime_broker, "description", "") or "").strip().split("\n")[0]
        delegation_first_line = str(getattr(delegation_broker, "description", "") or "").strip().split("\n")[0]
        fetch_skill_first_line = str(getattr(fetch_skill_instructions, "description", "") or "").strip().split("\n")[0]

        self.assertIn("Write/read/edit Spec Mode documents", spec_first_line)
        self.assertIn("user/client approval gates", spec_first_line)
        self.assertNotIn("approve, and advance", spec_first_line)
        self.assertIn("active execution runtimes", runtime_first_line)
        self.assertIn("Supervisor route broker", runtime_first_line)
        self.assertIn("real local subagent", delegation_first_line)
        self.assertIn("complete SKILL.md", fetch_skill_first_line)
        self.assertIn("relative_path", fetch_skill_first_line)

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

    def test_runtime_episode_audit_request_forces_planner_auto_dispatch(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="Create Engineering and Delegation runtime episodes.")],
            data=ChatRequestData(runtime_subagent_closure_live_audit=True),
        )

        prepared = runtime.prepare_request(request)

        self.assertTrue(prepared.task_planning_mode)
        self.assertEqual(prepared.planner_mode, "force")
        self.assertEqual(prepared.planner_dispatch_mode, "auto")

    def test_explicit_runtime_episode_request_forces_planner_auto_dispatch(self):
        runtime = ChatRuntime()
        request = ChatRequest(
            messages=[
                ChatMessage(
                    role="user",
                    content="必须经过 runtime_broker(route)，创建 engineering episode 并返回 typed handoff。",
                )
            ],
        )

        prepared = runtime.prepare_request(request)

        self.assertTrue(prepared.task_planning_mode)
        self.assertEqual(prepared.planner_mode, "force")
        self.assertEqual(prepared.planner_dispatch_mode, "auto")
        self.assertEqual(prepared.engineering_mode, "force")

    def test_project_coding_deferred_planner_still_requires_runtime_episode_fallback(self):
        runtime = ChatRuntime()
        chat_run = SimpleNamespace(
            prepared=SimpleNamespace(
                latest_user_content="创建一个 Canvas Web 游戏项目并保存到当前工作区。",
                spec_mode=False,
                spec_brief={},
                live_audit_context={},
                task_shape_hint={"primaryTaskShape": "project_coding"},
                engineering_required=True,
                explicit_engineering_requested=False,
                task_planning_mode=True,
            )
        )

        self.assertTrue(runtime._planner_request_requires_runtime_episode_fallback(chat_run))

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

    def test_planner_contract_verifier_repairs_skill_artifact_contract(self):
        chat_run = SimpleNamespace(
            prepared=SimpleNamespace(
                skill_references=[{"name": "huashu-nuwa"}],
                task_shape_hint={
                    "writingRoute": {
                        "mode": "skill_subagent",
                        "requiresSkillExecution": True,
                        "requiresArtifact": True,
                        "skillName": "huashu-nuwa",
                    }
                },
            )
        )
        fallback_plan = {
            "planId": "fallback",
            "executionStrategy": "delegate",
            "taskBriefs": [{"taskBriefId": "task-1", "goal": "Generate a perspective skill", "familyHint": "writing"}],
            "qualityFlags": [],
            "repairCount": 0,
        }

        repaired = ChatRuntime._verify_and_repair_planner_contract(
            {
                "planId": "model-plan",
                "executionStrategy": "delegate",
                "taskBriefs": [{"taskBriefId": "task-1", "goal": "Generate a perspective skill", "familyHint": "writing"}],
                "qualityFlags": [],
                "repairCount": 0,
            },
            fallback_plan=fallback_plan,
            chat_run=chat_run,
        )

        task = repaired["taskBriefs"][0]
        brief = task["context"]["writingExecutionBrief"]
        self.assertIn("fetch_skill_instructions", task["requiredCapabilities"])
        self.assertTrue(task["validateSkillArtifact"])
        self.assertEqual(task["requiredSkillContracts"], ["huashu-nuwa", "skill-creator"])
        self.assertEqual(brief["subagentFirstAction"], "fetch_skill_instructions")
        self.assertEqual(brief["skillArtifactContract"]["requiredValidator"], "SkillArtifactValidator")
        self.assertTrue(any(item.get("skillName") == "skill-creator" for item in brief["requiredInstructionReads"]))
        self.assertTrue(
            any(
                item.get("skillName") == "huashu-nuwa" and item.get("relativePath") == "references/skill-template.md"
                for item in brief["requiredInstructionReads"]
            )
        )
        self.assertTrue(
            any(
                item.get("skillName") == "huashu-nuwa" and item.get("relativePath") == "references/extraction-framework.md"
                for item in brief["requiredInstructionReads"]
            )
        )
        self.assertIn("planner_contract_skill_creator_full_read_required", repaired["qualityFlags"])
        self.assertIn("planner_contract_huashu_template_read_required", repaired["qualityFlags"])
        self.assertIn("planner_contract_huashu_framework_read_required", repaired["qualityFlags"])

    def test_planner_contract_verifier_does_not_force_artifact_for_huashu_plan_only(self):
        chat_run = SimpleNamespace(
            prepared=SimpleNamespace(
                skill_references=[{"name": "huashu-nuwa"}],
                task_shape_hint={
                    "writingRoute": {
                        "mode": "skill_subagent",
                        "requiresSkillExecution": True,
                        "requiresArtifact": False,
                        "skillName": "huashu-nuwa",
                    }
                },
            )
        )

        repaired = ChatRuntime._verify_and_repair_planner_contract(
            {
                "planId": "model-plan",
                "executionStrategy": "delegate",
                "taskBriefs": [{"taskBriefId": "task-1", "goal": "Read huashu-nuwa and draft an execution plan", "familyHint": "writing"}],
                "qualityFlags": [],
                "repairCount": 0,
            },
            fallback_plan=None,
            chat_run=chat_run,
        )

        task = repaired["taskBriefs"][0]
        brief = task["context"]["writingExecutionBrief"]
        self.assertIn("fetch_skill_instructions", task["requiredCapabilities"])
        self.assertFalse(task.get("validateSkillArtifact", False))
        self.assertNotIn("requiredSkillContracts", task)
        reads = brief["requiredInstructionReads"]
        self.assertTrue(any(item.get("skillName") == "huashu-nuwa" and item.get("detailLevel") == "full" for item in reads))
        self.assertFalse(any(item.get("skillName") == "skill-creator" for item in reads))
        self.assertFalse(any(item.get("relativePath") == "references/skill-template.md" for item in reads))

    def test_planner_contract_verifier_repairs_delegation_capability_into_task_shape(self):
        chat_run = SimpleNamespace(
            prepared=SimpleNamespace(
                skill_references=[],
                task_shape_hint={},
            )
        )

        repaired = ChatRuntime._verify_and_repair_planner_contract(
            {
                "planId": "model-plan",
                "executionStrategy": "delegate",
                "capabilityPlan": [
                    {
                        "kind": "delegation",
                        "reason": "Need an independent subagent to review the proposed runtime plan.",
                    }
                ],
                "taskBriefs": [],
                "qualityFlags": [],
                "repairCount": 0,
            },
            fallback_plan=None,
            chat_run=chat_run,
        )

        self.assertEqual(repaired["taskBriefs"][0]["executionLaneHint"], "subagent")
        self.assertIn("subagent_execution", repaired["taskBriefs"][0]["requiredCapabilities"])
        self.assertIn("independent_review", repaired["taskBriefs"][0]["requiredCapabilities"])
        self.assertIn("planner_contract_delegation_task_created", repaired["qualityFlags"])
        self.assertNotIn("planner_contract_delegation_without_tasks", repaired["qualityFlags"])

    def test_planner_contract_verifier_repairs_explainer_video_to_engineering(self):
        chat_run = SimpleNamespace(
            prepared=SimpleNamespace(
                skill_references=[],
                task_shape_hint={
                    "boundaryDecision": {
                        "primaryRuntime": "engineering",
                        "executionMode": "code_video_runtime",
                        "reason": "explainer_or_course_video_prefers_editable_code_timeline",
                        "forbiddenRoutes": ["creative_media_as_primary_unless_provider_named"],
                    }
                },
            )
        )

        repaired = ChatRuntime._verify_and_repair_planner_contract(
            {
                "planId": "model-plan",
                "executionStrategy": "delegate",
                "capabilityPlan": [{"kind": "creative_media", "taskBriefId": "task-1"}],
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "Make an explainer video",
                        "familyHint": "creative_media",
                        "executionLaneHint": "creative_media",
                    }
                ],
                "qualityFlags": [],
                "repairCount": 0,
            },
            fallback_plan=None,
            chat_run=chat_run,
        )

        self.assertEqual(repaired["taskBriefs"][0]["familyHint"], "engineering")
        self.assertEqual(repaired["taskBriefs"][0]["executionLaneHint"], "auto")
        self.assertIn("creative_media", repaired["taskBriefs"][0]["supportingRuntimes"])
        self.assertTrue(any(item.get("kind") == "engineering" for item in repaired["capabilityPlan"]))
        self.assertIn("planner_boundary_primary_engineering_repaired", repaired["qualityFlags"])

    def test_planner_contract_verifier_repairs_literal_terminal_to_native_command_route(self):
        chat_run = SimpleNamespace(
            prepared=SimpleNamespace(
                skill_references=[],
                task_shape_hint={
                    "boundaryDecision": {
                        "primaryRuntime": "engineering",
                        "executionMode": "native_terminal_command",
                        "reason": "logical_terminal_request_prefers_native_command_session",
                        "forbiddenRoutes": ["computer_use_for_literal_terminal_only"],
                    }
                },
            )
        )

        repaired = ChatRuntime._verify_and_repair_planner_contract(
            {
                "planId": "model-plan",
                "executionStrategy": "delegate",
                "capabilityPlan": [{"kind": "computer_use", "taskBriefId": "task-1"}],
                "taskBriefs": [{"taskBriefId": "task-1", "goal": "Open terminal and install a skill", "familyHint": "computer_use"}],
                "qualityFlags": [],
                "repairCount": 0,
            },
            fallback_plan=None,
            chat_run=chat_run,
        )

        self.assertEqual(repaired["taskBriefs"][0]["familyHint"], "engineering")
        self.assertFalse(any(item.get("kind") == "computer_use" for item in repaired["capabilityPlan"]))
        self.assertTrue(any(item.get("kind") == "engineering" for item in repaired["capabilityPlan"]))
        self.assertIn("planner_boundary_terminal_native_command_repaired", repaired["qualityFlags"])

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

    def test_planner_repair_synthesizes_runtime_chain_from_bad_model_plan(self):
        plan = ChatRuntime._validate_and_repair_planner_plan(
            {
                "planId": "plan-bad-model",
                "executionStrategy": "delegate",
                "planSummary": "演示一次调研 + 工程 + 子 agent + child delegation 的主链调度。",
                "qualityFlags": ["planner_fallback_used", "source_quality_required", "delegation_required_by_task_shape"],
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "实现演示工程。",
                        "writeSet": ["demo/app.py"],
                        "behaviorScope": ["implementation"],
                        "familyHint": "engineering",
                    },
                    {
                        "taskBriefId": "task-2",
                        "goal": "继续实现演示工程。",
                        "writeSet": ["demo/app.py"],
                        "behaviorScope": ["implementation"],
                        "familyHint": "engineering",
                    },
                ],
            },
            fallback_plan={"planSummary": "fallback"},
        )

        kinds = [item.get("kind") for item in plan["capabilityPlan"]]
        self.assertIn("research", kinds)
        self.assertIn("engineering", kinds)
        self.assertIn("delegation", kinds)
        self.assertIn("capability_plan_research_repaired", plan["qualityFlags"])
        self.assertIn("capability_plan_delegation_repaired", plan["qualityFlags"])
        delegation_task_id = next(item["taskBriefId"] for item in plan["capabilityPlan"] if item["kind"] == "delegation")
        delegation_task = next(item for item in plan["taskBriefs"] if item["taskBriefId"] == delegation_task_id)
        self.assertTrue(delegation_task["allowChildDelegation"])
        self.assertEqual(delegation_task["childDelegationBudget"]["maxDepth"], 1)
        self.assertTrue(plan["handoffPlan"])

    def test_planner_auto_dispatch_routes_capability_plan_despite_write_conflict(self):
        plan = ChatRuntime._validate_and_repair_planner_plan(
            {
                "planId": "plan-conflict-runtime",
                "executionStrategy": "delegate",
                "planSummary": "调研、工程实现并让子 agent 复核。",
                "qualityFlags": ["source_quality_required", "delegation_required_by_task_shape"],
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "实现核心文件。",
                        "writeSet": ["same/file.py"],
                        "familyHint": "engineering",
                        "executionLaneHint": "auto",
                    },
                    {
                        "taskBriefId": "task-2",
                        "goal": "实现同一核心文件的另一部分。",
                        "writeSet": ["same/file.py"],
                        "familyHint": "engineering",
                        "executionLaneHint": "auto",
                    },
                ],
            },
            fallback_plan={"planSummary": "fallback"},
        )
        decision = ChatRuntime._decide_planner_auto_dispatch(
            plan,
            registry={
                "subagents": [
                    {
                        "id": "engineering-reviewer",
                        "name": "Engineering Reviewer",
                        "isEnabled": True,
                        "description": "review specialist",
                        "capabilitySnapshot": {
                            "specialistFamily": "engineering",
                            "agentClass": "reviewer",
                            "domainTags": ["engineering"],
                            "operationCapabilities": ["review", "verification", "subagent_collaboration"],
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
        target_ids = [item["targetId"] for item in decision["selectedTargets"]]
        self.assertIn("local_runtime:engineering", target_ids)
        self.assertIn("engineering-reviewer", target_ids)

    def test_planner_auto_dispatch_repairs_review_task_write_set_and_unblocks_subagent(self):
        plan = ChatRuntime._validate_and_repair_planner_plan(
            {
                "planId": "plan-review",
                "executionStrategy": "mixed",
                "planSummary": "Research, implement, then delegate an independent review.",
                "taskBriefs": [
                    {
                        "taskBriefId": "task-1",
                        "goal": "Collect evidence for the implementation plan.",
                        "writeSet": ["docs/research-notes.md"],
                        "behaviorScope": ["research"],
                        "requiredCapabilities": ["research"],
                    },
                    {
                        "taskBriefId": "task-2",
                        "goal": "Implement the runtime route and episode handoff.",
                        "writeSet": ["src/runtime.py"],
                        "behaviorScope": ["implementation"],
                        "requiredCapabilities": ["software_engineering"],
                    },
                    {
                        "taskBriefId": "task-3",
                        "goal": "Delegate an independent review of the runtime handoff.",
                        "writeSet": ["docs/research-notes.md", "src/runtime.py"],
                        "criticalFiles": ["docs/research-notes.md"],
                        "behaviorScope": ["delegated_execution", "verification", "child_delegation"],
                        "requiredCapabilities": ["review", "verification", "subagent_collaboration"],
                        "acceptanceContract": "Return confirmed subagent task results or a recoverable missing-target diagnostic; do not report fake delegation.",
                        "dependency": ["task-2"],
                        "parallelGroup": "delegation",
                        "executionLaneHint": "subagent",
                        "familyHint": "engineering",
                    },
                ],
            },
            fallback_plan={"planSummary": "Research, implement, then delegate an independent review."},
        )

        review_task = plan["taskBriefs"][2]
        self.assertEqual(review_task["writeSet"], [])
        self.assertIn("docs/research-notes.md", review_task["readSet"])
        self.assertIn("src/runtime.py", review_task["readSet"])
        self.assertEqual(review_task["criticalFiles"], [])
        self.assertIn("review_task_write_set_cleared", plan["qualityFlags"])
        self.assertIn("review_task_critical_files_cleared", plan["qualityFlags"])

        decision = ChatRuntime._decide_planner_auto_dispatch(
            plan,
            registry={
                "subagents": [
                    {
                        "id": "engineering-reviewer",
                        "name": "Engineering Reviewer",
                        "isEnabled": True,
                        "description": "review specialist",
                        "capabilitySnapshot": {
                            "specialistFamily": "engineering",
                            "agentClass": "reviewer",
                            "domainTags": ["engineering"],
                            "operationCapabilities": ["review", "verification", "subagent_collaboration"],
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
        self.assertEqual(decision["selectedTargets"][2]["targetId"], "engineering-reviewer")

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

    def test_planner_auto_dispatch_accepts_explicit_local_runtime_lane(self):
        decision = ChatRuntime._decide_planner_auto_dispatch(
            {
                "executionStrategy": "delegate",
                "taskBriefs": [
                    {
                        "taskBriefId": "task-skill-artifact",
                        "goal": "Generate workspace skill artifact",
                        "familyHint": "engineering",
                        "executionLaneHint": "engineering",
                        "requiredCapabilities": ["skill_artifact_validation", "workspace_file_write"],
                    }
                ],
            },
            registry={"subagents": [], "externalWorkers": []},
            planner_mode="force",
            planner_dispatch_mode="auto",
        )

        self.assertTrue(decision["willDispatch"])
        self.assertEqual(decision["reason"], "eligible")
        self.assertEqual(decision["selectedTargets"][0]["targetId"], "local_runtime:engineering")

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

    def test_planner_auto_dispatch_node_synthesizes_episode_from_task_briefs(self):
        node = build_planner_auto_dispatch_node()
        command = node(
            {
                "session_id": "session-synth",
                "run_id": "run-synth",
                "workspace_path": r"E:\Projects\demo",
                "planner_plan": {
                    "planId": "plan-synth",
                    "taskBriefs": [
                        {
                            "taskBriefId": "task-1",
                            "goal": "Create a tiny demo file through runtime routing.",
                            "familyHint": "engineering",
                            "writeSet": ["<workspace_root>/hello_demo.txt"],
                        }
                    ],
                    "autoDispatchDecision": {
                        "mode": "auto",
                        "willDispatch": True,
                        "reason": "eligible",
                    },
                },
            }
        )

        self.assertEqual(getattr(command, "goto", None), "runtime_episode")
        self.assertNotIn("messages", command.update)
        self.assertEqual(command.update["planner_dispatch_status"]["episodeCount"], 1)
        episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
        self.assertEqual(episode["kind"], "engineering")
        self.assertEqual(episode["state"], "queued")
        self.assertEqual(episode["session_id"], "session-synth")
        self.assertEqual(episode["run_id"], "run-synth")
        self.assertEqual(episode["inputs"]["workspacePath"], r"E:\Projects\demo")

    def test_planner_auto_dispatch_research_uses_task_route_query_and_run_mode(self):
        node = build_planner_auto_dispatch_node()
        command = node(
            {
                "session_id": "session-research-route",
                "run_id": "run-research-route",
                "planner_plan": {
                    "planId": "plan-research-route",
                    "capabilityPlan": [
                        {
                            "kind": "research",
                            "source": "planner_fallback",
                            "reason": "skill_driven_writing_requires_source_evidence",
                            "taskBriefId": "task-1",
                        }
                    ],
                    "taskBriefs": [
                        {
                            "taskBriefId": "task-1",
                            "goal": "Collect real sources for March 7th before skill writing.",
                            "routeQuery": "调研《崩坏：星穹铁道》三月七官方设定、剧情台词、表达风格和版本时间线",
                            "context": {"sourcePolicy": "multi_source_full_read_required"},
                            "requiredCapabilities": ["web_research", "evidence_bundle", "claim_table"],
                            "familyHint": "research",
                        }
                    ],
                    "autoDispatchDecision": {
                        "mode": "auto",
                        "willDispatch": True,
                        "reason": "eligible",
                    },
                },
            }
        )

        self.assertEqual(getattr(command, "goto", None), "runtime_episode")
        episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
        self.assertEqual(episode["kind"], "research")
        self.assertIn("三月七", episode["inputs"]["query"])
        self.assertEqual(episode["inputs"]["mode"], "run")
        self.assertNotEqual(episode["inputs"]["query"], "skill_driven_writing_requires_source_evidence")

    def test_planner_auto_dispatch_node_preserves_child_delegation_policy(self):
        node = build_planner_auto_dispatch_node()
        command = node(
            {
                "session_id": "session-child-policy",
                "run_id": "run-child-policy",
                "planner_plan": {
                    "planId": "plan-child-policy",
                    "taskBriefs": [
                        {
                            "taskBriefId": "task-1",
                            "goal": "Dispatch review workers and allow one child verification hop.",
                            "familyHint": "delegation",
                            "targetCount": 1,
                            "allowChildDelegation": True,
                            "childDelegationBudget": {"maxChildren": 2, "maxDepth": 1, "maxTotalNodes": 3},
                            "writeSetPartitions": [{"path": "docs/**", "owner": "reviewer"}],
                        }
                    ],
                    "autoDispatchDecision": {
                        "mode": "auto",
                        "willDispatch": True,
                        "reason": "eligible",
                    },
                },
            }
        )

        self.assertEqual(getattr(command, "goto", None), "runtime_episode")
        episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
        self.assertEqual(episode["kind"], "delegation")
        self.assertTrue(episode["inputs"]["allowChildDelegation"])
        self.assertEqual(episode["inputs"]["childDelegationBudget"], {"maxChildren": 2, "maxDepth": 1, "maxTotalNodes": 3})
        self.assertEqual(episode["inputs"]["writeSetPartitions"], [{"path": "docs/**", "owner": "reviewer"}])

    def test_planner_auto_dispatch_node_allows_engineering_child_delegation_when_plan_requires_it(self):
        node = build_planner_auto_dispatch_node()
        command = node(
            {
                "session_id": "session-engineering-child-policy",
                "run_id": "run-engineering-child-policy",
                "planner_plan": {
                    "planId": "plan-engineering-child-policy",
                    "planSummary": "Research, implement, then use subagent child delegation to verify the work.",
                    "qualityFlags": ["delegation_required_by_task_shape"],
                    "capabilityPlan": [
                        {
                            "kind": "engineering",
                            "source": "planner",
                            "reason": "implementation_required",
                            "taskBriefId": "task-1",
                        },
                        {
                            "kind": "delegation",
                            "source": "planner",
                            "reason": "child_delegation_required",
                            "taskBriefId": "task-2",
                            "requiredRuntimeAccess": ["delegation.recursive"],
                        },
                    ],
                    "taskBriefs": [
                        {
                            "taskBriefId": "task-1",
                            "goal": "Implement the project change.",
                            "writeSet": ["apps/demo/**"],
                            "familyHint": "engineering",
                        },
                        {
                            "taskBriefId": "task-2",
                            "goal": "Verify with a child delegation hop.",
                            "familyHint": "delegation",
                            "runtimeAccess": ["delegation.recursive"],
                            "allowChildDelegation": True,
                            "childDelegationBudget": {"maxChildren": 2, "maxDepth": 1, "maxTotalNodes": 3},
                        },
                    ],
                    "autoDispatchDecision": {
                        "mode": "auto",
                        "willDispatch": True,
                        "reason": "eligible",
                    },
                },
            }
        )

        self.assertEqual(getattr(command, "goto", None), "runtime_episode")
        episodes = command.update["current_route_context"]["capabilityEpisodes"]
        engineering = next(item for item in episodes if item["kind"] == "engineering")
        self.assertTrue(engineering["inputs"]["allowChildDelegation"])
        self.assertEqual(engineering["inputs"]["childDelegationBudget"]["maxDepth"], 1)

    def test_planner_auto_dispatch_node_enters_wait_for_prequeued_episode(self):
        node = build_planner_auto_dispatch_node()
        command = node(
            {
                "session_id": "session-prequeued",
                "run_id": "run-prequeued",
                "current_route_context": {
                    "capabilityEpisodes": [
                        {
                            "episodeId": "episode-prequeued",
                            "needId": "episode-prequeued",
                            "kind": "engineering",
                            "state": "queued",
                        }
                    ]
                },
                "planner_dispatch_status": {
                    "mode": "auto",
                    "dispatched": True,
                    "nextAction": "wait_episode",
                    "episodeCount": 1,
                },
                "planner_plan": {
                    "planId": "plan-prequeued",
                    "capabilityPlan": [
                        {
                            "episodeId": "episode-prequeued",
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
                },
            }
        )

        self.assertEqual(getattr(command, "goto", None), "runtime_episode")
        self.assertEqual(command.update["planner_dispatch_status"]["nextAction"], "wait_episode")
        episode = command.update["current_route_context"]["capabilityEpisodes"][-1]
        self.assertEqual(episode["episodeId"], "episode-prequeued")
        self.assertEqual(episode["state"], "queued")
        self.assertEqual(episode["inputs"]["taskBriefs"][0]["taskBriefId"], "task-1")

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

    def test_runtime_episode_wait_node_reports_failed_handoff_as_recoverable_failure(self):
        node = build_runtime_episode_wait_node()
        episode_id = f"episode_wait_node_failed_handoff_{uuid4().hex}"
        episode = build_runtime_episode(
            need={"episodeId": episode_id, "kind": "engineering", "reason": "create artifact"},
            kind="engineering",
            state="queued",
            continuation_target="runtime_episode_runner",
        )
        db.upsert_runtime_episode_record(episode, enqueue=True, priority=999)
        handoff = build_handoff_ref(
            producer_episode_id=episode_id,
            kind="engineering",
            compact_summary="Delegated artifact creation failed acceptance.",
            status="failed",
            extra={"errorCode": "artifact_acceptance_failed"},
        )
        db.add_runtime_episode_handoff(episode_id=episode_id, handoff=handoff)
        db.complete_runtime_episode(
            episode_id,
            state="failed",
            result_ref=handoff["handoffRefId"],
            error_code="artifact_acceptance_failed",
            error_message="Required SKILL.md was missing.",
        )

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
        self.assertEqual(command.update["planner_dispatch_status"]["nextAction"], "recoverable_failure")
        self.assertEqual(command.update["planner_dispatch_status"]["state"], "episode_failed")
        self.assertEqual(command.update["planner_dispatch_status"]["failedHandoffCount"], 1)

    def test_runtime_episode_wait_node_resumes_when_only_optional_lane_failed(self):
        node = build_runtime_episode_wait_node()
        research_id = f"episode_wait_node_optional_research_{uuid4().hex}"
        delegation_id = f"episode_wait_node_optional_delegation_{uuid4().hex}"
        research = build_runtime_episode(
            need={"episodeId": research_id, "kind": "research", "reason": "need evidence"},
            kind="research",
            state="completed",
            continuation_target="runtime_episode_runner",
        )
        optional_delegation = build_runtime_episode(
            need={"episodeId": delegation_id, "kind": "delegation", "reason": "optional review"},
            kind="delegation",
            state="queued",
            continuation_target="runtime_episode_runner",
            extra={"optional": True, "dependencyMode": "optional"},
        )
        db.upsert_runtime_episode_record(research, enqueue=False)
        db.upsert_runtime_episode_record(optional_delegation, enqueue=False)
        research_handoff = build_handoff_ref(
            producer_episode_id=research_id,
            kind="research",
            compact_summary="Research evidence bundle ready.",
            status="ready",
        )
        delegation_handoff = build_handoff_ref(
            producer_episode_id=delegation_id,
            kind="delegation",
            compact_summary="Optional subagent review failed.",
            status="failed",
            extra={"errorCode": "optional_subagent_failed"},
        )
        db.add_runtime_episode_handoff(episode_id=research_id, handoff=research_handoff)
        db.complete_runtime_episode(research_id, state="completed", result_ref=research_handoff["handoffRefId"])
        db.add_runtime_episode_handoff(episode_id=delegation_id, handoff=delegation_handoff)
        db.complete_runtime_episode(
            delegation_id,
            state="failed",
            result_ref=delegation_handoff["handoffRefId"],
            error_code="optional_subagent_failed",
            error_message="Optional lane failed.",
        )

        command = asyncio.run(
            node(
                {
                    "current_route_context": {
                        "capabilityEpisodes": [research, optional_delegation],
                    }
                }
            )
        )

        self.assertEqual(getattr(command, "goto", None), "supervisor")
        self.assertEqual(command.update["planner_dispatch_status"]["nextAction"], "resume_supervisor")
        self.assertEqual(command.update["planner_dispatch_status"]["state"], "degraded_handoff_ready")
        self.assertEqual(command.update["planner_dispatch_status"]["degradedEpisodeCount"], 1)

    def test_runtime_episode_wait_node_does_not_resume_on_partial_handoff(self):
        node = build_runtime_episode_wait_node()
        research_id = "episode_wait_node_partial_research"
        engineering_id = "episode_wait_node_partial_engineering"
        research = build_runtime_episode(
            need={"episodeId": research_id, "kind": "research", "reason": "need evidence"},
            kind="research",
            state="completed",
            continuation_target="runtime_episode_runner",
        )
        engineering = build_runtime_episode(
            need={"episodeId": engineering_id, "kind": "engineering", "reason": "implementation still running"},
            kind="engineering",
            state="active",
            continuation_target="runtime_episode_runner",
        )
        db.upsert_runtime_episode_record(research, enqueue=False)
        db.upsert_runtime_episode_record(engineering, enqueue=False)
        handoff = build_handoff_ref(
            producer_episode_id=research_id,
            kind="research",
            compact_summary="Research evidence bundle ready.",
            status="ready",
        )
        db.add_runtime_episode_handoff(episode_id=research_id, handoff=handoff)
        db.complete_runtime_episode(research_id, state="completed", result_ref=handoff["handoffRefId"])

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(
                asyncio.wait_for(
                    node(
                        {
                            "current_route_context": {
                                "capabilityEpisodes": [research, engineering],
                            }
                        }
                    ),
                    timeout=0.2,
                )
            )

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
