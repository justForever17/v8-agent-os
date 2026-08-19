from __future__ import annotations

from unittest.mock import patch

from runtimes.extensions.skills.loader import SkillLoader


class _PendingTask:
    @staticmethod
    def done() -> bool:
        return False


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


def test_cold_inventory_does_not_duplicate_an_active_background_scan(monkeypatch, tmp_path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    descriptor = {
        "rootPath": str(root),
        "sourceType": "global",
        "visibility": "global",
    }
    monkeypatch.setattr(SkillLoader, "_skills_registry", {})
    monkeypatch.setattr(SkillLoader, "_skills_revision", "")
    monkeypatch.setattr(SkillLoader, "_skills_fingerprint", "")
    monkeypatch.setattr(SkillLoader, "_startup_state", "refreshing")
    monkeypatch.setattr(SkillLoader, "_snapshot_freshness", "cold")
    monkeypatch.setattr(SkillLoader, "_background_refresh_in_progress", True)
    monkeypatch.setattr(SkillLoader, "_background_refresh_task", _PendingTask())
    monkeypatch.setattr(SkillLoader, "prime_startup_cache", classmethod(lambda cls: False))
    monkeypatch.setattr(
        SkillLoader,
        "_resolve_inventory_descriptors",
        classmethod(lambda cls, **_kwargs: [descriptor]),
    )
    monkeypatch.setattr(
        SkillLoader,
        "ensure_fresh",
        classmethod(lambda cls, force=False: (_ for _ in ()).throw(AssertionError("duplicate refresh"))),
    )
    monkeypatch.setattr(
        SkillLoader,
        "_scan_single_root_descriptor",
        classmethod(lambda cls, _descriptor: (_ for _ in ()).throw(AssertionError("foreground scan"))),
    )

    inventory = SkillLoader.get_inventory(force_refresh=False, include_scoped=True)

    assert inventory["items"] == []
    assert inventory["inventoryReadyState"] == "refreshing"
    assert inventory["snapshotFreshness"] == "cold"
    assert inventory["scopedRefreshMode"] == "background_refresh_pending"
