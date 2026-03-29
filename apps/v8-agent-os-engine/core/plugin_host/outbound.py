from __future__ import annotations

"""
Plugin host outbound adapter.

当前主线不再回退到旧 Feishu / WeCom 自建链。PluginHostRuntime 的本地出站
统一走 OpenClaw 官方 CLI：`openclaw message send`。
"""

import asyncio
import json
import locale
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from core.v8_agent_os_paths import OPENCLAW_DEFAULT_STATE_ROOT

from .registry import default_plugin_registry


def default_channel_type() -> str | None:
    registry = default_plugin_registry()
    plugins = sorted(
        [dict(item) for item in (registry.get("plugins") or {}).values() if isinstance(item, dict)],
        key=lambda item: str(item.get("displayName") or item.get("pluginId") or "").lower(),
    )
    for plugin in plugins:
        if str(plugin.get("activationState") or "active").lower() == "disabled":
            continue
        channels = list((plugin.get("capabilities") or {}).get("channels") or (plugin.get("manifestSummary") or {}).get("channels") or [])
        if channels:
            normalized = str(channels[0]).strip()
            if normalized:
                return normalized
    return None


def default_target_for(channel_type: str) -> str | None:
    return None


def _prepend_path(env: dict[str, str], *entries: Path) -> dict[str, str]:
    separator = ";" if os.name == "nt" else ":"
    existing = str(env.get("PATH") or "")
    normalized_entries = [str(path) for path in entries if str(path)]
    env["PATH"] = separator.join([*normalized_entries, existing] if existing else normalized_entries)
    return env


def _tooling_cli_candidates(tooling_root: Path) -> list[Path]:
    return [
        tooling_root / "bin" / "openclaw.cmd",
        tooling_root / "bin" / "openclaw",
        tooling_root / "node_modules" / ".bin" / "openclaw.cmd",
        tooling_root / "node_modules" / ".bin" / "openclaw",
        tooling_root / "openclaw.cmd",
        tooling_root / "openclaw",
    ]


def _tooling_package_root_candidates(tooling_root: Path) -> list[Path]:
    return [
        tooling_root / "node_modules" / "openclaw",
        tooling_root / "lib" / "node_modules" / "openclaw",
    ]


def _windows_global_npm_openclaw_cli() -> Path | None:
    if os.name != "nt":
        return None
    appdata = str(os.environ.get("APPDATA") or "").strip()
    if not appdata:
        return None
    npm_root = Path(appdata).expanduser() / "npm"
    for candidate in (npm_root / "openclaw.cmd", npm_root / "openclaw"):
        if candidate.exists():
            return candidate
    return None


def _resolve_openclaw_cli(env: dict[str, str], tooling_root: Path | None = None) -> str | None:
    if tooling_root:
        for candidate in _tooling_cli_candidates(tooling_root):
            if candidate.exists():
                return str(candidate)
    path_value = env.get("PATH") or os.environ.get("PATH") or ""
    system_cli = shutil.which("openclaw", path=path_value) or shutil.which("openclaw.cmd", path=path_value)
    if system_cli:
        return system_cli
    global_npm_cli = _windows_global_npm_openclaw_cli()
    return str(global_npm_cli) if global_npm_cli else None


def _resolve_openclaw_package_root(env: dict[str, str], tooling_root: Path | None = None) -> Path | None:
    if tooling_root:
        for candidate in _tooling_package_root_candidates(tooling_root):
            if candidate.exists():
                return candidate
    cli_path = _resolve_openclaw_cli(env, tooling_root)
    if not cli_path:
        return None
    cli_candidate = Path(cli_path)
    inferred_candidates = [
        cli_candidate.parent.parent / "openclaw",
        cli_candidate.parent.parent / "node_modules" / "openclaw",
        cli_candidate.parent / "node_modules" / "openclaw",
        cli_candidate.parent.parent / "lib" / "node_modules" / "openclaw",
    ]
    for candidate in inferred_candidates:
        if candidate.exists():
            return candidate
    return None


def _resolve_windows_node_openclaw_argv(env: dict[str, str], tooling_root: Path | None, *args: str) -> list[str] | None:
    if os.name != "nt":
        return None
    path_value = env.get("PATH") or os.environ.get("PATH") or ""
    node_executable = shutil.which("node", path=path_value) or shutil.which("node.exe", path=path_value)
    package_root = _resolve_openclaw_package_root(env, tooling_root)
    openclaw_entry = package_root / "openclaw.mjs" if package_root else None
    if not node_executable or not openclaw_entry or not openclaw_entry.exists():
        return None
    return [node_executable, str(openclaw_entry), *[str(item) for item in args]]


def _wrap_windows_executable_argv(executable: str, *args: str) -> list[str]:
    shell = os.environ.get("COMSPEC") or "cmd.exe"
    normalized = str(executable)
    if normalized.lower().endswith((".cmd", ".bat")):
        return [shell, "/d", "/c", "call", normalized, *[str(item) for item in args]]
    return [normalized, *[str(item) for item in args]]


def _extract_json_payload(stdout: str) -> dict[str, Any]:
    candidates = [line.strip() for line in stdout.splitlines() if line.strip()]
    for candidate in reversed(candidates):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    stripped = stdout.strip()
    if stripped:
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            return payload
    return {}


def _decode_output(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    encodings: list[str] = ["utf-8"]
    preferred = str(locale.getpreferredencoding(False) or "").strip()
    if preferred and preferred.lower() not in {item.lower() for item in encodings}:
        encodings.append(preferred)
    if os.name == "nt":
        for candidate in ("gbk", "cp936"):
            if candidate.lower() not in {item.lower() for item in encodings}:
                encodings.append(candidate)
    for encoding in encodings:
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode(encodings[0], errors="replace").strip()


def _build_send_argv(
    *,
    env: dict[str, str],
    channel_type: str,
    receive_id: str,
    text: str | None = None,
    media_url: str | None = None,
    managed_tooling_root: str | Path | None = None,
    account_id: str | None = None,
    reply_to_id: str | None = None,
    thread_id: str | None = None,
) -> list[str]:
    tooling_root = Path(str(managed_tooling_root)).expanduser() if managed_tooling_root else None
    cli_executable = _resolve_openclaw_cli(env, tooling_root)
    windows_node_argv = _resolve_windows_node_openclaw_argv(
        env,
        tooling_root,
        "message",
        "send",
        "--channel",
        str(channel_type),
        "--target",
        str(receive_id),
        "--json",
    )
    if text:
        if windows_node_argv is not None:
            windows_node_argv.extend(["--message", str(text)])
    if media_url:
        if windows_node_argv is not None:
            windows_node_argv.extend(["--media", str(media_url)])
    if account_id:
        if windows_node_argv is not None:
            windows_node_argv.extend(["--account", str(account_id)])
    if reply_to_id:
        if windows_node_argv is not None:
            windows_node_argv.extend(["--reply-to", str(reply_to_id)])
    if thread_id:
        if windows_node_argv is not None:
            windows_node_argv.extend(["--thread-id", str(thread_id)])

    if not cli_executable and windows_node_argv is None:
        raise RuntimeError("当前宿主未找到 openclaw CLI，无法执行 PluginHostRuntime 出站。")

    argv = [cli_executable, "message", "send", "--channel", str(channel_type), "--target", str(receive_id), "--json"]
    if text:
        argv.extend(["--message", str(text)])
    if media_url:
        argv.extend(["--media", str(media_url)])
    if account_id:
        argv.extend(["--account", str(account_id)])
    if reply_to_id:
        argv.extend(["--reply-to", str(reply_to_id)])
    if thread_id:
        argv.extend(["--thread-id", str(thread_id)])
    if windows_node_argv is not None:
        return windows_node_argv
    if os.name == "nt":
        return _wrap_windows_executable_argv(cli_executable, *argv[1:])
    return argv


async def _broadcast(
    *,
    channel_type: str,
    receive_id: str,
    text: str | None = None,
    media_url: str | None = None,
    managed_root: str | Path | None = None,
    managed_tooling_root: str | Path | None = None,
    account_id: str | None = None,
    reply_to_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    normalized_text = str(text or "").strip()
    normalized_media_url = str(media_url or "").strip()
    if not normalized_text and not normalized_media_url:
        raise RuntimeError("PluginHostRuntime 出站至少需要文本或媒体之一。")
    root = Path(str(managed_root or OPENCLAW_DEFAULT_STATE_ROOT))
    env = os.environ.copy()
    env["OPENCLAW_STATE_DIR"] = str(root)
    tooling_root = Path(str(managed_tooling_root)).expanduser() if managed_tooling_root else None
    global_npm_cli = _windows_global_npm_openclaw_cli()
    global_npm_root = global_npm_cli.parent if global_npm_cli else None
    if tooling_root:
        entries = [tooling_root / "node_modules" / ".bin", tooling_root]
        if global_npm_root:
            entries.append(global_npm_root)
        env = _prepend_path(env, *entries)
    elif global_npm_root:
        env = _prepend_path(env, global_npm_root)
    argv = _build_send_argv(
        env=env,
        channel_type=channel_type,
        receive_id=receive_id,
        text=normalized_text or None,
        media_url=normalized_media_url or None,
        managed_tooling_root=tooling_root,
        account_id=account_id,
        reply_to_id=reply_to_id,
        thread_id=thread_id,
    )

    completed = await asyncio.to_thread(
        subprocess.run,
        argv,
        cwd=str(root),
        env=env,
        capture_output=True,
        text=False,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stdout = _decode_output(completed.stdout)
    stderr = _decode_output(completed.stderr)
    if completed.returncode != 0:
        detail = stderr or stdout or f"退出码 {completed.returncode}"
        raise RuntimeError(f"OpenClaw 出站失败：{detail}")

    payload = _extract_json_payload(stdout) if stdout else {}
    return {
        "channel": channel_type,
        "receiveId": receive_id,
        "message": normalized_text or None,
        "mediaUrl": normalized_media_url or None,
        "returnCode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "payload": payload,
        "command": " ".join(argv),
    }


async def broadcast_text(
    *,
    channel_type: str,
    receive_id: str,
    text: str,
    managed_root: str | Path | None = None,
    managed_tooling_root: str | Path | None = None,
    account_id: str | None = None,
    reply_to_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    return await _broadcast(
        channel_type=channel_type,
        receive_id=receive_id,
        text=text,
        managed_root=managed_root,
        managed_tooling_root=managed_tooling_root,
        account_id=account_id,
        reply_to_id=reply_to_id,
        thread_id=thread_id,
    )


async def broadcast_media(
    *,
    channel_type: str,
    receive_id: str,
    media_url: str,
    text: str | None = None,
    managed_root: str | Path | None = None,
    managed_tooling_root: str | Path | None = None,
    account_id: str | None = None,
    reply_to_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    return await _broadcast(
        channel_type=channel_type,
        receive_id=receive_id,
        text=text,
        media_url=media_url,
        managed_root=managed_root,
        managed_tooling_root=managed_tooling_root,
        account_id=account_id,
        reply_to_id=reply_to_id,
        thread_id=thread_id,
    )
