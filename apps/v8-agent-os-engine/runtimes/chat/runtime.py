from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import time
import uuid
import logging
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

from api.models import ChatRequest, ChatToolCall
from agents.runners.supervisor_runner import SupervisorExecutionBundle, supervisor_runner
from core.chat_output_extractor import extract_text_and_reasoning
from core.delegation_broker import (
    choose_best_external_worker_with_diagnostics,
    choose_best_local_agent_with_diagnostics,
    compact_external_worker_registry_entry,
    expand_delegation_task_briefs,
    normalize_task_brief,
    normalize_task_briefs,
    summarize_capability_snapshot,
)
from core.llm_factory import llm_factory
from core.response_normalizer import V8_CANONICAL_TOOL_CALL_PREFIX, is_v8_canonical_tool_call_id
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
from core.scoped_workspace_resource import (
    build_workspace_resource_ref,
    resolve_scoped_workspace_resource,
)
from core.stream_chunk_aggregator import TextChunkAggregator
from core.storage import storage
from core.context_window_guard import context_window_guard
from core.agents import build_specialist_family_registry, normalize_specialist_family_id
from core.task_shape_classifier import classify_task_shape
from core.workspace_capability import build_workspace_binding
from core.context.workspace import workspace_resolution_service
from erc.chat_canonical_transcript import (
    CanonicalTranscriptBuilder,
    export_legacy_message_payload,
    validate_canonical_message_invariants,
)
from erc.ask_user_tool_result import resolve_ask_user_tool_result_interaction
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
from pydantic import BaseModel, Field
from runtimes.engineering.service import engineering_lane_service
from runtimes.memory.scope_resolution import (
    scope_resolution_service,
    session_scope_binding_service,
)
from core.time_truth import utc_now_iso
from runtimes.network_supervisor.openai_compat import build_external_tool_alias_maps
from runtimes.network_supervisor.compat_errors import CompatBridgeHardStop


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
    planner_mode: str = "off"
    planner_dispatch_mode: str = "suggest"
    planner_intent_diagnostics: dict[str, Any] = field(default_factory=dict)
    task_planning_mode: bool = False
    engineering_mode: str = "auto"
    explicit_engineering_requested: bool = False
    engineering_trigger_decision: dict[str, Any] = field(default_factory=dict)
    engineering_context_pack: dict[str, Any] | None = None
    task_shape_hint: dict[str, Any] = field(default_factory=dict)
    skill_references: list[dict[str, str]] = field(default_factory=list)
    context_mentions: list[dict[str, str]] = field(default_factory=list)
    explicit_subagent_families: list[str] = field(default_factory=list)
    planner_plan: dict[str, Any] | None = None


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


def _compat_ingress_diagnostics_from_request(request: ChatRequest) -> dict[str, Any]:
    try:
        data = getattr(request, "data", None)
        diagnostics = getattr(data, "compat_ingress_diagnostics", None) if data is not None else None
        return dict(diagnostics) if isinstance(diagnostics, dict) else {}
    except Exception:
        return {}


class PlannerTaskBriefPayload(BaseModel):
    taskBriefId: str = ""
    goal: str = ""
    context: str | dict[str, Any] = ""
    writeSet: list[str] = Field(default_factory=list)
    criticalFiles: list[str] = Field(default_factory=list)
    readSet: list[str] = Field(default_factory=list)
    verificationMatrix: list[str] = Field(default_factory=list)
    proofExpectations: list[str] = Field(default_factory=list)
    engineeringTaskCapsule: dict[str, Any] = Field(default_factory=dict)
    behaviorScope: list[str] = Field(default_factory=list)
    requiredCapabilities: list[str] = Field(default_factory=list)
    acceptanceContract: str = ""
    dependency: list[str] = Field(default_factory=list)
    parallelGroup: str = ""
    executionLaneHint: Literal["subagent", "external_worker", "auto"] = "auto"
    familyHint: str = ""
    preferredAgentId: str = ""
    preferredWorkerType: str = ""
    targetCount: int = 1
    workerBriefs: list[dict[str, Any]] = Field(default_factory=list)
    fanoutReason: str = ""


class PlannerTaskNodePayload(BaseModel):
    taskBriefId: str = ""
    title: str = ""
    dependency: list[str] = Field(default_factory=list)
    parallelGroup: str = ""


class PlannerPlanPayload(BaseModel):
    planId: str = ""
    executionStrategy: Literal["direct", "delegate", "mixed"] = "direct"
    planSummary: str = ""
    taskGraph: list[PlannerTaskNodePayload] = Field(default_factory=list)
    taskBriefs: list[PlannerTaskBriefPayload] = Field(default_factory=list)
    globalAcceptanceContract: str = ""
    riskFlags: list[str] = Field(default_factory=list)
    codingPlannerContract: dict[str, Any] = Field(default_factory=dict)
    qualityFlags: list[str] = Field(default_factory=list)
    repairCount: int = 0
    autoDispatchDecision: dict[str, Any] = Field(default_factory=dict)
    dispatchEligibilityReason: str = ""


@dataclass(slots=True)
class ChatStreamState:
    current_agent: str = "supervisor"
    output_buffer: list[str] = field(default_factory=list)
    reasoning_buffer: list[str] = field(default_factory=list)
    authoritative_final_text: str | None = None
    tool_calls_buffer: list[dict[str, Any]] = field(default_factory=list)
    streamed_model_run_ids: set[str] = field(default_factory=set)
    text_snapshots_by_run: dict[str, str] = field(default_factory=dict)
    text_node_snapshots_by_run: dict[str, str] = field(default_factory=dict)
    text_segment_seq_by_run: dict[str, int] = field(default_factory=dict)
    output_text_by_run: dict[str, str] = field(default_factory=dict)
    output_text_run_order: list[str] = field(default_factory=list)
    trace_group_seq: int = 0
    active_trace_group_id: str | None = None
    reasoning_snapshots_by_run: dict[str, str] = field(default_factory=dict)
    last_text_delta: str = ""
    last_text_delta_run_id: str = ""
    last_text_delta_at_ms: int | None = None
    last_reasoning_delta: str = ""
    last_reasoning_delta_run_id: str = ""
    last_graph_event_kind: str = ""
    last_graph_event_at_ms: int | None = None
    text_flush_deadline: float | None = None
    text_raw_chars: int = 0
    text_emitted_chunks: int = 0
    text_timer_flushes: int = 0
    text_timer_deferrals: int = 0
    text_final_flush_chars: int = 0
    watchdog: GraphStreamWatchdogState = field(default_factory=GraphStreamWatchdogState)
    interrupted_signal: dict[str, Any] | None = None
    valid_agent_node_names: list[str] = field(default_factory=list)
    text_filter: StreamFilter = field(default_factory=lambda: StreamFilter(["NONE", "None", "null", "```json", "```"]))
    text_aggregator: TextChunkAggregator = field(default_factory=TextChunkAggregator)
    preserve_stream_timeline: bool = False
    reasoning_surface_contract: dict[str, Any] = field(default_factory=dict)
    pending_stream_event_task: asyncio.Task[Any] | None = None
    assistant_message_id: str | None = None
    assistant_transcript_version: int = 0
    narrative_started_model_run_ids: set[str] = field(default_factory=set)
    active_tool_call_ids: set[str] = field(default_factory=set)
    tool_call_id_by_callback_run_id: dict[str, str] = field(default_factory=dict)
    provider_tool_call_id_to_tool_call_id: dict[str, str] = field(default_factory=dict)
    tool_call_shadow_by_tool_call_id: dict[str, dict[str, str]] = field(default_factory=dict)
    pending_ask_user_interaction_id: str | None = None
    pending_ask_user_tool_call_id: str | None = None
    delegation_claim_detected: bool = False
    delegation_claim_samples: list[str] = field(default_factory=list)
    delegation_dispatch_seen: bool = False
    delegation_claim_diagnostic_emitted: bool = False
    reasoning_suppressed_count: int = 0
    supervisor_tool_step_count: int = 0
    supervisor_project_write_count: int = 0
    supervisor_direct_scope_exceeded_emitted: bool = False
    supervisor_direct_scope_gate_active: bool = False


canonical_transcript_builder = CanonicalTranscriptBuilder()
canonical_model_event_adapter = LangChainCanonicalModelEventAdapter()


class GraphRecursionContinuationBudgetExceeded(RuntimeError):
    def __init__(
        self,
        *,
        continuation_count: int,
        continuation_limit: int,
        recursion_limit: int,
        last_tool: str | None = None,
        last_todo: str | None = None,
    ) -> None:
        self.continuation_count = continuation_count
        self.continuation_limit = continuation_limit
        self.recursion_limit = recursion_limit
        self.last_tool = last_tool
        self.last_todo = last_todo
        super().__init__(
            "长任务已达到 graph continuation 预算，当前 run 保留为可恢复状态；"
            "请继续执行、拆分任务，或派发 Engineering/delegation。"
        )


def _supervisor_direct_scope_operation_fingerprint(run_id: str) -> str:
    return f"supervisor_direct_scope_exception:{str(run_id or '').strip()}"


class ChatRuntime:
    """
    Phase 2 运行时层：
    把聊天请求的生命周期准备、run 启动、scope 绑定、输入落库、
    graph 执行包构建逐步从 routes.py 收口到 ChatRuntime。
    """

    kind = "chat"
    TEXT_FLUSH_INTERVAL_SECONDS = 0.22
    DELEGATION_CLAIM_RE = re.compile(
        r"(派|派发|分配|交给|delegate|dispatch).{0,32}(子\s*agent|子代理|subagent|agent|工程子|工程师)",
        re.IGNORECASE,
    )
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
            "summary": "当前用户请求所在的主对话执行面；Supervisor 已在其中运行，通常无需显式选择它。",
            "responsibilities": [
                "创建与恢复聊天 run",
                "驱动 Supervisor Graph 执行",
                "把输入、流式输出和中断状态同步到账本与投影",
            ],
            "routingKeywords": ["当前对话", "用户请求", "会话恢复", "审批恢复"],
            "acceptedInputs": ["ChatRequest", "resume_run_id", "tool_outputs"],
            "producedOutputs": ["chat_projection", "runtime_events", "workflow_steps"],
            "ownedSteps": ["chat.main", "chat.supervisor_graph"],
            "supportsPause": True,
            "supportsResume": True,
            "supportsApproval": True,
            "supportsRepair": True,
            "visibility": "primary",
            "promptHints": [
                "把 ChatRuntime 视为当前编排容器；需要专门能力时改用 runtime_broker 授权对应 runtime 工具组。",
                "不要把 ChatRuntime 当作可被自己再次调度的下游能力。",
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

    def _is_external_tool_request(self, request_payload: dict[str, Any] | None) -> bool:
        payload = request_payload or {}
        approval_kind = str(payload.get("approvalKind") or payload.get("approval_kind") or "").strip().lower()
        interaction_kind = str(payload.get("interactionKind") or payload.get("interaction_kind") or "").strip().lower()
        external_origin = str(payload.get("externalOrigin") or payload.get("external_origin") or "").strip().lower()
        return interaction_kind == "external_tool" or approval_kind == "external_tool" or external_origin == "network_client"

    def _build_ask_user_event(
        self,
        chat_run: ChatRunContext,
        *,
        request_payload: dict[str, Any],
        interaction: dict[str, Any] | None = None,
        governance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        interaction = interaction or {}
        event_data = {
            "id": interaction.get("id"),
            "interactionId": interaction.get("id"),
            "question": request_payload.get("question") or interaction.get("question"),
            "prompt": request_payload.get("prompt") or interaction.get("prompt"),
            "toolCallId": request_payload.get("toolCallId") or interaction.get("tool_call_id"),
            "interactionKind": request_payload.get("interactionKind") or "ask_user",
            "status": interaction.get("status") or "pending",
            "request": request_payload,
        }
        if interaction.get("assistant_message_id"):
            event_data["assistantMessageId"] = interaction.get("assistant_message_id")
        if governance:
            event_data["governance"] = governance
        return {
            "type": "custom_event",
            "name": "ask_user",
            "run_id": chat_run.active_run_id,
            "data": event_data,
        }

    def _build_ask_user_request_from_tool_call(
        self,
        *,
        args: dict[str, Any],
        tool_call_id: str,
    ) -> dict[str, Any]:
        question = str(
            args.get("question")
            or args.get("prompt")
            or args.get("message")
            or "我需要您的输入以继续执行任务。"
        ).strip() or "我需要您的输入以继续执行任务。"
        request_payload = dict(args or {})
        request_payload["question"] = question
        request_payload["prompt"] = question
        request_payload["interactionKind"] = "ask_user"
        request_payload["approvalKind"] = "ask_user"
        if tool_call_id:
            request_payload["toolCallId"] = tool_call_id
        return request_payload

    def _resolve_ask_user_tool_result_context(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        *,
        candidate_tool_call_id: str,
        output_text: str,
    ) -> dict[str, Any] | None:
        interactions = db.list_ask_user_interactions(
            session_id=chat_run.session_id,
            run_id=chat_run.active_run_id,
            status="resolved",
        )
        return resolve_ask_user_tool_result_interaction(
            interactions,
            pending_interaction_id=stream_state.pending_ask_user_interaction_id,
            candidate_tool_call_id=candidate_tool_call_id,
            output_text=output_text,
        )

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
        wire_to_internal, _internal_to_wire = build_external_tool_alias_maps(request.config.external_tools)
        for message in request.messages:
            if message.role == "user":
                lc_messages.append(HumanMessage(content=message.content))
            elif message.role == "assistant":
                tool_calls_payload: list[dict[str, Any]] = []
                for item in list(message.tool_calls or []):
                    if not isinstance(item, ChatToolCall):
                        continue
                    function_payload = item.function
                    tool_name = str(function_payload.name or "").strip()
                    arguments_text = str(function_payload.arguments or "{}")
                    try:
                        parsed_arguments = json.loads(arguments_text) if arguments_text.strip() else {}
                    except Exception:
                        parsed_arguments = {}
                    tool_calls_payload.append(
                        {
                            "id": str(item.id or "").strip() or None,
                            "name": wire_to_internal.get(tool_name, tool_name),
                            "args": parsed_arguments if isinstance(parsed_arguments, dict) else {},
                            "type": str(item.type or "tool_call").strip() or "tool_call",
                        }
                    )
                if tool_calls_payload:
                    lc_messages.append(AIMessage(content=message.content, tool_calls=tool_calls_payload))
                else:
                    lc_messages.append(AIMessage(content=message.content))
            elif message.role == "system":
                lc_messages.append(SystemMessage(content=message.content))
            elif message.role == "tool":
                lc_messages.append(
                    ToolMessage(
                        content=message.content,
                        tool_call_id=message.tool_call_id,
                        name=wire_to_internal.get(str(message.name or "").strip(), message.name or "unknown"),
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
            if "/api/workspace/files/" in url or "/api/client/workspace/files/" in url:
                marker = "/api/client/workspace/files/" if "/api/client/workspace/files/" in url else "/api/workspace/files/"
                subpath = unquote(url.split(marker)[-1].split("?", 1)[0]).replace("/", os.sep).replace("\\", os.sep)
                workspace_dir = workspace_resolution_service.resolve_workspace_path(
                    runtime_kind="chat",
                    session_id=request.conversation_id or request.session_id,
                    explicit_workspace_id=request.workspace_id,
                    explicit_project_id=request.project_id,
                    explicit_workspace_path=request.workspace_path,
                )
                local_path = Path(workspace_dir) / subpath
                local_files.append(str(local_path.absolute().resolve()))
            elif "/workspace/resource" in url:
                parsed = urlparse(url)
                query = parse_qs(parsed.query or "")
                try:
                    resolved = resolve_scoped_workspace_resource(
                        workspace_relative_path=(query.get("workspace_relative_path") or [""])[0],
                        path_plane=(query.get("path_plane") or [""])[0],
                        workspace_id=(query.get("workspace_id") or [None])[0],
                        project_id=(query.get("project_id") or [None])[0],
                    )
                    local_files.append(str(resolved.absolute_path))
                except Exception:
                    local_files.append(url)
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

    def _normalize_planner_mode(self, value: Any, *, task_planning_mode: bool) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"auto", "force", "off"}:
            return normalized
        if task_planning_mode:
            return "force"
        return "off"

    def _normalize_planner_dispatch_mode(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"suggest", "auto", "off"} else "suggest"

    def _normalize_engineering_mode(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"auto", "force", "off"} else "auto"

    def _detect_explicit_engineering_runtime_request(self, user_content: str) -> bool:
        text = str(user_content or "").strip().lower()
        if not text:
            return False
        patterns = (
            r"\buse\s+engineering\s+runtime\b",
            r"\bengineering\s+runtime\b",
            r"\bengineering\s+mode\b",
            r"使用\s*engineering\s*runtime",
            r"用\s*engineering\s*runtime",
            r"使用\s*工程运行时",
            r"用\s*工程运行时",
            r"进入\s*工程运行时",
            r"使用\s*工程模式",
            r"用\s*工程模式",
            r"进入\s*工程模式",
        )
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

    def _detect_planner_intent(self, user_content: str) -> dict[str, Any]:
        text = str(user_content or "").strip().lower()
        if not text:
            return {"matched": False, "signals": [], "reason": "empty_user_request"}
        signal_patterns: list[tuple[str, tuple[str, ...]]] = [
            ("explicit_planning", ("plan", "planner", "todo", "todos", "roadmap", "break down", "decompose", "拆解", "计划", "规划", "分工", "任务清单", "执行计划")),
            ("delegation_or_parallel", ("delegate", "subagent", "parallel", "swarm", "agent", "agents", "并发", "子代理", "子agent", "蜂群", "多代理")),
            ("large_implementation", ("implement", "refactor", "migration", "architecture", "upgrade", "phase", "rollout", "scaffold", "frontend", "project", "app", "实施", "实现", "改造", "迁移", "架构", "升级", "阶段", "开发", "搭建", "创建", "项目", "应用", "前端")),
            ("verification_contract", ("acceptance", "test plan", "verify", "validation", "验收", "验证", "测试计划", "回归")),
        ]
        signals: list[str] = []
        for name, patterns in signal_patterns:
            if any(pattern in text for pattern in patterns):
                signals.append(name)
        word_count = len(re.findall(r"\w+", text))
        if word_count >= 80 or len(text) >= 360:
            signals.append("large_request")
        seen: set[str] = set()
        deduped = [item for item in signals if not (item in seen or seen.add(item))]
        return {
            "matched": bool(deduped),
            "signals": deduped,
            "reason": "signals_matched" if deduped else "no_planner_signal",
        }

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

    def _normalize_context_mentions(self, request: ChatRequest, *, skill_references: list[dict[str, str]]) -> list[dict[str, str]]:
        request_data = request.data
        selected = getattr(request_data, "context_mentions", None) if request_data else None
        normalized: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()

        def add_mention(payload: dict[str, Any]) -> None:
            kind = str(payload.get("kind") or "").strip().lower()
            mention_id = str(payload.get("id") or payload.get("familyId") or "").strip()
            name = str(payload.get("name") or payload.get("label") or "").strip()
            path = str(payload.get("path") or "").strip()
            if not kind or (not mention_id and not name and not path):
                return
            if kind in {"subagent-family", "subagentfamily", "family"}:
                kind = "subagent_family"
            dedupe_key = (kind, mention_id.lower() or name.lower(), path.lower())
            if dedupe_key in seen:
                return
            seen.add(dedupe_key)
            current = {
                "kind": kind,
                "id": mention_id,
                "name": name,
                "label": str(payload.get("label") or name or mention_id).strip(),
                "description": str(payload.get("description") or "").strip(),
                "path": path,
                "familyId": normalize_specialist_family_id(payload.get("familyId") or mention_id or name) if kind == "subagent_family" else "",
                "sourceType": str(payload.get("sourceType") or payload.get("source_type") or "explicit_mention").strip(),
            }
            normalized.append(current)

        for item in list(selected or []):
            if not item:
                continue
            add_mention(
                {
                    "kind": getattr(item, "kind", ""),
                    "id": getattr(item, "id", ""),
                    "name": getattr(item, "name", ""),
                    "label": getattr(item, "label", ""),
                    "description": getattr(item, "description", ""),
                    "path": getattr(item, "path", ""),
                    "familyId": getattr(item, "family_id", ""),
                    "sourceType": getattr(item, "source_type", ""),
                }
            )
        for skill in list(skill_references or []):
            add_mention(
                {
                    "kind": "skill",
                    "id": skill.get("id") or "",
                    "name": skill.get("name") or "",
                    "label": skill.get("name") or "",
                    "description": skill.get("description") or "",
                    "path": skill.get("path") or "",
                    "sourceType": skill.get("sourceType") or "",
                }
            )
        return normalized

    def _registered_subagent_family_lookup(self) -> dict[str, str]:
        supervisor_config = storage.get_supervisor_config() or {}
        registry = build_specialist_family_registry(
            storage.get_all_agents(),
            supervisor_config.get("specialistRegistry") if isinstance(supervisor_config.get("specialistRegistry"), dict) else {},
        )
        lookup: dict[str, str] = {}
        for entry in registry:
            family_id = normalize_specialist_family_id(entry.get("familyId"))
            candidates = [
                family_id,
                str(entry.get("displayName") or "").strip(),
                str(entry.get("name") or "").strip(),
                *[str(item or "").strip() for item in list(entry.get("aliases") or [])],
            ]
            for candidate in candidates:
                if not candidate:
                    continue
                lookup[normalize_specialist_family_id(candidate)] = family_id
                lookup[candidate.strip().lower()] = family_id
        return lookup

    def _resolve_explicit_subagent_families(self, request: ChatRequest, context_mentions: list[dict[str, str]]) -> list[str]:
        lookup = self._registered_subagent_family_lookup()
        resolved: list[str] = []

        def add_candidate(value: Any) -> None:
            if value is None:
                return
            raw = str(value or "").strip()
            if not raw:
                return
            family_id = lookup.get(normalize_specialist_family_id(raw)) or lookup.get(raw.lower())
            if family_id and family_id not in resolved:
                resolved.append(family_id)

        for mention in list(context_mentions or []):
            if str(mention.get("kind") or "").strip().lower() != "subagent_family":
                continue
            add_candidate(mention.get("familyId") or mention.get("id") or mention.get("name") or mention.get("label"))

        latest_user = self._latest_user_content(request)
        for match in re.finditer(r"@family:([^\s,，。;；]+)", latest_user, flags=re.IGNORECASE):
            add_candidate(match.group(1))
        for match in re.finditer(r"@([^\s,，。;；:：]+)", latest_user):
            add_candidate(match.group(1))

        return resolved

    def _resolve_request_context(
        self,
        request: ChatRequest,
    ) -> tuple[dict[str, Any] | None, bool, str, str, dict[str, Any], str, bool, list[dict[str, str]], list[dict[str, str]], list[str]]:
        request_data = request.data
        command_selection = request_data.command_preset if request_data else None
        task_planning_mode = bool(request_data.task_planning_mode) if request_data else False
        requested_planner_mode = getattr(request_data, "planner_mode", None) if request_data else None
        planner_dispatch_mode = self._normalize_planner_dispatch_mode(getattr(request_data, "planner_dispatch_mode", None) if request_data else None)
        planner_mode = self._normalize_planner_mode(requested_planner_mode, task_planning_mode=task_planning_mode)
        engineering_mode = self._normalize_engineering_mode(getattr(request_data, "engineering_mode", None) if request_data else None)
        latest_user = self._latest_user_content(request)
        explicit_engineering_requested = self._detect_explicit_engineering_runtime_request(latest_user)
        planner_diagnostics = self._detect_planner_intent(latest_user)
        if explicit_engineering_requested:
            planner_diagnostics = {
                **planner_diagnostics,
                "matched": True,
                "signals": list(dict.fromkeys([*list(planner_diagnostics.get("signals") or []), "explicit_engineering_runtime"])),
                "reason": "explicit_engineering_runtime_requested",
            }
            task_planning_mode = True
            planner_mode = "force"
            planner_dispatch_mode = "auto"
            engineering_mode = "force"
        if planner_mode == "auto":
            task_planning_mode = bool(planner_diagnostics.get("matched"))
        elif planner_mode == "force":
            task_planning_mode = True
        elif planner_mode == "off":
            task_planning_mode = False

        command_preset = None
        if command_selection and command_selection.name:
            command_preset = read_command_preset(command_selection.name)
            if not command_preset:
                raise RuntimeError(f"Command preset '{command_selection.name}' does not exist.")

        skill_references = self._normalize_skill_references(request)
        context_mentions = self._normalize_context_mentions(request, skill_references=skill_references)
        explicit_subagent_families = self._resolve_explicit_subagent_families(request, context_mentions)
        return command_preset, task_planning_mode, planner_mode, planner_dispatch_mode, planner_diagnostics, engineering_mode, explicit_engineering_requested, skill_references, context_mentions, explicit_subagent_families

    def _inject_structured_request_context(
        self,
        lc_messages: list[Any],
        *,
        command_preset: dict[str, Any] | None,
        task_planning_mode: bool,
        planner_mode: str,
        planner_intent_diagnostics: dict[str, Any],
        skill_references: list[dict[str, str]],
        context_mentions: list[dict[str, str]],
        planner_dispatch_mode: str = "suggest",
    ) -> None:
        if not command_preset and not skill_references and not context_mentions:
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
            subagent_family_mentions = [
                mention for mention in list(context_mentions or [])
                if str(mention.get("kind") or "").strip().lower() == "subagent_family"
            ]
            if subagent_family_mentions:
                mention_lines = ["[SUBAGENT FAMILY MENTIONS]"]
                for mention in subagent_family_mentions:
                    mention_lines.append(
                        f"- familyId: {mention.get('familyId') or mention.get('id') or mention.get('name') or 'unknown'}"
                    )
                    if mention.get("label"):
                        mention_lines.append(f"  label: {mention['label']}")
                    if mention.get("description"):
                        mention_lines.append(f"  description: {mention['description']}")
                mention_lines.append("[/SUBAGENT FAMILY MENTIONS]")
                wrapped_sections.append("\n".join(mention_lines))
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

    @staticmethod
    def _planner_registry_snapshot() -> dict[str, Any]:
        loaded_agents = [
            item for item in storage.get_all_agents()
            if isinstance(item, dict) and str(item.get("id") or "").strip() and str(item.get("id") or "").strip() != "supervisor"
        ]
        supervisor_config = storage.get_supervisor_config() or {}
        external_workers = [
            compact_external_worker_registry_entry(item)
            for item in list((supervisor_config.get("delegation") or {}).get("externalWorkers") or [])
            if isinstance(item, dict) and bool(item.get("enabled"))
        ]
        return {
            "subagents": loaded_agents,
            "externalWorkers": external_workers,
        }

    @staticmethod
    def _planner_registry_lines(registry: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        subagents = list(registry.get("subagents") or [])
        external_workers = list(registry.get("externalWorkers") or [])
        if subagents:
            lines.append("[Local Subagents]")
            for agent in subagents[:12]:
                snapshot = agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {}
                capability = summarize_capability_snapshot(snapshot)
                lines.append(
                    f"- {str(agent.get('name') or agent.get('id') or 'unknown-agent').strip()} "
                    f"({str(agent.get('id') or '').strip()}): "
                    f"{str(agent.get('description') or 'No description').strip()}"
                    f"{' | ' + capability if capability else ''}"
                )
        if external_workers:
            lines.append("[External Workers]")
            for worker in external_workers[:12]:
                snapshot = worker.get("capabilitySnapshot") if isinstance(worker.get("capabilitySnapshot"), dict) else {}
                capability = summarize_capability_snapshot(snapshot)
                lines.append(
                    f"- {str(worker.get('name') or worker.get('id') or 'external-worker').strip()} "
                    f"({str(worker.get('id') or '').strip()}): "
                    f"{str(worker.get('description') or 'No description').strip()}"
                    f"{' | ' + capability if capability else ''}"
                )
        if not lines:
            lines.append("- No local subagents or external workers are currently registered.")
        return lines

    def _planner_system_prompt(self) -> str:
        return (
            "You are the V8 Agent OS planner lane.\n"
            "You are a non-executing orchestration planner. Produce only a structured planning contract.\n"
            "Your job is to decide whether the request should be handled directly by the supervisor, delegated, or split into a mixed strategy.\n"
            "Core discipline:\n"
            "- Slice before execute.\n"
            "- Keep the minimum task count that preserves write-set isolation, behavior isolation, and acceptance clarity.\n"
            "- Prefer direct execution only for small, bounded tasks that fit within 1-10 tool steps and a tiny writeSet.\n"
            "- Use delegation or mixed strategy when specialized capability, independent context, parallel work, broad multi-file implementation, research+implementation, or external worker execution materially helps.\n"
            "- Every task brief must be broker-ready and concrete.\n"
            "- Define acceptance contracts before execution starts.\n"
            "- Do not pretend work has already been done.\n"
            "- Do not execute tools, browse, or simulate outputs.\n"
            "- Broad product tasks may require multiple runtime lanes; model them explicitly instead of flattening them into one direct task.\n"
            "Output rules:\n"
            "- executionStrategy must be one of: direct, delegate, mixed.\n"
            "- taskBriefs must align with executionStrategy.\n"
            "- direct may still include one compact task brief for governance and verification.\n"
            "- preferredAgentId and preferredWorkerType are optional hints, not guesses.\n"
            "- familyHint may name a specialist family such as engineering or creative_media; it guides delegation_broker selection but does not reveal members or grant runtime tools.\n"
            "- executionLaneHint must be one of: subagent, external_worker, auto.\n"
            "- If one logical task needs multiple parallel workers, keep it as one macro task and set targetCount to the exact requested fanout; optionally provide workerBriefs with one atomic brief per worker.\n"
            "- targetCount is explicit upper-agent intent, not an automatic default. Do not set it above 1 unless parallel workers materially help and their work can be isolated.\n"
            "- Keep riskFlags short and concrete.\n"
            "Research plus implementation discipline:\n"
            "- If the request combines research/search with building, code, frontend, or app creation, use executionStrategy=mixed.\n"
            "- Put the research evidence task before implementation; the research task should request source quality, conflicts, citations, and compact evidence refs.\n"
            "- Put the implementation task in the engineering family when it creates or changes project files, with an explicit tentative writeSet and proof expectations.\n"
            "- Project scaffolding, dependency installs, and dev servers should be session-observed commands, not blocking sync commands.\n"
            "- If a plan would need scaffolding + dependencies + implementation + verification, it should not be one direct supervisor task.\n"
            "Engineering lane discipline when EngineeringEvidenceGraph is provided:\n"
            "- Prefer evidenceGraphDigest over raw guessing for critical files, writeSet, and verification choices.\n"
            "- Populate codingPlannerContract with criticalFiles, readSet, writeSet, ownershipPlan, verificationMatrix, mergeOrder, riskFlags, and proofExpectations.\n"
            "- Add engineeringTaskCapsule to each task brief when the task touches code; keep it compact and do not copy full repo evidence.\n"
            "- If writeSet cannot be proven, say so in riskFlags instead of pretending certainty.\n"
        )

    def _fallback_planner_plan(self, *, chat_run: ChatRunContext, reason: str) -> dict[str, Any]:
        latest_user_content = str(chat_run.prepared.latest_user_content or "").strip()
        diagnostics = dict(chat_run.prepared.planner_intent_diagnostics or {})
        signals = [str(item).strip() for item in list(diagnostics.get("signals") or []) if str(item).strip()]
        task_shape_hint = dict(chat_run.prepared.task_shape_hint or {})
        primary_shape = str(task_shape_hint.get("primaryTaskShape") or "").strip()
        secondary_shapes = [
            str(item or "").strip()
            for item in list(task_shape_hint.get("secondaryTaskShapes") or [])
            if str(item or "").strip()
        ]
        optional_grants = [
            str(item or "").strip()
            for item in list(task_shape_hint.get("optionalRuntimeGrants") or [])
            if str(item or "").strip()
        ]
        should_delegate = any(signal in {"delegation_or_parallel", "large_implementation"} for signal in signals)
        fallback_context = {
            "source": "planner_fallback",
            "reason": reason,
            "plannerMode": chat_run.prepared.planner_mode,
            "intentSignals": signals,
            "taskShapeHint": task_shape_hint,
        }
        suggested_families = [
            str(item or "").strip()
            for item in list(task_shape_hint.get("suggestedFamilies") or [])
            if str(item or "").strip()
        ]
        if primary_shape == "project_coding" and ("research" in secondary_shapes or "research.core" in optional_grants):
            research_brief = normalize_task_brief(
                {
                    "taskBriefId": "task-1",
                    "goal": "Research the external facts needed before implementation.",
                    "context": fallback_context,
                    "writeSet": [],
                    "behaviorScope": ["research", "read_only", "evidence_collection"],
                    "requiredCapabilities": ["web_research", "source_quality", "citations"],
                    "acceptanceContract": "Produce a compact evidence bundle with source quality, conflicts, citations, raw refs, and confidence notes before implementation decisions.",
                    "dependency": [],
                    "parallelGroup": "research",
                    "executionLaneHint": "auto",
                    "familyHint": "research",
                    "runtimeAccess": ["research.core"],
                }
            )
            implementation_brief = normalize_task_brief(
                {
                    "taskBriefId": "task-2",
                    "goal": latest_user_content or "Implement the requested project.",
                    "context": {
                        **fallback_context,
                        "implementationPrerequisite": "Use task-1 evidence refs; do not treat weak ad-hoc search results as implementation truth.",
                    },
                    "writeSet": ["<project-workspace>/"],
                    "behaviorScope": ["implementation", "project_scaffold", "verification"],
                    "requiredCapabilities": ["project_scaffold", "frontend_implementation", "dependency_management", "verification"],
                    "acceptanceContract": "Create or modify the project files, use session-observed commands for scaffolding/dependency work, and report touched files, verification commands, and residual risks.",
                    "dependency": ["task-1"],
                    "parallelGroup": "",
                    "executionLaneHint": "auto",
                    "familyHint": "engineering",
                    "engineeringTaskCapsule": {
                        "writeSet": ["<project-workspace>/"],
                        "proofExpectations": [
                            "Record scaffold/dependency commands as observable sessions.",
                            "Run the narrowest available verification and report failures instead of claiming completion.",
                        ],
                        "riskFlags": ["planner_fallback_write_set_tentative"],
                    },
                },
                index=1,
            )
            briefs = [research_brief, implementation_brief]
            return {
                "planId": f"plan_{uuid.uuid4().hex[:10]}",
                "executionStrategy": "mixed",
                "planSummary": latest_user_content or "Research first, then implement with engineering proof discipline.",
                "taskGraph": [
                    {
                        "taskBriefId": brief["taskBriefId"],
                        "title": brief["goal"],
                        "dependency": list(brief.get("dependency") or []),
                        "parallelGroup": str(brief.get("parallelGroup") or "").strip(),
                    }
                    for brief in briefs
                ],
                "taskBriefs": briefs,
                "globalAcceptanceContract": "Complete the user request through source-backed research, project implementation, and observable verification, or report the exact recoverable blocker.",
                "riskFlags": ["planner_fallback_used", "mixed_research_engineering_fallback", "tentative_project_write_set"],
                "qualityFlags": ["planner_fallback_used", "source_quality_required"],
                "repairCount": 0,
                "autoDispatchDecision": {},
                "dispatchEligibilityReason": "",
            }
        brief = normalize_task_brief(
            {
                "taskBriefId": "task-1",
                "goal": latest_user_content or "Handle the current user request.",
                "context": fallback_context,
                "writeSet": [],
                "behaviorScope": ["delegated_execution", "implementation"] if should_delegate else ["direct_execution", "implementation"],
                "requiredCapabilities": [],
                "acceptanceContract": "Produce the requested result and report concrete outputs, touched files, and verification status.",
                "dependency": [],
                "parallelGroup": "main" if should_delegate else "",
                "executionLaneHint": "auto" if should_delegate else "subagent",
                "familyHint": suggested_families[0] if suggested_families else "",
            }
        )
        briefs = [brief]
        if should_delegate and any(signal in {"large_implementation", "verification_contract"} for signal in signals):
            briefs.append(
                normalize_task_brief(
                    {
                        "taskBriefId": "task-2",
                        "goal": "Verify the delegated work against the requested acceptance criteria.",
                        "context": fallback_context,
                        "writeSet": [],
                        "behaviorScope": ["verification", "review"],
                        "requiredCapabilities": ["verification"],
                        "acceptanceContract": "Check the result for regressions, missing acceptance criteria, and unresolved risks.",
                        "dependency": ["task-1"],
                        "parallelGroup": "",
                        "executionLaneHint": "auto",
                    },
                    index=1,
                )
            )
        execution_strategy = "delegate" if should_delegate else "direct"
        return {
            "planId": f"plan_{uuid.uuid4().hex[:10]}",
            "executionStrategy": execution_strategy,
            "planSummary": latest_user_content or "Plan the current request.",
            "taskGraph": [
                {
                    "taskBriefId": brief["taskBriefId"],
                    "title": brief["goal"],
                    "dependency": list(brief.get("dependency") or []),
                    "parallelGroup": str(brief.get("parallelGroup") or "").strip(),
                }
                for brief in briefs
            ],
            "taskBriefs": briefs,
            "globalAcceptanceContract": "Satisfy the user request and report concrete outputs, touched files, and verification status.",
            "riskFlags": ["planner_fallback_used"] if reason else [],
            "qualityFlags": ["planner_fallback_used"] if reason else [],
            "repairCount": 0,
            "autoDispatchDecision": {},
            "dispatchEligibilityReason": "",
        }

    @staticmethod
    def _has_parallel_write_conflict(task_briefs: list[dict[str, Any]]) -> bool:
        owners: dict[str, str] = {}
        for brief in task_briefs:
            task_id = str(brief.get("taskBriefId") or "").strip()
            dependencies = {str(item).strip() for item in list(brief.get("dependency") or []) if str(item).strip()}
            for raw_path in list(brief.get("writeSet") or []):
                path = str(raw_path or "").strip().lower()
                if not path:
                    continue
                owner = owners.get(path)
                if owner and owner not in dependencies:
                    return True
                owners[path] = task_id
        return False

    @staticmethod
    def _validate_and_repair_planner_plan(plan: dict[str, Any], *, fallback_plan: dict[str, Any]) -> dict[str, Any]:
        repaired = dict(plan or {})
        quality_flags = [str(item).strip() for item in list(repaired.get("qualityFlags") or []) if str(item).strip()]
        repair_count = int(repaired.get("repairCount") or 0)
        task_briefs = normalize_task_briefs(repaired.get("taskBriefs") or [])
        if not task_briefs:
            task_briefs = [dict(item) for item in list(fallback_plan.get("taskBriefs") or []) if isinstance(item, dict)]
            quality_flags.append("missing_task_briefs_repaired")
            repair_count += 1

        global_acceptance = str(repaired.get("globalAcceptanceContract") or fallback_plan.get("globalAcceptanceContract") or "").strip()
        plan_summary = str(repaired.get("planSummary") or fallback_plan.get("planSummary") or "Plan the current request.").strip()
        task_ids: set[str] = set()
        repaired_briefs: list[dict[str, Any]] = []
        for index, brief in enumerate(task_briefs):
            item = normalize_task_brief(brief, index=index)
            if not str(item.get("taskBriefId") or "").strip() or item["taskBriefId"] in task_ids:
                item["taskBriefId"] = f"task-{index + 1}"
                quality_flags.append("task_id_repaired")
                repair_count += 1
            task_ids.add(item["taskBriefId"])
            if not str(item.get("goal") or "").strip():
                item["goal"] = plan_summary if index == 0 else f"Complete task {index + 1} for the current request."
                quality_flags.append("missing_goal_repaired")
                repair_count += 1
            if not item.get("behaviorScope"):
                item["behaviorScope"] = ["execution", "verification"]
                quality_flags.append("missing_behavior_scope_repaired")
                repair_count += 1
            if not str(item.get("acceptanceContract") or "").strip():
                item["acceptanceContract"] = global_acceptance or "Report concrete outputs, verification status, and unresolved risks."
                quality_flags.append("missing_acceptance_contract_repaired")
                repair_count += 1
            repaired_briefs.append(item)

        for item in repaired_briefs:
            dependencies: list[str] = []
            for dep in list(item.get("dependency") or []):
                dep_id = str(dep).strip()
                if dep_id and dep_id in task_ids and dep_id != item.get("taskBriefId"):
                    dependencies.append(dep_id)
                elif dep_id:
                    quality_flags.append("invalid_dependency_removed")
                    repair_count += 1
            item["dependency"] = dependencies

        execution_strategy = str(repaired.get("executionStrategy") or fallback_plan.get("executionStrategy") or "direct").strip().lower()
        if execution_strategy not in {"direct", "delegate", "mixed"}:
            execution_strategy = str(fallback_plan.get("executionStrategy") or "direct")
            quality_flags.append("invalid_execution_strategy_repaired")
            repair_count += 1
        if execution_strategy == "direct" and len(repaired_briefs) > 1:
            execution_strategy = "mixed"
            quality_flags.append("direct_strategy_with_multiple_tasks_repaired")
            repair_count += 1
        if execution_strategy in {"delegate", "mixed"} and not repaired_briefs:
            repaired_briefs = [dict(item) for item in list(fallback_plan.get("taskBriefs") or []) if isinstance(item, dict)]
            quality_flags.append("delegation_without_tasks_repaired")
            repair_count += 1

        if not global_acceptance and repaired_briefs:
            global_acceptance = str(repaired_briefs[-1].get("acceptanceContract") or "").strip()
            quality_flags.append("global_acceptance_from_task")
            repair_count += 1

        repaired["executionStrategy"] = execution_strategy
        repaired["planSummary"] = plan_summary
        repaired["taskBriefs"] = repaired_briefs
        repaired["taskGraph"] = [
            {
                "taskBriefId": str(item.get("taskBriefId") or f"task-{index + 1}").strip(),
                "title": str(item.get("goal") or item.get("taskBriefId") or f"Task {index + 1}").strip(),
                "dependency": list(item.get("dependency") or []),
                "parallelGroup": str(item.get("parallelGroup") or "").strip(),
            }
            for index, item in enumerate(repaired_briefs)
        ]
        repaired["globalAcceptanceContract"] = global_acceptance
        repaired["qualityFlags"] = list(dict.fromkeys(quality_flags))
        repaired["repairCount"] = repair_count
        return repaired

    @staticmethod
    def _decide_planner_auto_dispatch(
        plan: dict[str, Any],
        *,
        registry: dict[str, Any],
        planner_mode: str,
        planner_dispatch_mode: str,
    ) -> dict[str, Any]:
        strategy = str(plan.get("executionStrategy") or "direct").strip().lower()
        task_briefs = [dict(item) for item in list(plan.get("taskBriefs") or []) if isinstance(item, dict)]
        if planner_dispatch_mode == "off":
            return {"mode": "off", "eligible": False, "willDispatch": False, "reason": "planner_dispatch_mode_off"}
        if planner_dispatch_mode != "auto":
            return {"mode": planner_dispatch_mode or "suggest", "eligible": False, "willDispatch": False, "reason": "suggest_only"}
        if strategy == "direct":
            return {"mode": "auto", "eligible": False, "willDispatch": False, "reason": "direct_strategy"}
        if not task_briefs:
            return {"mode": "auto", "eligible": False, "willDispatch": False, "reason": "no_task_briefs"}
        expanded_task_briefs = expand_delegation_task_briefs(task_briefs)
        if len(expanded_task_briefs) > 10:
            return {
                "mode": "auto",
                "eligible": False,
                "willDispatch": False,
                "reason": "task_count_exceeds_default_governance_limit",
                "macroTaskCount": len(task_briefs),
                "taskCount": len(expanded_task_briefs),
            }
        if ChatRuntime._has_parallel_write_conflict(expanded_task_briefs):
            return {"mode": "auto", "eligible": False, "willDispatch": False, "reason": "write_set_conflict"}
        quality_flags = {str(item).strip() for item in list(plan.get("qualityFlags") or []) if str(item).strip()}
        hard_quality_flags = {"invalid_dependency_removed", "delegation_without_tasks_repaired"}
        if quality_flags & hard_quality_flags:
            return {
                "mode": "auto",
                "eligible": False,
                "willDispatch": False,
                "reason": "planner_quality_flags_block_dispatch",
                "qualityFlags": sorted(quality_flags & hard_quality_flags),
            }

        subagents = list(registry.get("subagents") or [])
        external_workers = list(registry.get("externalWorkers") or [])
        selections: list[dict[str, Any]] = []
        for brief in expanded_task_briefs:
            lane = str(brief.get("executionLaneHint") or "auto").strip().lower() or "auto"
            selected = None
            diagnostics: dict[str, Any] = {}
            if lane in {"subagent", "auto"}:
                selected, diagnostics = choose_best_local_agent_with_diagnostics(brief, subagents)
            if selected is None and lane in {"external_worker", "auto"}:
                selected, diagnostics = choose_best_external_worker_with_diagnostics(brief, external_workers)
            if selected is None:
                return {
                    "mode": "auto",
                    "eligible": False,
                    "willDispatch": False,
                    "reason": "no_matching_target",
                    "taskBriefId": brief.get("taskBriefId"),
                    "selectionDiagnostics": diagnostics,
                }
            selections.append(
                {
                    "taskBriefId": brief.get("taskBriefId"),
                    "targetId": selected.get("id"),
                    "targetLabel": selected.get("name") or selected.get("id"),
                    "selectionReason": diagnostics.get("selectionReason"),
                    "selectionConfidence": diagnostics.get("selectionConfidence"),
                    "matchSignals": list(diagnostics.get("matchSignals") or []),
                }
            )
        return {
            "mode": "auto",
            "eligible": True,
            "willDispatch": True,
            "reason": "eligible",
            "plannerMode": planner_mode,
            "macroTaskCount": len(task_briefs),
            "taskCount": len(expanded_task_briefs),
            "selectedTargets": selections,
        }

    @staticmethod
    def _normalize_planner_plan_payload(raw_plan: Any, *, fallback_plan: dict[str, Any]) -> dict[str, Any]:
        if isinstance(raw_plan, BaseModel):
            payload = raw_plan.model_dump(mode="json")
        elif isinstance(raw_plan, list):
            payload = {
                "executionStrategy": "mixed" if len(raw_plan) > 1 else "delegate",
                "taskBriefs": [item for item in raw_plan if isinstance(item, dict)],
                "qualityFlags": ["planner_list_payload_wrapped"],
            }
        elif isinstance(raw_plan, dict):
            payload = dict(raw_plan or {})
        else:
            payload = dict(fallback_plan or {})
        normalized_briefs = [
            normalize_task_brief(item, index=index)
            for index, item in enumerate(list(payload.get("taskBriefs") or []))
        ]
        if not normalized_briefs:
            normalized_briefs = list(fallback_plan.get("taskBriefs") or [])
        normalized_graph: list[dict[str, Any]] = []
        graph_rows = list(payload.get("taskGraph") or [])
        task_lookup = {str(item.get("taskBriefId") or "").strip(): item for item in normalized_briefs}
        for index, item in enumerate(graph_rows):
            row = dict(item or {})
            task_brief_id = str(row.get("taskBriefId") or normalized_briefs[min(index, len(normalized_briefs) - 1)].get("taskBriefId") or "").strip()
            normalized_graph.append(
                {
                    "taskBriefId": task_brief_id,
                    "title": str(row.get("title") or task_lookup.get(task_brief_id, {}).get("goal") or task_brief_id or f"Task {index + 1}").strip(),
                    "dependency": [str(dep).strip() for dep in list(row.get("dependency") or task_lookup.get(task_brief_id, {}).get("dependency") or []) if str(dep).strip()],
                    "parallelGroup": str(row.get("parallelGroup") or task_lookup.get(task_brief_id, {}).get("parallelGroup") or "").strip(),
                }
            )
        if not normalized_graph:
            normalized_graph = [
                {
                    "taskBriefId": str(item.get("taskBriefId") or f"task-{index + 1}").strip(),
                    "title": str(item.get("goal") or item.get("taskBriefId") or f"Task {index + 1}").strip(),
                    "dependency": [str(dep).strip() for dep in list(item.get("dependency") or []) if str(dep).strip()],
                    "parallelGroup": str(item.get("parallelGroup") or "").strip(),
                }
                for index, item in enumerate(normalized_briefs)
            ]
        execution_strategy = str(payload.get("executionStrategy") or fallback_plan.get("executionStrategy") or "direct").strip().lower()
        if execution_strategy not in {"direct", "delegate", "mixed"}:
            execution_strategy = str(fallback_plan.get("executionStrategy") or "direct")
        plan_summary = str(payload.get("planSummary") or fallback_plan.get("planSummary") or "").strip()
        global_acceptance = str(payload.get("globalAcceptanceContract") or fallback_plan.get("globalAcceptanceContract") or "").strip()
        risk_flags = [str(item).strip() for item in list(payload.get("riskFlags") or fallback_plan.get("riskFlags") or []) if str(item).strip()]
        quality_flags = [str(item).strip() for item in list(payload.get("qualityFlags") or fallback_plan.get("qualityFlags") or []) if str(item).strip()]
        return {
            "planId": str(payload.get("planId") or fallback_plan.get("planId") or f"plan_{uuid.uuid4().hex[:10]}").strip(),
            "executionStrategy": execution_strategy,
            "planSummary": plan_summary or str(fallback_plan.get("planSummary") or "").strip(),
            "taskGraph": normalized_graph,
            "taskBriefs": normalized_briefs,
            "globalAcceptanceContract": global_acceptance or str(fallback_plan.get("globalAcceptanceContract") or "").strip(),
            "riskFlags": risk_flags,
            "codingPlannerContract": payload.get("codingPlannerContract") if isinstance(payload.get("codingPlannerContract"), dict) else dict(fallback_plan.get("codingPlannerContract") or {}),
            "qualityFlags": quality_flags,
            "repairCount": int(payload.get("repairCount") or fallback_plan.get("repairCount") or 0),
            "autoDispatchDecision": payload.get("autoDispatchDecision") if isinstance(payload.get("autoDispatchDecision"), dict) else dict(fallback_plan.get("autoDispatchDecision") or {}),
            "dispatchEligibilityReason": str(payload.get("dispatchEligibilityReason") or fallback_plan.get("dispatchEligibilityReason") or "").strip(),
        }

    async def ensure_planner_plan(self, *, chat_run: ChatRunContext) -> dict[str, Any] | None:
        if not chat_run.prepared.task_planning_mode or str(chat_run.prepared.planner_mode or "off").strip().lower() == "off":
            chat_run.prepared.planner_plan = None
            return None
        if chat_run.prepared.is_resume_request:
            return chat_run.prepared.planner_plan
        if isinstance(chat_run.prepared.planner_plan, dict) and chat_run.prepared.planner_plan:
            return chat_run.prepared.planner_plan

        registry = self._planner_registry_snapshot()
        planner_request = {
            "plannerMode": chat_run.prepared.planner_mode,
            "taskPlanningMode": chat_run.prepared.task_planning_mode,
            "intentDiagnostics": dict(chat_run.prepared.planner_intent_diagnostics or {}),
            "userRequest": str(chat_run.prepared.latest_user_content or "").strip(),
            "sessionScope": {
                "projectId": chat_run.scope_result.binding.project_id,
                "workspaceId": chat_run.scope_result.binding.workspace_id,
                "workspacePath": chat_run.scope_result.binding.workspace_path,
                "resolvedScope": chat_run.scope_result.binding.resolved_scope,
            },
            "skillReferences": [
                {
                    "name": item.get("name"),
                    "description": item.get("description"),
                    "path": item.get("path"),
                }
                for item in list(chat_run.prepared.skill_references or [])
            ],
            "engineering": {
                "triggerDecision": dict(chat_run.prepared.engineering_trigger_decision or {}),
                "evidenceGraphDigest": (
                    ((chat_run.prepared.engineering_context_pack or {}).get("contextPack") or {}).get("evidenceGraphDigest")
                    if isinstance(chat_run.prepared.engineering_context_pack, dict)
                    else {}
                ),
                "codingPlannerContractPreview": (
                    ((chat_run.prepared.engineering_context_pack or {}).get("contextPack") or {}).get("codingPlannerContractPreview")
                    if isinstance(chat_run.prepared.engineering_context_pack, dict)
                    else {}
                ),
            },
            "specialists": {
                "localSubagents": [
                    {
                        "id": str(agent.get("id") or "").strip(),
                        "name": str(agent.get("name") or "").strip(),
                        "description": str(agent.get("description") or "").strip(),
                        "capabilitySnapshot": agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {},
                    }
                    for agent in list(registry.get("subagents") or [])
                ],
                "externalWorkers": list(registry.get("externalWorkers") or []),
            },
        }
        planner_user_message = (
            "[Planner Request]\n"
            f"Current Time: {utc_now_iso()}\n"
            f"Planner Mode: {chat_run.prepared.planner_mode}\n"
            f"Intent Signals: {', '.join(list(chat_run.prepared.planner_intent_diagnostics.get('signals') or [])) or str(chat_run.prepared.planner_intent_diagnostics.get('reason') or 'manual')}\n"
            f"User Request:\n{str(chat_run.prepared.latest_user_content or '').strip() or '(empty request)'}\n\n"
            "[Specialist Registry]\n"
            + "\n".join(self._planner_registry_lines(registry))
            + "\n\n[Planner Input JSON]\n"
            + json.dumps(planner_request, ensure_ascii=False, indent=2)
        )

        fallback_plan = self._fallback_planner_plan(chat_run=chat_run, reason="planner_model_unavailable")
        plan = fallback_plan
        planning_error: str | None = None
        try:
            planner_model = llm_factory.create_for_role(
                "supervisor",
                streaming=False,
                temperature=0,
                max_tokens=1400,
                _request_kind="planner",
            ).with_structured_output(PlannerPlanPayload)
            raw_plan = await planner_model.ainvoke(
                [
                    SystemMessage(content=self._planner_system_prompt()),
                    HumanMessage(content=planner_user_message),
                ]
            )
            plan = self._normalize_planner_plan_payload(raw_plan, fallback_plan=fallback_plan)
        except Exception as exc:
            planning_error = str(exc)
            logging.getLogger("v8chat.chat_runtime").warning(
                "Planner lane fell back to deterministic plan for run '%s': %s",
                chat_run.active_run_id,
                planning_error,
            )
            fallback_plan = self._fallback_planner_plan(chat_run=chat_run, reason=planning_error)
            plan = fallback_plan

        plan = self._validate_and_repair_planner_plan(plan, fallback_plan=fallback_plan)
        plan = engineering_lane_service.enrich_planner_plan_with_engineering_contract(
            plan,
            engineering_context=chat_run.prepared.engineering_context_pack,
        )
        auto_dispatch_decision = self._decide_planner_auto_dispatch(
            plan,
            registry=registry,
            planner_mode=chat_run.prepared.planner_mode,
            planner_dispatch_mode=chat_run.prepared.planner_dispatch_mode,
        )
        plan["autoDispatchDecision"] = auto_dispatch_decision
        plan["dispatchEligibilityReason"] = str(auto_dispatch_decision.get("reason") or "").strip()
        chat_run.prepared.planner_plan = plan
        workflow_ledger_service.activate_runtime_step(
            chat_run.active_run_id,
            owner_runtime="planner_lane",
            step_key="planner.pass",
            title="Planner lane",
            input_payload={
                "plannerMode": chat_run.prepared.planner_mode,
                "taskPlanningMode": chat_run.prepared.task_planning_mode,
                "intentDiagnostics": dict(chat_run.prepared.planner_intent_diagnostics or {}),
                "userRequest": str(chat_run.prepared.latest_user_content or "").strip(),
                "plannerPlan": plan,
            },
            projection_payload={
                "plannerPlan": plan,
                "plannerDiagnostics": {
                    "mode": chat_run.prepared.planner_mode,
                    "dispatchMode": chat_run.prepared.planner_dispatch_mode,
                    "intentDiagnostics": dict(chat_run.prepared.planner_intent_diagnostics or {}),
                    "usedFallback": bool(planning_error),
                    "error": planning_error,
                    "qualityFlags": list(plan.get("qualityFlags") or []),
                    "repairCount": int(plan.get("repairCount") or 0),
                    "autoDispatchDecision": auto_dispatch_decision,
                },
            },
            status="completed",
        )
        expanded_task_briefs = expand_delegation_task_briefs(plan.get("taskBriefs") or [])
        payload = {
            "planId": plan.get("planId"),
            "executionStrategy": plan.get("executionStrategy"),
            "planSummary": plan.get("planSummary"),
            "macroTaskCount": len(list(plan.get("taskBriefs") or [])),
            "taskCount": len(expanded_task_briefs),
            "taskBriefs": list(plan.get("taskBriefs") or []),
            "dependencies": [
                {
                    "taskBriefId": item.get("taskBriefId"),
                    "dependency": list(item.get("dependency") or []),
                    "parallelGroup": item.get("parallelGroup"),
                }
                for item in list(plan.get("taskGraph") or [])
            ],
            "globalAcceptanceContract": plan.get("globalAcceptanceContract"),
            "riskFlags": list(plan.get("riskFlags") or []),
            "codingPlannerContract": plan.get("codingPlannerContract") if isinstance(plan.get("codingPlannerContract"), dict) else {},
            "engineeringEvidenceGraphDigest": plan.get("engineeringEvidenceGraphDigest") if isinstance(plan.get("engineeringEvidenceGraphDigest"), dict) else {},
            "qualityFlags": list(plan.get("qualityFlags") or []),
            "repairCount": int(plan.get("repairCount") or 0),
            "autoDispatchDecision": auto_dispatch_decision,
            "dispatchEligibilityReason": plan.get("dispatchEligibilityReason"),
            "traceRef": {"runId": chat_run.active_run_id, "planId": plan.get("planId")},
            "usedFallback": bool(planning_error),
            "error": planning_error,
        }
        if planning_error:
            chat_run.emit_runtime_event(
                "planner.plan.failed",
                {
                    "planId": plan.get("planId"),
                    "summary": "Planner lane failed over to deterministic fallback.",
                    "error": planning_error,
                    "traceRef": {"runId": chat_run.active_run_id, "planId": plan.get("planId")},
                },
                agent_id=None,
                node="planner_lane",
            )
        chat_run.emit_runtime_event(
            "planner.plan.created",
            payload,
            agent_id=None,
            node="planner_lane",
        )
        return plan

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
        (
            command_preset,
            task_planning_mode,
            planner_mode,
            planner_dispatch_mode,
            planner_intent_diagnostics,
            engineering_mode,
            explicit_engineering_requested,
            skill_references,
            context_mentions,
            explicit_subagent_families,
        ) = self._resolve_request_context(request)
        self._inject_structured_request_context(
            lc_messages,
            command_preset=command_preset,
            task_planning_mode=task_planning_mode,
            planner_mode=planner_mode,
            planner_dispatch_mode=planner_dispatch_mode,
            planner_intent_diagnostics=planner_intent_diagnostics,
            skill_references=skill_references,
            context_mentions=context_mentions,
        )

        request.session_id = session_id
        request.conversation_id = conversation_id
        request.user_id = user_id
        self._resolve_engine_config(request)
        latest_user_content = self._latest_user_content(request)
        compat_diagnostics = _compat_ingress_diagnostics_from_request(request)
        compat_latest_human = str(compat_diagnostics.get("latestHumanUtterance") or "").strip()
        if compat_latest_human:
            latest_user_content = compat_latest_human

        return ChatPreparedRequest(
            request=request,
            lc_messages=lc_messages,
            session_id=session_id,
            conversation_id=conversation_id,
            user_id=user_id,
            is_resume_request=bool(request.resume_run_id),
            latest_user_content=latest_user_content,
            command_preset_name=(str(command_preset.get("name") or "").strip() or None) if command_preset else None,
            command_preset_hash=(str(command_preset.get("contentHash") or "").strip() or None) if command_preset else None,
            planner_mode=planner_mode,
            planner_dispatch_mode=planner_dispatch_mode,
            planner_intent_diagnostics=planner_intent_diagnostics,
            task_planning_mode=task_planning_mode,
            engineering_mode=engineering_mode,
            explicit_engineering_requested=explicit_engineering_requested,
            skill_references=skill_references,
            context_mentions=context_mentions,
            explicit_subagent_families=explicit_subagent_families,
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
        existing_binding = None
        scope_result = None

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
                    "transport": transport,
                    "externalSurface": "network_supervisor_openai" if transport == "network_supervisor_openai" else None,
                    "hideFromChatHistory": bool(transport == "network_supervisor_openai"),
                },
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
                run_id=run_id,
            )
            run_handle = self.attach_run(run_id) if run_id and db.get_run_record(run_id) else None
            if run_handle is None:
                run_handle = self.begin_run(
                    session_id=prepared.session_id,
                    conversation_id=prepared.conversation_id,
                    user_id=prepared.user_id,
                    transport=transport,
                    provider=prepared.request.config.provider,
                    model_name=prepared.request.config.model_name,
                    run_id=run_id,
                )

        if scope_result is None:
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
        try:
            prepared.task_shape_hint = classify_task_shape(
                prepared.latest_user_content,
                workspace_descriptor={
                    "projectId": scope_result.binding.project_id,
                    "workspaceId": scope_result.binding.workspace_id,
                    "workspacePath": scope_result.binding.workspace_path,
                    "resolvedScope": scope_result.binding.resolved_scope,
                },
            )
            primary_shape = str(prepared.task_shape_hint.get("primaryTaskShape") or "").strip()
            secondary_shapes = {
                str(item or "").strip()
                for item in list(prepared.task_shape_hint.get("secondaryTaskShapes") or [])
                if str(item or "").strip()
            }
            if prepared.explicit_engineering_requested or primary_shape == "project_coding" or (primary_shape and "research" in secondary_shapes and primary_shape in {"creative_media", "automation"}):
                prepared.task_planning_mode = True
                prepared.planner_mode = "force"
                prepared.planner_dispatch_mode = "auto"
                if prepared.explicit_engineering_requested:
                    prepared.engineering_mode = "force"
            run_service.update_metadata(
                run_handle.run_id,
                {
                    "taskShapeHint": dict(prepared.task_shape_hint or {}),
                    "plannerMode": prepared.planner_mode,
                    "plannerDispatchMode": prepared.planner_dispatch_mode,
                    "taskPlanningMode": prepared.task_planning_mode,
                    "explicitEngineeringRequested": bool(prepared.explicit_engineering_requested),
                },
            )
        except Exception as exc:
            prepared.task_shape_hint = {
                "primaryTaskShape": "unknown",
                "secondaryTaskShapes": [],
                "confidence": 0.0,
                "reason": "task_shape_classifier_failed",
                "error": str(exc),
                "policy": "hint_only_non_authoritative_no_reveal_no_grant",
            }
            run_service.update_metadata(run_handle.run_id, {"taskShapeHint": dict(prepared.task_shape_hint or {})})
        try:
            engineering_pack = engineering_lane_service.build_context_pack(
                user_query=prepared.latest_user_content,
                mode=prepared.engineering_mode,
                session_id=prepared.session_id,
                run_id=run_handle.run_id,
                project_id=scope_result.binding.project_id,
                workspace_id=scope_result.binding.workspace_id,
                workspace_path=scope_result.binding.workspace_path,
                task_brief=None,
            )
            prepared.engineering_trigger_decision = dict(engineering_pack.get("triggerDecision") or {})
            if prepared.engineering_trigger_decision.get("active"):
                prepared.engineering_context_pack = engineering_pack
            run_service.update_metadata(
                run_handle.run_id,
                {
                    "engineeringMode": prepared.engineering_mode,
                    "engineeringTriggerDecision": dict(prepared.engineering_trigger_decision or {}),
                    **({"engineeringContextPack": dict(engineering_pack)} if prepared.engineering_context_pack else {}),
                },
            )
        except Exception as exc:
            prepared.engineering_trigger_decision = {
                "mode": prepared.engineering_mode,
                "active": False,
                "matched": False,
                "reason": "engineering_context_pack_failed",
                "error": str(exc),
            }
            run_service.update_metadata(
                run_handle.run_id,
                {
                    "engineeringMode": prepared.engineering_mode,
                    "engineeringTriggerDecision": dict(prepared.engineering_trigger_decision or {}),
                },
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
        if chat_run.prepared.engineering_trigger_decision:
            chat_run.emit_runtime_event(
                "engineering_lane.trigger.decided",
                {
                    "engineeringMode": chat_run.prepared.engineering_mode,
                    "triggerDecision": dict(chat_run.prepared.engineering_trigger_decision or {}),
                    "contextPackActive": bool(chat_run.prepared.engineering_context_pack),
                },
                agent_id=None,
                node="engineering_lane",
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

    def record_request_inputs(self, chat_run: ChatRunContext) -> dict[str, Any] | None:
        request = chat_run.request
        client_message_id = self._request_client_message_id(request)
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
        if client_message_id:
            metadata["clientMessageId"] = client_message_id
        if chat_run.prepared.command_preset_name:
            metadata["commandPreset"] = {
                "name": chat_run.prepared.command_preset_name,
                "contentHash": chat_run.prepared.command_preset_hash,
            }
        prepared_planner_mode = getattr(chat_run.prepared, "planner_mode", "off")
        prepared_planner_diagnostics = getattr(chat_run.prepared, "planner_intent_diagnostics", {}) or {}
        if prepared_planner_mode != "off":
            metadata["plannerMode"] = prepared_planner_mode
            metadata["plannerIntentDiagnostics"] = dict(prepared_planner_diagnostics)
        if getattr(chat_run.prepared, "planner_dispatch_mode", "suggest") != "suggest":
            metadata["plannerDispatchMode"] = chat_run.prepared.planner_dispatch_mode
        if chat_run.prepared.task_planning_mode:
            metadata["taskPlanningMode"] = True
        if isinstance(getattr(chat_run.prepared, "task_shape_hint", None), dict) and chat_run.prepared.task_shape_hint:
            metadata["taskShapeHint"] = dict(chat_run.prepared.task_shape_hint)
        engineering_mode = getattr(chat_run.prepared, "engineering_mode", "auto")
        engineering_trigger_decision = getattr(chat_run.prepared, "engineering_trigger_decision", None)
        if engineering_mode != "auto" or engineering_trigger_decision:
            metadata["engineeringMode"] = engineering_mode
            metadata["engineeringTriggerDecision"] = dict(engineering_trigger_decision or {})
            engineering_context_pack = getattr(chat_run.prepared, "engineering_context_pack", None)
            if isinstance(engineering_context_pack, dict):
                metadata["engineeringContextPack"] = dict(engineering_context_pack)
        if chat_run.prepared.skill_references:
            metadata["skillReferences"] = list(chat_run.prepared.skill_references)
        context_mentions = getattr(chat_run.prepared, "context_mentions", None) or []
        if context_mentions:
            metadata["contextMentions"] = list(context_mentions)
        explicit_subagent_families = getattr(chat_run.prepared, "explicit_subagent_families", None) or []
        if explicit_subagent_families:
            metadata["explicitSubagentFamilies"] = list(explicit_subagent_families)

        user_input_already_recorded: dict[str, Any] | None = None
        if not chat_run.is_resume_request and request.messages and request.messages[-1].role == "user":
            if client_message_id:
                user_input_already_recorded = db.get_chat_canonical_message_by_client_message_id(
                    session_id=chat_run.session_id,
                    client_message_id=client_message_id,
                    role="user",
                )
            if not user_input_already_recorded:
                user_input_already_recorded = db.get_chat_canonical_message_by_run(
                    session_id=chat_run.session_id,
                    run_id=chat_run.active_run_id,
                    role="user",
                )

        if not chat_run.is_resume_request and request.messages and request.messages[-1].role == "user" and not user_input_already_recorded:
            latest_user = request.messages[-1]
            candidate_message_id = client_message_id or ""
            if candidate_message_id and db.get_chat_canonical_message(candidate_message_id):
                candidate_message_id = ""
            user_message_id = candidate_message_id or str(uuid.uuid4())
            user_node_id = f"{user_message_id}:narrative"
            attachments = [dict(item) for item in list(request.attachments or []) if isinstance(item, dict)]
            user_structured_metadata: dict[str, Any] = {
                **({"clientMessageId": client_message_id} if client_message_id else {}),
                **({"commandPreset": dict(metadata["commandPreset"])} if isinstance(metadata.get("commandPreset"), dict) else {}),
                **({"plannerMode": metadata.get("plannerMode")} if metadata.get("plannerMode") else {}),
                **({"plannerDispatchMode": metadata.get("plannerDispatchMode")} if metadata.get("plannerDispatchMode") else {}),
                **({"plannerIntentDiagnostics": dict(metadata["plannerIntentDiagnostics"])} if isinstance(metadata.get("plannerIntentDiagnostics"), dict) else {}),
                **({"taskPlanningMode": True} if metadata.get("taskPlanningMode") is True else {}),
                **({"taskShapeHint": dict(metadata["taskShapeHint"])} if isinstance(metadata.get("taskShapeHint"), dict) else {}),
                **({"engineeringMode": metadata.get("engineeringMode")} if metadata.get("engineeringMode") else {}),
                **({"engineeringTriggerDecision": dict(metadata["engineeringTriggerDecision"])} if isinstance(metadata.get("engineeringTriggerDecision"), dict) else {}),
                **({"skillReferences": list(metadata.get("skillReferences") or [])} if isinstance(metadata.get("skillReferences"), list) and metadata.get("skillReferences") else {}),
                **({"contextMentions": list(metadata.get("contextMentions") or [])} if isinstance(metadata.get("contextMentions"), list) and metadata.get("contextMentions") else {}),
                **({"explicitSubagentFamilies": list(metadata.get("explicitSubagentFamilies") or [])} if isinstance(metadata.get("explicitSubagentFamilies"), list) and metadata.get("explicitSubagentFamilies") else {}),
                **({"attachments": attachments} if attachments else {}),
            }
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
            latest_user_content = latest_user.content or ""
            if not latest_user_content.strip() and attachment_nodes:
                latest_user_content = f"已上传 {len(attachment_nodes)} 个文件"
            user_metadata = self._canonical_message_metadata(
                chat_run,
                role="user",
                images=request.fileUrls,
                extra=user_structured_metadata or None,
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
                        "content": latest_user_content,
                        "timestamp": user_metadata["timestamp"],
                    },
                    *attachment_nodes,
                ],
                metadata=user_metadata,
                run_id=chat_run.active_run_id,
                content_text=latest_user_content,
            )
            db.add_message(
                msg_id=user_message_id,
                session_id=chat_run.session_id,
                role="user",
                content=latest_user_content,
                images=request.fileUrls,
                metadata={
                    **metadata,
                    **({"attachments": attachments} if attachments else {}),
                },
            )
            chat_run.emit_runtime_event(
                "message.user.recorded",
                {
                    "message_id": user_message_id,
                    "clientMessageId": client_message_id or None,
                    "node_id": user_node_id,
                    "transcript_version": int(user_row.get("version") or 1),
                    "content": latest_user_content,
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
                        "chat.planner_mode.enabled",
                            {
                                "messageId": user_message_id,
                                "plannerMode": prepared_planner_mode,
                                "plannerDispatchMode": getattr(chat_run.prepared, "planner_dispatch_mode", "suggest"),
                                "enabled": True,
                                "intentDiagnostics": dict(prepared_planner_diagnostics),
                            },
                        agent_id=None,
                        node="planner_lane",
                )
                chat_run.emit_runtime_event(
                    "chat.task_planning_mode.enabled",
                    {
                        "messageId": user_message_id,
                        "enabled": True,
                        "plannerMode": prepared_planner_mode,
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
            if explicit_subagent_families:
                chat_run.emit_runtime_event(
                    "chat.subagent_family_mentions.applied",
                    {
                        "messageId": user_message_id,
                        "families": list(explicit_subagent_families),
                    },
                    agent_id=None,
                    node="input_recorder",
                )
            workflow_ledger_service.record_step_inputs(
                chat_run.active_run_id,
                inputs={
                    "latest_user_message_id": user_message_id,
                    "latest_user_content": latest_user_content,
                    "images": request.fileUrls or [],
                    "attachments": attachments,
                    "transport": chat_run.transport,
                    "resolved_scope": chat_run.scope_result.binding.resolved_scope,
                    "command_preset_name": chat_run.prepared.command_preset_name,
                    "planner_mode": prepared_planner_mode,
                    "planner_dispatch_mode": getattr(chat_run.prepared, "planner_dispatch_mode", "suggest"),
                    "planner_intent_diagnostics": dict(prepared_planner_diagnostics),
                    "task_planning_mode": chat_run.prepared.task_planning_mode,
                    "skill_references": list(chat_run.prepared.skill_references),
                    "context_mentions": list(context_mentions),
                    "explicit_subagent_families": list(explicit_subagent_families),
                },
            )
            chat_run.run_handle.refresh_chat_snapshot()
            user_input_already_recorded = user_row

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

        return user_input_already_recorded

    def _recursion_limit(self) -> int:
        ctx_config = storage.get_context_config()
        return ctx_config.get("recursion_limit", 500)

    def _max_graph_continuations(self) -> int:
        ctx_config = storage.get_context_config() or {}
        raw_value = ctx_config.get("maxGraphContinuations", ctx_config.get("max_graph_continuations", 5))
        try:
            return max(0, min(20, int(raw_value)))
        except (TypeError, ValueError):
            return 5

    async def create_execution_bundle(self, *, chat_run: ChatRunContext) -> ChatExecutionBundle:
        compat_diagnostics = _compat_ingress_diagnostics_from_request(chat_run.request)
        current_route_context = {}
        if compat_diagnostics:
            current_route_context = {
                "transport": chat_run.transport,
                "compatIngressDiagnostics": compat_diagnostics,
                "compatClientProfile": compat_diagnostics.get("compatClientProfile"),
                "compatRequestKind": compat_diagnostics.get("compatRequestKind"),
                "compatExecutionPolicy": compat_diagnostics.get("compatExecutionPolicy"),
                "latestHumanUtterance": compat_diagnostics.get("latestHumanUtterance"),
                "suppressPassiveRag": compat_diagnostics.get("suppressPassiveRag"),
                "suppressExtensionsPrefilter": compat_diagnostics.get("suppressExtensionsPrefilter"),
                "externalToolsPrimary": compat_diagnostics.get("externalToolsPrimary"),
            }
        if getattr(chat_run.prepared, "explicit_engineering_requested", False) or chat_run.prepared.engineering_trigger_decision:
            current_route_context = {
                **current_route_context,
                "explicitEngineeringRequested": bool(getattr(chat_run.prepared, "explicit_engineering_requested", False)),
                "engineeringTriggerDecision": dict(chat_run.prepared.engineering_trigger_decision or {}),
            }
        runner_bundle = await supervisor_runner.create_execution_bundle(
            config=chat_run.request.config,
            messages=chat_run.lc_messages,
            session_id=chat_run.session_id,
            current_route_context=current_route_context,
            planner_plan=chat_run.prepared.planner_plan,
            engineering_context=chat_run.prepared.engineering_context_pack,
            task_shape_hint=chat_run.prepared.task_shape_hint,
            explicit_subagent_families=chat_run.prepared.explicit_subagent_families,
            context_mentions=chat_run.prepared.context_mentions,
            recursion_limit=self._recursion_limit(),
            transport=chat_run.transport,
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
            "plannerPlanId": str((snapshot.get("planner_plan") or {}).get("planId") or "").strip() or None,
            "sessionId": chat_run.session_id,
            "projectId": chat_run.scope_result.binding.project_id,
            "workspaceId": chat_run.scope_result.binding.workspace_id,
            "workspacePath": chat_run.scope_result.binding.workspace_path,
            "resolvedScope": chat_run.scope_result.binding.resolved_scope,
        }
        todos = list(snapshot.get("todos") or [])
        last_todo = next((item for item in reversed(todos) if isinstance(item, dict)), None)
        if last_todo:
            continuation_envelope["lastTodo"] = {
                "id": last_todo.get("id"),
                "status": last_todo.get("status"),
                "text": str(last_todo.get("text") or "")[:240],
            }
        if isinstance(snapshot.get("planner_plan"), dict) and snapshot.get("planner_plan"):
            chat_run.prepared.planner_plan = dict(snapshot.get("planner_plan") or {})

        runner_bundle = await supervisor_runner.create_execution_bundle(
            config=chat_run.request.config,
            messages=state_messages,
            session_id=chat_run.session_id,
            planner_plan=snapshot.get("planner_plan") if isinstance(snapshot.get("planner_plan"), dict) else chat_run.prepared.planner_plan,
            engineering_context=snapshot.get("engineering_context") if isinstance(snapshot.get("engineering_context"), dict) else chat_run.prepared.engineering_context_pack,
            task_shape_hint=snapshot.get("task_shape_hint") if isinstance(snapshot.get("task_shape_hint"), dict) else chat_run.prepared.task_shape_hint,
            explicit_subagent_families=snapshot.get("explicit_subagent_families") if isinstance(snapshot.get("explicit_subagent_families"), list) else chat_run.prepared.explicit_subagent_families,
            context_mentions=snapshot.get("context_mentions") if isinstance(snapshot.get("context_mentions"), list) else chat_run.prepared.context_mentions,
            recursion_limit=self._recursion_limit(),
            transport=chat_run.transport,
        )
        diagnostics = dict(runner_bundle.diagnostics or {})
        diagnostics.update(continuation_envelope)
        runner_bundle.diagnostics = diagnostics
        return ChatExecutionBundle(run_handle=chat_run.run_handle, runner_bundle=runner_bundle)

    async def create_guidance_bundle(
        self,
        *,
        chat_run: ChatRunContext,
        previous_bundle: ChatExecutionBundle,
        queue_item: dict[str, Any],
    ) -> ChatExecutionBundle | None:
        snapshot = await supervisor_runner.get_state_snapshot(previous_bundle.runner_bundle)
        if isinstance(snapshot, dict):
            state_messages = list(snapshot.get("messages") or [])
        else:
            state_messages = []
        if not state_messages:
            state_messages = list(chat_run.lc_messages or [])
        if not state_messages:
            return None

        guidance_content = str(queue_item.get("content") or "").strip()
        if not guidance_content:
            return None
        guidance_envelope = (
            "[V8OS 运行中用户引导]\n"
            "用户在当前任务运行中追加了这条引导。请把它视为高优先级纠偏信息："
            "不要重启整个任务，先简短确认理解，再调整后续计划和下一步工具调用。"
            "如果当前工具刚完成，请基于已有结果继续。\n\n"
            f"用户引导：{guidance_content}"
        )
        state_messages.append(
            HumanMessage(
                content=guidance_envelope,
                id=f"human_guidance_{queue_item.get('id') or uuid.uuid4().hex}",
            )
        )
        snapshot_dict = snapshot if isinstance(snapshot, dict) else {}
        runner_bundle = await supervisor_runner.create_execution_bundle(
            config=chat_run.request.config,
            messages=state_messages,
            session_id=chat_run.session_id,
            planner_plan=snapshot_dict.get("planner_plan") if isinstance(snapshot_dict.get("planner_plan"), dict) else chat_run.prepared.planner_plan,
            engineering_context=snapshot_dict.get("engineering_context") if isinstance(snapshot_dict.get("engineering_context"), dict) else chat_run.prepared.engineering_context_pack,
            task_shape_hint=snapshot_dict.get("task_shape_hint") if isinstance(snapshot_dict.get("task_shape_hint"), dict) else chat_run.prepared.task_shape_hint,
            explicit_subagent_families=snapshot_dict.get("explicit_subagent_families") if isinstance(snapshot_dict.get("explicit_subagent_families"), list) else chat_run.prepared.explicit_subagent_families,
            context_mentions=snapshot_dict.get("context_mentions") if isinstance(snapshot_dict.get("context_mentions"), list) else chat_run.prepared.context_mentions,
            recursion_limit=self._recursion_limit(),
            transport=chat_run.transport,
        )
        diagnostics = dict(runner_bundle.diagnostics or {})
        diagnostics.update(
            {
                "guidanceQueueMessageId": queue_item.get("id"),
                "guidanceInjected": True,
                "guidanceChars": len(guidance_content),
                "messageCount": len(state_messages),
            }
        )
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

    def create_stream_state(self, *, transport: str = "http", chat_run: ChatRunContext | None = None) -> ChatStreamState:
        loaded_agents = storage.get_all_agents()
        valid_nodes = [item.get("id") for item in loaded_agents if item.get("id")] + ["supervisor", "reviewer"]
        preserve_timeline = str(transport or "").strip() in {"network_supervisor_openai", "network_supervisor_anthropic"}
        reasoning_surface_contract: dict[str, Any] = {}
        model_ref = ""
        try:
            if chat_run is not None:
                model_ref = str(getattr(chat_run.request.config, "model_name", "") or "").strip()
            if model_ref:
                reasoning_surface_contract = dict(llm_factory.get_model_metadata(model_ref).get("reasoning_surface") or {})
        except Exception:
            reasoning_surface_contract = {}
        return ChatStreamState(
            valid_agent_node_names=valid_nodes,
            preserve_stream_timeline=preserve_timeline,
            reasoning_surface_contract=reasoning_surface_contract,
        )

    @staticmethod
    def _now_timestamp_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _timestamp_ms_to_iso(timestamp_ms: int | None) -> str | None:
        if timestamp_ms is None:
            return None
        try:
            return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()
        except Exception:
            return None

    def _stream_trace_diagnostics(
        self,
        chat_run: ChatRunContext,
        *,
        kind: str,
        delta: str,
        provider_delta_at_ms: int | None = None,
        canonical_event_at_ms: int | None = None,
        model_run_id: str = "",
    ) -> dict[str, Any]:
        canonical_ms = canonical_event_at_ms or self._now_timestamp_ms()
        diagnostics: dict[str, Any] = {
            "streamTraceVersion": 1,
            "streamKind": kind,
            "transport": chat_run.transport,
            "runId": chat_run.active_run_id,
            "modelRunId": model_run_id,
            "deltaChars": len(delta or ""),
            "canonicalEventAtMs": canonical_ms,
            "canonicalEventAt": self._timestamp_ms_to_iso(canonical_ms),
        }
        if provider_delta_at_ms is not None:
            diagnostics["providerDeltaAtMs"] = provider_delta_at_ms
            diagnostics["providerDeltaAt"] = self._timestamp_ms_to_iso(provider_delta_at_ms)
            diagnostics["providerToCanonicalMs"] = max(0, canonical_ms - provider_delta_at_ms)
        return diagnostics

    @staticmethod
    def _request_client_message_id(request: ChatRequest) -> str:
        direct = str(getattr(request, "client_message_id", "") or "").strip()
        if direct:
            return direct
        data = getattr(request, "data", None)
        return str(getattr(data, "client_message_id", "") or "").strip() if data is not None else ""

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
        existing = db.get_chat_canonical_message_by_run(
            session_id=chat_run.session_id,
            run_id=chat_run.active_run_id,
            role="assistant",
        )
        if existing:
            existing_id = str(existing.get("id") or "").strip()
            if existing_id:
                stream_state.assistant_message_id = existing_id
                stream_state.assistant_transcript_version = int(existing.get("version") or 1)
                return existing_id
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

    def _ensure_workspace_media_artifacts_for_message(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        message_id: str,
    ) -> None:
        row = db.get_chat_canonical_message(message_id)
        if not row:
            return
        final_text = str(row.get("content_text") or self._current_canonical_text(stream_state) or "")
        if not final_text:
            return
        profile = self._get_agent_profile(stream_state.current_agent)
        artifact_nodes = self._workspace_media_artifact_nodes_from_text(
            text=final_text,
            message_id=message_id,
            profile=profile,
            request=chat_run.request,
        )
        if not artifact_nodes:
            return

        def _append_missing_artifacts(nodes: list[dict[str, Any]], _metadata: dict[str, Any]):
            existing_node_ids = {str(node.get("id") or "").strip() for node in nodes}
            missing_nodes = [
                node
                for node in artifact_nodes
                if str(node.get("id") or "").strip() not in existing_node_ids
            ]
            if not missing_nodes:
                return nodes, None
            return [*nodes, *missing_nodes], missing_nodes[0]["id"]

        mutation = canonical_transcript_builder.mutate_message(
            message_id,
            _append_missing_artifacts,
            state=str(row.get("state") or "streaming"),
            metadata_updates={"workspaceMediaArtifactsDerived": True},
        )
        stream_state.assistant_transcript_version = mutation.version

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

    def _emit_human_guidance_injected(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        queue_item: dict[str, Any],
    ) -> dict[str, Any]:
        message_id = self._ensure_assistant_canonical_message(chat_run, stream_state)
        queue_id = str(queue_item.get("id") or "").strip() or uuid.uuid4().hex
        content = str(queue_item.get("content") or "").strip()
        trace_group_id = f"{message_id}:human_guidance:{queue_id}"
        node = {
            "id": f"{message_id}:human_guidance:{queue_id}",
            "kind": "governance",
            "governanceType": "human_guidance",
            "question": content,
            "status": "injected",
            "reason": "运行中用户引导",
            "topic": "human_guidance.injected",
            "timestamp": self._now_timestamp_ms(),
            "agentType": "user",
            "ownerRuntimeId": "chat",
            "ownerAgentKind": "supervisor",
            "ownerAgentId": "supervisor",
            "ownerStreamKey": f"human_guidance:{queue_id}",
            "traceGroupId": trace_group_id,
            "displayInMessage": True,
            "requestInfo": {
                "queueMessageId": queue_id,
                "clientMessageId": queue_item.get("client_message_id"),
                "state": "injected",
            },
        }
        payload = {
            "queueMessage": {
                "id": queue_id,
                "sessionId": queue_item.get("session_id"),
                "runId": queue_item.get("run_id"),
                "clientMessageId": queue_item.get("client_message_id"),
                "content": content,
                "state": "injected",
                "createdAt": queue_item.get("created_at"),
                "promotedAt": queue_item.get("promoted_at"),
            },
            "content": content,
            "state": "injected",
            "summary": "运行中用户引导已注入当前 Supervisor 主链。",
            "traceGroupId": trace_group_id,
            "surfaceTargets": ["message", "runtime_card"],
            "targets": ["message", "runtime_card"],
            "displayInMessage": True,
        }
        event = self._emit_message_targeted_runtime_event(
            chat_run,
            stream_state,
            topic="human_guidance.injected",
            payload=payload,
            node=node,
            agent_id=None,
            runtime_node="human_guidance_queue",
            state="streaming",
        )
        db.update_chat_user_message_queue_item(
            queue_id,
            state="injected",
            timestamp_field="injected_at",
        )
        return event

    @staticmethod
    def _runtime_topic_prefix(runtime_id: str) -> str:
        if runtime_id == "subagent_swarm":
            return "subagent"
        return runtime_id or "chat"

    @staticmethod
    def _owner_kind_for_agent(agent_id: str) -> str:
        normalized = str(agent_id or "").strip().lower()
        if not normalized or normalized == "supervisor":
            return "supervisor"
        if "shard" in normalized or "research" in normalized:
            return "shard"
        return "subagent"

    def _resolve_event_owner(self, stream_state: ChatStreamState, *, tool_name: str | None = None) -> dict[str, Any]:
        normalized_tool = str(tool_name or "").strip().lower()
        current_agent = str(stream_state.current_agent or "supervisor").strip() or "supervisor"
        owner_kind = self._owner_kind_for_agent(current_agent)
        owner_runtime_id = "chat" if owner_kind == "supervisor" else "subagent_swarm"

        if normalized_tool:
            if normalized_tool == "research_broker" or normalized_tool.startswith("research_"):
                owner_runtime_id = "research"
                owner_kind = "runtime"
            elif normalized_tool.startswith("creative_media_"):
                owner_runtime_id = "creative_media"
                owner_kind = "runtime"
            elif normalized_tool.startswith("computer_use_"):
                owner_runtime_id = "computer_use"
                owner_kind = "runtime"
            elif normalized_tool.startswith("rpa_"):
                owner_runtime_id = "rpa"
                owner_kind = "runtime"
            elif normalized_tool in {"delegation_broker", "subagent_broker"} or normalized_tool.startswith("subagent_"):
                owner_runtime_id = "subagent_swarm"
                owner_kind = "subagent"
            elif owner_kind != "supervisor":
                owner_runtime_id = "subagent_swarm"

        return {
            "ownerRuntimeId": owner_runtime_id,
            "ownerAgentKind": owner_kind,
            "ownerAgentId": current_agent,
            "displayInMessage": owner_runtime_id == "chat" and owner_kind == "supervisor",
        }

    def _event_targets_for_owner(self, owner: dict[str, Any], *, event_kind: str) -> list[str]:
        if bool(owner.get("displayInMessage")):
            if event_kind in {"tool_start", "tool_result"}:
                return ["message", "runtime_card", "process"]
            if event_kind == "reasoning_chunk":
                return ["message", "runtime_card"]
            if event_kind == "agent_start":
                return ["runtime_card", "hud"]
            return ["message"]
        if event_kind in {"tool_start", "tool_result"}:
            return ["runtime_card", "runtime_timeline", "process"]
        if event_kind == "agent_start":
            return ["runtime_card", "runtime_timeline", "hud"]
        if event_kind == "text_chunk":
            return ["runtime_card", "runtime_timeline", "hud"]
        return ["runtime_card", "runtime_timeline"]

    def _apply_event_owner_fields(
        self,
        payload: dict[str, Any],
        owner: dict[str, Any],
        *,
        event_kind: str,
        stream_key: str,
    ) -> dict[str, Any]:
        targets = self._event_targets_for_owner(owner, event_kind=event_kind)
        return {
            **payload,
            "runtimeId": owner.get("ownerRuntimeId"),
            "ownerRuntimeId": owner.get("ownerRuntimeId"),
            "ownerAgentKind": owner.get("ownerAgentKind"),
            "ownerAgentId": owner.get("ownerAgentId"),
            "ownerStreamKey": stream_key,
            "surfaceTargets": targets,
            "targets": targets,
            "displayInMessage": bool(owner.get("displayInMessage")),
        }

    def _emit_owner_scoped_runtime_event(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        *,
        topic: str,
        payload: dict[str, Any],
        owner: dict[str, Any],
        event_kind: str,
        stream_key: str,
        node: dict[str, Any] | None = None,
        state: str = "streaming",
        finalize: bool = False,
    ) -> dict[str, Any]:
        trace_group_id: str | None = None
        if bool(owner.get("displayInMessage")) and event_kind in {"reasoning_chunk", "tool_start", "tool_result"}:
            if not stream_state.active_trace_group_id:
                stream_state.trace_group_seq += 1
                message_id = stream_state.assistant_message_id or self._ensure_assistant_canonical_message(chat_run, stream_state)
                stream_state.active_trace_group_id = f"{message_id}:trace:{stream_state.trace_group_seq}"
            trace_group_id = stream_state.active_trace_group_id
            payload = {**payload, "traceGroupId": trace_group_id}
        enriched_payload = self._apply_event_owner_fields(
            payload,
            owner,
            event_kind=event_kind,
            stream_key=stream_key,
        )
        if node is not None:
            owner_node_fields = {
                "ownerRuntimeId": owner.get("ownerRuntimeId"),
                "ownerAgentKind": owner.get("ownerAgentKind"),
                "ownerAgentId": owner.get("ownerAgentId"),
                "ownerStreamKey": stream_key,
                "displayInMessage": bool(owner.get("displayInMessage")),
            }
            if trace_group_id:
                owner_node_fields["traceGroupId"] = trace_group_id
            node = {**node, **owner_node_fields}
        if bool(owner.get("displayInMessage")):
            return self._emit_message_targeted_runtime_event(
                chat_run,
                stream_state,
                topic=topic,
                payload=enriched_payload,
                node=node,
                agent_id=str(owner.get("ownerAgentId") or stream_state.current_agent),
                runtime_node=str(owner.get("ownerAgentId") or stream_state.current_agent),
                state=state,
                finalize=finalize,
            )
        return chat_run.emit_runtime_event(
            topic,
            enriched_payload,
            agent_id=str(owner.get("ownerAgentId") or stream_state.current_agent),
            node=str(owner.get("ownerRuntimeId") or stream_state.current_agent),
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

    def _maybe_note_delegation_claim(self, stream_state: ChatStreamState, content: str) -> None:
        text = str(content or "").strip()
        if not text or not self.DELEGATION_CLAIM_RE.search(text):
            return
        stream_state.delegation_claim_detected = True
        if len(stream_state.delegation_claim_samples) >= 3:
            return
        compact = re.sub(r"\s+", " ", text)
        stream_state.delegation_claim_samples.append(compact[:240])

    def _emit_delegation_claim_diagnostic(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> None:
        if (
            stream_state.delegation_claim_diagnostic_emitted
            or not stream_state.delegation_claim_detected
            or stream_state.delegation_dispatch_seen
        ):
            return
        stream_state.delegation_claim_diagnostic_emitted = True
        chat_run.emit_runtime_event(
            "subagent.delegation.claimed_without_dispatch",
            {
                "riskCode": "delegation_claim_without_dispatch",
                "summary": "Supervisor 文本声称派发了子代理，但本轮没有 delegation_broker 或 subagent 事件。",
                "samples": list(stream_state.delegation_claim_samples),
                "actualDispatchSeen": False,
                "recommendedNextAction": "若要派发子代理，必须调用 delegation_broker；若由 Supervisor 直接执行，应明确说明直接执行。",
            },
            agent_id=stream_state.current_agent,
            node="subagent_swarm",
        )

    def _maybe_emit_supervisor_direct_scope_diagnostic(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        *,
        tool_name: str,
        owner: dict[str, Any],
    ) -> None:
        if stream_state.supervisor_direct_scope_exceeded_emitted:
            return
        if not bool(owner.get("displayInMessage")) or str(owner.get("ownerAgentKind") or "") != "supervisor":
            return
        normalized_tool = str(tool_name or "").strip()
        if normalized_tool in {"delegation_broker", "runtime_broker"}:
            return
        project_write_tools = {"write_native_file", "replace_native_file", "edit_native_file", "delete_native_file"}
        if normalized_tool in project_write_tools:
            stream_state.supervisor_project_write_count += 1
        stream_state.supervisor_tool_step_count += 1
        exceeded_reasons: list[str] = []
        if stream_state.supervisor_tool_step_count > 10:
            exceeded_reasons.append("tool_steps_gt_10")
        if stream_state.supervisor_project_write_count > 3:
            exceeded_reasons.append("project_file_writes_gt_3")
        if not exceeded_reasons:
            return
        stream_state.supervisor_direct_scope_exceeded_emitted = True
        stream_state.supervisor_direct_scope_gate_active = True
        chat_run.emit_runtime_event(
            "supervisor.direct_scope.exceeded",
            {
                "riskCode": "supervisor_direct_scope_exceeded",
                "summary": "Supervisor direct 执行已超过小任务阈值，应转入 Engineering/delegation 或明确说明继续 direct 的理由。",
                "toolStepCount": stream_state.supervisor_tool_step_count,
                "projectWriteCount": stream_state.supervisor_project_write_count,
                "latestTool": normalized_tool,
                "reasons": exceeded_reasons,
                "recommendedNextAction": "调用 delegation_broker 派发 engineering family/external worker，或进入 Engineering proof/workset 纪律后继续。",
            },
            agent_id=stream_state.current_agent,
            node="supervisor_direct_scope_guard",
        )

    def _enforce_supervisor_direct_scope_gate(
        self,
        chat_run: ChatRunContext,
        stream_state: ChatStreamState,
        *,
        tool_name: str,
        owner: dict[str, Any],
    ) -> bool:
        normalized_tool = str(tool_name or "").strip()
        if normalized_tool == "delegation_broker":
            stream_state.supervisor_direct_scope_gate_active = False
            return False
        if not stream_state.supervisor_direct_scope_gate_active:
            return False
        if not bool(owner.get("displayInMessage")) or str(owner.get("ownerAgentKind") or "") != "supervisor":
            return False
        allowed_escape_tools = {"delegation_broker", "runtime_broker", "ask_user", "write_todos", "update_todo"}
        if normalized_tool in allowed_escape_tools:
            return False
        gated_tools = {
            "run_system_command",
            "command_session_broker",
            "write_native_file",
            "replace_native_file",
            "edit_native_file",
            "delete_native_file",
            "creative_media_create_job",
            "creative_media_retry_job",
            "computer_use_execute",
            "computer_use_click",
            "computer_use_type_text",
            "computer_use_drag",
        }
        if normalized_tool not in gated_tools and not normalized_tool.startswith(("creative_media_", "computer_use_")):
            return False
        operation_fingerprint = _supervisor_direct_scope_operation_fingerprint(chat_run.active_run_id)
        if self._is_supervisor_direct_scope_exception_approved(chat_run.active_run_id, operation_fingerprint):
            stream_state.supervisor_direct_scope_gate_active = False
            chat_run.emit_runtime_event(
                "supervisor.direct_scope.exception_approved",
                {
                    "riskCode": "supervisor_direct_scope_exception_approved",
                    "summary": "用户已批准本轮 Supervisor direct exception，允许继续当前 direct 执行路径。",
                    "approvedOperationFingerprint": operation_fingerprint,
                    "latestTool": normalized_tool,
                },
                agent_id=stream_state.current_agent,
                node="supervisor_direct_scope_guard",
            )
            return False
        payload = {
            "riskCode": "supervisor_direct_scope_blocked",
            "summary": "Supervisor direct 执行已进入硬门禁；复杂任务后续可变更/长耗时工具必须先进入 delegation、Engineering discipline 或用户批准 direct exception。",
            "blockedTool": normalized_tool,
            "toolStepCount": stream_state.supervisor_tool_step_count,
            "projectWriteCount": stream_state.supervisor_project_write_count,
            "allowedNextTools": ["delegation_broker", "runtime_broker", "ask_user"],
            "recommendedNextAction": "调用 delegation_broker 派发 engineering family/external worker，或请求用户批准继续 direct exception。",
            "operationFingerprint": operation_fingerprint,
            "operationTargetFingerprint": operation_fingerprint,
        }
        chat_run.emit_runtime_event(
            "supervisor.direct_scope.blocked",
            payload,
            agent_id=stream_state.current_agent,
            node="supervisor_direct_scope_guard",
        )
        # Tool-start callbacks are an observation surface, not the safe place to
        # prevent side effects. The actual pre-execution stop lives in
        # graph.tool_routing.async_tool_call_wrapper; this event keeps Phone/Web
        # diagnostics in the correct timeline without turning the whole run into
        # a generic failure.
        return True

    @staticmethod
    def _is_supervisor_direct_scope_exception_approved(run_id: str, operation_fingerprint: str) -> bool:
        if not run_id or not operation_fingerprint:
            return False
        try:
            run_record = run_service.get_run(run_id)
        except Exception:
            return False
        metadata = dict((run_record or {}).get("metadata") or {})
        operations = metadata.get("approvedSafetyOperations")
        if not isinstance(operations, list):
            return False
        for item in operations:
            if not isinstance(item, dict):
                continue
            if str(item.get("fingerprint") or "").strip() != operation_fingerprint:
                continue
            if str(item.get("approval_kind") or "").strip() == "supervisor_direct_scope_exception":
                return True
        return False

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
        provider_delta_at_ms: int | None = None,
        canonical_event_at_ms: int | None = None,
        partial: bool = False,
    ) -> dict[str, Any] | None:
        if not stable_chunk:
            return None
        profile = self._get_agent_profile(stream_state.current_agent)
        run_key = self._normalized_stream_run_id(model_run_id)
        owner = self._resolve_event_owner(stream_state)
        owner_runtime_id = str(owner.get("ownerRuntimeId") or "chat")
        topic = "run.text.delta" if bool(owner.get("displayInMessage")) else f"{self._runtime_topic_prefix(owner_runtime_id)}.text.delta"
        if bool(owner.get("displayInMessage")):
            segment_seq = int(stream_state.text_segment_seq_by_run.get(run_key) or 0) + 1
            stream_state.text_segment_seq_by_run[run_key] = segment_seq
            stream_run_key = f"{owner_runtime_id}:{stream_state.current_agent}:text:{run_key}"
            stream_key = f"{stream_run_key}:segment:{segment_seq}"
            node_content = stable_chunk
            stream_state.last_text_delta_at_ms = self._now_timestamp_ms()
            self._maybe_note_delegation_claim(stream_state, node_content)
        else:
            segment_seq = int(stream_state.text_segment_seq_by_run.get(run_key) or 0) + 1
            stream_state.text_segment_seq_by_run[run_key] = segment_seq
            stream_run_key = f"{owner_runtime_id}:{stream_state.current_agent}:text:{run_key}"
            stream_key = f"{stream_run_key}:segment:{segment_seq}"
            node_content = snapshot or stream_state.text_snapshots_by_run.get(run_key) or stable_chunk
        text_event = {
            "type": "text_chunk",
            "content": stable_chunk,
            "snapshot": node_content,
            "streamRunKey": stream_run_key,
            "segmentKey": stream_key,
            "segmentSeq": segment_seq,
            "finalized": True,
            "partial": bool(partial),
            "timestamp": 0,
            "_diagnostics": self._stream_trace_diagnostics(
                chat_run,
                kind="text_delta",
                delta=stable_chunk,
                provider_delta_at_ms=provider_delta_at_ms,
                canonical_event_at_ms=canonical_event_at_ms,
                model_run_id=model_run_id,
            ),
        }
        narrative_node = {
            "id": (
                f"{self._ensure_assistant_canonical_message(chat_run, stream_state)}:narrative:{stream_key}"
                if bool(owner.get("displayInMessage"))
                else f"runtime:{chat_run.active_run_id}:narrative:{stream_key}"
            ),
            "kind": "narrative",
            "role": "assistant",
            "content": node_content,
            "timestamp": self._now_timestamp_ms(),
            "agentName": profile["name"],
            "agentAvatar": profile["avatar"],
            "agentRoleLabel": profile["roleLabel"],
            "finalized": True,
            "partial": bool(partial),
        }
        runtime_event = self._emit_owner_scoped_runtime_event(
            chat_run,
            stream_state,
            topic=topic,
            payload=text_event,
            owner=owner,
            event_kind="text_chunk",
            stream_key=stream_key,
            node=narrative_node,
        )
        if bool(owner.get("displayInMessage")):
            stream_state.active_trace_group_id = None
        payload = runtime_event.get("payload") if isinstance(runtime_event, dict) else None
        if isinstance(payload, dict):
            text_event["message_id"] = payload.get("message_id")
            text_event["node_id"] = payload.get("node_id")
            text_event["transcript_version"] = payload.get("transcript_version")
            text_event.update(
                {
                    "runtimeId": payload.get("runtimeId"),
                    "ownerRuntimeId": payload.get("ownerRuntimeId"),
                    "ownerAgentKind": payload.get("ownerAgentKind"),
                    "ownerAgentId": payload.get("ownerAgentId"),
                    "ownerStreamKey": payload.get("ownerStreamKey"),
                    "targets": payload.get("targets"),
                    "surfaceTargets": payload.get("surfaceTargets"),
                    "displayInMessage": payload.get("displayInMessage"),
                }
            )
        if bool(owner.get("displayInMessage")):
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
        reasoning_kind: str = "provider_reasoning",
        reasoning_surface: dict[str, Any] | None = None,
        provider_delta_at_ms: int | None = None,
        canonical_event_at_ms: int | None = None,
    ) -> dict[str, Any] | None:
        if not reasoning_delta:
            return None
        profile = self._get_agent_profile(stream_state.current_agent)
        run_key = self._normalized_stream_run_id(model_run_id)
        owner = self._resolve_event_owner(stream_state)
        owner_runtime_id = str(owner.get("ownerRuntimeId") or "chat")
        stream_key = f"{owner_runtime_id}:{stream_state.current_agent}:reasoning:{run_key}"
        topic = "run.reasoning.delta" if bool(owner.get("displayInMessage")) else f"{self._runtime_topic_prefix(owner_runtime_id)}.reasoning.delta"
        node_content = snapshot or stream_state.reasoning_snapshots_by_run.get(run_key) or reasoning_delta
        reasoning_event = {
            "type": "reasoning_chunk",
            "content": reasoning_delta,
            "snapshot": node_content,
            "reasoningKind": reasoning_kind,
            "reasoningSurface": reasoning_surface or {},
            "timestamp": 0,
            "_diagnostics": self._stream_trace_diagnostics(
                chat_run,
                kind="reasoning_delta",
                delta=reasoning_delta,
                provider_delta_at_ms=provider_delta_at_ms,
                canonical_event_at_ms=canonical_event_at_ms,
                model_run_id=model_run_id,
            ),
        }
        reasoning_node = {
            "id": (
                f"{self._ensure_assistant_canonical_message(chat_run, stream_state)}:reasoning:{stream_key}"
                if bool(owner.get("displayInMessage"))
                else f"runtime:{chat_run.active_run_id}:reasoning:{stream_key}"
            ),
            "kind": "execution",
            "executionType": "reasoning",
            "content": node_content,
            "reasoningKind": reasoning_kind,
            "timestamp": self._now_timestamp_ms(),
            "agentName": profile["name"],
            "agentAvatar": profile["avatar"],
            "agentRoleLabel": profile["roleLabel"],
            "data": {
                "reasoningKind": reasoning_kind,
                "reasoningSurface": reasoning_surface or {},
            },
        }
        runtime_event = self._emit_owner_scoped_runtime_event(
            chat_run,
            stream_state,
            topic=topic,
            payload=reasoning_event,
            owner=owner,
            event_kind="reasoning_chunk",
            stream_key=stream_key,
            node=reasoning_node,
        )
        payload = runtime_event.get("payload") if isinstance(runtime_event, dict) else None
        if isinstance(payload, dict):
            reasoning_event["message_id"] = payload.get("message_id")
            reasoning_event["node_id"] = payload.get("node_id")
            reasoning_event["transcript_version"] = payload.get("transcript_version")
            reasoning_event.update(
                {
                    "runtimeId": payload.get("runtimeId"),
                    "ownerRuntimeId": payload.get("ownerRuntimeId"),
                    "ownerAgentKind": payload.get("ownerAgentKind"),
                    "ownerAgentId": payload.get("ownerAgentId"),
                    "ownerStreamKey": payload.get("ownerStreamKey"),
                    "targets": payload.get("targets"),
                    "surfaceTargets": payload.get("surfaceTargets"),
                    "displayInMessage": payload.get("displayInMessage"),
                }
            )
        if bool(owner.get("displayInMessage")):
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
        provider_delta_at_ms: int | None = None,
        canonical_event_at_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        emitted_events: list[dict[str, Any]] = []
        if not delta:
            return emitted_events
        owner = self._resolve_event_owner(stream_state)
        resolved_snapshot = None if bool(owner.get("displayInMessage")) else snapshot
        if not bool(owner.get("displayInMessage")):
            text_event = await self._emit_stable_text_chunk(
                chat_run,
                stream_state,
                delta,
                model_run_id=model_run_id,
                snapshot=resolved_snapshot,
                provider_delta_at_ms=provider_delta_at_ms,
                canonical_event_at_ms=canonical_event_at_ms,
            )
            return [text_event] if text_event is not None else []
        if stream_state.preserve_stream_timeline:
            self._clear_text_flush_deadline(stream_state)
            text_event = await self._emit_stable_text_chunk(
                chat_run,
                stream_state,
                delta,
                model_run_id=model_run_id,
                snapshot=resolved_snapshot,
                provider_delta_at_ms=provider_delta_at_ms,
                canonical_event_at_ms=canonical_event_at_ms,
            )
            return [text_event] if text_event is not None else []
        for stable_chunk in stream_state.text_aggregator.push(delta):
            if not stable_chunk:
                continue
            text_event = await self._emit_stable_text_chunk(
                chat_run,
                stream_state,
                stable_chunk,
                model_run_id=model_run_id,
                snapshot=resolved_snapshot,
                provider_delta_at_ms=provider_delta_at_ms,
                canonical_event_at_ms=canonical_event_at_ms,
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
        ready_only: bool = False,
    ) -> list[dict[str, Any]]:
        self._clear_text_flush_deadline(stream_state)
        flush_ready = stream_state.text_aggregator.should_flush_now()
        if (from_timer or ready_only) and not flush_ready:
            if from_timer:
                stream_state.text_timer_deferrals += 1
            self._schedule_text_flush_deadline(stream_state)
            return []
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
            snapshot=None,
            partial=bool(final and not flush_ready),
        )
        return [text_event] if text_event is not None else []

    def _emit_text_stream_diagnostics(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> None:
        if (
            stream_state.text_raw_chars <= 0
            and stream_state.text_emitted_chunks <= 0
            and stream_state.text_timer_flushes <= 0
            and stream_state.text_timer_deferrals <= 0
            and stream_state.text_final_flush_chars <= 0
        ):
            return
        chat_run.emit_runtime_event(
            "run.text_stream.diagnostics",
            {
                "rawTextChars": stream_state.text_raw_chars,
                "emittedTextChunkCount": stream_state.text_emitted_chunks,
                "timerFlushCount": stream_state.text_timer_flushes,
                "timerDeferralCount": stream_state.text_timer_deferrals,
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
                "activeToolCallIds": sorted(str(item) for item in stream_state.watchdog.active_tool_call_ids),
                "activeRuntimeToolCallIds": sorted(str(item) for item in stream_state.active_tool_call_ids),
                "lastObservedEvent": stream_state.watchdog.last_observed_event,
                "lastGraphEventKind": stream_state.last_graph_event_kind or stream_state.watchdog.last_observed_event,
                "lastGraphEventAtMs": stream_state.last_graph_event_at_ms,
                "lastTextDeltaAtMs": stream_state.last_text_delta_at_ms,
                "lastTextDeltaPreview": str(stream_state.last_text_delta or "")[:200],
                "provider": str(getattr(chat_run.request.config, "provider", "") or "").strip(),
                "model": str(getattr(chat_run.request.config, "model_name", "") or "").strip(),
                "recoverable": True,
                "failureClass": "stream_idle_timeout",
                "recommendedNextAction": "模型流 45 秒没有新事件；可重试本轮、检查 provider streaming，若后台命令仍在运行请先查看 Command card。",
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
        kind = str(event.get("event") or "").strip()
        name = str(event.get("name") or "").strip()
        stream_state.last_graph_event_kind = f"{kind}:{name}" if name else kind
        stream_state.last_graph_event_at_ms = self._now_timestamp_ms()
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
        if stream_state.output_text_run_order:
            return "".join(
                stream_state.output_text_by_run.get(run_key, "")
                for run_key in stream_state.output_text_run_order
            )
        return "".join(stream_state.output_buffer)

    def _append_message_output_text(
        self,
        stream_state: ChatStreamState,
        *,
        model_run_id: str,
        delta: str = "",
        replacement_snapshot: str | None = None,
    ) -> None:
        run_key = self._normalized_stream_run_id(model_run_id)
        if run_key not in stream_state.output_text_run_order:
            stream_state.output_text_run_order.append(run_key)
            stream_state.output_text_by_run.setdefault(run_key, "")
        if replacement_snapshot is not None:
            stream_state.output_text_by_run[run_key] = replacement_snapshot
        elif delta:
            stream_state.output_text_by_run[run_key] = stream_state.output_text_by_run.get(run_key, "") + delta
        stream_state.output_buffer = [
            stream_state.output_text_by_run.get(item, "")
            for item in stream_state.output_text_run_order
        ]

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

    @staticmethod
    def _trim_preview_text(value: str, *, limit: int = 1200) -> tuple[str, bool]:
        normalized = str(value or "")
        if len(normalized) <= limit:
            return normalized, False
        return normalized[:limit].rstrip(), True

    @staticmethod
    def _line_count(value: str) -> int:
        if not value:
            return 0
        return len(str(value).splitlines()) or 1

    @classmethod
    def _coerce_json_like_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, (dict, list, str, int, float, bool)):
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith("{") or stripped.startswith("["):
                    try:
                        return json.loads(stripped)
                    except Exception:
                        return value
            return value
        return to_jsonable(value)

    @classmethod
    def _extract_tool_call_id_from_value(cls, value: Any, *, depth: int = 0) -> str:
        if depth > 3 or value is None:
            return ""
        candidate = cls._coerce_json_like_value(value)
        if isinstance(candidate, dict):
            direct = str(
                candidate.get("toolCallId")
                or candidate.get("tool_call_id")
                or candidate.get("toolCallID")
                or ""
            ).strip()
            if direct:
                return direct
            tool_call = candidate.get("tool_call")
            if isinstance(tool_call, dict):
                nested_direct = str(tool_call.get("id") or tool_call.get("toolCallId") or "").strip()
                if nested_direct:
                    return nested_direct
            for nested_key in ("request", "input", "kwargs", "metadata", "additional_kwargs"):
                nested = candidate.get(nested_key)
                nested_result = cls._extract_tool_call_id_from_value(nested, depth=depth + 1)
                if nested_result:
                    return nested_result
        return ""

    @classmethod
    def _extract_provider_tool_call_id_from_value(cls, value: Any, *, depth: int = 0) -> str:
        if depth > 3 or value is None:
            return ""
        candidate = cls._coerce_json_like_value(value)
        if isinstance(candidate, dict):
            direct = str(
                candidate.get("providerToolCallId")
                or candidate.get("provider_tool_call_id")
                or ""
            ).strip()
            if direct:
                return direct
            tool_call = candidate.get("tool_call")
            if isinstance(tool_call, dict):
                nested_direct = str(
                    tool_call.get("providerToolCallId")
                    or tool_call.get("provider_tool_call_id")
                    or ""
                ).strip()
                if nested_direct:
                    return nested_direct
            for nested_key in ("request", "input", "kwargs", "metadata", "additional_kwargs"):
                nested = candidate.get(nested_key)
                nested_result = cls._extract_provider_tool_call_id_from_value(nested, depth=depth + 1)
                if nested_result:
                    return nested_result
        return ""

    @classmethod
    def _extract_provider_standard_from_value(cls, value: Any, *, depth: int = 0) -> str:
        if depth > 3 or value is None:
            return ""
        candidate = cls._coerce_json_like_value(value)
        if isinstance(candidate, dict):
            direct = str(
                candidate.get("providerStandard")
                or candidate.get("provider_standard")
                or ""
            ).strip().lower()
            if direct:
                return direct
            tool_call = candidate.get("tool_call")
            if isinstance(tool_call, dict):
                nested_direct = str(
                    tool_call.get("providerStandard")
                    or tool_call.get("provider_standard")
                    or ""
                ).strip().lower()
                if nested_direct:
                    return nested_direct
            for nested_key in ("request", "input", "kwargs", "metadata", "additional_kwargs"):
                nested = candidate.get(nested_key)
                nested_result = cls._extract_provider_standard_from_value(nested, depth=depth + 1)
                if nested_result:
                    return nested_result
        return ""

    @classmethod
    def _extract_canonical_tool_call_id_from_value(cls, value: Any, *, depth: int = 0) -> str:
        candidate = cls._extract_tool_call_id_from_value(value, depth=depth)
        return candidate if is_v8_canonical_tool_call_id(candidate) else ""

    @classmethod
    def _resolve_known_active_tool_call_id(
        cls,
        candidate: Any,
        *,
        stream_state: ChatStreamState,
    ) -> str:
        normalized = str(candidate or "").strip()
        if not normalized:
            return ""
        if is_v8_canonical_tool_call_id(normalized):
            return normalized
        if normalized in stream_state.active_tool_call_ids:
            return normalized
        if any(str((call or {}).get("id") or "").strip() == normalized for call in list(stream_state.tool_calls_buffer or [])):
            return normalized
        return ""

    @classmethod
    def _compact_tool_display_args(cls, tool_name: str, value: Any) -> Any:
        sanitized = cls._sanitize_tool_input_value(value)
        normalized_tool_name = str(tool_name or "").strip().lower()
        if not isinstance(sanitized, dict):
            return sanitized

        if normalized_tool_name == "ask_user":
            compact: dict[str, Any] = {}
            question = str(sanitized.get("question") or sanitized.get("prompt") or "").strip()
            if question:
                compact["question"] = question
            details = sanitized.get("details")
            if details not in (None, "", [], {}):
                compact["details"] = details
            tool_call_id = str(sanitized.get("toolCallId") or sanitized.get("tool_call_id") or "").strip()
            if tool_call_id:
                compact["toolCallId"] = tool_call_id
            return compact

        if normalized_tool_name == "run_system_command":
            compact = {
                "command": sanitized.get("command"),
                "mode": sanitized.get("mode"),
                "profile": sanitized.get("profile"),
            }
            return {key: val for key, val in compact.items() if val not in (None, "", [], {})}

        if normalized_tool_name == "command_session_broker":
            compact = {
                "mode": sanitized.get("mode"),
                "command": sanitized.get("command"),
                "commandId": sanitized.get("commandId") or sanitized.get("command_id"),
                "sessionId": sanitized.get("sessionId") or sanitized.get("session_id"),
                "profile": sanitized.get("profile"),
            }
            input_text = str(sanitized.get("inputText") or sanitized.get("input_text") or "").strip()
            if input_text:
                preview, truncated = cls._trim_preview_text(input_text, limit=200)
                compact["inputPreview"] = preview
                if truncated:
                    compact["inputTruncated"] = True
            if sanitized.get("debug") is True:
                compact["debug"] = True
            return {key: val for key, val in compact.items() if val not in (None, "", [], {})}

        if normalized_tool_name == "delegation_broker":
            compact = {
                "mode": sanitized.get("mode"),
                "delegationId": sanitized.get("delegationId") or sanitized.get("delegation_id"),
            }
            followup = str(sanitized.get("followup") or "").strip()
            if followup:
                preview, truncated = cls._trim_preview_text(followup, limit=200)
                compact["followupPreview"] = preview
                if truncated:
                    compact["followupTruncated"] = True
            if isinstance(sanitized.get("tasks"), list):
                task_previews: list[dict[str, Any]] = []
                for task in list(sanitized.get("tasks") or [])[:6]:
                    if not isinstance(task, dict):
                        continue
                    task_previews.append(
                        {
                            "taskBriefId": task.get("taskBriefId") or task.get("task_brief_id"),
                            "goal": task.get("goal"),
                            "executionLaneHint": task.get("executionLaneHint") or task.get("execution_lane_hint"),
                            "preferredAgentId": task.get("preferredAgentId") or task.get("preferred_agent_id"),
                            "preferredWorkerType": task.get("preferredWorkerType") or task.get("preferred_worker_type"),
                        }
                    )
                if task_previews:
                    compact["tasks"] = task_previews
            return {key: val for key, val in compact.items() if val not in (None, "", [], {})}

        if normalized_tool_name == "web_broker":
            compact = {
                "mode": sanitized.get("mode"),
                "target": sanitized.get("target"),
                "extract": sanitized.get("extract"),
                "searchEngine": sanitized.get("search_engine") or sanitized.get("searchEngine"),
                "fetchMode": sanitized.get("fetch_mode") or sanitized.get("fetchMode"),
                "limit": sanitized.get("limit"),
            }
            if sanitized.get("adaptive") is True:
                compact["adaptive"] = True
            if sanitized.get("debug") is True:
                compact["debug"] = True
            return {key: val for key, val in compact.items() if val not in (None, "", [], {})}

        if normalized_tool_name == "s3_broker":
            compact = {
                "mode": sanitized.get("mode"),
                "filePath": sanitized.get("file_path") or sanitized.get("filePath"),
                "key": sanitized.get("key"),
                "prefix": sanitized.get("prefix"),
                "destinationPath": sanitized.get("destination_path") or sanitized.get("destinationPath"),
                "limit": sanitized.get("limit"),
            }
            return {key: val for key, val in compact.items() if val not in (None, "", [], {})}

        return sanitized

    @classmethod
    def _compact_command_session_broker_result(cls, value: Any) -> Any:
        candidate = cls._coerce_json_like_value(value)
        if not isinstance(candidate, dict):
            return value
        return cls._render_command_session_terminal_surface(candidate)

    @staticmethod
    def _command_surface_raw_ref(candidate: dict[str, Any]) -> str:
        surface = candidate.get("_v8ToolSurface")
        if isinstance(surface, dict):
            return str(surface.get("rawRef") or "").strip()
        return ""

    @classmethod
    def _append_command_control_line(
        cls,
        lines: list[str],
        label: str,
        value: Any,
    ) -> None:
        text = str(value or "").strip()
        if text:
            lines.append(f"[{label}: {text}]")

    @classmethod
    def _append_command_stream(
        cls,
        lines: list[str],
        tag: str,
        value: Any,
        *,
        truncated: bool = False,
        raw_ref: str = "",
        limit: int = 2400,
    ) -> None:
        text = str(value or "").strip()
        if not text:
            return
        trimmed, was_truncated = cls._trim_preview_text(text, limit=limit)
        lines.append(f"<{tag}>")
        lines.append(trimmed)
        lines.append(f"</{tag}>")
        if truncated or was_truncated:
            suffix = f"; rawRef={raw_ref}" if raw_ref else ""
            lines.append(f"[{tag} truncated{suffix}]")

    @staticmethod
    def _strip_command_echo_from_stream(command: str, value: Any) -> str:
        text = str(value or "").strip()
        rendered_command = str(command or "").strip()
        if not text or not rendered_command:
            return text
        lines = text.splitlines()
        while lines:
            first = lines[0].strip()
            if first == rendered_command or first.endswith(f">{rendered_command}") or first.endswith(f"$ {rendered_command}"):
                lines.pop(0)
                continue
            break
        return "\n".join(lines).strip()

    @classmethod
    def _render_command_terminal_surface(
        cls,
        *,
        command: str = "",
        stdout: Any = "",
        stderr: Any = "",
        exit_code: Any = None,
        session_id: str = "",
        state: str = "",
        waiting_input: bool = False,
        still_running: bool = False,
        no_output_text: str = "[completed with no output]",
        raw_ref: str = "",
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
        control_lines: list[str] | None = None,
    ) -> str:
        lines: list[str] = []
        rendered_command = str(command or "").strip()
        if rendered_command:
            lines.append(f"$ {rendered_command}")
        elif session_id:
            lines.append(f"$ <command session {session_id}>")
        else:
            lines.append("$ <command>")

        cleaned_stdout = cls._strip_command_echo_from_stream(rendered_command, stdout)
        cleaned_stderr = cls._strip_command_echo_from_stream(rendered_command, stderr)
        cls._append_command_stream(
            lines,
            "stdout",
            cleaned_stdout,
            truncated=stdout_truncated,
            raw_ref=raw_ref,
        )
        cls._append_command_stream(
            lines,
            "stderr",
            cleaned_stderr,
            truncated=stderr_truncated,
            raw_ref=raw_ref,
        )

        has_visible_stream = any(line in {"<stdout>", "<stderr>"} for line in lines)
        for line in control_lines or []:
            normalized = str(line or "").strip()
            if normalized:
                lines.append(normalized)
        if waiting_input:
            lines.append("[waiting for input]")
        if still_running:
            lines.append("[still running]")
        if exit_code not in (None, "", [], 0, "0"):
            lines.append(f"[exit code: {exit_code}]")
        if not has_visible_stream and not waiting_input and not still_running and exit_code in (None, 0, "0"):
            lines.append(no_output_text)
        return "\n".join(lines).strip()

    @classmethod
    def _render_command_session_terminal_surface(cls, candidate: dict[str, Any]) -> str:
        session_id = str(candidate.get("sessionId") or candidate.get("commandId") or "").strip()
        state = str(candidate.get("state") or "").strip().lower()
        command = str(candidate.get("command") or "").strip()
        raw_ref = cls._command_surface_raw_ref(candidate)
        waiting_input = bool(candidate.get("awaitingInput")) or state == "awaiting_input"
        still_running = state in {"running", "render_stalled", "recoverable_stalled"} and not waiting_input
        exit_code = candidate.get("returnCode")

        stdout_candidates = (
            (
                candidate.get("finalPreview"),
                candidate.get("keyOutput"),
                candidate.get("deltaText"),
                candidate.get("outputPreview"),
            )
            if state in {"completed", "failed"}
            else (
                candidate.get("deltaText"),
                candidate.get("keyOutput"),
                candidate.get("outputPreview"),
                candidate.get("finalPreview"),
            )
        )
        stdout = next((item for item in stdout_candidates if item not in (None, "")), "")
        control_lines: list[str] = []
        if session_id and state not in {"completed", "failed"}:
            control_lines.append(f"[session: {session_id}]")
        if state == "recoverable_stalled":
            control_lines.append("[command appears stalled; observe later or terminate]")
        elif state == "render_stalled":
            control_lines.append("[terminal screen is still settling]")
        if candidate.get("terminated"):
            control_lines.append("[terminated]")
        error = str(candidate.get("error") or "").strip()
        stderr = error if error else ""
        return cls._render_command_terminal_surface(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            session_id=session_id,
            state=state,
            waiting_input=waiting_input,
            still_running=still_running,
            no_output_text="[no new output]" if state not in {"completed", "failed"} else "[completed with no output]",
            raw_ref=raw_ref,
            stdout_truncated=bool(
                candidate.get("deltaTruncated")
                or candidate.get("keyOutputTruncated")
                or candidate.get("outputPreviewTruncated")
                or candidate.get("finalPreviewTruncated")
            ),
            stderr_truncated=False,
            control_lines=control_lines,
        )

    @classmethod
    def _legacy_compact_command_session_broker_result(cls, value: Any) -> Any:
        candidate = cls._coerce_json_like_value(value)
        if not isinstance(candidate, dict):
            return value
        session_id = candidate.get("sessionId")
        command_id = candidate.get("commandId")
        compact: dict[str, Any] = {
            "mode": candidate.get("mode"),
            "sessionId": session_id,
            "summary": candidate.get("summary"),
            "recommendedNextAction": candidate.get("recommendedNextAction"),
            "state": candidate.get("state"),
        }
        if command_id and command_id != session_id:
            compact["commandId"] = command_id
        for key in ("ok", "interactive", "awaitingInput", "hasMore", "terminated"):
            if key == "ok":
                if candidate.get(key) is False:
                    compact[key] = False
                continue
            if candidate.get(key) is True:
                compact[key] = candidate.get(key)
        for key in ("profile", "reason", "returnCode", "runId", "linkedProcess", "error"):
            if candidate.get(key) not in (None, "", [], {}):
                compact[key] = candidate.get(key)
        for key in ("initialPreview", "deltaText", "acceptedInputPreview", "keyOutput", "screenAfterInput", "outputPreview", "finalPreview"):
            preview = str(candidate.get(key) or "").strip()
            if not preview:
                continue
            trimmed, truncated = cls._trim_preview_text(preview, limit=1200 if key in {"outputPreview", "finalPreview"} else 800)
            compact[key] = trimmed
            if truncated:
                compact[f"{key}Truncated"] = True
        if isinstance(candidate.get("debug"), dict) and candidate.get("debug"):
            compact["debug"] = candidate.get("debug")
        return {key: val for key, val in compact.items() if val not in (None, "", [], {})}

    @classmethod
    def _compact_web_broker_result(cls, value: Any) -> Any:
        candidate = cls._coerce_json_like_value(value)
        if not isinstance(candidate, dict):
            return value
        compact: dict[str, Any] = {
            "ok": candidate.get("ok"),
            "mode": candidate.get("mode"),
            "summary": candidate.get("summary"),
            "url": candidate.get("url"),
            "finalUrl": candidate.get("finalUrl"),
            "query": candidate.get("query"),
            "provider": candidate.get("provider"),
            "title": candidate.get("title"),
            "resultCount": candidate.get("resultCount"),
            "warnings": candidate.get("warnings"),
            "error": candidate.get("error"),
        }
        if isinstance(candidate.get("results"), list):
            compact["results"] = list(candidate.get("results") or [])[:5]
        for key in ("text", "textPreview"):
            text = str(candidate.get(key) or "").strip()
            if not text:
                continue
            trimmed, truncated = cls._trim_preview_text(text, limit=1400)
            compact[key] = trimmed
            if truncated:
                compact[f"{key}Truncated"] = True
        for key in ("links", "media", "metadata", "extract", "analysisHints", "visionCandidates"):
            payload = candidate.get(key)
            if payload not in (None, "", [], {}):
                compact[key] = payload
        if isinstance(candidate.get("debug"), dict) and candidate.get("debug"):
            compact["debug"] = candidate.get("debug")
        return {key: val for key, val in compact.items() if val not in (None, "", [], {})}

    @classmethod
    def _compact_s3_broker_result(cls, value: Any) -> Any:
        candidate = cls._coerce_json_like_value(value)
        if not isinstance(candidate, dict):
            return value
        compact: dict[str, Any] = {
            "ok": candidate.get("ok"),
            "mode": candidate.get("mode"),
            "summary": candidate.get("summary"),
            "bucket": candidate.get("bucket"),
            "key": candidate.get("key"),
            "url": candidate.get("url"),
            "contentType": candidate.get("contentType"),
            "size": candidate.get("size"),
            "prefix": candidate.get("prefix"),
            "count": candidate.get("count"),
            "destinationPath": candidate.get("destinationPath"),
            "downloaded": candidate.get("downloaded"),
            "error": candidate.get("error"),
            "code": candidate.get("code"),
        }
        if isinstance(candidate.get("objects"), list):
            compact["objects"] = list(candidate.get("objects") or [])[:8]
        return {key: val for key, val in compact.items() if val not in (None, "", [], {})}

    @classmethod
    def _compact_delegation_broker_result(cls, value: Any) -> Any:
        candidate = cls._coerce_json_like_value(value)
        if not isinstance(candidate, dict):
            return value
        compact: dict[str, Any] = {
            "ok": candidate.get("ok"),
            "mode": candidate.get("mode"),
            "summary": candidate.get("summary"),
            "recommendedNextAction": candidate.get("recommendedNextAction"),
            "error": candidate.get("error"),
        }
        if isinstance(candidate.get("items"), list):
            compact_items: list[dict[str, Any]] = []
            for item in list(candidate.get("items") or [])[:8]:
                if not isinstance(item, dict):
                    continue
                compact_items.append(
                    {
                        "delegationId": item.get("delegationId"),
                        "taskBriefId": item.get("taskBriefId"),
                        "taskGoal": item.get("taskGoal"),
                        "lane": item.get("lane"),
                        "targetId": item.get("targetId") or item.get("agentId"),
                        "targetLabel": item.get("targetLabel") or item.get("agentName"),
                        "status": item.get("status"),
                        "workerType": item.get("workerType"),
                        "commandSession": item.get("commandSession"),
                        "resultSchemaMatched": item.get("resultSchemaMatched"),
                        "artifactRefs": item.get("artifactRefs"),
                        "adoptedArtifactRefs": item.get("adoptedArtifactRefs"),
                        "localSelfCheck": item.get("localSelfCheck"),
                        "supervisorAcceptance": item.get("supervisorAcceptance"),
                        "acceptanceHint": item.get("acceptanceHint"),
                        "selectionReason": item.get("selectionReason"),
                        "selectionConfidence": item.get("selectionConfidence"),
                        "matchSignals": item.get("matchSignals"),
                        "compatSource": item.get("compatSource"),
                        "autoDispatchSource": item.get("autoDispatchSource"),
                        "error": item.get("error"),
                    }
                )
            if compact_items:
                compact["items"] = compact_items
        return {key: val for key, val in compact.items() if val not in (None, "", [], {})}

    @classmethod
    def _compact_download_media_for_vision_result(cls, value: Any) -> Any:
        record = cls._coerce_json_like_value(value)
        if not isinstance(record, dict):
            return value
        return {
            "ok": record.get("ok"),
            "artifactId": record.get("artifactId") or record.get("primaryArtifactId"),
            "kind": record.get("kind") or record.get("primaryKind"),
            "mimeType": record.get("mimeType"),
            "fileName": record.get("fileName"),
            "workspacePath": record.get("workspacePath") or record.get("canonicalPath") or record.get("userVisiblePath") or record.get("primaryFile"),
            "workspaceRelativePath": record.get("workspaceRelativePath"),
            "message": record.get("message") or record.get("statusMessage") or record.get("error"),
        }

    @classmethod
    def _collect_urls_from_result(cls, value: Any, urls: list[str], *, depth: int = 0) -> None:
        if depth > 4 or value is None:
            return
        candidate = cls._coerce_json_like_value(value)
        if isinstance(candidate, str):
            for match in re.findall(r"https?://[^\s\"'<>]+", candidate):
                normalized = str(match).strip()
                if normalized and normalized not in urls:
                    urls.append(normalized)
            return
        if isinstance(candidate, list):
            for item in candidate:
                cls._collect_urls_from_result(item, urls, depth=depth + 1)
            return
        if isinstance(candidate, dict):
            for key in ("url", "image_url", "imageUrl", "previewUrl", "externalUrl"):
                nested = candidate.get(key)
                if isinstance(nested, dict):
                    cls._collect_urls_from_result(nested, urls, depth=depth + 1)
                elif isinstance(nested, str):
                    normalized = nested.strip()
                    if normalized.startswith("http") and normalized not in urls:
                        urls.append(normalized)
            for nested in candidate.values():
                cls._collect_urls_from_result(nested, urls, depth=depth + 1)

    @classmethod
    def _compact_generate_image_result(cls, value: Any) -> Any:
        candidate = cls._coerce_json_like_value(value)
        urls: list[str] = []
        cls._collect_urls_from_result(candidate, urls)
        compact: dict[str, Any] = {}
        if isinstance(candidate, dict):
            model = candidate.get("model") or candidate.get("model_name") or candidate.get("providerModel")
            size = candidate.get("size") or candidate.get("image_size") or candidate.get("aspect_ratio")
            ok = candidate.get("ok")
            if ok is not None:
                compact["ok"] = ok
            if model:
                compact["model"] = model
            if size:
                compact["size"] = size
        if urls:
            compact["imageCount"] = len(urls)
            compact["urls"] = urls[:4]
            compact.setdefault("ok", True)
        if compact:
            return compact
        preview = str(candidate or "")
        trimmed, truncated = cls._trim_preview_text(preview, limit=800)
        return {"preview": trimmed, "truncated": truncated} if trimmed else value

    @classmethod
    def _compact_run_system_command_result(cls, value: Any) -> Any:
        candidate = cls._coerce_json_like_value(value)
        if isinstance(candidate, str):
            stripped_candidate = candidate.strip()
            if stripped_candidate.startswith("$ ") or "\n<stdout>" in stripped_candidate or "\n<stderr>" in stripped_candidate:
                return stripped_candidate
        if isinstance(candidate, dict):
            if candidate.get("ok") is False or str(candidate.get("summary") or "").strip():
                kind = str(candidate.get("kind") or "").strip()
                if kind and kind not in {"command_result", "command_session", "command_session_redirect"}:
                    command = str(candidate.get("command") or "").strip()
                    redirect = candidate.get("redirect")
                    if isinstance(redirect, dict):
                        redirect_args = redirect.get("args")
                        if not command and isinstance(redirect_args, dict):
                            command = str(redirect_args.get("command") or "").strip()
                    control_lines = [f"[{kind}]"]
                    for key in ("reason", "summary", "error"):
                        text = str(candidate.get(key) or "").strip()
                        if text and text not in control_lines:
                            control_lines.append(f"[{text}]")
                    suggested = str(candidate.get("suggestedCommand") or "").strip()
                    if suggested:
                        control_lines.append(f"[suggested command: {suggested}]")
                    if isinstance(redirect, dict) and redirect:
                        tool = str(redirect.get("tool") or "").strip()
                        args = redirect.get("args")
                        if tool:
                            control_lines.append(f"[use {tool} to continue]")
                        if isinstance(args, dict):
                            session_cmd = str(args.get("command") or "").strip()
                            if session_cmd and session_cmd != command:
                                control_lines.append(f"[session command: {session_cmd}]")
                    return cls._render_command_terminal_surface(
                        command=command,
                        stderr=str(candidate.get("error") or candidate.get("summary") or "").strip(),
                        exit_code=None,
                        raw_ref=cls._command_surface_raw_ref(candidate),
                        control_lines=control_lines,
                    )
            if str(candidate.get("kind") or "").strip() == "command_result":
                stdout = candidate.get("keyOutput") or candidate.get("stdoutPreview") or ""
                stderr = candidate.get("keyErrors") or candidate.get("stderrPreview") or ""
                return cls._render_command_terminal_surface(
                    command=str(candidate.get("command") or "").strip(),
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=candidate.get("returnCode"),
                    raw_ref=cls._command_surface_raw_ref(candidate),
                    stdout_truncated=bool(candidate.get("keyOutputTruncated") or candidate.get("stdoutTruncated")),
                    stderr_truncated=bool(candidate.get("keyErrorsTruncated") or candidate.get("stderrTruncated")),
                )
            if str(candidate.get("kind") or "").strip() == "command_session_redirect":
                redirect = candidate.get("redirect")
                command = str(candidate.get("command") or "").strip()
                if isinstance(redirect, dict):
                    args = redirect.get("args")
                    if isinstance(args, dict) and not command:
                        command = str(args.get("command") or "").strip()
                control_lines = ["[command requires an observable session]"]
                reason = str(candidate.get("reason") or candidate.get("summary") or "").strip()
                if reason:
                    control_lines.append(f"[{reason}]")
                if isinstance(redirect, dict) and str(redirect.get("tool") or "").strip():
                    control_lines.append(f"[use {redirect.get('tool')} to continue]")
                return cls._render_command_terminal_surface(
                    command=command,
                    raw_ref=cls._command_surface_raw_ref(candidate),
                    control_lines=control_lines,
                )
            if str(candidate.get("kind") or "").strip() == "command_session":
                return cls._render_command_session_terminal_surface(candidate)
            preview = json.dumps(candidate, ensure_ascii=False)
            trimmed, truncated = cls._trim_preview_text(preview, limit=1200)
            raw_ref = cls._command_surface_raw_ref(candidate)
            return cls._render_command_terminal_surface(
                stdout=trimmed,
                raw_ref=raw_ref,
                stdout_truncated=truncated,
            )

        text = str(candidate or "")
        trimmed, truncated = cls._trim_preview_text(text, limit=1200)
        status = "error" if trimmed.lower().startswith("error") else "ok"
        if status == "error":
            return cls._render_command_terminal_surface(stderr=trimmed, stderr_truncated=truncated)
        return cls._render_command_terminal_surface(stdout=trimmed, stdout_truncated=truncated)

    @classmethod
    def _compact_share_workspace_file_result(cls, value: Any) -> Any:
        candidate = cls._coerce_json_like_value(value)
        if not isinstance(candidate, dict):
            return value
        compact: dict[str, Any] = {
            "ok": candidate.get("ok"),
            "filename": candidate.get("filename"),
            "mimeType": candidate.get("mimeType"),
            "mode": candidate.get("mode"),
            "url": candidate.get("url"),
            "previewable": candidate.get("previewable"),
            "downloadable": candidate.get("downloadable"),
            "viewerKind": candidate.get("viewerKind"),
            "workspaceRelativePath": candidate.get("workspaceRelativePath"),
            "workspaceId": candidate.get("workspaceId"),
            "projectId": candidate.get("projectId"),
            "error": candidate.get("error"),
        }
        return {key: val for key, val in compact.items() if val not in (None, "", [], {})}

    @classmethod
    def _compact_tool_result_value(cls, tool_name: str, value: Any) -> Any:
        normalized_tool_name = str(tool_name or "").strip().lower()
        raw_value = getattr(value, "content", value)
        jsonable = cls._coerce_json_like_value(to_jsonable(raw_value))
        if normalized_tool_name == "ask_user":
            answer = str(jsonable or "").strip()
            return answer
        if normalized_tool_name == "download_media_for_vision":
            return cls._compact_download_media_for_vision_result(jsonable)
        if normalized_tool_name == "generate_image":
            return cls._compact_generate_image_result(jsonable)
        if normalized_tool_name == "run_system_command":
            return cls._compact_run_system_command_result(jsonable)
        if normalized_tool_name == "command_session_broker":
            return cls._compact_command_session_broker_result(jsonable)
        if normalized_tool_name == "delegation_broker":
            return cls._compact_delegation_broker_result(jsonable)
        if normalized_tool_name == "web_broker":
            return cls._compact_web_broker_result(jsonable)
        if normalized_tool_name == "s3_broker":
            return cls._compact_s3_broker_result(jsonable)
        if normalized_tool_name == "share_workspace_file":
            return cls._compact_share_workspace_file_result(jsonable)
        if isinstance(jsonable, str):
            preview, truncated = cls._trim_preview_text(jsonable, limit=2400)
            if truncated:
                return {"preview": preview, "chars": len(jsonable), "truncated": True}
        return jsonable

    @classmethod
    def _resolve_tool_call_id_for_start(
        cls,
        *,
        callback_run_id: str,
        raw_inputs: Any,
        metadata: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> str:
        for candidate in (
            cls._extract_tool_call_id_from_value(raw_inputs),
            cls._extract_tool_call_id_from_value(metadata),
            cls._extract_tool_call_id_from_value(data),
            callback_run_id,
        ):
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized
        return ""

    @classmethod
    def _resolve_provider_shadow_for_start(
        cls,
        *,
        raw_inputs: Any,
        metadata: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        provider_tool_call_id = ""
        provider_standard = ""
        for candidate in (raw_inputs, metadata, data):
            if not provider_tool_call_id:
                provider_tool_call_id = cls._extract_provider_tool_call_id_from_value(candidate)
            if not provider_standard:
                provider_standard = cls._extract_provider_standard_from_value(candidate)
            if provider_tool_call_id and provider_standard:
                break
        result: dict[str, str] = {}
        if provider_tool_call_id:
            result["providerToolCallId"] = provider_tool_call_id
        if provider_standard:
            result["providerStandard"] = provider_standard
        return result

    @classmethod
    def _resolve_tool_call_id_for_end(
        cls,
        *,
        callback_run_id: str,
        output: Any,
        metadata: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        stream_state: ChatStreamState,
    ) -> str:
        mapped = str(stream_state.tool_call_id_by_callback_run_id.get(callback_run_id) or "").strip()
        if mapped:
            return mapped
        for candidate in (
            cls._resolve_known_active_tool_call_id(
                getattr(output, "tool_call_id", None),
                stream_state=stream_state,
            ),
            cls._extract_canonical_tool_call_id_from_value(output),
            cls._extract_canonical_tool_call_id_from_value(metadata),
            cls._extract_canonical_tool_call_id_from_value(data),
        ):
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized
        for provider_candidate in (
            cls._extract_provider_tool_call_id_from_value(output),
            cls._extract_provider_tool_call_id_from_value(metadata),
            cls._extract_provider_tool_call_id_from_value(data),
        ):
            normalized_provider = str(provider_candidate or "").strip()
            if not normalized_provider:
                continue
            canonical = str(stream_state.provider_tool_call_id_to_tool_call_id.get(normalized_provider) or "").strip()
            if canonical:
                return canonical
        return ""

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
        owner = self._resolve_event_owner(stream_state)
        stream_key = f"agent:{node_name}"
        topic_prefix = self._runtime_topic_prefix(str(owner.get("ownerRuntimeId") or "chat"))
        agent_start_node = {
            "id": (
                f"{self._ensure_assistant_canonical_message(chat_run, stream_state)}:agent_start:{node_name}:{self._now_timestamp_ms()}"
                if bool(owner.get("displayInMessage"))
                else f"runtime:{chat_run.active_run_id}:agent_start:{node_name}:{self._now_timestamp_ms()}"
            ),
            "kind": "execution",
            "executionType": "agent_start",
            "timestamp": self._now_timestamp_ms(),
            "agentName": profile["name"],
            "agentAvatar": profile["avatar"],
            "agentRoleLabel": profile["roleLabel"],
        }
        agent_event = {
            "type": "agent_start",
            "agent": {
                "id": node_name,
                "name": profile["name"],
                "avatar": profile["avatar"],
                "roleLabel": profile["roleLabel"],
            },
        }
        if bool(owner.get("displayInMessage")):
            agent_event["message_id"] = stream_state.assistant_message_id
        runtime_event = self._emit_owner_scoped_runtime_event(
            chat_run,
            stream_state,
            topic="agent.started" if bool(owner.get("displayInMessage")) else f"{topic_prefix}.agent.started",
            payload=agent_event,
            owner=owner,
            event_kind="agent_start",
            stream_key=stream_key,
            node=agent_start_node,
        )
        payload = runtime_event.get("payload") if isinstance(runtime_event, dict) else None
        if isinstance(payload, dict):
            agent_event["node_id"] = payload.get("node_id") or agent_start_node["id"]
            if payload.get("message_id"):
                agent_event["message_id"] = payload.get("message_id")
            if payload.get("transcript_version"):
                agent_event["transcript_version"] = payload.get("transcript_version")
            agent_event.update(
                {
                    "runtimeId": payload.get("runtimeId"),
                    "ownerRuntimeId": payload.get("ownerRuntimeId"),
                    "ownerAgentKind": payload.get("ownerAgentKind"),
                    "ownerAgentId": payload.get("ownerAgentId"),
                    "ownerStreamKey": payload.get("ownerStreamKey"),
                    "targets": payload.get("targets"),
                    "surfaceTargets": payload.get("surfaceTargets"),
                    "displayInMessage": payload.get("displayInMessage"),
                }
            )
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
                if self._is_ask_user_request(interrupt_request):
                    assistant_message_id = self._ensure_assistant_canonical_message(chat_run, stream_state)
                    tool_call_id = str(interrupt_request.get("toolCallId") or "").strip()
                    if not tool_call_id:
                        tool_call_id = f"{V8_CANONICAL_TOOL_CALL_PREFIX}ask_{uuid.uuid4().hex[:20]}"
                        interrupt_request["toolCallId"] = tool_call_id
                    interaction = chat_run.run_handle.request_ask_user_interaction(
                        request=interrupt_request,
                        assistant_message_id=assistant_message_id,
                    )
                    tool_call_id = str(interaction.get("tool_call_id") or interrupt_request.get("toolCallId") or tool_call_id).strip()
                    stream_state.pending_ask_user_interaction_id = str(interaction.get("id") or "")
                    stream_state.pending_ask_user_tool_call_id = tool_call_id
                    display_args = self._compact_tool_display_args("ask_user", interrupt_request)
                    profile = self._get_agent_profile(stream_state.current_agent)
                    tool_start_event = {
                        "type": "tool_start",
                        "tool": {
                            "toolCallId": tool_call_id,
                            "toolName": "ask_user",
                            "args": display_args,
                        },
                        "timestamp": 0,
                    }
                    tool_call_node = {
                        "id": f"{assistant_message_id}:tool_call:{tool_call_id}",
                        "kind": "execution",
                        "executionType": "tool_call",
                        "toolCallId": tool_call_id,
                        "toolName": "ask_user",
                        "args": display_args,
                        "state": "waiting_input",
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
                    stream_state.tool_calls_buffer.append({"id": tool_call_id, "name": "ask_user", "args": display_args})
                    emitted_events.append(tool_start_event)
                    emitted_events.append(
                        self._build_ask_user_event(
                            chat_run,
                            request_payload=interrupt_request,
                            interaction=interaction,
                        )
                    )
                    chat_run.run_handle.refresh_chat_snapshot()
                    stream_state.interrupted_signal = {
                        "command": "ask_user_requested",
                        "reason": "ask_user",
                        "payload": {
                            "interaction_id": interaction.get("id"),
                            "tool_call_id": tool_call_id,
                        },
                    }
                    return emitted_events
                if self._is_external_tool_request(interrupt_request):
                    tool_call_id = str(interrupt_request.get("toolCallId") or "").strip()
                    requested_tool_name = str(
                        interrupt_request.get("internalAliasName")
                        or interrupt_request.get("toolName")
                        or interrupt_request.get("externalWireName")
                        or ""
                    ).strip()
                    if not tool_call_id:
                        for call in reversed(list(stream_state.tool_calls_buffer or [])):
                            call_name = str((call or {}).get("name") or "").strip()
                            call_id = str((call or {}).get("id") or "").strip()
                            if requested_tool_name and call_name == requested_tool_name and call_id:
                                tool_call_id = call_id
                                break
                            if not requested_tool_name and call_id:
                                tool_call_id = call_id
                                break
                        if tool_call_id:
                            interrupt_request["toolCallId"] = tool_call_id
                    chat_run.run_handle.refresh_chat_snapshot()
                    stream_state.interrupted_signal = {
                        "command": "external_tool_requested",
                        "reason": "external_tool",
                        "payload": {
                            "tool_call_id": tool_call_id or None,
                            "external_wire_name": str(interrupt_request.get("externalWireName") or "").strip() or None,
                            "internal_alias_name": str(interrupt_request.get("internalAliasName") or "").strip() or None,
                        },
                    }
                    return emitted_events
                approval_kind = interrupt_request.get("approvalKind") or "human_input_required"
                approval = chat_run.run_handle.request_approval(
                    approval_kind=approval_kind,
                    request=interrupt_request,
                )
                if str(approval.get("status") or "").strip().lower() != "pending":
                    chat_run.run_handle.refresh_chat_snapshot()
                    return emitted_events
                chat_run.run_handle.refresh_chat_snapshot()
                stream_state.interrupted_signal = {
                    "command": "approval_requested",
                    "reason": approval.get("approval_kind") or approval_kind,
                    "payload": {"approval_id": approval.get("approval_id")},
                }
                return emitted_events

        if kind == "on_chat_model_stream":
            if stream_state.active_tool_call_ids:
                return emitted_events
            provider_delta_at_ms = self._now_timestamp_ms()
            model_events = canonical_model_event_adapter.normalize_chat_model_stream(
                event,
                text_snapshots=stream_state.text_snapshots_by_run,
                reasoning_snapshots=stream_state.reasoning_snapshots_by_run,
                reasoning_surface=stream_state.reasoning_surface_contract,
            )
            for model_event in model_events:
                canonical_event_at_ms = self._now_timestamp_ms()
                model_run_id = model_event.model_run_id
                stream_state.streamed_model_run_ids.add(model_run_id)
                if model_event.event_type == "text_delta":
                    text_delta = model_event.delta
                    stream_state.text_raw_chars += len(text_delta)
                    owner = self._resolve_event_owner(stream_state)
                    display_in_message = bool(owner.get("displayInMessage"))
                    text_delta = stream_state.text_filter.process(text_delta) if display_in_message else text_delta
                    text_delta = self._suppress_neighbor_duplicate_delta(
                        stream_state,
                        delta=text_delta,
                        model_run_id=model_run_id,
                        kind="text",
                    )
                    if not text_delta:
                        continue
                    stream_state.watchdog.note_text_progress()
                    if display_in_message:
                        self._append_message_output_text(
                            stream_state,
                            model_run_id=model_run_id,
                            delta=text_delta,
                        )
                    stream_state.narrative_started_model_run_ids.add(self._normalized_stream_run_id(model_run_id))
                    emitted_events.extend(
                        await self._emit_text_delta(
                            chat_run,
                            stream_state,
                            text_delta,
                            model_run_id=model_run_id,
                            snapshot=None if display_in_message else model_event.snapshot,
                            provider_delta_at_ms=provider_delta_at_ms,
                            canonical_event_at_ms=canonical_event_at_ms,
                        )
                    )
                elif model_event.event_type == "reasoning_suppressed":
                    stream_state.reasoning_suppressed_count += 1
                    if stream_state.reasoning_suppressed_count <= 3:
                        chat_run.emit_runtime_event(
                            "run.reasoning.suppressed",
                            {
                                "count": stream_state.reasoning_suppressed_count,
                                "reason": model_event.diagnostics.get("reason") or "reasoning_surface_not_trusted",
                                "surface": model_event.diagnostics.get("surface") or "hidden",
                                "reasoningKind": model_event.diagnostics.get("reasoningKind") or "hidden",
                                "reasoningSurfaceMode": model_event.diagnostics.get("reasoningSurfaceMode") or "hidden",
                                "reasoningSurfaceTrust": model_event.diagnostics.get("reasoningSurfaceTrust") or "unknown",
                                "looksLikeProgress": bool(model_event.diagnostics.get("looksLikeProgress")),
                                "modelRunId": model_run_id,
                                "preview": str(model_event.delta or "")[:160],
                            },
                            agent_id=stream_state.current_agent,
                            node="canonical_model_event_adapter",
                        )
                elif model_event.event_type == "reasoning_delta":
                    emitted_events.extend(
                        await self._flush_pending_text_aggregator(
                            chat_run,
                            stream_state,
                            ready_only=True,
                        )
                    )
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
                        reasoning_kind=str(model_event.diagnostics.get("reasoningKind") or "provider_reasoning"),
                        reasoning_surface=dict(stream_state.reasoning_surface_contract or {}),
                        provider_delta_at_ms=provider_delta_at_ms,
                        canonical_event_at_ms=canonical_event_at_ms,
                    )
                    if reasoning_event is not None:
                        emitted_events.append(reasoning_event)
            return emitted_events

        if kind == "on_chat_model_end":
            if stream_state.active_tool_call_ids:
                return emitted_events
            provider_delta_at_ms = self._now_timestamp_ms()
            model_run_id = (event.get("run_id") or "").strip()
            model_events = canonical_model_event_adapter.normalize_chat_model_end(
                event,
                text_snapshots=stream_state.text_snapshots_by_run,
                reasoning_snapshots=stream_state.reasoning_snapshots_by_run,
                suppress_reasoning=self._normalized_stream_run_id(model_run_id) in stream_state.narrative_started_model_run_ids,
                emitted_text=self._current_canonical_text(stream_state),
                reasoning_surface=stream_state.reasoning_surface_contract,
            )
            final_snapshot = stream_state.text_snapshots_by_run.get(self._normalized_stream_run_id(model_run_id))
            if final_snapshot:
                stream_state.authoritative_final_text = final_snapshot
            for model_event in model_events:
                canonical_event_at_ms = self._now_timestamp_ms()
                if model_event.event_type == "text_delta":
                    text_delta = model_event.delta
                    stream_state.text_raw_chars += len(text_delta)
                    owner = self._resolve_event_owner(stream_state)
                    display_in_message = bool(owner.get("displayInMessage"))
                    text_delta = stream_state.text_filter.process(text_delta) if display_in_message else text_delta
                    text_delta = self._suppress_neighbor_duplicate_delta(
                        stream_state,
                        delta=text_delta,
                        model_run_id=model_event.model_run_id,
                        kind="text",
                    )
                    if not text_delta:
                        continue
                    stream_state.watchdog.note_text_progress()
                    if display_in_message:
                        if model_event.diagnostics.get("terminalTextCorrection"):
                            stream_state.text_aggregator.flush()
                            self._append_message_output_text(
                                stream_state,
                                model_run_id=model_event.model_run_id,
                                replacement_snapshot=model_event.snapshot,
                            )
                        else:
                            self._append_message_output_text(
                                stream_state,
                                model_run_id=model_event.model_run_id,
                                delta=text_delta,
                            )
                    stream_state.narrative_started_model_run_ids.add(self._normalized_stream_run_id(model_event.model_run_id))
                    emitted_events.extend(
                        await self._emit_text_delta(
                            chat_run,
                            stream_state,
                            text_delta,
                            model_run_id=model_event.model_run_id,
                            snapshot=None if display_in_message else model_event.snapshot,
                            provider_delta_at_ms=provider_delta_at_ms,
                            canonical_event_at_ms=canonical_event_at_ms,
                        )
                    )
                elif model_event.event_type == "reasoning_suppressed":
                    stream_state.reasoning_suppressed_count += 1
                    if stream_state.reasoning_suppressed_count <= 3:
                        chat_run.emit_runtime_event(
                            "run.reasoning.suppressed",
                            {
                                "count": stream_state.reasoning_suppressed_count,
                                "reason": model_event.diagnostics.get("reason") or "reasoning_surface_not_trusted",
                                "surface": model_event.diagnostics.get("surface") or "hidden",
                                "reasoningKind": model_event.diagnostics.get("reasoningKind") or "hidden",
                                "reasoningSurfaceMode": model_event.diagnostics.get("reasoningSurfaceMode") or "hidden",
                                "reasoningSurfaceTrust": model_event.diagnostics.get("reasoningSurfaceTrust") or "unknown",
                                "looksLikeProgress": bool(model_event.diagnostics.get("looksLikeProgress")),
                                "modelRunId": model_event.model_run_id,
                                "preview": str(model_event.delta or "")[:160],
                            },
                            agent_id=stream_state.current_agent,
                            node="canonical_model_event_adapter",
                        )
                elif model_event.event_type == "reasoning_delta":
                    emitted_events.extend(
                        await self._flush_pending_text_aggregator(
                            chat_run,
                            stream_state,
                            ready_only=True,
                        )
                    )
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
                        reasoning_kind=str(model_event.diagnostics.get("reasoningKind") or "provider_reasoning"),
                        reasoning_surface=dict(stream_state.reasoning_surface_contract or {}),
                        provider_delta_at_ms=provider_delta_at_ms,
                        canonical_event_at_ms=canonical_event_at_ms,
                    )
                    if reasoning_event is not None:
                        emitted_events.append(reasoning_event)
            return emitted_events

        if kind == "on_tool_start":
            emitted_events.extend(
                await self._flush_pending_text_aggregator(
                    chat_run,
                    stream_state,
                    ready_only=True,
                )
            )
            raw_inputs = data.get("input", {})
            inputs = self._compact_tool_display_args(name, raw_inputs)
            if str(name or "").strip().lower() == "delegation_broker":
                stream_state.delegation_dispatch_seen = True
            callback_run_id = str(event.get("run_id") or "").strip()
            tool_call_id = self._resolve_tool_call_id_for_start(
                callback_run_id=callback_run_id,
                raw_inputs=raw_inputs,
                metadata=metadata,
                data=data,
            )
            provider_shadow = self._resolve_provider_shadow_for_start(
                raw_inputs=raw_inputs,
                metadata=metadata,
                data=data,
            )
            if str(name or "").strip() == "ask_user":
                # ask_user 是 LangGraph interrupt 驱动的控制流工具；真正的等待点
                # 只能由后续 on_chain_stream 里的 __interrupt__ 创建，不能在 tool_start
                # 阶段伪造成普通审批，否则 resume 会重新进入同一工具并循环卡住。
                return emitted_events
            if callback_run_id and tool_call_id:
                stream_state.tool_call_id_by_callback_run_id[callback_run_id] = tool_call_id
            provider_tool_call_id = str(provider_shadow.get("providerToolCallId") or "").strip()
            if provider_tool_call_id and tool_call_id:
                stream_state.provider_tool_call_id_to_tool_call_id[provider_tool_call_id] = tool_call_id
            if tool_call_id and provider_shadow:
                stream_state.tool_call_shadow_by_tool_call_id[tool_call_id] = dict(provider_shadow)
            owner = self._resolve_event_owner(stream_state, tool_name=str(name or ""))
            self._maybe_emit_supervisor_direct_scope_diagnostic(
                chat_run,
                stream_state,
                tool_name=str(name or ""),
                owner=owner,
            )
            if self._enforce_supervisor_direct_scope_gate(
                chat_run,
                stream_state,
                tool_name=str(name or ""),
                owner=owner,
            ):
                return emitted_events
            active_tool_key = str(tool_call_id or name or "__unknown_tool__").strip()
            stream_state.active_tool_call_ids.add(active_tool_key)
            tool_start_event = {
                "type": "tool_start",
                "tool": {
                    "toolCallId": tool_call_id,
                    "toolName": name,
                    "args": inputs,
                    **provider_shadow,
                },
                "timestamp": 0,
            }
            profile = self._get_agent_profile(stream_state.current_agent)
            owner_runtime_id = str(owner.get("ownerRuntimeId") or "chat")
            stream_key = f"{owner_runtime_id}:{stream_state.current_agent}:tool:{tool_call_id or name}"
            topic = "tool.started" if bool(owner.get("displayInMessage")) else f"{self._runtime_topic_prefix(owner_runtime_id)}.tool.started"
            tool_call_node = {
                "id": (
                    f"{self._ensure_assistant_canonical_message(chat_run, stream_state)}:tool_call:{tool_call_id or name}"
                    if bool(owner.get("displayInMessage"))
                    else f"runtime:{chat_run.active_run_id}:tool_call:{tool_call_id or name}"
                ),
                "kind": "execution",
                "executionType": "tool_call",
                "toolCallId": tool_call_id,
                "toolName": name,
                "args": inputs,
                "timestamp": self._now_timestamp_ms(),
                "agentName": profile["name"],
                "agentAvatar": profile["avatar"],
                "agentRoleLabel": profile["roleLabel"],
                **provider_shadow,
            }
            runtime_event = self._emit_owner_scoped_runtime_event(
                chat_run,
                stream_state,
                topic=topic,
                payload=tool_start_event,
                owner=owner,
                event_kind="tool_start",
                stream_key=stream_key,
                node=tool_call_node,
            )
            payload = runtime_event.get("payload") if isinstance(runtime_event, dict) else None
            if isinstance(payload, dict):
                tool_start_event["message_id"] = payload.get("message_id")
                tool_start_event["node_id"] = payload.get("node_id")
                tool_start_event["transcript_version"] = payload.get("transcript_version")
                tool_start_event.update(
                    {
                        "runtimeId": payload.get("runtimeId"),
                        "ownerRuntimeId": payload.get("ownerRuntimeId"),
                        "ownerAgentKind": payload.get("ownerAgentKind"),
                        "ownerAgentId": payload.get("ownerAgentId"),
                        "ownerStreamKey": payload.get("ownerStreamKey"),
                        "targets": payload.get("targets"),
                        "surfaceTargets": payload.get("surfaceTargets"),
                        "displayInMessage": payload.get("displayInMessage"),
                    }
                )
            stream_state.watchdog.note_tool_start(tool_call_id)
            stream_state.tool_calls_buffer.append(
                {
                    "id": tool_call_id,
                    "name": name,
                    "args": inputs,
                    **provider_shadow,
                }
            )
            emitted_events.append(tool_start_event)
            return emitted_events

        if kind == "on_tool_end":
            output = data.get("output", "")
            output_str = str(output.content) if hasattr(output, "content") else str(output)
            callback_run_id = str(event.get("run_id") or "").strip()
            candidate_tool_call_id = self._resolve_tool_call_id_for_end(
                callback_run_id=callback_run_id,
                output=output,
                metadata=metadata,
                data=data,
                stream_state=stream_state,
            )
            tool_call_id = candidate_tool_call_id
            if str(name or "").strip() == "ask_user":
                interaction = self._resolve_ask_user_tool_result_context(
                    chat_run,
                    stream_state,
                    candidate_tool_call_id=str(
                        stream_state.pending_ask_user_tool_call_id
                        or candidate_tool_call_id
                        or ""
                    ).strip(),
                    output_text=output_str,
                )
                resolved_tool_call_id = str((interaction or {}).get("tool_call_id") or "").strip()
                if not interaction or not resolved_tool_call_id:
                    chat_run.emit_runtime_event(
                        "ask_user.tool_result.unmatched",
                        {
                            "candidateToolCallId": candidate_tool_call_id,
                            "resultPreview": output_str[:200],
                        },
                        agent_id=stream_state.current_agent,
                        node=stream_state.current_agent or "chat_runtime",
                    )
                    return emitted_events
                tool_call_id = resolved_tool_call_id
                stream_state.pending_ask_user_interaction_id = ""
                stream_state.pending_ask_user_tool_call_id = ""
            else:
                known_active_tool_call = bool(tool_call_id) and (
                    tool_call_id in stream_state.active_tool_call_ids
                    or any(str((call or {}).get("id") or "").strip() == tool_call_id for call in list(stream_state.tool_calls_buffer or []))
                )
                if not known_active_tool_call:
                    chat_run.emit_runtime_event(
                        "tool_result.unmatched",
                        {
                            "toolName": name,
                            "candidateToolCallId": candidate_tool_call_id,
                            "callbackRunId": callback_run_id,
                            "resultPreview": output_str[:200],
                        },
                        agent_id=stream_state.current_agent,
                        node=stream_state.current_agent or "chat_runtime",
                    )
                    return emitted_events
            compact_result = self._compact_tool_result_value(str(name or ""), output)
            active_tool_key = str(tool_call_id or name or "__unknown_tool__").strip()
            provider_shadow = dict(stream_state.tool_call_shadow_by_tool_call_id.get(tool_call_id) or {})
            tool_result_event = {
                "type": "tool_result",
                "tool": {
                    "toolCallId": tool_call_id,
                    "toolName": name,
                    "result": compact_result,
                    **provider_shadow,
                },
                "timestamp": 0,
            }
            stream_state.watchdog.note_tool_end(tool_call_id)
            profile = self._get_agent_profile(stream_state.current_agent)
            owner = self._resolve_event_owner(stream_state, tool_name=str(name or ""))
            owner_runtime_id = str(owner.get("ownerRuntimeId") or "chat")
            stream_key = f"{owner_runtime_id}:{stream_state.current_agent}:tool:{tool_call_id or name}"
            topic = "tool.finished" if bool(owner.get("displayInMessage")) else f"{self._runtime_topic_prefix(owner_runtime_id)}.tool.finished"
            tool_result_node = {
                "id": (
                    f"{self._ensure_assistant_canonical_message(chat_run, stream_state)}:tool_result:{tool_call_id or name}"
                    if bool(owner.get("displayInMessage"))
                    else f"runtime:{chat_run.active_run_id}:tool_result:{tool_call_id or name}"
                ),
                "kind": "execution",
                "executionType": "tool_result",
                "toolCallId": tool_call_id,
                "toolName": name,
                "result": compact_result,
                "timestamp": self._now_timestamp_ms(),
                "agentName": profile["name"],
                "agentAvatar": profile["avatar"],
                "agentRoleLabel": profile["roleLabel"],
                **provider_shadow,
            }
            runtime_event = self._emit_owner_scoped_runtime_event(
                chat_run,
                stream_state,
                topic=topic,
                payload=tool_result_event,
                owner=owner,
                event_kind="tool_result",
                stream_key=stream_key,
                node=tool_result_node,
            )
            payload = runtime_event.get("payload") if isinstance(runtime_event, dict) else None
            if isinstance(payload, dict):
                tool_result_event["message_id"] = payload.get("message_id")
                tool_result_event["node_id"] = payload.get("node_id")
                tool_result_event["transcript_version"] = payload.get("transcript_version")
                tool_result_event.update(
                    {
                        "runtimeId": payload.get("runtimeId"),
                        "ownerRuntimeId": payload.get("ownerRuntimeId"),
                        "ownerAgentKind": payload.get("ownerAgentKind"),
                        "ownerAgentId": payload.get("ownerAgentId"),
                        "ownerStreamKey": payload.get("ownerStreamKey"),
                        "targets": payload.get("targets"),
                        "surfaceTargets": payload.get("surfaceTargets"),
                        "displayInMessage": payload.get("displayInMessage"),
                    }
                )
            stream_state.active_tool_call_ids.discard(active_tool_key)
            if callback_run_id:
                stream_state.tool_call_id_by_callback_run_id.pop(callback_run_id, None)
            if not active_tool_key:
                stream_state.active_tool_call_ids.clear()
            emitted_events.append(tool_result_event)
            return emitted_events

        return emitted_events

    async def flush_stream_state(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> list[dict[str, Any]]:
        emitted_events: list[dict[str, Any]] = []
        self._clear_text_flush_deadline(stream_state)
        owner = self._resolve_event_owner(stream_state)
        display_in_message = bool(owner.get("displayInMessage"))
        final_filtered_text = stream_state.text_filter.flush() if display_in_message else ""
        if final_filtered_text:
            if display_in_message:
                stream_state.output_buffer.append(final_filtered_text)
            emitted_events.extend(
                await self._emit_text_delta(
                    chat_run,
                    stream_state,
                    final_filtered_text,
                    model_run_id=stream_state.last_text_delta_run_id,
                    snapshot=None,
                )
            )

        emitted_events.extend(await self._flush_pending_text_aggregator(chat_run, stream_state, final=True))
        self._emit_delegation_claim_diagnostic(chat_run, stream_state)
        self._emit_text_stream_diagnostics(chat_run, stream_state)
        return emitted_events

    def persist_final_assistant_message(self, chat_run: ChatRunContext, stream_state: ChatStreamState) -> None:
        assistant_message_id = stream_state.assistant_message_id
        if not assistant_message_id:
            return
        self._ensure_workspace_media_artifacts_for_message(chat_run, stream_state, assistant_message_id)
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
        return False

    _WORKSPACE_MEDIA_PATH_PATTERN = re.compile(
        r"(?P<path>[A-Za-z]:[\\/][^\r\n`\"']+?\.(?:png|jpe?g|gif|webp|bmp|mp4|mov|webm|mkv|mp3|wav|m4a|aac|flac))",
        re.IGNORECASE,
    )

    @staticmethod
    def _media_kind_for_mime(mime_type: str, path: str) -> str:
        lowered_mime = str(mime_type or "").lower()
        lowered_path = str(path or "").lower()
        if lowered_mime.startswith("image/") or lowered_path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
            return "image"
        if lowered_mime.startswith("video/") or lowered_path.endswith((".mp4", ".mov", ".webm", ".mkv")):
            return "video"
        if lowered_mime.startswith("audio/") or lowered_path.endswith((".mp3", ".wav", ".m4a", ".aac", ".flac")):
            return "audio"
        return "file"

    def _workspace_media_artifact_nodes_from_text(
        self,
        *,
        text: str,
        message_id: str,
        profile: dict[str, str],
        request: ChatRequest | None = None,
    ) -> list[dict[str, Any]]:
        raw_text = str(text or "")
        if not raw_text:
            return []
        try:
            descriptor = workspace_resolution_service.resolve_workspace_descriptor(
                runtime_kind="chat",
                session_id=(request.conversation_id or request.session_id) if request else None,
                explicit_workspace_id=request.workspace_id if request else None,
                explicit_project_id=request.project_id if request else None,
                explicit_workspace_path=request.workspace_path if request else None,
            )
            workspace_root = Path(
                str(descriptor.get("workspaceRoot") or workspace_resolution_service.get_main_workspace_path())
            ).expanduser().resolve()
        except Exception:
            return []

        nodes: list[dict[str, Any]] = []
        seen_relative_paths: set[str] = set()
        for match in self._WORKSPACE_MEDIA_PATH_PATTERN.finditer(raw_text):
            raw_path = unquote(str(match.group("path") or "").strip().rstrip("，。；;、)）]】"))
            if not raw_path:
                continue
            try:
                resolved_path = Path(raw_path).expanduser().resolve()
                relative_path = resolved_path.relative_to(workspace_root)
            except Exception:
                continue
            if not resolved_path.is_file():
                continue
            workspace_path = relative_path.as_posix()
            if not workspace_path or workspace_path in seen_relative_paths:
                continue
            seen_relative_paths.add(workspace_path)
            mime_type = mimetypes.guess_type(str(resolved_path))[0] or "application/octet-stream"
            media_kind = self._media_kind_for_mime(mime_type, workspace_path)
            artifact_id = f"workspace:{uuid.uuid5(uuid.NAMESPACE_URL, workspace_path).hex}"
            artifact = {
                "id": artifact_id,
                "artifactId": artifact_id,
                "kind": media_kind,
                "title": resolved_path.name,
                "displayLabel": resolved_path.name,
                "sourcePath": str(resolved_path),
                "workspacePath": workspace_path,
                "mimeType": mime_type,
                "resourceRef": build_workspace_resource_ref(
                    workspace_relative_path=workspace_path,
                    path_plane="workspace_artifact",
                    workspace_root=workspace_root,
                    workspace_id=str(descriptor.get("workspaceId") or "").strip() or None,
                    project_id=str(descriptor.get("projectId") or "").strip() or None,
                    mime_type=mime_type,
                    display_label=resolved_path.name,
                    previewable=media_kind in {"image", "video", "audio"},
                    downloadable=True,
                    surface_visible=True,
                ),
                "metadata": {
                    "source": "assistant_narrative_workspace_path",
                    "workspaceRoot": str(workspace_root),
                    "workspaceId": str(descriptor.get("workspaceId") or "").strip() or None,
                    "projectId": str(descriptor.get("projectId") or "").strip() or None,
                    "workspaceRelativePath": workspace_path,
                    "pathPlane": "workspace_artifact",
                },
            }
            nodes.append(
                {
                    "id": f"{message_id}:artifact:{artifact_id}",
                    "kind": "artifact",
                    "artifact": artifact,
                    "timestamp": self._now_timestamp_ms(),
                    "agentName": profile["name"],
                    "agentAvatar": profile["avatar"],
                    "agentRoleLabel": profile["roleLabel"],
                }
            )
        return nodes

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
        if self._owner_kind_for_agent(stream_state.current_agent) != "supervisor":
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
            "ownerRuntimeId": "chat",
            "ownerAgentKind": "supervisor",
            "ownerAgentId": "supervisor",
            "ownerStreamKey": "chat:supervisor:final",
            "displayInMessage": True,
        }
        derived_artifact_nodes = self._workspace_media_artifact_nodes_from_text(
            text=final_text,
            message_id=message_id,
            profile=profile,
            request=chat_run.request,
        )

        def _replace_narrative(nodes: list[dict[str, Any]], _metadata: dict[str, Any]):
            preserved = [
                dict(node)
                for node in nodes
                if not (
                    str(node.get("kind") or "").strip() == "narrative"
                    and str(node.get("role") or "").strip() == "assistant"
                )
            ]
            existing_node_ids = {str(node.get("id") or "").strip() for node in preserved}
            append_artifact_nodes = [
                node
                for node in derived_artifact_nodes
                if str(node.get("id") or "").strip() not in existing_node_ids
            ]
            return [*preserved, final_node, *append_artifact_nodes], final_node["id"]

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
            "chat.planner_mode.decided",
            {
                "plannerMode": chat_run.prepared.planner_mode,
                "enabled": True,
                "usedTodos": used_todos,
                "reason": reason,
                "summary": summary,
                "message": message,
                "todoToolCount": len(todo_tool_calls),
                "intentDiagnostics": dict(chat_run.prepared.planner_intent_diagnostics or {}),
                "runId": chat_run.active_run_id,
            },
            agent_id=None,
            node="planner_lane",
        )
        chat_run.emit_runtime_event(
            "chat.task_planning_mode.decided",
            {
                "enabled": True,
                "plannerMode": chat_run.prepared.planner_mode,
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

    async def emit_planner_lane_projection(
        self,
        chat_run: ChatRunContext,
        execution_bundle: ChatExecutionBundle | None,
    ) -> None:
        if execution_bundle is None:
            return
        try:
            state = await supervisor_runner.get_state_snapshot(execution_bundle.runner_bundle)
        except Exception:
            logging.getLogger("v8chat.chat_runtime").exception(
                "Failed to inspect final graph state for planner projection in run '%s'",
                chat_run.active_run_id,
            )
            return
        plan = chat_run.prepared.planner_plan if isinstance(chat_run.prepared.planner_plan, dict) else None
        if not plan and isinstance((state or {}).get("planner_plan"), dict):
            plan = dict((state or {}).get("planner_plan") or {})
        if not plan:
            return
        task_briefs = [dict(item) for item in list(plan.get("taskBriefs") or []) if isinstance(item, dict)]
        selected_delegations: list[dict[str, Any]] = []
        for item in [dict(row) for row in list((state or {}).get("parallel_results") or []) if isinstance(row, dict)]:
            selected_delegations.append(
                {
                    "delegationId": item.get("delegationId"),
                    "taskBriefId": item.get("taskBriefId"),
                    "lane": item.get("lane"),
                    "targetId": item.get("targetId"),
                    "targetLabel": item.get("targetLabel") or item.get("agentName"),
                    "status": item.get("status"),
                    "workerType": item.get("workerType"),
                    "commandSession": item.get("commandSession"),
                }
            )
        chat_run.emit_runtime_event(
            "planner.plan.projected",
            {
                "planId": plan.get("planId"),
                "executionStrategy": plan.get("executionStrategy"),
                "planSummary": plan.get("planSummary"),
                "taskCount": len(task_briefs),
                "taskBriefs": task_briefs,
                "dependencies": [
                    {
                        "taskBriefId": row.get("taskBriefId"),
                        "dependency": list(row.get("dependency") or []),
                        "parallelGroup": row.get("parallelGroup"),
                    }
                    for row in list(plan.get("taskGraph") or [])
                    if isinstance(row, dict)
                ],
                "globalAcceptanceContract": plan.get("globalAcceptanceContract"),
                "riskFlags": list(plan.get("riskFlags") or []),
                "qualityFlags": list(plan.get("qualityFlags") or []),
                "repairCount": int(plan.get("repairCount") or 0),
                "autoDispatchDecision": dict(plan.get("autoDispatchDecision") or {}),
                "dispatchEligibilityReason": plan.get("dispatchEligibilityReason"),
                "selectedDelegations": selected_delegations,
                "traceRef": {"runId": chat_run.active_run_id, "planId": plan.get("planId")},
            },
            agent_id=None,
            node="planner_lane",
        )

    async def emit_engineering_lane_projection(
        self,
        chat_run: ChatRunContext,
        execution_bundle: ChatExecutionBundle | None,
    ) -> None:
        if execution_bundle is None:
            return
        if not chat_run.prepared.engineering_trigger_decision.get("active"):
            return
        try:
            state = await supervisor_runner.get_state_snapshot(execution_bundle.runner_bundle)
        except Exception:
            logging.getLogger("v8chat.chat_runtime").exception(
                "Failed to inspect final graph state for engineering projection in run '%s'",
                chat_run.active_run_id,
            )
            return
        plan = chat_run.prepared.planner_plan if isinstance(chat_run.prepared.planner_plan, dict) else None
        if not plan and isinstance((state or {}).get("planner_plan"), dict):
            plan = dict((state or {}).get("planner_plan") or {})
        engineering_pack = chat_run.prepared.engineering_context_pack if isinstance(chat_run.prepared.engineering_context_pack, dict) else {}
        context_pack = engineering_pack.get("contextPack") if isinstance(engineering_pack.get("contextPack"), dict) else engineering_pack
        coding_contract = {}
        if isinstance(plan, dict) and isinstance(plan.get("codingPlannerContract"), dict):
            coding_contract = dict(plan.get("codingPlannerContract") or {})
        elif isinstance(context_pack.get("codingPlannerContractPreview"), dict):
            coding_contract = dict(context_pack.get("codingPlannerContractPreview") or {})
        if not coding_contract and not plan:
            return

        results = [dict(item) for item in list((state or {}).get("parallel_results") or []) if isinstance(item, dict)]
        selected_delegations: list[dict[str, Any]] = []
        blocked_count = 0
        warning_count = 0
        for item in results:
            decision = engineering_lane_service._normalize_workset_dispatch_decision(  # type: ignore[attr-defined]
                item.get("worksetDispatchDecision") if isinstance(item.get("worksetDispatchDecision"), dict) else {}
            )
            blocked_count += 1 if bool(decision.get("blocked")) else 0
            warning_count += 1 if bool(decision.get("warning")) else 0
            selected_delegations.append(
                {
                    "delegationId": item.get("delegationId"),
                    "taskBriefId": item.get("taskBriefId"),
                    "taskGoal": item.get("taskGoal"),
                    "targetId": item.get("targetId"),
                    "targetLabel": item.get("targetLabel") or item.get("agentName"),
                    "status": item.get("status"),
                    "worksetDispatchDecision": decision,
                }
            )

        task_briefs = [dict(item) for item in list((plan or {}).get("taskBriefs") or []) if isinstance(item, dict)]
        task_capsules: list[dict[str, Any]] = []
        for item in task_briefs[:12]:
            capsule = item.get("engineeringTaskCapsule") if isinstance(item.get("engineeringTaskCapsule"), dict) else {}
            task_capsules.append(
                {
                    "taskBriefId": item.get("taskBriefId"),
                    "taskGoal": item.get("goal"),
                    "criticalFiles": list(capsule.get("criticalFiles") or item.get("criticalFiles") or [])[:12],
                    "readSet": list(capsule.get("readSet") or item.get("readSet") or [])[:12],
                    "writeSet": list(capsule.get("writeSet") or item.get("writeSet") or [])[:12],
                    "riskFlags": list(capsule.get("riskFlags") or [])[:8],
                    "proofExpectations": list(capsule.get("proofExpectations") or item.get("proofExpectations") or [])[:8],
                    "verificationContract": list(capsule.get("verificationContract") or [])[:8],
                }
            )

        ownership_plan = list(coding_contract.get("ownershipPlan") or []) if isinstance(coding_contract, dict) else []
        critical_files = list(coding_contract.get("criticalFiles") or [])[:12] if isinstance(coding_contract, dict) else []
        verification_matrix = [
            {
                "kind": row.get("kind"),
                "command": row.get("command"),
                "requiredForVerified": row.get("requiredForVerified"),
            }
            for row in list(coding_contract.get("verificationMatrix") or [])
            if isinstance(row, dict)
        ][:8]
        risk_flags = [str(item).strip() for item in list(coding_contract.get("riskFlags") or (plan or {}).get("riskFlags") or []) if str(item).strip()]
        proof_expectations = [str(item).strip() for item in list(coding_contract.get("proofExpectations") or []) if str(item).strip()][:8]
        summary = str((plan or {}).get("planSummary") or "").strip() or "工程契约已投影"
        summary = f"{summary} · {len(task_capsules)} 个工程任务"
        if blocked_count > 0:
            summary += f" · {blocked_count} 个 blocked"
        elif warning_count > 0:
            summary += f" · {warning_count} 个 warning"

        chat_run.emit_runtime_event(
            "engineering.plan.projected",
            {
                "planId": (plan or {}).get("planId"),
                "summary": summary,
                "engineeringMode": chat_run.prepared.engineering_mode,
                "taskCount": len(task_capsules),
                "ownershipCount": len(ownership_plan),
                "blockedCount": blocked_count,
                "warningCount": warning_count,
                "criticalFiles": critical_files,
                "readSet": list(coding_contract.get("readSet") or [])[:12] if isinstance(coding_contract, dict) else [],
                "writeSet": list(coding_contract.get("writeSet") or [])[:12] if isinstance(coding_contract, dict) else [],
                "ownershipPlan": ownership_plan[:8],
                "verificationMatrix": verification_matrix,
                "proofExpectations": proof_expectations,
                "riskFlags": risk_flags[:8],
                "mergeOrder": list(coding_contract.get("mergeOrder") or [])[:6] if isinstance(coding_contract, dict) else [],
                "selectedDelegations": selected_delegations[:12],
                "taskCapsules": task_capsules,
                "triggerDecision": dict(chat_run.prepared.engineering_trigger_decision or {}),
                "traceRef": {"runId": chat_run.active_run_id, "planId": (plan or {}).get("planId")},
            },
            agent_id=None,
            node="engineering_lane",
        )

    async def emit_subagent_swarm_projection(
        self,
        chat_run: ChatRunContext,
        execution_bundle: ChatExecutionBundle | None,
    ) -> None:
        if execution_bundle is None:
            return
        try:
            state = await supervisor_runner.get_state_snapshot(execution_bundle.runner_bundle)
        except Exception:
            logging.getLogger("v8chat.chat_runtime").exception(
                "Failed to inspect final graph state for subagent swarm projection in run '%s'",
                chat_run.active_run_id,
            )
            return
        results = [dict(item) for item in list((state or {}).get("parallel_results") or []) if isinstance(item, dict)]
        if not results:
            return
        seen: set[tuple[str, str, str]] = set()
        for item in results:
            invocation_id = str(item.get("invocationId") or "").strip()
            agent_id = str(item.get("agentId") or item.get("targetId") or "").strip()
            task_brief_id = str(item.get("taskBriefId") or f"{invocation_id}:{item.get('branchIndex') or 0}").strip()
            key = (invocation_id, agent_id, task_brief_id)
            if key in seen:
                continue
            seen.add(key)
            status = str(item.get("status") or "unknown").strip().lower()
            if status in {"ok", "completed", "success", "terminated"}:
                topic = "subagent.task.completed"
            elif status in {"queued", "running", "starting", "waiting_input", "attached", "streaming", "observing"}:
                topic = "subagent.task.updated"
            else:
                topic = "subagent.task.failed"
            chat_run.emit_runtime_event(
                topic,
                {
                    "invocationId": invocation_id or None,
                    "taskBriefId": task_brief_id,
                    "taskGoal": item.get("taskGoal"),
                    "subagentId": agent_id,
                    "subagentName": item.get("agentName") or item.get("targetLabel") or agent_id,
                    "status": status,
                    "lane": item.get("lane") or "subagent",
                    "workerType": item.get("workerType"),
                    "commandSession": item.get("commandSession"),
                    "resultSchemaMatched": item.get("resultSchemaMatched"),
                    "localSelfCheck": item.get("localSelfCheck"),
                    "supervisorAcceptance": item.get("supervisorAcceptance") or {
                        "status": "pending",
                        "summary": "Supervisor has not accepted, retried, or ignored this subtask result yet.",
                    },
                    "compactTranscript": item.get("compactTranscript") or item.get("error") or "",
                    "traceRef": {
                        "runId": chat_run.active_run_id,
                        "invocationId": invocation_id,
                        "branchIndex": item.get("branchIndex"),
                        "commandId": (item.get("commandSession") or {}).get("commandId") if isinstance(item.get("commandSession"), dict) else None,
                    },
                    "artifactRefs": list(item.get("artifactRefs") or []),
                    "adoptedArtifactRefs": list(item.get("adoptedArtifactRefs") or []),
                    "acceptanceHint": item.get("acceptanceHint") or "Supervisor must explicitly accept, retry, or ignore this subtask result.",
                    "autoDispatchSource": item.get("autoDispatchSource"),
                    "messageCount": item.get("messageCount"),
                    "todoDeltaCount": item.get("todoDeltaCount"),
                    "toolMode": item.get("toolMode"),
                    "targetId": item.get("targetId"),
                    "targetLabel": item.get("targetLabel"),
                },
                agent_id=agent_id or None,
                node="subagent_swarm",
            )

    def finalize_interrupted_run(
        self,
        chat_run: ChatRunContext,
        interrupted_signal: dict[str, Any],
        stream_state: ChatStreamState | None = None,
    ) -> list[dict[str, Any]]:
        if interrupted_signal.get("command") == "ask_user_requested":
            return [{"type": "done", "status": "waiting_input", "run_id": chat_run.active_run_id}]
        if interrupted_signal.get("command") == "approval_requested":
            status = "waiting_input" if str(interrupted_signal.get("reason") or "").strip().lower() == "ask_user" else "waiting_approval"
            return [
                {
                    "type": "done",
                    "status": status,
                    "run_id": chat_run.active_run_id,
                    "payload": dict(interrupted_signal.get("payload") or {}),
                }
            ]
        if interrupted_signal.get("command") == "external_tool_requested":
            if stream_state is not None:
                stream_state.active_tool_call_ids.clear()
                self.persist_final_assistant_message(chat_run, stream_state)
            chat_run.run_handle.transition(
                "waiting_external_tool",
                reason="external_tool_requested",
                node="run_manager",
            )
            return [
                {
                    "type": "done",
                    "status": "waiting_external_tool",
                    "run_id": chat_run.active_run_id,
                    "payload": dict(interrupted_signal.get("payload") or {}),
                }
            ]
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
            if self._is_ask_user_request(request_payload):
                assistant_message_id = None
                try:
                    stream_state = ChatStreamState()
                    assistant_message_id = self._ensure_assistant_canonical_message(chat_run, stream_state)
                except Exception:
                    assistant_message_id = None
                interaction = chat_run.run_handle.request_ask_user_interaction(
                    request=request_payload,
                    assistant_message_id=assistant_message_id,
                )
                chat_run.run_handle.refresh_chat_snapshot()
                return [
                    self._build_ask_user_event(
                        chat_run,
                        request_payload=request_payload,
                        interaction=interaction,
                        governance={
                            "message": str(exc),
                            "details": exc.details,
                        },
                    ),
                    {"type": "done", "status": "waiting_input", "run_id": chat_run.active_run_id},
                ]
            approval = chat_run.run_handle.request_approval(
                approval_kind=exc.approval_kind,
                request=request_payload,
            )
            chat_run.run_handle.refresh_chat_snapshot()
            if str(approval.get("status") or "").strip().lower() == "pending":
                return [{"type": "done", "status": "waiting_approval", "run_id": chat_run.active_run_id}]
        normalized = normalize_provider_error(exc)
        if isinstance(exc, CompatBridgeHardStop):
            failure_class = getattr(exc, "failure_class", CompatBridgeHardStop.failure_class)
            normalized["failureClass"] = failure_class
            normalized["code"] = failure_class
        if isinstance(exc, GraphStreamIdleTimeoutError):
            normalized["failureClass"] = "stream_idle_timeout"
            normalized["code"] = "stream_idle_timeout"
            normalized["recoverable"] = True
            normalized["userAction"] = (
                "模型流长时间没有新事件。可以重试本轮，或先检查 provider streaming / 后台命令状态。"
            )
        if isinstance(exc, GraphRecursionContinuationBudgetExceeded):
            normalized["message"] = str(exc)
            normalized["failureClass"] = "graph_recursion_continuation_budget"
            normalized["code"] = "graph_recursion_continuation_budget"
            normalized["recoverable"] = True
            normalized["continuationCount"] = exc.continuation_count
            normalized["continuationLimit"] = exc.continuation_limit
            normalized["recursionLimit"] = exc.recursion_limit
            normalized["lastTool"] = exc.last_tool
            normalized["lastTodo"] = exc.last_todo
            normalized["userAction"] = "继续本轮、拆分任务，或要求 Supervisor 改走 Engineering/delegation。"
        if chat_run and normalized.get("code") == "context_window_overflow":
            try:
                context_config = storage.get_context_config() or {}
                guard = context_window_guard.resolve(
                    target_role="supervisor",
                    runtime_kind="chat",
                    model_ref=str(chat_run.request.config.model_name or "").strip(),
                    compression=dict(context_config.get("compression") or {}),
                )
                chat_run.emit_runtime_event(
                    "context.prepared",
                    {
                        "context_policy_version": context_config.get("schema_version", 1),
                        "runtime_kind": "chat",
                        "target_role": "supervisor",
                        "resolved_model_id": str(chat_run.request.config.model_name or "").strip(),
                        "context_governance_reason": "context_window_overflow",
                        "trigger_reason": "context_window_overflow",
                        "context_window_tokens": guard.get("effectiveContextWindowTokens"),
                        "effective_context_window_tokens": guard.get("effectiveContextWindowTokens"),
                        "summary_input_budget_tokens": guard.get("summaryInputBudgetTokens"),
                        "context_window_participants": guard.get("participants") or [],
                        "context_window_warnings": guard.get("warnings") or [],
                        "original_message_count": len(chat_run.lc_messages or []),
                        "estimated_input_tokens": 0,
                        "compaction_applied": False,
                        "compaction_method": "none",
                        "compaction_mode": "overflow_absorbed",
                        "durable_flush": {"ok": False, "skipped": True, "reason": "provider_context_window_overflow"},
                        "block_types": [],
                        "block_count": 0,
                        "estimated_saved_tokens": 0,
                        "provider_error": {
                            "code": normalized.get("code"),
                            "provider": normalized.get("provider"),
                            "model": normalized.get("model"),
                            "message": normalized.get("message"),
                            "userAction": normalized.get("userAction"),
                        },
                    },
                    node="context_window_guard",
                )
            except Exception:
                logging.getLogger("v8chat.chat_runtime").exception(
                    "Failed to emit context governance overflow event for run '%s'",
                    chat_run.active_run_id,
                )
        if chat_run:
            try:
                preserve_background_commands = bool(normalized.get("recoverable")) and str(normalized.get("failureClass") or "") in {
                    "graph_recursion_continuation_budget",
                    "stream_idle_timeout",
                }
                if not preserve_background_commands:
                    from core.system_tools.native import _terminate_run_background_commands

                    _terminate_run_background_commands(chat_run.active_run_id, interactive_only=True)
            except Exception:
                logging.getLogger("v8chat.chat_runtime").exception(
                    "Failed to clean up interactive background commands for failed run '%s'",
                    chat_run.active_run_id,
                )
            try:
                chat_run.run_handle.fail(normalized["message"], node="run_manager")
                if isinstance(exc, CompatBridgeHardStop):
                    failure_class = getattr(exc, "failure_class", CompatBridgeHardStop.failure_class)
                    run_service.update_metadata(
                        chat_run.active_run_id,
                        {"failureClass": failure_class},
                    )
                elif isinstance(exc, GraphStreamIdleTimeoutError):
                    run_service.update_metadata(
                        chat_run.active_run_id,
                        {"failureClass": "stream_idle_timeout", "recoverable": True},
                    )
                elif isinstance(exc, GraphRecursionContinuationBudgetExceeded):
                    run_service.update_metadata(
                        chat_run.active_run_id,
                        {
                            "failureClass": "graph_recursion_continuation_budget",
                            "recoverable": True,
                            "continuationCount": exc.continuation_count,
                            "continuationLimit": exc.continuation_limit,
                            "recursionLimit": exc.recursion_limit,
                        },
                    )
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
        context = {
            "runtime_kind": "chat",
            "trigger_source": chat_run.transport,
            "session_id": chat_run.session_id,
            "run_id": chat_run.active_run_id,
            "user_id": chat_run.user_id,
            "project_id": chat_run.scope_result.binding.project_id,
            "workspace_id": chat_run.scope_result.binding.workspace_id,
            "workspace_path": chat_run.scope_result.binding.workspace_path,
            "resolved_scope": chat_run.scope_result.binding.resolved_scope,
            "goal": chat_run.prepared.latest_user_content,
        }
        context["workspace_binding"] = build_workspace_binding(context, runtime_kind="chat").as_dict()
        return context

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
                for final_event in self.finalize_interrupted_run(chat_run, interrupted_signal, stream_state):
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

        stream_state = self.create_stream_state(transport=chat_run.transport, chat_run=chat_run)

        try:
            for startup_event in self.emit_stream_start_events(chat_run, stream_state):
                yield startup_event

            preflight_events = self.handle_preflight_gate(chat_run)
            if preflight_events:
                for preflight_event in preflight_events:
                    yield preflight_event
                return

            await self.ensure_planner_plan(chat_run=chat_run)

            continuation_count = 0
            continuation_reason = ""
            continuation_bundle: ChatExecutionBundle | None = None
            last_execution_bundle: ChatExecutionBundle | None = None
            max_continuations = self._max_graph_continuations()
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
                    guidance_signal: dict[str, Any] | None = None
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
                                        if control_signal and control_signal.get("command") == "guidance":
                                            guidance_signal = control_signal
                                            break
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
                    if guidance_signal:
                        for flushed_event in await self._flush_pending_text_aggregator(
                            chat_run,
                            stream_state,
                            from_timer=False,
                            final=True,
                        ):
                            yield flushed_event
                        queue_id = str((guidance_signal.get("payload") or {}).get("queueMessageId") or "").strip()
                        queue_item = db.get_chat_user_message_queue_item(queue_id) if queue_id else None
                        if not queue_item:
                            chat_run.emit_runtime_event(
                                "human_guidance.missed",
                                {
                                    "queueMessageId": queue_id or None,
                                    "failureClass": "queued_message_missing",
                                    "summary": "运行中引导信号已收到，但队列项不存在或已被处理。",
                                },
                                agent_id=None,
                                node="human_guidance_queue",
                            )
                            continuation_bundle = None
                            break
                        self._emit_human_guidance_injected(chat_run, stream_state, queue_item)
                        guidance_bundle = await self.create_guidance_bundle(
                            chat_run=chat_run,
                            previous_bundle=execution_bundle,
                            queue_item=queue_item,
                        )
                        if guidance_bundle is None:
                            chat_run.emit_runtime_event(
                                "human_guidance.failed",
                                {
                                    "queueMessageId": queue_id,
                                    "failureClass": "guidance_continuation_unavailable",
                                    "summary": "运行中引导已记录，但无法创建续跑执行包。",
                                },
                                agent_id=None,
                                node="human_guidance_queue",
                            )
                            break
                        continuation_bundle = guidance_bundle
                        continue
                    break
                except GraphRecursionError:
                    if continuation_count >= max_continuations:
                        last_todo = None
                        diagnostics = dict(getattr(last_execution_bundle.runner_bundle, "diagnostics", {}) or {}) if last_execution_bundle else {}
                        if isinstance(diagnostics.get("lastTodo"), dict):
                            last_todo = str(diagnostics["lastTodo"].get("text") or "").strip() or None
                        raise GraphRecursionContinuationBudgetExceeded(
                            continuation_count=continuation_count,
                            continuation_limit=max_continuations,
                            recursion_limit=self._recursion_limit(),
                            last_tool=stream_state.watchdog.last_observed_event,
                            last_todo=last_todo,
                        ) from None
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
                    active_command_sessions: list[dict[str, Any]] = []
                    try:
                        from core.native_tools import list_background_process_snapshots

                        active_command_sessions = [
                            {
                                "commandId": item.get("commandId"),
                                "status": item.get("status"),
                                "cwd": item.get("cwd"),
                                "awaitingInput": item.get("awaitingInput"),
                            }
                            for item in list_background_process_snapshots(run_id=chat_run.active_run_id)
                            if item.get("status") in {"running", "failed", "stopped", "completed"}
                        ][:6]
                    except Exception:
                        active_command_sessions = []
                    continuation_diagnostics = dict(continuation_bundle.runner_bundle.diagnostics or {})
                    chat_run.emit_runtime_event(
                        "run.continuation.scheduled",
                        {
                            "continuationCount": continuation_count,
                            "continuationLimit": max_continuations,
                            "continuationReason": continuation_reason,
                            "reason": continuation_reason,
                            "recursionLimit": self._recursion_limit(),
                            "lastTool": stream_state.watchdog.last_observed_event,
                            "lastTodo": continuation_diagnostics.get("lastTodo"),
                            "activeCommandSessions": active_command_sessions,
                            "summary": f"长任务达到单段 graph 步数上限，正在自动续跑第 {continuation_count}/{max_continuations} 段。",
                            "recommendedNextAction": "继续观察；若多次续跑仍接近预算，应拆分任务或派发 Engineering/delegation。",
                        },
                        agent_id=None,
                        node="continuation_manager",
                    )
                    continue

            if interrupted_signal:
                for final_event in self.finalize_interrupted_run(chat_run, interrupted_signal, stream_state):
                    yield final_event
                return

            for flushed_event in await self.flush_stream_state(chat_run, stream_state):
                yield flushed_event
            await self.reconcile_final_assistant_message(chat_run, stream_state, last_execution_bundle)
            await self.emit_planner_lane_projection(chat_run, last_execution_bundle)
            await self.emit_engineering_lane_projection(chat_run, last_execution_bundle)
            await self.emit_subagent_swarm_projection(chat_run, last_execution_bundle)
            self.persist_final_assistant_message(chat_run, stream_state)
            self.emit_task_planning_mode_decision(chat_run, stream_state)
            yield self.finalize_success_run(chat_run)
        except Exception as exc:
            self._emit_delegation_claim_diagnostic(chat_run, stream_state)
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
