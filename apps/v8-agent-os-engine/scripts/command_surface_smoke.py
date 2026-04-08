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


def run_ai_cli_smoke(command: str) -> dict[str, Any]:
    if not shutil.which(command):
        return {
            "name": f"ai_cli:{command}",
            "skipped": True,
            "reason": "executable_not_found",
        }
    payload = _parse_session_payload(
        native_tools.run_system_command.func(
            command=command,
            mode="session",
            tool_call_id=f"smoke-{command}",
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
                    input_text="hello\n",
                )
            )
            for _ in range(4):
                time.sleep(1.0)
                transcripts.append(native_tools.read_background_output.func(command_id=command_id))
        combined = "\n\n".join(transcripts)
        return {
            "name": f"ai_cli:{command}",
            "passed": prompt_ready,
            "promptReady": prompt_ready,
            "outputPreview": combined[:1200],
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
    ]

    if args.with_ai:
        results.append(run_ai_cli_smoke("qwen"))
        results.append(run_ai_cli_smoke("claude"))

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    failed = [item for item in results if not item.get("passed", False) and not item.get("skipped", False)]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
