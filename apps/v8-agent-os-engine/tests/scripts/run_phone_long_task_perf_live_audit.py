from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[1]
PHONE_ROOT = REPO_ROOT / "apps" / "v8-agent-os-phone"
SESSION_REALTIME_ROOT = REPO_ROOT / "packages" / "session-realtime"
DEFAULT_ADMIN_URL = "http://127.0.0.1:9528"
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
DEFAULT_PACKAGE = "com.v8agentos.phone"
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))
DEFAULT_PHONE_BUILD_ROOT = Path(
    os.environ.get("V8_PHONE_APK_BUILD_ROOT")
    or ("E:/v8p" if os.name == "nt" and Path("E:/").exists() else str(Path(os.environ.get("TEMP", "/tmp")) / "v8p"))
)

TOKEN_RE = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._\-]+|((?:password|api[_-]?key|token|cookie|authorization)[\"'\s:=]+)[^\"'\s,;]+"
)
PHONE_PERF_RE = re.compile(r"V8_PHONE_PERF\s+({.*})")


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: int = 0


@dataclass
class Finding:
    severity: str
    category: str
    summary: str
    evidence: str
    recommendation: str


@dataclass
class AuditState:
    report_dir: Path
    findings: list[Finding] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    raw_artifacts: dict[str, str] = field(default_factory=dict)

    def add_finding(self, severity: str, category: str, summary: str, evidence: str, recommendation: str) -> None:
        self.findings.append(Finding(severity, category, summary, evidence, recommendation))


def redact(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = TOKEN_RE.sub(lambda match: f"{match.group(1) or match.group(2)}[REDACTED]", text)
    for raw_path, replacement in (
        (Path.home(), "~"),
        (REPO_ROOT, "<REPO_ROOT>"),
        (ENGINE_ROOT, "<ENGINE_ROOT>"),
        (PHONE_ROOT, "<PHONE_ROOT>"),
    ):
        raw = str(raw_path)
        text = text.replace(raw, replacement).replace(raw.replace("\\", "\\\\"), replacement)
    return text


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> CommandResult:
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        return CommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            elapsed_ms=round((time.time() - started) * 1000),
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            returncode=124,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=((exc.stderr or "") if isinstance(exc.stderr, str) else "") + f"\nTIMEOUT after {timeout}s",
            elapsed_ms=round((time.time() - started) * 1000),
        )
    except FileNotFoundError as exc:
        return CommandResult(
            command=command,
            returncode=127,
            stderr=f"COMMAND_NOT_FOUND: {exc}",
            elapsed_ms=round((time.time() - started) * 1000),
        )


def record_command(state: AuditState, result: CommandResult) -> None:
    state.commands.append(
        {
            "command": result.command,
            "returncode": result.returncode,
            "elapsedMs": result.elapsed_ms,
            "stdoutPreview": redact(result.stdout[-4000:]),
            "stderrPreview": redact(result.stderr[-4000:]),
        }
    )


def which(command: str) -> str | None:
    resolved = shutil.which(command)
    if resolved:
        return resolved
    return None


def resolve_adb() -> str | None:
    for candidate in (
        os.environ.get("ADB"),
        str(Path(os.environ.get("ANDROID_HOME", "")) / "platform-tools" / "adb.exe") if os.environ.get("ANDROID_HOME") else None,
        str(Path(os.environ.get("ANDROID_SDK_ROOT", "")) / "platform-tools" / "adb.exe") if os.environ.get("ANDROID_SDK_ROOT") else None,
        which("adb"),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return which("adb")


def resolve_emulator() -> str | None:
    for candidate in (
        os.environ.get("ANDROID_EMULATOR"),
        str(Path(os.environ.get("ANDROID_HOME", "")) / "emulator" / "emulator.exe") if os.environ.get("ANDROID_HOME") else None,
        str(Path(os.environ.get("ANDROID_SDK_ROOT", "")) / "emulator" / "emulator.exe") if os.environ.get("ANDROID_SDK_ROOT") else None,
        which("emulator"),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return which("emulator")


def parse_adb_devices(raw: str) -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("list of devices"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1]})
    return devices


def parse_gfxinfo(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    total_match = re.search(r"Total frames rendered:\s*(\d+)", raw)
    janky_match = re.search(r"Janky frames:\s*(\d+)\s*\(([\d.]+)%\)", raw)
    p90_match = re.search(r"90th percentile:\s*(\d+)ms", raw)
    p95_match = re.search(r"95th percentile:\s*(\d+)ms", raw)
    p99_match = re.search(r"99th percentile:\s*(\d+)ms", raw)
    if total_match:
        result["totalFrames"] = int(total_match.group(1))
    if janky_match:
        result["jankyFrames"] = int(janky_match.group(1))
        result["jankyPercent"] = float(janky_match.group(2))
    if p90_match:
        result["p90Ms"] = int(p90_match.group(1))
    if p95_match:
        result["p95Ms"] = int(p95_match.group(1))
    if p99_match:
        result["p99Ms"] = int(p99_match.group(1))
    return result


def parse_meminfo(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    total_pss = re.search(r"TOTAL\s+(\d+)\s+", raw)
    total_ram = re.search(r"Total RAM:\s*([\d,]+)K", raw)
    native_heap = re.search(r"Native Heap\s+(\d+)\s+", raw)
    dalvik_heap = re.search(r"Dalvik Heap\s+(\d+)\s+", raw)
    if total_pss:
        result["totalPssKb"] = int(total_pss.group(1))
    if total_ram:
        result["totalRamKb"] = int(total_ram.group(1).replace(",", ""))
    if native_heap:
        result["nativeHeapPssKb"] = int(native_heap.group(1))
    if dalvik_heap:
        result["dalvikHeapPssKb"] = int(dalvik_heap.group(1))
    return result


def parse_phone_perf_logcat(raw: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for line in raw.splitlines():
        match = PHONE_PERF_RE.search(line)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            samples.append(payload)
    return samples


def percentile(values: list[float], percentile_value: float) -> float:
    filtered = sorted(value for value in values if isinstance(value, (int, float)))
    if not filtered:
        return 0.0
    index = min(len(filtered) - 1, max(0, int((percentile_value / 100) * len(filtered) + 0.999) - 1))
    return float(filtered[index])


def summarize_phone_perf(samples: list[dict[str, Any]]) -> dict[str, Any]:
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        stage = str(sample.get("stage") or "unknown")
        by_stage.setdefault(stage, []).append(sample)
    stages = []
    for stage, stage_samples in by_stage.items():
        elapsed = [float(sample.get("elapsedMs")) for sample in stage_samples if isinstance(sample.get("elapsedMs"), (int, float))]
        payloads = [float(sample.get("payloadBytes")) for sample in stage_samples if isinstance(sample.get("payloadBytes"), (int, float))]
        stages.append(
            {
                "stage": stage,
                "count": len(stage_samples),
                "p50Ms": round(percentile(elapsed, 50), 1),
                "p95Ms": round(percentile(elapsed, 95), 1),
                "payloadP95Bytes": round(percentile(payloads, 95), 1),
            }
        )
    return {
        "sampleCount": len(samples),
        "stages": sorted(stages, key=lambda item: (-float(item["p95Ms"]), -int(item["count"]))),
    }


def json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 10,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw) if raw.strip() else {"error": raw}
        except json.JSONDecodeError:
            return exc.code, {"error": raw}
    except Exception as exc:  # noqa: BLE001
        return 599, {"error": f"{type(exc).__name__}: {exc}"}


def check_engine_health(engine_url: str) -> tuple[bool, str]:
    for path in ("/health", "/api/health"):
        status, payload = json_request(f"{engine_url.rstrip('/')}{path}", timeout=5)
        if status < 400:
            return True, f"{path} ok"
        if status != 404:
            return False, f"{path} failed: status={status}, payload={redact(payload)}"
    return False, "no known Engine health endpoint responded"


def check_admin_health(admin_url: str) -> tuple[bool, str]:
    status, payload = json_request(f"{admin_url.rstrip('/')}/api/client/perf", timeout=5)
    if status in (200, 401, 403):
        return True, f"/api/client/perf reachable with status={status}"
    return False, f"/api/client/perf failed: status={status}, payload={redact(payload)}"


def admin_login(admin_url: str, email: str, password: str) -> tuple[str | None, str | None]:
    status, payload = json_request(
        f"{admin_url.rstrip('/')}/api/client/auth/login",
        method="POST",
        payload={"login": email, "password": password, "deviceName": "phone-long-task-perf-audit"},
        timeout=15,
    )
    if status >= 400:
        return None, f"admin_login_failed:{status}:{payload.get('error') or payload}"
    token = str(payload.get("accessToken") or "").strip()
    return (token or None), None if token else "admin_login_missing_access_token"


def create_audit_session(admin_url: str, token: str) -> tuple[str | None, str | None]:
    title = f"Phone 长任务卡顿 Live Audit {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    status, payload = json_request(
        f"{admin_url.rstrip('/')}/api/client/conversations",
        method="POST",
        token=token,
        payload={"title": title, "scopeMode": "explicit"},
        timeout=20,
    )
    if status >= 400:
        return None, f"create_session_failed:{status}:{payload.get('error') or payload}"
    session_id = str(payload.get("sessionId") or payload.get("id") or "").strip()
    return (session_id or None), None if session_id else "create_session_missing_id"


def list_conversations(admin_url: str, token: str) -> tuple[list[dict[str, Any]], str | None]:
    status, payload = json_request(
        f"{admin_url.rstrip('/')}/api/client/conversations",
        token=token,
        timeout=15,
    )
    if status >= 400:
        return [], f"list_conversations_failed:{status}:{payload.get('error') or payload}"
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        raw_items = payload.get("conversations") or payload.get("items") or payload.get("sessions") or []
        items = raw_items if isinstance(raw_items, list) else []
    else:
        items = []
    return [item for item in items if isinstance(item, dict)], None


def conversation_id_of(item: dict[str, Any]) -> str:
    return str(item.get("sessionId") or item.get("conversationId") or item.get("id") or "").strip()


def conversation_time_key(item: dict[str, Any]) -> str:
    for key in ("updatedAt", "lastMessageAt", "lastRunAt", "createdAt"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def pick_latest_conversation(items: list[dict[str, Any]], *, exclude_ids: set[str] | None = None) -> dict[str, Any] | None:
    candidates = [item for item in items if conversation_id_of(item)]
    if exclude_ids:
        new_candidates = [item for item in candidates if conversation_id_of(item) not in exclude_ids]
        if new_candidates:
            candidates = new_candidates
    if not candidates:
        return None
    return sorted(candidates, key=conversation_time_key, reverse=True)[0]


def submit_long_task(admin_url: str, token: str, session_id: str, prompt: str) -> tuple[str | None, str | None]:
    client_message_id = f"phone-perf-audit-{int(time.time() * 1000)}"
    status, payload = json_request(
        f"{admin_url.rstrip('/')}/api/client/chat-submit",
        method="POST",
        token=token,
        payload={
            "clientMessageId": client_message_id,
            "messages": [{"role": "user", "content": prompt}],
            "data": {
                "conversationId": session_id,
                "clientMessageId": client_message_id,
            },
        },
        timeout=45,
    )
    if status >= 400:
        return None, f"submit_failed:{status}:{payload.get('error') or payload}"
    run_id = str(payload.get("runId") or payload.get("run_id") or payload.get("id") or "").strip()
    return (run_id or None), None


def start_sse_collector(admin_url: str, token: str, session_id: str, output_path: Path, stop_event: threading.Event) -> dict[str, Any]:
    stats: dict[str, Any] = {"events": 0, "bytes": 0, "errors": []}

    def worker() -> None:
        request = urllib.request.Request(
            f"{admin_url.rstrip('/')}/api/client/realtime/sessions/{session_id}/stream",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response, output_path.open("wb") as handle:
                while not stop_event.is_set():
                    chunk = response.readline()
                    if not chunk:
                        break
                    stats["bytes"] = int(stats["bytes"]) + len(chunk)
                    if chunk.startswith(b"data:"):
                        stats["events"] = int(stats["events"]) + 1
                    handle.write(chunk)
        except Exception as exc:  # noqa: BLE001 - live diagnostic must record exact failure.
            stats["errors"].append(f"{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    stats["thread"] = thread
    return stats


def observe_manual_phone(args: argparse.Namespace, state: AuditState, token: str | None) -> None:
    state.metrics["manualPhone"] = {"enabled": True, "sessionDiscovery": bool(token)}
    stop_sse = threading.Event()
    sse_stats: dict[str, Any] | None = None
    session_id = (args.session_id or "").strip()
    before_ids: set[str] = set()
    if token:
        before_items, before_error = list_conversations(args.admin_url, token)
        if before_error:
            state.metrics["manualPhone"]["conversationListError"] = before_error
        before_ids = {conversation_id_of(item) for item in before_items if conversation_id_of(item)}
        if not session_id:
            state.metrics["manualPhone"]["initialConversationCount"] = len(before_ids)
    elif not session_id:
        state.add_finding(
            "P1",
            "phone_auth_required",
            "手动观察模式缺少 Admin token，无法自动发现 session/SSE",
            "未提供 --email/--password，且未传 --session-id。",
            "传入测试账号或 --session-id；Phone 端基础 logcat/gfx/meminfo 仍会采集。",
        )

    started = time.time()
    deadline = started + max(1, args.capture_seconds)
    while time.time() < deadline:
        if token and not session_id:
            items, error = list_conversations(args.admin_url, token)
            if error:
                state.metrics["manualPhone"]["lastConversationListError"] = error
            else:
                latest = pick_latest_conversation(items, exclude_ids=before_ids)
                if latest:
                    session_id = conversation_id_of(latest)
                    state.metrics["manualPhone"]["discoveredSession"] = {
                        "sessionId": session_id,
                        "title": latest.get("title") or latest.get("name"),
                        "updatedAt": latest.get("updatedAt") or latest.get("lastMessageAt"),
                    }
        if token and session_id and not sse_stats:
            state.metrics["sessionId"] = session_id
            sse_path = state.report_dir / "admin_sse_stream.txt"
            sse_stats = start_sse_collector(args.admin_url, token, session_id, sse_path, stop_sse)
            state.raw_artifacts["admin_sse_stream.txt"] = str(sse_path)
        time.sleep(min(5, max(1, deadline - time.time())))

    stop_sse.set()
    if sse_stats and isinstance(sse_stats.get("thread"), threading.Thread):
        sse_stats["thread"].join(timeout=5)
        sse_stats.pop("thread", None)
        state.metrics["sse"] = sse_stats
    if token:
        try:
            status, perf = json_request(f"{args.admin_url.rstrip('/')}/api/client/perf", token=token, timeout=15)
            state.metrics["adminPerf"] = perf if status < 400 else {"error": perf, "status": status}
        except Exception as exc:  # noqa: BLE001
            state.metrics["adminPerf"] = {"error": f"{type(exc).__name__}: {exc}"}


def copy_minimal_phone_build_tree(target_root: Path) -> Path:
    phone_target = target_root / "apps" / "v8-agent-os-phone"
    realtime_target = target_root / "packages" / "session-realtime"
    shutil.copytree(
        PHONE_ROOT,
        phone_target,
        ignore=shutil.ignore_patterns("node_modules", ".expo", "android", "ios", "dist-android", "build"),
    )
    shutil.copytree(
        SESSION_REALTIME_ROOT,
        realtime_target,
        ignore=shutil.ignore_patterns("node_modules", "dist", "*.log"),
    )
    return phone_target


def build_and_install_local_prebuild(args: argparse.Namespace, state: AuditState, adb: str, serial: str) -> bool:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    build_root = DEFAULT_PHONE_BUILD_ROOT / timestamp
    build_root.parent.mkdir(parents=True, exist_ok=True)
    phone_build_root = copy_minimal_phone_build_tree(build_root)
    state.metrics["localPrebuildRoot"] = str(build_root)
    env = os.environ.copy()
    env["EXPO_PUBLIC_V8_PHONE_PERF_AUDIT"] = "1"
    env["EXPO_PUBLIC_V8_PHONE_PERF_AUDIT_POST"] = "0"
    env["ANDROID_SERIAL"] = serial
    if args.phone_perf_post:
        env["EXPO_PUBLIC_V8_PHONE_PERF_AUDIT_POST"] = "1"

    npm = which("npm") or "npm"
    npx = which("npx") or "npx"
    for command, timeout in (
        ([npm, "ci"], 900),
        ([npx, "expo", "prebuild", "--platform", "android"], 900),
    ):
        result = run_command(command, cwd=phone_build_root, env=env, timeout=timeout)
        record_command(state, result)
        if result.returncode != 0:
            state.add_finding(
                "P0",
                "apk_validation_gap",
                "本地预构建 APK 阶段失败",
                f"command={command}, returncode={result.returncode}",
                "先修复 Phone 本地 Android prebuild/installDebug 工具链，或用 --apk-mode existing-apk 提供已构建 APK 复验。",
            )
            return False

    gradlew = phone_build_root / "android" / ("gradlew.bat" if os.name == "nt" else "gradlew")
    state.metrics["apkVariant"] = "release"
    result = run_command(
        [str(gradlew), ":app:installRelease", f"-Pandroid.injected.adb.device.serial={serial}"],
        cwd=phone_build_root / "android",
        env=env,
        timeout=1800,
    )
    record_command(state, result)
    if result.returncode != 0:
        state.add_finding(
            "P0",
            "apk_validation_gap",
            "Gradle installRelease 失败，APK 未安装到模拟器",
            f"returncode={result.returncode}",
            "检查 Android SDK/Gradle/JDK 与模拟器状态；也可以用 --apk-mode existing-apk 先验证运行期采集链路。",
        )
        return False
    return True


def install_existing_apk(args: argparse.Namespace, state: AuditState, adb: str, serial: str) -> bool:
    apk = Path(args.apk or "")
    if not apk.exists():
        state.add_finding("P0", "apk_validation_gap", "existing-apk 路径不存在", str(apk), "传入 --apk <path> 或改用 local-prebuild-debug。")
        return False
    result = run_command([adb, "-s", serial, "install", "-r", str(apk)], timeout=240)
    record_command(state, result)
    if result.returncode != 0:
        state.add_finding("P0", "apk_validation_gap", "APK 安装失败", result.stderr[-1000:], "检查 APK 架构、签名和模拟器 ABI。")
        return False
    return True


def launch_phone_app(adb: str, serial: str, package_name: str, state: AuditState) -> bool:
    result = run_command([adb, "-s", serial, "shell", "cmd", "package", "resolve-activity", "--brief", package_name], timeout=20)
    record_command(state, result)
    activity = ""
    for line in result.stdout.splitlines()[::-1]:
        if "/" in line and not line.strip().startswith("priority"):
            activity = line.strip()
            break
    if not activity:
        activity = f"{package_name}/.MainActivity"
    state.metrics["packageName"] = package_name
    state.metrics["launchActivity"] = activity
    result = run_command([adb, "-s", serial, "shell", "am", "start", "-n", activity], timeout=20)
    record_command(state, result)
    return result.returncode == 0


def emulator_reachable_admin_url(admin_url: str, serial: str) -> str:
    parsed = urllib.parse.urlparse(admin_url)
    if serial.startswith("emulator-") and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        netloc = f"10.0.2.2:{parsed.port}" if parsed.port else "10.0.2.2"
        return urllib.parse.urlunparse((parsed.scheme or "http", netloc, parsed.path or "", "", "", ""))
    return admin_url


def parse_bounds(bounds: str) -> tuple[int, int] | None:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    left, top, right, bottom = (int(value) for value in match.groups())
    return ((left + right) // 2, (top + bottom) // 2)


def dump_ui_xml(adb: str, serial: str) -> str:
    run_command([adb, "-s", serial, "shell", "uiautomator", "dump", "/sdcard/window.xml"], timeout=20)
    result = run_command([adb, "-s", serial, "shell", "cat", "/sdcard/window.xml"], timeout=20)
    return result.stdout


def find_node_center_by_text(xml_text: str, needles: list[str]) -> tuple[int, int] | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for node in root.iter("node"):
        text = node.attrib.get("text", "")
        desc = node.attrib.get("content-desc", "")
        if any(needle in text or needle in desc for needle in needles):
            center = parse_bounds(node.attrib.get("bounds", ""))
            if center:
                return center
    return None


def adb_tap(adb: str, serial: str, x: int, y: int) -> None:
    run_command([adb, "-s", serial, "shell", "input", "tap", str(x), str(y)], timeout=10)


def adb_clear_and_type(adb: str, serial: str, x: int, y: int, text: str) -> None:
    adb_tap(adb, serial, x, y)
    time.sleep(0.2)
    run_command([adb, "-s", serial, "shell", "input", "keyevent", "KEYCODE_MOVE_END"], timeout=10)
    for _ in range(80):
        run_command([adb, "-s", serial, "shell", "input", "keyevent", "KEYCODE_DEL"], timeout=5)
    safe_text = text.replace("%", "%25").replace(" ", "%s")
    run_command([adb, "-s", serial, "shell", "input", "text", safe_text], timeout=20)


def automate_phone_login(args: argparse.Namespace, state: AuditState, adb: str, serial: str) -> None:
    if not args.email or not args.password:
        state.metrics["phoneLoginAutomation"] = {"attempted": False, "reason": "missing_credentials"}
        return
    admin_url = emulator_reachable_admin_url(args.admin_url, serial)
    state.metrics["phoneLoginAutomation"] = {"attempted": True, "adminUrl": admin_url}
    login_seen = False
    for _ in range(30):
        xml_text = dump_ui_xml(adb, serial)
        if "Admin 地址" in xml_text or "欢迎回来" in xml_text or "登录并进入" in xml_text:
            login_seen = True
            break
        if "V8 OS" in xml_text and ("发送消息" in xml_text or "任务进度" in xml_text):
            state.metrics["phoneLoginAutomation"] = {"attempted": False, "reason": "already_in_chat"}
            return
        time.sleep(1)
    if not login_seen:
        state.add_finding(
            "P1",
            "phone_auth_required",
            "Phone 未进入可自动登录的登录页",
            "uiautomator 未发现 Admin 地址/欢迎回来/登录按钮。",
            "确认 APK 已正常启动，或手动登录后使用 --apk-mode existing-apk 复验运行期采集。",
        )
        return

    # The login page uses three stacked inputs. Text nodes are sparse in RN release,
    # so use stable screen-relative centers after confirming the login page.
    adb_clear_and_type(adb, serial, 240, 493, admin_url)
    adb_clear_and_type(adb, serial, 240, 595, args.email)
    adb_clear_and_type(adb, serial, 240, 701, args.password)
    adb_tap(adb, serial, 240, 779)
    for _ in range(40):
        xml_text = dump_ui_xml(adb, serial)
        if "发送消息" in xml_text or "任务进度" in xml_text or "对话运行" in xml_text or "智能主管" in xml_text:
            state.metrics["phoneLoginAutomation"]["ok"] = True
            return
        time.sleep(1)
    state.add_finding(
        "P1",
        "phone_auth_required",
        "Phone 自动登录后未进入对话页",
        "已填写 Admin URL/账号/密码并点击登录，但等待超时。",
        "检查 Admin URL 是否可从模拟器访问、账号状态、或登录页错误提示。",
    )


def choose_device(devices: list[dict[str, str]], device_mode: str) -> str | None:
    online = [device for device in devices if device.get("state") == "device"]
    if device_mode == "emulator":
        online = [device for device in online if device.get("serial", "").startswith("emulator-")]
    elif device_mode == "physical":
        online = [device for device in online if not device.get("serial", "").startswith("emulator-")]
    return online[0]["serial"] if online else None


def collect_device_metrics(adb: str, serial: str, package_name: str, state: AuditState) -> None:
    artifacts = {
        "logcat.txt": [adb, "-s", serial, "logcat", "-d"],
        "gfxinfo.txt": [adb, "-s", serial, "shell", "dumpsys", "gfxinfo", package_name],
        "meminfo.txt": [adb, "-s", serial, "shell", "dumpsys", "meminfo", package_name],
    }
    for filename, command in artifacts.items():
        result = run_command(command, timeout=90)
        record_command(state, result)
        path = state.report_dir / filename
        path.write_text(result.stdout + ("\nSTDERR:\n" + result.stderr if result.stderr else ""), encoding="utf-8")
        state.raw_artifacts[filename] = str(path)
        if filename == "logcat.txt":
            samples = parse_phone_perf_logcat(result.stdout)
            state.metrics["phonePerf"] = summarize_phone_perf(samples)
            (state.report_dir / "phone_perf_samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
        elif filename == "gfxinfo.txt":
            state.metrics["gfxinfo"] = parse_gfxinfo(result.stdout)
        elif filename == "meminfo.txt":
            state.metrics["meminfo"] = parse_meminfo(result.stdout)


def classify_findings(state: AuditState) -> None:
    logcat_path = state.raw_artifacts.get("logcat.txt")
    if logcat_path:
        try:
            logcat = Path(logcat_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            logcat = ""
        if "Unable to load script" in logcat or "index.android.bundle" in logcat:
            state.add_finding(
                "P0",
                "apk_js_bundle_missing",
                "APK 启动后缺少内置 JS bundle",
                "logcat/redbox 显示 Unable to load script 或 index.android.bundle 缺失。",
                "必须安装 release/preview APK 或显式 embed bundle；不能把依赖 Metro 的 debug 包当作 APK 验收。",
            )

    phone_perf = state.metrics.get("phonePerf") if isinstance(state.metrics.get("phonePerf"), dict) else {}
    stages = phone_perf.get("stages") if isinstance(phone_perf, dict) else []
    if isinstance(stages, list):
        by_stage = {str(stage.get("stage")): stage for stage in stages if isinstance(stage, dict)}
        for stage_name in ("projection-build", "projection-state", "snapshot-apply"):
            stage = by_stage.get(stage_name)
            if stage and float(stage.get("p95Ms") or 0) > 32:
                state.add_finding(
                    "P1",
                    "projection_hot_path",
                    f"{stage_name} p95 超过 32ms",
                    json.dumps(stage, ensure_ascii=False),
                    "优先检查 buildPhoneChatProjection、snapshot merge、runtimeTimeline/message fingerprint 是否重复全量计算。",
                )
        process_stage = by_stage.get("process-poll")
        if process_stage and float(process_stage.get("p95Ms") or 0) > 6000:
            state.add_finding(
                "P1",
                "process_poll_drag",
                "process polling p95 超过 6s",
                json.dumps(process_stage, ensure_ascii=False),
                "继续收紧 processes 接口超时与 stale fallback，Phone 端不要在失败时清空已有 process surface。",
            )
    if state.metrics.get("captureAttempted") and (not phone_perf or int(phone_perf.get("sampleCount") or 0) == 0):
        state.add_finding(
            "P1",
            "apk_validation_gap",
            "未采集到 V8_PHONE_PERF 样本",
            "logcat 中没有 Phone audit 样本；可能仍在 Expo dev server、未用 audit APK、或没有进入目标会话页面。",
            "确认 EXPO_PUBLIC_V8_PHONE_PERF_AUDIT=1 打进 APK，并在安装后打开真实 Phone 对话页。",
        )

    sse_path = state.raw_artifacts.get("admin_sse_stream.txt")
    if sse_path:
        try:
            sse_text = Path(sse_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            sse_text = ""
        if "runtime_episode_failed" in sse_text or "Runtime Recoverable Failure" in sse_text:
            state.add_finding(
                "P1",
                "runtime_chain_failure_sample",
                "长任务期间出现 runtime episode recoverable failure",
                "SSE/runtime stream 中包含 runtime_episode_failed 或 Runtime Recoverable Failure。",
                "这不是 Phone 渲染问题，但会导致长任务状态持续膨胀；需要单独追 runtime_broker → EpisodeRunner → handoff 链路。",
            )

    gfx = state.metrics.get("gfxinfo") if isinstance(state.metrics.get("gfxinfo"), dict) else {}
    if gfx and float(gfx.get("jankyPercent") or 0) >= 5:
        state.add_finding(
            "P1",
            "device_pressure",
            "Android gfxinfo 显示明显 jank",
            json.dumps(gfx, ensure_ascii=False),
            "进一步采集 Perfetto/Simpleperf，区分 JS projection、主线程渲染、GC 或设备资源瓶颈。",
        )

    admin_perf = state.metrics.get("adminPerf") if isinstance(state.metrics.get("adminPerf"), dict) else {}
    routes = admin_perf.get("routes") if isinstance(admin_perf, dict) else []
    if isinstance(routes, list):
        for route in routes:
            if not isinstance(route, dict):
                continue
            route_name = str(route.get("route") or "")
            if "snapshot" in route_name and float(route.get("payloadP95Bytes") or 0) > 1_000_000:
                state.add_finding(
                    "P1",
                    "sse_snapshot_bloat",
                    "snapshot payload p95 超过 1MB",
                    json.dumps(route, ensure_ascii=False),
                    "检查 runtimeTimeline/message/projection 是否重复进入 snapshot，必要时改增量或分页。",
                )


def write_report(state: AuditState, args: argparse.Namespace) -> Path:
    classify_findings(state)
    payload = {
        "args": {key: ("[REDACTED]" if key == "password" and value else value) for key, value in vars(args).items()},
        "metrics": state.metrics,
        "findings": [finding.__dict__ for finding in state.findings],
        "commands": state.commands,
        "rawArtifacts": state.raw_artifacts,
    }
    (state.report_dir / "phone_long_task_perf_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Phone 长任务卡顿 Live Audit",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 模式：{'live' if args.live else 'dry-run'}",
        f"- APK 模式：{args.apk_mode}",
        f"- 设备模式：{args.device}",
        "",
        "## 结论",
    ]
    if state.findings:
        for finding in sorted(state.findings, key=lambda item: item.severity):
            lines.extend(
                [
                    f"- **{finding.severity} / {finding.category}**：{finding.summary}",
                    f"  - 证据：{redact(finding.evidence)}",
                    f"  - 建议：{finding.recommendation}",
                ]
            )
    else:
        lines.append("- 未发现 P0/P1 毒点；请结合 raw artifacts 复核。")
    lines.extend(
        [
            "",
            "## 指标摘要",
            "```json",
            redact(json.dumps(state.metrics, ensure_ascii=False, indent=2)),
            "```",
            "",
            "## 原始文件",
        ]
    )
    for name, path in state.raw_artifacts.items():
        lines.append(f"- {name}: `{redact(path)}`")
    report_path = state.report_dir / "PHONE_LONG_TASK_PERF_AUDIT_ZH.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def run_audit(args: argparse.Namespace) -> int:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = Path(args.output_dir) if args.output_dir else Path(args.report_root) / "phone_long_task_perf" / timestamp
    report_dir.mkdir(parents=True, exist_ok=True)
    state = AuditState(report_dir=report_dir)

    adb = resolve_adb()
    emulator = resolve_emulator()
    state.metrics["toolchain"] = {
        "adb": adb,
        "emulator": emulator,
        "node": which("node"),
        "npm": which("npm"),
        "npx": which("npx"),
        "java": which("java"),
    }
    engine_ok, engine_message = check_engine_health(args.engine_url)
    admin_ok, admin_message = check_admin_health(args.admin_url)
    state.metrics["serviceHealth"] = {
        "engine": {"ok": engine_ok, "message": engine_message},
        "admin": {"ok": admin_ok, "message": admin_message},
    }
    service_severity = "P0" if args.live else "P1"
    if not engine_ok:
        state.add_finding(
            service_severity,
            "process_poll_drag",
            "Engine 健康检查不可达",
            engine_message,
            "先启动 Engine，避免长任务提交、process polling 和 SSE 采集被误判为 Phone 卡顿。",
        )
    if not admin_ok:
        state.add_finding(
            service_severity,
            "process_poll_drag",
            "Admin 健康检查不可达",
            admin_message,
            "先启动 Admin，或传入正确 --admin-url；否则无法自动登录、提交任务和收集 Admin perf。",
        )
    if not adb:
        state.add_finding("P0", "apk_validation_gap", "找不到 adb", "ADB 未在 PATH/ANDROID_HOME/ANDROID_SDK_ROOT 中发现。", "安装 Android SDK platform-tools 并配置 PATH。")
        report_path = write_report(state, args)
        print(f"Report: {report_path}")
        return 2

    devices_result = run_command([adb, "devices"], timeout=20)
    record_command(state, devices_result)
    devices = parse_adb_devices(devices_result.stdout)
    state.metrics["adbDevices"] = devices
    serial = choose_device(devices, args.device)
    if not serial:
        avds: list[str] = []
        if emulator:
            avd_result = run_command([emulator, "-list-avds"], timeout=20)
            record_command(state, avd_result)
            avds = [line.strip() for line in avd_result.stdout.splitlines() if line.strip()]
        state.metrics["availableAvds"] = avds
        state.add_finding(
            "P0",
            "apk_validation_gap",
            "没有可用的目标 Android 设备",
            f"deviceMode={args.device}, adbDevices={devices}, avds={avds}",
            "先启动一个 Android 模拟器，或传 --device physical 使用真机。",
        )
        report_path = write_report(state, args)
        print(f"Report: {report_path}")
        return 2
    state.metrics["selectedDevice"] = serial

    if not args.live:
        state.metrics["dryRun"] = True
        report_path = write_report(state, args)
        print(f"Dry-run report: {report_path}")
        return 0 if not any(item.severity == "P0" for item in state.findings) else 2

    token = None
    if args.email and args.password:
        token, login_error = admin_login(args.admin_url, args.email, args.password)
        if login_error:
            state.add_finding("P0", "phone_auth_required", "Admin 测试账号登录失败", login_error, "确认 Admin 运行中、账号密码正确，并且手机端可访问同一 Admin URL。")
        else:
            state.metrics["adminLogin"] = {"ok": True, "email": args.email}
    else:
        state.add_finding("P1", "phone_auth_required", "未提供 Phone/Admin 测试账号", "缺少 --email/--password。", "传入测试账号以自动提交长任务；否则只能采集已登录设备的基础性能。")

    if args.apk_mode == "existing-apk":
        install_ok = install_existing_apk(args, state, adb, serial)
    elif args.apk_mode in {"local-prebuild-release", "local-prebuild-debug"}:
        install_ok = build_and_install_local_prebuild(args, state, adb, serial)
    elif args.apk_mode == "eas-preview":
        install_ok = False
        state.add_finding("P1", "apk_validation_gap", "eas-preview 需要云端构建或现有 artifact", "Windows 不默认执行 EAS local build。", "先用 EAS cloud 产出 preview APK，再用 --apk-mode existing-apk --apk <path> 复验。")
    else:
        install_ok = True
    state.metrics["apkInstalled"] = install_ok

    if install_ok and not args.manual_phone:
        record_command(state, run_command([adb, "-s", serial, "shell", "am", "force-stop", args.package], timeout=20))
        record_command(state, run_command([adb, "-s", serial, "logcat", "-c"], timeout=20))
        if launch_phone_app(adb, serial, args.package, state):
            automate_phone_login(args, state, adb, serial)
    elif args.manual_phone:
        state.metrics["phoneLaunch"] = {"skipped": True, "reason": "manual_phone_mode"}
        record_command(state, run_command([adb, "-s", serial, "logcat", "-c"], timeout=20))

    if args.manual_phone:
        observe_manual_phone(args, state, token)
    elif token:
        stop_sse = threading.Event()
        sse_stats: dict[str, Any] | None = None
        session_id, session_error = create_audit_session(args.admin_url, token)
        if session_error or not session_id:
            state.add_finding("P0", "phone_auth_required", "创建测试 session 失败", session_error or "missing session id", "确认 Admin/Engine 均已启动。")
        else:
            state.metrics["sessionId"] = session_id
            sse_path = state.report_dir / "admin_sse_stream.txt"
            sse_stats = start_sse_collector(args.admin_url, token, session_id, sse_path, stop_sse)
            state.raw_artifacts["admin_sse_stream.txt"] = str(sse_path)
            run_id, submit_error = submit_long_task(args.admin_url, token, session_id, args.prompt)
            if submit_error:
                state.add_finding("P0", "phone_auth_required", "提交长任务失败", submit_error, "检查 /api/client/chat-submit 与 Engine /chat/submit。")
            else:
                state.metrics["runId"] = run_id
                time.sleep(max(1, args.capture_seconds))
            stop_sse.set()
            if sse_stats and isinstance(sse_stats.get("thread"), threading.Thread):
                sse_stats["thread"].join(timeout=5)
                sse_stats.pop("thread", None)
                state.metrics["sse"] = sse_stats
            try:
                status, perf = json_request(f"{args.admin_url.rstrip('/')}/api/client/perf", token=token, timeout=15)
                state.metrics["adminPerf"] = perf if status < 400 else {"error": perf, "status": status}
            except Exception as exc:  # noqa: BLE001
                state.metrics["adminPerf"] = {"error": f"{type(exc).__name__}: {exc}"}
    else:
        time.sleep(max(1, min(args.capture_seconds, 15)))

    state.metrics["captureAttempted"] = True
    collect_device_metrics(adb, serial, args.package, state)
    report_path = write_report(state, args)
    print(f"Report: {report_path}")
    return 1 if any(item.severity == "P0" for item in state.findings) else 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V8OS Phone long-task performance live audit")
    parser.add_argument("--live", action="store_true", help="Run real APK install/device capture/model task. Omit for dry-run preflight.")
    parser.add_argument("--case", default="long-runtime", choices=["long-runtime"], help="Audit case to run.")
    parser.add_argument("--device", default="emulator", choices=["emulator", "physical", "any"], help="ADB device selection policy.")
    parser.add_argument(
        "--apk-mode",
        default="local-prebuild-release",
        choices=["local-prebuild-release", "local-prebuild-debug", "existing-apk", "eas-preview", "none"],
        help="APK install/build mode. local-prebuild-debug is kept as a compatibility alias but installs a bundled release APK.",
    )
    parser.add_argument("--apk", default="", help="APK path for --apk-mode existing-apk.")
    parser.add_argument("--package", default=DEFAULT_PACKAGE, help="Android package name.")
    parser.add_argument("--admin-url", default=DEFAULT_ADMIN_URL)
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--email", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--capture-seconds", type=int, default=120)
    parser.add_argument("--manual-phone", action="store_true", help="Only observe the currently open Phone app. Do not launch, auto-login, create a session, or submit a prompt.")
    parser.add_argument("--session-id", default="", help="Existing session id to follow in --manual-phone mode. If omitted, the script tries to discover the newest session.")
    parser.add_argument("--phone-perf-post", action="store_true", help="Ask the APK to POST V8_PHONE_PERF samples to Admin /api/client/perf.")
    parser.add_argument("--report-root", default=str(DEFAULT_REPORT_ROOT))
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--prompt",
        default=(
            "请执行一个长任务压测：规划一个包含 Research、Engineering、Subagent 的 V8OS 主链优化方案，"
            "要求中途持续产出执行地图和阶段状态，不需要真实写文件。"
        ),
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    return run_audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
