from __future__ import annotations

import json
import re
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any


_ASCII_WORD_RE = re.compile(r"^[a-z0-9_+-]+$", re.IGNORECASE)


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
        "script",
        "repo",
        "repository",
        "workspace",
        "patch",
        "test",
        "debug",
        "实现",
        "写代码",
        "代码",
        "项目",
        "仓库",
        "组件",
        "脚本",
        "修复",
        "测试",
        "构建",
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
        "可灵",
        "即梦",
        "豆包",
        "百炼",
        "火山",
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


def classify_task_shape(
    user_query: str,
    *,
    planner_plan: dict[str, Any] | None = None,
    workspace_descriptor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a conservative, non-authoritative task shape hint.

    The classifier may recommend an auto reveal target, but it never grants
    runtime tools and never dispatches subagents.  The central rule is
    execution method before final output modality: "make a video with Remotion"
    is code work that produces media, not a Creative Media provider job.
    """

    text = _lower_text(user_query)
    plan_text = _lower_text(planner_plan)
    workspace_text = _lower_text(workspace_descriptor)
    combined = "\n".join(part for part in (text, plan_text, workspace_text) if part)
    term_sets = _task_shape_term_sets()

    code_frameworks = _find_terms(combined, term_sets["code_media_framework"])
    code_actions = _find_terms(combined, term_sets["code_action"])
    media_outputs = _find_terms(combined, term_sets["media_output"])
    media_providers = _find_terms(combined, term_sets["media_provider"])
    writing_actions = _find_terms(combined, term_sets["writing_action"])
    research_actions = _find_terms(combined, term_sets["research_action"])
    explicit_writing_actions = [
        item
        for item in writing_actions
        if item not in {"docs", "document", "documentation", "文档", "说明"}
    ]

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
    elif code_actions:
        primary = "project_coding"
        confidence = 0.74
        reason = "engineering_action_terms"
        family_scores["engineering"] = 0.74
        suggested_families = ["engineering"]
        signals.extend(f"code_action:{item}" for item in code_actions[:6])
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
    elif research_actions:
        primary = "research"
        confidence = 0.91
        reason = "research_or_current_source_terms"
        family_scores["research"] = 0.91
        suggested_families = ["research"]
        optional_grants = ["research.core"]
        signals.extend(f"research_action:{item}" for item in research_actions[:8])
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

    return {
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
        "lexiconSignature": _lexicon_signature(),
        "policy": "hint_only_conservative_auto_reveal_recommendation_no_grant",
    }


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
    return (
        "<task_shape>\n"
        f"primary={primary}; secondary={secondary}; confidence={confidence_text}; reason={reason}\n"
        f"suggestedFamilies={families}; optionalRuntimeGrants={grants}\n"
        f"topFamily={top_family}; scoreMargin={margin_text}; ambiguityFlags={ambiguity}\n"
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
    lexicon_maps = _load_extension_lexicon_maps()
    for key, values in list(terms.items()):
        if key in {"media_output"}:
            terms[key] = set(_expand_with_lexicon(values, lexicon_maps))
    return {key: tuple(sorted(values, key=lambda item: (len(item), item))) for key, values in terms.items()}


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
    if lowered.isascii() and _ASCII_WORD_RE.match(lowered):
        return bool(re.search(rf"(?<![a-z0-9_+-]){re.escape(lowered)}(?![a-z0-9_+-])", text, re.IGNORECASE))
    return lowered in text


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
