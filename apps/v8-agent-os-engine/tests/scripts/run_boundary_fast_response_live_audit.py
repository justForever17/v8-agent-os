from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[2]
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


@dataclass
class Finding:
    severity: str
    code: str
    summary: str
    evidence: str


@dataclass
class CaseResult:
    case_id: str
    session_id: str
    prompt: str
    submit_latency_ms: int | None = None
    first_supervisor_activity_ms: int | None = None
    final_status: str = "pending"
    observed_topics: list[str] = field(default_factory=list)
    assistant_messages: list[str] = field(default_factory=list)
    forbidden_runtime_hits: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def _engine_api_base(engine_url: str) -> str:
    base = engine_url.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def _engine_root_url(engine_url: str) -> str:
    base = engine_url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def _wait_for_engine(engine_url: str, timeout_s: float) -> tuple[bool, str]:
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            _json_request(f"{_engine_root_url(engine_url)}/health", timeout=3)
            return True, ""
        except Exception as exc:  # noqa: BLE001 - live audit reports exact connectivity failures.
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.75)
    return False, last_error


def _skill_list(engine_url: str, workspace: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"workspacePath": workspace})
    return _json_request(f"{_engine_api_base(engine_url)}/skills/list?{query}", timeout=20)


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


def _is_assistant_activity(message: dict[str, Any]) -> bool:
    role = str(message.get("role") or "").strip().lower()
    if role not in {"assistant", "ai", "supervisor"}:
        return False
    if _message_text(message):
        return True
    metadata = message.get("metadata") or message.get("metadata_json")
    if isinstance(metadata, str):
        return any(marker in metadata for marker in ("tool_calls", "runtime_broker", "write_todos"))
    return bool(metadata)


def _topic(event: dict[str, Any]) -> str:
    return str(event.get("topic") or event.get("event_type") or event.get("type") or "").strip()


def _payload(event: dict[str, Any]) -> Any:
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload
    return payload


def _collect_runtime_mentions(value: Any) -> set[str]:
    hits: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in {"runtimekind", "runtime_kind", "episodekind", "episode_kind", "runtime", "primaryruntime"}:
                text = str(item or "").strip().lower()
                if text in {"computer_use", "computer-use", "rpa"}:
                    hits.add(text.replace("-", "_"))
            hits.update(_collect_runtime_mentions(item))
    elif isinstance(value, list):
        for item in value:
            hits.update(_collect_runtime_mentions(item))
    return hits


def _case_specs() -> list[tuple[str, str]]:
    return [
        (
            "video_boundary",
            (
                "请设计一个 60 秒科普讲解视频：主题是 V8OS Memory 如何选择证据。"
                "不要真实生成媒体、不要写文件、不要调用桌面或 RPA。"
                "请判断主路线应走代码视频/工程链路还是 Creative Media，并给出简短执行方案。"
            ),
        ),
        (
            "terminal_boundary",
            (
                "边界判断题：如果用户说“打开终端安装 wechat-account-articles skill 并启动 Claude”，"
                "本轮不要真实安装、不要写文件、不要打开桌面。请判断应该走逻辑命令/工程链路还是 Computer Use，"
                "并说明原因与下一步安全做法。"
            ),
        ),
    ]


def _submit_case(engine_url: str, *, case_id: str, prompt: str, workspace: str, timestamp: str) -> CaseResult:
    session_id = f"boundary-fast-response-live-{timestamp}-{case_id}"
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": f"{case_id}-{timestamp}",
        "stream": False,
        "workspacePath": workspace,
        "messages": [{"role": "user", "content": prompt}],
        "data": {
            "conversationId": session_id,
            "clientMessageId": f"{case_id}-{timestamp}",
            "boundaryFastResponseLiveAudit": True,
        },
    }
    result = CaseResult(case_id=case_id, session_id=session_id, prompt=prompt)
    started = time.perf_counter()
    try:
        response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
        result.submit_latency_ms = int((time.perf_counter() - started) * 1000)
        if not response.get("accepted"):
            result.findings.append(Finding("P0", "submit_not_accepted", "chat submit 未 accepted", json.dumps(response, ensure_ascii=False)))
    except Exception as exc:  # noqa: BLE001
        result.submit_latency_ms = int((time.perf_counter() - started) * 1000)
        result.final_status = "failed"
        result.findings.append(Finding("P0", "submit_failed", "chat submit 调用失败", f"{type(exc).__name__}: {exc}"))
    return result


def _observe_case(result: CaseResult, *, max_wait_s: float) -> None:
    started = time.perf_counter()
    latest_seq = 0
    first_activity_seen = False
    terminal_seen = False
    while time.perf_counter() - started < max_wait_s:
        for event in _runtime_events(result.session_id, after_seq=latest_seq):
            try:
                latest_seq = max(latest_seq, int(event.get("seq") or latest_seq))
            except Exception:
                pass
            topic = _topic(event)
            if topic and topic not in result.observed_topics:
                result.observed_topics.append(topic)
            runtime_hits = _collect_runtime_mentions({"topic": topic, "payload": _payload(event)})
            for hit in sorted(runtime_hits):
                if hit not in result.forbidden_runtime_hits:
                    result.forbidden_runtime_hits.append(hit)
            if topic.endswith(".completed") or topic.endswith(".failed") or topic.endswith(".cancelled") or "run.completed" in topic:
                terminal_seen = True
        messages = _canonical_messages(result.session_id)
        assistant_messages = [message for message in messages if _is_assistant_activity(message)]
        if assistant_messages and not first_activity_seen:
            result.first_supervisor_activity_ms = int((time.perf_counter() - started) * 1000)
            first_activity_seen = True
        result.assistant_messages = [_message_text(message)[:500] for message in assistant_messages if _message_text(message)]
        if terminal_seen and first_activity_seen:
            break
        time.sleep(1.0)
    if result.submit_latency_ms is not None and result.submit_latency_ms > 30000:
        result.findings.append(Finding("P0", "submit_too_slow", "chat submit 超过 30s", f"{result.submit_latency_ms}ms"))
    if result.first_supervisor_activity_ms is None:
        result.findings.append(Finding("P0", "no_supervisor_activity", "等待窗口内没有观察到真实 Supervisor 活动", f"waited={max_wait_s}s"))
    elif result.first_supervisor_activity_ms > 3000:
        result.findings.append(
            Finding(
                "P1",
                "first_supervisor_activity_slow",
                "首个 Supervisor 活动超过 3s 目标",
                f"{result.first_supervisor_activity_ms}ms",
            )
        )
    if result.forbidden_runtime_hits:
        result.findings.append(
            Finding(
                "P1",
                "forbidden_runtime_route",
                "边界测试中出现 Computer Use/RPA runtime 路由迹象",
                ", ".join(result.forbidden_runtime_hits),
            )
        )
    result.final_status = "failed" if any(item.severity == "P0" for item in result.findings) else "completed"


def _write_report(report_dir: Path, *, skill_ok: bool, skill_payload: dict[str, Any], results: list[CaseResult]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "BOUNDARY_FAST_RESPONSE_LIVE_AUDIT_ZH.md"
    lines = [
        "# Boundary / Fast Response Live Audit",
        "",
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- scoped skill list: {'PASS' if skill_ok else 'FAIL'}",
        f"- skill count: {len(skill_payload.get('skills') or [])}",
        "",
        "## Findings",
    ]
    findings = [finding for result in results for finding in result.findings]
    if not findings:
        lines.append("- 未发现 P0/P1。")
    else:
        for finding in findings:
            lines.append(f"- **{finding.severity} {finding.code}**: {finding.summary} / {finding.evidence}")
    lines.extend(["", "## Cases"])
    for result in results:
        lines.extend(
            [
                f"### {result.case_id}",
                f"- session: `{result.session_id}`",
                f"- submitLatencyMs: {result.submit_latency_ms}",
                f"- firstSupervisorActivityMs: {result.first_supervisor_activity_ms}",
                f"- status: {result.final_status}",
                f"- forbiddenRuntimeHits: {', '.join(result.forbidden_runtime_hits) or 'none'}",
                f"- observedTopics: {', '.join(result.observed_topics[:40]) or 'none'}",
                "- assistantMessages:",
            ]
        )
        for text in result.assistant_messages[:3]:
            lines.append(f"  - {text.replace(chr(10), ' ')[:240]}")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Live audit for workspace skills, fuzzy task boundaries, and fast Supervisor activity.")
    parser.add_argument("--live", action="store_true", help="Required to call the running Engine and create live sessions.")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", default=r"E:\Projects\test1")
    parser.add_argument("--max-wait", type=float, default=120.0)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--report-dir", default="")
    args = parser.parse_args()

    if not args.live:
        print("Refusing to run without --live. This script creates real chat sessions.")
        return 2

    ok, error = _wait_for_engine(args.engine_url, timeout_s=20)
    if not ok:
        print(f"Engine unavailable: {error}")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    skill_payload = _skill_list(args.engine_url, args.workspace)
    skill_names = {str(item.get("skillName") or item.get("name") or "") for item in skill_payload.get("skills") or []}
    skill_ok = "wechat-account-articles" in skill_names
    print(f"[skill-list] wechat-account-articles={'yes' if skill_ok else 'no'} total={len(skill_names)}")

    results: list[CaseResult] = []
    for case_id, prompt in _case_specs():
        result = _submit_case(args.engine_url, case_id=case_id, prompt=prompt, workspace=args.workspace, timestamp=timestamp)
        if result.final_status != "failed":
            _observe_case(result, max_wait_s=args.max_wait)
        results.append(result)
        print(
            f"[case:{case_id}] status={result.final_status} submit={result.submit_latency_ms}ms "
            f"firstActivity={result.first_supervisor_activity_ms}ms findings={len(result.findings)}"
        )

    if not skill_ok:
        results.append(
            CaseResult(
                case_id="skill_list",
                session_id="",
                prompt="",
                final_status="failed",
                findings=[
                    Finding(
                        "P0",
                        "workspace_skill_missing",
                        "test1 workspace skill 未出现在 scoped skill list",
                        "expected skillName=wechat-account-articles",
                    )
                ],
            )
        )

    if args.write_report:
        report_dir = Path(args.report_dir) if args.report_dir else DEFAULT_REPORT_ROOT / "boundary_fast_response" / timestamp
        report_path = _write_report(report_dir, skill_ok=skill_ok, skill_payload=skill_payload, results=results)
        print(f"[report] {report_path}")

    has_p0 = any(finding.severity == "P0" for result in results for finding in result.findings)
    has_p1 = any(finding.severity == "P1" for result in results for finding in result.findings)
    return 2 if has_p0 else 1 if has_p1 else 0


if __name__ == "__main__":
    raise SystemExit(main())
