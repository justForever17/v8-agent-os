from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core import native_tools  # noqa: E402


def _parse_session_payload(raw: str) -> dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("kind") != "command_session":
        raise RuntimeError(f"unexpected session payload: {raw}")
    return payload


def _safe_terminate(command_id: str) -> None:
    try:
        native_tools.terminate_background_command.func(command_id=command_id)
    except Exception:
        pass


def run_sync_smoke() -> dict[str, Any]:
    output = native_tools.execute_system_command.func(
        command='python -c "print(12345)"',
        tool_call_id="smoke-sync",
    )
    return {
        "name": "sync",
        "passed": "12345" in str(output),
        "outputPreview": str(output).strip()[:240],
    }


def run_repl_smoke() -> dict[str, Any]:
    payload = _parse_session_payload(
        native_tools.run_system_command.func(
            command="python -q",
            mode="session",
            tool_call_id="smoke-repl",
        )
    )
    command_id = str(payload["commandId"])
    try:
        native_tools.read_background_output.func(command_id=command_id)
        send_result = native_tools.send_background_input.func(
            command_id=command_id,
            input_text="print(2+3)\n",
        )
        time.sleep(0.4)
        read_result = native_tools.read_background_output.func(command_id=command_id)
        combined = f"{send_result}\n\n{read_result}"
        passed = "5" in combined
        return {
            "name": "interactive_repl",
            "passed": passed,
            "outputPreview": combined[:400],
        }
    finally:
        _safe_terminate(command_id)


def run_chat_cli_profile_smoke() -> dict[str, Any]:
    command = (
        'python -u -c "import sys,time;'
        'time.sleep(1.2);'
        'parts=[\'你好！\',\'你好！我是***\',\'你好！我是***，我能为你做什么吗？\'];'
        '[(print(part), sys.stdout.flush(), time.sleep(0.25)) for part in parts]"'
    )
    payload = _parse_session_payload(
        native_tools.run_system_command.func(
            command=command,
            mode="session",
            profile="chat_cli",
            tool_call_id="smoke-chat-cli",
        )
    )
    command_id = str(payload["commandId"])
    try:
        outputs: list[str] = []
        for _ in range(7):
            time.sleep(0.35)
            outputs.append(native_tools.read_background_output.func(command_id=command_id))
        combined = "\n\n".join(outputs)
        passed = (
            "CLI 新增回复" in combined
            and "我能为你做什么吗？" in combined
            and combined.count("CLI 新增回复") <= 3
        )
        return {
            "name": "chat_cli_profile",
            "passed": passed,
            "outputPreview": combined[:1400],
        }
    finally:
        _safe_terminate(command_id)


def _simulate_chat_cli_turn_chunks(variant: str, snapshots: list[str]) -> tuple[list[str], str]:
    merged_text = ""
    chunks: list[str] = []
    for snapshot in snapshots:
        semantic_view = native_tools._strip_chat_cli_prompt_tail(
            native_tools._collapse_chat_cli_cumulative_lines(snapshot, variant=variant)
        )
        if not semantic_view:
            continue
        delta_text = native_tools._consume_chat_cli_semantic_suffix(merged_text, semantic_view)
        merged_text = native_tools._merge_chat_cli_turn_text(merged_text, semantic_view)
        delta_text = native_tools._normalize_chat_cli_text(delta_text)
        if delta_text:
            chunks.append(native_tools._slice_chat_cli_delta_chunk(delta_text))
    return chunks, merged_text


def run_chat_cli_variant_fixture_smoke(
    variant: str,
    fixture_lines: list[str],
    *,
    expected_text: str,
    forbidden_fragments: list[str],
) -> dict[str, Any]:
    cumulative_snapshots = ["\n".join(fixture_lines[: index + 1]) for index in range(len(fixture_lines))]
    outputs, final_text = _simulate_chat_cli_turn_chunks(variant, cumulative_snapshots)
    combined = "\n\n".join(outputs)
    passed = (
        expected_text in final_text
        and not any(fragment in combined for fragment in forbidden_fragments)
        and combined.count(expected_text) <= 1
    )
    return {
        "name": f"chat_cli_fixture:{variant}",
        "passed": passed,
        "outputPreview": combined[:1600],
        "finalSemanticText": final_text[:800],
    }


def run_chat_cli_multi_turn_fixture_smoke() -> dict[str, Any]:
    turn_1_snapshots = [
        "你好！",
        "你好！我是 Qwen。",
        "你好！我是 Qwen。我能帮你做什么？",
    ]
    turn_2_snapshots = [
        "收到，我来继续。",
        "收到，我来继续。下面是第二轮的新回复。",
    ]
    turn_1_chunks, turn_1_final = _simulate_chat_cli_turn_chunks("qwen", turn_1_snapshots)
    turn_2_chunks, turn_2_final = _simulate_chat_cli_turn_chunks("qwen", turn_2_snapshots)
    combined = "\n\n".join(turn_1_chunks + ["---TURN---"] + turn_2_chunks)
    passed = (
        "你好！我是 Qwen。我能帮你做什么？" in turn_1_final
        and "下面是第二轮的新回复。" in turn_2_final
        and "你好！我是 Qwen。我能帮你做什么？" not in "\n".join(turn_2_chunks)
    )
    return {
        "name": "chat_cli_fixture:multi_turn",
        "passed": passed,
        "outputPreview": combined[:1600],
        "turn1Final": turn_1_final[:400],
        "turn2Final": turn_2_final[:400],
    }


def _wait_for_prompt(command_id: str, timeout_seconds: float = 12.0) -> tuple[bool, list[str]]:
    deadline = time.time() + timeout_seconds
    transcripts: list[str] = []
    while time.time() < deadline:
        result = native_tools.read_background_output.func(command_id=command_id)
        transcripts.append(result)
        lowered = result.lower()
        if '"awaiting_input": true' in lowered or "prompt 已就绪" in result or "可继续输入" in result:
            return True, transcripts
        time.sleep(0.8)
    return False, transcripts


def run_ai_cli_prompt_smoke(command: str) -> dict[str, Any]:
    if not shutil.which(command):
        return {
            "name": f"ai_cli_prompt:{command}",
            "skipped": True,
            "reason": "executable_not_found",
        }
    payload = _parse_session_payload(
        native_tools.run_system_command.func(
            command=command,
            mode="session",
            profile="chat_cli",
            tool_call_id=f"smoke-{command}",
        )
    )
    command_id = str(payload["commandId"])
    transcripts: list[str] = []
    try:
        prompt_ready, prompt_reads = _wait_for_prompt(command_id)
        transcripts.extend(prompt_reads)
        combined = "\n\n".join(transcripts)
        return {
            "name": f"ai_cli_prompt:{command}",
            "passed": prompt_ready,
            "promptReady": prompt_ready,
            "outputPreview": combined[:1200],
        }
    finally:
        _safe_terminate(command_id)


def run_qwen_real_chat_cli_smoke() -> dict[str, Any]:
    if not shutil.which("qwen"):
        return {
            "name": "ai_cli_turn:qwen",
            "skipped": True,
            "reason": "executable_not_found",
        }
    payload = _parse_session_payload(
        native_tools.run_system_command.func(
            command="qwen",
            mode="session",
            profile="chat_cli",
            tool_call_id="smoke-qwen-turn",
        )
    )
    command_id = str(payload["commandId"])
    transcripts: list[str] = []
    try:
        prompt_ready, prompt_reads = _wait_for_prompt(command_id)
        transcripts.extend(prompt_reads)
        if prompt_ready:
            transcripts.append(
                native_tools.send_background_input.func(
                    command_id=command_id,
                    input_text="a\n",
                )
            )
            for _ in range(4):
                time.sleep(1.0)
                transcripts.append(native_tools.read_background_output.func(command_id=command_id))
            transcripts.append(
                native_tools.send_background_input.func(
                    command_id=command_id,
                    input_text="请只回复一句话：你好，不要使用工具。\n",
                )
            )
            for _ in range(8):
                time.sleep(1.0)
                transcripts.append(native_tools.read_background_output.func(command_id=command_id))
        combined = "\n\n".join(transcripts)
        semantic_blocks: list[str] = []
        marker = "CLI 新增回复（turn "
        for item in transcripts:
            if marker not in item:
                continue
            _, _, remainder = item.partition(marker)
            _, _, after_header = remainder.partition("）:\n")
            block, _, _ = after_header.partition("\n\nTurn Status:")
            cleaned_block = block.strip()
            if cleaned_block:
                semantic_blocks.append(cleaned_block)
        semantic_text = "\n\n".join(semantic_blocks)
        noise_fragments = [
            "Connecting to MCP servers",
            "Tasting the snozberries",
            "Qwen Code",
            "Switch auth type quickly",
        ]
        passed = prompt_ready and not any(fragment in semantic_text for fragment in noise_fragments)
        return {
            "name": "ai_cli_turn:qwen",
            "passed": passed,
            "promptReady": prompt_ready,
            "hasSemanticDelta": bool(semantic_text),
            "semanticPreview": semantic_text[:600],
            "outputPreview": combined[:2200],
        }
    finally:
        _safe_terminate(command_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime command surface smoke tests")
    parser.add_argument("--with-ai", action="store_true", help="also run qwen/claude prompt-ready smoke")
    args = parser.parse_args()

    results: list[dict[str, Any]] = [
        run_sync_smoke(),
        run_repl_smoke(),
        run_chat_cli_profile_smoke(),
        run_chat_cli_multi_turn_fixture_smoke(),
        run_chat_cli_variant_fixture_smoke(
            "qwen",
            [
                "┌──────────────────────────────────────┐",
                "│ >_ Qwen Code (v0.14.3)              │",
                "│ Qwen OAuth | coder-model            │",
                "└──────────────────────────────────────┘",
                "Tips: Switch auth type quickly with /auth.",
                "⠋ Initializing...",
                "✦ 用户",
                "你好！",
                "你好！我是Qwen。",
                "你好！我是Qwen。我能帮你做什么？",
            ],
            expected_text="你好！我是Qwen。我能帮你做什么？",
            forbidden_fragments=["Qwen Code", "Switch auth type", "Initializing", "Qwen OAuth", "✦ 用户"],
        ),
        run_chat_cli_variant_fixture_smoke(
            "gemini",
            [
                "┌───────────────────────────────┐",
                "│ Gemini CLI                    │",
                "└───────────────────────────────┘",
                "Model: gemini-2.5-pro",
                "⠋ Authorizing... (0s · esc to cancel)",
                "好的。",
                "好的。我是 Gemini。",
            ],
            expected_text="好的。我是 Gemini。",
            forbidden_fragments=["Gemini CLI", "Authorizing", "Model: gemini"],
        ),
        run_chat_cli_variant_fixture_smoke(
            "claude",
            [
                "┌───────────────────────────────┐",
                "│ Claude Code                   │",
                "└───────────────────────────────┘",
                "Model: claude-sonnet",
                "⠋ Working... (0s · esc to cancel)",
                "明白。",
                "明白。我是 Claude。",
            ],
            expected_text="明白。我是 Claude。",
            forbidden_fragments=["Claude Code", "Working", "Model: claude"],
        ),
        run_chat_cli_variant_fixture_smoke(
            "codex",
            [
                "┌───────────────────────────────┐",
                "│ Codex CLI                     │",
                "└───────────────────────────────┘",
                "Model: codex",
                "⠋ Thinking... (0s · esc to cancel)",
                "收到。",
                "收到。我是 Codex。",
            ],
            expected_text="收到。我是 Codex。",
            forbidden_fragments=["Codex CLI", "Thinking", "Model: codex"],
        ),
    ]

    if args.with_ai:
        results.append(run_ai_cli_prompt_smoke("qwen"))
        results.append(run_ai_cli_prompt_smoke("claude"))
        results.append(run_ai_cli_prompt_smoke("gemini"))
        results.append(run_ai_cli_prompt_smoke("codex"))
        results.append(run_qwen_real_chat_cli_smoke())

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    failed = [item for item in results if not item.get("passed", False) and not item.get("skipped", False)]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
