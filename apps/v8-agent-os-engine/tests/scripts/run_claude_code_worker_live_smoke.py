from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.delegation_broker import (  # noqa: E402
    default_external_worker_descriptors,
    external_worker_command_profile,
    parse_external_worker_result_block,
    render_external_worker_command,
)
from core.native_tools import command_session_broker  # noqa: E402


def _load_payload(raw: str) -> dict:
    try:
        parsed = json.loads(str(raw or "{}"))
    except Exception as exc:  # pragma: no cover - diagnostic path
        return {"ok": False, "error": f"invalid_json:{type(exc).__name__}", "raw": str(raw or "")[:500]}
    return parsed if isinstance(parsed, dict) else {"ok": False, "error": "payload_not_object"}


def run_live_smoke(*, timeout_seconds: int) -> int:
    workspace = Path.home() / ".v8-agent-os" / "tmp" / f"claude-worker-smoke-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    workspace.mkdir(parents=True, exist_ok=True)
    descriptor = default_external_worker_descriptors()[0]
    task_brief = {
        "taskBriefId": "claude-worker-smoke",
        "goal": "Create a tiny Markdown smoke-test document named CLAUDE_WORKER_SMOKE.md in this workspace.",
        "context": "This is a V8OS Claude Code Worker live smoke. Keep the file short. Do not read or print secrets.",
        "writeSet": ["CLAUDE_WORKER_SMOKE.md"],
        "behaviorScope": ["workspace_write", "tool_use"],
        "requiredCapabilities": ["documentation", "file_write"],
        "acceptanceContract": "CLAUDE_WORKER_SMOKE.md exists and contains the phrase V8 Claude Code Worker smoke.",
    }
    command = render_external_worker_command(
        descriptor=descriptor,
        task_brief=task_brief,
        workspace_path=str(workspace),
    )
    start_payload = _load_payload(
        command_session_broker.func(
            mode="start",
            command=command,
            profile=external_worker_command_profile(descriptor),
            tool_call_id="claude-worker-live-smoke",
        )
    )
    command_id = str(start_payload.get("commandId") or start_payload.get("sessionId") or "")
    result_text = str(start_payload.get("workerResultBlock") or start_payload.get("semanticTextTail") or start_payload.get("initialPreview") or "")
    deadline = time.time() + timeout_seconds
    last_payload = start_payload
    while command_id and time.time() < deadline:
        parsed = parse_external_worker_result_block(result_text)
        if parsed:
            output_file = workspace / "CLAUDE_WORKER_SMOKE.md"
            ok = output_file.exists() and "V8 Claude Code Worker smoke" in output_file.read_text(encoding="utf-8", errors="ignore")
            print(json.dumps({
                "ok": bool(ok),
                "workspace": str(workspace),
                "commandId": command_id,
                "workerResult": parsed,
                "outputFileExists": output_file.exists(),
            }, ensure_ascii=False, indent=2))
            return 0 if ok else 2
        time.sleep(2.0)
        last_payload = _load_payload(
            command_session_broker.func(
                mode="observe",
                session_id=command_id,
                profile=external_worker_command_profile(descriptor),
                tool_call_id="claude-worker-live-smoke",
            )
        )
        result_text = "\n".join(
            part
            for part in [
                str(last_payload.get("workerResultBlock") or ""),
                str(last_payload.get("semanticTextTail") or ""),
                str(last_payload.get("deltaText") or ""),
            ]
            if part
        )
        if str(last_payload.get("state") or "") in {"completed", "failed"} and not result_text:
            break

    print(json.dumps({
        "ok": False,
        "workspace": str(workspace),
        "commandId": command_id,
        "error": "marker_not_found_or_timeout",
        "lastPayload": {key: last_payload.get(key) for key in ("state", "summary", "recommendedNextAction", "workerResultDetected", "semanticTextTail")},
    }, ensure_ascii=False, indent=2))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live Claude Code Worker smoke test.")
    parser.add_argument("--live-claude", action="store_true", help="Actually invoke the local claude CLI.")
    parser.add_argument("--allow-workspace-write", action="store_true", help="Allow the smoke to write a temp workspace document.")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    if not args.live_claude or not args.allow_workspace_write:
        print(json.dumps({
            "ok": True,
            "dryRun": True,
            "message": "Pass --live-claude --allow-workspace-write to invoke the real claude CLI.",
        }, ensure_ascii=False, indent=2))
        return 0
    return run_live_smoke(timeout_seconds=max(30, min(int(args.timeout_seconds), 600)))


if __name__ == "__main__":
    raise SystemExit(main())
