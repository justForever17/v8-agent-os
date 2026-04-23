from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.database import db
from core.json_safe import to_jsonable
from core.realtime_protocol import utc_now_iso
from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME


WORKFLOW_MEMORY_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "hintInjectionEnabled": True,
    "progressiveHintsEnabled": True,
    "minSuccessCount": 2,
    "errorfulSuccessRequiresUserAcceptance": True,
    "maxInjectedHints": 2,
    "maxHintChars": 900,
    "maxActiveWorkflowGuidesPerRun": 2,
    "quarantineOnNegativeFeedback": True,
    "requireApprovalForSideEffects": True,
    "riskTierActivationPolicy": {
        "read_only": "auto",
        "low": "auto",
        "medium": "approval",
        "high": "approval",
        "critical": "quarantine",
    },
}

ACTIVE_WORKFLOW_STATUSES = {"active_hint", "approved"}
TERMINAL_GUIDE_STATES = {"verified", "failed", "ignored", "contradicted"}
NEGATIVE_HINT_OUTCOMES = {"ignored", "contradicted", "caused_failure"}


def workflow_memory_config() -> Dict[str, Any]:
    memory_config = storage.get_memory_config() or {}
    raw = memory_config.get("workflowMemory")
    if not isinstance(raw, dict):
        raw = {}
    cfg = {**WORKFLOW_MEMORY_DEFAULTS, **raw}
    for key in (
        "enabled",
        "hintInjectionEnabled",
        "progressiveHintsEnabled",
        "errorfulSuccessRequiresUserAcceptance",
        "quarantineOnNegativeFeedback",
        "requireApprovalForSideEffects",
    ):
        cfg[key] = bool(cfg.get(key))
    for key, default, minimum, maximum in (
        ("minSuccessCount", 2, 1, 10),
        ("maxInjectedHints", 2, 0, 5),
        ("maxHintChars", 900, 240, 2400),
        ("maxActiveWorkflowGuidesPerRun", 2, 0, 10),
    ):
        try:
            cfg[key] = max(minimum, min(int(cfg.get(key) or default), maximum))
        except (TypeError, ValueError):
            cfg[key] = default
    policy = cfg.get("riskTierActivationPolicy")
    if not isinstance(policy, dict):
        policy = {}
    default_policy = dict(WORKFLOW_MEMORY_DEFAULTS["riskTierActivationPolicy"])
    for tier, action in list(policy.items()):
        normalized = str(action or "").strip().lower()
        if normalized not in {"auto", "approval", "quarantine"}:
            policy[tier] = default_policy.get(tier, "approval")
        else:
            policy[tier] = normalized
    cfg["riskTierActivationPolicy"] = {**default_policy, **policy}
    return cfg


def _json_dump(value: Any) -> str:
    return json.dumps(to_jsonable(value if value is not None else []), ensure_ascii=False)


def _json_load(value: Any, fallback: Any = None) -> Any:
    if value is None or value == "":
        return [] if fallback is None else fallback
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return [] if fallback is None else fallback


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _uniq(items: Iterable[Any], *, limit: int = 24) -> List[Any]:
    result: List[Any] = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(to_jsonable(item), ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _norm_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _signature_for(task_family: str, triggers: Iterable[Any], intent: str = "") -> str:
    base = " ".join([task_family, " ".join(str(item) for item in triggers), intent]).lower()
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", base)
    compact = " ".join(tokens[:24]) or "workflow"
    digest = hashlib.sha1(compact.encode("utf-8")).hexdigest()[:12]
    return f"wf:{digest}"


def _token_set(text: str) -> set[str]:
    normalized = str(text or "").lower()
    tokens = set(re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", normalized))
    for raw in re.findall(r"[\u4e00-\u9fff]{4,}", normalized):
        for idx in range(max(0, len(raw) - 1)):
            tokens.add(raw[idx : idx + 2])
    return tokens


_GENERIC_HINT_TOKENS = {
    "帮我",
    "生成",
    "创建",
    "制作",
    "使用",
    "需要",
    "一个",
    "技能",
    "流程",
    "任务",
    "create",
    "generate",
    "make",
    "build",
    "use",
    "skill",
    "workflow",
    "task",
}


def _meaningful_tokens(text: str) -> set[str]:
    return {token for token in _token_set(text) if token not in _GENERIC_HINT_TOKENS}


def _normalized_contains(text: str, phrase: Any) -> bool:
    source = str(text or "").lower()
    needle = str(phrase or "").strip().lower()
    if not source or not needle:
        return False
    if needle in source:
        return True
    compact_source = re.sub(r"\s+", "", source)
    compact_needle = re.sub(r"\s+", "", needle)
    return bool(compact_needle and compact_needle in compact_source)


def _anchor_phrases(item: Dict[str, Any]) -> List[str]:
    raw: List[Any] = []
    raw.extend(_as_list(item.get("canonicalTriggerPatterns")))
    raw.extend(_as_list(item.get("firstActionTriggers")))
    raw.extend(_as_list((item.get("metadata") or {}).get("canonicalAnchors") if isinstance(item.get("metadata"), dict) else []))
    phrases: List[str] = []
    for value in raw:
        text = _norm_text(value)
        if not text:
            continue
        tokens = _meaningful_tokens(text)
        # Keep explicit phrases such as "女娲", "huashu-nuwa",
        # "fetch_skill_instructions", but drop template-level noise.
        if len(text) >= 2 and (tokens or "_" in text or "-" in text):
            phrases.append(text)
    return _uniq(phrases, limit=24)


def _intent_anchor_match(item: Dict[str, Any], query: str, q_tokens: set[str]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    for phrase in _anchor_phrases(item):
        if _normalized_contains(query, phrase):
            reasons.append(f"anchor:{phrase[:48]}")
    first_action_text = " ".join(map(str, item.get("firstActionTriggers") or []))
    first_action_overlap = q_tokens & _meaningful_tokens(first_action_text)
    if first_action_overlap:
        reasons.append("first_action:" + ",".join(sorted(first_action_overlap)[:5]))
    family_tokens = _meaningful_tokens(str(item.get("task_family") or ""))
    family_overlap = q_tokens & family_tokens
    if len(family_overlap) >= 2:
        reasons.append("task_family:" + ",".join(sorted(family_overlap)[:5]))
    trigger_text = " ".join(map(str, item.get("canonicalTriggerPatterns") or []))
    trigger_overlap = q_tokens & _meaningful_tokens(trigger_text)
    if trigger_overlap:
        reasons.append("trigger:" + ",".join(sorted(trigger_overlap)[:5]))
    return bool(reasons), reasons


def _row_to_episode(row: Any) -> Dict[str, Any]:
    data = dict(row)
    data["orderedActions"] = _json_load(data.pop("ordered_actions_json", None), [])
    data["toolSkillSequence"] = _json_load(data.pop("tool_skill_sequence_json", None), [])
    data["failureMarkers"] = _json_load(data.pop("failure_markers_json", None), [])
    data["userCorrectionPoints"] = _json_load(data.pop("user_correction_points_json", None), [])
    data["metadata"] = _json_load(data.pop("metadata_json", None), {})
    return data


def _row_to_candidate(row: Any) -> Dict[str, Any]:
    data = dict(row)
    data["canonicalTriggerPatterns"] = _json_load(data.pop("canonical_trigger_patterns_json", None), [])
    data["firstActionTriggers"] = _json_load(data.pop("first_action_triggers_json", None), [])
    data["goldenPathSteps"] = _json_load(data.pop("golden_path_steps_json", None), [])
    data["antiPatterns"] = _json_load(data.pop("anti_patterns_json", None), [])
    data["verificationSteps"] = _json_load(data.pop("verification_steps_json", None), [])
    data["sourceEpisodeIds"] = _json_load(data.pop("source_episode_ids_json", None), [])
    data["approvalRequired"] = bool(data.get("approval_required"))
    data["riskTier"] = data.get("risk_tier") or "low"
    data["lastHintOutcome"] = data.get("last_hint_outcome")
    data["guideState"] = _json_load(data.pop("guide_state_json", None), {})
    data["mergeSuggestion"] = _json_load(data.pop("merge_suggestion_json", None), {})
    data["metadata"] = _json_load(data.pop("metadata_json", None), {})
    return data


def _row_to_hint_event(row: Any) -> Dict[str, Any]:
    data = dict(row)
    data["injectedHint"] = _json_load(data.pop("injected_hint_json", None), {})
    data["metadata"] = _json_load(data.pop("metadata_json", None), {})
    return data


def _risk_tier_from_scope(side_effect_scope: str, tool_sequence: Iterable[Any] = ()) -> str:
    text = " ".join([str(side_effect_scope or ""), " ".join(str(item) for item in tool_sequence)]).lower()
    if any(token in text for token in ("delete", "destructive", "rm ", "remove-item", "format", "credential", "secret", "token", "payment")):
        return "critical"
    if any(token in text for token in ("computer_use", "desktop", "rpa", "launch_app", "click", "paste", "install", "pip ", "npm ", "powershell", "command_session", "run_system_command", "external_worker")):
        return "high"
    if any(token in text for token in ("write", "file", "s3_", "network", "http", "web_", "download", "upload", "config", "mcp", "skill home", "writes_skill_home")):
        return "medium"
    if any(token in text for token in ("read", "observe", "search", "recall", "fetch_skill_instructions", "read-only", "readonly")):
        return "read_only"
    return "low"


def _activation_policy_for_risk(risk_tier: str) -> str:
    cfg = workflow_memory_config()
    policy = cfg.get("riskTierActivationPolicy") if isinstance(cfg.get("riskTierActivationPolicy"), dict) else {}
    return str(policy.get(risk_tier) or "approval").strip().lower()


def _activation_allowed_for_candidate(*, risk_tier: str, current: str) -> Tuple[bool, bool, str]:
    if current in {"approved", "promoted_skill_candidate"}:
        return True, False, "approved_override"
    policy = _activation_policy_for_risk(risk_tier)
    if policy == "auto":
        return True, False, "risk_policy_auto"
    if policy == "quarantine":
        return False, True, "risk_policy_quarantine"
    return False, True, "risk_policy_requires_approval"


DEFAULT_WORKFLOW_SEED_VERSION = "2026-04-23.1"


def _default_workflow_candidate_definitions() -> List[Dict[str, Any]]:
    """System-seeded workflow memories.

    These are ordinary workflow candidates: once inserted they flow through the
    same matcher, ranked-path renderer, hint events, outcomes and Admin
    governance as learned workflows. The seed metadata is only provenance and
    versioning, not a special runtime path.
    """

    return [
        {
            "id": "mw_seed_v8os_engineering_change_verification",
            "taskFamilySignature": "seed:v8os:engineering-change-verification",
            "taskFamily": "V8OS engineering code change verification loop",
            "scope": "global",
            "canonicalTriggerPatterns": [
                "修改代码",
                "修复代码",
                "实现功能",
                "改组件",
                "debug",
                "typecheck",
                "pytest",
                "npm run build",
                "回归测试",
                "code change",
                "implementation",
                "refactor",
            ],
            "firstActionTriggers": [
                "git status",
                "read relevant files",
                "inspect repo state",
                "identify write set",
                "run targeted test",
            ],
            "goldenPathSteps": [
                "先确认 repo 状态、已读文件和计划写集；不要在不了解 dirty worktree 时直接编辑。",
                "做手术式改动，保持 changed files 窄；若涉及并发 subagent，先隔离 write-set。",
                "运行最窄的相关验证：单测、typecheck、py_compile、build 或定点脚本，先小后大。",
                "记录 proof：diff 摘要、验证命令、diagnostics、残余风险；未跑验证不得说 verified。",
            ],
            "antiPatterns": [
                "不要把聊天里的“看起来完成”当成工程完成证据。",
                "不要在未检查 git status 时覆盖用户已有改动。",
                "不要为小修复先跑全仓重型验证，除非窄验证无法覆盖风险。",
            ],
            "verificationSteps": [
                "git status --short 能解释新增/修改文件来源。",
                "至少有一条与改动相关的验证命令或明确说明无法运行。",
                "Proof Ledger 中 verificationStatus 不能在无证据时标记 verified。",
            ],
            "riskTier": "low",
            "confidence": 0.88,
            "maturityScore": 0.86,
            "successCount": 2,
            "metadata": {
                "canonicalAnchors": ["code change", "修改代码", "测试", "验证", "typecheck", "pytest", "build"],
                "actionVariantsByStep": {
                    "0": ["先运行 git status --short 并标记用户已有改动。", "先读入口文件、测试文件和相关配置，列出 read-set/write-set。"],
                    "1": ["优先 apply_patch 做小补丁。", "若发现写集冲突，暂停并让 supervisor 重新切片。"],
                    "2": ["优先跑定点 pytest / py_compile / npm typecheck。", "验证失败时先收集 diagnostics，再改下一轮。"],
                    "3": ["用 Proof Ledger 口径写清 changed files、commands、result、residual risks。"],
                },
                "negativeMatchPatterns": ["生成图片", "生成视频", "写文章", "做ppt", "语音回复"],
            },
        },
        {
            "id": "mw_seed_v8os_task_alignment_before_execution",
            "taskFamilySignature": "seed:v8os:task-alignment-before-execution",
            "taskFamily": "Task-mode alignment before planning or execution",
            "scope": "global",
            "canonicalTriggerPatterns": [
                "任务模式",
                "帮我规划",
                "写计划",
                "拆解任务",
                "分工",
                "多阶段",
                "roadmap",
                "task mode",
                "todos",
                "planner",
            ],
            "firstActionTriggers": [
                "ask_user",
                "clarify acceptance",
                "align granularity",
                "confirm write set",
            ],
            "goldenPathSteps": [
                "如果颗粒度、验收标准、写集或风险不清，先用 ask_user 提 1-3 个聚焦问题对齐，不要急着开写泛计划。",
                "把用户回答压成 task brief：goal、context、writeSet、behaviorScope、requiredCapabilities、acceptanceContract。",
                "再决定 direct / delegate / mixed；计划必须服务执行和验收，不做空泛路线图。",
            ],
            "antiPatterns": [
                "不要在信息足够时为了形式主义反复问问题。",
                "不要把开放式长问卷丢给用户；问题要短、互斥、可决策。",
                "不要绕过用户明确约束直接开始大范围改动。",
            ],
            "verificationSteps": [
                "task brief 至少包含目标、边界、验收和风险。",
                "如果选择不提问，需要写清合理假设。",
            ],
            "riskTier": "read_only",
            "confidence": 0.84,
            "maturityScore": 0.82,
            "successCount": 2,
            "metadata": {
                "canonicalAnchors": ["任务模式", "拆解", "分工", "planner", "todos", "ask_user"],
                "actionVariantsByStep": {
                    "0": ["用 ask_user 问清验收标准和范围。", "若用户已给足上下文，直接列出假设并进入 task brief。", "只问会改变执行路线的问题。"],
                    "1": ["把回答转为 broker-ready task brief。", "列出不做什么和需要保留的用户约束。"],
                    "2": ["先选 executionStrategy，再决定是否委派。"],
                },
                "negativeMatchPatterns": ["闲聊", "解释概念", "生成图片", "翻译一句话"],
            },
        },
        {
            "id": "mw_seed_v8os_skill_instruction_first",
            "taskFamilySignature": "seed:v8os:skill-instruction-first",
            "taskFamily": "Fetch skill instructions before executing named skills",
            "scope": "global",
            "canonicalTriggerPatterns": [
                "使用技能",
                "用这个skill",
                "use skill",
                "女娲技能",
                "huashu-nuwa",
                "造skill",
                "蒸馏",
                "fetch_skill_instructions",
            ],
            "firstActionTriggers": [
                "fetch_skill_instructions",
                "read skill instructions",
                "resolve skill alias",
            ],
            "goldenPathSteps": [
                "先调用 fetch_skill_instructions 读取用户点名或强命中的技能说明；不要凭 description 或记忆直接执行。",
                "把技能说明压成 task brief：输入、步骤、产物、写集、风险和验收。",
                "执行前由 supervisor 保留最终验收；需要并行时再委派 subagent，但 subagent 只接收 task brief truth。",
            ],
            "antiPatterns": [
                "不要因为看到技能名就假装已经加载完整 SKILL.md。",
                "不要把父级 route 候选列表当成技能说明本体。",
                "不要让 subagent 直接接收完整用户历史代替 task brief。",
            ],
            "verificationSteps": [
                "SYSTEM_CONTENT 或工具记录能看到 fetch_skill_instructions 命中。",
                "task brief 中显式列出该 skill 的产物和验收标准。",
            ],
            "riskTier": "read_only",
            "confidence": 0.9,
            "maturityScore": 0.9,
            "successCount": 2,
            "metadata": {
                "canonicalAnchors": ["使用技能", "女娲", "huashu-nuwa", "造skill", "蒸馏", "fetch_skill_instructions"],
                "actionVariantsByStep": {
                    "0": ["优先用精确 skill id/name 调 fetch_skill_instructions。", "如果别名命中不确定，查看 resolver 候选和诊断后再执行。"],
                    "1": ["提取技能输入、步骤、产物、写集、风险和验收。"],
                    "2": ["supervisor 负责采纳/重试/最终验收；subagent 只做被委派的片段。"],
                },
                "negativeMatchPatterns": ["普通技能介绍", "skill列表", "不使用技能"],
            },
        },
        {
            "id": "mw_seed_v8os_voice_reply_contract",
            "taskFamilySignature": "seed:v8os:voice-reply-contract",
            "taskFamily": "V8OS voice reply formatting contract",
            "scope": "global",
            "canonicalTriggerPatterns": [
                "发语音",
                "语音回复",
                "用语音说",
                "朗读",
                "tts",
                "voice reply",
                "audio response",
            ],
            "firstActionTriggers": [
                "voice tag",
                "plain spoken text",
                "audio runtime",
            ],
            "goldenPathSteps": [
                "面向语音输出时，用约定的语音标签/语音载荷边界包裹要朗读的内容。",
                "语音正文保持可朗读：不用 Markdown 表格、代码块、裸 URL、复杂符号或过多括号。",
                "必要时另给一行短文本摘要，避免把视觉卡片当作语音可消费内容。",
            ],
            "antiPatterns": [
                "不要在语音正文里放代码围栏、表格、emoji 或难读特殊字符。",
                "不要假设语音端能展示 artifact/runtime card。",
            ],
            "verificationSteps": [
                "语音正文只包含自然语言和少量普通标点。",
                "视觉链接或文件引用用普通文本摘要替代。",
            ],
            "riskTier": "read_only",
            "confidence": 0.82,
            "maturityScore": 0.78,
            "successCount": 2,
            "metadata": {
                "canonicalAnchors": ["语音", "voice", "tts", "朗读", "audio"],
                "actionVariantsByStep": {
                    "0": ["先生成干净的 spoken text，再交给 audio runtime。", "如果协议要求标签，确保只包裹朗读正文。"],
                    "1": ["删除 Markdown、代码块、裸 URL 和难读符号。"],
                    "2": ["给非语音 surface 保留一行可读摘要。"],
                },
                "negativeMatchPatterns": ["音频转文字", "语音识别", "分析音频"],
            },
        },
        {
            "id": "mw_seed_v8os_git_handoff_gate",
            "taskFamilySignature": "seed:v8os:git-handoff-gate",
            "taskFamily": "Git commit and handoff readiness gate",
            "scope": "global",
            "canonicalTriggerPatterns": [
                "提交git",
                "提交代码",
                "commit",
                "git commit",
                "push",
                "开PR",
                "pull request",
                "发布改动",
            ],
            "firstActionTriggers": [
                "git status",
                "git diff",
                "verification evidence",
                "commit message",
            ],
            "goldenPathSteps": [
                "提交前先检查 git status 和 diff，确认只包含本任务相关改动且没有用户未授权改动。",
                "确认验证证据：至少有定点测试、typecheck、build 或无法运行的明确说明。",
                "只有用户明确要求提交/推送时才执行 git commit/push；提交信息要概括意图和风险。",
            ],
            "antiPatterns": [
                "不要自动提交用户未要求提交的改动。",
                "不要把 unrelated dirty files 打进同一个 commit。",
                "不要在验证失败或未解释风险时宣称 ready to ship。",
            ],
            "verificationSteps": [
                "git status --short 与 diff summary 已检查。",
                "验证命令和结果已记录。",
                "commit 范围与用户请求一致。",
            ],
            "riskTier": "medium",
            "confidence": 0.8,
            "maturityScore": 0.74,
            "successCount": 2,
            "metadata": {
                "canonicalAnchors": ["git", "commit", "提交", "push", "PR", "pull request"],
                "actionVariantsByStep": {
                    "0": ["用 git status --short 找出 unrelated dirty files。", "用 git diff --stat 确认提交范围。"],
                    "1": ["复用本轮 Proof Ledger 验证证据。", "若没跑验证，先补最窄验证或说明无法运行。"],
                    "2": ["只有用户明确授权时才 git commit。", "若需要 PR，先提交再推送并打开草稿 PR。"],
                },
                "sideEffectScope": "git_commit_push_requires_user_intent",
                "negativeMatchPatterns": ["查看git状态", "解释git", "不要提交"],
            },
        },
    ]


class WorkflowMemoryService:
    """Programmatic behavior memory: episodes, candidates, and progressive hints."""

    export_root = V8_AGENT_OS_HOME / "memory" / "workflows"

    def normalize_episode_payload(
        self,
        payload: Dict[str, Any],
        *,
        session_id: str,
        run_id: Optional[str],
        scope: str,
        extraction_source: str = "memory_agent",
    ) -> Dict[str, Any]:
        task_family = _norm_text(payload.get("taskFamily") or payload.get("task_family") or payload.get("summary"))[:220]
        initial_intent = _norm_text(payload.get("initialUserIntent") or payload.get("initial_user_intent") or "")
        triggers = _as_list(payload.get("canonicalTriggerPatterns") or payload.get("triggerPatterns") or payload.get("triggers"))
        signature = _norm_text(payload.get("taskFamilySignature") or payload.get("task_family_signature") or "")
        if not signature:
            signature = _signature_for(task_family, triggers, initial_intent)
        failure_markers = _as_list(payload.get("failureMarkers") or payload.get("failure_markers"))
        correction_points = _as_list(payload.get("userCorrectionPoints") or payload.get("user_correction_points"))
        final_success = _norm_text(payload.get("finalSuccessEvidence") or payload.get("final_success_evidence") or "")
        user_verdict = _norm_text(payload.get("userVerdict") or payload.get("user_verdict") or "")
        runtime_evidence = _as_list(payload.get("runtimeEvidence") or payload.get("runtime_evidence"))
        evidence_source = _norm_text(payload.get("evidenceSource") or payload.get("evidence_source") or extraction_source)
        has_runtime_evidence = bool(runtime_evidence) or evidence_source in {"runtime_events", "runtime_evidence", "workflow_ledger"}
        negative = any(
            token in f"{user_verdict} {final_success}".lower()
            for token in ("失败", "不对", "错误", "bad", "wrong", "failed", "negative")
        )
        has_success = bool(final_success) and not negative
        status = "success_after_corrections" if has_success and (failure_markers or correction_points) else "success" if has_success else "candidate"
        if negative:
            status = "negative_feedback"
        try:
            confidence = max(0.0, min(float(payload.get("confidence") or 0.55), 1.0))
        except (TypeError, ValueError):
            confidence = 0.55
        if not has_runtime_evidence:
            confidence = min(confidence, 0.45)
            # Transcript-only workflow claims are useful raw material, but they
            # must not count as proven successes for active hint promotion.
            if status.startswith("success"):
                status = "candidate"
        explicit_golden = _as_list(payload.get("goldenPathSteps") or payload.get("golden_path_steps"))
        side_effect_scope = _norm_text(payload.get("sideEffectScope") or payload.get("side_effect_scope"))
        tool_sequence = _as_list(payload.get("toolSkillSequence") or payload.get("toolsOrSkillsUsed") or payload.get("tool_skill_sequence"))
        risk_tier = _norm_text(payload.get("riskTier") or payload.get("risk_tier")) or _risk_tier_from_scope(side_effect_scope, tool_sequence)
        activation_allowed, approval_required, activation_reason = _activation_allowed_for_candidate(
            risk_tier=risk_tier,
            current="candidate",
        )
        return {
            "id": _norm_text(payload.get("id")) or f"mw_ep_{uuid.uuid4().hex}",
            "session_id": session_id,
            "run_id": run_id,
            "scope": scope or "global",
            "task_family": task_family or "reusable workflow",
            "task_family_signature": signature,
            "initial_user_intent": initial_intent,
            "first_action_signature": _norm_text(payload.get("firstActionSignature") or payload.get("first_action_signature")),
            "runtime_lane": _norm_text(payload.get("runtimeLane") or payload.get("runtime_lane")),
            "ordered_actions": _as_list(payload.get("orderedActions") or payload.get("ordered_actions")),
            "tool_skill_sequence": tool_sequence,
            "failure_markers": failure_markers,
            "user_correction_points": correction_points,
            "final_success_evidence": final_success,
            "user_verdict": user_verdict,
            "side_effect_scope": side_effect_scope,
            "privacy_scope": _norm_text(payload.get("privacyScope") or payload.get("privacy_scope")) or "local_runtime",
            "status": status,
            "confidence": confidence,
            "extraction_source": extraction_source,
            "metadata": {
                "canonicalTriggerPatterns": triggers,
                "goldenPathSteps": explicit_golden,
                "antiPatterns": _as_list(payload.get("antiPatterns") or payload.get("anti_patterns")),
                "verificationSteps": _as_list(payload.get("verificationSteps") or payload.get("verification_steps")),
                "runtimeEvidence": runtime_evidence,
                "evidenceSource": evidence_source,
                "hasRuntimeEvidence": has_runtime_evidence,
                "activationEligible": bool(activation_allowed and has_runtime_evidence),
                "activationReason": activation_reason,
                "approvalRequired": approval_required,
                "riskTier": risk_tier,
                "raw": to_jsonable(payload),
            },
        }

    def add_episode(self, episode: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now_iso()
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_workflow_episodes
                (id, session_id, run_id, scope, task_family, task_family_signature, initial_user_intent,
                 first_action_signature, runtime_lane, ordered_actions_json, tool_skill_sequence_json,
                 failure_markers_json, user_correction_points_json, final_success_evidence, user_verdict,
                 side_effect_scope, privacy_scope, status, confidence, extraction_source, metadata_json,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM memory_workflow_episodes WHERE id = ?), ?), ?)
                """,
                (
                    episode["id"],
                    episode.get("session_id"),
                    episode.get("run_id"),
                    episode.get("scope") or "global",
                    episode.get("task_family"),
                    episode.get("task_family_signature"),
                    episode.get("initial_user_intent"),
                    episode.get("first_action_signature"),
                    episode.get("runtime_lane"),
                    _json_dump(episode.get("ordered_actions")),
                    _json_dump(episode.get("tool_skill_sequence")),
                    _json_dump(episode.get("failure_markers")),
                    _json_dump(episode.get("user_correction_points")),
                    episode.get("final_success_evidence"),
                    episode.get("user_verdict"),
                    episode.get("side_effect_scope"),
                    episode.get("privacy_scope"),
                    episode.get("status"),
                    float(episode.get("confidence") or 0.5),
                    episode.get("extraction_source"),
                    _json_dump(episode.get("metadata") or {}),
                    episode["id"],
                    now,
                    now,
                ),
            )
            conn.commit()
        candidate = self.upsert_candidate_from_episode(episode)
        self.export_candidate(candidate)
        return {"episode": episode, "candidate": candidate}

    def ensure_default_workflow_candidates(self) -> Dict[str, Any]:
        """Ensure V8's built-in workflow memories exist as normal candidates.

        The seeds are not injected through a special prompt path. They are
        persisted in `memory_workflow_candidates` so they can be inspected,
        edited, quarantined, deleted, and scored exactly like learned workflow
        memories. If a user replaces a seed with a non-system candidate using
        the same signature, startup will not overwrite it.
        """

        now = utc_now_iso()
        created: List[str] = []
        updated: List[str] = []
        skipped: List[str] = []
        exported: List[str] = []
        definitions = _default_workflow_candidate_definitions()
        with db.get_connection() as conn:
            for raw in definitions:
                signature = str(raw.get("taskFamilySignature") or "").strip()
                if not signature:
                    continue
                row = conn.execute(
                    "SELECT * FROM memory_workflow_candidates WHERE task_family_signature = ?",
                    (signature,),
                ).fetchone()
                existing = _row_to_candidate(row) if row else None
                existing_metadata = existing.get("metadata") if isinstance((existing or {}).get("metadata"), dict) else {}
                existing_source = str(existing_metadata.get("source") or "").strip()
                if existing and existing_source and existing_source != "system_seed":
                    skipped.append(str(existing.get("id") or signature))
                    continue
                existing_version = str(existing_metadata.get("defaultSeedVersion") or "").strip()
                if existing and existing_version == DEFAULT_WORKFLOW_SEED_VERSION:
                    skipped.append(str(existing.get("id") or signature))
                    continue

                candidate_id = str(raw.get("id") or (existing or {}).get("id") or f"mw_seed_{uuid.uuid4().hex}").strip()
                metadata = {
                    **(raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}),
                    "source": "system_seed",
                    "defaultSeedVersion": DEFAULT_WORKFLOW_SEED_VERSION,
                    "seededAt": existing_metadata.get("seededAt") or now,
                    "updatedFromSeedAt": now,
                    "hasRuntimeEvidence": True,
                    "activationAllowed": True,
                    "activationReason": "system_default_workflow_memory",
                    "approvalRequired": False,
                    "riskTier": raw.get("riskTier") or "low",
                    "provenanceNote": "Built-in workflow memory. It uses the same candidate/hint/outcome pipeline as learned workflows.",
                }
                conn.execute(
                    """
                    INSERT INTO memory_workflow_candidates
                    (id, task_family_signature, task_family, scope, canonical_trigger_patterns_json,
                     first_action_triggers_json, golden_path_steps_json, anti_patterns_json,
                     verification_steps_json, success_count, correction_count, negative_feedback_count,
                     maturity_score, status, confidence, source_episode_ids_json, risk_tier, approval_required,
                     last_hint_outcome, guide_state_json, merge_suggestion_json, last_seen_at,
                     metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_family_signature) DO UPDATE SET
                        id = excluded.id,
                        task_family = excluded.task_family,
                        scope = excluded.scope,
                        canonical_trigger_patterns_json = excluded.canonical_trigger_patterns_json,
                        first_action_triggers_json = excluded.first_action_triggers_json,
                        golden_path_steps_json = excluded.golden_path_steps_json,
                        anti_patterns_json = excluded.anti_patterns_json,
                        verification_steps_json = excluded.verification_steps_json,
                        success_count = excluded.success_count,
                        correction_count = excluded.correction_count,
                        negative_feedback_count = excluded.negative_feedback_count,
                        maturity_score = excluded.maturity_score,
                        status = excluded.status,
                        confidence = excluded.confidence,
                        source_episode_ids_json = excluded.source_episode_ids_json,
                        risk_tier = excluded.risk_tier,
                        approval_required = excluded.approval_required,
                        last_seen_at = excluded.last_seen_at,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        candidate_id,
                        signature,
                        raw.get("taskFamily") or "V8OS default workflow memory",
                        raw.get("scope") or "global",
                        _json_dump(raw.get("canonicalTriggerPatterns") or []),
                        _json_dump(raw.get("firstActionTriggers") or []),
                        _json_dump(raw.get("goldenPathSteps") or []),
                        _json_dump(raw.get("antiPatterns") or []),
                        _json_dump(raw.get("verificationSteps") or []),
                        int(raw.get("successCount") or 2),
                        0,
                        0,
                        float(raw.get("maturityScore") or 0.75),
                        raw.get("status") or "approved",
                        float(raw.get("confidence") or 0.8),
                        _json_dump([f"system_seed:{candidate_id}:{DEFAULT_WORKFLOW_SEED_VERSION}"]),
                        raw.get("riskTier") or "low",
                        0,
                        (existing or {}).get("lastHintOutcome"),
                        _json_dump((existing or {}).get("guideState") or {}),
                        _json_dump((existing or {}).get("mergeSuggestion") or {}),
                        now,
                        _json_dump(metadata),
                        (existing or {}).get("created_at") or now,
                        now,
                    ),
                )
                if existing:
                    updated.append(candidate_id)
                else:
                    created.append(candidate_id)
        with db.get_connection() as conn:
            conn.commit()
        for candidate_id in [*created, *updated]:
            candidate = self.get_candidate(candidate_id) or {}
            if candidate:
                self.export_candidate(candidate)
                exported.append(candidate_id)
        return {
            "seedVersion": DEFAULT_WORKFLOW_SEED_VERSION,
            "seedCount": len(definitions),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "exported": exported,
        }

    def _candidate_status_for_counts(
        self,
        *,
        success_count: int,
        correction_count: int,
        negative_feedback_count: int,
        current: str = "candidate",
        risk_tier: str = "low",
        has_runtime_evidence: bool = True,
    ) -> str:
        cfg = workflow_memory_config()
        if negative_feedback_count > 0 and cfg.get("quarantineOnNegativeFeedback", True):
            return "quarantine"
        if current in {"approved", "promoted_skill_candidate"}:
            return current
        activation_allowed, approval_required, _reason = _activation_allowed_for_candidate(
            risk_tier=risk_tier,
            current=current,
        )
        if approval_required and _activation_policy_for_risk(risk_tier) == "quarantine":
            return "quarantine"
        if not has_runtime_evidence:
            return current if current in {"quarantine"} else "candidate"
        if success_count >= int(cfg.get("minSuccessCount") or 2):
            return "active_hint" if activation_allowed else "candidate"
        if correction_count > 0 and success_count > 0 and not cfg.get("errorfulSuccessRequiresUserAcceptance", True):
            return "active_hint" if activation_allowed else "candidate"
        return current if current in {"active_hint", "quarantine"} else "candidate"

    def upsert_candidate_from_episode(self, episode: Dict[str, Any]) -> Dict[str, Any]:
        signature = str(episode.get("task_family_signature") or "").strip()
        if not signature:
            signature = _signature_for(str(episode.get("task_family") or ""), [], str(episode.get("initial_user_intent") or ""))
        now = utc_now_iso()
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM memory_workflow_candidates WHERE task_family_signature = ?",
                (signature,),
            ).fetchone()
            existing = _row_to_candidate(row) if row else None
            metadata = dict(existing.get("metadata") or {}) if existing else {}
            episode_meta = episode.get("metadata") if isinstance(episode.get("metadata"), dict) else {}
            triggers = _uniq(
                (existing or {}).get("canonicalTriggerPatterns", [])
                + _as_list(episode_meta.get("canonicalTriggerPatterns"))
                + [episode.get("initial_user_intent"), episode.get("task_family")],
                limit=16,
            )
            first_actions = _uniq(
                (existing or {}).get("firstActionTriggers", []) + [episode.get("first_action_signature")],
                limit=10,
            )
            has_runtime_evidence = bool(episode_meta.get("hasRuntimeEvidence")) or bool(episode_meta.get("runtimeEvidence"))
            golden = _uniq(
                (existing or {}).get("goldenPathSteps", [])
                + (_as_list(episode_meta.get("goldenPathSteps")) if has_runtime_evidence or episode_meta.get("activationEligible") else []),
                limit=12,
            )
            anti = _uniq(
                (existing or {}).get("antiPatterns", [])
                + _as_list(episode_meta.get("antiPatterns"))
                + _as_list(episode.get("failure_markers")),
                limit=12,
            )
            verification = _uniq(
                (existing or {}).get("verificationSteps", [])
                + _as_list(episode_meta.get("verificationSteps"))
                + [episode.get("final_success_evidence")],
                limit=10,
            )
            source_ids = _uniq((existing or {}).get("sourceEpisodeIds", []) + [episode.get("id")], limit=100)
            status = str((existing or {}).get("status") or "candidate")
            success_delta = 1 if str(episode.get("status") or "").startswith("success") else 0
            correction_delta = 1 if str(episode.get("status") or "") == "success_after_corrections" else 0
            negative_delta = 1 if str(episode.get("status") or "") == "negative_feedback" else 0
            success_count = int((existing or {}).get("success_count") or 0) + success_delta
            correction_count = int((existing or {}).get("correction_count") or 0) + correction_delta
            negative_count = int((existing or {}).get("negative_feedback_count") or 0) + negative_delta
            risk_tier = str(episode_meta.get("riskTier") or (existing or {}).get("riskTier") or (existing or {}).get("risk_tier") or "low")
            activation_allowed, approval_required, activation_reason = _activation_allowed_for_candidate(
                risk_tier=risk_tier,
                current=status,
            )
            status = self._candidate_status_for_counts(
                success_count=success_count,
                correction_count=correction_count,
                negative_feedback_count=negative_count,
                current=status,
                risk_tier=risk_tier,
                has_runtime_evidence=has_runtime_evidence or bool((existing or {}).get("metadata", {}).get("hasRuntimeEvidence")),
            )
            maturity_score = min(1.0, (success_count / max(1, int(workflow_memory_config().get("minSuccessCount") or 2))) * 0.7 + correction_count * 0.1 - negative_count * 0.4)
            confidence = max(float((existing or {}).get("confidence") or 0.0), float(episode.get("confidence") or 0.5))
            candidate_id = (existing or {}).get("id") or f"mw_cand_{uuid.uuid4().hex}"
            metadata.update(
                {
                    "lastEpisodeStatus": episode.get("status"),
                    "sideEffectScope": episode.get("side_effect_scope"),
                    "privacyScope": episode.get("privacy_scope"),
                    "riskTier": risk_tier,
                    "activationAllowed": activation_allowed,
                    "activationReason": activation_reason,
                    "approvalRequired": approval_required,
                    "hasRuntimeEvidence": bool(has_runtime_evidence or metadata.get("hasRuntimeEvidence")),
                    "latestRuntimeEvidence": episode_meta.get("runtimeEvidence") or [],
                }
            )
            conn.execute(
                """
                INSERT INTO memory_workflow_candidates
                (id, task_family_signature, task_family, scope, canonical_trigger_patterns_json,
                 first_action_triggers_json, golden_path_steps_json, anti_patterns_json,
                 verification_steps_json, success_count, correction_count, negative_feedback_count,
                 maturity_score, status, confidence, source_episode_ids_json, risk_tier, approval_required,
                 last_hint_outcome, guide_state_json, merge_suggestion_json, last_seen_at,
                 metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_family_signature) DO UPDATE SET
                    task_family = excluded.task_family,
                    scope = excluded.scope,
                    canonical_trigger_patterns_json = excluded.canonical_trigger_patterns_json,
                    first_action_triggers_json = excluded.first_action_triggers_json,
                    golden_path_steps_json = excluded.golden_path_steps_json,
                    anti_patterns_json = excluded.anti_patterns_json,
                    verification_steps_json = excluded.verification_steps_json,
                    success_count = excluded.success_count,
                    correction_count = excluded.correction_count,
                    negative_feedback_count = excluded.negative_feedback_count,
                    maturity_score = excluded.maturity_score,
                    status = excluded.status,
                    confidence = excluded.confidence,
                    source_episode_ids_json = excluded.source_episode_ids_json,
                    risk_tier = excluded.risk_tier,
                    approval_required = excluded.approval_required,
                    last_hint_outcome = excluded.last_hint_outcome,
                    guide_state_json = excluded.guide_state_json,
                    merge_suggestion_json = excluded.merge_suggestion_json,
                    last_seen_at = excluded.last_seen_at,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    candidate_id,
                    signature,
                    episode.get("task_family") or (existing or {}).get("task_family") or "reusable workflow",
                    episode.get("scope") or (existing or {}).get("scope") or "global",
                    _json_dump(triggers),
                    _json_dump(first_actions),
                    _json_dump(golden),
                    _json_dump(anti),
                    _json_dump(verification),
                    success_count,
                    correction_count,
                    negative_count,
                    maturity_score,
                    status,
                    confidence,
                    _json_dump(source_ids),
                    risk_tier,
                    1 if approval_required else 0,
                    (existing or {}).get("last_hint_outcome"),
                    _json_dump((existing or {}).get("guideState") or (existing or {}).get("guide_state") or {}),
                    _json_dump((existing or {}).get("mergeSuggestion") or {}),
                    now,
                    _json_dump(metadata),
                    (existing or {}).get("created_at") or now,
                    now,
                ),
            )
            conn.commit()
        return self.get_candidate(candidate_id) or {}

    def list_candidates(self, *, status: Optional[str] = None, query: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM memory_workflow_candidates WHERE 1=1"
        params: List[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 50), 200)))
        with db.get_connection() as conn:
            items = [_row_to_candidate(row) for row in conn.execute(sql, params).fetchall()]
        if query:
            q = _token_set(query)
            items = [
                item for item in items
                if q & _token_set(" ".join([
                    str(item.get("task_family") or ""),
                    " ".join(map(str, item.get("canonicalTriggerPatterns") or [])),
                    " ".join(map(str, item.get("goldenPathSteps") or [])),
                ]))
            ]
        return items

    def get_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM memory_workflow_candidates WHERE id = ?", (candidate_id,)).fetchone()
            return _row_to_candidate(row) if row else None

    def update_candidate(self, candidate_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        current = self.get_candidate(candidate_id)
        if not current:
            raise ValueError(f"workflow candidate not found: {candidate_id}")
        editable = {
            "task_family": updates.get("taskFamily", updates.get("task_family", current.get("task_family"))),
            "scope": updates.get("scope", current.get("scope")),
            "status": updates.get("status", current.get("status")),
            "canonicalTriggerPatterns": updates.get("canonicalTriggerPatterns", current.get("canonicalTriggerPatterns") or []),
            "firstActionTriggers": updates.get("firstActionTriggers", current.get("firstActionTriggers") or []),
            "goldenPathSteps": updates.get("goldenPathSteps", current.get("goldenPathSteps") or []),
            "antiPatterns": updates.get("antiPatterns", current.get("antiPatterns") or []),
            "verificationSteps": updates.get("verificationSteps", current.get("verificationSteps") or []),
            "sourceEpisodeIds": updates.get("sourceEpisodeIds", updates.get("source_episode_ids", current.get("sourceEpisodeIds") or [])),
            "success_count": updates.get("successCount", updates.get("success_count", current.get("success_count") or 0)),
            "correction_count": updates.get("correctionCount", updates.get("correction_count", current.get("correction_count") or 0)),
            "negative_feedback_count": updates.get("negativeFeedbackCount", updates.get("negative_feedback_count", current.get("negative_feedback_count") or 0)),
            "maturity_score": updates.get("maturityScore", updates.get("maturity_score", current.get("maturity_score") or 0)),
            "confidence": updates.get("confidence", current.get("confidence") or 0.5),
            "risk_tier": updates.get("riskTier", updates.get("risk_tier", current.get("riskTier") or current.get("risk_tier") or "low")),
            "approval_required": updates.get("approvalRequired", updates.get("approval_required", current.get("approvalRequired") or current.get("approval_required") or False)),
            "last_hint_outcome": updates.get("lastHintOutcome", updates.get("last_hint_outcome", current.get("lastHintOutcome") or current.get("last_hint_outcome"))),
            "guideState": updates.get("guideState", current.get("guideState") or {}),
            "mergeSuggestion": updates.get("mergeSuggestion", current.get("mergeSuggestion") or {}),
            "metadata": updates.get("metadata", current.get("metadata") or {}),
        }
        now = utc_now_iso()
        with db.get_connection() as conn:
            conn.execute(
                """
                UPDATE memory_workflow_candidates
                SET task_family = ?, scope = ?, canonical_trigger_patterns_json = ?,
                    first_action_triggers_json = ?, golden_path_steps_json = ?,
                    anti_patterns_json = ?, verification_steps_json = ?, source_episode_ids_json = ?,
                    success_count = ?, correction_count = ?, negative_feedback_count = ?, maturity_score = ?,
                    status = ?, confidence = ?, risk_tier = ?, approval_required = ?, last_hint_outcome = ?,
                    guide_state_json = ?, merge_suggestion_json = ?, metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    editable["task_family"],
                    editable["scope"],
                    _json_dump(editable["canonicalTriggerPatterns"]),
                    _json_dump(editable["firstActionTriggers"]),
                    _json_dump(editable["goldenPathSteps"]),
                    _json_dump(editable["antiPatterns"]),
                    _json_dump(editable["verificationSteps"]),
                    _json_dump(editable["sourceEpisodeIds"]),
                    int(editable["success_count"] or 0),
                    int(editable["correction_count"] or 0),
                    int(editable["negative_feedback_count"] or 0),
                    float(editable["maturity_score"] or 0),
                    editable["status"],
                    float(editable["confidence"] or 0.5),
                    editable["risk_tier"],
                    1 if editable["approval_required"] else 0,
                    editable["last_hint_outcome"],
                    _json_dump(editable["guideState"]),
                    _json_dump(editable["mergeSuggestion"]),
                    _json_dump(editable["metadata"]),
                    now,
                    candidate_id,
                ),
            )
            conn.commit()
        candidate = self.get_candidate(candidate_id) or {}
        self.export_candidate(candidate)
        return candidate

    def delete_candidate(self, candidate_id: str) -> bool:
        with db.get_connection() as conn:
            cursor = conn.execute("DELETE FROM memory_workflow_candidates WHERE id = ?", (candidate_id,))
            conn.commit()
            return cursor.rowcount > 0

    def merge_candidates(self, target_id: str, source_ids: List[str]) -> Dict[str, Any]:
        target = self.get_candidate(target_id)
        if not target:
            raise ValueError(f"target workflow candidate not found: {target_id}")
        for source_id in source_ids:
            if source_id == target_id:
                continue
            source = self.get_candidate(source_id)
            if not source:
                continue
            target["canonicalTriggerPatterns"] = _uniq(target.get("canonicalTriggerPatterns", []) + source.get("canonicalTriggerPatterns", []), limit=24)
            target["firstActionTriggers"] = _uniq(target.get("firstActionTriggers", []) + source.get("firstActionTriggers", []), limit=16)
            target["goldenPathSteps"] = _uniq(target.get("goldenPathSteps", []) + source.get("goldenPathSteps", []), limit=18)
            target["antiPatterns"] = _uniq(target.get("antiPatterns", []) + source.get("antiPatterns", []), limit=18)
            target["verificationSteps"] = _uniq(target.get("verificationSteps", []) + source.get("verificationSteps", []), limit=14)
            target["sourceEpisodeIds"] = _uniq(target.get("sourceEpisodeIds", []) + source.get("sourceEpisodeIds", []), limit=200)
            target["success_count"] = int(target.get("success_count") or 0) + int(source.get("success_count") or 0)
            target["correction_count"] = int(target.get("correction_count") or 0) + int(source.get("correction_count") or 0)
            target["negative_feedback_count"] = int(target.get("negative_feedback_count") or 0) + int(source.get("negative_feedback_count") or 0)
            self.delete_candidate(source_id)
        return self.update_candidate(target_id, target)

    def list_episodes(self, *, candidate_id: Optional[str] = None, session_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        params: List[Any] = []
        sql = "SELECT * FROM memory_workflow_episodes WHERE 1=1"
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if candidate_id:
            candidate = self.get_candidate(candidate_id)
            ids = candidate.get("sourceEpisodeIds", []) if candidate else []
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            sql += f" AND id IN ({placeholders})"
            params.extend(ids)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 50), 200)))
        with db.get_connection() as conn:
            return [_row_to_episode(row) for row in conn.execute(sql, params).fetchall()]

    def record_hint_event(self, *, candidate_id: str, query: str, hint: Dict[str, Any], session_id: Optional[str] = None, run_id: Optional[str] = None, outcome: str = "injected", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = utc_now_iso()
        event_id = f"mw_hint_{uuid.uuid4().hex}"
        normalized_outcome = str(outcome or "injected").strip().lower() or "injected"
        current_candidate = self.get_candidate(candidate_id) if candidate_id else None
        candidate_metadata = dict(current_candidate.get("metadata") or {}) if current_candidate else {}
        outcome_counts = dict(candidate_metadata.get("hintOutcomeCounts") or {})
        outcome_counts[normalized_outcome] = int(outcome_counts.get(normalized_outcome) or 0) + 1
        candidate_metadata["hintOutcomeCounts"] = outcome_counts
        candidate_metadata["lastHintOutcomeAt"] = now
        if normalized_outcome == "contradicted":
            candidate_metadata["hintSuppressedReason"] = "last_outcome_contradicted"
        elif candidate_metadata.get("hintSuppressedReason") == "last_outcome_contradicted" and normalized_outcome in {"helped_success", "accepted"}:
            candidate_metadata.pop("hintSuppressedReason", None)
        next_status = str((current_candidate or {}).get("status") or "")
        if normalized_outcome == "caused_failure":
            next_status = "quarantine"
            candidate_metadata["hintSuppressedReason"] = "caused_failure"
        maturity_score = float((current_candidate or {}).get("maturity_score") or 0.0)
        negative_feedback_count = int((current_candidate or {}).get("negative_feedback_count") or 0)
        if normalized_outcome in {"helped_success", "accepted"}:
            maturity_score = min(1.0, maturity_score + 0.08)
        elif normalized_outcome == "ignored":
            maturity_score = max(0.0, maturity_score - 0.05)
        elif normalized_outcome == "contradicted":
            maturity_score = max(0.0, maturity_score - 0.15)
            negative_feedback_count += 1
        elif normalized_outcome == "caused_failure":
            maturity_score = 0.0
            negative_feedback_count += 1
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO memory_workflow_hint_events
                (id, candidate_id, session_id, run_id, query, injected_hint_json, outcome, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, candidate_id, session_id, run_id, query, _json_dump(hint), normalized_outcome, _json_dump(metadata or {}), now, now),
            )
            if candidate_id:
                conn.execute(
                    """
                    UPDATE memory_workflow_candidates
                    SET last_hint_outcome = ?, metadata_json = ?, status = COALESCE(NULLIF(?, ''), status),
                        maturity_score = ?, negative_feedback_count = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_outcome,
                        _json_dump(candidate_metadata),
                        next_status,
                        maturity_score,
                        negative_feedback_count,
                        now,
                        candidate_id,
                    ),
                )
            conn.commit()
        if candidate_id and normalized_outcome in NEGATIVE_HINT_OUTCOMES and (session_id or run_id):
            terminal_state = "failed" if normalized_outcome == "caused_failure" else normalized_outcome
            try:
                self.record_guide_state(
                    candidate_id=candidate_id,
                    query=query,
                    session_id=session_id,
                    run_id=run_id,
                    state=terminal_state,
                    current_step_index=int((hint or {}).get("currentStepIndex") or 0),
                    outcome=normalized_outcome,
                    metadata={
                        "terminalFromHintOutcome": True,
                        "hintEventId": event_id,
                    },
                )
            except Exception:
                pass
        if current_candidate:
            try:
                self.export_candidate(self.get_candidate(candidate_id) or {})
            except Exception:
                pass
        return {"id": event_id, "candidateId": candidate_id, "outcome": normalized_outcome}

    def list_hint_events(self, *, candidate_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        params: List[Any] = []
        sql = "SELECT * FROM memory_workflow_hint_events WHERE 1=1"
        if candidate_id:
            sql += " AND candidate_id = ?"
            params.append(candidate_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 50), 200)))
        with db.get_connection() as conn:
            return [_row_to_hint_event(row) for row in conn.execute(sql, params).fetchall()]

    def _latest_guide_state(
        self,
        *,
        candidate_id: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not candidate_id:
            return None
        params: List[Any] = [candidate_id]
        sql = "SELECT * FROM memory_workflow_guide_states WHERE candidate_id = ?"
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += " ORDER BY updated_at DESC LIMIT 1"
        with db.get_connection() as conn:
            row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        data = dict(row)
        data["metadata"] = _json_load(data.pop("metadata_json", None), {})
        data["currentStepIndex"] = int(data.get("current_step_index") or 0)
        return data

    def _terminal_guide_state_for_query(
        self,
        *,
        query: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not session_id and not run_id:
            return None
        terminal_states = sorted(TERMINAL_GUIDE_STATES)
        params: List[Any] = list(terminal_states)
        sql = "SELECT * FROM memory_workflow_guide_states WHERE state IN ({})".format(
            ",".join("?" for _ in terminal_states)
        )
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += " ORDER BY updated_at DESC LIMIT 12"
        query_tokens = _meaningful_tokens(query)
        with db.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        for row in rows:
            data = dict(row)
            state_query = str(data.get("query") or "")
            if not state_query:
                continue
            state_tokens = _meaningful_tokens(state_query)
            if (
                query_tokens & state_tokens
                or _normalized_contains(query, state_query)
                or _normalized_contains(state_query, query)
            ):
                data["metadata"] = _json_load(data.pop("metadata_json", None), {})
                data["currentStepIndex"] = int(data.get("current_step_index") or 0)
                return data
        return None

    def _recent_runtime_events(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        limit: int = 80,
    ) -> List[Dict[str, Any]]:
        if not session_id and not run_id:
            return []
        try:
            if run_id:
                return db.get_runtime_events_for_run(run_id, session_id=session_id, limit=limit)
            if session_id:
                return db.get_runtime_events(session_id)[-limit:]
        except Exception:
            return []
        return []

    def _planner_context_from_runtime_events(
        self,
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        events: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        recent_events = list(events or self._recent_runtime_events(session_id=session_id, run_id=run_id))
        if not recent_events:
            return {
                "plannerAware": False,
                "deliveryMode": "direct_guide",
                "plannerPlanId": None,
                "plannerTaskRef": None,
            }

        planner_plan_id: Optional[str] = None
        planner_task_ref: Optional[str] = None
        for event in reversed(recent_events):
            topic = str(event.get("topic") or "").strip().lower()
            source = event.get("source") if isinstance(event.get("source"), dict) else {}
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            runtime_id = str(event.get("runtime_id") or source.get("runtimeId") or payload.get("runtimeId") or "").strip().lower()
            if runtime_id == "planner_lane" or topic.startswith("planner.") or topic.startswith("chat.planner_"):
                planner_plan_id = (
                    str(payload.get("planId") or payload.get("plannerPlanId") or "").strip()
                    or str((payload.get("traceRef") or {}).get("planId") or "").strip()
                    or planner_plan_id
                )
                task_briefs = list(payload.get("taskBriefs") or [])
                if task_briefs:
                    first_brief = task_briefs[0] if isinstance(task_briefs[0], dict) else {}
                    planner_task_ref = str(first_brief.get("taskBriefId") or "").strip() or planner_task_ref
                dependencies = list(payload.get("dependencies") or [])
                if not planner_task_ref and dependencies:
                    first_dep = dependencies[0] if isinstance(dependencies[0], dict) else {}
                    planner_task_ref = str(first_dep.get("taskBriefId") or "").strip() or planner_task_ref
                if topic == "planner.plan.projected":
                    break

        planner_aware = bool(planner_plan_id or planner_task_ref)
        return {
            "plannerAware": planner_aware,
            "deliveryMode": "planner_checklist_bias" if planner_aware else "direct_guide",
            "plannerPlanId": planner_plan_id or None,
            "plannerTaskRef": planner_task_ref or None,
        }

    def _infer_step_index_from_runtime_events(
        self,
        *,
        item: Dict[str, Any],
        session_id: Optional[str],
        run_id: Optional[str],
        events: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        recent_events = list(events or self._recent_runtime_events(session_id=session_id, run_id=run_id))
        if not recent_events:
            return 0
        event_chunks: List[str] = []
        for event in recent_events:
            event_chunks.extend(
                [
                    str(event.get("topic") or ""),
                    str(event.get("type") or ""),
                    str(event.get("payload") or ""),
                    str(event.get("source") or ""),
                ]
            )
        event_text = " ".join(event_chunks).lower()
        steps = list(item.get("goldenPathSteps") or [])
        if not steps:
            return 0
        first_actions = " ".join(map(str, item.get("firstActionTriggers") or [])).lower()
        if first_actions and any(token in event_text for token in _meaningful_tokens(first_actions)):
            return min(1, len(steps) - 1)
        for index, step in enumerate(steps):
            tokens = _meaningful_tokens(str(step))
            if tokens and len(tokens & _token_set(event_text)) >= 2:
                return min(index + 1, len(steps) - 1)
        return 0

    @staticmethod
    def _outcome_score_delta(item: Dict[str, Any]) -> Tuple[float, Optional[str]]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        counts = metadata.get("hintOutcomeCounts") if isinstance(metadata.get("hintOutcomeCounts"), dict) else {}
        last = str(item.get("lastHintOutcome") or item.get("last_hint_outcome") or "").strip().lower()
        if last == "caused_failure":
            return 0.0, "caused_failure"
        if last == "contradicted":
            return 0.0, "contradicted"
        delta = 0.0
        try:
            delta += float(counts.get("helped_success") or 0) * 0.75
            delta -= float(counts.get("ignored") or 0) * 0.2
            delta -= float(counts.get("contradicted") or 0) * 0.6
        except (TypeError, ValueError):
            delta = 0.0
        if last == "helped_success":
            delta += 0.5
        if last == "ignored":
            delta -= 0.25
        return delta, None

    def record_guide_state(
        self,
        *,
        candidate_id: str,
        query: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        state: str = "matched",
        current_step_index: int = 0,
        last_event_topic: Optional[str] = None,
        outcome: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        state_id = f"mw_guide_{uuid.uuid4().hex}"
        guide_state = {
            "id": state_id,
            "candidateId": candidate_id,
            "state": state,
            "currentStepIndex": current_step_index,
            "lastEventTopic": last_event_topic,
            "outcome": outcome,
            "updatedAt": now,
            "metadata": metadata or {},
        }
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO memory_workflow_guide_states
                (id, candidate_id, session_id, run_id, query, state, current_step_index,
                 last_event_topic, outcome, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state_id,
                    candidate_id,
                    session_id,
                    run_id,
                    query,
                    state,
                    current_step_index,
                    last_event_topic,
                    outcome,
                    _json_dump(metadata or {}),
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE memory_workflow_candidates SET guide_state_json = ?, updated_at = ? WHERE id = ?",
                (_json_dump(guide_state), now, candidate_id),
            )
            conn.commit()
        return guide_state

    def dashboard_summary(self) -> Dict[str, Any]:
        cfg = workflow_memory_config()
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM memory_workflow_candidates
                GROUP BY status
                """
            ).fetchall()
            recent = [
                _row_to_candidate(row)
                for row in conn.execute(
                    "SELECT * FROM memory_workflow_candidates ORDER BY updated_at DESC LIMIT 8"
                ).fetchall()
            ]
            episode_count = conn.execute("SELECT COUNT(*) AS count FROM memory_workflow_episodes").fetchone()
            hint_count = conn.execute("SELECT COUNT(*) AS count FROM memory_workflow_hint_events").fetchone()
        by_status = {str(row["status"] or "candidate"): int(row["count"] or 0) for row in rows}
        episode_total = int(episode_count["count"] or 0) if episode_count else 0
        hint_total = int(hint_count["count"] or 0) if hint_count else 0
        return {
            "enabled": bool(cfg.get("enabled")),
            "hintInjectionEnabled": bool(cfg.get("hintInjectionEnabled")),
            "candidateCount": sum(by_status.values()),
            "episodeCount": episode_total,
            "hintEventCount": hint_total,
            "byStatus": by_status,
            "recent": [
                {
                    "id": item.get("id"),
                    "taskFamily": item.get("task_family"),
                    "status": item.get("status"),
                    "maturityScore": item.get("maturity_score"),
                    "successCount": item.get("success_count"),
                    "correctionCount": item.get("correction_count"),
                    "negativeFeedbackCount": item.get("negative_feedback_count"),
                    "updatedAt": item.get("updated_at"),
                }
                for item in recent
            ],
        }

    def match_hints(self, *, query: str, scope_chain: Optional[List[str]] = None, limit: int = 2) -> List[Dict[str, Any]]:
        cfg = workflow_memory_config()
        if not cfg.get("enabled") or not cfg.get("hintInjectionEnabled"):
            return []
        q_tokens = _token_set(query)
        if not q_tokens:
            return []
        scope_set = set(scope_chain or ["global"])
        candidates = self.list_candidates(limit=120)
        scored: List[tuple[float, Dict[str, Any]]] = []
        for item in candidates:
            if str(item.get("status") or "") not in ACTIVE_WORKFLOW_STATUSES:
                continue
            scope = str(item.get("scope") or "global")
            if scope != "global" and scope not in scope_set:
                continue
            haystack = " ".join(
                [
                    str(item.get("task_family") or ""),
                    " ".join(map(str, item.get("canonicalTriggerPatterns") or [])),
                    " ".join(map(str, item.get("firstActionTriggers") or [])),
                    " ".join(map(str, item.get("goldenPathSteps") or [])),
                ]
            )
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            negative_patterns = _as_list(metadata.get("negativeMatchPatterns") or metadata.get("negative_match_patterns"))
            if any(_normalized_contains(query, pattern) for pattern in negative_patterns):
                continue
            anchored, reasons = _intent_anchor_match(item, query, q_tokens)
            if not anchored:
                continue
            outcome_delta, suppress_reason = self._outcome_score_delta(item)
            if suppress_reason:
                continue
            overlap = len(q_tokens & _meaningful_tokens(haystack))
            if overlap <= 0:
                continue
            generic_overlap = len((q_tokens & _token_set(haystack)) - _meaningful_tokens(haystack))
            score = (
                overlap * 1.5
                + generic_overlap * 0.15
                + float(item.get("maturity_score") or 0) * 2
                + float(item.get("confidence") or 0)
                + outcome_delta
            )
            if metadata.get("source") == "system_seed":
                # Built-in memories are baseline guidance. Learned or
                # user-approved candidates with the same anchors should be
                # allowed to outrank them.
                score -= 0.6
            enriched = dict(item)
            enriched["_workflowHintDiagnostics"] = {
                "score": round(score, 4),
                "overlap": overlap,
                "genericOverlap": generic_overlap,
                "matchedReasons": reasons,
                "outcomeDelta": round(outcome_delta, 4),
            }
            scored.append((score, enriched))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[: max(0, min(limit, int(cfg.get("maxInjectedHints") or 2)))]]

    def build_hints_block(
        self,
        *,
        query: str,
        scope_chain: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> str:
        cfg = workflow_memory_config()
        max_guides = int(cfg.get("maxActiveWorkflowGuidesPerRun") or cfg.get("maxInjectedHints") or 2)
        if self._terminal_guide_state_for_query(query=query, session_id=session_id, run_id=run_id):
            return ""
        match_limit = min(int(cfg.get("maxInjectedHints") or 2), max_guides)
        if session_id or run_id:
            # Fetch a slightly wider pool so an in-progress guide state can
            # outrank broad system-seeded baseline memories before we enforce
            # the per-run active guide cap.
            match_limit = max(match_limit, int(cfg.get("maxInjectedHints") or 2))
        hints = self.match_hints(
            query=query,
            scope_chain=scope_chain,
            limit=match_limit,
        )
        if not hints:
            return ""
        max_chars = int(cfg.get("maxHintChars") or 900)
        try:
            ranked_path_count = int(storage.get_engineering_lane_config().get("rankedWorkflowPathCount") or 3)
        except Exception:
            ranked_path_count = 3
        ranked_path_count = max(1, min(ranked_path_count, 5))
        lines = [
            "[WORKFLOW HINTS]",
            "Procedural memory guidance. Use this as a small route aid, not a script.",
        ]
        emitted = 0
        if session_id or run_id:
            def _hint_priority(item: Dict[str, Any]) -> Tuple[int, float]:
                guide_state = self._latest_guide_state(
                    candidate_id=str(item.get("id") or ""),
                    session_id=session_id,
                    run_id=run_id,
                )
                state = str((guide_state or {}).get("state") or "").strip().lower()
                diagnostics = item.get("_workflowHintDiagnostics") if isinstance(item.get("_workflowHintDiagnostics"), dict) else {}
                try:
                    score = float(diagnostics.get("score") or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
                return (1 if guide_state and state not in TERMINAL_GUIDE_STATES else 0, score)

            hints.sort(key=_hint_priority, reverse=True)
        for item in hints:
            if emitted >= max_guides:
                break
            golden = item.get("goldenPathSteps") or []
            anti = item.get("antiPatterns") or []
            verify = item.get("verificationSteps") or []
            recent_events = self._recent_runtime_events(session_id=session_id, run_id=run_id)
            planner_context = self._planner_context_from_runtime_events(
                session_id=session_id,
                run_id=run_id,
                events=recent_events,
            )
            guide_state = self._latest_guide_state(
                candidate_id=str(item.get("id") or ""),
                session_id=session_id,
                run_id=run_id,
            )
            if guide_state and str(guide_state.get("state") or "").strip().lower() in TERMINAL_GUIDE_STATES:
                continue
            if guide_state:
                step_index = int(guide_state.get("currentStepIndex") or 0)
            else:
                step_index = self._infer_step_index_from_runtime_events(
                    item=item,
                    session_id=session_id,
                    run_id=run_id,
                    events=recent_events,
                )
            if golden:
                step_index = max(0, min(step_index, len(golden) - 1))
            else:
                step_index = 0
            next_step = str(golden[step_index])[:220] if golden else ""
            diagnostics = item.get("_workflowHintDiagnostics") if isinstance(item.get("_workflowHintDiagnostics"), dict) else {}
            ranked_next_actions = self._ranked_next_actions_for_item(
                item=item,
                start_index=step_index,
                max_paths=ranked_path_count,
            )
            hint_payload = {
                "taskFamily": item.get("task_family"),
                "nextStep": next_step,
                "rankedNextActions": ranked_next_actions,
                "avoid": anti[:2],
                "verify": verify[:2],
                "confidence": item.get("confidence"),
                "riskTier": item.get("riskTier") or item.get("risk_tier") or "low",
                "currentStepIndex": step_index,
                "matchedReason": diagnostics.get("matchedReasons") or [],
                "score": diagnostics.get("score"),
                "adaptation": (
                    "Planner exists for this run; use the workflow as a checklist / route bias only."
                    if planner_context.get("plannerAware")
                    else "Keep the goal and verification intent, but adjust concrete actions if this task differs from the learned workflow."
                ),
                "deliveryMode": planner_context.get("deliveryMode"),
                "plannerAware": bool(planner_context.get("plannerAware")),
                "plannerPlanId": planner_context.get("plannerPlanId"),
                "plannerTaskRef": planner_context.get("plannerTaskRef"),
            }
            lines.append(f"- Workflow: {item.get('task_family') or item.get('id')} (confidence {float(item.get('confidence') or 0):.2f})")
            if diagnostics.get("matchedReasons"):
                lines.append(f"  Applies because: {'; '.join(str(reason)[:80] for reason in diagnostics.get('matchedReasons')[:3])}")
            if planner_context.get("plannerAware"):
                planner_bits = []
                if planner_context.get("plannerPlanId"):
                    planner_bits.append(f"plan={planner_context.get('plannerPlanId')}")
                if planner_context.get("plannerTaskRef"):
                    planner_bits.append(f"task={planner_context.get('plannerTaskRef')}")
                planner_suffix = f" ({', '.join(planner_bits)})" if planner_bits else ""
                lines.append(f"  Delivery mode: checklist / bias{planner_suffix}")
            if golden:
                if planner_context.get("plannerAware"):
                    lines.append(f"  Checklist focus (Step {step_index + 1}/{len(golden)}): {next_step}")
                    lines.append("  Adaptation: Respect the current planner/task brief; use this only to bias route choice and verification.")
                else:
                    lines.append(f"  Suggested next move (Step {step_index + 1}/{len(golden)}): {next_step}")
                    lines.append("  Adaptation: Keep the goal and verification intent; change concrete actions when the current task differs.")
                if ranked_next_actions:
                    lines.append("  Ranked next action paths:")
                    for action in ranked_next_actions[:ranked_path_count]:
                        score = action.get("behaviorMatch")
                        score_text = f"{float(score):.2f}" if isinstance(score, (int, float)) else str(score or "n/a")
                        lines.append(f"    #{action.get('rank')}: match={score_text} · {str(action.get('suggestedAction') or '')[:160]}")
                        variants = action.get("reasonableVariants") if isinstance(action.get("reasonableVariants"), list) else []
                        if variants:
                            lines.append(f"      variants: {'; '.join(str(value)[:80] for value in variants[:2])}")
            if anti:
                lines.append(f"  Avoid: {'; '.join(str(step)[:120] for step in anti[:2])}")
            if verify:
                lines.append(f"  Verify: {'; '.join(str(step)[:120] for step in verify[:2])}")
            try:
                new_guide_state = self.record_guide_state(
                    candidate_id=str(item.get("id") or ""),
                    query=query,
                    session_id=session_id,
                    run_id=run_id,
                    state=f"step_{step_index}_pending",
                    current_step_index=step_index,
                    metadata={
                        "scopeChain": scope_chain or [],
                        "riskTier": hint_payload["riskTier"],
                        "matchedReasons": diagnostics.get("matchedReasons") or [],
                        "score": diagnostics.get("score"),
                        "deliveryMode": hint_payload["deliveryMode"],
                        "plannerAware": hint_payload["plannerAware"],
                        "plannerPlanId": hint_payload["plannerPlanId"],
                        "plannerTaskRef": hint_payload["plannerTaskRef"],
                    },
                )
                self.record_hint_event(
                    candidate_id=str(item.get("id") or ""),
                    query=query,
                    hint=hint_payload,
                    outcome="injected",
                    session_id=session_id,
                    run_id=run_id,
                    metadata={
                        "scopeChain": scope_chain or [],
                        "guideStateId": new_guide_state.get("id"),
                        "deliveryMode": hint_payload["deliveryMode"],
                        "plannerAware": hint_payload["plannerAware"],
                        "plannerPlanId": hint_payload["plannerPlanId"],
                        "plannerTaskRef": hint_payload["plannerTaskRef"],
                    },
                )
            except Exception:
                pass
            emitted += 1
        if emitted <= 0:
            return ""
        lines.append("[/WORKFLOW HINTS]")
        text = "\n".join(lines)
        return text[:max_chars].rstrip()

    def _ranked_next_actions_for_item(
        self,
        *,
        item: Dict[str, Any],
        start_index: int,
        max_paths: int,
    ) -> List[Dict[str, Any]]:
        golden = list(item.get("goldenPathSteps") or [])
        if not golden:
            return []
        diagnostics = item.get("_workflowHintDiagnostics") if isinstance(item.get("_workflowHintDiagnostics"), dict) else {}
        base_score = float(diagnostics.get("score") or item.get("confidence") or 0.0)
        anti = list(item.get("antiPatterns") or [])
        verify = list(item.get("verificationSteps") or [])
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        configured_variants = metadata.get("actionVariants") or metadata.get("action_variants") or []
        variants_by_step = metadata.get("actionVariantsByStep") or metadata.get("action_variants_by_step") or {}
        if isinstance(variants_by_step, dict):
            configured_variants = (
                variants_by_step.get(str(start_index))
                or variants_by_step.get(start_index)
                or configured_variants
            )
        ranked: List[Dict[str, Any]] = []
        step = golden[max(0, min(start_index, len(golden) - 1))]
        variants: List[str] = []
        if isinstance(configured_variants, list):
            variants = [str(value)[:180] for value in configured_variants[: max_paths - 1] if str(value).strip()]
        step_text = str(step or "")
        if not variants:
            if "fetch_skill_instructions" in step_text:
                variants = ["Use exact skill id/name first", "Verify resolver diagnostics before executing the skill"]
            elif "验证" in step_text or "test" in step_text.lower():
                variants = ["Run the narrowest relevant verification first", "Escalate only after local signal is clean"]
            else:
                variants = ["Adapt the concrete tool/action to current evidence while preserving the verification intent"]
        current_actions = [step_text, *variants]
        for offset, action in enumerate(current_actions[:max_paths]):
            ranked.append(
                {
                    "rank": len(ranked) + 1,
                    "stepIndex": start_index,
                    "behaviorMatch": round(max(0.05, base_score / (offset + 1)), 4),
                    "evidence": diagnostics.get("matchedReasons") or [],
                    "suggestedAction": str(action)[:260],
                    "reasonableVariants": [value for value in variants[:3] if value != action],
                    "avoid": anti[:2],
                    "verify": verify[:2],
                }
            )
        return ranked

    def maintenance_consolidate(self) -> Dict[str, Any]:
        candidates = self.list_candidates(limit=500)
        updated = 0
        quarantined = 0
        activated = 0
        for item in candidates:
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            next_status = self._candidate_status_for_counts(
                success_count=int(item.get("success_count") or 0),
                correction_count=int(item.get("correction_count") or 0),
                negative_feedback_count=int(item.get("negative_feedback_count") or 0),
                current=str(item.get("status") or "candidate"),
                risk_tier=str(item.get("riskTier") or metadata.get("riskTier") or "low"),
                has_runtime_evidence=bool(metadata.get("hasRuntimeEvidence")),
            )
            if next_status != item.get("status"):
                self.update_candidate(item["id"], {"status": next_status})
                updated += 1
                if next_status == "quarantine":
                    quarantined += 1
                if next_status == "active_hint":
                    activated += 1
        return {
            "candidateCount": len(candidates),
            "updatedCount": updated,
            "activatedCount": activated,
            "quarantinedCount": quarantined,
        }

    def export_candidate(self, candidate: Dict[str, Any]) -> None:
        if not candidate:
            return
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(candidate.get("id") or "candidate"))
        folder = self.export_root / "candidates"
        folder.mkdir(parents=True, exist_ok=True)
        json_path = folder / f"{safe_id}.json"
        md_path = folder / f"{safe_id}.md"
        json_path.write_text(json.dumps(to_jsonable(candidate), ensure_ascii=False, indent=2), encoding="utf-8")
        md_lines = [
            f"# {candidate.get('task_family') or safe_id}",
            "",
            f"- ID: `{candidate.get('id')}`",
            f"- Status: `{candidate.get('status')}`",
            f"- Scope: `{candidate.get('scope')}`",
            f"- Risk: `{candidate.get('riskTier') or candidate.get('risk_tier') or 'low'}`",
            f"- Approval required: `{bool(candidate.get('approvalRequired') or candidate.get('approval_required'))}`",
            f"- Maturity: `{candidate.get('maturity_score')}`",
            f"- Successes: `{candidate.get('success_count')}`",
            f"- Corrections: `{candidate.get('correction_count')}`",
            "",
            "## Golden Path",
            *[f"- {step}" for step in candidate.get("goldenPathSteps", [])],
            "",
            "## Anti-Patterns",
            *[f"- {step}" for step in candidate.get("antiPatterns", [])],
            "",
            "## Verification",
            *[f"- {step}" for step in candidate.get("verificationSteps", [])],
        ]
        md_path.write_text("\n".join(md_lines), encoding="utf-8")


workflow_memory_service = WorkflowMemoryService()
