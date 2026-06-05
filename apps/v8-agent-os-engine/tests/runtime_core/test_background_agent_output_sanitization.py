from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.llm_tree_prefilter import _response_text
from erc.safety_guardian import safety_guardian
from runtimes.computer_use.runtime import ComputerUseRuntime
from runtimes.computer_use.visual_actor_provider import _parse_json_response
from runtimes.computer_use.visual_judge import run_visual_judge
from runtimes.rpa.robot_keywords import V8ChatRPAKeywords


def _response(content):  # noqa: ANN001
    return SimpleNamespace(content=content, additional_kwargs={"reasoning_content": "hidden extra"})


def test_prefilter_ignores_reasoning_json() -> None:
    text = _response_text(
        _response(
            [
                {"type": "reasoning", "text": '{"selected":["wrong"]}'},
                {"type": "text", "text": '{"selected":["right"]}'},
            ]
        )
    )

    assert text == '{"selected":["right"]}'
    assert "wrong" not in text


def test_safety_review_text_ignores_reasoning_json() -> None:
    text = safety_guardian._extract_llm_text(  # noqa: SLF001
        _response(
            [
                {"type": "reasoning", "text": '{"decision":"block"}'},
                {"type": "text", "text": '{"decision":"allow"}'},
            ]
        )
    )

    assert text == '{"decision":"allow"}'
    assert "block" not in text


def test_computer_use_planner_does_not_use_reasoning_steps() -> None:
    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)

    text = runtime._planner_response_text(  # noqa: SLF001
        _response([{"type": "reasoning", "text": '[{"action":"click"}]'}])
    )

    assert text == ""


def test_visual_actor_json_parser_ignores_reasoning_action() -> None:
    payload = _parse_json_response(
        _response(
            [
                {"type": "reasoning", "text": '{"status":"proposed","candidateId":"danger"}'},
                {"type": "text", "text": '{"status":"no_action","reason":"visible"}'},
            ]
        )
    )

    assert payload == {"status": "no_action", "reason": "visible"}


def test_visual_judge_keeps_only_visible_analysis(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"not-a-real-image-but-path-exists")

    result = run_visual_judge(
        resolution={
            "visualJudgeSuggestion": {
                "required": True,
                "topCandidates": [{"bbox": [1, 2, 11, 12], "text": "ok", "risk": "low"}],
            }
        },
        current_search_image_path=str(image),
        capture_image_path=str(image),
        capture_bounds=[0, 0, 100, 100],
        available=True,
        invoke=lambda _path, _prompt: '<think>{"decision":"candidate","selectedIndex":0}</think>{"decision":"no_click","confidence":"low","reason":"visible"}',
    )

    judge = result.get("visualJudge") or {}
    assert judge.get("decision") == "no_click"
    assert "candidate" not in str(judge.get("analysis") or "")


def test_rpa_llm_call_response_text_strips_think_tags() -> None:
    text = V8ChatRPAKeywords()._model_response_text(  # noqa: SLF001
        _response('<think>hidden RPA chain</think>\nvisible workflow text')
    )

    assert text == "visible workflow text"
    assert "hidden RPA chain" not in text
