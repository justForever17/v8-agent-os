from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from core.database import db
from core.llm_factory import llm_factory
from core.llm_tree_prefilter import select_family_keys_with_llm
from core.model_control_plane import model_control_plane
from core.plugin_host.tool_exposure import expand_tool_family_seeds
from core.plugin_host.silk_codec import silk_toolchain_status
from core.skills_install_service import get_skill_dependency_policy
from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME
from erc.event_bus import event_bus
from erc.models import RuntimeSource
from erc.runtime_context import get_runtime_context
from erc.runtime_registry import runtime_registry
from runtimes.extensions.mcp.client import mcp_manager
from runtimes.extensions.skills.loader import SkillLoader


_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]{2,}")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "help",
    "please",
    "skill",
    "skills",
    "tool",
    "tools",
    "mcp",
    "服务",
    "工具",
    "一下",
    "一个",
    "一些",
    "我想",
    "想要",
    "给我",
    "请帮",
    "如何",
    "怎么",
    "请问",
    "帮忙",
    "想想",
    "使用",
    "帮我",
}
_SKILL_RERANK_POOL_FLOOR = 10
_MCP_RERANK_POOL_FLOOR = 12
_PLUGIN_HOST_RERANK_POOL_FLOOR = 12
_PLUGIN_HOST_BOUND_CAP = 24
_PLUGIN_HOST_LIVE_INVENTORY_SOURCES = {"gateway_rpc", "plugin_source_scan", "durable_cache"}
_PLUGIN_HOST_QUERY_HINTS = {
    "openclaw",
    "pluginhost",
    "plugin_host",
    "plugin-host",
    "plugin host",
    "channel",
    "channels",
    "bridge",
    "gateway",
    "feishu",
    "lark",
    "wechat",
    "weixin",
    "slack",
    "discord",
    "telegram",
    "line",
    "teams",
    "whatsapp",
    "飞书",
    "微信",
    "插件宿主",
    "桥接",
    "渠道",
}
_CROSS_RUNTIME_ESCAPE_TOKENS = {
    "blocker",
    "blocked",
    "blocking",
    "stale",
    "stuck",
    "retry",
    "fallback",
    "switch",
    "handoff",
    "delegate",
    "delegation",
    "parallel",
    "error",
    "errors",
    "failed",
    "failure",
    "cannot",
    "cant",
    "missing",
    "auth",
    "unauthorized",
    "permission",
    "权限",
    "授权",
    "失败",
    "错误",
    "卡住",
    "阻塞",
    "并发",
    "切换",
    "降级",
}
_EXTENSION_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "v8_agent_os_extensions_runtime_context",
    default={},
)
_EXTENSION_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "视频": ("video", "videos"),
    "生成": ("generate", "generation", "create", "creation"),
    "图像": ("image", "images", "picture", "pictures"),
    "图片": ("image", "images", "picture", "pictures"),
    "文档": ("doc", "docs", "documentation", "library"),
    "代码": ("code", "coding"),
    "邮件": ("mail", "email"),
    "头像": ("avatar", "avatars", "talking-head"),
    "音频": ("audio", "sound", "voice"),
    "语音": ("voice", "audio", "speech"),
    "设计": ("design", "designer"),
    "界面": ("ui", "interface"),
    "动画": ("animation", "animated"),
}
_QUERY_ARTIFACT_INTENT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "presentation": ("ppt", "pptx", "presentation", "slide", "slides", "deck", "幻灯片", "演示稿", "演示文稿"),
    "video": ("视频", "video", "videos", "video generation", "短片", "动画视频"),
    "image": ("图片", "图像", "image", "images", "picture", "poster", "illustration"),
    "document": ("文档", "document", "doc", "docx", "markdown", "md", "report", "文章"),
    "pdf": ("pdf",),
    "spreadsheet": ("excel", "xlsx", "xls", "csv", "spreadsheet", "表格", "表单"),
    "audio": ("音频", "语音", "audio", "voice", "speech"),
    "code": ("代码", "脚本", "code", "script", "scripts"),
    "skill": ("skill", "skills", "人物skill", "思维顾问", "persona"),
}
_QUERY_OPERATION_INTENT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "create": ("生成", "创建", "制作", "做", "写", "generate", "create", "build", "draft", "make"),
    "edit": ("编辑", "修改", "调整", "edit", "revise", "modify", "update"),
    "analyze": ("分析", "检查", "评估", "审阅", "analyze", "analysis", "review", "audit"),
    "convert": ("转换", "转成", "导出", "convert", "transform", "export"),
    "search": ("搜索", "检索", "查找", "查询", "search", "find", "lookup", "query"),
    "guide": ("教程", "指南", "最佳实践", "guide", "tutorial", "best practice"),
    "advise": ("建议", "视角", "顾问", "advise", "advisor", "perspective"),
}
_QUERY_PRIMARY_THEME_SYNONYMS: dict[str, tuple[str, ...]] = {
    "decision_quality": (
        "决策质量",
        "decision quality",
        "认知偏误",
        "bias",
        "biases",
        "判断力",
        "判断",
        "第一性原理",
        "first principles",
        "逆向思考",
        "inversion",
    ),
    "wealth_money": (
        "赚钱",
        "财富",
        "wealth",
        "money",
        "投资",
        "资本配置",
        "杠杆",
        "leverage",
        "specific knowledge",
        "特定知识",
    ),
    "startup_growth": (
        "增长",
        "growth",
        "创业",
        "startup",
        "商业化",
        "traction",
        "distribution",
        "用户增长",
    ),
    "product_strategy": (
        "产品战略",
        "product strategy",
        "产品",
        "product",
        "定位",
        "positioning",
        "roadmap",
        "成本结构",
        "cost structure",
        "垂直整合",
        "vertical integration",
    ),
    "engineering_ai": (
        "ai",
        "人工智能",
        "machine learning",
        "机器学习",
        "llm",
        "engineering",
        "software",
        "代码",
        "神经网络",
    ),
    "content_media": (
        "内容",
        "content",
        "视频增长",
        "thumbnail",
        "hook",
        "retention",
        "attention",
        "创作者",
        "creator",
        "youtube",
    ),
    "writing_communication": (
        "写作",
        "writing",
        "沟通",
        "communication",
        "storytelling",
        "copywriting",
        "表达",
    ),
    "organization_leadership": (
        "组织",
        "leadership",
        "组织效率",
        "管理",
        "management",
        "culture",
        "hiring",
        "组织设计",
        "人才密度",
    ),
    "career_learning": (
        "学习",
        "learning",
        "职业",
        "career",
        "education",
        "成长",
        "学习方法",
    ),
    "negotiation_persuasion": (
        "谈判",
        "negotiation",
        "说服",
        "persuasion",
        "influence",
        "激励结构",
        "incentive",
        "attention arbitrage",
        "注意力套利",
    ),
}
_QUERY_SECONDARY_THEME_SYNONYMS: dict[str, tuple[str, ...]] = {
    "first_principles": ("第一性原理", "first principles"),
    "cost_structure": ("成本结构", "cost structure", "idiot index", "白痴指数"),
    "inversion": ("逆向思考", "inversion"),
    "specific_knowledge": ("特定知识", "specific knowledge"),
    "creator_growth": ("creator growth", "内容增长", "retention", "thumbnail", "hook", "ctr"),
    "organizational_design": ("组织设计", "organizational design", "组织效率"),
    "attention_arbitrage": ("attention arbitrage", "注意力套利", "注意力经济"),
    "cognitive_bias": ("认知偏误", "bias", "biases", "lollapalooza"),
    "leverage": ("杠杆", "leverage"),
    "talent_density": ("人才密度", "talent density"),
}
_SECONDARY_THEME_PRIMARY_MAP: dict[str, tuple[str, ...]] = {
    "first_principles": ("decision_quality", "product_strategy"),
    "cost_structure": ("product_strategy", "wealth_money"),
    "inversion": ("decision_quality",),
    "specific_knowledge": ("wealth_money", "career_learning"),
    "creator_growth": ("content_media", "startup_growth"),
    "organizational_design": ("organization_leadership",),
    "attention_arbitrage": ("content_media", "wealth_money"),
    "cognitive_bias": ("decision_quality",),
    "leverage": ("wealth_money", "startup_growth"),
    "talent_density": ("organization_leadership",),
}
_THEME_HEAVY_CLASSES = {"advisor_or_perspective", "methodology_or_tutorial"}
_ARTIFACT_MISMATCH_GROUPS: dict[str, set[str]] = {
    "presentation": {"video", "image", "audio"},
    "video": {"presentation", "document", "pdf", "spreadsheet"},
    "image": {"presentation", "spreadsheet", "audio"},
    "document": {"video", "audio"},
    "pdf": {"video", "audio"},
    "spreadsheet": {"video", "image", "audio"},
    "audio": {"presentation", "spreadsheet", "document"},
}
_ARTIFACT_PROFILE_ANCHORS: dict[str, set[str]] = {
    "presentation": {"ppt", "pptx", ".ppt", ".pptx", "powerpoint"},
    "document": {"doc", "docx", ".doc", ".docx", "word"},
    "pdf": {"pdf", ".pdf"},
    "spreadsheet": {"xls", "xlsx", "csv", ".xls", ".xlsx", ".csv", "excel"},
    "video": {"video", "videos"},
    "image": {"image", "images", "poster", "illustration"},
    "audio": {"audio", "voice", "speech", "podcast"},
}


@dataclass(slots=True)
class ExtensionRouteBundle:
    prompt_addition: str
    filtered_tools: list[Any]
    selected_skill_names: list[str]
    selected_skill_ids: list[str]
    skill_root_descriptors: list[dict[str, Any]]
    exposed_mcp_tool_names: list[str]
    candidate_summary: dict[str, Any]


def _tokenize(text: str) -> list[str]:
    lowered = str(text or "").lower()
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(lowered):
        stripped = token.strip().lower()
        if len(stripped) <= 1 or stripped in _STOPWORDS:
            continue
        tokens.append(stripped)
    return tokens


def _unique_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        normalized = str(item or "").strip().lower()
        if not normalized or normalized in seen or normalized in _STOPWORDS:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _expand_query_token_variants(query_tokens: list[str]) -> list[str]:
    expanded: list[str] = list(query_tokens)
    for token in list(query_tokens):
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", token):
            if len(token) >= 4:
                expanded.append(token[:2])
                expanded.append(token[-2:])
            for index in range(0, len(token) - 1):
                expanded.append(token[index : index + 2])
    return _unique_preserve_order(expanded)


def _query_tokens_for_extensions(text: str) -> list[str]:
    expanded = _expand_query_token_variants(_tokenize(text))
    query_text = str(text or "").strip().lower()
    for token in list(expanded):
        expanded.extend(_EXTENSION_QUERY_SYNONYMS.get(token, ()))
    for hint, synonyms in _EXTENSION_QUERY_SYNONYMS.items():
        if hint in query_text:
            expanded.extend(synonyms)
    return _unique_preserve_order(expanded)


def _detect_query_intents(text: str, query_tokens: list[str]) -> dict[str, Any]:
    lowered = str(text or "").strip().lower()
    artifact_intents = [
        key
        for key, synonyms in _QUERY_ARTIFACT_INTENT_SYNONYMS.items()
        if any(str(synonym).lower() in lowered or str(synonym).lower() in query_tokens for synonym in synonyms)
    ]
    operation_intents = [
        key
        for key, synonyms in _QUERY_OPERATION_INTENT_SYNONYMS.items()
        if any(str(synonym).lower() in lowered or str(synonym).lower() in query_tokens for synonym in synonyms)
    ]
    primary_theme_intents = [
        key
        for key, synonyms in _QUERY_PRIMARY_THEME_SYNONYMS.items()
        if any(str(synonym).lower() in lowered or str(synonym).lower() in query_tokens for synonym in synonyms)
    ]
    secondary_theme_hints = [
        key
        for key, synonyms in _QUERY_SECONDARY_THEME_SYNONYMS.items()
        if any(str(synonym).lower() in lowered or str(synonym).lower() in query_tokens for synonym in synonyms)
    ]
    for tag in secondary_theme_hints:
        for primary_theme in _SECONDARY_THEME_PRIMARY_MAP.get(tag, ()):
            if primary_theme not in primary_theme_intents:
                primary_theme_intents.append(primary_theme)
    matched_terms = {
        str(synonym).lower()
        for key in artifact_intents
        for synonym in _QUERY_ARTIFACT_INTENT_SYNONYMS.get(key, ())
        if str(synonym).strip()
    }
    matched_terms.update(
        str(synonym).lower()
        for key in operation_intents
        for synonym in _QUERY_OPERATION_INTENT_SYNONYMS.get(key, ())
        if str(synonym).strip()
    )
    matched_terms.update(
        str(synonym).lower()
        for key in primary_theme_intents
        for synonym in _QUERY_PRIMARY_THEME_SYNONYMS.get(key, ())
        if str(synonym).strip()
    )
    matched_terms.update(
        str(synonym).lower()
        for key in secondary_theme_hints
        for synonym in _QUERY_SECONDARY_THEME_SYNONYMS.get(key, ())
        if str(synonym).strip()
    )
    topic_tokens = [token for token in query_tokens if token not in matched_terms]
    return {
        "artifactIntent": artifact_intents[0] if artifact_intents else None,
        "artifactIntents": artifact_intents,
        "operationIntent": operation_intents[0] if operation_intents else None,
        "operationIntents": operation_intents,
        "primaryThemeIntents": primary_theme_intents,
        "secondaryThemeHints": secondary_theme_hints,
        "topicTokens": topic_tokens,
    }


def _score_text(*, query_tokens: list[str], title: str, description: str) -> int:
    if not query_tokens:
        return 0

    title_tokens = _tokenize(title)
    description_tokens = _tokenize(description)
    title_set = set(title_tokens)
    description_set = set(description_tokens)

    score = 0
    for token in query_tokens:
        if token in title_set:
            score += 4
        if token in description_set:
            score += 2
        if token in str(title or "").lower():
            score += 2
        if token in str(description or "").lower():
            score += 1
    return score


def _normalize_hint_items(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[,\n/|]+", value)
    else:
        raw_items = list(value or [])
    return [
        str(item).strip()
        for item in raw_items
        if str(item).strip()
    ]


def _skill_recall_hints(skill: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for key in ("aliases", "triggers", "keywords", "tags"):
        hints.extend(_normalize_hint_items(skill.get(key)))
    return hints


def _normalize_profile_items(value: Any) -> list[str]:
    return [
        str(item).strip().lower()
        for item in list(value or [])
        if str(item).strip()
    ]


def _score_skill_entry(
    *,
    query_text: str,
    query_tokens: list[str],
    query_profile: dict[str, Any],
    skill: dict[str, Any],
) -> tuple[int, bool, bool]:
    def _profile_artifact_anchor_bonus(profile_payload: dict[str, Any], matched_artifacts: list[str]) -> int:
        evidence = profile_payload.get("evidenceSignals") or {}
        artifact_matches = evidence.get("artifactMatches") if isinstance(evidence, dict) else {}
        if not isinstance(artifact_matches, dict):
            return 0
        bonus = 0
        for artifact in matched_artifacts:
            terms = {
                str(term or "").strip().lower()
                for term in list(artifact_matches.get(artifact) or [])
                if str(term or "").strip()
            }
            anchors = _ARTIFACT_PROFILE_ANCHORS.get(artifact, set())
            if anchors.intersection(terms):
                bonus = max(bonus, 6)
            elif len(terms) >= 4:
                bonus = max(bonus, 2)
        return bonus

    name = str(skill.get("name") or skill.get("folder") or "").strip()
    folder = str(skill.get("folder") or "").strip()
    description = str(skill.get("description") or "").strip()
    normalized_query = str(query_text or "").strip().lower()
    score = _score_text(query_tokens=query_tokens, title=name or folder, description=description)
    has_query_signal = score > 0
    for candidate in (name, folder):
        normalized_candidate = str(candidate or "").strip().lower()
        if normalized_candidate and normalized_candidate in normalized_query:
            score += 12
            has_query_signal = True
    for hint in _skill_recall_hints(skill):
        normalized_hint = str(hint or "").strip().lower()
        if not normalized_hint:
            continue
        if normalized_hint in normalized_query:
            score += 10
            has_query_signal = True
        hint_score = _score_text(query_tokens=query_tokens, title=normalized_hint, description="")
        score += hint_score
        if hint_score > 0:
            has_query_signal = True

    profile = dict(skill.get("capabilityProfile") or {})
    theme_profile = dict(skill.get("themeProfile") or {})
    primary_artifact_types = _normalize_profile_items(profile.get("primaryArtifactTypes"))
    primary_operations = _normalize_profile_items(profile.get("primaryOperations"))
    secondary_artifact_hints = _normalize_profile_items(profile.get("secondaryArtifactHints"))
    secondary_operation_hints = _normalize_profile_items(profile.get("secondaryOperationHints"))
    skill_class = str(profile.get("skillClass") or "").strip().lower()
    confidence = float(profile.get("capabilityConfidence") or 0.0)
    primary_themes = _normalize_profile_items(theme_profile.get("primaryThemes"))
    secondary_theme_tags = _normalize_profile_items(theme_profile.get("secondaryThemeTags"))
    implied_primary_themes = _unique_preserve_order(
        implied_theme
        for tag in secondary_theme_tags
        for implied_theme in _SECONDARY_THEME_PRIMARY_MAP.get(tag, ())
    )
    theme_confidence = float(theme_profile.get("themeConfidence") or 0.0)

    artifact_intents = _normalize_profile_items(query_profile.get("artifactIntents"))
    operation_intents = _normalize_profile_items(query_profile.get("operationIntents"))
    primary_theme_intents = _normalize_profile_items(query_profile.get("primaryThemeIntents"))
    secondary_theme_hints = _normalize_profile_items(query_profile.get("secondaryThemeHints"))
    topic_tokens = list(query_profile.get("topicTokens") or [])

    artifact_match = False
    if artifact_intents:
        matched_artifacts = [item for item in artifact_intents if item in primary_artifact_types]
        if matched_artifacts:
            artifact_match = True
            has_query_signal = True
            score += 36 + (8 * len(matched_artifacts))
            score += _profile_artifact_anchor_bonus(profile, matched_artifacts)
        elif primary_artifact_types:
            mismatched = False
            for artifact_intent in artifact_intents:
                conflicting = _ARTIFACT_MISMATCH_GROUPS.get(artifact_intent, set())
                if conflicting.intersection(primary_artifact_types):
                    mismatched = True
                    break
            if mismatched:
                score -= 18

    operation_match = False
    if operation_intents:
        matched_operations = [item for item in operation_intents if item in primary_operations]
        if matched_operations:
            operation_match = True
            has_query_signal = True
            score += 14 + (4 * len(matched_operations))
        elif skill_class in {"advisor_or_perspective", "methodology_or_tutorial"} and "advise" in operation_intents:
            operation_match = True
            has_query_signal = True
            score += 8

    theme_match = False
    matched_primary_themes = [item for item in primary_theme_intents if item in primary_themes]
    matched_secondary_tags = [item for item in secondary_theme_hints if item in secondary_theme_tags]
    matched_implied_themes = [item for item in primary_theme_intents if item in implied_primary_themes]
    no_artifact_anchor = not artifact_intents
    if skill_class in _THEME_HEAVY_CLASSES:
        if matched_primary_themes:
            theme_match = True
            has_query_signal = True
            if no_artifact_anchor:
                score += 26 + (8 * len(matched_primary_themes)) + 12
            else:
                score += 6 + (2 * len(matched_primary_themes))
        elif matched_implied_themes:
            theme_match = True
            has_query_signal = True
            if no_artifact_anchor:
                score += 14 + (5 * len(matched_implied_themes)) + 6
            else:
                score += 4 + len(matched_implied_themes)
        if matched_secondary_tags:
            has_query_signal = True
            if no_artifact_anchor:
                score += 8 + (3 * len(matched_secondary_tags)) + 4
            else:
                score += 2 + len(matched_secondary_tags)
    else:
        if matched_primary_themes:
            has_query_signal = True
            score += 6 + (2 * len(matched_primary_themes))
        elif matched_implied_themes:
            has_query_signal = True
            score += 3 + len(matched_implied_themes)
        if matched_secondary_tags:
            has_query_signal = True
            score += 3 + len(matched_secondary_tags)

    if topic_tokens:
        topic_score = _score_text(
            query_tokens=topic_tokens,
            title=name or folder,
            description=" ".join(
                part
                for part in [
                    description,
                    " ".join(primary_artifact_types),
                    " ".join(primary_operations),
                    " ".join(secondary_artifact_hints),
                    " ".join(secondary_operation_hints),
                ]
                if str(part or "").strip()
            ),
        )
        score += min(topic_score, 18)
        if topic_score > 0:
            has_query_signal = True

    if not has_query_signal:
        return 0, artifact_match, operation_match

    confidence_bonus = int(round(confidence * 6))
    score += confidence_bonus
    score += int(round(theme_confidence * 4))

    if artifact_match and skill_class == "artifact_producer":
        score += 10
    if artifact_match and skill_class == "artifact_editor_or_analyzer":
        score += 6
    if artifact_intents and skill_class in {"advisor_or_perspective", "methodology_or_tutorial"} and not artifact_match:
        score -= 8
    if no_artifact_anchor and theme_match and skill_class in _THEME_HEAVY_CLASSES:
        score += 6

    return score, artifact_match, operation_match


def _score_mcp_server_entry(*, query_text: str, query_tokens: list[str], server_name: str, items: list[Any]) -> int:
    tool_names = [_tool_name(tool) for tool in items if _tool_name(tool)]
    tool_descriptions = [_tool_description(tool) for tool in items if _tool_description(tool)]
    server_description = " | ".join(
        part
        for part in [
            f"MCP server {server_name}",
            " ".join(tool_names),
            " ".join(tool_descriptions),
        ]
        if str(part or "").strip()
    )
    normalized_query = str(query_text or "").strip().lower()
    score = _score_text(query_tokens=query_tokens, title=server_name, description=server_description)
    if server_name.lower() in normalized_query:
        score += 10
    for tool_name in tool_names:
        if tool_name.lower() in normalized_query:
            score += 8
    return score


def _truncate(text: str, limit: int = 100) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _single_line_text(text: str) -> str:
    return " ".join(str(text or "").split())


def _tool_name(tool_ref: Any) -> str:
    return getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")).strip()


def _tool_description(tool_ref: Any) -> str:
    raw = getattr(tool_ref, "description", getattr(tool_ref, "__doc__", "")) or ""
    lines = str(raw).strip().splitlines()
    return lines[0] if lines else ""


def _is_mcp_tool(tool_ref: Any) -> bool:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return bool(metadata.get("server_name"))


def _is_plugin_host_tool(tool_ref: Any) -> bool:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return bool(metadata.get("pluginHost"))


def _build_skill_rerank_document(skill: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"skill: {str(skill.get('name') or skill.get('folder') or '').strip()}",
            f"description: {str(skill.get('description') or '').strip() or '暂无说明。'}",
            f"path: {str(skill.get('path') or '').strip()}",
        ]
    ).strip()


def _skill_entry_payload(skill: dict[str, Any]) -> dict[str, Any]:
    return {
        "skillId": str(skill.get("skillId") or "").strip(),
        "skillName": str(skill.get("skillName") or skill.get("name") or skill.get("folder") or "").strip(),
        "description": str(skill.get("description") or "").strip(),
        "skillRoot": str(skill.get("skillRoot") or skill.get("path") or "").strip(),
        "instructionPath": str(skill.get("instructionPath") or "").strip(),
        "sourceType": str(skill.get("sourceType") or "").strip(),
        "visibility": str(skill.get("visibility") or "").strip(),
        "workspacePath": str(skill.get("workspacePath") or "").strip(),
        "workspaceId": str(skill.get("workspaceId") or "").strip(),
        "projectId": str(skill.get("projectId") or "").strip(),
        "rootPath": str(skill.get("rootPath") or skill.get("skillRoot") or skill.get("path") or "").strip(),
        "referencesDir": str(skill.get("referencesDir") or "").strip(),
        "scriptsDir": str(skill.get("scriptsDir") or "").strip(),
        "assetsDir": str(skill.get("assetsDir") or "").strip(),
        "templatesDir": str(skill.get("templatesDir") or "").strip(),
        "examplesDir": str(skill.get("examplesDir") or "").strip(),
        "availableFiles": [
            str(item).strip()
            for item in list(skill.get("availableFiles") or [])
            if str(item).strip()
        ],
        "aliases": [str(item).strip() for item in list(skill.get("aliases") or []) if str(item).strip()],
        "triggers": [str(item).strip() for item in list(skill.get("triggers") or []) if str(item).strip()],
        "keywords": [str(item).strip() for item in list(skill.get("keywords") or []) if str(item).strip()],
        "tags": [str(item).strip() for item in list(skill.get("tags") or []) if str(item).strip()],
        "capabilityProfile": dict(skill.get("capabilityProfile") or {}),
        "themeProfile": dict(skill.get("themeProfile") or {}),
    }


def _build_mcp_rerank_document(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return "\n".join(
        [
            f"tool: {_tool_name(tool_ref)}",
            f"server: {str(metadata.get('server_name') or '').strip() or 'unknown'}",
            f"description: {_tool_description(tool_ref) or '暂无说明。'}",
        ]
    ).strip()


def _build_plugin_host_rerank_document(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return "\n".join(
        [
            f"tool: {str(metadata.get('canonicalName') or _tool_name(tool_ref)).strip()}",
            f"plugin: {str(metadata.get('pluginId') or '').strip() or 'gateway'}",
            f"raw: {str(metadata.get('rawName') or '').strip() or _tool_name(tool_ref)}",
            f"description: {_tool_description(tool_ref) or '暂无说明。'}",
        ]
    ).strip()


def _plugin_host_tool_plugin_id(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return str(metadata.get("pluginId") or "gateway").strip() or "gateway"


def _plugin_host_tool_raw_name(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    raw_name = str(metadata.get("rawName") or "").strip()
    if raw_name:
        return raw_name
    tool_name = _tool_name(tool_ref)
    if tool_name.startswith("gateway."):
        return tool_name[len("gateway.") :].strip()
    if "." in tool_name:
        return tool_name.split(".", 1)[1].strip()
    return tool_name


def _plugin_host_tool_identity(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return str(metadata.get("canonicalName") or _tool_name(tool_ref)).strip()


def _plugin_host_tool_managed_channels(tool_ref: Any) -> list[str]:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return [
        str(item).strip().lower()
        for item in list(metadata.get("managedChannels") or [])
        if str(item).strip()
    ]


def _plugin_host_tool_inventory_ready(tool_ref: Any) -> bool:
    metadata = getattr(tool_ref, "metadata", None) or {}
    if not bool(metadata.get("bridgeReady")):
        return False
    inventory_source = str(metadata.get("toolInventorySource") or "").strip().lower()
    if inventory_source not in _PLUGIN_HOST_LIVE_INVENTORY_SOURCES:
        return False
    inventory_health = str(metadata.get("toolInventoryHealth") or "").strip().lower()
    if inventory_health and inventory_health != "healthy":
        return False
    return True


def _query_mentions_plugin_host_surface(*, user_query: str, plugin_host_tools: list[Any]) -> bool:
    query_text = str(user_query or "").strip().lower()
    if not query_text:
        return False
    if any(hint in query_text for hint in _PLUGIN_HOST_QUERY_HINTS):
        return True

    derived_hints: set[str] = set()
    for tool in plugin_host_tools:
        metadata = getattr(tool, "metadata", None) or {}
        for value in (
            metadata.get("pluginId"),
            metadata.get("canonicalName"),
            metadata.get("rawName"),
        ):
            normalized = str(value or "").strip().lower()
            if not normalized:
                continue
            derived_hints.add(normalized)
            derived_hints.update(part for part in re.split(r"[^a-z0-9\u4e00-\u9fff]+", normalized) if len(part) >= 2)
        derived_hints.update(_plugin_host_tool_managed_channels(tool))
    return any(hint and hint in query_text for hint in derived_hints)


def _should_expose_plugin_host_tools(
    *,
    user_query: str,
    plugin_host_tools: list[Any],
    context_payload: dict[str, Any],
) -> bool:
    if not plugin_host_tools:
        return False
    if not any(_plugin_host_tool_inventory_ready(tool) for tool in plugin_host_tools):
        return False
    runtime_kind = str(context_payload.get("runtime_kind") or "").strip().lower()
    if runtime_kind in {"plugin_host", "channel"}:
        return True
    return _query_mentions_plugin_host_surface(user_query=user_query, plugin_host_tools=plugin_host_tools)


def _mcp_tool_server_name(tool_ref: Any) -> str:
    metadata = getattr(tool_ref, "metadata", None) or {}
    return str(metadata.get("server_name") or "unknown").strip() or "unknown"


def _normalize_tool_name_parts(tool_name: str) -> list[str]:
    return [part for part in str(tool_name or "").strip().split("_") if part]


def _family_prefix_for_tool(*, tool_name: str, sibling_tool_names: Iterable[str]) -> str | None:
    parts = _normalize_tool_name_parts(tool_name)
    if len(parts) < 2:
        return None
    normalized_siblings = [str(item or "").strip() for item in sibling_tool_names if str(item or "").strip()]
    if len(normalized_siblings) < 2:
        return None
    for width in range(len(parts) - 1, 0, -1):
        prefix = "_".join(parts[:width])
        matches = [item for item in normalized_siblings if item == prefix or item.startswith(f"{prefix}_")]
        if len(matches) >= 2:
            return prefix
    return None


def _mcp_tool_identity(tool_ref: Any) -> str:
    return f"{_mcp_tool_server_name(tool_ref)}::{_tool_name(tool_ref)}"


def _mcp_tool_family_prefix(tool_ref: Any, sibling_tool_names: Iterable[str]) -> str | None:
    return _family_prefix_for_tool(
        tool_name=_tool_name(tool_ref),
        sibling_tool_names=sibling_tool_names,
    )


def _mcp_tool_family_key(tool_ref: Any, sibling_tool_names: Iterable[str]) -> str:
    server_name = _mcp_tool_server_name(tool_ref)
    family_prefix = _mcp_tool_family_prefix(tool_ref, sibling_tool_names)
    if family_prefix:
        return f"{server_name}::{family_prefix}"
    return f"{server_name}::{_tool_name(tool_ref)}"


def _mcp_tool_family_title(tool_ref: Any, sibling_tool_names: Iterable[str]) -> str:
    family_prefix = _mcp_tool_family_prefix(tool_ref, sibling_tool_names)
    return family_prefix or _tool_name(tool_ref) or _mcp_tool_server_name(tool_ref)


def _mcp_family_payload(
    family_key: str,
    items: list[Any],
    mcp_sibling_names_by_server: dict[str, list[str]],
) -> dict[str, Any]:
    ordered_items = sorted(
        list(items or []),
        key=lambda tool: (
            _mcp_tool_server_name(tool).lower(),
            _tool_name(tool).lower(),
        ),
    )
    if not ordered_items:
        return {
            "familyKey": family_key,
            "serverName": "unknown",
            "title": family_key,
            "toolCount": 0,
            "toolNames": [],
            "descriptions": [],
        }
    seed_tool = ordered_items[0]
    server_name = _mcp_tool_server_name(seed_tool)
    sibling_names = mcp_sibling_names_by_server.get(server_name, [])
    descriptions = [
        _tool_description(tool)
        for tool in ordered_items
        if _tool_description(tool)
    ]
    unique_descriptions: list[str] = []
    seen_descriptions: set[str] = set()
    for item in descriptions:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen_descriptions:
            continue
        seen_descriptions.add(normalized)
        unique_descriptions.append(normalized)
    return {
        "familyKey": family_key,
        "serverName": server_name,
        "title": _mcp_tool_family_title(seed_tool, sibling_names),
        "toolCount": len(ordered_items),
        "toolNames": [_tool_name(tool) for tool in ordered_items],
        "descriptions": unique_descriptions[:4],
    }


def _mcp_server_payload(server_name: str, items: list[Any]) -> dict[str, Any]:
    normalized_server_name = str(server_name or "").strip() or "unknown"
    ordered_items = sorted(
        list(items or []),
        key=lambda tool: (
            _mcp_tool_server_name(tool).lower(),
            _tool_name(tool).lower(),
        ),
    )
    tool_entries: list[dict[str, str]] = []
    unique_descriptions: list[str] = []
    seen_descriptions: set[str] = set()
    for tool in ordered_items:
        tool_name = _tool_name(tool)
        tool_description = _tool_description(tool)
        if tool_name:
            tool_entries.append(
                {
                    "name": tool_name,
                    "description": tool_description,
                }
            )
        normalized_description = str(tool_description or "").strip()
        if normalized_description and normalized_description not in seen_descriptions:
            seen_descriptions.add(normalized_description)
            unique_descriptions.append(normalized_description)
    return {
        "serverKey": normalized_server_name,
        "familyKey": normalized_server_name,
        "serverName": normalized_server_name,
        "title": normalized_server_name,
        "toolCount": len(tool_entries),
        "toolNames": [item["name"] for item in tool_entries],
        "tools": tool_entries,
        "descriptions": unique_descriptions,
    }


def _should_enable_cross_runtime_escape(query_tokens: list[str]) -> bool:
    if not query_tokens:
        return False
    return any(token in _CROSS_RUNTIME_ESCAPE_TOKENS for token in query_tokens)


def _extension_runtime_source(node: str = "extensions_runtime") -> RuntimeSource:
    runtime_context = get_runtime_context()
    return RuntimeSource(
        plane="engine",
        component="extensions_runtime",
        node=node,
        agent_id=str(runtime_context.get("agent_id") or "supervisor"),
    )


class ExtensionsRuntimeService:
    def __init__(self) -> None:
        self._startup_state = "cold"
        self._snapshot_freshness = "cold"
        self._last_refresh_at: str | None = None
        self._last_refresh_error: str | None = None
        self._cached_catalog: dict[str, Any] | None = None
        self._cached_health: dict[str, Any] | None = None
        self._background_refresh_task: asyncio.Task | None = None
        self._skills_inventory_watcher_task: asyncio.Task | None = None
        self._refresh_lock = asyncio.Lock()
        self._route_cache: dict[str, tuple[float, ExtensionRouteBundle]] = {}
        self._route_cache_ttl_seconds = 20.0
        self._last_skill_inventory_change: dict[str, Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._blocked_skill_records: list[dict[str, Any]] = []

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _cache_path(self) -> Path:
        configured = str(os.getenv("V8_AGENT_OS_EXTENSIONS_CACHE_FILE") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return V8_AGENT_OS_HOME / "extensions_runtime_cache.json"

    def _controls_payload(self) -> list[dict[str, str]]:
        return [
            {"id": "refresh", "label": "刷新扩展"},
            {"id": "rebuild_catalog", "label": "重建目录"},
            {"id": "retry_subsystem", "label": "重试子系统"},
            {"id": "acknowledge_block", "label": "确认阻断"},
        ]

    def _recent_blocked_skill_records(self) -> list[dict[str, Any]]:
        return list(self._blocked_skill_records[-12:])

    def _build_mcp_server_families(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        family_map: dict[str, list[dict[str, Any]]] = {}
        sibling_names = [str(tool.get("name") or "").strip() for tool in tools if str(tool.get("name") or "").strip()]
        for tool in tools:
            tool_name = str(tool.get("name") or "").strip()
            if not tool_name:
                continue
            family_key = _family_prefix_for_tool(tool_name=tool_name, sibling_tool_names=sibling_names) or tool_name
            family_entry = {
                "name": tool_name,
                "description": str(tool.get("description") or "").strip(),
            }
            family_map.setdefault(family_key, []).append(family_entry)
        families: list[dict[str, Any]] = []
        for family_key, family_tools in sorted(family_map.items(), key=lambda item: item[0].lower()):
            families.append(
                {
                    "key": family_key,
                    "title": family_key.replace("_", " "),
                    "toolCount": len(family_tools),
                    "tools": sorted(family_tools, key=lambda item: str(item.get("name") or "").lower()),
                }
            )
        return families

    def _cached_skill_entries(self) -> list[dict[str, Any]]:
        cached_items = list((((self._cached_catalog or {}).get("skills") or {}).get("items") or []))
        normalized: list[dict[str, Any]] = []
        for item in cached_items:
            if not isinstance(item, dict):
                continue
            entry = dict(item.get("entry") or {})
            normalized.append(
                {
                    "skillId": str(item.get("skillId") or entry.get("skillId") or "").strip(),
                    "name": str(item.get("name") or entry.get("skillName") or "").strip(),
                    "folder": str(item.get("name") or entry.get("skillName") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                    "path": str(item.get("path") or entry.get("skillRoot") or "").strip(),
                    "skillName": str(entry.get("skillName") or item.get("name") or "").strip(),
                    "skillRoot": str(entry.get("skillRoot") or item.get("path") or "").strip(),
                    "instructionPath": str(entry.get("instructionPath") or "").strip(),
                    "sourceType": str(item.get("sourceType") or entry.get("sourceType") or "").strip(),
                    "visibility": str(item.get("visibility") or entry.get("visibility") or "").strip(),
                    "workspacePath": str(item.get("workspacePath") or entry.get("workspacePath") or "").strip(),
                    "workspaceId": str(item.get("workspaceId") or entry.get("workspaceId") or "").strip(),
                    "projectId": str(item.get("projectId") or entry.get("projectId") or "").strip(),
                    "rootPath": str(item.get("rootPath") or entry.get("rootPath") or entry.get("skillRoot") or item.get("path") or "").strip(),
                    "referencesDir": str(entry.get("referencesDir") or "").strip(),
                    "scriptsDir": str(entry.get("scriptsDir") or "").strip(),
                    "assetsDir": str(entry.get("assetsDir") or "").strip(),
                    "templatesDir": str(entry.get("templatesDir") or "").strip(),
                    "examplesDir": str(entry.get("examplesDir") or "").strip(),
                    "availableFiles": list(entry.get("availableFiles") or []),
                }
            )
        return normalized

    def _resolve_skill_loader_context(
        self,
        *,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
        runtime_kind: str | None = None,
    ) -> dict[str, Any]:
        context_payload = self._resolve_event_context()
        return {
            "session_id": session_id if session_id is not None else str(context_payload.get("session_id") or "").strip() or None,
            "explicit_workspace_id": explicit_workspace_id if explicit_workspace_id is not None else str(context_payload.get("workspace_id") or "").strip() or None,
            "explicit_workspace_path": explicit_workspace_path if explicit_workspace_path is not None else str(context_payload.get("workspace_path") or "").strip() or None,
            "explicit_project_id": explicit_project_id if explicit_project_id is not None else str(context_payload.get("project_id") or "").strip() or None,
            "runtime_kind": runtime_kind if runtime_kind is not None else str(context_payload.get("runtime_kind") or "chat").strip() or "chat",
        }

    def _resolve_skill_inventory(
        self,
        *,
        force_refresh: bool = False,
        prefer_cached_ready_inventory: bool = False,
        include_scoped: bool = True,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
        runtime_kind: str | None = None,
    ) -> dict[str, Any]:
        skill_context = self._resolve_skill_loader_context(
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
            runtime_kind=runtime_kind,
        )
        has_scoped_context = include_scoped and any(
            skill_context.get(key)
            for key in ("session_id", "explicit_workspace_id", "explicit_workspace_path", "explicit_project_id")
        )
        if (
            prefer_cached_ready_inventory
            and self._cached_catalog is not None
            and self._startup_state == "refreshing"
            and not force_refresh
            and not has_scoped_context
            and not include_scoped
        ):
            cached_entries = self._cached_skill_entries()
            if cached_entries:
                skills_payload = dict((self._cached_catalog or {}).get("skills") or {})
                root_descriptors = list(skills_payload.get("rootDescriptors") or [])
                roots = [str(item.get("rootPath") or "") for item in root_descriptors] or list(skills_payload.get("roots") or [])
                return {
                    "registry": {
                        str(item.get("skillId") or item.get("skillRoot") or item.get("skillName") or ""): item
                        for item in cached_entries
                        if str(item.get("skillId") or item.get("skillRoot") or item.get("skillName") or "").strip()
                    },
                    "items": cached_entries,
                    "rootDescriptors": root_descriptors,
                    "roots": roots,
                    "fingerprint": str(skills_payload.get("fingerprint") or "").strip(),
                }
        return SkillLoader.get_inventory(
            force_refresh=force_refresh,
            include_scoped=include_scoped,
            runtime_kind=skill_context.get("runtime_kind"),
            session_id=skill_context.get("session_id"),
            explicit_workspace_id=skill_context.get("explicit_workspace_id"),
            explicit_workspace_path=skill_context.get("explicit_workspace_path"),
            explicit_project_id=skill_context.get("explicit_project_id"),
        )

    def list_skills(
        self,
        *,
        force_refresh: bool = False,
        prefer_cached_ready_inventory: bool = False,
        include_blocked: bool = False,
        include_scoped: bool = True,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
        runtime_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        inventory = self._resolve_skill_inventory(
            force_refresh=force_refresh,
            prefer_cached_ready_inventory=prefer_cached_ready_inventory,
            include_scoped=include_scoped,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
            runtime_kind=runtime_kind,
        )
        skills = list(inventory.get("items") or [])
        if include_blocked:
            return skills
        blocked_keys = {
            str(item.get("skillId") or item.get("skillPath") or item.get("rootPath") or item.get("skillName") or "").strip()
            for item in self._recent_blocked_skill_records()
            if str(item.get("skillId") or item.get("skillPath") or item.get("rootPath") or item.get("skillName") or "").strip()
        }
        if not blocked_keys:
            return skills
        return [
            item
            for item in skills
            if str(item.get("skillId") or item.get("skillRoot") or item.get("skillName") or "").strip() not in blocked_keys
        ]

    def get_skill_startup_status(self) -> dict[str, Any]:
        return SkillLoader.get_startup_status()

    def prime_skill_cache(self) -> bool:
        return bool(SkillLoader.prime_startup_cache())

    def schedule_skill_refresh(self) -> asyncio.Task:
        return SkillLoader.schedule_background_refresh()

    def get_mcp_tools(self) -> list[Any]:
        return list(mcp_manager.get_tools())

    def get_mcp_status(self) -> dict[str, Any]:
        return dict(mcp_manager.get_status())

    def get_mcp_health_summary(self) -> dict[str, Any]:
        return dict(mcp_manager.get_health_summary())

    def get_mcp_startup_status(self) -> dict[str, Any]:
        return dict(mcp_manager.get_startup_status())

    def _derive_phase(self, *, skills_state: dict[str, Any], mcp_state: dict[str, Any]) -> tuple[str, list[str], list[str]]:
        blocked_reasons: list[str] = []
        degraded_reasons: list[str] = []
        blocked_records = self._recent_blocked_skill_records()
        if blocked_records:
            blocked_reasons.extend(
                [
                    f"skill:{str(item.get('skillName') or 'unknown').strip()}"
                    for item in blocked_records
                    if str(item.get("skillName") or "").strip()
                ]
            )
        if self._last_refresh_error:
            degraded_reasons.append(f"refresh:{self._last_refresh_error}")
        mcp_health = self.get_mcp_health_summary()
        if int(mcp_health.get("degraded") or 0) > 0:
            for item in list(mcp_health.get("degradedServers") or []):
                name = str(item.get("name") or "unknown").strip()
                degraded_reasons.append(f"mcp:{name}")
        if blocked_reasons:
            return "blocked", blocked_reasons[:12], degraded_reasons[:12]
        if self._startup_state == "refreshing":
            return "refreshing", [], degraded_reasons[:12]
        if self._startup_state == "cold":
            return "cold", [], degraded_reasons[:12]
        if degraded_reasons:
            return "degraded", [], degraded_reasons[:12]
        return "ready", [], []

    def _build_runtime_state(self) -> dict[str, Any]:
        skills_state = self.get_skill_startup_status()
        mcp_state = self.get_mcp_startup_status()
        phase, blocked_reasons, degraded_reasons = self._derive_phase(skills_state=skills_state, mcp_state=mcp_state)
        catalog_summary = dict(((self._cached_catalog or {}).get("summary") or {}))
        health_summary = dict(((self._cached_health or {}).get("summary") or {}))
        return {
            "phase": phase,
            "startupState": self._startup_state,
            "snapshotFreshness": self._snapshot_freshness,
            "lastRefreshAt": self._last_refresh_at,
            "lastRefreshError": self._last_refresh_error,
            "skillsStartupState": str(skills_state.get("startupState") or "cold"),
            "mcpStartupState": str(mcp_state.get("startupState") or "cold"),
            "inventoryFreshness": self._snapshot_freshness,
            "exposureFreshness": self._snapshot_freshness,
            "skillsState": skills_state,
            "mcpState": mcp_state,
            "skills": skills_state,
            "mcp": mcp_state,
            "catalogSummary": catalog_summary,
            "healthSummary": health_summary,
            "blockedReasons": blocked_reasons,
            "degradedReasons": degraded_reasons,
            "controls": self._controls_payload(),
            "silk": silk_toolchain_status(),
            "lastSkillInventoryChange": self._last_skill_inventory_change,
        }

    def _decorate_catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(payload or {})
        runtime_state = self._build_runtime_state()
        decorated.update(
            {
                "phase": runtime_state["phase"],
                "startupState": runtime_state["startupState"],
                "snapshotFreshness": runtime_state["snapshotFreshness"],
                "lastRefreshAt": runtime_state["lastRefreshAt"],
                "lastRefreshError": runtime_state["lastRefreshError"],
                "skillsStartupState": runtime_state["skillsStartupState"],
                "mcpStartupState": runtime_state["mcpStartupState"],
                "catalogSummary": runtime_state["catalogSummary"],
                "healthSummary": runtime_state["healthSummary"],
                "blockedReasons": runtime_state["blockedReasons"],
                "degradedReasons": runtime_state["degradedReasons"],
                "controls": runtime_state["controls"],
                "runtime": runtime_state,
            }
        )
        return decorated

    def _decorate_health(self, payload: dict[str, Any]) -> dict[str, Any]:
        decorated = dict(payload or {})
        runtime_state = self._build_runtime_state()
        decorated.update(
            {
                "phase": runtime_state["phase"],
                "startupState": runtime_state["startupState"],
                "snapshotFreshness": runtime_state["snapshotFreshness"],
                "lastRefreshAt": runtime_state["lastRefreshAt"],
                "lastRefreshError": runtime_state["lastRefreshError"],
                "skillsStartupState": runtime_state["skillsStartupState"],
                "mcpStartupState": runtime_state["mcpStartupState"],
                "catalogSummary": runtime_state["catalogSummary"],
                "healthSummary": runtime_state["healthSummary"],
                "blockedReasons": runtime_state["blockedReasons"],
                "degradedReasons": runtime_state["degradedReasons"],
                "controls": runtime_state["controls"],
                "runtime": runtime_state,
                "silk": runtime_state["silk"],
            }
        )
        return decorated

    def _persist_cache(self) -> None:
        if self._cached_catalog is None or self._cached_health is None:
            return
        cache_path = self._cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updatedAt": self._last_refresh_at or self._now_iso(),
            "catalog": self._cached_catalog,
            "health": self._cached_health,
        }
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_cache(self) -> bool:
        cache_path = self._cache_path()
        if not cache_path.exists():
            return False
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        catalog = payload.get("catalog")
        health = payload.get("health")
        if not isinstance(catalog, dict) or not isinstance(health, dict):
            return False
        self._cached_catalog = catalog
        self._cached_health = health
        self._startup_state = "ready"
        self._snapshot_freshness = "cached"
        self._last_refresh_at = str(payload.get("updatedAt") or "").strip() or None
        self._last_refresh_error = None
        return True

    def _build_catalog_live(self) -> dict[str, Any]:
        skill_inventory = self._resolve_skill_inventory(force_refresh=False, include_scoped=False)
        skills = list(skill_inventory.get("items") or [])
        skills_sorted = sorted(skills, key=lambda item: str(item.get("name") or "").lower())
        mcp_status = self.get_mcp_status()
        skills_state = self.get_skill_startup_status()

        servers: list[dict[str, Any]] = []
        total_tools = 0
        connected_servers = 0
        total_families = 0
        for server_name, payload in sorted(mcp_status.items(), key=lambda item: item[0].lower()):
            tools = list(payload.get("tools") or [])
            status = str(payload.get("status") or "error")
            if status == "connected":
                connected_servers += 1
            total_tools += len(tools)
            families = self._build_mcp_server_families(tools)
            total_families += len(families)
            config = dict(payload.get("config") or {})
            target = str(config.get("url") or config.get("command") or "")
            if config.get("args"):
                args_text = " ".join(str(item) for item in list(config.get("args") or []))
                target = f"{target} {args_text}".strip()
            servers.append(
                {
                    "name": server_name,
                    "status": status,
                    "toolCount": len(tools),
                    "tools": tools,
                    "families": families,
                    "transport": str(config.get("type") or ("stdio" if config.get("command") else "sse")),
                    "target": target,
                    "disabled": bool(config.get("disabled", False)),
                }
            )

        root_descriptors = list(skill_inventory.get("rootDescriptors") or list(skills_state.get("rootDescriptors") or []))
        roots = [str(item.get("rootPath") or "") for item in root_descriptors]
        skills_fingerprint = str(skills_state.get("fingerprint") or "").strip()
        changed_at = str(
            ((self._last_skill_inventory_change or {}).get("changedAt"))
            or skills_state.get("lastRefreshAt")
            or "",
        ).strip() or None
        return {
            "fingerprint": skills_fingerprint,
            "changedAt": changed_at,
            "lastSkillInventoryChange": self._last_skill_inventory_change,
            "summary": {
                "skillCount": len(skills_sorted),
                "blockedSkillCount": len(self._recent_blocked_skill_records()),
                "mcpServerCount": len(servers),
                "connectedMcpServerCount": connected_servers,
                "mcpToolCount": total_tools,
                "mcpFamilyCount": total_families,
            },
            "skillDependencyPolicy": get_skill_dependency_policy(),
            "skills": {
                "root": str(roots[0]) if roots else "",
                "roots": [str(root) for root in roots],
                "rootDescriptors": root_descriptors,
                "fingerprint": skills_fingerprint,
                "changedAt": changed_at,
                "items": [
                    {
                        "skillId": str(item.get("skillId") or "").strip(),
                        "name": str(item.get("name") or item.get("folder") or ""),
                        "description": str(item.get("description") or "暂无说明。"),
                        "path": str(item.get("path") or ""),
                        "sourceType": str(item.get("sourceType") or "").strip(),
                        "visibility": str(item.get("visibility") or "").strip(),
                        "workspacePath": str(item.get("workspacePath") or "").strip(),
                        "workspaceId": str(item.get("workspaceId") or "").strip(),
                        "projectId": str(item.get("projectId") or "").strip(),
                        "rootPath": str(item.get("rootPath") or item.get("skillRoot") or item.get("path") or "").strip(),
                        "entry": _skill_entry_payload(item),
                    }
                    for item in skills_sorted
                ],
            },
            "mcp": {
                "servers": servers,
            },
        }

    def _build_health_live(self, catalog: dict[str, Any]) -> dict[str, Any]:
        status_breakdown = Counter()
        for server in list(((catalog.get("mcp") or {}).get("servers") or [])):
            status_breakdown[str(server.get("status") or "error")] += 1

        skills_payload = dict(catalog.get("skills") or {})
        root = str(skills_payload.get("root") or "")
        return {
            "summary": dict(catalog.get("summary") or {}),
            "skillDependencyPolicy": dict(catalog.get("skillDependencyPolicy") or {}),
            "skills": {
                "root": root,
                "roots": list(skills_payload.get("roots") or []),
                "rootDescriptors": list(skills_payload.get("rootDescriptors") or []),
                "available": bool(root),
                "blockedCount": len(self._recent_blocked_skill_records()),
            },
            "mcp": {
                "statusBreakdown": dict(status_breakdown),
                "health": self.get_mcp_health_summary(),
            },
        }

    async def _wait_optional_task(self, task: asyncio.Task | None, *, timeout: float, label: str) -> None:
        if not task:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"[ExtensionsRuntime] {label} is still refreshing in background; catalog warmup will continue with current state.")

    async def _refresh_runtime_snapshot(
        self,
        *,
        skill_refresh_task: asyncio.Task | None = None,
        mcp_init_task: asyncio.Task | None = None,
        force_skill_reload: bool = False,
        force_mcp_reload: bool = False,
    ) -> dict[str, Any]:
        async with self._refresh_lock:
            self._startup_state = "refreshing"
            self._snapshot_freshness = "cached" if self._cached_catalog else "cold"
            self._last_refresh_error = None
            if force_skill_reload:
                await asyncio.to_thread(SkillLoader.reload_skills)
            else:
                await self._wait_optional_task(skill_refresh_task, timeout=12.0, label="SkillLoader")
            if force_mcp_reload:
                await mcp_manager.cleanup()
                await mcp_manager.initialize()
            else:
                await self._wait_optional_task(mcp_init_task, timeout=12.0, label="MCP")

            catalog = self._build_catalog_live()
            health = self._build_health_live(catalog)
            self._cached_catalog = catalog
            self._cached_health = health
            self._startup_state = "ready"
            self._snapshot_freshness = "live"
            self._last_refresh_at = self._now_iso()
            self._last_refresh_error = None
            self._route_cache.clear()
            self._persist_cache()
            return self._decorate_health(health)

    async def _refresh_skill_inventory_if_changed(self, *, reason: str = "watcher") -> dict[str, Any]:
        change = await asyncio.to_thread(SkillLoader.reload_if_changed)
        if not change.get("changed"):
            return change
        self._last_skill_inventory_change = {
            **change,
            "changedAt": self._now_iso(),
            "reason": reason,
        }
        await self._refresh_runtime_snapshot()
        print(
            "[ExtensionsRuntime] Skills inventory changed: "
            f"reason={reason}, "
            f"added={change.get('addedSkills') or []}, "
            f"removed={change.get('removedSkills') or []}, "
            f"updated={change.get('updatedSkills') or []}"
        )
        return change

    def request_skill_inventory_refresh(self, *, reason: str = "manual") -> None:
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._refresh_skill_inventory_if_changed(reason=reason),
                loop,
            )
            return
        try:
            change = SkillLoader.reload_if_changed()
            if change.get("changed"):
                self._last_skill_inventory_change = {
                    **change,
                    "changedAt": self._now_iso(),
                    "reason": reason,
                }
                self._route_cache.clear()
                self._cached_catalog = None
                self._cached_health = None
        except Exception as exc:
            self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
            print(f"[ExtensionsRuntime] Immediate skills inventory refresh failed: {type(exc).__name__}: {exc}")

    def _ensure_skill_inventory_watcher(self) -> None:
        task = self._skills_inventory_watcher_task
        if task and not task.done():
            return

        async def _runner() -> None:
            while True:
                await asyncio.sleep(2.0)
                try:
                    await self._refresh_skill_inventory_if_changed(reason="watcher")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
                    print(f"[ExtensionsRuntime] Skills inventory watcher failed: {type(exc).__name__}: {exc}")

        self._skills_inventory_watcher_task = asyncio.create_task(_runner(), name="extensions_runtime:skills_inventory_watcher")

    async def start(
        self,
        *,
        skill_refresh_task: asyncio.Task | None = None,
        mcp_init_task: asyncio.Task | None = None,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        if self._cached_catalog is None or self._cached_health is None:
            self._load_cache()
        if self._cached_catalog is None or self._cached_health is None:
            cold_catalog = self._build_catalog_live()
            self._cached_catalog = cold_catalog
            self._cached_health = self._build_health_live(cold_catalog)
        self._startup_state = "refreshing"
        self._snapshot_freshness = "cached" if self._last_refresh_at else "cold"
        self._last_refresh_error = None

        if self._background_refresh_task and not self._background_refresh_task.done():
            self._ensure_skill_inventory_watcher()
            return

        async def _runner() -> None:
            try:
                await self._refresh_runtime_snapshot(
                    skill_refresh_task=skill_refresh_task,
                    mcp_init_task=mcp_init_task,
                )
            except Exception as exc:
                self._startup_state = "error"
                self._last_refresh_error = str(exc).strip() or exc.__class__.__name__
                print(f"[ExtensionsRuntime] Background refresh failed: {type(exc).__name__}: {exc}")

        self._background_refresh_task = asyncio.create_task(_runner(), name="extensions_runtime:refresh")
        self._ensure_skill_inventory_watcher()

    async def stop(self) -> None:
        self._loop = None
        watcher_task = self._skills_inventory_watcher_task
        if watcher_task and not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
        self._skills_inventory_watcher_task = None
        task = self._background_refresh_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def get_runtime_snapshot(self) -> dict[str, Any]:
        return self._build_runtime_state()

    def get_startup_status(self) -> dict[str, Any]:
        return self.get_runtime_snapshot()

    def _resolve_prefilter_policy(self) -> dict[str, Any]:
        config = storage.get_extensions_config() or {}
        policy = dict(config.get("prefilterPolicy") or config.get("rerankPolicy") or {})
        default_policy = ((storage.get_extensions_config() or {}).get("prefilterPolicy") or {})
        skills_policy = dict(policy.get("skills") or default_policy.get("skills") or {})
        mcp_policy = dict(policy.get("mcp") or default_policy.get("mcp") or {})
        enabled = bool(policy.get("enabled", False))

        def _stage_policy(raw: dict[str, Any], *, default_top: int, default_llm_top: int) -> dict[str, Any]:
            return {
                "stage1TopK": max(1, min(int(raw.get("stage1TopK") or default_top), 100)),
                "llmEnabled": bool(raw.get("llmEnabled", True)),
                "stage2TopK": max(1, min(int(raw.get("stage2TopK") or default_llm_top), 50)),
                "llmTimeoutSeconds": max(5, min(int(raw.get("llmTimeoutSeconds") or 5), 10)),
            }

        if not enabled:
            return {
                "enabled": False,
                "available": False,
                "mode": "lexical",
                "modelId": "",
                "role": "",
                "reason": "disabled",
                "skills": _stage_policy(skills_policy, default_top=20, default_llm_top=5),
                "mcp": _stage_policy(mcp_policy, default_top=20, default_llm_top=2),
            }

        for role in ("extensions_prefilter", "extensions_reranker"):
            try:
                resolved = model_control_plane.resolve_model_for_role(role)
            except Exception as exc:
                return {
                    "enabled": True,
                    "available": False,
                    "mode": "fallback",
                    "modelId": "",
                    "role": role,
                    "reason": str(exc),
                    "skills": _stage_policy(skills_policy, default_top=20, default_llm_top=5),
                    "mcp": _stage_policy(mcp_policy, default_top=20, default_llm_top=2),
                }
            model_id = str(resolved.get("resolvedModelId") or "").strip()
            if model_id:
                return {
                    "enabled": True,
                    "available": True,
                    "mode": str(policy.get("mode") or "two_stage").strip() or "two_stage",
                    "modelId": model_id,
                    "role": role,
                    "reason": "",
                    "skills": _stage_policy(skills_policy, default_top=20, default_llm_top=5),
                    "mcp": _stage_policy(mcp_policy, default_top=20, default_llm_top=2),
                }

        return {
            "enabled": True,
            "available": False,
            "mode": "fallback",
            "modelId": "",
            "role": "extensions_prefilter",
            "reason": "未绑定可用的扩展候选预筛模型。",
            "skills": _stage_policy(skills_policy, default_top=20, default_llm_top=5),
            "mcp": _stage_policy(mcp_policy, default_top=20, default_llm_top=2),
        }

    def build_catalog(self) -> dict[str, Any]:
        payload = dict(self._cached_catalog or self._build_catalog_live())
        return self._decorate_catalog(payload)

    def build_health(self) -> dict[str, Any]:
        payload = dict(self._cached_health or self._build_health_live(self._cached_catalog or self._build_catalog_live()))
        return self._decorate_health(payload)

    async def reload(self) -> dict[str, Any]:
        return await self._refresh_runtime_snapshot(force_skill_reload=True, force_mcp_reload=True)

    async def build_prefilter_preview(self, *, user_query: str, refresh: bool = False) -> dict[str, Any]:
        normalized_query = str(user_query or "").strip()
        if not normalized_query:
            return {
                "queryPreview": "",
                "skillStage1Entries": [],
                "skillEntries": [],
                "mcpStage1Servers": [],
                "mcpServers": [],
                "mcpFamilies": [],
                "counts": {},
                "routing": {},
            }
        if refresh:
            await self.reload()
        route_bundle = self.build_contextual_route(
            user_query=normalized_query,
            available_tools=list(self.get_mcp_tools()),
            loaded_agents=None,
            skill_limit=5,
            mcp_limit=2,
            plugin_host_limit=0,
        )
        candidate_summary = route_bundle.candidate_summary
        return {
            "queryPreview": _truncate(normalized_query, 160),
            "skillStage1Entries": candidate_summary.get("skillStage1Entries") or [],
            "skillEntries": candidate_summary.get("skillEntries") or [],
            "skillRootDescriptors": candidate_summary.get("skillRootDescriptors") or [],
            "mcpStage1Servers": candidate_summary.get("mcpStage1Servers") or [],
            "mcpServers": candidate_summary.get("mcpServers") or [],
            "mcpFamilies": candidate_summary.get("mcpFamilies") or [],
            "counts": candidate_summary,
            "routing": {
                "mode": candidate_summary.get("mode"),
                "routingMode": candidate_summary.get("routingMode"),
                "skillsRoutingMode": candidate_summary.get("skillsRoutingMode"),
                "mcpRoutingMode": candidate_summary.get("mcpRoutingMode"),
                "modelId": candidate_summary.get("modelId"),
                "role": candidate_summary.get("role"),
                "reason": candidate_summary.get("reason"),
                "prefilterTimedOut": candidate_summary.get("prefilterTimedOut"),
                "prefilterCacheHit": candidate_summary.get("prefilterCacheHit"),
                "stage1Enabled": candidate_summary.get("stage1Enabled") or {},
                "stage1TopK": candidate_summary.get("stage1TopK") or {},
                "stage2Enabled": candidate_summary.get("stage2Enabled") or {},
                "stage2TopK": candidate_summary.get("stage2TopK") or {},
                "llmTimeoutSeconds": candidate_summary.get("llmTimeoutSeconds") or {},
                "skillStage1HitCount": candidate_summary.get("skillStage1HitCount"),
                "skillStage1ShortlistCount": candidate_summary.get("skillStage1ShortlistCount"),
                "skillFinalExposedCount": candidate_summary.get("skillFinalExposedCount"),
                "mcpStage1HitCount": candidate_summary.get("mcpStage1HitCount"),
                "mcpStage1ShortlistCount": candidate_summary.get("mcpStage1ShortlistCount"),
                "mcpFinalExposedCount": candidate_summary.get("mcpFinalExposedCount"),
                "skillInventoryCount": candidate_summary.get("skillInventoryCount"),
                "mcpInventoryCount": candidate_summary.get("mcpInventoryCount"),
                "skillPoolSize": candidate_summary.get("skillPoolSize"),
                "mcpPoolSize": candidate_summary.get("mcpPoolSize"),
                "mcpServerPoolSize": candidate_summary.get("mcpServerPoolSize"),
                "mcpFamilyPoolSize": candidate_summary.get("mcpFamilyPoolSize"),
                "selectedSkills": candidate_summary.get("skills") or [],
                "selectedSkillIds": candidate_summary.get("selectedSkillIds") or [],
                "selectedMcpServers": candidate_summary.get("mcpSelectedServers") or [],
                "selectedMcpFamilies": candidate_summary.get("mcpSelectedFamilies") or [],
                "selectedMcpTools": candidate_summary.get("mcpTools") or [],
                "primaryThemeIntents": candidate_summary.get("primaryThemeIntents") or [],
                "themeMatchedCount": candidate_summary.get("themeMatchedCount"),
                "themeBackfilledCount": candidate_summary.get("themeBackfilledCount"),
                "themeRankingSignals": candidate_summary.get("themeRankingSignals") or {},
            },
        }

    def build_contextual_route(
        self,
        *,
        user_query: str,
        available_tools: list[Any],
        loaded_agents: list[dict[str, Any]] | None = None,
        inherited_skill_ids: list[str] | None = None,
        inherited_skill_names: list[str] | None = None,
        skill_limit: int = 5,
        mcp_limit: int = 2,
        plugin_host_limit: int = 8,
    ) -> ExtensionRouteBundle:
        query_text = str(user_query or "").strip()
        query_tokens = _query_tokens_for_extensions(query_text)
        query_profile = _detect_query_intents(query_text, query_tokens)
        context_payload = self._resolve_event_context()
        cross_runtime_escape = _should_enable_cross_runtime_escape(query_tokens)
        prefilter_policy = self._resolve_prefilter_policy()
        skill_policy = dict(prefilter_policy.get("skills") or {})
        mcp_policy = dict(prefilter_policy.get("mcp") or {})
        skill_stage1_enabled = bool(skill_policy.get("stage1Enabled", True))
        skill_stage1_top_k = max(1, int(skill_policy.get("stage1TopK") or 20))
        skill_stage2_configured = bool(skill_policy.get("llmEnabled", True))
        skill_stage2_top_k = max(1, int(skill_policy.get("stage2TopK") or 5))
        skill_stage2_timeout = max(5, min(int(skill_policy.get("llmTimeoutSeconds") or 5), 10))
        mcp_stage1_enabled = bool(mcp_policy.get("stage1Enabled", True))
        mcp_stage1_top_k = max(1, int(mcp_policy.get("stage1TopK") or 20))
        mcp_stage2_configured = bool(mcp_policy.get("llmEnabled", True))
        mcp_stage2_top_k = max(1, int(mcp_policy.get("stage2TopK") or 2))
        mcp_stage2_timeout = max(5, min(int(mcp_policy.get("llmTimeoutSeconds") or 5), 10))
        effective_skill_stage1_limit = skill_stage1_top_k
        effective_mcp_stage1_limit = mcp_stage1_top_k
        effective_plugin_host_limit = max(0, min(plugin_host_limit, 12))
        prefilter_model_id = str(prefilter_policy.get("modelId") or "").strip()
        prefilter_role = str(prefilter_policy.get("role") or "").strip()
        prefilter_reason = str(prefilter_policy.get("reason") or "").strip()
        stage2_runtime_available = bool(
            prefilter_policy.get("enabled") and prefilter_policy.get("available") and prefilter_model_id
        )

        skill_inventory = self._resolve_skill_inventory(
            force_refresh=False,
            prefer_cached_ready_inventory=True,
            include_scoped=True,
        )
        skill_entries = list(skill_inventory.get("items") or [])
        skill_root_descriptors = list(skill_inventory.get("rootDescriptors") or [])
        ranked_skills = sorted(
            (
                (
                    *_score_skill_entry(
                        query_text=query_text,
                        query_tokens=query_tokens,
                        query_profile=query_profile,
                        skill=item,
                    ),
                    str(item.get("name") or item.get("folder") or ""),
                    item,
                )
                for item in skill_entries
            ),
            key=lambda row: (-int(bool(row[1])), -row[0], row[3].lower()),
        )
        skill_stage1_hits = [row[4] for row in ranked_skills if row[0] > 0] if skill_stage1_enabled else []
        skill_stage1_hit_count = len(skill_stage1_hits)
        skill_pool = skill_stage1_hits[:effective_skill_stage1_limit] if effective_skill_stage1_limit > 0 else []
        skill_stage1_shortlist = list(skill_pool)
        skill_stage2_candidate_entries = list(skill_stage1_shortlist) if skill_stage1_enabled else list(skill_entries)
        selected_skills = list(skill_stage1_shortlist) if skill_stage1_enabled else list(skill_entries)
        skill_routing_mode = "stage1_only" if skill_stage1_enabled else "unfiltered"

        mcp_tools = [tool for tool in available_tools if _is_mcp_tool(tool)]
        raw_plugin_host_tools = [tool for tool in available_tools if _is_plugin_host_tool(tool)]
        base_tools = [tool for tool in available_tools if not _is_mcp_tool(tool) and not _is_plugin_host_tool(tool)]
        plugin_host_tools = (
            raw_plugin_host_tools
            if _should_expose_plugin_host_tools(
                user_query=user_query,
                plugin_host_tools=raw_plugin_host_tools,
                context_payload=context_payload,
            )
            else []
        )
        mcp_server_map: dict[str, list[Any]] = {}
        for tool in mcp_tools:
            server_name = _mcp_tool_server_name(tool)
            mcp_server_map.setdefault(server_name, []).append(tool)

        ranked_mcp_servers: list[tuple[int, str, list[Any]]] = []
        for server_name, raw_items in mcp_server_map.items():
            items = sorted(
                raw_items,
                key=lambda tool: (
                    _mcp_tool_server_name(tool).lower(),
                    _tool_name(tool).lower(),
                ),
            )
            if not items:
                continue
            tool_names = [_tool_name(tool) for tool in items if _tool_name(tool)]
            tool_descriptions = [
                _tool_description(tool)
                for tool in items
                if _tool_description(tool)
            ]
            server_score = _score_mcp_server_entry(
                query_text=query_text,
                query_tokens=query_tokens,
                server_name=server_name,
                items=items,
            )
            ranked_mcp_servers.append((server_score, server_name, items))

        ranked_mcp_servers.sort(
            key=lambda row: (
                -row[0],
                row[1].lower(),
            ),
        )

        mcp_stage1_hits = [
            server_name
            for score, server_name, _items in ranked_mcp_servers
            if score > 0
        ] if mcp_stage1_enabled else []
        mcp_stage1_hit_count = len(mcp_stage1_hits)
        mcp_pool_server_keys = mcp_stage1_hits[:effective_mcp_stage1_limit] if effective_mcp_stage1_limit > 0 else []
        mcp_pool = list(mcp_pool_server_keys)
        mcp_stage1_shortlist_keys = list(mcp_pool_server_keys)
        mcp_stage2_candidate_keys = list(mcp_stage1_shortlist_keys) if mcp_stage1_enabled else list(mcp_server_map.keys())
        selected_mcp_server_keys = list(mcp_stage1_shortlist_keys) if mcp_stage1_enabled else list(mcp_server_map.keys())
        mcp_routing_mode = "stage1_only" if mcp_stage1_enabled else "unfiltered"

        def _expand_mcp_server_keys(server_keys: list[str]) -> list[Any]:
            expanded: list[Any] = []
            seen: set[str] = set()
            for server_key in server_keys:
                items = sorted(
                    mcp_server_map.get(server_key, []),
                    key=lambda tool: (
                        _mcp_tool_server_name(tool).lower(),
                        _tool_name(tool).lower(),
                    ),
                )
                for tool in items:
                    identity = _mcp_tool_identity(tool)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    expanded.append(tool)
            return expanded

        selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)

        ranked_plugin_host = sorted(
            (
                (
                    _score_text(
                        query_tokens=query_tokens,
                        title=_tool_name(tool),
                        description=_tool_description(tool),
                    ),
                    _tool_name(tool).lower(),
                    tool,
                )
                for tool in plugin_host_tools
            ),
            key=lambda row: (-row[0], row[1]),
        )
        plugin_host_pool_limit = max(effective_plugin_host_limit * 2, _PLUGIN_HOST_RERANK_POOL_FLOOR)
        plugin_host_pool = [row[2] for row in ranked_plugin_host if row[0] > 0][:plugin_host_pool_limit]
        selected_plugin_host_seeds = list(plugin_host_pool[:effective_plugin_host_limit])
        skill_family_map = {
            str(item.get("path") or item.get("name") or item.get("folder") or "").strip(): item
            for item in skill_stage2_candidate_entries
            if str(item.get("path") or item.get("name") or item.get("folder") or "").strip()
        }
        plugin_host_seed_map: dict[str, Any] = {}
        for tool in plugin_host_pool:
            family_key = f"{_plugin_host_tool_plugin_id(tool)}::{_plugin_host_tool_raw_name(tool)}"
            plugin_host_seed_map.setdefault(family_key, tool)

        skill_state: dict[str, Any] = {
            "mode": skill_routing_mode,
            "reason": prefilter_reason or (
                "Stage 2 已关闭，直接使用第 1 层 shortlist。"
                if skill_stage1_enabled
                else "Stage 1 与 Stage 2 已关闭，直接暴露完整 inventory。"
            ),
            "timedOut": False,
            "cacheHit": False,
            "bypassed": not stage2_runtime_available or not skill_stage2_configured,
        }
        mcp_state: dict[str, Any] = {
            "mode": mcp_routing_mode,
            "reason": prefilter_reason or (
                "Stage 2 已关闭，直接使用第 1 层 shortlist。"
                if mcp_stage1_enabled
                else "Stage 1 与 Stage 2 已关闭，直接暴露完整 inventory。"
            ),
            "timedOut": False,
            "cacheHit": False,
            "bypassed": not stage2_runtime_available or not mcp_stage2_configured,
        }
        plugin_host_state: dict[str, Any] = {}
        selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)
        skill_stage2_enabled = bool(stage2_runtime_available and skill_stage2_configured)
        mcp_stage2_enabled = bool(stage2_runtime_available and mcp_stage2_configured)
        plugin_host_llm_enabled = (
            stage2_runtime_available
            and effective_plugin_host_limit > 0
            and bool(plugin_host_tools)
            and bool(plugin_host_seed_map)
            and any(_plugin_host_tool_inventory_ready(tool) for tool in plugin_host_tools)
        )
        if skill_stage2_enabled:
            if len(skill_family_map) > skill_stage2_top_k:
                skill_state = {}
            else:
                selected_skills = list(skill_stage2_candidate_entries[:skill_stage2_top_k])
                skill_routing_mode = "llm_rerank_shortlist" if skill_stage1_enabled else "llm_rerank_full_inventory"
                skill_state = {
                    "mode": skill_routing_mode,
                    "reason": "候选数量不足，直接使用当前候选集。",
                    "timedOut": False,
                    "cacheHit": False,
                    "bypassed": True,
                }
        if mcp_stage2_enabled:
            if len(mcp_stage2_candidate_keys) > mcp_stage2_top_k:
                mcp_state = {}
            else:
                selected_mcp_server_keys = list(mcp_stage2_candidate_keys[:mcp_stage2_top_k])
                selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)
                mcp_routing_mode = "llm_rerank_shortlist" if mcp_stage1_enabled else "llm_rerank_full_inventory"
                mcp_state = {
                    "mode": mcp_routing_mode,
                    "reason": "候选数量不足，直接使用当前候选集。",
                    "timedOut": False,
                    "cacheHit": False,
                    "bypassed": True,
                }
        if stage2_runtime_available:
            rerank_specs: list[tuple[str, dict[str, Any]]] = []
            if skill_stage2_enabled and len(skill_family_map) > skill_stage2_top_k:
                rerank_specs.append((
                    "skills",
                    {
                        "role": prefilter_role or "extensions_prefilter",
                        "user_query": query_text,
                        "family_label": "skills",
                        "families": [
                            {
                                "key": key,
                                "name": str(item.get("name") or item.get("folder") or "").strip() or key,
                                "description": str(item.get("description") or "").strip(),
                            }
                            for key, item in skill_family_map.items()
                        ],
                        "max_families": skill_stage2_top_k,
                        "timeout_seconds": skill_stage2_timeout,
                    },
                ))
            if mcp_stage2_enabled and len(mcp_stage2_candidate_keys) > mcp_stage2_top_k:
                rerank_specs.append((
                    "mcp",
                    {
                        "role": prefilter_role or "extensions_prefilter",
                        "user_query": query_text,
                        "family_label": "mcp",
                        "families": [
                            {
                                "key": server_name,
                                "name": server_name,
                                "description": " | ".join(
                                    part
                                    for part in [
                                        " ".join(_tool_name(tool) for tool in items if _tool_name(tool)),
                                        " ".join(_tool_description(tool) for tool in items if _tool_description(tool)),
                                    ]
                                    if str(part or "").strip()
                                ),
                            }
                            for server_name in mcp_stage2_candidate_keys
                            for items in [mcp_server_map.get(server_name, [])]
                            if items
                        ],
                        "max_families": mcp_stage2_top_k,
                        "timeout_seconds": mcp_stage2_timeout,
                    },
                ))
            if plugin_host_llm_enabled and len(plugin_host_seed_map) > effective_plugin_host_limit:
                rerank_specs.append((
                    "plugin_host",
                    {
                        "role": prefilter_role or "extensions_prefilter",
                        "user_query": query_text,
                        "family_label": "plugin_host",
                        "families": [
                            {
                                "key": family_key,
                                "name": str((getattr(tool, "metadata", None) or {}).get("canonicalName") or _tool_name(tool)).strip() or family_key,
                                "description": _tool_description(tool),
                            }
                            for family_key, tool in plugin_host_seed_map.items()
                        ],
                        "max_families": effective_plugin_host_limit,
                        "timeout_seconds": max(skill_stage2_timeout, mcp_stage2_timeout),
                    },
                ))
            else:
                plugin_host_state = {
                    "mode": "lexical_shortlist",
                    "reason": "PluginHost bridge 未 ready 或候选数量不足，跳过第 2 层精排。",
                    "timedOut": False,
                    "cacheHit": False,
                    "bypassed": True,
                }

            rerank_results: dict[str, tuple[list[str], dict[str, Any]]] = {}
            if rerank_specs:
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(rerank_specs), thread_name_prefix="v8-ext-route") as executor:
                        future_map = {
                            executor.submit(select_family_keys_with_llm, **kwargs): family_label
                            for family_label, kwargs in rerank_specs
                        }
                        for future in concurrent.futures.as_completed(future_map):
                            family_label = future_map[future]
                            rerank_results[family_label] = future.result()
                except Exception as exc:
                    prefilter_reason = str(exc).strip() or exc.__class__.__name__
            if "skills" in rerank_results:
                skill_keys, skill_state = rerank_results["skills"]
                selected_skills = [skill_family_map[key] for key in skill_keys if key in skill_family_map][:skill_stage2_top_k]
                skill_routing_mode = "llm_rerank_shortlist" if skill_stage1_enabled else "llm_rerank_full_inventory"
            if "mcp" in rerank_results:
                mcp_keys, mcp_state = rerank_results["mcp"]
                selected_mcp_server_keys = [key for key in mcp_keys if key in mcp_server_map]
                selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)
                mcp_routing_mode = "llm_rerank_shortlist" if mcp_stage1_enabled else "llm_rerank_full_inventory"
            if "plugin_host" in rerank_results:
                plugin_host_keys, plugin_host_state = rerank_results["plugin_host"]
                selected_plugin_host_seeds = [plugin_host_seed_map[key] for key in plugin_host_keys if key in plugin_host_seed_map][:effective_plugin_host_limit]

            if bool(skill_state.get("timedOut")):
                prefilter_reason = "timeout"
                if skill_stage1_enabled:
                    selected_skills = list(skill_stage1_shortlist)
                    skill_routing_mode = "fallback_stage1"
                else:
                    selected_skills = list(skill_entries)
                    skill_routing_mode = "fallback_unfiltered"
            if bool(mcp_state.get("timedOut")):
                prefilter_reason = "timeout"
                if mcp_stage1_enabled:
                    selected_mcp_server_keys = list(mcp_stage1_shortlist_keys)
                    mcp_routing_mode = "fallback_stage1"
                else:
                    selected_mcp_server_keys = list(mcp_server_map.keys())
                    mcp_routing_mode = "fallback_unfiltered"
                selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)
            if bool(plugin_host_state.get("timedOut")):
                prefilter_reason = "timeout"
                selected_plugin_host_seeds = list(plugin_host_pool[:effective_plugin_host_limit])
        else:
            plugin_host_state = {
                "mode": "lexical_shortlist",
                "reason": prefilter_reason or "未启用第 2 层 LLM 精排。",
                "timedOut": False,
                "cacheHit": False,
                "bypassed": True,
            }
        selected_mcp_server_keys = _unique_preserve_order(selected_mcp_server_keys)
        selected_mcp_tools = _expand_mcp_server_keys(selected_mcp_server_keys)

        plugin_host_bound_limit = min(
            max(effective_plugin_host_limit * 2, _PLUGIN_HOST_RERANK_POOL_FLOOR),
            _PLUGIN_HOST_BOUND_CAP,
        )
        selected_plugin_host_tools = expand_tool_family_seeds(
            items=plugin_host_tools,
            seeds=selected_plugin_host_seeds,
            get_plugin_id=_plugin_host_tool_plugin_id,
            get_tool_name=_plugin_host_tool_raw_name,
            get_identity=_plugin_host_tool_identity,
            get_sort_key=lambda tool: (
                _plugin_host_tool_plugin_id(tool).lower(),
                _plugin_host_tool_raw_name(tool).lower(),
                _plugin_host_tool_identity(tool).lower(),
            ),
            max_items=plugin_host_bound_limit,
        )

        inherited_skill_ids_set = {
            str(item or "").strip()
            for item in list(inherited_skill_ids or [])
            if str(item or "").strip()
        }
        inherited_skill_names_set = {
            str(item or "").strip()
            for item in list(inherited_skill_names or [])
            if str(item or "").strip()
        }
        if inherited_skill_ids_set or inherited_skill_names_set:
            inherited_skill_entries = [
                item
                for item in skill_entries
                if (
                    str(item.get("skillId") or "").strip() in inherited_skill_ids_set
                    or str(item.get("name") or item.get("folder") or "").strip() in inherited_skill_names_set
                )
            ]
            if inherited_skill_entries:
                merged_skills: list[dict[str, Any]] = []
                seen_skill_keys: set[str] = set()
                for item in inherited_skill_entries + list(selected_skills):
                    key = str(item.get("skillId") or item.get("path") or item.get("name") or item.get("folder") or "").strip()
                    if not key or key in seen_skill_keys:
                        continue
                    seen_skill_keys.add(key)
                    merged_skills.append(item)
                selected_skills = merged_skills

        selected_skill_ids = [str(item.get("skillId") or "").strip() for item in selected_skills if str(item.get("skillId") or "").strip()]
        selected_skill_names = [str(item.get("name") or item.get("folder") or "") for item in selected_skills]
        skill_stage1_entries = [_skill_entry_payload(item) for item in skill_stage1_shortlist]
        selected_skill_entries = [_skill_entry_payload(item) for item in selected_skills]
        skill_name_counts: dict[str, int] = {}
        for item in skill_entries:
            normalized_skill_name = str(item.get("skillName") or item.get("name") or item.get("folder") or "").strip().lower()
            if not normalized_skill_name:
                continue
            skill_name_counts[normalized_skill_name] = skill_name_counts.get(normalized_skill_name, 0) + 1
        exposed_mcp_tool_names = [_tool_name(tool) for tool in selected_mcp_tools]
        mcp_stage1_servers = [
            _mcp_server_payload(server_key, mcp_server_map.get(server_key, []))
            for server_key in mcp_stage1_shortlist_keys
            if mcp_server_map.get(server_key)
        ]
        selected_mcp_servers = [
            _mcp_server_payload(server_key, mcp_server_map.get(server_key, []))
            for server_key in selected_mcp_server_keys
            if mcp_server_map.get(server_key)
        ]
        exposed_plugin_host_tool_names = [_tool_name(tool) for tool in selected_plugin_host_tools]
        filtered_tools = base_tools + selected_mcp_tools + selected_plugin_host_tools
        plugin_host_routing_mode = str(plugin_host_state.get("mode") or ("skipped" if not plugin_host_tools else "lexical_shortlist")).strip() or "skipped"
        route_modes = [skill_routing_mode, mcp_routing_mode]
        distinct_route_modes = _unique_preserve_order([mode for mode in route_modes if str(mode or "").strip()])
        prefilter_mode = distinct_route_modes[0] if len(distinct_route_modes) == 1 else "mixed"
        prefilter_reason = (
            prefilter_reason
            or skill_state.get("reason")
            or mcp_state.get("reason")
            or plugin_host_state.get("reason")
            or ""
        )

        lines = [
            "\n[Extensions Runtime]",
            f"- Skills 候选：{len(selected_skill_names)} / 已安装 {len(skill_entries)}",
            f"- MCP 工具候选：{len(exposed_mcp_tool_names)} / 已连接工具 {len(mcp_tools)}",
        ]
        if cross_runtime_escape:
            lines.append("- Cross-runtime escape：已启用。检测到阻塞/切换类任务语义，本轮适度放宽跨 runtime 候选。")
        if any(mode.startswith("llm_rerank") for mode in (skill_routing_mode, mcp_routing_mode)):
            lines.append(f"- 候选预筛：已启用两层预筛（LLM 精排模型：{prefilter_model_id}）")
        elif any(mode.startswith("fallback") for mode in (skill_routing_mode, mcp_routing_mode)):
            details = prefilter_reason or "当前未绑定可用的扩展候选预筛模型。"
            lines.append(f"- 候选预筛：本轮已回退 lexical（{_truncate(details, 120)}）")
        elif skill_stage1_enabled or mcp_stage1_enabled:
            lines.append("- 候选预筛：当前使用第 1 层 shortlist。")
        else:
            lines.append("- 候选预筛：当前未启用 Stage 1 / Stage 2，将直接暴露完整 inventory。")
        if selected_skill_names:
            lines.append("- 当前命中的 Skills 目录入口：")
            for entry in selected_skill_entries:
                source_label = str(entry.get("sourceType") or "global").strip() or "global"
                normalized_entry_name = str(entry.get("skillName") or "").strip().lower()
                has_duplicate_skill_name = skill_name_counts.get(normalized_entry_name, 0) > 1
                lines.append(f"  - {entry.get('skillName') or 'unknown'} [{source_label}]")
                lines.append(
                    f"    - Skill description: {_truncate(_single_line_text(entry.get('description') or '') or '暂无说明。', 180)}"
                )
                if has_duplicate_skill_name and entry.get("skillRoot"):
                    lines.append(f"    - Root: {entry.get('skillRoot')}")
        if exposed_mcp_tool_names:
            lines.append("- 当前暴露给本轮的 MCP servers（选中 server 后暴露完整工具树）：")
            for server in selected_mcp_servers:
                lines.append(f"  - {server.get('serverName')} ({server.get('toolCount')} tools)")
            lines.append("- 当前暴露给本轮的 MCP 工具：")
            for tool in selected_mcp_tools:
                server_name = _mcp_tool_server_name(tool)
                lines.append(
                    f"  - {_tool_name(tool)} ({server_name}): {_truncate(_tool_description(tool) or '暂无说明。', 80)}"
                )
        if exposed_plugin_host_tool_names:
            lines.append("- 当前暴露给本轮的 OpenClaw 工具：")
            for tool in selected_plugin_host_tools[:effective_plugin_host_limit]:
                metadata = getattr(tool, "metadata", None) or {}
                plugin_id = str(metadata.get("pluginId") or "").strip() or "gateway"
                lines.append(
                    f"  - {str(metadata.get('canonicalName') or _tool_name(tool)).strip()} ({plugin_id}): "
                    f"{_truncate(_tool_description(tool) or '暂无说明。', 80)}"
                )
        lines.append("[/Extensions Runtime]")

        return ExtensionRouteBundle(
            prompt_addition="\n".join(lines),
            filtered_tools=filtered_tools,
            selected_skill_names=selected_skill_names,
            selected_skill_ids=selected_skill_ids,
            skill_root_descriptors=skill_root_descriptors,
            exposed_mcp_tool_names=exposed_mcp_tool_names,
            candidate_summary={
                "mode": prefilter_mode,
                "skillsRoutingMode": skill_routing_mode,
                "mcpRoutingMode": mcp_routing_mode,
                "pluginHostRoutingMode": plugin_host_routing_mode,
                "modelId": prefilter_model_id,
                "role": prefilter_role,
                "reason": prefilter_reason or None,
                "prefilterTimedOut": bool(any(bool(state.get("timedOut")) for state in (skill_state, mcp_state, plugin_host_state))),
                "prefilterCacheHit": bool(any(bool(state.get("cacheHit")) for state in (skill_state, mcp_state, plugin_host_state))),
                "stage1Enabled": {
                    "skills": skill_stage1_enabled,
                    "mcp": mcp_stage1_enabled,
                },
                "stage1TopK": {
                    "skills": effective_skill_stage1_limit,
                    "mcp": effective_mcp_stage1_limit,
                },
                "stage2Enabled": {
                    "skills": skill_stage2_enabled,
                    "mcp": mcp_stage2_enabled,
                },
                "stage2TopK": {
                    "skills": skill_stage2_top_k,
                    "mcp": mcp_stage2_top_k,
                },
                "llmTimeoutSeconds": {
                    "skills": skill_stage2_timeout,
                    "mcp": mcp_stage2_timeout,
                },
                "routingMode": prefilter_mode,
                "skills": selected_skill_names,
                "selectedSkillIds": selected_skill_ids,
                "artifactIntent": query_profile.get("artifactIntent"),
                "operationIntent": query_profile.get("operationIntent"),
                "primaryThemeIntents": list(query_profile.get("primaryThemeIntents") or []),
                "secondaryThemeHints": list(query_profile.get("secondaryThemeHints") or []),
                "rankingSignals": {
                    "artifactAnchor": bool(query_profile.get("artifactIntent")),
                    "operationIntent": bool(query_profile.get("operationIntent")),
                    "topicTokenCount": len(list(query_profile.get("topicTokens") or [])),
                },
                "themeRankingSignals": {
                    "themeIntent": bool(query_profile.get("primaryThemeIntents")),
                    "secondaryThemeHints": len(list(query_profile.get("secondaryThemeHints") or [])),
                    "artifactAnchorPresent": bool(query_profile.get("artifactIntent")),
                },
                "profileMatchedCount": len(
                    [
                        item
                        for item in selected_skill_entries
                        if bool((item.get("capabilityProfile") or {}).get("primaryArtifactTypes"))
                        or bool((item.get("capabilityProfile") or {}).get("primaryOperations"))
                    ]
                ),
                "profileBackfilledCount": len(
                    [
                        item
                        for item in selected_skill_entries
                        if str((item.get("capabilityProfile") or {}).get("profileSource") or "").strip() == "llm_assisted"
                    ]
                ),
                "themeMatchedCount": len(
                    [
                        item
                        for item in selected_skill_entries
                        if bool((item.get("themeProfile") or {}).get("primaryThemes"))
                        or bool((item.get("themeProfile") or {}).get("secondaryThemeTags"))
                    ]
                ),
                "themeBackfilledCount": len(
                    [
                        item
                        for item in selected_skill_entries
                        if str((item.get("themeProfile") or {}).get("themeSource") or "").strip() == "llm_assisted"
                    ]
                ),
                "skillStage1Entries": skill_stage1_entries,
                "skillEntries": selected_skill_entries,
                "skillRootDescriptors": skill_root_descriptors,
                "mcpTools": exposed_mcp_tool_names,
                "mcpStage1Servers": mcp_stage1_servers,
                "mcpServers": selected_mcp_servers,
                "mcpFamilies": selected_mcp_servers,
                "pluginHostTools": exposed_plugin_host_tool_names,
                "seedUnit": "skill_or_mcp_server",
                "skillCandidates": len(selected_skill_names),
                "mcpCandidates": len(exposed_mcp_tool_names),
                "mcpServerCandidates": len(selected_mcp_servers),
                "pluginHostCandidates": len(exposed_plugin_host_tool_names),
                "skillInventoryCount": len(skill_entries),
                "skillPoolSize": len(skill_entries),
                "skillStage1HitCount": skill_stage1_hit_count,
                "skillStage1ShortlistCount": len(skill_stage1_shortlist),
                "skillLexicalPoolSize": len(skill_stage1_shortlist),
                "skillFinalExposedCount": len(selected_skill_entries),
                "mcpInventoryCount": len(mcp_server_map),
                "mcpPoolSize": len(mcp_server_map),
                "mcpStage1HitCount": mcp_stage1_hit_count,
                "mcpStage1ShortlistCount": len(mcp_stage1_shortlist_keys),
                "mcpLexicalPoolSize": len(mcp_stage1_shortlist_keys),
                "mcpFinalExposedCount": len(selected_mcp_servers),
                "mcpServerPoolSize": len(mcp_server_map),
                "mcpServerCount": len(mcp_server_map),
                "mcpFamilyPoolSize": len(mcp_server_map),
                "mcpFamilyCount": len(mcp_server_map),
                "mcpExpandedToolCount": len(exposed_mcp_tool_names),
                "mcpSelectedServers": list(selected_mcp_server_keys),
                "mcpSelectedFamilies": list(selected_mcp_server_keys),
                "pluginHostPoolSize": len(plugin_host_pool),
                "requestedSkillLimit": skill_limit,
                "requestedMcpLimit": mcp_limit,
                "requestedPluginHostLimit": plugin_host_limit,
                "effectiveSkillLimit": len(selected_skill_entries),
                "effectiveMcpLimit": len(selected_mcp_servers),
                "effectivePluginHostLimit": effective_plugin_host_limit,
                "crossRuntimeEscape": cross_runtime_escape,
                "pluginHostSeedCount": len(selected_plugin_host_seeds),
                "pluginHostBoundLimit": plugin_host_bound_limit,
                "pluginHostBoundCount": len(exposed_plugin_host_tool_names),
                "totalInstalledSkills": len(skill_entries),
                "totalConnectedMcpTools": len(mcp_tools),
                "totalPluginHostTools": len(plugin_host_tools),
                "agentCount": len(list(loaded_agents or [])),
            },
        )

    def build_supervisor_route(
        self,
        *,
        user_query: str,
        supervisor_tools: list[Any],
        loaded_agents: list[dict[str, Any]] | None = None,
        skill_limit: int = 5,
        mcp_limit: int = 2,
        plugin_host_limit: int = 8,
    ) -> ExtensionRouteBundle:
        context_payload = self._resolve_event_context()
        session_id = str(context_payload.get("session_id") or "").strip() or "global"
        skill_inventory = self._resolve_skill_inventory(
            force_refresh=False,
            include_scoped=True,
            session_id=str(context_payload.get("session_id") or "").strip() or None,
            explicit_workspace_id=str(context_payload.get("workspace_id") or "").strip() or None,
            explicit_workspace_path=str(context_payload.get("workspace_path") or "").strip() or None,
            explicit_project_id=str(context_payload.get("project_id") or "").strip() or None,
            runtime_kind=str(context_payload.get("runtime_kind") or "chat").strip() or "chat",
        )
        has_scoped_roots = any(
            str(item.get("sourceType") or "").strip() == "scoped_workspace"
            for item in list(skill_inventory.get("rootDescriptors") or [])
        )
        normalized_query = " ".join(_tokenize(user_query)) or str(user_query or "").strip().lower()
        tool_signature = ",".join(sorted(_tool_name(tool) for tool in supervisor_tools if _tool_name(tool)))
        inventory_revision = str(self._last_refresh_at or "cold")
        cache_key = "|".join(
            [
                session_id,
                str(context_payload.get("project_id") or ""),
                str(context_payload.get("workspace_id") or ""),
                str(context_payload.get("workspace_path") or ""),
                normalized_query,
                inventory_revision,
                str(len(list(loaded_agents or []))),
                str(skill_limit),
                str(mcp_limit),
                str(plugin_host_limit),
                tool_signature,
            ]
        )
        now = time.monotonic()
        cached = None if has_scoped_roots else self._route_cache.get(cache_key)
        if cached and (now - cached[0]) <= self._route_cache_ttl_seconds:
            return cached[1]

        bundle = self.build_contextual_route(
            user_query=user_query,
            available_tools=supervisor_tools,
            loaded_agents=loaded_agents,
            skill_limit=skill_limit,
            mcp_limit=mcp_limit,
            plugin_host_limit=plugin_host_limit,
        )
        if not has_scoped_roots:
            self._route_cache[cache_key] = (now, bundle)
            if len(self._route_cache) > 128:
                stale_keys = sorted(self._route_cache.items(), key=lambda item: item[1][0])[:32]
                for stale_key, _ in stale_keys:
                    self._route_cache.pop(stale_key, None)
        return bundle

    def bind_execution_context(self, **context: Any):
        current = dict(_EXTENSION_CONTEXT.get() or {})
        current.update({key: value for key, value in context.items() if value is not None})
        return _EXTENSION_CONTEXT.set(current)

    def reset_execution_context(self, token: contextvars.Token) -> None:
        _EXTENSION_CONTEXT.reset(token)

    def _resolve_event_context(self) -> dict[str, Any]:
        payload = dict(_EXTENSION_CONTEXT.get() or {})
        runtime_context = get_runtime_context()
        for key in ("session_id", "conversation_id", "run_id", "agent_id", "workspace_id", "workspace_path", "project_id", "runtime_kind"):
            if payload.get(key) is None and runtime_context.get(key) is not None:
                payload[key] = runtime_context.get(key)
        return payload

    def _emit(self, topic: str, payload: dict[str, Any], *, node: str) -> None:
        context_payload = self._resolve_event_context()
        session_id = str(context_payload.get("session_id") or "")
        if not session_id:
            return
        emitter = event_bus.create_emitter(
            session_id=session_id,
            conversation_id=str(context_payload.get("conversation_id") or session_id),
            run_id=str(context_payload.get("run_id") or "") or None,
            source=_extension_runtime_source(node=node),
        )
        emitter.emit(topic, payload, source=_extension_runtime_source(node=node))

    def emit_route_selected(self, *, user_query: str, route_bundle: ExtensionRouteBundle) -> None:
        self._emit(
            "extension.route.selected",
            {
                "queryPreview": _truncate(user_query, 160),
                "skillCandidates": route_bundle.selected_skill_names,
                "selectedSkillIds": route_bundle.selected_skill_ids,
                "skillStage1Entries": route_bundle.candidate_summary.get("skillStage1Entries") or [],
                "skillEntries": route_bundle.candidate_summary.get("skillEntries") or [],
                "skillRootDescriptors": route_bundle.skill_root_descriptors,
                "mcpStage1Servers": route_bundle.candidate_summary.get("mcpStage1Servers") or [],
                "mcpToolCandidates": route_bundle.exposed_mcp_tool_names,
                "mcpServerCandidates": route_bundle.candidate_summary.get("mcpServers") or [],
                "pluginHostToolCandidates": route_bundle.candidate_summary.get("pluginHostTools") or [],
                "counts": route_bundle.candidate_summary,
                "routing": {
                    "mode": route_bundle.candidate_summary.get("mode"),
                    "routingMode": route_bundle.candidate_summary.get("routingMode"),
                    "skillsRoutingMode": route_bundle.candidate_summary.get("skillsRoutingMode"),
                    "mcpRoutingMode": route_bundle.candidate_summary.get("mcpRoutingMode"),
                    "modelId": route_bundle.candidate_summary.get("modelId"),
                    "role": route_bundle.candidate_summary.get("role"),
                    "stage1Enabled": route_bundle.candidate_summary.get("stage1Enabled"),
                    "stage1TopK": route_bundle.candidate_summary.get("stage1TopK"),
                    "stage2Enabled": route_bundle.candidate_summary.get("stage2Enabled"),
                    "stage2TopK": route_bundle.candidate_summary.get("stage2TopK"),
                    "llmTimeoutSeconds": route_bundle.candidate_summary.get("llmTimeoutSeconds"),
                    "skillInventoryCount": route_bundle.candidate_summary.get("skillInventoryCount"),
                    "skillStage1ShortlistCount": route_bundle.candidate_summary.get("skillStage1ShortlistCount"),
                    "skillFinalExposedCount": route_bundle.candidate_summary.get("skillFinalExposedCount"),
                    "mcpInventoryCount": route_bundle.candidate_summary.get("mcpInventoryCount"),
                    "mcpStage1ShortlistCount": route_bundle.candidate_summary.get("mcpStage1ShortlistCount"),
                    "mcpFinalExposedCount": route_bundle.candidate_summary.get("mcpFinalExposedCount"),
                    "skillPoolSize": route_bundle.candidate_summary.get("skillPoolSize"),
                    "mcpPoolSize": route_bundle.candidate_summary.get("mcpPoolSize"),
                    "pluginHostPoolSize": route_bundle.candidate_summary.get("pluginHostPoolSize"),
                    "selectedSkills": route_bundle.candidate_summary.get("skills"),
                    "selectedSkillIds": route_bundle.candidate_summary.get("selectedSkillIds"),
                    "selectedMcpServers": route_bundle.candidate_summary.get("mcpSelectedServers"),
                    "selectedMcpTools": route_bundle.candidate_summary.get("mcpTools"),
                    "selectedPluginHostTools": route_bundle.candidate_summary.get("pluginHostTools"),
                },
            },
            node="route_selected",
        )
        if route_bundle.exposed_mcp_tool_names:
            self._emit(
                "extension.mcp.candidate_exposed",
                {
                    "toolNames": route_bundle.exposed_mcp_tool_names,
                    "count": len(route_bundle.exposed_mcp_tool_names),
                },
                node="mcp_candidate_exposed",
            )

    def emit_skill_loaded(self, *, skill_id: str | None = None, skill_name: str, skill_path: str) -> None:
        normalized_identity = str(skill_id or skill_path or skill_name or "").strip()
        if normalized_identity:
            self._blocked_skill_records = [
                item
                for item in self._blocked_skill_records
                if str(item.get("skillId") or item.get("skillPath") or item.get("skillName") or "").strip() != normalized_identity
            ]
        self._emit(
            "extension.skill.loaded",
            {
                "skillId": skill_id,
                "skillName": skill_name,
                "skillPath": skill_path,
            },
            node="skill_loaded",
        )

    def emit_skill_blocked(
        self,
        *,
        skill_id: str,
        skill_name: str,
        skill_path: str,
        root_path: str,
        source_type: str,
        verdict: str,
        confidence: float,
        skill_trust_score: int,
        audit_id: str,
        reasons: list[str],
        flagged_files: list[dict[str, Any]],
    ) -> None:
        payload = {
            "skillId": skill_id,
            "skillName": skill_name,
            "skillPath": skill_path,
            "rootPath": root_path,
            "sourceType": source_type,
            "verdict": verdict,
            "confidence": confidence,
            "skillTrustScore": skill_trust_score,
            "auditId": audit_id,
            "reasons": list(reasons or []),
            "flaggedFiles": list(flagged_files or []),
        }
        self._blocked_skill_records.append(
            {
                **payload,
                "blockedAt": self._now_iso(),
            }
        )
        self._blocked_skill_records = self._blocked_skill_records[-24:]
        self._route_cache.clear()
        self._emit("extension.skill.blocked", payload, node="skill_blocked")
        self._emit("safety.skill_blocked", payload, node="skill_blocked")

    def emit_response_tool_calls(self, response: Any) -> None:
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        if not tool_calls:
            return
        current_mcp_tools = {tool.name for tool in self.get_mcp_tools()}
        invoked = [str(item.get("name") or "") for item in tool_calls if str(item.get("name") or "") in current_mcp_tools]
        if not invoked:
            return
        self._emit(
            "extension.mcp.invoked",
            {
                "toolNames": invoked,
                "count": len(invoked),
            },
            node="mcp_invoked",
        )

    def emit_execution_completed(self, *, response: Any) -> None:
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        self._emit(
            "extension.execution.completed",
            {
                "hasToolCalls": bool(tool_calls),
                "toolNames": [str(item.get("name") or "") for item in tool_calls],
                "messagePreview": _truncate(str(getattr(response, "content", "") or ""), 200),
            },
            node="execution_completed",
        )

    def emit_supervisor_diagnostics(self, payload: dict[str, Any]) -> None:
        self._emit("supervisor.turn.diagnostics", payload, node="supervisor_diagnostics")

    def build_usage_summary(self, *, window_hours: int = 24) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        threshold = (now - timedelta(hours=max(window_hours, 1))).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        current_mcp_tools = {tool.name for tool in self.get_mcp_tools()}

        skill_counter: Counter[str] = Counter()
        mcp_counter: Counter[str] = Counter()
        recent_events: list[dict[str, Any]] = []
        exposure_counter: Counter[str] = Counter()
        failure_count = 0

        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT topic, payload_json, event_ts
                FROM runtime_events
                WHERE event_ts >= ?
                  AND (topic LIKE 'extension.%' OR topic = 'tool.started')
                ORDER BY event_ts DESC
                LIMIT 300
                """,
                (threshold,),
            )
            rows = cursor.fetchall()

        for row in rows:
            topic = str(row["topic"] or "")
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            event_ts = str(row["event_ts"] or "")
            if topic == "extension.skill.loaded":
                skill_name = str(payload.get("skillName") or "").strip()
                if skill_name:
                    skill_counter[skill_name] += 1
                    recent_events.append({"kind": "skill", "name": skill_name, "ts": event_ts})
            elif topic == "extension.skill.blocked":
                skill_name = str(payload.get("skillName") or "").strip()
                verdict = str(payload.get("verdict") or "").strip()
                if skill_name:
                    recent_events.append(
                        {
                            "kind": "skill_blocked",
                            "name": skill_name,
                            "status": verdict or "blocked",
                            "ts": event_ts,
                        }
                    )
            elif topic == "extension.mcp.invoked":
                for tool_name in list(payload.get("toolNames") or []):
                    normalized = str(tool_name or "").strip()
                    if normalized:
                        mcp_counter[normalized] += 1
                        recent_events.append({"kind": "mcp", "name": normalized, "ts": event_ts})
            elif topic == "extension.mcp.candidate_exposed":
                for tool_name in list(payload.get("toolNames") or []):
                    normalized = str(tool_name or "").strip()
                    if normalized:
                        exposure_counter[normalized] += 1
            elif topic == "extension.execution.completed":
                if not payload.get("hasToolCalls") and "失败" in str(payload.get("messagePreview") or ""):
                    failure_count += 1
            elif topic == "tool.started":
                tool = payload.get("tool") or payload
                tool_name = str(tool.get("toolName") or tool.get("tool_name") or "").strip()
                if tool_name and tool_name in current_mcp_tools:
                    mcp_counter[tool_name] += 1
                    recent_events.append({"kind": "mcp", "name": tool_name, "ts": event_ts})

        candidate_summary = {
            "skills": sum(skill_counter.values()),
            "mcpTools": sum(mcp_counter.values()),
            "currentExposedSkillCandidates": len(skill_counter),
            "currentExposedMcpCandidates": len(exposure_counter),
        }

        return {
            "windowHours": max(window_hours, 1),
            "skills": {
                "totalUses": sum(skill_counter.values()),
                "topItems": [{"name": name, "count": count} for name, count in skill_counter.most_common(6)],
            },
            "mcp": {
                "totalUses": sum(mcp_counter.values()),
                "topItems": [{"name": name, "count": count} for name, count in mcp_counter.most_common(8)],
            },
            "recentHits": recent_events[:10],
            "degradationSummary": {
                "recentFailures": failure_count,
            },
            "candidateExposure": candidate_summary,
        }

class ExtensionsRuntime:
    kind = "extensions"

    def __init__(self, service: ExtensionsRuntimeService) -> None:
        self.service = service

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "ExtensionsRuntime",
            "summary": "负责 Skills + MCP 的编目、健康、候选暴露与扩展治理汇总，不承担 plugin_host 渠道宿主职责。",
            "responsibilities": [
                "统一管理 skills 与 MCP inventory",
                "维护扩展 catalog / health / phase / refresh 控制面",
                "为 chat / supervisor / delegation 暴露统一的扩展候选真相",
                "汇总 skill / MCP 安全与降级状态",
            ],
            "routingKeywords": ["skills", "mcp", "extensions", "扩展", "候选工具", "技能"],
            "acceptedInputs": ["catalog refresh", "inventory snapshot", "tool exposure request"],
            "producedOutputs": ["extensions snapshot", "candidate summary", "health summary"],
            "ownedSteps": ["extensions.refresh", "extensions.catalog", "extensions.route", "extensions.health"],
            "supportsPause": False,
            "supportsResume": False,
            "supportsApproval": True,
            "supportsRepair": True,
            "visibility": "primary",
            "promptHints": [
                "skills 和 MCP 的候选、健康与暴露语义，都应先看 ExtensionsRuntime，而不是各自直连 loader/manager。",
            ],
            "capabilities": [
                {
                    "key": "extensions.inventory",
                    "label": "扩展目录与候选暴露",
                    "summary": "统一输出 skills 与 MCP 的 catalog、health、候选筛选与热更新状态。",
                    "accepts": ["refresh request", "route query", "inventory change"],
                    "outputs": ["catalog", "health", "candidate bundle"],
                    "examples": ["扩展刷新后重建候选", "基于上下文筛选 skill 与 MCP families"],
                    "risk_level": "medium",
                }
            ],
            "metadata": {
                "umbrellaScope": ["skills", "mcp", "catalog", "health", "exposure", "safety_mediation"],
                "installMode": "background_reconcile",
                "hotInsertMode": "soft",
                "controls": [item["id"] for item in self.service._controls_payload()],
                "selectionUnits": {
                    "skills": "item",
                    "mcp": "server",
                },
            },
        }

    def snapshot(self) -> dict[str, Any]:
        return self.service.get_runtime_snapshot()


extensions_runtime_service = ExtensionsRuntimeService()
extensions_runtime = runtime_registry.register(ExtensionsRuntime(extensions_runtime_service))

__all__ = [
    "ExtensionRouteBundle",
    "ExtensionsRuntime",
    "ExtensionsRuntimeService",
    "extensions_runtime",
    "extensions_runtime_service",
]
