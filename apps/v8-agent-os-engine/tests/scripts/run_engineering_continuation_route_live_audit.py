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
from run_supervisor_runtime_skill_live_audit import (  # noqa: E402
    DEFAULT_ENGINE_URL,
    _engine_api_base,
    _json_request,
    _wait_for_engine,
)


def _submit(
    *,
    engine_url: str,
    session_id: str,
    workspace: Path,
    client_message_id: str,
) -> str:
    response = _json_request(
        f"{_engine_api_base(engine_url)}/chat/submit",
        method="POST",
        payload={
            "session_id": session_id,
            "conversationId": session_id,
            "clientMessageId": client_message_id,
            "stream": False,
            "workspacePath": str(workspace),
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "刚才的工程任务还是不行。运行日志：Traceback (most recent call last): "
                        "NameError: name 'continuation_value' is not defined。"
                        "请续接同一会话和工作区的上一轮 Engineering 上下文修复，"
                        "不要改用 Supervisor 本地工具，也不要只给诊断说明。"
                    ),
                }
            ],
            "data": {
                "conversationId": session_id,
                "clientMessageId": client_message_id,
                "engineeringContinuationRouteLiveAudit": True,
            },
        },
        timeout=30,
    )
    return str(response.get("run_id") or response.get("runId") or "").strip()


def _cancel_run(engine_url: str, run_id: str) -> None:
    if not run_id:
        return
    try:
        _json_request(
            f"{_engine_api_base(engine_url)}/runs/{run_id}/commands/cancel",
            method="POST",
            payload={"reason": "engineering_continuation_route_live_audit_complete"},
            timeout=15,
        )
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that a same-session Engineering continuation creates a fresh runtime episode."
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--workspace", required=True)
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
    session_id = f"engineering-continuation-route-live-{timestamp}"
    seed_episode_id = f"episode_seed_engineering_{timestamp}"
    seed_run_id = f"run_seed_engineering_{timestamp}"
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "continuation-route-live" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("continuation_value = 1\n", encoding="utf-8")

    db.create_or_update_session(
        session_id=session_id,
        title="Engineering continuation route live audit",
        user_id="live-audit",
        metadata={
            "workspacePath": str(workspace),
            "workspace_path": str(workspace),
            "hiddenFromHistory": True,
            "source": "live_audit",
        },
    )
    db.create_run_record(
        seed_run_id,
        session_id,
        user_id="live-audit",
        run_type="chat",
        status="completed",
        trigger_source="live_audit_seed",
        agent_id="supervisor",
        metadata={"seed": True, "hiddenFromHistory": True},
    )
    db.upsert_runtime_episode_record(
        {
            "episodeId": seed_episode_id,
            "kind": "engineering",
            "state": "completed",
            "source": "live_audit_seed",
            "reason": "Seed prior Engineering evidence for same-session continuation routing.",
            "inputs": {
                "workspacePath": str(workspace),
                "writeSet": ["continuation-route-live/app.py"],
            },
            "resultRef": f"live-audit:{seed_episode_id}",
            "metadata": {"seed": True, "hiddenFromHistory": True},
        },
        session_id=session_id,
        run_id=seed_run_id,
    )

    client_message_id = f"continuation-route-{timestamp}"
    run_id = _submit(
        engine_url=args.engine_url,
        session_id=session_id,
        workspace=workspace,
        client_message_id=client_message_id,
    )
    deadline = time.monotonic() + max(30, int(args.max_wait or 240))
    fresh_episode: dict[str, Any] | None = None
    run: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        episodes = db.list_runtime_episodes(session_id=session_id, limit=20)
        fresh_episode = next(
            (
                item
                for item in episodes
                if str(item.get("id") or item.get("episodeId") or "") != seed_episode_id
                and str(item.get("kind") or "").strip().lower() == "engineering"
                and str(item.get("run_id") or item.get("runId") or "") == run_id
            ),
            None,
        )
        run = db.get_run_record(run_id) if run_id else None
        if fresh_episode:
            break
        if str((run or {}).get("status") or "").strip().lower() in {
            "completed",
            "failed",
            "cancelled",
            "interrupted",
            "waiting_input",
            "waiting_approval",
        }:
            break
        time.sleep(1)

    metadata = dict((run or {}).get("metadata") or {})
    task_shape = metadata.get("taskShapeHint") if isinstance(metadata.get("taskShapeHint"), dict) else {}
    continuation = task_shape.get("engineeringContinuation") if isinstance(task_shape.get("engineeringContinuation"), dict) else {}
    result = {
        "status": "ok" if fresh_episode and bool(continuation.get("active")) else "failed",
        "sessionId": session_id,
        "runId": run_id,
        "runStatus": (run or {}).get("status"),
        "seedEpisodeId": seed_episode_id,
        "freshEpisodeId": (fresh_episode or {}).get("id") or (fresh_episode or {}).get("episodeId"),
        "freshEpisodeState": (fresh_episode or {}).get("state"),
        "engineeringContinuationActive": bool(continuation.get("active")),
        "engineeringRequired": metadata.get("engineeringRequired"),
        "continuationReason": continuation.get("reason"),
        "workspace": str(workspace),
    }
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    _cancel_run(args.engine_url, run_id)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
