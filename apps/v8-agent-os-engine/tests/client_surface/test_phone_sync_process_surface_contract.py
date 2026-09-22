from __future__ import annotations

from pathlib import Path

from tests.client_surface.test_client_connection_history_contract import _run_phone_behavior


REPO_ROOT = Path(__file__).resolve().parents[4]


def _read_repo_file(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_phone_local_sync_keeps_client_side_tombstones() -> None:
    _run_phone_behavior("same IDs in independent profiles", "phone-multidevice-behavior.test.cjs")


def test_phone_desktop_live_stale_poll_does_not_clear_existing_process_surface() -> None:
    _run_phone_behavior("process surface")
    _run_phone_behavior("actual terminal polling effect", "phone-multidevice-behavior.test.cjs")


def test_admin_process_surface_timeout_returns_stale_cache_payload() -> None:
    source = _read_repo_file("apps/v8-agent-os-web/src/app/api/admin/client/sessions/[id]/processes/route.ts")

    assert "const processSurfaceCache = new Map" in source
    assert "function staleProcessSurface" in source
    assert '"x-v8-process-surface-stale": "1"' in source
    assert "Engine process surface timed out; returning stale cache." in source
