from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.tools.research_quality import build_research_review_binding
from tests.scripts import run_research_runtime_deep_live_audit as audit


@pytest.mark.parametrize("behavior", ["correct", "always_accept", "always_reject", "invalid_schema"])
@pytest.mark.parametrize("variant", ["scope", "version", "metadata", "request_origin"])
def test_semantic_review_contrast_detects_false_success_and_false_veto(monkeypatch, behavior, variant):
    from core.tools import research_broker as research
    from langchain_core.messages import AIMessage

    pool = [(object(), "configured-first", "supervisor"), (object(), "configured-second", "verification")]
    monkeypatch.setattr(research, "_create_web_research_architect_llm_candidates", lambda: pool)
    monkeypatch.setattr(research, "_create_web_research_reviewer_llm_candidates", lambda _: pool)
    monkeypatch.setattr(research, "_architect_candidate_identity", lambda c: c[1])
    monkeypatch.setattr(research, "_architect_candidate_context_model_ref", lambda c: c[1])
    calls = []

    def invoke(candidate, messages, **kwargs):
        text = "\n".join(str(message.content) for message in messages)
        assert '"citationKey": "S1"' in text
        if variant == "scope":
            assert "第三方转载" in text and "Verification Engineer" in text
        elif variant == "version":
            assert "2020-01-01" in text and "4.2" in text and "Linux" in text
        elif variant == "metadata":
            assert "2023-07-13" in text and "第15号" in text
        else:
            assert "Supervisor 转述" in text and "ZX 2041" in text
        assert 0 < kwargs["seconds"] <= 120 and kwargs["max_tokens"] is None
        assert kwargs["tool_choice"] == "required"
        expected = audit._semantic_review_contrast_cases(variant)[len(calls) % 3][2]
        calls.append(candidate[1])
        accept = expected if behavior == "correct" else behavior == "always_accept"
        candidate = json.loads(messages[1].content)["candidate"]
        payload = {"decision": "accept" if accept else "revise", "coverage": "complete",
                   "corrections": [] if accept else [{"kind": "fact", "answerQuote": candidate["answer"], "reason": "counterexample"}]}
        return AIMessage(content="", tool_calls=[{"name": "review_research_answer", "id": f"review-{len(calls)}",
                                                  "args": {} if behavior == "invalid_schema" else payload}])

    monkeypatch.setattr(research, "_invoke_architect_candidate_with_deadline", invoke)
    result = audit._run_semantic_review_contrast_case(variant)
    assert len(calls) == (18 if behavior == "invalid_schema" else 3)
    assert result.status == ("ok" if behavior == "correct" else "failed")
    assert len(result.evidence) == 3
    assert all(json.loads(row)["evidenceMode"] == "synthetic-evidence-real-provider-contrast" for row in result.evidence)


def test_semantic_review_contrast_requires_live(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["audit", "--case", "semantic_review_contrast"])
    monkeypatch.setitem(audit.CASES, "semantic_review_contrast", lambda: pytest.fail("unauthorized provider call"))
    assert audit.main() == 2


@pytest.mark.parametrize("review_flags", [[], ["--review-only", "--original-request-file", "original.txt", "--expect-review-decision", "revise"]])
def test_fixed_bundle_cli_still_requires_explicit_live(monkeypatch, tmp_path, review_flags):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Read input or invoked live code without --live")

    monkeypatch.setattr(sys, "argv", ["audit", "--fixed-bundle", str(tmp_path / "ledger.json"), *review_flags])
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(audit, "_run_fixed_bundle_case", forbidden)
    monkeypatch.setattr(audit, "_review_fixed_candidate", forbidden)
    assert audit.main() == 2


def _accepted_review_bundle():
    body = "发布机关正文：规范编号 ZX 2041。本文不包含任何用户对话。"
    source = {"citationKey": "S1", "sourceId": "fixture-source", "url": "https://example.org/fact", "text": body,
              "readEvidence": {"verified": True, "contentSha256": hashlib.sha256(body.encode()).hexdigest()}}
    return {"evidenceBundleId": "accepted-fixture", "question": "Supervisor 派生任务：核查 ZX 2040。",
            "answer": "用户把 ZX 2040 写错了。\n保留原答案换行。[S1]", "deliveryScope": "partial",
            "limitations": ["没有逐字用户引文。"], "reviewDecision": "accept",
            "sourceMatrix": [{"url": "https://example.org/fact"}],
            "shards": [{"fetchedTopSources": [{"text": "Frozen source"}]}],
            "researchEvidenceBank": {"sources": [source]},
            "researchResult": {"modelSynthesis": {"trace": [
                {"name": "read_research_source", "citationKey": "S1", "start": 0, "end": len(body)},
                {"stage": "review_input"},
            ]}}}


@pytest.mark.parametrize("stored_draft", [False, True])
def test_stored_review_candidate_keeps_accepted_answer_or_existing_draft(stored_draft):
    bundle = _accepted_review_bundle()
    expected = {"answer": bundle["answer"], "coverage": "partial", "limitations": list(bundle["limitations"])}
    if stored_draft:
        expected = {"answer": "Unaccepted candidate\nkept verbatim", "coverage": "complete", "limitations": ["draft limitation"]}
        bundle["researchResult"] = {"candidateDraft": copy.deepcopy(expected)}
    before = copy.deepcopy(bundle)
    restored = audit._stored_review_candidate(bundle)
    assert restored == expected
    restored["answer"] = "changed"
    restored["limitations"].append("changed")
    assert bundle == before
    bundle.pop("answer")
    bundle.pop("researchResult", None)
    with pytest.raises(ValueError, match="fixed_review_requires_stored_candidate_draft"):
        audit._stored_review_candidate(bundle)  # A derived question is never an answer fallback.


@pytest.mark.parametrize("expected,actual", [("revise", "accept"), ("accept", "revise"), ("revise", "revise"), ("accept", "accept")])
def test_fixed_review_cli_preserves_original_request_and_enforces_expected_decision(monkeypatch, tmp_path, expected, actual):
    bundle = _accepted_review_bundle()
    ledger, original = tmp_path / "ledger.json", tmp_path / "original.txt"
    ledger.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    original_text = "请查规范实施时间。\n请保留说明边界。\n"
    original.write_text(original_text, encoding="utf-8-sig")
    observed = {}

    def review(value, *, original_user_request=""):
        observed["request"] = original_user_request
        assert value == bundle
        return {"modelId": "fixture-reviewer", "review": {"decision": actual}, "trace": [],
                "oracle": "review_protocol_only_not_independent_semantic_acceptance"}

    execute = audit._run_fixed_bundle_case

    def capture(*args, **kwargs):
        observed["result"] = execute(*args, **kwargs)
        return observed["result"]

    monkeypatch.setattr(audit, "_review_fixed_candidate", review)
    monkeypatch.setattr(audit, "_run_fixed_bundle_case", capture)
    monkeypatch.setattr(sys, "argv", ["audit", "--live", "--fixed-bundle", str(ledger), "--review-only",
                                      "--original-request-file", str(original), "--expect-review-decision", expected,
                                      "--output-dir", str(tmp_path / "out")])
    assert audit.main() == (0 if expected == actual else 1)
    assert observed["request"] == original_text and observed["request"] != bundle["question"]
    result = observed["result"]
    assert any("fixed_review_decision_mismatch" in item for item in result.failures) is (expected != actual)
    receipt = next(json.loads(row) for row in result.evidence if "originalRequestSha256" in row)
    assert receipt["originalRequestSha256"] == hashlib.sha256(original_text.encode()).hexdigest()
    assert receipt["expectedReviewDecision"] == expected


@pytest.mark.parametrize("original_request", ["", "请查规范实施时间，未指定编号。"])
@pytest.mark.parametrize("claim_from_derived", [False, True])
def test_fixed_reviewer_wire_separates_original_request_from_derived_question(monkeypatch, original_request, claim_from_derived):
    from core.context_orchestrator import context_orchestrator
    from core.tools import research_broker as research
    from langchain_core.messages import AIMessage

    bundle = _accepted_review_bundle()
    pool = [object()]
    monkeypatch.setattr(research, "_create_web_research_architect_llm_candidates", lambda: pool)
    monkeypatch.setattr(research, "_create_web_research_reviewer_llm_candidates", lambda _: pool)
    monkeypatch.setattr(research, "_architect_candidate_selection_origin", lambda _: "agent_reviewer:verification-engineer")
    monkeypatch.setattr(research, "_architect_candidate_context_model_ref", lambda _: "fixture-reviewer")
    monkeypatch.setattr(research, "_architect_candidate_identity", lambda _: "fixture-reviewer")
    monkeypatch.setattr(context_orchestrator, "prepare", lambda **kwargs: SimpleNamespace(messages=kwargs["messages"]))
    calls = []

    def invoke(_candidate, messages, **kwargs):
        payload = json.loads(messages[1].content)
        assert payload["requestContext"]["originalUserRequest"] == original_request
        assert payload["requestContext"]["questionSource"] == (
            "supervisor_derived_task" if original_request else "unattributed_research_task"
        )
        assert payload["question"] == bundle["question"]
        assert payload["candidate"]["answer"] == bundle["answer"]
        assert payload["candidate"]["limitations"] == bundle["limitations"]
        assert payload["observedSourceKeys"] == ["S1"]
        assert bundle["researchEvidenceBank"]["sources"][0]["text"] in json.dumps(payload["observedPassages"], ensure_ascii=False)
        assert kwargs["max_tokens"] is None and kwargs["tool_choice"] == "required"
        calls.append(payload)
        review = {"decision": "accept" if claim_from_derived else "revise", "coverage": "partial",
                  "limitations": bundle["limitations"], "corrections": [] if claim_from_derived else [
                      {"kind": "attribution", "answerQuote": bundle["answer"], "reason": "派生任务不能证明用户用词。"}],
                  "requestAttribution": {"verdict": "supported" if claim_from_derived else "unverified",
                      "answerQuote": bundle["answer"], "originalRequestQuote": "ZX 2040" if claim_from_derived else "",
                      "explanation": "核对原请求与转述任务的来源。"}}
        return AIMessage(content="", tool_calls=[{"name": "review_research_answer", "id": f"review-{len(calls)}", "args": review}])

    monkeypatch.setattr(research, "_invoke_architect_candidate_with_deadline", invoke)
    if claim_from_derived:
        with pytest.raises(RuntimeError, match="research_review_step_budget_exhausted"):
            audit._review_fixed_candidate(bundle, original_user_request=original_request)
        assert len(calls) == 6  # Reject even a schema-valid accept with a quote found only in the derived task.
    else:
        result = audit._review_fixed_candidate(bundle, original_user_request=original_request)
        assert result["review"]["decision"] == "revise" and len(calls) == 1


@pytest.mark.parametrize("mutate_input", ["", "source", "candidate"])
def test_fixed_review_protocol_keeps_rejection_and_avoids_writer(monkeypatch, tmp_path, mutate_input):
    from core.tools import research_broker as research

    bundle = {"evidenceBundleId": "fixture", "question": "Compare facts",
              "sourceMatrix": [{"url": "https://example.org/fact"}],
              "shards": [{"fetchedTopSources": [{"text": "Exact source text"}]}],
              "researchResult": {"candidateDraft": {"answer": "Candidate kept verbatim"}}}
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr(research, "_web_research_architect_pack", lambda **_: pytest.fail("Review replay called writer"))

    def review(value):
        assert value["researchResult"]["candidateDraft"]["answer"] == "Candidate kept verbatim"
        if mutate_input == "source":
            value["shards"][0]["fetchedTopSources"][0]["text"] = "Changed"
        elif mutate_input == "candidate":
            value["researchResult"]["candidateDraft"]["answer"] = "Changed"
        return {"modelId": "configured", "review": {"decision": "revise"}, "trace": [],
                "oracle": "review_protocol_only_not_independent_semantic_acceptance"}

    monkeypatch.setattr(audit, "_review_fixed_candidate", review)
    result = audit._run_fixed_bundle_case(path, "fixture", tmp_path / "out", review_only=True)
    assert result.status == ("failed" if mutate_input else "ok")
    assert result.case_id == "fixed_review_protocol"
    if not mutate_input:
        proof = json.loads(result.evidence[-1])
        assert proof["reviewDecision"] == "revise"
        assert proof["oracle"] == "review_protocol_only_not_independent_semantic_acceptance"


@pytest.mark.parametrize("attempt_fetch", [False, True])
@pytest.mark.parametrize("prune_original", [False, True])
def test_fixed_bundle_replay_blocks_acquisition_and_preserves_input(monkeypatch, tmp_path, attempt_fetch, prune_original):
    from core.tools import research_broker as research
    from tests.scripts import run_research_runtime_fixed_bundle_acceptance as fixed

    bundle = {"evidenceBundleId": "fixture", "question": "Compare facts",
              "sourceMatrix": [{"url": "https://example.org/fact"}],
              "shards": [{"fetchedTopSources": [{"text": "Exact source text"}]}]}
    before = copy.deepcopy(bundle)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps({"evidenceBundles": [bundle]}), encoding="utf-8")
    monkeypatch.setattr(fixed, "_result_assessment", lambda _: {
        "highQualityIssues": [], "reviewDecision": "accept", "providerModels": ["fixture"],
    })
    original_read = research.web_read.func

    def synthesize(**kwargs):
        kwargs["source_matrix"].clear()
        kwargs["shards"].clear()
        if prune_original:
            ledger_path.write_text(json.dumps({"evidenceBundles": []}), encoding="utf-8")
        if attempt_fetch:
            # Even if runtime catches this exception, the audit must fail.
            try:
                research.web_read.func(url="https://example.org/fact")
            except fixed.EvidenceAcquisitionForbidden:
                pass
        return {"answerMarkdown": "fixture answer"}

    monkeypatch.setattr(research, "_web_research_architect_pack", synthesize)
    case = audit._run_fixed_bundle_case(ledger_path, "fixture", tmp_path)
    assert case.status == ("failed" if attempt_fetch else "ok")
    assert ("fixed_evidence_acquisition_attempted" in case.failures) is attempt_fetch
    assert bundle == before
    assert research.web_read.func is original_read
    inputs = list(tmp_path.glob("input-*.result.json"))
    results = list(tmp_path.glob("synthesis-*.result.json"))
    assert len(inputs) == len(results) == 1
    assert fixed.load_fixed_bundle(inputs[0], bundle_id="fixture") == before
    assert json.loads(results[0].read_text(encoding="utf-8")) == {"answerMarkdown": "fixture answer"}
    evidence = [json.loads(row) for row in case.evidence]
    assert evidence[0]["frozenInputSha256"] == hashlib.sha256(inputs[0].read_bytes()).hexdigest()
    assert evidence[1]["bundleDigest"] == fixed.bundle_digest(before)


def _technical_bundle() -> dict:
    reviewed_at = "2026-07-29T04:00:00Z"
    sources = []
    claims = []
    for index in range(1, 9):
        source_id = f"src_{index}"
        url = f"https://source{index}.example/pathlib"
        source_text = f"Verified source body {index} with enough concrete pathlib guidance."
        sources.append(
            {
                "sourceId": source_id,
                "citationKey": f"S{index}",
                "url": url,
                "host": f"source{index}.example",
                "selectedForEvidence": True,
                "sourceQualityGate": {"selectedForEvidence": True},
                "contentChars": 500,
                "retrievedAt": reviewed_at,
                "readEvidence": {
                    "verified": True,
                    "contentChars": 500,
                    "contentSha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                    "retrievedAt": reviewed_at,
                },
            }
        )
        excerpt = f"Source {index} states a concrete pathlib recommendation and its boundary."
        claims.append(
            {
                "claim": f"Pathlib practice {index} is supported by an independently read source.",
                "claimType": "source_fact",
                "supportingSources": [
                    {
                        "sourceId": source_id,
                        "citationKey": f"S{index}",
                        "url": url,
                    }
                ],
                "evidenceExcerptKey": f"S{index}:E1",
                "evidenceExcerpt": excerpt,
                "evidenceExcerptSha256": hashlib.sha256(excerpt.lower().encode("utf-8")).hexdigest(),
                "evidenceVerified": True,
            }
        )
    answer = "\n\n".join(
        f"## Practice {index}\nConcrete pathlib guidance with operating details and evidence [S{index}]."
        for index in range(1, 9)
    )
    model_synthesis = {
        "used": True,
        "agentId": "web-research-architect",
        "mode": "full_synthesis",
        "writerModelRole": "summary",
        "writerModelId": "fixture-reviewer",
        "reviewerModelRole": "summary",
        "reviewerModelId": "fixture-reviewer",
        "reviewerConsensusCount": 2,
        "reviewerConsensusModelIds": ["fixture-reviewer", "fixture-adversarial-reviewer"],
        "writerMode": "segmented",
        "writerSectionCount": 4,
        "writerRevisionCount": 0,
        "sameEvidenceReviewRejected": False,
        "claimPlanMode": "runtime_canonical",
        "claimPlanVersion": "v8.research_claim_plan.v1",
        "claimPlanDigest": "a" * 64,
        "claimPlanElapsedMs": 3,
        "structureStatus": "skipped_not_required",
        "structureElapsedMs": 0,
        "writerElapsedMs": 1200,
        "reviewElapsedMs": 800,
        "modelPlanCallCount": 0,
    }
    final_pack = {
        "question": "Current pathlib CLI practices",
        "freshness": "current",
        "answer": answer,
        "researchResult": answer,
        "claimTable": copy.deepcopy(claims),
        "sourceUrls": copy.deepcopy(sources),
        "reviewDecision": "accept",
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "asOf": reviewed_at,
        "synthesisMode": "model_agent",
        "modelSynthesis": model_synthesis,
    }
    answer_pack = {
        "answer": answer,
        "sources": copy.deepcopy(sources),
        "claimTable": copy.deepcopy(claims),
        "reviewDecision": "accept",
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "asOf": reviewed_at,
    }
    bundle = {
        "question": "Current pathlib CLI practices",
        "freshness": "current",
        "answer": answer,
        "claimTable": copy.deepcopy(claims),
        "sourceMatrix": copy.deepcopy(sources),
        "sourceUrls": [source["url"] for source in sources],
        "researchAnswerPack": answer_pack,
        "finalExperiencePack": final_pack,
        "reviewDecision": "accept",
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "asOf": reviewed_at,
        "experienceReuse": {"reuseDecision": "refresh", "reason": "forced_live_validation"},
        "researchLoopState": {
            "phase": "research_loop",
            "rounds": [{"round": 1, "queries": ["pathlib current practices"], "readSourceCount": 8}],
            "performance": {
                "synthesisStages": {
                    "claimPlanMode": "runtime_canonical",
                    "modelPlanCallCount": 0,
                }
            },
        },
        "shards": [
            {
                "shardId": "shard_1",
                "kind": "official_docs",
                "query": "pathlib current practices",
                "ok": True,
                "provider": "fixture-search",
                "networkRoute": "global",
                "resultCount": 8,
                "fetchedTopSources": [{"url": source["url"], "ok": True} for source in sources],
            }
        ],
    }
    review_template = {
        "reviewDecision": "accept",
        "reviewReasons": ["The detailed answer is fully supported by the bound evidence."],
        "questionCoverage": True,
        "claimEntailment": True,
        "freshnessAdequacy": True,
        "unsupportedClaims": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }
    consensus_reviews = []
    for review_mode, reviewer_model_id in (
        ("semantic", "fixture-reviewer"),
        ("adversarial", "fixture-adversarial-reviewer"),
    ):
        item = {**review_template, "reviewMode": review_mode}
        item.update(
            build_research_review_binding(
                bundle,
                reviewer_model_id=reviewer_model_id,
                reviewed_at=reviewed_at,
            )
        )
        consensus_reviews.append(item)
    review = {
        **consensus_reviews[0],
        "consensusAccepted": True,
        "consensusReviewCount": 2,
        "consensusReviewerModelIds": [item["reviewerModelId"] for item in consensus_reviews],
        "consensusReviews": consensus_reviews,
    }
    bundle["independentReview"] = copy.deepcopy(review)
    answer_pack["independentReview"] = copy.deepcopy(review)
    final_pack["independentReview"] = copy.deepcopy(review)
    return bundle


def test_high_quality_delivery_recomputes_instead_of_trusting_reported_metrics() -> None:
    fake_metrics = {"independentReviewAccepted": True, "selectedSourceCount": 99}
    payload = {
        "ok": True,
        "deliveryReady": True,
        "qualityTier": "high_quality",
        "qualityMetrics": fake_metrics,
        "reviewDecision": "accept",
        "researchAnswerPack": {
            "reviewDecision": "accept",
            "answer": "too short",
            "sources": [],
            "score": {
                "deliveryReady": True,
                "qualityTier": "high_quality",
                "acceptanceMetrics": fake_metrics,
            },
        },
    }

    failures = audit._high_quality_delivery_failures(payload)

    assert any(item.startswith("recomputed_quality_issue:") for item in failures)
    assert "answer_pack_acceptance_metrics_do_not_match_recomputed" in failures
    assert "top_level_quality_metrics_do_not_match_recomputed" in failures


def test_research_run_passes_force_refresh_to_broker(monkeypatch) -> None:
    from core.tools.research_broker import research_broker

    captured = {}

    def fake_func(**kwargs):
        captured.update(kwargs)
        return json.dumps({"ok": True})

    monkeypatch.setattr(research_broker, "func", fake_func)

    assert audit._research_run("question", force_refresh=True) == {"ok": True}
    assert captured["forceRefresh"] is True


def test_pure_case_forces_a_fresh_research_run(monkeypatch) -> None:
    calls = []

    def fake_research_run(question, **kwargs):
        calls.append({"question": question, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(audit, "_research_run", fake_research_run)
    monkeypatch.setattr(audit, "_high_quality_delivery_failures", lambda _payload: [])
    monkeypatch.setattr(audit, "_pure_research_semantic_failures", lambda _payload: [])

    result = audit._run_pure_research_case()

    assert result.status == "ok"
    assert calls == [
        {
            "question": audit.PURE_RESEARCH_QUESTION,
            "freshness": "current",
            "max_shards": 20,
            "force_refresh": True,
        }
    ]


def test_domestic_delivery_case_restricts_and_restores_source_router(monkeypatch) -> None:
    from core.tools import research_quality, web_fetcher

    original_provider_order = web_fetcher._configured_source_provider_order

    def fake_research_run(question, **kwargs):
        assert "生成式人工智能服务管理暂行办法" in question
        assert kwargs["source_policy"] == "authoritative"
        assert web_fetcher._configured_source_provider_order("global") == [
            "bing_cn",
            "metaso",
            "baidu",
        ]
        return {
            "ok": True,
            "deliveryReady": True,
            "qualityTier": "minimum_qualified",
            "researchAnswerPack": {
                "answer": "有证据绑定的国内网络调研答案。",
                "score": {
                    "qualityTier": "minimum_qualified",
                    "acceptanceMetrics": {"selectedSourceCount": 5},
                },
            },
            "sourceMatrix": [
                {
                    "provider": "bing_cn",
                    "networkRoute": "cn_direct",
                    "title": "生成式人工智能服务管理暂行办法",
                    "selectedForEvidence": True,
                },
                {
                    "provider": "metaso",
                    "networkRoute": "cn_direct",
                    "title": "政策解读",
                    "snippet": "《生成式人工智能服务管理暂行办法》官方解读",
                    "selectedForEvidence": True,
                },
            ],
            "shards": [
                {
                    "provider": "explicit_seed_url",
                    "networkRoute": "direct_read",
                    "fetchedTopSources": [{"ok": True}],
                }
            ],
        }

    monkeypatch.setattr(audit, "_research_run", fake_research_run)
    monkeypatch.setattr(audit, "_persisted_research_bundle", lambda payload: payload)
    monkeypatch.setattr(audit, "_compact_delivery_evidence", lambda _payload: {})
    monkeypatch.setattr(audit, "_persisted_research_diagnostic", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(research_quality, "research_acceptance_issues", lambda _payload: [])

    result = audit._run_domestic_delivery_case()

    assert result.status == "ok"
    assert result.providers == ["bing_cn", "metaso"]
    assert web_fetcher._configured_source_provider_order is original_provider_order


def test_pure_semantic_gate_requires_every_product_and_decision_coverage() -> None:
    answer = (
        "在 Windows 和 PowerShell 上，OpenAI Codex CLI、Claude Code、Gemini CLI 与 "
        "GitHub Copilot CLI 的安装方式和代码库工作流不同。OpenAI、Anthropic、Google "
        "和 GitHub 的官方资料分别说明了工具调用与 MCP 扩展、账号订阅和价格依赖、"
        "隐私边界与常见局限。综合这些证据，以下给出按团队情况划分的选型建议。"
    )

    assert audit._pure_research_semantic_failures({"answer": answer}) == []

    incomplete = audit._pure_research_semantic_failures(
        {"answer": "Windows 用户可以考虑 OpenAI Codex CLI，并查看官方资料。"}
    )

    assert any(item.startswith("pure_research_product_coverage_missing:") for item in incomplete)
    assert "pure_research_decision_coverage_below_target:1/6" in incomplete


def test_reuse_case_refreshes_first_then_allows_zero_network_reuse(monkeypatch) -> None:
    calls = []
    responses = iter(
        [
            {"ok": True, "experienceReuse": {"reuseDecision": "refresh"}},
            {"ok": True, "experienceReuse": {"reuseDecision": "reuse"}},
        ]
    )

    def fake_research_run(question, **kwargs):
        calls.append({"question": question, **kwargs})
        return next(responses)

    monkeypatch.setattr(audit, "_research_run", fake_research_run)
    monkeypatch.setattr(audit, "_high_quality_delivery_failures", lambda _payload: [])
    monkeypatch.setattr(audit, "_pure_research_semantic_failures", lambda _payload: [])
    monkeypatch.setattr(
        audit,
        "_compact_delivery_evidence",
        lambda _payload: {"answerSha256": "bound-answer", "sourceUrls": ["https://source.example"]},
    )

    result = audit._run_reuse_case()

    assert result.status == "ok"
    assert [call.get("force_refresh", False) for call in calls] == [True, False]
    assert all(call["question"] == audit.PURE_RESEARCH_QUESTION for call in calls)
    assert all(call["freshness"] == "current" for call in calls)
    assert all(call["max_shards"] == 20 for call in calls)


def test_technical_runtime_accepts_bound_segmented_fresh_bundle() -> None:
    bundle = _technical_bundle()

    assert audit._technical_runtime_failures({"experienceReuse": bundle["experienceReuse"]}, bundle) == []

    diagnostic = audit._persisted_research_diagnostic(
        {"evidenceBundleId": "research_fixture"},
        bundle=bundle,
    )
    synthesis = diagnostic["finalPack"]["modelSynthesis"]
    assert synthesis["writerMode"] == "segmented"
    assert synthesis["writerSectionCount"] == 4
    assert synthesis["writerRevisionCount"] == 0
    assert synthesis["sameEvidenceReviewRejected"] is False
    assert synthesis["claimPlanMode"] == "runtime_canonical"
    assert synthesis["modelPlanCallCount"] == 0
    assert synthesis["writerElapsedMs"] == 1200
    assert synthesis["reviewElapsedMs"] == 800
    assert diagnostic["searchReceipts"][0]["provider"] == "fixture-search"
    assert len(diagnostic["readReceipts"]) == 8
    assert diagnostic["finalPack"]["independentReview"]["bindingVersion"] == 6


def test_technical_runtime_accepts_single_writer_and_task_shaped_target() -> None:
    bundle = _technical_bundle()
    bundle["deliveryRequirements"] = {
        "mode": "narrow_authoritative_technical",
        "targetSources": 4,
    }
    synthesis = bundle["finalExperiencePack"]["modelSynthesis"]
    synthesis["writerMode"] = "single"
    synthesis["writerSectionCount"] = 0

    assert audit._technical_runtime_failures({}, bundle) == []


def test_technical_runtime_rejects_restored_model_plan_ownership() -> None:
    bundle = _technical_bundle()
    synthesis = bundle["finalExperiencePack"]["modelSynthesis"]
    synthesis["modelPlanCallCount"] = 1
    synthesis["claimPlanMode"] = "model_generated"

    failures = audit._technical_runtime_failures({}, bundle)

    assert "technical_canonical_claim_plan_missing" in failures
    assert "technical_model_plan_call_present" in failures


def test_technical_runtime_rejects_surface_and_review_binding_drift() -> None:
    bundle = _technical_bundle()
    bundle["researchAnswerPack"]["answer"] += " drift"
    bundle["finalExperiencePack"]["independentReview"]["answerSha256"] = "0" * 64

    failures = audit._technical_runtime_failures({}, bundle)

    assert "answer_surface_parity_mismatch" in failures
    assert "independent_review_surface_parity_mismatch" in failures
    assert "technical_independent_review_binding_mismatch" in failures
