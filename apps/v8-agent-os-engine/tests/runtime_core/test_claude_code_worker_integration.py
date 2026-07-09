from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.delegation_broker import (
    default_external_worker_descriptors,
    external_worker_command_profile,
    parse_external_worker_result_block,
    render_external_worker_command,
)
from core.native_tools import (
    _collapse_chat_cli_cumulative_lines,
    _contains_v8_worker_result_marker,
    _detect_background_command_profile,
    _detect_interactive_command,
    _extract_chat_cli_command_head,
    _extract_v8_worker_result_block_text,
)


def test_claude_code_renderer_writes_brief_file_and_uses_short_prompt(tmp_path: Path):
    descriptor = default_external_worker_descriptors()[0]
    task_brief = {
        "taskBriefId": "docs-task",
        "goal": "Write a short handoff document.",
        "writeSet": ["docs/HANDOFF.md"],
        "acceptanceContract": "Document exists and has a concise summary.",
    }

    brief_path = tmp_path / ".v8-agent-os" / "external-workers" / "docs-task" / "task_brief.json"
    project = SimpleNamespace(
        project_id="proj_docs",
        workspace_id="ws_docs",
        workspace_path=str(tmp_path),
        workspace_trust_state="trusted",
        workspace_trust_source="user_confirmed",
    )
    with patch("core.workspace_authority.project_registry_service.get_project", return_value=project):
        command = render_external_worker_command(
            descriptor=descriptor,
            task_brief=task_brief,
            workspace_path=str(tmp_path),
            workspace_id="ws_docs",
            project_id="proj_docs",
        )

    assert brief_path.exists()
    payload = json.loads(brief_path.read_text(encoding="utf-8"))
    assert payload["taskBrief"]["goal"] == task_brief["goal"]
    assert "{task_brief_b64}" not in command
    assert "Decode this taskBrief base64" not in command
    assert "task_brief.json" in command
    assert "--permission-mode" in command
    assert "acceptEdits" in command
    assert external_worker_command_profile(descriptor) == "chat_cli"


def test_claude_code_renderer_blocks_untrusted_workspace_path(tmp_path: Path):
    descriptor = default_external_worker_descriptors()[0]
    command = render_external_worker_command(
        descriptor=descriptor,
        task_brief={
            "taskBriefId": "blocked-task",
            "goal": "Write a short handoff document.",
        },
        workspace_path=str(tmp_path),
    )

    brief_path = tmp_path / ".v8-agent-os" / "external-workers" / "blocked-task" / "task_brief.json"
    assert not brief_path.exists()
    payload = json.loads(command)
    assert payload["kind"] == "workspace_side_effect_blocked"


def test_cd_prefixed_claude_print_is_chat_cli_but_not_interactive():
    command = 'cd /d "E:\\Projects\\v8chat" && claude -p --permission-mode acceptEdits --output-format text "hello"'

    assert _extract_chat_cli_command_head(command) == "claude"
    assert _detect_background_command_profile(command, requested_profile="auto")[0] == "chat_cli"
    assert _detect_interactive_command(command) is None


def test_claude_noise_filter_preserves_worker_result_marker():
    result = {
        "status": "succeeded",
        "summary": "Wrote docs.",
        "changedFiles": ["docs/WORKER.md"],
        "commandsRun": [],
        "verification": "file exists",
        "notes": "",
    }
    noisy = "\n".join(
        [
            "Claude Code (v1.2.3)",
            "Model: Claude",
            "✻ Thinking… press esc to interrupt",
            "⎿ Read docs/WORKER.md",
            f"<V8_WORKER_RESULT>{json.dumps(result)}</V8_WORKER_RESULT>",
            "> type your message",
        ]
    )

    cleaned = _collapse_chat_cli_cumulative_lines(noisy, variant="claude")
    assert "Claude Code" not in cleaned
    assert "Thinking" not in cleaned
    assert _contains_v8_worker_result_marker(cleaned)
    block = _extract_v8_worker_result_block_text(cleaned)
    parsed = parse_external_worker_result_block(block)
    assert parsed is not None
    assert parsed["status"] == "succeeded"
    assert parsed["changedFiles"] == ["docs/WORKER.md"]


def test_missing_worker_result_marker_is_not_accepted():
    assert parse_external_worker_result_block("Done. I wrote the file.") is None


def test_worker_result_parser_recovers_terminal_wrapped_json_strings():
    wrapped = (
        "<V8_WORKER_RESULT>\n"
        '{"status":"success","summary":"Created the req\n'
        'uired document.","changedFiles":["docs/WORKER.md"],"commandsRun":[],"verification":"ok","notes":""}\n'
        "</V8_WORKER_RESULT>"
    )

    parsed = parse_external_worker_result_block(wrapped)
    assert parsed is not None
    assert parsed["status"] == "success"
    assert parsed["summary"] == "Created the required document."


def test_worker_result_parser_recovers_terminal_wrapped_end_marker():
    wrapped = (
        '<V8_WORKER_RESULT>{"status":"success","summary":"ok","changedFiles":[],"commandsRun":[],"verification":"ok","notes":""}</V8_WORKER_RES\n'
        "ULT>"
    )

    parsed = parse_external_worker_result_block(wrapped)
    assert parsed is not None
    assert parsed["status"] == "success"


def test_worker_result_parser_uses_last_marker_pair_not_instruction_example():
    text = (
        "Instruction: print <V8_WORKER_RESULT> JSON object </V8_WORKER_RESULT> block.\n"
        '<V8_WORKER_RESULT>{"status":"success","summary":"actual","changedFiles":[],"commandsRun":[],"verification":"ok","notes":""}</V8_WORKER_RESULT>'
    )

    parsed = parse_external_worker_result_block(text)
    assert parsed is not None
    assert parsed["summary"] == "actual"


def test_worker_result_parser_prefers_wrapped_actual_result_over_prompt_example():
    text = (
        "Prompt says <V8_WORKER_RESULT> JSON object </V8_WORKER_RESULT> block.\n"
        '<V8_WORKER_RESULT>{"status":"success","summary":"actual","changedFiles":["CLAUDE\n'
        '_WORKER_SMOKE.md"],"commandsRun":[],"verification":"ok","notes":""}</V8_WOR\n'
        "KER_RESULT>"
    )

    parsed = parse_external_worker_result_block(text)
    assert parsed is not None
    assert parsed["summary"] == "actual"
    assert parsed["changedFiles"] == ["CLAUDE_WORKER_SMOKE.md"]
