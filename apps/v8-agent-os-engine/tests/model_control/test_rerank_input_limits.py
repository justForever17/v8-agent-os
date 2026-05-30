from __future__ import annotations

from unittest.mock import patch

from core.llm_factory import RestReranker, _safe_log_text


class _Response:
    def __init__(self, status_code: int, text: str = "", payload: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_reranker_retries_query_too_long_with_observed_query_limit() -> None:
    reranker = RestReranker(
        model_name="rerank-fixture",
        api_key="key",
        base_url="https://example.test/v1",
        max_tokens=32000,
        provider_id="fixture-provider",
    )
    calls: list[dict] = []

    def fake_post(endpoint, payload, headers):  # noqa: ANN001
        calls.append(dict(payload))
        if len(calls) == 1:
            return _Response(400, '{"message":"Query is too long. Please provide a shorter query."}')
        return _Response(
            200,
            payload={
                "results": [
                    {
                        "index": 0,
                        "relevance_score": 0.9,
                    }
                ]
            },
        )

    with patch.object(reranker, "_post_rerank", side_effect=fake_post), patch(
        "core.llm_factory.model_budget_service.enforce_or_raise",
        return_value=None,
    ), patch(
        "core.llm_factory.model_telemetry_service.record_aux_model_invocation",
        return_value=None,
    ):
        result = reranker.rerank("这是一段很长的查询" * 2000, ["doc one", "doc two"], top_k=1)

    assert result[0]["index"] == 0
    assert len(calls) == 2
    assert len(calls[1]["query"]) < len(calls[0]["query"])


def test_reranker_log_text_is_safe_for_gbk_stderr(monkeypatch) -> None:
    class _GbkStderr:
        encoding = "gbk"

    monkeypatch.setattr("core.llm_factory.sys.stderr", _GbkStderr())

    text = _safe_log_text("provider said ⚠️ retry with emoji ✨")

    text.encode("gbk")
    assert "\\u26a0" in text or "\\ufe0f" in text
