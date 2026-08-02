from __future__ import annotations

from types import SimpleNamespace

from core.storage import StorageManager


def test_agent_runtime_profile_prefers_configured_identity_label_over_description() -> None:
    fake_storage = SimpleNamespace(get_agent=lambda _agent_id: {
        "name": "镜头规划师",
        "roleLabel": "分镜导演",
        "description": "负责把剧本拆成镜头。",
        "avatar": "/avatars/storyboard.png",
    })

    profile = StorageManager.get_agent_runtime_profile(fake_storage, "storyboard-agent")

    assert profile == {
        "name": "镜头规划师",
        "roleLabel": "分镜导演",
        "avatar": "/avatars/storyboard.png",
    }
