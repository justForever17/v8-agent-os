"""Opt-in, scoped provider-contract capture for the synthetic cross-graph live task.

No headers, credentials, whole messages or arbitrary argument values are saved.
Parsed tool_calls are explicitly a SDK view, which may repair incomplete JSON.
SDK input deltas and unparsed arguments retain only structure/length/hash facts;
historical calls are never represented as original network responses.
"""
from __future__ import annotations

import argparse
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import threading
from typing import Any
import uuid


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _without_url_queries(value: Any) -> Any:
    if isinstance(value, dict):
        return {_without_url_queries(str(key)): _without_url_queries(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_without_url_queries(child) for child in value]
    if isinstance(value, str):
        return re.sub(r"(https?://[^\s\"'<>?]+)\?[^\s\"'<>]+", r"\1?[query omitted]", value)
    return value


def _shape(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(name): _shape(child, key=str(name)) for name, child in value.items()}
    if isinstance(value, list):
        return [_shape(child, key=key) for child in value]
    kind = "null" if value is None else type(value).__name__
    if key in {"targetAgentName", "executionLaneHint", "mode"} and isinstance(value, str):
        return {"type": kind, "value": re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[query omitted]", value)}
    if isinstance(value, str):
        return {"type": kind, "length": len(value), "sha256": _hash(value)}
    return {"type": kind}


def summarize_calls(calls: list[Any]) -> list[dict[str, Any]]:
    summaries = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else call
        if function.get("name") != "delegation_broker":
            continue
        arguments = function.get("arguments") if "arguments" in function else function.get("args")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                summaries.append({"name": "delegation_broker", "arguments": {"type": "invalid_json", "length": len(arguments), "sha256": _hash(arguments)}})
                continue
        summaries.append({"name": "delegation_broker", "arguments": _shape(arguments)})
    return summaries


def matching_payload(payload: dict[str, Any], marker: str) -> bool:
    return any(marker in json.dumps(message.get("content"), ensure_ascii=False)
               for message in payload.get("messages", []) if isinstance(message, dict))


def argument_facts(value: Any) -> dict[str, Any]:
    facts = {"type": type(value).__name__}
    if not isinstance(value, str):
        facts["completeObject"] = isinstance(value, dict)
        return facts
    facts.update({"chars": len(value), "utf8Bytes": len(value.encode("utf-8")), "sha256": _hash(value)})
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        facts.update({"completeObject": False, "errorCategory": "JSONDecodeError", "jsonErrorOffset": error.pos})
    else:
        facts.update({"completeObject": isinstance(parsed, dict), "jsonType": type(parsed).__name__})
        if isinstance(parsed, dict):
            # Dynamic JSON keys may themselves contain private data.
            facts["fieldCount"] = len(parsed)
            facts["knownFields"] = sorted(set(parsed) & {"mode", "tasks", "taskBriefId", "targetAgentName", "goal",
                                                          "expectedOutputs", "acceptanceContract", "executionLaneHint"})
    return facts


def raw_call_facts(calls: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for position, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else call
        name = function.get("name")
        rows.append({"position": position, "index": call.get("index") if isinstance(call.get("index"), int) else None,
                     "name": name if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", name) else None,
                     "arguments": argument_facts(function.get("arguments", function.get("args"))),
                     "errorPresent": bool(call.get("error"))})
    return rows


def numeric_usage(value: Any) -> dict[str, Any]:
    allowed = {"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens",
               "input_token_details", "output_token_details", "prompt_tokens_details", "completion_tokens_details",
               "reasoning", "reasoning_tokens", "cache_read", "cache_creation", "cached_tokens", "audio", "audio_tokens",
               "accepted_prediction_tokens", "rejected_prediction_tokens"}
    if not isinstance(value, dict):
        return {}
    return {key: numeric_usage(child) if isinstance(child, dict) else child
            for key, child in value.items() if key in allowed
            and (isinstance(child, dict) or type(child) in (int, float))}


def finish_fact(value: Any) -> str | None:
    return value if value in ("stop", "tool_calls", "function_call", "length", "content_filter", "max_tokens",
                              "max_output_tokens", "incomplete", "end_turn", "tool_use", "stop_sequence") else None


def cap_facts(payload: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for container_name, container in (("topLevel", payload), ("extra_body", payload.get("extra_body"))):
        if not isinstance(container, dict):
            continue
        for key in ("max_tokens", "max_completion_tokens", "max_output_tokens", "max_tokens_to_sample"):
            value = container.get(key)
            result[f"{container_name}.{key}"] = {"present": key in container,
                                               "value": value if value is None or type(value) is int else None}
    return result


def policy_facts(budget: dict[str, Any]) -> dict[str, Any]:
    return {"mode": budget.get("mode") if budget.get("mode") in {"auto", "fixed"} else None,
            "maxTokens": budget.get("maxTokens") if type(budget.get("maxTokens")) is int else None,
            "source": budget.get("source") if budget.get("source") in {"user_or_legacy_fixed", "request_budget",
                       "provider_default", "protocol_required_verified_capacity", "required_parameter_default"} else None}


class ScopedCapture:
    def __init__(self, marker: str, output: Path):
        if "cross-graph-live" not in marker or len(marker) < 16:
            raise ValueError("Use the exact synthetic cross-graph-live workspace marker")
        self.marker, self.output = marker, output
        self.lock = threading.Lock()
        self.current: ContextVar[dict | None] = ContextVar("cross_graph_capture", default=None)
        self.invocation: ContextVar[dict | None] = ContextVar("cross_graph_adapter_capture", default=None)

    def write(self, item: dict[str, Any]) -> None:
        encoded = json.dumps(_without_url_queries(item), ensure_ascii=False)
        with self.lock:
            with self.output.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")

    def request(self, payload: dict[str, Any]) -> None:
        if not matching_payload(payload, self.marker):
            if self.current.get() is not None:
                self.current.get()["matched"] = False
            return
        context = self.current.get()
        if context is None:
            context = {"captureId": uuid.uuid4().hex}
            self.current.set(context)
        context["matched"] = True
        invocation = self.invocation.get()
        if invocation is not None:
            invocation["captureId"] = context["captureId"]
        schemas = [dict(tool["function"]) for tool in payload.get("tools", [])
                   if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
                   and tool["function"].get("name") == "delegation_broker"]
        schemas = [{key: schema[key] for key in ("name", "description", "parameters") if key in schema} for schema in schemas]
        messages = [message for message in payload.get("messages", []) if isinstance(message, dict)]
        system_text = "\n".join(str(message.get("content") or "") for message in messages if message.get("role") == "system")
        delegation_call_ids = {call.get("id") for message in messages for call in message.get("tool_calls", [])
                               if isinstance(call, dict) and (call.get("function") or {}).get("name") == "delegation_broker"}
        replies = [str(message.get("content") or "") for message in messages
                   if message.get("role") == "tool" and message.get("tool_call_id") in delegation_call_ids]
        self.write({"boundary": "openai_final_request_payload", "captureId": context["captureId"], "tools": schemas,
                    "schemaSha256": _hash(json.dumps(schemas, sort_keys=True, ensure_ascii=False)),
                    "toolChoice": payload.get("tool_choice"),
                    "outputCaps": cap_facts(payload),
                    "outputTokenPolicy": (invocation or {}).get("outputTokenPolicy"),
                    "streamUsageRequested": (payload.get("stream_options") or {}).get("include_usage") is True,
                    "availableToolNames": [tool["function"].get("name") for tool in payload.get("tools", [])
                                           if isinstance(tool, dict) and isinstance(tool.get("function"), dict)],
                    "promptFacts": {"registeredAgentIndexPresent": "[registeredAgentIndex]" in system_text,
                                    "requiredDelegationGuidancePresent": "[Required Delegation Dispatch]" in system_text},
                    "delegationReplyFacts": [{"length": len(reply), "sha256": _hash(reply),
                                              "targetAgentNamePresent": "targetAgentName" in reply,
                                              "expectedOutputsPresent": "expectedOutputs" in reply,
                                              "acceptanceContractPresent": "acceptanceContract" in reply,
                                              "agentDiscoveryPresent": "agent_broker" in reply} for reply in replies],
                    "serializedHistoryToolCalls": [item for message in payload.get("messages", [])
                        if isinstance(message, dict) and message.get("role") == "assistant"
                        for item in summarize_calls(message.get("tool_calls") or [])]})

    def response(self, message: Any, generation_info: dict | None = None, *, stream_end: str = "completed", error: BaseException | None = None) -> None:
        context = self.current.get()
        if not context or not context.get("matched"):
            return
        metadata = {**dict(getattr(message, "response_metadata", None) or {}), **dict(generation_info or {})}
        extra = dict(getattr(message, "additional_kwargs", None) or {})
        self.write({"boundary": "openai_sdk_assembled_response", "captureId": context["captureId"],
                    "toolCallsView": "sdk_parsed_may_repair_incomplete_json_not_wire_arguments",
                    "toolCalls": summarize_calls(list(getattr(message, "tool_calls", None) or [])),
                    "argumentViews": {
                        "tool_call_chunks": raw_call_facts(list(getattr(message, "tool_call_chunks", None) or [])),
                        "additional_kwargs.tool_calls": raw_call_facts(list(extra.get("tool_calls") or [])),
                        "invalid_tool_calls": raw_call_facts(list(getattr(message, "invalid_tool_calls", None) or [])),
                    },
                    "finishReason": finish_fact(metadata.get("finish_reason") or metadata.get("stop_reason")),
                    "usage": numeric_usage(dict(getattr(message, "usage_metadata", None) or metadata.get("token_usage") or {})),
                    "usageReported": bool(getattr(message, "usage_metadata", None) or metadata.get("token_usage")),
                    "streamEnd": stream_end, "exceptionCategory": type(error).__name__ if error else None})

    def conversion(self, chunk: dict, generation: Any) -> None:
        context = self.current.get()
        if not context or not context.get("matched"):
            return
        context["chunkSequence"] = context.get("chunkSequence", 0) + 1
        choices = chunk.get("choices") or (chunk.get("chunk") or {}).get("choices") or []
        rows = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if delta.get("tool_calls") or choice.get("finish_reason"):
                rows.append({"index": choice.get("index") if type(choice.get("index")) is int else None,
                             "finishReason": finish_fact(choice.get("finish_reason")),
                             "toolArguments": raw_call_facts(delta.get("tool_calls") or [])})
        if rows or chunk.get("usage"):
            self.write({"boundary": "openai_sdk_input_delta_conversion", "captureId": context["captureId"],
                        "chunkSequence": context["chunkSequence"], "choices": rows, "usage": numeric_usage(chunk.get("usage")),
                        "sdkGenerationPresent": generation is not None,
                        "sdkToolChunks": raw_call_facts(list(getattr(getattr(generation, "message", None), "tool_call_chunks", None) or []))})

    def install(self) -> None:
        from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
        from core.llm_chat_adapter import V8ChatModelAdapter
        original_payload = V8OpenAICompatibleChatModel._get_request_payload
        original_stream = V8OpenAICompatibleChatModel._stream
        original_astream = V8OpenAICompatibleChatModel._astream
        original_result = V8OpenAICompatibleChatModel._create_chat_result
        original_conversion = V8OpenAICompatibleChatModel._convert_chunk_to_generation_chunk
        original_prepare = V8ChatModelAdapter._prepare_prompt_cache_request
        original_validate = V8ChatModelAdapter._validate_complete_tool_response
        capture = self

        def payload(model, *args, **kwargs):
            result = original_payload(model, *args, **kwargs)
            capture.request(result)
            return result

        def stream(model, *args, **kwargs):
            token = capture.current.set({"captureId": uuid.uuid4().hex})
            aggregate = None
            try:
                for chunk in original_stream(model, *args, **kwargs):
                    aggregate = chunk if aggregate is None else aggregate + chunk
                    yield chunk
                capture.response(getattr(aggregate, "message", None), getattr(aggregate, "generation_info", None))
            except BaseException as error:
                capture.response(getattr(aggregate, "message", None), getattr(aggregate, "generation_info", None),
                                 stream_end="interrupted", error=error)
                raise
            finally:
                capture.current.reset(token)

        async def astream(model, *args, **kwargs):
            token = capture.current.set({"captureId": uuid.uuid4().hex})
            aggregate = None
            try:
                async for chunk in original_astream(model, *args, **kwargs):
                    aggregate = chunk if aggregate is None else aggregate + chunk
                    yield chunk
                capture.response(getattr(aggregate, "message", None), getattr(aggregate, "generation_info", None))
            except BaseException as error:
                capture.response(getattr(aggregate, "message", None), getattr(aggregate, "generation_info", None),
                                 stream_end="interrupted", error=error)
                raise
            finally:
                capture.current.reset(token)

        def result(model, *args, **kwargs):
            response = original_result(model, *args, **kwargs)
            for generation in response.generations:
                capture.response(generation.message, generation.generation_info)
            capture.current.set(None)
            return response

        def conversion(model, chunk, *args, **kwargs):
            generation = original_conversion(model, chunk, *args, **kwargs)
            capture.conversion(chunk, generation)
            return generation

        def prepare(adapter, messages, *args, **kwargs):
            capture.invocation.set(None)
            prepared = original_prepare(adapter, messages, *args, **kwargs)
            if any(capture.marker in str(getattr(message, "content", "")) for message in messages):
                capture.invocation.set({"outputTokenPolicy": policy_facts(prepared.diagnostics.get("outputTokenBudget") or {})})
            return prepared

        def validate(adapter, response):
            invocation = capture.invocation.get()
            context = {"captureId": invocation["captureId"], "matched": True} if invocation and invocation.get("captureId") else None
            error = None
            try:
                return original_validate(adapter, response)
            except BaseException as caught:
                error = caught
                raise
            finally:
                if context:
                    details = getattr(error, "details", None) or {}
                    reason = details.get("reason")
                    metadata = dict(getattr(response, "response_metadata", None) or {})
                    capture.write({"boundary": "v8_adapter_complete_response_validation", "captureId": context["captureId"],
                                   "accepted": error is None, "exceptionCategory": type(error).__name__ if error else None,
                                   "reason": reason if reason in {"incomplete_tool_arguments", "invalid_tool_arguments", "output_limit", "tool_protocol_in_text"} else None,
                                   "finishReason": finish_fact(metadata.get("finish_reason") or metadata.get("stop_reason")),
                                   "argumentViews": {
                                       "tool_call_chunks": raw_call_facts(list(getattr(response, "tool_call_chunks", None) or [])),
                                       "additional_kwargs.tool_calls": raw_call_facts(list((getattr(response, "additional_kwargs", None) or {}).get("tool_calls") or [])),
                                       "invalid_tool_calls": raw_call_facts(list(getattr(response, "invalid_tool_calls", None) or [])),
                                   }})

        V8OpenAICompatibleChatModel._get_request_payload = payload
        V8OpenAICompatibleChatModel._stream = stream
        V8OpenAICompatibleChatModel._astream = astream
        V8OpenAICompatibleChatModel._create_chat_result = result
        V8OpenAICompatibleChatModel._convert_chunk_to_generation_chunk = conversion
        V8ChatModelAdapter._prepare_prompt_cache_request = prepare
        V8ChatModelAdapter._validate_complete_tool_response = validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--workspace-marker", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=22830)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("Provider capture requires explicit --live")
    state = args.state_root.resolve()
    if state == (Path.home() / ".v8-agent-os").resolve() or not state.is_dir():
        parser.error("Use an existing isolated state root")
    if args.port in {9527, 9528, 9530} or not 1024 <= args.port <= 65535:
        parser.error("Use the assigned isolated Engine port")
    capture = ScopedCapture(args.workspace_marker, args.output.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["V8_AGENT_OS_HOME"] = str(state)
    os.environ["ENGINE_PORT"] = str(args.port)
    os.environ["ENGINE_HOST"] = "127.0.0.1"
    os.environ["ENGINE_RELOAD"] = "0"
    import sys
    engine = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(engine))
    capture.install()
    runpy.run_path(str(engine / "main.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
