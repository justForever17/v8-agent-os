import importlib.util
from pathlib import Path
import subprocess
import sys
import json


SOURCE = Path(__file__).with_name("run_cross_graph_live_acceptance.py")


def test_live_flag_is_required_before_importing_engine_or_creating_state(tmp_path):
    absent = tmp_path / "uncreated"
    result = subprocess.run([sys.executable, str(SOURCE), "--engine-url", "http://127.0.0.1:22930",
        "--web-url", "http://127.0.0.1:22927", "--state-root", str(absent), "--output", str(absent / "report.json")],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "--live and --allow-side-effects" in result.stderr
    assert not absent.exists()


def test_completion_after_the_worker_finishes_is_not_concurrent_parent_work():
    spec = importlib.util.spec_from_file_location("cross_graph_live", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    observed = module.parent_write_during_episode
    assert observed(15, [(10, 20)])
    assert not observed(25, [(10, 20)])
    assert not observed(20, [(10, 20)])
    assert not observed(5, [(10, 20)])
    assert not observed(15, [])


def test_parent_B_needs_a_successful_exact_native_writer_receipt(tmp_path):
    spec = importlib.util.spec_from_file_location("cross_graph_native_write", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "parent-b.txt"
    def event(topic, **fields):
        return {"topic": topic, "payload": {"ownerRuntimeId": "chat", "ownerAgentKind": "supervisor",
            "tool": {"toolName": "write_native_file", "toolCallId": "native-B", **fields}}}
    events = [event("tool.started", args={"path": str(target)}),
        event("tool.finished", resultStatus="completed", result=f"Successfully Created/Overwritten file: {target} (12 chars written)\nContent version: fixture-version; same-actor consecutive edits can reuse this receipt.")]
    assert module.parent_native_write_receipts(events, target) == ["native-B"]
    assert not module.parent_native_write_receipts(events[:1], target)
    assert not module.parent_native_write_receipts(events, tmp_path / "other.txt")
    events[-1]["payload"]["tool"]["resultStatus"] = "failed"
    assert not module.parent_native_write_receipts(events, target)
    events[-1]["payload"]["tool"]["resultStatus"] = "completed"
    events[-1]["payload"]["ownerAgentKind"] = "subagent"
    assert not module.parent_native_write_receipts(events, target)


def test_readonly_worker_proof_requires_a_real_child_command_and_zero_exit(tmp_path):
    spec = importlib.util.spec_from_file_location("cross_graph_stdout", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    worker = tmp_path / "validate_a.py"
    marker = "cross-graph-live-fixture"
    ready = {"marker": marker, "pid": 123, "started": 10.0, "scriptSha256": "source-hash"}
    done = {**ready, "finished": 20.0, "sha256": "result-hash"}
    def event(topic, call, name, **tool):
        return {"topic": topic, "payload": {"ownerAgentKind": "subagent", "tool": {"toolCallId": call, "toolName": name, **tool}}}
    events = [
        event("tool.started", "start", "run_system_command", args={"command": "python -B -u validate_a.py"}),
        event("tool.finished", "start", "run_system_command", result={"commandId": "command-1", "initialPreview": "CROSS_GRAPH_READY " + json.dumps(ready)}),
        event("tool.started", "observe", "command_session_broker", args={"command_id": "command-1"}),
        event("tool.finished", "observe", "command_session_broker", result={"commandId": "command-1", "returnCode": 0, "finalPreview": "CROSS_GRAPH_DONE " + json.dumps(done)}),
    ]
    assert module.worker_stdout_proof(events, worker, marker)["done"] == done
    assert not module.worker_stdout_proof(events[1:], worker, marker), "an unpaired output is not execution proof"
    assert not module.worker_stdout_proof(events, worker, "another-run"), "another run's stdout cannot be replayed"
    events[-1]["payload"]["tool"]["result"]["returnCode"] = 1
    assert not module.worker_stdout_proof(events, worker, marker), "a failed process cannot satisfy verification"
    events[-1]["payload"]["tool"]["result"]["returnCode"] = 0
    events[0]["payload"]["tool"]["args"]["command"] = 'python -B -u validate_a.py; echo fake-proof'
    assert not module.worker_stdout_proof(events, worker, marker), "a shell recipe is not the exact readonly fixture"
    events[0]["payload"]["tool"]["args"]["command"] = 'python -B -u validate_a.py'
    for row in events:
        row["payload"]["ownerAgentKind"] = "supervisor"
    assert not module.worker_stdout_proof(events, worker, marker), "the parent cannot impersonate the child"
