"""Bounded provider calls preserving runtime identity and actual stream timing."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any

from langchain_core.messages import message_chunk_to_message

from core.response_normalizer import extract_text_and_reasoning


class IncompleteModelResponse(RuntimeError):
    """A partial transport payload cannot authorize a tool action."""


def validate_complete_response(response: Any) -> None:
    metadata = getattr(response, "response_metadata", None) or {}
    finish = str(metadata.get("finish_reason") or metadata.get("stop_reason") or "").lower()
    if finish in {"length", "max_tokens", "incomplete"}:
        raise IncompleteModelResponse("research_model_output_limit")
    # AIMessageChunk.tool_calls uses parse_partial_json, which repairs an EOF
    # inside a string/object. Only the original, fully assembled JSON is proof
    # that the model finished specifying this action.
    for call in getattr(response, "tool_call_chunks", []) or []:
        try:
            arguments = json.loads(call.get("args") or "")
        except (TypeError, ValueError) as exc:
            raise IncompleteModelResponse("research_model_response_incomplete") from exc
        if not isinstance(arguments, dict):
            raise IncompleteModelResponse("research_model_response_incomplete")
    if getattr(response, "invalid_tool_calls", None):
        raise IncompleteModelResponse("research_model_response_incomplete")


def invoke_bounded(llm: Any, messages: list[Any], *, seconds: float, request_kwargs: dict[str, Any], streaming: bool,
                   idle_timeout_seconds: float | None = None) -> Any:
    stopped = threading.Event()
    started = time.monotonic()
    last_progress = started
    timing: dict[str, Any] = {"streaming": streaming, "chunkCount": 0, "firstChunkMs": None, "maxChunkGapMs": 0,
                              "requestedMaxTokens": request_kwargs.get("max_tokens")}

    def invoke():
        nonlocal last_progress
        stream = None
        try:
            if not streaming:
                return llm.invoke(messages, **request_kwargs)
            stream = llm.stream(messages, **request_kwargs)
            aggregate = None
            previous = None
            for chunk in stream:
                now = time.monotonic()
                if stopped.is_set() or now - started >= seconds:
                    raise TimeoutError("research_call_deadline_exceeded")
                if previous is None:
                    timing["firstChunkMs"] = int((now - started) * 1000)
                else:
                    timing["maxChunkGapMs"] = max(timing["maxChunkGapMs"], int((now - previous) * 1000))
                previous = now
                timing["chunkCount"] += 1
                text, reasoning = extract_text_and_reasoning(chunk)
                if text or reasoning or getattr(chunk, "tool_call_chunks", None):
                    last_progress = now
                aggregate = chunk if aggregate is None else aggregate + chunk
            if aggregate is None:
                raise RuntimeError("research_provider_empty_stream")
            timing["finishReason"] = (aggregate.response_metadata or {}).get("finish_reason") or (aggregate.response_metadata or {}).get("stop_reason") or ""
            timing["contentChars"] = len(aggregate.content) if isinstance(aggregate.content, str) else len(json.dumps(aggregate.content, ensure_ascii=False))
            timing["toolArguments"] = [{"name": call.get("name"), "chars": len(call.get("args") or "")}
                                       for call in aggregate.tool_call_chunks or []]
            validate_complete_response(aggregate)
            return message_chunk_to_message(aggregate)
        finally:
            if stream is not None and callable(getattr(stream, "close", None)):
                stream.close()

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-provider")
    future = executor.submit(copy_context().run, invoke)
    try:
        while True:
            now = time.monotonic()
            remaining = seconds - (now - started)
            if remaining <= 0:
                raise TimeoutError("research_call_deadline_exceeded")
            idle_remaining = (idle_timeout_seconds - (now - last_progress)
                              if streaming and idle_timeout_seconds is not None else remaining)
            if idle_remaining <= 0:
                raise IncompleteModelResponse("research_provider_idle_timeout")
            try:
                response = future.result(timeout=min(0.25, remaining, idle_remaining))
                break
            except TimeoutError:
                if future.done():
                    raise
        validate_complete_response(response)
        timing["elapsedMs"] = int((time.monotonic() - started) * 1000)
        timing["idleMs"] = int((time.monotonic() - last_progress) * 1000)
        response.response_metadata = {**(getattr(response, "response_metadata", None) or {}), "researchCallTiming": dict(timing)}
        return response
    except Exception as exc:
        stopped.set()
        timing["elapsedMs"] = int((time.monotonic() - started) * 1000)
        timing["idleMs"] = int((time.monotonic() - last_progress) * 1000)
        exc.research_call_timing = dict(timing)
        raise
    finally:
        # Active streams observe stopped on their next chunk; transport timeout
        # bounds a blocked socket. Python threads cannot be forcibly terminated.
        executor.shutdown(wait=False, cancel_futures=True)
