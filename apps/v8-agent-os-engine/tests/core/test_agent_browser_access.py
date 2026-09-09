from __future__ import annotations

import time
from unittest.mock import Mock

import pytest

from core import agent_browser_access as access
from core.tools import web_fetcher


def test_discovery_never_promotes_cookie_presence_to_verified_login(monkeypatch):
    raw = {"available": True, "sites": [{"host": "metaso.cn", "sessionPresent": True, "access": "authenticated", "cookie": "PRIVATE"}]}
    from runtimes.computer_use.browser_automation import agent_browser_automation
    monkeypatch.setattr(agent_browser_automation, "configure", lambda _: None)
    monkeypatch.setattr(agent_browser_automation, "profile_access_summary", lambda **_: raw)
    monkeypatch.setattr(access, "_snapshot", {})
    monkeypatch.setattr(access, "_updated", 0)
    monkeypatch.setattr(access, "_refreshing", False)
    access._refresh()
    snapshot = access.access_snapshot()
    assert snapshot["sites"][0]["access"] == "unverified"
    assert "PRIVATE" not in str(snapshot)


def test_new_session_domain_is_used_without_a_manual_allowlist_but_not_siblings(monkeypatch):
    monkeypatch.setattr(web_fetcher, "get_web_fetch_config", lambda: {"useAgentBrowserProfile": True, "agentBrowserProfileAllowlist": []})
    monkeypatch.setattr(access, "access_snapshot", lambda **_: {"available": True, "sites": [{"host": "metaso.cn", "sessionPresent": True}]})
    assert web_fetcher._agent_browser_profile_allowed("https://metaso.cn/search") == (True, "metaso.cn")
    assert web_fetcher._agent_browser_profile_allowed("https://www.metaso.cn/") == (True, "metaso.cn")
    assert web_fetcher._agent_browser_profile_allowed("https://evil.metaso.cn/") == (False, None)
    assert web_fetcher._agent_browser_profile_allowed("https://metaso.cn.attacker.test/") == (False, None)


def test_disabled_reuse_does_not_discover_or_use_session(monkeypatch):
    monkeypatch.setattr(web_fetcher, "get_web_fetch_config", lambda: {"useAgentBrowserProfile": False, "agentBrowserProfileAllowlist": ["metaso.cn"]})
    probe = Mock(side_effect=AssertionError("Disabled profile must not be probed"))
    monkeypatch.setattr(access, "observed_profile_host", probe)
    assert web_fetcher._agent_browser_profile_allowed("https://metaso.cn") == (False, None)
    probe.assert_not_called()


def test_logout_observation_replaces_cached_session_candidate(monkeypatch):
    monkeypatch.setattr(access, "access_snapshot", lambda **_: {"available": True, "sites": [{"host": "example.test", "sessionPresent": False}]})
    assert access.observed_profile_host("https://example.test") is None


def test_prompt_read_is_nonblocking_while_refresh_in_flight(monkeypatch):
    monkeypatch.setattr(access, "_refreshing", True)
    monkeypatch.setattr(access, "_snapshot", {"available": True, "sites": []})
    started = time.monotonic()
    assert access.access_snapshot()["refreshPending"]
    assert time.monotonic() - started < 0.1


def test_read_outcomes_do_not_upgrade_whole_domain_access(monkeypatch):
    monkeypatch.setattr(access, "_snapshot", {"available": True, "sites": [{"host": "example.test", "sessionPresent": True, "access": "unverified"}]})
    monkeypatch.setattr(access, "_updated", time.monotonic())
    monkeypatch.setattr(access, "_reads", {})
    access.record_profile_read("https://example.test/article?token=PRIVATE", "readable")
    snapshot = access.access_snapshot()
    site = snapshot["sites"][0]
    assert site["access"] == "unverified" and site["lastRead"]["status"] == "readable"
    assert "PRIVATE" not in str(snapshot)
    access.record_profile_read("https://example.test/login", "needs_login")
    assert access.access_snapshot()["sites"][0]["lastRead"]["status"] == "needs_login"


def test_profile_read_refuses_a_healthy_proxy_pointing_at_a_different_browser(monkeypatch):
    import pytest
    from runtimes.computer_use.browser_automation import BrowserAutomationProvider

    browser = BrowserAutomationProvider()
    browser._target_port = 9222
    monkeypatch.setattr(browser, "_ensure_proxy", lambda **_: None)
    monkeypatch.setattr(browser, "_health", lambda **_: {"connected": True, "targetPort": 9333, "targetEndpoint": "http://127.0.0.1:9333"})
    request = Mock(side_effect=AssertionError("Mismatched browser must not receive the URL"))
    monkeypatch.setattr(browser, "_request_json", request)
    with pytest.raises(RuntimeError, match="profile_proxy_target_mismatch"):
        browser.read_profile_page(url="https://example.test", timeout_seconds=4)
    request.assert_not_called()


def test_challenge_body_is_not_recorded_as_successful_authenticated_read(monkeypatch):
    from types import SimpleNamespace
    observed = []
    monkeypatch.setattr(access, "record_profile_read", lambda url, status: observed.append(status))
    page = SimpleNamespace(url="https://example.test", final_url="https://example.test", status=200,
                           title="Security check", text="Please complete CAPTCHA", html="<p>CAPTCHA</p>",
                           media=[], agent_browser_profile_used=True)
    assert web_fetcher._auto_fetch_reject_reason(page) == "verification_or_anti_crawl"
    web_fetcher._page_quality_fields(page, text=page.text)
    assert observed == ["challenge"]


def test_profile_proxy_preserves_authority_failure_without_private_response_data(monkeypatch):
    import requests
    import pytest
    from runtimes.computer_use.browser_automation import BrowserAutomationProvider

    browser = BrowserAutomationProvider()
    monkeypatch.setattr(browser, "_ensure_proxy", lambda **_: None)
    monkeypatch.setattr(browser, "_assert_profile_proxy_target", lambda: None)
    response = requests.Response()
    response.status_code = 503
    response._content = b'{"error":"agent_browser_profile_redirect_requires_authorization:login.example.test","private":"SECRET"}'
    monkeypatch.setattr(browser, "_request_json", Mock(side_effect=requests.HTTPError(response=response)))
    with pytest.raises(RuntimeError) as error:
        browser.read_profile_page(url="https://example.test", timeout_seconds=4)
    assert str(error.value) == "agent_browser_profile_redirect_requires_authorization:login.example.test"
    assert web_fetcher._classify_web_fetch_failure(str(error.value)) == "agent_browser_profile_not_allowed"
    response._content = b'{"error":"page.goto https://example.test/?token=SECRET"}'
    with pytest.raises(RuntimeError, match="^agent_browser_profile_read_proxy_failed$"):
        browser.read_profile_page(url="https://example.test", timeout_seconds=4)


@pytest.mark.parametrize("code,expected", [
    ("agent_browser_profile_redirect_requires_authorization:login.example.test", "agent_browser_profile_not_allowed"),
    ("agent_browser_profile_proxy_target_mismatch", "agent_browser_profile_mismatch"),
])
def test_search_does_not_retry_an_unauthorized_profile_redirect(monkeypatch, code, expected):
    import json
    monkeypatch.setattr(web_fetcher, "get_web_fetch_config", lambda: {"useAgentBrowserProfile": True, "providers": {"baidu": {"enabled": True}}})
    monkeypatch.setattr(web_fetcher, "_agent_browser_profile_allowed", lambda _: (True, "baidu.com"))
    fetch = Mock(side_effect=RuntimeError(code))
    monkeypatch.setattr(web_fetcher, "_fetch_with_scrapling_internal", fetch)
    payload = json.loads(web_fetcher.source_router_search(query="test profile document", search_engine="baidu", total_timeout_seconds=10))
    assert payload["failureClass"] == expected
    assert payload["retryable"] is False
    assert code in payload["error"]
    assert "不要" in payload["recommendedNextAction"]
    assert payload["attemptedProviders"][-1]["profileAttempted"] is True
    assert fetch.call_count == 1


@pytest.mark.parametrize("observed_host", ["example.test", "www.example.test"])
def test_www_and_root_read_outcomes_replace_each_other_without_merging_siblings(monkeypatch, observed_host):
    monkeypatch.setattr(access, "_snapshot", {"available": True, "sites": [{"host": observed_host, "sessionPresent": True}]})
    monkeypatch.setattr(access, "_updated", time.monotonic())
    monkeypatch.setattr(access, "_reads", {})
    access.record_profile_read("https://example.test/article", "readable")
    access.record_profile_read("https://www.example.test/article", "failed")
    assert access.access_snapshot()["sites"][0]["lastRead"]["status"] == "failed"
    access.record_profile_read("https://other.example.test/article", "readable")
    assert access.access_snapshot()["sites"][0]["lastRead"]["status"] == "failed"
    access.record_profile_read("https://example.test/article", "needs_login")
    assert access.access_snapshot()["sites"][0]["lastRead"]["status"] == "needs_login"


@pytest.mark.parametrize("code,expected", [
    ("agent_browser_profile_redirect_requires_authorization:timeout.example.test", "agent_browser_profile_not_allowed"),
    ("agent_browser_profile_proxy_target_mismatch", "agent_browser_profile_mismatch"),
    ("agent_browser_profile_context_not_reused", "agent_browser_profile_mismatch"),
])
def test_automatic_profile_rejection_stops_before_public_fallback(monkeypatch, code, expected):
    from runtimes.computer_use.browser_automation import agent_browser_automation

    monkeypatch.setattr(web_fetcher, "get_web_fetch_config", lambda: {"useAgentBrowserProfile": True, "agentBrowserProfileAllowlist": ["example.test"]})
    monkeypatch.setattr(web_fetcher, "_active_agent_browser_cdp_context", lambda: {"profileDir": "fixture", "browserKind": "edge", "cdpUrl": "ws://fixture"})
    monkeypatch.setattr(web_fetcher, "_dependency_status", lambda: {key: {"available": True} for key in ("static", "dynamic", "stealth")})
    monkeypatch.setattr(agent_browser_automation, "configure", lambda _: None)
    profile = Mock(side_effect=RuntimeError(code))
    monkeypatch.setattr(agent_browser_automation, "read_profile_page", profile)
    public = Mock(side_effect=AssertionError("No public fetch after an authority rejection"))
    monkeypatch.setattr(web_fetcher, "_try_import_static_fetcher", public)
    reader = Mock(side_effect=AssertionError("No third-party reader after an authority rejection"))
    monkeypatch.setattr(web_fetcher, "_fetch_with_reader_fallback", reader)
    with pytest.raises(RuntimeError) as error:
        web_fetcher._fetch_with_scrapling_internal("https://example.test/article", mode="auto", timeout_seconds=10)
    assert profile.call_count == 1
    public.assert_not_called()
    reader.assert_not_called()
    assert code in str(error.value)
    assert web_fetcher._classify_web_fetch_failure(str(error.value)) == expected
