"""Contracts verifying dual-prompt LSP injection, English directives, and Supervisor double-insurance."""

from unittest.mock import MagicMock, patch

from core.delegated_agent_charter import DELEGATED_AGENT_OPERATING_CHARTER
from graph.supervisor_context import _render_engineering_context
from runtimes.engineering.lsp import LSPProbeRunner, LSPStatus, LSPStatusRegistry
from runtimes.engineering.lsp.lsp_diff import LSPDiffResult
from runtimes.engineering.lsp.lsp_protocol import Diagnostic, DiagnosticSeverity, Position, Range


def test_supervisor_engineering_context_pack_lsp_principles():
    """Verify that Supervisor's engineering context pack injects the 3-line English LSP principles."""
    state = {
        "engineering_context": {
            "triggerDecision": {
                "active": True,
                "reason": "code_modification",
                "signals": ["modify_file", "ts"],
            },
            "contextPack": {
                "repoBrief": {"repoRoot": "/test/repo", "branch": "main"},
            },
        }
    }

    rendered, diags = _render_engineering_context(state)

    assert "--- ENGINEERING CONTEXT PACK ---" in rendered
    assert "LSP principles:" in rendered
    assert "Language Server Protocol (LSP) diagnostics run on-demand to safeguard code correctness." in rendered
    assert "install it yourself via `run_system_command` to ~/.v8-agent-os/cache/lsp before proceeding." in rendered
    assert "retry with explicit --registry mirror. Never invent syntax without verification." in rendered


def test_delegated_agent_charter_contains_lsp():
    """Verify that the operating charter for delegated subagents contains the LSP directives."""
    assert "Language Server Protocol (LSP) diagnostics run on-demand to safeguard code correctness." in DELEGATED_AGENT_OPERATING_CHARTER
    assert "install it yourself via `run_system_command` to ~/.v8-agent-os/cache/lsp before proceeding." in DELEGATED_AGENT_OPERATING_CHARTER
    assert "explicit --registry mirror" in DELEGATED_AGENT_OPERATING_CHARTER


def test_lsp_probe_runner_get_install_guidance_all_languages():
    """Verify JIT guidance produces single-line actionable install commands with cache root and fallback mirrors."""
    ts_guidance = LSPProbeRunner.get_install_guidance("ts")
    assert "TypeScript Language Server missing" in ts_guidance
    assert "npm install --prefix ~/.v8-agent-os/cache/lsp typescript typescript-language-server" in ts_guidance
    assert "--registry=https://registry.npmmirror.com" in ts_guidance

    py_guidance = LSPProbeRunner.get_install_guidance("py")
    assert "Python Language Server (pyright) missing" in py_guidance
    assert "pip install --target ~/.v8-agent-os/cache/lsp/py pyright" in py_guidance
    assert "-i https://pypi.tuna.tsinghua.edu.cn/simple" in py_guidance

    rust_guidance = LSPProbeRunner.get_install_guidance("rust")
    assert "rustup component add rust-analyzer" in rust_guidance

    go_guidance = LSPProbeRunner.get_install_guidance("go")
    assert "go install golang.org/x/tools/gopls@latest" in go_guidance
    assert "GOPROXY=https://goproxy.cn,direct" in go_guidance


def test_workspace_file_lsp_double_insurance(tmp_path, monkeypatch):
    """Verify that file write hook triggers LSPManager on_file_saved and reflects errors in tool output."""
    from erc.runtime_context import bind_runtime_context
    from core.tools.native.workspace_file import write_native_file, read_native_file

    monkeypatch.setattr(
        "core.tools.native.workspace_file.resolve_workspace_tool_path",
        lambda path, **_kwargs: {
            "ok": True,
            "resolvedPath": str(path),
            "binding": {"activeWorkspaceRoot": str(tmp_path), "sideEffectsAllowed": True},
        },
    )
    monkeypatch.setattr(
        "core.tools.native.workspace_file.ensure_workspace_side_effect_allowed",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        "core.tools.native.workspace_file._check_file_read_receipt",
        lambda *_args, **_kwargs: (True, None),
    )
    from erc.safety_guardian import SafetyDecision

    monkeypatch.setattr(
        "core.tools.native.workspace_file.safety_guardian.assess_file_write",
        lambda *_args, **_kwargs: SafetyDecision(),
    )
    monkeypatch.setattr(
        "core.tools.native.workspace_file._enforce_safety_decision",
        lambda *_args, **_kwargs: (True, None),
    )
    from core.database import db

    monkeypatch.setattr(
        db,
        "assert_chat_run_epoch",
        lambda *_args, **_kwargs: None,
    )

    test_file = tmp_path / "index.ts"
    test_file.write_text("console.log('init');", encoding="utf-8")

    with bind_runtime_context(
        runtime_kind="chat",
        session_id="session-1",
        workspace_path=str(tmp_path),
    ):
        # 1. Test read includes LSP status
        read_out = read_native_file.func(str(test_file))
        assert "LSP Status: ts=" in read_out

        # 2. Test write with mocked LSP incremental errors
        mock_diff = LSPDiffResult(
            language="ts",
            file_path=str(test_file),
            new_errors=[
                Diagnostic(
                    range=Range(start=Position(0, 0), end=Position(0, 5)),
                    message="Cannot find name 'wrongVar'",
                    code="TS2304",
                    severity=DiagnosticSeverity.ERROR,
                )
            ],
            formatted_feedback="error[TS2304]: Cannot find name 'wrongVar'\n  --> index.ts:1:1\n1 | wrongVar\n  | ^^^^^^^^",
            has_regressions=True,
        )

        with patch("runtimes.engineering.lsp.lsp_manager.LSPManager.on_file_saved_sync", return_value=mock_diff):
            write_out = write_native_file.func(
                path=str(test_file),
                content="wrongVar = 1;",
                allow_full_replace=True,
            )
            assert "[LSP Diagnostics - Incremental Errors Introduced]" in write_out
            assert "error[TS2304]: Cannot find name 'wrongVar'" in write_out
