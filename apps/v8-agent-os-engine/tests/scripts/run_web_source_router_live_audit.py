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
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))
TOKEN_RE = re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]+|((?:api[_-]?key|token|cookie|authorization)[\"'\s:=]+)[^\"'\s,;]+")

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


@dataclass
class AuditCaseResult:
    case_id: str
    title: str
    status: str = "pending"
    summary: str = ""
    session_id: str = ""
    run_id: str = ""
    elapsed_ms: int = 0
    tools: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _redact(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = TOKEN_RE.sub(lambda match: f"{match.group(1) or match.group(2)}[REDACTED]", text)
    text = text.replace(str(Path.home()), "~")
    return text


def _json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
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


def _wait_for_engine(engine_url: str, *, timeout: float = 20.0) -> tuple[bool, str]:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            _json_request(f"{_engine_root_url(engine_url)}/health", timeout=3)
            return True, ""
        except Exception as exc:  # noqa: BLE001 - live diagnostic script reports exact connectivity failures.
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.75)
    return False, last_error


def _collect_tool_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in {"tool", "tool_name", "toolname", "function_name"} and isinstance(item, str):
                names.add(item)
            if normalized == "function" and isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(str(item["name"]))
            names.update(_collect_tool_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_collect_tool_names(item))
    return names


def _load_runtime_events(session_id: str, run_id: str = "") -> list[dict[str, Any]]:
    try:
        from core.database import db
    except Exception:
        return []
    events: list[dict[str, Any]] = []
    try:
        if session_id:
            events.extend(db.get_runtime_events(session_id))
        if run_id:
            events.extend(db.get_runtime_events_for_run(run_id, session_id=session_id or None, limit=500))
    except Exception:
        return events
    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = str(event.get("id") or event.get("event_id") or f"{event.get('session_id')}:{event.get('seq')}:{event.get('topic')}")
        deduped[event_id] = event
    return sorted(deduped.values(), key=lambda item: int(item.get("seq") or 0))


def _run_chat_case(engine_url: str, *, case_id: str, title: str, prompt: str, max_wait: int) -> AuditCaseResult:
    result = AuditCaseResult(case_id=case_id, title=title)
    session_id = f"web-source-router-live-{datetime.now().strftime('%Y%m%d%H%M%S')}-{case_id}"
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": f"{case_id}-{int(time.time())}",
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
        "data": {
            "conversationId": session_id,
            "webSourceRouterLiveAudit": True,
        },
    }
    started = time.perf_counter()
    try:
        response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"submit_failed: {type(exc).__name__}: {exc}"))
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result
    result.session_id = str(response.get("session_id") or response.get("sessionId") or session_id)
    result.run_id = str(response.get("run_id") or response.get("runId") or "")
    deadline = time.time() + max_wait
    last_events: list[dict[str, Any]] = []
    while time.time() < deadline:
        last_events = _load_runtime_events(result.session_id, result.run_id)
        topics = {str(event.get("topic") or event.get("event_type") or "") for event in last_events}
        if any(topic.endswith(".completed") or ".completed" in topic or topic.endswith(".failed") or ".failed" in topic for topic in topics):
            break
        time.sleep(2)
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    result.tools = sorted(_collect_tool_names(last_events))
    result.evidence.append(_redact({"submitResponse": response}))
    result.evidence.append(_redact({"runtimeEventCount": len(last_events), "topics": [event.get("topic") for event in last_events[-20:]]}))
    if not last_events:
        result.status = "warning"
        result.failures.append("no_runtime_events_observed")
    else:
        result.status = "ok"
    return result


def _run_research_runtime_case() -> AuditCaseResult:
    result = AuditCaseResult(case_id="research_runtime", title="Research Runtime 多源调研")
    started = time.perf_counter()
    try:
        from core.tools.research_broker import research_broker

        raw = research_broker.func(
            mode="run",
            question="Source Router 当前应如何选择 Brave/Tavily/Exa/Jina/Firecrawl 与国内信息源？请保留来源。",
            sourcePolicy="authoritative",
            maxShards=2,
            maxRounds=1,
            tool_call_id="live-source-router-research",
        )
        payload = json.loads(raw)
        result.status = "ok" if payload.get("ok") else "warning"
        result.summary = str(payload.get("summary") or payload.get("finalAnswer") or "")[:600]
        result.providers = sorted({str(item.get("provider") or "") for item in payload.get("sourceMatrix") or [] if isinstance(item, dict) and item.get("provider")})
        result.evidence.append(_redact(payload))
        if not payload.get("ok"):
            result.failures.append(str(payload.get("summary") or payload.get("error") or "research_runtime_not_ok"))
        if not payload.get("evidenceBundleId"):
            result.failures.append("missing_evidence_bundle_id")
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_continuation_read_case() -> AuditCaseResult:
    result = AuditCaseResult(case_id="continuation_read", title="页面截断后的稳定续读")
    started = time.perf_counter()
    try:
        from langchain_core.messages import ToolMessage

        from core.native_tools import tool_observation_detail
        from core.tool_surface import apply_tool_surface_budget
        from core.tools.web_fetcher import web_read

        raw = web_read.func(url="https://docs.python.org/3/library/pathlib.html", mode="static", tool_call_id="live-continuation-read")
        message = ToolMessage(content=raw, tool_call_id="live-continuation-read", name="web_read")
        visible = apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 900, "contextWindowTokens": 16000},
            tool_name="web_read",
            runtime_kind="research",
        )
        content = str(visible.content)
        match = re.search(r"tool_observation_detail\(raw_ref='([^']+)'", content)
        if not match:
            result.status = "failed"
            result.failures.append("visible_surface_missing_tool_observation_detail")
            result.evidence.append(_redact(content[:1200]))
        else:
            raw_ref = match.group(1)
            detail = tool_observation_detail.invoke({"raw_ref": raw_ref, "max_chars": 8000})
            result.status = "ok" if "pathlib" in str(detail).lower() else "warning"
            result.evidence.append(_redact({"visible": content, "detailPreview": str(detail)[:3000]}))
            if result.status != "ok":
                result.failures.append("detail_read_did_not_include_expected_page_content")
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _write_report(results: list[AuditCaseResult], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "WEB_SOURCE_ROUTER_LIVE_AUDIT_ZH.md"
    lines = [
        "# Web Source Router Live Audit",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- Case 数量：{len(results)}",
        "",
        "## 结果摘要",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"### {result.case_id} · {result.title}",
                "",
                f"- 状态：{result.status}",
                f"- 耗时：{result.elapsed_ms} ms",
                f"- Session：{result.session_id or '-'}",
                f"- Run：{result.run_id or '-'}",
                f"- 工具：{', '.join(result.tools) if result.tools else '-'}",
                f"- Provider：{', '.join(result.providers) if result.providers else '-'}",
                f"- 失败：{'; '.join(result.failures) if result.failures else '-'}",
                "",
                "```json",
                _redact(result.evidence[-1] if result.evidence else {}),
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live Source Router / web read / Research Runtime audit.")
    parser.add_argument("--live", action="store_true", help="Required. Allows real model/network/engine calls.")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--case", choices=["supervisor_read", "subagent_read", "research_runtime", "continuation_read", "all"], default="all")
    parser.add_argument("--max-wait", type=int, default=180)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.live:
        print("Refusing to run live audit without --live.")
        return 2

    selected = {"supervisor_read", "subagent_read", "research_runtime", "continuation_read"} if args.case == "all" else {args.case}
    results: list[AuditCaseResult] = []
    needs_engine = bool(selected & {"supervisor_read", "subagent_read"})
    if needs_engine:
        ok, error = _wait_for_engine(args.engine_url)
        if not ok:
            print(f"Engine unavailable: {error}")
            return 1
    if "supervisor_read" in selected:
        results.append(
            _run_chat_case(
                args.engine_url,
                case_id="supervisor_read",
                title="Supervisor 查读公开页面并保留来源",
                prompt=(
                    "请查读 Python 官方 pathlib 文档，回答 Path.read_text 的 encoding 参数用途。"
                    "必须给出来源 URL；如果页面太长，需要使用 detail/续读能力，不要凭空回答。"
                ),
                max_wait=args.max_wait,
            )
        )
    if "subagent_read" in selected:
        results.append(
            _run_chat_case(
                args.engine_url,
                case_id="subagent_read",
                title="Subagent 查读信息并回流",
                prompt=(
                    "请派一个合适的子代理查读 Python 官方 pathlib 文档，确认 Path.read_text 的 encoding 参数用途，"
                    "然后把带来源 URL 的结论回流给 Supervisor。不要凭空总结。"
                ),
                max_wait=args.max_wait,
            )
        )
    if "research_runtime" in selected:
        results.append(_run_research_runtime_case())
    if "continuation_read" in selected:
        results.append(_run_continuation_read_case())

    for result in results:
        print(f"[{result.status}] {result.case_id}: {result.title} ({result.elapsed_ms} ms)")
        if result.failures:
            print("  failures:", "; ".join(result.failures))
    if args.write_report:
        output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_REPORT_ROOT / "agent_quality" / datetime.now().strftime("%Y%m%d_%H%M%S")
        print(f"Report: {_write_report(results, output_dir)}")
    return 1 if any(result.status == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
