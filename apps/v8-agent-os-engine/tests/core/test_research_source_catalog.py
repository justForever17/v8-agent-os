from __future__ import annotations

import copy

import pytest

from core.tools import research_broker as research
from core.tools.research_source_catalog import match_source_catalog


@pytest.mark.parametrize("url,expected", [
    ("https://cloud.tencent.com/developer/article/2528305", "vendor_developer_community"),
    ("https://cloud.tencent.com/developer/ask/123", "vendor_developer_community"),
    ("https://cloud.tencent.com/developer", "vendor_developer_community"),
    ("https://cloud.tencent.com/document/product/1729/111007", "official_vendor_docs"),
    ("https://cloud.tencent.com/developer-tools", "official_vendor_docs"),
    ("https://cloud.tencent.com.evil.example/developer/article/123", None),
])
def test_catalog_distinguishes_community_from_vendor_documentation(url, expected):
    assert (research._catalog_match(url) or {}).get("id") == expected


@pytest.mark.parametrize("allowed_domains", [[], ["cloud.tencent.com"]])
def test_allowlisted_community_is_readable_but_not_primary_evidence(allowed_domains):
    quality = research._source_quality(
        "https://cloud.tencent.com/developer/article/2528305",
        allowed_domains=allowed_domains, source_policy="authoritative",
        question="Tencent service requirements", title="An author's commentary",
    )
    assert quality["catalogSourceId"] == "vendor_developer_community"
    assert quality["authorityTier"] == quality["tier"] == "secondary"
    assert research._architect_support_role(quality) == "secondary"
    assert not research._source_matches_intent(quality, "official_primary")
    assert research._source_matches_intent(quality, "balanced")


def test_old_bundle_cannot_promote_community_from_stale_domain_wide_classification():
    source = {
        "url": "https://cloud.tencent.com/developer/article/2528305",
        "tier": "primary", "authorityTier": "primary", "authorityScore": 95,
        "catalogSourceId": "official_vendor_docs", "catalogCategory": "official_docs",
    }
    original = copy.deepcopy(source)
    assert research._architect_support_role(source) == "secondary"
    assert source == original


@pytest.mark.parametrize("reverse", [False, True])
def test_path_scopes_are_order_independent_and_choose_the_longest_boundary(reverse):
    entries = [
        {"id": "domain", "hosts": ["example.com"]},
        {"id": "community", "hosts": ["example.com"], "pathPrefixes": ["/community"]},
        {"id": "announcements", "hosts": ["example.com"], "pathPrefixes": ["/community/announcements/"]},
    ]
    if reverse:
        entries.reverse()
    assert match_source_catalog("https://example.com/community/announcements/new", entries)["id"] == "announcements"
    assert match_source_catalog("https://example.com/community/articles/123", entries)["id"] == "community"
    assert match_source_catalog("https://example.com/community-tools", entries)["id"] == "domain"


@pytest.mark.parametrize("prefixes", [[], [""], ["/"], ["community"], "/community", None])
def test_invalid_path_scope_cannot_expand_to_whole_domain(prefixes):
    assert match_source_catalog("https://example.com/docs", [
        {"hosts": ["example.com"], "pathPrefixes": prefixes},
    ]) is None
