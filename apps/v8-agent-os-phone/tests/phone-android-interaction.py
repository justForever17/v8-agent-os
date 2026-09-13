"""Run only against a disposable emulator and the synthetic local fixture.

python phone-android-interaction.py --live --serial emulator-5586 --output <directory>
Requires the release APK installed and profile A paired with phone-api-fixture.cjs.
Reads only the synthetic emulator UI and this app's memory counts, never credentials.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true", required=True)
parser.add_argument("--serial", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
if not args.serial.startswith("emulator-"):
    parser.error("This fixture harness only accepts an isolated emulator.")
sys.stdout.reconfigure(encoding="utf-8")
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)

def adb(*command):
    return subprocess.run(["adb", "-s", args.serial, *command], check=True, capture_output=True, timeout=30).stdout

def ui():
    adb("shell", "uiautomator", "dump", "/sdcard/phone-fixture-ui.xml")
    return ET.fromstring(adb("exec-out", "cat", "/sdcard/phone-fixture-ui.xml"))

def wait(label=None, editor=False, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root = ui()
        matches = [node for node in root.iter("node") if (
            node.get("class") == "android.widget.EditText" if editor else label in (node.get("text"), node.get("content-desc"))
        )]
        if matches:
            return matches[-1]
    texts = [node.get("text") or node.get("content-desc") for node in root.iter("node") if node.get("text") or node.get("content-desc")]
    raise AssertionError(f"Missing {label or 'editor'}; visible: {texts}")

def tap(label):
    node = wait(label)
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds")))
    adb("shell", "input", "tap", str((x1+x2)//2), str((y1+y2)//2))

def navigate(label):
    tap("导航")
    tap(label)

def type_text(value):
    node = wait(editor=True)
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds")))
    adb("shell", "input", "tap", str((x1+x2)//2), str((y1+y2)//2))
    ui()  # The keyboard must be laid out before key events and later taps.
    adb("shell", "input", "keycombination", "113", "29")
    adb("shell", "input", "text", value)
    adb("shell", "input", "keyevent", "4")
    assert wait(editor=True).get("text") == value

navigate("工作区")
tap("A task 1")
type_text("Native_A_draft")
navigate("工作区")
tap("A task 1")
assert wait(editor=True).get("text") == "Native_A_draft"
navigate("工作区")
tap("A task 2")
type_text("Native_A2_draft")
navigate("工作区")
tap("A task 1")
assert wait(editor=True).get("text") == "Native_A_draft"

for cycle in range(20):
    navigate("连接与设备")
    wait("已配对连接")
    tap("V8 Agent OS")
    assert wait(editor=True).get("text") == "Native_A_draft"
    if cycle % 5 == 4:
        print(f"Native navigation cycles: {cycle+1}", flush=True)

adb("shell", "am", "force-stop", "com.v8agentos.phone")
adb("shell", "am", "start", "-n", "com.v8agentos.phone/.MainActivity")
assert wait(editor=True, timeout=30).get("text") == "Native_A_draft"
(output / "phone-android-chat.png").write_bytes(adb("exec-out", "screencap", "-p"))
navigate("连接与设备")
tap("当前连接的 Supervisor")
wait("Supervisor 000")
(output / "phone-android-peers.png").write_bytes(adb("exec-out", "screencap", "-p"))
tap("V8 Agent OS")
assert wait(editor=True).get("text") == "Native_A_draft"
with urllib.request.urlopen("http://127.0.0.1:22836/fixture/metrics") as response:
    metrics = json.load(response)
assert metrics["activeStreams"] <= 2, metrics
report = {"platform": "Android emulator release x86_64", "serial": args.serial, "physicalPhone": False,
          "os": adb("shell", "getprop", "ro.build.version.release").decode().strip(),
          "checks": ["native manual pairing (preceding UI run)", "same session draft", "session A1-A2-A1", "20 connect/chat cycles", "process restart retains SQLite draft and SecureStore pairing", "100 synthetic peers, half offline", "at most 2 active streams at the final observation"],
          "metrics": metrics, "limitations": "No physical-device frame, thermal, battery, cellular or iOS evidence. UIAutomator timings are not native rendering measurements. Fixture maximumStreams is lifetime-wide and may include other browser contexts; it is not this run's isolated peak."}
(output / "phone-android-interaction.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
(output / "phone-android-memory.txt").write_bytes(adb("shell", "dumpsys", "meminfo", "com.v8agentos.phone"))
print(json.dumps(report, ensure_ascii=False), flush=True)
