import asyncio
import concurrent.futures
import hashlib
import json
import re
import shutil
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import tool

from core.llm_factory import llm_factory
from core.background_context_guard import prepare_background_model_messages
from core.background_model_output import parse_background_json_object
from core.model_control_plane import model_control_plane
from core.storage import storage
from core.extensions_capability_index import (
    annotate_skill_entries,
    legacy_skill_inventory_cache_path,
    skill_inventory_cache_path,
)
from core.workspace_resolution import workspace_resolution_service
from runtimes.memory.project_registry import project_registry_service


_PROFILE_LLM_TIMEOUT_SECONDS = 6.0
_SKILLS_CACHE_SCHEMA_VERSION = 11
_PRIMARY_ARTIFACT_LIMIT = 2
_PRIMARY_OPERATION_LIMIT = 3
_SECONDARY_HINT_LIMIT = 4
_PRIMARY_THEME_LIMIT = 2
_SECONDARY_THEME_LIMIT = 5
_PROFILE_INFERENCE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="v8-skill-profile",
)
_HINT_SPLIT_RE = re.compile(r"[,\n/|]+")
_TEXT_WHITESPACE_RE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_SKILL_MATCH_TOKEN_RE = re.compile(r"[0-9a-z\u4e00-\u9fff]+")
_SKILL_MATCH_FUZZY_MIN_SCORE = 10
_SKILL_MATCH_AMBIGUITY_GAP = 4
_SKILL_MATCH_AMBIGUITY_RATIO = 1.15
_SKILL_MATCH_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "ppt": ("pptx", "powerpoint", "presentation", "slide", "slides", "deck", "演示稿"),
    "slides": ("slide", "pptx", "presentation", "slide deck", "deck", "演示稿"),
    "presentation deck": ("presentation", "pptx", "slides", "slide deck", "powerpoint", "演示稿"),
    "演示稿": ("presentation", "ppt", "pptx", "slides", "powerpoint"),
    "word": ("doc", "docx", "document", "word document", "office document", "文档"),
    "word文档": ("word", "doc", "docx", "word document", "office document", "document"),
    "docs": ("documentation", "design doc", "decision doc", "proposal", "prd", "rfc", "技术文档"),
    "documentation": ("docs", "design doc", "decision doc", "proposal", "prd", "rfc", "技术文档"),
    "思维顾问": ("女娲", "huashu-nuwa"),
}
_SKILL_TEMPLATE_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\buse\s+this\s+skill\b", re.IGNORECASE),
    re.compile(r"\buse\s+when\b", re.IGNORECASE),
    re.compile(r"\bwhen\s+claude\s+needs\b", re.IGNORECASE),
    re.compile(r"\bthis\s+skill\s+should\b", re.IGNORECASE),
    re.compile(r"\bskill\s+description\b", re.IGNORECASE),
    re.compile(r"\bSKILL\.md\b", re.IGNORECASE),
    re.compile(r"使用该技能"),
    re.compile(r"使用技能"),
    re.compile(r"当用户需要"),
)
_SKILL_AUTHORING_STRONG_TERMS: tuple[str, ...] = (
    "skill-creator",
    "skill creator",
    "skill-builder",
    "skill builder",
    "darwin-skill",
    "nuwa",
    "女娲",
    "女娲造人",
    "造skill",
    "造 skill",
    "造人",
    "蒸馏",
    "create skill",
    "create a skill",
    "generate skill",
    "generate a skill",
    "build skill",
    "build a skill",
    "update skill",
    "optimize skill",
    "persona skill",
    "人物skill",
    "人物 skill",
)
_SEEDED_GLOBAL_SKILL_NAMES: tuple[str, ...] = ("code-reviewer",)
_SKILL_SEED_TOMBSTONE_SCHEMA_VERSION = 1
_ARTIFACT_RULES: dict[str, tuple[str, ...]] = {
    "presentation": (
        "ppt",
        "pptx",
        ".ppt",
        ".pptx",
        "powerpoint",
        "presentation",
        "slide",
        "slides",
        "slide deck",
        "deck",
        "幻灯片",
        "演示稿",
        "演示文稿",
        "汇报材料",
    ),
    "video": (
        "video",
        "videos",
        "text-to-video",
        "image-to-video",
        "video generation",
        "动画视频",
        "视频",
        "短片",
        "影像",
        "remotion",
        "manim",
    ),
    "image": (
        "image",
        "images",
        "picture",
        "pictures",
        "illustration",
        "poster",
        "visual art",
        "海报",
        "图片",
        "图像",
        "插画",
    ),
    "document": (
        "document",
        "doc",
        "docx",
        ".doc",
        ".docx",
        "word",
        "report",
        "article",
        "markdown",
        "md",
        "文档",
        "文章",
        "报告",
    ),
    "pdf": ("pdf", ".pdf"),
    "spreadsheet": (
        "excel",
        "xlsx",
        "xls",
        "csv",
        ".xlsx",
        ".xls",
        ".csv",
        "spreadsheet",
        "table",
        "表格",
        "表单",
    ),
    "audio": (
        "audio",
        "voice",
        "speech",
        "podcast",
        "music",
        "音频",
        "语音",
        "配音",
    ),
    "code": ("code", "coding", "script", "scripts", "代码", "脚本"),
    "skill": _SKILL_AUTHORING_STRONG_TERMS,
}
_OPERATION_RULES: dict[str, tuple[str, ...]] = {
    "create": (
        "create",
        "creation",
        "creating",
        "generate",
        "generated",
        "generating",
        "build",
        "draft",
        "make",
        "publish",
        "publishing",
        "写",
        "生成",
        "创建",
        "制作",
        "产出",
    ),
    "edit": ("edit", "editing", "editor", "modify", "revise", "update", "编辑", "修改", "调整"),
    "analyze": ("analyze", "analysis", "analytical", "review", "audit", "检查", "分析", "审阅", "复盘"),
    "convert": ("convert", "conversion", "transform", "export", "exporting", "转成", "转换", "导出"),
    "search": ("search", "find", "lookup", "query", "检索", "搜索", "查找", "查询"),
    "guide": ("guide", "guidance", "tutorial", "how to", "best practice", "教程", "指南", "最佳实践"),
    "advise": ("advise", "advisory", "advisor", "perspective", "consult", "建议", "视角", "顾问"),
    "automate": ("workflow", "pipeline", "automation", "automated", "api", "cli", "batch", "自动化", "流水线", "接口"),
}
_SKILL_CLASS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "skill_authoring": _SKILL_AUTHORING_STRONG_TERMS,
    "advisor_or_perspective": ("perspective", "视角", "顾问", "advisor", "mentor", "思维框架"),
    "methodology_or_tutorial": ("tutorial", "guide", "指南", "教程", "best practice", "framework", "方法论"),
    "integration_or_tooling": ("mcp", "plugin", "bridge", "integration", "builder", "server", "宿主", "桥接"),
    "workflow_or_script": ("workflow", "pipeline", "script", "scripts", "api", "cli", "automation", "流水线", "脚本"),
}
_INTERACTION_MODE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "advisory": ("perspective", "advisor", "顾问", "视角"),
    "reference_guidance": ("tutorial", "guide", "教程", "指南", "best practice"),
    "workflow": ("workflow", "pipeline", "automation", "cli", "api", "脚本", "自动化"),
    "file_workflow": ("pptx", "pdf", "docx", "xlsx", "presentation", "slides", "document", "excel"),
    "media_workflow": ("video", "image", "audio", "avatar", "poster"),
}
_PRIMARY_THEME_RULES: dict[str, tuple[str, ...]] = {
    "decision_quality": (
        "decision quality",
        "决策质量",
        "认知偏误",
        "bias",
        "biases",
        "mental model",
        "mental models",
        "判断力",
        "判断",
        "多元思维模型",
        "思维框架",
        "第一性原理",
        "first principles",
        "inversion",
        "逆向思考",
    ),
    "wealth_money": (
        "wealth",
        "money",
        "赚钱",
        "财富",
        "杠杆",
        "leverage",
        "specific knowledge",
        "specific_knowledge",
        "financial freedom",
        "资本配置",
        "投资",
    ),
    "startup_growth": (
        "startup",
        "growth",
        "创业",
        "增长",
        "增长策略",
        "distribution",
        "商业化",
        "monetization",
        "go to market",
        "gtm",
        "traction",
        "acquisition",
        "conversion",
        "growth loop",
        "用户增长",
        "渠道增长",
        "增长飞轮",
    ),
    "product_strategy": (
        "product",
        "产品",
        "strategy",
        "战略",
        "positioning",
        "roadmap",
        "product market fit",
        "pmf",
        "成本结构",
        "cost structure",
        "垂直整合",
        "vertical integration",
    ),
    "engineering_ai": (
        "engineering",
        "engineer",
        "ai",
        "人工智能",
        "机器学习",
        "machine learning",
        "llm",
        "software",
        "代码",
        "神经网络",
        "training",
        "research taste",
    ),
    "content_media": (
        "content",
        "creator",
        "media",
        "内容",
        "wechat",
        "weixin",
        "official account",
        "public account",
        "公众号",
        "微信公众号",
        "视频",
        "youtube",
        "thumbnail",
        "hook",
        "retention",
        "attention",
        "publishing",
        "publish",
        "图文",
        "短视频",
        "创作者",
    ),
    "writing_communication": (
        "writing",
        "communication",
        "写作",
        "沟通",
        "storytelling",
        "表达",
        "article",
        "articles",
        "文章",
        "copywriting",
        "文风",
        "newsletter",
    ),
    "organization_leadership": (
        "organization",
        "leadership",
        "组织",
        "管理",
        "管理者",
        "manager",
        "culture",
        "hiring",
        "hire",
        "talent",
        "人才密度",
        "talent density",
        "组织效率",
        "团队效率",
        "团队管理",
        "组织协同",
        "org design",
        "组织设计",
        "lead",
        "team",
    ),
    "career_learning": (
        "career",
        "learning",
        "学习",
        "成长",
        "职业",
        "education",
        "特定知识",
        "学习方法",
        "school",
    ),
    "negotiation_persuasion": (
        "negotiation",
        "persuasion",
        "influence",
        "说服",
        "说服力",
        "谈判",
        "convince",
        "pitch",
        "objection",
        "objection handling",
        "incentive",
        "incentive alignment",
        "激励结构",
        "pricing power",
        "narrative",
        "framing",
        "attention arbitrage",
        "public narrative",
    ),
}
_SECONDARY_THEME_RULES: dict[str, tuple[str, ...]] = {
    "first_principles": ("第一性原理", "first principles"),
    "cost_structure": ("成本结构", "cost structure", "idiot index", "白痴指数"),
    "inversion": ("逆向思考", "inversion"),
    "specific_knowledge": ("特定知识", "specific knowledge"),
    "creator_growth": ("creator growth", "内容增长", "retention", "thumbnail", "hook", "ctr"),
    "social_publishing": (
        "social publishing",
        "wechat",
        "weixin",
        "official account",
        "public account",
        "公众号",
        "微信公众号",
        "公众号文章",
        "微信文章",
        "图文",
        "publishing",
        "publish",
    ),
    "organizational_design": ("组织设计", "organizational design", "组织效率"),
    "attention_arbitrage": ("attention arbitrage", "注意力套利", "注意力经济"),
    "cognitive_bias": ("认知偏误", "bias", "biases", "lollapalooza"),
    "leverage": ("杠杆", "leverage"),
    "talent_density": ("talent density", "人才密度"),
}
_SECONDARY_THEME_PRIMARY_MAP: dict[str, tuple[str, ...]] = {
    "first_principles": ("decision_quality", "product_strategy"),
    "cost_structure": ("product_strategy", "wealth_money"),
    "inversion": ("decision_quality",),
    "specific_knowledge": ("wealth_money", "career_learning"),
    "creator_growth": ("content_media", "startup_growth"),
    "social_publishing": ("content_media", "writing_communication"),
    "organizational_design": ("organization_leadership",),
    "attention_arbitrage": ("content_media", "wealth_money"),
    "cognitive_bias": ("decision_quality",),
    "leverage": ("wealth_money", "startup_growth"),
    "talent_density": ("organization_leadership",),
}
_THEME_HEAVY_CLASSES = {"advisor_or_perspective", "methodology_or_tutorial", "skill_authoring"}


class SkillLoader:
    _skills_registry: dict[str, dict] = {}
    _skills_fingerprint: str = ""
    _skills_manifest: dict[str, dict[str, Any]] = {}
    _skills_root_signature: str = ""
    _skills_revision: str = ""
    _root_inventory_states: dict[str, dict[str, Any]] = {}
    _visible_inventory_cache: dict[str, dict[str, Any]] = {}
    _skills_roots: list[Path] = []
    _skills_root_descriptors: list[dict[str, Any]] = []
    _recent_skill_discovery: list[dict[str, Any]] = []
    _last_reload_result: dict[str, Any] = {}
    _last_check_at: float = 0.0
    _check_interval_seconds: float = 0.75
    _background_refresh_timeout_ms: int = 1500
    _dirty_root_paths: set[str] = set()
    _startup_state: str = "cold"
    _snapshot_freshness: str = "cold"
    _last_refresh_at: str | None = None
    _last_refresh_error: str | None = None
    _background_refresh_task: asyncio.Task | None = None
    _background_refresh_in_progress: bool = False

    @classmethod
    def _normalize_hint_items(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            raw_items = _HINT_SPLIT_RE.split(value)
        else:
            raw_items = list(value or [])
        seen: set[str] = set()
        items: list[str] = []
        for item in raw_items:
            normalized = str(item or "").strip()
            lowered = normalized.lower()
            if not normalized or lowered in seen:
                continue
            seen.add(lowered)
            items.append(normalized)
        return items

    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _TEXT_WHITESPACE_RE.sub(" ", str(value or "").strip().lower())

    @classmethod
    def _match_phrase(cls, text: str, phrase: str) -> bool:
        normalized_text = cls._normalize_text(text)
        normalized_phrase = cls._normalize_text(phrase)
        if not normalized_text or not normalized_phrase:
            return False
        if _CJK_RE.search(normalized_phrase):
            return normalized_phrase in normalized_text
        escaped = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
        pattern = rf"(?<![0-9a-z]){escaped}(?![0-9a-z])"
        return re.search(pattern, normalized_text) is not None

    @classmethod
    def _collect_rule_hits(cls, text: str, rules: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:
        hits: dict[str, list[str]] = {}
        for key, needles in rules.items():
            matched = [
                str(needle).strip()
                for needle in needles
                if str(needle).strip() and cls._match_phrase(text, str(needle))
            ]
            if matched:
                hits[key] = matched
        return hits

    @classmethod
    def _skill_match_description_preview(cls, description: str) -> str:
        lines = [line.strip() for line in str(description or "").splitlines() if line.strip()]
        if not lines:
            return ""
        return lines[0][:220]

    @classmethod
    def _skill_match_query_variants(cls, identifier: str) -> list[str]:
        normalized_identifier = cls._normalize_text(identifier)
        if not normalized_identifier:
            return []
        variants = [normalized_identifier]
        for key, synonyms in _SKILL_MATCH_QUERY_SYNONYMS.items():
            if cls._match_phrase(normalized_identifier, key):
                variants.extend(cls._normalize_text(item) for item in synonyms if str(item).strip())
        for token in _SKILL_MATCH_TOKEN_RE.findall(normalized_identifier):
            if token in _SKILL_MATCH_QUERY_SYNONYMS:
                variants.extend(cls._normalize_text(item) for item in _SKILL_MATCH_QUERY_SYNONYMS[token] if str(item).strip())
        ordered: list[str] = []
        seen: set[str] = set()
        for item in variants:
            normalized = cls._normalize_text(item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered

    @classmethod
    def _skill_match_candidate_score(cls, term: str, candidate: str) -> int:
        normalized_term = cls._normalize_text(term)
        normalized_candidate = cls._normalize_text(candidate)
        if not normalized_term or not normalized_candidate:
            return 0
        if normalized_term == normalized_candidate:
            return 24
        if cls._match_phrase(candidate, term):
            return 18
        if cls._match_phrase(term, candidate):
            return 14
        if "xx" in normalized_candidate:
            wildcard_parts = [part for part in normalized_candidate.split("xx") if part]
            if wildcard_parts and all(part in normalized_term for part in wildcard_parts):
                return 18 if wildcard_parts[0] and normalized_term.startswith(wildcard_parts[0]) else 14
        if "xx" in normalized_term:
            wildcard_parts = [part for part in normalized_term.split("xx") if part]
            if wildcard_parts and all(part in normalized_candidate for part in wildcard_parts):
                return 18
        ratio = SequenceMatcher(None, normalized_term, normalized_candidate).ratio()
        if ratio >= 0.92:
            return 12
        if ratio >= 0.84:
            return 9
        if ratio >= 0.74 and (normalized_term in normalized_candidate or normalized_candidate in normalized_term):
            return 7
        term_tokens = {token for token in _SKILL_MATCH_TOKEN_RE.findall(normalized_term) if token}
        candidate_tokens = {token for token in _SKILL_MATCH_TOKEN_RE.findall(normalized_candidate) if token}
        overlap = len(term_tokens.intersection(candidate_tokens))
        if overlap > 0:
            return 5 + (2 * overlap)
        return 0

    @classmethod
    def _score_skill_match_entry(cls, entry: dict[str, Any], query_variants: list[str]) -> int:
        if not query_variants:
            return 0
        weighted_fields = [
            (str(entry.get("skillName") or entry.get("name") or "").strip(), 1.45),
            (str(entry.get("folder") or "").strip(), 1.35),
            (cls._skill_match_description_preview(entry.get("description") or ""), 0.95),
        ]
        for key, weight in (
            ("aliases", 1.2),
            ("triggers", 1.15),
            ("keywords", 1.0),
            ("tags", 0.95),
        ):
            for item in cls._normalize_hint_items(entry.get(key)):
                weighted_fields.append((item, weight))
        score = 0
        for field_text, weight in weighted_fields:
            best_for_field = max(
                (cls._skill_match_candidate_score(term, field_text) for term in query_variants),
                default=0,
            )
            score += int(round(best_for_field * weight))
        return score

    @classmethod
    def _select_primary_keys(
        cls,
        *,
        scores: dict[str, float],
        max_items: int,
        minimum: float,
        dominance_ratio: float,
    ) -> list[str]:
        ranked = sorted(
            (
                (float(score), str(key))
                for key, score in scores.items()
                if float(score) >= minimum
            ),
            key=lambda item: (-item[0], item[1]),
        )
        if not ranked:
            return []
        top_score = ranked[0][0]
        threshold = max(minimum, top_score * dominance_ratio)
        return [key for score, key in ranked if score >= threshold][:max_items]

    @classmethod
    def _extract_intro_text(cls, body: str) -> str:
        lines = [line.strip() for line in str(body or "").splitlines() if line.strip()]
        return "\n".join(lines[:12])[:900]

    @classmethod
    def _extract_extension_evidence(cls, available_files: list[str]) -> list[str]:
        extension_terms: list[str] = []
        for candidate in list(available_files or []):
            normalized = str(candidate or "").strip().lower()
            if not normalized:
                continue
            suffix = Path(normalized).suffix
            if suffix in {".ppt", ".pptx", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"}:
                extension_terms.append(suffix)
        return cls._normalize_hint_items(extension_terms)

    @classmethod
    def _clean_template_noise(cls, text: str) -> str:
        cleaned = str(text or "")
        for pattern in _SKILL_TEMPLATE_NOISE_PATTERNS:
            cleaned = pattern.sub(" ", cleaned)
        return _TEXT_WHITESPACE_RE.sub(" ", cleaned).strip()

    @classmethod
    def _available_file_evidence_text(cls, available_files: list[str]) -> str:
        evidence_items: list[str] = []
        for candidate in list(available_files or []):
            raw = str(candidate or "").strip()
            if not raw:
                continue
            normalized = raw.replace("\\", "/").lower()
            if normalized.endswith("/skill.md") or normalized == "skill.md":
                continue
            evidence_items.append(raw)
        return " ".join(evidence_items)

    @classmethod
    def _extract_description_hint_fields(cls, description: str) -> dict[str, list[str]]:
        hints: dict[str, list[str]] = {"aliases": [], "triggers": [], "keywords": [], "tags": []}
        for raw_line in str(description or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            lowered = line.lower()
            target_key = ""
            if any(marker in lowered for marker in ("trigger", "triggers")) or "触发" in line or "触发词" in line:
                target_key = "triggers"
            elif any(marker in lowered for marker in ("alias", "aliases")) or "别名" in line:
                target_key = "aliases"
            elif any(marker in lowered for marker in ("capabilities", "capability", "use for")) or "用途" in line or "能力" in line:
                target_key = "keywords"
            if not target_key:
                continue
            quoted = re.findall(r"[「“\"]([^」”\"]+)[」”\"]", line)
            payload = line
            for marker in ("触发词", "触发", "Triggers", "triggers", "Aliases", "aliases", "Capabilities", "capabilities", "Use for", "use for", "用途", "能力"):
                index = payload.find(marker)
                if index >= 0:
                    payload = payload[index + len(marker):]
                    break
            payload = payload.split("：", 1)[-1].split(":", 1)[-1]
            payload_items = [] if quoted else re.split(r"[、，,;/；]+", payload)
            items = [*quoted, *payload_items]
            hints[target_key].extend(cls._normalize_hint_items(items))
        return hints

    @classmethod
    def _accumulate_weighted_rule_scores(
        cls,
        *,
        evidence_segments: list[tuple[str, float]],
        rules: dict[str, tuple[str, ...]],
    ) -> tuple[dict[str, float], dict[str, list[str]]]:
        scores: dict[str, float] = {}
        matches: dict[str, list[str]] = {}
        for text, weight in evidence_segments:
            if not str(text or "").strip() or weight <= 0:
                continue
            segment_hits = cls._collect_rule_hits(text, rules)
            for key, matched_terms in segment_hits.items():
                contribution = float(weight) * min(len(matched_terms), 2)
                scores[key] = scores.get(key, 0.0) + contribution
                bucket = matches.setdefault(key, [])
                for term in matched_terms:
                    if term not in bucket:
                        bucket.append(term)
        return scores, matches

    @classmethod
    def _derive_evidence_segments(
        cls,
        *,
        name: str,
        folder: str,
        description: str,
        body: str,
        available_files: list[str],
        aliases: list[str],
        triggers: list[str],
        keywords: list[str],
        tags: list[str],
    ) -> dict[str, list[tuple[str, float]]]:
        intro_text = cls._clean_template_noise(cls._extract_intro_text(body))
        cleaned_description = cls._clean_template_noise(description)
        cleaned_body = cls._clean_template_noise(str(body or "")[:3200])
        explicit_extensions = " ".join(cls._extract_extension_evidence(available_files))
        available_file_evidence = cls._available_file_evidence_text(available_files)
        strong_segments = [
            (name, 5.0),
            (folder, 4.5),
            (" ".join(aliases), 4.0),
            (" ".join(triggers), 4.0),
            (" ".join(keywords), 3.5),
            (" ".join(tags), 3.5),
            (explicit_extensions, 4.0),
        ]
        medium_segments = [
            (cleaned_description, 2.5),
            (intro_text, 1.75),
        ]
        weak_segments = [
            (cleaned_body, 0.75),
            (available_file_evidence, 0.5),
        ]
        return {
            "strong": [(text, weight) for text, weight in strong_segments if str(text or "").strip()],
            "medium": [(text, weight) for text, weight in medium_segments if str(text or "").strip()],
            "weak": [(text, weight) for text, weight in weak_segments if str(text or "").strip()],
        }

    @classmethod
    def _derive_interaction_mode(
        cls,
        *,
        primary_artifact_types: list[str],
        skill_class: str,
        operation_scores: dict[str, float],
        interaction_scores: dict[str, float],
    ) -> str:
        if skill_class == "advisor_or_perspective":
            return "advisory"
        if skill_class == "methodology_or_tutorial":
            return "reference_guidance"
        if skill_class == "skill_authoring":
            return "guided_workflow"
        if skill_class in {"workflow_or_script", "integration_or_tooling"}:
            return "workflow"
        if interaction_scores.get("media_workflow", 0.0) >= interaction_scores.get("file_workflow", 0.0) and interaction_scores.get("media_workflow", 0.0) >= 2.0:
            return "media_workflow"
        if interaction_scores.get("file_workflow", 0.0) >= 2.0:
            return "file_workflow"
        if primary_artifact_types:
            if any(item in {"video", "image", "audio"} for item in primary_artifact_types):
                return "media_workflow"
            return "file_workflow"
        if operation_scores.get("automate", 0.0) >= 2.0:
            return "workflow"
        return "general"

    @classmethod
    def _derive_profile_rules(
        cls,
        *,
        name: str,
        folder: str,
        description: str,
        body: str,
        available_files: list[str],
        aliases: list[str],
        triggers: list[str],
        keywords: list[str],
        tags: list[str],
        has_scripts: bool,
        has_templates: bool,
        has_examples: bool,
        has_assets: bool,
    ) -> dict[str, Any]:
        evidence = cls._derive_evidence_segments(
            name=name,
            folder=folder,
            description=description,
            body=body,
            available_files=available_files,
            aliases=aliases,
            triggers=triggers,
            keywords=keywords,
            tags=tags,
        )
        strong_medium_segments = [*evidence["strong"], *evidence["medium"]]
        all_segments = [*strong_medium_segments, *evidence["weak"]]

        artifact_scores_primary, artifact_matches_primary = cls._accumulate_weighted_rule_scores(
            evidence_segments=strong_medium_segments,
            rules=_ARTIFACT_RULES,
        )
        artifact_scores_all, artifact_matches_all = cls._accumulate_weighted_rule_scores(
            evidence_segments=all_segments,
            rules=_ARTIFACT_RULES,
        )
        operation_scores_primary, operation_matches_primary = cls._accumulate_weighted_rule_scores(
            evidence_segments=strong_medium_segments,
            rules=_OPERATION_RULES,
        )
        operation_scores_all, operation_matches_all = cls._accumulate_weighted_rule_scores(
            evidence_segments=all_segments,
            rules=_OPERATION_RULES,
        )
        class_scores, class_matches = cls._accumulate_weighted_rule_scores(
            evidence_segments=strong_medium_segments,
            rules=_SKILL_CLASS_KEYWORDS,
        )
        interaction_scores, _ = cls._accumulate_weighted_rule_scores(
            evidence_segments=strong_medium_segments,
            rules=_INTERACTION_MODE_KEYWORDS,
        )

        primary_artifact_types = cls._select_primary_keys(
            scores=artifact_scores_primary,
            max_items=_PRIMARY_ARTIFACT_LIMIT,
            minimum=3.5,
            dominance_ratio=0.7,
        )
        primary_operations = cls._select_primary_keys(
            scores=operation_scores_primary,
            max_items=_PRIMARY_OPERATION_LIMIT,
            minimum=3.0,
            dominance_ratio=0.6,
        )
        secondary_artifact_hints = [
            key
            for key, _score in sorted(
                artifact_scores_all.items(),
                key=lambda item: (-float(item[1]), item[0]),
            )
            if key not in primary_artifact_types
        ][:_SECONDARY_HINT_LIMIT]
        secondary_operation_hints = [
            key
            for key, _score in sorted(
                operation_scores_all.items(),
                key=lambda item: (-float(item[1]), item[0]),
            )
            if key not in primary_operations
        ][:_SECONDARY_HINT_LIMIT]

        skill_class = "general"
        if class_scores:
            ranked_classes = sorted(class_scores.items(), key=lambda item: (-float(item[1]), item[0]))
            top_class, top_class_score = ranked_classes[0]
            second_class_score = float(ranked_classes[1][1]) if len(ranked_classes) > 1 else 0.0
            if top_class_score >= 3.5 and top_class_score >= (second_class_score + 1.0):
                skill_class = str(top_class)
        if skill_class == "general":
            if primary_artifact_types:
                if "create" in primary_operations:
                    skill_class = "artifact_producer"
                elif any(item in primary_operations for item in ("edit", "analyze", "convert")):
                    skill_class = "artifact_editor_or_analyzer"
                elif has_scripts:
                    skill_class = "workflow_or_script"
                else:
                    skill_class = "artifact_producer"
            elif has_scripts or "automate" in primary_operations:
                skill_class = "workflow_or_script"
            elif "advise" in primary_operations:
                skill_class = "advisor_or_perspective"
            elif "guide" in primary_operations:
                skill_class = "methodology_or_tutorial"
            elif has_templates or has_examples:
                skill_class = "methodology_or_tutorial"
        if skill_class == "workflow_or_script":
            document_evidence_terms = {
                str(term or "").strip().lower()
                for term in list(artifact_matches_all.get("document") or [])
                if str(term or "").strip()
            }
            if (
                "create" in primary_operations
                and artifact_scores_all.get("document", 0.0) >= 2.0
                and document_evidence_terms.intersection(
                    {"article", "document", "doc", "docx", "md", "markdown", "report", "html", "文章", "文档", "报告"}
                )
            ):
                primary_artifact_types = ["document", *[item for item in primary_artifact_types if item != "document"]]
                primary_artifact_types = primary_artifact_types[:_PRIMARY_ARTIFACT_LIMIT]
        if skill_class == "workflow_or_script" and not primary_artifact_types:
            should_promote_artifact = bool(
                "create" in primary_operations
                or artifact_scores_all.get("document", 0.0) >= 2.0
                or artifact_scores_all.get("video", 0.0) >= 2.0
                or artifact_scores_all.get("image", 0.0) >= 2.0
                or artifact_scores_all.get("presentation", 0.0) >= 2.0
                or artifact_scores_all.get("audio", 0.0) >= 2.0
                or has_templates
                or has_assets
            )
            if should_promote_artifact:
                promoted_artifacts = [
                    item
                    for item in cls._select_primary_keys(
                        scores=artifact_scores_all,
                        max_items=_PRIMARY_ARTIFACT_LIMIT,
                        minimum=2.0,
                        dominance_ratio=0.55,
                    )
                    if item != "skill"
                ]
                if (
                    "create" in primary_operations
                    and artifact_scores_all.get("document", 0.0) >= 2.0
                    and document_evidence_terms.intersection(
                        {"article", "document", "doc", "docx", "md", "markdown", "report", "html", "文章", "文档", "报告"}
                    )
                ):
                    promoted_artifacts = ["document", *[item for item in promoted_artifacts if item != "document"]]
                    promoted_artifacts = promoted_artifacts[:_PRIMARY_ARTIFACT_LIMIT]
                if promoted_artifacts:
                    primary_artifact_types = promoted_artifacts
        if skill_class == "skill_authoring":
            if "skill" not in primary_artifact_types:
                primary_artifact_types = ["skill", *primary_artifact_types][:_PRIMARY_ARTIFACT_LIMIT]
            if "create" not in primary_operations:
                primary_operations = ["create", *primary_operations][:_PRIMARY_OPERATION_LIMIT]
        if skill_class in {"advisor_or_perspective", "methodology_or_tutorial", "integration_or_tooling"}:
            primary_artifact_types = []
        interaction_mode = cls._derive_interaction_mode(
            primary_artifact_types=primary_artifact_types,
            skill_class=skill_class,
            operation_scores=operation_scores_primary,
            interaction_scores=interaction_scores,
        )
        evidence_strength = max(
            list(artifact_scores_primary.values())
            + list(operation_scores_primary.values())
            + list(class_scores.values())
            + [0.0]
        )
        confidence = 0.2
        if primary_artifact_types:
            confidence += 0.24 + (0.07 * min(len(primary_artifact_types), 2))
        if primary_operations:
            confidence += 0.14 + (0.04 * min(len(primary_operations), 3))
        if skill_class != "general":
            confidence += 0.12
        if aliases or triggers or keywords or tags:
            confidence += 0.08
        if evidence_strength >= 6.0:
            confidence += 0.12
        elif evidence_strength >= 4.0:
            confidence += 0.06
        if has_scripts and skill_class == "workflow_or_script":
            confidence += 0.05
        if has_templates and skill_class in {"artifact_producer", "artifact_editor_or_analyzer"}:
            confidence += 0.04
        if has_examples and skill_class in {"methodology_or_tutorial", "advisor_or_perspective"}:
            confidence += 0.03
        if has_assets and interaction_mode == "media_workflow":
            confidence += 0.03
        return {
            "skillClass": skill_class,
            "primaryArtifactTypes": primary_artifact_types,
            "primaryOperations": primary_operations,
            "interactionMode": interaction_mode,
            "capabilityConfidence": round(max(0.0, min(confidence, 0.98)), 3),
            "profileSource": "rules",
            "secondaryArtifactHints": secondary_artifact_hints,
            "secondaryOperationHints": secondary_operation_hints,
            "evidenceSignals": {
                "artifactMatches": {key: artifact_matches_primary.get(key, []) for key in primary_artifact_types},
                "operationMatches": {key: operation_matches_primary.get(key, []) for key in primary_operations},
                "classMatches": class_matches,
                "secondaryArtifacts": {key: artifact_matches_all.get(key, []) for key in secondary_artifact_hints},
                "secondaryOperations": {key: operation_matches_all.get(key, []) for key in secondary_operation_hints},
            },
        }

    @classmethod
    def _derive_theme_profile_rules(
        cls,
        *,
        name: str,
        folder: str,
        description: str,
        body: str,
        available_files: list[str],
        aliases: list[str],
        triggers: list[str],
        keywords: list[str],
        tags: list[str],
        skill_class: str,
    ) -> dict[str, Any]:
        evidence = cls._derive_evidence_segments(
            name=name,
            folder=folder,
            description=description,
            body=body,
            available_files=available_files,
            aliases=aliases,
            triggers=triggers,
            keywords=keywords,
            tags=tags,
        )
        if skill_class in _THEME_HEAVY_CLASSES:
            primary_segments = [*evidence["strong"], *evidence["medium"]]
            primary_minimum = 2.5
            primary_ratio = 0.58
            secondary_minimum = 1.5
        else:
            primary_segments = list(evidence["strong"])
            primary_minimum = 3.8
            primary_ratio = 0.7
            secondary_minimum = 2.0

        secondary_segments = [*primary_segments, *evidence["weak"]]
        theme_scores, theme_matches = cls._accumulate_weighted_rule_scores(
            evidence_segments=primary_segments,
            rules=_PRIMARY_THEME_RULES,
        )
        secondary_scores, secondary_matches = cls._accumulate_weighted_rule_scores(
            evidence_segments=secondary_segments,
            rules=_SECONDARY_THEME_RULES,
        )

        primary_themes = cls._select_primary_keys(
            scores=theme_scores,
            max_items=_PRIMARY_THEME_LIMIT,
            minimum=primary_minimum,
            dominance_ratio=primary_ratio,
        )
        secondary_theme_tags = [
            key
            for key, score in sorted(secondary_scores.items(), key=lambda item: (-float(item[1]), item[0]))
            if float(score) >= secondary_minimum
        ][:_SECONDARY_THEME_LIMIT]
        evidence_strength = max(list(theme_scores.values()) + list(secondary_scores.values()) + [0.0])
        confidence = 0.1
        if primary_themes:
            confidence += 0.28 + (0.08 * min(len(primary_themes), 2))
        if secondary_theme_tags:
            confidence += 0.12 + (0.03 * min(len(secondary_theme_tags), 3))
        if skill_class in _THEME_HEAVY_CLASSES:
            confidence += 0.08
        if evidence_strength >= 6.0:
            confidence += 0.12
        elif evidence_strength >= 3.5:
            confidence += 0.06
        return {
            "primaryThemes": primary_themes,
            "secondaryThemeTags": secondary_theme_tags,
            "themeConfidence": round(max(0.0, min(confidence, 0.98)), 3),
            "themeSource": "rules",
            "themeEvidenceSignals": {
                "primaryThemeMatches": {key: theme_matches.get(key, []) for key in primary_themes},
                "secondaryThemeMatches": {key: secondary_matches.get(key, []) for key in secondary_theme_tags},
            },
        }

    @classmethod
    def _should_attempt_llm_theme_inference(
        cls,
        *,
        base_theme_profile: dict[str, Any],
        skill_class: str,
    ) -> bool:
        normalized_skill_class = str(skill_class or "").strip()
        if normalized_skill_class not in _THEME_HEAVY_CLASSES:
            return False
        config = storage.get_extensions_config() or {}
        policy = dict((config.get("prefilterPolicy") or {}))
        if not bool(policy.get("enabled")):
            return False
        if not str(model_control_plane.get_role_model_id("extensions_prefilter") or "").strip():
            return False
        confidence = float(base_theme_profile.get("themeConfidence") or 0.0)
        primary_themes = list(base_theme_profile.get("primaryThemes") or [])
        secondary_theme_tags = list(base_theme_profile.get("secondaryThemeTags") or [])
        if normalized_skill_class == "methodology_or_tutorial" and not primary_themes and not secondary_theme_tags:
            return False
        if skill_class in _THEME_HEAVY_CLASSES and not primary_themes:
            return True
        if confidence < 0.56:
            return True
        if len(primary_themes) > 1 and confidence < 0.72:
            return True
        return False

    @classmethod
    def _infer_theme_with_llm(
        cls,
        *,
        name: str,
        description: str,
        body: str,
        aliases: list[str],
        triggers: list[str],
        keywords: list[str],
        tags: list[str],
        skill_class: str,
        base_theme_profile: dict[str, Any],
    ) -> dict[str, Any] | None:
        prompt_payload = json.dumps(
            {
                "skillName": name,
                "description": description,
                "aliases": aliases,
                "triggers": triggers,
                "keywords": keywords,
                "tags": tags,
                "bodyPreview": cls._extract_intro_text(body),
                "skillClass": skill_class,
                "currentThemeProfile": base_theme_profile,
                "allowedPrimaryThemes": list(_PRIMARY_THEME_RULES.keys()),
                "tagExamples": list(_SECONDARY_THEME_RULES.keys()),
            },
            ensure_ascii=False,
        )

        def _invoke() -> dict[str, Any] | None:
            model = llm_factory.create_for_role("extensions_prefilter", streaming=False, temperature=0)
            prepared = prepare_background_model_messages(
                system_prompt=(
                    "你是 V8 Agent OS 的 skill 主题画像归一器。\n"
                    "任务：为顾问类、方法论类或其他 skill 输出稳定的主题画像。\n"
                    "只返回 JSON："
                    "{\"primaryThemes\":[...],\"secondaryThemeTags\":[...],\"themeConfidence\":0.0}\n"
                    "要求：primaryThemes 最多 2 个；secondaryThemeTags 最多 5 个；"
                    "不要输出解释文本。"
                ),
                instruction="根据已准备的 skill 材料输出唯一 JSON 对象。",
                materials=[
                    {
                        "title": "Skill theme profile payload",
                        "kind": "skill_theme_profile_payload",
                        "content": prompt_payload,
                    }
                ],
                runtime_kind="extensions",
                target_role="extensions:skill_theme_profile",
                resolved_model_id="extensions_prefilter",
                component="extensions",
                node="skill_theme_profile_context",
            )
            response = model.invoke(prepared.messages, config={"callbacks": []})
            payload, _sanitized, _error = parse_background_json_object(response)
            return payload if isinstance(payload, dict) else None

        try:
            return _PROFILE_INFERENCE_EXECUTOR.submit(_invoke).result(timeout=_PROFILE_LLM_TIMEOUT_SECONDS)
        except Exception:
            return None

    @classmethod
    def _normalize_theme_profile_with_fallback(
        cls,
        *,
        payload: dict[str, Any] | None,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(fallback)
        if isinstance(payload, dict):
            primary_themes = [
                str(item).strip()
                for item in list(payload.get("primaryThemes") or [])
                if str(item).strip() in _PRIMARY_THEME_RULES
            ][:_PRIMARY_THEME_LIMIT]
            secondary_theme_tags = cls._normalize_hint_items(payload.get("secondaryThemeTags"))[:_SECONDARY_THEME_LIMIT]
            if primary_themes:
                normalized["primaryThemes"] = primary_themes
            if secondary_theme_tags:
                normalized["secondaryThemeTags"] = secondary_theme_tags
            try:
                confidence = float(payload.get("themeConfidence"))
                normalized["themeConfidence"] = round(max(0.0, min(confidence, 0.99)), 3)
            except (TypeError, ValueError):
                pass
            normalized["themeSource"] = "llm_assisted"
            normalized.setdefault("themeEvidenceSignals", {})
        return normalized

    @classmethod
    def _derive_theme_profile(
        cls,
        *,
        name: str,
        folder: str,
        description: str,
        body: str,
        available_files: list[str],
        aliases: list[str],
        triggers: list[str],
        keywords: list[str],
        tags: list[str],
        skill_class: str,
    ) -> dict[str, Any]:
        base_theme_profile = cls._derive_theme_profile_rules(
            name=name,
            folder=folder,
            description=description,
            body=body,
            available_files=available_files,
            aliases=aliases,
            triggers=triggers,
            keywords=keywords,
            tags=tags,
            skill_class=skill_class,
        )
        if not cls._should_attempt_llm_theme_inference(
            base_theme_profile=base_theme_profile,
            skill_class=skill_class,
        ):
            return base_theme_profile
        llm_theme_profile = cls._infer_theme_with_llm(
            name=name,
            description=description,
            body=body,
            aliases=aliases,
            triggers=triggers,
            keywords=keywords,
            tags=tags,
            skill_class=skill_class,
            base_theme_profile=base_theme_profile,
        )
        return cls._normalize_theme_profile_with_fallback(
            payload=llm_theme_profile,
            fallback=base_theme_profile,
        )

    @classmethod
    def _should_attempt_llm_profile_inference(cls, *, base_profile: dict[str, Any]) -> bool:
        config = storage.get_extensions_config() or {}
        policy = dict((config.get("prefilterPolicy") or {}))
        if not bool(policy.get("enabled")):
            return False
        if not str(model_control_plane.get_role_model_id("extensions_prefilter") or "").strip():
            return False
        confidence = float(base_profile.get("capabilityConfidence") or 0.0)
        if confidence < 0.58:
            return True
        if str(base_profile.get("skillClass") or "").strip() in {"general", ""}:
            return True
        if not list(base_profile.get("primaryArtifactTypes") or []) and not list(base_profile.get("primaryOperations") or []):
            return True
        return False

    @classmethod
    def _infer_profile_with_llm(
        cls,
        *,
        name: str,
        description: str,
        body: str,
        available_files: list[str],
        base_profile: dict[str, Any],
    ) -> dict[str, Any] | None:
        prompt_payload = json.dumps(
            {
                "skillName": name,
                "description": description,
                "availableFiles": list(available_files or []),
                "bodyPreview": str(body or "")[:2400],
                "currentProfile": base_profile,
                "allowedSkillClasses": [
                    "artifact_producer",
                    "artifact_editor_or_analyzer",
                    "skill_authoring",
                    "workflow_or_script",
                    "methodology_or_tutorial",
                    "advisor_or_perspective",
                    "integration_or_tooling",
                    "general",
                ],
                "allowedArtifactTypes": list(_ARTIFACT_RULES.keys()),
                "allowedOperations": list(_OPERATION_RULES.keys()),
                "allowedInteractionModes": [
                    "advisory",
                    "reference_guidance",
                    "workflow",
                    "file_workflow",
                    "media_workflow",
                    "guided_workflow",
                    "multi_agent_swarm",
                    "general",
                ],
            },
            ensure_ascii=False,
        )

        def _invoke() -> dict[str, Any] | None:
            model = llm_factory.create_for_role("extensions_prefilter", streaming=False, temperature=0)
            prepared = prepare_background_model_messages(
                system_prompt=(
                    "你是 V8 Agent OS 的 skill 能力画像归一器。\n"
                    "任务：根据 skill 的名称、描述、结构与正文，输出稳定的内部能力画像。\n"
                    "只返回 JSON："
                    "{\"skillClass\":\"...\",\"primaryArtifactTypes\":[...],\"primaryOperations\":[...],"
                    "\"interactionMode\":\"...\",\"capabilityConfidence\":0.0}\n"
                    "要求：skillClass 只能选一个；primaryArtifactTypes 最多 2 项；"
                    "primaryOperations 最多 3 项；如果不是文件/媒体产物型 skill，primaryArtifactTypes 可以为空；不要输出解释。"
                ),
                instruction="根据已准备的 skill 材料输出唯一 JSON 对象。",
                materials=[
                    {
                        "title": "Skill capability profile payload",
                        "kind": "skill_capability_profile_payload",
                        "content": prompt_payload,
                    }
                ],
                runtime_kind="extensions",
                target_role="extensions:skill_capability_profile",
                resolved_model_id="extensions_prefilter",
                component="extensions",
                node="skill_capability_profile_context",
            )
            response = model.invoke(prepared.messages, config={"callbacks": []})
            payload, _sanitized, _error = parse_background_json_object(response)
            return payload if isinstance(payload, dict) else None

        try:
            return _PROFILE_INFERENCE_EXECUTOR.submit(_invoke).result(timeout=_PROFILE_LLM_TIMEOUT_SECONDS)
        except Exception:
            return None

    @classmethod
    def _normalize_profile_with_fallback(
        cls,
        *,
        payload: dict[str, Any] | None,
        fallback: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(fallback)
        if isinstance(payload, dict):
            skill_class = str(payload.get("skillClass") or "").strip()
            if skill_class:
                normalized["skillClass"] = skill_class
            primary_artifact_types = [
                str(item).strip()
                for item in list(payload.get("primaryArtifactTypes") or [])
                if str(item).strip()
            ][:_PRIMARY_ARTIFACT_LIMIT]
            if primary_artifact_types or skill_class in {"artifact_producer", "artifact_editor_or_analyzer"}:
                normalized["primaryArtifactTypes"] = primary_artifact_types
            primary_operations = [
                str(item).strip()
                for item in list(payload.get("primaryOperations") or [])
                if str(item).strip()
            ][:_PRIMARY_OPERATION_LIMIT]
            if primary_operations:
                normalized["primaryOperations"] = primary_operations
            interaction_mode = str(payload.get("interactionMode") or "").strip()
            if interaction_mode:
                normalized["interactionMode"] = interaction_mode
            try:
                confidence = float(payload.get("capabilityConfidence"))
                normalized["capabilityConfidence"] = round(max(0.0, min(confidence, 0.99)), 3)
            except (TypeError, ValueError):
                pass
            normalized["profileSource"] = "llm_assisted"
            normalized.setdefault("secondaryArtifactHints", [])
            normalized.setdefault("secondaryOperationHints", [])
            normalized.setdefault("evidenceSignals", {})
        return normalized

    @classmethod
    def _derive_capability_profile(
        cls,
        *,
        name: str,
        folder: str,
        description: str,
        body: str,
        available_files: list[str],
        aliases: list[str],
        triggers: list[str],
        keywords: list[str],
        tags: list[str],
        has_scripts: bool,
        has_templates: bool,
        has_examples: bool,
        has_assets: bool,
    ) -> dict[str, Any]:
        base_profile = cls._derive_profile_rules(
            name=name,
            folder=folder,
            description=description,
            body=body,
            available_files=available_files,
            aliases=aliases,
            triggers=triggers,
            keywords=keywords,
            tags=tags,
            has_scripts=has_scripts,
            has_templates=has_templates,
            has_examples=has_examples,
            has_assets=has_assets,
        )
        if not cls._should_attempt_llm_profile_inference(base_profile=base_profile):
            return base_profile
        llm_profile = cls._infer_profile_with_llm(
            name=name,
            description=description,
            body=body,
            available_files=available_files,
            base_profile=base_profile,
        )
        return cls._normalize_profile_with_fallback(payload=llm_profile, fallback=base_profile)

    @classmethod
    def _derive_capability_tags(
        cls,
        *,
        capability_profile: dict[str, Any],
        theme_profile: dict[str, Any],
        aliases: list[str],
        triggers: list[str],
        keywords: list[str],
        tags: list[str],
        has_scripts: bool,
        has_templates: bool,
        has_examples: bool,
        has_assets: bool,
    ) -> dict[str, Any]:
        skill_class = str(capability_profile.get("skillClass") or "general").strip() or "general"
        primary_artifacts = cls._normalize_hint_items(capability_profile.get("primaryArtifactTypes"))
        secondary_artifacts = cls._normalize_hint_items(capability_profile.get("secondaryArtifactHints"))
        primary_operations = cls._normalize_hint_items(capability_profile.get("primaryOperations"))
        secondary_operations = cls._normalize_hint_items(capability_profile.get("secondaryOperationHints"))
        topic_themes = cls._normalize_hint_items(theme_profile.get("primaryThemes"))
        language_aliases = cls._normalize_hint_items([*aliases, *triggers, *keywords, *tags])

        capability_kind = [skill_class]
        if skill_class == "skill_authoring":
            capability_kind.extend(["research_workflow", "advisor"])
        artifact_types = list(primary_artifacts)
        if skill_class == "skill_authoring":
            for artifact in ("skill", "persona_skill"):
                if artifact not in artifact_types:
                    artifact_types.append(artifact)
        operation_tags = list(primary_operations)
        if skill_class == "skill_authoring":
            for operation in ("search", "extract", "synthesize", "create", "verify", "orchestrate"):
                if operation not in operation_tags:
                    operation_tags.append(operation)
        side_effect_level: list[str] = []
        if has_scripts:
            side_effect_level.append("executes_command")
        if skill_class == "skill_authoring":
            side_effect_level.append("writes_skill_home")
            side_effect_level.append("external_network")
        elif "search" in operation_tags:
            side_effect_level.append("external_network")
        runtime_affinity: list[str] = []
        if has_scripts:
            runtime_affinity.append("command_session")
        if skill_class == "skill_authoring":
            runtime_affinity.append("subagent_swarm")
        if has_templates:
            runtime_affinity.append("template_assets")
        if has_assets:
            runtime_affinity.append("media_assets")

        return {
            "capabilityKind": cls._normalize_hint_items(capability_kind),
            "artifactTypes": cls._normalize_hint_items(artifact_types),
            "operationTags": cls._normalize_hint_items([*operation_tags, *secondary_operations[:2]]),
            "topicThemes": cls._normalize_hint_items([*topic_themes, *cls._normalize_hint_items(theme_profile.get("secondaryThemeTags"))[:2]]),
            "interactionMode": str(capability_profile.get("interactionMode") or "general").strip() or "general",
            "evidenceMode": {
                "profileSource": str(capability_profile.get("profileSource") or "rules"),
                "themeSource": str(theme_profile.get("themeSource") or "rules"),
                "hasScripts": bool(has_scripts),
                "hasTemplates": bool(has_templates),
                "hasExamples": bool(has_examples),
                "hasAssets": bool(has_assets),
            },
            "sideEffectLevel": cls._normalize_hint_items(side_effect_level),
            "runtimeAffinity": cls._normalize_hint_items(runtime_affinity),
            "languageAliases": language_aliases,
            "trustAndProvenance": {
                "capabilityConfidence": capability_profile.get("capabilityConfidence"),
                "themeConfidence": theme_profile.get("themeConfidence"),
            },
        }

    @classmethod
    def _now_iso(cls) -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _cache_file(cls) -> Path:
        return skill_inventory_cache_path()

    @classmethod
    def _legacy_cache_file(cls) -> Path:
        return legacy_skill_inventory_cache_path()

    @classmethod
    def _resolve_repo_root(cls) -> Path:
        current = Path(__file__).resolve()
        for ancestor in current.parents:
            if (ancestor / ".agents").exists() and (ancestor / "apps").exists():
                return ancestor
        return current.parents[5]

    @classmethod
    def _normalize_path(cls, value: str | Path | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        return str(Path(raw).expanduser().resolve(strict=False))

    @classmethod
    def _path_is_within(cls, child: str | Path | None, parent: str | Path | None) -> bool:
        child_path = cls._normalize_path(child).rstrip("\\/")
        parent_path = cls._normalize_path(parent).rstrip("\\/")
        if not child_path or not parent_path:
            return False
        if child_path == parent_path:
            return True
        return child_path.startswith(parent_path + "\\") or child_path.startswith(parent_path + "/")

    @classmethod
    def _entry_belongs_to_root_descriptor(cls, entry: dict[str, Any], descriptor: dict[str, Any]) -> bool:
        entry_root = cls._normalize_path(entry.get("rootPath") or entry.get("skillRoot") or entry.get("path"))
        descriptor_root = cls._normalize_path(descriptor.get("rootPath"))
        return cls._path_is_within(entry_root, descriptor_root)

    @classmethod
    def _build_root_descriptor(
        cls,
        *,
        root_path: Path,
        source_type: str,
        visibility: str,
        workspace_path: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_root = cls._normalize_path(root_path)
        return {
            "rootPath": normalized_root,
            "sourceType": source_type,
            "workspacePath": cls._normalize_path(workspace_path),
            "workspaceId": str(workspace_id or "").strip() or None,
            "projectId": str(project_id or "").strip() or None,
            "visibility": visibility,
        }

    @classmethod
    def _dedupe_root_descriptors(cls, descriptors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for descriptor in descriptors:
            root_path = cls._normalize_path(descriptor.get("rootPath"))
            if not root_path or root_path in seen:
                continue
            seen.add(root_path)
            normalized = dict(descriptor)
            normalized["rootPath"] = root_path
            normalized["workspacePath"] = cls._normalize_path(normalized.get("workspacePath"))
            normalized["workspaceId"] = str(normalized.get("workspaceId") or "").strip() or None
            normalized["projectId"] = str(normalized.get("projectId") or "").strip() or None
            normalized["sourceType"] = str(normalized.get("sourceType") or "global").strip() or "global"
            normalized["visibility"] = str(normalized.get("visibility") or "global").strip() or "global"
            deduped.append(normalized)
        return deduped

    @classmethod
    def _root_descriptors_signature(cls, descriptors: list[dict[str, Any]]) -> str:
        normalized = [
            {
                "rootPath": cls._normalize_path(item.get("rootPath")),
                "sourceType": str(item.get("sourceType") or "global").strip() or "global",
                "visibility": str(item.get("visibility") or "global").strip() or "global",
                "workspacePath": cls._normalize_path(item.get("workspacePath")),
                "workspaceId": str(item.get("workspaceId") or "").strip() or None,
                "projectId": str(item.get("projectId") or "").strip() or None,
            }
            for item in cls._dedupe_root_descriptors(descriptors)
        ]
        payload = json.dumps(sorted(normalized, key=lambda item: item["rootPath"]), ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    @classmethod
    def _compute_manifest(cls, descriptors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        manifest: dict[str, dict[str, Any]] = {}
        for descriptor in cls._dedupe_root_descriptors(descriptors):
            root_path = cls._normalize_path(descriptor.get("rootPath"))
            root = Path(root_path)
            if not root.exists() or not root.is_dir():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                try:
                    stat = skill_file.stat()
                except OSError:
                    continue
                instruction_path = cls._normalize_path(skill_file)
                skill_root = cls._normalize_path(skill_file.parent)
                key = instruction_path
                content_hash = cls._file_sha1(skill_file)
                manifest_hash = cls._skill_directory_manifest_hash(skill_file.parent, skill_file)
                manifest[key] = {
                    "key": key,
                    "manifestKey": key,
                    "instructionPath": instruction_path,
                    "skillRoot": skill_root,
                    "folder": skill_file.parent.name,
                    "rootPath": root_path,
                    "sourceType": str(descriptor.get("sourceType") or "global").strip() or "global",
                    "visibility": str(descriptor.get("visibility") or "global").strip() or "global",
                    "workspacePath": cls._normalize_path(descriptor.get("workspacePath")),
                    "workspaceId": str(descriptor.get("workspaceId") or "").strip() or None,
                    "projectId": str(descriptor.get("projectId") or "").strip() or None,
                    "mtimeNs": int(stat.st_mtime_ns),
                    "size": int(stat.st_size),
                    "contentHash": content_hash,
                    "manifestHash": manifest_hash,
                }
        return manifest

    @classmethod
    def _manifest_fingerprint(cls, descriptors: list[dict[str, Any]], manifest: dict[str, dict[str, Any]]) -> str:
        digest = hashlib.sha1()
        digest.update(cls._root_descriptors_signature(descriptors).encode("utf-8"))
        for key in sorted(manifest):
            item = manifest[key]
            digest.update(str(key).encode("utf-8"))
            digest.update(str(item.get("sourceType") or "").encode("utf-8"))
            digest.update(str(item.get("visibility") or "").encode("utf-8"))
            digest.update(str(item.get("rootPath") or "").encode("utf-8"))
            digest.update(str(item.get("mtimeNs") or "").encode("utf-8"))
            digest.update(str(item.get("size") or "").encode("utf-8"))
            digest.update(str(item.get("contentHash") or "").encode("utf-8"))
            digest.update(str(item.get("manifestHash") or "").encode("utf-8"))
        return digest.hexdigest()

    @classmethod
    def _file_sha1(cls, path: Path) -> str:
        try:
            return hashlib.sha1(path.read_bytes()).hexdigest()
        except Exception:
            return ""

    @classmethod
    def _skill_directory_manifest_hash(cls, skill_root: Path, instruction_path: Path) -> str:
        digest = hashlib.sha1()
        tracked_paths: list[Path] = [instruction_path]
        for subdir_name in ("references", "scripts", "assets", "templates", "examples"):
            subdir = skill_root / subdir_name
            if not subdir.exists() or not subdir.is_dir():
                continue
            tracked_paths.extend(path for path in sorted(subdir.rglob("*")) if path.is_file())
        seen: set[str] = set()
        for path in tracked_paths:
            normalized = cls._normalize_path(path)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            try:
                stat = path.stat()
                relative = path.relative_to(skill_root).as_posix()
            except Exception:
                continue
            digest.update(relative.encode("utf-8"))
            digest.update(str(int(stat.st_mtime_ns)).encode("utf-8"))
            digest.update(str(int(stat.st_size)).encode("utf-8"))
            if path == instruction_path:
                digest.update(cls._file_sha1(path).encode("utf-8"))
        return digest.hexdigest()

    @classmethod
    def _manifest_item_descriptor(cls, manifest_item: dict[str, Any]) -> dict[str, Any]:
        return cls._build_root_descriptor(
            root_path=Path(str(manifest_item.get("rootPath") or "")),
            source_type=str(manifest_item.get("sourceType") or "global").strip() or "global",
            visibility=str(manifest_item.get("visibility") or "global").strip() or "global",
            workspace_path=manifest_item.get("workspacePath"),
            workspace_id=manifest_item.get("workspaceId"),
            project_id=manifest_item.get("projectId"),
        )

    @classmethod
    def _entry_manifest_key(cls, entry: dict[str, Any]) -> str:
        return cls._normalize_path(entry.get("manifestKey") or entry.get("instructionPath"))

    @classmethod
    def _remember_recent_skill_discovery(
        cls,
        *,
        added: list[dict[str, Any]],
        updated: list[dict[str, Any]],
        refresh_mode: str,
    ) -> list[dict[str, Any]]:
        observed_at = cls._now_iso()
        retained: list[dict[str, Any]] = []
        cutoff = time.time() - 300
        for item in cls._recent_skill_discovery:
            try:
                item_ts = float(item.get("_observedTs") or 0.0)
            except (TypeError, ValueError):
                item_ts = 0.0
            if item_ts >= cutoff:
                retained.append(dict(item))
        for reason, entries in (("added", added), ("updated", updated)):
            for entry in entries:
                retained.append(
                    {
                        "skillId": str(entry.get("skillId") or ""),
                        "skillName": str(entry.get("skillName") or entry.get("name") or ""),
                        "skillRoot": cls._normalize_path(entry.get("skillRoot") or entry.get("path")),
                        "instructionPath": cls._normalize_path(entry.get("instructionPath")),
                        "reason": reason,
                        "refreshMode": refresh_mode,
                        "observedAt": observed_at,
                        "_observedTs": time.time(),
                    }
                )
        deduped: dict[str, dict[str, Any]] = {}
        for item in retained:
            key = str(item.get("skillId") or item.get("instructionPath") or item.get("skillRoot") or "").strip()
            if key:
                deduped[key] = item
        cls._recent_skill_discovery = list(deduped.values())[-64:]
        return [
            {key: value for key, value in item.items() if key != "_observedTs"}
            for item in cls._recent_skill_discovery
        ]

    @classmethod
    def _build_alias_snapshot(cls, entry: dict[str, Any]) -> dict[str, Any]:
        capability_profile = dict(entry.get("capabilityProfile") or {})
        theme_profile = dict(entry.get("themeProfile") or {})
        capability_tags = dict(entry.get("capabilityTags") or {})
        language_aliases = cls._normalize_hint_items(capability_tags.get("languageAliases"))
        aliases = cls._normalize_hint_items(
            [
                str(entry.get("name") or entry.get("skillName") or ""),
                str(entry.get("folder") or ""),
                *list(entry.get("aliases") or []),
                *list(entry.get("triggers") or []),
                *list(entry.get("keywords") or []),
                *list(entry.get("tags") or []),
                *language_aliases,
            ]
        )
        operation_tags = cls._normalize_hint_items(
            [
                *list(capability_profile.get("primaryOperations") or []),
                *list(capability_profile.get("secondaryOperationHints") or []),
                *list(capability_tags.get("operationTags") or []),
            ]
        )
        artifact_types = cls._normalize_hint_items(
            [
                *list(capability_profile.get("primaryArtifactTypes") or []),
                *list(capability_profile.get("secondaryArtifactHints") or []),
                *list(capability_tags.get("artifactTypes") or []),
            ]
        )
        theme_tags = cls._normalize_hint_items(
            [
                *list(theme_profile.get("primaryThemes") or []),
                *list(theme_profile.get("secondaryThemeTags") or []),
                *list(capability_tags.get("topicThemes") or []),
            ]
        )
        snapshot = {
            "skillName": str(entry.get("name") or entry.get("skillName") or "").strip(),
            "wakeWords": aliases[:32],
            "aliases": aliases[:64],
            "languageAliases": language_aliases[:32],
            "operationTags": operation_tags[:32],
            "artifactTypes": artifact_types[:24],
            "themeTags": theme_tags[:32],
        }
        snapshot["signature"] = hashlib.sha1(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return snapshot

    @classmethod
    def _is_valid_capability_profile(cls, profile: Any) -> bool:
        if not isinstance(profile, dict):
            return False
        required_keys = {
            "skillClass",
            "primaryArtifactTypes",
            "primaryOperations",
            "interactionMode",
            "capabilityConfidence",
            "profileSource",
        }
        if not required_keys.issubset(set(profile.keys())):
            return False
        if not isinstance(profile.get("primaryArtifactTypes"), list):
            return False
        if not isinstance(profile.get("primaryOperations"), list):
            return False
        if not isinstance(profile.get("secondaryArtifactHints", []), list):
            return False
        if not isinstance(profile.get("secondaryOperationHints", []), list):
            return False
        if not isinstance(profile.get("evidenceSignals", {}), dict):
            return False
        return True

    @classmethod
    def _is_valid_theme_profile(cls, profile: Any) -> bool:
        if not isinstance(profile, dict):
            return False
        required_keys = {
            "primaryThemes",
            "secondaryThemeTags",
            "themeConfidence",
            "themeSource",
        }
        if not required_keys.issubset(set(profile.keys())):
            return False
        if not isinstance(profile.get("primaryThemes"), list):
            return False
        if not isinstance(profile.get("secondaryThemeTags"), list):
            return False
        if not isinstance(profile.get("themeEvidenceSignals", {}), dict):
            return False
        return True

    @classmethod
    def _is_valid_capability_tags(cls, tags: Any) -> bool:
        if not isinstance(tags, dict):
            return False
        required_keys = {
            "capabilityKind",
            "artifactTypes",
            "operationTags",
            "topicThemes",
            "interactionMode",
            "evidenceMode",
            "sideEffectLevel",
            "runtimeAffinity",
            "languageAliases",
            "trustAndProvenance",
        }
        if not required_keys.issubset(set(tags.keys())):
            return False
        for key in ("capabilityKind", "artifactTypes", "operationTags", "topicThemes", "sideEffectLevel", "runtimeAffinity", "languageAliases"):
            if not isinstance(tags.get(key), list):
                return False
        if not isinstance(tags.get("evidenceMode"), dict):
            return False
        if not isinstance(tags.get("trustAndProvenance"), dict):
            return False
        return True

    @classmethod
    def _persist_cache(cls) -> None:
        cache_file = cls._cache_file()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _SKILLS_CACHE_SCHEMA_VERSION,
            "updatedAt": cls._now_iso(),
            "fingerprint": cls._skills_fingerprint,
            "revision": cls._skills_revision or cls._skills_fingerprint,
            "rootSignature": cls._skills_root_signature,
            "manifest": cls._skills_manifest,
            "recentSkillDiscovery": [
                {key: value for key, value in item.items() if key != "_observedTs"}
                for item in cls._recent_skill_discovery
            ],
            "roots": [str(root.resolve(strict=False)) for root in cls._skills_roots],
            "rootDescriptors": list(cls._skills_root_descriptors),
            "items": list(cls._skills_registry.values()),
        }
        cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def _stable_skill_id(cls, *, source_type: str, root_path: str, instruction_path: str) -> str:
        digest = hashlib.sha1(
            "|".join([source_type, cls._normalize_path(root_path), cls._normalize_path(instruction_path)]).encode("utf-8")
        ).hexdigest()
        return f"{source_type}:{digest[:16]}"

    @classmethod
    def _normalize_cached_item(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        skill_name = str(item.get("skillName") or item.get("name") or item.get("folder") or "").strip()
        skill_root = cls._normalize_path(item.get("skillRoot") or item.get("path"))
        instruction_path = cls._normalize_path(item.get("instructionPath"))
        source_type = str(item.get("sourceType") or "global").strip() or "global"
        skill_root_path = Path(skill_root) if skill_root else None

        def _cached_or_live_dir(key: str, folder_name: str) -> str:
            cached = cls._normalize_path(item.get(key))
            if cached:
                return cached
            if skill_root_path is None:
                return ""
            candidate = skill_root_path / folder_name
            return cls._normalize_path(candidate) if candidate.exists() else ""

        skill_id = str(item.get("skillId") or "").strip() or cls._stable_skill_id(
            source_type=source_type,
            root_path=skill_root,
            instruction_path=instruction_path or skill_root,
        )
        if not skill_id or not skill_name:
            return None
        cached_available_files = list(item.get("availableFiles") or [])
        return {
            "skillId": skill_id,
            "name": skill_name,
            "description": str(item.get("description") or "No description provided."),
            "instructions": str(item.get("instructions") or ""),
            "folder": str(item.get("folder") or Path(skill_root).name or skill_name),
            "path": skill_root,
            "skillName": skill_name,
            "skillRoot": skill_root,
            "instructionPath": instruction_path,
            "referencesDir": _cached_or_live_dir("referencesDir", "references"),
            "scriptsDir": _cached_or_live_dir("scriptsDir", "scripts"),
            "assetsDir": _cached_or_live_dir("assetsDir", "assets"),
            "templatesDir": _cached_or_live_dir("templatesDir", "templates"),
            "examplesDir": _cached_or_live_dir("examplesDir", "examples"),
            "availableFiles": cached_available_files
            or (cls._summarize_skill_structure(skill_root_path) if skill_root_path else []),
            "aliases": cls._normalize_hint_items(item.get("aliases")),
            "triggers": cls._normalize_hint_items(item.get("triggers")),
            "keywords": cls._normalize_hint_items(item.get("keywords")),
            "tags": cls._normalize_hint_items(item.get("tags")),
            "capabilityProfile": dict(item.get("capabilityProfile") or {}),
            "themeProfile": dict(item.get("themeProfile") or {}),
            "capabilityTags": dict(item.get("capabilityTags") or {}),
            "safety": dict(item.get("safety") or {}),
            "manifestKey": str(item.get("manifestKey") or item.get("instructionPath") or "").strip(),
            "contentHash": str(item.get("contentHash") or "").strip(),
            "manifestHash": str(item.get("manifestHash") or "").strip(),
            "aliasSnapshot": dict(item.get("aliasSnapshot") or {}),
            "sourceType": source_type,
            "visibility": str(item.get("visibility") or "global").strip() or "global",
            "workspacePath": cls._normalize_path(item.get("workspacePath")),
            "workspaceId": str(item.get("workspaceId") or "").strip() or None,
            "projectId": str(item.get("projectId") or "").strip() or None,
            "rootPath": cls._normalize_path(item.get("rootPath") or skill_root),
        }

    @classmethod
    def _load_cached_registry(cls) -> bool:
        cache_file = cls._cache_file()
        if not cache_file.exists():
            legacy_cache_file = cls._legacy_cache_file()
            if legacy_cache_file.exists():
                cache_file = legacy_cache_file
        if not cache_file.exists():
            return False
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return False
        if int(payload.get("version") or 0) != _SKILLS_CACHE_SCHEMA_VERSION:
            return False

        registry: dict[str, dict] = {}
        for item in list(payload.get("items") or []):
            if not isinstance(item, dict):
                return False
            if not cls._is_valid_capability_profile(item.get("capabilityProfile")):
                return False
            if not cls._is_valid_theme_profile(item.get("themeProfile")):
                return False
            if not cls._is_valid_capability_tags(item.get("capabilityTags")):
                return False
            normalized = cls._normalize_cached_item(item)
            if normalized is None:
                return False
            instruction_path = Path(str(normalized.get("instructionPath") or "").strip())
            skill_root = Path(str(normalized.get("skillRoot") or normalized.get("rootPath") or "").strip())
            if not instruction_path.exists() or not instruction_path.is_file():
                continue
            if skill_root and not skill_root.exists():
                continue
            registry[str(normalized.get("skillId"))] = normalized
        if not registry:
            return False
        if any(not isinstance(item.get("safety"), dict) or not item.get("safety") for item in registry.values()):
            try:
                registry = annotate_skill_entries(registry, record_reviews=True)  # type: ignore[assignment]
            except Exception:
                pass

        cls._skills_registry = registry
        cls._skills_fingerprint = str(payload.get("fingerprint") or "").strip()
        cls._skills_revision = str(payload.get("revision") or cls._skills_fingerprint or "").strip()
        cls._skills_manifest = {
            str(key): dict(value)
            for key, value in dict(payload.get("manifest") or {}).items()
            if str(key).strip() and isinstance(value, dict)
        }
        cls._skills_root_signature = str(payload.get("rootSignature") or "").strip()
        cls._skills_roots = [
            Path(candidate)
            for candidate in [str(root or "").strip() for root in list(payload.get("roots") or [])]
            if candidate
        ]
        root_descriptors = cls._dedupe_root_descriptors(list(payload.get("rootDescriptors") or []))
        if root_descriptors:
            cls._skills_root_descriptors = root_descriptors
        else:
            cls._skills_root_descriptors = [
                cls._build_root_descriptor(root_path=root, source_type="global", visibility="global")
                for root in cls._skills_roots
            ]
        if not cls._skills_manifest:
            cls._skills_manifest = cls._compute_manifest(cls._skills_root_descriptors)
        if not cls._skills_root_signature:
            cls._skills_root_signature = cls._root_descriptors_signature(cls._skills_root_descriptors)
        if not cls._skills_revision:
            cls._skills_revision = cls._manifest_fingerprint(cls._skills_root_descriptors, cls._skills_manifest)
        cls._recent_skill_discovery = [
            dict(item)
            for item in list(payload.get("recentSkillDiscovery") or [])
            if isinstance(item, dict)
        ][-64:]
        cls._startup_state = "ready"
        cls._snapshot_freshness = "cached"
        cls._last_refresh_at = str(payload.get("updatedAt") or "").strip() or None
        cls._last_refresh_error = None
        cls._last_check_at = time.monotonic()
        cls._rebuild_root_inventory_states_from_registry()
        if cache_file == cls._legacy_cache_file():
            try:
                cls._persist_cache()
            except Exception:
                pass
        return True

    @classmethod
    def _ensure_seeded_global_skills(cls, global_agents_path: Path) -> None:
        repo_skill_root = cls._resolve_repo_root() / ".agents" / "skills"
        if not repo_skill_root.exists():
            return

        cls._cleanup_invalid_seed_shells(global_agents_path)

        for skill_name in _SEEDED_GLOBAL_SKILL_NAMES:
            source_dir = repo_skill_root / skill_name
            target_dir = global_agents_path / skill_name
            instruction_path = source_dir / "SKILL.md"
            if (
                not source_dir.exists()
                or target_dir.exists()
                or not instruction_path.exists()
                or not cls._skill_instruction_has_content(instruction_path)
                or cls._is_seed_tombstoned(skill_name, source_dir=source_dir)
            ):
                continue
            try:
                shutil.copytree(source_dir, target_dir)
                print(f"[SkillLoader] Seeded workspace skill '{skill_name}' into {target_dir}")
            except Exception as exc:
                print(f"[SkillLoader] Failed to seed skill '{skill_name}': {exc}")

    @classmethod
    def _skill_instruction_has_content(cls, instruction_path: Path) -> bool:
        try:
            return bool(instruction_path.read_text(encoding="utf-8", errors="ignore").strip())
        except Exception:
            return False

    @classmethod
    def _skill_seed_tombstone_path(cls) -> Path:
        return Path.home() / ".v8-agent-os" / "cache" / "extensions" / "skill_seed_tombstones.json"

    @classmethod
    def _read_skill_seed_tombstones(cls) -> dict[str, Any]:
        tombstone_path = cls._skill_seed_tombstone_path()
        try:
            payload = json.loads(tombstone_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            entries = {}
        return {
            "schemaVersion": int(payload.get("schemaVersion") or _SKILL_SEED_TOMBSTONE_SCHEMA_VERSION),
            "entries": {str(key): value for key, value in entries.items() if isinstance(value, dict)},
        }

    @classmethod
    def _write_skill_seed_tombstones(cls, payload: dict[str, Any]) -> None:
        tombstone_path = cls._skill_seed_tombstone_path()
        tombstone_path.parent.mkdir(parents=True, exist_ok=True)
        tombstone_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def _is_seed_tombstoned(cls, skill_name: str, *, source_dir: Path) -> bool:
        tombstones = cls._read_skill_seed_tombstones()
        entry = dict((tombstones.get("entries") or {}).get(str(skill_name or "").strip()) or {})
        if not entry:
            return False
        recorded_source = cls._normalize_path(entry.get("sourceRoot"))
        current_source = cls._normalize_path(source_dir)
        return not recorded_source or recorded_source == current_source

    @classmethod
    def _record_skill_seed_tombstone(
        cls,
        *,
        skill_name: str,
        source_dir: Path | None,
        target_dir: Path,
        initiated_by: str,
    ) -> dict[str, Any]:
        normalized_name = str(skill_name or "").strip()
        if not normalized_name:
            return {}
        tombstones = cls._read_skill_seed_tombstones()
        entries = dict(tombstones.get("entries") or {})
        entry = {
            "skillName": normalized_name,
            "sourceRoot": cls._normalize_path(source_dir) if source_dir else "",
            "targetRoot": cls._normalize_path(target_dir),
            "deletedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "initiatedBy": str(initiated_by or "admin_extensions_manual_delete").strip() or "admin_extensions_manual_delete",
        }
        entries[normalized_name] = entry
        cls._write_skill_seed_tombstones(
            {
                "schemaVersion": _SKILL_SEED_TOMBSTONE_SCHEMA_VERSION,
                "entries": entries,
            }
        )
        return entry

    @classmethod
    def _cleanup_invalid_seed_shells(cls, global_agents_path: Path) -> None:
        for skill_name in _SEEDED_GLOBAL_SKILL_NAMES:
            target_dir = global_agents_path / skill_name
            if not target_dir.exists() or not target_dir.is_dir() or (target_dir / "SKILL.md").exists():
                continue
            try:
                has_files = any(item.is_file() for item in target_dir.rglob("*"))
            except Exception:
                has_files = True
            if has_files:
                continue
            try:
                shutil.rmtree(target_dir)
                print(f"[SkillLoader] Removed invalid seeded skill shell '{skill_name}' at {target_dir}")
            except Exception as exc:
                print(f"[SkillLoader] Failed to remove invalid seeded skill shell '{skill_name}': {exc}")

    @classmethod
    def _lookup_project_binding_for_workspace(cls, workspace_path: str | None) -> tuple[str | None, str | None]:
        normalized = cls._normalize_path(workspace_path)
        if not normalized:
            return None, None
        project = project_registry_service.find_project_for_workspace(workspace_path=normalized)
        if project is None:
            return None, None
        return (
            str(project.workspace_id or "").strip() or None,
            str(project.project_id or "").strip() or None,
        )

    @classmethod
    def _global_root_descriptor(cls) -> dict[str, Any]:
        global_agents_path = Path.home() / ".agents" / "skills"
        global_agents_path.mkdir(parents=True, exist_ok=True)
        cls._ensure_seeded_global_skills(global_agents_path)
        return cls._build_root_descriptor(
            root_path=global_agents_path,
            source_type="global",
            visibility="global",
        )

    @classmethod
    def _main_workspace_root_descriptor(cls) -> dict[str, Any] | None:
        workspace_path = workspace_resolution_service.get_main_workspace_path()
        normalized_workspace = cls._normalize_path(workspace_path)
        if not normalized_workspace:
            return None
        workspace_id, project_id = cls._lookup_project_binding_for_workspace(normalized_workspace)
        return cls._build_root_descriptor(
            root_path=Path(normalized_workspace) / ".agents" / "skills",
            source_type="main_workspace",
            visibility="global",
            workspace_path=normalized_workspace,
            workspace_id=workspace_id,
            project_id=project_id,
        )

    @classmethod
    def _registered_project_workspace_root_descriptors(cls) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = []
        main_workspace_path = cls._normalize_path(workspace_resolution_service.get_main_workspace_path())
        try:
            projects = list(project_registry_service.list_projects() or [])
        except Exception:
            return descriptors
        for project in projects:
            workspace_path = cls._normalize_path(getattr(project, "workspace_path", None))
            if not workspace_path or workspace_path == main_workspace_path:
                continue
            descriptors.append(
                cls._build_root_descriptor(
                    root_path=Path(workspace_path) / ".agents" / "skills",
                    source_type="scoped_workspace",
                    visibility="scoped",
                    workspace_path=workspace_path,
                    workspace_id=str(getattr(project, "workspace_id", "") or "").strip() or None,
                    project_id=str(getattr(project, "project_id", "") or "").strip() or None,
                )
            )
        return cls._dedupe_root_descriptors(descriptors)

    @classmethod
    def _discovery_root_descriptors(cls) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = [cls._global_root_descriptor()]
        main_descriptor = cls._main_workspace_root_descriptor()
        if main_descriptor is not None:
            descriptors.append(main_descriptor)
        descriptors.extend(cls._registered_project_workspace_root_descriptors())
        return cls._dedupe_root_descriptors(descriptors)

    @classmethod
    def _scoped_workspace_root_descriptor(
        cls,
        *,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> dict[str, Any] | None:
        descriptor = workspace_resolution_service.resolve_workspace_descriptor(
            runtime_kind=runtime_kind or "chat",
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        workspace_root = cls._normalize_path(descriptor.get("workspaceRoot"))
        main_workspace_root = cls._normalize_path(descriptor.get("mainWorkspacePath"))
        if not workspace_root or workspace_root == main_workspace_root:
            return None
        return cls._build_root_descriptor(
            root_path=Path(workspace_root) / ".agents" / "skills",
            source_type="scoped_workspace",
            visibility="scoped",
            workspace_path=workspace_root,
            workspace_id=str(descriptor.get("workspaceId") or "").strip() or None,
            project_id=str(descriptor.get("projectId") or "").strip() or None,
        )

    @classmethod
    def _resolve_root_descriptors(
        cls,
        *,
        include_scoped: bool = False,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = [cls._global_root_descriptor()]
        scoped_descriptor = None
        if include_scoped:
            scoped_descriptor = cls._scoped_workspace_root_descriptor(
                runtime_kind=runtime_kind,
                session_id=session_id,
                explicit_workspace_id=explicit_workspace_id,
                explicit_workspace_path=explicit_workspace_path,
                explicit_project_id=explicit_project_id,
            )
        if scoped_descriptor is not None:
            descriptors.append(scoped_descriptor)
        else:
            main_descriptor = cls._main_workspace_root_descriptor()
            if main_descriptor is not None:
                descriptors.append(main_descriptor)
        return cls._dedupe_root_descriptors(descriptors)

    @classmethod
    def resolve_root_descriptors(
        cls,
        *,
        include_scoped: bool = False,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return cls._resolve_root_descriptors(
            include_scoped=include_scoped,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )

    @classmethod
    def _root_manifest_fingerprint(
        cls,
        descriptor: dict[str, Any],
        manifest: dict[str, dict[str, Any]],
    ) -> str:
        return cls._manifest_fingerprint([descriptor], manifest)

    @classmethod
    def _descriptor_cache_key(cls, descriptor: dict[str, Any]) -> str:
        return cls._normalize_path(descriptor.get("rootPath"))

    @classmethod
    def _compute_root_manifest(cls, descriptor: dict[str, Any]) -> dict[str, dict[str, Any]]:
        manifest: dict[str, dict[str, Any]] = {}
        normalized_descriptor = cls._dedupe_root_descriptors([descriptor])
        if not normalized_descriptor:
            return manifest
        root_descriptor = normalized_descriptor[0]
        root_path = cls._normalize_path(root_descriptor.get("rootPath"))
        root = Path(root_path)
        if not root.exists() or not root.is_dir():
            return manifest
        for skill_file in sorted(root.glob("*/SKILL.md")):
            try:
                stat = skill_file.stat()
            except OSError:
                continue
            instruction_path = cls._normalize_path(skill_file)
            skill_root = cls._normalize_path(skill_file.parent)
            content_hash = cls._file_sha1(skill_file)
            manifest_hash = cls._skill_directory_manifest_hash(skill_file.parent, skill_file)
            manifest[instruction_path] = {
                "key": instruction_path,
                "manifestKey": instruction_path,
                "instructionPath": instruction_path,
                "skillRoot": skill_root,
                "folder": skill_file.parent.name,
                "rootPath": root_path,
                "sourceType": str(root_descriptor.get("sourceType") or "global").strip() or "global",
                "visibility": str(root_descriptor.get("visibility") or "global").strip() or "global",
                "workspacePath": cls._normalize_path(root_descriptor.get("workspacePath")),
                "workspaceId": str(root_descriptor.get("workspaceId") or "").strip() or None,
                "projectId": str(root_descriptor.get("projectId") or "").strip() or None,
                "mtimeNs": int(stat.st_mtime_ns),
                "size": int(stat.st_size),
                "contentHash": content_hash,
                "manifestHash": manifest_hash,
            }
        return manifest

    @classmethod
    def _refresh_alias_snapshot(cls, entry: dict[str, Any]) -> dict[str, Any]:
        next_entry = dict(entry)
        next_entry["aliasSnapshot"] = cls._build_alias_snapshot(next_entry)
        return next_entry

    @classmethod
    def _scan_single_skill_descriptor(
        cls,
        *,
        descriptor: dict[str, Any],
        manifest_item: dict[str, Any],
    ) -> dict[str, Any] | None:
        instruction_path = Path(str(manifest_item.get("instructionPath") or ""))
        if not instruction_path.exists() or not instruction_path.is_file():
            return None
        try:
            content = instruction_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"[SkillLoader] Error reading {instruction_path}: {exc}")
            return None
        if not content.strip():
            try:
                from core.audit_logger import audit_logger
                from core.run_ledger import run_ledger_service

                audit_logger.log(
                    source_type="SAFETY",
                    action="skill_integrity_empty_instruction",
                    status="WARNING",
                    details=json.dumps(
                        {
                            "instructionPath": cls._normalize_path(instruction_path),
                            "skillRoot": cls._normalize_path(instruction_path.parent),
                            "reason": "SKILL.md is empty during read-only refresh; no V8 delete/install audit was observed in this scan path.",
                        },
                        ensure_ascii=False,
                    ),
                )
                run_ledger_service.record_event(
                    event_type="skill.integrity.empty_instruction",
                    runtime_kind="extensions",
                    source="extensions.skill_loader",
                    summary=f"SKILL.md is empty: {instruction_path.parent.name}",
                    refs={
                        "instructionPath": cls._normalize_path(instruction_path),
                        "skillRoot": cls._normalize_path(instruction_path.parent),
                    },
                    payload={
                        "reason": "empty_instruction_during_read_only_refresh",
                        "sourceType": manifest_item.get("sourceType"),
                        "visibility": manifest_item.get("visibility"),
                    },
                )
            except Exception:
                pass
        entry = cls._build_skill_entry(
            folder_name=str(manifest_item.get("folder") or instruction_path.parent.name),
            file_path=instruction_path,
            descriptor=descriptor,
            content=content,
        )
        if entry is None:
            return None
        entry["manifestKey"] = str(manifest_item.get("manifestKey") or manifest_item.get("key") or entry.get("instructionPath") or "")
        entry["contentHash"] = str(manifest_item.get("contentHash") or "")
        entry["manifestHash"] = str(manifest_item.get("manifestHash") or "")
        entry["aliasSnapshot"] = cls._build_alias_snapshot(entry)
        try:
            annotated = annotate_skill_entries([entry], record_reviews=True)
            if isinstance(annotated, list) and annotated:
                entry = dict(annotated[0])
        except Exception as exc:
            print(f"[SkillLoader] Safety single-skill annotation failed for {entry.get('name')}: {exc}")
        print(
            f"[SkillLoader] Refreshed Skill: {entry.get('skillName') or entry.get('name')} "
            f"({entry.get('sourceType')})"
        )
        return entry

    @classmethod
    def _scan_single_root_descriptor(
        cls,
        descriptor: dict[str, Any],
        *,
        manifest: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict]:
        current_manifest = manifest if manifest is not None else cls._compute_root_manifest(descriptor)
        root_path = cls._descriptor_cache_key(descriptor)
        previous_state = cls._root_inventory_states.get(root_path) or {}
        previous_manifest = {
            str(key): dict(value)
            for key, value in dict(previous_state.get("manifest") or {}).items()
            if isinstance(value, dict)
        }
        previous_registry_by_manifest_key = {
            cls._entry_manifest_key(item): dict(item)
            for item in dict(previous_state.get("registry") or {}).values()
            if cls._entry_manifest_key(item)
        }
        registry: dict[str, dict] = {}
        for manifest_key in sorted(current_manifest):
            manifest_item = dict(current_manifest.get(manifest_key) or {})
            previous_item = previous_manifest.get(manifest_key) or {}
            previous_entry = previous_registry_by_manifest_key.get(manifest_key)
            unchanged = (
                previous_entry is not None
                and str(previous_item.get("contentHash") or "") == str(manifest_item.get("contentHash") or "")
                and str(previous_item.get("manifestHash") or "") == str(manifest_item.get("manifestHash") or "")
                and str(previous_item.get("mtimeNs") or "") == str(manifest_item.get("mtimeNs") or "")
                and str(previous_item.get("size") or "") == str(manifest_item.get("size") or "")
            )
            if unchanged:
                reused = dict(previous_entry)
                reused["manifestKey"] = manifest_key
                reused["contentHash"] = str(manifest_item.get("contentHash") or reused.get("contentHash") or "")
                reused["manifestHash"] = str(manifest_item.get("manifestHash") or reused.get("manifestHash") or "")
                if not isinstance(reused.get("aliasSnapshot"), dict) or not reused.get("aliasSnapshot"):
                    reused = cls._refresh_alias_snapshot(reused)
                registry[str(reused.get("skillId"))] = reused
                continue
            entry = cls._scan_single_skill_descriptor(descriptor=descriptor, manifest_item=manifest_item)
            if entry is not None:
                registry[str(entry.get("skillId"))] = entry
        return registry

    @classmethod
    def _visible_root_revision_key(cls, descriptors: list[dict[str, Any]]) -> str:
        normalized_descriptors = cls._dedupe_root_descriptors(descriptors)
        payload: list[dict[str, Any]] = []
        for descriptor in normalized_descriptors:
            root_path = cls._descriptor_cache_key(descriptor)
            state = cls._root_inventory_states.get(root_path) or {}
            payload.append(
                {
                    "rootPath": root_path,
                    "rootRevision": str(state.get("rootRevision") or ""),
                    "descriptorSignature": str(state.get("descriptorSignature") or ""),
                }
            )
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def _visible_inventory_cache_key(cls, descriptors: list[dict[str, Any]]) -> str:
        return "|".join([cls._root_descriptors_signature(descriptors), cls._visible_root_revision_key(descriptors)])

    @classmethod
    def _dirty_root_paths_for_descriptors(cls, descriptors: list[dict[str, Any]]) -> list[str]:
        dirty: list[str] = []
        for descriptor in cls._dedupe_root_descriptors(descriptors):
            root_path = cls._descriptor_cache_key(descriptor)
            if root_path and root_path in cls._dirty_root_paths:
                dirty.append(root_path)
        return dirty

    @classmethod
    def _invalidate_visible_inventory_cache(cls, changed_root_paths: set[str] | None = None) -> None:
        if not changed_root_paths:
            cls._visible_inventory_cache.clear()
            return
        normalized_changed = {
            cls._normalize_path(item)
            for item in list(changed_root_paths or set())
            if cls._normalize_path(item)
        }
        if not normalized_changed:
            return
        stale_keys: list[str] = []
        for cache_key, snapshot in cls._visible_inventory_cache.items():
            cache_root_paths = {
                cls._descriptor_cache_key(descriptor)
                for descriptor in list((snapshot or {}).get("rootDescriptors") or [])
                if cls._descriptor_cache_key(descriptor)
            }
            if cache_root_paths.intersection(normalized_changed):
                stale_keys.append(cache_key)
        for cache_key in stale_keys:
            cls._visible_inventory_cache.pop(cache_key, None)

    @classmethod
    def _rebuild_root_inventory_states_from_registry(cls) -> None:
        descriptors = cls._dedupe_root_descriptors(cls._skills_root_descriptors)
        states: dict[str, dict[str, Any]] = {}
        for descriptor in descriptors:
            root_path = cls._descriptor_cache_key(descriptor)
            if not root_path:
                continue
            manifest = {
                key: dict(value)
                for key, value in cls._skills_manifest.items()
                if cls._normalize_path((value or {}).get("rootPath")) == root_path
            }
            registry = {
                skill_id: dict(item)
                for skill_id, item in cls._skills_registry.items()
                if cls._normalize_path((item or {}).get("rootPath")) == root_path
            }
            states[root_path] = {
                "descriptor": dict(descriptor),
                "descriptorSignature": cls._root_descriptors_signature([descriptor]),
                "manifest": manifest,
                "registry": registry,
                "rootRevision": cls._root_manifest_fingerprint(descriptor, manifest),
                "lastScanAt": cls._now_iso(),
                "dirty": False,
            }
        cls._root_inventory_states = states
        cls._dirty_root_paths = set()
        cls._invalidate_visible_inventory_cache()

    @classmethod
    def _rebuild_aggregate_registry_from_root_states(
        cls,
        *,
        descriptors: list[dict[str, Any]],
        changed_root_paths: set[str] | None = None,
    ) -> None:
        normalized_descriptors = cls._dedupe_root_descriptors(descriptors)
        registry: dict[str, dict] = {}
        manifest: dict[str, dict[str, Any]] = {}
        roots: list[Path] = []
        revision_payload: list[dict[str, str]] = []
        for descriptor in normalized_descriptors:
            root_path = cls._descriptor_cache_key(descriptor)
            if not root_path:
                continue
            state = cls._root_inventory_states.get(root_path)
            if not state:
                continue
            roots.append(Path(root_path))
            manifest.update({str(key): dict(value) for key, value in dict(state.get("manifest") or {}).items()})
            registry.update({str(key): dict(value) for key, value in dict(state.get("registry") or {}).items()})
            revision_payload.append(
                {
                    "rootPath": root_path,
                    "rootRevision": str(state.get("rootRevision") or ""),
                    "descriptorSignature": str(state.get("descriptorSignature") or ""),
                }
            )
        revision_text = json.dumps(revision_payload, ensure_ascii=False, sort_keys=True)
        cls._skills_registry = registry
        cls._skills_root_descriptors = normalized_descriptors
        cls._skills_roots = roots
        cls._skills_manifest = manifest
        cls._skills_root_signature = cls._root_descriptors_signature(normalized_descriptors)
        cls._skills_fingerprint = cls._manifest_fingerprint(normalized_descriptors, manifest)
        cls._skills_revision = hashlib.sha1(revision_text.encode("utf-8")).hexdigest()
        cls._invalidate_visible_inventory_cache(changed_root_paths)

    @classmethod
    def _build_visible_inventory_from_descriptors(
        cls,
        *,
        descriptors: list[dict[str, Any]],
        discovery_revision: str | None = None,
        changed_roots: list[str] | None = None,
        scoped_refresh_mode: str | None = None,
        visible_registry_cache_hit: bool = False,
        exclude_root_paths: set[str] | None = None,
    ) -> dict[str, Any]:
        normalized_descriptors = cls._dedupe_root_descriptors(descriptors)
        excluded = {cls._normalize_path(item) for item in list(exclude_root_paths or set()) if cls._normalize_path(item)}
        registry: dict[str, dict] = {}
        fingerprint_payload: list[dict[str, str]] = []
        for descriptor in normalized_descriptors:
            root_path = cls._descriptor_cache_key(descriptor)
            if not root_path or root_path in excluded:
                continue
            state = cls._root_inventory_states.get(root_path) or {}
            registry.update({str(key): dict(value) for key, value in dict(state.get("registry") or {}).items()})
            fingerprint_payload.append(
                {
                    "rootPath": root_path,
                    "rootRevision": str(state.get("rootRevision") or ""),
                }
            )
        snapshot = cls._inventory_snapshot(
            registry=registry,
            descriptors=[descriptor for descriptor in normalized_descriptors if cls._descriptor_cache_key(descriptor) not in excluded],
            fingerprint=hashlib.sha1(
                json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            discovery_revision=discovery_revision,
            changed_roots=changed_roots,
            scoped_refresh_mode=scoped_refresh_mode,
        )
        snapshot["visibleRootRevisionKey"] = cls._visible_root_revision_key(snapshot.get("rootDescriptors") or [])
        snapshot["visibleRegistryCacheHit"] = bool(visible_registry_cache_hit)
        snapshot["dirtyVisibleRoots"] = cls._dirty_root_paths_for_descriptors(snapshot.get("rootDescriptors") or [])
        return snapshot

    @classmethod
    def _compute_fingerprint(cls, descriptors: list[dict[str, Any]]) -> str:
        manifest = cls._compute_manifest(descriptors)
        return cls._manifest_fingerprint(descriptors, manifest)

    @classmethod
    def _inventory_snapshot(
        cls,
        *,
        registry: dict[str, dict],
        descriptors: list[dict[str, Any]],
        fingerprint: str,
        discovery_revision: str | None = None,
        changed_roots: list[str] | None = None,
        scoped_refresh_mode: str | None = None,
        visible_registry_cache_hit: bool = False,
    ) -> dict[str, Any]:
        normalized_descriptors = cls._dedupe_root_descriptors(descriptors)
        items = sorted(
            list(registry.values()),
            key=lambda item: (
                str(item.get("skillName") or item.get("name") or "").lower(),
                str(item.get("sourceType") or ""),
                str(item.get("skillRoot") or item.get("path") or ""),
            ),
        )
        return {
            "registry": dict(registry),
            "items": items,
            "rootDescriptors": list(normalized_descriptors),
            "roots": [str(item.get("rootPath") or "") for item in normalized_descriptors],
            "fingerprint": fingerprint,
            "revision": fingerprint,
            "discoveryRevision": str(discovery_revision or cls._skills_revision or fingerprint).strip(),
            "visibleRootSignature": cls._root_descriptors_signature(normalized_descriptors),
            "visibleRootRevisionKey": cls._visible_root_revision_key(normalized_descriptors),
            "visibleRegistryCacheHit": bool(visible_registry_cache_hit),
            "dirtyVisibleRoots": cls._dirty_root_paths_for_descriptors(normalized_descriptors),
            "inventoryReadyState": cls._startup_state,
            "snapshotFreshness": cls._snapshot_freshness,
            "changedRoots": list(changed_roots or []),
            "scopedRefreshMode": str(scoped_refresh_mode or "").strip() or None,
            "recentSkillDiscovery": [
                {key: value for key, value in item.items() if key != "_observedTs"}
                for item in cls._recent_skill_discovery
            ],
        }

    @classmethod
    def _build_skill_entry(
        cls,
        *,
        folder_name: str,
        file_path: Path,
        descriptor: dict[str, Any],
        content: str,
    ) -> dict[str, Any] | None:
        if not content.startswith("---"):
            return None
        try:
            parts = content.split("---", 2)
            if len(parts) < 3:
                return None
            frontmatter = yaml.safe_load(parts[1]) or {}
        except Exception as exc:
            print(f"[SkillLoader] Error parsing frontmatter in {file_path}: {exc}")
            return None

        body = parts[2].strip()
        name = str(frontmatter.get("name") or folder_name).strip() or folder_name
        description = str(frontmatter.get("description") or "No description provided.").strip() or "No description provided."
        skill_root = file_path.parent
        source_type = str(descriptor.get("sourceType") or "global").strip() or "global"
        normalized_skill_root = cls._normalize_path(skill_root)
        normalized_instruction_path = cls._normalize_path(file_path)
        available_files = cls._summarize_skill_structure(skill_root)
        aliases = cls._normalize_hint_items(frontmatter.get("aliases"))
        triggers = cls._normalize_hint_items(frontmatter.get("triggers"))
        keywords = cls._normalize_hint_items(frontmatter.get("keywords"))
        tags = cls._normalize_hint_items(frontmatter.get("tags"))
        description_hints = cls._extract_description_hint_fields(description)
        aliases = cls._normalize_hint_items([*aliases, *description_hints.get("aliases", [])])
        triggers = cls._normalize_hint_items([*triggers, *description_hints.get("triggers", [])])
        keywords = cls._normalize_hint_items([*keywords, *description_hints.get("keywords", [])])
        tags = cls._normalize_hint_items([*tags, *description_hints.get("tags", [])])
        references_dir = cls._normalize_path(skill_root / "references") if (skill_root / "references").exists() else ""
        scripts_dir = cls._normalize_path(skill_root / "scripts") if (skill_root / "scripts").exists() else ""
        assets_dir = cls._normalize_path(skill_root / "assets") if (skill_root / "assets").exists() else ""
        templates_dir = cls._normalize_path(skill_root / "templates") if (skill_root / "templates").exists() else ""
        examples_dir = cls._normalize_path(skill_root / "examples") if (skill_root / "examples").exists() else ""
        capability_profile = cls._derive_capability_profile(
            name=name,
            folder=folder_name,
            description=description,
            body=body,
            available_files=available_files,
            aliases=aliases,
            triggers=triggers,
            keywords=keywords,
            tags=tags,
            has_scripts=bool(scripts_dir),
            has_templates=bool(templates_dir),
            has_examples=bool(examples_dir),
            has_assets=bool(assets_dir),
        )
        theme_profile = cls._derive_theme_profile(
            name=name,
            folder=folder_name,
            description=description,
            body=body,
            available_files=available_files,
            aliases=aliases,
            triggers=triggers,
            keywords=keywords,
            tags=tags,
            skill_class=str(capability_profile.get("skillClass") or "general").strip() or "general",
        )
        capability_tags = cls._derive_capability_tags(
            capability_profile=capability_profile,
            theme_profile=theme_profile,
            aliases=aliases,
            triggers=triggers,
            keywords=keywords,
            tags=tags,
            has_scripts=bool(scripts_dir),
            has_templates=bool(templates_dir),
            has_examples=bool(examples_dir),
            has_assets=bool(assets_dir),
        )
        manifest_key = normalized_instruction_path
        content_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()
        manifest_hash = cls._skill_directory_manifest_hash(skill_root, file_path)
        entry = {
            "skillId": cls._stable_skill_id(
                source_type=source_type,
                root_path=normalized_skill_root,
                instruction_path=normalized_instruction_path,
            ),
            "name": name,
            "description": description,
            "instructions": body,
            "folder": folder_name,
            "path": normalized_skill_root,
            "skillName": name,
            "skillRoot": normalized_skill_root,
            "instructionPath": normalized_instruction_path,
            "referencesDir": references_dir,
            "scriptsDir": scripts_dir,
            "assetsDir": assets_dir,
            "templatesDir": templates_dir,
            "examplesDir": examples_dir,
            "availableFiles": available_files,
            "aliases": aliases,
            "triggers": triggers,
            "keywords": keywords,
            "tags": tags,
            "capabilityProfile": capability_profile,
            "themeProfile": theme_profile,
            "capabilityTags": capability_tags,
            "manifestKey": manifest_key,
            "contentHash": content_hash,
            "manifestHash": manifest_hash,
            "sourceType": source_type,
            "visibility": str(descriptor.get("visibility") or "global").strip() or "global",
            "workspacePath": cls._normalize_path(descriptor.get("workspacePath")),
            "workspaceId": str(descriptor.get("workspaceId") or "").strip() or None,
            "projectId": str(descriptor.get("projectId") or "").strip() or None,
            "rootPath": cls._normalize_path(descriptor.get("rootPath") or normalized_skill_root),
        }
        entry["aliasSnapshot"] = cls._build_alias_snapshot(entry)
        return entry

    @classmethod
    def _looks_like_skill_path_identifier(cls, identifier: str) -> bool:
        needle = str(identifier or "").strip()
        if not needle:
            return False
        lowered = needle.replace("\\", "/").lower()
        return (
            "/" in lowered
            or "\\\\" in needle
            or bool(re.match(r"^[a-zA-Z]:[\\/]", needle))
            or lowered.endswith("skill.md")
        )

    @classmethod
    def _looks_like_precise_skill_identifier(cls, identifier: str) -> bool:
        needle = str(identifier or "").strip()
        if not needle or cls._looks_like_skill_path_identifier(needle):
            return False
        if any(ch.isspace() for ch in needle):
            return False
        return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*[-_:][A-Za-z0-9_.:-]*$", needle))

    @classmethod
    def _resolve_direct_skill_entry(cls, identifier: str) -> dict[str, Any] | None:
        needle = str(identifier or "").strip().strip("\"'")
        if not needle or not cls._looks_like_skill_path_identifier(needle):
            return None
        try:
            candidate = Path(needle).expanduser()
            if candidate.is_dir():
                skill_file = candidate / "SKILL.md"
                skill_root = candidate
            else:
                skill_file = candidate
                skill_root = candidate.parent
            if not skill_file.exists() or not skill_file.is_file() or skill_file.name.lower() != "skill.md":
                return None
            content = skill_file.read_text(encoding="utf-8")
            descriptor: dict[str, Any] = {
                "sourceType": "direct_path",
                "visibility": "direct",
                "rootPath": str(skill_root.parent),
            }
            normalized = cls._normalize_path(skill_file)
            workspace_marker = "/.agents/skills/".lower()
            normalized_slash = normalized.replace("\\", "/")
            normalized_slash_lower = normalized_slash.lower()
            if workspace_marker in normalized_slash_lower:
                split_index = normalized_slash_lower.index(workspace_marker)
                workspace_path = normalized_slash[:split_index]
                descriptor.update(
                    {
                        "sourceType": "workspace",
                        "visibility": "scoped",
                        "workspacePath": workspace_path,
                        "rootPath": str(skill_root.parent),
                    }
                )
            return cls._build_skill_entry(
                folder_name=skill_root.name,
                file_path=skill_file,
                descriptor=descriptor,
                content=content,
            )
        except Exception:
            return None

    @classmethod
    def _scan_root_descriptors(cls, descriptors: list[dict[str, Any]]) -> dict[str, dict]:
        registry: dict[str, dict] = {}
        for descriptor in descriptors:
            base_path = Path(str(descriptor.get("rootPath") or ""))
            if not base_path.exists() or not base_path.is_dir():
                continue
            print(f"[SkillLoader] Scanning skills in {base_path} ...")
            for item in sorted(base_path.iterdir()):
                if not item.is_dir():
                    continue
                skill_file = item / "SKILL.md"
                if not skill_file.exists():
                    continue
                try:
                    content = skill_file.read_text(encoding="utf-8")
                except Exception as exc:
                    print(f"[SkillLoader] Error reading {skill_file}: {exc}")
                    continue
                entry = cls._build_skill_entry(
                    folder_name=item.name,
                    file_path=skill_file,
                    descriptor=descriptor,
                    content=content,
                )
                if entry is None:
                    continue
                entry["aliasSnapshot"] = cls._build_alias_snapshot(entry)
                registry[str(entry.get("skillId"))] = entry
                print(
                    f"[SkillLoader] Successfully loaded Skill: {entry.get('skillName')} "
                    f"({entry.get('sourceType')})"
                )
        try:
            return annotate_skill_entries(registry, record_reviews=True)  # type: ignore[return-value]
        except Exception as exc:
            print(f"[SkillLoader] Safety capability index annotation failed: {exc}")
            return registry

    @classmethod
    def ensure_fresh(cls, force: bool = False) -> None:
        if not force and cls._background_refresh_in_progress and cls._skills_registry:
            return
        now = time.monotonic()
        if not force and (now - cls._last_check_at) < cls._check_interval_seconds and cls._skills_registry:
            return
        cls._last_check_at = now
        if not force:
            cls.reload_if_changed()
            return
        descriptors = cls._discovery_root_descriptors()
        manifest = cls._compute_manifest(descriptors)
        fingerprint = cls._manifest_fingerprint(descriptors, manifest)
        cls.reload_skills(root_descriptors=descriptors, fingerprint=fingerprint)

    @classmethod
    def discover_skills(
        cls,
        skills_dir: str = "skills",
        *,
        skill_roots: list[Path] | None = None,
        root_descriptors: list[dict[str, Any]] | None = None,
        fingerprint: str | None = None,
    ) -> None:
        del skills_dir
        descriptors = root_descriptors or [
            cls._build_root_descriptor(root_path=root, source_type="global", visibility="global")
            for root in list(skill_roots or [])
        ] or cls._discovery_root_descriptors()
        descriptors = cls._dedupe_root_descriptors(descriptors)
        manifest = cls._compute_manifest(descriptors)
        registry = cls._scan_root_descriptors(descriptors)
        cls._skills_registry = registry
        cls._skills_root_descriptors = descriptors
        cls._skills_roots = [Path(item["rootPath"]) for item in descriptors]
        cls._skills_manifest = manifest
        cls._skills_root_signature = cls._root_descriptors_signature(descriptors)
        cls._skills_fingerprint = fingerprint or cls._manifest_fingerprint(descriptors, manifest)
        cls._skills_revision = cls._skills_fingerprint
        cls._last_check_at = time.monotonic()
        cls._rebuild_root_inventory_states_from_registry()

    @classmethod
    def _summarize_skill_structure(cls, skill_root: Path) -> list[str]:
        allowed_roots = ("references", "scripts", "assets", "templates", "examples")
        items: list[str] = []
        for subdir_name in allowed_roots:
            subdir = skill_root / subdir_name
            if not subdir.exists() or not subdir.is_dir():
                continue
            items.append(f"{subdir_name}/")
            for path in sorted(subdir.rglob("*")):
                if path.is_dir():
                    continue
                try:
                    relative = path.relative_to(skill_root).as_posix()
                except ValueError:
                    continue
                items.append(relative)
        return items

    @classmethod
    def _resolve_inventory_descriptors(
        cls,
        *,
        include_scoped: bool,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return cls._resolve_root_descriptors(
            include_scoped=include_scoped,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )

    @classmethod
    def _tracked_root_paths(cls) -> set[str]:
        return {
            cls._descriptor_cache_key(descriptor)
            for descriptor in cls._dedupe_root_descriptors(cls._skills_root_descriptors or cls._discovery_root_descriptors())
            if cls._descriptor_cache_key(descriptor)
        }

    @classmethod
    def _update_startup_freshness_state(cls) -> None:
        if cls._dirty_root_paths:
            cls._startup_state = "refreshing" if cls._skills_registry else "cold"
            cls._snapshot_freshness = "cached" if cls._skills_registry else "cold"
            return
        cls._startup_state = "ready"
        cls._snapshot_freshness = "live"

    @classmethod
    def refresh_root_descriptors_if_changed(
        cls,
        descriptors: list[dict[str, Any]],
        *,
        compare_existing: bool = False,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        normalized_descriptors = cls._dedupe_root_descriptors(descriptors)
        current_root_paths = {
            cls._descriptor_cache_key(descriptor)
            for descriptor in normalized_descriptors
            if cls._descriptor_cache_key(descriptor)
        }
        tracked_root_paths = cls._tracked_root_paths()
        before = {
            skill_id: cls._skill_registry_signature(item)
            for skill_id, item in cls._skills_registry.items()
        }
        before_by_manifest_key = {
            cls._entry_manifest_key(item): (skill_id, item)
            for skill_id, item in cls._skills_registry.items()
            if cls._entry_manifest_key(item)
        }
        next_states = {
            str(root_path): {
                key: (
                    {str(item_key): dict(item_value) for item_key, item_value in dict(value).items()}
                    if key in {"manifest", "registry"}
                    else dict(value)
                    if isinstance(value, dict)
                    else value
                )
                for key, value in dict(state).items()
            }
            for root_path, state in cls._root_inventory_states.items()
        }
        dirty_root_paths = set(cls._dirty_root_paths)
        changed_root_paths: set[str] = set()
        timed_out_root_paths: set[str] = set()

        for index, descriptor in enumerate(normalized_descriptors):
            root_path = cls._descriptor_cache_key(descriptor)
            if not root_path:
                continue
            if timeout_ms is not None and ((time.perf_counter() - started_at) * 1000) >= timeout_ms:
                timed_out_root_paths.add(root_path)
                dirty_root_paths.add(root_path)
                for remaining_descriptor in normalized_descriptors[index + 1 :]:
                    remaining_root_path = cls._descriptor_cache_key(remaining_descriptor)
                    if remaining_root_path:
                        timed_out_root_paths.add(remaining_root_path)
                        dirty_root_paths.add(remaining_root_path)
                break
            state = next_states.get(root_path) or {}
            descriptor_signature = cls._root_descriptors_signature([descriptor])
            manifest = cls._compute_root_manifest(descriptor)
            root_revision = cls._root_manifest_fingerprint(descriptor, manifest)
            tracked_root = root_path in tracked_root_paths or compare_existing
            changed = (
                not state
                or str(state.get("rootRevision") or "") != root_revision
                or str(state.get("descriptorSignature") or "") != descriptor_signature
            )
            if not tracked_root and not state:
                continue
            if not changed:
                dirty_root_paths.discard(root_path)
                continue
            registry = cls._scan_single_root_descriptor(descriptor)
            next_states[root_path] = {
                "descriptor": dict(descriptor),
                "descriptorSignature": descriptor_signature,
                "manifest": manifest,
                "registry": registry,
                "rootRevision": root_revision,
                "lastScanAt": cls._now_iso(),
                "dirty": False,
            }
            dirty_root_paths.discard(root_path)
            changed_root_paths.add(root_path)

        removed_root_paths: set[str] = set()
        if compare_existing:
            previous_paths = {
                cls._descriptor_cache_key(descriptor)
                for descriptor in cls._dedupe_root_descriptors(cls._skills_root_descriptors or [])
                if cls._descriptor_cache_key(descriptor)
            }
            removed_root_paths = {
                root_path
                for root_path in previous_paths
                if root_path and root_path not in current_root_paths
            }
            for root_path in removed_root_paths:
                next_states.pop(root_path, None)
                dirty_root_paths.discard(root_path)
            changed_root_paths.update(removed_root_paths)

        aggregate_descriptors = (
            normalized_descriptors
            if compare_existing
            else cls._dedupe_root_descriptors(cls._skills_root_descriptors or cls._discovery_root_descriptors())
        )
        aggregate_root_signature = cls._root_descriptors_signature(aggregate_descriptors)
        aggregate_changed = bool(changed_root_paths) or aggregate_root_signature != cls._skills_root_signature
        if aggregate_changed:
            cls._root_inventory_states = next_states
            cls._dirty_root_paths = dirty_root_paths
            cls._rebuild_aggregate_registry_from_root_states(
                descriptors=aggregate_descriptors,
                changed_root_paths=changed_root_paths or removed_root_paths,
            )
        else:
            cls._root_inventory_states = next_states
            cls._dirty_root_paths = dirty_root_paths
        cls._last_check_at = time.monotonic()

        after = {
            skill_id: cls._skill_registry_signature(item)
            for skill_id, item in cls._skills_registry.items()
        }
        before_ids = set(before)
        after_ids = set(after)
        shared_ids = before_ids & after_ids
        added_skill_ids = sorted(after_ids - before_ids)
        updated_skill_ids = sorted(
            skill_id
            for skill_id in shared_ids
            if before.get(skill_id) != after.get(skill_id)
        )
        removed_skill_ids = sorted(before_ids - after_ids)
        recent = cls._remember_recent_skill_discovery(
            added=[cls._skills_registry[skill_id] for skill_id in added_skill_ids if skill_id in cls._skills_registry],
            updated=[cls._skills_registry[skill_id] for skill_id in updated_skill_ids if skill_id in cls._skills_registry],
            refresh_mode="full" if compare_existing and not before else "delta",
        )
        if aggregate_changed:
            cls._persist_cache()
            cls._last_refresh_at = cls._now_iso()
            cls._last_refresh_error = None
        cls._update_startup_freshness_state()

        result = {
            "changed": bool(aggregate_changed),
            "refreshMode": "full" if compare_existing and not before else "delta",
            "fingerprint": cls._skills_fingerprint,
            "revision": cls._skills_revision or cls._skills_fingerprint,
            "roots": [str(item.get("rootPath") or "") for item in aggregate_descriptors],
            "rootDescriptors": list(aggregate_descriptors),
            "changedRoots": sorted(changed_root_paths),
            "dirtyRoots": sorted(dirty_root_paths),
            "inventoryReadyState": cls._startup_state,
            "addedSkills": added_skill_ids,
            "removedSkills": removed_skill_ids,
            "updatedSkills": updated_skill_ids,
            "recentSkillDiscovery": recent,
            "timedOut": bool(timed_out_root_paths),
            "timedOutRoots": sorted(timed_out_root_paths),
            "durationMs": round((time.perf_counter() - started_at) * 1000, 2),
        }
        cls._last_reload_result = dict(result)
        return result

    @classmethod
    def get_inventory(
        cls,
        *,
        force_refresh: bool = True,
        include_scoped: bool = True,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
        exclude_root_paths: set[str] | None = None,
    ) -> dict[str, Any]:
        if force_refresh:
            cls.ensure_fresh()
        elif not cls._skills_registry:
            cls.prime_startup_cache()
            if not cls._skills_registry:
                cls.ensure_fresh()

        visible_descriptors = cls._resolve_inventory_descriptors(
            include_scoped=include_scoped,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        normalized_visible_descriptors = cls._dedupe_root_descriptors(visible_descriptors)
        excluded_root_paths = {
            cls._normalize_path(item)
            for item in list(exclude_root_paths or set())
            if cls._normalize_path(item)
        }
        tracked_root_paths = set(cls._root_inventory_states)
        missing_descriptors = [
            descriptor
            for descriptor in normalized_visible_descriptors
            if cls._descriptor_cache_key(descriptor) not in tracked_root_paths
        ]
        discovery_revision = cls._skills_revision or cls._skills_fingerprint

        if not missing_descriptors:
            visible_cache_key = (
                cls._visible_inventory_cache_key(normalized_visible_descriptors)
                if not excluded_root_paths
                else ""
            )
            if visible_cache_key and visible_cache_key in cls._visible_inventory_cache:
                cached_snapshot = dict(cls._visible_inventory_cache.get(visible_cache_key) or {})
                cached_snapshot["visibleRegistryCacheHit"] = True
                cached_snapshot["dirtyVisibleRoots"] = cls._dirty_root_paths_for_descriptors(
                    cached_snapshot.get("rootDescriptors") or []
                )
                return cached_snapshot
            snapshot = cls._build_visible_inventory_from_descriptors(
                descriptors=normalized_visible_descriptors,
                discovery_revision=discovery_revision,
                visible_registry_cache_hit=False,
                exclude_root_paths=excluded_root_paths,
            )
            if visible_cache_key:
                cls._visible_inventory_cache[visible_cache_key] = dict(snapshot)
            return snapshot

        base_descriptors = [
            descriptor
            for descriptor in normalized_visible_descriptors
            if cls._descriptor_cache_key(descriptor) in tracked_root_paths
        ]
        base_snapshot = cls._build_visible_inventory_from_descriptors(
            descriptors=base_descriptors,
            discovery_revision=discovery_revision,
            scoped_refresh_mode="live_overlay",
            exclude_root_paths=excluded_root_paths,
        )
        merged_registry = dict(base_snapshot.get("registry") or {})
        missing_root_paths: list[str] = []
        fingerprint_payload: list[dict[str, str]] = [
            {
                "rootPath": cls._descriptor_cache_key(descriptor),
                "rootRevision": str(
                    (cls._root_inventory_states.get(cls._descriptor_cache_key(descriptor)) or {}).get("rootRevision") or ""
                ),
            }
            for descriptor in base_descriptors
            if cls._descriptor_cache_key(descriptor)
        ]
        for descriptor in missing_descriptors:
            root_path = cls._descriptor_cache_key(descriptor)
            if not root_path or root_path in excluded_root_paths:
                continue
            descriptor_manifest = cls._compute_root_manifest(descriptor)
            live_registry = cls._scan_single_root_descriptor(descriptor)
            merged_registry.update(live_registry)
            missing_root_paths.append(root_path)
            fingerprint_payload.append(
                {
                    "rootPath": root_path,
                    "rootRevision": cls._root_manifest_fingerprint(descriptor, descriptor_manifest),
                }
            )
        snapshot = cls._inventory_snapshot(
            registry=merged_registry,
            descriptors=[descriptor for descriptor in normalized_visible_descriptors if cls._descriptor_cache_key(descriptor) not in excluded_root_paths],
            fingerprint=hashlib.sha1(
                json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            discovery_revision=discovery_revision,
            changed_roots=missing_root_paths,
            scoped_refresh_mode="live_overlay",
            visible_registry_cache_hit=False,
        )
        snapshot["dirtyVisibleRoots"] = cls._dirty_root_paths_for_descriptors(snapshot.get("rootDescriptors") or [])
        return snapshot

    @classmethod
    def get_all_skills(
        cls,
        *,
        force_refresh: bool = True,
        include_scoped: bool = True,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> dict[str, dict]:
        inventory = cls.get_inventory(
            force_refresh=force_refresh,
            include_scoped=include_scoped,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        return dict(inventory.get("registry") or {})

    @classmethod
    def get_cached_skills(cls) -> dict[str, dict]:
        return dict(cls._skills_registry)

    @classmethod
    def delete_skill(
        cls,
        skill_id: str,
        *,
        scope: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        project_id: str | None = None,
        initiated_by: str | None = None,
    ) -> dict[str, Any]:
        normalized_skill_id = str(skill_id or "").strip()
        if not normalized_skill_id:
            raise ValueError("skillId is required")
        inventory = cls.get_inventory(
            force_refresh=True,
            include_scoped=True,
            explicit_workspace_id=workspace_id,
            explicit_workspace_path=workspace_path,
            explicit_project_id=project_id,
        )
        registry = dict(inventory.get("registry") or {})
        skill = dict(registry.get(normalized_skill_id) or {})
        if not skill:
            raise FileNotFoundError(f"Skill '{normalized_skill_id}' was not found in the visible inventory")
        normalized_scope = str(scope or "").strip().lower()
        if normalized_scope == "global" and str(skill.get("visibility") or "").strip().lower() == "scoped":
            raise PermissionError("Skill is scoped; pass scope=workspace to delete it")
        if normalized_scope == "workspace" and str(skill.get("visibility") or "").strip().lower() != "scoped":
            raise PermissionError("Skill is global; pass scope=global or omit scope to delete it")

        skill_root = Path(cls._normalize_path(skill.get("skillRoot") or skill.get("path")))
        instruction_path_text = cls._normalize_path(skill.get("instructionPath"))
        instruction_path = Path(instruction_path_text) if instruction_path_text else None
        if not skill_root.exists() or not skill_root.is_dir() or not (skill_root / "SKILL.md").exists():
            raise FileNotFoundError(f"Skill directory for '{normalized_skill_id}' does not exist")
        if instruction_path is not None and instruction_path.exists() and instruction_path.parent != skill_root:
            raise PermissionError("Skill instruction path is outside the skill root")

        root_descriptors = cls._dedupe_root_descriptors(list(inventory.get("rootDescriptors") or []))
        owner_descriptor: dict[str, Any] | None = None
        for descriptor in root_descriptors:
            descriptor_root = Path(cls._descriptor_cache_key(descriptor))
            try:
                if skill_root.parent.resolve(strict=False) == descriptor_root.resolve(strict=False):
                    owner_descriptor = descriptor
                    break
            except Exception:
                if cls._normalize_path(skill_root.parent) == cls._normalize_path(descriptor_root):
                    owner_descriptor = descriptor
                    break
        if owner_descriptor is None:
            raise PermissionError("Skill is not under a V8-managed skill root")

        removed = {
            "skillId": normalized_skill_id,
            "skillName": skill.get("name") or skill.get("skillName"),
            "skillRoot": cls._normalize_path(skill_root),
            "instructionPath": cls._normalize_path(skill_root / "SKILL.md"),
            "sourceType": skill.get("sourceType"),
            "visibility": skill.get("visibility"),
            "initiatedBy": str(initiated_by or "admin_extensions_manual_delete").strip() or "admin_extensions_manual_delete",
        }
        seed_source_dir: Path | None = None
        seed_tombstone: dict[str, Any] = {}
        if skill_root.name in _SEEDED_GLOBAL_SKILL_NAMES and str(skill.get("visibility") or "").strip().lower() != "scoped":
            candidate_source = cls._resolve_repo_root() / ".agents" / "skills" / skill_root.name
            seed_source_dir = candidate_source if candidate_source.exists() else None
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(removed.get("skillName") or skill_root.name)).strip(".-_") or "skill"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_root = Path.home() / ".v8-agent-os" / "backups" / "skills" / f"{timestamp}-{safe_name}-{hashlib.sha1(str(skill_root).encode('utf-8')).hexdigest()[:8]}"
        backup_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_root, backup_root)
        inactive_review_count = 0
        try:
            from erc.safety_guardian import safety_guardian

            inactive_review_count = safety_guardian.mark_skill_safety_reviews_inactive(
                skill_root=str(removed["skillRoot"] or ""),
                instruction_path=str(removed["instructionPath"] or ""),
            )
        except Exception:
            inactive_review_count = 0
        shutil.rmtree(skill_root)
        if skill_root.name in _SEEDED_GLOBAL_SKILL_NAMES and str(skill.get("visibility") or "").strip().lower() != "scoped":
            seed_tombstone = cls._record_skill_seed_tombstone(
                skill_name=skill_root.name,
                source_dir=seed_source_dir,
                target_dir=skill_root,
                initiated_by=str(removed.get("initiatedBy") or ""),
            )
            removed["seedTombstoned"] = bool(seed_tombstone)
            removed["seedSource"] = cls._normalize_path(seed_source_dir) if seed_source_dir else ""
        else:
            removed["seedTombstoned"] = False
            removed["seedSource"] = ""
        try:
            from core.audit_logger import audit_logger
            from core.run_ledger import run_ledger_service

            audit_logger.log(
                source_type="EXTENSIONS",
                action="skill_delete",
                status="WARNING",
                details=json.dumps(
                    {
                        **removed,
                        "backupPath": cls._normalize_path(backup_root),
                        "inactiveReviewCount": inactive_review_count,
                    },
                    ensure_ascii=False,
                ),
            )
            run_ledger_service.record_event(
                event_type="skill.deleted",
                runtime_kind="extensions",
                source="extensions.skill_loader",
                summary=f"Skill deleted by Admin: {removed.get('skillName') or normalized_skill_id}",
                refs={
                    "skillId": normalized_skill_id,
                    "skillRoot": removed.get("skillRoot"),
                    "backupPath": cls._normalize_path(backup_root),
                },
                payload={
                    "initiatedBy": removed.get("initiatedBy"),
                    "visibility": removed.get("visibility"),
                    "inactiveReviewCount": inactive_review_count,
                    "seedTombstoned": removed.get("seedTombstoned"),
                    "seedSource": removed.get("seedSource"),
                },
            )
        except Exception:
            pass
        refresh_result = cls.refresh_root_descriptors_if_changed(
            [owner_descriptor],
            compare_existing=False,
            timeout_ms=None,
        )
        return {
            "removed": removed,
            "refresh": refresh_result,
            "ledgerRetained": True,
            "ledgerState": "inactive_orphan_by_missing_path",
            "inactiveReviewCount": inactive_review_count,
            "backupPath": cls._normalize_path(backup_root),
        }

    @classmethod
    def _skill_registry_signature(cls, item: dict[str, Any]) -> str:
        instruction_path = Path(str(item.get("instructionPath") or item.get("path") or "").strip())
        parts = [
            str(item.get("skillId") or ""),
            str(item.get("name") or ""),
            str(item.get("description") or ""),
            str(instruction_path),
            str(item.get("sourceType") or ""),
            str(item.get("rootPath") or ""),
            str(item.get("contentHash") or ""),
            str(item.get("manifestHash") or ""),
            str((item.get("aliasSnapshot") or {}).get("signature") if isinstance(item.get("aliasSnapshot"), dict) else ""),
        ]
        if instruction_path.exists() and instruction_path.is_file():
            try:
                stat = instruction_path.stat()
                parts.extend([str(stat.st_mtime_ns), str(stat.st_size)])
            except OSError:
                pass
        return "|".join(parts)

    @classmethod
    def reload_if_changed(cls) -> dict[str, Any]:
        descriptors = cls._discovery_root_descriptors()
        return cls.refresh_root_descriptors_if_changed(
            descriptors,
            compare_existing=True,
            timeout_ms=cls._background_refresh_timeout_ms,
        )

    @classmethod
    def reload_skills(
        cls,
        skills_dir: str = "skills",
        *,
        skill_roots: list[Path] | None = None,
        root_descriptors: list[dict[str, Any]] | None = None,
        fingerprint: str | None = None,
    ) -> None:
        del skills_dir
        print("[SkillLoader] Reloading skills registry...")
        descriptors = root_descriptors or [
            cls._build_root_descriptor(root_path=root, source_type="global", visibility="global")
            for root in list(skill_roots or [])
        ] or cls._discovery_root_descriptors()
        cls.discover_skills(root_descriptors=descriptors, fingerprint=fingerprint)
        cls._persist_cache()
        cls._startup_state = "ready"
        cls._snapshot_freshness = "live"
        cls._last_refresh_at = cls._now_iso()
        cls._last_refresh_error = None
        print(f"[SkillLoader] Reloaded {len(cls._skills_registry)} skills.")

    @classmethod
    def prime_startup_cache(cls) -> bool:
        loaded = cls._load_cached_registry()
        if not loaded:
            cls._startup_state = "cold"
            cls._snapshot_freshness = "cold"
        return loaded

    @classmethod
    def schedule_background_refresh(cls, *, force: bool = False) -> asyncio.Task:
        current = cls._background_refresh_task
        if current and not current.done():
            return current

        cls._startup_state = "refreshing"
        cls._snapshot_freshness = "cached" if cls._skills_registry else "cold"
        cls._last_refresh_error = None

        async def _runner() -> None:
            cls._background_refresh_in_progress = True
            try:
                if force:
                    await asyncio.to_thread(cls.reload_skills)
                else:
                    await asyncio.to_thread(cls.reload_if_changed)
            except Exception as exc:
                cls._startup_state = "error"
                cls._last_refresh_error = str(exc).strip() or exc.__class__.__name__
                raise
            finally:
                cls._background_refresh_in_progress = False

        task = asyncio.create_task(_runner(), name="skills:background_refresh")
        cls._background_refresh_task = task
        return task

    @classmethod
    async def wait_for_background_refresh(cls, timeout: float | None = None) -> None:
        task = cls._background_refresh_task
        if not task:
            return
        if timeout is None:
            await asyncio.shield(task)
            return
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    @classmethod
    def get_startup_status(cls) -> dict[str, object]:
        descriptors = cls._skills_root_descriptors or cls._discovery_root_descriptors()
        roots = [str(item.get("rootPath") or "") for item in descriptors]
        return {
            "startupState": cls._startup_state,
            "snapshotFreshness": cls._snapshot_freshness,
            "lastRefreshAt": cls._last_refresh_at,
            "lastRefreshError": cls._last_refresh_error,
            "backgroundRefreshInProgress": bool(cls._background_refresh_in_progress),
            "skillCount": len(cls._skills_registry),
            "fingerprint": cls._skills_fingerprint,
            "revision": cls._skills_revision or cls._skills_fingerprint,
            "rootSignature": cls._skills_root_signature,
            "visibleRootRevisionKey": cls._visible_root_revision_key(descriptors),
            "visibleInventoryCacheSize": len(cls._visible_inventory_cache),
            "dirtyRoots": sorted(cls._dirty_root_paths),
            "lastReloadResult": dict(cls._last_reload_result or {}),
            "recentSkillDiscovery": [
                {key: value for key, value in item.items() if key != "_observedTs"}
                for item in cls._recent_skill_discovery
            ],
            "root": roots[0] if roots else "",
            "roots": roots,
            "rootDescriptors": list(descriptors),
            "cacheFile": str(cls._cache_file()),
        }

    @classmethod
    def get_system_prompt_addition(cls) -> str:
        inventory = cls.get_inventory(force_refresh=False, include_scoped=False)
        registry_items = list(inventory.get("items") or [])
        if not registry_items:
            return "No persistent skills available at the moment."

        root_descriptors = list(inventory.get("rootDescriptors") or [])
        lines = [
            "\n# Available Custom Skills",
            f"You have access to {len(registry_items)} reusable workflow skills from {len(root_descriptors)} configured roots.",
            "Use `fetch_skill_instructions` only when a task clearly matches one of these workflow areas:",
        ]
        for meta in registry_items[:8]:
            lines.append(f"- **{meta['skillName']}**: {meta['description']}")
        if len(registry_items) > 8:
            lines.append(f"- 还有 {len(registry_items) - 8} 个技能未在此处展开，请按任务领域选择最相关技能。")
        if root_descriptors:
            lines.append("当前默认扫描的 skills roots：")
            for descriptor in root_descriptors:
                lines.append(f"- {descriptor.get('sourceType')}: {descriptor.get('rootPath')}")
        lines.append("\nCRITICAL: Always read a skill's instructions before attempting a complex task relating to it!")
        return "\n".join(lines)

    @classmethod
    def resolve_skill_matches(
        cls,
        identifier: str,
        *,
        force_refresh: bool = False,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        needle = str(identifier or "").strip()
        if not needle:
            return []
        direct_entry = cls._resolve_direct_skill_entry(needle)
        if direct_entry is not None:
            return [direct_entry]
        inventory = cls.get_inventory(
            force_refresh=force_refresh,
            include_scoped=True,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        normalized_needle = needle.lower()
        query_variants = cls._skill_match_query_variants(needle)
        path_like_identifier = cls._looks_like_skill_path_identifier(needle)
        precise_identifier = cls._looks_like_precise_skill_identifier(needle)

        def _dedupe(entries_to_sort: list[dict[str, Any]]) -> list[dict[str, Any]]:
            deduped: list[dict[str, Any]] = []
            seen: set[str] = set()
            for candidate in sorted(
                entries_to_sort,
                key=lambda item: (
                    str(item.get("skillName") or item.get("name") or item.get("folder") or "").lower(),
                    str(item.get("skillRoot") or item.get("path") or "").lower(),
                ),
            ):
                key = str(candidate.get("skillId") or candidate.get("skillRoot") or candidate.get("path") or "").strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                deduped.append(candidate)
            return deduped

        def _match(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            exact_matches: list[dict[str, Any]] = []
            hint_matches: list[dict[str, Any]] = []
            fuzzy_scored: list[tuple[int, dict[str, Any]]] = []
            for entry in entries:
                skill_id = str(entry.get("skillId") or "").strip()
                skill_name = str(entry.get("skillName") or entry.get("name") or entry.get("folder") or "").strip()
                folder_name = str(entry.get("folder") or "").strip()
                skill_root = cls._normalize_path(entry.get("skillRoot") or entry.get("path"))
                instruction_path = cls._normalize_path(entry.get("instructionPath"))
                candidates = {
                    skill_id.lower(),
                    skill_name.lower(),
                    folder_name.lower(),
                    skill_root.lower(),
                    instruction_path.lower(),
                }
                if normalized_needle in {candidate for candidate in candidates if candidate}:
                    exact_matches.append(entry)
                    continue
                hint_candidates = {
                    cls._normalize_text(item)
                    for key in ("aliases", "triggers", "keywords", "tags")
                    for item in cls._normalize_hint_items(entry.get(key))
                }
                if cls._normalize_text(needle) in {candidate for candidate in hint_candidates if candidate}:
                    hint_matches.append(entry)
                    continue
                fuzzy_score = cls._score_skill_match_entry(entry, query_variants)
                if fuzzy_score >= _SKILL_MATCH_FUZZY_MIN_SCORE:
                    fuzzy_scored.append((fuzzy_score, entry))
            if exact_matches:
                return _dedupe(exact_matches)
            if hint_matches:
                return _dedupe(hint_matches)
            if path_like_identifier or precise_identifier:
                return []
            if not fuzzy_scored:
                return []
            fuzzy_scored.sort(
                key=lambda item: (
                    -item[0],
                    str(item[1].get("skillName") or item[1].get("name") or item[1].get("folder") or "").lower(),
                    str(item[1].get("skillRoot") or item[1].get("path") or "").lower(),
                )
            )
            top_score = int(fuzzy_scored[0][0])
            if len(fuzzy_scored) == 1:
                return [fuzzy_scored[0][1]]
            second_score = int(fuzzy_scored[1][0])
            if top_score <= 0:
                return []
            if top_score < int(round(second_score * _SKILL_MATCH_AMBIGUITY_RATIO)) and (top_score - second_score) <= _SKILL_MATCH_AMBIGUITY_GAP:
                ambiguous = [
                    entry
                    for score, entry in fuzzy_scored
                    if (top_score - int(score)) <= _SKILL_MATCH_AMBIGUITY_GAP
                ]
                return _dedupe(ambiguous[:12])
            return [fuzzy_scored[0][1]]

        matches = _match(list(inventory.get("items") or []))
        if matches or force_refresh:
            return matches
        targeted_refresh = cls._refresh_missing_skill_candidates(
            needle,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        if targeted_refresh.get("refreshed"):
            refreshed_inventory = cls.get_inventory(
                force_refresh=False,
                include_scoped=True,
                runtime_kind=runtime_kind,
                session_id=session_id,
                explicit_workspace_id=explicit_workspace_id,
                explicit_workspace_path=explicit_workspace_path,
                explicit_project_id=explicit_project_id,
            )
            refreshed_matches = _match(list(refreshed_inventory.get("items") or []))
            if refreshed_matches:
                return refreshed_matches
        change = cls.reload_if_changed()
        if not change.get("changed"):
            return []
        refreshed = cls.get_inventory(
            force_refresh=False,
            include_scoped=True,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        return _match(list(refreshed.get("items") or []))

    @classmethod
    def _refresh_missing_skill_candidates(
        cls,
        identifier: str,
        *,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> dict[str, Any]:
        needle = str(identifier or "").strip()
        if not needle or cls._looks_like_skill_path_identifier(needle):
            return {"refreshed": False, "reason": "path_like_or_empty"}
        descriptors: list[dict[str, Any]] = []
        scoped = cls._scoped_workspace_root_descriptor(
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        if scoped is not None:
            descriptors.append(scoped)
        descriptors.append(cls._global_root_descriptor())
        descriptors = cls._dedupe_root_descriptors(descriptors)
        candidate_descriptors = [
            descriptor
            for descriptor in descriptors
            if cls._root_has_missing_skill_candidate(descriptor, needle)
        ]
        if not candidate_descriptors:
            cls._last_reload_result = {
                **dict(cls._last_reload_result or {}),
                "missingSkillTargetedRefresh": {
                    "identifier": needle,
                    "refreshed": False,
                    "roots": [cls._descriptor_cache_key(item) for item in descriptors],
                    "reason": "no_one_level_candidate",
                },
            }
            return {"refreshed": False, "reason": "no_one_level_candidate"}

        changed_root_paths: set[str] = set()
        next_states = {
            str(root_path): {
                key: (
                    {str(item_key): dict(item_value) for item_key, item_value in dict(value).items()}
                    if key in {"manifest", "registry"}
                    else dict(value)
                    if isinstance(value, dict)
                    else value
                )
                for key, value in dict(state).items()
            }
            for root_path, state in cls._root_inventory_states.items()
        }
        for descriptor in candidate_descriptors:
            root_path = cls._descriptor_cache_key(descriptor)
            if not root_path:
                continue
            manifest = cls._compute_root_manifest(descriptor)
            registry = cls._scan_single_root_descriptor(descriptor, manifest=manifest)
            next_states[root_path] = {
                "descriptor": dict(descriptor),
                "descriptorSignature": cls._root_descriptors_signature([descriptor]),
                "manifest": manifest,
                "registry": registry,
                "rootRevision": cls._root_manifest_fingerprint(descriptor, manifest),
                "lastScanAt": cls._now_iso(),
                "dirty": False,
            }
            changed_root_paths.add(root_path)

        if not changed_root_paths:
            return {"refreshed": False, "reason": "no_changed_roots"}
        cls._root_inventory_states = next_states
        aggregate_descriptors = cls._dedupe_root_descriptors(
            [
                *list(cls._skills_root_descriptors or cls._discovery_root_descriptors()),
                *candidate_descriptors,
            ]
        )
        cls._rebuild_aggregate_registry_from_root_states(
            descriptors=aggregate_descriptors,
            changed_root_paths=changed_root_paths,
        )
        cls._dirty_root_paths.difference_update(changed_root_paths)
        recent = cls._remember_recent_skill_discovery(
            added=[
                item
                for item in cls._skills_registry.values()
                if cls._normalize_path(item.get("rootPath")) in changed_root_paths
            ],
            updated=[],
            refresh_mode="missing_skill_targeted",
        )
        cls._last_reload_result = {
            **dict(cls._last_reload_result or {}),
            "changed": True,
            "refreshMode": "missing_skill_targeted",
            "missingSkillTargetedRefresh": {
                "identifier": needle,
                "refreshed": True,
                "roots": sorted(changed_root_paths),
                "recentSkillDiscovery": recent,
            },
        }
        cls._update_startup_freshness_state()
        return {"refreshed": True, "changedRoots": sorted(changed_root_paths)}

    @classmethod
    def _root_has_missing_skill_candidate(cls, descriptor: dict[str, Any], identifier: str) -> bool:
        root_path = cls._descriptor_cache_key(descriptor)
        if not root_path:
            return False
        root = Path(root_path)
        if not root.exists() or not root.is_dir():
            return False
        needle = cls._normalize_text(identifier)
        normalized_folder = cls._normalize_text(str(identifier or "").strip().strip("\"'"))
        direct = root / str(identifier or "").strip().strip("\"'")
        if direct.exists() and direct.is_dir() and (direct / "SKILL.md").exists():
            return True
        for skill_file in sorted(root.glob("*/SKILL.md")):
            folder = cls._normalize_text(skill_file.parent.name)
            if folder == normalized_folder:
                return True
            try:
                text = skill_file.read_text(encoding="utf-8", errors="replace")[:4000]
            except Exception:
                continue
            try:
                body = text
                if text.startswith("---"):
                    end = text.find("\n---", 3)
                    if end > 0:
                        meta = yaml.safe_load(text[3:end]) or {}
                        if isinstance(meta, dict) and cls._normalize_text(str(meta.get("name") or "")) == needle:
                            return True
                        body = text[end + 4 :]
                first_heading = ""
                for line in body.splitlines()[:24]:
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        first_heading = stripped.lstrip("#").strip()
                        break
                if first_heading and cls._normalize_text(first_heading) == needle:
                    return True
            except Exception:
                continue
        return False


def _skill_instruction_headings(markdown: str, *, limit: int = 24) -> list[str]:
    headings: list[str] = []
    for line in str(markdown or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        title = stripped.lstrip("#").strip()
        if title:
            headings.append(title)
        if len(headings) >= limit:
            break
    return headings


def _skill_instruction_intro(markdown: str, *, limit: int = 1200) -> str:
    lines: list[str] = []
    for line in str(markdown or "").splitlines():
        stripped = line.rstrip()
        if stripped.startswith("## ") and lines:
            break
        lines.append(stripped)
        if sum(len(item) for item in lines) >= limit:
            break
    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}\n...[summary truncated]"


def _skill_instruction_section(markdown: str, section: str | None, *, limit: int = 5000) -> str:
    target = str(section or "").strip().lower()
    if not target:
        return _skill_instruction_intro(markdown, limit=limit)
    lines = str(markdown or "").splitlines()
    start = -1
    start_level = 0
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        title = stripped.lstrip("#").strip().lower()
        if target in title:
            start = idx
            start_level = len(stripped) - len(stripped.lstrip("#"))
            break
    if start < 0:
        return f"Section not found: {section}\nAvailable sections:\n" + "\n".join(f"- {item}" for item in _skill_instruction_headings(markdown))
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        if level <= start_level:
            end = idx
            break
    text = "\n".join(lines[start:end]).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}\n...[section truncated; request detail_level='full' only when explicitly needed]"


def _normalize_skill_relative_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    raw = raw.lstrip("/")
    parts = [part for part in raw.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("relative_path must stay inside the skill directory")
    if ":" in parts[0]:
        raise ValueError("relative_path must be relative, not an absolute drive path")
    return "/".join(parts)


def _looks_like_text_skill_file(path: Path) -> bool:
    if path.suffix.lower() in {
        ".md",
        ".txt",
        ".rst",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".py",
        ".sh",
        ".ps1",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".html",
        ".css",
    }:
        return True
    return path.name.upper() in {"README", "LICENSE"}


def _read_skill_text_file(path: Path, *, max_chars: int = 220_000, offset: int = 0) -> tuple[str, int, int, bool]:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ValueError("skill file appears to be binary")
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            text = ""
    if not text:
        text = raw.decode("utf-8", errors="replace")
    total_chars = len(text)
    safe_offset = max(0, min(int(offset or 0), total_chars))
    chunk = text[safe_offset : safe_offset + max_chars]
    truncated = safe_offset + len(chunk) < total_chars
    return chunk.rstrip(), safe_offset, total_chars, truncated


def _filter_parent_resource_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in list(paths or []):
        path = str(item or "").strip().replace("\\", "/").strip("/")
        if path and path not in normalized:
            normalized.append(path)
    filtered: list[str] = []
    for path in normalized:
        directory_prefix = f"{path}/"
        if any(other != path and other.startswith(directory_prefix) for other in normalized):
            continue
        filtered.append(path)
    return filtered


def _skill_continuation_manifest(skill: dict[str, Any], available_files: list[str]) -> dict[str, Any]:
    def _norm(item: Any) -> str:
        return str(item or "").strip().replace("\\", "/").lstrip("/")

    files = [_norm(item) for item in list(available_files or []) if _norm(item)]
    references = _filter_parent_resource_paths([item for item in files if item.startswith("references/")])
    scripts = _filter_parent_resource_paths([item for item in files if item.startswith("scripts/")])
    templates = _filter_parent_resource_paths([
        item
        for item in files
        if item.startswith("templates/") or "template" in Path(item).name.lower()
    ])
    frameworks = _filter_parent_resource_paths([
        item
        for item in files
        if "framework" in Path(item).name.lower() or "method" in Path(item).name.lower()
    ])
    examples = _filter_parent_resource_paths([item for item in files if item.startswith("examples/") and item.endswith("SKILL.md")])
    priority: list[str] = []
    for bucket in (templates, frameworks, references):
        for item in bucket:
            if item.lower().endswith((".md", ".txt", ".rst")) and item not in priority:
                priority.append(item)
    script_hints = []
    for item in scripts[:12]:
        name = Path(item).name
        purpose = "supporting script; read before executing and run only through governed tools"
        lower = name.lower()
        if "subtitle" in lower:
            purpose = "subtitle/media transcript helper; execute only when the task has media evidence and permission"
        elif "quality" in lower:
            purpose = "quality check helper; useful after generating the skill artifact"
        elif "merge" in lower:
            purpose = "research merge helper; useful after collecting per-dimension research notes"
        script_hints.append({"path": item, "purpose": purpose})

    return {
        "schema": "v8.skill_continuation_manifest.v1",
        "skillName": skill.get("skillName") or skill.get("name") or "",
        "readContract": {
            "primary": "SKILL.md has already been loaded by this tool call.",
            "nextStep": "Use fetch_skill_instructions(skill_name, relative_path='<path>') to continue into references/templates/scripts when the task needs implementation detail.",
            "artifactRule": "For creating or updating a Skill artifact, template/framework reads are mandatory before drafting; direct filesystem reads do not satisfy the Skill contract because the loader resolver, safety review, and raw-ref surface must be exercised.",
            "doNotInlineEverything": True,
        },
        "requiredReadsForArtifact": [
            item for item in priority[:10] if item in templates or item in frameworks
        ],
        "recommendedReads": priority[:10],
        "references": references[:24],
        "templates": templates[:12],
        "frameworks": frameworks[:12],
        "scripts": script_hints,
        "examples": examples[:12],
        "scriptExecutionBoundary": "Scripts are method assets, not permissions. Read them first; any execution must use existing governed command/runtime tools.",
    }


def _format_skill_resource_tree(available_files: list[str], *, limit: int = 96) -> str:
    normalized = _filter_parent_resource_paths([str(item or "") for item in list(available_files or [])])
    if not normalized:
        return "- (no extra references/scripts/assets/templates/examples found)"

    root: dict[str, Any] = {}
    for path in sorted(normalized):
        node = root
        parts = [part for part in path.split("/") if part]
        for index, part in enumerate(parts):
            key = part + "/" if index < len(parts) - 1 else part
            node = node.setdefault(key, {})

    lines: list[str] = []

    def _walk(node: dict[str, Any], depth: int = 0) -> None:
        for name, child in sorted(node.items(), key=lambda item: (not item[0].endswith("/"), item[0].lower())):
            if len(lines) >= limit:
                return
            lines.append(f"{'  ' * depth}- {name}")
            if isinstance(child, dict) and child:
                _walk(child, depth + 1)

    _walk(root)
    if len(normalized) > limit:
        lines.append(f"...[{len(normalized) - limit} more relative resource paths omitted]")
    return "\n".join(lines)


def _format_skill_continuation_manifest(manifest: dict[str, Any]) -> str:
    lines = ["=== CONTINUATION MANIFEST ==="]
    skill_name = str(manifest.get("skillName") or "").strip()
    lines.append("SKILL.md above is the primary method contract. Do not replace it with this resource list.")
    if skill_name:
        lines.append(f"Continue reading skill-relative files with: fetch_skill_instructions(skill_name={skill_name!r}, relative_path='<path>')")
    else:
        lines.append("Continue reading skill-relative files with: fetch_skill_instructions(skill_name='<skill>', relative_path='<path>')")
    required_reads = [str(item) for item in list(manifest.get("requiredReadsForArtifact") or []) if str(item).strip()]
    recommended_reads = [str(item) for item in list(manifest.get("recommendedReads") or []) if str(item).strip()]
    references = [str(item) for item in list(manifest.get("references") or []) if str(item).strip()]
    templates = [str(item) for item in list(manifest.get("templates") or []) if str(item).strip()]
    frameworks = [str(item) for item in list(manifest.get("frameworks") or []) if str(item).strip()]
    examples = [str(item) for item in list(manifest.get("examples") or []) if str(item).strip()]
    scripts = [item for item in list(manifest.get("scripts") or []) if isinstance(item, dict)]

    def _append_list(title: str, values: list[str], *, cap: int = 12) -> None:
        if not values:
            return
        lines.append(f"{title}:")
        for value in values[:cap]:
            lines.append(f"- {value}")
        if len(values) > cap:
            lines.append(f"- ...[{len(values) - cap} more]")

    _append_list("Required reads for artifact work", required_reads, cap=10)
    _append_list("Recommended next reads", recommended_reads, cap=10)
    _append_list("References", references, cap=16)
    _append_list("Templates", templates, cap=10)
    _append_list("Frameworks / methods", frameworks, cap=10)
    _append_list("Examples", examples, cap=10)
    if scripts:
        lines.append("Scripts:")
        for item in scripts[:12]:
            path = str(item.get("path") or "").strip()
            purpose = str(item.get("purpose") or "").strip()
            if path:
                lines.append(f"- {path}" + (f" — {purpose}" if purpose else ""))
    boundary = str(manifest.get("scriptExecutionBoundary") or "").strip()
    if boundary:
        lines.append(f"Script boundary: {boundary}")
    return "\n".join(lines)


@tool
def fetch_skill_instructions(
    skill_name: str,
    detail_level: str = "full",
    section: str | None = None,
    relative_path: str | None = None,
    offset: int = 0,
) -> str:
    """Read complete SKILL.md workflow instructions by exact skill name/path, then continue skill-relative docs/scripts via relative_path.

    Default detail_level='full' returns the approved SKILL.md instructions so delegated agents can follow the workflow.
    Use detail_level='summary' only for browsing/discovery, or detail_level='section' with section='...' for a specific heading.
    If the conversation, route context, or delegated brief already names an exact installed Skill, call this tool directly with that skill_name even when the current prefilter did not select it.
    Pass relative_path='references/foo.md' to continue reading a file inside that skill directory after inspecting the continuation manifest.
    When a relative file is too long, pass offset=<next offset> to continue the same document without switching to raw workspace reads.
    """

    runtime_kind = "chat"
    session_id = None
    explicit_workspace_id = None
    explicit_workspace_path = None
    explicit_project_id = None
    try:
        from erc.runtime_context import get_runtime_context

        runtime_context = get_runtime_context()
        runtime_kind = str(runtime_context.get("runtime_kind") or "chat")
        session_id = str(runtime_context.get("session_id") or "").strip() or None
        explicit_workspace_id = str(runtime_context.get("workspace_id") or "").strip() or None
        explicit_workspace_path = str(runtime_context.get("workspace_path") or "").strip() or None
        explicit_project_id = str(runtime_context.get("project_id") or "").strip() or None
    except Exception:
        pass

    matches = SkillLoader.resolve_skill_matches(
        skill_name,
        force_refresh=False,
        runtime_kind=runtime_kind,
        session_id=session_id,
        explicit_workspace_id=explicit_workspace_id,
        explicit_workspace_path=explicit_workspace_path,
        explicit_project_id=explicit_project_id,
    )
    if len(matches) > 1:
        lines = [
            "Error: 找到了多个同名或同引用的 skill，请改用 skillId 或绝对路径精确指定：",
        ]
        for skill in matches[:12]:
            lines.append(
                f"- {skill.get('skillName')} | id={skill.get('skillId')} | "
                f"source={skill.get('sourceType')} | root={skill.get('skillRoot')}"
            )
        return "\n".join(lines)
    if not matches:
        status = SkillLoader.get_startup_status()
        targeted = (status.get("lastReloadResult") or {}).get("missingSkillTargetedRefresh") if isinstance(status.get("lastReloadResult"), dict) else {}
        roots = "\n".join(f"- {item}" for item in list(status.get("roots") or [])[:8]) or "- (no visible skill roots)"
        recent_items = list(status.get("recentSkillDiscovery") or [])[:8]
        recent = "\n".join(
            f"- {item.get('skillName') or item.get('skillId')} | {item.get('reason')} | {item.get('skillRoot')}"
            for item in recent_items
        ) or "- (no recent skill discovery)"
        targeted_lines = ""
        if isinstance(targeted, dict) and targeted:
            targeted_lines = (
                "Targeted refresh:\n"
                f"- identifier: {targeted.get('identifier') or skill_name}\n"
                f"- refreshed: {targeted.get('refreshed')}\n"
                f"- roots: {', '.join(str(item) for item in list(targeted.get('roots') or [])[:4]) or '(none)'}\n"
            )
        return (
            f"Error: The requested skill '{skill_name}' was not found in the registry after a freshness check.\n"
            f"Skill inventory revision: {status.get('revision') or status.get('fingerprint') or 'unknown'}\n"
            f"Visible skill roots:\n{roots}\n"
            f"{targeted_lines}"
            f"Recent skill discovery:\n{recent}\n"
            "If the skill was just installed, confirm that its SKILL.md lives directly under one of the visible skill roots."
        )

    skill = matches[0]
    scan_payload: dict[str, Any] | None = None
    review_payload: dict[str, Any] | None = None
    try:
        from core.audit_logger import audit_logger
        from erc.safety_guardian import safety_guardian

        ledger_review = safety_guardian.get_skill_safety_review(
            skill_id=str(skill.get("skillId") or ""),
            skill_name=skill.get("name") or skill_name,
            skill_root=skill.get("path") or "",
            instruction_path=skill.get("instructionPath") or "",
        )
        if ledger_review:
            scan_payload = safety_guardian.skill_review_to_scan_payload(ledger_review)
        else:
            scan_payload = safety_guardian.assess_skill_directory(
                skill_name=skill.get("name") or skill_name,
                skill_root=skill.get("path") or "",
                instruction_path=skill.get("instructionPath") or "",
            )
            scan_payload["reviewMode"] = "rules_only_fetch_fallback"
            ledger_review = safety_guardian.record_skill_safety_review(
                skill_id=str(skill.get("skillId") or ""),
                skill_name=skill.get("name") or skill_name,
                skill_root=skill.get("path") or "",
                instruction_path=skill.get("instructionPath") or "",
                scan_payload=scan_payload,
                llm_review=review_payload,
            )
            scan_payload = safety_guardian.skill_review_to_scan_payload(ledger_review)
        audit_logger.log(
            source_type="SAFETY",
            action="skill_scan",
            status=(
                "ERROR"
                if scan_payload.get("verdict") == "block"
                else "WARNING"
                if scan_payload.get("verdict") == "review"
                else "INFO"
            ),
            details=json.dumps(
                {
                    "skillId": skill.get("skillId") or "",
                    "skillName": skill.get("name") or skill_name,
                    "skillPath": skill.get("path") or "",
                    "instructionPath": skill.get("instructionPath") or "",
                    **scan_payload,
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        scan_payload = None

    if scan_payload and scan_payload.get("verdict") == "block":
        try:
            from core.extensions_runtime import extensions_runtime_service

            extensions_runtime_service.emit_skill_blocked(
                skill_id=str(skill.get("skillId") or ""),
                skill_name=skill.get("name") or skill_name,
                skill_path=skill.get("path") or "",
                root_path=skill.get("rootPath") or skill.get("path") or "",
                source_type=str(skill.get("sourceType") or ""),
                verdict=str(scan_payload.get("verdict") or "block"),
                confidence=float(scan_payload.get("confidence") or 0.0),
                skill_trust_score=int(scan_payload.get("skillTrustScore") or 0),
                audit_id=str(scan_payload.get("auditId") or ""),
                reasons=list(scan_payload.get("reasons") or []),
                flagged_files=list(scan_payload.get("flaggedFiles") or []),
            )
        except Exception:
            pass
        reasons = "\n".join(f"- {item}" for item in list(scan_payload.get("reasons") or [])[:8]) or "- Safety Guardian 未提供具体原因。"
        flagged_files = "\n".join(
            f"- {item.get('path')}: {', '.join(str(entry.get('label') or '') for entry in list(item.get('findings') or [])[:4] if str(entry.get('label') or '').strip()) or '高风险特征'}"
            for item in list(scan_payload.get("flaggedFiles") or [])[:12]
        ) or "- 未返回命中文件详情。"
        return (
            f"=== SKILL BLOCKED BY SAFETY GUARDIAN ===\n"
            f"Skill ID: {skill.get('skillId') or ''}\n"
            f"Skill Name: {skill.get('skillName') or skill.get('name') or skill_name}\n"
            f"Skill Root: {skill.get('skillRoot') or skill.get('path') or ''}\n"
            f"Source Type: {skill.get('sourceType') or ''}\n"
            f"Verdict: {scan_payload.get('verdict')}\n"
            f"Confidence: {scan_payload.get('confidence')}\n"
            f"Skill Trust Score: {scan_payload.get('skillTrustScore')}\n"
            f"Audit ID: {scan_payload.get('auditId')}\n"
            f"Reasons:\n{reasons}\n"
            f"Flagged Files:\n{flagged_files}\n\n"
            f"Safety Guardian 已阻断该 skill 的说明读取。不要继续使用这个 skill，"
            f"请改用其他 skill、MCP、插件工具或系统工具继续完成当前任务。"
        )

    if (
        scan_payload
        and str(scan_payload.get("verdict") or "").strip().lower() == "review"
        and str(scan_payload.get("userOverride") or "").strip().lower() != "approved"
    ):
        reasons = "\n".join(f"- {item}" for item in list(scan_payload.get("reasons") or [])[:8]) or "- Safety Guardian 未提供具体原因。"
        flagged_files = "\n".join(
            f"- {item.get('path')}: {', '.join(str(entry.get('label') or '') for entry in list(item.get('findings') or [])[:4] if str(entry.get('label') or '').strip()) or '需要人工复核'}"
            for item in list(scan_payload.get("flaggedFiles") or [])[:12]
        ) or "- 未返回命中文件详情。"
        return (
            f"=== SKILL APPROVAL REQUIRED BY SAFETY GUARDIAN ===\n"
            f"Skill ID: {skill.get('skillId') or ''}\n"
            f"Skill Name: {skill.get('skillName') or skill.get('name') or skill_name}\n"
            f"Skill Root: {skill.get('skillRoot') or skill.get('path') or ''}\n"
            f"Verdict: review\n"
            f"Ledger ID: {scan_payload.get('ledgerId') or scan_payload.get('auditId') or ''}\n"
            f"Skill Trust Score: {scan_payload.get('skillTrustScore')}\n"
            f"Reasons:\n{reasons}\n"
            f"Flagged Files:\n{flagged_files}\n\n"
            "Safety Guardian 已允许该 skill 出现在候选列表中，但在用户批准前不会暴露完整 SKILL.md。"
            "请请求用户在 Admin / Safety Runtime 中 approve 该 skill，或改用其他已批准能力。"
        )

    safety_banner = ""
    if scan_payload and scan_payload.get("verdict") in {"audit", "review"}:
        verdict = str(scan_payload.get("verdict") or "").strip().lower()
        reasons = "\n".join(f"- {item}" for item in list(scan_payload.get("reasons") or [])[:6]) or "- Safety Guardian 未返回额外说明。"
        banner_title = "=== SKILL SAFETY REVIEW ==="
        banner_mode = "审计放行" if verdict == "audit" else "允许读取，但建议复核"
        safety_banner = (
            f"{banner_title}\n"
            f"Skill ID: {skill.get('skillId') or ''}\n"
            f"Skill Name: {skill.get('skillName') or skill.get('name') or skill_name}\n"
            f"Verdict: {verdict}\n"
            f"Mode: {banner_mode}\n"
            f"Governance Target: {scan_payload.get('governanceTarget') or 'skill_supply_chain'}\n"
            f"Posture: {scan_payload.get('posture') or ''}\n"
            f"Audit ID: {scan_payload.get('auditId')}\n"
            f"Reasons:\n{reasons}\n\n"
        )

    try:
        from core.extensions_runtime import extensions_runtime_service

        extensions_runtime_service.emit_skill_loaded(
            skill_id=str(skill.get("skillId") or ""),
            skill_name=skill["name"],
            skill_path=skill["path"],
        )
    except Exception:
        pass

    available_files = list(skill.get("availableFiles") or [])
    manifest = _skill_continuation_manifest(skill, available_files)
    if str(relative_path or "").strip():
        try:
            normalized_relative_path = _normalize_skill_relative_path(str(relative_path or ""))
            skill_root = Path(str(skill.get("skillRoot") or skill.get("path") or "")).resolve(strict=False)
            target = (skill_root / normalized_relative_path).resolve(strict=False)
            try:
                target.relative_to(skill_root)
            except ValueError:
                raise ValueError("relative_path escapes the skill directory")
            if not target.exists() or not target.is_file():
                raise FileNotFoundError(f"skill file not found: {normalized_relative_path}")
            if not _looks_like_text_skill_file(target):
                raise ValueError(f"skill file is not a supported text file: {normalized_relative_path}")
            content, read_offset, total_chars, truncated = _read_skill_text_file(target, offset=offset)
            execution_hint = ""
            if normalized_relative_path.startswith("scripts/"):
                execution_hint = (
                    "\nExecution Boundary: This is a script asset. Reading it does not grant permission to run it; "
                    "execute only through governed command/runtime tools when the task explicitly requires it."
                )
            next_offset = read_offset + len(content)
            continuation_api = (
                f"fetch_skill_instructions(skill_name={skill_name!r}, relative_path={normalized_relative_path!r}, offset={next_offset})"
                if truncated
                else f"fetch_skill_instructions(skill_name={skill_name!r}, relative_path='<path>')"
            )
            return (
                "=== SKILL FILE ===\n"
                f"Skill Name: {skill.get('skillName') or skill.get('name') or skill_name}\n"
                f"Relative Path: {normalized_relative_path}\n"
                f"Read Offset: {read_offset}\n"
                f"Returned Chars: {len(content)}\n"
                f"Total Chars: {total_chars}\n"
                f"Next Offset: {next_offset if truncated else ''}\n"
                f"Continuation API: {continuation_api}\n"
                f"{execution_hint}\n\n"
                "=== FILE CONTENT ===\n"
                f"{content}"
            )
        except Exception as exc:
            return (
                "=== SKILL FILE ERROR ===\n"
                f"Skill Name: {skill.get('skillName') or skill.get('name') or skill_name}\n"
                f"Requested Path: {relative_path}\n"
                f"Error: {exc}\n"
                "Visible continuation manifest:\n"
                f"{json.dumps(manifest, ensure_ascii=False, indent=2)}"
            )
    structure = _format_skill_resource_tree(available_files)
    normalized_detail = str(detail_level or "summary").strip().lower()
    if normalized_detail not in {"summary", "section", "full"}:
        normalized_detail = "summary"
    headings = _skill_instruction_headings(skill.get("instructions") or "")
    outline = "\n".join(f"- {item}" for item in headings[:24]) if headings else "- (no markdown headings found)"
    intro = _skill_instruction_intro(skill.get("instructions") or "")
    section_text = _skill_instruction_section(skill.get("instructions") or "", section)
    instructions_block = ""
    if normalized_detail == "full":
        full_instructions = str(skill.get("instructions") or "")
        try:
            instruction_offset = max(0, min(int(offset or 0), len(full_instructions)))
        except (TypeError, ValueError):
            instruction_offset = 0
        visible_instructions = full_instructions[instruction_offset:]
        instructions_block = (
            "=== INSTRUCTIONS (FULL) ===\n"
            f"Read Offset: {instruction_offset}\n"
            f"Returned Chars: {len(visible_instructions)}\n"
            f"Total Chars: {len(full_instructions)}\n"
            f"Next Offset: \n\n"
            f"{visible_instructions}"
        )
    elif normalized_detail == "section":
        instructions_block = (
            f"=== INSTRUCTIONS SECTION ===\n"
            f"Requested Section: {section or '(intro)'}\n"
            f"{section_text}"
        )
    else:
        instructions_block = (
            "=== INSTRUCTIONS SUMMARY ===\n"
            f"{intro}\n\n"
            "=== SECTION OUTLINE ===\n"
            f"{outline}\n\n"
            "Need execution detail? Call fetch_skill_instructions(skill_name, detail_level='full') "
            "or fetch_skill_instructions(skill_name, detail_level='section', section='<heading>')."
        )
    return (
        f"{safety_banner}"
        f"=== SKILL ENTRYPOINTS ===\n"
        f"Skill ID: {skill.get('skillId') or ''}\n"
        f"Source Type: {skill.get('sourceType') or ''}\n"
        f"Skill Root: {skill.get('skillRoot') or skill.get('path') or ''}\n"
        f"Relative Resources:\n{structure}\n\n"
        f"{_format_skill_continuation_manifest(manifest)}\n\n"
        f"{instructions_block}"
    )
