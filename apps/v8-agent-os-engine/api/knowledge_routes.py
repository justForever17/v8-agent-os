import asyncio
import json
import uuid
from typing import List, Union

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .models import (
    ChannelBindingPayload,
    GraphEntityPayload,
    GraphRelationPayload,
    PreferenceMutationPayload,
    ProjectDescriptorPayload,
    WorkspaceBindingPayload,
    WorkflowBindingPayload,
)
from core.model_control_plane import model_control_plane
from core.realtime_protocol import format_ndjson
from core.response_normalizer import extract_text_and_reasoning, normalize_tool_calls
from core.storage import MEMORY_DURABLE_POLICY_DEFAULTS, storage
from core.workspace_resolution import workspace_resolution_service
from runtimes.chat.runtime import StreamFilter
from runtimes.memory.prompts import render_memory_admin_chat_prompt
from runtimes.memory.project_registry import project_registry_service
from runtimes.memory.runtime import memory_runtime


router = APIRouter()


def _update_role_binding(role: str, model_id: str | None):
    config = model_control_plane.get_config()
    roles = dict(config.get("roles") or {})
    roles[role] = str(model_id or "").strip()
    config["roles"] = roles
    model_control_plane.save_config(config)


def _get_role_binding(role: str) -> str:
    return str(model_control_plane.get_role_model_id(role) or "").strip()


@router.get("/agents")
async def get_agents():
    try:
        return {"agents": storage.get_all_agents()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents")
async def save_agent(agent: dict = Body(...)):
    try:
        storage.save_agent(agent)
        return {"status": "success", "id": agent.get("id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    try:
        success = storage.delete_agent(agent_id)
        if not success:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/context/config")
async def get_context_config():
    try:
        return {
            "policy": storage.get_context_config() or {},
            "bindings": {
                "summary_model": _get_role_binding("summary"),
            },
            "source_map": {
                "summary_model": "models.roles.summary",
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/context/config")
async def update_context_config(config: dict = Body(...)):
    try:
        if not isinstance(config, dict) or "policy" not in config:
            raise HTTPException(status_code=400, detail="Expected payload with { policy, bindings }.")
        policy = dict(config.get("policy") or {})
        bindings = dict(config.get("bindings") or {})
        summary_model = str(bindings.get("summary_model") or "").strip()
        _update_role_binding("summary", summary_model)
        storage.save_context_config(policy)
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/config")
async def get_memory_config():
    try:
        config = storage.get_memory_config() or {}
        metadata = storage.get_memory_config_metadata()
        config.setdefault("recall_strategy", "balanced")
        config.setdefault("recall_top_k", 3)
        config.setdefault("retrieval_threshold", metadata["recommendedRetrievalThreshold"])
        config.setdefault("passive_injection_enabled", True)
        config.setdefault("max_recent_days", 1)
        config.setdefault("max_context_tokens", 2000)
        config.setdefault("extraction_enabled", True)
        for key, value in MEMORY_DURABLE_POLICY_DEFAULTS.items():
            config.setdefault(key, value)
        config.setdefault("graph_enabled", True)
        config.setdefault("fts_enabled", True)
        config["extraction_model"] = _get_role_binding("extraction")
        config["embedding_model"] = _get_role_binding("embedding")
        config["reranker_model"] = _get_role_binding("reranker")
        config["recommended_retrieval_threshold"] = metadata["recommendedRetrievalThreshold"]
        config["retrieval_threshold_source"] = metadata["retrievalThresholdSource"]
        config["retrieval_threshold_is_default"] = metadata["retrievalThresholdIsDefault"]
        config["durable_policy_defaults"] = metadata["durablePolicyDefaults"]
        return config
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/config")
async def update_memory_config(config: dict = Body(...)):
    try:
        next_config = dict(config or {})
        for ui_only_key in (
            "recommended_retrieval_threshold",
            "retrieval_threshold_source",
            "retrieval_threshold_is_default",
            "durable_policy_defaults",
        ):
            next_config.pop(ui_only_key, None)
        if "extraction_model" in next_config:
            extraction_model = str(next_config.get("extraction_model") or "").strip()
            _update_role_binding("extraction", "" if extraction_model.lower() == "none" else extraction_model)
            next_config.pop("extraction_model", None)
        if "embedding_model" in next_config:
            embedding_model = str(next_config.get("embedding_model") or "").strip()
            _update_role_binding("embedding", "" if embedding_model.lower() == "none" else embedding_model)
            next_config.pop("embedding_model", None)
        if "reranker_model" in next_config:
            reranker_model = str(next_config.get("reranker_model") or "").strip()
            _update_role_binding("reranker", "" if reranker_model.lower() == "none" else reranker_model)
            next_config.pop("reranker_model", None)
        if "recall_strategy" in next_config:
            recall_strategy = str(next_config.get("recall_strategy") or "balanced").strip().lower()
            next_config["recall_strategy"] = recall_strategy if recall_strategy in {"balanced", "semantic", "keyword"} else "balanced"
        if "recall_top_k" in next_config:
            try:
                next_config["recall_top_k"] = max(1, min(int(next_config.get("recall_top_k") or 3), 10))
            except (TypeError, ValueError):
                next_config["recall_top_k"] = 3
        if "retrieval_threshold" in next_config:
            try:
                threshold = float(next_config.get("retrieval_threshold") or 0.0)
            except (TypeError, ValueError):
                threshold = 0.0
            next_config["retrieval_threshold"] = max(0.0, min(threshold, 1.0))
        for key, default in (
            ("preference_importance_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["preference_importance_threshold"]),
            ("knowledge_importance_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["knowledge_importance_threshold"]),
            ("global_knowledge_importance_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["global_knowledge_importance_threshold"]),
            ("global_operational_importance_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_importance_threshold"]),
        ):
            if key in next_config:
                try:
                    next_config[key] = max(0, min(int(next_config.get(key) or default), 100))
                except (TypeError, ValueError):
                    next_config[key] = default
        for key, default in (
            ("preference_confidence_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["preference_confidence_threshold"]),
            ("knowledge_confidence_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["knowledge_confidence_threshold"]),
            ("global_knowledge_confidence_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["global_knowledge_confidence_threshold"]),
            ("global_operational_confidence_threshold", MEMORY_DURABLE_POLICY_DEFAULTS["global_operational_confidence_threshold"]),
        ):
            if key in next_config:
                try:
                    next_config[key] = max(0.0, min(float(next_config.get(key) or default), 1.0))
                except (TypeError, ValueError):
                    next_config[key] = default
        storage.save_memory_config(next_config)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/preferences")
async def get_memory_preferences():
    try:
        return memory_runtime.get_preference_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/memory/preferences")
async def upsert_memory_preference(payload: PreferenceMutationPayload):
    try:
        value = (payload.value or "").strip()
        if not payload.key.strip():
            raise HTTPException(status_code=400, detail="Preference key is required.")
        if not value:
            raise HTTPException(status_code=400, detail="Preference value is required.")
        memory_runtime.upsert_preference(
            key=payload.key.strip(),
            value=value,
            scope=(payload.scope or "global").strip() or "global",
        )
        return {"updated": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/preferences")
async def delete_memory_preference(payload: PreferenceMutationPayload):
    try:
        deleted = memory_runtime.delete_preference(
            key=payload.key.strip(),
            scope=(payload.scope or "global").strip() or "global",
        )
        return {"deleted": deleted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/dashboard")
async def get_memory_dashboard():
    try:
        return memory_runtime.get_dashboard()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/graph/entity/{entity}")
async def get_graph_entity(entity: str):
    try:
        return {"entity": entity, "relations": memory_runtime.query_entity(entity=entity)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/graph/multi-hop/{entity}")
async def get_graph_multi_hop(entity: str, hops: int = 2):
    try:
        return {"start": entity, "hops": hops, "paths": memory_runtime.query_multi_hop(entity=entity, hops=hops)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/search")
async def memory_fts_search(q: str, scope: str = None):
    try:
        results = memory_runtime.search_full_text(query=q, scope=scope, limit=20)
        valid_results = [
            item
            for item in results
            if str(item.get("scope") or "global").strip() == "global"
            or str(item.get("scope") or "").startswith(("project:", "channel:"))
        ]
        return {"query": q, "results": valid_results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/recall-preview")
async def memory_recall_preview(q: str, scope: str = None):
    try:
        preview = memory_runtime.preview_unified_recall(query=q, scope=scope, limit=8)
        metadata = storage.get_memory_config_metadata()
        return {
            **preview,
            "threshold_source": metadata["retrievalThresholdSource"],
            "recommended_retrieval_threshold": metadata["recommendedRetrievalThreshold"],
            "retrieval_threshold_is_default": metadata["retrievalThresholdIsDefault"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/knowledge")
async def get_all_knowledge(scope: str = None, limit: int = 50):
    try:
        results = memory_runtime.list_knowledge(scope=scope, limit=limit)
        return {"items": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/graph/all")
async def get_full_graph(limit: int = 100):
    try:
        return memory_runtime.get_full_graph(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/graph/search")
async def search_graph_entities(keyword: str, limit: int = 20):
    try:
        return {"items": memory_runtime.search_entities(keyword=keyword, limit=limit), "keyword": keyword}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/graph/entity")
async def add_graph_entity(payload: GraphEntityPayload):
    try:
        memory_runtime.add_entity(name=payload.name, entity_type=payload.entity_type)
        return {"created": True, "name": payload.name.lower(), "entityType": payload.entity_type}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/graph/entity")
async def remove_graph_entity(payload: GraphEntityPayload):
    try:
        deleted = memory_runtime.delete_entity(name=payload.name)
        return {"deleted": deleted, "name": payload.name.lower()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/graph/relation")
async def add_graph_relation(payload: GraphRelationPayload):
    try:
        memory_runtime.add_relation(
            subject=payload.subject,
            predicate=payload.predicate,
            object_name=payload.object_name,
            confidence=payload.confidence or 1.0,
        )
        return {
            "created": True,
            "relation": {
                "subject": payload.subject.lower(),
                "predicate": payload.predicate.upper(),
                "object": payload.object_name.lower(),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/graph/relation")
async def remove_graph_relation(payload: GraphRelationPayload):
    try:
        deleted = memory_runtime.delete_relation(
            subject=payload.subject,
            predicate=payload.predicate,
            object_name=payload.object_name,
        )
        return {
            "deleted": deleted,
            "relation": {
                "subject": payload.subject.lower(),
                "predicate": payload.predicate.upper(),
                "object": payload.object_name.lower(),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/knowledge/{fact_id}")
async def delete_knowledge_item(fact_id: str):
    try:
        return {"deleted": memory_runtime.delete_knowledge(fact_id=fact_id), "id": fact_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/memory/knowledge/{fact_id}")
async def update_knowledge_item(fact_id: str, body: dict = Body(...)):
    try:
        new_fact = body.get("fact", "")
        category = body.get("category")
        scope = body.get("scope")
        if not new_fact:
            raise HTTPException(status_code=400, detail="fact is required")
        ok = memory_runtime.update_knowledge(
            fact_id=fact_id,
            new_fact=new_fact,
            category=category,
            scope=scope,
        )
        return {"updated": ok, "id": fact_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects")
async def list_projects():
    try:
        return {
            "defaultProjectId": storage.get_projects_registry().get("defaultProjectId"),
            "mainWorkspacePath": workspace_resolution_service.get_main_workspace_path(),
            "projects": [item.model_dump(by_alias=True, exclude_none=True) for item in project_registry_service.list_projects()],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects")
async def create_project(payload: ProjectDescriptorPayload):
    try:
        project = project_registry_service.save_project(payload.model_dump(by_alias=True, exclude_none=True))
        return project.model_dump(by_alias=True, exclude_none=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    try:
        project = project_registry_service.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return project.model_dump(by_alias=True, exclude_none=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/projects/{project_id}")
async def patch_project(project_id: str, updates: dict = Body(...)):
    try:
        project = project_registry_service.patch_project(project_id, updates)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return project.model_dump(by_alias=True, exclude_none=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    try:
        ok = project_registry_service.delete_project(project_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return {"status": "success", "projectId": project_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/bind-workspace")
async def bind_project_workspace(project_id: str, payload: WorkspaceBindingPayload):
    try:
        project = project_registry_service.bind_workspace(
            project_id=project_id,
            workspace_id=payload.workspace_id,
            workspace_path=payload.workspace_path,
            source=payload.source or "admin_selected",
            confidence=payload.confidence or 1.0,
        )
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return project.model_dump(by_alias=True, exclude_none=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/bind-channel")
async def bind_project_channel(project_id: str, payload: ChannelBindingPayload):
    try:
        project = project_registry_service.bind_channel(
            project_id=project_id,
            channel_type=payload.channel_type,
            remote_id=payload.remote_id,
            mode=payload.mode or "default",
        )
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return project.model_dump(by_alias=True, exclude_none=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/bind-workflow")
async def bind_project_workflow(project_id: str, payload: WorkflowBindingPayload):
    try:
        project = project_registry_service.bind_workflow(
            project_id=project_id,
            workflow_id=payload.workflow_id,
            mode=payload.mode or "default",
        )
        if project is None:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
        return project.model_dump(by_alias=True, exclude_none=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AdminChatRequest(BaseModel):
    message: str


@router.post("/memory/admin-chat")
async def memory_admin_chat(request: AdminChatRequest):
    from core.knowledge_db import knowledge_db
    from core.memory_router import MemoryRouter
    from langchain_core.messages import AIMessage as _AIMessage
    from langchain_core.messages import ToolMessage
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def search_knowledge(query: str, scope: str = "") -> str:
        results = memory_runtime.query_knowledge(query=query, scope=scope or None, limit=10)
        if not results:
            return f"No results for '{query}'."
        lines = [f"Found {len(results)} items:"]
        for result in results:
            lines.append(f"  [{result.get('scope','?')}] {result.get('fact','')} (id: {result.get('id','?')})")
        return "\n".join(lines)

    @lc_tool
    def get_knowledge_stats() -> str:
        try:
            summary = memory_runtime.get_preference_summary()
            prefs = summary["preferences"]
            total_prefs = summary["total"]
            pref_scopes = ", ".join(prefs.keys())
            knowledge_count = memory_runtime.get_knowledge_count()
            with knowledge_db._conn() as conn:
                scope_counts = conn.execute("SELECT scope, COUNT(*) FROM knowledge WHERE status = 'active' GROUP BY scope").fetchall()
                scope_breakdown = ", ".join(f"{row[0]}: {row[1]}" for row in scope_counts) if scope_counts else "None"
            return (
                f"Knowledge Base: {knowledge_count} total active entries. Breakdown by scope: {scope_breakdown}.\n"
                f"User Preferences: {total_prefs} total items across scopes: {pref_scopes}."
            )
        except Exception as e:
            return f"Error getting stats: {e}"

    @lc_tool
    def get_graph_stats() -> str:
        stats = memory_runtime.get_graph_stats()
        top = ", ".join(f"{entity['name']}({entity['degree']})" for entity in stats.get("top_entities", [])[:5])
        return f"Graph stats: {stats['entities']} entities, {stats['relations']} relations.\nTop entities: {top or 'none'}"

    @lc_tool
    def get_recent_logs(days: int = 2) -> str:
        return memory_runtime.get_recent_logs(days=days) or "No recent logs found."

    @lc_tool
    def query_entity(entity: str) -> str:
        relations = knowledge_db.query_entity(entity)
        if not relations:
            return f"No graph relations found for '{entity}'."
        lines = [f"Relations for '{entity}':"]
        for relation in relations:
            lines.append(f"  ({relation['subject']}) -[{relation['predicate']}]-> ({relation['object']})")
        return "\n".join(lines)

    @lc_tool
    def delete_graph_relation(subject: str, predicate: str, object: str) -> str:
        success = knowledge_db.delete_relation(subject, predicate, object)
        if success:
            return f"Successfully deleted relation: ({subject}) -[{predicate}]-> ({object})"
        return f"Relation not found or could not be deleted: ({subject}) -[{predicate}]-> ({object})"

    @lc_tool
    def delete_graph_entities(entities: Union[str, List[str]]) -> str:
        if isinstance(entities, str):
            entities = [entities]
        results = []
        for entity in entities:
            success = knowledge_db.delete_entity(entity)
            if success:
                results.append(f"Successfully deleted entity '{entity}' and all associated relations.")
            else:
                results.append(f"Entity '{entity}' not found or could not be deleted.")
        return "\n".join(results)

    @lc_tool
    def add_graph_relation(subject: str, predicate: str, object: str) -> str:
        knowledge_db.add_relation(subject, predicate, object)
        return f"Successfully added relation: ({subject}) -[{predicate}]-> ({object})"

    @lc_tool
    def add_graph_entity(entity_name: str, entity_type: str = "concept") -> str:
        knowledge_db.add_entity(entity_name, entity_type)
        return f"Successfully added entity '{entity_name}' of type '{entity_type}'."

    @lc_tool
    def add_knowledge(fact: str, category: str = "general", scope: str = "global") -> str:
        fact_id = memory_runtime.add_knowledge(fact=fact, category=category, scope=scope)
        return f"Successfully added new knowledge with ID: {fact_id}"

    @lc_tool
    def update_knowledge(fact_id: str, new_fact: str) -> str:
        memory_runtime.update_knowledge(fact_id=fact_id, new_fact=new_fact)
        return f"Successfully updated knowledge fact {fact_id}."

    @lc_tool
    def delete_knowledge(fact_id: str) -> str:
        memory_runtime.delete_knowledge(fact_id=fact_id)
        return f"Successfully deleted knowledge fact {fact_id}."

    @lc_tool
    def search_graph_entities(keyword: str, limit: int = 20) -> str:
        entities = knowledge_db.search_entities(keyword, limit)
        if not entities:
            return f"No entities found matching '{keyword}'."
        lines = [f"Found {len(entities)} entities matching '{keyword}':"]
        for entity in entities:
            lines.append(f"  - {entity['name']} (type: {entity['type']})")
        return "\n".join(lines)

    @lc_tool
    def get_isolated_entities(limit: int = 50) -> str:
        entities = knowledge_db.get_isolated_entities(limit)
        if not entities:
            return "No isolated entities found in the graph."
        lines = [f"Found {len(entities)} isolated entities:"]
        for entity in entities:
            lines.append(f"  - {entity['name']} (type: {entity['type']})")
        return "\n".join(lines)

    admin_tools = [
        search_knowledge,
        get_knowledge_stats,
        get_graph_stats,
        get_recent_logs,
        query_entity,
        search_graph_entities,
        get_isolated_entities,
        delete_graph_relation,
        delete_graph_entities,
        add_graph_relation,
        add_graph_entity,
        add_knowledge,
        update_knowledge,
        delete_knowledge,
    ]

    try:
        router_instance = MemoryRouter()
        base_llm = router_instance.get_extractor_llm()
        llm = base_llm.bind_tools(admin_tools)
        raw_llm = base_llm
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LLM init failed: {e}")

    system_prompt = render_memory_admin_chat_prompt()

    async def stream_admin_chat():
        import re as _re

        try:
            messages = [SystemMessage(content=system_prompt), HumanMessage(content=request.message)]
            tool_map = {tool.name: tool for tool in admin_tools}
            text_filter = StreamFilter(["NONE", "None", "null", "```json", "```"])

            for _ in range(10):
                response = await asyncio.get_event_loop().run_in_executor(None, lambda: llm.invoke(messages))
                parsed_tool_calls = normalize_tool_calls(getattr(response, "tool_calls", []))
                is_native_tool_call = bool(parsed_tool_calls)

                if not parsed_tool_calls and isinstance(response.content, str) and ("DSML" in response.content or "invoke name=" in response.content):
                    matches = _re.finditer(r'invoke\s+name="([^"]+)"\s*>(.*?)</\s*\|?[ \t]*DSML', response.content, _re.DOTALL)
                    for match in matches:
                        tool_name = match.group(1).strip()
                        tool_args_str = match.group(2).strip()
                        tool_args = {}
                        if tool_args_str:
                            try:
                                tool_args = json.loads(tool_args_str)
                            except Exception:
                                pass
                        parsed_tool_calls.append({"name": tool_name, "args": tool_args, "id": f"call_{uuid.uuid4().hex[:12]}"})
                    if not parsed_tool_calls:
                        for match in _re.finditer(r'invoke\s+name="([^"]+)"', response.content):
                            parsed_tool_calls.append({"name": match.group(1).strip(), "args": {}, "id": f"call_{uuid.uuid4().hex[:12]}"})

                if not parsed_tool_calls:
                    break

                _, reasoning_text = extract_text_and_reasoning(response)
                if reasoning_text:
                    yield format_ndjson({"type": "thinking", "token": reasoning_text + "\n"})

                if is_native_tool_call:
                    messages.append(response)
                else:
                    messages.append(
                        _AIMessage(
                            content="",
                            tool_calls=[{"name": call["name"], "args": call["args"], "id": call["id"]} for call in parsed_tool_calls],
                        )
                    )

                for tool_call in parsed_tool_calls:
                    tool_name = tool_call.get("name")
                    tool_args = tool_call.get("args", {})
                    tool_call_id = tool_call.get("id", f"call_{uuid.uuid4().hex[:12]}")
                    yield format_ndjson({"type": "tool_call", "name": tool_name, "arguments": tool_args})

                    if tool_name in tool_map:
                        try:
                            result = await asyncio.get_event_loop().run_in_executor(
                                None,
                                lambda current_tool=tool_map[tool_name], args=tool_args: current_tool.invoke(args),
                            )
                        except Exception as tool_exc:
                            result = f"Error: {tool_exc}"
                    else:
                        result = f"Unknown tool: {tool_name}"

                    result_str = str(result)
                    yield format_ndjson({"type": "tool_result", "name": tool_name, "result": result_str})
                    messages.append(ToolMessage(content=result_str, tool_call_id=tool_call_id))

            async for chunk in raw_llm.astream(messages):
                token = chunk.content
                if isinstance(token, str):
                    filtered_token = text_filter.process(token)
                    if filtered_token:
                        yield format_ndjson({"token": filtered_token})

            final_token = text_filter.flush()
            if final_token:
                yield format_ndjson({"token": final_token})
        except Exception as e:
            import traceback

            traceback.print_exc()
            yield format_ndjson({"error": str(e)})

    return StreamingResponse(stream_admin_chat(), media_type="application/x-ndjson")
