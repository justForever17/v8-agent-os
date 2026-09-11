import hashlib
import json

import pytest

from core.tools import web_fetcher, web_chat_source
from runtimes.research.evidence import EvidenceStore


def observation(completion="observed_stable"):
    text = "A web AI answer with useful material. Its references still need reading."
    return {"ok": True, "webChatAnswer": {"text": text, "url": "https://chatgpt.com/uc/fixture",
            "sourceKind": "ai_generated_answer", "title": "Question", "contentHash": hashlib.sha256(text.encode()).hexdigest(),
            "completion": completion, "references": [{"url": "https://example.org/source", "title": "Source"}]}}


def test_guest_answer_is_captured_once_and_survives_evidence_restore():
    result, captured = web_chat_source.captured_chat_source(observation("partial"))
    assert captured[0]["url"] == result[0]["url"]
    store = EvidenceStore()
    assert store.add(captured) == ["S1"]
    restored = EvidenceStore()
    restored.restore(list(store.sources.values()))
    assert restored.read("S1")["text"] == observation()["webChatAnswer"]["text"]
    assert restored.index()[0]["sourceRole"] == "secondary"
    assert restored.index()[0]["acquisitionState"] == "partial"
    assert restored.read("S1")["linksAreReadEvidence"] is False


@pytest.mark.parametrize("options", [{"allowed_domains": ["example.org"]}, {"blocked_domains": ["chatgpt.com"]},
    {"site_domains": ["example.org"]}, {"source_intent": "official_primary"}])
def test_captured_answer_cannot_expand_requested_source_scope(options):
    assert web_chat_source.captured_chat_source(observation(), **options) == ([], [])


def test_mutated_capture_is_not_read_evidence():
    payload = observation()
    payload["webChatAnswer"]["text"] += " fabricated addition"
    assert web_chat_source.captured_chat_source(payload) == ([], [])


def test_transport_truncation_survives_capture_and_evidence_index():
    payload = observation()
    payload['webChatAnswer'].update(textTruncated=True, originalContentChars=120000)
    _, captured = web_chat_source.captured_chat_source(payload)
    store = EvidenceStore()
    store.add(captured)
    assert store.index()[0]['omittedChars'] == 120000 - len(payload['webChatAnswer']['text'])
    assert captured[0]['metadata']['textTruncated'] is True


def test_ai_identity_survives_claims_source_pack_and_handoff():
    from core.tools.research_broker import _research_source_pack
    from core.research_handoff_surface import render_research_handoff_evidence
    _, captured = web_chat_source.captured_chat_source(observation('partial'))
    store = EvidenceStore()
    store.add(captured)
    read = store.read('S1')
    claims = store.bind_read_refs([read['evidenceRef']], 'Observed material [S1]')
    packed = _research_source_pack(store.selected(claims)[0])
    assert packed['sourceRole'] == 'secondary'
    assert packed['sourceKind'] == 'ai_generated_answer' and packed['acquisitionState'] == 'partial'
    assert claims[0]['supportingSources'][0]['sourceKind'] == 'ai_generated_answer'
    rendered = render_research_handoff_evidence({'sources': [packed], 'claimTable': claims})
    assert 'ai_generated_answer; acquisition: partial' in rendered


def test_compact_web_result_keeps_ai_body_and_complete_detail():
    raw = observation()
    raw.update(provider="chatgpt", query="Question", results=[], resultCount=0)
    compact = web_fetcher._compact_web_broker_payload(raw, requested_mode="search", debug=False)
    assert compact["text"] == raw["webChatAnswer"]["text"]
    assert compact["webChatAnswer"] == raw["webChatAnswer"]
    assert compact["citationsVerified"] is False


@pytest.mark.parametrize("failure", ["provider_challenge", "provider_busy", "composer_unavailable", "answer_pending", "observation_changed"])
def test_browser_attention_state_survives_source_and_agent_projection(monkeypatch, failure):
    from runtimes.computer_use.browser_automation import agent_browser_automation
    from core.storage import storage
    monkeypatch.setattr(storage, "get_computer_use_config", lambda: {})
    monkeypatch.setattr(agent_browser_automation, "configure", lambda _: None)
    raw = {"ok": False, "provider": "chatgpt", "failureClass": failure,
           "error": "agent_browser_chat_verification_required" if failure == "provider_challenge" else "agent_browser_chat_busy",
           "querySubmitted": failure in {"answer_pending", "observation_changed"},
           "retryable": failure in {"provider_busy", "answer_pending"}, "verificationTargetId": "owned-target",
           "verificationPageRetained": True, "recommendedNextAction": "Complete verification in the original page."}
    monkeypatch.setattr(agent_browser_automation, "query_chat_page", lambda **_: raw)
    payload = web_chat_source.search_chat_page(provider="chatgpt", query="Question", limit=2, timeout_seconds=5, reuse_profile=True)
    compact = web_fetcher._compact_web_broker_payload(payload, requested_mode="search", debug=False)
    for key in ("failureClass", "querySubmitted", "retryable", "verificationTargetId", "verificationPageRetained", "recommendedNextAction"):
        assert compact[key] == raw[key]


@pytest.mark.parametrize("submitted", [True, None])
def test_observation_change_survives_explicit_search_without_retry_or_lost_target(monkeypatch, submitted):
    from runtimes.computer_use.browser_automation import agent_browser_automation
    from core.storage import storage
    monkeypatch.setattr(storage, "get_computer_use_config", lambda: {})
    monkeypatch.setattr(agent_browser_automation, "configure", lambda _: None)
    monkeypatch.setattr(web_fetcher, "_source_router_plan", lambda **kw: {"providers": ["chatgpt", "metaso"]})
    monkeypatch.setattr(web_fetcher, "_guard_url", lambda *a, **kw: (True, None))
    monkeypatch.setattr(web_fetcher, "_agent_browser_profile_allowed", lambda *a: (True, "chatgpt.com"))
    calls = []
    raw = {"ok": False, "provider": "chatgpt", "failureClass": "observation_changed",
           "error": "agent_browser_chat_observation_changed", "querySubmitted": submitted, "retryable": False,
           "verificationTargetId": "owned-pending-target", "verificationPageRetained": True,
           "recommendedNextAction": "Inspect the original page; do not resend."}
    monkeypatch.setattr(agent_browser_automation, "query_chat_page", lambda **kw: calls.append(kw) or raw)
    result = json.loads(web_fetcher.web_search.func(query="Question", search_engine="chatgpt", mode="dynamic"))
    compact = web_fetcher._compact_web_broker_payload(result, requested_mode="search", debug=False)
    assert [call["provider"] for call in calls] == ["chatgpt"]
    for key in ("failureClass", "querySubmitted", "retryable", "verificationTargetId", "verificationPageRetained", "recommendedNextAction"):
        assert compact[key] == raw[key]


def test_static_default_search_auto_uses_eligible_baidu_profile(monkeypatch):
    monkeypatch.setattr(web_fetcher, "_source_router_plan", lambda **kw: {"providers": ["baidu"]})
    monkeypatch.setattr(web_fetcher, "_guard_url", lambda *a, **kw: (True, None))
    monkeypatch.setattr(web_fetcher, "_agent_browser_profile_allowed", lambda *a: (True, "baidu.com"))
    seen = []
    def boundary(url, **kwargs):
        seen.append(kwargs)
        raise RuntimeError("fixture navigation failure")
    monkeypatch.setattr(web_fetcher, "_fetch_with_scrapling_internal", boundary)
    json.loads(web_fetcher.web_search.func(query="fixture", search_engine="baidu", mode="static"))
    assert seen and seen[0]["mode"] == "auto" and seen[0]["use_agent_browser_profile"] is True


@pytest.mark.parametrize("mode", ["auto", "dynamic"])
def test_explicit_website_or_failed_api_uses_chat_without_generic_spa_fetch(monkeypatch, mode):
    monkeypatch.setattr(web_fetcher, "_source_router_plan", lambda **kw: {"providers": ["metaso"]})
    monkeypatch.setattr(web_fetcher, "_guard_url", lambda *a, **kw: (True, None))
    monkeypatch.setattr(web_fetcher, "_agent_browser_profile_allowed", lambda *a: (True, "metaso.cn"))
    monkeypatch.setattr(web_fetcher, "_provider_api_key", lambda *a: "fixture-key")
    api, chats = [], []
    monkeypatch.setattr(web_fetcher, "_metaso_api_search", lambda *a, **kw: api.append(kw) or {"ok": False, "failureClass": "provider_rate_limited"})
    monkeypatch.setattr(web_fetcher, "_fetch_with_scrapling_internal", lambda *a, **kw: pytest.fail("bare SPA URL cannot submit a chat"))
    monkeypatch.setattr(web_chat_source, "search_chat_page", lambda **kw: chats.append(kw) or observation())
    result = json.loads(web_fetcher.web_search.func(query="Question", search_engine="metaso", mode=mode))
    assert result["ok"] and len(chats) == 1
    assert len(api) == (1 if mode == "auto" else 0)
    assert chats[0]["reuse_profile"] is True


def test_research_dynamic_mode_reaches_router_without_forcing_other_provider_profiles(monkeypatch):
    from core.tools import research_broker as research
    calls = []
    monkeypatch.setattr(research, "_source_router_search", lambda **kw: calls.append(kw) or json.dumps({"ok": False, "results": []}))
    research._run_search_shard({"kind": "agent_query", "query": "Question", "searchEngine": "metaso", "fetchMode": "dynamic"},
        allowed_domains=[], blocked_domains=[], source_policy="mixed", max_rounds=1,
        use_agent_browser_profile=True, tool_call_id="fixture", preferred_language="zh-CN")
    assert calls[0]["mode"] == "dynamic"
    assert calls[0]["useAgentBrowserProfile"] is False
    assert calls[0]["allow_browser_profile_fallback"] is True
