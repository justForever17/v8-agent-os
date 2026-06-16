from core.runtime import startup_profile
from erc.capability_registry import _KNOWN_RUNTIME_BASELINES, _SNAPSHOT_RUNTIME_ORDER


def test_engineering_runtime_is_default_installed_family():
    assert "engineering" in startup_profile.KNOWN_RUNTIME_FAMILIES
    assert "engineering" in startup_profile._default_runtime_families_for_profile("minimal")
    assert startup_profile._FEATURE_RUNTIME_FAMILY["engineering_lane"] == "engineering"
    assert startup_profile._FEATURE_RUNTIME_FAMILY["project_coding"] == "engineering"


def test_engineering_runtime_has_capability_baseline_without_desktop_live():
    assert "engineering" in _SNAPSHOT_RUNTIME_ORDER
    assert _KNOWN_RUNTIME_BASELINES["engineering"]["displayName"] == "EngineeringRuntime"
    assert "desktop_live" not in _KNOWN_RUNTIME_BASELINES
    hints = "\n".join(_KNOWN_RUNTIME_BASELINES["engineering"].get("promptHints") or [])
    assert "workspace" in hints
    assert "allowed workset" in hints
    assert "handoffRequired" in hints
