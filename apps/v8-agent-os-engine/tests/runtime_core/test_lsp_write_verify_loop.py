"""Tests for LSP write verification loop, incremental diffing, and ANSI diagnostic formatting."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from runtimes.engineering.lsp import (
    Diagnostic,
    DiagnosticSeverity,
    LSPDiffResult,
    LSPManager,
    LSPProcessInstance,
    LSPStatus,
    Position,
    Range,
    compute_diagnostic_diff,
    format_diagnostics_ansi,
)


def _make_diag(line: int, start_col: int, end_col: int, msg: str, code: str = "2304", sev: DiagnosticSeverity = DiagnosticSeverity.ERROR) -> Diagnostic:
    return Diagnostic(
        range=Range(
            start=Position(line=line, character=start_col),
            end=Position(line=line, character=end_col),
        ),
        message=msg,
        code=code,
        severity=sev,
        source="typescript",
    )


def test_compute_diagnostic_diff_new_and_resolved():
    d1 = _make_diag(1, 0, 5, "Cannot find name 'foo'", "TS2304")
    d2 = _make_diag(2, 4, 10, "Type 'number' is not assignable to type 'string'", "TS2322")
    d3 = _make_diag(5, 2, 8, "Unused variable 'bar'", "TS6133", sev=DiagnosticSeverity.WARNING)

    # Initial: d1 and d2 exist
    before = [d1, d2]
    # After edit: d1 is fixed, d2 remains, d3 is introduced
    after = [d2, d3]

    diff = compute_diagnostic_diff(before, after, language="ts", file_path="src/index.ts")

    assert len(diff.new_errors) == 1
    assert diff.new_errors[0].code == "TS6133"
    assert len(diff.resolved_errors) == 1
    assert diff.resolved_errors[0].code == "TS2304"
    assert len(diff.existing_errors) == 1
    assert diff.existing_errors[0].code == "TS2322"
    assert diff.has_regressions is True


def test_format_diagnostics_ansi_rustc_style():
    content = """import React from 'react';

export const Button = () => {
    const total = undefinedVar + 1;
    return <button>{total}</button>;
};
"""
    diag = _make_diag(
        line=3,
        start_col=18,
        end_col=30,
        msg="Cannot find name 'undefinedVar'.",
        code="TS2304",
        sev=DiagnosticSeverity.ERROR,
    )

    formatted = format_diagnostics_ansi(
        file_path="src/components/Button.tsx",
        file_content=content,
        diagnostics=[diag],
        use_color=False,
    )

    assert "error[TS2304]: Cannot find name 'undefinedVar'." in formatted
    assert "--> src/components/Button.tsx:4:19" in formatted
    assert "4 |     const total = undefinedVar + 1;" in formatted
    assert "^^^^^^^^^^^^ Cannot find name 'undefinedVar'." in formatted


def test_lsp_manager_detect_language():
    assert LSPManager.detect_language("/path/to/file.ts") == "ts"
    assert LSPManager.detect_language("/path/to/file.tsx") == "ts"
    assert LSPManager.detect_language("script.py") == "py"
    assert LSPManager.detect_language("main.rs") == "rust"
    assert LSPManager.detect_language("server.go") == "go"
    assert LSPManager.detect_language("document.md") is None
    assert LSPManager.detect_language("style.css") is None


def test_lsp_manager_lru_cleanup():
    async def _run():
        manager = LSPManager()
        mock_instance_old = MagicMock(spec=LSPProcessInstance)
        mock_instance_old.shutdown = AsyncMock()
        mock_instance_new = MagicMock(spec=LSPProcessInstance)
        mock_instance_new.shutdown = AsyncMock()

        key_old = ("ts", "/workspace/old")
        key_new = ("ts", "/workspace/new")

        manager._instances[key_old] = mock_instance_old
        manager._last_used[key_old] = 100.0  # long ago

        manager._instances[key_new] = mock_instance_new
        manager._last_used[key_new] = 1000.0  # recent

        with patch("time.time", return_value=500.0):
            # Idle timeout = 300s. key_old idle time = 500 - 100 = 400s (> 300s) -> evicted
            # key_new idle time = 500 - 1000 < 0 -> kept
            evicted = await manager.cleanup_idle_instances(idle_timeout_sec=300.0)

            assert evicted == 1
            assert key_old not in manager._instances
            assert key_new in manager._instances
            mock_instance_old.shutdown.assert_awaited_once()
            mock_instance_new.shutdown.assert_not_called()

    asyncio.run(_run())


def test_lsp_manager_on_file_saved_flow():
    async def _run():
        manager = LSPManager()
        mock_instance = MagicMock(spec=LSPProcessInstance)
        mock_instance.is_alive.return_value = True
        mock_instance.did_save = AsyncMock()

        diag_err = _make_diag(0, 0, 4, "Syntax error", "TS1005")

        # Before save: no diags. After save: 1 diag.
        call_count = 0

        def mock_get_diagnostics(uri_or_path: str):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []
            return [diag_err]

        mock_instance.get_diagnostics = mock_get_diagnostics

        with patch.object(manager, "get_or_spawn", AsyncMock(return_value=mock_instance)):
            content = "let a = ;"
            diff = await manager.on_file_saved("test.ts", content, workspace_root="/test", wait_ms=0)

            assert diff is not None
            assert len(diff.new_errors) == 1
            assert diff.new_errors[0].code == "TS1005"
            assert "error[TS1005]: Syntax error" in diff.formatted_feedback
            mock_instance.did_save.assert_awaited_once_with("test.ts", content)

    asyncio.run(_run())
