import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from core.runtime_tool_access import RUNTIME_TOOL_GROUPS, filter_visible_tools_for_actor
from core.supervisor_tool_policy import build_supervisor_tool_policy_snapshot
from core.tool_surface import _render_creative_media_surface
from core.tools.native import creative_media_facade as facade
from core.tools.native.creative_media import _creative_media_quality_job_summary
from core.tools.native.creative_media_facade import (
    CREATIVE_MEDIA_ACTION_REGISTRY,
    CREATIVE_MEDIA_FACADE_TOOLS,
    creative_media_jobs,
    creative_media_assets,
    creative_media_capabilities,
    creative_media_edit,
    creative_media_plan,
    creative_media_quality,
)
from core.tools.native.registry import native_tool_family_for_name
from core.tools.native.runtime import _RUNTIME_ROUTE_DEFAULT_GROUPS
from core.tools.native.runtime import _required_runtime_access_from_spec_bundle


EXPECTED = {
    "creative_media_capabilities",
    "creative_media_plan",
    "creative_media_assets",
    "creative_media_jobs",
    "creative_media_edit",
    "creative_media_quality",
}
ENGINE_ROOT = Path(__file__).resolve().parents[2]


def test_creative_media_agent_surface_is_exactly_six_facades() -> None:
    names = {tool.name for tool in CREATIVE_MEDIA_FACADE_TOOLS}
    assert names == EXPECTED
    assert set(RUNTIME_TOOL_GROUPS["creative_media.core"]["toolNames"]) == EXPECTED
    assert all(native_tool_family_for_name(name) == "creative_media" for name in EXPECTED)


def test_supervisor_does_not_receive_creative_media_facades_by_default() -> None:
    assert _RUNTIME_ROUTE_DEFAULT_GROUPS["creative_media"] == []
    default_names = {
        item["name"]
        for item in build_supervisor_tool_policy_snapshot(None)["lockedNativeTools"]
    }
    assert not (default_names & EXPECTED)


def test_creative_runtime_tool_matrix_is_bound_agent_only() -> None:
    tools = [*CREATIVE_MEDIA_FACADE_TOOLS, SimpleNamespace(name="read_native_file")]

    creative_child = {
        tool.name
        for tool in filter_visible_tools_for_actor(
            tools,
            actor="subagent",
            runtime_access=["creative_media.core"],
        )
    }
    research_child = {
        tool.name
        for tool in filter_visible_tools_for_actor(
            tools,
            actor="subagent",
            runtime_access=["research.core"],
        )
    }
    unbound_grandchild = {
        tool.name
        for tool in filter_visible_tools_for_actor(
            tools,
            actor="subagent",
            runtime_access=[],
        )
    }

    assert creative_child == EXPECTED | {"read_native_file"}
    assert research_child == {"read_native_file"}
    assert unbound_grandchild == {"read_native_file"}


def test_every_facade_action_has_a_strict_contract() -> None:
    assert set(CREATIVE_MEDIA_ACTION_REGISTRY) == {
        "capabilities",
        "plan",
        "assets",
        "jobs",
        "edit",
        "quality",
    }
    for facade_name, actions in CREATIVE_MEDIA_ACTION_REGISTRY.items():
        assert actions, facade_name
        for action, spec in actions.items():
            assert spec.facade == facade_name
            assert spec.action == action
            assert spec.handler_name
            assert spec.output_kind
            assert spec.required_fields <= spec.allowed_fields
            assert all(group <= spec.allowed_fields for group in spec.any_of_fields)


def test_every_facade_action_dispatches_through_the_registry(monkeypatch) -> None:
    class FakeHandler:
        def invoke(self, _payload):
            return json.dumps({"ok": True, "status": "succeeded", "summary": "registry dispatch ok", "artifactId": "artifact-fixture"})

        async def ainvoke(self, _payload):
            return self.invoke(_payload)

    tools = {
        "capabilities": creative_media_capabilities,
        "plan": creative_media_plan,
        "assets": creative_media_assets,
        "jobs": creative_media_jobs,
        "edit": creative_media_edit,
        "quality": creative_media_quality,
    }

    def sample(name: str):
        if name in {"artifactIds", "assetIds", "artifacts", "layers", "media", "referenceAssetIds", "sampleArtifactRefs", "videoAssetIds"}:
            return [{"artifactId": "artifact-fixture"}] if name in {"artifacts", "media", "sampleArtifactRefs"} else ["artifact-fixture"]
        if name == "canvas":
            return {"width": 320, "height": 180, "background": "transparent"}
        if name == "modality":
            return "image"
        if name == "operationKind":
            return "image.generate"
        if name == "workOrderKind":
            return "simple_asset"
        if name == "name":
            return "fixture"
        if name.endswith("Id"):
            return f"{name[:-2].lower()}-fixture"
        return "fixture"

    monkeypatch.setattr(facade, "_resolve_handler", lambda _spec: FakeHandler())
    monkeypatch.setattr(facade, "_record_internal_detail", lambda _spec, _raw: "toolobs://registry-dispatch")
    monkeypatch.setattr(
        facade,
        "_contract_result",
        lambda: json.dumps({"ok": True, "status": "ready", "summary": "registry dispatch ok"}),
    )
    monkeypatch.setattr(
        facade,
        "_plugin_status_result",
        lambda: json.dumps({"ok": True, "status": "ready", "summary": "registry dispatch ok"}),
    )

    for facade_name, actions in CREATIVE_MEDIA_ACTION_REGISTRY.items():
        for action, spec in actions.items():
            request = {name: sample(name) for name in spec.required_fields}
            for group in spec.any_of_fields:
                selected = sorted(group)[0]
                request.setdefault(selected, sample(selected))
            invocation = {"action": action, "request": request}
            if facade_name == "jobs":
                result = json.loads(asyncio.run(tools[facade_name].ainvoke(invocation)))
            else:
                result = json.loads(tools[facade_name].invoke(invocation))
            assert result["ok"] is True, (facade_name, action, result)
            assert result["facade"] == facade_name
            assert result["action"] == action
            assert result["status"] in {"ready", "succeeded"}
            assert result["detailRef"] == "toolobs://registry-dispatch"


def test_unknown_and_missing_action_fields_are_rejected_before_handler(monkeypatch) -> None:
    invoked = False

    def _unexpected_handler(_spec):
        nonlocal invoked
        invoked = True
        raise AssertionError("handler must not be resolved for an invalid request")

    monkeypatch.setattr(facade, "_resolve_handler", _unexpected_handler)

    unknown = json.loads(
        creative_media_plan.invoke(
            {
                "action": "compile_recipe",
                "request": {"modality": "image", "prompt": "hero", "sessionId": "spoofed"},
            }
        )
    )
    missing = json.loads(
        asyncio.run(
            creative_media_jobs.ainvoke(
                {"action": "create", "request": {"modality": "image", "prompt": "hero"}}
            )
        )
    )

    assert unknown["error"]["code"] == "unknown_fields"
    assert missing["error"]["code"] == "missing_required_fields"
    assert invoked is False


def test_facade_normalizes_internal_output_and_keeps_raw_detail_out_of_agent_surface(monkeypatch) -> None:
    class FakeHandler:
        async def ainvoke(self, _payload):
            return json.dumps(
                {
                    "ok": True,
                    "job": {
                        "jobId": "cm_job_123",
                        "status": "running",
                        "providerResponse": {"secretNoise": "must stay behind detailRef"},
                    },
                    "recommendedNextAction": "Poll the job.",
                }
            )

    monkeypatch.setattr(facade, "_resolve_handler", lambda _spec: FakeHandler())
    monkeypatch.setattr(facade, "_record_internal_detail", lambda _spec, _raw: "toolobs://internal-creative")

    result = json.loads(
        asyncio.run(
            creative_media_jobs.ainvoke(
                {
                    "action": "create",
                    "request": {"modality": "image", "operationKind": "image.generate", "prompt": "hero"},
                }
            )
        )
    )

    assert result == {
        "ok": True,
        "facade": "jobs",
        "action": "create",
        "status": "running",
        "summary": "job | cm_job_123 | running",
        "refs": ["cm_job_123"],
        "detailRef": "toolobs://internal-creative",
        "nextAction": "Poll the job.",
    }
    visible = _render_creative_media_surface("creative_media_jobs", result, "toolobs://outer-envelope")
    assert visible is not None
    assert "cm_job_123" in visible
    assert "tool_observation_detail(raw_ref='toolobs://internal-creative')" in visible
    assert "secretNoise" not in visible


def test_facade_normalizes_internal_failure(monkeypatch) -> None:
    class FakeHandler:
        def invoke(self, _payload):
            return json.dumps(
                {
                    "ok": False,
                    "status": "failed",
                    "error": {"code": "provider_failed", "message": "provider unavailable"},
                }
            )

    monkeypatch.setattr(facade, "_resolve_handler", lambda _spec: FakeHandler())
    monkeypatch.setattr(facade, "_record_internal_detail", lambda _spec, _raw: "toolobs://provider-failure")

    result = json.loads(
        asyncio.run(creative_media_jobs.ainvoke({"action": "get", "request": {"jobId": "job-fixture"}}))
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == {"code": "provider_failed", "message": "provider unavailable"}
    assert result["detailRef"] == "toolobs://provider-failure"


def test_quality_agent_surface_keeps_evidence_without_runtime_noise() -> None:
    summary = _creative_media_quality_job_summary(
        {
            "qualityJobId": "cm_quality_internal",
            "jobId": "cm_job_internal",
            "status": "review_required",
            "qualityProfile": "product_packshot",
            "summary": "复杂背景需要图像分析增强包。",
            "requiredFeaturePackId": "creative_media_image_analysis",
            "retryRecommendation": {"action": "manual_review"},
            "providerResponse": {"raw": "must not leak"},
        }
    )

    assert summary["detailRef"] == "cm_quality_internal"
    assert summary["status"] == "review_required"
    assert summary["nextAction"] == "manual_review"
    assert "jobId" not in summary
    assert "providerResponse" not in summary


def test_supplier_adapter_never_falls_back_without_plugin_grant(monkeypatch) -> None:
    native_handler_resolved = False

    def _unexpected_native_handler(_spec):
        nonlocal native_handler_resolved
        native_handler_resolved = True
        raise AssertionError("supplier adapter must not fall back to the base provider tool")

    monkeypatch.setattr(facade, "_resolve_handler", _unexpected_native_handler)
    monkeypatch.setattr(facade, "_resolve_code_owned_provider_adapter", lambda _adapter_id: None)

    result = json.loads(
        asyncio.run(
            creative_media_jobs.ainvoke(
                {
                    "action": "create",
                    "request": {
                        "modality": "video",
                        "operationKind": "video.supplier_exclusive_edit",
                        "prompt": "edit the clip",
                        "providerAdapterId": "hyperframes.video",
                    },
                }
            )
        )
    )

    assert result["status"] == "blocked"
    assert result["error"]["code"] == "plugin_grant_required"
    assert native_handler_resolved is False


def test_engineering_spec_does_not_gain_creative_runtime_from_media_words() -> None:
    bundle = {
        "tasks": [
            {
                "runtimeLane": "engineering",
                "excerpt": "Build a page that displays image and video artifacts supplied by Creative Media.",
            }
        ]
    }

    groups = _required_runtime_access_from_spec_bundle(bundle, "engineering")

    assert groups == ["delegation.recursive"]
    assert "creative_media.core" not in groups


def test_active_agent_surfaces_do_not_reference_legacy_creative_tool_names() -> None:
    legacy_names = {
        "creative_media_create_job",
        "creative_media_get_job",
        "creative_media_job_artifacts",
        "creative_media_retry_job",
        "creative_media_rank_models",
        "creative_media_production_pack",
        "creative_media_reference_media_brief",
        "creative_media_sample_approval_packet",
        "creative_media_qa_check",
        "creative_media_alpha_inspect",
        "creative_media_psd_compose_template",
        "creative_media_psd_export_preview",
    }
    paths = [
        ENGINE_ROOT / "core/agents.py",
        ENGINE_ROOT / "core/delegated_agent_charter.py",
        ENGINE_ROOT / "core/runtime_episode_runner.py",
        ENGINE_ROOT / "core/tool_surface.py",
        ENGINE_ROOT / "graph/tool_routing.py",
        ENGINE_ROOT / "runtimes/chat/runtime.py",
        ENGINE_ROOT / "runtimes/creative_media/production_pack.py",
        ENGINE_ROOT / "runtimes/creative_media/tool_surface.py",
    ]

    residues = {
        str(path): sorted(name for name in legacy_names if name in path.read_text(encoding="utf-8"))
        for path in paths
    }

    assert not {path: names for path, names in residues.items() if names}


def test_native_tool_module_does_not_wildcard_export_legacy_creative_tools() -> None:
    source = (ENGINE_ROOT / "core/native_tools.py").read_text(encoding="utf-8")

    assert "from core.tools.native.creative_media import *" not in source
    assert "from core.tools.native.creative_media_psd import *" not in source
