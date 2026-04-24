from __future__ import annotations

from memory_eval_matrix import external_api_isolation_case


def test_external_api_threads_do_not_share_memory_scope() -> None:
    result = external_api_isolation_case()
    assert result["status"] == "pass", result
