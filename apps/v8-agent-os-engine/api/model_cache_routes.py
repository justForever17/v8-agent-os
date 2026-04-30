from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from core.database import db
from core.prompt_cache_gateway import load_prompt_cache_profiles, prompt_cache_gateway


router = APIRouter(prefix="/model-cache", tags=["model-cache"])


def _message_from_payload(item: dict[str, Any]) -> BaseMessage:
    role = str(item.get("role") or item.get("type") or "user").strip().lower()
    content = item.get("content", "")
    if role in {"system", "developer"}:
        return SystemMessage(content=content)
    if role in {"assistant", "ai"}:
        return AIMessage(content=content)
    if role == "tool":
        return ToolMessage(content=content, tool_call_id=str(item.get("tool_call_id") or item.get("toolCallId") or "dry-run-tool"))
    return HumanMessage(content=content)


def _tools_from_payload(items: Any) -> list[Any]:
    tools: list[Any] = []
    if not isinstance(items, list):
        return tools
    for item in items:
        if not isinstance(item, dict):
            continue
        tools.append(
            SimpleNamespace(
                name=str(item.get("name") or item.get("id") or "tool"),
                description=str(item.get("description") or ""),
                args_schema=item.get("args_schema") or item.get("argsSchema") or {},
            )
        )
    return tools


@router.get("/profiles")
async def get_model_cache_profiles():
    return load_prompt_cache_profiles()


@router.get("/stats")
async def get_model_cache_stats(
    limit: int = Query(50, ge=1, le=200),
    days: int = Query(1, ge=1, le=30),
):
    return db.get_prompt_cache_stats(limit=limit, days=days)


@router.post("/purge")
async def purge_model_cache():
    return db.purge_prompt_cache()


@router.post("/dry-run")
async def dry_run_model_cache(payload: dict[str, Any] = Body(...)):
    try:
        messages_payload = payload.get("messages") or []
        if not isinstance(messages_payload, list):
            raise HTTPException(status_code=422, detail="messages must be an array")
        messages = [_message_from_payload(item) for item in messages_payload if isinstance(item, dict)]
        if not messages:
            raise HTTPException(status_code=422, detail="messages are required")
        return prompt_cache_gateway.dry_run(
            messages=messages,
            provider_id=str(payload.get("providerId") or payload.get("provider_id") or ""),
            model_id=str(payload.get("modelId") or payload.get("model_id") or ""),
            model_ref=str(payload.get("modelRef") or payload.get("model_ref") or ""),
            role=str(payload.get("role") or ""),
            kwargs=dict(payload.get("kwargs") or {}),
            model_kwargs=dict(payload.get("modelKwargs") or payload.get("model_kwargs") or {}),
            meta=dict(payload.get("meta") or {}),
            bound_tools=_tools_from_payload(payload.get("boundTools") or payload.get("bound_tools")),
            streaming=bool(payload.get("streaming", False)),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
