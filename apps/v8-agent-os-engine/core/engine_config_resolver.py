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
