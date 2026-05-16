from __future__ import annotations

from memory_eval_matrix import run_memory_eval_matrix


def test_memory_eval_matrix_reaches_internal_gate() -> None:
    result = run_memory_eval_matrix()
    assert result["p0Passed"], result
    assert result["passRate"] >= 0.95, result
    assert result["benchmarkMappingScore"] >= 9.8, result
    assert result["runtimeFirstScore"] >= 9.0, result
