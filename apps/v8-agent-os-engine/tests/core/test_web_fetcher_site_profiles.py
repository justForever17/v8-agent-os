from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from core.tools import web_fetcher


def test_internal_source_router_options_are_scoped_and_reset(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_search(**_kwargs):
        observed["timeout"] = web_fetcher._SOURCE_ROUTER_SEARCH_TIMEOUT_SECONDS.get()
        observed["locale"] = web_fetcher._SOURCE_ROUTER_LOCALE_HINT.get()
        observed["browserFallback"] = web_fetcher._SOURCE_ROUTER_BROWSER_FALLBACK.get()
        observed["preferredProviders"] = web_fetcher._SOURCE_ROUTER_PREFERRED_PROVIDERS.get()
        observed["excludedProviders"] = web_fetcher._SOURCE_ROUTER_EXCLUDED_PROVIDERS.get()
        return json.dumps({"ok": True, "results": []})

    monkeypatch.setattr(web_fetcher, "web_search", SimpleNamespace(func=fake_search))

    web_fetcher.source_router_search(
        query="LangChain current API",
        total_timeout_seconds=14,
        locale_hint="zh-CN",
        allow_browser_profile_fallback=False,
        preferred_providers=["bing_cn"],
        excluded_providers=["google"],
    )

    assert observed == {
        "timeout": 14.0,
        "locale": "zh-CN",
        "browserFallback": False,
        "preferredProviders": ("bing_cn",),
        "excludedProviders": ("google",),
    }
    assert web_fetcher._SOURCE_ROUTER_SEARCH_TIMEOUT_SECONDS.get() == 0.0
    assert web_fetcher._SOURCE_ROUTER_LOCALE_HINT.get() == ""
    assert web_fetcher._SOURCE_ROUTER_BROWSER_FALLBACK.get() is True
    assert web_fetcher._SOURCE_ROUTER_PREFERRED_PROVIDERS.get() == ()
    assert web_fetcher._SOURCE_ROUTER_EXCLUDED_PROVIDERS.get() == ()


def test_internal_source_router_hints_reorder_and_skip_only_auto_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {"globalPreferred": ["bing", "yahoo"]},
            "providers": {
                "bing": {"enabled": True},
                "yahoo": {"enabled": True},
            },
            "useAgentBrowserProfile": False,
            "agentBrowserProfileAllowlist": [],
        },
    )
    calls: list[str] = []

    def fake_html_search(_url, *, provider, **_kwargs):
        calls.append(provider)
        return {
            "ok": True,
            "statusCode": 200,
            "finalUrl": f"https://{provider}.example/search",
            "results": [
                {
                    "title": "Pathlib CLI guidance",
                    "url": "https://docs.python.org/3/library/pathlib.html",
                    "snippet": "Pathlib command line path guidance.",
                }
            ],
        }

    monkeypatch.setattr(web_fetcher, "_html_search_public", fake_html_search)

    payload = json.loads(
        web_fetcher.source_router_search(
            query="pathlib command line guidance",
            search_engine="auto",
            preferred_providers=["yahoo"],
            excluded_providers=["bing"],
        )
    )

    assert payload["ok"] is True
    assert payload["provider"] == "yahoo"
    assert calls == ["yahoo"]
    assert payload["sourceRouter"]["runPreferredProviders"] == ["yahoo"]
    assert payload["sourceRouter"]["runExcludedProviders"] == ["bing"]
    assert payload["providerAttemptMatrix"][0]["failureClass"] == "provider_circuit_open"

    explicit_payload = json.loads(
        web_fetcher.source_router_search(
            query="pathlib command line guidance",
            search_engine="bing",
            preferred_providers=["yahoo"],
            excluded_providers=["bing"],
        )
    )
    assert explicit_payload["ok"] is True
    assert explicit_payload["provider"] == "bing"
    assert calls == ["yahoo", "bing"]


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_search_result_extraction_resolves_provider_redirect_urls() -> None:
    duckduckgo = _soup(
        """
        <div class="result">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Feur-lex.europa.eu%2Feli%2Freg%2F2024%2F1689%2Foj%2Feng&amp;rut=abc">EU AI Act</a>
          <div class="result__snippet">Official legal text.</div>
        </div>
        """
    )
    google = _soup(
        """
        <div class="g">
          <a href="/url?q=https%3A%2F%2Fdigital-strategy.ec.europa.eu%2Fen%2Fpolicies%2Fgeneral-purpose-ai&amp;sa=U">GPAI guidance</a>
          <div class="VwiC3b">Official guidance.</div>
        </div>
        """
    )
    yahoo = _soup(
        """
        <div class="sw-Card Algo">
          <a class="sw-Card__titleInner" href="https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng">
            Regulation (EU) 2024/1689
          </a>
          <div class="sw-Card__summary">Official Journal AI Act text.</div>
        </div>
        """
    )

    assert web_fetcher._extract_search_results(duckduckgo, provider="duckduckgo", limit=5)[0]["url"] == (
        "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng"
    )
    assert web_fetcher._extract_search_results(google, provider="google", limit=5)[0]["url"] == (
        "https://digital-strategy.ec.europa.eu/en/policies/general-purpose-ai"
    )
    assert web_fetcher._extract_search_results(yahoo, provider="yahoo", limit=5)[0] == {
        "title": "Regulation (EU) 2024/1689",
        "url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng",
        "snippet": "Official Journal AI Act text.",
    }


def test_yahoo_search_url_normalizes_caret_exponents() -> None:
    url = web_fetcher._provider_search_url(
        "yahoo",
        "GPAI systemic risk threshold 10^25 FLOPs",
    )

    assert url.startswith("https://search.yahoo.co.jp/search?p=")
    assert "10e25" in url
    assert "%5E" not in url


def test_configured_provider_preferences_adopt_new_registry_order_for_untouched_legacy_default(monkeypatch) -> None:
    legacy_order = list(web_fetcher._LEGACY_GLOBAL_SOURCE_PROVIDERS_V1)
    current_order = (
        *web_fetcher._LEGACY_GLOBAL_SOURCE_PROVIDERS_V1[:3],
        "yahoo",
        *web_fetcher._LEGACY_GLOBAL_SOURCE_PROVIDERS_V1[3:],
    )
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {"sourceRouter": {"globalPreferred": legacy_order}},
    )
    monkeypatch.setattr(
        web_fetcher,
        "DEFAULT_GLOBAL_SOURCE_PROVIDERS",
        current_order,
    )

    assert web_fetcher._configured_source_provider_order("global") == list(current_order)


def test_configured_provider_preferences_migrate_previous_shipped_order_to_bing_cn(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {
                "cnPreferred": list(web_fetcher._LEGACY_CN_SOURCE_PROVIDERS_V2),
                "globalPreferred": list(web_fetcher._LEGACY_GLOBAL_SOURCE_PROVIDERS_V2),
            },
        },
    )

    assert web_fetcher._configured_source_provider_order("cn") == list(web_fetcher.DEFAULT_CN_SOURCE_PROVIDERS)
    assert web_fetcher._configured_source_provider_order("global") == list(web_fetcher.DEFAULT_GLOBAL_SOURCE_PROVIDERS)
    assert "bing_cn" in web_fetcher._configured_source_provider_order("cn")
    assert "bing_cn" in web_fetcher._configured_source_provider_order("global")


def test_configured_cn_preferences_append_new_fallback_only_for_untouched_legacy_default(monkeypatch) -> None:
    legacy_order = list(web_fetcher._LEGACY_CN_SOURCE_PROVIDERS_V1)
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {"sourceRouter": {"cnPreferred": legacy_order}},
    )
    monkeypatch.setattr(
        web_fetcher,
        "DEFAULT_CN_SOURCE_PROVIDERS",
        (*web_fetcher._LEGACY_CN_SOURCE_PROVIDERS_V1[:3], "yahoo", *web_fetcher._LEGACY_CN_SOURCE_PROVIDERS_V1[3:]),
    )

    assert web_fetcher._configured_source_provider_order("cn") == [
        *legacy_order[:3],
        "yahoo",
        *legacy_order[3:],
    ]


def test_configured_provider_preferences_preserve_a_user_curated_legacy_superset(monkeypatch) -> None:
    curated_order = [*web_fetcher._LEGACY_GLOBAL_SOURCE_PROVIDERS_V1, "custom-search"]
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {"sourceRouter": {"globalPreferred": curated_order}},
    )
    monkeypatch.setattr(
        web_fetcher,
        "DEFAULT_GLOBAL_SOURCE_PROVIDERS",
        ("brave", "tavily", "exa", "yahoo", "duckduckgo", "google", "bing"),
    )

    assert web_fetcher._configured_source_provider_order("global") == curated_order


def test_configured_provider_preferences_do_not_restore_user_removed_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {"sourceRouter": {"globalPreferred": ["bing"]}},
    )
    monkeypatch.setattr(web_fetcher, "DEFAULT_GLOBAL_SOURCE_PROVIDERS", ("bing", "yahoo"))

    assert web_fetcher._configured_source_provider_order("global") == ["bing"]


def test_auto_router_prioritizes_allowlisted_agent_browser_search_hosts(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {
                "globalPreferred": ["yahoo", "duckduckgo", "google", "bing", "metaso", "baidu"],
            },
            "providers": {
                provider: {"enabled": True}
                for provider in ("yahoo", "duckduckgo", "google", "bing", "metaso", "baidu")
            },
            "useAgentBrowserProfile": True,
            "agentBrowserProfileAllowlist": ["metaso.cn", "baidu.com"],
        },
    )

    plan = web_fetcher._source_router_plan(
        query="LangChain current capabilities official documentation",
        requested_provider="auto",
    )

    assert plan["plannedProviders"][:2] == ["metaso", "baidu"]
    assert plan["providers"][:2] == ["metaso", "baidu"]
    assert plan["profilePromotedProviders"] == ["metaso", "baidu"]
    assert plan["networkRoute"] == "agent_browser"
    assert web_fetcher._source_router_payload_fields(plan)["networkRoute"] == "agent_browser"


def test_auto_router_keeps_public_cn_direct_ahead_of_login_profile_routes(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {
                "globalPreferred": ["yahoo", "metaso", "bing_cn", "baidu"],
            },
            "providers": {
                provider: {"enabled": True}
                for provider in ("yahoo", "metaso", "bing_cn", "baidu")
            },
            "useAgentBrowserProfile": True,
            "agentBrowserProfileAllowlist": ["metaso.cn", "baidu.com"],
        },
    )

    plan = web_fetcher._source_router_plan(
        query="LangChain current capabilities official documentation",
        requested_provider="auto",
    )

    assert plan["plannedProviders"] == ["bing_cn", "metaso", "baidu", "yahoo"]
    assert plan["providers"] == ["bing_cn", "metaso", "baidu", "yahoo"]
    assert plan["profilePromotedProviders"] == ["metaso", "baidu"]
    assert plan["networkRoute"] == "cn_direct"


def test_metaso_profile_configuration_still_tries_structured_search_first(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {"cnPreferred": ["metaso"]},
            "providers": {"metaso": {"enabled": True}},
            "useAgentBrowserProfile": True,
            "agentBrowserProfileAllowlist": ["metaso.cn"],
        },
    )
    monkeypatch.setattr(
        web_fetcher,
        "_metaso_search_public",
        lambda *_args, **_kwargs: {
            "ok": True,
            "results": [
                {
                    "title": "LangChain v1 中文参考",
                    "url": "https://docs.example.cn/langchain-v1",
                    "snippet": "LangChain v1 current capabilities.",
                }
            ],
            "eventsSeen": 3,
        },
    )
    monkeypatch.setattr(
        web_fetcher,
        "_fetch_with_scrapling_internal",
        lambda *_args, **_kwargs: pytest.fail("browser page fallback should not run after structured success"),
    )

    payload = json.loads(
        web_fetcher.web_search.func(
            query="LangChain v1 current capabilities",
            search_engine="metaso",
            useAgentBrowserProfile=True,
        )
    )

    assert payload["ok"] is True
    assert payload["provider"] == "metaso"
    assert payload["attemptedProviders"][-1]["route"] == "public_sse"
    assert payload["metaso"]["route"] == "public_sse"


def test_auto_search_continues_when_provider_ignores_site_constraint(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {
                "cnPreferred": ["bing_cn", "metaso"],
                "globalPreferred": ["bing_cn", "metaso"],
            },
            "providers": {
                "bing_cn": {"enabled": True},
                "metaso": {"enabled": True},
            },
            "useAgentBrowserProfile": False,
            "agentBrowserProfileAllowlist": [],
        },
    )
    monkeypatch.setattr(
        web_fetcher,
        "_html_search_public",
        lambda *_args, **_kwargs: {
            "ok": True,
            "statusCode": 200,
            "finalUrl": "https://cn.bing.com/search?q=langchain",
            "results": [
                {
                    "title": "LangChain product home",
                    "url": "https://www.langchain.com/langchain",
                    "snippet": "LangChain agent framework.",
                }
            ],
        },
    )
    monkeypatch.setattr(
        web_fetcher,
        "_metaso_search_public",
        lambda *_args, **_kwargs: {
            "ok": True,
            "results": [
                {
                    "title": "LangChain v1 migration guide",
                    "url": "https://docs.langchain.com/oss/python/migrate/langchain-v1",
                    "snippet": "Official v1 migration guide.",
                }
            ],
        },
    )

    payload = json.loads(
        web_fetcher.web_search.func(
            query="site:docs.langchain.com LangChain v1 migration guide",
            search_engine="auto",
        )
    )

    assert payload["ok"] is True
    assert payload["provider"] == "metaso"
    assert payload.get("siteConstraintRelaxed") is not True
    assert payload["attemptedProviders"][0]["status"] == "relaxed_candidate"
    assert payload["attemptedProviders"][0]["failureClass"] == "site_constraint_not_honored"


def test_site_query_rejects_a_lone_homepage_and_keeps_only_exact_site_results(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {
                "cnPreferred": ["bing_cn", "yahoo"],
                "globalPreferred": ["bing_cn", "yahoo"],
            },
            "providers": {
                "bing_cn": {"enabled": True},
                "yahoo": {"enabled": True},
            },
            "useAgentBrowserProfile": False,
            "agentBrowserProfileAllowlist": [],
        },
    )

    def fake_html_search(_url, *, provider, **_kwargs):
        if provider == "bing_cn":
            results = [
                {
                    "title": "Home - Docs by LangChain",
                    "url": "https://docs.langchain.com/",
                    "snippet": "LangChain documentation home.",
                },
                {
                    "title": "LangChain product home",
                    "url": "https://www.langchain.com/langchain",
                    "snippet": "LangChain agent framework.",
                },
            ]
        else:
            results = [
                {
                    "title": "Agents - Docs by LangChain",
                    "url": "https://docs.langchain.com/oss/python/langchain/agents",
                    "snippet": "create_agent API documentation.",
                },
                {
                    "title": "Middleware overview - Docs by LangChain",
                    "url": "https://docs.langchain.com/oss/python/langchain/middleware/overview",
                    "snippet": "LangChain middleware API documentation.",
                },
                {
                    "title": "Unrelated mirror",
                    "url": "https://example.com/langchain",
                    "snippet": "LangChain mirror.",
                },
            ]
        return {
            "ok": True,
            "statusCode": 200,
            "finalUrl": _url,
            "results": results,
        }

    monkeypatch.setattr(web_fetcher, "_html_search_public", fake_html_search)

    payload = json.loads(
        web_fetcher.web_search.func(
            query="site:docs.langchain.com LangChain Python create_agent middleware API",
            search_engine="auto",
        )
    )

    assert payload["provider"] == "yahoo"
    assert {item["url"] for item in payload["results"]} == {
        "https://docs.langchain.com/oss/python/langchain/agents",
        "https://docs.langchain.com/oss/python/langchain/middleware/overview",
    }
    bing_attempt = next(item for item in payload["attemptedProviders"] if item["provider"] == "bing_cn")
    assert bing_attempt["status"] == "relaxed_candidate"
    assert bing_attempt["exactSiteResultCount"] == 1


def test_explicit_metaso_empty_results_fail_instead_of_reporting_success(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "providers": {"metaso": {"enabled": True}},
            "useAgentBrowserProfile": False,
            "agentBrowserProfileAllowlist": [],
        },
    )
    monkeypatch.setattr(
        web_fetcher,
        "_metaso_search_public",
        lambda *_args, **_kwargs: {"ok": True, "results": []},
    )

    payload = json.loads(
        web_fetcher.web_search.func(
            query="site:docs.example.com current API",
            search_engine="metaso",
        )
    )

    assert payload["ok"] is False
    assert payload["failureClass"] == "no_results"
    assert payload["attemptedProviders"][-1]["status"] == "empty"


def test_auto_search_failure_preserves_operational_error_and_has_no_selected_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {"globalPreferred": ["bing", "metaso"]},
            "providers": {"bing": {"enabled": True}, "metaso": {"enabled": True}},
            "useAgentBrowserProfile": False,
            "agentBrowserProfileAllowlist": [],
        },
    )
    monkeypatch.setattr(web_fetcher, "DEFAULT_GLOBAL_SOURCE_PROVIDERS", ())
    monkeypatch.setattr(
        web_fetcher,
        "_html_search_public",
        lambda *_args, **_kwargs: {
            "ok": False,
            "failureClass": "no_results",
            "reason": "bing_no_results",
            "statusCode": 200,
        },
    )
    monkeypatch.setattr(
        web_fetcher,
        "_agent_browser_profile_search_skip",
        lambda provider, _url: (
            {
                "provider": provider,
                "status": "skipped",
                "failureClass": "needs_agent_browser_login",
                "reason": "agent_browser_profile_not_enabled_or_domain_not_allowlisted",
                "recommendedNextAction": "use another provider",
            }
            if provider == "metaso"
            else None
        ),
    )

    payload = json.loads(
        web_fetcher.web_search.func(
            query="EU AI Act GPAI official evidence",
            search_engine="auto",
        )
    )

    assert payload["ok"] is False
    assert payload["failureClass"] == "no_results"
    assert payload["error"] == "bing_no_results"
    assert payload["sourceRouter"]["selectedProvider"] is None
    assert payload["sourceCapability"] == {}


def test_search_relevance_ignores_task_verbs_and_rejects_ambiguous_topic_pollution() -> None:
    query = "确认 EU AI Act 对 GPAI 提供者的合规时间，并核实 2026 年是否延期"
    signals = web_fetcher._search_query_relevance_signals(query)

    assert "确认" not in signals["signals"]
    assert "核实" not in signals["signals"]
    assert "gpai" in signals["signals"]

    polluted = web_fetcher._assess_search_result_relevance(
        query,
        [
            {
                "title": "沙特确认参加 Global Partnership on AI 峰会",
                "snippet": "The GPAI event discussed artificial intelligence cooperation in 2026.",
                "url": "https://news.example/gpai-summit",
            }
        ],
    )
    relevant = web_fetcher._assess_search_result_relevance(
        query,
        [
            {
                "title": "EU AI Act GPAI provider compliance timeline",
                "snippet": "General-purpose AI obligations and the 2026 application milestone.",
                "url": "https://digital-strategy.ec.europa.eu/general-purpose-ai",
            }
        ],
    )

    assert polluted["relevant"] is False
    assert polluted["matchedDistinctiveSignalCount"] < 2
    assert relevant["relevant"] is True


def test_search_relevance_treats_chinese_recency_words_as_context_not_topic() -> None:
    query = (
        "截至 2026-07-29 欧盟 AI Act 对 GPAI 提供者的关键合规日期线，"
        "核实适用时间、义务和官方更新"
    )
    signals = web_fetcher._search_query_relevance_signals(query)

    assert "截至" in signals["signals"]
    assert "日期" in signals["signals"]
    assert "截至" not in signals["distinctiveSignals"]
    assert "日期" not in signals["distinctiveSignals"]
    assert "者的" not in signals["distinctiveSignals"]

    polluted = web_fetcher._assess_search_result_relevance(
        query,
        [
            {
                "title": "截止与截至的区别",
                "snippet": "截止和截至都与日期、时间有关，本文说明两者的用法。",
                "url": "https://dict.example.cn/jiezhi",
            }
        ],
    )

    assert polluted["relevant"] is False
    assert polluted["matchedDistinctiveSignalCount"] == 0


def test_extract_main_text_supports_research_depth_without_changing_the_default_limit() -> None:
    late_marker = "LATE_OFFICIAL_API_CONTRACT_MARKER"
    html = (
        "<html><body><main><p>"
        + ("early documentation detail " * 650)
        + "</p><p>"
        + late_marker
        + " records the authoritative behavior and version boundary.</p></main></body></html>"
    )

    default_text = web_fetcher._extract_main_text(_soup(html), "https://docs.example.com/reference")
    research_text = web_fetcher._extract_main_text(
        _soup(html),
        "https://docs.example.com/reference",
        max_chars=32_000,
    )

    assert late_marker not in default_text
    assert "...[TRUNCATED]" in default_text
    assert late_marker in research_text
    assert "...[TRUNCATED]" not in research_text


def test_web_fetch_cache_write_probe_is_unique_across_parallel_shards(tmp_path, monkeypatch) -> None:
    probe_names: list[str] = []
    real_named_temporary_file = web_fetcher.tempfile.NamedTemporaryFile

    def recording_named_temporary_file(*args, **kwargs):
        handle = real_named_temporary_file(*args, **kwargs)
        probe_names.append(handle.name)
        return handle

    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {"cacheDir": str(tmp_path)},
    )
    monkeypatch.setattr(
        web_fetcher.tempfile,
        "NamedTemporaryFile",
        recording_named_temporary_file,
    )

    with ThreadPoolExecutor(max_workers=16) as executor:
        resolved = list(executor.map(lambda _index: web_fetcher._web_fetch_cache_dir(), range(32)))

    assert resolved == [tmp_path] * 32
    assert len(probe_names) == 32
    assert len(set(probe_names)) == 32
    assert list(tmp_path.glob(".write-test-*")) == []


def test_static_fetch_does_not_restart_tls_attempt_after_budget_is_exhausted(monkeypatch) -> None:
    calls: list[float] = []

    class _SlowFailingFetcher:
        @staticmethod
        def get(_url: str, **kwargs: object) -> object:
            calls.append(float(kwargs["timeout"]))
            time.sleep(0.6)
            raise TimeoutError("simulated network timeout")

    monkeypatch.setattr(web_fetcher, "_try_import_static_fetcher", lambda: (_SlowFailingFetcher, None))
    monkeypatch.setattr(
        web_fetcher,
        "_resolve_verify_candidates",
        lambda: [("certifi", True), ("system", "system-ca.pem")],
    )

    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"attempted=\['static'\]"):
        web_fetcher._fetch_with_scrapling_internal(
            "https://example.com/search",
            mode="static",
            timeout_seconds=1.5,
        )
    elapsed = time.monotonic() - started

    assert len(calls) == 1
    assert 1.0 <= calls[0] <= 1.5
    assert elapsed < 1.5


def test_agent_browser_profile_gets_the_full_provider_navigation_budget(monkeypatch) -> None:
    timeouts: list[int] = []

    class _FailingBrowserFetcher:
        @staticmethod
        def fetch(_url: str, **kwargs: object) -> object:
            timeouts.append(int(kwargs["timeout"]))
            raise TimeoutError("simulated browser navigation timeout")

    monkeypatch.setattr(web_fetcher, "_try_import_dynamic_fetcher", lambda: (_FailingBrowserFetcher, None))
    monkeypatch.setattr(web_fetcher, "_try_import_stealth_fetcher", lambda: (_FailingBrowserFetcher, None))
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "useAgentBrowserProfile": True,
            "agentBrowserProfileAllowlist": ["metaso.cn"],
        },
    )
    monkeypatch.setattr(
        web_fetcher,
        "_active_agent_browser_cdp_context",
        lambda: {"profileDir": "agent-profile", "browserKind": "edge", "cdpUrl": "ws://127.0.0.1/devtools/browser/test"},
    )

    with pytest.raises(RuntimeError, match="网页抓取失败"):
        web_fetcher._fetch_with_scrapling_internal(
            "https://metaso.cn/?q=langchain",
            mode="auto",
            timeout_seconds=8,
            use_agent_browser_profile=True,
        )

    assert timeouts
    assert timeouts[0] >= 7_500


def test_allowlisted_auto_fetch_prioritizes_headless_profile_before_public_static(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class _DynamicFetcher:
        @staticmethod
        def fetch(url: str, **kwargs: object) -> object:
            calls.append(("dynamic", dict(kwargs)))
            return type(
                "Response",
                (),
                {
                    "html_content": (
                        "<html><head><title>Signed-in page</title></head><body><main>"
                        "Authenticated content is available from the governed browser profile. "
                        "This body is intentionally long enough for the normal auto-fetch quality gate "
                        "to accept it without falling back to a public static request."
                        "</main></body></html>"
                    ),
                    "url": url,
                    "status": 200,
                },
            )()

    class _UnexpectedStaticFetcher:
        @staticmethod
        def get(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("allowlisted auto fetch must not start public static first")

    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "useAgentBrowserProfile": True,
            "agentBrowserProfileAllowlist": ["metaso.cn"],
        },
    )
    monkeypatch.setattr(
        web_fetcher,
        "_active_agent_browser_cdp_context",
        lambda: {
            "profileDir": "agent-profile",
            "browserKind": "edge",
            "cdpUrl": "ws://127.0.0.1/devtools/browser/test",
        },
    )
    monkeypatch.setattr(web_fetcher, "_try_import_static_fetcher", lambda: (_UnexpectedStaticFetcher, None))
    monkeypatch.setattr(web_fetcher, "_try_import_dynamic_fetcher", lambda: (_DynamicFetcher, None))
    monkeypatch.setattr(web_fetcher, "_try_import_stealth_fetcher", lambda: (None, "stealth not needed"))

    payload = web_fetcher._fetch_with_scrapling_internal(
        "https://metaso.cn/?q=langchain",
        mode="auto",
        timeout_seconds=8,
        use_agent_browser_profile=False,
    )

    assert payload.agent_browser_profile_used is True
    assert payload.fetch_mode == "dynamic"
    assert [name for name, _kwargs in calls] == ["dynamic"]
    assert calls[0][1]["cdp_url"] == "ws://127.0.0.1/devtools/browser/test"


def test_missing_scrapling_fetcher_dependency_is_terminal_and_actionable() -> None:
    error = "No module named 'curl_cffi'"

    assert web_fetcher._classify_web_fetch_failure(error) == "runtime_dependency_missing"
    payload = json.loads(
        web_fetcher._render_error_payload(
            url="https://search.example.com",
            requested_mode="dynamic",
            referer_mode="none",
            referer_url="",
            error=error,
        )
    )

    assert payload["failureClass"] == "runtime_dependency_missing"
    assert payload["retryable"] is False
    assert "重新安装 V8OS" in payload["recommendedNextAction"]


def test_explicit_metaso_search_preserves_runtime_dependency_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {"globalPreferred": ["metaso"], "cnPreferred": ["metaso"]},
            "providers": {"metaso": {"enabled": True}},
            "useAgentBrowserProfile": False,
            "agentBrowserProfileAllowlist": [],
        },
    )
    monkeypatch.setattr(web_fetcher, "_agent_browser_profile_search_skip", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        web_fetcher,
        "_metaso_search_public",
        lambda *_args, **_kwargs: {
            "ok": False,
            "failureClass": "runtime_dependency_missing",
            "reason": "No module named 'curl_cffi'",
        },
    )

    payload = json.loads(
        web_fetcher.web_search.func(
            query="LangChain release history",
            search_engine="metaso",
        )
    )

    assert payload["ok"] is False
    assert payload["failureClass"] == "runtime_dependency_missing"
    assert payload["retryable"] is False
    assert "重新安装 V8OS" in payload["recommendedNextAction"]


def test_allowlisted_metaso_profile_falls_back_to_browser_after_empty_structured_response(monkeypatch) -> None:
    """An authenticated profile must get a chance after public/SSE returns no rows."""

    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {"globalPreferred": ["metaso"], "cnPreferred": ["metaso"]},
            "providers": {"metaso": {"enabled": True}},
            "useAgentBrowserProfile": True,
            "agentBrowserProfileAllowlist": ["metaso.cn"],
        },
    )
    monkeypatch.setattr(web_fetcher, "_guard_url", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(
        web_fetcher,
        "_metaso_search_public",
        lambda *_args, **_kwargs: {"ok": True, "results": [], "reason": "empty_public_response"},
    )
    fetch_calls: list[dict[str, object]] = []

    def fake_profile_fetch(url: str, **kwargs: object) -> web_fetcher.WebPagePayload:
        fetch_calls.append({"url": url, **kwargs})
        return web_fetcher.WebPagePayload(
            url=url,
            final_url=url,
            requested_mode="auto",
            referer_mode="none",
            referer_url="",
            fetch_mode="dynamic",
            attempted_modes=["dynamic"],
            available_modes={"dynamic": {"available": True}},
            status=200,
            tls_strategy="browser_managed",
            ca_bundle_path="",
            proxy_bypass_used=False,
            title="MetaSo authenticated results",
            text="LangChain current capabilities reference",
            html=(
                "<html><head><title>MetaSo</title></head><body><main>"
                "<a href='https://docs.example.com/langchain'>"
                "LangChain current capabilities reference</a>"
                "</main></body></html>"
            ),
            metadata={},
            links=[],
            media=[],
            warnings=[],
            agent_browser_profile_used=True,
            agent_browser_profile_host="metaso.cn",
            agent_browser_profile_dir="profile",
            agent_browser_kind="edge",
        )

    monkeypatch.setattr(web_fetcher, "_fetch_with_scrapling_internal", fake_profile_fetch)

    payload = json.loads(
        web_fetcher.web_search.func(
            query="LangChain current capabilities",
            search_engine="metaso",
        )
    )

    assert payload["ok"] is True
    assert payload["provider"] == "metaso"
    assert payload["agentBrowserProfile"]["used"] is True
    assert fetch_calls
    assert fetch_calls[0]["use_agent_browser_profile"] is True
    assert any(
        item.get("provider") == "metaso" and item.get("status") == "empty"
        for item in payload["attemptedProviders"]
    )


def test_auto_search_preserves_budget_for_a_later_working_provider(monkeypatch) -> None:
    timeouts: list[tuple[str, float]] = []

    def fake_html_search(_url: str, **kwargs: object):
        provider = str(kwargs["provider"])
        timeouts.append((provider, float(kwargs["timeout_seconds"])))
        if provider != "bing":
            return {
                "ok": False,
                "failureClass": "network_timeout",
                "reason": f"{provider} unavailable",
                "retryable": True,
            }
        return {
            "ok": True,
            "statusCode": 200,
            "finalUrl": _url,
            "results": [
                {
                    "title": "Official source",
                    "url": "https://example.com/official",
                    "snippet": "Current official evidence.",
                }
            ],
        }

    monkeypatch.setattr(
        web_fetcher,
        "get_web_fetch_config",
        lambda: {
            "sourceRouter": {
                "globalPreferred": ["duckduckgo", "google", "bing"],
                "cnPreferred": ["bing"],
            },
            "useAgentBrowserProfile": False,
            "agentBrowserProfileAllowlist": [],
        },
    )
    monkeypatch.setattr(web_fetcher, "_html_search_public", fake_html_search)

    payload = json.loads(
        web_fetcher.web_search.func(
            query="EU AI Act GPAI Article 113 official",
            search_engine="auto",
        )
    )

    assert payload["ok"] is True
    assert payload["provider"] == "bing"
    assert [provider for provider, _timeout in timeouts] == ["duckduckgo", "google", "bing"]
    assert all(timeout <= web_fetcher.WEB_SEARCH_PROVIDER_TIMEOUT_SECONDS for _provider, timeout in timeouts)


def test_html_search_public_uses_one_bounded_request_and_existing_bing_parser(monkeypatch) -> None:
    request_timeouts: list[float] = []

    class _Response:
        status_code = 200
        url = "https://cn.bing.com/search?q=gpai"
        text = (
            "<html><head><title>Bing</title></head><body>"
            "<li class='b_algo'><h2><a href='https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng'>"
            "Regulation (EU) 2024/1689</a></h2><div class='b_caption'><p>Official AI Act text.</p></div></li>"
            "</body></html>"
        )

    def fake_get(_url: str, **kwargs: object):
        request_timeouts.append(float(kwargs["timeout"]))
        return _Response()

    monkeypatch.setattr(web_fetcher.requests, "get", fake_get)

    payload = web_fetcher._html_search_public(
        "https://cn.bing.com/search?q=gpai",
        provider="bing",
        limit=5,
        timeout_seconds=7.5,
    )

    assert payload["ok"] is True
    assert payload["results"][0]["url"] == "https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng"
    assert request_timeouts == [7.5]


def test_bing_cn_reuses_bing_parser_with_a_cn_direct_url() -> None:
    soup = _soup(
        "<li class='b_algo'><h2><a href='https://docs.example.cn/reference'>"
        "国内可读参考资料</a></h2><div class='b_caption'><p>可引用正文入口。</p></div></li>"
    )

    assert web_fetcher._provider_search_url("bing_cn", "LangChain reference").startswith(
        "https://cn.bing.com/search?q="
    )
    assert web_fetcher._extract_search_results(soup, provider="bing_cn", limit=5) == [
        {
            "title": "国内可读参考资料",
            "url": "https://docs.example.cn/reference",
            "snippet": "可引用正文入口。",
        }
    ]


def test_yahoo_html_search_serializes_parallel_anonymous_requests(monkeypatch) -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    class _Response:
        status_code = 200
        url = "https://search.yahoo.co.jp/search?p=gpai"
        text = (
            "<div class='sw-Card Algo'><a class='sw-Card__titleInner' "
            "href='https://eur-lex.europa.eu/eli/reg/2024/1689/oj/eng'>AI Act</a>"
            "<div class='sw-Card__summary'>Official GPAI evidence.</div></div>"
        )

    def fake_get(_url: str, **_kwargs: object):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return _Response()

    monkeypatch.setattr(web_fetcher.requests, "get", fake_get)

    with ThreadPoolExecutor(max_workers=4) as executor:
        payloads = list(
            executor.map(
                lambda index: web_fetcher._html_search_public(
                    f"https://search.yahoo.co.jp/search?p=gpai-{index}",
                    provider="yahoo",
                    limit=5,
                    timeout_seconds=2.0,
                ),
                range(4),
            )
        )

    assert peak == 1
    assert all(payload["ok"] is True for payload in payloads)


def test_wikipedia_profile_keeps_article_and_removes_chrome() -> None:
    html = """
    <html><body>
      <main id="content">
        <h1 id="firstHeading">Artificial intelligence</h1>
        <div class="mw-parser-output">
          <div class="shortdescription">Field of computer science</div>
          <p>Artificial intelligence is machine intelligence.</p>
          <span class="mw-editsection">edit</span>
          <table class="navbox"><tr><td>navigation box</td></tr></table>
          <h2>History</h2>
          <p>Early work explored symbolic reasoning.</p>
          <div class="reflist">reference chrome</div>
        </div>
      </main>
    </body></html>
    """

    text = web_fetcher._extract_main_text(_soup(html), "https://en.wikipedia.org/wiki/Artificial_intelligence")

    assert "Artificial intelligence" in text
    assert "Artificial intelligence is machine intelligence." in text
    assert "Early work explored symbolic reasoning." in text
    assert "navigation box" not in text
    assert "reference chrome" not in text
    assert "edit" not in text


def test_github_profile_keeps_readme_and_preserves_star_metadata() -> None:
    html = """
    <html><body>
      <header class="Header">global navigation</header>
      <main>
        <nav class="UnderlineNav">Code Issues Pull requests</nav>
        <a href="/owner/repo/stargazers" aria-label="1.2k users starred this repository">1.2k</a>
        <div data-testid="readme">
          <div class="OverviewRepoFiles-module__Box_2__zsLGk">
            <article class="markdown-body">
              <h1>Project</h1>
              <p>README body for the project.</p>
            </article>
          </div>
        </div>
      </main>
    </body></html>
    """
    soup = _soup(html)

    text = web_fetcher._extract_main_text(soup, "https://github.com/owner/repo")
    metadata = web_fetcher._extract_metadata(soup, "https://github.com/owner/repo")

    assert "README body for the project." in text
    assert "global navigation" not in text
    assert "Code Issues Pull requests" not in text
    assert metadata["githubRepository"] == "owner/repo"
    assert metadata["githubStars"] == 1200
    assert "1.2k" in metadata["githubStarsText"]


def test_pep_profile_preserves_created_date_and_python_version_metadata() -> None:
    html = """
    <html><head><title>PEP 428</title></head><body>
      <dl class="field-list simple">
        <dt class="field-even">Created<span class="colon">:</span></dt>
        <dd class="field-even">30-Jul-2012</dd>
        <dt class="field-odd">Python-Version<span class="colon">:</span></dt>
        <dd class="field-odd">3.4</dd>
        <dt class="field-even">Post-History<span class="colon">:</span></dt>
        <dd class="field-even">05-Oct-2012</dd>
      </dl>
    </body></html>
    """

    metadata = web_fetcher._extract_metadata(_soup(html), "https://peps.python.org/pep-0428/")

    assert metadata["date"] == "30-Jul-2012"
    assert metadata["version"] == "3.4"
    assert metadata["pepCreated"] == "30-Jul-2012"
    assert metadata["pepPostHistory"] == "05-Oct-2012"


def test_p0_site_profiles_are_registered() -> None:
    urls = (
        "https://zh.wikipedia.org/wiki/Python",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript",
        "https://learn.microsoft.com/en-us/dotnet/csharp/",
        "https://github.com/owner/repo/releases/tag/v1.0.0",
        "https://github.com/owner/repo/issues/12",
        "https://arxiv.org/abs/1706.03762",
        "https://openreview.net/forum?id=example",
        "https://aclanthology.org/2024.acl-long.1/",
        "https://pubmed.ncbi.nlm.nih.gov/12345678/",
        "https://dl.acm.org/doi/10.1145/example",
        "https://paperswithcode.com/paper/example",
        "https://news.ycombinator.com/item?id=123",
        "https://www.npmjs.com/package/react",
        "https://pypi.org/project/requests/",
        "https://stackoverflow.com/questions/1/example",
        "https://www.sec.gov/Archives/edgar/data/example",
        "https://www.sse.com.cn/disclosure/listedinfo/announcement/",
        "https://www.cninfo.com.cn/new/disclosure/detail",
        "https://www.binance.com/en/support/announcement/example",
        "https://etherscan.io/tx/0x123",
        "https://www.amazon.com/dp/B000000",
        "https://item.jd.com/100000.html",
    )

    for url in urls:
        assert web_fetcher._builtin_extract_profile(url, "article"), url


def test_google_scholar_is_discovery_source_without_body_profile() -> None:
    hints = web_fetcher._search_result_quality_hints("https://scholar.google.com/scholar?q=transformer")

    assert not web_fetcher._builtin_extract_profile("https://scholar.google.com/scholar?q=transformer", "article")
    assert hints["catalogSourceId"] == "academic_discovery_secondary"
    assert hints["catalogCategory"] == "academic_discovery"
    assert hints["authorityTier"] == "secondary"
    assert hints["tier"] == "secondary"
    assert "secondary_source_hint" in hints["signals"]


def test_p2_community_site_profiles_are_registered() -> None:
    urls = (
        "https://stackoverflow.com/questions/1/example",
        "https://www.zhihu.com/question/123/answer/456",
        "https://juejin.cn/post/123",
        "https://blog.csdn.net/example/article/details/123",
        "https://www.cnblogs.com/example/p/123.html",
    )

    for url in urls:
        assert web_fetcher._builtin_extract_profile(url, "article"), url


def test_official_docs_generic_profile_applies_to_docs_paths() -> None:
    html = """
    <html><body>
      <main>
        <aside class="sidebar">Install menu</aside>
        <article class="prose">
          <h1>API guide</h1>
          <p>Use this endpoint to create a run.</p>
        </article>
        <div class="toc">On this page</div>
        <div class="feedback">Was this helpful?</div>
      </main>
    </body></html>
    """
    url = "https://example.com/docs/api/create-run"

    profile = web_fetcher._builtin_site_profile(url)
    text = web_fetcher._extract_main_text(_soup(html), url)

    assert profile["description"].startswith("Official documentation pages")
    assert "Use this endpoint to create a run." in text
    assert "Install menu" not in text
    assert "On this page" not in text
    assert "Was this helpful?" not in text


def test_stackoverflow_profile_keeps_question_and_answers_without_comments() -> None:
    html = """
    <html><body>
      <main id="mainbar">
        <div id="question-header"><h1>How do I parse HTML?</h1></div>
        <div id="question">
          <div class="votecell">votes</div>
          <div class="js-post-body"><p>Question body.</p></div>
          <div class="comments">noisy comments</div>
        </div>
        <div id="answers">
          <div class="answer accepted-answer">
            <div class="js-post-body"><p>Accepted answer body.</p></div>
          </div>
          <div class="answer" data-score="98">
            <div class="js-post-body"><p>High-vote answer body.</p></div>
          </div>
        </div>
        <aside id="sidebar">hot network questions</aside>
      </main>
    </body></html>
    """

    text = web_fetcher._extract_main_text(_soup(html), "https://stackoverflow.com/questions/1/how-do-i-parse-html")

    assert "How do I parse HTML?" in text
    assert "Question body." in text
    assert "Accepted answer body." in text
    assert "High-vote answer body." in text
    assert "noisy comments" not in text
    assert "hot network questions" not in text


def test_hacker_news_profile_keeps_story_signal_and_comments_without_table_chrome() -> None:
    html = """
    <html><body><center>
      <table class="itemlist">
        <tr class="athing" id="123">
          <td class="votelinks"><div class="votearrow">vote</div></td>
          <td class="title">
            <span class="titleline"><a href="https://example.com">Show HN: V8 Agent OS</a></span>
          </td>
        </tr>
        <tr>
          <td colspan="2"></td>
          <td class="subtext">
            <span class="score">123 points</span> by <a class="hnuser">sunny</a>
            <span class="age">1 hour ago</span> | <a>42 comments</a>
          </td>
        </tr>
        <tr class="spacer"><td>layout spacer</td></tr>
        <tr class="athing comtr">
          <td class="default">
            <div class="comment">
              <span class="comhead">alice 30 minutes ago</span>
              <div class="commtext"><p>This is a useful launch discussion.</p></div>
              <div class="reply">reply link</div>
            </div>
          </td>
        </tr>
        <tr><td class="title"><a class="morelink">More</a></td></tr>
      </table>
    </center></body></html>
    """

    text = web_fetcher._extract_main_text(_soup(html), "https://news.ycombinator.com/item?id=123")
    hints = web_fetcher._search_result_quality_hints("https://news.ycombinator.com/item?id=123")

    assert "Show HN: V8 Agent OS" in text
    assert "123 points" in text
    assert "42 comments" in text
    assert "alice 30 minutes ago" in text
    assert "This is a useful launch discussion." in text
    assert "vote" not in text
    assert "reply link" not in text
    assert "layout spacer" not in text
    assert "More" not in text
    assert hints["catalogSourceId"] == "hacker_news_developer_signal"
    assert hints["catalogCategory"] == "developer_signal"
    assert hints["authorityTier"] == "secondary"
    assert hints["authorityScore"] > web_fetcher._search_result_quality_hints("https://lobste.rs/s/example")["authorityScore"]
    assert hints["tier"] == "secondary"
    assert "secondary_source_hint" in hints["signals"]


def test_chinese_community_profiles_clean_body_without_authority_boost() -> None:
    cases = (
        (
            "https://www.zhihu.com/question/123/answer/456",
            """
            <html><body><main>
              <h1 class="QuestionHeader-title">知乎问题标题</h1>
              <aside class="Question-sideColumn">侧栏推荐</aside>
              <div class="AnswerItem"><div class="RichContent-inner"><p>知乎答案正文。</p></div></div>
              <div class="ContentItem-actions">点赞按钮</div>
            </main></body></html>
            """,
            "知乎答案正文。",
            "侧栏推荐",
        ),
        (
            "https://juejin.cn/post/123",
            """
            <html><body><main>
              <h1 class="article-title">掘金文章标题</h1>
              <article class="markdown-body"><p>掘金文章正文。</p><pre>code()</pre></article>
              <aside class="sidebar">作者推荐</aside>
            </main></body></html>
            """,
            "掘金文章正文。",
            "作者推荐",
        ),
        (
            "https://blog.csdn.net/example/article/details/123",
            """
            <html><body><main id="mainBox">
              <h1 class="title-article">CSDN文章标题</h1>
              <div id="content_views"><p>CSDN文章正文。</p></div>
              <aside class="blog_container_aside">侧边栏广告</aside>
            </main></body></html>
            """,
            "CSDN文章正文。",
            "侧边栏广告",
        ),
        (
            "https://www.cnblogs.com/example/p/123.html",
            """
            <html><body><div id="mainContent">
              <a class="postTitle">博客园文章标题</a>
              <div id="cnblogs_post_body"><p>博客园文章正文。</p></div>
              <div id="sideBar">侧边栏目录</div>
            </div></body></html>
            """,
            "博客园文章正文。",
            "侧边栏目录",
        ),
    )

    for url, html, expected, noise in cases:
        text = web_fetcher._extract_main_text(_soup(html), url)
        hints = web_fetcher._search_result_quality_hints(url)

        assert expected in text
        assert noise not in text
        assert hints["tier"] == "weak"
        assert "low_quality_host_hint" in hints["signals"]


def test_academic_paper_profiles_clean_body_with_existing_site_profile_path() -> None:
    cases = (
        (
            "https://openreview.net/forum?id=example",
            """
            <html><body><main class="forum-container">
              <nav>OpenReview navigation</nav>
              <h1 class="forum-title">A Better Alignment Method</h1>
              <div class="authors">Alice Example, Bob Example</div>
              <div class="abstract"><p>This paper introduces a stable alignment method.</p></div>
              <div class="tldr">TL;DR: Stable alignment.</div>
              <div class="note-replies"><p>Reviewer discussion noise.</p></div>
            </main></body></html>
            """,
            ("A Better Alignment Method", "Alice Example", "stable alignment method", "TL;DR"),
            ("OpenReview navigation", "Reviewer discussion noise"),
            "academic_paper_primary",
            "primary",
        ),
        (
            "https://aclanthology.org/2024.acl-long.1/",
            """
            <html><body><main id="main-container">
              <header>ACL header</header>
              <h1 id="title">Efficient Parsing for Long Documents</h1>
              <div class="authors">Chen Example and Singh Example</div>
              <div class="venue">ACL 2024</div>
              <div class="abstract"><p>We present a parser for long documents.</p></div>
              <div class="related">Related proceedings noise</div>
            </main></body></html>
            """,
            ("Efficient Parsing", "Chen Example", "ACL 2024", "parser for long documents"),
            ("ACL header", "Related proceedings noise"),
            "academic_paper_primary",
            "primary",
        ),
        (
            "https://pubmed.ncbi.nlm.nih.gov/12345678/",
            """
            <html><body><main id="article-page">
              <h1 class="heading-title">Clinical Study of Example Treatment</h1>
              <ul class="authors-list"><li>Rivera A</li><li>Ng B</li></ul>
              <div class="cit">Nature Medicine. 2026 Jul.</div>
              <div class="abstract"><p>Objective: evaluate treatment response.</p></div>
              <div class="identifiers"><span class="doi">doi:10.1000/example</span></div>
              <div class="similar-articles">Similar article noise</div>
            </main></body></html>
            """,
            ("Clinical Study", "Rivera A", "Nature Medicine", "treatment response", "doi:10.1000/example"),
            ("Similar article noise",),
            "academic_paper_primary",
            "primary",
        ),
        (
            "https://dl.acm.org/doi/10.1145/example",
            """
            <html><body><main>
              <h1 class="citation__title">A User Study of AI Assistants</h1>
              <div class="citation__authors">Kim Example; Patel Example</div>
              <section class="abstractInFull"><p>We report findings from a controlled user study.</p></section>
              <div class="doi">https://doi.org/10.1145/example</div>
              <div class="recommendations">Recommended paper noise</div>
              <div class="article__references">Reference list noise</div>
            </main></body></html>
            """,
            ("A User Study", "Kim Example", "controlled user study", "10.1145/example"),
            ("Recommended paper noise", "Reference list noise"),
            "academic_paper_primary",
            "primary",
        ),
        (
            "https://paperswithcode.com/paper/example",
            """
            <html><body><main class="paper-detail">
              <h1 class="paper-title">ExampleNet for Image Classification</h1>
              <div class="paper-abstract"><p>ExampleNet improves top-1 accuracy.</p></div>
              <div class="tasks"><p>Task: Image Classification</p></div>
              <div class="leaderboard"><p>Leaderboard: 90.1 top-1</p></div>
              <div class="comments">Discussion noise</div>
            </main></body></html>
            """,
            ("ExampleNet", "top-1 accuracy", "Image Classification", "Leaderboard: 90.1"),
            ("Discussion noise",),
            "academic_benchmark_secondary",
            "secondary",
        ),
    )

    for url, html, expected_texts, noise_texts, catalog_id, tier in cases:
        text = web_fetcher._extract_main_text(_soup(html), url)
        hints = web_fetcher._search_result_quality_hints(url)
        _profile_key, _candidates, profile_selectors, _selector_entries = web_fetcher._selector_candidates_for_extract(url, "article")

        for expected in expected_texts:
            assert expected in text
        for noise in noise_texts:
            assert noise not in text
        assert hints["catalogSourceId"] == catalog_id
        assert hints["tier"] == tier
        assert profile_selectors


def test_finance_crypto_and_shopping_profiles_clean_with_existing_site_profile_path() -> None:
    cases = (
        (
            "https://www.sec.gov/Archives/edgar/data/example",
            """
            <html><body><main>
              <nav>SEC navigation</nav>
              <h1>Form 10-K Annual Report</h1>
              <div class="article-content">
                <p>Registrant revenue increased in fiscal year 2026.</p>
              </div>
              <aside class="sidebar">Related filings sidebar</aside>
              <div class="recommend">Recommended disclosure links</div>
            </main></body></html>
            """,
            ("Form 10-K Annual Report", "Registrant revenue increased"),
            ("SEC navigation", "Related filings sidebar", "Recommended disclosure links"),
            "us_equity_primary",
            "primary",
        ),
        (
            "https://www.cninfo.com.cn/new/disclosure/detail",
            """
            <html><body><main>
              <header>巨潮导航</header>
              <h1 class="announcement-title">关于重大资产重组的公告</h1>
              <div class="detail-content"><p>公司董事会审议通过相关议案。</p></div>
              <div class="search-box">搜索公告</div>
              <aside>右侧推荐</aside>
            </main></body></html>
            """,
            ("关于重大资产重组的公告", "公司董事会审议通过相关议案。"),
            ("巨潮导航", "搜索公告", "右侧推荐"),
            "cn_equity_primary",
            "primary",
        ),
        (
            "https://www.binance.com/en/support/announcement/example",
            """
            <html><body><main>
              <div class="trade">BTCUSDT trading widget</div>
              <h1 class="announcement-title">Binance Will List Example Token</h1>
              <article class="article-content"><p>Trading will open at 2026-07-08 10:00 UTC.</p></article>
              <div class="promotion">Download app promotion</div>
            </main></body></html>
            """,
            ("Binance Will List Example Token", "Trading will open"),
            ("BTCUSDT trading widget", "Download app promotion"),
            "crypto_market_primary",
            "primary",
        ),
        (
            "https://etherscan.io/tx/0x123",
            """
            <html><body><main>
              <nav class="navbar">Explorer menu</nav>
              <h1>Transaction Details</h1>
              <div class="card-body"><p>Status: Success</p><p>Value: 1 ETH</p></div>
              <div class="ads">Sponsored validator ad</div>
            </main></body></html>
            """,
            ("Transaction Details", "Status: Success", "Value: 1 ETH"),
            ("Explorer menu", "Sponsored validator ad"),
            "crypto_onchain_primary",
            "primary",
        ),
        (
            "https://www.amazon.com/dp/B000000",
            """
            <html><body><main id="dp">
              <h1 id="productTitle">Portable Monitor 15.6 inch</h1>
              <div class="a-price">$199.99</div>
              <div id="availability">In Stock</div>
              <div class="seller">Sold by Example Store</div>
              <div class="specs"><p>Resolution: 1080p</p></div>
              <div class="recommendation">Customers also bought noisy item</div>
              <div class="reviews">Very long review list</div>
            </main></body></html>
            """,
            ("Portable Monitor 15.6 inch", "$199.99", "In Stock", "Sold by Example Store", "Resolution: 1080p"),
            ("Customers also bought noisy item", "Very long review list"),
            "shopping_platform_primary",
            "primary",
        ),
        (
            "https://item.jd.com/100000.html",
            """
            <html><body><main id="item">
              <div class="sku-name">京东自营机械键盘</div>
              <div class="p-price">￥399.00</div>
              <div class="stock">现货</div>
              <div class="shopName">京东自营旗舰店</div>
              <div class="Ptable"><p>轴体：茶轴</p></div>
              <div class="recommend">猜你喜欢</div>
              <div class="comment">用户评论长列表</div>
            </main></body></html>
            """,
            ("京东自营机械键盘", "￥399.00", "现货", "京东自营旗舰店", "轴体：茶轴"),
            ("猜你喜欢", "用户评论长列表"),
            "shopping_platform_primary",
            "primary",
        ),
    )

    for url, html, expected_texts, noise_texts, catalog_id, tier in cases:
        text = web_fetcher._extract_main_text(_soup(html), url)
        hints = web_fetcher._search_result_quality_hints(url)
        _profile_key, _candidates, profile_selectors, _selector_entries = web_fetcher._selector_candidates_for_extract(url, "article")

        for expected in expected_texts:
            assert expected in text
        for noise in noise_texts:
            assert noise not in text
        assert hints["catalogSourceId"] == catalog_id
        assert hints["tier"] == tier
        assert "primary_source_hint" in hints["signals"]
        assert profile_selectors


def test_secondary_market_and_crypto_aggregate_sources_are_marked_as_supporting_evidence() -> None:
    cases = {
        "https://finance.yahoo.com/quote/AAPL": "market_data_secondary",
        "https://www.coingecko.com/en/coins/bitcoin": "crypto_aggregate_secondary",
        "https://defillama.com/protocol/example": "crypto_aggregate_secondary",
    }

    for url, catalog_id in cases.items():
        hints = web_fetcher._search_result_quality_hints(url)

        assert hints["catalogSourceId"] == catalog_id
        assert hints["authorityTier"] == "secondary"
        assert hints["tier"] == "secondary"
        assert "secondary_source_hint" in hints["signals"]


def test_paywall_and_removed_market_hosts_are_not_trusted_catalog_sources() -> None:
    urls = (
        "https://www.nature.com/articles/example",
        "https://www.science.org/doi/10.1126/science.example",
        "https://ieeexplore.ieee.org/document/1234567",
        "https://britannica.com/topic/example",
        "https://www.britannica.com/topic/example",
        "https://www.tradingview.com/symbols/NASDAQ-AAPL/",
    )

    for url in urls:
        hints = web_fetcher._search_result_quality_hints(url)

        assert hints["catalogSourceId"] is None
        assert hints["catalogCategory"] is None
        assert hints["authorityTier"] is None
        assert "primary_source_hint" not in hints["signals"]
        assert "secondary_source_hint" not in hints["signals"]
        assert "encyclopedic_background_source" not in hints["signals"]


def test_auto_fetch_uses_reader_fallback_after_static_challenge_and_browser_unavailable() -> None:
    class _StaticFetcher:
        @staticmethod
        def get(_url: str, **_kwargs: object) -> object:
            return type(
                "Response",
                (),
                {
                    "html_content": "<html><head><title>Just a moment...</title></head><body></body></html>",
                    "url": "https://www.npmjs.com/package/react",
                    "status": 403,
                },
            )()

    class _ReaderResponse:
        status_code = 200
        text = (
            "Title: react\n\n"
            "URL Source: https://www.npmjs.com/package/react\n\n"
            "Markdown Content:\n"
            "## `react`\n\n"
            "React is a JavaScript library for creating user interfaces."
        )

    with patch("core.tools.web_fetcher._try_import_static_fetcher", return_value=(_StaticFetcher, None)), patch(
        "core.tools.web_fetcher._try_import_dynamic_fetcher",
        return_value=(None, "playwright browser missing"),
    ), patch(
        "core.tools.web_fetcher._try_import_stealth_fetcher",
        return_value=(None, "playwright browser missing"),
    ), patch(
        "core.tools.web_fetcher.requests.get",
        return_value=_ReaderResponse(),
    ) as reader_get:
        payload = json.loads(web_fetcher.web_read.func(url="https://www.npmjs.com/package/react", mode="auto"))

    assert payload["ok"] is True
    assert payload["fetchMode"] == "reader"
    assert payload["attemptedModes"] == ["static", "dynamic", "stealth", "reader"]
    assert payload["metadata"]["readerFallbackProvider"] == "jina"
    assert "React is a JavaScript library" in payload["text"]
    reader_get.assert_called_once()
    assert reader_get.call_args.args[0] == "https://r.jina.ai/https://www.npmjs.com/package/react"


def test_reader_fallback_honors_research_text_depth_without_expanding_default_surface() -> None:
    late_marker = "LATE_CLICK_PATH_TYPE_CONTRACT"

    class _StaticFetcher:
        @staticmethod
        def get(_url: str, **_kwargs: object) -> object:
            return type(
                "Response",
                (),
                {
                    "html_content": "<html><head><title>Just a moment...</title></head><body></body></html>",
                    "url": "https://docs.example.com/reference",
                    "status": 403,
                },
            )()

    class _ReaderResponse:
        status_code = 200
        text = (
            "Title: Deep reference\n\n"
            "URL Source: https://docs.example.com/reference\n\n"
            "Markdown Content:\n"
            + ("early API detail " * 1_000)
            + late_marker
            + " records path_type=pathlib.Path."
        )

    with patch("core.tools.web_fetcher._try_import_static_fetcher", return_value=(_StaticFetcher, None)), patch(
        "core.tools.web_fetcher._try_import_dynamic_fetcher",
        return_value=(None, "playwright browser missing"),
    ), patch(
        "core.tools.web_fetcher._try_import_stealth_fetcher",
        return_value=(None, "playwright browser missing"),
    ), patch(
        "core.tools.web_fetcher.requests.get",
        return_value=_ReaderResponse(),
    ):
        default_payload = json.loads(
            web_fetcher.web_read.func(url="https://docs.example.com/reference", mode="auto")
        )
        research_payload = json.loads(
            web_fetcher.web_read.func(
                url="https://docs.example.com/reference",
                mode="auto",
                maxTextChars=32_000,
            )
        )

    assert late_marker not in default_payload["text"]
    assert "...[TRUNCATED]" in default_payload["text"]
    assert late_marker in research_payload["text"]
    assert "path_type=pathlib.Path" in research_payload["text"]
