from __future__ import annotations

from typing import Any, Dict, Type

from core.provider_compatibility import normalize_provider_error


class V8LLMError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        provider: str = "unknown",
        model: str = "",
        retryable: bool = False,
        user_action: str = "",
        details: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}" + (f" ({user_action})" if user_action else ""))
        self.code = code
        self.provider = provider
        self.model = model
        self.retryable = retryable
        self.user_action = user_action
        self.details = details or {}
        self.message = message


class V8LLMAuthenticationError(V8LLMError):
    pass


class V8LLMRateLimitError(V8LLMError):
    pass


class V8LLMTimeoutError(V8LLMError):
    pass


class V8LLMProviderUnavailableError(V8LLMError):
    pass


class V8LLMCapabilityMismatchError(V8LLMError):
    pass


class V8LLMInvalidRequestError(V8LLMError):
    pass


class V8LLMContextWindowOverflowError(V8LLMError):
    pass


class V8LLMContentPolicyError(V8LLMError):
    pass


class V8LLMStructuredOutputError(V8LLMError):
    pass


_ERROR_CLASS_MAP: dict[str, Type[V8LLMError]] = {
    "auth_error": V8LLMAuthenticationError,
    "rate_limit": V8LLMRateLimitError,
    "timeout": V8LLMTimeoutError,
    "provider_unavailable": V8LLMProviderUnavailableError,
    "capability_mismatch": V8LLMCapabilityMismatchError,
    "invalid_request": V8LLMInvalidRequestError,
    "context_window_overflow": V8LLMContextWindowOverflowError,
    "content_policy_block": V8LLMContentPolicyError,
    "structured_output_invalid": V8LLMStructuredOutputError,
}


def build_llm_error_from_normalized(normalized: Dict[str, Any], *, details: Dict[str, Any] | None = None) -> V8LLMError:
    error_cls = _ERROR_CLASS_MAP.get(str(normalized.get("code") or ""), V8LLMError)
    return error_cls(
        code=str(normalized.get("code") or "unknown_provider_error"),
        message=str(normalized.get("message") or "Unknown provider error"),
        provider=str(normalized.get("provider") or "unknown"),
        model=str(normalized.get("model") or ""),
        retryable=bool(normalized.get("retryable")),
        user_action=str(normalized.get("userAction") or ""),
        details=details,
    )


def raise_as_v8_llm_error(exc: Exception, *, provider: str | None = None, model: str | None = None, details: Dict[str, Any] | None = None) -> None:
    if isinstance(exc, V8LLMError):
        raise exc
    normalized = normalize_provider_error(exc, provider=provider, model=model)
    error_details = dict(details or {})
    diagnostic = normalized.get("diagnostic")
    if isinstance(diagnostic, dict) and diagnostic:
        error_details["providerDiagnostic"] = dict(diagnostic)
    raise build_llm_error_from_normalized(normalized, details=error_details) from exc
