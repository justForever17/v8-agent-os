"""Drive only the isolated Engine and separately installed synthetic Android fixture."""
import argparse
import json
from pathlib import Path
import ssl
import subprocess
import time
import uuid
import httpx

TERMINAL = {"succeeded", "failed", "rejected", "cancelled", "unknown_outcome"}
ANCHORS = ("observationId", "deviceId", "bootId", "controlSessionId", "resourceId", "appId", "windowId", "nodeMapRevision")
FIXTURE = "com.v8agentos.executorfixture"
TEST_APP = "expo.modules.v8executor.test"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("This test sends actions to the synthetic fixture; pass --live explicitly.")
    context = ssl.create_default_context(cafile=str(args.ca))
    client = httpx.Client(base_url="https://localhost:9533", verify=context, trust_env=False, timeout=10)
    evidence = {"platform": "physical_android", "target": FIXTURE, "checks": []}

    def api(path, body=None):
        response = client.get(path) if body is None else client.post(path, json=body)
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and result.get("ok") is False:
            raise AssertionError(result.get("code", "fixture_rejected"))
        return result

    def state():
        return api("/fixture/state")

    def terminal(command_id, timeout=12, require_device_receipt=True):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            command = api("/fixture/commands/" + command_id)
            if command and command["status"] in TERMINAL and (not require_device_receipt or (command.get("receipt") or {}).get("status") in TERMINAL):
                return command
            time.sleep(.08)
        raise AssertionError("no terminal receipt: " + command_id)

    def wait_started(command_id):
        until = time.monotonic() + 12
        while time.monotonic() < until:
            result = api("/fixture/commands/" + command_id)
            if result["status"] == "started":
                return result
            if result["status"] in TERMINAL:
                raise AssertionError("driver settled before fault injection; repeat this bounded timing fixture")
            time.sleep(.02)
        raise AssertionError("driver never started")

    def local(command, nonce=None):
        extras = ["--es", "fixtureProbeNonce", nonce] if nonce else []
        subprocess.run(["adb", "-s", args.serial, "shell", "am", "start", "-f", "0x10008000", "-n",
                        TEST_APP + "/expo.modules.v8executor.ExecutorTestActivity", "--es", "fixtureCommand", command, *extras],
                       check=True, capture_output=True, timeout=10)

    def probe(command="status"):
        nonce = uuid.uuid4().hex
        local(command, nonce)
        prefix = "probe_nonce=" + nonce + ";probe_enabled="
        until = time.monotonic() + 8
        while time.monotonic() < until:
            log = subprocess.run(["adb", "-s", args.serial, "logcat", "-d", "-s", "V8ExecutorFixture:I", "-v", "brief"],
                                 check=True, capture_output=True, text=True, timeout=10).stdout
            values = [line.split(prefix, 1)[1] for line in log.splitlines() if prefix in line]
            if values:
                assert values[-1] in {"true", "false"}, "invalid_fixture_probe"
                return values[-1] == "true"
            time.sleep(.08)
        raise AssertionError("fresh_native_probe_missing")

    def wait_online(device_id):
        until = time.monotonic() + 25
        while time.monotonic() < until:
            if any(d["deviceId"] == device_id and d["online"] for d in state()["devices"]):
                return
            time.sleep(.1)
        raise AssertionError("native control did not reconnect")

    def create(capability="android.observe", arguments=None, precondition=None):
        return api("/fixture/command", {"capability": capability, "arguments": arguments or {}, "precondition": precondition or {}})

    def observe():
        result = terminal(create()["commandId"])
        assert result["status"] == "succeeded", result
        observation = result["receipt"]["observation"]
        assert observation["resourceId"] == FIXTURE and observation["appId"] == FIXTURE
        return result, observation

    def node(observation, text):
        return next(n for n in observation["nodes"] if n["text"].upper() == text.upper())

    def count(observation):
        return int(next(n["text"] for n in observation["nodes"] if n["text"].startswith("Count: ")).split(": ")[1])

    def click(observation, text):
        return terminal(create("android.action", {"action": "click", "nodeId": node(observation, text)["nodeId"]},
                               {key: observation[key] for key in ANCHORS})["commandId"])

    def check(name, result, **detail):
        evidence["checks"].append({"name": name, "commandId": result.get("commandId"), "status": result.get("status"),
                                   "error": (result.get("receipt") or {}).get("error"), **detail})
        print(name + ": passed", flush=True)

    def reconcile_fixture(result):
        # Acknowledgement leaves the fixture outcome unknown. An unchanged
        # visible counter does not prove whether a no-op executed.
        assert result["command"]["resourceId"] == FIXTURE
        assert result["command"]["capability"] == "android.action"
        deadline = result["command"]["deadlineUnixMs"]
        while time.time() * 1000 <= deadline:
            time.sleep(.1)
        acknowledged = api("/fixture/reconcile/" + result["commandId"], {})
        assert acknowledged["status"] == "unknown_outcome" and acknowledged["reconciled"]
        return acknowledged

    try:
        ready_deadline = time.monotonic() + 25
        while len([device for device in state()["devices"] if device["online"]]) != 1:
            if time.monotonic() >= ready_deadline:
                raise AssertionError("Unlock the device and start the synthetic fixture control session locally")
            time.sleep(.2)
        baseline, observation = observe()
        initial_count = count(observation)
        check("fresh_tree_in_exact_fixture", baseline, nodeCount=len(observation["nodes"]))
        action = click(observation, "Increment counter")
        assert action["status"] == "succeeded" and action["receipt"]["driverAccepted"] is True
        assert count(action["receipt"]["observation"]) == initial_count + 1
        assert action["businessVerification"] == "unverified"
        check("real_node_click_changes_counter", action, before=initial_count, after=initial_count + 1)

        api("/fixture/replay/" + action["commandId"], {})
        time.sleep(.3)
        replay_observe, observation2 = observe()
        assert count(observation2) == initial_count + 1
        check("exact_replay_does_not_click_twice", replay_observe, count=count(observation2))
        noop = click(observation2, "No effect (driver can accept)")
        assert noop["status"] == "succeeded" and noop["receipt"]["driverAccepted"] is True
        assert count(noop["receipt"]["observation"]) == initial_count + 1
        assert noop["businessVerification"] == "unverified" and noop["receipt"]["businessVerification"] == "unverified"
        check("driver_true_is_not_business_verified", noop)

        for name, overrides, expected in [
            ("old_observation_rejected", {"precondition": {key: observation[key] for key in ANCHORS}}, "stale_observation"),
            ("old_epoch_rejected", {"leaseEpoch": 0}, "stale_lease"),
            ("old_grant_rejected", {"grantRevision": 0}, "stale_revision"),
            ("old_boot_rejected", {"bootId": "prior-process"}, "stale_control_session"),
            ("old_arm_rejected", {"controlSessionId": "prior-local-session"}, "stale_control_session"),
        ]:
            now = int(time.time() * 1000)
            fault = api("/fixture/raw-command", {"baseCommandId": action["commandId"], "overrides": {
                "issuedUnixMs": now, "deadlineUnixMs": now + 15000, "ttlMs": 15000, **overrides}})
            result = terminal(fault["commandId"])
            assert result["status"] == "rejected" and result["receipt"]["error"] == expected, result
            check(name, result)

        now = int(time.time() * 1000)
        expired = api("/fixture/raw-command", {"baseCommandId": action["commandId"], "overrides": {
            "issuedUnixMs": now - 20000, "deadlineUnixMs": now - 5000, "ttlMs": 15000}})
        expired_result = terminal(expired["commandId"])
        assert expired_result["status"] == "rejected" and expired_result["receipt"]["error"] == "expired"
        check("expired_mutation_never_runs", expired_result)

        before_reconnect, observation3 = observe()
        device_id = observation3["deviceId"]
        api("/fixture/disconnect/" + device_id, {})
        wait_online(device_id)
        after_reconnect, observation4 = observe()
        assert count(observation4) == initial_count + 1
        assert after_reconnect["command"]["leaseEpoch"] > before_reconnect["command"]["leaseEpoch"]
        check("reconnect_fences_previous_epoch", after_reconnect)
        api("/fixture/replay/" + action["commandId"], {})
        time.sleep(.3)
        after_old_replay, observation5 = observe()
        assert count(observation5) == initial_count + 1
        check("historical_receipt_query_after_reconnect_never_reexecutes", after_old_replay)

        # The no-op is intentionally side-effect-free while exercising the same
        # started driver/receipt boundary as a non-idempotent node click.
        pending_cancel = create("android.action", {"action": "click", "nodeId": node(observation5, "No effect (driver can accept)")["nodeId"]},
                                {key: observation5[key] for key in ANCHORS})
        wait_started(pending_cancel["commandId"])
        api("/fixture/cancel/" + pending_cancel["commandId"], {})
        cancelled = terminal(pending_cancel["commandId"])
        assert cancelled["status"] == "unknown_outcome" and cancelled["cancelRequested"], cancelled
        assert cancelled["receipt"]["status"] == "unknown_outcome" and cancelled["receipt"]["error"] == "cancel_requested"
        check("cancel_after_started_records_unknown_not_zero_execution", cancelled)
        _, after_cancel = observe()
        assert count(after_cancel) == initial_count + 1
        check("explicit_reconciliation_preserves_unknown_outcome", reconcile_fixture(cancelled))

        before_crash, crash_observation = observe()
        crash_command = create("android.action", {"action": "click", "nodeId": node(crash_observation, "No effect (driver can accept)")["nodeId"]},
                               {key: crash_observation[key] for key in ANCHORS})
        wait_started(crash_command["commandId"])
        subprocess.run(["adb", "-s", args.serial, "shell", "am", "force-stop", TEST_APP], check=True, capture_output=True, timeout=10)
        interrupted = terminal(crash_command["commandId"], require_device_receipt=False)
        assert interrupted["status"] == "unknown_outcome", interrupted
        local("resume")
        wait_online(device_id)
        reboot_result, reboot_observation = observe()
        assert reboot_observation["bootId"] != crash_observation["bootId"]
        assert reboot_observation["controlSessionId"] != crash_observation["controlSessionId"]
        check("process_restart_requires_fresh_boot_and_local_arm", reboot_result)
        api("/fixture/replay/" + crash_command["commandId"], {})
        time.sleep(.4)
        recovered = terminal(crash_command["commandId"])
        assert recovered["status"] == "unknown_outcome"
        assert recovered["receipt"]["status"] == "unknown_outcome" and recovered["receipt"]["error"] == "process_restarted", recovered
        _, post_replay_observation = observe()
        assert count(post_replay_observation) == initial_count + 1
        check("crashed_journal_preserves_unknown_receipt", recovered,
              limitation="The no-op counter cannot prove that a no-op was not executed again.")
        reconcile_fixture(recovered)

        # A second local app process activates its own fixture button. This is
        # independent of the remote driver and exercises the platform UI event
        # takeover path without injecting a touch into an unverified foreground app.
        subprocess.run(["adb", "-s", args.serial, "shell", "am", "start", "-f", "0x20000000", "-n",
                        FIXTURE + "/.FixtureActivity", "--es", "fixtureAction", "local_click"],
                       check=True, capture_output=True, timeout=10)
        time.sleep(.5)
        assert not next(d for d in state()["devices"] if d["deviceId"] == device_id)["online"]
        check("independent_local_ui_activation_preempts_remote_control", {"status": "stopped"})
        local("resume")
        wait_online(device_id)

        assert probe("stop") is False
        time.sleep(.5)
        stopped = next(d for d in state()["devices"] if d["deviceId"] == device_id)
        assert not stopped["online"]
        check("local_stop_closes_device_control", {"status": "stopped"})

        local("resume")
        wait_online(device_id)
        api("/fixture/revoke/" + device_id, {})
        revoke_deadline = time.monotonic() + 10
        while probe():
            assert time.monotonic() < revoke_deadline, "remote_revoke_did_not_disable_native_control"
            time.sleep(.1)
        check("revoked_credential_disables_local_reconnect", {"status": "stopped"})
        evidence["result"] = "passed"
    except Exception as error:
        evidence["result"] = "failed"
        evidence["failureType"] = type(error).__name__
        raise
    finally:
        try:
            assert probe("stop") is False, "cleanup_stop_failed"
            evidence["cleanup"] = {"status": "passed"}
        except Exception as error:
            evidence["cleanup"] = {"status": "failed", "errorType": type(error).__name__}
            evidence["result"] = "failed"
        finally:
            try:
                client.close()
            finally:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    assert evidence["result"] == "passed", "physical_checks_or_cleanup_failed"


if __name__ == "__main__":
    main()
