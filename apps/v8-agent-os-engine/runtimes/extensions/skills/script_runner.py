from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from core.audit_logger import audit_logger
from core.tools.native.command import execute_governed_argv
from core.workspace_capability import build_workspace_binding


_SCRIPT_INTERPRETERS: dict[str, tuple[str, ...]] = {
    ".py": (sys.executable,),
    ".js": ("node",),
    ".mjs": ("node",),
    ".cjs": ("node",),
    ".ps1": ("pwsh", "-NoProfile", "-File"),
    ".sh": ("bash",),
}


def _resolve_argv(target: Path, script_args: list[str] | None) -> tuple[list[str], list[str]]:
    interpreter = list(_SCRIPT_INTERPRETERS.get(target.suffix.lower()) or ())
    if not interpreter:
        raise ValueError(f"unsupported skill script type: {target.suffix.lower() or '(no extension)'}")
    if not Path(interpreter[0]).is_absolute():
        executable = shutil.which(interpreter[0])
        if not executable:
            raise RuntimeError(f"required script interpreter is unavailable: {interpreter[0]}")
        interpreter[0] = executable

    args = [str(item) for item in list(script_args or [])]
    if len(args) > 64:
        raise ValueError("script_args may contain at most 64 entries")
    if any("\x00" in item or "\r" in item or "\n" in item or len(item) > 4096 for item in args):
        raise ValueError("script_args contains an unsafe or oversized argument")
    return [*interpreter, str(target), *args], args


def _format_result(relative_path: str, result: dict[str, Any]) -> str:
    lines = [
        "=== SKILL SCRIPT RESULT ===",
        f"Status: {'completed' if result.get('ok') else 'failed'}",
        f"Script: {relative_path}",
        f"Exit Code: {result.get('returnCode') if result.get('returnCode') is not None else ''}",
        f"Summary: {result.get('summary') or ''}",
    ]
    stdout = str(result.get("stdout") or "").rstrip()
    stderr = str(result.get("stderr") or "").rstrip()
    if stdout:
        lines.extend(["", "Output:", stdout])
    if stderr:
        lines.extend(["", "Errors:", stderr])
    if result.get("stdoutTruncated"):
        lines.append(f"Output note: stdout capped at 220000 of {result.get('stdoutChars') or 0} chars.")
    if result.get("stderrTruncated"):
        lines.append(f"Error note: stderr capped at 220000 of {result.get('stderrChars') or 0} chars.")
    lines.extend(["", f"Next Action: {result.get('recommendedNextAction') or ''}"])
    return "\n".join(lines).strip()


def run_skill_script(
    *,
    skill: dict[str, Any],
    relative_path: str,
    script_args: list[str] | None,
    timeout_seconds: int,
    runtime_context: dict[str, Any],
) -> str:
    if not relative_path.startswith("scripts/"):
        raise ValueError('run_script only accepts a relative_path under "scripts/"')

    skill_root = Path(str(skill.get("skillRoot") or skill.get("path") or "")).resolve(strict=False)
    target = (skill_root / relative_path).resolve(strict=False)
    target.relative_to(skill_root)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"skill script not found: {relative_path}")

    argv, normalized_args = _resolve_argv(target, script_args)
    binding = build_workspace_binding(runtime_context)
    result = execute_governed_argv(
        argv,
        cwd=str(binding.active_workspace_root),
        allowed_extra_roots=[str(skill_root), str(Path(argv[0]).resolve(strict=False).parent)],
        tool_call_id="",
        timeout_seconds=timeout_seconds,
        action_family="skill_script",
        action_subject=relative_path,
    )
    audit_logger.log(
        source_type="EXTENSIONS",
        action="skill_script_execute",
        status="INFO" if result.get("ok") else "ERROR",
        details=json.dumps(
            {
                "skillId": skill.get("skillId") or "",
                "relativePath": relative_path,
                "scriptSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "argumentCount": len(normalized_args),
                "returnCode": result.get("returnCode"),
                "runId": runtime_context.get("run_id") or runtime_context.get("runId"),
                "sessionId": runtime_context.get("session_id") or runtime_context.get("sessionId"),
            },
            ensure_ascii=False,
        ),
    )
    return _format_result(relative_path, result)
