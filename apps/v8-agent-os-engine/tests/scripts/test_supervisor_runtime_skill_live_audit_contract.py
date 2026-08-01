from __future__ import annotations

import hashlib
from types import SimpleNamespace

import core.database as database_module
from core.tools.research_quality import research_acceptance_metrics
from tests.runtime_core.test_runtime_episode_runner import _accepted_research_payload
from tests.scripts import run_supervisor_runtime_skill_live_audit as audit


def test_pure_research_submit_uses_research_mode_without_engineering_lane(monkeypatch):
    captured: dict = {}

    def fake_request(_url, *, method, payload, timeout):
        captured.update(payload)
        return {"session_id": payload["session_id"], "run_id": "run-pure-research"}

    monkeypatch.setattr(audit, "_json_request", fake_request)
    case = audit._case_specs(audit.PURE_RESEARCH_CASE_ID)[0]

    result = audit._submit_case(
        "http://127.0.0.1:19532",
        case=case,
        model_profile="configured",
        timestamp="20260730T170000Z",
        workspace=r"E:\workspace\v8-agent-os",
    )

    assert result.status == "submitted"
    assert captured["data"]["supervisorWorkMode"] == "daily"
    assert captured["data"]["supervisorRuntimeMode"] == "research"
    assert captured["data"]["engineeringMode"] == "off"
    assert "research episode" not in case.prompt.lower()


def test_final_text_prefers_completed_research_delivery_over_later_short_assistant_message():
    delivery = "\n\n".join(
        [
            "截至 2026 年 7 月 29 日，通用目的 AI 模型的合规时间线需要区分法规原文和后续指南。",
            "系统性风险门槛、透明度、版权和模型文档义务必须分别核对，并保留来源约束。",
            "既有模型过渡规则、Code of Practice、执法罚则与上线行动清单构成完整交付。",
        ]
    )
    messages = [
        {
            "role": "assistant",
            "run_id": "run-research",
            "state": "completed",
            "ordinal": 2,
            "content_text": delivery,
        },
        {
            "role": "assistant",
            "run_id": "run-research",
            "state": "completed",
            "ordinal": 3,
            "content_text": "Research handoff 已回流。",
        },
    ]

    selected = audit._extract_final_text(
        messages,
        preferred_run_id="run-research",
        min_effective_chars=80,
    )

    assert selected == delivery


def test_final_text_keeps_later_short_blocker_instead_of_hiding_it_behind_old_delivery():
    messages = [
        {
            "role": "assistant",
            "run_id": "run-research",
            "state": "completed",
            "ordinal": 2,
            "content_text": "A detailed but now invalid answer. " * 200,
        },
        {
            "role": "assistant",
            "run_id": "run-research",
            "state": "completed",
            "ordinal": 3,
            "content_text": "无法交付：独立复核发现关键证据不成立。",
        },
    ]

    selected = audit._extract_final_text(
        messages,
        preferred_run_id="run-research",
        min_effective_chars=80,
    )

    assert selected == "无法交付：独立复核发现关键证据不成立。"


def test_research_handoff_assessment_recomputes_instead_of_trusting_forged_metrics():
    answer = "A sufficiently long-looking answer body " * 300
    payload = {
        "kind": "research_evidence_bundle",
        "status": "ready",
        "deliveryReady": True,
        "coverageComplete": True,
        "reviewDecision": "accept",
        "qualityTier": "high_quality",
        "answer": answer,
        "answerSha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "sources": [
            {
                "sourceId": f"source-{index}",
                "citationKey": f"[S{index}]",
                "url": f"https://source-{index}.example/report",
                "selectedForEvidence": True,
            }
            for index in range(1, 9)
        ],
        "qualityMetrics": {
            "effectiveAnswerChars": 9000,
            "selectedSourceCount": 8,
            "distinctHostCount": 8,
            "retrievedSourceCount": 8,
            "freshRetrievedSourceCount": 8,
            "readVerifiedSourceCount": 8,
            "datedSourceCount": 8,
            "claimCount": 8,
            "uniqueClaimCount": 8,
            "supportedClaimCount": 8,
            "evidenceVerifiedClaimCount": 8,
            "claimSupportedSourceCount": 8,
            "answerCitedSourceCount": 8,
            "answerCitedContentUnitCount": 8,
            "asOfCurrent": True,
            "independentReviewAccepted": True,
        },
        "taskBriefResults": [
            {
                "taskBriefId": "research-1",
                "query": "current compliance facts",
                "status": "ready",
                "acceptancePassed": True,
                "reviewDecision": "accept",
                "qualityTier": "high_quality",
                "answer": answer,
                "answerSha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            }
        ],
    }

    assessment = audit._research_handoff_assessment(payload, question="current compliance facts")

    assert assessment["highQuality"] is False
    assert "recomputed_high_quality" in assessment["failedChecks"]
    assert "advertised_metrics_match_recomputed" in assessment["failedChecks"]
    assert assessment["qualityIssues"]
    assert assessment["advertisedMetricMismatches"]


def test_research_handoff_assessment_accepts_complete_projected_review_binding():
    question = "Verify the current compliance timeline with fresh primary evidence."
    raw = _accepted_research_payload("bundle-valid", question)
    answer = raw["researchAnswerPack"]["answer"]
    sources = raw["researchAnswerPack"]["sources"]
    claims = raw["researchAnswerPack"]["claimTable"]
    review = raw["independentReview"]
    model_synthesis = raw["finalExperiencePack"]["modelSynthesis"]
    metrics = research_acceptance_metrics(raw)
    unit = {
        "taskBriefId": "research-1",
        "query": question,
        "status": "ready",
        "acceptancePassed": True,
        "reviewDecision": "accept",
        "qualityTier": "high_quality",
        "freshness": "current",
        "asOf": "2026-07-28",
        "answer": answer,
        "answerSha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "sources": sources,
        "sourceUrls": [source["url"] for source in sources],
        "claimTable": claims,
        "independentReview": review,
        "modelSynthesis": model_synthesis,
        "experienceReuse": raw["experienceReuse"],
        "forceRefreshRequested": True,
        "qualityMetrics": metrics,
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }
    payload = {
        "kind": "research_evidence_bundle",
        "status": "ready",
        "deliveryReady": True,
        "coverageComplete": True,
        "reviewDecision": "accept",
        "qualityTier": "high_quality",
        "asOf": "2026-07-28",
        "answer": answer,
        "answerSha256": unit["answerSha256"],
        "sources": sources,
        "sourceUrls": unit["sourceUrls"],
        "claimTable": claims,
        "independentReview": review,
        "modelSynthesis": model_synthesis,
        "experienceReuse": raw["experienceReuse"],
        "forceRefreshRequested": True,
        "qualityMetrics": metrics,
        "taskBriefResults": [unit],
    }

    assessment = audit._research_handoff_assessment(payload, question=question)

    assert assessment["highQuality"] is True
    assert assessment["failedChecks"] == []
    assert assessment["qualityIssues"] == []
    assert assessment["advertisedMetricMismatches"] == {}


def test_pure_research_audit_recognizes_exact_dates_semantics_and_all_direct_web_tools():
    text = (
        "截至 2026 年 7 月 29 日，GPAI 通用目的 AI 的合规时间线包含 2025-08-02、"
        "2026 年 8 月 2 日和 2 August 2027。系统性风险门槛是 10^25 FLOP，并应分别说明"
        "透明度、版权、模型文档、既有模型过渡规则、Code of Practice、罚款执法、"
        "法规原文、欧盟委员会与 AI Office 指南和行业实践仍待明确之处，最后提供上线前可执行清单。"
    )

    dates = audit._normalized_date_evidence(text)
    coverage = audit._pure_research_semantic_coverage(text)

    assert {"2026-07-29", "2025-08-02", "2026-08-02", "2027-08-02"}.issubset(dates)
    assert all(coverage.values())
    assert {"web_search", "web_broker", "web_read", "web_fetch", "web_extract"}.issubset(
        audit.SUPERVISOR_DIRECT_WEB_TOOLS
    )


def test_terminal_probe_does_not_treat_missing_local_run_as_completed(monkeypatch):
    fake_db = SimpleNamespace(
        get_run_record=lambda _run_id: None,
        list_runtime_episodes=lambda **_kwargs: [],
    )
    monkeypatch.setattr(database_module, "db", fake_db)
    result = audit.LiveCaseResult(
        spec=audit.LiveCaseSpec(case_id="profile-affinity", title="profile affinity", prompt="test"),
        session_id="session-on-remote-engine",
        run_id="run-not-in-local-profile",
    )

    terminal, facts = audit._load_run_terminal(result)

    assert terminal is False
    assert facts["runRecordFound"] is False
    assert facts["runRecordMissing"] is True


def test_poll_case_uses_matching_api_terminal_when_local_profile_has_no_run(monkeypatch):
    class FakeClock:
        now = 0.0

        def time(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = FakeClock()
    responses = iter(
        [
            {
                "events": [
                    {
                        "seq": 7,
                        "topic": "run.completed",
                        "run_id": "run-remote",
                        "payload": {"status": "finished", "reason": "stream_finished"},
                    }
                ]
            },
            {"events": []},
            {"events": []},
            {"events": []},
        ]
    )
    monkeypatch.setattr(audit.time, "time", clock.time)
    monkeypatch.setattr(audit.time, "sleep", clock.sleep)
    monkeypatch.setattr(audit, "_json_request", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(
        audit,
        "_load_run_terminal",
        lambda _result: (False, {"runRecordFound": False, "runRecordMissing": True}),
    )
    monkeypatch.setattr(audit, "_load_durable_runtime_events", lambda _result: ([], None))
    monkeypatch.setattr(audit, "_load_durable_episode_facts", lambda _result: ([], [], None))
    monkeypatch.setattr(audit, "_load_canonical_messages", lambda _result: ([], None))
    result = audit.LiveCaseResult(
        spec=audit.LiveCaseSpec(case_id="profile-affinity", title="profile affinity", prompt="test"),
        session_id="session-on-remote-engine",
        run_id="run-remote",
        status="submitted",
    )

    completed = audit._poll_case("http://127.0.0.1:19532", result, max_wait=30)

    assert completed.status == "completed"
    assert completed.failure_reason is None
    assert "api_events" in " ".join(completed.key_events)


def test_api_terminal_ignores_other_run_and_preserves_remote_failure():
    terminal, facts = audit._api_run_terminal_facts(
        [
            {
                "seq": 8,
                "topic": "run.completed",
                "run_id": "run-old",
                "payload": {"status": "finished"},
            },
            {
                "seq": 9,
                "topic": "run.state.changed",
                "run_id": "run-current",
                "payload": {"from_status": "running", "to_status": "failed", "reason": "provider timeout"},
            },
        ],
        run_id="run-current",
    )

    assert terminal is True
    assert facts["apiTerminalStatus"] == "failed"
    assert facts["apiTerminalError"] == "provider timeout"
    assert facts["apiTerminalRunId"] == "run-current"
