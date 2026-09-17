"""Real Windows native tools -> canonical CU trace -> RPA draft, owned fixture only."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--isolated-root", required=True)
    parser.add_argument("--state", help="An existing isolated state with governed CU/RPA packs, for Robot replay")
    args = parser.parse_args()
    root = Path(args.isolated_root).resolve()
    if not args.live or root.exists() or root == (Path.home() / ".v8-agent-os").resolve():
        parser.error("--live and a fresh isolated root are required")
    root.mkdir(parents=True)
    state = Path(args.state).resolve() if args.state else root
    if state == (Path.home() / ".v8-agent-os").resolve():
        parser.error("Real user state is not an acceptance fixture")
    os.environ["V8_AGENT_OS_HOME"] = str(state)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from core.runtime.startup_profile import get_runtime_registry_state
    get_runtime_registry_state()
    from core.database import db
    from core.llm_factory import llm_factory
    from runtimes.computer_use.runtime import computer_use_runtime
    from runtimes.rpa.runtime import rpa_runtime
    from computer_use_owned_window_probe import run_owned_window_probe

    original = db.create_or_update_session
    recordings = {}
    model_calls = []

    def create_session(session_id, *positional, **kwargs):
        result = original(session_id, *positional, **kwargs)
        if session_id.startswith("owned-native-") and session_id not in recordings:
            recordings[session_id] = rpa_runtime.start_recording({
                "source": "computer_use", "sessionId": session_id, "name": "Owned native transcript",
                "goal": "Enter synthetic text and submit in the disposable fixture.",
            })
        return result

    def reject_model(*args, **kwargs):
        model_calls.append("unexpected")
        raise RuntimeError("This deterministic recording fixture must not call a model")

    def replay(*, marker, process_id):
        recording = next(iter(recordings.values()))
        stopped = rpa_runtime.stop_recording(recording["recordingSessionId"])
        draft = stopped.get("draft") or {}
        assert draft.get("id"), stopped.get("compileError")
        submitted = root / "probe" / "submitted.json"
        submitted.write_text(json.dumps({"submittedText": "BEFORE_REPLAY", "pid": process_id}), encoding="utf-8")
        outcome = rpa_runtime.run_draft(script_id=draft["id"], user_id="owned-native-audit",
                workspace_path=str(root / "probe" / "workspace"), expected_updated_at=draft["updatedAt"], timeout_ms=90000)
        if outcome.get("status") == "review_required":
            from erc.command_router import runtime_command_router
            from erc.models import RuntimeCommand
            async def approve_owned_replay():
                for _ in range(1800):
                    for approval in db.list_pending_approvals(run_id=outcome["runId"], status="pending"):
                        runtime_command_router.dispatch_approval_command(RuntimeCommand(topic="approval.approve", approval_id=approval["id"]))
                    record = db.get_run_record(outcome["runId"])
                    if record["metadata"].get("executionState") in {"completed", "unknown", "blocked", "cancelled"}:
                        return record
                    await asyncio.sleep(.05)
                raise TimeoutError("Owned native replay did not finish")
            record = asyncio.run(approve_owned_replay())
            outcome = {**outcome, "status": record["metadata"].get("executionState"), "metadata": record["metadata"]}
        (root / "replay.json").write_text(json.dumps(outcome, default=str, ensure_ascii=False, indent=2), encoding="utf-8")
        actual = json.loads(submitted.read_text(encoding="utf-8-sig"))
        assert outcome.get("status") == "completed", {"status": outcome.get("status"), "error": outcome.get("error"), "reason": outcome.get("reason")}
        assert actual["submittedText"] == marker and actual["pid"] == process_id, actual
        return {"status": "real_host_passed", "runId": outcome.get("runId"), "submittedMarker": marker}

    with patch.object(db, "create_or_update_session", side_effect=create_session), \
         patch.object(llm_factory, "create_for_role", side_effect=reject_model), \
         patch.object(llm_factory, "create_chat_model", side_effect=reject_model):
        result = run_owned_window_probe(computer_use_runtime, output_directory=root / "probe", native_actions=True,
                                        before_cleanup=replay if args.state else None)
    drafts = []
    for recording in recordings.values():
        stopped = rpa_runtime.stop_recording(recording["recordingSessionId"])
        trace = rpa_runtime.compiler.trace_store.get_trace(recording["traceRunId"]) or {}
        drafts.append({"state": stopped["recording"]["state"], "draftId": (stopped.get("draft") or {}).get("id"),
                       "stepCount": len(trace.get("steps") or []), "compileError": stopped.get("compileError"),
                       "steps": [{"action": s.get("action"), "evidence": s.get("metadata", {}).get("evidence"),
                                  "sourceRunId": s.get("metadata", {}).get("sourceRunId"),
                                  "actorId": s.get("metadata", {}).get("actorId"),
                                  "toolCallId": s.get("metadata", {}).get("toolCallId")} for s in trace.get("steps", [])]})
    result.update({"drafts": drafts, "modelCalls": len(model_calls)})
    result["ok"] = bool(result.get("ok")) and not model_calls and bool(drafts) and all(
        d["state"] == "draft_ready" and d["stepCount"] >= 4 and d["draftId"] for d in drafts)
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "failedStage": result.get("failedStage"), "error": result.get("error"),
                      "drafts": drafts, "report": str(root / "result.json")}, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
