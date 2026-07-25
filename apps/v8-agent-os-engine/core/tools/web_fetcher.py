from __future__ import annotations

import json
import mimetypes
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import os
from pathlib import Path
import ssl
import tempfile
import time
from typing import Annotated, Any, Dict, List, Literal
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup
import certifi
from langchain_core.tools import InjectedToolCallId, tool
import requests
from scrapling.core.storage import SQLiteStorageSystem
from scrapling.parser import Selector

from core.agent_browser_profile import (
    agent_browser_profile_allowed_for_url,
    agent_browser_profile_summary,
    configured_agent_browser_profile_dir,
    debug_port_owned_by_profile,
)
from core.source_provider_registry import get_source_provider_capabilities, get_source_router_defaults
from core.system_base import get_web_fetch_config
from core.tools.native.tool_governance import log_safety_review_auto_approved, should_auto_approve_safety_review
from core.storage import storage
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import safety_guardian


WebFetchMode = Literal["auto", "static", "dynamic", "stealth"]
WebExtractMode = Literal["article", "links", "metadata", "media", "raw_html", "ui_snapshot"]
WebRefererMode = Literal["none", "google", "custom"]
WebFetchIntent = Literal["auto", "read", "extract", "search"]
WebSearchEngine = Literal[
    "auto",
    "metaso",
    "bing",
    "google",
    "baidu",
    "duckduckgo",
    "brave",
    "tavily",
    "exa",
    "jina",
    "firecrawl",
    "bocha",
    "searxng",
    "perplexity",
]
WebSearchVertical = Literal["all", "web", "document", "academic", "image", "video", "podcast"]
WEB_CONTAINER_SELECTOR = "main, article, [role='main'], body"
MAX_SELECTOR_CANDIDATES = 12
DEFAULT_CONTAINER_SELECTORS = (
    "main",
    "article",
    "[role='main']",
    "#main",
    "#content",
    "#main-content",
    ".main",
    ".content",
    ".main-content",
    ".article-content",
    ".post-content",
    ".entry-content",
    "body",
)
EXTRACT_CONTAINER_SELECTORS: dict[str, tuple[str, ...]] = {
    "article": (
        "article",
        "main article",
        "[itemprop='articleBody']",
        ".article-content",
        ".post-content",
        ".entry-content",
    ),
    "links": (
        "main",
        "nav",
        "article",
        ".content",
    ),
    "metadata": (),
    "raw_html": (
        "main",
        "article",
        "[role='main']",
        "body",
    ),
    "ui_snapshot": (
        "main",
        "article",
        "[role='main']",
        "body",
    ),
    "media": (
        "main",
        "article",
        ".gallery",
        ".content",
        "body",
    ),
}
FINANCE_DISCLOSURE_SITE_PROFILE: dict[str, Any] = {
    "description": "Finance regulator, exchange, company filing and announcement pages: keep title, date, disclosure body and visible notice text; skip navigation, search, sidebars and recommendation chrome.",
    "extracts": {
        "article": {
            "containerSelectors": (
                "main",
                "article",
                "#content",
                "#main",
                "#mainContent",
                ".content",
                ".article",
                ".article-content",
                ".detail",
                ".detail-content",
                ".announcement",
                ".notice",
                ".disclosure",
                "body",
            ),
            "articleSelectors": (
                "main h1",
                "article h1",
                "h1",
                ".title",
                ".article-title",
                ".main-title",
                ".content-title",
                ".announcement-title",
                ".notice-title",
                ".date",
                ".time",
                ".publish-time",
                ".article-content",
                ".detail-content",
                ".content",
                ".detail",
                ".announcement",
                ".notice",
                ".disclosure",
                "#content",
                "#mainContent",
                "article",
            ),
            "removeSelectors": (
                ".sidebar",
                ".side",
                ".toc",
                ".breadcrumb",
                ".breadcrumbs",
                ".search",
                ".search-box",
                ".menu",
                ".nav",
                ".pagination",
                ".related",
                ".recommend",
                ".advertisement",
                ".advertise",
                ".ads",
                ".ad",
                ".share",
                ".toolbar",
                ".cookie",
                ".login",
                ".popup",
                "aside",
                "nav",
                "footer",
                "header",
                "form",
                "button",
            ),
            "skipMarkerTokens": (
                "sidebar",
                "side",
                "catalog",
                "breadcrumb",
                "search",
                "menu",
                "pagination",
                "related",
                "recommend",
                "advert",
                "share",
                "toolbar",
                "cookie",
                "login",
                "popup",
            ),
        }
    },
}
CRYPTO_INFO_SITE_PROFILE: dict[str, Any] = {
    "description": "Crypto exchange, market data and announcement pages: keep announcement/API/market explanation body; skip trading widgets, login, promotions and recommendation chrome.",
    "extracts": {
        "article": {
            "containerSelectors": (
                "main",
                "article",
                "#content",
                ".content",
                ".article",
                ".article-content",
                ".markdown",
                ".markdown-body",
                ".prose",
                ".announcement",
                "body",
            ),
            "articleSelectors": (
                "main h1",
                "article h1",
                "h1",
                ".title",
                ".article-title",
                ".announcement-title",
                ".article-content",
                ".content",
                ".markdown",
                ".markdown-body",
                ".prose",
                ".announcement",
                "article",
            ),
            "removeSelectors": (
                ".trade",
                ".trading",
                ".chart",
                ".orderbook",
                ".order-book",
                ".ticker-tape",
                ".login",
                ".signup",
                ".download",
                ".app-download",
                ".promotion",
                ".promo",
                ".banner",
                ".related",
                ".recommend",
                ".sidebar",
                ".toc",
                ".breadcrumb",
                ".cookie",
                "aside",
                "nav",
                "footer",
                "header",
                "form",
                "button",
            ),
            "skipMarkerTokens": (
                "trade",
                "trading",
                "orderbook",
                "order-book",
                "ticker",
                "login",
                "signup",
                "download",
                "promotion",
                "promo",
                "banner",
                "related",
                "recommend",
                "sidebar",
                "cookie",
            ),
        }
    },
}
ONCHAIN_EXPLORER_SITE_PROFILE: dict[str, Any] = {
    "description": "Blockchain explorer pages: keep visible transaction/token/address summary and status fields; skip ads, menus, charts and footer chrome.",
    "extracts": {
        "article": {
            "containerSelectors": (
                "main",
                "#content",
                ".content",
                ".container",
                ".card",
                ".card-body",
                ".overview",
                "body",
            ),
            "articleSelectors": (
                "main h1",
                "h1",
                ".title",
                ".card-header",
                ".card-body",
                ".overview",
                ".hash-tag",
                ".u-label",
                ".text-muted",
                ".content",
                "#content",
            ),
            "removeSelectors": (
                ".navbar",
                ".sidebar",
                ".dropdown-menu",
                ".ads",
                ".ad",
                ".advertisement",
                ".chart",
                ".sponsored",
                ".cookie",
                ".modal",
                ".popup",
                "aside",
                "nav",
                "footer",
                "header",
                "form",
                "button",
            ),
            "skipMarkerTokens": (
                "navbar",
                "sidebar",
                "dropdown",
                "advert",
                "sponsor",
                "cookie",
                "modal",
                "popup",
            ),
        }
    },
}
SHOPPING_PRODUCT_SITE_PROFILE: dict[str, Any] = {
    "description": "Large shopping product pages: keep visible product summary text such as title, price text, availability, seller/shop, specs and rating summary; skip navigation, ads, recommendations, comments and Q&A.",
    "extracts": {
        "article": {
            "containerSelectors": (
                "main",
                "#dp",
                "#centerCol",
                "#ppd",
                "#item",
                "#itemInfo",
                ".product",
                ".product-detail",
                ".product-page",
                ".goods-detail",
                ".item-detail",
                "body",
            ),
            "articleSelectors": (
                "main h1",
                "h1",
                "#productTitle",
                ".product-title",
                ".prod-ProductTitle",
                ".sku-name",
                ".tb-main-title",
                ".p-name",
                ".goods-name",
                ".price",
                ".a-price",
                ".p-price",
                ".tm-price",
                ".tb-rmb-num",
                ".summary-price",
                "[class*='price']",
                "#availability",
                ".availability",
                ".stock",
                "[class*='stock']",
                ".delivery",
                "[class*='delivery']",
                ".seller",
                ".seller-name",
                ".store",
                ".shop",
                ".shopName",
                ".specs",
                ".parameters",
                ".Ptable",
                ".item-props",
                ".product-params",
                ".a-icon-alt",
                ".rating",
                "[class*='rating']",
            ),
            "removeSelectors": (
                ".recommend",
                ".recommendation",
                ".related",
                ".sponsored",
                ".advertisement",
                ".advertise",
                ".ads",
                ".ad",
                ".comments",
                ".comment",
                ".reviews",
                ".review-list",
                ".qa",
                ".question",
                ".ask",
                ".navbar",
                ".breadcrumb",
                ".breadcrumbs",
                ".sidebar",
                ".login",
                ".popup",
                ".modal",
                ".share",
                ".toolbar",
                ".footer",
                ".header",
                "aside",
                "nav",
                "footer",
                "header",
                "form",
                "button",
            ),
            "skipMarkerTokens": (
                "recommend",
                "related",
                "sponsored",
                "advert",
                "comment",
                "review-list",
                "qa",
                "question",
                "ask",
                "navbar",
                "breadcrumb",
                "sidebar",
                "login",
                "popup",
                "modal",
                "share",
                "toolbar",
            ),
        }
    },
}
OPENREVIEW_SITE_PROFILE: dict[str, Any] = {
    "description": "OpenReview paper pages: keep paper title, authors, abstract, TL;DR, keywords and venue/decision metadata; skip discussion threads and site chrome.",
    "extracts": {
        "article": {
            "containerSelectors": (
                "main",
                "#content",
                ".forum-container",
                ".forum",
                ".note",
                ".note_content",
                ".note-content",
                "body",
            ),
            "articleSelectors": (
                "main h1",
                "h1",
                ".forum-title",
                ".note_content_title",
                ".note-content-title",
                "[data-field-name='title']",
                ".authors",
                ".forum-authors",
                "[data-field-name='authors']",
                ".abstract",
                "[data-field-name='abstract']",
                ".tldr",
                ".tl-dr",
                "[class*='tldr']",
                "[data-field-name='TL;DR']",
                ".keywords",
                "[data-field-name='keywords']",
                ".venue",
                ".decision",
                ".date",
            ),
            "removeSelectors": (
                ".note-replies",
                ".reply",
                ".comment",
                ".review",
                ".reviews",
                ".invitation",
                ".forum-note-actions",
                ".note-actions",
                ".paperlist",
                ".tabs",
                ".navigation",
                ".sidebar",
                "aside",
                "nav",
                "footer",
                "header",
                "form",
                "button",
            ),
            "skipMarkerTokens": (
                "reply",
                "comment",
                "review",
                "invitation",
                "action",
                "paperlist",
                "navigation",
                "sidebar",
            ),
        }
    },
}
ACL_ANTHOLOGY_SITE_PROFILE: dict[str, Any] = {
    "description": "ACL Anthology paper pages: keep paper title, authors, venue/date, abstract and citation metadata; skip proceedings navigation and related chrome.",
    "extracts": {
        "article": {
            "containerSelectors": (
                "main",
                "article",
                "#main-container",
                "#content",
                ".acl-paper",
                ".paper-details",
                ".container",
                "body",
            ),
            "articleSelectors": (
                "main h1",
                "article h1",
                "h1",
                "#title",
                ".acl-paper-title",
                ".paper-title",
                ".lead",
                ".authors",
                ".acl-paper-authors",
                ".paper-authors",
                ".venue",
                ".acl-venue",
                ".date",
                ".abstract",
                "#abstract",
                ".acl-abstract",
                ".paper-abstract",
                ".citation",
                ".bibtex",
                ".doi",
            ),
            "removeSelectors": (
                ".navbar",
                ".breadcrumb",
                ".breadcrumbs",
                ".acl-paper-links",
                ".card-footer",
                ".list-group",
                ".related",
                ".recommend",
                ".sidebar",
                ".toc",
                "aside",
                "nav",
                "footer",
                "header",
                "form",
                "button",
            ),
            "skipMarkerTokens": (
                "navbar",
                "breadcrumb",
                "paper-links",
                "related",
                "recommend",
                "sidebar",
                "toc",
            ),
        }
    },
}
PUBMED_SITE_PROFILE: dict[str, Any] = {
    "description": "PubMed article pages: keep title, authors, journal/date, abstract, DOI/PMID, keywords and publication types; skip search, sidebars and related articles.",
    "extracts": {
        "article": {
            "containerSelectors": (
                "main",
                "#article-page",
                ".article-page",
                "#enc-abstract",
                ".abstract",
                ".article-details",
                "body",
            ),
            "articleSelectors": (
                "main h1",
                "h1.heading-title",
                ".heading-title",
                ".authors-list",
                ".cit",
                ".abstract",
                "#enc-abstract",
                ".abstract-content",
                ".keywords-section",
                ".publication-types",
                ".identifiers",
                ".doi",
                ".pmid",
            ),
            "removeSelectors": (
                ".search-results",
                ".results-amount",
                ".similar-articles",
                ".references",
                ".timeline",
                ".actions-bar",
                ".side-bar",
                ".sidebar",
                ".related",
                ".recommend",
                ".social-share",
                ".ncbi-alerts",
                "aside",
                "nav",
                "footer",
                "header",
                "form",
                "button",
            ),
            "skipMarkerTokens": (
                "search",
                "similar",
                "reference",
                "timeline",
                "action",
                "sidebar",
                "related",
                "recommend",
                "share",
            ),
        }
    },
}
ACM_DIGITAL_LIBRARY_SITE_PROFILE: dict[str, Any] = {
    "description": "ACM Digital Library paper pages: keep title, authors, venue/date, abstract, DOI and keywords; skip login, metrics, recommendations and reference chrome.",
    "extracts": {
        "article": {
            "containerSelectors": (
                "main",
                "article",
                "#pb-page-content",
                ".article",
                ".citation",
                ".issue-item",
                ".abstractSection",
                "body",
            ),
            "articleSelectors": (
                "main h1",
                "article h1",
                "h1",
                ".citation__title",
                ".issue-item__title",
                ".citation__authors",
                ".issue-item__authors",
                ".citation__publication-date",
                ".publication-title",
                ".abstractInFull",
                ".abstractSection",
                "#abstract",
                ".article__abstract",
                ".keywords-section",
                ".doi",
            ),
            "removeSelectors": (
                ".article__references",
                ".references",
                ".reference-list",
                ".relatedContent",
                ".recommendations",
                ".metrics",
                ".tabbed-nav",
                ".access-options",
                ".sign-in",
                ".login",
                ".ads",
                ".advertisement",
                ".sidebar",
                ".toc",
                "aside",
                "nav",
                "footer",
                "header",
                "form",
                "button",
            ),
            "skipMarkerTokens": (
                "references",
                "related",
                "recommend",
                "metrics",
                "tabbed",
                "access",
                "sign-in",
                "login",
                "advert",
                "sidebar",
                "toc",
            ),
        }
    },
}
PAPERS_WITH_CODE_SITE_PROFILE: dict[str, Any] = {
    "description": "Papers With Code paper and benchmark pages: keep paper summary, tasks, datasets, methods, code and leaderboard text; skip comments, recommendations and navigation.",
    "extracts": {
        "article": {
            "containerSelectors": (
                "main",
                "article",
                ".paper",
                ".paper-detail",
                ".benchmark-page",
                ".content",
                "body",
            ),
            "articleSelectors": (
                "main h1",
                "article h1",
                "h1",
                ".paper-title",
                ".item-title",
                ".paper-authors",
                ".authors",
                ".paper-abstract",
                ".abstract",
                ".tasks",
                ".task",
                ".datasets",
                ".dataset",
                ".methods",
                ".method",
                ".leaderboard",
                ".sota-table",
                ".results",
                ".code-table",
            ),
            "removeSelectors": (
                ".comments",
                ".comment",
                ".discussion",
                ".related-papers",
                ".recommend",
                ".sidebar",
                ".newsletter",
                ".modal",
                ".popup",
                ".login",
                "aside",
                "nav",
                "footer",
                "header",
                "form",
                "button",
            ),
            "skipMarkerTokens": (
                "comment",
                "discussion",
                "related",
                "recommend",
                "sidebar",
                "newsletter",
                "modal",
                "popup",
                "login",
            ),
        }
    },
}
HACKER_NEWS_SITE_PROFILE: dict[str, Any] = {
    "description": "Hacker News item pages: keep story title, points, age, comment count and readable comments; skip navigation, voting chrome, reply forms and footer links.",
    "extracts": {
        "article": {
            "allowTableLayout": True,
            "containerSelectors": (
                "body",
                "center",
                "table.itemlist",
                ".itemlist",
                "table",
            ),
            "articleSelectors": (
                "tr.athing .titleline",
                "tr.athing .title",
                "tr.athing + tr .subtext",
                "td.subtext",
                "tr.comtr .comhead",
                "tr.comtr .commtext",
                ".commtext",
            ),
            "removeSelectors": (
                ".votearrow",
                ".reply",
                ".pagetop",
                ".yclinks",
                ".morelink",
                ".hnmore",
                "tr.spacer",
                "form",
                "textarea",
                "input",
                "button",
            ),
            "skipMarkerTokens": (
                "votearrow",
                "reply",
                "pagetop",
                "yclinks",
                "morelink",
                "hnmore",
                "spacer",
            ),
        }
    },
}
BUILTIN_WEB_FETCH_SITE_PROFILES: dict[str, dict[str, Any]] = {
    "baike.baidu.com": {
        "description": "Baidu Baike lemma pages: prefer lemma summary plus body paragraphs; skip catalog, relation/sidebar/module/table chrome.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "body",
                    "main",
                    ".main-content",
                    ".J-lemma-content",
                ),
                "articleSelectors": (
                    ".lemma-summary",
                    ".lemmaWgt-lemmaSummary",
                    "[class*='lemmaSummary']",
                    ".para",
                    "[class*='para-title']",
                    "[class*='lemma-content'] .para",
                    ".J-lemma-content .para",
                ),
                "removeSelectors": (
                    ".lemma-catalog",
                    ".lemmaWgt-lemmaCatalog",
                    ".catalog-list",
                    "[class*='catalog']",
                    ".basic-info",
                    "table",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    ".side-content",
                    "[class*='side']",
                    ".lemmaWgt-relation",
                    "[class*='relation']",
                    "[class*='module']",
                    ".lemmaWgt-promotion-vbaike",
                    ".top-tool",
                    ".share",
                    ".toolbar",
                    ".album-list",
                ),
                "skipMarkerTokens": (
                    "catalog",
                    "relation",
                    "module",
                    "side-content",
                    "basic-info",
                    "toolbar",
                    "album-list",
                    "top-tool",
                    "promotion",
                ),
            }
        },
    },
    "wikipedia.org": {
        "description": "Wikipedia article pages: keep title, lead, headings, paragraphs and lists; skip navigation, edit links, reference chrome and category boxes.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    "article",
                    "#content",
                    ".mw-body",
                    ".mw-parser-output",
                    "body",
                ),
                "articleSelectors": (
                    "#firstHeading",
                    ".mw-parser-output > .shortdescription",
                    ".mw-parser-output > p",
                    ".mw-parser-output > h2",
                    ".mw-parser-output > h3",
                    ".mw-parser-output > h4",
                    ".mw-parser-output > ul",
                    ".mw-parser-output > ol",
                    ".mw-parser-output > blockquote",
                    ".mw-parser-output > pre",
                ),
                "removeSelectors": (
                    "#toc",
                    ".toc",
                    ".vector-toc",
                    ".mw-editsection",
                    ".mw-empty-elt",
                    ".navbox",
                    ".vertical-navbox",
                    ".metadata",
                    ".ambox",
                    ".hatnote",
                    ".reflist",
                    ".reference",
                    ".mw-references-wrap",
                    ".catlinks",
                    ".printfooter",
                    ".noprint",
                    ".sistersitebox",
                    ".side-box",
                    ".mw-footer",
                    ".vector-page-toolbar",
                    ".vector-header",
                    ".mw-indicators",
                    "table",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                ),
            }
        },
    },
    "developer.mozilla.org": {
        "description": "MDN documentation pages: keep main reference/tutorial body; skip menus, breadcrumbs, sidebars, toc, feedback and newsletter chrome.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    "article",
                    ".main-page-content",
                    ".section-content",
                    "#content",
                    "body",
                ),
                "articleSelectors": (
                    "main h1",
                    ".main-page-content",
                    "article",
                    ".section-content",
                    "#content",
                ),
                "removeSelectors": (
                    ".sidebar",
                    ".toc",
                    ".table-of-contents",
                    ".breadcrumb",
                    ".breadcrumbs",
                    ".document-toc",
                    ".metadata",
                    ".page-footer",
                    ".newsletter",
                    ".feedback",
                    ".notecard.deprecated",
                    ".language-menu",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    "form",
                ),
            }
        },
    },
    "learn.microsoft.com": {
        "description": "Microsoft Learn pages: keep article content and reference body; skip left rail, toc, feedback, rating and contribution chrome.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    "article",
                    "[data-bi-name='content']",
                    ".content",
                    ".mainContainer",
                    "body",
                ),
                "articleSelectors": (
                    "main h1",
                    "main article",
                    "article",
                    "[data-bi-name='content']",
                    ".content",
                    ".mainContainer",
                ),
                "removeSelectors": (
                    "#left-container",
                    "#right-container",
                    ".left-container",
                    ".right-container",
                    ".toc",
                    ".table-of-contents",
                    ".breadcrumb",
                    ".breadcrumbs",
                    ".feedback-section",
                    ".feedback-verbatim",
                    ".rating",
                    ".contributors",
                    ".metadata",
                    ".page-metadata",
                    ".is-hidden",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    "form",
                ),
            }
        },
    },
    "github.com": {
        "description": "GitHub repository, README, release and issue pages: keep markdown body and issue/release content; skip repository chrome while preserving repository popularity metadata.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    "article",
                    "[data-testid='readme']",
                    ".repository-content",
                    ".js-issue-title",
                    ".js-issue-body",
                    "body",
                ),
                "articleSelectors": (
                    "[data-testid='readme'] article.markdown-body",
                    "[data-testid='readme'] article",
                    "article.markdown-body",
                    ".repository-content .markdown-body",
                    ".Box-body .markdown-body",
                    ".release-entry .markdown-body",
                    ".release .markdown-body",
                    ".markdown-body",
                    ".js-issue-title",
                    ".js-issue-body .markdown-body",
                    "[data-testid='issue-body']",
                    ".js-comment-body",
                    ".comment-body",
                ),
                "removeSelectors": (
                    ".Header",
                    ".AppHeader",
                    ".UnderlineNav",
                    ".reponav",
                    ".file-navigation",
                    ".file-header",
                    ".js-file-line-container",
                    ".js-reactions-container",
                    ".timeline-comment-actions",
                    ".social-count",
                    ".tooltipped",
                    ".octicon",
                    "clipboard-copy",
                    "relative-time",
                    "include-fragment",
                    "details-menu",
                    "nav",
                    "footer",
                    "header",
                    "form",
                    "button",
                ),
            }
        },
    },
    "arxiv.org": {
        "description": "arXiv abstract pages: keep title, authors, abstract, subjects and submission history; skip global navigation and service chrome.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "#abs",
                    "main",
                    "article",
                    "body",
                ),
                "articleSelectors": (
                    "#abs h1.title",
                    "#abs .authors",
                    "#abs blockquote.abstract",
                    "#abs .dateline",
                    "#abs .subjects",
                    "#abs .submission-history",
                    "#abs .comments",
                    "#abs .msc-classes",
                    "#abs .acm-classes",
                ),
                "removeSelectors": (
                    ".extra-services",
                    ".full-text",
                    ".leftcolumn",
                    ".mobile-submission-download",
                    ".submission-history-extra",
                    ".browse",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    "form",
                ),
            }
        },
    },
    "openreview.net": OPENREVIEW_SITE_PROFILE,
    "aclanthology.org": ACL_ANTHOLOGY_SITE_PROFILE,
    "pubmed.ncbi.nlm.nih.gov": PUBMED_SITE_PROFILE,
    "dl.acm.org": ACM_DIGITAL_LIBRARY_SITE_PROFILE,
    "paperswithcode.com": PAPERS_WITH_CODE_SITE_PROFILE,
    "news.ycombinator.com": HACKER_NEWS_SITE_PROFILE,
    "npmjs.com": {
        "description": "npm package pages: keep package summary and README; skip package chrome, tabs, install widgets and sidebars.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    "article",
                    "[data-testid='package-readme']",
                    ".markdown",
                    "body",
                ),
                "articleSelectors": (
                    "main h1",
                    "[data-testid='package-name']",
                    "[data-testid='package-description']",
                    "[data-testid='package-readme']",
                    "article",
                    ".markdown",
                ),
                "removeSelectors": (
                    "[data-testid='tabs']",
                    "[data-testid='sidebar']",
                    "[data-testid='install-command']",
                    ".package__sidebar",
                    ".sidebar",
                    ".toc",
                    ".breadcrumb",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    "form",
                    "button",
                ),
            }
        },
    },
    "pypi.org": {
        "description": "PyPI project pages: keep package header and project description; skip navigation, sidebars and release history chrome.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    ".package-header",
                    ".project-description",
                    "#description",
                    "body",
                ),
                "articleSelectors": (
                    ".package-header",
                    ".project-description",
                    "#description",
                    ".description",
                    ".markdown",
                    "main",
                ),
                "removeSelectors": (
                    ".sidebar-section",
                    ".vertical-tabs",
                    ".release-timeline",
                    ".breadcrumb",
                    ".sponsors",
                    ".meta",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    "form",
                    "button",
                ),
            }
        },
    },
    "stackoverflow.com": {
        "description": "Stack Overflow question pages: keep question title, accepted answer and answer bodies; skip votes, comments, menus, ads and sidebars.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    "#mainbar",
                    "#question",
                    "#answers",
                    "body",
                ),
                "articleSelectors": (
                    "#question-header h1",
                    "#question .js-post-body",
                    "#answers .answer.accepted-answer .js-post-body",
                    "#answers .answer[data-is-accepted-answer='true'] .js-post-body",
                    "#answers .answer .js-post-body",
                    ".js-post-body",
                ),
                "removeSelectors": (
                    ".comments",
                    ".js-comments-container",
                    ".post-menu",
                    ".votecell",
                    ".js-voting-container",
                    ".js-post-menu",
                    ".js-post-notice",
                    ".s-sidebarwidget",
                    "#sidebar",
                    "#left-sidebar",
                    ".question-status",
                    ".everyonelovesstackoverflow",
                    ".js-consent-banner",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    "form",
                    "button",
                ),
            }
        },
    },
    "zhihu.com": {
        "description": "Zhihu article/question pages: clean readable Chinese content only; treat as variable-quality community evidence, not authority.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    "article",
                    ".Question-main",
                    ".Post-main",
                    ".RichContent",
                    ".Post-RichText",
                    "body",
                ),
                "articleSelectors": (
                    ".QuestionHeader-title",
                    ".QuestionRichText",
                    ".Post-Title",
                    ".Post-RichText",
                    ".AnswerItem .RichContent-inner",
                    ".RichContent-inner",
                    ".RichText",
                ),
                "removeSelectors": (
                    ".ContentItem-actions",
                    ".RichContent-actions",
                    ".Question-sideColumn",
                    ".Recommendations-Main",
                    ".Sticky",
                    ".Footer",
                    ".Reward",
                    ".VoteButton",
                    ".AuthorInfo",
                    ".Comment",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    "form",
                    "button",
                ),
            }
        },
    },
    "juejin.cn": {
        "description": "Juejin article pages: clean article body and code blocks; keep as low-confidence Chinese community evidence.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    "article",
                    ".article",
                    ".article-content",
                    ".markdown-body",
                    "body",
                ),
                "articleSelectors": (
                    "h1.article-title",
                    ".article-title",
                    ".markdown-body",
                    ".article-content",
                    ".main-area article",
                    "article",
                ),
                "removeSelectors": (
                    ".sidebar",
                    ".author-info-block",
                    ".recommended-area",
                    ".catalog",
                    ".comment",
                    ".action-bar",
                    ".extension",
                    ".tag-list",
                    ".app-download-sidebar-block",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    "form",
                    "button",
                ),
            }
        },
    },
    "csdn.net": {
        "description": "CSDN blog pages: clean post title and article body; keep as low-confidence Chinese community evidence.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    "article",
                    "#mainBox",
                    "#article_content",
                    "#content_views",
                    ".blog-content-box",
                    "body",
                ),
                "articleSelectors": (
                    "h1.title-article",
                    ".title-article",
                    "#article_content",
                    "#content_views",
                    ".article_content",
                    ".htmledit_views",
                    ".blog-content-box article",
                ),
                "removeSelectors": (
                    ".blog_container_aside",
                    "#asideProfile",
                    "#asideCategory",
                    "#recommendNps",
                    ".recommend-box",
                    ".comment-box",
                    ".toolbar",
                    ".csdn-side-toolbar",
                    ".passport-login-container",
                    ".more-toolbox",
                    ".template-box",
                    ".hide-article-box",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    "form",
                    "button",
                ),
            }
        },
    },
    "cnblogs.com": {
        "description": "CNBlogs post pages: clean post title and body; keep as low-confidence Chinese community evidence.",
        "extracts": {
            "article": {
                "containerSelectors": (
                    "main",
                    "article",
                    "#topics",
                    "#mainContent",
                    ".post",
                    ".postBody",
                    "body",
                ),
                "articleSelectors": (
                    ".postTitle",
                    ".postTitle a",
                    "#cnblogs_post_body",
                    ".postBody",
                    ".blogpost-body",
                    "#topics .post",
                ),
                "removeSelectors": (
                    "#sideBar",
                    "#sidebar",
                    "#blog-comments-placeholder",
                    "#commentform",
                    ".postDesc",
                    ".catList",
                    "#navigator",
                    "#footer",
                    "#header",
                    ".under-post-card",
                    ".commentform",
                    "aside",
                    "nav",
                    "footer",
                    "header",
                    "form",
                    "button",
                ),
            }
        },
    },
    "sec.gov": FINANCE_DISCLOSURE_SITE_PROFILE,
    "nasdaq.com": FINANCE_DISCLOSURE_SITE_PROFILE,
    "nyse.com": FINANCE_DISCLOSURE_SITE_PROFILE,
    "cboe.com": FINANCE_DISCLOSURE_SITE_PROFILE,
    "sse.com.cn": FINANCE_DISCLOSURE_SITE_PROFILE,
    "szse.cn": FINANCE_DISCLOSURE_SITE_PROFILE,
    "cninfo.com.cn": FINANCE_DISCLOSURE_SITE_PROFILE,
    "csrc.gov.cn": FINANCE_DISCLOSURE_SITE_PROFILE,
    "csindex.com.cn": FINANCE_DISCLOSURE_SITE_PROFILE,
    "eastmoney.com": FINANCE_DISCLOSURE_SITE_PROFILE,
    "finance.sina.com.cn": FINANCE_DISCLOSURE_SITE_PROFILE,
    "finance.yahoo.com": FINANCE_DISCLOSURE_SITE_PROFILE,
    "investing.com": FINANCE_DISCLOSURE_SITE_PROFILE,
    "binance.com": CRYPTO_INFO_SITE_PROFILE,
    "coinbase.com": CRYPTO_INFO_SITE_PROFILE,
    "okx.com": CRYPTO_INFO_SITE_PROFILE,
    "kraken.com": CRYPTO_INFO_SITE_PROFILE,
    "coingecko.com": CRYPTO_INFO_SITE_PROFILE,
    "coinmarketcap.com": CRYPTO_INFO_SITE_PROFILE,
    "defillama.com": CRYPTO_INFO_SITE_PROFILE,
    "etherscan.io": ONCHAIN_EXPLORER_SITE_PROFILE,
    "bscscan.com": ONCHAIN_EXPLORER_SITE_PROFILE,
    "polygonscan.com": ONCHAIN_EXPLORER_SITE_PROFILE,
    "solscan.io": ONCHAIN_EXPLORER_SITE_PROFILE,
    "mempool.space": ONCHAIN_EXPLORER_SITE_PROFILE,
    "amazon.com": SHOPPING_PRODUCT_SITE_PROFILE,
    "ebay.com": SHOPPING_PRODUCT_SITE_PROFILE,
    "walmart.com": SHOPPING_PRODUCT_SITE_PROFILE,
    "bestbuy.com": SHOPPING_PRODUCT_SITE_PROFILE,
    "target.com": SHOPPING_PRODUCT_SITE_PROFILE,
    "jd.com": SHOPPING_PRODUCT_SITE_PROFILE,
    "tmall.com": SHOPPING_PRODUCT_SITE_PROFILE,
    "taobao.com": SHOPPING_PRODUCT_SITE_PROFILE,
    "pinduoduo.com": SHOPPING_PRODUCT_SITE_PROFILE,
    "1688.com": SHOPPING_PRODUCT_SITE_PROFILE,
    "suning.com": SHOPPING_PRODUCT_SITE_PROFILE,
}
OFFICIAL_DOCS_GENERIC_SITE_PROFILE: dict[str, Any] = {
    "description": "Official documentation pages: keep main docs/reference body; skip navigation, toc, version switchers, feedback and marketing chrome.",
    "extracts": {
        "article": {
            "containerSelectors": (
                "main",
                "article",
                "[role='main']",
                ".docs-content",
                ".documentation",
                ".docMainContainer",
                ".theme-doc-markdown",
                ".markdown",
                ".markdown-body",
                ".prose",
                ".sl-markdown-content",
                "#content",
                "body",
            ),
            "articleSelectors": (
                "main article",
                "main",
                "article",
                "[role='main']",
                ".docs-content",
                ".documentation",
                ".docMainContainer",
                ".theme-doc-markdown",
                ".markdown",
                ".markdown-body",
                ".prose",
                ".sl-markdown-content",
                "#content",
            ),
            "removeSelectors": (
                ".sidebar",
                ".toc",
                ".table-of-contents",
                ".on-this-page",
                ".breadcrumbs",
                ".breadcrumb",
                ".feedback",
                ".rating",
                ".pagination",
                ".pager",
                ".prev-next",
                ".theme-doc-sidebar-container",
                ".theme-doc-toc-desktop",
                ".theme-doc-footer",
                ".version",
                ".version-selector",
                ".language-selector",
                ".announcement",
                ".banner",
                ".cookie",
                "[aria-label='breadcrumb']",
                "[aria-label='Table of contents']",
                "[role='navigation']",
                "aside",
                "nav",
                "footer",
                "header",
                "form",
            ),
        }
    },
}

MAX_TEXT_CHARS = 12000
MAX_LINKS = 20
MAX_MEDIA = 12
WEB_READ_TIMEOUT_SECONDS = 45.0
WEB_READER_FALLBACK_ENDPOINT = "https://r.jina.ai/"
WEB_SEARCH_PROVIDER_TIMEOUT_SECONDS = 20.0
WEB_SEARCH_TOTAL_TIMEOUT_SECONDS = 45.0
METASO_HOME_URL = "https://metaso.cn/"
METASO_API_SEARCH_ENDPOINT = "https://metaso.cn/api/v1/search"
METASO_SEARCH_ENDPOINT = "https://metaso.cn/api/searchV2"
METASO_VERTICAL_ENGINE_TYPES: dict[str, str] = {
    "all": "",
    "web": "",
    "webpage": "",
    "document": "pdf",
    "academic": "scholar",
    "scholar": "scholar",
    "image": "image",
    "video": "video",
    "podcast": "podcast",
}
METASO_API_SCOPES: dict[str, str] = {
    "all": "webpage",
    "web": "webpage",
    "webpage": "webpage",
    "document": "document",
    "academic": "scholar",
    "scholar": "scholar",
    "image": "image",
    "video": "video",
    "podcast": "podcast",
}
SEARCH_PROVIDER_URLS: dict[str, str] = {
    "metaso": "https://metaso.cn/?q={query}",
    "bing": "https://www.bing.com/search?q={query}",
    "google": "https://www.google.com/search?q={query}&hl=en",
    "baidu": "https://www.baidu.com/s?wd={query}",
    "duckduckgo": "https://html.duckduckgo.com/html/?q={query}",
}
# Prefer MetaSo and scrape-friendly lightweight HTML endpoints first. Bing is
# frequently unavailable behind some VPN/proxy routes; Google/Baidu may return
# challenge pages. All providers remain available explicitly or via config override.
SEARCH_PROVIDER_ORDER = ("metaso", "duckduckgo", "google", "bing", "baidu")
IMPLEMENTED_SEARCH_PROVIDERS = (
    "brave",
    "tavily",
    "exa",
    "metaso",
    "duckduckgo",
    "google",
    "bing",
    "baidu",
    "searxng",
)
SOURCE_PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "brave": {
        "region": "global",
        "role": "discovery",
        "authEnv": "BRAVE_SEARCH_API_KEY",
        "supports": ["search", "freshness", "safe_search"],
        "costTier": "low",
        "latencyTier": "fast",
        "requiresProxy": "auto",
        "outputFormats": ["search_results"],
        "implemented": True,
    },
    "tavily": {
        "region": "global",
        "role": "discovery",
        "authEnv": "TAVILY_API_KEY",
        "supports": ["search", "raw_markdown", "country", "time_filter"],
        "costTier": "medium",
        "latencyTier": "medium",
        "requiresProxy": "auto",
        "outputFormats": ["search_results", "markdown"],
        "implemented": True,
    },
    "exa": {
        "region": "global",
        "role": "discovery",
        "authEnv": "EXA_API_KEY",
        "supports": ["search", "contents", "neural_search"],
        "costTier": "medium",
        "latencyTier": "medium",
        "requiresProxy": "auto",
        "outputFormats": ["search_results", "contents"],
        "implemented": True,
    },
    "jina": {
        "region": "global",
        "role": "read_extract",
        "authEnv": "JINA_API_KEY",
        "supports": ["reader", "search", "rerank", "llm_friendly_text"],
        "costTier": "low",
        "latencyTier": "medium",
        "requiresProxy": "auto",
        "outputFormats": ["markdown", "text"],
        "implemented": True,
    },
    "firecrawl": {
        "region": "global",
        "role": "read_extract",
        "authEnv": "FIRECRAWL_API_KEY",
        "supports": ["scrape", "markdown", "html", "raw_html", "screenshot"],
        "costTier": "medium",
        "latencyTier": "medium",
        "requiresProxy": "auto",
        "outputFormats": ["markdown", "html", "raw_html", "screenshot"],
        "implemented": False,
    },
    "perplexity": {
        "region": "global",
        "role": "deep_answer",
        "authEnv": "PERPLEXITY_API_KEY",
        "supports": ["grounded_answer", "citations"],
        "costTier": "medium",
        "latencyTier": "medium",
        "requiresProxy": "auto",
        "outputFormats": ["answer", "citations"],
        "implemented": False,
    },
    "bocha": {
        "region": "cn",
        "role": "discovery",
        "authEnv": "BOCHA_API_KEY",
        "supports": ["search", "cn_web"],
        "costTier": "low",
        "latencyTier": "fast",
        "requiresProxy": False,
        "outputFormats": ["search_results"],
        "implemented": False,
    },
    "metaso": {
        "region": "cn",
        "role": "discovery",
        "supports": ["search", "verticals", "cn_web", "documents", "academic", "media"],
        "costTier": "free_public",
        "latencyTier": "medium",
        "requiresProxy": False,
        "supportsLoginProfile": True,
        "outputFormats": ["search_results"],
        "implemented": True,
    },
    "baidu": {
        "region": "cn",
        "role": "discovery",
        "supports": ["search", "cn_web"],
        "costTier": "free_public",
        "latencyTier": "medium",
        "requiresProxy": False,
        "supportsLoginProfile": True,
        "outputFormats": ["search_results"],
        "implemented": True,
    },
    "duckduckgo": {
        "region": "global",
        "role": "discovery",
        "supports": ["search", "lightweight_html"],
        "costTier": "free_public",
        "latencyTier": "medium",
        "requiresProxy": "auto",
        "outputFormats": ["search_results"],
        "implemented": True,
    },
    "google": {
        "region": "global",
        "role": "discovery",
        "supports": ["search"],
        "costTier": "free_public",
        "latencyTier": "medium",
        "requiresProxy": "auto",
        "outputFormats": ["search_results"],
        "implemented": True,
    },
    "bing": {
        "region": "global",
        "role": "discovery",
        "supports": ["search"],
        "costTier": "free_public",
        "latencyTier": "medium",
        "requiresProxy": "auto",
        "outputFormats": ["search_results"],
        "implemented": True,
    },
    "searxng": {
        "region": "self_host",
        "role": "discovery",
        "supports": ["search", "aggregated_search"],
        "costTier": "self_host",
        "latencyTier": "depends",
        "requiresProxy": "config",
        "outputFormats": ["search_results"],
        "implemented": True,
    },
}
DEFAULT_CN_SOURCE_PROVIDERS = ("bocha", "metaso", "baidu", "duckduckgo", "google", "bing", "searxng")
DEFAULT_GLOBAL_SOURCE_PROVIDERS = ("brave", "tavily", "exa", "duckduckgo", "google", "bing", "metaso", "baidu", "searxng")
_SOURCE_PROVIDER_REGISTRY = get_source_provider_capabilities()
if _SOURCE_PROVIDER_REGISTRY:
    SOURCE_PROVIDER_CAPABILITIES = _SOURCE_PROVIDER_REGISTRY
_SOURCE_ROUTER_DEFAULTS = get_source_router_defaults()
if isinstance(_SOURCE_ROUTER_DEFAULTS.get("cnPreferred"), list):
    DEFAULT_CN_SOURCE_PROVIDERS = tuple(str(item).strip() for item in _SOURCE_ROUTER_DEFAULTS["cnPreferred"] if str(item).strip())
if isinstance(_SOURCE_ROUTER_DEFAULTS.get("globalPreferred"), list):
    DEFAULT_GLOBAL_SOURCE_PROVIDERS = tuple(str(item).strip() for item in _SOURCE_ROUTER_DEFAULTS["globalPreferred"] if str(item).strip())
WINDOWS_CA_BUNDLE_NAME = "windows-system-ca.pem"
WINDOWS_CA_BUNDLE_MAX_AGE_SECONDS = 24 * 60 * 60
PROXY_ENV_KEYS = (
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)
_WEB_BROKER_CONTEXT_COUNTS: dict[str, int] = {}
_WEB_BROKER_RESEARCH_WARNING_THRESHOLD = 3


@dataclass(slots=True)
class WebPagePayload:
    url: str
    final_url: str
    requested_mode: str
    referer_mode: str
    referer_url: str
    fetch_mode: str
    attempted_modes: List[str]
    available_modes: Dict[str, Dict[str, Any]]
    status: int | None
    tls_strategy: str
    ca_bundle_path: str
    proxy_bypass_used: bool
    title: str
    text: str
    html: str
    metadata: Dict[str, Any]
    links: List[Dict[str, str]]
    media: List[Dict[str, str]]
    warnings: List[str]
    agent_browser_profile_used: bool = False
    agent_browser_profile_host: str = ""
    agent_browser_profile_dir: str = ""
    agent_browser_kind: str = ""


def _enforce_safety_decision(decision, *, tool_call_id: str, question: str) -> tuple[bool, str | None]:
    safety_guardian.log_decision_event(
        action="web_fetch_safety",
        decision=decision,
        subject=question,
        metadata={"toolCallId": tool_call_id},
    )
    if decision.is_allow():
        return True, None

    if should_auto_approve_safety_review(decision):
        log_safety_review_auto_approved(
            decision,
            action="web_fetch_safety",
            subject=question,
            tool_call_id=tool_call_id,
        )
        return True, None

    from langgraph.types import interrupt

    response = interrupt(decision.to_interrupt_request(question=question, tool_call_id=tool_call_id))
    approved = True
    if isinstance(response, dict):
        approved = bool(response.get("approved", True))

    if decision.is_block() or not decision.allow_override:
        return False, f"Safety Guardian 已阻止网页操作：{decision.reason}"

    if not approved:
        return False, f"Safety Guardian 未获得批准，网页操作已取消：{decision.reason}"

    return True, None


def _guard_url(url: str, *, tool_call_id: str) -> tuple[bool, str | None]:
    runtime_context = get_runtime_context()
    decision = safety_guardian.assess_http_request("GET", url, body=None, runtime_context=runtime_context)
    return _enforce_safety_decision(
        decision,
        tool_call_id=tool_call_id,
        question=f"Safety Guardian 检测到网页读取需要确认，是否继续？\n\nGET {url}",
    )


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _runtime_context_value(context: Any, *keys: str) -> str:
    if isinstance(context, dict):
        for key in keys:
            value = _safe_text(context.get(key))
            if value:
                return value
    for key in keys:
        value = _safe_text(getattr(context, key, ""))
        if value:
            return value
    return ""


def _note_web_broker_context_call(tool_call_id: str) -> tuple[int, bool]:
    try:
        context = get_runtime_context()
    except Exception:
        context = None
    context_id = (
        _runtime_context_value(context, "run_id", "runId")
        or _runtime_context_value(context, "session_id", "sessionId", "conversation_id", "conversationId")
    )
    if not context_id:
        context_id = _safe_text(tool_call_id)[:32]
    if not context_id:
        return 0, False
    current = _WEB_BROKER_CONTEXT_COUNTS.get(context_id, 0) + 1
    _WEB_BROKER_CONTEXT_COUNTS[context_id] = current
    # Keep the in-memory counter bounded for long-lived dev processes.
    if len(_WEB_BROKER_CONTEXT_COUNTS) > 512:
        for key in list(_WEB_BROKER_CONTEXT_COUNTS.keys())[:128]:
            _WEB_BROKER_CONTEXT_COUNTS.pop(key, None)
    return current, current > _WEB_BROKER_RESEARCH_WARNING_THRESHOLD


def _classify_web_fetch_failure(error: str, *, blocked: bool = False) -> str:
    if blocked:
        return "blocked_by_safety"
    lowered = _safe_text(error).lower()
    timeout_needles = (
        "timeout",
        "timed_out",
        "deadline_exceeded",
        "err_connection_timed_out",
        "net::err_connection_timed_out",
    )
    if any(needle in lowered for needle in timeout_needles):
        return "network_timeout"
    if "invalid argument" in lowered or "expected `int`" in lowered:
        return "tool_configuration_error"
    if "no active session available" in lowered:
        return "tool_context_unavailable"
    if "agent_browser_profile_not_allowed" in lowered:
        return "agent_browser_profile_not_allowed"
    if "agent_browser_profile_mismatch" in lowered:
        return "agent_browser_profile_mismatch"
    if "agent_browser_not_open" in lowered or "agent_browser_cdp_unavailable" in lowered:
        return "agent_browser_not_open"
    if "needs_login" in lowered or "login_required" in lowered or "auth_required" in lowered:
        return "needs_login"
    return "web_fetch_failed"


def _source_provider_config(provider: str) -> dict[str, Any]:
    config = get_web_fetch_config()
    providers = config.get("providers")
    if isinstance(providers, dict) and isinstance(providers.get(provider), dict):
        return dict(providers[provider])
    return {}


def _source_provider_capability(provider: str) -> dict[str, Any]:
    capability = dict(SOURCE_PROVIDER_CAPABILITIES.get(provider) or {})
    capability.setdefault("id", provider)
    provider_config = _source_provider_config(provider)
    for key in ("region", "role", "authEnv", "costTier", "latencyTier", "requiresProxy", "supportsLoginProfile", "outputFormats", "implemented"):
        if provider_config.get(key) not in (None, "", [], {}):
            capability[key] = provider_config.get(key)
    if provider_config.get("supports") not in (None, "", [], {}):
        capability["supports"] = provider_config.get("supports")
    return capability


def _source_provider_public_capability(provider: str) -> dict[str, Any]:
    capability = _source_provider_capability(provider)
    return {
        "id": provider,
        "region": capability.get("region") or "unknown",
        "role": capability.get("role") or "discovery",
        "supports": capability.get("supports") or [],
        "costTier": capability.get("costTier") or "unknown",
        "latencyTier": capability.get("latencyTier") or "unknown",
        "requiresProxy": capability.get("requiresProxy", "auto"),
        "supportsLoginProfile": bool(capability.get("supportsLoginProfile")),
        "outputFormats": capability.get("outputFormats") or ["search_results"],
    }


def _provider_auth_env(provider: str) -> str:
    config = _source_provider_config(provider)
    return _safe_text(config.get("authEnv") or _source_provider_capability(provider).get("authEnv"))


def _provider_api_key(provider: str) -> str:
    config = _source_provider_config(provider)
    configured_key = _safe_text(config.get("apiKey") or config.get("credential") or config.get("key"))
    if configured_key:
        return configured_key
    auth_env = _provider_auth_env(provider)
    return _safe_text(os.getenv(auth_env)) if auth_env else ""


def _provider_missing_credential(provider: str) -> str:
    # MetaSo has two valid routes: configured API key via /api/v1/search, or the
    # existing Agent Browser/public-search fallback. Do not let the generic API
    # credential gate skip it before those routes can decide.
    if str(provider or "").strip().lower() == "metaso":
        return ""
    auth_env = _provider_auth_env(provider)
    if auth_env and not _provider_api_key(provider):
        return auth_env
    return ""


def _provider_enabled(provider: str) -> bool:
    config = _source_provider_config(provider)
    if "enabled" in config:
        return bool(config.get("enabled"))
    return provider in SOURCE_PROVIDER_CAPABILITIES


def _searxng_base_url() -> str:
    provider_config = _source_provider_config("searxng")
    return _safe_text(provider_config.get("baseUrl") or provider_config.get("url") or get_web_fetch_config().get("searxngUrl"))


def _provider_search_url(provider: str, query: str) -> str:
    quoted = quote_plus(query)
    if provider == "brave":
        return f"https://api.search.brave.com/res/v1/web/search?q={quoted}"
    if provider == "tavily":
        return "https://api.tavily.com/search"
    if provider == "exa":
        return "https://api.exa.ai/search"
    if provider == "searxng":
        base_url = _searxng_base_url()
        if not base_url:
            return ""
        return f"{base_url.rstrip('/')}/search?q={quoted}&format=json"
    template = SEARCH_PROVIDER_URLS.get(provider)
    return template.format(query=quoted) if template else ""


def _looks_cn_query(value: str) -> bool:
    text = _safe_text(value).lower()
    if re.search(r"[\u4e00-\u9fff]", text):
        return True
    cn_needles = (
        ".cn",
        "china",
        "chinese",
        "wechat",
        "weixin",
        "bilibili",
        "zhihu",
        "douyin",
        "taobao",
        "alipay",
        "qq",
    )
    return any(needle in text for needle in cn_needles)


def _detect_source_locale(value: str, *, locale_hint: str = "", source_policy: str = "") -> str:
    hint = _safe_text(locale_hint or source_policy).lower()
    if hint in {"cn", "zh", "zh-cn", "china", "cn_direct", "domestic"}:
        return "cn"
    if hint in {"global", "en", "english", "global_proxy", "international"}:
        return "global"
    return "cn" if _looks_cn_query(value) else "global"


def _ordered_unique(items: list[str] | tuple[str, ...]) -> list[str]:
    ordered: list[str] = []
    for item in items:
        value = _safe_text(item).lower()
        if value and value not in ordered:
            ordered.append(value)
    return ordered


def _configured_source_provider_order(locale: str) -> list[str]:
    config = get_web_fetch_config()
    router = config.get("sourceRouter") if isinstance(config.get("sourceRouter"), dict) else {}
    key = "cnPreferred" if locale == "cn" else "globalPreferred"
    configured = router.get(key) if isinstance(router, dict) else None
    if isinstance(configured, list) and configured:
        return _ordered_unique(configured)

    legacy = config.get("searchProviderOrder")
    legacy_order = _ordered_unique(legacy) if isinstance(legacy, list) else []
    defaults = list(DEFAULT_CN_SOURCE_PROVIDERS if locale == "cn" else DEFAULT_GLOBAL_SOURCE_PROVIDERS)
    return _ordered_unique([*legacy_order, *defaults])


def _provider_network_route(provider: str, locale: str, *, needs_login: bool = False) -> str:
    if needs_login:
        return "agent_browser"
    capability = _source_provider_capability(provider)
    region = _safe_text(capability.get("region")).lower()
    requires_proxy = capability.get("requiresProxy")
    if region == "cn":
        return "cn_direct"
    if region == "self_host":
        return "auto"
    if requires_proxy is True:
        return "global_proxy"
    return "cn_direct" if locale == "cn" and region == "cn" else "global_proxy"


def _source_router_plan(
    *,
    query: str,
    requested_provider: str,
    locale_hint: str = "",
    source_policy: str = "",
    needs_login: bool = False,
    ui_reference: bool = False,
    high_stakes: bool = False,
) -> dict[str, Any]:
    locale = _detect_source_locale(query, locale_hint=locale_hint, source_policy=source_policy)
    if requested_provider and requested_provider != "auto":
        candidates = [requested_provider]
    else:
        candidates = _configured_source_provider_order(locale)

    planned = _ordered_unique(candidates)
    executable: list[str] = []
    skipped: list[dict[str, Any]] = []
    for provider in planned:
        capability = _source_provider_capability(provider)
        if provider not in SOURCE_PROVIDER_CAPABILITIES:
            skipped.append(
                {
                    "provider": provider,
                    "status": "skipped",
                    "failureClass": "provider_unknown",
                    "reason": "source_provider_not_registered",
                }
            )
            continue
        if not _provider_enabled(provider):
            skipped.append(
                {
                    "provider": provider,
                    "status": "skipped",
                    "failureClass": "provider_disabled",
                    "reason": "provider_disabled_by_config",
                }
            )
            continue
        missing_env = _provider_missing_credential(provider)
        if missing_env:
            skipped.append(
                {
                    "provider": provider,
                    "status": "skipped",
                    "failureClass": "credential_missing",
                    "reason": f"missing_env:{missing_env}",
                    "authEnv": missing_env,
                }
            )
            continue
        if provider == "searxng" and not _searxng_base_url():
            skipped.append(
                {
                    "provider": provider,
                    "status": "skipped",
                    "failureClass": "provider_unconfigured",
                    "reason": "searxng_base_url_missing",
                }
            )
            continue
        if provider not in IMPLEMENTED_SEARCH_PROVIDERS or not bool(capability.get("implemented")):
            skipped.append(
                {
                    "provider": provider,
                    "status": "skipped",
                    "failureClass": "provider_adapter_unavailable",
                    "reason": "provider_registered_but_adapter_not_enabled",
                }
            )
            continue
        executable.append(provider)

    selected_route_provider = executable[0] if executable else (planned[0] if planned else "")
    return {
        "locale": locale,
        "intent": "ui_reference" if ui_reference else ("high_stakes" if high_stakes else "search"),
        "requestedProvider": requested_provider,
        "plannedProviders": planned,
        "providers": executable,
        "skippedProviders": skipped,
        "networkRoute": _provider_network_route(selected_route_provider, locale, needs_login=needs_login) if selected_route_provider else "auto",
    }


def _source_router_payload_fields(
    plan: dict[str, Any],
    *,
    selected_provider: str = "",
    attempted_providers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    provider = selected_provider or _safe_text((plan.get("providers") or [""])[0] if isinstance(plan.get("providers"), list) else "")
    locale = plan.get("locale") or "global"
    network_route = _provider_network_route(provider, str(locale)) if provider else (plan.get("networkRoute") or "auto")
    return {
        "sourceCapability": _source_provider_public_capability(provider) if provider else {},
        "networkRoute": network_route,
        "providerAttemptMatrix": attempted_providers if attempted_providers is not None else plan.get("skippedProviders") or [],
        "sourceRouter": {
            "locale": locale,
            "intent": plan.get("intent") or "search",
            "requestedProvider": plan.get("requestedProvider") or "auto",
            "plannedProviders": plan.get("plannedProviders") or [],
            "executableProviders": plan.get("providers") or [],
            "selectedProvider": provider or None,
            "skippedProviders": plan.get("skippedProviders") or [],
            "networkRoute": network_route,
        },
    }


def _web_read_source_fields(url: str, *, used_browser_profile: bool = False) -> dict[str, Any]:
    locale = _detect_source_locale(url)
    network_route = "agent_browser" if used_browser_profile else ("cn_direct" if locale == "cn" else "global_proxy")
    capability = {
        "id": "builtin_scrapling",
        "region": "auto",
        "role": "read_extract",
        "supports": ["markdown", "article", "links", "metadata", "media", "raw_html", "ui_snapshot"],
        "costTier": "local",
        "latencyTier": "medium",
        "requiresProxy": "auto",
        "supportsLoginProfile": True,
        "outputFormats": ["markdown", "html", "raw_html", "ui_snapshot"],
    }
    return {
        "sourceCapability": capability,
        "networkRoute": network_route,
        "providerAttemptMatrix": [
            {
                "provider": "builtin_scrapling",
                "status": "ok",
                "networkRoute": network_route,
                "usedBrowserProfile": bool(used_browser_profile),
            }
        ],
        "sourceRouter": {
            "locale": locale,
            "intent": "read_extract",
            "requestedProvider": "builtin_scrapling",
            "plannedProviders": ["builtin_scrapling"],
            "executableProviders": ["builtin_scrapling"],
            "selectedProvider": "builtin_scrapling",
            "skippedProviders": [],
            "networkRoute": network_route,
        },
    }


def _search_provider_order(requested_provider: str) -> list[str]:
    plan = _source_router_plan(query="", requested_provider=requested_provider)
    return list(plan.get("providers") or [])


def _is_loopback_sink_proxy(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    return parsed.port in {0, 9}


def _should_bypass_proxy_env() -> bool:
    config = get_web_fetch_config()
    if bool(config.get("bypassProxyEnv")):
        return True

    values = [_safe_text(os.getenv(key)) for key in PROXY_ENV_KEYS]
    active = [value for value in values if value]
    return bool(active) and all(_is_loopback_sink_proxy(value) for value in active if "://" in value)


def _agent_browser_profile_allowed(url: str) -> tuple[bool, str | None]:
    config = get_web_fetch_config()
    if not bool(config.get("useAgentBrowserProfile")):
        return False, None
    return agent_browser_profile_allowed_for_url(url, config.get("agentBrowserProfileAllowlist") or [])


def _auto_agent_browser_profile_allowed(url: str, mode: str) -> tuple[bool, str | None]:
    if str(mode or "").strip().lower() == "static":
        return False, None
    return _agent_browser_profile_allowed(url)


def _active_agent_browser_cdp_context() -> dict[str, str]:
    runtime_config = storage.get_computer_use_config() or {}
    browser_lane = dict(runtime_config.get("browserLane") or {})
    proxy_port = int(browser_lane.get("proxyPort") or 3456)
    target_port = max(9222, proxy_port + 100)
    endpoint = f"http://127.0.0.1:{target_port}"
    try:
        response = requests.get(f"{endpoint}/json/version", timeout=1.0)
        response.raise_for_status()
        payload = dict(response.json() or {})
    except Exception as exc:
        raise RuntimeError(
            "agent_browser_not_open: open Agent Browser in Admin and finish login before using its session."
        ) from exc
    product = " ".join(
        [
            str(payload.get("Browser") or ""),
            str(payload.get("User-Agent") or payload.get("userAgent") or ""),
        ]
    ).lower()
    if "edg/" in product or "edge/" in product:
        browser_kind = "edge"
    elif "chromium/" in product:
        browser_kind = "chromium"
    else:
        browser_kind = "chrome"
    websocket_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
    if not websocket_url.startswith(("ws://", "wss://")):
        raise RuntimeError("agent_browser_cdp_unavailable: browser did not publish a WebSocket debugger URL.")
    profile_dir = configured_agent_browser_profile_dir(browser_kind)
    if not debug_port_owned_by_profile(port=target_port, profile_dir=profile_dir):
        raise RuntimeError(
            "agent_browser_profile_mismatch: the CDP endpoint is not owned by the V8OS Agent Browser profile."
        )
    return {
        "cdpUrl": websocket_url,
        "browserKind": browser_kind,
        "profileDir": str(profile_dir),
    }


def _provider_prefers_agent_browser_profile(provider: str) -> bool:
    return str(provider or "").strip().lower() in {"metaso", "baidu"}


def _agent_browser_profile_search_skip(provider: str, search_url: str) -> dict[str, Any] | None:
    if not _provider_prefers_agent_browser_profile(provider):
        return None
    if str(provider or "").strip().lower() == "metaso" and _provider_api_key("metaso"):
        return None
    allowed, matched_host = _agent_browser_profile_allowed(search_url)
    if allowed:
        return None
    return {
        "provider": provider,
        "status": "skipped",
        "failureClass": "needs_agent_browser_login",
        "reason": "agent_browser_profile_not_enabled_or_domain_not_allowlisted",
        "matchedHost": matched_host,
        "recommendedNextAction": (
            "在 Admin / 深度调研打开 Agent 浏览器登录该站点，"
            "并启用 systemBase.webFetch.useAgentBrowserProfile 与域名 allowlist；否则使用其他公开搜索源。"
        ),
    }


@contextmanager
def _bypass_proxy_env(enabled: bool):
    if not enabled:
        yield False
        return

    snapshot = {key: os.environ.pop(key, None) for key in PROXY_ENV_KEYS}
    try:
        yield True
    finally:
        for key, value in snapshot.items():
            if value is not None:
                os.environ[key] = value


def _web_fetch_cache_dir() -> Path:
    web_fetch_config = get_web_fetch_config()
    override = _safe_text(web_fetch_config.get("cacheDir"))
    candidates = [Path(override)] if override else []
    temp_dir_candidate = ""
    try:
        temp_dir_candidate = tempfile.gettempdir()
    except Exception:
        temp_dir_candidate = ""
    candidates.extend(
        [
            storage.base_dir / "web_fetch",
            Path(os.getenv("LOCALAPPDATA", "")) / "v8chat" / "web_fetch" if _safe_text(os.getenv("LOCALAPPDATA")) else Path(),
            Path(temp_dir_candidate) / "v8chat-web-fetch" if temp_dir_candidate else Path(),
        ]
    )
    last_error: Exception | None = None
    for candidate in candidates:
        if not str(candidate):
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test_file = candidate / ".write-test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            return candidate
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"无法创建网页抓取缓存目录：{last_error}")


def _export_windows_ca_bundle() -> str | None:
    if os.name != "nt" or not hasattr(ssl, "enum_certificates"):
        return None

    bundle_path = _web_fetch_cache_dir() / WINDOWS_CA_BUNDLE_NAME
    if bundle_path.exists():
        age_seconds = max(0.0, time.time() - bundle_path.stat().st_mtime)
        if age_seconds <= WINDOWS_CA_BUNDLE_MAX_AGE_SECONDS and bundle_path.stat().st_size > 0:
            return str(bundle_path)

    pem_chunks: list[str] = []
    seen: set[bytes] = set()
    for store_name in ("ROOT", "CA"):
        try:
            certificates = ssl.enum_certificates(store_name)
        except Exception:
            continue
        for cert_bytes, encoding, _trust in certificates:
            if encoding != "x509_asn" or cert_bytes in seen:
                continue
            seen.add(cert_bytes)
            pem_chunks.append(ssl.DER_cert_to_PEM_cert(cert_bytes))

    if not pem_chunks:
        return None

    bundle_path.write_text("".join(pem_chunks), encoding="ascii")
    return str(bundle_path)


def _resolve_verify_candidates() -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    for env_name in ("CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        value = _safe_text(os.getenv(env_name))
        if value and os.path.exists(value) and value not in seen:
            candidates.append((f"env:{env_name}", value))
            seen.add(value)

    windows_bundle = _export_windows_ca_bundle()
    if windows_bundle and windows_bundle not in seen:
        candidates.append(("windows_root_store", windows_bundle))
        seen.add(windows_bundle)

    certifi_bundle = certifi.where()
    if certifi_bundle not in seen:
        candidates.append(("certifi", certifi_bundle))

    return candidates


def _dependency_status() -> dict[str, dict[str, Any]]:
    static_fetcher, static_error = _try_import_static_fetcher()
    dynamic_fetcher, dynamic_error = _try_import_dynamic_fetcher()
    stealth_fetcher, stealth_error = _try_import_stealth_fetcher()
    return {
        "static": {
            "available": static_fetcher is not None,
            "driver": "Fetcher",
            "error": static_error,
        },
        "dynamic": {
            "available": dynamic_fetcher is not None,
            "driver": "DynamicFetcher",
            "error": dynamic_error,
        },
        "stealth": {
            "available": stealth_fetcher is not None,
            "driver": "StealthyFetcher",
            "error": stealth_error,
        },
        "reader": {
            "available": True,
            "driver": "JinaReader",
            "error": None,
        },
    }


def _try_import_static_fetcher():
    try:
        from scrapling.fetchers import Fetcher

        return Fetcher, None
    except Exception as exc:  # pragma: no cover - exercised by runtime environment
        return None, str(exc)


def _try_import_dynamic_fetcher():
    try:
        from scrapling.fetchers import DynamicFetcher

        return DynamicFetcher, None
    except Exception as exc:  # pragma: no cover
        return None, str(exc)


def _try_import_stealth_fetcher():
    try:
        from scrapling.fetchers import StealthyFetcher

        return StealthyFetcher, None
    except Exception as exc:  # pragma: no cover
        return None, str(exc)


def _fetch_with_scrapling(url: str, *, mode: WebFetchMode = "auto", headless: bool = True) -> WebPagePayload:
    return _fetch_with_scrapling_internal(
        url,
        mode=mode,
        headless=headless,
        referer_mode="none",
        referer_url="",
    )


def _build_fetch_options(
    *,
    headless: bool,
    referer_mode: WebRefererMode,
    referer_url: str,
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    extra_headers: dict[str, str] = {}
    static_headers: dict[str, str] = {}
    if referer_mode == "none":
        static_headers["referer"] = ""
    elif referer_mode == "custom" and referer_url:
        static_headers["referer"] = referer_url
    if referer_mode == "custom" and referer_url:
        extra_headers["referer"] = referer_url
    shared = {
        "google_search": referer_mode == "google",
        "extra_headers": extra_headers or None,
        # Scrapling/Playwright validates this as an integer >= 1. Use one
        # attempt at the fetcher layer and keep higher-level retry decisions in
        # V8's tool envelope so the agent sees honest failures quickly.
        "retries": 1,
        "retry_delay": 0,
    }
    browser = {
        **shared,
        "headless": headless,
        "timeout": max(1000, int(timeout_seconds * 1000)),
    }
    static = {
        **shared,
        "headers": static_headers or None,
        "stealthy_headers": referer_mode == "google",
        "timeout": max(1.0, float(timeout_seconds)),
    }
    return static, browser


def _fetch_with_scrapling_internal(
    url: str,
    *,
    mode: WebFetchMode = "auto",
    headless: bool = True,
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    timeout_seconds: float | None = None,
    use_agent_browser_profile: bool = False,
) -> WebPagePayload:
    attempted_modes: list[str] = []
    errors: dict[str, str] = {}
    warnings: list[str] = []
    available_modes = _dependency_status()
    started_at = time.monotonic()

    def _fetch_static() -> WebPagePayload:
        fetcher, error = _try_import_static_fetcher()
        if fetcher is None:
            raise RuntimeError(error or "静态 Fetcher 不可用。")
        bypass_proxy_env = _should_bypass_proxy_env()
        verify_errors: list[str] = []
        verify_candidates = _resolve_verify_candidates()

        with _bypass_proxy_env(bypass_proxy_env):
            for verify_label, verify_target in verify_candidates:
                try:
                    response = fetcher.get(url, verify=verify_target, **static_fetch_options)
                    return _build_payload(
                        response=response,
                        requested_url=url,
                        requested_mode=mode,
                        referer_mode=referer_mode,
                        referer_url=referer_url,
                        fetch_mode="static",
                        attempted_modes=list(attempted_modes),
                        available_modes=available_modes,
                        tls_strategy=verify_label,
                        ca_bundle_path=verify_target,
                        proxy_bypass_used=bypass_proxy_env,
                        warnings=list(warnings),
                    )
                except Exception as exc:
                    verify_errors.append(f"{verify_label}={exc}")
                    errors[f"static_tls:{verify_label}"] = str(exc)

            warnings.append(
                "静态抓取在当前环境无法通过证书链校验，已降级为 verify=False。"
            )
            if verify_errors:
                warnings.append(
                    "静态抓取证书链探测失败：" + " | ".join(verify_errors[:3])
                )
            response = fetcher.get(url, verify=False, **static_fetch_options)
            return _build_payload(
                response=response,
                requested_url=url,
                requested_mode=mode,
                referer_mode=referer_mode,
                referer_url=referer_url,
                fetch_mode="static",
                attempted_modes=list(attempted_modes),
                available_modes=available_modes,
                tls_strategy="verify_false_fallback",
                ca_bundle_path="",
                proxy_bypass_used=bypass_proxy_env,
                warnings=list(warnings),
            )

    def _fetch_dynamic() -> WebPagePayload:
        fetcher, error = _try_import_dynamic_fetcher()
        if fetcher is None:
            raise RuntimeError(error or "动态 Fetcher 不可用。")
        response = fetcher.fetch(url, **_effective_browser_fetch_options())
        return _build_payload(
            response=response,
            requested_url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            fetch_mode="dynamic",
            attempted_modes=list(attempted_modes),
            available_modes=available_modes,
            tls_strategy="browser_managed",
            ca_bundle_path="",
            proxy_bypass_used=False,
            warnings=list(warnings),
            agent_browser_profile_used=bool(agent_browser_profile_dir),
            agent_browser_profile_host=agent_browser_profile_host,
            agent_browser_profile_dir=agent_browser_profile_dir,
            agent_browser_kind=agent_browser_kind,
        )

    def _fetch_stealth() -> WebPagePayload:
        fetcher, error = _try_import_stealth_fetcher()
        if fetcher is None:
            raise RuntimeError(error or "Stealth Fetcher 不可用。")
        response = fetcher.fetch(url, **_effective_browser_fetch_options())
        return _build_payload(
            response=response,
            requested_url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            fetch_mode="stealth",
            attempted_modes=list(attempted_modes),
            available_modes=available_modes,
            tls_strategy="browser_managed",
            ca_bundle_path="",
            proxy_bypass_used=False,
            warnings=list(warnings),
            agent_browser_profile_used=bool(agent_browser_profile_dir),
            agent_browser_profile_host=agent_browser_profile_host,
            agent_browser_profile_dir=agent_browser_profile_dir,
            agent_browser_kind=agent_browser_kind,
        )

    def _fetch_reader() -> WebPagePayload:
        return _fetch_with_reader_fallback(
            url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            attempted_modes=list(attempted_modes),
            available_modes=available_modes,
            timeout_seconds=per_mode_timeout,
            warnings=list(warnings),
        )

    plans: list[tuple[str, Any]]
    if mode == "static":
        plans = [("static", _fetch_static)]
    elif mode == "dynamic":
        plans = [("dynamic", _fetch_dynamic)]
    elif mode == "stealth":
        plans = [("stealth", _fetch_stealth)]
    else:
        plans = [
            ("static", _fetch_static),
            ("dynamic", _fetch_dynamic),
            ("stealth", _fetch_stealth),
            ("reader", _fetch_reader),
        ]
    requested_agent_browser_profile = bool(use_agent_browser_profile)
    auto_agent_browser_profile, auto_matched_host = _auto_agent_browser_profile_allowed(url, mode)
    effective_agent_browser_profile = requested_agent_browser_profile or auto_agent_browser_profile
    if requested_agent_browser_profile:
        plans = [(label, runner) for label, runner in plans if label in {"dynamic", "stealth"}]
        if not plans:
            raise RuntimeError("agent_browser_profile_requires_browser_mode: use dynamic/stealth/auto when useAgentBrowserProfile=true.")
    total_timeout = float(timeout_seconds or WEB_READ_TIMEOUT_SECONDS)
    per_mode_timeout = max(5.0, total_timeout / max(len(plans), 1))
    agent_browser_profile_dir = ""
    agent_browser_profile_host = ""
    agent_browser_kind = ""
    if effective_agent_browser_profile:
        allowed, matched_host = _agent_browser_profile_allowed(url)
        if not allowed:
            raise RuntimeError(
                "agent_browser_profile_not_allowed:"
                " useAgentBrowserProfile=true requires systemBase.webFetch.useAgentBrowserProfile=true"
                " and a matching agentBrowserProfileAllowlist domain."
            )
        agent_browser_profile_host = matched_host or auto_matched_host or ""
    static_fetch_options, browser_fetch_options = _build_fetch_options(
        headless=headless,
        referer_mode=referer_mode,
        referer_url=referer_url,
        timeout_seconds=per_mode_timeout,
    )

    def _effective_browser_fetch_options() -> dict[str, Any]:
        nonlocal agent_browser_profile_dir, agent_browser_kind
        options = dict(browser_fetch_options)
        if effective_agent_browser_profile:
            context = _active_agent_browser_cdp_context()
            agent_browser_profile_dir = context["profileDir"]
            agent_browser_kind = context["browserKind"]
            options["cdp_url"] = context["cdpUrl"]
            options.pop("user_data_dir", None)
        return options

    auto_degraded_pages: list[tuple[str, WebPagePayload, str]] = []

    for label, runner in plans:
        if time.monotonic() - started_at >= total_timeout:
            errors[label] = f"deadline_exceeded_after_{round(time.monotonic() - started_at, 2)}s"
            break
        attempted_modes.append(label)
        try:
            page = runner()
            if mode == "auto":
                reject_reason = _auto_fetch_reject_reason(page)
                if reject_reason:
                    auto_degraded_pages.append((label, page, reject_reason))
                    errors[f"{label}_quality"] = reject_reason
                    continue
                _attach_auto_quality_warnings(
                    page,
                    attempted_modes=attempted_modes,
                    degraded_pages=auto_degraded_pages,
                    returned_degraded=False,
                )
                return page
            return page
        except Exception as exc:
            errors[label] = str(exc)

    if auto_degraded_pages:
        _label, page, _reason = max(auto_degraded_pages, key=lambda item: len(str(item[1].text or "")))
        _attach_auto_quality_warnings(
            page,
            attempted_modes=attempted_modes,
            degraded_pages=auto_degraded_pages,
            returned_degraded=True,
        )
        return page

    elapsed = round(time.monotonic() - started_at, 2)
    details = "; ".join(f"{key}={value}" for key, value in errors.items())
    raise RuntimeError(f"网页抓取失败。attempted={attempted_modes}; elapsed={elapsed}s; deadline={total_timeout}s; errors={details}")


def _fetch_with_reader_fallback(
    url: str,
    *,
    requested_mode: str,
    referer_mode: str,
    referer_url: str,
    attempted_modes: list[str],
    available_modes: dict[str, dict[str, Any]],
    timeout_seconds: float,
    warnings: list[str],
) -> WebPagePayload:
    reader_url = WEB_READER_FALLBACK_ENDPOINT + url
    try:
        with _bypass_proxy_env(_should_bypass_proxy_env()):
            response = requests.get(
                reader_url,
                headers={
                    "Accept": "text/plain, text/markdown;q=0.9, */*;q=0.1",
                    "User-Agent": "V8 Agent OS Web Reader/1.0",
                },
                timeout=max(1.0, float(timeout_seconds)),
            )
    except Exception as exc:
        raise RuntimeError(f"reader_fallback_request_failed: {exc}") from exc

    if response.status_code >= 400:
        body = _safe_text(getattr(response, "text", ""))[:300]
        raise RuntimeError(f"reader_fallback_http_status_{response.status_code}: {body}")

    raw_text = _safe_text(getattr(response, "text", ""))
    title, markdown_text, reader_metadata = _parse_reader_fallback_text(raw_text, url)
    if len(markdown_text) > MAX_TEXT_CHARS:
        markdown_text = markdown_text[:MAX_TEXT_CHARS] + f"\n\n...[TRUNCATED] ({len(markdown_text)} chars total)"

    metadata = {
        "readerFallbackProvider": "jina",
        "readerFallbackUrl": reader_url,
        "readerSourceUrl": url,
        **reader_metadata,
    }
    return WebPagePayload(
        url=url,
        final_url=url,
        requested_mode=requested_mode,
        referer_mode=referer_mode,
        referer_url=referer_url,
        fetch_mode="reader",
        attempted_modes=attempted_modes,
        available_modes=available_modes,
        status=response.status_code,
        tls_strategy="requests",
        ca_bundle_path=certifi.where(),
        proxy_bypass_used=_should_bypass_proxy_env(),
        title=title,
        text=markdown_text,
        html="",
        metadata=metadata,
        links=[],
        media=[],
        warnings=[*warnings, "已使用 Jina Reader fallback 获取页面正文。"],
    )


def _parse_reader_fallback_text(raw_text: str, source_url: str) -> tuple[str, str, dict[str, str]]:
    text = _safe_text(raw_text)
    metadata: dict[str, str] = {}
    title = ""
    content = text

    marker = "\nMarkdown Content:\n"
    if marker in text:
        header, content = text.split(marker, 1)
        for line in header.splitlines():
            normalized = _safe_text(line)
            if normalized.startswith("Title:"):
                title = _safe_text(normalized.removeprefix("Title:"))
                metadata["readerTitle"] = title
            elif normalized.startswith("URL Source:"):
                metadata["readerUrlSource"] = _safe_text(normalized.removeprefix("URL Source:"))
            elif normalized.startswith("Published Time:"):
                metadata["readerPublishedTime"] = _safe_text(normalized.removeprefix("Published Time:"))
    if not title:
        first_line = next((line.strip("# ").strip() for line in content.splitlines() if line.strip()), "")
        title = _safe_text(first_line)[:200] or source_url
    if title and title.lower() not in content[:240].lower():
        content = f"# {title}\n\n{content}"
    return title, content.strip(), metadata


def _builtin_site_profile(url: str) -> dict[str, Any]:
    host = _site_profile_key(url)
    for profile_host, profile in BUILTIN_WEB_FETCH_SITE_PROFILES.items():
        if host == profile_host or host.endswith(f".{profile_host}"):
            return profile
    if _looks_like_official_docs_site(url):
        return OFFICIAL_DOCS_GENERIC_SITE_PROFILE
    return {}


def _looks_like_official_docs_site(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    if not host:
        return False
    if host.startswith("docs.") or ".docs." in host:
        return True

    official_docs_hosts = (
        "ai.google.dev",
        "cloud.google.com",
        "developer.apple.com",
        "developers.google.com",
        "docs.anthropic.com",
        "docs.aws.amazon.com",
        "docs.expo.dev",
        "docs.oracle.com",
        "docs.python.org",
        "docs.rs",
        "learn.microsoft.com",
        "nextjs.org",
        "platform.openai.com",
        "react.dev",
        "reactnative.dev",
        "tailwindcss.com",
        "vercel.com",
        "www.typescriptlang.org",
    )
    if host in official_docs_hosts or any(host.endswith(f".{item}") for item in official_docs_hosts):
        return True

    docs_path_markers = (
        "/api/",
        "/api-reference/",
        "/docs/",
        "/documentation/",
        "/guide/",
        "/guides/",
        "/learn/",
        "/manual/",
        "/reference/",
    )
    normalized_path = f"/{path.strip('/')}/" if path.strip("/") else "/"
    return any(marker in normalized_path for marker in docs_path_markers)


def _builtin_extract_profile(url: str, extract: WebExtractMode) -> dict[str, Any]:
    profile = _builtin_site_profile(url)
    return dict(((profile.get("extracts") or {}).get(extract) or {}))


def _builtin_container_selectors(url: str, extract: WebExtractMode) -> tuple[str, ...]:
    profile = _builtin_extract_profile(url, extract)
    selectors = profile.get("containerSelectors") or ()
    return tuple(_safe_text(selector) for selector in selectors if _safe_text(selector))


def _apply_site_profile_cleanup(node: BeautifulSoup, *, url: str, extract: WebExtractMode) -> None:
    profile = _builtin_extract_profile(url, extract)
    remove_selectors = profile.get("removeSelectors") or ()
    for selector in remove_selectors:
        normalized = _safe_text(selector)
        if not normalized:
            continue
        try:
            matches = list(node.select(normalized))
        except Exception:
            continue
        for match in matches:
            match.decompose()


def _append_profiled_text_node(fragment: BeautifulSoup, parent: Any, node: Any) -> None:
    text = _safe_text(node.get_text(" ", strip=True))
    if not text:
        return
    clone = BeautifulSoup(str(node), "html.parser")
    if clone.find(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "tr"]):
        for child in list(clone.contents):
            parent.append(child)
        return
    paragraph = fragment.new_tag("p")
    paragraph.string = text
    parent.append(paragraph)


def _site_profile_node_should_skip(node: Any, *, url: str, extract: WebExtractMode) -> bool:
    profile = _builtin_extract_profile(url, extract)
    if not profile:
        return False
    allow_table_layout = bool(profile.get("allowTableLayout"))
    noisy_tokens = tuple(
        _safe_text(token).lower()
        for token in (
            profile.get("skipMarkerTokens")
            or (
                "catalog",
                "relation",
                "side-content",
                "basic-info",
                "toolbar",
                "album-list",
                "top-tool",
                "promotion",
            )
        )
        if _safe_text(token)
    )
    current = node
    while current is not None and getattr(current, "name", None):
        name = _safe_text(getattr(current, "name", "")).lower()
        if name == "table" and not allow_table_layout:
            return True
        if name in {"aside", "nav", "footer", "header"}:
            return True
        classes = current.get("class") if hasattr(current, "get") else []
        class_text = " ".join(classes) if isinstance(classes, list) else _safe_text(classes)
        id_text = _safe_text(current.get("id")) if hasattr(current, "get") else ""
        marker = f"{class_text} {id_text}".lower()
        if any(token in marker for token in noisy_tokens):
            return True
        current = getattr(current, "parent", None)
    return False


def _profiled_article_container(soup: BeautifulSoup, *, url: str) -> BeautifulSoup | None:
    profile = _builtin_extract_profile(url, "article")
    selectors = profile.get("articleSelectors") or ()
    if not selectors:
        return None

    fragment = BeautifulSoup("<main></main>", "html.parser")
    main = fragment.find("main")
    if main is None:
        return None
    seen_nodes: set[int] = set()
    for selector in selectors:
        normalized = _safe_text(selector)
        if not normalized:
            continue
        try:
            matches = list(soup.select(normalized))
        except Exception:
            continue
        for match in matches:
            node_id = id(match)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            if _site_profile_node_should_skip(match, url=url, extract="article"):
                continue
            _append_profiled_text_node(fragment, main, match)
    if not _safe_text(main.get_text(" ", strip=True)):
        return None
    return fragment


def _build_payload(
    *,
    response: Any,
    requested_url: str,
    requested_mode: str,
    referer_mode: str,
    referer_url: str,
    fetch_mode: str,
    attempted_modes: list[str],
    available_modes: dict[str, dict[str, Any]],
    tls_strategy: str,
    ca_bundle_path: str,
    proxy_bypass_used: bool,
    warnings: list[str],
    agent_browser_profile_used: bool = False,
    agent_browser_profile_host: str = "",
    agent_browser_profile_dir: str = "",
    agent_browser_kind: str = "",
) -> WebPagePayload:
    html = _safe_text(getattr(response, "html_content", "")) or _safe_text(getattr(response, "text", ""))
    final_url = _safe_text(getattr(response, "url", "")) or requested_url
    status = getattr(response, "status", None)
    soup = BeautifulSoup(html, "html.parser")

    title = _safe_text(soup.title.string if soup.title and soup.title.string else "")
    if not title and hasattr(response, "css"):
        try:
            title = _safe_text(response.css("title::text").get())
        except Exception:
            title = ""

    text = _extract_main_text(soup, final_url)
    links = _extract_links(soup, final_url)
    metadata = _extract_metadata(soup, final_url)
    media = _extract_media(soup, final_url)
    return WebPagePayload(
        url=requested_url,
        final_url=final_url,
        requested_mode=requested_mode,
        referer_mode=referer_mode,
        referer_url=referer_url,
        fetch_mode=fetch_mode,
        attempted_modes=attempted_modes,
        available_modes=available_modes,
        status=status,
        tls_strategy=tls_strategy,
        ca_bundle_path=ca_bundle_path,
        proxy_bypass_used=proxy_bypass_used,
        title=title,
        text=text,
        html=html,
        metadata=metadata,
        links=links,
        media=media,
        warnings=warnings,
        agent_browser_profile_used=agent_browser_profile_used,
        agent_browser_profile_host=agent_browser_profile_host,
        agent_browser_profile_dir=agent_browser_profile_dir,
        agent_browser_kind=agent_browser_kind,
    )


def _extract_main_text(soup: BeautifulSoup, url: str = "") -> str:
    candidate = _profiled_article_container(soup, url=url) or soup.find("main") or soup.find("article") or soup.body or soup
    candidate = BeautifulSoup(str(candidate), "html.parser")
    _apply_site_profile_cleanup(candidate, url=url, extract="article")
    for tag in candidate(["script", "style", "noscript", "svg", "canvas", "nav", "footer", "header"]):
        tag.decompose()
    text = _html_to_markdown(candidate)
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS] + f"\n\n...[TRUNCATED] ({len(text)} chars total)"
    return text


def _html_to_markdown(node: BeautifulSoup) -> str:
    lines: list[str] = []
    for child in node.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "tr"], recursive=True):
        text = _safe_text(child.get_text(" ", strip=True))
        if not text:
            continue
        name = str(child.name or "").lower()
        if name.startswith("h") and len(name) == 2 and name[1].isdigit():
            level = max(1, min(6, int(name[1])))
            lines.append(f"{'#' * level} {text}")
        elif name == "li":
            lines.append(f"- {text}")
        elif name == "pre":
            lines.extend(["```", text, "```"])
        elif name == "blockquote":
            lines.append(f"> {text}")
        elif name == "tr":
            cells = [_safe_text(cell.get_text(" ", strip=True)) for cell in child.find_all(["th", "td"], recursive=False)]
            cells = [cell for cell in cells if cell]
            if cells:
                lines.append("| " + " | ".join(cells) + " |")
        else:
            lines.append(text)
    if not lines:
        lines = [line.strip() for line in node.get_text("\n").splitlines() if line.strip()]
    deduped: list[str] = []
    previous = ""
    for line in lines:
        if line == previous:
            continue
        deduped.append(line)
        previous = line
    return "\n".join(deduped)


def _html_preview(html: str, *, limit: int = 12000) -> tuple[str, bool]:
    text = str(html or "").strip()
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n\n...[TRUNCATED RAW HTML] ({len(text)} chars total)", True


def _ui_snapshot(node: BeautifulSoup, *, limit: int = 80) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    selectors = "main, article, section, header, footer, nav, form, input, textarea, select, button, a, img, h1, h2, h3, [role]"
    for item in node.select(selectors):
        label = _safe_text(item.get("aria-label") or item.get("alt") or item.get("title") or item.get_text(" ", strip=True))
        if not label and item.name not in {"input", "textarea", "select"}:
            continue
        entry = {
            "tag": item.name,
            "role": _safe_text(item.get("role")),
            "id": _safe_text(item.get("id")),
            "class": " ".join(item.get("class") or [])[:120] if isinstance(item.get("class"), list) else _safe_text(item.get("class")),
            "text": label[:220],
            "href": _safe_text(item.get("href")),
        }
        snapshot.append({key: value for key, value in entry.items() if value})
        if len(snapshot) >= limit:
            break
    return snapshot


def _page_quality_fields(page: WebPagePayload, *, text: str = "", html: str = "", mode: str = "read") -> dict[str, Any]:
    content_chars = len(str(text or ""))
    html_chars = len(str(html or page.html or ""))
    missing_reason = ""
    if content_chars < 120:
        missing_reason = "low_text_content"
    elif content_chars < 400 and len(page.media) >= 2:
        missing_reason = "media_heavy_or_dynamic"
    return {
        "extractionQuality": "weak" if missing_reason else "usable",
        "contentChars": content_chars,
        "htmlChars": html_chars,
        "missingContentReason": missing_reason,
        "contentFormat": "markdown" if mode in {"read", "article"} else mode,
        "usedBrowserProfile": bool(page.agent_browser_profile_used),
    }


def _auto_fetch_reject_reason(page: WebPagePayload) -> str:
    login_wall = _detect_login_wall(page)
    if login_wall:
        return _safe_text(login_wall.get("reason")) or "login_wall_detected"

    status = int(page.status or 0)
    if status in {403, 429, 503}:
        return f"http_status_{status}"

    final_url = _safe_text(page.final_url or page.url).lower()
    title = _safe_text(page.title).lower()
    text = _safe_text(page.text).lower()
    haystack = " ".join([final_url, title, text[:1200]])
    verification_needles = (
        "anticrawl",
        "captchaview",
        "captcha",
        "captcha challenge",
        "security challenge",
        "cf-challenge",
        "security check",
        "访问验证",
        "安全验证",
        "验证码",
        "百度百科-验证",
        "请输入验证码",
        "验证页面",
    )
    if any(needle in haystack for needle in verification_needles):
        return "verification_or_anti_crawl"

    quality = _page_quality_fields(page, text=page.text, html=page.html, mode="read")
    missing_reason = _safe_text(quality.get("missingContentReason"))
    if missing_reason:
        return missing_reason
    return ""


def _attach_auto_quality_warnings(
    page: WebPagePayload,
    *,
    attempted_modes: list[str],
    degraded_pages: list[tuple[str, WebPagePayload, str]],
    returned_degraded: bool,
) -> None:
    page.attempted_modes = list(attempted_modes)
    if not degraded_pages:
        return
    reasons = ", ".join(f"{label}={reason}" for label, _page, reason in degraded_pages)
    warning = (
        f"auto 未找到更高质量 fallback，已返回最佳降级结果：{reasons}"
        if returned_degraded
        else f"auto 已跳过低质量抓取结果并使用后续 fallback：{reasons}"
    )
    page.warnings = [*page.warnings, warning]


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = _safe_text(anchor.get("href"))
        if not href:
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        label = _safe_text(anchor.get_text(" ", strip=True)) or absolute
        links.append({"text": label[:200], "url": absolute})
        if len(links) >= MAX_LINKS:
            break
    return links


def _extract_metadata(soup: BeautifulSoup, url: str = "") -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for meta in soup.select("meta"):
        key = _safe_text(meta.get("name") or meta.get("property") or meta.get("http-equiv"))
        value = _safe_text(meta.get("content"))
        if key and value and key not in metadata:
            metadata[key] = value[:500]
    metadata.update(_extract_site_profile_metadata(soup, url))
    return metadata


def _extract_site_profile_metadata(soup: BeautifulSoup, url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host == "github.com" or host.endswith(".github.com"):
        return _extract_github_metadata(soup, parsed.path or "")
    return {}


def _extract_github_metadata(soup: BeautifulSoup, path: str) -> dict[str, Any]:
    owner, repo = _github_repository_from_path(path)
    metadata: dict[str, Any] = {}
    if owner and repo:
        metadata["githubOwner"] = owner
        metadata["githubRepo"] = repo
        metadata["githubRepository"] = f"{owner}/{repo}"

    star_text = _extract_github_star_text(soup, owner=owner, repo=repo)
    star_count = _parse_compact_count(star_text)
    if star_text:
        metadata["githubStarsText"] = star_text[:80]
    if star_count is not None:
        metadata["githubStars"] = star_count
    return metadata


def _github_repository_from_path(path: str) -> tuple[str, str]:
    parts = [part for part in (path or "").strip("/").split("/") if part]
    if len(parts) < 2:
        return "", ""
    blocked_roots = {
        "apps",
        "collections",
        "contact",
        "customer-stories",
        "enterprise",
        "events",
        "explore",
        "features",
        "issues",
        "login",
        "marketplace",
        "new",
        "notifications",
        "orgs",
        "organizations",
        "pricing",
        "pulls",
        "search",
        "settings",
        "sponsors",
        "topics",
    }
    owner = _safe_text(parts[0])
    repo = _safe_text(parts[1])
    if owner.lower() in blocked_roots or not owner or not repo:
        return "", ""
    return owner, repo


def _extract_github_star_text(soup: BeautifulSoup, *, owner: str, repo: str) -> str:
    selectors: list[str] = []
    if owner and repo:
        selectors.extend(
            [
                f'a[href="/{owner}/{repo}/stargazers"]',
                f'a[href="/{owner}/{repo}/stargazers/"]',
                f'a[href$="/{owner}/{repo}/stargazers"]',
                f'a[href$="/{owner}/{repo}/stargazers/"]',
            ]
        )
    selectors.extend(
        [
            'a[href$="/stargazers"]',
            'a[href$="/stargazers/"]',
            '[data-testid="stargazers"]',
            '[aria-label*="star" i]',
        ]
    )
    for selector in selectors:
        try:
            matches = list(soup.select(selector))
        except Exception:
            continue
        for match in matches:
            candidates = (
                _safe_text(match.get("aria-label")) if hasattr(match, "get") else "",
                _safe_text(match.get("title")) if hasattr(match, "get") else "",
                _safe_text(match.get_text(" ", strip=True)),
            )
            for candidate in candidates:
                if _looks_like_github_star_text(candidate):
                    return candidate

    page_text = _safe_text(soup.get_text(" ", strip=True))
    for pattern in (
        r"([0-9][0-9,]*(?:\.[0-9]+)?\s*[kKmM]?)\s+(?:stars?|stargazers?)\b",
        r"\bStar\s+([0-9][0-9,]*(?:\.[0-9]+)?\s*[kKmM]?)\b",
        r"\bstargazers?\s+([0-9][0-9,]*(?:\.[0-9]+)?\s*[kKmM]?)\b",
    ):
        match = re.search(pattern, page_text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _looks_like_github_star_text(text: str) -> bool:
    normalized = _safe_text(text)
    if not normalized:
        return False
    lowered = normalized.lower()
    if any(token in lowered for token in ("star", "stargazer", "users starred")):
        return _parse_compact_count(normalized) is not None
    return _parse_compact_count(normalized) is not None and len(normalized) <= 24


def _parse_compact_count(text: str) -> int | None:
    normalized = _safe_text(text).replace("\xa0", " ")
    match = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM]?)", normalized)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    return int(round(value))


def _extract_media(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    media: list[dict[str, str]] = []
    seen: set[str] = set()
    selectors = [
        ("img[src]", "image", "src"),
        ("source[src]", "source", "src"),
        ("video[src]", "video", "src"),
        ("audio[src]", "audio", "src"),
    ]
    for selector, media_type, attr in selectors:
        for node in soup.select(selector):
            raw = _safe_text(node.get(attr))
            if not raw:
                continue
            absolute = urljoin(base_url, raw)
            if absolute in seen:
                continue
            seen.add(absolute)
            label = _safe_text(node.get("alt") or node.get("title") or node.get_text(" ", strip=True)) or absolute
            media.append({"type": media_type, "label": label[:200], "url": absolute})
            if len(media) >= MAX_MEDIA:
                return media
    return media


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _site_profile_key(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.netloc or "unknown").lower()


def _site_path_key(url: str) -> str:
    parsed = urlparse(url)
    path = (parsed.path or "/").strip("/") or "root"
    return path.replace("/", ":")


def _selector_identifier_token(selector: str) -> str:
    normalized = selector.lower()
    return "".join(ch if ch.isalnum() else "_" for ch in normalized).strip("_") or "container"


def _load_web_fetch_profiles() -> dict[str, Any]:
    if hasattr(storage, "get_web_fetch_profiles"):
        return storage.get_web_fetch_profiles()
    data = storage.read_json("web_fetch_profiles.json")
    if not data:
        data = {"version": 1, "sites": {}}
    data.setdefault("version", 1)
    data.setdefault("sites", {})
    return data


def _save_web_fetch_profiles(data: dict[str, Any]) -> None:
    if hasattr(storage, "save_web_fetch_profiles"):
        storage.save_web_fetch_profiles(data)
        return
    payload = dict(data or {})
    payload.setdefault("version", 1)
    payload.setdefault("sites", {})
    storage.write_json("web_fetch_profiles.json", payload)


def _selector_score(entry: dict[str, Any]) -> int:
    direct_hits = int(entry.get("directHits") or 0)
    adaptive_hits = int(entry.get("adaptiveHits") or 0)
    misses = int(entry.get("misses") or 0)
    return direct_hits * 4 + adaptive_hits * 3 - misses


def _site_selector_candidates(url: str, extract: WebExtractMode) -> tuple[str, list[str], dict[str, dict[str, Any]]]:
    profile_key = _site_profile_key(url)
    profiles = _load_web_fetch_profiles()
    site_profile = ((profiles.get("sites") or {}).get(profile_key) or {})
    extract_profile = ((site_profile.get("extracts") or {}).get(extract) or {})
    selector_entries = dict(extract_profile.get("selectors") or {})
    ordered_profile_selectors = [
        selector
        for selector, _entry in sorted(
            selector_entries.items(),
            key=lambda item: (_selector_score(item[1]), int(item[1].get("successes") or 0), item[0]),
            reverse=True,
        )
    ]
    builtin_selectors = list(_builtin_container_selectors(url, extract))
    ordered_profile_selectors = [*builtin_selectors, *ordered_profile_selectors]
    defaults = [*EXTRACT_CONTAINER_SELECTORS.get(extract, ()), *DEFAULT_CONTAINER_SELECTORS]
    candidates: list[str] = []
    seen: set[str] = set()
    for selector in [*ordered_profile_selectors, *defaults]:
        normalized = _safe_text(selector)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
        if len(candidates) >= MAX_SELECTOR_CANDIDATES:
            break
    return profile_key, ordered_profile_selectors, selector_entries


def _selector_candidates_for_extract(url: str, extract: WebExtractMode) -> tuple[str, list[str], list[str], dict[str, dict[str, Any]]]:
    profile_key, profile_selectors, selector_entries = _site_selector_candidates(url, extract)
    defaults = [*EXTRACT_CONTAINER_SELECTORS.get(extract, ()), *DEFAULT_CONTAINER_SELECTORS]
    candidates: list[str] = []
    seen: set[str] = set()
    for selector in [*profile_selectors, *defaults]:
        normalized = _safe_text(selector)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
        if len(candidates) >= MAX_SELECTOR_CANDIDATES:
            break
    if "body" not in seen:
        if len(candidates) >= MAX_SELECTOR_CANDIDATES:
            candidates[-1] = "body"
        else:
            candidates.append("body")
    return profile_key, candidates, profile_selectors, selector_entries


def _record_selector_signal(
    *,
    url: str,
    extract: WebExtractMode,
    selector: str,
    source: str,
    direct_hit: bool,
    adaptive_hit: bool,
    success: bool,
    selected_tag: str,
) -> dict[str, Any]:
    profiles = _load_web_fetch_profiles()
    sites = profiles.setdefault("sites", {})
    profile_key = _site_profile_key(url)
    site_profile = sites.setdefault(profile_key, {"updatedAt": "", "extracts": {}})
    extracts = site_profile.setdefault("extracts", {})
    extract_profile = extracts.setdefault(extract, {"updatedAt": "", "selectors": {}})
    selectors = extract_profile.setdefault("selectors", {})
    entry = selectors.setdefault(
        selector,
        {
            "selector": selector,
            "firstSeenAt": _utc_now_iso(),
            "lastUsedAt": "",
            "directHits": 0,
            "adaptiveHits": 0,
            "successes": 0,
            "misses": 0,
            "lastSource": "",
            "lastSelectedTag": "",
        },
    )
    if success:
        entry["successes"] = int(entry.get("successes") or 0) + 1
    else:
        entry["misses"] = int(entry.get("misses") or 0) + 1
    if direct_hit:
        entry["directHits"] = int(entry.get("directHits") or 0) + 1
    if adaptive_hit:
        entry["adaptiveHits"] = int(entry.get("adaptiveHits") or 0) + 1
    entry["lastUsedAt"] = _utc_now_iso()
    entry["lastSource"] = source
    entry["lastSelectedTag"] = selected_tag
    site_profile["updatedAt"] = entry["lastUsedAt"]
    extract_profile["updatedAt"] = entry["lastUsedAt"]
    _save_web_fetch_profiles(profiles)
    return {
        "profileKey": profile_key,
        "profileUpdatedAt": extract_profile["updatedAt"],
        "selectorScore": _selector_score(entry),
        "selectorStats": {
            "directHits": entry["directHits"],
            "adaptiveHits": entry["adaptiveHits"],
            "successes": entry["successes"],
            "misses": entry["misses"],
        },
    }


def _adaptive_storage_file(url: str = "") -> str:
    override = _safe_text(get_web_fetch_config().get("adaptiveStorageFile"))
    if override:
        override_path = Path(override)
        override_path.parent.mkdir(parents=True, exist_ok=True)
        return str(override_path)
    profile_key = _site_profile_key(url) if url else "global"
    storage_dir = _web_fetch_cache_dir() / "adaptive"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return str(storage_dir / f"{profile_key}.db")


def _default_adaptive_id(url: str, extract: WebExtractMode) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "unknown").lower()
    path = (parsed.path or "/").strip("/") or "root"
    stable_path = path.replace("/", ":")
    return f"{host}:{stable_path}:{extract}:container"


def _build_adaptive_selector(page: WebPagePayload) -> Selector:
    return Selector(
        content=page.html,
        url=page.final_url or page.url,
        adaptive=True,
        storage=SQLiteStorageSystem,
        storage_args={
            "storage_file": _adaptive_storage_file(page.final_url or page.url),
            "url": page.final_url or page.url,
        },
    )


def _resolve_extract_container(
    page: WebPagePayload,
    *,
    extract: WebExtractMode,
    adaptive_enabled: bool,
    adaptive_id: str,
    adaptive_threshold: int,
) -> tuple[BeautifulSoup, dict[str, Any], dict[str, Any]]:
    resolved_url = page.final_url or page.url
    profile_key, selector_candidates, profile_selectors, selector_entries = _selector_candidates_for_extract(resolved_url, extract)
    soup = BeautifulSoup(page.html, "html.parser")
    fallback_container = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"}) or soup.body or soup

    if not adaptive_enabled:
        matched_selector = next((selector for selector in selector_candidates if soup.select_one(selector)), "")
        selected_selector = matched_selector or _safe_text(getattr(fallback_container, "name", "")) or WEB_CONTAINER_SELECTOR
        selected_node = soup.select_one(selected_selector) if matched_selector else fallback_container
        selector_source = (
            "site_profile" if selected_selector in profile_selectors else "default" if matched_selector else "fallback"
        )
        selector_meta = _record_selector_signal(
            url=resolved_url,
            extract=extract,
            selector=selected_selector,
            source=selector_source,
            direct_hit=selected_node is not None,
            adaptive_hit=False,
            success=selected_node is not None,
            selected_tag=_safe_text(getattr(selected_node, "name", "")),
        )
        return selected_node or fallback_container, {
            "adaptiveEnabled": False,
            "adaptiveId": "",
            "adaptiveThreshold": adaptive_threshold,
            "storageFile": "",
            "storagePresentBefore": False,
            "storagePresentAfter": False,
            "directSelectorMatched": False,
            "usedAdaptiveRecovery": False,
            "selector": WEB_CONTAINER_SELECTOR,
            "selectedNodeTag": _safe_text(getattr(selected_node, "name", "")) or _safe_text(getattr(fallback_container, "name", "")),
            "adaptiveFallback": False,
            "error": "",
        }, {
            "profileKey": profile_key,
            "selectorCandidates": selector_candidates,
            "profileSelectors": profile_selectors,
            "selectorChosen": selected_selector,
            "selectorSource": selector_source,
            "profileHit": selected_selector in profile_selectors,
            "profileSelectorCount": len(profile_selectors),
            "profileUpdatedAt": selector_meta["profileUpdatedAt"],
            "selectorScore": selector_meta["selectorScore"],
            "selectorStats": selector_meta["selectorStats"],
        }

    try:
        selector = _build_adaptive_selector(page)
        selected_html = page.html
        selected_node = None
        selected_selector = WEB_CONTAINER_SELECTOR
        selector_source = "fallback"
        profile_hit = False
        direct_selector_matched = False
        used_adaptive_recovery = False
        storage_present_before = False
        storage_present_after = False

        for candidate in selector_candidates:
            direct_node = soup.select_one(candidate)
            identifier = f"{adaptive_id}:{_selector_identifier_token(candidate)}"
            storage_present_before = storage_present_before or selector.retrieve(identifier) is not None
            if direct_node is not None:
                direct_selector_matched = True
                selector.css(
                    candidate,
                    identifier=identifier,
                    adaptive=True,
                    auto_save=True,
                    percentage=max(0, min(adaptive_threshold, 100)),
                )
                storage_present_after = storage_present_after or selector.retrieve(identifier) is not None
                selected_node = direct_node
                selected_html = str(direct_node)
                selected_selector = candidate
                selector_source = "site_profile" if candidate in profile_selectors else "default"
                profile_hit = candidate in profile_selectors
                break

        if selected_node is None:
            for candidate in selector_candidates:
                identifier = f"{adaptive_id}:{_selector_identifier_token(candidate)}"
                storage_present_before = storage_present_before or selector.retrieve(identifier) is not None
                adaptive_matches = selector.css(
                    candidate,
                    identifier=identifier,
                    adaptive=True,
                    auto_save=True,
                    percentage=max(0, min(adaptive_threshold, 100)),
                )
                storage_present_after = storage_present_after or selector.retrieve(identifier) is not None
                if not adaptive_matches:
                    continue
                selected_node = adaptive_matches[0]
                selected_html = selected_node.get()
                selected_selector = candidate
                selector_source = "site_profile" if candidate in profile_selectors else "default"
                profile_hit = candidate in profile_selectors
                used_adaptive_recovery = storage_present_before
                break

        if selected_node is None:
            fallback_selector = _safe_text(getattr(fallback_container, "name", "")) or "body"
            identifier = f"{adaptive_id}:{_selector_identifier_token(fallback_selector)}"
            storage_present_before = storage_present_before or selector.retrieve(identifier) is not None
            if fallback_selector in {"main", "article", "body"}:
                try:
                    selector.css(
                        fallback_selector,
                        identifier=identifier,
                        adaptive=True,
                        auto_save=True,
                        percentage=max(0, min(adaptive_threshold, 100)),
                    )
                    storage_present_after = storage_present_after or selector.retrieve(identifier) is not None
                except Exception:
                    pass
            selected_node = fallback_container
            selected_html = str(fallback_container)
            selected_selector = fallback_selector
            selector_source = "fallback"
            direct_selector_matched = bool(soup.select_one(fallback_selector))

        container = BeautifulSoup(str(selected_html), "html.parser") if selected_node is not None else fallback_container
        selected_tag = _safe_text(getattr(selected_node, "tag", "")) or _safe_text(getattr(selected_node, "name", "")) or _safe_text(getattr(container, "name", ""))
        selector_meta = _record_selector_signal(
            url=resolved_url,
            extract=extract,
            selector=selected_selector,
            source=selector_source,
            direct_hit=direct_selector_matched,
            adaptive_hit=used_adaptive_recovery,
            success=selected_node is not None,
            selected_tag=selected_tag,
        )
        return container, {
            "adaptiveEnabled": True,
            "adaptiveId": adaptive_id,
            "adaptiveThreshold": adaptive_threshold,
            "storageFile": _adaptive_storage_file(resolved_url),
            "storagePresentBefore": storage_present_before,
            "storagePresentAfter": storage_present_after,
            "directSelectorMatched": direct_selector_matched,
            "usedAdaptiveRecovery": used_adaptive_recovery,
            "selector": selected_selector,
            "selectedNodeTag": selected_tag,
            "adaptiveFallback": False,
            "error": "",
        }, {
            "profileKey": profile_key,
            "selectorCandidates": selector_candidates,
            "profileSelectors": profile_selectors,
            "selectorChosen": selected_selector,
            "selectorSource": selector_source,
            "profileHit": profile_hit,
            "profileSelectorCount": len(profile_selectors),
            "profileUpdatedAt": selector_meta["profileUpdatedAt"],
            "selectorScore": selector_meta["selectorScore"],
            "selectorStats": selector_meta["selectorStats"],
        }
    except Exception as exc:
        selector_meta = _record_selector_signal(
            url=resolved_url,
            extract=extract,
            selector=WEB_CONTAINER_SELECTOR,
            source="fallback",
            direct_hit=False,
            adaptive_hit=False,
            success=True,
            selected_tag=_safe_text(getattr(fallback_container, "name", "")),
        )
        return fallback_container, {
            "adaptiveEnabled": True,
            "adaptiveId": adaptive_id,
            "adaptiveThreshold": adaptive_threshold,
            "storageFile": _adaptive_storage_file(resolved_url),
            "storagePresentBefore": False,
            "storagePresentAfter": False,
            "directSelectorMatched": False,
            "usedAdaptiveRecovery": False,
            "selector": WEB_CONTAINER_SELECTOR,
            "selectedNodeTag": _safe_text(getattr(fallback_container, "name", "")),
            "adaptiveFallback": True,
            "error": str(exc),
        }, {
            "profileKey": profile_key,
            "selectorCandidates": selector_candidates,
            "profileSelectors": profile_selectors,
            "selectorChosen": WEB_CONTAINER_SELECTOR,
            "selectorSource": "fallback",
            "profileHit": False,
            "profileSelectorCount": len(profile_selectors),
            "profileUpdatedAt": selector_meta["profileUpdatedAt"],
            "selectorScore": selector_meta["selectorScore"],
            "selectorStats": selector_meta["selectorStats"],
        }


def _build_analysis_hints(page: WebPagePayload) -> list[str]:
    hints: list[str] = []
    if page.media:
        hints.append("页面包含图片或媒体资源；如果需要理解视觉内容，优先把媒体 URL 或截图交给 vision_media_analyzer。")
    if len(page.text.strip()) < 300 and len(page.media) >= 2:
        hints.append("该页面正文较少但媒体较多，可能更适合走视觉分析而不是纯文本抽取。")
    if page.warnings:
        hints.append("当前抓取存在降级或环境告警，必要时可改用 dynamic/stealth 或交给浏览器自动化链路。")
    if page.agent_browser_profile_used:
        hints.append("本次读取使用了 Agent 专用浏览器 profile；结果可能包含该 profile 的登录态视角。")
    return hints


def _detect_login_wall(page: WebPagePayload) -> dict[str, Any] | None:
    final_url = _safe_text(page.final_url or page.url).lower()
    title = _safe_text(page.title).lower()
    text = _safe_text(page.text).lower()
    haystack = " ".join([final_url, title, text[:2000]])
    status = int(page.status or 0)
    if status in {401, 407}:
        return {"failureClass": "needs_login", "reason": f"http_status_{status}"}
    login_needles = (
        "/login",
        "/signin",
        "/sign-in",
        "login required",
        "sign in to continue",
        "please sign in",
        "log in to continue",
        "需要登录",
        "请登录",
        "登录后",
        "账号登录",
        "登录 / 注册",
    )
    if any(needle in haystack for needle in login_needles):
        return {"failureClass": "needs_login", "reason": "login_wall_detected"}
    return None


def _guess_remote_mime(url: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(urlparse(url).path)
    return guessed or fallback


def _build_vision_candidates(page: WebPagePayload, *, limit: int = 6) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append_candidate(*, url: str, label: str, media_type: str, source: str):
        normalized = _safe_text(url)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        mime_type = _guess_remote_mime(normalized)
        kind = media_type or ("image" if mime_type.startswith("image/") else "video" if mime_type.startswith("video/") else "file")
        candidates.append(
            {
                "sourceUrl": normalized,
                "mimeTypeHint": mime_type,
                "kind": kind,
                "label": _safe_text(label)[:200] or normalized,
                "source": source,
                "promptSuggestion": (
                    "提取其中的文字、界面结构和关键视觉元素。"
                    if kind == "image"
                    else "总结媒体里的关键内容、文字和视觉变化。"
                ),
            }
        )

    for item in page.media:
        _append_candidate(
            url=item.get("url") or "",
            label=item.get("label") or item.get("url") or "",
            media_type=item.get("type") or "",
            source="page_media",
        )
        if len(candidates) >= limit:
            return candidates

    for meta_key in ("og:image", "twitter:image", "og:video", "twitter:player:stream"):
        meta_url = _safe_text(page.metadata.get(meta_key))
        if not meta_url:
            continue
        _append_candidate(
            url=urljoin(page.final_url or page.url, meta_url),
            label=meta_key,
            media_type="image" if "image" in meta_key else "video",
            source="page_metadata",
        )
        if len(candidates) >= limit:
            break

    return candidates


def _render_page_summary(page: WebPagePayload) -> dict[str, Any]:
    vision_candidates = _build_vision_candidates(page)
    text = page.text
    if not text and page.html:
        try:
            text = _extract_main_text(BeautifulSoup(page.html, "html.parser"), page.final_url or page.url)
        except Exception:
            text = page.text
    result = {
        "ok": True,
        "url": page.url,
        "finalUrl": page.final_url,
        "requestedMode": page.requested_mode,
        "refererMode": page.referer_mode,
        "refererUrl": page.referer_url,
        "status": page.status,
        "fetchMode": page.fetch_mode,
        "tlsStrategy": page.tls_strategy,
        "caBundlePath": page.ca_bundle_path,
        "proxyBypassUsed": page.proxy_bypass_used,
        "attemptedModes": page.attempted_modes,
        "availableModes": page.available_modes,
        "fallbackUsed": page.requested_mode == "auto" and page.fetch_mode != "static",
        "warnings": page.warnings,
        "title": page.title,
        "text": text,
        "metadata": page.metadata,
        "links": page.links,
        "media": page.media,
        "analysisHints": _build_analysis_hints(page),
        "visionCandidates": vision_candidates,
        "visionRecommended": bool(vision_candidates),
        "agentBrowserProfile": (
            {
                "used": True,
                "matchedHost": page.agent_browser_profile_host,
                "profile": agent_browser_profile_summary(page.agent_browser_kind or "auto", include_security_note=False),
            }
            if page.agent_browser_profile_used
            else {"used": False}
        ),
    }
    result.update(_page_quality_fields(page, text=text, html=page.html, mode="read"))
    result.update(_web_read_source_fields(page.final_url or page.url, used_browser_profile=page.agent_browser_profile_used))
    return result


def _render_error_payload(
    *,
    url: str,
    requested_mode: str,
    referer_mode: str,
    referer_url: str,
    error: str,
    blocked: bool = False,
    failure_class: str = "",
    attempted_modes: list[str] | None = None,
    elapsed_ms: int | None = None,
    retryable: bool | None = None,
) -> str:
    normalized_error = _safe_text(error)
    if not failure_class:
        failure_class = _classify_web_fetch_failure(normalized_error, blocked=blocked)
    return json.dumps(
        {
            "ok": False,
            "blocked": blocked,
            "url": url,
            "requestedMode": requested_mode,
            "refererMode": referer_mode,
            "refererUrl": referer_url,
            "availableModes": _dependency_status(),
            "failureClass": failure_class,
            "attemptedModes": attempted_modes or [],
            "elapsedMs": elapsed_ms,
            "retryable": bool(retryable) if retryable is not None else failure_class in {"network_timeout", "web_fetch_failed"},
            "recommendedNextAction": (
                "该 URL 当前不可达；不要等待 watchdog。请换可访问来源、改用 research_broker 多源调研，或把失败源标记为 unavailable。"
                if failure_class == "network_timeout"
                else "目标可能需要登录。请在 Admin / 深度调研打开 Agent 浏览器完成登录；Admin 开启 Agent profile 且目标域名命中 allowlist 后，web/research 的浏览器读取路径会自动复用该登录态。"
                if failure_class == "needs_login"
                else "Agent 浏览器尚未打开。请在 Admin / 深度调研打开 Agent 浏览器并完成登录后重试。"
                if failure_class == "agent_browser_not_open"
                else "Agent 浏览器调试端口被其他浏览器占用。请关闭该调试浏览器或更换端口后重新打开 Agent 浏览器；V8OS 不会读取用户日常 profile。"
                if failure_class == "agent_browser_profile_mismatch"
                else "Agent 浏览器 profile 未启用或目标域名未命中 allowlist；请在 Admin / System Base 配置 useAgentBrowserProfile 与 allowlist，或改用无登录公开来源。"
                if failure_class == "agent_browser_profile_not_allowed"
                else "根据 failureClass 决定换源、缩小请求或停止该工具链。"
            ),
            "error": normalized_error,
        },
        ensure_ascii=False,
        indent=2,
    )


def _render_needs_login_payload(*, page: WebPagePayload, use_agent_browser_profile: bool) -> str:
    login_wall = _detect_login_wall(page) or {"failureClass": "needs_login", "reason": "login_required"}
    text_preview = "" if page.agent_browser_profile_used else _safe_text(page.text)[:500]
    return json.dumps(
        {
            "ok": False,
            "failureClass": "needs_login",
            "reason": login_wall.get("reason"),
            "url": page.url,
            "finalUrl": page.final_url,
            "title": page.title,
            "status": page.status,
            "fetchMode": page.fetch_mode,
            "attemptedModes": page.attempted_modes,
            "useAgentBrowserProfile": bool(use_agent_browser_profile),
            "agentBrowserProfile": (
                {
                    "used": True,
                    "matchedHost": page.agent_browser_profile_host,
                    "profile": agent_browser_profile_summary(page.agent_browser_kind or "auto", include_security_note=False),
                }
                if page.agent_browser_profile_used
                else {"used": False}
            ),
            "retryable": True,
            "recommendedNextAction": "请在 Admin / 深度调研点击“打开 Agent 浏览器”并手动登录目标网站；登录后确认 systemBase.webFetch.useAgentBrowserProfile 已开启且目标域名在 agentBrowserProfileAllowlist 中，web/research 会通过同一 CDP 会话复用登录态。",
            "textPreview": text_preview,
        },
        ensure_ascii=False,
        indent=2,
    )


def _error_attempted_modes(error: str) -> list[str]:
    match = re.search(r"attempted=\[([^\]]*)\]", str(error or ""))
    if not match:
        return []
    return [
        item.strip().strip("'\"")
        for item in match.group(1).split(",")
        if item.strip().strip("'\"")
    ]


def _error_elapsed_ms(error: str) -> int | None:
    match = re.search(r"elapsed=([0-9.]+)s", str(error or ""))
    if not match:
        return None
    try:
        return int(float(match.group(1)) * 1000)
    except Exception:
        return None


def _trim_broker_text(value: Any, *, limit: int = 2400) -> tuple[str, bool]:
    normalized = _safe_text(value)
    if len(normalized) <= limit:
        return normalized, False
    return normalized[:limit].rstrip(), True


_WEB_SOURCE_CATALOG_PATH = Path(__file__).resolve().parents[2] / "runtimes" / "research" / "assets" / "source_quality_catalog.json"


@lru_cache(maxsize=1)
def _web_source_catalog() -> dict[str, Any]:
    try:
        payload = json.loads(_WEB_SOURCE_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {"entries": []}
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    return {"entries": [entry for entry in entries if isinstance(entry, dict)]}


def _web_source_catalog_match(url: str) -> dict[str, Any] | None:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None
    for entry in _web_source_catalog().get("entries") or []:
        for raw_host in list(entry.get("hosts") or []):
            catalog_host = _safe_text(raw_host).lower()
            if catalog_host and (host == catalog_host or host.endswith(f".{catalog_host}")):
                return entry
    return None


def _search_result_quality_hints(url: str) -> dict[str, Any]:
    host = (urlparse(url).hostname or "").lower()
    catalog_entry = _web_source_catalog_match(url)
    authoritative = any(
        hint in host or hint in str(url or "").lower()
        for hint in (
            "docs.",
            "developer.",
            "developers.",
            "platform.",
            "api.",
            "learn.microsoft.com",
            "cloud.google.com",
            "docs.aws.amazon.com",
            "github.com",
        )
    )
    catalog_tier = _safe_text(catalog_entry.get("authorityTier") if catalog_entry else "").lower()
    catalog_category = _safe_text(catalog_entry.get("category") if catalog_entry else "")
    background = host in {
        "baike.baidu.com",
        "wikipedia.org",
        "www.wikipedia.org",
    } or catalog_tier == "background"
    low_quality = any(
        hint in host or hint in str(url or "").lower()
        for hint in (
            "pinterest.",
            "quora.",
            "reddit.",
            "medium.",
            "zhihu.",
            "juejin.",
            "csdn.",
            "cnblogs.",
        )
    )
    score = 70 if authoritative else (55 if background else 50)
    if catalog_entry:
        score += _as_int(catalog_entry.get("authorityBoost"), 0)
        if catalog_tier == "primary":
            score = max(score, 80)
        elif catalog_tier == "secondary":
            score = max(score, 60)
        elif catalog_tier == "popularity":
            score = max(score, 55)
    if low_quality:
        score -= 25
    score = max(0, min(score, 100))
    signals = []
    if catalog_entry:
        signals.append(f"source_catalog:{catalog_entry.get('id')}")
        if catalog_category:
            signals.append(f"source_category:{catalog_category}")
    if authoritative:
        signals.append("authoritative_host_hint")
    if catalog_tier == "primary":
        signals.append("primary_source_hint")
    elif catalog_tier == "secondary":
        signals.append("secondary_source_hint")
    if background:
        signals.append("encyclopedic_background_source")
    if low_quality:
        signals.append("low_quality_host_hint")
    return {
        "host": host,
        "authorityScore": score,
        "tier": "primary" if score >= 70 else ("secondary" if score >= 45 else "weak"),
        "signals": signals,
        "catalogSourceId": catalog_entry.get("id") if catalog_entry else None,
        "catalogCategory": catalog_category or None,
        "authorityTier": catalog_entry.get("authorityTier") if catalog_entry else None,
    }


def _search_relevance_score(query: str, result: dict[str, Any]) -> int:
    haystack = " ".join(
        _safe_text(result.get(key)).lower()
        for key in ("title", "snippet", "url")
    )
    query_text = _safe_text(query).lower()
    if not query_text or not haystack:
        return 0
    latin_terms = [term for term in re.split(r"[^a-z0-9]+", query_text) if len(term) >= 3]
    cjk_terms = re.findall(r"[\u4e00-\u9fff]{2,}", query_text)
    terms = latin_terms + cjk_terms
    if not terms:
        return 0
    hits = sum(1 for term in terms if term in haystack)
    return int(round((hits / max(len(terms), 1)) * 100))


def _compact_web_broker_payload(payload: dict[str, Any], *, requested_mode: str, debug: bool) -> dict[str, Any]:
    ok = bool(payload.get("ok"))
    resolved_mode = requested_mode
    if requested_mode == "fetch":
        if "query" in payload or "results" in payload:
            resolved_mode = "search"
        elif "extract" in payload:
            resolved_mode = "extract"
        else:
            resolved_mode = "read"

    compact: dict[str, Any] = {
        "ok": payload.get("ok"),
        "mode": resolved_mode,
    }
    debug_payload: dict[str, Any] = {}

    if ok:
        if resolved_mode == "search":
            query = _safe_text(payload.get("query"))
            provider = _safe_text(payload.get("provider"))
            results = payload.get("results") if isinstance(payload.get("results"), list) else []
            ranked_results = []
            relevance_scores = []
            authority_scores = []
            for index, result in enumerate(results, start=1):
                item = dict(result or {})
                url = _safe_text(item.get("url"))
                item["resultRank"] = index
                item["finalUrl"] = item.get("finalUrl") or url
                if url:
                    item["sourceQualityHints"] = _search_result_quality_hints(url)
                    authority_scores.append(int(item["sourceQualityHints"].get("authorityScore") or 0))
                relevance = _search_relevance_score(query, item)
                item["relevanceScore"] = relevance
                relevance_scores.append(relevance)
                ranked_results.append(item)
            average_relevance = int(round(sum(relevance_scores) / len(relevance_scores))) if relevance_scores else 0
            average_authority = int(round(sum(authority_scores) / len(authority_scores))) if authority_scores else 0
            quality = "weak" if not results or average_relevance < 20 or average_authority < 35 else "usable"
            compact.update(
                {
                    "summary": f"搜索到 {len(results)} 条结果。" if results else "没有找到可用结果。",
                    "query": query,
                    "provider": provider or None,
                    "searchVertical": payload.get("searchVertical") or None,
                    "resultCount": payload.get("resultCount") if payload.get("resultCount") is not None else len(results),
                    "quality": quality,
                    "sourceQualitySummary": {
                        "averageRelevance": average_relevance,
                        "averageAuthority": average_authority,
                        "recommendedNextAction": (
                            "结果相关性较弱；请换关键词、限定官方/权威域名，或改用 research_broker 汇总多源证据。"
                            if quality == "weak"
                            else "可作为单次搜索线索；复杂事实仍建议用 research_broker。"
                        ),
                    },
                    "results": ranked_results,
                    "omitted": {
                        "fullSearchHtml": "omitted",
                        "sourceRanking": "heuristic; use research_broker for multi-source confidence and evidence bundles",
                    },
                }
            )
        else:
            final_url = payload.get("finalUrl") or payload.get("url")
            title = _safe_text(payload.get("title"))
            text = payload.get("text")
            text_preview, text_truncated = _trim_broker_text(text, limit=2200) if text not in (None, "") else ("", False)
            compact.update(
                {
                    "summary": title or ("网页提取完成。" if resolved_mode == "extract" else "网页读取完成。"),
                    "url": payload.get("url"),
                    "finalUrl": final_url,
                    "title": title or None,
                }
            )
            if text_preview:
                if text_truncated:
                    compact["textPreview"] = text_preview
                    compact["textTruncated"] = True
                else:
                    compact["text"] = text_preview
            if resolved_mode == "extract":
                compact["extract"] = payload.get("extract")
                if "links" in payload:
                    compact["links"] = payload.get("links")
                if "media" in payload:
                    compact["media"] = payload.get("media")
                if "metadata" in payload:
                    compact["metadata"] = payload.get("metadata")
                if "rawHtml" in payload:
                    raw_preview, raw_truncated = _trim_broker_text(payload.get("rawHtml"), limit=3200)
                    compact["rawHtmlPreview"] = raw_preview
                    if raw_truncated or payload.get("rawHtmlTruncated"):
                        compact["rawHtmlTruncated"] = True
                if "uiSnapshot" in payload:
                    compact["uiSnapshot"] = payload.get("uiSnapshot")
            else:
                if "links" in payload:
                    compact["links"] = payload.get("links")
                if "media" in payload:
                    compact["media"] = payload.get("media")
        for key in ("extractionQuality", "contentChars", "htmlChars", "missingContentReason", "contentFormat", "usedBrowserProfile"):
            if payload.get(key) not in (None, "", [], {}):
                compact[key] = payload.get(key)
        analysis_hints = payload.get("analysisHints")
        if analysis_hints not in (None, "", [], {}):
            compact["analysisHints"] = analysis_hints
        vision_candidates = payload.get("visionCandidates")
        if vision_candidates not in (None, "", [], {}):
            compact["visionCandidates"] = vision_candidates
        warnings = payload.get("warnings")
        if isinstance(warnings, list) and warnings:
            compact["warnings"] = warnings
        if isinstance(payload.get("agentBrowserProfile"), dict) and payload["agentBrowserProfile"].get("used"):
            compact["agentBrowserProfile"] = payload.get("agentBrowserProfile")
    else:
        failure_class = str(payload.get("failureClass") or "").strip()
        if failure_class == "needs_login":
            summary = "目标页面需要登录；请在 Admin 打开 Agent 专用浏览器完成登录后重试。"
        elif failure_class == "agent_browser_profile_not_allowed":
            summary = "Agent 浏览器 profile 未启用或目标域名未命中 allowlist。"
        elif failure_class == "network_timeout":
            summary = "目标网络请求超时；请换源或稍后重试。"
        else:
            summary = _safe_text(payload.get("error")) or "Web broker 执行失败。"
        compact.update(
            {
                "summary": summary,
                "error": payload.get("error"),
                "failureClass": payload.get("failureClass"),
                "attemptedModes": payload.get("attemptedModes"),
                "elapsedMs": payload.get("elapsedMs"),
                "retryable": payload.get("retryable"),
                "recommendedNextAction": payload.get("recommendedNextAction"),
            }
        )
        if payload.get("blocked") is not None:
            compact["blocked"] = payload.get("blocked")
        if payload.get("url") not in (None, ""):
            compact["url"] = payload.get("url")
        if payload.get("query") not in (None, ""):
            compact["query"] = payload.get("query")
        if payload.get("attemptedProviders") not in (None, "", [], {}):
            compact["attemptedProviders"] = payload.get("attemptedProviders")
        if isinstance(payload.get("agentBrowserProfile"), dict):
            compact["agentBrowserProfile"] = payload.get("agentBrowserProfile")

    for key in (
        "requestedMode",
        "refererMode",
        "refererUrl",
        "fetchMode",
        "tlsStrategy",
        "caBundlePath",
        "proxyBypassUsed",
        "attemptedModes",
        "availableModes",
        "adaptiveSignals",
        "selectorSignals",
        "requestedProvider",
        "attemptedProviders",
        "searchUrl",
        "sourceCapability",
        "providerAttemptMatrix",
        "networkRoute",
        "sourceRouter",
        "status",
        "fallbackUsed",
        "visionRecommended",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            debug_payload[key] = value

    if debug and debug_payload:
        compact["debug"] = debug_payload

    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def _looks_like_url(value: str) -> bool:
    normalized = _safe_text(value).lower()
    return normalized.startswith("http://") or normalized.startswith("https://")


def _extract_search_results(soup: BeautifulSoup, *, provider: str, limit: int) -> list[dict[str, str]]:
    selectors = {
        "bing": [
            ("li.b_algo", "h2 a", ".b_caption p"),
        ],
        "google": [
            ("div.g", "a", ".VwiC3b, .yXK7lf, .MUxGbd"),
        ],
        "baidu": [
            ("div.result, div.c-container, div.result-op", "h3 a", ".c-abstract, .content-right_8Zs40, .c-span-last"),
        ],
        "duckduckgo": [
            (".result", ".result__a", ".result__snippet"),
        ],
    }.get(provider, [])
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for node_selector, anchor_selector, snippet_selector in selectors:
        for result_node in soup.select(node_selector):
            anchor = result_node.select_one(anchor_selector) or result_node.select_one("a[href]")
            if not anchor:
                continue
            href = _safe_text(anchor.get("href"))
            title = _safe_text(anchor.get_text(" ", strip=True))
            if not href or href in seen:
                continue
            seen.add(href)
            snippet_node = result_node.select_one(snippet_selector) if snippet_selector else None
            snippet = _safe_text(snippet_node.get_text(" ", strip=True) if snippet_node else "")
            results.append({"title": title[:300], "url": href, "snippet": snippet[:600]})
            if len(results) >= max(1, min(limit, 10)):
                return results
        if results:
            return results
    return results


def _searxng_search_public(search_url: str, *, limit: int, timeout_seconds: float) -> dict[str, Any]:
    try:
        with _bypass_proxy_env(_should_bypass_proxy_env()):
            response = requests.get(
                search_url,
                headers={"User-Agent": "V8 Agent OS Source Router/1.0"},
                timeout=max(1.0, timeout_seconds),
            )
    except Exception as exc:
        return {
            "ok": False,
            "failureClass": _classify_web_fetch_failure(str(exc)),
            "reason": str(exc)[:1000],
            "retryable": True,
        }
    content_type = _safe_text(response.headers.get("content-type")).lower()
    if response.status_code in {403, 429}:
        return {
            "ok": False,
            "failureClass": "provider_challenge",
            "reason": f"http_status_{response.status_code}",
            "retryable": True,
        }
    try:
        payload = response.json()
    except Exception:
        return {
            "ok": False,
            "failureClass": "provider_format_unavailable",
            "reason": f"searxng_json_format_unavailable content_type={content_type or 'unknown'}",
            "retryable": False,
        }
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return {
            "ok": False,
            "failureClass": "provider_format_unavailable",
            "reason": "searxng_response_missing_results_array",
            "retryable": False,
        }
    results: list[dict[str, str]] = []
    for index, item in enumerate(raw_results[: max(1, min(limit, 10))], start=1):
        if not isinstance(item, dict):
            continue
        url = _safe_text(item.get("url"))
        title = _safe_text(item.get("title") or item.get("content") or url)
        snippet = _safe_text(item.get("content") or item.get("snippet"))
        if not url:
            continue
        results.append(
            {
                "title": title[:300],
                "url": url,
                "snippet": snippet[:700],
                "source": _safe_text(item.get("engine"))[:120],
                "rank": str(index),
            }
        )
    return {"ok": True, "results": results, "statusCode": response.status_code}


def _provider_api_failure(provider: str, response: requests.Response) -> dict[str, Any] | None:
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code < 400:
        return None
    if status_code in {401, 403}:
        failure_class = "provider_auth_failed"
        retryable = False
    elif status_code == 429:
        failure_class = "provider_rate_limited"
        retryable = True
    elif status_code >= 500:
        failure_class = "provider_server_error"
        retryable = True
    else:
        failure_class = "provider_http_error"
        retryable = False
    return {
        "ok": False,
        "failureClass": failure_class,
        "reason": f"{provider}_http_status_{status_code}",
        "retryable": retryable,
        "statusCode": status_code,
    }


def _brave_search_public(query: str, *, limit: int, timeout_seconds: float) -> dict[str, Any]:
    api_key = _provider_api_key("brave")
    try:
        with _bypass_proxy_env(_should_bypass_proxy_env()):
            response = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={
                    "q": query,
                    "count": max(1, min(limit, 20)),
                    "safesearch": "moderate",
                    "spellcheck": "1",
                },
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                    "User-Agent": "V8 Agent OS Source Router/1.0",
                },
                timeout=max(1.0, timeout_seconds),
            )
    except Exception as exc:
        return {
            "ok": False,
            "failureClass": _classify_web_fetch_failure(str(exc)),
            "reason": str(exc)[:1000],
            "retryable": True,
        }
    api_failure = _provider_api_failure("brave", response)
    if api_failure:
        return api_failure
    try:
        payload = response.json()
    except Exception:
        return {
            "ok": False,
            "failureClass": "provider_format_unavailable",
            "reason": "brave_response_not_json",
            "retryable": False,
            "statusCode": response.status_code,
        }
    raw_results = ((payload.get("web") or {}).get("results") if isinstance(payload, dict) else None) or []
    results: list[dict[str, str]] = []
    for index, item in enumerate(raw_results[: max(1, min(limit, 20))], start=1):
        if not isinstance(item, dict):
            continue
        url = _safe_text(item.get("url"))
        title = _safe_text(item.get("title") or url)
        snippet = _safe_text(item.get("description") or item.get("extra_snippets") or item.get("page_age"))
        if not url:
            continue
        results.append(
            {
                "title": title[:300],
                "url": url,
                "snippet": snippet[:700],
                "source": "brave",
                "rank": str(index),
            }
        )
    return {"ok": True, "results": results, "statusCode": response.status_code}


def _tavily_search_public(query: str, *, limit: int, timeout_seconds: float) -> dict[str, Any]:
    api_key = _provider_api_key("tavily")
    request_payload = {
        "query": query,
        "search_depth": "basic",
        "max_results": max(1, min(limit, 10)),
        "include_answer": False,
        "include_raw_content": "markdown",
    }
    try:
        with _bypass_proxy_env(_should_bypass_proxy_env()):
            response = requests.post(
                "https://api.tavily.com/search",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "V8 Agent OS Source Router/1.0",
                },
                json=request_payload,
                timeout=max(1.0, timeout_seconds),
            )
    except Exception as exc:
        return {
            "ok": False,
            "failureClass": _classify_web_fetch_failure(str(exc)),
            "reason": str(exc)[:1000],
            "retryable": True,
        }
    api_failure = _provider_api_failure("tavily", response)
    if api_failure:
        return api_failure
    try:
        payload = response.json()
    except Exception:
        return {
            "ok": False,
            "failureClass": "provider_format_unavailable",
            "reason": "tavily_response_not_json",
            "retryable": False,
            "statusCode": response.status_code,
        }
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return {
            "ok": False,
            "failureClass": "provider_format_unavailable",
            "reason": "tavily_response_missing_results_array",
            "retryable": False,
            "statusCode": response.status_code,
        }
    results: list[dict[str, str]] = []
    for index, item in enumerate(raw_results[: max(1, min(limit, 10))], start=1):
        if not isinstance(item, dict):
            continue
        url = _safe_text(item.get("url"))
        title = _safe_text(item.get("title") or url)
        snippet = _safe_text(item.get("content") or item.get("raw_content") or "")
        if not url:
            continue
        results.append(
            {
                "title": title[:300],
                "url": url,
                "snippet": snippet[:900],
                "source": "tavily",
                "score": _safe_text(item.get("score")),
                "rank": str(index),
            }
        )
    return {"ok": True, "results": results, "statusCode": response.status_code}


def _exa_search_public(query: str, *, limit: int, timeout_seconds: float) -> dict[str, Any]:
    api_key = _provider_api_key("exa")
    try:
        with _bypass_proxy_env(_should_bypass_proxy_env()):
            response = requests.post(
                "https://api.exa.ai/search",
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                    "User-Agent": "V8 Agent OS Source Router/1.0",
                },
                json={
                    "query": query,
                    "numResults": max(1, min(limit, 10)),
                    "contents": {"text": {"maxCharacters": 900}},
                },
                timeout=max(1.0, timeout_seconds),
            )
    except Exception as exc:
        return {
            "ok": False,
            "failureClass": _classify_web_fetch_failure(str(exc)),
            "reason": str(exc)[:1000],
            "retryable": True,
        }
    api_failure = _provider_api_failure("exa", response)
    if api_failure:
        return api_failure
    try:
        payload = response.json()
    except Exception:
        return {
            "ok": False,
            "failureClass": "provider_format_unavailable",
            "reason": "exa_response_not_json",
            "retryable": False,
            "statusCode": response.status_code,
        }
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return {
            "ok": False,
            "failureClass": "provider_format_unavailable",
            "reason": "exa_response_missing_results_array",
            "retryable": False,
            "statusCode": response.status_code,
        }
    results: list[dict[str, str]] = []
    for index, item in enumerate(raw_results[: max(1, min(limit, 10))], start=1):
        if not isinstance(item, dict):
            continue
        url = _safe_text(item.get("url"))
        title = _safe_text(item.get("title") or url)
        snippet = _safe_text(item.get("text") or item.get("summary") or item.get("highlights") or "")
        if not url:
            continue
        results.append(
            {
                "title": title[:300],
                "url": url,
                "snippet": snippet[:900],
                "source": "exa",
                "score": _safe_text(item.get("score")),
                "rank": str(index),
            }
        )
    return {"ok": True, "results": results, "statusCode": response.status_code}


def _api_search_public(provider: str, query: str, *, limit: int, timeout_seconds: float) -> dict[str, Any]:
    if provider == "brave":
        return _brave_search_public(query, limit=limit, timeout_seconds=timeout_seconds)
    if provider == "tavily":
        return _tavily_search_public(query, limit=limit, timeout_seconds=timeout_seconds)
    if provider == "exa":
        return _exa_search_public(query, limit=limit, timeout_seconds=timeout_seconds)
    return {
        "ok": False,
        "failureClass": "provider_adapter_unavailable",
        "reason": f"{provider}_api_adapter_unavailable",
        "retryable": False,
    }


def _metaso_api_search(query: str, *, limit: int, vertical: str, timeout_seconds: float) -> dict[str, Any]:
    api_key = _provider_api_key("metaso")
    if not api_key:
        return {
            "ok": False,
            "failureClass": "credential_missing",
            "reason": "missing_METASO_API_KEY",
            "retryable": False,
        }
    scope = METASO_API_SCOPES.get(_normalize_search_vertical(vertical), "webpage")
    size = max(1, min(int(limit or 5), 20))
    try:
        response = requests.post(
            METASO_API_SEARCH_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "q": query,
                "scope": scope,
                "size": str(size),
                "includeSummary": False,
                "includeRawContent": False,
                "conciseSnippet": False,
            },
            timeout=max(1.0, timeout_seconds),
        )
    except Exception as exc:
        return {
            "ok": False,
            "failureClass": _classify_web_fetch_failure(str(exc)),
            "reason": str(exc)[:1000],
            "retryable": True,
        }
    api_failure = _provider_api_failure("metaso", response)
    if api_failure:
        return api_failure
    try:
        payload = response.json()
    except Exception:
        return {
            "ok": False,
            "failureClass": "provider_format_unavailable",
            "reason": "metaso_api_response_not_json",
            "retryable": False,
            "statusCode": response.status_code,
        }
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in ("webPages", "webpages", "results", "data", "items", "documents"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
            if isinstance(value, dict):
                nested = value.get("value") or value.get("items") or value.get("results") or value.get("webPages")
                if isinstance(nested, list):
                    candidates = nested
                    break
    if not isinstance(candidates, list):
        return {
            "ok": False,
            "failureClass": "provider_format_unavailable",
            "reason": "metaso_api_response_missing_results_array",
            "retryable": False,
            "statusCode": response.status_code,
        }
    results: list[dict[str, str]] = []
    for index, item in enumerate(candidates[:size], start=1):
        if not isinstance(item, dict):
            continue
        normalized = _metaso_result_from_item(item, rank=index, vertical=scope)
        if normalized.get("url") or normalized.get("title"):
            normalized.setdefault("source", "metaso")
            normalized["rank"] = str(index)
            results.append(normalized)
    return {
        "ok": True,
        "results": results,
        "statusCode": response.status_code,
        "scope": scope,
        "apiEndpoint": METASO_API_SEARCH_ENDPOINT,
    }


def _search_page_failure(payload: WebPagePayload, soup: BeautifulSoup, *, provider: str, result_count: int) -> dict[str, Any] | None:
    if result_count > 0:
        return None
    final_url = _safe_text(payload.final_url or payload.url).lower()
    title = _safe_text(payload.title).lower()
    text_preview = _safe_text(soup.get_text(" ", strip=True))[:1000].lower()
    if int(payload.status or 0) in {403, 429}:
        return {"status": "challenge", "failureClass": "provider_challenge", "reason": f"http_status_{payload.status}"}
    if provider == "google" and (
        "/sorry/" in final_url
        or "/httpservice/retry/enablejs" in text_preview
        or "please click here if you are not redirected" in text_preview
    ):
        return {"status": "challenge", "failureClass": "provider_challenge", "reason": "google_requires_js_or_captcha"}
    if provider == "baidu" and (
        "wappass.baidu.com" in final_url
        or "百度安全验证" in title
        or "网络不给力，请稍后重试" in text_preview
    ):
        return {"status": "challenge", "failureClass": "provider_challenge", "reason": "baidu_safety_verification"}
    return {"status": "empty", "failureClass": "no_results", "reason": "no_search_results_extracted"}


def _normalize_search_vertical(value: str) -> str:
    normalized = _safe_text(value).lower().replace("-", "_")
    aliases = {
        "": "all",
        "auto": "all",
        "default": "all",
        "metaso": "all",
        "all": "all",
        "web": "web",
        "网页": "web",
        "全网": "all",
        "document": "document",
        "documents": "document",
        "doc": "document",
        "pdf": "document",
        "library": "document",
        "文库": "document",
        "academic": "academic",
        "scholar": "academic",
        "paper": "academic",
        "papers": "academic",
        "学术": "academic",
        "image": "image",
        "images": "image",
        "图片": "image",
        "video": "video",
        "videos": "video",
        "视频": "video",
        "podcast": "podcast",
        "podcasts": "podcast",
        "播客": "podcast",
    }
    return aliases.get(normalized, normalized if normalized in METASO_VERTICAL_ENGINE_TYPES else "all")


def _metaso_extract_token(html: str) -> str:
    match = re.search(r'<meta[^>]+id=["\']meta-token["\'][^>]+content=["\']([^"\']+)["\']', html)
    return match.group(1) if match else ""


def _metaso_result_from_item(item: dict[str, Any], *, rank: int, vertical: str) -> dict[str, str]:
    url = _safe_text(item.get("link") or item.get("url") or item.get("origin_url") or item.get("previewUrl"))
    title = _safe_text(item.get("title") or item.get("orig_title") or item.get("orig_o_title") or item.get("displaySource"))
    snippet = _safe_text(
        item.get("matched_snippet")
        or item.get("snippet")
        or item.get("abstract")
        or item.get("caption")
        or item.get("export")
    )
    source = _safe_text(item.get("displaySource") or item.get("source") or item.get("institution"))
    date = _safe_text(item.get("date") or item.get("publish_date_str") or item.get("publish_date"))
    result: dict[str, str] = {
        "title": title[:300],
        "url": url,
        "snippet": snippet[:700],
        "source": source[:160],
        "date": date[:80],
        "vertical": vertical,
        "rank": str(rank),
    }
    if vertical == "image":
        image_url = _safe_text(
            item.get("thumbnail")
            or item.get("pic")
            or item.get("image")
            or item.get("imageUrl")
            or item.get("url")
        )
        if image_url:
            result["imageUrl"] = image_url[:700]
    return {key: value for key, value in result.items() if value not in ("", None)}


def _metaso_search_public(query: str, *, limit: int, vertical: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    normalized_vertical = _normalize_search_vertical(vertical)
    engine_type = METASO_VERTICAL_ENGINE_TYPES.get(normalized_vertical, "")
    session = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        "metaso-pc": "pc",
    }
    with _bypass_proxy_env(_should_bypass_proxy_env()):
        home_timeout = max(1.0, min(8.0, deadline - time.monotonic()))
        home = session.get(METASO_HOME_URL, headers=headers, timeout=home_timeout)
        token = _metaso_extract_token(home.text)
        if not token:
            return {
                "ok": False,
                "failureClass": "provider_challenge",
                "reason": "metaso_token_not_found",
                "retryable": True,
            }

        payload = {
            "question": query,
            "mode": "detail",
            "model": "fast_thinking",
            "deepResearchModel": "fast",
            "engineType": engine_type,
            "scholarSearchDomain": "all",
            "debug": False,
            "url": METASO_HOME_URL,
            "lang": "zh",
            "enableMix": True,
            "newEngine": True,
            "enableImage": normalized_vertical == "image",
            "metaso-pc": "pc",
            "token": token,
        }
        request_headers = {
            **headers,
            "accept": "text/event-stream",
            "content-type": "application/json",
            "origin": "https://metaso.cn",
            "referer": METASO_HOME_URL,
            "token": token,
        }
        stream_timeout = max(1.0, deadline - time.monotonic())
        response = session.post(
            METASO_SEARCH_ENDPOINT,
            headers=request_headers,
            json=payload,
            stream=True,
            timeout=stream_timeout,
        )

        results: list[dict[str, str]] = []
        seen: set[str] = set()
        events_seen = 0
        error_event: dict[str, Any] | None = None
        result_id = ""
        group_id = ""
        max_results = max(1, min(int(limit or 5), 10))
        for raw_line in response.iter_lines(decode_unicode=True):
            if time.monotonic() >= deadline:
                error_event = {"failureClass": "deadline_exceeded", "reason": "metaso_provider_deadline_exceeded"}
                break
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            if data == "[TOO_MANY_REQUESTS]":
                error_event = {"failureClass": "provider_rate_limited", "reason": "metaso_too_many_requests"}
                break
            try:
                event = json.loads(data)
            except Exception:
                continue
            events_seen += 1
            event_type = _safe_text(event.get("type"))
            if event_type == "error":
                error_event = {
                    "failureClass": "provider_rate_limited" if int(event.get("code") or 0) == 4001 else "provider_error",
                    "reason": _safe_text(event.get("msg") or event.get("message") or "metaso_error"),
                    "code": event.get("code"),
                }
                break
            if event_type == "session-created" and isinstance(event.get("data"), dict):
                group_id = _safe_text(event["data"].get("groupId") or event["data"].get("id"))
            if event_type in {"query", "set-reference"}:
                result_id = _safe_text(event.get("debugId") or event.get("resultId") or result_id)
            if event_type in {"set-reference", "update-reference"} and isinstance(event.get("list"), list):
                for item in event["list"]:
                    if not isinstance(item, dict):
                        continue
                    result = _metaso_result_from_item(item, rank=len(results) + 1, vertical=normalized_vertical)
                    identity = _safe_text(result.get("url") or item.get("id") or item.get("docId"))
                    if not identity or identity in seen:
                        continue
                    seen.add(identity)
                    results.append(result)
                    if len(results) >= max_results:
                        return {
                            "ok": True,
                            "provider": "metaso",
                            "searchVertical": normalized_vertical,
                            "engineType": engine_type,
                            "resultId": result_id,
                            "groupId": group_id,
                            "eventsSeen": events_seen,
                            "results": results,
                        }
        if results:
            return {
                "ok": True,
                "provider": "metaso",
                "searchVertical": normalized_vertical,
                "engineType": engine_type,
                "resultId": result_id,
                "groupId": group_id,
                "eventsSeen": events_seen,
                "results": results[:max_results],
            }
        if error_event:
            return {"ok": False, **error_event, "eventsSeen": events_seen, "searchVertical": normalized_vertical}
        return {
            "ok": False,
            "failureClass": "no_results",
            "reason": "metaso_no_public_results",
            "eventsSeen": events_seen,
            "searchVertical": normalized_vertical,
        }


@tool
def web_read(
    url: str,
    mode: WebFetchMode = "auto",
    headless: bool = True,
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    useAgentBrowserProfile: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Read one known webpage and return cleaned page text.

    Use this when you already have a URL and need the page content. Do not use it as deep research: if the task
    needs several sources, freshness checks, source confidence, or an answer pack, request 深度调研 and use
    `research_broker`. If the user needs DOM/UI/code reference instead of article text, use
    web_extract(extract="raw_html" or "ui_snapshot").

    mode:
    - auto: 先走静态抓取，再按需尝试 dynamic / stealth
    - static: 仅静态抓取
    - dynamic: 仅动态页面抓取
    - stealth: 仅反反爬抓取

    useAgentBrowserProfile:
    - Admin 已开启 systemBase.webFetch.useAgentBrowserProfile 且目标域名命中 allowlist 时，
      auto/dynamic/stealth 会自动复用 Agent 专用浏览器登录态；显式 true 会直接使用该 profile。
    """
    allowed, error_message = _guard_url(url, tool_call_id=tool_call_id)
    if not allowed:
        return _render_error_payload(
            url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            error=error_message or "Safety Guardian 已阻止网页读取。",
            blocked=True,
        )

    try:
        payload = _fetch_with_scrapling_internal(
            url,
            mode=mode,
            headless=headless,
            referer_mode=referer_mode,
            referer_url=referer_url,
            timeout_seconds=WEB_READ_TIMEOUT_SECONDS,
            use_agent_browser_profile=bool(useAgentBrowserProfile),
        )
        if _detect_login_wall(payload):
            return _render_needs_login_payload(page=payload, use_agent_browser_profile=bool(useAgentBrowserProfile))
        return json.dumps(_render_page_summary(payload), ensure_ascii=False, indent=2)
    except Exception as exc:
        error = str(exc)
        return _render_error_payload(
            url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            error=f"Error reading webpage with Scrapling: {error}",
            attempted_modes=_error_attempted_modes(error),
            elapsed_ms=_error_elapsed_ms(error),
        )


@tool
def web_extract(
    url: str,
    extract: WebExtractMode = "article",
    mode: WebFetchMode = "auto",
    headless: bool = True,
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    adaptive: bool = False,
    adaptive_id: str = "",
    adaptive_threshold: int = 70,
    useAgentBrowserProfile: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Extract structured data from one known webpage.

    Use this when the page itself is the target and you need a specific shape: article text, links, metadata,
    media resources, raw HTML, or a UI snapshot. For facts that must be verified across multiple sources, use
    `research_broker` instead of repeatedly extracting unrelated pages.

    extract:
    - article: 提取正文、标题与摘要信息
    - links: 提取页面主要链接
    - metadata: 提取 meta 数据
    - media: 提取页面图片/视频/音频资源
    - raw_html: 返回用于 DOM/UI/选择器分析的 HTML 片段；普通阅读不要使用
    - ui_snapshot: 返回轻量结构快照，适合参考页面 UI/表单/按钮结构

    useAgentBrowserProfile 同 web_read：allowlist 命中时浏览器模式会自动复用 Agent 专用浏览器登录态；显式 true 会直接使用该 profile。
    """
    allowed, error_message = _guard_url(url, tool_call_id=tool_call_id)
    if not allowed:
        return _render_error_payload(
            url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            error=error_message or "Safety Guardian 已阻止网页提取。",
            blocked=True,
        )

    try:
        payload = _fetch_with_scrapling_internal(
            url,
            mode=mode,
            headless=headless,
            referer_mode=referer_mode,
            referer_url=referer_url,
            timeout_seconds=WEB_READ_TIMEOUT_SECONDS,
            use_agent_browser_profile=bool(useAgentBrowserProfile),
        )
        if _detect_login_wall(payload):
            return _render_needs_login_payload(page=payload, use_agent_browser_profile=bool(useAgentBrowserProfile))
        resolved_adaptive_id = adaptive_id.strip() or _default_adaptive_id(payload.final_url or payload.url, extract)
        container, adaptive_signals, selector_signals = _resolve_extract_container(
            payload,
            extract=extract,
            adaptive_enabled=adaptive,
            adaptive_id=resolved_adaptive_id,
            adaptive_threshold=adaptive_threshold,
        )
        result = {
            "ok": True,
            "url": payload.url,
            "finalUrl": payload.final_url,
            "requestedMode": payload.requested_mode,
            "refererMode": payload.referer_mode,
            "refererUrl": payload.referer_url,
            "status": payload.status,
            "fetchMode": payload.fetch_mode,
            "tlsStrategy": payload.tls_strategy,
            "caBundlePath": payload.ca_bundle_path,
            "proxyBypassUsed": payload.proxy_bypass_used,
            "attemptedModes": payload.attempted_modes,
            "availableModes": payload.available_modes,
            "fallbackUsed": payload.requested_mode == "auto" and payload.fetch_mode != "static",
            "warnings": payload.warnings,
            "analysisHints": _build_analysis_hints(payload),
            "visionCandidates": _build_vision_candidates(payload),
            "visionRecommended": bool(_build_vision_candidates(payload, limit=1)),
            "extract": extract,
            "adaptiveSignals": adaptive_signals,
            "selectorSignals": selector_signals,
        }
        if extract == "links":
            result["links"] = _extract_links(container, payload.final_url)
        elif extract == "media":
            result["media"] = _extract_media(container, payload.final_url)
        elif extract == "metadata":
            result["metadata"] = payload.metadata
            if adaptive:
                result["warnings"] = [
                    *payload.warnings,
                    "metadata 提取对 adaptive 的增益有限，当前仅对页面主容器定位做稳定性记录。",
                ]
        elif extract == "raw_html":
            raw_html, truncated = _html_preview(str(container), limit=16000)
            result["title"] = payload.title
            result["rawHtml"] = raw_html
            result["rawHtmlTruncated"] = truncated
            result["metadata"] = payload.metadata
        elif extract == "ui_snapshot":
            result["title"] = payload.title
            result["uiSnapshot"] = _ui_snapshot(container)
            result["metadata"] = payload.metadata
            result["links"] = _extract_links(container, payload.final_url)[:10]
        else:
            title = payload.title
            if not title:
                title_node = container.select_one("h1, title")
                title = _safe_text(title_node.get_text(" ", strip=True) if title_node else "")
            result["title"] = title
            result["text"] = _extract_main_text(container, payload.final_url or payload.url)
            result["metadata"] = payload.metadata
            result["media"] = _extract_media(container, payload.final_url)
        result.update(
            _page_quality_fields(
                payload,
                text=str(result.get("text") or result.get("rawHtml") or result.get("uiSnapshot") or ""),
                html=payload.html,
                mode=str(extract),
            )
        )
        result.update(_web_read_source_fields(payload.final_url or payload.url, used_browser_profile=payload.agent_browser_profile_used))
        if adaptive and adaptive_signals.get("adaptiveFallback"):
            result["warnings"] = [
                *result.get("warnings", []),
                "adaptive 容器定位未能稳定启用，已自动回退到普通抽取。",
            ]
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:
        error = str(exc)
        return _render_error_payload(
            url=url,
            requested_mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            error=f"Error extracting webpage with Scrapling: {error}",
            attempted_modes=_error_attempted_modes(error),
            elapsed_ms=_error_elapsed_ms(error),
        )


@tool
def web_search(
    query: str,
    limit: int = 5,
    search_engine: WebSearchEngine = "auto",
    search_vertical: WebSearchVertical = "all",
    mode: WebFetchMode = "auto",
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    useAgentBrowserProfile: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Internal/raw search primitive behind Source Router.

    Product agents should normally use `web_broker` for one quick public-web lookup, or `research_broker` for
    深度调研 with evidence, ranking, and reusable answer packs.

    For multi-source research, current facts, source confidence, or parallel query decomposition, request
    research.core and use research_broker instead of doing ad-hoc one-shot searches.

    search_vertical is honored by MetaSo public search:
    - all/web: 全网
    - document: 文库
    - academic: 学术
    - image/video/podcast: 图片/视频/播客

    useAgentBrowserProfile 仅用于需要登录态的 browser-backed 搜索/读取路径；allowlist 命中时会自动复用 Agent 浏览器 profile。
    """
    requested_provider = str(search_engine or "auto").strip().lower()
    requested_vertical = _normalize_search_vertical(str(search_vertical or "all"))
    router_plan = _source_router_plan(
        query=query,
        requested_provider=requested_provider,
        needs_login=bool(useAgentBrowserProfile),
    )
    providers = list(router_plan.get("providers") or [])
    attempted_providers: list[dict[str, Any]] = list(router_plan.get("skippedProviders") or [])
    started_at = time.monotonic()
    last_error = ""

    if not providers:
        first_failure = attempted_providers[0] if attempted_providers else {}
        failure_class = _safe_text(first_failure.get("failureClass")) or "unsupported_operation"
        recommended = (
            "该 provider 需要配置 API key 或启用适配器；请检查 systemBase.webFetch.providers，或使用 search_engine=auto 让 Source Router 自动降级。"
            if failure_class in {"credential_missing", "provider_adapter_unavailable", "provider_unconfigured"}
            else "使用 search_engine=auto，或选择 metaso/duckduckgo/baidu/bing/google/searxng 中的一个。"
        )
        return json.dumps(
            {
                "ok": False,
                "query": query,
                "requestedProvider": requested_provider,
                "searchVertical": requested_vertical,
                "attemptedProviders": attempted_providers,
                "failureClass": failure_class,
                "retryable": False,
                "recommendedNextAction": recommended,
                "error": _safe_text(first_failure.get("reason")) or f"Unsupported search provider: {requested_provider}",
                **_source_router_payload_fields(router_plan, attempted_providers=attempted_providers),
            },
            ensure_ascii=False,
            indent=2,
        )

    for provider in providers:
        elapsed = time.monotonic() - started_at
        remaining = WEB_SEARCH_TOTAL_TIMEOUT_SECONDS - elapsed
        if remaining <= 0:
            attempted_providers.append(
                {
                    "provider": provider,
                    "status": "skipped",
                    "failureClass": "deadline_exceeded",
                    "reason": "search_total_deadline_exceeded",
                    "elapsedMs": int(elapsed * 1000),
                }
            )
            break
        search_url = _provider_search_url(provider, query)
        if not search_url:
            attempted_providers.append(
                {
                    "provider": provider,
                    "status": "skipped",
                    "failureClass": "provider_unconfigured",
                    "reason": "search_url_not_configured",
                }
            )
            last_error = "search_url_not_configured"
            continue
        allowed, error_message = _guard_url(search_url, tool_call_id=tool_call_id)
        if not allowed:
            attempted_providers.append({"provider": provider, "status": "blocked", "reason": error_message or "blocked"})
            last_error = error_message or "Safety Guardian 已阻止网页搜索。"
            continue
        profile_skip = _agent_browser_profile_search_skip(provider, search_url)
        if profile_skip:
            attempted_providers.append(profile_skip)
            last_error = str(profile_skip.get("reason") or profile_skip.get("failureClass") or "")
            if requested_provider == "auto":
                continue
            return json.dumps(
                {
                    "ok": False,
                    "query": query,
                    "requestedProvider": requested_provider,
                    "searchVertical": requested_vertical,
                    "attemptedProviders": attempted_providers,
                    "failureClass": "needs_agent_browser_login",
                    "elapsedMs": int((time.monotonic() - started_at) * 1000),
                    "retryable": True,
                    "recommendedNextAction": profile_skip["recommendedNextAction"],
                    "error": last_error,
                    **_source_router_payload_fields(
                        router_plan,
                        selected_provider=provider,
                        attempted_providers=attempted_providers,
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )

        try:
            provider_timeout = max(1.0, min(WEB_SEARCH_PROVIDER_TIMEOUT_SECONDS, remaining))
            if provider in {"brave", "tavily", "exa"}:
                api_result = _api_search_public(provider, query, limit=limit, timeout_seconds=provider_timeout)
                if not bool(api_result.get("ok")):
                    attempted_providers.append(
                        {
                            "provider": provider,
                            "status": "error",
                            "failureClass": api_result.get("failureClass") or "search_failed",
                            "reason": api_result.get("reason") or f"{provider}_api_search_failed",
                            "statusCode": api_result.get("statusCode"),
                        }
                    )
                    last_error = _safe_text(api_result.get("reason") or api_result.get("failureClass"))
                    if requested_provider == "auto":
                        continue
                    return json.dumps(
                        {
                            "ok": False,
                            "query": query,
                            "requestedProvider": requested_provider,
                            "searchVertical": requested_vertical,
                            "attemptedProviders": attempted_providers,
                            "failureClass": api_result.get("failureClass") or "search_failed",
                            "elapsedMs": int((time.monotonic() - started_at) * 1000),
                            "retryable": bool(api_result.get("retryable")),
                            "recommendedNextAction": "检查 provider API key、配额或网络 route；也可以用 search_engine=auto 让 Source Router 自动降级。",
                            "error": last_error,
                            **_source_router_payload_fields(
                                router_plan,
                                selected_provider=provider,
                                attempted_providers=attempted_providers,
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                results = api_result.get("results") if isinstance(api_result.get("results"), list) else []
                attempted_providers.append({"provider": provider, "status": "ok", "resultCount": len(results)})
                response = {
                    "ok": True,
                    "query": query,
                    "provider": provider,
                    "requestedProvider": requested_provider,
                    "searchVertical": requested_vertical,
                    "attemptedProviders": attempted_providers,
                    "searchUrl": search_url,
                    "resultCount": len(results),
                    "results": results,
                    **_source_router_payload_fields(
                        router_plan,
                        selected_provider=provider,
                        attempted_providers=attempted_providers,
                    ),
                }
                return json.dumps(response, ensure_ascii=False, indent=2)
            if provider == "searxng":
                searxng_result = _searxng_search_public(search_url, limit=limit, timeout_seconds=provider_timeout)
                if not bool(searxng_result.get("ok")):
                    attempted_providers.append(
                        {
                            "provider": provider,
                            "status": "error",
                            "failureClass": searxng_result.get("failureClass") or "search_failed",
                            "reason": searxng_result.get("reason") or "searxng_search_failed",
                        }
                    )
                    last_error = _safe_text(searxng_result.get("reason") or searxng_result.get("failureClass"))
                    if requested_provider == "auto":
                        continue
                    return json.dumps(
                        {
                            "ok": False,
                            "query": query,
                            "requestedProvider": requested_provider,
                            "searchVertical": requested_vertical,
                            "attemptedProviders": attempted_providers,
                            "failureClass": searxng_result.get("failureClass") or "search_failed",
                            "elapsedMs": int((time.monotonic() - started_at) * 1000),
                            "retryable": bool(searxng_result.get("retryable")),
                            "recommendedNextAction": "SearXNG 实例需要启用 JSON format；否则换 provider 或改用 research_broker。",
                            "error": last_error,
                            **_source_router_payload_fields(
                                router_plan,
                                selected_provider=provider,
                                attempted_providers=attempted_providers,
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                results = searxng_result.get("results") if isinstance(searxng_result.get("results"), list) else []
                attempted_providers.append({"provider": provider, "status": "ok", "resultCount": len(results)})
                response = {
                    "ok": True,
                    "query": query,
                    "provider": provider,
                    "requestedProvider": requested_provider,
                    "searchVertical": requested_vertical,
                    "attemptedProviders": attempted_providers,
                    "searchUrl": search_url,
                    "resultCount": len(results),
                    "results": results,
                    **_source_router_payload_fields(
                        router_plan,
                        selected_provider=provider,
                        attempted_providers=attempted_providers,
                    ),
                }
                return json.dumps(response, ensure_ascii=False, indent=2)
            if provider == "metaso":
                use_browser_for_provider = bool(useAgentBrowserProfile) or bool(_agent_browser_profile_allowed(search_url)[0])
                if not use_browser_for_provider:
                    if _provider_api_key("metaso"):
                        metaso_result = _metaso_api_search(
                            query,
                            limit=limit,
                            vertical=requested_vertical,
                            timeout_seconds=provider_timeout,
                        )
                    else:
                        metaso_result = _metaso_search_public(
                            query,
                            limit=limit,
                            vertical=requested_vertical,
                            timeout_seconds=provider_timeout,
                        )
                    if not bool(metaso_result.get("ok")):
                        attempted_providers.append(
                            {
                                "provider": provider,
                                "status": "error",
                                "failureClass": metaso_result.get("failureClass") or "search_failed",
                                "reason": metaso_result.get("reason") or "metaso_public_search_failed",
                                "searchVertical": requested_vertical,
                                "eventsSeen": metaso_result.get("eventsSeen"),
                            }
                        )
                        last_error = _safe_text(metaso_result.get("reason") or metaso_result.get("failureClass"))
                        if requested_provider == "auto":
                            continue
                        return json.dumps(
                            {
                                "ok": False,
                                "query": query,
                                "requestedProvider": requested_provider,
                                "searchVertical": requested_vertical,
                                "attemptedProviders": attempted_providers,
                                "failureClass": metaso_result.get("failureClass") or "search_failed",
                                "elapsedMs": int((time.monotonic() - started_at) * 1000),
                                "retryable": metaso_result.get("failureClass") in {"provider_rate_limited", "network_timeout", "deadline_exceeded", "no_results"},
                                "recommendedNextAction": "MetaSo 公共搜索当前限流或无结果；请启用 Agent 浏览器登录态、稍后重试、换 search_vertical，或让 auto 降级到其他搜索源。",
                                "error": last_error,
                                **_source_router_payload_fields(
                                    router_plan,
                                    selected_provider=provider,
                                    attempted_providers=attempted_providers,
                                ),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    results = metaso_result.get("results") if isinstance(metaso_result.get("results"), list) else []
                    attempted_providers.append(
                        {
                            "provider": provider,
                            "status": "ok",
                            "resultCount": len(results),
                            "searchVertical": requested_vertical,
                            "scope": metaso_result.get("scope"),
                            "resultId": metaso_result.get("resultId"),
                        }
                    )
                    response = {
                        "ok": True,
                        "query": query,
                        "provider": provider,
                        "requestedProvider": requested_provider,
                        "searchVertical": requested_vertical,
                        "attemptedProviders": attempted_providers,
                        "searchUrl": search_url,
                        "resultCount": len(results),
                        "results": results,
                        "metaso": {
                            "engineType": metaso_result.get("engineType"),
                            "scope": metaso_result.get("scope"),
                            "apiEndpoint": metaso_result.get("apiEndpoint"),
                            "resultId": metaso_result.get("resultId"),
                            "groupId": metaso_result.get("groupId"),
                            "eventsSeen": metaso_result.get("eventsSeen"),
                        },
                        **_source_router_payload_fields(
                            router_plan,
                            selected_provider=provider,
                            attempted_providers=attempted_providers,
                        ),
                    }
                    return json.dumps(response, ensure_ascii=False, indent=2)
            effective_use_agent_browser_profile = bool(useAgentBrowserProfile) or bool(
                _provider_prefers_agent_browser_profile(provider) and _agent_browser_profile_allowed(search_url)[0]
            )
            payload = _fetch_with_scrapling_internal(
                search_url,
                mode=mode,
                headless=True,
                referer_mode=referer_mode,
                referer_url=referer_url,
                timeout_seconds=provider_timeout,
                use_agent_browser_profile=effective_use_agent_browser_profile,
            )
            soup = BeautifulSoup(payload.html, "html.parser")
            results = _extract_search_results(soup, provider=provider, limit=limit)
            page_failure = _search_page_failure(payload, soup, provider=provider, result_count=len(results))
            if page_failure:
                attempted_providers.append(
                    {
                        "provider": provider,
                        "status": page_failure["status"],
                        "failureClass": page_failure["failureClass"],
                        "reason": page_failure["reason"],
                        "resultCount": 0,
                        "finalUrl": payload.final_url,
                        "statusCode": payload.status,
                    }
                )
                last_error = str(page_failure["reason"])
                if requested_provider == "auto":
                    continue
                return json.dumps(
                    {
                        "ok": False,
                        "query": query,
                        "requestedProvider": requested_provider,
                        "searchVertical": requested_vertical,
                        "attemptedProviders": attempted_providers,
                        "failureClass": page_failure["failureClass"],
                        "elapsedMs": int((time.monotonic() - started_at) * 1000),
                        "retryable": page_failure["failureClass"] in {"provider_challenge", "no_results"},
                        "recommendedNextAction": "该搜索源返回验证页或没有可抽取结果；请换 provider、换关键词，或使用 research_broker 多源调研。",
                        "error": str(page_failure["reason"]),
                        **_source_router_payload_fields(
                            router_plan,
                            selected_provider=provider,
                            attempted_providers=attempted_providers,
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            attempted_providers.append({"provider": provider, "status": "ok", "resultCount": len(results)})
            if not results and requested_provider == "auto":
                continue

            response = {
                "ok": True,
                "query": query,
                "provider": provider,
                "requestedProvider": requested_provider,
                "searchVertical": requested_vertical,
                "attemptedProviders": attempted_providers,
                "searchUrl": search_url,
                "requestedMode": payload.requested_mode,
                "refererMode": payload.referer_mode,
                "refererUrl": payload.referer_url,
                "fetchMode": payload.fetch_mode,
                "tlsStrategy": payload.tls_strategy,
                "caBundlePath": payload.ca_bundle_path,
                "proxyBypassUsed": payload.proxy_bypass_used,
                "attemptedModes": payload.attempted_modes,
                "availableModes": payload.available_modes,
                "fallbackUsed": payload.requested_mode == "auto" and payload.fetch_mode != "static",
                "warnings": payload.warnings,
                "analysisHints": _build_analysis_hints(payload),
                "agentBrowserProfile": (
                    {
                        "used": True,
                        "matchedHost": payload.agent_browser_profile_host,
                        "profile": agent_browser_profile_summary(payload.agent_browser_kind or "auto", include_security_note=False),
                    }
                    if payload.agent_browser_profile_used
                    else {"used": False}
                ),
                "resultCount": len(results),
                "results": results,
                **_source_router_payload_fields(
                    router_plan,
                    selected_provider=provider,
                    attempted_providers=attempted_providers,
                ),
            }
            return json.dumps(response, ensure_ascii=False, indent=2)
        except Exception as exc:
            error = str(exc)
            last_error = error
            failure_class = _classify_web_fetch_failure(error)
            attempted_providers.append({
                "provider": provider,
                "status": "error",
                "failureClass": failure_class,
                "reason": error[:1000],
                "elapsedMs": _error_elapsed_ms(error),
            })

    aggregate_failure = "search_failed"
    non_blocked_attempts = [item for item in attempted_providers if item.get("status") != "blocked"]
    if non_blocked_attempts and all(item.get("failureClass") == "network_timeout" for item in non_blocked_attempts):
        aggregate_failure = "network_timeout"
    elif any(item.get("failureClass") == "tool_configuration_error" for item in attempted_providers):
        aggregate_failure = "tool_configuration_error"
    elif any(item.get("failureClass") == "tool_context_unavailable" for item in attempted_providers):
        aggregate_failure = "tool_context_unavailable"
    elif any(item.get("failureClass") == "provider_challenge" for item in attempted_providers):
        aggregate_failure = "provider_challenge"
    elif attempted_providers and all(item.get("failureClass") == "no_results" for item in attempted_providers):
        aggregate_failure = "no_results"
    elif any(item.get("status") == "blocked" for item in attempted_providers):
        aggregate_failure = "blocked_by_safety"

    return json.dumps(
        {
            "ok": False,
            "query": query,
            "requestedProvider": requested_provider,
            "searchVertical": requested_vertical,
            "attemptedProviders": attempted_providers,
            "failureClass": aggregate_failure,
            "elapsedMs": int((time.monotonic() - started_at) * 1000),
            "retryable": aggregate_failure in {"network_timeout", "search_failed", "provider_challenge", "no_results"},
            "recommendedNextAction": (
                "部分搜索源当前不可用；优先使用 MetaSo/DuckDuckGo，或换 search_vertical/缩小关键词，或改用 research_broker 记录 failed_source。"
                if aggregate_failure in {"network_timeout", "search_failed", "provider_challenge", "no_results"}
                else "检查工具配置/安全审批上下文；不要继续盲等 watchdog。"
            ),
            "error": last_error or "No search provider returned usable results.",
            **_source_router_payload_fields(router_plan, attempted_providers=attempted_providers),
        },
        ensure_ascii=False,
        indent=2,
    )


def source_router_search(
    *,
    query: str,
    limit: int = 5,
    search_engine: WebSearchEngine = "auto",
    search_vertical: WebSearchVertical = "all",
    mode: WebFetchMode = "auto",
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    useAgentBrowserProfile: bool = False,
    tool_call_id: str = "",
) -> str:
    """Internal Source Router search primitive used by Research/Web runtimes.

    This deliberately is not a public LangChain tool surface. Product-facing
    agents should prefer web_broker for one-off use and research_broker for
    multi-source evidence work.
    """
    return web_search.func(
        query=query,
        limit=limit,
        search_engine=search_engine,
        search_vertical=search_vertical,
        mode=mode,
        referer_mode=referer_mode,
        referer_url=referer_url,
        useAgentBrowserProfile=useAgentBrowserProfile,
        tool_call_id=tool_call_id,
    )


@tool
def web_fetch(
    target: str,
    intent: WebFetchIntent = "auto",
    extract: WebExtractMode = "article",
    search_engine: WebSearchEngine = "auto",
    search_vertical: WebSearchVertical = "all",
    mode: WebFetchMode = "auto",
    headless: bool = True,
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    adaptive: bool = False,
    adaptive_id: str = "",
    adaptive_threshold: int = 70,
    limit: int = 5,
    useAgentBrowserProfile: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Convenience web entrypoint for one read, one extract, or one search.

    Use this for quick public-web work. It is not the deep research path: when the answer depends on comparing
    multiple sources, current model/provider/API facts, conflicting claims, or source quality, use
    `research_broker`.

    intent:
    - auto: URL 走 read，非 URL 走 search
    - read: 返回清洗后的 Markdown 页面内容
    - extract: 返回结构化内容；UI/DOM 参考用 raw_html 或 ui_snapshot
    - search: 通过 Source Router 选择国内/海外 provider，并返回清洗后的搜索结果

    useAgentBrowserProfile 显式为 true 时会直接使用 Agent 浏览器 profile；未显式设置但目标域名命中 Admin/System Base allowlist 时，浏览器读取路径也会自动复用。
    """
    normalized_intent = str(intent or "auto").strip().lower()
    if normalized_intent == "auto":
        normalized_intent = "read" if _looks_like_url(target) else "search"

    if normalized_intent == "read":
        return web_read.func(
            url=target,
            mode=mode,
            headless=headless,
            referer_mode=referer_mode,
            referer_url=referer_url,
            useAgentBrowserProfile=bool(useAgentBrowserProfile),
            tool_call_id=tool_call_id,
        )
    if normalized_intent == "extract":
        return web_extract.func(
            url=target,
            extract=extract,
            mode=mode,
            headless=headless,
            referer_mode=referer_mode,
            referer_url=referer_url,
            adaptive=adaptive,
            adaptive_id=adaptive_id,
            adaptive_threshold=adaptive_threshold,
            useAgentBrowserProfile=bool(useAgentBrowserProfile),
            tool_call_id=tool_call_id,
        )
    if normalized_intent == "search":
        return web_search.func(
            query=target,
            limit=limit,
            search_engine=search_engine,
            search_vertical=search_vertical,
            mode=mode,
            referer_mode=referer_mode,
            referer_url=referer_url,
            useAgentBrowserProfile=bool(useAgentBrowserProfile),
            tool_call_id=tool_call_id,
        )
    return json.dumps(
        {"ok": False, "intent": normalized_intent, "error": f"Unsupported web_fetch intent: {normalized_intent}"},
        ensure_ascii=False,
        indent=2,
    )


@tool
def web_broker(
    target: str,
    mode: str = "fetch",
    extract: WebExtractMode = "article",
    search_engine: WebSearchEngine = "auto",
    search_vertical: WebSearchVertical = "all",
    fetch_mode: WebFetchMode = "static",
    headless: bool = True,
    referer_mode: WebRefererMode = "none",
    referer_url: str = "",
    adaptive: bool = False,
    adaptive_id: str = "",
    adaptive_threshold: int = 70,
    limit: int = 5,
    useAgentBrowserProfile: bool = False,
    debug: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """L1 普通网页读取器：一个已知页面、页面结构或全新孤立窄事实；返回网页材料，不产出受管证据结论或 handoff。

    Use this for one URL/page or one narrow inline lookup. Use `research_broker` for one focused multi-source question;
    use a Research episode for several independent domains, recovery/progress, or evidence that must feed a later
    workflow. A brief already owned by a Research episode must be repaired there, never by chaining this tool.
    An unreadable page is transport evidence, not proof that the requested fact is false.

    mode:
    - fetch: smart unified entrypoint; URLs auto-route to read, non-URLs auto-route to search
    - read: read a single page and return compact cleaned Markdown/title/link results
    - extract: 抽取结构化内容，适合 article / links / metadata / media / raw_html / ui_snapshot
    - search: Source Router 公开搜索，返回清洗后的搜索结果列表和 provider/网络路由质量信号

    fetch_mode: static is the default; use auto/dynamic/stealth for JS/login/challenge pages. For DOM/UI structure,
    use mode=extract with extract=raw_html or ui_snapshot.

    debug=false keeps the Agent result compact; true adds transport/TLS/fallback/selector diagnostics.
    useAgentBrowserProfile=true skips public/static attempts and uses the allowlisted Agent browser profile.
    """
    normalized_mode = str(mode or "fetch").strip().lower()
    if normalized_mode not in {"fetch", "read", "extract", "search"}:
        return json.dumps(
            {
                "ok": False,
                "mode": normalized_mode,
                "summary": f"Unsupported web_broker mode: {normalized_mode}",
                "error": f"Unsupported web_broker mode: {normalized_mode}",
            },
            ensure_ascii=False,
            indent=2,
        )

    intent = "auto" if normalized_mode == "fetch" else normalized_mode
    raw_result = web_fetch.func(
        target=target,
        intent=intent,
        extract=extract,
        search_engine=search_engine,
        search_vertical=search_vertical,
        mode=fetch_mode,
        headless=headless,
        referer_mode=referer_mode,
        referer_url=referer_url,
        adaptive=adaptive,
        adaptive_id=adaptive_id,
        adaptive_threshold=adaptive_threshold,
        limit=limit,
        useAgentBrowserProfile=bool(useAgentBrowserProfile),
        tool_call_id=tool_call_id,
    )
    try:
        parsed = json.loads(raw_result)
    except Exception:
        return raw_result
    if not isinstance(parsed, dict):
        return raw_result
    compact = _compact_web_broker_payload(parsed, requested_mode=normalized_mode, debug=bool(debug))
    call_count, over_research_threshold = _note_web_broker_context_call(tool_call_id)
    if call_count:
        compact["webBrokerCallCount"] = call_count
    if over_research_threshold:
        compact["researchRuntimeWarning"] = (
            "本轮已连续使用 web_broker。请重新判断剩余范围：只有一个聚焦的多源问题时改用 "
            "research_broker；还有多个独立事实 brief、需要恢复/进度/typed handoff，或证据要交给后续"
            "执行时，通过 runtime_broker route 创建一个 Research episode。不要继续用网页调用手工拼调研流程。"
        )
        compact["recommendedNextAction"] = (
            "reassess_scope; choose one focused research_broker call or one managed Research episode; "
            "do not chain both as parallel orchestration"
        )
    return json.dumps(compact, ensure_ascii=False, indent=2)
