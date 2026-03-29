from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from core.storage import storage


def commands_root() -> Path:
    root = storage.base_dir / "commands"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _build_summary(content: str) -> str:
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        if line:
            return line[:120]
    return ""


def _serialize_preset(path: Path, *, include_content: bool) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    payload: dict[str, Any] = {
        "name": path.stem,
        "filename": path.name,
        "path": str(path),
        "updatedAt": path.stat().st_mtime,
        "summary": _build_summary(content),
        "contentHash": f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
    }
    if include_content:
        payload["content"] = content
    return payload


def list_command_presets() -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = []
    for path in sorted(commands_root().glob("*.md"), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        presets.append(_serialize_preset(path, include_content=False))
    return presets


def read_command_preset(name: str) -> dict[str, Any] | None:
    normalized = str(name or "").strip()
    if not normalized or any(token in normalized for token in ("/", "\\", "..")):
        return None

    path = commands_root() / f"{normalized}.md"
    if not path.exists() or not path.is_file():
        return None
    return _serialize_preset(path, include_content=True)
