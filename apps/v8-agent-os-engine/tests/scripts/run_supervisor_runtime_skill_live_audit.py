from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[2]
DEFAULT_ENGINE_URL = "http://127.0.0.1:9530"
DEFAULT_REPORT_ROOT = Path(os.environ.get("V8_AGENT_OS_REPORTS_ROOT") or (Path.home() / ".v8-agent-os" / "reports"))
HUASHU_NUWA_SKILL_ROOT = Path.home() / ".agents" / "skills" / "huashu-nuwa"
TOKEN_RE = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._\-]+|((?:api[_-]?key|token|cookie|authorization)[\"'\s:=]+)[^\"'\s,;]+"
)
PURE_RESEARCH_CASE_ID = "pure_research_delivery"
PURE_RESEARCH_MIN_EFFECTIVE_CHARS = 3_000
PURE_RESEARCH_MIN_SOURCE_COUNT = 5
PURE_RESEARCH_TARGET_EFFECTIVE_CHARS = 5_000
PURE_RESEARCH_TARGET_SOURCE_COUNT = 8
PURE_RESEARCH_TARGET_DISTINCT_HOST_COUNT = 5
PURE_RESEARCH_TARGET_CLAIM_COUNT = 8
PURE_RESEARCH_TARGET_DATED_SOURCE_COUNT = 5
SUPERVISOR_DIRECT_WEB_TOOLS = {"web_search", "web_broker", "web_read", "web_fetch", "web_extract"}

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


@dataclass
class LiveCaseSpec:
    case_id: str
    title: str
    prompt: str
    expected_any_tools: list[str] = field(default_factory=list)
    expected_all_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    source_required: bool = False
    skill_required: bool = False
    forbid_runtime_episodes: bool = False
    expected_episode_kinds: list[str] = field(default_factory=list)
    explicit_degradation_ok: bool = False
    skill_references: list[dict[str, Any]] = field(default_factory=list)
    context_mentions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AuditFinding:
    severity: str
    case_id: str
    title: str
    summary: str
    evidence: str
    modules: list[str] = field(default_factory=list)
    recommended_fix: str = ""
    regression_test: str = ""


@dataclass
class LiveCaseResult:
    spec: LiveCaseSpec
    session_id: str | None = None
    run_id: str | None = None
    status: str = "pending"
    latency_ms: int | None = None
    failure_reason: str | None = None
    actual_tools: list[str] = field(default_factory=list)
    observed_topics: list[str] = field(default_factory=list)
    key_events: list[str] = field(default_factory=list)
    canonical_messages: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    episodes: list[dict[str, Any]] = field(default_factory=list)
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    tool_invocations: list[dict[str, Any]] = field(default_factory=list)
    research_completed_seq: int | None = None


def _redact(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    text = TOKEN_RE.sub(lambda match: f"{match.group(1) or match.group(2)}[REDACTED]", text)
    for raw_path, replacement in (
        (Path.home(), "~"),
        (REPO_ROOT, "<REPO_ROOT>"),
        (ENGINE_ROOT, "<ENGINE_ROOT>"),
        (HUASHU_NUWA_SKILL_ROOT, "<HUASHU_NUWA_SKILL>"),
    ):
        path_text = str(raw_path)
        text = text.replace(path_text, replacement).replace(path_text.replace("\\", "\\\\"), replacement)
    return text


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        if not raw.strip():
            return {}
        return json.loads(raw)


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
        except Exception as exc:  # noqa: BLE001 - diagnostic script reports exact connectivity failures.
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.75)
    return False, last_error


def _ensure_explicit_live_workspace_trusted(workspace: Path) -> tuple[bool, dict[str, Any]]:
    """Trust and inspect only the disposable workspace supplied to this live side-effect harness.

    Product trust remains unchanged.  This helper is called only for the case
    that already requires ``--allow-side-effects``; normal Engine execution
    never adopts an arbitrary workspace implicitly.
    """

    event: dict[str, Any] = {"workspacePath": str(workspace), "action": "unchanged"}
    if not workspace.exists() or not workspace.is_dir():
        event["error"] = "workspace_missing_or_not_directory"
        return False, event
    try:
        from core.workspace_authority import workspace_authority_service
        from runtimes.memory.project_registry import project_registry_service

        project = project_registry_service.find_project_for_workspace(workspace_path=str(workspace))
        trust_state = str(getattr(project, "workspace_trust_state", "") or "").strip().lower() if project else ""
        if project is None or trust_state != "trusted":
            project = project_registry_service.save_project(
                {
                    "name": "Supervisor joint live harness workspace",
                    "workspacePath": str(workspace),
                    "workspaceTrustState": "trusted",
                    "workspaceTrustSource": "user_confirmed_live_harness",
                    "tags": ["live_harness", "supervisor_joint_runtime"],
                }
            )
            event["action"] = "registered_trusted_project"
        else:
            event["action"] = "already_trusted"
        event["projectId"] = str(getattr(project, "project_id", "") or "")
        event["workspaceId"] = str(getattr(project, "workspace_id", "") or "")
        from core.engineering_sandbox.service import get_engineering_sandbox_service

        sandbox = get_engineering_sandbox_service()
        repository = sandbox.project_repository_status(
            workspace_root=str(workspace),
            project_id=event["projectId"] or None,
        )
        parallel_isolation = dict(repository.get("parallelIsolation") or {})
        event["repository"] = {
            "state": ((repository.get("repository") or {}).get("state")),
            "setupRequired": bool(parallel_isolation.get("setupRequired")),
            "parallelIsolationEnabled": bool(parallel_isolation.get("enabled")),
            "directExecutionAvailable": bool(parallel_isolation.get("directExecutionAvailable", True)),
        }
        authority = workspace_authority_service.resolve(
            runtime_kind="engineering",
            explicit_workspace_path=str(workspace),
            explicit_project_id=event["projectId"] or None,
        )
        event["authority"] = authority.as_dict()
        return bool(authority.side_effects_allowed), event
    except Exception as exc:  # noqa: BLE001 - live harness must preserve the preflight failure.
        event["error"] = f"{type(exc).__name__}: {exc}"
        return False, event


def _default_model_profile_label() -> str:
    config_path = Path.home() / ".v8-agent-os" / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return "engine-default"
    models = config.get("models") if isinstance(config, dict) else None
    if not isinstance(models, dict):
        return "engine-default"
    default = models.get("default") or models.get("active") or models.get("chat")
    if isinstance(default, str) and default.strip():
        return default.strip()
    if isinstance(default, dict):
        for key in ("model", "modelId", "modelName", "profile", "id"):
            value = default.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "engine-default"


def _huashu_skill_reference() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not HUASHU_NUWA_SKILL_ROOT.exists():
        return [], []
    reference = {
        "id": "huashu-nuwa",
        "name": "huashu-nuwa",
        "description": "女娲造人：深度调研并生成可运行的人物/主题视角 Skill。",
        "path": str(HUASHU_NUWA_SKILL_ROOT),
        "sourceType": "local",
    }
    mention = {
        "kind": "skill",
        "id": "huashu-nuwa",
        "name": "huashu-nuwa",
        "label": "huashu-nuwa",
        "description": reference["description"],
        "path": str(HUASHU_NUWA_SKILL_ROOT),
        "sourceType": "local",
    }
    return [reference], [mention]


def _case_specs(selected_case: str) -> list[LiveCaseSpec]:
    skill_refs, mentions = _huashu_skill_reference()
    cases = [
        LiveCaseSpec(
            case_id="simple_doc",
            title="简单短文应由 Supervisor 直接完成",
            prompt="只在聊天里写一段 300 字以内的 V8OS 简短说明，不保存文件、不调研、不调用工程运行时。",
            forbidden_tools=["runtime_broker", "delegation_broker", "write_native_file", "run_system_command"],
            forbid_runtime_episodes=True,
        ),
        LiveCaseSpec(
            case_id="ambiguous_doc",
            title="模糊写文档应先澄清",
            prompt="帮我写一篇文档。",
            forbidden_tools=["write_native_file", "run_system_command"],
        ),
        LiveCaseSpec(
            case_id="weather",
            title="天气查询必须有实时来源或明确降级",
            prompt="请查一下上海今天的天气和出门建议，必须说明来源或说明无法获取实时来源。",
            expected_any_tools=["web_broker", "research_broker"],
            source_required=True,
            explicit_degradation_ok=True,
        ),
        LiveCaseSpec(
            case_id="huashu_plan",
            title="huashu-nuwa Skill 应先读取 skill instructions",
            prompt=(
                "使用已选择的 huashu-nuwa skill，给我做一次女娲造人的执行计划：目标是蒸馏一个"
                "“测试人物视角”，只输出计划和需要的资料，不写文件、不创建 skill。"
            ),
            expected_all_tools=["fetch_skill_instructions"],
            skill_required=True,
            skill_references=skill_refs,
            context_mentions=mentions,
        ),
        LiveCaseSpec(
            case_id="huashu_video_gap",
            title="huashu-nuwa 视频能力缺口应变通或降级",
            prompt=(
                "使用已选择的 huashu-nuwa skill。假设用户只有一个本地视频文件但没有字幕，也没有单独配置"
                " gemini-video API key；不要访问真实文件、不要写文件。请说明你会如何用 V8OS 内置视觉/附件/"
                "字幕/调研能力变通，哪些条件缺失必须向用户确认。不要假装已经分析视频。"
            ),
            expected_all_tools=["fetch_skill_instructions"],
            skill_required=True,
            explicit_degradation_ok=True,
            skill_references=skill_refs,
            context_mentions=mentions,
        ),
        LiveCaseSpec(
            case_id="source_write",
            title="来源型写作应 Research evidence 后成稿",
            prompt="联网查找 2-3 个来源，写一段关于 V8OS runtime-first 设计价值的短报告，必须保留来源，不保存文件。",
            expected_any_tools=["research_broker", "runtime_broker"],
            source_required=True,
            explicit_degradation_ok=True,
        ),
    ]
    if selected_case == PURE_RESEARCH_CASE_ID:
        return [
            LiveCaseSpec(
                case_id=PURE_RESEARCH_CASE_ID,
                title="纯调研应由 Research 形成高质量证据并由 Supervisor 完整交付",
                prompt=(
                    "这是纯调研任务，不写文件、不执行工程修改。请交给深度调研，回答：截至 2026 年 7 月 29 日，"
                    "欧盟《人工智能法案》对通用目的 AI 模型（GPAI）提供者的合规时间线、系统性风险门槛、"
                    "透明度/版权/模型文档义务、既有模型过渡规则、GPAI Code of Practice 的法律作用及执法罚则"
                    "分别是什么？请严格区分法规原文、欧盟委员会或 AI Office 后续指南、行业实践和仍待明确事项，"
                    "并给出面向 2026 年下半年准备上线或继续运营模型团队的可执行清单。最终回答必须真正回答问题，"
                    "提供明确的截至日期和时效证据；至少保留 5 个可访问来源、3000 个有效字符，这是拒绝线，不是"
                    "质量目标。正常目标是至少 8 个独立可读来源、5000 个有效字符、8 条来源支撑的关键结论；信息"
                    "不够就继续获取来源和细节，不得凑字、重复或捏造。Supervisor 应直接消费深度调研回流的完整"
                    "证据答案，不要再自行调用 web_search/web_broker/web_read 做二次搜索。本次是实际 live 验收，"
                    "不得复用既有经验包；深度调研运行必须在 inputs 或 taskBrief context 中设置 "
                    "forceRefresh=true。"
                ),
                expected_any_tools=["research_broker", "runtime_broker"],
                expected_episode_kinds=["research"],
                source_required=True,
            )
        ]
    if selected_case == "joint_research_delivery":
        return [
            LiveCaseSpec(
                case_id="joint_research_delivery",
                title="多主题时效调研应连续进入 Research 与 Engineering",
                prompt=(
                    "团队准备重做一个本地数据兼容性基线。请查清 2026 年当前 SQLite FTS5、"
                    "SQLite JSONB 和 Python 在 Windows 上的官方现状，再在当前工作区留下让同事"
                    "可以一键复现、机器读取并继续扩展的最小基线。别只给建议，依据、能运行的东西、"
                    "验证结果和关键取舍都整理好，不要改别的项目。"
                ),
                expected_episode_kinds=["research", "engineering"],
                source_required=True,
            )
        ]
    if selected_case == "all":
        return cases
    return [case for case in cases if case.case_id == selected_case]


def _submit_case(
    engine_url: str,
    *,
    case: LiveCaseSpec,
    model_profile: str,
    timestamp: str,
    workspace: str,
) -> LiveCaseResult:
    result = LiveCaseResult(spec=case)
    session_id = f"supervisor-runtime-skill-live-{timestamp}-{case.case_id}"
    client_message_id = f"{case.case_id}-{timestamp}"
    payload: dict[str, Any] = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": client_message_id,
        "stream": False,
        "workspacePath": workspace,
        "messages": [{"role": "user", "content": case.prompt}],
        "data": {
            "conversationId": session_id,
            "clientMessageId": client_message_id,
            "supervisorRuntimeSkillLiveAudit": True,
            "modelProfile": model_profile,
            "skillReferences": case.skill_references or None,
            "contextMentions": case.context_mentions or None,
        },
    }
    if case.case_id == PURE_RESEARCH_CASE_ID:
        payload["data"].update(
            {
                "supervisorWorkMode": "daily",
                "supervisorRuntimeMode": "research",
                "engineeringMode": "off",
            }
        )
    started = time.perf_counter()
    try:
        response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    except Exception as exc:  # noqa: BLE001 - diagnostic script must preserve exact failure.
        result.status = "failed"
        result.session_id = session_id
        result.failure_reason = _redact(f"{type(exc).__name__}: {exc}")
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    result.session_id = str(response.get("session_id") or response.get("sessionId") or session_id)
    run_id = response.get("run_id") or response.get("runId")
    result.run_id = str(run_id) if run_id else None
    result.status = "submitted"
    result.key_events.append(_redact({"submitResponse": response}))
    return result


def _event_topic(event: dict[str, Any]) -> str:
    return str(event.get("topic") or event.get("event_type") or event.get("type") or "").strip()


def _event_payload(event: dict[str, Any]) -> Any:
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload
    return payload


def _collect_tool_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in {"tool", "tool_name", "toolname", "function_name"} and isinstance(item, str):
                if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]{2,}$", item):
                    names.add(item)
            if normalized == "function" and isinstance(item, dict):
                function_name = item.get("name")
                if isinstance(function_name, str) and re.match(r"^[a-zA-Z_][a-zA-Z0-9_]{2,}$", function_name):
                    names.add(function_name)
            names.update(_collect_tool_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_collect_tool_names(item))
    return names


def _tool_invocation_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    topic = _event_topic(event)
    if topic != "tool.started" and not topic.endswith(".tool.started"):
        return None
    payload = _event_payload(event)
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool") if isinstance(payload.get("tool"), dict) else payload
    tool_name = str(tool.get("toolName") or tool.get("tool_name") or tool.get("name") or "").strip()
    if not tool_name:
        return None
    try:
        seq = int(event.get("seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    return {
        "seq": seq,
        "topic": topic,
        "toolName": tool_name,
        "toolCallId": str(
            tool.get("toolCallId")
            or tool.get("tool_call_id")
            or payload.get("toolCallId")
            or payload.get("tool_call_id")
            or ""
        ).strip(),
        "ownerRuntimeId": str(payload.get("ownerRuntimeId") or payload.get("runtimeId") or "").strip(),
        "ownerAgentKind": str(payload.get("ownerAgentKind") or "").strip(),
        "ownerAgentId": str(payload.get("ownerAgentId") or "").strip(),
        "displayInMessage": payload.get("displayInMessage"),
    }


def _collect_tool_invocations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    invocations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        invocation = _tool_invocation_from_event(event)
        if not invocation:
            continue
        identity = str(invocation.get("toolCallId") or "").strip() or (
            f"{invocation.get('seq')}:{invocation.get('topic')}:{invocation.get('toolName')}"
        )
        if identity in seen:
            continue
        seen.add(identity)
        invocations.append(invocation)
    return invocations


def _research_completion_seq(events: list[dict[str, Any]], research_episode_ids: set[str]) -> int | None:
    completed: list[int] = []
    for event in events:
        if _event_topic(event) != "runtime.episode.completed":
            continue
        payload = _event_payload(event)
        if not isinstance(payload, dict):
            continue
        episode = payload.get("episode") if isinstance(payload.get("episode"), dict) else {}
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        episode_kind = str(episode.get("kind") or "").strip().lower()
        if episode_kind != "research" and episode_id not in research_episode_ids:
            continue
        try:
            completed.append(int(event.get("seq") or 0))
        except (TypeError, ValueError):
            continue
    return max(completed) if completed else None


def _append_unique(target: list[str], values: list[str], *, limit: int = 120) -> None:
    seen = set(target)
    for value in values:
        if value and value not in seen:
            target.append(value)
            seen.add(value)
        if len(target) >= limit:
            break


def _load_durable_runtime_events(result: LiveCaseResult) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001 - diagnostic script should record import failures.
        return [], f"{type(exc).__name__}: {exc}"
    events: list[dict[str, Any]] = []
    try:
        if result.session_id:
            events.extend(db.get_runtime_events(result.session_id))
        if result.run_id:
            events.extend(db.get_runtime_events_for_run(result.run_id, session_id=result.session_id, limit=500))
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"
    deduped: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = str(event.get("id") or event.get("event_id") or f"{event.get('session_id')}:{event.get('seq')}:{event.get('topic')}")
        deduped[event_id] = event
    return sorted(deduped.values(), key=lambda item: int(item.get("seq") or 0)), None


def _load_durable_episode_facts(result: LiveCaseResult) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        return [], [], f"{type(exc).__name__}: {exc}"
    episodes: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    try:
        episodes = db.list_runtime_episodes(session_id=result.session_id, run_id=result.run_id, limit=500)
        episode_ids = [str(item.get("episodeId") or item.get("id") or "") for item in episodes if item.get("episodeId") or item.get("id")]
        for episode_id in episode_ids:
            handoffs.extend(db.list_runtime_episode_handoffs(episode_id))
    except TypeError:
        try:
            with db.get_connection() as conn:
                clauses: list[str] = []
                params: list[Any] = []
                if result.session_id:
                    clauses.append("session_id = ?")
                    params.append(result.session_id)
                if result.run_id:
                    clauses.append("run_id = ?")
                    params.append(result.run_id)
                if clauses:
                    rows = conn.execute(
                        f"""
                        SELECT id, kind, state, session_id, run_id, parent_episode_id, root_episode_id,
                               error_code, error_message, result_ref, last_progress, worker_id
                        FROM runtime_episodes
                        WHERE {" OR ".join(clauses)}
                        ORDER BY created_at
                        """,
                        tuple(params),
                    ).fetchall()
                    episodes = [dict(row) for row in rows]
                    episode_ids = [str(item.get("id") or "") for item in episodes if item.get("id")]
                    if episode_ids:
                        placeholders = ",".join("?" for _ in episode_ids)
                        rows = conn.execute(
                            f"""
                            SELECT id, episode_id, kind, status, compact_summary, raw_ref, detail_tool, payload_json
                            FROM runtime_episode_handoffs
                            WHERE episode_id IN ({placeholders})
                            ORDER BY created_at
                            """,
                            tuple(episode_ids),
                        ).fetchall()
                        handoffs = [dict(row) for row in rows]
        except Exception as exc:  # noqa: BLE001
            return [], [], f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        return [], [], f"{type(exc).__name__}: {exc}"
    return episodes, handoffs, None


def _load_canonical_messages(result: LiveCaseResult) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from core.database import db
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"
    try:
        return db.get_chat_canonical_messages(result.session_id or ""), None
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"


def _load_run_terminal(result: LiveCaseResult) -> tuple[bool, dict[str, Any]]:
    facts: dict[str, Any] = {}
    try:
        from core.database import db

        if result.run_id:
            record = db.get_run_record(result.run_id)
            facts["runRecordFound"] = bool(record)
            record = record or {}
            facts["runStatus"] = record.get("status")
            facts["runFinishedAt"] = record.get("finished_at") or record.get("finishedAt")
            facts["runError"] = record.get("error_message") or record.get("errorMessage")
    except Exception as exc:  # noqa: BLE001
        facts["runRecordError"] = f"{type(exc).__name__}: {exc}"
    episodes, _handoffs, error = _load_durable_episode_facts(result)
    if error:
        facts["episodeError"] = error
    terminal_episode_states = {"completed", "failed", "cancelled", "canceled", "merged"}
    active_episodes = [
        item
        for item in episodes
        if str(item.get("state") or item.get("status") or "").lower() not in terminal_episode_states
    ]
    facts["activeEpisodes"] = [
        {
            "episodeId": item.get("episodeId") or item.get("id"),
            "kind": item.get("kind"),
            "state": item.get("state"),
        }
        for item in active_episodes
    ]
    run_status = str(facts.get("runStatus") or "").lower()
    if result.run_id and facts.get("runRecordFound") is not True:
        facts["runRecordMissing"] = True
        return False, facts
    if result.run_id and run_status and run_status not in {"completed", "failed", "cancelled", "canceled", "succeeded", "success"}:
        return False, facts
    if result.run_id and run_status in {"completed", "succeeded", "success"}:
        return True, facts
    if active_episodes:
        return False, facts
    return True, facts


def _api_run_terminal_facts(
    events: list[dict[str, Any]],
    *,
    run_id: str | None,
) -> tuple[bool, dict[str, Any]]:
    """Read the matching run's terminal state from remotely polled events."""

    target_run_id = str(run_id or "").strip()
    terminal_statuses = {"completed", "failed", "cancelled", "canceled", "succeeded", "success"}
    latest: dict[str, Any] = {}
    for event in events:
        payload = _event_payload(event)
        payload = payload if isinstance(payload, dict) else {}
        event_run_id = str(
            event.get("run_id")
            or event.get("runId")
            or payload.get("run_id")
            or payload.get("runId")
            or ""
        ).strip()
        if target_run_id and event_run_id != target_run_id:
            continue
        topic = _event_topic(event).lower()
        status = ""
        if topic == "run.state.changed":
            status = str(
                payload.get("to_status")
                or payload.get("toStatus")
                or payload.get("status")
                or payload.get("state")
                or payload.get("nextState")
                or ""
            ).strip().lower()
            if status not in terminal_statuses:
                continue
        elif topic == "run.completed":
            status = "completed"
        elif topic == "run.failed":
            status = "failed"
        elif topic in {"run.cancelled", "run.canceled"}:
            status = "cancelled"
        else:
            continue
        try:
            seq = int(event.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        if latest and seq < int(latest.get("apiTerminalSeq") or 0):
            continue
        latest = {
            "apiTerminalObserved": True,
            "apiTerminalTopic": topic,
            "apiTerminalStatus": status,
            "apiTerminalSeq": seq,
            "apiTerminalRunId": event_run_id or target_run_id,
            "apiTerminalError": str(
                payload.get("error")
                or payload.get("error_message")
                or payload.get("errorMessage")
                or (payload.get("reason") if status in {"failed", "cancelled", "canceled"} else "")
                or ""
            ).strip(),
        }
    return bool(latest), latest


def _poll_case(engine_url: str, result: LiveCaseResult, *, max_wait: float) -> LiveCaseResult:
    if not result.session_id or result.status == "failed":
        return result
    after_seq = 0
    start = time.time()
    last_event_at = start
    terminal_seen_at: float | None = None
    api_terminal_facts: dict[str, Any] = {}
    while time.time() - start < max_wait:
        query = f"?after_seq={after_seq}" if after_seq else ""
        try:
            response = _json_request(
                f"{_engine_api_base(engine_url)}/sessions/{result.session_id}/runtime-events{query}",
                timeout=10,
            )
            events = response.get("events") or response.get("items") or []
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            result.key_events.append(_redact({"runtimeEventsPollError": f"{type(exc).__name__}: {exc}"}))
            events = []
        if events:
            last_event_at = time.time()
        for event in events:
            try:
                seq = int(event.get("seq") or 0)
                after_seq = max(after_seq, seq)
            except Exception:
                pass
            topic = _event_topic(event)
            if topic:
                _append_unique(result.observed_topics, [topic])
            payload = _event_payload(event)
            if _event_carries_tool_result(topic):
                _append_unique(result.actual_tools, sorted(_collect_tool_names(payload)))
            if topic in {
                "agent.started",
                "agent.completed",
                "agent.failed",
                "tool.call",
                "tool.result",
                "runtime.episode.queued",
                "runtime.episode.started",
                "runtime.episode.completed",
                "runtime.episode.failed",
                "handoff.ref.created",
                "human_guidance.injected",
                "run.resume.scheduling",
                "run.resume.scheduled",
                "run.resume.not_scheduled",
            }:
                result.key_events.append(_redact({"topic": topic, "payload": payload})[:1600])
        api_terminal, observed_api_facts = _api_run_terminal_facts(events, run_id=result.run_id)
        if api_terminal:
            api_terminal_facts = observed_api_facts
        local_terminal, facts = _load_run_terminal(result)
        terminal = local_terminal or bool(api_terminal_facts)
        if api_terminal_facts:
            facts = {**facts, **api_terminal_facts, "terminalSource": "local_db" if local_terminal else "api_events"}
        if terminal and terminal_seen_at is None:
            terminal_seen_at = time.time()
        if terminal and (time.time() - last_event_at > 2 or (terminal_seen_at is not None and time.time() - terminal_seen_at > 5)):
            result.status = "completed"
            result.key_events.append(_redact({"terminalFacts": facts})[:1600])
            break
        time.sleep(1.0)
    else:
        result.status = "timeout"
        local_terminal, facts = _load_run_terminal(result)
        terminal = local_terminal or bool(api_terminal_facts)
        if api_terminal_facts:
            facts = {**facts, **api_terminal_facts, "terminalSource": "local_db" if local_terminal else "api_events"}
        result.failure_reason = "run_or_episode_not_terminal_within_max_wait"
        result.key_events.append(_redact({"timeoutFacts": facts, "terminal": terminal})[:1600])

    durable_events, event_error = _load_durable_runtime_events(result)
    if event_error:
        result.key_events.append(_redact({"durableRuntimeEventsError": event_error}))
    for event in durable_events:
        topic = _event_topic(event)
        if topic:
            _append_unique(result.observed_topics, [topic])
        if _event_carries_tool_result(topic):
            _append_unique(result.actual_tools, sorted(_collect_tool_names(_event_payload(event))))
    result.tool_invocations = _collect_tool_invocations(durable_events)
    episodes, handoffs, episode_error = _load_durable_episode_facts(result)
    if episode_error:
        result.key_events.append(_redact({"durableEpisodesError": episode_error}))
    result.episodes = episodes
    result.handoffs = handoffs
    research_episode_ids = {
        str(item.get("episodeId") or item.get("id") or "").strip()
        for item in episodes
        if str(item.get("kind") or item.get("runtimeKind") or item.get("episodeKind") or "").strip().lower()
        == "research"
        and str(item.get("episodeId") or item.get("id") or "").strip()
    }
    result.research_completed_seq = _research_completion_seq(durable_events, research_episode_ids)
    _append_unique(result.actual_tools, sorted(_collect_handoff_tool_names(handoffs)))
    messages, message_error = _load_canonical_messages(result)
    if message_error:
        result.key_events.append(_redact({"canonicalMessagesError": message_error}))
    result.canonical_messages = messages
    result.final_text = _extract_final_text(
        messages,
        preferred_run_id=result.run_id,
        min_effective_chars=(
            PURE_RESEARCH_MIN_EFFECTIVE_CHARS
            if result.spec.case_id == PURE_RESEARCH_CASE_ID
            else 0
        ),
    )
    _terminal, terminal_facts = _load_run_terminal(result)
    run_status = str(terminal_facts.get("runStatus") or "").lower()
    if not run_status and api_terminal_facts:
        run_status = str(api_terminal_facts.get("apiTerminalStatus") or "").lower()
    if run_status in {"failed", "cancelled", "canceled"}:
        result.status = "failed"
        result.failure_reason = str(
            terminal_facts.get("runError")
            or api_terminal_facts.get("apiTerminalError")
            or f"run_status_{run_status}"
        )
        result.key_events.append(
            _redact({"finalRunStatus": {**terminal_facts, **api_terminal_facts}})[:1600]
        )
    return result


def _event_carries_tool_result(topic: str) -> bool:
    normalized = topic.lower()
    if "tool" in normalized:
        return True
    return normalized in {
        "extension.execution.completed",
        "extension.execution.failed",
        "mcp.tool.executed",
        "native_tool.executed",
        "delegation_broker.dispatch",
        "runtime_broker.route",
    }


def _collect_handoff_tool_names(handoffs: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()

    def _add(value: Any) -> None:
        name = str(value or "").strip().strip("'\"`\\")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\\-]*", name):
            names.add(name)

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = str(key or "").lower()
                if normalized_key in {"toolsused", "toolnames", "usedtools", "actualtools"}:
                    if isinstance(item, list):
                        for entry in item:
                            _add(entry)
                    else:
                        _add(item)
                elif normalized_key in {"tool", "toolname", "tool_name", "name"} and "tool" in normalized_key:
                    _add(item)
                _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    for handoff in list(handoffs or []):
        _walk(handoff)
        try:
            text = json.dumps(handoff, ensure_ascii=False)
        except TypeError:
            text = str(handoff)
        for match in re.finditer(r"使用工具\s*[:：]\s*([A-Za-z0-9_.,，、/\\ -]+)", text):
            raw = match.group(1).replace("\\n", " ").replace("\\r", " ")
            for part in re.split(r"[,，、\s]+", raw):
                name = part.strip().strip("'\"`\\")
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\\-]*", name):
                    names.add(name)
        for match in re.finditer(r'"(?:tool|toolName|tool_name)"\s*:\s*"([A-Za-z_][A-Za-z0-9_\\-]*)"', text):
            names.add(match.group(1))
    return sorted(names)


def _extract_message_text(message: dict[str, Any]) -> str:
    for key in ("content_text", "content", "text", "reasoning_text"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raw = message.get("metadata_json") or message.get("metadata")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if isinstance(raw, dict):
        for key in ("content", "text", "summary"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _extract_final_text(
    messages: list[dict[str, Any]],
    *,
    preferred_run_id: str | None = None,
    min_effective_chars: int = 0,
) -> str:
    candidates: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        role = str(message.get("role") or message.get("source") or "").lower()
        if role in {"assistant", "ai", "supervisor"}:
            text = _extract_message_text(message)
            if text:
                try:
                    ordinal = int(message.get("ordinal") or index)
                except (TypeError, ValueError):
                    ordinal = index
                candidates.append(
                    {
                        "text": text,
                        "ordinal": ordinal,
                        "runId": str(message.get("run_id") or message.get("runId") or "").strip(),
                        "state": str(message.get("state") or "").strip().lower(),
                        "finalized": bool(message.get("finalized_at") or message.get("finalizedAt")),
                    }
                )
    if not candidates:
        return ""
    completed = [
        item
        for item in candidates
        if item["state"] in {"completed", "complete", "final", "finalized"} or item["finalized"]
    ]
    if completed:
        candidates = completed
    normalized_run_id = str(preferred_run_id or "").strip()
    run_matched = [item for item in candidates if normalized_run_id and item["runId"] == normalized_run_id]
    if run_matched:
        candidates = run_matched
    selected = max(candidates, key=lambda item: int(item["ordinal"]))
    selected_text = str(selected["text"])
    progress_only = bool(
        _effective_answer_chars(selected_text) < min_effective_chars
        and not _explicit_degradation(selected_text)
        and re.search(
            r"handoff|回流|等待|处理中|继续执行|继续处理|已路由|runtime\s+episode|episode\s+(?:ready|completed)",
            selected_text,
            re.I,
        )
    )
    if min_effective_chars > 0 and progress_only:
        delivery_candidates = [
            item
            for item in candidates
            if _effective_answer_chars(str(item["text"])) >= min_effective_chars
        ]
        if delivery_candidates:
            selected = max(delivery_candidates, key=lambda item: int(item["ordinal"]))
    return str(selected["text"])


def _mentions_source(text: str) -> bool:
    return bool(re.search(r"https?://|来源|参考|source|according to|weather\.com|中国天气|气象|duckduckgo|bing|google", text, re.I))


def _explicit_degradation(text: str) -> bool:
    return bool(
        re.search(
            r"无法|不能|需要|请提供|缺少|未配置|没有权限|没有访问|无法获取|无法分析|降级|fallback|degrad|"
            r"cannot|unable|need(?:s|ed)?|must provide|required|missing|not configured|"
            r"no actual|not performed|was not performed|blocking|blocker|workaround",
            text,
            re.I,
        )
    )


def _has_research_evidence_path(result: LiveCaseResult) -> bool:
    if "research_broker" in set(result.actual_tools):
        return True
    for episode in result.episodes:
        kind = str(episode.get("kind") or episode.get("runtimeKind") or episode.get("episodeKind") or "").strip().lower()
        if kind == "research":
            return True
    observed = " ".join(
        [
            *result.observed_topics,
            *result.key_events,
            json.dumps(result.handoffs, ensure_ascii=False, default=str),
        ]
    )
    return bool(
        re.search(
            r"runtime\.episode\..*research|research_evidence_bundle|researchResult|claimTable|sourceMatrix|Web Research Architect",
            observed,
            re.I,
        )
    )


def _handoff_payload(handoff: dict[str, Any]) -> dict[str, Any]:
    payload = handoff.get("payload")
    if isinstance(payload, dict):
        return payload
    raw = handoff.get("payload_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _research_handoff_payloads(result: LiveCaseResult) -> list[dict[str, Any]]:
    research_episode_ids = {
        str(item.get("episodeId") or item.get("id") or "").strip()
        for item in result.episodes
        if str(item.get("kind") or item.get("runtimeKind") or item.get("episodeKind") or "").strip().lower()
        == "research"
        and str(item.get("episodeId") or item.get("id") or "").strip()
    }
    payloads: list[dict[str, Any]] = []
    for handoff in result.handoffs:
        episode_id = str(handoff.get("episode_id") or handoff.get("episodeId") or "").strip()
        payload = _handoff_payload(handoff)
        kind = str(payload.get("kind") or handoff.get("kind") or "").strip().lower()
        if episode_id in research_episode_ids or kind in {"research_evidence_bundle", "research_result_pack"}:
            payloads.append(payload)
    return payloads


def _source_identities_from_urls(urls: list[str], *, question: str) -> set[str]:
    from core.tools.research_source_identity import research_document_identity

    identities: set[str] = set()
    for url in urls:
        identity = research_document_identity(url, question=question)
        if identity:
            identities.add(identity)
    return identities


def _visible_source_urls(text: str) -> list[str]:
    from core.tools.research_source_identity import canonical_source_url

    urls: list[str] = []
    for match in re.finditer(r"https?://[^\s<>\"'`]+", text or "", re.I):
        raw = match.group(0).rstrip(".,;:!?。，；：！？)]}>")
        normalized = canonical_source_url(raw)
        if normalized and normalized not in urls:
            urls.append(normalized)
    return urls


def _effective_answer_chars(text: str) -> int:
    from core.tools.research_quality import research_effective_answer_chars

    return research_effective_answer_chars({"answer": text})


def _research_handoff_assessment(payload: dict[str, Any], *, question: str) -> dict[str, Any]:
    from core.tools.research_quality import research_acceptance_metrics, research_high_quality_issues

    task_results = [item for item in list(payload.get("taskBriefResults") or []) if isinstance(item, dict)]
    primary_result = task_results[0] if len(task_results) == 1 else {}
    advertised_metrics = payload.get("qualityMetrics") if isinstance(payload.get("qualityMetrics"), dict) else {}
    if not advertised_metrics and isinstance(primary_result.get("qualityMetrics"), dict):
        advertised_metrics = primary_result.get("qualityMetrics") or {}
    answer = str(payload.get("answer") or primary_result.get("answer") or "").strip()
    sources = [item for item in list(payload.get("sources") or primary_result.get("sources") or []) if isinstance(item, dict)]
    claims = [
        item
        for item in list(payload.get("claimTable") or primary_result.get("claimTable") or [])
        if isinstance(item, dict)
    ]
    independent_review = (
        payload.get("independentReview")
        if isinstance(payload.get("independentReview"), dict)
        else primary_result.get("independentReview")
        if isinstance(primary_result.get("independentReview"), dict)
        else {}
    )
    model_synthesis = (
        payload.get("modelSynthesis")
        if isinstance(payload.get("modelSynthesis"), dict)
        else primary_result.get("modelSynthesis")
        if isinstance(primary_result.get("modelSynthesis"), dict)
        else {}
    )
    experience_reuse = (
        payload.get("experienceReuse")
        if isinstance(payload.get("experienceReuse"), dict)
        else primary_result.get("experienceReuse")
        if isinstance(primary_result.get("experienceReuse"), dict)
        else {}
    )
    temporal_assessment = (
        payload.get("temporalAssessment")
        if isinstance(payload.get("temporalAssessment"), dict)
        else primary_result.get("temporalAssessment")
        if isinstance(primary_result.get("temporalAssessment"), dict)
        else {}
    )
    source_urls = [
        str(item.get("url") or item.get("sourceUrl") or "").strip()
        for item in sources
        if str(item.get("url") or item.get("sourceUrl") or "").strip()
    ]
    source_urls.extend(
        str(item).strip()
        for item in list(payload.get("sourceUrls") or primary_result.get("sourceUrls") or [])
        if str(item).strip()
    )
    source_identities = _source_identities_from_urls(source_urls, question=question)
    critical_missing = [
        str(item).strip()
        for item in [
            *list(payload.get("criticalMissingEvidence") or []),
            *[
                gap
                for result in task_results
                for gap in list(result.get("criticalMissingEvidence") or [])
            ],
        ]
        if str(item).strip()
    ]
    recommended_queries = list(
        dict.fromkeys(
            str(item).strip()
            for item in [
                *list(payload.get("recommendedNextQueries") or []),
                *list(primary_result.get("recommendedNextQueries") or []),
                *list(independent_review.get("recommendedNextQueries") or []),
            ]
            if str(item).strip()
        )
    )
    review_decision = str(
        payload.get("reviewDecision") or primary_result.get("reviewDecision") or ""
    ).strip().lower()
    as_of = str(
        payload.get("asOf")
        or primary_result.get("asOf")
        or temporal_assessment.get("asOf")
        or ""
    ).strip()
    verification_payload = {
        "question": str(primary_result.get("query") or payload.get("query") or question).strip(),
        "freshness": primary_result.get("freshness") or payload.get("freshness") or "current",
        "asOf": as_of,
        "reviewDecision": review_decision,
        "independentReview": independent_review,
        "criticalMissingEvidence": critical_missing,
        "recommendedNextQueries": recommended_queries,
        "researchAnswerPack": {
            "answer": answer,
            "sources": sources,
            "claimTable": claims,
            "asOf": as_of,
            "reviewDecision": review_decision,
            "independentReview": independent_review,
            "criticalMissingEvidence": critical_missing,
            "recommendedNextQueries": recommended_queries,
        },
    }
    recomputed_metrics = research_acceptance_metrics(verification_payload)
    recomputed_issues = research_high_quality_issues(verification_payload)
    metric_mismatches = {
        key: {"advertised": advertised_metrics.get(key), "recomputed": value}
        for key, value in recomputed_metrics.items()
        if key not in advertised_metrics or advertised_metrics.get(key) != value
    }
    binding_keys = {
        "bindingVersion",
        "questionFingerprint",
        "answerSha256",
        "claimDigest",
        "sourceDigest",
        "temporalDigest",
        "reviewerModelId",
        "reviewedAt",
    }
    reuse_decision = str(experience_reuse.get("reuseDecision") or "").strip().lower()
    force_refresh_requested = bool(
        payload.get("forceRefreshRequested") is True
        or primary_result.get("forceRefreshRequested") is True
    )
    reuse_proven = bool(
        reuse_decision in {"ignore", "refresh", "reuse"}
        and str(experience_reuse.get("reason") or "").strip()
        and str(experience_reuse.get("topicFingerprint") or "").strip()
        and (
            reuse_decision != "reuse"
            or (
                str(experience_reuse.get("candidatePackId") or "").strip()
                and experience_reuse.get("skippedSearches") is True
            )
        )
    )
    synthesis_or_reuse_proven = bool(model_synthesis.get("used") is True or reuse_decision == "reuse")
    fresh_live_proven = bool(
        force_refresh_requested
        and reuse_decision != "reuse"
        and model_synthesis.get("used") is True
    )
    reviewer_model_consistent = bool(
        reuse_decision == "reuse"
        or (
            str(model_synthesis.get("reviewerModelId") or "").strip()
            and str(model_synthesis.get("reviewerModelId") or "").strip()
            == str(independent_review.get("reviewerModelId") or "").strip()
        )
    )
    expected_answer_digest = str(
        payload.get("answerSha256") or primary_result.get("answerSha256") or ""
    ).strip().lower()
    answer_digest_matches = bool(
        expected_answer_digest
        and expected_answer_digest == hashlib.sha256(answer.encode("utf-8")).hexdigest()
    )
    acceptance_passed = bool(task_results) and all(item.get("acceptancePassed") is True for item in task_results)
    delivery_ready = payload.get("deliveryReady") is True or (
        str(payload.get("status") or "").strip().lower() == "ready"
        and payload.get("coverageComplete") is True
        and acceptance_passed
    )
    checks = {
        "typed_research_handoff": str(payload.get("kind") or "").strip().lower() == "research_evidence_bundle",
        "delivery_ready": delivery_ready,
        "coverage_complete": payload.get("coverageComplete") is True,
        "review_accepted": review_decision == "accept",
        "quality_tier_high": str(payload.get("qualityTier") or primary_result.get("qualityTier") or "").strip().lower()
        == "high_quality",
        "recomputed_high_quality": not recomputed_issues,
        "advertised_metrics_match_recomputed": not metric_mismatches,
        "answer_at_target": int(recomputed_metrics.get("effectiveAnswerChars") or 0)
        >= PURE_RESEARCH_TARGET_EFFECTIVE_CHARS,
        "sources_at_target": int(recomputed_metrics.get("selectedSourceCount") or 0)
        >= PURE_RESEARCH_TARGET_SOURCE_COUNT,
        "distinct_hosts_at_target": int(recomputed_metrics.get("distinctHostCount") or 0)
        >= PURE_RESEARCH_TARGET_DISTINCT_HOST_COUNT,
        "claims_at_target": int(recomputed_metrics.get("uniqueClaimCount") or 0)
        >= PURE_RESEARCH_TARGET_CLAIM_COUNT,
        "supported_claims_complete": int(recomputed_metrics.get("supportedClaimCount") or 0)
        == int(recomputed_metrics.get("claimCount") or 0),
        "evidence_verified_claims_complete": int(recomputed_metrics.get("evidenceVerifiedClaimCount") or 0)
        == int(recomputed_metrics.get("claimCount") or 0),
        "claim_source_coverage_at_target": int(recomputed_metrics.get("claimSupportedSourceCount") or 0)
        >= PURE_RESEARCH_TARGET_SOURCE_COUNT,
        "answer_body_citations_at_target": int(recomputed_metrics.get("answerCitedSourceCount") or 0)
        >= PURE_RESEARCH_TARGET_SOURCE_COUNT,
        "answer_body_citation_spread_at_target": int(recomputed_metrics.get("answerCitedContentUnitCount") or 0)
        >= PURE_RESEARCH_TARGET_SOURCE_COUNT,
        "dated_sources_at_target": int(recomputed_metrics.get("datedSourceCount") or 0)
        >= PURE_RESEARCH_TARGET_DATED_SOURCE_COUNT,
        "retrieved_sources_at_target": int(recomputed_metrics.get("retrievedSourceCount") or 0)
        >= PURE_RESEARCH_TARGET_SOURCE_COUNT,
        "fresh_retrieved_sources_at_target": int(recomputed_metrics.get("freshRetrievedSourceCount") or 0)
        >= PURE_RESEARCH_TARGET_SOURCE_COUNT,
        "read_verified_sources_at_target": int(recomputed_metrics.get("readVerifiedSourceCount") or 0)
        >= PURE_RESEARCH_TARGET_SOURCE_COUNT,
        "independent_review_accepted": recomputed_metrics.get("independentReviewAccepted") is True,
        "review_binding_complete": binding_keys.issubset(independent_review),
        "reviewer_model_consistent": reviewer_model_consistent,
        "synthesis_or_reuse_proven": synthesis_or_reuse_proven,
        "fresh_live_proven": fresh_live_proven,
        "experience_reuse_proven": reuse_proven,
        "answer_digest_matches": answer_digest_matches,
        "as_of_current": recomputed_metrics.get("asOfCurrent") is True,
        "no_critical_missing_evidence": not critical_missing,
        "no_recommended_queries": not recommended_queries,
    }
    return {
        "highQuality": all(checks.values()),
        "failedChecks": [name for name, passed in checks.items() if not passed],
        "effectiveAnswerChars": int(recomputed_metrics.get("effectiveAnswerChars") or 0),
        "sourceCount": int(recomputed_metrics.get("selectedSourceCount") or 0),
        "sourceUrls": sorted(set(source_urls)),
        "sourceIdentities": sorted(source_identities),
        "qualityTier": payload.get("qualityTier") or primary_result.get("qualityTier"),
        "reviewDecision": payload.get("reviewDecision") or primary_result.get("reviewDecision"),
        "asOf": as_of,
        "qualityMetrics": recomputed_metrics,
        "qualityIssues": recomputed_issues,
        "advertisedMetricMismatches": metric_mismatches,
        "answerSha256": expected_answer_digest,
        "modelSynthesis": model_synthesis,
        "experienceReuse": experience_reuse,
        "forceRefreshRequested": force_refresh_requested,
        "independentReview": independent_review,
        "criticalMissingEvidence": critical_missing,
        "recommendedNextQueries": recommended_queries,
    }


def _is_supervisor_owned_invocation(invocation: dict[str, Any]) -> bool:
    owner_runtime = str(invocation.get("ownerRuntimeId") or "").strip().lower()
    owner_kind = str(invocation.get("ownerAgentKind") or "").strip().lower()
    owner_id = str(invocation.get("ownerAgentId") or "").strip().lower()
    if owner_runtime or owner_kind or owner_id:
        return owner_runtime == "chat" and owner_kind in {"", "supervisor"} and owner_id in {"", "supervisor"}
    # Older events did not carry owner fields. An unprefixed tool.started event
    # was the Supervisor message surface; runtime-owned calls use a prefixed topic.
    return str(invocation.get("topic") or "").strip().lower() == "tool.started"


def _normalized_date_evidence(text: str) -> set[str]:
    without_urls = re.sub(r"https?://[^\s<>\"'`]+", " ", text or "", flags=re.I)
    dates: set[str] = set()
    for match in re.finditer(
        r"(?<!\d)(20\d{2})\s*(?:[-/.]|年)\s*(\d{1,2})\s*(?:(?:[-/.]|月)\s*(\d{1,2})\s*日?)?",
        without_urls,
    ):
        year, month, day = match.groups()
        dates.add(f"{int(year):04d}-{int(month):02d}" + (f"-{int(day):02d}" if day else ""))
    month_names = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    month_pattern = "|".join(month_names)
    for match in re.finditer(
        rf"\b({month_pattern})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b|\b(\d{{1,2}})\s+({month_pattern})\s+(20\d{{2}})\b",
        without_urls,
        re.I,
    ):
        if match.group(1):
            month_name, day, year = match.group(1), match.group(2), match.group(3)
        else:
            day, month_name, year = match.group(4), match.group(5), match.group(6)
        dates.add(f"{int(year):04d}-{month_names[str(month_name).lower()]:02d}-{int(day):02d}")
    return dates


def _pure_research_semantic_coverage(text: str) -> dict[str, bool]:
    patterns = {
        "gpai_scope": r"通用目的\s*(?:AI|人工智能)|\bGPAI\b",
        "compliance_timeline": r"时间线|生效|开始适用|适用日期|合规期限",
        "systemic_risk_threshold": r"系统性风险|10\s*(?:\^|\*\*)?\s*25|10²⁵",
        "transparency": r"透明度|透明义务",
        "copyright": r"版权|著作权",
        "model_documentation": r"模型文档|技术文档|文档义务|下游提供者",
        "legacy_transition": r"既有模型|已(?:经)?投放市场|过渡规则|过渡期",
        "code_of_practice": r"Code\s+of\s+Practice|行为准则|实践准则",
        "enforcement_penalties": r"罚则|罚款|处罚|执法",
        "regulation_text": r"法规原文|法案原文|条例原文|Regulation\s*\(EU\)",
        "eu_guidance": r"欧盟委员会|AI\s*Office|人工智能办公室",
        "industry_practice": r"行业实践|业界实践|行业做法",
        "unresolved_items": r"仍待明确|尚待明确|待澄清|仍不明确|不确定事项",
        "action_checklist": r"可执行清单|行动清单|上线前|准备清单|行动项",
    }
    return {name: bool(re.search(pattern, text or "", re.I)) for name, pattern in patterns.items()}


def _research_handoff_answer(payload: dict[str, Any]) -> str:
    task_results = [item for item in list(payload.get("taskBriefResults") or []) if isinstance(item, dict)]
    primary_result = task_results[0] if len(task_results) == 1 else {}
    return str(payload.get("answer") or primary_result.get("answer") or "").strip()


def _pure_research_diagnostic(result: LiveCaseResult) -> dict[str, Any]:
    final_urls = _visible_source_urls(result.final_text)
    final_identities = _source_identities_from_urls(final_urls, question=result.spec.prompt)
    research_payloads = _research_handoff_payloads(result)
    assessed_payloads = [
        (payload, _research_handoff_assessment(payload, question=result.spec.prompt))
        for payload in research_payloads
    ]
    handoff_assessments = [assessment for _payload, assessment in assessed_payloads]
    accepted_pairs = [
        (payload, assessment)
        for payload, assessment in assessed_payloads
        if assessment.get("highQuality") is True
    ]
    accepted = [assessment for _payload, assessment in accepted_pairs]
    accepted_identities = {
        identity
        for item in accepted
        for identity in list(item.get("sourceIdentities") or [])
        if str(identity).strip()
    }
    supervisor_web_calls = [
        item
        for item in result.tool_invocations
        if _is_supervisor_owned_invocation(item)
        and str(item.get("toolName") or "").strip().lower() in SUPERVISOR_DIRECT_WEB_TOOLS
    ]
    post_handoff_web_calls = [
        item
        for item in supervisor_web_calls
        if result.research_completed_seq is not None and int(item.get("seq") or 0) > result.research_completed_seq
    ]
    accepted_answers = [_research_handoff_answer(payload) for payload, _assessment in accepted_pairs]
    accepted_answers = [answer for answer in accepted_answers if answer]
    accepted_answer_digests = {
        str(assessment.get("answerSha256") or "").strip().lower()
        for assessment in accepted
        if str(assessment.get("answerSha256") or "").strip()
    }
    final_answer_sha256 = hashlib.sha256(result.final_text.strip().encode("utf-8")).hexdigest()
    handoff_dates = {
        date
        for answer in accepted_answers
        for date in _normalized_date_evidence(answer)
    }
    final_dates = _normalized_date_evidence(result.final_text)
    expected_as_of_dates = {
        match.group(0)
        for assessment in accepted
        for match in [re.search(r"20\d{2}-\d{2}-\d{2}", str(assessment.get("asOf") or ""))]
        if match
    }
    final_semantic_coverage = _pure_research_semantic_coverage(result.final_text)
    final_citation_metrics: dict[str, Any] = {}
    if accepted_pairs:
        from core.tools.research_quality import research_acceptance_metrics

        accepted_payload, _assessment = accepted_pairs[0]
        task_results = [
            item for item in list(accepted_payload.get("taskBriefResults") or []) if isinstance(item, dict)
        ]
        primary_result = task_results[0] if len(task_results) == 1 else {}
        accepted_sources = [
            item
            for item in list(accepted_payload.get("sources") or primary_result.get("sources") or [])
            if isinstance(item, dict)
        ]
        final_citation_metrics = research_acceptance_metrics(
            {
                "question": result.spec.prompt,
                "answer": result.final_text,
                "researchAnswerPack": {"answer": result.final_text, "sources": accepted_sources},
            }
        )
    final_answer_digest_matches_handoff = final_answer_sha256 in accepted_answer_digests
    exact_handoff_answer_preserved = final_answer_digest_matches_handoff or any(
        answer in result.final_text for answer in accepted_answers
    )
    required_date_overlap = 3
    final_fact_retention = bool(
        exact_handoff_answer_preserved
        or (
            accepted_answers
            and all(final_semantic_coverage.values())
            and bool(expected_as_of_dates & final_dates)
            and len(handoff_dates & final_dates) >= required_date_overlap
            and int(final_citation_metrics.get("answerCitedSourceCount") or 0)
            >= PURE_RESEARCH_MIN_SOURCE_COUNT
        )
    )
    readability_issues: list[str] = []
    stripped = result.final_text.strip()
    if not stripped:
        readability_issues.append("final_answer_missing")
    elif stripped.startswith("{"):
        try:
            if isinstance(json.loads(stripped), dict):
                readability_issues.append("final_answer_is_raw_json")
        except json.JSONDecodeError:
            pass
    if _looks_like_handoff_leak(result.final_text) or re.search(
        r"\b(?:taskBriefResults|qualityMetrics|evidenceBundleId|research_evidence_bundle)\b",
        result.final_text,
        re.I,
    ):
        readability_issues.append("runtime_handoff_leaked_to_user")
    if result.final_text and not (expected_as_of_dates & final_dates):
        readability_issues.append("exact_as_of_date_missing")
    if result.final_text and len(handoff_dates & final_dates) < required_date_overlap:
        readability_issues.append("key_timeline_dates_not_preserved")
    return {
        "finalEffectiveAnswerChars": _effective_answer_chars(result.final_text),
        "finalVisibleSourceCount": len(final_identities),
        "finalSourceUrls": final_urls,
        "highQualityResearchHandoffCount": len(accepted),
        "researchHandoffs": handoff_assessments,
        "finalHandoffSourceOverlap": len(final_identities & accepted_identities),
        "finalBodyCitedSourceCount": int(final_citation_metrics.get("answerCitedSourceCount") or 0),
        "finalBodyCitedContentUnitCount": int(final_citation_metrics.get("answerCitedContentUnitCount") or 0),
        "expectedAsOfDates": sorted(expected_as_of_dates),
        "finalDateEvidence": sorted(final_dates),
        "handoffDateEvidence": sorted(handoff_dates),
        "finalHandoffDateOverlap": sorted(handoff_dates & final_dates),
        "finalSemanticCoverage": final_semantic_coverage,
        "finalAnswerSha256": final_answer_sha256,
        "acceptedHandoffAnswerSha256": sorted(accepted_answer_digests),
        "finalAnswerDigestMatchesHandoff": final_answer_digest_matches_handoff,
        "exactHandoffAnswerPreserved": exact_handoff_answer_preserved,
        "finalFactRetention": final_fact_retention,
        "researchCompletedSeq": result.research_completed_seq,
        "supervisorDirectWebCalls": supervisor_web_calls,
        "postResearchHandoffWebCalls": post_handoff_web_calls,
        "readabilityIssues": readability_issues,
    }


def _claims_video_analysis_without_evidence(result: LiveCaseResult) -> bool:
    text = result.final_text
    if not re.search(r"已(经)?分析.*视频|我看了.*视频|视频中.*显示|画面.*显示|从视频.*可以看到", text):
        return False
    observed = " ".join(result.actual_tools + result.observed_topics + result.key_events)
    return not re.search(r"vision|视觉|video|字幕|transcript|download_media_for_vision|vision_media_analyzer", observed, re.I)


def _looks_like_handoff_leak(text: str) -> bool:
    return bool(
        re.search(
            r"运行时链路已经完成并回流|research_evidence_bundle\s*/\s*ready|engineering_patch_bundle\s*/\s*ready|Delegation executed",
            text,
            re.I,
        )
    )


def _pure_research_findings(result: LiveCaseResult) -> list[AuditFinding]:
    diagnostic = _pure_research_diagnostic(result)
    findings: list[AuditFinding] = []
    regression = (
        "tests/scripts/run_supervisor_runtime_skill_live_audit.py --live "
        "--case pure_research_delivery --strict"
    )

    def _add(
        severity: str,
        summary: str,
        recommended_fix: str,
        *,
        evidence: Any | None = None,
        modules: list[str] | None = None,
        regression_test: str | None = None,
    ) -> None:
        findings.append(
            AuditFinding(
                severity=severity,
                case_id=result.spec.case_id,
                title=result.spec.title,
                summary=summary,
                evidence=_redact(diagnostic if evidence is None else evidence),
                modules=modules or ["runtimes/chat/runtime.py", "core/runtime_episode_runner.py", "runtimes/research"],
                recommended_fix=recommended_fix,
                regression_test=regression_test or regression,
            )
        )

    if not _has_research_evidence_path(result):
        _add(
            "P0",
            "纯调研任务没有进入可验证的 Research evidence 路径。",
            "Supervisor 必须创建 Research episode，并等待 typed handoff 回流后再交付。",
            modules=["graph/supervisor_context.py", "core/runtime_episode_runner.py", "runtimes/research"],
        )
    if int(diagnostic.get("highQualityResearchHandoffCount") or 0) < 1:
        _add(
            "P0",
            "Research episode 未产出可识别的 high_quality/accept typed handoff。",
            (
                "只有 coverageComplete、acceptancePassed、reviewDecision=accept、qualityTier=high_quality，"
                "且达到 5000/8/8 claims、独立复核和当前时效指标的 handoff 才允许消费。"
            ),
            evidence={"researchHandoffs": diagnostic.get("researchHandoffs")},
            modules=["core/runtime_episode_runner.py", "core/tools/research_quality.py", "core/tools/research_broker.py"],
            regression_test=(
                "tests/runtime_core/test_runtime_episode_runner.py::"
                "test_research_episode_uses_task_route_query_and_runs_full_evidence"
            ),
        )
    if result.research_completed_seq is None:
        _add(
            "P1",
            "缺少可排序的 Research episode completed 事件，无法审计回流后二次查询。",
            "Research terminal handoff 和 completed 事件必须带同一 episode/session/run 绑定并持久化。",
            evidence={"topics": result.observed_topics, "episodes": result.episodes[:6]},
            modules=["core/runtime_episode_runner.py", "core/database.py"],
            regression_test="tests/runtime_core/test_runtime_episode_runner.py",
        )

    effective_chars = int(diagnostic.get("finalEffectiveAnswerChars") or 0)
    if effective_chars < PURE_RESEARCH_MIN_EFFECTIVE_CHARS:
        _add(
            "P0",
            f"Supervisor 最终答案低于 {PURE_RESEARCH_MIN_EFFECTIVE_CHARS} 有效字符硬门槛：{effective_chars}。",
            "消费并保留完整 Research answer，不得把高质量 handoff 压缩成短摘要。",
        )
    elif effective_chars < PURE_RESEARCH_TARGET_EFFECTIVE_CHARS:
        _add(
            "P1",
            f"Supervisor 最终答案仅越过最低线，未达到 {PURE_RESEARCH_TARGET_EFFECTIVE_CHARS} 字符目标：{effective_chars}。",
            "保留时间线、义务、例外、争议、执行清单和来源细节；不得靠重复凑字。",
        )

    visible_sources = int(diagnostic.get("finalVisibleSourceCount") or 0)
    if visible_sources < PURE_RESEARCH_MIN_SOURCE_COUNT:
        _add(
            "P0",
            f"Supervisor 最终答案低于 {PURE_RESEARCH_MIN_SOURCE_COUNT} 个可访问来源硬门槛：{visible_sources}。",
            "最终答案必须保留 handoff 中经过选择的来源 URL 和就近引用。",
        )
    elif visible_sources < PURE_RESEARCH_TARGET_SOURCE_COUNT:
        _add(
            "P1",
            f"Supervisor 最终答案仅越过最低来源线，未达到 {PURE_RESEARCH_TARGET_SOURCE_COUNT} 来源目标：{visible_sources}。",
            "不要把 Research 已验证的 8 个独立来源压缩丢失。",
        )

    if int(diagnostic.get("highQualityResearchHandoffCount") or 0) > 0:
        source_overlap = int(diagnostic.get("finalHandoffSourceOverlap") or 0)
        if source_overlap < PURE_RESEARCH_MIN_SOURCE_COUNT:
            _add(
                "P0",
                f"最终答案与已接受 Research handoff 的来源交集不足，无法证明消费回流证据：{source_overlap}。",
                "以 typed handoff 的 answer/sources 为事实输入成稿，不要另起无绑定来源列表。",
            )
        elif source_overlap < PURE_RESEARCH_TARGET_SOURCE_COUNT:
            _add(
                "P1",
                f"Supervisor 只保留 {source_overlap} 个 handoff 来源，未达到完整消费 8 来源目标。",
                "保留所有支撑关键结论的已验证来源，避免过度摘要。",
            )

    final_body_citations = int(diagnostic.get("finalBodyCitedSourceCount") or 0)
    final_body_spread = int(diagnostic.get("finalBodyCitedContentUnitCount") or 0)
    if final_body_citations < PURE_RESEARCH_MIN_SOURCE_COUNT or final_body_spread < PURE_RESEARCH_MIN_SOURCE_COUNT:
        _add(
            "P0",
            (
                "Supervisor 最终正文没有达到来源就近引用硬门槛："
                f"正文来源 {final_body_citations}，分散引用内容单元 {final_body_spread}。"
            ),
            "不能只在末尾堆来源列表；至少 5 个来源必须在回答事实的正文单元中就近出现。",
            evidence={
                "finalBodyCitedSourceCount": final_body_citations,
                "finalBodyCitedContentUnitCount": final_body_spread,
            },
        )
    elif final_body_citations < PURE_RESEARCH_TARGET_SOURCE_COUNT or final_body_spread < PURE_RESEARCH_TARGET_SOURCE_COUNT:
        _add(
            "P1",
            (
                "Supervisor 最终正文虽过最低引用线，但未保留 8 来源目标："
                f"正文来源 {final_body_citations}，分散引用内容单元 {final_body_spread}。"
            ),
            "保留 Research answer 已有的逐结论引用，不要在 Supervisor 成稿时退化成来源附录。",
        )

    semantic_coverage = dict(diagnostic.get("finalSemanticCoverage") or {})
    missing_semantics = [name for name, covered in semantic_coverage.items() if covered is not True]
    if missing_semantics:
        _add(
            "P0",
            "Supervisor 最终答案没有覆盖纯调研问题要求的全部关键法律语义。",
            "完整保留 GPAI 范围、时间线、系统性风险门槛、透明/版权/文档、过渡、准则、罚则、来源层级和行动清单。",
            evidence={"missingSemantics": missing_semantics, "coverage": semantic_coverage},
        )
    if len(list(diagnostic.get("handoffDateEvidence") or [])) < 3:
        _add(
            "P0",
            "已接受的 Research handoff 本身没有给出足够的合规关键日期。",
            "Research answer 除精确 as-of 外，必须明确列出至少两个适用/过渡/执法时间点并绑定来源。",
            evidence={"handoffDateEvidence": diagnostic.get("handoffDateEvidence")},
        )
    if diagnostic.get("finalFactRetention") is not True:
        _add(
            "P0",
            "无法证明 Supervisor 最终正文保留了 Research handoff 的关键事实。",
            "优先原样消费完整 handoff answer；若重写，必须同时保留问题语义、精确 as-of、至少三个关键日期及来源绑定。",
            evidence={
                "exactHandoffAnswerPreserved": diagnostic.get("exactHandoffAnswerPreserved"),
                "dateOverlap": diagnostic.get("finalHandoffDateOverlap"),
                "expectedAsOfDates": diagnostic.get("expectedAsOfDates"),
                "semanticCoverage": semantic_coverage,
            },
        )

    post_handoff_web_calls = list(diagnostic.get("postResearchHandoffWebCalls") or [])
    supervisor_web_calls = list(diagnostic.get("supervisorDirectWebCalls") or [])
    if post_handoff_web_calls:
        _add(
            "P0",
            "Supervisor 在 Research handoff 完成后再次调用 Web 工具。",
            "高质量 handoff 回流后直接成稿；不得建立第二条 web_search/web_broker/web_read/web_fetch/web_extract 检索链。",
            evidence={"calls": post_handoff_web_calls, "researchCompletedSeq": result.research_completed_seq},
            modules=["runtimes/chat/runtime.py", "graph/supervisor_context.py", "core/runtime_episode_runner.py"],
        )
    elif supervisor_web_calls:
        _add(
            "P0",
            "纯调研主链观察到 Supervisor 直接 Web 调用，绕过 Research 证据治理。",
            "Supervisor 只负责路由、等待和消费 Research handoff；检索由 Research Runtime 执行。",
            evidence={"calls": supervisor_web_calls, "researchCompletedSeq": result.research_completed_seq},
            modules=["runtimes/chat/runtime.py", "graph/supervisor_context.py"],
        )
    if diagnostic.get("readabilityIssues"):
        _add(
            "P0",
            "Supervisor 最终答案不是可直接交付给用户的时效调研正文。",
            "将 handoff 事实转成可读正文并显式说明截至日期；不得输出 raw JSON 或 runtime 内部字段。",
            evidence={"issues": diagnostic.get("readabilityIssues"), "finalText": result.final_text[:1200]},
        )
    return findings


def _case_findings(result: LiveCaseResult) -> list[AuditFinding]:
    spec = result.spec
    findings: list[AuditFinding] = []
    tool_set = set(result.actual_tools)
    episode_kinds = {
        str(item.get("kind") or item.get("runtimeKind") or item.get("episodeKind") or "").strip().lower()
        for item in result.episodes
        if isinstance(item, dict)
    }
    if result.status in {"failed", "timeout"}:
        findings.append(
            AuditFinding(
                severity="P0" if result.status == "failed" else "P1",
                case_id=spec.case_id,
                title=spec.title,
                summary=f"Live case 未正常完成：{result.failure_reason or result.status}",
                evidence=_redact({"sessionId": result.session_id, "runId": result.run_id, "events": result.key_events[-5:]}),
                modules=["api/chat_realtime_routes.py", "runtimes/chat/runtime.py"],
                recommended_fix="先确认 /chat/submit 是否轻量返回，再看 run record、runtime events 与 episode 是否进入 terminal。",
                regression_test=f"tests/agent_quality/test_live_{spec.case_id}.py",
            )
        )
    for forbidden in spec.forbidden_tools:
        forbidden_seen = forbidden in tool_set
        if forbidden == "runtime_broker":
            forbidden_seen = forbidden_seen or bool(result.episodes) or any(
                topic.startswith("runtime.episode.") for topic in result.observed_topics
            )
        elif forbidden == "delegation_broker":
            forbidden_seen = forbidden_seen or any(
                topic.startswith("delegation.") or topic.startswith("subagent.")
                for topic in result.observed_topics
            )
        if forbidden_seen:
            findings.append(
                AuditFinding(
                    severity="P0" if forbidden in {"write_native_file", "run_system_command"} else "P1",
                    case_id=spec.case_id,
                    title=spec.title,
                    summary=f"观察到不应出现的工具：{forbidden}",
                    evidence=_redact({"tools": result.actual_tools, "events": result.key_events[-6:]}),
                    modules=["graph/supervisor_context.py", "core/native_tools.py", "runtimes/chat/runtime.py"],
                    recommended_fix="收紧任务分流和 direct gate；简单写作不应被误判为 runtime/subagent，复杂副作用必须 route。",
                    regression_test="tests/agent_quality/test_tool_call_validation.py",
                )
            )
    if spec.forbid_runtime_episodes and result.episodes:
        findings.append(
            AuditFinding(
                severity="P1",
                case_id=spec.case_id,
                title=spec.title,
                summary="简单聊天写作误进入 runtime episode。",
                evidence=_redact(
                    {
                        "episodes": [
                            {
                                "id": item.get("episodeId") or item.get("id"),
                                "kind": item.get("kind"),
                                "state": item.get("state"),
                                "reason": item.get("reason"),
                            }
                            for item in result.episodes[:8]
                        ],
                        "finalText": result.final_text[:800],
                    }
                ),
                modules=["graph/supervisor_context.py", "runtimes/chat/runtime.py"],
                recommended_fix="中文任务形状识别需要处理否定词：不保存、不调研、不调用工程运行时不能作为工程/调研正向信号。",
                regression_test="tests/agent_quality/test_writing_routing.py::test_simple_doc_does_not_route_runtime",
            )
        )
    missing_episode_kinds = [kind for kind in spec.expected_episode_kinds if kind not in episode_kinds]
    if missing_episode_kinds:
        findings.append(
            AuditFinding(
                severity="P0",
                case_id=spec.case_id,
                title=spec.title,
                summary=f"联合执行链缺少 runtime：{', '.join(missing_episode_kinds)}",
                evidence=_redact({"episodeKinds": sorted(episode_kinds), "episodes": result.episodes[:8]}),
                modules=["graph/supervisor_context.py", "graph/workflow_assembly.py", "core/runtime_episode_runner.py"],
                recommended_fix="先修 Supervisor 对复合调研交付的连续路线认知，再核查 terminal handoff 是否恢复同一 run。",
                regression_test="tests/scripts/run_supervisor_runtime_skill_live_audit.py --case joint_research_delivery --allow-side-effects",
            )
        )
    for expected in spec.expected_all_tools:
        if expected not in tool_set:
            severity = "P0" if expected == "fetch_skill_instructions" else "P1"
            findings.append(
                AuditFinding(
                    severity=severity,
                    case_id=spec.case_id,
                    title=spec.title,
                    summary=f"缺少必须工具调用：{expected}",
                    evidence=_redact({"tools": result.actual_tools, "topics": result.observed_topics, "finalText": result.final_text[:800]}),
                    modules=["runtimes/chat/runtime.py", "graph/supervisor_context.py"],
                    recommended_fix="Skill 驱动任务必须把 skillReferences/contextMentions 注入模型可见上下文，并把 fetch_skill_instructions 作为首个可验证动作。",
                    regression_test="tests/agent_quality/test_skill_writing_routing.py",
                )
            )
    if spec.expected_any_tools and not any(tool in tool_set for tool in spec.expected_any_tools):
        route_observed = "runtime_broker" in spec.expected_any_tools and (
            bool(result.episodes) or any(topic.startswith("runtime.episode.") for topic in result.observed_topics)
        )
        if route_observed:
            route_missing = False
        else:
            route_missing = True
    else:
        route_missing = False
    if route_missing:
        severity = "P2" if spec.explicit_degradation_ok and _explicit_degradation(result.final_text) else "P1"
        findings.append(
            AuditFinding(
                severity=severity,
                case_id=spec.case_id,
                title=spec.title,
                summary=f"未观察到期望工具族之一：{', '.join(spec.expected_any_tools)}",
                evidence=_redact({"tools": result.actual_tools, "finalText": result.final_text[:1000]}),
                modules=["core/native_tools.py", "graph/supervisor_context.py", "runtimes/research"],
                recommended_fix="检查该模型的 tool call 适配、web_broker/research_broker 可见性，以及天气/来源型写作的路由提示。",
                regression_test="tests/agent_quality/test_tool_call_validation.py",
            )
        )
    if spec.case_id == "source_write" and not _has_research_evidence_path(result):
        findings.append(
            AuditFinding(
                severity="P1",
                case_id=spec.case_id,
                title=spec.title,
                summary="来源型写作没有进入 Research evidence 路径，存在只靠临时网页搜索成稿的假通过风险。",
                evidence=_redact(
                    {
                        "tools": result.actual_tools,
                        "topics": result.observed_topics[-12:],
                        "episodes": result.episodes[:6],
                        "finalText": result.final_text[:1000],
                    }
                ),
                modules=["runtimes/chat/runtime.py", "runtimes/research", "tests/scripts/run_supervisor_runtime_skill_live_audit.py"],
                recommended_fix="写作分流为 research_then_write 且 primaryTaskShape=writing 时由 Supervisor 强制进入 Research evidence 链路，再由 Supervisor 或 writing subagent 成稿。",
                regression_test="tests/agent_quality/test_skill_writing_routing.py",
            )
        )
    if spec.case_id == PURE_RESEARCH_CASE_ID:
        findings.extend(_pure_research_findings(result))
    if result.final_text and _looks_like_handoff_leak(result.final_text):
        findings.append(
            AuditFinding(
                severity="P1",
                case_id=spec.case_id,
                title=spec.title,
                summary="Supervisor 最终回答泄露 runtime handoff 摘要，没有完成面向用户的最终交付。",
                evidence=_redact({"finalText": result.final_text[:1200], "handoffs": result.handoffs[:4]}),
                modules=["runtimes/chat/runtime.py", "core/runtime_episode_runner.py"],
                recommended_fix="handoff merge 后必须恢复 Supervisor 成稿/总结步骤；typed handoff 是事实输入，不应直接作为最终回答。",
                regression_test="tests/agent_quality/test_multi_agent_collaboration.py::test_handoff_merge_resumes_supervisor",
            )
        )
    if spec.source_required and result.final_text and not _mentions_source(result.final_text) and not (
        spec.explicit_degradation_ok and _explicit_degradation(result.final_text)
    ):
        findings.append(
            AuditFinding(
                severity="P1",
                case_id=spec.case_id,
                title=spec.title,
                summary="需要来源的回答缺少来源或可读降级说明。",
                evidence=_redact(result.final_text[:1200]),
                modules=["runtimes/research", "core/tool_observation_detail.py", "graph/supervisor_context.py"],
                recommended_fix="Research Runtime 最终 agent-visible output 必须是提纯后的 evidence bundle + 来源 URL，不是裸搜索/治理 JSON。",
                regression_test="tests/agent_quality/test_hallucination_mitigation.py",
            )
        )
    if spec.case_id == "ambiguous_doc" and result.final_text:
        wrote_full_doc = len(result.final_text) > 600 or result.final_text.count("##") >= 2
        asks_first = bool(
            re.search(
                r"^(?:<voice>.*?</voice>\s*)?(请问|需要.*确认|你希望|请选择|我需要先确认|先确认|这篇文档我先确认|先确认一下)",
                result.final_text,
                re.S,
            )
        )
        if wrote_full_doc or not asks_first:
            findings.append(
                AuditFinding(
                    severity="P1",
                    case_id=spec.case_id,
                    title=spec.title,
                    summary="模糊写作没有先澄清交付形态。",
                    evidence=_redact(result.final_text[:1000]),
                    modules=["graph/supervisor_context.py", "runtimes/chat/runtime.py"],
                    recommended_fix="模糊“写文档/方案/报告”需 ask/clarify：只要正文、需要调研、还是保存为文件。",
                    regression_test="tests/agent_quality/test_writing_routing.py",
                )
            )
    if spec.case_id == "huashu_video_gap":
        if _claims_video_analysis_without_evidence(result):
            findings.append(
                AuditFinding(
                    severity="P1",
                    case_id=spec.case_id,
                    title=spec.title,
                    summary="模型疑似假装已经分析过视频。",
                    evidence=_redact({"finalText": result.final_text[:1200], "tools": result.actual_tools}),
                    modules=["runtimes/chat/runtime.py", "core/native_tools.py"],
                    recommended_fix="缺视频/字幕/Gemini 专用能力时，必须转向 V8OS vision/附件/字幕路径或明确降级，不能写已分析结论。",
                    regression_test="tests/agent_quality/test_hallucination_mitigation.py",
                )
            )
        elif result.final_text and not _explicit_degradation(result.final_text):
            findings.append(
                AuditFinding(
                    severity="P2",
                    case_id=spec.case_id,
                    title=spec.title,
                    summary="huashu 视频缺口没有清楚列出可变通路径或缺失条件。",
                    evidence=_redact(result.final_text[:1200]),
                    modules=["runtimes/chat/runtime.py"],
                    recommended_fix="Skill 执行 brief 应要求：缺 Gemini/video 字幕时说明 V8OS 可用视觉、附件、字幕和需要用户确认的条件。",
                    regression_test="tests/agent_quality/test_skill_writing_routing.py",
                )
            )
    return findings


def _table_row(values: list[Any]) -> str:
    escaped = [str(value).replace("|", "\\|").replace("\n", "<br>") for value in values]
    return "| " + " | ".join(escaped) + " |"


def _write_report(
    output_dir: Path,
    *,
    timestamp: str,
    model_profile: str,
    engine_url: str = DEFAULT_ENGINE_URL,
    results: list[LiveCaseResult],
    findings: list[AuditFinding],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "SUPERVISOR_RUNTIME_SKILL_LIVE_BREAKPOINTS_ZH.md"
    by_severity = {"P0": [], "P1": [], "P2": []}
    for finding in findings:
        by_severity.setdefault(finding.severity, []).append(finding)
    lines: list[str] = [
        "# Supervisor / Runtime / Skill Live 断点审计报告",
        "",
        f"- 生成时间：{timestamp}",
        f"- 模型标签：`{model_profile}`",
        f"- Engine：`{engine_url.rstrip('/')}`（报告内敏感路径已脱敏）",
        "",
        "## 结论概览",
        "",
        _table_row(["等级", "数量"]),
        _table_row(["---", "---"]),
        _table_row(["P0", len(by_severity.get("P0", []))]),
        _table_row(["P1", len(by_severity.get("P1", []))]),
        _table_row(["P2", len(by_severity.get("P2", []))]),
        "",
        "## Case 结果",
        "",
        _table_row(["Case", "状态", "Session", "Run", "延迟", "工具", "Runtime topics"]),
        _table_row(["---", "---", "---", "---", "---", "---", "---"]),
    ]
    for result in results:
        lines.append(
            _table_row(
                [
                    result.spec.case_id,
                    result.status,
                    result.session_id or "",
                    result.run_id or "",
                    f"{result.latency_ms or 0} ms",
                    ", ".join(result.actual_tools[:12]),
                    ", ".join(result.observed_topics[:12]),
                ]
            )
        )
    lines.extend(["", "## 失败与整改", ""])
    if not findings:
        lines.append("未发现 P0/P1/P2 断点。")
    for severity in ("P0", "P1", "P2"):
        items = by_severity.get(severity, [])
        if not items:
            continue
        lines.extend(["", f"### {severity}", ""])
        for item in items:
            lines.extend(
                [
                    f"#### {item.case_id} - {item.title}",
                    "",
                    f"- 摘要：{item.summary}",
                    f"- 涉及模块：{', '.join(item.modules) if item.modules else '未定位'}",
                    f"- 建议修复：{item.recommended_fix or '待补充'}",
                    f"- 回归测试：`{item.regression_test or '待补充'}`",
                    "",
                    "<details>",
                    "<summary>证据</summary>",
                    "",
                    "```text",
                    item.evidence[:4000],
                    "```",
                    "",
                    "</details>",
                    "",
                ]
            )
    lines.extend(["", "## 详细回答摘录", ""])
    for result in results:
        pure_research_diagnostic = (
            _pure_research_diagnostic(result)
            if result.spec.case_id == PURE_RESEARCH_CASE_ID
            else None
        )
        lines.extend(
            [
                f"### {result.spec.case_id}",
                "",
                f"- 标题：{result.spec.title}",
                f"- 最终回答摘录：{_redact(result.final_text[:1800]) or '未找到 assistant 最终文本'}",
                *(
                    [f"- 纯调研质量诊断：`{_redact(pure_research_diagnostic)}`"]
                    if pure_research_diagnostic is not None
                    else []
                ),
                "",
                "<details>",
                "<summary>关键事件</summary>",
                "",
                "```text",
                "\n".join(result.key_events[-20:])[:6000],
                "```",
                "",
                "</details>",
                "",
            ]
        )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    evidence_path = output_dir / "supervisor_runtime_skill_live_results.json"
    evidence_path.write_text(
        json.dumps(
            {
                "timestamp": timestamp,
                "modelProfile": model_profile,
                "results": [
                    {
                        "caseId": result.spec.case_id,
                        "status": result.status,
                        "sessionId": result.session_id,
                        "runId": result.run_id,
                        "latencyMs": result.latency_ms,
                        "tools": result.actual_tools,
                        "topics": result.observed_topics,
                        "finalText": _redact(result.final_text),
                        "episodes": result.episodes,
                        "handoffs": result.handoffs,
                        "toolInvocations": result.tool_invocations,
                        "researchCompletedSeq": result.research_completed_seq,
                        "pureResearchDiagnostic": (
                            _pure_research_diagnostic(result)
                            if result.spec.case_id == PURE_RESEARCH_CASE_ID
                            else None
                        ),
                    }
                    for result in results
                ],
                "findings": [finding.__dict__ for finding in findings],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run live Supervisor / Runtime / Skill breakpoint audit against the Engine.",
    )
    parser.add_argument("--live", action="store_true", help="Required. Without this flag the script does not call live models.")
    parser.add_argument("--model-profile", default=None, help="Audit label. If omitted, read Engine default model config best-effort.")
    parser.add_argument(
        "--case",
        default="all",
        choices=[
            "simple_doc",
            "ambiguous_doc",
            "weather",
            "huashu_plan",
            "huashu_video_gap",
            "source_write",
            PURE_RESEARCH_CASE_ID,
            "joint_research_delivery",
            "all",
        ],
        help="Select one case. 'all' excludes the expensive pure research and side-effect joint cases.",
    )
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", default=str(REPO_ROOT))
    parser.add_argument(
        "--max-wait",
        type=float,
        default=None,
        help="Maximum wait per case. Defaults to 1200s for pure_research_delivery and 420s otherwise.",
    )
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--allow-side-effects", action="store_true", help="Allow the explicit disposable workspace used by side-effect live cases.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero on any P1/P2 finding, not only P0. pure_research_delivery is always strict.",
    )
    args = parser.parse_args(argv)

    if not args.live:
        print("Refusing to call live Engine/model without --live.", file=sys.stderr)
        return 2
    if args.case == "joint_research_delivery" and not args.allow_side_effects:
        print("joint_research_delivery writes a disposable workspace; pass --allow-side-effects.", file=sys.stderr)
        return 2
    if args.case == "joint_research_delivery":
        trusted, trust_event = _ensure_explicit_live_workspace_trusted(Path(args.workspace).expanduser().resolve(strict=False))
        print(f"[live-audit] workspace preflight: {_redact(trust_event)}")
        if not trusted:
            print("joint_research_delivery workspace trust preflight failed.", file=sys.stderr)
            return 2
    model_profile = args.model_profile or _default_model_profile_label()
    ok, error = _wait_for_engine(args.engine_url)
    if not ok:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        result = LiveCaseResult(
            spec=LiveCaseSpec(case_id="engine_unavailable", title="Engine unavailable", prompt=""),
            status="failed",
            failure_reason=error,
        )
        finding = AuditFinding(
            severity="P0",
            case_id="engine_unavailable",
            title="Engine unavailable",
            summary="Engine health check failed before live audit.",
            evidence=_redact(error or "unknown"),
            modules=["apps/v8-agent-os-engine"],
            recommended_fix="先启动 Engine，再运行 live audit。",
            regression_test="manual live smoke",
        )
        if args.write_report:
            output_dir = Path(args.output_dir).expanduser() if args.output_dir else DEFAULT_REPORT_ROOT / "agent_quality" / timestamp
            report_path = _write_report(
                output_dir,
                timestamp=timestamp,
                model_profile=model_profile,
                engine_url=args.engine_url,
                results=[result],
                findings=[finding],
            )
            print(f"Report written: {report_path}")
        print(f"Engine unavailable: {error}", file=sys.stderr)
        return 1

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results: list[LiveCaseResult] = []
    for case in _case_specs(args.case):
        print(f"[live-audit] submit {case.case_id}: {case.title}")
        result = _submit_case(
            args.engine_url,
            case=case,
            model_profile=model_profile,
            timestamp=timestamp,
            workspace=args.workspace,
        )
        max_wait = args.max_wait
        if max_wait is None:
            max_wait = 1200.0 if case.case_id == PURE_RESEARCH_CASE_ID else 420.0
        result = _poll_case(args.engine_url, result, max_wait=max_wait)
        results.append(result)
        print(
            f"[live-audit] {case.case_id}: status={result.status} run={result.run_id or '-'} "
            f"tools={','.join(result.actual_tools[:6]) or '-'}"
        )

    findings: list[AuditFinding] = []
    for result in results:
        findings.extend(_case_findings(result))

    if args.write_report:
        output_dir = Path(args.output_dir).expanduser() if args.output_dir else DEFAULT_REPORT_ROOT / "agent_quality" / timestamp
        report_path = _write_report(
            output_dir,
            timestamp=timestamp,
            model_profile=model_profile,
            engine_url=args.engine_url,
            results=results,
            findings=findings,
        )
        print(f"Report written: {report_path}")

    p0_count = sum(1 for finding in findings if finding.severity == "P0")
    if p0_count:
        print(f"Live audit found {p0_count} P0 issue(s).", file=sys.stderr)
        return 1
    strict_findings = args.strict or any(result.spec.case_id == PURE_RESEARCH_CASE_ID for result in results)
    if strict_findings and findings:
        print(f"Live audit found {len(findings)} issue(s).", file=sys.stderr)
        return 1
    print(f"Live audit complete: {len(findings)} issue(s), P0={p0_count}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
