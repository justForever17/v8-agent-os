from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.storage import storage  # noqa: E402
from tests.evals.longmemeval.harness import (  # noqa: E402
    LongMemEvalV8Harness,
    create_v8os_model_answerer,
)


DEFAULT_OFFICIAL_REPO = WORKSPACE_ROOT / "_external" / "LongMemEval"
DEFAULT_DATA_DIR = WORKSPACE_ROOT / "data" / "longmemeval"
DEFAULT_REPORT_ROOT = Path.home() / ".v8-agent-os" / "reports" / "longmemeval"
SUPPORTED_SPLITS = {
    "oracle": "longmemeval_oracle.json",
    "longmemeval_s_cleaned": "longmemeval_s_cleaned.json",
    "longmemeval_m_cleaned": "longmemeval_m_cleaned.json",
}
OPENAI_JUDGES = {"gpt-4o", "gpt-4o-mini"}
LOCAL_JUDGES = {"llama-3.1-70b-instruct"}


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_default_model() -> str:
    return (
        str(storage.get_role_model_id("memory") or "").strip()
        or str(storage.get_role_model_id("default") or "").strip()
        or str(storage.get_default_agent_model_id() or "").strip()
    )


def _official_python(official_repo: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    candidate = official_repo / ".venv-lite" / "Scripts" / "python.exe"
    return candidate if candidate.exists() else Path(sys.executable)


def _git_head(repo: Path) -> dict[str, str]:
    if not (repo / ".git").exists():
        return {"commit": "", "date": "", "subject": ""}
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo), "log", "-1", "--format=%H%x09%ci%x09%s"],
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()
        commit, date, subject = (output.split("\t", 2) + ["", "", ""])[:3]
        return {"commit": commit, "date": date, "subject": subject}
    except Exception:
        return {"commit": "", "date": "", "subject": ""}


def _local_judge_available() -> bool:
    try:
        with urlopen("http://localhost:8001/v1/models", timeout=3) as response:  # noqa: S310 - local-only probe
            return 200 <= int(response.status) < 300
    except Exception:
        return False


def _judge_preflight(judge_model: str) -> dict[str, Any]:
    if judge_model in OPENAI_JUDGES:
        return {
            "ok": bool(os.getenv("OPENAI_API_KEY")),
            "kind": "openai",
            "missing": [] if os.getenv("OPENAI_API_KEY") else ["OPENAI_API_KEY"],
        }
    if judge_model in LOCAL_JUDGES:
        return {
            "ok": _local_judge_available(),
            "kind": "local_openai_compatible",
            "missing": [] if _local_judge_available() else ["http://localhost:8001/v1/models"],
        }
    return {"ok": False, "kind": "unsupported", "missing": [f"unsupported judge model: {judge_model}"]}


def _parse_official_stdout(text: str) -> dict[str, Any]:
    overall = None
    by_type: dict[str, float] = {}
    for line in str(text or "").splitlines():
        stripped = line.strip()
        match = re.match(r"Accuracy:\s*([0-9.]+)", stripped)
        if match:
            overall = float(match.group(1))
            continue
        match = re.match(r"([A-Za-z0-9_-]+):\s*([0-9.]+)\s*\((\d+)\)", stripped)
        if match:
            by_type[match.group(1)] = float(match.group(2))
    return {"overallAccuracy": overall, "byQuestionType": by_type}


def _write_report(output_dir: Path, payload: dict[str, Any]) -> Path:
    report = output_dir / "LONGMEMEVAL_OFFICIAL_BENCHMARK_ZH.md"
    official = payload.get("officialEvaluation") or {}
    lines = [
        "# LongMemEval 官方 Harness Benchmark 报告",
        "",
        f"- 时间: `{payload.get('createdAt')}`",
        f"- split: `{payload.get('split')}`",
        f"- V8OS answer model: `{payload.get('modelProfile')}`",
        f"- question count: `{payload.get('questionCount')}`",
        f"- hypothesis: `{payload.get('hypothesisFile')}`",
        f"- official repo commit: `{(payload.get('officialRepo') or {}).get('commit')}`",
        f"- official repo date: `{(payload.get('officialRepo') or {}).get('date')}`",
        "",
        "## 官方评分状态",
        "",
        f"- available: `{bool(official.get('officialScoreAvailable'))}`",
        f"- judge model: `{official.get('judgeModel')}`",
        f"- status: `{official.get('status')}`",
    ]
    if official.get("blockingReason"):
        lines.append(f"- blocking reason: `{official.get('blockingReason')}`")
    if official.get("score"):
        lines.extend(
            [
                f"- overall accuracy: `{official['score'].get('overallAccuracy')}`",
                f"- by type: `{json.dumps(official['score'].get('byQuestionType') or {}, ensure_ascii=False)}`",
            ]
        )
    if official.get("stdoutFile"):
        lines.append(f"- official stdout: `{official.get('stdoutFile')}`")
    if official.get("stderrFile"):
        lines.append(f"- official stderr: `{official.get('stderrFile')}`")
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 本报告只有在 `officialScoreAvailable=true` 时才可视为 LongMemEval 官方 harness 分数。",
            "- V8OS 生成 hypothesis 与官方 judge 评分是两段独立流程；任何内部 smoke 分数都不得冒充官方成绩。",
        ]
    )
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def run(args: argparse.Namespace) -> dict[str, Any]:
    official_repo = Path(args.official_repo_root)
    data_dir = Path(args.data_dir)
    data_file = Path(args.input) if args.input else data_dir / SUPPORTED_SPLITS[args.split]
    model_profile = str(args.model_profile or "").strip() or _resolve_default_model()
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_REPORT_ROOT / _now_stamp()
    output_dir.mkdir(parents=True, exist_ok=True)
    hypothesis_file = output_dir / f"v8os_{args.split}_{'full' if args.limit is None else args.limit}.jsonl"

    payload: dict[str, Any] = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "split": args.split,
        "dataFile": str(data_file),
        "modelProfile": model_profile,
        "officialRepo": _git_head(official_repo),
        "hypothesisFile": str(hypothesis_file),
        "questionCount": 0,
        "officialEvaluation": {
            "judgeModel": args.judge_model,
            "officialScoreAvailable": False,
            "status": "not_started",
        },
    }

    if args.preflight:
        payload["preflight"] = {
            "officialRepoExists": official_repo.exists(),
            "dataFileExists": data_file.exists(),
            "officialPython": str(_official_python(official_repo, args.official_python)),
            "judge": _judge_preflight(args.judge_model),
            "modelProfile": model_profile,
        }
        _write_report(output_dir, payload)
        (output_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    if not args.live:
        raise SystemExit("Refusing to run LongMemEval live benchmark without --live.")
    if not data_file.exists():
        raise FileNotFoundError(f"LongMemEval data file not found: {data_file}")
    if not official_repo.exists():
        raise FileNotFoundError(f"Official LongMemEval repo not found: {official_repo}")
    if not model_profile:
        raise RuntimeError("No V8OS model profile resolved. Pass --model-profile explicitly.")

    answerer = create_v8os_model_answerer(model_id=model_profile, max_context_chars=args.max_context_chars)
    generation = LongMemEvalV8Harness(answerer=answerer).run_dataset(
        input_path=data_file,
        output_jsonl_path=hypothesis_file,
        split=args.split,
        limit=args.limit,
    )
    payload["questionCount"] = generation.get("questionCount")
    payload["generation"] = generation

    judge = _judge_preflight(args.judge_model)
    official = payload["officialEvaluation"]
    official["preflight"] = judge
    if not judge.get("ok"):
        official["status"] = "blocked"
        official["blockingReason"] = "judge_unavailable:" + ",".join(judge.get("missing") or [])
        _write_report(output_dir, payload)
        (output_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    official_python = _official_python(official_repo, args.official_python)
    evaluate_script = official_repo / "src" / "evaluation" / "evaluate_qa.py"
    stdout_file = output_dir / "official_evaluate_stdout.txt"
    stderr_file = output_dir / "official_evaluate_stderr.txt"
    command = [
        str(official_python),
        str(evaluate_script),
        args.judge_model,
        str(hypothesis_file),
        str(data_file),
    ]
    proc = subprocess.run(command, text=True, encoding="utf-8", capture_output=True, timeout=args.official_timeout_seconds)
    stdout_file.write_text(proc.stdout or "", encoding="utf-8")
    stderr_file.write_text(proc.stderr or "", encoding="utf-8")
    official.update(
        {
            "status": "completed" if proc.returncode == 0 else "failed",
            "returnCode": proc.returncode,
            "stdoutFile": str(stdout_file),
            "stderrFile": str(stderr_file),
            "resultFile": str(hypothesis_file) + f".eval-results-{args.judge_model}",
            "score": _parse_official_stdout(proc.stdout or ""),
            "officialScoreAvailable": proc.returncode == 0,
        }
    )
    _write_report(output_dir, payload)
    (output_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V8OS against the official LongMemEval harness.")
    parser.add_argument("--live", action="store_true", help="Required for model-backed hypothesis generation.")
    parser.add_argument("--preflight", action="store_true", help="Only check local official harness/data/judge readiness.")
    parser.add_argument("--split", choices=sorted(SUPPORTED_SPLITS), default="oracle")
    parser.add_argument("--input", default="", help="Explicit LongMemEval data JSON path.")
    parser.add_argument("--limit", type=int, default=None, help="Optional smoke sample size. Omit for full split.")
    parser.add_argument("--model-profile", default="", help="V8OS model ref. Defaults to memory/default role model.")
    parser.add_argument("--judge-model", choices=sorted(OPENAI_JUDGES | LOCAL_JUDGES), default="gpt-4o")
    parser.add_argument("--max-context-chars", type=int, default=24000)
    parser.add_argument("--official-timeout-seconds", type=int, default=7200)
    parser.add_argument("--official-repo-root", default=str(DEFAULT_OFFICIAL_REPO))
    parser.add_argument("--official-python", default="")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
