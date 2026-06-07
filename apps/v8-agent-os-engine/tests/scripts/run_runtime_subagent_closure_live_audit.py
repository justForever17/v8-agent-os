from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[2]
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))
DEFAULT_MODEL_FALLBACKS = ["doubao-seed-2.0-pro", "mimo2.5pro", "deepseek-v4-flash"]
TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "canceled"}
ACTIVE_QUEUE_STATES = ["pending", "promoted", "queued"]
TOKEN_RE = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._\-]+|((?:api[_-]?key|token|cookie|authorization)[\"'\s:=]+)[^\"'\s,;]+"
)

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


@dataclass
class LiveCaseSpec:
    case_id: str
    title: str
    prompt: str
    expect_kinds: list[str] = field(default_factory=list)
    expect_handoff_markers: list[str] = field(default_factory=list)
    forbid_topics: list[str] = field(default_factory=list)
    allow_degraded: bool = True
    followup_prompt: str | None = None
    prefill_prompts: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class LiveCaseResult:
    spec: LiveCaseSpec
    status: str = "pending"
    session_id: str | None = None
    run_id: str | None = None
    model_profile: str | None = None
    latency_ms: int | None = None
    failure_reason: str | None = None
    observed_topics: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)
    episode_kinds: list[str] = field(default_factory=list)
    handoff_kinds: list[str] = field(default_factory=list)
    handoff_markers: list[str] = field(default_factory=list)
    key_events: list[str] = field(default_factory=list)
    degraded_count: int = 0
    repeated_failure_count: int = 0
    fake_agent_swarm_risk: bool = False
    context_governance_events: int = 0
    compaction_applied: bool = False
    compaction_reason: str | None = None


def _redact(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = TOKEN_RE.sub(lambda match: f"{match.group(1) or match.group(2)}[REDACTED]", text)
    for raw_path, replacement in ((Path.home(), "~"), (REPO_ROOT, "<REPO_ROOT>"), (ENGINE_ROOT, "<ENGINE_ROOT>")):
        path_text = str(raw_path)
        text = text.replace(path_text, replacement).replace(path_text.replace("\\", "\\\\"), replacement)
    return text


def _preview_text(value: str, *, limit: int = 240) -> str:
    text = value.replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _case_dry_run_summary(case: LiveCaseSpec) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "title": case.title,
        "promptPreview": _preview_text(case.prompt),
        "expect_kinds": case.expect_kinds,
        "expect_handoff_markers": case.expect_handoff_markers,
        "allow_degraded": case.allow_degraded,
        "followupPromptPreview": _preview_text(case.followup_prompt) if case.followup_prompt else None,
        "prefillCount": len(case.prefill_prompts),
        "prefillChars": sum(len(item) for item in case.prefill_prompts),
        "dataKeys": sorted(case.data.keys()),
    }


def _json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def _engine_api_base(engine_url: str) -> str:
    base = engine_url.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def _engine_root_url(engine_url: str) -> str:
    base = engine_url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _wait_for_engine(engine_url: str, *, timeout: float = 20.0) -> tuple[bool, str | None]:
    deadline = time.time() + timeout
    last_error: str | None = None
    while time.time() < deadline:
        try:
            _json_request(f"{_engine_root_url(engine_url)}/health", timeout=3)
            return True, None
        except Exception as exc:  # noqa: BLE001 - live audit preserves connectivity diagnostics.
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.75)
    return False, last_error


def _case_specs(selected: str) -> list[LiveCaseSpec]:
    compaction_filler = (
        "上下文压缩压力样本。请只回复“收到”，不要调用工具、不要规划、不要请求审批。\n"
        + "\n".join(
            [
                (
                    f"普通历史片段 {idx}: 这是一段中性的历史背景文本，用来模拟较长会话中的旧内容。"
                    "它不包含执行请求、不要求写文件、不要求调研、不要求委派，也不要求访问外部系统。"
                    "请把它当作需要在后续上下文压缩中被提炼或折叠的普通聊天历史。"
                )
                for idx in range(40)
            ]
        )
    )
    cases = [
        LiveCaseSpec(
            case_id="mixed_runtime_chain",
            title="Research + Engineering + Delegation 混合长任务",
            prompt=(
                "请规划一个不含 Computer Use/RPA 的 V8OS 主链优化任务：需要先做多源调研，"
                "再产出工程执行方案，并派一个子代理复核风险。允许降级 handoff，但不要真实写项目文件。"
            ),
            expect_kinds=["research", "engineering", "delegation"],
            expect_handoff_markers=["evidence", "work_plan", "delegation"],
        ),
        LiveCaseSpec(
            case_id="engineering_plan_only",
            title="plan-only 工程压测不应因无写入 worker 失败",
            prompt=(
                "执行一次工程压测：只输出执行地图和阶段状态，不需要真实写文件、不运行构建、"
                "不安装依赖。请通过 Engineering Runtime 产出 plan_only work_plan_ready。"
            ),
            expect_kinds=["engineering"],
            expect_handoff_markers=["work_plan_ready", "plan_only"],
        ),
        LiveCaseSpec(
            case_id="engineering_continuation",
            title="同 session 模糊报错续接 Engineering",
            prompt="先为一个小型 TypeScript 项目产出工程修复方案，不真实写文件，只返回 proof 预期。",
            followup_prompt="刚才那轮运行后还是报错：TypeScript 提示 unused import 和 route handler timeout，继续定位。",
            expect_kinds=["engineering"],
            expect_handoff_markers=["continuation", "work_plan", "proof"],
        ),
        LiveCaseSpec(
            case_id="multi_skill_contract",
            title="huashu-nuwa + skill-creator 多 skill 合同",
            prompt=(
                "使用 huashu-nuwa 和 skill-creator 的规范，设计一个生成人物视角 skill 的执行计划。"
                "只要求读取/续读合同并规划，不要真实写文件。"
            ),
            expect_kinds=["engineering", "delegation"],
            expect_handoff_markers=["skill", "skill-creator", "huashu-nuwa"],
            data={
                "skillReferences": [
                    {"name": "huashu-nuwa", "path": str(Path.home() / ".agents" / "skills" / "huashu-nuwa")},
                    {"name": "skill-creator"},
                ],
                "contextMentions": [
                    {"kind": "skill", "name": "huashu-nuwa"},
                    {"kind": "skill", "name": "skill-creator"},
                ],
            },
        ),
        LiveCaseSpec(
            case_id="schedule_workspace_scope",
            title="短延迟定时任务绑定当前 workspace",
            prompt=(
                "创建一个 1 分钟内触发的一次性定时任务，内容是提醒我检查 Runtime/Subagent 闭环验收报告。"
                "任务必须绑定当前 workspace，并标记 automation/cron 来源。"
            ),
            expect_kinds=["automation"],
            expect_handoff_markers=["workspace", "automation", "cron"],
        ),
        LiveCaseSpec(
            case_id="delegation_degraded",
            title="故意不完整子代理任务应 degraded handoff",
            prompt=(
                "请尝试委派一个子代理任务，但我故意不给完整任务内容。"
                "系统应该只产生 delegation_missing_tasks 或 delegation_degraded 诊断，不要显示假的真实 Agent Swarm。"
            ),
            expect_kinds=["delegation"],
            expect_handoff_markers=["delegation_missing_tasks", "delegation_degraded", "missing_tasks"],
        ),
        LiveCaseSpec(
            case_id="context_compaction_runtime_resume",
            title="上下文压缩介入后仍能续接 runtime episode/handoff",
            prompt=(
                "基于前面很长的历史，请继续执行一条必须经过 runtime_broker(route) 的 plan_only 验收："
                "1) 创建/路由 Engineering episode，deliverableKind=plan_only、writeRequired=false，产出 work_plan_ready handoff；"
                "2) 创建/路由 Delegation/Subagent episode，派一个真实子代理复核风险，若无法派发必须返回 delegation_degraded handoff；"
                "3) 重点验证 context compaction 介入后 route context、episode、typed handoff 没丢。"
                "不要只写 todo 或直接在 Supervisor 文本里完成。"
            ),
            prefill_prompts=[compaction_filler for _ in range(8)],
            expect_kinds=["engineering", "delegation"],
            expect_handoff_markers=["context.prepared", "work_plan", "delegation"],
            data={"requireContextGovernance": True, "preferContextCompaction": True},
        ),
    ]
    if selected == "all":
        return cases
    return [case for case in cases if case.case_id == selected]


def _submit_message(
    engine_url: str,
    *,
    session_id: str,
    prompt: str,
    workspace: str,
    model_profile: str,
    case: LiveCaseSpec,
    suffix: str = "",
    live_audit: bool = True,
    history_prompts: list[str] | None = None,
) -> tuple[str | None, int, dict[str, Any]]:
    request_data: dict[str, Any] = {
        "conversationId": session_id,
        "modelProfile": model_profile,
    }
    if live_audit:
        request_data.update(
            {
                "runtimeSubagentClosureLiveAudit": True,
                **case.data,
            }
        )
    else:
        # Prefill turns only create durable history for compaction tests.
        # Keep them out of Planner/runtime routing so the test does not
        # accidentally validate a synthetic filler message instead of the
        # final runtime request.
        request_data.update(
            {
                "taskPlanningMode": False,
                "plannerMode": "off",
                "plannerDispatchMode": "suggest",
                "disableExtensionsPrefilter": True,
            }
        )
    messages = [{"role": "user", "content": item} for item in list(history_prompts or [])]
    messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": f"{case.case_id}{suffix}-{int(time.time() * 1000)}",
        "stream": False,
        "workspacePath": workspace,
        "messages": messages,
        "data": request_data,
    }
    started = time.perf_counter()
    response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    latency_ms = int((time.perf_counter() - started) * 1000)
    run_id = str(response.get("run_id") or response.get("runId") or "") or None
    return run_id, latency_ms, response


def _compact_submit_response(response: dict[str, Any]) -> dict[str, Any]:
    user_message = response.get("userMessage") if isinstance(response.get("userMessage"), dict) else {}
    queued_message = response.get("queuedMessage") if isinstance(response.get("queuedMessage"), dict) else {}
    return {
        "accepted": bool(response.get("accepted")),
        "queued": bool(response.get("queued")),
        "sessionId": response.get("session_id") or response.get("sessionId") or response.get("conversationId"),
        "runId": response.get("run_id") or response.get("runId"),
        "clientMessageId": response.get("clientMessageId"),
        "userMessageId": user_message.get("id"),
        "queuedMessageId": queued_message.get("id"),
        "queuedState": queued_message.get("state"),
    }


def _session_idle_state(session_id: str) -> tuple[bool, dict[str, Any]]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}
    try:
        runs = db.list_run_records(session_id=session_id, run_type="chat", limit=20)
        active_runs: list[dict[str, Any]] = []
        for item in runs:
            status = str(item.get("status") or "").strip().lower()
            if status in TERMINAL_RUN_STATES:
                continue
            run_summary: dict[str, Any] = {"id": item.get("id"), "status": item.get("status")}
            if status == "waiting_approval":
                review = _latest_model_review_summary(session_id=session_id, run_id=str(item.get("id") or ""))
                if review:
                    run_summary["approval"] = review
            active_runs.append(run_summary)
        queue_items = db.list_chat_user_message_queue(
            session_id=session_id,
            states=ACTIVE_QUEUE_STATES,
            limit=20,
        )
        active_queue = [
            {"id": item.get("id"), "state": item.get("state"), "runId": item.get("run_id") or item.get("runId")}
            for item in queue_items
        ]
        return not active_runs and not active_queue, {
            "activeRuns": active_runs,
            "activeQueue": active_queue,
            "latestRun": {"id": runs[0].get("id"), "status": runs[0].get("status")} if runs else None,
        }
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}


def _latest_model_review_summary(*, session_id: str, run_id: str) -> dict[str, Any]:
    if not session_id or not run_id:
        return {}
    try:
        from core.database import db

        events = db.get_runtime_events_for_run(run_id, session_id=session_id, limit=100)
    except Exception:
        return {}
    for event in reversed(events):
        if str(event.get("topic") or "").strip() != "approval.requested":
            continue
        payload = _event_payload(event)
        if not isinstance(payload, dict):
            continue
        request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        details = request.get("details") if isinstance(request.get("details"), dict) else {}
        attempts = [item for item in list(details.get("attempts") or []) if isinstance(item, dict)]
        last_attempt = attempts[-1] if attempts else {}
        return {
            "kind": payload.get("approval_kind") or payload.get("approvalKind") or request.get("approvalKind"),
            "code": last_attempt.get("code"),
            "message": _preview_text(str(last_attempt.get("message") or request.get("question") or ""), limit=240),
            "modelId": last_attempt.get("modelId") or details.get("preferredModelId"),
            "providerId": last_attempt.get("providerId"),
        }
    return {}


def _wait_for_session_idle(session_id: str, *, timeout: int) -> tuple[bool, dict[str, Any]]:
    deadline = time.time() + max(5, timeout)
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        idle, state = _session_idle_state(session_id)
        last_state = state
        if idle:
            return True, state
        if _state_has_model_quota_block(state):
            return False, state
        time.sleep(2)
    return False, last_state


def _state_has_model_quota_block(state: dict[str, Any]) -> bool:
    for run in list(state.get("activeRuns") or []):
        if not isinstance(run, dict):
            continue
        approval = run.get("approval") if isinstance(run.get("approval"), dict) else {}
        text = " ".join(
            str(approval.get(key) or "")
            for key in ("kind", "code", "message", "modelId", "providerId")
        ).lower()
        if "model_review" in text and ("quota" in text or "rate_limit" in text or "usage_limit" in text):
            return True
    return False


def _load_durable(result: LiveCaseResult) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str | None]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        return [], [], [], f"{type(exc).__name__}: {exc}"
    events: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    try:
        if result.session_id:
            events.extend(db.get_runtime_events(result.session_id))
            episodes.extend(db.list_runtime_episodes(session_id=result.session_id, limit=200))
        if result.run_id:
            events.extend(db.get_runtime_events_for_run(result.run_id, session_id=result.session_id, limit=500))
            episodes.extend(db.list_runtime_episodes(run_id=result.run_id, limit=200))
        seen_episodes: set[str] = set()
        unique_episodes: list[dict[str, Any]] = []
        for episode in episodes:
            episode_id = str(episode.get("episodeId") or episode.get("id") or episode.get("needId") or "").strip()
            if not episode_id or episode_id in seen_episodes:
                continue
            seen_episodes.add(episode_id)
            unique_episodes.append(episode)
            handoffs.extend(db.list_runtime_episode_handoffs(episode_id))
        return events, unique_episodes, handoffs, None
    except Exception as exc:  # noqa: BLE001
        return events, episodes, handoffs, f"{type(exc).__name__}: {exc}"


def _event_payload(event: dict[str, Any]) -> Any:
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload
    return payload


def _summarize_result(result: LiveCaseResult) -> None:
    events, episodes, handoffs, error = _load_durable(result)
    if error:
        result.key_events.append(_redact({"durableLookupError": error}))
    topics: list[str] = []
    failure_topics = 0
    for event in events:
        topic = str(event.get("topic") or event.get("type") or event.get("event_type") or "").strip()
        if topic and topic not in topics:
            topics.append(topic)
        if "recoverable" in topic or topic.endswith(".failed"):
            failure_topics += 1
        if topic == "context.prepared":
            result.context_governance_events += 1
            payload = _event_payload(event)
            if isinstance(payload, dict):
                if bool(payload.get("compaction_applied") or payload.get("compactionApplied")):
                    result.compaction_applied = True
                durable_flush = payload.get("durable_flush") if isinstance(payload.get("durable_flush"), dict) else {}
                reason = str(
                    payload.get("trigger_reason")
                    or payload.get("triggerReason")
                    or durable_flush.get("reason")
                    or ""
                ).strip()
                if reason:
                    result.compaction_reason = reason
    result.observed_topics = topics[:200]
    result.repeated_failure_count = max(0, failure_topics - 1)

    for episode in episodes:
        episode_id = str(episode.get("episodeId") or episode.get("id") or episode.get("needId") or "").strip()
        kind = str(episode.get("kind") or episode.get("runtimeKind") or "").strip()
        if episode_id and episode_id not in result.episode_ids:
            result.episode_ids.append(episode_id)
        if kind and kind not in result.episode_kinds:
            result.episode_kinds.append(kind)
        if not str(episode.get("session_id") or episode.get("sessionId") or "").strip():
            result.key_events.append(_redact({"unboundEpisode": episode_id, "kind": kind}))
    for row in handoffs:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        kind = str((payload or {}).get("kind") or "").strip()
        status = str((payload or {}).get("status") or "").strip().lower()
        summary = str((payload or {}).get("compactSummary") or (payload or {}).get("summary") or "")
        marker_text = f"{kind} {status} {summary}".lower()
        if marker_text and marker_text not in result.handoff_markers:
            result.handoff_markers.append(marker_text)
        if kind and kind not in result.handoff_kinds:
            result.handoff_kinds.append(kind)
        if status == "degraded" or "degraded" in marker_text or "missing_tasks" in marker_text:
            result.degraded_count += 1
    if "delegation" in result.episode_kinds and result.degraded_count and not any("subagent.task." in topic for topic in topics):
        result.fake_agent_swarm_risk = False


def _evaluate(result: LiveCaseResult) -> None:
    spec = result.spec
    if result.status == "failed":
        return
    missing_kinds = [
        kind
        for kind in spec.expect_kinds
        if kind not in result.episode_kinds and not any(kind in handoff for handoff in result.handoff_kinds)
    ]
    marker_text = " ".join([*result.handoff_kinds, *result.handoff_markers, *result.observed_topics, *result.key_events]).lower()
    missing_markers = [marker for marker in spec.expect_handoff_markers if marker.lower() not in marker_text]
    repeated_runtime_failure = result.repeated_failure_count >= 2
    require_context = bool(spec.data.get("requireContextGovernance"))
    if missing_kinds or (missing_markers and not spec.allow_degraded) or repeated_runtime_failure:
        result.status = "failed"
        result.failure_reason = _redact(
            {
                "missingKinds": missing_kinds,
                "missingMarkers": missing_markers,
                "repeatedFailureCount": result.repeated_failure_count,
                "contextGovernanceEvents": result.context_governance_events,
            }
        )
        return
    if require_context and result.context_governance_events <= 0:
        result.status = "failed"
        result.failure_reason = "context_governance_event_missing"
        return
    if require_context and not result.compaction_applied:
        result.status = "degraded"
        result.failure_reason = _redact(
            {
                "reason": "context_governance_seen_but_compaction_not_applied",
                "triggerReason": result.compaction_reason,
            }
        )
        return
    if missing_markers:
        result.status = "degraded"
        result.failure_reason = _redact({"missingMarkers": missing_markers})
        return
    result.status = "passed"


def _run_case(
    engine_url: str,
    *,
    case: LiveCaseSpec,
    workspace: str,
    model_profile: str,
    timestamp: str,
    max_wait: int,
) -> LiveCaseResult:
    session_id = f"runtime-subagent-closure-{timestamp}-{case.case_id}"
    result = LiveCaseResult(spec=case, session_id=session_id, model_profile=model_profile)
    try:
        if case.prefill_prompts:
            result.key_events.append(
                _redact(
                    {
                        "historyContextInjected": True,
                        "historyMessageCount": len(case.prefill_prompts),
                        "historyChars": sum(len(item) for item in case.prefill_prompts),
                    }
                )
            )
        run_id, latency_ms, response = _submit_message(
            engine_url,
            session_id=session_id,
            prompt=case.prompt,
            workspace=workspace,
            model_profile=model_profile,
            case=case,
            history_prompts=case.prefill_prompts,
        )
        result.run_id = run_id
        result.latency_ms = latency_ms
        result.key_events.append(_redact({"submitResponse": _compact_submit_response(response)}))
        if case.followup_prompt:
            idle, idle_state = _wait_for_session_idle(session_id, timeout=max_wait)
            result.key_events.append(_redact({"beforeFollowupIdle": idle, "idleState": idle_state}))
            if not idle:
                result.status = "failed"
                result.failure_reason = _redact({"reason": "initial_run_or_queue_not_idle_before_followup", "idleState": idle_state})
                return result
            followup_run_id, _, followup_response = _submit_message(
                engine_url,
                session_id=session_id,
                prompt=case.followup_prompt,
                workspace=workspace,
                model_profile=model_profile,
                case=case,
                suffix="-followup",
            )
            result.run_id = followup_run_id or result.run_id
            result.key_events.append(_redact({"followupSubmitResponse": _compact_submit_response(followup_response)}))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failure_reason = _redact(f"{type(exc).__name__}: {exc}")
        return result

    deadline = time.time() + max(5, max_wait)
    while time.time() < deadline:
        _summarize_result(result)
        if result.episode_ids and (result.handoff_kinds or result.degraded_count):
            break
        time.sleep(2)
    if bool(case.data.get("requireContextGovernance")) and bool(case.data.get("preferContextCompaction")):
        compaction_deadline = time.time() + min(90, max(15, max_wait // 3))
        while time.time() < compaction_deadline:
            _summarize_result(result)
            if result.compaction_applied:
                break
            time.sleep(3)
    idle, idle_state = _wait_for_session_idle(session_id, timeout=min(120, max(20, max_wait // 2)))
    result.key_events.append(_redact({"postRunIdle": idle, "idleState": idle_state}))
    if idle:
        time.sleep(3)
    _summarize_result(result)
    _evaluate(result)
    return result


def _write_report(results: list[LiveCaseResult], *, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "runtime_subagent_closure_results.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "caseId": item.spec.case_id,
                    "title": item.spec.title,
                    "status": item.status,
                    "sessionId": item.session_id,
                    "runId": item.run_id,
                    "modelProfile": item.model_profile,
                    "latencyMs": item.latency_ms,
                    "failureReason": item.failure_reason,
                    "episodeIds": item.episode_ids,
                    "episodeKinds": item.episode_kinds,
                    "handoffKinds": item.handoff_kinds,
                    "handoffMarkers": item.handoff_markers[:40],
                    "observedTopics": item.observed_topics,
                    "degradedCount": item.degraded_count,
                    "repeatedFailureCount": item.repeated_failure_count,
                    "contextGovernanceEvents": item.context_governance_events,
                    "compactionApplied": item.compaction_applied,
                    "compactionReason": item.compaction_reason,
                    "keyEvents": item.key_events,
                }
                for item in results
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    lines = [
        "# Runtime/Subagent Closure Live Audit",
        "",
        f"- Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"- Cases: {len(results)}",
        "",
        "| Case | Status | Model | Episodes | Handoffs | Failure |",
        "|---|---:|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    item.spec.case_id,
                    item.status,
                    item.model_profile or "",
                    ", ".join(item.episode_kinds) or "-",
                    ", ".join(item.handoff_kinds) or "-",
                    (item.failure_reason or "").replace("\n", " ")[:180] or "-",
                ]
            )
            + " |"
        )
    lines.extend(["", f"Raw JSON: `{json_path}`", ""])
    md_path = output_dir / "RUNTIME_SUBAGENT_CLOSURE_LIVE_AUDIT_ZH.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def _safe_session_suffix(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    return cleaned[:48] or "model"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live Runtime/Subagent closure audit for V8OS.")
    parser.add_argument("--live", action="store_true", help="Actually submit live sessions to Engine.")
    parser.add_argument("--case", default="all", choices=[
        "all",
        "mixed_runtime_chain",
        "engineering_plan_only",
        "engineering_continuation",
        "multi_skill_contract",
        "schedule_workspace_scope",
        "delegation_degraded",
        "context_compaction_runtime_resume",
    ])
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", default=str(REPO_ROOT))
    parser.add_argument("--model-profile", default=DEFAULT_MODEL_FALLBACKS[0])
    parser.add_argument("--model-fallbacks", default=",".join(DEFAULT_MODEL_FALLBACKS))
    parser.add_argument("--max-wait", type=int, default=240)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    cases = _case_specs(args.case)
    if not args.live:
        print(json.dumps({"live": False, "cases": [_case_dry_run_summary(case) for case in cases]}, ensure_ascii=False, indent=2))
        return 0

    ok, error = _wait_for_engine(args.engine_url)
    if not ok:
        print(f"[runtime-subagent-live] Engine unavailable: {error}", file=sys.stderr)
        return 2

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    model_fallbacks = [item.strip() for item in str(args.model_fallbacks or "").split(",") if item.strip()]
    if args.model_profile and args.model_profile not in model_fallbacks:
        model_fallbacks.insert(0, args.model_profile)

    results: list[LiveCaseResult] = []
    for case in cases:
        last_result: LiveCaseResult | None = None
        for model_profile in model_fallbacks:
            print(f"[runtime-subagent-live] {case.case_id} using {model_profile}")
            result = _run_case(
                args.engine_url,
                case=case,
                workspace=args.workspace,
                model_profile=model_profile,
                timestamp=f"{timestamp}-{_safe_session_suffix(model_profile)}",
                max_wait=args.max_wait,
            )
            last_result = result
            if result.status in {"passed", "degraded"}:
                break
            if "quota" not in str(result.failure_reason or "").lower() and "rate" not in str(result.failure_reason or "").lower():
                break
        results.append(last_result or LiveCaseResult(spec=case, status="failed", failure_reason="not_run"))

    if args.write_report:
        output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_REPORT_ROOT / "runtime_subagent_closure" / timestamp
        report_path = _write_report(results, output_dir=output_dir)
        print(f"[runtime-subagent-live] report: {report_path}")

    print(json.dumps({"results": [{"caseId": r.spec.case_id, "status": r.status, "failure": r.failure_reason} for r in results]}, ensure_ascii=False, indent=2))
    return 0 if all(result.status in {"passed", "degraded"} for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
