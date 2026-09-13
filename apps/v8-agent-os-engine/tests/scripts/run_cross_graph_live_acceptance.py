"""Real configured-model acceptance for background delegation and parent work.

Uses only a disposable Engine state root, workspace, and the existing audit
transport/observer. This script never reads the user's model configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit


def parent_write_during_episode(write_time: float, intervals: list[tuple[float, float]]) -> bool:
    return any(start <= write_time < end for start, end in intervals)


def _fixture_command(command: str, worker: Path) -> bool:
    """Accept a Python invocation of this exact fixture, never a shell recipe."""
    if any(char in command for char in (";", "|", "\n", "\r", ">", "<", "`")):
        return False
    try:
        parts = [item.strip("\"'") for item in shlex.split(command, posix=False)]
        if parts and parts[0] == "&":
            parts.pop(0)
        if len(parts) != 4 or set(parts[1:3]) != {"-B", "-u"}:
            return False
        executable = parts[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        target = Path(parts[-1])
        if not target.is_absolute():
            target = worker.parent / target
        return executable in {"python", "python.exe", "python3", "python3.exe"} and target.resolve() == worker.resolve()
    except ValueError:
        return False


def _decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def parent_native_write_receipts(events, target: Path):
    """Bind B to the canonical writer's successful receipt, not a requested call."""
    target = target.resolve()
    starts, receipts = set(), set()
    for event in events:
        payload = _decoded(event.get("payload")) or {}
        if not isinstance(payload, dict) or payload.get("ownerRuntimeId") != "chat" or payload.get("ownerAgentKind") != "supervisor":
            continue
        tool = payload.get("tool") or {}
        if tool.get("toolName") != "write_native_file":
            continue
        call_id = tool.get("toolCallId") or payload.get("toolCallId")
        topic = event.get("topic") or event.get("event_type")
        if topic == "tool.started":
            value = str((tool.get("args") or {}).get("path") or "")
            path = Path(value)
            if value and (path if path.is_absolute() else target.parent / path).resolve() == target:
                starts.add(call_id)
        elif topic == "tool.finished" and call_id in starts and tool.get("resultStatus") == "completed":
            raw = tool.get("result")
            result = _decoded(raw)
            text_receipt = isinstance(raw, str) and any(raw.startswith(f"Successfully {operation} file: {target} (")
                for operation in ("Created/Overwritten", "Appended")) and "\nContent version: " in raw
            patch_receipt = isinstance(result, dict) and result.get("ok") is True and result.get("contentVersion") and Path(str(result.get("path") or "")).resolve() == target
            if text_receipt or patch_receipt:
                receipts.add(call_id)
    return sorted(receipts)


def worker_stdout_proof(events, worker: Path, marker: str):
    """Bind process stdout to a child command receipt, excluding model prose."""
    starts, commands, observations = {}, set(), {}
    for event in events:
        payload = _decoded(event.get("payload")) or {}
        if not isinstance(payload, dict):
            continue
        topic = str(event.get("topic") or event.get("event_type") or "")
        tool = payload.get("tool") or {}
        name = tool.get("toolName")
        call_id = tool.get("toolCallId") or payload.get("toolCallId")
        is_child = payload.get("ownerAgentKind") in {"subagent", "child"} or topic.startswith("subagent.")
        if not is_child or name not in {"run_system_command", "command_session_broker", "read_background_output"}:
            continue
        if topic.endswith("tool.started"):
            args = tool.get("args") or {}
            if _fixture_command(str(args.get("command") or ""), worker):
                starts[call_id] = "start"
            elif str(args.get("command_id") or args.get("session_id") or "") in commands:
                starts[call_id] = "observe"
        elif topic.endswith("tool.finished") and call_id in starts:
            result = _decoded(tool.get("result"))
            if not isinstance(result, dict) or result.get("ok") is False or tool.get("resultStatus") in {"failed", "cancelled", "blocked"}:
                continue
            command_id = str(result.get("commandId") or result.get("sessionId") or "")
            if starts[call_id] == "start" and command_id:
                commands.add(command_id)
            if command_id not in commands:
                continue
            for text in _strings(result):
                for match in re.finditer(r"CROSS_GRAPH_(READY|DONE) (\{[^\r\n]*\})", text):
                    try:
                        record = json.loads(match[2])
                    except ValueError:
                        continue
                    if record.get("marker") != marker:
                        continue
                    if match[1] == "DONE" and result.get("returnCode") != 0:
                        continue
                    observations[(command_id, match[1])] = record
    for command_id in commands:
        ready, done = observations.get((command_id, "READY")), observations.get((command_id, "DONE"))
        if ready and done and ready.get("pid") == done.get("pid") and ready.get("started") == done.get("started"):
            return {"commandId": command_id, "ready": ready, "done": done}
    return {}


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _load_audit():
    name = "v8_cross_graph_existing_live_audit"
    source = Path(__file__).with_name("run_supervisor_runtime_skill_live_audit.py")
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--engine-url", required=True)
    parser.add_argument("--web-url", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace-name", default="", help="Optional unique synthetic name for scoped provider capture")
    parser.add_argument("--max-wait", type=float, default=420)
    parser.add_argument("--engine-only-diagnostic", action="store_true", help="Diagnose Engine without a browser; never passes the combined acceptance gate")
    args = parser.parse_args(argv)
    if not args.live or not args.allow_side_effects:
        parser.error("--live and --allow-side-effects are required before imports or network calls")
    if args.workspace_name and not re.fullmatch(r"cross-graph-live-[A-Za-z0-9_-]+", args.workspace_name):
        parser.error("workspace-name must be a single synthetic cross-graph-live name")
    state = args.state_root.resolve()
    configured_state = Path(os.environ.get("V8_AGENT_OS_HOME", "")).resolve()
    if state != configured_state or state == (Path.home() / ".v8-agent-os").resolve():
        parser.error("V8_AGENT_OS_HOME must identify the disposable state root")
    if not (state / "config.json").is_file():
        parser.error("Prepare the isolated state with public model metadata and managed references first")
    for address in (args.engine_url, args.web_url):
        parsed = urlsplit(address)
        if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port in {9527, 9528, 9530}:
            parser.error("Use the explicitly isolated local Engine/Web ports")
    audit = _load_audit()
    from core.database import db
    from tests.scripts.live_web_activity_audit import WebActivityAuditObserver

    if args.workspace_name:
        workspace = state / args.workspace_name
        workspace.mkdir()  # Never reuse another run's files or overwrite its proof.
    else:
        workspace = Path(tempfile.mkdtemp(prefix="cross-graph-live-", dir=state))
    trusted, _ = audit._ensure_explicit_live_workspace_trusted(workspace)
    if not trusted:
        raise RuntimeError("isolated_workspace_trust_unverified")
    marker = "parent-B-" + workspace.name
    worker = workspace / "validate_a.py"
    worker_source = '''import hashlib, json, os, pathlib, time
root = pathlib.Path(__file__).resolve().parent
receipt = {"marker": root.name, "pid": os.getpid(), "started": time.time(), "scriptSha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()}
print("CROSS_GRAPH_READY " + json.dumps(receipt), flush=True)
deadline = time.monotonic() + 180
while not (root / "parent-b.txt").is_file():
    if time.monotonic() >= deadline:
        raise TimeoutError("parent independent work was not delivered during A")
    time.sleep(0.1)
data = (root / "parent-b.txt").read_bytes()
print("CROSS_GRAPH_DONE " + json.dumps({**receipt, "sha256": hashlib.sha256(data).hexdigest(), "finished": time.time()}), flush=True)
'''
    worker.write_text(worker_source, encoding="utf-8")
    original_worker_hash = hashlib.sha256(worker.read_bytes()).hexdigest()
    prompt = (
        "这是已授权的真实协作验收，工作区只有本次合成文件。请只委派一个已注册的工程或验证子代理执行A，禁止继续委派。"
        "A必须是readOnly=true、writeRequired=false、writeSet=[]，仅可读validate_a.py和parent-b.txt；脚本完全只读，不能更改任何文件。"
        "A先读取现成脚本，然后在绑定工作区用run_system_command以mode='session'、terminal_mode='pipe'、timeout_seconds=210运行精确命令 python -B -u validate_a.py。"
        "取得真实commandId和CROSS_GRAPH_READY输出后，A用delegation_broker(mode='publish_partial')发布一次a-ready中间成果："
        f"outputKey='a-ready', version='v1', sourceVersion='{original_worker_hash}', usableFor=['parent-independent-b']，"
        "compactSummary包含实际进程启动时间与commandId，proofRefs引用本次命令；然后继续观察原命令到退出码0与CROSS_GRAPH_DONE，禁止提前结束任务或重启脚本。"
        f"{'在本机PowerShell中，A发布ready后可用一次Wait-Process -Id实际PID -Timeout 80做有界只读等待，再observe原command；不要高频重复observe。' if os.name == 'nt' else '发布ready后仅按需观察原command，避免高频重复读取。'}"
        "派发A后，你作为Supervisor先await该episode，收到a-ready后inspect并用accept_partial接受此版本仅用于parent-independent-b，"
        "随即在A真实进程仍运行期间独立完成B：使用原生文件工具写parent-b.txt，"
        f"内容严格为 {marker}，不加换行。B不依赖A的结果，不得委派B，也不能等待A完成才做B。"
        "A的等待是验收夹具内部行为，不需要任何Agent循环轮询。你可按需inspect，完成B后按具体episode await。"
        "待A真实结束后核对command stdout的CROSS_GRAPH_DONE摘要与B一致，再验收交付。不要修改配置、调用外网或操作本工作区以外文件。"
    )
    case = audit.LiveCaseSpec(case_id="cross_graph_parent_work", title="Parent works during background A",
                              prompt=prompt, expected_all_tools=["delegation_broker"])
    result = audit._submit_case(args.engine_url, case=case, model_profile="engine-configured-default",
        timestamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"), workspace=str(workspace))
    print(json.dumps({"phase": "submitted", "runId": result.run_id, "sessionId": result.session_id}), flush=True)
    observer = None if args.engine_only_diagnostic else WebActivityAuditObserver(web_url=args.web_url, session_id=result.session_id, headless=True)
    observation = {"performed": False, "errors": []}
    try:
        if result.status == "failed":
            raise RuntimeError("live_submission_failed")
        if observer:
            observer.start()
        result = audit._poll_case(args.engine_url, result, max_wait=args.max_wait, sample_web_activity=observer.sample_live if observer else None)
        if observer:
            observation = observer.finish()
    except BaseException:
        if result.run_id:
            try:
                audit._json_request(f"{audit._engine_api_base(args.engine_url)}/runs/{result.run_id}/commands/cancel",
                    method="POST", payload={"reason": "cross_graph_live_harness_failed"}, timeout=10)
            except Exception:
                print(json.dumps({"cleanupRequestFailed": True, "runId": result.run_id}), flush=True)
        raise
    finally:
        if observer:
            observer.close()
        audit._cancel_timed_out_case(args.engine_url, result)

    events, error = audit._load_durable_runtime_events(result)
    if error:
        raise RuntimeError("durable_live_evidence_unavailable")
    episodes = db.list_runtime_episodes(run_id=result.run_id, limit=500)
    starts = {}
    for event in events:
        topic, payload = audit._event_topic(event), audit._event_payload(event)
        if not isinstance(payload, dict):
            continue
        timestamp = _time(event.get("created_at") or event.get("timestamp"))
        episode = payload.get("episode") or {}
        episode_id = episode.get("episodeId") or episode.get("id") or payload.get("episodeId")
        if topic == "runtime.episode.started" and timestamp is not None and episode_id:
            starts.setdefault(episode_id, timestamp)
    intervals = []
    for episode in episodes:
        episode_id = episode.get("episodeId") or episode.get("id")
        end = _time(episode.get("completed_at") or episode.get("completedAt"))
        if episode_id in starts and end is not None and episode.get("kind") == "delegation":
            intervals.append((starts[episode_id], end))
    target = workspace / "parent-b.txt"
    parent_writes = parent_native_write_receipts(events, target)
    body = target.read_bytes() if target.is_file() else b""
    process_proof = worker_stdout_proof(events, worker, workspace.name)
    proof = process_proof.get("done") or {}
    process_interval = [(proof["started"], proof["finished"])] if "started" in proof and "finished" in proof else []
    ready_refs, accepted_ready_refs = set(), set()
    for episode in episodes:
        episode_id = episode.get("episodeId") or episode.get("id")
        for row in db.list_runtime_episode_handoffs(episode_id):
            handoff = row.get("payload") or row
            if handoff.get("status") == "partial" and handoff.get("outputKey") == "a-ready" and handoff.get("sourceVersion") == original_worker_hash:
                ready_refs.add(handoff["handoffRefId"])
        for receipt in db.list_runtime_episode_messages(run_id=result.run_id, recipient=f"partial:{episode_id}", pending_only=False):
            content = receipt.get("content") or {}
            if receipt.get("kind") == "accept_partial" and receipt.get("deliveryState") == "processed" and content.get("consumers") == ["parent-independent-b"]:
                accepted_ready_refs.add(content.get("handoffRefId"))
    checks = {
        "runCompleted": result.status == "completed",
        "backgroundDelegationRecorded": bool(intervals),
        "parentOwnedWriteRecorded": bool(parent_writes),
        "BWrittenWhileARunning": target.is_file() and parent_write_during_episode(target.stat().st_mtime, intervals),
        "BWrittenDuringActualChildProcess": target.is_file() and parent_write_during_episode(target.stat().st_mtime, process_interval),
        "BContentsExact": body == marker.encode(),
        "WorkerVerifiedB": proof.get("sha256") == hashlib.sha256(body).hexdigest() and bool(body),
        "ReadyPartialPublishedAndAccepted": bool(ready_refs & accepted_ready_refs),
        "WorkerUnmodified": hashlib.sha256(worker.read_bytes()).hexdigest() == original_worker_hash and proof.get("scriptSha256") == original_worker_hash,
        "WorkerReadOnly": sorted(path.name for path in workspace.iterdir() if path.is_file()) == ["parent-b.txt", "validate_a.py"],
        "WebLiveAndReloadAgree": bool(observation.get("performed") and not observation.get("errors")
            and observation.get("liveSubagentIds") and all((observation.get("parity") or {}).get(key) is True
                for key in ("runtimeCards", "subagentCards", "renderedNarratives"))),
    }
    report = {"passed": not args.engine_only_diagnostic and all(checks.values()),
        "diagnosticOnly": args.engine_only_diagnostic,
        "engineChecksPassed": all(value for key, value in checks.items() if key != "WebLiveAndReloadAgree"),
        "checks": checks, "runId": result.run_id,
        "sessionId": result.session_id, "workspace": str(workspace), "status": result.status,
        "parentWriteCount": len(set(parent_writes)), "delegationIntervals": intervals, "processProof": process_proof,
        "fixtureContract": "readonly child command + one partial readiness handoff + parent native write",
        "tools": result.actual_tools, "topics": result.observed_topics, "web": observation,
        "evidenceClass": "configured provider + isolated Engine/DB + real files" + ("; diagnostic only, browser not performed" if args.engine_only_diagnostic else " + browser")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(audit._redact(report) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "report": str(args.output)}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
