from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.tools.native import creative_media_facade as facade
from runtimes.creative_media.recipe import CreativeRecipeCompiler, prepare_provider_prompt_policy
from runtimes.creative_media.runtime import (
    _build_dashscope_video_payload,
    _build_minimax_video_payload,
    _build_volcengine_video_payload,
    creative_media_runtime,
)


@pytest.fixture
def compiler(monkeypatch):
    values = {}

    class MemoryStorage:
        def read_json(self, name):
            return deepcopy(values.get(name, {}))

        def write_json(self, name, data):
            values[name] = deepcopy(data)

    monkeypatch.setattr("runtimes.creative_media.recipe.storage", MemoryStorage())
    return CreativeRecipeCompiler()


@pytest.mark.parametrize("modality", ["image", "video"])
def test_quoted_words_keep_their_original_role_and_position(modality):
    prompt = '甲低声说：“别动。”乙回答：“我等你。”门牌写着「出口」。不加字幕。'
    policy = prepare_provider_prompt_policy(prompt, modality=modality)
    assert policy["translatedPrompt"] == prompt
    assert "on-canvas text" not in policy["translatedPrompt"]


def test_compiler_keeps_every_identity_anchor_and_negative_constraint(compiler):
    anchors = [f"身份锚点{i}：物件编号A{i}不得改变" for i in range(8)]
    bible = compiler.create_character_bible({"name": "合成角色", "identityAnchors": anchors})
    recipe = compiler.compile_recipe({
        "modality": "video", "prompt": "合成角色抬手", "durationSeconds": 8,
        "characterBibleIds": [bible["characterBibleId"]],
        "negativePrompt": "不能增加耳饰；不能把布料改为金属",
    })
    prompt = recipe["providerPrompts"]["video_generic"]
    assert all(anchor in prompt for anchor in anchors)
    assert "不能增加耳饰；不能把布料改为金属" in prompt


def test_compiler_keeps_timeline_and_numbers_references_per_modality(compiler):
    prompt = '0–7秒：固定镜头，甲说“等我”。7–12秒：乙转身，镜头仍固定；不切镜头。'
    recipe = compiler.compile_recipe({
        "modality": "video", "prompt": prompt, "durationSeconds": 12,
        "assets": [
            {"modality": "image", "role": "character_a"},
            {"modality": "video", "role": "action"},
            {"modality": "image", "role": "character_b"},
            {"modality": "music", "role": "background_music"},
        ],
    })
    rendered = recipe["providerPrompts"]["volcengine_seedance"]
    assert "@image1 as character_a" in rendered
    assert "@image2 as character_b" in rendered
    assert "@video1 as action" in rendered
    assert "@audio1 as background_music" in rendered
    assert prompt in rendered
    assert "推镜头" not in rendered and "环绕镜头" not in rendered
    assert "0-4s:" not in rendered and "End on a stable hold" not in rendered


@pytest.mark.parametrize("adapter,modality,native", [
    ("volcengine_ark", "video", False), ("minimax_video", "video", False),
    ("openai_images", "image", False), ("agnes_images", "image", False),
    ("dashscope", "video", True), ("dashscope", "image", True),
    ("agnes_video", "video", True),
])
def test_negative_constraints_are_native_or_preserved_inline(adapter, modality, native):
    negative = "排除红色围巾，尾部唯一约束：不加字幕"
    prompt, policy = creative_media_runtime._prepare_prompt_for_provider({
        "prompt": "两个角色在车站交谈", "negativePrompt": negative, "adapter": adapter,
        "operationKind": "video.reference_to_video" if modality == "video" else "image.generate",
    }, modality=modality)
    assert (negative in prompt) is not native
    assert policy["negativePromptMode"] == ("native_field" if native else "inline_constraints")


def test_facade_exposes_complete_ordered_reference_inputs(monkeypatch):
    monkeypatch.setattr(facade, "get_runtime_context", lambda: {"session_id": "synthetic"})
    monkeypatch.setattr(facade.creative_media_resource_authority, "authorize_request_resources", lambda request: [])
    request = {
        "modality": "video", "operationKind": "video.reference_to_video", "prompt": "Image 1 faces Image 2",
        "referenceImageUrls": ["https://example.test/a.png", "https://example.test/b.png"],
        "referenceVideoUrls": ["https://example.test/move.mp4", "https://example.test/camera.mp4"],
        "referenceAudioUrls": ["https://example.test/a.wav", "https://example.test/b.wav"],
        "promptExtend": False,
    }
    validated, error = facade._validate_request(facade.CREATIVE_MEDIA_ACTION_REGISTRY["jobs"]["create"], request)
    assert error is None
    assert validated == {**request, "sessionId": "synthetic"}


def test_reference_arrays_keep_duplicate_slots_for_role_binding():
    request = {"referenceImageUrls": ["https://example.test/a.png", "https://example.test/a.png", "https://example.test/b.png"]}
    assert creative_media_runtime._seedance_references_from_request(request)["image"] == request["referenceImageUrls"]
    frames = {"firstFrame": "https://example.test/loop.png", "lastFrame": "https://example.test/loop.png"}
    assert creative_media_runtime._image_urls_from_request(frames) == list(frames.values())
    payload = _build_dashscope_video_payload(
        model="wan2.7-r2v", prompt="Image 3 is the second character", operation_kind="video.reference_to_video",
        reference_image_urls=request["referenceImageUrls"],
    )
    assert [item["url"] for item in payload["input"]["media"]] == request["referenceImageUrls"]


@pytest.mark.parametrize("method", ["_seedance_references_from_request", "_minimax_h3_references_from_request", "_dashscope_references_from_request"])
def test_artifact_reference_ids_use_authorized_media_kind_and_keep_every_slot(monkeypatch, method):
    artifacts = {"a": ("image", "https://example.test/a.png"), "v": ("video", "https://example.test/v.mp4")}
    authorized = []

    def resolve(artifact_id, **kwargs):
        authorized.append((artifact_id, kwargs["context"]["sessionId"]))
        return SimpleNamespace(record={"kind": artifacts[artifact_id][0]})

    monkeypatch.setattr(creative_media_runtime, "_authorized_artifact_resource", resolve)
    monkeypatch.setattr(creative_media_runtime, "_artifact_provider_transport_url", lambda artifact_id: artifacts[artifact_id][1])
    result = getattr(creative_media_runtime, method)({"sessionId": "synthetic", "referenceAssetIds": ["a", "v", "a"]})
    assert authorized == [("a", "synthetic"), ("v", "synthetic"), ("a", "synthetic")]
    assert result["image"] == [artifacts["a"][1], artifacts["a"][1]]
    assert result["video"] == [artifacts["v"][1]]


def test_unavailable_artifact_reference_fails_before_provider_submission(monkeypatch):
    monkeypatch.setattr(creative_media_runtime, "_authorized_artifact_resource", lambda *a, **k: SimpleNamespace(record={"kind": "image"}))
    monkeypatch.setattr(creative_media_runtime, "_artifact_provider_transport_url", lambda artifact_id: "")
    with pytest.raises(ValueError, match="local-only"):
        creative_media_runtime._seedance_references_from_request({"sessionId": "synthetic", "referenceAssetIds": ["missing-transport"]})


def test_wan_voice_binding_preserves_each_subject_without_broadcast():
    media = [
        {"type": "reference_image", "url": "https://example.test/a.png", "reference_voice": "https://example.test/a.wav"},
        {"type": "reference_image", "url": "https://example.test/b.png", "reference_voice": "https://example.test/b.wav"},
        {"type": "reference_video", "url": "https://example.test/c.mp4"},
    ]
    payload = _build_dashscope_video_payload(model="wan2.7-r2v", prompt="Image 1 says A. Image 2 says B.",
                                             operation_kind="video.reference_to_video", reference_media=media)
    assert payload["input"]["media"] == media
    shared = _build_dashscope_video_payload(model="wan2.7-r2v", prompt="Two views of the same speaker", operation_kind="video.reference_to_video",
                                           reference_image_urls=[item["url"] for item in media[:2]], audio_url="https://example.test/a.wav")
    assert all(item["reference_voice"] == "https://example.test/a.wav" for item in shared["input"]["media"])
    with pytest.raises(ValueError, match="do not mix"):
        _build_dashscope_video_payload(model="wan2.7-r2v", prompt="Two speakers", operation_kind="video.reference_to_video",
                                       reference_media=media, reference_image_urls=["https://example.test/d.png"])


def test_professional_character_fields_reach_compiler_through_facade(monkeypatch, compiler):
    monkeypatch.setattr(facade, "get_runtime_context", lambda: {"session_id": "synthetic"})
    monkeypatch.setattr(facade.creative_media_resource_authority, "authorize_request_resources", lambda request: [])
    character_request = {"name": "Speaker A", "identityAnchors": ["asymmetric collar"],
                         "voiceAnchors": ["slow low voice"], "props": ["blue case"],
                         "negativeConstraints": ["no earrings"], "details": "observable texture"}
    payload, error = facade._validate_request(facade.CREATIVE_MEDIA_ACTION_REGISTRY["assets"]["create_character_bible"], character_request)
    assert error is None
    bible = compiler.create_character_bible(payload)
    recipe_payload, error = facade._validate_request(facade.CREATIVE_MEDIA_ACTION_REGISTRY["plan"]["compile_recipe"], {
        "modality": "video", "prompt": "Subject turns", "characterBibleIds": [bible["characterBibleId"]],
    })
    assert error is None
    prompt = compiler.compile_recipe(recipe_payload)["providerPrompts"]["video_generic"]
    for value in ["asymmetric collar", "slow low voice", "blue case", "no earrings", "observable texture"]:
        assert value in prompt


@pytest.mark.parametrize("field,limit", [("prompt", 5000), ("negative_prompt", 500)])
def test_wan_rejects_input_that_provider_would_silently_truncate(field, limit):
    args = {"model": "wan2.7-r2v", "prompt": "A quiet station", "operation_kind": "video.reference_to_video",
            "reference_image_urls": ["https://example.test/a.png"]}
    args[field] = "甲" * limit
    _build_dashscope_video_payload(**args)
    args[field] += "尾部硬约束"
    with pytest.raises(ValueError, match="must not exceed"):
        _build_dashscope_video_payload(**args)


def test_seedance_wire_capture_preserves_long_tail_dialogue_and_all_references(monkeypatch, tmp_path):
    captured = {}
    binding = {"providerId": "synthetic", "providerModelId": "doubao-seedance-2-0-260128",
               "providerMeta": {"base_url": "https://example.test/api/v3", "api_key": "synthetic-only"}}
    monkeypatch.setattr(creative_media_runtime, "_configured_endpoint_binding", lambda *a, **k: binding)
    monkeypatch.setattr(creative_media_runtime, "_save_job", lambda job: job)

    async def capture(method, url, **kwargs):
        captured.update(deepcopy(kwargs["json"]))
        return {"id": "synthetic-task"}

    monkeypatch.setattr(creative_media_runtime, "_request_json", capture)
    prefix = "0–4秒：甲与乙在车站并肩停步。Image 1定义甲，Image 2定义乙，Video 1只供动作，Audio 1只供环境声。\n"
    details = "主体身份不交换；衣料保留织纹，地面潮湿反光；固定50mm中景，侧面漫射光，背景保持车站。\n" * 85
    tail = "10–15秒：乙右手握蓝色手提箱，甲说：“下一站见。”无字幕；最后一帧不得裁去箱子。"
    request = {"adapter": "volcengine_ark", "providerId": "synthetic", "modelId": binding["providerModelId"],
               "operationKind": "video.reference_to_video", "prompt": prefix + details + tail,
               "negativePrompt": "不加耳饰；不交换角色；不加画面文字", "durationSeconds": 15,
               "referenceImageUrls": ["https://example.test/a.png", "https://example.test/b.png"],
               "referenceVideoUrls": ["https://example.test/action.mp4"],
               "referenceAudioUrls": ["https://example.test/ambient.wav"]}
    job = asyncio.run(creative_media_runtime._create_video_job(request))
    assert job["status"] != "failed", job.get("error")
    wire_prompt = captured["content"][0]["text"]
    assert request["prompt"] in wire_prompt and tail in wire_prompt
    assert request["negativePrompt"] in wire_prompt
    assert [item[item["type"]]["url"] for item in captured["content"][1:]] == [
        *request["referenceImageUrls"], *request["referenceVideoUrls"], *request["referenceAudioUrls"],
    ]
    assert job["providerRequestHash"] == hashlib.sha256(json.dumps(captured, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    (tmp_path / "synthetic-provider-input.json").write_text(json.dumps({
        "evidenceLevel": "intercepted_http_boundary_not_live_provider", "request": request,
        "wire": captured, "wirePromptSha256": hashlib.sha256(wire_prompt.encode()).hexdigest(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def test_minimax_length_validation_keeps_short_prompt_and_rejects_long_complete_input():
    args = {"model": "MiniMax-Hailuo-2.3", "operation_kind": "video.text_to_video", "prompt": "甲" * 2000}
    assert _build_minimax_video_payload(**args)["prompt"] == args["prompt"]
    with pytest.raises(ValueError, match="2000"):
        _build_minimax_video_payload(**{**args, "prompt": args["prompt"] + "尾"})


@pytest.mark.parametrize("adapter,model,operation", [
    ("volcengine_ark", "doubao-seedance-2-0-260128", "video.first_last_frame"),
    ("dashscope", "wan2.7-r2v", "video.reference_to_video"),
])
def test_provider_submit_keeps_frame_roles_and_subject_voice_bindings(monkeypatch, tmp_path, adapter, model, operation):
    captured = {}
    binding = {"providerId": "synthetic", "providerModelId": model,
               "providerMeta": {"base_url": "https://example.test/api/v3", "api_key": "synthetic-only"}}
    monkeypatch.setattr(creative_media_runtime, "_configured_endpoint_binding", lambda *a, **k: binding)
    monkeypatch.setattr(creative_media_runtime, "_save_job", lambda job: job)

    async def capture(method, url, **kwargs):
        captured.update(deepcopy(kwargs["json"]))
        return {"id": "synthetic-task", "output": {"task_id": "synthetic-task"}}

    monkeypatch.setattr(creative_media_runtime, "_request_json", capture)
    request = {"adapter": adapter, "providerId": "synthetic", "modelId": model, "operationKind": operation,
               "prompt": 'Image 1说：“你好。”Image 2说：“再见。”不显示字幕。', "durationSeconds": 8}
    if adapter == "volcengine_ark":
        request.update({"firstFrame": "https://example.test/loop.png", "lastFrame": "https://example.test/loop.png"})
    else:
        request.update({"referenceMedia": [
            {"type": "reference_image", "url": "https://example.test/a.png", "reference_voice": "https://example.test/a.wav"},
            {"type": "reference_image", "url": "https://example.test/b.png", "reference_voice": "https://example.test/b.wav"},
        ], "negativePrompt": "不加字幕", "promptExtend": False})
    job = asyncio.run(creative_media_runtime._create_video_job(request))
    assert job["status"] != "failed", job.get("error")
    if adapter == "volcengine_ark":
        assert captured["content"][1:] == [
            {"type": "image_url", "image_url": {"url": request["firstFrame"]}, "role": "first_frame"},
            {"type": "image_url", "image_url": {"url": request["lastFrame"]}, "role": "last_frame"},
        ]
    else:
        assert captured["input"]["media"] == request["referenceMedia"]
        assert captured["input"]["negative_prompt"] == request["negativePrompt"]
        assert captured["parameters"]["prompt_extend"] is False
        assert captured["input"]["prompt"] == request["prompt"]
    (tmp_path / f"{adapter}-provider-input.json").write_text(json.dumps({
        "evidenceLevel": "intercepted_http_boundary_not_live_provider", "request": request, "wire": captured,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("method", ["_run_openai_image_job", "_run_openai_image_edit_job"])
def test_image_adapter_cannot_ignore_unsupported_reference_inputs(method):
    with pytest.raises(ValueError, match="reference"):
        asyncio.run(getattr(creative_media_runtime, method)({}, {"imageUrls": ["https://example.test/a.png"]}))


def test_reference_only_request_does_not_drop_exclusions():
    prompt, policy = creative_media_runtime._prepare_prompt_for_provider({
        "adapter": "volcengine_ark", "operationKind": "video.reference_to_video",
        "referenceImageUrl": "https://example.test/a.png", "negativePrompt": "不要出现字幕",
    }, modality="video")
    assert "不要出现字幕" in prompt
    assert policy["negativePromptMode"] == "inline_constraints"


@pytest.mark.parametrize("prompt", [
    '画钢铁侠的线稿，不改变用户指定的主体；标题写“钢铁侠”。',
    "不要模仿声线，使用原创旁白，保持静态构图。",
    "本人已授权使用我的真人肖像和声音，请保留我的长发和绿色衬衫。",
    "对比蜘蛛侠与蝙蝠侠的剪影；不要把品牌名称当成违法判断。",
])
def test_reference_observation_never_rewrites_subject_or_implies_illegality(prompt):
    policy = prepare_provider_prompt_policy(prompt, modality="video")
    assert policy["translatedPrompt"] == prompt
    observation = policy["safetyTransform"]
    assert observation["applied"] is False
    assert observation["detected"] is True
    assert all(event["action"] == "observe_only" for event in observation["events"])
    assert all(event["assessment"] == "not_determined" for event in observation["events"])
    assert "replacementSummary" not in json.dumps(observation)


def test_reference_observation_persistence_and_tool_summary_are_redacted(monkeypatch):
    from core.tools.native.creative_media import _creative_media_safety_event_summary

    saved = {}
    monkeypatch.setattr(creative_media_runtime, "_store_is_active", lambda: False)
    monkeypatch.setattr(creative_media_runtime, "_read_versioned_store", lambda *a: {"events": {}})
    monkeypatch.setattr(creative_media_runtime, "_write_versioned_store", lambda name, key, rows: saved.update(rows))
    prompt = "PRIVATE_SYNTHETIC_DETAILS 钢铁侠；不要模仿声线。"
    transform = prepare_provider_prompt_policy(prompt, modality="image")["safetyTransform"]
    creative_media_runtime._record_safety_event(source="job_create", job={"jobId": "synthetic"}, transform=transform)
    event = next(iter(saved.values()))
    assert event["applied"] is False and event["detected"] is True
    assert event["rawPromptHash"] == hashlib.sha256(prompt.encode()).hexdigest()
    assert "PRIVATE_SYNTHETIC_DETAILS" not in json.dumps(event)
    summary = _creative_media_safety_event_summary(event)
    assert summary["applied"] is False and summary["detected"] is True
    assert summary["action"] == "reference_observation"
    assert "PRIVATE_SYNTHETIC_DETAILS" not in json.dumps(summary)


def test_policy_rejection_retry_preserves_user_subject_and_explicit_revision(monkeypatch):
    original = {"jobId": "rejected", "modality": "image", "operationKind": "image.generate",
                "policyRejectReason": "provider content policy rejected request",
                "request": {"prompt": "钢铁侠线稿", "negativePrompt": "不加金色",
                            "promptPolicy": {"rawUserRequest": "钢铁侠线稿"}}}
    monkeypatch.setattr(creative_media_runtime, "get_job", lambda *a, **k: deepcopy(original))

    async def capture(request):
        return request

    monkeypatch.setattr(creative_media_runtime, "create_job", capture)
    retried = asyncio.run(creative_media_runtime.retry_job("rejected"))
    assert retried["prompt"] == "钢铁侠线稿"
    revised = asyncio.run(creative_media_runtime.retry_job("rejected", {"prompt": "用户明确改为原创机器人线稿"}))
    assert revised["prompt"] == "用户明确改为原创机器人线稿"


def test_template_vocabulary_cannot_override_deliberate_transformation(compiler):
    recipe = compiler.compile_recipe({"modality": "video", "prompt": "产品展示：让耳机外形变形成流体，故意动作过载和突然切镜。"})
    prompt = recipe["providerPrompts"]["video_generic"]
    assert "产品变形" not in recipe["providerNeutralRecipe"]["avoid"]
    assert "动作过载" not in recipe["providerNeutralRecipe"]["avoid"]
    assert "Avoid:" not in prompt


def test_string_artifact_reference_is_one_id(monkeypatch):
    seen = []

    def resolve(artifact_id, **kwargs):
        seen.append(artifact_id)
        return SimpleNamespace(record={"kind": "image"})

    monkeypatch.setattr(creative_media_runtime, "_authorized_artifact_resource", resolve)
    monkeypatch.setattr(creative_media_runtime, "_artifact_provider_transport_url", lambda artifact_id: "https://example.test/a.png")
    request = {"sessionId": "synthetic", "referenceAssetIds": "artifact-one"}
    assert len(creative_media_runtime._image_urls_from_request(request)) == 1
    assert len(creative_media_runtime._multimodal_reference_inputs(request)) == 1
    assert seen == ["artifact-one", "artifact-one"]


def test_image_composition_request_reaches_compiler_through_real_facade(monkeypatch, compiler, tmp_path):
    scope = {"sessionId": "synthetic", "workspacePath": str(tmp_path)}
    monkeypatch.setattr(facade, "get_runtime_context", lambda: {"session_id": "synthetic", "workspace_path": str(tmp_path)})
    monkeypatch.setattr(facade.creative_media_resource_authority, "authorize_request_resources", lambda request: [])
    monkeypatch.setattr(creative_media_runtime, "_canonical_owner_scope", lambda *a, **k: scope)
    monkeypatch.setattr(facade, "_record_internal_detail", lambda *a, **k: "fixture://composition")
    result = json.loads(facade.creative_media_plan.invoke({
        "action": "compile_recipe",
        "request": {"modality": "image", "prompt": "A ceramic teapot", "compositionStructure": ["subject on left third", "empty space on right"],
                    "style": "flat ink illustration", "negativePrompt": "no photorealism"},
    }))
    assert result["ok"] is True, result
    recipe = compiler.list_recipes()[0]
    prompt = recipe["providerPrompts"]["openai_images"]
    assert "subject on left third; empty space on right" in prompt
    assert "flat ink illustration" in prompt
    assert "no photorealism" in prompt


def test_wan_multiple_global_audio_inputs_cannot_reach_first_item_selection():
    with pytest.raises(ValueError, match="referenceMedia"):
        creative_media_runtime._dashscope_references_from_request({
            "referenceImageUrls": ["https://example.test/a.png", "https://example.test/b.png"],
            "referenceAudioUrls": ["https://example.test/a.wav", "https://example.test/b.wav"],
        })


def test_seedance_mixed_generic_and_explicit_frames_cannot_swap_roles():
    with pytest.raises(ValueError, match="Do not mix"):
        creative_media_runtime._seedance_references_from_request({
            "operationKind": "video.first_last_frame", "imageUrl": "https://example.test/a.png",
            "firstFrame": "https://example.test/b.png",
        })
    with pytest.raises(ValueError, match="lastFrame cannot"):
        creative_media_runtime._seedance_references_from_request({
            "operationKind": "video.image_to_video", "lastFrame": "https://example.test/end.png",
        })


@pytest.mark.parametrize("duration", [90, 601, 7.5])
def test_video_recipe_never_clamps_explicit_duration(compiler, duration):
    recipe = compiler.compile_recipe({"modality": "video", "durationSeconds": duration,
                                      "prompt": f"完整{duration}秒时间轴；不缩短，不改变尾声。"})
    assert recipe["controls"]["durationSeconds"] == duration
    assert recipe["hardRequirements"]["durationSeconds"] == duration
    assert recipe["providerNeutralRecipe"]["timedSegments"][0]["end"] == duration
    assert f"{duration}-second video" in recipe["providerPrompts"]["video_generic"]


@pytest.mark.parametrize("invalid", [0, -1, float("inf"), True])
def test_invalid_recipe_duration_is_explicitly_rejected(compiler, invalid):
    with pytest.raises(ValueError, match="durationSeconds"):
        compiler.compile_recipe({"modality": "video", "durationSeconds": invalid, "prompt": "A scene"})


@pytest.mark.parametrize("invalid", ["", None, "file:///private.png", 7])
def test_empty_or_invalid_reference_slot_cannot_shift_later_subject(invalid):
    with pytest.raises(ValueError):
        creative_media_runtime._seedance_references_from_request({
            "referenceImageUrls": [invalid, "https://example.test/b.png"],
        })
    assert creative_media_runtime._request_url_list({"imageUrl": "", "imageUrls": []}, "imageUrl", "imageUrls") == []


def test_integer_second_adapters_reject_fractional_duration_without_rounding():
    with pytest.raises(ValueError, match="integer"):
        _build_volcengine_video_payload(model="doubao-seedance-2-0-260128", prompt="A scene",
                                        operation_kind="video.text_to_video", ratio="16:9", resolution="720p", duration=7.5)
    with pytest.raises(ValueError, match="integer"):
        _build_dashscope_video_payload(model="wan2.7-r2v", prompt="Image 1", operation_kind="video.reference_to_video",
                                       reference_image_urls=["https://example.test/a.png"], duration=7.5)
    with pytest.raises(ValueError, match="integer"):
        _build_minimax_video_payload(model="MiniMax-H3", prompt="A scene", operation_kind="video.text_to_video", duration_seconds=7.5)
    with pytest.raises(ValueError, match="between 2 and 10"):
        _build_dashscope_video_payload(model="wan2.7-r2v", prompt="Video 1", operation_kind="video.reference_to_video",
                                       reference_video_urls=["https://example.test/a.mp4"], duration=15)


@pytest.mark.parametrize("adapter,model", [("volcengine_ark", "doubao-seedance-2-0-260128"), ("dashscope", "wan2.7-t2v")])
def test_submit_rejects_fractional_duration_before_any_provider_request(monkeypatch, adapter, model):
    binding = {"providerId": "synthetic", "providerModelId": model,
               "providerMeta": {"base_url": "https://example.test/api/v3", "api_key": "synthetic-only"}}
    monkeypatch.setattr(creative_media_runtime, "_configured_endpoint_binding", lambda *a, **k: binding)
    monkeypatch.setattr(creative_media_runtime, "_save_job", lambda job: job)

    async def forbidden(*args, **kwargs):
        raise AssertionError("invalid duration reached provider submission")

    monkeypatch.setattr(creative_media_runtime, "_request_json", forbidden)
    job = asyncio.run(creative_media_runtime._create_video_job({
        "adapter": adapter, "providerId": "synthetic", "modelId": model, "operationKind": "video.text_to_video",
        "prompt": "A scene", "durationSeconds": 7.5,
    }))
    assert job["status"] == "failed"
    assert "integer" in job["error"]
