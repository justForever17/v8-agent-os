from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.scripts import run_research_runtime_fixed_bundle_acceptance as audit


def _bundle() -> dict:
    return {
        "evidenceBundleId": "fixed-1",
        "question": "Exact question?",
        "sourcePolicy": "authoritative",
        "freshness": "current",
        "confidence": "high",
        "authorityScore": 85,
        "sourceMatrix": [{"sourceId": "s1", "url": "https://one.example", "text": "evidence"}],
        "shards": [{"shardId": "q1", "fetchedTopSources": [{"url": "https://one.example", "text": "evidence"}]}],
        "answer": "mutable old answer",
        "reviewDecision": "reject",
        "runs": [{"run": 1}],
    }


def _start(attempt_id: str, binding: str) -> dict:
    return {
        "event": "attempt_started",
        "attemptId": attempt_id,
        "bindingKey": binding,
        "startedAt": "2026-07-29T00:00:00Z",
    }


def _finish(attempt_id: str, binding: str, *, qualified: bool = True, **updates) -> dict:
    event = {
        "event": "attempt_finished",
        "attemptId": attempt_id,
        "bindingKey": binding,
        "terminalStatus": "completed",
        "reviewDecision": "accept",
        "highQualityIssues": [],
        "evidenceSearchCalls": 0,
        "evidenceReadCalls": 0,
        "zeroAdditionalEvidenceAcquisition": True,
        "zeroNetworkClaimPermitted": False,
        "formalRetry": False,
        "bindingValid": True,
        "qualified": qualified,
    }
    event.update(updates)
    return event


def test_bundle_digest_ignores_mutable_answer_review_and_run_history():
    before = _bundle()
    after = _bundle()
    after.update(
        {
            "answer": "a completely different generated answer",
            "reviewDecision": "accept",
            "runs": [{"run": 999}],
            "createdAt": "2099-01-01T00:00:00Z",
        }
    )

    assert audit.bundle_digest(before) == audit.bundle_digest(after)


def test_bundle_digest_changes_with_exact_question_or_source_content():
    original = _bundle()
    question_changed = _bundle()
    question_changed["question"] += " "
    source_changed = _bundle()
    source_changed["shards"][0]["fetchedTopSources"][0]["text"] = "different evidence"

    assert audit.bundle_digest(original) != audit.bundle_digest(question_changed)
    assert audit.bundle_digest(original) != audit.bundle_digest(source_changed)
    assert audit.question_digest(original) != audit.question_digest(question_changed)


def test_code_fingerprint_uses_file_bytes_not_git_head(tmp_path: Path):
    source = tmp_path / "runtime.py"
    source.write_text("value = 1\n", encoding="utf-8")
    before = audit.code_fingerprint([source])
    source.write_text("value = 2\n", encoding="utf-8")

    assert audit.code_fingerprint([source]) != before


def test_safe_config_projection_excludes_credentials_and_fingerprint_is_stable():
    provider = {
        "name": "Provider",
        "base_url": "https://api.example/v1",
        "api_standard": "openai",
        "api_key": "secret",
        "credentialRef": "secret-ref",
    }
    safe = audit._safe_provider_projection(provider)
    first = {"transactionIds": ["b", "a"], "provider": safe}
    second = {"provider": safe, "transactionIds": ["b", "a"]}

    assert "api_key" not in safe
    assert "credentialRef" not in safe
    assert "secret" not in json.dumps(safe)
    assert audit.config_fingerprint(first) == audit.config_fingerprint(second)


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"extra_body": {"thinking": {"type": "disabled"}}},
        {"thinking": {"type": "disabled"}},
        {"extra_body": {"enable_thinking": False}},
        {"reasoning_effort": "none"},
        {"reasoning": {"effort": "none"}},
        {"thinking_budget": 0},
    ],
)
def test_no_think_request_detection_requires_an_actual_wire_parameter(
    request_kwargs: dict,
):
    assert audit._request_disables_thinking(request_kwargs) is True


def test_no_think_model_label_without_wire_parameter_is_not_enough():
    assert audit._request_disables_thinking(
        {"model": "deepseek-v4-no-think", "thinkingControl": {"disabled": True}}
    ) is False


def _projection_transactions(*, include_thinking: bool = False) -> dict[str, dict]:
    transactions = {
        "txn-architect": {
            "targetKind": "agent_model_role",
            "targetId": "web-research-architect",
            "operation": "assign",
            "state": "committed",
            "planDigest": "plan-architect",
            "result": {
                "agentId": "web-research-architect",
                "modelRef": "provider::chat",
            },
        },
        "txn-reviewer": {
            "targetKind": "agent_model_role",
            "targetId": "verification-engineer",
            "operation": "assign",
            "state": "committed",
            "planDigest": "plan-reviewer",
            "result": {
                "agentId": "verification-engineer",
                "modelRef": "provider::chat",
            },
        },
    }
    if include_thinking:
        transactions["txn-thinking"] = {
            "targetKind": "model_thinking_control",
            "targetId": "provider::chat",
            "operation": "set_thinking_disabled",
            "state": "committed",
            "planDigest": "plan-thinking",
            "validation": {
                "thinkingControl": {
                    "supportsNoThink": True,
                    "beforeDisabled": False,
                    "afterDisabled": True,
                },
                "connection": {"ok": True, "status": "healthy"},
            },
            "result": {
                "modelRef": "provider::chat",
                "thinkingDisabled": True,
                "verified": True,
            },
        }
    return transactions


def _patch_projection_runtime(monkeypatch, transactions: dict[str, dict]) -> None:
    import core.config_broker_service as config_module
    import core.llm_factory as llm_module
    import core.model_control_plane as model_module

    config = {
        "bindings": {
            "agents": {
                "web-research-architect": {"model_id": "provider::chat"},
                "verification-engineer": {"model_id": "provider::chat"},
            }
        },
        "roles": {"default": "provider::chat", "supervisor": "provider::chat"},
    }
    record = {
        "provider_id": "provider",
        "model_id": "chat",
        "model_ref": "provider::chat",
        "provider": {
            "name": "Provider",
            "api_standard": "openai",
            "base_url": "https://api.example.test/v1",
        },
        "model": {
            "type": "TEXT",
            "contextWindow": 262_144,
            "maxTokens": 8_192,
            "thinkingControl": {
                "supportsNoThink": True,
                "disabled": True,
                "requestStyle": "openai_thinking_disabled",
            },
        },
    }
    monkeypatch.setattr(
        config_module.config_broker_service,
        "get_transaction",
        lambda transaction_id: json.loads(json.dumps(transactions[transaction_id])),
    )
    monkeypatch.setattr(
        model_module.model_control_plane,
        "get_config",
        lambda: json.loads(json.dumps(config)),
    )
    monkeypatch.setattr(
        model_module.model_control_plane,
        "get_model_record",
        lambda model_ref, _config: json.loads(json.dumps(record)) if model_ref == "provider::chat" else None,
    )
    monkeypatch.setattr(
        llm_module.llm_factory,
        "create_chat_model",
        lambda *_args, **_kwargs: SimpleNamespace(
            _model_kwargs={"extra_body": {"thinking": {"type": "disabled"}}}
        ),
    )


def test_config_projection_accepts_two_research_bindings_with_optional_related_thinking_transaction(monkeypatch):
    base_transactions = _projection_transactions()
    _patch_projection_runtime(monkeypatch, base_transactions)
    base = audit.resolve_config_projection(list(base_transactions), require_no_think=True)

    transactions = _projection_transactions(include_thinking=True)
    _patch_projection_runtime(monkeypatch, transactions)
    with_thinking = audit.resolve_config_projection(list(transactions), require_no_think=True)

    assert set(base["agents"]) == audit.REQUIRED_RESEARCH_AGENT_IDS
    assert len(base["transactions"]) == 2
    assert len(with_thinking["transactions"]) == 3
    assert with_thinking["transactions"][-1]["targetKind"] == "model_thinking_control"
    assert audit.config_fingerprint(base) != audit.config_fingerprint(with_thinking)


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda transactions: transactions.pop("txn-reviewer"),
            "exactly two Research agent model assignments",
        ),
        (
            lambda transactions: transactions["txn-architect"].update(state="rolled_back"),
            "not committed",
        ),
        (
            lambda transactions: transactions["txn-thinking"].update(targetId="provider::other", result={"modelRef": "provider::other", "thinkingDisabled": True}),
            "unrelated to an effective Research model",
        ),
        (
            lambda transactions: transactions["txn-thinking"].update(targetKind="model_role"),
            "unsupported config transaction target",
        ),
    ],
)
def test_config_projection_rejects_invalid_transaction_sets(monkeypatch, mutate, error):
    transactions = _projection_transactions(include_thinking=True)
    mutate(transactions)
    _patch_projection_runtime(monkeypatch, transactions)

    with pytest.raises(audit.FixedBundleAcceptanceError, match=error):
        audit.resolve_config_projection(list(transactions), require_no_think=True)


def test_fixed_assessment_rejects_deterministic_fallback_even_when_quality_metrics_pass(monkeypatch):
    import core.tools.research_quality as quality

    metrics = {
        "effectiveAnswerChars": 8_000,
        "answerCitedSourceCount": 8,
        "distinctHostCount": 5,
        "independentReviewCount": 2,
        "independentReviewerModelCount": 2,
    }
    monkeypatch.setattr(quality, "research_acceptance_metrics", lambda _result: dict(metrics))
    monkeypatch.setattr(quality, "research_high_quality_issues", lambda _result: [])
    monkeypatch.setattr(quality, "research_review_decision", lambda _result: "accept")

    assessment = audit._result_assessment(
        {
            "modelSynthesis": {
                "writerMode": "deterministic_claim_report_after_writer",
                "writerSectionCount": 4,
            }
        }
    )

    assert "fixed_writer_not_segmented:deterministic_claim_report_after_writer" in assessment["highQualityIssues"]


def test_fixed_assessment_accepts_two_independent_reviews_from_same_configured_model(monkeypatch):
    import core.tools.research_quality as quality

    metrics = {
        "effectiveAnswerChars": 8_000,
        "answerCitedSourceCount": 8,
        "distinctHostCount": 5,
        "independentReviewCount": 2,
        "independentReviewerModelCount": 1,
    }
    monkeypatch.setattr(quality, "research_acceptance_metrics", lambda _result: dict(metrics))
    monkeypatch.setattr(quality, "research_high_quality_issues", lambda _result: [])
    monkeypatch.setattr(quality, "research_review_decision", lambda _result: "accept")

    assessment = audit._result_assessment(
        {"modelSynthesis": {"writerMode": "segmented", "writerSectionCount": 4}}
    )

    assert assessment["highQualityIssues"] == []


def test_first_and_second_qualified_attempt_replay_to_streak_two():
    events = [
        _start("a1", "binding-a"),
        _finish("a1", "binding-a"),
        _start("a2", "binding-a"),
        _finish("a2", "binding-a"),
    ]

    assert audit.replay_streak(events[:2], expected_binding="binding-a") == 1
    assert audit.replay_streak(events, expected_binding="binding-a") == 2


def test_binding_change_resets_before_counting_new_accept():
    events = [
        _start("a1", "binding-a"),
        _finish("a1", "binding-a"),
        _start("b1", "binding-b"),
        _finish("b1", "binding-b"),
    ]

    assert audit.replay_streak(events, expected_binding="binding-a") == 0
    assert audit.replay_streak(events, expected_binding="binding-b") == 1


@pytest.mark.parametrize(
    "failed",
    [
        {"reviewDecision": "reject", "qualified": False},
        {"formalRetry": True, "qualified": False},
        {"terminalStatus": "exception", "qualified": False},
        {"evidenceSearchCalls": 1, "qualified": False},
        {"evidenceReadCalls": 1, "qualified": False},
        {"highQualityIssues": ["gap"], "qualified": False},
        {"bindingValid": False, "qualified": False},
    ],
)
def test_any_failed_terminal_condition_resets_streak(failed: dict):
    events = [
        _start("a1", "binding-a"),
        _finish("a1", "binding-a"),
        _start("a2", "binding-a"),
        _finish("a2", "binding-a", **failed),
    ]

    assert audit.replay_streak(events, expected_binding="binding-a") == 0


def test_failed_first_then_accepted_second_finishes_with_streak_one():
    events = [
        _start("a1", "binding-a"),
        _finish("a1", "binding-a", reviewDecision="reject", qualified=False),
        _start("a2", "binding-a"),
        _finish("a2", "binding-a"),
    ]

    assert audit.replay_streak(events, expected_binding="binding-a") == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"evidenceSearchCalls": None},
        {"evidenceSearchCalls": False},
        {"evidenceReadCalls": "0"},
        {"highQualityIssues": None},
        {"zeroNetworkClaimPermitted": True},
    ],
)
def test_missing_or_wrongly_typed_proof_fields_fail_closed(updates: dict):
    event = _finish("a1", "binding-a", **updates)

    assert audit.attempt_qualified(event) is False


def test_unfinished_attempt_is_abandoned_and_resets_streak():
    events = [
        _start("a1", "binding-a"),
        _finish("a1", "binding-a"),
        _start("a2", "binding-a"),
    ]
    abandoned = audit.abandoned_events(events, abandoned_at="2026-07-30T00:00:00Z")

    assert len(abandoned) == 1
    assert abandoned[0]["attemptId"] == "a2"
    assert audit.replay_streak([*events, *abandoned], expected_binding="binding-a") == 0


def test_unfinished_attempt_without_recovery_is_already_fail_closed():
    events = [
        _start("a1", "binding-a"),
        _finish("a1", "binding-a"),
        _start("a2", "binding-a"),
    ]

    assert audit.replay_streak(events, expected_binding="binding-a") == 0


def test_artifact_verification_rejects_missing_or_modified_result(tmp_path: Path):
    result = tmp_path / "result.json"
    result.write_bytes(b'{"ok":true}')
    event = _finish(
        "a1",
        "binding-a",
        resultRef=str(result),
        resultSha256=audit._sha256_bytes(result.read_bytes()),
    )
    events = [_start("a1", "binding-a"), event]

    assert audit.replay_streak(
        events, expected_binding="binding-a", verify_artifacts=True
    ) == 1
    result.write_bytes(b'{"ok":false}')
    assert audit.replay_streak(
        events, expected_binding="binding-a", verify_artifacts=True
    ) == 0


def test_duplicate_terminal_event_is_rejected():
    events = [
        _start("a1", "binding-a"),
        _finish("a1", "binding-a"),
        _finish("a1", "binding-a"),
    ]

    with pytest.raises(audit.AttemptLogError, match="duplicate terminal"):
        audit.replay_streak(events, expected_binding="binding-a")


def test_corrupt_jsonl_fails_closed_without_rewriting_history(tmp_path: Path):
    log = tmp_path / "attempts.jsonl"
    original = '{"event":"attempt_started"}\nnot-json\n'
    log.write_text(original, encoding="utf-8")

    with pytest.raises(audit.AttemptLogError, match="corrupt"):
        audit.load_attempt_events(log)

    assert log.read_text(encoding="utf-8") == original


def test_append_attempt_event_is_append_only(tmp_path: Path):
    log = tmp_path / "attempts.jsonl"
    first = _start("a1", "binding-a")
    second = _finish("a1", "binding-a")

    audit.append_attempt_event(log, first)
    audit.append_attempt_event(log, second)

    assert audit.load_attempt_events(log) == [first, second]


def test_forbidden_gateways_increment_before_raise_and_restore():
    original_router = lambda: "router"  # noqa: E731
    original_search = lambda: "search"  # noqa: E731
    original_read = lambda: "read"  # noqa: E731
    module = SimpleNamespace(
        _source_router_search=original_router,
        web_search=SimpleNamespace(func=original_search),
        web_read=SimpleNamespace(func=original_read),
    )

    with audit.forbid_evidence_acquisition(module) as counters:
        with pytest.raises(audit.EvidenceAcquisitionForbidden):
            module._source_router_search()
        with pytest.raises(audit.EvidenceAcquisitionForbidden):
            module.web_search.func()
        with pytest.raises(audit.EvidenceAcquisitionForbidden):
            module.web_read.func()
        assert counters == {"search": 2, "read": 1}

    assert module._source_router_search is original_router
    assert module.web_search.func is original_search
    assert module.web_read.func is original_read


def test_provider_network_is_explicitly_allowed_but_never_called_zero_network():
    event = _finish("a1", "binding-a")
    event.update(
        {
            "providerNetworkMode": "live-allowed",
            "providerCallsObserved": None,
        }
    )

    assert audit.attempt_qualified(event) is True
    assert event["providerNetworkMode"] == "live-allowed"
    assert event["zeroNetworkClaimPermitted"] is False
