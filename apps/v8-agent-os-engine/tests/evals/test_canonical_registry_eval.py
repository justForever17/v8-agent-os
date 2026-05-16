from __future__ import annotations

from memory_eval_matrix import canonical_registry_case


def test_canonical_registry_covers_common_drift_keys() -> None:
    result = canonical_registry_case()
    assert result["status"] == "pass", result
