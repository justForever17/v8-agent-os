from langchain_core.messages import HumanMessage, message_chunk_to_message
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver
from openai.types.chat import ChatCompletionChunk

from tests.model_control import test_openai_compatible_reasoning_adapter as fixtures


def test_known_native_block_index_survives_an_id_only_delta_and_checkpoint(tmp_path):
    model = fixtures._model(stream_mode="delta")
    blocks = [
        {"index": 4, "id": "block-a", "type": "reasoning.text", "format": "native", "text": "A"},
        {"index": 9, "id": "block-b", "type": "reasoning.text", "format": "native", "text": "B"},
        {"id": "block-a", "text": "A"},
        {"index": 9, "text": "B"},
    ]
    chunks = [ChatCompletionChunk.model_validate({
        "id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": "MiniMax-M3",
        "choices": [{"index": 0, "delta": {"reasoning_details": [block]}, "finish_reason": None}],
    }) for block in blocks]
    chunks.append(ChatCompletionChunk.model_validate({
        "id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": "MiniMax-M3",
        "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, **fixtures._tool_call()}]},
                     "finish_reason": "tool_calls"}],
    }))
    object.__setattr__(model, "client", fixtures._SyncClient(chunks))
    emitted = list(model.stream([HumanMessage(content="synthetic fixture")]))
    aggregate = emitted[0]
    for chunk in emitted[1:]:
        aggregate += chunk
    message = message_chunk_to_message(aggregate)
    expected = [{**blocks[0], "text": "AA"}, {**blocks[1], "text": "BB"}]
    # The SDK's public aggregate and the private continuation must represent
    # the same two native blocks, even when one fragment omits its known index.
    assert message.additional_kwargs["reasoning_details"] == expected
    path = str(tmp_path / "checkpoint.sqlite")
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": [message]}
    checkpoint["channel_versions"] = {"messages": 1}
    with SqliteSaver.from_conn_string(path) as saver:
        reference = saver.put({"configurable": {"thread_id": "fixture", "checkpoint_ns": ""}}, checkpoint,
                              {"source": "update", "step": 0, "parents": {}}, {"messages": 1})
    with SqliteSaver.from_conn_string(path) as saver:
        restored = saver.get_tuple(reference).checkpoint["channel_values"]["messages"][0]
    wire = model._get_request_payload([restored])["messages"][0]
    assert wire["reasoning_details"] == expected
    other = fixtures._model(base_url="https://other.example/v1")
    assert "reasoning_details" not in other._get_request_payload([restored])["messages"][0]
