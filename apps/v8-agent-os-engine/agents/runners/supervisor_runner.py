from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Any

from api.models import EngineConfig
from graph.supervisor import AgentState, create_supervisor_graph
from langgraph.types import Command

from erc.checkpoint_store import checkpoint_store


@dataclass(slots=True)
class SupervisorExecutionBundle:
    graph: object
    payload: object
    graph_config: dict
    mode: str = "start"


class SupervisorAgentRunner:
    """
    Phase 2 过渡层：
    先把 Supervisor Graph 的构建入口收敛成 Runner，
    后续再逐步迁出运行时职责。
    """

    async def build_graph(self, config: EngineConfig):
        checkpointer = await checkpoint_store.get_async_sqlite_saver()
        return create_supervisor_graph(config, checkpointer=checkpointer)

    def runtime_metadata(self) -> dict[str, str | bool]:
        return {
            "runner": "SupervisorAgentRunner",
            "checkpoint_backend": "sqlite_async",
            "supports_checkpoint_resume": True,
            "supports_graph_interrupt": True,
        }

    def create_state(self, messages):
        return AgentState(messages=messages)

    def build_graph_config(self, session_id: str, *, recursion_limit: int) -> dict:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": recursion_limit}

    async def create_execution_bundle(self, *, config: EngineConfig, messages, session_id: str, recursion_limit: int):
        graph = await self.build_graph(config)
        return SupervisorExecutionBundle(
            graph=graph,
            payload=self.create_state(messages),
            graph_config=self.build_graph_config(session_id, recursion_limit=recursion_limit),
            mode="start",
        )

    def build_resume_input(self, resume_value):
        return Command(resume=resume_value)

    async def create_resume_bundle(self, *, config: EngineConfig, session_id: str, resume_value, recursion_limit: int):
        graph = await self.build_graph(config)
        return SupervisorExecutionBundle(
            graph=graph,
            payload=self.build_resume_input(resume_value),
            graph_config=self.build_graph_config(session_id, recursion_limit=recursion_limit),
            mode="resume",
        )

    def open_bundle_stream(self, bundle: SupervisorExecutionBundle):
        return bundle.graph.astream_events(bundle.payload, config=bundle.graph_config, version="v2")

    async def stream_events(self, bundle: SupervisorExecutionBundle) -> AsyncIterator[dict[str, Any]]:
        async for event in self.open_bundle_stream(bundle):
            yield event


supervisor_runner = SupervisorAgentRunner()
