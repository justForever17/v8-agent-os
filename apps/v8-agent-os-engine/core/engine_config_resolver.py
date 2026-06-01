from __future__ import annotations

from typing import Any, Dict

from api.models import EngineConfig
from core.model_control_plane import model_control_plane
from core.oauth_credentials import resolve_provider_oauth_credential
from core.storage import storage


def _hydrate_provider_credentials(provider: str, provider_config: Dict[str, Any]) -> tuple[str, str]:
    base_url = str(provider_config.get("base_url") or "")
    oauth_resolution = resolve_provider_oauth_credential(
        provider_id=provider,
        provider_config=provider_config,
    )
    api_key = str(oauth_resolution.get("credential") or "")

    if oauth_resolution.get("error"):
        raise RuntimeError(str(oauth_resolution["error"]))

    if not api_key and provider.lower() in ["qwen", "qwen-oauth", "platform"]:
        from core.credential_sniffer import QwenCredentialSniffer

        local_qwen_token = QwenCredentialSniffer.get_qwen_token()
        if local_qwen_token:
            api_key = local_qwen_token

    if not base_url:
        if provider.lower() == "deepseek":
            base_url = "https://api.deepseek.com/v1"
        elif provider.lower() in ["qwen", "dashscope"]:
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        elif provider.lower() == "siliconflow":
            base_url = "https://api.siliconflow.cn/v1"
        elif provider.lower() == "modelscope":
            base_url = "https://api-inference.modelscope.cn/v1"
        elif provider.lower() in ["volcengine", "volcengine-coding", "doubao"]:
            base_url = "https://ark.cn-beijing.volces.com/api/v3"
        elif provider.lower() == "openrouter":
            base_url = "https://openrouter.ai/api/v1"

    return api_key, base_url


def resolve_engine_config_for_role(
    role: str,
    *,
    fallback_provider: str = "openai",
    fallback_model: str = "gpt-4o",
    require_explicit: bool = False,
) -> dict[str, Any]:
    routes = storage.get_routes()
    resolution = model_control_plane.resolve_model_for_role(role)

    should_use_role_binding = not require_explicit or resolution.get("bindingState") == "explicit"
    resolved_model_id = str(resolution.get("resolvedModelId") or "") if should_use_role_binding else ""
    resolved_provider_id = str(resolution.get("resolvedProviderId") or "") if should_use_role_binding else ""

    provider = resolved_provider_id or fallback_provider
    model_name = resolved_model_id or fallback_model

    if resolved_model_id and not resolved_provider_id:
        for provider_name, provider_data in routes.get("providers", {}).items():
            if resolved_model_id in (provider_data.get("models") or {}):
                provider = provider_name
                break

    provider_config = ((routes.get("providers") or {}).get(provider) or {}).get("provider") or {}
    api_key, base_url = _hydrate_provider_credentials(provider, provider_config)

    return {
        "engine_config": EngineConfig(
            provider=provider,
            model_name=model_name,
            api_key=api_key or None,
            base_url=base_url or None,
        ),
        "resolution": resolution,
    }


def resolve_engine_config_for_model_ref(
    model_ref: str,
    *,
    provider_id: str = "",
    fallback_provider: str = "openai",
    fallback_model: str = "gpt-4o",
) -> dict[str, Any]:
    """Resolve an explicit per-run model ref without mutating role bindings."""
    normalized_model_ref = str(model_ref or "").strip()
    normalized_provider_id = str(provider_id or "").strip()
    record = (
        model_control_plane.get_model_record(normalized_model_ref, provider_id=normalized_provider_id)
        if normalized_model_ref
        else None
    )
    resolved_provider_id = str((record or {}).get("provider_id") or normalized_provider_id or fallback_provider)
    resolved_model_id = str((record or {}).get("model_id") or normalized_model_ref or fallback_model)

    routes = storage.get_routes()
    provider_config = ((routes.get("providers") or {}).get(resolved_provider_id) or {}).get("provider") or {}
    api_key, base_url = _hydrate_provider_credentials(resolved_provider_id, provider_config)

    return {
        "engine_config": EngineConfig(
            provider=resolved_provider_id,
            model_name=resolved_model_id,
            api_key=api_key or None,
            base_url=base_url or None,
        ),
        "resolution": {
            "role": "request_override",
            "bindingState": "request_override" if record else "missing",
            "rawModelId": normalized_model_ref,
            "resolvedModelId": resolved_model_id if record else "",
            "resolvedModelRef": str((record or {}).get("model_ref") or ""),
            "resolvedProviderId": resolved_provider_id if record else "",
            "lookupStatus": "exact" if record else "missing",
        },
    }
