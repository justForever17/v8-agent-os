from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.tools import bocha_provider, web_fetcher


def test_bocha_normalizes_official_webpages_value(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"_type": "SearchResponse", "webPages": {"totalEstimatedMatches": 17, "value": [
                {"name": "官方页面", "url": "https://example.test/a", "siteName": "Example",
                 "snippet": "摘要", "datePublished": "2026-09-09"},
                {"name": "缺少 URL"},
            ]}}

    calls = []
    monkeypatch.setattr(bocha_provider.requests, "post", lambda *args, **kwargs: (calls.append((args, kwargs)) or Response()))
    result = bocha_provider.bocha_search("LangChain", api_key="fixture-key", limit=8, timeout_seconds=3)
    assert result["ok"] is True
    assert result["results"] == [{"title": "官方页面", "url": "https://example.test/a", "snippet": "摘要",
                                  "source": "bocha", "rank": "1", "datePublished": "2026-09-09", "siteName": "Example"}]
    assert calls[0][0] == (bocha_provider.BOCHA_WEB_SEARCH_ENDPOINT,)
    assert calls[0][1]["json"] == {"query": "LangChain", "freshness": "noLimit", "summary": True, "count": 8}
    assert calls[0][1]["headers"]["Authorization"] == "Bearer fixture-key"


def test_bocha_classifies_timeout_and_http_auth_errors(monkeypatch):
    import requests

    monkeypatch.setattr(bocha_provider.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("slow")))
    assert bocha_provider.bocha_search("q", api_key="k", limit=1, timeout_seconds=1)["failureClass"] == "network_timeout"

    class Response:
        status_code = 401

    monkeypatch.setattr(bocha_provider.requests, "post", lambda *args, **kwargs: Response())
    result = bocha_provider.bocha_search("q", api_key="k", limit=1, timeout_seconds=1)
    assert result["failureClass"] == "credential_rejected" and result["retryable"] is False


def test_source_router_dispatches_bocha_through_api_adapter(monkeypatch):
    monkeypatch.setattr(web_fetcher, "get_web_fetch_config", lambda: {
        "providers": {"bocha": {"enabled": True, "apiKey": "fixture-key"}},
    })
    urls, calls = [], []
    monkeypatch.setattr(web_fetcher, "_guard_url", lambda url, **_: (urls.append(url) or (True, None)))
    response = SimpleNamespace(status_code=200, json=lambda: {"code": 200, "data": {"webPages": {"value": [
        {"name": "LangChain release", "url": "https://example.test/langchain", "summary": "LangChain release details"},
    ]}}})
    monkeypatch.setattr(bocha_provider.requests, "post", lambda *a, **k: (calls.append((a, k)) or response))
    result = json.loads(web_fetcher.web_broker.func(target="LangChain release", mode="search", search_engine="bocha"))
    assert result["ok"] is True and result["results"][0]["source"] == "bocha"
    assert urls == [bocha_provider.BOCHA_WEB_SEARCH_ENDPOINT]
    assert len(calls) == 1 and calls[0][1]["allow_redirects"] is False


def test_web_broker_promotes_profile_static_to_auto_but_keeps_public_static(monkeypatch):
    calls = []

    def fake_web_fetch(**kwargs):
        calls.append(kwargs)
        return json.dumps({"ok": True, "results": []})

    monkeypatch.setattr(web_fetcher, "web_fetch", SimpleNamespace(func=fake_web_fetch))
    web_fetcher.web_broker.func(target="metaso", mode="search", search_engine="metaso",
                                useAgentBrowserProfile=True, fetch_mode="static")
    assert calls[-1]["mode"] == "auto"
    web_fetcher.web_broker.func(target="metaso", mode="search", search_engine="metaso",
                                useAgentBrowserProfile=False, fetch_mode="static")
    assert calls[-1]["mode"] == "static"


@pytest.mark.parametrize("code,category,retryable", [(401, "credential_rejected", False),
    (403, "provider_access_denied", False), (429, "provider_rate_limited", True), (500, "provider_server_error", True)])
@pytest.mark.parametrize("http_error", [True, False])
def test_http_and_embedded_error_code_never_accept_fake_success(monkeypatch, code, category, retryable, http_error):
    response = SimpleNamespace(status_code=code if http_error else 200,
        json=lambda: {"code": code, "msg": "untrusted private error", "data": {"webPages": {"value": [
            {"name": "Must not return", "url": "https://example.test"},
        ]}}})
    monkeypatch.setattr(bocha_provider.requests, "post", lambda *a, **k: response)
    result = bocha_provider.bocha_search("q", api_key="fixture-key", limit=3, timeout_seconds=1)
    assert result["ok"] is False and result["failureClass"] == category and result["retryable"] is retryable
    assert "results" not in result and "private" not in json.dumps(result)


@pytest.mark.parametrize("payload", [{}, {"code": 200, "data": None}, {"data": {"webPages": {"value": {}}}}, []])
def test_malformed_provider_payload_is_not_empty_success(monkeypatch, payload):
    monkeypatch.setattr(bocha_provider.requests, "post", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: payload))
    result = bocha_provider.bocha_search("q", api_key="fixture-key", limit=3, timeout_seconds=1)
    assert result["ok"] is False and result["failureClass"] == "provider_format_unavailable"


def test_redirect_cannot_contact_new_host_or_return_results(monkeypatch):
    requests = []
    def post(*args, **kwargs):
        requests.append(kwargs)
        return SimpleNamespace(status_code=307)
    monkeypatch.setattr(bocha_provider.requests, "post", post)
    result = bocha_provider.bocha_search("q", api_key="fixture-key", limit=3, timeout_seconds=1)
    assert result["ok"] is False and len(requests) == 1 and requests[0]["allow_redirects"] is False


@pytest.mark.parametrize("enabled,key", [(False, "fixture-key"), (True, "")])
def test_real_router_respects_disabled_or_missing_credential(monkeypatch, enabled, key):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    monkeypatch.setattr(web_fetcher, "get_web_fetch_config", lambda: {
        "providers": {"bocha": {"enabled": enabled, "apiKey": key}},
    })
    monkeypatch.setattr(bocha_provider.requests, "post", lambda *a, **k: pytest.fail("disabled/missing-key provider must not send"))
    result = json.loads(web_fetcher.web_search.func(query="q", search_engine="bocha"))
    assert result["ok"] is False
    assert result["failureClass"] == ("provider_disabled" if not enabled else "credential_missing")
