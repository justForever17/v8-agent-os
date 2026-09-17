"""Continue the physical no-op crash, then test media Stop and revocation."""
import argparse
import importlib.util
import json
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--crash-receipt", type=Path, required=True)
    args = parser.parse_args()
    if not args.live: parser.error("--live required before device actions")
    args.output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("fixture_media", Path(__file__).with_name("run-executor-media-live.py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    b = module.Bench(args)
    result = "failed"

    def wait_barrier(barrier_id, command_id, *, finished=False):
        until = time.monotonic() + 10
        while time.monotonic() < until:
            barrier = b.api("/fixture/media-barrier")
            assert barrier["barrierId"] == barrier_id, "media_barrier_replaced"
            if barrier["arrived"]:
                assert barrier["commandId"] == command_id, "media_barrier_wrong_command"
                if not finished or (barrier["finished"] and barrier["mediaState"] in {"deleted", "gone"} and not barrier["bytesRemain"]):
                    return barrier
            time.sleep(.05)
        raise AssertionError("capture_barrier_or_cleanup_missing")

    def held_capture():
        barrier = b.api("/fixture/media-hold", {})
        queued = b.api("/fixture/command", {"deviceId": b.device_id, "capability": "android.capture"})
        assert queued.get("commandId"), queued
        wait_barrier(barrier["barrierId"], queued["commandId"])
        return barrier["barrierId"], queued["commandId"]

    def release_and_verify(barrier_id, command_id, native_receipt):
        assert native_receipt["commandId"] == command_id and native_receipt["status"] == "unknown_outcome"
        b.api("/fixture/media-release", {})
        barrier = wait_barrier(barrier_id, command_id, finished=True)
        until = time.monotonic() + 10
        while time.monotonic() < until:
            stopped = b.current(command_id)
            assert "screenshotRef" not in stopped and stopped["status"] != "succeeded"
            if stopped["status"] == "unknown_outcome":
                break
            time.sleep(.05)
        assert stopped["status"] == "unknown_outcome"
        # New nonce after the held upload has actually settled: an earlier Stop
        # probe or a server-side disconnect alone cannot satisfy this check.
        probe = b.probe(command_id=command_id)
        assert probe["enabled"] is False and probe["receipt"] == native_receipt
        assert "screenshotRef" not in b.current(command_id)
        return stopped, barrier

    try:
        old = json.loads(args.crash_receipt.read_text(encoding="utf-8"))
        assert old["command"]["resourceId"] == module.FIXTURE and old["command"]["capability"] == "android.action"
        b.device_id = old["deviceId"]
        recovered = b.current(old["commandId"])
        assert recovered["status"] == "unknown_outcome" and recovered["receipt"]["status"] == "unknown_outcome" and recovered["receipt"]["error"] == "process_restarted"
        observed = b.execute("android.observe")
        observation = observed["receipt"]["observation"]
        assert observation["bootId"] != old["command"]["bootId"] and observation["controlSessionId"] != old["command"]["controlSessionId"]
        b.record("explicit_rearm_uses_new_boot_and_control_session", observed)
        def counter(item):
            return next(n["text"] for n in item["receipt"]["observation"]["nodes"] if n["text"].startswith("Count: "))
        before = counter(observed)
        b.api("/fixture/replay/" + old["commandId"], {})
        time.sleep(.4)
        assert b.current(old["commandId"])["receipt"]["receiptSeq"] == recovered["receipt"]["receiptSeq"]
        after = b.execute("android.observe")
        assert counter(after) == before
        b.record("crashed_journal_preserves_unknown_receipt", recovered, before=before, after=counter(after),
                 limitation="The no-op counter cannot prove that a no-op was not executed again.")
        deadline = recovered["command"]["deadlineUnixMs"]
        assert deadline - time.time() * 1000 <= 30000, "fixture_deadline_out_of_bounds"
        while time.time() * 1000 < deadline:
            time.sleep(.1)
        acknowledged = b.api("/fixture/reconcile/" + old["commandId"], {})
        assert acknowledged["reconciled"] and acknowledged["status"] == "unknown_outcome"
        b.record("reconciliation_does_not_manufacture_success", acknowledged)

        barrier_id, command_id = held_capture()
        probe = b.probe("stop", command_id)
        assert probe["enabled"] is False and probe["receipt"]["error"] == "local_stop"
        stopped, barrier = release_and_verify(barrier_id, command_id, probe["receipt"])
        b.record("local_stop_during_actual_upload_prevents_publication", stopped,
                 nativeReceipt=probe["receipt"], uploadFinished=barrier["finished"], bytesRemain=barrier["bytesRemain"])
        b.local("resume")
        fresh, _ = b.capture("post_stop_new_capture")
        assert fresh["precondition"]["controlSessionId"] != stopped["command"]["controlSessionId"]

        device = fresh["precondition"]["deviceId"]
        b.scene("local_click")
        time.sleep(.3)
        assert not next(d for d in b.api("/fixture/state")["devices"] if d["deviceId"] == device)["online"]
        b.record("independent_local_fixture_input_preempts_remote_control", {"status": "stopped"})
        b.local("resume")
        barrier_id, command_id = held_capture()
        b.api("/fixture/revoke/" + device, {})
        until = time.monotonic() + 8
        while True:
            probe = b.probe(command_id=command_id)
            if probe["enabled"] is False:
                break
            assert time.monotonic() < until, "remote_revoke_did_not_disable_native_control"
            time.sleep(.1)
        stopped, barrier = release_and_verify(barrier_id, command_id, probe["receipt"])
        b.record("remote_revocation_during_upload_disables_native_control", stopped,
                 nativeReceipt=probe["receipt"], uploadFinished=barrier["finished"], bytesRemain=barrier["bytesRemain"])
        result = "passed"
    finally:
        cleanup = []
        try:
            cleanup = b.cleanup()
        finally:
            if not cleanup or any(item["status"] != "passed" for item in cleanup):
                result = "failed"
            (args.output / "report.json").write_text(json.dumps({"layer": "physical_android", "result": result, "checks": b.results, "cleanup": cleanup}, indent=2), encoding="utf-8")
    assert result == "passed", "physical_checks_or_cleanup_failed"


if __name__ == "__main__": main()
