from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from .models import ModelConnectionTestPayload
from core.extensions_runtime import extensions_runtime_service
from core.model_connection_tester import model_connection_tester
from core.model_control_plane import model_control_plane
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
        runtime_health = await extensions_runtime_service.reload()
        return {"status": "success", "extensionsRuntime": runtime_health}
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
        result = model_connection_tester.test_model_connection(model_id=payload.model_id)
        if result.get("ok"):
            return result
        raise HTTPException(status_code=422, detail=result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/telemetry/overview")
async def get_telemetry_overview(days: int = 7):
    try:
        return model_telemetry_service.build_dashboard_overview(days=max(1, min(days, 30)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
