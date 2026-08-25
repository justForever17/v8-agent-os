from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import AsyncIterator, Any

from api.models import EngineConfig
from graph.supervisor import AgentState, create_supervisor_graph
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from erc.checkpoint_store import checkpoint_store


@dataclass(slots=True)
class SupervisorExecutionBundle:
    graph: object
    payload: object
    graph_config: dict
    mode: str = "start"
    diagnostics: dict[str, Any] | None = None


class SupervisorAgentRunner:
    """
    Phase 2 过渡层：
    先把 Supervisor Graph 的构建入口收敛成 Runner，
    后续再逐步迁出运行时职责。
    """

    def __init__(self) -> None:
        self._graph_cache: dict[str, object] = {}
        self._thread_lock = threading.Lock()
        self._graph_cache_locks: dict[int, asyncio.Lock] = {}

    def _graph_signature(self, config: EngineConfig) -> str:
        payload = config.model_dump(mode="json", by_alias=True)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    async def build_graph(self, config: EngineConfig):
        signature = self._graph_signature(config)
        loop_id = id(asyncio.get_running_loop())
        cache_key = f"{loop_id}:{signature}"
        started_at = asyncio.get_running_loop().time()
        with self._thread_lock:
            graph_lock = self._graph_cache_locks.get(loop_id)
            if graph_lock is None:
                graph_lock = asyncio.Lock()
                self._graph_cache_locks[loop_id] = graph_lock
        async with graph_lock:
            cached = self._graph_cache.get(cache_key)
            if cached is not None:
                return cached, {"graphCacheHit": True, "graphBuildMs": round((asyncio.get_running_loop().time() - started_at) * 1000, 2)}
            checkpointer = await checkpoint_store.get_async_sqlite_saver()
            graph = create_supervisor_graph(config, checkpointer=checkpointer)
            self._graph_cache[cache_key] = graph
            return graph, {"graphCacheHit": False, "graphBuildMs": round((asyncio.get_running_loop().time() - started_at) * 1000, 2)}

    def runtime_metadata(self) -> dict[str, str | bool]:
        return {
            "runner": "SupervisorAgentRunner",
            "checkpoint_backend": "sqlite_async",
            "checkpoint_deserialization": "msgpack_strict",
            "checkpoint_write_contract": True,
            "checkpoint_encryption": "aes_256_gcm",
            "checkpoint_delta_channel": True,
            "checkpoint_replay_governance": True,
            "supports_checkpoint_resume": True,
            "supports_graph_interrupt": True,
        }

    def create_state(
        self,
        messages,
        *,
        current_route_context: dict[str, Any] | None = None,
        runtime_dispatch_status: dict[str, Any] | None = None,
        engineering_context: dict[str, Any] | None = None,
        task_shape_hint: dict[str, Any] | None = None,
        explicit_subagent_families: list[str] | None = None,
        context_mentions: list[dict[str, Any]] | None = None,
        context_session_refs: list[dict[str, Any]] | None = None,
        session_coordination: dict[str, Any] | None = None,
        transport: str | None = None,
    ):
        state = AgentState(messages=messages)
        if transport:
            state["transport"] = transport
        if isinstance(current_route_context, dict) and current_route_context:
            state["current_route_context"] = dict(current_route_context)
            identity_pairs = (
                ("session_id", "session_id"),
                ("sessionId", "sessionId"),
                ("run_id", "run_id"),
                ("runId", "runId"),
                ("workspace_path", "workspace_path"),
                ("workspacePath", "workspacePath"),
                ("workspace_id", "workspace_id"),
                ("workspaceId", "workspaceId"),
                ("project_id", "project_id"),
                ("projectId", "projectId"),
                ("resolved_scope", "resolved_scope"),
                ("resolvedScope", "resolvedScope"),
                ("safety_approval_mode", "safety_approval_mode"),
                ("safetyApprovalMode", "safetyApprovalMode"),
                ("original_workspace_path", "original_workspace_path"),
                ("originalWorkspacePath", "originalWorkspacePath"),
                ("repository_root", "repository_root"),
                ("repositoryRoot", "repositoryRoot"),
                ("worktree_root", "worktree_root"),
                ("worktreeRoot", "worktreeRoot"),
                ("worktree_id", "worktree_id"),
                ("worktreeId", "worktreeId"),
                ("sandbox_lease_id", "sandbox_lease_id"),
                ("sandboxLeaseId", "sandboxLeaseId"),
                ("sandbox_policy", "sandbox_policy"),
                ("sandbox_policy_digest", "sandbox_policy_digest"),
                ("sandbox_policy_file", "sandbox_policy_file"),
                ("sandbox_capabilities", "sandbox_capabilities"),
                ("managed_engineering_execution", "managed_engineering_execution"),
            )
            for source_key, state_key in identity_pairs:
                value = current_route_context.get(source_key)
                if value:
                    state[state_key] = value
        if isinstance(runtime_dispatch_status, dict):
            state["runtime_dispatch_status"] = dict(runtime_dispatch_status)
        if isinstance(engineering_context, dict) and engineering_context:
            state["engineering_context"] = engineering_context
        if isinstance(task_shape_hint, dict) and task_shape_hint:
            state["task_shape_hint"] = task_shape_hint
        if explicit_subagent_families:
            state["explicit_subagent_families"] = list(explicit_subagent_families)
        if context_mentions:
            state["context_mentions"] = list(context_mentions)
        if context_session_refs:
            state["context_session_refs"] = list(context_session_refs)
            state["contextSessionRefs"] = list(context_session_refs)
        if isinstance(session_coordination, dict) and session_coordination:
            state["session_coordination"] = dict(session_coordination)
            state["sessionCoordination"] = dict(session_coordination)
        return state

    def build_graph_config(self, session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}}

    @staticmethod
    def _message_id(message: Any) -> str:
        if isinstance(message, dict):
            return str(message.get("id") or "").strip()
        return str(getattr(message, "id", "") or "").strip()

    @staticmethod
    def _message_kwargs(message: Any) -> dict[str, Any]:
        if isinstance(message, dict):
            value = message.get("additional_kwargs") or message.get("additionalKwargs") or {}
        else:
            value = getattr(message, "additional_kwargs", {}) or {}
        return dict(value) if isinstance(value, dict) else {}

    async def _reconcile_persistent_input(
        self,
        *,
        graph: Any,
        graph_config: dict[str, Any],
        messages: Any,
    ) -> tuple[list[Any], dict[str, Any]]:
        incoming = list(messages or [])
        getter = getattr(graph, "aget_state", None)
        if not callable(getter):
            return incoming, {"persistentInputReconciled": False, "reason": "state_reader_unavailable"}
        snapshot = await getter(graph_config)
        values = getattr(snapshot, "values", None)
        existing = list((values or {}).get("messages") or []) if isinstance(values, dict) else []
        if not existing:
            return incoming, {"persistentInputReconciled": False, "reason": "new_thread"}

        existing_ids = {self._message_id(message) for message in existing if self._message_id(message)}
        latest_ingress_human_index = -1
        for index, message in enumerate(incoming):
            kwargs = self._message_kwargs(message)
            if kwargs.get("v8_ingress_history") and isinstance(message, HumanMessage):
                latest_ingress_human_index = index

        reconciled: list[Any] = []
        dropped = 0
        for index, message in enumerate(incoming):
            kwargs = self._message_kwargs(message)
            if not kwargs.get("v8_ingress_history"):
                reconciled.append(message)
                continue
            message_id = self._message_id(message)
            keep_latest_human = index == latest_ingress_human_index and (
                not message_id or message_id not in existing_ids
            )
            if keep_latest_human:
                reconciled.append(message)
            else:
                dropped += 1
        return reconciled, {
            "persistentInputReconciled": True,
            "existingMessageCount": len(existing),
            "incomingMessageCount": len(incoming),
            "reconciledMessageCount": len(reconciled),
            "droppedHistoricalMessageCount": dropped,
        }

    async def create_execution_bundle(
        self,
        *,
        config: EngineConfig,
        messages,
        session_id: str,
        current_route_context: dict[str, Any] | None = None,
        runtime_dispatch_status: dict[str, Any] | None = None,
        engineering_context: dict[str, Any] | None = None,
        task_shape_hint: dict[str, Any] | None = None,
        explicit_subagent_families: list[str] | None = None,
        context_mentions: list[dict[str, Any]] | None = None,
        context_session_refs: list[dict[str, Any]] | None = None,
        session_coordination: dict[str, Any] | None = None,
        transport: str | None = None,
    ):
        graph, diagnostics = await self.build_graph(config)
        graph_config = self.build_graph_config(session_id)
        reconciled_messages, reconciliation = await self._reconcile_persistent_input(
            graph=graph,
            graph_config=graph_config,
            messages=messages,
        )
        return SupervisorExecutionBundle(
            graph=graph,
            payload=self.create_state(
                reconciled_messages,
                current_route_context=current_route_context,
                runtime_dispatch_status=runtime_dispatch_status,
                engineering_context=engineering_context,
                task_shape_hint=task_shape_hint,
                explicit_subagent_families=explicit_subagent_families,
                context_mentions=context_mentions,
                context_session_refs=context_session_refs,
                session_coordination=session_coordination,
                transport=transport,
            ),
            graph_config=graph_config,
            mode="start",
            diagnostics={**dict(diagnostics or {}), **reconciliation},
        )

    def build_resume_input(self, resume_value):
        return Command(resume=resume_value)

    async def create_resume_bundle(self, *, config: EngineConfig, session_id: str, resume_value):
        graph, diagnostics = await self.build_graph(config)
        return SupervisorExecutionBundle(
            graph=graph,
            payload=self.build_resume_input(resume_value),
            graph_config=self.build_graph_config(session_id),
            mode="resume",
            diagnostics=diagnostics,
        )

    def open_bundle_stream(self, bundle: SupervisorExecutionBundle):
        return bundle.graph.astream_events(bundle.payload, config=bundle.graph_config, version="v2")

    async def get_state_snapshot(self, bundle: SupervisorExecutionBundle) -> dict[str, Any] | None:
        getter = getattr(bundle.graph, "aget_state", None)
        if not callable(getter):
            return None
        snapshot = await getter(bundle.graph_config)
        values = getattr(snapshot, "values", None)
        return dict(values or {}) if isinstance(values, dict) else None

    async def stream_events(self, bundle: SupervisorExecutionBundle) -> AsyncIterator[dict[str, Any]]:
        async for event in self.open_bundle_stream(bundle):
            yield event


supervisor_runner = SupervisorAgentRunner()
