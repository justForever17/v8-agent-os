from __future__ import annotations

import hashlib
import json
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import AIMessage

import core.tools.research_broker as research_module


_QUESTION = "What evidence supports the current Research Runtime architecture?"
_AS_OF = "2026-07-29T03:00:00Z"
_SOURCE_TOPICS = (
    "architecture",
    "deployment",
    "evidence",
    "freshness",
    "security",
    "performance",
    "compatibility",
    "decision",
)
_SECTION_VOCABULARIES = (
    "architecture boundary interface cohesion dependency ownership contract responsibility",
    "deployment rollout rollback telemetry failure recovery operations observability",
    "evidence provenance excerpt citation authority verification traceability receipt",
    "temporal release version timestamp freshness revision history currency lifecycle",
    "security permission isolation credential policy audit governance containment",
    "performance latency throughput concurrency queue capacity benchmark saturation",
    "compatibility migration deprecation caller adoption transition protocol interoperability",
    "decision tradeoff limitation uncertainty recommendation validation action consequence",
)


class _Candidate:
    def __init__(
        self,
        *,
        max_tokens: int,
        model_ref: str,
        supports_no_think: bool = False,
    ) -> None:
        self._meta = {
            "model_ref": model_ref,
            "global_max_tokens": max_tokens,
            "global_context_window": 1_000_000,
            "thinking_control": {"supportsNoThink": supports_no_think},
        }


class _DualInvocationCandidate(_Candidate):
    def __init__(self) -> None:
        super().__init__(max_tokens=4096, model_ref="fixture::dual")
        self.lock = threading.Lock()
        self.sync_calls = 0
        self.async_calls = 0

    def invoke(self, _messages, **_kwargs):  # noqa: ANN001
        with self.lock:
            self.sync_calls += 1
        return AIMessage(content="sync")

    async def ainvoke(self, _messages, **_kwargs):  # noqa: ANN001
        with self.lock:
            self.async_calls += 1
        return AIMessage(content="async")


def _background_material(messages, title: str) -> str:  # noqa: ANN001
    chunks: list[tuple[int, str]] = []
    prefix = f"[BACKGROUND MATERIAL: {title} |"
    for message in messages:
        content = str(getattr(message, "content", ""))
        if not content.startswith(prefix):
            continue
        header, body = content.split("\n", 1)
        body = body.rsplit("\n[/BACKGROUND MATERIAL]", 1)[0]
        part = header.rsplit("|", 1)[-1].strip().rstrip("]")
        part_index = int(part.split("/", 1)[0])
        chunks.append((part_index, body))
    return "".join(body for _part, body in sorted(chunks))


def _sources() -> list[dict]:
    sources: list[dict] = []
    for index, topic in enumerate(_SOURCE_TOPICS, start=1):
        body = (
            f"The current Research Runtime {topic} source records a directly verified implementation fact, its operating condition, "
            f"and the boundary beyond which the {topic} evidence does not apply. "
            f"The dated {topic} record identifies a 2026 release state and a reproducible verification method. "
        ) * 12
        body = body.strip()
        retrieved_at = f"2026-07-29T03:{index:02d}:00Z"
        sources.append(
            {
                "sourceId": f"source-{index}",
                "citationKey": f"S{index}",
                "title": f"Verified {topic.title()} Source",
                "url": f"https://source-{index}.example/research-runtime",
                "host": f"source-{index}.example",
                "tier": "primary" if index <= 5 else "secondary",
                "authorityScore": 90 - index,
                "selectedForEvidence": True,
                "sourceQualityGate": {"selectedForEvidence": True},
                "retrievedAt": retrieved_at,
                "publishedAt": f"2026-07-{19 + index:02d}T00:00:00Z",
                "sourceDate": f"2026-07-{19 + index:02d}",
                "sourceDateKind": "published",
                "temporalEvidence": {
                    "publishedAt": f"2026-07-{19 + index:02d}T00:00:00Z",
                    "sourceDate": f"2026-07-{19 + index:02d}",
                },
                "contentChars": len(body),
                "originalContentChars": len(body),
                "omittedChars": 0,
                "evidenceSelection": "read_body",
                "readEvidence": {
                    "verified": True,
                    "contentChars": len(body),
                    "contentSha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    "retrievedAt": retrieved_at,
                },
                "text": body,
            }
        )
    return sources


def _accepted_plan(sources: list[dict]) -> dict:
    claims: list[dict] = []
    for index, (source, topic) in enumerate(zip(sources, _SOURCE_TOPICS, strict=True), start=1):
        evidence_candidates = research_module._architect_evidence_candidates(
            source,
            _QUESTION,
            limit=research_module._RESEARCH_ARCHITECT_EVIDENCE_CANDIDATE_COUNT,
        )
        claims.append(
            {
                "claimId": f"claim-{topic}",
                "claim": (
                    f"The verified {topic} record establishes one concrete operational fact, "
                    "its applicable condition, and its evidence boundary."
                ),
                "claimType": "source_fact",
                "supportingSources": [f"S{index}"],
                "evidenceExcerptKey": evidence_candidates[0]["evidenceExcerptKey"],
                "confidence": "high",
            }
        )
    return {
        "reviewDecision": "accept",
        "reviewReasons": ["The verified plan covers every required source."],
        "headline": "Current Research Runtime evidence",
        "claimTable": claims,
        "answerOutline": [
            {
                "sectionId": f"planned-{index + 1}",
                "title": title,
                "objective": title,
                "claimIds": [claim["claimId"] for claim in claims[index * 2 : (index + 1) * 2]],
            }
            for index, title in enumerate(
                (
                    "Direct conclusion and architecture",
                    "Evidence and freshness",
                    "Security and performance",
                    "Compatibility, limitations, and action",
                )
            )
        ],
        "compositeInferences": [],
        "conflictMatrix": [],
        "missingEvidence": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "assumptions": [],
        "temporalAssessment": {"asOf": _AS_OF, "status": "current"},
    }


def _section_answer(task: dict) -> str:
    sequence = int(task["sequence"])
    citation_keys = list(task["requiredCitationKeys"])
    first_vocabulary = _SECTION_VOCABULARIES[(sequence - 1) * 2]
    second_vocabulary = _SECTION_VOCABULARIES[(sequence - 1) * 2 + 1]
    first_unit = (first_vocabulary + " ") * 14
    second_unit = (second_vocabulary + " ") * 14
    return (
        f"## Runtime section {sequence}\n\n"
        f"{first_unit}[{citation_keys[0]}].\n\n"
        f"{second_unit}[{citation_keys[-1]}]."
    )


class _StagedInvocation:
    def __init__(
        self,
        plan: dict,
        *,
        permanently_failed_section: str = "",
        permanently_failed_section_by_model: dict[str, str] | None = None,
        reviewer_revision_failed_section_by_model: dict[str, str] | None = None,
        split_after_retries_section: str = "",
        reject_first_review_without_evidence_gap: bool = False,
        same_evidence_rejected_review_numbers: set[int] | None = None,
        invalid_schema_review_numbers: set[int] | None = None,
    ) -> None:
        self.plan = plan
        self.permanently_failed_section = permanently_failed_section
        self.permanently_failed_section_by_model = dict(
            permanently_failed_section_by_model or {}
        )
        self.reviewer_revision_failed_section_by_model = dict(
            reviewer_revision_failed_section_by_model or {}
        )
        self.split_after_retries_section = split_after_retries_section
        self.reject_first_review_without_evidence_gap = reject_first_review_without_evidence_gap
        self.same_evidence_rejected_review_numbers = set(
            same_evidence_rejected_review_numbers or set()
        )
        self.invalid_schema_review_numbers = set(invalid_schema_review_numbers or set())
        self.lock = threading.Lock()
        self.section_attempts: dict[str, int] = defaultdict(int)
        self.section_attempts_by_model: dict[tuple[str, str], int] = defaultdict(int)
        self.section_max_tokens: list[int] = []
        self.section_source_payloads: list[list[dict]] = []
        self.review_payloads: list[dict] = []
        self.review_prompts: list[str] = []
        self.structure_disable_thinking: list[bool] = []
        self.stage_disable_thinking: list[tuple[str, bool]] = []
        self.reviewer_previous_section_flags: list[bool] = []
        self.completion_order: list[str] = []
        self.active_sections = 0
        self.max_active_sections = 0
        self.two_initial_sections_entered = threading.Event()
        self.section_two_valid = threading.Event()
        self.initial_section_entries = 0

    def __call__(
        self,
        _candidate,  # noqa: ANN001
        messages,  # noqa: ANN001
        *,
        seconds: float,
        max_tokens: int,
        disable_thinking: bool = False,
    ) -> AIMessage:
        del seconds
        prompt = "\n".join(str(getattr(message, "content", "")) for message in messages)
        section_contract = _background_material(messages, "Section contract")
        if section_contract:
            self.stage_disable_thinking.append(("section", disable_thinking))
            task = json.loads(section_contract)
            model_ref = str((_candidate[0]._meta or {}).get("model_ref") or "")
            reviewer_revision = "同一独立 Reviewer" in prompt
            if reviewer_revision:
                self.reviewer_previous_section_flags.append(
                    "PREVIOUS_ACCEPTED_SECTION:" in prompt
                )
            source_material = _background_material(messages, "Verified sources for this section")
            with self.lock:
                self.section_source_payloads.append(json.loads(source_material))
            return self._section_response(
                task,
                model_ref=model_ref,
                max_tokens=max_tokens,
                reviewer_revision=reviewer_revision,
            )

        review_candidate = _background_material(messages, "Candidate answer")
        if review_candidate:
            self.stage_disable_thinking.append(("review", disable_thinking))
            payload = json.loads(review_candidate)
            with self.lock:
                self.review_payloads.append(payload)
                self.review_prompts.append(prompt)
                review_number = len(self.review_payloads)
            if review_number in self.invalid_schema_review_numbers:
                return AIMessage(
                    content=json.dumps(
                        {
                            "reviewDecision": "accept",
                            "reviewReasons": None,
                            "questionCoverage": True,
                            "claimEntailment": True,
                            "freshnessAdequacy": True,
                            "unsupportedClaims": None,
                            "criticalMissingEvidence": None,
                            "recommendedNextQueries": None,
                        }
                    )
                )
            if (
                self.reject_first_review_without_evidence_gap and review_number == 1
            ) or review_number in self.same_evidence_rejected_review_numbers:
                return AIMessage(
                    content=json.dumps(
                        {
                            "reviewDecision": "retry",
                            "reviewReasons": ["Clarify the boundary between source facts and cross-source conclusions."],
                            "questionCoverage": True,
                            "claimEntailment": False,
                            "freshnessAdequacy": True,
                            "unsupportedClaims": ["One synthesis sentence is not clearly labelled."],
                            "criticalMissingEvidence": [],
                            "recommendedNextQueries": [],
                        }
                    )
                )
            return AIMessage(
                content=json.dumps(
                    {
                        "reviewDecision": "accept",
                        "reviewReasons": ["The assembled answer is fully supported and current."],
                        "questionCoverage": True,
                        "claimEntailment": True,
                        "freshnessAdequacy": True,
                        "unsupportedClaims": [],
                        "criticalMissingEvidence": [],
                        "recommendedNextQueries": [],
                    }
                )
            )

        structure_material = _background_material(
            messages,
            "Immutable canonical claim plan",
        )
        if structure_material:
            self.structure_disable_thinking.append(disable_thinking)
            self.stage_disable_thinking.append(("structure", disable_thinking))
            canonical = json.loads(structure_material)
            return AIMessage(
                content=json.dumps(
                    {
                        "answerOutline": canonical["deterministicOutline"],
                        "compositeInferences": [],
                    }
                )
            )
        raise AssertionError("The 4096-token candidate must use section calls instead of a monolithic writer call.")

    def _section_response(
        self,
        task: dict,
        *,
        model_ref: str,
        max_tokens: int,
        reviewer_revision: bool,
    ) -> AIMessage:
        section_id = str(task["sectionId"])
        with self.lock:
            self.section_attempts[section_id] += 1
            self.section_attempts_by_model[(model_ref, section_id)] += 1
            attempt = self.section_attempts[section_id]
            self.section_max_tokens.append(max_tokens)
            self.active_sections += 1
            self.max_active_sections = max(self.max_active_sections, self.active_sections)
            if attempt == 1:
                self.initial_section_entries += 1
                if self.initial_section_entries >= 2:
                    self.two_initial_sections_entered.set()
        try:
            if attempt == 1:
                assert self.two_initial_sections_entered.wait(timeout=2)
            if section_id == "section_1" and attempt == 1:
                assert self.section_two_valid.wait(timeout=2)
            failed_section = (
                (
                    self.reviewer_revision_failed_section_by_model.get(model_ref)
                    if reviewer_revision
                    else ""
                )
                or self.permanently_failed_section_by_model.get(model_ref)
                or self.permanently_failed_section
            )
            if failed_section and (
                section_id == failed_section
                or section_id.startswith(f"{failed_section}_")
            ):
                return AIMessage(content="incomplete section without its marker")
            if section_id == self.split_after_retries_section and attempt <= 2:
                return AIMessage(content="incomplete section without its marker")
            if section_id == "section_2" and attempt == 1:
                return AIMessage(content="first local draft intentionally omits its completion marker")

            body = _section_answer(task)
            if reviewer_revision:
                body += (
                    "\n\nThe reviewer-guided revision now labels the evidence boundary explicitly "
                    f"[{task['requiredCitationKeys'][0]}]."
                )
            marker = (
                f"<!-- {research_module._RESEARCH_ARCHITECT_SECTION_COMPLETE_MARKER_PREFIX}:"
                f"{section_id} -->"
            )
            with self.lock:
                self.completion_order.append(section_id)
            if section_id == "section_2":
                self.section_two_valid.set()
            return AIMessage(content=f"{body}\n\n{marker}")
        finally:
            with self.lock:
                self.active_sections -= 1


def test_segmented_writer_profile_uses_4096_but_not_a_high_output_limit() -> None:
    low = research_module._architect_segmented_writer_profile(
        (_Candidate(max_tokens=4096, model_ref="fixture::low"), "fixture::low", "summary")
    )
    high = research_module._architect_segmented_writer_profile(
        (
            _Candidate(
                max_tokens=32_768,
                model_ref="fixture::high",
                supports_no_think=True,
            ),
            "fixture::high",
            "summary",
        )
    )
    unknown = research_module._architect_segmented_writer_profile(
        (type("UnknownCandidate", (), {"_meta": {"model_ref": "fixture::unknown"}})(), "fixture::unknown", "summary")
    )

    assert low == {
        "enabled": True,
        "configuredMaxTokens": 4096,
        "sectionCount": 4,
        "sectionMaxTokens": research_module._RESEARCH_ARCHITECT_SECTION_MAX_TOKENS,
        "targetMinChars": 1625,
        "targetMaxChars": 2750,
    }
    assert high["configuredMaxTokens"] == 32_768
    assert high["enabled"] is False
    assert unknown["configuredMaxTokens"] is None
    assert unknown["enabled"] is True


def test_architect_parallel_workers_use_sync_client_instead_of_cross_loop_async_client() -> None:
    llm = _DualInvocationCandidate()
    candidate = (llm, "fixture::dual", "summary")

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _index: research_module._invoke_architect_candidate_with_deadline(
                    candidate,
                    [],
                    seconds=2,
                    max_tokens=128,
                ),
                range(2),
            )
        )

    assert [response.content for response in responses] == ["sync", "sync"]
    assert llm.sync_calls == 2
    assert llm.async_calls == 0


def test_segment_tasks_are_stable_disjoint_and_enforce_their_citation_scope() -> None:
    claims = [
        {
            "claimId": f"claim-{index}",
            "claim": f"Distinct verified claim {index}",
            "supportingSources": [{"citationKey": f"S{index}"}],
        }
        for index in range(1, 9)
    ]
    plan = {
        "claimTable": claims,
        "answerOutline": [
            {
                "sectionId": f"planned-{index}",
                "title": title,
                "objective": title,
                "claimIds": [f"claim-{index * 2 - 1}", f"claim-{index * 2}"],
            }
            for index, title in enumerate(("one", "two", "three", "four"), start=1)
        ],
        "compositeInferences": [
            {"inferenceId": "combined", "premiseClaimIds": ["claim-3", "claim-4"]},
            {"inferenceId": "cross-section", "premiseClaimIds": ["claim-2", "claim-3"]},
        ],
        "conflictMatrix": [{"topic": "known boundary"}],
        "temporalAssessment": {"status": "current"},
    }

    first = research_module._architect_segment_tasks(
        plan,
        section_count=4,
        target_min_chars=1600,
        target_max_chars=2800,
    )
    second = research_module._architect_segment_tasks(
        plan,
        section_count=4,
        target_min_chars=1600,
        target_max_chars=2800,
    )

    assert first == second
    assert [task["sectionId"] for task in first] == [
        "section_1",
        "section_2",
        "section_3",
        "section_4",
    ]
    assert [[claim["claimId"] for claim in task["assignedClaims"]] for task in first] == [
        ["claim-1", "claim-2"],
        ["claim-3", "claim-4"],
        ["claim-5", "claim-6"],
        ["claim-7", "claim-8"],
    ]
    assert [task["requiredCitationKeys"] for task in first] == [
        ["S1", "S2"],
        ["S3", "S4"],
        ["S5", "S6"],
        ["S7", "S8"],
    ]
    assert first[0]["claimCitationChecklist"] == [
        {"claimId": "claim-1", "citationKeys": ["S1"]},
        {"claimId": "claim-2", "citationKeys": ["S2"]},
    ]
    assert all(task["minimumAcceptableChars"] < task["targetMinChars"] for task in first)
    assert first[1]["compositeInferences"] == [plan["compositeInferences"][0]]
    assert all(
        inference.get("inferenceId") != "cross-section"
        for task in first
        for inference in task["compositeInferences"]
    )
    assert research_module._architect_outline_inference_issues(
        plan["answerOutline"],
        claims,
        plan["compositeInferences"],
    ) == ["composite_inference_2_crosses_sections"]
    assert first[-1]["conflictMatrix"] == plan["conflictMatrix"]
    assert first[0]["directConclusionSection"] is True


    assert first[-1]["limitationsAndActionSection"] is True

    scoped_task = {
        **first[0],
        "targetMinChars": 0,
        "targetMaxChars": 1000,
        "minimumAcceptableChars": 0,
    }
    issues = research_module._architect_section_issues(
        "A sufficiently detailed local claim [S1] also cites an unassigned source [S3].",
        scoped_task,
        complete=True,
    )
    assert "section_citation_missing:S2" in issues
    assert "section_citation_out_of_scope:S3" in issues

    uneven_but_substantive = "A" * first[0]["minimumAcceptableChars"] + " [S1] [S2]"
    assert research_module._architect_section_issues(
        uneven_but_substantive,
        first[0],
        complete=True,
    ) == []


def test_segment_tasks_carry_required_facet_goals_from_verified_claim_lineage() -> None:
    claims = [
        {
            "claimId": "claim-timeline",
            "claim": "The timeline has a verified effective date.",
            "supportingSources": [
                {"citationKey": "S1", "researchFacetId": "timeline"}
            ],
        },
        {
            "claimId": "claim-penalties",
            "claim": "The penalty framework has a verified ceiling.",
            "supportingSources": [
                {"citationKey": "S2", "researchFacetId": "penalties"}
            ],
        },
    ]
    tasks = research_module._architect_segment_tasks(
        {
            "question": (
                "Research every item below.\n"
                "1. [timeline] Verify the effective date.\n"
                "2. [penalties] Verify the penalty framework."
            ),
            "requiredFacets": [
                {"facetId": "timeline", "goal": "Verify the effective date."},
                {"facetId": "penalties", "goal": "Verify the penalty framework."},
            ],
            "claimTable": claims,
            "answerOutline": [
                {
                    "sectionId": "timeline-section",
                    "title": "Timeline",
                    "objective": "Answer the timeline facet.",
                    "claimIds": ["claim-timeline"],
                },
                {
                    "sectionId": "penalty-section",
                    "title": "Penalties",
                    "objective": "Answer the penalties facet.",
                    "claimIds": ["claim-penalties"],
                },
            ],
        },
        section_count=2,
        target_min_chars=20,
        target_max_chars=400,
    )

    assert tasks[0]["facetIds"] == ["timeline"]
    assert tasks[0]["facetGoals"] == [
        {"facetId": "timeline", "goal": "Verify the effective date."}
    ]
    assert tasks[1]["facetIds"] == ["penalties"]


def test_segment_tasks_allocate_depth_by_claim_weight_without_lowering_total_target() -> None:
    claims = [
        {
            "claimId": f"claim-{index}",
            "claim": f"Distinct verified claim {index}",
            "supportingSources": [{"citationKey": f"S{index}"}],
        }
        for index in range(1, 11)
    ]
    plan = {
        "claimTable": claims,
        "answerOutline": [
            {"sectionId": "one", "claimIds": ["claim-1", "claim-2"]},
            {
                "sectionId": "two",
                "claimIds": ["claim-3", "claim-4", "claim-5", "claim-6"],
            },
            {"sectionId": "three", "claimIds": ["claim-7", "claim-8"]},
            {"sectionId": "four", "claimIds": ["claim-9", "claim-10"]},
        ],
        "compositeInferences": [],
        "conflictMatrix": [],
    }

    tasks = research_module._architect_segment_tasks(
        plan,
        section_count=4,
        target_min_chars=1625,
        target_max_chars=2750,
    )

    assert [task["targetMinChars"] for task in tasks] == [1300, 2600, 1300, 1300]
    assert tasks[0]["minimumAcceptableChars"] == 650
    assert tasks[1]["minimumAcceptableChars"] > tasks[0]["minimumAcceptableChars"]
    assert sum(task["targetMinChars"] for task in tasks) == 6500

    five_section_plan = {
        **plan,
        "answerOutline": [
            {"sectionId": "one", "claimIds": ["claim-1", "claim-2"]},
            {"sectionId": "two", "claimIds": ["claim-3", "claim-4"]},
            {"sectionId": "three", "claimIds": ["claim-5", "claim-6"]},
            {"sectionId": "four", "claimIds": ["claim-7", "claim-8"]},
            {"sectionId": "five", "claimIds": ["claim-9", "claim-10"]},
        ],
    }
    five_section_tasks = research_module._architect_segment_tasks(
        five_section_plan,
        section_count=4,
        target_min_chars=1625,
        target_max_chars=2750,
    )

    assert len(five_section_tasks) == 5
    assert 6500 <= sum(task["targetMinChars"] for task in five_section_tasks) < 7000


def test_single_claim_section_depth_is_bounded_by_its_verified_evidence_density() -> None:
    claim = {
        "claimId": "claim-one",
        "claim": "Path methods return Path objects and permit method chaining.",
        "supportingSources": [{"citationKey": "S1"}],
        "evidenceExcerpt": "Path methods return Path objects, which allows for method chaining.",
    }
    tasks = research_module._architect_segment_tasks(
        {
            "claimTable": [claim],
            "answerOutline": [{"sectionId": "one", "claimIds": ["claim-one"]}],
            "compositeInferences": [],
            "conflictMatrix": [],
        },
        section_count=4,
        target_min_chars=1625,
        target_max_chars=2750,
    )

    assert len(tasks) == 1
    assert 80 <= tasks[0]["minimumAcceptableChars"] <= 260
    assert tasks[0]["minimumAcceptableChars"] < tasks[0]["targetMinChars"]


def test_segment_tasks_do_not_promote_incidental_excerpt_anchors_to_writer_permissions() -> None:
    claim = {
        "claimId": "claim-install",
        "claim": "The product documents a native Windows installation path.",
        "supportingSources": [{"citationKey": "S1"}],
        "evidenceExcerpt": (
            "The adjacent setup example mentions Node.js 22, gpt-5.6-sol, "
            "and a 4096 tokens limit, but those details are not part of this claim."
        ),
    }

    tasks = research_module._architect_segment_tasks(
        {
            "claimTable": [claim],
            "answerOutline": [{"sectionId": "install", "claimIds": ["claim-install"]}],
            "compositeInferences": [],
        },
        section_count=1,
        target_min_chars=20,
        target_max_chars=400,
    )

    assert research_module._architect_hard_assertion_anchors(claim["claim"]) == []
    assert research_module._architect_hard_assertion_anchors(claim["evidenceExcerpt"])
    assert tasks[0]["permittedHardAnchors"] == []


def test_runtime_source_appendix_is_deterministic_and_numeric() -> None:
    sources = [
        {
            "citationKey": "S10",
            "title": "Tenth Source",
            "url": "https://example.com/ten",
            "publishedAt": "2026-07-10",
        },
        {
            "citationKey": "S2",
            "title": "Second Source",
            "url": "https://example.com/two",
            "updatedAt": "2026-07-20",
        },
        {
            "citationKey": "S1",
            "title": "First Source",
            "url": "https://example.com/one",
            "version": "v1.4",
        },
        {
            "citationKey": "S3",
            "title": "Older Foundation",
            "url": "https://example.com/older",
            "publishedAt": "2018-07-20",
        },
        {
            "citationKey": "S4",
            "title": "Undated Experience",
            "url": "https://example.com/undated",
            "tier": "secondary",
        },
        {
            "citationKey": "S5",
            "title": "Epoch Metadata",
            "url": "https://example.com/epoch",
            "updatedAt": "1696032739",
        },
        {
            "citationKey": "S6",
            "title": "Retrieval Alias",
            "url": "https://example.com/retrieval-alias",
            "retrievedAt": "2026-07-29T12:00:00Z",
            "sourceDate": "2026-07-29",
            "sourceDateKind": "retrieved_at",
        },
        {
            "citationKey": "S12",
            "title": "Unreferenced Source",
            "url": "https://example.com/unreferenced",
            "updatedAt": "2026-07-21",
        },
    ]
    kwargs = {
        "question": "What changed?",
        "verified_plan": {"headline": "Stable report", "asOf": _AS_OF},
        "sections": ["## Finding\n\nA source-backed finding [S1][S2][S3][S4][S5][S6][S10]."],
        "sources": sources,
    }

    first = research_module._assemble_architect_sections(**kwargs)
    second = research_module._assemble_architect_sections(**kwargs)

    assert first == second
    assert "## Evidence currency and applicability" in first
    assert (
        "[S1] First Source: applicable version v1.4; evidence status version-bounded; "
        "the version bounds the evidence scope; broader or current applicability is left to the independent Reviewer."
    ) in first
    assert "[S2] Second Source: updated 2026-07-20; evidence status source-reported document dates are shown explicitly" in first
    assert "[S3] Older Foundation: published 2018-07-20; evidence status source-reported document dates are shown explicitly" in first
    assert "[S4] Undated Experience (secondary/experience source): evidence status undated" in first
    assert "[S5] Epoch Metadata: updated 2023-09-30" in first
    assert "[S6] Retrieval Alias: retrieved 2026-07-29T12:00:00Z; evidence status undated; used only for non-temporal facts" in first
    assert "Retrieval Alias - https://example.com/retrieval-alias (2026-07-29)" not in first
    assert "[S10] Tenth Source: published 2026-07-10; evidence status source-reported document dates are shown explicitly" in first
    assert "Unreferenced Source" not in first
    assert "No fixed document-age cutoff is applied" in first
    assert "Retrieval time records when the evidence was fetched" in first
    assert "broad window" not in first.lower()
    assert first.index("- [S1] First Source - https://example.com/one (version v1.4)") < first.index(
        "- [S2] Second Source - https://example.com/two (updated 2026-07-20)"
    )
    assert first.index("- [S2] Second Source - https://example.com/two (updated 2026-07-20)") < first.index(
        "- [S10] Tenth Source - https://example.com/ten (published 2026-07-10)"
    )
    assert first.count("## Sources") == 1


def test_section_assembly_restores_only_missing_verified_audience_inferences() -> None:
    claims = [
        {
            "claimId": "C1",
            "claim": "个人方案包含本地交互入口。",
            "supportingSources": [{"citationKey": "S1", "title": "个人方案文档"}],
            "evidenceExcerpt": "个人方案包含本地交互入口。",
        },
        {
            "claimId": "C2",
            "claim": "个人方案允许按需复核结果。",
            "supportingSources": [{"citationKey": "S2", "title": "复核文档"}],
            "evidenceExcerpt": "个人方案允许按需复核结果。",
        },
    ]
    inference_text = "个人开发者可把本地交互与按需复核结合为默认工作方式。"
    plan = {
        "headline": "选择建议",
        "claimTable": claims,
        "compositeInferences": [
            {
                "inferenceId": "inference-individual",
                "audience": "个人开发者",
                "inference": inference_text,
                "premiseClaimIds": ["C1", "C2"],
            }
        ],
    }
    sources = [
        {"citationKey": "S1", "title": "个人方案文档", "url": "https://one.example"},
        {"citationKey": "S2", "title": "复核文档", "url": "https://two.example"},
    ]
    fact_section = "## 已验证事实\n\n个人方案有两个已验证前提 [S1][S2]。"

    restored = research_module._assemble_architect_sections(
        question="请按使用者类型给出选型建议。",
        verified_plan=plan,
        sections=[fact_section],
        sources=sources,
    )

    assert "## 按使用者类型的可执行选型建议" in restored
    assert "### 个人开发者" in restored
    assert f"**本报告的综合判断：** {inference_text} [S1][S2]" in restored

    already_rendered = (
        f"## 个人开发者\n\n**本报告的综合判断：** {inference_text} [S1][S2]"
    )
    deduplicated = research_module._assemble_architect_sections(
        question="请按使用者类型给出选型建议。",
        verified_plan=plan,
        sections=[already_rendered, fact_section],
        sources=sources,
    )

    assert deduplicated.count(inference_text) == 1
    assert "## 按使用者类型的可执行选型建议" not in deduplicated


def test_staged_segmented_writer_is_parallel_locally_retried_ordered_and_whole_reviewed(
    monkeypatch,
) -> None:
    monkeypatch.setattr(research_module, "_assemble_architect_claim_report", lambda **_kwargs: "")
    sources = _sources()
    invocation = _StagedInvocation(_accepted_plan(sources))
    candidate = _Candidate(max_tokens=4096, model_ref="fixture::segmented")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(candidate, "fixture::segmented", "summary")],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["reviewDecision"] == "accept"
    assert result["_writerMode"] == "segmented"
    assert result["_writerSectionCount"] == 4
    assert result["_writerRevisionCount"] == 1
    assert invocation.structure_disable_thinking == []
    assert {stage for stage, _disabled in invocation.stage_disable_thinking} == {
        "section",
        "review",
    }
    assert all(disabled is True for _stage, disabled in invocation.stage_disable_thinking)
    assert invocation.max_active_sections >= 2
    assert invocation.section_attempts == {
        "section_1": 1,
        "section_2": 2,
        "section_3": 1,
        "section_4": 1,
    }
    assert invocation.section_max_tokens == [
        research_module._RESEARCH_ARCHITECT_SECTION_MAX_TOKENS
    ] * 5
    assert invocation.section_source_payloads
    assert all(
        source.get("verifiedClaims")
        and "text" not in source
        and "evidenceCandidates" not in source
        for payload in invocation.section_source_payloads
        for source in payload
    )
    assert all(
        claim.get("exactEvidenceExcerpt")
        for payload in invocation.section_source_payloads
        for source in payload
        for claim in source["verifiedClaims"]
    )
    assert invocation.completion_order.index("section_2") < invocation.completion_order.index("section_1")
    assert result["_contextPreparations"]
    assert all(
        preparation["preparedMessageChars"] > 0
        and preparation["materials"]
        and all(material["sha256"] for material in preparation["materials"])
        for preparation in result["_contextPreparations"]
    )
    section_diagnostics = result["_writerSectionDiagnostics"]
    assert section_diagnostics
    section_two_digests = {
        item["claimEvidenceDigest"]
        for item in section_diagnostics
        if item["sectionId"] == "section_2"
    }
    assert len(section_two_digests) == 1
    assert all(
        item["rawResponseChars"] >= item["parsedSectionChars"]
        and item["postRestoreChars"] >= item["postUnsupportedDropChars"]
        for item in section_diagnostics
        if item["status"] in {"accepted", "rejected"}
    )

    answer = result["researchResult"]
    assert answer.index("## Runtime section 1") < answer.index("## Runtime section 2")
    assert answer.index("## Runtime section 2") < answer.index("## Runtime section 3")
    assert answer.index("## Runtime section 3") < answer.index("## Runtime section 4")
    assert answer.index("- [S1] Verified Architecture Source") < answer.index(
        "- [S8] Verified Decision Source"
    )
    assert research_module._RESEARCH_ARCHITECT_SECTION_COMPLETE_MARKER_PREFIX not in answer

    assert len(invocation.review_payloads) == 2
    assert all(payload["answer"] == answer for payload in invocation.review_payloads)
    review = result["_independentReview"]
    assert review["consensusAccepted"] is True
    assert review["consensusReviewCount"] == 2
    assert {item["reviewMode"] for item in review["consensusReviews"]} == {
        "semantic",
        "adversarial",
    }
    expected_binding = research_module.build_research_review_binding(
        {
            "question": _QUESTION,
            "freshness": "current",
            "reviewDecision": "accept",
            "answer": answer,
            "sourceUrls": sources,
            "claimTable": result["claimTable"],
            "asOf": result["asOf"],
        },
        reviewer_model_id="fixture::segmented",
        reviewed_at=review["reviewedAt"],
    )
    for key, value in expected_binding.items():
        assert review[key] == value


def test_segmented_writer_resigns_truncated_prompt_bodies_and_keeps_plan_payload_lean(
    monkeypatch,
) -> None:
    sources = _sources()
    for index, source in enumerate(sources, start=1):
        body = source["text"] + (
            f" Verified long-form evidence boundary {index}. " * 900
        )
        assert len(body) > research_module._RESEARCH_ARCHITECT_SOURCE_TEXT_CHARS
        source["text"] = body
        source["contentChars"] = len(body)
        source["originalContentChars"] = len(body)
        source["readEvidence"] = {
            "verified": True,
            "contentChars": len(body),
            "contentSha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "retrievedAt": source["retrievedAt"],
        }

    class CapturingInvocation(_StagedInvocation):
        def __init__(self, plan: dict) -> None:
            super().__init__(plan)
            self.canonical_structure_material = ""

        def __call__(self, candidate, messages, **kwargs):  # noqa: ANN001
            material = _background_material(messages, "Immutable canonical claim plan")
            if material:
                self.canonical_structure_material = material
            return super().__call__(candidate, messages, **kwargs)

    invocation = CapturingInvocation(_accepted_plan(sources))
    candidate = _Candidate(max_tokens=4096, model_ref="minimax-cn::MiniMax-M3")
    monkeypatch.setattr(
        "core.context_window_guard.llm_factory.get_model_metadata",
        lambda model_ref: {
            "is_found": model_ref == "minimax-cn::MiniMax-M3",
            "global_context_window": (
                1_000_000 if model_ref == "minimax-cn::MiniMax-M3" else None
            ),
            "model_record": {"type": "TEXT"},
            "capability_class": "chat",
        },
    )
    monkeypatch.setattr(
        "core.context_window_guard.storage.get_role_model_id",
        lambda _role: "",
    )
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(candidate, "minimax-cn::MiniMax-M3", "summary")],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION + " What implementation do you recommend?",
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result.get("reviewDecision") == "accept", json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    )
    structure_material = json.loads(invocation.canonical_structure_material)
    assert len(structure_material["claims"]) == 8
    assert all("evidenceExcerpt" not in claim for claim in structure_material["claims"])
    assert len(invocation.canonical_structure_material) < 20_000
    structure_preparation = next(
        item
        for item in result["_contextPreparations"]
        if item["node"] == "web_research_structure_projection"
    )
    assert structure_preparation["effectiveContextWindowTokens"] == 1_000_000
    assert structure_preparation["compactionApplied"] is False


def test_segmented_writer_preserves_accepted_sections_when_whole_answer_gate_rejects(
    monkeypatch,
) -> None:
    sources = _sources()
    invocation = _StagedInvocation(_accepted_plan(sources))
    candidate = _Candidate(max_tokens=4096, model_ref="fixture::whole-gate-reject")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(candidate, "fixture::whole-gate-reject", "summary")],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )
    monkeypatch.setattr(
        research_module,
        "research_acceptance_issues",
        lambda _payload: ["forced_whole_answer_gate_rejection"],
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["_agentError"] == "architect_answer_unavailable"
    segmented_attempts = [
        attempt
        for attempt in result["_writerAttempts"]
        if attempt["mode"] == "segmented"
    ]
    assert segmented_attempts
    assert segmented_attempts[0]["acceptedSectionCount"] == 4
    assert segmented_attempts[0]["newAcceptedSectionCount"] == 4
    assert "researchResult" not in result


def test_segmented_writer_retries_a_malformed_review_without_discarding_the_answer(
    monkeypatch,
) -> None:
    sources = _sources()
    invocation = _StagedInvocation(
        _accepted_plan(sources),
        invalid_schema_review_numbers={1},
    )
    candidate = _Candidate(max_tokens=4096, model_ref="fixture::review-schema-retry")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(candidate, "fixture::review-schema-retry", "summary")],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["reviewDecision"] == "accept", result
    assert result["_writerMode"] == "segmented"
    assert result["_writerRuntimeFallback"] is False
    assert len(invocation.review_payloads) == 3
    assert invocation.review_payloads[0]["answer"] == result["researchResult"]
    assert all(payload["answer"] == result["researchResult"] for payload in invocation.review_payloads)
    assert "空值写 []，绝不能写 null" in invocation.review_prompts[1]
    assert any(
        "independent_review_invalid_schema" in issue
        for issue in result["_modelFallbackAttempts"]
    )


def test_empty_structure_projection_keeps_canonical_plan_and_writer_budget(monkeypatch) -> None:
    sources = _sources()

    class EmptyStructureInvocation(_StagedInvocation):
        def __init__(self, plan: dict) -> None:
            super().__init__(plan)
            self.primary_structure_calls = 0
            self.primary_disable_thinking: list[bool] = []

        def __call__(self, candidate, messages, *, seconds, max_tokens, disable_thinking=False):  # noqa: ANN001
            structure_material = _background_material(
                messages,
                "Immutable canonical claim plan",
            )
            model_ref = str((candidate[0]._meta or {}).get("model_ref") or "")
            if structure_material and model_ref == "fixture::bound-minimax":
                self.primary_structure_calls += 1
                self.primary_disable_thinking.append(disable_thinking)
                return AIMessage(
                    content="",
                    additional_kwargs={"reasoning_details": [{"type": "thinking", "text": "bounded"}]},
                    response_metadata={
                        "finish_reason": "length",
                        "token_usage": {
                            "completion_tokens": max_tokens,
                            "completion_tokens_details": {"reasoning_tokens": max_tokens},
                        },
                    },
                )
            return super().__call__(
                candidate,
                messages,
                seconds=seconds,
                max_tokens=max_tokens,
                disable_thinking=disable_thinking,
            )

    invocation = EmptyStructureInvocation(_accepted_plan(sources))
    primary = _Candidate(
        max_tokens=4096,
        model_ref="fixture::bound-minimax",
        supports_no_think=True,
    )
    fallback = _Candidate(max_tokens=4096, model_ref="fixture::summary-fallback")
    primary._meta["research_candidate_origin"] = "agent_binding"
    fallback._meta["research_candidate_origin"] = "role_fallback:summary"
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [
            (primary, "fixture::bound-minimax", "web-research-architect"),
            (fallback, "fixture::summary-fallback", "summary"),
        ],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION + " What implementation do you recommend?",
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["reviewDecision"] == "accept", result
    assert invocation.primary_structure_calls == 1
    assert invocation.primary_disable_thinking == [True]
    assert result["_canonicalClaimPlan"]["mode"] == "runtime_canonical"
    assert result["_structureAttempt"]["status"] == "accepted_with_drops"
    assert result["_structureAttempt"]["requestedMaxTokens"] == (
        research_module._RESEARCH_ARCHITECT_STRUCTURE_MAX_TOKENS
    )
    assert result["_modelId"] == "fixture::bound-minimax"


def test_staged_segmented_writer_does_not_review_or_deliver_partial_answer_when_a_section_fails_twice(
    monkeypatch,
) -> None:
    monkeypatch.setattr(research_module, "_assemble_architect_claim_report", lambda **_kwargs: "")
    sources = _sources()
    invocation = _StagedInvocation(
        _accepted_plan(sources),
        permanently_failed_section="section_3",
    )
    candidate = _Candidate(max_tokens=4096, model_ref="fixture::partial-failure")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(candidate, "fixture::partial-failure", "summary")],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["_agentError"] == "architect_answer_unavailable"
    assert "researchResult" not in result
    assert "answer" not in result
    assert invocation.section_attempts["section_3"] == 4
    assert [
        attempt["mode"]
        for attempt in result["_writerAttempts"]
        if attempt["mode"].startswith("segmented")
    ] == [
        "segmented",
        "segmented_tail_recovery",
    ]
    assert result["_writerAttempts"][-1]["accepted"] is False
    assert invocation.review_payloads == []


def test_segmented_writer_hands_only_failed_sections_to_the_next_candidate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(research_module, "_assemble_architect_claim_report", lambda **_kwargs: "")
    sources = _sources()
    primary = _Candidate(max_tokens=4096, model_ref="fixture::handoff-primary")
    fallback = _Candidate(max_tokens=4096, model_ref="fixture::handoff-fallback")
    invocation = _StagedInvocation(
        _accepted_plan(sources),
        permanently_failed_section_by_model={
            "fixture::handoff-primary": "section_3",
        },
    )
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [
            (primary, "fixture::handoff-primary", "summary"),
            (fallback, "fixture::handoff-fallback", "research"),
        ],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["reviewDecision"] == "accept"
    assert result["_writerMode"] == "segmented_candidate_handoff"
    assert result["_writerSectionCount"] == 4
    fallback_section_ids = {
        section_id
        for (model_ref, section_id), count in invocation.section_attempts_by_model.items()
        if model_ref == "fixture::handoff-fallback" and count
    }
    assert fallback_section_ids == {"section_3"}
    segmented_attempts = [
        attempt
        for attempt in result["_writerAttempts"]
        if attempt["mode"].startswith("segmented")
    ]
    assert [attempt["acceptedSectionCount"] for attempt in segmented_attempts] == [3, 4]
    assert [attempt["newAcceptedSectionCount"] for attempt in segmented_attempts] == [3, 1]
    assert len(invocation.review_payloads) == 2


def test_reviewer_guided_revision_also_hands_only_failed_sections_to_next_candidate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        research_module,
        "_assemble_architect_claim_report",
        lambda **_kwargs: "",
    )
    sources = _sources()
    primary = _Candidate(max_tokens=4096, model_ref="fixture::revision-primary")
    fallback = _Candidate(max_tokens=4096, model_ref="fixture::revision-fallback")
    invocation = _StagedInvocation(
        _accepted_plan(sources),
        reviewer_revision_failed_section_by_model={
            "fixture::revision-primary": "section_2",
        },
        reject_first_review_without_evidence_gap=True,
    )
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [
            (primary, "fixture::revision-primary", "summary"),
            (fallback, "fixture::revision-fallback", "research"),
        ],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["reviewDecision"] == "accept"
    assert result["_writerMode"] == "segmented_reviewer_revised_handoff"
    revision_fallback_sections = {
        section_id
        for (model_ref, section_id), count in invocation.section_attempts_by_model.items()
        if model_ref == "fixture::revision-fallback" and count
    }
    assert revision_fallback_sections == {"section_2"}
    assert any(
        attempt["mode"] == "segmented_reviewer_revision"
        and attempt["newAcceptedSectionCount"] == 1
        for attempt in result["_writerAttempts"]
    )
    assert len(invocation.review_payloads) >= 3


def test_tail_recovery_retries_only_the_last_missing_section(monkeypatch) -> None:
    monkeypatch.setattr(research_module, "_assemble_architect_claim_report", lambda **_kwargs: "")
    sources = _sources()

    class TailRecoveryInvocation(_StagedInvocation):
        def __init__(self, plan: dict) -> None:
            super().__init__(plan)
            self.tail_unlocked = False

        def __call__(self, candidate, messages, *, seconds, max_tokens, disable_thinking=False):  # noqa: ANN001
            section_contract = _background_material(messages, "Section contract")
            if section_contract:
                task = json.loads(section_contract)
                section_id = str(task["sectionId"])
                model_ref = str((candidate[0]._meta or {}).get("model_ref") or "")
                must_fail = bool(
                    section_id.startswith("section_4")
                    and (
                        model_ref == "fixture::tail-fallback"
                        or (model_ref == "fixture::tail-primary" and not self.tail_unlocked)
                    )
                )
                if must_fail:
                    with self.lock:
                        self.section_attempts[section_id] += 1
                        self.section_attempts_by_model[(model_ref, section_id)] += 1
                    if model_ref == "fixture::tail-fallback":
                        self.tail_unlocked = True
                    return AIMessage(content="incomplete section without its marker")
            return super().__call__(
                candidate,
                messages,
                seconds=seconds,
                max_tokens=max_tokens,
                disable_thinking=disable_thinking,
            )

    invocation = TailRecoveryInvocation(_accepted_plan(sources))
    primary = _Candidate(max_tokens=4096, model_ref="fixture::tail-primary")
    fallback = _Candidate(max_tokens=4096, model_ref="fixture::tail-fallback")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [
            (primary, "fixture::tail-primary", "web-research-architect"),
            (fallback, "fixture::tail-fallback", "summary"),
        ],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["reviewDecision"] == "accept", result
    assert result["_writerMode"] == "segmented_tail_recovered"
    modes = [
        attempt["mode"]
        for attempt in result["_writerAttempts"]
        if attempt["mode"].startswith("segmented")
    ]
    assert modes == ["segmented", "segmented", "segmented_tail_recovery"]
    assert result["_writerAttempts"][-1]["missingSectionSequencesBefore"] == [4]
    assert result["_writerAttempts"][-1]["newAcceptedSectionCount"] == 1
    fallback_sections = {
        section_id
        for (model_ref, section_id), count in invocation.section_attempts_by_model.items()
        if model_ref == "fixture::tail-fallback" and count
    }
    assert "section_4" in fallback_sections
    assert all(section_id.startswith("section_4") for section_id in fallback_sections)


def test_high_output_candidate_revises_assembled_answer_before_segment_regeneration(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        research_module,
        "_assemble_architect_claim_report",
        lambda **_kwargs: "",
    )
    sources = _sources()

    class FullRevisionInvocation(_StagedInvocation):
        def __init__(self, plan: dict) -> None:
            super().__init__(plan, reject_first_review_without_evidence_gap=True)
            self.full_revision_calls = 0

        def __call__(self, candidate, messages, *, seconds, max_tokens, disable_thinking=False):  # noqa: ANN001
            instruction = next(
                (
                    str(getattr(message, "content", ""))
                    for message in messages
                    if "PREVIOUS_DRAFT:" in str(getattr(message, "content", ""))
                ),
                "",
            )
            if instruction and not _background_material(messages, "Section contract"):
                self.full_revision_calls += 1
                previous = instruction.split("PREVIOUS_DRAFT:\n", 1)[-1]
                previous = previous.split("\n[BACKGROUND MATERIAL:", 1)[0].strip()
                return AIMessage(
                    content=(
                        previous
                        + "\n\n"
                        + research_module._RESEARCH_ARCHITECT_ANSWER_COMPLETE_MARKER
                    )
                )
            return super().__call__(
                candidate,
                messages,
                seconds=seconds,
                max_tokens=max_tokens,
                disable_thinking=disable_thinking,
            )

    invocation = FullRevisionInvocation(_accepted_plan(sources))
    segmented_candidate = _Candidate(max_tokens=4096, model_ref="fixture::segmented")
    full_revision_candidate = _Candidate(max_tokens=8192, model_ref="fixture::full-revision")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [
            (segmented_candidate, "fixture::segmented", "summary"),
            (full_revision_candidate, "fixture::full-revision", "summary"),
        ],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["reviewDecision"] == "accept", result
    assert invocation.full_revision_calls == 1, result
    assert result["_writerMode"] == "single_reviewer_revised"
    assert any(
        attempt["mode"] == "single_reviewer_revision" and attempt["accepted"] is True
        for attempt in result["_writerAttempts"]
    )


def test_segmented_writer_splits_one_failed_multi_claim_section_within_six_section_cap(
    monkeypatch,
) -> None:
    monkeypatch.setattr(research_module, "_assemble_architect_claim_report", lambda **_kwargs: "")
    sources = _sources()
    for source in sources:
        source["tier"] = "primary"
    invocation = _StagedInvocation(
        _accepted_plan(sources),
        split_after_retries_section="section_3",
    )
    candidate = _Candidate(max_tokens=4096, model_ref="fixture::adaptive-split")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(candidate, "fixture::adaptive-split", "summary")],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["reviewDecision"] == "accept"
    assert result["_writerMode"] == "segmented", {
        "writerAttempts": " | ".join(
            f"{attempt.get('mode')}:{attempt.get('acceptedSectionCount')}/"
            f"{attempt.get('targetSectionCount')}:{attempt.get('failureCodes')}"
            for attempt in result.get("_writerAttempts") or []
        ),
        "sectionDiagnostics": " | ".join(
            f"{diagnostic.get('sectionId')}#{diagnostic.get('attempt')}:"
            f"{diagnostic.get('status')}:claims={diagnostic.get('assignedClaimIds')}:"
            f"chars={diagnostic.get('effectiveChars')}/"
            f"{diagnostic.get('minimumAcceptableChars')}:"
            f"issues={diagnostic.get('issues')}"
            for diagnostic in result.get("_writerSectionDiagnostics") or []
        ),
        "fallbackAttempts": result.get("_modelFallbackAttempts"),
    }
    assert result["_writerSectionCount"] == 5
    assert result["_writerRevisionCount"] == 2
    assert invocation.section_attempts["section_3"] == 2
    assert invocation.section_attempts["section_3_a"] == 1
    assert invocation.section_attempts["section_3_b"] == 1
    assert len(invocation.review_payloads) == 2


def test_segmented_same_evidence_revision_stays_segmented_and_uses_the_same_reviewer(
    monkeypatch,
) -> None:
    sources = _sources()
    invocation = _StagedInvocation(
        _accepted_plan(sources),
        reject_first_review_without_evidence_gap=True,
    )
    candidate = _Candidate(max_tokens=4096, model_ref="fixture::segmented-revision")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(candidate, "fixture::segmented-revision", "summary")],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["reviewDecision"] == "accept"
    assert result["_writerMode"] == "segmented_reviewer_revised"
    assert result["_reviewerModelId"] == "fixture::segmented-revision"
    assert result["_writerRevisionCount"] == 2
    assert len(invocation.review_payloads) == 3
    assert invocation.section_attempts == {
        "section_1": 2,
        "section_2": 3,
        "section_3": 2,
        "section_4": 2,
    }
    assert set(invocation.section_max_tokens) == {
        research_module._RESEARCH_ARCHITECT_SECTION_MAX_TOKENS
    }
    assert invocation.review_payloads[0]["answer"] != invocation.review_payloads[1]["answer"]
    assert invocation.review_payloads[1]["answer"] == result["researchResult"]
    assert invocation.review_payloads[2]["answer"] == result["researchResult"]
    assert result["_independentReview"]["consensusReviewCount"] == 2
    assert invocation.reviewer_previous_section_flags
    assert all(invocation.reviewer_previous_section_flags)
    assert not any(
        attempt["mode"].startswith("deterministic_claim_report")
        for attempt in result["_writerAttempts"]
    )


def test_split_section_requires_synthesis_only_where_an_inference_survives() -> None:
    claims = [
        {
            "claimId": f"C{index}",
            "claim": f"Verified claim {index}",
            "supportingSources": [{"citationKey": f"S{index}"}],
        }
        for index in range(1, 5)
    ]
    task = {
        "sectionId": "section_1",
        "sequence": 1,
        "assignedClaims": claims,
        "compositeInferences": [
            {
                "inferenceId": "I1",
                "inference": "Claims one and two support a bounded synthesis.",
                "premiseClaimIds": ["C1", "C2"],
            }
        ],
        "requiresSynthesisConclusion": True,
        "targetMinChars": 1_600,
        "targetMaxChars": 2_800,
    }

    children = research_module._split_architect_section_task(task)

    assert children[0]["compositeInferences"] == task["compositeInferences"]
    assert children[0]["requiresSynthesisConclusion"] is True
    assert children[1]["compositeInferences"] == []
    assert children[1]["requiresSynthesisConclusion"] is False


def test_segmented_revision_precedes_deterministic_same_evidence_fallback(monkeypatch) -> None:
    sources = _sources()
    invocation = _StagedInvocation(
        _accepted_plan(sources),
        same_evidence_rejected_review_numbers={1, 2},
    )
    candidate = _Candidate(max_tokens=4096, model_ref="fixture::revision-then-fallback")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(candidate, "fixture::revision-then-fallback", "summary")],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION,
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result["reviewDecision"] == "accept", result
    assert result["_writerMode"] == "deterministic_claim_report_after_review"
    assert result["_writerRuntimeFallback"] is True
    attempt_modes = [attempt["mode"] for attempt in result["_writerAttempts"]]
    assert attempt_modes.index("segmented_reviewer_revision") < attempt_modes.index(
        "deterministic_claim_report_after_review"
    )
    assert len(invocation.review_payloads) == 4
    assert invocation.review_payloads[0]["answer"] != invocation.review_payloads[1]["answer"]
    assert invocation.review_payloads[2]["answer"] == result["researchResult"]
    assert invocation.review_payloads[3]["answer"] == result["researchResult"]


def test_structure_projection_cannot_create_a_normative_evidence_gap(monkeypatch) -> None:
    sources = _sources()
    retry_plan = {
        "reviewDecision": "retry",
        "reviewReasons": ["The sources do not state a final official best-practice recommendation."],
        "headline": "Evidence-backed pathlib CLI synthesis",
        "claimTable": [],
        "answerOutline": [],
        "compositeInferences": [],
        "conflictMatrix": [],
        "missingEvidence": [],
        "criticalMissingEvidence": [
            "No official excerpt contains prescriptive best-practice language."
        ],
        "recommendedNextQueries": ["No such official wording is present."],
        "assumptions": [],
        "temporalAssessment": {"asOf": _AS_OF, "status": "current"},
    }
    class UnsafeStructureInvocation(_StagedInvocation):
        def __call__(self, candidate, messages, **kwargs):  # noqa: ANN001
            if _background_material(messages, "Immutable canonical claim plan"):
                return AIMessage(content=json.dumps(retry_plan))
            return super().__call__(candidate, messages, **kwargs)

    invocation = UnsafeStructureInvocation(retry_plan)
    candidate = _Candidate(max_tokens=4096, model_ref="fixture::plan-reclassification")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(candidate, "fixture::plan-reclassification", "summary")],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION + " What implementation do you recommend?",
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result.get("reviewDecision") == "accept", {
        "agentError": result.get("_agentError"),
        "missingFacetIds": result.get("_missingFacetIds"),
        "fallbackAttempts": result.get("_modelFallbackAttempts"),
    }
    assert invocation.section_attempts
    assert result["_writerAttempts"][0]["mode"] == "segmented"
    assert result["_canonicalClaimPlan"]["mode"] == "runtime_canonical"
    assert result["_structureAttempt"]["claimMutationIgnored"] is True
    assert "structure_outline_invalid" in result["_structureAttempt"]["issues"]


def test_invalid_structure_projection_falls_back_without_model_plan_retry(monkeypatch) -> None:
    sources = _sources()
    retry_plan = {
        "reviewDecision": "retry",
        "reviewReasons": [
            "Available evidence lacks explicit normative best-practice statements from official docs",
            "No single source covers the unified recommendation",
        ],
        "headline": "Evidence-backed synthesis",
        "claimTable": [],
        "answerOutline": [],
        "compositeInferences": [],
        "conflictMatrix": [],
        "missingEvidence": [],
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
        "assumptions": [],
        "temporalAssessment": {"asOf": _AS_OF, "status": "current"},
    }
    class InvalidStructureInvocation(_StagedInvocation):
        def __call__(self, candidate, messages, **kwargs):  # noqa: ANN001
            if _background_material(messages, "Immutable canonical claim plan"):
                return AIMessage(content=json.dumps(retry_plan))
            return super().__call__(candidate, messages, **kwargs)

    invocation = InvalidStructureInvocation(retry_plan)
    candidate = _Candidate(max_tokens=4096, model_ref="fixture::empty-gap-retry")
    monkeypatch.setattr(
        research_module,
        "_create_web_research_architect_llm_candidates",
        lambda: [(candidate, "fixture::empty-gap-retry", "summary")],
    )
    monkeypatch.setattr(
        research_module,
        "_invoke_architect_candidate_with_deadline",
        invocation,
    )

    result = research_module._invoke_web_research_architect_staged(
        question=_QUESTION + " What implementation do you recommend?",
        sources=sources,
        freshness="current",
        timeout_seconds=90,
    )

    assert result.get("reviewDecision") == "accept", {
        "agentError": result.get("_agentError"),
        "missingFacetIds": result.get("_missingFacetIds"),
        "fallbackAttempts": result.get("_modelFallbackAttempts"),
    }
    assert invocation.section_attempts
    assert result["_canonicalClaimPlan"]["mode"] == "runtime_canonical"
    assert result["_structureAttempt"]["status"] == "accepted_with_drops"
    assert "structure_outline_invalid" in result["_structureAttempt"]["issues"]
