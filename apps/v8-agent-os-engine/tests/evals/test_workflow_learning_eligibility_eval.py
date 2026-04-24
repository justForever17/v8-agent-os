from __future__ import annotations

from memory_eval_matrix import workflow_learning_case


def test_workflow_learning_uses_proof_and_risk_gates() -> None:
    result = workflow_learning_case()
    assert result["status"] == "pass", result
