from __future__ import annotations

import json
import hashlib

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from core.tools.research_quality import research_answer_is_usable, research_bundle_is_accepted
from runtimes.research.agent import ResearchAgent
from runtimes.research.evidence import EvidenceStore
from runtimes.research.model_call import IncompleteModelResponse


FACT = "Version 2 retains the existing on-disk format."
ANSWER = "Version 2 retains the existing on-disk format. [S1]"


@pytest.fixture(autouse=True)
def isolated_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("V8_RESEARCH_LEDGER_PATH", str(tmp_path / "research_ledger.json"))


def source(text=FACT, url="https://example.org/spec/v2"):
    return {"ok": True, "url": url, "title": "Format specification", "text": text,
            "publishedAt": "2021-01-01", "retrievedAt": "2026-09-05T08:00:00Z"}


def call(name, **args):
    return AIMessage(content="", tool_calls=[{"id": f"call-{name}", "name": name, "args": args}])


def read(**kwargs):
    return call("read_research_source", sourceKey="S1", **kwargs)


def submit(answer=ANSWER, coverage="complete", limitations=None):
    return call("submit_research_answer", answer=answer, coverage=coverage,
                limitations=limitations or [])


def approve(coverage="complete", limitations=None):
    return call("review_research_answer", decision="accept", coverage=coverage,
                corrections=[], limitations=limitations or [], nextQueries=[])


class ScriptedTransport:
    def __init__(self, writer, reviewer):
        self.writer, self.reviewer = iter(writer), iter(reviewer)
        self.requests = []

    def __call__(self, messages, tools, *, reviewer, seconds, required=False):
        self.requests.append((list(messages), tools, reviewer))
        assert seconds > 0
        return next(self.reviewer if reviewer else self.writer)


def agent(writer, reviewer, **kwargs):
    transport = ScriptedTransport(writer, reviewer)
    instance = ResearchAgent(invoke=transport, acquire=None, progress=lambda **_: None,
                             writer_id="configured-writer", reviewer_id="configured-reviewer", **kwargs)
    instance.store.add([source()])
    return instance, transport


def test_single_source_complete_answer_needs_no_planner_or_second_adversarial_pass():
    instance, transport = agent([read(), submit()], [approve()])
    result = instance.run(question="Does version 2 change the file format?")
    assert result["answer"] == ANSWER
    assert research_bundle_is_accepted(result)
    assert len(transport.requests) == 3
    assert result["modelSynthesis"]["searchCount"] == 0
    assert result["claimTable"][0]["verificationKind"] == "read_snapshot_ref_only"
    assert not any("Hard rejection floor" in str(message.content)
                   for request, _, _ in transport.requests for message in request)


def test_reusable_body_guidance_reaches_actual_writer_and_both_submission_tools():
    from runtimes.research.agent import ANSWER_BODY_CONTRACT

    limitation = "The source establishes the format only; migration tooling remains unverified."
    instance, transport = agent([
        read(), call("save_research_answer_section", sectionId="finding", text=ANSWER),
        call("submit_research_answer", sectionIds=["finding"], coverage="partial", limitations=[limitation]),
    ], [approve("partial", [limitation])])
    result = instance.run(question="Explain the format and migration tooling.")
    messages, tools, _ = transport.requests[0]
    assert ANSWER_BODY_CONTRACT in messages[0].content
    assert "lead with supported findings" in messages[0].content
    assert "unless specifically requested or necessary to interpret evidence" in messages[0].content
    assert "do not repeat a full limitations block in the body" in messages[0].content
    for name in ("save_research_answer_section", "submit_research_answer"):
        actual = next(item["function"] for item in tools if item["function"]["name"] == name)
        assert ANSWER_BODY_CONTRACT in actual["description"]
    reviewer_messages = next(messages for messages, _, reviewer in transport.requests if reviewer)
    assert "在 assessment 给精简建议" in reviewer_messages[0].content
    assert "不设正文占比或字数门槛" in reviewer_messages[0].content
    assert "不能把错误改称一般知识外推" in reviewer_messages[0].content
    assert "不能把观察范围扩张为排他因果或普遍必要条件" in reviewer_messages[0].content
    review_tools = next(tools for _, tools, reviewer in transport.requests if reviewer)
    review_contract = next(item["function"] for item in review_tools if item["function"]["name"] == "review_research_answer")
    assert "Unsupported substantive mechanism claims require a local correction" in review_contract["description"]
    assert "not acceptance with a caveat" in review_contract["parameters"]["properties"]["limitations"]["description"]
    assert result["answer"] == ANSWER
    assert result["deliveryScope"] == "partial"
    assert result["limitations"] == [limitation]
    assert len([item for item in transport.requests if item[2]]) == 1
    assert instance.searches == 0


def test_body_guidance_is_not_a_lexical_filter_or_another_acceptance_pass():
    # Synthetic methodology question: words like "timeout" may be the requested
    # subject. Preserve reviewer-accepted prose and restrictions byte for byte.
    method = "## Method and limits\n\nA timeout leaves the old format intact. [S1]"
    limitation = "No claim is made about retry safety."
    instance, transport = agent([read(), submit(answer=method, coverage="partial", limitations=[limitation])],
                                [approve("partial", [limitation])])
    instance.store = EvidenceStore()
    instance.store.add([source("A timeout leaves the old format intact.")])
    result = instance.run(question="Describe the observed timeout behavior and its limits.")
    assert result["answer"] == method
    assert result["researchResult"] == method
    assert result["deliveryScope"] == "partial"
    assert result["limitations"] == [limitation]
    assert result["reviewDecision"] == "accept"
    assert len(transport.requests) == 3
    assert instance.searches == 0


def test_explicit_factual_correction_cannot_be_accepted_by_adding_a_limitation():
    wrong = "Every failed write deletes the old file. [S1]"
    evidence = "A failed write leaves the old file unchanged."
    finding = {"kind": "fact", "answerQuote": "Every failed write deletes the old file.",
               "reason": "The claimed failure mechanism contradicts the observed behavior.",
               "sourceKey": "S1", "evidenceQuote": evidence}
    contradictory = call("review_research_answer", decision="accept", coverage="partial", corrections=[finding],
                         limitations=["The deletion mechanism is a general extrapolation."])
    revise = call("review_research_answer", decision="revise", coverage="partial", corrections=[finding],
                  limitations=["Other storage backends were not examined."])
    instance, transport = agent([read(), submit(answer=wrong)], [contradictory, revise], max_revisions=0)
    instance.store = EvidenceStore()
    instance.store.add([source(evidence)])
    result = instance.run(question="What happens to the old file after a failed write?")
    review_requests = [messages for messages, _, reviewer in transport.requests if reviewer]
    errors = [json.loads(message.content).get("error", "") for message in review_requests[-1]
              if isinstance(message, ToolMessage)]
    assert "accept_requires_supported_answer_without_unresolved_errors" in errors
    assert result["reviewDecision"] == "retry"
    assert result["answer"] == ""
    assert result["candidateDraft"]["answer"] == wrong
    assert result["modelSynthesis"]["fallbackReason"] == "research_revision_budget_exhausted"
    assert any(item.get("decision") == "revise" for item in result["modelSynthesis"]["trace"])


def test_writer_and_reviewer_receive_original_request_separate_from_derived_question():
    original = "Please identify the applicable content-labeling standard."
    derived = "Verify standard GB 00000-2025 and its effective date."
    accepted = approve()
    accepted.tool_calls[0]["args"]["requestAttribution"] = {
        "verdict": "not_claimed", "explanation": "The answer does not claim that the user supplied an identifier.",
    }
    instance, transport = agent([read(), submit()], [accepted], original_user_request=original)
    result = instance.run(question=derived)
    assert result["answer"] == ANSWER
    seen_roles = set()
    for messages, _, reviewer in transport.requests:
        request = json.loads(messages[1].content)
        assert request["question"] == derived
        assert request["requestContext"]["originalUserRequest"] == original
        assert "00000" not in request["requestContext"]["originalUserRequest"]
        seen_roles.add(reviewer)
    assert seen_roles == {False, True}


def test_direct_review_without_original_request_does_not_invent_conversation_provenance():
    instance, transport = agent([], [approve()])
    instance.store.read("S1")
    instance.review("Derived task wording", {"answer": ANSWER, "limitations": []}, "en")
    request = json.loads(transport.requests[0][0][1].content)
    assert request["requestContext"] == {"questionSource": "unattributed_research_task", "originalUserRequest": ""}


@pytest.mark.parametrize("failure", ["invented_original_quote", "contradictory_accept"])
def test_request_attribution_quotes_cannot_be_borrowed_from_the_derived_task(failure):
    candidate = {"answer": "The user supplied GB 00000. The actual standard differs. [S1]", "limitations": []}
    attribution = {"verdict": "supported", "answerQuote": "The user supplied GB 00000.",
                   "originalRequestQuote": "GB 00000", "explanation": "The derived question supplied this number."}
    if failure == "contradictory_accept":
        attribution.update(verdict="incorrect", originalRequestQuote="")
    bad = approve()
    bad.tool_calls[0]["args"]["requestAttribution"] = attribution
    correction = call("review_research_answer", decision="revise", coverage="partial",
        requestAttribution={**attribution, "verdict": "incorrect", "originalRequestQuote": ""},
        corrections=[{"kind": "attribution", "answerQuote": "The user supplied GB 00000.",
                      "reason": "Only the derived task supplies the number; correct its attribution."}])
    instance, transport = agent([], [bad, correction], original_user_request="Identify the standard.")
    instance.store.read("S1")
    result = instance.review("Verify GB 00000", candidate, "en")
    assert result["decision"] == "revise"
    feedback = [str(message.content) for message in transport.requests[-1][0] if isinstance(message, ToolMessage)]
    expected = "request_attribution_original_quote_not_located" if failure == "invented_original_quote" else "review_decision_conflicts_with_request_attribution"
    assert any(expected in message for message in feedback)


def test_review_added_limitations_cannot_publish_unknown_references():
    instance, transport = agent([read(), submit()], [
        approve("partial", ["Unverified migration requirement 【S99】"]),
        approve("partial", ["Deployment behavior has not been tested."]),
    ])
    result = instance.run(question="Does version 2 change the file format?")
    assert result["reviewDecision"] == "accept"
    assert result["limitations"] == ["Deployment behavior has not been tested."]
    repair = transport.requests[-1][0][-1]
    assert json.loads(repair.content)["unknownSourceKeys"] == ["S99"]


def test_reviewer_source_reads_are_bound_to_delivered_limitations():
    instance, _ = agent([read(), submit()], [
        call("read_research_source", sourceKey="S2"),
        approve("partial", ["Remote filesystems are excluded. [S2]"]),
    ])
    instance.store.add([source("Remote filesystems are excluded.", "https://example.org/spec/exclusions")])
    result = instance.run(question="Does version 2 change the file format?")
    assert result["reviewDecision"] == "accept"
    assert {support["citationKey"] for claim in result["claimTable"] for support in claim["supportingSources"]} == {"S1", "S2"}
    assert len(result["sourceUrls"]) == 2


@pytest.mark.parametrize("marker", ["【S1】", "[s1]", "[来源：S1（规范原文）]", "[Sources: S1 (specification)]"])
def test_explicit_citation_typography_is_normalized_before_review_and_binding(marker):
    from runtimes.research.evidence import normalize_citation_tokens
    answer = f"{FACT} {marker}"
    instance, transport = agent([read(), submit(answer=answer)], [approve()])
    result = instance.run(question="Does version 2 change the format?")
    assert result["answer"] == normalize_citation_tokens(answer)
    assert "[S1]" in result["answer"]
    assert research_bundle_is_accepted(result)
    review = [messages for messages, _, reviewer in transport.requests if reviewer][0]
    assert json.loads(review[1].content)["candidate"]["answer"] == result["answer"]


def test_citation_normalization_preserves_labels_and_does_not_bless_unknown_or_unread_sources():
    from runtimes.research.evidence import normalize_citation_tokens
    original = f"{FACT} [来源：S1（原文）、S99（未核实）]"
    result = normalize_citation_tokens(original)
    assert result == f"{FACT} （来源：[S1]（原文）、[S99]（未核实））"
    assert normalize_citation_tokens(result) == result
    instance, _ = agent([], [])
    saved = instance.save_section({"sectionId": "finding", "text": original})
    assert saved["unknownSourceKeys"] == ["S99"]
    assert saved["unreadSourceKeys"] == ["S1"]
    assert normalize_citation_tokens("S1 is a device model; Sources: S2") == "S1 is a device model; Sources: S2"


def test_agent_owned_sections_are_reviewed_as_one_answer_and_revised_without_duplication():
    correction = call("review_research_answer", decision="revise", coverage="complete", corrections=[{
        "kind": "fact", "answerQuote": "All files are deleted.", "reason": "The source retains the file format.",
        "sourceKey": "S1", "evidenceQuote": FACT,
    }])
    submit_sections = call("submit_research_answer", sectionIds=["finding", "boundary"], coverage="complete")
    instance, transport = agent([
        read(),
        call("save_research_answer_section", sectionId="finding", text=ANSWER),
        call("save_research_answer_section", sectionId="boundary", text="All files are deleted. [S1]"),
        submit_sections,
        call("save_research_answer_section", sectionId="boundary", text="The format remains unchanged. [S1]"),
        submit_sections,
    ], [correction, approve()])
    result = instance.run(question="Does version 2 change the format?")
    expected = ANSWER + "\n\nThe format remains unchanged. [S1]"
    assert result["answer"] == expected
    assert research_bundle_is_accepted(result)
    review_requests = [messages for messages, _, reviewer in transport.requests if reviewer]
    assert len(review_requests) == 2
    assert json.loads(review_requests[-1][-1].content)["candidate"]["answer"] == expected
    assert any(isinstance(message, ToolMessage) and "Review recorded" in message.content
               for message in review_requests[-1][:-1])
    assert json.loads(review_requests[-1][-1].content)["observedPassages"] == []
    assert result["modelSynthesis"]["revisionCount"] == 1
    assert instance.searches == 0


def test_limitations_are_reviewed_and_their_citations_need_actual_reads():
    correction = call("review_research_answer", decision="revise", coverage="partial", corrections=[{
        "kind": "fact", "answerQuote": "All files are deleted.", "reason": "Contradicts the original",
        "sourceKey": "S1", "evidenceQuote": FACT,
    }])
    instance, transport = agent([
        read(), submit(coverage="partial", limitations=["All files are deleted. [S1]"]),
        submit(coverage="partial", limitations=["Other formats were not examined."]),
    ], [correction, approve("partial", ["Other formats were not examined."])])
    result = instance.run(question="Format behavior")
    assert result["deliveryScope"] == "partial"
    review = next(row for row in result["modelSynthesis"]["trace"] if row["stage"] == "review_tool")
    assert review["unlocatedFindings"] == []
    assert "All files are deleted" not in " ".join(result["limitations"])

    instance, transport = agent([
        read(), submit(coverage="partial", limitations=["Unsupported assertion [S99]"]),
        submit(coverage="partial", limitations=["Other formats were not examined."]),
    ], [approve("partial", ["Other formats were not examined."])])
    result = instance.run(question="Format behavior")
    assert result["reviewDecision"] == "accept"
    assert any(row.get("unknownSourceKeys") == ["S99"] for row in result["modelSynthesis"]["trace"])
    assert len([request for request in transport.requests if request[2]]) == 1


def test_saved_sections_cannot_become_accepted_or_visible_without_submission_and_review():
    instance, _ = agent([read(), call("save_research_answer_section", sectionId="finding", text=ANSWER), AIMessage(content="Done")], [], max_steps=3)
    result = instance.run(question="Does version 2 change the format?")
    assert result["answer"] == ""
    assert result["candidateDraft"]["status"] == "unreviewed"
    assert result["candidateDraft"]["sections"] == {"finding": ANSWER}
    from core.tools.research_broker import _visible_bundle
    assert "candidateDraft" not in _visible_bundle(result)
    assert not research_bundle_is_accepted(result)
    assert instance.answer_sections == {"finding": ANSWER}


def test_writer_reserves_final_existing_step_for_submission_and_review():
    instance, transport = agent([
        read(), call("save_research_answer_section", sectionId="finding", text=ANSWER),
        call("submit_research_answer", sectionIds=["finding"], coverage="complete"),
    ], [approve()], max_steps=3)
    result = instance.run(question="Does version 2 change the format?")
    writer_calls = [(messages, tools) for messages, tools, reviewer in transport.requests if not reviewer]
    assert len(writer_calls) == 3
    assert [tool["function"]["name"] for tool in writer_calls[-1][1]] == ["submit_research_answer"]
    assert "还剩 1 次" in writer_calls[-1][0][-1].content
    assert result["answer"] == ANSWER
    assert research_bundle_is_accepted(result)
    assert len([1 for _, _, reviewer in transport.requests if reviewer]) == 1


def test_writer_cannot_save_or_search_past_reserved_submission_boundary():
    instance, _ = agent([read(), call("save_research_answer_section", sectionId="late", text=ANSWER)], [], max_steps=2)
    result = instance.run(question="Does version 2 change the format?")
    assert result["answer"] == ""
    assert instance.answer_sections == {}
    assert result["modelSynthesis"]["trace"][-1]["error"] == "research_final_step_requires_submission"


def test_unread_citation_is_located_and_repaired_without_rewriting_saved_answer():
    submit_sections = call("submit_research_answer", sectionIds=["finding"], coverage="complete")
    instance, transport = agent([
        call("save_research_answer_section", sectionId="finding", text=ANSWER),
        submit_sections, read(), submit_sections,
    ], [approve()])
    events = []
    instance.progress = lambda **event: events.append(event)
    result = instance.run(question="Does version 2 change the format?")
    feedback = [json.loads(message.content) for message in transport.requests[2][0] if isinstance(message, ToolMessage)]
    assert feedback[-1]["unreadSourceKeys"] == ["S1"]
    assert feedback[-1]["retrySubmission"]["sectionIds"] == ["finding"]
    assert 0 < feedback[-1]["remainingSeconds"] <= 480
    assert feedback[-1]["reviewStillRequired"] is True
    assert result["answer"] == ANSWER
    assert research_bundle_is_accepted(result)
    assert len(instance.answer_sections) == 1
    assert instance.searches == 0
    rejected = [event for event in events if event["status"] == "failed"]
    assert len(rejected) == 1
    assert rejected[0]["summary"] == "本步操作未完成"


def test_unknown_reference_returns_exact_key_without_accepting_draft():
    instance, transport = agent([submit(answer="Unsupported reference. [S99]"), read()], [], max_steps=2)
    result = instance.run(question="Question")
    feedback = [json.loads(message.content) for message in transport.requests[1][0] if isinstance(message, ToolMessage)][-1]
    assert feedback["unknownSourceKeys"] == ["S99"]
    assert result["answer"] == ""
    assert result["candidateDraft"]["answer"] == "Unsupported reference. [S99]"
    assert not research_bundle_is_accepted(result)


def test_direct_submission_retains_draft_for_citation_repair_without_second_full_output():
    draft_id = "submitted-" + hashlib.sha256(ANSWER.encode()).hexdigest()[:16]
    instance, transport = agent([
        submit(), read(), call("submit_research_answer", sectionIds=[draft_id], coverage="complete"),
    ], [approve()])
    result = instance.run(question="Does version 2 change the format?")
    feedback = [json.loads(message.content) for message in transport.requests[1][0] if isinstance(message, ToolMessage)][-1]
    assert feedback["retrySubmission"] == {"sectionIds": [draft_id], "coverage": "complete", "limitations": []}
    assert "do not retype" in feedback["nextAction"]
    assert "answer" not in feedback["retrySubmission"]
    assert instance.answer_sections == {draft_id: ANSWER}
    assert result["answer"] == ANSWER
    assert research_bundle_is_accepted(result)
    assert len([entry for entry in result["modelSynthesis"]["trace"] if entry["stage"] == "review"]) == 1


@pytest.mark.parametrize("arguments", [
    {"answer": ANSWER, "sectionIds": ["one"]},
    {"sectionIds": ["one", "one"]},
    {"sectionIds": ["missing"]},
    {},
])
def test_submission_does_not_guess_missing_sections_or_merge_two_answer_authorities(arguments):
    instance, _ = agent([], [])
    instance.save_section({"sectionId": "one", "text": ANSWER})
    with pytest.raises(ValueError):
        instance.submitted_answer({"coverage": "complete", **arguments})


def test_failure_diagnostic_keeps_acquired_sources_separate_from_accepted_evidence():
    from core.runtime_episode_runner import _research_source_acquisition_diagnostic
    diagnostic = _research_source_acquisition_diagnostic({
        "researchContract": "agent-research.v1", "researchLoopState": {
            "phase": "research_agent", "readableSourceCount": 26,
            "selectedSourceCount": 0, "stopReason": "research_model_response_incomplete",
        },
    })
    assert diagnostic["readableSourceCount"] == 26
    assert diagnostic["selectedSourceCount"] == 0
    assert diagnostic["exhaustedForRun"] is False
    assert diagnostic["stopReason"] == "research_model_response_incomplete"
    assert "restart all searches" in diagnostic["recommendedNextAction"]


def test_reader_can_recover_exception_beyond_old_excerpt_boundary():
    exception = "Exception: files encrypted with version 1 must be migrated first."
    instance, _ = agent([read(find="Exception:"), submit(
        answer="Encrypted version 1 files require migration. [S1]",
    )], [approve()])
    instance.store = EvidenceStore()
    instance.store.add([source("Background information.\n" * 1500 + exception)])
    result = instance.run(question="Do encrypted version 1 files need migration?")
    assert research_bundle_is_accepted(result)
    assert exception in result["claimTable"][0]["evidenceExcerpt"]


def test_counterfactual_truncated_source_cannot_prove_the_missing_exception():
    exception = "Exception: encrypted files require migration."
    instance, _ = agent([read(find=exception), submit()], [], max_steps=2)
    result = instance.run(question="What are the exceptions?")
    assert not research_bundle_is_accepted(result)
    assert any("every_answer_citation_needs_observed_evidence" in row.get("error", "") for row in result["modelSynthesis"]["trace"])


def test_reviewer_reads_full_source_and_writer_locally_fixes_an_error_without_new_search():
    incorrect = "Version 2 changes the existing file format. [S1]"
    correction = call("review_research_answer", decision="revise", coverage="complete", corrections=[{
        "kind": "fact", "answerQuote": "changes the existing file format", "reason": "The specification says it retains the format.",
        "sourceKey": "S1", "evidenceQuote": FACT,
    }], limitations=[], nextQueries=[])
    instance, transport = agent([read(), submit(answer=incorrect), submit()], [read(), correction, approve()])
    result = instance.run(question="Does version 2 change the format?")
    assert result["answer"] == ANSWER
    assert research_bundle_is_accepted(result)
    assert result["modelSynthesis"]["revisionCount"] == 1
    assert instance.searches == 0
    last_writer = [request for request, _, reviewer in transport.requests if not reviewer][-1]
    assert any(isinstance(message, ToolMessage) and "revision_requested" in str(message.content)
               for message in last_writer)


def test_unlocated_review_objection_returns_to_writer_without_fabricating_an_offending_quote():
    false_veto = call("review_research_answer", decision="revise", coverage="complete", corrections=[{
        "kind": "fact", "answerQuote": "All files are deleted", "reason": "Unsupported deletion claim.",
    }])
    draft_id = "submitted-" + hashlib.sha256(ANSWER.encode()).hexdigest()[:16]
    instance, transport = agent([read(), submit(),
                                call("submit_research_answer", sectionIds=[draft_id], coverage="complete")], [false_veto, approve()])
    result = instance.run(question="Does version 2 change the format?")
    assert research_bundle_is_accepted(result)
    assert result["answer"] == ANSWER
    assert result["modelSynthesis"]["revisionCount"] == 1
    feedback = [json.loads(message.content) for message in transport.requests[-2][0] if isinstance(message, ToolMessage)][-1]
    assert feedback["review"]["corrections"][0]["answerQuote"] == ""
    assert feedback["review"]["unlocatedFindings"][0]["reason"] == "review_answer_quote_not_located"
    assert feedback["retrySubmission"]["sectionIds"] == [draft_id]
    assert len([request for request in transport.requests if request[2]]) == 2


def test_unverified_reviewer_quote_is_a_hypothesis_not_a_repeated_format_gate():
    correction = call("review_research_answer", decision="revise", coverage="complete", corrections=[{
        "kind": "fact", "answerQuote": "retains the existing on-disk format",
        "reason": "Verify whether an exception applies.", "sourceKey": "S1",
        "evidenceQuote": "An invented counterexample absent from the source.",
    }])
    instance, transport = agent([read(), submit(), read(), submit()], [correction, approve()])
    result = instance.run(question="Does version 2 change the format?")
    feedback = [json.loads(message.content) for message in transport.requests[-2][0] if isinstance(message, ToolMessage)]
    review = next(item["review"] for item in feedback if item.get("status") == "revision_requested")
    assert review["corrections"][0]["evidenceQuote"] == ""
    assert review["unverifiedCounterevidence"][0]["sourceKey"] == "S1"
    assert result["answer"] == ANSWER
    assert research_bundle_is_accepted(result)
    assert len([request for request in transport.requests if request[2]]) == 2


def test_review_preview_omission_does_not_erase_observed_source_identity():
    instance, transport = agent([read(maxChars=12000), call("read_research_source", sourceKey="S2", maxChars=12000),
                                 call("read_research_source", sourceKey="S3"),
                                 submit(answer="Supported synthesis. [S1] [S2] [S3]")], [approve()])
    instance.store = EvidenceStore()
    instance.store.add([source("A" * 12000, "https://example.org/1"), source("B" * 12000, "https://example.org/2"), source(FACT, "https://example.org/3")])
    result = instance.run(question="Compare the sources")
    payload = json.loads(transport.requests[-1][0][1].content)
    assert payload["observedSourceKeys"] == ["S1", "S2", "S3"]
    assert "S3" not in {item["sourceKey"] for item in payload["observedPassages"]}
    assert research_bundle_is_accepted(result)


def test_supported_partial_answer_survives_without_being_promoted_to_complete():
    limitations = ["Migration tooling is not covered by the available specification."]
    instance, _ = agent([read(), submit(coverage="partial", limitations=limitations)],
                        [approve(coverage="partial", limitations=limitations)])
    result = instance.run(question="Does the format change and which migration tools are provided?")
    assert research_answer_is_usable(result)
    assert not research_bundle_is_accepted(result)
    assert result["answer"] == ANSWER
    assert result["limitations"] == limitations


def test_source_provenance_cannot_be_forged_or_read_from_another_snapshot():
    store = EvidenceStore()
    store.add([source()])
    with pytest.raises(ValueError, match="every_answer_citation_needs_observed_evidence"):
        store.bind_answer(ANSWER)
    store.read("S1")
    store.add([source("Version 3 replaces the file format.")])
    assert store.index()[0]["citationKey"] == "S1"
    assert store.index()[1]["citationKey"] == "S2"
    assert store.bind_answer(ANSWER)[0]["evidenceExcerpt"] == FACT
    with pytest.raises(ValueError, match="every_answer_citation_needs_observed_evidence"):
        store.bind_answer("Version 3 replaces the format. [S2]")


def test_search_uses_governed_callback_and_preserves_observations():
    transport = ScriptedTransport([
        call("search_research_sources", queries=["format v2 specification"]), read(), submit(),
    ], [approve()])
    requests = []

    def acquire(**kwargs):
        requests.append(kwargs)
        return {"sources": [source()], "diagnostics": []}

    instance = ResearchAgent(invoke=transport, acquire=acquire, progress=lambda **_: None,
                             writer_id="writer", reviewer_id="reviewer")
    result = instance.run(question="Does version 2 change the file format?")
    assert research_bundle_is_accepted(result)
    assert len(requests) == 1
    assert requests[0]["queries"] == ["format v2 specification"]


def test_exhausted_discovery_does_not_block_fetching_a_known_original():
    requests = []
    instance = ResearchAgent(invoke=lambda *_: None, acquire=lambda **kw: requests.append(kw) or {
        "sources": [source()] if kw["urls"] else [],
        "diagnostics": [{"candidates": [{"url": source()["url"], "readStatus": "not_fetched"}]}],
    }, progress=lambda **_: None, writer_id="writer", reviewer_id="reviewer", max_searches=1)
    discovered = instance.execute_search({"queries": ["format specification"]})
    assert discovered["diagnostics"][0]["candidates"][0]["readStatus"] == "not_fetched"
    fetched = instance.execute_search({"queries": ["another query"], "urls": [source()["url"]]})
    assert requests[-1]["queries"] == []
    assert requests[-1]["urls"] == [source()["url"]]
    assert fetched["addedSourceKeys"] == ["S1"]
    assert fetched["skippedQueries"] == ["another query"]
    assert instance.searches == 1
    # Fetched documents remain unread until the researcher actually opens them.
    with pytest.raises(ValueError, match="every_answer_citation_needs_observed_evidence"):
        instance.store.bind_answer(ANSWER)
    instance.execute_read({"sourceKey": "S1"})
    assert instance.store.bind_answer(ANSWER)[0]["evidenceExcerpt"] == FACT


def test_original_links_are_recoverable_and_never_inherited_as_evidence():
    store = EvidenceStore()
    links = [{"url": f"https://example.org/original/{i}", "text": f"Original {i}"} for i in range(45)]
    store.add([{**source(), "links": links}])
    first = store.read("S1")
    second = store.read("S1", link_start=first["nextLinkStart"])
    assert first["links"] + second["links"] == links
    assert not first["linksAreReadEvidence"]
    assert len(store.sources) == 1
    assert store.bind_answer(ANSWER)[0]["supportingSources"][0]["url"] == source()["url"]
    assert "links" not in store.selected(store.bind_answer(ANSWER))[0]


def test_acquisition_feedback_distinguishes_not_fetched_from_network_failure():
    from runtimes.research.acquisition import acquisition_feedback

    rows = [{"url": f"https://example.org/{name}", "title": name, "snippet": "Discovery only"}
            for name in ("good", "slow", "original", "index")]
    rows[-1]["readSelectionReason"] = "navigation_candidate"
    result = acquisition_feedback([{"query": "site:example.org specification", "provider": "configured",
        "results": rows, "fetchedTopSources": [
            {"url": rows[0]["url"], "ok": True},
            {"url": rows[1]["url"], "ok": False, "failureClass": "timeout"},
        ]}])[0]
    assert [r["readStatus"] for r in result["candidates"]] == ["fetched", "fetch_failed", "not_fetched", "not_fetched"]
    assert result["candidates"][1]["readReason"] == "timeout"
    assert result["candidates"][3]["readReason"] == "navigation_candidate"
    assert "text" not in result["candidates"][0]


def test_researcher_can_switch_provider_without_repeating_the_same_search():
    requests = []
    instance = ResearchAgent(invoke=lambda *_: None, acquire=lambda **kw: requests.append(kw) or {},
                             progress=lambda **_: None, writer_id="writer", reviewer_id="reviewer", max_searches=2)
    instance.execute_search({"queries": ["site:example.org original"], "searchEngine": "metaso"})
    instance.execute_search({"queries": ["site:example.org original"], "searchEngine": "bing_cn"})
    assert [r["search_engine"] for r in requests] == ["metaso", "bing_cn"]
    assert instance.searches == 2
    assert instance.execute_search({"queries": ["site:example.org original"], "searchEngine": "baidu"})["status"] == "search_budget_exhausted"
    assert len(requests) == 2


def test_cancel_does_not_call_the_model_or_acquire_sources():
    instance, transport = agent([], [], cancelled=lambda: True)
    with pytest.raises(InterruptedError, match="research_cancelled"):
        instance.run(question="Question")
    assert not transport.requests


def test_timeout_closes_the_same_progress_node_and_cannot_return_success():
    events = []
    def invoke(*_args, **_kwargs):
        raise TimeoutError("fixture timeout")
    instance = ResearchAgent(invoke=invoke, acquire=None, progress=lambda **event: events.append(event),
                             writer_id="configured-writer", reviewer_id="configured-reviewer")
    result = instance.run(question="Question")
    assert result["answer"] == ""
    assert events[0]["status"] == "active"
    assert events[-1]["status"] == "failed"
    assert events[0]["nodeId"] == events[-1]["nodeId"]
    assert result["modelSynthesis"]["trace"][0]["errorType"] == "TimeoutError"


def test_numeric_read_arguments_follow_standard_tool_parsing():
    instance, _ = agent([read(start="0", maxChars="6000"), submit()], [approve()])
    assert research_bundle_is_accepted(instance.run(question="Does version 2 change the format?"))


@pytest.mark.parametrize("recover", [True, False])
def test_incomplete_generation_recovery_is_bounded_and_keeps_existing_reads(recover):
    actions = iter([read(), IncompleteModelResponse("research_model_response_incomplete"),
                    submit() if recover else IncompleteModelResponse("research_model_response_incomplete")])
    requests = []
    def invoke(messages, tools, *, reviewer, **_kwargs):
        requests.append((list(messages), reviewer))
        if reviewer:
            return approve()
        action = next(actions)
        if isinstance(action, Exception):
            raise action
        return action
    instance = ResearchAgent(invoke=invoke, acquire=None, progress=lambda **_: None,
                             writer_id="configured-writer", reviewer_id="configured-reviewer")
    instance.store.add([source()])
    result = instance.run(question="Does version 2 change the format?")
    assert len([item for item in requests if not item[1]]) == 3
    assert instance.searches == 0
    assert len(instance.store.read_refs) == 1
    assert research_bundle_is_accepted(result) is recover
    if not recover:
        assert result["answer"] == ""
        assert result["modelSynthesis"]["fallbackReason"] == "research_model_response_incomplete"


def test_repeating_the_same_invalid_arguments_stops_before_the_model_step_limit():
    instance, transport = agent([read(start=-1)] * 4, [])
    result = instance.run(question="Question")
    assert not result["answer"]
    assert len(transport.requests) == 3


def test_repeated_prose_or_review_refusals_are_bounded_not_success():
    instance, transport = agent([AIMessage(content="Done.")] * 3, [], max_steps=3)
    result = instance.run(question="Question")
    assert not result["answer"]
    assert len(transport.requests) == 3
    assert result["modelSynthesis"]["fallbackReason"] == "research_step_budget_exhausted"


def test_review_has_no_search_write_command_or_delegation_authority():
    instance, transport = agent([read(), submit()], [approve()])
    instance.run(question="Question")
    review_tools = next(tools for _, tools, reviewer in transport.requests if reviewer)
    assert {tool["function"]["name"] for tool in review_tools} == {
        "read_research_source", "review_research_answer",
    }
    assert '"$ref"' not in json.dumps(review_tools)


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("output_budget", [4096, 8192, None])
@pytest.mark.parametrize("explicit_batch", [False, True])
def test_broker_persistence_surface_and_episode_share_the_same_review(monkeypatch, partial, output_budget, explicit_batch):
    from core.tools import research_broker as broker
    from core.tools import research_ledger
    from core.runtime_episode_runner import _research_evidence_status
    from core.tool_surface import _render_research_broker_surface
    from core.database import db
    from erc.runtime_context import get_runtime_context

    db.create_or_update_session("research-owner-session", "Research context test")
    db.create_run_record("research-owner-run", "research-owner-session")

    limitations = ["Migration tooling has not been verified."] if partial else []
    transport = ScriptedTransport(
        ([call("search_research_sources", queries=["specification v2", "original format"],
               urls=["https://example.org/spec/v2", "https://example.org/original"], searchEngine="bing_cn")]
         if explicit_batch else []) + [read(), submit(coverage="partial" if partial else "complete", limitations=limitations)],
        [approve(coverage="partial" if partial else "complete", limitations=limitations)],
    )

    class Model:
        def __init__(self, reviewer):
            self.reviewer = reviewer
            self._meta = {"model_ref": "test-reviewer" if reviewer else "test-writer", "global_max_tokens": output_budget}

        def bind_tools(self, tools, **kwargs):
            self.tools = tools
            return self

        def invoke(self, messages, **kwargs):
            assert kwargs.get("max_tokens") == output_budget
            assert get_runtime_context()["run_id"] == "research-owner-run"
            assert get_runtime_context()["session_id"] == "research-owner-session"
            assert get_runtime_context()["runtime_episode_id"] == "research-owner-episode"
            return transport(messages, self.tools, reviewer=self.reviewer, seconds=kwargs["timeout"])

    monkeypatch.setattr(broker, "_create_web_research_architect_llm_candidates", lambda: [(Model(False), "test-writer", "subagent")])
    monkeypatch.setattr(broker, "_create_web_research_reviewer_llm_candidates", lambda _: [(Model(True), "test-reviewer", "supervisor")])
    reads = []

    def read_page(**kwargs):
        reads.append(kwargs)
        return json.dumps(source())

    monkeypatch.setattr(broker, "_source_router_read", read_page)
    progress = []
    monkeypatch.setattr(broker, "_report_research_progress", lambda **event: progress.append(event))
    searches = []
    monkeypatch.setattr(broker, "_source_router_search", lambda **kwargs: searches.append(kwargs) or json.dumps({"ok": True, "provider": "bing_cn", "results": []}))
    bundle = broker._run_agent_owned_research(
        question="Does version 2 change the file format?", research_intent="format comparison",
        source_policy="authoritative", freshness="current", allowed_domains=["example.org"],
        blocked_domains=[], use_agent_browser_profile=False, tool_call_id="test-read",
        max_shards=1, max_rounds=2, preferred_language="en", seed_urls=[] if explicit_batch else ["https://example.org/spec/v2"],
        deliverable="evidence_bundle", experience_reuse={},
        state={"run_id": "research-owner-run", "session_id": "research-owner-session", "runtime_episode_id": "research-owner-episode"},
    )
    assert len(reads) == (2 if explicit_batch else 1)
    search_events = [event for event in progress if event.get("stage") == "source_search"]
    started_ids = {event["nodeId"] for event in search_events if event["status"] == "active"}
    final_status = {event["nodeId"]: event["status"] for event in search_events}
    assert started_ids and all(final_status[node] in {"completed", "failed"} for node in started_ids)
    if explicit_batch:
        assert {item["url"] for item in reads} == {"https://example.org/spec/v2", "https://example.org/original"}
        assert {item["query"] for item in searches} == {"specification v2", "original format"}
        assert all(item["search_engine"] == "bing_cn" for item in searches)
    assert bundle["answer"] == ANSWER
    assert bundle["usableAnswer"] is True
    assert bundle["ok"] is True
    assert bundle["deliveryReady"] is not partial
    stored = broker._store_evidence(bundle, state={"session_id": "research-agent-test"})
    reloaded = research_ledger.get_evidence_bundle(stored["evidenceBundleId"])
    assert research_answer_is_usable(reloaded)
    visible = broker._visible_bundle(reloaded)
    assert visible["answer"] == ANSWER
    surface = _render_research_broker_surface(visible, "", budget=6000)
    assert ANSWER in surface
    if partial:
        assert "partial answer" in surface
    else:
        assert research_ledger._experience_acceptance_issues(reloaded) == []
    accepted, reasons = _research_evidence_status(
        brief={"goal": bundle["question"]}, run_payload=reloaded,
        answer_pack=reloaded["researchAnswerPack"], source_items=reloaded["sourceMatrix"],
        claim_items=reloaded["claimTable"], answer=ANSWER, research_ref="research://bundle/test",
        freshness="current", source_policy="authoritative", seed_urls=[], allowed_domains=[],
    )
    assert accepted is not partial, reasons
    if partial:
        assert reasons == ["research_scope_partial"]


def test_research_experience_never_promotes_an_unreviewed_draft():
    from core.tools.research_ledger import _experience_acceptance_issues
    assert _experience_acceptance_issues({"question": "Question", "answer": "It worked.", "ok": True})


def saved_bundle(bundle_id="research-original", partial=False):
    from core.tools import research_broker as broker
    limitations = ["Migration tools remain unverified."] if partial else []
    instance, _ = agent([read(), submit(coverage="partial" if partial else "complete", limitations=limitations)],
                        [approve(coverage="partial" if partial else "complete", limitations=limitations)])
    bundle = instance.run(question="Does version 2 change the file format?")
    bundle.update(evidenceBundleId=bundle_id, sourceMatrix=bundle["sourceUrls"],
                  researchEvidenceBank={"sources": list(instance.store.sources.values())})
    bundle["researchAnswerPack"] = broker._research_answer_pack(bundle)
    return bundle


@pytest.mark.parametrize("partial", [False, True])
def test_reviewed_answers_are_auto_saved_and_keep_their_exact_scope(partial):
    from core.tools import research_ledger as ledger
    stored = ledger.store_evidence_bundle(saved_bundle(partial=partial), ttl_seconds=60, scope="global")
    packs = ledger.search_experience_packs(query="file format")
    assert len(packs) == 1
    assert packs[0]["deliveryScope"] == ("partial" if partial else "complete")
    assert packs[0]["researchResult"] == ANSWER
    assert stored["experienceUpdate"]["status"] == "created"


def test_save_replay_preserves_usage_archive_and_deleted_state():
    from core.tools import research_ledger as ledger
    bundle = saved_bundle()
    stored = ledger.store_evidence_bundle(bundle, ttl_seconds=60, scope="global")
    pack_id = stored["experienceUpdate"]["experiencePackId"]
    ledger.get_experience_pack(pack_id, record_usage=True)
    ledger.store_evidence_bundle(bundle, ttl_seconds=60, scope="global")
    assert ledger.get_experience_pack(pack_id)["usageCount"] == 1
    ledger.archive_experience_pack(pack_id)
    ledger.store_evidence_bundle(bundle, ttl_seconds=60, scope="global")
    assert ledger.get_experience_pack(pack_id) is None
    assert ledger.promote_experience_pack(bundle["evidenceBundleId"])["status"] == "archived"
    assert ledger.delete_experience_pack(pack_id, confirm=True)
    replay = ledger.store_evidence_bundle(bundle, ttl_seconds=60, scope="global")
    assert replay["experienceUpdate"]["reason"] == "experience_deleted"
    assert ledger.promote_experience_pack(bundle["evidenceBundleId"]) is None
    assert ledger.list_experience_packs(include_archived=True) == []


def test_revision_updates_one_record_without_overwriting_a_concurrent_revision():
    from core.tools import research_ledger as ledger
    original = ledger.store_evidence_bundle(saved_bundle(), ttl_seconds=60, scope="global")
    pack_id = original["experienceUpdate"]["experiencePackId"]
    revision = saved_bundle("research-revision")
    revision.update(supersedesExperiencePackId=pack_id, supersedesBundleId=original["evidenceBundleId"])
    result = ledger.store_evidence_bundle(revision, ttl_seconds=60, scope="global")
    assert result["experienceUpdate"]["status"] == "updated"
    pack = ledger.get_experience_pack(pack_id)
    assert pack["revision"] == 2
    assert pack["previousBundleId"] == original["evidenceBundleId"]
    conflict = {**revision, "evidenceBundleId": "research-conflicting"}
    result = ledger.store_evidence_bundle(conflict, ttl_seconds=60, scope="global")
    assert result["experienceUpdate"]["reason"] == "experience_revision_conflict"
    assert ledger.get_experience_pack(pack_id)["createdFromBundleId"] == "research-revision"
    assert len(ledger.list_experience_packs()) == 1


def test_saved_sources_outlive_cache_ttl_and_age_is_not_auto_archival(monkeypatch):
    from datetime import datetime, timezone
    from core.tools import research_ledger as ledger
    stored = ledger.store_evidence_bundle(saved_bundle(), ttl_seconds=60, scope="global")
    monkeypatch.setattr(ledger.time, "time", lambda: 10_000_000_000)
    assert ledger.get_evidence_bundle(stored["evidenceBundleId"])
    outcome = ledger.maintain_experience_packs(now=datetime(2099, 1, 1, tzinfo=timezone.utc))
    assert outcome["expiredArchivedCount"] == 0
    assert ledger.list_experience_packs()[0]["reuseEligible"]


def test_chinese_paraphrase_search_returns_candidate_without_claiming_equivalence():
    from core.tools import research_ledger as ledger
    bundle = saved_bundle()
    stored = ledger.store_evidence_bundle(bundle, ttl_seconds=60, scope="global")
    payload = ledger._read_store()
    payload["experiencePacks"][0]["title"] = "版本二文件格式与迁移要求"
    ledger._write_store(payload)
    matches = ledger.search_experience_packs(query="请说明文件格式是否改变")
    assert matches[0]["createdFromBundleId"] == stored["evidenceBundleId"]


def test_exact_reuse_keeps_review_receipt_and_needs_no_model_or_network(monkeypatch):
    from core.tools import research_broker as broker, research_ledger as ledger
    bundle = saved_bundle()
    stored = ledger.store_evidence_bundle(bundle, ttl_seconds=60, scope="global")
    pack = ledger.get_experience_pack(stored["experienceUpdate"]["experiencePackId"])
    decision = broker._experience_reuse_decision([pack], question=bundle["question"], source_policy="authoritative", freshness="auto")
    assert decision["reuseDecision"] == "reuse"
    reused = broker._bundle_from_reused_pack(pack, question=bundle["question"], reuse=decision, deliverable="evidence_bundle")
    assert reused["deliveryReady"], reused["researchAnswerPack"].get("missingOrStaleReasons")
    assert reused["answer"] == bundle["answer"]
    assert reused["independentReview"] == bundle["independentReview"]
    assert "pack" not in reused["experienceReuse"]
    rendered = json.loads(broker._render_payload(reused, max_chars=36000))
    assert rendered["researchAnswerPack"]["answer"] == bundle["answer"]


def test_existing_sources_can_answer_a_related_question_without_fresh_network():
    original = saved_bundle()
    transport = ScriptedTransport([read(), submit()], [approve()])
    instance = ResearchAgent(invoke=transport, acquire=lambda **_: pytest.fail("unnecessary acquisition"), progress=lambda **_: None,
                             writer_id="configured-writer", reviewer_id="configured-reviewer")
    instance.store.restore(original["researchEvidenceBank"]["sources"])
    result = instance.run(question="Is the existing format retained in version 2?", previous_answer={"answer": original["answer"]})
    assert research_bundle_is_accepted(result)
    assert result["modelSynthesis"]["searchCount"] == 0
    assert "previousAnswer" in transport.requests[0][0][-1].content
    assert result["sourceUrls"][0]["retrievedAt"] == original["sourceUrls"][0]["retrievedAt"]


def test_changed_saved_source_cannot_inherit_original_read_proof():
    from copy import deepcopy
    rows = deepcopy(saved_bundle()["researchEvidenceBank"]["sources"])
    rows[0]["text"] = "The opposite of the source."
    with pytest.raises(ValueError, match="digest_mismatch"):
        EvidenceStore().restore(rows)


def test_durable_source_pages_recover_full_text_under_small_surface_budget(monkeypatch, tmp_path):
    import re
    from core.tools import research_broker as broker, research_ledger as ledger
    from core.tool_surface import _render_research_broker_surface
    from runtimes.research.evidence import digest
    from tests.core.research_scope_fixture import research_sessions

    _, create, _ = research_sessions(monkeypatch, tmp_path)
    context = create("source-pages")
    bundle = saved_bundle()
    body = "Original paragraph\n" * 1000 + "End canary."
    row = bundle["researchEvidenceBank"]["sources"][0]
    row.update(text=body, contentChars=len(body), originalContentChars=len(body))
    row["readEvidence"].update(contentChars=len(body), contentSha256=digest(body))
    stored = ledger.store_evidence_bundle(bundle, ttl_seconds=60, scope=context["session_id"])
    # No transient observation or network is needed after reload.
    monkeypatch.setattr(broker, "_source_router_read", lambda **_: pytest.fail("unnecessary fetch"))
    offset, pages = 0, []
    while True:
        page = json.loads(broker.research_broker.func(mode="get_evidence", evidenceBundleId=stored["evidenceBundleId"],
                                                   sourceKey="S1", startChar=offset, maxChars=12000, state=context))
        assert page["contentSha256"] == digest(body)
        visible = _render_research_broker_surface(page, "", budget=1400)
        pages.append(visible.split("<source>\n", 1)[1].rsplit("\n</source>", 1)[0])
        assert len(visible) <= 1400
        match = re.search(r"nextOffset: (\d+|None)", visible)
        if match[1] == "None":
            break
        offset = int(match[1])
        assert offset == sum(map(len, pages))
    assert "".join(pages) == body


def test_large_saved_answer_lookup_keeps_identity_and_honest_preview(monkeypatch, tmp_path):
    from core.tools import research_broker as broker, research_ledger as ledger
    from tests.core.research_scope_fixture import research_sessions

    _, create, _ = research_sessions(monkeypatch, tmp_path)
    context = create("answer-preview")
    stored = ledger.store_evidence_bundle(saved_bundle(), ttl_seconds=60, scope=context["session_id"])
    pack_id = stored["experienceUpdate"]["experiencePackId"]
    payload = ledger._read_store()
    payload["experiencePacks"][0]["researchResult"] = "Answer content. " * 2000
    ledger._write_store(payload)
    result = json.loads(broker.research_broker.func(mode="get_experience", experiencePackId=pack_id, state=context))
    assert result["ok"]
    assert result["item"]["experiencePackId"] == pack_id
    assert result["item"]["answerComplete"] is False
    assert result["item"]["answerChars"] == 31999
    assert stored["evidenceBundleId"] in result["detailTool"]


def test_selected_research_evidence_survives_child_grandchild_and_context_rebuild():
    from core.context.delegation import build_delegation_context, latest_delegation_context
    from core.tools.native.delegation import _inject_inherited_handoffs_into_tasks
    ref = "research://bundle/selected"
    selected = {"handoffRefId": "handoff-selected", "producerEpisodeId": "episode_selected", "kind": "research",
                "researchRefs": [ref], "rawRef": "toolobs://selected", "summary": "Supported answer with a known limitation."}
    unrelated = {"handoffRefId": "handoff-other", "researchRefs": ["research://bundle/unrelated"], "rawRef": "toolobs://unrelated"}
    parent_task = _inject_inherited_handoffs_into_tasks([
        {"goal": "Implement using the selected evidence", "researchRefs": [ref]},
    ], {"handoffRefs": [selected, unrelated]})[0]
    parent_context = build_delegation_context(mode="serial", task_brief=parent_task)
    rebuilt = latest_delegation_context([parent_context])
    grandchild = _inject_inherited_handoffs_into_tasks([
        {"goal": "Independently verify applicability", "researchRefs": [ref]},
    ], rebuilt)[0]
    handoffs = grandchild["context"]["upstreamHandoffs"]
    assert len(handoffs) == 1
    assert handoffs[0]["rawRef"] == "toolobs://selected"
    assert grandchild["context"]["evidenceResolutionDiagnostics"]["unresolvedRefs"] == []
    assert "toolobs://unrelated" not in json.dumps(grandchild)
    assert not grandchild.get("writeSet")
