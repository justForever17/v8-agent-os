from __future__ import annotations

import json

from .retrieval_eval import evaluate_retrieval_dataset


def _retrieval_dataset() -> list[dict]:
    return [
        {
            "question_id": "retrieval_exact",
            "question_type": "single-session-preference",
            "question": "Which editor did the user settle on?",
            "answer_session_ids": ["s2"],
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2026-01-01", "2026-02-01"],
            "haystack_sessions": [
                [{"role": "user", "content": "I tried several editors."}],
                [{"role": "user", "content": "I settled on VS Code for daily work.", "has_answer": True}],
            ],
        },
        {
            "question_id": "retrieval_no_answer",
            "question_type": "temporal-reasoning",
            "question": "What happened after the user moved to Mars?",
            "answer": "No evidence",
            "haystack_session_ids": ["s3"],
            "haystack_sessions": [[{"role": "user", "content": "The user planned a trip to Oslo."}]],
        },
    ]


def test_retrieval_eval_reports_grounded_metrics_and_scope(tmp_path) -> None:
    input_path = tmp_path / "longmemeval.json"
    input_path.write_text(json.dumps(_retrieval_dataset(), ensure_ascii=False), encoding="utf-8")

    result = evaluate_retrieval_dataset(input_path, split="oracle", top_k=3, strategy="keyword")

    assert result["status"] == "completed"
    assert result["granularity"] == "session"
    assert result["metrics"]["questionCount"] == 2
    assert result["metrics"]["answerableCount"] == 1
    assert result["metrics"]["acceptedRecallAtK"] == 1.0


def test_retrieval_eval_supports_turn_granularity(tmp_path) -> None:
    input_path = tmp_path / "longmemeval-turns.json"
    input_path.write_text(json.dumps(_retrieval_dataset(), ensure_ascii=False), encoding="utf-8")

    result = evaluate_retrieval_dataset(
        input_path,
        split="oracle",
        top_k=3,
        strategy="keyword",
        granularity="turn",
    )

    assert result["granularity"] == "turn"
    assert result["metrics"]["acceptedRecallAtK"] == 1.0
    assert 0.0 < result["metrics"]["acceptedMRR"] <= 1.0
    assert result["metrics"]["scopePrecision"] == 1.0
    assert result["metrics"]["noAnswerCount"] == 1
    assert result["cases"][0]["expectedSessionIds"] == ["s2"]
    assert result["cases"][0]["unresolvedExpectedSessionIds"] == []


def test_retrieval_eval_falls_back_to_turn_labels_when_answer_session_ids_missing(tmp_path) -> None:
    row = _retrieval_dataset()[0]
    row.pop("answer_session_ids")
    input_path = tmp_path / "longmemeval-turn-labels.json"
    input_path.write_text(json.dumps([row], ensure_ascii=False), encoding="utf-8")

    result = evaluate_retrieval_dataset(input_path, split="oracle", top_k=3, strategy="keyword")

    assert result["cases"][0]["expectedSessionIds"] == ["s2"]
    assert result["metrics"]["acceptedRecallAtK"] == 1.0
