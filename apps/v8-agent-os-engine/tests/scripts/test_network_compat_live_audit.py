from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_live_audit_requires_opt_in_before_creating_state(tmp_path):
    target = tmp_path / "must-not-be-created"
    result = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).with_name("run_network_compat_live_audit.py")), "--isolated-root", str(target)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "--live required" in result.stdout
    assert not target.exists()
