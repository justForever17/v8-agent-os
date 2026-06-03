from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.engine_config_resolver import (  # noqa: E402
    resolve_engine_config_for_model_ref,
    resolve_engine_config_for_role,
)
from core.storage import storage  # noqa: E402


DEFAULT_OFFICIAL_REPO = WORKSPACE_ROOT / "_external" / "LongMemEval-V2"
DEFAULT_DATA_ROOT = DEFAULT_OFFICIAL_REPO / "data" / "longmemeval-v2"
DEFAULT_REPORT_ROOT = Path.home() / ".v8-agent-os" / "reports" / "longmemeval_v2"


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _git_head(repo: Path) -> dict[str, str]:
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


def _resolve_default_model_profile() -> str:
    return (
        str(storage.get_role_model_id("memory") or "").strip()
        or str(storage.get_role_model_id("default") or "").strip()
        or str(storage.get_default_agent_model_id() or "").strip()
    )


def _normalize_model_lookup_key(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _find_configured_model_ref(model_profile: str) -> tuple[str, str] | None:
    """Resolve friendly model names against configured providers without exposing credentials."""
    profile = str(model_profile or "").strip()
    if not profile or "::" in profile:
        return None

    routes = storage.get_routes()
    providers = routes.get("providers") or {}
    normalized_profile = _normalize_model_lookup_key(profile)

    exact_matches: list[tuple[str, str]] = []
    normalized_matches: list[tuple[str, str]] = []
    for provider_id, provider_data in providers.items():
        models = (provider_data or {}).get("models") or {}
        for model_id, model_payload in models.items():
            model_id_text = str(model_id or "").strip()
            if model_id_text == profile:
                exact_matches.append((str(provider_id), model_id_text))
                continue
            candidates = [model_id_text]
            if isinstance(model_payload, dict):
                candidates.extend(
                    str(model_payload.get(key) or "").strip()
                    for key in [
                        "id",
                        "model_id",
                        "modelId",
                        "model_name",
                        "modelName",
                        "name",
                        "displayName",
                    ]
                    if str(model_payload.get(key) or "").strip()
                )
            if any(_normalize_model_lookup_key(item) == normalized_profile for item in candidates):
                normalized_matches.append((str(provider_id), model_id_text))

    matches = exact_matches or normalized_matches
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_openai_compatible_model(model_profile: str, *, role: str = "default") -> dict[str, Any]:
    profile = str(model_profile or "").strip()
    if profile:
        provider_id = ""
        model_ref = profile
        if "::" in profile:
            provider_id, model_ref = profile.split("::", 1)
        else:
            configured = _find_configured_model_ref(profile)
            if configured:
                provider_id, model_ref = configured
        resolved = resolve_engine_config_for_model_ref(
            model_ref,
            provider_id=provider_id,
            fallback_provider=provider_id or "openai",
            fallback_model=model_ref or "gpt-4o",
        )
    else:
        resolved = resolve_engine_config_for_role(role, fallback_provider="openai", fallback_model="gpt-4o")
    config = resolved["engine_config"]
    payload = asdict(config) if hasattr(config, "__dataclass_fields__") else dict(config)
    provider = str(payload.get("provider") or "")
    model_name = str(payload.get("model_name") or "")
    api_key = str(payload.get("api_key") or "")
    base_url = str(payload.get("base_url") or "")
    if not model_name:
        raise RuntimeError("Resolved LongMemEval reader model name is empty.")
    if not base_url:
        raise RuntimeError(f"Resolved provider {provider!r} has no OpenAI-compatible base_url.")
    if not api_key:
        raise RuntimeError(f"Resolved provider {provider!r} has no API key available for live benchmark.")
    return {
        "provider": provider,
        "model": model_name,
        "base_url": base_url,
        "api_key": api_key,
    }


def _run_subprocess(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    return {
        "command": command,
        "cwd": str(cwd),
        "startedAt": started,
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "returnCode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _data_download_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    # LongMemEval-V2 uses large Hugging Face files. On Windows and proxied
    # networks the Xet path is more prone to metadata/partial-read failures;
    # the classic HTTP path resumes more predictably.
    env.setdefault("HF_HUB_DISABLE_XET", "1")
    if str(getattr(args, "hf_endpoint", "") or "").strip():
        env["HF_ENDPOINT"] = str(args.hf_endpoint).strip()
    return env


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _metrics_score(metrics: dict[str, Any]) -> float | None:
    score = metrics.get("score")
    if isinstance(score, (int, float)):
        return float(score)
    overall = metrics.get("overall")
    if isinstance(overall, dict):
        overall_score = overall.get("overall_full_set")
        if isinstance(overall_score, (int, float)):
            return float(overall_score)
    return None


def _metrics_count(metrics: dict[str, Any]) -> int:
    count = metrics.get("count")
    if isinstance(count, int):
        return count
    overall = metrics.get("overall")
    if isinstance(overall, dict):
        overall_count = overall.get("count_all_questions")
        if isinstance(overall_count, int):
            return overall_count
    return 0


def _evaluation_summary(output_dir: Path, *, domain: str, tier: str) -> dict[str, Any]:
    metrics = _safe_read_json(output_dir / "aggregated_metrics.json")
    score = _metrics_score(metrics)
    per_question_path = output_dir / "per_question.jsonl"
    reader_errors = 0
    evaluator_errors = 0
    script_errors = 0
    taxonomy_counts: dict[str, int] = {}
    question_count = 0
    if per_question_path.exists():
        with per_question_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except Exception:
                    script_errors += 1
                    continue
                question_count += 1
                if item.get("reader_error"):
                    reader_errors += 1
                if item.get("evaluator_error") or item.get("score_error"):
                    evaluator_errors += 1
                metadata = item.get("memory_post_query_metadata")
                if isinstance(metadata, dict):
                    taxonomy = metadata.get("failureTaxonomy")
                    if isinstance(taxonomy, dict):
                        for key, value in taxonomy.items():
                            if value:
                                taxonomy_counts[str(key)] = taxonomy_counts.get(str(key), 0) + 1
    min_score = 0.382 if domain == "web" else 0.20
    return {
        "domain": domain,
        "tier": tier,
        "score": score,
        "questionCount": question_count or _metrics_count(metrics),
        "readerErrors": reader_errors,
        "evaluatorErrors": evaluator_errors,
        "scriptErrors": script_errors,
        "failureTaxonomyCounts": taxonomy_counts,
        "acceptance": {
            "scoreMin": min_score,
            "scorePassed": isinstance(score, (int, float)) and float(score) >= min_score,
            "readerErrorsPassed": reader_errors == 0,
            "evaluatorErrorsPassed": evaluator_errors == 0,
            "scriptErrorsPassed": script_errors == 0,
        },
    }


def _weighted_combined_summary(web_dir: Path, enterprise_dir: Path) -> dict[str, Any]:
    web = _evaluation_summary(web_dir, domain="web", tier="small")
    enterprise = _evaluation_summary(enterprise_dir, domain="enterprise", tier="small")
    web_score = web.get("score")
    enterprise_score = enterprise.get("score")
    web_count = int(web.get("questionCount") or 0)
    enterprise_count = int(enterprise.get("questionCount") or 0)
    combined: float | None = None
    if isinstance(web_score, (int, float)) and isinstance(enterprise_score, (int, float)) and web_count + enterprise_count > 0:
        combined = (float(web_score) * web_count + float(enterprise_score) * enterprise_count) / (web_count + enterprise_count)
    return {
        "web": web,
        "enterprise": enterprise,
        "weightedCombined": combined,
        "acceptance": {
            "webPassed": bool(web.get("acceptance", {}).get("scorePassed")),
            "enterprisePassed": bool(enterprise.get("acceptance", {}).get("scorePassed")),
            "combinedMin": 0.30,
            "combinedPassed": isinstance(combined, float) and combined >= 0.30,
            "readerErrorsPassed": int(web.get("readerErrors") or 0) == 0 and int(enterprise.get("readerErrors") or 0) == 0,
            "evaluatorErrorsPassed": int(web.get("evaluatorErrors") or 0) == 0 and int(enterprise.get("evaluatorErrors") or 0) == 0,
        },
    }


def _write_report(output_dir: Path, payload: dict[str, Any]) -> Path:
    metrics_path = output_dir / "aggregated_metrics.json"
    metrics: dict[str, Any] = {}
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            metrics = {}
    evaluation_summary = payload.get("evaluationSummary") or _evaluation_summary(
        output_dir,
        domain=str(payload.get("domain") or "web"),
        tier=str(payload.get("tier") or "small"),
    )
    report_path = output_dir / "LONGMEMEVAL_V2_OFFICIAL_BENCHMARK_ZH.md"
    lines = [
        "# LongMemEval-V2 官方 Harness Benchmark 报告",
        "",
        f"- 时间: `{payload.get('createdAt')}`",
        f"- 官方仓库: `{payload.get('officialRepoRoot')}`",
        f"- 官方 commit: `{(payload.get('officialRepo') or {}).get('commit')}`",
        f"- domain/tier: `{payload.get('domain')}` / `{payload.get('tier')}`",
        f"- method: `{payload.get('method')}`",
        f"- limit: `{payload.get('limit')}`",
        f"- reader provider/model: `{(payload.get('reader') or {}).get('provider')}` / `{(payload.get('reader') or {}).get('model')}`",
        f"- evaluator provider/model: `{(payload.get('evaluator') or {}).get('provider')}` / `{(payload.get('evaluator') or {}).get('model')}`",
        f"- status: `{payload.get('status')}`",
        "",
        "## 官方聚合指标",
        "",
        "```json",
        json.dumps(metrics, ensure_ascii=False, indent=2) if metrics else "{}",
        "```",
        "",
        "## 错误与验收摘要",
        "",
        "```json",
        json.dumps(evaluation_summary, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 边界说明",
        "",
        "- 本报告使用 LongMemEval-V2 官方 `evaluation.harness` 评分链路。",
        "- `v8os_context` 是实现官方 `memory_modules.Memory` 接口的 V8OS benchmark bridge。",
        "- API key 只通过子进程环境变量传递，不写入报告。",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _data_ready(data_root: Path) -> bool:
    required = [
        data_root / "questions.jsonl",
        data_root / "trajectories.jsonl",
        data_root / "haystacks" / "lme_v2_small.json",
    ]
    return all(path.exists() for path in required)


def run(args: argparse.Namespace) -> dict[str, Any]:
    official_repo = Path(args.official_repo_root).expanduser().resolve()
    data_root = Path(args.data_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else DEFAULT_REPORT_ROOT / _now_stamp()
    output_dir.mkdir(parents=True, exist_ok=True)
    python_exe = Path(args.python).expanduser().resolve() if args.python else Path(sys.executable).resolve()
    payload: dict[str, Any] = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "officialRepoRoot": str(official_repo),
        "officialRepo": _git_head(official_repo),
        "dataRoot": str(data_root),
        "outputDir": str(output_dir),
        "domain": args.domain,
        "tier": args.tier,
        "limit": args.limit,
        "method": args.method,
        "status": "preflight" if args.preflight else "not_started",
    }

    if not official_repo.exists():
        raise FileNotFoundError(f"LongMemEval-V2 repo not found: {official_repo}")

    if args.download_data:
        download_env = _data_download_env(args)
        result = _run_subprocess(
            [str(python_exe), str(official_repo / "data" / "download_data.py"), "--data-root", str(data_root)],
            cwd=official_repo,
            env=download_env,
            timeout=args.timeout_seconds,
        )
        _write_text(output_dir / "download_stdout.txt", result["stdout"])
        _write_text(output_dir / "download_stderr.txt", result["stderr"])
        payload["download"] = {k: v for k, v in result.items() if k not in {"stdout", "stderr"}}
        payload["download"]["hfEndpoint"] = download_env.get("HF_ENDPOINT", "https://huggingface.co")
        payload["download"]["hfHubDisableXet"] = download_env.get("HF_HUB_DISABLE_XET")
        if result["returnCode"] != 0:
            payload["status"] = "download_failed"
            (output_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return payload

    if args.prepare_data:
        result = _run_subprocess(
            [
                str(python_exe),
                str(official_repo / "data" / "prepare_data.py"),
                "--data-root",
                str(data_root),
                "--mode",
                args.prepare_mode,
            ],
            cwd=official_repo,
            env=os.environ.copy(),
            timeout=args.timeout_seconds,
        )
        _write_text(output_dir / "prepare_stdout.txt", result["stdout"])
        _write_text(output_dir / "prepare_stderr.txt", result["stderr"])
        payload["prepare"] = {k: v for k, v in result.items() if k not in {"stdout", "stderr"}}
        if result["returnCode"] != 0:
            payload["status"] = "prepare_failed"
            (output_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return payload

    if args.validate_data:
        result = _run_subprocess(
            [
                str(python_exe),
                str(official_repo / "data" / "validate_data.py"),
                "--data-root",
                str(data_root),
                "--tier",
                args.tier,
            ],
            cwd=official_repo,
            env=os.environ.copy(),
            timeout=args.timeout_seconds,
        )
        _write_text(output_dir / "validate_stdout.txt", result["stdout"])
        _write_text(output_dir / "validate_stderr.txt", result["stderr"])
        payload["validate"] = {k: v for k, v in result.items() if k not in {"stdout", "stderr"}}
        if result["returnCode"] != 0:
            payload["status"] = "validate_failed"
            (output_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return payload

    payload["dataReady"] = _data_ready(data_root)
    try:
        reader = _resolve_openai_compatible_model(args.model_profile, role="memory")
        evaluator = _resolve_openai_compatible_model(args.evaluator_model_profile or args.model_profile, role="memory")
        payload["reader"] = {k: v for k, v in reader.items() if k != "api_key"}
        payload["evaluator"] = {k: v for k, v in evaluator.items() if k != "api_key"}
    except Exception as exc:
        payload["modelResolutionError"] = str(exc)
        if not args.preflight:
            raise
        reader = evaluator = {}

    if args.preflight:
        payload["status"] = "preflight_ok" if payload.get("dataReady") and not payload.get("modelResolutionError") else "preflight_attention"
        _write_report(output_dir, payload)
        (output_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    if not args.live:
        raise SystemExit("Refusing to run LongMemEval-V2 live benchmark without --live.")
    if not payload["dataReady"]:
        raise FileNotFoundError(
            f"LongMemEval-V2 data is not prepared at {data_root}. Use --download-data --prepare-data --validate-data first."
        )

    env = _data_download_env(args)
    env["V8_LME_READER_API_KEY"] = str(reader["api_key"])
    env["V8_LME_EVALUATOR_API_KEY"] = str(evaluator["api_key"])
    command = [
        str(python_exe),
        str(official_repo / "evaluation" / "run_eval.py"),
        "--data-root",
        str(data_root),
        "--domain",
        args.domain,
        "--tier",
        args.tier,
        "--method",
        args.method,
        "--output-dir",
        str(output_dir),
        "--reader-model",
        str(reader["model"]),
        "--reader-base-url",
        str(reader["base_url"]),
        "--reader-api-key-env",
        "V8_LME_READER_API_KEY",
        "--reader-temperature",
        str(args.reader_temperature),
        "--reader-top-p",
        str(args.reader_top_p),
        "--reader-top-k",
        str(args.reader_top_k),
        "--reader-max-concurrent-requests",
        str(args.reader_max_concurrent_requests),
        "--reader-request-timeout-seconds",
        str(args.reader_request_timeout_seconds),
        "--reader-max-retries",
        str(args.reader_max_retries),
        "--max-completion-tokens",
        str(args.max_completion_tokens),
        "--memory-context-max-tokens",
        str(args.memory_context_max_tokens),
        "--evaluator-model",
        str(evaluator["model"]),
        "--evaluator-base-url",
        str(evaluator["base_url"]),
        "--evaluator-api-key-env",
        "V8_LME_EVALUATOR_API_KEY",
        "--evaluator-max-completion-tokens",
        str(args.evaluator_max_completion_tokens),
        "--evaluator-timeout-seconds",
        str(args.evaluator_timeout_seconds),
        "--prompt-build-max-workers",
        str(args.prompt_build_max_workers),
    ]
    if not args.memory_include_images:
        command.append("--no-v8os-context-include-images")
    if args.limit:
        command.extend(["--limit", str(args.limit)])
    if args.question_ids:
        command.extend(["--question-ids", args.question_ids])
    if args.disable_reader_thinking:
        command.append("--no-reader-enable-thinking")
    result = _run_subprocess(command, cwd=official_repo, env=env, timeout=args.timeout_seconds)
    _write_text(output_dir / "run_eval_stdout.txt", result["stdout"])
    _write_text(output_dir / "run_eval_stderr.txt", result["stderr"])
    payload["runEval"] = {k: v for k, v in result.items() if k not in {"stdout", "stderr"}}
    payload["status"] = "completed" if result["returnCode"] == 0 else "failed"
    payload["evaluationSummary"] = _evaluation_summary(output_dir, domain=args.domain, tier=args.tier)
    _write_report(output_dir, payload)
    (output_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V8OS against the official LongMemEval-V2 harness.")
    parser.add_argument("--live", action="store_true", help="Required to call real reader/evaluator models.")
    parser.add_argument("--preflight", action="store_true", help="Only check repo/data/model readiness.")
    parser.add_argument("--download-data", action="store_true", help="Download official LongMemEval-V2 data from Hugging Face.")
    parser.add_argument("--prepare-data", action="store_true", help="Prepare screenshot links/copies.")
    parser.add_argument("--validate-data", action="store_true", help="Run official data validation.")
    parser.add_argument("--prepare-mode", choices=["symlink", "copy"], default="symlink")
    parser.add_argument("--hf-endpoint", default="", help="Optional Hugging Face endpoint/mirror for data download.")
    parser.add_argument("--domain", choices=["web", "enterprise"], default="web")
    parser.add_argument("--tier", choices=["small", "medium"], default="small")
    parser.add_argument("--method", choices=["v8os_context", "v8os_context_v2"], default="v8os_context")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--question-ids", default="")
    parser.add_argument("--model-profile", default="", help="V8OS model profile, e.g. provider::model. Defaults to memory/default role.")
    parser.add_argument("--evaluator-model-profile", default="", help="Optional separate V8OS evaluator model profile.")
    parser.add_argument("--reader-temperature", type=float, default=0.2)
    parser.add_argument("--reader-top-p", type=float, default=0.8)
    parser.add_argument("--reader-top-k", type=int, default=20)
    parser.add_argument("--reader-max-concurrent-requests", type=int, default=2)
    parser.add_argument("--reader-request-timeout-seconds", type=float, default=240.0)
    parser.add_argument("--reader-max-retries", type=int, default=2)
    parser.add_argument("--max-completion-tokens", type=int, default=4096)
    parser.add_argument("--memory-context-max-tokens", type=int, default=180000)
    parser.add_argument("--memory-include-images", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--evaluator-max-completion-tokens", type=int, default=4096)
    parser.add_argument("--evaluator-timeout-seconds", type=float, default=240.0)
    parser.add_argument("--prompt-build-max-workers", type=int, default=1)
    parser.add_argument("--disable-reader-thinking", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=43200)
    parser.add_argument("--official-repo-root", default=str(DEFAULT_OFFICIAL_REPO))
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--python", default=str(ENGINE_ROOT / ".venv" / "Scripts" / "python.exe"))
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({k: v for k, v in result.items() if k not in {"reader", "evaluator"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
