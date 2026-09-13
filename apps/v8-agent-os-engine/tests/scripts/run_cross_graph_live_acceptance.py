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
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit


def parent_write_during_episode(write_time: float, intervals: list[tuple[float, float]]) -> bool:
    return any(start <= write_time < end for start, end in intervals)


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
    parser.add_argument("--max-wait", type=float, default=420)
    args = parser.parse_args(argv)
    if not args.live or not args.allow_side_effects:
        parser.error("--live and --allow-side-effects are required before imports or network calls")
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

    workspace = Path(tempfile.mkdtemp(prefix="cross-graph-live-", dir=state))
    trusted, _ = audit._ensure_explicit_live_workspace_trusted(workspace)
    if not trusted:
        raise RuntimeError("isolated_workspace_trust_unverified")
    marker = "parent-B-" + workspace.name
    worker = workspace / "validate_a.py"
    worker_source = '''import hashlib, json, os, pathlib, time
root = pathlib.Path(__file__).resolve().parent
(root / "a-start.json").write_text(json.dumps({"pid": os.getpid(), "started": time.time()}))
deadline = time.monotonic() + 180
while not (root / "parent-b.txt").is_file():
    if time.monotonic() >= deadline:
        raise TimeoutError("parent independent work was not delivered during A")
    time.sleep(0.1)
data = (root / "parent-b.txt").read_bytes()
(root / "a-done.json").write_text(json.dumps({"sha256": hashlib.sha256(data).hexdigest(), "finished": time.time()}))
print("Validated parent work: " + hashlib.sha256(data).hexdigest())
'''
    worker.write_text(worker_source, encoding="utf-8")
    original_worker_hash = hashlib.sha256(worker.read_bytes()).hexdigest()
    prompt = (
        "这是已授权的真实协作验收，工作区只有本次合成文件。请只委派一个已注册的工程或验证子代理执行A，禁止继续委派。"
        f"A在绑定工作区直接执行现成脚本 validate_a.py，Python解释器为 {sys.executable}；不改脚本。"
        "A只可写a-start.json与a-done.json，其脚本会等待父级B文件然后验证。"
        "派发A后，你作为Supervisor在A仍运行期间独立完成B：使用原生文件工具写parent-b.txt，"
        f"内容严格为 {marker}，不加换行。B不依赖A的结果，不得委派B，也不能等待A完成才做B。"
        "A的等待是验收夹具内部行为，不需要任何Agent循环轮询。你可按需inspect，完成B后按具体episode await。"
        "待A真实结束后读取a-done.json并验收摘要与B一致，再向用户交付。不要修改配置、调用外网或操作本工作区以外文件。"
    )
    case = audit.LiveCaseSpec(case_id="cross_graph_parent_work", title="Parent works during background A",
                              prompt=prompt, expected_all_tools=["delegation_broker"])
    result = audit._submit_case(args.engine_url, case=case, model_profile="engine-configured-default",
        timestamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"), workspace=str(workspace))
    print(json.dumps({"phase": "submitted", "runId": result.run_id, "sessionId": result.session_id}), flush=True)
    observer = WebActivityAuditObserver(web_url=args.web_url, session_id=result.session_id, headless=True)
    observation = {"performed": False, "errors": []}
    try:
        if result.status == "failed":
            raise RuntimeError("live_submission_failed")
        observer.start()
        result = audit._poll_case(args.engine_url, result, max_wait=args.max_wait, sample_web_activity=observer.sample_live)
        observation = observer.finish()
    except BaseException:
        if result.run_id:
            try:
                audit._json_request(f"{audit._engine_api_base(args.engine_url)}/runs/{result.run_id}/cancel",
                    method="POST", payload={"reason": "cross_graph_live_harness_failed"}, timeout=10)
            except Exception:
                print(json.dumps({"cleanupRequestFailed": True, "runId": result.run_id}), flush=True)
        raise
    finally:
        observer.close()
        audit._cancel_timed_out_case(args.engine_url, result)

    events, error = audit._load_durable_runtime_events(result)
    if error:
        raise RuntimeError("durable_live_evidence_unavailable")
    episodes = db.list_runtime_episodes(run_id=result.run_id, limit=500)
    starts = {}
    parent_writes = []
    for event in events:
        topic, payload = audit._event_topic(event), audit._event_payload(event)
        if not isinstance(payload, dict):
            continue
        timestamp = _time(event.get("created_at") or event.get("timestamp"))
        episode = payload.get("episode") or {}
        episode_id = episode.get("episodeId") or episode.get("id") or payload.get("episodeId")
        if topic == "runtime.episode.started" and timestamp is not None and episode_id:
            starts.setdefault(episode_id, timestamp)
        invocation = audit._tool_invocation_from_event(event)
        if invocation and audit._is_supervisor_owned_invocation(invocation) and "parent-b.txt" in json.dumps(payload):
            if invocation["toolName"] in {"write_native_file", "write_workspace_file", "workspace_file", "file_broker"}:
                parent_writes.append(invocation["toolCallId"])
    intervals = []
    for episode in episodes:
        episode_id = episode.get("episodeId") or episode.get("id")
        end = _time(episode.get("completed_at") or episode.get("completedAt"))
        if episode_id in starts and end is not None and episode.get("kind") == "delegation":
            intervals.append((starts[episode_id], end))
    target = workspace / "parent-b.txt"
    completion = workspace / "a-done.json"
    body = target.read_bytes() if target.is_file() else b""
    proof = json.loads(completion.read_text()) if completion.is_file() else {}
    checks = {
        "runCompleted": result.status == "completed",
        "backgroundDelegationRecorded": bool(intervals),
        "parentOwnedWriteRecorded": bool(parent_writes),
        "BWrittenWhileARunning": target.is_file() and parent_write_during_episode(target.stat().st_mtime, intervals),
        "BContentsExact": body == marker.encode(),
        "WorkerVerifiedB": proof.get("sha256") == hashlib.sha256(body).hexdigest() and bool(body),
        "WorkerUnmodified": hashlib.sha256(worker.read_bytes()).hexdigest() == original_worker_hash,
        "WebLiveAndReloadAgree": bool(observation.get("performed") and not observation.get("errors")
            and observation.get("liveSubagentIds") and all((observation.get("parity") or {}).get(key) is True
                for key in ("runtimeCards", "subagentCards", "renderedNarratives"))),
    }
    report = {"passed": all(checks.values()), "checks": checks, "runId": result.run_id,
        "sessionId": result.session_id, "workspace": str(workspace), "status": result.status,
        "parentWriteCount": len(set(parent_writes)), "delegationIntervals": intervals,
        "tools": result.actual_tools, "topics": result.observed_topics, "web": observation,
        "evidenceClass": "configured provider + isolated Engine/DB + real files + browser"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(audit._redact(report) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": checks, "report": str(args.output)}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
