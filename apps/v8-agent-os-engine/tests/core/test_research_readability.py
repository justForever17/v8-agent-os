from __future__ import annotations

import importlib

import pytest


research = importlib.import_module("core.tools.research_broker")


def test_canonical_plan_excludes_footer_but_keeps_original_body_and_operative_fact():
    from core.tools.research_claim_plan import build_canonical_claim_plan
    import copy

    footer = "示例服务管理办法 某信息办公室 © 版权所有 联系我们 技术支持：示例公司 京ICP备12345号 返回顶部 PC版"
    fact = "The public service provider must disclose the scope of processing; internal experiments are excluded."
    source = {"citationKey": "S1", "text": fact + "\n" + footer, "evidenceCandidates": [
        {"text": footer, "evidenceExcerptKey": "S1:E1", "relevanceScore": 100},
        {"text": fact, "evidenceExcerptKey": "S1:E2", "relevanceScore": 60},
    ]}
    before = copy.deepcopy(source)
    plan = build_canonical_claim_plan(
        question="What are the service provider's obligations?", sources=[source],
        required_source_keys=["S1"], required_facet_ids=[],
        minimum_source_count=1, minimum_claim_count=1, target_claim_count=2,
    )
    assert [claim["evidenceExcerptKey"] for claim in plan["claimTable"]] == ["S1:E2"]
    assert source == before


def test_footer_detection_preserves_copyright_prose_and_explicit_site_metadata_research():
    from core.tools.research_readability import is_site_chrome_excerpt

    footer = "版权所有 联系我们 京ICP备12345号 返回顶部"
    assert is_site_chrome_excerpt(footer)
    assert not is_site_chrome_excerpt(footer, question="核查网站版权信息与备案号")
    assert not is_site_chrome_excerpt("版权所有条款应说明技术支持责任。联系我们时应保留授权证据。")


@pytest.mark.parametrize("table", [
    "| 标准号：EXAMPLE 12345-2025 |\n| 中文名称：示例系统内容标识方法 |\n| 标准状态：现行 |\n| 记录类型：标准详情 |",
    "| Parameter | Value |\n| --- | --- |\n| Transport | HTTPS |\n| Retry | bounded |",
])
def test_read_table_is_not_discarded_for_pipe_count(table):
    body = ("- 首页\n- 国家标准\n- 联系我们\n" + table + "\n"
            + "此页面提供记录详情，原始字段应按表格读取，不能当作目录链接。" * 12)
    assert 400 <= len(body) < 1200
    gate = research._source_quality_gate(
        question="示例系统内容标识方法的记录详情和参数 Parameter Value Transport HTTPS Retry bounded",
        result={"url": "https://example.test/record/12345", "sourceQualityHints": {"authorityScore": 80}},
        read_payload={"ok": True, "status": 200, "text": body, "title": "Record details"},
        source_policy="authoritative",
    )
    assert gate["selectedForEvidence"] is True, gate
    assert "navigation_or_footer_like_text" not in gate["readabilityReasons"]


def test_pipe_separated_navigation_is_still_noise():
    assert "navigation_or_footer_like_text" in research._source_noise_reasons(
        "Home | About | Privacy | Contact | News | Jobs | Search | Login | Register"
    )


@pytest.mark.parametrize("failure", ["captcha", "http-error", "navigation-page", "snippet-only"])
def test_table_shape_does_not_override_access_read_or_navigation_failure(failure):
    body = "| Parameter | Value |\n| --- | --- |\n| Transport | HTTPS |\n" + "A documented protocol field and its value. " * 14
    result = {
        "url": "https://example.test/protocol", "snippet": body,
        "sourceQualityHints": {"authorityScore": 90},
    }
    read = {"ok": True, "status": 200, "text": body, "title": "Protocol fields"}
    if failure == "captcha":
        read["text"] = "verify you are human\n" + body
    elif failure == "http-error":
        read.update(ok=False, status=503)
    elif failure == "navigation-page":
        result["url"] = "https://docs.python.org/3/genindex.html"
    else:
        read = {}
    gate = research._source_quality_gate(
        question="Protocol field transport HTTPS", result=result,
        read_payload=read, source_policy="authoritative",
    )
    assert gate["selectedForEvidence"] is False


def test_flattened_label_value_excerpt_remains_a_canonical_fact():
    from core.tools.research_claim_plan import build_canonical_claim_plan

    excerpt = "| Record: EXAMPLE 12345-2025 | Name: Example content labeling | Status: active | Version: final | Scope: public |"
    plan = build_canonical_claim_plan(
        question="What is the record's name and status?",
        sources=[{
            "citationKey": "S1", "tier": "primary", "text": excerpt,
            "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
        }],
        required_source_keys=["S1"], required_facet_ids=[],
        minimum_source_count=1, minimum_claim_count=1, target_claim_count=1,
    )
    assert plan["claimTable"][0]["evidenceExcerpt"] == excerpt
