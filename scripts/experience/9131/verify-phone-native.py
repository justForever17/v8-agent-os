"""Independent UI oracles on a handed-off, synthetic Android release AVD.

Requires explicit --live, an emulator serial, and the reviewed APK hash. Never
reads credentials, private databases or logs, and never clears application data.
Use --only P01 then --only P04,P05,P10 for separately observable handoff phases.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

PACKAGE = "com.v8agentos.phone"
A1 = "Independent_A1_9131_UNSENT"
A2 = "Independent_A2_9131_UNSENT"
B1 = "Independent_B1_9131_UNSENT"


class NativeReview:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.report = {
            "candidate": args.candidate, "platform": "Android emulator release x86_64",
            "serial": args.serial, "physicalPhone": False,
            "fixture": f"{args.fixture_commit} phone-api-fixture.cjs on 22836",
            "realtimePackageHandoff": args.realtime_version,
            "checks": [], "limitations": [
                "UIAutomator observations are not native frame timings or physical-device evidence.",
                "Pairing, sessions and messages use public synthetic fixture data only.",
                "The APK-to-source relationship is the build owner's handoff; file and installed hashes are independently compared.",
                "Restart follows observed stable UI and completed navigation; it does not test termination during a pending SQLite write.",
            ],
        }

    def adb(self, *command):
        return subprocess.run(["adb", "-s", self.args.serial, *command], check=True,
                              capture_output=True, timeout=35).stdout

    def ui(self):
        self.adb("shell", "uiautomator", "dump", "/sdcard/experience-fixture-ui.xml")
        return ET.fromstring(self.adb("exec-out", "cat", "/sdcard/experience-fixture-ui.xml"))

    @staticmethod
    def visible(root):
        return [n.get("text") or n.get("content-desc") for n in root.iter("node")
                if n.get("text") or n.get("content-desc")]

    def wait(self, label=None, editor=False, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root = self.ui()
            if label != "Retry" and "Something went wrong" in self.visible(root):
                raise AssertionError(f"Native error boundary while waiting for {label or 'editor'}: {self.visible(root)}")
            found = [n for n in root.iter("node") if (
                n.get("class") == "android.widget.EditText" if editor
                else label in (n.get("text"), n.get("content-desc")))]
            if found:
                buttons = [n for n in found if n.get("clickable") == "true"]
                return (buttons or found)[-1]
        raise AssertionError(f"Missing {label or 'editor'}; visible={self.visible(root)}")

    def tap_node(self, node):
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds")))
        self.adb("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))

    def tap(self, label):
        self.tap_node(self.wait(label))

    def navigate(self, label):
        self.tap("导航")
        self.tap(label)

    def fill(self, value):
        assert value.isascii() and " " not in value, "ADB fixture typing accepts space-free ASCII only"
        self.tap_node(self.wait(editor=True))
        self.ui()  # Re-observe keyboard layout before replacing the editor.
        self.adb("shell", "input", "keycombination", "113", "29")
        self.adb("shell", "input", "text", shlex.quote(value))
        self.adb("shell", "input", "keyevent", "4")
        self.expect_draft(value)

    def expect_draft(self, value):
        actual = self.wait(editor=True).get("text")
        assert actual == value, {"expectedDraft": value, "actualDraft": actual}
        return actual

    def expect_authority_message(self, authority):
        self.wait(f"{authority} synthetic message")
        visible = self.visible(self.ui())
        other = "B" if authority == "A" else "A"
        assert f"{other} synthetic message" not in visible, visible
        return f"{authority} synthetic message"

    def choose(self, authority, session):
        self.navigate("工作区")
        self.tap(f"{authority} task {session}")

    def switch(self, authority):
        self.navigate("连接与设备")
        self.wait("已配对连接")
        root = self.ui()
        switches = [n for n in root.iter("node") if n.get("content-desc") == "切换"
                    and n.get("clickable") == "true"]
        if not switches:
            switches = [n for n in root.iter("node") if n.get("text") == "切换"]
        assert len(switches) == 1, {"expected": "exactly two profiles with one inactive switch", "visible": self.visible(root)}
        self.tap_node(switches[0])
        self.wait(editor=True)
        self.expect_authority_message(authority)

    def capture(self, name):
        (self.out / f"{name}.png").write_bytes(self.adb("exec-out", "screencap", "-p"))
        (self.out / f"{name}.xml").write_bytes(ET.tostring(self.ui(), encoding="utf-8"))

    def preflight(self):
        actual = hashlib.sha256(Path(self.args.apk).read_bytes()).hexdigest()
        assert actual == self.args.apk_sha256.lower(), {"actualFileHash": actual}
        package_path = self.adb("shell", "pm", "path", PACKAGE).decode().strip().removeprefix("package:")
        assert package_path.startswith("/data/app/") and package_path.endswith("/base.apk")
        installed = self.adb("shell", "sha256sum", package_path).decode().split()[0]
        assert installed == actual, {"fileHash": actual, "installedHash": installed}
        os_version = self.adb("shell", "getprop", "ro.build.version.release").decode().strip()
        abi = self.adb("shell", "getprop", "ro.product.cpu.abi").decode().strip()
        self.report.update(apkSha256=actual, installedApkSha256=installed, os=os_version, abi=abi)

    def p01(self):
        self.choose("A", 1)
        self.fill(A1)
        self.choose("A", 1)
        self.expect_draft(A1)
        self.choose("A", 2)
        self.fill(A2)
        self.choose("A", 1)
        self.expect_draft(A1)
        self.expect_authority_message("A")
        self.capture("P01-A1-retained")
        return {"sameSession": A1, "session2": A2, "returnedSession1": A1}

    def p04(self):
        self.navigate("连接与设备")
        self.tap("配对另一台设备")
        self.report["pairEntryVisible"] = self.visible(self.ui())
        self.tap("备用配对链接")
        link = "v8agentosphone://pair?admin=http://127.0.0.1:22836/B&code=synthetic-code&instance=fixture-B"
        self.fill(link)
        self.report["manualEntryVisible"] = self.visible(self.ui())
        self.tap("连接并进入 V8 OS")
        self.wait("导航")  # A new profile initially has no selected workspace/session.
        self.choose("B", 1)
        self.expect_authority_message("B")
        before = self.wait(editor=True).get("text")
        assert A1 not in before and A2 not in before, {"newProfileEditor": before}
        self.fill(B1)
        self.capture("P04-B1-isolated")
        self.switch("A")
        self.expect_draft(A1)
        self.choose("A", 2)
        self.expect_draft(A2)
        self.choose("A", 1)
        self.expect_draft(A1)
        return {"newBEditor": before, "B1": B1, "A1AfterB": A1, "A2AfterB": A2}

    def p05(self):
        observations = []
        for authority, draft in [("B", B1), ("A", A1), ("B", B1), ("A", A1)]:
            self.switch(authority)
            observations.append({"authority": authority, "draft": self.expect_draft(draft),
                                 "message": self.expect_authority_message(authority)})
        self.capture("P05-A1-after-ABABA")
        return {"collidingFixtureIds": ["session-1", "message-1", "fixture-owner"],
                "observations": observations, "notCovered": ["tombstone", "resource cache", "offline cache"]}

    def r04(self):
        # Separate recovery observation after P04 fails. It never turns P04 green.
        before = self.visible(self.ui())
        if "Retry" in before:
            self.tap("Retry")
        self.wait("导航")
        self.choose("B", 1)
        self.expect_authority_message("B")
        self.fill(B1)
        self.capture("R04-B-after-Retry")
        self.switch("A")
        self.expect_draft(A1)
        self.capture("R04-A-after-Retry-switch")
        return {"retryRecoveredB": True, "switchBackToA": A1}

    def p10(self):
        self.expect_draft(A1)
        self.adb("shell", "am", "force-stop", PACKAGE)
        self.adb("shell", "am", "start", "-n", f"{PACKAGE}/.MainActivity")
        self.expect_draft(A1)
        self.expect_authority_message("A")
        self.switch("B")
        self.expect_draft(B1)
        self.expect_authority_message("B")
        self.adb("shell", "am", "force-stop", PACKAGE)
        self.adb("shell", "am", "start", "-n", f"{PACKAGE}/.MainActivity")
        self.expect_draft(B1)
        self.expect_authority_message("B")
        self.switch("A")
        self.expect_draft(A1)
        self.choose("A", 2)
        self.expect_draft(A2)
        self.choose("A", 1)
        self.expect_draft(A1)
        self.capture("P10-native-restart-A-restored")
        return {"restartActiveA": A1, "restartActiveB": B1, "retainedA2": A2,
                "pairingReentered": False, "clearedData": False}

    def p10a(self):
        self.expect_draft(A1)
        self.expect_authority_message("A")
        self.adb("shell", "am", "force-stop", PACKAGE)
        self.adb("shell", "am", "start", "-n", f"{PACKAGE}/.MainActivity")
        self.expect_draft(A1)
        self.expect_authority_message("A")
        self.capture("P10A-one-native-restart")
        return {"restarts": 1, "activeAuthority": "A", "draft": A1,
                "pairingReentered": False, "clearedData": False}

    def run(self):
        self.preflight()
        try:
            for check in self.args.only.split(","):
                started = time.time()
                try:
                    evidence = getattr(self, check.lower())()
                    result = {"id": check, "status": "PASS", "evidence": evidence}
                except Exception as error:
                    result = {"id": check, "status": "FAIL", "error": str(error)}
                    self.capture(f"{check}-failure")
                    self.report["checks"].append(result)
                    remaining = self.args.only.split(",")[self.args.only.split(",").index(check) + 1:]
                    self.report["checks"].extend({"id": pending, "status": "NOT_RUN", "reason": f"Stopped after {check}"} for pending in remaining)
                    raise
                result["automationElapsedSeconds"] = round(time.time() - started, 2)
                self.report["checks"].append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            filename = "native-" + self.args.only.replace(",", "-") + ".json"
            (self.out / filename).write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--fixture-commit", default="82ed1c88f04881dcde5932bf39bd70cb0497c3e2")
    parser.add_argument("--realtime-version", default="not independently supplied")
    parser.add_argument("--apk", required=True)
    parser.add_argument("--apk-sha256", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="P01,P04,P05,P10")
    args = parser.parse_args()
    if not re.fullmatch(r"emulator-\d+", args.serial):
        parser.error("Only the explicitly handed-off disposable emulator is permitted")
    NativeReview(args).run()
