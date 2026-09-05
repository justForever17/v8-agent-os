from __future__ import annotations

import copy
import hashlib
import importlib

import pytest

from core.tools.research_claim_plan import build_canonical_claim_plan, structure_material
from core.tools.research_review_prompt import build_review_prompt, REVIEW_SYSTEM_PROMPT


research = importlib.import_module("core.tools.research_broker")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("configured,requested,expected", [(4096, 12000, 4096), (4096, 1800, 1800), (None, 1800, 1800)])
def test_research_stage_budget_respects_real_model_configuration(asynchronous, configured, requested, expected):
    seen = []
    class Model:
        _meta = {"global_max_tokens": configured}
        def invoke(self, _messages, **kwargs):
            seen.append(kwargs)
            return "ok"
    llm = Model()
    if asynchronous:
        async def ainvoke(messages, **kwargs):
            return llm.invoke(messages, **kwargs)
        llm.ainvoke = ainvoke
    assert research._invoke_architect_candidate_with_deadline(
        (llm, "configured::model", "research"), [], seconds=2, max_tokens=requested,
    ) == "ok"
    assert seen[0]["max_tokens"] == expected
    assert llm._meta == {"global_max_tokens": configured}


@pytest.mark.parametrize("query_count", [1, 12, 38])
def test_multi_query_duplicates_do_not_consume_unique_excerpt_capacity(monkeypatch, query_count):
    candidates = [{"text": f"The shared document contains exact independent obligation number {i}.",
                   "evidenceExcerptKey": f"S1:E{i}"} for i in range(1, 7)]
    def rank(_source, _question, *, limit):
        return copy.deepcopy(candidates[:limit])
    monkeypatch.setattr(research, "_architect_evidence_candidates", rank)
    result = research._architect_multi_query_evidence_candidates(
        {"citationKey": "S1"}, [f"Document query {i}" for i in range(query_count)],
    )
    assert [row["text"] for row in result] == [row["text"] for row in candidates]
    assert len(result) <= research._RESEARCH_ARCHITECT_MAX_CLAIM_COUNT


def test_claim_budget_prioritizes_named_documents_after_source_coverage():
    sources = []
    for i, title in enumerate(["背景材料", "服务甲规则", "服务乙规则"], 1):
        facts = [f"Document {i} establishes operating obligation {j} with a distinct condition."
                 for j in range(1, 5)]
        sources.append({"citationKey": f"S{i}", "title": title, "text": "\n\n".join(facts),
                        "evidenceCandidates": [{"text": fact, "evidenceExcerptKey": f"S{i}:E{j}"}
                                               for j, fact in enumerate(facts, 1)]})
    before = copy.deepcopy(sources)
    plan = build_canonical_claim_plan(
        question="对比《服务甲规则》和《服务乙规则》，保留相关背景来源。", sources=sources,
        required_source_keys=["S1", "S2", "S3"], required_facet_ids=[],
        minimum_source_count=3, minimum_claim_count=3, target_claim_count=7,
    )
    keys = [row["supportingSources"][0]["citationKey"] for row in plan["claimTable"]]
    assert {key: keys.count(key) for key in set(keys)} == {"S1": 1, "S2": 3, "S3": 3}
    assert all(row["supportingSources"][0]["sourceRole"] == "unknown" for row in plan["claimTable"])
    assert sources == before


@pytest.mark.parametrize("before,after", [
    ("The service applies only to public access [S1].", "The service applies only to public access. [S1]"),
    ("该服务只适用于面向公众的场景 [S1]。", "该服务只适用于面向公众的场景。[S1]"),
    ("Both records confirm the same boundary [S1][S2].", "Both records confirm the same boundary. [S1] [S2]"),
])
def test_citation_coverage_accepts_same_line_references_after_sentence_punctuation(before, after):
    from core.tools.research_quality import _answer_cited_content_unit_count
    sources = [{"citationKey": "S1"}, {"citationKey": "S2"}]
    assert _answer_cited_content_unit_count(before, sources) == 1
    assert _answer_cited_content_unit_count(after, sources) == 1
    assert _answer_cited_content_unit_count("[S1] [S2]", sources) == 0
    assert _answer_cited_content_unit_count("Unrelated text. [S99]", sources) == 0
    assert _answer_cited_content_unit_count("Uncited fact.\n\n## Sources\n[S1]", sources) == 0


def test_claim_budget_deepens_chosen_documents_instead_of_adding_unrequested_sources():
    sources = []
    for i in range(1, 7):
        facts = [f"Document {i} establishes the exact operating boundary for service clause {j}." for j in range(1, 5)]
        sources.append({
            "citationKey": f"S{i}", "text": "\n\n".join(facts),
            "evidenceCandidates": [{"text": fact, "evidenceExcerptKey": f"S{i}:E{j}"} for j, fact in enumerate(facts, 1)],
        })
    before = copy.deepcopy(sources)
    plan = build_canonical_claim_plan(
        question="Explain selected service documents", sources=sources,
        required_source_keys=["S1", "S2"], required_facet_ids=[],
        minimum_source_count=2, minimum_claim_count=2, target_claim_count=6,
    )
    assert {row["supportingSources"][0]["citationKey"] for row in plan["claimTable"]} == {"S1", "S2"}
    assert {row["evidenceExcerptKey"] for row in plan["claimTable"]} == {f"S{i}:E{j}" for i in (1, 2) for j in (1, 2, 3)}
    assert sources == before


@pytest.mark.parametrize("heading", ["# 示例服务管理办法", "\n\n# 示例服务管理办法\n"])
def test_article_boundary_keeps_the_promulgation_paragraph_out_of_navigation(heading):
    preamble = ("示例管理局\n示例发展委员会\n令\n第15号\n"
                "《示例服务管理办法》已经审议通过，现予公布，自2010年8月15日起施行。")
    body = ("- 首页\n- 机构设置\n- 机构职能\n- 机关厅局\n- 直属单位\n- 政务服务\n"
            + heading + "\n" + preamble + "\n\n第一条 本办法规范公开服务的适用范围，内部研究不适用。")
    source = {"citationKey": "S1", "title": "示例服务管理办法", "text": body}
    question = "示例服务管理办法 发布机关 文号 发布日期 施行日期 全文"
    before = copy.deepcopy(source)
    candidates = research._architect_evidence_candidates(source, question, limit=4)
    assert any("第15号" in row["text"] and "示例管理局" in row["text"] for row in candidates)
    assert all("机构职能" not in row["text"] for row in candidates)
    assert source == before


@pytest.mark.parametrize("view_count", [2, 38])
@pytest.mark.parametrize("distinct_body", [False, True])
def test_identical_read_views_do_not_expand_or_crop_the_document_before_claim_selection(view_count, distinct_body):
    body = ("公开服务管理规则保护公众权益，服务提供者应履行管理义务，内部研究不适用。\n\n" * 80
            + "第二十四条 本管理规则自2010年8月15日起施行。")
    query = "核查公开服务管理规则的适用范围和施行日期。"
    url = "https://official.example/rule"
    views = [{"shardId": f"shard-{i}", "researchFacetId": "scope-date", "evidenceQuery": query}
             for i in range(view_count)]
    sources = [{"sourceId": "rule", "citationKey": "S1", "title": "公开服务管理规则", "url": url,
                "tier": "primary", "authorityScore": 90, "selectedForEvidence": True, "evidenceViews": views}]
    shards = [{"shardId": view["shardId"], "query": query, "fetchedTopSources": [
        {"ok": True, "url": url, "title": "公开服务管理规则", "text": body, "retrievedAt": "2026-09-05T00:00:00Z"},
    ]} for view in views]
    if distinct_body:
        shards[-1]["fetchedTopSources"][0]["text"] = body.replace("2010年", "2011年")
    before = copy.deepcopy((sources, shards))
    projected = research._research_architect_sources_for_prompt(sources, shards, question=query)
    assert len(projected) == 1
    if distinct_body:
        assert body in projected[0]["text"]
        assert body.replace("2010年", "2011年") in projected[0]["text"]
    else:
        assert projected[0]["text"] == body
    assert len(projected[0]["evidenceViews"]) == view_count
    candidates = research._architect_multi_query_evidence_candidates(projected[0], [query])
    assert any("2010年8月15日" in candidate["text"] for candidate in candidates)
    plan = build_canonical_claim_plan(
        question=query, sources=[{**projected[0], "evidenceCandidates": candidates}],
        required_source_keys=["S1"], required_facet_ids=[], minimum_source_count=1,
        minimum_claim_count=1, target_claim_count=2,
    )
    assert any("2010年8月15日" in claim["evidenceExcerpt"] for claim in plan["claimTable"])
    assert (sources, shards) == before


@pytest.mark.parametrize("host", ["example.gov.cn", "example.edu.cn", "example.com"])
def test_retrieval_rank_never_becomes_first_hand_document_identity(host):
    source = {"citationKey": "S1", "tier": "primary", "authorityScore": 100,
              "authorityTier": "primary", "catalogCategory": "public_institution",
              "runtimeOfficialSeed": True, "url": f"https://{host}/reprint",
              "title": "Source document", "text": "A reproduced record describes the public deployment boundary."}
    source["evidenceCandidates"] = [{"text": source["text"], "evidenceExcerptKey": "S1:E1"}]
    before = copy.deepcopy(source)
    plan = build_canonical_claim_plan(
        question="Explain the deployment boundary", sources=[source],
        required_source_keys=["S1"], required_facet_ids=[],
        minimum_source_count=1, minimum_claim_count=1, target_claim_count=1,
    )
    assert plan["claimTable"][0]["supportingSources"][0]["sourceRole"] == "unknown"
    verified, issues = research._verify_architect_claim_excerpts(plan["claimTable"], [source], require_evidence_key=True)
    assert issues == []
    assert verified[0]["sourceRole"] == "unknown"
    assert research._research_source_pack(source)["sourceRole"] == "unknown"
    answer = research._assemble_architect_sections(
        question="请用中文回答", verified_plan={"asOf": "2026-09-04"},
        sections=["已读取该文档所描述的边界 [S1]。"], sources=[source],
    )
    assert "一手来源" not in answer
    assert answer.startswith("# 调研结论")
    assert source == before


def test_long_handoff_exposes_binding_index_before_narrative_without_loss():
    from core.research_handoff_surface import render_research_handoff_evidence

    answer = "完整调研正文和已确认限制。" * 2000
    payload = {"answer": answer, "answerSha256": "digest", "claimTable": [{
        "claimId": "claim-exact", "evidenceExcerptKey": "S7:E2", "evidenceExcerpt": "Only public deployments apply.",
        "supportingSources": [{"citationKey": "S7", "url": "https://mirror.example/doc"}],
    }]}
    before = copy.deepcopy(payload)
    text = render_research_handoff_evidence(payload)
    assert "claim-exact | [S7] | https://mirror.example/doc | excerpt S7:E2" in text[:2500]
    assert text.index("## Claim-to-source bindings") < text.index("## Accepted answer")
    assert answer in text
    assert "Only public deployments apply." in text
    assert payload == before


@pytest.mark.parametrize("question", [
    "调研法规，回流后由 Supervisor 委派 Verification Engineer 独立核验，再给最终答案。",
    "Research deployment methods, then deploy the app after approval.",
    "调研如何设计独立验证流程及部署方法，并说明已知限制。",
])
def test_review_scope_preserves_question_but_does_not_require_future_actions(question):
    prompt = build_review_prompt(question)
    assert prompt.endswith(f"QUESTION: {question}")
    assert "not downstream actions" in REVIEW_SYSTEM_PROMPT
    assert "尚未执行不是当前答案缺证" in prompt
    assert "也不得把它们加入 criticalMissingEvidence 或 recommendedNextQueries" in prompt
    assert "必须拒绝该无证据声明" in prompt
    assert "这些知识仍属调研范围" in prompt


@pytest.mark.parametrize("excerpt", [
    "This document describes the public service deployment process and explains the operational "
    "boundary for providers, integrators and internal research teams. It covers the different "
    "responsibilities for deploying, monitoring and auditing services, with attention to local "
    "implementation conditions and documented exceptions. Only providers offering the service "
    "to the public must register; internal research without public access is excluded.",
    "本文件说明服务部署的适用范围、使用场景、责任主体和审查条件，并介绍研究机构开展内部测试时需要关注的技术背景。"
    "文件分别讨论服务提供者、集成商与内部研究团队的不同职责，并强调部署、监测和审计活动需要根据实际应用条件进行安排。"
    "这些背景说明用于解释术语和职责边界，不能脱离文件全文或仅凭标题推断所有研发活动都承担完全相同的义务。"
    "本文件进一步列举部署阶段的技术说明、上线前的检查事项、运行过程中的管理记录、变更之后的持续核对和相关资料的保留方式。"
    "各参与方应先判断服务的实际开放范围，区分内部实验、受限验证和向公众开放等不同阶段，再结合具体条款核对各自责任。"
    "有关说明需要连同适用条件和例外一起阅读，不能把背景中的技术发展趋势直接改写成对所有参与者的普遍要求。"
    "仅向公众提供服务的提供者须登记；不向公众开放的内部研究不适用这一登记要求。",
], ids=["english-scope-and-exception", "chinese-scope-and-exception"])
def test_long_bound_evidence_keeps_operating_condition_and_exception_through_delivery(excerpt):
    assert 320 < len(excerpt) <= 600
    source = {
        "citationKey": "S1", "sourceId": "source-1", "tier": "primary",
        "url": "https://example.gov/document", "title": "Service deployment process",
        "text": excerpt, "evidenceQuery": "service deployment process",
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }
    before = copy.deepcopy(source)
    plan = build_canonical_claim_plan(
        question="What applies to internal research and public service providers?",
        sources=[source], required_source_keys=["S1"], required_facet_ids=[],
        minimum_source_count=1, minimum_claim_count=1, target_claim_count=1,
    )
    claim = plan["claimTable"][0]
    assert claim["claim"] == excerpt
    verified, issues = research._verify_architect_claim_excerpts(
        [claim], [source], require_evidence_key=True,
    )
    assert issues == []
    assert verified[0]["claim"] == excerpt
    assert verified[0]["evidenceExcerpt"] == excerpt
    assert verified[0]["evidenceExcerptSha256"] == hashlib.sha256(excerpt.lower().encode()).hexdigest()
    assert research._compact_visible_claim(verified[0])["claim"] == excerpt
    assert source == before

    secondary = {**source, "tier": "secondary", "sourceRole": "secondary"}
    secondary_plan = build_canonical_claim_plan(
        question="Compare official sources", sources=[secondary],
        required_source_keys=["S1"], required_facet_ids=[],
        minimum_source_count=1, minimum_claim_count=1, target_claim_count=1,
    )
    attributed, _ = research._architect_govern_claim_source_roles_for_question(
        secondary_plan["claimTable"], "Compare official sources",
    )
    attributed_verified, issues = research._verify_architect_claim_excerpts(
        attributed, [secondary], require_evidence_key=True,
    )
    assert issues == []
    delivered = research._compact_visible_claim(attributed_verified[0])
    assert delivered["sourceClaim"] == excerpt
    assert delivered["claim"].endswith(excerpt)


@pytest.mark.parametrize("cue", ["prohibits", "forbids", "shall not", "may not"])
def test_canonical_prohibition_cue_is_accepted_by_exact_excerpt_verifier(cue):
    excerpt = f"The service contract {cue} submit duplicate external writes after an ambiguous timeout."
    source = {
        "citationKey": "S1", "tier": "primary", "url": "https://example.gov/contract",
        "text": excerpt,
        "evidenceCandidates": [{"evidenceExcerptKey": "S1:E1", "text": excerpt}],
    }
    plan = build_canonical_claim_plan(
        question="What is prohibited?", sources=[source],
        required_source_keys=["S1"], required_facet_ids=[],
        minimum_source_count=1, minimum_claim_count=1, target_claim_count=1,
    )
    verified, issues = research._verify_architect_claim_excerpts(
        plan["claimTable"], [source], require_evidence_key=True,
    )
    assert issues == []
    assert verified[0]["normativeCue"] == cue
    assert verified[0]["claim"] == excerpt


def _ranked_sources():
    return [
        {
            "citationKey": key, "tier": tier, "sourceRole": tier, "authorityScore": score,
            "answerabilityScore": score, "subjectFocused": True,
            "url": f"https://example.test/{key}", "researchFacetIds": ["scope"],
            "text": text,
            "evidenceCandidates": [{
                "text": text, "evidenceExcerptKey": f"{key}:E1",
                "researchFacetId": "scope", "relevanceScore": score,
            }],
        }
        for key, tier, score, text in [
            ("S1", "secondary", 100, "An independent commentary discusses the overall service scope and deployment trends."),
            ("S2", "primary", 75, "The service regulation applies to public deployments; private research is excluded from this scope."),
        ]
    ]


def test_official_source_request_prioritizes_read_primary_body_over_commentary_score():
    sources = _ranked_sources()
    before = copy.deepcopy(sources)
    assert research._architect_required_claim_source_keys(
        sources, limit=1, question="核实适用范围，保留实际读取的官方来源。",
    ) == ["S2"]
    assert sources == before


def test_official_original_request_uses_retrieval_rank_without_fabricating_origin():
    sources = _ranked_sources()
    for source in sources:
        source.pop("sourceRole")
    before = copy.deepcopy(sources)
    assert research._architect_required_claim_source_keys(
        sources, limit=1, question="核实适用范围，定位至少一个官方原文。",
    ) == ["S2"]
    assert all(research._architect_support_role(source) == "unknown" for source in sources)
    assert sources == before


def test_facet_reservation_uses_required_source_without_spending_extra_commentary_slot():
    plan = build_canonical_claim_plan(
        question="Compare the scope from official sources", sources=_ranked_sources(),
        required_source_keys=["S2"], required_facet_ids=["scope"],
        minimum_source_count=1, minimum_claim_count=1, target_claim_count=1,
    )
    assert len(plan["claimTable"]) == 1
    assert plan["claimTable"][0]["evidenceExcerptKey"] == "S2:E1"


def test_structure_projection_keeps_draft_identity_and_source_attribution():
    sources = _ranked_sources()
    sources[1]["title"] = "Public service regulation (consultation draft)"
    sources[1]["version"] = "draft"
    plan = build_canonical_claim_plan(
        question="Compare source obligations", sources=sources,
        required_source_keys=["S1", "S2"], required_facet_ids=[],
        minimum_source_count=2, minimum_claim_count=2, target_claim_count=2,
    )
    before = copy.deepcopy(plan)
    material = structure_material(plan)
    by_key = {source["citationKey"]: source for source in material["citationIndex"]}
    assert by_key["S2"]["title"] == sources[1]["title"]
    assert by_key["S2"]["version"] == "draft"
    assert by_key["S1"]["sourceRole"] == "secondary"
    assert by_key["S2"]["sourceRole"] == "primary"
    assert "text" not in by_key["S2"]
    assert "evidenceCandidates" not in by_key["S2"]
    assert plan == before


def test_review_projection_preserves_source_identity_and_conditional_evidence():
    sources = _ranked_sources()
    sources[0].update(title="Reprinted consultation draft", version="draft", tier="primary")
    plan = build_canonical_claim_plan(
        question="Check scope and original attribution", sources=sources,
        required_source_keys=["S1", "S2"], required_facet_ids=[],
        minimum_source_count=2, minimum_claim_count=2, target_claim_count=2,
    )
    claims, issues = research._verify_architect_claim_excerpts(plan["claimTable"], sources, require_evidence_key=True)
    assert issues == []
    before = copy.deepcopy((plan, sources, claims))
    material = research._architect_review_claim_ledger(claims, sources)
    by_key = {source["citationKey"]: source for source in material["citationIndex"]}
    assert by_key["S1"]["sourceRole"] == "secondary"
    assert by_key["S1"]["version"] == "draft"
    assert by_key["S2"]["sourceRole"] == "primary"
    for row in material["claims"]:
        original = next(claim for claim in claims if claim["claimId"] == row["claimId"])
        assert row["exactEvidenceExcerpt"] == original["evidenceExcerpt"]
        assert row["sourceRole"] == original["sourceRole"]
    assert (plan, sources, claims) == before
