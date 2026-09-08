from contextvars import ContextVar
import threading
import time

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from core.llm_exceptions import V8LLMStructuredOutputError
from runtimes.research.model_call import IncompleteModelResponse, invoke_bounded


def test_stream_preserves_runtime_context_and_assembles_tool_arguments():
    identity = ContextVar("test_research_run", default="missing")
    token = identity.set("run-test")
    closed = []
    class Model:
        def stream(self, messages, **kwargs):
            assert identity.get() == "run-test"
            assert kwargs["timeout"] == 1
            try:
                yield AIMessageChunk(content="", tool_call_chunks=[{"name": "read_research_source", "args": '{"sourceKey":', "id": "read", "index": 0}])
                yield AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": '"S1"}', "id": None, "index": 0}])
            finally:
                closed.append(True)
    try:
        response = invoke_bounded(Model(), [], seconds=1, request_kwargs={"timeout": 1}, streaming=True)
    finally:
        identity.reset(token)
    assert response.tool_calls[0]["args"] == {"sourceKey": "S1"}
    assert response.response_metadata["researchCallTiming"]["chunkCount"] == 2
    assert response.response_metadata["researchCallTiming"]["firstChunkMs"] is not None
    assert closed == [True]


def test_timeout_stops_a_live_stream_without_accepting_its_partial_tool_call():
    closed = threading.Event()
    class Model:
        def stream(self, *_args, **_kwargs):
            try:
                while True:
                    time.sleep(0.02)
                    yield AIMessageChunk(content="incomplete")
            finally:
                closed.set()
    with pytest.raises(TimeoutError) as error:
        invoke_bounded(Model(), [], seconds=0.08, request_kwargs={}, streaming=True)
    assert closed.wait(0.5)
    assert error.value.research_call_timing["chunkCount"] > 0


def test_nonstreaming_transport_still_preserves_context_and_calls_once():
    identity = ContextVar("test_nonstream_run", default="missing")
    token = identity.set("run-test")
    calls = []
    class Model:
        def invoke(self, *_args, **_kwargs):
            calls.append(identity.get())
            return AIMessage(content="response")
    try:
        result = invoke_bounded(Model(), [], seconds=1, request_kwargs={}, streaming=False)
    finally:
        identity.reset(token)
    assert calls == ["run-test"]
    assert result.response_metadata["researchCallTiming"]["streaming"] is False


def test_active_output_can_finish_after_idle_timeout_within_total_budget():
    class Model:
        def stream(self, *_args, **kwargs):
            assert kwargs["timeout"] == 0.05
            for _ in range(8):
                time.sleep(0.02)
                yield AIMessageChunk(content="x")
    result = invoke_bounded(Model(), [], seconds=1, request_kwargs={"timeout": 0.05}, streaming=True, idle_timeout_seconds=0.05)
    assert result.content == "xxxxxxxx"
    assert result.response_metadata["researchCallTiming"]["elapsedMs"] >= 150


def test_empty_keepalives_cannot_consume_the_whole_research_deadline():
    closed = threading.Event()
    class Model:
        def stream(self, *_args, **_kwargs):
            try:
                while True:
                    time.sleep(0.01)
                    yield AIMessageChunk(content="")
            finally:
                closed.set()
    with pytest.raises(IncompleteModelResponse, match="research_provider_idle_timeout") as error:
        invoke_bounded(Model(), [], seconds=2, request_kwargs={}, streaming=True, idle_timeout_seconds=0.05)
    assert error.value.research_call_timing["elapsedMs"] < 1000
    assert error.value.research_call_timing["idleMs"] >= 45
    assert closed.wait(0.5)


@pytest.mark.parametrize("fields", [
    {"content": "", "additional_kwargs": {"reasoning_content": "working"}},
    {"content": "", "additional_kwargs": {"reasoning": "working"}},
    {"content": [{"type": "thinking", "thinking": "working"}]},
])
def test_provider_reasoning_progress_is_not_mistaken_for_idle(fields):
    class Model:
        def stream(self, *_args, **_kwargs):
            for _ in range(8):
                time.sleep(0.02)
                yield AIMessageChunk(**fields)
            yield AIMessageChunk(content="done")
    result = invoke_bounded(Model(), [], seconds=1, request_kwargs={}, streaming=True, idle_timeout_seconds=0.05)
    assert result.response_metadata["researchCallTiming"]["chunkCount"] == 9


@pytest.mark.parametrize("arguments", [
    '{"answer":"partial',
    '{"coverage":"complete","limitations":[],"answer":"Looks complete [S1]"',
])
def test_eof_repaired_by_langchain_is_not_a_completed_tool_call(arguments):
    chunk = AIMessageChunk(content="", tool_call_chunks=[{
        "name": "submit_research_answer", "args": arguments, "id": "submit", "index": 0,
    }])
    assert chunk.tool_calls  # The upstream partial parser alone is insufficient.
    class Model:
        def stream(self, *_args, **_kwargs):
            yield chunk
    with pytest.raises(IncompleteModelResponse, match="research_model_response_incomplete") as error:
        invoke_bounded(Model(), [], seconds=1, request_kwargs={"max_tokens": 512}, streaming=True)
    assert error.value.research_call_timing["requestedMaxTokens"] == 512
    assert error.value.research_call_timing["finishReason"] == ""


@pytest.mark.parametrize("streaming", [True, False])
def test_reported_output_limit_is_not_a_schema_error_or_success(streaming):
    class Model:
        def stream(self, *_args, **_kwargs):
            yield AIMessageChunk(content="partial", response_metadata={"finish_reason": "length"})
        def invoke(self, *_args, **_kwargs):
            return AIMessage(content="partial", response_metadata={"stop_reason": "max_tokens"})
    with pytest.raises(IncompleteModelResponse, match="research_model_output_limit"):
        invoke_bounded(Model(), [], seconds=1, request_kwargs={}, streaming=streaming)


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("reason,expected", [
    ("output_limit", "research_model_output_limit"),
    ("incomplete_tool_arguments", "research_model_response_incomplete"),
])
def test_shared_adapter_incomplete_error_preserves_local_recovery_type(streaming, reason, expected):
    failure = V8LLMStructuredOutputError(
        code="model_output_incomplete", message="incomplete", provider="fixture", model="fixture",
        details={"reason": reason, "finishReason": "length" if reason == "output_limit" else ""},
    )
    calls = []
    class Model:
        def stream(self, *_args, **_kwargs):
            calls.append("stream")
            yield AIMessageChunk(content="progress")
            raise failure
        def invoke(self, *_args, **_kwargs):
            calls.append("invoke")
            raise failure

    with pytest.raises(IncompleteModelResponse, match=expected) as error:
        invoke_bounded(Model(), [], seconds=1, request_kwargs={}, streaming=streaming)
    assert len(calls) == 1  # The Research agent, not this transport, owns retry.
    assert error.value.__cause__ is failure
    assert error.value.research_call_timing["finishReason"] == failure.details["finishReason"]
    assert error.value.research_call_timing["chunkCount"] == (1 if streaming else 0)


def test_non_incomplete_provider_errors_do_not_become_local_generation_retries():
    failure = V8LLMStructuredOutputError(code="auth_error", message="authentication failed")
    class Model:
        def invoke(self, *_args, **_kwargs):
            raise failure
    with pytest.raises(V8LLMStructuredOutputError) as error:
        invoke_bounded(Model(), [], seconds=1, request_kwargs={}, streaming=False)
    assert error.value is failure
