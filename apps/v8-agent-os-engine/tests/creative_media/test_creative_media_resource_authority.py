from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import creative_media_resource_authority as authority_module
from core.tools.native import creative_media_facade as facade
from core.tools.native import creative_media_psd as psd_tools
from runtimes.creative_media import recipe as recipe_module


class _FakeDatabase:
    def __init__(self, *, artifacts: dict[str, dict], sources: dict[tuple[str, str], dict]) -> None:
        self.artifacts = artifacts
        self.sources = sources

    @staticmethod
    def get_session(session_id: str):
        return {"id": session_id} if session_id in {"session-a", "session-b"} else None

    def get_runtime_artifact(self, artifact_id: str):
        return self.artifacts.get(artifact_id)

    def get_session_source(self, *, session_id: str, source_id: str):
        return self.sources.get((session_id, source_id))


class _FakeWorkspaceMediaLibrary:
    def __init__(self, workspace_paths: dict[tuple[str, str], Path]) -> None:
        self.workspace_paths = workspace_paths

    def get_asset(self, *, session_id: str, asset_id: str) -> dict:
        path = self.workspace_paths.get((session_id, asset_id))
        if path is None:
            raise LookupError("asset unavailable")
        return {
            "assetId": asset_id,
            "sessionId": session_id,
            "workspaceId": f"workspace-{session_id[-1]}",
            "projectId": f"project-{session_id[-1]}",
            "workspaceRelativePath": path.name,
            "adoptedByCurrentSession": True,
        }

    def resolve_asset_path(self, *, session_id: str, asset_id: str, require_session_use: bool = False) -> Path:
        del require_session_use
        path = self.workspace_paths.get((session_id, asset_id))
        if path is None:
            raise LookupError("asset unavailable")
        return path


@pytest.fixture
def authority_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    file_a = workspace_a / "a.png"
    file_b = workspace_b / "b.png"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")

    def artifact(artifact_id: str, *, session_id: str, workspace: Path, suffix: str) -> dict:
        return {
            "id": artifact_id,
            "session_id": session_id,
            "artifact_kind": "image",
            "mime_type": "image/png",
            "source_path": str(workspace / suffix),
            "external_url": f"https://provider.invalid/{artifact_id}",
            "metadata": {
                "workspaceId": f"workspace-{session_id[-1]}",
                "projectId": f"project-{session_id[-1]}",
                "workspaceRoot": str(workspace),
                "workspaceRelativePath": suffix,
                "storageClass": "workspace",
                "pathPlane": "workspace_artifact",
            },
        }

    database = _FakeDatabase(
        artifacts={
            "artifact-a": artifact("artifact-a", session_id="session-a", workspace=workspace_a, suffix="a.png"),
            "artifact-b": artifact("artifact-b", session_id="session-b", workspace=workspace_b, suffix="b.png"),
        },
        sources={
            ("session-a", "source-a"): {
                "sourceId": "source-a",
                "sessionId": "session-a",
                "workspacePath": str(file_a),
                "metadata": {"workspaceId": "workspace-a", "projectId": "project-a"},
            },
            ("session-b", "source-b"): {
                "sourceId": "source-b",
                "sessionId": "session-b",
                "workspacePath": str(file_b),
                "metadata": {"workspaceId": "workspace-b", "projectId": "project-b"},
            },
        },
    )
    authorities = {
        "session-a": SimpleNamespace(
            workspace_id="workspace-a",
            project_id="project-a",
            workspace_root=str(workspace_a),
        ),
        "session-b": SimpleNamespace(
            workspace_id="workspace-b",
            project_id="project-b",
            workspace_root=str(workspace_b),
        ),
    }
    authority_service = SimpleNamespace(
        resolve=lambda *, runtime_kind, session_id, **_kwargs: authorities[session_id]
    )
    media_library = _FakeWorkspaceMediaLibrary(
        {
            ("session-a", "asset-a"): file_a,
            ("session-b", "asset-b"): file_b,
        }
    )
    resolver = authority_module.CreativeMediaResourceAuthorityService(
        database=database,
        authority_service=authority_service,
        media_library=media_library,
    )
    monkeypatch.setattr(authority_module, "creative_media_resource_authority", resolver)
    monkeypatch.setattr(facade, "creative_media_resource_authority", resolver)
    monkeypatch.setattr(psd_tools, "creative_media_resource_authority", resolver)
    monkeypatch.setattr(recipe_module, "creative_media_resource_authority", resolver)
    return resolver, workspace_a, workspace_b


def test_core_resolver_fails_closed_for_cross_session_resources(authority_fixture) -> None:
    resolver, workspace_a, workspace_b = authority_fixture

    assert resolver.resolve_artifact(session_id="session-a", artifact_id="artifact-a").path == workspace_a / "a.png"
    assert resolver.resolve_source(session_id="session-a", source_id="source-a").path == workspace_a / "a.png"
    assert resolver.resolve_workspace_asset(session_id="session-a", asset_id="asset-a").path == workspace_a / "a.png"
    assert resolver.resolve_path(session_id="session-a", path="a.png").path == workspace_a / "a.png"
    assert resolver.resolve_output_path(
        session_id="session-a",
        path="deliverables/new.psd",
    ).path == workspace_a / "deliverables" / "new.psd"

    for resolve in (
        lambda: resolver.resolve_artifact(session_id="session-a", artifact_id="artifact-b"),
        lambda: resolver.resolve_source(session_id="session-a", source_id="source-b"),
        lambda: resolver.resolve_workspace_asset(session_id="session-a", asset_id="asset-b"),
        lambda: resolver.resolve_path(session_id="session-a", path=str(workspace_b / "b.png")),
        lambda: resolver.resolve_output_path(
            session_id="session-a",
            path=str(workspace_b / "new.psd"),
        ),
    ):
        with pytest.raises(authority_module.CreativeMediaResourceAuthorityError):
            resolve()


def test_resource_preflight_rejects_oversized_nested_manifest_without_truncating(
    authority_fixture,
) -> None:
    resolver, workspace_a, _workspace_b = authority_fixture
    payload = {
        "sessionId": "session-a",
        "workspaceId": "workspace-a",
        "projectId": "project-a",
        "workspacePath": str(workspace_a),
        "inputs": [{"label": f"input-{index}"} for index in range(200)]
        + [{"artifactId": "artifact-b"}],
    }

    with pytest.raises(
        authority_module.CreativeMediaResourceAuthorityError,
        match="current session scope",
    ) as raised:
        resolver.authorize_request_resources(payload)

    assert raised.value.reason_code == "media_resource_manifest_too_large"


def test_facade_rejects_cross_session_reference_before_handler(authority_fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    invoked = 0

    def unexpected_handler(_spec):
        nonlocal invoked
        invoked += 1
        raise AssertionError("provider handler must not be resolved")

    monkeypatch.setattr(
        facade,
        "get_runtime_context",
        lambda: {
            "session_id": "session-a",
            "workspace_id": "workspace-a",
            "project_id": "project-a",
            "workspace_path": str(authority_fixture[1]),
        },
    )
    monkeypatch.setattr(facade, "_resolve_handler", unexpected_handler)

    result = json.loads(
        asyncio.run(
            facade.creative_media_jobs.ainvoke(
                {
                    "action": "create",
                    "request": {
                        "modality": "video",
                        "operationKind": "video.image_to_video",
                        "prompt": "fixture",
                        "referenceAssetIds": ["artifact-b"],
                    },
                },
            )
        )
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "media_resource_not_authorized"
    assert invoked == 0


def test_facade_rejects_cross_workspace_output_before_handler(
    authority_fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = 0

    def unexpected_handler(_spec):
        nonlocal invoked
        invoked += 1
        raise AssertionError("PSD writer must not be resolved")

    monkeypatch.setattr(
        facade,
        "get_runtime_context",
        lambda: {
            "session_id": "session-a",
            "workspace_id": "workspace-a",
            "project_id": "project-a",
            "workspace_path": str(authority_fixture[1]),
        },
    )
    monkeypatch.setattr(facade, "_resolve_handler", unexpected_handler)

    result = json.loads(
        facade.creative_media_assets.invoke(
            {
                "action": "psd_compose_template",
                "request": {
                    "canvas": {"width": 16, "height": 16},
                    "layers": [{"name": "fixture"}],
                    "outputPath": str(authority_fixture[2] / "outside.psd"),
                },
            }
        )
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "media_resource_not_authorized"
    assert invoked == 0


def test_psd_rejects_cross_session_artifact_before_file_open(authority_fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = 0
    monkeypatch.setattr(
        psd_tools,
        "_runtime_context",
        lambda: {
            "session_id": "session-a",
            "workspace_id": "workspace-a",
            "project_id": "project-a",
            "workspace_path": str(authority_fixture[1]),
        },
    )

    def unexpected_open(_source):
        nonlocal opened
        opened += 1
        raise AssertionError("image reader must not run")

    monkeypatch.setattr(psd_tools, "_open_preview_image", unexpected_open)
    output = psd_tools.creative_media_alpha_inspect.invoke({"artifact_id": "artifact-b"})

    assert "Status: blocked" in output
    assert opened == 0


def test_psd_compose_rejects_cross_workspace_output_before_any_file_side_effect(
    authority_fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    side_effects = {"input": 0, "compose": 0, "artifact": 0}
    monkeypatch.setattr(
        psd_tools,
        "_runtime_context",
        lambda: {
            "session_id": "session-a",
            "workspace_id": "workspace-a",
            "project_id": "project-a",
            "workspace_path": str(authority_fixture[1]),
        },
    )

    def unexpected_input(**_kwargs):
        side_effects["input"] += 1
        raise AssertionError("input files must not be resolved")

    def unexpected_compose(**_kwargs):
        side_effects["compose"] += 1
        raise AssertionError("PSD writer must not run")

    def unexpected_artifact(*_args, **_kwargs):
        side_effects["artifact"] += 1
        raise AssertionError("artifact ledger must not be written")

    monkeypatch.setattr(psd_tools, "_resolve_input_path", unexpected_input)
    monkeypatch.setattr(psd_tools, "compose_psd_document", unexpected_compose)
    monkeypatch.setattr(psd_tools, "_record_artifact", unexpected_artifact)

    output = psd_tools.creative_media_psd_compose_template.invoke(
        {
            "request": {
                "canvas": {"width": 16, "height": 16},
                "layers": [{"name": "fixture", "path": "a.png"}],
                "outputPath": str(authority_fixture[2] / "outside.psd"),
            }
        }
    )

    assert "Status: blocked" in output
    assert side_effects == {"input": 0, "compose": 0, "artifact": 0}


def test_recipe_rejects_cross_session_artifact_before_ledger_write(authority_fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    writes = 0

    def unexpected_write(*_args, **_kwargs):
        nonlocal writes
        writes += 1
        raise AssertionError("ledger must not be written")

    monkeypatch.setattr(recipe_module, "_write_store", unexpected_write)
    compiler = recipe_module.CreativeRecipeCompiler()

    with pytest.raises(authority_module.CreativeMediaResourceAuthorityError):
        compiler.register_asset(
            {
                "sessionId": "session-a",
                "workspaceId": "workspace-a",
                "projectId": "project-a",
                "workspacePath": str(authority_fixture[1]),
                "artifactId": "artifact-b",
                "modality": "image",
            }
        )

    assert writes == 0


def test_recipe_internal_compiler_without_session_keeps_legacy_fixture_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, dict] = {}

    def unexpected_authority(_request):
        raise AssertionError("unscoped internal compiler fixture must not enter Agent authority")

    def read_store(_filename: str, key: str) -> dict:
        return {"version": 1, key: dict(stored.get(key) or {})}

    def write_store(_filename: str, key: str, values: dict) -> None:
        stored[key] = dict(values)

    monkeypatch.setattr(
        recipe_module.creative_media_resource_authority,
        "authorize_request_resources",
        unexpected_authority,
    )
    monkeypatch.setattr(recipe_module, "_read_store", read_store)
    monkeypatch.setattr(recipe_module, "_write_store", write_store)

    asset = recipe_module.CreativeRecipeCompiler().register_asset(
        {"artifactId": "fixture-artifact", "modality": "image"}
    )

    assert asset["artifactId"] == "fixture-artifact"
    assert asset["sessionId"] == ""
    assert stored["assets"][asset["assetId"]]["artifactId"] == "fixture-artifact"
