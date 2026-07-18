from __future__ import annotations

import argparse
import json
import subprocess
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
    target_relative: str,
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
                        "刚才的工程任务还是不行。请续接同一会话和工作区的上一轮 Engineering 上下文，"
                        f"修复 {target_relative} 的运行错误："
                        "NameError: name 'continuation_value' is not defined。"
                        "只允许修改这个文件；修复后执行它，必须输出 continuation-ok。"
                        "必须由 Engineering episode 内部委派真实 worker 完成，不要改用 Supervisor 本地工具，"
                        "也不要只给诊断说明。"
                    ),
                }
            ],
            "data": {
                "conversationId": session_id,
                "clientMessageId": client_message_id,
                "engineeringContinuationRouteLiveAudit": True,
                "safetyApprovalMode": "reduced",
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


def _runtime_route_attempt_metrics(*, run_id: str, session_id: str) -> dict[str, Any]:
    events = db.get_runtime_events_for_run(run_id, session_id=session_id, limit=1000) if run_id else []
    starts: list[dict[str, Any]] = []
    finishes: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else {}
        if str(tool.get("toolName") or "").strip() != "runtime_broker":
            continue
        call_id = str(tool.get("toolCallId") or tool.get("toolInvocationId") or "").strip()
        topic = str(event.get("topic") or "").strip()
        if topic == "tool.started":
            args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
            if str(args.get("mode") or "").strip().lower() == "route":
                starts.append({"callId": call_id, "args": args, "seq": event.get("seq")})
        elif topic == "tool.finished" and call_id:
            finishes[call_id] = tool

    accepted_call_ids: set[str] = set()
    parameter_repair_call_ids: set[str] = set()
    for call_id, tool in finishes.items():
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
        summary = result.get("summary")
        decoded: dict[str, Any] = {}
        if isinstance(summary, dict):
            decoded = summary
        elif isinstance(summary, str):
            try:
                parsed = json.loads(summary)
                decoded = parsed if isinstance(parsed, dict) else {}
            except Exception:
                decoded = {}
        visible = str(tool.get("agentVisibleResult") or "")
        if decoded.get("ok") is True:
            accepted_call_ids.add(call_id)
        if (
            str(decoded.get("error") or "").strip() in {"typed_need_required", "typed_need_invalid"}
            or "parameter-shape error" in visible
            or "runtime_route_parameter_repair" in visible
        ):
            parameter_repair_call_ids.add(call_id)

    first = starts[0] if starts else {}
    first_args = first.get("args") if isinstance(first.get("args"), dict) else {}
    first_need = first_args.get("need") if isinstance(first_args.get("need"), dict) else {}
    first_inputs = first_need.get("inputs") if isinstance(first_need.get("inputs"), dict) else {}
    first_tasks = first_inputs.get("taskBriefs") if isinstance(first_inputs.get("taskBriefs"), list) else []
    canonical_array = bool(first_tasks) and not any(alias in first_inputs for alias in ("workerBriefs", "tasks"))
    typed_arrays = bool(first_tasks)
    for brief in first_tasks:
        if not isinstance(brief, dict):
            typed_arrays = False
            break
        for key in ("writeSet", "expectedOutputs", "constraints", "detailRefs", "dependencies"):
            if key in brief and not isinstance(brief.get(key), list):
                typed_arrays = False
        if "dependency" in brief:
            typed_arrays = False
        if not str(brief.get("taskBriefId") or "").strip() or not str(brief.get("goal") or "").strip():
            typed_arrays = False
    if "proofExpectations" in first_inputs and not isinstance(first_inputs.get("proofExpectations"), list):
        typed_arrays = False
    first_call_id = str(first.get("callId") or "")
    accepted_count = len([item for item in starts if str(item.get("callId") or "") in accepted_call_ids])
    return {
        "runtimeBrokerRouteAttemptCount": len(starts),
        "runtimeBrokerRouteAcceptedCount": accepted_count,
        "runtimeBrokerRouteRejectedCount": max(0, len(starts) - accepted_count),
        "runtimeBrokerParameterRepairCount": len(parameter_repair_call_ids),
        "firstRuntimeRouteAccepted": bool(first_call_id and first_call_id in accepted_call_ids),
        "firstRuntimeRouteCanonicalTaskArray": canonical_array,
        "firstRuntimeRouteArrayTypesValid": typed_arrays,
        "firstRuntimeRouteToolCallId": first_call_id,
    }


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
    target_relative_path = Path(".v8") / "live-audit" / "engineering-continuation-route-live" / timestamp / "app.py"
    target_relative = target_relative_path.as_posix()
    target = workspace / target_relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("print(continuation_value)\n", encoding="utf-8")

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
                "writeSet": [target_relative],
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
        target_relative=target_relative,
        client_message_id=client_message_id,
    )
    deadline = time.monotonic() + max(30, int(args.max_wait or 240))
    fresh_episodes: list[dict[str, Any]] = []
    fresh_episode: dict[str, Any] | None = None
    run: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        episodes = db.list_runtime_episodes(session_id=session_id, limit=20)
        fresh_episodes = [
            item
            for item in episodes
            if str(item.get("id") or item.get("episodeId") or "") != seed_episode_id
            and str(item.get("kind") or "").strip().lower() == "engineering"
            and str(item.get("run_id") or item.get("runId") or "") == run_id
        ]
        # Database ordering is newest update first; follow the latest retry if
        # Supervisor legitimately creates a corrected Engineering episode.
        fresh_episode = fresh_episodes[0] if fresh_episodes else None
        run = db.get_run_record(run_id) if run_id else None
        run_status = str((run or {}).get("status") or "").strip().lower()
        episode_state = str((fresh_episode or {}).get("state") or "").strip().lower()
        if run_status in {
            "completed",
            "failed",
            "cancelled",
            "interrupted",
            "waiting_input",
            "waiting_approval",
        } and (not fresh_episode or episode_state in {"completed", "degraded", "failed", "cancelled", "merged"}):
            break
        time.sleep(1)

    metadata = dict((run or {}).get("metadata") or {})
    task_shape = metadata.get("taskShapeHint") if isinstance(metadata.get("taskShapeHint"), dict) else {}
    continuation = task_shape.get("engineeringContinuation") if isinstance(task_shape.get("engineeringContinuation"), dict) else {}
    fresh_episode_id = str((fresh_episode or {}).get("id") or (fresh_episode or {}).get("episodeId") or "").strip()
    delegation_episodes = [
        item
        for item in db.list_runtime_episodes(run_id=run_id, limit=100)
        if str(item.get("kind") or "").strip().lower() == "delegation"
    ] if run_id else []
    engineering_episode_ids = {
        str(item.get("id") or item.get("episodeId") or "").strip()
        for item in fresh_episodes
        if str(item.get("id") or item.get("episodeId") or "").strip()
    }
    delegation_episode_ids = {
        str(item.get("id") or item.get("episodeId") or "").strip()
        for item in delegation_episodes
        if str(item.get("id") or item.get("episodeId") or "").strip()
    }
    delegation_parent_ids = [
        str(item.get("parentEpisodeId") or item.get("parent_episode_id") or "").strip()
        for item in delegation_episodes
    ]
    delegation_lineage_valid = bool(delegation_episodes) and all(
        parent_id and parent_id in engineering_episode_ids.union(delegation_episode_ids)
        for parent_id in delegation_parent_ids
    )
    direct_delegation_episodes = [
        item
        for item in delegation_episodes
        if str(item.get("parentEpisodeId") or item.get("parent_episode_id") or "").strip() == fresh_episode_id
    ]
    engineering_handoffs = db.list_runtime_episode_handoffs(fresh_episode_id) if fresh_episode_id else []
    delegation_handoffs = [
        handoff
        for child in delegation_episodes
        for handoff in db.list_runtime_episode_handoffs(str(child.get("id") or child.get("episodeId") or ""))
    ]
    durable_text = json.dumps(
        {
            "engineeringEpisodes": fresh_episodes,
            "delegationEpisodes": delegation_episodes,
            "engineeringHandoffs": engineering_handoffs,
            "delegationHandoffs": delegation_handoffs,
        },
        ensure_ascii=False,
        default=str,
    )
    depth_terminal_observed = "delegation_depth_terminal" in durable_text
    execution = subprocess.run(
        [sys.executable, str(target)],
        cwd=str(target.parent),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    target_output = str(execution.stdout or "").strip()
    engineering_ready = any(
        str((item.get("payload") or item).get("status") or item.get("status") or "").strip().lower() == "ready"
        for item in engineering_handoffs
        if isinstance(item, dict)
    )
    delegation_ready = any(
        str(child.get("state") or "").strip().lower() in {"completed", "merged"}
        for child in direct_delegation_episodes
        if str(child.get("kind") or "").strip().lower() == "delegation"
    )
    run_status = str((run or {}).get("status") or "").strip().lower()
    fresh_episode_state = str((fresh_episode or {}).get("state") or "").strip().lower()
    route_metrics = _runtime_route_attempt_metrics(run_id=run_id, session_id=session_id)
    passed = bool(
        fresh_episode
        and continuation.get("active")
        and run_status == "completed"
        and fresh_episode_state in {"completed", "merged"}
        and engineering_ready
        and delegation_ready
        and delegation_lineage_valid
        and not depth_terminal_observed
        and execution.returncode == 0
        and target_output == "continuation-ok"
        and route_metrics["runtimeBrokerRouteAttemptCount"] == 1
        and route_metrics["runtimeBrokerRouteRejectedCount"] == 0
        and route_metrics["runtimeBrokerParameterRepairCount"] == 0
        and route_metrics["firstRuntimeRouteAccepted"]
        and route_metrics["firstRuntimeRouteCanonicalTaskArray"]
        and route_metrics["firstRuntimeRouteArrayTypesValid"]
    )
    result = {
        "status": "ok" if passed else "failed",
        "sessionId": session_id,
        "runId": run_id,
        "runStatus": run_status,
        "seedEpisodeId": seed_episode_id,
        "freshEpisodeId": fresh_episode_id,
        "freshEpisodeState": fresh_episode_state,
        "freshEngineeringEpisodeCount": len(fresh_episodes),
        "delegationEpisodeIds": [
            str(item.get("id") or item.get("episodeId") or "")
            for item in delegation_episodes
            if str(item.get("kind") or "").strip().lower() == "delegation"
        ],
        "delegationEpisodeStates": [
            str(item.get("state") or "")
            for item in delegation_episodes
            if str(item.get("kind") or "").strip().lower() == "delegation"
        ],
        "delegationParentEpisodeIds": delegation_parent_ids,
        "delegationLineageValid": delegation_lineage_valid,
        "engineeringHandoffReady": engineering_ready,
        "delegationHandoffReady": delegation_ready,
        "delegationDepthTerminalObserved": depth_terminal_observed,
        "engineeringContinuationActive": bool(continuation.get("active")),
        "engineeringRequired": metadata.get("engineeringRequired"),
        "continuationReason": continuation.get("reason"),
        "targetReturnCode": execution.returncode,
        "targetStdout": target_output,
        "targetStderr": str(execution.stderr or "").strip(),
        "targetRelativePath": target_relative,
        "workspace": str(workspace),
        **route_metrics,
    }
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if run_status not in {"completed", "failed", "cancelled", "interrupted"}:
        _cancel_run(args.engine_url, run_id)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
