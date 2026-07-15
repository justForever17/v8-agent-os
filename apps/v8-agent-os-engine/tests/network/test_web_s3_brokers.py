from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.delegation_broker import (
    default_external_worker_descriptors,
    normalize_external_worker_descriptors,
    render_external_worker_command,
)
from core.native_tools import command_session_broker, delegation_broker, run_system_command
from core.source_provider_registry import get_source_provider_capabilities, get_source_provider_config_defaults, get_source_router_defaults
from core.tools.s3_tools import s3_broker
from core.tools.web_fetcher import _WEB_BROKER_CONTEXT_COUNTS, WebPagePayload, web_broker, web_extract, web_read, web_search
from core.workspace_capability import WorkspaceBinding


class WebAndS3BrokerTests(unittest.TestCase):
    def _page(self, *, html: str, url: str = "https://example.com/doc") -> WebPagePayload:
        return WebPagePayload(
            url=url,
            final_url=url,
            requested_mode="auto",
            referer_mode="none",
            referer_url="",
            fetch_mode="static",
            attempted_modes=["static"],
            available_modes={},
            status=200,
            tls_strategy="default",
            ca_bundle_path="",
            proxy_bypass_used=False,
            title="",
            text="",
            html=html,
            metadata={},
            links=[],
            media=[],
            warnings=[],
        )

    def test_source_provider_registry_loads_config_defaults(self):
        providers = get_source_provider_capabilities()
        router_defaults = get_source_router_defaults()
        config_defaults = get_source_provider_config_defaults()

        self.assertIn("brave", providers)
        self.assertIn("duckduckgo", providers)
        self.assertIn("globalPreferred", router_defaults)
        self.assertEqual(config_defaults["brave"]["authEnv"], "BRAVE_SEARCH_API_KEY")
        self.assertIn("enabled", config_defaults["duckduckgo"])

    def test_web_read_returns_clean_markdown_without_page_chrome(self):
        html = """
        <html><head><title>Demo</title><style>.x{}</style><script>bad()</script></head>
        <body><header>Top nav</header><nav>Menu item</nav><main>
        <h1>Important Title</h1><p>Useful paragraph.</p><ul><li>First point</li></ul>
        </main><footer>Legal footer</footer></body></html>
        """
        with patch("core.tools.web_fetcher._fetch_with_scrapling_internal", return_value=self._page(html=html)):
            payload = json.loads(web_read.func(url="https://example.com/doc"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["contentFormat"], "markdown")
        self.assertIn("# Important Title", payload["text"])
        self.assertIn("- First point", payload["text"])
        self.assertNotIn("Top nav", payload["text"])
        self.assertNotIn("bad()", payload["text"])

    def test_web_read_auto_skips_anti_crawl_static_result(self):
        class _StaticFetcher:
            @staticmethod
            def get(_url, **_kwargs):
                return type(
                    "Response",
                    (),
                    {
                        "html_content": "<html><head><title>百度百科-验证</title></head><body>验证码</body></html>",
                        "url": "https://baike.baidu.com/anticrawl/captchaview",
                        "status": 200,
                    },
                )()

        class _DynamicFetcher:
            @staticmethod
            def fetch(url, **_kwargs):
                return type(
                    "Response",
                    (),
                    {
                        "html_content": (
                            "<html><head><title>Clean page</title></head><body><main>"
                            "<h1>Clean page</h1>"
                            "<p>This dynamic fallback contains enough article text to be accepted by the "
                            "quality-aware auto fetch path instead of returning a blocked verification page. "
                            "It also keeps a second sentence so the extracted Markdown is safely above "
                            "the weak-content threshold used by auto mode.</p>"
                            "</main></body></html>"
                        ),
                        "url": url,
                        "status": 200,
                    },
                )()

        with patch("core.tools.web_fetcher._try_import_static_fetcher", return_value=(_StaticFetcher, None)), patch(
            "core.tools.web_fetcher._try_import_dynamic_fetcher",
            return_value=(_DynamicFetcher, None),
        ), patch("core.tools.web_fetcher._resolve_verify_candidates", return_value=[("default", True)]):
            payload = json.loads(web_read.func(url="https://baike.baidu.com/item/demo", mode="auto"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["fetchMode"], "dynamic")
        self.assertEqual(payload["attemptedModes"], ["static", "dynamic"])
        self.assertIn("dynamic fallback", payload["text"])
        self.assertTrue(payload["fallbackUsed"])
        self.assertIn("verification_or_anti_crawl", " ".join(payload["warnings"]))

    def test_web_read_auto_uses_allowlisted_agent_browser_profile_without_explicit_flag(self):
        captured_browser_kwargs = {}

        class _StaticFetcher:
            @staticmethod
            def get(_url, **_kwargs):
                return type(
                    "Response",
                    (),
                    {
                        "html_content": "<html><head><title>Login required</title></head><body>请登录后继续访问</body></html>",
                        "url": "https://example.com/private",
                        "status": 403,
                    },
                )()

        class _DynamicFetcher:
            @staticmethod
            def fetch(url, **kwargs):
                captured_browser_kwargs.update(kwargs)
                return type(
                    "Response",
                    (),
                    {
                        "html_content": (
                            "<html><head><title>Private page</title></head><body><main>"
                            "<h1>Private page</h1>"
                            "<p>This login-backed content is now available from the Agent browser profile. "
                            "The page includes enough private article text to pass the normal extraction "
                            "quality gate after static fetching reports a login challenge. This proves that "
                            "allowlisted web and research reads can reuse the dedicated browser session without "
                            "requiring every caller to pass an explicit useAgentBrowserProfile flag. The content "
                            "stays inside browser-backed fetching and cookies are not exported to the model.</p>"
                            "</main></body></html>"
                        ),
                        "url": url,
                        "status": 200,
                    },
                )()

        with patch(
            "core.tools.web_fetcher.get_web_fetch_config",
            return_value={"useAgentBrowserProfile": True, "agentBrowserProfileAllowlist": ["example.com"]},
        ), patch(
            "core.tools.web_fetcher.configured_agent_browser_profile_dir",
            return_value="E:/tmp/v8-agent-browser-profile",
        ), patch(
            "core.tools.web_fetcher._try_import_static_fetcher",
            return_value=(_StaticFetcher, None),
        ), patch(
            "core.tools.web_fetcher._try_import_dynamic_fetcher",
            return_value=(_DynamicFetcher, None),
        ), patch(
            "core.tools.web_fetcher._resolve_verify_candidates",
            return_value=[("default", True)],
        ):
            payload = json.loads(web_read.func(url="https://example.com/private", mode="auto"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["fetchMode"], "dynamic")
        self.assertEqual(payload["attemptedModes"], ["static", "dynamic"])
        self.assertEqual(captured_browser_kwargs["user_data_dir"], "E:/tmp/v8-agent-browser-profile")
        self.assertTrue(payload["agentBrowserProfile"]["used"])
        self.assertEqual(payload["agentBrowserProfile"]["matchedHost"], "example.com")
        self.assertIn("login-backed content", payload["text"])

    def test_web_read_baidu_baike_profile_prefers_summary_and_paragraphs(self):
        html = """
        <html><head><title>李白_百度百科</title></head><body>
        <main>
          <div class="lemma-catalog"><a>目录</a><ol><li>噪声目录项</li></ol></div>
          <div class="lemma-summary">李白，字太白，号青莲居士。</div>
          <table class="basic-info"><tr><td>表格噪声</td></tr></table>
          <div class="para">李白是唐代伟大的浪漫主义诗人。</div>
          <div class="lemmaWgt-relation"><div class="para">关系模块噪声</div></div>
          <aside class="side-content"><div class="para">侧栏噪声</div></aside>
          <div class="some-module"><div class="para">模块噪声</div></div>
        </main>
        </body></html>
        """
        with patch(
            "core.tools.web_fetcher._fetch_with_scrapling_internal",
            return_value=self._page(html=html, url="https://baike.baidu.com/item/%E6%9D%8E%E7%99%BD"),
        ):
            payload = json.loads(web_read.func(url="https://baike.baidu.com/item/%E6%9D%8E%E7%99%BD"))

        self.assertTrue(payload["ok"])
        self.assertIn("李白，字太白", payload["text"])
        self.assertIn("李白是唐代伟大的浪漫主义诗人", payload["text"])
        self.assertNotIn("噪声目录项", payload["text"])
        self.assertNotIn("表格噪声", payload["text"])
        self.assertNotIn("关系模块噪声", payload["text"])
        self.assertNotIn("侧栏噪声", payload["text"])
        self.assertNotIn("模块噪声", payload["text"])

    def test_web_extract_raw_html_keeps_dom_preview_for_ui_reference(self):
        html = "<html><body><main><button id='submit'>Submit</button><form><input name='email'></form></main></body></html>"
        with patch("core.tools.web_fetcher._fetch_with_scrapling_internal", return_value=self._page(html=html)):
            payload = json.loads(web_extract.func(url="https://example.com/form", extract="raw_html"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["extract"], "raw_html")
        self.assertIn("<button", payload["rawHtml"])
        self.assertEqual(payload["contentFormat"], "raw_html")
        self.assertIn("htmlChars", payload)

    def test_web_extract_ui_snapshot_exposes_structure_not_full_html(self):
        html = "<html><body><main><h1>Sign in</h1><button aria-label='Continue'>Go</button><a href='/help'>Help</a></main></body></html>"
        with patch("core.tools.web_fetcher._fetch_with_scrapling_internal", return_value=self._page(html=html)):
            payload = json.loads(web_extract.func(url="https://example.com/login", extract="ui_snapshot"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["extract"], "ui_snapshot")
        labels = " ".join(str(item.get("text", "")) for item in payload["uiSnapshot"])
        self.assertIn("Continue", labels)
        self.assertIn("Sign in", labels)

    def test_web_extract_login_wall_reports_needs_login(self):
        page = self._page(html="<html><body><main>请登录后继续</main></body></html>", url="https://example.com/login")
        page.title = "账号登录"
        page.status = 200
        with patch("core.tools.web_fetcher._fetch_with_scrapling_internal", return_value=page):
            payload = json.loads(web_extract.func(url="https://example.com/login", extract="article"))

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["failureClass"], "needs_login")
        self.assertIn("Agent 专用浏览器", payload["recommendedNextAction"])

    def test_web_broker_fetch_mode_dispatches_to_unified_web_fetch(self):
        with patch(
            "core.tools.web_fetcher.web_fetch.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "url": "https://example.com",
                    "finalUrl": "https://example.com/final",
                    "requestedMode": "auto",
                    "fetchMode": "dynamic",
                    "title": "Example",
                    "text": "hello world",
                    "attemptedModes": ["static", "dynamic"],
                    "adaptiveSignals": {"score": 0.9},
                },
                ensure_ascii=False,
            ),
        ) as mocked:
            result = web_broker.func(target="https://example.com", mode="fetch")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "read")
        self.assertEqual(payload["title"], "Example")
        self.assertNotIn("attemptedModes", payload)
        self.assertNotIn("adaptiveSignals", payload)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["intent"], "auto")
        self.assertEqual(mocked.call_args.kwargs["target"], "https://example.com")
        self.assertEqual(mocked.call_args.kwargs["mode"], "static")

    def test_web_broker_debug_mode_moves_transport_fields_under_debug(self):
        with patch(
            "core.tools.web_fetcher.web_fetch.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "query": "v8",
                    "provider": "bing",
                    "results": [{"title": "V8", "url": "https://example.com", "snippet": "demo"}],
                    "attemptedProviders": [{"provider": "bing", "status": "ok", "resultCount": 1}],
                    "searchUrl": "https://www.bing.com/search?q=v8",
                    "sourceCapability": {"role": "discovery"},
                    "networkRoute": "global_proxy",
                    "providerAttemptMatrix": [{"provider": "bing", "status": "ok"}],
                    "sourceRouter": {"selectedProvider": "bing"},
                },
                ensure_ascii=False,
            ),
        ):
            result = web_broker.func(target="v8", mode="search", debug=True)

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "search")
        self.assertIn("debug", payload)
        self.assertEqual(payload["debug"]["searchUrl"], "https://www.bing.com/search?q=v8")
        self.assertEqual(payload["debug"]["networkRoute"], "global_proxy")
        self.assertEqual(payload["debug"]["sourceRouter"]["selectedProvider"], "bing")

    def test_web_broker_default_hides_source_router_runtime_fields(self):
        with patch(
            "core.tools.web_fetcher.web_fetch.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "query": "v8",
                    "provider": "bing",
                    "results": [{"title": "V8", "url": "https://example.com", "snippet": "demo"}],
                    "sourceCapability": {"role": "discovery"},
                    "networkRoute": "global_proxy",
                    "providerAttemptMatrix": [{"provider": "bing", "status": "ok"}],
                    "sourceRouter": {"selectedProvider": "bing"},
                },
                ensure_ascii=False,
            ),
        ):
            result = web_broker.func(target="v8", mode="search")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "search")
        self.assertNotIn("debug", payload)
        self.assertNotIn("sourceCapability", payload)
        self.assertNotIn("networkRoute", payload)
        self.assertNotIn("providerAttemptMatrix", payload)
        self.assertNotIn("sourceRouter", payload)

    def test_web_broker_error_surfaces_timeout_without_debug(self):
        with patch(
            "core.tools.web_fetcher.web_fetch.func",
            return_value=json.dumps(
                {
                    "ok": False,
                    "url": "https://zh.wikipedia.org/wiki/demo",
                    "error": "Error reading webpage with Scrapling: Page.goto: net::ERR_CONNECTION_TIMED_OUT",
                    "failureClass": "network_timeout",
                    "attemptedModes": ["static"],
                    "elapsedMs": 45001,
                    "retryable": True,
                    "recommendedNextAction": "换可访问来源或改用 research_broker。",
                    "networkRoute": "global_proxy",
                    "providerAttemptMatrix": [{"provider": "duckduckgo", "status": "timeout"}],
                    "sourceRouter": {"selectedProvider": "duckduckgo"},
                },
                ensure_ascii=False,
            ),
        ):
            result = web_broker.func(target="https://zh.wikipedia.org/wiki/demo", mode="read")

        payload = json.loads(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["failureClass"], "network_timeout")
        self.assertEqual(payload["attemptedModes"], ["static"])
        self.assertEqual(payload["elapsedMs"], 45001)
        self.assertTrue(payload["retryable"])
        self.assertIn("research_broker", payload["recommendedNextAction"])
        self.assertNotIn("networkRoute", payload)
        self.assertNotIn("providerAttemptMatrix", payload)
        self.assertNotIn("sourceRouter", payload)

    def test_web_broker_search_marks_weak_results(self):
        with patch(
            "core.tools.web_fetcher.web_fetch.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "query": "中国象棋规则",
                    "provider": "bing",
                    "resultCount": 1,
                    "results": [
                        {
                            "title": "Weather archive",
                            "url": "https://example.com/weather",
                            "snippet": "Historical rainfall and temperature data.",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        ):
            result = web_broker.func(target="中国象棋规则", mode="search")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["quality"], "weak")
        self.assertLess(payload["sourceQualitySummary"]["averageRelevance"], 20)
        self.assertIn("research_broker", payload["sourceQualitySummary"]["recommendedNextAction"])

    def test_web_broker_warns_after_repeated_single_run_calls(self):
        _WEB_BROKER_CONTEXT_COUNTS.pop("call-web-repeat", None)
        with patch(
            "core.tools.web_fetcher.web_fetch.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "query": "v8",
                    "provider": "duckduckgo",
                    "results": [{"title": "V8", "url": "https://example.com", "snippet": "demo"}],
                },
                ensure_ascii=False,
            ),
        ):
            payload = {}
            for _ in range(4):
                payload = json.loads(web_broker.func(target="v8", mode="search", tool_call_id="call-web-repeat"))

        self.assertEqual(payload["webBrokerCallCount"], 4)
        self.assertIn("researchRuntimeWarning", payload)
        self.assertIn("research_broker", payload["recommendedNextAction"])

    def test_web_search_explicit_metaso_requires_agent_browser_profile_when_not_allowlisted(self):
        with patch(
            "core.tools.web_fetcher.get_web_fetch_config",
            return_value={"useAgentBrowserProfile": False, "agentBrowserProfileAllowlist": []},
        ):
            result = web_broker.func(target="中国象棋规则", mode="search", search_engine="metaso")

        payload = json.loads(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["failureClass"], "needs_agent_browser_login")
        self.assertEqual(payload["attemptedProviders"][0]["status"], "skipped")
        self.assertIn("Agent 专用浏览器", payload["recommendedNextAction"])

    def test_web_search_source_router_prefers_cn_route_for_chinese_query(self):
        config = {
            "useAgentBrowserProfile": False,
            "agentBrowserProfileAllowlist": [],
            "sourceRouter": {"cnPreferred": ["bocha", "metaso"], "globalPreferred": ["duckduckgo"]},
            "providers": {"bocha": {"authEnv": "BOCHA_API_KEY", "enabled": True}},
        }
        with patch("core.tools.web_fetcher.get_web_fetch_config", return_value=config), patch.dict(
            "os.environ",
            {"BOCHA_API_KEY": ""},
            clear=False,
        ), patch(
            "core.tools.web_fetcher._agent_browser_profile_search_skip",
            return_value=None,
        ), patch(
            "core.tools.web_fetcher._metaso_search_public",
            return_value={
                "ok": True,
                "results": [{"title": "中国象棋规则", "url": "https://example.cn/chess", "snippet": "象棋规则。"}],
                "resultId": "r1",
            },
        ):
            payload = json.loads(web_search.func(query="中国象棋规则", limit=2, search_engine="auto"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "metaso")
        self.assertEqual(payload["networkRoute"], "cn_direct")
        self.assertEqual(payload["sourceRouter"]["locale"], "cn")
        self.assertEqual(payload["attemptedProviders"][0]["failureClass"], "credential_missing")
        self.assertEqual(payload["sourceCapability"]["region"], "cn")

    def test_web_search_source_router_skips_missing_global_api_key(self):
        config = {
            "sourceRouter": {"globalPreferred": ["brave", "duckduckgo"], "cnPreferred": ["metaso"]},
            "providers": {"brave": {"authEnv": "BRAVE_SEARCH_API_KEY", "enabled": True}},
            "useAgentBrowserProfile": False,
            "agentBrowserProfileAllowlist": [],
        }
        html = """
        <html><body>
          <div class="result"><a class="result__a" href="https://docs.example.com/api">Official API</a>
          <a class="result__snippet">Official documentation for the API.</a></div>
        </body></html>
        """
        with patch("core.tools.web_fetcher.get_web_fetch_config", return_value=config), patch.dict(
            "os.environ",
            {"BRAVE_SEARCH_API_KEY": ""},
            clear=False,
        ), patch(
            "core.tools.web_fetcher._fetch_with_scrapling_internal",
            return_value=self._page(html=html, url="https://html.duckduckgo.com/html/?q=api"),
        ):
            payload = json.loads(web_search.func(query="official API docs", search_engine="auto"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "duckduckgo")
        self.assertEqual(payload["sourceRouter"]["locale"], "global")
        self.assertEqual(payload["networkRoute"], "global_proxy")
        self.assertEqual(payload["attemptedProviders"][0]["provider"], "brave")
        self.assertEqual(payload["attemptedProviders"][0]["failureClass"], "credential_missing")

    def test_web_search_explicit_brave_without_key_returns_credential_missing(self):
        with patch(
            "core.tools.web_fetcher.get_web_fetch_config",
            return_value={"providers": {"brave": {"authEnv": "BRAVE_SEARCH_API_KEY", "enabled": True}}},
        ), patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": ""}, clear=False):
            payload = json.loads(web_search.func(query="api docs", search_engine="brave"))

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["failureClass"], "credential_missing")
        self.assertEqual(payload["sourceRouter"]["plannedProviders"], ["brave"])

    def test_web_search_brave_api_adapter_returns_clean_results(self):
        class _Response:
            status_code = 200
            headers = {"content-type": "application/json"}

            def json(self):
                return {
                    "web": {
                        "results": [
                            {
                                "title": "Official API",
                                "url": "https://docs.example.com/api",
                                "description": "Official documentation for the API.",
                            }
                        ]
                    }
                }

        with patch(
            "core.tools.web_fetcher.get_web_fetch_config",
            return_value={"providers": {"brave": {"authEnv": "BRAVE_SEARCH_API_KEY", "apiKey": "brave-config-key", "enabled": True}}},
        ), patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": ""}, clear=False), patch(
            "core.tools.web_fetcher.requests.get",
            return_value=_Response(),
        ) as mocked_get:
            payload = json.loads(web_search.func(query="api docs", search_engine="brave"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "brave")
        self.assertEqual(payload["results"][0]["url"], "https://docs.example.com/api")
        self.assertEqual(payload["results"][0]["source"], "brave")
        self.assertEqual(payload["sourceCapability"]["role"], "discovery")
        self.assertEqual(mocked_get.call_args.kwargs["headers"]["X-Subscription-Token"], "brave-config-key")

    def test_web_search_metaso_api_adapter_uses_configured_key_and_scope(self):
        class _Response:
            status_code = 200
            headers = {"content-type": "application/json"}

            def json(self):
                return {
                    "results": [
                        {
                            "title": "MetaSo API",
                            "url": "https://metaso.cn/search-api/playground",
                            "snippet": "Search API playground.",
                        }
                    ]
                }

        with patch(
            "core.tools.web_fetcher.get_web_fetch_config",
            return_value={"providers": {"metaso": {"authEnv": "METASO_API_KEY", "apiKey": "metaso-config-key", "enabled": True}}},
        ), patch("core.tools.web_fetcher.requests.post", return_value=_Response()) as mocked_post:
            payload = json.loads(web_search.func(query="秘塔 API", search_engine="metaso", search_vertical="image"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "metaso")
        self.assertEqual(payload["results"][0]["url"], "https://metaso.cn/search-api/playground")
        self.assertEqual(payload["metaso"]["scope"], "image")
        self.assertEqual(mocked_post.call_args.kwargs["headers"]["Authorization"], "Bearer metaso-config-key")
        self.assertEqual(mocked_post.call_args.kwargs["json"]["scope"], "image")

    def test_web_search_searxng_json_disabled_reports_format_unavailable(self):
        class _Response:
            status_code = 200
            headers = {"content-type": "text/html"}

            def json(self):
                raise ValueError("not json")

        with patch(
            "core.tools.web_fetcher.get_web_fetch_config",
            return_value={"providers": {"searxng": {"baseUrl": "https://search.example", "enabled": True}}},
        ), patch("core.tools.web_fetcher.requests.get", return_value=_Response()):
            payload = json.loads(web_search.func(query="api docs", search_engine="searxng"))

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["failureClass"], "provider_format_unavailable")
        self.assertEqual(payload["sourceRouter"]["selectedProvider"], "searxng")

    def test_web_broker_read_mode_forces_read_intent(self):
        with patch("core.tools.web_fetcher.web_fetch.func", return_value='{"ok": true, "mode": "read"}') as mocked:
            result = web_broker.func(target="https://example.com/doc", mode="read", fetch_mode="dynamic")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(mocked.call_args.kwargs["intent"], "read")
        self.assertEqual(mocked.call_args.kwargs["mode"], "dynamic")

    def test_web_broker_baidu_baike_is_background_source_hint(self):
        with patch(
            "core.tools.web_fetcher.web_fetch.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "query": "李白 百度百科",
                    "provider": "fake",
                    "results": [
                        {
                            "title": "李白_百度百科",
                            "url": "https://baike.baidu.com/item/%E6%9D%8E%E7%99%BD/1043",
                            "snippet": "唐代诗人。",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        ):
            result = web_broker.func(target="李白 百度百科", mode="search")

        payload = json.loads(result)
        hints = payload["results"][0]["sourceQualityHints"]
        self.assertEqual(hints["host"], "baike.baidu.com")
        self.assertIn("encyclopedic_background_source", hints["signals"])
        self.assertEqual(hints["tier"], "secondary")

    def test_s3_broker_upload_mode_returns_structured_json(self):
        with patch(
            "core.tools.s3_tools.upload_file_to_s3",
            return_value={
                "bucket": "demo-bucket",
                "key": "demo.txt",
                "url": "https://cdn.example.com/demo.txt",
                "contentType": "text/plain",
                "size": 42,
            },
        ) as mocked:
            result = s3_broker.func(mode="upload", file_path="E:/tmp/demo.txt", key="demo.txt", prefix="demo")

        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "upload")
        self.assertEqual(payload["bucket"], "demo-bucket")
        self.assertEqual(payload["key"], "demo.txt")
        mocked.assert_called_once_with("E:/tmp/demo.txt", key="demo.txt", prefix="demo")

    def test_s3_broker_download_requires_destination(self):
        payload = json.loads(s3_broker.func(mode="download", key="demo.txt", destination_path=""))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "missing_destination_path")

    def test_run_system_command_auto_starts_session_preferred_commands(self):
        with patch(
            "core.native_tools._launch_background_command",
            return_value={
                "commandId": "cmd-dev-1",
                "sessionId": "cmd-dev-1",
                "runId": "run-dev-1",
                "interactive": False,
                "profile": "shell",
                "status": {"is_running": True, "awaiting_input": False},
                "initialOutput": "",
            },
        ):
            payload = json.loads(run_system_command.func(command="npm run dev", mode="auto"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "command_session")
        self.assertEqual(payload["mode"], "session")
        self.assertEqual(payload["command"], "npm run dev")
        self.assertEqual(payload["recommendedNextAction"], "observe")
        self.assertEqual(payload["state"], "running")

    def test_command_session_broker_start_returns_process_link_contract(self):
        with patch(
            "core.native_tools._launch_background_command",
            return_value={
                "commandId": "cmd123",
                "mode": "interactive",
                "tty": "pty",
                "sessionId": "chat-session-1",
                "runId": "run-1",
                "status": {
                    "is_running": True,
                    "interactive": True,
                    "awaiting_input": False,
                    "observation_state": "busy",
                },
                "interactive": True,
                "profile": "chat_cli",
                "profileReason": "ai_cli_detected",
                "initialOutput": "Booting...",
            },
        ):
            payload = json.loads(command_session_broker.func(mode="start", command="qwen"))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "start")
        self.assertEqual(payload["kind"], "command_session")
        self.assertEqual(payload["commandId"], "cmd123")
        self.assertEqual(payload["sessionId"], "cmd123")
        self.assertEqual(payload["recommendedNextAction"], "observe")
        self.assertEqual(payload["state"], "running")

    def test_delegation_broker_dispatch_starts_external_worker_session(self):
        descriptor = {
            "id": "coding-cli-worker",
            "name": "Coding CLI Worker",
            "description": "External coding worker",
            "enabled": True,
            "workerType": "coding_cli",
            "capabilitySnapshot": {
                "agentClass": "external_worker",
                "domainTags": ["software_engineering"],
                "operationCapabilities": ["implement"],
                "externalWorkerSuitability": "high",
            },
            "launchProfile": {
                "commandTemplate": "worker --task {task_brief_b64}",
                "cwdPolicy": "inherit_workspace",
                "envPassThrough": [],
                "startupTimeoutSeconds": 10,
            },
            "sessionMode": "interactive",
            "allowedSideEffects": ["workspace_write"],
            "resultSchema": {
                "type": "v8_worker_result_v1",
                "markers": ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"],
            },
        }

        with patch("core.native_tools.storage.get_all_agents", return_value=[]), patch(
            "core.native_tools.storage.get_supervisor_config",
            return_value={"delegation": {"externalWorkers": [descriptor]}},
        ), patch(
            "core.native_tools.command_session_broker.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "mode": "start",
                    "kind": "command_session",
                    "commandId": "cmd-ext-1",
                    "sessionId": "cmd-ext-1",
                    "runId": "run-ext-1",
                    "state": "running",
                    "summary": "worker started",
                    "recommendedNextAction": "observe",
                }
            ),
        ) as mocked_start:
            command = delegation_broker.func(
                mode="dispatch",
                tasks=[
                    {
                        "taskBriefId": "task-impl",
                        "goal": "Implement the requested patch",
                        "requiredCapabilities": ["software_engineering", "implement"],
                        "executionLaneHint": "external_worker",
                        "preferredWorkerType": "coding_cli",
                    }
                ],
                state={"run_id": "run-supervisor-1", "workspace_path": "E:/Projects/v8chat"},
            )

        payload = json.loads(command.update["messages"][0].content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "dispatch")
        self.assertEqual(payload["recommendedNextAction"], "observe")
        self.assertEqual(payload["items"][0]["lane"], "external_worker")
        self.assertEqual(payload["items"][0]["targetId"], "coding-cli-worker")
        self.assertEqual(payload["items"][0]["commandSession"]["commandId"], "cmd-ext-1")
        self.assertFalse(payload["items"][0]["resultSchemaMatched"])
        self.assertIn(payload["items"][0]["selectionReason"], {"preferredWorkerType", "strong_capability_match", "moderate_capability_match"})
        self.assertGreaterEqual(payload["items"][0]["selectionConfidence"], 0)
        self.assertIsInstance(payload["items"][0]["matchSignals"], list)
        self.assertEqual(payload["items"][0]["supervisorAcceptance"]["status"], "pending")
        mocked_start.assert_called_once()
        self.assertEqual(mocked_start.call_args.kwargs["mode"], "start")

    def test_delegation_broker_dispatch_local_subagent_records_parallel_invocation_timestamp(self):
        agent = {
            "id": "engineering-impl",
            "name": "Engineering Implementer",
            "isEnabled": True,
            "description": "Implements bounded code changes.",
            "capabilitySnapshot": {
                "agentClass": "executor",
                "specialistFamily": "engineering",
                "domainTags": ["software_engineering"],
                "operationCapabilities": ["implement", "workspace_changes"],
                "artifactCapabilities": ["apps/v8-agent-os-engine"],
            },
        }

        with patch("core.native_tools.storage.get_all_agents", return_value=[agent]), patch(
            "core.native_tools.storage.get_supervisor_config",
            return_value={"delegation": {"externalWorkers": []}},
        ):
            command = delegation_broker.func(
                mode="dispatch",
                tasks=[
                    {
                        "taskBriefId": "task-impl",
                        "goal": "Implement the requested patch",
                        "requiredCapabilities": ["software_engineering", "implement"],
                        "behaviorScope": ["workspace_changes"],
                        "writeSet": ["apps/v8-agent-os-engine"],
                        "executionLaneHint": "subagent",
                    }
                ],
                state={"run_id": "run-supervisor-1", "workspace_path": "E:/Projects/v8chat"},
            )

        self.assertIn("parallel_invocations", command.update)
        self.assertRegex(command.update["parallel_invocations"][0]["createdAt"], r"^\d{4}-\d{2}-\d{2}T")
        payload = json.loads(command.update["messages"][0].content)
        self.assertEqual(payload["items"][0]["lane"], "subagent")
        self.assertEqual(payload["items"][0]["targetId"], "engineering-impl")

    def test_delegation_broker_parallel_expected_uses_expanded_worker_count(self):
        agent = {
            "id": "engineering-impl",
            "name": "Engineering Implementer",
            "isEnabled": True,
            "description": "Implements bounded code changes.",
            "capabilitySnapshot": {
                "agentClass": "executor",
                "specialistFamily": "engineering",
                "domainTags": ["software_engineering"],
                "operationCapabilities": ["implement", "workspace_changes"],
                "artifactCapabilities": ["apps/v8-agent-os-engine"],
            },
        }

        with patch("core.native_tools.storage.get_all_agents", return_value=[agent]), patch(
            "core.native_tools.storage.get_supervisor_config",
            return_value={"delegation": {"externalWorkers": []}},
        ):
            command = delegation_broker.func(
                mode="dispatch",
                tasks=[
                    {
                        "taskBriefId": "task-impl",
                        "goal": "Implement the requested patch",
                        "requiredCapabilities": ["software_engineering", "implement"],
                        "behaviorScope": ["workspace_changes"],
                        "writeSet": ["apps/v8-agent-os-engine"],
                        "executionLaneHint": "subagent",
                    },
                    {
                        "taskBriefId": "task-worker-missing",
                        "goal": "Run an unavailable external worker",
                        "requiredCapabilities": ["nonexistent_external_worker"],
                        "executionLaneHint": "external_worker",
                    },
                ],
                state={"run_id": "run-supervisor-1", "workspace_path": "E:/Projects/v8chat"},
            )

        invocation = command.update["parallel_invocations"][0]
        self.assertEqual(invocation["expected"], 2)
        self.assertEqual(invocation["dispatchedSubagentCount"], 1)
        self.assertEqual(invocation["immediateResultCount"], 1)

    def test_delegation_broker_observe_parses_worker_result_block(self):
        descriptor = {
            "id": "research-writer-worker",
            "name": "Research / Writing Worker",
            "description": "External research worker",
            "enabled": True,
            "workerType": "research_writer",
            "capabilitySnapshot": {
                "agentClass": "external_worker",
                "domainTags": ["research", "writing"],
                "operationCapabilities": ["research", "write"],
                "externalWorkerSuitability": "high",
            },
            "launchProfile": {
                "commandTemplate": "worker --task {task_brief_b64}",
                "cwdPolicy": "inherit_workspace",
                "envPassThrough": [],
                "startupTimeoutSeconds": 10,
            },
            "sessionMode": "interactive",
            "allowedSideEffects": ["workspace_write"],
            "resultSchema": {
                "type": "v8_worker_result_v1",
                "markers": ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"],
            },
        }
        result_block = (
            "<V8_WORKER_RESULT>"
            + json.dumps(
                {
                    "summary": "Draft completed",
                    "localSelfCheck": "Checked structure and evidence coverage.",
                    "artifactRefs": [{"kind": "file", "path": "E:/Projects/v8chat/out.md"}],
                    "acceptanceHint": "Review draft tone and references before publishing.",
                },
                ensure_ascii=False,
            )
            + "</V8_WORKER_RESULT>"
        )

        with patch(
            "core.native_tools.storage.get_supervisor_config",
            return_value={"delegation": {"externalWorkers": [descriptor]}},
        ), patch(
            "core.native_tools.command_session_broker.func",
            return_value=json.dumps(
                {
                    "ok": True,
                    "mode": "observe",
                    "kind": "command_session",
                    "commandId": "cmd-ext-2",
                    "sessionId": "cmd-ext-2",
                    "runId": "run-ext-2",
                    "state": "completed",
                    "summary": "worker finished",
                    "deltaText": result_block,
                    "recommendedNextAction": "none",
                }
            ),
        ):
            command = delegation_broker.func(
                mode="observe",
                delegation_id="external::cmd-ext-2::task-draft::research-writer-worker",
                state={"run_id": "run-supervisor-2"},
            )

        payload = json.loads(command.update["messages"][0].content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["items"][0]["lane"], "external_worker")
        self.assertEqual(payload["items"][0]["targetId"], "research-writer-worker")
        self.assertTrue(payload["items"][0]["resultSchemaMatched"])
        self.assertEqual(payload["items"][0]["localSelfCheck"], "Checked structure and evidence coverage.")
        self.assertEqual(payload["items"][0]["artifactRefs"][0]["path"], "E:/Projects/v8chat/out.md")
        self.assertEqual(payload["items"][0]["acceptanceHint"], "Review draft tone and references before publishing.")

    def test_default_external_workers_include_disabled_claude_code_template(self):
        descriptors = default_external_worker_descriptors()
        claude = next((item for item in descriptors if item.get("id") == "claude-code-worker"), None)

        self.assertIsNotNone(claude)
        self.assertFalse(claude["enabled"])
        self.assertEqual(claude["workerType"], "claude_code")
        self.assertIn("claude -p --permission-mode acceptEdits", claude["launchProfile"]["commandTemplate"])
        self.assertEqual(claude["resultSchema"]["markers"], ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"])

    def test_external_worker_normalization_appends_claude_code_without_overwriting_existing_items(self):
        legacy = {
            "id": "legacy-worker",
            "name": "Legacy Worker",
            "enabled": True,
            "workerType": "legacy",
            "launchProfile": {"commandTemplate": "legacy --task {task_brief_b64}"},
        }

        normalized = normalize_external_worker_descriptors([legacy])

        self.assertEqual(normalized[0]["id"], "legacy-worker")
        self.assertTrue(normalized[0]["enabled"])
        self.assertTrue(any(item["id"] == "claude-code-worker" and item["enabled"] is False for item in normalized))

    def test_render_external_worker_command_uses_workspace_for_inherit_policy(self):
        descriptor = default_external_worker_descriptors()[0]
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir).resolve()
            binding = WorkspaceBinding(
                runtime_kind="chat",
                workspace_id="",
                project_id="",
                active_workspace_root=workspace,
                main_workspace_root=workspace,
                source="main_workspace",
                uses_scoped_workspace=False,
                is_scoped_override=False,
                trust_state="trusted",
                trust_source="test_explicit_trust",
                side_effects_allowed=True,
            )
            with patch("core.workspace_capability.build_workspace_binding", return_value=binding):
                command = render_external_worker_command(
                    descriptor=descriptor,
                    task_brief={
                        "taskBriefId": "task-claude",
                        "goal": "Implement a focused patch",
                        "writeSet": ["apps/example.py"],
                    },
                    workspace_path=str(workspace),
                )

        self.assertIn("claude -p --permission-mode", command)
        self.assertIn("acceptEdits", command)
        self.assertIn("<V8_WORKER_RESULT>", command)
        self.assertIn("Read task brief JSON from file", command)
        self.assertIn(".v8-agent-os/external-workers/task-claude/task_brief.json", command)
        self.assertTrue(command.startswith("cd /d ") or command.startswith("cd "))
        self.assertIn(str(workspace), command)


if __name__ == "__main__":
    unittest.main()
