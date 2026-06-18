from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
DEFAULT_WORKSPACE = Path("E:/Projects/test3")
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))
TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "canceled", "succeeded", "success"}
ACTIVE_RUNTIME_EPISODE_STATES = {
    "detected",
    "routed",
    "queued",
    "leased",
    "active",
    "waiting",
    "waiting_child",
    "waiting_external",
    "waiting_approval",
}

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


@dataclass
class Finding:
    severity: str
    code: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseResult:
    case_id: str
    prompt: str
    session_id: str
    run_id: str = ""
    status: str = "pending"
    latency_ms: int | None = None
    actual_tools: list[str] = field(default_factory=list)
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    observed_topics: list[str] = field(default_factory=list)
    episodes: list[dict[str, Any]] = field(default_factory=list)
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    output_checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    key_events: list[dict[str, Any]] = field(default_factory=list)


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


def _submit(
    engine_url: str,
    *,
    session_id: str,
    workspace: Path,
    prompt: str,
    model_profile: str,
    client_tag: str,
) -> tuple[str, int, dict[str, Any]]:
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": client_tag,
        "stream": False,
        "workspacePath": str(workspace),
        "messages": [{"role": "user", "content": prompt}],
        "data": {
            "conversationId": session_id,
            "clientMessageId": client_tag,
            "modelProfile": model_profile,
            "taskPlanningMode": False,
            "plannerMode": "suggest",
            "relaxedLimitsLiveAudit": True,
        },
    }
    started = time.perf_counter()
    response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return str(response.get("run_id") or response.get("runId") or ""), latency_ms, response


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
        queue_items = db.list_chat_user_message_queue(session_id=session_id, states=["pending", "promoted", "queued"], limit=20)
        active_queue = [{"id": item.get("id"), "state": item.get("state")} for item in queue_items]
        episode_items = db.list_runtime_episodes(session_id=session_id, active_only=True, limit=50)
        active_episodes = [
            {
                "id": item.get("episodeId") or item.get("id"),
                "kind": item.get("kind"),
                "state": item.get("state"),
                "runId": item.get("runId") or item.get("run_id"),
            }
            for item in episode_items
            if str(item.get("state") or "").strip().lower() in ACTIVE_RUNTIME_EPISODE_STATES
        ]
        return not active_runs and not active_queue and not active_episodes, {
            "activeRuns": active_runs,
            "activeQueue": active_queue,
            "activeRuntimeEpisodes": active_episodes,
        }
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}


def _wait_for_idle(session_id: str, *, timeout_s: int) -> tuple[bool, dict[str, Any]]:
    deadline = time.time() + max(5, timeout_s)
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        idle, state = _session_idle_state(session_id)
        last_state = state
        if idle:
            return True, state
        time.sleep(2)
    return False, last_state


def _load_case_facts(result: CaseResult) -> None:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        result.key_events.append({"dbImportError": f"{type(exc).__name__}: {exc}"})
        return
    try:
        events = list(db.get_runtime_events(result.session_id))
    except Exception as exc:  # noqa: BLE001
        events = []
        result.key_events.append({"runtimeEventsError": f"{type(exc).__name__}: {exc}"})
    topics: list[str] = []
    tools: list[str] = []
    seen_tool_call_ids: set[str] = set()
    for event in events:
        topic = str(event.get("topic") or event.get("event_type") or event.get("type") or "").strip()
        if topic and topic not in topics:
            topics.append(topic)
        tools.extend(_collect_tool_names(event))
        if topic == "tool.started":
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            tool_payload = payload.get("tool") if isinstance(payload.get("tool"), dict) else payload
            tool_name = str(tool_payload.get("toolName") or tool_payload.get("tool_name") or "").strip()
            tool_call_id = str(tool_payload.get("toolCallId") or tool_payload.get("tool_call_id") or "").strip()
            dedupe_id = tool_call_id or f"{event.get('seq')}:{tool_name}"
            if tool_name and dedupe_id not in seen_tool_call_ids:
                seen_tool_call_ids.add(dedupe_id)
                result.tool_call_counts[tool_name] = result.tool_call_counts.get(tool_name, 0) + 1
    result.observed_topics = topics
    try:
        result.episodes = list(db.list_runtime_episodes(session_id=result.session_id, limit=100))
        handoffs: list[dict[str, Any]] = []
        for episode in result.episodes:
            episode_id = str(episode.get("episodeId") or episode.get("id") or "")
            if episode_id:
                handoffs.extend(db.list_runtime_episode_handoffs(episode_id))
        result.handoffs = handoffs
        tools.extend(_collect_tool_names(result.episodes))
        tools.extend(_collect_tool_names(result.handoffs))
    except Exception as exc:  # noqa: BLE001
        result.key_events.append({"episodeFactsError": f"{type(exc).__name__}: {exc}"})
    try:
        messages = list(db.get_chat_canonical_messages(result.session_id))
        result.final_text = _extract_final_text(messages)
        tools.extend(_collect_tool_names(messages))
    except Exception as exc:  # noqa: BLE001
        result.key_events.append({"canonicalMessagesError": f"{type(exc).__name__}: {exc}"})
    result.actual_tools = sorted(set(tools))


def _collect_tool_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        for key in ("tool", "toolName", "tool_name", "name"):
            raw = value.get(key)
            if isinstance(raw, str) and _looks_like_tool_name(raw):
                names.append(raw)
        for key in ("toolCalls", "tool_calls", "toolsUsed", "tools", "actualTools"):
            raw = value.get(key)
            if isinstance(raw, list):
                for item in raw:
                    names.extend(_collect_tool_names(item))
        for item in value.values():
            if isinstance(item, (dict, list)):
                names.extend(_collect_tool_names(item))
    elif isinstance(value, list):
        for item in value:
            names.extend(_collect_tool_names(item))
    elif isinstance(value, str):
        for match in re.findall(r"\b(?:web_broker|research_broker|runtime_broker|delegation_broker|write_native_file|run_system_command|spec_broker)\b", value):
            names.append(match)
    return names


def _looks_like_tool_name(value: str) -> bool:
    return value in {
        "web_broker",
        "research_broker",
        "runtime_broker",
        "delegation_broker",
        "write_native_file",
        "run_system_command",
        "spec_broker",
        "memory_broker",
        "fetch_skill_instructions",
    } or value.startswith(("creative_media_", "computer_use_", "rpa_"))


def _extract_final_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        role = str(message.get("role") or "").lower()
        if role not in {"assistant", "ai", "supervisor"}:
            continue
        for key in ("content_text", "contentText", "content", "text"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _case_specs(timestamp: str, marker: str, target_dir: Path) -> list[tuple[str, str, dict[str, Any]]]:
    simple_file = target_dir / "simple-direct-note.md"
    return [
        (
            "simple_direct_file",
            (
                "普通模式小任务：请在当前工作区创建一个很小的 Markdown 文件，路径为 "
                f"`{simple_file}`，内容必须包含 `{marker}`。"
                "这是简单交付，不需要开启 Spec，不需要调研，不需要桌面或 RPA。"
            ),
            {"expectedFiles": [simple_file], "expectedMarker": marker, "warnIfRuntimeEpisode": True},
        ),
        (
            "web_fact_gathering",
            (
                "普通模式资料核查：请使用 web_broker 核查 V8OS 这类本地 agent 系统做"
                "“工具输出给 agent 应该简洁、raw JSON 放 detailRef”这个设计是否合理。"
                "web_broker 总调用次数最多 4 次，search/read/extract 全部计入这 4 次。"
                "不要写文件，不要开启 Spec，不要使用 Computer Use/RPA。最后用 5 条以内中文要点回答。"
            ),
            {"expectedAnyTools": ["web_broker", "research_broker"], "maxToolCalls": {"web_broker": 4}},
        ),
        (
            "complex_runtime_route",
            (
                "普通模式复杂任务边界测试：请规划一个需要 Research、Engineering、Subagent 协作的 V8OS 改造小任务，"
                "并选择合适方式启动一次 runtime/delegation 编排或说明为什么只需要计划。不要写真实项目文件，不要使用 Computer Use/RPA。"
            ),
            {"expectedRuntimeEpisode": True},
        ),
    ]


def _evaluate_case(result: CaseResult, expectations: dict[str, Any]) -> None:
    marker = str(expectations.get("expectedMarker") or "")
    for path in expectations.get("expectedFiles") or []:
        file_path = Path(path)
        text = file_path.read_text(encoding="utf-8", errors="replace") if file_path.exists() else ""
        result.output_checks[str(file_path)] = {
            "exists": file_path.exists(),
            "chars": len(text),
            "containsMarker": bool(marker and marker in text),
        }
        if not file_path.exists():
            result.findings.append(Finding("P0", "expected_file_missing", "简单文件交付没有产物", {"path": str(file_path)}))
        elif marker and marker not in text:
            result.findings.append(Finding("P1", "expected_marker_missing", "文件存在但缺少 live marker", {"path": str(file_path)}))
    expected_any = set(expectations.get("expectedAnyTools") or [])
    if expected_any and not expected_any.intersection(result.actual_tools):
        result.findings.append(
            Finding(
                "P1",
                "expected_tool_not_observed",
                "未观测到期望的工具调用",
                {"expectedAny": sorted(expected_any), "actualTools": result.actual_tools},
            )
        )
    for tool_name, raw_limit in dict(expectations.get("maxToolCalls") or {}).items():
        limit = int(raw_limit)
        actual = int(result.tool_call_counts.get(str(tool_name), 0))
        if actual > limit:
            result.findings.append(
                Finding(
                    "P1",
                    "user_tool_call_limit_exceeded",
                    "实际工具调用次数超过用户明确限制",
                    {"tool": str(tool_name), "limit": limit, "actual": actual},
                )
            )
    if expectations.get("expectedRuntimeEpisode") and not result.episodes:
        result.findings.append(Finding("P1", "runtime_episode_not_observed", "复杂任务未观测到 runtime episode", {"tools": result.actual_tools}))
    if expectations.get("warnIfRuntimeEpisode") and result.episodes:
        result.findings.append(
            Finding(
                "P2",
                "simple_task_routed",
                "简单任务完成了，但仍进入 runtime episode，可继续观察 Supervisor 是否过度路由",
                {"episodes": [{"kind": item.get("kind"), "state": item.get("state")} for item in result.episodes]},
            )
        )
    combined = "\n".join([result.final_text, json.dumps(result.key_events, ensure_ascii=False), json.dumps(result.handoffs, ensure_ascii=False)])
    if "[route required]" in combined:
        result.findings.append(Finding("P1", "route_required_surface_seen", "live 输出中仍出现 route required 提示", {"snippet": combined[:1200]}))
    if result.status != "completed":
        result.findings.append(Finding("P0", "case_not_completed", "live case 未正常结束", {"status": result.status}))


def _write_report(output_dir: Path, results: list[CaseResult]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "supervisor_relaxed_limits_live_result.json"
    payload = {
        "status": "passed" if not any(item.findings for item in results) else "failed",
        "cases": [
            {
                "caseId": item.case_id,
                "sessionId": item.session_id,
                "runId": item.run_id,
                "status": item.status,
                "latencyMs": item.latency_ms,
                "actualTools": item.actual_tools,
                "toolCallCounts": item.tool_call_counts,
                "episodeKinds": sorted(set(str(ep.get("kind") or "") for ep in item.episodes if ep.get("kind"))),
                "handoffKinds": sorted(set(str(h.get("kind") or "") for h in item.handoffs if h.get("kind"))),
                "outputChecks": item.output_checks,
                "findings": [finding.__dict__ for finding in item.findings],
                "finalTextPreview": item.final_text[:1000],
            }
            for item in results
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = output_dir / "SUPERVISOR_RELAXED_LIMITS_LIVE_AUDIT_ZH.md"
    lines = [
        "# Supervisor Relaxed Limits Live Audit",
        "",
        f"- Status: {payload['status']}",
        f"- Cases: {len(results)}",
        "",
        "| Case | Status | Tools | Episodes | Findings |",
        "|---|---|---|---|---|",
    ]
    for item in results:
        findings = "; ".join(f"{finding.severity}:{finding.code}" for finding in item.findings) or "-"
        episode_kinds = ",".join(sorted(set(str(ep.get("kind") or "") for ep in item.episodes if ep.get("kind")))) or "-"
        lines.append(
            f"| {item.case_id} | {item.status} | {', '.join(item.actual_tools[:8]) or '-'} | {episode_kinds} | {findings} |"
        )
    lines.extend(["", f"Raw JSON: `{json_path}`", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def run_live(
    *,
    engine_url: str,
    workspace: Path,
    model_profile: str,
    max_wait: int,
    output_dir: Path,
    selected_cases: set[str] | None = None,
) -> tuple[str, list[CaseResult]]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    marker = f"RELAXED_LIMITS_{timestamp}"
    target_dir = workspace / ".v8" / "live-audit" / "relaxed-limits" / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)
    results: list[CaseResult] = []
    for case_id, prompt, expectations in _case_specs(timestamp, marker, target_dir):
        if selected_cases and case_id not in selected_cases:
            continue
        session_id = f"supervisor-relaxed-limits-live-{timestamp}-{case_id}"
        result = CaseResult(case_id=case_id, prompt=prompt, session_id=session_id)
        try:
            run_id, latency_ms, response = _submit(
                engine_url,
                session_id=session_id,
                workspace=workspace,
                prompt=prompt,
                model_profile=model_profile,
                client_tag=f"{case_id}-{timestamp}",
            )
            result.run_id = run_id
            result.latency_ms = latency_ms
            result.key_events.append({"submit": response})
            idle, idle_state = _wait_for_idle(session_id, timeout_s=max_wait)
            result.key_events.append({"idle": idle, "idleState": idle_state})
            result.status = "completed" if idle else "timeout"
        except Exception as exc:  # noqa: BLE001
            result.status = "failed"
            result.findings.append(Finding("P0", "submit_or_wait_failed", "提交或等待 live case 失败", {"error": f"{type(exc).__name__}: {exc}"}))
        _load_case_facts(result)
        _evaluate_case(result, expectations)
        results.append(result)
    report = _write_report(output_dir, results)
    return str(report), results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Submit real live chat runs.")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--model-profile", default="deepseek-v4-flash")
    parser.add_argument("--max-wait", type=int, default=300)
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--case",
        action="append",
        choices=["simple_direct_file", "web_fact_gathering", "complex_runtime_route"],
        help="Run only the selected case. Repeat to select multiple cases.",
    )
    args = parser.parse_args()

    if not args.live:
        print("Refusing to run without --live. This script creates real chat sessions.")
        return 2
    ok, error = _wait_for_engine(args.engine_url)
    if not ok:
        print(f"[relaxed-limits-live] Engine unavailable: {error}", file=sys.stderr)
        return 1
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else DEFAULT_REPORT_ROOT / "supervisor_relaxed_limits" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    report, results = run_live(
        engine_url=args.engine_url,
        workspace=Path(args.workspace),
        model_profile=args.model_profile,
        max_wait=args.max_wait,
        output_dir=output_dir,
        selected_cases=set(args.case or []),
    )
    status = "passed" if not any(item.findings for item in results) else "failed"
    print(f"[relaxed-limits-live] report: {report}")
    print(json.dumps({"status": status, "cases": [{"caseId": item.case_id, "findings": [f.code for f in item.findings]} for item in results]}, ensure_ascii=False, indent=2))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
