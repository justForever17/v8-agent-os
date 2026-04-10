from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from core.database import db
from erc.runtime_context import get_runtime_context


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _safe_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _extract_usage_from_mapping(payload: Mapping[str, Any]) -> Dict[str, int]:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0

    for key in ("prompt_tokens", "input_tokens", "inputTokenCount", "prompt_token_count"):
        input_tokens = max(input_tokens, _safe_int(payload.get(key)))
    for key in ("completion_tokens", "output_tokens", "candidates_token_count", "outputTokenCount"):
        output_tokens = max(output_tokens, _safe_int(payload.get(key)))
    for key in ("total_tokens", "totalTokenCount", "total_token_count"):
        total_tokens = max(total_tokens, _safe_int(payload.get(key)))

    if not total_tokens:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def extract_token_usage(response: Any) -> Dict[str, int]:
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, Mapping):
        for key in ("token_usage", "usage", "usage_metadata"):
            payload = llm_output.get(key)
            if isinstance(payload, Mapping):
                usage = _extract_usage_from_mapping(payload)
                if usage["total_tokens"]:
                    return usage

    generations = getattr(response, "generations", None) or []
    for generation_group in generations:
        if not isinstance(generation_group, list):
            continue
        for generation in generation_group:
            message = getattr(generation, "message", None)
            for candidate in (
                getattr(message, "usage_metadata", None),
                getattr(message, "response_metadata", None),
                getattr(generation, "generation_info", None),
            ):
                if isinstance(candidate, Mapping):
                    usage = _extract_usage_from_mapping(candidate)
                    if usage["total_tokens"]:
                        return usage
                    nested = candidate.get("usage_metadata") or candidate.get("token_usage") or candidate.get("usage")
                    if isinstance(nested, Mapping):
                        usage = _extract_usage_from_mapping(nested)
                        if usage["total_tokens"]:
                            return usage

    return usage


def extract_runtime_diagnostics(response: Any) -> Dict[str, Any]:
    diagnostics: Dict[str, Any] = {}
    generations = getattr(response, "generations", None) or []
    for generation_group in generations:
        if not isinstance(generation_group, list):
            continue
        for generation in generation_group:
            message = getattr(generation, "message", None)
            response_metadata = dict(getattr(message, "response_metadata", {}) or {})
            if not response_metadata:
                continue
            if response_metadata.get("v8_provider_adapter"):
                diagnostics["providerAdapter"] = response_metadata.get("v8_provider_adapter")
            if response_metadata.get("v8_provider_adapter_label"):
                diagnostics["providerAdapterLabel"] = response_metadata.get("v8_provider_adapter_label")
            if response_metadata.get("v8_effective_capability_matrix"):
                diagnostics["effectiveCapabilityMatrix"] = response_metadata.get("v8_effective_capability_matrix")
            if response_metadata.get("v8_tool_calling_mode"):
                diagnostics["toolCallingMode"] = response_metadata.get("v8_tool_calling_mode")
            if response_metadata.get("v8_structured_output_mode"):
                diagnostics["structuredOutputMode"] = response_metadata.get("v8_structured_output_mode")
            if response_metadata.get("v8_stream_mode"):
                diagnostics["streamMode"] = response_metadata.get("v8_stream_mode")
            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                diagnostics["toolCallCount"] = len(tool_calls)
            return diagnostics
    return diagnostics


def _estimate_cost(tokens: int, unit_price: Any) -> float:
    price = _safe_float(unit_price)
    if price <= 0 or tokens <= 0:
        return 0.0
    # 当前 models.json 沿用 Admin 里的 costPerInput / costPerOutput 约定，按每百万 token 估算。
    return (tokens / 1_000_000.0) * price


def _resolve_scope(ctx: Dict[str, Any]) -> tuple[str, str]:
    if ctx.get("project_id"):
        return "project", str(ctx["project_id"])
    if ctx.get("session_id"):
        return "session", str(ctx["session_id"])
    if ctx.get("user_id"):
        return "user", str(ctx["user_id"])
    return "global", "global"


@dataclass
class _InvocationStart:
    started_at: float
    started_at_iso: str
    context: Dict[str, Any]
    message_batches: int


class ModelTelemetryCallback(BaseCallbackHandler):
    raise_error = False
    run_inline = True

    def __init__(
        self,
        *,
        model_id: str,
        provider_id: str,
        provider_name: str,
        role: str = "",
        capability_class: str = "",
        request_kind: str = "chat",
        cost_per_input: Any = None,
        cost_per_output: Any = None,
        is_streaming: bool = False,
        provider_adapter: str = "",
        effective_capability_matrix: Optional[Dict[str, Any]] = None,
        tool_calling_mode: str = "",
        structured_output_mode: str = "",
        stream_mode: str = "",
    ):
        self.model_id = model_id
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.role = role
        self.capability_class = capability_class
        self.request_kind = request_kind
        self.cost_per_input = cost_per_input
        self.cost_per_output = cost_per_output
        self.is_streaming = is_streaming
        self.provider_adapter = provider_adapter
        self.effective_capability_matrix = dict(effective_capability_matrix or {})
        self.tool_calling_mode = tool_calling_mode
        self.structured_output_mode = structured_output_mode
        self.stream_mode = stream_mode
        self._starts: Dict[str, _InvocationStart] = {}

    @property
    def ignore_chat_model(self) -> bool:
        return False

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        **kwargs: Any,
    ) -> None:
        self._starts[str(run_id)] = _InvocationStart(
            started_at=time.perf_counter(),
            started_at_iso=_utc_now(),
            context=get_runtime_context(),
            message_batches=sum(len(batch) for batch in messages or []),
        )

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        **kwargs: Any,
    ) -> None:
        start = self._starts.pop(str(run_id), None)
        ctx = start.context if start else get_runtime_context()
        usage = extract_token_usage(response)
        runtime_diagnostics = extract_runtime_diagnostics(response)
        latency_ms = (time.perf_counter() - start.started_at) * 1000 if start else 0.0
        cost_input = _estimate_cost(usage["input_tokens"], self.cost_per_input)
        cost_output = _estimate_cost(usage["output_tokens"], self.cost_per_output)
        cost_total = cost_input + cost_output
        self._record_invocation(
            ctx=ctx,
            status="completed",
            started_at=(start.started_at_iso if start else _utc_now()),
            latency_ms=latency_ms,
            usage=usage,
            cost_input=cost_input,
            cost_output=cost_output,
            cost_total=cost_total,
            error_code=None,
            error_message=None,
            metadata={
                "message_batches": start.message_batches if start else 0,
                "llm_output_keys": sorted((getattr(response, "llm_output", {}) or {}).keys()),
                "providerAdapter": runtime_diagnostics.get("providerAdapter") or self.provider_adapter,
                "providerAdapterLabel": runtime_diagnostics.get("providerAdapterLabel") or self.provider_adapter,
                "effectiveCapabilityMatrix": runtime_diagnostics.get("effectiveCapabilityMatrix") or self.effective_capability_matrix,
                "toolCallingMode": runtime_diagnostics.get("toolCallingMode") or self.tool_calling_mode,
                "structuredOutputMode": runtime_diagnostics.get("structuredOutputMode") or self.structured_output_mode,
                "streamMode": runtime_diagnostics.get("streamMode") or self.stream_mode,
                "toolCallCount": runtime_diagnostics.get("toolCallCount", 0),
            },
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        **kwargs: Any,
    ) -> None:
        start = self._starts.pop(str(run_id), None)
        ctx = start.context if start else get_runtime_context()
        latency_ms = (time.perf_counter() - start.started_at) * 1000 if start else 0.0
        self._record_invocation(
            ctx=ctx,
            status="failed",
            started_at=(start.started_at_iso if start else _utc_now()),
            latency_ms=latency_ms,
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            cost_input=0.0,
            cost_output=0.0,
            cost_total=0.0,
            error_code=str(getattr(error, "code", "") or ""),
            error_message=str(error),
            metadata={
                "exception_type": error.__class__.__name__,
                "message_batches": start.message_batches if start else 0,
                "providerAdapter": self.provider_adapter,
                "effectiveCapabilityMatrix": self.effective_capability_matrix,
                "toolCallingMode": self.tool_calling_mode,
                "structuredOutputMode": self.structured_output_mode,
                "streamMode": self.stream_mode,
            },
        )

    def _record_invocation(
        self,
        *,
        ctx: Dict[str, Any],
        status: str,
        started_at: str,
        latency_ms: float,
        usage: Dict[str, int],
        cost_input: float,
        cost_output: float,
        cost_total: float,
        error_code: Optional[str],
        error_message: Optional[str],
        metadata: Dict[str, Any],
    ) -> None:
        invocation_id = str(uuid.uuid4())
        finished_at = _utc_now()
        scope_type, scope_id = _resolve_scope(ctx)
        log_record = {
            "id": invocation_id,
            "run_id": ctx.get("run_id"),
            "session_id": ctx.get("session_id"),
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "model_id": self.model_id,
            "role": self.role,
            "capability_class": self.capability_class,
            "request_kind": self.request_kind,
            "status": status,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "cost_input": cost_input,
            "cost_output": cost_output,
            "cost_total": cost_total,
            "latency_ms": latency_ms,
            "error_code": error_code,
            "error_message": error_message,
            "is_streaming": self.is_streaming,
            "metadata": metadata,
            "started_at": started_at,
            "finished_at": finished_at,
        }
        db.add_model_invocation_log(log_record)
        db.upsert_usage_ledger(
            {
                "id": str(uuid.uuid4()),
                "bucket_date": finished_at[:10],
                "scope_type": scope_type,
                "scope_id": scope_id,
                "provider_id": self.provider_id,
                "model_id": self.model_id,
                "role": self.role or "unassigned",
                "capability_class": self.capability_class,
                "invocations": 1,
                "success_count": 1 if status == "completed" else 0,
                "error_count": 0 if status == "completed" else 1,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "cost_total": cost_total,
                "latency_ms_total": latency_ms,
            }
        )
        db.add_provider_health_log(
            {
                "id": str(uuid.uuid4()),
                "provider_id": self.provider_id or "unknown",
                "provider_name": self.provider_name or self.provider_id or "unknown",
                "model_id": self.model_id,
                "run_id": ctx.get("run_id"),
                "session_id": ctx.get("session_id"),
                "status": "healthy" if status == "completed" else "failed",
                "error_code": error_code,
                "error_message": error_message,
                "latency_ms": latency_ms,
                "detail": {
                    "role": self.role,
                    "capabilityClass": self.capability_class,
                    "requestKind": self.request_kind,
                },
            }
        )


class ModelTelemetryService:
    def build_chat_callback(
        self,
        *,
        model_id: str,
        provider_id: str,
        provider_name: str,
        role: str = "",
        capability_class: str = "",
        cost_per_input: Any = None,
        cost_per_output: Any = None,
        is_streaming: bool = False,
        provider_adapter: str = "",
        effective_capability_matrix: Optional[Dict[str, Any]] = None,
        tool_calling_mode: str = "",
        structured_output_mode: str = "",
        stream_mode: str = "",
    ) -> ModelTelemetryCallback:
        return ModelTelemetryCallback(
            model_id=model_id,
            provider_id=provider_id,
            provider_name=provider_name,
            role=role,
            capability_class=capability_class,
            request_kind="chat",
            cost_per_input=cost_per_input,
            cost_per_output=cost_per_output,
            is_streaming=is_streaming,
            provider_adapter=provider_adapter,
            effective_capability_matrix=effective_capability_matrix,
            tool_calling_mode=tool_calling_mode,
            structured_output_mode=structured_output_mode,
            stream_mode=stream_mode,
        )

    def record_aux_model_invocation(
        self,
        *,
        model_id: str,
        provider_id: str,
        provider_name: str,
        role: str,
        capability_class: str,
        request_kind: str,
        latency_ms: float,
        status: str = "completed",
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        ctx = get_runtime_context()
        scope_type, scope_id = _resolve_scope(ctx)
        finished_at = _utc_now()
        invocation_id = str(uuid.uuid4())
        record = {
            "id": invocation_id,
            "run_id": ctx.get("run_id"),
            "session_id": ctx.get("session_id"),
            "provider_id": provider_id,
            "provider_name": provider_name,
            "model_id": model_id,
            "role": role,
            "capability_class": capability_class,
            "request_kind": request_kind,
            "status": status,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_input": 0.0,
            "cost_output": 0.0,
            "cost_total": 0.0,
            "latency_ms": latency_ms,
            "error_code": error_code,
            "error_message": error_message,
            "is_streaming": False,
            "metadata": metadata or {},
            "started_at": finished_at,
            "finished_at": finished_at,
        }
        db.add_model_invocation_log(record)
        db.upsert_usage_ledger(
            {
                "id": str(uuid.uuid4()),
                "bucket_date": finished_at[:10],
                "scope_type": scope_type,
                "scope_id": scope_id,
                "provider_id": provider_id,
                "model_id": model_id,
                "role": role or "unassigned",
                "capability_class": capability_class,
                "invocations": 1,
                "success_count": 1 if status == "completed" else 0,
                "error_count": 0 if status == "completed" else 1,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_total": 0.0,
                "latency_ms_total": latency_ms,
            }
        )
        db.add_provider_health_log(
            {
                "id": str(uuid.uuid4()),
                "provider_id": provider_id or "unknown",
                "provider_name": provider_name or provider_id or "unknown",
                "model_id": model_id,
                "run_id": ctx.get("run_id"),
                "session_id": ctx.get("session_id"),
                "status": "healthy" if status == "completed" else "failed",
                "error_code": error_code,
                "error_message": error_message,
                "latency_ms": latency_ms,
                "detail": metadata or {},
            }
        )

    def build_dashboard_overview(self, days: int = 7) -> Dict[str, Any]:
        counts = db.get_counts_snapshot()
        daily = db.get_daily_telemetry_activity(days=days)
        usage_distribution = db.get_model_usage_distribution(days=days, limit=10)
        provider_health = db.get_provider_health_summary(days=days)
        recent_invocations = db.get_recent_model_invocations(limit=12)

        total_tokens = sum(int(item.get("total_tokens") or 0) for item in usage_distribution)
        estimated_cost = sum(float(item.get("cost_total") or 0.0) for item in usage_distribution)

        charts = {
            "dailyActivity": [
                {
                    "date": row["day"][5:].replace("-", "/"),
                    "messages": int(row.get("messages") or 0),
                    "runs": int(row.get("runs") or 0),
                    "invocations": int(row.get("invocations") or 0),
                    "tokens": int(row.get("total_tokens") or 0),
                }
                for row in daily
            ],
            "modelUsage": [
                {
                    "name": item.get("model_id") or "unknown",
                    "provider": item.get("provider_name") or item.get("provider_id") or "unknown",
                    "value": int(item.get("invocations") or 0),
                    "tokens": int(item.get("total_tokens") or 0),
                    "cost": float(item.get("cost_total") or 0.0),
                }
                for item in usage_distribution
            ],
            "providerHealth": [
                {
                    "providerId": item.get("provider_id") or "unknown",
                    "providerName": item.get("provider_name") or item.get("provider_id") or "unknown",
                    "events": int(item.get("events") or 0),
                    "successCount": int(item.get("success_count") or 0),
                    "errorCount": int(item.get("error_count") or 0),
                    "avgLatencyMs": round(float(item.get("avg_latency_ms") or 0.0), 2),
                    "lastSeenAt": item.get("last_seen_at"),
                }
                for item in provider_health
            ],
        }

        return {
            "stats": {
                "totalSessions": counts.get("sessions", 0),
                "totalMessages": counts.get("messages", 0),
                "totalRuns": counts.get("runs", 0),
                "totalInvocations": counts.get("invocations", 0),
                "pendingApprovals": counts.get("pending_approvals", 0),
                "activeRuns": counts.get("active_runs", 0),
                "recentWindowTokens": total_tokens,
                "recentWindowEstimatedCost": round(estimated_cost, 6),
            },
            "charts": charts,
            "recentInvocations": recent_invocations,
        }


model_telemetry_service = ModelTelemetryService()
