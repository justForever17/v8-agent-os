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
    elapsed_ms: int = 0
    providers: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def _redact(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = TOKEN_RE.sub(lambda match: f"{match.group(1) or match.group(2)}[REDACTED]", text)
    text = text.replace(str(Path.home()), "~")
    return text


def _research_run(
    question: str,
    *,
    source_policy: str = "authoritative",
    freshness: str = "auto",
    max_shards: int = 2,
    max_rounds: int = 2,
) -> dict[str, Any]:
    from core.tools.research_broker import research_broker

    raw = research_broker.func(
        mode="run",
        question=question,
        sourcePolicy=source_policy,
        freshness=freshness,
        maxShards=max_shards,
        maxRounds=max_rounds,
        tool_call_id=f"live-research-deep-{int(time.time())}",
    )
    return json.loads(raw)


def _run_technical_case() -> AuditCaseResult:
    result = AuditCaseResult("technical", "公开技术问题：Source Router + 全文读取 + claimTable")
    started = time.perf_counter()
    try:
        payload = _research_run("What are the current best practices for using Python pathlib in CLI tools? cite official sources.", freshness="latest")
        final_pack = payload.get("finalExperiencePack") if isinstance(payload.get("finalExperiencePack"), dict) else {}
        synthesis_mode = str(final_pack.get("synthesisMode") or "")
        result.status = "ok" if payload.get("ok") and payload.get("claimTable") else "warning"
        result.summary = f"{str(payload.get('summary') or '')[:520]} synthesis={synthesis_mode or 'unknown'}"
        result.providers = sorted({str(item.get("provider") or "") for item in payload.get("sourceMatrix") or [] if isinstance(item, dict) and item.get("provider")})
        if not payload.get("claimTable"):
            result.failures.append("missing_claim_table")
        if not payload.get("researchLoopState"):
            result.failures.append("missing_research_loop_state")
        if synthesis_mode != "model_agent":
            result.status = "warning" if result.status == "ok" else result.status
            result.failures.append(f"architect_agent_not_used:{synthesis_mode or 'unknown'}")
        result.evidence.append(_redact(payload))
    except Exception as exc:  # noqa: BLE001 - live audit reports diagnostic failure.
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_cn_case() -> AuditCaseResult:
    result = AuditCaseResult("cn", "中文国内问题：CN/global provider 自动切换与降级")
    started = time.perf_counter()
    try:
        payload = _research_run("秘塔搜索 API 的 search scope 支持哪些类型？请说明来源。", max_shards=2, max_rounds=2)
        result.status = "ok" if payload.get("ok") or payload.get("providerAttemptMatrix") else "warning"
        result.summary = str(payload.get("summary") or payload.get("answer") or "")[:600]
        routes = {str(item.get("networkRoute") or "") for item in payload.get("sourceMatrix") or [] if isinstance(item, dict)}
        if not routes and not payload.get("providerAttemptMatrix"):
            result.failures.append("missing_network_route_or_provider_attempts")
        result.evidence.append(_redact(payload))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_continuation_case() -> AuditCaseResult:
    result = AuditCaseResult("continuation", "长页面读取：raw_ref 续读稳定")
    started = time.perf_counter()
    try:
        from langchain_core.messages import ToolMessage

        from core.native_tools import tool_observation_detail
        from core.tool_surface import apply_tool_surface_budget
        from core.tools.web_fetcher import web_read

        raw = web_read.func(url="https://docs.python.org/3/library/pathlib.html", mode="static", tool_call_id="live-research-continuation")
        message = ToolMessage(content=raw, tool_call_id="live-research-continuation", name="web_read")
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
            result.evidence.append(_redact(content[:1600]))
        else:
            detail = tool_observation_detail.invoke({"raw_ref": match.group(1), "max_chars": 8000})
            result.status = "ok" if "pathlib" in str(detail).lower() else "warning"
            result.evidence.append(_redact({"visible": content[:1600], "detailPreview": str(detail)[:3000]}))
            if result.status != "ok":
                result.failures.append("detail_did_not_include_expected_page_content")
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_reuse_case() -> AuditCaseResult:
    result = AuditCaseResult("reuse", "同题连续两次：经验包复用或可解释增量刷新")
    started = time.perf_counter()
    try:
        question = "Research Runtime Source Router evidence pack reuse validation"
        first = _research_run(question, max_shards=1, max_rounds=2)
        second = _research_run(question, max_shards=1, max_rounds=2)
        reuse = second.get("experienceReuse") or {}
        result.status = "ok" if reuse.get("reuseDecision") in {"reuse", "refresh"} else "warning"
        result.summary = f"reuseDecision={reuse.get('reuseDecision')} reason={reuse.get('reason')}"
        if reuse.get("reuseDecision") not in {"reuse", "refresh"}:
            result.failures.append("second_run_did_not_report_reuse_or_refresh_decision")
        result.evidence.append(_redact({"first": first, "second": second}))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


def _run_conflict_case() -> AuditCaseResult:
    result = AuditCaseResult("conflict", "冲突来源：conflictMatrix 与不确定性表达")
    started = time.perf_counter()
    try:
        payload = _research_run("Compare claims that Python pathlib is deprecated versus recommended in current Python docs.", max_shards=2, max_rounds=2)
        result.status = "ok" if payload.get("conflictMatrix") or payload.get("claimTable") else "warning"
        result.summary = str(payload.get("summary") or "")[:600]
        if not payload.get("conflictMatrix"):
            result.failures.append("conflict_matrix_empty_or_no_conflict_detected")
        result.evidence.append(_redact(payload))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failures.append(_redact(f"{type(exc).__name__}: {exc}"))
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result


CASES = {
    "technical": _run_technical_case,
    "cn": _run_cn_case,
    "continuation": _run_continuation_case,
    "reuse": _run_reuse_case,
    "conflict": _run_conflict_case,
}


def _write_report(results: list[AuditCaseResult], output_root: Path) -> Path:
    report_dir = output_root / "research_runtime_deep" / datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "RESEARCH_RUNTIME_DEEP_LIVE_AUDIT_ZH.md"
    lines = [
        "# Research Runtime Deep Live Audit",
        "",
        f"- generatedAt: {datetime.now().isoformat()}",
        f"- cases: {len(results)}",
        "",
    ]
    for item in results:
        lines.extend(
            [
                f"## {item.case_id} - {item.title}",
                "",
                f"- status: {item.status}",
                f"- elapsedMs: {item.elapsed_ms}",
                f"- providers: {', '.join(item.providers) if item.providers else 'n/a'}",
                f"- summary: {item.summary or 'n/a'}",
            ]
        )
        if item.failures:
            lines.append(f"- failures: {'; '.join(item.failures)}")
        lines.append("")
        lines.append("<details><summary>Evidence</summary>")
        lines.append("")
        for evidence in item.evidence:
            lines.append("```json")
            lines.append(evidence[:12000])
            lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live Research Runtime deep audit.")
    parser.add_argument("--live", action="store_true", help="Required to perform network/provider live calls.")
    parser.add_argument("--case", choices=[*CASES.keys(), "all"], default="all")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_ROOT)
    args = parser.parse_args()
    if not args.live:
        print("Refusing to run live audit without --live.")
        return 2
    selected = list(CASES.keys()) if args.case == "all" else [args.case]
    results = [CASES[case_id]() for case_id in selected]
    for item in results:
        print(f"[{item.status}] {item.case_id}: {item.summary or '; '.join(item.failures) or item.title}")
    if args.write_report:
        path = _write_report(results, args.output_dir)
        print(f"report={path}")
    return 1 if any(item.status == "failed" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
