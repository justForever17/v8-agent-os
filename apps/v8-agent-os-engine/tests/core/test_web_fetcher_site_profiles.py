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
        </div>
        <aside id="sidebar">hot network questions</aside>
      </main>
    </body></html>
    """

    text = web_fetcher._extract_main_text(_soup(html), "https://stackoverflow.com/questions/1/how-do-i-parse-html")

    assert "How do I parse HTML?" in text
    assert "Question body." in text
    assert "Accepted answer body." in text
    assert "noisy comments" not in text
    assert "hot network questions" not in text


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
