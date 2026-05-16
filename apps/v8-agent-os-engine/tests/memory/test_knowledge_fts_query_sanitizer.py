from __future__ import annotations

from core.knowledge_db import sanitize_fts_query_tokens


def test_sanitize_fts_query_tokens_removes_quotes_and_operators():
    tokens = sanitize_fts_query_tokens("Bob's repo OR (path:/tmp/foo) https://example.com?q='x'")

    assert "OR" not in tokens
    assert all("'" not in token and '"' not in token for token in tokens)
    assert tokens

