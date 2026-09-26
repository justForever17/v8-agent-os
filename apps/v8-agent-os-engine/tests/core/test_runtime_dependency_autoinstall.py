from __future__ import annotations

import sys
from pathlib import Path

from core import dependency_registry
from core.runtime_dependency_autoinstall import (
    DOMESTIC_PIP_EXTRA_INDEX,
    DOMESTIC_PIP_INDEX,
    ensure_runtime_dependency,
    is_module_installed,
    refresh_installed_runtime_state,
    resolve_pip_index_args,
)


def test_is_module_installed_detects_builtin():
    assert is_module_installed("sys") is True
    assert is_module_installed("os") is True
    assert is_module_installed("non_existent_fake_module_xyz_123") is False


def test_resolve_pip_index_args_switches_to_domestic_when_pypi_unreachable(monkeypatch):
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.setattr("core.runtime_dependency_autoinstall.can_reach_pypi", lambda timeout_seconds=1.5: False)

    args = resolve_pip_index_args()
    assert args == ["-i", DOMESTIC_PIP_INDEX, "--extra-index-url", DOMESTIC_PIP_EXTRA_INDEX]


def test_resolve_pip_index_args_respects_official_pypi_when_reachable(monkeypatch):
    monkeypatch.delenv("PIP_INDEX_URL", raising=False)
    monkeypatch.setattr("core.runtime_dependency_autoinstall.can_reach_pypi", lambda timeout_seconds=1.5: True)

    args = resolve_pip_index_args()
    assert args == []


def test_resolve_pip_index_args_respects_user_env(monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://custom.pip.org/simple")
    args = resolve_pip_index_args()
    assert args == []


def test_refresh_installed_runtime_state_clears_failed_modules_and_registry_cache(monkeypatch):
    # Set up a fake failed module in sys.modules
    fake_mod = "test_fake_unloaded_mod"
    sys.modules[fake_mod] = None

    cache_cleared = False
    def fake_invalidate():
        nonlocal cache_cleared
        cache_cleared = True

    monkeypatch.setattr(dependency_registry, "invalidate_dependency_cache", fake_invalidate)

    refresh_installed_runtime_state(fake_mod)

    assert fake_mod not in sys.modules
    assert cache_cleared is True


def test_ensure_runtime_dependency_returns_immediately_when_already_installed():
    ok, err = ensure_runtime_dependency("sys")
    assert ok is True
    assert err is None


def test_engine_lightweight_requirements_profile():
    engine_root = Path(__file__).resolve().parents[2]
    req_file = engine_root / "requirements" / "engine-lightweight.txt"
    assert req_file.exists()

    content = req_file.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    active_content = "\n".join(lines)
    assert "-r base.txt" in lines
    assert "scrapling[fetchers]==0.4.1" in lines
    assert "jieba" in lines

    # Verify heavy or deadweight dependencies are absent
    for deadweight in ("chromadb", "edge_tts", "yt-dlp", "psd-tools", "boto3", "botocore", "kubernetes", "onnxruntime"):
        assert deadweight not in active_content, f"{deadweight} should not be in engine-lightweight.txt"
