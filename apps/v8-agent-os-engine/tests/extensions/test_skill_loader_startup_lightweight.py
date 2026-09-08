from __future__ import annotations

from unittest.mock import patch

from runtimes.extensions.skills import loader as loader_module
from runtimes.extensions.skills.loader import SkillLoader


class _PendingTask:
    @staticmethod
    def done() -> bool:
        return False


def test_agent_cold_inventory_reads_routing_metadata_without_full_refresh(monkeypatch, tmp_path) -> None:
    root = tmp_path / "skills"
    skill = root / "sample"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: sample\ndescription: inspect sample metadata\n---\nRead carefully.\n", encoding="utf-8")
    descriptor = {"rootPath": str(root), "sourceType": "global", "visibility": "global"}
    for name, value in {"_skills_registry": {}, "_root_inventory_states": {}, "_visible_inventory_cache": {},
                        "_skills_root_descriptors": [], "_dirty_root_paths": set(),
                        "_background_refresh_in_progress": False, "_background_refresh_task": None}.items():
        monkeypatch.setattr(SkillLoader, name, value)
    monkeypatch.setattr(SkillLoader, "_resolve_inventory_descriptors", classmethod(lambda cls, **_kwargs: [descriptor]))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("ordinary agent preparation performed a full inventory refresh")

    monkeypatch.setattr(SkillLoader, "ensure_fresh", forbidden)
    monkeypatch.setattr(SkillLoader, "prime_startup_cache", forbidden)
    monkeypatch.setattr(SkillLoader, "_compute_root_manifest", forbidden)
    monkeypatch.setattr(SkillLoader, "_skill_directory_manifest_hash", forbidden)
    original_scan = SkillLoader._scan_single_root_descriptor
    scans = []

    def scan(cls, root_descriptor, **kwargs):
        scans.append(kwargs)
        return original_scan(root_descriptor, **kwargs)

    monkeypatch.setattr(SkillLoader, "_scan_single_root_descriptor", classmethod(scan))
    first = SkillLoader.get_inventory(force_refresh=False, allow_blocking_refresh=False)
    second = SkillLoader.get_inventory(force_refresh=False, allow_blocking_refresh=False)
    assert [item["name"] for item in first["items"]] == ["sample"]
    assert second["items"] == first["items"]
    assert len(scans) == 1
    assert scans[0]["summarize_structure"] is False
    assert scans[0]["allow_llm_profile_inference"] is False


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


def test_bounded_watcher_resumes_after_slow_roots_instead_of_starving_later_roots(monkeypatch, tmp_path) -> None:
    descriptors = [SkillLoader._build_root_descriptor(root_path=tmp_path / name, source_type="global", visibility="global")
                   for name in ("first", "second", "third")]
    paths = [SkillLoader._descriptor_cache_key(item) for item in descriptors]
    states = {path: {"descriptor": item, "descriptorSignature": SkillLoader._root_descriptors_signature([item]),
                     "rootRevision": "stable", "manifest": {}, "registry": {}}
              for path, item in zip(paths, descriptors)}
    for name, value in {"_skills_registry": {}, "_root_inventory_states": states, "_dirty_root_paths": set(),
                        "_skills_root_descriptors": descriptors, "_background_refresh_next_root": None,
                        "_skills_root_signature": SkillLoader._root_descriptors_signature(descriptors)}.items():
        monkeypatch.setattr(SkillLoader, name, value)
    elapsed, scanned = [0.0], []

    def slow_manifest(cls, descriptor):
        scanned.append(cls._descriptor_cache_key(descriptor))
        elapsed[0] += 2.0
        return {}

    monkeypatch.setattr(loader_module.time, "perf_counter", lambda: elapsed[0])
    monkeypatch.setattr(SkillLoader, "_compute_root_manifest", classmethod(slow_manifest))
    monkeypatch.setattr(SkillLoader, "_root_manifest_fingerprint", classmethod(lambda cls, *_args: "stable"))
    monkeypatch.setattr(SkillLoader, "_remember_recent_skill_discovery", classmethod(lambda cls, **_kwargs: []))
    monkeypatch.setattr(SkillLoader, "_persist_cache", classmethod(lambda cls: None))
    for expected in paths:
        result = SkillLoader.refresh_root_descriptors_if_changed(descriptors, compare_existing=True, timeout_ms=1500)
        assert scanned[-1] == expected
        assert expected not in result["dirtyRoots"]
        assert result["rootDescriptors"] == descriptors  # Scheduling order is not a visibility revision.
    assert scanned == paths
    assert SkillLoader._background_refresh_next_root == paths[0]
