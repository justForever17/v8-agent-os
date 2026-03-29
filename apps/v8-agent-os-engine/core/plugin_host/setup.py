from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .capability import resolve_renderable_config_fields
from .profiles import onboarding_profile


QR_BLOCK_CHARS = {"█", "▀", "▄", "▌", "▐", "■", "▓", "▒", "░", "▇", "▆", "▅", "▃", "▂", "▁"}
ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", str(text or ""))


def _looks_like_qr_block_line(raw_line: str) -> bool:
    stripped = str(raw_line or "").rstrip("\r\n")
    if not stripped.strip():
        return False
    block_count = sum(1 for char in stripped if char in QR_BLOCK_CHARS)
    if block_count >= 6:
        return True
    return block_count >= 3 and len(stripped.strip()) >= 8 and not any(token in stripped.lower() for token in ("http://", "https://"))


def _line_requires_user_action(line: str, lowered: str) -> bool:
    if any(keyword in line for keyword in ("扫码", "二维码", "授权", "登录", "复制", "令牌", "token", "重启", "打开", "访问")):
        return True
    if any(keyword in lowered for keyword in ("scan", "login", "authorize", "open ", "openclaw channels login", "gateway install", "gateway restart", "start with:", "copy token", "manual", "manually", "retry")):
        return True
    if lowered.startswith("run ") or lowered.startswith("openclaw "):
        return True
    return False


def detect_onboarding_hints(lines: list[str]) -> dict[str, Any]:
    urls: list[str] = []
    qr_hints: list[str] = []
    qr_blocks: list[str] = []
    instructions: list[str] = []
    seen_urls: set[str] = set()
    seen_qr: set[str] = set()
    current_qr_block: list[str] = []
    seen_instruction: set[str] = set()

    def _flush_qr_block() -> None:
        nonlocal current_qr_block
        if not current_qr_block:
            return
        block = "\n".join(current_qr_block).rstrip()
        if block:
            qr_blocks.append(block)
        current_qr_block = []

    for raw_line in lines:
        physical_lines = _strip_ansi(str(raw_line or "")).splitlines() or [""]
        for raw_text in physical_lines:
            normalized_text = str(raw_text or "").rstrip("\r\n")
            if _looks_like_qr_block_line(normalized_text):
                current_qr_block.append(normalized_text)
                continue
            line = normalized_text.strip()
            if not line:
                if current_qr_block:
                    continue
                continue
            _flush_qr_block()
            lowered = line.lower()
            for token in line.replace("(", " ").replace(")", " ").split():
                if token.startswith("http://") or token.startswith("https://"):
                    candidate = token.rstrip(".,;)]}>")
                    if candidate not in seen_urls:
                        seen_urls.add(candidate)
                        urls.append(candidate)
            if any(keyword in line for keyword in ("扫码", "二维码")) or "qr" in lowered:
                if line not in seen_qr:
                    seen_qr.add(line)
                    qr_hints.append(line)
            if _line_requires_user_action(line, lowered):
                if line not in seen_instruction:
                    seen_instruction.add(line)
                    instructions.append(line)
    _flush_qr_block()

    if urls:
        urls = [urls[-1]]
    if qr_hints:
        qr_hints = [qr_hints[-1]]
    if qr_blocks:
        qr_blocks = [qr_blocks[-1]]

    return {
        "urls": urls,
        "qrHints": qr_hints,
        "qrBlocks": qr_blocks,
        "instructions": instructions,
        "requiresUserAction": bool(urls or qr_hints or qr_blocks or instructions),
    }


def build_setup_surface(
    *,
    plugin_id: str,
    manifest: dict[str, Any],
    package_manifest: dict[str, Any],
    setup_state: str | None = None,
) -> dict[str, Any]:
    openclaw_meta = package_manifest.get("openclaw") if isinstance(package_manifest, dict) else {}
    setup_entry = str((openclaw_meta or {}).get("setupEntry") or "").strip() or None
    render_surface = resolve_renderable_config_fields(
        plugin_id=plugin_id,
        manifest=manifest,
        package_manifest=package_manifest,
    )
    onboarding_surface = onboarding_profile(
        plugin_id=plugin_id,
        package_manifest=package_manifest,
    )
    config_fields = list(render_surface.get("renderableFields") or [])
    instructions: list[str] = []
    if setup_entry:
        instructions.append("插件声明了 setup entry，安装后可能需要继续执行接入向导。")
    if config_fields:
        instructions.append("插件声明了配置字段，完成安装后仍需补齐接入参数。")
    if not instructions:
        instructions.append("当前插件未声明额外 setup 向导，通常只需完成安装并通过健康检查。")
    return {
        "phase": str(setup_state or "installed"),
        "setupEntry": setup_entry,
        "configFields": config_fields,
        "renderMode": str(render_surface.get("renderMode") or ("config_schema" if config_fields else "wizard_only")),
        "renderableFields": config_fields,
        "actionMode": str(onboarding_surface.get("actionMode") or "config_form"),
        "manualSteps": list(onboarding_surface.get("manualSteps") or []),
        "docsUrl": str(onboarding_surface.get("docsUrl") or "").strip() or None,
        "requiredSecrets": list(onboarding_surface.get("requiredSecrets") or []),
        "requiredIds": list(onboarding_surface.get("requiredIds") or []),
        "pairingMode": str(onboarding_surface.get("pairingMode") or "none"),
        "onboardingType": str(onboarding_surface.get("onboardingType") or "config_only"),
        "requiresWizard": bool(setup_entry),
        "requiresConfiguration": bool(config_fields),
        "instructions": instructions,
    }


def merge_setup_user_action(
    setup_surface: dict[str, Any],
    *,
    user_action: dict[str, Any] | None = None,
    job_status: str | None = None,
) -> dict[str, Any]:
    merged = dict(setup_surface or {})
    normalized_user_action = dict(user_action or {})
    merged["jobStatus"] = str(job_status or "").strip() or None
    merged["userAction"] = normalized_user_action
    merged["requiresUserAction"] = bool(normalized_user_action.get("requiresUserAction"))
    if merged["requiresUserAction"]:
        merged["phase"] = "needs_user_action"
    return merged


def resolve_local_openclaw_package_root(tooling_root: Path) -> Path | None:
    candidate = tooling_root / "node_modules" / "openclaw"
    return candidate if candidate.exists() else None


def ensure_openclaw_host_bridge(*, plugin_dir: Path, host_package_root: Path) -> dict[str, Any]:
    plugin_dir = Path(plugin_dir)
    host_package_root = Path(host_package_root)
    if not plugin_dir.exists():
        raise RuntimeError(f"插件安装路径不存在，无法创建宿主桥接：{plugin_dir}")
    if not host_package_root.exists():
        raise RuntimeError(f"宿主 openclaw 包根目录不存在：{host_package_root}")

    node_modules_dir = plugin_dir / "node_modules"
    bridge_path = node_modules_dir / "openclaw"
    node_modules_dir.mkdir(parents=True, exist_ok=True)

    if bridge_path.exists():
        try:
            same_target = bridge_path.resolve() == host_package_root.resolve()
        except Exception:
            same_target = False
        if same_target:
            return {"bridgePath": str(bridge_path), "created": False, "method": "existing"}
        raise RuntimeError(f"插件目录已存在冲突的 openclaw 依赖路径，无法安全桥接：{bridge_path}")

    try:
        os.symlink(str(host_package_root), str(bridge_path), target_is_directory=True)
        return {"bridgePath": str(bridge_path), "created": True, "method": "symlink"}
    except Exception:
        if os.name != "nt":
            raise

    shell = os.environ.get("COMSPEC") or "cmd.exe"
    completed = subprocess.run(
        [shell, "/d", "/c", "mklink", "/J", str(bridge_path), str(host_package_root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode == 0 and bridge_path.exists():
        return {"bridgePath": str(bridge_path), "created": True, "method": "junction"}

    stderr = str(completed.stderr or "").strip()
    stdout = str(completed.stdout or "").strip()
    detail = stderr or stdout or f"returnCode={completed.returncode}"
    raise RuntimeError(f"宿主 openclaw 包桥接失败：{detail}")
