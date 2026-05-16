from __future__ import annotations

from pathlib import Path


def test_session_realtime_projection_matrix_covers_core_targets():
    repo_root = Path(__file__).resolve().parents[4]
    matrix_path = repo_root / "packages" / "session-realtime" / "src" / "runtime-projection-matrix.ts"
    content = matrix_path.read_text(encoding="utf-8")

    for runtime_id in ("engineering", "creative_media", "computer_use", "network_supervisor", "automation"):
        assert f'runtimeId: "{runtime_id}"' in content
    for target in ("runtime_card", "runtime_timeline", "hud", "artifact", "context"):
        assert f'"{target}"' in content
    assert "governanceOnly" in content
    assert "desktop_live" in content
