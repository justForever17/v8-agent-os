from __future__ import annotations

import hashlib

from core.tools.research_quality import (
    MIN_RESEARCH_ANSWER_CHARS,
    MIN_RESEARCH_CLAIM_COUNT,
    MIN_RESEARCH_DISTINCT_HOST_COUNT,
    MIN_RESEARCH_SOURCE_COUNT,
    TARGET_RESEARCH_ANSWER_CHARS,
    TARGET_RESEARCH_CLAIM_COUNT,
    TARGET_RESEARCH_DISTINCT_HOST_COUNT,
    TARGET_RESEARCH_SOURCE_COUNT,
)


RESEARCH_PROMPT_CONTRACT_VERSION = "2026-08-01.10"
RESEARCH_INTERNAL_STAGES = frozenset(
    {
        "query_plan",
        "evidence_gap",
        "evidence_plan",
        "answer_writer",
    }
)


def build_research_runtime_system_prompt(*, stage: str, stage_prompt: str) -> str:
    """Build the authoritative system contract for an internal Research stage.

    The registered Agent remains the model-routing identity. Its managed
    Markdown is intentionally not loaded here: user-editable persona text must
    never become execution truth for Research orchestration or quality gates.
    """

    normalized_stage = str(stage or "").strip().lower()
    if normalized_stage not in RESEARCH_INTERNAL_STAGES:
        raise ValueError(f"Unknown Research internal stage: {normalized_stage or '<empty>'}")
    normalized_stage_prompt = str(stage_prompt or "").strip()
    if not normalized_stage_prompt:
        raise ValueError(f"Research internal stage prompt is empty: {normalized_stage}")

    return (
        f"[RESEARCH-RUNTIME-CONTRACT version={RESEARCH_PROMPT_CONTRACT_VERSION} "
        f"stage={normalized_stage}]\n"
        "Authority and roles:\n"
        "- The Supervisor has already selected the Research route and remains the final user-facing decision maker.\n"
        "- Research Runtime owns search, page/PDF reads, parallel shard execution, retries, URL deduplication, the canonical ledger, validation, and delivery gates.\n"
        "- You are the model bound to exactly one internal stage. Use only the supplied stage inputs, do not call tools, do not delegate, do not address the end user, and do not self-approve delivery.\n"
        "Evidence contract:\n"
        "- Treat only successfully read source bodies and Runtime-verified excerpts as evidence. Search snippets, unread URLs, provider summaries, and aggregate counts are not evidence.\n"
        "- Preserve every supplied facetId/taskBriefId and bind every material claim to supplied source lineage. Never fabricate facts, excerpts, dates, citations, or official positions.\n"
        "- Primary and official sources are preferred for authoritative claims. Attributed secondary sources remain valid for experience, implementation examples, history, or disputes and must not be upgraded into official rules.\n"
        "- Evidence hierarchy controls attribution, confidence, and caveats; it is not by itself a whole-answer veto. When only a readable secondary source supports a bounded point, retain it as that source's attributed view with its timestamp and limitation instead of silently promoting it or discarding the rest of the answer.\n"
        "- Reserve criticalMissingEvidence for an absent material premise that prevents a useful core answer even after unsupported claims are dropped or qualified. A preferred primary source being unavailable is non-blocking when the existing evidence can support an explicitly attributed finding or an honest unresolved-item statement.\n"
        "- Judge temporal adequacy by the claim and source: publication/update/effective dates, versions, stable-current status, or an explicitly undated source paired with retrieval time may all be relevant. Do not impose a fixed publication year.\n"
        "- Write every free-text field in the question's primary language unless a verbatim source excerpt must retain its original language. Preserve identifiers and schema field names exactly.\n"
        "- When a stage emits query, searchQuery, or recommendedNextQueries, write literal search-engine queries. Do not prefix them with commands such as Fetch, Retrieve, Read, Open, Search for, or equivalent imperatives.\n"
        "Quality policy:\n"
        f"- Hard rejection floor: {MIN_RESEARCH_SOURCE_COUNT} readable selected sources, "
        f"{MIN_RESEARCH_DISTINCT_HOST_COUNT} independent hosts, {MIN_RESEARCH_CLAIM_COUNT} "
        f"source-backed claims, and {MIN_RESEARCH_ANSWER_CHARS} effective non-URL answer characters.\n"
        f"- Normal delivery target: at least {TARGET_RESEARCH_SOURCE_COUNT} sources, "
        f"{TARGET_RESEARCH_DISTINCT_HOST_COUNT} hosts, {TARGET_RESEARCH_CLAIM_COUNT} "
        f"supported conclusions, and {TARGET_RESEARCH_ANSWER_CHARS} effective answer characters. "
        "A result between the rejection floor and target is only minimum-qualified; continue research when useful evidence is still obtainable.\n"
        "- Coverage and usefulness outrank raw counts. Never pad with repetition, irrelevant background, navigation text, duplicated claims, or invented detail.\n"
        "- Gate the claims that will actually be published, not every candidate sentence discovered during search. Drop unsupported candidates; do not let one discarded candidate erase an otherwise grounded answer.\n"
        "Stage contract:\n"
        f"{normalized_stage_prompt}\n"
        "[/RESEARCH-RUNTIME-CONTRACT]"
    )


def research_runtime_prompt_digest(prompt: str) -> str:
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()[:16]
