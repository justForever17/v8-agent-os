"""Opt-in, scoped provider-contract capture for the synthetic cross-graph live task.

No headers, credentials, whole messages or arbitrary argument values are saved.
Response evidence is labelled SDK-assembled; historical calls in a request are
labelled serialized history, never represented as raw network responses.
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


class ScopedCapture:
    def __init__(self, marker: str, output: Path):
        if "cross-graph-live" not in marker or len(marker) < 16:
            raise ValueError("Use the exact synthetic cross-graph-live workspace marker")
        self.marker, self.output = marker, output
        self.lock = threading.Lock()
        self.current: ContextVar[dict | None] = ContextVar("cross_graph_capture", default=None)

    def write(self, item: dict[str, Any]) -> None:
        encoded = json.dumps(_without_url_queries(item), ensure_ascii=False)
        with self.lock:
            with self.output.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")

    def request(self, payload: dict[str, Any]) -> None:
        if not matching_payload(payload, self.marker):
            return
        context = self.current.get()
        if context is None:
            context = {"captureId": uuid.uuid4().hex}
            self.current.set(context)
        context["matched"] = True
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

    def response(self, message: Any) -> None:
        context = self.current.get()
        if not context or not context.get("matched"):
            return
        summaries = summarize_calls(list(getattr(message, "tool_calls", None) or []))
        if summaries:
            self.write({"boundary": "openai_sdk_assembled_response", "captureId": context["captureId"], "toolCalls": summaries})

    def install(self) -> None:
        from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
        original_payload = V8OpenAICompatibleChatModel._get_request_payload
        original_stream = V8OpenAICompatibleChatModel._stream
        original_astream = V8OpenAICompatibleChatModel._astream
        original_result = V8OpenAICompatibleChatModel._create_chat_result
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
                    aggregate = chunk.message if aggregate is None else aggregate + chunk.message
                    yield chunk
                if aggregate is not None:
                    capture.response(aggregate)
            finally:
                capture.current.reset(token)

        async def astream(model, *args, **kwargs):
            token = capture.current.set({"captureId": uuid.uuid4().hex})
            aggregate = None
            try:
                async for chunk in original_astream(model, *args, **kwargs):
                    aggregate = chunk.message if aggregate is None else aggregate + chunk.message
                    yield chunk
                if aggregate is not None:
                    capture.response(aggregate)
            finally:
                capture.current.reset(token)

        def result(model, *args, **kwargs):
            response = original_result(model, *args, **kwargs)
            for generation in response.generations:
                capture.response(generation.message)
            capture.current.set(None)
            return response

        V8OpenAICompatibleChatModel._get_request_payload = payload
        V8OpenAICompatibleChatModel._stream = stream
        V8OpenAICompatibleChatModel._astream = astream
        V8OpenAICompatibleChatModel._create_chat_result = result


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
