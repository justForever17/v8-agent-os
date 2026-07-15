from __future__ import annotations

import json
import re
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.task_boundary_resolver import attach_task_boundary_decision


_ASCII_WORD_RE = re.compile(r"^[a-z0-9_+-]+$", re.IGNORECASE)
_CJK_SINGLE_CHAR_RE = re.compile(r"^[\u3400-\u9fff\uf900-\ufaff]$")
_NEGATION_CLAUSE_SEPARATORS = "，。；;,.!?！？、\n\r"
_NEGATION_MARKERS = (
    "不要直接",
    "不要调用",
    "不调用",
    "不需要",
    "无需",
    "无须",
    "不必",
    "不要",
    "不得",
    "禁止",
    "不能",
    "别",
    "不",
    "without",
    "do not",
    "don't",
    "dont",
    "no need to",
    "not",
    "never",
)


def _lower_text(value: Any) -> str:
    return str(value or "").strip().lower()


_BASE_TERMS: dict[str, tuple[str, ...]] = {
    "code_media_framework": (
        "remotion",
        "manim",
        "ffmpeg",
        "ffprobe",
        "three.js",
        "threejs",
        "three js",
        "p5.js",
        "p5js",
        "processing",
        "canvas",
        "webgl",
        "react",
        "tsx",
        "typescript",
        "next.js",
        "vue",
        "svelte",
    ),
    "code_action": (
        "implement",
        "implementation",
        "code",
        "coding",
        "build",
        "write",
        "create component",
        "create app",
        "build app",
        "web app",
        "web application",
        "frontend",
        "front-end",
        "frontend ui",
        "script",
        "repo",
        "repository",
        "patch",
        "test",
        "debug",
        "实现",
        "写代码",
        "代码",
        "项目",
        "仓库",
        "创建文件",
        "新建文件",
        "写文件",
        "保存文件",
        "组件",
        "前端",
        "前端界面",
        "前端页面",
        "前端应用",
        "web应用",
        "web 应用",
        "网页应用",
        "应用界面",
        "脚本",
        "修复",
        "测试",
        "构建",
        "工程",
        "工程实现",
        "工程任务",
    ),
    "delegation_action": (
        "delegate",
        "delegation",
        "delegated",
        "subagent",
        "sub-agent",
        "sub agent",
        "agent swarm",
        "worker",
        "workerbrief",
        "workerbriefs",
        "child delegation",
        "child agent",
        "grandchild agent",
        "multi-agent",
        "multi agent",
        "runtime orchestration",
        "runtime coordination",
        "委派",
        "派发",
        "分派",
        "子代理",
        "子 agent",
        "子agent",
        "孙代理",
        "孙 agent",
        "孙agent",
        "多智能体",
        "多 agent",
        "多agent",
        "agent 蜂群",
        "子代理蜂群",
        "蜂群",
        "协作",
        "编排",
        "运行时编排",
        "runtime 编排",
        "主链调度",
        "调度",
        "分工",
    ),
    "media_output": (
        "image",
        "picture",
        "video",
        "movie",
        "audio",
        "voice",
        "music",
        "render",
        "poster",
        "shot",
        "storyboard",
        "图片",
        "图像",
        "视频",
        "音频",
        "语音",
        "音乐",
        "渲染",
        "海报",
        "镜头",
        "分镜",
    ),
    "media_provider": (
        "seedance",
        "seedream",
        "sora",
        "veo",
        "kling",
        "runway",
        "luma",
        "comfyui",
        "tts",
        "mureka",
        "suno",
        "psd",
        "photoshop",
        "layered asset",
        "layered assets",
        "layered psd",
        "alpha mask",
        "alpha channel",
        "chroma key",
        "chroma-key",
        "cutout",
        "background removal",
        "background remove",
        "可灵",
        "即梦",
        "豆包",
        "百炼",
        "火山",
        "psd分层",
        "psd 分层",
        "分层psd",
        "分层 PSD",
        "图层",
        "分层",
        "透明通道",
        "alpha通道",
        "alpha 通道",
        "抠图",
        "蒙版",
        "色键",
        "纯色背景",
    ),
    "writing_action": (
        "write",
        "writing",
        "docs",
        "document",
        "documentation",
        "handoff",
        "release note",
        "proposal",
        "summary",
        "article",
        "copy",
        "文档",
        "写作",
        "撰写",
        "总结",
        "交付",
        "说明",
        "公众号",
        "文章",
        "报告",
    ),
    "research_action": (
        "research",
        "search",
        "lookup",
        "look up",
        "find sources",
        "source-backed",
        "cite",
        "citation",
        "latest",
        "official docs",
        "api docs",
        "调研",
        "搜索",
        "检索",
        "查资料",
        "查一下",
        "最新",
        "官方文档",
        "引用",
        "来源",
        "出处",
        "联网",
    ),
}

_LEXICON_MAP_KEYS = (
    "querySynonyms",
    "artifactIntentSynonyms",
    "operationIntentSynonyms",
)

_TASK_SHAPE_ALIAS_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtimes"
    / "extensions"
    / "skills"
    / "lexicons"
    / "task_shape"
)

_NON_ACTION_CONTEXT_TERMS: dict[str, set[str]] = {
    "code_action": {"workspace", "工作区"},
}

_WRITING_SIMPLE_TERMS = (
    "写一段",
    "写个简短",
    "简单写",
    "帮我回复",
    "回复一下",
    "总结一下",
    "说明一下",
    "一段说明",
    "short reply",
    "brief reply",
    "summarize",
)

_WRITING_AMBIGUOUS_DELIVERABLE_TERMS = (
    "写一篇文档",
    "写一份文档",
    "写个文档",
    "写一篇方案",
    "写一份方案",
    "写个方案",
    "写一篇报告",
    "写一份报告",
    "写个报告",
    "写篇文章",
    "写一篇文章",
    "write a document",
    "write a proposal",
    "write a report",
    "write an article",
)

_WRITING_ARTIFACT_TERMS = (
    "保存",
    "写入",
    "生成文件",
    "导出",
    "放到",
    "修改 readme",
    "写到 docs",
    "docs/",
    "readme",
    ".md",
    ".markdown",
    ".docx",
    ".pdf",
    "save to",
    "write to",
    "export as",
)

_WRITING_SKILL_TERMS = (
    "skill",
    "技能",
)

_WRITING_SKILL_ARTIFACT_TERMS = (
    "造skill",
    "造 skill",
    "造人",
    "女娲",
    "蒸馏",
    "生成 skill",
    "生成skill",
    "创建 skill",
    "创建skill",
    "更新 skill",
    "更新skill",
    "写入 skill",
    "写入skill",
    "skill.md",
    ".agents",
    "references/research",
)

_WRITING_SKILL_DIRECT_USAGE_TERMS = (
    "回答",
    "回复",
    "安慰",
    "建议",
    "怎么看",
    "视角",
    "perspective",
    "answer",
    "reply",
    "respond",
    "advise",
)

_WRITING_SKILL_CURATOR_TERMS = (
    "skill 设计",
    "skill设计",
    "skill 审查",
    "skill审查",
    "skill 优化",
    "skill优化",
    "创建 skill",
    "创建skill",
    "更新 skill",
    "更新skill",
    "设计技能",
    "审查技能",
    "优化技能",
    "创建技能",
    "更新技能",
    "skill workflow",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    normalized = _lower_text(text)
    return any(_term_in_text(normalized, term) for term in terms if term)


def _extract_skill_name_hint(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    patterns = (
        r"(?:使用|用|按照|按)\s*[`\"'“”]?([A-Za-z0-9_.:-]+)[`\"'“”]?\s*(?:skill|技能)",
        r"(?:skill|技能)\s*[`\"'“”]?([A-Za-z0-9_.:-]+)[`\"'“”]?",
        r"\$([A-Za-z0-9_.:-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return str(match.group(1) or "").strip()
    return ""


def _looks_like_skill_artifact_creation(text: str) -> bool:
    normalized = _lower_text(text)
    if not normalized:
        return False
    if not _contains_any(normalized, _WRITING_SKILL_ARTIFACT_TERMS):
        return False
    # A common planning request is "use huashu-nuwa to output a plan, do not
    # create/write/save a skill".  Treat that as method usage instead of a file
    # artifact request unless an explicit target path or create/save verb remains.
    plan_only_markers = ("只输出计划", "只要计划", "输出执行计划", "不写文件", "不保存", "不创建")
    explicit_artifact_markers = (
        "生成 skill",
        "生成skill",
        "创建 skill",
        "创建skill",
        "写入",
        "保存",
        "skill.md",
        ".agents",
        "references/research",
    )
    if any(marker in normalized for marker in plan_only_markers) and not _contains_any(normalized, explicit_artifact_markers):
        return False
    return True


def _classify_writing_route(
    *,
    text: str,
    writing_actions: list[str],
    research_actions: list[str],
    code_actions: list[str],
    delegation_actions: list[str],
) -> dict[str, Any]:
    """Return non-authoritative writing routing metadata for Supervisor use."""

    has_skill = _contains_any(text, _WRITING_SKILL_TERMS)
    normalized_text = _lower_text(text)
    has_skill_writing_verb = has_skill and (
        any(_term_in_text(normalized_text, term) for term in ("write", "draft", "撰写"))
        or any(True for _ in _iter_non_negated_term_matches(normalized_text, "写"))
    )
    has_writing = bool(writing_actions) or _contains_any(text, _WRITING_AMBIGUOUS_DELIVERABLE_TERMS) or has_skill_writing_verb
    has_artifact = _contains_any(text, _WRITING_ARTIFACT_TERMS)
    has_source = bool(research_actions)
    has_skill_artifact_intent = has_skill and _looks_like_skill_artifact_creation(text)
    if not any((has_writing, has_skill, has_artifact)):
        return {}

    skill_name = _extract_skill_name_hint(text)
    is_skill_curator = has_skill and _contains_any(text, _WRITING_SKILL_CURATOR_TERMS)
    if is_skill_curator or has_skill_artifact_intent:
        return {
            "present": True,
            "mode": "skill_subagent",
            "reason": "skill_design_or_artifact_creation_requires_curator",
            "needsClarification": False,
            "requiresResearch": bool(has_source or has_skill_artifact_intent),
            "requiresArtifact": bool(has_skill_artifact_intent),
            "requiresSkillExecution": True,
            "recommendedFamily": "engineering" if has_skill_artifact_intent else "writing",
            "preferredAgentId": "skill-workflow-curator",
            "skillName": skill_name,
            "firstActionTool": "fetch_skill_instructions",
            "allowCreateSubagentOnMismatch": False,
        }

    if has_skill and not has_source and not has_artifact and _contains_any(text, _WRITING_SKILL_DIRECT_USAGE_TERMS):
        return {
            "present": True,
            "mode": "direct_supervisor",
            "reason": "existing_skill_direct_answer_or_perspective_usage",
            "needsClarification": False,
            "requiresResearch": False,
            "requiresArtifact": False,
            "requiresSkillExecution": True,
            "recommendedFamily": "",
            "preferredAgentId": "",
            "skillName": skill_name,
            "firstActionTool": "fetch_skill_instructions",
            "allowCreateSubagentOnMismatch": False,
        }

    if has_skill and has_writing:
        return {
            "present": True,
            "mode": "skill_subagent",
            "reason": "named_or_implied_skill_should_be_executed_by_writing_subagent",
            "needsClarification": False,
            "requiresResearch": has_source,
            "requiresArtifact": has_artifact,
            "requiresSkillExecution": True,
            "recommendedFamily": "writing",
            "preferredAgentId": "",
            "skillName": skill_name,
            "firstActionTool": "fetch_skill_instructions",
            "allowCreateSubagentOnMismatch": True,
        }

    if has_artifact and has_writing:
        return {
            "present": True,
            "mode": "artifact_runtime",
            "reason": "writing_requires_file_or_repository_side_effect",
            "needsClarification": False,
            "requiresResearch": has_source,
            "requiresArtifact": True,
            "requiresSkillExecution": False,
            "recommendedFamily": "engineering",
            "preferredAgentId": "",
            "skillName": "",
            "firstActionTool": "",
            "allowCreateSubagentOnMismatch": False,
        }

    if has_source and has_writing:
        return {
            "present": True,
            "mode": "research_then_write",
            "reason": "source_backed_writing_requires_research_evidence_first",
            "needsClarification": False,
            "requiresResearch": True,
            "requiresArtifact": False,
            "requiresSkillExecution": False,
            "recommendedFamily": "writing",
            "preferredAgentId": "",
            "skillName": "",
            "firstActionTool": "",
            "allowCreateSubagentOnMismatch": False,
        }

    if has_writing and _contains_any(text, _WRITING_SIMPLE_TERMS):
        return {
            "present": True,
            "mode": "direct_supervisor",
            "reason": "simple_bounded_text_generation",
            "needsClarification": False,
            "requiresResearch": False,
            "requiresArtifact": False,
            "requiresSkillExecution": False,
            "recommendedFamily": "",
            "preferredAgentId": "",
            "skillName": "",
            "firstActionTool": "",
            "allowCreateSubagentOnMismatch": False,
        }

    if has_writing and _contains_any(text, _WRITING_AMBIGUOUS_DELIVERABLE_TERMS):
        return {
            "present": True,
            "mode": "ask_user_clarify",
            "reason": "ambiguous_writing_deliverable_needs_body_research_or_file_choice",
            "needsClarification": True,
            "clarificationOptions": ["direct_body", "research_backed", "save_as_file"],
            "requiresResearch": False,
            "requiresArtifact": False,
            "requiresSkillExecution": False,
            "recommendedFamily": "",
            "preferredAgentId": "",
            "skillName": "",
            "firstActionTool": "",
            "allowCreateSubagentOnMismatch": False,
        }

    if has_writing and (delegation_actions or "审稿" in text or "独立审" in text or "复核" in text):
        return {
            "present": True,
            "mode": "writing_subagent",
            "reason": "writing_needs_independent_review_or_specialist_execution",
            "needsClarification": False,
            "requiresResearch": False,
            "requiresArtifact": False,
            "requiresSkillExecution": False,
            "recommendedFamily": "writing",
            "preferredAgentId": "",
            "skillName": "",
            "firstActionTool": "",
            "allowCreateSubagentOnMismatch": True,
        }

    return {
        "present": True,
        "mode": "direct_supervisor",
        "reason": "named_skill_can_be_used_directly_by_supervisor" if has_skill else "plain_writing_can_remain_with_supervisor",
        "needsClarification": False,
        "requiresResearch": False,
        "requiresArtifact": False,
        "requiresSkillExecution": bool(has_skill),
        "recommendedFamily": "",
        "preferredAgentId": "",
        "skillName": skill_name,
        "firstActionTool": "fetch_skill_instructions" if has_skill else "",
        "allowCreateSubagentOnMismatch": False,
    }


def classify_task_shape(
    user_query: str,
    *,
    workspace_descriptor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a conservative, non-authoritative task shape hint.

    The classifier may recommend an auto reveal target, but it never grants
    runtime tools and never dispatches subagents.  The central rule is
    execution method before final output modality: "make a video with Remotion"
    is code work that produces media, not a Creative Media provider job.
    """

    text = _lower_text(user_query)
    # Workspace descriptors contain paths/scopes such as "workspace:main" for
    # almost every request.  They are execution context, not user intent; using
    # them for keyword matching made ordinary context questions look like
    # engineering work and blocked memory/context flows.
    combined = text
    term_sets = _task_shape_term_sets()

    code_frameworks = _find_terms(combined, term_sets["code_media_framework"])
    code_actions = _find_terms(combined, term_sets["code_action"])
    media_outputs = _find_terms(combined, term_sets["media_output"])
    media_providers = _find_terms(combined, term_sets["media_provider"])
    writing_actions = _find_terms(combined, term_sets["writing_action"])
    research_actions = _find_terms(combined, term_sets["research_action"])
    delegation_actions = _find_terms(combined, term_sets["delegation_action"])
    if delegation_actions and _looks_like_delegation_claim_safety_eval(combined):
        delegation_actions = []
    research_project_build_signal = _detect_research_project_build_signal(combined)
    explicit_writing_actions = [
        item
        for item in writing_actions
        if item not in {"docs", "document", "documentation", "文档", "说明"}
    ]
    writing_route = _classify_writing_route(
        text=combined,
        writing_actions=writing_actions,
        research_actions=research_actions,
        code_actions=code_actions,
        delegation_actions=delegation_actions,
    )

    primary = "general_chat"
    secondary: list[str] = []
    confidence = 0.35
    reason = "no_strong_shape_signal"
    suggested_families: list[str] = []
    optional_grants: list[str] = []
    signals: list[str] = []
    ambiguity_flags: list[str] = []
    family_scores: dict[str, float] = {
        "engineering": 0.0,
        "creative_media": 0.0,
        "research": 0.0,
        "writing": 0.0,
    }

    multi_runtime_orchestration = bool(delegation_actions and (research_actions or code_actions or "工程" in combined))

    if code_frameworks:
        primary = "project_coding"
        confidence = 0.93
        reason = "execution_method_code_framework_over_output_modality"
        family_scores["engineering"] = 0.95
        suggested_families = ["engineering"]
        signals.extend(f"code_framework:{item}" for item in code_frameworks[:6])
        if media_outputs or media_providers:
            secondary.append("creative_media")
            suggested_families.append("creative_media")
            family_scores["creative_media"] = 0.42 if media_outputs else 0.5
            signals.extend(f"media_secondary:{item}" for item in (media_outputs + media_providers)[:6])
        if research_actions:
            secondary.append("research")
            suggested_families.append("research")
            optional_grants.append("research.core")
            family_scores["research"] = 0.48
            signals.extend(f"research_secondary:{item}" for item in research_actions[:4])
        if delegation_actions:
            secondary.append("delegation")
            optional_grants.append("delegation.recursive")
            signals.extend(f"delegation_secondary:{item}" for item in delegation_actions[:4])
    elif media_providers:
        primary = "creative_media"
        confidence = 0.92
        reason = "provider_or_media_generation_intent"
        family_scores["creative_media"] = 0.94
        suggested_families = ["creative_media"]
        optional_grants = ["creative_media.core"]
        signals.extend(f"media_provider:{item}" for item in media_providers[:6])
        if research_actions:
            secondary.append("research")
            suggested_families.append("research")
            optional_grants.append("research.core")
            family_scores["research"] = 0.46
            signals.extend(f"research_secondary:{item}" for item in research_actions[:4])
        if code_actions:
            secondary.append("project_coding")
            suggested_families.append("engineering")
            family_scores["engineering"] = 0.58
            signals.extend(f"code_secondary:{item}" for item in code_actions[:4])
            ambiguity_flags.append("provider_generation_with_code_action")
        if delegation_actions:
            secondary.append("delegation")
            optional_grants.append("delegation.recursive")
            signals.extend(f"delegation_secondary:{item}" for item in delegation_actions[:4])
    elif code_actions or multi_runtime_orchestration:
        primary = "project_coding"
        confidence = 0.9 if multi_runtime_orchestration else 0.74
        reason = "multi_runtime_orchestration_terms" if multi_runtime_orchestration else "engineering_action_terms"
        family_scores["engineering"] = 0.9 if multi_runtime_orchestration else 0.74
        suggested_families = ["engineering"]
        signals.extend(f"code_action:{item}" for item in code_actions[:6])
        if multi_runtime_orchestration:
            signals.extend(f"delegation_action:{item}" for item in delegation_actions[:6])
        if media_outputs:
            secondary.append("creative_media")
            family_scores["creative_media"] = 0.38
            signals.extend(f"media_output:{item}" for item in media_outputs[:4])
        if research_actions:
            secondary.append("research")
            suggested_families.append("research")
            optional_grants.append("research.core")
            family_scores["research"] = 0.45
            signals.extend(f"research_secondary:{item}" for item in research_actions[:4])
        if delegation_actions:
            secondary.append("delegation")
            optional_grants.append("delegation.recursive")
    elif writing_actions and (not research_actions or explicit_writing_actions):
        primary = "writing"
        confidence = 0.7
        reason = "writing_or_document_terms"
        family_scores["writing"] = 0.7
        suggested_families = ["writing"]
        signals.extend(f"writing_action:{item}" for item in writing_actions[:6])
        if research_actions:
            secondary.append("research")
            suggested_families.append("research")
            optional_grants.append("research.core")
            family_scores["research"] = 0.48
            signals.extend(f"research_secondary:{item}" for item in research_actions[:4])
        if delegation_actions:
            secondary.append("delegation")
            optional_grants.append("delegation.recursive")
            signals.extend(f"delegation_secondary:{item}" for item in delegation_actions[:4])
    elif writing_route.get("present") and (writing_route.get("requiresSkillExecution") or writing_route.get("requiresArtifact")):
        primary = "writing"
        confidence = 0.78
        reason = str(writing_route.get("reason") or "writing_route_terms")
        family_scores["writing"] = 0.78
        recommended_family = str(writing_route.get("recommendedFamily") or "writing").strip()
        suggested_families = [recommended_family] if recommended_family else ["writing"]
        if writing_route.get("requiresResearch"):
            secondary.append("research")
            suggested_families.append("research")
            optional_grants.append("research.core")
            family_scores["research"] = 0.56
            signals.extend(f"research_secondary:{item}" for item in research_actions[:4])
        if writing_route.get("requiresArtifact"):
            secondary.append("project_coding")
            suggested_families.append("engineering")
            family_scores["engineering"] = 0.62
        if writing_route.get("mode") in {"skill_subagent", "writing_subagent"}:
            secondary.append("delegation")
            optional_grants.append("delegation.recursive")
        signals.append(f"writing_route:{writing_route.get('mode') or 'unknown'}")
    elif research_actions and research_project_build_signal:
        primary = "project_coding"
        confidence = 0.86
        reason = "research_plus_project_build_intent"
        family_scores["engineering"] = 0.86
        family_scores["research"] = 0.56
        suggested_families = ["engineering", "research"]
        optional_grants = ["research.core"]
        secondary.append("research")
        signals.extend(f"research_action:{item}" for item in research_actions[:4])
        signals.extend(research_project_build_signal[:6])
        if delegation_actions:
            secondary.append("delegation")
            optional_grants.append("delegation.recursive")
            signals.extend(f"delegation_secondary:{item}" for item in delegation_actions[:4])
    elif research_actions:
        if delegation_actions:
            primary = "project_coding"
            confidence = 0.9
            reason = "research_plus_delegation_orchestration_terms"
            family_scores["engineering"] = 0.82
            family_scores["research"] = 0.62
            suggested_families = ["engineering", "research"]
            optional_grants = ["research.core", "delegation.recursive"]
            secondary.extend(["research", "delegation"])
            signals.extend(f"research_action:{item}" for item in research_actions[:4])
            signals.extend(f"delegation_action:{item}" for item in delegation_actions[:6])
        else:
            primary = "research"
            confidence = 0.91
            reason = "research_or_current_source_terms"
            family_scores["research"] = 0.91
            suggested_families = ["research"]
            optional_grants = ["research.core"]
            signals.extend(f"research_action:{item}" for item in research_actions[:8])
    elif writing_route.get("present"):
        primary = "writing"
        confidence = 0.72
        reason = str(writing_route.get("reason") or "writing_route_terms")
        family_scores["writing"] = 0.72
        recommended_family = str(writing_route.get("recommendedFamily") or "writing").strip()
        suggested_families = [recommended_family] if recommended_family else ["writing"]
        if writing_route.get("requiresResearch"):
            secondary.append("research")
            suggested_families.append("research")
            optional_grants.append("research.core")
            family_scores["research"] = 0.48
        if writing_route.get("requiresArtifact"):
            secondary.append("project_coding")
            suggested_families.append("engineering")
            family_scores["engineering"] = 0.58
        if writing_route.get("mode") in {"skill_subagent", "writing_subagent"}:
            secondary.append("delegation")
            optional_grants.append("delegation.recursive")
        signals.append(f"writing_route:{writing_route.get('mode') or 'unknown'}")
    elif media_outputs:
        primary = "creative_media"
        confidence = 0.62
        reason = "media_output_terms_without_code_method_or_provider"
        family_scores["creative_media"] = 0.62
        suggested_families = ["creative_media"]
        optional_grants = ["creative_media.core"]
        ambiguity_flags.append("output_modality_only")
        signals.extend(f"media_output:{item}" for item in media_outputs[:6])
        if research_actions:
            secondary.append("research")
            suggested_families.append("research")
            optional_grants.append("research.core")
            family_scores["research"] = 0.44
            signals.extend(f"research_secondary:{item}" for item in research_actions[:4])
        if delegation_actions:
            secondary.append("delegation")
            optional_grants.append("delegation.recursive")
            signals.extend(f"delegation_secondary:{item}" for item in delegation_actions[:4])

    if primary not in secondary:
        secondary = [item for item in secondary if item != primary]
    suggested_families = _unique_preserve_order(suggested_families)
    optional_grants = _unique_preserve_order(optional_grants)

    family_scores = {
        family: round(score, 2)
        for family, score in sorted(family_scores.items(), key=lambda item: item[1], reverse=True)
        if score > 0
    }
    top_family, score_margin = _top_family_and_margin(family_scores)
    if score_margin < 0.15 and len(family_scores) > 1:
        ambiguity_flags.append("close_family_scores")

    auto_reveal = _build_auto_reveal_recommendation(
        top_family=top_family,
        confidence=confidence,
        score_margin=score_margin,
        ambiguity_flags=ambiguity_flags,
    )

    result = {
        "primaryTaskShape": primary,
        "secondaryTaskShapes": secondary[:4],
        "confidence": round(confidence, 2),
        "reason": reason,
        "suggestedFamilies": suggested_families[:6],
        "optionalRuntimeGrants": optional_grants[:6],
        "familyScores": family_scores,
        "topFamily": top_family,
        "scoreMargin": round(score_margin, 2),
        "ambiguityFlags": _unique_preserve_order(ambiguity_flags),
        "autoRevealRecommendation": auto_reveal,
        "signals": signals[:12],
        "writingRoute": writing_route,
        "lexiconSignature": _lexicon_signature(),
        "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant",
    }
    return attach_task_boundary_decision(result, user_query=user_query)


def render_task_shape_hint(hint: dict[str, Any] | None) -> str:
    if not isinstance(hint, dict) or not hint:
        return ""
    primary = str(hint.get("primaryTaskShape") or "unknown").strip() or "unknown"
    confidence = hint.get("confidence")
    try:
        confidence_text = f"{float(confidence):.2f}"
    except (TypeError, ValueError):
        confidence_text = "n/a"
    secondary = ", ".join(_as_list(hint.get("secondaryTaskShapes"))) or "none"
    families = ", ".join(_as_list(hint.get("suggestedFamilies"))) or "none"
    grants = ", ".join(_as_list(hint.get("optionalRuntimeGrants"))) or "none"
    reason = str(hint.get("reason") or "").strip() or "unspecified"
    top_family = str(hint.get("topFamily") or "none").strip() or "none"
    margin = hint.get("scoreMargin")
    try:
        margin_text = f"{float(margin):.2f}"
    except (TypeError, ValueError):
        margin_text = "n/a"
    ambiguity = ", ".join(_as_list(hint.get("ambiguityFlags"))) or "none"
    auto_reveal = hint.get("autoRevealRecommendation") if isinstance(hint.get("autoRevealRecommendation"), dict) else {}
    auto_families = ", ".join(_as_list(auto_reveal.get("families") if isinstance(auto_reveal, dict) else None)) or "none"
    auto_status = "eligible" if bool(auto_reveal.get("eligible")) else "not_eligible"
    writing_route = hint.get("writingRoute") if isinstance(hint.get("writingRoute"), dict) else {}
    writing_route_line = ""
    if writing_route.get("present"):
        writing_route_line = (
            f"writingRoute={writing_route.get('mode') or 'unknown'}; "
            f"needsClarification={bool(writing_route.get('needsClarification'))}; "
            f"recommendedFamily={writing_route.get('recommendedFamily') or 'none'}; "
            f"reason={writing_route.get('reason') or 'unspecified'}\n"
        )
    return (
        "<task_shape>\n"
        f"primary={primary}; secondary={secondary}; confidence={confidence_text}; reason={reason}\n"
        f"suggestedFamilies={families}; optionalRuntimeGrants={grants}\n"
        f"topFamily={top_family}; scoreMargin={margin_text}; ambiguityFlags={ambiguity}\n"
        f"{writing_route_line}"
        f"autoReveal={auto_status}; autoRevealFamilies={auto_families}; source=task_shape_classifier\n"
        "policy=non-binding hint; high-confidence auto reveal may expose one subagent family, but never grants runtime tools.\n"
        "</task_shape>\n"
    )


@lru_cache(maxsize=1)
def _task_shape_term_sets() -> dict[str, tuple[str, ...]]:
    terms = {key: set(values) for key, values in _BASE_TERMS.items()}
    task_aliases = _load_task_shape_aliases()
    for key, values in task_aliases.items():
        terms.setdefault(key, set()).update(values)
    for key, values in _NON_ACTION_CONTEXT_TERMS.items():
        terms.setdefault(key, set()).difference_update(values)
    lexicon_maps = _load_extension_lexicon_maps()
    for key, values in list(terms.items()):
        if key in {"media_output"}:
            terms[key] = set(_expand_with_lexicon(values, lexicon_maps))
    return {key: tuple(sorted(values, key=lambda item: (len(item), item))) for key, values in terms.items()}


def _detect_research_project_build_signal(text: str) -> list[str]:
    normalized = _lower_text(text)
    if not normalized:
        return []
    action_terms = (
        "build",
        "create",
        "develop",
        "implement",
        "design",
        "make",
        "开发",
        "实现",
        "制作",
        "设计",
        "做一个",
        "做个",
        "做一款",
        "设计一个",
        "设计一款",
        "开发一个",
        "开发一款",
    )
    artifact_terms = (
        "web app",
        "web application",
        "frontend",
        "front-end",
        "ui",
        "game",
        "application",
        "project",
        "web应用",
        "web 应用",
        "网页应用",
        "前端",
        "前端界面",
        "前端页面",
        "动态ui",
        "动态 ui",
        "游戏",
        "应用",
        "项目",
    )
    action_hits = [term for term in action_terms if _term_in_text(normalized, term)]
    artifact_hits = [term for term in artifact_terms if _term_in_text(normalized, term)]
    if not action_hits or not artifact_hits:
        return []
    return [
        *(f"project_build_action:{item}" for item in action_hits[:3]),
        *(f"project_build_artifact:{item}" for item in artifact_hits[:3]),
    ]


def _looks_like_delegation_claim_safety_eval(text: str) -> bool:
    """Avoid treating anti-hallucination policy examples as real delegation work."""
    normalized = _lower_text(text)
    if not normalized:
        return False
    claim_terms = (
        "不要声称",
        "不要宣称",
        "不得声称",
        "不得宣称",
        "不要假装",
        "不要编造",
        "如果没有证据",
        "没有证据",
        "do not claim",
        "don't claim",
        "do not pretend",
        "hallucination",
    )
    delegation_markers = ("subagent", "sub-agent", "子代理", "子 agent", "子agent", "delegation")
    success_markers = ("成功", "完成", "succeeded", "successful", "completed")
    return (
        any(term in normalized for term in claim_terms)
        and any(term in normalized for term in delegation_markers)
        and any(term in normalized for term in success_markers)
    )


@lru_cache(maxsize=1)
def _lexicon_signature() -> str:
    try:
        from runtimes.extensions.skills.lexicons import get_extension_lexicon_registry

        snapshot = get_extension_lexicon_registry().ensure_fresh()
        signature = str(snapshot.get("signature") or "lexicon:empty")
    except Exception:
        signature = "lexicon:unavailable"
    alias_signature = _task_shape_alias_signature()
    return f"{signature}|task-shape:{alias_signature}"


def _load_extension_lexicon_maps() -> dict[str, dict[str, tuple[str, ...]]]:
    try:
        from runtimes.extensions.skills.lexicons import get_extension_lexicon_registry

        snapshot = get_extension_lexicon_registry().ensure_fresh()
    except Exception:
        return {}
    maps: dict[str, dict[str, tuple[str, ...]]] = {}
    for key in _LEXICON_MAP_KEYS:
        raw_map = snapshot.get(key)
        if not isinstance(raw_map, dict):
            continue
        maps[key] = {
            _lower_text(name): tuple(_lower_text(item) for item in list(values or []) if _lower_text(item))
            for name, values in raw_map.items()
            if _lower_text(name)
        }
    return maps


def _load_task_shape_aliases() -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    if not _TASK_SHAPE_ALIAS_DIR.exists():
        return aliases
    for path in sorted(_TASK_SHAPE_ALIAS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw_aliases = payload.get("taskShapeAliases")
        if not isinstance(raw_aliases, dict):
            continue
        for key, values in raw_aliases.items():
            if not isinstance(values, list):
                continue
            bucket = aliases.setdefault(str(key), set())
            for value in values:
                text = _lower_text(value)
                if text:
                    bucket.add(text)
    return aliases


def _task_shape_alias_signature() -> str:
    if not _TASK_SHAPE_ALIAS_DIR.exists():
        return "empty"
    manifest: list[tuple[str, int, int]] = []
    for path in sorted(_TASK_SHAPE_ALIAS_DIR.glob("*.json")):
        try:
            stat = path.stat()
        except OSError:
            continue
        manifest.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _expand_with_lexicon(seed_terms: set[str], lexicon_maps: dict[str, dict[str, tuple[str, ...]]]) -> set[str]:
    expanded = {_lower_text(item) for item in seed_terms if _lower_text(item)}
    for _ in range(2):
        before = len(expanded)
        for mapping in lexicon_maps.values():
            for key, values in mapping.items():
                value_set = set(values)
                if key in expanded or expanded.intersection(value_set):
                    expanded.add(key)
                    expanded.update(value_set)
        if len(expanded) == before:
            break
    return expanded


def _find_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for term in terms:
        if not term:
            continue
        if _term_in_text(text, term):
            matches.append(term)
    return matches[:24]


def _term_in_text(text: str, term: str) -> bool:
    lowered = term.lower()
    if not lowered:
        return False
    if _CJK_SINGLE_CHAR_RE.match(lowered):
        return False
    return any(True for _ in _iter_non_negated_term_matches(text, lowered))


def _iter_non_negated_term_matches(text: str, term: str):
    if not text or not term:
        return
    if term.isascii() and _ASCII_WORD_RE.match(term):
        pattern = re.compile(rf"(?<![a-z0-9_+-]){re.escape(term)}(?![a-z0-9_+-])", re.IGNORECASE)
        for match in pattern.finditer(text):
            if not _is_negated_match(text, match.start()):
                yield match
        return
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            break
        if not _is_negated_match(text, index):
            yield (index, index + len(term))
        start = index + max(1, len(term))


def _is_negated_match(text: str, start_index: int) -> bool:
    left = text[max(0, start_index - 32) : start_index]
    last_separator = max((left.rfind(separator) for separator in _NEGATION_CLAUSE_SEPARATORS), default=-1)
    if last_separator >= 0:
        left = left[last_separator + 1 :]
    compact = re.sub(r"\s+", " ", left.strip().lower())
    if not compact:
        return False
    return any(marker in compact for marker in _NEGATION_MARKERS)


def _top_family_and_margin(scores: dict[str, float]) -> tuple[str, float]:
    if not scores:
        return "", 0.0
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_family, top_score = ordered[0]
    next_score = ordered[1][1] if len(ordered) > 1 else 0.0
    return top_family, max(0.0, top_score - next_score)


def _build_auto_reveal_recommendation(
    *,
    top_family: str,
    confidence: float,
    score_margin: float,
    ambiguity_flags: list[str],
) -> dict[str, Any]:
    eligible = bool(top_family) and confidence >= 0.9 and score_margin >= 0.15 and not ambiguity_flags
    reason = "high_confidence_single_family" if eligible else "below_threshold_or_ambiguous"
    return {
        "eligible": eligible,
        "families": [top_family] if eligible else [],
        "source": "task_shape_classifier",
        "reason": reason,
        "minConfidence": 0.9,
        "minScoreMargin": 0.15,
        "requireNoAmbiguity": True,
    }


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _unique_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result
