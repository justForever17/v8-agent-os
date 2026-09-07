from __future__ import annotations

import pytest

from core.research_runtime_prompts import (
    RESEARCH_INTERNAL_STAGES,
    RESEARCH_PROMPT_CONTRACT_VERSION,
    build_research_runtime_system_prompt,
    research_runtime_prompt_digest,
)


@pytest.mark.parametrize("stage", sorted(RESEARCH_INTERNAL_STAGES))
def test_research_runtime_builds_authoritative_contract_for_every_internal_stage(stage: str):
    prompt = build_research_runtime_system_prompt(
        stage=stage,
        stage_prompt=f"STAGE-SCHEMA-SENTINEL:{stage}",
    )

    assert f"version={RESEARCH_PROMPT_CONTRACT_VERSION}" in prompt
    assert f"stage={stage}" in prompt
    assert f"STAGE-SCHEMA-SENTINEL:{stage}" in prompt
    assert "Research Runtime owns search" in prompt
    assert "Use only the tools explicitly bound to this stage" in prompt
    assert "do not call tools" not in prompt
    assert "successfully read source bodies" in prompt
    assert "explicitly undated source paired with retrieval time" in prompt
    assert "Attributed secondary sources remain valid" in prompt
    assert "it is not by itself a whole-answer veto" in prompt
    assert "Reserve criticalMissingEvidence for an absent material premise" in prompt
    assert "Drop unsupported candidates" in prompt
    assert "question's primary language" in prompt
    assert "write literal search-engine queries" in prompt
    assert "Do not prefix them with commands" in prompt


def test_research_runtime_contract_keeps_counts_advisory_and_claims_revisable():
    prompt = build_research_runtime_system_prompt(
        stage="evidence_plan",
        stage_prompt="Return the evidence-plan schema.",
    )

    assert "counts are descriptive or advisory" in prompt
    assert "research claims are revisable" in prompt
    assert "read snapshots are immutable" in prompt
    assert "Never pad" in prompt
    assert "one relevant primary document may suffice" in prompt


@pytest.mark.parametrize("stage", ["", "final_answer", "web_search", "unknown"])
def test_research_runtime_contract_rejects_unknown_stages(stage: str):
    with pytest.raises(ValueError, match="Unknown Research internal stage"):
        build_research_runtime_system_prompt(stage=stage, stage_prompt="anything")


def test_research_runtime_contract_rejects_empty_stage_schema():
    with pytest.raises(ValueError, match="stage prompt is empty"):
        build_research_runtime_system_prompt(stage="query_plan", stage_prompt="  ")


def test_research_runtime_prompt_digest_is_stable_and_stage_sensitive():
    query_prompt = build_research_runtime_system_prompt(
        stage="query_plan",
        stage_prompt="Return queries.",
    )
    writer_prompt = build_research_runtime_system_prompt(
        stage="answer_writer",
        stage_prompt="Return an answer.",
    )

    assert research_runtime_prompt_digest(query_prompt) == research_runtime_prompt_digest(query_prompt)
    assert research_runtime_prompt_digest(query_prompt) != research_runtime_prompt_digest(writer_prompt)
