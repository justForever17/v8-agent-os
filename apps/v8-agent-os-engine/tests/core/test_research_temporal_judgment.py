from __future__ import annotations

import copy
import importlib

import pytest


research = importlib.import_module("core.tools.research_broker")


@pytest.mark.parametrize("year", [2010, 2099])
def test_requested_effective_date_survives_repeated_subject_and_duty_paragraphs(year):
    date_clause = f"第十二条 本管理规则自{year}年8月15日起施行。"
    source = {"citationKey": "S1", "title": "公开服务管理规则", "url": "https://example.test/rule",
              "text": "\n\n".join([
                  "第二条 公开服务管理规则适用于向公众提供服务的单位，内部研究不适用。",
                  "第三条 公开服务管理规则要求服务提供者应当遵守数据管理义务。",
                  "第四条 公开服务管理规则要求服务提供者必须保护个人信息。",
                  date_clause,
              ])}
    before = copy.deepcopy(source)
    candidates = research._architect_evidence_candidates(
        source, "核查公开服务管理规则的适用范围、提供者义务和施行日期。", limit=2,
    )
    assert any(date_clause.rstrip("。") in item["text"] for item in candidates)
    assert source == before


@pytest.mark.parametrize(("question", "excerpt", "expected"), [
    ("What is the effective date?", "The rule takes effect on August 15, 2010.", True),
    ("何时施行？", "本规则自2099年8月15日起施行。", True),
    ("What is the service scope?", "The rule takes effect on August 15, 2010.", False),
    ("When does it apply?", "There is no published date.", False),
    ("何时施行？", "页面广告更新于2099年8月15日。", False),
])
def test_requested_date_ranking_is_not_a_freshness_or_validity_gate(question, excerpt, expected):
    from core.tools.research_temporal_surface import is_requested_date_evidence

    assert is_requested_date_evidence(question, excerpt) is expected


@pytest.mark.parametrize("document_date", [None, "2010-01-01", "2099-01-01", "unresolved"])
@pytest.mark.parametrize("subject", ["policy obligations", "runtime dependencies"])
def test_document_dates_do_not_block_primary_claim_writing(document_date, subject):
    support = {"citationKey": "S1", "tier": "primary", "url": "https://official.example/document"}
    if document_date is not None:
        support["publishedAt"] = document_date
    claims = [{"supportingSources": [support]}]
    before = copy.deepcopy(claims)

    # The role validator may enforce attribution, not decide the lifetime of
    # a policy or dependency by the presence/age of a page's timestamp.
    assert research._architect_source_role_issues(
        f"The current official documentation describes {subject}.", claims
    ) == []
    assert claims == before


def test_date_gate_removal_does_not_promote_secondary_evidence():
    issues = research._architect_source_role_issues(
        "This is the current official requirement.",
        [{"supportingSources": [{"citationKey": "S1", "tier": "secondary", "sourceRole": "secondary"}]}],
    )
    assert "primary_source_role_required" in issues
    assert "secondary_source_role_required" in issues
    assert "current_document_evidence_required" not in issues
