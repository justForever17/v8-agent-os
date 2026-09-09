import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("context7_live_audit", Path(__file__).with_name("run_context7_source_live_audit.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_no_live_does_not_read_config_connect_or_create_state(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: pytest.fail("configuration read before --live"))
    root = tmp_path / "must-not-exist"
    assert audit.main(["--isolated-root", str(root)]) == 2
    assert not root.exists()


def test_docs_oracle_requires_actual_example_and_official_link():
    for payload in [{"ok": True, "text": "Context7 connected"},
                    {"ok": True, "text": "create_agent tools"},
                    {"ok": True, "text": "https://docs.langchain.com/ page"},
                    {"ok": False, "text": "create_agent tools https://docs.langchain.com/"}]:
        assert audit.document_summary(payload)["ok"] is False
    valid = audit.document_summary({"ok": True, "text": "Source: https://docs.langchain.com/oss/python/agents?token=HIDDEN\nfrom langchain.agents import create_agent\nagent = create_agent(model, tools=[search])"})
    assert valid["ok"] is True and valid["sourceUrls"] == ["https://docs.langchain.com/oss/python/agents"]
    assert "HIDDEN" not in str(valid)


def test_lookalike_hosts_do_not_prove_library_documentation():
    assert not audit.document_summary({"ok": True, "text": "create_agent tools https://docs.langchain.com.evil.test/"})["ok"]
