from __future__ import annotations

from memory_eval_matrix import summary_contamination_case


def test_stale_summary_is_audited_against_canonical_preference() -> None:
    result = summary_contamination_case()
    assert result["status"] == "pass", result
