from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def _domain_summary(output_dir: Path, *, domain: str) -> dict[str, Any]:
    metrics = _safe_read_json(output_dir / "aggregated_metrics.json")
    score = _metrics_score(metrics)
    count = _metrics_count(metrics)
    reader_errors = 0
    evaluator_errors = 0
    script_errors = 0
    taxonomy_counts: dict[str, int] = {}
    per_question = output_dir / "per_question.jsonl"
    if per_question.exists():
        count = 0
        with per_question.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except Exception:
                    script_errors += 1
                    continue
                count += 1
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
        "outputDir": str(output_dir),
        "score": score,
        "count": count,
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


def _combined(web: dict[str, Any], enterprise: dict[str, Any]) -> dict[str, Any]:
    web_score = web.get("score")
    ent_score = enterprise.get("score")
    web_count = int(web.get("count") or 0)
    ent_count = int(enterprise.get("count") or 0)
    weighted = None
    if isinstance(web_score, (int, float)) and isinstance(ent_score, (int, float)) and web_count + ent_count > 0:
        weighted = (float(web_score) * web_count + float(ent_score) * ent_count) / (web_count + ent_count)
    return {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "web": web,
        "enterprise": enterprise,
        "weightedCombined": weighted,
        "acceptance": {
            "webPassed": bool(web.get("acceptance", {}).get("scorePassed")),
            "enterprisePassed": bool(enterprise.get("acceptance", {}).get("scorePassed")),
            "combinedMin": 0.30,
            "combinedPassed": isinstance(weighted, float) and weighted >= 0.30,
            "readerErrorsPassed": int(web.get("readerErrors") or 0) == 0 and int(enterprise.get("readerErrors") or 0) == 0,
            "evaluatorErrorsPassed": int(web.get("evaluatorErrors") or 0) == 0 and int(enterprise.get("evaluatorErrors") or 0) == 0,
            "scriptErrorsPassed": int(web.get("scriptErrors") or 0) == 0 and int(enterprise.get("scriptErrors") or 0) == 0,
        },
    }


def _write_report(output_dir: Path, payload: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "LONGMEMEVAL_V2_COMBINED_SUMMARY_ZH.md"
    lines = [
        "# LongMemEval-V2 Web / Enterprise Weighted Combined 汇总",
        "",
        f"- 时间: `{payload['createdAt']}`",
        f"- web score: `{payload['web'].get('score')}`",
        f"- enterprise score: `{payload['enterprise'].get('score')}`",
        f"- weighted combined: `{payload.get('weightedCombined')}`",
        "",
        "## 严格验收",
        "",
        "```json",
        json.dumps(payload["acceptance"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 明细",
        "",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    (output_dir / "combined_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize LongMemEval-V2 web/enterprise small scores.")
    parser.add_argument("--web-output-dir", required=True)
    parser.add_argument("--enterprise-output-dir", required=True)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()
    web = _domain_summary(Path(args.web_output_dir).expanduser().resolve(), domain="web")
    enterprise = _domain_summary(Path(args.enterprise_output_dir).expanduser().resolve(), domain="enterprise")
    payload = _combined(web, enterprise)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else Path.home() / ".v8-agent-os" / "reports" / "longmemeval_v2" / "combined_latest"
    report_path = _write_report(output_dir, payload)
    print(json.dumps({"report": str(report_path), "summary": payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
