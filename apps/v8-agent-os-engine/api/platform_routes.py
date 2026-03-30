from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from .models import ModelConnectionTestPayload
from core.extensions_runtime import extensions_runtime_service
from core.model_connection_tester import model_connection_tester
from core.model_control_plane import model_control_plane
from core.model_telemetry import model_telemetry_service
from core.skills_install_service import install_skill_from_command, install_skills_from_zip
from core.storage import storage
from mcp_client import mcp_manager


router = APIRouter()


@router.get("/mcp/config")
async def get_mcp_config():
    try:
        return storage.get_mcp_config() or {"mcpServers": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mcp/status")
async def get_mcp_status():
    try:
        return {"servers": mcp_manager.get_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mcp/config")
async def update_mcp_config(config: dict = Body(...)):
    try:
        new_servers = config["mcpServers"] if "mcpServers" in config else config
        existing = storage.get_mcp_config() or {"mcpServers": {}}
        existing_servers = existing.get("mcpServers", {})
        existing_servers.update(new_servers)
        storage.save_mcp_config({"mcpServers": existing_servers})
        return {"status": "success"}
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
        from skills.loader import SkillLoader

        return {"skills": list(SkillLoader.get_all_skills().values())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/install/command")
async def install_skill_command(command: str = Body(..., embed=True)):
    try:
        return install_skill_from_command(command)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/install/zip")
async def install_skill_zip(file: UploadFile = File(...)):
    try:
        content = await file.read()
        return install_skills_from_zip(file.filename or "skills.zip", content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mcp/tools")
async def get_mcp_tools():
    try:
        from core.native_tools import NATIVE_TOOLS

        tools = mcp_manager.get_tools()
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
