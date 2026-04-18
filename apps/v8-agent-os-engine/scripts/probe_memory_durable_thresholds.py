from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

if "chromadb" not in sys.modules:
    class _FakeChromaCollection:
        def upsert(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

        def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {}

    class _FakeChromaClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def get_or_create_collection(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeChromaCollection()

    sys.modules["chromadb"] = type("chromadb", (), {"PersistentClient": _FakeChromaClient})()

from agents.memory_agent import (  # noqa: E402
    KnowledgeExtraction,
    MemoryExtractionAttempt,
    MemoryExtractionResult,
    PreferenceExtraction,
    _evaluate_knowledge_persistence,
    _evaluate_preference_persistence,
    _extract_with_llm,
    _load_memory_policy,
)
from core.storage import MEMORY_DURABLE_POLICY_DEFAULTS  # noqa: E402


FIXTURE_PATH = ENGINE_ROOT / "tests" / "fixtures" / "memory" / "session_replay_cases.json"


def _default_policy() -> dict[str, Any]:
    return {
        "extraction_enabled": True,
        **MEMORY_DURABLE_POLICY_DEFAULTS,
    }


def _load_fixture_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("cases") or [])


def _attempt_from_case(case: dict[str, Any]) -> MemoryExtractionAttempt:
    failure = case.get("extractionFailure")
    if isinstance(failure, dict):
        return MemoryExtractionAttempt(
            result=None,
            failure_stage=str(failure.get("failureStage") or ""),
            failure_reason=str(failure.get("failureReason") or ""),
            extractor_model=str(failure.get("extractorModel") or ""),
            raw_output_preview=str(failure.get("rawOutputPreview") or ""),
            parser_error_preview=str(failure.get("parserErrorPreview") or ""),
        )
    result = MemoryExtractionResult.model_validate(case["extraction"])
    return MemoryExtractionAttempt(result=result, extractor_model="fixture-memory-extractor")


def _scan_preference(pref: PreferenceExtraction, policy: dict[str, Any]) -> dict[str, Any]:
    passes, reason = _evaluate_preference_persistence(pref, policy)
    base_importance = int(pref.importance or 0)
    base_confidence = float(pref.confidence or 0.0)

    first_importance = None
    for candidate in range(max(0, base_importance - 20), min(100, base_importance + 20) + 1):
        mutated = pref.model_copy(deep=True)
        mutated.importance = candidate
        allowed, _ = _evaluate_preference_persistence(mutated, policy)
        if allowed:
            first_importance = candidate
            break

    first_confidence = None
    confidence_values = [round(max(0.0, min(1.0, base_confidence + delta)), 2) for delta in [x / 100 for x in range(-20, 21, 2)]]
    for candidate in sorted(dict.fromkeys(confidence_values)):
        mutated = pref.model_copy(deep=True)
        mutated.confidence = candidate
        allowed, _ = _evaluate_preference_persistence(mutated, policy)
        if allowed:
            first_confidence = candidate
            break

    return {
        "kind": "preference",
        "key": pref.key,
        "scope": pref.scope,
        "value": pref.value,
        "importance": base_importance,
        "confidence": base_confidence,
        "passes": passes,
        "decision": reason,
        "thresholdScan": {
            "firstPassingImportanceNearCurrent": first_importance,
            "firstPassingConfidenceNearCurrent": first_confidence,
            "importanceWindow": [max(0, base_importance - 20), min(100, base_importance + 20)],
            "confidenceWindow": [max(0.0, round(base_confidence - 0.20, 2)), min(1.0, round(base_confidence + 0.20, 2))],
        },
    }


def _scan_knowledge(item: KnowledgeExtraction, policy: dict[str, Any]) -> dict[str, Any]:
    passes, reason = _evaluate_knowledge_persistence(item, policy)
    base_importance = int(item.importance or 0)
    base_confidence = float(item.confidence or 0.0)

    first_importance = None
    for candidate in range(max(0, base_importance - 20), min(100, base_importance + 20) + 1):
        mutated = item.model_copy(deep=True)
        mutated.importance = candidate
        allowed, _ = _evaluate_knowledge_persistence(mutated, policy)
        if allowed:
            first_importance = candidate
            break

    first_confidence = None
    confidence_values = [round(max(0.0, min(1.0, base_confidence + delta)), 2) for delta in [x / 100 for x in range(-20, 21, 2)]]
    for candidate in sorted(dict.fromkeys(confidence_values)):
        mutated = item.model_copy(deep=True)
        mutated.confidence = candidate
        allowed, _ = _evaluate_knowledge_persistence(mutated, policy)
        if allowed:
            first_confidence = candidate
            break

    return {
        "kind": "knowledge",
        "scope": item.scope,
        "category": item.category,
        "fact": item.fact,
        "durability": item.durability,
        "importance": base_importance,
        "confidence": base_confidence,
        "passes": passes,
        "decision": reason,
        "thresholdScan": {
            "firstPassingImportanceNearCurrent": first_importance,
            "firstPassingConfidenceNearCurrent": first_confidence,
            "importanceWindow": [max(0, base_importance - 20), min(100, base_importance + 20)],
            "confidenceWindow": [max(0.0, round(base_confidence - 0.20, 2)), min(1.0, round(base_confidence + 0.20, 2))],
        },
    }


def _report_case(case_id: str, attempt: MemoryExtractionAttempt, policy: dict[str, Any]) -> dict[str, Any]:
    if attempt.result is None:
        return {
            "id": case_id,
            "status": "failed",
            "failureStage": attempt.failure_stage or "llm_response_empty",
            "failureReason": attempt.failure_reason or "No structured extraction result.",
            "extractorModel": attempt.extractor_model,
            "rawOutputPreview": attempt.raw_output_preview,
            "parserErrorPreview": attempt.parser_error_preview,
            "items": [],
        }

    preference_items = [_scan_preference(pref, policy) for pref in attempt.result.preferences]
    knowledge_items = [_scan_knowledge(item, policy) for item in attempt.result.knowledge]
    persisted_knowledge = [item for item in knowledge_items if item["passes"]]
    return {
        "id": case_id,
        "status": "completed",
        "summary": attempt.result.summary,
        "tags": attempt.result.tags,
        "extractorModel": attempt.extractor_model,
        "preferenceItems": preference_items,
        "knowledgeItems": knowledge_items,
        "graphWouldGrow": bool(persisted_knowledge and attempt.result.relations),
        "graphRelationCountIfPersisted": len(attempt.result.relations) if persisted_knowledge else 0,
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Durable Threshold Probe",
        "",
        f"- policySource: `{report['policySource']}`",
        f"- fixturePath: `{report.get('fixturePath') or ''}`",
        "",
        "## 当前阈值",
        "",
    ]
    for key, value in report["policy"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Case 结果", ""])
    for case in report["cases"]:
        lines.append(f"### {case['id']}")
        if case["status"] == "failed":
            lines.append(f"- extractorFailureStage: `{case['failureStage']}`")
            lines.append(f"- extractorFailureReason: {case['failureReason']}")
            if case.get("parserErrorPreview"):
                lines.append(f"- parserErrorPreview: `{case['parserErrorPreview']}`")
            if case.get("rawOutputPreview"):
                lines.append(f"- rawOutputPreview: `{case['rawOutputPreview']}`")
            lines.append("")
            continue
        lines.append(f"- summary: {case['summary']}")
        lines.append(f"- graphWouldGrow: `{case['graphWouldGrow']}`")
        if case["preferenceItems"]:
            lines.append("- preferenceItems:")
            for item in case["preferenceItems"]:
                lines.append(
                    f"  - `{item['key']}` => `{item['decision']}` "
                    f"(importance={item['importance']}, confidence={item['confidence']}, "
                    f"firstPassingImportance={item['thresholdScan']['firstPassingImportanceNearCurrent']}, "
                    f"firstPassingConfidence={item['thresholdScan']['firstPassingConfidenceNearCurrent']})"
                )
        if case["knowledgeItems"]:
            lines.append("- knowledgeItems:")
            for item in case["knowledgeItems"]:
                lines.append(
                    f"  - `{item['category']}` / `{item['scope']}` => `{item['decision']}` "
                    f"(importance={item['importance']}, confidence={item['confidence']}, durability={item['durability']}, "
                    f"firstPassingImportance={item['thresholdScan']['firstPassingImportanceNearCurrent']}, "
                    f"firstPassingConfidence={item['thresholdScan']['firstPassingConfidenceNearCurrent']})"
                )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe durable memory thresholds with fixture or live transcript input.")
    parser.add_argument("--fixture-path", default=str(FIXTURE_PATH), help="Path to session replay fixture JSON.")
    parser.add_argument("--case", action="append", help="Fixture case id to run. Can be repeated.")
    parser.add_argument("--transcript-file", help="Plain text transcript file. Uses live extractor instead of fixture extraction.")
    parser.add_argument("--scope", default="global", help="Resolved scope when running a raw transcript through the extractor.")
    parser.add_argument("--policy-source", choices=("current", "defaults"), default="current", help="Use current config thresholds or built-in defaults.")
    parser.add_argument("--output-dir", help="Optional directory to write report.json and report.md.")
    args = parser.parse_args()

    policy = _load_memory_policy() if args.policy_source == "current" else _default_policy()
    policy["extraction_enabled"] = True

    cases_report: list[dict[str, Any]] = []
    fixture_path = Path(args.fixture_path)
    if args.transcript_file:
        transcript_text = Path(args.transcript_file).read_text(encoding="utf-8")
        attempt = _extract_with_llm(
            transcript_text,
            "No prior knowledge retrieved.",
            resolved_scope=args.scope,
            scope_chain=[args.scope] if args.scope == "global" else [args.scope, "global"],
        )
        cases_report.append(_report_case(Path(args.transcript_file).stem, attempt, policy))
    else:
        fixture_cases = _load_fixture_cases(fixture_path)
        selected = set(args.case or [])
        for case in fixture_cases:
            if selected and case["id"] not in selected:
                continue
            cases_report.append(_report_case(case["id"], _attempt_from_case(case), policy))

    report = {
        "policySource": args.policy_source,
        "policy": policy,
        "fixturePath": str(fixture_path) if not args.transcript_file else "",
        "cases": cases_report,
    }
    markdown = _markdown_report(report)
    json_text = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "memory-durable-threshold-report.md").write_text(markdown, encoding="utf-8")
        (output_dir / "memory-durable-threshold-report.json").write_text(json_text, encoding="utf-8")
        print(f"wrote markdown -> {output_dir / 'memory-durable-threshold-report.md'}")
        print(f"wrote json -> {output_dir / 'memory-durable-threshold-report.json'}")
    else:
        print(markdown)
        print(json_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
