from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable

try:
    from .harness import (
        SUPPORTED_SPLITS,
        LongMemEvalInstance,
        _render_session_text,
        _session_date_for,
        _session_id_for,
        isolated_v8_memory_store,
        load_longmemeval_dataset,
    )
except ImportError:
    from harness import (
        SUPPORTED_SPLITS,
        LongMemEvalInstance,
        _render_session_text,
        _session_date_for,
        _session_id_for,
        isolated_v8_memory_store,
        load_longmemeval_dataset,
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y"}
    return bool(value)


def _expected_session_ids(instance: LongMemEvalInstance) -> set[str]:
    explicit = {str(item).strip() for item in instance.answer_session_ids if str(item).strip()}
    if explicit:
        return explicit
    expected: set[str] = set()
    for index, session in enumerate(instance.haystack_sessions):
        if any(isinstance(turn, dict) and _truthy(turn.get("has_answer")) for turn in session):
            expected.add(_session_id_for(instance, index))
    return expected


def _ingest_instance(
    store: Any,
    instance: LongMemEvalInstance,
    scope: str,
    *,
    granularity: str,
) -> dict[str, list[str]]:
    fact_ids: dict[str, list[str]] = {}
    for index, session in enumerate(instance.haystack_sessions):
        session_id = _session_id_for(instance, index)
        date_value = _session_date_for(instance, index) or "unknown date"
        chunks = [session] if granularity == "session" else [[turn] for turn in session]
        for chunk_index, chunk in enumerate(chunks):
            text = _render_session_text(chunk)
            if not text:
                continue
            fact_id = store.add_knowledge(
                fact=(
                    f"LongMemEval session {session_id} at {date_value}"
                    f"{f' turn {chunk_index + 1}' if granularity == 'turn' else ''}:\n{text}"
                ),
                category="longmemeval_session",
                scope=scope,
                source_session=(
                    f"longmemeval:{instance.question_id}:{session_id}"
                    f"{f':turn-{chunk_index + 1}' if granularity == 'turn' else ''}"
                ),
                tags=["longmemeval", granularity, instance.question_type or "unknown"],
            )
            fact_ids.setdefault(session_id, []).append(fact_id)
    return fact_ids


def _unique_ids(items: Iterable[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for item in items:
        fact_id = str(item.get("id") or "").strip()
        if fact_id and fact_id not in ids:
            ids.append(fact_id)
    return ids


def _first_relevant_rank(retrieved_ids: list[str], expected_ids: set[str]) -> int | None:
    for index, fact_id in enumerate(retrieved_ids, start=1):
        if fact_id in expected_ids:
            return index
    return None


def _ndcg(retrieved_ids: list[str], expected_ids: set[str]) -> float | None:
    if not expected_ids:
        return None
    dcg = sum(
        1.0 / math.log2(index + 1)
        for index, fact_id in enumerate(retrieved_ids, start=1)
        if fact_id in expected_ids
    )
    ideal_length = min(len(expected_ids), len(retrieved_ids))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(ideal_length))
    return round(dcg / ideal, 6) if ideal else 0.0


def _mean(values: Iterable[float | None]) -> float | None:
    normalized = [float(value) for value in values if value is not None]
    return round(sum(normalized) / len(normalized), 6) if normalized else None


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    normalized = sorted(float(value) for value in values)
    if not normalized:
        return None
    index = min(len(normalized) - 1, max(0, math.ceil(len(normalized) * percentile) - 1))
    return round(normalized[index], 3)


def _aggregate_cases(cases: list[dict[str, Any]], *, top_k: int) -> dict[str, Any]:
    answerable = [case for case in cases if case["expectedEvidenceIds"]]
    no_answer = [case for case in cases if not case["expectedEvidenceIds"]]
    candidate_scope_total = sum(len(case["candidateItems"]) for case in cases)
    candidate_scope_allowed = sum(
        sum(1 for item in case["candidateItems"] if item["scopeAllowed"])
        for case in cases
    )
    channel_counts: dict[str, int] = defaultdict(int)
    for case in cases:
        for channel, count in (case.get("diagnostics") or {}).get("channel_candidate_counts", {}).items():
            try:
                channel_counts[str(channel)] += int(count or 0)
            except (TypeError, ValueError):
                continue
    return {
        "questionCount": len(cases),
        "answerableCount": len(answerable),
        "noAnswerCount": len(no_answer),
        "candidateRecallAtK": _mean(case["candidateRecallAtK"] for case in answerable),
        "acceptedRecallAtK": _mean(case["acceptedRecallAtK"] for case in answerable),
        "candidateMRR": _mean(case["candidateMRR"] for case in answerable),
        "acceptedMRR": _mean(case["acceptedMRR"] for case in answerable),
        "candidateNDCG": _mean(case["candidateNDCG"] for case in answerable),
        "acceptedNDCG": _mean(case["acceptedNDCG"] for case in answerable),
        "scopePrecision": round(candidate_scope_allowed / candidate_scope_total, 6) if candidate_scope_total else None,
        "noAnswerFalsePositiveRate": (
            round(sum(bool(case["acceptedEvidenceIds"]) for case in no_answer) / len(no_answer), 6)
            if no_answer
            else None
        ),
        "encodingCoverage": _mean(case["encodingCoverage"] for case in cases),
        "latencyMs": {
            "p50": _percentile((case["latencyMs"] for case in cases), 0.50),
            "p95": _percentile((case["latencyMs"] for case in cases), 0.95),
        },
        "channelCandidateCounts": dict(sorted(channel_counts.items())),
        "topK": top_k,
    }


def evaluate_retrieval_dataset(
    input_path: str | Path,
    *,
    split: str = "oracle",
    limit: int | None = None,
    top_k: int = 8,
    strategy: str = "keyword",
    granularity: str = "session",
) -> dict[str, Any]:
    if split not in SUPPORTED_SPLITS:
        raise ValueError(f"Unsupported LongMemEval split: {split}")
    normalized_strategy = str(strategy or "keyword").strip().lower()
    if normalized_strategy not in {"keyword", "semantic", "balanced"}:
        raise ValueError("strategy must be keyword, semantic, or balanced")
    normalized_granularity = str(granularity or "session").strip().lower()
    if normalized_granularity not in {"session", "turn"}:
        raise ValueError("granularity must be session or turn")
    effective_top_k = max(1, int(top_k))
    instances = load_longmemeval_dataset(input_path, limit=limit)
    cases: list[dict[str, Any]] = []
    memory_config = {
        "recall_strategy": normalized_strategy,
        "fts_enabled": normalized_strategy in {"keyword", "balanced"},
        "graph_enabled": False,
        "rerank_enabled": False,
        "recall_top_k": effective_top_k,
    }
    with isolated_v8_memory_store(memory_config=memory_config) as store:
        for instance in instances:
            scope = f"external_api_thread:longmemeval_{instance.question_id}"
            session_fact_ids = _ingest_instance(
                store,
                instance,
                scope,
                granularity=normalized_granularity,
            )
            expected_session_ids = _expected_session_ids(instance)
            expected_ids = {
                fact_id
                for session_id in expected_session_ids
                for fact_id in session_fact_ids.get(session_id, [])
            }
            started = time.perf_counter()
            preview = store.preview_unified_recall(
                query=instance.question,
                limit=effective_top_k,
                scope=scope,
                scopes=["global", scope],
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            candidate_items = [
                item for item in list(preview.get("items") or [])[:effective_top_k] if isinstance(item, dict)
            ]
            accepted_items = [
                item for item in list(preview.get("accepted_items") or [])[:effective_top_k] if isinstance(item, dict)
            ]
            candidate_ids = _unique_ids(candidate_items)
            accepted_ids = _unique_ids(accepted_items)
            candidate_rank = _first_relevant_rank(candidate_ids, expected_ids)
            accepted_rank = _first_relevant_rank(accepted_ids, expected_ids)
            allowed_scopes = {"global", scope}
            encoding_coverage = (
                len(expected_session_ids & set(session_fact_ids)) / len(expected_session_ids)
                if expected_session_ids
                else 1.0
            )
            cases.append(
                {
                    "questionId": instance.question_id,
                    "questionType": instance.question_type,
                    "expectedSessionIds": sorted(expected_session_ids),
                    "unresolvedExpectedSessionIds": sorted(expected_session_ids - set(session_fact_ids)),
                    "expectedEvidenceIds": sorted(expected_ids),
                    "candidateEvidenceIds": candidate_ids,
                    "acceptedEvidenceIds": accepted_ids,
                    "candidateRecallAtK": 1.0 if candidate_rank else 0.0 if expected_ids else None,
                    "acceptedRecallAtK": 1.0 if accepted_rank else 0.0 if expected_ids else None,
                    "candidateMRR": 1.0 / candidate_rank if candidate_rank else 0.0 if expected_ids else None,
                    "acceptedMRR": 1.0 / accepted_rank if accepted_rank else 0.0 if expected_ids else None,
                    "candidateNDCG": _ndcg(candidate_ids, expected_ids),
                    "acceptedNDCG": _ndcg(accepted_ids, expected_ids),
                    "encodingCoverage": round(encoding_coverage, 6),
                    "latencyMs": round(latency_ms, 3),
                    "candidateItems": [
                        {
                            "id": str(item.get("id") or ""),
                            "scope": str(item.get("scope") or "global"),
                            "scopeAllowed": str(item.get("scope") or "global") in allowed_scopes,
                            "source": item.get("source"),
                        }
                        for item in candidate_items
                    ],
                    "diagnostics": dict(preview.get("diagnostics") or {}),
                }
            )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[case["questionType"] or "unknown"].append(case)
    return {
        "status": "completed",
        "source": "v8os_unified_recall",
        "inputPath": str(input_path),
        "split": split,
        "strategy": normalized_strategy,
        "granularity": normalized_granularity,
        "topK": effective_top_k,
        "questionCount": len(cases),
        "metrics": _aggregate_cases(cases, top_k=effective_top_k),
        "byQuestionType": {
            question_type: _aggregate_cases(group, top_k=effective_top_k)
            for question_type, group in sorted(grouped.items())
        },
        "cases": cases,
        "officialScore": None,
        "officialScoreAvailable": False,
        "note": "Retrieval metrics use answer_session_ids/has_answer evidence labels; run the official evaluator separately for answer quality.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate V8OS Memory unified recall on LongMemEval-format data.")
    parser.add_argument("--input", required=True, help="Path to LongMemEval-format JSON outside the repository.")
    parser.add_argument("--output", help="Optional JSON result path outside the repository.")
    parser.add_argument("--split", default="oracle", choices=SUPPORTED_SPLITS)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--strategy", default="keyword", choices=("keyword", "semantic", "balanced"))
    parser.add_argument("--granularity", default="session", choices=("session", "turn"))
    args = parser.parse_args(argv)
    result = evaluate_retrieval_dataset(
        args.input,
        split=args.split,
        limit=args.limit,
        top_k=args.top_k,
        strategy=args.strategy,
        granularity=args.granularity,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
