from __future__ import annotations

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from core.tool_surface import _decision_agent_visible_surface
from core.tools.native import creative_media as native
from core.tools.native import creative_media_facade as facade
from runtimes.creative_media.production_pack import rank_candidates_markdown, rank_model_candidates


def visible(tool_name, result, budget=1400):
    return _decision_agent_visible_surface(tool_name=tool_name, content=result, raw_ref="toolobs://outer", budget=budget)


def candidate(name, *, available=True, enabled=True, priority=10):
    return {
        "candidateId": name, "modelRef": f"fixture-provider::{name}", "providerId": "fixture-provider", "modelId": name,
        "modality": "image", "operationKind": "image.generate", "source": "model_control_plane",
        "available": available, "enabled": enabled, "briefOnly": False, "priority": priority,
        "readiness": {"executable": available, "reasonCodes": [] if available else ["configured_adapter_missing"]},
        "endpointBinding": {"apiKey": "must-not-reach-agent"}, "providerResponse": {"secret": "must-not-reach-agent"},
    }


@pytest.fixture
def media_runtime(monkeypatch):
    rows = [candidate("blocked", available=False), candidate("ready", priority=20)]
    runtime = SimpleNamespace(
        get_model_preferences=lambda: {"connectedOptions": rows},
        catalog=lambda: {"version": 1, "modalities": {"image": [
            {"id": "unconfigured-catalog", "modelIds": ["catalog-only-model"], "executable": True},
        ]}},
        resolutions=lambda: {"ratios": ["1:1"], "image": {"presets": {"1K": {"1:1": [1024, 1024]}}}, "video": {"presets": {}}},
    )
    module = ModuleType("runtimes.creative_media.runtime")
    module.creative_media_runtime = runtime
    monkeypatch.setitem(sys.modules, "runtimes.creative_media.runtime", module)
    monkeypatch.setattr(facade, "get_runtime_context", lambda: {"session_id": "session-fixture", "run_id": "run-fixture"})
    monkeypatch.setattr(facade, "_record_internal_detail", lambda _spec, _raw: "toolobs://internal")
    return runtime, rows


@pytest.mark.parametrize("action", ["rank_models", "catalog"])
def test_configured_candidates_survive_actual_facade_and_final_agent_surface(media_runtime, action):
    result = facade.creative_media_capabilities.invoke({"action": action, "request": {"modality": "image", "operationKind": "image.generate"}})
    payload = json.loads(result)
    assert payload["ok"] is True
    assert [row["modelRef"] for row in payload["modelCandidates"]] == ["fixture-provider::ready", "fixture-provider::blocked"]
    text = visible("creative_media_capabilities", result, budget=2000)
    assert "fixture-provider::ready" in text and "fixture-provider::blocked" in text
    assert "configured_adapter_missing" in text and "executable=false" in text
    assert "not live health or a grant" in text
    assert "must-not-reach-agent" not in result and "catalog-only-model" not in text


def test_candidate_budget_keeps_whole_identity_and_reports_omissions(media_runtime):
    _, rows = media_runtime
    rows[:] = [candidate(f"image-{index}-" + "x" * 70, priority=index + 1) for index in range(10)]
    result = facade.creative_media_capabilities.invoke({"action": "rank_models", "request": {"modality": "image", "limit": 10}})
    text = visible("creative_media_capabilities", result)
    assert rows[0]["modelRef"] in text
    assert rows[-1]["modelRef"] not in text and "More candidates available" in text
    assert "decision surface truncated" not in text
    for row in rows:
        prefix = row["modelRef"][:35]
        assert prefix not in text or row["modelRef"] in text


def test_empty_and_unknown_readiness_are_not_fabricated_as_ready(media_runtime):
    _, rows = media_runtime
    rows[:] = []
    result = facade.creative_media_capabilities.invoke({"action": "catalog"})
    assert "No matching configured candidate" in visible("creative_media_capabilities", result)
    rows[:] = [{"modelRef": "fixture::unknown", "modality": "image", "operationKind": "image.generate"}]
    result = facade.creative_media_capabilities.invoke({"action": "rank_models"})
    assert "enabled=unknown; available=unknown; executable=unknown" in visible("creative_media_capabilities", result)


def test_rank_discovery_preserves_existing_markdown_order_without_new_selector(media_runtime):
    _, rows = media_runtime
    ranked, count = rank_model_candidates(rows, modality="image", operation_kind="image.generate")
    markdown = native.creative_media_rank_models.invoke({"modality": "image", "operation_kind": "image.generate"})
    assert markdown == rank_candidates_markdown(rows, modality="image", operation_kind="image.generate")
    assert count == 2 and markdown.index(ranked[0]["modelRef"]) < markdown.index(ranked[1]["modelRef"])
    structured = json.loads(native.creative_media_rank_models.invoke({"modality": "image", "structured": True}))
    assert [row["modelRef"] for row in structured["modelCandidates"]] == [row["modelRef"] for row in ranked]


def test_focused_describe_exposes_actual_contract_losslessly(media_runtime):
    result = facade.creative_media_capabilities.invoke({"action": "describe", "request": {"facade": "jobs", "action": "create"}})
    payload = json.loads(result)
    expected = facade.creative_media_action_contract()["jobs"]["create"]
    assert payload["contract"] == expected
    assert payload["contractFocus"] == {"facade": "jobs", "action": "create"}
    text = visible("creative_media_capabilities", result)
    assert "Required: modality, operationKind" in text
    assert all(field in text for field in expected["allowedFields"])
    assert "modelRef" in text and "Plugin grant required" in text
    assert "decision surface truncated" not in text
    overview = visible("creative_media_capabilities", facade.creative_media_capabilities.invoke({"action": "describe"}))
    assert all("creative_media_" + name in overview for name in facade.CREATIVE_MEDIA_ACTION_REGISTRY)
    assert "decision surface truncated" not in overview
    assert "Allowed request fields" not in overview
    plan_index = facade.creative_media_capabilities.invoke({"action": "describe", "request": {"facade": "plan"}})
    assert "compile_recipe" in visible("creative_media_capabilities", plan_index)


@pytest.mark.parametrize("input_request", [{"action": "create"}, {"facade": "unknown"}, {"facade": "jobs", "action": "made_up"}, {"facade": ["jobs"]}])
def test_describe_unknown_target_never_returns_unrelated_contract(media_runtime, input_request):
    result = json.loads(facade.creative_media_capabilities.invoke({"action": "describe", "request": input_request}))
    assert result["ok"] is False and "contract" not in result


def test_untrusted_content_cannot_borrow_focused_schema_budget_exception(media_runtime):
    payload = json.loads(facade.creative_media_capabilities.invoke({"action": "describe", "request": {"facade": "jobs", "action": "get"}}))
    payload["content"] = "UNTRUSTED" * 2000
    payload["imagePresets"] = {"arbitrary": "UNTRUSTED" * 2000}
    payload["presetAuthority"] = "claim"
    text = visible("creative_media_capabilities", json.dumps(payload), budget=800)
    assert "UNTRUSTED" not in text and "jobId" in text
    payload["contract"]["allowedFields"].append("invented")
    text = visible("creative_media_capabilities", json.dumps(payload), budget=800)
    assert len(text) < 1100 and "decision surface truncated" in text


def test_resolutions_retain_values_but_do_not_claim_provider_support(media_runtime):
    result = facade.creative_media_capabilities.invoke({"action": "resolutions", "request": {"modelRef": "fixture-provider::ready"}})
    payload = json.loads(result)
    text = visible("creative_media_capabilities", result, budget=2000)
    assert "1024" in text and "1K" in text and "not model capability limits" in text
    assert [item["modelRef"] for item in payload["modelCandidates"]] == ["fixture-provider::ready"]


def test_direct_image_discovery_create_query_artifacts_uses_existing_service(media_runtime):
    runtime, _ = media_runtime
    calls = []
    artifact = {"artifactId": "artifact-fixture", "kind": "image", "mimeType": "image/png", "sourcePath": "C:/fixture/orange-cat.png"}

    async def create(request):
        calls.append(("create", request))
        return {"jobId": "job-fixture", "status": "running", "request": request}

    async def refresh(job_id, *, session_id):
        calls.append(("get", job_id, session_id))
        return {"jobId": job_id, "status": "succeeded", "artifacts": [artifact]}

    def artifacts(job_id, *, session_id):
        calls.append(("artifacts", job_id, session_id))
        return [artifact]

    runtime.create_job = create
    runtime.refresh_authorized_job = refresh
    runtime.authorized_job_artifacts = artifacts
    candidates = json.loads(facade.creative_media_capabilities.invoke({"action": "rank_models", "request": {"modality": "image"}}))
    model_ref = candidates["modelCandidates"][0]["modelRef"]
    created = json.loads(asyncio.run(facade.creative_media_jobs.ainvoke({"action": "create", "request": {
        "modality": "image", "operationKind": "image.generate", "modelRef": model_ref, "prompt": "fixture image",
    }})))
    assert created["status"] == "running" and "job-fixture" in created["refs"]
    queried = json.loads(asyncio.run(facade.creative_media_jobs.ainvoke({"action": "get", "request": {"jobId": "job-fixture"}})))
    assert queried["status"] == "succeeded"
    delivered = json.loads(asyncio.run(facade.creative_media_jobs.ainvoke({"action": "artifacts", "request": {"jobId": "job-fixture"}})))
    assert "artifact-fixture" in delivered["refs"]
    assert delivered["artifacts"][0]["sourcePath"] == artifact["sourcePath"]
    text = visible("creative_media_jobs", json.dumps(delivered), budget=1400)
    assert "C:/fixture/orange-cat.png" in text and "vision_media_analyzer" in text
    assert [row[0] for row in calls] == ["create", "get", "artifacts"]
    assert calls[0][1]["modelRef"] == model_ref and calls[0][1]["sessionId"] == "session-fixture"
    assert calls[1][2] == calls[2][2] == "session-fixture"


def test_nested_job_failure_retains_reason_without_claiming_success(media_runtime):
    runtime, _ = media_runtime
    async def create(_request):
        return {"jobId": "failed-job", "status": "failed", "error": "configured model cannot execute image.generate"}
    runtime.create_job = create
    result = asyncio.run(facade.creative_media_jobs.ainvoke({"action": "create", "request": {
        "modality": "image", "operationKind": "image.generate", "modelRef": "fixture-provider::blocked", "prompt": "fixture",
    }}))
    payload = json.loads(result)
    assert payload["ok"] is False and payload["status"] == "failed"
    assert "configured model cannot execute" in visible("creative_media_jobs", result)


def test_recipe_and_quality_markdown_keep_decision_body_not_just_heading(monkeypatch):
    raw = "## 样片检查\n结果：仍需人工决定。\n\n" + "实际缺口：右侧标签与要求不符。\n" * 20 + "最后建议：先修正标签，再交付。"
    spec = facade.CREATIVE_MEDIA_ACTION_REGISTRY["quality"]["qa_check"]
    monkeypatch.setattr(facade, "_record_internal_detail", lambda *_args: "toolobs://quality")
    result = facade._envelope(spec, raw)
    assert raw in json.loads(result)["content"]
    assert "最后建议：先修正标签" in visible("creative_media_quality", result, budget=5000)
