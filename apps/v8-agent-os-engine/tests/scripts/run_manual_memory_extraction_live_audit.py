from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.database import db  # noqa: E402
from core.memory_extraction_policy import memory_extraction_runtime_session_id  # noqa: E402
from core.storage import storage  # noqa: E402
from core.terminal_post_run import TerminalPostRunService  # noqa: E402
from run_supervisor_runtime_skill_live_audit import (  # noqa: E402
    DEFAULT_ENGINE_URL,
    _engine_api_base,
    _json_request,
    _wait_for_engine,
)


def _memory_runs(session_id: str) -> list[dict[str, Any]]:
    return db.list_run_records(
        session_id=memory_extraction_runtime_session_id(session_id),
        run_type="memory",
        limit=20,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify manual Memory extraction mode against a live Engine and real Memory Agent."
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--max-wait", type=int, default=240)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if not args.live:
        print("Refusing to call live Engine without --live.")
        return 2

    ok, error = _wait_for_engine(args.engine_url, timeout=20)
    if not ok:
        print(f"Engine is unavailable: {error}")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = f"manual-memory-live-{timestamp}"
    terminal_run_id = f"run_manual_memory_seed_{timestamp}"
    user_id = "live-audit"
    original_config = dict(storage.get_memory_config() or {})
    result: dict[str, Any] = {
        "status": "failed",
        "sessionId": session_id,
        "terminalRunId": terminal_run_id,
    }
    try:
        _json_request(
            f"{_engine_api_base(args.engine_url)}/memory/config",
            method="POST",
            payload={"extraction_mode": "manual"},
            timeout=30,
        )
        db.create_or_update_session(
            session_id=session_id,
            title="Manual memory live audit",
            user_id=user_id,
            metadata={"hiddenFromHistory": True, "source": "live_audit"},
        )
        db.add_message(
            f"message_user_{timestamp}",
            session_id,
            "user",
            (
                "这是 V8OS 手动记忆入口的临时验收消息，不代表任何长期偏好、事实或项目知识。"
                "请勿把本句持久化为长期记忆。"
            ),
            metadata={"source": "live_audit", "runId": terminal_run_id},
        )
        db.add_message(
            f"message_assistant_{timestamp}",
            session_id,
            "assistant",
            "已确认，本轮仅用于验证手动触发链路。",
            metadata={"source": "live_audit", "runId": terminal_run_id},
        )
        db.create_run_record(
            terminal_run_id,
            session_id,
            user_id=user_id,
            run_type="chat",
            status="completed",
            trigger_source="live_audit_seed",
            agent_id="supervisor",
            metadata={"hiddenFromHistory": True, "source": "live_audit"},
        )

        baseline_run_ids = {str(item.get("id") or "") for item in _memory_runs(session_id)}
        TerminalPostRunService()._schedule_memory_extraction(
            session_id=session_id,
            run_id=terminal_run_id,
            source_component="manual_memory_live_audit",
        )
        time.sleep(1.5)
        automatic_run_ids = {str(item.get("id") or "") for item in _memory_runs(session_id)}
        automatic_delta = sorted(automatic_run_ids - baseline_run_ids)

        response = _json_request(
            f"{_engine_api_base(args.engine_url)}/memory/session-extraction",
            method="POST",
            payload={"sessionId": session_id, "userId": user_id},
            timeout=30,
        )
        deadline = time.monotonic() + max(30, int(args.max_wait or 240))
        manual_run: dict[str, Any] | None = None
        terminal_event: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            runs = [
                item
                for item in _memory_runs(session_id)
                if str(item.get("id") or "") not in baseline_run_ids
            ]
            if runs:
                manual_run = runs[0]
            for event in reversed(db.get_runtime_events(session_id)):
                topic = str(event.get("topic") or "")
                if topic in {
                    "memory.extraction.manual.completed",
                    "memory.extraction.manual.failed",
                }:
                    terminal_event = event
                    break
            if manual_run and terminal_event:
                break
            time.sleep(1)

        run_metadata = dict((manual_run or {}).get("metadata") or {})
        memory_result = (
            run_metadata.get("memory_result")
            if isinstance(run_metadata.get("memory_result"), dict)
            else {}
        )
        event_payload = (
            terminal_event.get("payload")
            if terminal_event and isinstance(terminal_event.get("payload"), dict)
            else {}
        )
        manual_reason = str(memory_result.get("reason") or "")
        result.update(
            {
                "status": (
                    "ok"
                    if not automatic_delta
                    and bool(response.get("accepted"))
                    and manual_run is not None
                    and str((manual_run or {}).get("status") or "") in {"completed", "failed"}
                    and terminal_event is not None
                    and str((terminal_event or {}).get("topic") or "") == "memory.extraction.manual.completed"
                    and manual_reason not in {"manual_mode", "extraction_disabled"}
                    else "failed"
                ),
                "automaticRunDelta": automatic_delta,
                "manualAccepted": response.get("accepted"),
                "manualRequestId": response.get("requestId"),
                "manualRunId": (manual_run or {}).get("id"),
                "manualRunStatus": (manual_run or {}).get("status"),
                "manualResultStatus": memory_result.get("status"),
                "manualResultReason": manual_reason or None,
                "terminalTopic": (terminal_event or {}).get("topic"),
                "terminalSummary": event_payload.get("summary"),
                "preferenceCount": event_payload.get("preferenceCount"),
                "knowledgeCount": event_payload.get("knowledgeCount"),
            }
        )
    finally:
        storage.save_memory_config(original_config)

    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
