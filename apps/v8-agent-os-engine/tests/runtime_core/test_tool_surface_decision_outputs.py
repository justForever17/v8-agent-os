from __future__ import annotations

import hashlib
import json

from langchain_core.messages import ToolMessage

from core.tool_surface import MAX_RESEARCH_DELIVERY_SURFACE_CHARS, apply_tool_surface_budget
from core.tools.research_quality import build_research_review_binding
from runtimes.extensions.skills.loader import _read_skill_text_file


def _visible(tool_name: str, payload: dict, *, budget: int = 2500) -> str:
    message = ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        name=tool_name,
        tool_call_id=f"call-{tool_name}",
    )
    return str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": budget},
            tool_name=tool_name,
        ).content
    )


def _assert_not_json_wrapper(text: str) -> None:
    assert not text.lstrip().startswith("{")
    assert "_v8ToolSurface" not in text
    assert '"ok"' not in text
    assert "recommendedNextAction" not in text


def _bind_accepted_research_consensus(payload: dict) -> dict:
    reviews = []
    for index, mode in enumerate(("semantic", "adversarial"), start=1):
        reviewer_model_id = f"surface-test-reviewer-{mode}"
        reviewed_at = f"2026-07-28T08:00:0{index}Z"
        review = {
            "reviewDecision": "accept",
            "reviewReasons": [
                f"The {mode} review confirms question coverage, source entailment, and temporal adequacy."
            ],
            "questionCoverage": True,
            "claimEntailment": True,
            "freshnessAdequacy": True,
            "unsupportedClaims": [],
            "criticalMissingEvidence": [],
            "recommendedNextQueries": [],
            "reviewMode": mode,
            "reviewCallId": f"surface-test-review-call-{mode}",
            "reviewInvocationId": f"surface-test-review-invocation-{mode}",
        }
        review.update(
            build_research_review_binding(
                payload,
                reviewer_model_id=reviewer_model_id,
                reviewed_at=reviewed_at,
            )
        )
        reviews.append(review)

    consensus = {
        **reviews[0],
        "consensusAccepted": True,
        "consensusReviewCount": len(reviews),
        "consensusReviewerModelIds": [review["reviewerModelId"] for review in reviews],
        "consensusReviews": reviews,
    }
    payload["independentReview"] = consensus
    payload["researchAnswerPack"]["independentReview"] = consensus
    payload["finalExperiencePack"]["independentReview"] = consensus
    return consensus


def _accepted_research_surface_payload() -> dict:
    sources = [
        {
            "sourceId": f"source-{index}",
            "citationKey": f"[S{index}]",
            "title": f"Selected source {index}",
            "url": f"https://source{index}.example.com/report",
            "selectedForEvidence": True,
            "retrievedAt": "2026-07-28T08:00:00Z",
            "updatedAt": "2026-07-27",
            "contentChars": 6000,
            "readEvidence": {
                "verified": True,
                "contentChars": 6000,
                "contentSha256": "d" * 64,
                "retrievedAt": "2026-07-28T08:00:00Z",
            },
        }
        for index in range(1, 9)
    ]
    sources[-1].pop("url")
    sources[-1]["host"] = "source-id-8.example.com"
    claim_topics = (
        "Scope definition",
        "Authentication behavior",
        "Retry semantics",
        "Error handling",
        "Service limits",
        "Freshness changes",
        "Operational risks",
        "Deployment verification",
    )
    claims = []
    for index in range(1, 9):
        excerpt = f"Selected source {index} states an explicit API fact, applicability condition, and evidence boundary for deployment."
        claims.append({
            "claim": f"{claim_topics[index - 1]} is supported by a selected source with explicit applicability boundaries.",
            "supportingSources": [{"sourceId": f"source-{index}"}],
            "evidenceExcerpt": excerpt,
            "evidenceExcerptSha256": hashlib.sha256(excerpt.lower().encode("utf-8")).hexdigest(),
            "evidenceVerified": True,
        })
    independent_review: dict = {}
    citations = " ".join(source["citationKey"] for source in sources)
    subjects = (
        ("scope", "The scope section separates supported endpoints from adjacent product features and records which operations remain outside the reviewed contract"),
        ("authentication", "The authentication section distinguishes credential issuance, header transport, rotation, revocation, and the permissions checked at each request boundary"),
        ("retries", "The retry section identifies idempotent operations, backoff signals, duplicate side effects, retry budgets, and the point where an error must surface to the caller"),
        ("errors", "The error section maps transport failures, validation responses, provider faults, and partial results to concrete handling and observability requirements"),
        ("limits", "The limits section records request size, concurrency, rate windows, pagination, retention, and the conditions under which published quotas may change"),
        ("verification", "The verification section defines contract tests, negative cases, deployment probes, audit evidence, and refresh triggers for future API revisions"),
    )
    aspects = (
        ("source authority", "Official reference text is treated as contract evidence while examples and community reports are used only to explain implementation consequences"),
        ("time boundary", "Version labels, publication dates, retrieval timestamps, and superseding notices establish exactly when the recommendation is valid"),
        ("applicability", "Runtime, account tier, region, request shape, and client-library assumptions are stated so the reader can decide whether the finding applies"),
        ("counterevidence", "Conflicting statements and failed cases are compared against the primary source, with an explicit condition that would overturn the conclusion"),
        ("user impact", "The operational consequence is translated into latency, reliability, security, cost, and maintenance decisions instead of remaining an abstract fact"),
        ("failure mode", "A reproducible failure signal, visible status, recovery action, and follow-up check show how the recommendation behaves when its assumptions break"),
    )
    paragraphs = [
        (
            f"{subject.title()} through {aspect}: {subject_detail}, and {aspect_detail}; together these facts identify the decision, its boundary, and the exact verification needed before deployment."
        )
        for subject, subject_detail in subjects
        for aspect, aspect_detail in aspects
    ]
    paragraphs = [
        f"{paragraph.rstrip('.')} {sources[index % len(sources)]['citationKey']}."
        for index, paragraph in enumerate(paragraphs)
    ]
    answer = "ACCEPTED ANSWER START " + citations + "\n\n" + "\n\n".join(paragraphs)
    assert len("".join(answer.split())) > 5000
    payload = {
        "ok": True,
        "kind": "research_evidence_bundle",
        "question": "如何接入官方 API?",
        "freshness": "current",
        "asOf": "2026-07-28",
        "reviewDecision": "accept",
        "independentReview": independent_review,
        "researchAnswerPack": {
            "answer": answer,
            "sources": sources,
            "claimTable": claims,
            "reviewDecision": "accept",
            "independentReview": independent_review,
            "asOf": "2026-07-28",
            "limitations": ["需要结合当前项目中的 SDK 版本继续核对。"],
        },
        "finalExperiencePack": {
            "question": "如何接入官方 API?",
            "reviewDecision": "accept",
            "independentReview": independent_review,
            "asOf": "2026-07-28",
        },
        "sourceMatrix": [{"snippet": "provider-only diagnostic"}],
        "loopReport": {"rounds": [{"query": "raw process log"}]},
        "architectPrompt": "diagnostic prompt must not leak",
    }
    _bind_accepted_research_consensus(payload)
    return payload


def test_runtime_broker_default_is_decision_summary():
    visible = _visible(
        "runtime_broker",
        {
            "mode": "list",
            "ok": True,
            "availableGroups": [
                {"group": "research.core", "kind": "research", "label": "Research core"},
                {"group": "creative_media.core", "kind": "creative_media", "label": "Creative Media core"},
            ],
            "recommendedNextAction": "Grant one needed group.",
            "omitted": {"toolNames": 48},
        },
    )

    assert visible.startswith("Runtime route menu")
    assert "research.core" in visible
    assert "Runtime route menu" in visible
    assert "Run-scoped tool groups (not execution routes)" in visible
    _assert_not_json_wrapper(visible)


def test_research_plan_hides_shard_defaults():
    visible = _visible(
        "research_broker",
        {
            "ok": True,
            "mode": "plan",
            "kind": "research_plan",
            "question": "Compare official docs",
            "researchIntent": "source quality",
            "experienceFirstPolicy": {"summary": "Search reusable packs first."},
            "shardDefaults": {"allowedTools": ["web_search", "web_read"], "deadlineMs": 45000},
            "limits": {"effectiveMaxShards": 2, "effectiveMaxRounds": 1},
            "shards": [
                {"shardId": "shard_1", "kind": "baseline", "query": "Compare official docs", "reason": "baseline"},
                {"shardId": "shard_2", "kind": "official_docs", "query": "Compare official docs API", "reason": "official"},
            ],
        },
    )

    assert "Research plan" in visible
    assert "Shard briefs" in visible
    assert "allowedTools" not in visible
    assert "deadlineMs" not in visible
    _assert_not_json_wrapper(visible)


def test_research_result_pack_exposes_accepted_answer_sources_and_as_of_not_process_logs():
    visible = _visible("research_broker", _accepted_research_surface_payload(), budget=12000)

    assert visible.startswith("Research answer\nACCEPTED ANSWER START")
    assert "Selected source 1" in visible
    assert "https://source1.example.com/report" in visible
    assert "Selected source 8" in visible
    assert "source-8" in visible
    assert "As of: 2026-07-28" in visible
    assert "Quality: high_quality; review=accept" in visible
    assert "Limitations:" in visible
    assert "sourceMatrix" not in visible
    assert "loopReport" not in visible
    assert "architectPrompt" not in visible
    assert "provider-only diagnostic" not in visible
    _assert_not_json_wrapper(visible)


def test_research_surface_validates_compact_delivery_against_runtime_ledger(monkeypatch):
    full_payload = _accepted_research_surface_payload()
    full_payload["evidenceBundleId"] = "research-surface-compact"
    compact_payload = {
        "ok": True,
        "kind": "research_evidence_bundle",
        "question": full_payload["question"],
        "evidenceBundleId": "research-surface-compact",
        "deliveryReady": True,
        "qualityTier": "high_quality",
        "reviewDecision": "accept",
        "researchAnswerPack": {
            "reviewDecision": "accept",
            "answer": full_payload["researchAnswerPack"]["answer"],
            "sources": [
                {
                    key: source.get(key)
                    for key in (
                        "sourceId",
                        "citationKey",
                        "title",
                        "url",
                        "host",
                        "selectedForEvidence",
                    )
                    if source.get(key) not in (None, "")
                }
                for source in full_payload["researchAnswerPack"]["sources"]
            ],
        },
    }
    from core.tools import research_ledger

    monkeypatch.setattr(
        research_ledger,
        "get_evidence_bundle",
        lambda _bundle_id: full_payload,
    )

    visible = _visible("research_broker", compact_payload, budget=12000)

    assert visible.startswith("Research answer\nACCEPTED ANSWER START")
    assert "Selected source 8" in visible
    assert "Quality: high_quality; review=accept" in visible


def test_research_accepted_surface_preserves_full_delivery_under_small_budget():
    visible = _visible("research_broker", _accepted_research_surface_payload(), budget=1200)

    assert visible.startswith("Research answer\nACCEPTED ANSWER START")
    assert len(visible) >= 5000
    assert "Selected source 8" in visible
    assert "decision surface truncated" not in visible


def test_research_delivery_surface_has_a_hard_context_bound():
    payload = _accepted_research_surface_payload()
    payload["researchAnswerPack"]["answer"] += "\n\n" + ("Additional bounded appendix content with cited evidence [S1].\n" * 600)
    _bind_accepted_research_consensus(payload)

    visible = _visible("research_broker", payload, budget=1200)

    assert len(visible) <= MAX_RESEARCH_DELIVERY_SURFACE_CHARS
    assert "research delivery surface truncated" in visible


def test_research_rejected_surface_shows_gaps_and_queries_without_draft_answer():
    payload = _accepted_research_surface_payload()
    payload["reviewDecision"] = "retry"
    payload["researchAnswerPack"]["reviewDecision"] = "retry"
    payload["researchAnswerPack"]["answer"] = "REJECTED DRAFT MUST STAY HIDDEN " + payload["researchAnswerPack"]["answer"]
    payload["researchAnswerPack"]["sources"] = payload["researchAnswerPack"]["sources"][:5]
    payload["researchAnswerPack"]["claimTable"] = payload["researchAnswerPack"]["claimTable"][:5]
    payload["criticalMissingEvidence"] = ["Official current-version evidence remains unresolved."]
    payload["recommendedNextQueries"] = ["site:source1.example.com current API version"]
    payload["detailTool"] = "research_broker(mode='get_evidence', evidenceBundleId='rejected')"

    visible = _visible("research_broker", payload, budget=5000)

    assert visible.startswith("Research evidence incomplete")
    assert "Review: retry; quality=insufficient" in visible
    assert "Official current-version evidence remains unresolved." in visible
    assert "Fewer than 8 selected sources support a normal Research delivery." in visible
    assert "Suggested follow-up searches:" in visible
    assert "site:source1.example.com current API version" in visible
    assert "REJECTED DRAFT MUST STAY HIDDEN" not in visible
    assert "get_evidence" not in visible
    assert "target_source_count_not_met" not in visible
    assert "provider-only diagnostic" not in visible
    _assert_not_json_wrapper(visible)


def test_research_surface_requires_explicit_boolean_success_before_showing_answer():
    payload = _accepted_research_surface_payload()
    payload["ok"] = 0
    payload["detailTool"] = "research_broker(mode='get_evidence', evidenceBundleId='not-successful')"

    visible = _visible("research_broker", payload, budget=5000)

    assert visible.startswith("Research evidence incomplete")
    assert "Research execution did not report a successful evidence bundle." in visible
    assert "ACCEPTED ANSWER START" not in visible
    assert "get_evidence" not in visible


def test_computer_use_route_hides_matches_and_manual_controls():
    visible = _visible(
        "computer_use_resolve_execution_route",
        {
            "ok": True,
            "recommendedMode": "hybrid_mode",
            "recommendedTool": "computer_use_execute_task",
            "recommendedAction": "run_hybrid_with_computer_use",
            "recommendedMatch": {
                "id": "system.github.star_repository",
                "name": "GitHub Star Repository",
                "score": 0.41,
                "confidence": 0.81,
            },
            "matches": [{"id": "too much"}],
            "manualControls": {"humanCanApprove": True},
        },
    )

    assert "Computer Use route" in visible
    assert "system.github.star_repository" in visible
    assert "computer_use_execute_task" in visible
    assert "manualControls" not in visible
    assert '"matches"' not in visible
    _assert_not_json_wrapper(visible)


def test_creative_media_jobs_facade_is_short_queue_surface():
    visible = _visible(
        "creative_media_jobs",
        {
            "ok": True,
            "facade": "jobs",
            "action": "list",
            "status": "ready",
            "summary": "17 jobs: failed=8, succeeded=9",
            "refs": ["cm_15ff203da04c46b0a39506b5a9ade2c2"],
            "detailRef": "toolobs://creative-internal",
        },
    )

    assert "Creative Media jobs.list" in visible
    assert "Status: ready" in visible
    assert "failed=8, succeeded=9" in visible
    assert "cm_15ff203da04c46b0a39506b5a9ade2c2" in visible
    assert "providerResponse" not in visible
    _assert_not_json_wrapper(visible)


def test_memory_broker_route_exposes_selected_evidence_not_ranking_matrix():
    visible = _visible(
        "memory_broker",
        {
            "ok": True,
            "mode": "route",
            "query": "之前调研过三月七吗",
            "selectedDomains": ["research_experience", "memory_core"],
            "summary": "Routed query to research experience and memory core.",
            "evidencePacks": [
                {
                    "sourceDomain": "research_experience",
                    "whySelected": "topic fingerprint and source-backed answer match the query.",
                    "confidence": "high",
                    "selectedEvidence": [
                        {
                            "id": "rxp_sanyueqi",
                            "title": "三月七角色调研",
                            "answer": "三月七是《崩坏：星穹铁道》的列车组成员，调研应以官方角色设定和剧情文本为主。",
                            "claimDigest": [
                                "三月七的表达风格偏活泼、直接，并常用拍照和记录作为角色行为线索。"
                            ],
                            "sources": [
                                {"title": "官方角色资料", "url": "https://sr.mihoyo.com/role/march7th"}
                            ],
                            "score": {"confidence": "high", "authorityScore": 78},
                            "rankingFeatures": {"internal": "do-not-show"},
                        }
                    ],
                    "rejectedEvidence": [
                        {"id": "rxp_noise", "reason": "low_quality_pack; source text unreadable"}
                    ],
                    "recommendedNextAction": "Reuse selected evidence only if the current task asks about the same character.",
                }
            ],
            "rankingMatrix": [{"candidate": "raw"}],
            "graphTraversal": {"internal": "raw"},
        },
        budget=4200,
    )

    assert "Memory broker: route" in visible
    assert "Evidence packs:" in visible
    assert "research_experience" in visible
    assert "三月七是《崩坏：星穹铁道》" in visible
    assert "三月七的表达风格偏活泼" in visible
    assert "https://sr.mihoyo.com/role/march7th" in visible
    assert "low_quality_pack" in visible
    assert "rankingMatrix" not in visible
    assert "graphTraversal" not in visible
    assert "rankingFeatures" not in visible
    _assert_not_json_wrapper(visible)


def test_computer_use_list_apps_limits_aliases_and_windows():
    visible = _visible(
        "computer_use_list_apps",
        {
            "ok": True,
            "count": 20,
            "apps": [
                {
                    "appId": "vscode",
                    "displayName": "Visual Studio Code",
                    "isRunning": True,
                    "launchable": True,
                    "topWindowTitle": "Codex",
                    "aliases": ["VS Code", "vscode", "visual studio code"],
                    "windows": [{"handle": 1}],
                }
            ],
        },
    )

    assert "Computer Use apps" in visible
    assert "vscode" in visible
    assert "VS Code" in visible
    assert "visual studio code" not in visible
    assert "windows" not in visible
    _assert_not_json_wrapper(visible)


def test_computer_use_observation_filters_blank_candidates():
    visible = _visible(
        "computer_use_observe_scene",
        {
            "ok": True,
            "summary": "Observed current scene.",
            "candidates": [
                {"confidence": 0.92},
                {"role": "Pane", "confidence": 0.9},
                {"name": "Search box", "confidence": 0.81},
                {"role": "button", "confidence": 0.7},
            ],
        },
    )

    assert "Computer Use observation" in visible
    assert "Search box" in visible
    assert "button" in visible
    assert "-  confidence=0.92" not in visible
    assert "Pane confidence" not in visible
    _assert_not_json_wrapper(visible)


def test_web_broker_search_exposes_sources_not_control_json():
    visible = _visible(
        "web_broker",
        {
            "ok": True,
            "mode": "search",
            "query": "V8 Agent OS runtime episode",
            "resultCount": 2,
            "sourceQualitySummary": {
                "quality": "mixed",
                "recommendedNextAction": "Read the official docs first.",
            },
            "results": [
                {
                    "title": "Runtime Episodes Guide",
                    "url": "https://example.com/runtime-episodes",
                    "snippet": "Canonical episode queue and typed handoff details.",
                    "sourceQualityHints": {"large": "diagnostic-only"},
                },
                {
                    "title": "Worker Leases",
                    "url": "https://example.com/leases",
                    "snippet": "Heartbeat and lease generation behavior.",
                },
            ],
            "trace": {"raw": "diagnostic"},
        },
    )

    assert "Web broker (search)" in visible
    assert "V8 Agent OS runtime episode" in visible
    assert "Runtime Episodes Guide" in visible
    assert "https://example.com/runtime-episodes" in visible
    assert "sourceQualityHints" not in visible
    assert '"trace"' not in visible
    _assert_not_json_wrapper(visible)


def test_web_broker_read_exposes_content_and_url():
    visible = _visible(
        "web_broker",
        {
            "ok": True,
            "mode": "read",
            "title": "Official API docs",
            "finalUrl": "https://example.com/api",
            "textPreview": "Use this endpoint to create durable runtime episodes.",
            "links": [{"title": "Reference", "url": "https://example.com/ref"}],
            "contentChars": 4096,
            "htmlChars": 9999,
            "usedBrowserProfile": False,
            "providerAttemptMatrix": [{"provider": "duckduckgo", "status": "ok"}],
            "networkRoute": "global_proxy",
            "sourceRouter": {"selectedProvider": "duckduckgo"},
        },
    )

    assert "Web broker (read)" in visible
    assert "Official API docs" in visible
    assert "https://example.com/api" in visible
    assert "durable runtime episodes" in visible
    assert "contentChars" not in visible
    assert "htmlChars" not in visible
    assert "usedBrowserProfile" not in visible
    assert "providerAttemptMatrix" not in visible
    assert "networkRoute" not in visible
    assert "sourceRouter" not in visible
    _assert_not_json_wrapper(visible)


def test_web_broker_read_preserves_useful_content_shape():
    visible = _visible(
        "web_broker",
        {
            "ok": True,
            "mode": "read",
            "title": "Reference page",
            "finalUrl": "https://example.com/reference",
            "text": "Steps:\n1. Create an episode.\n2. Wait for typed handoff.\n\n| Field | Meaning |\n| episodeId | durable run unit |",
        },
        budget=3200,
    )

    assert "Content:" in visible
    assert "1. Create an episode." in visible
    assert "| Field | Meaning |" in visible
    assert "https://example.com/reference" in visible
    _assert_not_json_wrapper(visible)


def test_delegation_broker_exposes_tasks_without_selection_diagnostics():
    visible = _visible(
        "delegation_broker",
        {
            "ok": True,
            "mode": "dispatch",
            "summary": "Dispatched 2 worker tasks.",
            "tasks": [
                {
                    "taskGoal": "Review research evidence.",
                    "target": "evidence-reviewer",
                    "status": "started",
                    "selectionTrace": {"large": "diagnostic-only"},
                },
                {
                    "taskGoal": "Draft implementation risks.",
                    "target": "engineering-reviewer",
                    "status": "queued",
                },
            ],
            "traceRef": "diag://trace",
        },
    )

    assert "Delegation broker (dispatch)" in visible
    assert "Review research evidence" in visible
    assert "engineering-reviewer" in visible
    assert "selectionTrace" not in visible
    assert "traceRef" not in visible
    _assert_not_json_wrapper(visible)


def test_fetch_skill_instructions_keeps_method_and_hides_loader_paths():
    message = ToolMessage(
        content=(
            "=== SKILL ENTRYPOINTS ===\n"
            "Skill Name: huashu-nuwa\n"
            "Skill Root: C:/Users/sunny/.agents/skills/huashu-nuwa\n"
            "Directory Structure: very large tree\n"
            "=== CONTINUATION MANIFEST ===\n"
            "- references/template.md\n"
            "=== INSTRUCTIONS SUMMARY ===\n"
            "Read the source material, extract the mental model, and produce a runnable skill.\n"
            "Never invent citations.\n"
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1600},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert visible.startswith("Skill instructions")
    assert "huashu-nuwa" not in visible
    assert "extract the mental model" in visible
    assert "Never invent citations" in visible
    assert "Skill Root:" not in visible
    assert "Directory Structure:" not in visible
    assert "C:/Users/sunny" not in visible
    assert visible.index("Instructions:") < visible.index("Continuation manifest")
    assert "not a replacement for the instructions above" in visible


def test_invalid_fetch_skill_call_stays_a_compact_error_instead_of_skill_guidance():
    message = ToolMessage(
        content=(
            "Error: fetch_skill_instructions is not a valid tool, "
            "try one of [read_native_file, run_system_command]."
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-invalid-fetch-skill",
    )
    visible_message = apply_tool_surface_budget(
        message,
        {"agentVisibleBudget": 1200},
        tool_name="fetch_skill_instructions",
    )
    visible = str(visible_message.content)

    assert visible.startswith("Error: fetch_skill_instructions is not a valid tool")
    assert "Skill instructions" not in visible
    assert "Relative path continuation" not in visible
    assert visible_message.additional_kwargs["v8_tool_output_budget"]["semanticTruncationStrategy"] == "invalid_tool_error"


def test_fetch_skill_instructions_drops_manifest_before_truncating_main_contract():
    main_body = "Main contract line.\n" * 80
    manifest_body = "- references/generated.md\n" * 200
    message = ToolMessage(
        content=(
            "Skill Name: big-skill\n"
            "=== CONTINUATION MANIFEST ===\n"
            f"{manifest_body}"
            "=== INSTRUCTIONS SUMMARY ===\n"
            f"{main_body}"
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill-big",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 2600},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert "Instructions:" in visible
    assert "Main contract line." in visible
    assert "Continuation manifest" not in visible
    assert "too large" not in visible


def test_fetch_skill_instructions_truncates_main_contract_with_same_document_offset():
    message = ToolMessage(
        content=(
            "Skill Name: too-large-skill\n"
            "=== INSTRUCTIONS SUMMARY ===\n"
            + ("Do not start from a partial skill contract.\n" * 120)
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill-too-large",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1200},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert visible.startswith("Skill instructions")
    assert "Do not start from a partial skill contract." in visible
    assert "main SKILL.md truncated at offset" in visible
    assert "fetch_skill_instructions(skill_name='too-large-skill', detail_level='full', offset=" in visible
    assert "do not start implementing from a partial SKILL.md" in visible
    assert "blocked until the complete main SKILL.md contract can be read" not in visible


def test_fetch_skill_relative_file_keeps_resource_document_content():
    message = ToolMessage(
        content=(
            "=== SKILL FILE ===\n"
            "Skill Name: demo-skill\n"
            "Relative Path: references/workflow.md\n"
            "Read Offset: 0\n"
            "Returned Chars: 86\n"
            "Total Chars: 86\n"
            "Next Offset: \n"
            "Continuation API: fetch_skill_instructions(skill_name='demo-skill', relative_path='<path>')\n\n"
            "=== FILE CONTENT ===\n"
            "# Workflow\n\n"
            "Step 1: read the whole resource document.\n"
            "Step 2: only then execute the method.\n"
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill-relative",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1800},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert visible.startswith("Skill file: references/workflow.md")
    assert "Contract: preserve this file's original order" in visible
    assert "# Workflow" in visible
    assert "Step 2: only then execute the method." in visible
    assert "Use the main SKILL.md instructions below" not in visible
    assert "Continuation manifest" not in visible


def test_fetch_skill_script_result_keeps_actionable_output_and_hides_runtime_shape():
    message = ToolMessage(
        content=(
            "=== SKILL SCRIPT RESULT ===\n"
            "Status: completed\n"
            "Script: scripts/check-quality.py\n"
            "Exit Code: 0\n"
            "Summary: 脚本执行成功。\n\n"
            "Output:\nquality score: 98\n\n"
            "Next Action: 继续按 SKILL.md 验证后续产物。"
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill-script",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1400},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert visible.startswith("=== SKILL SCRIPT RESULT ===")
    assert "quality score: 98" in visible
    assert "Next Action:" in visible
    assert "Skill instructions\n" not in visible
    assert "toolobs://" in visible


def test_fetch_skill_relative_file_truncates_with_same_document_offset():
    body = "Resource contract line.\n" * 80
    message = ToolMessage(
        content=(
            "=== SKILL FILE ===\n"
            "Skill Name: demo-skill\n"
            "Relative Path: references/large.md\n"
            "Read Offset: 1200\n"
            f"Returned Chars: {len(body)}\n"
            "Total Chars: 9000\n"
            "Next Offset: 3200\n"
            "Continuation API: fetch_skill_instructions(skill_name='demo-skill', relative_path='references/large.md', offset=3200)\n\n"
            "=== FILE CONTENT ===\n"
            f"{body}"
        ),
        name="fetch_skill_instructions",
        tool_call_id="call-fetch-skill-relative-large",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1200},
            tool_name="fetch_skill_instructions",
        ).content
    )

    assert "Skill file: references/large.md" in visible
    assert "Resource contract line." in visible
    assert "skill relative file truncated at offset" in visible
    assert "relative_path='references/large.md'" in visible
    assert "offset=" in visible
    assert "inspect the raw file in workspace tools" not in visible


def test_skill_relative_file_reader_supports_offset_continuation(tmp_path):
    target = tmp_path / "references.md"
    target.write_text("abcdefg" * 10, encoding="utf-8")

    first, first_offset, total, truncated = _read_skill_text_file(target, max_chars=12, offset=0)
    second, second_offset, second_total, second_truncated = _read_skill_text_file(target, max_chars=12, offset=12)

    assert first == "abcdefgabcde"
    assert first_offset == 0
    assert total == 70
    assert truncated is True
    assert second == "fgabcdefgabc"
    assert second_offset == 12
    assert second_total == 70
    assert second_truncated is True


def test_read_audit_log_summarizes_json_body_without_long_raw_line():
    payload = {
        "skillName": "huashu-nuwa",
        "verdict": "audit",
        "confidence": 0.88,
        "reasons": ["发现 声明式密钥/环境变量依赖（11 个文件）。"],
        "flaggedFiles": [
            {
                "path": f"examples/example-{idx}/references/research/long-file.md",
                "severity": "low",
                "findings": [{"id": "secret_declaration", "reason": "发现 skill 需要 API Key、Token 或环境变量配置。"}],
            }
            for idx in range(20)
        ],
        "scannedFiles": 126,
        "candidateFiles": 126,
        "skillTrustScore": 56,
        "ledgerId": "skillreview_abc",
    }
    message = ToolMessage(
        content=(
            "[2026-06-12 01:52:21] [SAFETY] skill_scan - INFO: "
            + json.dumps(payload, ensure_ascii=False)
        ),
        name="read_audit_log",
        tool_call_id="call-read-audit-log",
    )
    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 2500},
            tool_name="read_audit_log",
        ).content
    )

    assert visible.startswith("Audit log")
    assert "huashu-nuwa" in visible
    assert "verdict=audit" in visible
    assert "items=20" in visible
    assert "skillreview_abc" in visible
    assert "secret_declaration" not in visible
    assert '"flaggedFiles"' not in visible
    assert max(len(line) for line in visible.splitlines()) < 1200
    _assert_not_json_wrapper(visible)


def test_unknown_json_defaults_to_minimal_summary_with_detail_tool():
    visible = _visible(
        "new_experimental_tool",
        {
            "ok": True,
            "summary": "Created a candidate plan with two sources.",
            "results": [
                {"title": "Primary source", "url": "https://example.com/source", "snippet": "Useful fact."}
            ],
            "internalControl": {"token": "do-not-show"},
        },
    )

    assert visible.startswith("new experimental tool result")
    assert "Created a candidate plan" in visible
    assert "https://example.com/source" in visible
    assert "tool_observation_detail" in visible
    assert "internalControl" not in visible
    _assert_not_json_wrapper(visible)


def test_malformed_structured_output_never_exposes_partial_json():
    message = ToolMessage(
        content='{"ok": true, "summary": "cut off", "internal": {',
        name="new_experimental_tool",
        tool_call_id="call-malformed-json",
    )

    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1200},
            tool_name="new_experimental_tool",
        ).content
    )

    assert "incomplete structured output" in visible
    assert "tool_observation_detail" in visible
    assert not visible.lstrip().startswith("{")
    assert '"internal"' not in visible


def test_bracketed_control_message_is_not_misclassified_as_partial_json():
    message = ToolMessage(
        content="[route required] Engineering runtime must handle this write.",
        name="write_native_file",
        tool_call_id="call-route-required",
    )

    visible = str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": 1200},
            tool_name="write_native_file",
        ).content
    )

    assert "[route required]" in visible
    assert "incomplete structured output" not in visible
