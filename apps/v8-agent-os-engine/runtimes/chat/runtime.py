from __future__ import annotations

import asyncio
import os
import uuid
import logging
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from api.models import ChatRequest
from agents.runners.supervisor_runner import SupervisorExecutionBundle, supervisor_runner
from core.system_tools.command_presets import read_command_preset
from core.chat_output_extractor import extract_text_and_reasoning
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.models.provider_compatibility import normalize_provider_error
from core.database import db
from core.engine_config_resolver import resolve_engine_config_for_role
from core.graph_stream_watchdog import GraphStreamWatchdogState, next_graph_stream_event
from core.realtime_protocol import protocol_connected_event
from core.stream_chunk_aggregator import TextChunkAggregator
from core.storage import storage
from core.context.workspace import workspace_resolution_service
from erc.kernel import erc_kernel
from erc.models import RuntimeSource
from erc.runtime_registry import runtime_registry
from erc.runtime_context import bind_runtime_context
from erc.runtime_stability import runtime_stability_service
from erc.run_service import run_service
from erc.session_admission_service import session_admission_service
from erc.safety_guardian import safety_guardian
from erc.workflow_ledger import workflow_ledger_service
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from runtimes.memory.scope_resolution import (
    scope_resolution_service,
    session_scope_binding_service,
)


class StreamFilter:
    """
    仅在流开头抑制常见空输出/JSON 围栏碎片，
    一旦出现有效正文就立刻透传后续内容。
    """

    def __init__(self, bad_words: list[str]):
        self.bad_words = bad_words
        self.buffer = ""
        self.flushed = False

    def process(self, chunk: str) -> str:
        if self.flushed:
            return chunk

        self.buffer += chunk
        if any(item == self.buffer for item in self.bad_words):
            return ""
        if any(item.startswith(self.buffer) for item in self.bad_words):
            return ""
        self.flushed = True
        return self.buffer

    def flush(self) -> str:
        if self.flushed or not self.buffer:
            return ""
        if any(item == self.buffer for item in self.bad_words):
            return ""
        self.flushed = True
        return self.buffer


@dataclass(slots=True)
class ChatExecutionBundle:
    run_handle: Any
    runner_bundle: SupervisorExecutionBundle

    @property
    def graph(self):
        return self.runner_bundle.graph

    @property
    def payload(self):
        return self.runner_bundle.payload

    @property
    def graph_config(self) -> dict:
        return self.runner_bundle.graph_config


@dataclass(slots=True)
class ChatPreparedRequest:
    request: ChatRequest
    lc_messages: list[Any]
    session_id: str
    conversation_id: str
    user_id: str
    is_resume_request: bool
    latest_user_content: str
    command_preset_name: str | None = None
    command_preset_hash: str | None = None
    task_planning_mode: bool = False


@dataclass(slots=True)
class ChatRunContext:
    prepared: ChatPreparedRequest
    run_handle: Any
    scope_result: Any
    transport: str
    existing_binding: Any | None
    preflight_decision: Any

    @property
    def active_run_id(self) -> str:
        return self.run_handle.run_id

    @property
    def request(self) -> ChatRequest:
        return self.prepared.request

    @property
    def session_id(self) -> str:
        return self.prepared.session_id

    @property
    def conversation_id(self) -> str:
        return self.prepared.conversation_id

    @property
    def user_id(self) -> str:
        return self.prepared.user_id

    @property
    def lc_messages(self) -> list[Any]:
        return self.prepared.lc_messages

    @property
    def is_resume_request(self) -> bool:
        return self.prepared.is_resume_request

    def emit_runtime_event(
        self,
        topic: str,
        payload: dict,
        *,
        kind: str = "event",
        agent_id: str | None = "supervisor",
        node: str | None = None,
    ) -> dict:
        return self.run_handle.emit(
            topic,
            payload,
            kind=kind,
            source=RuntimeSource(
                plane="engine",
                component="chat_runtime",
                node=node or agent_id or "system",
                agent_id=agent_id,
            ),
        )


@dataclass(slots=True)
class ChatStreamState:
    current_agent: str = "supervisor"
    output_buffer: list[str] = field(default_factory=list)
    reasoning_buffer: list[str] = field(default_factory=list)
    tool_calls_buffer: list[dict[str, Any]] = field(default_factory=list)
    streamed_model_run_ids: set[str] = field(default_factory=set)
    watchdog: GraphStreamWatchdogState = field(default_factory=GraphStreamWatchdogState)
    interrupted_signal: dict[str, Any] | None = None
    valid_agent_node_names: list[str] = field(default_factory=list)
    text_filter: StreamFilter = field(default_factory=lambda: StreamFilter(["NONE", "None", "null", "```json", "```"]))
    text_aggregator: TextChunkAggregator = field(default_factory=TextChunkAggregator)


class ChatRuntime:
    """
    Phase 2 运行时层：
    把聊天请求的生命周期准备、run 启动、scope 绑定、输入落库、
    graph 执行包构建逐步从 routes.py 收口到 ChatRuntime。
    """

    kind = "chat"

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "ChatRuntime",
            "summary": "负责用户会话主流程、Supervisor 编排、暂停/恢复与 durable 对话投影。",
            "responsibilities": [
                "创建与恢复聊天 run",
                "驱动 Supervisor Graph 执行",
                "把输入、流式输出和中断状态同步到账本与投影",
            ],
            "routingKeywords": ["聊天", "复杂协作", "任务拆解", "审批", "暂停", "恢复"],
            "acceptedInputs": ["ChatRequest", "resume_run_id", "tool_outputs"],
            "producedOutputs": ["chat_projection", "runtime_events", "workflow_steps"],
            "ownedSteps": ["chat.main", "chat.supervisor_graph"],
            "supportsPause": True,
            "supportsResume": True,
            "supportsApproval": True,
            "supportsRepair": True,
            "visibility": "primary",
            "promptHints": [
                "开放式请求、跨模块任务和需要 Supervisor 编排的流程，优先走 ChatRuntime。",
                "不要把所有模块细节塞进 Supervisor；先看能力卡片，再决定是否切给专门 Runtime。",
            ],
            "capabilities": [
                {
                    "key": "chat.orchestrate",
                    "label": "多步骤对话编排",
                    "summary": "负责意图理解、任务拆解、审批和恢复。",
                    "accepts": ["自然语言请求", "人工回复", "恢复指令"],
                    "outputs": ["流程状态", "聊天投影"],
                    "examples": ["跨模块任务协调", "需要人工确认的长流程"],
                    "risk_level": "medium",
                }
            ],
        }

    def _get_agent_profile(self, agent_id: str) -> dict[str, str]:
        return storage.get_agent_runtime_profile(agent_id)

    def _extract_interrupt_request(self, chunk: dict | None) -> dict | None:
        if not isinstance(chunk, dict):
            return None
        raw_interrupts = chunk.get("__interrupt__")
        if not raw_interrupts:
            return None

        first_interrupt = raw_interrupts[0] if isinstance(raw_interrupts, (list, tuple)) else raw_interrupts
        payload = getattr(first_interrupt, "value", first_interrupt)
        interrupt_id = getattr(first_interrupt, "id", None)
        if isinstance(first_interrupt, dict):
            payload = first_interrupt.get("value", payload)
            interrupt_id = first_interrupt.get("id", interrupt_id)
        if not isinstance(payload, dict):
            payload = {"question": str(payload)}

        question = payload.get("question") or payload.get("prompt") or "我需要您的输入以继续执行任务。"
        tool_call_id = payload.get("toolCallId") or payload.get("tool_call_id")
        approval_kind = payload.get("approvalKind") or payload.get("approval_kind") or "human_input_required"
        interaction_kind = payload.get("interactionKind") or payload.get("interaction_kind") or "approval"
        request_payload = dict(payload)
        request_payload["question"] = question
        request_payload["prompt"] = question
        request_payload["approvalKind"] = approval_kind
        request_payload["interactionKind"] = interaction_kind
        if tool_call_id:
            request_payload["toolCallId"] = tool_call_id
        if interrupt_id:
            request_payload["interruptId"] = interrupt_id
        return request_payload

    def _to_langchain_messages(self, request: ChatRequest) -> list[Any]:
        lc_messages: list[Any] = []
        for message in request.messages:
            if message.role == "user":
                lc_messages.append(HumanMessage(content=message.content))
            elif message.role == "assistant":
                lc_messages.append(AIMessage(content=message.content))
            elif message.role == "system":
                lc_messages.append(SystemMessage(content=message.content))
            elif message.role == "tool":
                lc_messages.append(
                    ToolMessage(
                        content=message.content,
                        tool_call_id=message.tool_call_id,
                        name=message.name or "unknown",
                    )
                )

        if request.tool_outputs:
            for tool_output in request.tool_outputs:
                lc_messages.append(
                    ToolMessage(
                        content=tool_output.output,
                        tool_call_id=tool_output.tool_call_id,
                        name=tool_output.name or "ask_user",
                    )
                )
        return lc_messages

    def _inject_uploaded_file_notices(self, request: ChatRequest, lc_messages: list[Any]) -> None:
        if not request.fileUrls:
            return

        local_files: list[str] = []
        for url in request.fileUrls:
            if "/api/workspace/files/" in url:
                subpath = url.split("/api/workspace/files/")[-1]
                workspace_dir = workspace_resolution_service.resolve_workspace_path(
                    runtime_kind="chat",
                    session_id=request.conversationId or request.session_id,
                    explicit_workspace_path=request.workspace_path,
                )
                local_path = Path(workspace_dir) / subpath
                local_files.append(str(local_path.absolute().resolve()))
            else:
                local_files.append(url)

        file_notices = "\n\n" + "\n".join([f"[User uploaded file: {path}]" for path in local_files])
        for message in reversed(lc_messages):
            if isinstance(message, HumanMessage):
                if isinstance(message.content, str):
                    message.content += file_notices
                elif isinstance(message.content, list):
                    message.content.append({"type": "text", "text": file_notices})
                break

    def _latest_user_content(self, request: ChatRequest) -> str:
        for candidate in reversed(request.messages):
            if candidate.role == "user" and candidate.content:
                return candidate.content
        return ""

    def _resolve_command_context(self, request: ChatRequest) -> tuple[dict[str, Any] | None, bool]:
        request_data = request.data
        command_selection = request_data.command_preset if request_data else None
        task_planning_mode = bool(request_data.task_planning_mode) if request_data else False

        command_preset = None
        if command_selection and command_selection.name:
            command_preset = read_command_preset(command_selection.name)
            if not command_preset:
                raise RuntimeError(f"Command preset '{command_selection.name}' does not exist.")

        return command_preset, task_planning_mode

    def _inject_command_and_task_mode_context(
        self,
        lc_messages: list[Any],
        *,
        command_preset: dict[str, Any] | None,
        task_planning_mode: bool,
    ) -> None:
        if not command_preset and not task_planning_mode:
            return

        for message in reversed(lc_messages):
            if not isinstance(message, HumanMessage):
                continue
            if not isinstance(message.content, str):
                continue

            wrapped_sections: list[str] = []
            if command_preset:
                wrapped_sections.append(
                    "\n".join(
                        [
                            f"[COMMAND PRESET: {command_preset.get('name') or 'unknown'}]",
                            str(command_preset.get("content") or "").strip(),
                            "[/COMMAND PRESET]",
                        ]
                    )
                )
            if task_planning_mode:
                wrapped_sections.append(
                    "\n".join(
                        [
                            "[TASK PLANNING MODE]",
                            "请把本轮请求视为任务执行请求，优先给出结构化计划、执行步骤、风险与依赖。",
                            "[/TASK PLANNING MODE]",
                        ]
                    )
                )

            user_content = str(message.content or "").strip()
            wrapped_sections.append(
                "\n".join(
                    [
                        "[USER REQUEST]",
                        user_content or "用户未补充额外文字，仅选择了命令预设或任务模式。",
                        "[/USER REQUEST]",
                    ]
                )
            )
            message.content = "\n\n".join(section for section in wrapped_sections if section.strip())
            return

    def _attach_scope_context(self, lc_messages: list[Any], *, session_id: str, user_id: str, scope_result: Any) -> None:
        scope_payload = {
            "session_id": session_id,
            "user_id": user_id,
            "project_id": scope_result.binding.project_id,
            "workspace_id": scope_result.binding.workspace_id,
            "workspace_path": scope_result.binding.workspace_path,
            "workflow_id": scope_result.binding.workflow_id,
            "channel_type": scope_result.binding.channel_type,
            "channel_remote_id": scope_result.binding.channel_remote_id,
            "resolved_scope": scope_result.binding.resolved_scope,
            "scope_source": scope_result.binding.scope_source,
            "scope_chain": scope_result.scope_chain,
        }
        for message in reversed(lc_messages):
            if isinstance(message, HumanMessage):
                existing = dict(getattr(message, "additional_kwargs", {}) or {})
                existing.update(scope_payload)
                message.additional_kwargs = existing
                break

    def _scope_event_payload(self, result: Any) -> dict:
        binding = result.binding
        return {
            "session_id": binding.session_id,
            "conversation_id": binding.conversation_id,
            "project_id": binding.project_id,
            "workspace_id": binding.workspace_id,
            "workspace_path": binding.workspace_path,
            "workflow_id": binding.workflow_id,
            "channel_type": binding.channel_type,
            "channel_remote_id": binding.channel_remote_id,
            "resolved_scope": binding.resolved_scope,
            "scope_source": binding.scope_source,
            "scope_confidence": binding.scope_confidence,
            "scope_chain": result.scope_chain,
            "requested_scope": result.requested_scope,
            "reused_existing_binding": result.reused_existing_binding,
        }

    def _resolve_engine_config(self, request: ChatRequest) -> None:
        if request.config.provider == "openai" and request.config.model_name == "gpt-4o":
            resolved = resolve_engine_config_for_role(
                "supervisor",
                fallback_provider=request.config.provider,
                fallback_model=request.config.model_name,
                require_explicit=True,
            )
            if resolved["resolution"].get("bindingState") != "explicit":
                return
            role_engine_config = resolved["engine_config"]
            request.config.provider = role_engine_config.provider
            request.config.model_name = role_engine_config.model_name
            request.config.api_key = role_engine_config.api_key
            request.config.base_url = role_engine_config.base_url

    def prepare_request(self, request: ChatRequest) -> ChatPreparedRequest:
        session_id = request.session_id or str(uuid.uuid4())
        conversation_id = request.conversation_id or session_id
        user_id = request.user_id or "anonymous"
        lc_messages = self._to_langchain_messages(request)
        self._inject_uploaded_file_notices(request, lc_messages)
        command_preset, task_planning_mode = self._resolve_command_context(request)
        self._inject_command_and_task_mode_context(
            lc_messages,
            command_preset=command_preset,
            task_planning_mode=task_planning_mode,
        )

        request.session_id = session_id
        request.conversation_id = conversation_id
        request.user_id = user_id
        self._resolve_engine_config(request)

        return ChatPreparedRequest(
            request=request,
            lc_messages=lc_messages,
            session_id=session_id,
            conversation_id=conversation_id,
            user_id=user_id,
            is_resume_request=bool(request.resume_run_id),
            latest_user_content=self._latest_user_content(request),
            command_preset_name=(str(command_preset.get("name") or "").strip() or None) if command_preset else None,
            command_preset_hash=(str(command_preset.get("contentHash") or "").strip() or None) if command_preset else None,
            task_planning_mode=task_planning_mode,
        )

    def begin_run(
        self,
        *,
        session_id: str,
        conversation_id: str,
        user_id: str,
        transport: str,
        provider: str,
        model_name: str,
        run_id: str | None = None,
    ):
        return erc_kernel.submit_run(
            session_id=session_id,
            conversation_id=conversation_id,
            user_id=user_id,
            runtime_kind="chat",
            trigger_source=transport,
            agent_id="supervisor",
            metadata={
                "provider": provider,
                "model": model_name,
                "runtime": "chat",
                **supervisor_runner.runtime_metadata(),
            },
            run_id=run_id,
            initial_status="queued",
            component="chat_runtime",
            node="run_manager",
        )

    def attach_run(self, run_id: str):
        return erc_kernel.attach_run(run_id, component="chat_runtime", node="resume_manager")

    def prepare_run_context(
        self,
        request: ChatRequest,
        *,
        transport: str,
        run_id: str | None = None,
    ) -> ChatRunContext:
        prepared = self.prepare_request(request)
        run_handle = None

        if prepared.is_resume_request:
            run_handle = self.attach_run(prepared.request.resume_run_id or "")
            if run_handle is None:
                raise RuntimeError(f"Run '{prepared.request.resume_run_id}' does not exist or cannot be resumed.")
            prepared.session_id = run_handle.session_id
            prepared.conversation_id = run_handle.descriptor.conversation_id or run_handle.session_id
            prepared.user_id = run_handle.descriptor.user_id or prepared.user_id
            prepared.request.session_id = prepared.session_id
            prepared.request.conversation_id = prepared.conversation_id
            prepared.request.user_id = prepared.user_id
        else:
            title_source = prepared.latest_user_content or (prepared.request.messages[0].content if prepared.request.messages else "New Chat")
            title = f"{title_source[:50]}..." if len(title_source) > 50 else (title_source or "New Chat")
            db.create_or_update_session(
                session_id=prepared.session_id,
                title=title,
                user_id=prepared.user_id,
                metadata={
                    "model": prepared.request.config.model_name,
                    "provider": prepared.request.config.provider,
                    "conversation_id": prepared.conversation_id,
                },
            )
            run_handle = self.begin_run(
                session_id=prepared.session_id,
                conversation_id=prepared.conversation_id,
                user_id=prepared.user_id,
                transport=transport,
                provider=prepared.request.config.provider,
                model_name=prepared.request.config.model_name,
                run_id=run_id,
            )

        existing_binding = session_scope_binding_service.get_binding(prepared.session_id)
        scope_result = scope_resolution_service.resolve(
            session_id=prepared.session_id,
            conversation_id=prepared.conversation_id,
            user_id=prepared.user_id,
            user_query=prepared.latest_user_content,
            project_id=prepared.request.project_id,
            workspace_id=prepared.request.workspace_id,
            workspace_path=prepared.request.workspace_path,
            scope_hint=prepared.request.scope_hint,
            scope_mode=prepared.request.scope_mode,
            run_id=run_handle.run_id,
        )
        self._attach_scope_context(
            prepared.lc_messages,
            session_id=prepared.session_id,
            user_id=prepared.user_id,
            scope_result=scope_result,
        )
        preflight_decision = safety_guardian.preflight_runtime(
            runtime_kind="chat",
            trigger_source=transport,
            session_id=prepared.session_id,
            run_id=run_handle.run_id,
            resolved_scope=scope_result.binding.resolved_scope,
            user_id=prepared.user_id,
        )
        return ChatRunContext(
            prepared=prepared,
            run_handle=run_handle,
            scope_result=scope_result,
            transport=transport,
            existing_binding=existing_binding,
            preflight_decision=preflight_decision,
        )

    def emit_lifecycle_start_events(self, chat_run: ChatRunContext) -> None:
        workflow_ledger_service.activate_runtime_step(
            chat_run.active_run_id,
            owner_runtime="chat",
            step_key="chat.supervisor_graph",
            title="Supervisor 编排主流程",
            owner_agent_id="supervisor",
            input_payload={
                "transport": chat_run.transport,
                "resolved_scope": chat_run.scope_result.binding.resolved_scope,
            },
        )
        chat_run.emit_runtime_event(
            "safety.preflight.checked",
            chat_run.preflight_decision.to_payload(),
            agent_id=None,
            node="safety_guardian",
        )

        if chat_run.is_resume_request:
            chat_run.emit_runtime_event(
                "run.execution.resumed",
                {
                    "run_id": chat_run.active_run_id,
                    "transport": chat_run.transport,
                    "resume_value": chat_run.request.resume_value or {},
                    "resolved_scope": chat_run.scope_result.binding.resolved_scope,
                },
                agent_id=None,
                node="resume_manager",
            )
            chat_run.run_handle.transition("running", reason=chat_run.transport, node="resume_manager")
        else:
            chat_run.emit_runtime_event(
                "run.created",
                {
                    "run_id": chat_run.active_run_id,
                    "transport": chat_run.transport,
                    "user_id": chat_run.user_id,
                    "project_id": chat_run.scope_result.binding.project_id,
                    "resolved_scope": chat_run.scope_result.binding.resolved_scope,
                },
                node="run_manager",
            )
            chat_run.run_handle.transition("running", reason=chat_run.transport, node="run_manager")

        if not chat_run.scope_result.reused_existing_binding:
            chat_run.emit_runtime_event(
                "scope.binding.updated" if chat_run.existing_binding else "scope.binding.created",
                self._scope_event_payload(chat_run.scope_result),
                agent_id=None,
                node="scope_resolution",
            )

    def record_request_inputs(self, chat_run: ChatRunContext) -> None:
        request = chat_run.request
        metadata = {
            "run_id": chat_run.active_run_id,
            "transport": chat_run.transport,
            "project_id": chat_run.scope_result.binding.project_id,
            "workspace_id": chat_run.scope_result.binding.workspace_id,
            "resolved_scope": chat_run.scope_result.binding.resolved_scope,
        }
        if chat_run.prepared.command_preset_name:
            metadata["commandPreset"] = {
                "name": chat_run.prepared.command_preset_name,
                "contentHash": chat_run.prepared.command_preset_hash,
            }
        if chat_run.prepared.task_planning_mode:
            metadata["taskPlanningMode"] = True

        if not chat_run.is_resume_request and request.messages and request.messages[-1].role == "user":
            latest_user = request.messages[-1]
            user_message_id = str(uuid.uuid4())
            db.add_message(
                msg_id=user_message_id,
                session_id=chat_run.session_id,
                role="user",
                content=latest_user.content,
                images=request.fileUrls,
                metadata=metadata,
            )
            chat_run.emit_runtime_event(
                "message.user.recorded",
                {
                    "message_id": user_message_id,
                    "content": latest_user.content,
                    "images": request.fileUrls or [],
                    "resolved_scope": chat_run.scope_result.binding.resolved_scope,
                    "metadata": metadata,
                },
                agent_id=None,
                node="input_recorder",
            )
            if chat_run.prepared.command_preset_name:
                chat_run.emit_runtime_event(
                    "chat.command_preset.applied",
                    {
                        "name": chat_run.prepared.command_preset_name,
                        "contentHash": chat_run.prepared.command_preset_hash,
                        "messageId": user_message_id,
                    },
                    agent_id=None,
                    node="input_recorder",
                )
            if chat_run.prepared.task_planning_mode:
                chat_run.emit_runtime_event(
                    "chat.task_planning_mode.enabled",
                    {
                        "messageId": user_message_id,
                        "enabled": True,
                    },
                    agent_id=None,
                    node="input_recorder",
                )
            workflow_ledger_service.record_step_inputs(
                chat_run.active_run_id,
                inputs={
                    "latest_user_message_id": user_message_id,
                    "latest_user_content": latest_user.content,
                    "images": request.fileUrls or [],
                    "transport": chat_run.transport,
                    "resolved_scope": chat_run.scope_result.binding.resolved_scope,
                    "command_preset_name": chat_run.prepared.command_preset_name,
                    "task_planning_mode": chat_run.prepared.task_planning_mode,
                },
            )
            chat_run.run_handle.refresh_chat_snapshot()

        if not chat_run.is_resume_request and request.tool_outputs:
            for tool_output in request.tool_outputs:
                tool_message_id = str(uuid.uuid4())
                db.add_message(
                    msg_id=tool_message_id,
                    session_id=chat_run.session_id,
                    role="tool",
                    content=tool_output.output,
                    metadata={
                        **metadata,
                        "tool_call_id": tool_output.tool_call_id,
                        "tool_name": tool_output.name or "ask_user",
                    },
                )
                chat_run.emit_runtime_event(
                    "message.tool.recorded",
                    {
                        "message_id": tool_message_id,
                        "content": tool_output.output,
                        "tool_call_id": tool_output.tool_call_id,
                        "tool_name": tool_output.name or "ask_user",
                    },
                    agent_id=None,
                    node="input_recorder",
                )
            workflow_ledger_service.record_step_inputs(
                chat_run.active_run_id,
                inputs={
                    "tool_outputs": [
                        {
                            "tool_call_id": item.tool_call_id,
                            "tool_name": item.name or "ask_user",
                            "output": item.output,
                        }
                        for item in request.tool_outputs
                    ]
                },
            )
            chat_run.run_handle.refresh_chat_snapshot()

    def _recursion_limit(self) -> int:
        ctx_config = storage.get_context_config()
        return ctx_config.get("recursion_limit", 500)

    async def create_execution_bundle(self, *, chat_run: ChatRunContext) -> ChatExecutionBundle:
        runner_bundle = await supervisor_runner.create_execution_bundle(
            config=chat_run.request.config,
            messages=chat_run.lc_messages,
            session_id=chat_run.session_id,
            recursion_limit=self._recursion_limit(),
        )
        return ChatExecutionBundle(run_handle=chat_run.run_handle, runner_bundle=runner_bundle)

    async def create_resume_bundle(self, *, chat_run: ChatRunContext) -> ChatExecutionBundle:
        runner_bundle = await supervisor_runner.create_resume_bundle(
            config=chat_run.request.config,
            session_id=chat_run.session_id,
            resume_value=chat_run.request.resume_value or {},
            recursion_limit=self._recursion_limit(),
        )
        return ChatExecutionBundle(run_handle=chat_run.run_handle, runner_bundle=runner_bundle)

    async def create_continuation_bundle(
        self,
        *,
        chat_run: ChatRunContext,
        previous_bundle: ChatExecutionBundle,
        continuation_count: int,
        continuation_reason: str,
    ) -> ChatExecutionBundle | None:
        snapshot = await supervisor_runner.get_state_snapshot(previous_bundle.runner_bundle)
        if not isinstance(snapshot, dict):
            return None

        state_messages = list(snapshot.get("messages") or [])
        if not state_messages:
            return None

        continuation_envelope = {
            "continuationCount": continuation_count,
            "continuationReason": continuation_reason,
            "routeContext": dict(snapshot.get("current_route_context") or {}),
            "todosCount": len(list(snapshot.get("todos") or [])),
            "messageCount": len(state_messages),
            "sessionId": chat_run.session_id,
            "projectId": chat_run.scope_result.binding.project_id,
            "workspaceId": chat_run.scope_result.binding.workspace_id,
            "workspacePath": chat_run.scope_result.binding.workspace_path,
            "resolvedScope": chat_run.scope_result.binding.resolved_scope,
        }

        runner_bundle = await supervisor_runner.create_execution_bundle(
            config=chat_run.request.config,
            messages=state_messages,
            session_id=chat_run.session_id,
            recursion_limit=self._recursion_limit(),
        )
        diagnostics = dict(runner_bundle.diagnostics or {})
        diagnostics.update(continuation_envelope)
        runner_bundle.diagnostics = diagnostics
        return ChatExecutionBundle(run_handle=chat_run.run_handle, runner_bundle=runner_bundle)

    async def resolve_execution_bundle(self, *, chat_run: ChatRunContext) -> ChatExecutionBundle:
        if chat_run.is_resume_request:
            return await self.create_resume_bundle(chat_run=chat_run)
        return await self.create_execution_bundle(chat_run=chat_run)

    def open_event_stream(self, bundle: ChatExecutionBundle):
        return supervisor_runner.open_bundle_stream(bundle.runner_bundle)

    async def stream_runner_events(self, bundle: ChatExecutionBundle):
        async for event in supervisor_runner.stream_events(bundle.runner_bundle):
            yield event

    def create_stream_state(self) -> ChatStreamState:
        loaded_agents = storage.get_all_agents()
        valid_nodes = [item.get("id") for item in loaded_agents if item.get("id")] + ["supervisor", "reviewer"]
        return ChatStreamState(valid_agent_node_names=valid_nodes)

    def emit_stream_connected_events(self, chat_run: ChatRunContext) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        chat_run.emit_runtime_event(
            "session.connected",
            {"transport": chat_run.transport},
            agent_id=None,
            node="session_runtime",
        )
        events.append(protocol_connected_event(session_id=chat_run.session_id, transport=chat_run.transport, run_id=chat_run.active_run_id))
        return events

    def emit_stream_start_events(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        init_profile = self._get_agent_profile(stream_state.current_agent)
        init_agent_event = {
            "type": "agent_start",
            "agent": {
                "id": stream_state.current_agent,
                "name": init_profile["name"],
                "avatar": init_profile["avatar"],
                "roleLabel": init_profile["roleLabel"],
            },
        }
        chat_run.emit_runtime_event("agent.started", init_agent_event, agent_id=stream_state.current_agent, node=stream_state.current_agent)
        events.append(init_agent_event)
        return events

    def build_lane_status_event(
        self,
        chat_run: ChatRunContext,
        *,
        status: str,
        policy: str,
        blocked_by_run_id: str | None = None,
        interrupted_run_id: str | None = None,
        heartbeat: bool = False,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": status,
            "policy": policy,
        }
        if blocked_by_run_id:
            data["blockedByRunId"] = blocked_by_run_id
        if interrupted_run_id:
            data["interruptedRunId"] = interrupted_run_id
        if heartbeat:
            data["heartbeat"] = True
        return {
            "type": "custom_event",
            "name": "run_status",
            "run_id": chat_run.active_run_id,
            "data": data,
        }

    def handle_preflight_gate(self, chat_run: ChatRunContext) -> list[dict[str, Any]]:
        decision = chat_run.preflight_decision
        if decision.is_allow():
            return []

        request_payload = safety_guardian.build_runtime_preflight_request(
            runtime_kind="chat",
            trigger_source=chat_run.transport,
            decision=decision,
            subject=chat_run.prepared.latest_user_content or chat_run.session_id,
        )
        ask_user_event = {
            "type": "custom_event",
            "name": "ask_user",
            "run_id": chat_run.active_run_id,
            "data": {
                "question": request_payload["question"],
                "interactionKind": request_payload.get("interactionKind") or "approval",
                "approvalKind": request_payload["approvalKind"],
                "request": request_payload,
                "safety": decision.to_payload(),
            },
        }

        if decision.is_review():
            approval = chat_run.run_handle.request_approval(
                approval_kind="safety_review",
                request=request_payload,
            )
            if str(approval.get("status") or "").strip().lower() != "pending":
                chat_run.run_handle.refresh_chat_snapshot()
                return []
            chat_run.run_handle.refresh_chat_snapshot()
            ask_user_event["data"]["approvalId"] = approval.get("approval_id")
            ask_user_event["data"]["toolCallId"] = approval.get("approval_id")
            return [ask_user_event, {"type": "done", "status": "waiting_approval", "run_id": chat_run.active_run_id}]

        chat_run.emit_runtime_event(
            "safety.preflight.blocked",
            {
                "reason": decision.reason,
                "risk_code": decision.risk_code,
                "details": decision.details,
            },
            agent_id=None,
            node="safety_guardian",
        )
        chat_run.run_handle.fail(f"Safety Guardian blocked chat run: {decision.reason}", node="safety_guardian")
        return [ask_user_event, {"type": "done", "status": "blocked", "run_id": chat_run.active_run_id}]

    async def _emit_text_delta(self, chat_run: ChatRunContext, stream_state: ChatStreamState, delta: str) -> list[dict[str, Any]]:
        emitted_events: list[dict[str, Any]] = []
        if not delta:
            return emitted_events
        for stable_chunk in stream_state.text_aggregator.push(delta):
            if not stable_chunk:
                continue
            text_event = {"type": "text_chunk", "content": stable_chunk, "timestamp": 0}
            runtime_event = chat_run.emit_runtime_event("run.text.delta", text_event, agent_id=stream_state.current_agent, node=stream_state.current_agent)
            workflow_ledger_service.append_chat_projection(
                session_id=chat_run.session_id,
                run_id=chat_run.active_run_id,
                text_delta=stable_chunk,
                agent_profile=self._get_agent_profile(stream_state.current_agent),
                latest_seq=runtime_event.get("seq"),
            )
            emitted_events.append(text_event)
        return emitted_events

    def _maybe_agent_start_event(self, chat_run: ChatRunContext, stream_state: ChatStreamState, metadata: dict[str, Any]) -> dict[str, Any] | None:
        node_name = metadata.get("langgraph_node", "")
        if not node_name or node_name not in stream_state.valid_agent_node_names or node_name == stream_state.current_agent:
            return None
        stream_state.current_agent = node_name
        profile = self._get_agent_profile(node_name)
        agent_event = {
            "type": "agent_start",
            "agent": {
                "id": node_name,
                "name": profile["name"],
                "avatar": profile["avatar"],
                "roleLabel": profile["roleLabel"],
            },
        }
        chat_run.emit_runtime_event("agent.started", payload=agent_event, agent_id=node_name, node=node_name)
        return agent_event

    async def handle_stream_event(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        event: dict[str, Any],
    ) -> list[dict[str, Any]]:
        emitted_events: list[dict[str, Any]] = []
        kind = event["event"]
        name = event.get("name", "")
        data = event.get("data", {})
        metadata = event.get("metadata") or {}

        agent_event = self._maybe_agent_start_event(chat_run, stream_state, metadata)
        if agent_event:
            emitted_events.append(agent_event)

        if kind == "on_chain_stream":
            interrupt_request = self._extract_interrupt_request(data.get("chunk"))
            if interrupt_request:
                approval = chat_run.run_handle.request_approval(
                    approval_kind=interrupt_request.get("approvalKind") or "human_input_required",
                    request=interrupt_request,
                )
                if str(approval.get("status") or "").strip().lower() != "pending":
                    chat_run.run_handle.refresh_chat_snapshot()
                    return emitted_events
                chat_run.run_handle.refresh_chat_snapshot()
                emitted_events.append(
                    {
                        "type": "custom_event",
                        "name": "ask_user",
                        "run_id": chat_run.active_run_id,
                        "data": {
                            "question": interrupt_request.get("question"),
                            "toolCallId": interrupt_request.get("toolCallId") or approval.get("approval_id"),
                            "approvalId": approval.get("approval_id"),
                            "interactionKind": interrupt_request.get("interactionKind") or "approval",
                            "approvalKind": approval.get("approval_kind"),
                            "request": interrupt_request,
                        },
                    }
                )
                stream_state.interrupted_signal = {
                    "command": "approval_requested",
                    "reason": approval.get("approval_kind") or "human_input_required",
                    "payload": {"approval_id": approval.get("approval_id")},
                }
                return emitted_events

        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk:
                text_delta, reasoning_delta = extract_text_and_reasoning(chunk)
                if not text_delta and isinstance(chunk, str):
                    text_delta = chunk
                if text_delta:
                    stream_state.streamed_model_run_ids.add(event.get("run_id", ""))
                    text_delta = stream_state.text_filter.process(text_delta)
                if text_delta:
                    stream_state.watchdog.note_text_progress()
                    stream_state.output_buffer.append(text_delta)
                    emitted_events.extend(await self._emit_text_delta(chat_run, stream_state, text_delta))
                if reasoning_delta:
                    stream_state.streamed_model_run_ids.add(event.get("run_id", ""))
                    stream_state.watchdog.note_text_progress()
                    stream_state.reasoning_buffer.append(reasoning_delta)
                    reasoning_event = {"type": "reasoning_chunk", "content": reasoning_delta, "timestamp": 0}
                    runtime_event = chat_run.emit_runtime_event("run.reasoning.delta", reasoning_event, agent_id=stream_state.current_agent, node=stream_state.current_agent)
                    workflow_ledger_service.append_chat_projection(
                        session_id=chat_run.session_id,
                        run_id=chat_run.active_run_id,
                        reasoning_delta=reasoning_delta,
                        agent_profile=self._get_agent_profile(stream_state.current_agent),
                        latest_seq=runtime_event.get("seq"),
                    )
                    emitted_events.append(reasoning_event)
            return emitted_events

        if kind == "on_chat_model_end":
            model_run_id = event.get("run_id", "")
            if model_run_id in stream_state.streamed_model_run_ids:
                return emitted_events
            final_output = data.get("output")
            text_delta, reasoning_delta = extract_text_and_reasoning(final_output)
            if not text_delta and isinstance(final_output, str):
                text_delta = final_output
            if reasoning_delta:
                stream_state.watchdog.note_text_progress()
                stream_state.reasoning_buffer.append(reasoning_delta)
                reasoning_event = {"type": "reasoning_chunk", "content": reasoning_delta, "timestamp": 0}
                runtime_event = chat_run.emit_runtime_event("run.reasoning.delta", reasoning_event, agent_id=stream_state.current_agent, node=stream_state.current_agent)
                workflow_ledger_service.append_chat_projection(
                    session_id=chat_run.session_id,
                    run_id=chat_run.active_run_id,
                    reasoning_delta=reasoning_delta,
                    agent_profile=self._get_agent_profile(stream_state.current_agent),
                    latest_seq=runtime_event.get("seq"),
                )
                emitted_events.append(reasoning_event)
            if text_delta:
                text_delta = stream_state.text_filter.process(text_delta)
                if text_delta:
                    stream_state.watchdog.note_text_progress()
                    stream_state.output_buffer.append(text_delta)
                    emitted_events.extend(await self._emit_text_delta(chat_run, stream_state, text_delta))
            return emitted_events

        if kind == "on_tool_start":
            inputs = data.get("input", {})
            tool_call_id = event.get("run_id", "")
            tool_start_event = {
                "type": "tool_start",
                "tool": {"toolCallId": tool_call_id, "toolName": name, "args": inputs},
                "timestamp": 0,
            }
            chat_run.emit_runtime_event("tool.started", tool_start_event, agent_id=stream_state.current_agent, node=stream_state.current_agent)
            stream_state.watchdog.note_tool_start(tool_call_id)
            stream_state.tool_calls_buffer.append({"id": tool_call_id, "name": name, "args": inputs})
            emitted_events.append(tool_start_event)
            return emitted_events

        if kind == "on_tool_end":
            output = data.get("output", "")
            tool_call_id = event.get("run_id", "")
            output_str = str(output.content) if hasattr(output, "content") else str(output)
            tool_result_event = {
                "type": "tool_result",
                "tool": {"toolCallId": tool_call_id, "result": output_str},
                "timestamp": 0,
            }
            stream_state.watchdog.note_tool_end(tool_call_id)
            chat_run.emit_runtime_event("tool.finished", tool_result_event, agent_id=stream_state.current_agent, node=stream_state.current_agent)
            emitted_events.append(tool_result_event)
            return emitted_events

        return emitted_events

    async def flush_stream_state(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> list[dict[str, Any]]:
        emitted_events: list[dict[str, Any]] = []
        final_filtered_text = stream_state.text_filter.flush()
        if final_filtered_text:
            stream_state.output_buffer.append(final_filtered_text)
            emitted_events.extend(await self._emit_text_delta(chat_run, stream_state, final_filtered_text))

        final_aggregated_chunk = stream_state.text_aggregator.flush()
        if final_aggregated_chunk:
            final_text_event = {"type": "text_chunk", "content": final_aggregated_chunk, "timestamp": 0}
            runtime_event = chat_run.emit_runtime_event("run.text.delta", final_text_event, agent_id=stream_state.current_agent, node=stream_state.current_agent)
            workflow_ledger_service.append_chat_projection(
                session_id=chat_run.session_id,
                run_id=chat_run.active_run_id,
                text_delta=final_aggregated_chunk,
                agent_profile=self._get_agent_profile(stream_state.current_agent),
                latest_seq=runtime_event.get("seq"),
            )
            emitted_events.append(final_text_event)
        return emitted_events

    def persist_final_assistant_message(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> None:
        if not stream_state.output_buffer and not stream_state.tool_calls_buffer and not stream_state.reasoning_buffer:
            return
        profile = self._get_agent_profile(stream_state.current_agent)
        message_id = str(uuid.uuid4())
        db.add_message(
            msg_id=message_id,
            session_id=chat_run.session_id,
            role="assistant",
            content="".join(stream_state.output_buffer),
            reasoning_content="".join(stream_state.reasoning_buffer) if stream_state.reasoning_buffer else None,
            tool_calls=stream_state.tool_calls_buffer if stream_state.tool_calls_buffer else None,
            metadata={
                "run_id": chat_run.active_run_id,
                "transport": chat_run.transport,
                "project_id": chat_run.scope_result.binding.project_id,
                "workspace_id": chat_run.scope_result.binding.workspace_id,
                "resolved_scope": chat_run.scope_result.binding.resolved_scope,
            },
            agent_id=stream_state.current_agent,
            agent_name=profile["name"],
            agent_avatar=profile["avatar"],
            agent_role_label=profile["roleLabel"],
        )
        db.attach_runtime_artifacts_to_message(
            session_id=chat_run.session_id,
            run_id=chat_run.active_run_id,
            message_id=message_id,
        )
        workflow_ledger_service.clear_chat_projection(chat_run.active_run_id)

    def finalize_interrupted_run(self, chat_run: ChatRunContext, interrupted_signal: dict[str, Any]) -> list[dict[str, Any]]:
        if interrupted_signal.get("command") == "approval_requested":
            return [{"type": "done", "status": "waiting_approval", "run_id": chat_run.active_run_id}]
        if interrupted_signal.get("command") in {"cancel", "interrupt"}:
            try:
                from core.system_tools.native import _terminate_run_background_commands

                _terminate_run_background_commands(chat_run.active_run_id, interactive_only=True)
            except Exception:
                logging.getLogger("v8chat.chat_runtime").exception(
                    "Failed to clean up interactive background commands for interrupted run '%s'",
                    chat_run.active_run_id,
                )
        chat_run.run_handle.refresh_chat_snapshot()
        status = "paused" if interrupted_signal.get("command") in {"pause", "interrupt"} else "cancelled"
        return [
            self.build_legacy_control_event(interrupted_signal),
            {"type": "done", "status": status, "run_id": chat_run.active_run_id},
        ]

    def finalize_success_run(self, chat_run: ChatRunContext) -> dict[str, Any]:
        chat_run.run_handle.complete(reason="stream_finished", node="run_manager")
        return {"type": "done", "status": "finished", "run_id": chat_run.active_run_id}

    def finalize_failed_run(self, chat_run: ChatRunContext | None, exc: Exception) -> list[dict[str, Any]]:
        run_id = chat_run.active_run_id if chat_run else None
        if chat_run and isinstance(exc, ModelGovernanceInterventionRequired):
            request_payload = exc.to_request_payload()
            approval = chat_run.run_handle.request_approval(
                approval_kind=exc.approval_kind,
                request=request_payload,
            )
            chat_run.run_handle.refresh_chat_snapshot()
            if str(approval.get("status") or "").strip().lower() == "pending":
                return [
                    {
                        "type": "custom_event",
                        "name": "ask_user",
                        "run_id": chat_run.active_run_id,
                        "data": {
                            "question": request_payload["question"],
                            "interactionKind": request_payload.get("interactionKind") or "approval",
                            "approvalKind": exc.approval_kind,
                            "approvalId": approval.get("approval_id"),
                            "toolCallId": approval.get("approval_id"),
                            "request": request_payload,
                            "governance": {
                                "message": str(exc),
                                "details": exc.details,
                            },
                        },
                    },
                    {"type": "done", "status": "waiting_approval", "run_id": chat_run.active_run_id},
                ]
        normalized = normalize_provider_error(exc)
        if chat_run:
            try:
                from core.system_tools.native import _terminate_run_background_commands

                _terminate_run_background_commands(chat_run.active_run_id, interactive_only=True)
            except Exception:
                logging.getLogger("v8chat.chat_runtime").exception(
                    "Failed to clean up interactive background commands for failed run '%s'",
                    chat_run.active_run_id,
                )
            try:
                chat_run.run_handle.fail(normalized["message"], node="run_manager")
            except Exception as fail_exc:
                logging.getLogger("v8chat.chat_runtime").exception(
                    "Failed to persist failed run state for run '%s' during error finalization",
                    chat_run.active_run_id,
                )
                try:
                    run_service.transition_run(
                        chat_run.active_run_id,
                        status="failed",
                        error_message=normalized["message"],
                    )
                except Exception:
                    logging.getLogger("v8chat.chat_runtime").exception(
                        "Fallback run_service.transition_run also failed for run '%s'",
                        chat_run.active_run_id,
                    )
        return [
            {
                "type": "error",
                "error": normalized["message"],
                "providerError": normalized,
                "run_id": run_id,
            }
        ]

    def consume_control_signal(self, run_id: str):
        return erc_kernel.consume_control_signal(run_id)

    def should_stop_stream(self, signal: dict | None) -> bool:
        if not signal:
            return False
        return signal.get("command") in {"pause", "cancel", "interrupt"}

    def build_legacy_control_event(self, signal: dict) -> dict:
        command = signal.get("command")
        status = "paused" if command in {"pause", "interrupt"} else "cancelled"
        return {
            "type": "custom_event",
            "name": "run_controlled",
            "data": {
                "command": command,
                "reason": signal.get("reason"),
                "status": status,
                "payload": signal.get("payload") or {},
            },
        }

    def _runtime_context_kwargs(self, chat_run: ChatRunContext) -> dict[str, Any]:
        return {
            "runtime_kind": "chat",
            "trigger_source": chat_run.transport,
            "session_id": chat_run.session_id,
            "run_id": chat_run.active_run_id,
            "user_id": chat_run.user_id,
            "project_id": chat_run.scope_result.binding.project_id,
            "workspace_id": chat_run.scope_result.binding.workspace_id,
            "resolved_scope": chat_run.scope_result.binding.resolved_scope,
            "goal": chat_run.prepared.latest_user_content,
        }

    async def stream_legacy_events(
        self,
        request: ChatRequest,
        *,
        transport: str = "http",
        run_id: str | None = None,
    ):
        chat_run = self.prepare_run_context(request, transport=transport, run_id=run_id)
        for connected_event in self.emit_stream_connected_events(chat_run):
            yield connected_event
        lane_policy = runtime_stability_service.session_lane_policy()
        lane_metadata = {"transport": transport, "runtimeKind": "chat"}
        lane_decision = session_admission_service.try_acquire(
            chat_run.session_id,
            chat_run.active_run_id,
            policy=lane_policy,
            runtime_kind="chat",
            metadata=lane_metadata,
        )
        interrupted_signal = None
        if not lane_decision.acquired and lane_decision.waited:
            chat_run.emit_runtime_event(
                "run.lane.queued",
                {
                    "policy": lane_decision.policy,
                    "blocked_by_run_id": lane_decision.active_run_id,
                    "interrupted_run_id": lane_decision.interrupted_run_id,
                },
                agent_id=None,
                node="session_lane",
            )
            chat_run.emit_runtime_event(
                "run.liveness.blocked",
                {
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": f"lane_busy:{lane_decision.active_run_id}",
                    "last_progress_at": None,
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
                agent_id=None,
                node="session_lane",
            )
            yield self.build_lane_status_event(
                chat_run,
                status="queued",
                policy=lane_decision.policy,
                blocked_by_run_id=lane_decision.active_run_id,
                interrupted_run_id=lane_decision.interrupted_run_id,
            )
            loop = asyncio.get_running_loop()
            next_heartbeat_at = loop.time() + 15.0
            blocked_by_run_id = lane_decision.active_run_id
            interrupted_run_id = lane_decision.interrupted_run_id
            while True:
                control_signal = self.consume_control_signal(chat_run.active_run_id)
                if self.should_stop_stream(control_signal):
                    interrupted_signal = control_signal
                    break
                await asyncio.sleep(1.0)
                lane_decision = session_admission_service.try_acquire(
                    chat_run.session_id,
                    chat_run.active_run_id,
                    policy=lane_policy,
                    runtime_kind="chat",
                    metadata=lane_metadata,
                )
                if lane_decision.acquired:
                    lane_decision.waited = True
                    lane_decision.active_run_id = blocked_by_run_id
                    lane_decision.interrupted_run_id = interrupted_run_id
                    break
                now = loop.time()
                if now >= next_heartbeat_at:
                    chat_run.emit_runtime_event(
                        "run.liveness.heartbeat",
                        {
                            "heartbeat_kind": "session_lane",
                            "blocked_reason": f"lane_busy:{blocked_by_run_id}",
                            "watchdog_source": "session_lane",
                            "stalled": False,
                        },
                        agent_id=None,
                        node="session_lane",
                    )
                    yield self.build_lane_status_event(
                        chat_run,
                        status="queued",
                        policy=lane_decision.policy,
                        blocked_by_run_id=blocked_by_run_id,
                        interrupted_run_id=interrupted_run_id,
                        heartbeat=True,
                    )
                    next_heartbeat_at = now + 15.0
            if interrupted_signal and not lane_decision.acquired:
                for final_event in self.finalize_interrupted_run(chat_run, interrupted_signal):
                    yield final_event
                return
        if not lane_decision.acquired:
            busy_run_id = lane_decision.rejected_by_run_id or lane_decision.active_run_id
            chat_run.emit_runtime_event(
                "run.lane.rejected",
                {
                    "policy": lane_decision.policy,
                    "busy_run_id": busy_run_id,
                    "session_id": chat_run.session_id,
                },
                agent_id=None,
                node="session_lane",
            )
            chat_run.run_handle.fail(
                f"Session lane busy: session '{chat_run.session_id}' is already running '{busy_run_id}'.",
                node="session_lane",
            )
            yield {
                "type": "error",
                "error": f"当前会话已有任务在执行中，策略为 {lane_decision.policy}，本次请求未进入执行。",
                "run_id": chat_run.active_run_id,
            }
            return

        chat_run.emit_runtime_event(
            "run.lane.acquired",
            {
                "policy": lane_decision.policy,
                "waited": lane_decision.waited,
                "previous_run_id": lane_decision.active_run_id,
                "interrupted_run_id": lane_decision.interrupted_run_id,
            },
            agent_id=None,
            node="session_lane",
        )
        if lane_decision.waited:
            chat_run.emit_runtime_event(
                "run.liveness.recovered",
                {
                    "heartbeat_kind": "session_lane",
                    "blocked_reason": None,
                    "watchdog_source": "session_lane",
                    "stalled": False,
                },
                agent_id=None,
                node="session_lane",
            )
        self.emit_lifecycle_start_events(chat_run)
        self.record_request_inputs(chat_run)

        stream_state = self.create_stream_state()

        try:
            for startup_event in self.emit_stream_start_events(chat_run, stream_state):
                yield startup_event

            preflight_events = self.handle_preflight_gate(chat_run)
            if preflight_events:
                for preflight_event in preflight_events:
                    yield preflight_event
                return

            continuation_count = 0
            continuation_reason = ""
            continuation_bundle: ChatExecutionBundle | None = None
            while True:
                execution_bundle = continuation_bundle or await self.resolve_execution_bundle(chat_run=chat_run)
                if execution_bundle.runner_bundle.diagnostics:
                    chat_run.emit_runtime_event(
                        "supervisor.graph.diagnostics",
                        dict(execution_bundle.runner_bundle.diagnostics),
                        agent_id=None,
                        node="supervisor_graph",
                    )

                event_stream = self.stream_runner_events(execution_bundle)
                try:
                    with bind_runtime_context(**self._runtime_context_kwargs(chat_run)):
                        async with aclosing(event_stream):
                            stream_iter = event_stream.__aiter__()
                            while True:
                                try:
                                    event = await next_graph_stream_event(
                                        stream_iter,
                                        state=stream_state.watchdog,
                                        session_id=chat_run.session_id,
                                        run_id=chat_run.active_run_id,
                                        on_timeout=lambda payload: (
                                            chat_run.emit_runtime_event(
                                                "run.watchdog.stream_idle_timeout",
                                                payload,
                                                agent_id=None,
                                                node="stream_watchdog",
                                            ),
                                            chat_run.emit_runtime_event(
                                                "run.liveness.stalled",
                                                {
                                                    "heartbeat_kind": "stream_watchdog",
                                                    "watchdog_source": "stream_watchdog",
                                                    "idle_reason": "stream_idle_timeout",
                                                    "stalled": True,
                                                    **payload,
                                                },
                                                agent_id=None,
                                                node="stream_watchdog",
                                            ),
                                        )[-1],
                                    )
                                except StopAsyncIteration:
                                    break
                                try:
                                    control_signal = self.consume_control_signal(chat_run.active_run_id)
                                    if self.should_stop_stream(control_signal):
                                        interrupted_signal = control_signal
                                        break

                                    emitted_events = await self.handle_stream_event(
                                        chat_run,
                                        stream_state,
                                        event,
                                    )
                                    for emitted_event in emitted_events:
                                        yield emitted_event

                                    if stream_state.interrupted_signal:
                                        interrupted_signal = stream_state.interrupted_signal
                                        break
                                finally:
                                    stream_state.watchdog.finish_event(event)
                    break
                except GraphRecursionError:
                    if continuation_count >= 1:
                        raise
                    continuation_count += 1
                    continuation_reason = "graph_recursion_limit"
                    continuation_bundle = await self.create_continuation_bundle(
                        chat_run=chat_run,
                        previous_bundle=execution_bundle,
                        continuation_count=continuation_count,
                        continuation_reason=continuation_reason,
                    )
                    if continuation_bundle is None:
                        raise
                    chat_run.emit_runtime_event(
                        "run.continuation.scheduled",
                        {
                            "continuationCount": continuation_count,
                            "continuationReason": continuation_reason,
                        },
                        agent_id=None,
                        node="continuation_manager",
                    )
                    continue

            if interrupted_signal:
                for final_event in self.finalize_interrupted_run(chat_run, interrupted_signal):
                    yield final_event
                return

            for flushed_event in await self.flush_stream_state(chat_run, stream_state):
                yield flushed_event
            self.persist_final_assistant_message(chat_run, stream_state)
            yield self.finalize_success_run(chat_run)
        except Exception as exc:
            for failed_event in self.finalize_failed_run(chat_run, exc):
                yield failed_event
        finally:
            # 这里避免在 finally 里再出现 await，防止请求收尾阶段的取消打断
            # lane 已释放但 released 事件尚未来得及落库，造成 handoff 账本缺口。
            session_admission_service.release(
                chat_run.session_id,
                chat_run.active_run_id,
                policy=lane_policy,
                runtime_kind="chat",
                metadata=lane_metadata,
            )
            chat_run.emit_runtime_event(
                "run.lane.released",
                {
                    "policy": lane_decision.policy,
                    "session_id": chat_run.session_id,
                },
                agent_id=None,
                node="session_lane",
            )


chat_runtime = runtime_registry.register(ChatRuntime())
