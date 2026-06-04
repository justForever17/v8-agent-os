from __future__ import annotations

from pathlib import Path

from tests.scripts.run_phone_long_task_perf_live_audit import (
    AuditState,
    DEFAULT_PHONE_BUILD_ROOT,
    PHONE_ROOT,
    REPO_ROOT,
    build_arg_parser,
    classify_findings,
    emulator_reachable_admin_url,
    parse_bounds,
    parse_adb_devices,
    parse_gfxinfo,
    parse_meminfo,
    parse_phone_perf_logcat,
    run_command,
    summarize_phone_perf,
)


def test_repo_root_points_to_v8_agent_os() -> None:
    assert REPO_ROOT.name == "v8-agent-os"
    assert PHONE_ROOT.name == "v8-agent-os-phone"
    assert PHONE_ROOT.exists()


def test_default_phone_build_root_is_short() -> None:
    assert DEFAULT_PHONE_BUILD_ROOT.name == "v8p"
    assert len(str(DEFAULT_PHONE_BUILD_ROOT)) < len(str(Path.home() / ".v8-agent-os" / "tmp" / "phone-apk-build"))


def test_apk_mode_accepts_release_and_compat_debug() -> None:
    parser = build_arg_parser()
    assert parser.parse_args(["--apk-mode", "local-prebuild-release"]).apk_mode == "local-prebuild-release"
    assert parser.parse_args(["--apk-mode", "local-prebuild-debug"]).apk_mode == "local-prebuild-debug"


def test_emulator_admin_url_uses_host_loopback() -> None:
    assert emulator_reachable_admin_url("http://127.0.0.1:9528", "emulator-5554") == "http://10.0.2.2:9528"
    assert emulator_reachable_admin_url("http://192.168.1.9:9528", "emulator-5554") == "http://192.168.1.9:9528"


def test_parse_bounds_center() -> None:
    assert parse_bounds("[10,20][30,60]") == (20, 40)
    assert parse_bounds("") is None


def test_parse_adb_devices() -> None:
    raw = """List of devices attached
emulator-5554	device
R5CW12345	unauthorized
"""
    assert parse_adb_devices(raw) == [
        {"serial": "emulator-5554", "state": "device"},
        {"serial": "R5CW12345", "state": "unauthorized"},
    ]


def test_run_command_missing_executable_returns_result() -> None:
    result = run_command(["definitely-missing-v8os-command"], timeout=1)
    assert result.returncode == 127
    assert "COMMAND_NOT_FOUND" in result.stderr


def test_parse_gfxinfo_summary() -> None:
    raw = """
Total frames rendered: 120
Janky frames: 9 (7.50%)
90th percentile: 20ms
95th percentile: 38ms
99th percentile: 74ms
"""
    assert parse_gfxinfo(raw) == {
        "totalFrames": 120,
        "jankyFrames": 9,
        "jankyPercent": 7.5,
        "p90Ms": 20,
        "p95Ms": 38,
        "p99Ms": 74,
    }


def test_parse_meminfo_summary() -> None:
    raw = """
                 Pss  Private  Private
               Total    Dirty    Clean
Native Heap    12345    12000        0
Dalvik Heap    23456    23000        0
TOTAL          45678    45000        0
Total RAM: 8,388,608K
"""
    assert parse_meminfo(raw) == {
        "nativeHeapPssKb": 12345,
        "dalvikHeapPssKb": 23456,
        "totalPssKb": 45678,
        "totalRamKb": 8388608,
    }


def test_parse_phone_perf_logcat_and_summary() -> None:
    raw = """
06-04 10:00:00.001 I ReactNativeJS: V8_PHONE_PERF {"stage":"projection-build","elapsedMs":12,"payloadBytes":1000}
06-04 10:00:00.002 I ReactNativeJS: V8_PHONE_PERF {"stage":"projection-build","elapsedMs":40,"payloadBytes":2000}
noise
06-04 10:00:00.003 I ReactNativeJS: V8_PHONE_PERF {"stage":"snapshot-apply","elapsedMs":8,"payloadBytes":120000}
"""
    samples = parse_phone_perf_logcat(raw)
    assert len(samples) == 3
    summary = summarize_phone_perf(samples)
    projection = next(item for item in summary["stages"] if item["stage"] == "projection-build")
    assert projection["count"] == 2
    assert projection["p95Ms"] == 40
    assert projection["payloadP95Bytes"] == 2000


def test_classify_findings_from_perf_metrics(tmp_path: Path) -> None:
    state = AuditState(report_dir=tmp_path)
    state.metrics["phonePerf"] = {
        "sampleCount": 4,
        "stages": [
            {"stage": "projection-build", "count": 3, "p95Ms": 48, "payloadP95Bytes": 1000},
            {"stage": "process-poll", "count": 1, "p95Ms": 6200, "payloadP95Bytes": 500},
        ],
    }
    state.metrics["adminPerf"] = {
        "routes": [
            {"route": "admin.realtime.stream.snapshot", "payloadP95Bytes": 1_200_000, "p95Ms": 80},
        ],
    }
    state.metrics["gfxinfo"] = {"jankyPercent": 8.0}
    classify_findings(state)
    categories = {finding.category for finding in state.findings}
    assert {"projection_hot_path", "process_poll_drag", "sse_snapshot_bloat", "device_pressure"} <= categories


def test_classify_missing_phone_perf_samples(tmp_path: Path) -> None:
    state = AuditState(report_dir=tmp_path)
    state.metrics["captureAttempted"] = True
    state.metrics["phonePerf"] = {"sampleCount": 0, "stages": []}
    classify_findings(state)
    assert any(finding.category == "apk_validation_gap" for finding in state.findings)
