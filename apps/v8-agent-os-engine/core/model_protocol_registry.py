"""Model-level wire protocol advice.

Provider ``api_standard`` describes a family of adapters.  It is not enough to
decide which request schema a particular model uses (for example, OpenAI now
has both Chat Completions and Responses).  This module keeps that distinction
explicit and auditable.  Advice is only a default for newly connected models;
an explicit ``wireProtocol`` in a model binding always wins.
"""

from __future__ import annotations

from typing import Any, Dict


OPENAI_CHAT_COMPLETIONS = "openai.chat_completions"
OPENAI_RESPONSES = "openai.responses"
ANTHROPIC_MESSAGES = "anthropic.messages"
GEMINI_GENERATE_CONTENT = "gemini.generate_content"

_ENDPOINT_PATHS = {
    OPENAI_CHAT_COMPLETIONS: "chat/completions",
    OPENAI_RESPONSES: "responses",
    ANTHROPIC_MESSAGES: "messages",
    GEMINI_GENERATE_CONTENT: "models/{model}:generateContent",
}

_SOURCE_REFS = {
    "openai": [
        "https://developers.openai.com/api/docs/models",
        "https://developers.openai.com/api/docs/guides/migrate-to-responses",
    ],
    "anthropic": ["https://platform.claude.com/docs/en/api/messages"],
    "gemini": ["https://ai.google.dev/api/generate-content"],
    "openrouter": ["https://openrouter.ai/docs/api/api-reference/responses/create-responses"],
    "deepseek": ["https://api-docs.deepseek.com/api/create-chat-completion"],
    "dashscope": ["https://help.aliyun.com/en/model-studio/first-api-call-to-qwen"],
    "dashscope_responses": ["https://help.aliyun.com/en/model-studio/qwen-api-via-openai-responses"],
    "zhipu": ["https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8"],
    "minimax": ["https://platform.minimaxi.com/docs/api-reference/text-chat-openai"],
    "perplexity": ["https://docs.perplexity.ai/docs/sonar/quickstart"],
    "xiaomi_mimo": [
        "https://mimo.mi.com/docs/zh-CN/welcome",
        "https://mimo.mi.com/docs/zh-CN/api/chat/anthropic-api",
    ],
    "sensenova": ["https://platform.sensenova.cn/product/APIService/document"],
    "volcengine": ["https://www.volcengine.com/docs/82379/2123434"],
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _provider_family(provider_id: Any, api_standard: Any, provider_meta: Dict[str, Any]) -> str:
    raw = " ".join(
        _clean(item).lower()
        for item in (
            provider_id,
            provider_meta.get("name"),
            provider_meta.get("code"),
            provider_meta.get("base_url") or provider_meta.get("baseUrl"),
        )
    )
    standard = _clean(api_standard).lower()
    if "anthropic" in raw or standard == "anthropic":
        return "anthropic"
    if "gemini" in raw or "google" in raw or standard in {"gemini", "google"}:
        return "gemini"
    if "openrouter" in raw:
        return "openrouter"
    if "deepseek" in raw:
        return "deepseek"
    if "dashscope" in raw or "qwen" in raw or "aliyun" in raw:
        return "dashscope"
    if "zhipu" in raw or "bigmodel" in raw or "glm" in raw or "zai-coding" in raw or "api.z.ai" in raw:
        return "zhipu"
    if "minimax" in raw:
        return "minimax"
    if "perplexity" in raw or "sonar" in raw:
        return "perplexity"
    if "xiaomi" in raw or "mimo" in raw or "tokenplan" in raw:
        return "xiaomi_mimo"
    if "sensenova" in raw or "sensetime" in raw:
        return "sensenova"
    if "volcengine" in raw or "volces.com" in raw or "doubao" in raw:
        return "volcengine"
    if "openai" in raw or "codex" in raw or "chatgpt.com" in raw:
        return "openai"
    return "unknown"


def _result(
    protocol: str,
    *,
    confidence: str,
    source_refs: list[str],
    warning: str = "",
    source: str = "provider_docs",
) -> Dict[str, Any]:
    return {
        "wireProtocol": protocol,
        "endpointPath": _ENDPOINT_PATHS.get(protocol, ""),
        "confidence": confidence,
        "source": source,
        "sourceRefs": list(source_refs),
        "warning": warning,
    }


def _provider_source_refs(key: str, provider: Dict[str, Any]) -> list[str]:
    return list(dict.fromkeys([
        *([_clean(provider.get("sourceUrl"))] if _clean(provider.get("sourceUrl")) else []),
        *_SOURCE_REFS.get(key, []),
    ]))


def suggest_model_protocol(
    provider_id: Any,
    api_standard: Any,
    model_id: Any,
    *,
    provider_meta: Dict[str, Any] | None = None,
    model_meta: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return a conservative protocol suggestion without changing a binding.

    The result is deliberately serializable because it is projected to Admin.
    ``confidence=hint`` means the UI must show a verification warning.
    """

    provider = dict(provider_meta or {})
    model = dict(model_meta or {})
    model_type = _clean(model.get("type")).upper()
    capability_class = _clean(model.get("capabilityClass") or model.get("capability_class")).lower()
    provider_kind = _clean(provider.get("providerKind") or provider.get("provider_kind")).lower()
    media_modality = _clean(provider.get("mediaModality") or provider.get("media_modality")).lower()
    standard = _clean(api_standard).lower()
    if (
        model_type in {"IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "MEDIA", "WORKFLOW", "MODEL3D", "EMBEDDING", "RERANK", "RERANKER"}
        or capability_class in {"media_generation", "embedding", "rerank", "reranker"}
        or provider_kind == "media_generation"
        or bool(media_modality)
        or standard == "comfyui"
    ):
        return _result("", confidence="not_applicable", source_refs=[])

    custom_provider = str(provider_id or "").lower().startswith("custom-") or bool(provider.get("isCustom"))
    family = _provider_family(provider_id, api_standard, provider)
    model_name = _clean(model_id).lower()
    if custom_provider:
        if standard == "anthropic":
            return _result(ANTHROPIC_MESSAGES, confidence="hint", source_refs=[], warning="自定义供应商的协议未由公共目录确认，请到模型官网核实端点。")
        if standard in {"gemini", "google"}:
            return _result(GEMINI_GENERATE_CONTENT, confidence="hint", source_refs=[], warning="自定义供应商的协议未由公共目录确认，请到模型官网核实端点。")
        return _result(
            OPENAI_CHAT_COMPLETIONS,
            confidence="hint",
            source_refs=[],
            warning="自定义供应商未由公共目录确认端点，当前按 OpenAI Chat Completions 兼容方式建议；请到模型官网核实。",
            source="fallback",
        )
    if family == "anthropic":
        return _result(ANTHROPIC_MESSAGES, confidence="reviewed", source_refs=_provider_source_refs("anthropic", provider))
    if family == "gemini":
        return _result(GEMINI_GENERATE_CONTENT, confidence="reviewed", source_refs=_provider_source_refs("gemini", provider))
    if family == "openai":
        # Current first-party GPT reasoning/vision families are documented on
        # both endpoints; Responses is the recommended new-project default.
        if model_name.startswith(("gpt-5", "o1", "o3", "o4")) and not str(provider_id).lower().startswith("custom-"):
            return _result(OPENAI_RESPONSES, confidence="reviewed", source_refs=_provider_source_refs("openai", provider))
        return _result(OPENAI_CHAT_COMPLETIONS, confidence="reviewed", source_refs=_provider_source_refs("openai", provider))
    if family == "openrouter":
        return _result(OPENAI_CHAT_COMPLETIONS, confidence="reviewed", source_refs=_provider_source_refs("openrouter", provider))
    if family == "dashscope" and "qwen3.5-ocr" in model_name:
        return _result(OPENAI_RESPONSES, confidence="reviewed", source_refs=_provider_source_refs("dashscope_responses", provider))
    if family in {"deepseek", "dashscope", "zhipu", "minimax"}:
        return _result(OPENAI_CHAT_COMPLETIONS, confidence="reviewed", source_refs=_provider_source_refs(family, provider))
    if family == "perplexity":
        return _result(OPENAI_CHAT_COMPLETIONS, confidence="reviewed", source_refs=_provider_source_refs("perplexity", provider))
    if family == "xiaomi_mimo":
        # MiMo documents Chat Completions, Responses and Anthropic-compatible
        # surfaces.  The provider-level apiStandard selects the compatibility
        # family; Chat Completions remains the conservative OpenAI default.
        if standard == "anthropic":
            return _result(ANTHROPIC_MESSAGES, confidence="reviewed", source_refs=_provider_source_refs("xiaomi_mimo", provider))
        return _result(OPENAI_CHAT_COMPLETIONS, confidence="reviewed", source_refs=_provider_source_refs("xiaomi_mimo", provider))
    if family == "volcengine":
        return _result(OPENAI_CHAT_COMPLETIONS, confidence="reviewed", source_refs=_provider_source_refs("volcengine", provider))
    if family == "sensenova":
        return _result(
            OPENAI_CHAT_COMPLETIONS,
            confidence="hint",
            source_refs=_provider_source_refs("sensenova", provider),
            warning="该供应商公开文档使用专用 chat-completions 路径，请在模型官网核实并手动选择端点。",
        )

    fallback_refs = [_clean(provider.get("sourceUrl"))] if _clean(provider.get("sourceUrl")) else []
    if _clean(api_standard).lower() == "anthropic":
        return _result(ANTHROPIC_MESSAGES, confidence="hint", source_refs=fallback_refs, warning="公共资料未确认此模型，请到模型官网核实端点。")
    if _clean(api_standard).lower() in {"gemini", "google"}:
        return _result(GEMINI_GENERATE_CONTENT, confidence="hint", source_refs=fallback_refs, warning="公共资料未确认此模型，请到模型官网核实端点。")
    return _result(
        OPENAI_CHAT_COMPLETIONS,
        confidence="hint",
        source_refs=fallback_refs,
        warning="公共资料未确认此模型端点，当前仅按 OpenAI Chat Completions 兼容方式建议；请到模型官网核实。",
    )


def endpoint_path_for_protocol(protocol: Any) -> str:
    return _ENDPOINT_PATHS.get(_clean(protocol), "")


__all__ = [
    "OPENAI_CHAT_COMPLETIONS",
    "OPENAI_RESPONSES",
    "ANTHROPIC_MESSAGES",
    "GEMINI_GENERATE_CONTENT",
    "endpoint_path_for_protocol",
    "suggest_model_protocol",
]
