from __future__ import annotations

import re
import sys
from typing import Any


def _strip_leading_shell_cwd(command: str) -> str:
    stripped = str(command or "").strip()
    if not stripped:
        return ""
    patterns = (
        r'^\s*cd\s+/d\s+"[^"]+"\s*&&\s*(?P<rest>.+)$',
        r"^\s*cd\s+/d\s+'[^']+'\s*&&\s*(?P<rest>.+)$",
        r"^\s*cd\s+/d\s+\S+\s*&&\s*(?P<rest>.+)$",
        r'^\s*cd\s+"[^"]+"\s*&&\s*(?P<rest>.+)$',
        r"^\s*cd\s+'[^']+'\s*&&\s*(?P<rest>.+)$",
        r"^\s*cd\s+\S+\s*&&\s*(?P<rest>.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, stripped, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return str(match.group("rest") or "").strip()
    return stripped


def _detect_interactive_command(command: str) -> str | None:
    """Detect obviously interactive/TTY-oriented commands that should never run in blocking mode."""
    stripped = _strip_leading_shell_cwd(command)
    if not stripped:
        return None

    lowered = stripped.lower()
    tokens = lowered.split()
    if not tokens:
        return None

    head = tokens[0]
    simple_flags = {"-h", "--help", "-v", "--version"}
    if any(flag in tokens for flag in simple_flags):
        return None

    bare_interactive = {
        "qwen",
        "claude",
        "gemini",
        "codex",
        "aider",
        "python",
        "python3",
        "ipython",
        "node",
        "bash",
        "sh",
        "pwsh",
        "powershell",
        "cmd",
        "ssh",
        "sftp",
        "ftp",
        "telnet",
        "mysql",
        "psql",
        "sqlite3",
    }
    if lowered in bare_interactive:
        return f"检测到交互式命令 `{stripped}`，同步命令工具会阻塞等待输入。"

    if head in {"python", "python3"}:
        non_interactive_flags = {"-c", "-m"}
        if len(tokens) == 1 or not any(flag in tokens for flag in non_interactive_flags):
            return f"检测到 `{head}` 进入 REPL/交互模式的风险。"

    if head in {"node", "bash", "sh", "pwsh", "powershell"}:
        non_interactive_flags = {"-c", "-command", "-file", "-e"}
        if len(tokens) == 1 or not any(flag in tokens for flag in non_interactive_flags):
            return f"检测到 `{head}` 可能启动交互式终端。"

    if head == "cmd" and "/c" not in tokens:
        return "检测到 `cmd` 缺少 `/c`，很可能进入交互式终端。"

    if head == "claude" and any(flag in tokens for flag in {"-p", "--print"}):
        return None

    if head in {"qwen", "claude", "gemini", "codex", "aider"}:
        return f"检测到 `{head}` 可能需要 TTY 或交互输入。"

    return None


def _detect_session_preferred_command(command: str) -> str | None:
    stripped = _strip_leading_shell_cwd(command)
    lowered = str(stripped or "").strip().lower()
    if not lowered:
        return None
    if lowered.startswith("npx skills "):
        return f"检测到 `{command}` 可能进入 Skills CLI 的交互会话，建议进入 session 模式。"
    scaffolding_markers = (
        "npx create-next-app",
        "npx create-",
        "npm create",
        "pnpm create",
        "yarn create",
        "bun create",
        "create-next-app",
    )
    if any(marker in lowered for marker in scaffolding_markers) or re.search(r"(^|[;&|]\s*)npx\s+(?:--yes\s+|-y\s+)?create-", lowered):
        return f"检测到 `{command}` 是项目脚手架命令，必须进入 session 模式以便观察交互、超时和落地状态。"
    install_patterns = (
        r"(^|[;&|]\s*)npm\s+(install|i)\b",
        r"(^|[;&|]\s*)pnpm\s+(install|i)\b",
        r"(^|[;&|]\s*)yarn\s+(install|add)\b",
        r"(^|[;&|]\s*)bun\s+(install|add)\b",
    )
    if any(re.search(pattern, lowered) for pattern in install_patterns):
        return f"检测到 `{command}` 是依赖安装类命令，建议进入 session 模式以便轮询、恢复和捕获失败原因。"
    long_running_markers = (
        "uvicorn ",
        "gunicorn ",
        "npm run dev",
        "pnpm dev",
        "yarn dev",
        "npm start",
        "pnpm start",
        "yarn start",
        "next dev",
        "vite",
        "tail -f",
        "watch ",
        "python -m http.server",
        "python -m uvicorn",
    )
    if any(marker in lowered for marker in long_running_markers):
        return f"检测到 `{command}` 更像长驻进程，建议进入 session 模式以便轮询和中断。"
    return None


def _windows_shell_syntax_violation_payload(command: str) -> dict[str, Any] | None:
    if sys.platform != "win32":
        return None
    stripped = _strip_leading_shell_cwd(command)
    if not stripped:
        return None
    lowered = stripped.lower()
    violations: list[str] = []
    suggestions: list[str] = []
    if re.search(r"(^|[;&|]\s*)mkdir\s+-p(?:\s|$)", lowered):
        violations.append("mkdir_-p")
        suggestions.append("PowerShell: New-Item -ItemType Directory -Force <path>")
        suggestions.append("Prefer write_native_file for project files; it creates parent directories safely.")
    if re.search(r"\{[^{}\r\n,]+,[^{}\r\n]+\}", stripped):
        violations.append("brace_expansion")
        suggestions.append("PowerShell: create each directory explicitly or use an array piped to New-Item.")
    if re.search(r"(^|[;&|]\s*)ls\s+-[A-Za-z]*[la][A-Za-z]*(?:\s|$)", stripped):
        violations.append("ls_dash_la")
        suggestions.append("PowerShell: Get-ChildItem -Force <path>")
    if not violations:
        return None
    return {
        "ok": False,
        "kind": "cross_shell_syntax_violation",
        "summary": "检测到 POSIX shell 写法，但当前命令会在 Windows shell 中执行，已阻断以避免污染工作区。",
        "command": command,
        "platform": "windows",
        "violations": violations,
        "suggestedAlternatives": list(dict.fromkeys(suggestions)),
        "recommendedNextAction": "改用 PowerShell/Windows 等价命令，或使用 V8 文件工具在 Active Workspace Root 内创建文件。",
    }
