from __future__ import annotations

from unittest.mock import patch

from runtimes.extensions.skills.loader import SkillLoader


def test_system_prompt_addition_uses_cached_inventory_without_force_refresh() -> None:
    calls: list[dict] = []

    def fake_inventory(**kwargs):  # noqa: ANN001
        calls.append(dict(kwargs))
        return {"items": [], "rootDescriptors": []}

    with patch.object(SkillLoader, "get_inventory", side_effect=fake_inventory):
        assert SkillLoader.get_system_prompt_addition() == "No persistent skills available at the moment."

    assert calls == [{"force_refresh": False, "include_scoped": False}]


def test_reload_if_changed_uses_bounded_background_timeout(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(SkillLoader, "_discovery_root_descriptors", classmethod(lambda cls: []))

    def fake_refresh(cls, descriptors, *, compare_existing=False, timeout_ms=None):  # noqa: ANN001
        calls.append(
            {
                "descriptors": descriptors,
                "compare_existing": compare_existing,
                "timeout_ms": timeout_ms,
            }
        )
        return {"changed": False}

    monkeypatch.setattr(SkillLoader, "refresh_root_descriptors_if_changed", classmethod(fake_refresh))

    assert SkillLoader.reload_if_changed() == {"changed": False}
    assert calls == [
        {
            "descriptors": [],
            "compare_existing": True,
            "timeout_ms": SkillLoader._background_refresh_timeout_ms,
        }
    ]
