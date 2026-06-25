from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from core.reasoning_payload_contract import REASONING_KEY_SET, SIGNATURE_KEY_SET


ENGINE_ROOT = Path(__file__).resolve().parents[2]

ALLOWLIST = {
    "core/background_model_output.py",
    "core/database.py",
    "core/gemini_cli_runtime.py",
    "core/model_capability_matrix.py",
    "core/model_connection_tester.py",
    "core/model_control_plane.py",
    "core/model_provider_catalog.py",
    "core/model_reasoning_repair.py",
    "core/model_thinking_control.py",
    "core/provider_compatibility.py",
    "core/reasoning_payload_contract.py",
    "core/reasoning_surface_contract.py",
    "core/response_normalizer.py",
    "core/runtime_projection.py",
    "erc/chat_canonical_transcript.py",
    "erc/workflow_ledger.py",
    "erc/workflow_projection.py",
    "runtimes/chat/runtime.py",
    "runtimes/network_supervisor/anthropic_compat.py",
    "runtimes/network_supervisor/compat_ingress_filter.py",
    "runtimes/network_supervisor/compat_wire_emitter.py",
    "runtimes/network_supervisor/openai_compat.py",
}


def test_background_runtime_code_does_not_directly_read_reasoning_payloads() -> None:
    violations: list[str] = []
    for path in _python_files(ENGINE_ROOT):
        relative = path.relative_to(ENGINE_ROOT).as_posix()
        if relative in ALLOWLIST or relative.startswith("tests/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, expression in _direct_reasoning_reads(tree):
            violations.append(f"{relative}:{lineno}: {expression}")

    assert not violations, (
        "后台业务链路不得直接读取 reasoning/thinking 字段；请改用 "
        "sanitize_background_model_output 或 parse_background_json_*。违规位置：\n"
        + "\n".join(violations)
    )


def _python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        parts = set(path.parts)
        if ".venv" in parts or "__pycache__" in parts:
            continue
        yield path


def _direct_reasoning_reads(tree: ast.AST) -> Iterable[tuple[int, str]]:
    keys = (set(REASONING_KEY_SET) | set(SIGNATURE_KEY_SET)) - {"analysis", "deliberation"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and node.args and _literal_key(node.args[0]) in keys:
                yield node.lineno, f".get({_literal_key(node.args[0])!r})"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "getattr" and len(node.args) >= 2 and _literal_key(node.args[1]) in keys:
                yield node.lineno, f"getattr(..., {_literal_key(node.args[1])!r})"
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and _literal_key(node.slice) in keys:
            yield node.lineno, f"[{_literal_key(node.slice)!r}]"


def _literal_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Index):  # pragma: no cover - py<3.9 compatibility
        return _literal_key(node.value)
    return None
