from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def harness():
    spec = importlib.util.spec_from_file_location("bocha_live_fixture", Path(__file__).with_name("run_bocha_provider_live_audit.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_without_live_does_not_read_config_or_import_adapter(monkeypatch, capsys):
    mod = harness()
    monkeypatch.setattr(sys, "argv", ["audit", "--config", "nonexistent-fixture"])
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: pytest.fail("no config read without --live"))
    assert mod.main() == 2
    assert "refused" in capsys.readouterr().out


def test_live_key_is_memory_only_and_output_contains_no_rows_headers_or_errors(tmp_path, monkeypatch, capsys):
    mod = harness()
    config = tmp_path / "fixture-config.json"
    source = json.dumps({"systemBase": {"webFetch": {"providers": {"bocha": {"apiKey": "CANARY-fixture-key"}}}}})
    config.write_text(source, encoding="utf-8")
    calls = []
    def search(query, **kwargs):
        calls.append((query, kwargs))
        return {"ok": False, "statusCode": 403, "failureClass": "provider_access_denied",
                "reason": "CANARY-fixture-key", "headers": {"Authorization": "CANARY-fixture-key"}}
    monkeypatch.setitem(sys.modules, "core.tools.bocha_provider", SimpleNamespace(bocha_search=search))
    monkeypatch.setattr(sys, "argv", ["audit", "--live", "--config", str(config)])
    assert mod.main() == 1
    output = capsys.readouterr().out
    assert "CANARY" not in output and "Authorization" not in output
    assert json.loads(output)["statusCode"] == 403
    assert calls[0][1]["api_key"] == "CANARY-fixture-key"
    assert config.read_text(encoding="utf-8") == source


@pytest.mark.parametrize("rows", [[], [{}], [{"source": "bocha", "title": "Fake", "url": "file:///fixture"}]])
def test_provider_self_report_without_real_result_shape_cannot_pass(rows):
    assert harness().safe_result_summary({"ok": True, "results": rows}, 5)["ok"] is False


def test_valid_result_shape_passes_without_exposing_content():
    result = harness().safe_result_summary({"ok": True, "results": [
        {"source": "bocha", "title": "Public documentation", "url": "https://example.test/doc", "snippet": "DO-NOT-LOG"},
    ]}, 10)
    assert result["ok"] is True and result["resultCount"] == 1
    assert "DO-NOT-LOG" not in json.dumps(result) and "example.test" not in json.dumps(result)
