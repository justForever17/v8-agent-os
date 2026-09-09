from __future__ import annotations

import importlib.util
from pathlib import Path


def load_script():
    path = Path(__file__).with_name("run_browser_broker_live_audit.py")
    spec = importlib.util.spec_from_file_location("browser_live_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_live_flag_does_not_touch_files_or_start_engine(monkeypatch):
    script = load_script()
    monkeypatch.setattr(Path, "resolve", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no file resolution before --live")))
    assert script.main(["--isolated-root", "not-to-be-read"]) == 2


def test_fixture_requires_actual_page_state_password_protection_and_source_recovery():
    script = load_script()
    assert "PRIVATE-PASSWORD-CANARY" in script.HTML
    assert "document.querySelector('#status').textContent='Saved: '" in script.HTML
