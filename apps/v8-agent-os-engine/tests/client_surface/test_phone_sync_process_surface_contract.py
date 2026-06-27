from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def _read_repo_file(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_phone_local_sync_keeps_client_side_tombstones() -> None:
    source = _read_repo_file("apps/v8-agent-os-phone/src/services/LocalDatabaseService.ts")

    assert "CREATE TABLE IF NOT EXISTS local_message_deletions" in source
    assert "PRIMARY KEY (session_id, message_id)" in source
    assert "SELECT message_id FROM local_message_deletions" in source
    assert "deletedIds.has(messageId)" in source
    assert "INSERT OR REPLACE INTO local_message_deletions" in source
    assert "AND id NOT IN" in source


def test_phone_desktop_live_stale_poll_does_not_clear_existing_process_surface() -> None:
    source = _read_repo_file("apps/v8-agent-os-phone/src/screens/ChatScreen.tsx")

    assert "applySessionProcessSurface(payload.processes || [], { stale: payload.stale })" in source
    assert "processCount: processesRef.current.length" in source

    initial_error_start = source.index('phase: "initial_error"')
    initial_error_block = source[max(0, initial_error_start - 500): initial_error_start + 500]
    assert "stale: true" in initial_error_block
    assert "forceClear" not in initial_error_block

    interval_error_start = source.index('phase: "interval_error"')
    interval_error_block = source[max(0, interval_error_start - 500): interval_error_start + 500]
    assert "stale: true" in interval_error_block
    assert "forceClear" not in interval_error_block


def test_admin_process_surface_timeout_returns_stale_cache_payload() -> None:
    source = _read_repo_file("apps/v8-agent-os-admin/src/app/api/client/sessions/[id]/processes/route.ts")

    assert "const processSurfaceCache = new Map" in source
    assert "function staleProcessSurface" in source
    assert '"x-v8-process-surface-stale": "1"' in source
    assert "Engine process surface timed out; returning stale cache." in source
