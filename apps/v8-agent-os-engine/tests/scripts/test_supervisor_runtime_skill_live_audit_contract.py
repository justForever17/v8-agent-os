from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
import urllib.error
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import core.database as database_module
from core.tools.research_quality import research_acceptance_metrics
from tests.runtime_core.test_runtime_episode_runner import _accepted_research_payload
from tests.scripts import run_supervisor_runtime_skill_live_audit as audit


def test_saved_verification_requires_current_worker_proof_not_only_ready_or_parent_prose(monkeypatch):
    sources = [{"citationKey": f"S{i}", "url": f"https://example.org/source/{i}"} for i in range(1, 6)]
    bundle = {"evidenceBundleId": "research_fixture", "claimTable": [
        {"claimId": f"S{i}:R{i}", "supportingSources": [source]} for i, source in enumerate(sources, 1)
    ], "researchEvidenceBank": {"sources": [{**source, "text": "Body"} for source in sources]}}
    monkeypatch.setattr("core.tools.research_quality.research_selected_sources", lambda _: sources)
    spec = audit._saved_research_case("rxp_fixture", "research_fixture")
    result = audit.LiveCaseResult(spec, status="completed", episodes=[{"kind": "delegation", "state": "completed"}])
    result.web_activity_audit = {"performed": True, "parity": {"runtimeCards": True, "subagentCards": True}, "errors": []}
    result.final_text = "ACCEPT\n" + "\n".join(source["url"] for source in sources)
    worker = {"kind": "subagent_result", "status": "ready", "payload": {"toolsUsed": ["research_broker"], "resultText": "BLOCKED: no source can be read"}}
    result.handoffs = [worker, {"kind": "subagent_acceptance", "status": "accepted"}]
    assert not audit._audit_saved_research_verification(result, bundle)["checks"]["workerExactBindings"]
    worker["payload"]["resultText"] = "| claimId | 引用 | URL | 核验结论 |\n|---|---|---|---|\n" + "\n".join(
        f"| S{i}:R{i} | [S{i}] | {source['url']} | 支持 |" for i, source in enumerate(sources, 1)
    )
    assert not audit._audit_saved_research_verification(result, bundle)["checks"]["workerReadEvidence"]
    result.saved_source_reads = [{**source, "evidenceBundleId": "research_fixture",
                                  "contentSha256": hashlib.sha256(b"Body").hexdigest()} for source in sources]
    assert all(audit._audit_saved_research_verification(result, bundle)["checks"].values())
    result.saved_source_reads[0]["contentSha256"] = "wrong-snapshot"
    assert not audit._audit_saved_research_verification(result, bundle)["checks"]["workerReadEvidence"]
    result.saved_source_reads[0]["contentSha256"] = hashlib.sha256(b"Body").hexdigest()
    result.handoffs[1]["status"] = "ignored"
    assert not audit._audit_saved_research_verification(result, bundle)["checks"]["parentAcceptedVerification"]
    result.episodes.append({"kind": "research", "state": "completed"})
    assert not audit._audit_saved_research_verification(result, bundle)["checks"]["noFreshResearchOrEngineering"]


def test_saved_source_receipts_require_successful_worker_body_return():
    body = {"kind": "research_source_page", "ok": True, "text": "Original body", "citationKey": "S4",
            "url": "https://example.org/original", "evidenceBundleId": "saved", "contentSha256": "hash"}
    def event(topic, **updates):
        return {"topic": topic, "payload": {"tool": {"toolName": "research_broker", "result": {**body, **updates}}}}
    assert audit._collect_saved_source_reads([event("subagent.tool.started"), event("tool.finished"),
        event("subagent.tool.finished", ok=False), event("subagent.tool.finished", text=""),
        event("subagent.tool.finished", kind="research_answer_page")]) == []
    receipts = audit._collect_saved_source_reads([event("subagent.tool.finished")])
    assert len(receipts) == 1 and receipts[0]["citationKey"] == "S4"
    assert "text" not in receipts[0]


def test_saved_verification_cannot_start_without_live_or_saved_ids(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("must reject before network or model")
    monkeypatch.setattr(audit, "_submit_case", forbidden)
    monkeypatch.setattr(audit.urllib.request, "urlopen", forbidden)
    assert audit.main(["--case", audit.SAVED_RESEARCH_VERIFICATION_CASE_ID]) == 2
    assert audit.main(["--case", audit.SAVED_RESEARCH_VERIFICATION_CASE_ID, "--live"]) == 2


def test_final_wait_claim_after_reused_route_is_not_a_passing_delivery():
    result = audit.LiveCaseResult(audit.LiveCaseSpec("delivery_truth", "delivery truth", "verify"))
    result.episodes = [{"id": "engineering-done", "state": "completed"}]
    result.final_runtime_dispatch = {"dispatched": False, "reason": "engineering_episode_already_completed"}
    result.final_text = "我已针对该单项发起一次有界修复，等待子代理完成后再做最终只读复核。"
    assert audit._unbacked_pending_delivery_claim(result)
    assert any("最后路由未派发" in finding.summary for finding in audit._case_findings(result))
    result.final_text = "此次复用了已有结果，没有启动新修复。无需等待子代理完成。"
    assert not audit._unbacked_pending_delivery_claim(result)
    result.final_text = "等待子代理完成后再做最终复核。"
    result.episodes.append({"id": "real-repair", "state": "active"})
    assert not audit._unbacked_pending_delivery_claim(result)


def test_last_dispatch_preserves_false_and_excludes_another_run():
    def event(seq, run_id, dispatched):
        return {"seq": seq, "run_id": run_id, "payload": {"tool": {"toolName": "runtime_broker", "result": {
            "runtimeDispatchStatus": {"dispatched": dispatched, "episodeCount": int(dispatched)},
        }}}}
    assert audit._last_runtime_dispatch([event(3, "other", True), event(2, "current", False), event(1, "current", True)],
                                        run_id="current") == {"dispatched": False, "episodeCount": 0, "seq": 2}


@pytest.mark.parametrize("case_id", [audit.ENGINEERING_LONG_WRITE_CASE_ID, audit.ENGINEERING_PARENT_REPAIR_CASE_ID])
@pytest.mark.parametrize("flags", [[], ["--live"], ["--allow-side-effects"]])
def test_engineering_live_requires_both_flags_before_any_io(monkeypatch, flags, case_id):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("no model, network, workspace, or database mutation without both flags")
    monkeypatch.setattr(audit.urllib.request, "urlopen", unexpected)
    monkeypatch.setattr(audit, "_submit_case", unexpected)
    monkeypatch.setattr(audit, "_prepare_engineering_live_workspace", unexpected)
    assert audit.main(["--case", case_id, *flags]) == 2


def test_parent_repair_live_proof_needs_real_file_current_reference_and_single_repair(tmp_path):
    spec = audit._case_specs(audit.ENGINEERING_PARENT_REPAIR_CASE_ID)[0]
    result = audit.LiveCaseResult(spec)
    result.episodes = [
        {"id": "original", "kind": "engineering", "state": "completed", "result_ref": "current-handoff"},
        {"id": "repair", "kind": "engineering", "state": "completed", "inputs": {
            "parentAcceptance": {"episodeId": "original", "handoffRefId": "current-handoff"},
            "engineeringRepair": {"finalRepairAttempt": True},
            "repairLineage": {"priorWriteSet": ["acceptance-note.txt"], "replacementWriteSet": ["acceptance-note.txt"]},
        }},
    ]
    result.web_activity_audit = {"performed": True, "errors": [], "liveSubagentIds": ["worker"],
                                 "parity": {"runtimeCards": True, "subagentCards": True, "renderedNarratives": True}}
    target = tmp_path / "acceptance-note.txt"
    target.write_text("draft", encoding="utf-8")
    proof = audit._audit_engineering_parent_repair(result, str(tmp_path))
    assert proof["checks"]["actualFinalContent"] is False
    target.write_text("approved", encoding="utf-8")
    assert all(audit._audit_engineering_parent_repair(result, str(tmp_path))["checks"].values())
    result.episodes[1]["inputs"]["parentAcceptance"]["handoffRefId"] = "old-handoff"
    assert audit._audit_engineering_parent_repair(result, str(tmp_path))["checks"]["currentHandoffBound"] is False
    result.episodes.append(deepcopy(result.episodes[1]))
    assert audit._audit_engineering_parent_repair(result, str(tmp_path))["checks"]["oneExplicitParentRepair"] is False


def _engineering_proof_fixture():
    data = [{"id": f"task-{i}", "title": f"示例事项{i}", "description": f"第{i}项需要记录进度并核对执行结果以方便后续复盘和跟进。",
             "category": ["工作", "学习", "生活"][i % 3], "completed": False} for i in range(120)]
    initial = ('<!doctype html><html><head><style>:root { --accent: #7c3aed; }</style></head><body>'
               '<h1 id="board-title">任务看板</h1><script id="seed-data" type="application/json">'
               + json.dumps(data, ensure_ascii=False) + '</script><!-- V8OS-LONG-WRITE-END --></body></html>')
    second = initial.replace('id="board-title">任务看板', 'id="board-title">我的任务看板')
    final = second.replace("#7c3aed", "#0f766e")
    versions = ["sha256:" + hashlib.sha256(html.encode()).hexdigest() for html in (initial, second, final)]
    arguments = [
        {"path": audit.ENGINEERING_BOARD_FILE, "content": initial},
        {"path": audit.ENGINEERING_BOARD_FILE, "content": '<h1 id="board-title">我的任务看板</h1>',
         "expected_old_text": '<h1 id="board-title">任务看板</h1>', "expected_version": versions[0]},
        {"path": audit.ENGINEERING_BOARD_FILE, "content": "--accent: #0f766e;",
         "expected_old_text": "--accent: #7c3aed;", "expected_version": versions[1]},
    ]
    events = []
    for index, args in enumerate(arguments):
        common = {"ownerAgentId": "worker", "ownerAgentKind": "subagent", "ownerRuntimeId": "engineering"}
        events += [
            {"seq": index * 2 + 1, "event_ts": "2026-09-08T01:00:01Z", "topic": "engineering.tool.started",
             "payload": {**common, "tool": {"toolName": "write_native_file", "toolCallId": f"write-{index}", "args": args}}},
            {"seq": index * 2 + 2, "topic": "engineering.tool.finished",
             "payload": {**common, "tool": {"toolName": "write_native_file", "toolCallId": f"write-{index}",
                         "resultStatus": "completed", "agentVisibleResult": json.dumps({"ok": True, "contentVersion": versions[index]})}}},
        ]
    models = [{"id": "fixture-invocation", "status": "completed", "role": "agent:worker", "output_tokens": 8000,
               "finished_at": "2026-09-08T01:00:00Z", "metadata": {"usageReported": True, "usageSource": "usage_metadata",
                   "toolCallingMode": "native", "toolCallCount": 1, "promptCache": {"outputTokenBudget": {"mode": "auto", "maxTokens": None}}}}]
    return events, models, final


def test_engineering_version_chain_checks_full_data_hash_and_reported_usage():
    events, models, html = _engineering_proof_fixture()
    proof = audit._engineering_long_write_proof(events, models, html)
    assert proof["checks"] and all(proof["checks"].values())
    assert proof["unverified"] == []
    assert proof["seedCount"] == 120
    assert proof["writeModel"]["output_tokens"] == 8000


def _engineering_range_fixture():
    events, models, _ = _engineering_proof_fixture()
    initial = events[0]["payload"]["tool"]["args"]["content"].replace("><", ">\n<")
    versions = [initial, initial.replace('id="board-title">任务看板', 'id="board-title">我的任务看板')]
    versions.append(versions[1].replace("#7c3aed", "#0f766e"))
    for index in range(3):
        args = events[index * 2]["payload"]["tool"]["args"]
        if index == 0:
            args["content"] = initial[:80] + "…"
            events[0]["payload"]["tool"]["data"] = {
                "inputContentChars": len(initial), "inputContentSha256": hashlib.sha256(initial.encode()).hexdigest(),
            }
        else:
            old = args.pop("expected_old_text")
            line = versions[index-1][:versions[index-1].index(old)].count("\n") + 1
            args.update(line_start=line, line_end=line, content=versions[index].splitlines()[line-1],
                        expected_version="sha256:" + hashlib.sha256(versions[index-1].encode()).hexdigest())
        events[index*2+1]["payload"]["tool"]["agentVisibleResult"] = json.dumps({
            "ok": True, "contentVersion": "sha256:" + hashlib.sha256(versions[index].encode()).hexdigest(),
        })
    return events, models, versions


def test_line_range_proof_requires_actual_first_file_capture_not_a_guessed_preimage(tmp_path):
    events, models, versions = _engineering_range_fixture()
    result = audit.LiveCaseResult(spec=audit._case_specs(audit.ENGINEERING_LONG_WRITE_CASE_ID)[0], run_id="r",
                                 engineering_workspace_path=str(tmp_path))
    target = tmp_path / audit.ENGINEERING_BOARD_FILE
    target.write_bytes(versions[0].encode())
    event = {**events[0], "run_id": "r"}
    audit._sample_engineering_initial_file(result, event)
    assert result.engineering_initial_content == versions[0]
    proof = audit._engineering_long_write_proof(events, models, versions[-1], initial_content=result.engineering_initial_content)
    assert all(proof["checks"].values()) and not proof["unverified"]
    missing = audit._engineering_long_write_proof(events, models, versions[-1])
    assert missing["checks"]["initialVersionMatchesArguments"] is None
    assert missing["checks"]["inputHashMatchesReceipt"] is True
    assert missing["unverified"] == ["initial_content_reverse_patch_unverified"]
    changed = audit._engineering_long_write_proof(events, models, versions[-1].replace("示例事项0", "被改的数据"),
                                                 initial_content=result.engineering_initial_content)
    assert changed["checks"]["originalDataPreserved"] is False
    assert changed["checks"]["finalVersionMatchesFile"] is False


@pytest.mark.parametrize("wrong_run", [False, True])
def test_first_file_capture_rejects_later_version_and_cross_run_events(tmp_path, wrong_run):
    events, _, versions = _engineering_range_fixture()
    result = audit.LiveCaseResult(spec=audit._case_specs(audit.ENGINEERING_LONG_WRITE_CASE_ID)[0], run_id="r",
                                 engineering_workspace_path=str(tmp_path))
    (tmp_path / audit.ENGINEERING_BOARD_FILE).write_bytes(versions[0 if wrong_run else -1].encode())
    audit._sample_engineering_initial_file(result, {**events[0], "run_id": "other" if wrong_run else "r"})
    assert result.engineering_initial_content is None


def test_engineering_progress_contract_and_clipped_args_are_verified_by_full_hash():
    events, models, html = _engineering_proof_fixture()
    for event in events:
        tool = event["payload"]["tool"]
        initial = str((tool.get("args") or {}).get("content") or "")
        if len(initial) > 2400:
            tool["data"] = {"inputContentChars": len(initial), "inputContentSha256": hashlib.sha256(initial.encode()).hexdigest()}
            tool["args"]["content"] = initial[:2399] + "…"
        if event["topic"].endswith("finished"):
            raw = tool.get("result") or tool.get("agentVisibleResult") or ""
            version = re.search(r"sha256:[a-f0-9]{64}", str(raw))
            assert version
            tool.pop("result", None)
            tool.pop("resultStatus", None)
            tool["agentVisibleResult"] = f"write native file result\nContent version: {version[0]}; reuse as expected_version.\nKind: scoped_file_patch"
        event["payload"] = {"progress": {"agentId": "worker", "delegationId": "worker-delegation", "status": "running",
            "timelineNode": {**tool, "topic": event["topic"], "executionType": "tool_call" if event["topic"].endswith("started") else "tool_result"}}}
        event["topic"] = "runtime.episode.progress"
    proof = audit._engineering_long_write_proof(events, models, html)
    assert all(proof["checks"].values()) and not proof["unverified"], proof
    events[0]["payload"]["progress"]["timelineNode"]["data"]["inputContentSha256"] = "f" * 64
    assert audit._engineering_long_write_proof(events, models, html)["checks"]["inputHashMatchesReceipt"] is False


@pytest.mark.parametrize("mutation,check", [
    ("estimated_usage", "providerReportedLongGeneration"),
    ("short_generation", "providerReportedLongGeneration"),
    ("fixed_cap", "autoRequestBudgetObserved"),
    ("other_actor", "sameImplementationActor"),
    ("stale_version", "versionReuse"),
    ("whole_rewrite", "localPatchesOnly"),
    ("data_changed", "originalDataPreserved"),
    ("missing_tail", "completeHtmlTail"),
    ("forged_proof", "finalVersionMatchesFile"),
])
def test_engineering_harness_rejects_real_failure_mutants(mutation, check):
    events, models, html = _engineering_proof_fixture()
    if mutation == "estimated_usage":
        models[0]["metadata"]["usageReported"] = False
    elif mutation == "short_generation":
        models[0]["output_tokens"] = 4096
    elif mutation == "fixed_cap":
        models[0]["metadata"]["promptCache"]["outputTokenBudget"] = {"mode": "fixed", "maxTokens": 4096}
    elif mutation == "other_actor":
        events[2]["payload"]["ownerAgentId"] = "second-worker"
    elif mutation == "stale_version":
        events[4]["payload"]["tool"]["args"]["expected_version"] = "stale"
    elif mutation == "whole_rewrite":
        events[2]["payload"]["tool"]["args"].update(allow_full_replace=True, content=html)
    elif mutation == "data_changed":
        html = html.replace("示例事项0", "丢失原始事项")
    elif mutation == "missing_tail":
        html = html.replace(audit.ENGINEERING_TAIL_CANARY, "")
    elif mutation == "forged_proof":
        events[5]["payload"]["tool"]["agentVisibleResult"] = json.dumps({"ok": True, "contentVersion": "sha256:" + "0" * 64})
    assert audit._engineering_long_write_proof(events, models, html)["checks"][check] is False


def test_engineering_missing_telemetry_is_unverified_not_pass_or_token_estimate():
    events, _, html = _engineering_proof_fixture()
    proof = audit._engineering_long_write_proof(events, [], html)
    assert "write_model_usage_or_request_budget_not_bound" in proof["unverified"]
    assert proof["checks"]["providerReportedLongGeneration"] is False


def test_engineering_repeated_read_and_failed_tool_result_cannot_prove_receipt_reuse():
    events, models, html = _engineering_proof_fixture()
    for row in events[2:]:
        row["seq"] += 1
    events.append({"seq": 3, "topic": "engineering.tool.started", "payload": {"ownerAgentId": "worker", "tool": {
        "toolCallId": "reread", "toolName": "read_native_file", "args": {"path": audit.ENGINEERING_BOARD_FILE}}}})
    assert audit._engineering_long_write_proof(events, models, html)["checks"]["noRedundantReadsBetweenWrites"] is False
    events[3]["payload"]["tool"]["resultStatus"] = "failed"
    assert audit._engineering_long_write_proof(events, models, html)["checks"]["threeSuccessfulWrites"] is False


def test_engineering_preflight_failure_stops_before_billable_submit(monkeypatch):
    class Reachable:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
    monkeypatch.setattr(audit.urllib.request, "urlopen", lambda *_args, **_kwargs: Reachable())
    monkeypatch.setattr(audit, "_wait_for_engine", lambda *_args, **_kwargs: (True, None))
    def preflight(*_args, **_kwargs):
        raise RuntimeError("browser unavailable or trust readback failed")
    def unexpected(*_args, **_kwargs):
        raise AssertionError("must not submit live when preflight failed")
    monkeypatch.setattr(audit, "_prepare_engineering_live_workspace", preflight)
    monkeypatch.setattr(audit, "_submit_case", unexpected)
    assert audit.main(["--live", "--allow-side-effects", "--case", audit.ENGINEERING_LONG_WRITE_CASE_ID,
                       "--web-url", "http://127.0.0.1:9527", "--model-profile", "fixture"]) == 2


def test_engineering_telemetry_reads_only_its_run_without_mutating_the_database(monkeypatch, tmp_path):
    import sqlite3
    from core import v8_agent_os_paths
    path = tmp_path / "observability.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE model_invocation_logs (id TEXT, provider_id TEXT, model_id TEXT, role TEXT, status TEXT, "
                           "output_tokens INTEGER, metadata_json TEXT, started_at TEXT, finished_at TEXT, session_id TEXT, run_id TEXT)")
        for run in ("target", "another"):
            connection.execute("INSERT INTO model_invocation_logs VALUES (?, 'fixture', 'model', 'agent:worker', 'completed', 9000, '{}', '', '', 'session', ?)", (run, run))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(v8_agent_os_paths, "OBSERVABILITY_DB_PATH", path)
    result = audit.LiveCaseResult(spec=audit.LiveCaseSpec(case_id="fixture", title="fixture", prompt="fixture"), session_id="session", run_id="target")
    rows, error = audit._engineering_model_observations(result)
    assert error is None and [row["id"] for row in rows] == ["target"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_engineering_long_case_is_opt_in_and_requests_inline_data_not_generated_shortcuts():
    assert audit.ENGINEERING_LONG_WRITE_CASE_ID not in {case.case_id for case in audit._case_specs("all")}
    spec = audit._case_specs(audit.ENGINEERING_LONG_WRITE_CASE_ID)[0]
    assert "120" in spec.prompt and "不用Array.from" in spec.prompt
    assert "通用下限" in spec.prompt and "expected_version" in spec.prompt


def test_engineering_case_does_not_pass_without_real_browser_and_live_web_parity():
    events, models, html = _engineering_proof_fixture()
    proof = audit._engineering_long_write_proof(events, models, html)
    result = audit.LiveCaseResult(spec=audit._case_specs(audit.ENGINEERING_LONG_WRITE_CASE_ID)[0], status="completed",
                                 actual_tools=["runtime_broker", "write_native_file"],
                                 episodes=[{"kind": "engineering"}, {"kind": "delegation"}],
                                 engineering_long_write_audit=deepcopy(proof), final_text="任务看板已交付。")
    assert audit._case_findings(result)
    result.engineering_long_write_audit["browser"] = {"performed": True, "checks": {
        key: True for key in ["initialRows", "titleMicroEdit", "colorMicroEdit", "filter", "add", "saveReload", "offlineSingleFile"]}, "errors": []}
    result.web_activity_audit = {"performed": True, "liveRuntimeIds": ["engineering"], "liveSubagentIds": ["worker"],
                                 "parity": {"runtimeCards": True, "subagentCards": True}, "errors": []}
    assert not audit._case_findings(result)
    result.engineering_long_write_audit["browser"]["checks"].pop("saveReload")
    assert audit._case_findings(result)


def test_reviewed_partial_terminal_is_observable_but_unreviewed_degraded_is_not():
    from tests.core.test_research_agent import saved_bundle
    bundle = saved_bundle(partial=True)
    result = {**bundle, "taskBriefId": "limited", "query": bundle["question"], "sources": bundle["sourceMatrix"]}
    event = {"seq": 12, "topic": "runtime.episode.degraded", "payload": {
        "episode": {"id": "research-episode", "kind": "research"},
        "handoff": {"taskBriefResults": [result]},
    }}
    assert audit._research_completion_seq([event], {"research-episode"}) == 12
    result["answer"] += " Unverified change."
    assert audit._research_completion_seq([event], {"research-episode"}) is None


def test_harness_session_continuation_uses_new_message_identity_and_rejects_user_session(monkeypatch):
    requests = []
    def request(_url, **kwargs):
        requests.append(kwargs["payload"])
        return {"runId": "run-new"}
    monkeypatch.setattr(audit, "_json_request", request)
    case = audit._case_specs("research_delegated_verification")[0]
    arguments = {"case": case, "model_profile": "fixture", "timestamp": "new-attempt", "workspace": str(audit.REPO_ROOT)}
    audit._submit_case("http://localhost:9530", **arguments, existing_session_id="supervisor-runtime-skill-live-prior")
    assert requests[0]["session_id"] == "supervisor-runtime-skill-live-prior"
    assert requests[0]["clientMessageId"].endswith("new-attempt")
    with pytest.raises(ValueError, match="harness-owned"):
        audit._submit_case("http://localhost:9530", **arguments, existing_session_id="user-session")
    assert len(requests) == 1


def test_continuation_audit_does_not_count_prior_run_events(monkeypatch):
    prior = {"id": "old", "run_id": "run-old", "seq": 2, "topic": "tool.started"}
    current = {"id": "new", "run_id": "run-current", "seq": 4, "topic": "runtime.episode.completed"}
    monkeypatch.setattr(database_module, "db", SimpleNamespace(
        get_runtime_events=lambda _: [prior, current],
        get_runtime_events_for_run=lambda *_args, **_kwargs: [current],
    ))
    result = audit.LiveCaseResult(spec=audit.LiveCaseSpec(case_id="reuse", title="reuse", prompt="test"),
                                 session_id="same-session", run_id="run-current")
    events, error = audit._load_durable_runtime_events(result)
    assert error is None
    assert events == [current]


def test_unreachable_web_prevents_billable_live_submission(monkeypatch):
    def unreachable(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")
    def unexpected(*_args, **_kwargs):
        raise AssertionError("must not submit a billable run without the requested Web observer")
    monkeypatch.setattr(audit.urllib.request, "urlopen", unreachable)
    monkeypatch.setattr(audit, "_submit_case", unexpected)
    assert audit.main([
        "--live", "--case", "research_delegated_verification",
        "--web-url", "http://127.0.0.1:19527", "--model-profile", "fixture",
    ]) == 2


def test_tool_inventory_does_not_promote_transcript_prose_to_execution():
    handoffs = [{"toolsUsed": ["tool_observation_detail"], "compactTranscript": (
        'ai: 使用工具: write_native_file\ntool: Tool observation detail\n'
        '{"toolName":"run_system_command"}'
    )}]
    assert audit._collect_handoff_tool_names(handoffs) == ["tool_observation_detail"]
    assert audit._collect_handoff_tool_names([{"resultText": "使用工具: web_broker"}]) == []


def test_timed_out_live_case_cancels_only_its_run_without_changing_verdict(monkeypatch):
    calls = []
    def request(url, **kwargs):
        calls.append((url, kwargs))
        return {"transition_event": {"topic": "run.state.changed"}}
    monkeypatch.setattr(audit, "_json_request", request)
    result = audit.LiveCaseResult(
        spec=audit.LiveCaseSpec(case_id="bounded", title="bounded", prompt="test"),
        session_id="session-live", run_id="run-live", status="timeout",
        failure_reason="run_or_episode_not_terminal_within_max_wait",
    )
    audit._cancel_timed_out_case("http://127.0.0.1:19532", result)
    assert len(calls) == 1
    assert calls[0][0] == "http://127.0.0.1:19532/v1/runs/run-live/commands/cancel"
    assert calls[0][1]["method"] == "POST"
    assert result.status == "timeout"
    assert result.failure_reason == "run_or_episode_not_terminal_within_max_wait"
    assert "deadlineCleanup" in " ".join(result.key_events)
    calls.clear()
    result.status = "completed"
    audit._cancel_timed_out_case("http://127.0.0.1:19532", result)
    result.status, result.run_id = "timeout", None
    audit._cancel_timed_out_case("http://127.0.0.1:19532", result)
    assert calls == []


def test_timeout_cleanup_error_stays_visible_and_does_not_hide_live_failure(monkeypatch):
    def request(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(audit, "_json_request", request)
    result = audit.LiveCaseResult(
        spec=audit.LiveCaseSpec(case_id="bounded", title="bounded", prompt="test"),
        session_id="session-live", run_id="run-live", status="timeout",
    )
    audit._cancel_timed_out_case("http://127.0.0.1:19532", result)
    assert result.status == "timeout"
    assert "deadlineCleanupError" in " ".join(result.key_events)
    assert "connection refused" in " ".join(result.key_events)


def test_live_audit_default_workspace_is_product_repository():
    assert audit.REPO_ROOT.name == "v8-agent-os"
    assert (audit.REPO_ROOT / "release-manifest.json").is_file()


def test_pure_research_submit_uses_research_mode_without_engineering_lane(monkeypatch):
    captured: dict = {}

    def fake_request(_url, *, method, payload, timeout):
        captured.update(payload)
        return {"session_id": payload["session_id"], "run_id": "run-pure-research"}

    monkeypatch.setattr(audit, "_json_request", fake_request)
    case = audit._case_specs(audit.PURE_RESEARCH_CASE_ID)[0]

    result = audit._submit_case(
        "http://127.0.0.1:19532",
        case=case,
        model_profile="configured",
        timestamp="20260730T170000Z",
        workspace=r"E:\workspace\v8-agent-os",
    )

    assert result.status == "submitted"
    assert captured["data"]["auditProfile"] == "configured"
    assert "modelProfile" not in captured["data"]
    assert captured["data"]["supervisorWorkMode"] == "daily"
    assert captured["data"]["supervisorRuntimeMode"] == "research"
    assert captured["data"]["engineeringMode"] == "off"
    assert "research episode" not in case.prompt.lower()


def test_delegated_research_submit_uses_research_mode_and_requires_verifier(monkeypatch):
    captured: dict = {}

    def fake_request(_url, *, method, payload, timeout):
        captured.update(payload)
        return {"session_id": payload["session_id"], "run_id": "run-research-verifier"}

    monkeypatch.setattr(audit, "_json_request", fake_request)
    case = audit._case_specs(audit.RESEARCH_DELEGATED_VERIFICATION_CASE_ID)[0]

    result = audit._submit_case(
        "http://127.0.0.1:19532",
        case=case,
        model_profile="configured",
        timestamp="20260903T010000Z",
        workspace=str(audit.REPO_ROOT),
    )

    assert result.status == "submitted"
    assert captured["data"]["supervisorRuntimeMode"] == "research"
    assert captured["data"]["engineeringMode"] == "off"
    assert case.expected_episode_kinds == ["research", "delegation"]
    assert "Verification Engineer" in case.prompt


def test_engineering_long_write_explicitly_selects_the_runtime_it_asserts(monkeypatch):
    captured = {}
    def request(_url, *, method, payload, timeout):
        captured.update(payload)
        return {"session_id": payload["session_id"], "run_id": "run-engineering"}
    monkeypatch.setattr(audit, "_json_request", request)
    case = audit._case_specs(audit.ENGINEERING_LONG_WRITE_CASE_ID)[0]
    result = audit._submit_case("http://localhost:9530", case=case, model_profile="configured", timestamp="fixture", workspace="temporary")
    assert result.status == "submitted"
    assert captured["data"]["supervisorRuntimeMode"] == "engineering"
    assert "max_tokens" not in captured["data"]


def test_delegated_research_diagnostic_requires_sequential_durable_truth(monkeypatch):
    research_payload = {
        "kind": "research_evidence_bundle",
        "answer": "复核前研究正文" * 500,
        "sources": [
            {"url": f"https://official-{index}.gov.cn/policy", "selectedForEvidence": True}
            for index in range(1, 6)
        ],
        "claimTable": [{"claimId": f"C{index}", "supportingSources": [
            {"citationKey": f"S{index}", "url": f"https://official-{index}.gov.cn/policy"}
        ]} for index in range(1, 4)],
    }
    result = audit.LiveCaseResult(
        spec=audit._case_specs(audit.RESEARCH_DELEGATED_VERIFICATION_CASE_ID)[0],
        status="completed",
        final_text=(
            "截至 2026 年 9 月 3 日，以下结论已经 Verification Engineer 独立复核。\n"
            + "\n".join(
                f"检查项 {index}：{hashlib.sha256(f'official-evidence-{index}'.encode()).hexdigest()}；"
                "核对法规义务、适用范围、关键日期与对应官方证据。"
                for index in range(1, 70)
            )
            + "\n"
            + "\n".join(f"https://official-{index}.gov.cn/policy" for index in range(1, 6))
        ),
        research_completed_seq=40,
        episodes=[
            {"episodeId": "research-1", "kind": "research", "state": "completed"},
            {
                "episodeId": "delegation-1",
                "kind": "delegation",
                "state": "completed",
                "taskBrief": {"preferredAgentId": "verification-engineer"},
            },
        ],
        handoffs=[
            {"episodeId": "research-1", "payload": research_payload},
            {
                "episodeId": "delegation-1",
                "payload": {
                    "kind": "delegation",
                    "status": "completed",
                    "summary": "Verification Engineer 已核验日期、法规层级、义务和来源。\n" + "\n".join(
                        f"C{index} [S{index}] https://official-{index}.gov.cn/policy 已核验"
                        for index in range(1, 4)
                    ),
                },
            },
        ],
        tool_invocations=[
            {
                "seq": 44,
                "topic": "tool.started",
                "toolName": "delegation_broker",
                "ownerRuntimeId": "chat",
                "ownerAgentKind": "supervisor",
                "ownerAgentId": "supervisor",
            }
        ],
    )
    result.final_text += "\n" + research_payload["answer"]
    result.final_text += (
        "\n生成式人工智能服务管理暂行办法；互联网信息服务深度合成管理规定；"
        "人工智能生成合成内容标识办法；GB 45438-2025。未核实事项仍列为证据缺口；上线清单如下。"
    )
    monkeypatch.setattr(
        audit,
        "_research_handoff_assessment",
        lambda _payload, *, question: {
            "sourceUrls": [f"https://official-{index}.gov.cn/policy" for index in range(1, 6)]
        },
    )

    diagnostic = audit._delegated_research_verification_diagnostic(result)
    findings = audit._delegated_research_verification_findings(result)

    assert diagnostic["delegationAfterResearch"] is True
    assert diagnostic["verificationIdentityPresent"] is True
    assert diagnostic["delegationTerminal"] is True
    assert findings == [], (diagnostic, [item.summary for item in findings])

    # A worker name or an exception wrapper mentioning verification is not a
    # verification result, even if legacy lifecycle metadata says completed.
    result.handoffs[1]["payload"].update({
        "summary": "[Verification Engineer 执行异常] V8LLMTimeoutError: deadline exceeded",
        "workerStatus": "ok",
    })
    assert audit._delegated_research_verification_diagnostic(result)["verificationResultPresent"] is False


@pytest.mark.parametrize("selected,clicks", [("true", 0), ("false", 1)])
def test_web_activity_audit_does_not_reclick_active_overview(selected, clicks):
    from tests.scripts import live_web_activity_audit as web_audit
    calls = []
    first = SimpleNamespace(get_attribute=lambda _name: selected,
                            click=lambda **kwargs: calls.append(kwargs))
    observer = web_audit.WebActivityAuditObserver(web_url="http://localhost", session_id="test")
    observer._page = SimpleNamespace(locator=lambda _selector: SimpleNamespace(count=lambda: 1, first=first))
    observer._overview()
    assert len(calls) == clicks


def test_web_activity_audit_waits_for_authoritative_reload_evidence(monkeypatch):
    from tests.scripts import live_web_activity_audit as web_audit

    observer = web_audit.WebActivityAuditObserver(web_url="http://localhost", session_id="test")
    monkeypatch.setattr(observer, "_terminal_runtime_details", lambda _snapshot: [])
    observer._page = SimpleNamespace(
        reload=lambda **_kwargs: None,
        locator=lambda _selector: SimpleNamespace(wait_for=lambda **_kwargs: None),
    )
    terminal = {
        "pageReady": True,
        "narratives": [{"chars": 10, "sha256": "stable-content", "turnId": "initial"}],
        "runtimeCards": [{"runtimeId": "research", "status": "recent", "eventCount": 2}],
        "subagentCards": [],
        "researchEvents": [{"eventSeq": 113, "topic": "runtime.episode.completed"}],
    }
    provisional = {**terminal, "researchEvents": [], "narratives": []}
    phases = []
    def snapshot(phase, **_kwargs):
        phases.append(phase)
        if phase == "terminal_reload" and phases.count(phase) == 1:
            return provisional
        return {**terminal, "narratives": [{**terminal["narratives"][0], "turnId": "" if phase == "terminal_reload" else "initial"}]}
    clock = [0.0]
    monkeypatch.setattr(observer, "_snapshot", snapshot)
    monkeypatch.setattr(web_audit.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(web_audit.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    result = observer.finish()

    assert result["errors"] == []
    assert all(result["parity"].values())
    assert phases.count("terminal_live") >= 5 and phases.count("terminal_reload") >= 6


@pytest.mark.parametrize("flash_content,wrong_page", [(False, False), (True, False), (False, True)])
def test_web_activity_audit_empty_or_wrong_page_never_passes_parity_or_measurement(monkeypatch, flash_content, wrong_page):
    from tests.scripts import live_web_activity_audit as web_audit
    observer = web_audit.WebActivityAuditObserver(web_url="http://localhost", session_id="test")
    monkeypatch.setattr(observer, "_terminal_runtime_details", lambda _snapshot: [])
    observer._page = SimpleNamespace(reload=lambda **_kwargs: None,
                                     locator=lambda _selector: SimpleNamespace(wait_for=lambda **_kwargs: None))
    counts = {}
    def snapshot(phase, **_kwargs):
        counts[phase] = counts.get(phase, 0) + 1
        return {"pageReady": not wrong_page, "runtimeCards": [], "subagentCards": [], "researchEvents": [],
                "narratives": [{"chars": 20, "sha256": "content"}] if wrong_page or (flash_content and counts[phase] == 1) else []}
    clock = [0.0]
    monkeypatch.setattr(observer, "_snapshot", snapshot)
    monkeypatch.setattr(web_audit.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(web_audit.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    result = observer.finish()
    assert result["errors"]
    assert not any(result["parity"].values())
    assert result["narrativeMeasurement"]["status"] == "unverified"


def test_terminal_runtime_details_record_all_rows_without_leaking_text_or_live_tab_churn(monkeypatch):
    from tests.scripts import live_web_activity_audit as web_audit

    observer = web_audit.WebActivityAuditObserver(web_url="http://localhost", session_id="test")
    selected, clicks = [], []
    monkeypatch.setattr(observer, "_overview", lambda: None)
    def locator(selector):
        if selector.startswith('[data-runtime-activity-runtime='):
            runtime_id = selector.split('"')[1]
            return SimpleNamespace(first=SimpleNamespace(click=lambda **_: (selected.append(runtime_id), clicks.append(runtime_id))))
        return SimpleNamespace(wait_for=lambda **_: None, locator=lambda rows: SimpleNamespace(evaluate_all=lambda _: [
            {"position": 0, "eventSeq": 11 if selected[-1] == "engineering" else 12, "topic": "runtime.episode.completed", "summary": "private text"},
            {"position": 1, "eventSeq": 0, "topic": "", "summary": "private artifact"},
        ]))
    observer._page = SimpleNamespace(locator=locator)
    snapshot = {"runtimeCards": [{"runtimeId": item, "eventCount": 2} for item in ["engineering", "extensions"]]}
    monkeypatch.setattr(observer, "_snapshot", lambda *_args, **_kwargs: snapshot)
    observer.sample_live()
    assert clicks == []
    observed = observer._terminal_runtime_details(snapshot)
    assert clicks == ["engineering", "extensions"]
    assert all(item["observedEntryCount"] == item["overviewEventCount"] == 2 for item in observed)
    assert [item["entries"][0]["eventSeq"] for item in observed] == [11, 12]
    assert all(item["entries"][1]["identitySource"] == "dom_position_and_summary_hash" for item in observed)
    assert "private" not in json.dumps(observed)
    assert observed[0]["entries"][0]["summarySha256"] == hashlib.sha256(b"private text").hexdigest()


def test_terminal_details_cannot_turn_real_live_reload_count_mismatch_into_success(monkeypatch):
    from tests.scripts import live_web_activity_audit as web_audit

    observer = web_audit.WebActivityAuditObserver(web_url="http://localhost", session_id="test")
    observer._page = SimpleNamespace(reload=lambda **_: None, locator=lambda _: SimpleNamespace(wait_for=lambda **_: None))
    collected = []
    def details(snapshot):
        collected.append(snapshot["phase"])
        return [{"runtimeId": "engineering", "entries": [{"eventSeq": 11}]}]
    monkeypatch.setattr(observer, "_terminal_runtime_details", details)
    monkeypatch.setattr(observer, "_snapshot", lambda phase, **_: {
        "phase": phase, "pageReady": True, "narratives": [{"chars": 10, "sha256": "same", "turnId": ""}],
        "runtimeCards": [{"runtimeId": "engineering", "eventCount": 12 if phase == "terminal_live" else 16}],
    })
    clock = [0.0]
    monkeypatch.setattr(web_audit.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(web_audit.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    result = observer.finish()
    assert collected == ["terminal_live", "terminal_reload"]
    assert result["errors"] and result["parity"]["runtimeCards"] is False
    assert result["terminalLive"]["runtimeDetails"] and result["terminalReload"]["runtimeDetails"]
    assert result["narrativeMeasurement"]["status"] == "unverified"


def test_web_activity_audit_selector_handles_missing_turn_id_and_excludes_non_chat_regions():
    from tests.scripts import live_web_activity_audit as web_audit
    observer = web_audit.WebActivityAuditObserver(web_url="http://localhost", session_id="test")
    seen = []
    def locator(selector):
        seen.append(selector)
        # The hydrated message still renders without the optional turnId.
        return SimpleNamespace(evaluate_all=lambda _script: [{"turnId": "", "position": 0, "texts": ["Actual answer\nNext line"]}]
                               if selector == '.v8-chat-viewport-surface [aria-live="polite"]' else [])
    observer._page = SimpleNamespace(locator=locator)
    rows = observer._narrative_snapshot()
    assert rows[0]["chars"] == len("Actual answer\nNext line")
    assert rows[0]["sha256"] == hashlib.sha256(b"Actual answer\nNext line").hexdigest()
    assert seen == ['.v8-chat-viewport-surface [aria-live="polite"]']


@pytest.mark.parametrize("url,visible,ready", [
    ("http://localhost/chat?id=test", True, True),
    ("http://localhost/chat?id=other", True, False),
    ("http://localhost/login", True, False),
    ("http://localhost/chat?id=test", False, False),
])
def test_web_activity_audit_requires_requested_session_and_chat_viewport(url, visible, ready):
    from tests.scripts import live_web_activity_audit as web_audit
    observer = web_audit.WebActivityAuditObserver(web_url="http://localhost", session_id="test")
    observer._page = SimpleNamespace(url=url, locator=lambda _selector: SimpleNamespace(is_visible=lambda: visible))
    assert observer._page_ready() is ready


def test_final_text_does_not_hide_a_later_completed_message_based_on_length():
    delivery = "\n\n".join(
        [
            "截至 2026 年 7 月 29 日，通用目的 AI 模型的合规时间线需要区分法规原文和后续指南。",
            "系统性风险门槛、透明度、版权和模型文档义务必须分别核对，并保留来源约束。",
            "既有模型过渡规则、Code of Practice、执法罚则与上线行动清单构成完整交付。",
        ]
    )
    messages = [
        {
            "role": "assistant",
            "run_id": "run-research",
            "state": "completed",
            "ordinal": 2,
            "content_text": delivery,
        },
        {
            "role": "assistant",
            "run_id": "run-research",
            "state": "completed",
            "ordinal": 3,
            "content_text": "Research handoff 已回流。",
        },
    ]

    selected = audit._extract_final_text(
        messages,
        preferred_run_id="run-research",
    )

    assert selected == "Research handoff 已回流。"


def test_final_text_keeps_later_short_blocker_instead_of_hiding_it_behind_old_delivery():
    messages = [
        {
            "role": "assistant",
            "run_id": "run-research",
            "state": "completed",
            "ordinal": 2,
            "content_text": "A detailed but now invalid answer. " * 200,
        },
        {
            "role": "assistant",
            "run_id": "run-research",
            "state": "completed",
            "ordinal": 3,
            "content_text": "无法交付：独立复核发现关键证据不成立。",
        },
    ]

    selected = audit._extract_final_text(
        messages,
        preferred_run_id="run-research",
    )

    assert selected == "无法交付：独立复核发现关键证据不成立。"


def test_final_text_never_substitutes_streaming_or_another_run_for_delivery():
    old = {"role": "assistant", "run_id": "old", "state": "completed", "content_text": "Earlier completed research."}
    running = {"role": "assistant", "run_id": "current", "state": "streaming", "content_text": "<tool_call>" * 200}
    assert audit._extract_final_text([running], preferred_run_id="current") == ""
    assert audit._extract_final_text([old, running], preferred_run_id="current") == ""
    assert audit._extract_final_text([old], preferred_run_id="current") == ""
    failed = {**running, "state": "failed", "finalized_at": "2026-09-04T00:00:00Z"}
    assert audit._extract_final_text([failed], preferred_run_id="current") == ""


def test_final_text_selects_last_model_narrative_and_keeps_prior_turns_auditable():
    nodes = [{"id": str(index), "kind": "narrative", "ownerAgentKind": "supervisor",
              "content": content, "finalized": True, "partial": False,
              "ownerStreamKey": f"chat:supervisor:text:{'prior' if index == 0 else 'final'}:segment:{index}"}
             for index, content in enumerate(["正在安排调研。", "最终核验", "答案。"])]
    nodes.insert(1, {"kind": "reasoning", "content": "not a final answer"})
    message = {"role": "assistant", "run_id": "current", "state": "completed",
               "content_text": "正在安排调研。最终核验答案。", "nodes_json": json.dumps(nodes)}
    delivery = audit._extract_final_delivery([message], preferred_run_id="current")
    assert delivery["text"] == "最终核验答案。"
    assert delivery["source"] == "finalized_model_narrative"
    assert len(delivery["selectedNarrativeSegments"]) == 2
    assert [part["preview"] for part in delivery["excludedNarrativeSegments"]] == ["正在安排调研。"]
    assert "not a final answer" not in json.dumps(delivery)


def test_final_text_does_not_hide_uncorrected_fragments_from_the_final_model():
    message = {"role": "assistant", "run_id": "current", "state": "completed", "nodes": [
        {"id": str(index), "kind": "narrative", "ownerStreamKey": f"chat:supervisor:text:final:segment:{index}",
         "finalized": True, "content": text} for index, text in enumerate(["旧片段", "完整答案"])
    ]}
    assert audit._extract_final_text([message], preferred_run_id="current") == "旧片段完整答案"


@pytest.mark.parametrize("node", [
    {"kind": "reasoning", "content": "private reasoning", "finalized": True},
    {"kind": "narrative", "content": "partial answer", "partial": True, "finalized": True},
    {"kind": "narrative", "content": "pending answer", "finalized": False},
])
def test_final_text_does_not_replace_missing_narrative_with_old_progress_or_reasoning(node):
    old = {"role": "assistant", "run_id": "current", "state": "completed", "ordinal": 1, "content_text": "旧的过程说明"}
    current = {"role": "assistant", "run_id": "current", "state": "completed", "ordinal": 2,
               "nodes": [node], "content_text": "旧的过程说明", "reasoning_text": "private reasoning"}
    assert audit._extract_final_text([old, current], preferred_run_id="current") == ""
    assert audit._extract_message_text({"reasoning_text": "private reasoning", "metadata": {"summary": "not delivery"}}) == ""


@pytest.mark.parametrize("text,expected", [
    ("用户原始提问中的 GB 45440 为笔误，应为 GB 45438-2025。", True),
    ("你输入的 GB45440 是错误编号。", True),
    ("Supervisor 派生 brief 的 GB 45440 是笔误，已改成 GB 45438。", False),
    ("GB 45440 是派生 brief 的错误，用户并未提供这个编号。", False),
    ("GB 45440 的笔误并非用户引入。", False),
])
def test_known_research_fixture_does_not_blame_user_for_derived_brief_number(text, expected):
    result = audit.LiveCaseResult(spec=audit._case_specs(audit.RESEARCH_DELEGATED_VERIFICATION_CASE_ID)[0], final_text=text)
    assert bool(audit._research_fixture_user_attribution_errors(result)) is expected
    findings = audit._delegated_research_verification_findings(result)
    assert any(item.severity == "P2" and "笔误" in item.summary for item in findings) is expected


def test_attribution_oracle_is_fixture_scoped_and_respects_actual_user_prompt():
    spec = deepcopy(audit._case_specs(audit.RESEARCH_DELEGATED_VERIFICATION_CASE_ID)[0])
    result = audit.LiveCaseResult(spec=spec, final_text="用户原始提问中的 GB 45440 为笔误。")
    spec.prompt += "我说的是 GB 45440。"
    assert audit._research_fixture_user_attribution_errors(result) == []
    spec.case_id = "unrelated_case"
    spec.prompt = "other task"
    assert audit._research_fixture_user_attribution_errors(result) == []


def test_attribution_oracle_checks_research_handoff_even_when_supervisor_corrects_it(monkeypatch):
    result = audit.LiveCaseResult(spec=audit._case_specs(audit.RESEARCH_DELEGATED_VERIFICATION_CASE_ID)[0], final_text="编号由 Supervisor 引入。")
    monkeypatch.setattr(audit, "_research_handoff_payloads", lambda _: [{"answer": "用户原始提问中的 GB 45440 为笔误。"}])
    monkeypatch.setattr(audit, "_research_handoff_answer", lambda payload: payload["answer"])
    assert audit._research_fixture_user_attribution_errors(result)[0]["surface"] == "research_handoff"


def test_delegated_research_does_not_accept_provider_tool_markup_as_final_delivery():
    result = audit.LiveCaseResult(
        spec=audit._case_specs(audit.RESEARCH_DELEGATED_VERIFICATION_CASE_ID)[0],
        status="completed", final_text='已复核。<tool_call><invoke name="delegation_broker">not a native call</invoke>',
    )
    findings = audit._delegated_research_verification_findings(result)
    assert any("工具协议文本" in item.summary for item in findings)


def test_verification_proof_rejects_mirror_relabeling_and_unbound_success():
    payload = {"claimTable": [
        {"claimId": f"C{index}", "supportingSources": [
            {"citationKey": f"S{index}", "url": f"https://mirror.example.org/document-{index}"}
        ]} for index in range(1, 4)
    ]}
    proof = "\n".join(f"C{i} [S{i}] https://mirror.example.org/document-{i} 转载，已核验" for i in range(1, 4))
    assert audit._verification_binding_audit(proof, [payload])["passed"] is True
    forged = proof.replace("https://mirror.example.org/document-3", "https://official.gov.cn/original")
    diagnostic = audit._verification_binding_audit(forged, [payload])
    assert diagnostic["passed"] is False
    assert diagnostic["mismatches"] == ["C3/S3:source_url_mismatch"]
    assert audit._verification_binding_audit("Verification Engineer 全部验证成功。" * 1000, [payload])["passed"] is False
    assert audit._verification_binding_audit(proof, [])["passed"] is False


def test_verification_audit_does_not_grade_navigation_as_a_verified_conclusion():
    payload = {"claimTable": [{"claimId": f"read_S{i}:R1", "supportingSources": [
        {"citationKey": f"S{i}", "url": f"https://mirror.example/document-{i}"}
    ]} for i in range(1, 4)]}
    navigation = "| claimId | [S#] | URL |\n|---|---|---|\n| read_S1:R1 | [S1] | https://mirror.example/... |\n\n"
    verified = "| claimId | [S#] | URL | 核验结论 |\n|---|---|---|---|\n" + "\n".join(
        f"| read_S{i}:R1 | [S{i}] | https://mirror.example/document-{i} | 部分支持，限制已说明 |" for i in range(1, 4))
    assert audit._verification_binding_audit(navigation + verified, [payload])["passed"] is True
    assert audit._verification_binding_audit(navigation, [payload])["passed"] is False
    mutant = verified.replace("https://mirror.example/document-1", "https://official.example/original")
    assert audit._verification_binding_audit(navigation + mutant, [payload])["passed"] is False


def test_delivery_coverage_accepts_paraphrase_but_rejects_missing_domains_and_unread_urls():
    urls = [f"https://official-{i}.gov.cn/document" for i in range(5)]
    text = ("截至2026年9月3日，生成式人工智能服务管理暂行办法、互联网信息服务深度合成管理规定、"
            "人工智能生成合成内容标识办法和GB 45438-2025的适用关系、义务和上线清单如下。"
            "与原答案不同，复核已更正结论并保留未核实事项。\n" + "\n".join(urls))
    report = audit._delegated_research_delivery_coverage(text, urls)
    assert report["passed"] is True
    assert report["semanticTruthAssessed"] is False
    for removed in ("生成式人工智能服务管理暂行办法", "GB 45438-2025", "未核实", "上线清单", urls[0]):
        assert audit._delegated_research_delivery_coverage(text.replace(removed, ""), urls)["passed"] is False
    assert audit._delegated_research_delivery_coverage(text, urls[1:])["passed"] is False
    assert audit._delegated_research_delivery_coverage("全文见Research。" + "\n".join(urls), urls)["passed"] is False


def test_research_handoff_assessment_recomputes_instead_of_trusting_forged_metrics():
    answer = "A sufficiently long-looking answer body " * 300
    payload = {
        "kind": "research_evidence_bundle",
        "status": "ready",
        "deliveryReady": True,
        "coverageComplete": True,
        "reviewDecision": "accept",
        "qualityTier": "high_quality",
        "answer": answer,
        "answerSha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "sources": [
            {
                "sourceId": f"source-{index}",
                "citationKey": f"[S{index}]",
                "url": f"https://source-{index}.example/report",
                "selectedForEvidence": True,
            }
            for index in range(1, 9)
        ],
        "qualityMetrics": {
            "effectiveAnswerChars": 9000,
            "selectedSourceCount": 8,
            "distinctHostCount": 8,
            "retrievedSourceCount": 8,
            "freshRetrievedSourceCount": 8,
            "readVerifiedSourceCount": 8,
            "datedSourceCount": 8,
            "claimCount": 8,
            "uniqueClaimCount": 8,
            "supportedClaimCount": 8,
            "evidenceVerifiedClaimCount": 8,
            "claimSupportedSourceCount": 8,
            "answerCitedSourceCount": 8,
            "answerCitedContentUnitCount": 8,
            "asOfCurrent": True,
            "independentReviewAccepted": True,
        },
        "taskBriefResults": [
            {
                "taskBriefId": "research-1",
                "query": "current compliance facts",
                "status": "ready",
                "acceptancePassed": True,
                "reviewDecision": "accept",
                "qualityTier": "high_quality",
                "answer": answer,
                "answerSha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            }
        ],
    }

    assessment = audit._research_handoff_assessment(payload, question="current compliance facts")

    assert assessment["highQuality"] is False
    assert "recomputed_high_quality" in assessment["failedChecks"]
    assert "advertised_metrics_match_recomputed" in assessment["failedChecks"]
    assert assessment["qualityIssues"]
    assert assessment["advertisedMetricMismatches"]


def test_research_handoff_assessment_accepts_complete_projected_review_binding():
    question = "Verify the current compliance timeline with fresh primary evidence."
    raw = _accepted_research_payload(
        "bundle-valid",
        question,
        fixture_time=datetime.now(timezone.utc),
    )
    answer = raw["researchAnswerPack"]["answer"]
    sources = raw["researchAnswerPack"]["sources"]
    claims = raw["researchAnswerPack"]["claimTable"]
    review = raw["independentReview"]
    model_synthesis = raw["finalExperiencePack"]["modelSynthesis"]
    metrics = research_acceptance_metrics(raw)
    unit = {
        "taskBriefId": "research-1",
        "query": question,
        "status": "ready",
        "acceptancePassed": True,
        "reviewDecision": "accept",
        "qualityTier": "high_quality",
        "freshness": "current",
        "asOf": raw["asOf"],
        "answer": answer,
        "answerSha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "sources": sources,
        "sourceUrls": [source["url"] for source in sources],
        "claimTable": claims,
        "independentReview": review,
        "modelSynthesis": model_synthesis,
        "experienceReuse": raw["experienceReuse"],
        "forceRefreshRequested": True,
        "qualityMetrics": metrics,
        "criticalMissingEvidence": [],
        "recommendedNextQueries": [],
    }
    payload = {
        "kind": "research_evidence_bundle",
        "status": "ready",
        "deliveryReady": True,
        "coverageComplete": True,
        "reviewDecision": "accept",
        "qualityTier": "high_quality",
        "asOf": raw["asOf"],
        "answer": answer,
        "answerSha256": unit["answerSha256"],
        "sources": sources,
        "sourceUrls": unit["sourceUrls"],
        "claimTable": claims,
        "independentReview": review,
        "modelSynthesis": model_synthesis,
        "experienceReuse": raw["experienceReuse"],
        "forceRefreshRequested": True,
        "qualityMetrics": metrics,
        "taskBriefResults": [unit],
    }

    assessment = audit._research_handoff_assessment(payload, question=question)

    assert assessment["highQuality"] is True, assessment
    assert assessment["failedChecks"] == []
    assert assessment["qualityIssues"] == []
    assert assessment["advertisedMetricMismatches"] == {}


def test_pure_research_audit_recognizes_exact_dates_semantics_and_all_direct_web_tools():
    text = (
        "截至 2026 年 7 月 29 日，GPAI 通用目的 AI 的合规时间线包含 2025-08-02、"
        "2026 年 8 月 2 日和 2 August 2027。系统性风险门槛是 10^25 FLOP，并应分别说明"
        "透明度、版权、模型文档、既有模型过渡规则、Code of Practice、罚款执法、"
        "法规原文、欧盟委员会与 AI Office 指南和行业实践仍待明确之处，最后提供上线前可执行清单。"
    )

    dates = audit._normalized_date_evidence(text)
    coverage = audit._pure_research_semantic_coverage(text)

    assert {"2026-07-29", "2025-08-02", "2026-08-02", "2027-08-02"}.issubset(dates)
    assert all(coverage.values())
    assert {"web_search", "web_broker", "web_read", "web_fetch", "web_extract"}.issubset(
        audit.SUPERVISOR_DIRECT_WEB_TOOLS
    )


def test_terminal_probe_does_not_treat_missing_local_run_as_completed(monkeypatch):
    fake_db = SimpleNamespace(
        get_run_record=lambda _run_id: None,
        list_runtime_episodes=lambda **_kwargs: [],
    )
    monkeypatch.setattr(database_module, "db", fake_db)
    result = audit.LiveCaseResult(
        spec=audit.LiveCaseSpec(case_id="profile-affinity", title="profile affinity", prompt="test"),
        session_id="session-on-remote-engine",
        run_id="run-not-in-local-profile",
    )

    terminal, facts = audit._load_run_terminal(result)

    assert terminal is False
    assert facts["runRecordFound"] is False
    assert facts["runRecordMissing"] is True


def test_terminal_probe_treats_interrupted_run_and_cancelled_episode_as_terminal(monkeypatch):
    fake_db = SimpleNamespace(
        get_run_record=lambda _run_id: {
            "status": "interrupted",
            "finished_at": "2026-09-03T00:09:14Z",
            "error_message": "live audit interrupted the run",
        },
        list_runtime_episodes=lambda **_kwargs: [
            {"episodeId": "delegation-1", "kind": "delegation", "state": "cancelled"}
        ],
        list_runtime_episode_handoffs=lambda _episode_id: [],
    )
    monkeypatch.setattr(database_module, "db", fake_db)
    result = audit.LiveCaseResult(
        spec=audit.LiveCaseSpec(case_id="interrupted", title="interrupted", prompt="test"),
        session_id="session-interrupted",
        run_id="run-interrupted",
    )

    terminal, facts = audit._load_run_terminal(result)

    assert terminal is True
    assert facts["runStatus"] == "interrupted"
    assert facts["activeEpisodes"] == []


def test_poll_case_uses_matching_api_terminal_when_local_profile_has_no_run(monkeypatch):
    class FakeClock:
        now = 0.0

        def time(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = FakeClock()
    responses = iter(
        [
            {
                "events": [
                    {
                        "seq": 7,
                        "topic": "run.completed",
                        "run_id": "run-remote",
                        "payload": {"status": "finished", "reason": "stream_finished"},
                    }
                ]
            },
            {"events": []},
            {"events": []},
            {"events": []},
        ]
    )
    monkeypatch.setattr(audit.time, "time", clock.time)
    monkeypatch.setattr(audit.time, "perf_counter", clock.time)
    monkeypatch.setattr(audit.time, "sleep", clock.sleep)
    monkeypatch.setattr(audit, "_json_request", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(
        audit,
        "_load_run_terminal",
        lambda _result: (False, {"runRecordFound": False, "runRecordMissing": True}),
    )
    monkeypatch.setattr(audit, "_load_durable_runtime_events", lambda _result: ([], None))
    monkeypatch.setattr(audit, "_load_durable_episode_facts", lambda _result: ([], [], None))
    monkeypatch.setattr(audit, "_load_canonical_messages", lambda _result: ([], None))
    result = audit.LiveCaseResult(
        spec=audit.LiveCaseSpec(case_id="profile-affinity", title="profile affinity", prompt="test"),
        session_id="session-on-remote-engine",
        run_id="run-remote",
        status="submitted",
    )

    completed = audit._poll_case("http://127.0.0.1:19532", result, max_wait=30)

    assert completed.status == "completed"
    assert completed.failure_reason is None
    assert "api_events" in " ".join(completed.key_events)
    assert completed.poll_elapsed_ms == 3000
    assert completed.latency_ms is None  # Never confuse submission with run duration.


def test_api_terminal_ignores_other_run_and_preserves_remote_failure():
    terminal, facts = audit._api_run_terminal_facts(
        [
            {
                "seq": 8,
                "topic": "run.completed",
                "run_id": "run-old",
                "payload": {"status": "finished"},
            },
            {
                "seq": 9,
                "topic": "run.state.changed",
                "run_id": "run-current",
                "payload": {"from_status": "running", "to_status": "failed", "reason": "provider timeout"},
            },
        ],
        run_id="run-current",
    )

    assert terminal is True
    assert facts["apiTerminalStatus"] == "failed"
    assert facts["apiTerminalError"] == "provider timeout"
    assert facts["apiTerminalRunId"] == "run-current"


def test_api_terminal_recognizes_interrupted_topic():
    terminal, facts = audit._api_run_terminal_facts(
        [
            {
                "seq": 11,
                "topic": "run.interrupted",
                "run_id": "run-current",
                "payload": {"reason": "bounded live audit stop"},
            }
        ],
        run_id="run-current",
    )

    assert terminal is True
    assert facts["apiTerminalStatus"] == "interrupted"
    assert facts["apiTerminalError"] == "bounded live audit stop"
