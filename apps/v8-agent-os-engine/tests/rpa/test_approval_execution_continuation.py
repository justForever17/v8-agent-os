from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest

from core.database import db
from erc.command_router import runtime_command_router
from erc.command_service import command_service
from erc.models import RuntimeCommand
from erc.safety_guardian import SafetyDecision
from runtimes.rpa.runtime import rpa_runtime


@pytest.mark.parametrize("change", ["approve", "cancel", "artifact", "identity", "command"])
def test_approval_resumes_exact_version_once(tmp_path, monkeypatch, change):
    # Real ERC database, approval delivery and receipt; a visible local effect
    # replaces only the external Robot process at this contract-test layer.
    monkeypatch.setattr(command_service, "_should_auto_approve", lambda kind: False)
    monkeypatch.setattr(rpa_runtime, "_run_preflight", lambda **kw: SafetyDecision(verdict="allow"))
    monkeypatch.setattr(rpa_runtime, "_required_approvals", lambda *a, **kw: [{"stepId": "fixture"}])
    effect = tmp_path / "effects.txt"
    def execute(**kw):
        with effect.open("a") as stream:
            stream.write("1")
        return {"returncode": 0, "stdout": "", "stderr": ""}
    monkeypatch.setattr(rpa_runtime.adapter, "run_command", execute)
    artifact = tmp_path / "flow.robot"
    artifact.write_text("fixture")
    prepared = {"robotFile": str(artifact), "command": ["robot", str(artifact)],
                "export": {"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}}
    run_id = f"rpa-contract-{uuid.uuid4().hex}"
    result = rpa_runtime._execute_prepared(prepared=prepared, subject="owned", mode="existing_robot", run_id=run_id,
                                          user_id="fixture", workspace_path=str(tmp_path))
    assert result["status"] == "review_required" and not effect.exists()
    if change == "cancel":
        command_service.cancel_run(run_id, reason="fixture")
    elif change == "artifact":
        artifact.write_text("changed")
    elif change == "identity":
        from erc.run_service import run_service
        run_service.update_metadata(run_id, {"variables": {"destination": "different"}})
    elif change == "command":
        from erc.run_service import run_service
        run_service.update_metadata(run_id, {"command": ["unexpected-program", "different-effect"]})
    async def approve():
        command = RuntimeCommand(topic="approval.approve", approval_id=result["approvalId"])
        if change == "cancel":
            from erc.command_service import ApprovalDecisionConflict
            with pytest.raises(ApprovalDecisionConflict):
                runtime_command_router.dispatch_approval_command(command)
            return
        runtime_command_router.dispatch_approval_command(command)
        for _ in range(100):
            state = (db.get_run_record(run_id)["metadata"] or {}).get("executionState")
            if state in {"completed", "blocked"} or change == "cancel":
                break
            await asyncio.sleep(.025)
        runtime_command_router.dispatch_approval_command(command)
        await asyncio.sleep(.1)
    asyncio.run(approve())
    if change == "approve":
        assert effect.read_text() == "1"
        assert db.get_run_record(run_id)["metadata"]["executionState"] == "completed"
    else:
        assert not effect.exists()
        assert db.get_run_record(run_id)["status"] in {"failed", "cancelled"}
