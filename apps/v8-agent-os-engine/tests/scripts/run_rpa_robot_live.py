"""Robot timeout/cancel/dedup/recovery using only disposable local files."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    state = Path(os.environ.get("V8_AGENT_OS_HOME", "")).resolve()
    output = Path(args.output).resolve()
    if not args.live or not os.environ.get("V8_AGENT_OS_HOME") or state == (Path.home() / ".v8-agent-os").resolve() or output.exists():
        parser.error("Requires --live, isolated V8_AGENT_OS_HOME, and fresh --output")
    output.mkdir(parents=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.runtime.startup_profile import get_runtime_registry_state
    get_runtime_registry_state()
    from runtimes.rpa.runtime import rpa_runtime
    from erc.command_service import command_service
    from core.database import db

    checks = []
    for kind in ("timeout", "cancel", "complete"):
        directory = output / kind
        directory.mkdir()
        counter = directory / "counter.txt"
        flow = directory / "fixture.robot"
        flow.write_text("*** Settings ***\nLibrary    OperatingSystem\n*** Tasks ***\nLocal fixture\n"
                        + f"    Append To File    {counter.as_posix()}    1\n"
                        + ("    Sleep    60s\n" if kind != "complete" else "    File Should Exist    " + counter.as_posix() + "\n"), encoding="utf-8")
        prepared = rpa_runtime.prepare_existing_run(robot_file=flow, output_dir=directory / "robot-output")
        run_id = f"rpa-live-{uuid.uuid4().hex}"
        def stop_after_dispatch():
            deadline = time.monotonic() + 20
            while not counter.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if counter.exists():
                command_service.cancel_run(run_id, reason="Owned RPA fixture cancellation")
        stopper = threading.Thread(target=stop_after_dispatch, daemon=True) if kind == "cancel" else None
        if stopper:
            stopper.start()
        started = time.monotonic()
        result = rpa_runtime._execute_prepared(prepared=prepared, subject=flow.name, mode="existing_robot",
                timeout_ms=1500 if kind == "timeout" else 30000, run_id=run_id,
                user_id="rpa-live-fixture", workspace_path=str(directory), trigger_source="rpa_live_fixture", non_chat_run=True)
        if stopper:
            stopper.join(timeout=3)
        elapsed = round(time.monotonic() - started, 3)
        assert counter.read_text() == "1", result
        expected = {"timeout": "unknown", "cancel": "cancelled", "complete": "completed"}[kind]
        assert result["status"] == expected, result
        record = db.get_run_record(run_id)
        assert record["metadata"]["executionState"] == expected, record
        if kind == "timeout":
            duplicate = rpa_runtime._execute_prepared(prepared=prepared, subject=flow.name, mode="existing_robot",
                timeout_ms=1500, run_id=run_id, user_id="rpa-live-fixture", workspace_path=str(directory),
                trigger_source="rpa_live_fixture", non_chat_run=True)
            assert duplicate["status"] == "unknown" and counter.read_text() == "1", duplicate
        checks.append({"case": kind, "status": result["status"], "runId": run_id,
                       "elapsedSeconds": elapsed, "effectCount": len(counter.read_text()),
                       "reconciliationRequired": result.get("reconciliationRequired", False)})
    from erc.command_router import runtime_command_router
    from erc.command_service import ApprovalDecisionConflict
    from erc.kernel import erc_kernel
    from erc.models import RuntimeCommand
    from erc.safety_guardian import SafetyDecision
    # Inject only the policy verdict. Approval persistence/delivery and Robot
    # are real, including recovery of an approved but not-yet-delivered job.
    with patch.object(command_service, "_should_auto_approve", return_value=False), patch.object(
            rpa_runtime, "_run_preflight", return_value=SafetyDecision(verdict="review", reason="owned fixture review")):
        for kind in ("approve", "cancel_before_approve", "changed_artifact", "recover_approval"):
            directory = output / kind
            directory.mkdir()
            counter = directory / "counter.txt"
            flow = directory / "fixture.robot"
            flow.write_text("*** Settings ***\nLibrary    OperatingSystem\n*** Tasks ***\nApproved fixture\n"
                            + f"    Append To File    {counter.as_posix()}    1\n"
                            + f"    File Should Exist    {counter.as_posix()}\n", encoding="utf-8")
            prepared = rpa_runtime.prepare_existing_run(robot_file=flow, output_dir=directory / "robot-output")
            run_id = f"rpa-live-{uuid.uuid4().hex}"
            result = rpa_runtime._execute_prepared(prepared=prepared, subject=flow.name, mode="existing_robot",
                    run_id=run_id, user_id="rpa-live-fixture", workspace_path=str(directory), non_chat_run=True)
            assert result["status"] == "review_required" and not counter.exists(), result
            if kind == "cancel_before_approve":
                command_service.cancel_run(run_id, reason="owned fixture")
            elif kind == "changed_artifact":
                flow.write_text(flow.read_text(encoding="utf-8") + "    No Operation\n", encoding="utf-8")
            async def approve():
                command = RuntimeCommand(topic="approval.approve", approval_id=result["approvalId"])
                if kind == "cancel_before_approve":
                    try:
                        runtime_command_router.dispatch_approval_command(command)
                    except ApprovalDecisionConflict:
                        return
                    raise AssertionError("Cancelled run accepted an approval")
                if kind == "recover_approval":
                    erc_kernel.approve(result["approvalId"])
                    runtime_command_router.deliver_approval_resume(result["approvalId"], restart=True)
                else:
                    runtime_command_router.dispatch_approval_command(command)
                for _ in range(400):
                    if db.get_run_record(run_id)["metadata"].get("executionState") in {"completed", "blocked", "unknown"}:
                        break
                    await asyncio.sleep(.05)
                runtime_command_router.dispatch_approval_command(command)
            asyncio.run(approve())
            record = db.get_run_record(run_id)
            count = len(counter.read_text()) if counter.exists() else 0
            expected = "cancelled" if kind == "cancel_before_approve" else "failed" if kind == "changed_artifact" else "completed"
            assert record["status"] == expected, record["status"]
            assert count == (1 if expected == "completed" else 0), count
            checks.append({"case": kind, "runId": run_id, "status": record["status"], "effectCount": count})
    report = {"ok": True, "evidenceClass": "real_robot_local_file_effect", "checks": checks}
    (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
