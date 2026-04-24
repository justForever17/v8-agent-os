from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://skills.sh"
TARGET_SKILL_COUNT = 1000
LEADERBOARD_SPECS = (
    ("/", "all-time"),
    ("/trending", "trending"),
    ("/hot", "hot"),
)
USER_AGENT = "V8-Agent-OS-LexiconBuilder/1.0 (+https://skills.sh)"
REQUEST_TIMEOUT_SECONDS = 30
MAX_WORKERS = 8
MAX_EN_QUERY_SYNONYMS = 420
MAX_ZH_QUERY_SYNONYMS = 260
MAX_THEME_PHRASES_PER_BUCKET = 48
SKILL_TEXT_SAMPLE_LIMIT = 5000
DETAIL_EXAMPLE_LIMIT = 5
OUTPUT_DIR = Path(__file__).resolve().parent

_ARRAY_RE = re.compile(
    r'initialSkills\\":(\[.*?\]),\\\"totalSkills\\\":(\d+).*?\\\"view\\\":\\\"([^\\]+)\\\"',
    re.S,
)
_EN_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.+-]{1,}")
_SENTENCE_SPLIT_RE = re.compile(r"[.\n;:|•]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "api",
    "app",
    "apps",
    "assistant",
    "best",
    "build",
    "built",
    "by",
    "capabilities",
    "capability",
    "cli",
    "complete",
    "comprehensive",
    "create",
    "creation",
    "custom",
    "efficient",
    "enhanced",
    "for",
    "from",
    "generate",
    "generation",
    "generator",
    "guide",
    "helps",
    "how",
    "in",
    "includes",
    "install",
    "installation",
    "instructions",
    "kit",
    "language",
    "local",
    "modern",
    "of",
    "on",
    "or",
    "powerful",
    "quality",
    "skill",
    "skills",
    "solution",
    "support",
    "supports",
    "system",
    "template",
    "the",
    "this",
    "tool",
    "tools",
    "use",
    "used",
    "using",
    "via",
    "with",
    "workflow",
}
_BLOCKED_SUBSTRINGS = (
    "api key",
    "authentication",
    "belt login",
    "copy to clipboard",
    "github stars",
    "install instructions",
    "npx skills",
    "originally from",
    "repository",
    "security audits",
    "socket warn",
    "weekly installs",
)
_KEEP_SINGLE_TOKEN_PHRASES = {
    "airtable",
    "analytics",
    "backtest",
    "benchmark",
    "compliance",
    "diagnostics",
    "docker",
    "docs",
    "excel",
    "figma",
    "firebase",
    "flutter",
    "framer",
    "grok",
    "invoice",
    "jest",
    "kubernetes",
    "medical",
    "notion",
    "playwright",
    "podcast",
    "powerpoint",
    "prompt",
    "seo",
    "slides",
    "spreadsheet",
    "supabase",
    "translation",
    "typescript",
    "vercel",
    "video",
    "vue",
    "wechat",
    "youtube",
}
_THEME_KEYWORDS = {
    "content_media": {
        "blog",
        "content",
        "copywriting",
        "creator",
        "newsletter",
        "podcast",
        "publishing",
        "seo",
        "social",
        "thumbnail",
        "video",
        "wechat",
        "youtube",
    },
    "writing_communication": {
        "blog",
        "copywriting",
        "documentation",
        "email",
        "memo",
        "newsletter",
        "proposal",
        "report",
        "spec",
        "writing",
    },
    "engineering_ai": {
        "agent",
        "api",
        "automation",
        "backend",
        "code",
        "debug",
        "deploy",
        "deployment",
        "frontend",
        "integration",
        "llm",
        "migration",
        "playwright",
        "react",
        "testing",
        "typescript",
    },
    "startup_growth": {
        "acquisition",
        "conversion",
        "growth",
        "gtm",
        "landing",
        "marketing",
        "monetization",
        "onboarding",
        "retention",
        "seo",
    },
    "product_strategy": {
        "benchmark",
        "blueprint",
        "design",
        "pricing",
        "roadmap",
        "specification",
        "strategy",
        "ux",
    },
    "finance_research": {
        "backtest",
        "equity",
        "finance",
        "financial",
        "forecast",
        "investment",
        "macro",
        "portfolio",
        "research",
        "stock",
        "trading",
        "valuation",
    },
    "healthcare_medical": {
        "clinical",
        "diagnosis",
        "health",
        "healthcare",
        "medical",
        "medication",
        "patient",
        "symptom",
        "therapy",
    },
    "culture_humanities": {
        "art",
        "book",
        "culture",
        "film",
        "history",
        "humanities",
        "literature",
        "philosophy",
        "poetry",
        "review",
    },
    "career_learning": {
        "career",
        "education",
        "learning",
        "mentor",
        "resume",
        "teaching",
        "training",
    },
    "organization_leadership": {
        "compliance",
        "leadership",
        "management",
        "manager",
        "operations",
        "policy",
        "team",
    },
}
_SECONDARY_THEME_KEYWORDS = {
    "social_publishing": {"newsletter", "social", "wechat", "youtube"},
    "creator_growth": {"creator", "retention", "seo", "thumbnail", "youtube"},
    "financial_analysis": {"backtest", "financial", "macro", "stock", "trading", "valuation"},
    "medical_review": {"diagnosis", "healthcare", "medical", "medication", "symptom"},
    "cultural_analysis": {"art", "book", "film", "history", "literature", "poetry"},
    "organizational_design": {"compliance", "leadership", "management", "operations", "policy"},
    "attention_arbitrage": {"marketing", "seo", "social", "thumbnail"},
}
_SECONDARY_THEME_PRIMARY_MAP = {
    "social_publishing": ["content_media", "writing_communication"],
    "creator_growth": ["content_media", "startup_growth"],
    "financial_analysis": ["finance_research"],
    "medical_review": ["healthcare_medical"],
    "cultural_analysis": ["culture_humanities", "writing_communication"],
    "organizational_design": ["organization_leadership"],
    "attention_arbitrage": ["content_media", "startup_growth"],
}
_PHRASE_TRANSLATIONS_ZH = {
    "api integration": ["接口集成"],
    "avatar video": ["数字人视频", "虚拟人视频"],
    "book review": ["书评"],
    "bug triage": ["缺陷分诊"],
    "code review": ["代码审查"],
    "customer support": ["客服支持"],
    "data extraction": ["数据提取"],
    "database migration": ["数据库迁移"],
    "design system": ["设计系统"],
    "documentation workflow": ["文档工作流"],
    "email outreach": ["邮件触达"],
    "financial analysis": ["金融分析"],
    "financial report": ["财报"],
    "film review": ["影评"],
    "frontend ui": ["前端界面"],
    "healthcare report": ["医疗报告"],
    "image to video": ["图生视频"],
    "landing page": ["落地页"],
    "meeting notes": ["会议纪要"],
    "medical report": ["检查报告", "医疗报告"],
    "official account": ["公众号"],
    "official account article": ["公众号文章"],
    "official account articles": ["公众号文章"],
    "podcast editing": ["播客剪辑"],
    "presentation deck": ["演示文稿", "演示稿"],
    "pricing strategy": ["定价策略"],
    "product roadmap": ["产品路线图"],
    "public account": ["公众号"],
    "public account article": ["公众号文章"],
    "public account articles": ["公众号文章"],
    "research report": ["研报", "调研报告"],
    "resume review": ["简历审查"],
    "screen recording": ["录屏"],
    "short video": ["短视频"],
    "slide deck": ["幻灯片", "演示文稿"],
    "social publishing": ["社媒发布"],
    "technical blog": ["技术文章"],
    "technical writing": ["技术写作"],
    "text to video": ["文生视频"],
    "thumbnail design": ["缩略图设计", "封面图设计"],
    "unit testing": ["单元测试"],
    "video editing": ["视频剪辑"],
    "wechat": ["微信"],
    "wechat article": ["微信文章", "公众号文章"],
    "wechat official account": ["微信公众号"],
    "wechat official account article": ["微信公众号文章", "公众号文章"],
    "wechat official account articles": ["微信公众号文章", "公众号文章"],
    "wechat official account writing": ["微信公众号写作", "公众号写作"],
    "youtube thumbnail": ["YouTube缩略图", "视频封面图"],
}
_TOKEN_TRANSLATIONS_ZH = {
    "agent": "代理",
    "analysis": "分析",
    "api": "接口",
    "article": "文章",
    "audio": "音频",
    "automation": "自动化",
    "avatar": "数字人",
    "blog": "博客",
    "book": "书籍",
    "career": "职业",
    "code": "代码",
    "compliance": "合规",
    "content": "内容",
    "copywriting": "文案",
    "customer": "客户",
    "data": "数据",
    "database": "数据库",
    "debug": "调试",
    "design": "设计",
    "document": "文档",
    "documentation": "文档",
    "editing": "剪辑",
    "email": "邮件",
    "finance": "金融",
    "financial": "财务",
    "film": "电影",
    "frontend": "前端",
    "generation": "生成",
    "growth": "增长",
    "healthcare": "医疗",
    "image": "图片",
    "integration": "集成",
    "investment": "投资",
    "invoice": "发票",
    "landing": "落地",
    "literature": "文学",
    "management": "管理",
    "marketing": "营销",
    "medical": "医疗",
    "meeting": "会议",
    "migration": "迁移",
    "newsletter": "简报",
    "notes": "纪要",
    "official": "官方",
    "operations": "运营",
    "page": "页面",
    "podcast": "播客",
    "policy": "策略",
    "portfolio": "组合",
    "presentation": "演示文稿",
    "pricing": "定价",
    "product": "产品",
    "proposal": "方案",
    "public": "公开",
    "publishing": "发布",
    "react": "React",
    "report": "报告",
    "research": "研究",
    "resume": "简历",
    "review": "审查",
    "roadmap": "路线图",
    "screen": "屏幕",
    "seo": "SEO",
    "short": "短",
    "slide": "幻灯片",
    "social": "社媒",
    "spec": "规范",
    "strategy": "策略",
    "support": "支持",
    "symptom": "症状",
    "system": "系统",
    "technical": "技术",
    "testing": "测试",
    "text": "文本",
    "thumbnail": "缩略图",
    "training": "培训",
    "translation": "翻译",
    "trading": "交易",
    "triage": "分诊",
    "unit": "单元",
    "valuation": "估值",
    "video": "视频",
    "voice": "语音",
    "wechat": "微信",
    "workflow": "工作流",
    "writing": "写作",
    "youtube": "YouTube",
}
_BRIDGE_EXTRA_TOKENS = {
    "official": "wechat",
    "public": "wechat",
    "thumbnail": "image",
    "newsletter": "document",
    "podcast": "audio",
    "pricing": "analysis",
    "resume": "document",
    "valuation": "finance",
    "wechat": "article",
    "youtube": "video",
}


@dataclass(slots=True)
class SkillEntry:
    key: str
    source: str
    skill_id: str
    name: str
    detail_url: str
    score: float
    views: dict[str, dict[str, int | None]]
    summary_text: str = ""
    skill_text: str = ""


def _http_get(url: str) -> str:
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.text


def _fetch_leaderboard(path: str, expected_view: str) -> list[dict[str, Any]]:
    html = _http_get(f"{BASE_URL}{path}")
    match = _ARRAY_RE.search(html)
    if not match:
        raise RuntimeError(f"无法从 {path} 提取 skills.sh leaderboard 数据")
    payload = json.loads(match.group(1).encode("utf-8").decode("unicode_escape"))
    actual_view = str(match.group(3)).strip()
    if actual_view != expected_view:
        raise RuntimeError(f"{path} 返回视图 {actual_view}，与预期 {expected_view} 不一致")
    if not isinstance(payload, list):
        raise RuntimeError(f"{path} leaderboard payload 不是数组")
    return payload


def _leaderboard_score(views: dict[str, dict[str, int | None]]) -> float:
    score = 0.0
    if "all-time" in views:
        rank = int(views["all-time"].get("rank") or 999999)
        installs = int(views["all-time"].get("installs") or 0)
        score += 2_000_000 - (rank * 2_000) + min(installs, 2_000_000) * 0.20
    if "trending" in views:
        rank = int(views["trending"].get("rank") or 999999)
        installs = int(views["trending"].get("installs") or 0)
        score += 350_000 - (rank * 350) + min(installs, 50_000) * 1.5
    if "hot" in views:
        rank = int(views["hot"].get("rank") or 999999)
        installs = int(views["hot"].get("installs") or 0)
        change = int(views["hot"].get("change") or 0)
        score += 180_000 - (rank * 260) + min(installs, 2_000) * 25 + min(change, 500) * 40
    score += len(views) * 10_000
    return round(score, 3)


def _select_top_skills() -> tuple[list[SkillEntry], dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    leaderboard_counts: dict[str, int] = {}
    for path, view in LEADERBOARD_SPECS:
        items = _fetch_leaderboard(path, view)
        leaderboard_counts[view] = len(items)
        for rank, item in enumerate(items, start=1):
            source = str(item.get("source") or "").strip()
            skill_id = str(item.get("skillId") or "").strip()
            name = str(item.get("name") or skill_id).strip()
            if not source or not skill_id or not name:
                continue
            key = f"{source}::{skill_id}"
            bucket = merged.setdefault(
                key,
                {
                    "key": key,
                    "source": source,
                    "skillId": skill_id,
                    "name": name,
                    "detailUrl": f"{BASE_URL}/{source}/{skill_id}",
                    "views": {},
                },
            )
            bucket["views"][view] = {
                "rank": rank,
                "installs": int(item.get("installs") or 0),
                "change": int(item.get("change") or 0),
            }

    ordered = sorted(
        (
            SkillEntry(
                key=payload["key"],
                source=payload["source"],
                skill_id=payload["skillId"],
                name=payload["name"],
                detail_url=payload["detailUrl"],
                score=_leaderboard_score(payload["views"]),
                views=payload["views"],
            )
            for payload in merged.values()
        ),
        key=lambda item: (
            -item.score,
            int(item.views.get("all-time", {}).get("rank") or 999999),
            int(item.views.get("trending", {}).get("rank") or 999999),
            int(item.views.get("hot", {}).get("rank") or 999999),
            item.name.lower(),
        ),
    )
    selected = ordered[:TARGET_SKILL_COUNT]
    metadata = {
        "leaderboardCounts": leaderboard_counts,
        "leaderboardUniqueCount": len(ordered),
        "selectedCount": len(selected),
        "selectionStrategy": "skills.sh public leaderboard union: all-time(600) + trending(600) + hot(600) => weighted unique top1000",
    }
    return selected, metadata


def _normalize_space(text: str) -> str:
    return " ".join(str(text or "").split())


def _extract_skill_sections(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    summary_text = ""
    skill_text = ""

    summary_label = soup.find(lambda tag: tag.name in {"div", "span"} and tag.get_text(" ", strip=True) == "Summary")
    if summary_label is not None:
        summary_container = summary_label.find_next(
            lambda tag: tag.name == "div" and "rounded-lg" in " ".join(tag.get("class") or [])
        )
        if summary_container is not None:
            summary_text = _normalize_space(summary_container.get_text(" ", strip=True))

    skill_label = soup.find(lambda tag: tag.name in {"div", "span"} and tag.get_text(" ", strip=True) == "SKILL.md")
    if skill_label is not None:
        skill_container = skill_label.parent.find_next_sibling()
        if skill_container is None:
            skill_container = skill_label.find_next(
                lambda tag: tag.name == "div" and "prose" in " ".join(tag.get("class") or [])
            )
        if skill_container is not None:
            skill_text = _normalize_space(skill_container.get_text(" ", strip=True))[:SKILL_TEXT_SAMPLE_LIMIT]
    return summary_text, skill_text


def _fetch_skill_details(entry: SkillEntry) -> SkillEntry:
    html = _http_get(entry.detail_url)
    summary_text, skill_text = _extract_skill_sections(html)
    entry.summary_text = summary_text
    entry.skill_text = skill_text
    return entry


def _tokenize(text: str) -> list[str]:
    return [token for token in _EN_TOKEN_RE.findall(str(text or "").lower()) if token]


def _normalize_phrase(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = normalized.replace("/", " ").replace("_", " ").replace("-", " ")
    normalized = re.sub(r"[^a-z0-9.+\s]", " ", normalized)
    return " ".join(normalized.split())


def _singularize_token(token: str) -> str:
    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 4:
        return token[:-1]
    return token


def _translation_lookup_variants(phrase: str) -> list[str]:
    normalized = _normalize_phrase(phrase)
    if not normalized:
        return []
    variants = [normalized]
    tokens = _tokenize(normalized)
    if tokens:
        singularized = " ".join(_singularize_token(token) for token in tokens)
        if singularized and singularized not in variants:
            variants.append(singularized)
    return variants


def _english_phrase_aliases(phrase: str) -> list[str]:
    aliases: list[str] = []
    for candidate in _translation_lookup_variants(phrase):
        normalized_candidate = _normalize_phrase(candidate)
        if normalized_candidate and normalized_candidate not in aliases:
            aliases.append(normalized_candidate)
    return aliases


def _looks_like_noise(phrase: str) -> bool:
    if not phrase:
        return True
    if any(part in phrase for part in _BLOCKED_SUBSTRINGS):
        return True
    if "github.com" in phrase or "https://" in phrase or "http://" in phrase:
        return True
    if phrase.startswith("requires ") or phrase.startswith("install "):
        return True
    return False


def _is_valid_phrase(phrase: str) -> bool:
    phrase = _normalize_phrase(phrase)
    if _looks_like_noise(phrase):
        return False
    tokens = _tokenize(phrase)
    if not tokens:
        return False
    if len(tokens) == 1:
        return tokens[0] in _KEEP_SINGLE_TOKEN_PHRASES
    if len(tokens) > 5:
        return False
    meaningful_tokens = [token for token in tokens if token not in _STOPWORDS and len(token) >= 3]
    return bool(meaningful_tokens)


def _sentence_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(str(text or "")):
        normalized_sentence = _normalize_phrase(sentence)
        if not normalized_sentence:
            continue
        if _is_valid_phrase(normalized_sentence):
            candidates.append(normalized_sentence)
        tokens = [token for token in _tokenize(normalized_sentence) if token not in _STOPWORDS]
        max_n = min(4, len(tokens))
        for n in range(2, max_n + 1):
            for index in range(0, len(tokens) - n + 1):
                phrase = " ".join(tokens[index : index + n])
                if _is_valid_phrase(phrase):
                    candidates.append(phrase)
    return candidates


def _skill_name_phrase(name: str) -> str | None:
    phrase = _normalize_phrase(name)
    if _is_valid_phrase(phrase):
        return phrase
    return None


def _extract_skill_phrases(entry: SkillEntry) -> list[tuple[str, float, str]]:
    phrases: list[tuple[str, float, str]] = []
    name_phrase = _skill_name_phrase(entry.name)
    if name_phrase:
        phrases.append((name_phrase, entry.score * 1.6, "skill_name"))

    seen_sentences: set[str] = set()
    for source_name, text, weight in (
        ("summary", entry.summary_text, 1.0),
        ("skill_text", entry.skill_text[:1600], 0.55),
    ):
        for phrase in _sentence_candidates(text):
            if phrase in seen_sentences:
                continue
            seen_sentences.add(phrase)
            phrases.append((phrase, entry.score * weight, source_name))
    return phrases


def _phrase_bridge_tokens(phrase: str, entry: SkillEntry) -> list[str]:
    base_tokens = [token for token in _tokenize(phrase) if token not in _STOPWORDS]
    corpus_tokens = [
        token
        for token in _tokenize(f"{entry.summary_text} {entry.skill_text[:1200]}")
        if token not in _STOPWORDS
    ]
    token_counts = Counter(corpus_tokens)
    bridge_tokens = list(base_tokens)
    for token in list(base_tokens):
        extra = _BRIDGE_EXTRA_TOKENS.get(token)
        if extra and extra not in bridge_tokens:
            bridge_tokens.append(extra)
    for token, _count in token_counts.most_common(12):
        if token in bridge_tokens or token in _STOPWORDS or len(token) < 4:
            continue
        bridge_tokens.append(token)
        if len(bridge_tokens) >= 7:
            break
    return bridge_tokens[:7]


def _translate_phrase_to_zh(phrase: str) -> list[str]:
    for variant in _translation_lookup_variants(phrase):
        if variant in _PHRASE_TRANSLATIONS_ZH:
            return list(dict.fromkeys(_PHRASE_TRANSLATIONS_ZH[variant]))
    normalized = _normalize_phrase(phrase)
    tokens = _tokenize(normalized)
    translated_tokens: list[str] = []
    for token in tokens:
        translated = _TOKEN_TRANSLATIONS_ZH.get(_singularize_token(token))
        if not translated:
            return []
        translated_tokens.append(translated)
    if not translated_tokens:
        return []
    joined = "".join(translated_tokens)
    return [joined] if joined else []


def _match_themes(phrase: str) -> tuple[list[str], list[str]]:
    tokens = set(_tokenize(phrase))
    primary = [
        key
        for key, keywords in _THEME_KEYWORDS.items()
        if tokens.intersection(keywords)
    ]
    secondary = [
        key
        for key, keywords in _SECONDARY_THEME_KEYWORDS.items()
        if tokens.intersection(keywords)
    ]
    for key in list(secondary):
        for primary_theme in _SECONDARY_THEME_PRIMARY_MAP.get(key, []):
            if primary_theme not in primary:
                primary.append(primary_theme)
    return primary, secondary


def _build_market_lexicons(selected: list[SkillEntry]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    phrase_stats: dict[str, dict[str, Any]] = {}
    concept_examples: defaultdict[str, list[str]] = defaultdict(list)

    for entry in selected:
        for phrase, raw_score, source_name in _extract_skill_phrases(entry):
            normalized_phrase = _normalize_phrase(phrase)
            if not _is_valid_phrase(normalized_phrase):
                continue
            bucket = phrase_stats.setdefault(
                normalized_phrase,
                {
                    "score": 0.0,
                    "skillCount": 0,
                    "skills": set(),
                    "sourceKinds": set(),
                    "bridgeTokenCounts": Counter(),
                    "primaryThemes": Counter(),
                    "secondaryThemes": Counter(),
                },
            )
            if entry.key not in bucket["skills"]:
                bucket["skills"].add(entry.key)
                bucket["skillCount"] += 1
            bucket["score"] += raw_score
            bucket["sourceKinds"].add(source_name)
            for token in _phrase_bridge_tokens(normalized_phrase, entry):
                bucket["bridgeTokenCounts"][token] += 1
            primary_themes, secondary_themes = _match_themes(normalized_phrase)
            for theme in primary_themes:
                bucket["primaryThemes"][theme] += 1
                if len(concept_examples[theme]) < DETAIL_EXAMPLE_LIMIT and entry.name not in concept_examples[theme]:
                    concept_examples[theme].append(entry.name)
            for theme in secondary_themes:
                bucket["secondaryThemes"][theme] += 1

    def _phrase_priority(item: tuple[str, dict[str, Any]]) -> tuple[float, int, str]:
        phrase, payload = item
        score = float(payload["score"])
        skill_count = int(payload["skillCount"])
        return (score + (skill_count * 50), skill_count, phrase)

    def _is_priority_translation_phrase(phrase: str) -> bool:
        return any(variant in _PHRASE_TRANSLATIONS_ZH for variant in _translation_lookup_variants(phrase))

    ordered_phrases = [
        (phrase, payload)
        for phrase, payload in sorted(phrase_stats.items(), key=_phrase_priority, reverse=True)
        if (
            int(payload["skillCount"]) >= 2
            or float(payload["score"]) >= 700_000
            or _is_priority_translation_phrase(phrase)
        )
    ]
    translation_priority_phrases = [
        (phrase, payload)
        for phrase, payload in ordered_phrases
        if _is_priority_translation_phrase(phrase)
    ]
    high_score_phrases = [
        (phrase, payload)
        for phrase, payload in ordered_phrases
        if not _is_priority_translation_phrase(phrase) and float(payload["score"]) >= 700_000
    ]
    general_phrases = [
        (phrase, payload)
        for phrase, payload in ordered_phrases
        if not _is_priority_translation_phrase(phrase) and float(payload["score"]) < 700_000
    ]

    core_en = json.loads((OUTPUT_DIR.parent.parent / "en.json").read_text(encoding="utf-8"))
    core_zh = json.loads((OUTPUT_DIR.parent.parent / "zh-CN.json").read_text(encoding="utf-8"))
    core_en_keys = {str(key).strip().lower() for key in dict(core_en.get("querySynonyms") or {}).keys()}
    core_zh_keys = {str(key).strip() for key in dict(core_zh.get("querySynonyms") or {}).keys()}

    en_query_synonyms: dict[str, list[str]] = {}
    zh_query_synonyms: dict[str, list[str]] = {}
    en_primary_themes: defaultdict[str, list[str]] = defaultdict(list)
    zh_primary_themes: defaultdict[str, list[str]] = defaultdict(list)
    en_secondary_themes: defaultdict[str, list[str]] = defaultdict(list)
    zh_secondary_themes: defaultdict[str, list[str]] = defaultdict(list)

    def _remaining_en_capacity() -> int:
        return max(0, MAX_EN_QUERY_SYNONYMS - len(en_query_synonyms))

    def _remaining_zh_capacity() -> int:
        return max(0, MAX_ZH_QUERY_SYNONYMS - len(zh_query_synonyms))

    def _record_phrase(phrase: str, payload: dict[str, Any]) -> None:
        bridge_tokens = [
            token
            for token, _count in payload["bridgeTokenCounts"].most_common(6)
            if token not in _STOPWORDS
        ]
        if len(bridge_tokens) < 2:
            return
        en_aliases = [
            alias
            for alias in _english_phrase_aliases(phrase)
            if alias and alias not in core_en_keys
        ]
        for alias in en_aliases:
            if _remaining_en_capacity() <= 0:
                break
            en_query_synonyms.setdefault(alias, bridge_tokens)
        zh_aliases = [alias for alias in _translate_phrase_to_zh(phrase) if alias and alias not in core_zh_keys]
        for alias in zh_aliases:
            if _remaining_zh_capacity() <= 0:
                break
            zh_query_synonyms.setdefault(alias, bridge_tokens)

        primary_themes = [theme for theme, _count in payload["primaryThemes"].most_common(2)]
        secondary_themes = [theme for theme, _count in payload["secondaryThemes"].most_common(2)]
        for theme in primary_themes:
            if len(en_primary_themes[theme]) < MAX_THEME_PHRASES_PER_BUCKET and phrase not in en_primary_themes[theme]:
                en_primary_themes[theme].append(phrase)
            for alias in zh_aliases:
                if len(zh_primary_themes[theme]) < MAX_THEME_PHRASES_PER_BUCKET and alias not in zh_primary_themes[theme]:
                    zh_primary_themes[theme].append(alias)
        for theme in secondary_themes:
            if len(en_secondary_themes[theme]) < MAX_THEME_PHRASES_PER_BUCKET and phrase not in en_secondary_themes[theme]:
                en_secondary_themes[theme].append(phrase)
            for alias in zh_aliases:
                if len(zh_secondary_themes[theme]) < MAX_THEME_PHRASES_PER_BUCKET and alias not in zh_secondary_themes[theme]:
                    zh_secondary_themes[theme].append(alias)

    for phrase, payload in translation_priority_phrases:
        _record_phrase(phrase, payload)
    for phrase, payload in high_score_phrases:
        _record_phrase(phrase, payload)
    for phrase, payload in general_phrases:
        if _remaining_en_capacity() <= 0 and _remaining_zh_capacity() <= 0:
            break
        _record_phrase(phrase, payload)

    base_meta = {
        "version": 1,
        "layer": "market",
        "provider": "skills-sh",
        "selectionTarget": TARGET_SKILL_COUNT,
        "selectionCount": len(selected),
    }

    en_payload = {
        "locale": "en",
        **base_meta,
        "querySynonyms": en_query_synonyms,
        "artifactIntentSynonyms": {},
        "operationIntentSynonyms": {},
        "primaryThemeSynonyms": dict(en_primary_themes),
        "secondaryThemeSynonyms": dict(en_secondary_themes),
        "secondaryThemePrimaryMap": dict(_SECONDARY_THEME_PRIMARY_MAP),
        "documentSubIntentSynonyms": {},
        "skillDocumentSubIntentSynonyms": {},
        "artifactProfileAnchors": {},
    }
    zh_payload = {
        "locale": "zh-CN",
        **base_meta,
        "querySynonyms": zh_query_synonyms,
        "artifactIntentSynonyms": {},
        "operationIntentSynonyms": {},
        "primaryThemeSynonyms": dict(zh_primary_themes),
        "secondaryThemeSynonyms": dict(zh_secondary_themes),
        "secondaryThemePrimaryMap": dict(_SECONDARY_THEME_PRIMARY_MAP),
        "documentSubIntentSynonyms": {},
        "skillDocumentSubIntentSynonyms": {},
        "artifactProfileAnchors": {},
    }
    manifest_payload = {
        "provider": "skills-sh",
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "locales": ["en", "zh-CN"],
        "selectionTarget": TARGET_SKILL_COUNT,
        "selectionCount": len(selected),
        "selectionStrategy": "skills.sh public leaderboard union with weak-weight market lexicon extraction",
        "leaderboardViews": [spec[1] for spec in LEADERBOARD_SPECS],
        "selectedSkills": [
            {
                "source": entry.source,
                "skillId": entry.skill_id,
                "name": entry.name,
                "score": round(entry.score, 3),
                "views": entry.views,
            }
            for entry in selected
        ],
        "themeExamples": {
            key: value[:DETAIL_EXAMPLE_LIMIT]
            for key, value in sorted(concept_examples.items())
        },
    }
    manifest_signature = hashlib.sha256(
        json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    manifest_payload["selectionSignature"] = f"skills-sh:{manifest_signature}"
    en_payload["sourceManifestSignature"] = manifest_payload["selectionSignature"]
    zh_payload["sourceManifestSignature"] = manifest_payload["selectionSignature"]
    return manifest_payload, en_payload, zh_payload


def build_market_lexicons() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    selected, selection_meta = _select_top_skills()
    enriched: list[SkillEntry] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="skills-sh-lexicon") as executor:
        future_map = {executor.submit(_fetch_skill_details, entry): entry for entry in selected}
        for future in as_completed(future_map):
            entry = future_map[future]
            try:
                enriched.append(future.result())
            except Exception:
                enriched.append(entry)
    enriched.sort(key=lambda item: (-item.score, item.name.lower()))
    manifest_payload, en_payload, zh_payload = _build_market_lexicons(enriched)
    manifest_payload.update(selection_meta)
    return manifest_payload, en_payload, zh_payload


def main() -> None:
    manifest_payload, en_payload, zh_payload = build_market_lexicons()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUTPUT_DIR / "skills-sh-top1000.en.json").write_text(
        json.dumps(en_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUTPUT_DIR / "skills-sh-top1000.zh-CN.json").write_text(
        json.dumps(zh_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "provider": manifest_payload.get("provider"),
                "selectionCount": manifest_payload.get("selectionCount"),
                "selectionSignature": manifest_payload.get("selectionSignature"),
                "enQuerySynonymCount": len(en_payload.get("querySynonyms") or {}),
                "zhQuerySynonymCount": len(zh_payload.get("querySynonyms") or {}),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
