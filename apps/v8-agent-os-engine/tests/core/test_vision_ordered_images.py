from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from PIL import Image
from pydantic import ValidationError

from core import local_visual_support
from core.runtime_tool_access import filter_visible_tools_for_actor
from core.tools import vision_image_inputs as image_inputs
from core.tools import vision_media_analyzer as vision


@pytest.fixture
def environment(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    context = {"workspace_path": str(root), "session_id": "fixture-session", "run_id": "fixture-run",
               "actor_role": "supervisor", "agent_id": "supervisor", "runtime_kind": "chat"}
    calls, observed, model_kwargs = [], [], []
    monkeypatch.setattr(vision, "get_runtime_context", lambda: context)
    monkeypatch.setattr(vision.model_control_plane, "resolve_model_for_role", lambda role: {
        "resolvedProvider": {"type": "API", "api_standard": "openai"}, "resolvedProviderId": "fixture-provider",
        "resolvedModel": {}, "resolvedModelId": "fixture-vision",
    })
    monkeypatch.setattr(vision.model_control_plane, "get_config", lambda: {})
    monkeypatch.setattr(vision.model_budget_service, "enforce_or_raise", lambda **kwargs: None)

    class Model:
        def invoke(self, messages, config):
            calls.append((messages, config))
            return AIMessage(content="按 image_1、image_2 的顺序比较；标签不足以证明因果。")

    def model_factory(role, **kwargs):
        model_kwargs.append((role, kwargs))
        return Model()
    monkeypatch.setattr(vision.llm_factory, "create_for_role", model_factory)
    from core import tool_surface
    monkeypatch.setattr(tool_surface, "record_raw_observation", lambda **kwargs: (observed.append(kwargs) or "toolobs://fixture"))
    monkeypatch.setattr(vision, "_try_upload_media_to_s3", lambda *_a: pytest.fail("joint input must not upload individual files to S3"))
    monkeypatch.setattr(vision.artifact_store, "record_local_file", lambda **_k: pytest.fail("joint inputs are not new artifacts"))
    return root, context, calls, observed, model_kwargs


def picture(root, name, color, size=(24, 18)):
    path = root / name
    Image.new("RGB", size, color=color).save(path)
    return path


def call_tool(images, **kwargs):
    return vision.vision_media_analyzer.invoke({"type": "tool_call", "id": "call-fixture",
        "name": "vision_media_analyzer", "args": {"images": images, **kwargs}}).content


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 1, 0)])
def test_one_actual_request_preserves_order_labels_pixels_and_hashes(environment, order):
    root, _, calls, observed, model_kwargs = environment
    paths = [picture(root, f"{index}.png", color) for index, color in enumerate(["red", "green", "blue"])]
    result = call_tool([{"file_path": str(paths[index]), "label": label}
                        for index, label in zip(order, ["before", "during", "after"])], prompt="比较这些画面")
    assert result.startswith("--- Vision Analysis Complete ---")
    assert len(calls) == 1
    messages, config = calls[0]
    assert len(messages) == 1
    blocks = messages[0].content
    assert [block["type"] for block in blocks] == ["text", "text", "image_url", "text", "image_url", "text", "image_url"]
    assert "not verified chronology or proof of causality" in blocks[0]["text"]
    for index, source_index in enumerate(order):
        item = config["metadata"]["images"][index]
        assert item["imageId"] == f"image_{index + 1}" and item["index"] == index + 1
        assert item["sourceSha256"] == hashlib.sha256(paths[source_index].read_bytes()).hexdigest()
        sent = base64.b64decode(blocks[index * 2 + 2]["image_url"]["url"].split(",", 1)[1])
        assert item["inputSha256"] == hashlib.sha256(sent).hexdigest()
        with Image.open(BytesIO(sent)) as image, Image.open(paths[source_index]) as original:
            assert image.getpixel((4, 4)) == original.getpixel((4, 4))
        assert f'"imageId": "image_{index + 1}"' in blocks[index * 2 + 1]["text"]
        assert f"image_{index + 1}" in result and item["sourceRef"] in result
        assert item["inputSha256"] not in result, "raw hashes belong to Runtime observation metadata"
    assert model_kwargs == [("vision", {"temperature": 0.1})], "no hidden output cap"
    assert observed[0]["metadata"]["images"] == config["metadata"]["images"]
    assert "tool_observation_detail(raw_ref='toolobs://fixture')" in result


def test_resize_reports_original_and_actual_sent_dimensions(environment):
    root, _, calls, _, _ = environment
    image = picture(root, "large.png", "purple", (2400, 1800))
    call_tool([{"file_path": str(image)}])
    metadata = calls[0][1]["metadata"]["images"][0]
    assert (metadata["sourceWidth"], metadata["sourceHeight"]) == (2400, 1800)
    assert metadata["resized"] is True
    assert metadata["inputWidth"] <= 1344 and metadata["inputHeight"] <= 1344
    assert metadata["inputSha256"] != metadata["sourceSha256"]


@pytest.mark.parametrize("key", ["file_path", "source_url", "mime_type_hint"])
def test_images_cannot_mix_legacy_source_parameters(environment, key):
    _, _, calls, _, _ = environment
    result = call_tool([{"file_path": "image.png"}], **{key: "conflicting input"})
    assert "images_cannot_mix_single_source_parameters" in result and calls == []


@pytest.mark.parametrize("item", [{}, {"file_path": "a.png", "source_url": "https://example.test/a.png"},
                                  {"source_url": "file:///private/a.png"}, {"source_url": "https://user:secret@example.test/a.png"},
                                  {"file_path": "a.png", "label": 42}, {"file_path": "a.png", "extra": "ignored?"}])
def test_invalid_item_schema_is_not_coerced_or_partially_executed(environment, item):
    with pytest.raises(ValidationError):
        call_tool([item])
    assert environment[2] == []


@pytest.mark.parametrize("count", [0, 9])
def test_count_limits_are_request_schema_and_function_guards(environment, count):
    images = [{"file_path": "a.png"}] * count
    with pytest.raises(ValidationError):
        call_tool(images)
    result = vision.vision_media_analyzer.func(images=images)
    assert "image_count_out_of_range" in result and environment[2] == []


@pytest.mark.parametrize("fault", ["missing", "bad", "outside", "traversal", "animated"])
def test_bad_second_image_rejects_whole_set_without_model_or_artifact(environment, fault):
    root, _, calls, observed, _ = environment
    valid = picture(root, "valid.png", "red")
    second = root / "missing.png"
    if fault == "bad":
        second.write_bytes(b"not an image")
    elif fault in {"outside", "traversal"}:
        second = picture(root.parent, "outside.png", "blue")
        if fault == "traversal":
            second = root / ".." / second.name
    elif fault == "animated":
        second = root / "animated.gif"
        Image.new("RGB", (10, 10), "red").save(second, save_all=True, append_images=[Image.new("RGB", (10, 10), "blue")])
    result = call_tool([{"file_path": str(valid)}, {"file_path": str(second)}])
    assert "image 2" in result and "整组图片未发送" in result
    assert not calls and not observed


def test_all_local_permissions_checked_before_any_remote_request(environment, monkeypatch):
    root = environment[0]
    monkeypatch.setattr(local_visual_support.requests, "get", lambda *_a, **_k: pytest.fail("later local denial must precede network"))
    result = call_tool([{"source_url": "https://example.test/a.png"}, {"file_path": str(root.parent / "outside.png")}])
    assert "image_workspace_access_denied" in result and not environment[2]


@pytest.mark.parametrize("budget", ["bytes", "pixels"])
def test_cumulative_budget_rejects_a_set_even_when_each_image_fits(environment, monkeypatch, budget):
    root = environment[0]
    first = picture(root, "a.png", "red")
    second = picture(root, "b.png", "blue")
    if budget == "bytes":
        monkeypatch.setattr(image_inputs, "MAX_VISION_IMAGE_BYTES", first.stat().st_size + second.stat().st_size - 1)
    else:
        monkeypatch.setattr(image_inputs, "MAX_VISION_IMAGE_PIXELS", 24 * 18 * 2 - 1)
    result = call_tool([{"file_path": str(first)}, {"file_path": str(second)}])
    assert f"image_total_{budget}_exceeded" in result and not environment[2]


@pytest.mark.parametrize("actor,context", [("supervisor", {}), ("subagent", {"delegationDepth": 1}), ("subagent", {"delegationDepth": 2})])
def test_all_collaboration_roles_receive_actual_nested_schema(actor, context):
    tools = filter_visible_tools_for_actor([vision.vision_media_analyzer], actor=actor, route_context=context, runtime_access=[])
    assert [tool.name for tool in tools] == ["vision_media_analyzer"]
    schema = convert_to_openai_tool(tools[0])["function"]["parameters"]
    images = next(item for item in schema["properties"]["images"]["anyOf"] if item.get("type") == "array")
    assert images["maxItems"] == 8 and images["minItems"] == 1
    assert set(images["items"]["properties"]) == {"file_path", "source_url", "label"}
    assert "file_path" in schema["properties"] and "tool_call_id" not in schema["properties"]


class Response:
    def __init__(self, data=b"", status=200, headers=None):
        self.status_code = status
        self.headers = headers or {"Content-Type": "image/png"}
        self.data = data
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def raise_for_status(self): pass
    def iter_content(self, **_kwargs): yield self.data


def test_remote_redirects_are_guarded_and_resolved_source_hash_is_recorded(environment, monkeypatch):
    root, _, calls, _, _ = environment
    data = picture(root, "download.png", "green").read_bytes()
    guarded, fetched = [], []
    monkeypatch.setattr(vision, "_enforce_remote_media_guard", lambda url, **_k: (guarded.append(url) or (True, None)))
    def get(url, **kwargs):
        fetched.append(url)
        assert kwargs["allow_redirects"] is False
        return Response(status=302, headers={"Location": "/final.png"}) if url.endswith("start.png") else Response(data)
    monkeypatch.setattr(local_visual_support.requests, "get", get)
    result = call_tool([{"source_url": "https://example.test/start.png", "label": "remote"}])
    assert result.startswith("--- Vision Analysis Complete ---")
    assert guarded == fetched == ["https://example.test/start.png", "https://example.test/final.png"]
    source = calls[0][1]["metadata"]["images"][0]
    assert source["resolvedSourceUrl"] == fetched[-1] and source["sourceSha256"] == hashlib.sha256(data).hexdigest()


def test_remote_redirect_denial_prevents_request_and_entire_model_input(environment, monkeypatch):
    fetched = []
    monkeypatch.setattr(vision, "_enforce_remote_media_guard", lambda url, **_k: ("127.0.0.1" not in url, "denied"))
    monkeypatch.setattr(local_visual_support.requests, "get", lambda url, **_k:
                        (fetched.append(url) or Response(status=302, headers={"Location": "http://127.0.0.1/private.png"})))
    result = call_tool([{"source_url": "https://example.test/start.png"}])
    assert "image_remote_access_denied" in result and "image 1" in result
    assert fetched == ["https://example.test/start.png"] and environment[2] == []


def test_full_model_result_remains_in_return_and_observation(environment, monkeypatch):
    root, _, _, observed, _ = environment
    long_answer = "完整分析内容。" * 12000 + "FINAL-CANARY"
    class Model:
        def invoke(self, *_args, **_kwargs): return AIMessage(content=long_answer)
    monkeypatch.setattr(vision.llm_factory, "create_for_role", lambda *_a, **_k: Model())
    result = call_tool([{"file_path": str(picture(root, "a.png", "red"))}])
    assert long_answer in result and long_answer in observed[0]["raw_content"]


def test_workspace_symlink_does_not_grant_unregistered_external_image(environment):
    root, _, calls, _, _ = environment
    target = picture(root.parent, "external.png", "blue")
    link = root / "innocent.png"
    try:
        link.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows user lacks symlink creation privilege")
        raise
    result = call_tool([{"file_path": str(link)}])
    assert "image_workspace_access_denied" in result and calls == []


def test_legacy_single_image_and_one_item_set_send_decodable_actual_bytes(environment, monkeypatch):
    root, _, calls, _, model_kwargs = environment
    path = picture(root, "legacy.png", "orange")
    derivatives = []
    monkeypatch.setattr(vision.artifact_store, "record_local_file", lambda **kwargs: derivatives.append(kwargs))
    legacy = vision.vision_media_analyzer.func(file_path=str(path), prompt="描述画面")
    joint = call_tool([{"file_path": str(path)}], prompt="描述画面")
    assert legacy.startswith("--- Vision Analysis Complete ---")
    assert joint.startswith("--- Vision Analysis Complete ---")
    assert len(calls) == 2 and len(derivatives) == 1
    assert derivatives[0]["resource_role"] == "source_derivative"
    for messages, config in calls:
        images = [block for block in messages[0].content if block["type"] == "image_url"]
        assert len(images) == 1
        actual_bytes = base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1], validate=True)
        with Image.open(BytesIO(actual_bytes)) as sent:
            assert sent.size == (24, 18) and sent.getpixel((4, 4)) == (255, 165, 0)
        assert config["metadata"]["transportMode"] == "inline_base64_image"
    assert model_kwargs == [("vision", {"temperature": 0.1})] * 2


def test_local_model_explicitly_unsupported_rejects_entire_set_before_invoke(environment, monkeypatch):
    root, _, calls, observed, model_kwargs = environment
    monkeypatch.setattr(vision.model_control_plane, "resolve_model_for_role", lambda _role: {
        "resolvedProvider": {"type": "LOCAL", "api_standard": "openai", "base_url": "http://127.0.0.1:11434"},
        "resolvedProviderId": "fixture-local", "resolvedModel": {}, "resolvedModelId": "fixture-text-only",
    })
    monkeypatch.setattr(vision, "probe_local_multimodal_capability", lambda **_kwargs: {"status": "unsupported"})
    result = call_tool([{"file_path": str(picture(root, f"{index}.png", color))}
                        for index, color in enumerate(["red", "blue"])])
    assert "vision_model_image_input_unsupported" in result and "整组图片未发送" in result
    assert calls == observed == model_kwargs == []


@pytest.fixture
def registered_resources(environment, tmp_path, monkeypatch):
    from core import creative_media_resource_authority as authority_module
    from core.database import DatabaseManager

    root, context, *_ = environment
    database = DatabaseManager(tmp_path / "isolated-media.db")
    database.create_or_update_session(context["session_id"], "Fixture", user_id="fixture-owner")
    database.create_or_update_session("other-session", "Other", user_id="other-owner")
    authority = SimpleNamespace(resolve=lambda **_kwargs: SimpleNamespace(
        workspace_root=root, workspace_id="fixture-workspace", project_id="fixture-project"))
    resolver = authority_module.CreativeMediaResourceAuthorityService(database=database, authority_service=authority)
    monkeypatch.setattr(authority_module, "creative_media_resource_authority", resolver)
    return database, resolver


@pytest.mark.parametrize("kind", ["source", "artifact"])
def test_registered_same_session_external_image_is_readable_without_directory_grant(environment, registered_resources, kind):
    root, context, calls, _, _ = environment
    database, _ = registered_resources
    path = picture(root.parent, "registered.png", "cyan")
    if kind == "source":
        database.add_session_source(source_id="registered", session_id=context["session_id"], source_kind="upload",
                                    mime_type="image/png", workspace_path=str(path))
    else:
        database.add_runtime_artifact("registered", "image", "image/png", session_id=context["session_id"],
                                      source_path=str(path), auto_attach_to_message=False,
                                      metadata={"workspacePath": str(root), "sourceComponent": "computer_use"})
    result = call_tool([{"file_path": str(path)}])
    assert result.startswith("--- Vision Analysis Complete ---")
    assert calls[0][1]["metadata"]["images"][0]["resourceKind"] == kind
    assert calls[0][1]["metadata"]["images"][0]["resourceId"] == "registered"
    calls.clear()
    neighbor = picture(root.parent, "unregistered-neighbor.png", "cyan")
    denied = call_tool([{"file_path": str(path)}, {"file_path": str(neighbor)}])
    assert "image_workspace_access_denied" in denied and "image 2" in denied and calls == []


@pytest.mark.parametrize("kind", ["source", "artifact"])
@pytest.mark.parametrize("fault", ["other_session", "wrong_workspace", "deleted", "changed_path"])
def test_exact_session_registration_does_not_authorize_other_scope_or_stale_reference(environment, registered_resources, kind, fault):
    root, context, calls, _, _ = environment
    database, _ = registered_resources
    path = picture(root.parent, "private.png", "blue")
    session_id = "other-session" if fault == "other_session" else context["session_id"]
    metadata = {"workspaceId": "wrong-workspace"} if fault == "wrong_workspace" else {"workspacePath": str(root)}
    declared_path = str(path.with_name("other.png")) if fault == "changed_path" else str(path)
    if kind == "source":
        database.add_session_source(source_id="private", session_id=session_id, source_kind="upload",
                                    workspace_path=declared_path, metadata=metadata)
    else:
        database.add_runtime_artifact("private", "image", "image/png", session_id=session_id,
                                      source_path=declared_path, metadata=metadata)
    if fault == "deleted":
        with database.get_connection() as connection:
            connection.execute(f"DELETE FROM {'session_sources' if kind == 'source' else 'runtime_artifacts'} WHERE id = ?", ("private",))
            connection.commit()
    result = call_tool([{"file_path": str(path)}])
    assert "image_workspace_access_denied" in result and calls == []
