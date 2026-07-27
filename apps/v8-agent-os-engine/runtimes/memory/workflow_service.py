from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.database import db
from core.json_safe import to_jsonable
from core.realtime_protocol import utc_now_iso
from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME
from runtimes.memory.workspace_scope import (
    canonical_workspace_scope,
    resolve_workspace_scope_identity,
    workspace_directory_exists,
)


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
    "engineering": {
        "enabled": True,
        "extractFromProofLedger": True,
        "requireEngineeringModeForInjection": True,
        "requireVerifiedProofForActivation": True,
        "learnFailedVerificationAsAntiPattern": True,
        "minVerifiedSuccessCount": 2,
    },
    "retention": {
        "pendingGuideTtlHours": 72,
        "episodeDays": 365,
        "hintDays": 180,
        "guideDays": 365,
        "engineeringProofDays": 730,
        "maintenancePageSize": 200,
    },
}

ACTIVE_WORKFLOW_STATUSES = {"active_hint", "approved"}
TERMINAL_GUIDE_STATES = {"helped", "ignored", "conflict", "failed", "verified", "contradicted"}
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
    engineering = cfg.get("engineering")
    if not isinstance(engineering, dict):
        engineering = {}
    engineering_defaults = dict(WORKFLOW_MEMORY_DEFAULTS["engineering"])
    cfg["engineering"] = {**engineering_defaults, **engineering}
    for key in (
        "enabled",
        "extractFromProofLedger",
        "requireEngineeringModeForInjection",
        "requireVerifiedProofForActivation",
        "learnFailedVerificationAsAntiPattern",
    ):
        cfg["engineering"][key] = bool(cfg["engineering"].get(key))
    try:
        cfg["engineering"]["minVerifiedSuccessCount"] = max(1, min(int(cfg["engineering"].get("minVerifiedSuccessCount") or 2), 10))
    except (TypeError, ValueError):
        cfg["engineering"]["minVerifiedSuccessCount"] = 2
    retention = cfg.get("retention")
    if not isinstance(retention, dict):
        retention = {}
    retention_defaults = dict(WORKFLOW_MEMORY_DEFAULTS["retention"])
    cfg["retention"] = {**retention_defaults, **retention}
    for key, minimum, maximum in (
        ("pendingGuideTtlHours", 1, 24 * 30),
        ("episodeDays", 7, 3650),
        ("hintDays", 7, 3650),
        ("guideDays", 7, 3650),
        ("engineeringProofDays", 30, 3650),
        ("maintenancePageSize", 10, 500),
    ):
        try:
            cfg["retention"][key] = max(minimum, min(int(cfg["retention"].get(key) or retention_defaults[key]), maximum))
        except (TypeError, ValueError):
            cfg["retention"][key] = retention_defaults[key]
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
    family = _norm_text(task_family).lower() or "workflow"
    trigger_text = " ".join(_norm_text(item).lower() for item in triggers if _norm_text(item))
    tokens = sorted(_meaningful_tokens(trigger_text))
    if not tokens:
        tokens = sorted(_meaningful_tokens(_norm_text(intent).lower()))[:12]
    compact = " | ".join([family, " ".join(tokens[:20])]).strip(" |") or "workflow"
    digest = hashlib.sha1(compact.encode("utf-8")).hexdigest()[:12]
    return f"wf:{digest}"


def _canonical_workflow_scope(scope: Any) -> str:
    normalized = _norm_text(scope) or "global"
    if normalized in {"global", "workspace:main"}:
        return normalized
    identity = resolve_workspace_scope_identity(scope_alias=normalized)
    write_scope = str((identity or {}).get("writeScope") or "").strip()
    return write_scope or normalized


def _scoped_task_family_signature(base_signature: str, scope: str) -> str:
    normalized_scope = _canonical_workflow_scope(scope)
    if normalized_scope == "global":
        return base_signature
    digest = hashlib.sha256(
        f"{normalized_scope}\n{base_signature}".encode("utf-8")
    ).hexdigest()[:20]
    return f"wf:scoped:{digest}"


def _workflow_scope_owner(scope: Any) -> str:
    normalized = _norm_text(scope) or "global"
    if normalized == "global":
        return "global"
    # Historical workspace:main rows do not prove which physical default they
    # belonged to. Keep them in their own legacy bucket instead of attaching
    # them to today's default workspace during a manual merge.
    if normalized == "workspace:main":
        return "scope:workspace:main"
    identity = resolve_workspace_scope_identity(scope_alias=normalized)
    workspace_key = str((identity or {}).get("workspaceKey") or "").strip()
    return f"workspace:{workspace_key}" if workspace_key else f"scope:{normalized}"


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
    data["proofRefs"] = _json_load(data.pop("proof_refs_json", None), [])
    data["workflowClass"] = data.get("workflow_class") or "general"
    data["sourceRuntime"] = data.get("source_runtime")
    data["verificationBacked"] = bool(data.get("verification_backed"))
    data["worksetRisk"] = data.get("workset_risk")
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
    data["workflowClass"] = data.get("workflow_class") or "general"
    data["sourceRuntime"] = data.get("source_runtime")
    data["proofBacked"] = bool(data.get("proof_backed"))
    data["verificationBacked"] = bool(data.get("verification_backed"))
    data["lastVerificationStatus"] = data.get("last_verification_status")
    data["worksetRisk"] = data.get("workset_risk")
    data["outsideWriteSetCount"] = int(data.get("outside_write_set_count") or 0)
    data["manualOverrideCount"] = int(data.get("manual_override_count") or 0)
    data["proofEntryIds"] = _json_load(data.pop("proof_entry_ids_json", None), [])
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
                "task brief",
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
                "canonicalAnchors": ["任务模式", "拆解", "分工", "task brief", "todos", "ask_user"],
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

    def maintenance_cursor(self, phase: str) -> Dict[str, Any]:
        normalized_phase = str(phase or "").strip()
        if not normalized_phase:
            raise ValueError("maintenance cursor phase is required")
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM memory_maintenance_cursors WHERE phase = ?",
                (normalized_phase,),
            ).fetchone()
        return dict(row) if row else {
            "phase": normalized_phase,
            "cursor_value": "",
            "cycle_count": 0,
            "last_batch_count": 0,
        }

    def advance_maintenance_cursor(
        self,
        phase: str,
        *,
        cursor_value: str,
        batch_count: int,
        wrapped: bool = False,
    ) -> Dict[str, Any]:
        current = self.maintenance_cursor(phase)
        next_cycle = int(current.get("cycle_count") or 0) + (1 if wrapped else 0)
        now = utc_now_iso()
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO memory_maintenance_cursors (
                    phase, cursor_value, cycle_count, last_batch_count, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(phase) DO UPDATE SET
                    cursor_value = excluded.cursor_value,
                    cycle_count = excluded.cycle_count,
                    last_batch_count = excluded.last_batch_count,
                    updated_at = excluded.updated_at
                """,
                (str(phase), str(cursor_value or ""), next_cycle, max(0, int(batch_count or 0)), now),
            )
            conn.commit()
        return {
            "phase": str(phase),
            "cursorValue": str(cursor_value or ""),
            "cycleCount": next_cycle,
            "lastBatchCount": max(0, int(batch_count or 0)),
            "wrapped": bool(wrapped),
            "updatedAt": now,
        }

    def _candidate_export_paths(self, candidate_id: str) -> tuple[Path, Path]:
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(candidate_id or "candidate"))
        folder = self.export_root / "candidates"
        return folder / f"{safe_id}.json", folder / f"{safe_id}.md"

    def _remove_candidate_exports(self, candidate_id: str) -> List[str]:
        removed: List[str] = []
        for path in self._candidate_export_paths(candidate_id):
            try:
                if path.exists():
                    path.unlink()
                    removed.append(str(path))
            except OSError:
                continue
        return removed

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
        normalized_scope = _canonical_workflow_scope(scope)
        base_signature = _norm_text(payload.get("taskFamilySignature") or payload.get("task_family_signature") or "")
        if not base_signature:
            base_signature = _signature_for(task_family, triggers, initial_intent)
        signature = _scoped_task_family_signature(base_signature, normalized_scope)
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
        workflow_class = _norm_text(payload.get("workflowClass") or payload.get("workflow_class")) or "general"
        source_runtime = _norm_text(payload.get("sourceRuntime") or payload.get("source_runtime"))
        proof_refs = _as_list(payload.get("proofRefs") or payload.get("proof_refs") or payload.get("proofEntryIds") or payload.get("proof_entry_ids"))
        verification_status = _norm_text(payload.get("lastVerificationStatus") or payload.get("verificationStatus") or payload.get("verification_status"))
        verification_backed = bool(payload.get("verificationBacked") or payload.get("verification_backed") or verification_status == "verified")
        workset_risk = _norm_text(payload.get("worksetRisk") or payload.get("workset_risk"))
        activation_allowed, approval_required, activation_reason = _activation_allowed_for_candidate(
            risk_tier=risk_tier,
            current="candidate",
        )
        return {
            "id": _norm_text(payload.get("id")) or f"mw_ep_{uuid.uuid4().hex}",
            "session_id": session_id,
            "run_id": run_id,
            "scope": normalized_scope,
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
            "workflow_class": workflow_class,
            "source_runtime": source_runtime,
            "proof_refs": proof_refs,
            "verification_backed": verification_backed,
            "workset_risk": workset_risk,
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
                "workflowClass": workflow_class,
                "sourceRuntime": source_runtime,
                "proofRefs": proof_refs,
                "verificationBacked": verification_backed,
                "lastVerificationStatus": verification_status,
                "worksetRisk": workset_risk,
                "proofBacked": bool(proof_refs),
                "baseTaskFamilySignature": base_signature,
                "outsideWriteSetCount": int(payload.get("outsideWriteSetCount") or payload.get("outside_write_set_count") or 0),
                "manualOverrideCount": int(payload.get("manualOverrideCount") or payload.get("manual_override_count") or 0),
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
                 side_effect_scope, privacy_scope, status, confidence, extraction_source, workflow_class,
                 source_runtime, proof_refs_json, verification_backed, workset_risk, metadata_json,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM memory_workflow_episodes WHERE id = ?), ?), ?)
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
                    episode.get("workflow_class") or "general",
                    episode.get("source_runtime"),
                    _json_dump(episode.get("proof_refs")),
                    1 if episode.get("verification_backed") else 0,
                    episode.get("workset_risk"),
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

    def record_engineering_proof_episode(
        self,
        *,
        proof_entry: Dict[str, Any],
        workset_observations: Optional[List[Dict[str, Any]]] = None,
        source_component: str = "engineering_proof_collector",
    ) -> Dict[str, Any]:
        """Distill an engineering proof entry into the normal workflow memory chain."""

        cfg = workflow_memory_config()
        engineering_cfg = cfg.get("engineering") if isinstance(cfg.get("engineering"), dict) else {}
        if not bool(engineering_cfg.get("enabled", True)) or not bool(engineering_cfg.get("extractFromProofLedger", True)):
            return {"status": "skipped", "reason": "engineering_workflow_memory_disabled"}
        if not proof_entry or not proof_entry.get("id"):
            return {"status": "skipped", "reason": "missing_proof_entry"}
        mode = str(proof_entry.get("mode") or "").strip().lower()
        if mode == "dry_run":
            return {"status": "skipped", "reason": "dry_run_not_learned", "proofEntryId": proof_entry.get("id")}

        metadata = proof_entry.get("metadata") if isinstance(proof_entry.get("metadata"), dict) else {}
        trigger = metadata.get("triggerDecision") if isinstance(metadata.get("triggerDecision"), dict) else {}
        engineering_mode = str(metadata.get("engineeringMode") or "").strip().lower()
        if not (trigger.get("active") or engineering_mode == "force"):
            return {"status": "skipped", "reason": "engineering_mode_inactive", "proofEntryId": proof_entry.get("id")}

        verification_status = str(proof_entry.get("verificationStatus") or "planned").strip().lower()
        if verification_status == "failed_verification" and not bool(engineering_cfg.get("learnFailedVerificationAsAntiPattern", True)):
            return {"status": "skipped", "reason": "failed_verification_learning_disabled", "proofEntryId": proof_entry.get("id")}

        diagnostics = proof_entry.get("diagnostics") if isinstance(proof_entry.get("diagnostics"), dict) else {}
        workset_correlation = proof_entry.get("worksetCorrelation") if isinstance(proof_entry.get("worksetCorrelation"), dict) else {}
        if not workset_correlation:
            workset_correlation = diagnostics.get("worksetCorrelation") if isinstance(diagnostics.get("worksetCorrelation"), dict) else {}
        workset_risk = str(
            workset_correlation.get("risk")
            or (diagnostics.get("worksetRisk") if isinstance(diagnostics.get("worksetRisk"), dict) else {}).get("risk")
            or ""
        ).strip()
        outside_files = _as_list(proof_entry.get("outsideWriteSetFiles") or diagnostics.get("outsideWriteSetFiles") or workset_correlation.get("outsideWriteSetFiles"))
        manual_override = proof_entry.get("manualOverride") if isinstance(proof_entry.get("manualOverride"), dict) else diagnostics.get("manualOverride")
        manual_override_present = bool((manual_override or {}).get("present")) if isinstance(manual_override, dict) else bool(manual_override)
        commands = [item for item in _as_list(proof_entry.get("commands")) if isinstance(item, dict)]
        diagnostics_items = [item for item in _as_list(diagnostics.get("items")) if isinstance(item, dict)]
        changed_files = [str(item).strip() for item in _as_list(proof_entry.get("changedFiles")) if str(item).strip()]
        write_set = [str(item).strip() for item in _as_list(proof_entry.get("writeSet")) if str(item).strip()]
        file_family = self._engineering_file_family([*changed_files, *write_set])
        verification_family = self._engineering_verification_family(commands=commands, diagnostics=diagnostics_items, verification_status=verification_status)
        dominant_tools = self._engineering_dominant_tool_chain(commands=commands)
        behavior_scope = self._engineering_behavior_scope(proof_entry=proof_entry, file_family=file_family)
        task_family_parts = " / ".join(part for part in (behavior_scope, verification_family) if part)
        task_family = f"Engineering workflow: {task_family_parts}" if task_family_parts else "Engineering workflow"
        signature = self._engineering_task_signature(
            task_family=task_family,
            file_family=file_family,
            verification_family=verification_family,
            dominant_tools=dominant_tools,
            behavior_scope=behavior_scope,
            scope=str(proof_entry.get("scope") or ""),
        )

        successful_validation = [cmd for cmd in commands if cmd.get("isValidation") and (cmd.get("returnCode") in (0, "0"))]
        failed_validation = [cmd for cmd in commands if cmd.get("isValidation") and cmd.get("returnCode") not in (None, 0, "0")]
        residual_risks = [str(item).strip() for item in _as_list(proof_entry.get("residualRisks")) if str(item).strip()]
        failure_markers: List[str] = []
        if failed_validation:
            failure_markers.append("A validation command failed before the final proof state was accepted.")
        if outside_files:
            failure_markers.append("Changed files were observed outside the planned writeSet.")
        if manual_override_present:
            failure_markers.append("Supervisor manual override was present in the workset decision.")
        failure_markers.extend(residual_risks[:4])

        verified = verification_status == "verified"
        status_hint = "success_after_corrections" if verified and failure_markers else "success" if verified else "candidate"
        final_success = (
            f"Verified engineering proof via {verification_family}."
            if verified
            else ""
        )
        anti_patterns = self._engineering_anti_patterns(
            verification_status=verification_status,
            failure_markers=failure_markers,
            workset_risk=workset_risk,
        )
        golden_path = self._engineering_golden_path(
            verification_status=verification_status,
            behavior_scope=behavior_scope,
            verification_family=verification_family,
            workset_risk=workset_risk,
        )
        if not verified:
            golden_path = []
        payload = {
            "id": f"mw_ep_eng_{proof_entry.get('id')}",
            "taskFamily": task_family,
            "taskFamilySignature": signature,
            "initialUserIntent": str(proof_entry.get("patchIntent") or task_family)[:500],
            "canonicalTriggerPatterns": self._engineering_triggers(
                behavior_scope=behavior_scope,
                file_family=file_family,
                verification_family=verification_family,
                dominant_tools=dominant_tools,
            ),
            "firstActionSignature": dominant_tools[0] if dominant_tools else "inspect engineering proof",
            "runtimeLane": "engineering_lane",
            "orderedActions": self._engineering_ordered_actions(commands=commands, verification_status=verification_status),
            "toolSkillSequence": dominant_tools,
            "failureMarkers": failure_markers,
            "userCorrectionPoints": ["manual override"] if manual_override_present else [],
            "finalSuccessEvidence": final_success,
            "userVerdict": "verified" if verified else verification_status,
            "sideEffectScope": "engineering_code_change",
            "privacyScope": "local_runtime",
            "status": status_hint,
            "confidence": 0.82 if verified else 0.55,
            "runtimeEvidence": [
                {
                    "proofEntryId": proof_entry.get("id"),
                    "verificationStatus": verification_status,
                    "changedFileCount": len(changed_files),
                    "commandCount": len(commands),
                    "worksetRisk": workset_risk,
                }
            ],
            "evidenceSource": "proof_ledger",
            "goldenPathSteps": golden_path,
            "antiPatterns": anti_patterns,
            "verificationSteps": self._engineering_verification_steps(verification_family=verification_family, commands=commands),
            "riskTier": "medium" if workset_risk in {"outside_write_set", "missing_write_set", "unknown_write_set"} or manual_override_present else "low",
            "workflowClass": "engineering",
            "sourceRuntime": "engineering_lane",
            "proofRefs": [proof_entry.get("id")],
            "verificationBacked": verified,
            "lastVerificationStatus": verification_status,
            "worksetRisk": workset_risk,
            "outsideWriteSetCount": len(outside_files),
            "manualOverrideCount": 1 if manual_override_present else 0,
            "metadata": {
                "worksetObservationIds": [
                    str(item.get("id"))
                    for item in list(workset_observations or [])
                    if isinstance(item, dict) and item.get("id")
                ][:24],
                "fileFamily": file_family,
                "verificationFamily": verification_family,
                "dominantToolChain": dominant_tools,
                "sourceComponent": source_component,
            },
        }
        scope = self._scope_for_engineering_proof(proof_entry)
        if not scope:
            return {
                "status": "skipped",
                "reason": "workspace_identity_missing",
                "proofEntryId": proof_entry.get("id"),
            }
        session_id = str(proof_entry.get("sessionId") or proof_entry.get("session_id") or "").strip() or None
        run_id = str(proof_entry.get("runId") or proof_entry.get("run_id") or "").strip() or None
        episode = self.normalize_episode_payload(
            payload,
            session_id=session_id,
            run_id=run_id,
            scope=scope,
            extraction_source="engineering_proof_ledger",
        )
        record = self.add_episode(episode)
        self._emit_engineering_workflow_events(record=record, proof_entry=proof_entry, source_component=source_component)
        return {
            "status": "extracted",
            "proofEntryId": proof_entry.get("id"),
            "episode": record.get("episode"),
            "candidate": record.get("candidate"),
        }

    def _scope_for_engineering_proof(self, proof_entry: Dict[str, Any]) -> str:
        session_id = str(proof_entry.get("sessionId") or proof_entry.get("session_id") or "").strip()
        if session_id:
            try:
                binding = db.get_session_scope_binding(session_id) or {}
                workspace_path = str(binding.get("workspace_path") or "").strip()
                if workspace_path:
                    if not workspace_directory_exists(workspace_path):
                        return ""
                    return canonical_workspace_scope(workspace_path)
                resolved = str(binding.get("resolved_scope") or "").strip()
                if resolved and resolved not in {"global", "workspace:main"}:
                    identity = resolve_workspace_scope_identity(
                        scope_alias=resolved,
                        project_id=str(binding.get("project_id") or "").strip() or None,
                        workspace_id=str(binding.get("workspace_id") or "").strip() or None,
                    )
                    if identity and workspace_directory_exists(str(identity.get("workspacePath") or "")):
                        return str(identity.get("writeScope") or "")
            except Exception:
                pass
        metadata = proof_entry.get("metadata") if isinstance(proof_entry.get("metadata"), dict) else {}
        workspace_path = str(
            proof_entry.get("workspaceRoot")
            or proof_entry.get("workspace_root")
            or metadata.get("workspaceRoot")
            or metadata.get("workspace_root")
            or metadata.get("workspacePath")
            or metadata.get("workspace_path")
            or ""
        ).strip()
        if workspace_path:
            if not workspace_directory_exists(workspace_path):
                return ""
            return canonical_workspace_scope(workspace_path)
        project_id = str(metadata.get("projectId") or metadata.get("project_id") or "").strip()
        workspace_id = str(metadata.get("workspaceId") or metadata.get("workspace_id") or "").strip()
        identity = resolve_workspace_scope_identity(
            project_id=project_id or None,
            workspace_id=workspace_id or None,
        )
        if identity and workspace_directory_exists(str(identity.get("workspacePath") or "")):
            return str(identity.get("writeScope") or "")
        return ""

    def _engineering_file_family(self, paths: Iterable[Any]) -> str:
        extensions: set[str] = set()
        buckets: set[str] = set()
        for raw in paths:
            text = str(raw or "").replace("\\", "/").strip()
            if not text:
                continue
            name = text.rsplit("/", 1)[-1]
            if "." in name:
                suffix = "." + name.rsplit(".", 1)[-1].lower()
                if 1 < len(suffix) <= 10:
                    extensions.add(suffix)
            lowered = text.lower()
            for marker, bucket in (
                ("apps/v8-agent-os-engine", "engine"),
                ("apps/v8-agent-os-admin", "admin"),
                ("apps/v8-agent-os-phone", "phone"),
                ("apps/v8-agent-os-web", "web"),
                ("packages/", "shared_package"),
                ("tests/", "tests"),
                ("docs/", "docs"),
            ):
                if marker in lowered:
                    buckets.add(bucket)
        parts = [*sorted(buckets)[:4], *sorted(extensions)[:4]]
        return " ".join(parts) or "repo_files"

    def _engineering_verification_family(self, *, commands: List[Dict[str, Any]], diagnostics: List[Dict[str, Any]], verification_status: str) -> str:
        families: set[str] = set()
        for command in commands:
            text = str(command.get("command") or command.get("summary") or "").lower()
            if "pytest" in text or "py_compile" in text:
                families.add("python_validation")
            if "typecheck" in text or "tsc" in text:
                families.add("typescript_typecheck")
            if "npm run build" in text or "pnpm build" in text or "yarn build" in text:
                families.add("frontend_build")
            if "test" in text or "vitest" in text:
                families.add("test_suite")
        if not families and diagnostics:
            families.add("diagnostics_review")
        if not families:
            families.add(str(verification_status or "proof_review"))
        return " ".join(sorted(families)[:4])

    def _engineering_dominant_tool_chain(self, *, commands: List[Dict[str, Any]]) -> List[str]:
        tools: List[str] = []
        for command in commands:
            tool = str(command.get("tool") or "").strip()
            if tool:
                tools.append(tool)
            command_text = str(command.get("command") or "").strip().lower()
            if "pytest" in command_text:
                tools.append("pytest")
            elif "py_compile" in command_text:
                tools.append("py_compile")
            elif "typecheck" in command_text or "tsc" in command_text:
                tools.append("typecheck")
            elif "build" in command_text:
                tools.append("build")
        return _uniq(tools, limit=8)

    def _engineering_behavior_scope(self, *, proof_entry: Dict[str, Any], file_family: str) -> str:
        text = f"{proof_entry.get('patchIntent') or ''} {file_family}".lower()
        if any(token in text for token in ("review", "审查", "audit")):
            return "code_review"
        if any(token in text for token in ("test", "验证", "typecheck", "build")):
            return "verification"
        if any(token in text for token in ("doc", "docs", "文档")):
            return "docs_update"
        if any(token in text for token in ("fix", "修复", "bug", "error", "报错")):
            return "bug_fix"
        if any(token in text for token in ("refactor", "重构", "architecture", "架构")):
            return "refactor"
        return "implementation"

    def _engineering_task_signature(
        self,
        *,
        task_family: str,
        file_family: str,
        verification_family: str,
        dominant_tools: List[str],
        behavior_scope: str,
        scope: str,
    ) -> str:
        canonical = " ".join([task_family, file_family, verification_family, " ".join(dominant_tools), behavior_scope, scope])
        return _signature_for("engineering workflow", [canonical], behavior_scope)

    def _engineering_triggers(
        self,
        *,
        behavior_scope: str,
        file_family: str,
        verification_family: str,
        dominant_tools: List[str],
    ) -> List[str]:
        return _uniq(
            [
                "engineering mode",
                "工程任务",
                behavior_scope,
                file_family,
                verification_family,
                *dominant_tools,
            ],
            limit=16,
        )

    def _engineering_ordered_actions(self, *, commands: List[Dict[str, Any]], verification_status: str) -> List[str]:
        actions = ["inspect engineering context and write-set"]
        if commands:
            actions.append("execute or observe engineering command evidence")
        if verification_status == "verified":
            actions.append("record verified proof ledger outcome")
        elif verification_status == "failed_verification":
            actions.append("record failed verification as anti-pattern")
        else:
            actions.append("record unverified proof and residual risks")
        return actions

    def _engineering_golden_path(self, *, verification_status: str, behavior_scope: str, verification_family: str, workset_risk: str) -> List[str]:
        if verification_status != "verified":
            return []
        steps = [
            "先读取 Engineering ContextPack / Supervisor task contract，确认 read-set、write-set 和任务边界。",
            "只在授权 writeSet 内做最小必要修改；如果需要越界，先让 supervisor 重新确认或记录 override。",
            f"完成改动后运行与任务匹配的验证链：{verification_family or 'targeted validation'}。",
            "把 changed files、验证命令、diagnostics、residual risks 写入 Proof Ledger；没有验证证据不得标记 verified。",
        ]
        if behavior_scope in {"code_review", "verification", "docs_update"}:
            steps[1] = "默认保持只读纪律；除非 task brief 明确授权写入，否则不要修改生产代码。"
        if workset_risk in {"outside_write_set", "missing_write_set", "unknown_write_set"}:
            steps.insert(2, "若 Proof/Observation 显示 write-set 风险，先修正 task capsule 或记录人工批准原因。")
        return steps

    def _engineering_anti_patterns(self, *, verification_status: str, failure_markers: List[str], workset_risk: str) -> List[str]:
        anti = list(failure_markers[:6])
        if verification_status == "failed_verification":
            anti.append("不要把失败验证后的聊天总结当成工程完成证据。")
        if verification_status == "unverified":
            anti.append("不要在未观察到验证命令时把工程链路升级为 active hint。")
        if workset_risk in {"outside_write_set", "missing_write_set", "unknown_write_set"}:
            anti.append("不要把越界写入或缺失 writeSet 的流程沉淀为默认 golden path。")
        return _uniq(anti, limit=10)

    def _engineering_verification_steps(self, *, verification_family: str, commands: List[Dict[str, Any]]) -> List[str]:
        validation_labels: List[str] = []
        for cmd in commands:
            if not cmd.get("isValidation"):
                continue
            text = str(cmd.get("command") or cmd.get("summary") or "").lower()
            if "pytest" in text or "py_compile" in text:
                validation_labels.append("复用同类 Python validation，优先选择最窄测试/编译检查。")
            elif "typecheck" in text or "tsc" in text:
                validation_labels.append("复用同类 TypeScript typecheck，先跑与改动面匹配的检查。")
            elif "npm run build" in text or "pnpm build" in text or "yarn build" in text or "build" in text:
                validation_labels.append("复用同类 frontend build，确认产物构建与类型约束通过。")
            elif "test" in text or "vitest" in text:
                validation_labels.append("复用同类 test suite，优先选择覆盖改动面的测试。")
        if validation_labels:
            return _uniq(validation_labels, limit=6)
        return [
            f"选择与 {verification_family or 'current task'} 匹配的最窄验证命令。",
            "Proof Ledger 中必须能看到验证结果或明确的未验证风险。",
        ]

    def _emit_engineering_workflow_events(self, *, record: Dict[str, Any], proof_entry: Dict[str, Any], source_component: str) -> None:
        try:
            session_id = str(proof_entry.get("sessionId") or proof_entry.get("session_id") or "").strip()
            run_id = str(proof_entry.get("runId") or proof_entry.get("run_id") or "").strip()
            if not session_id:
                return
            episode = record.get("episode") if isinstance(record.get("episode"), dict) else {}
            candidate = record.get("candidate") if isinstance(record.get("candidate"), dict) else {}
            for topic, payload in (
                (
                    "memory.workflow.episode_extracted",
                    {
                        "session_id": session_id,
                        "episode_id": episode.get("id"),
                        "candidate_id": candidate.get("id"),
                        "task_family": episode.get("task_family"),
                        "status": episode.get("status"),
                        "candidate_status": candidate.get("status"),
                        "extraction_source": "engineering_proof_ledger",
                        "risk_tier": candidate.get("riskTier") or candidate.get("risk_tier"),
                        "workflowClass": "engineering",
                        "sourceRuntime": "engineering_lane",
                        "proofEntryId": proof_entry.get("id"),
                        "verificationStatus": proof_entry.get("verificationStatus"),
                    },
                ),
                (
                    "memory.workflow.candidate_updated",
                    {
                        "candidate_id": candidate.get("id"),
                        "task_family": candidate.get("task_family"),
                        "status": candidate.get("status"),
                        "success_count": candidate.get("success_count"),
                        "correction_count": candidate.get("correction_count"),
                        "negative_feedback_count": candidate.get("negative_feedback_count"),
                        "workflowClass": "engineering",
                        "sourceRuntime": "engineering_lane",
                        "proofEntryId": proof_entry.get("id"),
                        "verificationStatus": proof_entry.get("verificationStatus"),
                    },
                ),
            ):
                db.add_runtime_event({
                    "event_id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "run_id": run_id or None,
                    "seq": db.get_next_runtime_seq(session_id),
                    "kind": "event",
                    "topic": topic,
                    "ts": utc_now_iso(),
                    "source": {
                        "plane": "engine",
                        "component": "memory.workflow",
                        "node": source_component,
                        "agent_id": None,
                    },
                    "payload": payload,
                })
        except Exception:
            return

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
            workflow_class = str(episode.get("workflow_class") or episode_meta.get("workflowClass") or (existing or {}).get("workflowClass") or "general").strip() or "general"
            source_runtime = str(episode.get("source_runtime") or episode_meta.get("sourceRuntime") or (existing or {}).get("sourceRuntime") or "").strip() or None
            proof_refs = _as_list(episode.get("proof_refs") or episode_meta.get("proofRefs"))
            proof_entry_ids = _uniq((existing or {}).get("proofEntryIds", []) + proof_refs, limit=100)
            verification_status = str(episode_meta.get("lastVerificationStatus") or (existing or {}).get("lastVerificationStatus") or "").strip()
            verification_backed = bool(episode.get("verification_backed") or episode_meta.get("verificationBacked") or (existing or {}).get("verificationBacked"))
            proof_backed = bool(proof_entry_ids or (existing or {}).get("proofBacked"))
            workset_risk = str(episode.get("workset_risk") or episode_meta.get("worksetRisk") or (existing or {}).get("worksetRisk") or "").strip()
            outside_write_set_count = int((existing or {}).get("outsideWriteSetCount") or 0) + int(episode_meta.get("outsideWriteSetCount") or 0)
            manual_override_count = int((existing or {}).get("manualOverrideCount") or 0) + int(episode_meta.get("manualOverrideCount") or 0)
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
            if workflow_class == "engineering":
                engineering_cfg = workflow_memory_config().get("engineering") if isinstance(workflow_memory_config().get("engineering"), dict) else {}
                min_verified = int(engineering_cfg.get("minVerifiedSuccessCount") or 2)
                risky_workset = workset_risk in {"outside_write_set", "missing_write_set", "unknown_write_set"} or outside_write_set_count > 0 or manual_override_count > 0
                if verification_status == "failed_verification":
                    status = "candidate" if status != "quarantine" else status
                    activation_allowed = False
                    activation_reason = "failed_verification_never_auto_active"
                elif engineering_cfg.get("requireVerifiedProofForActivation", True) and not (verification_backed and proof_backed):
                    status = "candidate" if status != "quarantine" else status
                    activation_allowed = False
                    activation_reason = "requires_verified_proof_backed_engineering_evidence"
                elif success_count < min_verified and status == "active_hint":
                    status = "candidate"
                    activation_reason = "below_min_verified_success_count"
                if risky_workset and status == "active_hint":
                    status = "candidate"
                    activation_allowed = False
                    approval_required = True
                    activation_reason = "engineering_workset_risk_requires_approval"
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
                    "workflowClass": workflow_class,
                    "sourceRuntime": source_runtime,
                    "proofBacked": proof_backed,
                    "verificationBacked": verification_backed,
                    "lastVerificationStatus": verification_status,
                    "worksetRisk": workset_risk,
                    "outsideWriteSetCount": outside_write_set_count,
                    "manualOverrideCount": manual_override_count,
                    "proofEntryIds": proof_entry_ids,
                }
            )
            conn.execute(
                """
                INSERT INTO memory_workflow_candidates
                (id, task_family_signature, task_family, scope, canonical_trigger_patterns_json,
                 first_action_triggers_json, golden_path_steps_json, anti_patterns_json,
                 verification_steps_json, success_count, correction_count, negative_feedback_count,
                 maturity_score, status, confidence, source_episode_ids_json, risk_tier, approval_required,
                 last_hint_outcome, guide_state_json, merge_suggestion_json, workflow_class, source_runtime,
                 proof_backed, verification_backed, last_verification_status, workset_risk,
                 outside_write_set_count, manual_override_count, proof_entry_ids_json, last_seen_at,
                 metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    workflow_class = excluded.workflow_class,
                    source_runtime = excluded.source_runtime,
                    proof_backed = excluded.proof_backed,
                    verification_backed = excluded.verification_backed,
                    last_verification_status = excluded.last_verification_status,
                    workset_risk = excluded.workset_risk,
                    outside_write_set_count = excluded.outside_write_set_count,
                    manual_override_count = excluded.manual_override_count,
                    proof_entry_ids_json = excluded.proof_entry_ids_json,
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
                    workflow_class,
                    source_runtime,
                    1 if proof_backed else 0,
                    1 if verification_backed else 0,
                    verification_status,
                    workset_risk,
                    outside_write_set_count,
                    manual_override_count,
                    _json_dump(proof_entry_ids),
                    now,
                    _json_dump(metadata),
                    (existing or {}).get("created_at") or now,
                    now,
                ),
            )
            conn.commit()
        return self.get_candidate(candidate_id) or {}

    def list_candidates(
        self,
        *,
        status: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 50,
        workflow_class: Optional[str] = None,
        proof_backed: Optional[bool] = None,
        verification_status: Optional[str] = None,
        source_runtime: Optional[str] = None,
        cursor_after: Optional[str] = None,
        order: str = "recent",
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM memory_workflow_candidates WHERE 1=1"
        params: List[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if workflow_class:
            sql += " AND workflow_class = ?"
            params.append(workflow_class)
        if proof_backed is not None:
            sql += " AND proof_backed = ?"
            params.append(1 if proof_backed else 0)
        if verification_status:
            sql += " AND last_verification_status = ?"
            params.append(verification_status)
        if source_runtime:
            sql += " AND source_runtime = ?"
            params.append(source_runtime)
        if cursor_after:
            sql += " AND id > ?"
            params.append(str(cursor_after))
        if str(order or "recent").strip().lower() == "id_asc":
            sql += " ORDER BY id ASC"
        else:
            sql += " ORDER BY updated_at DESC, id DESC"
        sql += " LIMIT ?"
        params.append(max(1, min(int(limit or 50), 500)))
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
            deleted = cursor.rowcount > 0
        if deleted:
            self._remove_candidate_exports(candidate_id)
        return deleted

    def merge_candidates(self, target_id: str, source_ids: List[str]) -> Dict[str, Any]:
        target = self.get_candidate(target_id)
        if not target:
            raise ValueError(f"target workflow candidate not found: {target_id}")
        target_owner = _workflow_scope_owner(target.get("scope"))
        sources: List[tuple[str, Dict[str, Any]]] = []
        for source_id in source_ids:
            if source_id == target_id:
                continue
            source = self.get_candidate(source_id)
            if not source:
                continue
            source_owner = _workflow_scope_owner(source.get("scope"))
            if source_owner != target_owner:
                raise ValueError(
                    "workflow_candidate_scope_mismatch: candidates from different "
                    "physical workspaces cannot be merged"
                )
            sources.append((source_id, source))
        for source_id, source in sources:
            target["canonicalTriggerPatterns"] = _uniq(target.get("canonicalTriggerPatterns", []) + source.get("canonicalTriggerPatterns", []), limit=24)
            target["firstActionTriggers"] = _uniq(target.get("firstActionTriggers", []) + source.get("firstActionTriggers", []), limit=16)
            target["goldenPathSteps"] = _uniq(target.get("goldenPathSteps", []) + source.get("goldenPathSteps", []), limit=18)
            target["antiPatterns"] = _uniq(target.get("antiPatterns", []) + source.get("antiPatterns", []), limit=18)
            target["verificationSteps"] = _uniq(target.get("verificationSteps", []) + source.get("verificationSteps", []), limit=14)
            target["sourceEpisodeIds"] = _uniq(target.get("sourceEpisodeIds", []) + source.get("sourceEpisodeIds", []), limit=200)
            target["success_count"] = int(target.get("success_count") or 0) + int(source.get("success_count") or 0)
            target["correction_count"] = int(target.get("correction_count") or 0) + int(source.get("correction_count") or 0)
            target["negative_feedback_count"] = int(target.get("negative_feedback_count") or 0) + int(source.get("negative_feedback_count") or 0)
            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE memory_workflow_hint_events SET candidate_id = ? WHERE candidate_id = ?",
                    (target_id, source_id),
                )
                conn.execute(
                    "UPDATE memory_workflow_guide_states SET candidate_id = ? WHERE candidate_id = ?",
                    (target_id, source_id),
                )
                conn.commit()
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
        normalized_query = _norm_text(query)
        with db.get_connection() as conn:
            repeated = conn.execute(
                """
                SELECT id, metadata_json, created_at
                FROM memory_workflow_hint_events
                WHERE COALESCE(candidate_id, '') = COALESCE(?, '')
                  AND COALESCE(session_id, '') = COALESCE(?, '')
                  AND COALESCE(run_id, '') = COALESCE(?, '')
                  AND query = ? AND outcome = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (candidate_id, session_id, run_id, normalized_query, normalized_outcome),
            ).fetchone()
            if repeated:
                repeated_metadata = _json_load(repeated["metadata_json"], {})
                repeated_metadata.update(dict(metadata or {}))
                repeated_metadata["deliveryCount"] = int(repeated_metadata.get("deliveryCount") or 1) + 1
                repeated_metadata.setdefault("firstDeliveryAt", repeated["created_at"])
                repeated_metadata["lastDeliveryAt"] = now
                conn.execute(
                    "UPDATE memory_workflow_hint_events SET metadata_json = ?, updated_at = ? WHERE id = ?",
                    (_json_dump(repeated_metadata), now, repeated["id"]),
                )
                conn.commit()
                return {
                    "id": str(repeated["id"]),
                    "candidateId": candidate_id,
                    "outcome": normalized_outcome,
                    "aggregated": True,
                    "deliveryCount": int(repeated_metadata["deliveryCount"]),
                }
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
                (event_id, candidate_id, session_id, run_id, normalized_query, _json_dump(hint), normalized_outcome, _json_dump({**dict(metadata or {}), "deliveryCount": 1}), now, now),
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
        sql = "SELECT * FROM memory_workflow_guide_states WHERE candidate_id = ? AND is_current = 1"
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
        sql = "SELECT * FROM memory_workflow_guide_states WHERE is_current = 1 AND state IN ({})".format(
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

    def _engineering_active_from_runtime_events(self, events: Optional[List[Dict[str, Any]]] = None) -> bool:
        for event in reversed(list(events or [])):
            topic = str(event.get("topic") or "").strip().lower()
            source = event.get("source") if isinstance(event.get("source"), dict) else {}
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            runtime_id = str(event.get("runtime_id") or source.get("runtimeId") or payload.get("runtimeId") or "").strip().lower()
            if runtime_id == "engineering_lane" or topic.startswith("engineering."):
                if topic == "engineering_lane.trigger.decided":
                    trigger = payload.get("triggerDecision") if isinstance(payload.get("triggerDecision"), dict) else {}
                    if bool(trigger.get("active")) or str(payload.get("engineeringMode") or "").strip().lower() == "force":
                        return True
                if topic in {"engineering.proof.collected", "engineering.plan.projected"}:
                    return True
        return False

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
        if last in {"caused_failure", "failed"}:
            return 0.0, "caused_failure"
        if last in {"contradicted", "conflict"}:
            return 0.0, "contradicted"
        delta = 0.0
        try:
            delta += float(counts.get("helped_success") or 0) * 0.75
            delta -= float(counts.get("ignored") or 0) * 0.2
            delta -= float(counts.get("contradicted") or 0) * 0.6
        except (TypeError, ValueError):
            delta = 0.0
        if last in {"helped_success", "helped"}:
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
        normalized_state = str(state or "matched").strip().lower() or "matched"
        retention = dict(workflow_memory_config().get("retention") or {})
        expires_at = None
        finalized_at = now if normalized_state in TERMINAL_GUIDE_STATES else None
        if finalized_at is None:
            ttl_hours = max(1, int(retention.get("pendingGuideTtlHours") or 72))
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat().replace("+00:00", "Z")
        owner_sql = "candidate_id = ? AND is_current = 1"
        owner_params: List[Any] = [candidate_id]
        if run_id:
            owner_sql += " AND run_id = ?"
            owner_params.append(run_id)
        elif session_id:
            owner_sql += " AND run_id IS NULL AND session_id = ?"
            owner_params.append(session_id)
        else:
            owner_sql += " AND run_id IS NULL AND session_id IS NULL"
        guide_state = {
            "id": "",
            "candidateId": candidate_id,
            "state": normalized_state,
            "currentStepIndex": current_step_index,
            "lastEventTopic": last_event_topic,
            "outcome": outcome,
            "expiresAt": expires_at,
            "finalizedAt": finalized_at,
            "updatedAt": now,
            "metadata": metadata or {},
        }
        with db.get_connection() as conn:
            existing = conn.execute(
                f"SELECT id FROM memory_workflow_guide_states WHERE {owner_sql} ORDER BY updated_at DESC LIMIT 1",
                owner_params,
            ).fetchone()
            state_id = str(existing["id"]) if existing else f"mw_guide_{uuid.uuid4().hex}"
            guide_state["id"] = state_id
            if existing:
                conn.execute(
                    """
                    UPDATE memory_workflow_guide_states
                    SET session_id = ?, run_id = ?, query = ?, state = ?, current_step_index = ?,
                        last_event_topic = ?, outcome = ?, metadata_json = ?, expires_at = ?,
                        finalized_at = ?, is_current = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        session_id,
                        run_id,
                        query,
                        normalized_state,
                        current_step_index,
                        last_event_topic,
                        outcome,
                        _json_dump(metadata or {}),
                        expires_at,
                        finalized_at,
                        now,
                        state_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO memory_workflow_guide_states
                    (id, candidate_id, session_id, run_id, query, state, current_step_index,
                     last_event_topic, outcome, is_current, expires_at, finalized_at,
                     metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        state_id,
                        candidate_id,
                        session_id,
                        run_id,
                        query,
                        normalized_state,
                        current_step_index,
                        last_event_topic,
                        outcome,
                        expires_at,
                        finalized_at,
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

    def expire_pending_guides(self, *, limit: int = 500, dry_run: bool = False) -> Dict[str, Any]:
        now = utc_now_iso()
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_workflow_guide_states
                WHERE is_current = 1
                  AND state NOT IN ('helped', 'ignored', 'conflict', 'failed', 'verified', 'contradicted')
                  AND expires_at IS NOT NULL
                  AND datetime(expires_at) <= datetime(?)
                ORDER BY expires_at ASC
                LIMIT ?
                """,
                (now, max(1, min(int(limit or 500), 2000))),
            ).fetchall()
            if dry_run:
                return {
                    "dryRun": True,
                    "expiredCount": len(rows),
                    "guideStateIds": [str(row["id"]) for row in rows[:50]],
                }
            expired_ids: List[str] = []
            for row in rows:
                metadata = _json_load(row["metadata_json"], {})
                metadata.update({"terminalReason": "pending_ttl_expired", "terminalFinalizedAt": now})
                state_payload = {
                    "id": str(row["id"]),
                    "candidateId": str(row["candidate_id"] or ""),
                    "state": "ignored",
                    "currentStepIndex": int(row["current_step_index"] or 0),
                    "lastEventTopic": row["last_event_topic"],
                    "outcome": "ignored",
                    "expiresAt": None,
                    "finalizedAt": now,
                    "updatedAt": now,
                    "metadata": metadata,
                }
                conn.execute(
                    """
                    UPDATE memory_workflow_guide_states
                    SET state = 'ignored', outcome = 'ignored', expires_at = NULL,
                        finalized_at = ?, metadata_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, _json_dump(metadata), now, row["id"]),
                )
                if row["candidate_id"]:
                    conn.execute(
                        """
                        UPDATE memory_workflow_candidates
                        SET guide_state_json = ?, last_hint_outcome = 'ignored', updated_at = ?
                        WHERE id = ?
                        """,
                        (_json_dump(state_payload), now, row["candidate_id"]),
                    )
                expired_ids.append(str(row["id"]))
            conn.commit()
        return {"dryRun": False, "expiredCount": len(expired_ids), "guideStateIds": expired_ids[:50]}

    def finalize_guides_for_run(
        self,
        *,
        session_id: str,
        run_id: str,
        run_status: str,
    ) -> Dict[str, Any]:
        """Deterministically close each current candidate/run guide exactly once."""
        normalized_run_status = str(run_status or "").strip().lower()
        now = utc_now_iso()
        finalized: List[Dict[str, Any]] = []
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_workflow_guide_states
                WHERE run_id = ? AND is_current = 1
                ORDER BY updated_at ASC, id ASC
                """,
                (run_id,),
            ).fetchall()
            for row in rows:
                metadata = _json_load(row["metadata_json"], {})
                if row["finalized_at"] or metadata.get("terminalFinalizedAt"):
                    continue
                hint_event = conn.execute(
                    """
                    SELECT id, outcome FROM memory_workflow_hint_events
                    WHERE candidate_id = ? AND run_id = ?
                    ORDER BY updated_at DESC, created_at DESC LIMIT 1
                    """,
                    (row["candidate_id"], run_id),
                ).fetchone()
                hint_outcome = str(hint_event["outcome"] or "").strip().lower() if hint_event else ""
                if hint_outcome in {"helped_success", "accepted", "helped"}:
                    terminal = "helped"
                elif hint_outcome in {"contradicted", "conflict"}:
                    terminal = "conflict"
                elif hint_outcome in {"caused_failure", "failed"}:
                    terminal = "failed"
                elif normalized_run_status in {"failed", "cancelled", "abandoned"}:
                    terminal = "failed"
                else:
                    terminal = "ignored"
                metadata.update(
                    {
                        "terminalFinalizedAt": now,
                        "terminalRunStatus": normalized_run_status,
                        "terminalHintOutcome": hint_outcome or None,
                    }
                )
                state_payload = {
                    "id": str(row["id"]),
                    "candidateId": str(row["candidate_id"] or ""),
                    "state": terminal,
                    "currentStepIndex": int(row["current_step_index"] or 0),
                    "lastEventTopic": row["last_event_topic"],
                    "outcome": terminal,
                    "expiresAt": None,
                    "finalizedAt": now,
                    "updatedAt": now,
                    "metadata": metadata,
                }
                conn.execute(
                    """
                    UPDATE memory_workflow_guide_states
                    SET state = ?, outcome = ?, expires_at = NULL, finalized_at = ?,
                        metadata_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (terminal, terminal, now, _json_dump(metadata), now, row["id"]),
                )
                if hint_event:
                    conn.execute(
                        "UPDATE memory_workflow_hint_events SET outcome = ?, updated_at = ? WHERE id = ?",
                        (terminal, now, hint_event["id"]),
                    )
                candidate = conn.execute(
                    "SELECT metadata_json, maturity_score, negative_feedback_count, status FROM memory_workflow_candidates WHERE id = ?",
                    (row["candidate_id"],),
                ).fetchone()
                if candidate:
                    candidate_metadata = _json_load(candidate["metadata_json"], {})
                    counts = dict(candidate_metadata.get("terminalGuideOutcomeCounts") or {})
                    counts[terminal] = int(counts.get(terminal) or 0) + 1
                    candidate_metadata["terminalGuideOutcomeCounts"] = counts
                    candidate_metadata["lastTerminalGuideOutcomeAt"] = now
                    maturity = float(candidate["maturity_score"] or 0.0)
                    negative = int(candidate["negative_feedback_count"] or 0)
                    status = str(candidate["status"] or "candidate")
                    if terminal == "helped":
                        maturity = min(1.0, maturity + 0.08)
                    elif terminal == "ignored":
                        maturity = max(0.0, maturity - 0.05)
                    elif terminal == "conflict":
                        maturity = max(0.0, maturity - 0.15)
                        negative += 1
                        status = "quarantine"
                    else:
                        maturity = max(0.0, maturity - 0.08)
                    conn.execute(
                        """
                        UPDATE memory_workflow_candidates
                        SET guide_state_json = ?, last_hint_outcome = ?, metadata_json = ?,
                            maturity_score = ?, negative_feedback_count = ?, status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            _json_dump(state_payload),
                            terminal,
                            _json_dump(candidate_metadata),
                            maturity,
                            negative,
                            status,
                            now,
                            row["candidate_id"],
                        ),
                    )
                finalized.append({"guideStateId": str(row["id"]), "candidateId": str(row["candidate_id"] or ""), "outcome": terminal})
            conn.commit()
        return {"runId": run_id, "runStatus": normalized_run_status, "finalizedCount": len(finalized), "items": finalized}

    def reconcile_terminal_guides(self, *, limit: int = 500) -> Dict[str, Any]:
        """Close historical/current guides whose owning run is already terminal."""
        effective_limit = max(1, min(int(limit or 500), 2000))
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT guide.session_id, guide.run_id, run.status
                FROM memory_workflow_guide_states guide
                JOIN run_records run ON run.id = guide.run_id
                WHERE guide.is_current = 1
                  AND guide.finalized_at IS NULL
                  AND run.status IN ('completed', 'failed', 'cancelled', 'abandoned')
                ORDER BY guide.updated_at ASC, guide.run_id ASC
                LIMIT ?
                """,
                (effective_limit,),
            ).fetchall()
        finalized_count = 0
        runs: List[Dict[str, Any]] = []
        for row in rows:
            result = self.finalize_guides_for_run(
                session_id=str(row["session_id"] or ""),
                run_id=str(row["run_id"] or ""),
                run_status=str(row["status"] or ""),
            )
            finalized_count += int(result.get("finalizedCount") or 0)
            runs.append(
                {
                    "runId": str(row["run_id"] or ""),
                    "status": str(row["status"] or ""),
                    "finalizedCount": int(result.get("finalizedCount") or 0),
                }
            )
        return {"runCount": len(runs), "finalizedCount": finalized_count, "runs": runs[:50]}

    def maintenance_retention(self, *, dry_run: bool = False, batch_limit: int = 500) -> Dict[str, Any]:
        cfg = workflow_memory_config()
        retention = dict(cfg.get("retention") or {})
        limit = max(1, min(int(batch_limit or 500), 2000))
        now = datetime.now(timezone.utc)
        cutoffs = {
            "episodes": (now - timedelta(days=int(retention.get("episodeDays") or 365))).isoformat().replace("+00:00", "Z"),
            "hints": (now - timedelta(days=int(retention.get("hintDays") or 180))).isoformat().replace("+00:00", "Z"),
            "guides": (now - timedelta(days=int(retention.get("guideDays") or 365))).isoformat().replace("+00:00", "Z"),
            "proofs": (now - timedelta(days=int(retention.get("engineeringProofDays") or 730))).isoformat().replace("+00:00", "Z"),
        }
        expired = self.expire_pending_guides(limit=limit, dry_run=dry_run)
        with db.get_connection() as conn:
            candidate_rows = conn.execute(
                "SELECT source_episode_ids_json, proof_entry_ids_json FROM memory_workflow_candidates"
            ).fetchall()
            protected_episodes: set[str] = set()
            protected_proofs: set[str] = set()
            for candidate in candidate_rows:
                protected_episodes.update(str(item) for item in _json_load(candidate["source_episode_ids_json"], []) if str(item).strip())
                protected_proofs.update(str(item) for item in _json_load(candidate["proof_entry_ids_json"], []) if str(item).strip())

            episode_rows = conn.execute(
                "SELECT id FROM memory_workflow_episodes WHERE datetime(created_at) < datetime(?) ORDER BY created_at ASC LIMIT ?",
                (cutoffs["episodes"], limit * 4),
            ).fetchall()
            episode_ids = [str(row["id"]) for row in episode_rows if str(row["id"]) not in protected_episodes][:limit]
            hint_rows = conn.execute(
                """
                SELECT ranked.id FROM (
                    SELECT id, candidate_id, created_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY COALESCE(candidate_id, ''), COALESCE(run_id, ''),
                                            COALESCE(session_id, ''), COALESCE(query, ''), COALESCE(outcome, '')
                               ORDER BY updated_at DESC, created_at DESC, id DESC
                           ) AS rank_no
                    FROM memory_workflow_hint_events
                ) ranked
                LEFT JOIN memory_workflow_candidates candidate ON candidate.id = ranked.candidate_id
                WHERE rank_no > 1
                   OR (datetime(ranked.created_at) < datetime(?) AND candidate.id IS NULL)
                ORDER BY ranked.created_at ASC LIMIT ?
                """,
                (cutoffs["hints"], limit),
            ).fetchall()
            hint_ids = [str(row["id"]) for row in hint_rows]
            guide_rows = conn.execute(
                """
                SELECT id FROM memory_workflow_guide_states
                WHERE finalized_at IS NOT NULL AND datetime(finalized_at) < datetime(?)
                ORDER BY finalized_at ASC LIMIT ?
                """,
                (cutoffs["guides"], limit),
            ).fetchall()
            guide_ids = [str(row["id"]) for row in guide_rows]
            proof_rows = conn.execute(
                "SELECT id FROM engineering_proof_entries WHERE datetime(created_at) < datetime(?) ORDER BY created_at ASC LIMIT ?",
                (cutoffs["proofs"], limit * 4),
            ).fetchall()
            proof_ids = [str(row["id"]) for row in proof_rows if str(row["id"]) not in protected_proofs][:limit]
            if not dry_run:
                for table, ids in (
                    ("memory_workflow_episodes", episode_ids),
                    ("memory_workflow_hint_events", hint_ids),
                    ("memory_workflow_guide_states", guide_ids),
                    ("engineering_proof_entries", proof_ids),
                ):
                    if ids:
                        placeholders = ",".join("?" for _ in ids)
                        conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
                conn.commit()
        return {
            "dryRun": bool(dry_run),
            "expiredPendingGuideCount": int(expired.get("expiredCount") or 0),
            "retentionDays": retention,
            "candidateCounts": {
                "episodes": len(episode_ids),
                "hints": len(hint_ids),
                "guides": len(guide_ids),
                "engineeringProofs": len(proof_ids),
            },
        }

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
            recent_hint_rows = conn.execute(
                "SELECT metadata_json FROM memory_workflow_hint_events WHERE datetime(created_at) >= datetime('now', '-7 days')"
            ).fetchall()
        by_status = {str(row["status"] or "candidate"): int(row["count"] or 0) for row in rows}
        episode_total = int(episode_count["count"] or 0) if episode_count else 0
        hint_total = int(hint_count["count"] or 0) if hint_count else 0
        recent_hint_total = 0
        for row in recent_hint_rows:
            metadata = _json_load(row["metadata_json"], {})
            try:
                recent_hint_total += max(int(metadata.get("deliveryCount") or 1), 1)
            except (TypeError, ValueError):
                recent_hint_total += 1
        return {
            "enabled": bool(cfg.get("enabled")),
            "hintInjectionEnabled": bool(cfg.get("hintInjectionEnabled")),
            "candidateCount": sum(by_status.values()),
            "episodeCount": episode_total,
            "hintEventCount": hint_total,
            "hintDeliveryCount7d": recent_hint_total,
            "reusableCandidateCount": int(by_status.get("active_hint") or 0),
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

    def match_hints(
        self,
        *,
        query: str,
        scope_chain: Optional[List[str]] = None,
        limit: int = 2,
        engineering_active: bool = False,
    ) -> List[Dict[str, Any]]:
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
            workflow_class = str(item.get("workflowClass") or item.get("workflow_class") or "general").strip() or "general"
            engineering_cfg = cfg.get("engineering") if isinstance(cfg.get("engineering"), dict) else {}
            if workflow_class == "engineering" and bool(engineering_cfg.get("requireEngineeringModeForInjection", True)) and not engineering_active:
                continue
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
            if engineering_active and workflow_class == "engineering":
                score += 1.2
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
        recent_events_for_context = self._recent_runtime_events(session_id=session_id, run_id=run_id)
        engineering_active = self._engineering_active_from_runtime_events(recent_events_for_context)
        hints = self.match_hints(
            query=query,
            scope_chain=scope_chain,
            limit=match_limit,
            engineering_active=engineering_active,
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
            recent_events = recent_events_for_context
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
                "adaptation": "Keep the goal and verification intent, but adjust concrete actions if this task differs from the learned workflow.",
                "deliveryMode": "direct_guide",
            }
            lines.append(f"- Workflow: {item.get('task_family') or item.get('id')} (confidence {float(item.get('confidence') or 0):.2f})")
            if diagnostics.get("matchedReasons"):
                lines.append(f"  Applies because: {'; '.join(str(reason)[:80] for reason in diagnostics.get('matchedReasons')[:3])}")
            if golden:
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
        cfg = workflow_memory_config()
        page_size = int((cfg.get("retention") or {}).get("maintenancePageSize") or 200)
        cursor_state = self.maintenance_cursor("workflow_candidates")
        cursor_after = str(cursor_state.get("cursor_value") or "")
        candidates = self.list_candidates(limit=page_size, cursor_after=cursor_after or None, order="id_asc")
        wrapped = False
        if not candidates and cursor_after:
            candidates = self.list_candidates(limit=page_size, order="id_asc")
            cursor_after = ""
            wrapped = True
        updated = 0
        quarantined = 0
        activated = 0
        merge_suggestions = 0
        budget_stopped = False

        def _candidate_merge_key(item: Dict[str, Any]) -> str:
            identity = {
                "scope": _norm_text(item.get("scope") or "global").lower(),
                "workflowClass": _norm_text(item.get("workflowClass") or item.get("workflow_class") or "general").lower(),
                "sourceRuntime": _norm_text(item.get("sourceRuntime") or item.get("source_runtime") or "").lower(),
                "taskFamily": _norm_text(item.get("task_family") or item.get("taskFamily") or "").lower(),
                "triggers": sorted(_norm_text(value).lower() for value in list(item.get("canonicalTriggerPatterns") or [])),
                "firstActions": sorted(_norm_text(value).lower() for value in list(item.get("firstActionTriggers") or [])),
                "steps": [_norm_text(value).lower() for value in list(item.get("goldenPathSteps") or [])],
                "verify": sorted(_norm_text(value).lower() for value in list(item.get("verificationSteps") or [])),
            }
            if not identity["taskFamily"] or not identity["steps"]:
                return ""
            return hashlib.sha256(_json_dump(identity).encode("utf-8")).hexdigest()

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in candidates:
            key = _candidate_merge_key(item)
            if key:
                grouped.setdefault(key, []).append(item)

        def _candidate_score(item: Dict[str, Any]) -> tuple[float, int, str, str]:
            try:
                maturity = float(item.get("maturity_score") or 0.0)
            except (TypeError, ValueError):
                maturity = 0.0
            try:
                successes = int(item.get("success_count") or 0)
            except (TypeError, ValueError):
                successes = 0
            return (-maturity, -successes, str(item.get("created_at") or ""), str(item.get("id") or ""))

        for group in grouped.values():
            if len(group) < 2:
                continue
            keeper = sorted(group, key=_candidate_score)[0]
            keeper_id = str(keeper.get("id") or "").strip()
            if not keeper_id:
                continue
            duplicate_ids = [
                str(item.get("id") or "").strip()
                for item in group
                if str(item.get("id") or "").strip() and str(item.get("id") or "").strip() != keeper_id
            ]
            if duplicate_ids:
                self.merge_candidates(keeper_id, duplicate_ids)
                merge_suggestions += len(duplicate_ids)
            if merge_suggestions >= 40:
                budget_stopped = True
                break
            if budget_stopped:
                break

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
        if len(candidates) < page_size:
            next_cursor = ""
            wrapped = True
        else:
            next_cursor = str(candidates[-1].get("id") or "")
        cursor_result = self.advance_maintenance_cursor(
            "workflow_candidates",
            cursor_value=next_cursor,
            batch_count=len(candidates),
            wrapped=wrapped,
        )
        terminal_guide_result = self.reconcile_terminal_guides(limit=page_size)
        retention_result = self.maintenance_retention(dry_run=False, batch_limit=page_size)
        return {
            "candidateCount": len(candidates),
            "updatedCount": updated,
            "activatedCount": activated,
            "quarantinedCount": quarantined,
            "mergeSuggestionCount": merge_suggestions,
            "mergedDuplicateCount": merge_suggestions,
            "budgetStopped": budget_stopped,
            "cursor": cursor_result,
            "terminalGuides": terminal_guide_result,
            "retention": retention_result,
        }

    def export_candidate(self, candidate: Dict[str, Any]) -> None:
        if not candidate:
            return
        candidate_id = str(candidate.get("id") or "candidate")
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", candidate_id)
        json_path, md_path = self._candidate_export_paths(candidate_id)
        json_path.parent.mkdir(parents=True, exist_ok=True)
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
