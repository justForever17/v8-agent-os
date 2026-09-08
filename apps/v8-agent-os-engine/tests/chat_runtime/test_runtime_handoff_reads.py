from types import SimpleNamespace

import pytest

from graph.runtime_handoff_reads import is_research_handoff_read, research_handoff_read_targets


def test_saved_answer_preparation_keeps_exact_refs_and_pagination_before_delegation():
    targets = research_handoff_read_targets({}, user_query="复核 rxp_12345678，当前包 research_87654321")
    for args in [
        {"mode": "get_experience", "experiencePackId": "rxp_12345678"},
        {"mode": "get_evidence", "evidenceBundleId": "research_87654321", "readAnswer": True, "startChar": 5371, "maxChars": 6000},
        {"mode": "get_evidence", "evidenceBundleId": "research_87654321", "sourceKey": "S3", "startChar": 0},
    ]:
        assert is_research_handoff_read(SimpleNamespace(tool_calls=[{"name": "research_broker", "args": args}]), targets)
    for args in [
        {"mode": "run", "experiencePackId": "rxp_12345678"},
        {"mode": "get_experience", "experiencePackId": "rxp_other123"},
        {"mode": "get_evidence", "evidenceBundleId": "research_other123"},
        {"mode": "get_evidence", "evidenceBundleId": "research_87654321", "forceRefresh": True},
    ]:
        assert not is_research_handoff_read(SimpleNamespace(tool_calls=[{"name": "research_broker", "args": args}]), targets)
from graph.supervisor_turn import _response_runtime_route_kinds


def test_read_preparation_preserves_exact_terminal_references_without_completing_route():
    state = {"current_route_context": {"handoffRefs": [{
        "kind": "research", "status": "degraded", "rawRef": "toolobs://original",
        "researchRefs": ["research://bundle/original"],
    }]}}
    targets = research_handoff_read_targets(state)
    for name, args in [
        ("research_broker", {"mode": "get_evidence", "evidenceBundleId": "original"}),
        ("tool_observation_detail", {"raw_ref": "toolobs://original", "start_char": 8000, "max_chars": 4000}),
    ]:
        response = SimpleNamespace(tool_calls=[{"name": name, "args": args}])
        assert is_research_handoff_read(response, targets)
        assert _response_runtime_route_kinds(response) == []
    state["current_route_context"]["effectiveHandoffRefs"] = [{"kind": "research", "status": "queued"}]
    assert research_handoff_read_targets(state) == {}


@pytest.mark.parametrize("calls", [
    [],
    [{"name": "research_broker", "args": {"mode": "run", "evidenceBundleId": "original"}}],
    [{"name": "research_broker", "args": {"mode": "get_evidence", "evidenceBundleId": "other"}}],
    [{"name": "research_broker", "args": {"mode": "get_evidence", "evidenceBundleId": "original", "forceRefresh": True}}],
    [{"name": "tool_observation_detail", "args": {"raw_ref": "toolobs://other"}}],
    [{"name": "tool_observation_detail", "args": {"raw_ref": "toolobs://original"}}, {"name": "write_native_file", "args": {}}],
    [{"name": "research_broker", "args": "invalid"}],
])
def test_preparation_does_not_admit_unbound_reads_research_restarts_or_mixed_side_effects(calls):
    targets = {"research_broker": {"original"}, "tool_observation_detail": {"toolobs://original"}}
    assert not is_research_handoff_read(SimpleNamespace(tool_calls=calls), targets)
