from __future__ import annotations

import re
from typing import Any


_WORD_RE = re.compile(r"[a-z0-9_+.-]+", re.IGNORECASE)


def _lower_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _has_any(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term and term.lower() in text]


_CODE_MEDIA_FRAMEWORK_TERMS = (
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
)

_CODE_ACTION_TERMS = (
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
)

_MEDIA_OUTPUT_TERMS = (
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
)

_MEDIA_PROVIDER_TERMS = (
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
)


def classify_task_shape(
    user_query: str,
    *,
    planner_plan: dict[str, Any] | None = None,
    workspace_descriptor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a non-authoritative task shape hint for prompt routing.

    This classifier deliberately does not grant tools or reveal subagents.  It
    only tells the supervisor what shape the request most likely has.  The main
    rule is execution method before final output modality: "make a video with
    Remotion" is code work that produces media, not a Creative Media provider
    job.
    """

    text = _lower_text(user_query)
    plan_text = _lower_text(planner_plan)
    workspace_text = _lower_text(workspace_descriptor)
    combined = "\n".join(part for part in (text, plan_text, workspace_text) if part)

    code_frameworks = _has_any(combined, _CODE_MEDIA_FRAMEWORK_TERMS)
    code_actions = _has_any(combined, _CODE_ACTION_TERMS)
    media_outputs = _has_any(combined, _MEDIA_OUTPUT_TERMS)
    media_providers = _has_any(combined, _MEDIA_PROVIDER_TERMS)

    primary = "general_chat"
    secondary: list[str] = []
    confidence = 0.35
    reason = "no_strong_shape_signal"
    suggested_families: list[str] = []
    optional_grants: list[str] = []
    signals: list[str] = []

    if code_frameworks:
        primary = "project_coding"
        confidence = 0.86
        reason = "execution_method_code_framework_over_output_modality"
        suggested_families = ["engineering"]
        signals.extend(f"code_framework:{item}" for item in code_frameworks[:6])
        if media_outputs or media_providers:
            secondary.append("creative_media")
            suggested_families.append("creative_media")
            signals.extend(f"media_secondary:{item}" for item in (media_outputs + media_providers)[:6])
    elif media_providers:
        primary = "creative_media"
        confidence = 0.82
        reason = "provider_or_media_generation_intent"
        suggested_families = ["creative_media"]
        optional_grants = ["creative_media.core"]
        signals.extend(f"media_provider:{item}" for item in media_providers[:6])
        if code_actions:
            secondary.append("project_coding")
            suggested_families.append("engineering")
            signals.extend(f"code_secondary:{item}" for item in code_actions[:4])
    elif code_actions:
        primary = "project_coding"
        confidence = 0.72
        reason = "engineering_action_terms"
        suggested_families = ["engineering"]
        signals.extend(f"code_action:{item}" for item in code_actions[:6])
        if media_outputs:
            secondary.append("creative_media")
            signals.extend(f"media_output:{item}" for item in media_outputs[:4])
    elif media_outputs:
        primary = "creative_media"
        confidence = 0.62
        reason = "media_output_terms_without_code_method"
        suggested_families = ["creative_media"]
        optional_grants = ["creative_media.core"]
        signals.extend(f"media_output:{item}" for item in media_outputs[:6])

    if primary not in secondary:
        secondary = [item for item in secondary if item != primary]
    suggested_families = _unique_preserve_order(suggested_families)
    optional_grants = _unique_preserve_order(optional_grants)

    return {
        "primaryTaskShape": primary,
        "secondaryTaskShapes": secondary[:4],
        "confidence": round(confidence, 2),
        "reason": reason,
        "suggestedFamilies": suggested_families[:6],
        "optionalRuntimeGrants": optional_grants[:6],
        "signals": signals[:12],
        "policy": "hint_only_non_authoritative_no_reveal_no_grant",
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
    return (
        "<task_shape>\n"
        f"primary={primary}; secondary={secondary}; confidence={confidence_text}; reason={reason}\n"
        f"suggestedFamilies={families}; optionalRuntimeGrants={grants}\n"
        "policy=non-binding hint only; does not reveal subagents or grant runtime tools.\n"
        "</task_shape>\n"
    )


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
