from __future__ import annotations

import asyncio

from api import platform_routes


def test_skills_list_forwards_session_and_workspace_scope(monkeypatch):
    captured: dict[str, object] = {}

    class FakeExtensionsRuntimeService:
        def list_skills(self, **kwargs):
            captured.update(kwargs)
            return [
                {
                    "skillName": "wechat-account-articles",
                    "name": "wechat-account-articles",
                    "description": "Workspace skill from test1.",
                }
            ]

    monkeypatch.setattr(platform_routes, "extensions_runtime_service", FakeExtensionsRuntimeService())
    monkeypatch.setattr(platform_routes.storage, "get_supervisor_config", lambda: {})
    monkeypatch.setattr(platform_routes.storage, "get_all_agents", lambda: [])
    monkeypatch.setattr(platform_routes, "build_specialist_family_registry", lambda agents, registry: [])

    result = asyncio.run(
        platform_routes.get_skills_list(
            session_id="session-test1",
            workspace_path=r"E:\Projects\test1",
            workspace_id="workspace-test1",
            project_id="project-test1",
        )
    )

    assert result["skills"][0]["skillName"] == "wechat-account-articles"
    assert captured["force_refresh"] is False
    assert captured["session_id"] == "session-test1"
    assert captured["explicit_workspace_path"] == r"E:\Projects\test1"
    assert captured["explicit_workspace_id"] == "workspace-test1"
    assert captured["explicit_project_id"] == "project-test1"
    assert captured["runtime_kind"] == "chat"
