from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_supervisor_runtime_skill_live_audit import (  # noqa: E402
    DEFAULT_ENGINE_URL,
    DEFAULT_REPORT_ROOT,
    _engine_api_base,
    _event_payload,
    _event_topic,
    _extract_final_text,
    _json_request,
    _load_canonical_messages,
    _load_durable_episode_facts,
    _load_durable_runtime_events,
    _poll_case,
    _redact,
    _wait_for_engine,
    LiveCaseResult,
    LiveCaseSpec,
)


@dataclass
class Finding:
    severity: str
    code: str
    summary: str
    evidence: str = ""


@dataclass
class ContinuationAuditResult:
    status: str = "pending"
    timestamp: str = ""
    session_id: str = ""
    model_profile: str = ""
    workspace: str = ""
    findings: list[Finding] = field(default_factory=list)
    report_dir: str | None = None
    observed_topics: list[str] = field(default_factory=list)
    final_text: str = ""

    def add(self, severity: str, code: str, summary: str, evidence: Any = "") -> None:
        self.findings.append(Finding(severity, code, summary, _redact(evidence) if evidence else ""))


def _report_dir(output_root: Path, timestamp: str) -> Path:
    return output_root / "engineering_continuation_live" / timestamp


def _submit_message(
    *,
    engine_url: str,
    session_id: str,
    client_message_id: str,
    workspace: Path,
    model_profile: str,
    content: str,
) -> LiveCaseResult:
    case = LiveCaseSpec(case_id=client_message_id, title=client_message_id, prompt=content)
    result = LiveCaseResult(spec=case, session_id=session_id)
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": client_message_id,
        "stream": False,
        "workspacePath": str(workspace),
        "messages": [{"role": "user", "content": content}],
        "data": {
            "conversationId": session_id,
            "clientMessageId": client_message_id,
            "modelProfile": model_profile,
            "engineeringContinuationLiveAudit": True,
        },
    }
    started = time.perf_counter()
    try:
        response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failure_reason = _redact(f"{type(exc).__name__}: {exc}")
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    result.run_id = str(response.get("run_id") or response.get("runId") or "") or None
    result.key_events.append(_redact({"submitResponse": response}))
    result.status = "submitted"
    return result


def _prepare_long_file(workspace: Path) -> Path:
    target = workspace / "debug-continuation-demo" / "large.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"line {index:04d}: keep\n" for index in range(1, 1201)), encoding="utf-8")
    return target


def _collect_session_facts(session_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    dummy = LiveCaseResult(LiveCaseSpec(case_id="engineering_continuation", title="Engineering continuation", prompt=""))
    dummy.session_id = session_id
    events, _event_error = _load_durable_runtime_events(dummy)
    episodes, handoffs, _episode_error = _load_durable_episode_facts(dummy)
    return events, episodes, handoffs


def _validate(
    result: ContinuationAuditResult,
    *,
    engine_url: str,
    session_id: str,
    workspace: Path,
    large_file: Path,
    first: LiveCaseResult,
    second: LiveCaseResult,
    third: LiveCaseResult,
) -> None:
    del engine_url
    events, episodes, handoffs = _collect_session_facts(session_id)
    topics = [_event_topic(item) for item in events if _event_topic(item)]
    result.observed_topics = topics
    result.final_text = third.final_text or second.final_text or first.final_text
    blob = json.dumps(
        {
            "topics": topics,
            "episodes": episodes,
            "handoffs": handoffs,
            "first": first.key_events[-6:],
            "second": second.key_events[-6:],
            "third": third.key_events[-6:],
        },
        ensure_ascii=False,
        default=str,
    )
    if first.status not in {"completed", "terminal"}:
        result.add("P1", "initial_engineering_run_not_completed", "初始工程任务未正常完成，续接上下文可能不足。", first.failure_reason or first.status)
    if not re.search(r"engineering_continuation|same_session_engineering_continuation", blob, re.I):
        result.add("P0", "engineering_continuation_not_detected", "同 session 报错/日志没有被识别为 Engineering continuation。", blob[:6000])
    if not any(str(item.get("kind") or "").lower() == "engineering" for item in episodes):
        result.add("P0", "engineering_episode_missing", "没有观察到 Engineering episode，可能仍由 Supervisor 直接修。", blob[:6000])
    if re.search(r"Blocked direct Supervisor tool", blob) and not re.search(r"runtime\.episode|engineering", blob, re.I):
        result.add("P0", "supervisor_hit_direct_gate_without_route", "Supervisor 撞 direct gate 后没有进入 Engineering route。", blob[:6000])

    text = large_file.read_text(encoding="utf-8", errors="replace")
    expected = "line 0500: patched-by-continuation\nline 0501: patched-by-continuation\nline 0502: patched-by-continuation\n"
    if expected not in text:
        result.add("P0", "long_file_patch_not_applied", "长文件行号片段替换没有生效。", text[text.find("line 0498"): text.find("line 0505")])
    if "line 0499: keep" not in text or "line 0503: keep" not in text:
        result.add("P0", "long_file_patch_over_touched_neighbors", "长文件替换疑似越界或全量覆盖。", text[text.find("line 0498"): text.find("line 0505")])


def _write_report(result: ContinuationAuditResult, output_root: Path) -> Path:
    report_dir = _report_dir(output_root, result.timestamp)
    report_dir.mkdir(parents=True, exist_ok=True)
    result.report_dir = str(report_dir)
    report = report_dir / "ENGINEERING_CONTINUATION_LIVE_AUDIT_ZH.md"
    lines = [
        "# Engineering Continuation Live Audit",
        "",
        f"- generatedAt: {datetime.now().isoformat()}",
        f"- status: {result.status}",
        f"- sessionId: {result.session_id}",
        f"- workspace: {result.workspace}",
        f"- modelProfile: {result.model_profile}",
        "",
        "## Findings",
    ]
    if result.findings:
        for finding in result.findings:
            lines.extend([f"- [{finding.severity}] {finding.code}: {finding.summary}", f"  - evidence: {finding.evidence[:1200]}"])
    else:
        lines.append("- No P0/P1/P2 findings.")
    lines.extend(["", "## Observed Topics", "```", "\n".join(result.observed_topics[:120]), "```"])
    report.write_text("\n".join(lines), encoding="utf-8")
    (report_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, default=lambda obj: obj.__dict__, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live Engineering continuation and scoped long-file patch audit.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--workspace", default=r"E:\Projects\test7")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--model-profile", default="")
    parser.add_argument("--max-wait", type=int, default=900)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-root", default=str(DEFAULT_REPORT_ROOT))
    args = parser.parse_args()
    if not args.live:
        print("Refusing to call live Engine without --live.")
        return 2
    if not args.allow_side_effects:
        print("This audit creates and patches workspace files; pass --allow-side-effects.")
        return 2
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    ok, _error = _wait_for_engine(args.engine_url, timeout=20)
    if not ok:
        print("Engine is unavailable.")
        return 1
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_profile = args.model_profile.strip() or "engine-default"
    session_id = f"engineering-continuation-live-{timestamp}"
    large_file = _prepare_long_file(workspace)
    result = ContinuationAuditResult(timestamp=timestamp, session_id=session_id, workspace=str(workspace), model_profile=model_profile)

    first = _submit_message(
        engine_url=args.engine_url,
        session_id=session_id,
        client_message_id=f"initial-{timestamp}",
        workspace=workspace,
        model_profile=model_profile,
        content=(
            "在当前工作区创建一个 debug-continuation-demo 小项目，写一个 Python 文件和 README，"
            "通过 Engineering Runtime 完成并返回写入路径与 proof。"
        ),
    )
    first = _poll_case(args.engine_url, first, max_wait=args.max_wait)

    second = _submit_message(
        engine_url=args.engine_url,
        session_id=session_id,
        client_message_id=f"debug-{timestamp}",
        workspace=workspace,
        model_profile=model_profile,
        content=(
            "刚才那个项目还是不行，运行日志如下：Traceback (most recent call last): "
            "NameError: name 'demo_config' is not defined。请续接上一轮工程上下文修复，不要新建会话。"
        ),
    )
    second = _poll_case(args.engine_url, second, max_wait=args.max_wait)

    third = _submit_message(
        engine_url=args.engine_url,
        session_id=session_id,
        client_message_id=f"long-patch-{timestamp}",
        workspace=workspace,
        model_profile=model_profile,
        content=(
            f"请精准修改已有长文件 {large_file} 的第 500 到 502 行，替换为三行："
            "line 0500: patched-by-continuation、line 0501: patched-by-continuation、"
            "line 0502: patched-by-continuation。必须用行号/片段 patch，不要全量重写。"
        ),
    )
    third = _poll_case(args.engine_url, third, max_wait=args.max_wait)

    _validate(result, engine_url=args.engine_url, session_id=session_id, workspace=workspace, large_file=large_file, first=first, second=second, third=third)
    result.status = "failed" if any(item.severity in {"P0", "P1"} for item in result.findings) else "ok"
    if args.write_report or result.findings:
        path = _write_report(result, Path(args.output_root).expanduser())
        print(f"report: {path}")
    if result.findings:
        for finding in result.findings:
            print(f"[{finding.severity}] {finding.code}: {finding.summary}")
    else:
        print("Engineering continuation live audit passed.")
    return 1 if any(item.severity in {"P0", "P1"} for item in result.findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
