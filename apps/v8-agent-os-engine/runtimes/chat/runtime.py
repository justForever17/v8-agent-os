from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
import logging
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from api.models import ChatRequest
from agents.runners.supervisor_runner import SupervisorExecutionBundle, supervisor_runner
from core.chat_output_extractor import extract_text_and_reasoning
from core.system_tools.command_presets import read_command_preset
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.models.provider_compatibility import normalize_provider_error
from core.database import db
from core.engine_config_resolver import resolve_engine_config_for_role
from core.graph_stream_watchdog import (
    GraphStreamDownstreamTimeoutError,
    GraphStreamIdleTimeoutError,
    GraphStreamWatchdogState,
    normalize_stream_iterator_exception,
)
from core.json_safe import to_jsonable
from core.realtime_protocol import protocol_connected_event
from core.stream_chunk_aggregator import TextChunkAggregator
from core.storage import storage
from core.context.workspace import workspace_resolution_service
from erc.chat_canonical_transcript import (
    CanonicalTranscriptBuilder,
    export_legacy_message_payload,
    validate_canonical_message_invariants,
)
from erc.canonical_model_events import LangChainCanonicalModelEventAdapter
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
    skill_references: list[dict[str, str]] = field(default_factory=list)


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
    authoritative_final_text: str | None = None
    tool_calls_buffer: list[dict[str, Any]] = field(default_factory=list)
    streamed_model_run_ids: set[str] = field(default_factory=set)
    text_snapshots_by_run: dict[str, str] = field(default_factory=dict)
    reasoning_snapshots_by_run: dict[str, str] = field(default_factory=dict)
    last_text_delta: str = ""
    last_text_delta_run_id: str = ""
    last_reasoning_delta: str = ""
    last_reasoning_delta_run_id: str = ""
    text_flush_deadline: float | None = None
    text_raw_chars: int = 0
    text_emitted_chunks: int = 0
    text_timer_flushes: int = 0
    text_final_flush_chars: int = 0
    watchdog: GraphStreamWatchdogState = field(default_factory=GraphStreamWatchdogState)
    interrupted_signal: dict[str, Any] | None = None
    valid_agent_node_names: list[str] = field(default_factory=list)
    text_filter: StreamFilter = field(default_factory=lambda: StreamFilter(["NONE", "None", "null", "```json", "```"]))
    text_aggregator: TextChunkAggregator = field(default_factory=TextChunkAggregator)
    pending_stream_event_task: asyncio.Task[Any] | None = None
    assistant_message_id: str | None = None
    assistant_transcript_version: int = 0
    narrative_started_model_run_ids: set[str] = field(default_factory=set)
    active_tool_call_ids: set[str] = field(default_factory=set)


canonical_transcript_builder = CanonicalTranscriptBuilder()
canonical_model_event_adapter = LangChainCanonicalModelEventAdapter()


class ChatRuntime:
    """
    Phase 2 运行时层：
    把聊天请求的生命周期准备、run 启动、scope 绑定、输入落库、
    graph 执行包构建逐步从 routes.py 收口到 ChatRuntime。
    """

    kind = "chat"
    TEXT_FLUSH_INTERVAL_SECONDS = 0.22
    TOOL_INPUT_INTERNAL_KEYS = {
        "runtime",
        "callbacks",
        "config",
        "context",
        "store",
        "streamwriter",
        "toolcallid",
    }

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
        interaction_kind = payload.get("interactionKind") or payload.get("interaction_kind")
        approval_kind = payload.get("approvalKind") or payload.get("approval_kind")
        if not interaction_kind and approval_kind == "ask_user":
            interaction_kind = "ask_user"
        if not interaction_kind:
            interaction_kind = "approval"
        if not approval_kind:
            approval_kind = "ask_user" if interaction_kind == "ask_user" else "human_input_required"
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

    def _is_ask_user_request(self, request_payload: dict[str, Any] | None) -> bool:
        payload = request_payload or {}
        approval_kind = str(payload.get("approvalKind") or payload.get("approval_kind") or "").strip().lower()
        interaction_kind = str(payload.get("interactionKind") or payload.get("interaction_kind") or "").strip().lower()
        return interaction_kind == "ask_user" or approval_kind == "ask_user"

    def _build_ask_user_event(
        self,
        chat_run: ChatRunContext,
        *,
        request_payload: dict[str, Any],
        approval: dict[str, Any] | None = None,
        governance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_data = {
            "question": request_payload.get("question"),
            "toolCallId": request_payload.get("toolCallId") or (approval or {}).get("approval_id"),
            "approvalId": (approval or {}).get("approval_id"),
            "interactionKind": request_payload.get("interactionKind") or "ask_user",
            "approvalKind": request_payload.get("approvalKind") or (approval or {}).get("approval_kind"),
            "request": request_payload,
        }
        if governance:
            event_data["governance"] = governance
        return {
            "type": "custom_event",
            "name": "ask_user",
            "run_id": chat_run.active_run_id,
            "data": event_data,
        }

    def _build_safety_blocked_event(
        self,
        chat_run: ChatRunContext,
        *,
        reason: str,
        risk_code: str | None = None,
        details: dict[str, Any] | None = None,
        request_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "custom_event",
            "name": "safety_blocked",
            "run_id": chat_run.active_run_id,
            "data": {
                "reason": reason,
                "riskCode": risk_code,
                "details": details or {},
                "request": request_payload or {},
            },
        }

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

    @staticmethod
    def _attachment_url(attachment: dict[str, Any]) -> str:
        return str(
            attachment.get("url")
            or attachment.get("publicUrl")
            or attachment.get("public_url")
            or attachment.get("workspacePath")
            or attachment.get("workspace_path")
            or attachment.get("path")
            or ""
        ).strip()

    @staticmethod
    def _attachment_name(attachment: dict[str, Any]) -> str:
        name = str(attachment.get("name") or "").strip()
        if name:
            return name
        url = ChatRuntime._attachment_url(attachment)
        return Path(url).name if url else "uploaded-file"

    def _normalize_request_attachments(self, request: ChatRequest) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(raw: Any, *, source: str) -> None:
            if raw is None:
                return
            if isinstance(raw, str):
                item: dict[str, Any] = {"url": raw, "source": source}
            elif hasattr(raw, "model_dump"):
                item = dict(raw.model_dump(mode="json", by_alias=True, exclude_none=True))
                item.setdefault("source", source)
            elif isinstance(raw, dict):
                item = dict(raw)
                item.setdefault("source", source)
            else:
                return
            url = self._attachment_url(item)
            if not url:
                return
            fingerprint = url.lower()
            if fingerprint in seen:
                return
            seen.add(fingerprint)
            item.setdefault("id", str(uuid.uuid4()))
            item.setdefault("name", self._attachment_name(item))
            normalized.append({key: value for key, value in item.items() if value is not None})

        for attachment in list(request.attachments or []):
            _add(attachment, source="chat_request.attachments")
        request_data = request.data
        for attachment in list(getattr(request_data, "attachments", None) or []):
            _add(attachment, source="chat_request.data.attachments")
        for url in list(request.fileUrls or []):
            _add(url, source="chat_request.fileUrls")
        for url in list(getattr(request_data, "fileUrls", None) or []):
            _add(url, source="chat_request.data.fileUrls")

        request.attachments = normalized  # type: ignore[assignment]
        request.fileUrls = [self._attachment_url(item) for item in normalized if self._attachment_url(item)]
        return normalized

    def _ensure_latest_user_content_for_attachments(self, request: ChatRequest, attachments: list[dict[str, Any]]) -> None:
        if not attachments:
            return
        for message in reversed(request.messages):
            if message.role != "user":
                continue
            if str(message.content or "").strip():
                return
            count = len(attachments)
            message.content = f"已上传 {count} 个文件" if count != 1 else "已上传 1 个文件"
            return

    def _inject_uploaded_file_notices(self, request: ChatRequest, lc_messages: list[Any]) -> None:
        attachments = [dict(item) for item in list(request.attachments or []) if isinstance(item, dict)]
        if not attachments:
            return

        local_files: list[str] = []
        for attachment in attachments:
            url = self._attachment_url(attachment)
            if "/api/workspace/files/" in url:
                subpath = url.split("/api/workspace/files/")[-1]
                workspace_dir = workspace_resolution_service.resolve_workspace_path(
                    runtime_kind="chat",
                    session_id=request.conversationId or request.session_id,
                    explicit_workspace_id=request.workspace_id,
                    explicit_project_id=request.project_id,
                    explicit_workspace_path=request.workspace_path,
                )
                local_path = Path(workspace_dir) / subpath
                local_files.append(str(local_path.absolute().resolve()))
            else:
                local_files.append(url)

        file_notices = "\n\n" + "\n".join([f"[User uploaded file: {path}]" for path in local_files if path])
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

    def _normalize_skill_references(self, request: ChatRequest) -> list[dict[str, str]]:
        request_data = request.data
        selected = getattr(request_data, "skill_references", None) if request_data else None
        normalized: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in list(selected or []):
            skill_id = str(getattr(item, "id", "") or "").strip()
            name = str(getattr(item, "name", "") or "").strip()
            description = str(getattr(item, "description", "") or "").strip()
            path = str(getattr(item, "path", "") or "").strip()
            source_type = str(getattr(item, "source_type", "") or "").strip()
            workspace_path = str(getattr(item, "workspace_path", "") or "").strip()
            workspace_id = str(getattr(item, "workspace_id", "") or "").strip()
            project_id = str(getattr(item, "project_id", "") or "").strip()
            if not skill_id and not name and not path:
                continue
            dedupe_key = (skill_id.lower(), name.lower(), path.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(
                {
                    "id": skill_id,
                    "name": name or Path(path).name or "unknown-skill",
                    "description": description,
                    "path": path,
                    "sourceType": source_type,
                    "workspacePath": workspace_path,
                    "workspaceId": workspace_id,
                    "projectId": project_id,
                }
            )
        return normalized

    def _resolve_request_context(self, request: ChatRequest) -> tuple[dict[str, Any] | None, bool, list[dict[str, str]]]:
        request_data = request.data
        command_selection = request_data.command_preset if request_data else None
        task_planning_mode = bool(request_data.task_planning_mode) if request_data else False

        command_preset = None
        if command_selection and command_selection.name:
            command_preset = read_command_preset(command_selection.name)
            if not command_preset:
                raise RuntimeError(f"Command preset '{command_selection.name}' does not exist.")

        return command_preset, task_planning_mode, self._normalize_skill_references(request)

    def _inject_structured_request_context(
        self,
        lc_messages: list[Any],
        *,
        command_preset: dict[str, Any] | None,
        task_planning_mode: bool,
        skill_references: list[dict[str, str]],
    ) -> None:
        if not command_preset and not task_planning_mode and not skill_references:
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
            if skill_references:
                skill_lines = ["[SKILL REFERENCES]"]
                for skill in skill_references:
                    if skill.get("id"):
                        skill_lines.append(f"  id: {skill['id']}")
                    skill_lines.append(f"- name: {skill.get('name') or 'unknown-skill'}")
                    if skill.get("description"):
                        skill_lines.append(f"  description: {skill['description']}")
                    if skill.get("path"):
                        skill_lines.append(f"  path: {skill['path']}")
                    if skill.get("sourceType"):
                        skill_lines.append(f"  sourceType: {skill['sourceType']}")
                    if skill.get("workspacePath"):
                        skill_lines.append(f"  workspacePath: {skill['workspacePath']}")
                    if skill.get("workspaceId"):
                        skill_lines.append(f"  workspaceId: {skill['workspaceId']}")
                    if skill.get("projectId"):
                        skill_lines.append(f"  projectId: {skill['projectId']}")
                skill_lines.append("[/SKILL REFERENCES]")
                wrapped_sections.append("\n".join(skill_lines))
            if task_planning_mode:
                wrapped_sections.append(
                    "\n".join(
                        [
                            "[TASK PLANNING MODE]",
                            "请把本轮请求视为任务执行请求，优先给出结构化计划、执行步骤、风险与依赖。",
                            "如果任务具备多步骤、依赖关系、执行过程或需要追踪进度，请优先创建并维护 todos。",
                            "如果你判断本轮不需要 todos，也请按单步任务处理，而不是假装进入任务规划流程。",
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
        evidence = dict(getattr(result, "evidence", {}) or {})
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
            "rebind_reason": str(evidence.get("rebind_reason") or "").strip() or None,
            "previous_scope": str(evidence.get("previous_scope") or "").strip() or None,
            "next_scope": str(evidence.get("next_scope") or "").strip() or None,
            "scope_anchor_comparison": evidence.get("scope_anchor_comparison") if isinstance(evidence.get("scope_anchor_comparison"), dict) else None,
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
        attachments = self._normalize_request_attachments(request)
        self._ensure_latest_user_content_for_attachments(request, attachments)
        lc_messages = self._to_langchain_messages(request)
        self._inject_uploaded_file_notices(request, lc_messages)
        command_preset, task_planning_mode, skill_references = self._resolve_request_context(request)
        self._inject_structured_request_context(
            lc_messages,
            command_preset=command_preset,
            task_planning_mode=task_planning_mode,
            skill_references=skill_references,
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
            skill_references=skill_references,
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
            thread_id=prepared.request.thread_id,
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
            "workspace_path": chat_run.scope_result.binding.workspace_path,
            "workflow_id": chat_run.scope_result.binding.workflow_id,
            "resolved_scope": chat_run.scope_result.binding.resolved_scope,
            "scope_source": chat_run.scope_result.binding.scope_source,
            "scope_chain": list(chat_run.scope_result.scope_chain or []),
        }
        if chat_run.prepared.command_preset_name:
            metadata["commandPreset"] = {
                "name": chat_run.prepared.command_preset_name,
                "contentHash": chat_run.prepared.command_preset_hash,
            }
        if chat_run.prepared.task_planning_mode:
            metadata["taskPlanningMode"] = True
        if chat_run.prepared.skill_references:
            metadata["skillReferences"] = list(chat_run.prepared.skill_references)

        if not chat_run.is_resume_request and request.messages and request.messages[-1].role == "user":
            latest_user = request.messages[-1]
            user_message_id = str(uuid.uuid4())
            user_node_id = f"{user_message_id}:narrative"
            attachments = [dict(item) for item in list(request.attachments or []) if isinstance(item, dict)]
            attachment_nodes = [
                {
                    "id": f"{user_message_id}:artifact:{index}",
                    "kind": "artifact",
                    "artifact": {
                        "id": str(attachment.get("id") or f"{user_message_id}:attachment:{index}"),
                        "kind": "file",
                        "title": self._attachment_name(attachment),
                        "displayLabel": self._attachment_name(attachment),
                        "sourcePath": self._attachment_url(attachment),
                        "workspacePath": attachment.get("workspacePath") or attachment.get("workspace_path"),
                        "externalUrl": attachment.get("publicUrl") or attachment.get("public_url") or attachment.get("url"),
                        "previewUrl": attachment.get("publicUrl") or attachment.get("public_url") or attachment.get("url"),
                        "mimeType": attachment.get("mimeType") or attachment.get("mime_type") or attachment.get("type"),
                        "size": attachment.get("size"),
                        "metadata": {"source": attachment.get("source") or "chat_attachment"},
                    },
                    "timestamp": self._now_timestamp_ms(),
                }
                for index, attachment in enumerate(attachments)
            ]
            user_metadata = self._canonical_message_metadata(
                chat_run,
                role="user",
                images=request.fileUrls,
                extra={"attachments": attachments} if attachments else None,
            )
            user_row = self._create_canonical_message(
                chat_run,
                message_id=user_message_id,
                role="user",
                state="completed",
                nodes=[
                    {
                        "id": user_node_id,
                        "kind": "narrative",
                        "role": "user",
                        "content": latest_user.content,
                        "timestamp": user_metadata["timestamp"],
                    },
                    *attachment_nodes,
                ],
                metadata=user_metadata,
                content_text=latest_user.content,
            )
            db.add_message(
                msg_id=user_message_id,
                session_id=chat_run.session_id,
                role="user",
                content=latest_user.content,
                images=request.fileUrls,
                metadata={**metadata, "attachments": attachments} if attachments else metadata,
            )
            chat_run.emit_runtime_event(
                "message.user.recorded",
                {
                    "message_id": user_message_id,
                    "node_id": user_node_id,
                    "transcript_version": int(user_row.get("version") or 1),
                    "content": latest_user.content,
                    "images": request.fileUrls or [],
                    "attachments": attachments,
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
            if chat_run.prepared.skill_references:
                chat_run.emit_runtime_event(
                    "chat.skill_references.applied",
                    {
                        "messageId": user_message_id,
                        "skills": list(chat_run.prepared.skill_references),
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
                    "attachments": attachments,
                    "transport": chat_run.transport,
                    "resolved_scope": chat_run.scope_result.binding.resolved_scope,
                    "command_preset_name": chat_run.prepared.command_preset_name,
                    "task_planning_mode": chat_run.prepared.task_planning_mode,
                    "skill_references": list(chat_run.prepared.skill_references),
                },
            )
            chat_run.run_handle.refresh_chat_snapshot()

        if not chat_run.is_resume_request and request.tool_outputs:
            for tool_output in request.tool_outputs:
                tool_message_id = str(uuid.uuid4())
                tool_node_id = f"{tool_message_id}:tool_result:{tool_output.tool_call_id or 'ask_user'}"
                tool_metadata = self._canonical_message_metadata(
                    chat_run,
                    role="tool",
                    extra={
                        "tool_call_id": tool_output.tool_call_id,
                        "tool_name": tool_output.name or "ask_user",
                    },
                )
                tool_row = self._create_canonical_message(
                    chat_run,
                    message_id=tool_message_id,
                    role="tool",
                    state="completed",
                    nodes=[
                        {
                            "id": tool_node_id,
                            "kind": "execution",
                            "executionType": "tool_result",
                            "toolCallId": tool_output.tool_call_id,
                            "toolName": tool_output.name or "ask_user",
                            "result": tool_output.output,
                            "timestamp": tool_metadata["timestamp"],
                        }
                    ],
                    metadata=tool_metadata,
                    content_text=tool_output.output,
                )
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
                        "node_id": tool_node_id,
                        "transcript_version": int(tool_row.get("version") or 1),
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

    @staticmethod
    def _now_timestamp_ms() -> int:
        return int(time.time() * 1000)

    def _canonical_message_metadata(
        self,
        chat_run: ChatRunContext,
        *,
        role: str,
        profile: dict[str, str] | None = None,
        images: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        binding = chat_run.scope_result.binding
        metadata: dict[str, Any] = {
            "run_id": chat_run.active_run_id,
            "runId": chat_run.active_run_id,
            "transport": chat_run.transport,
            "project_id": getattr(binding, "project_id", None),
            "workspace_id": getattr(binding, "workspace_id", None),
            "workspace_path": getattr(binding, "workspace_path", None),
            "resolved_scope": getattr(binding, "resolved_scope", None),
            "scope_source": getattr(binding, "scope_source", None),
            "scope_chain": list(getattr(chat_run.scope_result, "scope_chain", []) or []),
            "timestamp": self._now_timestamp_ms(),
            "role": role,
        }
        if profile:
            metadata.update(
                {
                    "agentName": profile.get("name"),
                    "agentAvatar": profile.get("avatar"),
                    "agentRoleLabel": profile.get("roleLabel"),
                }
            )
        if images:
            metadata["images"] = list(images)
        if extra:
            metadata.update(extra)
        return {key: value for key, value in metadata.items() if value is not None}

    def _create_canonical_message(
        self,
        chat_run: ChatRunContext,
        *,
        message_id: str,
        role: str,
        state: str,
        nodes: list[dict[str, Any]],
        metadata: dict[str, Any],
        run_id: str | None = None,
        content_text: str | None = None,
        reasoning_text: str | None = None,
    ) -> dict[str, Any]:
        ordinal = db.get_next_chat_canonical_ordinal(chat_run.session_id)
        return canonical_transcript_builder.create_message(
            message_id=message_id,
            session_id=chat_run.session_id,
            run_id=run_id,
            ordinal=ordinal,
            role=role,
            state=state,
            metadata=metadata,
            nodes=nodes,
            content_text=content_text,
            reasoning_text=reasoning_text,
        )

    def _ensure_assistant_canonical_message(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> str:
        if stream_state.assistant_message_id:
            return stream_state.assistant_message_id
        message_id = str(uuid.uuid4())
        profile = self._get_agent_profile(stream_state.current_agent)
        metadata = self._canonical_message_metadata(
            chat_run,
            role="assistant",
            profile=profile,
            extra={"agentId": stream_state.current_agent},
        )
        self._create_canonical_message(
            chat_run,
            message_id=message_id,
            role="assistant",
            state="streaming",
            nodes=[],
            metadata=metadata,
            run_id=chat_run.active_run_id,
        )
        stream_state.assistant_message_id = message_id
        stream_state.assistant_transcript_version = 1
        return message_id

    def _upsert_canonical_node(
        self,
        *,
        nodes: list[dict[str, Any]],
        node: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str]:
        node_id = str(node.get("id") or "").strip() or str(uuid.uuid4())
        normalized_node = {**node, "id": node_id}
        for index, existing in enumerate(nodes):
            if str(existing.get("id") or "").strip() == node_id:
                nodes[index] = {**existing, **normalized_node}
                return nodes, node_id
        nodes.append(normalized_node)
        return nodes, node_id

    def _append_canonical_node(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        *,
        node: dict[str, Any],
        metadata_updates: dict[str, Any] | None = None,
        state: str = "streaming",
        finalize: bool = False,
    ) -> tuple[str, int]:
        message_id = self._ensure_assistant_canonical_message(chat_run, stream_state)
        mutation = canonical_transcript_builder.mutate_message(
            message_id,
            lambda nodes, metadata: self._upsert_canonical_node(nodes=nodes, node=node),
            state=state,
            metadata_updates=metadata_updates,
            finalize=finalize,
        )
        stream_state.assistant_transcript_version = mutation.version
        return mutation.node_id or str(node.get("id") or ""), mutation.version

    def _emit_message_targeted_runtime_event(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        *,
        topic: str,
        payload: dict[str, Any],
        node: dict[str, Any] | None = None,
        agent_id: str | None = None,
        runtime_node: str | None = None,
        state: str = "streaming",
        finalize: bool = False,
    ) -> dict[str, Any]:
        message_id = self._ensure_assistant_canonical_message(chat_run, stream_state)
        enriched_payload = dict(payload)
        if node is not None:
            node_id, version = self._append_canonical_node(
                chat_run,
                stream_state,
                node=node,
                state=state,
                finalize=finalize,
            )
            enriched_payload["message_id"] = message_id
            enriched_payload["node_id"] = node_id
            enriched_payload["transcript_version"] = version
        else:
            mutation = canonical_transcript_builder.set_message_state(
                message_id,
                state=state,
                finalize=finalize,
            )
            stream_state.assistant_transcript_version = mutation.version
            enriched_payload["message_id"] = message_id
            enriched_payload["transcript_version"] = mutation.version
        return chat_run.emit_runtime_event(
            topic,
            enriched_payload,
            agent_id=agent_id or stream_state.current_agent,
            node=runtime_node or stream_state.current_agent,
        )

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
        self._ensure_assistant_canonical_message(chat_run, stream_state)
        agent_start_node = {
            "id": f"{stream_state.assistant_message_id}:agent_start:{stream_state.current_agent}",
            "kind": "execution",
            "executionType": "agent_start",
            "timestamp": self._now_timestamp_ms(),
            "agentName": init_profile["name"],
            "agentAvatar": init_profile["avatar"],
            "agentRoleLabel": init_profile["roleLabel"],
        }
        init_agent_event = {
            "type": "agent_start",
            "message_id": stream_state.assistant_message_id,
            "agent": {
                "id": stream_state.current_agent,
                "name": init_profile["name"],
                "avatar": init_profile["avatar"],
                "roleLabel": init_profile["roleLabel"],
            },
        }
        runtime_event = self._emit_message_targeted_runtime_event(
            chat_run,
            stream_state,
            topic="agent.started",
            payload=init_agent_event,
            node=agent_start_node,
            agent_id=stream_state.current_agent,
            runtime_node=stream_state.current_agent,
        )
        init_agent_event["node_id"] = agent_start_node["id"]
        init_agent_event["transcript_version"] = stream_state.assistant_transcript_version
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
        safety_guardian.log_decision_event(
            action="chat_preflight",
            decision=decision,
            subject=chat_run.prepared.latest_user_content or chat_run.session_id,
            metadata={"runId": chat_run.active_run_id, "sessionId": chat_run.session_id},
        )
        if decision.is_allow():
            return []

        request_payload = safety_guardian.build_runtime_preflight_request(
            runtime_kind="chat",
            trigger_source=chat_run.transport,
            decision=decision,
            subject=chat_run.prepared.latest_user_content or chat_run.session_id,
        )

        if decision.is_review():
            approval = chat_run.run_handle.request_approval(
                approval_kind="safety_review",
                request=request_payload,
            )
            if str(approval.get("status") or "").strip().lower() != "pending":
                chat_run.run_handle.refresh_chat_snapshot()
                return []
            chat_run.run_handle.refresh_chat_snapshot()
            return [{"type": "done", "status": "waiting_approval", "run_id": chat_run.active_run_id}]

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
        return [
            self._build_safety_blocked_event(
                chat_run,
                reason=decision.reason,
                risk_code=decision.risk_code,
                details=decision.details,
                request_payload=request_payload,
            ),
            {"type": "done", "status": "blocked", "run_id": chat_run.active_run_id},
        ]

    @staticmethod
    def _clear_text_flush_deadline(stream_state: ChatStreamState) -> None:
        stream_state.text_flush_deadline = None

    def _schedule_text_flush_deadline(self, stream_state: ChatStreamState) -> None:
        if not stream_state.text_aggregator.has_buffered_content():
            stream_state.text_flush_deadline = None
            return
        if stream_state.text_flush_deadline is not None:
            return
        stream_state.text_flush_deadline = asyncio.get_running_loop().time() + self.TEXT_FLUSH_INTERVAL_SECONDS

    async def _emit_stable_text_chunk(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        stable_chunk: str,
        *,
        model_run_id: str,
        snapshot: str | None = None,
    ) -> dict[str, Any] | None:
        if not stable_chunk:
            return None
        profile = self._get_agent_profile(stream_state.current_agent)
        run_key = self._normalized_stream_run_id(model_run_id)
        node_content = snapshot or stream_state.text_snapshots_by_run.get(run_key) or stable_chunk
        text_event = {"type": "text_chunk", "content": stable_chunk, "snapshot": node_content, "timestamp": 0}
        narrative_node = {
            "id": f"{self._ensure_assistant_canonical_message(chat_run, stream_state)}:narrative:{run_key}",
            "kind": "narrative",
            "role": "assistant",
            "content": node_content,
            "timestamp": self._now_timestamp_ms(),
            "agentName": profile["name"],
            "agentAvatar": profile["avatar"],
            "agentRoleLabel": profile["roleLabel"],
        }
        runtime_event = self._emit_message_targeted_runtime_event(
            chat_run,
            stream_state,
            topic="run.text.delta",
            payload=text_event,
            node=narrative_node,
            agent_id=stream_state.current_agent,
            runtime_node=stream_state.current_agent,
        )
        payload = runtime_event.get("payload") if isinstance(runtime_event, dict) else None
        if isinstance(payload, dict):
            text_event["message_id"] = payload.get("message_id")
            text_event["node_id"] = payload.get("node_id")
            text_event["transcript_version"] = payload.get("transcript_version")
        workflow_ledger_service.append_chat_projection(
            session_id=chat_run.session_id,
            run_id=chat_run.active_run_id,
            text_delta=stable_chunk,
            agent_profile=profile,
            latest_seq=runtime_event.get("seq"),
        )
        stream_state.text_emitted_chunks += 1
        return text_event

    def _emit_reasoning_delta(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        reasoning_delta: str,
        *,
        model_run_id: str,
        snapshot: str | None = None,
    ) -> dict[str, Any] | None:
        if not reasoning_delta:
            return None
        profile = self._get_agent_profile(stream_state.current_agent)
        run_key = self._normalized_stream_run_id(model_run_id)
        node_content = snapshot or stream_state.reasoning_snapshots_by_run.get(run_key) or reasoning_delta
        reasoning_event = {
            "type": "reasoning_chunk",
            "content": reasoning_delta,
            "snapshot": node_content,
            "timestamp": 0,
        }
        reasoning_node = {
            "id": f"{self._ensure_assistant_canonical_message(chat_run, stream_state)}:reasoning:{run_key}",
            "kind": "execution",
            "executionType": "reasoning",
            "content": node_content,
            "timestamp": self._now_timestamp_ms(),
            "agentName": profile["name"],
            "agentAvatar": profile["avatar"],
            "agentRoleLabel": profile["roleLabel"],
        }
        runtime_event = self._emit_message_targeted_runtime_event(
            chat_run,
            stream_state,
            topic="run.reasoning.delta",
            payload=reasoning_event,
            node=reasoning_node,
            agent_id=stream_state.current_agent,
            runtime_node=stream_state.current_agent,
        )
        payload = runtime_event.get("payload") if isinstance(runtime_event, dict) else None
        if isinstance(payload, dict):
            reasoning_event["message_id"] = payload.get("message_id")
            reasoning_event["node_id"] = payload.get("node_id")
            reasoning_event["transcript_version"] = payload.get("transcript_version")
        workflow_ledger_service.append_chat_projection(
            session_id=chat_run.session_id,
            run_id=chat_run.active_run_id,
            reasoning_delta=reasoning_delta,
            agent_profile=profile,
            latest_seq=runtime_event.get("seq"),
        )
        return reasoning_event

    async def _emit_text_delta(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        delta: str,
        *,
        model_run_id: str,
        snapshot: str | None = None,
    ) -> list[dict[str, Any]]:
        emitted_events: list[dict[str, Any]] = []
        if not delta:
            return emitted_events
        for stable_chunk in stream_state.text_aggregator.push(delta):
            if not stable_chunk:
                continue
            text_event = await self._emit_stable_text_chunk(
                chat_run,
                stream_state,
                stable_chunk,
                model_run_id=model_run_id,
                snapshot=snapshot or self._current_canonical_text(stream_state),
            )
            if text_event is not None:
                emitted_events.append(text_event)
        if emitted_events or not stream_state.text_aggregator.has_buffered_content():
            self._clear_text_flush_deadline(stream_state)
        else:
            self._schedule_text_flush_deadline(stream_state)
        return emitted_events

    async def _flush_pending_text_aggregator(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        *,
        from_timer: bool = False,
        final: bool = False,
    ) -> list[dict[str, Any]]:
        self._clear_text_flush_deadline(stream_state)
        final_chunk = stream_state.text_aggregator.flush()
        if not final_chunk:
            return []
        stream_state.watchdog.note_text_progress()
        if from_timer:
            stream_state.text_timer_flushes += 1
        if final:
            stream_state.text_final_flush_chars += len(final_chunk)
        text_event = await self._emit_stable_text_chunk(
            chat_run,
            stream_state,
            final_chunk,
            model_run_id=stream_state.last_text_delta_run_id,
            snapshot=self._current_canonical_text(stream_state),
        )
        return [text_event] if text_event is not None else []

    def _emit_text_stream_diagnostics(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> None:
        if (
            stream_state.text_raw_chars <= 0
            and stream_state.text_emitted_chunks <= 0
            and stream_state.text_timer_flushes <= 0
            and stream_state.text_final_flush_chars <= 0
        ):
            return
        chat_run.emit_runtime_event(
            "run.text_stream.diagnostics",
            {
                "rawTextChars": stream_state.text_raw_chars,
                "emittedTextChunkCount": stream_state.text_emitted_chunks,
                "timerFlushCount": stream_state.text_timer_flushes,
                "finalFlushChars": stream_state.text_final_flush_chars,
                "flushIntervalMs": int(self.TEXT_FLUSH_INTERVAL_SECONDS * 1000),
            },
            agent_id=None,
            node="stream_chunk_aggregator",
        )

    async def _cancel_pending_stream_event_task(self, stream_state: ChatStreamState) -> None:
        task = stream_state.pending_stream_event_task
        stream_state.pending_stream_event_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, StopAsyncIteration, Exception):
            return

    async def _wait_for_stream_signal(
        self,
        *,
        stream_iter,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
    ) -> tuple[str, dict[str, Any] | None]:
        idle_timeout = stream_state.watchdog.idle_timeout_seconds()
        phase = stream_state.watchdog.idle_phase()
        loop = asyncio.get_running_loop()
        now = loop.time()
        effective_timeout = idle_timeout
        selected_deadline_kind = "idle_watchdog"
        if stream_state.text_flush_deadline is not None and stream_state.text_aggregator.has_buffered_content():
            flush_timeout = max(stream_state.text_flush_deadline - now, 0.0)
            if flush_timeout <= effective_timeout:
                effective_timeout = flush_timeout
                selected_deadline_kind = "text_flush"
        if stream_state.pending_stream_event_task is None:
            stream_state.pending_stream_event_task = asyncio.create_task(anext(stream_iter))
        next_event_task = stream_state.pending_stream_event_task
        done, _ = await asyncio.wait({next_event_task}, timeout=effective_timeout)
        if next_event_task not in done:
            if selected_deadline_kind == "text_flush":
                return "text_flush", None

            payload = {
                "idleTimeoutSeconds": idle_timeout,
                "configuredIdleTimeoutSeconds": idle_timeout,
                "effectiveTimeoutSeconds": effective_timeout,
                "deadlineKind": selected_deadline_kind,
                "phase": phase,
                "activeToolCount": len(stream_state.watchdog.active_tool_call_ids),
                "lastObservedEvent": stream_state.watchdog.last_observed_event,
            }
            await self._cancel_pending_stream_event_task(stream_state)
            chat_run.emit_runtime_event(
                "run.watchdog.stream_idle_timeout",
                payload,
                agent_id=None,
                node="stream_watchdog",
            )
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
            )
            raise GraphStreamIdleTimeoutError(
                run_id=chat_run.active_run_id,
                session_id=chat_run.session_id,
                idle_seconds=idle_timeout,
                phase=phase,
                last_event=stream_state.watchdog.last_observed_event,
            )

        stream_state.pending_stream_event_task = None
        try:
            event = next_event_task.result()
        except Exception as exc:
            normalized_exc = normalize_stream_iterator_exception(
                exc,
                session_id=chat_run.session_id,
                run_id=chat_run.active_run_id,
                phase=phase,
                last_event=stream_state.watchdog.last_observed_event,
            )
            if isinstance(normalized_exc, GraphStreamDownstreamTimeoutError):
                chat_run.emit_runtime_event(
                    "run.stream.downstream_timeout",
                    {
                        "phase": phase,
                        "idleTimeoutSeconds": idle_timeout,
                        "configuredIdleTimeoutSeconds": idle_timeout,
                        "effectiveTimeoutSeconds": effective_timeout,
                        "deadlineKind": "stream_event",
                        "lastObservedEvent": stream_state.watchdog.last_observed_event,
                        "message": str(normalized_exc),
                    },
                    agent_id=None,
                    node="stream_watchdog",
                )
            raise normalized_exc from exc

        stream_state.watchdog.observe_event(event)
        return "graph_event", event

    @staticmethod
    def _longest_overlap_suffix_prefix(previous: str, current: str) -> int:
        max_overlap = min(len(previous), len(current))
        for size in range(max_overlap, 0, -1):
            if previous[-size:] == current[:size]:
                return size
        return 0

    def _consume_stream_suffix(
        self,
        snapshots: dict[str, str],
        model_run_id: str,
        raw_value: str,
    ) -> str:
        normalized_run_id = (model_run_id or "").strip() or "__default__"
        current_value = raw_value or ""
        if not current_value:
            return ""

        previous_value = snapshots.get(normalized_run_id, "")
        if not previous_value:
            snapshots[normalized_run_id] = current_value
            return current_value

        if current_value == previous_value:
            return ""

        if current_value.startswith(previous_value):
            suffix = current_value[len(previous_value):]
            snapshots[normalized_run_id] = current_value
            return suffix

        if previous_value.endswith(current_value) or current_value in previous_value:
            return ""

        overlap = self._longest_overlap_suffix_prefix(previous_value, current_value)
        if overlap > 0:
            suffix = current_value[overlap:]
            snapshots[normalized_run_id] = previous_value + suffix
            return suffix

        snapshots[normalized_run_id] = current_value
        return ""

    @staticmethod
    def _current_canonical_text(stream_state: ChatStreamState) -> str:
        return "".join(stream_state.output_buffer)

    @staticmethod
    def _has_started_narrative_output(stream_state: ChatStreamState, *, raw_text: str = "") -> bool:
        return bool(raw_text or stream_state.output_buffer or stream_state.text_aggregator.has_buffered_content())

    def _consume_terminal_text_suffix(
        self,
        stream_state: ChatStreamState,
        snapshots: dict[str, str],
        model_run_id: str,
        raw_value: str,
    ) -> str:
        normalized_run_id = (model_run_id or "").strip() or "__default__"
        current_value = raw_value or ""
        if not current_value:
            return ""

        snapshots[normalized_run_id] = current_value
        stream_state.authoritative_final_text = current_value

        emitted_text = self._current_canonical_text(stream_state)
        if not emitted_text:
            return current_value

        if current_value == emitted_text:
            return ""

        if current_value.startswith(emitted_text):
            return current_value[len(emitted_text):]

        if emitted_text.endswith(current_value) or current_value in emitted_text:
            return ""

        overlap = self._longest_overlap_suffix_prefix(emitted_text, current_value)
        if overlap > 0:
            return current_value[overlap:]

        return ""

    @staticmethod
    def _normalized_stream_run_id(model_run_id: str) -> str:
        return (model_run_id or "").strip() or "__default__"

    @classmethod
    def _normalize_tool_arg_key(cls, key: object) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(key or "").strip().lower())

    @classmethod
    def _sanitize_tool_input_value(cls, value: Any) -> Any:
        sentinel = object()

        def _sanitize(item: Any) -> Any:
            if item is None or isinstance(item, (str, int, float, bool)):
                return item
            if isinstance(item, dict):
                cleaned: dict[str, Any] = {}
                for raw_key, raw_value in item.items():
                    if cls._normalize_tool_arg_key(raw_key) in cls.TOOL_INPUT_INTERNAL_KEYS:
                        continue
                    sanitized_child = _sanitize(raw_value)
                    if sanitized_child is sentinel:
                        continue
                    cleaned[str(raw_key)] = sanitized_child
                return cleaned
            if isinstance(item, list):
                return [sanitized_child for child in item if (sanitized_child := _sanitize(child)) is not sentinel]
            return sentinel

        sanitized = _sanitize(to_jsonable(value))
        return {} if sanitized is sentinel else sanitized

    def _suppress_neighbor_duplicate_delta(
        self,
        stream_state: ChatStreamState,
        *,
        delta: str,
        model_run_id: str,
        kind: str,
    ) -> str:
        normalized_delta = delta or ""
        if not normalized_delta:
            return ""

        normalized_run_id = self._normalized_stream_run_id(model_run_id)
        if kind == "reasoning":
            last_delta = stream_state.last_reasoning_delta
            last_run_id = stream_state.last_reasoning_delta_run_id
        else:
            last_delta = stream_state.last_text_delta
            last_run_id = stream_state.last_text_delta_run_id

        if last_delta and normalized_delta == last_delta:
            return ""

        if last_delta and normalized_run_id != last_run_id:
            if normalized_delta.startswith(last_delta):
                normalized_delta = normalized_delta[len(last_delta):]
            elif last_delta.startswith(normalized_delta) or normalized_delta in last_delta:
                return ""
            else:
                overlap = self._longest_overlap_suffix_prefix(last_delta, normalized_delta)
                if overlap > 0:
                    normalized_delta = normalized_delta[overlap:]

        if not normalized_delta:
            return ""

        if kind == "reasoning":
            stream_state.last_reasoning_delta = delta or ""
            stream_state.last_reasoning_delta_run_id = normalized_run_id
        else:
            stream_state.last_text_delta = delta or ""
            stream_state.last_text_delta_run_id = normalized_run_id
        return normalized_delta

    def _maybe_agent_start_event(self, chat_run: ChatRunContext, stream_state: ChatStreamState, metadata: dict[str, Any]) -> dict[str, Any] | None:
        node_name = metadata.get("langgraph_node", "")
        if not node_name or node_name not in stream_state.valid_agent_node_names or node_name == stream_state.current_agent:
            return None
        stream_state.current_agent = node_name
        profile = self._get_agent_profile(node_name)
        agent_start_node = {
            "id": f"{self._ensure_assistant_canonical_message(chat_run, stream_state)}:agent_start:{node_name}:{self._now_timestamp_ms()}",
            "kind": "execution",
            "executionType": "agent_start",
            "timestamp": self._now_timestamp_ms(),
            "agentName": profile["name"],
            "agentAvatar": profile["avatar"],
            "agentRoleLabel": profile["roleLabel"],
        }
        agent_event = {
            "type": "agent_start",
            "message_id": stream_state.assistant_message_id,
            "agent": {
                "id": node_name,
                "name": profile["name"],
                "avatar": profile["avatar"],
                "roleLabel": profile["roleLabel"],
            },
        }
        runtime_event = self._emit_message_targeted_runtime_event(
            chat_run,
            stream_state,
            topic="agent.started",
            payload=agent_event,
            node=agent_start_node,
            agent_id=node_name,
            runtime_node=node_name,
        )
        agent_event["node_id"] = agent_start_node["id"]
        agent_event["transcript_version"] = stream_state.assistant_transcript_version
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
                approval_kind = interrupt_request.get("approvalKind") or "human_input_required"
                approval = chat_run.run_handle.request_approval(
                    approval_kind=approval_kind,
                    request=interrupt_request,
                )
                if str(approval.get("status") or "").strip().lower() != "pending":
                    chat_run.run_handle.refresh_chat_snapshot()
                    return emitted_events
                chat_run.run_handle.refresh_chat_snapshot()
                if self._is_ask_user_request(interrupt_request):
                    emitted_events.append(
                        self._build_ask_user_event(
                            chat_run,
                            request_payload=interrupt_request,
                            approval=approval,
                        )
                    )
                stream_state.interrupted_signal = {
                    "command": "approval_requested",
                    "reason": approval.get("approval_kind") or approval_kind,
                    "payload": {"approval_id": approval.get("approval_id")},
                }
                return emitted_events

        if kind == "on_chat_model_stream":
            if stream_state.active_tool_call_ids:
                return emitted_events
            model_events = canonical_model_event_adapter.normalize_chat_model_stream(
                event,
                text_snapshots=stream_state.text_snapshots_by_run,
                reasoning_snapshots=stream_state.reasoning_snapshots_by_run,
            )
            for model_event in model_events:
                model_run_id = model_event.model_run_id
                stream_state.streamed_model_run_ids.add(model_run_id)
                if model_event.event_type == "text_delta":
                    text_delta = model_event.delta
                    stream_state.text_raw_chars += len(text_delta)
                    text_delta = stream_state.text_filter.process(text_delta)
                    text_delta = self._suppress_neighbor_duplicate_delta(
                        stream_state,
                        delta=text_delta,
                        model_run_id=model_run_id,
                        kind="text",
                    )
                    if not text_delta:
                        continue
                    stream_state.watchdog.note_text_progress()
                    stream_state.output_buffer.append(text_delta)
                    stream_state.narrative_started_model_run_ids.add(self._normalized_stream_run_id(model_run_id))
                    emitted_events.extend(
                        await self._emit_text_delta(
                            chat_run,
                            stream_state,
                            text_delta,
                            model_run_id=model_run_id,
                            snapshot=self._current_canonical_text(stream_state),
                        )
                    )
                elif model_event.event_type == "reasoning_delta":
                    reasoning_delta = self._suppress_neighbor_duplicate_delta(
                        stream_state,
                        delta=model_event.delta,
                        model_run_id=model_run_id,
                        kind="reasoning",
                    )
                    if not reasoning_delta:
                        continue
                    stream_state.watchdog.note_text_progress()
                    stream_state.reasoning_buffer.append(reasoning_delta)
                    reasoning_event = self._emit_reasoning_delta(
                        chat_run,
                        stream_state,
                        reasoning_delta,
                        model_run_id=model_run_id,
                        snapshot=model_event.snapshot,
                    )
                    if reasoning_event is not None:
                        emitted_events.append(reasoning_event)
            return emitted_events

        if kind == "on_chat_model_end":
            if stream_state.active_tool_call_ids:
                return emitted_events
            model_run_id = (event.get("run_id") or "").strip()
            model_events = canonical_model_event_adapter.normalize_chat_model_end(
                event,
                text_snapshots=stream_state.text_snapshots_by_run,
                reasoning_snapshots=stream_state.reasoning_snapshots_by_run,
                suppress_reasoning=self._normalized_stream_run_id(model_run_id) in stream_state.narrative_started_model_run_ids,
                emitted_text=self._current_canonical_text(stream_state),
            )
            final_snapshot = stream_state.text_snapshots_by_run.get(self._normalized_stream_run_id(model_run_id))
            if final_snapshot:
                stream_state.authoritative_final_text = final_snapshot
            for model_event in model_events:
                if model_event.event_type == "text_delta":
                    text_delta = model_event.delta
                    stream_state.text_raw_chars += len(text_delta)
                    text_delta = stream_state.text_filter.process(text_delta)
                    text_delta = self._suppress_neighbor_duplicate_delta(
                        stream_state,
                        delta=text_delta,
                        model_run_id=model_event.model_run_id,
                        kind="text",
                    )
                    if not text_delta:
                        continue
                    stream_state.watchdog.note_text_progress()
                    stream_state.output_buffer.append(text_delta)
                    stream_state.narrative_started_model_run_ids.add(self._normalized_stream_run_id(model_event.model_run_id))
                    emitted_events.extend(
                        await self._emit_text_delta(
                            chat_run,
                            stream_state,
                            text_delta,
                            model_run_id=model_event.model_run_id,
                            snapshot=self._current_canonical_text(stream_state),
                        )
                    )
                elif model_event.event_type == "reasoning_delta":
                    reasoning_delta = self._suppress_neighbor_duplicate_delta(
                        stream_state,
                        delta=model_event.delta,
                        model_run_id=model_event.model_run_id,
                        kind="reasoning",
                    )
                    if not reasoning_delta:
                        continue
                    stream_state.watchdog.note_text_progress()
                    stream_state.reasoning_buffer.append(reasoning_delta)
                    reasoning_event = self._emit_reasoning_delta(
                        chat_run,
                        stream_state,
                        reasoning_delta,
                        model_run_id=model_event.model_run_id,
                        snapshot=model_event.snapshot,
                    )
                    if reasoning_event is not None:
                        emitted_events.append(reasoning_event)
            return emitted_events

        if kind == "on_tool_start":
            inputs = self._sanitize_tool_input_value(data.get("input", {}))
            tool_call_id = event.get("run_id", "")
            active_tool_key = str(tool_call_id or name or "__unknown_tool__").strip()
            stream_state.active_tool_call_ids.add(active_tool_key)
            tool_start_event = {
                "type": "tool_start",
                "tool": {"toolCallId": tool_call_id, "toolName": name, "args": inputs},
                "timestamp": 0,
            }
            profile = self._get_agent_profile(stream_state.current_agent)
            tool_call_node = {
                "id": f"{self._ensure_assistant_canonical_message(chat_run, stream_state)}:tool_call:{tool_call_id or name}",
                "kind": "execution",
                "executionType": "tool_call",
                "toolCallId": tool_call_id,
                "toolName": name,
                "args": inputs,
                "timestamp": self._now_timestamp_ms(),
                "agentName": profile["name"],
                "agentAvatar": profile["avatar"],
                "agentRoleLabel": profile["roleLabel"],
            }
            runtime_event = self._emit_message_targeted_runtime_event(
                chat_run,
                stream_state,
                topic="tool.started",
                payload=tool_start_event,
                node=tool_call_node,
                agent_id=stream_state.current_agent,
                runtime_node=stream_state.current_agent,
            )
            payload = runtime_event.get("payload") if isinstance(runtime_event, dict) else None
            if isinstance(payload, dict):
                tool_start_event["message_id"] = payload.get("message_id")
                tool_start_event["node_id"] = payload.get("node_id")
                tool_start_event["transcript_version"] = payload.get("transcript_version")
            stream_state.watchdog.note_tool_start(tool_call_id)
            stream_state.tool_calls_buffer.append({"id": tool_call_id, "name": name, "args": inputs})
            emitted_events.append(tool_start_event)
            return emitted_events

        if kind == "on_tool_end":
            output = data.get("output", "")
            tool_call_id = event.get("run_id", "")
            active_tool_key = str(tool_call_id or name or "__unknown_tool__").strip()
            output_str = str(output.content) if hasattr(output, "content") else str(output)
            tool_result_event = {
                "type": "tool_result",
                "tool": {"toolCallId": tool_call_id, "toolName": name, "result": output_str},
                "timestamp": 0,
            }
            stream_state.watchdog.note_tool_end(tool_call_id)
            profile = self._get_agent_profile(stream_state.current_agent)
            tool_result_node = {
                "id": f"{self._ensure_assistant_canonical_message(chat_run, stream_state)}:tool_result:{tool_call_id or name}",
                "kind": "execution",
                "executionType": "tool_result",
                "toolCallId": tool_call_id,
                "toolName": name,
                "result": output_str,
                "timestamp": self._now_timestamp_ms(),
                "agentName": profile["name"],
                "agentAvatar": profile["avatar"],
                "agentRoleLabel": profile["roleLabel"],
            }
            runtime_event = self._emit_message_targeted_runtime_event(
                chat_run,
                stream_state,
                topic="tool.finished",
                payload=tool_result_event,
                node=tool_result_node,
                agent_id=stream_state.current_agent,
                runtime_node=stream_state.current_agent,
            )
            payload = runtime_event.get("payload") if isinstance(runtime_event, dict) else None
            if isinstance(payload, dict):
                tool_result_event["message_id"] = payload.get("message_id")
                tool_result_event["node_id"] = payload.get("node_id")
                tool_result_event["transcript_version"] = payload.get("transcript_version")
            stream_state.active_tool_call_ids.discard(active_tool_key)
            if not active_tool_key:
                stream_state.active_tool_call_ids.clear()
            emitted_events.append(tool_result_event)
            return emitted_events

        return emitted_events

    async def flush_stream_state(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> list[dict[str, Any]]:
        emitted_events: list[dict[str, Any]] = []
        self._clear_text_flush_deadline(stream_state)
        final_filtered_text = stream_state.text_filter.flush()
        if final_filtered_text:
            stream_state.output_buffer.append(final_filtered_text)
            emitted_events.extend(
                await self._emit_text_delta(
                    chat_run,
                    stream_state,
                    final_filtered_text,
                    model_run_id=stream_state.last_text_delta_run_id,
                )
            )

        emitted_events.extend(await self._flush_pending_text_aggregator(chat_run, stream_state, final=True))
        self._emit_text_stream_diagnostics(chat_run, stream_state)
        return emitted_events

    def persist_final_assistant_message(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> None:
        assistant_message_id = stream_state.assistant_message_id
        if not assistant_message_id:
            return
        canonical_transcript_builder.set_message_state(
            assistant_message_id,
            state="completed",
            metadata_updates={
                "timestamp": self._now_timestamp_ms(),
                "agentId": stream_state.current_agent,
            },
            finalize=True,
        )
        row = db.get_chat_canonical_message(assistant_message_id)
        if not row:
            return
        invariant_errors = validate_canonical_message_invariants(row)
        if invariant_errors:
            error_message = f"Canonical transcript invariant failed: {', '.join(invariant_errors)}"
            chat_run.emit_runtime_event(
                "run.transcript.invariant_failed",
                {"errors": invariant_errors, "messageId": assistant_message_id},
                agent_id=None,
                node="canonical_transcript",
            )
            raise RuntimeError(error_message)
        export_payload = export_legacy_message_payload(row)
        final_content = str(export_payload.get("content") or "")
        if not final_content and not export_payload.get("tool_calls") and not export_payload.get("reasoning_content"):
            return
        db.add_message(
            msg_id=assistant_message_id,
            session_id=chat_run.session_id,
            role=str(export_payload.get("role") or "assistant"),
            content=final_content,
            reasoning_content=export_payload.get("reasoning_content"),
            tool_calls=export_payload.get("tool_calls"),
            images=export_payload.get("images"),
            metadata=export_payload.get("metadata"),
            agent_id=export_payload.get("agent_id"),
            agent_name=export_payload.get("agent_name"),
            agent_avatar=export_payload.get("agent_avatar"),
            agent_role_label=export_payload.get("agent_role_label"),
        )
        db.attach_runtime_artifacts_to_message(
            session_id=chat_run.session_id,
            run_id=chat_run.active_run_id,
            message_id=assistant_message_id,
        )
        workflow_ledger_service.clear_chat_projection(chat_run.active_run_id)

    @staticmethod
    def _extract_final_assistant_text_from_state(state: dict[str, Any] | None) -> str:
        if not isinstance(state, dict):
            return ""
        for message in reversed(list(state.get("messages") or [])):
            if not isinstance(message, AIMessage):
                continue
            raw_text, _raw_reasoning = extract_text_and_reasoning(message)
            if not raw_text and isinstance(getattr(message, "content", None), str):
                raw_text = str(message.content or "")
            raw_text = str(raw_text or "").strip()
            if raw_text:
                return raw_text
        return ""

    @classmethod
    def _should_reconcile_final_text(cls, *, current_text: str, final_text: str) -> bool:
        current = str(current_text or "").strip()
        final = str(final_text or "").strip()
        if not final or final == current:
            return False
        if any(marker in final for marker in ("ToolRuntime(", "PregelScratchpad", "__pregel_", "stream_writer=")):
            return False
        if not current:
            return True
        if len(current) <= 4:
            return True
        if final.startswith(current) or current in final:
            return True
        return len(final) >= max(len(current) * 2, 16)

    async def reconcile_final_assistant_message(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        execution_bundle: ChatExecutionBundle | None,
    ) -> None:
        if execution_bundle is None:
            return
        try:
            state = await supervisor_runner.get_state_snapshot(execution_bundle.runner_bundle)
        except Exception:
            logging.getLogger("v8chat.chat_runtime").exception(
                "Failed to inspect final graph state for transcript reconciliation in run '%s'",
                chat_run.active_run_id,
            )
            return
        final_text = self._extract_final_assistant_text_from_state(state)
        if not final_text:
            return
        message_id = self._ensure_assistant_canonical_message(chat_run, stream_state)
        row = db.get_chat_canonical_message(message_id) or {}
        current_text = str(row.get("content_text") or self._current_canonical_text(stream_state) or "")
        if not self._should_reconcile_final_text(current_text=current_text, final_text=final_text):
            return
        profile = self._get_agent_profile(stream_state.current_agent)
        final_node = {
            "id": f"{message_id}:narrative:final",
            "kind": "narrative",
            "role": "assistant",
            "content": final_text,
            "timestamp": self._now_timestamp_ms(),
            "agentName": profile["name"],
            "agentAvatar": profile["avatar"],
            "agentRoleLabel": profile["roleLabel"],
        }

        def _replace_narrative(nodes: list[dict[str, Any]], _metadata: dict[str, Any]):
            preserved = [
                dict(node)
                for node in nodes
                if not (
                    str(node.get("kind") or "").strip() == "narrative"
                    and str(node.get("role") or "").strip() == "assistant"
                )
            ]
            return [*preserved, final_node], final_node["id"]

        mutation = canonical_transcript_builder.mutate_message(
            message_id,
            _replace_narrative,
            state="streaming",
            metadata_updates={
                "timestamp": self._now_timestamp_ms(),
                "agentId": stream_state.current_agent,
                "transcriptReconciled": True,
            },
        )
        stream_state.assistant_transcript_version = mutation.version
        stream_state.output_buffer = [final_text]
        stream_state.authoritative_final_text = final_text
        chat_run.emit_runtime_event(
            "run.transcript.reconciled",
            {
                "message_id": message_id,
                "node_id": final_node["id"],
                "transcript_version": mutation.version,
                "previousContentChars": len(current_text),
                "finalContentChars": len(final_text),
                "reason": "final_graph_state_authoritative",
            },
            agent_id=None,
            node="canonical_transcript",
        )

    def emit_task_planning_mode_decision(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> None:
        if not chat_run.prepared.task_planning_mode:
            return
        todo_tool_calls = [
            call for call in list(stream_state.tool_calls_buffer or [])
            if str((call or {}).get("name") or "").strip().lower() in {"write_todos", "update_todo"}
        ]
        used_todos = len(todo_tool_calls) > 0
        reason = "todos_used" if used_todos else "single_step_or_not_needed"
        summary = (
            "任务规划偏好已命中 Todo 链"
            if used_todos
            else "任务规划偏好已开启，但本轮按单步任务完成"
        )
        message = (
            "本轮任务已创建或更新 todos，并按任务规划方式推进。"
            if used_todos
            else "本轮没有进入 todos 链，通常表示模型判断当前任务更适合一次性完成或无需持续跟踪。"
        )
        chat_run.emit_runtime_event(
            "chat.task_planning_mode.decided",
            {
                "enabled": True,
                "usedTodos": used_todos,
                "reason": reason,
                "summary": summary,
                "message": message,
                "todoToolCount": len(todo_tool_calls),
                "runId": chat_run.active_run_id,
            },
            agent_id=None,
            node="task_planning",
        )

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
                if self._is_ask_user_request(request_payload):
                    return [
                        self._build_ask_user_event(
                            chat_run,
                            request_payload=request_payload,
                            approval=approval,
                            governance={
                                "message": str(exc),
                                "details": exc.details,
                            },
                        ),
                        {"type": "done", "status": "waiting_approval", "run_id": chat_run.active_run_id},
                    ]
                return [{"type": "done", "status": "waiting_approval", "run_id": chat_run.active_run_id}]
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
            last_execution_bundle: ChatExecutionBundle | None = None
            while True:
                execution_bundle = continuation_bundle or await self.resolve_execution_bundle(chat_run=chat_run)
                last_execution_bundle = execution_bundle
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
                            try:
                                while True:
                                    event = None
                                    try:
                                        signal_kind, event = await self._wait_for_stream_signal(
                                            stream_iter=stream_iter,
                                            chat_run=chat_run,
                                            stream_state=stream_state,
                                        )
                                    except StopAsyncIteration:
                                        break
                                    if signal_kind == "text_flush":
                                        for emitted_event in await self._flush_pending_text_aggregator(
                                            chat_run,
                                            stream_state,
                                            from_timer=True,
                                        ):
                                            yield emitted_event
                                        continue
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
                                        if event is not None:
                                            stream_state.watchdog.finish_event(event)
                            finally:
                                await self._cancel_pending_stream_event_task(stream_state)
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
            await self.reconcile_final_assistant_message(chat_run, stream_state, last_execution_bundle)
            self.persist_final_assistant_message(chat_run, stream_state)
            self.emit_task_planning_mode_decision(chat_run, stream_state)
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
