"""Opt-in physical screenshot/gesture checks against only the synthetic target."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import ssl
import subprocess
import time
import uuid
import httpx

FIXTURE = "com.v8agentos.executorfixture"
TEST_APP = "expo.modules.v8executor.test"
TERMINAL = {"succeeded", "failed", "rejected", "cancelled", "unknown_outcome", "expired"}


def parse_probe(log, nonce, command_id=None):
    prefix = "probe_nonce=" + nonce + ";"
    enabled, receipt = None, None
    for line in log.splitlines():
        if prefix not in line:
            continue
        value = line.split(prefix, 1)[1]
        if value in {"probe_enabled=true", "probe_enabled=false"}:
            enabled = value.endswith("=true")
        elif value.startswith("probe_receipt=") and command_id:
            candidate = json.loads(value.removeprefix("probe_receipt="))
            if candidate.get("commandId") == command_id:
                receipt = candidate
    if enabled is None or (command_id and receipt is None):
        return None
    return {"enabled": enabled, "receipt": receipt}


class Bench:
    def __init__(self, args):
        self.args = args
        self.context = ssl.create_default_context(cafile=str(args.ca))
        self.client = httpx.Client(base_url="https://localhost:9533", verify=self.context, trust_env=False, timeout=10)
        self.results = []
        self.device_id = None
        self.canvas_nonce = None

    def api(self, path, data=None):
        response = self.client.get(path) if data is None else self.client.post(path, json=data)
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and result.get("ok") is False:
            raise AssertionError(result.get("code", "fixture_rejected"))
        return result

    def adb(self, *args):
        return subprocess.check_output(["adb", "-s", self.args.serial, *args], encoding="utf-8", errors="replace", timeout=15)

    def scene(self, action):
        extras = []
        if action == "canvas":
            self.canvas_nonce = uuid.uuid4().hex
            extras = ["--es", "fixtureSceneNonce", self.canvas_nonce]
        self.adb("shell", "am", "start", "-f", "0x20000000", "-n", FIXTURE + "/.FixtureActivity", "--es", "fixtureAction", action, *extras)
        time.sleep(.8)

    def local_intent(self, action, *extras):
        self.adb("shell", "am", "start", "-f", "0x10008000", "-n", TEST_APP + "/expo.modules.v8executor.ExecutorTestActivity",
                 "--es", "fixtureCommand", action, *extras)

    def probe(self, action="status", command_id=None):
        nonce = uuid.uuid4().hex
        self.local_intent(action, "--es", "fixtureProbeNonce", nonce)
        until = time.monotonic() + 8
        while time.monotonic() < until:
            result = parse_probe(self.adb("logcat", "-d", "-s", "V8ExecutorFixture:I", "-v", "brief"), nonce, command_id)
            if result is not None:
                return result
            time.sleep(.08)
        raise AssertionError("fresh_native_probe_missing")

    def local(self, action):
        self.local_intent(action)
        until = time.monotonic() + 18
        while time.monotonic() < until:
            devices = [d for d in self.api("/fixture/state")["devices"] if d["online"]]
            if len(devices) == 1 and (self.device_id is None or devices[0]["deviceId"] == self.device_id):
                self.device_id = devices[0]["deviceId"]
                time.sleep(.6)
                return
            time.sleep(.15)
        raise AssertionError("local_fixture_resume_failed")

    def current(self, command_id):
        return self.api("/fixture/commands/" + command_id)

    def execute(self, capability, arguments=None, precondition=None):
        queued = self.api("/fixture/command", {"deviceId": self.device_id, "capability": capability, "arguments": arguments or {}, "precondition": precondition or {}})
        assert queued.get("commandId"), queued
        until = time.monotonic() + 20
        while time.monotonic() < until:
            result = self.current(queued["commandId"])
            if result["status"] in TERMINAL and (result.get("receipt") or {}).get("status") in TERMINAL:
                assert result["command"]["resourceId"] == FIXTURE
                if self.device_id:
                    assert result["deviceId"] == self.device_id
                self.device_id = result["deviceId"]
                return result
            time.sleep(.08)
        raise AssertionError("device_terminal_receipt_missing")

    def record(self, name, result, **extra):
        item = {"name": name, "status": result["status"], "commandId": result.get("commandId"),
                "error": (result.get("receipt") or {}).get("error"), "businessVerification": result.get("businessVerification"), **extra}
        self.results.append(item)
        print(json.dumps(item, ensure_ascii=True), flush=True)

    def capture(self, name):
        result = self.execute("android.capture")
        if result["status"] == "failed" and result["receipt"].get("error") in {"window_changed", "capture_rate_limited"}:
            self.record(name + "_capture_invalidated_before_publication", result)
            time.sleep(.5)
            result = self.execute("android.capture")
        (self.args.output / (name + "-receipt.json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
        assert result["status"] == "succeeded" and result.get("mediaStatus") == "available", result
        text = self.api("/fixture/agent-view/" + result["commandId"])["content"]
        visible = json.loads(text.split("\nData: ", 1)[1])
        frame = result["receipt"]["observation"]["frame"]
        source = Path(visible["screenshotRef"]["filePath"])
        # Only this fixture's owned server image, never adb display screenshots.
        assert result["command"]["resourceId"] == FIXTURE and source.parent.name == "executor-media"
        raw = source.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == frame["sha256"]
        output = self.args.output / (name + ".jpg")
        output.write_bytes(raw)
        self.record(name, result, frameId=frame["frameId"], sha256=frame["sha256"], width=frame["width"], height=frame["height"],
                    image=str(output), agentVisibleChars=len(text), agentAnchors=visible["precondition"])
        return visible, output

    def canvas_point(self, image):
        from PIL import Image
        with Image.open(image) as source:
            pixels = source.convert("RGB")
            xs, ys = [], []
            for y in range(0, pixels.height, 3):
                for x in range(0, pixels.width, 3):
                    r, g, b = pixels.getpixel((x, y))
                    if b > 150 and 50 < g < 160 and r < 80:
                        xs.append(x); ys.append(y)
            assert len(xs) > 1000, "synthetic_blue_target_not_visible"
            return (min(xs), min(ys), max(xs), max(ys))

    def counters(self):
        assert self.canvas_nonce, "canvas_scene_required"
        log = self.adb("logcat", "-d", "-s", "V8ExecutorTarget:I", "-v", "brief")
        matches = re.findall(r"canvas_scene=" + re.escape(self.canvas_nonce) + r";canvas_event=(\d+);canvas_taps=(\d+);canvas_swipes=(\d+)", log)
        assert matches, "target_effect_log_missing"
        return tuple(map(int, matches[-1]))

    def effect(self, before, taps, swipes):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            after = self.counters()
            if after[0] > before[0]:
                assert after == (before[0] + 1, before[1] + taps, before[2] + swipes), "unexpected_fixture_effect"
                return after
            time.sleep(.08)
        raise AssertionError("new_fixture_effect_missing")

    def cleanup(self):
        results = []
        def stop():
            assert self.probe("stop")["enabled"] is False, "cleanup_stop_failed"
        for name, action in (
            ("stop_before_cleanup", stop),
            ("release_media_barrier", lambda: self.api("/fixture/media-release", {})),
            ("remove_synthetic_overlay", lambda: self.local_intent("overlay_clear")),
            ("clear_fixture_secure_flag", lambda: self.scene("secure_off")),
            ("restore_fixture_portrait", lambda: self.scene("portrait")),
            ("stop_after_cleanup", stop),
        ):
            try:
                action()
                results.append({"name": name, "status": "passed"})
            except Exception as error:
                results.append({"name": name, "status": "failed", "errorType": type(error).__name__})
        self.client.close()
        return results

    def run(self):
        self.capture("real_window_capture")
        self.scene("canvas")
        baseline = self.counters()
        assert baseline == (0, 0, 0), "new_canvas_scene_not_zero"
        visible, image = self.capture("canvas_before")
        left, top, right, bottom = self.canvas_point(image)
        result = self.execute("android.action", {"action": "tap", "x": (left + right)//2, "y": (top + bottom)//2}, visible["precondition"])
        assert result["status"] == "succeeded", result
        after_tap = self.effect(baseline, 1, 0)
        self.record("current_frame_tap_changes_actual_canvas", result, sceneNonce=self.canvas_nonce, eventSeq=after_tap[0], counters=list(after_tap[1:]))
        visible, image = self.capture("canvas_after_tap")
        left, top, right, bottom = self.canvas_point(image)
        result = self.execute("android.action", {"action": "swipe", "x": left + (right-left)//4, "y": (top+bottom)//2,
            "endX": right-(right-left)//4, "endY": (top+bottom)//2, "durationMs": 500}, visible["precondition"])
        if result["status"] == "rejected" and result["receipt"].get("error") == "stale_frame":
            self.record("in_flight_scene_event_rejects_swipe_before_dispatch", result)
            visible, image = self.capture("swipe_fresh_decision")
            left, top, right, bottom = self.canvas_point(image)
            result = self.execute("android.action", {"action": "swipe", "x": left + (right-left)//4, "y": (top+bottom)//2,
                "endX": right-(right-left)//4, "endY": (top+bottom)//2, "durationMs": 500}, visible["precondition"])
        assert result["status"] == "succeeded", result
        after_swipe = self.effect(after_tap, 0, 1)
        self.record("current_frame_swipe_changes_actual_canvas", result, sceneNonce=self.canvas_nonce, eventSeq=after_swipe[0], counters=list(after_swipe[1:]))
        self.capture("canvas_after_swipe")
        self.local("overlay_on")
        visible, image = self.capture("window_under_synthetic_overlay")
        from PIL import Image
        with Image.open(image) as bitmap:
            assert sum(1 for r, g, b in bitmap.convert("RGB").getdata() if r > 190 and g < 70 and b < 70) == 0, "overlay_pixels_leaked_into_window_capture"
        geometry = visible["precondition"]
        viewport = geometry["viewport"]
        windows = self.adb("shell", "dumpsys", "accessibility")
        overlay_lines = [line for line in windows.splitlines() if "AccessibilityWindowInfo[" in line and "TYPE_ACCESSIBILITY_OVERLAY" in line]
        assert len(overlay_lines) == 1, "synthetic_overlay_window_not_unique"
        match = re.search(r"bounds=Rect\((\d+), (\d+) - (\d+), (\d+)\)", overlay_lines[0])
        assert match, "synthetic_overlay_geometry_unavailable"
        overlay_left, overlay_top, overlay_right, overlay_bottom = map(int, match.groups())
        assert overlay_right-overlay_left == 160 and overlay_bottom-overlay_top == 160
        overlay_y = (overlay_top + overlay_bottom)//2
        def frame_point(x, y):
            return int((x - viewport["left"]) * geometry["width"] / viewport["width"]), int((y - viewport["top"]) * geometry["height"] / viewport["height"])
        x, y = frame_point((overlay_left+overlay_right)//2, overlay_y)
        result = self.execute("android.action", {"action": "tap", "x": x, "y": y}, geometry)
        assert result["status"] == "rejected" and result["receipt"]["error"] == "coordinate_in_obstructed_region", result
        assert self.counters() == after_swipe
        self.record("point_inside_overlay_cannot_tap_underlying_app", result)
        visible, _ = self.capture("overlay_before_crossing_swipe")
        geometry = visible["precondition"]
        viewport = geometry["viewport"]
        x, y = frame_point(overlay_left-100, overlay_y); end_x, end_y = frame_point(overlay_right+100, overlay_y)
        result = self.execute("android.action", {"action": "swipe", "x": x, "y": y, "endX": end_x, "endY": end_y, "durationMs": 500}, visible["precondition"])
        assert result["status"] == "rejected" and result["receipt"]["error"] == "coordinate_in_obstructed_region", result
        assert self.counters() == after_swipe
        self.record("swipe_clear_endpoints_cannot_cross_overlay", result)
        self.local("overlay_off")
        self.scene("secure_on")
        result = self.execute("android.capture")
        assert result["status"] == "failed" and result["receipt"]["error"] == "secure_window" and "screenshotRef" not in result, result
        self.record("flag_secure_cannot_publish_pixels", result)
        self.scene("secure_off")
        visible, _ = self.capture("before_rotation")
        self.scene("landscape")
        result = self.execute("android.action", {"action": "tap", "x": 400, "y": 400}, visible["precondition"])
        assert result["status"] == "rejected" and result["receipt"]["error"] in {"stale_frame", "frame_required", "window_changed"}, result
        self.record("rotation_rejects_prior_frame", result)
        rotated, _ = self.capture("after_rotation")
        assert rotated["precondition"]["rotation"] != visible["precondition"]["rotation"]
        self.scene("portrait")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live: parser.error("--live required before contacting the phone")
    args.output.mkdir(parents=True, exist_ok=True)
    bench = Bench(args)
    result = "failed"
    try:
        bench.run()
        result = "passed"
    finally:
        cleanup = []
        try:
            cleanup = bench.cleanup()
        finally:
            if not cleanup or any(item["status"] != "passed" for item in cleanup):
                result = "failed"
            (args.output / "report.json").write_text(json.dumps({"layer": "physical_android", "result": result, "checks": bench.results, "cleanup": cleanup}, indent=2), encoding="utf-8")
    assert result == "passed", "physical_checks_or_cleanup_failed"


if __name__ == "__main__":
    main()
