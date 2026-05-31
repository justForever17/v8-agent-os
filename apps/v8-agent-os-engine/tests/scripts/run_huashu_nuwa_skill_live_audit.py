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

    def add(self, severity: str, code: str, summary: str, evidence: Any = "") -> None:
        self.findings.append(Finding(severity, code, summary, _redact(evidence) if evidence else ""))

    @property
    def has_blocking_failures(self) -> bool:
        return any(item.severity in {"P0", "P1"} for item in self.findings)


def _target_dir(workspace: Path) -> Path:
    return workspace / ".agents" / "skills" / TARGET_SKILL_DIR_NAME


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


def _prompt(target: str, game: str, target_dir: Path) -> str:
    return f"""这是一次 V8OS 主链 live 验收，请完整执行，不要只给计划。

目标：使用已选择的 huashu-nuwa skill，调研米哈游游戏《{game}》角色「{target}」，并按 huashu-nuwa 的要求蒸馏生成一个可运行的角色视角 skill。

硬性要求：
1. 第一阶段必须读取 fetch_skill_instructions(skill_name="huashu-nuwa", detail_level="full") 和 fetch_skill_instructions(skill_name="skill-creator", detail_level="full")，不要只读摘要；huashu-nuwa 规定蒸馏流程，skill-creator 规定可加载 SKILL.md schema。
   - 读取 huashu-nuwa full 后，必须按 continuationManifest 继续读取：
     fetch_skill_instructions(skill_name="huashu-nuwa", relative_path="references/skill-template.md")
     fetch_skill_instructions(skill_name="huashu-nuwa", relative_path="references/extraction-framework.md")
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
   name: sanyueqi-perspective
   description: 三月七（《{game}》）的思维框架与表达方式。用于以三月七视角分析问题、回应选择、生成台词风格建议。
   ---
   并且正文至少包含这些一级或二级章节：使用说明、身份卡、心智模型、决策启发式、表达DNA、时间线、诚实边界、调研来源。
5. 三月七是虚构角色，请把 huashu-nuwa 的人物调研六维适配为：
   - 官方设定、角色故事、角色档案、命途/版本设定
   - 剧情台词、短信、同行任务、活动剧情中的表达方式
   - 口头禅、句式、语气、幽默方式、情绪节奏
   - 官方/玩家/媒体解读的外部视角，并保留冲突
   - 关键剧情行为、选择、成长弧线和内在张力
   - 版本时间线、登场节点和信息截止边界
6. Research Runtime 必须产出可核验来源；Web Research Architect 必须把清洗材料提纯为 evidence pack / claim table / source matrix，不能把搜索 snippet 当最终调研结论。
7. 如果遇到 gemini-video 或视频转写要求，但本轮没有 Gemini key 或本地视频：优先使用 V8OS 内置视觉/附件/字幕/网页读取能力；仍不可用时在诚实边界注明“未进行视频画面级分析”，不要假装看过视频。
8. 本 live 验收已经授权写入 test7 工作区；文件副作用应走 Engineering/runtime 路径。不要写到全局 ~/.agents/skills，也不要写到旧 .claude/skills。
9. huashu-nuwa 的 Phase 1.5 / Phase 2.5 检查点在本次验收中视为用户授权继续：如果质量足够请继续；如果不足，请补证或在诚实边界标注后交付当前最优版本，不要无限等待用户。

最终回复只需要给出：生成目录、关键文件清单、调研质量摘要、无法覆盖的信息边界、二次复用方式。
"""


def _make_live_result(session_id: str, prompt: str, title: str = "huashu-nuwa 真实生成三月七 skill") -> LiveCaseResult:
    skill_refs, mentions = _huashu_skill_reference()
    case = LiveCaseSpec(
        case_id="huashu_nuwa_sanyueqi_skill",
        title=title,
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
    model_profile: str,
    timestamp: str,
) -> LiveCaseResult:
    session_id = f"huashu-nuwa-sanyueqi-live-{timestamp}"
    prompt = _prompt(target, game, target_dir)
    result = _make_live_result(session_id, prompt)
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
            "allowSideEffects": True,
            "huashuNuwaSkillLiveAudit": True,
            "targetSkillPath": str(target_dir),
            "skillReferences": result.spec.skill_references or None,
            "contextMentions": result.spec.context_mentions or None,
        },
    }
    started = time.perf_counter()
    try:
        response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    except Exception as exc:  # noqa: BLE001
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
1. SKILL.md 必须以 YAML frontmatter 开头：
   ---
   name: sanyueqi-perspective
   description: 三月七（《{game}》）的思维框架与表达方式。用于以三月七视角分析问题、回应选择、生成台词风格建议。
   ---
2. SKILL.md 正文必须显式包含：使用说明、身份卡、心智模型、决策启发式、表达DNA、时间线、诚实边界、调研来源。
3. SKILL.md 不能是简短角色扮演提示词；它必须是可复用 skill，至少 4000 字符，能教另一个 agent 如何以「{target}」视角思考和表达。
4. references/research/01-writings.md 到 06-timeline.md 必须全部保留，每个文件必须有来源 URL 或来源说明/可信度标记。
5. 不要声称分析过未实际读取的视频；如无视频画面证据，在诚实边界写清。
6. 修复完成后再次让 SkillLoader 能在 test7 workspace 发现并 fetch `sanyueqi-perspective`。

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
) -> LiveCaseResult:
    prompt = _repair_prompt(target=target, game=game, target_dir=target_dir, findings=findings, attempt=attempt)
    result = _make_live_result(session_id, prompt, title=f"huashu-nuwa 三月七 skill 自动修复 #{attempt}")
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
        response = _json_request(f"{_engine_api_base(engine_url)}/chat/submit", method="POST", payload=payload, timeout=30)
    except Exception as exc:  # noqa: BLE001
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


def _validate_live_result(result: HuashuAuditResult, live: LiveCaseResult, target_dir: Path, workspace: Path) -> None:
    result.session_id = live.session_id
    result.run_id = live.run_id
    result.observed_tools = list(live.actual_tools)
    result.observed_topics = list(live.observed_topics)
    result.final_text = live.final_text
    result.generated_files = _generated_manifest(target_dir)
    blob = _event_blob(live)

    if live.status in {"failed", "timeout"}:
        result.add("P0", "live_run_not_terminal", f"Live run 未正常完成：{live.failure_reason or live.status}", live.key_events[-8:])
    if "fetch_skill_instructions" not in blob:
        result.add("P0", "missing_fetch_skill_instructions", "Live session 没有观察到 fetch_skill_instructions。", blob[:5000])
    if "skill-creator" not in blob:
        result.add("P1", "missing_skill_creator_contract_read", "Live session 没有观察到 skill-creator/full 合同读取，生成 skill 时可能再次缺 YAML/schema。", blob[:5000])
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
    missing_markers = [marker for marker in SKILL_MARKERS if marker not in skill_text]
    if missing_markers:
        result.add("P1", "skill_missing_required_sections", "SKILL.md 缺少关键内容标记。", {"missing": missing_markers})
    if PLACEHOLDER_PATTERN.search(skill_text):
        result.add("P1", "skill_contains_placeholder_text", "SKILL.md 仍含占位/待补充文本。", skill_text[:1200])
    if str(target_dir).lower().startswith(str(Path.home() / ".agents" / "skills").lower()):
        result.add("P0", "skill_written_to_global_root", "最终产物写到了全局 skill 目录，而不是 test7 工作区。", str(target_dir))
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
            TARGET_SKILL_DIR_NAME,
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
                "生成后 SkillLoader 无法在 test7 workspace 发现 sanyueqi-perspective。",
                {"matches": matches[:8]},
            )
        fetch_candidates = [str(target_dir)]
        if target_match:
            fetch_candidates.extend(
                str(item)
                for item in (
                    target_match.get("skillId"),
                    target_match.get("skillName"),
                    TARGET_SKILL_DIR_NAME,
                )
                if str(item or "").strip()
            )
        fetched = ""
        fetch_errors: list[str] = []
        for fetch_name in fetch_candidates:
            fetched = fetch_skill_instructions.func(fetch_name, detail_level="summary")
            if "三月七" in fetched or TARGET_SKILL_DIR_NAME in fetched:
                break
            fetch_errors.append(f"{fetch_name}: {fetched[:600]}")
        if "三月七" not in fetched and TARGET_SKILL_DIR_NAME not in fetched:
            result.add(
                "P1",
                "generated_skill_fetch_smoke_failed",
                "生成后的 skill fetch smoke 未返回三月七相关内容。",
                "\n\n".join(fetch_errors)[:3000] or fetched[:2000],
            )
    except Exception as exc:  # noqa: BLE001
        result.add("P0", "generated_skill_discovery_exception", f"{type(exc).__name__}: {exc}")


def _write_report(result: HuashuAuditResult, output_root: Path) -> Path:
    report_dir = _report_dir(output_root, result.timestamp)
    report_dir.mkdir(parents=True, exist_ok=True)
    result.report_dir = str(report_dir)
    report_path = report_dir / "HUASHU_NUWA_SANYUEQI_LIVE_AUDIT_ZH.md"
    json_path = report_dir / "result.json"
    lines = [
        "# huashu-nuwa 三月七 Skill Live Audit",
        "",
        f"- generatedAt: {datetime.now().isoformat()}",
        f"- status: {result.status}",
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
    parser = argparse.ArgumentParser(description="Run huashu-nuwa live audit for creating March 7th skill in test7 workspace.")
    parser.add_argument("--preflight", action="store_true", help="Run local/engine preflight checks only.")
    parser.add_argument("--validate-existing-session", default="", help="Validate an already executed live session and generated target directory.")
    parser.add_argument("--live", action="store_true", help="Required to submit a real live Supervisor run.")
    parser.add_argument("--allow-side-effects", action="store_true", help="Required with --live to write the target skill directory.")
    parser.add_argument("--engine-url", default=DEFAULT_ENGINE_URL)
    parser.add_argument("--workspace", type=Path, default=Path(r"E:\Projects\test7"))
    parser.add_argument("--target", default="三月七")
    parser.add_argument("--game", default="崩坏：星穹铁道")
    parser.add_argument("--model-profile", default=None)
    parser.add_argument("--max-wait", type=float, default=3600.0)
    parser.add_argument("--repair-attempts", type=int, default=2)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_ROOT)
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    workspace = args.workspace.resolve()
    target_dir = _target_dir(workspace)
    model_profile = args.model_profile or _default_model_profile_label()
    result = HuashuAuditResult(
        timestamp=timestamp,
        target_dir=str(target_dir),
        model_profile=model_profile,
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
        _validate_live_result(result, live, target_dir, workspace)
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
    backup_dir = _backup_existing_target(target_dir, workspace, timestamp)
    result.backup_dir = str(backup_dir) if backup_dir else None
    live = _submit_live_case(
        args.engine_url,
        workspace=workspace,
        target=args.target,
        game=args.game,
        target_dir=target_dir,
        model_profile=model_profile,
        timestamp=timestamp,
    )
    if live.status != "failed":
        live = _poll_case(args.engine_url, live, max_wait=args.max_wait)
    final_candidate: HuashuAuditResult | None = None
    attempts_used = 0
    for attempt in range(0, max(0, args.repair_attempts) + 1):
        candidate = HuashuAuditResult(
            timestamp=timestamp,
            target_dir=str(target_dir),
            backup_dir=result.backup_dir,
            model_profile=model_profile,
        )
        _validate_live_result(candidate, live, target_dir, workspace)
        if not candidate.has_blocking_failures or attempt >= max(0, args.repair_attempts):
            final_candidate = candidate
            attempts_used = attempt
            break
        attempts_used = attempt + 1
        print(f"repairAttempt={attempts_used}")
        live = _submit_repair_case(
            args.engine_url,
            workspace=workspace,
            target=args.target,
            game=args.game,
            target_dir=target_dir,
            model_profile=model_profile,
            session_id=candidate.session_id or live.session_id or f"huashu-nuwa-sanyueqi-live-{timestamp}",
            findings=candidate.findings,
            attempt=attempts_used,
        )
        if live.status != "failed":
            live = _poll_case(args.engine_url, live, max_wait=args.max_wait)
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
