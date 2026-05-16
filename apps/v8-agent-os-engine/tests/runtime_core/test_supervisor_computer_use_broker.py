from __future__ import annotations

import json
import sys
import unittest
import importlib.machinery
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest import mock


if "chromadb" not in sys.modules:
    class _FakeChromaCollection:
        def upsert(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {}

    class _FakeChromaClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def get_or_create_collection(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeChromaCollection()

    sys.modules["chromadb"] = SimpleNamespace(PersistentClient=_FakeChromaClient)

if "bs4" not in sys.modules:
    fake_bs4 = ModuleType("bs4")
    fake_bs4.BeautifulSoup = object
    fake_bs4.__spec__ = importlib.machinery.ModuleSpec("bs4", loader=None)
    sys.modules["bs4"] = fake_bs4

if "scrapling.core.storage" not in sys.modules:
    fake_scrapling_storage = ModuleType("scrapling.core.storage")
    fake_scrapling_storage.SQLiteStorageSystem = object
    fake_scrapling_storage.__spec__ = importlib.machinery.ModuleSpec("scrapling.core.storage", loader=None)
    sys.modules["scrapling.core.storage"] = fake_scrapling_storage

if "scrapling.parser" not in sys.modules:
    fake_scrapling_parser = ModuleType("scrapling.parser")
    fake_scrapling_parser.Selector = object
    fake_scrapling_parser.__spec__ = importlib.machinery.ModuleSpec("scrapling.parser", loader=None)
    sys.modules["scrapling.parser"] = fake_scrapling_parser

if "langgraph.checkpoint.sqlite.aio" not in sys.modules:
    fake_langgraph_aio = ModuleType("langgraph.checkpoint.sqlite.aio")
    fake_langgraph_aio.AsyncSqliteSaver = object
    fake_langgraph_aio.__spec__ = importlib.machinery.ModuleSpec("langgraph.checkpoint.sqlite.aio", loader=None)
    sys.modules["langgraph.checkpoint.sqlite.aio"] = fake_langgraph_aio

from core.computer_use_execution_route import build_compact_execution_route
from core.computer_use_tool_surface import select_supervisor_native_tools
import core.native_tools as native_tools_module


class _Tool:
    def __init__(self, name: str):
        self.name = name


class SupervisorComputerUseBrokerTests(unittest.TestCase):
    def test_supervisor_computer_use_surface_is_route_first_and_brokered(self):
        selected = select_supervisor_native_tools(
            filtered_native_tools=[
                _Tool("computer_use_list_apps"),
                _Tool("computer_use_list_primitives"),
                _Tool("computer_use_desktop_capabilities"),
                _Tool("computer_use_lookup_muscle_memory"),
                _Tool("computer_use_list_muscle_memories"),
                _Tool("computer_use_resolve_execution_route"),
                _Tool("computer_use_launch_app"),
                _Tool("computer_use_ensure_window"),
                _Tool("computer_use_observe_scene"),
                _Tool("computer_use_execute_task"),
                _Tool("computer_use_click_target"),
                _Tool("computer_use_input_text"),
                _Tool("computer_use_paste_text"),
                _Tool("computer_use_paste_files"),
                _Tool("computer_use_right_click_target"),
                _Tool("computer_use_hover_target"),
                _Tool("computer_use_send_hotkey"),
                _Tool("computer_use_scroll_view"),
                _Tool("computer_use_drag_pointer"),
            ],
            supervisor_allowed_tools=None,
            config_allowed_tools=None,
        )

        self.assertEqual(
            [tool.name for tool in selected],
            [
                "computer_use_desktop_capabilities",
                "computer_use_observe_scene",
                "computer_use_execute_task",
            ],
        )

    def test_compact_execution_route_recommends_task_broker(self):
        payload = build_compact_execution_route(
            action="resolve_execution_route",
            goal="在 Excel 中整理本周报表",
            app_hint="Excel",
            target_hint="报表表格",
            resolved_app={"appId": "excel", "displayName": "Microsoft Excel"},
            route={
                "appId": "excel",
                "recommendedMode": "reuse_mode",
                "recommendedAction": "reuse_template",
                "recommendedTemplateId": "template-1",
                "recommendedDraftId": "draft-7",
                "summary": {},
                "matches": [],
            },
        )

        self.assertEqual(payload["recommendedTool"], "computer_use_execute_task")
        self.assertEqual(
            payload["recommendedToolInput"],
            {
                "goal": "在 Excel 中整理本周报表",
                "app": "Excel",
                "target": "报表表格",
            },
        )

    def test_execute_task_resolves_route_internally_when_no_route_context(self):
        route = {
            "goal": "打开记事本",
            "requestedApp": None,
            "target": None,
            "appId": "browser_checkout",
            "executionReadyMode": "learn_mode",
        }
        computer_use_runtime = mock.Mock()
        computer_use_runtime.prepare_task_loop.return_value = {"domain": {}}
        computer_use_runtime.playbook_executor_registry = mock.Mock()
        computer_use_runtime.playbook_executor_registry.can_handle.return_value = False
        computer_use_runtime.plan.return_value = {"planner": {"steps": [{"action": "observe"}]}}
        computer_use_runtime.execute_plan.return_value = {"steps": []}
        compact_execution_payload = {
            "ok": True,
            "executionSummary": {"ok": True, "totalSteps": 1, "completedSteps": 1},
            "contractSummary": {"steps": []},
        }

        with mock.patch.object(
            native_tools_module,
            "_desktop_route_gate",
            return_value=(False, '{"gateErrorCode":"ROUTE_GATE_REQUIRED"}', None),
        ), \
            mock.patch.object(native_tools_module, "_computer_use_build_desktop_route", return_value=({}, route)), \
            mock.patch.object(native_tools_module, "_desktop_route_merge_into_response", side_effect=lambda response, **kwargs: response), \
            mock.patch.object(native_tools_module, "_get_computer_use_runtime", return_value=computer_use_runtime), \
            mock.patch.object(native_tools_module, "_guard_computer_use_steps", return_value=(True, None)), \
            mock.patch.object(native_tools_module, "_computer_use_attach_plan_contract_summary", return_value=compact_execution_payload):
            result = native_tools_module.computer_use_execute_task.func(goal="打开记事本")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["executedBy"], "computer_use")
        computer_use_runtime.plan.assert_called_once()

    def test_execute_task_dispatches_reuse_mode_to_rpa(self):
        route = {
            "goal": "在 Excel 中整理本周报表",
            "requestedApp": "Excel",
            "target": "报表表格",
            "appId": "excel",
            "executionReadyMode": "reuse_mode",
            "recommendedDraftId": "draft-7",
        }
        rpa_runtime = mock.Mock()
        rpa_runtime.run_draft.return_value = {
            "status": "completed",
            "scriptId": "draft-7",
            "templateExecutionPolicy": {"executionPath": "reuse_mode"},
        }

        with mock.patch.object(native_tools_module, "_computer_use_resolve_app", return_value={"appId": "excel", "displayName": "Microsoft Excel"}), \
            mock.patch.object(native_tools_module, "_desktop_route_gate", return_value=(True, None, route)), \
            mock.patch.object(native_tools_module, "_desktop_route_merge_into_response", side_effect=lambda response, **kwargs: response), \
            mock.patch.object(native_tools_module, "_get_rpa_runtime", return_value=rpa_runtime), \
            mock.patch.object(native_tools_module, "get_runtime_context", return_value={"session_id": "session-1", "run_id": "run-1"}):
            result = native_tools_module.computer_use_execute_task.func(
                goal="在 Excel 中整理本周报表",
                app="Excel",
                target="报表表格",
                state={"current_route_context": {"desktopRoute": route}},
            )

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["executedBy"], "rpa")
        self.assertEqual(payload["executionReadyMode"], "reuse_mode")
        self.assertEqual(payload["recommendedNextAction"], "observe_scene_verify")
        rpa_runtime.run_draft.assert_called_once()

    def test_execute_task_dispatches_learn_mode_to_computer_use_runtime(self):
        route = {
            "goal": "在记事本里写一段测试文本",
            "requestedApp": "Notepad",
            "target": "编辑区",
            "appId": "notepad",
            "executionReadyMode": "learn_mode",
        }
        computer_use_runtime = mock.Mock()
        computer_use_runtime.plan.return_value = {
            "planner": {
                "steps": [
                    {"action": "observe"},
                    {"action": "click"},
                ]
            }
        }
        computer_use_runtime.execute_plan.return_value = {
            "steps": [],
        }
        compact_execution_payload = {
            "ok": True,
            "executionSummary": {
                "ok": True,
                "totalSteps": 2,
                "completedSteps": 2,
                "blockedSteps": 0,
                "updateRequestedSteps": 0,
                "failedSteps": 0,
                "otherSteps": 0,
            },
            "contractSummary": {
                "steps": [
                    {
                        "index": 1,
                        "action": "observe",
                        "status": "completed",
                        "summary": "已完成观察。",
                        "recommendedNextAction": "continue",
                    }
                ]
            },
            "visualSignalSummary": {"visualLocatorBacked": True},
            "timingSignalSummary": {"waitSensitive": False},
            "environmentSignalSummary": {"desktopEnvironmentAware": True},
        }

        with mock.patch.object(native_tools_module, "_computer_use_resolve_app", return_value={"appId": "notepad", "displayName": "Notepad"}), \
            mock.patch.object(native_tools_module, "_desktop_route_gate", return_value=(True, None, route)), \
            mock.patch.object(native_tools_module, "_desktop_route_merge_into_response", side_effect=lambda response, **kwargs: response), \
            mock.patch.object(native_tools_module, "_get_computer_use_runtime", return_value=computer_use_runtime), \
            mock.patch.object(native_tools_module, "_guard_computer_use_steps", return_value=(True, None)), \
            mock.patch.object(native_tools_module, "_computer_use_attach_plan_contract_summary", return_value=compact_execution_payload):
            result = native_tools_module.computer_use_execute_task.func(
                goal="在记事本里写一段测试文本",
                app="Notepad",
                target="编辑区",
                successCriteria="文本已经出现在编辑区并保持可见",
                state={"current_route_context": {"desktopRoute": route}},
            )

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["executedBy"], "computer_use")
        self.assertEqual(payload["executionReadyMode"], "learn_mode")
        self.assertEqual(payload["recommendedNextAction"], "observe_scene_verify")
        computer_use_runtime.plan.assert_called_once()
        computer_use_runtime.execute_plan.assert_called_once()


if __name__ == "__main__":
    unittest.main()

