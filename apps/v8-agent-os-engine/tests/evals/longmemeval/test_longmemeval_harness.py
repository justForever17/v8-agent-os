from __future__ import annotations

import json

from .harness import (
    LongMemEvalV8Harness,
    build_official_evaluation_command,
    load_longmemeval_dataset,
    _render_history_evidence,
    _render_session_text,
)


def _sample_dataset():
    return [
        {
            "question_id": "sample_preference_1",
            "question_type": "single-session-preference",
            "question": "Which shoe brand does the user currently prefer?",
            "answer": "Nike",
            "question_date": "2026-07-30",
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2026-01-30", "2026-04-30"],
            "haystack_sessions": [
                [{"role": "user", "content": "I like Adidas shoes.", "has_answer": True}],
                [{"role": "user", "content": "Actually I now prefer Nike shoes.", "has_answer": True}],
            ],
        },
        {
            "question_id": "sample_knowledge_update_1",
            "question_type": "knowledge-update",
            "question": "What editor did the user settle on?",
            "answer": "VS Code",
            "question_date": "2026-04-24",
            "haystack_session_ids": ["s3", "s4"],
            "haystack_dates": ["2026-02-01", "2026-03-01"],
            "haystack_sessions": [
                [{"role": "user", "content": "I am trying Cursor this month."}],
                [{"role": "user", "content": "For daily work I settled on VS Code.", "has_answer": True}],
            ],
        },
        {
            "question_id": "sample_temporal_1_abs",
            "question_type": "temporal-reasoning",
            "question": "What did the user say about piano lessons after August?",
            "answer": "No evidence",
            "question_date": "2026-09-01",
            "haystack_session_ids": ["s5"],
            "haystack_dates": ["2026-05-01"],
            "haystack_sessions": [
                [{"role": "user", "content": "I considered piano lessons in May."}],
            ],
        },
    ]


def test_loader_preserves_longmemeval_fields(tmp_path):
    data_path = tmp_path / "longmemeval_sample.json"
    data_path.write_text(json.dumps(_sample_dataset(), ensure_ascii=False), encoding="utf-8")

    instances = load_longmemeval_dataset(data_path)

    assert [item.question_id for item in instances] == [
        "sample_preference_1",
        "sample_knowledge_update_1",
        "sample_temporal_1_abs",
    ]
    assert instances[0].question_type == "single-session-preference"
    assert instances[0].haystack_dates == ["2026-01-30", "2026-04-30"]


def test_harness_generates_official_compatible_jsonl(tmp_path):
    data_path = tmp_path / "longmemeval_sample.json"
    output_path = tmp_path / "hypotheses.jsonl"
    data_path.write_text(json.dumps(_sample_dataset(), ensure_ascii=False), encoding="utf-8")

    result = LongMemEvalV8Harness().run_dataset(
        input_path=data_path,
        output_jsonl_path=output_path,
        split="oracle",
    )
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert result["officialScoreAvailable"] is False
    assert result["questionCount"] == 3
    assert len(rows) == 3
    assert set(rows[0]) == {"question_id", "hypothesis"}
    assert [row["question_id"] for row in rows] == [
        "sample_preference_1",
        "sample_knowledge_update_1",
        "sample_temporal_1_abs",
    ]
    assert all(row["hypothesis"] for row in rows)


def test_official_evaluation_command_shape(tmp_path):
    command = build_official_evaluation_command(
        official_repo_root=tmp_path / "LongMemEval",
        hypothesis_file=tmp_path / "hypotheses.jsonl",
        data_file=tmp_path / "longmemeval_oracle.json",
    )

    assert command[0] == "python"
    assert command[2] == "gpt-4o"
    assert str(command[1]).endswith("src\\evaluation\\evaluate_qa.py") or str(command[1]).endswith("src/evaluation/evaluate_qa.py")
    assert str(command[3]).endswith("hypotheses.jsonl")


def test_rendered_history_does_not_expose_answer_labels(tmp_path):
    session = [{"role": "user", "content": "I now prefer Nike shoes.", "has_answer": True}]

    rendered = _render_session_text(session)

    assert "has_answer" not in rendered
    assert "Nike shoes" in rendered


def test_history_evidence_preserves_tail_when_truncated(tmp_path):
    row = _sample_dataset()[1]
    row["haystack_sessions"].append(
        [{"role": "user", "content": "late evidence: the editor is VS Code with extensions.", "has_answer": True}]
    )
    row["haystack_session_ids"].append("s5")
    row["haystack_dates"].append("2026-04-01")
    data_path = tmp_path / "longmemeval_sample.json"
    data_path.write_text(json.dumps([row], ensure_ascii=False), encoding="utf-8")
    instance = load_longmemeval_dataset(data_path)[0]

    rendered = _render_history_evidence(instance, max_chars=180)

    assert "timestamped history truncated" in rendered
    assert "late evidence" in rendered
    assert "has_answer" not in rendered
