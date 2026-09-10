from __future__ import annotations

import base64
import json
from io import BytesIO

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from PIL import Image

from core.direct_image_input import caller_accepts_direct_images, direct_image_receipt, hydrate_direct_images
from core.tools.vision_image_inputs import prepare_ordered_images


@pytest.fixture
def example(tmp_path):
    paths = []
    for index, color in enumerate(["red", "blue", "green"]):
        path = tmp_path / f"{index}.png"
        Image.new("RGB", (1600, 900), color).save(path)
        paths.append(path)
    context = {"session_id": "fixture", "run_id": "run", "workspace_path": str(tmp_path)}
    images = [{"file_path": str(path), "label": str(i)} for i, path in enumerate(paths)]
    prepared = prepare_ordered_images(images, runtime_context=context, remote_guard=lambda _u: pytest.fail("unexpected network"))
    receipt = direct_image_receipt(prepared, prompt="Compare before, during and after.", tool_call_id="view-1", context=context)
    messages = [HumanMessage(content="Compare the images"),
                AIMessage(content="", tool_calls=[{"id": "view-1", "name": "vision_media_analyzer", "args": {"images": images}}]),
                ToolMessage(content=receipt, tool_call_id="view-1", name="vision_media_analyzer")]
    return paths, context, messages


def test_late_hydration_preserves_transcript_pixels_and_order(example):
    paths, context, messages = example
    original = [m.model_dump() for m in messages]
    result = hydrate_direct_images(messages, context=context, supports_multimodal=True)
    assert len(result) == len(messages) + 1
    assert [m.model_dump() for m in messages] == original
    assert "base64" not in json.dumps(original)
    content = result[-1].content
    images = [c["image_url"]["url"] for c in content if c["type"] == "image_url"]
    assert len(images) == 3
    for i, (path, image) in enumerate(zip(paths, images)):
        with Image.open(BytesIO(base64.b64decode(image.split(",", 1)[1]))) as sent, Image.open(path) as source:
            assert sent.size == (1344, 756)
            assert sent.getpixel((2, 2)) == source.getpixel((2, 2))
        assert f'"imageId": "image_{i+1}"' in content[2*i+1]["text"]
    assert hydrate_direct_images(result, context=context, supports_multimodal=True) == result


@pytest.mark.parametrize("fault", ["changed", "deleted", "model"])
def test_no_silent_stale_or_missing_image_success(example, fault):
    paths, context, messages = example
    if fault == "changed":
        Image.new("RGB", (1600, 900), "black").save(paths[1])
    if fault == "deleted":
        paths[1].unlink()
    result = hydrate_direct_images(messages, context=context, supports_multimodal=fault != "model")
    assert isinstance(result[-1].content, str)
    assert "No image was attached" in result[-1].content
    assert "base64" not in str(result)


@pytest.mark.parametrize("fault", ["user", "name", "call_id", "session", "run", "historical"])
def test_only_current_paired_owned_tool_result_can_attach_images(example, fault):
    _, context, messages = example
    if fault == "user": messages[-1] = HumanMessage(content=messages[-1].content)
    if fault == "name": messages[-1].name = "web_fetch"
    if fault == "call_id": messages[-1].tool_call_id = "not-paired"
    if fault == "session": context["session_id"] = "other"
    if fault == "run": context["run_id"] = "other"
    if fault == "historical": messages.append(AIMessage(content="Already analyzed."))
    assert hydrate_direct_images(messages, context=context, supports_multimodal=True) == messages


def test_tool_surface_raw_ref_restores_receipt_but_not_another_session(example, monkeypatch):
    from core.observability_db import observability_db
    _, context, messages = example
    receipt = messages[-1].content
    messages[-1].content = "Images ready; open detail for metadata."
    messages[-1].additional_kwargs = {"v8_tool_output_budget": {"rawRef": "toolobs://fixture"}}
    record = {"tool_call_id": "view-1", "tool_name": "vision_media_analyzer", "metadata": {"sessionId": "fixture"}, "raw_body_text": receipt}
    monkeypatch.setattr(observability_db, "get_tool_observation_record", lambda _: record)
    assert len(hydrate_direct_images(messages, context=context, supports_multimodal=True)) == 4
    record["metadata"]["sessionId"] = "other"
    assert hydrate_direct_images(messages, context=context, supports_multimodal=True) == messages


@pytest.mark.parametrize("enabled,vision,kind,agent,expected", [
    (True, True, "chat", "supervisor", True), (False, True, "chat", "supervisor", False),
    (True, False, "chat", "supervisor", False), (True, True, "subagent", "worker", True),
    (True, True, "research", "", False),
])
def test_preference_and_actual_caller_capability_both_required(monkeypatch, enabled, vision, kind, agent, expected):
    from core.storage import storage
    from core.model_control_plane import model_control_plane
    monkeypatch.setattr(storage, "get_supervisor_config", lambda: {"compressedDirectImages": enabled})
    roles = []
    def resolve(role):
        roles.append(role)
        return {"resolvedModel": {"capabilityClass": "chat_general", "capabilities": {"vision": vision}}}
    monkeypatch.setattr(model_control_plane, "resolve_model_for_role", resolve)
    assert caller_accepts_direct_images({"runtime_kind": kind, "agent_id": agent}) is expected
    if agent == "worker": assert roles == ["agent:worker"]


def test_visual_tool_direct_path_makes_no_analysis_model_call(example, monkeypatch):
    from core.tools import vision_media_analyzer as tool_module
    from core.storage import storage
    _, context, messages = example
    monkeypatch.setattr(tool_module, "get_runtime_context", lambda: context)
    monkeypatch.setattr(storage, "get_supervisor_config", lambda: {"compressedDirectImages": True})
    monkeypatch.setattr(tool_module.model_control_plane, "resolve_model_for_role", lambda _: {
        "resolvedModel": {"capabilityClass": "vision_multimodal"}})
    monkeypatch.setattr(tool_module.llm_factory, "create_for_role", lambda *_a, **_k: pytest.fail("extra vision model hop"))
    result = tool_module.vision_media_analyzer.invoke({"type": "tool_call", **messages[1].tool_calls[0]})
    assert json.loads(result.content)["status"] == "prepared_not_analyzed"
    assert "base64" not in result.content


def test_real_observation_store_contract_survives_compaction(example):
    from core.tool_surface import apply_tool_surface_budget
    from erc.runtime_context import bind_runtime_context
    _, context, messages = example
    with bind_runtime_context(**context):
        messages[-1] = apply_tool_surface_budget(messages[-1], {"agentVisibleBudget": 320})
    assert messages[-1].additional_kwargs["v8_tool_output_budget"]["rawRef"].startswith("toolobs://")
    result = hydrate_direct_images(messages, context=context, supports_multimodal=True)
    assert len(result) == 4
    assert sum(b.get("type") == "image_url" for b in result[-1].content) == 3
