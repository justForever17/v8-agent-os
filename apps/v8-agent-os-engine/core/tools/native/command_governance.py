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

    if head in {"python", "python3", "py"}:
        non_interactive_flags = {"-c", "-m"}
        if any(flag in tokens for flag in non_interactive_flags):
            return None
        script_arguments: list[str] = []
        skip_next = False
        for token in tokens[1:]:
            if skip_next:
                skip_next = False
                continue
            if token in {"-w", "-x", "--check-hash-based-pycs"}:
                skip_next = True
                continue
            if token.startswith("-"):
                continue
            script_arguments.append(token)
        if not script_arguments:
            return f"检测到 `{head}` 进入 REPL/交互模式的风险。"
        return None

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
        r"(^|[;&|]\s*)python(?:3)?\s+-m\s+pip\s+install\b",
        r"(^|[;&|]\s*)py\s+-m\s+pip\s+install\b",
        r"(^|[;&|]\s*)pip(?:3)?\s+install\b",
        r"(^|[;&|]\s*)uv\s+(?:add|sync|pip\s+install)\b",
        r"(^|[;&|]\s*)poetry\s+(?:add|install|update)\b",
        r"(^|[;&|]\s*)pipenv\s+(?:install|sync)\b",
        r"(^|[;&|]\s*)cargo\s+(?:add|install|build|test|run)\b",
        r"(^|[;&|]\s*)go\s+(?:get|install|mod\s+tidy|build|test|run)\b",
        r"(^|[;&|]\s*)mvn(?:\.cmd)?\s+(?:install|package|test|verify|dependency:|spring-boot:run)\b",
        r"(^|[;&|]\s*)(?:gradle|gradlew|\.\\gradlew|./gradlew)\s+(?:build|test|assemble|install|install\w+|dependencies|run)\b",
        r"(^|[;&|]\s*)dotnet\s+(?:restore|add\s+package|build|test|run)\b",
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
        "cargo watch",
        "mvn spring-boot:run",
        "gradle bootrun",
        "gradlew bootrun",
        "go run ",
        "dotnet watch",
        "python -m http.server",
        "python -m uvicorn",
    )
    if any(marker in lowered for marker in long_running_markers):
        return f"检测到 `{command}` 更像长驻进程，建议进入 session 模式以便轮询和中断。"
    return None


def _windows_shell_syntax_violation_payload(
    command: str,
    *,
    shell_dialect: str = "auto",
) -> dict[str, Any] | None:
    if sys.platform != "win32":
        return None
    # Validate the complete command. Removing a leading `cd ... &&` here hid
    # the exact cross-shell operator that must be rejected on PowerShell 5.1.
    stripped = str(command or "").strip()
    if not stripped:
        return None
    lowered = stripped.lower()
    # Braces inside quoted text are literal in both PowerShell and POSIX
    # shells. PowerShell format strings such as "{0,-32}" must not be
    # mistaken for POSIX brace expansion.
    unquoted = re.sub(r'"(?:`.|[^"\r\n])*"|\'(?:\'\'|[^\'\r\n])*\'', "", stripped)
    dialect = str(shell_dialect or "auto").strip().lower()
    violations: list[str] = []
    suggestions: list[str] = []
    if dialect != "bash" and re.search(r"(^|[;&|]\s*)mkdir\s+-p(?:\s|$)", lowered):
        violations.append("mkdir_-p")
        suggestions.append("PowerShell: New-Item -ItemType Directory -Force <path>")
        suggestions.append("Prefer write_native_file for project files; it creates parent directories safely.")
    if dialect != "bash" and re.search(r"\{[^{}\r\n,]+,[^{}\r\n]+\}", unquoted):
        violations.append("brace_expansion")
        suggestions.append("PowerShell: create each directory explicitly or use an array piped to New-Item.")
    if dialect != "bash" and re.search(r"(^|[;&|]\s*)ls\s+-[A-Za-z]*[la][A-Za-z]*(?:\s|$)", stripped):
        violations.append("ls_dash_la")
        suggestions.append("PowerShell: Get-ChildItem -Force <path>")
    if not violations:
        if dialect in {"powershell", "pwsh"} and (
            re.search(r"(^|[;&|]\s*)set\s+[A-Za-z_][A-Za-z0-9_]*=", stripped, re.IGNORECASE)
            or re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", stripped)
            or re.search(r"(^|[;&|]\s*)dir\s+/[A-Za-z]", stripped, re.IGNORECASE)
        ):
            violations.append("cmd_syntax_in_powershell")
            suggestions.append("Use $env:NAME for environment variables and Get-ChildItem for directory listing.")
        elif dialect == "powershell" and ("&&" in stripped or "||" in stripped):
            violations.append("powershell_5_chain_operator")
            suggestions.append("Pass cwd separately and run one PowerShell command, use '; if ($?) { ... }', or explicitly choose shell_dialect='pwsh'/'cmd' when that dialect is required.")
        elif dialect == "cmd" and (
            re.search(r"\$env:[A-Za-z_]", stripped, re.IGNORECASE)
            or re.search(r"\$\([^\r\n]+\)", stripped)
            or re.search(r"(^|[;&|]\s*)(?:Get|Set|New|Remove|Copy|Move)-[A-Za-z]+", stripped, re.IGNORECASE)
            or re.search(r"\|\s*(?:Where|ForEach|Select)-Object\b", stripped, re.IGNORECASE)
        ):
            violations.append("powershell_syntax_in_cmd")
            suggestions.append("Choose shell_dialect='powershell' or rewrite the command with cmd.exe syntax.")
    if not violations:
        return None
    summary = (
        "检测到所选 shell dialect 与命令语法不一致，已阻断以避免执行偏差。"
        if any(item in {"cmd_syntax_in_powershell", "powershell_5_chain_operator", "powershell_syntax_in_cmd"} for item in violations)
        else "检测到 POSIX shell 写法，但当前命令会在 Windows shell 中执行，已阻断以避免污染工作区。"
    )
    return {
        "ok": False,
        "kind": "cross_shell_syntax_violation",
        "summary": summary,
        "command": command,
        "platform": "windows",
        "shellDialect": dialect,
        "violations": violations,
        "suggestedAlternatives": list(dict.fromkeys(suggestions)),
        "recommendedNextAction": "改用 PowerShell/Windows 等价命令，或使用 V8 文件工具在 Active Workspace Root 内创建文件。",
    }
