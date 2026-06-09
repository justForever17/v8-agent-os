from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[2]
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
DEFAULT_WORKSPACE = Path("E:/Projects/test3")
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))
TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "canceled"}
ACTIVE_QUEUE_STATES = ["pending", "promoted", "queued"]

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


@dataclass
class PlannerFirstResponseResult:
    status: str = "pending"
    session_id: str = ""
    run_id: str = ""
    submit_latency_ms: int | None = None
    first_visible_ms: int | None = None
    first_visible_kind: str = ""
    first_visible_preview: str = ""
    first_activity_ms: int | None = None
    first_activity_kind: str = ""
    first_activity_preview: str = ""
    execution_map_before_activity: bool = False
    observed_topics: list[str] = field(default_factory=list)
    planner_events: list[str] = field(default_factory=list)
    findings: list[dict[str, str]] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)
    final_idle_state: dict[str, Any] = field(default_factory=dict)


def _json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def _engine_api_base(engine_url: str) -> str:
    base = engine_url.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def _engine_root_url(engine_url: str) -> str:
    base = engine_url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _wait_for_engine(engine_url: str, timeout_s: float = 20.0) -> tuple[bool, str]:
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            _json_request(f"{_engine_root_url(engine_url)}/health", timeout=3)
            return True, ""
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.75)
    return False, last_error


def _submit(engine_url: str, *, session_id: str, workspace: Path, model_profile: str, prompt: str, tag: str) -> tuple[str, int, dict[str, Any]]:
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": tag,
        "stream": False,
        "workspacePath": str(workspace),
        "messages": [{"role": "user", "content": prompt}],
        "data": {
            "conversationId": session_id,
            "clientMessageId": tag,
            "modelProfile": model_profile,
            "taskPlanningMode": True,
            "plannerMode": "force",
            "plannerDispatchMode": "suggest",
            "specMode": False,
        },
    }
    started = time.perf_counter()
    response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    latency_ms = int((time.perf_counter() - started) * 1000)
    run_id = str(response.get("run_id") or response.get("runId") or "")
    return run_id, latency_ms, response


def _canonical_messages(session_id: str) -> list[dict[str, Any]]:
    try:
        from core.database import db
    except Exception:
        return []
    try:
        return list(db.get_chat_canonical_messages(session_id))
    except Exception:
        return []


def _runtime_events(session_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    try:
        from core.database import db
    except Exception:
        return []
    try:
        return list(db.get_runtime_events(session_id, after_seq=after_seq))
    except Exception:
        return []


def _message_text(message: dict[str, Any]) -> str:
    for key in ("content_text", "contentText", "content", "text"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _message_nodes(message: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = message.get("nodes")
    if isinstance(nodes, list):
        return [item for item in nodes if isinstance(item, dict)]
    raw = message.get("nodes_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _assistant_visible_activity(messages: list[dict[str, Any]]) -> tuple[str, str] | None:
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        if role not in {"assistant", "ai", "supervisor"}:
            continue
        text = _message_text(message)
        if text:
            return "text", text[:300]
        nodes = _message_nodes(message)
        if nodes:
            node = nodes[0]
            return str(node.get("kind") or node.get("type") or "node"), json.dumps(node, ensure_ascii=False)[:300]
        metadata = message.get("metadata") or message.get("metadata_json")
        if metadata:
            return "metadata", str(metadata)[:300]
    return None


def _is_meaningful_node(node: dict[str, Any]) -> bool:
    execution_type = str(node.get("executionType") or node.get("type") or node.get("kind") or "").strip()
    if execution_type in {"agent_start", "execution_projection", "runtime_projection", "stage_status"}:
        return False
    if execution_type in {"reasoning", "tool_call", "tool_result", "runtime_broker", "todo", "todos", "text"}:
        return True
    content = str(node.get("content") or node.get("summary") or node.get("message") or "").strip()
    return bool(content and execution_type not in {"agent_start"})


def _assistant_meaningful_activity(messages: list[dict[str, Any]]) -> tuple[str, str] | None:
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        if role not in {"assistant", "ai", "supervisor"}:
            continue
        text = _message_text(message)
        if text:
            return "text", text[:300]
        for node in _message_nodes(message):
            if _is_meaningful_node(node):
                return str(node.get("executionType") or node.get("kind") or node.get("type") or "node"), json.dumps(node, ensure_ascii=False)[:300]
    return None


def _runtime_event_meaningful_activity(event: dict[str, Any]) -> tuple[str, str] | None:
    topic = str(event.get("topic") or event.get("event_type") or event.get("type") or "").strip()
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raw = event.get("payload_json")
        if isinstance(raw, str) and raw.strip():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {}
        else:
            payload = {}
    if topic == "run.reasoning.delta":
        return "reasoning", str(payload.get("content") or payload.get("snapshot") or "")[:300]
    if topic == "extension.execution.completed" and (payload.get("hasToolCalls") or payload.get("messagePreview")):
        preview = payload.get("messagePreview") or ",".join(str(item) for item in payload.get("toolNames") or [])
        return "tool_activity", str(preview)[:300]
    if topic in {"todo.updated", "todos.updated", "todo.created", "runtime_broker.called"}:
        return topic, json.dumps(payload, ensure_ascii=False)[:300]
    return None


def _session_idle_state(session_id: str) -> tuple[bool, dict[str, Any]]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}
    try:
        runs = db.list_run_records(session_id=session_id, run_type="chat", limit=20)
        active_runs = [
            {"id": item.get("id"), "status": item.get("status")}
            for item in runs
            if str(item.get("status") or "").strip().lower() not in TERMINAL_RUN_STATES
        ]
        queue_items = db.list_chat_user_message_queue(session_id=session_id, states=ACTIVE_QUEUE_STATES, limit=20)
        active_queue = [{"id": item.get("id"), "state": item.get("state")} for item in queue_items]
        return not active_runs and not active_queue, {"activeRuns": active_runs, "activeQueue": active_queue}
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}


def _observe(result: PlannerFirstResponseResult, *, started_at: float, max_wait_s: int) -> None:
    latest_seq = 0
    deadline = time.time() + max(5, max_wait_s)
    while time.time() < deadline:
        for event in _runtime_events(result.session_id, after_seq=latest_seq):
            try:
                latest_seq = max(latest_seq, int(event.get("seq") or latest_seq))
            except Exception:
                pass
            topic = str(event.get("topic") or event.get("event_type") or event.get("type") or "").strip()
            if topic and topic not in result.observed_topics:
                result.observed_topics.append(topic)
            if topic.startswith("planner.") and topic not in result.planner_events:
                result.planner_events.append(topic)
            if topic.startswith("runtime.episode.") and result.first_activity_ms is None:
                result.execution_map_before_activity = True
            event_activity = _runtime_event_meaningful_activity(event)
            if event_activity and result.first_activity_ms is None:
                result.first_activity_ms = int((time.perf_counter() - started_at) * 1000)
                result.first_activity_kind, result.first_activity_preview = event_activity
        messages = _canonical_messages(result.session_id)
        visible = _assistant_visible_activity(messages)
        if visible and result.first_visible_ms is None:
            result.first_visible_ms = int((time.perf_counter() - started_at) * 1000)
            result.first_visible_kind, result.first_visible_preview = visible
        activity = _assistant_meaningful_activity(messages)
        if activity and result.first_activity_ms is None:
            result.first_activity_ms = int((time.perf_counter() - started_at) * 1000)
            result.first_activity_kind, result.first_activity_preview = activity
        if result.first_activity_ms is not None:
            break
        time.sleep(0.5)


def _wait_idle(session_id: str, timeout_s: int) -> dict[str, Any]:
    deadline = time.time() + max(5, timeout_s)
    last: dict[str, Any] = {}
    while time.time() < deadline:
        idle, state = _session_idle_state(session_id)
        last = state
        if idle:
            return state
        time.sleep(2)
    return last


def _cancel_active_run(run_id: str, *, reason: str) -> dict[str, Any]:
    if not run_id:
        return {"cancelled": False, "reason": "missing_run_id"}
    try:
        from erc.kernel import erc_kernel
    except Exception as exc:  # noqa: BLE001
        return {"cancelled": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        result = erc_kernel.cancel_run(run_id, reason=reason)
        return {"cancelled": bool(result), "result": result}
    except Exception as exc:  # noqa: BLE001
        return {"cancelled": False, "error": f"{type(exc).__name__}: {exc}"}


def _write_report(result: PlannerFirstResponseResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "planner_first_response_live_result.json"
    json_path.write_text(json.dumps(result.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Planner First Response Live Audit",
        "",
        f"- Status: {result.status}",
        f"- Session: `{result.session_id}`",
        f"- Run: `{result.run_id}`",
        f"- submitLatencyMs: {result.submit_latency_ms}",
        f"- firstVisibleMs: {result.first_visible_ms}",
        f"- firstVisibleKind: {result.first_visible_kind}",
        f"- firstActivityMs: {result.first_activity_ms}",
        f"- firstActivityKind: {result.first_activity_kind}",
        f"- executionMapBeforeActivity: {result.execution_map_before_activity}",
        f"- plannerEvents: {', '.join(result.planner_events) or '-'}",
        "",
        "## Findings",
        "",
    ]
    if result.findings:
        for finding in result.findings:
            lines.append(f"- [{finding.get('severity')}] {finding.get('code')}: {finding.get('summary')}")
    else:
        lines.append("- none")
    lines.extend(["", f"Raw JSON: `{json_path}`", ""])
    md_path = output_dir / "PLANNER_FIRST_RESPONSE_LIVE_AUDIT_ZH.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def _run(args: argparse.Namespace) -> PlannerFirstResponseResult:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    session_id = f"planner-first-response-live-{timestamp}"
    prompt = (
        "普通 Planner 模式验证，不开启 Spec Mode。请规划一个不含 Computer Use/RPA 的小型任务："
        "先调研 V8OS Memory Evidence Pack 的改进点，再给出 Engineering 计划，并让一个子代理复核风险。"
        "不要真实写文件。关键验收是：Supervisor 应尽快开始真实思考/工具/todo 活动，不能先被 Planner 长时间卡住。"
    )
    result = PlannerFirstResponseResult(session_id=session_id)
    started = time.perf_counter()
    run_id, latency, response = _submit(
        args.engine_url,
        session_id=session_id,
        workspace=Path(args.workspace).resolve(),
        model_profile=args.model_profile,
        prompt=prompt,
        tag=f"planner-first-response-{timestamp}",
    )
    result.run_id = run_id
    result.submit_latency_ms = latency
    if not response.get("accepted"):
        result.findings.append({"severity": "P0", "code": "submit_not_accepted", "summary": json.dumps(response, ensure_ascii=False)[:500]})
    _observe(result, started_at=started, max_wait_s=args.first_activity_wait)
    if result.first_activity_ms is not None and args.observe_after_activity > 0:
        time.sleep(args.observe_after_activity)
    result.final_idle_state = _wait_idle(session_id, timeout_s=args.max_wait)
    active_runs = list(result.final_idle_state.get("activeRuns") or []) if isinstance(result.final_idle_state, dict) else []
    if active_runs and args.cleanup_active_run:
        result.cleanup = _cancel_active_run(
            run_id,
            reason="planner_first_response_live_cleanup_after_measurement",
        )
        result.final_idle_state = _wait_idle(session_id, timeout_s=5)
    if result.submit_latency_ms is not None and result.submit_latency_ms > 30000:
        result.findings.append({"severity": "P0", "code": "submit_too_slow", "summary": f"submit took {result.submit_latency_ms}ms"})
    if result.first_activity_ms is None:
        result.findings.append({"severity": "P0", "code": "no_first_activity", "summary": "等待窗口内没有观察到 Supervisor 真实活动"})
    elif result.first_activity_ms > args.first_activity_budget_ms:
        result.findings.append({"severity": "P1", "code": "first_activity_slow", "summary": f"first activity took {result.first_activity_ms}ms"})
    if result.execution_map_before_activity:
        result.findings.append(
            {
                "severity": "P1",
                "code": "execution_projection_before_supervisor_activity",
                "summary": "runtime episode/projection appeared before meaningful Supervisor activity",
            }
        )
    if result.first_activity_ms is not None and "planner.deferred" not in result.planner_events and result.first_activity_ms > args.first_activity_budget_ms:
        result.findings.append({"severity": "P1", "code": "planner_not_deferred_before_slow_activity", "summary": "首响慢且未观察到 planner.deferred"})
    result.status = "failed" if any(item.get("severity") == "P0" for item in result.findings) else ("degraded" if result.findings else "passed")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live Planner first-response audit.")
    parser.add_argument("--live", action="store_true", help="Submit a real live chat run.")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--model-profile", default="deepseek-v4-flash")
    parser.add_argument("--first-activity-budget-ms", type=int, default=3000)
    parser.add_argument("--first-activity-wait", type=int, default=60)
    parser.add_argument("--observe-after-activity", type=int, default=8)
    parser.add_argument("--max-wait", type=int, default=5)
    parser.add_argument("--cleanup-active-run", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"live": False, "workspace": args.workspace, "modelProfile": args.model_profile}, ensure_ascii=False, indent=2))
        return 0
    ok, error = _wait_for_engine(args.engine_url)
    if not ok:
        print(f"[planner-first-response-live] Engine unavailable: {error}", file=sys.stderr)
        return 2
    result = _run(args)
    if args.write_report:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_REPORT_ROOT / "planner_first_response" / timestamp
        report_path = _write_report(result, output_dir)
        print(f"[planner-first-response-live] report: {report_path}")
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0 if result.status in {"passed", "degraded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
