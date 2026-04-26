from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from .models import ModelConnectionTestPayload
from core.extensions_runtime import extensions_runtime_service
from core.model_connection_tester import model_connection_tester
from core.model_control_plane import model_control_plane
from core.model_provider_catalog import model_provider_catalog
from core.model_telemetry import model_telemetry_service
from core.skills_install_service import SkillInstallValidationError, install_skill_from_command, install_skills_from_zip
from core.storage import storage


router = APIRouter()


class McpConfigValidationError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def _validate_mcp_server_map(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(config, dict):
        raise McpConfigValidationError("invalid_payload", "MCP 配置必须是 JSON 对象。")

    server_map = config.get("mcpServers") if "mcpServers" in config else config
    if not isinstance(server_map, dict):
        raise McpConfigValidationError("invalid_server_map", "`mcpServers` 必须是对象映射。")
    if not server_map:
        raise McpConfigValidationError("empty_server_map", "MCP 配置中至少需要包含一个 server。")

    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_server in server_map.items():
        server_name = str(raw_name or "").strip()
        if not server_name:
            raise McpConfigValidationError("empty_server_name", "MCP server 名称不能为空。")
        if not isinstance(raw_server, dict):
            raise McpConfigValidationError(
                "invalid_server_payload",
                f"MCP server `{server_name}` 的配置必须是对象。",
            )

        server = dict(raw_server)
        if "command" in server and not isinstance(server.get("command"), str):
            raise McpConfigValidationError("invalid_command", f"MCP server `{server_name}` 的 command 必须是字符串。")
        if "url" in server and not isinstance(server.get("url"), str):
            raise McpConfigValidationError("invalid_url", f"MCP server `{server_name}` 的 url 必须是字符串。")
        if "args" in server and not isinstance(server.get("args"), list):
            raise McpConfigValidationError("invalid_args", f"MCP server `{server_name}` 的 args 必须是数组。")
        if "env" in server and not isinstance(server.get("env"), dict):
            raise McpConfigValidationError("invalid_env", f"MCP server `{server_name}` 的 env 必须是对象。")
        if "headers" in server and not isinstance(server.get("headers"), dict):
            raise McpConfigValidationError("invalid_headers", f"MCP server `{server_name}` 的 headers 必须是对象。")

        disabled = bool(server.get("disabled", False))
        command = str(server.get("command") or "").strip()
        url = str(server.get("url") or "").strip()
        if not disabled and not command and not url:
            raise McpConfigValidationError(
                "missing_target",
                f"MCP server `{server_name}` 至少需要提供 command 或 url。",
            )
        normalized[server_name] = server

    return normalized


@router.get("/mcp/config")
async def get_mcp_config():
    try:
        return storage.get_mcp_config() or {"mcpServers": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mcp/status")
async def get_mcp_status():
    try:
        return {"servers": extensions_runtime_service.get_mcp_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mcp/config")
async def update_mcp_config(config: dict = Body(...)):
    try:
        new_servers = _validate_mcp_server_map(config)
        existing = storage.get_mcp_config() or {"mcpServers": {}}
        existing_servers = existing.get("mcpServers", {})
        existing_servers.update(new_servers)
        storage.save_mcp_config({"mcpServers": existing_servers})
        inventory_refresh = await extensions_runtime_service.refresh_inventory_if_changed(reason="mcp_config_update")
        runtime_health = extensions_runtime_service.build_health()
        return {"status": "success", "extensionsRuntime": runtime_health, "inventoryRefresh": inventory_refresh}
    except McpConfigValidationError as e:
        raise HTTPException(status_code=400, detail=e.to_payload())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/workspace")
async def get_workspace_config():
    try:
        return storage.get_workspace_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config/workspace")
async def update_workspace_config(config: dict = Body(...)):
    try:
        storage.save_workspace_config(config)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/reload")
async def reload_system():
    try:
        health = await extensions_runtime_service.reload()
        return {"status": "success", **health}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/list")
async def get_skills_list():
    try:
        return {"skills": list(extensions_runtime_service.list_skills(force_refresh=False))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/install/command")
async def install_skill_command(command: str = Body(..., embed=True)):
    try:
        result = install_skill_from_command(command)
        extensions_runtime_service.request_skill_inventory_refresh(reason="platform_skill_install_command")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/install/zip")
async def install_skill_zip(file: UploadFile = File(...)):
    try:
        content = await file.read()
        result = install_skills_from_zip(file.filename or "skills.zip", content)
        extensions_runtime_service.request_skill_inventory_refresh(reason="platform_skill_install_zip")
        return result
    except SkillInstallValidationError as e:
        raise HTTPException(status_code=400, detail=e.to_payload())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mcp/tools")
async def get_mcp_tools():
    try:
        from core.native_tools import NATIVE_TOOLS

        tools = extensions_runtime_service.get_mcp_tools()
        mcp_list = [
            {
                "name": tool.name,
                "description": getattr(tool, "description", ""),
                "serverName": getattr(tool, "metadata", {}).get("server_name", "Unknown")
                if getattr(tool, "metadata", None)
                else "Unknown",
            }
            for tool in tools
        ]
        native_list = [
            {
                "name": getattr(tool, "name", tool.__name__ if hasattr(tool, "__name__") else "unknown"),
                "description": getattr(tool, "description", getattr(tool, "__doc__", "")),
                "serverName": "系统原生能力 (Native Tools)",
            }
            for tool in NATIVE_TOOLS
        ]
        return {"mcpTools": native_list + mcp_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
async def get_models_config():
    try:
        return model_control_plane.get_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models")
async def save_models_config(data: dict = Body(...)):
    try:
        config = model_control_plane.save_config(data)
        return {"status": "success", "config": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/control-plane")
async def get_model_control_plane():
    try:
        config = model_control_plane.get_config()
        return model_control_plane.build_payload(config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/control-plane")
async def save_model_control_plane(data: dict = Body(...)):
    try:
        config = model_control_plane.save_config(data)
        return {"status": "success", **model_control_plane.build_payload(config)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/test-connection")
async def test_model_connection(payload: ModelConnectionTestPayload):
    try:
        result = model_connection_tester.test_model_connection(
            model_id=payload.model_id,
            model_ref=payload.model_ref or "",
            provider_id=payload.provider_id or "",
        )
        if result.get("ok"):
            return result
        raise HTTPException(status_code=422, detail=result)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models/catalog")
async def get_model_provider_catalog():
    try:
        return model_provider_catalog.load()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/providers/probe")
async def probe_model_provider(data: dict = Body(...)):
    try:
        provider_id = str(data.get("providerId") or data.get("provider_id") or "").strip()
        custom_provider_name = str(data.get("customProviderName") or data.get("custom_provider_name") or "").strip()
        base_url = str(data.get("baseUrl") or data.get("base_url") or "").strip()
        is_custom_probe = provider_id in {"", "__custom__", "custom"} and bool(custom_provider_name or base_url)
        if not provider_id and not is_custom_probe:
            raise HTTPException(status_code=422, detail="providerId is required")
        credential = str(data.get("apiKey") or data.get("api_key") or "").strip()
        credential_source = "request" if credential else ""
        provider_kind = str(data.get("providerKind") or data.get("provider_kind") or "").strip()
        media_modality = str(data.get("mediaModality") or data.get("media_modality") or "").strip()
        api_standard = str(data.get("apiStandard") or data.get("api_standard") or "openai").strip()
        provider = None
        if is_custom_probe:
            provider = model_provider_catalog.build_custom_provider(
                custom_provider_name,
                base_url,
                provider_kind=provider_kind or "chat",
                media_modality=media_modality,
                api_standard=api_standard,
            )
            provider_id = str(provider.get("id") or "")
        elif not credential:
            config = model_control_plane.get_config()
            existing_provider = (
                ((config.get("providers") or {}).get(provider_id) or {}).get("provider") or {}
            )
            stored_key = str(existing_provider.get("api_key") or "").strip()
            if stored_key and not stored_key.startswith("oauth:"):
                credential = stored_key
                credential_source = "stored_provider"
        result = (
            model_provider_catalog.probe_provider_entry(provider, credential=credential, base_url=base_url)
            if provider
            else model_provider_catalog.probe_provider(provider_id, credential=credential, base_url=base_url)
        )
        if is_custom_probe and result.get("ok"):
            saved_provider = model_provider_catalog.save_custom_provider(provider or {})
            result["provider"] = saved_provider
            result["providerId"] = saved_provider.get("id")
            result["customProviderSaved"] = True
        else:
            result["providerId"] = provider_id
        result["credentialSource"] = credential_source or "none"
        result["usedStoredCredential"] = credential_source == "stored_provider"
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/models/providers/custom/{provider_id}")
async def delete_custom_model_provider(provider_id: str):
    try:
        provider = model_provider_catalog.get_provider(provider_id)
        if not provider or not provider.get("isCustom"):
            raise HTTPException(status_code=404, detail="custom provider not found")
        deleted = model_provider_catalog.delete_custom_provider(provider_id)
        return {"ok": deleted, "providerId": provider_id}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/connect")
async def connect_model_provider(data: dict = Body(...)):
    try:
        provider_id = str(data.get("providerId") or data.get("provider_id") or "").strip()
        model_id = str(data.get("modelId") or data.get("model_id") or "").strip()
        if not provider_id or not model_id:
            raise HTTPException(status_code=422, detail="providerId and modelId are required")
        custom_provider_name = str(data.get("customProviderName") or data.get("custom_provider_name") or "").strip()
        base_url = str(data.get("baseUrl") or data.get("base_url") or "").strip()
        incoming_credential = str(data.get("apiKey") or data.get("api_key") or "").strip()
        provider_kind = str(data.get("providerKind") or data.get("provider_kind") or "").strip()
        media_modality = str(data.get("mediaModality") or data.get("media_modality") or "").strip()
        api_standard = str(data.get("apiStandard") or data.get("api_standard") or "").strip()
        requested_model_type = str(data.get("modelType") or data.get("type") or "").strip().upper()
        provider = model_provider_catalog.get_provider(provider_id)
        if not provider and provider_id in {"__custom__", "custom"}:
            if not incoming_credential:
                raise HTTPException(status_code=422, detail="apiKey is required before connecting this Provider")
            provider = model_provider_catalog.build_custom_provider(
                custom_provider_name,
                base_url,
                provider_kind=provider_kind or ("media_generation" if media_modality else "chat"),
                media_modality=media_modality,
                api_standard=api_standard or "openai",
            )
            provider = model_provider_catalog.save_custom_provider(provider)
            provider_id = str(provider.get("id") or "")
        if not provider:
            raise HTTPException(status_code=404, detail="provider not found")

        model = model_provider_catalog.normalize_model(provider, model_id)
        config = model_control_plane.get_config()
        providers = dict(config.get("providers") or {})
        existing = dict(providers.get(provider_id) or {})
        existing_provider = dict(existing.get("provider") or {})
        auth = dict(provider.get("auth") or {})
        credential = str(incoming_credential or existing_provider.get("api_key") or "").strip()
        if auth.get("type") == "api_key" and not credential:
            raise HTTPException(status_code=422, detail="apiKey is required before connecting this Provider")
        credential_mode = "oauthFile" if auth.get("type") == "oauth_file" else "apiKey"
        oauth_path = str(auth.get("path") or "")
        next_provider = {
            **existing_provider,
            "name": provider.get("name") or provider_id,
            "base_url": str(base_url or provider.get("baseUrl") or ""),
            "api_standard": api_standard or provider.get("apiStandard") or "openai",
            "providerKind": provider.get("providerKind") or existing_provider.get("providerKind") or "chat",
            "mediaModality": provider.get("mediaModality") or media_modality or existing_provider.get("mediaModality") or "",
            "type": "PLATFORM" if auth.get("type") == "oauth_file" else "API",
            "api_key": credential if auth.get("type") != "oauth_file" else f"oauth:{oauth_path}",
            "credential_mode": credential_mode,
            "oauth_preset": auth.get("preset") or existing_provider.get("oauth_preset") or "",
            "logoAsset": provider.get("logoAsset") or existing_provider.get("logoAsset") or "",
            "is_enabled": True,
        }
        is_custom_provider = bool(provider.get("isCustom"))
        is_oauth_provider = auth.get("type") == "oauth_file"
        media_model_types = {"MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"}
        normalized_model_type = requested_model_type if requested_model_type in media_model_types | {"TEXT", "MULTIMODAL", "EMBEDDING", "RERANK"} else str(model.get("type") or "TEXT").upper()
        is_media_provider = str(provider.get("providerKind") or "") == "media_generation" or normalized_model_type in media_model_types
        managed_context_window = None if (is_custom_provider or is_oauth_provider or is_media_provider) else model.get("contextWindow")
        managed_max_tokens = None if (is_custom_provider or is_oauth_provider or is_media_provider) else model.get("maxTokens")
        next_model = {
            "type": normalized_model_type or "TEXT",
            "contextWindow": managed_context_window,
            "maxTokens": managed_max_tokens,
            "capabilities": model.get("capabilities") or {},
            "capabilityClass": model.get("capabilityClass")
            or ("media_generation" if is_media_provider else "vision_multimodal" if (model.get("capabilities") or {}).get("vision") else "chat_general"),
            "capabilitySource": model.get("capabilitySource") or "manual",
            "parameterProfile": model.get("parameterProfile") or ("media_generation" if is_media_provider else "chat"),
            "mediaLimits": model.get("mediaLimits") or {},
            "isEnabled": True,
        }
        current_models = dict(existing.get("models") or {})
        if provider.get("singleActiveModel"):
            current_models = {}
        current_models[model_id] = next_model
        providers[provider_id] = {"provider": next_provider, "models": current_models}
        config["providers"] = providers
        saved = model_control_plane.save_config(config)
        return {
            "ok": True,
            "providerId": provider_id,
            "modelId": model_id,
            "modelRef": model.get("modelRef"),
            "config": saved,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/telemetry/overview")
async def get_telemetry_overview(days: int = 7):
    try:
        return model_telemetry_service.build_dashboard_overview(days=max(1, min(days, 30)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
