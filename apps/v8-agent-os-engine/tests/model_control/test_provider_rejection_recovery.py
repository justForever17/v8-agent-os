import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from core.llm_chat_adapter import V8ChatModelAdapter
from core.llm_exceptions import V8LLMContentPolicyError
from core.provider_compatibility import normalize_provider_error


def test_structured_policy_signal_precedes_generic_http_400():
    error = RuntimeError('400 bad request')
    error.body = {'error': {'code': 'content_policy_violation', 'message': 'PRIVATE-INPUT'}}
    normalized = normalize_provider_error(error)
    assert normalized['code'] == 'content_policy_block'
    assert normalized['retryable'] is False
    assert 'PRIVATE-INPUT' not in str(normalized)


def test_opaque_invalid_parameter_is_not_a_policy_refusal():
    normalized = normalize_provider_error(ValueError('400 invalid safety_settings parameter'))
    assert normalized['code'] == 'invalid_request'


@pytest.mark.parametrize('response', [
    AIMessage(content='', additional_kwargs={'refusal': 'PRIVATE-REFUSAL'}),
    AIMessage(content='', response_metadata={'stop_reason': 'refusal'}),
    AIMessage(content=[{'type': 'refusal', 'refusal': 'PRIVATE-REFUSAL'}]),
    AIMessage(content='', response_metadata={'finish_reason': 'content_filter'}),
    AIMessage(content='', response_metadata={'finish_reason': 'SAFETY'}),
    AIMessage(content='', response_metadata={'prompt_feedback': {'block_reason': 'PROHIBITED_CONTENT'}}),
])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_successful_transport_refusal_is_typed_and_next_input_works(response, asynchronous):
    class Native:
        calls = 0

        def invoke(self, messages, **kwargs):
            self.calls += 1
            return response if self.calls == 1 else AIMessage(content='next request completed')

        async def ainvoke(self, messages, **kwargs):
            return self.invoke(messages, **kwargs)

    native = Native()
    adapter = V8ChatModelAdapter(model_id='fixture', provider_standard='openai', role='supervisor',
                                 meta={}, model_kwargs={}, builder=lambda: native)
    invoke = (lambda text: asyncio.run(adapter.ainvoke([HumanMessage(content=text)]))) if asynchronous else (
        lambda text: adapter.invoke([HumanMessage(content=text)]))
    with pytest.raises(V8LLMContentPolicyError) as caught:
        invoke('first benign fixture')
    assert caught.value.retryable is False
    assert caught.value.details['failureClass'] == 'provider_content_policy'
    assert 'PRIVATE-REFUSAL' not in str(caught.value.details)
    assert native.calls == 1
    assert invoke('new request').content == 'next request completed'


@pytest.mark.parametrize('asynchronous', [False, True])
def test_stream_refusal_retains_partial_and_does_not_retry(asynchronous):
    class Native:
        calls = 0

        def stream(self, messages, **kwargs):
            self.calls += 1
            yield AIMessageChunk(content='retained partial')
            yield AIMessageChunk(content='', response_metadata={'stop_reason': 'refusal'})

        async def astream(self, messages, **kwargs):
            for chunk in self.stream(messages, **kwargs):
                yield chunk

    native = Native()
    adapter = V8ChatModelAdapter(model_id='fixture', provider_standard='anthropic', role='supervisor',
                                 meta={}, model_kwargs={}, builder=lambda: native)
    seen = []

    async def collect():
        async for chunk in adapter.astream([HumanMessage(content='benign fixture')]):
            seen.append(chunk.content)

    with pytest.raises(V8LLMContentPolicyError) as caught:
        if asynchronous:
            asyncio.run(collect())
        else:
            for chunk in adapter.stream([HumanMessage(content='benign fixture')]):
                seen.append(chunk.content)
    assert ''.join(seen) == 'retained partial'
    assert caught.value.details['stage'] == 'stream'
    assert caught.value.details['partialOutputAvailable'] is True
    assert native.calls == 1
