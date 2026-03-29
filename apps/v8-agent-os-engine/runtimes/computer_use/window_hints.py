from __future__ import annotations

import shlex
from pathlib import Path
from typing import List


def derive_window_title_hints(
    *,
    app_id: str | None,
    command: str | List[str] | None,
    explicit_title: str | None = None,
) -> List[str]:
    hints: List[str] = []

    def _push(value: str | None) -> None:
        normalized = str(value or "").strip()
        if normalized and normalized not in hints:
            hints.append(normalized)

    _push(explicit_title)
    normalized_app_id = str(app_id or "").strip().lower()
    tokens = _split_command(command)
    path_like_args = [Path(token.strip('"')) for token in tokens[1:] if str(token or "").strip()]

    for item in path_like_args:
        name = item.name.strip()
        stem = item.stem.strip()
        if item.suffix:
            _push(name)
            _push(stem)
        else:
            _push(name or stem)
            _push(str(item))
            if normalized_app_id == "explorer":
                _push(f"{name} - 文件资源管理器")
                _push(f"{name} - Explorer")
                _push(f"{name} - File Explorer")

    return hints


def _split_command(command: str | List[str] | None) -> List[str]:
    if isinstance(command, list):
        return [str(item or "").strip() for item in command if str(item or "").strip()]
    raw = str(command or "").strip()
    if not raw:
        return []
    try:
        return [str(item or "").strip() for item in shlex.split(raw, posix=False) if str(item or "").strip()]
    except Exception:
        return [raw]
