from __future__ import annotations

import json
from pathlib import Path

from tests.scripts import export_spec_runtime_distribution_dry_run as dry_run


def _has_mojibake_marker(text: str) -> bool:
    markers = ("�", "Ã", "Â", "ä¸", "äº", "å®", "å¼", "çš", "è¿", "æµ")
    return any(marker in text for marker in markers)


def test_default_spec_distribution_fixture_is_repo_local_and_utf8() -> None:
    payload = dry_run.build_export()

    sample_source = str(payload.get("sampleSource") or "")
    assert payload["passed"] is True
    assert "apps" in sample_source and "tests" in sample_source and "fixtures" in sample_source
    assert "pdf2docx" not in sample_source.lower()
    payload_text = json.dumps(payload, ensure_ascii=False)
    assert "E:\\Projects\\pdf2docx" not in payload_text
    assert "pdf-to-docx" not in payload_text.lower()

    preview = json.dumps(payload.get("agentSurfacePreview") or [], ensure_ascii=False)
    assert "SPEC_DRY_RUN_COUNTER" in preview
    assert "中文按钮文案" in preview or "中文" in preview
    assert not _has_mojibake_marker(preview)


def test_spec_distribution_report_writes_readable_utf8_markdown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dry_run, "OUTPUT_ROOT", tmp_path)
    payload = dry_run.build_export()

    reports = dry_run._write_default_reports(payload)
    markdown_path = Path(reports["markdown"])
    json_path = Path(reports["json"])

    markdown = markdown_path.read_text(encoding="utf-8")
    json_payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert json_payload["passed"] is True
    assert "Sample source:" in markdown
    assert "pdf2docx" not in markdown.lower()
    assert "pdf-to-docx" not in markdown.lower()
    assert "SPEC_DRY_RUN_COUNTER" in markdown
    assert "中文" in markdown
    assert not _has_mojibake_marker(markdown)
