from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_supervisor_runtime_skill_live_audit import (  # noqa: E402
    DEFAULT_ENGINE_URL,
    DEFAULT_REPORT_ROOT,
    HUASHU_NUWA_SKILL_ROOT,
    LiveCaseResult,
    LiveCaseSpec,
    _append_unique,
    _collect_handoff_tool_names,
    _collect_tool_names,
    _default_model_profile_label,
    _engine_api_base,
    _event_carries_tool_result,
    _event_payload,
    _event_topic,
    _extract_final_text,
    _huashu_skill_reference,
    _json_request,
    _load_canonical_messages,
    _load_durable_episode_facts,
    _load_durable_runtime_events,
    _poll_case,
    _redact,
    _wait_for_engine,
)


TARGET_SKILL_DIR_NAME = "sanyueqi-perspective"
DEFAULT_MODEL_FALLBACKS = ["mimo2.5pro", "doubao-seed-2.0-pro", "deepseek-v4-flash"]
REQUIRED_RESEARCH_FILES = [
    "01-writings.md",
    "02-conversations.md",
    "03-expression-dna.md",
    "04-external-views.md",
    "05-decisions.md",
    "06-timeline.md",
]
FULL_READ_MARKERS = [
    "=== INSTRUCTIONS (FULL) ===",
    "Phase 0.5",
    "Phase 1",
    "Phase 2",
    "Phase 3",
    "references/research",
]
SKILL_MARKERS = [
    "三月七",
    "崩坏",
    "心智模型",
    "决策启发式",
    "表达DNA",
    "诚实边界",
    "调研来源",
]
PLACEHOLDER_PATTERN = re.compile(r"(待调研|待补充|待填充|占位|空目录|空模板|placeholder|todo|tbd|无官方设定来源|仅示例|示例内容)", re.I)
SPEC_STAGES = ("requirements", "bugfix", "design", "tasks")
SUBMIT_REQUEST_TIMEOUT_SECONDS = 30
ACTIVE_RUN_STATUSES = {"queued", "running", "waiting_approval", "waiting_input", "waiting_external_tool", "paused"}


@dataclass
class Finding:
    severity: str
    code: str
    summary: str
    evidence: str = ""


@dataclass
class HuashuAuditResult:
    status: str = "pending"
    timestamp: str = ""
    session_id: str | None = None
    run_id: str | None = None
    target_dir: str = ""
    backup_dir: str | None = None
    model_profile: str = ""
    findings: list[Finding] = field(default_factory=list)
    observed_tools: list[str] = field(default_factory=list)
    observed_topics: list[str] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)
    final_text: str = ""
    report_dir: str | None = None
    target: str = ""
    target_skill_dir_name: str = ""

    def add(self, severity: str, code: str, summary: str, evidence: Any = "") -> None:
        self.findings.append(Finding(severity, code, summary, _redact(evidence) if evidence else ""))

    @property
    def has_blocking_failures(self) -> bool:
        return any(item.severity in {"P0", "P1"} for item in self.findings)


def _target_dir(workspace: Path, skill_dir_name: str = TARGET_SKILL_DIR_NAME) -> Path:
    return workspace / ".agents" / "skills" / skill_dir_name


def _skill_markers(target: str, game: str) -> list[str]:
    markers = [target, "心智模型", "决策启发式", "表达DNA", "诚实边界", "调研来源"]
    game_prefix = str(game or "").strip()[:2]
    if game_prefix:
        markers.append(game_prefix)
    return markers


def _report_dir(output_root: Path, timestamp: str) -> Path:
    return output_root / "huashu_nuwa_skill_live" / timestamp


def _read_text(path: Path, *, limit: int | None = None) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text if limit is None else text[:limit]


def _preflight(workspace: Path, *, require_engine: bool, engine_url: str) -> list[Finding]:
    findings: list[Finding] = []
    if not workspace.exists() or not workspace.is_dir():
        findings.append(Finding("P0", "workspace_missing", f"工作区不存在：{workspace}"))
    skill_parent = workspace / ".agents" / "skills"
    if not skill_parent.exists():
        findings.append(Finding("P1", "workspace_skill_root_missing", f"工作区 skill root 不存在：{skill_parent}"))
    if not HUASHU_NUWA_SKILL_ROOT.exists():
        findings.append(Finding("P0", "huashu_nuwa_missing", f"huashu-nuwa skill 不存在：{HUASHU_NUWA_SKILL_ROOT}"))
    if require_engine:
        ok, error = _wait_for_engine(engine_url, timeout=20)
        if not ok:
            findings.append(Finding("P0", "engine_unavailable", f"Engine 不可用：{error or 'unknown'}"))
    try:
        from runtimes.extensions.skills.loader import SkillLoader, fetch_skill_instructions

        matches = SkillLoader.resolve_skill_matches(
            "huashu-nuwa",
            force_refresh=True,
            runtime_kind="chat",
            explicit_workspace_path=str(workspace),
        )
        if not matches:
            findings.append(Finding("P0", "huashu_nuwa_not_resolved", "SkillLoader 无法发现 huashu-nuwa。"))
        full = fetch_skill_instructions.func("huashu-nuwa", detail_level="full")
        missing = [marker for marker in FULL_READ_MARKERS if marker not in full]
        if missing:
            findings.append(
                Finding(
                    "P0",
                    "huashu_nuwa_full_read_incomplete",
                    "fetch_skill_instructions(detail_level='full') 没有返回完整关键流程。",
                    {"missing": missing, "preview": full[:2000]},
                )
            )
        if "=== CONTINUATION MANIFEST ===" not in full or "references/skill-template.md" not in full:
            findings.append(
                Finding(
                    "P1",
                    "huashu_nuwa_continuation_manifest_missing",
                    "fetch_skill_instructions(full) 没有暴露可续读的 continuationManifest 或关键模板。",
                    {"preview": full[:3000]},
                )
            )
        template = fetch_skill_instructions.func("huashu-nuwa", relative_path="references/skill-template.md")
        framework = fetch_skill_instructions.func("huashu-nuwa", relative_path="references/extraction-framework.md")
        if "=== SKILL FILE ===" not in template or "=== SKILL FILE ===" not in framework:
            findings.append(
                Finding(
                    "P1",
                    "huashu_nuwa_continuation_read_failed",
                    "无法通过 fetch_skill_instructions(relative_path=...) 续读 huashu-nuwa 关键参考文件。",
                    {"templatePreview": template[:1200], "frameworkPreview": framework[:1200]},
                )
            )
    except Exception as exc:  # noqa: BLE001 - preflight should preserve exact import/runtime failures.
        findings.append(Finding("P0", "skill_full_read_exception", f"{type(exc).__name__}: {exc}"))
    return findings


def _backup_existing_target(target_dir: Path, workspace: Path, timestamp: str) -> Path | None:
    if not target_dir.exists():
        return None
    # Keep backups outside the workspace so Engineering/Research context pack scans do
    # not confuse old artifacts with the requested acceptance target.
    backup_root = Path.home() / ".v8-agent-os" / "backups" / "huashu_nuwa_skill_live" / timestamp
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_dir = backup_root / target_dir.name
    suffix = 1
    while backup_dir.exists():
        suffix += 1
        backup_dir = backup_root / f"{target_dir.name}-{suffix}"
    shutil.move(str(target_dir), str(backup_dir))
    return backup_dir


def _same_path(left: str | Path | None, right: Path) -> bool:
    if not left:
        return False
    try:
        return Path(str(left)).expanduser().resolve() == right.expanduser().resolve()
    except Exception:
        return str(left).strip().lower() == str(right).strip().lower()


def _clear_workspace_sessions(workspace: Path) -> list[str]:
    from core.database import db

    deleted: list[str] = []
    for row in db.get_sessions():
        session_id = str(row.get("id") or "").strip()
        if not session_id:
            continue
        binding = db.get_session_scope_binding(session_id) or {}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        candidates = [
            binding.get("workspace_path"),
            metadata.get("workspacePath"),
            metadata.get("workspace_path"),
        ]
        if not any(_same_path(candidate, workspace) for candidate in candidates):
            continue
        db.delete_session(session_id)
        try:
            db.close_session_scope_binding(session_id, status="cleared_by_live_audit")
        except Exception:
            pass
        deleted.append(session_id)
    return deleted


def _find_pending_spec_stage_targets(workspace: Path) -> list[dict[str, Any]]:
    from core.spec_service import spec_service

    targets: list[dict[str, Any]] = []
    try:
        listing = spec_service.list_specs(workspace_path=str(workspace), include_archived=False, limit=20)
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    for spec in listing.get("specs") or []:
        if not isinstance(spec, dict):
            continue
        spec_id = str(spec.get("specId") or "").strip()
        if not spec_id:
            continue
        pipeline = spec.get("pipelineControl") if isinstance(spec.get("pipelineControl"), dict) else {}
        documents = spec.get("documents") if isinstance(spec.get("documents"), dict) else {}
        approved = set(str(item) for item in list(pipeline.get("approvedStages") or []))
        candidates = [
            str(pipeline.get("blockedByApproval") or "").strip(),
            str(pipeline.get("currentStage") or spec.get("currentStage") or "").strip(),
        ]
        for stage in candidates:
            normalized_stage = stage.lower()
            if normalized_stage not in SPEC_STAGES:
                continue
            if normalized_stage in approved:
                continue
            if normalized_stage not in documents:
                continue
            targets.append(
                {
                    "specId": spec_id,
                    "stage": normalized_stage,
                    "source": "spec_pipeline_pending_approval",
                }
            )
            break
    return targets


def _current_run_status(run_id: str | None) -> str:
    if not run_id:
        return ""
    try:
        from core.database import db

        record = db.get_run_record(str(run_id)) or {}
        return str(record.get("status") or "").strip().lower()
    except Exception:
        return ""


def _resume_waiting_run_after_spec_approval(
    engine_url: str,
    result: LiveCaseResult,
    approvals: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not result.run_id or not approvals:
        return None
    status = _current_run_status(result.run_id)
    if status not in {"waiting_input", "waiting_approval", "paused"}:
        return None
    response = _json_request(
        f"{_engine_api_base(engine_url)}/runs/{result.run_id}/commands/resume",
        method="POST",
        payload={
            "reason": "spec_auto_approved",
            "payload": {
                "autoSpecApprovals": approvals,
                "source": "huashu_nuwa_skill_live_audit",
            },
        },
        timeout=12,
    )
    return {"runId": result.run_id, "previousStatus": status, "resumeResponse": response}


def _approve_pending_spec_stage_approvals(
    engine_url: str,
    result: LiveCaseResult,
    approvals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not result.run_id or not approvals:
        return []
    approved_keys = {
        (
            str(item.get("specId") or "").strip(),
            str(item.get("stage") or "").strip().lower(),
        )
        for item in approvals
        if isinstance(item, dict)
    }
    approved_keys.discard(("", ""))
    if not approved_keys:
        return []
    try:
        from core.database import db

        pending = db.list_pending_approvals(run_id=str(result.run_id), status="pending")
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    responses: list[dict[str, Any]] = []
    for approval in pending:
        approval_kind = str(approval.get("approval_kind") or "").strip()
        if approval_kind != "spec_stage_approval":
            continue
        request = approval.get("request") if isinstance(approval.get("request"), dict) else {}
        key = (
            str(request.get("specId") or "").strip(),
            str(request.get("stage") or "").strip().lower(),
        )
        if key not in approved_keys:
            continue
        approval_id = str(approval.get("id") or approval.get("approval_id") or "").strip()
        if not approval_id:
            continue
        try:
            response = _json_request(
                f"{_engine_api_base(engine_url)}/approvals/{approval_id}/approve",
                method="POST",
                payload={
                    "reason": "spec stage approved by huashu-nuwa live audit",
                    "response": {
                        "decision": "approved",
                        "source": "huashu_nuwa_skill_live_audit",
                        "specId": key[0],
                        "stage": key[1],
                    },
                },
                timeout=12,
            )
        except Exception as exc:  # noqa: BLE001
            responses.append({"approvalId": approval_id, "specId": key[0], "stage": key[1], "error": f"{type(exc).__name__}: {exc}"})
            continue
        responses.append(
            {
                "approvalId": approval_id,
                "specId": key[0],
                "stage": key[1],
                "status": "approved",
                "resumeScheduled": bool(response.get("resume_scheduled") if isinstance(response, dict) else False),
            }
        )
    return responses


def _auto_respond_pending_ask_user(engine_url: str, result: LiveCaseResult) -> list[dict[str, Any]]:
    if not result.session_id:
        return []
    try:
        from core.database import db

        interactions = db.list_ask_user_interactions(session_id=result.session_id, status="pending")
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    responses: list[dict[str, Any]] = []
    for interaction in interactions:
        interaction_id = str(interaction.get("id") or "").strip()
        if not interaction_id:
            continue
        if result.run_id and str(interaction.get("run_id") or "").strip() not in {"", str(result.run_id)}:
            continue
        answer = (
            "同意，批准继续。请按已批准 Spec 和 live 验收要求继续进入下一阶段；"
            "如果是质量检查点，请在诚实边界中记录限制后继续交付当前最优版本。"
        )
        try:
            response = _json_request(
                f"{_engine_api_base(engine_url)}/ask-user/{interaction_id}/respond",
                method="POST",
                payload={
                    "reason": "auto-approved by huashu-nuwa live audit",
                    "response": {
                        "answer": answer,
                        "approved": True,
                        "source": "huashu_nuwa_skill_live_audit",
                    },
                },
                timeout=12,
            )
        except Exception as exc:  # noqa: BLE001
            responses.append({"interactionId": interaction_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        responses.append(
            {
                "interactionId": interaction_id,
                "question": str(interaction.get("question") or interaction.get("prompt") or "")[:240],
                "status": "responded",
                "responseKind": response.get("command_event", {}).get("topic") if isinstance(response, dict) else None,
            }
        )
    return responses


def _latest_run_id_for_session(session_id: str, *, wait_seconds: float = 0.0) -> str | None:
    from core.database import db

    session_id = str(session_id or "").strip()
    if not session_id:
        return None
    deadline = time.time() + max(0.0, wait_seconds)
    while True:
        try:
            runs = db.list_run_records(session_id=session_id, limit=1)
        except Exception:
            runs = []
        if runs:
            run_id = str(runs[0].get("id") or "").strip()
            if run_id:
                return run_id
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


def _looks_like_submit_timeout(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "timeout" in text or "timed out" in text


def _recover_submit_timeout_result(
    result: LiveCaseResult,
    *,
    session_id: str,
    started: float,
    exc: Exception,
) -> bool:
    error = f"{type(exc).__name__}: {exc}"
    if not _looks_like_submit_timeout(exc):
        return False
    run_id = _latest_run_id_for_session(session_id, wait_seconds=30)
    if not run_id:
        return False
    result.status = "submitted"
    result.session_id = session_id
    result.run_id = run_id
    result.failure_reason = None
    result.latency_ms = int((time.perf_counter() - started) * 1000)
    result.key_events.append(
        _redact(
            {
                "submitTimedOutButRunRecovered": True,
                "sessionId": session_id,
                "runId": run_id,
                "submitError": error,
            }
        )
    )
    return True


def _poll_case_with_spec_auto_approve(
    engine_url: str,
    result: LiveCaseResult,
    *,
    max_wait: float,
    workspace: Path,
    auto_approve_spec: bool,
) -> LiveCaseResult:
    if not auto_approve_spec:
        return _poll_case(engine_url, result, max_wait=max_wait)
    deadline = time.time() + max_wait
    pending_resume_approvals: list[dict[str, Any]] = []
    while time.time() < deadline:
        approvals = _find_pending_spec_stage_targets(workspace)
        if approvals:
            result.key_events.append(_redact({"autoSpecApprovalTargets": approvals})[:1600])
            pending_resume_approvals.extend(approvals)
            approval_responses = _approve_pending_spec_stage_approvals(engine_url, result, approvals)
            if approval_responses:
                result.key_events.append(_redact({"autoSpecApprovalResponses": approval_responses})[:1600])
                pending_resume_approvals = []
                result.status = "submitted"
                result.failure_reason = None
        ask_responses = _auto_respond_pending_ask_user(engine_url, result)
        if ask_responses:
            result.key_events.append(_redact({"autoAskUserResponses": ask_responses})[:1600])
            pending_resume_approvals = []
        remaining = max(1.0, deadline - time.time())
        result = _poll_case(engine_url, result, max_wait=min(8.0, remaining))
        approval_responses = _approve_pending_spec_stage_approvals(engine_url, result, pending_resume_approvals)
        if approval_responses:
            result.key_events.append(_redact({"autoSpecApprovalResponses": approval_responses})[:1600])
            pending_resume_approvals = []
            result.status = "submitted"
            result.failure_reason = None
            continue
        ask_responses = _auto_respond_pending_ask_user(engine_url, result)
        if ask_responses:
            result.key_events.append(_redact({"autoAskUserResponses": ask_responses})[:1600])
            pending_resume_approvals = []
            result.status = "submitted"
            result.failure_reason = None
            continue
        run_status = _current_run_status(result.run_id)
        if run_status in ACTIVE_RUN_STATUSES:
            result.key_events.append(
                _redact(
                    {
                        "runStillActiveAfterPoll": True,
                        "runId": result.run_id,
                        "runStatus": run_status,
                    }
                )[:1600]
            )
            result.status = "submitted"
            result.failure_reason = None
            continue
        if result.status != "timeout":
            return result
        result.status = "submitted"
        result.failure_reason = None
    result.status = "timeout"
    result.failure_reason = "run_or_episode_not_terminal_within_max_wait"
    return result


def _live_run_still_active(live: LiveCaseResult) -> bool:
    run_id = str(live.run_id or "").strip()
    if not run_id:
        return False
    try:
        from core.database import db

        record = db.get_run_record(run_id) or {}
    except Exception:
        return False
    return str(record.get("status") or "").strip() in ACTIVE_RUN_STATUSES


def _prompt(target: str, game: str, target_dir: Path, *, skill_dir_name: str, user_prompt: str = "") -> str:
    user_prompt_line = f"用户原始请求：{user_prompt.strip()}\n\n" if user_prompt.strip() else ""
    return f"""{user_prompt_line}这是一次 V8OS 主链 live 验收，请完整执行，不要只给计划。

目标：使用已选择的 huashu-nuwa skill，调研米哈游游戏《{game}》角色「{target}」，并按 huashu-nuwa 的要求蒸馏生成一个可运行的角色视角 skill。

硬性要求：
1. 第一阶段必须读取 fetch_skill_instructions(skill_name="huashu-nuwa", detail_level="full") 和 fetch_skill_instructions(skill_name="skill-creator", detail_level="full")，不要只读摘要；huashu-nuwa 规定蒸馏流程，skill-creator 规定可加载 SKILL.md schema。
   - 读取 huashu-nuwa full 后，必须按 continuationManifest 继续读取：
     fetch_skill_instructions(skill_name="huashu-nuwa", relative_path="references/skill-template.md")
     fetch_skill_instructions(skill_name="huashu-nuwa", relative_path="references/extraction-framework.md")
   这两次必须作为 fetch_skill_instructions 工具调用出现在 live 轨迹中；不能用 read_native_file、记忆、摘要或最终 validator 替代。
2. 输出目录只能是：{target_dir}
3. 必须创建自包含目录结构：
   - SKILL.md
   - scripts/
   - references/research/01-writings.md
   - references/research/02-conversations.md
   - references/research/03-expression-dna.md
   - references/research/04-external-views.md
   - references/research/05-decisions.md
   - references/research/06-timeline.md
   - references/sources/
4. SKILL.md 必须是可被 SkillLoader 发现的有效 skill 文件，开头必须包含 YAML frontmatter，例如：
   ---
   name: {skill_dir_name}
   description: {target}（《{game}》）的思维框架与表达方式。用于以{target}视角分析问题、回应选择、生成台词风格建议。
   ---
   并且正文至少包含这些一级或二级章节：使用说明、身份卡、心智模型、决策启发式、表达DNA、时间线、诚实边界、调研来源。
   这不是简短角色扮演提示词；正文必须达到至少 4500 个 Unicode 字符。请在交付前重新读取磁盘上的 SKILL.md 并确认字符数，而不是按字节数估计。
5. {target}是虚构角色，请把 huashu-nuwa 的人物调研六维适配为：
   - 官方设定、角色故事、角色档案、命途/版本设定
   - 剧情台词、短信、同行任务、活动剧情中的表达方式
   - 口头禅、句式、语气、幽默方式、情绪节奏
   - 官方/玩家/媒体解读的外部视角，并保留冲突
   - 关键剧情行为、选择、成长弧线和内在张力
   - 版本时间线、登场节点和信息截止边界
6. Research Runtime 必须产出可核验来源；Web Research Architect 必须把清洗材料提纯为 evidence pack / claim table / source matrix，不能把搜索 snippet 当最终调研结论。
7. 如果遇到 gemini-video 或视频转写要求，但本轮没有 Gemini key 或本地视频：优先使用 V8OS 内置视觉/附件/字幕/网页读取能力；仍不可用时在诚实边界注明“未进行视频画面级分析”，不要假装看过视频。
8. 本 live 验收已经授权写入当前工作区；文件副作用应走 Engineering/runtime 路径。不要写到全局 ~/.agents/skills，也不要写到旧 .claude/skills。
9. huashu-nuwa 的 Phase 1.5 / Phase 2.5 检查点在本次验收中视为用户授权继续：如果质量足够请继续；如果不足，请补证或在诚实边界标注后交付当前最优版本，不要无限等待用户。

最终回复只需要给出：生成目录、关键文件清单、调研质量摘要、无法覆盖的信息边界、二次复用方式。
"""


def _make_live_result(
    session_id: str,
    prompt: str,
    *,
    target: str = "三月七",
    skill_dir_name: str = TARGET_SKILL_DIR_NAME,
    title: str | None = None,
) -> LiveCaseResult:
    skill_refs, mentions = _huashu_skill_reference()
    case = LiveCaseSpec(
        case_id=f"huashu_nuwa_{skill_dir_name}_skill",
        title=title or f"huashu-nuwa 真实生成{target} skill",
        prompt=prompt,
        expected_all_tools=["fetch_skill_instructions"],
        expected_any_tools=["research_broker", "runtime_broker", "web_broker"],
        skill_required=True,
        source_required=True,
        skill_references=skill_refs,
        context_mentions=mentions,
    )
    result = LiveCaseResult(spec=case)
    result.session_id = session_id
    return result


def _submit_live_case(
    engine_url: str,
    *,
    workspace: Path,
    target: str,
    game: str,
    target_dir: Path,
    skill_dir_name: str,
    model_profile: str,
    timestamp: str,
    spec_mode: bool = False,
    user_prompt: str = "",
) -> LiveCaseResult:
    session_id = f"huashu-nuwa-{skill_dir_name}-live-{timestamp}"
    prompt = _prompt(target, game, target_dir, skill_dir_name=skill_dir_name, user_prompt=user_prompt)
    result = _make_live_result(session_id, prompt, target=target, skill_dir_name=skill_dir_name)
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": f"{session_id}-user",
        "stream": False,
        "workspacePath": str(workspace),
        "messages": [{"role": "user", "content": prompt}],
        "data": {
            "conversationId": session_id,
            "clientMessageId": f"{session_id}-user",
            "workspacePath": str(workspace),
            "modelProfile": model_profile,
            "specMode": bool(spec_mode),
            "allowSideEffects": True,
            "huashuNuwaSkillLiveAudit": True,
            "targetSkillPath": str(target_dir),
            "skillReferences": result.spec.skill_references or None,
            "contextMentions": result.spec.context_mentions or None,
        },
    }
    started = time.perf_counter()
    try:
        response = _json_request(
            f"{_engine_api_base(engine_url)}/chat/submit",
            method="POST",
            payload=payload,
            timeout=SUBMIT_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        if _recover_submit_timeout_result(result, session_id=session_id, started=started, exc=exc):
            return result
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


def _findings_payload(findings: list[Finding]) -> str:
    rows = []
    for item in findings:
        if item.severity not in {"P0", "P1"}:
            continue
        rows.append(
            {
                "severity": item.severity,
                "code": item.code,
                "summary": item.summary,
                "evidence": item.evidence[:1800] if item.evidence else "",
            }
        )
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _repair_prompt(*, target: str, game: str, target_dir: Path, findings: list[Finding], attempt: int) -> str:
    return f"""继续同一个 live 验收，不要新建目录，不要改写到全局 skill root。

上一次生成没有通过验收。请只修复并覆盖这个目录里的产物：{target_dir}

失败项如下：
```json
{_findings_payload(findings)}
```

硬性修复要求：
0. 先重新读取 huashu-nuwa/full 和 skill-creator/full，再按两者合同修复；不要只凭上轮记忆补模板。
0.0a 产物类 skill 任务必须通过 fetch_skill_instructions 的相对路径续读完成模板和方法论读取，不能用 read_native_file 或“我已知道模板”替代。修复前必须实际调用：
   fetch_skill_instructions(skill_name="huashu-nuwa", relative_path="references/skill-template.md")
   fetch_skill_instructions(skill_name="huashu-nuwa", relative_path="references/extraction-framework.md")
0.1 本 live audit 已显式允许工作区副作用写入；系统不存在 `skill-validation-repair` 工具组，也不需要等待额外授权。你必须实际调用可用工具覆盖文件，或通过 runtime_broker(route) 等待 Engineering 完成。只输出计划、等待授权、让用户手动覆盖，都视为失败。
0.2 修复完成前不要给最终交付回复；最终回复必须基于磁盘上已写入并可读取的文件，而不是“准备好了/待执行”的计划。
1. SKILL.md 必须以 YAML frontmatter 开头：
   ---
   name: {target_dir.name}
   description: {target}（《{game}》）的思维框架与表达方式。用于以{target}视角分析问题、回应选择、生成台词风格建议。
   ---
2. SKILL.md 正文必须显式包含：使用说明、身份卡、心智模型、决策启发式、表达DNA、时间线、诚实边界、调研来源。
3. SKILL.md 不能是简短角色扮演提示词；它必须是可复用 skill，至少 4500 个 Unicode 字符（不是字节数），能教另一个 agent 如何以「{target}」视角思考和表达。
4. references/research/01-writings.md 到 06-timeline.md 必须全部保留，每个文件必须有来源 URL 或来源说明/可信度标记；缺来源的文件直接追加 `## 来源与可信度` 小节即可。
5. 不要声称分析过未实际读取的视频；如无视频画面证据，在诚实边界写清。
6. 修复完成后再次让 SkillLoader 能在当前 workspace 发现并 fetch `{target_dir.name}`。

这是第 {attempt} 次自动修复尝试。请直接修复文件并交付，不要只解释原因。
"""


def _submit_repair_case(
    engine_url: str,
    *,
    workspace: Path,
    target: str,
    game: str,
    target_dir: Path,
    model_profile: str,
    session_id: str,
    findings: list[Finding],
    attempt: int,
    spec_mode: bool = False,
) -> LiveCaseResult:
    prompt = _repair_prompt(target=target, game=game, target_dir=target_dir, findings=findings, attempt=attempt)
    result = _make_live_result(
        session_id,
        prompt,
        target=target,
        skill_dir_name=target_dir.name,
        title=f"huashu-nuwa {target} skill 自动修复 #{attempt}",
    )
    payload = {
        "session_id": session_id,
        "conversationId": session_id,
        "clientMessageId": f"{session_id}-repair-{attempt}",
        "stream": False,
        "workspacePath": str(workspace),
        "messages": [{"role": "user", "content": prompt}],
        "data": {
            "conversationId": session_id,
            "clientMessageId": f"{session_id}-repair-{attempt}",
            "workspacePath": str(workspace),
            "modelProfile": model_profile,
            "specMode": bool(spec_mode),
            "allowSideEffects": True,
            "huashuNuwaSkillLiveAudit": True,
            "targetSkillPath": str(target_dir),
            "repairAttempt": attempt,
            "skillReferences": result.spec.skill_references or None,
            "contextMentions": result.spec.context_mentions or None,
        },
    }
    started = time.perf_counter()
    try:
        response = _json_request(
            f"{_engine_api_base(engine_url)}/chat/submit",
            method="POST",
            payload=payload,
            timeout=SUBMIT_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        if _recover_submit_timeout_result(result, session_id=session_id, started=started, exc=exc):
            return result
        result.status = "failed"
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


def _load_existing_live_case(session_id: str) -> LiveCaseResult:
    result = _make_live_result(session_id, "existing live session validation")
    result.session_id = session_id
    result.status = "completed"
    durable_events, event_error = _load_durable_runtime_events(result)
    if event_error:
        result.key_events.append(_redact({"durableRuntimeEventsError": event_error}))
    for event in durable_events:
        topic = _event_topic(event)
        if topic:
            _append_unique(result.observed_topics, [topic])
        payload = _event_payload(event)
        if _event_carries_tool_result(topic):
            _append_unique(result.actual_tools, sorted(_collect_tool_names(payload)))
    episodes, handoffs, episode_error = _load_durable_episode_facts(result)
    if episode_error:
        result.key_events.append(_redact({"durableEpisodesError": episode_error}))
    result.episodes = episodes
    result.handoffs = handoffs
    _append_unique(result.actual_tools, sorted(_collect_handoff_tool_names(handoffs)))
    messages, message_error = _load_canonical_messages(result)
    if message_error:
        result.key_events.append(_redact({"canonicalMessagesError": message_error}))
    result.canonical_messages = messages
    result.final_text = _extract_final_text(messages)
    return result


def _generated_manifest(target_dir: Path) -> list[str]:
    if not target_dir.exists():
        return []
    files: list[str] = []
    for path in sorted(target_dir.rglob("*")):
        if path.is_file():
            files.append(str(path.relative_to(target_dir)).replace("\\", "/"))
    return files


def _event_blob(result: LiveCaseResult) -> str:
    payload = {
        "tools": result.actual_tools,
        "topics": result.observed_topics,
        "events": result.key_events,
        "episodes": result.episodes,
        "handoffs": result.handoffs,
        "finalText": result.final_text,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _durable_event_blob(result: LiveCaseResult) -> str:
    """Load durable events losslessly enough for acceptance checks.

    Live polling intentionally stores compact key events for reports. Acceptance
    checks for long tool outputs must use the DB-backed event stream so a real
    tool result is not hidden by the report preview budget.
    """

    events, error = _load_durable_runtime_events(result)
    payload = {
        "error": error,
        "events": events,
        "episodes": result.episodes,
        "handoffs": result.handoffs,
        "finalText": result.final_text,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _has_structured_skill_file_read(live: LiveCaseResult, relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/").strip()
    events, _error = _load_durable_runtime_events(live)
    matching_call_ids: set[str] = set()

    def _payload_from_event(event: dict[str, Any]) -> dict[str, Any]:
        payload = _event_payload(event)
        if not isinstance(payload, dict) and event.get("payload_json") is not None:
            try:
                payload = json.loads(str(event.get("payload_json") or "{}"))
            except json.JSONDecodeError:
                payload = {}
        return payload if isinstance(payload, dict) else {}

    def _tool_call_id(tool_payload: dict[str, Any]) -> str:
        return str(
            tool_payload.get("toolCallId")
            or tool_payload.get("toolInvocationId")
            or tool_payload.get("tool_call_id")
            or tool_payload.get("id")
            or ""
        ).strip()

    def _result_text(tool_payload: dict[str, Any]) -> str:
        result_payload = tool_payload.get("result")
        if result_payload is None and tool_payload.get("output") is not None:
            result_payload = tool_payload.get("output")
        if isinstance(result_payload, str):
            return result_payload
        return json.dumps(result_payload, ensure_ascii=False, default=str)

    for event in events:
        payload = _payload_from_event(event)
        tool_payload = payload.get("tool") if isinstance(payload.get("tool"), dict) else payload
        tool_name = str(tool_payload.get("toolName") or tool_payload.get("name") or tool_payload.get("tool") or "").strip()
        if tool_name != "fetch_skill_instructions":
            continue
        args = tool_payload.get("args") if isinstance(tool_payload.get("args"), dict) else {}
        arg_path = str(args.get("relative_path") or args.get("relativePath") or "").replace("\\", "/").strip()
        call_id = _tool_call_id(tool_payload)
        if arg_path == normalized and call_id:
            matching_call_ids.add(call_id)
        result_text = _result_text(tool_payload)
        if arg_path == normalized and "=== SKILL FILE ===" in result_text and normalized in result_text:
            return True
        if call_id and call_id in matching_call_ids and "=== SKILL FILE ===" in result_text and normalized in result_text:
            return True
    return False


def _validate_live_result(
    result: HuashuAuditResult,
    live: LiveCaseResult,
    target_dir: Path,
    workspace: Path,
    *,
    target: str,
    game: str,
    skill_dir_name: str,
) -> None:
    result.session_id = live.session_id
    result.run_id = live.run_id
    result.observed_tools = list(live.actual_tools)
    result.observed_topics = list(live.observed_topics)
    result.final_text = live.final_text
    result.generated_files = _generated_manifest(target_dir)
    compact_blob = _event_blob(live)
    durable_blob = _durable_event_blob(live)
    blob = compact_blob + "\n" + durable_blob

    if live.status in {"failed", "timeout"}:
        result.add("P0", "live_run_not_terminal", f"Live run 未正常完成：{live.failure_reason or live.status}", live.key_events[-8:])
    if "fetch_skill_instructions" not in blob:
        result.add("P0", "missing_fetch_skill_instructions", "Live session 没有观察到 fetch_skill_instructions。", compact_blob[:5000])
    if "skill-creator" not in blob:
        result.add("P1", "missing_skill_creator_contract_read", "Live session 没有观察到 skill-creator/full 合同读取，生成 skill 时可能再次缺 YAML/schema。", compact_blob[:5000])
    if not _has_structured_skill_file_read(live, "references/skill-template.md"):
        result.add(
            "P1",
            "missing_huashu_template_continuation_read",
            "Live session 没有观察到通过 fetch_skill_instructions(relative_path=...) 续读 huashu-nuwa skill-template。",
            compact_blob[:7000],
        )
    if not _has_structured_skill_file_read(live, "references/extraction-framework.md"):
        result.add(
            "P1",
            "missing_huashu_framework_continuation_read",
            "Live session 没有观察到通过 fetch_skill_instructions(relative_path=...) 续读 huashu-nuwa extraction-framework。",
            compact_blob[:7000],
        )
    if re.search(
        r"当前任务.*阻塞|不可绕过的运行时约束|无法启动修复流程|需要用户提供.*(?:节点|支持)|只需提供.*节点ID|"
        r"当前卡住|遇到.*卡点|工程运行时.*失败|没办法直接.*写入|权限恢复之后|暂时.*无法.*写入",
        live.final_text or "",
    ):
        result.add(
            "P1",
            "final_response_reports_false_blocked_state",
            "最终用户可见回复仍声称任务被阻塞或需要额外执行节点；这会造成 live 假通过。",
            live.final_text[:3000],
        )
    if not re.search(r"research_broker|runtime\.episode\..*research|research_evidence_bundle|Web Research Architect|claimTable|sourceMatrix", blob, re.I):
        result.add("P1", "missing_research_evidence", "没有观察到 Research Runtime evidence 或 Architect synthesis 证据。", blob[:5000])
    if not re.search(r"runtime_broker|engineering|write_native_file|patch_bundle|work_plan_ready|文件|SKILL\.md", blob, re.I):
        result.add("P1", "missing_engineering_or_write_trace", "没有观察到 Engineering/文件写入链路证据。", blob[:5000])
    if re.search(r"已(经)?分析.*视频|我看了.*视频|视频中.*显示|画面.*显示|从视频.*可以看到", blob) and not re.search(
        r"vision|视觉|video|字幕|transcript|download_media_for_vision|vision_media_analyzer", blob, re.I
    ):
        result.add("P1", "video_analysis_claim_without_evidence", "疑似声称已分析视频，但没有观察到视频/视觉/字幕证据。", blob[:5000])

    skill_file = target_dir / "SKILL.md"
    if not skill_file.exists():
        result.add("P0", "skill_file_missing", f"缺少最终 SKILL.md：{skill_file}")
        return
    try:
        from runtimes.extensions.skills.artifact_validator import SkillArtifactValidator

        validation = SkillArtifactValidator.validate(target_dir, require_huashu_research=True)
        if not validation.ok:
            result.add(
                "P0",
                "skill_artifact_validator_failed",
                "SkillArtifactValidator 未通过，不能把该 skill 标记为完成。",
                validation.as_dict(),
            )
    except Exception as exc:  # noqa: BLE001
        result.add("P0", "skill_artifact_validator_exception", f"{type(exc).__name__}: {exc}")
    skill_text = _read_text(skill_file)
    if not skill_text.lstrip().startswith("---"):
        result.add("P0", "skill_missing_frontmatter", "SKILL.md 缺少 YAML frontmatter，SkillLoader 会忽略该 skill。", skill_text[:1200])
    if len(skill_text) < 4000:
        result.add("P1", "skill_file_too_short", "SKILL.md 内容过短，疑似空模板或未完成。", {"chars": len(skill_text), "preview": skill_text[:1200]})
    missing_markers = [marker for marker in _skill_markers(target, game) if marker not in skill_text]
    if missing_markers:
        result.add("P1", "skill_missing_required_sections", "SKILL.md 缺少关键内容标记。", {"missing": missing_markers})
    if PLACEHOLDER_PATTERN.search(skill_text):
        result.add("P1", "skill_contains_placeholder_text", "SKILL.md 仍含占位/待补充文本。", skill_text[:1200])
    if str(target_dir).lower().startswith(str(Path.home() / ".agents" / "skills").lower()):
        result.add("P0", "skill_written_to_global_root", "最终产物写到了全局 skill 目录，而不是目标工作区。", str(target_dir))
    if ".claude" in str(target_dir).lower():
        result.add("P0", "skill_written_to_legacy_claude_root", "最终产物写到了旧 .claude/skills 目录。", str(target_dir))

    research_dir = target_dir / "references" / "research"
    for filename in REQUIRED_RESEARCH_FILES:
        path = research_dir / filename
        if not path.exists():
            result.add("P0", "research_file_missing", f"缺少 huashu-nuwa 要求的调研文件：{filename}")
            continue
        text = _read_text(path)
        if len(text.strip()) < 350:
            result.add("P1", "research_file_too_short", f"调研文件内容过短：{filename}", {"chars": len(text), "preview": text[:800]})
        if not re.search(r"https?://|来源|source|可信|confidence|官方|HoYo|米哈游", text, re.I):
            result.add("P1", "research_file_missing_sources", f"调研文件缺少来源或可信度标记：{filename}", text[:1000])
        if PLACEHOLDER_PATTERN.search(text):
            result.add("P1", "research_file_contains_placeholder_text", f"调研文件仍含占位/待补充文本：{filename}", text[:1000])
    if not (target_dir / "references" / "sources").exists():
        result.add("P1", "sources_dir_missing", "缺少 references/sources/ 目录。")
    if not (target_dir / "scripts").exists():
        result.add("P1", "scripts_dir_missing", "缺少 scripts/ 目录。")

    try:
        from runtimes.extensions.skills.loader import SkillLoader, fetch_skill_instructions

        matches = SkillLoader.resolve_skill_matches(
            skill_dir_name,
            force_refresh=True,
            runtime_kind="chat",
            explicit_workspace_path=str(workspace),
        )
        target_match = next(
            (
                item
                for item in matches
                if str(item.get("skillRoot") or item.get("path") or "").lower() == str(target_dir).lower()
            ),
            None,
        )
        if not target_match:
            result.add(
                "P0",
                "generated_skill_not_discoverable",
                f"生成后 SkillLoader 无法在目标 workspace 发现 {skill_dir_name}。",
                {"matches": matches[:8]},
            )
        fetch_candidates = [str(target_dir)]
        if target_match:
            fetch_candidates.extend(
                str(item)
                for item in (
                    target_match.get("skillId"),
                    target_match.get("skillName"),
                    skill_dir_name,
                )
                if str(item or "").strip()
            )
        fetched = ""
        fetch_errors: list[str] = []
        for fetch_name in fetch_candidates:
            fetched = fetch_skill_instructions.func(fetch_name, detail_level="summary")
            if target in fetched or skill_dir_name in fetched:
                break
            fetch_errors.append(f"{fetch_name}: {fetched[:600]}")
        if target not in fetched and skill_dir_name not in fetched:
            result.add(
                "P1",
                "generated_skill_fetch_smoke_failed",
                f"生成后的 skill fetch smoke 未返回 {target} 相关内容。",
                "\n\n".join(fetch_errors)[:3000] or fetched[:2000],
            )
    except Exception as exc:  # noqa: BLE001
        result.add("P0", "generated_skill_discovery_exception", f"{type(exc).__name__}: {exc}")


def _write_report(result: HuashuAuditResult, output_root: Path) -> Path:
    report_dir = _report_dir(output_root, result.timestamp)
    report_dir.mkdir(parents=True, exist_ok=True)
    result.report_dir = str(report_dir)
    target_label = result.target or "target"
    report_path = report_dir / "HUASHU_NUWA_SKILL_LIVE_AUDIT_ZH.md"
    json_path = report_dir / "result.json"
    lines = [
        f"# huashu-nuwa {target_label} Skill Live Audit",
        "",
        f"- generatedAt: {datetime.now().isoformat()}",
        f"- status: {result.status}",
        f"- target: {result.target or 'n/a'}",
        f"- targetSkillDirName: {result.target_skill_dir_name or 'n/a'}",
        f"- modelProfile: {result.model_profile}",
        f"- sessionId: {result.session_id or 'n/a'}",
        f"- runId: {result.run_id or 'n/a'}",
        f"- targetDir: {result.target_dir}",
        f"- backupDir: {result.backup_dir or 'n/a'}",
        f"- repairAttempts: {getattr(result, 'repair_attempts', 0)}",
        "",
        "## Findings",
        "",
    ]
    if result.findings:
        for finding in result.findings:
            lines.extend(
                [
                    f"### [{finding.severity}] {finding.code}",
                    "",
                    finding.summary,
                    "",
                ]
            )
            if finding.evidence:
                lines.extend(["```json", finding.evidence[:12000], "```", ""])
    else:
        lines.append("- No P0/P1/P2 findings.")
        lines.append("")
    lines.extend(
        [
            "## Generated Files",
            "",
            *[f"- {item}" for item in result.generated_files[:200]],
            "",
            "## Observed Runtime Surface",
            "",
            f"- tools: {', '.join(result.observed_tools) if result.observed_tools else 'n/a'}",
            f"- topics: {', '.join(result.observed_topics[:80]) if result.observed_topics else 'n/a'}",
            "",
            "## Final Text Preview",
            "",
            result.final_text[:3000] or "n/a",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    json_payload = {
        "status": result.status,
        "timestamp": result.timestamp,
        "sessionId": result.session_id,
        "runId": result.run_id,
        "targetDir": result.target_dir,
        "target": result.target,
        "targetSkillDirName": result.target_skill_dir_name,
        "backupDir": result.backup_dir,
        "modelProfile": result.model_profile,
        "findings": [finding.__dict__ for finding in result.findings],
        "observedTools": result.observed_tools,
        "observedTopics": result.observed_topics,
        "generatedFiles": result.generated_files,
        "finalText": result.final_text,
    }
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run huashu-nuwa live audit for creating a character skill in a workspace.")
    parser.add_argument("--preflight", action="store_true", help="Run local/engine preflight checks only.")
    parser.add_argument("--validate-existing-session", default="", help="Validate an already executed live session and generated target directory.")
    parser.add_argument("--live", action="store_true", help="Required to submit a real live Supervisor run.")
    parser.add_argument("--allow-side-effects", action="store_true", help="Required with --live to write the target skill directory.")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", type=Path, default=Path(r"E:\Projects\test7"))
    parser.add_argument("--target", default="三月七")
    parser.add_argument("--game", default="崩坏：星穹铁道")
    parser.add_argument("--target-skill-dir", default=TARGET_SKILL_DIR_NAME)
    parser.add_argument("--user-prompt", default="", help="Original user utterance to prepend before the audit contract.")
    parser.add_argument("--spec-mode", action="store_true", help="Submit the live prompt with specMode=true.")
    parser.add_argument("--auto-approve-spec", action="store_true", help="Automatically approve generated Spec stages for this workspace during polling.")
    parser.add_argument("--clear-workspace-sessions", action="store_true", help="Delete existing Engine sessions bound to this workspace before live submission.")
    parser.add_argument("--model-profile", default=None)
    parser.add_argument(
        "--model-fallbacks",
        default=",".join(DEFAULT_MODEL_FALLBACKS),
        help="Comma-separated model profiles to try in order. --model-profile is prepended when provided.",
    )
    parser.add_argument("--max-wait", type=float, default=3600.0)
    parser.add_argument("--repair-attempts", type=int, default=2)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_ROOT)
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    workspace = args.workspace.resolve()
    if args.target_skill_dir:
        requested_target_dir = Path(args.target_skill_dir)
        if requested_target_dir.is_absolute():
            target_dir = requested_target_dir.resolve()
            skill_dir_name = target_dir.name
        else:
            skill_dir_name = str(requested_target_dir).strip() or TARGET_SKILL_DIR_NAME
            target_dir = _target_dir(workspace, skill_dir_name)
    else:
        skill_dir_name = TARGET_SKILL_DIR_NAME
        target_dir = _target_dir(workspace, skill_dir_name)
    configured_fallbacks = [item.strip() for item in str(args.model_fallbacks or "").split(",") if item.strip()]
    if args.model_profile:
        requested_profile = str(args.model_profile).strip()
        model_fallbacks = [requested_profile, *[item for item in configured_fallbacks if item != requested_profile]]
    else:
        model_fallbacks = configured_fallbacks or [_default_model_profile_label()]
    model_profile = model_fallbacks[0]
    result = HuashuAuditResult(
        timestamp=timestamp,
        target_dir=str(target_dir),
        model_profile=model_profile,
        target=args.target,
        target_skill_dir_name=skill_dir_name,
    )

    preflight_findings = _preflight(workspace, require_engine=args.live, engine_url=args.engine_url)
    result.findings.extend(preflight_findings)
    if args.preflight and not args.live:
        result.status = "failed" if any(item.severity == "P0" for item in result.findings) else "ok"
        for finding in result.findings:
            print(f"[{finding.severity}] {finding.code}: {finding.summary}")
        if not result.findings:
            print("preflight=ok")
        if args.write_report:
            print(f"report={_write_report(result, args.output_dir)}")
        return 1 if any(item.severity == "P0" for item in result.findings) else 0

    if str(args.validate_existing_session or "").strip():
        live = _load_existing_live_case(str(args.validate_existing_session).strip())
        _validate_live_result(
            result,
            live,
            target_dir,
            workspace,
            target=args.target,
            game=args.game,
            skill_dir_name=skill_dir_name,
        )
        result.session_id = live.session_id
        result.run_id = live.run_id
        result.observed_tools = list(live.actual_tools)
        result.observed_topics = list(live.observed_topics)
        result.generated_files = _generated_manifest(target_dir)
        result.final_text = live.final_text
        result.status = "failed" if result.has_blocking_failures else "ok"
        for finding in result.findings:
            print(f"[{finding.severity}] {finding.code}: {finding.summary}")
        print(f"status={result.status}")
        print(f"sessionId={result.session_id or 'n/a'}")
        print(f"targetDir={result.target_dir}")
        if args.write_report:
            print(f"report={_write_report(result, args.output_dir)}")
        return 1 if result.has_blocking_failures else 0

    if not args.live:
        print("Refusing to run full audit without --live. Use --preflight for read-only checks.")
        return 2
    if not args.allow_side_effects:
        print("Refusing to run live skill creation without --allow-side-effects.")
        return 2
    if any(item.severity == "P0" for item in result.findings):
        result.status = "failed"
        for finding in result.findings:
            print(f"[{finding.severity}] {finding.code}: {finding.summary}")
        if args.write_report:
            print(f"report={_write_report(result, args.output_dir)}")
        return 1

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if args.clear_workspace_sessions:
        deleted_sessions = _clear_workspace_sessions(workspace)
        print(f"clearedWorkspaceSessions={len(deleted_sessions)}")
    backup_dir = _backup_existing_target(target_dir, workspace, timestamp)
    result.backup_dir = str(backup_dir) if backup_dir else None
    final_candidate: HuashuAuditResult | None = None
    attempts_used = 0
    for model_profile in model_fallbacks:
        print(f"modelAttempt={model_profile}")
        live = _submit_live_case(
            args.engine_url,
            workspace=workspace,
            target=args.target,
            game=args.game,
            target_dir=target_dir,
            skill_dir_name=skill_dir_name,
            model_profile=model_profile,
            timestamp=f"{timestamp}-{re.sub(r'[^A-Za-z0-9]+', '-', model_profile).strip('-').lower()}",
            spec_mode=args.spec_mode,
            user_prompt=args.user_prompt,
        )
        if live.status != "failed":
            live = _poll_case_with_spec_auto_approve(
                args.engine_url,
                live,
                max_wait=args.max_wait,
                workspace=workspace,
                auto_approve_spec=args.auto_approve_spec,
            )
        model_candidate: HuashuAuditResult | None = None
        model_attempts_used = 0
        for attempt in range(0, max(0, args.repair_attempts) + 1):
            candidate = HuashuAuditResult(
                timestamp=timestamp,
                target_dir=str(target_dir),
                backup_dir=result.backup_dir,
                model_profile=model_profile,
                target=args.target,
                target_skill_dir_name=skill_dir_name,
            )
            _validate_live_result(
                candidate,
                live,
                target_dir,
                workspace,
                target=args.target,
                game=args.game,
                skill_dir_name=skill_dir_name,
            )
            if candidate.has_blocking_failures and live.status == "timeout" and _live_run_still_active(live):
                candidate.add(
                    "P0",
                    "live_run_still_active_no_repair_submitted",
                    "Live run 超时但 Engine run 仍处于 active 状态；harness 不再向同一会话插入 repair guidance，以免污染长任务事件链。",
                    {"runId": live.run_id, "sessionId": live.session_id, "failureReason": live.failure_reason},
                )
                model_candidate = candidate
                model_attempts_used = attempt
                break
            if not candidate.has_blocking_failures or attempt >= max(0, args.repair_attempts):
                model_candidate = candidate
                model_attempts_used = attempt
                break
            model_attempts_used = attempt + 1
            print(f"repairAttempt={model_attempts_used}")
            live = _submit_repair_case(
                args.engine_url,
                workspace=workspace,
                target=args.target,
                game=args.game,
                target_dir=target_dir,
                model_profile=model_profile,
                session_id=candidate.session_id or live.session_id or f"huashu-nuwa-{skill_dir_name}-live-{timestamp}",
                findings=candidate.findings,
                attempt=model_attempts_used,
                spec_mode=args.spec_mode,
            )
            if live.status != "failed":
                live = _poll_case_with_spec_auto_approve(
                    args.engine_url,
                    live,
                    max_wait=args.max_wait,
                    workspace=workspace,
                    auto_approve_spec=args.auto_approve_spec,
                )
        if model_candidate is None:
            continue
        final_candidate = model_candidate
        attempts_used = model_attempts_used
        if not model_candidate.has_blocking_failures:
            break
        joined_findings = " ".join(f"{item.code} {item.summary} {item.evidence}" for item in model_candidate.findings).lower()
        retryable = any(token in joined_findings for token in ("quota", "rate", "provider", "model", "timeout", "429", "too frequent"))
        if not retryable:
            break
    if final_candidate is not None:
        result.session_id = final_candidate.session_id
        result.run_id = final_candidate.run_id
        result.observed_tools = final_candidate.observed_tools
        result.observed_topics = final_candidate.observed_topics
        result.generated_files = final_candidate.generated_files
        result.final_text = final_candidate.final_text
        result.findings.extend(final_candidate.findings)
    result.repair_attempts = attempts_used
    result.status = "failed" if result.has_blocking_failures else "ok"
    for finding in result.findings:
        print(f"[{finding.severity}] {finding.code}: {finding.summary}")
    print(f"status={result.status}")
    print(f"sessionId={result.session_id or 'n/a'}")
    print(f"runId={result.run_id or 'n/a'}")
    print(f"targetDir={result.target_dir}")
    if result.backup_dir:
        print(f"backupDir={result.backup_dir}")
    if args.write_report:
        print(f"report={_write_report(result, args.output_dir)}")
    return 1 if result.has_blocking_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
