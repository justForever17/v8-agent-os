from __future__ import annotations

from runtimes.rpa.keyword_contract import bridge_keyword_issues
from runtimes.rpa.robot_adapter import RobotFrameworkAdapter


def test_canvas_action_uses_have_runtime_contracts():
    assert bridge_keyword_issues(
        [
            "browser_click",
            "browser_type",
            "browser_assert",
            "browser_extract",
            "assert_text",
            "set_variable",
            "file_copy",
            "http_request",
            "ocr",
            "llm_call",
            "subflow",
            "comment",
            "loop",
            "try_catch",
        ]
    ) == []


def test_robot_export_renders_loop_body_once():
    adapter = RobotFrameworkAdapter()
    script = {
        "id": "draft_loop",
        "name": "Loop smoke",
        "appId": "desktop",
        "steps": [
            {
                "stepId": "click_body",
                "use": "click",
                "intent": "click body",
                "params": {"x": 10, "y": 20},
            },
            {
                "stepId": "repeat_body",
                "use": "loop",
                "intent": "repeat body",
                "params": {
                    "count": 3,
                    "loopStartStepKey": "click_body",
                    "loopEndStepKey": "click_body",
                },
            },
        ],
    }

    robot = adapter.render_script(script)

    assert "|  | FOR | ${rpa_loop_index} | IN RANGE | 3 |" in robot
    assert "|  |  | Click | x=10 | y=20 |" in robot
    assert robot.count("STEP click_body") == 1


def test_robot_export_accepts_action_field_from_canvas_nodes():
    adapter = RobotFrameworkAdapter()
    script = {
        "id": "draft_canvas",
        "name": "Canvas smoke",
        "appId": "desktop",
        "steps": [
            {"stepId": "browser_click", "action": "browser_click", "params": {"selector": "css:#ok"}},
            {"stepId": "set_value", "action": "set_variable", "params": {"name": "answer", "value": "42"}},
            {"stepId": "note", "action": "comment", "params": {"text": "done"}},
        ],
    }

    assert adapter._export_contract_issues(script) == []
    robot = adapter.render_script(script)
    assert "|  | Click | selector=css:#ok |" in robot
    assert "|  | Set Workflow Variable | name=answer | value=42 |" in robot
    assert "|  | Comment | done |" in robot


def test_robot_export_renders_nested_control_nodes():
    adapter = RobotFrameworkAdapter()
    script = {
        "id": "draft_nested",
        "name": "Nested control smoke",
        "appId": "desktop",
        "steps": [
            {"stepId": "loop", "action": "loop", "params": {"count": 2, "bodyStepKeys": ["guard"]}},
            {"stepId": "guard", "action": "if", "params": {"condition": "${TRUE}", "bodyStepKeys": ["body"]}},
            {"stepId": "body", "action": "subflow", "params": {"scriptId": "child_flow"}},
        ],
    }

    robot = adapter.render_script(script)

    assert "|  | FOR | ${rpa_loop_index} | IN RANGE | 2 |" in robot
    assert "|  |  | IF | ${TRUE} |" in robot
    assert "|  |  |  | Run Subflow | scriptId=child_flow |" in robot
    assert robot.count("STEP body") == 1
