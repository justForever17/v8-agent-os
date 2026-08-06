from __future__ import annotations

import re
from typing import Any, Dict, Iterable
from urllib.parse import quote, quote_plus

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


def _redact_provider_error_message(message: Any, *, sensitive_values: Iterable[Any] | None = None) -> str:
    redacted = str(message or "").strip()
    sensitive_variants: set[str] = set()
    for item in sensitive_values or ():
        value = str(item or "")
        if len(value) < 4:
            continue
        sensitive_variants.update(
            {
                value,
                quote(value),
                quote(value, safe=""),
                quote_plus(value),
            }
        )
    for value in sorted(sensitive_variants, key=len, reverse=True):
        redacted = redacted.replace(value, "[redacted]")
    patterns = (
        (r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)\s+)[^\s\"',}\]]+", r"\1[redacted]"),
        (r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passphrase|secret|cookie)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}&\]]+", r"\1[redacted]"),
        (r"(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|signature|sig)=)[^&#\s]+", r"\1[redacted]"),
        (r"(?i)([?&][A-Za-z0-9._~-]+)=([^&#\s\"']+)", r"\1=[redacted]"),
        (r"(?i)(https?://[^\s#]+#)[^\s\"']+", r"\1[redacted]"),
        (r"(?i)\b(?:sk|rk|pk|xox[abprs])-[-A-Za-z0-9_]{8,}\b", "[redacted]"),
        (r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[redacted]@"),
    )
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted)
    return redacted


def normalize_provider_error(
    exc: Exception,
    *,
    provider: str | None = None,
    model: str | None = None,
    sensitive_values: Iterable[Any] | None = None,
) -> Dict[str, Any]:
    raw_message = str(exc or "").strip() or "Unknown provider error"
    lower = raw_message.lower()
    supplied_sensitive_values = tuple(
        str(item or "") for item in sensitive_values or () if len(str(item or "")) >= 4
    )

    code = "unknown_provider_error"
    retryable = False
    user_action = "请检查模型配置或稍后重试。"

    if (
        any(token in lower for token in ("auth_unavailable", "no auth available"))
        and any(token in lower for token in ("503", "service unavailable", "temporarily unavailable"))
    ):
        code = "provider_unavailable"
        retryable = True
        user_action = "供应商认证资源暂时不可用，可稍后重试或切换同类模型。"
    elif any(token in lower for token in ("401", "unauthorized", "invalid api key", "authentication", "auth", "invalid access token", "token expired", "oauth 凭据已过期", "oauth credential expired")):
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

    safe_messages = {
        "auth_error": "Provider authentication failed.",
        "context_window_overflow": "Provider rejected the request because the context window was exceeded.",
        "rate_limit": "Provider rate limit was reached.",
        "quota_exceeded": "Provider quota was exceeded.",
        "timeout": "Provider request timed out.",
        "provider_unavailable": "Provider is temporarily unavailable.",
        "capability_mismatch": "Provider or model capability does not match this request.",
        "invalid_request": "Provider rejected the request parameters or model configuration.",
        "content_policy_block": "Provider blocked the request under its content policy.",
        "unknown_provider_error": "Provider request failed.",
    }
    redacted_message = _redact_provider_error_message(
        raw_message,
        sensitive_values=supplied_sensitive_values,
    )
    message = safe_messages.get(code, "Provider request failed.")
    if (
        not supplied_sensitive_values
        and redacted_message
        and redacted_message == raw_message
        and len(redacted_message) <= 160
        and code != "unknown_provider_error"
    ):
        # Preserve a short non-secret provider diagnostic only after the
        # credential-aware scrubber has found nothing to remove.
        message = redacted_message
    return {
        "code": code,
        "provider": provider or "unknown",
        "model": model or "",
        "retryable": retryable,
        "message": message,
        "userAction": user_action,
    }
