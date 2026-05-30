from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[2]
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports")) / "tool_surface"

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from langchain_core.messages import ToolMessage  # noqa: E402

from core.tool_surface import apply_tool_surface_budget  # noqa: E402
from core.tools.web_fetcher import web_broker  # noqa: E402
from runtimes.extensions.skills.loader import fetch_skill_instructions  # noqa: E402


CONTROL_KEY_RE = re.compile(
    r"(?i)\"(?:ok|debug|trace|traceRef|internalControl|sourceQualityHints|_v8ToolSurface|rawProviderResponse)\""
)


@dataclass
class SurfaceCase:
    case_id: str
    title: str
    tool_name: str
    raw_factory: Callable[[bool], str]
    expected_terms: list[str] = field(default_factory=list)


@dataclass
class SurfaceCaseResult:
    case_id: str
    title: str
    tool_name: str
    ok: bool
    elapsed_ms: int
    visible_chars: int = 0
    starts_json: bool = False
    has_control_json: bool = False
    missing_terms: list[str] = field(default_factory=list)
    error: str = ""
    visible_preview: str = ""


def _redact(text: str) -> str:
    redacted = str(text or "")
    for raw_path, replacement in ((Path.home(), "~"), (REPO_ROOT, "<REPO_ROOT>"), (ENGINE_ROOT, "<ENGINE_ROOT>")):
        path_text = str(raw_path)
        redacted = redacted.replace(path_text, replacement).replace(path_text.replace("\\", "\\\\"), replacement)
    return redacted


def _invoke_tool(tool: Any, kwargs: dict[str, Any]) -> str:
    if hasattr(tool, "func"):
        return str(tool.func(**kwargs))
    if hasattr(tool, "invoke"):
        return str(tool.invoke(kwargs))
    return str(tool(**kwargs))


def _surface(tool_name: str, raw: str, *, budget: int = 2600) -> str:
    message = ToolMessage(content=raw, name=tool_name, tool_call_id=f"live-{tool_name}-{int(time.time() * 1000)}")
    return str(apply_tool_surface_budget(message, {"agentVisibleBudget": budget}, tool_name=tool_name).content)


def _fixture_delegation(_: bool) -> str:
    return json.dumps(
        {
            "ok": True,
            "mode": "dispatch",
            "summary": "Prepared two worker tasks for review.",
            "tasks": [
                {
                    "taskGoal": "Validate the evidence bundle against source URLs.",
                    "target": "research-reviewer",
                    "status": "started",
                    "selectionTrace": {"scores": [0.91, 0.86]},
                },
                {
                    "taskGoal": "Draft implementation risks and proof requirements.",
                    "target": "engineering-reviewer",
                    "status": "queued",
                },
            ],
            "internalControl": {"lease": "diag-only"},
        },
        ensure_ascii=False,
    )


def _fixture_generic(_: bool) -> str:
    return json.dumps(
        {
            "ok": True,
            "summary": "Collected facts and prepared a compact answer.",
            "results": [{"title": "Primary source", "url": "https://example.com/source", "snippet": "Useful evidence."}],
            "internalControl": {"token": "diag-only"},
        },
        ensure_ascii=False,
    )


def _skill_raw(_: bool) -> str:
    return _invoke_tool(fetch_skill_instructions, {"skill_name": "huashu-nuwa"})


def _web_search_raw(live: bool) -> str:
    if not live:
        return json.dumps(
            {
                "ok": True,
                "mode": "search",
                "query": "V8 Agent OS runtime episodes",
                "results": [
                    {
                        "title": "Runtime Episode Fabric",
                        "url": "https://example.com/runtime",
                        "snippet": "Canonical runtime scheduling and typed handoff.",
                    }
                ],
            },
            ensure_ascii=False,
        )
    return _invoke_tool(web_broker, {"target": "OpenAI Responses API official docs", "mode": "search", "limit": 3})


def _web_read_raw(live: bool) -> str:
    if not live:
        return json.dumps(
            {
                "ok": True,
                "mode": "read",
                "title": "Example Domain",
                "finalUrl": "https://example.com",
                "textPreview": "This domain is for use in illustrative examples.",
            },
            ensure_ascii=False,
        )
    return _invoke_tool(web_broker, {"target": "https://example.com", "mode": "read", "limit": 3})


CASES: dict[str, SurfaceCase] = {
    "web_search": SurfaceCase(
        "web_search",
        "Live web_broker search should expose ranked sources, not control JSON",
        "web_broker",
        _web_search_raw,
        ["Web broker", "Sources:", "tool_observation_detail"],
    ),
    "web_read": SurfaceCase(
        "web_read",
        "Live web_broker read should expose page content and URL",
        "web_broker",
        _web_read_raw,
        ["Web broker", "URL:", "Content:"],
    ),
    "skill": SurfaceCase(
        "skill",
        "fetch_skill_instructions should expose executable SKILL.md instructions without loader paths",
        "fetch_skill_instructions",
        _skill_raw,
        ["Skill instructions", "Instructions:", "女娲"],
    ),
    "delegation": SurfaceCase(
        "delegation",
        "delegation_broker JSON should expose tasks without selection diagnostics",
        "delegation_broker",
        _fixture_delegation,
        ["Delegation broker", "Tasks:"],
    ),
    "generic": SurfaceCase(
        "generic",
        "Unknown JSON tool should degrade to compact summary plus detail tool",
        "new_experimental_tool",
        _fixture_generic,
        ["new experimental tool result", "tool_observation_detail"],
    ),
}


def _run_case(case: SurfaceCase, *, live: bool) -> SurfaceCaseResult:
    started = time.perf_counter()
    try:
        raw = case.raw_factory(live)
        visible = _surface(case.tool_name, raw)
        stripped = visible.lstrip()
        missing = [term for term in case.expected_terms if term not in visible]
        starts_json = stripped.startswith("{") or stripped.startswith("[")
        has_control_json = bool(CONTROL_KEY_RE.search(visible))
        ok = not starts_json and not has_control_json and not missing
        return SurfaceCaseResult(
            case_id=case.case_id,
            title=case.title,
            tool_name=case.tool_name,
            ok=ok,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            visible_chars=len(visible),
            starts_json=starts_json,
            has_control_json=has_control_json,
            missing_terms=missing,
            visible_preview=_redact(visible[:1600]),
        )
    except Exception as exc:  # pragma: no cover - script diagnostics
        return SurfaceCaseResult(
            case_id=case.case_id,
            title=case.title,
            tool_name=case.tool_name,
            ok=False,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}",
        )


def _write_report(results: list[SurfaceCaseResult], *, live: bool) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = DEFAULT_REPORT_ROOT / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "TOOL_SURFACE_LIVE_AUDIT_ZH.md"
    passed = sum(1 for item in results if item.ok)
    lines = [
        "# Tool Surface Live Audit",
        "",
        f"- live: `{str(live).lower()}`",
        f"- generatedAt: `{datetime.now(timezone.utc).isoformat()}`",
        f"- passed: `{passed}/{len(results)}`",
        "",
        "## 结论",
        "",
    ]
    if passed == len(results):
        lines.append("所有检查项都通过：agent-visible 输出没有原始 JSON/control envelope，并保留了必要正文、来源或 detailTool。")
    else:
        lines.append("存在失败项，优先处理 starts_json / has_control_json / missing_terms。")
    lines.append("")
    lines.append("## Case Results")
    for result in results:
        lines.extend(
            [
                "",
                f"### {result.case_id} - {'PASS' if result.ok else 'FAIL'}",
                "",
                f"- tool: `{result.tool_name}`",
                f"- title: {result.title}",
                f"- elapsedMs: `{result.elapsed_ms}`",
                f"- visibleChars: `{result.visible_chars}`",
                f"- startsJson: `{str(result.starts_json).lower()}`",
                f"- hasControlJson: `{str(result.has_control_json).lower()}`",
                f"- missingTerms: `{', '.join(result.missing_terms) if result.missing_terms else 'none'}`",
            ]
        )
        if result.error:
            lines.extend(["", "Error:", "", "```text", _redact(result.error), "```"])
        if result.visible_preview:
            lines.extend(["", "Visible preview:", "", "```text", result.visible_preview, "```"])
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live/dry-run audit for V8 tool surface double-layer output.")
    parser.add_argument("--live", action="store_true", help="Allow real live tool calls such as web_broker network access.")
    parser.add_argument("--case", choices=["all", *CASES.keys()], default="all")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    selected = list(CASES.values()) if args.case == "all" else [CASES[args.case]]
    results = [_run_case(case, live=bool(args.live)) for case in selected]
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.case_id} {result.elapsed_ms}ms")
        if result.error:
            print(_redact(result.error.splitlines()[0]))
        if result.missing_terms:
            print("  missing:", ", ".join(result.missing_terms))
        if result.starts_json or result.has_control_json:
            print(f"  startsJson={result.starts_json} hasControlJson={result.has_control_json}")
    if args.write_report:
        global DEFAULT_REPORT_ROOT
        if args.output_dir:
            DEFAULT_REPORT_ROOT = Path(args.output_dir).expanduser()
        report_path = _write_report(results, live=bool(args.live))
        print(f"report={report_path}")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
