from copy import deepcopy
import asyncio

import pytest
from langchain_core.messages import AIMessage

from core.research_verification_bindings import audit_research_verification_bindings, research_evidence_bindings
from core.tools.native.delegation import _compact_upstream_handoff_for_agent, _inject_inherited_handoffs_into_tasks
from graph.parallel_support import _validate_required_verification_evidence


def handoff(count=3):
    return {"kind": "research", "producerEpisodeId": "episode-proof", "rawRef": "toolobs://proof",
            "claimTable": [{"claimId": f"read_S{i}:R1", "supportingSources": [
                {"citationKey": f"S{i}", "url": f"https://mirror.example/document-{i}"}
            ]} for i in range(1, count + 1)]}


def table():
    return "| claimId | [S#] | URL | 载体/状态 | 结论 |\n|---|---|---|---|---|\n" + "\n".join(
        f"| read_S{i}:R1 | [S{i}] | https://mirror.example/document-{i} | 译文/正式版 | 部分支持，有缺口 |"
        for i in range(1, 4))


def test_index_survives_selected_parent_and_grandchild_compaction_without_mutation():
    payload = handoff()
    before = deepcopy(payload)
    first = _compact_upstream_handoff_for_agent(payload)
    second = _compact_upstream_handoff_for_agent(first)
    assert second["evidenceBindings"] == research_evidence_bindings(payload)
    assert second["evidenceBindingsComplete"] is True
    task = _inject_inherited_handoffs_into_tasks(
        [{"evidenceRefs": ["episode-proof"]}], {"handoffRefs": [second]})[0]
    assert task["context"]["upstreamHandoffs"][0]["evidenceBindings"] == second["evidenceBindings"]
    assert payload == before


def test_original_read_ids_and_partial_conclusions_pass_without_semantic_grading():
    result = audit_research_verification_bindings(table(), [handoff()])
    assert result == {"checkedRows": 3, "matchedClaimIds": ["read_S1:R1", "read_S2:R1", "read_S3:R1"], "mismatches": []}


@pytest.mark.parametrize("old,new,error", [
    ("read_S1:R1", "C1", "unknown_claim_id:C1"),
    ("https://mirror.example/document-1", "https://official.example/original", "citation_url_binding_mismatch:read_S1:R1"),
    ("[S1]", "[S2]", "citation_url_binding_mismatch:read_S1:R1"),
])
def test_live_failure_mutants_are_rejected_at_existing_branch_acceptance(old, new, error):
    compact = _compact_upstream_handoff_for_agent(handoff())
    branch = {"taskBrief": {"context": {"upstreamHandoffs": [compact]}}}
    message = AIMessage(content=table().replace(old, new))
    failure = _validate_required_verification_evidence(branch=branch, delta_messages=[message])
    assert failure["error"] == "research_verification_binding_mismatch"
    assert error in failure["verificationEvidenceMismatches"]
    # A corrected final result replaces the mistaken one; old messages do not deadlock the branch.
    assert _validate_required_verification_evidence(branch=branch, delta_messages=[message, AIMessage(content=table())]) is None


def test_grouped_claims_and_citations_use_union_of_exact_urls():
    text = "| claimId | [S#] | URL |\n|---|---|---|\n| read_S1:R1, read_S2:R1 | [S1][S2] | https://mirror.example/document-1 + https://mirror.example/document-2 |"
    result = audit_research_verification_bindings(text, [handoff()])
    assert result["matchedClaimIds"] == ["read_S1:R1", "read_S2:R1"]
    assert result["mismatches"] == []


def test_truncated_index_is_not_authority_to_reject_unseen_ids():
    compact = _compact_upstream_handoff_for_agent(handoff(40))
    assert len(compact["evidenceBindings"]) == 32
    assert compact["evidenceBindingsComplete"] is False
    assert compact["truncation"]["omittedByField"]["evidenceBindings"]["omittedCount"] == 8
    assert _compact_upstream_handoff_for_agent(compact)["evidenceBindingsComplete"] is False
    result = audit_research_verification_bindings(table().replace("read_S1:R1", "read_S40:R1"), [compact])
    assert result["mismatches"] == []
    assert "read_S40:R1" not in result["matchedClaimIds"]


@pytest.mark.parametrize("text", ["证据不足，无法核验。", "| label | URL |\n| C1 | https://example.org |", "No table [S1]."])
def test_unstructured_prose_is_unassessed_not_forced_into_a_quality_gate(text):
    assert audit_research_verification_bindings(text, [handoff()]) == {"checkedRows": 0, "matchedClaimIds": [], "mismatches": []}


@pytest.mark.parametrize("repair", [True, False])
def test_real_branch_correction_is_bounded_and_does_not_demand_duplicate_reads(repair):
    from graph.parallel_support import _run_parallel_agent_branch
    from langgraph.types import Command

    calls = []
    parent = {"messages": [], "todos": [], "parallel_branch": {
        "agentId": "verification_worker", "agentName": "Verification Worker",
        "delegationId": "delegation-binding", "invocationId": "invoke-binding",
        "taskBriefId": "brief-binding", "reason": "Verify supplied evidence",
        "taskBrief": {"readOnly": True, "context": {
            "upstreamHandoffs": [_compact_upstream_handoff_for_agent(handoff())]}}}}

    def node(state):
        calls.append(list(state.get("messages") or []))
        text = table() if repair and len(calls) > 1 else table().replace("read_S1:R1", "C1")
        return Command(goto="supervisor", update={"messages": [AIMessage(content=text)]})

    _messages, _todos, summary, children = asyncio.run(_run_parallel_agent_branch(parent, {"node_func": node, "tool_mode": "test"}))
    assert len(calls) == (2 if repair else 3)
    corrections = [message for message in calls[-1] if getattr(message, "additional_kwargs", {}).get(
        "v8_governance_type") == "required_verification_evidence_correction"]
    assert len(corrections) == (1 if repair else 2)
    assert all("repeat successful reads unnecessarily" in message.content for message in corrections)
    assert children == []
    assert summary["status"] == ("ok" if repair else "failed")
    assert summary["verificationEvidence"]["passed"] is repair
    if not repair:
        assert summary["error"] == "research_verification_binding_mismatch"
