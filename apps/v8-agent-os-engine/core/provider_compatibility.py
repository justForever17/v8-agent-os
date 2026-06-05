from __future__ import annotations

from typing import Any, Dict

from core.reasoning_payload_contract import REASONING_KEYS, SIGNATURE_KEYS


REASONING_STREAM_KEYS = (*REASONING_KEYS, *SIGNATURE_KEYS)
_PROVIDER_MESSAGE_REASONING_KEYS = tuple(
    key for key in REASONING_KEYS if key in {"reasoning", "thinking", "reasoning_details", "thought"}
)

_PATCHED = False


def install_provider_compatibility_patches() -> None:
    global _PATCHED
    if _PATCHED:
        return

    try:
        import langchain_openai.chat_models.base as openai_base
        from langchain_core.messages import AIMessage
    except Exception:
        _PATCHED = True
        return

    original_convert = openai_base._convert_message_to_dict
    original_convert_delta = openai_base._convert_delta_to_message_chunk

    def patched_convert(message, api="chat/completions") -> dict:
        try:
            message_dict = original_convert(message, api=api)
        except TypeError:
            message_dict = original_convert(message)
        if isinstance(message, AIMessage) and message_dict.get("role") == "assistant":
            additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
            if message_dict.get("tool_calls"):
                message_dict["reasoning_content"] = additional_kwargs.get("reasoning_content", "")
            for key in _PROVIDER_MESSAGE_REASONING_KEYS:
                if key in additional_kwargs and key not in message_dict:
                    message_dict[key] = additional_kwargs[key]
        return message_dict

    def patched_convert_delta(delta_dict, default_class):
        chunk = original_convert_delta(delta_dict, default_class)
        if hasattr(chunk, "additional_kwargs"):
            additional_kwargs = dict(getattr(chunk, "additional_kwargs", {}) or {})
            for key in REASONING_STREAM_KEYS:
                if key in delta_dict:
                    additional_kwargs[key] = delta_dict[key]
            chunk.additional_kwargs = additional_kwargs
        return chunk

    openai_base._convert_message_to_dict = patched_convert
    openai_base._convert_delta_to_message_chunk = patched_convert_delta
    _PATCHED = True


def normalize_provider_error(exc: Exception, *, provider: str | None = None, model: str | None = None) -> Dict[str, Any]:
    message = str(exc or "").strip() or "Unknown provider error"
    lower = message.lower()

    code = "unknown_provider_error"
    retryable = False
    user_action = "请检查模型配置或稍后重试。"

    if any(token in lower for token in ("401", "unauthorized", "invalid api key", "authentication", "auth", "invalid access token", "token expired", "oauth 凭据已过期", "oauth credential expired")):
        code = "auth_error"
        user_action = "请检查供应商 API Key / OAuth 凭据。"
    elif any(
        token in lower
        for token in (
            "context_length_exceeded",
            "context length exceeded",
            "maximum context",
            "context window",
            "too many tokens",
            "token limit",
            "maximum token",
            "prompt is too long",
            "input is too long",
            "exceeds the context",
            "exceed context",
            "reduce the length",
        )
    ):
        code = "context_window_overflow"
        user_action = "当前上下文超过模型窗口，请检查模型 context window 配置或启用/调整上下文压缩。"
    elif any(token in lower for token in ("429", "rate limit", "too many requests")):
        code = "rate_limit"
        retryable = True
        user_action = "当前触发限流，可稍后重试或切换同类模型。"
    elif "quota" in lower or "insufficient_balance" in lower:
        code = "quota_exceeded"
        user_action = "当前供应商额度不足，请检查配额或切换模型。"
    elif any(token in lower for token in ("timeout", "timed out", "deadline exceeded")):
        code = "timeout"
        retryable = True
        user_action = "请求超时，可重试或切换同类模型。"
    elif any(token in lower for token in ("503", "502", "service unavailable", "overloaded", "temporarily unavailable", "connection reset")):
        code = "provider_unavailable"
        retryable = True
        user_action = "供应商暂时不可用，可稍后重试或切换同类模型。"
    elif any(token in lower for token in ("capability", "unsupported", "does not support", "not support", "vision input is not enabled")):
        code = "capability_mismatch"
        user_action = "当前模型能力不匹配，请改用兼容角色模型。"
    elif any(token in lower for token in ("400", "invalid request", "malformed", "bad request", "model not found")):
        code = "invalid_request"
        user_action = "请求参数或模型配置有误，请检查后重试。"
    elif any(token in lower for token in ("content policy", "safety", "policy violation")):
        code = "content_policy_block"
        user_action = "请求触发了供应商安全策略，请调整输入。"

    return {
        "code": code,
        "provider": provider or "unknown",
        "model": model or "",
        "retryable": retryable,
        "message": message,
        "userAction": user_action,
    }


install_provider_compatibility_patches()
