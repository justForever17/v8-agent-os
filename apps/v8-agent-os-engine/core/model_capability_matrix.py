from __future__ import annotations

from typing import Any, Mapping, Sequence

from langchain_core.messages import BaseMessage


CHAT_CAPABILITY_CLASSES = {
    "chat_general",
    "chat_tool_calling",
    "chat_reasoning",
    "vision_multimodal",
}


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _content_requires_multimodal(content: Any) -> bool:
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                item_type = str(item.get("type") or "").strip().lower()
                if item_type in {
                    "image",
                    "image_url",
                    "input_image",
                    "video",
                    "video_url",
                    "input_video",
                    "media",
                    "file",
                    "input_audio",
                    "audio",
                }:
                    return True
                if item.get("image_url") or item.get("video_url") or item.get("media_url"):
                    return True
            elif hasattr(item, "type"):
                item_type = str(getattr(item, "type", "") or "").strip().lower()
                if item_type in {"image", "image_url", "input_image", "video", "video_url", "input_video", "media", "audio", "input_audio"}:
                    return True
    return False


def messages_require_multimodal(messages: Sequence[Any]) -> bool:
    for message in list(messages or []):
        content = getattr(message, "content", None)
        if _content_requires_multimodal(content):
            return True
    return False


def build_effective_capability_matrix(
    *,
    capability_class: str,
    capabilities: Mapping[str, Any] | None,
    api_standard: str,
    runtime_ready: bool = True,
) -> dict[str, Any]:
    raw = dict(capabilities or {})
    normalized_class = str(capability_class or "").strip().lower()
    normalized_api = str(api_standard or "openai").strip().lower()
    chat_like = normalized_class in CHAT_CAPABILITY_CLASSES
    multimodal = normalized_class == "vision_multimodal" or _bool(raw.get("supportsMultimodal"), _bool(raw.get("vision"), False))
    reasoning = _bool(raw.get("supportsReasoning"), _bool(raw.get("reasoning"), normalized_class in {"chat_reasoning", "vision_multimodal"}))
    streaming = _bool(raw.get("supportsStreaming"), _bool(raw.get("streaming"), chat_like))
    tools = _bool(raw.get("supportsTools"), _bool(raw.get("toolCalling"), chat_like))
    structured = _bool(raw.get("supportsStructuredOutput"), _bool(raw.get("structuredOutput"), chat_like))
    native_tools = _bool(raw.get("supportsNativeTools"), tools)
    native_structured = _bool(raw.get("supportsNativeStructuredOutput"), structured)
    prompt_emulated_tools = _bool(raw.get("supportsPromptEmulatedTools"), chat_like)
    prompt_fallback_structured = _bool(raw.get("supportsPromptFallbackStructuredOutput"), chat_like)
    reasoning_blocks = _bool(raw.get("supportsReasoningBlocks"), reasoning)

    matrix = {
        "capabilityClass": normalized_class or "chat_general",
        "apiStandard": normalized_api or "openai",
        "supports_streaming": streaming,
        "supports_multimodal": multimodal,
        "supports_reasoning_blocks": reasoning_blocks,
        "supports_native_tools": bool(tools and native_tools),
        "supports_prompt_emulated_tools": bool(chat_like and prompt_emulated_tools),
        "supports_native_structured_output": bool(structured and native_structured),
        "supports_prompt_fallback_structured_output": bool(chat_like and prompt_fallback_structured),
        "runtime_ready": bool(runtime_ready),
    }
    if not runtime_ready:
        matrix.update(
            {
                "supports_streaming": False,
                "supports_multimodal": False,
                "supports_reasoning_blocks": False,
                "supports_native_tools": False,
                "supports_prompt_emulated_tools": False,
                "supports_native_structured_output": False,
                "supports_prompt_fallback_structured_output": False,
            }
        )
    return matrix


def infer_runtime_capability_requirements(
    *,
    role: str,
    messages: Sequence[Any],
    tools: Sequence[Any] | None,
    preferred_matrix: Mapping[str, Any] | None,
    require_structured_output: bool = False,
) -> dict[str, Any]:
    normalized_role = str(role or "").strip().lower()
    preferred = dict(preferred_matrix or {})
    requires_tools = bool(list(tools or []))
    requires_multimodal = normalized_role.startswith("vision") or normalized_role.startswith("computer_use_visual_judge") or messages_require_multimodal(messages)
    requires_streaming = _bool(preferred.get("supports_streaming"), normalized_role not in {"connection_test"})
    requires_reasoning = _bool(
        preferred.get("supports_reasoning_blocks"),
        normalized_role.endswith("reasoning")
        or normalized_role.startswith("reviewer:")
        or normalized_role.startswith("computer_use_planner")
        or normalized_role.startswith("rpa_discovery"),
    )
    prefers_native_tools = requires_tools and _bool(preferred.get("supports_native_tools"), True)
    prefers_native_structured = require_structured_output and _bool(preferred.get("supports_native_structured_output"), True)

    return {
        "require_streaming": requires_streaming,
        "require_multimodal": requires_multimodal,
        "require_reasoning_blocks": requires_reasoning,
        "require_tools": requires_tools,
        "require_structured_output": bool(require_structured_output),
        "prefer_native_tools": prefers_native_tools,
        "prefer_native_structured_output": prefers_native_structured,
    }


def evaluate_capability_matrix(
    candidate_matrix: Mapping[str, Any] | None,
    requirements: Mapping[str, Any] | None,
) -> dict[str, Any]:
    matrix = dict(candidate_matrix or {})
    requirements_map = dict(requirements or {})
    degrade_reasons: list[str] = []

    if _bool(requirements_map.get("require_streaming")) and not _bool(matrix.get("supports_streaming")):
        return {
            "effectiveCapabilityMatch": False,
            "degradeApplied": False,
            "degradeReason": "streaming_unavailable",
        }
    if _bool(requirements_map.get("require_multimodal")) and not _bool(matrix.get("supports_multimodal")):
        return {
            "effectiveCapabilityMatch": False,
            "degradeApplied": False,
            "degradeReason": "multimodal_unavailable",
        }
    if _bool(requirements_map.get("require_reasoning_blocks")) and not _bool(matrix.get("supports_reasoning_blocks")):
        return {
            "effectiveCapabilityMatch": False,
            "degradeApplied": False,
            "degradeReason": "reasoning_blocks_unavailable",
        }

    if _bool(requirements_map.get("require_tools")):
        supports_any_tools = _bool(matrix.get("supports_native_tools")) or _bool(matrix.get("supports_prompt_emulated_tools"))
        if not supports_any_tools:
            return {
                "effectiveCapabilityMatch": False,
                "degradeApplied": False,
                "degradeReason": "tool_calling_unavailable",
            }
        if _bool(requirements_map.get("prefer_native_tools")) and not _bool(matrix.get("supports_native_tools")):
            degrade_reasons.append("prompt_emulated_tools")

    if _bool(requirements_map.get("require_structured_output")):
        supports_any_structured = _bool(matrix.get("supports_native_structured_output")) or _bool(matrix.get("supports_prompt_fallback_structured_output"))
        if not supports_any_structured:
            return {
                "effectiveCapabilityMatch": False,
                "degradeApplied": False,
                "degradeReason": "structured_output_unavailable",
            }
        if _bool(requirements_map.get("prefer_native_structured_output")) and not _bool(matrix.get("supports_native_structured_output")):
            degrade_reasons.append("prompt_fallback_structured_output")

    return {
        "effectiveCapabilityMatch": True,
        "degradeApplied": bool(degrade_reasons),
        "degradeReason": ",".join(degrade_reasons),
    }


def normalize_capability_metadata(
    capabilities: Mapping[str, Any] | None,
    *,
    capability_class: str,
    api_standard: str,
    runtime_ready: bool = True,
) -> dict[str, Any]:
    matrix = build_effective_capability_matrix(
        capability_class=capability_class,
        capabilities=capabilities,
        api_standard=api_standard,
        runtime_ready=runtime_ready,
    )
    return {
        "supportsTools": bool(matrix["supports_native_tools"] or matrix["supports_prompt_emulated_tools"]),
        "supportsStructuredOutput": bool(
            matrix["supports_native_structured_output"] or matrix["supports_prompt_fallback_structured_output"]
        ),
        "supportsStreaming": bool(matrix["supports_streaming"]),
        "supportsMultimodal": bool(matrix["supports_multimodal"]),
        "supportsReasoning": bool(matrix["supports_reasoning_blocks"]),
        "supportsNativeTools": bool(matrix["supports_native_tools"]),
        "supportsPromptEmulatedTools": bool(matrix["supports_prompt_emulated_tools"]),
        "supportsNativeStructuredOutput": bool(matrix["supports_native_structured_output"]),
        "supportsPromptFallbackStructuredOutput": bool(matrix["supports_prompt_fallback_structured_output"]),
        "supportsReasoningBlocks": bool(matrix["supports_reasoning_blocks"]),
        "runtimeReady": bool(matrix["runtime_ready"]),
    }
