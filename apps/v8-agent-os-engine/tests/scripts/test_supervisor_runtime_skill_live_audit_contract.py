from __future__ import annotations

import hashlib
import urllib.error
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import core.database as database_module
from core.tools.research_quality import research_acceptance_metrics
from tests.runtime_core.test_runtime_episode_runner import _accepted_research_payload
from tests.scripts import run_supervisor_runtime_skill_live_audit as audit


def test_unreachable_web_prevents_billable_live_submission(monkeypatch):
    def unreachable(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")
    def unexpected(*_args, **_kwargs):
        raise AssertionError("must not submit a billable run without the requested Web observer")
    monkeypatch.setattr(audit.urllib.request, "urlopen", unreachable)
    monkeypatch.setattr(audit, "_submit_case", unexpected)
    assert audit.main([
        "--live", "--case", "research_delegated_verification",
        "--web-url", "http://127.0.0.1:19527", "--model-profile", "fixture",
    ]) == 2


def test_tool_inventory_does_not_promote_transcript_prose_to_execution():
    handoffs = [{"toolsUsed": ["tool_observation_detail"], "compactTranscript": (
        'ai: 使用工具: write_native_file\ntool: Tool observation detail\n'
        '{"toolName":"run_system_command"}'
    )}]
    assert audit._collect_handoff_tool_names(handoffs) == ["tool_observation_detail"]
    assert audit._collect_handoff_tool_names([{"resultText": "使用工具: web_broker"}]) == []


def test_timed_out_live_case_cancels_only_its_run_without_changing_verdict(monkeypatch):
    calls = []
    def request(url, **kwargs):
        calls.append((url, kwargs))
        return {"transition_event": {"topic": "run.state.changed"}}
    monkeypatch.setattr(audit, "_json_request", request)
    result = audit.LiveCaseResult(
        spec=audit.LiveCaseSpec(case_id="bounded", title="bounded", prompt="test"),
        session_id="session-live", run_id="run-live", status="timeout",
        failure_reason="run_or_episode_not_terminal_within_max_wait",
    )
    audit._cancel_timed_out_case("http://127.0.0.1:19532", result)
    assert len(calls) == 1
    assert calls[0][0] == "http://127.0.0.1:19532/v1/runs/run-live/commands/cancel"
    assert calls[0][1]["method"] == "POST"
    assert result.status == "timeout"
    assert result.failure_reason == "run_or_episode_not_terminal_within_max_wait"
    assert "deadlineCleanup" in " ".join(result.key_events)
    calls.clear()
    result.status = "completed"
    audit._cancel_timed_out_case("http://127.0.0.1:19532", result)
    result.status, result.run_id = "timeout", None
    audit._cancel_timed_out_case("http://127.0.0.1:19532", result)
    assert calls == []


def test_timeout_cleanup_error_stays_visible_and_does_not_hide_live_failure(monkeypatch):
    def request(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(audit, "_json_request", request)
    result = audit.LiveCaseResult(
        spec=audit.LiveCaseSpec(case_id="bounded", title="bounded", prompt="test"),
        session_id="session-live", run_id="run-live", status="timeout",
    )
    audit._cancel_timed_out_case("http://127.0.0.1:19532", result)
    assert result.status == "timeout"
    assert "deadlineCleanupError" in " ".join(result.key_events)
    assert "connection refused" in " ".join(result.key_events)


def test_live_audit_default_workspace_is_product_repository():
    assert audit.REPO_ROOT.name == "v8-agent-os"
    assert (audit.REPO_ROOT / "release-manifest.json").is_file()


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


def test_delegated_research_submit_uses_research_mode_and_requires_verifier(monkeypatch):
    captured: dict = {}

    def fake_request(_url, *, method, payload, timeout):
        captured.update(payload)
        return {"session_id": payload["session_id"], "run_id": "run-research-verifier"}

    monkeypatch.setattr(audit, "_json_request", fake_request)
    case = audit._case_specs(audit.RESEARCH_DELEGATED_VERIFICATION_CASE_ID)[0]

    result = audit._submit_case(
        "http://127.0.0.1:19532",
        case=case,
        model_profile="configured",
        timestamp="20260903T010000Z",
        workspace=str(audit.REPO_ROOT),
    )

    assert result.status == "submitted"
    assert captured["data"]["supervisorRuntimeMode"] == "research"
    assert captured["data"]["engineeringMode"] == "off"
    assert case.expected_episode_kinds == ["research", "delegation"]
    assert "Verification Engineer" in case.prompt


def test_delegated_research_diagnostic_requires_sequential_durable_truth(monkeypatch):
    research_payload = {
        "kind": "research_evidence_bundle",
        "answer": "复核前研究正文" * 500,
        "sources": [
            {"url": f"https://official-{index}.gov.cn/policy", "selectedForEvidence": True}
            for index in range(1, 6)
        ],
        "claimTable": [{"claimId": f"C{index}", "supportingSources": [
            {"citationKey": f"S{index}", "url": f"https://official-{index}.gov.cn/policy"}
        ]} for index in range(1, 4)],
    }
    result = audit.LiveCaseResult(
        spec=audit._case_specs(audit.RESEARCH_DELEGATED_VERIFICATION_CASE_ID)[0],
        status="completed",
        final_text=(
            "截至 2026 年 9 月 3 日，以下结论已经 Verification Engineer 独立复核。\n"
            + "\n".join(
                f"检查项 {index}：{hashlib.sha256(f'official-evidence-{index}'.encode()).hexdigest()}；"
                "核对法规义务、适用范围、关键日期与对应官方证据。"
                for index in range(1, 70)
            )
            + "\n"
            + "\n".join(f"https://official-{index}.gov.cn/policy" for index in range(1, 6))
        ),
        research_completed_seq=40,
        episodes=[
            {"episodeId": "research-1", "kind": "research", "state": "completed"},
            {
                "episodeId": "delegation-1",
                "kind": "delegation",
                "state": "completed",
                "taskBrief": {"preferredAgentId": "verification-engineer"},
            },
        ],
        handoffs=[
            {"episodeId": "research-1", "payload": research_payload},
            {
                "episodeId": "delegation-1",
                "payload": {
                    "kind": "delegation",
                    "status": "completed",
                    "summary": "Verification Engineer 已核验日期、法规层级、义务和来源。\n" + "\n".join(
                        f"C{index} [S{index}] https://official-{index}.gov.cn/policy 已核验"
                        for index in range(1, 4)
                    ),
                },
            },
        ],
        tool_invocations=[
            {
                "seq": 44,
                "topic": "tool.started",
                "toolName": "delegation_broker",
                "ownerRuntimeId": "chat",
                "ownerAgentKind": "supervisor",
                "ownerAgentId": "supervisor",
            }
        ],
    )
    result.final_text += "\n" + research_payload["answer"]
    monkeypatch.setattr(
        audit,
        "_research_handoff_assessment",
        lambda _payload, *, question: {
            "sourceUrls": [f"https://official-{index}.gov.cn/policy" for index in range(1, 6)]
        },
    )

    diagnostic = audit._delegated_research_verification_diagnostic(result)
    findings = audit._delegated_research_verification_findings(result)

    assert diagnostic["delegationAfterResearch"] is True
    assert diagnostic["verificationIdentityPresent"] is True
    assert diagnostic["delegationTerminal"] is True
    assert findings == [], (diagnostic, [item.summary for item in findings])

    # A worker name or an exception wrapper mentioning verification is not a
    # verification result, even if legacy lifecycle metadata says completed.
    result.handoffs[1]["payload"].update({
        "summary": "[Verification Engineer 执行异常] V8LLMTimeoutError: deadline exceeded",
        "workerStatus": "ok",
    })
    assert audit._delegated_research_verification_diagnostic(result)["verificationResultPresent"] is False


@pytest.mark.parametrize("selected,clicks", [("true", 0), ("false", 1)])
def test_web_activity_audit_does_not_reclick_active_overview(selected, clicks):
    from tests.scripts import live_web_activity_audit as web_audit
    calls = []
    first = SimpleNamespace(get_attribute=lambda _name: selected,
                            click=lambda **kwargs: calls.append(kwargs))
    observer = web_audit.WebActivityAuditObserver(web_url="http://localhost", session_id="test")
    observer._page = SimpleNamespace(locator=lambda _selector: SimpleNamespace(count=lambda: 1, first=first))
    observer._overview()
    assert len(calls) == clicks


def test_web_activity_audit_waits_for_authoritative_reload_evidence(monkeypatch):
    from tests.scripts import live_web_activity_audit as web_audit

    observer = web_audit.WebActivityAuditObserver(web_url="http://localhost", session_id="test")
    observer._page = SimpleNamespace(
        reload=lambda **_kwargs: None,
        locator=lambda _selector: SimpleNamespace(wait_for=lambda **_kwargs: None),
    )
    terminal = {
        "runtimeCards": [{"runtimeId": "research", "status": "recent", "eventCount": 2}],
        "subagentCards": [],
        "researchEvents": [{"eventSeq": 113, "topic": "runtime.episode.completed"}],
    }
    provisional = {**terminal, "researchEvents": []}
    snapshots = iter([terminal, provisional, terminal])
    monkeypatch.setattr(observer, "_snapshot", lambda *_args, **_kwargs: next(snapshots))
    monkeypatch.setattr(web_audit.time, "sleep", lambda _seconds: None)

    result = observer.finish()

    assert result["errors"] == []
    assert all(result["parity"].values())


def test_final_text_does_not_hide_a_later_completed_message_based_on_length():
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
    )

    assert selected == "Research handoff 已回流。"


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
    )

    assert selected == "无法交付：独立复核发现关键证据不成立。"


def test_final_text_never_substitutes_streaming_or_another_run_for_delivery():
    old = {"role": "assistant", "run_id": "old", "state": "completed", "content_text": "Earlier completed research."}
    running = {"role": "assistant", "run_id": "current", "state": "streaming", "content_text": "<tool_call>" * 200}
    assert audit._extract_final_text([running], preferred_run_id="current") == ""
    assert audit._extract_final_text([old, running], preferred_run_id="current") == ""
    assert audit._extract_final_text([old], preferred_run_id="current") == ""
    failed = {**running, "state": "failed", "finalized_at": "2026-09-04T00:00:00Z"}
    assert audit._extract_final_text([failed], preferred_run_id="current") == ""


def test_delegated_research_does_not_accept_provider_tool_markup_as_final_delivery():
    result = audit.LiveCaseResult(
        spec=audit._case_specs(audit.RESEARCH_DELEGATED_VERIFICATION_CASE_ID)[0],
        status="completed", final_text='已复核。<tool_call><invoke name="delegation_broker">not a native call</invoke>',
    )
    findings = audit._delegated_research_verification_findings(result)
    assert any("工具协议文本" in item.summary for item in findings)


def test_verification_proof_rejects_mirror_relabeling_and_unbound_success():
    payload = {"claimTable": [
        {"claimId": f"C{index}", "supportingSources": [
            {"citationKey": f"S{index}", "url": f"https://mirror.example.org/document-{index}"}
        ]} for index in range(1, 4)
    ]}
    proof = "\n".join(f"C{i} [S{i}] https://mirror.example.org/document-{i} 转载，已核验" for i in range(1, 4))
    assert audit._verification_binding_audit(proof, [payload])["passed"] is True
    forged = proof.replace("https://mirror.example.org/document-3", "https://official.gov.cn/original")
    diagnostic = audit._verification_binding_audit(forged, [payload])
    assert diagnostic["passed"] is False
    assert diagnostic["mismatches"] == ["C3/S3:source_url_mismatch"]
    assert audit._verification_binding_audit("Verification Engineer 全部验证成功。" * 1000, [payload])["passed"] is False
    assert audit._verification_binding_audit(proof, [])["passed"] is False


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
    raw = _accepted_research_payload(
        "bundle-valid",
        question,
        fixture_time=datetime.now(timezone.utc),
    )
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
        "asOf": raw["asOf"],
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
        "asOf": raw["asOf"],
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

    assert assessment["highQuality"] is True, assessment
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


def test_terminal_probe_treats_interrupted_run_and_cancelled_episode_as_terminal(monkeypatch):
    fake_db = SimpleNamespace(
        get_run_record=lambda _run_id: {
            "status": "interrupted",
            "finished_at": "2026-09-03T00:09:14Z",
            "error_message": "live audit interrupted the run",
        },
        list_runtime_episodes=lambda **_kwargs: [
            {"episodeId": "delegation-1", "kind": "delegation", "state": "cancelled"}
        ],
        list_runtime_episode_handoffs=lambda _episode_id: [],
    )
    monkeypatch.setattr(database_module, "db", fake_db)
    result = audit.LiveCaseResult(
        spec=audit.LiveCaseSpec(case_id="interrupted", title="interrupted", prompt="test"),
        session_id="session-interrupted",
        run_id="run-interrupted",
    )

    terminal, facts = audit._load_run_terminal(result)

    assert terminal is True
    assert facts["runStatus"] == "interrupted"
    assert facts["activeEpisodes"] == []


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
    monkeypatch.setattr(audit.time, "perf_counter", clock.time)
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
    assert completed.poll_elapsed_ms == 3000
    assert completed.latency_ms is None  # Never confuse submission with run duration.


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


def test_api_terminal_recognizes_interrupted_topic():
    terminal, facts = audit._api_run_terminal_facts(
        [
            {
                "seq": 11,
                "topic": "run.interrupted",
                "run_id": "run-current",
                "payload": {"reason": "bounded live audit stop"},
            }
        ],
        run_id="run-current",
    )

    assert terminal is True
    assert facts["apiTerminalStatus"] == "interrupted"
    assert facts["apiTerminalError"] == "bounded live audit stop"
