from __future__ import annotations

import json
from unittest.mock import patch

from bs4 import BeautifulSoup

from core.tools import web_fetcher


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


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


def test_p0_site_profiles_are_registered() -> None:
    urls = (
        "https://zh.wikipedia.org/wiki/Python",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript",
        "https://learn.microsoft.com/en-us/dotnet/csharp/",
        "https://github.com/owner/repo/releases/tag/v1.0.0",
        "https://github.com/owner/repo/issues/12",
        "https://arxiv.org/abs/1706.03762",
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
        "https://www.tradingview.com/symbols/NASDAQ-AAPL/": "market_data_secondary",
        "https://www.coingecko.com/en/coins/bitcoin": "crypto_aggregate_secondary",
        "https://defillama.com/protocol/example": "crypto_aggregate_secondary",
    }

    for url, catalog_id in cases.items():
        hints = web_fetcher._search_result_quality_hints(url)

        assert hints["catalogSourceId"] == catalog_id
        assert hints["authorityTier"] == "secondary"
        assert hints["tier"] == "secondary"
        assert "secondary_source_hint" in hints["signals"]


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
