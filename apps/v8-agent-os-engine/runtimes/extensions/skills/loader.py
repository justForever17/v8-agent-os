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
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from core.llm_factory import llm_factory
from core.model_control_plane import model_control_plane
from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME
from core.workspace_resolution import workspace_resolution_service
from runtimes.memory.project_registry import project_registry_service


_PROFILE_LLM_TIMEOUT_SECONDS = 6.0
_SKILLS_CACHE_SCHEMA_VERSION = 8
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
    "skill": ("skill", "skills", "persona", "advisor", "思维顾问", "人物skill"),
}
_OPERATION_RULES: dict[str, tuple[str, ...]] = {
    "create": (
        "create",
        "creation",
        "generate",
        "generated",
        "generating",
        "build",
        "draft",
        "make",
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
        "视频",
        "youtube",
        "thumbnail",
        "hook",
        "retention",
        "attention",
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
    "organizational_design": ("organization_leadership",),
    "attention_arbitrage": ("content_media", "wealth_money"),
    "cognitive_bias": ("decision_quality",),
    "leverage": ("wealth_money", "startup_growth"),
    "talent_density": ("organization_leadership",),
}
_THEME_HEAVY_CLASSES = {"advisor_or_perspective", "methodology_or_tutorial"}


class SkillLoader:
    _skills_registry: dict[str, dict] = {}
    _skills_fingerprint: str = ""
    _skills_manifest: dict[str, dict[str, Any]] = {}
    _skills_root_signature: str = ""
    _skills_revision: str = ""
    _skills_roots: list[Path] = []
    _skills_root_descriptors: list[dict[str, Any]] = []
    _recent_skill_discovery: list[dict[str, Any]] = []
    _last_reload_result: dict[str, Any] = {}
    _last_check_at: float = 0.0
    _check_interval_seconds: float = 0.75
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
        intro_text = cls._extract_intro_text(body)
        explicit_extensions = " ".join(cls._extract_extension_evidence(available_files))
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
            (description, 2.5),
            (intro_text, 1.75),
        ]
        weak_segments = [
            (str(body or "")[:3200], 0.75),
            (" ".join(str(item or "") for item in list(available_files or [])), 0.5),
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
        if skill_class in {"advisor_or_perspective", "methodology_or_tutorial", "workflow_or_script", "integration_or_tooling"}:
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
            response = model.invoke(
                [
                    SystemMessage(
                        content=(
                            "你是 V8 Agent OS 的 skill 主题画像归一器。\n"
                            "任务：为顾问类、方法论类或其他 skill 输出稳定的主题画像。\n"
                            "只返回 JSON："
                            "{\"primaryThemes\":[...],\"secondaryThemeTags\":[...],\"themeConfidence\":0.0}\n"
                            "要求：primaryThemes 最多 2 个；secondaryThemeTags 最多 5 个；"
                            "不要输出解释文本。"
                        )
                    ),
                    HumanMessage(content=prompt_payload),
                ],
                config={"callbacks": []},
            )
            content = getattr(response, "content", response)
            text = content if isinstance(content, str) else str(content or "")
            try:
                payload = json.loads(text)
            except Exception:
                match = re.search(r"\{[\s\S]*\}", text)
                if not match:
                    return None
                try:
                    payload = json.loads(match.group(0))
                except Exception:
                    return None
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
                    "general",
                ],
            },
            ensure_ascii=False,
        )

        def _invoke() -> dict[str, Any] | None:
            model = llm_factory.create_for_role("extensions_prefilter", streaming=False, temperature=0)
            response = model.invoke(
                [
                    SystemMessage(
                        content=(
                            "你是 V8 Agent OS 的 skill 能力画像归一器。\n"
                            "任务：根据 skill 的名称、描述、结构与正文，输出稳定的内部能力画像。\n"
                            "只返回 JSON："
                            "{\"skillClass\":\"...\",\"primaryArtifactTypes\":[...],\"primaryOperations\":[...],"
                            "\"interactionMode\":\"...\",\"capabilityConfidence\":0.0}\n"
                            "要求：skillClass 只能选一个；primaryArtifactTypes 最多 2 项；"
                            "primaryOperations 最多 3 项；如果不是文件/媒体产物型 skill，primaryArtifactTypes 可以为空；不要输出解释。"
                        )
                    ),
                    HumanMessage(content=prompt_payload),
                ],
                config={"callbacks": []},
            )
            content = getattr(response, "content", response)
            text = content if isinstance(content, str) else str(content or "")
            try:
                payload = json.loads(text)
            except Exception:
                match = re.search(r"\{[\s\S]*\}", text)
                if not match:
                    return None
                try:
                    payload = json.loads(match.group(0))
                except Exception:
                    return None
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
    def _now_iso(cls) -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _cache_file(cls) -> Path:
        return V8_AGENT_OS_HOME / "skills_inventory_cache.json"

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
                manifest[key] = {
                    "key": key,
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
        return cls._normalize_path(entry.get("instructionPath"))

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
            normalized = cls._normalize_cached_item(item)
            if normalized is None:
                return False
            registry[str(normalized.get("skillId"))] = normalized
        if not registry:
            return False

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
        return True

    @classmethod
    def _ensure_seeded_global_skills(cls, global_agents_path: Path) -> None:
        repo_skill_root = cls._resolve_repo_root() / ".agents" / "skills"
        if not repo_skill_root.exists():
            return

        for skill_name in ("code-reviewer",):
            source_dir = repo_skill_root / skill_name
            target_dir = global_agents_path / skill_name
            if not source_dir.exists() or target_dir.exists():
                continue
            try:
                shutil.copytree(source_dir, target_dir)
                print(f"[SkillLoader] Seeded workspace skill '{skill_name}' into {target_dir}")
            except Exception as exc:
                print(f"[SkillLoader] Failed to seed skill '{skill_name}': {exc}")

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
        main_descriptor = cls._main_workspace_root_descriptor()
        if main_descriptor is not None:
            descriptors.append(main_descriptor)
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
        return cls._dedupe_root_descriptors(descriptors)

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
    ) -> dict[str, Any]:
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
            "rootDescriptors": list(descriptors),
            "roots": [str(item.get("rootPath") or "") for item in descriptors],
            "fingerprint": fingerprint,
            "revision": cls._skills_revision or fingerprint,
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
        return {
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
            "sourceType": source_type,
            "visibility": str(descriptor.get("visibility") or "global").strip() or "global",
            "workspacePath": cls._normalize_path(descriptor.get("workspacePath")),
            "workspaceId": str(descriptor.get("workspaceId") or "").strip() or None,
            "projectId": str(descriptor.get("projectId") or "").strip() or None,
            "rootPath": cls._normalize_path(descriptor.get("rootPath") or normalized_skill_root),
        }

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
                registry[str(entry.get("skillId"))] = entry
                print(
                    f"[SkillLoader] Successfully loaded Skill: {entry.get('skillName')} "
                    f"({entry.get('sourceType')})"
                )
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
        descriptors = cls._resolve_root_descriptors(include_scoped=False)
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
        ] or cls._resolve_root_descriptors(include_scoped=False)
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
    ) -> dict[str, Any]:
        if force_refresh:
            cls.ensure_fresh()
        elif not cls._skills_registry:
            cls.prime_startup_cache()
            if not cls._skills_registry:
                cls.ensure_fresh()

        base_descriptors = cls._skills_root_descriptors or cls._resolve_root_descriptors(include_scoped=False)
        base_registry = dict(cls._skills_registry)
        base_fingerprint = cls._skills_fingerprint or cls._compute_fingerprint(base_descriptors)
        if not include_scoped:
            return cls._inventory_snapshot(
                registry=base_registry,
                descriptors=base_descriptors,
                fingerprint=base_fingerprint,
            )

        visible_descriptors = cls._resolve_root_descriptors(
            include_scoped=True,
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        base_paths = {cls._normalize_path(item.get("rootPath")) for item in base_descriptors}
        scoped_descriptors = [
            item for item in visible_descriptors if cls._normalize_path(item.get("rootPath")) not in base_paths
        ]
        if not scoped_descriptors:
            return cls._inventory_snapshot(
                registry=base_registry,
                descriptors=visible_descriptors,
                fingerprint=base_fingerprint,
            )
        scoped_registry = cls._scan_root_descriptors(scoped_descriptors)
        merged_registry = dict(base_registry)
        merged_registry.update(scoped_registry)
        visible_fingerprint = cls._compute_fingerprint(visible_descriptors)
        return cls._inventory_snapshot(
            registry=merged_registry,
            descriptors=visible_descriptors,
            fingerprint=visible_fingerprint,
        )

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
    def _skill_registry_signature(cls, item: dict[str, Any]) -> str:
        instruction_path = Path(str(item.get("instructionPath") or item.get("path") or "").strip())
        parts = [
            str(item.get("skillId") or ""),
            str(item.get("name") or ""),
            str(item.get("description") or ""),
            str(instruction_path),
            str(item.get("sourceType") or ""),
            str(item.get("rootPath") or ""),
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
        started_at = time.perf_counter()
        descriptors = cls._resolve_root_descriptors(include_scoped=False)
        descriptors = cls._dedupe_root_descriptors(descriptors)
        manifest = cls._compute_manifest(descriptors)
        fingerprint = cls._manifest_fingerprint(descriptors, manifest)
        root_signature = cls._root_descriptors_signature(descriptors)
        if fingerprint == cls._skills_fingerprint and cls._skills_registry:
            cls._skills_root_descriptors = descriptors
            cls._skills_roots = [Path(item["rootPath"]) for item in descriptors]
            cls._last_check_at = time.monotonic()
            result = {
                "changed": False,
                "refreshMode": "delta",
                "fingerprint": fingerprint,
                "revision": cls._skills_revision or fingerprint,
                "roots": [str(item.get("rootPath") or "") for item in descriptors],
                "rootDescriptors": list(descriptors),
                "addedSkills": [],
                "removedSkills": [],
                "updatedSkills": [],
                "recentSkillDiscovery": [
                    {key: value for key, value in item.items() if key != "_observedTs"}
                    for item in cls._recent_skill_discovery
                ],
                "durationMs": round((time.perf_counter() - started_at) * 1000, 2),
            }
            cls._last_reload_result = dict(result)
            return result

        before = {
            skill_id: cls._skill_registry_signature(item)
            for skill_id, item in cls._skills_registry.items()
        }
        before_by_manifest_key = {
            cls._entry_manifest_key(item): (skill_id, item)
            for skill_id, item in cls._skills_registry.items()
            if cls._entry_manifest_key(item)
        }
        full_reload_required = (
            not cls._skills_registry
            or not cls._skills_manifest
            or not cls._skills_root_signature
            or root_signature != cls._skills_root_signature
        )
        refresh_mode = "full" if full_reload_required else "delta"
        rebuilt_updated_skill_ids: set[str] = set()
        if full_reload_required:
            cls.reload_skills(root_descriptors=descriptors, fingerprint=fingerprint)
        else:
            old_manifest = dict(cls._skills_manifest)
            added_keys = sorted(set(manifest) - set(old_manifest))
            removed_keys = sorted(set(old_manifest) - set(manifest))
            updated_keys = sorted(
                key
                for key in set(manifest).intersection(old_manifest)
                if (
                    int(manifest[key].get("mtimeNs") or 0) != int(old_manifest[key].get("mtimeNs") or 0)
                    or int(manifest[key].get("size") or 0) != int(old_manifest[key].get("size") or 0)
                )
            )
            next_registry = dict(cls._skills_registry)
            for key in removed_keys:
                old_entry = before_by_manifest_key.get(key)
                if old_entry:
                    next_registry.pop(old_entry[0], None)
            for key in [*added_keys, *updated_keys]:
                manifest_item = manifest.get(key)
                if not manifest_item:
                    continue
                skill_file = Path(str(manifest_item.get("instructionPath") or ""))
                try:
                    content = skill_file.read_text(encoding="utf-8")
                except Exception as exc:
                    print(f"[SkillLoader] Error reading {skill_file}: {exc}")
                    continue
                entry = cls._build_skill_entry(
                    folder_name=str(manifest_item.get("folder") or skill_file.parent.name),
                    file_path=skill_file,
                    descriptor=cls._manifest_item_descriptor(manifest_item),
                    content=content,
                )
                if entry is None:
                    old_entry = before_by_manifest_key.get(key)
                    if old_entry:
                        next_registry.pop(old_entry[0], None)
                    continue
                old_entry = before_by_manifest_key.get(key)
                if old_entry and old_entry[0] != str(entry.get("skillId") or ""):
                    next_registry.pop(old_entry[0], None)
                next_registry[str(entry.get("skillId"))] = entry
                if key in updated_keys and str(entry.get("skillId") or ""):
                    rebuilt_updated_skill_ids.add(str(entry.get("skillId")))
            cls._skills_registry = next_registry
            cls._skills_root_descriptors = descriptors
            cls._skills_roots = [Path(item["rootPath"]) for item in descriptors]
            cls._skills_manifest = manifest
            cls._skills_root_signature = root_signature
            cls._skills_fingerprint = fingerprint
            cls._skills_revision = fingerprint
            cls._last_check_at = time.monotonic()
            cls._persist_cache()
            cls._startup_state = "ready"
            cls._snapshot_freshness = "live"
            cls._last_refresh_at = cls._now_iso()
            cls._last_refresh_error = None
        after = {
            skill_id: cls._skill_registry_signature(item)
            for skill_id, item in cls._skills_registry.items()
        }
        before_ids = set(before)
        after_ids = set(after)
        shared_ids = before_ids & after_ids
        added_skill_ids = sorted(after_ids - before_ids)
        signature_updated_skill_ids = {
            skill_id
            for skill_id in shared_ids
            if before.get(skill_id) != after.get(skill_id)
        }
        updated_skill_ids = sorted(signature_updated_skill_ids | (rebuilt_updated_skill_ids & shared_ids))
        recent = cls._remember_recent_skill_discovery(
            added=[cls._skills_registry[skill_id] for skill_id in added_skill_ids if skill_id in cls._skills_registry],
            updated=[cls._skills_registry[skill_id] for skill_id in updated_skill_ids if skill_id in cls._skills_registry],
            refresh_mode=refresh_mode,
        )
        if added_skill_ids or updated_skill_ids:
            cls._persist_cache()
        result = {
            "changed": True,
            "refreshMode": refresh_mode,
            "fingerprint": fingerprint,
            "revision": cls._skills_revision or fingerprint,
            "roots": [str(item.get("rootPath") or "") for item in descriptors],
            "rootDescriptors": list(descriptors),
            "addedSkills": added_skill_ids,
            "removedSkills": sorted(before_ids - after_ids),
            "updatedSkills": updated_skill_ids,
            "recentSkillDiscovery": recent,
            "durationMs": round((time.perf_counter() - started_at) * 1000, 2),
        }
        cls._last_reload_result = dict(result)
        return result

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
        ] or cls._resolve_root_descriptors(include_scoped=False)
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
        descriptors = cls._skills_root_descriptors or cls._resolve_root_descriptors(include_scoped=False)
        roots = [str(item.get("rootPath") or "") for item in descriptors]
        return {
            "startupState": cls._startup_state,
            "snapshotFreshness": cls._snapshot_freshness,
            "lastRefreshAt": cls._last_refresh_at,
            "lastRefreshError": cls._last_refresh_error,
            "skillCount": len(cls._skills_registry),
            "fingerprint": cls._skills_fingerprint,
            "revision": cls._skills_revision or cls._skills_fingerprint,
            "rootSignature": cls._skills_root_signature,
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
        inventory = cls.get_inventory(force_refresh=True, include_scoped=False)
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


@tool
def fetch_skill_instructions(skill_name: str) -> str:
    """Fetches the detailed markdown workflow instructions for a specific given skill name.
    Use this tool whenever you want to learn HOW to perform a specific workflow that is listed in your Available Custom Skills.
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
        roots = "\n".join(f"- {item}" for item in list(status.get("roots") or [])[:8]) or "- (no visible skill roots)"
        recent_items = list(status.get("recentSkillDiscovery") or [])[:8]
        recent = "\n".join(
            f"- {item.get('skillName') or item.get('skillId')} | {item.get('reason')} | {item.get('skillRoot')}"
            for item in recent_items
        ) or "- (no recent skill discovery)"
        return (
            f"Error: The requested skill '{skill_name}' was not found in the registry after a freshness check.\n"
            f"Skill inventory revision: {status.get('revision') or status.get('fingerprint') or 'unknown'}\n"
            f"Visible skill roots:\n{roots}\n"
            f"Recent skill discovery:\n{recent}\n"
            "If the skill was just installed, confirm that its SKILL.md lives directly under one of the visible skill roots."
        )

    skill = matches[0]
    scan_payload: dict[str, Any] | None = None
    review_payload: dict[str, Any] | None = None
    try:
        from core.audit_logger import audit_logger
        from erc.safety_guardian import safety_guardian

        scan_payload = safety_guardian.assess_skill_directory(
            skill_name=skill.get("name") or skill_name,
            skill_root=skill.get("path") or "",
            instruction_path=skill.get("instructionPath") or "",
        )
        static_verdict = str(scan_payload.get("verdict") or "").strip().lower()
        if static_verdict == "review" and bool(scan_payload.get("llmReviewRecommended")):
            review_payload = safety_guardian.review_skill_scan_with_llm(
                skill_name=skill.get("name") or skill_name,
                skill_root=skill.get("path") or "",
                scan_payload=scan_payload,
            )
            if review_payload:
                scan_payload["llmReview"] = review_payload
                scan_payload["reviewMode"] = "llm_assisted"
                if review_payload.get("status") == "completed":
                    review_summary = str(review_payload.get("summary") or "").strip()
                    if review_payload.get("decision") == "allow":
                        scan_payload["staticVerdict"] = static_verdict
                        scan_payload["verdict"] = "audit"
                        reasons = list(scan_payload.get("reasons") or [])
                        reasons.append(
                            f"安全复审模型认为该 skill 可放行：{review_summary or '证据不足以支持阻断。'}"
                        )
                        scan_payload["reasons"] = reasons[:10]
                    elif review_payload.get("decision") == "block":
                        scan_payload["staticVerdict"] = static_verdict
                        scan_payload["verdict"] = "block"
                        reasons = list(scan_payload.get("reasons") or [])
                        reasons.append(
                            f"安全复审模型维持阻断：{review_summary or '疑点仍然足够高风险。'}"
                        )
                        scan_payload["reasons"] = reasons[:10]
                else:
                    scan_payload["reviewMode"] = "rules_only_fallback"
        else:
            scan_payload["reviewMode"] = "rules_only"
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
    structure = "\n".join(f"- {item}" for item in available_files[:64]) if available_files else "- (no extra references/scripts/assets/templates/examples found)"
    return (
        f"{safety_banner}"
        f"=== SKILL ENTRYPOINTS ===\n"
        f"Skill ID: {skill.get('skillId') or ''}\n"
        f"Skill Name: {skill.get('skillName') or skill.get('name') or skill_name}\n"
        f"Source Type: {skill.get('sourceType') or ''}\n"
        f"Visibility: {skill.get('visibility') or ''}\n"
        f"Workspace Path: {skill.get('workspacePath') or ''}\n"
        f"Workspace ID: {skill.get('workspaceId') or ''}\n"
        f"Project ID: {skill.get('projectId') or ''}\n"
        f"Skill Root: {skill.get('skillRoot') or skill.get('path') or ''}\n"
        f"Instruction Path: {skill.get('instructionPath') or ''}\n"
        f"References Dir: {skill.get('referencesDir') or ''}\n"
        f"Scripts Dir: {skill.get('scriptsDir') or ''}\n"
        f"Assets Dir: {skill.get('assetsDir') or ''}\n"
        f"Templates Dir: {skill.get('templatesDir') or ''}\n"
        f"Examples Dir: {skill.get('examplesDir') or ''}\n"
        f"Directory Structure:\n{structure}\n\n"
        f"按当前 skill 的要求去做。\n\n"
        f"=== INSTRUCTIONS ===\n{skill['instructions']}"
    )
