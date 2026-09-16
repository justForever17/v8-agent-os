from __future__ import annotations

import pytest

from core import extensions_modelscope as modelscope
from core import extensions_store_service as store


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv("V8_AGENT_OS_EXTENSIONS_STORE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(store.storage, "get_mcp_config", lambda: {"mcpServers": {}})
    monkeypatch.setattr(modelscope, "get_skill_receipt", lambda *args: None)


@pytest.mark.parametrize("kind,window,requested", [("mcp", 100, 30), ("mcp", 100, 60), ("skills", 3000, 59)])
def test_catalog_reaches_window_once_without_requesting_forbidden_page(monkeypatch, kind, window, requested):
    calls = []

    def public(path, *, method, params):
        page, size = params["page_number"], params["page_size"]
        calls.append((page, size))
        assert page * size <= window, "the upstream rejects this request with QuotaLimitExceed"
        start = (page - 1) * size
        rows = [{"id": f"fixture/item-{index}"} for index in range(start, start + size)]
        return {"skills" if kind == "skills" else "mcp_server_list": rows,
                "total" if kind == "skills" else "total_count": window + 50}

    monkeypatch.setattr(modelscope, "read_public", public)
    seen = []
    page = 1
    while True:
        result = modelscope.list_items(kind, query="", limit=requested, page=page, refresh=False)
        assert len(result["items"]) <= requested
        seen.extend(row["id"] for row in result["items"])
        if not result["hasMore"]:
            break
        page = int(result["nextCursor"])
    assert len(seen) == len(set(seen)) == window
    assert result["catalogLimit"] == window
    assert result["nextCursor"] is None
    assert result["warnings"]
    with pytest.raises(store.ExtensionStoreError) as error:
        modelscope.list_items(kind, query="", limit=requested, page=page + 1, refresh=False)
    assert error.value.code == "catalog_window_exceeded"
    assert len(calls) == page


def test_invalid_catalog_response_keeps_last_good_cache_as_stale(monkeypatch):
    monkeypatch.setattr(modelscope, "read_public", lambda *args, **kwargs: {
        "mcp_server_list": [{"id": "fixture/working"}], "total_count": 1})
    first = modelscope.list_items("mcp", query="fixture", limit=30, page=1, refresh=False)
    monkeypatch.setattr(modelscope, "read_public", lambda *args, **kwargs: {"unexpected_schema": []})
    refreshed = modelscope.list_items("mcp", query="fixture", limit=30, page=1, refresh=True)
    assert refreshed["items"] == first["items"]
    assert refreshed["freshness"] == "stale"
    assert refreshed["warnings"]


def test_invalid_catalog_response_without_cache_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(modelscope, "read_public", lambda *args, **kwargs: {"unexpected_schema": []})
    with pytest.raises(store.ExtensionStoreError) as error:
        modelscope.list_items("mcp", query="new-query", limit=30, page=1, refresh=False)
    assert error.value.code == "source_response"


def test_one_invalid_public_id_does_not_hide_valid_entries_or_relax_install_identity(monkeypatch):
    monkeypatch.setattr(modelscope, "read_public", lambda *args, **kwargs: {
        "mcp_server_list": [{"id": "fixture/valid"}, {"id": "/mcp-playwright"}], "total_count": 2})
    result = modelscope.list_items("mcp", query="", limit=30, page=1, refresh=False)
    assert [row["id"] for row in result["items"]] == ["fixture/valid"]
    assert result["partial"] is True and result["warnings"]
    with pytest.raises(store.ExtensionStoreError) as error:
        modelscope.mcp_detail("/mcp-playwright")
    assert error.value.code == "invalid_modelscope_id"
