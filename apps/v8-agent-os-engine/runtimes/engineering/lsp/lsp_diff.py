"""Diagnostic diff comparison and ANSI/compiler-style slice formatting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .lsp_protocol import Diagnostic, DiagnosticSeverity


@dataclass
class LSPDiffResult:
    """Represents the incremental delta in diagnostics before and after an edit."""

    language: str
    file_path: str
    new_errors: List[Diagnostic] = field(default_factory=list)
    resolved_errors: List[Diagnostic] = field(default_factory=list)
    existing_errors: List[Diagnostic] = field(default_factory=list)
    formatted_feedback: str = ""
    has_regressions: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "filePath": self.file_path,
            "newErrors": [d.to_dict() for d in self.new_errors],
            "resolvedErrors": [d.to_dict() for d in self.resolved_errors],
            "existingErrors": [d.to_dict() for d in self.existing_errors],
            "formattedFeedback": self.formatted_feedback,
            "hasRegressions": self.has_regressions,
        }


def _diagnostic_key(d: Diagnostic) -> Tuple[int, int, int, int, str, str]:
    """Generates a stable identity key for a diagnostic item."""
    return (
        d.range.start.line,
        d.range.start.character,
        d.range.end.line,
        d.range.end.character,
        str(d.code if d.code is not None else ""),
        d.message.strip(),
    )


def compute_diagnostic_diff(
    before: List[Diagnostic],
    after: List[Diagnostic],
    *,
    language: str = "",
    file_path: str = "",
    file_content: str = "",
    use_color: bool = False,
) -> LSPDiffResult:
    """Calculates added, resolved, and existing diagnostics between two snapshots."""
    before_map = {_diagnostic_key(d): d for d in before}
    after_map = {_diagnostic_key(d): d for d in after}

    new_errors = [d for k, d in after_map.items() if k not in before_map]
    resolved_errors = [d for k, d in before_map.items() if k not in after_map]
    existing_errors = [d for k, d in after_map.items() if k in before_map]

    # Regressions are defined as newly introduced ERROR or WARNING severities
    has_regressions = any(
        d.severity in (DiagnosticSeverity.ERROR, DiagnosticSeverity.WARNING)
        for d in new_errors
    )

    formatted = ""
    if new_errors and file_content:
        formatted = format_diagnostics_ansi(
            file_path=file_path,
            file_content=file_content,
            diagnostics=new_errors,
            use_color=use_color,
        )

    return LSPDiffResult(
        language=language,
        file_path=file_path,
        new_errors=new_errors,
        resolved_errors=resolved_errors,
        existing_errors=existing_errors,
        formatted_feedback=formatted,
        has_regressions=has_regressions,
    )


def format_diagnostics_ansi(
    file_path: str,
    file_content: str,
    diagnostics: List[Diagnostic],
    *,
    use_color: bool = False,
    max_items: int = 10,
) -> str:
    """Formats diagnostics into compiler-style (Rustc/GCC) diagnostic slices.

    Example output:
        error[TS2304]: Cannot find name 'foo'.
          --> src/index.ts:12:7
           |
        12 | const a = foo;
           |           ^^^ Cannot find name 'foo'.
    """
    if not diagnostics:
        return ""

    lines = file_content.splitlines()
    blocks: List[str] = []

    # ANSI escape color constants
    COLOR_RED = "\033[1;31m" if use_color else ""
    COLOR_YELLOW = "\033[1;33m" if use_color else ""
    COLOR_CYAN = "\033[1;36m" if use_color else ""
    COLOR_BOLD = "\033[1m" if use_color else ""
    COLOR_RESET = "\033[0m" if use_color else ""

    for d in diagnostics[:max_items]:
        sev = d.severity
        if sev == DiagnosticSeverity.ERROR:
            sev_color = COLOR_RED
            sev_name = "error"
        elif sev == DiagnosticSeverity.WARNING:
            sev_color = COLOR_YELLOW
            sev_name = "warning"
        elif sev == DiagnosticSeverity.INFORMATION:
            sev_color = COLOR_CYAN
            sev_name = "info"
        else:
            sev_color = COLOR_CYAN
            sev_name = "hint"

        code_suffix = f"[{d.code}]" if d.code is not None else ""
        header = f"{sev_color}{sev_name}{code_suffix}{COLOR_RESET}: {COLOR_BOLD}{d.message}{COLOR_RESET}"

        line_0 = d.range.start.line
        col_0 = d.range.start.character
        line_num = line_0 + 1
        col_num = col_0 + 1

        loc_str = f"  --> {file_path}:{line_num}:{col_num}"

        block_lines = [header, loc_str, "   |"]

        if 0 <= line_0 < len(lines):
            raw_line = lines[line_0]
            # Replace tabs with 4 spaces for consistent underline alignment
            expanded_line = raw_line.replace("\t", "    ")
            block_lines.append(f"{line_num:>4} | {expanded_line}")

            # Compute caret underline span
            start_col = max(0, col_0)
            end_col = (
                d.range.end.character
                if d.range.end.line == line_0 and d.range.end.character > start_col
                else start_col + 1
            )
            span_len = max(1, end_col - start_col)
            # Ensure span does not overflow line excessively
            span_len = min(span_len, max(1, len(expanded_line) - start_col + 1))

            caret = " " * start_col + "^" * span_len
            block_lines.append(f"     | {sev_color}{caret}{COLOR_RESET} {d.message}")
        else:
            block_lines.append(f"     | (line {line_num} out of bounds)")

        blocks.append("\n".join(block_lines))

    remaining = len(diagnostics) - max_items
    if remaining > 0:
        blocks.append(f"... and {remaining} more diagnostic(s) omitted.")

    return "\n\n".join(blocks)
