from core.output_surface_contract import RuntimeSurfaceRecord, project_output_surfaces


def test_runtime_record_hash_is_stable_and_agent_keeps_exact_control_ids():
    runtime = RuntimeSurfaceRecord.create(
        source="provider", version="v3", recorded_at="2026-09-15T00:00:00Z",
        actor_id="supervisor", payload={"delegationId": "d-1", "secret": "must-stay-runtime"},
    )
    same = RuntimeSurfaceRecord.create(
        source="provider", version="v3", recorded_at="2026-09-15T00:00:00Z",
        actor_id="supervisor", payload={"secret": "must-stay-runtime", "delegationId": "d-1"},
    )
    assert runtime.raw_sha256 == same.raw_sha256
    _, agent, human = project_output_surfaces(
        runtime=runtime, status="partial", summary="Child result needs acceptance",
        tool_call_id="call-7", delegation_id="d-1", detail_ref="toolobs://r-1",
        next_action={"tool": "runtime_broker", "arguments": {"mode": "accept_partial", "handoff_id": "h-1"}},
        proof={"handoffId": "h-1", "version": "v3"}, risk="Acceptance is pending", next_step="accept or retry",
    )
    assert agent.tool_call_id == "call-7"
    assert agent.delegation_id == "d-1"
    assert agent.detail_ref == "toolobs://r-1"
    assert "secret" not in human.result
    assert "delegationId" not in human.as_dict()

